"""The Outcome Loop (S7) — until the gates pass.

Spec: docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S7. The rubric IS the assurance layer:
no prose rubric to write — the grounding gate's deterministic findings are the grader. When a
finding is one the MODEL can fix (an unsupported claim whose span is known; a reference to a
data source outside the declared scope, with the right sources named; a figure that matches no
query result) the turn gets ONE bounded revision with a targeted note BEFORE delivery, and the
gate re-grades the revision (exact, 0 tokens). A revision that clears the findings is delivered
CLEAN (mark the exception — nothing extra shown); one that does not deliver the honest annotated
FIRST draft, never a second silent try. Both drafts are kept (append-only, sealed).

Pure decisions live here (which findings are revisable · the note · the bound · cleared?); the
executor performs the revision call and the re-grade at the ONE grounding seam every engine
calls. `profile.outcomeConfig {enabled, maxRevisions ∈ {0,1,2}, revisableKinds}` is posture
(signed, downgrade-guarded) under the deployment switch `APP_CONFIG.OUTCOME_LOOP`.
"""
from __future__ import annotations

import contextvars
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

_log = logging.getLogger("quart.app")

KINDS = ("unsupported_claim", "out_of_scope_ref", "ungrounded_number")
MAX_REVISIONS_CEILING = 2
DEFAULT_MAX_REVISIONS = 1
MAX_SPANS_IN_NOTE = 6


def enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "OUTCOME_LOOP", False))
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class OutcomeConfig:
    enabled: bool = False
    max_revisions: int = DEFAULT_MAX_REVISIONS
    kinds: Tuple[str, ...] = KINDS


def parse(profile: Optional[Dict[str, Any]]) -> OutcomeConfig:
    """The profile's `outcomeConfig` → a config. Absent/false = off; maxRevisions clamped to
    [0, 2]; revisableKinds filtered to the known kinds (an unknown kind is ignored, never a
    crash); an empty list = every kind."""
    oc = (profile or {}).get("outcomeConfig")
    if not isinstance(oc, dict) or not oc.get("enabled"):
        return OutcomeConfig(enabled=False)
    try:
        mr = int(oc.get("maxRevisions", DEFAULT_MAX_REVISIONS))
    except (TypeError, ValueError):
        mr = DEFAULT_MAX_REVISIONS
    mr = max(0, min(MAX_REVISIONS_CEILING, mr))
    raw = oc.get("revisableKinds")
    # an explicit list is honoured as written — an EMPTY one means no kind is revisable (the
    # loop is on but nothing qualifies); absent or malformed ⇒ every kind
    kinds = tuple(k for k in raw if k in KINDS) if isinstance(raw, list) else KINDS
    return OutcomeConfig(enabled=True, max_revisions=mr, kinds=kinds)


# the deterministic kinds each judge family adjudicates — a PASS from that judge retires them
_JUDGE_CLEARS = {"scope_faithfulness": ("out_of_scope_ref",), "groundedness": ("unsupported_claim",)}


@dataclass(frozen=True)
class Finding:
    kind: str
    spans: Tuple[str, ...]
    hint: str = ""

    def key(self) -> Tuple[str, Tuple[str, ...]]:
        return (self.kind, tuple(sorted(self.spans)))


