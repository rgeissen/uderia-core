"""Two context-window defects found while building the findings module (Nov 2026).

  [1] the trace shape — ONE reader (core/trace_shape): a trace entry is {action, result}; the
      phantom "tool_output_summary" key four readers used is gone, with the legacy fallback kept
  [2] the workflow_history module reads real statuses and row counts (not "unknown (0 rows)")
  [3] the plan_hydration module finds the previous turn's successful results (hydration fires)
  [4] the session store's turn ingestion reads the same shape
  [5] Agent Memory is DELIVERED: the executor caches the user_memory contribution and every
      engine appends it (memory first, then findings); the coordinator gets it on its prompt

CI-pure. Run: python test/test_cw_delivery_defects.py
"""
import asyncio
import logging
import os
import sys
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
logging.disable(logging.CRITICAL)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {str(detail)[:300]}")


def _src(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


def entry(tool, args, result):
    return {"action": {"tool_name": tool, "arguments": args}, "result": result}


TRACE = [
    entry("TDA_SystemLog", {"message": "Calling LLM"}, {"status": "info"}),
    entry("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM Sales.Customers"},
          {"status": "success", "results": [{"n": 4812}], "metadata": {"row_count": 1}}),
    entry("base_tableList", {"database_name": "Sales"},
          {"status": "success", "results": [{"TableName": "A"}, {"TableName": "B"}, {"TableName": "C"}]}),
    entry("base_readQuery", {"sql": "SELECT 1 FROM nowhere"}, {"status": "error", "error": "3807"}),
]

print("\n[1] the trace shape — one reader")
from trusted_data_agent.core.trace_shape import phase_action, phase_output, phase_rows   # noqa: E402

check("the real shape: output is the result", phase_output(TRACE[1])["status"] == "success" and phase_rows(TRACE[1]) == [{"n": 4812}])
check("the action", phase_action(TRACE[1])["tool_name"] == "base_readQuery")
legacy = {"action": {"tool_name": "x"}, "result": {"tool_output_summary": {"status": "success", "results": [{"a": 1}]}}}
check("a legacy nested tool_output_summary still reads", phase_output(legacy)["status"] == "success" and phase_rows(legacy) == [{"a": 1}])
legacy2 = {"action": {"tool_name": "x"}, "tool_output_summary": {"status": "success", "rows": [{"a": 1}]}}
check("a legacy top-level tool_output_summary still reads", phase_output(legacy2)["status"] == "success" and phase_rows(legacy2) == [{"a": 1}])
check("garbage reads empty, never raises", phase_output(None) == {} and phase_output({"result": "text"}) == {} and phase_rows(3) is None and phase_action("x") == {})
for rel in ("src/trusted_data_agent/agent/planner.py",
            "components/builtin/context_window/modules/workflow_history/handler.py",
            "components/builtin/context_window/modules/plan_hydration/handler.py",
            "src/trusted_data_agent/core/session_manager.py"):
    s = _src(rel)
    check(f"{os.path.basename(rel)} reads through trace_shape and never the phantom key directly",
          "trace_shape import" in s and 'phase.get("tool_output_summary"' not in s and 'entry.get("tool_output_summary"' not in s)

print("\n[2] the workflow_history module")
from components.builtin.context_window.module_registry import get_module_registry   # noqa: E402
from components.builtin.context_window.base import AssemblyContext                   # noqa: E402

reg = get_module_registry(); reg.discover_modules()
wf_mod = reg.get_handler("workflow_history")
ph_mod = reg.get_handler("plan_hydration")
sd = {"last_turn_data": {"workflow_history": [{"turn": 1, "turn_number": 1, "isValid": True, "execution_trace": TRACE}]}}


def ctx(turn=2, data=sd):
    return AssemblyContext(profile_type="tool_enabled", profile_id="p", session_id="s", user_uuid="u",
                           session_data=data, turn_number=turn, is_first_turn=False)


c = asyncio.run(wf_mod.contribute(4000, ctx()))
check("a successful query reads success with its row count", "- base_readQuery: success (1 rows)" in c.content, c.content)
check("a three-row listing reads 3 rows", "- base_tableList: success (3 rows)" in c.content, c.content)
check("a failed query reads error", "- base_readQuery: error" in c.content, c.content)
check("nothing reads unknown (0 rows) any more", "unknown" not in c.content and "(0 rows)" not in c.content, c.content)

