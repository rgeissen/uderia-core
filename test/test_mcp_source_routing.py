"""PROP-006 phase 2, B10 — a multi-server agent's call goes to the server that holds its data.

Live finding (Sep 2026): with a fitness and a NIBRS server whose `base_readQuery` descriptions are
identical, the model sent a `fitness_db` count to the NIBRS server; the database refused the login
(Error 3523) and the model answered "no permission". Each login's own database list names exactly
the databases it may read (measured on both servers), so code can decide.

    [1] what a call reads — statement refs, database arguments, system catalogs excluded
    [2] the refusal classifier — the real Teradata texts (3523 / 3802 / 3807), PostgreSQL, not a syntax error
    [3] holdings — list, proven outcomes, precedence, TTL, per-user isolation
    [4] decide_before — the truth table (one holder → reroute, several → refuse, unknown → stand, write → stand)
    [5] decide_after — the truth table (only a "cannot read" error, a write never, the only other server)
    [6] siblings — the server's own selection, the lane's catalog, Agent Authority on the name (fail-closed)
    [7] off / single server — nothing is armed or the switch is off: every call runs as called
    [8] Optimize lane — invoke_mcp_tool end to end over fake servers: before, after, several, write, TDA_,
        the Server row names the route, the note, the single-server path untouched
    [9] ReAct lane — wrap_langchain_mcp_tools end to end: before, after, the peer failing too,
        several, the ledger the tool-end event reads, the gate runs as the tool that really ran
    [10] learning from a turn's own results, and the probe (role-resolved list tool, cached)
    [11] wiring — the seam arms and releases the table; the UI names a routed call

Run: python test/test_mcp_source_routing.py
"""
import asyncio
import types
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.agent import source_routing as sr  # noqa: E402
from trusted_data_agent.agent import tool_enforcement as te  # noqa: E402
from trusted_data_agent.core import agent_mcp_servers as ams  # noqa: E402
from trusted_data_agent.core import agent_authority as aa  # noqa: E402
from trusted_data_agent.core import tool_identity as ti  # noqa: E402
from trusted_data_agent.core.agent_mcp_servers import AgentServer  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail != "" else ""))


FIT_ERR = ("Internal error: (teradatasql.OperationalError) [Version 20.0.0.63] [Session 2245] [Teradata Database] "
           "[Error 3523] [SQLState 42000] The user does not have SELECT access to fitness_db.Customers.")
NODB_ERR = "[Teradata Database] [Error 3802] [SQLState 42S02] Database 'nosuchdb' does not exist."
SYNTAX_ERR = "[Teradata Database] [Error 3706] Syntax error: expected something between 'SELECT' and 'FROM'."
U = "user-1"

SERVERS = (AgentServer("srvA", None, ("*",), ()),                     # primary: fitness login
           AgentServer("srvB", "nibrs", ("*",), ()),                  # additional: NIBRS login
           )
NAMES = {"srvA": "Teradata MCP - fitness_manager", "srvB": "Teradata MCP - nibrs_source_agent"}
HOLD = {"srvA": {"fitness_db", "fitness_manager", "dbc"},
        "srvB": {"nibrs_source_domain", "nibrs_source_agent", "dbc"},
        "srvC": {"fitness_db", "dbc"}}


class armed:
    def __init__(self, servers=SERVERS, user=U, names=NAMES):
        self.servers, self.user, self.names = servers, user, names

    def __enter__(self):
        self.t1 = ti.set_turn_servers({s.alias: s.server_id for s in self.servers if s.alias})
        prim = next((s.server_id for s in self.servers if s.primary), None)
        self.t2 = ams.set_turn_source_labels(prim, self.names)
        self.t3 = sr.set_turn(self.user, self.servers)
        return self

    def __exit__(self, *a):
        sr.reset_turn(self.t3)
        ams.reset_turn_source_labels(self.t2)
        ti.reset_turn_servers(self.t1)


def fresh():
    sr._reset_all()