def findings_from_event(event: Optional[Dict[str, Any]], kinds: Sequence[str] = KINDS) -> List[Finding]:
    """The revisable findings a grounding-gate event carries. Only DETERMINISTIC findings with
    the fix in hand qualify: an unsupported claim (its span), an out-of-scope reference (the
    declared scope names what to use instead), an ungrounded number (the figure). A judge's
    ruling is not revisable — it is a verdict, not a span the model can rewrite."""
    if not isinstance(event, dict) or not event.get("ran", True):
        return []
    # a judge that PASSED the answer ruled the family it adjudicates faithful: those spans are a
    # deterministic pre-signal the judge overrode, not a finding to revise (the loop would
    # otherwise re-litigate a cleared answer — a second opinion no one asked for)
    if event.get("judge_passed") is True:
        fam = str(event.get("judge_family") or "scope_faithfulness")
        kinds = tuple(k for k in kinds if k not in _JUDGE_CLEARS.get(fam, ()))
        if "ungrounded_number" in kinds and not event.get("numeric_concern"):
            kinds = tuple(k for k in kinds if k != "ungrounded_number")
    out: List[Finding] = []
    if "unsupported_claim" in kinds:
        spans = tuple(str(s) for s in (event.get("unsupported_claims") or []) if str(s).strip())
        if spans:
            out.append(Finding("unsupported_claim", spans,
                               "these statements are not supported by the sources this turn retrieved — remove them, or "
                               "restate them as what the sources actually say"))
    if "out_of_scope_ref" in kinds:
        refs = tuple(str(s) for s in (event.get("answer_oos_refs") or []) if str(s).strip())
        if refs:
            scope = [str(s) for s in (event.get("scope_databases") or [])]
            hint = "these references lie outside the data scope this agent is grounded on"
            if scope:
                hint += f" — the answer may reference only: {', '.join(scope[:8])}"
            out.append(Finding("out_of_scope_ref", refs, hint))
    if "ungrounded_number" in kinds:
        nums = tuple(str(s) for s in (event.get("ungrounded_numbers") or []) if str(s).strip())
        if nums:
            out.append(Finding("ungrounded_number", nums,
                               "these figures match no value the turn's data returned — use only figures the results "
                               "carry, or say the figure is not available"))
    return out


def should_revise(cfg: OutcomeConfig, findings: Sequence[Finding], revisions_so_far: int, decision: Optional[str]) -> bool:
    """Revise when the switch + config allow, the bound is not spent, there is a revisable
    finding, and the gate did not BLOCK (a block is withheld by the gate's own rule — a revision
    of a withheld answer is a second silent try, which the loop must never be)."""
    if not enabled() or not cfg.enabled or cfg.max_revisions <= 0:
        return False
    if revisions_so_far >= cfg.max_revisions:
        return False
    if not findings:
        return False
    if decision == "block":
        return False
    return True


def revision_note(findings: Sequence[Finding]) -> str:
    """The targeted note the model reads with its own draft — the finding, its spans, the fix.
    Short, specific, never a rubric."""
    lines = ["Your draft answer did not pass the grounding check. Revise it — keep everything that is "
             "supported, change only what is named here, and do not add new claims:"]
    for f in findings:
        spans = list(f.spans)[:MAX_SPANS_IN_NOTE]
        more = f" (+{len(f.spans) - MAX_SPANS_IN_NOTE} more)" if len(f.spans) > MAX_SPANS_IN_NOTE else ""
        quoted = "; ".join(f'"{s}"' for s in spans)
        lines.append(f"- {f.kind.replace('_', ' ')}: {quoted}{more} — {f.hint}.")
    lines.append("Return ONLY the revised answer, in the same format and language as the draft — "
                 "no preamble, no label, and do not repeat the --- delimiters.")
    return "\n".join(lines)


_WRAP_LINE_RE = re.compile(r"^\s*(-{3,}|`{3,}[a-zA-Z]*|\*{3,}|_{3,})\s*$")
_LABEL_RE = re.compile(r"^\s*[*_\"'#]*\s*(here is (the|my|a) )?(revised (answer|draft|version|response)|revision|answer|draft)\s*:?\s*[*_\"']*\s*:?\s*$", re.IGNORECASE)


def unwrap_revision(text: str) -> str:
    """Strip the FRAMING a model echoes back — the `---` delimiters the prompt used around
    the draft, a code fence, or a "Revised answer:" label — and nothing else. Only whole
    leading/trailing lines are removed, so a horizontal rule INSIDE the answer, or a fenced
    block the answer genuinely contains, survives. The grade then runs on the answer the
    reader would see, not on the echo of our own prompt."""
    lines = (text or "").strip().splitlines()
    while lines and (_WRAP_LINE_RE.match(lines[0]) or _LABEL_RE.match(lines[0])):
        lines.pop(0)
    while lines and _WRAP_LINE_RE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()


def revision_prompt(draft: str, findings: Sequence[Finding], question: str = "") -> str:
    q = f"The question was:\n{question}\n\n" if question else ""
    return f"{q}Your draft answer:\n---\n{draft}\n---\n\n{revision_note(findings)}"


