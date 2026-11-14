"""The canonical enforcement-mode ladder shared by every posture gate.

Root cause this addresses: the tuple ``("off","observe","annotate","enforce")`` existed in
**four independent copies** (``agent/grounding/types.py``, ``agent/guardrails/types.py``,
``agent/honesty/adapter.py``, ``agent/preflight/guard.py`` — the last with a different
membership), and every one of them normalized an unrecognised mode down to ``observe``.
That made ``review`` — a real, stricter value — silently degrade to *observe* on any path
that did not special-case it first, i.e. a **weakening**, the opposite of intent. Two of the
four paths carried a hand-rolled raw-mode guard; two did not.

One ladder, one normalizer, one ``review`` detector — so a gate cannot forget the guard.

The ladder
----------
``off < observe < annotate < enforce``  plus the runtime composite ``review``.

``review`` is *not* a config-ladder mode: it means **enforce WITH retention** (act exactly
as enforce, but park the original for human disposition). It therefore ranks **above**
enforce on the human-oversight axis while being identical to enforce on the content axis.
A gate whose ladder stops at ``enforce`` resolves it to ``enforce``; whether the original is
actually parked is the gate's own concern (it needs turn context to do so).

Normalization rule
------------------
``normalize()`` maps a requested mode onto the strongest mode the gate *supports* that does
not exceed what was asked — "the largest allowed rank ≤ the requested rank":

    review   (4) + ladder {off,observe,annotate,enforce} -> enforce   (the bug this fixes)
    annotate (2) + ladder {off,observe,enforce}          -> observe   (preflight, unchanged)
    enforce  (3) + ladder {off,observe,annotate,enforce} -> enforce

This never weakens below intent and never escalates beyond it.

An **unrecognised** string has no rank, so it resolves to the caller's ``default`` and is
logged. That is deliberate: a garbage mode is a config-integrity failure, not an attack, and
silently escalating it to ``enforce`` would start withholding answers on a typo. Rejecting
bad values belongs at the *write* boundary (the profile editor writes only ladder values;
``core/profile_config_contract.py`` validates on import) — reads stay forgiving and loud.

Pure: stdlib only, zero I/O, no platform imports, so every consumer stays CI-pure.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

logger = logging.getLogger("quart.app")

#: The standard gate ladder, lowest → highest enforcement.
LADDER: Tuple[str, ...] = ("off", "observe", "annotate", "enforce")

#: The preflight guard has no "annotate" step (it either observes or blocks).
PREFLIGHT_LADDER: Tuple[str, ...] = ("off", "observe", "enforce")

#: The runtime composite: enforce + retention (HITL). Never a config-ladder member.
REVIEW = "review"

#: Total order over every mode the platform recognises, including ``review``.
_RANK: Dict[str, int] = {"off": 0, "observe": 1, "annotate": 2, "enforce": 3, REVIEW: 4}

#: Every recognised mode string (the write-boundary allow-list).
ALL_MODES: Tuple[str, ...] = tuple(_RANK.keys())

#: Modes in which a gate ALTERS the delivered content in any way — annotate adds a
#: disclosure note, enforce/review suppress. This is the predicate for the DOWNGRADE
#: guard (weakening annotate → observe loses the disclosure, a real downgrade).
ACTING_MODES: frozenset = frozenset({"annotate", "enforce", REVIEW})

#: Modes in which a gate actually SUPPRESSES harm (withhold / redact / rewrite) — the
#: predicate for the SIGNING verdict and the Trust Circle's config reading. Annotate is
#: deliberately NOT here: disclosure informs the reader but stops nothing, so counting
#: it made an annotate-only agent sign "posture enforced" while the ring honestly read
#: "watching" — two trust surfaces, one fact, two answers. Acting ≠ protecting.
PROTECTING_MODES: frozenset = frozenset({"enforce", REVIEW})

# ── The reading of a posture, declared ONCE (TRUST_CIRCLE_LEGIBILITY.md W1/W4) ──────
#
# A configured posture is a CHOICE, never a finding. The Trust Circle, the posture
# matrix and the Agent Anatomy each used to hold their own mode→colour map, and they
# disagreed (observe was amber on the ring, neutral on the matrix, amber-worded
# "observing" on the anatomy). These two maps are the single source every reading
# surface mirrors (`trustGrammar.POSTURE_STATE` / `POSTURE_LABEL`, pinned byte-equal
# by test_assurance_points) until the vocabulary endpoint serves them.
#
#   POSTURE_STATE — what a posture READS as on an attention surface:
#     protecting (enforce/review)  → "pass"      nothing is owed; the gate can stop harm
#     watching   (observe/annotate) → "watching"  turned on, records, stops nothing —
#                                                a member of the ABSENCE family: never
#                                                amber (Rule 4.4 extended: a chosen
#                                                setting is never an alarm) and never
#                                                green (an estate of watchers must not
#                                                read quiet)
#     off                          → "na"        not turned on
#
#   POSTURE_LABELS — the words a READER sees for a stored mode. The editor's radios
#     and checkboxes already say it this way (Observe/Enforce + disclose / keep for
#     review); the composites `annotate` and `review` are STORAGE tokens and must not
#     reach a reading surface bare.
POSTURE_STATE: Dict[str, str] = {
    "off": "na",
    "observe": "watching",
    "annotate": "watching",
    "enforce": "pass",
    REVIEW: "pass",
}

POSTURE_LABELS: Dict[str, str] = {
    "off": "off",
    "observe": "watch",
    "annotate": "watch + disclose",
    "enforce": "enforce",
    REVIEW: "enforce + keep for review",
}


def posture_state(mode: Any) -> str:
    """The attention-surface state of a configured posture; ``"na"`` for anything
    unrecognised (an unknown mode is not turned on — it is a config-integrity failure
    the write boundary should have refused, never a protection)."""
    return POSTURE_STATE.get(_clean(mode), "na")


def posture_label(mode: Any) -> str:
    """The reader's word for a stored mode; the cleaned token itself when unrecognised,
    so a bad value is at least visible rather than silently renamed."""
    m = _clean(mode)
    return POSTURE_LABELS.get(m, m)


def _clean(mode: Any) -> str:
    """Lowercase a mode value defensively; non-strings become ``""``."""
    try:
        return str(mode or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def rank(mode: Any) -> int:
    """Strictness rank of ``mode``; ``-1`` for anything unrecognised."""
    return _RANK.get(_clean(mode), -1)


def is_known(mode: Any) -> bool:
    """True when ``mode`` is a mode string the platform recognises."""
    return rank(mode) >= 0


def is_review(mode: Any) -> bool:
    """True when ``mode`` is the ``review`` composite (enforce + retention)."""
    return _clean(mode) == REVIEW


def is_acting(mode: Any) -> bool:
    """True when ``mode`` alters delivered content in any way (incl. disclosure)."""
    return _clean(mode) in ACTING_MODES


def is_protecting(mode: Any) -> bool:
    """True when ``mode`` suppresses harm (enforce/review) — never mere disclosure."""
    return _clean(mode) in PROTECTING_MODES


def normalize(mode: Any, *, allowed: Sequence[str] = LADDER,
              default: str = "observe", context: str = "") -> str:
    """Resolve ``mode`` onto the gate's own ladder.

    Returns the strongest member of ``allowed`` whose rank does not exceed ``mode``'s —
    so ``review`` resolves to ``enforce`` rather than collapsing to ``observe``. An
    unrecognised value returns ``default`` and logs once (see the module docstring).
    """
    m = _clean(mode)
    if m in allowed:
        return m
    r = rank(m)
    if r < 0:
        if m:
            logger.warning(
                "[posture] unrecognised mode %r%s — falling back to %r. Valid modes: %s",
                m, f" ({context})" if context else "", default, ", ".join(ALL_MODES))
        return default
    # Known mode outside this gate's ladder: clamp to the strongest supported mode that
    # does not exceed what was asked for.
    candidates = [a for a in allowed if 0 <= rank(a) <= r]
    return max(candidates, key=rank) if candidates else default


def at_least(candidate: Any, floor: Any) -> bool:
    """True when ``candidate`` is at least as strict as ``floor``.

    Unrecognised values are treated as the weakest possible input, so an unknown string
    never satisfies a floor (fail-safe for the import downgrade guard).
    """
    return rank(candidate) >= rank(floor) and rank(candidate) >= 0


def is_weaker(candidate: Any, current: Any) -> bool:
    """True when moving from ``current`` to ``candidate`` would REDUCE enforcement.

    Used by the agent-pack import downgrade guard. An unrecognised ``candidate`` against a
    recognised ``current`` counts as weaker (it cannot be shown to preserve protection).
    """
    c, cur = rank(candidate), rank(current)
    if cur < 0:
        return False          # no meaningful current posture to weaken
    if c < 0:
        return True           # unknown replacement cannot be proven safe
    return c < cur


def strictest(*modes: Any) -> Optional[str]:
    """The strictest recognised mode among ``modes``; ``None`` when none are recognised."""
    known = [_clean(m) for m in modes if is_known(m)]
    return max(known, key=rank) if known else None


def raw_mode_of(block: Any, *path: str) -> str:
    """Read a mode string out of a nested config block **as written**, before any
    normalization — the input the ``review`` detector needs.

    ``raw_mode_of(profile.get("guardrailConfig"), "outbound", "mode")`` → ``"review"``.
    Returns ``""`` for any missing/!dict step, so callers never need try/except.
    """
    try:
        node: Any = block
        for key in path:
            if not isinstance(node, dict):
                return ""
            node = node.get(key)
        return _clean(node)
    except Exception:  # noqa: BLE001
        return ""


def resolve_effective(raw: Any, *, allowed: Sequence[str] = LADDER,
                      default: str = "observe", context: str = "") -> Tuple[str, bool]:
    """One-call resolution for a gate: ``(effective_mode, review_requested)``.

    ``review_requested`` tells the gate to apply retention (park the original) on top of
    the returned enforcement mode. Gates without turn context should still honour the
    enforcement mode and log that they could not park — failing toward safety.
    """
    return normalize(raw, allowed=allowed, default=default, context=context), is_review(raw)


def any_review(*raws: Any) -> bool:
    """True when any of ``raws`` requests the ``review`` composite."""
    return any(is_review(r) for r in raws)


# ── Agent-level posture inheritance (the five-gates-one-intent consolidation) ────
#
# An agent has ONE hardening intent; the five gates are its surfaces. The profile may
# carry an agent-level default in ``postureConfig.mode`` (same vocabulary, composites
# included); a gate whose own block has NO mode inherits it. Resolution, per gate:
#
#     gate block mode  →  postureConfig.mode  →  "observe"
#     (corpus first follows the grounding gate's EFFECTIVE mode, as it always has)
#
# TRANSPARENCY CONTRACT: every consumer of a resolved mode must be able to name its
# source — ``gate`` (the gate's own block), ``agent`` (inherited from postureConfig),
# ``default`` (nothing configured), or ``grounding`` (the corpus family following the
# grounding gate). ``effective_posture()`` is the ONE resolver: runtimes, the REST
# ``posture`` stamp on GET /v1/profiles, and the signing verdict all read it — a
# hand-rolled second copy is how the ring and the runtime would learn to disagree.

#: The five gate surfaces, with the profile block + path their own mode lives at.
GATE_MODE_SOURCES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("grounding", ("groundingConfig", "mode")),
    ("corpus", ("groundingConfig", "corpus", "mode")),
    ("honesty", ("honestyConfig", "mode")),
    ("outbound", ("guardrailConfig", "outbound", "mode")),
    ("inbound", ("guardrailConfig", "inbound", "mode")),
)


def agent_default_raw(profile: Any) -> str:
    """The agent-level posture default, as written (composites preserved); ``""`` if none."""
    if not isinstance(profile, dict):
        return ""
    return raw_mode_of(profile.get("postureConfig"), "mode")


def gate_raw_or_default(gate_raw: Any, profile: Any) -> str:
    """A gate's RAW mode with agent-level inheritance: the gate's own value when set,
    else the profile's ``postureConfig.mode``, else ``""`` (caller applies its own
    default). Composites (``annotate``/``review``) flow through untouched, so review
    detection and the ring read the same word the runtime enforces under."""
    raw = _clean(gate_raw)
    return raw or agent_default_raw(profile)


def effective_posture(profile: Any) -> Dict[str, Dict[str, str]]:
    """The resolved RAW posture of every gate, each naming its source —
    ``{gate: {"mode": <raw, composites preserved>, "source": gate|agent|grounding|default}}``.

    This is the transparency stamp GET /v1/profiles carries (the ``authorityScope``
    precedent): the Trust Circle, the posture matrix and the inbox's can-park logic
    read THIS instead of re-deriving inheritance — the two-instruments-one-fact
    defect class ends at the single resolver."""
    prof = profile if isinstance(profile, dict) else {}
    agent_raw = agent_default_raw(prof)
    out: Dict[str, Dict[str, str]] = {}
    for gate, path in GATE_MODE_SOURCES:
        block = prof.get(path[0])
        raw = raw_mode_of(block, *path[1:])
        if raw:
            out[gate] = {"mode": raw, "source": "gate"}
        elif gate == "corpus":
            g = out.get("grounding") or {"mode": "", "source": "default"}
            # The corpus family has always followed the grounding gate first; the
            # grounding gate itself may be inherited, and the label says so.
            out[gate] = {"mode": g["mode"] or "observe",
                         "source": ("grounding" if g["source"] == "gate"
                                    else g["source"])}
        elif agent_raw:
            out[gate] = {"mode": agent_raw, "source": "agent"}
        else:
            out[gate] = {"mode": "observe", "source": "default"}
    return out


def stamped_mode(review: bool, resolved: str) -> str:
    """The mode a turn STAMP must record.

    ``review`` is a runtime composite: the gate resolves its config to ``enforce`` and
    adds retention, so the *resolved* mode is what the policy ran under. But the stamp is
    the record of what was CONFIGURED and what happened, and every reader of that record
    — the assurance registry's REVIEWED station above all — needs to tell an armed
    review gate from a plain enforce one.

    Every runtime used to re-tag ``mode: review`` only on the path where the gate ACTED,
    so a review gate that ran and found nothing stamped ``enforce``. REVIEWED then read
    "no gate is set to park answers for a human" on a deployment with three gates armed
    in review — while the Trust Circle, which reads the profile config directly, said the
    agent could park. Two instruments, one fact, two answers.

    One helper rather than the same expression at four call sites: that is the
    hand-rolled-copy defect class this module exists to end.
    """
    return REVIEW if review else resolved
