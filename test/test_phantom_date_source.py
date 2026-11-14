"""A date range read from a table that does not exist becomes the turn's dates.

Fusion bench, 2026-09-24: after a TDA_DateRange phase the planner wrote SQL that read the
range FROM the phase's output as if it were a table (`FROM TDA_DateRange_result`, then on a
re-run `TABLE(CAST(? AS JSON))`), both runs ending "I cannot provide the total". The rewrite
(agent/phantom_date_source.py) is wired where raw SQL's date binds are filled
(PlanExecutor._resolve_arguments), which both the FASTPATH and the tactical path reach.

No server, no network, no API spend.
"""
import ast
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.agent import phantom_date_source as P  # noqa: E402
from trusted_data_agent.agent import executor as ex             # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f": {detail}" if detail is not None else ""))


S, E = "2026-08-25", "2026-09-23"
RUN_A = ("SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate BETWEEN (SELECT MIN(date) FROM (SELECT CAST(date AS DATE) "
         "as date FROM TDA_DateRange_result) as dates) AND (SELECT MAX(date) FROM (SELECT CAST(date AS DATE) as date "
         "FROM TDA_DateRange_result) as dates)")
RUN_B = ("SELECT COUNT(*) as SalesCount FROM fitness_db.Sales WHERE SaleDate >= (SELECT MIN(date_val) FROM (SELECT "
         "CAST(value AS DATE) as date_val FROM TABLE(CAST(? AS JSON))) AS d) AND SaleDate <= (SELECT MAX(date_val) FROM "
         "(SELECT CAST(value AS DATE) as date_val FROM TABLE(CAST(? AS JSON))) AS d)")

print("[1] the two shapes the bench produced")
out, ch = P.rewrite(RUN_A, S, E)
check("run A: the phantom-table MIN/MAX bounds become the range's dates",
      out == f"SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate BETWEEN DATE '{S}' AND DATE '{E}'", out)
check("…both bounds are named in the changes", len(ch) == 2, ch)
out, ch = P.rewrite(RUN_B, S, E)
check("run B: the `?`-bound JSON table bounds become the range's dates",
      out == f"SELECT COUNT(*) as SalesCount FROM fitness_db.Sales WHERE SaleDate >= DATE '{S}' AND SaleDate <= DATE '{E}'", out)

print("[2] membership in the range is the range")
out, _ = P.rewrite("SELECT COUNT(*) FROM s WHERE d IN (SELECT date FROM result_of_phase_2) AND k = 1", S, E)
check("`col IN (SELECT … FROM result_of_phase_N)` → BETWEEN",
      out == f"SELECT COUNT(*) FROM s WHERE d BETWEEN DATE '{S}' AND DATE '{E}' AND k = 1", out)
out, _ = P.rewrite("SELECT 1 FROM s WHERE s.d IN (SELECT r.date FROM TDA_DateRange r)", S, E)
check("…a qualified column and an aliased framework source too", out == f"SELECT 1 FROM s WHERE s.d BETWEEN DATE '{S}' AND DATE '{E}'", out)

print("[3] nothing real is touched")
for sql in ["SELECT * FROM fitness_db.Sales WHERE SaleDate = (SELECT MAX(SaleDate) FROM fitness_db.Sales)",
            "SELECT * FROM t WHERE d IN (SELECT d FROM holidays)",
            "SELECT MIN(d) FROM t",
            "SELECT * FROM t WHERE note = 'TDA_DateRange_result is not a table'",
            "SELECT * FROM t WHERE x = 'what? (SELECT MIN(a) FROM TDA_x)'"]:
    out, ch = P.rewrite(sql, S, E)
    check(f"untouched: {sql[:60]}", out == sql and ch == [], out)
check("no dates → untouched", P.rewrite(RUN_A, "", E) == (RUN_A, []))
_lit = "SELECT 1 FROM t WHERE note = '(SELECT MIN(x) FROM TDA_y)' AND d <= (SELECT MAX(date) FROM TDA_DateRange_result)"
check("a phantom-looking subquery inside a string literal is text, even beside a real phantom bound",
      P.rewrite(_lit, S, E)[0] == f"SELECT 1 FROM t WHERE note = '(SELECT MIN(x) FROM TDA_y)' AND d <= DATE '{E}'",
      P.rewrite(_lit, S, E)[0])
