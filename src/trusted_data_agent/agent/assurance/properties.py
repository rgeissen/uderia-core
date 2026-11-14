"""
The assessable PROPERTIES — the closed vocabulary the whole assurance substrate turns on.

WHY A PROPERTY IS THE UNIT. A mechanism (`grounding_gate`) and a measurement
(`grounded_overlap`) are two consumers of ONE assessment, but nothing in either name says
so, and a reader cannot tell they are about the same thing. The map
(TRUST_SYMMETRY_MAP.md) scored that pair 2/5 precisely because two unrelated pieces of
code assess one property and can disagree for structural reasons.

So the property is named ONCE, and both lanes are named FROM it:

    answer_support          ← the property (stable; this is what is assessed)
      · in-flight           the grounding gate decides on it
      · post-hoc            Grounding Overlap / Numeric Fidelity grade it

A module is named after the PROPERTY it assesses, never after the lane that consumes it —
`assessors/answer_support.py`, not `assessors/grounding_gate.py` — so "remap this module"
means "bind `answer_support` to a different assessor" and BOTH lanes follow. That is the
synergy made visible in the naming, and it is why a remap is one row of data rather than a
patch at two call sites.

A property is stable; an assessor is swappable. Renaming a property is a breaking change
(it appears in bindings, findings and the Guide); replacing its assessor is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Tuple

from .capabilities import Lane


@dataclass(frozen=True)
class Property:
    """One assessable property of a turn."""

    key: str
    label: str
    #: What is being assessed, in the operator's words. This is the sentence the Assurance
    #: Guide shows above BOTH lanes — so it must be true of the property, never of one
    #: implementation of it.
    question: str
    #: The lanes that are MEANINGFUL for this property. Distinct from what an assessor is
    #: CAPABLE of (capabilities.py): `cost` is replayable and pure, but there is no
    #: in-flight lane for it because nothing should withhold an answer for being
    #: expensive. Declaring it here keeps "we have not built it yet" apart from "it would
    #: be the wrong product", which the map's Part B/C distinction depends on.
    lanes: FrozenSet[Lane]
    #: Free text, only where a lane is deliberately absent. Rendered by the Guide as the
    #: honest-absence reading — silence is only honest when it is spoken (Rule 3.2).
    lane_notes: Dict[str, str] = field(default_factory=dict)
    #: The DEPLOYMENT's master switch for this property, if it has one: the `APP_CONFIG`
    #: attribute that must be truthy for the property to be assessed at all. Declared ONCE
    #: here and enforced ONCE in `Assessor.run`, so every reader of the property — the
    #: mechanism's stamp, the post-hoc scorer, the station panel's module line — abstains
    #: with the same words when the lane is off. Before this the three readers each kept
    #: (or forgot) their own copy of the gate, and a panel read "no data operations" on one
    #: line and "1 of 1 found something" on the next, about one turn.
    master_switch: Optional[str] = None
    #: The operator's route to that switch, for the abstention's words.
    master_switch_route: str = "Administration → App Config → Trust"


_BOTH = frozenset({Lane.IN_FLIGHT, Lane.IN_FLIGHT_TRIGGERED, Lane.POST_HOC})
_SCORE_ONLY = frozenset({Lane.POST_HOC})


#: The closed set. Adding a property is a deliberate act: it appears in the Guide, in the
#: binding table and in every Finding it produces.
PROPERTIES: Tuple[Property, ...] = (
    Property(
        key="answer_support",
        label="Answer support",
        question="Is every specific claim in the answer supported by the sources it was "
                 "given?",
        lanes=_BOTH,
    ),
    Property(
        key="answer_scope",
        label="Answer scope",
        question="Does the answer stay inside the data scope the agent was declared to "
                 "work within?",
        lanes=_BOTH,
    ),
    Property(
        key="retrieval_sufficiency",
        label="Retrieval sufficiency",
        question="Did retrieval find enough to justify a specific answer at all?",
        lanes=_BOTH,
    ),
    Property(
        key="context_use",
        label="Context use",
        question="Did the answer actually use what was retrieved, or answer from the "
                 "model's own weights and ignore it?",
        lanes=_BOTH,
    ),
    Property(
        key="absence_claim",
        label="Absence claim",
        question="When the answer says something does not exist, is that backed by a "
                 "query that actually succeeded?",
        lanes=_BOTH,
    ),
    Property(
        key="output_sensitivity",
        label="Output sensitivity",
        question="Does the delivered text carry personal data, abuse, exfiltration-shaped "
                 "links or leaked internals?",
        lanes=_BOTH,
    ),
    Property(
        key="citation_provenance",
        label="Citation provenance",
        question="When the answer says it consulted a section of its skill library, "
                 "did it actually open it?",
        lanes=_BOTH,
    ),
    Property(
        key="identifier_provenance",
        label="Identifier provenance",
        question="Does every identifier the answer presents as established actually "
                 "exist in the skill's library?",
        lanes=_BOTH,
    ),
    Property(
        key="request_integrity",
        label="Request integrity",
        question="Is the incoming request an attempt to subvert the agent's instructions?",
        lanes=frozenset({Lane.IN_FLIGHT}),
        lane_notes={
            "post_hoc": "There is no ground truth to grade a per-turn refusal against — "
                        "whether a blocked request was truly an attack is not decidable "
                        "from the turn. The detector's precision and recall are measured "
                        "against a curated corpus instead (the resilience self-test), "
                        "which is a reading about the DETECTOR, not about this turn.",
        },
    ),
    Property(
        key="data_governance",
        label="Data governance",
        question="Did the turn's data operations go through the governed semantic layer, "
                 "or was SQL free-formed?",
        lanes=_BOTH,
        # the lane exists where the OBDA-READ component is available (the component chain)
        master_switch="component:obda_read",
    ),
    Property(
        key="tool_repetition",
        label="Tool repetition",
        question="Is the agent calling the same tool with the same arguments over and "
                 "over, learning nothing new?",
        lanes=_BOTH,
    ),
    Property(
        key="tool_authority",
        label="Tool authority",
        question="Did every tool call stay inside the authority the agent was granted?",
        # SCORE-ONLY, and this is a modelling distinction rather than a missing feature.
        # Most properties are assessed once PER TURN. Authority is enforced PER CALL, at
        # the three tool chokepoints, fail-closed, before the call runs — so its in-flight
        # consumer is not an assessment of the turn at all. Declaring an in-flight lane
        # here would make the registry read `gap` and invite someone to build a per-turn
        # assessor that arrives after the call it was supposed to stop.
        lanes=_SCORE_ONLY,
        lane_notes={
            "in_flight": "Authority is enforced per CALL at the tool chokepoints, not "
                         "assessed per turn: the decision is made before each call runs, "
                         "fail-closed. A per-turn in-flight assessment would arrive after "
                         "the call it was meant to prevent.",
        },
    ),
)

#: The LIVE vocabulary: the shipped set plus anything a module pack registered.
#:
#: This dict — not the `PROPERTIES` tuple — is the source every lookup reads, so a
#: registered property appears in the binding table, the Guide and conformance with no
#: further wiring. `PROPERTIES` remains exactly what it says: the SHIPPED set, which is
#: what a reader wants when asking "what does the platform itself assess?".
BY_KEY: Dict[str, Property] = {p.key: p for p in PROPERTIES}


def register_property(p: Property) -> list:
    """Add a property from a module pack. Returns refusal reasons; empty = accepted.

    Refuses loudly rather than overwriting: a pack that silently replaced `answer_support`
    would change what every native gate and scorer means by it, on a station that still
    names the platform's guarantee.
    """
    problems = []
    if not p.key:
        problems.append("a property must declare a key")
    elif "." not in p.key:
        # The dot is the discriminator the trust gate, the `builtin` flag and governance
        # all key on. A pack property without one could collide with a future builtin and
        # would be indistinguishable from platform vocabulary in every UI.
        problems.append(
            f"pack property {p.key!r} must be namespaced as '<pack_id>.<key>'")
    elif p.key in BY_KEY:
        problems.append(f"property {p.key!r} already exists")
    if not p.question:
        problems.append(f"property {p.key!r} must state the question it answers — the "
                        f"Guide shows it above both lanes")
    if not p.lanes:
        problems.append(f"property {p.key!r} declares no lanes, so nothing could ever "
                        f"consume it")
    # A lane that is present AND explained-as-absent is a contradiction: the Guide would
    # render the honest-absence note beside a live binding for the same lane.
    contradictory = sorted(l.name.lower() for l in p.lanes
                           if p.lane_notes.get(l.name.lower()))
    if contradictory:
        problems.append(f"property {p.key!r} both declares and explains-away "
                        f"{', '.join(contradictory)}")
    if problems:
        return problems
    BY_KEY[p.key] = p
    return []


def unregister_property(key: str) -> bool:
    """Remove a registered pack property. Never removes a shipped one."""
    if key in {p.key for p in PROPERTIES}:
        return False
    return BY_KEY.pop(key, None) is not None


def get(key: str) -> Optional[Property]:
    return BY_KEY.get(key)


def keys() -> Tuple[str, ...]:
    """Every LIVE property key — shipped first, then registered, each group stable."""
    shipped = tuple(p.key for p in PROPERTIES)
    extra = tuple(sorted(k for k in BY_KEY if k not in set(shipped)))
    return shipped + extra


def all_properties() -> Tuple[Property, ...]:
    """Every live property, in `keys()` order. What the binding table iterates."""
    return tuple(BY_KEY[k] for k in keys() if k in BY_KEY)


def lane_is_meaningful(prop_key: str, lane: Lane) -> bool:
    """Whether this property has this lane at all.

    A lane that is absent BY DESIGN is not a gap, and the two must never be conflated:
    the map's whole Part B/C distinction rests on it, and a UX offering a binding for a
    lane that should not exist would invite an operator to build the wrong product.
    """
    p = BY_KEY.get(prop_key)
    return bool(p and lane in p.lanes)
