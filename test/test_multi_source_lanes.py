"""Multi-source implementation plan, stage 1 — ONE routing decision and ONE record of the turn's
sources for both tool lanes (the Optimize lane: adapter.invoke_mcp_tool; the ReAct lane:
tool_enforcement's wrapped coroutine), with what legitimately differs declared in SourceLane.

    [1] lane parity — the same call over the same servers makes the same decision in both lanes:
        re-route before, re-route after an error, several holders, a write, one statement over two
        servers, the right server, and a refusal after an error (both lanes now name the servers)
    [2] one decision function — plan_before / plan_after, identical across lanes without a plan
    [3] the plan-bound policy — a server the PLAN chose is honoured by the Optimize lane only, and
        never when that server is known not to hold the data
    [4] the re-route record names its phase — two parallel plan phases each record their own
    [5] one record of unreachable servers — the probe and the lanes feed it, each server announced
        once per turn, the note skips it and says it is unavailable; a missing list tool is not "down"
    [6] wiring — both lanes call the shared functions and keep no private copy of the decision

No server, no network (fake MCP servers). Run: python test/test_multi_source_lanes.py
"""
import ast
import asyncio
import json
import os
import sys
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.agent import source_routing as sr  # noqa: E402
from trusted_data_agent.agent import tool_enforcement as te  # noqa: E402
from trusted_data_agent.agent import result_sets as rs  # noqa: E402
from trusted_data_agent.core import agent_mcp_servers as ams  # noqa: E402
from trusted_data_agent.core import tool_identity as ti  # noqa: E402
from trusted_data_agent.core.agent_mcp_servers import AgentServer  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" — {detail}" if detail != "" else ""))


U = "user-lanes"
SERVERS = (AgentServer("srvA", None, ("*",), ()), AgentServer("srvB", "crm", ("*",), ()))
THREE = SERVERS + (AgentServer("srvC", "fit2", ("*",), ()),)
NAMES = {"srvA": "sales", "srvB": "crm", "srvC": "sales copy"}
HOLD = {"srvA": {"ms_sales"}, "srvB": {"ms_crm"}, "srvC": {"ms_sales"}}


class armed:
    def __init__(self, servers=SERVERS):
        self.servers = servers

    def __enter__(self):
        self.t1 = ti.set_turn_servers({s.alias: s.server_id for s in self.servers if s.alias})
        self.t2 = ams.set_turn_source_labels("srvA", NAMES)
        self.t3 = sr.set_turn(U, self.servers)
        return self

    def __exit__(self, *a):
        sr.reset_turn(self.t3)
        ams.reset_turn_source_labels(self.t2)
        ti.reset_turn_servers(self.t1)


async def probe_known(user, sid):
    return sorted(HOLD.get(sid, ()))


async def probe_unknown(user, sid):
    return None


def _sid_of(tool):
    a = ti.parse(tool).alias
    return ti.turn_server_for(a) if a else "srvA"


def _missing(sid, args):
    return [d for d in sr.referenced_databases(args) if d not in HOLD[sid]]


# ── the Optimize lane ─────────────────────────────────────────────────────────────────────
CALLS = []
STATE = {"mcp_tools": {n: 1 for n in ("base_readQuery", "crm__base_readQuery", "fit2__base_readQuery",
                                      "base_writeQuery", "crm__base_writeQuery")}}


async def fake_impl(STATE, command, user_uuid=None, session_id=None, call_id=None, workflow_state=None, profile_id=None):
    tool = command["tool_name"]
    sid = _sid_of(tool)
    adapter._MCP_ROUTE.get()["server_id"] = sid
    CALLS.append(sid)
    miss = _missing(sid, command.get("arguments") or {})
    if miss:
        return {"status": "error", "error": "An exception occurred",
                "data": f"[Error 3523] The user does not have SELECT access to {miss[0]}.T."}, 0, 0
    return {"status": "success", "results": [{"n": 1}]}, 0, 0