_lit_in = "SELECT 1 FROM t WHERE c = 'd IN (SELECT date FROM result_of_phase_2)' AND d IN (SELECT date FROM result_of_phase_2)"
check("…and the same for the IN form",
      P.rewrite(_lit_in, S, E)[0] == f"SELECT 1 FROM t WHERE c = 'd IN (SELECT date FROM result_of_phase_2)' AND d BETWEEN DATE '{S}' AND DATE '{E}'",
      P.rewrite(_lit_in, S, E)[0])
check("a MIN over a real table beside a phantom MAX: only the phantom one changes",
      P.rewrite("SELECT 1 FROM t WHERE d >= (SELECT MIN(x) FROM real_t) AND d <= (SELECT MAX(date) FROM TDA_DateRange_result)", S, E)[0]
      == f"SELECT 1 FROM t WHERE d >= (SELECT MIN(x) FROM real_t) AND d <= DATE '{E}'")
check("still_phantom names a shape the rewrite does not recognise",
      P.still_phantom("SELECT * FROM t JOIN TDA_DateRange_result r ON t.d = r.date"))

print("[4] wired where raw SQL's date binds are filled")


class _Ex:
    _resolve_arguments = ex.PlanExecutor._resolve_arguments
    _unwrap_single_value_from_result = ex.PlanExecutor._unwrap_single_value_from_result
    _find_value_by_key = ex.PlanExecutor._find_value_by_key
    _substitute_sql_date_binds = ex.PlanExecutor._substitute_sql_date_binds
    _turn_date_context = ex.PlanExecutor._turn_date_context
    _rewrite_phantom_date_source = ex.PlanExecutor._rewrite_phantom_date_source
    _DATE_BIND_START = ex.PlanExecutor._DATE_BIND_START
    _DATE_BIND_END = ex.PlanExecutor._DATE_BIND_END
    _DATE_BIND_NOW = ex.PlanExecutor._DATE_BIND_NOW

    def __init__(self, ws):
        self.workflow_state = ws; self.events_to_yield = []; self.meta_plan = []

    def _format_sse_with_depth(self, *a, **k):
        return a


RANGE_STATE = {"result_of_phase_1": [{"status": "success", "metadata": {"tool_name": "TDA_CurrentDate"}, "results": [{"current_date": "2026-09-24"}]}],
               "result_of_phase_2": [{"status": "success", "metadata": {"tool_name": "TDA_DateRange"}, "start_date": S, "end_date": E,
                                      "results": [{"date": S}]}]}
e = _Ex(RANGE_STATE)
out = e._resolve_arguments({"sql": RUN_A})
check("the turn's TDA_DateRange dates reach the executed SQL",
      out["sql"] == f"SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate BETWEEN DATE '{S}' AND DATE '{E}'", out["sql"])
evs = [ev[0] for ev in e.events_to_yield if (ev[0].get("details") or {}).get("correction_type") == "phantom_date_source"]
check("…with a System Correction step naming the original and the executed SQL",
      len(evs) == 1 and evs[0]["details"]["original_sql"] == RUN_A and "DATE '" in evs[0]["details"]["executed_sql"], evs)
e0 = _Ex({})
check("no date range this turn → the SQL is left as written (the database's error stands)",
      e0._resolve_arguments({"sql": RUN_A})["sql"] == RUN_A)
e1 = _Ex(RANGE_STATE)
check("a normal statement is byte-identical and emits nothing",
      e1._resolve_arguments({"sql": "SELECT COUNT(*) FROM fitness_db.Sales"})["sql"] == "SELECT COUNT(*) FROM fitness_db.Sales"
      and e1.events_to_yield == [])
