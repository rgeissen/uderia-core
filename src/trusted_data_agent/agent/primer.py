"""Session primer — the one rule for whether a primer turn may act.

A session primer is a statement the platform sends to an agent before any user input.
Two legitimate uses exist and they want opposite engine behaviour:

* a WARM-UP ("Educate yourself on the table DDLs in fitness_db") is a question the
  agent should answer with its tools, so the session starts with real context;
* a BRIEFING (the policing pack's analyst primers) is context the agent should absorb
  and acknowledge — a ReAct agent handed a briefing with its tools bound reads it as a
  task and goes exploring (seen on production: seven tool calls, 200k input tokens and
  $0.05 before the user had asked anything).

The choice is the primer author's, declared on the primer itself (``allow_tools``), and
it is enforced at the dispatch — never left to prose in the primer, which a weak model
ignores. Absent means WARM-UP: every primer written before the key existed keeps the
behaviour it was written for.
"""
from __future__ import annotations

from typing import Any

#: The system-message a briefing turn emits, so the Live Status trace says why an
#: agent that normally carries tools produced a direct answer.
BRIEFING_NOTE = ("Session primer is a briefing — no tools are offered on this turn "
                 "(set the primer's 'allow tools' option to make it a warm-up).")


def primer_allows_tools(profile: dict | None) -> bool:
    """Does this profile's session primer permit tool calls on the primer turn?

    ``True`` for a legacy string primer, an absent primer, or a dict without the key
    (the pre-existing behaviour); ``False`` only when the primer says ``allow_tools``
    is false. Never raises — a malformed block reads as the default.
    """
    try:
        sp = (profile or {}).get("session_primer")
    except Exception:  # noqa: BLE001
        return True
    if not isinstance(sp, dict):
        return True
    v = sp.get("allow_tools")
    if v is None:
        return True
    return bool(v)


def is_briefing_turn(profile: dict | None, is_session_primer: Any) -> bool:
    """A primer turn whose primer withholds tools. Anything else is an ordinary turn."""
    return bool(is_session_primer) and not primer_allows_tools(profile)
