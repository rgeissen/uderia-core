"""
The assurance substrate — ONE assessment of a property, consumed by up to two lanes.

    assess(ctx)           -> Finding    pure; what was found, no policy
    decide(finding, mode) -> Decision   in-flight; may move the answer
    score(finding, bands) -> Verdict    post-hoc; grades, never acts

Read `contract.py` first: it records what the shape was DERIVED from and why a post-hoc
lane cannot bind to a function that has already applied a policy.

Bindings are DATA (`registry.bindings()`), validated in one place
(`conformance.check_binding`), so a later phase can expose remapping through a UX without
any call site changing: a call site asks `registry.resolve(property, lane)` and never
imports an assessor.

Absence is loud, not silent. `resolve` returning `None` means NOT ASSESSED — never
assessed-clean — and this package deliberately ships no permissive stand-in.
"""

import logging as _logging

from .capabilities import AssessorCapability, Lane, lanes_for  # noqa: F401
from .contract import (  # noqa: F401
    AssessmentContext, Assessor, Finding, Severity, abstain,
)
from . import properties, registry, conformance, store  # noqa: F401

logger = _logging.getLogger("quart.app")

_BOOTSTRAPPED = False

#: Which properties a GATE actually resolves during a turn, and which gate does it.
#:
#: Declared as data because the honest reading differs from the binding table: a property
#: can be BOUND in flight and still be consulted by nobody, in which case rebinding it is
#: a decoration wearing a control's clothes. The binding studio renders that difference
#: (PROP-003 §9.3) rather than implying every in-flight row can move an answer.
#:
#: When a gate gains a `registry.resolve(...)` call site, add its row here — that is the
#: ONLY change required, and `test_assurance_substrate` asserts this map against the real
#: call sites so the two cannot drift.
INFLIGHT_CONSUMERS = {
    # Resolved DIRECTLY by a gate, as the gate's own assessment.
    "context_use": "grounding_gate",
    "retrieval_sufficiency": "honesty_gate",
    # Consulted through the in-flight seam (`inflight.augment`): the gate performs its own
    # native assessment AND takes whatever a bound third-party module found, deciding over
    # the union. A shipped module is skipped here — the gate already made that assessment
    # natively, and running it twice would double-count one finding.
    "output_sensitivity": "outbound_guard",
    "request_integrity": "inbound_guard",
    "answer_support": "grounding_gate",
    "answer_scope": "grounding_gate",
}

#: Properties a gate consults through the seam RATHER than resolving itself. Kept apart
#: from the map above because the two are different readings: the gate has its own
#: assessment for these and a plugin ADDS to it, whereas `context_use` IS the gate's
#: assessment. The studio needs the distinction to say the honest thing about a cell.
SEAM_CONSULTED = frozenset({
    "output_sensitivity", "request_integrity", "answer_support", "answer_scope",
})