src = open(os.path.join(ROOT, "src/trusted_data_agent/agent/executor.py")).read()
fn = src.split("def _resolve_arguments")[1].split("\n    def ")[0]
check("the rewrite runs after the bind substitution and before the embedded phase-reference substitution",
      fn.index("_substitute_sql_date_binds(value)") < fn.index("_rewrite_phantom_date_source(value)")
      < fn.index("_sub_embedded_scalar"))

print("[5] the next three shapes the bench produced once the first was fixed")
R3 = ("SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate >= (SELECT MIN(date_value) FROM (SELECT CAST(date_value AS DATE) "
      "as date_value FROM TABLE(TD_SYSFNLIB.TD_RANGE_DATE(CAST(2026-09-24 AS DATE), -30, 0)) AS T) AS DateRange)")
check("a MIN over an invented date GENERATOR (TABLE(fn(...))) is the range's start",
      P.rewrite(R3, S, E)[0] == f"SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate >= DATE '{S}'", P.rewrite(R3, S, E)[0])
R4 = ("SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate BETWEEN (SELECT MIN(date) FROM (SELECT CAST(val AS DATE) as date "
      "FROM UNNEST(result_of_phase_2) as val)) AND (SELECT MAX(date) FROM (SELECT CAST(val AS DATE) as date FROM "
      "UNNEST(result_of_phase_2) as val))")
check("a phase reference wrapped in ANY function (UNNEST(result_of_phase_2), run 4) is the same phantom source",
      P.rewrite(R4, S, E)[0] == f"SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate BETWEEN DATE '{S}' AND DATE '{E}'",
      P.rewrite(R4, S, E)[0])
check("…a real subquery with a function in its FROM is still untouched",
      P.rewrite("SELECT 1 FROM t WHERE d = (SELECT MAX(x) FROM LATERAL(SELECT x FROM real_t) r)", S, E)[1] == [])
check("…but a table function outside a MIN/MAX or IN subquery is not touched",
      P.rewrite("SELECT * FROM TABLE(calendar_fn(1)) AS c", S, E)[0] == "SELECT * FROM TABLE(calendar_fn(1)) AS c")
e2 = _Ex(RANGE_STATE)
out = e2._resolve_arguments({"sql": "SELECT COUNT(*) FROM s WHERE SaleDate >= 'result_of_phase_2.start_date' "
                                    "AND SaleDate <= 'result_of_phase_2.end_date'"})
check("a range bound WITH a key becomes a DATE literal (no 6760 round trip)",
      out["sql"] == f"SELECT COUNT(*) FROM s WHERE SaleDate >= DATE '{S}' AND SaleDate <= DATE '{E}'", out["sql"])
check("a phase reference with any other key resolves to that key's value",
      _Ex(RANGE_STATE)._resolve_arguments({"sql": "SELECT 1 WHERE d = 'result_of_phase_1.current_date'"})["sql"]
      == "SELECT 1 WHERE d = '2026-09-24'")
check("…and a keyed reference is never the prefix of a bare one (both in one statement)",
      _Ex(RANGE_STATE)._resolve_arguments({"sql": "SELECT 1 WHERE d = 'result_of_phase_1' AND x = 'result_of_phase_2.nope'"})["sql"]
      == "SELECT 1 WHERE d = '2026-09-24' AND x = 'result_of_phase_2.nope'",
      _Ex(RANGE_STATE)._resolve_arguments({"sql": "SELECT 1 WHERE d = 'result_of_phase_1' AND x = 'result_of_phase_2.nope'"})["sql"])
out = _Ex(RANGE_STATE)._resolve_arguments({"sql": "SELECT COUNT(*) FROM s WHERE SaleDate >= '{result_of_phase_2.start_date}' "
                                               "AND SaleDate <= '{result_of_phase_2.end_date}'"})
check("…and in braces (`'{result_of_phase_2.start_date}'`) — the braces go with the reference",
      out["sql"] == f"SELECT COUNT(*) FROM s WHERE SaleDate >= '{S}' AND SaleDate <= '{E}'", out["sql"])
check("…the bracket form still resolves as before",
      _Ex(RANGE_STATE)._resolve_arguments({"sql": "SELECT 1 WHERE d >= '{result_of_phase_2[start_date]}'"})["sql"]
      == f"SELECT 1 WHERE d >= '{S}'")
