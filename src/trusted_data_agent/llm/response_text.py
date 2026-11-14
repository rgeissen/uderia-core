"""The model's ANSWER, with its own reasoning left out.

LangChain hands a chat reply back as `.content`, and the SHAPE of that depends on
whether the model reasoned:

    thinking off  ->  "Here is the SQL ..."                       (a plain string)
    thinking off  ->  [{"type": "text", "text": "Here is ..."}]   (Gemini: still a list)
    thinking on   ->  [{"type": "thinking", ...},
                       {"type": "text", "text": "Here is ..."}]   (reasoning FIRST)

A caller that treats that list as a string does one of three things, all wrong:
leaks the model's private reasoning into the user's answer, reports a length of
"2" for a 500-word reply, or crashes (`json.loads` on a list).  Measured
2026-09-09 on `gemini-3.5-flash` and `gemini-3.7-flash`: with thinking enabled
`.content` is exactly `['thinking', 'text']`.

This is the ONE normaliser, so a site cannot handle the shape differently from
its neighbours — the defect that had `conversation_agent` doing it correctly
while `extensions/base` and `session_name_generator` did not.

Conservative by construction: a part is kept only if it is plainly TEXT — typed
`text`, a bare string, or an untyped mapping carrying `text`.  Anything with
another type is dropped, so a reasoning part a future provider names differently
(`reasoning`, `thought`, `redacted_thinking`) can never leak through a name this
module has not heard of.
"""
from __future__ import annotations

from typing import Any

#: Joins the kept parts. Newline (not a space) so a multi-part answer keeps its
#: paragraph breaks — matches what `conversation_agent` has always done.
_JOIN = "\n"


def text_from_content(content: Any) -> str:
    """Return only the answer text carried by a LangChain `.content` value."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            kind = block.get("type")
            if kind == "text" or (kind is None and "text" in block):
                parts.append(str(block.get("text") or ""))
            # every other type — thinking, reasoning, tool_use, image — is not
            # the answer and is deliberately dropped.
    return _JOIN.join(p for p in parts if p)


def response_text(response: Any) -> str:
    """`text_from_content` for a whole LangChain response object."""
    if response is None:
        return ""
    if hasattr(response, "content"):
        return text_from_content(response.content)
    return text_from_content(response)
