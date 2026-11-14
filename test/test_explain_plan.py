#!/usr/bin/env python3
"""The EXPLAIN Plan component: Teradata plan prose → a step graph and findings, read from the
statement the agent's own SQL tool ran, never re-run.

The parser is driven over six real plans captured from Teradata 20.00.24.60
(test/fixtures/teradata_explain_plans.json); the handler through the real result-set
registry; the Navigator guard admits EXPLAIN only where asked. CI-pure.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
sys.path.insert(0, ".")

passed = 0
failures = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
        print(f"  FAIL  {fn.__name__} raised {type(exc).__name__}: {exc}")


from components.builtin.explain_plan import plan_parser as P  # noqa: E402

FIX = json.loads(Path("test/fixtures/teradata_explain_plans.json").read_text())


def plan_of(key):
    return P.parse(P.explain_text([{"Explanation": l} for l in FIX[key]["lines"]]))


def kinds(plan):
    return {f["kind"]: f["steps"] for f in plan["findings"]}


def t_parser():
    print("\n[1] the parser over real Teradata plans")
    p = plan_of("join_agg")
    ops = [(s["id"], s["op"]) for s in p["steps"]]
    check("locks, work steps and END TRANSACTION in order",
          ops == [("1", "LOCK"), ("2", "LOCK"), ("3", "LOCK"), ("4", "RETRIEVE"), ("5", "JOIN"), ("6", "SUM"),
                  ("7", "RETRIEVE"), ("8", "END TRANSACTION")], ops)
    check("lock steps name the tables and the mode",
          [s.get("locks") for s in p["steps"][:3]] == [[{"table": "DEMO_USER.o", "mode": "read"}],
                                                       [{"table": "DEMO_USER.c", "mode": "read"}],
                                                       [{"table": "DEMO_USER.o", "mode": "read"}, {"table": "DEMO_USER.c", "mode": "read"}]],
          [s.get("locks") for s in p["steps"][:3]])
    s4, s5 = p["steps"][3], p["steps"][4]
    check("a step's scope, input, output, distribution, estimate and confidence",
          (s4["scope"], s4["inputs"], s4["output"], s4["distribution"], s4["est_rows"], s4["confidence"])
          == ("all-AMPs", ["DEMO_USER.c"], "Spool 3", "duplicated", 768, "low"), s4)
    check("a join's two inputs and its method",
          s5["inputs"] == ["Spool 3", "DEMO_USER.o"] and s5["join_method"] == "dynamic hash join", s5)
    check("spools become edges, tables become sources",
          [(e["from"], e["to"]) for e in p["edges"]]
          == [("table:DEMO_USER.c", "4"), ("4", "5"), ("table:DEMO_USER.o", "5"), ("5", "6"), ("6", "7")], p["edges"])
    check("the step whose spool is returned", p["result_steps"] == ["7"] and p["total_seconds"] == 0.01)
    check("findings: no confidence (one finding, both steps), duplication, full scan",
          kinds(p) == {"no_confidence": ["6", "7"], "duplicated": ["4"], "full_scan": ["4", "5"]}, kinds(p))

    p = plan_of("product")
    check("a product join is a danger finding and comes first",
          p["findings"][0]["kind"] == "product_join" and p["findings"][0]["severity"] == "danger"
          and p["findings"][0]["steps"] == ["5"], p["findings"][:1])
    check("an index access is not a full scan", "6" not in kinds(p).get("full_scan", []))

    p = plan_of("upi")
    s = p["steps"][0]
    check("a single-AMP read by the unique primary index", (s["op"], s["scope"], s["access"], s["by_index"])
          == ("RETRIEVE", "single-AMP", ["unique primary index"], True), s)
    check("…has no finding", p["findings"] == [], p["findings"])

    p = plan_of("three")
    check("a merge join with redistribution", p["steps"][4]["join_method"] == "merge join"
          and p["steps"][4]["distribution"] == "redistributed" and p["steps"][4]["sort"] is True)
    check("…three source tables", p["sources"] == ["DEMO_USER.o", "DEMO_USER.i", "DEMO_USER.p"], p["sources"])

    p = plan_of("parallel")
    ids = [s["id"] for s in p["steps"]]
    check("parallel steps are numbered inside their group", "5.1" in ids and "5.2" in ids
          and next(s for s in p["steps"] if s["id"] == "5.2")["parallel_group"] == "5", ids)
    check("…and the step after the group is top-level again", next(s for s in p["steps"] if s["id"] == "6")["parallel_group"] is None)
    check("…the join reads both branches", {(e["from"], e["to"]) for e in p["edges"]} >= {("7", "8"), ("5.1", "8")})

    p = plan_of("union")
    check("both UNION branches produce the returned spool", p["result_steps"] == ["4", "5"], p["result_steps"])

    check("an unrecognised step is kept, not dropped",
          P.parse("1) We frobnicate the widgets.\n-> done")["steps"][0]["op"] == "other")
    check("text that is no plan → no steps", P.parse("hello world")["steps"] == [])
    check("explain_text reads any single-column rows, in order",
          P.explain_text([{"x": "a"}, {"x": "b"}]) == "a\nb" and P.explain_text([]) == "")
    check("is_explain", P.is_explain(" explain SELECT 1") and not P.is_explain("SELECT 'explain'"))


def t_summary():
    print("\n[2] what the model reads")
    s = P.summary(plan_of("product"))
    check("the work steps, one line each, no locks", "LOCK" not in s and "5) JOIN, all-AMPs, from Spool 3 + DEMO_USER.o, product join" in s, s)
    check("the findings with their severity", "[danger] Step 5 joins with a product join" in s)
    check("singular and plural read right", "~1 row," in s and "Step 5 estimates its row count" in s, s)
    check("a clean plan says so", "Findings: none of the checked patterns" in P.summary(plan_of("upi")))
    check("long plans are bounded", "(+" in P.summary(plan_of("parallel"), limit=2))


def t_handler():
    print("\n[3] the handler reads the agent's own EXPLAIN result")
    from trusted_data_agent.core.config import APP_CONFIG
    from trusted_data_agent.agent import result_sets as RS
    from components.builtin.explain_plan.handler import ExplainPlanHandler
    saved = getattr(APP_CONFIG, "COMPUTE_LINEAGE", False)
    APP_CONFIG.COMPUTE_LINEAGE = True
    try:
        h = ExplainPlanHandler()
        check("relevant when the registry is on", h.is_profile_relevant({}) is True)
        sid = "s-explain"
        RS.begin_turn(sid, 1)
        call = lambda a: asyncio.run(h.process(a, {"session_id": sid}))
        out = call({})
        check("no EXPLAIN yet → recovery-shaped refusal naming the step",
              out.metadata.get("error_code") == "no_explain" and "EXPLAIN <the SELECT>" in out.spec["error"]
              and out.metadata.get("suppress_inline"), out.spec)
        RS.record(sid, "base_readQuery", {"sql": "SELECT COUNT(*) FROM orders"}, {"results": [{"n": 1000}]})
        RS.record(sid, "base_readQuery", {"sql": "EXPLAIN SELECT COUNT(*) FROM orders o, customers c WHERE o.amount > c.income"},
                  {"results": [{"Explanation": l} for l in FIX["product"]["lines"]]})
        out = call({})
        spec = out.spec
        check("the latest EXPLAIN is read", spec.get("statement") == 2 and "error" not in spec, spec.get("error"))
        check("…its SQL without the keyword", spec["sql"].startswith("SELECT COUNT(*) FROM orders o"))
        check("…steps, edges, findings and the summary", spec["steps"] and spec["edges"] and spec["findings"]
              and spec["summary"].startswith("Plan: 4 work steps"))
        check("the title names the step count and the worst finding",
              out.title == "Plan of statement 2 · 4 steps · total est. 0.01 s · product join", out.title)
        check("the model-facing metadata carries the summary and the finding kinds",
              out.metadata["summary"] == spec["summary"] and out.metadata["findings"][0]["kind"] == "product_join")
        check("no html: the renderer draws it (chat, reload and a dashboard tile alike)", not out.html)
        out = call({"statement": 1})
        check("a statement that is not an EXPLAIN → refused, named", out.metadata.get("error_code") == "not_an_explain", out.spec)
        out = call({"statement": 9})
        check("a statement that did not run → refused, the EXPLAIN statements listed",
              out.metadata.get("error_code") == "no_such_statement" and "EXPLAIN statements: 2" in out.spec["error"], out.spec)
        check("a non-numeric statement is refused before reading", call({"statement": "two"}).metadata.get("error_code") == "bad_arguments")
        RS.record(sid, "base_readQuery", {"sql": "EXPLAIN SELECT 1"}, {"results": [{"QUERY PLAN": "Seq Scan on t"}]})
        out = call({})
        check("a plan that is not Teradata's → refused as unreadable", out.metadata.get("error_code") == "unparsed", out.spec)
        long_text = [{"Explanation": l} for l in FIX["join_agg"]["lines"]]
        RS.record(sid, "base_readQuery", {"sql": "EXPLAIN SELECT x"}, {"results": long_text})
        st = RS.get(sid, 4)
        st["truncated"] = True
        out = call({"statement": 4})
        check("a cut plan says so to the model", "later steps are missing" in out.spec["summary"] and out.spec["truncated"])
        # The production turn (fba79782 turn 2, Oct 2026): the previous turn explained
        # statement 1, the follow-up re-sent `statement: 1` without running anything, and
        # the refusal's bare "run EXPLAIN first" was read as an instruction for the USER —
        # the agent handed the step back. The refusal must name the per-turn numbering and
        # the agent as the one who acts.
        RS.begin_turn(sid, 2)
        out = call({"statement": 1})
        err = out.spec.get("error", "")
        check("a number from an earlier turn → refused as no_such_statement",
              out.metadata.get("error_code") == "no_such_statement", out.spec)
        check("…says statement numbers start again every turn", "start again at 1 every turn" in err, err)
        check("…tells the agent to run the EXPLAIN itself with its SQL tool",
              "yourself" in err and "with your SQL tool" in err, err)
        check("…and to call TDA_ExplainPlan again", "call TDA_ExplainPlan again" in err, err)
        check("…and never to hand the step to the user", "Do not ask the user" in err, err)
        out = call({})
        check("the no-EXPLAIN refusal carries the same instruction",
              out.metadata.get("error_code") == "no_explain" and "Do not ask the user" in out.spec["error"]
              and "yourself" in out.spec["error"], out.spec)
        APP_CONFIG.COMPUTE_LINEAGE = False
        check("absent when the registry is off", h.is_profile_relevant({}) is False)
    finally:
        APP_CONFIG.COMPUTE_LINEAGE = saved


def t_guard():
    print("\n[4] the Navigator's guard admits EXPLAIN only where asked")
    from trusted_data_agent.dbcatalog.sql_runner import single_select_guard
    from trusted_data_agent.dbcatalog.base import CatalogError

    def refused(sql, **k):
        try:
            single_select_guard(sql, **k)
            return False
        except CatalogError:
            return True
    check("EXPLAIN of a SELECT, when allowed", single_select_guard("EXPLAIN SELECT 1", allow_explain=True) == "EXPLAIN SELECT 1")
    check("…refused by default (every other caller keeps its rule)", refused("EXPLAIN SELECT 1"))
    check("EXPLAIN of a write is refused", refused("EXPLAIN DELETE FROM t", allow_explain=True))
    try:
        single_select_guard("EXPLAIN ANALYZE SELECT 1", allow_explain=True)
        why = ""
    except CatalogError as exc:
        why = str(exc)
    check("EXPLAIN ANALYZE executes the statement — refused, and says why", "executes the statement" in why, why)
    check("EXPLAIN of two statements is refused", refused("EXPLAIN SELECT 1; DROP TABLE t", allow_explain=True))
    src = Path("components/builtin/database_navigator/handler.py").read_text()
    check("the Navigator's query path passes allow_explain", "single_select_guard(sql, dialect, allow_explain=True)" in src)
    for f in ("src/trusted_data_agent/dbcatalog/teradata_provider.py", "src/trusted_data_agent/dbcatalog/generic_provider.py"):
        if not Path(f).exists():
            continue  # a provider this distribution does not carry  # distribution-aware (the public core has no Teradata provider)
        t = Path(f).read_text()
        body = t[t.index("async def run_select"):]
        body = body[:body.index("\n    async def ", 10)]
        check(f"{Path(f).name}: run_select allows it, nothing else does", "allow_explain=True" in body
              and t.count("allow_explain=True") == 1)


def t_lineage():
    print("\n[4b] a plan is prose: its step numbers never vouch for a figure, its estimates do")
    from trusted_data_agent.agent import compute_lineage as L
    lines = FIX["join_agg"]["lines"]
    st = [{"n": 1, "statement": "EXPLAIN SELECT x", "rows": [{"Explanation": l} for l in lines], "row_count": len(lines)}]
    e = L.assess(f"Step 4 duplicates Customers ({768:,} rows estimated), then step 5 joins; the plan has {len(lines)} lines.", [], st)
    quoted = {q["figure"] for q in e["quoted"]}
    check("a row estimate in the answer traces to the plan", "768" in quoted, e["quoted"])
    check("step numbers are not marked as quoted data", not ({"4", "5"} & quoted), e["quoted"])
    check("the number of plan lines is not a row count", str(len(lines)) not in quoted, e["quoted"])
    data = [{"n": 2, "statement": "SELECT n FROM t", "rows": [{"n": 4}], "row_count": 1}]
    check("…while a small number from a real result still traces", "4" in {q["figure"] for q in L.assess("There are 4.", [], data)["quoted"]})


def t_canvas_explain():
    print("\n[4c] the canvas's Explain: one spec for both faces, a read-only Teradata EXPLAIN")
    built = P.build_spec(plan_of("product"), statement=None, sql="SELECT 1", origin="canvas")
    check("the canvas plan is titled as the canvas's, not a statement number",
          built["title"].startswith("Plan of the canvas statement · 4 steps") and built["spec"]["origin"] == "canvas"
          and built["spec"]["statement"] is None, built["title"])
    agent = P.build_spec(plan_of("product"), statement=3, sql="SELECT 1")
    check("…the same keys as the agent's plan (one renderer draws both)", set(agent["spec"]) == set(built["spec"]))
    h = Path("components/builtin/explain_plan/handler.py").read_text()
    check("the agent's tool builds its spec through the shared builder", "P.build_spec(" in h)
    src = Path("src/trusted_data_agent/api/rest_routes.py").read_text()
    body = src[src.index("async def canvas_explain"):]
    body = body[:body.index("\n@rest_api_bp.route")]
    check("the endpoint builds the same spec", "P.build_spec(" in body and 'origin="canvas"' in body)
    check("…for Teradata connections only", '!= "teradata"' in body and '"not_teradata"' in body)
    check("…refuses anything but one SELECT before the database is asked",
          body.index("single_select_guard(") < body.index("connector.execute("))
    check("…runs the EXPLAIN on a read-only connection", 'readonly=True' in body)
    check("…and returns the database's message without the driver's stack", 'split("\\n at ")' in body)


def t_wiring():
    print("\n[5] manifest, skill, renderer registration, dashboard")
    m = json.loads(Path("components/builtin/explain_plan/manifest.json").read_text())
    check("tool TDA_ExplainPlan with one optional integer argument",
          m["tool_definition"]["name"] == "TDA_ExplainPlan"
          and m["tool_definition"]["args"] == {"statement": {"type": "integer", "description": m["tool_definition"]["args"]["statement"]["description"], "required": False}})
    check("enabled for the classes that run SQL", m["profile_defaults"]["enabled_for"] == ["tool_enabled", "llm_only"])
    check("declares how it reads on the answer", m.get("answer_surface", {}).get("word") == "Plan")
    reg = json.loads(Path("skills/skill_registry.json").read_text())
    entries = reg["skills"] if isinstance(reg, dict) else reg
    check("the companion skill is linked to the component",
          any(e.get("skill_id") == "explain-plan-expert" and e.get("component_id") == "explain_plan" for e in entries))
    check("the companion skill exists", Path("skills/builtin/explain-plan-expert/explain-plan-expert.md").exists())
    cr = Path("static/js/componentRenderers.js").read_text()
    check("the renderer is registered (the builtin table — loaded dynamically, only where installed)", "explain_plan: 'renderExplainPlan'" in cr)
    check("a plan can be pinned to a dashboard (its row carries the pin act)",
          (m.get("answer_surface") or {}).get("pin") is True)
    dr = Path("components/builtin/dashboard/renderer.js").read_text()
    check("a pinned plan re-renders through the same renderer",
          "tile.component_id === 'explain_plan'" in dr and "m.renderExplainPlan(" in dr)
    from agent_packs.common.component_config import ACTION_COMPONENTS
    check("agent packs keep it off unless they opt in", "explain_plan" in ACTION_COMPONENTS)


for fn in (t_parser, t_summary, t_handler, t_guard, t_lineage, t_canvas_explain, t_wiring):
    run(fn)

print("\n" + "=" * 68)
print(f"RESULT: {'FAILURES' if failures else 'ALL PASS'}  ({passed} passed, {len(failures)} failed)")
print("=" * 68)
sys.exit(1 if failures else 0)