def optimize(tool, sql, probe=probe_known, servers=SERVERS):
    CALLS.clear()
    sr._reset_all()

    async def _go():
        with armed(servers):
            return await adapter.invoke_mcp_tool(STATE, {"tool_name": tool, "arguments": {"sql": sql}}, U, "s-opt")
    with patch.object(adapter, "_invoke_mcp_tool_impl", side_effect=fake_impl), \
            patch.object(sr, "_probe", side_effect=probe):
        res = asyncio.run(_go())[0]
    return {"calls": list(CALLS), "status": res.get("status"),
            "routed_to": (res.get("mcp_source") or {}).get("server_id") if (res.get("mcp_source") or {}).get("reason") == "routed" else None,
            "text": f"{res.get('error') or ''}\n{res.get('data') or ''}\n{res.get('routing_note') or ''}"}


# ── the ReAct lane ────────────────────────────────────────────────────────────────────────
class FakeTool:
    def __init__(self, name, sid):
        self.name, self.metadata = name, {"uderia_mcp_server_id": sid}
        self.coroutine = self._run

    async def _run(self, **kwargs):
        sid = self.metadata["uderia_mcp_server_id"]
        CALLS.append(sid)
        miss = _missing(sid, kwargs)
        if miss:
            raise Exception(f"[Error 3523] The user does not have SELECT access to {miss[0]}.T.")
        return json.dumps({"status": "success", "results": [{"n": 1}]}), None


def react(tool, sql, probe=probe_known, servers=SERVERS):
    CALLS.clear()
    sr._reset_all()
    tools = [FakeTool("base_readQuery", "srvA"), FakeTool("crm__base_readQuery", "srvB")]
    if any(s.server_id == "srvC" for s in servers):
        tools.append(FakeTool("fit2__base_readQuery", "srvC"))
    te.wrap_langchain_mcp_tools(tools, "s-react", user_uuid=U)
    by = {t.name: t for t in tools}

    async def _go():
        with armed(servers):
            out = await by[tool].coroutine(sql=sql)
            return out, sr.take_reroute(tool)
    with patch.object(sr, "_probe", side_effect=probe):
        (out, _art), info = asyncio.run(_go())
    text = str(out)
    ok = '"success"' in text
    return {"calls": list(CALLS), "status": "success" if ok else "error",
            "routed_to": info["server_id"] if info else None, "text": text}


# ---------------------------------------------------------------------------------------------
print("[1] lane parity — the same call makes the same decision in both lanes")
SCEN = [
    ("re-route before the call", "crm__base_readQuery", "SELECT COUNT(*) FROM ms_sales.Sales", probe_known, SERVERS),
    ("re-route after the refusal", "crm__base_readQuery", "SELECT COUNT(*) FROM ms_sales.Sales", probe_unknown, SERVERS),
    ("several holders: refused", "crm__base_readQuery", "SELECT COUNT(*) FROM ms_sales.Sales", probe_known, THREE),
    ("one statement over two servers: refused", "base_readQuery",
     "SELECT 1 FROM ms_sales.Sales s JOIN ms_crm.Customers c ON 1=1", probe_known, SERVERS),
    ("the right server", "base_readQuery", "SELECT COUNT(*) FROM ms_sales.Sales", probe_known, SERVERS),
    ("refused after the error, several unknown", "base_readQuery", "SELECT COUNT(*) FROM ms_crm.Customers",
     probe_unknown, THREE),
]
for name, tool, sql, probe, servers in SCEN:
    o, r = optimize(tool, sql, probe, servers), react(tool, sql, probe, servers)
    check(f"{name}: the same servers were called", o["calls"] == r["calls"], (o["calls"], r["calls"]))
    check(f"{name}: the same outcome", o["status"] == r["status"], (o["status"], r["status"]))
    check(f"{name}: routed to the same server", o["routed_to"] == r["routed_to"], (o["routed_to"], r["routed_to"]))

o = optimize("base_readQuery", "SELECT COUNT(*) FROM ms_crm.Customers", probe_unknown, THREE)
r = react("base_readQuery", "SELECT COUNT(*) FROM ms_crm.Customers", probe_unknown, THREE)
check("refused after the error: the Optimize lane names the servers that could hold it",
      "Several of this agent's servers can" in o["text"], o["text"][:300])
