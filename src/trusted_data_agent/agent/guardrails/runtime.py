"""Tier 1.3 P2 guardrails — engine-agnostic orchestration.

The single place the guardrail flow lives, so EVERY engine drives the same logic with no
duplication (charter #14). Takes explicit inputs rather than reaching into an executor —
exactly like ``agent/grounding/runtime.py``. Two entrypoints:

  * ``scan_inbound``  — prompt-injection check on user text (and, later, tool results)
  * ``scan_outbound`` — PII + toxicity check on the model's answer

Both return ``(content, event|None)``: ``content`` is the (possibly redacted/blocked) text to
use downstream, ``event`` is the ``guardrail`` Live-Status payload (``None`` ⇒ nothing to
report, content byte-for-byte unchanged). Fails OPEN throughout (charter #8/#15): any error
returns the original content untouched and no event. Deterministic + 0-token (charter #1).

See docs/roadmap/TIER_1.3_GUARDRAILS.md and docs/Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md.
"""
from __future__ import annotations

import logging

from trusted_data_agent.agent.guardrails import adapter, policy
from trusted_data_agent.agent.guardrails.types import Action, Direction
from trusted_data_agent.core import posture_modes as _modes

logger = logging.getLogger("quart.app")


def _enabled() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "GUARDRAILS_ENABLED", False))
    except Exception:
        return False


# ── the trace must be self-describing (migration P1) ─────────────────────────
# A guard that ran and found nothing used to return `None`, which made it
# indistinguishable from a guard that is switched off and from one that failed.
# Three different facts, one silence — so the trace could not answer "did this
# protection run?", and the Live Status strip reported a WORKING guard as
# "not enabled". Every path now leaves a stamp carrying `ran` and `acted`.
#
#   ran=False            the guard is off (master switch or profile mode)
#   ran=True  acted=False it ran and found nothing
#   ran=True  acted=True  it altered, annotated, blocked or parked
#   ran=True  error       it could not complete — never reported as clean
#
# `acted` is what callers gate rendering on. Stamping is unconditional; EMITTING
# stays conditional, so a clean turn is byte-identical in the trace and the UI.
def _stamp(direction: str, *, ran: bool, action: str, mode: str = None, **extra) -> dict:
    ev = {"direction": direction, "ran": bool(ran), "acted": False,
          "action": action, "categories": []}
    if mode:
        ev["mode"] = mode
    ev.update(extra)
    return ev


def _mark_acted(event: dict, direction: str) -> dict:
    """Tag a real verdict event so callers can tell it from a clean-pass stamp."""
    if isinstance(event, dict):
        event.setdefault("direction", direction)
        event["ran"] = True
        event["acted"] = True
    return event

def _plugin_inbound(text: str):
    """Third-party injection findings for this request, as `(label, excerpt)` pairs.

    The excerpt is what the platform's own detector supplies for the same category — a
    short, already-bounded snippet — so a plugin's finding renders exactly like a native
    one and cannot smuggle a longer quotation onto a surface.
    """
    try:
        from trusted_data_agent.agent.assurance import inflight as _if
        from trusted_data_agent.agent.assurance.contract import AssessmentContext
    except Exception:  # noqa: BLE001
        return None
    try:
        ctx = AssessmentContext(question=text or "", answer=text or "")
        found = _if.augment("inbound_guard", ctx=ctx)
        if not found:
            return None
        out = []
        for f in found:
            label = f"{(f.property.split('.')[-1] or 'finding')} ({f.assessor})"
            out.append((label, (f.evidence[0] if f.evidence else (f.summary or ""))[:120]))
        return tuple(out)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Guardrail/inbound] third-party assessment skipped: {e}")
        return None


