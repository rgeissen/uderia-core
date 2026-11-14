"""
`retrieval_sufficiency` — did retrieval find enough to justify a specific answer at all?

Assessor: **`retrieval_stats`**, reporting BOTH statistics of one retrieval.

THE PART E "KEEP", MADE ELEGANT (symmetry map A7, scored 3/5). The mechanism
(`honesty.gate.assess_rag`) reads `top_score`; the measurement (`retrieval_relevance`)
reads the MEAN. Part E tested whether that second reading earns its place and concluded it
does — they are different questions:

    top   → "was the BEST chunk good enough to justify answering?"
    mean  → "was the retrieval SET good?"

One excellent chunk among nine poor ones makes those diverge legitimately, so folding to
either one would lose a real reading. The fix is not to choose: ONE assessment returns
both, and each consumer reads the statistic its question needs. They can then disagree
about the ANSWER while never disagreeing about the DATA.

THE CALIBRATION LESSON THIS CARRIES. `assess_rag`'s absolute-threshold branch is off by
default, with the reason stated in its own docstring: *absolute thresholds are
backend-relative*. Its deterministic branch — zero chunks qualified — is
backend-independent and always on. `retrieval_relevance` meanwhile ships a hardcoded floor
that bands red on a healthy agent (measured: median 0.46 against a 0.4 default). Exposing
`qualifying_chunks` here puts the backend-independent signal in reach of both lanes, so
the lesson one side already learned can travel.
"""

from __future__ import annotations

from typing import Any

from ..capabilities import AssessorCapability as C
from ..contract import (
    AssessmentContext, Assessor, Finding, Severity, abstain, plural)


class RetrievalStatsAssessor(Assessor):
    key = "retrieval_stats"
    property = "retrieval_sufficiency"
    label = "Retrieval sufficiency (top and mean)"
    description = ("Reports the top and mean similarity of the retrieved chunks, and "
        "how many clear the configured floor — two different questions about one "
        "retrieval, so both travel.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC, C.RETURNS_EVIDENCE})
    requires = frozenset({"retrieval_scores"})

    def assess(self, ctx: AssessmentContext) -> Finding:
        scores = [float(s) for s in ctx.retrieval_scores]
        if not scores:
            return abstain(self.property, self.key, "no retrieval similarity scores")

        top = round(max(scores), 4)
        mean = round(sum(scores) / len(scores), 4)

        # The threshold an agent configured, if any. Read from config rather than assumed:
        # the mechanism defaults it OFF because an absolute similarity floor does not
        # transfer across embedding models, and an assessor that invented one would
        # manufacture the false red the mechanism deliberately refuses to produce.
        kc = (ctx.config or {}).get("knowledgeConfig") or {}
        try:
            thresh = (float(kc["sufficiencyThreshold"])
                      if kc.get("sufficiencyThreshold") is not None else None)
        except (TypeError, ValueError):
            thresh = None
        qualifying = sum(1 for s in scores if thresh is None or s >= thresh)

        # The mechanism's own rule, reported not applied: S1 (nothing qualified —
        # deterministic, backend-independent) or S2 (the configured floor, opt-in).
        weak = qualifying == 0 or (thresh is not None and top < thresh)

        return Finding(
            property=self.property, assessor=self.key,
            triggered=weak,
            severity=Severity.CONCERN if weak else Severity.NONE,
            summary=(f"top {top:.2f} · mean {mean:.2f} over "
                     f"{plural(len(scores), 'chunk')}"
                     + (" — nothing qualified" if qualifying == 0 else "")),
            evidence=(f"{len(scores)} chunks · top {top:.2f} · mean {mean:.2f}",),
            metrics={"top_score": top, "mean_score": mean,
                     "chunks": float(len(scores)),
                     "qualifying_chunks": float(qualifying)},
        )


ASSESSOR: Any = RetrievalStatsAssessor()
