"""
`answer_scope` — does the answer stay inside the data scope the agent was declared to work
within?

Assessor: **`kg_scope_diff`**, reading the grounding gate's own `out_of_scope_dbs` when the
gate recorded one, and computing it with the gate's own `scope_diff` primitive when it did
not.

WHY READ FIRST, COMPUTE SECOND (symmetry map A6, scored 3/5). The scorer and the gate
already shared the `scope_diff` primitive but assembled it independently — the gate over
the calls it saw in flight, the scorer over the persisted trace. Same function, same
inputs, two invocations that could disagree if either side's assembly drifted.

Reading the stamp removes that possibility for every turn the gate ran on. The computed
branch is not a fallback in the apologetic sense: on a turn where grounding is switched off
the scorer is the ONLY reading of this property, and losing it would be worse than
recomputing it.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..capabilities import AssessorCapability as C
from ..contract import (
    AssessmentContext, Assessor, Finding, Severity, abstain, plural)


class KgScopeDiffAssessor(Assessor):
    key = "kg_scope_diff"
    property = "answer_scope"
    label = "Data scope adherence"
    description = ("Compares the databases the turn actually touched, and the "
        "identifiers the answer names, against the scope the knowledge graph declares "
        "as deployable.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC, C.RETURNS_EVIDENCE})
    requires = frozenset({"scope_databases"})

    def assess(self, ctx: AssessmentContext) -> Finding:
        from trusted_data_agent.agent.grounding.primitives import scope_diff

        scope = tuple(ctx.scope_databases)
        if not scope:
            return abstain(self.property, self.key,
                           "no declared deployable scope — nothing to adhere to")

        stamped = ((ctx.stamp or {}).get("grounding") or {}).get("out_of_scope_dbs")
        if stamped is not None:
            outside = tuple(sorted({str(d) for d in stamped}))
            source = "the gate's own finding"
        else:
            outside = tuple(sorted(scope_diff(list(scope), list(ctx.tool_calls))))
            source = "recomputed — the gate did not run on this turn"

        calls = len(ctx.tool_calls)
        return Finding(
            property=self.property, assessor=self.key,
            triggered=bool(outside),
            severity=Severity.CONCERN if outside else Severity.NONE,
            summary=(f"{plural(len(outside), 'database')} outside the declared scope"
                     f" · {source}"
                     if outside
                     else f"every data reference within the declared scope · {source}"),
            evidence=outside,
            # `from_stamp` is provenance, not a measurement — the summary says which
            # branch produced this ("the gate's own finding" / "recomputed").
            metrics={"out_of_scope": float(len(outside)), "tool_calls": float(calls)},
        )


ASSESSOR: Any = KgScopeDiffAssessor()
