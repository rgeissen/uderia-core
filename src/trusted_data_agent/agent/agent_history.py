"""The conversation history an agent receives — one rule for every engine that hands the stored
chat to a LangChain agent (the ReAct lane, Focus's tool-assisted synthesis, the coordinator).

FOUND (production, demo_walkthrough's @UDERIA session 36c22ee3, 2026-10-29): turns 2 and 3 asked
"What is the uderia prompt caching architecture?" and both answered turn 1's "How can I find the
uderia version?". Focus's synthesis built its own history from `chat_object` and got three things
wrong that the ReAct engine and the coordinator each got right in their own copy:
  * assistant turns are stored under the role `model` (Gemini's convention) and the agent kept
    only `user`/`assistant` — every ANSWER was dropped, so turn 1's question read as still open;
  * the current question was removed by dropping EVERY earlier message with the same text — turn
    3 repeated turn 2, so turn 2's question vanished as well;
  * the priming pair ("You are a helpful assistant." / "Understood.") and messages the user turned
    off (`isValid: false`) were kept.
Three copies of one rule is how one of them drifted; ``build`` is now the only one.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: The session's opening pair (``session_manager``): not conversation.
PRIMING = frozenset({"You are a helpful assistant.", "Understood."})
#: Roles that mean "the assistant said this" — `model` is Gemini's, `ai` LangChain's.
ASSISTANT_ROLES = frozenset({"assistant", "model", "ai"})


def text_of(msg: Dict[str, Any]) -> str:
    """A stored message's text; Gemini's list-shaped content is joined."""
    c = (msg or {}).get("content", "")
    if isinstance(c, list):
        return " ".join(str(p) for p in c if p)
    return str(c or "")


def role_of(msg: Dict[str, Any]) -> Optional[str]:
    """``user`` · ``assistant`` · None (a role an agent does not take: system, tool, …)."""
    r = str((msg or {}).get("role") or "user").lower()
    if r in ASSISTANT_ROLES:
        return "assistant"
    if r == "user":
        return "user"
    return None


def build(chat_object: Optional[List[Dict[str, Any]]], current_query: Optional[str],
          window: int = 10, *, disabled: bool = False) -> List[Dict[str, str]]:
    """The last ``window`` turns' messages as ``[{role, content}]`` with roles normalised.

    The current question — already appended to ``chat_object`` by the time an engine runs — is
    removed ONLY as the trailing user message (the agent adds it itself); an earlier message
    with the same text is history and stays. Priming and turned-off messages are skipped.
    ``disabled`` is the turn's Turn Summaries mode (the Alt key, ``executor.disabled_history``):
    the agent receives no history at all — the coordinator honoured it, the ReAct lane and
    Focus's synthesis did not."""
    if disabled:
        return []
    window = max(0, int(window or 0))
    msgs = list(chat_object or [])[-(window + 1):] if window else []
    cur = (current_query or "").strip()
    if msgs and role_of(msgs[-1]) == "user" and text_of(msgs[-1]).strip() == cur:
        msgs = msgs[:-1]
    out: List[Dict[str, str]] = []
    for m in msgs[-window:] if window else []:
        if m.get("isValid") is False:
            continue
        content = text_of(m)
        if content in PRIMING:
            continue
        role = role_of(m)
        if role is None:
            continue
        out.append({"role": role, "content": content})
    return out
