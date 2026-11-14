"""
`data_governance` — did the turn's data operations go through the governed semantic layer,
or was SQL free-formed?

Assessor: **`obda_lanes`**, wrapping `agent/obda.py:scan_obda()`.

The second pair the symmetry map already scores **5/5**, and notably the NEWEST — built
symmetric because both lanes were designed together rather than retrofitted. It is the
existence proof behind the map's closing argument: symmetry costs nothing at design time
and is expensive to add later.
"""

from __future__ import annotations
from typing import Any

from ..capabilities import AssessorCapability as C
from ..contract import (
    AssessmentContext, Assessor, Finding, Severity, abstain, plural)


class ObdaLaneAssessor(Assessor):
    key = "obda_lanes"
    property = "data_governance"
    label = "Governed vs free-formed data operations"
    description = ("Classifies each of the turn's data operations as governed (through "
        "the semantic layer) or free-formed (raw SQL), and reports the ratio.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC, C.RETURNS_EVIDENCE})
    requires = frozenset({"tool_calls"})

    def assess(self, ctx: AssessmentContext) -> Finding:
        from trusted_data_agent.agent import obda as _obda

        s = _obda.scan_obda(list(ctx.tool_calls))
        if not s.get("has_data_ops"):
            return abstain(self.property, self.key, "no data operations this turn")
        governed = int(s.get("governed_count") or 0)
        free = int(s.get("freeform_count") or 0)
        total = int(s.get("total_data_ops") or 0)
        # The ratio is READ, not recomputed. Recomputing it would be a second
        # implementation of the one number both lanes report — the exact defect the map
        # scores 3/5 elsewhere (`numeric_fidelity` re-deriving a value the gate stamped).
        ratio = float(s.get("governed_ratio") or 0.0)
        return Finding(
            property=self.property, assessor=self.key,
            # Free-forming is not a failure — it is a lane. The finding REPORTS the mix;
            # whether a low ratio matters is a policy question, and answering it here
            # would be the assess/decide fusion this contract exists to undo.
            triggered=free > 0,
            severity=Severity.INFO if free else Severity.NONE,
            summary=(f"{governed} governed · {free} free-formed of "
                     f"{plural(total, 'data operation')}"),
            evidence=tuple(str(o) for o in (s.get("governed_ops") or ())),
            metrics={"governed_ratio": ratio, "operations": float(total)},
            payload=s,
        )


ASSESSOR: Any = ObdaLaneAssessor()
