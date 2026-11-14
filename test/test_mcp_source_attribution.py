"""PROP-006 phase 2 — every MCP tool step of a multi-server agent names the server it went to.

The fact is stamped on the persisted call (the Optimize lane's result `mcp_source`, the ReAct
lane's event `source`), so Live Status shows it live AND after a reload without re-deriving it.
A single-server agent arms nothing and its turns stay byte-identical.

    [1] call_source — the one rule: qualified → its alias's server, plain → the first server,
        nothing armed → None, an unroutable name → None (never a guess)
    [2] the adapter stamp — every branch: single-server, TDA_, connector, routed, refused before
        routing (plain name owned by a connector / not), non-dict result, a failing lookup
    [3] invoke_mcp_tool carries the route from the implementation to the stamp
    [4] the ReAct lane — the tool's own server metadata, the three events carry `source`
    [5] the execution seam arms the names only beside the alias map, and releases them

Run: python test/test_mcp_source_attribution.py
"""
import ast
import asyncio
import os
import sys
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.core import agent_mcp_servers as ams  # noqa: E402
from trusted_data_agent.core import tool_identity as ti  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail != "" else ""))


class armed:
    """Arm a turn: alias map + names (None names = arm the aliases only)."""
    def __init__(self, servers, primary="srvA", names=None):
        self.servers, self.primary, self.names = servers, primary, names
    def __enter__(self):
        self.t1 = ti.set_turn_servers(self.servers)
        self.t2 = ams.set_turn_source_labels(self.primary, self.names) if self.names is not None else None
        return self
    def __exit__(self, *a):
        if self.t2 is not None:
            ams.reset_turn_source_labels(self.t2)
        ti.reset_turn_servers(self.t1)


NAMES = {"srvA": "Warehouse", "srvB": "CRM"}

print("[1] call_source — the one rule")
check("nothing armed (a single-server agent) → None", ams.call_source("base_readQuery") is None)
check("nothing armed, a qualified-looking name → None", ams.call_source("crm__base_readQuery") is None)
with armed({"crm": "srvB"}, names=NAMES):
    q = ams.call_source("crm__base_readQuery")
    check("a qualified name → its alias's server, by name", q == {"server_id": "srvB", "server": "CRM", "alias": "crm", "reason": "qualified"}, q)
    p = ams.call_source("base_readQuery")
    check("a plain name → the first server", p == {"server_id": "srvA", "server": "Warehouse", "alias": None, "reason": "primary"}, p)
    r = ams.call_source("base_readQuery", "srvB")
    check("the routed server wins over the name (the adapter knows where it went)", r and r["server_id"] == "srvB" and r["server"] == "CRM", r)
    check("a name the turn does not arm is a plain (first-server) name", ams.call_source("erp__x")["server_id"] == "srvA")
    check("None / empty names → None", ams.call_source(None) is None and ams.call_source("") is None)
with armed({"crm": "srvB"}, names={}):
    u = ams.call_source("crm__base_readQuery")
    check("an unnamed server is shown by its id, never dropped", u and u["server"] == "srvB", u)
with armed({"crm": "srvB"}, primary=None, names=NAMES):
    check("no first server known → a plain name is not named (never a guess)", ams.call_source("base_readQuery") is None)
with armed({"crm": ""}, names=NAMES):
    check("an alias with no routable server → not named", ams.call_source("crm__base_readQuery") is None)
with armed({"crm": "srvB"}):   # aliases armed, names never armed (a caller outside the seam)
    n = ams.call_source("crm__base_readQuery")
    check("names not armed → the server id stands in", n and n["server"] == "srvB" and n["reason"] == "qualified", n)
check("everything released → None again", ams.call_source("crm__base_readQuery") is None)

print("[2] the adapter stamp — every branch")