def cleared(before: Sequence[Finding], after: Sequence[Finding]) -> bool:
    """A revision CLEARS when none of the findings it was asked to fix remain and the
    re-grade raised no new revisable finding."""
    return not after


def state(*, revisions: int, cleared_flag: bool, findings_before: Sequence[Finding],
          findings_after: Sequence[Finding], drafts: Sequence[Dict[str, Any]], delivered: str,
          cost_usd: float = 0.0, tokens_in: int = 0, tokens_out: int = 0) -> Dict[str, Any]:
    """The turn's `outcome` stamp — append-only (both drafts kept), never the model's judgement."""
    def _f(fs): return [{"kind": f.kind, "spans": list(f.spans)} for f in fs]
    return {"revisions": int(revisions), "cleared": bool(cleared_flag), "delivered": delivered,
            "findings_before": _f(findings_before), "findings_after": _f(findings_after),
            # private bookkeeping (`_sealed`) never reaches the stamp
            "drafts": [{k: v for k, v in d.items() if not str(k).startswith("_")} for d in drafts],
            "cost_usd": round(float(cost_usd or 0.0), 6),
            "tokens_in": int(tokens_in or 0), "tokens_out": int(tokens_out or 0)}


def summary(st: Dict[str, Any]) -> str:
    n = int(st.get("revisions") or 0)
    if n == 0:
        return "no revision"
    kinds = sorted({f.get("kind", "") for f in (st.get("findings_before") or [])})
    what = ", ".join(k.replace("_", " ") for k in kinds) or "finding"
    if st.get("cleared"):
        return f"revised {'once' if n == 1 else f'{n} times'} — {what} removed; the revision passed the gate"
    return f"revised {'once' if n == 1 else f'{n} times'} — {what} not cleared; the honest annotated first draft was delivered"


# ── the per-turn record + the loop driver ─────────────────────────────────────────────────
class TurnOutcome:
    """What the loop did this turn: the drafts (append-only), the findings before/after, the
    revision's own cost. One per turn, read by the provenance seal and the persistence stamp."""
    def __init__(self, *, user_uuid=None, session_id=None):
        self.user_uuid = user_uuid
        self.session_id = session_id
        self.revisions = 0
        self.cleared = False
        self.delivered = "first"          # "first" | "revision"
        self.findings_before: List[Finding] = []
        self.findings_after: List[Finding] = []
        self.drafts: List[Dict[str, Any]] = []
        self.cost_usd = 0.0
        self.tokens_in = 0
        self.tokens_out = 0

    def state(self) -> Dict[str, Any]:
        return state(revisions=self.revisions, cleared_flag=self.cleared, findings_before=self.findings_before,
                     findings_after=self.findings_after, drafts=self.drafts, delivered=self.delivered,
                     cost_usd=self.cost_usd, tokens_in=self.tokens_in, tokens_out=self.tokens_out)


_TURN: "contextvars.ContextVar[Optional[TurnOutcome]]" = contextvars.ContextVar("uderia_outcome_loop", default=None)


def set_turn(t: Optional[TurnOutcome]):
    return _TURN.set(t)


def reset_turn(token) -> None:
    try:
        _TURN.reset(token)
    except Exception:  # noqa: BLE001
        pass


def current() -> Optional[TurnOutcome]:
    return _TURN.get()


def stamp_for_turn() -> Optional[Dict[str, Any]]:
    t = _TURN.get()
    if t is None or t.revisions == 0:
        return None
    st = t.state()
    st["summary"] = summary(st)
    return st


def draft_content(d: Dict[str, Any]) -> str:
    """ONE content function for a sealed draft (the provenance chain and its verifier both
    derive the sealed content from the stamp through this) — reads only the public keys, so a
    live draft (with its `_sealed` marker) and the stamped one hash identically."""
    fs = "; ".join(f"{f.get('kind')}:{'|'.join(f.get('spans') or [])}" for f in (d.get("findings") or []))
    return f"outcome_draft|{d.get('which')}|{fs}|{d.get('text') or ''}"


