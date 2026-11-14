"""Vote consumer — ask the judges why (opt-in; the ONLY token spender).

A thumbs-down is a signal with no explanation attached. When an admin opts in, a
downvote runs the LLM-judge layer over that turn, so the user's rejection gains a
reason ("groundedness failed: the answer cited a document that does not support the
claim") and the calibration data gains a (human, judge) pair on exactly the turns
where the two are most likely to disagree.

**This is the only consumer that spends tokens**, so it is:
  * default OFF (`FEEDBACK_SIGNALS.judge_on_downvote`), admin-gated;
  * downvote-only, and never on a PROPAGATED vote (a coordinator downvote would
    otherwise pay for judges on the coordinator turn AND its expert);
  * once per turn per process — a down → up → down cycle must not re-spend.

It adds no judge path of its own: it calls `evaluate_turn_online(force_judges=True)`,
so the timeouts, the span, the Live-Status step and the canonical cost sink are all
the ones every other judge run uses (the one-sink imperative).
"""
from __future__ import annotations

import logging

from trusted_data_agent.agent.feedback.dispatcher import register_consumer, signal_enabled
from trusted_data_agent.agent.feedback.types import VoteEvent

app_logger = logging.getLogger("quart.app")

# (session_id, turn_id) already diagnosed in this process. Bounded so a long-lived
# server cannot grow it without limit; the cap is far above any real vote volume,
# and an eviction merely permits one extra diagnosis, never a wrong one.
_SEEN: set = set()
_SEEN_MAX = 5000


def _already_diagnosed(session_id: str, turn_id) -> bool:
    key = (session_id, str(turn_id))
    if key in _SEEN:
        return True
    if len(_SEEN) >= _SEEN_MAX:
        _SEEN.clear()
    _SEEN.add(key)
    return False


def resolve_profile(user_uuid: str, profile_tag):
    """The profile that ran this turn, resolved from the turn's own TAG.

    The turn does not carry a profile id, and the session's current profile is not
    necessarily the one that produced an older or @TAG-overridden turn — so the tag,
    which IS per-turn, is the honest key. Returns None when it cannot be resolved;
    the judge layer then falls back to the admin defaults rather than guessing.
    """
    if not profile_tag:
        return None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        tag = str(profile_tag).lstrip("@").upper()
        for p in get_config_manager().get_profiles(user_uuid) or []:
            if str(p.get("tag") or "").lstrip("@").upper() == tag:
                return p
    except Exception as e:  # noqa: BLE001 — resolution is best-effort
        app_logger.debug(f"[feedback] profile resolve failed for {profile_tag}: {e}")
    return None


@register_consumer("diagnosis")
async def diagnosis(event: VoteEvent) -> None:
    if not signal_enabled("judge_on_downvote"):
        return
    if not event.is_down or event.depth != 0:
        return

    from trusted_data_agent.core.config import APP_CONFIG
    if not getattr(APP_CONFIG, "ONLINE_EVAL_ENABLED", False):
        return

    if _already_diagnosed(event.session_id, event.turn_id):
        app_logger.debug(
            f"[feedback] judge diagnosis already run for {event.session_id}/"
            f"turn {event.turn_id} — not re-spending")
        return

    from trusted_data_agent.eval.online import evaluate_turn_online

    app_logger.info(
        f"[feedback] downvote on {event.session_id}/turn {event.turn_id} — "
        f"running the judge layer to explain it (opt-in, costs tokens)")
    await evaluate_turn_online(
        user_uuid=event.user_uuid, session_id=event.session_id,
        profile=resolve_profile(event.user_uuid, event.profile_tag),
        turn_id=event.turn_id, force_judges=True)
