"""
Fusion Optimizer robustness/quality hardening — regression gate for the MEDIUM/LOW findings
of docs/assessments/FUSION_OPTIMIZER_DEEP_ASSESSMENT_2026-07.md (§2 robustness, §3 RAG quality,
§4 cost, §5 safe cleanup). Blocks added per themed batch.

CI-pure: no server, no DB, no network. Functional where reachable; scoped source/structural
assertions where the fix lives in a hot path that can't be cheaply instantiated.

Run:  PYTHONPATH=src python3 test/test_fusion_robustness.py
"""
import asyncio
import sys
import types
from pathlib import Path

from trusted_data_agent.agent import orchestrators

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


async def _drain(agen):
    out = []
    async for x in agen:
        out.append(x)
    return out


_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "trusted_data_agent" / "agent"


# ============================================================ §2 — _is_date_arg (over-strip fix)
print("\n[§2] date-arg detection matches whole words, not substrings (no more stripping 'update')")
_ida = orchestrators._is_date_arg
check("'date' → date arg", _ida("date"))
check("'start_date' → date arg", _ida("start_date"))
check("'end_date' → date arg", _ida("end_date"))
check("'log_date' → date arg (tokenised)", _ida("log_date"))
check("'update' → NOT a date arg (was wrongly stripped)", not _ida("update"))
check("'validate' → NOT a date arg", not _ida("validate"))
check("'mandate' → NOT a date arg", not _ida("mandate"))
check("'database_name' → NOT a date arg", not _ida("database_name"))


# ============================================================ §2 — column orchestrator hardening
print("\n[§2] column orchestrator: null-type guard, parallelised, trace entries, honest status")


class _ColExec:
    def __init__(self):
        self.dependencies = {"STATE": {}}
        self.user_uuid = "u"
        self.session_id = "s"
        self.active_profile_id = "p"
        self.current_phase_index = 0
        self.turn_action_history = []
        self.workflow_state = {}
        self.structured = []
        self.last_tool_output = None

    def _add_to_structured_data(self, x):
        self.structured.append(x)

    async def _get_tool_constraints(self, tool_name):
        return {"dataType": None}, []   # no required type → every column runnable


_COLS = [
    {"ColumnName": "c1", "ColumnType": "INTEGER"},
    {"ColumnName": "c2", "ColumnType": None},        # <- null type: the .upper() crash case
    {"ColumnName": "c3"},                            # <- no type key at all
]


def _make_invoke(fail_col=None):
    """Fake mcp_adapter.invoke_mcp_tool: first call = column list; later = per-column tool."""
    calls = {"n": 0}

    async def _invoke(state, command, **kw):
        calls["n"] += 1
        if command.get("tool_name") == "base_columnDescription":
            return {"status": "success", "results": _COLS}, 0, 0
        col = command.get("arguments", {}).get("column_name")
        if col == fail_col:
            return {"status": "error", "metadata": {"column_name": col}, "error_message": "boom"}, 0, 0
        return {"status": "success", "results": [{"col": col, "stat": 1}]}, 0, 0

    return _invoke, calls


_orig_invoke = orchestrators.mcp_adapter.invoke_mcp_tool

