"""Deterministic flow scorers — the moat extends to flows.

Flows are graded in CODE only (no LLM), on the same grade-what-matters principle:
the run completed, every executed node succeeded, the required nodes were reached
(subset semantics), forbidden nodes were NOT reached (wrong-branch detection),
and cost/steps stayed within budget.

All scorers `applies_to={"flow"}`. `scorers_for("flow")` returns ONLY these (the
turn-shaped profile scorers never run against a flow record).

Assertion keys (on EvalCase.assertions):
  expect_complete:bool(default True)  require_nodes:[id|label|type]  forbid_nodes:[…]
  max_cost_usd:float  max_steps:int
"""
from __future__ import annotations

from trusted_data_agent.eval.types import Verdict, ScoreKind, EvalCase
from trusted_data_agent.eval.scorer import Scorer, register_scorer
from trusted_data_agent.eval.flow_types import FLOW_CLASS, FlowRunRecord

_FLOW = frozenset({FLOW_CLASS})


def _na(scorer, why):
    return Verdict(dimension=scorer.dimension, kind=scorer.kind, passed=None, explanation=why)


class FlowCompletedScorer(Scorer):
    dimension = "flow_completed"
    kind = ScoreKind.DETERMINISTIC
    applies_to = _FLOW

    def score(self, case: EvalCase, run: FlowRunRecord) -> Verdict:
        if case.assertions.get("expect_complete") is False:
            return _na(self, "case does not expect completion")
        ok = run.completed and run.is_valid and not run.error_message
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation="flow run completed" if ok
                             else f"status={run.status} error={run.error_message!r}")


class FlowNodesSucceededScorer(Scorer):
    dimension = "flow_nodes_succeeded"
    kind = ScoreKind.DETERMINISTIC
    applies_to = _FLOW

    def score(self, case: EvalCase, run: FlowRunRecord) -> Verdict:
        failed = run.failed_nodes()
        ok = not failed
        ev = [f"{n.get('node_id')} ({n.get('node_type')}): {n.get('error_msg')}" for n in failed]
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation="all executed nodes succeeded" if ok
                             else f"{len(failed)} node(s) failed", evidence=ev)


class FlowNodesReachedScorer(Scorer):
    """Superset semantics: every required node was executed (extra is fine)."""
    dimension = "flow_nodes_reached"
    kind = ScoreKind.DETERMINISTIC
    applies_to = _FLOW

    def score(self, case: EvalCase, run: FlowRunRecord) -> Verdict:
        required = case.assertions.get("require_nodes")
        if not required:
            return _na(self, "no require_nodes assertion")
        reached = run.executed_identifiers()
        missing = [n for n in required if n not in reached]
        ok = not missing
        return self._verdict(ok, score=1.0 if ok else (1 - len(missing) / len(required)),
                             explanation="all required nodes reached" if ok
                             else f"nodes not reached: {missing}", evidence=missing)


class FlowForbiddenNodesScorer(Scorer):
    """Wrong-branch detection: nodes that should NOT have executed."""
    dimension = "flow_forbidden_nodes"
    kind = ScoreKind.DETERMINISTIC
    applies_to = _FLOW

    def score(self, case: EvalCase, run: FlowRunRecord) -> Verdict:
        forbidden = case.assertions.get("forbid_nodes")
        if not forbidden:
            return _na(self, "no forbid_nodes assertion")
        reached = run.executed_identifiers()
        hit = [n for n in forbidden if n in reached]
        ok = not hit
        return self._verdict(ok, score=1.0 if ok else 0.0,
                             explanation="no forbidden nodes executed" if ok
                             else f"forbidden nodes executed (wrong branch?): {hit}", evidence=hit)


class FlowCostScorer(Scorer):
    dimension = "flow_cost"
    kind = ScoreKind.METRIC
    applies_to = _FLOW

    def score(self, case: EvalCase, run: FlowRunRecord) -> Verdict:
        mx = case.assertions.get("max_cost_usd")
        cost = run.cost_usd
        if mx is None:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None,
                           score=cost, explanation=f"cost=${cost:.4f} (no ceiling set)")
        ok = cost <= float(mx)
        return Verdict(dimension=self.dimension, kind=self.kind, passed=ok, score=cost,
                       threshold=float(mx),
                       explanation=f"cost=${cost:.4f} {'<=' if ok else '>'} ${float(mx):.4f}")


class FlowEfficiencyScorer(Scorer):
    dimension = "flow_efficiency"
    kind = ScoreKind.METRIC
    applies_to = _FLOW

    def score(self, case: EvalCase, run: FlowRunRecord) -> Verdict:
        steps = run.step_count()
        if steps == 0:
            return _na(self, "no executed nodes to measure")
        mx = case.assertions.get("max_steps")
        if mx is None:
            return Verdict(dimension=self.dimension, kind=self.kind, passed=None, score=float(steps),
                           explanation=f"{steps} node executions (no step budget set)")
        ok = steps <= int(mx)
        return Verdict(dimension=self.dimension, kind=self.kind, passed=ok, score=float(steps),
                       threshold=float(mx),
                       explanation=f"{steps} node executions {'<=' if ok else '>'} max {mx}")


_ALL = [
    FlowCompletedScorer(), FlowNodesSucceededScorer(), FlowNodesReachedScorer(),
    FlowForbiddenNodesScorer(), FlowCostScorer(), FlowEfficiencyScorer(),
]
for _s in _ALL:
    register_scorer(_s)
