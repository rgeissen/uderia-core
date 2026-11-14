"""
`context_use` — did the answer actually use what was retrieved, or answer from the model's
own weights and ignore it?

Assessor: **`content_word_overlap`** — the fraction of the answer's distinct content words
(≥5 chars) that appear in the retrieved chunk text.

WHY THIS PROPERTY EXISTS AT ALL (symmetry map A8, the only row that closed a DETECTION gap
rather than a presentation one). Both grounding mechanisms are blind to one answer shape:

    corpus.assess       triggers on SPANS — numbers, verbatim quotes, identifiers
    assess_rag          requires `claims_checked > 0`

A fluent fabrication carries none of those. The codebase records the case:
*"Quantum entanglement enables faster-than-light teleportation of refunds"* — no number,
no quote, no identifier, so both gates abstain and it ships. This assessor does not need
spans; it asks whether the answer's words appear in its sources at all.

WHY IT IS A TRIGGER AND NEVER A BLOCKER. It false-positives on BREVITY: a correct terse
summary and an honest refusal both score low, because neither repeats much source
vocabulary. So a low reading escalates to the judge that already adjudicates the corpus
family, and the judge makes the call — the tiered-enforce shape the gate already has. A
deterministic blocker here would withhold correct answers for being short.
"""

from __future__ import annotations

import re as _re
from typing import Any

from ..capabilities import AssessorCapability as C
from ..contract import AssessmentContext, Assessor, Finding, Severity, abstain

_CONTENT_WORD = _re.compile(r"[A-Za-z][A-Za-z_\-]{4,}")


def _content_words(text: str) -> set:
    return {w.lower() for w in _CONTENT_WORD.findall(text or "")}


class ContentWordOverlapAssessor(Assessor):
    key = "content_word_overlap"
    property = "context_use"
    label = "Answer's use of retrieved context"
    description = ("Measures how much of the answer's content vocabulary appears in the "
        "retrieved chunks at all — a span-free check, so it sees an answer written from "
        "the model's own weights that ignored what it was given.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC})
    #: The model's own output is the subject — an answer the platform replaced did not
    #: ignore its sources, it was never the model's answer at all.
    subject_is_model_output = True
    requires = frozenset({"answer", "chunks"})

    def assess(self, ctx: AssessmentContext) -> Finding:
        answer_words = _content_words(ctx.answer)
        if not answer_words:
            return abstain(self.property, self.key, "no scoreable answer content")
        corpus_words = _content_words(" ".join(ctx.chunks))
        if not corpus_words:
            return abstain(self.property, self.key, "retrieved content carries no words")

        used = answer_words & corpus_words
        ratio = round(len(used) / len(answer_words), 4)

        # The floor is read from config and NEVER invented. An absolute overlap floor does
        # not transfer across answer styles any more than a similarity floor transfers
        # across embedding models — the calibration lesson `assess_rag` already carries.
        # With no floor configured the finding REPORTS and never triggers.
        floor = (ctx.config or {}).get("min_context_utilization")
        try:
            floor = float(floor) if floor is not None else None
        except (TypeError, ValueError):
            floor = None
        low = floor is not None and ratio < floor

        return Finding(
            property=self.property, assessor=self.key,
            triggered=low,
            severity=Severity.CONCERN if low else Severity.NONE,
            summary=(f"{int(ratio * 100)}% of the answer's content words appear in the "
                     f"retrieved sources"
                     + (f" — below the {int(floor * 100)}% floor" if low else "")),
            # No sentinel for "unset": a metric is a number a reader can use, and a -1
            # the renderer must know to hide is a leaky contract. The floor's presence is
            # already stated in the summary, which is where prose belongs.
            metrics={"context_use_ratio": ratio,
                     "answer_words": float(len(answer_words))},
        )


ASSESSOR: Any = ContentWordOverlapAssessor()
