"""
Enterprise capability gates — the single switch for enterprise-only features.

**Today every gate returns True** (features visible to everyone — the switch is
NOT executed yet). Making a capability Enterprise-only is a **one-line change
here**; the whole platform reads these functions, so the gate is never scattered.

This is the *preparation* seam requested for the Ontology suite: wire it now,
flip it later.
"""

from __future__ import annotations


def is_enterprise_ontology_enabled() -> bool:
    """Master switch for the **Ontology suite** — the "Ontology" left-nav view
    (the Platform Ontology / agent-centric estate map) *and* the Trust Membrane
    that lives inside it.

    **TODAY: returns True** — the Ontology is visible to all tiers (switch off).

    LATER — Enterprise-only is exactly this one edit (nothing else changes)::

        from trusted_data_agent.core.config import APP_STATE
        return (APP_STATE.get("license_info") or {}).get("tier") == "Enterprise"

    When this returns False for a deployment:
      * the **"Ontology" sidebar item is not rendered** (template flag),
      * the ``/v1/platform-ontology/*`` REST endpoints return **404**, and
      * the **Trust Membrane** is inert (its blueprint isn't registered and
        ``trustBadge.js`` isn't emitted) — see ``core/trust/availability.py``,
        which defers to this switch.
    """
    return True


def license_tier() -> str:
    """The tier of this deployment's validated licence; ``Community`` without one."""
    from trusted_data_agent.core.config import APP_STATE
    return (APP_STATE.get("license_info") or {}).get("tier") or "Community"


def is_licensed() -> bool:
    """Whether the licence activates the enterprise package (``core/license_file.LICENSED_TIERS``:
    Enterprise or Partner). The package's own ``register()`` asks here; a capability asks
    ``is_entitled`` for its group (the editor: ``prompt-editing``)."""
    from trusted_data_agent.core.license_file import LICENSED_TIERS
    return license_tier() in LICENSED_TIERS


def is_entitled(group: str) -> bool:
    """Whether the licence entitles a group of the enterprise package (``core/entitlements.GROUPS``;
    a key without the field entitles every group). The one reader for every capability gate."""
    from trusted_data_agent.core import entitlements
    return entitlements.is_entitled(group)


def entitlements() -> frozenset:
    """The groups this deployment may run (``core/entitlements.entitlements``)."""
    from trusted_data_agent.core import entitlements as _e
    return _e.entitlements()