async def _regrade(grade, text: str):
    """The re-grade of a revision: a gate that can be TOLD it is re-grading (`regrade=True`)
    skips the judge escalation (a judge already ruled on the first draft; the deterministic
    trigger decides whether the revision cleared) and never parks the revision for review (the
    first draft is what a reviewer disposes of). A grade with no such parameter is called as is."""
    try:
        import inspect
        if "regrade" in inspect.signature(grade).parameters:
            return await grade(text, regrade=True)
    except (TypeError, ValueError):
        pass
    return await grade(text)


async def run_loop(*, first_text: str, first_event: Optional[Dict[str, Any]], profile: Optional[Dict[str, Any]],
                   question: str, grade, revise, raw_first: Optional[str] = None,
                   user_uuid=None, session_id=None, on_event=None):
    """The loop, engine-agnostic. `grade(text) -> (gated_text, event)` is the gate (the same
    call that produced `first_event`); `revise(prompt) -> (text, tokens_in, tokens_out, cost)`
    is ONE LLM call on the draft with the note. Returns `(delivered_text, delivered_event)`:
    the revision's gated text + event when the revision CLEARED, else the first draft's — the
    honest annotated answer, never a second silent try. Records into the ambient TurnOutcome
    (creating it) so the seal and the stamp see the drafts. Never raises: any failure delivers
    the first draft exactly as the gate returned it."""
    try:
        cfg = parse(profile)
        before = findings_from_event(first_event, cfg.kinds)
        decision = (first_event or {}).get("decision") if isinstance(first_event, dict) else None
        t = _TURN.get()
        if not should_revise(cfg, before, 0, decision):
            return first_text, first_event
        if t is None:
            t = TurnOutcome(user_uuid=user_uuid, session_id=session_id)
            set_turn(t)
        t.findings_before = list(before)
        # the first draft is what the MODEL wrote — the gate's annotation is not the draft
        t.drafts.append({"which": "first", "text": raw_first if raw_first is not None else first_text,
                         "findings": [{"kind": f.kind, "spans": list(f.spans)} for f in before]})
        delivered_text, delivered_event = first_text, first_event
        remaining = before
        draft_text = raw_first if raw_first is not None else first_text
        while should_revise(cfg, remaining, t.revisions, decision):
            t.revisions += 1
            prompt = revision_prompt(draft_text, remaining, question)
            revised, tin, tout, cost = await revise(prompt)
            t.tokens_in += int(tin or 0); t.tokens_out += int(tout or 0); t.cost_usd += float(cost or 0.0)
            revised = unwrap_revision(revised or "")
            if not revised:
                t.drafts.append({"which": f"revision_{t.revisions}", "text": "", "findings": [], "empty": True})
                break
            g_text, g_event = await _regrade(grade, revised)
            after = findings_from_event(g_event, cfg.kinds)
            delivered_now = cleared(remaining, after) and (g_event or {}).get("decision") != "block"
            t.drafts.append({"which": f"revision_{t.revisions}", "text": revised,
                             "findings": [{"kind": f.kind, "spans": list(f.spans)} for f in after]})
            if on_event:
                try:
                    await on_event({"step": "Outcome Revision", "type": "outcome_revision",
                                    "details": {"revision": t.revisions, "findings_before": [{"kind": f.kind, "spans": list(f.spans)} for f in remaining],
                                                "findings_after": [{"kind": f.kind, "spans": list(f.spans)} for f in after],
                                                "cleared": delivered_now, "tokens_in": tin, "tokens_out": tout, "cost_usd": cost,
                                                "summary": (f"revision {t.revisions}: " + ("the finding cleared — the revision is delivered" if delivered_now
                                                            else (f"{len(after)} finding(s) remain" if after else "the gate withheld the revision — the first draft is delivered")))}})
                except Exception:  # noqa: BLE001
                    pass
            if delivered_now:
                t.cleared = True; t.delivered = "revision"
                # the event the engines read: a delivered REVISION is named on it, so the
                # HTML each engine renders is rebuilt from the delivered text, never the draft's
                g_event = dict(g_event or {}); g_event["outcome_delivered"] = "revision"
                delivered_text, delivered_event = g_text, g_event
                remaining = after
                break
            remaining = after
            draft_text = revised
        t.findings_after = list(remaining)
        return delivered_text, delivered_event
    except Exception as e:  # noqa: BLE001 — the loop must never break a turn
        _log.debug(f"[outcome] loop failed open: {e}")
        return first_text, first_event
