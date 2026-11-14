"""A dropped MCP connection is resent once for a read; a tool's own error reads as that error.

Both found on the fusion bench, 2026-09-24:
  * the first call after a server restart failed with "unhandled errors in a TaskGroup" (the
    MCP client's HTTP session, not the tool) and cost an LLM correction round, although the
    identical call succeeded a second later;
  * a Teradata error — "[Version 20.0.0.63] … [SQLState 42000] Syntax error …" — was taken for
    a JSON array by the adapter's parser and reached the model labelled "Tool returned
    malformed JSON string".

Drives the real ``invoke_mcp_tool`` (Optimize) and the real ReAct tool wrapper over a fake wire.
No server, no network, no API spend.
"""
import asyncio
import json
import logging
import os
import sys
import types
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)

import anyio   # noqa: E402
import httpx   # noqa: E402
from trusted_data_agent.agent import tool_enforcement as te   # noqa: E402
from trusted_data_agent.mcp_adapter import adapter            # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f": {detail}" if detail is not None else ""))


def group(*leaves):
    return ExceptionGroup("unhandled errors in a TaskGroup", list(leaves))


DROP = group(httpx.ConnectError("connection refused"))
TD_ERROR = ("Internal error: (teradatasql.OperationalError) [Version 20.0.0.63] [Session 2128] "
            "[Teradata Database] [Error 3707] [SQLState 42000] Syntax error, expected something like a name "
            "between the 'as' keyword and the 'date' keyword.\n at gosqldriver/teradatasql.formatError ErrorUtil.go:84")

print("[1] what counts as the connection failing")
check("a TaskGroup whose leaf is a connect error", te.transient_transport_error(DROP))
check("a closed or broken stream", te.transient_transport_error(group(anyio.ClosedResourceError()))
      and te.transient_transport_error(group(anyio.BrokenResourceError(), ConnectionResetError())))
check("a connect TIMEOUT (nothing reached the server)", te.transient_transport_error(group(httpx.ConnectTimeout("t"))))
check("NOT a read timeout — the server may still be working", not te.transient_transport_error(group(httpx.ReadTimeout("t"))))
check("NOT a group that also holds a real error", not te.transient_transport_error(group(httpx.ConnectError("x"), ValueError("bad"))))
check("NOT a tool error", not te.transient_transport_error(RuntimeError("Syntax error")))
_c = RuntimeError("wrapped"); _c.__cause__ = ConnectionRefusedError()
check("a chained cause is followed to the connection error", te.transient_transport_error(_c))
import httpcore  # noqa: E402


def _raised(outer, inner):
    """``outer`` raised FROM ``inner`` — the shape httpx really raises (it wraps httpcore's)."""
    try:
        try:
            raise inner
        except Exception as i:
            raise outer from i
    except Exception as e:  # noqa: BLE001
        return e

# The REAL shape of a refused connection (measured against a stopped MCP endpoint): an
# ExceptionGroup whose leaf is httpx.ConnectError raised FROM httpcore.ConnectError. The leaf walk
# follows the cause to httpcore's type, which is not an httpx one — it was classified NOT
# transient, so a down HTTP MCP server never got "could not be reached" (multi-source stage 1).
check("a refused connection as httpx really raises it (from httpcore) is the connection failing",
      te.transient_transport_error(group(_raised(httpx.ConnectError("All connection attempts failed"),
                                                 httpcore.ConnectError("All connection attempts failed")))))
check("…and a connect timeout from httpcore too",
      te.transient_transport_error(group(_raised(httpx.ConnectTimeout("t"), httpcore.ConnectTimeout("t")))))
check("NOT a read timeout from httpcore — the server may still be working",
      not te.transient_transport_error(group(_raised(httpx.ReadTimeout("t"), httpcore.ReadTimeout("t")))))

print("[2] only a read is ever resent")
check("a SELECT through a read tool", te.transport_retry_allowed("base_readQuery", {"sql": "SELECT 1"}))
check("a catalog tool", te.transport_retry_allowed("base_tableList", {"database_name": "d"}))
check("NOT a DELETE through a read-named tool", not te.transport_retry_allowed("base_readQuery", {"sql": "DELETE FROM t"}))
check("NOT a tool named like a write", not te.transport_retry_allowed("base_writeQuery", {"sql": "SELECT 1"})
      and not te.transport_retry_allowed("send_email", {}))


# ── the Optimize lane: invoke_mcp_tool over a fake wire ──────────────────────────────────────
class _Text:
    def __init__(self, t): self.text = t


class _Result:
    def __init__(self, t, is_error=False):
        self.content = [_Text(t)]
        self.isError = is_error


