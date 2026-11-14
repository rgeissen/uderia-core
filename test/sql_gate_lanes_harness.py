"""The three SQL lanes, driven through their REAL entry points on one truth table.

Lanes:
  optimize  — ``mcp_adapter.adapter.invoke_mcp_tool`` over a fake MCP wire
  react     — ``tool_enforcement._make_guarded_coroutine`` around a fake LangChain tool
  navigator — ``tool_enforcement.guarded_sql_call`` around a fake executor

For every scenario × lane it records what the lane RETURNED, what reached the database
(the statement, or nothing), and the side effects every lane shares: the denied-call finding
on the tool surface, the tool-result firewall scans, the result-set registry, the turn budget's
step count and the loop guard's ledger.

``observe()`` returns a JSON-serialisable dict. ``test_sql_gate_lanes.py`` compares it with the
golden file written from the code BEFORE the lanes moved onto one gate — the byte-identical
proof step 7 of DAB_P1_NAVIGATOR_QUERY.md asks for. Regenerate the golden only on purpose:
    python test/sql_gate_lanes_harness.py --write
"""

import asyncio
import json
import os
import sys
import types
from contextlib import ExitStack
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "sql_gate_lanes.golden.json")

from trusted_data_agent.core.config import APP_CONFIG                    # noqa: E402
from trusted_data_agent.mcp_adapter import adapter                        # noqa: E402
from trusted_data_agent.agent import tool_enforcement as te               # noqa: E402
from trusted_data_agent.agent import loop_guard, result_sets, turn_budget  # noqa: E402
from trusted_data_agent.core import agent_authority as aa                 # noqa: E402

TOOL = "base_readQuery"
ROWS = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


# ── scripted database ────────────────────────────────────────────────────────────────────────
class Db:
    """What the database answers, per statement: a result dict, or an exception to raise."""

    def __init__(self, script):
        self.script = script
        self.seen = []

    def answer(self, sql):
        self.seen.append(sql)
        for pred, out in self.script:
            if pred(sql):
                if isinstance(out, BaseException):
                    raise out
                return out
        return {"status": "success", "results": ROWS}


def ok(rows=ROWS):
    return {"status": "success", "results": rows}


# ── the three lanes ──────────────────────────────────────────────────────────────────────────
def run_optimize(db, args, sid):
    class _C:
        def __init__(self, t): self.text = t

    class _R:
        def __init__(self, t): self.content = [_C(t)]

    class _S:
        async def call_tool(self, name, a):
            try:
                return _R(json.dumps(db.answer(a.get("sql"))))
            except Exception:
                raise

    class _Ctx:
        async def __aenter__(self): return _S()
        async def __aexit__(self, *e): return False

    class _Client:
        def session(self, sid_): return _Ctx()

    state = {"mcp_client": _Client(),
             "mcp_tools": {TOOL: types.SimpleNamespace(name=TOOL, args={"sql": {"name": "sql"}})}}
    with patch.object(adapter, "get_user_mcp_server_id", return_value="srv1"):
        res, i, o = asyncio.run(adapter.invoke_mcp_tool(
            state, {"tool_name": TOOL, "arguments": dict(args)},
            user_uuid="u1", session_id=sid, workflow_state={}))
    return {"returned": res, "tokens": [i, o]}


def run_react(db, args, sid):
    async def _tool(**kw):
        out = db.answer(kw.get("sql"))
        # Faithful to langchain_mcp_adapters 0.2 (`_convert_call_tool_result`): content is a
        # LIST of content blocks, the artifact None unless the server sent structuredContent.
        # A plain-string fake is NOT faithful — a note appended to a string breaks the JSON,
        # which the real list shape never does (found writing this harness).
        return [{"type": "text", "text": json.dumps(out)}], None
    guarded = te._make_guarded_coroutine(TOOL, _tool, sid, "u1")
    out = asyncio.run(guarded(**dict(args)))
    return {"returned": list(out) if isinstance(out, tuple) else out}


