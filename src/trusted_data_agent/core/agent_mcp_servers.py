"""PROP-006 phase 2 — an agent's MCP servers, one list for every reader (build brief decision 10).

Storage keeps the primary server exactly as today — ``mcpServerId``, ``tools``, ``prompts``,
``classification_results`` — and puts every ADDITIONAL server in one instance-local key::

    "additionalMcpServers": [{"id": "<server id>", "alias": "crm",
                              "tools": ["base_readQuery", ...], "prompts": [...]}]

``tools`` / ``prompts`` hold the server's OWN names and follow the primary's selection rule: a
literal list (empty = none), ``["*"]`` = all. An additional server's classification snapshots live
in the deployment-local ``additionalMcpClassification`` ({server id: snapshot}).

Every reader asks ``servers_of(profile)`` and gets the same ordered tuple — the primary first — so
the two storage shapes exist only here. A single-server agent's profile never carries the key
(``clean_additional`` returns ``None`` for an empty list, and the REST door removes the key), so
its stored JSON, its signature and every reader's output are byte-identical to before.

The key is not in the portable-config registry, so an agent-pack import never writes it and an
export never carries it (a subscriber's servers are their own).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import logging

from trusted_data_agent.core.log_once import warn_once
from trusted_data_agent.core.tool_identity import (
    parse as _parse,
    qualify as _qualify,
    turn_aliases as _turn_aliases,
    turn_server_for as _turn_server_for,
    valid_alias,
)

logger = logging.getLogger("quart.app")

PRIMARY_KEY = "mcpServerId"
ADDITIONAL_KEY = "additionalMcpServers"
ADDITIONAL_CLASSIFICATION_KEY = "additionalMcpClassification"
MAX_ADDITIONAL = 8
MAX_SELECTION = 2000


@dataclass(frozen=True)
class AgentServer:
    server_id: str
    alias: Optional[str]            # None = the primary server
    tools: Tuple[str, ...]          # the selection, the server's own names ("*" = all)
    prompts: Tuple[str, ...]

    @property
    def primary(self) -> bool:
        return self.alias is None

    def name_for(self, base: str) -> str:
        """The name the agent sees for the server's tool or prompt ``base``."""
        return _qualify(self.alias, base)

    def selects_tool(self, base: str) -> bool:
        return "*" in self.tools or base in self.tools

    def selects_prompt(self, base: str) -> bool:
        return "*" in self.prompts or base in self.prompts


def _selection(v: Any) -> Tuple[str, ...]:
    return tuple(str(x) for x in v if isinstance(x, str)) if isinstance(v, (list, tuple)) else ()


def servers_of(profile: Optional[Dict[str, Any]]) -> Tuple[AgentServer, ...]:
    """The agent's MCP servers, primary first. A stored entry that is no longer valid (a hand
    edit, a server id that became the primary) is skipped and named once in the log — never
    guessed into shape."""
    p = profile or {}
    out: List[AgentServer] = []
    primary = primary_of(p)
    if primary:
        out.append(AgentServer(str(primary), None,
                               _selection(p.get("tools", p.get("enabled_tools", []))),
                               _selection(p.get("prompts", p.get("enabled_prompts", [])))))
    raw = p.get(ADDITIONAL_KEY)
    if isinstance(raw, list) and raw:
        seen_ids = {str(primary)} if primary else set()
        seen_aliases = set()
        for e in raw:
            if not isinstance(e, dict):
                continue
            sid, alias = str(e.get("id") or ""), e.get("alias")
            if not sid or sid in seen_ids or not valid_alias(alias) or alias in seen_aliases:
                warn_once(logger, f"mcp_additional_skip:{p.get('id')}:{sid}:{alias}",
                          f"agent_mcp_servers: profile {p.get('id')} lists an additional MCP server "
                          f"that is not valid (id {sid!r}, alias {alias!r}) — it is ignored")
                continue
            seen_ids.add(sid)
            seen_aliases.add(alias)
            out.append(AgentServer(sid, alias, _selection(e.get("tools")), _selection(e.get("prompts"))))
    return tuple(out)


def primary_of(profile: Optional[Dict[str, Any]]) -> Optional[str]:
    """The agent's FIRST MCP server id (its tools keep their plain names), or None. The one reader
    of the stored key on the multi-server paths — a new direct read is refused by
    test/test_primary_server_reads.py, so the first server and the additional ones are read
    through one module."""
    v = (profile or {}).get(PRIMARY_KEY)
    return str(v) if v else None


def additional_of(profile: Optional[Dict[str, Any]]) -> Tuple[AgentServer, ...]:
    return tuple(s for s in servers_of(profile) if not s.primary)


def is_multi_server(profile: Optional[Dict[str, Any]]) -> bool:
    return bool(additional_of(profile))