def scan_inbound(text: str, *, profile, profile_class: str = "tool_enabled",
                 user_uuid: str = None, session_id: str = None, turn_id=None):
    """Scan inbound text for prompt-injection. Returns ``(content, event|None)``: in enforce a
    detected injection BLOCKs (content = refusal); observe/annotate keep the text. Synchronous
    + deterministic. Never raises.

    **review** mode = enforce **with retention** (HITL), matching ``scan_outbound``: the
    injected request is blocked exactly as enforce would, and the ORIGINAL inbound text is
    parked for the owner to disposition. Without turn context review still enforces, it just
    cannot park (fail toward safety, with a log line). Before the shared ladder, an inbound
    ``review`` silently normalized to ``observe`` — i.e. the injection was let through."""
    try:
        if not _enabled():
            return text, _stamp("inbound", ran=False, action="off", reason="master switch off")
        cfg = adapter.resolve_config(profile, Direction.INBOUND, True)
        review = _raw_mode(profile, Direction.INBOUND) == _modes.REVIEW
        if review:
            from dataclasses import replace as _dc_replace
            cfg = _dc_replace(cfg, mode="enforce")
        if cfg.normalized_mode() == "off":
            return text, _stamp("inbound", ran=False, action="off",
                                reason="not enabled for this agent")
        # PROP-003 — a bound third-party module reads the same request. `request_integrity`
        # ships with NO platform assessor at all, so this is also the one property a pack
        # can be the FIRST to assess.
        verdict = policy.evaluate_inbound(text or "", cfg,
                                          extra=_plugin_inbound(text or ""))
        if verdict.action is Action.PASS and not verdict.findings:
            # RAN, and found nothing. This is a finding in its own right — and the stamp
            # must say the gate was ARMED IN REVIEW, not the `enforce` it was resolved to
            # (see posture_modes.stamped_mode): otherwise REVIEWED reads "not armed".
            return text, _stamp("inbound", ran=True, action="pass",
                                mode=_modes.stamped_mode(review, cfg.normalized_mode()))
        new_text = verdict.text if (verdict.blocked and verdict.text is not None) else text
        event = verdict.to_event()
        if review:
            event["mode"] = _modes.REVIEW
            if verdict.blocked:
                review_id = _park(
                    source="guardrail_inbound", original_text=text, delivered_text=new_text,
                    reason=verdict.reason, categories=list(verdict.categories()),
                    profile=profile, user_uuid=user_uuid, session_id=session_id,
                    turn_id=turn_id)
                if review_id:
                    event["review_id"] = review_id
        logger.info(f"[Guardrail/inbound] class={profile_class} mode={event.get('mode')} "
                    f"action={verdict.action.value} cats={list(verdict.categories())}")
        return new_text, _mark_acted(event, "inbound")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Guardrail/inbound] failed open: {e}", exc_info=True)
        # Failed open — but never recorded as clean: an unknown is not a pass.
        return text, _stamp("inbound", ran=True, action="error", error=type(e).__name__)


def scan_tool_result(tool_name, result, *, user_uuid: str = None,
                     session_id: str = None) -> list:
    """Tool-result firewall (third indirect-injection lane, observe-only v1).

    The inbound guard screens USER text; the memory/retrieval firewalls screen
    memory writes and retrieved chunks. This closes the remaining lane: a
    poisoned TOOL OUTPUT (e.g. a database row containing "ignore all previous
    instructions") reaching the model. Deterministic, 0-token, gated on the
    guardrails master switch. **Observe-only by design (charter #15): the
    result is NEVER altered — byte-identical to baseline** — the detection is
    logged, recorded as an Assurance-Inbox event (labels only, never row
    content), and broadcast live. Content defusing inline is a documented
    further increment. Returns the matched labels ([] = clean/off). Fails open.
    """
    try:
        if not _enabled() or not isinstance(result, dict):
            return []
        if str(tool_name or "").startswith("TDA_"):
            return []  # framework tools carry the agent's own text, not external data
        import json as _json
        try:
            text = _json.dumps(result, default=str)
        except Exception:
            text = str(result)
        from trusted_data_agent.agent.guardrails.primitives import detect_prompt_injection
        hits = detect_prompt_injection(text[:40000])
        if not hits:
            return []
        labels = sorted({label for label, _ in hits})
        logger.warning(f"[Guardrail/tool-result] output of '{tool_name}' matched "
                       f"prompt-injection pattern(s): {', '.join(labels)} — observed "
                       f"(result unaltered; observe-only lane)")
        if user_uuid:
            try:
                from trusted_data_agent.core import trust_events
                summary = (f"Tool-result firewall: output of '{tool_name}' matched "
                           f"prompt-injection "
                           f"{'pattern' if len(labels) == 1 else 'patterns'}: "
                           f"{', '.join(labels)} — observed (result delivered unaltered)")
                trust_events.record_event(
                    user_uuid, "tool_result_injection", summary, severity="warning",
                    session_id=session_id, payload={"labels": labels, "tool": tool_name})
                trust_events.broadcast_notice(user_uuid, "tool_result_injection", summary,
                                              session_id=session_id, severity="warning")
            except Exception:
                pass
        return labels
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Guardrail/tool-result] failed open: {e}")
        return []