SENT = []


def optimize(tool, args, answers):
    calls = SENT
    calls.clear()

    class _S:
        async def call_tool(self, name, a):
            calls.append(a)
            out = answers[min(len(calls) - 1, len(answers) - 1)]
            if isinstance(out, BaseException):
                raise out
            return out

    class _Ctx:
        async def __aenter__(self): return _S()
        async def __aexit__(self, *e): return False

    class _Client:
        def session(self, _): return _Ctx()

    state = {"mcp_client": _Client(),
             "mcp_tools": {tool: types.SimpleNamespace(name=tool, args={k: {"name": k} for k in args})}}
    with patch.object(adapter, "get_user_mcp_server_id", return_value="srv1"):
        res, _, _ = asyncio.run(adapter.invoke_mcp_tool(state, {"tool_name": tool, "arguments": dict(args)},
                                                        user_uuid="u1", session_id="t-retry", workflow_state={}))
    return res, len(calls)


OK = _Result(json.dumps({"status": "success", "results": [{"n": 7}]}))

print("[3] Optimize: a dropped connection is resent once for a read")
res, n = optimize("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM d.t"}, [DROP, OK])
check("the read succeeds on the second attempt, no correction needed", res.get("status") == "success" and n == 2, (res, n))
res, n = optimize("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM d.t"}, [DROP, DROP])
check("…a second drop is reported, never a third attempt", res.get("status") == "error" and n == 2, (res, n))
res, n = optimize("base_writeQuery", {"sql": "INSERT INTO d.t VALUES (1)"}, [DROP, OK])
check("a write is NEVER resent (it may already have run)", res.get("status") == "error" and n == 1, (res, n))
res, n = optimize("base_readQuery", {"sql": "SELECT 1"}, [RuntimeError("Syntax error near FROM"), OK])
check("a failure that is not the connection is not resent", res.get("status") == "error" and n == 1, (res, n))

print("[4] Optimize: the server's own error reads as that error")
res, n = optimize("base_readQuery", {"sql": "SELECT CAST(x AS DATE) as date FROM d.t"}, [_Result(TD_ERROR, is_error=True)])
check("the error names what the database said, not 'malformed JSON'",
      res.get("status") == "error" and "Error 3707" in res.get("error", "") and "malformed" not in res.get("error", ""), res)
check("…and the full text stays in `data` for the correction strategies", res.get("data") == TD_ERROR, res.get("data"))
res, n = optimize("base_readQuery", {"sql": "SELECT 1"}, [OK])
check("a successful result is parsed exactly as before", res.get("status") == "success" and res.get("results") == [{"n": 7}], res)

print("[4b] Optimize: the gate's mechanical fix is what runs")
res, n = optimize("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM d.t WHERE d >= 2026-08-25"}, [OK])
check("an unquoted date reaches the database as a DATE literal",
      n == 1 and SENT and SENT[0].get("sql") == "SELECT COUNT(*) AS n FROM d.t WHERE d >= DATE '2026-08-25'", SENT)
res, n = optimize("base_readQuery", {"sql": "SELECT 1 FROM d.t JOIN TDA_DateRange_result r ON 1=1"}, [OK])
check("a phantom source never reaches the database", n == 0 and res.get("error_type") == "phantom_source", (res, n))


# ── the ReAct lane: the guarded coroutine around a fake LangChain MCP tool ───────────────────
def react(tool, args, answers):
    calls = []

    async def _tool(**kw):
        calls.append(kw)
        out = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(out, BaseException):
            raise out
        return out

    out = asyncio.run(te._make_guarded_coroutine(tool, _tool, "t-react-retry", "u1")(**dict(args)))
    return out, len(calls)


BLOCKS = ([{"type": "text", "text": json.dumps({"status": "success", "results": [{"n": 7}]})}], None)
print("[5] ReAct: the same rule")
out, n = react("base_readQuery", {"sql": "SELECT COUNT(*) AS n FROM d.t"}, [DROP, BLOCKS])
check("the read succeeds on the second attempt", n == 2 and '"n": 7' in out[0][0]["text"], (out, n))
out, n = react("base_writeQuery", {"sql": "INSERT INTO d.t VALUES (1)"}, [DROP, BLOCKS])
check("a write is never resent", n == 1 and "Error from base_writeQuery" in str(out[0]), (out, n))
out, n = react("base_readQuery", {"sql": "SELECT 1"}, [RuntimeError("Syntax error"), BLOCKS])
check("a tool error is not resent", n == 1 and "Error from base_readQuery" in str(out[0]), (out, n))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
