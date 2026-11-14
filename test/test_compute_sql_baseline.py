"""PROP-006 phase 1 baseline freeze — committed GREEN before the combine step (`TDA_Compute`
op "sql") landed. Regression assessment: docs/assessments/MULTI_SOURCE_REGRESSION_ASSESSMENT_2026-09.md.

Pins what the combine step must NOT change for anyone who does not use it:
  [1] every existing Compute receipt hashes byte-identically, and a key outside the closed
      receipt field set never moves a hash (C1 — sealed provenance chains keep verifying);
  [2] the Compute tool a model sees — its arguments, its description, its instruction text at
      every intensity — with Compute with Lineage ON and the combine switch absent (A7);
  [3] the shape of a registered statement and the registry bounds (the named exceptions A8
      `complete` and A14 as-of time will be declared here when they land, never silently);
  [4] the governed-data lane scan and the honesty gate's data-query reading of a turn with a SQL
      call beside a Compute call (C2, C3);
  [5] Agent Authority under an exclusive data surface: a Compute call is allowed, raw SQL is
      denied (C4);
  [6] the preflight fact namespaces (C5);
  [7] the planner's Compute-insertion pass inserts over a data phase, and never inserts a second
      Compute phase when the plan already has one (O2);
  [8] `guarded_sql_call`'s signature (C6/C7 — the combine calls it; a new parameter must default
      to today's behaviour).

Every assertion here stays green through PROP-006 phase 1. A change that is meant to alter one of
these is added as a NAMED exception with its own assertion, never by editing a pinned literal away.
"""
import copy, hashlib, inspect, json, logging, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
logging.disable(logging.CRITICAL)
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

def _sha(s): return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

