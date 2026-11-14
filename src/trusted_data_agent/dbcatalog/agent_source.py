"""The data sources an agent reaches, as catalog providers — ONE resolution for every tool.

The Database Navigator was the first tool that needed "the agent's own database" (its MCP
tools, or a direct Database Connection, through one ``CatalogProvider``). The skill library
needs the same thing to tell which pattern sections the agent's database can run. Both call
this module, so the two can never disagree about which database the agent is connected to.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def agent_profile(context: Dict[str, Any]) -> Dict[str, Any]:
    """The executing profile: from the call context, else read by id (the KG handler pattern)."""
    pcfg = context.get("profile_config")
    if isinstance(pcfg, dict) and pcfg:
        return pcfg
    pid, uid = context.get("profile_id"), context.get("user_uuid")
    if pid and uid:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            return get_config_manager().get_profile(pid, uid) or {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


async def agent_mcp(context: Dict[str, Any]):
    """(available_tools, invoke, label, server_id) for the agent's MCP source, from the
    process STATE — the same read the KG constructor performs. Never raises."""
    try:
        from trusted_data_agent.core.agent_mcp_servers import primary_of
        from trusted_data_agent.core.config import APP_STATE
        from trusted_data_agent.dbcatalog.mcp_provider import (available_tools_from_state, available_tools_live,
                                                             reconcile_state_schema)
        from trusted_data_agent.mcp_adapter.adapter import invoke_mcp_tool
        state = context.get("STATE") or APP_STATE
        prof = agent_profile(context)
        server_id = primary_of(prof)
        try:
            tools = await available_tools_live(state, server_id, context.get("user_uuid"))
        except Exception:  # noqa: BLE001
            tools = available_tools_from_state(state)
        for t in tools:
            if t.get("live"):
                reconcile_state_schema(state, t["name"], t["args"])
        uid, sid, pid = context.get("user_uuid"), context.get("session_id"), context.get("profile_id")

        async def _invoke(tool, args):
            res, _i, _o = await invoke_mcp_tool(state, {"tool_name": tool, "arguments": args},
                                                user_uuid=uid, session_id=sid, profile_id=pid)
            return res
        return tools, _invoke, str(prof.get("mcpServerName") or "MCP data source"), primary_of(prof) or "active"
    except Exception:  # noqa: BLE001
        return [], None, "MCP data source", "active"


async def agent_provider(context: Dict[str, Any], source_id: Optional[str] = None) -> Tuple[Any, Any, Dict[str, Any]]:
    """``(service, provider, resolution)`` for ``source_id`` or the agent's default source.
    ``provider`` is None when the agent reaches no source."""
    from trusted_data_agent.dbcatalog.service import get_navigator_service
    svc = get_navigator_service()
    uid = context.get("user_uuid") or ""
    prof = agent_profile(context)
    tools, invoke, mlabel, mid = await agent_mcp(context)
    res = svc.sources(uid, prof, tools, mlabel, mid)
    sid = (source_id or "").strip() or res.get("default")
    if not sid:
        return svc, None, res
    return svc, svc.provider(uid, sid, mcp_invoke=invoke, available_tools=tools, mcp_label=mlabel), res
