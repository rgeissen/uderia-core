"""A turn's tool calls go to ITS server — never to the server another concurrent turn of the
same user just switched to.

FOUND LIVE (Optimize campaign + trust campaign running together, 2026-10-27, OC-F1): one
user's REST turns on two agents bound to two MCP entries. Every REST query runs
`switch_profile_context`, which writes the PER-USER server id (`set_user_mcp_server_id`); the
`@TAG` overlay pins the server for the paths that carry `mcp_server_id` in the overlay — but two
overlay branches and the default path do not, and the adapter then falls back to the per-user
value: *"Couldn't find a server with name 'server-9698827f…', expected one of
['server-078897bf…']"* mid-turn, after the OTHER turn's switch.

The rule: the turn's PRIMARY server is ARMED at the execution seam in a context variable
(`tool_identity.set_turn_primary_server`, the sibling of the alias map), inherited by every
task the turn spawns (a delegated prompt, an orchestrator), and `mcp_adapter.turn_server_id`
reads: the overlay's slot → the armed primary → only then the per-user value. CI-pure.
"""
import asyncio
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


from collections import ChainMap  # noqa: E402

from trusted_data_agent.core import tool_identity as TI  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter as AD  # noqa: E402
from trusted_data_agent.core import config as CFG  # noqa: E402

USER = "user-pin-test"
# the per-user setter validates an id against the user's configured servers (and auto-corrects
# an unknown one), so the per-user READ is stubbed here — the rule under test is the ORDER
_PER_USER = {"value": "server-B-per-user"}
AD.get_user_mcp_server_id = lambda user_uuid: _PER_USER["value"]

print("\n── §1 resolution order: overlay slot → armed primary → per-user")
check("nothing armed, no overlay → the per-user value (today's behaviour)",
      AD.turn_server_id({}, USER) == "server-B-per-user", str(AD.turn_server_id({}, USER)))
tok = TI.set_turn_primary_server("server-A-this-turn")
try:
    check("armed primary beats the per-user value",
          AD.turn_server_id({}, USER) == "server-A-this-turn", str(AD.turn_server_id({}, USER)))
    overlay = ChainMap({"mcp_server_id": "server-O-overlay"}, {})
    check("the overlay's slot beats the armed primary",
          AD.turn_server_id(overlay, USER) == "server-O-overlay")
    check("an overlay WITHOUT the slot falls to the armed primary, never to the per-user value",
          AD.turn_server_id(ChainMap({}, {}), USER) == "server-A-this-turn")

    print("\n── §2 the armed primary is inherited by the tasks a turn spawns (a delegated prompt)")

    async def child():
        return AD.turn_server_id({}, USER)

    async def parent():
        _PER_USER["value"] = "server-B-switched-by-another-turn"   # the race: another turn's switch
        return await asyncio.create_task(child())
    check("a child task reads the parent's armed primary after the per-user value was switched",
          asyncio.run(parent()) == "server-A-this-turn")
finally:
    TI.reset_turn_primary_server(tok)
check("after reset the per-user value is read again", AD.turn_server_id({}, USER) == "server-B-switched-by-another-turn")
check("reset tolerates a foreign token", TI.reset_turn_primary_server(object()) is None)

print("\n── §3 the seams carry the server (source rules)")
es = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "execution_service.py")).read()
check("the execution seam ARMS the turn's primary server beside the alias map",
      "set_turn_primary_server" in es and "reset_turn_primary_server" in es)
oe = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "engines", "optimize_engine.py")).read()
calls = [m for m in re.finditer(r"apply_override_state_overlay\(\n((?:.*\n){1,14}?)\s*\)", oe)]
check("every overlay installation names the turn's server (mcp_server_id=)", len(calls) >= 3 and
      all("mcp_server_id=" in m.group(1) for m in calls), str([("mcp_server_id=" in m.group(1)) for m in calls]))
# `invoke_mcp_tool` only delegates; the routing is in `_invoke_mcp_tool_impl`
src = inspect.getsource(AD._invoke_mcp_tool_impl)
check("invoke_mcp_tool resolves its server through turn_server_id, never a bare per-user read",
      "turn_server_id(" in src and "server_id or get_user_mcp_server_id" not in src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
