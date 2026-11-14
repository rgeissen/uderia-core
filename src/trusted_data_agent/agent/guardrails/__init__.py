"""Guardrails — Tier 1.3 P2 policy primitives + the synchronous guardrail surface.

Deterministic, 0-token inbound prompt-injection detection and outbound PII/toxicity
detection-redaction-blocking, wired into the same per-engine seams as the grounding gate.
Observe by default (detect + report, never alter); annotate/enforce are opt-in per profile.

See docs/roadmap/TIER_1.3_GUARDRAILS.md.
"""
from trusted_data_agent.agent.guardrails.types import (
    Action, Category, Direction, Finding, GuardrailConfig, GuardrailVerdict,
)
from trusted_data_agent.agent.guardrails import primitives, policy, adapter, runtime

__all__ = ["Action", "Category", "Direction", "Finding",
           "GuardrailConfig", "GuardrailVerdict",
           "primitives", "policy", "adapter", "runtime"]