def stamp(name, route, result=None, connector=None, raise_on_lookup=False):
    res = {"status": "success", "results": []} if result is None else result
    def _gc(t):
        if raise_on_lookup:
            raise RuntimeError("registry down")
        return connector
    with patch("trusted_data_agent.core.platform_connector_registry.get_connector_for_tool", side_effect=_gc):
        adapter._stamp_mcp_source(res, name, route)
    return res


r = stamp("base_readQuery", {"server_id": "srvA"})
check("single-server (nothing armed): no key", "mcp_source" not in r, r)
with armed({"crm": "srvB"}, names=NAMES):
    r = stamp("crm__base_readQuery", {"server_id": "srvB"})
    check("routed to an additional server: named", r.get("mcp_source", {}).get("server_id") == "srvB", r)
    r = stamp("base_readQuery", {"server_id": "srvA"})
    check("routed to the first server: named with the primary reason", r.get("mcp_source", {}).get("reason") == "primary", r)
    r = stamp("TDA_FinalReport", {})
    check("a TDA_ tool is never named", "mcp_source" not in r, r)
    r = stamp("google__read_emails", {"connector": "uderia-google"})
    check("a connector call is never named", "mcp_source" not in r, r)
    r = stamp("google__read_emails", {}, connector="uderia-google")
    check("refused before routing, a connector owns the plain name: not named", "mcp_source" not in r, r)
    r = stamp("base_readQuery", {}, connector=None)
    check("refused before routing, no connector owns it: the first server", r.get("mcp_source", {}).get("server_id") == "srvA", r)
    r = stamp("crm__base_readQuery", {}, connector="should-not-be-asked")
    check("refused before routing, a qualified name: its server (the name is unambiguous)", r.get("mcp_source", {}).get("server_id") == "srvB", r)
    r = stamp("base_readQuery", {}, raise_on_lookup=True)
    check("a failing connector lookup never breaks the call and names nothing", "mcp_source" not in r, r)
    lst = [1, 2]
    stamp("base_readQuery", {"server_id": "srvA"}, result=lst)
    check("a non-dict result is left untouched", lst == [1, 2])
    r = stamp("base_readQuery", {"server_id": "srvA"}, result={"status": "success", "mcp_source": {"server_id": "keep"}})
    check("an existing stamp is never overwritten", r["mcp_source"] == {"server_id": "keep"}, r)

print("[3] invoke_mcp_tool carries the route to the stamp")


async def run_invoke(name, routed_to=None, connector=None):
    async def fake_impl(STATE, command, *a, **k):
        if connector:
            adapter._note_route(connector=connector)
        if routed_to:
            adapter._note_route(server_id=routed_to)
        return {"status": "success", "results": [{"x": 1}]}, 3, 4
    with patch.object(adapter, "_invoke_mcp_tool_impl", side_effect=fake_impl), \
         patch("trusted_data_agent.core.platform_connector_registry.get_connector_for_tool", return_value=None):
        return await adapter.invoke_mcp_tool({}, {"tool_name": name, "arguments": {}}, user_uuid="u", session_id="s")


out = asyncio.run(run_invoke("base_readQuery", routed_to="srvA"))
check("single-server: the implementation's output passes through untouched", out == ({"status": "success", "results": [{"x": 1}]}, 3, 4), out)
with armed({"crm": "srvB"}, names=NAMES):
    out = asyncio.run(run_invoke("crm__base_readQuery", routed_to="srvB"))
    check("the routed server reaches the result", out[0].get("mcp_source", {}).get("server") == "CRM" and out[1:] == (3, 4), out)
    out = asyncio.run(run_invoke("base_readQuery", routed_to="srvA"))
    check("…for the first server too", out[0].get("mcp_source", {}).get("server") == "Warehouse", out)
    out = asyncio.run(run_invoke("web__search", connector="uderia-web"))
    check("a call the implementation routed to a connector is not named", "mcp_source" not in out[0], out)
check("the route holder is released after the call", adapter._MCP_ROUTE.get() is None)

