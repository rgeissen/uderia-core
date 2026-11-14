"""The strategic planner sees the session's earlier turns.

FOUND LIVE (Optimize campaign, 2026-10-27, PASS-4): on eight follow-up turns whose first turn
had answered correctly ("We have 100 customers, with 59 service tickets awaiting resolution"),
the planner wrote "the previous counts mentioned are not available in the conversation
history" and declined. Root cause: `ContextBuilder._format_strategic_history` — the DEFAULT
path that fills the strategic prompt's `turn_action_history` — read
`session["workflow_history"]["workflow_history"]`. The session keeps its turns at
`session["last_turn_data"]["workflow_history"]` (every other reader — the workflow-history and
plan-hydration modules, execution_service's `previous_turn_data`, the legacy
`_create_summary_from_history` — reads that key), so the formatter returned an empty history on
every turn of every session. CI-pure, on the session file's real shape.
"""
import json
import os
import sys
import types

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


from components.builtin.context_window import context_builder as CB  # noqa: E402

TURN1 = {"turn": 1, "is_session_primer": True, "user_query": "You answer questions about fitness_db.",
         "final_summary_text": "I am configured to assist with fitness_db.", "execution_trace": []}
TURN2 = {"turn": 2, "user_query": "How many customers do we have, and how many service tickets are still unresolved?",
         "final_summary_text": "We currently have 100 customers, with 59 service tickets awaiting resolution.",
         "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}},
                              "result": {"status": "success", "results": [{"customer_count": 100, "unresolved": 59}]}}]}


def fmt(session, budget=343):
    b = CB.ContextBuilder.__new__(CB.ContextBuilder)
    b._assembly_ctx = types.SimpleNamespace(session_data=session)
    b._assembled = types.SimpleNamespace(contributions={"workflow_history": types.SimpleNamespace(tokens_used=budget)})
    b._executor = None
    return json.loads(b._format_strategic_history())


print("\n── §1 the session file's real shape (last_turn_data) reaches the planner")
real = {"id": "s", "chat_object": [], "last_turn_data": {"workflow_history": [TURN1, TURN2]}}
out = fmt(real)
turns = out.get("workflow_history") or []
check("the most recent turn is in the strategic history", any(t.get("turn") == 2 for t in turns), json.dumps(out)[:200])
check("…with its answer text (the figures a follow-up computes from)",
      any("100 customers" in str(t.get("final_summary_text")) for t in turns))
check("…and its tool result", any("59" in json.dumps(t.get("execution_trace")) for t in turns))
check("the most recent turn is marked most recent",
      any((t.get("turn_metadata") or {}).get("is_most_recent") and t.get("turn") == 2 for t in turns))

print("\n── §2 the shapes that must stay empty, and the legacy shape")
check("a new session (last_turn_data empty) → an empty history", fmt({"last_turn_data": {"workflow_history": []}}).get("workflow_history") == [])
check("no session data at all → an empty history", fmt({}).get("workflow_history") == [])
legacy = {"workflow_history": {"workflow_history": [TURN2]}}
check("a caller that passes the history at the top level is still read", any(t.get("turn") == 2 for t in fmt(legacy).get("workflow_history") or []))

print("\n── §2b an allow-list: telemetry stays out, figures stay in, results are capped")
heavy = dict(TURN2, provenance_chain=[{"hash": "x" * 400}] * 20, tool_surface={"offered": ["t"] * 200},
             context_window_snapshot_event={"contributions": [{"m": "x" * 200}] * 20}, bom={"x": "y" * 2000},
             execution_trace=[{"action": {"tool_name": "TDA_SystemLog", "arguments": {"message": "x" * 5000}}, "result": {}},
                              {"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT * FROM t"}},
                               "result": {"status": "success", "results": [{"id": i, "v": "x" * 1000} for i in range(500)],
                                          "metadata": {"columns": [{"name": "id"}, {"name": "v"}]}}}])
out = fmt({"last_turn_data": {"workflow_history": [heavy]}})
t = out["workflow_history"][0]
blob = json.dumps(out)
check("telemetry fields are not carried", not any(k in t for k in ("provenance_chain", "tool_surface", "context_window_snapshot_event", "bom")), sorted(t)[:12])
check("system log entries are not carried", all(e["action"]["tool_name"] != "TDA_SystemLog" for e in t["execution_trace"]))
check("a 500-row result keeps its row count and only a few rows", t["execution_trace"][0]["result"]["row_count"] == 500
      and len([r for r in t["execution_trace"][0]["result"]["results"] if isinstance(r, dict)]) <= 5)
check("long cells are clipped", "x" * 400 not in blob)
check("the answer text (the figures) survives", "100 customers" in t["final_summary_text"])
check("the whole turn is small", len(blob) < 6000, len(blob))

print("\n── §3 one source with the legacy builder")
from trusted_data_agent.agent import planner as pl  # noqa: E402
p = pl.Planner.__new__(pl.Planner)
legacy_out = json.loads(p._create_summary_from_history(real["last_turn_data"]))
check("the legacy builder (given last_turn_data, as execution_service does) and the default one keep the same latest turn",
      [t.get("turn") for t in legacy_out.get("workflow_history")][-1:] == [t.get("turn") for t in turns][-1:])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