check("…an unknown key is left as written",
      _Ex(RANGE_STATE)._resolve_arguments({"sql": "SELECT 1 WHERE x = 'result_of_phase_2.nope'"})["sql"]
      == "SELECT 1 WHERE x = 'result_of_phase_2.nope'")

print("[6] an ISO date string compared with a TIMESTAMP (Error 6760) — one rewrite, every lane")
from trusted_data_agent.agent import sql_dialect as SD          # noqa: E402
R1 = f"SELECT COUNT(*) AS SaleCount FROM fitness_db.Sales WHERE SaleDate >= '{S}' AND SaleDate <= '{E}'"
FIXED = f"SELECT COUNT(*) AS SaleCount FROM fitness_db.Sales WHERE SaleDate >= DATE '{S}' AND SaleDate <= DATE '{E}'"
ERR = "[Teradata Database] [Error 6760] [SQLState HY000] Invalid timestamp."
check("the rewrite types both literals", SD.teradata_iso_date_literal_rewrite(R1) == FIXED, SD.teradata_iso_date_literal_rewrite(R1))
for sql in (f"SELECT 1 WHERE d >= DATE '{S}'", "SELECT 1 WHERE d >= TIMESTAMP '2026-08-25 00:00:00'",
            f"SELECT 1 WHERE note = 'shipped {S}'", "SELECT 1; SELECT 2"):
    check(f"untouched: {sql}", SD.teradata_iso_date_literal_rewrite(sql) is None)
check("the error is recognised", SD.looks_like_invalid_timestamp(ERR) and not SD.looks_like_invalid_timestamp("Error 3706"))

import asyncio  # noqa: E402
import json     # noqa: E402
from trusted_data_agent.agent import phase_executor as pe      # noqa: E402
from trusted_data_agent.agent import tool_enforcement as te    # noqa: E402
strat = pe.TeradataDateLiteralStrategy(None)
act = {"tool_name": "base_readQuery", "arguments": {"sql": R1}}
check("Optimize: the strategy claims 6760", strat.can_handle(ERR) and not strat.can_handle("Error 3706 syntax"))
corrected, evs = asyncio.run(strat.generate_correction(act, {"status": "error", "data": ERR}))
check("Optimize: it corrects deterministically (0 LLM) with a System Self-Correction step",
      corrected["arguments"]["sql"] == FIXED and evs and evs[0][0]["details"]["correction_type"] == "teradata_date_literal", corrected)
check("Optimize: it declines when there is no bare ISO date string (the LLM judges it)",
      asyncio.run(strat.generate_correction({"tool_name": "t", "arguments": {"sql": "SELECT 1"}}, {"data": ERR})) == (None, []))
_chain = [type(x).__name__ for x in pe.CorrectionHandler(None).strategies]
check("Optimize: the strategy sits in the real chain before the LLM strategies",
      "TeradataDateLiteralStrategy" in _chain
      and _chain.index("TeradataDateLiteralStrategy") < _chain.index("GenericCorrectionStrategy"), _chain)

seen = []
async def _tool(**kw):
    seen.append(kw["sql"])
    if "DATE '" in kw["sql"]:
        return [{"type": "text", "text": json.dumps({"status": "success", "results": [{"SaleCount": 12}]})}], None
    return [{"type": "text", "text": json.dumps({"status": "error", "error": ERR})}], None
out = asyncio.run(te._make_guarded_coroutine("base_readQuery", _tool, "t-6760", "u1")(sql=R1))
check("ReAct: the statement runs once more with DATE literals and the model is told why",
      seen == [R1, FIXED] and '"SaleCount": 12' in out[0][0]["text"] and "DATE literal" in out[0][-1]["text"], (seen, out))
seen.clear()
async def _exec(a):
    return json.loads((await _tool(**a))[0][0]["text"])
nav = asyncio.run(te.guarded_sql_call("database_query", {"sql": R1}, _exec, session_id="t-6760n"))
check("Navigator: the same rewrite, the same words", nav["outcome"] == "success" and nav["executed_sql"] == FIXED
      and any("DATE literal" in n for n in nav["notes"]), nav)