def run_navigator(db, args, sid):
    async def _exec(a):
        return db.answer(a.get("sql"))
    out = asyncio.run(te.guarded_sql_call("database_query", dict(args), _exec,
                                          session_id=sid, user_uuid="u1"))
    return {"returned": out}


LANES = {"optimize": run_optimize, "react": run_react, "navigator": run_navigator}


# ── scenarios ────────────────────────────────────────────────────────────────────────────────
def _principal(**scope):
    return aa.AgentPrincipal(owner_uuid="u1", profile_id="p1", scope=aa.parse_scope(scope))


SCENARIOS = [
    # name, calls (list of argument dicts, run in order in ONE turn), db script, context
    ("read_success", [{"sql": "SELECT id, name FROM db1.t"}], [], {}),
    ("read_empty", [{"sql": "SELECT id FROM db1.t WHERE name = 'zz'"}],
     [(lambda s: True, ok([]))], {}),
    ("db_error", [{"sql": "SELECT nope FROM db1.t"}],
     [(lambda s: True, {"status": "error", "error": "Column nope does not exist"})], {}),
    ("db_raises", [{"sql": "SELECT id FROM db1.t"}],
     [(lambda s: True, RuntimeError("socket closed"))], {}),
    ("nonstring_sql", [{"sql": {"sql": "SELECT 1"}}], [], {}),
    ("deny_writes_delete", [{"sql": "DELETE FROM db1.t"}], [], {"principal": {"deny_writes": True}}),
    ("deny_writes_read_ok", [{"sql": "SELECT id FROM db1.t"}], [], {"principal": {"deny_writes": True}}),
    ("denied_tool", [{"sql": "SELECT id FROM db1.t"}], [],
     {"principal": {"denied_tools": [TOOL, "database_query"]}}),
    ("denied_and_nonstring", [{"sql": 7}], [],
     {"principal": {"denied_tools": [TOOL, "database_query"]}}),
    ("loop_guard_repeat", [{"sql": "SELECT id FROM db1.t"}] * 3, [], {"max_tool_repeats": 1}),
    ("nonstring_then_loop", [{"sql": 7}, {"sql": 7}, {"sql": 7}], [], {"max_tool_repeats": 1}),
    ("preflight_unknown_db", [{"sql": "SELECT id FROM nosuch.t"}], [],
     {"catalog": ["db1"], "preflight": "enforce"}),
    ("preflight_observe", [{"sql": "SELECT id FROM nosuch.t"}], [],
     {"catalog": ["db1"], "preflight": "observe"}),
    ("budget_exhausted", [{"sql": "SELECT id FROM db1.t"}, {"sql": "SELECT name FROM db1.t"}], [],
     {"budget": {"steps": 1}}),
    ("teradata_limit_retry", [{"sql": "SELECT id FROM db1.t LIMIT 5"}],
     [(lambda s: "LIMIT" in s, {"status": "error",
                                "error": "[Error 3706] Syntax error: expected something between 'db1.t' and 'LIMIT'"})],
     {}),
    ("teradata_limit_retry_registry", [{"sql": "SELECT id FROM db1.t LIMIT 5"}],
     [(lambda s: "LIMIT" in s, {"status": "error",
                                "error": "[Error 3706] Syntax error: expected something between 'db1.t' and 'LIMIT'"})],
     {"lineage": True}),
    ("registry_on", [{"sql": "SELECT id, name FROM db1.t"}], [], {"lineage": True}),
    ("unverified_literal_empty", [{"sql": "SELECT id FROM db1.t WHERE city = 'Atlantis'"}],
     [(lambda s: True, ok([]))],
     {"value_mode": "enforce", "domains": [{"table": "db1.t", "columns": [{"name": "kind", "values": {"a": "a"}}]}]}),
    ("unverified_literal_budget", [{"sql": "SELECT id FROM db1.t WHERE city = 'Atlantis'"}],
     [(lambda s: True, ok([]))],
     {"value_mode": "enforce", "budget": {"steps": 3}, "lineage": True,
      "domains": [{"table": "db1.t", "columns": [{"name": "kind", "values": {"a": "a"}}]}]}),
]


