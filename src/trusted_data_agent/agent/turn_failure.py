"""What a failed turn tells the user — one honest sentence, never the raw error.

FOUND (trust campaign C18, 2026-10-29): the campaign's provider account ran out of credits and
the ReAct executor's catch-all delivered `I encountered an error while processing your request:
{str(e)}` — the provider's raw JSON as the answer — while every assurance station read quiet.
The coordinator's catch-all did the same (`Error during coordination: {str(e)}`); Optimize ended
the turn with no answer at all.

``answer(exc)`` is the sentence every engine's failure path delivers. It names the failure CLASS
(read through the resilience envelope's ``classify`` and ``out_of_credits``, the platform's one
reading of provider errors) and what the user can do; the raw error stays in the server log and
in the turn record (``error``), never in the answer. A failed turn is recorded as failed, and
JUDGED's ``clean_termination`` grades it — the reading that makes I6 loud.
"""
from __future__ import annotations

from typing import Dict

# reason → the clause after "The language model provider did not answer: "
_PROVIDER_WORDS: Dict[str, str] = {
    "credits": "the provider account has no credits left — an administrator must top it up or "
               "switch this agent to another model",
    "auth": "the provider rejected the credentials — an administrator must check this agent's "
            "model configuration",
    "rate_limit": "the provider is rate-limiting requests — try again in a minute",
    "overloaded": "the provider is overloaded — try again in a minute",
    "server_error": "the provider returned a server error — try again in a minute",
    "timeout": "the provider did not respond in time — try again",
    "connection": "the provider could not be reached — try again",
    "context_too_long": "the request was larger than the model accepts — start a new conversation "
                        "or ask a narrower question",
    "not_found": "the configured model was not found at the provider — an administrator must check "
                 "this agent's model configuration",
    "bad_request": "the provider rejected the request — an administrator can find the reason in the "
                   "server log",
    "stream_ceiling": "the answer exceeded the platform's time limit for one reply",
}

PROVIDER_PREFIX = "The language model provider did not answer: "
NOTHING_DONE = " Nothing was computed for this question."


class DataSourceUnavailable(Exception):
    """The agent's data source (an MCP server) could not be reached or answered with an error
    while its tools were being loaded. Raised by the tool loader so the turn's sentence names
    the source — not the language model provider (found 2026-11-01: an unreachable MCP server
    read "the language model provider did not answer: the provider could not be reached")."""

    def __init__(self, server: str = "", detail: str = ""):
        self.server = str(server or "")
        self.detail = str(detail or "")
        super().__init__(f"data source {self.server or '(unnamed)'} unavailable: {self.detail}")


# The MCP client libraries' frames mark a failure as the MCP layer's — third-party only, so a
# defect in Uderia's own code is never read as a data source being down.
_MCP_FRAMES = ("/site-packages/mcp/", "/langchain_mcp_adapters/")


def _chain(exc: BaseException):
    """``exc``, its causes and contexts, and the members of any exception group — each once."""
    seen, todo = set(), [exc]
    while todo:
        e = todo.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        todo.extend([e.__cause__, e.__context__])
        todo.extend(list(getattr(e, "exceptions", None) or []))


def source_failure(exc: BaseException):
    """The failure as the data source's: ``(True, server name or "")``, else ``(False, "")``."""
    for e in _chain(exc):
        if isinstance(e, DataSourceUnavailable):
            return True, e.server
    for e in _chain(exc):
        tb = e.__traceback__
        while tb is not None:
            fn = tb.tb_frame.f_code.co_filename.replace("\\", "/")
            if any(m in fn for m in _MCP_FRAMES):
                return True, ""
            tb = tb.tb_next
    return False, ""


def describe(exc: BaseException) -> Dict[str, object]:
    """``{reason, status, provider}`` — the failure's class, read the platform's one way. A
    data source's failure is ``data_source`` and never the provider's: it must not read as a
    model failure, nor count against the model provider."""
    from trusted_data_agent.llm import resilience as R
    is_source, server = source_failure(exc)
    if is_source:
        return {"reason": "data_source", "status": None, "provider": False, "source": server}
    if R.out_of_credits(exc):
        return {"reason": "credits", "status": R._status_of(exc) or 402, "provider": True}
    c = R.classify(exc)
    return {"reason": c.reason, "status": c.status, "provider": c.reason in _PROVIDER_WORDS}


def answer(exc: BaseException) -> str:
    """The honest answer for a turn that failed on ``exc``. Never contains ``str(exc)``."""
    try:
        d = describe(exc)
        if d["reason"] == "data_source":
            name = f" ({d['source']})" if d.get("source") else ""
            return ("The agent's data source" + name + " could not be reached — its MCP server did not "
                    "answer. Check that the server is running and reachable from Uderia, then try again."
                    + NOTHING_DONE)
        words = _PROVIDER_WORDS.get(str(d["reason"]))
        if words:
            code = f" (HTTP {d['status']})" if d.get("status") else ""
            return PROVIDER_PREFIX + words + code + "." + NOTHING_DONE
    except Exception:  # noqa: BLE001 — the failure path must always produce its sentence
        pass
    return ("The request stopped on an internal error (" + type(exc).__name__ + ")." + NOTHING_DONE
            + " The details are in the server log.")