print("[7] a date range with no anchor is anchored on today, not on a date the LLM invents")
import types  # noqa: E402


class _LLMCalled(Exception):
    pass


class _Stub:
    def __init__(self, today):
        self.dependencies = {"STATE": {"mcp_tools": {"TDA_DateRange": types.SimpleNamespace(
            args={"start_date": {"required": True}, "date_phrase": {"required": True}})}}}
        self.is_in_loop = False
        self._today = today
        self.original_user_input = "how many sales happened in the past 30 days?"
        self.llm_calls = 0
        self.source = "test"; self.session_id = "s"; self.user_uuid = "u"
        self._last_call_metadata = {}; self.turn_input_tokens = 0; self.turn_output_tokens = 0

    def _log_system_event(self, *a, **k):
        pass

    def _turn_date_context(self):
        return {"today": self._today} if self._today else {}

    def _format_sse_with_depth(self, ev, *a, **k):
        return ev

    async def _call_llm_and_update_tokens(self, *a, **k):
        self.llm_calls += 1
        raise _LLMCalled()


async def _drain(gen):
    out = []
    async for ev in gen:
        out.append(ev)
    return out


def refine(today, args):
    px = object.__new__(pe.PhaseExecutor)
    px.executor = _Stub(today)
    act = {"tool_name": "TDA_DateRange", "arguments": dict(args)}
    phase = {"phase": 2, "relevant_tools": ["TDA_DateRange"], "arguments": dict(args), "_needs_refinement": True}
    try:
        evs = asyncio.run(_drain(px._proactively_refine_arguments(act, phase)))
    except _LLMCalled:
        evs = None
    return act, px.executor.llm_calls, evs


act, calls, evs = refine("2026-09-24", {"date_phrase": "past 30 days"})
check("the missing anchor is today's date from the turn's TDA_CurrentDate",
      act["arguments"].get("start_date") == "2026-09-24", act)
check("…with no refinement LLM round", calls == 0, calls)
check("…and a System Correction step saying so",
      evs is not None and any((e.get("details") or {}).get("correction_type") == "date_range_anchor" for e in evs if isinstance(e, dict)), evs)
act, calls, _ = refine(None, {"date_phrase": "past 30 days"})
check("no TDA_CurrentDate in the turn → the refinement path decides, as before", calls == 1 and "start_date" not in act["arguments"])
act, calls, _ = refine("2026-09-24", {"start_date": "2024-05-22", "date_phrase": "past 30 days"})
check("an anchor the plan DID give is never overridden", act["arguments"]["start_date"] == "2024-05-22", act)

print("[R] a phantom source the rewrite does not recognise is refused before the database")
_R1 = "SELECT COUNT(*) FROM s.Sales x JOIN TDA_DateRange_result d ON x.SaleDate = d.date"
check("a JOIN on the date-range step's output names it", P.phantom_names(_R1) == ["TDA_DateRange_result"], P.phantom_names(_R1))
_blk = te.phantom_source_block("base_readQuery", {"sql": _R1})
check("…the gate refuses it, recovery-shaped (names DATE literals)",
      _blk and _blk["status"] == "error" and "TDA_DateRange_result" in _blk["error"] and "DATE 'YYYY-MM-DD'" in _blk["error"], _blk)
check("…retryable: not marked definitive",
      _blk and not any(m in _blk["error"] for m in ("This is not retryable", "not retryable")), _blk)
check("a phase reference read through UNNEST(…) is named",
      P.phantom_names("SELECT d FROM UNNEST(result_of_phase_2) AS t(d)") == ["result_of_phase_2"])
