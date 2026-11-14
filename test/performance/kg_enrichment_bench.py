"""
Offline Knowledge-Graph enrichment benchmark — the retrieval stage measured exactly as
the planner receives it, with NO LLM: seeds → adaptive subgraph → capabilities →
rendered text, per question, per graph, per module budget.

Reads the real graphs on this instance (kg_id-addressed) and records, per question:
tokens rendered, nodes, whether the decision-relevant target is present, section sizes,
SQLite connections opened, wall time. Compare two runs to prove a change moved the
numbers it claims to move and nothing else.

Run:  PYTHONPATH=src python3 test/performance/kg_enrichment_bench.py --label before
      PYTHONPATH=src python3 test/performance/kg_enrichment_bench.py --compare before after
"""
import argparse
import asyncio
import inspect
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Determinism, for the same reason `usage_priors={}` is passed below: this harness
# exists to prove a CHANGE moved the numbers it claims to move. Semantic seeding ships
# ON, and whether its index happens to be warm for a graph is a property of the machine
# and the run order, not of the change under test — a before/after that includes it
# would compare index warmth. Measure the semantic channel with kg_semantic_bench.py.
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
APP_CONFIG.KG_SEMANTIC_SEEDING = False

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
USER = os.environ.get("UDERIA_USER_UUID", "1729670e-1eb5-45ca-bf1f-f4096b4d74b4")

GRAPHS = {
    "policing": ("profile-457a0455-c32f-4271-bdfe-673059ad727c", "82c63794-74fc-47fe-a2c5-e3946822fce3", [
        ("how many victims are female?", "V4019"),
        ("which offenses are most common?", "Offense"),
        ("how many arrestees were juveniles?", "Arrestee"),
        ("can I join CASE investigations to NIBRS incidents?", "Investigation To Incident"),
        ("which columns in the Victim table contain PII?", "Victim"),
        ("how many incidents occurred in 2023?", "INCDATE"),
        ("what evidence exhibits are linked to investigation 2023-INC-008912?", "Exhibit"),
    ]),
    "airlines": ("profile-1779124206778-s0nlcsqwl", "61883e64-9d6a-4526-ba31-ca38abadc7d5", [
        ("which stations have the most baggage irregularities?", "baggage_irregularity"),
        ("what is the total budget per cost centre?", "cost_centre"),
        ("which flights were delayed by weather last month?", "flight"),
        ("average revenue per passenger by route?", "route"),
    ]),
    "fitness": ("profile-1779124206778-s0nlcsqwl", "00f2e150-8f6e-4159-bc5e-9526bcbda24d", [
        ("what are the top 5 products based on revenue generated?", "TotalRevenue"),
        ("what are the top 5 customers based on revenue generated?", "Sales"),
        ("which customer state has the most tickets?", "ServiceTickets"),
    ]),
}
BUDGETS = {"gemini_1m": 73819, "k128": 9227, "k32": 2306}


def _sections(txt):
    out, cur = {}, "head"
    for line in (txt or "").splitlines():
        s = line.strip()
        if s and (s.endswith("ENTITIES:") or s.startswith("TABLE SCHEMAS") or s.startswith("JOINABLE COLUMNS")
                  or s.startswith("KNOWN RELATIONSHIPS") or s.startswith("CODED") or s.startswith("METRIC ENTITIES")
                  or s.startswith("SIGNED DATA PRODUCTS") or s.startswith("REPEATING GROUPS")):
            cur = s.split("(")[0].strip(":").strip()[:30]
        out[cur] = out.get(cur, 0) + len(line) + 1
    return out