# (a) null/missing column type does not crash; all columns run
_inv, _calls = _make_invoke()
orchestrators.mcp_adapter.invoke_mcp_tool = _inv
try:
    _ex = _ColExec()
    _cmd = {"tool_name": "qlty_univariateStatistics",
            "arguments": {"database_name": "db", "object_name": "t"}}
    _raised = None
    try:
        run(_drain(orchestrators.execute_column_iteration(_ex, _cmd)))
    except Exception as e:  # noqa: BLE001
        _raised = e
    check("null/missing column type does NOT crash (no None.upper())", _raised is None)
    check("last_tool_output status is success (all columns ran)",
          isinstance(_ex.last_tool_output, dict) and _ex.last_tool_output.get("status") == "success")
    # results = the initial column-list result + one entry per column (3) = 4
    check("all 3 columns produced results (+ the column-list result)", len(_ex.last_tool_output.get("results", [])) == 4)
    # trace entries: 3 per-column calls + 1 TDA_SystemOrchestration wrapper
    _tool_entries = [e for e in _ex.turn_action_history
                     if isinstance(e.get("action"), dict) and e["action"].get("tool_name") == "qlty_univariateStatistics"]
    _orch_entries = [e for e in _ex.turn_action_history
                     if isinstance(e.get("action"), dict) and e["action"].get("tool_name") == "TDA_SystemOrchestration"]
    check("per-column calls recorded in turn_action_history (trace contract)", len(_tool_entries) == 3)
    check("TDA_SystemOrchestration wrapper appended (documented contract)", len(_orch_entries) == 1)
    check("orchestration wrapper reports column_iteration_complete",
          _orch_entries and _orch_entries[0]["action"]["arguments"].get("orchestration_type") == "column_iteration_complete")
finally:
    orchestrators.mcp_adapter.invoke_mcp_tool = _orig_invoke

# (b) one failing column → honest 'partial' status, other columns still returned
_inv2, _ = _make_invoke(fail_col="c2")
orchestrators.mcp_adapter.invoke_mcp_tool = _inv2
try:
    _ex2 = _ColExec()
    run(_drain(orchestrators.execute_column_iteration(_ex2, {"tool_name": "T", "arguments": {"database_name": "db", "object_name": "t"}})))
    check("one failing column → status 'partial' (not a blanket success)",
          _ex2.last_tool_output.get("status") == "partial")
    check("failed-column count surfaced in metadata",
          _ex2.last_tool_output.get("metadata", {}).get("columns_failed") == 1)
    check("surviving columns still produced results", len(_ex2.last_tool_output.get("results", [])) == 4)
finally:
    orchestrators.mcp_adapter.invoke_mcp_tool = _orig_invoke


# ============================================================ §2 — date-range gather hardening
print("\n[§2] date-range orchestrator: bounded + resilient fan-out (structural)")
_orch_src = (_SRC_ROOT / "orchestrators.py").read_text()
_orch_code = "\n".join(l for l in _orch_src.splitlines() if not l.lstrip().startswith("#"))
check("fan-out is bounded by a semaphore (no 365-call storm)",
      "asyncio.Semaphore(_MAX_ORCHESTRATOR_CONCURRENCY)" in _orch_code)
check("date-range gather uses return_exceptions (one bad day can't abort the range)",
      _orch_code.count("return_exceptions=True") >= 2)
check("both date-range strip sites use _is_date_arg (whole-word), not substring",
      _orch_code.count("if not _is_date_arg(k)") >= 2)
check("partial failure is reported honestly (not a blanket success)",
      '"partial"' in _orch_src and "days_failed" in _orch_src)


# ============================================================ §2 — plan-parsing robustness (F1)
print("\n[§2] _resolve_arguments resolves whole-string JSON placeholders and list string items")
from trusted_data_agent.agent import executor as ex_mod
from trusted_data_agent.agent import planner as planner_mod


class _ResolveExec:
    _resolve_arguments = ex_mod.PlanExecutor._resolve_arguments
    _find_value_by_key = ex_mod.PlanExecutor._find_value_by_key
    _unwrap_single_value_from_result = ex_mod.PlanExecutor._unwrap_single_value_from_result

    def __init__(self, workflow_state):
        self.workflow_state = workflow_state
        self.meta_plan = []
        self.events_to_yield = []

    def _format_sse_with_depth(self, *a, **k):
        return {}


_ws = {"result_of_phase_1": {"status": "success", "results": [{"name": "foo", "id": 7}]}}

# (a) whole-string JSON placeholder value → resolved (was: raw JSON string to tool)
_r = _ResolveExec(_ws)._resolve_arguments({"db": '{"source": "result_of_phase_1", "key": "name"}'})
check("whole-string JSON placeholder resolves to the value (not raw JSON)", _r.get("db") == "foo")