def _released_allowlist(user_uuid, session_id) -> frozenset:
    """Per-session released-value allowlist (HITL release-to-conversation).

    When the owner explicitly releases a withheld original INTO the conversation,
    the exact PII values it contains stop being re-redacted in that session —
    otherwise the agent could reason over the released content but never state
    it (and every mention would re-park a new review item). Exact values only,
    session-scoped, derived on read from the review queue (no separate store to
    drift). Fail-open → empty set."""
    try:
        if not (user_uuid and session_id):
            return frozenset()
        from trusted_data_agent.core import review_queue
        released = review_queue.released_originals_for_session(user_uuid, session_id)
        if not released:
            return frozenset()
        from trusted_data_agent.agent.guardrails.primitives import detect_pii
        values = set()
        for item in released:
            for _label, _s, _e, val in detect_pii(item.get("original_text") or ""):
                values.add(val)
        return frozenset(values)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Guardrail/outbound] released-allowlist skipped: {e}")
        return frozenset()


def _raw_mode(profile, direction: Direction) -> str:
    """The profile's mode for ``direction`` as WRITTEN (before GuardrailConfig
    normalization), with agent-level inheritance (no own mode ⇒ ``postureConfig.mode``).
    Needed because ``review`` is not a GuardrailConfig ladder mode — it is a
    runtime-level composite (enforce + retention) that the gate must detect before
    normalization collapses it; an agent-level ``review`` must park exactly as a
    gate-level one does."""
    return _modes.gate_raw_or_default(
        _modes.raw_mode_of((profile or {}).get("guardrailConfig"),
                           direction.value, "mode"), profile)


def _park(*, source: str, original_text: str, delivered_text: str, reason: str,
          categories: list, profile, user_uuid: str, session_id: str, turn_id,
          status: str = "pending") -> str:
    """Retention. ``status='pending'`` = review mode's owed HITL work;
    ``status='retained'`` = plain enforce's recovery ledger (full withholdings
    only). Without turn context the gate still ENFORCES, it just cannot park
    (fail toward safety, with a log line). Never raises."""
    if not user_uuid:
        logger.warning(f"[Guardrail/{source}] {status} parking without turn context — "
                       "enforced but could not park")
        return None
    try:
        from trusted_data_agent.core import review_queue
        return review_queue.park(
            user_uuid, source=source, original_text=original_text,
            delivered_text=delivered_text, reason=reason, categories=categories,
            session_id=session_id, turn_id=turn_id,
            profile_tag=(profile or {}).get("tag"), status=status)
    except Exception as e:  # noqa: BLE001 — retention must never break a turn
        logger.warning(f"[Guardrail/{source}] {status} park failed (enforced anyway): {e}")
        return None