async def run_all():
    from components.builtin.knowledge_graph import handler as kgh
    from components.builtin.knowledge_graph.graph_store import GraphStore
    from trusted_data_agent.core import sqlite_util
    cls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_search_entities_for_query")][0]
    h = cls.__new__(cls)
    counter = {"n": 0}
    orig = sqlite_util.connect

    def spy(*a, **k):
        counter["n"] += 1
        return orig(*a, **k)
    sqlite_util.connect = spy

    rows = []
    for gname, (pid, kgid, qs) in GRAPHS.items():
        store = GraphStore(pid, USER, kg_id=kgid)
        for q, target in qs:
            row = {"graph": gname, "query": q, "target": target}
            counter["n"] = 0
            t0 = time.perf_counter()
            seeds = await h._search_entities_for_query(store, q, obda_surface=("off", frozenset()), usage_priors={})  # {} = deterministic (ignore production telemetry)
            names = [e.get("name") or "" for e in seeds]
            row["seeds"] = len(seeds)
            row["target_rank"] = next((i + 1 for i, n in enumerate(names) if target.lower() in n.lower()), None)
            eids = [e["id"] for e in seeds]
            for label, budget in BUDGETS.items():
                # the module's own budget → node-cap mapping, kept in step with the module
                try:
                    from importlib import import_module
                    mod = import_module("components.builtin.context_window.modules.knowledge_graph.handler")
                    M = getattr(mod, "KnowledgeGraphContextModule")
                    max_nodes = M._budget_to_max_nodes(M.__new__(M), budget)
                except Exception:  # noqa: BLE001 — module import shape may differ; fall back to the formula
                    max_nodes = max(20, min(500, budget // 30))
                # kg-id addressed path (the profile's assignment may differ from kgid): render directly
                scope = {}
                if hasattr(kgh, "_intensity_column_cap"):   # Phase B: mirror the handler's scoped extraction (default intensity)
                    scope = {"query_terms": kgh._scoring_terms(q), "per_table_cap": kgh._intensity_column_cap({})}
                sg = store.extract_subgraph_adaptive(seed_entity_ids=eids, query_entity_ids=eids, max_nodes=max_nodes, **scope) if eids else {"entities": [], "relationships": []}
                caps = await h._resolve_capabilities(store, sg["entities"]) if sg["entities"] else {}
                text = h._format_subgraph_for_prompt(sg, caps, surface="off") if sg["entities"] else ""
                tok = len(text) // 4
                # replicate the module's trailing-line trim
                if tok > budget:
                    kept, used = [], 0
                    for line in text.split("\n"):
                        t = max(1, len(line) // 4)
                        if used + t > budget and kept:
                            break
                        kept.append(line)
                        used += t
                    delivered = "\n".join(kept)
                else:
                    delivered = text
                row[label] = {
                    "max_nodes": max_nodes, "nodes": len(sg["entities"]), "rels": len(sg["relationships"]),
                    "rendered_tokens": tok, "delivered_tokens": len(delivered) // 4,
                    "target_in_delivered": target.lower() in delivered.lower(),
                    "schemas": "TABLE SCHEMAS" in delivered, "joinable": any(b in delivered for b in ("JOINABLE COLUMNS", "DECLARED FOREIGN KEYS", "NOT JOIN KEYS")),  # join GUIDANCE present: a hint reclassified as a declared key or a NOT-key is guidance kept, not lost
                    "relationships": "KNOWN RELATIONSHIPS" in delivered,
                    "sections": _sections(text) if label == "gemini_1m" else None,
                    "text_sha": __import__("hashlib").sha256(text.encode()).hexdigest()[:16],
                }
            row["sqlite_connections"] = counter["n"]
            row["seconds"] = round(time.perf_counter() - t0, 3)
            rows.append(row)
            g = row["gemini_1m"]
            print(f"[{gname:8}] {q[:48]:48} rank={row['target_rank']} nodes={g['nodes']:3} tok={g['rendered_tokens']:6} "
                  f"k128={row['k128']['delivered_tokens']:5}{'✓' if row['k128']['target_in_delivered'] else '✗'} "
                  f"k32={row['k32']['delivered_tokens']:5}{'✓' if row['k32']['target_in_delivered'] else '✗'} "
                  f"conn={row['sqlite_connections']:3} {row['seconds']:.2f}s")
    return rows


def summarize(rows):
    def mean(v):
        return sum(v) / len(v) if v else 0
    out = {}
    for g in sorted({r["graph"] for r in rows}):
        rs = [r for r in rows if r["graph"] == g]
        out[g] = {
            "n": len(rs),
            "rendered_tokens_mean": round(mean([r["gemini_1m"]["rendered_tokens"] for r in rs])),
            "delivered_1m_mean": round(mean([r["gemini_1m"]["delivered_tokens"] for r in rs])),
            "delivered_128k_mean": round(mean([r["k128"]["delivered_tokens"] for r in rs])),
            "target_present_128k": sum(1 for r in rs if r["k128"]["target_in_delivered"]),
            "target_present_32k": sum(1 for r in rs if r["k32"]["target_in_delivered"]),
            "schemas_present_1m": sum(1 for r in rs if r["gemini_1m"]["schemas"]),
            "target_rank_hits": sum(1 for r in rs if r["target_rank"] and r["target_rank"] <= 3),
            "sqlite_connections_mean": round(mean([r["sqlite_connections"] for r in rs]), 1),
            "seconds_mean": round(mean([r["seconds"] for r in rs]), 3),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--compare", nargs=2)
    a = ap.parse_args()
    os.makedirs(RESULTS_DIR, exist_ok=True)
    if a.compare:
        A = json.load(open(os.path.join(RESULTS_DIR, f"kg_bench_{a.compare[0]}.json")))["summary"]
        B = json.load(open(os.path.join(RESULTS_DIR, f"kg_bench_{a.compare[1]}.json")))["summary"]
        for g in A:
            print(f"\n== {g}")
            for k in A[g]:
                va, vb = A[g][k], B.get(g, {}).get(k)
                d = f"{(vb - va) / va * 100:+.0f}%" if isinstance(va, (int, float)) and va and isinstance(vb, (int, float)) else ""
                print(f"  {k:26} {va!s:>10} → {vb!s:>10}  {d}")
        return
    rows = asyncio.run(run_all())
    out = {"label": a.label, "ts": time.time(), "rows": rows, "summary": summarize(rows)}
    path = os.path.join(RESULTS_DIR, f"kg_bench_{a.label}.json")
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(out["summary"], indent=1))
    print("saved", path)


if __name__ == "__main__":
    main()
