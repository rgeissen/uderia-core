"""
PROP-003 · the generic property scorer — a third party writes NO eval code.

Nine native scorers already share one idiom: resolve the property through the assurance
registry, run the bound assessor over the persisted turn, band the metric it returns. That
repetition is the evidence the idiom is generic, so for a pack property it becomes a
platform guarantee instead of a contributor obligation: bind a third-party property in the
post-hoc lane and its dimension appears on the Scorecard, in the roster's dimension bars,
in the runs ledger and in the Guide's `graded by` row, with no pack-supplied Python.

The consequence that matters most is SYMMETRY BY CONSTRUCTION. The in-flight lane and this
scorer resolve the SAME binding, so a pack cannot ship a gate input and a score that
disagree — the asymmetry the whole substrate exists to remove.

`gating = False` is a deliberate v1 policy: a third-party dimension BANDS and is FLAGGED,
but never fails a turn. Failing a turn is the platform's own judgement to make, and the
day that changes it is a governance switch, not a redesign.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from trusted_data_agent.eval.scorer import Scorer, banded_metric, register_scorer
from trusted_data_agent.eval.types import ScoreKind, Verdict

logger = logging.getLogger("quart.app")

#: Registered instances, keyed by dimension, so a rebind does not register a duplicate.
_INSTALLED: Dict[str, "PropertyScorer"] = {}


class PropertyScorer(Scorer):
    """Grades one property through whatever module is bound to it post-hoc."""

    kind = ScoreKind.DETERMINISTIC
    # Empty = every profile class. A pack property is not tied to an IFOC class: the
    # binding decides where it applies, and a class filter here would be a second,
    # invisible gate on top of it.
    applies_to = frozenset()
    gating = False

    def __init__(self, property_key: str, *, label: str = "",
                 metric: str = "value", gate: Optional[float] = None,
                 healthy: Optional[float] = None, higher_is_better: bool = True):
        self.dimension = property_key
        self.property = property_key          # the Guide-routing declaration
        self.label = label or property_key
        self._metric = metric
        self._gate = gate
        self._healthy = healthy
        self._higher = higher_is_better

    def score(self, case, run) -> Verdict:
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg, run_context as _rc
        from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane

        try:
            _A.bootstrap()
            assessor = _reg.resolve(self.property, _Lane.POST_HOC)
        except Exception as exc:  # noqa: BLE001
            return self._verdict(None, explanation=f"assurance substrate unavailable: {exc}")
        if assessor is None:
            # NOT ASSESSED, never assessed-clean — the registry's own contract, carried
            # through to the surface a reader sees.
            return self._verdict(
                None, explanation=f"no module is bound to {self.property}")

        f = assessor.run(_rc.from_run(run))
        if f.abstained:
            return self._verdict(None, explanation=f.abstained)

        value = f.metrics.get(self._metric)
        if value is None and f.metrics:
            # A pack may name its metric differently from its tunable; one metric is
            # unambiguous, so use it rather than reporting nothing.
            value = next(iter(f.metrics.values())) if len(f.metrics) == 1 else None
        if value is None or self._gate is None:
            # No configured threshold — so there is no BAND to apply (inventing one would
            # present a number this deployment never chose as its line). But the assessor
            # has already made its own judgement, and `triggered` IS that judgement: a
            # pattern rule that matched found something, whether or not anyone configured
            # a band for it. Reporting `passed=None` here discarded a real finding, which
            # is the failure this scorer exists to avoid. The number still travels as the
            # score when there is one.
            return self._verdict(
                not f.triggered, explanation=f.summary or "assessed",
                evidence=list(f.evidence or ()),
                score=float(value) if value is not None else None)
        return banded_metric(
            self, value=float(value), gate=self._gate, healthy=self._healthy,
            higher_is_better=self._higher,
            explanation=f.summary or f"{self._metric} = {value}",
            evidence=list(f.evidence or ()))

    def _verdict(self, passed, *, explanation: str = "", evidence=None,
                 score=None) -> Verdict:
        return Verdict(dimension=self.dimension, kind=self.kind, passed=passed,
                       score=score, explanation=explanation,
                       evidence=list(evidence or ()))


class PropertyRubricScorer(Scorer):
    """A pack's RUBRIC, adjudicated by the platform's judge panel (PROP-003 Phase 3).

    Subclasses the platform's own `JudgeScorer` at runtime rather than reimplementing a
    model call, which is the whole point: the panel, the per-model timeout, the consensus
    rule, the vote attribution AND — the one that matters most — the canonical cost sink
    all come with it. A rubric that spent the operator's tokens through a second accounting
    path would break the platform's own "one canonical sink for every judge LLM call"
    imperative on its first day.

    A pack supplies the QUESTION. It never supplies the model, the panel, the sampling
    rate or the billing.
    """

    # Assigned in `_make_rubric_scorer` — declared here so the shape is readable.
    dimension = ""
    rubric = ""

    def score(self, case, run):  # pragma: no cover — replaced by the real base
        raise NotImplementedError


def _make_rubric_scorer(property_key: str, label: str, assessor):
    """Build a JudgeScorer subclass around a pack's rubric template.

    Constructed dynamically because `JudgeScorer` must be imported lazily: `eval` may not
    be importable in every context that loads the module plane, and the plane must not
    take an import dependency it does not need.
    """
    from trusted_data_agent.eval.scorers.judges import JudgeScorer, _clip

    class _PackRubric(JudgeScorer):
        dimension = property_key
        gating = False          # a pack's rubric informs; it never fails a turn
        applies_to = frozenset()
        rubric = ""             # set below, from the manifest
        property = property_key
        gate_family = ""        # no gate escalates to a pack rubric

        def _has_inputs(self, case, run):
            return bool((run.final_answer_text or "").strip())

        def build_messages(self, case, run):
            # The pack's template, rendered by the pack's own assessor — so the
            # placeholder rules (a closed set, capped fields) are enforced in ONE place
            # rather than re-implemented here.
            from trusted_data_agent.agent.assurance import run_context as _rc
            user = assessor.render(_rc.from_run(run))
            return self._system(), _clip(user, 12000)

    _PackRubric.rubric = (
        f"{label}. Answer PASS or FAIL with a one-sentence reason.")
    _PackRubric.__name__ = f"PackRubric_{property_key.replace('.', '_')}"
    return _PackRubric()


def sync_plugin_scorers(tunables: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Register a scorer for every BOUND third-party property. Returns the dimensions.

    Called after the module plane loads and after a rebind. Idempotent, and deliberately
    never DEREGISTERS: a property that was unbound keeps its scorer, which then abstains
    with "no module is bound" — the honest reading. Removing the scorer would make the
    dimension vanish from the Scorecard, which reads as "this was never measured" rather
    than "this is no longer being measured".
    """
    from trusted_data_agent.agent import assurance as _A
    from trusted_data_agent.agent.assurance import registry as _reg
    from trusted_data_agent.agent.assurance.capabilities import Lane as _Lane

    out: List[str] = []
    try:
        _A.bootstrap()
    except Exception as exc:  # noqa: BLE001
        logger.warning("plugin scorers: substrate unavailable (%s)", exc)
        return out

    by_prop = {}
    for t in (tunables or []):
        p = t.get("property")
        if p:
            by_prop[p] = t

    for row in _reg.bindings():
        prop = str(row.get("property") or "")
        if "." not in prop or row.get("lane") != _Lane.POST_HOC.name:
            continue
        if not row.get("bound"):
            continue
        if prop in _INSTALLED:
            out.append(prop)
            continue
        t = by_prop.get(prop, {})
        label = str(row.get("label") or prop)

        # A RUBRIC is adjudicated by the judge panel; everything else is a number this
        # process can compute. The bound assessor decides which, so a pack cannot get a
        # judge by asking for one — it gets one by being a rubric.
        assessor = _reg.resolve(prop, _Lane.POST_HOC)
        is_rubric = hasattr(assessor, "render") and hasattr(assessor, "template")
        try:
            s = (_make_rubric_scorer(prop, label, assessor) if is_rubric
                 else PropertyScorer(
                     prop, label=label, metric=str(t.get("metric") or "value"),
                     gate=t.get("gate"), healthy=t.get("healthy"),
                     higher_is_better=bool(t.get("higher_is_better", True))))
        except Exception as exc:  # noqa: BLE001 — eval layer unavailable
            logger.warning("plugin scorers: %s could not be registered (%s)", prop, exc)
            continue
        register_scorer(s)
        _INSTALLED[prop] = s
        out.append(prop)
    return out


def reset_for_tests() -> None:
    _INSTALLED.clear()