def _retain_enforced_blocks() -> bool:
    """The plain-enforce retention default: a FULL withholding (toxicity BLOCK)
    keeps its original as a `retained` recovery entry. A routine PII REDACT never
    auto-parks — its parked content would be the raw PII (the liability the
    escape-hatch flag exists for)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "REVIEW_RETAIN_ENFORCED_BLOCKS", True))
    except Exception:  # noqa: BLE001
        return True


def _plugin_outbound(answer_text: str, source_texts=()):
    """A third-party assessment of this answer, in the guard's own shape (PROP-003).

    Returns `None` when nothing third-party is bound — the overwhelmingly common case, and
    the one that must cost nothing and change nothing.

    The conversion is deliberately narrow: a plugin's `Finding` becomes
    `(label, start, end, value)` spans plus a label-only reportable finding, which is
    exactly what the platform already knows how to redact and report. Nothing about the
    plugin's own structure reaches the verdict, the event, or any surface — a category and
    a label do, and the raw values stay inside this process exactly as the guard's own do.
    """
    try:
        from trusted_data_agent.agent.assurance import inflight as _if
        from trusted_data_agent.agent.assurance.contract import AssessmentContext
        from trusted_data_agent.agent.guardrails.policy import OutboundFindings
        from trusted_data_agent.agent.guardrails.types import Category, Finding as GFinding
    except Exception:  # noqa: BLE001 — the substrate must never break the guard
        return None

    try:
        ctx = AssessmentContext(answer=answer_text or "",
                                chunks=tuple(str(s) for s in (source_texts or ())))
        found = _if.augment("outbound_guard", ctx=ctx)
        if not found:
            return None

        # PROP-003 — a pack's OWN control decides here. `resolve` returns only the controls
        # whose trigger fired AND whose posture permits an act, so a DORMANT control (the
        # arrival state) is simply absent: it never reaches this loop, and the answer is
        # untouched. The act it chose is mapped onto the platform's own buckets, so the
        # ladder, the parking, the retention and the event are the platform's whatever the
        # finding's origin.
        from trusted_data_agent.agent.assurance import controls as _ctl
        acting = {c.fires_on: act for c, act in _ctl.resolve("outbound_answer", found)}

        pii, tox, exfil, reportable = [], [], [], []
        for f in found:
            payload = f.payload if isinstance(f.payload, dict) else {}
            cat = str(payload.get("category") or "pii")
            # A control's chosen act overrides the finding's own category, because the act
            # is what the operator raised a posture for. `annotate` contributes a reportable
            # finding and no bucket — recorded, never altering.
            _act = acting.get(f.property)
            if _act == "withhold":
                cat = "toxicity"
            elif _act == "alter":
                cat = "pii"
            elif _act == "annotate" or (_act is None and _ctl.controls_at("outbound_answer")
                                        and any(c.fires_on == f.property
                                                for c in _ctl.controls_at("outbound_answer"))):
                # A control exists for this property but is dormant (or set to annotate):
                # the finding is REPORTED and the answer is left alone.
                reportable.append(GFinding(
                    category=Category.PII,
                    label=f"{(f.property.split('.')[-1] or 'finding').upper()} "
                          f"({f.assessor})"))
                continue
            spans = payload.get("spans") or []
            label = (f.property.split(".")[-1] or "finding").upper()
            reportable.append(GFinding(
                category=(Category.PII if cat == "pii"
                          else (Category.TOXICITY if cat == "toxicity" else Category.EXFIL)),
                label=f"{label} ({f.assessor})"))
            for s in spans:
                # A malformed span must not corrupt a splice — skip it rather than mask
                # the wrong range.
                if (isinstance(s, (list, tuple)) and len(s) == 4
                        and isinstance(s[1], int) and isinstance(s[2], int)):
                    if cat == "pii":
                        pii.append((str(s[0]), s[1], s[2], str(s[3])))
                    elif cat == "exfil":
                        exfil.append((str(s[0]), str(s[3])))
            if cat == "toxicity":
                tox.append((label, f.summary or ""))
        if not (pii or tox or exfil or reportable):
            return None
        return OutboundFindings(pii=tuple(pii), toxicity=tuple(tox),
                                exfil=tuple(exfil), findings=tuple(reportable))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Guardrail/outbound] third-party assessment skipped: {e}")
        return None


def scan_outbound(answer_text: str, *, profile, profile_class: str = "tool_enabled",
                  user_uuid: str = None, session_id: str = None, turn_id=None,
                  source_texts=()):
    """Scan an outbound answer for PII + toxicity. Returns ``(content, event|None)``: enforce
    REDACTs PII (content = masked) or BLOCKs toxicity (content = refusal); annotate appends a
    note; observe leaves content untouched. Synchronous + deterministic. Never raises.

    **review** mode = enforce **with retention** (HITL): the content is redacted/withheld
    exactly as enforce would, but the ORIGINAL answer is parked in the review queue for the
    owner to disposition — nothing lost, nothing silently delivered. Requires the turn
    context kwargs; without them (an older caller) review still enforces, it just cannot
    park (fail toward safety, with a log line)."""
    try:
        if not _enabled():
            return answer_text, _stamp("outbound", ran=False, action="off",
                                       reason="master switch off")
        cfg = adapter.resolve_config(profile, Direction.OUTBOUND, True)
        review = _raw_mode(profile, Direction.OUTBOUND) == _modes.REVIEW
        if review:
            # Evaluate under enforce semantics; retention + re-tagging happen below.
            from dataclasses import replace as _dc_replace
            cfg = _dc_replace(cfg, mode="enforce")
        if cfg.normalized_mode() == "off":
            return answer_text, _stamp("outbound", ran=False, action="off",
                                       reason="not enabled for this agent")
        # PROP-003 — a bound third-party module assesses the SAME answer, in flight. Its
        # findings join the platform's own before the decision, so the ladder that acts is
        # unchanged and a plugin can tighten what is delivered without ever choosing what
        # happens. `[]` when nothing third-party is bound ⇒ byte-identical baseline.
        _extra = _plugin_outbound(answer_text or "", source_texts)
        verdict = policy.evaluate_outbound(answer_text or "", cfg,
                                           source_texts=source_texts, extra=_extra)
        # Released-value allowlist (HITL): only consulted when PII actually fired
        # (keeps the clean-turn hot path free of any queue lookup).
        if any(f.category.value == "pii" for f in verdict.findings):
            _allow = _released_allowlist(user_uuid, session_id)
            if _allow:
                verdict = policy.evaluate_outbound(answer_text or "", cfg,
                                                   allowed_values=_allow,
                                                   source_texts=source_texts,
                                                   extra=_extra)
        if verdict.action is Action.PASS and not verdict.findings:
            # RAN, and found nothing — stamped as ARMED IN REVIEW where that is the
            # configured mode (see posture_modes.stamped_mode).
            return answer_text, _stamp("outbound", ran=True, action="pass",
                                       mode=_modes.stamped_mode(review, cfg.normalized_mode()))
        new_text = adapter.apply_to_answer(answer_text, verdict)
        event = verdict.to_event()
        if review:
            event["mode"] = _modes.REVIEW
            if verdict.modified:
                review_id = _park(
                    source="guardrail_outbound", original_text=answer_text,
                    delivered_text=new_text, reason=verdict.reason,
                    categories=list(verdict.categories()), profile=profile,
                    user_uuid=user_uuid, session_id=session_id, turn_id=turn_id)
                if review_id:
                    event["review_id"] = review_id
                    new_text += ("\n\n> The original answer was parked for human review "
                                 "(Assurance Inbox).")
        elif (cfg.normalized_mode() == "enforce" and verdict.action is Action.BLOCK
              and _retain_enforced_blocks()):
            # Plain-enforce retention default: a FULL withholding (toxicity BLOCK)
            # never silently destroys the answer — the original is retained as a
            # recovery-ledger entry (no disposition owed, not counted as open work).
            # REDACT deliberately never reaches here: parking each routine redaction
            # would store the raw PII and flood the ledger.
            retained_id = _park(
                source="guardrail_outbound", original_text=answer_text,
                delivered_text=new_text, reason=verdict.reason,
                categories=list(verdict.categories()), profile=profile,
                user_uuid=user_uuid, session_id=session_id, turn_id=turn_id,
                status="retained")
            if retained_id:
                event["retained_id"] = retained_id
                new_text += ("\n\n> The withheld original was retained — "
                             "recoverable from the Assurance Inbox.")
        logger.info(f"[Guardrail/outbound] class={profile_class} mode={event['mode']} "
                    f"action={verdict.action.value} cats={list(verdict.categories())}")
        return new_text, _mark_acted(event, "outbound")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Guardrail/outbound] failed open: {e}", exc_info=True)
        return answer_text, _stamp("outbound", ran=True, action="error",
                                   error=type(e).__name__)


def stream_policy(profile, *, user_uuid: str = None, session_id: str = None) -> dict:
    """What the Governed Stream's in-flight guard may ALTER — derived from the same
    resolution the post-hoc `scan_outbound` uses, so the two can never disagree about
    which mode governs. Returns ``{"redact_pii", "block_toxicity", "allowed_values"}``.

    Only an ACTING mode (enforce, or review = enforce with retention) alters delivered
    text, so only then does the stream redact PII / withdraw on toxicity; under observe /
    annotate / off the stream is untouched, exactly as the final answer will be. The
    released-value allowlist is resolved up front (the stream cannot wait for a finding
    to fire before consulting it). Never raises: an unresolvable profile reads as "alter
    nothing" — the post-hoc scan still stands over the final answer."""
    try:
        if not _enabled():
            return {"redact_pii": False, "block_toxicity": False, "allowed_values": frozenset()}
        cfg = adapter.resolve_config(profile, Direction.OUTBOUND, True)
        mode = cfg.normalized_mode()
        acting = mode == "enforce" or _raw_mode(profile, Direction.OUTBOUND) == _modes.REVIEW
        if not acting:
            return {"redact_pii": False, "block_toxicity": False, "allowed_values": frozenset()}
        return {"redact_pii": bool(cfg.check_pii), "block_toxicity": bool(cfg.check_toxicity),
                "allowed_values": _released_allowlist(user_uuid, session_id)}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[Guardrail/stream] policy resolution skipped: {e}")
        return {"redact_pii": False, "block_toxicity": False, "allowed_values": frozenset()}
