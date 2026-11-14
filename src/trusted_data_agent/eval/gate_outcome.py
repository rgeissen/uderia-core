"""I1 — the gate's OUTCOME, lifted from the turn onto the eval span.

Every turn is described by two records:

  * the **gate stamp** — what the runtime gates DID (judge votes, block, redact,
    park), persisted on the turn as ``grounding`` / ``guardrail`` /
    ``guardrail_inbound`` / ``honesty``;
  * the **eval span** — what the scorers MEASURED.

Every consumer surface (the Trust Circle's execution attributes, the Agent roster,
the runs ledger) reads only the span. That single omission produced five separate
findings in the session-2 walkthrough, all of the same shape — *a gate that acted
left no trace where anyone looks*:

  #50  the inbound guard refused a turn, and GUARDED still read "11/11 clean";
       the blocked turn is deliberately NOT scored (never feed an injected prompt
       to a judge), so it is absent from the population entirely.
  #53  the outbound guard redacted PII, and GUARDED still read "clean" — here the
       turn IS scored, but the safety scorers see the POST-redaction text, which
       is clean by construction. The guard erases its own evidence.
  #36  a corpus trigger escalated to the judge and the judge CLEARED it — the
       strongest evidence the gate works, and Evidence said "none in this window".
  #42  the block annotation named trigger noise rather than the judge's reason.
  #44  the runs ledger labelled a 2-judge adjudicated turn "deterministic".

This module is the fix's foundation: a pure, dependency-free reading of the turn's
own stamps into flat span attributes. Pure by design — no I/O, no imports from the
runtime — so the derivation is unit-testable without a server and cannot drift from
what the gates actually recorded.

Attribute contract (all optional; absent when the gate did not run):

    eval.gate.acted            bool  — a gate CHANGED what the user received
    eval.gate.parked           bool  — an original was parked for human review
    eval.gate.grounding        str   — pass | annotate | block
    eval.gate.grounding_mode   str   — observe | annotate | review | enforce
    eval.gate.judge_votes      str   — "0/2" (passed/total), when a panel ruled
    eval.gate.judge_family     str   — groundedness | scope_faithfulness
    eval.gate.escalated        bool  — the deterministic trigger called a judge
    eval.gate.cleared          bool  — …and the judge CLEARED it (#36's signal)
    eval.gate.outbound         str   — pass | annotate | redact | block
    eval.gate.inbound          str   — pass | annotate | block
    eval.gate.honesty          str   — pass | flag | rewrite
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# A gate ACTED when it changed what the user received. `annotate` attaches a note
# without altering the answer, so it is recorded but is not an action in this sense —
# the same predicate the Trust Circle uses to colour a station's configuration side.
_GROUNDING_ACTED = {"block"}
_OUTBOUND_ACTED = {"redact", "block"}
_INBOUND_ACTED = {"block"}

# "panel 0✓/2✗" / "panel 2✓/1✗/1?" — the consensus line the judge panel writes into
# the grounding verdict's reason. Parsed rather than re-derived so the span can never
# disagree with the adjudication that actually happened.
_VOTES_RE = re.compile(r"panel\s+(\d+)\s*[✓✔]\s*/\s*(\d+)\s*[✗✘x]", re.IGNORECASE)


def _s(v: Any) -> str:
    return str(v or "").strip().lower()


def _judge_votes(reason: str) -> Optional[str]:
    m = _VOTES_RE.search(reason or "")
    if not m:
        return None
    passed, failed = int(m.group(1)), int(m.group(2))
    total = passed + failed
    return f"{passed}/{total}" if total else None


def gate_attributes(turn: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flat span attributes describing what the gates DID on this turn.

    Returns ``{}`` for a turn with no gate stamps, so a deployment with the masters
    off produces a byte-identical span. Never raises: a malformed stamp yields fewer
    attributes, never an exception — this runs inside the online-eval hook, which is
    contractually forbidden from breaking a turn.
    """
    if not isinstance(turn, dict):
        return {}
    out: Dict[str, Any] = {}
    acted = False
    parked = False
    try:
        g = turn.get("grounding")
        if isinstance(g, dict):
            decision = _s(g.get("decision")) or "pass"
            out["eval.gate.grounding"] = decision
            mode = _s(g.get("mode"))
            if mode:
                out["eval.gate.grounding_mode"] = mode
            fam = _s(g.get("judge_family"))
            if fam:
                out["eval.gate.judge_family"] = fam
            reason = str(g.get("reason") or "")
            votes = _judge_votes(reason)
            if votes:
                out["eval.gate.judge_votes"] = votes
            # A judge ran iff the verdict SAYS a panel adjudicated. `judged` is stamped
            # where the adjudication happens (#37); the reason-prose parse remains as the
            # fallback for turns persisted before that field existed, so historical spans
            # keep their meaning. `needs_judge` is never the source: it is the
            # PRE-adjudication trigger and is persisted False on turns a judge ruled on.
            judged = g.get("judged")
            escalated = bool(judged) if judged is not None else ("judge" in reason.lower())
            if escalated:
                out["eval.gate.escalated"] = True
                # The cleared-escalation reading (#36): the deterministic trigger fired,
                # a judge adjudicated, and the answer was delivered anyway. That is the
                # strongest evidence a gate is working, and no surface could see it.
                # Prefer the recorded ruling; fall back to the decision for older turns.
                jp = g.get("judge_passed")
                out["eval.gate.cleared"] = (jp is True) if jp is not None else (decision == "pass")
            if decision in _GROUNDING_ACTED:
                acted = True
            if g.get("review_id"):
                parked = True

        ob = turn.get("guardrail")
        if isinstance(ob, dict):
            action = _s(ob.get("action")) or "pass"
            out["eval.gate.outbound"] = action
            if action in _OUTBOUND_ACTED:
                acted = True
            if ob.get("review_id"):
                parked = True

        ib = turn.get("guardrail_inbound")
        if isinstance(ib, dict):
            action = _s(ib.get("action")) or "pass"
            out["eval.gate.inbound"] = action
            if action in _INBOUND_ACTED:
                acted = True
            if ib.get("review_id"):
                parked = True

        h = turn.get("honesty")
        if isinstance(h, dict):
            decision = _s(h.get("decision")) or _s(h.get("action")) or "pass"
            out["eval.gate.honesty"] = decision
            if decision in ("rewrite", "block"):
                acted = True

        if out:
            out["eval.gate.acted"] = acted
            out["eval.gate.parked"] = parked
    except Exception:  # noqa: BLE001 — observability must never break a turn
        return out
    return out
