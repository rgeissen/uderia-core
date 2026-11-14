"""
Which MCP tools and prompts an agent may use — the ONE rule for reading and changing it.

An agent's selection is stored on its profile: ``tools`` / ``prompts`` for its first MCP server
(``["*"]`` = all of them, a list = exactly those, ``[]`` = none) and, for an agent with more
servers, the same two lists on each ``additionalMcpServers`` entry. Every surface that shows or
changes the selection goes through here, so the profile editor, the resource panel and the
runtime cannot disagree about it:

* ``is_selected``   — does the agent use this tool / prompt? (``None`` = the name belongs to none
  of the agent's servers — not a question the selection answers)
* ``toggle_updates`` — the profile fields to write so one name is switched on or off.

Before this module the resource panel's eye icon changed only a process-wide in-memory list:
the prompt looked disabled in the panel, stayed enabled on the profile, and came back on the next
profile switch or restart (and for every other user it was disabled meanwhile).
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Iterable, List, Optional

from trusted_data_agent.core.agent_mcp_servers import primary_of

KINDS = ("tools", "prompts")


class SelectionUnknown(ValueError):
    """The selection is "all" (``*``) and the full list of names is not known, so one name
    cannot be removed from it without guessing what the rest are."""


def _as_list(v: Any) -> List[str]:
    return [str(x) for x in v if isinstance(x, str)] if isinstance(v, (list, tuple)) else []


def toggle_selection(current: Any, base: str, enable: bool, universe: Iterable[str] = ()) -> List[str]:
    """The new selection after switching ``base`` on or off.

    ``universe`` is every name the server offers (its own names). Switching the last missing
    name ON collapses to ``["*"]`` — the editor's own convention for "all selected". Switching a
    name OFF from ``["*"]`` expands to the universe minus that name; without a universe that is
    refused (:class:`SelectionUnknown`) rather than guessed.
    """
    cur = _as_list(current)
    uni = [str(n) for n in (universe or ()) if isinstance(n, str) and n and n != "*"]
    wildcard = "*" in cur
    if enable:
        if wildcard:
            return ["*"]
        new = cur + ([base] if base not in cur else [])
        if uni and set(uni) <= set(new):
            return ["*"]
        return new
    if wildcard:
        if not uni:
            raise SelectionUnknown(
                f"'{base}' cannot be switched off: the agent uses everything its server offers and "
                "the full list is not known here — open the profile editor instead")
        return [n for n in uni if n != base]
    return [n for n in cur if n != base]


def is_selected(profile: Optional[Dict[str, Any]], name: str, kind: str) -> Optional[bool]:
    """Does the agent use ``name`` (``kind``: "tools" or "prompts")? ``None`` when the name
    belongs to none of the agent's servers."""
    from trusted_data_agent.core.agent_mcp_servers import route
    if kind not in KINDS or not name:
        return None
    r = route(profile, name)
    if r is None:
        return None
    srv, base = r
    return srv.selects_tool(base) if kind == "tools" else srv.selects_prompt(base)


def toggle_updates(profile: Dict[str, Any], name: str, kind: str, enable: bool,
                   universe_for: Callable[[Any], Iterable[str]]) -> Dict[str, Any]:
    """The profile fields to write so ``name`` is switched on (``enable``) or off.

    ``universe_for(server)`` returns every name (the server's own) that server offers for
    ``kind`` — used only when a ``["*"]`` selection has to be expanded or collapsed.
    Raises ``KeyError`` when the name belongs to none of the agent's servers.
    """
    from trusted_data_agent.core.agent_mcp_servers import route, ADDITIONAL_KEY
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    r = route(profile, name)
    if r is None:
        raise KeyError(name)
    srv, base = r
    if srv.primary:
        cur = profile.get(kind, profile.get(f"enabled_{kind}", []))
        return {kind: toggle_selection(cur, base, enable, universe_for(srv))}
    entries = copy.deepcopy(profile.get(ADDITIONAL_KEY) or [])
    for e in entries:
        if isinstance(e, dict) and str(e.get("id") or "") == srv.server_id and e.get("alias") == srv.alias:
            e[kind] = toggle_selection(e.get(kind), base, enable, universe_for(srv))
            return {ADDITIONAL_KEY: entries}
    raise KeyError(name)


def snapshot_names(snapshot: Any, kind: str) -> List[str]:
    """Every name in a classification snapshot's ``kind`` categories (a server's own names)."""
    out: List[str] = []
    cats = (snapshot or {}).get(kind) if isinstance(snapshot, dict) else None
    if isinstance(cats, dict):
        for items in cats.values():
            for it in items if isinstance(items, list) else []:
                n = it.get("name") if isinstance(it, dict) else (it if isinstance(it, str) else None)
                if n and n not in out:
                    out.append(n)
    return out