def _run_scenario(name, calls, script, ctx, lane):
    sid = f"freeze-{name}-{lane}"
    db = Db(script)
    denied, scans = [], []
    thresholds = dict(getattr(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", {}) or {})
    if "max_tool_repeats" in ctx:
        thresholds["max_tool_repeats"] = ctx["max_tool_repeats"]
    else:
        thresholds.pop("max_tool_repeats", None)
    from trusted_data_agent.agent import tool_surface
    from trusted_data_agent.agent.guardrails import runtime as gr
    outs = []
    with ExitStack() as st:
        st.enter_context(patch.object(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", thresholds, create=True))
        st.enter_context(patch.object(APP_CONFIG, "PREFLIGHT_SQL_MODE", ctx.get("preflight", "observe"), create=True))
        st.enter_context(patch.object(APP_CONFIG, "PREFLIGHT_VALUE_MODE", ctx.get("value_mode", "observe"), create=True))
        st.enter_context(patch.object(APP_CONFIG, "COMPUTE_LINEAGE", bool(ctx.get("lineage")), create=True))
        st.enter_context(patch.object(APP_CONFIG, "TURN_BUDGET", bool(ctx.get("budget")), create=True))
        st.enter_context(patch.object(tool_surface, "note_denied_call", lambda n: denied.append(n)))
        st.enter_context(patch.object(gr, "scan_tool_result",
                                      lambda n, r, **k: scans.append([n, sorted((r or {}).keys())])))
        lg_tok = loop_guard.begin_turn()
        tb_tok = None
        try:
            if ctx.get("budget"):
                tb_tok = turn_budget.arm({"turnBudget": ctx["budget"]}, user_uuid="u1", session_id=sid)
            if ctx.get("catalog"):
                from trusted_data_agent.agent.preflight import guard
                guard.maybe_record_catalog("base_databaseList", {"scope": "all"},
                                           {"status": "success",
                                            "results": [{"DataBaseName": d} for d in ctx["catalog"]]}, sid)
            if ctx.get("lineage"):
                result_sets.begin_turn(sid, 1)
            if ctx.get("domains"):
                from trusted_data_agent.agent.preflight import values as _vals
                _vals.record_value_domains(sid, ctx["domains"])
            ptok = aa.set_current_principal(_principal(**ctx["principal"])) if ctx.get("principal") else None
            try:
                for args in calls:
                    try:
                        outs.append(LANES[lane](db, args, sid))
                    except Exception as exc:   # a lane that crashes is itself an observation
                        outs.append({"raised": f"{type(exc).__name__}: {exc}"})
            finally:
                if ptok is not None:
                    aa.reset_current_principal(ptok)
            tb = turn_budget.current()
            budget_used = ({"steps": tb.steps, "refusals": tb.refusals, "notes": tb.notes, "claims": tb._claims}
                           if tb is not None else None)
            regs = [{"n": s.get("n"), "statement": s.get("statement"), "rows": s.get("row_count")}
                    for s in (result_sets.statements(sid) or [])] if ctx.get("lineage") else None
        finally:
            if tb_tok is not None:
                try:
                    turn_budget.reset_turn(tb_tok)
                except Exception:
                    pass
            loop_guard.end_turn(lg_tok)
    return {"outs": outs, "db_saw": db.seen, "denied": denied, "scans": scans,
            "budget_used": budget_used, "registry": regs}


def observe():
    return {name: {lane: _run_scenario(name, calls, script, ctx, lane) for lane in LANES}
            for name, calls, script, ctx in SCENARIOS}


def canon(obj):
    return json.loads(json.dumps(obj, sort_keys=True, default=str))


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)
    data = canon(observe())
    if "--write" in sys.argv:
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        print(f"wrote {GOLDEN}")
    else:
        print(json.dumps(data, indent=1, sort_keys=True))