check("a ? read FROM is named as a bind", P.phantom_names("SELECT * FROM TABLE(?) t") == ["a ? bind"])
check("text a user quoted is never a source", P.phantom_names("SELECT 'from result_of_phase_1' FROM s.t") == [])
check("a qualified real table is never a source", P.phantom_names("SELECT * FROM mydb.TDA_Sales") == [])
check("a real statement passes the gate", te.phantom_source_block("base_readQuery", {"sql": "SELECT 1 FROM s.t"}) is None)
check("a framework tool is exempt", te.phantom_source_block("TDA_LLMTask", {"sql": _R1}) is None)
_gb, _gf, _gk = te.sql_gate_before("base_readQuery", {"sql": _R1}, "t-phantom-gate", te.LANE_REACT)
check("…and it is wired into the ONE gate (every lane)", _gb is not None and _gb.get("error_type") == "phantom_source", (_gb, _gf, _gk))

print("[F] a report written after every data query failed is told so, before it writes")
from trusted_data_agent.agent.honesty import primitives as HP   # noqa: E402
_err = {"status": "error", "error": "[Error 3807] Object 'TDA_DateRange_result' does not exist."}
_fail = [{"tool_name": "TDA_DateRange", "arguments": {"date_phrase": "past 30 days"}, "result": {"status": "success", "results": [{"date": "2026-09-01"}] * 30}},
         {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM s.Sales"}, "result": _err}]
_n = HP.data_failure_report_note(_fail)
check("all data queries failed → a note naming the count and the database's words",
      _n and "1 of 1" in _n and "3807" in _n and "number of items in a list" in _n, _n)
check("one query succeeded → no note",
      HP.data_failure_report_note(_fail + [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}, "result": {"status": "success", "results": [{"n": 1}]}}]) is None)
check("no data query at all → no note (nothing failed)", HP.data_failure_report_note(_fail[:1]) is None)
_same = HP.data_query_outcomes(_fail)
check("…the same rule the honesty gate reads (one definition)", _same[1] == 0 and _same[2] == 1, _same)

from trusted_data_agent.mcp_adapter import adapter as AD   # noqa: E402
from unittest.mock import patch   # noqa: E402
_seen = {}


async def _fake_llm(**kw):
    _seen["prompt"] = kw.get("prompt")
    return (json.dumps({"direct_answer": "The sales count could not be retrieved."}), 10, 5, None, None)


def _report(args):
    _seen.clear()
    with patch.object(AD, "turn_llm", return_value=(object(), "Google", "m")), \
         patch.object(AD.llm_handler, "call_llm_api", side_effect=_fake_llm):
        try:
            res, _, _ = asyncio.run(AD._invoke_final_report_task(
                {}, {"tool_name": "TDA_FinalReport", "arguments": args},
                {"result_of_phase_1": [{"date": "2026-09-01"}] * 30}, user_uuid="u1", session_id="t-report"))
        except Exception as e:  # noqa: BLE001
            return None, repr(e)
    return res, _seen.get("prompt")


_res, _prompt = _report({"user_question": "how many sales in the past 30 days?", "_data_failure_note": _n})
check("the report prompt carries the fact", isinstance(_prompt, str) and "DATA RETRIEVAL FAILED" in _prompt, _prompt if not isinstance(_prompt, str) else _prompt[-300:])
_res, _prompt = _report({"user_question": "q", "report": "There were 30 sales.", "_data_failure_note": _n})
check("a pre-written report is NOT passed through when the data queries failed",
      _prompt is not None and "DATA RETRIEVAL FAILED" in (_prompt or ""), _res)
_res, _prompt = _report({"user_question": "q", "report": "There were 30 sales."})
check("…without the fact, pass-through is unchanged (no LLM call)", _prompt is None and "30 sales" in json.dumps(_res), _res)
_res, _prompt = _report({"user_question": "q"})
check("…and a report without the fact reads no such note", isinstance(_prompt, str) and "DATA RETRIEVAL FAILED" not in _prompt)

_src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "phase_executor.py")).read()
check("the phase executor stamps the note from the turn's own trace",
      "data_failure_report_note(calls_from_trace(self.executor.turn_action_history))" in _src
      and 'action.setdefault("arguments", {})["_data_failure_note"]' in _src)