def _classification_for(profile: Dict[str, Any], user_uuid: Optional[str], config_manager) -> Dict[str, Any]:
    """The primary server's classification snapshot — the master's when the profile inherits."""
    pid = profile.get("id")
    target = pid
    if profile.get("inherit_classification"):
        try:
            master = config_manager.get_master_classification_profile_id(
                user_uuid, primary_of(profile), strict=True)
            if master and master != pid:
                mp = config_manager.get_profile(master, user_uuid)
                if mp and primary_of(mp) == primary_of(profile):
                    target = master
        except Exception:  # noqa: BLE001 — fall back to the profile's own snapshot
            target = pid
    try:
        return config_manager.get_profile_classification(target, user_uuid) or {}
    except Exception:  # noqa: BLE001
        return {}


def universe_resolver(profile: Dict[str, Any], kind: str, user_uuid: Optional[str], config_manager,
                      live_names: Iterable[str] = ()) -> Callable[[Any], List[str]]:
    """``universe_for`` for :func:`toggle_updates`: every name a server of this agent offers.

    The primary server's names come from its classification snapshot (the master's when the
    profile inherits), plus ``live_names`` (the runtime catalog, when this profile is the one
    loaded) routed to the primary. An additional server's names come from its own snapshot.
    """
    from trusted_data_agent.core.agent_mcp_servers import ADDITIONAL_CLASSIFICATION_KEY, route
    live = [n for n in (live_names or ()) if isinstance(n, str)]

    def _for(srv) -> List[str]:
        if srv.primary:
            names = snapshot_names(_classification_for(profile, user_uuid, config_manager), kind)
            for n in live:
                r = route(profile, n)
                if r and r[0].primary and r[1] not in names:
                    names.append(r[1])
            return [n for n in names if not n.startswith("TDA_")]
        snaps = profile.get(ADDITIONAL_CLASSIFICATION_KEY) or {}
        return snapshot_names(snaps.get(srv.server_id) if isinstance(snaps, dict) else None, kind)
    return _for


class ToggleRefused(ValueError):
    """The toggle cannot be applied; ``status`` is the HTTP status to answer with."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def plan_toggle(profile: Optional[Dict[str, Any]], name: str, kind: str, enable: bool,
                universe_for: Callable[[Any], Iterable[str]]) -> Dict[str, Any]:
    """The profile fields to write, or :class:`ToggleRefused` naming why not.

    Refused: a platform tool (``TDA_*`` — always on, not part of the MCP selection), a name that
    belongs to none of the agent's servers, a name the server does not offer (when its list is
    known), and switching a name off out of an "all" selection whose full list is unknown.
    """
    from trusted_data_agent.core.agent_mcp_servers import route
    label = "tool" if kind == "tools" else "prompt"
    if not profile:
        raise ToggleRefused("Profile not found", 404)
    if not name or not isinstance(name, str):
        raise ToggleRefused(f"Missing {label} name")
    if name.startswith("TDA_"):
        raise ToggleRefused(f"'{name}' is a platform {label} and is always available — it cannot be switched off here")
    r = route(profile, name)
    if r is None:
        raise ToggleRefused(f"'{name}' is not a {label} of any MCP server of this agent", 404)
    srv, base = r
    uni = list(universe_for(srv) or [])
    if uni and base not in uni:
        raise ToggleRefused(f"'{name}' is not a {label} this agent's MCP server offers", 404)
    try:
        return toggle_updates(profile, name, kind, enable, lambda _s: uni)
    except SelectionUnknown as e:
        raise ToggleRefused(str(e), 409)
    except KeyError:
        raise ToggleRefused(f"'{name}' is not a {label} of any MCP server of this agent", 404)


def prompts_listing_unsupported(exc: BaseException) -> bool:
    """Did ``list_prompts`` fail because the server does not offer prompts at all (MCP
    "method not found", -32601) — as opposed to failing while trying (a timeout, a dropped
    connection)? Only the first is a legitimate "no prompts"; the second must be reported,
    or the editor reads an empty list and a save switches every prompt back on."""
    code = getattr(getattr(exc, "error", None), "code", None)
    if code == -32601:
        return True
    return "method not found" in str(exc).lower()


def disabled_prompt_refusal(profile_id: Optional[str], user_uuid: Optional[str], name: str) -> Optional[str]:
    """The sentence refusing to run ``name`` when the agent has it switched off — ``None`` when
    it may run. Fails OPEN: an unreadable profile, a system-default run or a name the agent's
    selection does not cover is never refused here (the catalog's own checks still apply)."""
    if not profile_id or profile_id == "__system_default__" or not user_uuid or not name:
        return None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        profile = get_config_manager().get_profile(profile_id, user_uuid)
    except Exception:  # noqa: BLE001
        return None
    if not profile:
        return None
    try:
        if is_selected(profile, name, "prompts") is False:
            tag = profile.get("tag")
            who = f"@{tag}" if tag else "this agent"
            return (f"The prompt '{name}' is switched off for {who} — enable it in the profile "
                    "(or the resource panel) to run it.")
    except Exception:  # noqa: BLE001
        return None
    return None
