"""
Binding conformance — the ONE place a remap is validated.

Mirrors `kgstore/conformance.py`. Every caller that can change a binding — the registry's
own `bind()`, the REST layer, and the remapping UX that comes later — asks this module,
so a rule cannot be enforced in the dialog and forgotten in the API.

WHAT IT REFUSES, AND WHY EACH REFUSAL IS STRUCTURAL RATHER THAN ADVISORY:

  * an assessor for a DIFFERENT property — a binding table that lets `answer_support` be
    served by a module assessing output sensitivity produces findings whose summary and
    metrics describe something else entirely, on a station that names the wrong guarantee;
  * a lane the property does not have — `request_integrity` has no post-hoc lane because
    there is no ground truth to grade a refusal against, so offering the binding would
    invite an operator to build a reading that cannot be honest;
  * a lane the assessor cannot serve — a model-calling judge bound as an UNCONDITIONAL
    in-flight assessor puts seconds and tokens on every turn's hot path; a non-replayable
    assessor bound post-hoc would score the world as it is now rather than as the turn saw
    it. Neither is a matter of preference (capabilities.py);
  * inputs the lane cannot supply — the failure that would be invisible: the module binds,
    runs, receives an empty input, and reports clean forever. A config screen must never be
    the only thing between a missing input and a fabricated green.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List

from . import contract as _ctx
from . import properties as _props
from . import registry as _reg
from .capabilities import Lane

#: What each lane is able to put in an `AssessmentContext`. The in-flight lanes run while
#: the turn is executing, so they have the live answer, calls and chunks but NOT the
#: mechanism's own stamp — that does not exist until the verdict is recorded. The post-hoc
#: lane replays a persisted turn, so it has everything the record kept, including the
#: stamp, which is exactly what lets a measurement READ a finding instead of re-deriving.
LANE_SUPPLIES: Dict[Lane, FrozenSet[str]] = {
    Lane.IN_FLIGHT: frozenset({
        "answer", "question", "tool_calls", "chunks", "retrieval_scores",
        "scope_databases", "config"}),
    Lane.IN_FLIGHT_TRIGGERED: frozenset({
        "answer", "question", "tool_calls", "chunks", "retrieval_scores",
        "scope_databases", "config"}),
    # `tool_outputs` is post-hoc only: `run_context.from_run` derives it from the recorded
    # tool results, which is exactly the evidence a replay has and a live turn assembles
    # incrementally. It was missing here while `AssessmentContext.support_set()` was built
    # on it, so an assessor declaring it could never bind to ANY lane — a whole half of
    # the support set was unreachable by construction.
    Lane.POST_HOC: frozenset({
        "answer", "question", "tool_calls", "chunks", "retrieval_scores",
        "scope_databases", "config", "stamp", "tool_outputs"}),
}

#: Every field an assessor may legitimately declare in `requires`.
#:
#: Derived from the context dataclass rather than typed out, so a new field is declarable
#: the moment it exists. `platform_authored` is deliberately EXCLUDED: it is envelope
#: state consumed by `Assessor.run()` (the subject-is-model-output invariant), never an
#: input an assessor reads — declaring it would make an assessor abstain on exactly the
#: turns the envelope already handles.
_DECLARABLE: FrozenSet[str] = frozenset(
    f for f in getattr(_ctx.AssessmentContext, "__dataclass_fields__", {})
    if f != "platform_authored")


def check_binding(prop: str, assessor_key: str, lane: Lane) -> List[str]:
    """Every reason this binding must be refused. Empty list = permitted."""
    problems: List[str] = []

    p = _props.get(prop)
    if not p:
        return [f"unknown property {prop!r}"]

    a = _reg.get(assessor_key)
    if a is None:
        return [f"unknown assessor {assessor_key!r}"]

    if a.property != prop:
        problems.append(
            f"{assessor_key!r} assesses {a.property!r}, not {prop!r} — its findings would "
            f"describe a different property under this one's name")

    if lane not in p.lanes:
        note = p.lane_notes.get(lane.name.lower())
        problems.append(
            f"{p.label} has no {lane.name} lane" + (f": {note}" if note else ""))

    if lane not in a.lanes():
        have = ", ".join(sorted(c.name for c in a.capabilities)) or "none"
        problems.append(
            f"{assessor_key!r} cannot serve {lane.name}: it declares [{have}]")

    # An input no context can EVER carry is a different fault from one this lane happens
    # not to supply: the first is a typo or a misunderstanding of the contract and would
    # make the assessor abstain on every turn forever, so it is named as its own reason
    # rather than reported as a lane limitation the operator might try to work around.
    unknown = sorted(set(a.requires) - _DECLARABLE)
    if unknown:
        problems.append(
            f"{assessor_key!r} requires {', '.join(unknown)}, which is not an assessment "
            f"input — no lane can ever supply it")

    supplies = LANE_SUPPLIES.get(lane, frozenset())
    missing = sorted((set(a.requires) & _DECLARABLE) - supplies)
    if missing:
        problems.append(
            f"{lane.name} cannot supply {', '.join(missing)}, which {assessor_key!r} "
            f"requires — it would run and report clean without ever seeing its input")

    # A third-party module (namespaced `<pack>.<key>` — the dot is the discriminator) may
    # only be bound while it is signed and intact. Builtins are trusted by shipping, as
    # component types are. Enforced HERE rather than at the REST layer so every caller —
    # the studio, the API, a future importer — inherits the gate from `registry.bind`.
    if "." in assessor_key:
        problems.extend(_third_party_trust_problems(assessor_key))

    return problems


def _third_party_trust_problems(assessor_key: str) -> List[str]:
    """Refusals arising from a third-party module's trust state. FAILS CLOSED.

    The trust suite is physically removable (it ships inside the Ontology Suite), so its
    absence is not an error — but it is also not permission: without it there is no way to
    establish that a third-party module is what it claimed to be, and binding one anyway
    would put unverified code in the assurance path. So absence REFUSES, naming why.
    """
    pack_id = assessor_key.split(".", 1)[0]
    try:
        from trusted_data_agent.core.trust import store as _tstore
        from trusted_data_agent.core.trust import content as _tcontent
        from trusted_data_agent.core.trust.attestation import resolve_state, TrustState
    except Exception:  # noqa: BLE001 — suite absent or unimportable
        return [f"third-party module {assessor_key!r} cannot be bound: the trust suite is "
                f"not present, so its signature cannot be verified"]
    try:
        live = _tcontent.live_hash("assurance_module", pack_id)
        attested = _tstore.attested_hash("assurance_module", pack_id)
        state = resolve_state(attested, live)
    except Exception as exc:  # noqa: BLE001
        return [f"third-party module {assessor_key!r} cannot be bound: its trust state "
                f"could not be resolved ({exc})"]
    if state == TrustState.TRUSTED:
        return []
    return [f"third-party module {assessor_key!r} is not signed and intact "
            f"(state: {getattr(state, 'value', state)}) — sign the pack "
            f"'{pack_id}' under Config → Trust before binding it"]


def candidates(prop: str, lane: Lane) -> List[Dict[str, object]]:
    """Every assessor that COULD serve this property in this lane, with the reasons the
    rejected ones were rejected.

    A UX that lists only the valid options teaches nothing; one that shows why a module is
    unavailable is how an operator learns the model — the same reasoning behind naming a
    laggard rather than showing a bare amber (Rule 5.4).
    """
    out: List[Dict[str, object]] = []
    for a in _reg.assessors():
        problems = check_binding(prop, a.key, lane)
        if a.property != prop:
            continue  # a different property is not a candidate, it is a category error
        out.append({**a.describe(), "eligible": not problems, "blocked_by": problems})
    return out


def audit() -> List[Dict[str, object]]:
    """Every binding that is currently invalid.

    Bindings are data and data drifts: an assessor can be renamed, a property can gain a
    capability requirement, a module can stop being loaded. This is the check that turns
    that drift into a visible fault instead of a property that quietly stopped being
    assessed.
    """
    bad: List[Dict[str, object]] = []
    for row in _reg.bindings():
        if not row.get("bound"):
            continue
        lane = Lane[str(row["lane"])]
        problems = check_binding(str(row["property"]), str(row["assessor"]), lane)
        if problems:
            bad.append({**row, "problems": problems})
    return bad