# ---------------------------------------------------------------------------------------------
print("[1] what a call reads")
check("qualified FROM target", sr.referenced_databases({"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}) == ("fitness_db",))
check("a join across two databases, order kept",
      sr.referenced_databases({"sql": "SELECT * FROM a.x JOIN b.y ON x.k = y.k"}) == ("a", "b"))
check("system catalog excluded (every login reads it)",
      sr.referenced_databases({"sql": "SELECT * FROM DBC.TablesV"}) == ())
check("a database-naming argument", sr.referenced_databases({"database_name": "Fitness_DB"}) == ("fitness_db",))
check("unqualified table → nothing (the call stands)", sr.referenced_databases({"sql": "SELECT * FROM Customers"}) == ())
check("no arguments", sr.referenced_databases(None) == ())
check("a write is a write", sr.is_write_call({"sql": "DELETE FROM fitness_db.Customers"}) is True)
check("a read is not", sr.is_write_call({"sql": "SELECT 1 FROM fitness_db.x"}) is False)
check("no statement is not a write", sr.is_write_call({"database_name": "x"}) is False)

print("[2] the refusal classifier")
check("3523 no SELECT access", sr.not_held_error(FIT_ERR))
check("3802 database does not exist", sr.not_held_error(NODB_ERR))
check("3807 is a missing TABLE, never a database the login cannot read (it poisoned the holdings)",
      not sr.not_held_error("[Error 3807] Object 'fitness_db.Nope' does not exist."))
check("PostgreSQL permission denied", sr.not_held_error("ERROR: permission denied for schema sales"))
check("a syntax error is not", not sr.not_held_error(SYNTAX_ERR))
check("a timeout is not", not sr.not_held_error("Tool 'base_readQuery' exceeded the platform's 120-second request ceiling"))
check("non-text", not sr.not_held_error(None))
check("the database is read from a 3523", sr.databases_in_error(FIT_ERR) == ("fitness_db",))
check("the database is read from a 3802", sr.databases_in_error(NODB_ERR) == ("nosuchdb",))

print("[3] holdings")
fresh()
check("unknown → None", sr.holds(U, "srvA", "fitness_db") is None)
sr.learn_list(U, "srvA", ["fitness_db", "DBC"])
check("listed → True", sr.holds(U, "srvA", "fitness_db") is True)
check("case-folded", sr.holds(U, "srvA", "dbc") is True)
check("not listed → False", sr.holds(U, "srvA", "nibrs_source_domain") is False)
check("another user knows nothing", sr.holds("user-2", "srvA", "fitness_db") is None)
sr.learn_outcome(U, "srvA", ["fitness_db"], held=False)
check("a proven refusal beats the list", sr.holds(U, "srvA", "fitness_db") is False)
sr.learn_outcome(U, "srvA", ["fitness_db"], held=True)
check("a later success reverses it", sr.holds(U, "srvA", "fitness_db") is True)
check("holds_all: one False → False", sr.holds_all(U, "srvA", ["fitness_db", "x"]) is False)
sr.learn_outcome(U, "srvZ", ["a"], held=True)
check("holds_all: one unknown → None", sr.holds_all(U, "srvZ", ["a", "b"]) is None)
check("holds_all: all True → True", sr.holds_all(U, "srvZ", ["a"]) is True)
sr._HOLDINGS[(U, "srvA")].ts = time.monotonic() - sr.HOLDINGS_TTL - 1
check("an expired list is forgotten (proven outcomes stay)",
      sr.known_list(U, "srvA") is None and sr.holds(U, "srvA", "fitness_db") is True)

print("[4] decide_before")
fresh()
for sid, dbs in HOLD.items():
    sr.learn_list(U, sid, dbs)
A = sr.Candidate("srvA", None, "base_readQuery", "fit")
B = sr.Candidate("srvB", "nibrs", "nibrs__base_readQuery", "nibrs")
C = sr.Candidate("srvC", "fit2", "fit2__base_readQuery", "fit2")
r = sr.decide_before(U, "srvB", ["fitness_db"], [A])
check("the called server lacks it, one other holds it → reroute", r.action == sr.REROUTE and r.to is A and r.why == "holds")
r = sr.decide_before(U, "srvA", ["fitness_db"], [B])
check("the called server holds it → stand", r.action == sr.STAND)
r = sr.decide_before(U, "srvB", ["fitness_db"], [A, C])
check("two others hold it → refuse, both named", r.action == sr.REFUSE and set(r.candidates) == {A, C})
r = sr.decide_before(U, "srvB", ["fitness_db"], [A], write=True)
check("a write never moves", r.action == sr.STAND)
r = sr.decide_before(U, "srvB", ["unknown_db"], [A])
check("nobody known to hold it → stand (the database answers)", r.action == sr.STAND)
r = sr.decide_before(U, "srvQ", ["fitness_db"], [A])
check("the called server's holdings unknown → stand", r.action == sr.STAND)
r = sr.decide_before(U, "srvB", [], [A])
check("no database named → stand", r.action == sr.STAND)
r = sr.decide_before(U, "srvB", ["fitness_db", "nibrs_source_domain"], [A])
check("a cross-server join nobody holds whole → refused as split (see [17])", r.action == sr.REFUSE and r.why == "split")

print("[5] decide_after")
fresh()
r = sr.decide_after(U, "srvB", FIT_ERR, ["fitness_db"], [A])
check("refused, nothing known, one other server → try it once", r.action == sr.REROUTE and r.to is A and r.why == "only_other")
check("…and the refusal is learned", sr.holds(U, "srvB", "fitness_db") is False)
r = sr.decide_after(U, "srvB", FIT_ERR, [], [A])
check("the database is taken from the error when the statement names none", r.action == sr.REROUTE and r.databases == ("fitness_db",))
r = sr.decide_after(U, "srvB", SYNTAX_ERR, ["fitness_db"], [A])
check("a syntax error → stand", r.action == sr.STAND)
r = sr.decide_after(U, "srvB", FIT_ERR, ["fitness_db"], [A], write=True)
check("a write → stand", r.action == sr.STAND)
fresh()
r = sr.decide_after(U, "srvB", FIT_ERR, ["fitness_db"], [A, C])
check("refused, two unknown others → refuse naming both", r.action == sr.REFUSE and len(r.candidates) == 2)
sr.learn_list(U, "srvC", HOLD["srvC"])
sr.learn_list(U, "srvA", HOLD["srvA"])
r = sr.decide_after(U, "srvB", FIT_ERR, ["fitness_db"], [A, C])
check("two known holders → refuse", r.action == sr.REFUSE)
fresh()
sr.learn_list(U, "srvA", {"x"})
r = sr.decide_after(U, "srvB", FIT_ERR, ["fitness_db"], [A])
check("never to a server known not to hold it", r.action == sr.STAND)
check("the note names both servers and the database",
      all(w in sr.reroute_note(sr.Route(sr.REROUTE, A, ("fitness_db",), (A,), "holds"), "nibrs", "nibrs__base_readQuery")
          for w in ("'fit'", "nibrs__base_readQuery", "fitness_db", "base_readQuery")))

print("[6] siblings")
with armed():
    got = sr.siblings("base_readQuery", "srvB")
    check("the other server's tool, under the name the agent sees", [c.tool_name for c in got] == ["base_readQuery"])
    got = sr.siblings("base_readQuery", "srvA")
    check("from the primary: the additional server's qualified tool", [c.tool_name for c in got] == ["nibrs__base_readQuery"])
    check("the lane's catalog narrows it", sr.siblings("base_readQuery", "srvA", available={"base_readQuery"}) == [])
    check("labels come from the turn's names", got[0].label == NAMES["srvB"])
sel = (AgentServer("srvA", None, ("base_readQuery",), ()), AgentServer("srvB", "nibrs", ("base_tableList",), ()))
with armed(servers=sel):
    check("a tool the server's selection excludes is never a target", sr.siblings("base_readQuery", "srvA") == [])
with armed():
    tok = aa.set_current_principal(aa.derive_principal(
        {"id": "p", "authorityConfig": {"autonomous": True, "denied_tools": ["nibrs__base_readQuery"]}}, U))
    try:
        check("Agent Authority denies the name → not a target", sr.siblings("base_readQuery", "srvA") == [])
        check("…while an allowed one stays", [c.tool_name for c in sr.siblings("base_readQuery", "srvB")] == ["base_readQuery"])
    finally:
        aa.reset_current_principal(tok)
    with patch.object(aa, "enforce_tool", side_effect=RuntimeError("boom")):
        check("authority unreadable → never a target (fail-closed)", sr.siblings("base_readQuery", "srvA") == [])

print("[7] off / single server")
check("nothing armed → inactive", not sr.active())
with armed(servers=SERVERS[:1]):
    check("one server armed → inactive", not sr.active())
with armed():
    check("two servers → active", sr.active())
    with patch.object(sr, "enabled", return_value=False):
        check("switch off → inactive", not sr.active())
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
check("the switch defaults on", getattr(APP_CONFIG, "MCP_SOURCE_ROUTING", None) is True)


# ---------------------------------------------------------------------------------------------
print("[8] Optimize lane — invoke_mcp_tool")


class FakeState(dict):
    pass


STATE = FakeState(mcp_tools={"base_readQuery": 1, "nibrs__base_readQuery": 1, "base_writeQuery": 1,
                             "nibrs__base_writeQuery": 1, "base_tableList": 1, "nibrs__base_tableList": 1})
CALLS = []


def _server_of(tool):
    ident = ti.parse(tool)
    return ti.turn_server_for(ident.alias) if ident.alias else "srvA"


async def fake_impl(STATE, command, user_uuid=None, session_id=None, call_id=None, workflow_state=None, profile_id=None):
    tool = command["tool_name"]
    sid = _server_of(tool)
    adapter._MCP_ROUTE.get()["server_id"] = sid
    CALLS.append((tool, sid, dict(command.get("arguments") or {})))
    dbs = sr.referenced_databases(command.get("arguments") or {})
    missing = [d for d in dbs if d not in HOLD[sid]]
    if missing:
        err = f"[Teradata Database] [Error 3523] [SQLState 42000] The user does not have SELECT access to {missing[0]}.T."
        return {"status": "error", "error": "An exception occurred", "data": err}, 0, 0
    return {"status": "success", "results": [{"n": 100}], "metadata": {"tool_name": ti.base_name(tool)}}, 0, 0


async def probe_known(user, sid):
    return sorted(HOLD.get(sid, ()))


async def probe_unknown(user, sid):
    return None


def invoke(tool, sql, probe=probe_known, servers=SERVERS):
    CALLS.clear()

    async def _go():
        with armed(servers=servers):
            return await adapter.invoke_mcp_tool(STATE, {"tool_name": tool, "arguments": {"sql": sql}}, U, "sess-1")
    with patch.object(adapter, "_invoke_mcp_tool_impl", side_effect=fake_impl), \
            patch.object(sr, "_probe", side_effect=probe):
        return asyncio.run(_go())


fresh()
res = invoke("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers")[0]
check("before: the fitness count goes to the fitness server (one call, never the wrong one)",
      [c[:2] for c in CALLS] == [("base_readQuery", "srvA")], CALLS)
check("…and succeeds", res.get("status") == "success")
check("the Server row names the route", (res.get("mcp_source") or {}).get("reason") == "routed"
      and res["mcp_source"]["server_id"] == "srvA" and res["mcp_source"]["called"] == "nibrs__base_readQuery"
      and res["mcp_source"]["databases"] == ["fitness_db"], res.get("mcp_source"))
check("the agent reads a routing note", "fitness_db" in (res.get("routing_note") or ""))
check("the routed stamp carries the answering server's alias (the primary: none)", res["mcp_source"].get("alias") is None)
check("the arguments are passed unchanged", CALLS[0][2] == {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"})

fresh()
res = invoke("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers", probe=probe_unknown)[0]
check("after: nothing known, refused → once on the other server",
      [c[:2] for c in CALLS] == [("nibrs__base_readQuery", "srvB"), ("base_readQuery", "srvA")], CALLS)
check("…the retry's answer is returned, named", res.get("status") == "success" and res["mcp_source"]["reason"] == "routed")
res = invoke("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers", probe=probe_unknown)[0]
check("next time the learned refusal routes before the call",
      [c[:2] for c in CALLS] == [("base_readQuery", "srvA")], CALLS)

fresh()
res = invoke("base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers")[0]
check("the right server → one call, no route named", [c[:2] for c in CALLS] == [("base_readQuery", "srvA")]
      and res["mcp_source"]["reason"] == "primary" and "routing_note" not in res, res.get("mcp_source"))

fresh()
res = invoke("nibrs__base_writeQuery", "DELETE FROM fitness_db.Customers")[0]
check("a write is never moved", [c[:2] for c in CALLS] == [("nibrs__base_writeQuery", "srvB")] and res["status"] == "error")

fresh()
res = invoke("nibrs__base_readQuery", "SELECT 1 FROM fitness_db.x JOIN nibrs_source_domain.v ON 1=1")[0]
check("a cross-server join is refused before it runs", CALLS == [] and "different MCP servers" in res.get("error", ""))

THREE = SERVERS + (AgentServer("srvC", "fit2", ("*",), ()),)
STATE["mcp_tools"]["fit2__base_readQuery"] = 1
fresh()
res = invoke("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers", servers=THREE)[0]
check("several holders → refused before running, both named, nothing sent",
      CALLS == [] and res["status"] == "error" and "base_readQuery" in res["error"] and "fit2__base_readQuery" in res["error"], res)

fresh()
CALLS.clear()


async def _single():
    return await adapter.invoke_mcp_tool(STATE, {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1 FROM nibrs_source_domain.v"}}, U, "s")
with patch.object(adapter, "_invoke_mcp_tool_impl", side_effect=fake_impl), \
        patch.object(sr, "_probe", side_effect=probe_known) as _p:
    _MCP_TOK = adapter._MCP_ROUTE.set({})
    try:
        res = asyncio.run(_single())
    finally:
        adapter._MCP_ROUTE.reset(_MCP_TOK)
check("a single-server agent: one call as sent, no probe, no stamp",
      [c[0] for c in CALLS] == ["base_readQuery"] and not _p.called and "mcp_source" not in res[0])

fresh()
with patch.object(sr, "enabled", return_value=False):
    res = invoke("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers")[0]
check("switch off: the call goes where the model sent it",
      [c[:2] for c in CALLS] == [("nibrs__base_readQuery", "srvB")] and res["status"] == "error")

fresh()
res = invoke("TDA_FinalReport", "SELECT 1 FROM fitness_db.x")
check("a TDA_ tool is never routed", [c[0] for c in CALLS] == ["TDA_FinalReport"])


# ---------------------------------------------------------------------------------------------
print("[9] ReAct lane — wrap_langchain_mcp_tools")


class FakeTool:
    def __init__(self, name, sid):
        self.name, self.metadata = name, {"uderia_mcp_server_id": sid}
        self.coroutine = self._run

    async def _run(self, **kwargs):
        CALLS.append((self.name, self.metadata["uderia_mcp_server_id"], dict(kwargs)))
        sid = self.metadata["uderia_mcp_server_id"]
        dbs = sr.referenced_databases(kwargs)
        missing = [d for d in dbs if d not in HOLD[sid]]
        if missing:
            raise Exception(f"[Error 3523] The user does not have SELECT access to {missing[0]}.T.")
        return json.dumps({"status": "success", "results": [{"n": 100}]}), None


GATES = []
_real_gate = te.sql_gate_before


def spy_gate(tool_name, arguments, session_id, lane, *a, **k):
    from trusted_data_agent.agent.preflight import dialect as _d
    GATES.append((tool_name, _d.source()))
    return _real_gate(tool_name, arguments, session_id, lane, *a, **k)


def react(tool, sql, probe=probe_known, servers=SERVERS, extra=()):
    CALLS.clear()
    GATES.clear()
    tools = [FakeTool("base_readQuery", "srvA"), FakeTool("nibrs__base_readQuery", "srvB")] + list(extra)
    te.wrap_langchain_mcp_tools(tools, "sess-2", user_uuid=U)
    by = {t.name: t for t in tools}

    async def _go():
        with armed(servers=servers):
            out = await by[tool].coroutine(sql=sql)
            return out, sr.take_reroute(tool)
    with patch.object(sr, "_probe", side_effect=probe), patch.object(te, "sql_gate_before", side_effect=spy_gate):
        return asyncio.run(_go())


fresh()
(out, _), info = react("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers")
check("before: runs as the fitness server's tool only", [c[:2] for c in CALLS] == [("base_readQuery", "srvA")], CALLS)
check("the model reads the route first", str(out).startswith("[Routing]") and "fitness_db" in str(out))
check("the tool-end event can name the server that answered",
      info and info["server_id"] == "srvA" and info["to"] == "base_readQuery" and info["databases"] == ["fitness_db"], info)
check("the SQL gate ran as the tool that really ran, in the primary's (session) scope",
      GATES == [("base_readQuery", "")], GATES)

fresh()
(out, _), info = react("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers", probe=probe_unknown)
check("after: refused → once on the other server",
      [c[:2] for c in CALLS] == [("nibrs__base_readQuery", "srvB"), ("base_readQuery", "srvA")], CALLS)
check("…answered and named", "[Routing]" in str(out) and '"success"' in str(out) and info and info["server_id"] == "srvA")
check("each attempt gated in its own server's scope",
      GATES == [("nibrs__base_readQuery", "mcp:srvB"), ("base_readQuery", "")], GATES)

fresh()
(out, _), info = react("base_readQuery", "SELECT COUNT(*) FROM nosuch.x", probe=probe_unknown)
check("both servers refuse → the first error stands, nothing recorded",
      str(out).startswith("Error from base_readQuery") and info is None and len(CALLS) == 2, (out, CALLS))

fresh()
(out, _), info = react("base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers")
check("the right server → one call, nothing recorded", [c[:2] for c in CALLS] == [("base_readQuery", "srvA")] and info is None)

fresh()
(out, _), info = react("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers",
                       servers=THREE, extra=[FakeTool("fit2__base_readQuery", "srvC")])
check("several holders → asked, nothing sent", CALLS == [] and "fit2__base_readQuery" in str(out))

fresh()
(out, _), info = react("nibrs__base_readQuery", "DELETE FROM fitness_db.Customers")
check("a write runs as called", [c[0] for c in CALLS] == ["nibrs__base_readQuery"] or CALLS == [])

fresh()
CALLS.clear()
tools = [FakeTool("base_readQuery", "srvA")]
te.wrap_langchain_mcp_tools(tools, "s", user_uuid=U)
with patch.object(sr, "_probe", side_effect=probe_known) as _p:
    out = asyncio.run(tools[0].coroutine(sql="SELECT 1 FROM nibrs_source_domain.v"))
check("single-server ReAct: runs as called, no probe", [c[0] for c in CALLS] == ["base_readQuery"] and not _p.called)


# ---------------------------------------------------------------------------------------------
print("[10] learning and the probe")
fresh()
with armed():
    sr._LIST_TOOL["srvB"] = ("base_databaseList", {"scope": "all"})
    sr.learn_from_result("nibrs__base_databaseList", {"scope": "all"},
                         {"status": "success", "results": [{"DatabaseName": "NIBRS_Source_Domain"}]}, "mcp:srvB")
    check("a turn's own database list (same tool, same arguments) is learned", sr.known_list(U, "srvB") == {"nibrs_source_domain"})
    sr.learn_from_result("nibrs__base_databaseList", {"scope": "user"},
                         {"status": "success", "results": [{"DatabaseName": "only_mine"}]}, "mcp:srvB")
    check("…a list asked differently is not (it may be partial)", sr.known_list(U, "srvB") == {"nibrs_source_domain"})
    sr.learn_from_result("base_readQuery", {"sql": "SELECT 1 FROM fitness_db.x"}, {"status": "success"}, "mcp:srvA")
    check("a successful read proves the server holds it", sr.holds(U, "srvA", "fitness_db") is True)
    sr.learn_from_result("base_readQuery", {"sql": "SELECT 1 FROM other_db.x"}, {"status": "error"}, "mcp:srvA")
    check("an error proves nothing here", sr.holds(U, "srvA", "other_db") is None)
fresh()
sr.learn_from_result("base_readQuery", {"sql": "SELECT 1 FROM fitness_db.x"}, {"status": "success"}, "mcp:srvA")
check("outside a routing turn nothing is learned", sr.holds("", "srvA", "fitness_db") is None)


class _T:
    def __init__(self, name, desc, props):
        self.name, self.description, self.inputSchema = name, desc, {"properties": {p: {} for p in props}}


class _Content:
    def __init__(self, text):
        self.text = text


class _Res:
    def __init__(self, text, err=False):
        self.content, self.isError = [_Content(text)], err


class _Sess:
    calls = []

    async def list_tools(self):
        class L:
            tools = [_T("base_readQuery", "Execute a read-only SQL query", ["sql"]),
                     _T("base_databaseList", "Lists all databases in the Teradata System.", ["scope"]),
                     _T("base_tableList", "Lists all tables in a database.", ["database_name"])]
        return L()

    async def call_tool(self, name, args):
        _Sess.calls.append((name, args))
        return _Res(json.dumps({"status": "success", "results": [{"DatabaseName": "fitness_db", "DBType": "DataBase"},
                                                                   {"DatabaseName": "DBC", "DBType": "User"},
                                                                   {"DatabaseName": "fitness_manager", "DBType": "User"}]}))


class _Client:
    def session(self, sid):
        class Ctx:
            async def __aenter__(s):
                return _Sess()

            async def __aexit__(s, *a):
                return False
        return Ctx()


fresh()
_Sess.calls = []
with patch("trusted_data_agent.core.configuration_service.pooled_mcp_client", return_value=_Client()):
    asyncio.run(sr.ensure_lists(U, ["srvA"]))
    check("the probe finds the list tool by role and asks for every database",
          _Sess.calls == [("base_databaseList", {"scope": "all"})], _Sess.calls)
    check("…and records which entries are user spaces", sr.user_spaces(U, "srvA") == {"dbc", "fitness_manager"})
    check("…and learns the list", sr.known_list(U, "srvA") == {"fitness_db", "dbc", "fitness_manager"})
    asyncio.run(sr.ensure_lists(U, ["srvA"]))
    check("a known list is not probed again", len(_Sess.calls) == 1)
fresh()
with patch("trusted_data_agent.core.configuration_service.pooled_mcp_client", side_effect=ValueError("gone")):
    asyncio.run(sr.ensure_lists(U, ["srvA"]))
    check("an unreachable server stays unknown, never raises", sr.known_list(U, "srvA") is None)
    t0 = sr._HOLDINGS[(U, "srvA")].probe_failed_at
    asyncio.run(sr.ensure_lists(U, ["srvA"]))
    check("…and is not re-probed at once", sr._HOLDINGS[(U, "srvA")].probe_failed_at == t0)


# ---------------------------------------------------------------------------------------------
print("[11] wiring")
es = open(os.path.join(ROOT, "src/trusted_data_agent/agent/execution_service.py")).read()
check("the seam arms the routing table beside the server map", "_srt.set_turn(user_uuid, _servers_of(active_profile))" in es
      and es.find("_mcp_routing_token = _srt.set_turn") > es.find("_mcp_servers_token = _set_turn_servers("))
check("…only for a multi-server agent", "if _mcp_servers_token is not None:\n            try:\n                from trusted_data_agent.agent import source_routing as _srt" in es)
check("…and releases it", "_srt.reset_turn(_mcp_routing_token)" in es)
check("…the token exists before the try", es.find("_mcp_routing_token = None   # ...and the routing table") < es.find("_srt.set_turn("))
ca = open(os.path.join(ROOT, "src/trusted_data_agent/agent/conversation_agent.py")).read()
check("the ReAct tool-end event reads the route", "self._with_source(_completed, tool_name, completed=True)" in ca)
js = r"""
import('./static/js/ui.js').then(m => {
  const html = m.mcpSourceKv({server_id: 'srvA', server: 'Fitness', reason: 'routed', called: 'nibrs__base_readQuery',
                              from_server: 'NIBRS', databases: ['fitness_db']});
  console.log(JSON.stringify(html));
}).catch(e => { console.log(JSON.stringify('ERR ' + e.message)); });
"""
try:
    out = subprocess.run(["node", "--input-type=module", "-e", js], cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
    html = json.loads(out.splitlines()[-1]) if out else ""
    ok = "routed here by Uderia" in html and "fitness_db" in html and "nibrs__base_readQuery" in html and "NIBRS" in html
    if html.startswith("ERR"):   # ui.js needs a browser; check the source instead
        src = open(os.path.join(ROOT, "static/js/ui.js")).read()
        ok = "src.reason === 'routed'" in src and "routed here by Uderia" in src
    check("the Server row says Uderia routed it, to what, from what", ok, html[:200])
except Exception as e:  # noqa: BLE001
    check("the Server row says Uderia routed it", False, e)

# ---------------------------------------------------------------------------------------------
print("[12] a server that cannot be reached (B9)")
import httpx  # noqa: E402
with armed():
    ev = sr.unavailable_event("srvB", "nibrs", "unhandled errors in a TaskGroup")
    check("the step names the server and why", ev["type"] == "mcp_server_unavailable"
          and ev["details"]["server"] == NAMES["srvB"] and "could not be reached" in ev["details"]["summary"])
    note = sr.unavailable_note([ev["details"]])
    check("the model is told a missing source is not missing data",
          NAMES["srvB"] in note and "nibrs__" in note and "never answer as if the data did not exist" in note)
    check("no failure → no note", sr.unavailable_note([]) is None)
    check("the call-time text says: do not retry, answer from the others",
          "could not be reached" in sr.unreachable_text("srvB", "nibrs__base_readQuery")
          and "Do not retry" in sr.unreachable_text("srvB", "nibrs__base_readQuery"))
fresh()
r = sr.decide_after(U, "srvB", "", ["fitness_db"], [A], unreachable=True)
check("unreachable, nothing known → stand (never guessed)", r.action == sr.STAND)
sr.learn_list(U, "srvA", HOLD["srvA"])
r = sr.decide_after(U, "srvB", "", ["fitness_db"], [A], unreachable=True)
check("unreachable, one known holder → there", r.action == sr.REROUTE and r.to is A and r.why == "unreachable")
r = sr.decide_after(U, "srvB", "", ["fitness_db"], [A], unreachable=True, write=True)
check("unreachable write → stand", r.action == sr.STAND)
check("the note says the first server could not be reached",
      "could not be reached" in sr.reroute_note(sr.Route(sr.REROUTE, A, ("fitness_db",), (A,), "unreachable"), "nibrs", "x"))

DOWN = set()


async def fake_impl_down(STATE, command, user_uuid=None, session_id=None, call_id=None, workflow_state=None, profile_id=None):
    tool = command["tool_name"]
    sid = _server_of(tool)
    if sid in DOWN:
        adapter._MCP_ROUTE.get().update(server_id=sid, unreachable=True)
        CALLS.append((tool, sid, {}))
        return {"status": "error", "error": "An exception occurred while invoking tool.", "data": "ConnectError"}, 0, 0
    return await fake_impl(STATE, command, user_uuid, session_id, call_id, workflow_state, profile_id)


def invoke_down(tool, sql, probe=probe_known, servers=SERVERS):
    CALLS.clear()

    async def _go():
        with armed(servers=servers):
            return await adapter.invoke_mcp_tool(STATE, {"tool_name": tool, "arguments": {"sql": sql}}, U, "sess-3")
    with patch.object(adapter, "_invoke_mcp_tool_impl", side_effect=fake_impl_down), \
            patch.object(sr, "_probe", side_effect=probe):
        return asyncio.run(_go())


fresh()
DOWN.clear(); DOWN.add("srvB")
res = invoke_down("nibrs__base_readQuery", "SELECT COUNT(*) FROM nibrs_source_domain.victim", probe=probe_unknown)[0]
check("Optimize: a server that did not answer is named, recovery-shaped",
      res["status"] == "error" and "could not be reached" in res["error"] and "Do not retry" in res["error"], res)
check("…the transport detail stays in data", res.get("data") == "ConnectError")
DOWN.clear()


class DownTool(FakeTool):
    async def _run(self, **kwargs):
        CALLS.append((self.name, self.metadata["uderia_mcp_server_id"], dict(kwargs)))
        raise httpx.ConnectError("connection refused")


fresh()
CALLS.clear()
tools = [FakeTool("base_readQuery", "srvA"), DownTool("nibrs__base_readQuery", "srvB")]
te.wrap_langchain_mcp_tools(tools, "sess-4", user_uuid=U)


async def _go_down(sql):
    with armed():
        return await tools[1].coroutine(sql=sql), sr.take_reroute("nibrs__base_readQuery")
with patch.object(sr, "_probe", side_effect=probe_unknown):
    (out, _), info = asyncio.run(_go_down("SELECT COUNT(*) FROM nibrs_source_domain.victim"))
check("ReAct: a server that did not answer is named first in the result",
      str(out).startswith("Server '") and "could not be reached" in str(out) and info is None, out)
fresh()
sr.learn_list(U, "srvA", HOLD["srvA"])
CALLS.clear()
with patch.object(sr, "_probe", side_effect=probe_unknown):
    (out, _), info = asyncio.run(_go_down("SELECT COUNT(*) FROM fitness_db.Customers"))
check("ReAct: down + a known holder → answered there, named",
      "[Routing]" in str(out) and info and info["server_id"] == "srvA"
      and [c[:2] for c in CALLS][-1] == ("base_readQuery", "srvA"), (out, CALLS))

from trusted_data_agent.api import rest_routes as rr  # noqa: E402
tr = {}
rr._additional_mcp_test({"additional_mcp": {"srvB": {"alias": "andp", "source": "error", "tools": 0, "error": "TaskGroup"}}}, tr)
check("activation: an unreachable additional server is a WARNING, named",
      tr.get("additional_mcp_servers", {}).get("status") == "warning" and "andp: could not be reached" in tr["additional_mcp_servers"]["message"])
tr = {}
sw = {"additional_mcp": {"srvB": {"alias": "nibrs", "source": "snapshot", "tools": 48}}}
rr._additional_mcp_test(sw, tr)
check("activation: reached → success, the tool count named; the raw report is not echoed",
      tr["additional_mcp_servers"]["status"] == "success" and "nibrs: 48 tools" in tr["additional_mcp_servers"]["message"]
      and "additional_mcp" not in sw)
tr = {}
rr._additional_mcp_test({"status": "success"}, tr)
check("activation: a single-server agent gets no entry", tr == {})

ce = open(os.path.join(ROOT, "src/trusted_data_agent/agent/engines/conversation_engine.py")).read()
# (stage 1: the step comes from the ONE per-turn record both lanes read — behaviour asserted in
# test_multi_source_lanes [5]; here only that the ReAct engine feeds it and emits from it)
check("ReAct engine: a server that fails to load is a Live Status step", "_srt.mark_unavailable(_srv.server_id, _xe)" in ce
      and "_srt.take_unavailable_events()" in ce and "yield executor._format_sse_with_depth(_uev)" in ce)
check("…persisted for reload", 'system_events.extend(getattr(executor, "_conv_source_events", []) or [])' in ce)
check("…and told to the model", "source_note=_source_note" in ce and "_srt.turn_sources_note()" in ce)
check("the agent puts the note on the user side of the turn",
      'effective_query = f"{self.source_note}\\n\\n{effective_query}"' in ca)
eh = open(os.path.join(ROOT, "static/js/eventHandlers.js")).read()
check("the step has a title on reload", "if (eventType === 'mcp_server_unavailable') return 'MCP Server Unavailable';" in eh)
cs = open(os.path.join(ROOT, "src/trusted_data_agent/core/configuration_service.py")).read()
check("the profile switch returns the per-server report only when there is one",
      'if _extra_report:   # only an agent with several MCP servers\n            _ok["additional_mcp"] = _extra_report' in cs)


# ---------------------------------------------------------------------------------------------
print("[13] the model is told which server holds what")
fresh()
check("nothing armed → no note", sr.servers_note() is None)
with armed(servers=SERVERS[:1]):
    check("a single-server agent → no note", sr.servers_note() is None)
with armed():
    n = sr.servers_note()
    check("lists unknown → said so, per server", n and n.count("databases not known yet") == 2)
    sr.learn_list(U, "srvA", HOLD["srvA"])
    sr.learn_list(U, "srvB", HOLD["srvB"])
    n = sr.servers_note()
    check("the primary's tools are the unprefixed ones", "tools without a prefix reach server 'Teradata MCP - fitness_manager'" in n, n)
    check("the additional server's are named by alias", "tools named nibrs__… reach server 'Teradata MCP - nibrs_source_agent'" in n)
    check("the databases each login reads, system catalogs left out", "fitness_db" in n and "nibrs_source_domain" in n and "dbc" not in n)
    check("the re-route is announced, so a note never contradicts a step", "re-routed" in n)
    # changed in multi-source stage 1 (live evidence): with the other server down the planner,
    # told nothing, invented a database — the reachable server is still listed
    _one = sr.servers_note(skip=["srvB"]) or ""
    check("a server skipped (unreachable) still leaves the reachable one listed",
          "reach server 'Teradata MCP - fitness_manager'" in _one and "nibrs" not in _one, _one)
    sr.learn_list(U, "srvB", HOLD["srvB"], users=["nibrs_source_agent"])
    n = sr.servers_note()
    check("a login's own user space is left out of the note (the model once queried it for data)",
          "nibrs_source_agent'" in n and "nibrs_source_agent," not in n
          and not n.rstrip().endswith("nibrs_source_agent") and "nibrs_source_domain" in n, n)
    check("…but routing still counts it as held", sr.holds(U, "srvB", "nibrs_source_agent") is True)
    sr.learn_list(U, "srvB", ["nibrs_source_agent", "dbc"], users=["nibrs_source_agent", "dbc"])
    check("…unless the user space is all the login reads (never claim 'none')",
          "databases: nibrs_source_agent" in sr.servers_note())
    sr.learn_list(U, "srvB", ["nibrs_source_domain", "sqlj", "sysjdbc", "td_sysxml", "dbc"])
    n = sr.servers_note()
    check("Teradata's utility databases are left out of the note", "sqlj" not in n and "td_sysxml" not in n
          and "databases: nibrs_source_domain" in n, n)
    check("…but routing still counts them", sr.holds(U, "srvB", "sqlj") is True)
    sr.learn_list(U, "srvA", [f"db{i:02d}" for i in range(20)])
    check("a long list is capped and counted", "(+8 more)" in sr.servers_note())
    with patch.object(sr, "enabled", return_value=False):
        check("routing off → no note", sr.servers_note() is None)

sys.path.insert(0, ROOT)
from components.builtin.context_window.module_registry import get_module_registry, reset_module_registry  # noqa: E402
fresh()
reset_module_registry()
m = get_module_registry().get_handler("tool_definitions")
check("Optimize tool context: unchanged without a multi-server turn", m._with_servers_note("TOOLS") == "TOOLS")
with armed():
    sr.learn_list(U, "srvA", HOLD["srvA"]); sr.learn_list(U, "srvB", HOLD["srvB"])
    out = m._with_servers_note("TOOLS")
    check("Optimize tool context: the note leads, the tools follow", out.startswith("[Data sources]") and out.endswith("TOOLS"))
    check("…never on empty content", m._with_servers_note("") == "")
th = open(os.path.join(ROOT, "components/builtin/context_window/modules/tool_definitions/handler.py")).read()
check("…in full and in names-only mode", th.count("_with_servers_note(") >= 3)
check("the seam warms each server's list once per turn, only for a routing turn",
      "await _srt.ensure_lists(user_uuid, [s.server_id for s in _srt.turn_servers()])" in es
      if (es := open(os.path.join(ROOT, "src/trusted_data_agent/agent/execution_service.py")).read()) else False)
with armed():   # the one note skips an unavailable server and says it is unavailable (both lanes)
    sr.learn_list(U, "srvA", HOLD["srvA"]); sr.learn_list(U, "srvB", HOLD["srvB"])
    sr.mark_unavailable("srvB", "connection refused")
    _n = sr.turn_sources_note() or ""
check("the ReAct note carries the servers note beside an unavailable one",
      "could not be reached" in _n and "reach server 'Teradata MCP - nibrs_source_agent'" not in _n, _n)
check("…and with ONE server left reachable, the model is still told what that server holds",
      "reach server 'Teradata MCP - fitness_manager'" in _n and "fitness_db" in _n, _n)


# ---------------------------------------------------------------------------------------------
print("[14] a learned plan naming a server the agent no longer has (O8)")
from trusted_data_agent.agent.planner import Planner  # noqa: E402
rk = Planner._rekey_unknown_server_tool
CAT = {"base_readQuery": 1, "nibrs__base_readQuery": 1, "nibrs__base_tableList": 1}
with armed():
    check("an unarmed alias → the first server's plain tool", rk("crm__base_readQuery", CAT) == "base_readQuery")
    check("…or the one tool of that base name on another server", rk("crm__base_tableList", CAT) == "nibrs__base_tableList")
    check("an armed alias is never touched", rk("nibrs__base_readQuery", CAT) is None)
    check("a name the catalog has is never touched", rk("base_readQuery", CAT) is None)
    check("no tool of that base name → stands", rk("crm__base_writeQuery", CAT) is None)
    check("two tools of that base name → stands (never guessed)",
          rk("crm__x", {"a__x": 1, "b__x": 1}) is None)
    check("not alias-shaped → stands", rk("Base__readQuery", CAT) is None and rk("readQuery", CAT) is None)
pl = open(os.path.join(ROOT, "src/trusted_data_agent/agent/planner.py")).read()
check("the plan validator applies it before the argument checks",
      pl.find("_rekey_unknown_server_tool(phase['relevant_tools'][0], all_tools)") <
      pl.find("# Correction 4: Remove hallucinated/extraneous arguments"))
check("…and says what it did", 'correction_type == "unknown_server_tool"' in pl)


# ---------------------------------------------------------------------------------------------
print("[15] a dashboard tile's MCP replay runs on the tile's profile's servers (M11)")
PROFILE = {"id": "p1", "mcpServerId": "srvA", "tools": ["*"],
           "additionalMcpServers": [{"id": "srvB", "alias": "nibrs", "tools": ["*"], "prompts": []}]}
seen = {}


async def fake_impl_scope(STATE, command, user_uuid=None, session_id=None, call_id=None, workflow_state=None, profile_id=None):
    tool = command["tool_name"]
    ident = ti.parse(tool)
    sid = ti.turn_server_for(ident.alias) if ident.alias else STATE["mcp_server_id"]
    adapter._MCP_ROUTE.get()["server_id"] = sid
    seen.setdefault("calls", []).append((tool, sid, STATE.get("mcp_client")))
    return await fake_impl(STATE, command, user_uuid, session_id, call_id, workflow_state, profile_id)


def replay(tool, sql, profile=PROFILE):
    seen.clear()
    CALLS.clear()

    async def _go():
        with sr.profile_call_scope(profile, U, {"mcp_tools": {"only": 1}, "mcp_client": "GLOBAL"}) as st:
            return await adapter.invoke_mcp_tool(st, {"tool_name": tool, "arguments": {"sql": sql}}, U, None)
    with patch("trusted_data_agent.core.configuration_service.pooled_mcp_client", side_effect=lambda sid, u: f"CLIENT:{sid}"), \
            patch("trusted_data_agent.core.config_manager.get_config_manager") as gcm, \
            patch.object(adapter, "_invoke_mcp_tool_impl", side_effect=fake_impl_scope), \
            patch.object(sr, "_probe", side_effect=probe_known):
        gcm.return_value.get_mcp_servers.return_value = [{"id": "srvA", "name": "Fitness"}, {"id": "srvB", "name": "NIBRS"}]
        return asyncio.run(_go())


fresh()
res = replay("base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers")[0]
check("a plain name runs on the profile's first server with its own client, never the global one",
      seen["calls"] == [("base_readQuery", "srvA", "CLIENT:srvA")], seen)
fresh()
res = replay("nibrs__base_readQuery", "SELECT COUNT(*) FROM nibrs_source_domain.victim")[0]
check("a qualified name reaches its server outside a turn", [c[:2] for c in seen["calls"]] == [("nibrs__base_readQuery", "srvB")], seen)
fresh()
res = replay("nibrs__base_readQuery", "SELECT COUNT(*) FROM fitness_db.Customers")[0]
check("a tile stamped from a wrong-server call is re-routed on refresh (catalog judged by the profile's selection)",
      [c[:2] for c in seen["calls"]] == [("base_readQuery", "srvA")] and res["status"] == "success", seen)
check("…and the scope is released afterwards", ti.turn_aliases() == () and not sr.active())
fresh()
st0 = {"x": 1}
with sr.profile_call_scope({"id": "p2"}, U, st0) as st:
    check("a profile without an MCP server: STATE unchanged, nothing armed", st is st0 and not sr.active())
single = {"id": "p3", "mcpServerId": "srvA", "tools": ["*"]}
with patch("trusted_data_agent.core.configuration_service.pooled_mcp_client", side_effect=lambda sid, u: f"CLIENT:{sid}"):
    with sr.profile_call_scope(single, U, {}) as st:
        check("a single-server profile: its server as the call's server, no aliases, no routing",
              st["mcp_server_id"] == "srvA" and ti.turn_aliases() == () and not sr.active())
dr = open(os.path.join(ROOT, "src/trusted_data_agent/api/dashboard_routes.py")).read()
check("the MCP refresh route uses the tile's profile scope", "with profile_call_scope(_tile_profile, user_uuid, APP_STATE) as _state:" in dr)


# ---------------------------------------------------------------------------------------------
print("[16] SQL consolidation never merges statements for different servers")
fresh()
F = {"sql": "SELECT COUNT(*) FROM fitness_db.Customers"}
N = {"sql": "SELECT COUNT(*) FROM nibrs_source_domain.Victim"}
check("nothing armed → never spans", not sr.spans_servers(["base_readQuery", "nibrs__base_readQuery"], [F, N]))
with armed():
    check("different aliases → spans", sr.spans_servers(["base_readQuery", "nibrs__base_readQuery"], [F, N]))
    check("same tool, lists unknown → does not block", not sr.spans_servers(["base_readQuery", "base_readQuery"], [F, N]))
    sr.learn_list(U, "srvA", HOLD["srvA"]); sr.learn_list(U, "srvB", HOLD["srvB"])
    check("same tool, databases held by different servers → spans",
          sr.spans_servers(["base_readQuery", "base_readQuery"], [F, N]))
    check("both on one server → may merge",
          not sr.spans_servers(["base_readQuery", "base_readQuery"], [F, {"sql": "SELECT 1 FROM fitness_db.Sales"}]))
    check("no databases named → may merge", not sr.spans_servers(["base_readQuery", "base_readQuery"], [{"sql": "SELECT 1"}, F]))
    with patch.object(sr, "enabled", return_value=False):
        check("routing off → never spans", not sr.spans_servers(["base_readQuery", "nibrs__base_readQuery"], [F, N]))
# Driven through the REAL consolidation pass: the LLM is asked only when the statements share a server.
from trusted_data_agent.agent import planner as _planner_mod


class _ConsolExec:
    def __init__(self, meta_plan):
        self.meta_plan, self.original_user_input, self.source = meta_plan, "counts", "text"
        self.user_uuid, self.session_id = U, "s"
        self.turn_input_tokens = self.turn_output_tokens = 0
        self._last_call_metadata = {"cost_usd": 0}
        self.llm_calls = 0
        self.prompt_resolver = types.SimpleNamespace(
            get_sql_consolidation_prompt=lambda: "Goal: {user_goal}\nQueries:\n{inefficient_queries}")

    async def _call_llm_and_update_tokens(self, *a, **k):
        self.llm_calls += 1
        return '{"consolidated_query": "SELECT 1"}', 1, 1

    def _log_system_event(self, e):
        pass

    def _format_sse_with_depth(self, *a, **k):
        return {}


async def _drain_pass(pl_obj):
    async for _ in pl_obj._rewrite_plan_for_sql_consolidation():
        pass


async def _no_session(*a, **k):
    return {"input_tokens": 0, "output_tokens": 0}


def _consolidate(tools, args_list):
    ex = _ConsolExec([{"phase": i + 1, "relevant_tools": [t], "arguments": a}
                      for i, (t, a) in enumerate(zip(tools, args_list))])
    with patch.object(_planner_mod.session_manager, "get_session", _no_session):
        asyncio.run(_drain_pass(_planner_mod.Planner(ex)))
    return ex


with armed():
    sr.learn_list(U, "srvA", HOLD["srvA"]); sr.learn_list(U, "srvB", HOLD["srvB"])
    ex = _consolidate(["base_readQuery", "nibrs__base_readQuery"], [F, N])
    check("the real pass: two servers' statements stay two phases, no LLM call",
          ex.llm_calls == 0 and len(ex.meta_plan) == 2, (ex.llm_calls, len(ex.meta_plan)))
    ex = _consolidate(["base_readQuery", "base_readQuery"],
                      [F, {"sql": "SELECT COUNT(*) FROM fitness_db.Sales"}])
    check("the real pass: one server's statements are still consolidated",
          ex.llm_calls == 1 and len(ex.meta_plan) == 1, (ex.llm_calls, len(ex.meta_plan)))
pl = open(os.path.join(ROOT, "src/trusted_data_agent/agent/planner.py")).read()
check("the consolidation pass asks before its LLM call",
      pl.find("_srt.spans_servers(") < pl.find("consolidation_prompt_content = self.executor.prompt_resolver.get_sql_consolidation_prompt()")
      and pl.find("_srt.spans_servers(") > pl.find("async def _rewrite_plan_for_sql_consolidation"))


# ---------------------------------------------------------------------------------------------
print("[17] one statement over two servers is refused before it runs")
fresh()
for sid, dbs in HOLD.items():
    sr.learn_list(U, sid, dbs)
with armed():
    r = sr.decide_before(U, "srvB", ["fitness_db", "nibrs_source_domain"], [A])
    check("databases on different servers → refused, split", r.action == sr.REFUSE and r.why == "split", r)
    t = sr.refusal_text(r, "nibrs", "nibrs__base_readQuery")
    check("…naming where each database is and what to do",
          "fitness_db is on server 'fit' (base_readQuery)" in t and "nibrs_source_domain is on server" in t
          and "Query each server with its own tool" in t, t)
    check("one of them unknown → stands", sr.decide_before(U, "srvB", ["fitness_db", "mystery"], [A]).action == sr.STAND)
res = invoke("nibrs__base_readQuery", "SELECT (SELECT COUNT(*) FROM fitness_db.Customers), (SELECT COUNT(*) FROM nibrs_source_domain.Victim)")[0]
check("Optimize: nothing sent, the split named", CALLS == [] and res["status"] == "error" and "different MCP servers" in res["error"], res)
fresh()
(out, _), info = react("base_readQuery", "SELECT (SELECT COUNT(*) FROM fitness_db.Customers), (SELECT COUNT(*) FROM nibrs_source_domain.Victim)")
check("ReAct: nothing sent, the split named", CALLS == [] and "different MCP servers" in str(out), out)


# ---------------------------------------------------------------------------------------------
print("[18] an @TAG / REST override agent gets its additional servers' tools")
from trusted_data_agent.agent.engines import optimize_engine as oe  # noqa: E402
_ex = types.SimpleNamespace(user_uuid=U, profile_override_id="p-ms")
_multi = {"id": "p-ms", "mcpServerId": "srvA",
          "additionalMcpServers": [{"id": "srvB", "alias": "nibrs", "tools": ["*"], "prompts": []}]}
_seen = []


async def _fake_load(state, user, pid, *, client_for=None):
    _seen.append((user, pid, client_for is not None))
    state["mcp_tools"]["nibrs__base_readQuery"] = "T"
    state["structured_tools"]["nibrs__Base"] = [{"name": "nibrs__base_readQuery"}]
    return {"srvB": {"alias": "nibrs", "tools": 1}}


tools, prompts, stools, sprompts = {"base_readQuery": "T0"}, {}, {"Base": []}, {}
with patch("trusted_data_agent.mcp_adapter.adapter.load_additional_mcp_resources", _fake_load):
    rep = asyncio.run(oe.merge_override_additional_servers(_ex, _multi, None, tools, prompts, stools, sprompts))
check("the override's own catalog dicts gain the qualified tools (merged in place)",
      "nibrs__base_readQuery" in tools and "base_readQuery" in tools and "nibrs__Base" in stools, tools)
check("…for the override user and profile, with a per-server client", _seen == [(U, "p-ms", True)], _seen)
check("…and the report is returned", rep == {"srvB": {"alias": "nibrs", "tools": 1}}, rep)
_seen.clear()
with patch("trusted_data_agent.mcp_adapter.adapter.load_additional_mcp_resources", _fake_load):
    rep = asyncio.run(oe.merge_override_additional_servers(
        _ex, {"id": "p1", "mcpServerId": "srvA"}, None, {}, {}, {}, {}))
check("a single-server override agent: no load at all", _seen == [] and rep == {}, _seen)


async def _boom(*a, **k):
    raise RuntimeError("down")

with patch("trusted_data_agent.mcp_adapter.adapter.load_additional_mcp_resources", _boom):
    rep = asyncio.run(oe.merge_override_additional_servers(_ex, _multi, None, {}, {}, {}, {}))
check("a failing load never stops the override", rep == {})
oe_src = open(os.path.join(ROOT, "src/trusted_data_agent/agent/engines/optimize_engine.py")).read()
check("the override path merges before installing its overlay",
      0 < oe_src.find("await merge_override_additional_servers(")
      < oe_src.find("apply_override_state_overlay(", oe_src.find("class OptimizeEngine")))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
