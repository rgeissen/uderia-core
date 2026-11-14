"""Recently used agents as slot suggestions (PROP-007 §7, a second pre-fill source).

Where it makes sense, and only there:
  · a recent agent is a SUGGESTION — a chip the consumer clicks — never a fill (recency
    says nothing about which role an agent should play);
  · only on a slot still open, and only for an agent whose class the slot accepts;
  · after any suggestion the opening agent or team already made, never replacing it;
  · at most a few per slot, most recent first.
Pure: the route reads the user's recent sessions and hands them in."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .slots import slot_of

RECENT_LIMIT = 5        # distinct agents taken from the recent sessions
PER_SLOT = 3            # suggestions (of any source) shown on one slot


def recent_agents(sessions: List[Dict[str, Any]], profiles: List[Dict[str, Any]],
                  limit: int = RECENT_LIMIT) -> List[Dict[str, Any]]:
    """The user's most recently used agents, newest first, as profile dicts. `sessions`
    are summaries ordered newest first (each with `profile_tags_used` and/or
    `profile_tag` / `profile_id`); a tag that no longer resolves is skipped."""
    by_id = {p.get("id"): p for p in profiles or [] if isinstance(p, dict)}
    by_tag = {str(p.get("tag") or "").lstrip("@").upper(): p for p in profiles or [] if isinstance(p, dict) and p.get("tag")}
    out: List[Dict[str, Any]] = []
    seen = set()
    for s in sessions or []:
        if not isinstance(s, dict):
            continue
        refs = []
        used = s.get("profile_tags_used") or []
        if isinstance(used, list):
            refs.extend(reversed(used))          # the last tag used in a session is the most recent
        refs.append(s.get("profile_tag"))
        for ref in refs:
            p = None
            if isinstance(ref, str) and ref:
                p = by_tag.get(ref.lstrip("@").upper())
            if p is None and s.get("profile_id"):
                p = by_id.get(s.get("profile_id"))
            if p is None or p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            out.append(p)
            if len(out) >= limit:
                return out
    return out


def add_recent_suggestions(definition: Dict[str, Any], suggestions: Dict[str, List[Dict[str, Any]]],
                           recent: Optional[List[Dict[str, Any]]], per_slot: int = PER_SLOT) -> None:
    """Append recent agents to the suggestions of every OPEN slot that accepts their class.
    Mutates `suggestions` ({node_id: [{id, tag, recent?}]})."""
    if not recent:
        return
    for n in definition.get("nodes", []) or []:
        slot = slot_of(n)
        if not slot or (n.get("data") or {}).get("profile_id"):
            continue
        accepts = slot.get("accepts") or []
        row = suggestions.setdefault(n["id"], [])
        have = {c.get("id") for c in row}
        for p in recent:
            if len(row) >= per_slot:
                break
            if p.get("id") in have:
                continue
            if accepts and p.get("profile_type") not in accepts:
                continue
            row.append({"id": p.get("id"), "tag": p.get("tag"), "recent": True})
            have.add(p.get("id"))
        if not row:
            suggestions.pop(n["id"], None)