def _apply_defaults() -> None:
    """The shipped binding table. A default that cannot bind is a BUILD error."""
    for prop, key, lanes in (
        ("answer_support",  "corpus_support",     (Lane.IN_FLIGHT, Lane.POST_HOC)),
        ("absence_claim",   "honesty_absence",  (Lane.IN_FLIGHT, Lane.POST_HOC)),
        ("data_governance", "obda_lanes",       (Lane.IN_FLIGHT, Lane.POST_HOC)),
        # POST_HOC only: it reads the sealed scope stamp, which does not exist until the
        # turn is recorded. In flight the boundary needs enforcement (already at the tool
        # chokepoints), not a measurement — `conformance` refuses the other binding.
        ("tool_authority",  "authority_scope",  (Lane.POST_HOC,)),
        ("answer_scope",    "kg_scope_diff",    (Lane.IN_FLIGHT, Lane.POST_HOC)),
        # BOTH lanes, now that `evaluate_outbound` is split: in flight it scans with the
        # guard's own assessor, post-hoc it reads what the guard recorded.
        ("output_sensitivity", "guardrail_findings", (Lane.IN_FLIGHT, Lane.POST_HOC)),
        # BOTH lanes, now that `assess_rag` is split into scan + decide AND the raw
        # similarity scores are threaded to the gate. The gate keeps the REMEDY — the only
        # half that can move an answer — and reads the assessment from here.
        ("retrieval_sufficiency", "retrieval_stats", (Lane.IN_FLIGHT, Lane.POST_HOC)),
        # IN_FLIGHT_TRIGGERED, never IN_FLIGHT: a low reading escalates to the judge that
        # already adjudicates the corpus family rather than deciding anything itself. It
        # false-positives on brevity, so a deterministic blocker here would withhold
        # correct answers for being short.
        ("context_use", "content_word_overlap",
         (Lane.IN_FLIGHT_TRIGGERED, Lane.POST_HOC)),
        ("tool_repetition", "identical_call_repeats", (Lane.IN_FLIGHT, Lane.POST_HOC)),
        # BOTH lanes, and the post-hoc one reads the stamp the in-flight one wrote —
        # not a re-derivation. `AssessmentContext.config` carries no profile after the
        # turn, so the anchor vocabulary is unreachable there; and re-deriving from
        # DELIVERED text would meet the disclosure the gate itself appended, which names
        # the unread sections, and could read the platform's own correction as a fresh
        # claim by the agent. Recovering the assessment is strictly stronger than
        # repeating it.
        ("citation_provenance", "cited_but_unread", (Lane.IN_FLIGHT, Lane.POST_HOC)),
        ("identifier_provenance", "fabricated_identifier",
         (Lane.IN_FLIGHT, Lane.POST_HOC)),
    ):
        problems = registry.bind(prop, key, lanes)
        if problems:  # a shipped default that cannot bind is a build error, not a warning
            raise RuntimeError(f"default binding {prop}->{key} refused: {problems}")


def _apply_overrides(db_path=None) -> None:
    """The deployment's edits, applied ON TOP of the shipped defaults.

    FAILS SOFT AND LOUD (PROP-003 §8 R3): an override that no longer validates — its
    assessor was uninstalled, the property lost a lane, a third-party module lost its
    signature — is logged at WARNING and SKIPPED, leaving the shipped default in force.
    It must never raise: startup failing because a stored preference went stale would
    take the whole platform down to protect a configuration row.
    """
    for row in store.load_overrides(db_path):
        prop = str(row.get("property") or "")
        lane_name = str(row.get("lane") or "")
        key = row.get("assessor_key")
        try:
            lane = Lane[lane_name]
        except KeyError:
            logger.warning("assurance: binding override for %s names unknown lane %r — "
                           "skipped; the shipped default remains in force", prop, lane_name)
            continue
        if key is None:
            # An explicit unbind. Deliberately NOT validated: choosing "not assessed" is
            # always a permissible act, and it is the one state that cannot be unsafe.
            registry.unbind(prop, lane)
            continue
        problems = registry.bind(prop, str(key), (lane,))
        if problems:
            logger.warning("assurance: binding override %s/%s -> %s refused (%s) — "
                           "skipped; the shipped default remains in force",
                           prop, lane_name, key, "; ".join(problems))


def bootstrap(db_path=None) -> None:
    """Register the shipped assessors, apply the DEFAULT bindings, then the overrides.

    Idempotent, and safe to call from anywhere. The defaults reproduce what the platform
    does today; a deployment override replaces a row, it does not replace this function.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    from .assessors import load_all

    for a in load_all():
        registry.register(a)

    _apply_defaults()
    _apply_overrides(db_path)
    _BOOTSTRAPPED = True


def reload_overrides(db_path=None) -> None:
    """Re-read the override layer after a write, without a process restart.

    Re-applies the defaults first so a REMOVED override reverts to its shipped row —
    `_apply_defaults` is an idempotent set of re-binds, and calling `reset_for_tests()`
    here would drop the registered assessors along with the table.
    """
    if not _BOOTSTRAPPED:
        bootstrap(db_path)
        return
    _apply_defaults()
    _apply_overrides(db_path)


def reset_for_tests() -> None:
    global _BOOTSTRAPPED
    registry.reset_for_tests()
    _BOOTSTRAPPED = False
