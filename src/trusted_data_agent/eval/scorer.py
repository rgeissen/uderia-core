"""Scorer abstraction + registry.

A Scorer evaluates ONE dimension of a run and returns a Verdict. Scorers declare
which profile classes they apply to (empty = all), so the runner resolves the
right suite per case (RAG-grounding for Focus, coordination for genie, plan
adherence for Optimize, …). Adding a metric = one class + @register_scorer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional

from trusted_data_agent.eval.types import Verdict, ScoreKind, EvalCase, RunRecord


class Scorer(ABC):
    #: dimension name (e.g. "tool_subset", "groundedness", "cost")
    dimension: str = "unnamed"
    #: deterministic | judge | metric
    kind: ScoreKind = ScoreKind.DETERMINISTIC
    #: profile classes this scorer applies to; empty set = all classes
    applies_to: frozenset = frozenset()
    #: whether this scorer can block a release by default (gate-eligible)
    gating: bool = True

    def applicable(self, profile_class: Optional[str]) -> bool:
        return (not self.applies_to) or (profile_class in self.applies_to)

    @abstractmethod
    def score(self, case: EvalCase, run: RunRecord) -> Verdict:
        """Return a Verdict. Deterministic scorers must not call an LLM."""

    # convenience for subclasses
    def _verdict(self, passed, score=None, explanation="", evidence=None, threshold=None) -> Verdict:
        return Verdict(
            dimension=self.dimension, kind=self.kind, passed=passed, score=score,
            explanation=explanation, evidence=evidence or [], threshold=threshold,
        )


def banded_metric(scorer: "Scorer", *, value: float, gate, healthy=None,
                  higher_is_better: bool, explanation: str, evidence=None) -> Verdict:
    """Build a 3-band (Healthy/Review/Danger) metric verdict from up to two cut points.

    ``gate``    = the Danger boundary (the fail line). None -> informational only
                  (passed=None, band=None) — the metric is measured, never gates.
    ``healthy`` = the Healthy boundary (the green/amber split). None -> single-line
                  mode: green below the gate, red above it, no amber band.
    ``higher_is_better`` True for ``min_*`` metrics, False for ``max_*`` metrics.

    Amber (review) PASSES the gate but is flagged ("Evaluation Recommended");
    only Danger (red) fails. Mirrors the roster's 4-class taxonomy per metric.
    """
    v = float(value)
    if gate is None:
        return Verdict(dimension=scorer.dimension, kind=scorer.kind, passed=None,
                       score=v, explanation=explanation, evidence=evidence or [], band=None)
    g = float(gate)
    h = float(healthy) if healthy is not None else None
    if higher_is_better:                         # min_* : bigger is healthier
        if v < g:
            band = "danger"
        elif h is None or v >= h:
            band = "healthy"
        else:
            band = "review"
    else:                                        # max_* : smaller is healthier
        if v > g:
            band = "danger"
        elif h is None or v <= h:
            band = "healthy"
        else:
            band = "review"
    return Verdict(dimension=scorer.dimension, kind=scorer.kind,
                   passed=(band != "danger"), score=v, threshold=g,
                   explanation=explanation, evidence=evidence or [], band=band)


_REGISTRY: list[Scorer] = []


def register_scorer(scorer: Scorer) -> Scorer:
    """Register a scorer instance (idempotent by dimension+type)."""
    for existing in _REGISTRY:
        if existing.dimension == scorer.dimension and type(existing) is type(scorer):
            return existing
    _REGISTRY.append(scorer)
    return scorer


# #43 — `Scorer.gating` was declared on the class but NEVER reached the Verdict:
# verdicts are built at ~10 sites (each scorer's own returns, the abstain helpers,
# the judge base) and none passed it, so `Verdict.gating` was always its True default
# and the flag was decorative. Consulting the REGISTRY by dimension makes the class
# attribute authoritative wherever it is read, and is immune to a construction site
# forgetting — the same reasoning behind every other "one source per rule" in the
# trust contract.
def is_gating_dimension(dimension: Optional[str]) -> bool:
    """Is this dimension gate-eligible? Unknown dimensions default to GATING —
    silently treating something unrecognised as advisory would hide real failures."""
    for s in _REGISTRY:
        if s.dimension == dimension:
            return getattr(s, "gating", True) is not False
    return True


def all_scorers() -> list[Scorer]:
    return list(_REGISTRY)


def scorers_for(profile_class: Optional[str],
                dimensions: Optional[Iterable[str]] = None,
                kinds: Optional[Iterable[ScoreKind]] = None) -> list[Scorer]:
    """Resolve the scorer suite for a profile class, optionally filtered by
    dimension names and/or kinds."""
    dimset = set(dimensions) if dimensions is not None else None
    kindset = set(kinds) if kinds is not None else None
    # Flows are a distinct agent type, not a profile class: they get the flow
    # scorers (turn-shaped profile scorers assume a turn shape a flow lacks). The
    # exception is the universal task KPIs (tasks / parallel_efficiency), whose
    # scorers opt in by listing "flow" in applies_to — so those cross over and the
    # Quality card reads consistently across profile-agents and flow-agents.
    is_flow = (profile_class == "flow")
    out = []
    for s in _REGISTRY:
        if is_flow:
            if "flow" not in s.applies_to:
                continue
        elif not s.applicable(profile_class):
            continue
        if dimset is not None and s.dimension not in dimset:
            continue
        if kindset is not None and s.kind not in kindset:
            continue
        out.append(s)
    return out
