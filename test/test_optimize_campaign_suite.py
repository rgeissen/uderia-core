"""The Optimize campaign's CI gate — the registry is extracted, the suite is complete,
the invariants behave. No server, no network, no tokens.

The live campaign (`test/optimize_campaign/run_campaign.py`) proves the LANE on real
turns; this proves the CAMPAIGN: that every rewrite pass, safeguard and orchestrator in
the source is provoked by a case or honestly declared, that every case with an oracle
says what to extract, that the invariants read the trace shapes the lane produces, and
that a fixed finding names a pin and a live case.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "optimize_campaign"))

import coverage as cov  # noqa: E402
import registry as R  # noqa: E402
import suite  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


def section(t):
    print(f"\n── {t}")


# ── §1 the registry is extracted from the source ─────────────────────────────
section("§1 registry")
check("≥ 15 rewrite passes extracted from planner.py", len(R.keys("pass")) >= 15, str(R.keys("pass")))
check("the catalogue's named passes are present",
      {"plan_validation", "final_report_guarantee", "plan_hydration", "temporal_data_flow", "sql_consolidation"} <= set(R.keys("pass")))
check("FASTPATH is registered", "fastpath" in R.REGISTRY)
check("self-correction is registered", "self_correction" in R.REGISTRY)
check("the three orchestrators are registered",
      {"orchestrator:date_range", "orchestrator:column_iteration", "orchestrator:hallucinated_loop"} <= set(R.REGISTRY))
for k, e in R.REGISTRY.items():
    check(f"{k} names its source file", e.where.endswith(".py"))

# ── §2 the suite ─────────────────────────────────────────────────────────────
section("§2 suite")
ids = [c["id"] for c in suite.CASES]
check("case ids unique", len(ids) == len(set(ids)))
for c in suite.CASES:
    check(f"{c['id']} has a prompt", bool(c["prompt"]))
    check(f"{c['id']} levers ⊆ ALL_LEVERS", c["levers"] and c["levers"] <= set(suite.ALL_LEVERS))
    if c.get("oracle_sql"):
        check(f"{c['id']} extract is known", c["extract"] in ("number", "list", "set", "rows"))
        check(f"{c['id']} oracle names fitness_db", "fitness_db." in c["oracle_sql"])
        check(f"{c['id']} oracle uses TOP, never FETCH FIRST (Teradata)", "FETCH FIRST" not in c["oracle_sql"])
    if c.get("waste"):
        check(f"{c['id']} waste detector exists", c["waste"] in suite.WASTE)
    for t in c.get("turns") or []:
        check(f"{c['id']} turn has a prompt", bool(t.get("prompt")))
fams = {c["family"] for c in suite.CASES}
check("families A E R M present", {"A", "E", "R", "M"} <= fams, str(fams))
check("≥ 10 oracle-checked accuracy cases", sum(1 for c in suite.CASES if c["family"] == "A" and c.get("oracle_sql")) >= 10)
check("the champion case asks the seeded question word for word",
      any(c["prompt"] == suite.CHAMPION["user_query"] for c in suite.CASES))

# ── §3 coverage ──────────────────────────────────────────────────────────────
section("§3 coverage is complete")
for lv in suite.LEVERS:
    c = cov.coverage(lv)
    check(f"{lv}: no unreached registry entry", not [k for k, v in c.items() if v == "UNREACHED"],
          str([k for k, v in c.items() if v == "UNREACHED"]))
check("no target names an unknown entry", not cov.unknown_targets(), str(cov.unknown_targets()))
check("no stale declaration", not cov.stale_declarations(), str(cov.stale_declarations()))
for k, why in suite.UNPROVOKABLE.items():
    check(f"UNPROVOKABLE {k} explains itself", len(why) > 30, why)

# ── §4 the invariants on the lane's trace shapes ─────────────────────────────
section("§4 invariants and waste on synthetic turns")


def _log(message, details):
    return {"action": {"tool_name": "TDA_SystemLog", "arguments": {"message": message, "details": details}},
            "result": {"status": "info"}}


def _tool(name, args, ok=True):
    return {"action": {"tool_name": name, "arguments": args},
            "result": {"status": "success" if ok else "error", **({} if ok else {"error": "Not found: Unknown tool"})}}


def _turn(trace, plan=None, cost=None, tokens=None, answer="There are 100 customers.", **extra):
    calls = [(e["action"]["arguments"]["details"]) for e in trace
             if e["action"]["tool_name"] == "TDA_SystemLog" and isinstance(e["action"]["arguments"].get("details"), dict)
             and "input_tokens" in e["action"]["arguments"]["details"]]
    t = {"execution_trace": trace, "original_plan": plan or [{"relevant_tools": ["base_readQuery"],
                                                              "arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}},
                                                             {"relevant_tools": ["TDA_FinalReport"], "arguments": {}}],
         "turn_cost": cost if cost is not None else round(sum(c["cost_usd"] for c in calls), 6),
         "turn_input_tokens": tokens if tokens is not None else sum(c["input_tokens"] for c in calls),
         "final_summary": answer, "eval": {"dimensions": [{"k": "numeric_fidelity", "b": "healthy"}]}}
    t.update(extra)
    return t


clean = _turn([
    _log("Calling LLM for Planning", {"input_tokens": 6000, "output_tokens": 100, "cost_usd": 0.002}),
    _log("Plan Optimization", {"summary": "FASTPATH initiated for 'base_readQuery'."}),
    _tool("base_readQuery", {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}),
    _log("Plan Optimization", {"summary": "FASTPATH initiated for 'TDA_FinalReport'."}),
    _log("Calling LLM for TDA_FinalReport", {"input_tokens": 500, "output_tokens": 50, "cost_usd": 0.0004}),
    _tool("TDA_FinalReport", {}),
])
case = suite.CASES[0]
res = {x["id"]: x for x in suite.check_invariants(clean, case, True)}
check("a clean FASTPATH turn passes O1–O10", all(x["ok"] for x in res.values()),
      str({k: v["detail"] for k, v in res.items() if not v["ok"]}))
check("fired() reads FASTPATH", "fastpath" in R.fired(clean))
check("llm_calls() reads the priced calls", len(R.llm_calls(clean)) == 2)
check("tool_calls() skips the system entries", [n for n, *_ in R.tool_calls(clean)] == ["base_readQuery"])

wrong = dict(clean, final_summary="There are 250 customers.")
res = {x["id"]: x for x in suite.check_invariants(wrong, case, False)}
check("O10 fails on a wrong figure delivered as an answer", not res["O10"]["ok"])

priced_wrong = dict(clean, turn_cost=0.5)
res = {x["id"]: x for x in suite.check_invariants(priced_wrong, case, True)}
check("O6 fails when turn_cost disagrees with the per-call sum", not res["O6"]["ok"], res["O6"]["detail"])

dup = _turn(clean["execution_trace"] + [_tool("base_readQuery", {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"})])
res = {x["id"]: x for x in suite.check_invariants(dup, case, True)}
check("O2 fails on identical SQL executed twice", not res["O2"]["ok"])

tactical = _turn([
    _log("Calling LLM for Planning", {"input_tokens": 6000, "output_tokens": 100, "cost_usd": 0.002}),
    _log("Calling LLM for Tactical Action", {"input_tokens": 1800, "output_tokens": 40, "cost_usd": 0.0006}),
    _tool("base_readQuery", {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}),
    _log("Calling LLM for TDA_FinalReport", {"input_tokens": 500, "output_tokens": 50, "cost_usd": 0.0004}),
    _tool("TDA_FinalReport", {}),
])
check("waste_tactical_when_known fires on a tactical call for a pre-known phase",
      suite.waste_tactical_when_known(tactical) is True)
res = {x["id"]: x for x in suite.check_invariants(tactical, case, True)}
check("O3 fails on that waste", not res["O3"]["ok"])

unknown = _turn([
    _log("Calling LLM for Planning", {"input_tokens": 6000, "output_tokens": 100, "cost_usd": 0.002}),
    _tool("base_rowCount", {"database_name": "fitness_db", "table_name": "Customers"}, ok=False),
    _log("System Self-Correction", {"details": {"error": "Not found: Unknown tool: 'base_rowCount'"}}),
    _log("Calling LLM for Self-Correction", {"input_tokens": 16000, "output_tokens": 30, "cost_usd": 0.005}),
    _tool("base_readQuery", {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}),
    _tool("TDA_FinalReport", {}),
])
check("self_corrections() counts the priced call once", suite.self_corrections(unknown) == 1)
check("waste_paid_for_unknown_tool fires when an LLM paid for a tool the catalog never had",
      suite.waste_paid_for_unknown_tool(unknown) is True)
check("fired() reads the self-correction", "self_correction" in R.fired(unknown))
check("waste_champion_missed reads rag_case_count", suite.waste_champion_missed({"rag_case_count": 0}) is True
      and suite.waste_champion_missed({"rag_case_count": 2}) is False and suite.waste_champion_missed({}) is None)

# ── §5 the oracle's extractors ───────────────────────────────────────────────
section("§5 oracle extractors")
import oracle as O  # noqa: E402
check("numbers_in reads thousands and currency", O.numbers_in("Total revenue is $90,801.15 across 100 sales") == [90801.15, 100.0])
check("number check: within tolerance", O.check("The average is 908.01", 908.0115, "number", 0.01)[0] is True)
check("number check: wrong figure", O.check("There are 250 customers", 100.0, "number")[0] is False)
check("number check: no figure → None", O.check("I cannot tell", 100.0, "number")[0] is None)
check("the budget footer's figures are not the answer",
      O.check("The count could not be retrieved.\n\n_finished within budget — 1 of 12 steps used · ~71k of ~150k tokens used · 10 of 300 s used_", 0.0, "number")[0] is None)
check("the question's echoed figure is not the answer",
      O.check("Sales in the past 30 days could not be retrieved.", 0.0, "number", 0, "How many sales in the past 30 days?")[0] is None)
check("stripping the question's figure never touches a product name",
      O.check("The top 5 are A, Life Fitness T5 Treadmill and B.", ["A", "Life Fitness T5 Treadmill", "B"], "list", 0, "What are the top 5 products?")[0] is True)
check("an honest 'none' matches an expected 0", O.check("There were no sales in that window.", 0.0, "number")[0] is True)
check("a decline with names absent → None, not wrong", O.check("I could not determine the top customers.", {"Took"}, "set")[0] is None)
check("list check: order matters", O.check("1. Schwinn 2. Bowflex 3. ProForm", ["Schwinn", "Bowflex", "ProForm"], "list")[0] is True
      and O.check("ProForm, Schwinn, Bowflex", ["Schwinn", "Bowflex", "ProForm"], "list")[0] is False)
check("set check: order free", O.check("Took, Taylor and Smirnov", {"Smirnov", "Took", "Taylor"}, "set")[0] is True)
check("expected_value: number/list/set/rows", O.expected_value([{"n": "100"}], "number") == 100.0
      and O.expected_value([{"b": "x"}, {"b": "y"}], "list") == ["x", "y"]
      and O.expected_value([{"b": "x"}, {"b": "y"}], "set") == {"x", "y"}
      and O.expected_value([{"b": "x"}, {"b": "y"}], "rows") == 2)

# ── §6 findings and the ledger ───────────────────────────────────────────────
section("§6 FINDINGS.md and harvest_ledger.json")
import re as _re
fp = os.path.join(HERE, "optimize_campaign", "FINDINGS.md")
findings = open(fp).read() if os.path.exists(fp) else ""
case_ids = set(ids)
for cid, status in _re.findall(r"^### (F\d+) ·[^\n]*\n(?:\n?_Status: ([^\n]*)_)?", findings, _re.M):
    check(f"{cid} carries a Status line", bool(status))
    if status and "fixed" in status:
        pin = _re.search(r"pinned: `([\w./]+?)(?:[:§][^`]*)?`", status)
        check(f"{cid} names its CI pin", bool(pin) and os.path.exists(os.path.join(HERE, pin.group(1))), status)
        case = _re.search(r"case: `([A-Z]\w*)`", status)
        check(f"{cid} names a live case that exists", bool(case) and case.group(1) in case_ids, status)
lp = os.path.join(HERE, "optimize_campaign", "harvest_ledger.json")
led = json.load(open(lp)) if os.path.exists(lp) else {"entries": {}}
for k, e in (led.get("entries") or {}).items():
    check(f"ledger {k} status known", e.get("status") in ("new", "adopted", "declined"))
    if e.get("status") == "adopted":
        check(f"ledger {k} adopted → a case that exists", e.get("case") in case_ids)
    if e.get("status") == "declined":
        check(f"ledger {k} declined → a reason", bool((e.get("reason") or "").strip()))

print("\n── §9 the cross-lane lever (@OCREACT — the ReAct lane on the same questions)")
check("the lane lever is a case lever but NOT an Optimize lever (coverage stays the Optimize lane's)",
      "OCREACT" in suite.ALL_LEVERS and "OCREACT" not in suite.LEVERS and suite.LANES["OCREACT"] == "conversation_with_tools")
check("the champion cases (E2, E2b, E2c) never run on it",
      all("OCREACT" not in c["levers"] for c in suite.CASES if c["id"] in ("E2", "E2b", "E2c")))
REACT = {"turn_cost": 0.0021, "turn_input_tokens": 3000, "final_summary_text": "There are 100 customers.",
         "conversation_agent_events": [
             {"type": "conversation_llm_step", "payload": {"step_name": "Tool Selection", "input_tokens": 1000, "output_tokens": 20, "cost_usd": 0.0007}},
             {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}}},
             {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": True}},
             {"type": "conversation_llm_step", "payload": {"step_name": "Tool Selection #2", "input_tokens": 1000, "output_tokens": 20, "cost_usd": 0.0007}},
             {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}}},
             {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": False}},
             {"type": "conversation_llm_step", "payload": {"step_name": "Response Generation", "input_tokens": 1000, "output_tokens": 40, "cost_usd": 0.0007}}]}
check("a ReAct turn is recognised as one", suite.is_react(REACT) and not suite.is_react({"original_plan": [{}], "conversation_agent_events": [1]}))
check("its LLM calls are read from its own steps", [c[0] for c in R.llm_calls(REACT)] == ["Tool Selection", "Tool Selection #2", "Response Generation"])
check("its tool calls pair invoked with completed, in order",
      [(n, ok) for n, _, ok in R.tool_calls(REACT)] == [("base_readQuery", True), ("base_readQuery", False)])
inv = {x["id"]: x for x in suite.check_invariants(REACT, {"id": "A1", "oracle_sql": "x"}, True, {"max_input_tokens": 10})}
check("O1 on the ReAct lane is an answer with its cost sealed — no plan demanded", inv["O1"]["ok"] and "ReAct lane" in inv["O1"]["detail"])
check("O7 is n/a there (the bands are the Optimize lane's)", inv["O7"]["ok"] and "n/a" in inv["O7"]["detail"])
check("the lane-neutral invariants still hold it: duplicate SQL fails O2", not inv["O2"]["ok"])
check("…and price honesty (O6) is measured from its steps", inv["O6"]["ok"] and "Σcalls=0.0021" in inv["O6"]["detail"])
exp = suite.check_expectations({"oracle_sql": "x", "expect": {"max_llm_calls": 1, "max_self_corrections": 0}}, REACT, True, None)
check("the Optimize call ceilings are not applied to it; the oracle is", [e["id"] for e in exp] == ["oracle agrees"])
import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("oc_run", os.path.join(HERE, "optimize_campaign", "run_campaign.py"))
_rc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rc)
rows = {r[0]: r for r in _rc.lane_gap([
    {"lever": "OCFL", "id": "A1", "correct": True, "cost_usd": 0.001, "llm_calls": 2},
    {"lever": "OCREACT", "id": "A1", "correct": False, "cost_usd": 0.004, "llm_calls": 5},
    {"lever": "OCFL", "id": "A2", "correct": True, "cost_usd": 0.002, "llm_calls": 2},
    {"lever": "OCREACT", "id": "A2", "correct": True, "cost_usd": 0.002, "llm_calls": 3}])}
cold = _rc.lane_gap([{"lever": "OCFL", "id": "A1", "correct": True, "cost_usd": 0.0001, "llm_calls": 2},
                     {"lever": "OCFL-NORAG", "id": "A1", "correct": True, "cost_usd": 0.002, "llm_calls": 3},
                     {"lever": "OCREACT", "id": "A1", "correct": True, "cost_usd": 0.004, "llm_calls": 3}])
check("the ReAct lane is compared with the COLD Optimize lever when it ran (@OCFL is seeded with the answers)",
      cold and cold[0][3] == 0.002)
CHART = dict(REACT, conversation_agent_events=[
    {"type": "conversation_tool_invoked", "payload": {"tool_name": "TDA_Charting", "arguments": {"data": '[{"ProductName": "A", "Revenue": 9}, {"ProductName": "B", "Revenue": 7}]'}}},
    {"type": "conversation_tool_completed", "payload": {"tool_name": "TDA_Charting", "success": True}}])
sa = _rc.scorable_answer(CHART, "The chart above shows the leaders.")
check("a chart the turn drew is part of what the oracle reads (ReAct · A2: the answer WAS the chart)",
      "A · 9" in sa and "B · 7" in sa and sa.index("A · 9") < sa.index("B · 7"))
FAILED_CHART = dict(REACT, conversation_agent_events=[
    {"type": "conversation_tool_invoked", "payload": {"tool_name": "TDA_Charting", "arguments": {"data": '[{"ProductName": "A"}]'}}},
    {"type": "conversation_tool_completed", "payload": {"tool_name": "TDA_Charting", "success": False}}])
check("…a chart call that failed is not", _rc.scorable_answer(FAILED_CHART, "x") == "x")
check("the lane table names where only Optimize was right, and the price multiple",
      rows["A1"][-1] == "OPTIMIZE ONLY" and rows["A1"][5] == 4.0 and rows["A2"][-1] == "both right")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
