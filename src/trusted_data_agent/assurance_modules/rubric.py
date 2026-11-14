"""
T0 rubrics — a declarative LLM judge, run by the platform's own judge machinery.

A rubric is a prompt template plus a fixed verdict shape. The pack supplies the QUESTION;
the platform supplies the model, the panel, the timeout and — critically — the cost sink.
A judge call is the one assurance form that spends the operator's money, so it rides the
existing governance (sample rate, master switch) and the ONE canonical accounting path
rather than inventing a second one.

LANES. A rubric declares `NEEDS_MODEL`, so `lanes_for` excludes the unconditional
in-flight lane automatically — nothing here re-implements that rule. What remains is the
triggered lane (a cheap signal fired first) and post-hoc. That is derived, not asserted.

PLACEHOLDERS are a closed set. A template that could interpolate arbitrary context fields
would be a way to exfiltrate whatever the turn happened to carry into a third-party
prompt; the three that are allowed are the three a rubric can reasonably need.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Tuple

from trusted_data_agent.agent.assurance.capabilities import AssessorCapability as C
from trusted_data_agent.agent.assurance.contract import (
    AssessmentContext, Assessor, Finding, Severity, abstain)

logger = logging.getLogger("quart.app")

ALLOWED_PLACEHOLDERS = ("answer", "question", "chunks")
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
MAX_TEMPLATE_CHARS = 4000
#: How much of each field reaches the prompt. A rubric is a judgement about the answer,
#: not a channel for the whole turn.
FIELD_CAPS = {"answer": 6000, "question": 1000, "chunks": 6000}


class RubricAssessor(Assessor):
    """Ask a model one closed question about the turn."""

    capabilities = frozenset({C.NEEDS_MODEL, C.REPLAYABLE, C.RETURNS_EVIDENCE})

    def __init__(self, *, key: str, property: str, label: str, template: str,
                 requires: Tuple[str, ...] = ("answer",),
                 subject_is_model_output: bool = True):
        self.key = key
        self.property = property
        self.label = label or key
        self.requires = frozenset(requires)
        self.subject_is_model_output = subject_is_model_output
        self.template = template

    def assess(self, ctx: AssessmentContext) -> Finding:
        """Synchronous contract, model call: this returns the PROMPT, never a verdict.

        An `Assessor.assess` is synchronous and must not block; a judge call is seconds of
        network. So a rubric produces the request, and the judge layer — which already owns
        the panel, the timeout and the cost sink — executes it. The finding this returns is
        an explicit abstention carrying the prompt, so a rubric that is never picked up by
        the judge layer reads as NOT ASSESSED rather than as a pass.
        """
        f = abstain(self.property, self.key,
                    "a rubric is adjudicated by the judge layer, not in this lane")
        return Finding(
            property=f.property, assessor=f.assessor, triggered=False,
            severity=Severity.NONE, summary=f.summary, abstained=f.abstained,
            payload={"rubric_prompt": self.render(ctx)})

    def render(self, ctx: AssessmentContext) -> str:
        vals = {
            "answer": (ctx.answer or "")[:FIELD_CAPS["answer"]],
            "question": (ctx.question or "")[:FIELD_CAPS["question"]],
            "chunks": "\n---\n".join(ctx.chunks or ())[:FIELD_CAPS["chunks"]],
        }
        out = self.template
        for name, v in vals.items():
            out = out.replace("{" + name + "}", v)
        return out


def build_rubric(spec: Dict[str, Any], *, key: str, property: str, label: str,
                 requires: Tuple[str, ...] = ("answer",),
                 subject_is_model_output: bool = True) -> Assessor:
    if not isinstance(spec, dict):
        raise ValueError(f"{key}: rubric must be an object")
    template = spec.get("prompt_template")
    if not isinstance(template, str) or not template.strip():
        raise ValueError(f"{key}: rubric declares no prompt_template")
    if len(template) > MAX_TEMPLATE_CHARS:
        raise ValueError(
            f"{key}: prompt_template is {len(template)} chars; the cap is "
            f"{MAX_TEMPLATE_CHARS}")
    unknown = sorted({m for m in _PLACEHOLDER.findall(template)
                      if m not in ALLOWED_PLACEHOLDERS})
    if unknown:
        raise ValueError(
            f"{key}: prompt_template uses {', '.join(unknown)} — a rubric may only "
            f"interpolate {', '.join(ALLOWED_PLACEHOLDERS)}")
    return RubricAssessor(key=key, property=property, label=label, template=template,
                          requires=requires,
                          subject_is_model_output=subject_is_model_output)