check("…and the ReAct lane now names them too (it said nothing before)",
      "Several of this agent's servers can" in r["text"], r["text"][:300])
o = optimize("crm__base_writeQuery", "DELETE FROM ms_sales.Sales")
check("a write is never moved (Optimize)", o["calls"] == ["srvB"] and o["routed_to"] is None, o)


# ---------------------------------------------------------------------------------------------
print("[2] one decision function")


def decide(lane, tool, sql, planned=None, probe=probe_known, servers=SERVERS, available=None):
    sr._reset_all()

    async def _go():
        with armed(servers):
            return await sr.plan_before(tool, _sid_of(tool), {"sql": sql}, lane=lane,
                                        available=available, planned_server=planned)
    with patch.object(sr, "_probe", side_effect=probe):
        return asyncio.run(_go())


for name, tool, sql, probe, servers in SCEN[:5]:
    a = decide(sr.SOURCE_LANE_OPTIMIZE, tool, sql, probe=probe, servers=servers)
    b = decide(sr.SOURCE_LANE_REACT, tool, sql, probe=probe, servers=servers)
    check(f"{name}: identical decision without a plan", (a.action, a.to, a.databases, a.refusal)
          == (b.action, b.to, b.databases, b.refusal), (a, b))
d = decide(sr.SOURCE_LANE_REACT, "crm__base_readQuery", "SELECT COUNT(*) FROM ms_sales.Sales")
check("a re-route carries the one record shape",
      set(d.info) == {"called", "from_label", "databases", "to", "server_id", "note", "phase"}, d.info)
check("…naming where it went", d.info["server_id"] == "srvA" and d.info["to"] == "base_readQuery")
d = decide(sr.SOURCE_LANE_REACT, "TDA_Compute", "SELECT 1 FROM ms_sales.Sales")
check("a TDA_ tool is never routed", d.action == sr.STAND)
with armed():
    with patch.object(sr, "enabled", return_value=False):
        check("switch off: nothing is routable", not sr.call_routable("crm__base_readQuery", "srvB"))
check("no turn armed: nothing is routable", not sr.call_routable("crm__base_readQuery", "srvB"))


# ---------------------------------------------------------------------------------------------
print("[3] the plan-bound policy")
check("the Optimize lane is plan-bound, the ReAct lane is not",
      sr.SOURCE_LANE_OPTIMIZE.plan_bound and not sr.SOURCE_LANE_REACT.plan_bound)
d = decide(sr.SOURCE_LANE_OPTIMIZE, "base_tableList", "", planned="srvB")
check("Optimize: a server the plan chose is honoured", d.action == sr.REROUTE and d.to.server_id == "srvB", d)
check("…and the note says the plan chose it", "the plan chose" in (d.info or {}).get("note", ""), d.info)
d = decide(sr.SOURCE_LANE_REACT, "base_tableList", "", planned="srvB")
check("ReAct: a plan's choice is ignored (the lane has no plan)", d.action == sr.STAND, d)
d = decide(sr.SOURCE_LANE_OPTIMIZE, "crm__base_readQuery", "SELECT COUNT(*) FROM ms_sales.Sales", planned="srvB")
check("a planned server known NOT to hold the data is not honoured — the holder is",
      d.action == sr.REROUTE and d.to.server_id == "srvA", d)
d = decide(sr.SOURCE_LANE_OPTIMIZE, "base_readQuery", "SELECT COUNT(*) FROM ms_sales.Sales", planned="srvA")
check("a planned server that IS the called one: the call stands", d.action == sr.STAND, d)
d = decide(sr.SOURCE_LANE_OPTIMIZE, "base_tableList", "", planned="srvZ")
check("a planned server that is not one of the agent's is ignored", d.action == sr.STAND, d)


# ---------------------------------------------------------------------------------------------
print("[4] the re-route record names its phase — parallel phases")


async def _phase_call(n):
    tok = rs.set_phase(n)
    try:
        await asyncio.sleep(0.01 * (3 - n))   # the later phase finishes first
        return await sr.plan_before("crm__base_readQuery", "srvB", {"sql": "SELECT 1 FROM ms_sales.Sales"},
                                    lane=sr.SOURCE_LANE_OPTIMIZE)
    finally:
        rs.reset_phase(tok)


