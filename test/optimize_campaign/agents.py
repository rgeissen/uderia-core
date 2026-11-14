#!/usr/bin/env python3
"""The Optimize campaign's OWN agents — levers as A/B pairs, on one data source.

    python3 test/optimize_campaign/agents.py ensure            # create/update, seed the champion, activate, sign
    python3 test/optimize_campaign/agents.py ensure --models OpenRouter/xiaomi/mimo-v2.6-flash,OpenRouter/amazon/nova-2-lite-v1
    python3 test/optimize_campaign/agents.py status
    python3 test/optimize_campaign/agents.py teardown

Each lever is an agent that differs from `@OCFL` in ONE setting, so a run measures the lever:

    OCFL        the working model (MiMo v2.6 Flash), champion repository bound, governed surface
    OCFL-NORAG  same, no champion repository          → the RAG lift
    OCFL-ALL    same, every enabled tool offered      → the governed surface's token price
    OCWEAK      the weak model (Nova 2 Lite), as OCFL → the stress test: the gap OCFL − OCWEAK is
                                                       the lane's remaining dependence on intelligence

The operator said: start on Flash-Lite; when the lane holds there, go lower for hardening —
so `--models` lists the models to provision (the first is the working one), and the runner's
`--levers` picks which agents a run drives.

Working model since 2026-10-28: MiMo v2.6 Flash (Arena ~1448, $0.14/$0.28 per M on OpenRouter)
replaces Gemini 3.1 Flash-Lite (Arena 1432-1447, $0.25/$1.50, retired by Google 2027-05-07) —
the same quality band at a fraction of the price, and no direct-Google spend. Smoke test before
the switch (`20261028-173022` + three E3 re-runs): 4/4 graded cases right, fastpath fired, 0
waste, $0.0041 for five cases; one E3 run of four answered with a greeting instead of a plan.

The campaign owns its ingredients: an MCP entry on the public fitness endpoint, a planner
repository seeded with ONE champion (`suite.CHAMPION`), its LLM configurations. All listed
in `tda_eval/optimize_campaign/agents.json`; `teardown` removes exactly that.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import importlib.util as _ilu


def _load(path, name):
    spec = _ilu.spec_from_file_location(name, path)
    m = _ilu.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# the trust campaign's transport, loaded by PATH — both campaigns have a suite.py and an
# agents.py, so a sys.path entry would shadow one with the other
_tc = _load(os.path.join(HERE, "..", "trust_campaign", "agents.py"), "tc_agents")
BASE, USER, login, req = _tc.BASE, _tc.USER, _tc.login, _tc.req
from suite import CASES, CHAMPION  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
STATE_PATH = os.path.join(ROOT, "tda_eval", "optimize_campaign", "agents.json")
DEFAULT_MODELS = "OpenRouter/xiaomi/mimo-v2.6-flash,OpenRouter/amazon/nova-2-lite-v1"
DEFAULT_MCP = os.environ.get("CAMPAIGN_MCP", "uderia.com:8888")
MCP_NAME = "Optimize campaign MCP"
PLANNER_NAME = "Optimize campaign planner"
EMPTY_PLANNER_NAME = "Optimize campaign planner (empty — the cold lever)"
LLM_PREFIX = "Optimize campaign model"

# One posture: observe on every gate (a withheld answer cannot be scored for price), writes
# denied and ENFORCED on interactive turns (the campaign never writes), a budget wide enough
# never to be the thing that ends a turn, the outcome loop off (it would re-run the gate).
# `autonomous: True` is REQUIRED with `enforce_interactive`: under a principal a non-autonomous
# agent is denied EVERY tool ("not permitted to act autonomously", agent_authority.is_tool_allowed)
# — run 1 (2026-10-27) lost 24 cases to that before a single query ran.
POSTURE = {
    "postureConfig": {"mode": "observe"},
    "groundingConfig": {"mode": "observe"},
    "honestyConfig": {"mode": "observe"},
    "guardrailConfig": {"inbound": {"mode": "observe", "injection": True},
                        "outbound": {"mode": "observe", "pii": True, "toxicity": True, "exfil": True}},
    "authorityConfig": {"autonomous": True, "deny_writes": True, "enforce_interactive": True,
                        "denied_tools": ["base_writeQuery"], "max_cost_per_run": 1.0},
    "turnBudget": {"steps": 12, "tokens": 150000, "seconds": 300},
    "outcomeConfig": {"enabled": False, "maxRevisions": 0},
}
# chart ON (the charting passes need it), every other action component off, memory off
COMPONENTS = {k: {"enabled": False, "intensity": "medium"}
              for k in ("canvas", "dashboard", "flow", "knowledge_graph", "scheduler", "memory")}
COMPONENTS["chart"] = {"enabled": True, "intensity": "medium"}
COMPONENTS["context_window"] = {"enabled": True, "intensity": "medium"}


def load_state():
    try:
        return json.load(open(STATE_PATH))
    except Exception:  # noqa: BLE001
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    json.dump(st, open(STATE_PATH, "w"), indent=1)


def tag_for(model_spec: str, idx: int) -> str:
    return "OCFL" if idx == 0 else "OCWEAK"


# ── ingredients ──────────────────────────────────────────────────────────────
def ensure_llm_config(token, model_spec: str, idx: int):
    provider, _, model = model_spec.partition("/")
    name = f"{LLM_PREFIX} {idx + 1}"
    cfgs = req("GET", "/api/v1/llm/configurations", token).get("configurations") or []
    ex = next((c for c in cfgs if c.get("name") == name), None)
    if ex and (ex.get("provider") != provider or ex.get("model") != model):
        upd = dict(ex, provider=provider, model=model)
        upd.pop("credentials", None)
        r = req("PUT", f"/api/v1/llm/configurations/{ex['id']}", token, upd)
        if r.get("__http_error__"):
            sys.exit(f"could not retarget {name}: {json.dumps(r)[:200]}")
    if not ex:
        cid = f"oc-{idx + 1}-{abs(hash(model_spec)) % 10**8}"
        r = req("POST", "/api/v1/llm/configurations", token,
                {"id": cid, "name": name, "provider": provider, "model": model, "credentials": {}})
        if r.get("__http_error__"):
            sys.exit(f"could not create {name}: {json.dumps(r)[:200]}")
        ex = {"id": cid}
    t = req("POST", f"/api/v1/llm_configurations/{ex['id']}/test", token, {})
    if t.get("status") != "success":
        sys.exit(f"{model_spec} does not answer: {json.dumps(t)[:200]}")
    print(f"  model  {provider} {model} · {ex['id']}")
    return ex["id"]


def ensure_mcp(token, hostport, state):
    host, _, port = hostport.partition(":")
    servers = req("GET", "/api/v1/mcp/servers", token).get("servers") or []
    ex = next((s for s in servers if s.get("name") == MCP_NAME), None)
    if ex and (ex.get("host") != host or str(ex.get("port")) != str(port)):
        req("PUT", f"/api/v1/mcp/servers/{ex['id']}", token, dict(ex, host=host, port=port, path="/mcp"))
    if not ex:
        r = req("POST", "/api/v1/mcp/servers", token,
                {"name": MCP_NAME, "host": host, "port": port, "path": "/mcp", "transport": {"type": "http"}})
        if r.get("__http_error__") or r.get("status") == "error":
            sys.exit(f"could not create the MCP entry: {json.dumps(r)[:200]}")
        ex = {"id": r.get("server_id")}
    state["mcp_server_id"] = ex["id"]
    print(f"  mcp    {hostport}/mcp · {ex['id']}")
    return ex["id"]


def champions() -> list:
    """What the repository is seeded with: the one named champion plus every accuracy case's
    own question/oracle pair. The oracle IS a proven pattern — exactly what the learning loop
    stores after a successful turn — so `@OCFL` measures the WARM lane (operators' agents carry
    hundreds of these) and `@OCFL-NORAG` the cold one; their difference is the champion lift."""
    out = [dict(CHAMPION)]
    for c in CASES:
        if c["family"] == "A" and c.get("oracle_sql"):
            out.append({"user_query": c["prompt"], "sql_statement": c["oracle_sql"]})
    return out


def ensure_planner(token, mcp_id, state):
    """The champion repository, keyed to the campaign's MCP entry (a planner repository is
    loaded only for sessions on ITS server — the re-keying trap in deploy/mcp/README.md),
    seeded with `champions()`; re-seeded whenever it holds fewer rows than that."""
    cols = req("GET", "/api/v1/rag/collections", token).get("collections") or []
    ex = next((c for c in cols if c.get("name") == PLANNER_NAME), None)
    if not ex:
        r = req("POST", "/api/v1/rag/collections", token,
                {"name": PLANNER_NAME, "repository_type": "planner", "mcp_server_id": mcp_id,
                 "description": "The Optimize campaign's champion repository — one seeded case. "
                                "Provisioned by test/optimize_campaign/agents.py; safe to delete."})
        cid = (r.get("collection") or {}).get("id") or r.get("collection_id") or r.get("id")
        if not cid:
            sys.exit(f"could not create the planner repository: {json.dumps(r)[:300]}")
        ex = {"id": cid}
    cid = ex["id"]
    state["planner_collection_id"] = cid
    rows = req("GET", f"/api/v1/rag/collections/{cid}/rows", token)
    have = {((x.get("full_case_data") or {}).get("intent") or {}).get("user_query") or x.get("user_query")
            for x in (rows.get("rows") or rows.get("cases") or [])} if isinstance(rows, dict) else set()
    todo = [dict(ex, database_name="fitness_db") for ex in champions() if ex["user_query"] not in have]
    seeded = f"{len(have)} rows, nothing to add"
    if todo:
        r = req("POST", f"/api/v1/rag/collections/{cid}/populate", token,
                {"template_type": "sql_query", "template_id": "sql_query_v1", "examples": todo}, timeout=900)
        seeded = (f"+{len(todo)} seeded ({len(have)} were there)" if not r.get("__http_error__") and r.get("status") != "error"
                  else f"SEED FAILED: {json.dumps(r)[:200]}")
    s = req("POST", f"/api/v1/trust/planner/{cid}/sign-off", token, {"note": "optimize campaign — signed as provisioned"})
    # LOCK it: `planner.exact_signed_champion` replays a seed word for word only from a
    # trust-LOCKED repository (signing alone leaves trust_locked = 0 — campaign 1, OC-F3); a
    # locked planner never learns, which is right for a repository seeded on purpose
    lk = req("PUT", f"/api/v1/rag/collections/{cid}", token, {"trust_locked": True})
    print(f"  planner {PLANNER_NAME!r} · id {cid} · {seeded} · sign: {'signed' if s.get('signed') else s.get('reason') or '?'}"
          f" · lock: {'locked' if not lk.get('__http_error__') else json.dumps(lk)[:120]}")
    return cid


def ensure_empty_planner(token, mcp_id, state):
    """The cold lever's repository: EMPTY and locked. `ragCollections: []` does not mean "no
    repository" — the planner reads it as "no filter" and retrieves from every planner
    repository the user owns (run 2: the lever meant to be cold replayed champions from the
    operator's own repository and from the server's learning collection). Binding an empty
    repository is the only way to make an agent retrieve nothing."""
    cols = req("GET", "/api/v1/rag/collections", token).get("collections") or []
    ex = next((c for c in cols if c.get("name") == EMPTY_PLANNER_NAME), None)
    if not ex:
        r = req("POST", "/api/v1/rag/collections", token,
                {"name": EMPTY_PLANNER_NAME, "repository_type": "planner", "mcp_server_id": mcp_id,
                 "description": "Deliberately empty and locked — the Optimize campaign's cold lever. Safe to delete."})
        cid = (r.get("collection") or {}).get("id") or r.get("collection_id") or r.get("id")
        if not cid:
            sys.exit(f"could not create the empty planner: {json.dumps(r)[:200]}")
        ex = {"id": cid}
    req("PUT", f"/api/v1/rag/collections/{ex['id']}", token, {"trust_locked": True})
    req("POST", f"/api/v1/trust/planner/{ex['id']}/sign-off", token, {"note": "optimize campaign — empty, locked"})
    state["empty_planner_collection_id"] = ex["id"]
    print(f"  planner {EMPTY_PLANNER_NAME!r} · id {ex['id']} · locked")
    return ex["id"]


# ── the levers ───────────────────────────────────────────────────────────────
def specs(llm_ids: list, mcp, planner, empty_planner=None):
    common = dict(POSTURE)
    # The primer every new session starts with (the UI sends it as the first turn; the runner
    # does the same through REST with `is_session_primer`): what the operator's own Optimize
    # agents carry, in one statement — the lane is measured as operators run it.
    primer = {"enabled": True, "mode": "combined",
              "statements": ["You answer questions about the fitness_db database. Its tables are "
                             "fitness_db.Customers, fitness_db.Products, fitness_db.Sales, "
                             "fitness_db.SaleDetails and fitness_db.ServiceTickets; always use the "
                             "fully qualified fitness_db.<Table> name in SQL."]}
    common.update({"componentConfig": COMPONENTS, "skillsConfig": {"skills": []}, "session_primer": primer,
                   "contextWindowTypeId": "cwt-default-balanced", "classification_mode": "light",
                   "knowledgeConfig": {"enabled": False, "collections": []},
                   "profile_type": "tool_enabled", "useMcpTools": False, "mcpServerId": mcp})

    def mk(tag, name, llm, **over):
        d = dict(common)
        d.update({"tag": tag, "name": f"Optimize campaign · {name}", "llmConfigurationId": llm,
                  "ragCollections": [planner], "toolSurface": {"mode": "governed"},
                  "description": f"Optimize campaign lever ({name}) — provisioned by "
                                 "test/optimize_campaign/agents.py. Safe to delete."})
        d.update(over)
        return d

    out = {
        "OCFL": mk("OCFL", "working model, champion repository, governed surface", llm_ids[0]),
        "OCFL-NORAG": mk("OCFL-NORAG", "the cold lane — an empty champion repository", llm_ids[0],
                         ragCollections=[empty_planner] if empty_planner else []),
        "OCFL-ALL": mk("OCFL-ALL", "every enabled tool offered", llm_ids[0], toolSurface=None),
    }
    if len(llm_ids) > 1:
        out["OCWEAK"] = mk("OCWEAK", "weak model — the stress test", llm_ids[1])
    # The cross-lane lever (2026-10-29): the SAME questions on the ReAct lane (Ideate + tools) —
    # same model, MCP entry, posture, primer and governed surface as @OCFL, so a difference is
    # the lane's. No planner repository (the ReAct lane has no champions).
    out["OCREACT"] = mk("OCREACT", "the ReAct lane (Ideate + tools) — the cross-lane comparison",
                        llm_ids[0], profile_type="llm_only", useMcpTools=True, ragCollections=[])
    return out


def current(token):
    d = req("GET", "/api/v1/profiles", token)
    return {p.get("tag"): p for p in d.get("profiles") or []}, set(d.get("active_for_consumption_profile_ids") or [])


def upsert(token, spec, existing):
    if existing:
        body = dict(existing)
        body.update(spec)
        body["id"] = existing["id"]
        r = req("PUT", f"/api/v1/profiles/{existing['id']}", token, body)
        if r.get("__http_error__"):
            sys.exit(f"update @{spec['tag']} failed: {json.dumps(r)[:300]}")
        return existing["id"]
    r = req("POST", "/api/v1/profiles", token, spec)
    if r.get("__http_error__") or r.get("status") != "success":
        sys.exit(f"create @{spec['tag']} failed: {json.dumps(r)[:300]}")
    return (r.get("profile") or {}).get("id")


def activate(token, pid):
    r = req("POST", f"/api/v1/profiles/{pid}/activate", token, {}, timeout=600)
    ok = r.get("status") == "success" and not r.get("__http_error__")
    if not ok and r.get("needs_reclassification"):
        req("POST", f"/api/v1/profiles/{pid}/reclassify", token, {}, timeout=900)
        r = req("POST", f"/api/v1/profiles/{pid}/activate", token, {}, timeout=600)
        ok = r.get("status") == "success" and not r.get("__http_error__")
    bad = {k: v.get("message") for k, v in (r.get("test_results") or {}).items()
           if isinstance(v, dict) and v.get("status") == "error"}
    return ok, bad or r.get("message")


def ensure(token, models: str, mcp_hostport: str):
    print(f"ensure · {BASE} · user {USER}")
    state = load_state()
    specs_list = [m.strip() for m in models.split(",") if m.strip()]
    llm_ids = [ensure_llm_config(token, m, i) for i, m in enumerate(specs_list)]
    state["llm_config_ids"] = llm_ids
    state["models"] = specs_list
    mcp = ensure_mcp(token, mcp_hostport, state)
    planner = ensure_planner(token, mcp, state)
    empty = ensure_empty_planner(token, mcp, state)
    save_state(state)
    sp = specs(llm_ids, mcp, planner, empty)
    profs, _ = current(token)
    ids = {}
    for tag, spec in sp.items():
        ids[tag] = upsert(token, spec, profs.get(tag))
        print(f"  {tag:11s} {ids[tag]}  {'updated' if tag in profs else 'created'}")
    state["profiles"] = ids
    save_state(state)
    for tag, pid in ids.items():
        ok, why = activate(token, pid)
        print(f"  {tag:11s} activate  {'ok' if ok else 'FAILED'}" + ("" if ok else f"  {why}"))
    # the activation list is a WHOLE-LIST write whose FIRST entry is re-classified: put the
    # campaign's agents first (a live MCP) so an operator agent on a dead endpoint cannot
    # refuse the whole write (the seeded entry here points at a retired port)
    _, active = current(token)
    want = [pid for pid in ids.values()] + [a for a in sorted(active) if a not in set(ids.values())]
    r = req("POST", "/api/v1/profiles/set_active_for_consumption", token, {"profile_ids": want}, timeout=600)
    print(f"  activation list: {'ok' if not r.get('__http_error__') else json.dumps(r)[:160]}")
    # OC-F5: every lever learns into its OWN planner repository, LOCKED — so no lever warms
    # itself (or another) during a run and a measurement is repeatable; the cold lever is
    # finally cold (it used to read the shared server default back). Unlock one to measure
    # learning. Not a binding, so the agents' signatures do not move.
    for tag, pid in ids.items():
        if sp[tag].get("profile_type") != "tool_enabled":
            continue   # only the Optimize lane learns champions
        lr = req("POST", f"/api/v1/profiles/{pid}/learning-repository", token, {})
        rid = (lr.get("repository") or {}).get("id")
        if rid is not None and not (lr.get("repository") or {}).get("locked"):
            req("PUT", f"/api/v1/rag/collections/{rid}", token, {"trust_locked": True})
        print(f"  {tag:11s} learns  {'own repository ' + str(rid) + ' (locked)' if rid is not None else 'FAILED ' + json.dumps(lr)[:100]}")
    for tag, pid in ids.items():
        s = req("POST", f"/api/v1/trust/agent/{pid}/sign-off", token, {"note": "optimize campaign — signed as provisioned"})
        a = req("POST", f"/api/v1/trust/agent/{pid}/attest-deployment", token, {})
        print(f"  {tag:11s} sign  {'signed' if s.get('signed') else (s.get('reason') or '?')}"
              f"  · attest  {'attested' if a.get('signed') or a.get('status') == 'success' else (a.get('error') or '?')[:60]}")
    return ids


def status(token):
    profs, active = current(token)
    tr = {a.get("tag"): a for a in (req("GET", "/api/v1/trust/agents", token).get("agents") or [])}
    st = load_state()
    print(f"models: {st.get('models')}")
    for tag in ("OCFL", "OCFL-NORAG", "OCFL-ALL", "OCWEAK", "OCREACT"):
        p = profs.get(tag)
        if not p:
            print(f"  {tag:11s} (absent)")
            continue
        lr = req("GET", f"/api/v1/profiles/{p['id']}/learning-repository", token)
        learn = (f"own {lr['repository']['id']}{' locked' if lr['repository'].get('locked') else ''}"
                 if lr.get("repository") else lr.get("mode", "?"))
        print(f"  {tag:11s} active={'yes' if p['id'] in active else 'NO':3s} trust={tr.get(tag, {}).get('state', '?'):22s} "
              f"rag={p.get('ragCollections')} learns={learn} surface={(p.get('toolSurface') or {}).get('mode', 'all')} {p['id']}")


def teardown(token):
    st = load_state()
    profs, _ = current(token)
    for tag, pid in (st.get("profiles") or {}).items():
        if profs.get(tag, {}).get("id") == pid:
            r = req("DELETE", f"/api/v1/profiles/{pid}", token)
            print(f"  {tag:11s} delete {'ok' if not r.get('__http_error__') else json.dumps(r)[:120]}")
    for key, path in (("planner_collection_id", "/api/v1/rag/collections/{}"),
                      ("empty_planner_collection_id", "/api/v1/rag/collections/{}"),
                      ("mcp_server_id", "/api/v1/mcp/servers/{}")):
        if st.get(key):
            r = req("DELETE", path.format(st[key]), token)
            print(f"  {key} delete {'ok' if not r.get('__http_error__') else json.dumps(r)[:120]}")
    for cid in st.get("llm_config_ids") or []:
        r = req("DELETE", f"/api/v1/llm/configurations/{cid}", token)
        print(f"  llm {cid} delete {'ok' if not r.get('__http_error__') else json.dumps(r)[:120]}")
    save_state({})


def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("ensure", "status", "teardown"))
    ap.add_argument("--models", default=DEFAULT_MODELS, help="comma list of Provider/model; first = working model")
    ap.add_argument("--mcp", default=DEFAULT_MCP)
    a = ap.parse_args(argv)
    token = login()
    if a.cmd == "ensure":
        ensure(token, a.models, a.mcp)
        status(token)
    elif a.cmd == "status":
        status(token)
    else:
        teardown(token)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