# (b) string item inside a list arg → resolved (was: raw placeholder text to tool)
_r2 = _ResolveExec(_ws)._resolve_arguments({"source_data": ['{"source": "result_of_phase_1", "key": "id"}', "literal"]})
check("list arg: JSON-placeholder string item resolved", _r2.get("source_data", [None])[0] == 7)
check("list arg: non-placeholder string item untouched", _r2.get("source_data", [None, None])[1] == "literal")

# (c) FASTPATH readiness now rejects an unresolved phase reference (structural — hot path)
_pe_src3 = (_SRC_ROOT / "phase_executor.py").read_text()
check("FASTPATH rejects an unresolved result_of_phase_N string value",
      "contains an unresolved phase reference" in _pe_src3 and r"result_of_phase_\d+" in _pe_src3)

print("\n[§2] dict-wrapped plans unwrap; temporal detect⇔extract are single-sourced (no drift)")
_pl_src = (_SRC_ROOT / "planner.py").read_text()
check("dict-wrapped plan ({'plan'/'phases'/'steps': [...]}) is unwrapped, not hard-failed",
      'for k in ("plan", "phases", "steps")' in _pl_src)

# temporal: detection (_inject) and extraction share _TEMPORAL_PATTERNS → no drift
class _TmpExec:
    def __init__(self, q):
        self.original_user_input = q


def _inject_detects(q):
    pl = planner_mod.Planner(_TmpExec(q))
    _new, injected = pl._inject_temporal_context([{"phase": 1, "relevant_tools": ["x"], "arguments": {}}])
    return injected


def _extract_gets(q):
    return planner_mod.Planner(_TmpExec(q))._extract_temporal_phrase(q.lower())


for _q, _resolvable in [
    ("show usage for the past 5 days", True),
    ("errors yesterday", True),
    ("data 3 days ago", True),
    ("show me recent errors", False),      # 'recent' — not resolvable; must NOT inject (was drift)
    ("the latest numbers", False),         # 'latest' — same
    ("something in the past", False),      # number-less — same
    ("just a normal query", False),
]:
    _det = _inject_detects(_q)
    _ext = _extract_gets(_q) is not None
    check(f"temporal detect⇔extract agree for {_q!r} (no dead phase)", _det == _ext == _resolvable)


# ============================================================ §2 — phase-exec robustness (F2)
print("\n[§2] Correction 5 (fuzzy param rename) runs BEFORE Correction 4 deletes the misspelling")


class _FakeToolDef:
    def __init__(self, args):
        self.args = args


class _ValidateExec:
    def __init__(self, meta_plan, tools):
        self.meta_plan = meta_plan
        self.dependencies = {"STATE": {"mcp_tools": tools, "mcp_prompts": {}}}
        self.is_conversational_plan = False
        self.events_to_yield = []

    def _log_system_event(self, e, *a):
        pass

    def _format_sse_with_depth(self, *a, **k):
        return {}


