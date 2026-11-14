"""
Offline knowledge-graph CONVERSATION benchmark — real sessions replayed turn by turn,
no LLM.

`kg_enrichment_bench.py` measures single questions. Every defect the Sep-2026 prod
session 0aa3a650 exposed was a property of a FOLLOW-UP — a turn whose enrichment must be
read against the turn before it — and of the SQL the agent then wrote. This harness
replays a recorded conversation exactly as it happened: for turn N it runs the real
`get_context_enrichment` with a `workflow_history` built from turns 1..N-1 (each carrying
the enrichment this harness produced for it plus the agent's RECORDED tool calls), so the
carry-over reads what production would have read. Per turn it measures the enrichment
against what a correct answer needs (the fixture's `expect`) and against what the agent
actually did (the recorded calls):

  tables      — every expected table is in TABLE SCHEMAS
  join_keys   — every expected pair has a checkable declared key (`join_keys`)
  identity    — every expected table renders a name/title column
  columns     — every expected `table.column` is rendered
  carry_over  — a follow-up carried the previous turn's entities (when expected)
  fanout      — every expected [holder, holder, column] is NAMED a fan-out key (metadata.fanout_keys)
  rewrite     — every recorded statement the guard refuses as a fan-out REWRITES to aggregate-then-join (0 LLM)
  recall      — tables the RECORDED SQL read that the enrichment did NOT show
                (`kgstore.usage.unshown_tables`, the one definition of a miss)
  chars / entities / seed rank of the expected tables

Fixtures: test/fixtures/kg_conversations/*.json — built from persisted sessions
(prod: sudo cat of tda_sessions/<user>/<sid>.json; local: the same path). A fixture names
its graph by kg_metadata NAME (resolved on this instance) — the local mirror of a prod
industry graph is byte-identical to prod's, so a prod conversation replays locally.

Run:  PYTHONPATH=src python3 test/performance/kg_conversation_bench.py --label before
      PYTHONPATH=src python3 test/performance/kg_conversation_bench.py --compare before after
      PYTHONPATH=src python3 test/performance/kg_conversation_bench.py --semantic off   # deterministic
"""
import argparse
import asyncio
import glob
import inspect
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

FIXTURES = os.path.join(ROOT, "test", "fixtures", "kg_conversations")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def _leaf(name):
    return str(name or "").rpartition(".")[2].lower()


