"""
`answer_support` — is the answer supported by the sources it was given?

Assessor: **`corpus_support`** — `agent/grounding/corpus.py:assess()` for answers that make
checkable claims, plus a lexical branch for answers that do not.

WHY TWO BRANCHES, AND WHY THAT IS NOT TWO IMPLEMENTATIONS. The symmetry map recommended
replacing the `grounded_overlap` scorer wholesale with `corpus.assess`, on a measurement
over the committed corpus benchmark: 4 of 18 cases diverged and the sentence-overlap
version was wrong in all four (a fabricated figure inside an otherwise-grounded sentence
scored 1.0 fully supported).

**That recommendation was wrong, and the eval gate caught it.** The benchmark corpus is
span-based by construction, so it could not show the opposite failure: a fluent fabrication
("Quantum entanglement enables faster-than-light teleportation of refunds") carries no
number, quote or identifier, so the span check has nothing to grade and abstains — letting
through the one thing a groundedness check most needs to catch. The scorer's own history
records this exact change being tried and reverted the same hour; the gate failed on it
again here.

So the two are **complementary, not redundant**, and the property is assessed by the branch
that fits the ANSWER'S SHAPE:

    claims present  → corpus.assess (spans) + second-chance clearing
    no claims       → lexical sentence overlap

One assessor doing its property properly. Two things follow that neither algorithm had
alone: the post-hoc lane stops convicting spans the gate cleared (the whole measured
divergence with the `groundedness` judge, which was mechanical rather than epistemic), and
because the gate binds to this same property, the **in-flight lane gains span-free
detection it never had** — the map's A8 detection gap, closed by the binding rather than by
separate wiring.
"""

from __future__ import annotations

import re as _re
from typing import Any

from ..capabilities import AssessorCapability as C
from ..contract import (
    AssessmentContext, Assessor, Finding, Severity, abstain, plural)


_WORD_RE = _re.compile(r"[A-Za-z0-9]{4,}")


def _lexical_support(answer: str, chunks, question: str) -> float:
    """The span-free branch: fraction of answer SENTENCES whose significant tokens overlap
    the support set by >= 50%.

    Preserved VERBATIM from the scorer this assessor replaced, because it is not a worse
    version of the span check — it is the only branch that sees a different failure shape.
    See the class docstring.
    """
    corpus = " ".join(list(chunks) + [question or ""]).lower()
    corpus_tokens = set(_WORD_RE.findall(corpus))
    sentences = [s for s in _re.split(r"[.!?\n]+", answer) if s.strip()]
    if not sentences:
        return 1.0
    supported = 0
    for s in sentences:
        toks = set(_WORD_RE.findall(s.lower()))
        if not toks:
            supported += 1
            continue
        if len(toks & corpus_tokens) / len(toks) >= 0.5:
            supported += 1
    return round(supported / len(sentences), 4)


class CorpusSupportAssessor(Assessor):
    key = "corpus_support"
    property = "answer_support"
    label = "Corpus support (claims, then lexical)"
    description = ("Extracts the answer's checkable claims — numbers, verbatim quotes, "
        "identifiers — and looks for each in the retrieved chunks; an answer carrying "
        "no such span falls back to lexical overlap, so a fluent fabrication is still "
        "caught.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC, C.RETURNS_EVIDENCE})
    #: The question joins the support set — an answer echoing the user's own terms is not
    #: inventing them. `corpus.assess` has always treated it that way; the scorer had to be
    #: told separately, in a comment, which is the hand-propagation this contract removes.
    #: grounding asks whether the MODEL supported its claims; the platform's withholding notice is not the model's answer, and it QUOTES the spans it is reporting as unsupported — engineered to fail this check.
    subject_is_model_output = True
    requires = frozenset({"answer", "chunks"})

    def assess(self, ctx: AssessmentContext) -> Finding:
        from trusted_data_agent.agent.grounding import corpus as _corpus

        findings = _corpus.assess(ctx.answer, list(ctx.chunks), ctx.question or "")

        # Second-chance clearing is PURE and the cleared spans are already recorded on the
        # turn by `GroundingVerdict.to_event()`. Applying it here is what makes the
        # post-hoc reading identical to the in-flight one rather than a harsher twin: the
        # scorer used to convict claims the gate had cleared, which is the entire measured
        # divergence between `grounded_overlap` and `groundedness`.
        cleared = tuple(((ctx.stamp or {}).get("grounding") or {})
                .get("second_chance_cleared") or ())
        if cleared:
            findings = _corpus.apply_second_chance(findings, cleared)

        if not findings.claims_checked:
            # ── the span-free branch ─────────────────────────────────────────
            # A fluent fabrication carries no number, quote or identifier, so the span
            # check has nothing to grade — and abstaining here would let exactly that
            # answer through. (Proven twice: the scorer's own history records this change
            # being tried and reverted the same hour, and the eval gate failed on it
            # again when this assessor first shipped claims-only.)
            #
            # So the property is assessed by the branch that fits the ANSWER'S SHAPE. That
            # is one assessor doing its property properly, not two implementations of it —
            # and because the gate binds to this same property, the in-flight lane gains
            # span-free detection it never had.
            ratio = _lexical_support(ctx.answer, list(ctx.chunks), ctx.question or "")
            low = ratio < 0.5
            return Finding(
                property=self.property, assessor=self.key,
                triggered=low,
                severity=Severity.CRITICAL if low else Severity.NONE,
                summary=(f"no checkable claim; {int(ratio * 100)}% of the answer's "
                         f"sentences overlap the sources"),
                evidence=(),
                # `branch_lexical` was an internal flag in a reader-facing field; the
                # summary already names which branch decided it.
                metrics={"support_ratio": ratio, "claims_checked": 0.0},
            )

        unsupported = tuple(findings.unsupported_spans)
        supported = max(0, findings.claims_checked - len(unsupported))
        ratio = round(supported / findings.claims_checked, 4)

        metrics = {"support_ratio": ratio,
                   "claims_checked": float(findings.claims_checked)}
        if findings.numeric_fidelity is not None:
            metrics["numeric_fidelity"] = float(findings.numeric_fidelity)

        if unsupported:
            summary = (f"{len(unsupported)} of "
                       f"{plural(findings.claims_checked, 'claim')} "
                       f"unsupported by the sources")
            if findings.second_chance_cleared:
                summary += (f" · {len(findings.second_chance_cleared)} cleared on a "
                            f"second look")
        else:
            summary = (f"all {plural(findings.claims_checked, 'checkable claim')} "
                       f"supported")
            if findings.second_chance_cleared:
                summary += (f" · {len(findings.second_chance_cleared)} cleared on a "
                            f"second look")

        return Finding(
            property=self.property, assessor=self.key,
            triggered=bool(unsupported),
            severity=Severity.CRITICAL if unsupported else Severity.NONE,
            summary=summary,
            evidence=unsupported,
            metrics=metrics,
            payload=findings,
        )


ASSESSOR: Any = CorpusSupportAssessor()
