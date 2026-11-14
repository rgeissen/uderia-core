"""OC-F14 — a tool failure RAISED by the server keeps the database's message; an identical
correction is not resent.

FOUND LIVE (Optimize campaign, run 20261028-194317, the first after MCP endpoint 8888 moved to
FastMCP 4): `SELECT AVG(Age) FROM fitness_db.Customers` failed and the turn's trace said only
"An exception occurred while invoking tool 'base_readQuery'" / "unhandled errors in a TaskGroup
(1 sub-exception)". FastMCP 3 answered a failed call with an `isError` RESULT carrying the
database's text; FastMCP 4 RAISES it as `McpError`, which the client wraps in two TaskGroup
ExceptionGroups — the adapter kept `str(e)` of the outer group. Measured on 8888: the leaf is
`McpError: Internal error: (teradatasql.OperationalError) … [Error 5628] … Column Age not found
in fitness_db.Customers.` With the message gone, every correction strategy that reads it
(column/table/database not found, the DB time ceiling) was blind, and the LLM correction,
given nothing to correct, re-proposed the identical SQL — run 2-3 times on R2, R3, R4 (O2).

Two rules: (1) `adapter.tool_error_text` reads the leaves, and a raised non-transport failure
is reported exactly like an `isError` result (error = first line, data = full text);
(2) a correction proposing the identical call that just failed is refused deterministically.
CI-pure.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


from mcp.shared.exceptions import McpError  # noqa: E402
from mcp.types import ErrorData  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter as AD  # noqa: E402
from trusted_data_agent.agent import phase_executor as PE  # noqa: E402
from trusted_data_agent.agent import tool_enforcement as TE  # noqa: E402
from trusted_data_agent.agent.tool_enforcement import transient_transport_error  # noqa: E402

DB_TEXT = ("(teradatasql.OperationalError) [Version 20.0.0.68] [Session 3962] [Teradata Database] "
           "[Error 5628] [SQLState HY000] Column Age not found in fitness_db.Customers.\n"
           " at gosqldriver/teradatasql.formatError ErrorUtil.go:84")
# The shape measured on 8888: ExceptionGroup → ExceptionGroup → McpError
LIVE = ExceptionGroup("unhandled errors in a TaskGroup",
                      [ExceptionGroup("unhandled errors in a TaskGroup",
                                      [McpError(ErrorData(code=-32603, message="Internal error: " + DB_TEXT))])])

print("\n── §1 the leaf text of a raised tool failure")
t = TE.tool_error_text(LIVE)
check("the database's message is recovered", "Column Age not found in fitness_db.Customers." in t, t[:120])
check("…without the JSON-RPC envelope prefix", not t.startswith("Internal error:"))
check("…and without the TaskGroup wrapper text", "unhandled errors in a TaskGroup" not in t)
check("a raised tool failure is not a transport failure (no resend, no 'unreachable')",
      not transient_transport_error(LIVE))
check("a bare wrapper with no message gives nothing", TE.tool_error_text(ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("")])) == "")
check("a client-side exception is not the tool's text (its error path is unchanged)",
      TE.tool_error_text(ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("socket closed")])) == ""
      and TE.tool_error_text(RuntimeError("socket closed")) == "")

print("\n── §2 the adapter reads it like an isError result")
import inspect  # noqa: E402
src = inspect.getsource(AD)
i = src.find("_tool_text = _tool_error_text(e)")
check("the error path reads the leaf text when the failure is not the connection",
      i > 0 and "if not _transport:" in src[i - 900:i])
check("…error = the first line, data = the full text (the isError shape every strategy matches on)",
      'result = {"status": "error", "error": _first_line[:500], "data": _tool_text}' in src)
check("…and the DB-time-ceiling hint reads that text, not the wrapper", '_db_abort_hint(result["data"])' in src)

print("\n── §2b the ReAct lane (Ideate + tools) reads it too")
content, _ = TE.failed_tool_result("base_readQuery", LIVE, {"sql": "SELECT AVG(Age) FROM fitness_db.Customers"})
check("the model is handed the database's message", "Column Age not found in fitness_db.Customers." in content, content[:160])
check("…not the wrapper", "unhandled errors in a TaskGroup" not in content)
lim = ExceptionGroup("unhandled errors in a TaskGroup", [McpError(ErrorData(code=-32603, message=
      "Internal error: [Teradata Database] [Error 3706] Syntax error: expected something between 'Customers' and 'LIMIT'."))])
content, _ = TE.failed_tool_result("base_readQuery", lim, {"sql": "SELECT * FROM fitness_db.Customers LIMIT 5"})
check("the dialect retries' error code reaches the result they read (Error 3706)", "Error 3706" in content)
check("the router (B10) is handed the leaf text on a multi-server turn",
      "(\"\" if _down else tool_error_text(exc)) or str(exc)" in inspect.getsource(TE._make_guarded_coroutine))

print("\n── §3 a correction strategy now sees the database's words")
err = {"status": "error", "error": t.splitlines()[0][:500], "data": t}
blob = f"{err['data']} {err['error']}"
check("ColumnNotFoundStrategy can handle the recovered error",
      PE.ColumnNotFoundStrategy.__new__(PE.ColumnNotFoundStrategy).can_handle(blob))
check("…it could not handle the wrapper text the adapter passed before",
      not PE.ColumnNotFoundStrategy.__new__(PE.ColumnNotFoundStrategy).can_handle(
          "unhandled errors in a TaskGroup (1 sub-exception) An exception occurred while invoking tool 'base_readQuery'."))

print("\n── §4 the identical call is never resent")
same = PE._same_call_args
check("same SQL, different case/whitespace/semicolon → the same call",
      same({"sql": "SELECT AVG(Age) AS avg_age FROM fitness_db.Customers"},
           {"sql": "select  avg(age) as avg_age\nfrom fitness_db.customers;"}))
check("a changed statement is a different call",
      not same({"sql": "SELECT AVG(Age) FROM fitness_db.Customers"}, {"sql": "SELECT AVG(DOB) FROM fitness_db.Customers"}))
check("an omitted None argument does not make a call different", same({"sql": "x", "db": None}, {"sql": "x"}))
check("unknown shapes are never 'the same'", not same(None, {"sql": "x"}) and not same({"sql": "x"}, "x"))


class _Exe:
    source = "text"
    user_uuid, session_id = "u", "s"
    turn_input_tokens = turn_output_tokens = 0
    _last_call_metadata = {"cost_usd": 0.001}
    dependencies = {"STATE": {"mcp_tools": {"base_readQuery": {}, "base_columnDescription": {}}, "mcp_prompts": {}}}

    def __init__(self, reply):
        self.reply = reply

    async def _call_llm_and_update_tokens(self, **kw):
        return self.reply, 17000, 30


async def _nosession(*a, **k):
    return None
PE.session_manager.get_session = _nosession

FAILED = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT AVG(Age) AS avg_age FROM fitness_db.Customers"}}


def correct(reply):
    st = PE.ColumnNotFoundStrategy(_Exe(reply))
    return asyncio.run(st._call_correction_llm("p", "Generic self-correction", None, FAILED))


for label, reply in (("as new arguments", '{"arguments": {"sql": "SELECT AVG(Age) AS avg_age FROM fitness_db.Customers"}}'),
                     ("as a bare argument dict", '{"sql": "select avg(age) as avg_age from fitness_db.customers"}'),
                     ("as the same tool + arguments", '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT AVG(Age) AS avg_age FROM fitness_db.Customers"}}')):
    action, events = correct(reply)
    check(f"identical proposal {label}: refused, nothing to resend",
          action is None and any("identical" in str((e[0].get("details") or {}).get("summary", "")) for e in events
                                 if isinstance(e[0], dict)))

action, _ = correct('{"arguments": {"sql": "SELECT AVG((CURRENT_DATE - DateOfBirth)/365.25) FROM fitness_db.Customers"}}')
check("a real correction still runs", action is not None and "DateOfBirth" in action["arguments"]["sql"])
action, _ = correct('{"tool_name": "base_columnDescription", "arguments": {"database_name": "fitness_db", "table_name": "Customers"}}')
check("…and so does a switch to another tool", action is not None and action["tool_name"] == "base_columnDescription")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