print("\n[3] the plan_hydration module")
prev = ph_mod._extract_previous_results(sd["last_turn_data"]["workflow_history"])
check("hydration finds the previous turn's successful results", [r["tool_name"] for r in prev] == ["base_readQuery", "base_tableList"], prev)
check("with their rows and arguments", prev[0]["results"] == [{"n": 4812}] and prev[0]["arguments"]["sql"].startswith("SELECT COUNT"), prev[0])
check("a failed phase and the agent's own log carry nothing", all(r["tool_name"] not in ("TDA_SystemLog",) for r in prev) and len(prev) == 2)
cp = asyncio.run(ph_mod.contribute(4000, ctx()))
check("the module contributes (hydration fires)", cp.content and "PREVIOUS TURN RESULTS" in cp.content and "base_readQuery" in cp.content, (cp.content[:200], cp.metadata))

print("\n[4] the session store's turn ingestion")
_sm = _src("src/trusted_data_agent/core/session_manager.py")
_a = _sm.index("# ── workflow_history ─")
check("the ingestion reads the real shape for the workflow chunk", "phase_rows(phase)" in _sm[_a:_a + 900] and "phase_output(phase)" in _sm[_a:_a + 900])
_b = _sm.index("# ── plan_hydration ─")
check("… and for the hydration chunks, skipping the agent's own log", "phase_output(phase)" in _sm[_b:_b + 700] and 'startswith("TDA_")' in _sm[_b:_b + 700])

print("\n[5] Agent Memory is delivered")
from trusted_data_agent.agent.executor import PlanExecutor   # noqa: E402

def fake_exec(mem, fnd):
    """The executor's three readers over its two caches, unbound — no executor construction."""
    o = SimpleNamespace(_cw_memory_text=mem, _cw_findings_text=fnd)
    o._get_memory_context = lambda: PlanExecutor._get_memory_context(o)
    o._get_findings_context = lambda: PlanExecutor._get_findings_context(o)
    return o


blocks = PlanExecutor._session_context_blocks(fake_exec("[Remembered about this user]\n- prefers metric tons",
                                                        "[Verified results from earlier turns in this session]\n…"))
check("memory first, then findings, one order", blocks.startswith("[Remembered about this user]") and blocks.index("[Verified") > blocks.index("metric tons"), blocks)
check("each block alone when the other is absent",
      PlanExecutor._session_context_blocks(fake_exec(None, "F")) == "F"
      and PlanExecutor._session_context_blocks(fake_exec("M", None)) == "M"
      and PlanExecutor._session_context_blocks(fake_exec(None, None)) == "")
_ex = _src("src/trusted_data_agent/agent/executor.py")
check("the executor caches the user_memory contribution", 'assembled_context.contributions.get("user_memory")' in _ex and "self._cw_memory_text = cw_memory.content" in _ex)
for rel in ("src/trusted_data_agent/agent/engines/ideate_engine.py",
            "src/trusted_data_agent/agent/engines/focus_engine.py",
            "src/trusted_data_agent/agent/engines/conversation_engine.py",
            "src/trusted_data_agent/agent/planner.py",
            "src/trusted_data_agent/agent/engines/optimize_engine.py"):
    s = _src(rel)
    check(f"{os.path.basename(rel)} appends the session blocks after the KG enrichment",
          "_session_context_blocks()" in s and s.index("_get_kg_enrichment()") < s.index("_session_context_blocks()"))
_ce = _src("src/trusted_data_agent/agent/engines/coordinate_engine.py")
_gc = _src("src/trusted_data_agent/agent/genie_coordinator.py")
check("the coordinator engine hands the block to the coordinator after its assembly",
      'assembled.contributions.get("user_memory")' in _ce and "coordinator.memory_context = _cw_mem.content" in _ce
      and _ce.index("coordinator.memory_context") < _ce.index("await coordinator.execute("))
check("the coordinator puts it on its prompt once, before the KG enrichment",
      'getattr(self, "memory_context", None)' in _gc and "_mem not in self.system_prompt" in _gc
      and _gc.index("_mem not in self.system_prompt") < _gc.index("kg_text, kg_details = await get_component_context_enrichment"))
_um = reg.get_handler("user_memory")
check("the user_memory module applies to every class incl. the coordinator", _um is not None and all(_um.applies_to(t) for t in ("tool_enabled", "llm_only", "rag_focused", "genie")))

print(f"\n{'=' * 52}\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
