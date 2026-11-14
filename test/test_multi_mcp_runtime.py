#!/usr/bin/env python3
"""PROP-006 phase 2 — an agent with TWO MCP servers, driven against two REAL stdio MCP servers.

The servers (``fixtures/fake_mcp_warehouse.py``) expose OVERLAPPING tool names
(``base_readQuery``, ``base_tableList``, ``base_databaseList``, prompt ``base_query``) and one tool
each of their own; every answer names the server that produced it.

[1] classification per server (CL1–CL3, CL8): the primary exactly as before, the additional
    server under its unique names and its own categories; each server's snapshot kept apart;
    full mode = one LLM call per server; a stored snapshot is reused (no session, no call)
[2] the merged catalog (M2, O4): both servers' same-named tools present, nothing overwritten;
    the planner's rendered catalog carries the qualified names; the selection per server holds
[3] routing (M1, M7): a qualified call reaches its own server under the server's name; a plain
    call reaches the primary; an alias the turn does not know fails closed; two concurrent turns
    with different mappings never cross
[4] the ReAct lane (M3, I1): one server's tools loaded under their qualified names, no duplicate
    names bound, a renamed tool still calls its server under the server's own name
[5] disabled tools: each name judged by its own server's selection
[6] one server down (B9): the other still loads

Run: python3 test/test_multi_mcp_runtime.py   (spawns two local stdio processes; no network)
"""
import asyncio
import copy
import json
import logging
import os
import sys
from contextlib import ExitStack
from unittest.mock import patch

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "src"))
logging.disable(logging.CRITICAL)

from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402
from trusted_data_agent.mcp_adapter import adapter  # noqa: E402
from trusted_data_agent.core import config_manager as cmmod  # noqa: E402
from trusted_data_agent.core import tool_identity as ti  # noqa: E402
from trusted_data_agent.core.agent_mcp_servers import ADDITIONAL_CLASSIFICATION_KEY  # noqa: E402
from trusted_data_agent.agent.executor import rebuild_tools_and_prompts_context  # noqa: E402
from trusted_data_agent.llm import langchain_adapter as lca  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {str(detail)[:500]}" if detail else ""))


FIX = os.path.join(HERE, "fixtures", "fake_mcp_warehouse.py")
USER = "u-multi"


def stdio(name, db, extra):
    return {"type": "stdio", "command": sys.executable, "args": [FIX, "--name", name, "--db", db, "--extra", extra]}


SERVERS = [
    {"id": "srvA", "name": "Warehouse A", "transport": stdio("A", "sales", "sales_forecast")},
    {"id": "srvB", "name": "Warehouse B", "transport": stdio("B", "crm", "crm_contacts")},
    {"id": "srvDown", "name": "Gone", "transport": {"type": "stdio", "command": sys.executable,
                                                    "args": ["-c", "import sys; sys.exit(3)"]}},
]


def client_for(sid):
    s = next(x for x in SERVERS if x["id"] == sid)
    t = s["transport"]
    return MultiServerMCPClient({sid: {"transport": "stdio", "command": t["command"], "args": t["args"]}})


class CountingClient:
    def __init__(self, inner, log):
        self.inner, self.log = inner, log

    def session(self, sid):
        self.log.append(sid)
        return self.inner.session(sid)


PROFILE = {
    "id": "p-multi", "tag": "MULTI", "profile_type": "tool_enabled", "classification_mode": "full",
    "mcpServerId": "srvA", "tools": ["*"], "prompts": ["*"],
    "additionalMcpServers": [{"id": "srvB", "alias": "crm", "tools": ["base_readQuery", "crm_contacts"],
                              "prompts": ["*"]}],
}


class FakeCM:
    """The real save rules (strip + timestamp + listed-server check) over an in-memory profile."""
    def __init__(self, profile):
        self.p = copy.deepcopy(profile)
        self.primary_saves = []

    def get_profile(self, pid, uid=None):
        return self.p if pid == self.p["id"] else None

    def get_profiles(self, uid=None):
        return [self.p]

    def save_profiles(self, profiles, uid=None):
        self.p = profiles[0]
        return True

    def save_profile_classification(self, profile_id, classification_results, user_uuid=None):
        self.primary_saves.append(copy.deepcopy(classification_results))

    _without_runtime_injected = cmmod.ConfigManager._without_runtime_injected
    _RUNTIME_INJECTED_CATEGORIES = cmmod.ConfigManager._RUNTIME_INJECTED_CATEGORIES

    def save_additional_classification(self, *a, **k):
        return cmmod.ConfigManager.save_additional_classification(self, *a, **k)

    def get_mcp_servers(self, uid=None):
        return copy.deepcopy(SERVERS)

    def get_profile_enabled_tools(self, pid, uid=None):
        return list(self.p.get("tools") or [])


