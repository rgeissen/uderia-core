"""Tier 1.3 P2 guardrails — config resolution + answer/HTML application.

The only guardrail module that knows the profile shape and the answer-rendering shape. Keeps
``policy.py`` pure. Mirrors ``agent/grounding/adapter.py``.

See docs/roadmap/TIER_1.3_GUARDRAILS.md.
"""
from __future__ import annotations

import html as _html

from trusted_data_agent.agent.guardrails.types import (
    Action, Direction, GuardrailConfig, GuardrailVerdict,
)


def resolve_config(profile, direction: Direction, master_enabled: bool) -> GuardrailConfig:
    """Merge the admin master switch with the profile's ``guardrailConfig`` block into the
    resolved config for one direction. Master off ⇒ ``off`` (the policy short-circuits).
    Absent profile block ⇒ ``observe`` defaults (detect + report, never alter)."""
    if not master_enabled:
        return GuardrailConfig(mode="off")
    # Shape-tolerant at EVERY level (`profile_config_contract.config_block`): a block
    # that is not a dict degrades to {} — the "no configuration" default — instead of
    # raising into the caller's fail-open except and taking every gate down with it.
    from trusted_data_agent.core.profile_config_contract import config_block
    sub = config_block(profile, "guardrailConfig", direction.value)
    # Agent-level inheritance: a direction with no own mode follows postureConfig.mode
    # (ONE resolver — core.posture_modes.gate_raw_or_default; composites flow through
    # and are normalized by GuardrailConfig exactly as a gate-level value would be).
    from trusted_data_agent.core import posture_modes as _pm
    mode = _pm.gate_raw_or_default(sub.get("mode"), profile) or "observe"
    if direction is Direction.INBOUND:
        return GuardrailConfig(
            mode=mode,
            check_injection=sub.get("injection", True))
    return GuardrailConfig(
        mode=mode,
        check_pii=sub.get("pii", True),
        check_toxicity=sub.get("toxicity", True),
        check_exfil=sub.get("exfil", True))


def apply_to_answer(answer_text: str, verdict: GuardrailVerdict) -> str:
    """Apply a verdict to plain-text answer content. REDACT/BLOCK swap in ``verdict.text``;
    ANNOTATE appends the note; PASS returns the answer untouched."""
    if verdict.action in (Action.REDACT, Action.BLOCK) and verdict.text is not None:
        base = verdict.text
        if verdict.action is Action.REDACT and verdict.annotation_md:
            return f"{base}\n\n{verdict.annotation_md}"
        return base
    if verdict.action is Action.ANNOTATE and verdict.annotation_md:
        return f"{answer_text}\n\n{verdict.annotation_md}"
    return answer_text


INBOUND_REFUSAL = ("This request was withheld by the input guardrail: it appears to contain a "
                   "prompt-injection / jailbreak attempt. Please rephrase your request.")


def build_block_turn(*, user_input: str, event: dict, profile_tag: str, profile_type: str,
                     turn_number: int, session_id: str, duration_ms: int = 0,
                     refusal_text: str = INBOUND_REFUSAL):
    """Build the artifacts for an inbound-guardrail BLOCK terminal — shared by every engine
    (the executor's 4-engine terminal and the genie path) so the refusal turn is constructed
    identically (DRY, charter #14). Returns ``(final_html, guardrail_event, final_answer_event,
    turn_summary)``; each caller emits/persists them via its own mechanism. The verdict rides in
    ``system_events`` (reload) AND a ``guardrail_inbound`` stamp (audit). Zero accounting."""
    safe = _html.escape(refusal_text)
    final_html = f'<div class="guardrail-content">{safe}</div>'
    guardrail_event = {"step": "Guardrail (input)", "type": "guardrail",
                       "details": event, "session_id": session_id}
    final_answer_event = {"step": "Finished", "final_answer": final_html,
                          "final_answer_text": refusal_text, "turn_id": turn_number,
                          "session_id": session_id, "input_tokens": 0, "output_tokens": 0,
                          # Signals downstream hooks (next-step recommender, online eval) to
                          # skip — a refused turn must not send the injected text to any LLM.
                          "guardrail_blocked": True}
    sys_events = [{"type": "guardrail",
                   "payload": {"type": "guardrail", "step": "Guardrail (input)", "details": event}}]
    turn_summary = {
        "turn": turn_number, "user_query": user_input,
        "final_response": refusal_text[:500], "final_summary": refusal_text,
        "profile_tag": profile_tag, "profile_type": profile_type,
        "duration_ms": duration_ms, "turn_input_tokens": 0, "turn_output_tokens": 0,
        "turn_cost": 0.0, "session_id": session_id,
        "guardrail_inbound": event, "guardrail_blocked": True, "system_events": sys_events,
    }
    return final_html, guardrail_event, final_answer_event, turn_summary


def _guardrail_note_html(answer_html: str, annotation_md: str) -> str:
    """An ANNOTATE verdict is a platform statement about the answer — it wears the ONE note
    shape every other gate uses (kind word + icon + attention rule) and sits where notes sit,
    directly under the prose, so it can never be mistaken for the answer's own text."""
    from trusted_data_agent.agent import answer_surface as _ans
    text = str(annotation_md or "").replace("`", "").strip()
    if text.startswith(">"):
        text = text.lstrip("> ").strip()
    # the note names its finding in bold ("**Sensitive content** — email"): rendered, never
    # shown as asterisks
    from trusted_data_agent.agent.formatter import md_inline
    return _ans.place_note(answer_html, _ans.note("guardrail", "", text_html=md_inline(text)))


def apply_event_to_html(answer_html: str, safe_text: str, event: dict) -> str:
    """Seam helper: apply an outbound verdict (already reduced to its ``event`` dict + the
    post-scan ``safe_text``) to rendered HTML. REDACT/BLOCK rebuild the body from the safe
    (masked/withheld) text so no raw PII survives in the HTML; ANNOTATE appends a note banner.
    Best-effort — returns the original HTML on any mismatch."""
    try:
        action = (event or {}).get("action", "pass")
        if action in ("redact", "block"):
            safe = _html.escape(safe_text or "").replace("\n", "<br>")
            return f'<div class="guardrail-content">{safe}</div>'
        if action == "annotate" and (event or {}).get("annotation_md"):
            return _guardrail_note_html(answer_html, event["annotation_md"])
        return answer_html
    except Exception:
        return answer_html


def apply_to_html(answer_html: str, verdict: GuardrailVerdict) -> str:
    """Apply a verdict to rendered HTML (genie/coordinator path). REDACT/BLOCK replace the body
    with the escaped guardrail text; ANNOTATE appends a theme-aware note banner."""
    if verdict.action in (Action.REDACT, Action.BLOCK) and verdict.text is not None:
        safe = _html.escape(verdict.text).replace("\n", "<br>")
        return f'<div class="guardrail-content">{safe}</div>'
    if verdict.action is Action.ANNOTATE and verdict.annotation_md:
        return _guardrail_note_html(answer_html, verdict.annotation_md)
    return answer_html