def resolve_graph(spec, db_path):
    """(kg_id, owner_profile_id, user_uuid) for a fixture's graph — by kg_id, else by NAME
    (the first non-subscribed match, else any). None when this instance has no such graph."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if spec.get("kg_id"):
            r = c.execute("SELECT kg_id, profile_id, user_uuid FROM kg_metadata WHERE kg_id=?", (spec["kg_id"],)).fetchone()
            return tuple(r) if r else None
        rows = c.execute("SELECT kg_id, profile_id, user_uuid, is_subscribed FROM kg_metadata WHERE name=? ORDER BY is_subscribed, created_at",
                         (spec.get("name"),)).fetchall()
        return tuple(rows[0][:3]) if rows else None
    finally:
        c.close()


def make_handler(kg_id, owner, user):
    from components.builtin.knowledge_graph import handler as kgh
    from components.builtin.knowledge_graph.graph_store import GraphStore
    cls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_search_entities_for_query")][0]
    h = cls.__new__(cls)
    store = GraphStore(owner, user, kg_id=kg_id)
    # pin the graph: the bench measures THIS graph whatever the owner's active assignment is
    h._resolve_grounding_store = lambda kg_pid, u, a: (store, {"mode": "live"})
    h._get_active_kg_owner = lambda p, u: owner
    return h, store


def synth_events(calls):
    """The recorded calls in the ReAct event shape the carry-over reads."""
    out = []
    for c in calls or []:
        out.append({"type": "conversation_tool_invoked", "payload": {"tool_name": c["tool_name"], "arguments": c.get("arguments") or {}}})
        out.append({"type": "conversation_tool_completed", "payload": {"tool_name": c["tool_name"]}})
    return out


async def replay(fixture, db_path, semantic):
    from trusted_data_agent.core.config import APP_CONFIG
    APP_CONFIG.KG_SEMANTIC_SEEDING = semantic
    from trusted_data_agent.kgstore import usage as _usage
    from trusted_data_agent.agent.preflight import joins as _joins
    res = resolve_graph(fixture["graph"], db_path)
    if not res:
        return {"id": fixture["id"], "skipped": f"graph {fixture['graph']} not on this instance"}
    kg_id, owner, user = res
    h, store = make_handler(kg_id, owner, user)
    history = []
    turns_out = []
    for n, t in enumerate(fixture["turns"], 1):
        t0 = time.time()
        text, meta = await h.get_context_enrichment(t["query"], owner, user, record_usage=False,
                                                    workflow_history=list(history))
        dt = time.time() - t0
        meta = meta or {}
        tc = meta.get("table_columns") or {}
        by_leaf = {_leaf(k): [str(c).lower() for c in v] for k, v in tc.items()}
        exp = t.get("expect") or {}
        want_tables = [x.lower() for x in exp.get("tables", [])]
        tables_ok = [x for x in want_tables if x in by_leaf]
        pairs = {frozenset(_leaf(x) for x in jk.get("tables", [])): jk.get("key") for jk in (meta.get("join_keys") or [])}
        want_pairs = [frozenset(x.lower() for x in p) for p in exp.get("join_pairs", [])]
        pairs_ok = [sorted(p) for p in want_pairs if pairs.get(p)]
        ident_ok = [x for x in exp.get("identity_for", []) if any(("name" in c or c.endswith("title")) and not c.endswith("_id") for c in by_leaf.get(x.lower(), []))]
        want_cols = [x.lower() for x in exp.get("columns", [])]
        cols_ok = [x for x in want_cols if x.split(".")[-1] in by_leaf.get(x.split(".")[0], [])]
        # a pair the enrichment must NAME as a fan-out (both hold the column as a foreign key) —
        # what the guard refuses; a join hint listing the two together is the failure
        fan = [(str(f.get("column", "")).lower(), {str(x).lower() for x in f.get("tables") or []}) for f in (meta.get("fanout_keys") or [])]
        want_fan = [[x.lower() for x in p] for p in exp.get("fanout_pairs", [])]
        fan_ok = [p for p in want_fan if any(c == p[2] and {p[0], p[1]} <= ts for c, ts in fan)]
        # …and every recorded statement the guard would refuse as a fan-out must REWRITE to the
        # aggregate-then-join shape at 0 LLM, from the SAME fanout_keys the enrichment produced —
        # the class proven on the real conversation, not on a hand-written statement.
        _jsid = f"bench-{fixture.get('id', 'x')}-{n}"
        _joins.record_fanout(_jsid, meta.get("fanout_keys") or [])
        fan_refused, fan_rewritten = [], []
        for i, c in enumerate(t.get("agent_calls") or []):
            _sql = (c.get("arguments") or {}).get("sql") if isinstance(c, dict) else None
            if isinstance(_sql, str) and _joins.fanout_violations(_jsid, _sql):
                fan_refused.append(i)
                fx = _joins.rewrite_fanout_sql(_jsid, _sql)
                if fx and not _joins.fanout_violations(_jsid, fx["sql"]):
                    fan_rewritten.append({"call": i, "top_pushed": fx["top_pushed"], "grain": fx["grain"]})
        carry = meta.get("carry_over")
        seeds = [str(s.get("name")) for s in (meta.get("seeds") or [])]
        rank = min([i for i, s in enumerate(seeds) if _leaf(s) in want_tables or _leaf(s.rpartition(".")[0]) in want_tables] or [None])
        shown = meta.get("entity_names") or []
        missed = _usage.unshown_tables(shown, t.get("agent_calls") or [], _usage.deployed_names([meta]))
        # A fixture may exempt a table a RECORDED statement read for a stated reason (e.g. the
        # statement rested on a column that does not exist) — the exemption names the table and
        # carries its reason in the fixture, so a gate is never quietly loosened.
        _exempt = {str(k).lower() for k in (t.get("recall_exempt") or {})}
        missed = [m for m in missed if str(m).lower() not in _exempt]
        row = {
            "turn": n, "query": t["query"], "seconds": round(dt, 2),
            "chars": len(text or ""), "entities": meta.get("entity_count", 0),
            "tables_expected": want_tables, "tables_present": tables_ok,
            "join_pairs_expected": [sorted(p) for p in want_pairs], "join_pairs_keyed": pairs_ok,
            "identity_expected": exp.get("identity_for", []), "identity_present": ident_ok,
            "columns_expected": want_cols, "columns_present": cols_ok,
            "carry_over_expected": bool(exp.get("carry_over")), "carry_over": carry,
            "fanout_expected": want_fan, "fanout_named": fan_ok,
            "fanout_refused": fan_refused, "fanout_rewritten": fan_rewritten,
            "target_seed_rank": rank,
            "recall_missed": missed,
            "semantic": (meta.get("semantic_seeding") or {}).get("state"),
            "recorded": t.get("recorded"),
        }
        row["pass"] = (len(tables_ok) == len(want_tables) and len(pairs_ok) == len(want_pairs)
                       and len(ident_ok) == len(exp.get("identity_for", [])) and len(cols_ok) == len(want_cols)
                       and (bool(carry) or not exp.get("carry_over")) and len(fan_ok) == len(want_fan))
        turns_out.append(row)
        # the turn as production would have persisted it: this enrichment + the recorded calls
        history.append({"turn_number": n, "user_query": t["query"],
                        "kg_enrichment_event": {"enrichments": [meta]} if meta else None,
                        "conversation_agent_events": synth_events(t.get("agent_calls"))})
    return {"id": fixture["id"], "graph": kg_id, "source": fixture.get("source"), "turns": turns_out,
            "pass": all(r["pass"] for r in turns_out)}


def run_all(semantic, only=None):
    db_path = os.path.join(ROOT, "tda_auth.db")
    out = []
    for f in sorted(glob.glob(os.path.join(FIXTURES, "*.json"))):
        fx = json.load(open(f))
        if only and only not in fx["id"]:
            continue
        out.append(asyncio.run(replay(fx, db_path, semantic)))
    return out


def summarize(results):
    print()
    for r in results:
        if r.get("skipped"):
            print(f"[{r['id']}] SKIPPED — {r['skipped']}"); continue
        print(f"[{r['id']}] {'PASS' if r['pass'] else 'FAIL'}")
        for t in r["turns"]:
            flags = []
            if t["tables_present"] != t["tables_expected"]: flags.append(f"tables {len(t['tables_present'])}/{len(t['tables_expected'])}")
            if len(t["join_pairs_keyed"]) != len(t["join_pairs_expected"]): flags.append(f"keys {len(t['join_pairs_keyed'])}/{len(t['join_pairs_expected'])}")
            if len(t["identity_present"]) != len(t["identity_expected"]): flags.append("identity")
            if len(t["columns_present"]) != len(t["columns_expected"]): flags.append(f"cols {len(t['columns_present'])}/{len(t['columns_expected'])}")
            if t["carry_over_expected"] and not t["carry_over"]: flags.append("no carry-over")
            if len(t.get("fanout_named") or []) != len(t.get("fanout_expected") or []): flags.append(f"fan-out {len(t.get('fanout_named') or [])}/{len(t.get('fanout_expected') or [])}")
            co = t["carry_over"] or {}
            print(f"  T{t['turn']} {'✓' if t['pass'] else '✗'} {t['query'][:48]:<48} {t['chars']:>6} chars {t['entities']:>4} ents  "
                  f"rank={t['target_seed_rank']} carry={co.get('source') or '-'} missed={t['recall_missed'] or '-'}"
                  + (f"  [{'; '.join(flags)}]" if flags else ""))


def compare(a, b):
    ra = {r["id"]: r for r in json.load(open(os.path.join(RESULTS_DIR, f"kg_conv_{a}.json")))}
    rb = {r["id"]: r for r in json.load(open(os.path.join(RESULTS_DIR, f"kg_conv_{b}.json")))}
    for fid in sorted(set(ra) | set(rb)):
        x, y = ra.get(fid), rb.get(fid)
        if not x or not y or x.get("skipped") or y.get("skipped"):
            print(f"[{fid}] present in both: {bool(x and y)}"); continue
        print(f"[{fid}] {'PASS' if x['pass'] else 'FAIL'} → {'PASS' if y['pass'] else 'FAIL'}")
        for ta, tb in zip(x["turns"], y["turns"]):
            print(f"  T{ta['turn']} chars {ta['chars']}→{tb['chars']}  ents {ta['entities']}→{tb['entities']}  "
                  f"tables {len(ta['tables_present'])}→{len(tb['tables_present'])}/{len(tb['tables_expected'])}  "
                  f"keys {len(ta['join_pairs_keyed'])}→{len(tb['join_pairs_keyed'])}/{len(tb['join_pairs_expected'])}  "
                  f"missed {len(ta['recall_missed'])}→{len(tb['recall_missed'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--compare", nargs=2)
    ap.add_argument("--semantic", choices=["on", "off"], default="on")
    ap.add_argument("--only")
    args = ap.parse_args()
    if args.compare:
        return compare(*args.compare)
    results = run_all(args.semantic == "on", args.only)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"kg_conv_{args.label}.json")
    json.dump(results, open(path, "w"), indent=1)
    summarize(results)
    print(f"\nwritten: {path}")
    sys.exit(0 if all(r.get("pass") or r.get("skipped") for r in results) else 1)


if __name__ == "__main__":
    main()
