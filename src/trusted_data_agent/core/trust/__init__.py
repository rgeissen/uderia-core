"""
Trust Membrane (Tier 5.2) — the agent is the unit of trust.

A signed agent must contain only signed ingredients; trust is a visible,
cryptographically-verifiable badge computed *compositionally* over the agent's
ingredients. This package is the Phase-1 slice:

  * ``attestation`` — pure, deterministic, zero-I/O trust core (states, hashing,
    the compositional resolver, the signability rule, the validator registry).
  * ``validators``  — per-type ingredient validators (ontology first, wrapping
    the Tier 4.3 conformance gate).
  * ``store``       — the ``trust_attestations`` table + persistence + Ed25519
    signing (reuses ``eval.online.sign_payload``).

Design: docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md
"""

from .attestation import (  # noqa: F401
    TrustState,
    IngredientRef,
    IngredientVerdict,
    AgentTrust,
    ValidationVerdict,
    content_hash,
    composition_hash,
    resolve_state,
    resolve_agent_trust,
    agent_signable,
    register_validator,
    get_validator,
    has_validator,
)
from . import validators  # noqa: F401  (registers the ontology validator on import)
from .composition import (  # noqa: F401
    resolve_profile_trust,
    sign_agent,
    agent_ingredient_refs,
    build_agent_ingredients,
)