def aliases_of(profile: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    return tuple(s.alias for s in additional_of(profile))


def server_map_of(profile: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """``{alias: server_id}`` of the agent's additional servers — what routing reads."""
    return {s.alias: s.server_id for s in additional_of(profile)}


def server_map_for(profile_id: Optional[str], user_uuid: Optional[str]) -> Dict[str, str]:
    """``server_map_of`` for a stored profile, for callers outside a turn (a REST endpoint).
    Never raises: an unknown profile or user has no additional servers."""
    if not profile_id or not user_uuid:
        return {}
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        return server_map_of(get_config_manager().get_profile(profile_id, user_uuid))
    except Exception:  # noqa: BLE001
        return {}


def server_ids_of(profile: Optional[Dict[str, Any]]) -> Tuple[str, ...]:
    return tuple(s.server_id for s in servers_of(profile))


def route(profile: Optional[Dict[str, Any]], name: str) -> Optional[Tuple[AgentServer, str]]:
    """Which of the agent's servers a tool or prompt name belongs to, and the name that server
    knows it by. ``None`` when the agent has no server for it (no primary, or an alias the agent
    does not have)."""
    servers = servers_of(profile)
    if not servers:
        return None
    ident = _parse(name, [s.alias for s in servers if s.alias])
    for s in servers:
        if s.alias == ident.alias:
            return s, ident.name
    return None


def clean_additional(raw: Any, primary_id: Optional[str],
                     known_server_ids: Optional[Iterable[str]] = None) -> Optional[List[Dict[str, Any]]]:
    """Validate ``additionalMcpServers`` at the door. Returns the cleaned list, or ``None`` when
    it is empty (the caller removes the key: a single-server profile never stores it). Raises
    ``ValueError`` naming the first problem."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"{ADDITIONAL_KEY} must be a list")
    if len(raw) > MAX_ADDITIONAL:
        raise ValueError(f"at most {MAX_ADDITIONAL} additional MCP servers per agent")
    known = set(str(k) for k in known_server_ids) if known_server_ids is not None else None
    seen_ids = {str(primary_id)} if primary_id else set()
    seen_aliases = set()
    out: List[Dict[str, Any]] = []
    for i, e in enumerate(raw, 1):
        if not isinstance(e, dict):
            raise ValueError(f"additional MCP server {i} must be an object")
        sid = str(e.get("id") or "").strip()
        alias = e.get("alias")
        if not sid:
            raise ValueError(f"additional MCP server {i} has no id")
        if primary_id and sid == str(primary_id):
            raise ValueError(f"additional MCP server {i} is the agent's primary server")
        if sid in seen_ids:
            raise ValueError(f"MCP server {sid} is listed twice")
        if known is not None and sid not in known:
            raise ValueError(f"MCP server {sid} is not one of your MCP servers")
        if not valid_alias(alias):
            raise ValueError(f"additional MCP server {i}: the short name {alias!r} must be lower-case "
                             f"letters and digits (single underscores between them), start with a "
                             f"letter, at most 12 characters")
        if alias in seen_aliases:
            raise ValueError(f"the short name {alias!r} is used twice")
        entry: Dict[str, Any] = {"id": sid, "alias": alias}
        for field in ("tools", "prompts"):
            sel = e.get(field, [])
            if not isinstance(sel, list) or not all(isinstance(x, str) for x in sel):
                raise ValueError(f"additional MCP server {alias}: {field} must be a list of names")
            if len(sel) > MAX_SELECTION:
                raise ValueError(f"additional MCP server {alias}: too many {field}")
            for n in sel:
                if n != "*":
                    _qualify(alias, n)          # a name no provider accepts is refused, never cut
            entry[field] = list(dict.fromkeys(sel))
        seen_ids.add(sid)
        seen_aliases.add(alias)
        out.append(entry)
    return out or None


# ---------------------------------------------------------------------------------------------
# Agent packs (implementation plan stage 2): a multi-server agent travels with ALL its servers.
# Server ids are instance-local, so a manifest names each server by its NAME (exactly as the
# first server has always travelled, `mcpServerName`) and the importer resolves the names to its
# own ids. The first server keeps its plain tool names (a compatibility rule — decision 2.0);
# every other attribute (alias, tool and prompt selection) travels and is signed identically.
# ---------------------------------------------------------------------------------------------

MANIFEST_SERVERS_KEY = "mcpServers"


def manifest_servers(profile: Optional[Dict[str, Any]],
                     names_by_id: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
    """The portable form of an agent's servers for a pack manifest: every server, the first
    first, by NAME with its alias (None for the first) and its own tool/prompt selection. None
    for a single-server agent — its manifest stays exactly as it was."""
    servers = servers_of(profile)
    if len(servers) < 2:
        return None
    names = {str(k): v for k, v in (names_by_id or {}).items()}
    return [{"name": names.get(s.server_id), "alias": s.alias,
             "tools": list(s.tools), "prompts": list(s.prompts)} for s in servers]


def resolve_manifest_servers(entries: Any, primary_id: Optional[str], name_to_id: Dict[str, str],
                             alias_bindings: Optional[Dict[str, str]] = None,
                             owned: Optional[Iterable[str]] = None
                             ) -> Tuple[Optional[List[Dict[str, Any]]], List[str]]:
    """A manifest's ADDITIONAL servers (``mcpServers`` entries with an alias) resolved to this
    instance's server ids: an explicit binding for the alias first, else the server NAME
    (case-insensitive) among this user's servers. Returns ``(additionalMcpServers | None,
    problems)``. A server that cannot be resolved — or would repeat the first server, repeat
    another, or break the alias rules — is left out and NAMED in ``problems``; it is never bound
    to a guess. Pure."""
    problems: List[str] = []
    if not isinstance(entries, list) or not primary_id:
        return None, problems
    binds = {str(k): str(v) for k, v in (alias_bindings or {}).items() if v}
    names = {str(k).strip().lower(): v for k, v in (name_to_id or {}).items()}
    out: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("alias"):
            continue                      # the first server is bound by the existing rules
        alias, name = str(e.get("alias")), (e.get("name") or "")
        sid = binds.get(alias) or names.get(str(name).strip().lower())
        if not sid:
            problems.append(f"additional MCP server '{name or alias}' ({alias}__…) is not configured "
                            f"on this instance — imported without it; bind it in the profile editor")
            continue
        candidate = out + [{"id": str(sid), "alias": alias,
                            "tools": list(e.get("tools") or []), "prompts": list(e.get("prompts") or [])}]
        try:
            clean_additional(candidate, primary_id, owned)
        except ValueError as exc:
            problems.append(f"additional MCP server '{name or alias}' ({alias}__…) not bound: {exc}")
            continue
        out = candidate
    return (out or None), problems


def disabled_by_selection(profile: Optional[Dict[str, Any]], names: Iterable[str], kind: str,
                          exempt: Iterable[str] = ()) -> List[str]:
    """The names among ``names`` that the agent has NOT selected, each judged by the selection of
    the server it belongs to (``kind``: "tools" or "prompts"). ``TDA_*`` framework tools and the
    ``exempt`` names (platform connector tools — their own governance chain) are never disabled;
    a name that belongs to none of the agent's servers is left alone."""
    exempt = set(exempt or ())
    out = []
    for n in names:
        if not isinstance(n, str) or n.startswith("TDA_") or n in exempt:
            continue
        r = route(profile, n)
        if r is None:
            continue
        srv, base = r
        chosen = srv.selects_tool(base) if kind == "tools" else srv.selects_prompt(base)
        if not chosen:
            out.append(n)
    return out


# ── which server answered a call (transparency, PROP-006 phase 2) ─────────────────────────────
#
# A multi-server agent's Live Status names the server every MCP tool step went to, and why —
# live AND after a reload, so the fact is stamped on the persisted call, never re-derived by a
# surface. The turn's server NAMES are armed at the execution seam beside the alias → server map
# (``tool_identity.set_turn_servers``); a single-server agent arms neither, so ``call_source``
# returns None and nothing is stamped — its turns stay byte-identical.

_TURN_LABELS: "contextvars.ContextVar[Optional[Tuple[Optional[str], Tuple[Tuple[str, str], ...]]]]" = \
    contextvars.ContextVar("agent_mcp_turn_labels", default=None)

#: Why a call went to the server it went to. The model chooses a server by choosing a name: a
#: ``<alias>__<name>`` is that server's tool; a name without a short name is the first server's.
REASON_QUALIFIED = "qualified"
REASON_PRIMARY = "primary"


def set_turn_source_labels(primary_id: Optional[str], names: Optional[Dict[str, str]]):
    """Arm the turn's server names (``{server id: display name}``) and its primary server id.
    Returns the reset token."""
    pairs = tuple(sorted((str(k), str(v)) for k, v in (names or {}).items() if k and v))
    return _TURN_LABELS.set((str(primary_id) if primary_id else None, pairs))


def reset_turn_source_labels(token) -> None:
    try:
        _TURN_LABELS.reset(token)
    except (ValueError, LookupError, RuntimeError):
        pass


def server_label(server_id: Optional[str]) -> Optional[str]:
    """The display name of one of the turn's servers (None when nothing is armed)."""
    if not server_id:
        return None
    labels = _TURN_LABELS.get() or (None, ())
    return dict(labels[1]).get(str(server_id))


def call_source(tool_name: Optional[str], server_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The server an MCP tool call of THIS turn went to: ``{server_id, server, alias, reason}``.

    None for a single-server agent (nothing is armed) and for a call whose server cannot be named
    — never a guess. ``server_id`` is the server the call was actually routed to when the caller
    knows it (the adapter after routing; a LangChain tool's own metadata); otherwise the name
    decides: an alias names its server, a plain name is the first server's. Callers pass MCP tool
    names only — a connector or ``TDA_`` tool never reaches here."""
    if not _turn_aliases() or not isinstance(tool_name, str) or not tool_name:
        return None
    labels = _TURN_LABELS.get() or (None, ())
    primary_id, names = labels[0], dict(labels[1])
    ident = _parse(tool_name)
    if ident.alias:
        sid, reason = (server_id or _turn_server_for(ident.alias)), REASON_QUALIFIED
    else:
        sid, reason = (server_id or primary_id), REASON_PRIMARY
    if not sid:
        return None
    sid = str(sid)
    return {"server_id": sid, "server": names.get(sid) or sid, "alias": ident.alias, "reason": reason}
