"""Seam helpers: resolve per-profile mode and apply a verdict to rendered HTML.
Mirrors ``guardrails/adapter.py`` so the honesty card renders consistently."""
from __future__ import annotations

import html as _html
from typing import Any, Dict, Optional

from trusted_data_agent.core import posture_modes as _modes

_VALID_MODES = _modes.LADDER


def raw_mode(profile: Optional[Dict[str, Any]]) -> str:
    """The profile's honesty mode exactly as written (composites preserved), with
    agent-level inheritance: no own mode ⇒ ``postureConfig.mode`` (one resolver,
    ``core.posture_modes.gate_raw_or_default``)."""
    if not isinstance(profile, dict):
        return ""
    return _modes.gate_raw_or_default(
        _modes.raw_mode_of(profile.get("honestyConfig"), "mode"), profile)


def wants_review(profile: Optional[Dict[str, Any]]) -> bool:
    """True when the profile requests the ``review`` composite (enforce + retention)."""
    return _modes.is_review(raw_mode(profile))


def resolve_mode(profile: Optional[Dict[str, Any]]) -> str:
    """Per-profile mode from ``profile['honestyConfig']['mode']``; default ``observe``
    (measure only → byte-identical baseline).

    ``review`` resolves to ``enforce`` (it means enforce *with retention*); pair this with
    :func:`wants_review` to decide whether to park the original. Previously ``review`` fell
    through the unknown-value branch and silently degraded to ``observe`` — a weakening.
    Genuinely unrecognised values still fall back to ``observe`` (see
    ``core.posture_modes``)."""
    if not isinstance(profile, dict):
        return "observe"
    return _modes.normalize(raw_mode(profile) or "observe", allowed=_VALID_MODES,
                            context="honestyConfig.mode")


def wants_grain_judge(profile: Optional[Dict[str, Any]]) -> bool:
    """``honestyConfig.grainJudge`` — the OPTIONAL grain_faithfulness judge
    confirmation for the heuristic (entity-trigger) grain-substitution path.
    Explicit ``True`` only; absent/false/malformed = off (deterministic behavior
    unchanged — the judge spends the user's tokens, so it is opt-in). Read via
    the shape-tolerant config reader so a malformed block can never raise."""
    try:
        from trusted_data_agent.core.profile_config_contract import config_block
        return config_block(profile, "honestyConfig").get("grainJudge") is True
    except Exception:
        return False


def apply_to_html(answer_html: str, event: Optional[dict], honest_text: str) -> str:
    """Apply the honesty verdict to rendered HTML. ``block`` (enforce) rebuilds the body from
    the honest retrieval-failure text so the confident false negative is gone; ``flag``
    (observe/annotate) appends a theme-aware note banner. Best-effort — returns the original
    HTML on any mismatch."""
    try:
        if not event:
            return answer_html
        if event.get("answer_contained"):
            safe = _html.escape(honest_text or "").replace("\n", "<br>")
            return f'<div class="honesty-content">{safe}</div>'
        if event.get("disclosure_appended"):
            # grain-substitution: the figure is correct governed data — the answer is
            # APPENDED to (the disclosure), never replaced. honest_text here is the full
            # text WITH the disclosure; the banner carries only the disclosure itself.
            tail = (honest_text or "")
            marker = "\n\nNote: "
            note_txt = tail[tail.rfind(marker) + 2:] if marker in tail else tail
            from trusted_data_agent.agent import answer_surface as _ans
            safe = _html.escape(note_txt.strip()).replace("\n", "<br>")
            return _ans.place_note(answer_html, _ans.note("honesty", "", text_html=safe))
        from trusted_data_agent.agent import answer_surface as _ans
        return _ans.place_note(answer_html, _ans.note(
            "honesty",
            "This negative answer follows a failed data retrieval — treat it as "
            "'could not retrieve', not confirmed non-existence."))
    except Exception:
        return answer_html


#: Components that render MODEL-SUPPLIED data — a chart or a dashboard tile built from
#: figures the model wrote into the tool call. Under a contained verdict those figures
#: are the invented ones.
DATA_VISUAL_COMPONENTS = frozenset({"chart", "dashboard"})


def suppress_components(payloads, event):
    """Drop the data visuals of a turn whose answer the honesty gate REPLACED.

    A contained verdict (every data query failed, the prose figure was invented) rewrites
    the text — but on the ReAct path the component cards are appended AFTER the text is
    rendered, so a column chart of the same invented monthly counts sat under the honest
    retrieval-failure notice (production, 2026-09-05). The Optimize path never had the
    gap: ``apply_to_html`` rebuilds the whole body there. Returns ``(kept, dropped ids)``
    and stamps ``components_suppressed`` on the event; anything but a contained verdict
    returns the payloads untouched."""
    payloads = list(payloads or [])
    if not (isinstance(event, dict) and event.get("acted") and event.get("answer_contained")):
        return payloads, []
    kept, dropped = [], []
    for p in payloads:
        cid = str((p or {}).get("component_id") or "") if isinstance(p, dict) else ""
        if cid in DATA_VISUAL_COMPONENTS:
            dropped.append(cid)
        else:
            kept.append(p)
    if dropped:
        event["components_suppressed"] = dropped
    return kept, dropped
