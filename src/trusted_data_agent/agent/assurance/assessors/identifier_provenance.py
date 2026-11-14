"""
`identifier_provenance` — does every identifier the answer presents as established
actually exist in the skill's own library?

Assessor: **`fabricated_identifier`** — count of ids matching a declared closed
vocabulary that the corpus does not contain.

WHY IT EXISTS (F-002), AND WHY THE CITATION CHECK COULD NOT COVER IT. Session ab53a8f6
designed five modules and settled five decisions. **Four of the five ids do not exist in
the corpus** — and not one turn made a read claim, so `citation_provenance` abstained on
every one of them, correctly and uselessly. The one turn that DID open a section is the
one turn that produced a real id. A natural experiment inside a single session, and the
argument for this check.

WHY IT IS DETERMINISTIC. The vocabulary is CLOSED and the skill declares it
(`uderia.progressive.closed_vocabularies`); the corpus supplies the members. An id
matching the pattern and absent from the members is invented. Nothing is tunable, because
an identifier is real or it is not — which is why this is a BOOLEAN dimension and adds no
slider.

WHY IT IS OPT-IN PER SKILL. ANDP's designer may not coin decisions — its catalogue is
fixed. Another skill may legitimately let its agent name new things, and a platform-wide
rule would flag it for working as designed.
"""

from __future__ import annotations

from ..capabilities import AssessorCapability as C
from ..contract import AssessmentContext, Assessor, Finding, Severity, abstain


class FabricatedIdentifierAssessor(Assessor):
    key = "fabricated_identifier"
    property = "identifier_provenance"
    label = "Fabricated identifier"
    description = ("Identifiers the answer presents as established that the skill's own "
                   "library does not contain. Abstains when the skill declares no closed "
                   "vocabulary — that is a skill saying its agent MAY name new things.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC, C.RETURNS_EVIDENCE})
    #: Empty for the same reason as its sibling: the envelope would read an empty input as
    #: "not supplied" and abstain, and the inputs this genuinely needs are refused inside
    #: `assess` with a reason that can be stated.
    requires = frozenset()

    def assess(self, ctx: AssessmentContext) -> Finding:
        stamp = (ctx.stamp or {}).get("honesty_citation") if ctx.stamp else None
        if isinstance(stamp, dict) and stamp.get("action") not in ("off", "error"):
            if stamp.get("kind") == "fabricated_identifier":
                return self._finding(list(stamp.get("fabricated") or []))
            # The branch ran and settled on the OTHER kind (or nothing). Either way no
            # fabricated identifier was recorded — but a citation verdict PRE-EMPTS this
            # check by declared precedence, so the honest reading is "not assessed", not
            # "clean".
            if stamp.get("kind") == "unread_citation":
                return abstain(self.property, self.key,
                               "an unbacked citation was reported instead — that verdict "
                               "takes precedence and covers the same answer")
            return self._finding([])

        vocab = ((ctx.config or {}).get("vocabulary") or ())
        if not vocab:
            return abstain(self.property, self.key,
                           "this skill declares no closed vocabulary")
        from trusted_data_agent.agent.honesty import primitives as _p
        bad = _p.fabricated_identifiers(
            ctx.answer or "", vocab, (ctx.config or {}).get("valid_identifiers") or ())
        return self._finding(bad)

    def _finding(self, bad) -> Finding:
        bad = list(bad or [])
        return Finding(
            property=self.property, assessor=self.key,
            triggered=bool(bad),
            severity=Severity.CONCERN if bad else Severity.NONE,
            summary=(f"{len(bad)} identifier(s) not found in the library"
                     if bad else "every identifier the answer names exists in the library"),
            metrics={"fabricated": len(bad)},
            evidence=tuple(f"invented: {b}" for b in bad[:5]),
        )


ASSESSOR = FabricatedIdentifierAssessor()
