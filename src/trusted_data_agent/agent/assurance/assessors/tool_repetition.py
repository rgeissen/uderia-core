"""
`tool_repetition` — is the agent calling the same tool with the same arguments over and
over, learning nothing new?

Assessor: **`identical_call_repeats`** — the highest count of a byte-identical
(tool, arguments) signature within one turn.

WHY IT EARNED AN IN-FLIGHT LANE (symmetry map Part D). Of the five candidates this was the
standout, and for a reason none of the others share: today a loop is **graded after the
agent has already run it**. The tokens are spent, the user has waited, and the reading
arrives too late to help anyone. Every other Part D row improves a READING; this one can
save the run.

WHAT COUNTS AS A REPEAT, AND WHAT DELIBERATELY DOES NOT. The signature is the tool name
plus its arguments, serialized deterministically. So:

  * an orchestrator iterating dates calls one tool with DIFFERENT arguments — not a repeat;
  * a retry after an error carries the same signature and IS counted, which is why the
    in-flight guard is threshold-gated rather than tripping on the second call;
  * `TDA_` framework tools are excluded by the guard, not here — they are the agent's own
    reporting and orchestration, and the same exemption Agent Authority makes at the same
    chokepoint.
"""

from __future__ import annotations

import json as _json
from collections import Counter
from typing import Any

from ..capabilities import AssessorCapability as C
from ..contract import AssessmentContext, Assessor, Finding, Severity, abstain


def signature(tool_name: str, arguments: Any) -> str:
    """The ONE definition of "the same call", shared by the measurement and the guard.

    Two definitions would let the guard break a run on a repeat the Scorecard does not
    report — a control acting on evidence the record contradicts.
    """
    return _json.dumps([str(tool_name or ""), arguments or {}],
                       sort_keys=True, default=str)


class IdenticalCallRepeatsAssessor(Assessor):
    key = "identical_call_repeats"
    property = "tool_repetition"
    label = "Identical tool-call repeats"
    description = ("Counts repeats of the same (tool, arguments) signature across the "
        "turn — the same call made again learns nothing new, and a retry after an error "
        "carries the same signature, so the count is what matters, not the fact of a "
        "repeat.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC, C.RETURNS_EVIDENCE})
    requires = frozenset({"tool_calls"})

    def assess(self, ctx: AssessmentContext) -> Finding:
        calls = [c for c in ctx.tool_calls if isinstance(c, dict)]
        if not calls:
            return abstain(self.property, self.key, "no tool calls")

        sigs = Counter(signature(c.get("tool_name"), c.get("arguments")) for c in calls)
        worst = max(sigs.values())

        # The ceiling is read from config and never invented: how many repeats are
        # legitimate depends on the agent's work, and a shipped default would convict
        # orchestration patterns nobody has seen.
        try:
            ceiling = (ctx.config or {}).get("max_tool_repeats")
            ceiling = int(ceiling) if ceiling is not None else None
        except (TypeError, ValueError):
            ceiling = None
        over = ceiling is not None and worst > ceiling

        offenders = tuple(sorted(
            f"{_json.loads(sig)[0]} ×{n}" for sig, n in sigs.items()
            if ceiling is not None and n > ceiling))

        return Finding(
            property=self.property, assessor=self.key,
            triggered=over,
            severity=Severity.CONCERN if over else Severity.NONE,
            summary=(f"the most-repeated identical call ran {worst}×"
                     + (f" — above the ceiling of {ceiling}" if over else "")),
            evidence=offenders,
            metrics={"max_repeats": float(worst), "distinct_calls": float(len(sigs)),
                     "total_calls": float(len(calls))},
        )


ASSESSOR: Any = IdenticalCallRepeatsAssessor()
