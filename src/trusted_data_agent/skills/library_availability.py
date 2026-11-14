"""Which library sections the agent's own database can run.

A progressive skill whose sections hold executed SQL (``teradata-patterns``) records, per
statement, the database functions the statement calls (``index[].requires``: statement name →
function names, derived by executing the section — never a hand-kept list). This module
compares that with what the agent's own database has, read through the same catalog provider
the Database Navigator uses (``dbcatalog.agent_source``), and decides per section:

* every statement can run                    → shown as is;
* some statements cannot run                 → shown, with those statements named;
* more than half of the statements cannot    → hidden from the root page; still readable,
                                               with a warning naming what is missing.

Content is never refused on this inference: a probe that is wrong must cost a warning, not a
capability. Nothing is decided without a probe: until one succeeded for the agent's source the
root page is unfiltered (fail open), so a first turn, a probe failure, or a source that is not
Teradata behaves exactly as before.

Two halves, so the per-turn root rendering never waits on a database: ``probe`` (async, from
the library tool) fills a process cache; ``filter_root`` (sync, at skill injection) only reads it.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger("quart.app")

CACHE_TTL_SECONDS = 6 * 3600

# key → (time, available functions, source label)
_CACHE: Dict[Tuple[str, str, str], Tuple[float, Set[str], str]] = {}


def source_key(profile: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    """What decides the answer: the profile, its MCP server and the Navigator's chosen source."""
    from trusted_data_agent.core.agent_mcp_servers import primary_of
    p = profile or {}
    nav = (((p.get("componentConfig") or {}).get("database_navigator") or {}).get("sourceId")) or ""
    return (str(p.get("id") or ""), str(primary_of(p) or ""), str(nav))


def required_functions(index: Iterable[Dict[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for e in index or []:
        for fns in (e.get("requires") or {}).values():
            out.update(str(f).upper() for f in fns or [])
    return out


def section_status(entry: Dict[str, Any], available: Optional[Set[str]]) -> Dict[str, Any]:
    """``{"total", "blocked": {statement: [missing functions]}, "hidden"}`` — pure.
    ``available`` None means unknown: nothing is blocked."""
    total = int(entry.get("statements") or 0)
    blocked: Dict[str, List[str]] = {}
    if available is not None:
        for stmt, fns in (entry.get("requires") or {}).items():
            missing = sorted(str(f).upper() for f in fns or [] if str(f).upper() not in available)
            if missing:
                blocked[stmt] = missing
    return {"total": total, "blocked": blocked, "hidden": bool(total) and len(blocked) * 2 > total}


def cached(profile: Optional[Dict[str, Any]]) -> Optional[Tuple[Set[str], str]]:
    hit = _CACHE.get(source_key(profile))
    if not hit or time.time() - hit[0] > CACHE_TTL_SECONDS:
        return None
    return hit[1], hit[2]


def remember(profile: Optional[Dict[str, Any]], available: Set[str], label: str) -> None:
    _CACHE[source_key(profile)] = (time.time(), set(available), label)


async def probe(context: Dict[str, Any], index: List[Dict[str, Any]]) -> Optional[Tuple[Set[str], str]]:
    """``(available, source label)`` for the agent's own database, cached; None when it cannot
    be told (no source, not Teradata, the read failed). Never raises."""
    profile = context.get("profile_config") or {}
    hit = cached(profile)
    if hit is not None:
        return hit
    names = required_functions(index)
    if not names:
        return None
    try:
        from trusted_data_agent.dbcatalog.agent_source import agent_profile, agent_provider
        svc, provider, res = await agent_provider(context)
        if provider is None:
            return None
        available = await provider.existing_functions(names)
        if available is None:
            return None
        label = ""
        for s in res.get("sources") or []:
            if s.get("id") == res.get("default"):
                label = str(s.get("label") or s.get("name") or "")
        label = label or "your database"
        remember(agent_profile(context) or profile, available, label)
        hidden = [e.get("anchor") for e in index if section_status(e, available)["hidden"]]
        logger.info(f"skill library: {label} has {len(available & names)} of {len(names)} functions "
                    f"the library calls; {len(hidden)} section(s) left off the root page"
                    + (f" ({', '.join(hidden)})" if hidden else ""))
        return available, label
    except Exception as exc:  # noqa: BLE001
        logger.info(f"skill library: function availability not determined ({exc}) — not filtering")
        return None


def filter_root(content: str, index: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> str:
    """The root page with the sections the agent's database cannot run removed from its list,
    and one line saying so. Unchanged until a probe succeeded for this agent's source."""
    hit = cached(profile)
    if hit is None or not index:
        return content
    available, label = hit
    hidden = [e["anchor"] for e in index if section_status(e, available)["hidden"]]
    if not hidden:
        return content
    out = content
    for a in hidden:
        out = re.sub(rf"(?m)^- `{re.escape(a)}` — .*\n?", "", out)
    note = (f"\n_{len(hidden)} section(s) are not listed because {label} lacks the functions most of "
            f"their statements call; `list_sections` names them._\n")
    end = "<!-- index:end -->"
    return out.replace(end, note + end, 1) if end in out else out + note
