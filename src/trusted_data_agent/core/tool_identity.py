"""PROP-006 phase 2 — one name per tool across an agent's MCP servers.

An agent may read several MCP servers, and two servers often expose a tool of the same name
(`base_readQuery` on two Teradata systems). Every store keyed by tool name — the catalog, the
profile's selection, Agent Authority's denied tools, the loop guard, the governed surface, the
classification categories, a plan's tools, a champion case — would then hold one of the two and
silently lose the other. So every tool and prompt carries a name that is unique across the
agent's servers (build brief decision 2, assessment §4.2):

* the agent's PRIMARY server keeps its tools' real names — a single-server agent is byte-identical
  and adding a server later renames nothing that exists;
* an ADDITIONAL server's tools are ``<alias>__<name>``, the alias chosen per agent
  (``crm__base_readQuery``). The server is always called with the original name.

ONE function qualifies (``qualify``) and ONE parses (``parse`` / ``base_name``). Every rule that
recognises a tool by name — "is this the SQL tool", "is this a discovery tool", a correction
strategy, preflight — reads the BASE name through ``base_name``; ``test_tool_identity_gate`` fails
the build on a literal comparison that bypasses it, because a rule matched against
``crm__base_readQuery`` would otherwise quietly skip the SQL checks.

Parsing needs the agent's aliases: a primary server may itself expose a name containing ``__``,
and only a known alias makes a prefix a qualifier. The aliases of the turn being run are set once
at the execution seam (``set_turn_aliases``). With none set — every single-server agent, and any
code outside a turn — ``base_name`` returns the name unchanged and ``parse`` finds no alias: the
n = 1 case costs one context-variable read.
"""

from __future__ import annotations

import contextvars
import re
from typing import Iterable, NamedTuple, Optional, Tuple

SEP = "__"
MAX_ALIAS_LEN = 12
# lower-case, starts with a letter, single underscores only, never ending in one — so an alias can
# never contain or run into the separator
ALIAS_RE = re.compile(r"^[a-z](?:[a-z0-9]|_(?=[a-z0-9]))*$")
# the tool-name alphabet every provider accepts for a function (OpenAI, Anthropic, Google, Bedrock)
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ToolId(NamedTuple):
    alias: Optional[str]   # None = the primary server
    name: str              # the name the server knows the tool by


def valid_alias(alias: object) -> bool:
    return isinstance(alias, str) and len(alias) <= MAX_ALIAS_LEN and bool(ALIAS_RE.match(alias))


def qualify(alias: Optional[str], name: str) -> str:
    """The name the agent sees for ``name`` on the server ``alias`` (None = the primary server).

    Raises ``ValueError`` for an invalid alias, or when the qualified name would not be a legal
    function name for every provider — never truncated: two long names cut to one prefix would be
    the silent collision this module exists to prevent."""
    if not alias:
        return name
    if not valid_alias(alias):
        raise ValueError(f"invalid server alias {alias!r}: lower-case letters and digits, single "
                         f"underscores between them, starting with a letter, at most {MAX_ALIAS_LEN} characters")
    q = f"{alias}{SEP}{name}"
    if not NAME_RE.match(q):
        raise ValueError(f"the tool name {q!r} is not usable: at most 64 characters of letters, digits, "
                         f"'_' and '-' — choose a shorter alias for this server")
    return q


# The turn's additional servers: alias → MCP server id. Per task, so one user's agent can never
# route another user's call (the 2026-09-20 cross-user reroute class); set once at the execution seam.
_TURN_SERVERS: "contextvars.ContextVar[Tuple[Tuple[str, str], ...]]" = contextvars.ContextVar(
    "tool_identity_servers", default=())


def set_turn_servers(servers):
    """Arm the agent's additional servers for this turn — ``{alias: server_id}`` or an iterable of
    aliases (routing then unknown). Invalid aliases are dropped. Returns the reset token."""
    if isinstance(servers, dict):
        pairs = {a: str(sid) for a, sid in servers.items() if valid_alias(a) and sid}
    else:
        pairs = {a: "" for a in (servers or ()) if valid_alias(a)}
    return _TURN_SERVERS.set(tuple(sorted(pairs.items(), key=lambda kv: (-len(kv[0]), kv[0]))))


# The turn's PRIMARY server, armed at the same seam. Every REST query rewrites the PER-USER
# server id (`switch_profile_context` → `set_user_mcp_server_id`), so two concurrent turns of one
# user on different MCP entries raced on that value and a tool call went to the other turn's
# server (Optimize campaign, 2026-10-27, OC-F1). A context variable is per task and inherited by
# the tasks a turn spawns (a delegated prompt, an orchestrator), so a reader inside the turn
# always sees ITS server; the per-user value is the fallback for callers outside any turn.
_TURN_PRIMARY: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "tool_identity_primary_server", default=None)


def set_turn_primary_server(server_id: Optional[str]):
    """Arm the turn's primary MCP server id. Returns the reset token."""
    return _TURN_PRIMARY.set(str(server_id) if server_id else None)


def reset_turn_primary_server(token) -> None:
    try:
        _TURN_PRIMARY.reset(token)
    except (ValueError, LookupError, RuntimeError, AttributeError, TypeError):   # a foreign or reused token
        pass


def turn_primary_server() -> Optional[str]:
    """The MCP server id this turn was armed with (None outside a turn)."""
    return _TURN_PRIMARY.get()


def set_turn_aliases(aliases: Iterable[str]):
    """Arm aliases only (recognition without routing). Returns the reset token."""
    return set_turn_servers(list(aliases or ()))


def reset_turn_servers(token) -> None:
    try:
        _TURN_SERVERS.reset(token)
    except (ValueError, LookupError, RuntimeError):   # a token from another context, or reused
        pass


reset_turn_aliases = reset_turn_servers


def turn_aliases() -> Tuple[str, ...]:
    return tuple(a for a, _ in _TURN_SERVERS.get())


def turn_server_ids() -> Tuple[str, ...]:
    """The server ids of the turn's ADDITIONAL servers (empty for a single-server agent)."""
    return tuple(sid for _, sid in _TURN_SERVERS.get() if sid)


def turn_server_for(alias: Optional[str]) -> Optional[str]:
    """The MCP server id an alias routes to in this turn (None: not armed or not known)."""
    if not alias:
        return None
    for a, sid in _TURN_SERVERS.get():
        if a == alias:
            return sid or None
    return None


def parse(name: str, aliases: Optional[Iterable[str]] = None) -> ToolId:
    """``crm__base_readQuery`` → ``ToolId("crm", "base_readQuery")`` when ``crm`` is one of the
    agent's aliases; any other name → ``ToolId(None, name)``. ``aliases`` defaults to the turn's."""
    if not isinstance(name, str) or SEP not in name:
        return ToolId(None, name)
    known = turn_aliases() if aliases is None else tuple(a for a in aliases if valid_alias(a))
    for a in sorted(known, key=lambda x: (-len(x), x)):
        prefix = a + SEP
        if name.startswith(prefix) and len(name) > len(prefix):
            return ToolId(a, name[len(prefix):])
    return ToolId(None, name)


def base_name(name: str, aliases: Optional[Iterable[str]] = None) -> str:
    """The name the server knows the tool by — what every name-based rule must compare."""
    return parse(name, aliases).name
