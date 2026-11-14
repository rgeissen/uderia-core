"""S3 baseline freeze — committed GREEN before Compute with Lineage landed.

Pins that with the compute-lineage switch OFF (the default) nothing about a turn changes:
no `compute` component is served, the numeric-fidelity scorer grades the delivered figures
against the result rows exactly as today, the honesty gate raises no verdict on a clean
turn, the planner's rewrite passes keep their order, a provenance chain without compute
steps verifies identically, and the grounding/honesty HTML adapters leave an answer's
figures untouched. Every assertion here must stay green through S3 and after it."""
import asyncio, logging, os, pathlib, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()

print("[1] the switch is absent or off")
check("COMPUTE_LINEAGE absent or False by default", not getattr(APP_CONFIG, "COMPUTE_LINEAGE", False))

print("[2] no compute component is served with the switch off")
from trusted_data_agent.components.manager import get_component_manager
_cm = get_component_manager()
_ids = {c.component_id for c in _cm.get_all_components()}
check("the builtin catalog carries the known action components", {"chart", "dashboard", "flow", "canvas", "knowledge_graph", "scheduler", "memory"} <= _ids, _ids)
_comp = _cm.get_component("compute")
check("with the switch off, `compute` is not a servable tool (absent, or not profile-relevant)",
      _comp is None or not getattr(_cm.get_handler("compute") if hasattr(_cm, "get_handler") else None, "is_profile_relevant", lambda p: False)({}))

print("[3] numeric fidelity grades the delivered figures against the result rows, as today")
from trusted_data_agent.eval.scorers.reliability import NumericFidelityScorer
from trusted_data_agent.eval.types import RunRecord, EvalCase
TURN = {
    "user_query": "total sales?", "final_summary_text": "Sales were 100 and 200; the total is 300 across 2 rows.",
    "status": "success", "profile_type": "llm_only",
    "conversation_agent_events": [
        {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT amount FROM s"}}},
        {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery", "success": True,
         "result_preview": "[{'amount': 100}, {'amount': 200}]", "result_rows": 2}},
    ],
}
_case = EvalCase(id="c", query="total sales?", profile_class="conversation_with_tools")
_v = NumericFidelityScorer().score(_case, RunRecord(TURN))
check("the sum the model computed (300) is NOT grounded today — the figure is not in any row (2 of 4 figures grounded)",
      _v.score is not None and abs(_v.score - 0.5) < 1e-6, (_v.score, _v.explanation))

print("[4] the honesty gate raises nothing on a clean data turn")
from trusted_data_agent.agent.honesty import gate as _hg
_calls = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT amount FROM s"}, "status": "success",
           "result": {"status": "success", "results": [{"amount": 100}, {"amount": 200}]}}]
check("assess → None on a clean turn", _hg.assess("Sales were 100 and 200; the total is 300.", _calls) is None)

print("[5] the planner's rewrite passes keep their order")
_pl = src("src/trusted_data_agent/agent/planner.py")
_passes = re.findall(r'_pass\("([a-z_]+)"', _pl)
_expected = ["temporal_data_flow", "governed_execution", "sql_consolidation", "llm_filter_deloop", "multi_loop_synthesis",
             "llm_task_optimization", "date_range_wiring", "text_date_cast", "plan_validation", "chart_data_reuse",
             "charting_cleanup", "plan_hydration", "context_report", "final_report_guarantee"]
_it = iter(_passes)
check("the 14 known passes appear in this order (an added pass may sit between them, never reorder them)",
      all(any(p == x for x in _it) for p in _expected), _passes)

print("[6] a provenance chain without compute steps verifies identically")
from trusted_data_agent.core import provenance as _prov
_pc = _prov.ProvenanceChain(session_id="s", turn_number=1, user_uuid="u", profile_type="llm_only")
if _pc is not None:
    _pc.add_step("query_intake", "total sales?", "q")
    _pc.add_step("turn_complete", "Sales were 100 and 200; the total is 300.", "a")
    _data = {"provenance_chain": _pc.steps}
    _r = _prov.verify_content(_data, {"user_query": "total sales?", "final_summary": "Sales were 100 and 200; the total is 300."})
    check("query_intake + turn_complete verify; nothing else is counted", _r.get("verified") == 2 and not _r.get("mismatches"), _r)
else:
    check("ProvenanceChain constructible", False, "no ProvenanceChain in core.provenance")

print("[7] the HTML adapters leave an answer's figures untouched on a passing turn")
from trusted_data_agent.agent.grounding import adapter as _ga
from trusted_data_agent.agent.honesty import adapter as _ha
_html = "<p>Sales were 100 and 200; the total is <strong>300</strong> across 2 rows.</p>"
check("grounding pass → identical HTML", _ga.apply_to_html(_html, {"decision": "pass"}) == _html)
check("no honesty event → identical HTML", _ha.apply_to_html(_html, None, "") == _html)
check("no figure carries a lineage mark today", "figure-" not in _html and "data-receipt" not in _html)

print(f"\n=== S3 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