print("[B] a range bound written as a name becomes the turn's date (fusion bench run 4)")
RUN_4 = "SELECT COUNT(*) as SalesCount FROM fitness_db.Sales WHERE SaleDate >= 'result_of_phase_2_start' AND SaleDate <= 'result_of_phase_2_end'"
e = _Ex(RANGE_STATE)
out = e._resolve_arguments({"sql": RUN_4})
check("the quoted bound names reach the executed SQL as DATE literals",
      out["sql"] == f"SELECT COUNT(*) as SalesCount FROM fitness_db.Sales WHERE SaleDate >= DATE '{S}' AND SaleDate <= DATE '{E}'", out["sql"])
check("…with a System Correction step", any((ev[0].get("details") or {}).get("correction_type") == "phantom_date_source" for ev in e.events_to_yield))
_b, _c = P.rewrite_bound_refs("x >= result_of_phase_2.start_date AND y <= TDA_DateRange.end", S, E)
check("dotted and TDA_DateRange forms too", _b == f"x >= DATE '{S}' AND y <= DATE '{E}'" and len(_c) == 2, _b)
check("a suffix that is not a bound is left as written",
      P.rewrite_bound_refs("x = 'result_of_phase_2_starting' AND n = result_of_phase_2_name", S, E) == ("x = 'result_of_phase_2_starting' AND n = result_of_phase_2_name", []))
check("half a quote is left alone", P.rewrite_bound_refs("x = 'a result_of_phase_2_start'", S, E)[1] == [])
check("no date range this turn → left as written", _Ex({})._resolve_arguments({"sql": RUN_4})["sql"] == RUN_4)

print("[U] an unquoted date is a date literal before it runs — every lane (fusion bench run 2)")
RUN_2 = "SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate >= 2026-08-25 AND SaleDate <= 2026-09-23"
check("unquoted ISO dates become DATE literals",
      SD.bare_iso_date_rewrite(RUN_2) == "SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate >= DATE '2026-08-25' AND SaleDate <= DATE '2026-09-23'")
check("SQLite gets the quoted ISO string it compares as text",
      SD.bare_iso_date_rewrite("SELECT * FROM t WHERE d > 2026-08-25", typed=False) == "SELECT * FROM t WHERE d > '2026-08-25'")
for _q, _why in (("SELECT * FROM t WHERE x = '2026-08-25'", "a quoted string"),
                 ("SELECT * FROM t WHERE d = DATE '2026-08-25'", "a typed literal"),
                 ("SELECT * FROM t -- since 2026-08-25", "a comment"),
                 ('SELECT "2026-08-25" FROM t', "a quoted identifier"),
                 ("SELECT 2026-13-01 FROM t", "an impossible month"),
                 ("SELECT a-b-c, 12026-08-25 FROM t", "arithmetic that is not a date"),
                 ("SELECT 1; SELECT 2026-08-25", "a multi-statement request")):
    check(f"…never {_why}", SD.bare_iso_date_rewrite(_q) is None, SD.bare_iso_date_rewrite(_q))
for _lane in (te.LANE_OPTIMIZE, te.LANE_REACT, te.LANE_NAVIGATOR):
    _r, _f, _k = te.sql_gate_before("base_readQuery", {"sql": RUN_2}, f"t-bare-{_lane.name}", _lane)
    check(f"the ONE gate returns the fix on the {_lane.name} lane",
          _r is None and _k == "bare_date" and _f and "DATE '2026-08-25'" in _f["sql"] and "1993" in _f["note"], (_r, _f, _k))
_r, _f, _k = te.sql_gate_before("base_readQuery", {"sql": RUN_2}, "t-bare-sqlite", te.LANE_NAVIGATOR, dialect="sqlite")
check("…in the source's dialect", _f and "'2026-08-25'" in _f["sql"] and "DATE '" not in _f["sql"], _f)
check("a statement without one passes untouched",
      te.sql_gate_before("base_readQuery", {"sql": "SELECT 1 FROM s.t"}, "t-bare-none", te.LANE_REACT) == (None, None, None))
_asrc = open(os.path.join(ROOT, "src/trusted_data_agent/mcp_adapter/adapter.py")).read()
check("the Optimize adapter runs the gate's fix",
      'command.setdefault("arguments", {})["sql"] = _fix["sql"]' in _asrc)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