def classify_llm(calls):
    async def _llm(llm, prompt, **kw):
        calls.append(prompt)
        names = [ln[2:].split(" (")[0] for ln in prompt.splitlines() if ln.startswith("- ")]
        return json.dumps({n: {"category": "Data" if "Query" in n or "List" in n else "Other"} for n in names}), 1, 1, "p", "m"
    return _llm


async def activate(fake, state, sessions, llm_calls):
    with ExitStack() as st:
        st.enter_context(patch.object(adapter, "get_user_mcp_server_id", return_value="srvA"))
        st.enter_context(patch.object(adapter.llm_handler, "call_llm_api", side_effect=classify_llm(llm_calls)))
        st.enter_context(patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=fake))
        st.enter_context(patch("trusted_data_agent.core.platform_connector_registry.get_effective_tools", return_value=[]))
        await adapter.load_and_categorize_mcp_resources(state, user_uuid=USER, profile_id=fake.p["id"])
        return await adapter.load_additional_mcp_resources(
            state, USER, fake.p["id"], client_for=lambda sid: CountingClient(client_for(sid), sessions))


async def main():
    print("[1] classification per server")
    fake = FakeCM(PROFILE)
    sessions, llm_calls = [], []
    clientA = CountingClient(client_for("srvA"), sessions)
    state = {"mcp_client": clientA, "llm": object()}
    report = await activate(fake, state, sessions, llm_calls)
    check("the primary opened one session on its own server, the additional one on its own",
          sessions == ["srvA", "srvB"], sessions)
    check("full mode: one LLM classification call per server", len(llm_calls) == 2, len(llm_calls))
    check("the additional server was classified (not a stored snapshot)",
          report.get("srvB", {}).get("source") == "classified", report)
    prim = fake.primary_saves[-1]
    prim_names = sorted(t["name"] for g in prim["tools"].values() for t in g)
    check("the primary's snapshot holds only the primary's names, unqualified",
          prim_names == ["base_databaseList", "base_readQuery", "base_tableList", "sales_forecast"], prim_names)
    extra_snap = fake.p.get(ADDITIONAL_CLASSIFICATION_KEY, {}).get("srvB")
    check("the additional snapshot is kept apart, keyed by server id", isinstance(extra_snap, dict))
    extra_names = sorted(t["name"] for g in extra_snap["tools"].values() for t in g) if extra_snap else []
    check("the additional snapshot holds the SERVER's own names (the alias applies on load)",
          extra_names == ["base_databaseList", "base_readQuery", "base_tableList", "crm_contacts"], extra_names)
    check("the additional snapshot carries a time and its mode",
          extra_snap and extra_snap.get("last_classified") and extra_snap.get("classified_with_mode") == "full")
    check("runtime-injected categories never enter the additional snapshot (CL8)",
          extra_snap and not ({"System Tools", "Component Tools"} & set(extra_snap["tools"])))

    print("[2] the merged catalog")
    keys = set(state["mcp_tools"])
    for n in ("base_readQuery", "crm__base_readQuery", "base_tableList", "crm__base_tableList",
              "sales_forecast", "crm__crm_contacts"):
        check(f"`{n}` is in the catalog", n in keys)
    check("no qualified name for a primary tool", "crm__sales_forecast" not in keys)
    _q = state["mcp_tools"].get("crm__base_readQuery")
    check("a qualified tool knows its server and its base name",
          getattr(_q, "mcp_server_id", None) == "srvB" and getattr(_q, "base_name", None) == "base_readQuery")
    cats = sorted(state["structured_tools"])
    check("the additional server's categories are its own", "crm: Data" in cats and "Data" in cats, cats)
    by_name = {t["name"]: t for g in state["structured_tools"].values() for t in g}
    check("the additional server's selection holds: selected tool enabled",
          (by_name.get("crm__base_readQuery") or {}).get("disabled") is False)
    check("…and an unselected tool disabled", (by_name.get("crm__base_tableList") or {}).get("disabled") is True)
    check("the primary's wildcard selection is untouched", (by_name.get("base_tableList") or {}).get("disabled") is False)
    check("the additional server's prompt is in the catalog under its unique name",
          "crm__base_query" in state.get("mcp_prompts", {}) and "base_query" in state.get("mcp_prompts", {}))
    tc, pc = rebuild_tools_and_prompts_context(state=state)
    check("the planner's catalog renders the qualified tool", "`crm__base_readQuery`" in tc)
    check("…and the primary's same-named tool", "`base_readQuery`" in tc)
    check("…under the additional server's category", "--- Category: crm: Data ---" in tc)
    check("an unselected additional tool is not rendered", "crm__base_tableList" not in tc)
    check("the prompt catalog renders the qualified prompt", "`crm__base_query`" in pc)

    sessions2, llm_calls2 = [], []
    state2 = {"mcp_client": CountingClient(client_for("srvA"), sessions2), "llm": object(),
              "mcp_tool_schema_cache": state["mcp_tool_schema_cache"]}
    report2 = await activate(fake, state2, sessions2, llm_calls2)
    check("a second activation reuses the stored snapshot: no session to the additional server",
          "srvB" not in sessions2 and report2.get("srvB", {}).get("source") == "snapshot", (sessions2, report2))
    check("…and it produces the same merged catalog", sorted(state2["mcp_tools"]) == sorted(state["mcp_tools"]))

    print("[3] routing")
    pool = {"srvA": client_for("srvA"), "srvB": client_for("srvB")}
    rstate = {"mcp_client": pool["srvA"], "mcp_tools": state["mcp_tools"]}

    async def call(name, sql="SELECT 1"):
        with ExitStack() as st:
            st.enter_context(patch.object(adapter, "get_user_mcp_server_id", return_value="srvA"))
            st.enter_context(patch("trusted_data_agent.core.platform_connector_registry.get_connector_for_tool", return_value=None))
            st.enter_context(patch("trusted_data_agent.core.configuration_service.pooled_mcp_client",
                                   side_effect=lambda sid, uid: pool[sid]))
            res, _, _ = await adapter.invoke_mcp_tool(rstate, {"tool_name": name, "arguments": {"sql": sql}},
                                                      user_uuid=USER, session_id=f"s-{name}", workflow_state={})
        return res

    tok = ti.set_turn_servers({"crm": "srvB"})
    try:
        r = await call("crm__base_readQuery")
        rows = (r or {}).get("results") or []
        check("a qualified call reaches its own server", rows and rows[0].get("server") == "B", r)
        r = await call("base_readQuery")
        rows = (r or {}).get("results") or []
        check("a plain call reaches the primary", rows and rows[0].get("server") == "A", r)
    finally:
        ti.reset_turn_servers(tok)
    # every call's result names the server that ANSWERED (Live Status, live and on reload)
    from trusted_data_agent.core import agent_mcp_servers as ams
    tok = ti.set_turn_servers({"crm": "srvB"})
    ltok = ams.set_turn_source_labels("srvA", {"srvA": "Warehouse A", "srvB": "CRM B"})
    try:
        r = await call("crm__base_readQuery")
        src = (r or {}).get("mcp_source") or {}
        check("a qualified call's result names its server, alias and reason",
              ((r or {}).get("results") or [{}])[0].get("server") == "B"
              and src == {"server_id": "srvB", "server": "CRM B", "alias": "crm", "reason": "qualified"}, r)
        r = await call("base_readQuery")
        src = (r or {}).get("mcp_source") or {}
        check("a plain call's result names the first server",
              ((r or {}).get("results") or [{}])[0].get("server") == "A"
              and src == {"server_id": "srvA", "server": "Warehouse A", "alias": None, "reason": "primary"}, r)
    finally:
        ams.reset_turn_source_labels(ltok)
        ti.reset_turn_servers(tok)
    r = await call("base_readQuery")
    check("a single-server turn's result carries no server field (byte-identical)",
          "mcp_source" not in (r or {}) and ((r or {}).get("results") or [{}])[0].get("server") == "A", r)
    r = await call("crm__base_readQuery")
    check("with no alias armed a qualified-looking name is not routed to another server",
          (r or {}).get("status") == "error" or ((r or {}).get("results") or [{}])[0].get("server") != "B", r)

    async def turn(mapping, name):
        tok = ti.set_turn_servers(mapping)
        try:
            await asyncio.sleep(0)
            r = await call(name)
            return ((r or {}).get("results") or [{}])[0].get("server"), (r or {}).get("status")
        finally:
            ti.reset_turn_servers(tok)

    got = await asyncio.gather(turn({"crm": "srvB"}, "crm__base_readQuery"),
                               turn({"crm": "srvA"}, "crm__base_readQuery"),
                               turn({}, "base_readQuery"))
    check("three concurrent turns route by their OWN mappings (M7)",
          [g[0] for g in got] == ["B", "A", "A"], got)

    tok = ti.set_turn_servers({"crm": "srvB", "erp": "srvA"})
    try:
        r1, r2 = await call("crm__base_readQuery"), await call("erp__base_readQuery")
        check("two aliases in one turn: each routes to ITS server",
              [((r or {}).get("results") or [{}])[0].get("server") for r in (r1, r2)] == ["B", "A"], (r1, r2))
    finally:
        ti.reset_turn_servers(tok)

    tok = ti.set_turn_servers({"erp": "srvB"})
    try:
        r = await call("crm__base_readQuery")
        check("an alias the turn does not arm is never routed to another server",
              ((r or {}).get("results") or [{}])[0].get("server") != "B", r)
    finally:
        ti.reset_turn_servers(tok)

    print("[3c] preflight per server (O5/O6): one server's database list never answers for another's")
    from trusted_data_agent.core.config import APP_CONFIG
    from trusted_data_agent.agent.preflight import catalog as pcat
    _mode = getattr(APP_CONFIG, "PREFLIGHT_SQL_MODE", "observe")
    APP_CONFIG.PREFLIGHT_SQL_MODE = "enforce"
    sid_pf = "s-preflight"

    async def run_sql(name, sql=None, args=None):
        with ExitStack() as st:
            st.enter_context(patch.object(adapter, "get_user_mcp_server_id", return_value="srvA"))
            st.enter_context(patch("trusted_data_agent.core.platform_connector_registry.get_connector_for_tool", return_value=None))
            st.enter_context(patch("trusted_data_agent.core.configuration_service.pooled_mcp_client",
                                   side_effect=lambda sid, uid: pool[sid]))
            res, _, _ = await adapter.invoke_mcp_tool(rstate, {"tool_name": name, "arguments": args or {"sql": sql}},
                                                      user_uuid=USER, session_id=sid_pf, workflow_state={})
        return res

    tok = ti.set_turn_servers({"crm": "srvB"})
    try:
        await run_sql("base_databaseList", args={"scope": "all"})
        await run_sql("crm__base_databaseList", args={"scope": "all"})
        keys = sorted(k for k in pcat._CATALOG if k.startswith(sid_pf))
        check("each server's database list is recorded under its own key",
              keys == [sid_pf, f"{sid_pf}::mcp:srvB"], keys)
        check("the primary's list is the primary's", pcat._CATALOG.get(sid_pf, {}).get("dbs") == frozenset({"sales"}),
              pcat._CATALOG.get(sid_pf))
        check("the additional server's list is its own",
              pcat._CATALOG.get(f"{sid_pf}::mcp:srvB", {}).get("dbs") == frozenset({"crm"}))
        r = await run_sql("base_readQuery", "SELECT * FROM sales.orders")
        check("a primary database on the primary is not refused",
              ((r or {}).get("results") or [{}])[0].get("server") == "A", r)
        r = await run_sql("crm__base_readQuery", "SELECT * FROM crm.contacts")
        check("the additional server's own database is not refused on it",
              ((r or {}).get("results") or [{}])[0].get("server") == "B", r)
        r = await run_sql("crm__base_readQuery", "SELECT * FROM sales.orders")
        check("a database only the primary holds is refused on the additional server",
              (r or {}).get("status") == "error" and "sales" in json.dumps(r).lower(), r)
    finally:
        ti.reset_turn_servers(tok)
        APP_CONFIG.PREFLIGHT_SQL_MODE = _mode

    print("[3b] prompts (O3): a qualified prompt is fetched from its own server")
    from langchain_mcp_adapters.prompts import load_mcp_prompt

    async def fetch(name, servers=None):
        with patch("trusted_data_agent.core.configuration_service.pooled_mcp_client",
                   side_effect=lambda sid, uid: pool[sid]):
            cl, sid, base = adapter.resolve_mcp_target(name, pool["srvA"], "srvA", USER, servers=servers)
            async with cl.session(sid) as sess:
                msgs = await load_mcp_prompt(sess, name=base, arguments={"database_name": "x"})
        return " ".join(str(getattr(m, "content", m)) for m in msgs)

    tok = ti.set_turn_servers({"crm": "srvB"})
    try:
        check("inside a turn: crm__base_query is rendered by server B", "on B" in await fetch("crm__base_query"))
        check("inside a turn: base_query is rendered by the primary", "on A" in await fetch("base_query"))
    finally:
        ti.reset_turn_servers(tok)
    check("outside a turn, with the profile's map: routed by it",
          "on B" in await fetch("crm__base_query", servers={"crm": "srvB"}))
    try:
        adapter.resolve_mcp_target("crm__base_query", None, "srvA", USER, servers={"erp": "srvB"})
        _raised = False
    except Exception:
        _raised = True
    check("a name whose alias the map does not know is the primary's name (never another server)", not _raised)
    try:
        with patch("trusted_data_agent.core.tool_identity.turn_server_for", return_value=None):
            tok = ti.set_turn_aliases(["crm"])
            try:
                adapter.resolve_mcp_target("crm__base_query", None, "srvA", USER)
                _raised = False
            finally:
                ti.reset_turn_servers(tok)
    except Exception:
        _raised = True
    check("a recognised alias with no server is refused, never sent to the primary", _raised)
    import re as _re
    src_all = "".join(open(os.path.join(HERE, "..", "src", "trusted_data_agent", f)).read()
                      for f in ("api/rest_routes.py", "api/routes.py", "agent/planner.py"))
    unrouted = _re.findall(r"(?:get_prompt|load_mcp_prompt)\([^)]*?name=(?:prompt_name|self\.executor\.active_prompt_name)\b",
                           src_all, flags=_re.S)
    check("no MCP prompt fetch uses the unrouted name", unrouted == [], unrouted)
    check("every MCP prompt fetch goes through resolve_mcp_target", src_all.count("resolve_mcp_target(") >= 4)

    print("[4] the ReAct lane")
    with patch.object(lca, "get_config_manager", return_value=fake):
        prim_tools = await lca.load_mcp_tools_for_langchain("srvA", fake.p["id"], USER)
        extra_tools = await lca.load_mcp_tools_for_langchain("srvB", fake.p["id"], USER, alias="crm",
                                                            selection=["base_readQuery", "crm_contacts"])
    names = [t.name for t in prim_tools + extra_tools]
    check("the primary's tools keep their names",
          sorted(t.name for t in prim_tools) == ["base_databaseList", "base_readQuery", "base_tableList", "sales_forecast"],
          [t.name for t in prim_tools])
    check("the additional server's SELECTED tools, qualified",
          sorted(t.name for t in extra_tools) == ["crm__base_readQuery", "crm__crm_contacts"], [t.name for t in extra_tools])
    check("no two bound tools share a name", len(names) == len(set(names)), names)
    check("each tool records its server", all((t.metadata or {}).get("uderia_mcp_server_id") == "srvB" for t in extra_tools))
    rq = next((t for t in extra_tools if t.name == "crm__base_readQuery"), None)
    out = await rq.ainvoke({"sql": "SELECT 7"}) if rq else ""
    text = json.dumps(out) if not isinstance(out, str) else out
    check("the renamed tool still calls ITS server under the server's own name",
          '\\"server\\": \\"B\\"' in text or '"server": "B"' in text, text[:300])

    print("[4b] preflight per server on the ReAct lane (O5/O6)")
    from trusted_data_agent.agent import tool_enforcement as te
    ran = []

    async def _orig(**kw):
        ran.append(kw.get("sql"))
        return [{"type": "text", "text": json.dumps({"status": "success", "results": [{"n": 1}]})}], None

    APP_CONFIG.PREFLIGHT_SQL_MODE = "enforce"
    tok = ti.set_turn_servers({"crm": "srvB"})
    try:
        g_extra = te._make_guarded_coroutine("crm__base_readQuery", _orig, sid_pf, USER, source="mcp:srvB")
        g_prim = te._make_guarded_coroutine("base_readQuery", _orig, sid_pf, USER, source="mcp:srvA")
        out_bad = await g_extra(sql="SELECT * FROM sales.orders")
        out_ok = await g_extra(sql="SELECT * FROM crm.contacts")
        out_prim = await g_prim(sql="SELECT * FROM sales.orders")
        check("ReAct: a database only the primary holds is refused on the additional server",
              "SELECT * FROM sales.orders" not in ran[:1] and "sales" in json.dumps(out_bad).lower(), (ran, out_bad))
        check("ReAct: the additional server's own database runs on it", "SELECT * FROM crm.contacts" in ran, ran)
        check("ReAct: the primary's database runs on the primary", ran.count("SELECT * FROM sales.orders") == 1, ran)
    finally:
        ti.reset_turn_servers(tok)
        APP_CONFIG.PREFLIGHT_SQL_MODE = _mode

    print("[5] disabled tools, each judged by its own server's selection")
    real = cmmod.ConfigManager.__new__(cmmod.ConfigManager)
    with ExitStack() as st:
        st.enter_context(patch.object(cmmod.ConfigManager, "get_profiles", lambda self, uid=None: [fake.p]))
        st.enter_context(patch("trusted_data_agent.core.config.APP_STATE", {"mcp_tools": state["mcp_tools"],
                                                                            "mcp_prompts": state["mcp_prompts"]}))
        st.enter_context(patch.object(cmmod.ConfigManager, "_platform_tool_names", staticmethod(lambda pid: set())))
        dis = sorted(real.get_profile_disabled_tools("p-multi", USER))
        dis_p = sorted(real.get_profile_disabled_prompts("p-multi", USER))
    check("only the additional server's unselected tools are disabled",
          dis == ["crm__base_databaseList", "crm__base_tableList"], dis)
    check("no prompt disabled (both wildcards)", dis_p == [], dis_p)

    print("[6] one server down")
    down = dict(PROFILE, additionalMcpServers=PROFILE["additionalMcpServers"] + [{"id": "srvDown", "alias": "gone", "tools": ["*"]}])
    fake3 = FakeCM(down)
    s3, l3 = [], []
    state3 = {"mcp_client": CountingClient(client_for("srvA"), s3), "llm": object()}
    rep3 = await activate(fake3, state3, s3, l3)
    check("the unreachable server is reported, not raised", rep3.get("srvDown", {}).get("source") == "error", rep3)
    check("the reachable additional server still loads", rep3.get("srvB", {}).get("source") == "classified")
    check("nothing of the unreachable server enters the catalog",
          not any(k.startswith("gone__") for k in state3["mcp_tools"]))

    print("[7] the additional snapshot saver")
    fake4 = FakeCM(PROFILE)
    ok = fake4.save_additional_classification("p-multi", "srvZ", {"tools": {"All Tools": [{"name": "x"}]}}, USER)
    check("a server the agent does not list is refused", ok is False and ADDITIONAL_CLASSIFICATION_KEY not in fake4.p)
    ok = fake4.save_additional_classification("p-multi", "srvB", {"tools": {"All Tools": [{"name": "x"}],
                                                                           "System Tools": [{"name": "TDA_X"}]}}, USER)
    snap = fake4.p[ADDITIONAL_CLASSIFICATION_KEY]["srvB"]
    check("runtime-injected categories are stripped", ok and "System Tools" not in snap["tools"], snap)
    ok = fake4.save_additional_classification("p-multi", "srvB", {"tools": {}, "prompts": {}}, USER)
    check("a snapshot with nothing carries no time", fake4.p[ADDITIONAL_CLASSIFICATION_KEY]["srvB"]["last_classified"] is None)


asyncio.run(asyncio.wait_for(main(), timeout=240))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