_SAVED = {k: getattr(APP_CONFIG, k, None) for k in ("COMPUTE_LINEAGE", "COMPUTE_SQL", "OBDA_ENABLED")}
try:
    from trusted_data_agent.agent import compute as C

    A = [{"region": "EMEA", "amount": 100, "k": 1}, {"region": "APAC", "amount": 200, "k": 2},
         {"region": "EMEA", "amount": 50, "k": 3}]
    B = [{"k": 1, "name": "a"}, {"k": 2, "name": "b"}, {"k": 4, "name": "d"}]
    B2 = [{"k": 1, "amount": 90}, {"k": 2, "amount": 200}, {"k": 4, "amount": 7}]

    print("[1] existing Compute receipts hash byte-identically (C1)")
    check("the receipt field set is unchanged", C.RECEIPT_FIELDS == (
        "op", "column", "by", "on", "n", "order", "where", "statements", "row_count",
        "row_count_used", "columns", "value", "values", "result_rows"), C.RECEIPT_FIELDS)
    check("the op set is unchanged", C.OPS == (
        "sum", "avg", "min", "max", "count", "rank", "pivot", "diff", "join", "pct_change", "filter"), C.OPS)
    pinned = {
        "sum": ("d001f4eb84842a60", dict(op="sum", rows=A, column="amount", statements=[1])),
        "avg": ("38442446a93a9354", dict(op="avg", rows=A, column="amount", statements=[1])),
        "rank": ("f957ba1e24f0501d", dict(op="rank", rows=A, column="amount", n=2, statements=[1])),
        "pivot": ("e4707692c67b8b76", dict(op="pivot", rows=A, column="amount", by="region", statements=[1])),
        "filter": ("5e31c1bbb091e2bd", dict(op="filter", rows=A, where="amount > 60", statements=[1])),
        "join": ("0ed9a86eb34fa802", dict(op="join", rows=A, rows_b=B, on="k", statements=[1, 2])),
        "diff": ("e1e994f0909d8070", dict(op="diff", rows=A, rows_b=B2, on="k", column="amount", statements=[1, 2])),
    }
    for name, (want, kw) in pinned.items():
        kw = dict(kw); op = kw.pop("op"); rows = kw.pop("rows")
        r = C.compute(op, rows, **kw)["receipt"]
        check(f"receipt hash of `{name}` unchanged", r["receipt_hash"] == want, (r["receipt_hash"], want))
        check(f"`{name}` receipt_hash == hash of its sealed content", r["receipt_hash"] == _sha(C.receipt_content(r)))
    r = C.compute("sum", A, column="amount", statements=[1])["receipt"]
    check("a key OUTSIDE the closed field set never moves the hash (the combine's `sql`/`inputs` must not)",
          C.receipt_hash(r) == C.receipt_hash(dict(r, sql="SELECT 1", inputs=[1, 2])))

    print("[2] the Compute tool a model sees (Compute ON, combine switch absent) (A7)")
    APP_CONFIG.COMPUTE_LINEAGE = True
    if hasattr(APP_CONFIG, "COMPUTE_SQL"):
        APP_CONFIG.COMPUTE_SQL = False
    from trusted_data_agent.components.manager import get_component_manager
    cm = get_component_manager()
    comp = cm.get_component("compute")
    check("the compute component is discovered", comp is not None)
    td = cm.tool_definition_for(comp, {})
    check("the tool's argument set is unchanged", sorted((td.get("args") or {}).keys()) ==
          ["agg", "by", "column", "n", "on", "op", "order", "order_by", "statement", "statement_b", "where"],
          sorted((td.get("args") or {}).keys()))
    check("the tool definition is byte-identical", _sha(json.dumps(td, sort_keys=True)) == "238f95a783b4069e",
          _sha(json.dumps(td, sort_keys=True)))
    for intensity, want in (("light", "8dadb28084bd6972"), ("medium", "8dadb28084bd6972"),
                            ("heavy", "654ddf5feefda75e"), ("none", "e3b0c44298fc1c14")):
        text = cm.apply_instruction_gates(comp.get_instructions(intensity), {}) or ""
        check(f"instruction text at `{intensity}` is byte-identical", _sha(text) == want, (_sha(text), want))

    print("[3] a registered statement's shape and the registry bounds (A8/A14 land as named exceptions)")
    from trusted_data_agent.agent import result_sets as RS
    check("bounds unchanged", (RS.MAX_ROWS, RS.MAX_STATEMENTS, RS.SAMPLE_ROWS) == (5000, 40, 50),
          (RS.MAX_ROWS, RS.MAX_STATEMENTS, RS.SAMPLE_ROWS))
    RS.begin_turn("freeze-s")
    n = RS.record("freeze-s", "base_readQuery", {"sql": "SELECT a FROM t"}, {"results": [{"a": 1}, {"a": 2}]})
    st = RS.statements("freeze-s")[0]
    check("a tool result is statement 1", n == 1, n)
    # NAMED EXCEPTION (PROP-006 A14, deliberate, beneficial to single-source agents too): a
    # statement records when its rows arrived. It is the ONLY key added; everything else as frozen.
    check("the statement's keys are unchanged except the named exception `ts` (A14)",
          sorted(st.keys()) == ["columns", "n", "row_count", "rows", "statement", "tool", "truncated", "ts"], sorted(st.keys()))
    check("A14: `ts` is an ISO-8601 UTC time", isinstance(st.get("ts"), str) and st["ts"].endswith("+00:00"), st.get("ts"))
    # NAMED EXCEPTION (PROP-006, source attribution for A13 / lineage / Live Status): a statement
    # whose producing source is known carries `source_id`; with no source given nothing is added.
    RS.record("freeze-s", "base_readQuery", {"sql": "SELECT b FROM u"}, {"results": [{"b": 1}]}, source="mcp:srv1")
    st_src = RS.statements("freeze-s")[-1]
    check("a known source adds exactly `source_id` (named exception)",
          sorted(set(st_src.keys()) - set(st.keys())) == ["source_id"] and st_src["source_id"] == "mcp:srv1",
          sorted(st_src.keys()))
    check("a framework tool is never a statement", RS.record("freeze-s", "TDA_Compute", {"op": "sum"}, {"results": [{"a": 1}]}) is None)
    big = [{"a": i} for i in range(RS.MAX_ROWS + 3)]
    RS.record("freeze-s", "base_readQuery", {"sql": "SELECT a FROM big"}, {"results": big})
    st2 = RS.statements("freeze-s")[-1]   # the statement just registered
    check("the registry marks its own cut", st2["truncated"] is True and len(st2["rows"]) == RS.MAX_ROWS
          and st2["row_count"] == RS.MAX_ROWS + 3, (st2["truncated"], len(st2["rows"]), st2["row_count"]))

    print("[4] the lane scan and the honesty reading of SQL beside Compute (C2, C3)")
    from trusted_data_agent.agent import obda
    calls = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"},
              "result": {"status": "success", "results": [{"a": 1}]}},
             {"tool_name": "TDA_Compute", "arguments": {"op": "sum", "column": "a"}, "result": {"status": "success"}}]
    scan = obda.scan_obda(calls)
    for k, want in (("freeform_count", 1), ("governed_count", 0), ("denied_count", 0), ("rejected_count", 0),
                    ("total_data_ops", 1), ("has_data_ops", True)):
        check(f"scan_obda.{k} unchanged", scan.get(k) == want, (scan.get(k), want))
    from trusted_data_agent.agent.honesty import primitives as HP
    check("the honesty gate reads the SQL call as a data query and the Compute call as none",
          [HP._is_raw_sql(c["arguments"]) for c in calls] == [True, False])
    check("data_query_outcomes unchanged", HP.data_query_outcomes(
        [{"tool_name": c["tool_name"], "arguments": c["arguments"], "status": "success", "result": c["result"]}
         for c in calls]) == (1, 1, 0, ""))

    print("[5] Agent Authority under an exclusive data surface (C4)")
    from trusted_data_agent.core import agent_authority as AA
    _OP.force("read", True)
    sc = AA.parse_scope({"dataSurface": {"products": ["p"], "exclusive": True}})
    check("a Compute call is allowed", AA.is_tool_allowed(sc, "TDA_Compute", arguments={"op": "sum"})[0] is True)
    check("raw SQL is denied", AA.is_tool_allowed(sc, "base_readQuery", arguments={"sql": "SELECT 1"})[0] is False)

    print("[6] preflight fact namespaces (C5)")
    from trusted_data_agent.agent.preflight import dialect as D
    with D.using("duckdb", ""):
        check("DuckDB with no source keys by dialect", D.ns("s1") == "s1::duckdb", D.ns("s1"))
    with D.using("duckdb", "combine"):
        check("DuckDB with source `combine` keys by it", D.ns("s1") == "s1::combine", D.ns("s1"))
    with D.using("teradata", ""):
        check("Teradata shares the session's facts", D.ns("s1") == "s1", D.ns("s1"))

    print("[7] the planner's Compute-insertion pass (O2)")
    from trusted_data_agent.agent.planner import Planner
    class _Ex: pass
    class _Self: pass
    def _run(plan):
        ex = _Ex(); ex.meta_plan = plan; ex.user_uuid = None
        ex._get_profile_config = lambda: {"profile_type": "tool_enabled"}
        ex._log_system_event = lambda e: None; ex._format_sse_with_depth = lambda e: e
        s = _Self(); s.executor = ex
        s._SYNTHESIS_TOOLS = Planner._SYNTHESIS_TOOLS; s._COMPUTE_INTENTS = Planner._COMPUTE_INTENTS
        list(Planner._rewrite_plan_for_compute_insertion(s))   # a generator: it runs only when consumed
        return [p.get("relevant_tools") for p in ex.meta_plan]
    base = [{"phase": 1, "goal": "get sales", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT amount FROM s"}},
            {"phase": 2, "goal": "Report the total sales", "relevant_tools": ["TDA_FinalReport"]}]
    check("control: a total over a data phase gets ONE Compute phase inserted",
          _run(copy.deepcopy(base)) == [["base_readQuery"], ["TDA_Compute"], ["TDA_FinalReport"]], _run(copy.deepcopy(base)))
    # The existing Compute phase is NOT directly before the report: a DATA phase is. So the only
    # thing that stops a second insertion is the "a phase already computes" guard itself — with the
    # Compute phase adjacent, the separate "the phase before must be a data phase" rule masked it.
    with_c = [{"phase": 1, "goal": "get sales", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT amount FROM s"}},
              {"phase": 2, "goal": "combine", "relevant_tools": ["TDA_Compute"], "arguments": {"op": "sql"}},
              {"phase": 3, "goal": "get targets", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT target FROM t"}},
              {"phase": 4, "goal": "Report the total sales against target", "relevant_tools": ["TDA_FinalReport"]}]
    check("a plan that already computes gets no second Compute phase (guard isolated)",
          _run(with_c) == [["base_readQuery"], ["TDA_Compute"], ["base_readQuery"], ["TDA_FinalReport"]], _run(with_c))

    print("[8] guarded_sql_call's signature (C6, C7)")
    from trusted_data_agent.agent import tool_enforcement as TE
    sig = inspect.signature(TE.guarded_sql_call)
    params = list(sig.parameters)
    check("the positional contract is unchanged", params[:3] == ["tool_name", "arguments", "execute"], params)
    check("today's keyword parameters are all still present",
          {"session_id", "user_uuid", "dialect", "aliases", "source_id"} <= set(params), params)
    added = [p for p in params[3:] if p not in {"session_id", "user_uuid", "dialect", "aliases", "source_id"}]
    check("any parameter added later has a default (existing callers unchanged)",
          all(sig.parameters[p].default is not inspect.Parameter.empty for p in added), added)
finally:
    for k, v in _SAVED.items():
        if v is None and hasattr(APP_CONFIG, k) and k == "COMPUTE_SQL":
            try: delattr(APP_CONFIG, k)
            except Exception: pass
        elif v is not None or hasattr(APP_CONFIG, k):
            setattr(APP_CONFIG, k, v)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)  — the combine step changes nothing for anyone who does not use it")
sys.exit(1 if FAIL else 0)