src = open(os.path.join(ROOT, "src/trusted_data_agent/mcp_adapter/adapter.py")).read()
check("the implementation notes a connector route where it routes to a connector",
      "platform_server_id = _get_connector_for_tool(tool_name)\n        if platform_server_id:\n            _note_route(connector=platform_server_id)" in src)
check("…and the MCP server right after the one resolver chose it",
      "resolve_mcp_target(tool_name, mcp_client, server_id, user_uuid)\n        _note_route(server_id=server_id)" in src)

print("[4] the ReAct lane")
from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor  # noqa: E402

ex = object.__new__(ConversationAgentExecutor)
ex._tool_server = {"crm__base_readQuery": "srvB", "base_readQuery": "srvA"}
with armed({"crm": "srvB"}, names=NAMES):
    check("a qualified tool names its server", (ex._call_source("crm__base_readQuery") or {}).get("server") == "CRM")
    check("a primary tool names the first server", (ex._call_source("base_readQuery") or {}).get("reason") == "primary")
    check("a tool with no server metadata (connector / component) is never named", ex._call_source("TDA_Charting") is None
          and ex._call_source("google__read_emails") is None)
check("single-server turn: nothing named", ex._call_source("base_readQuery") is None)
ex2 = object.__new__(ConversationAgentExecutor)
check("an executor built before the map exists names nothing (never raises)", ex2._call_source("base_readQuery") is None)

with armed({"crm": "srvB"}, names=NAMES):
    ev = ex._with_source({"tool_name": "crm__base_readQuery"}, "crm__base_readQuery")
    check("an event of a server's tool gains `source`", (ev.get("source") or {}).get("server_id") == "srvB", ev)
    ev = ex._with_source({"tool_name": "TDA_Charting"}, "TDA_Charting")
    check("…an event of a tool with no server does not", "source" not in ev, ev)
ev = ex._with_source({"tool_name": "base_readQuery"}, "base_readQuery")
check("a single-server turn's event is unchanged", ev == {"tool_name": "base_readQuery"}, ev)

ca = open(os.path.join(ROOT, "src/trusted_data_agent/agent/conversation_agent.py")).read()
tree = ast.parse(ca)
emits = {}
for node in ast.walk(tree):
    if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
        c = node.value
        if getattr(c.func, "attr", "") == "_emit_event" and c.args and isinstance(c.args[0], ast.Constant):
            et = c.args[0].value
            if et in ("conversation_tool_invoked", "conversation_tool_completed"):
                emits.setdefault(et, []).append(c.args[1])
# the loop (one invoked, two completed: success + failure) plus the leaked-SQL recovery
# (one of each) — every one of them through _with_source, asserted per emission below
check("two invoked and three completed tool events are emitted",
      len(emits.get("conversation_tool_invoked", [])) == 2 and len(emits.get("conversation_tool_completed", [])) == 3, emits)
for et, args in emits.items():
    for a in args:
        ok = (isinstance(a, ast.Call) and getattr(a.func, "attr", "") == "_with_source"
              and len(a.args) == 2 and isinstance(a.args[1], ast.Name) and a.args[1].id == "tool_name")
        check(f"{et} is emitted through self._with_source(…, tool_name)", ok, ast.dump(a)[:120])
check("the tool → server map is read from each tool's own metadata",
      '"uderia_mcp_server_id"' in ca and "self._tool_server[_t.name] = str(_srv)" in ca)

print("[5] the execution seam")
es = open(os.path.join(ROOT, "src/trusted_data_agent/agent/execution_service.py")).read()
i_arm, i_map = es.find("_set_labels(_primary_of(active_profile)"), es.find("_mcp_servers_token = _set_turn_servers(")
check("the names are armed after, and only beside, the alias map", 0 < i_map < i_arm
      and "if _mcp_servers_token is not None:" in es[i_map:i_arm])
check("the names are released in the same finally as the map", "_reset_labels(_mcp_labels_token)" in es)
check("the token exists before the try (the finally can always read it)", "    _mcp_labels_token = None    #" in es)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
