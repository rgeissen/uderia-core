"""
Assessor capability enum.

Mirrors ``vectorstore/capabilities.py`` and ``kgstore/capabilities.py`` — the house
pattern for every pluggable layer on this platform.

WHY THIS EXISTS. A binding will eventually be remappable from a UX: an operator picks
which module assesses a property and which lanes consume it. Some of those choices are
not merely unwise, they are **unsafe** — binding a judge that takes seconds as an
unconditional in-flight blocker would put a model call on every turn's hot path, and
binding an assessor that needs live retrieval to the post-hoc lane would make scoring
depend on I/O that may no longer be available.

A warning in a dialog does not prevent that. A capability does: lane eligibility is
DERIVED from what a module can do, so an unsafe binding is not offered and is refused if
constructed by hand (`conformance.check_binding`).

The roadmap states this as *"a lane is a capability, not a preference"*
(TRUST_RUNTIME_COMPREHENSIBILITY.md §6.4).
"""

from enum import Enum, auto
from typing import FrozenSet, Set


class AssessorCapability(Enum):
    # ── Cost / safety on the request path ─────────────────────────────
    PURE = auto()            # no I/O, no model call, no clock — safe on the hot path
    NEEDS_MODEL = auto()     # calls an LLM; seconds and tokens per invocation
    NEEDS_IO = auto()        # network / database / filesystem

    # ── Where it can run ──────────────────────────────────────────────
    REPLAYABLE = auto()      # can run over a PERSISTED turn, not just a live one

    # ── What it produces ──────────────────────────────────────────────
    DETERMINISTIC = auto()   # same inputs → same finding, always
    RETURNS_METRIC = auto()  # named numbers a band can be applied to (the score lane)
    RETURNS_EVIDENCE = auto()  # instances a station can NAME (the reason a gate acted)


#: Every assessor must be able to say what it produces, or neither lane can use it:
#: a mechanism with no evidence cannot state a reason, and a measurement with no
#: metric and no pass/fail has nothing to band.
REQUIRED_CAPABILITIES: FrozenSet[AssessorCapability] = frozenset()


class Lane(Enum):
    """Where an assessment is consumed. NOT a property of the assessor — a property of
    the binding. One assessor may legitimately feed both, which is the whole point."""
    IN_FLIGHT = auto()            # during the turn; its finding can move the answer
    IN_FLIGHT_TRIGGERED = auto()  # during the turn, but only when a cheap trigger fired
    POST_HOC = auto()             # after the answer shipped; grades, never acts


#: What a lane demands of an assessor. Read by `conformance.check_binding`, which is the
#: ONE place a binding is validated — the UX, the REST layer and the loader all call it.
LANE_REQUIREMENTS = {
    # An unconditional in-flight assessor runs on EVERY turn, so it must be free.
    Lane.IN_FLIGHT: frozenset({AssessorCapability.PURE}),
    # Behind a trigger, a model call is affordable: it runs only on the turns a cheap
    # deterministic signal already flagged. This is the tiered-enforce shape the
    # grounding gate already uses (`needs_judge` → judge decides).
    Lane.IN_FLIGHT_TRIGGERED: frozenset(),
    # Scoring runs over a turn that has already been persisted, so anything that cannot
    # be replayed from the record is structurally ineligible — it would score whatever
    # the world looks like now, not what the turn saw.
    Lane.POST_HOC: frozenset({AssessorCapability.REPLAYABLE}),
}


def lanes_for(caps: Set[AssessorCapability]) -> FrozenSet[Lane]:
    """The lanes an assessor with these capabilities may be bound to.

    Derived, never declared per assessor: a module that gains `NEEDS_MODEL` loses the
    unconditional in-flight lane automatically, without anyone remembering to update a
    second list.
    """
    have = frozenset(caps or ())
    return frozenset(
        lane for lane, need in LANE_REQUIREMENTS.items() if need <= have
    )
