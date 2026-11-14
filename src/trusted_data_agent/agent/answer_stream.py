"""The Governed Stream (S1) — the per-turn answer stream every engine writes to.

One object per turn, created at the ONE seam every engine passes through
(`execution_service.run_agent_execution`) and handed down: the four PlanExecutor
engines read it as `executor.answer_stream`, the genie coordinator as its own
`answer_stream`. Engines only ever call `delta(text)` / `reset()` — everything else
(coalescing, ordering, the in-flight guard, partial persistence) lives here, so the
five lanes cannot drift.

Contract to the frontend (`answer_delta` SSE events, in `seq` order):
  {seq, message_index, text}        — append `text` to provisional message `message_index`
  {seq, message_index, reset: true} — withdraw the provisional message; a NEW one may follow
                                      under the new `message_index`
  … `html`, `html_upto` (optional)  — the provisional text's first `html_upto` characters
                                      rendered by THE renderer the final answer uses
                                      (`stream_html`), so the reader watches the answer take
                                      the shape it will keep: the final lands in place instead
                                      of redrawing what they were reading. Absent when the
                                      lane's final is rendered in the browser (genie).
The `final_answer` event stays authoritative: whatever was streamed is REPLACED by it
(the trust gates may have redacted, annotated, rewritten or withheld the answer).

The in-flight guard (`StreamGuard`) applies the SAME PII detector the post-hoc
outbound guardrail applies, on a sliding window with a release lag, and ONLY under an
acting mode (enforce / review) — so what the user sees mid-stream never disagrees
with the final answer about what is redacted. A toxicity finding under an acting mode
withdraws the stream (`reset`) — the final answer will be the withholding notice.

Nothing here can slow or break a turn: `delta()` is synchronous and O(delta), emission
is coalesced onto the loop, every emit failure is logged and dropped.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Optional, Tuple

app_logger = logging.getLogger("quart.app")

EVENT_TYPE = "answer_delta"
PARTIAL_NOTE = "This answer was stopped before it was complete — what arrived is kept as it was."


# ---------------------------------------------------------------------------
# StreamGuard — the in-flight half of the outbound guardrail, sliding window
# ---------------------------------------------------------------------------
class StreamGuard:
    """Pure + deterministic. `feed(text)` returns `(release, halted)`: `release` is the
    text safe to show now (PII masked exactly as `redact_pii` would), holding back the last
    `lag` characters so a span still being generated is never shown raw; `halted` is True
    once a toxicity finding withdrew the stream (nothing is released after that — the
    final answer carries the platform's withholding notice). `flush()` releases the held
    tail at the end. Agreement with the post-hoc scan holds for any PII span shorter than
    the lag (every category the detector knows is)."""

    LAG = 80
    WINDOW = 512

    def __init__(self, *, redact_pii: bool = False, block_toxicity: bool = False,
                 allowed_values=frozenset(), lag: int = LAG, window: int = WINDOW):
        self.redact_pii = bool(redact_pii)
        self.block_toxicity = bool(block_toxicity)
        self.allowed_values = frozenset(allowed_values or ())
        self.lag = int(lag)
        self.window = int(window)
        self._held = ""
        self._released_tail = ""
        self.halted = False
        self.redactions = 0

    @property
    def active(self) -> bool:
        return self.redact_pii or self.block_toxicity

    def reset(self) -> None:
        self._held = ""
        self._released_tail = ""
        self.halted = False

    def _pii(self, text: str):
        if not self.redact_pii or not text:
            return []
        from trusted_data_agent.agent.guardrails.primitives import detect_pii
        spans = detect_pii(text)
        if self.allowed_values:
            spans = [f for f in spans if f[3] not in self.allowed_values]
        return spans

    def _release(self, boundary: int) -> str:
        """Release `held[:boundary]`, clipping the boundary down to the start of any PII
        span it would cut through, masking the spans inside the released part."""
        if boundary <= 0:
            return ""
        spans = self._pii(self._held)
        for _label, s, e, _v in spans:
            if s < boundary < e:
                boundary = s
        if boundary <= 0:
            return ""
        out = self._held[:boundary]
        inside = [sp for sp in spans if sp[2] <= boundary]
        if inside:
            from trusted_data_agent.agent.guardrails.primitives import redact_pii
            out = redact_pii(out, inside)
            self.redactions += len(inside)
        self._held = self._held[boundary:]
        self._released_tail = (self._released_tail + out)[-self.window:]
        return out

    def feed(self, text: str) -> Tuple[str, bool]:
        if self.halted:
            return "", True
        if not self.active:
            return text or "", False
        self._held += text or ""
        if self.block_toxicity:
            from trusted_data_agent.agent.guardrails.primitives import detect_toxicity
            if detect_toxicity(self._released_tail + self._held):
                self.halted = True
                self._held = ""
                return "", True
        return self._release(len(self._held) - self.lag), False

    def flush(self) -> str:
        if self.halted or not self.active:
            return ""
        return self._release(len(self._held))


# ---------------------------------------------------------------------------
# AnswerStream — ordered, coalesced delivery of provisional answer text
# ---------------------------------------------------------------------------
class AnswerStream:
    """The engines' delta sink for one turn. `emit(payload, event_type)` is the caller's
    async SSE emitter (the same `event_handler` every other event takes)."""

    def __init__(self, emit: Callable[[dict, str], Awaitable[None]], session_id: str, *,
                 guard: Optional[StreamGuard] = None, flush_ms: int = 40, turn_id=None,
                 render_mode: Optional[str] = "markdown"):
        self._emit = emit
        # How the lane's FINAL answer renders this text: "markdown" (the formatter's
        # markdown renderer — Ideate, Focus, ReAct), "direct_answer" (the Optimize report's
        # sentence, set by JsonFieldSink), None (the browser renders it — genie).
        self.render_mode = render_mode
        self._html_upto = 0
        self._last_html: Optional[str] = None
        self.session_id = session_id
        self.guard = guard or StreamGuard()
        self.flush_ms = max(0, int(flush_ms))
        self.turn_id = turn_id
        self.seq = 0
        self.message_index = 0
        self.raw_text = ""          # everything the model produced for the current message
        self.delivered_text = ""    # what the guard released (what the user has seen)
        self.deltas = 0             # provider deltas received (all messages)
        self.emitted = 0            # answer_delta events sent
        self.halted = False
        self.provider_streamed = True
        self.started_at = time.monotonic()
        self.first_delta_at: Optional[float] = None
        self._ops: List[Tuple[str, Any]] = []
        self._timer = None
        self._tasks: set = set()     # strong refs: an unreferenced task can be collected mid-flight
        self._lock = asyncio.Lock()
        self._closed = False
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # -- the sink contract -------------------------------------------------
    def delta(self, text: str) -> None:
        if self._closed or not text:
            return
        if self.first_delta_at is None:
            self.first_delta_at = time.monotonic()
        self.deltas += 1
        self.raw_text += text
        out, halted = self.guard.feed(text)
        if halted:
            if not self.halted:
                self.halted = True
                self._withdraw()
            return
        if out:
            self.delivered_text += out
            self._ops.append(("text", out))
            self._schedule()

    def reset(self) -> None:
        """Withdraw the provisional message (a retry, a tool-call message, a halt)."""
        if self._closed:
            return
        self.raw_text = ""
        self.delivered_text = ""
        self.guard.reset()
        self.halted = False
        self._html_upto = 0
        self._last_html = None
        self._withdraw()

    def note(self, kind: str) -> None:
        if kind == "provider_no_stream":
            self.provider_streamed = False

    def set_render_mode(self, mode: Optional[str]) -> None:
        if mode != self.render_mode:
            self.render_mode = mode
            self._html_upto = 0
            self._last_html = None

    def _html_fields(self) -> dict:
        """The delivered text rendered as the final will render it — only when it changed."""
        if not self.render_mode:
            return {}
        try:
            html, upto = stream_html(self.delivered_text, self.render_mode)
        except Exception as exc:  # noqa: BLE001 — a lost render is not a lost answer
            app_logger.debug(f"[answer_stream] render skipped: {exc}")
            return {}
        if html == self._last_html and upto == self._html_upto:
            return {}
        self._last_html, self._html_upto = html, upto
        return {"html": html, "html_upto": upto}

    # -- lifecycle -----------------------------------------------------------
    def _withdraw(self) -> None:
        self.message_index += 1
        self._ops.append(("reset", None))
        self._schedule()

    def _schedule(self) -> None:
        if self._timer is not None or self._loop is None:
            return
        self._timer = self._loop.call_later(self.flush_ms / 1000.0, self._fire)

    def _fire(self) -> None:
        self._timer = None
        if self._loop is not None:
            t = self._loop.create_task(self.flush())
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def flush(self) -> None:
        """Emit every pending op in order, coalescing consecutive text ops."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        async with self._lock:
            ops, self._ops = self._ops, []
            batch: List[str] = []
            for kind, val in ops + [("end", None)]:
                if kind == "text":
                    batch.append(val)
                    continue
                if batch:
                    body = {"text": "".join(batch)}
                    if kind == "end":        # the current message — never one a reset withdrew
                        body.update(self._html_fields())
                    await self._send(body)
                    batch = []
                if kind == "reset":
                    await self._send({"reset": True})

    async def _send(self, body: dict) -> None:
        self.seq += 1
        payload = {"seq": self.seq, "message_index": self.message_index,
                   "session_id": self.session_id, "turn_id": self.turn_id, **body}
        try:
            await self._emit(payload, EVENT_TYPE)
            self.emitted += 1
        except Exception as exc:  # noqa: BLE001 — a lost delta is not a lost answer
            app_logger.debug(f"[answer_stream] emit failed (ignored): {exc}")

    async def finish(self) -> None:
        """Release the guard's held tail and drain — called BEFORE the final answer is
        emitted so the authoritative message never overtakes a provisional delta."""
        if self._closed:
            return
        tail = self.guard.flush()
        if tail:
            self.delivered_text += tail
            self._ops.append(("text", tail))
        await self.flush()

    async def close(self) -> None:
        await self.finish()
        self._closed = True

    def summary(self) -> dict:
        """What the turn's assurance surface says about the stream — facts, never a verdict."""
        ttfb = None if self.first_delta_at is None else round((self.first_delta_at - self.started_at) * 1000)
        return {"deltas": self.deltas, "events": self.emitted, "messages": self.message_index + 1,
                "provider_streamed": self.provider_streamed, "halted": self.halted,
                "redactions": self.guard.redactions, "first_delta_ms": ttfb,
                "guarded": self.guard.active}


# ---------------------------------------------------------------------------
# JsonFieldSink — stream ONE string field out of a JSON answer as it is written
# ---------------------------------------------------------------------------
class JsonFieldSink:
    """A sink adapter for calls whose answer is a JSON object (the Optimize final report:
    `{"direct_answer": "...", ...}`). Forwards the CONTENT of `field` to the inner sink as
    the model writes it — JSON escapes decoded, a partial escape held back — and nothing
    else, so the reader sees the sentence and never the braces. If the reply does not
    start as JSON at all, the whole text is forwarded as prose (a model that answered
    plainly is not punished). Deterministic, O(delta)."""

    def __init__(self, inner, field: str = "direct_answer"):
        self.inner = inner
        self.field = field
        self._buf = ""          # text seen before the field's value starts (bounded)
        self._state = "seek"    # seek → value → done | prose
        self._pending = ""      # incomplete escape sequence at the end of the last delta
        self._marker = None
        self.forwarded = 0

    def _find_marker(self):
        import re
        if self._marker is None:
            self._marker = re.compile(r'"' + re.escape(self.field) + r'"\s*:\s*"')
        return self._marker.search(self._buf)

    def delta(self, text: str) -> None:
        if not text or self._state == "done":
            return
        if self._state == "prose":
            self.inner.delta(text)
            return
        if self._state == "seek":
            self._buf += text
            lead = self._buf.lstrip()
            if lead and lead[0] not in "{`[":
                # not JSON — forward everything seen so far and stay in prose
                self._state = "prose"
                self._set_mode("markdown")
                self.inner.delta(self._buf)
                self._buf = ""
                return
            m = self._find_marker()
            if not m:
                if len(self._buf) > 4096:
                    self._buf = self._buf[-1024:]
                return
            rest = self._buf[m.end():]
            self._buf = ""
            self._state = "value"
            self._set_mode("direct_answer" if self.field == "direct_answer" else "markdown")
            text = rest
        # state == value: decode JSON string content up to the closing quote
        out, self._pending, closed = _decode_json_string(self._pending + text)
        if out:
            self.forwarded += len(out)
            self.inner.delta(out)
        if closed:
            self._state = "done"

    def _set_mode(self, mode: str) -> None:
        if hasattr(self.inner, "set_render_mode"):
            self.inner.set_render_mode(mode)

    def reset(self) -> None:
        self._buf = ""
        self._pending = ""
        self._state = "seek"
        self.inner.reset()

    def note(self, kind: str) -> None:
        if hasattr(self.inner, "note"):
            self.inner.note(kind)


# ---------------------------------------------------------------------------
# stream_html — the provisional text rendered by the FINAL answer's renderer
# ---------------------------------------------------------------------------
import re as _re
_FENCE_OPEN = _re.compile(r'^(.*?)(```\s*(\w*))$')


def _stable_prefix(text: str) -> str:
    """The part of `text` the markdown renderer will render the same way once more text
    arrives: complete lines only, minus an unclosed code fence (its body would vanish until
    it closes) and minus a trailing pipe line that may still become a table header (it
    renders as a paragraph until its separator arrives, then jumps into a table)."""
    cut = text.rfind("\n") + 1
    if cut <= 0:
        return ""
    lines = text[:cut].split("\n")[:-1]
    open_at = None
    for k, line in enumerate(lines):
        if open_at is None:
            if _FENCE_OPEN.match(line.lstrip(" ")):
                open_at = k
        elif line.strip().endswith("```"):
            open_at = None
    if open_at is not None:
        lines = lines[:open_at]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and "|" in lines[-1] and (len(lines) < 2 or "|" not in lines[-2]):
        lines = lines[:-1]
    if not lines:
        return ""
    prefix = "\n".join(lines) + "\n"
    # map back onto the ORIGINAL text so `upto` is an exact character offset into it
    return prefix if text.startswith(prefix) else ""


def stream_html(text: str, mode: str):
    """`(html, upto)` — the first `upto` characters of `text` rendered exactly as the lane's
    final answer renders them (the SAME formatter functions, never a copy). Pure."""
    from trusted_data_agent.agent.formatter import OutputFormatter
    fmt = OutputFormatter(collected_data=[])
    if mode == "direct_answer":
        return (fmt._render_direct_answer(text) if text.strip() else ""), len(text)
    prefix = _stable_prefix(text or "")
    if not prefix:
        return "", 0
    return fmt._render_standard_markdown(prefix), len(prefix)


_JSON_ESC = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def _decode_json_string(s: str):
    """Decode the JSON-string body `s` (no opening quote) up to an unescaped closing quote.
    Returns `(decoded, pending, closed)` — `pending` is a trailing incomplete escape."""
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == '"':
            return "".join(out), "", True
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            return "".join(out), s[i:], False
        e = s[i + 1]
        if e == "u":
            if i + 6 > n:
                return "".join(out), s[i:], False
            try:
                out.append(chr(int(s[i + 2:i + 6], 16)))
            except ValueError:
                out.append(s[i:i + 6])
            i += 6
            continue
        out.append(_JSON_ESC.get(e, e))
        i += 2
    return "".join(out), "", False


# ---------------------------------------------------------------------------
# The ambient stream — for seams too deep to be handed the object (the adapter)
# ---------------------------------------------------------------------------
import contextvars as _cv
_CURRENT: "_cv.ContextVar[Optional[AnswerStream]]" = _cv.ContextVar("uderia_answer_stream", default=None)


def set_current(stream: Optional["AnswerStream"]):
    return _CURRENT.set(stream)


def reset_current(token) -> None:
    try:
        _CURRENT.reset(token)
    except Exception:  # noqa: BLE001 — a double reset is not an error worth a turn
        pass


def current() -> Optional["AnswerStream"]:
    return _CURRENT.get()


# ---------------------------------------------------------------------------
# Partial persistence — the answer that arrived is kept, never lost
# ---------------------------------------------------------------------------
def partial_html(delivered_text: str) -> str:
    """The stopped answer rendered like any other, with a marker that says so."""
    from trusted_data_agent.agent.formatter import OutputFormatter
    import html as _html
    try:
        body, _ = OutputFormatter(llm_response_text=delivered_text or "", collected_data=[],
                                  original_user_input="", active_prompt_name=None).render()
    except Exception:  # noqa: BLE001
        body = f"<p>{_html.escape(delivered_text or '')}</p>"
    return (f'<div class="answer-partial" data-partial="1">{body}'
            f'<div class="answer-partial-note" role="note">{_html.escape(PARTIAL_NOTE)}</div></div>')


def partial_final_answer(stream: "AnswerStream", *, status: str) -> dict:
    """The `final_answer` payload for a stopped turn — the same shape as a completed one,
    plus `partial`/`status` so the frontend keeps what arrived and marks it."""
    text = stream.delivered_text
    return {"step": "Finished", "final_answer": partial_html(text), "final_answer_text": text,
            "turn_id": stream.turn_id, "session_id": stream.session_id,
            "partial": True, "status": status, "input_tokens": 0, "output_tokens": 0,
            "answer_stream": stream.summary()}


async def persist_partial_turn(stream: "AnswerStream", *, user_uuid: str, session_id: str,
                               user_input: str, profile_tag: str, profile_type: str,
                               status: str, error_message: str = None,
                               provider: str = None, model: str = None) -> Optional[dict]:
    """Persist a stopped/failed turn WITH the partial answer, for engines that have no
    executor to do it (the genie coordinator). Mirrors `PlanExecutor._save_partial_turn_data`'s
    shape (status/is_partial) so reload and the eval layer read it like any other partial
    turn; the delivered text is the FINAL text of the assistant message. Never raises."""
    from trusted_data_agent.core import session_manager
    text = stream.delivered_text
    if not text:
        return None
    try:
        session = await session_manager.get_session(user_uuid, session_id)
        hist = ((session or {}).get("last_turn_data") or {}).get("workflow_history") or []
        turn_number = stream.turn_id or (len(hist) + 1)
        await session_manager.add_message_to_histories(
            user_uuid, session_id, "assistant", content=f"{text}\n\n[{PARTIAL_NOTE}]",
            html_content=partial_html(text), profile_tag=profile_tag)
        turn = {"turn": turn_number, "user_query": user_input, "final_summary_text": text,
                "final_summary": text, "status": status, "is_partial": True, "partial_answer": True,
                "error_message": error_message, "profile_tag": profile_tag, "profile_type": profile_type,
                "timestamp": datetime.now(timezone.utc).isoformat(), "session_id": session_id,
                "provider": provider, "model": model, "turn_input_tokens": 0, "turn_output_tokens": 0,
                "turn_cost": 0.0, "tokens_unknown": True, "execution_trace": [], "system_events": [],
                "answer_stream": stream.summary()}
        await session_manager.update_last_turn_data(user_uuid, session_id, turn)
        return turn
    except Exception as exc:  # noqa: BLE001
        app_logger.warning(f"[answer_stream] partial persistence failed: {exc}")
        return None