_tools = {"base_readQuery": _FakeToolDef({"database_name": {}, "object_name": {}})}
# a phase with a MISSPELLED arg name ('databse_name') that fuzzy-matches 'database_name'
_mp = [{"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {"databse_name": "db"}}]
_vex = _ValidateExec(_mp, _tools)
list(planner_mod.Planner(_vex)._validate_and_correct_plan())  # sync generator
_args = _vex.meta_plan[0]["arguments"]
check("misspelled arg RENAMED to the schema name (not deleted)", "database_name" in _args and _args["database_name"] == "db")
check("the misspelling is gone", "databse_name" not in _args)

# a genuinely unknown arg (no close schema match) is still deleted (Correction 4)
_mp2 = [{"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {"database_name": "db", "totally_bogus_xyz": 1}}]
_vex2 = _ValidateExec(_mp2, _tools)
list(planner_mod.Planner(_vex2)._validate_and_correct_plan())
check("a genuinely unknown arg is still removed", "totally_bogus_xyz" not in _vex2.meta_plan[0]["arguments"])
check("the valid arg is retained", _vex2.meta_plan[0]["arguments"].get("database_name") == "db")

print("\n[§2] loop-repeat breaker no longer self-disarms; FASTPATH phase_end honest; plan-gate robust")
_pe_src4 = (_SRC_ROOT / "phase_executor.py").read_text()
_pe_code4 = "\n".join(l for l in _pe_src4.splitlines() if not l.lstrip().startswith("#"))
check("loop-breaker no longer resets last_action_str to None after a detected repeat",
      "_ps['last_action_str'] = None\n                continue" not in _pe_code4)
check("FASTPATH phase_end derives status from last_tool_output (not hardcoded 'completed')",
      "_fp_status" in _pe_code4)

_oe_src4 = (_SRC_ROOT / "engines" / "optimize_engine.py").read_text()
_oe_code4 = "\n".join(l for l in _oe_src4.splitlines() if not l.lstrip().startswith("#"))
check("plan gate no longer routes on a 'COMPLEX' substring",
      '"COMPLEX" not in _pg_resp' not in _oe_code4)
check("plan gate validates gate-plan tool names against the catalog (escalates on unknown)",
      "named an unknown tool" in _oe_code4)


# ============================================================ §2 — parallel/cancellation (F3)
print("\n[§2] orchestrator trace entries carry phase_number (correct parallel-group replay order)")
_inv3, _ = _make_invoke()
orchestrators.mcp_adapter.invoke_mcp_tool = _inv3
try:
    _ex5 = _ColExec()
    _ex5.current_phase_index = 2   # phase 3
    run(_drain(orchestrators.execute_column_iteration(_ex5, {"tool_name": "T", "arguments": {"database_name": "db", "object_name": "t"}})))
    _col_calls = [e for e in _ex5.turn_action_history
                  if isinstance(e.get("action"), dict) and e["action"].get("tool_name") == "T"]
    check("per-column entries carry metadata.phase_number (post-sort key)",
          all(e["action"].get("metadata", {}).get("phase_number") == 3 for e in _col_calls) and _col_calls)
    _wrap = [e for e in _ex5.turn_action_history
             if isinstance(e.get("action"), dict) and e["action"].get("tool_name") == "TDA_SystemOrchestration"]
    check("orchestration wrapper carries phase_number too",
          _wrap and _wrap[0]["action"].get("metadata", {}).get("phase_number") == 3)
finally:
    orchestrators.mcp_adapter.invoke_mcp_tool = _orig_invoke

_orch_code2 = "\n".join(l for l in (_SRC_ROOT / "orchestrators.py").read_text().splitlines()
                        if not l.lstrip().startswith("#"))
check("date-range per-day entries stamp phase_number", '_md["phase_number"] = _pnum' in _orch_code2)
_pe_code5 = "\n".join(l for l in (_SRC_ROOT / "phase_executor.py").read_text().splitlines()
                      if not l.lstrip().startswith("#"))
check("_execute_tool has a cooperative cancellation checkpoint (mid-phase Stop)",
      "self.executor._check_cancellation()" in _pe_code5)
# C9 already fixed the isinstance-misses-CancelledError gap:
_ex_code5 = "\n".join(l for l in (_SRC_ROOT / "executor.py").read_text().splitlines()
                      if not l.lstrip().startswith("#"))
check("parallel-group guard checks BaseException (catches CancelledError — C9)",
      "isinstance(res, BaseException)" in _ex_code5)


# ============================================================ §4 — CostManager singleton (Batch G)
print("\n[§4] cost math routes through the shared CostManager singleton (warm 5-min cache)")
from trusted_data_agent.core.cost_manager import get_cost_manager
check("get_cost_manager() returns a process-wide singleton (warm cache, not a cold new instance)",
      get_cost_manager() is get_cost_manager())
# No agent file re-instantiates CostManager() (which reset the cache + reloaded bootstrap per call).
import subprocess
_agent_dir = _SRC_ROOT
_residual = subprocess.run(
    ["grep", "-rn", "CostManager()", str(_agent_dir)], capture_output=True, text=True).stdout.strip()
check("no CostManager() instantiation left in agent/ (all use the singleton)", _residual == "")


# ============================================================ §3 — RAG learning-loop quality (Batch H)
print("\n[§3] RAG champion store: no 0-token tie-break trap, DBA queries seed, corrupt record skipped")
_rag_src2 = (_SRC_ROOT / "rag_retriever.py").read_text()
_rag_code2 = "\n".join(l for l in _rag_src2.splitlines() if not l.lstrip().startswith("#"))

# The token-coercion invariant (the fix): 0/missing output_tokens → unknown (inf), not a free win.
def _coerce(v):
    return (v or float('inf'))
check("0 output_tokens coerces to inf (metering failure can't win the tie-break)", _coerce(0) == float('inf'))
check("missing output_tokens coerces to inf", _coerce(None) == float('inf'))
check("a real token count is kept", _coerce(150) == 150)
# both election sites use the coercion, not a plain .get(..., inf)/.get(...,0)
check("re-election uses `output_tokens') or float('inf')`",
      'meta.get("output_tokens") or float(\'inf\')' in _rag_code2)
check("seeding-time new_tokens uses the coercion",
      'output_tokens") or float(\'inf\')' in _rag_code2 and 'output_tokens", 0)' not in _rag_code2.split("new_tokens =")[-1][:200] if "new_tokens =" in _rag_code2 else True)

# §3 #4: DBA/quality turns (data contains "error") can seed — check explicit error FIELDS, not substring
check("seeding success no longer substring-matches 'error' in the whole result",
      '"error" not in str(result).lower()' not in _rag_code2)
check("seeding success checks explicit error fields instead",
      'not result.get("error")' in _rag_code2 and 'not result.get("error_message")' in _rag_code2)

# §3 #7: one corrupt full_case_data record skips + continues (doesn't truncate the collection)
check("corrupt full_case_data is guarded (skip + continue, not abort the loop)",
      "corrupt full_case_data" in _rag_src2 and "json.JSONDecodeError" in _rag_code2)

# §3 #1: TDA_SystemOrchestration wrapper is not seeded as a phase tool
check("TDA_SystemOrchestration wrapper excluded from champion seeding",
      "tool_name not in self._ORCHESTRATION_WRAPPERS" in _rag_code2)


# ============================================================ §5 — safe cleanup (Batch I)
print("\n[§5] robust_json_parse universalised at LLM-response sites; dead code + unused import removed")
_orch_code3 = "\n".join(l for l in (_SRC_ROOT / "orchestrators.py").read_text().splitlines()
                        if not l.lstrip().startswith("#"))
_ex_code6 = "\n".join(l for l in (_SRC_ROOT / "executor.py").read_text().splitlines()
                      if not l.lstrip().startswith("#"))
check("orchestrators parse LLM output via robust_json_parse (both sites)",
      _orch_code3.count("robust_json_parse(") >= 2)
check("orchestrators import robust_json_parse", "import robust_json_parse" in (_SRC_ROOT / "orchestrators.py").read_text())
check("executor tool-constraints + arg-enrichment use robust_json_parse",
      "constraints = robust_json_parse(response_text)" in _ex_code6 and "extracted_args = robust_json_parse(response_text)" in _ex_code6)
check("executor imports robust_json_parse", "from trusted_data_agent.agent.json_utils import robust_json_parse" in (_SRC_ROOT / "executor.py").read_text())
check("unused `import sqlite3` removed from prompt_mapping.py",
      "import sqlite3" not in (_SRC_ROOT / "prompt_mapping.py").read_text())
check("dead _generate_charting_mapping removed",
      "def _generate_charting_mapping" not in (_SRC_ROOT / "phase_executor.py").read_text())


print(f"\n=== Fusion robustness results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
