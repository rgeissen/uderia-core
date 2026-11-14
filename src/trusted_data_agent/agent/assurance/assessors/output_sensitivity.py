"""
`output_sensitivity` — does the delivered text carry personal data, abuse,
exfiltration-shaped links or leaked internals?

Assessor: **`guardrail_findings`** — reading what the outbound guardrail recorded, and
scanning with the guard's own assessor when there is nothing to read.

WHY BOTH BRANCHES. `evaluate_outbound` used to be FUSED: one function that assessed and
decided together, returning a `GuardrailVerdict` with the action already applied. A
post-hoc lane cannot bind to that — it would have to unpick the decision to recover the
facts — which is why the registry read `gap` for this property. Splitting it into
`scan_outbound` (assess) + `decide_outbound` (policy) is what makes ONE assessment
serviceable by both lanes, and the split is proven byte-identical across the full
mode × category matrix.

WHY THIS IS A FOLD, NOT A REWRITE (symmetry map A11, scored 4/5). The `safety` measurement
counted trace steps whose name contained "guardrail" — a COUNT, where the guardrail's own
verdict already carries `categories` and per-finding `label`s on the turn. Counting is
strictly less informative than reading, so the consumer gains from the fold: a number
becomes a named finding.

PRIVACY IS A PROPERTY OF THE ENVELOPE, NOT OF ONE IMPLEMENTATION. The guardrail's
persisted verdict is documented as carrying "labels/categories, never raw PII values", and
this assessor propagates only the category and label. The rule is enforced here rather than
trusted, because a Finding travels to surfaces that will render it, and re-litigating the
invariant at every one of them is how a value eventually leaks.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..capabilities import AssessorCapability as C
from ..contract import (
    AssessmentContext, Assessor, Finding, Severity, abstain, plural)


class GuardrailFindingsAssessor(Assessor):
    key = "guardrail_findings"
    property = "output_sensitivity"
    label = "Outbound guardrail findings"
    description = ("Reads what the outbound guardrail recorded for this turn, and scans "
        "with the guard's own detectors when there is nothing recorded — so the "
        "measurement reads the gate's own assessment rather than re-deriving one.")
    capabilities = frozenset({C.PURE, C.REPLAYABLE, C.DETERMINISTIC,
                              C.RETURNS_METRIC, C.RETURNS_EVIDENCE})
    #: `answer` only — the stamp is preferred but optional, so this can serve BOTH lanes.
    #: In flight there is no recorded verdict yet; post-hoc there usually is, and reading
    #: it is what stops the two lanes producing two assessments of one answer.
    #: DELIBERATELY False. This asks what reached the READER, so a platform-authored
    #: answer is the correct subject rather than a reason to abstain — and the withholding
    #: notice quotes the spans it is reporting, which can themselves be sensitive.
    #: Abstaining here would be a blind spot at exactly the moment a gate acted.
    subject_is_model_output = False
    requires = frozenset({"answer"})

    def assess(self, ctx: AssessmentContext) -> Finding:
        g = (ctx.stamp or {}).get("guardrail")
        if not isinstance(g, Mapping):
            return self._scan(ctx)

        findings = [f for f in (g.get("findings") or []) if isinstance(f, Mapping)]
        cats = sorted({str(f.get("category")) for f in findings if f.get("category")}
                      or {str(c) for c in (g.get("categories") or [])})
        action = str(g.get("action") or "")
        acted = action.lower() not in ("", "pass", "allow", "none")

        # Category + label only. The excerpt field is deliberately NOT propagated: a label
        # names WHAT was found, an excerpt can carry the value itself, and this envelope is
        # rendered by surfaces that have no way to know the difference.
        evidence = tuple(
            f"{f.get('category')}: {f.get('label')}" if f.get("label")
            else str(f.get("category")) for f in findings)

        return Finding(
            property=self.property, assessor=self.key,
            triggered=bool(findings) or acted,
            severity=(Severity.CRITICAL if acted
                      else Severity.CONCERN if findings else Severity.NONE),
            summary=(f"{plural(len(findings), 'finding')} — {', '.join(cats)}"
                     + (f" · the guard {action}ed the answer" if acted else "")
                     if findings or acted else "no sensitive content found"),
            evidence=evidence,
            metrics={"findings": float(len(findings)),
                     "acted": 1.0 if acted else 0.0},
            payload={k: g.get(k) for k in ("action", "mode", "direction", "reason")},
        )


    def _scan(self, ctx: AssessmentContext) -> Finding:
        """No recorded verdict — assess with the guard's OWN assessor.

        The sources are passed as `source_texts` so the X1 whitelist applies: a URL that
        appears in the turn's own sources is not novel and must not trigger. Omitting them
        would make this branch stricter than the gate on the same answer — a second
        assessment disagreeing with the first, which is the defect the split removes.
        """
        from trusted_data_agent.agent.guardrails.policy import scan_outbound

        f = scan_outbound(ctx.answer or "", source_texts=tuple(ctx.chunks))
        if not f.triggered:
            return Finding(property=self.property, assessor=self.key, triggered=False,
                           severity=Severity.NONE,
                           summary="no sensitive content found · scanned, not recorded",
                           metrics={"findings": 0.0, "acted": 0.0})
        # Category + label only. `OutboundFindings.pii` carries the RAW MATCHED VALUES for
        # redaction; they must not cross this boundary.
        cats = sorted({fi.category.value for fi in f.findings})
        return Finding(
            property=self.property, assessor=self.key, triggered=True,
            severity=Severity.CONCERN,
            summary=f"{plural(len(f.findings), 'finding')} — {', '.join(cats)} · scanned, "
                    f"not recorded",
            evidence=tuple(f"{fi.category.value}: {fi.label}" for fi in f.findings),
            metrics={"findings": float(len(f.findings)), "acted": 0.0})


ASSESSOR: Any = GuardrailFindingsAssessor()