async def _two_phases():
    with armed():
        return await asyncio.gather(_phase_call(1), _phase_call(2))

sr._reset_all()
with patch.object(sr, "_probe", side_effect=probe_known):
    d1, d2 = asyncio.run(_two_phases())
check("phase 1's record names phase 1", (d1.info or {}).get("phase") == 1, d1.info)
check("phase 2's record names phase 2", (d2.info or {}).get("phase") == 2, d2.info)
check("outside a plan the record names no phase",
      decide(sr.SOURCE_LANE_REACT, "crm__base_readQuery", "SELECT 1 FROM ms_sales.Sales").info["phase"] is None)


# ---------------------------------------------------------------------------------------------
print("[5] one record of unreachable servers")
sr._reset_all()
with armed():
    check("a server of the turn is recorded once", sr.mark_unavailable("srvB", "refused") is True
          and sr.mark_unavailable("srvB", "again") is False)
    check("a server that is not the turn's is not recorded", sr.mark_unavailable("srvZ", "x") is False)
    check("is_unavailable reads the record", sr.is_unavailable("srvB") and not sr.is_unavailable("srvA"))
    ev = sr.take_unavailable_events()
    check("the step is announced once …", len(ev) == 1 and ev[0]["type"] == "mcp_server_unavailable"
          and ev[0]["details"]["server_id"] == "srvB" and ev[0]["details"]["alias"] == "crm", ev)
    check("… whichever lane asks second gets nothing", sr.take_unavailable_events() == [])
    sr.learn_list(U, "srvA", ["ms_sales"])
    note = sr.turn_sources_note() or ""
    check("the note says the server is unavailable", "could not be reached" in note and "'crm'" in note, note)
    check("the note does not list the unreachable server as a source to call",
          "reach server 'crm'" not in note, note)
    check("…but still says what the reachable server holds (the model is never left blind)",
          "reach server 'sales'" in note and "ms_sales" in note, note)
check("no turn armed: nothing is recorded", sr.mark_unavailable("srvB", "x") is False
      and sr.take_unavailable_events() == [] and sr.turn_sources_note() is None)


async def _probe_down(user, sid):
    if sid == "srvB":
        raise ConnectionRefusedError("connection refused")
    return sorted(HOLD[sid])


async def _probe_no_tool(user, sid):
    return None if sid == "srvB" else sorted(HOLD[sid])


async def _turn_start(probe):
    with armed():
        with patch.object(sr, "_probe", side_effect=probe):
            await sr.ensure_lists(U, ["srvA", "srvB"])
        return sr.unavailable_servers(), sr.take_unavailable_events()

sr._reset_all()
down, ev = asyncio.run(_turn_start(_probe_down))
check("the turn-start probe finding a server down records it", [d["server_id"] for d in down] == ["srvB"], down)
check("…and the step is ready for the lane", len(ev) == 1)
down, ev = asyncio.run(_turn_start(_probe_down))
check("a second turn soon after (the probe is not repeated) is told too",
      [d["server_id"] for d in down] == ["srvB"] and len(ev) == 1, down)
sr._reset_all()
down, _ = asyncio.run(_turn_start(_probe_no_tool))
check("a server without a database-list tool is NOT called unavailable", down == (), down)


async def _probe_timeout(user, sid):
    if sid == "srvB":
        raise asyncio.TimeoutError()
    return sorted(HOLD[sid])

sr._reset_all()
down, _ = asyncio.run(_turn_start(_probe_timeout))
check("a probe that times out records the server with the reason",
      down and "did not answer" in down[0]["reason"], down)


# a server that goes down AFTER its list was cached: a call's transport failure records it, and
# the next turn's start names it (the cache no longer hides it) — found live, stage 1
sr._reset_all()
async def _cached_then_down():
    with armed():
        with patch.object(sr, "_probe", side_effect=probe_known):
            await sr.ensure_lists(U, ["srvA", "srvB"])          # both answer; lists cached
        sr.note_unreachable("srvB", "connection refused")     # a later call cannot reach srvB
        first = [d["server_id"] for d in sr.unavailable_servers()]
    with armed():                                             # the NEXT turn
        with patch.object(sr, "_probe", side_effect=probe_known) as p:
            await sr.ensure_lists(U, ["srvA", "srvB"])
            probed = p.called
        return first, [d["server_id"] for d in sr.unavailable_servers()], probed, sr.known_list(U, "srvB")
first, nxt, probed, cached = asyncio.run(_cached_then_down())
check("a transport failure mid-call records the server for this turn", first == ["srvB"], first)
check("the next turn names it at its start, without waiting for the cache to expire",
      nxt == ["srvB"], nxt)
check("…its cached list was dropped (no stale 'holds' answer)", cached is None, cached)
check("…and a single-server turn records nothing", (lambda: (sr.note_unreachable("srvB", "x"), sr.unavailable_servers()))()[1] == ())


# ---------------------------------------------------------------------------------------------
print("[6] wiring")
SRC = os.path.join(ROOT, "src", "trusted_data_agent")


def _fn(path, name):
    tree = ast.parse(open(path).read())
    return next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == name), None)


def _calls(node):
    """Names a function calls — an alias from ``from x import name as _a`` resolves to ``name``."""
    alias = {}
    for n in ast.walk(node):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                alias[a.asname or a.name] = a.name
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            out.add(alias.get(nm, nm))
    return out


ADP = os.path.join(SRC, "mcp_adapter", "adapter.py")
TE = os.path.join(SRC, "agent", "tool_enforcement.py")
ir = _calls(_fn(ADP, "_invoke_routed"))
check("Optimize lane decides through plan_before / plan_after", {"plan_before", "plan_after"} <= ir, ir)
check("…and keeps no private copy of the decision",
      not ({"decide_before", "decide_after", "siblings", "ensure_lists", "reroute_note"} & ir), ir)
rb, ra = _calls(_fn(TE, "_react_route_before")), _calls(_fn(TE, "_react_route_after"))
check("ReAct lane decides through plan_before / plan_after", "plan_before" in rb and "plan_after" in ra)
check("…and keeps no private copy of the decision",
      not ({"decide_before", "decide_after", "siblings", "ensure_lists", "reroute_note"} & (rb | ra)), rb | ra)
check("the private record builders are gone",
      _fn(TE, "_reroute_info") is None and "_routed_info" not in open(ADP).read())
check("both lanes stamp a re-routed call through routed_source",
      "routed_source" in _calls(_fn(ADP, "_stamp_mcp_reroute"))
      and "routed_source" in _calls(_fn(os.path.join(SRC, "agent", "conversation_agent.py"), "_with_source")))
OPT = open(os.path.join(SRC, "agent", "engines", "optimize_engine.py")).read()
CONV = open(os.path.join(SRC, "agent", "engines", "conversation_engine.py")).read()
check("Optimize names an unreachable server at turn start (R3)", "take_unavailable_events()" in OPT)
check("…and the plan gate reads the sources note", "turn_sources_note()" in OPT)
check("ReAct reads the same record and note", "take_unavailable_events()" in CONV and "turn_sources_note()" in CONV)
check("ReAct records a server it cannot load into the one record", "mark_unavailable(" in CONV)
check("ReAct does not load a server the probe already found down", "is_unavailable(" in CONV)
check("ReAct keeps no private list of unreachable servers", "_unavailable = []" not in CONV)
check("both lanes record a mid-call transport failure in the one record",
      "note_unreachable" in _calls(_fn(ADP, "_invoke_routed")) and "_sr.note_unreachable(_sid" in open(TE).read())
PL = _fn(os.path.join(SRC, "agent", "planner.py"), "_generate_meta_plan")
check("the planner's fallback prompt path reads the sources note", PL is not None and "turn_sources_note" in _calls(PL))
CW = open(os.path.join(ROOT, "components", "builtin", "context_window", "modules", "tool_definitions", "handler.py")).read()
check("the context-window module reads the same note", "turn_sources_note()" in CW)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
