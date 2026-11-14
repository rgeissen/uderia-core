"""Resolve the effective memory config for a profile from the **component system** — agent
memory is the ``memory`` component (KG precedent), so governance reuses the standard three-level
component chain (global ``component_settings`` → per-user override → per-profile ``componentConfig``)
instead of a bespoke master switch. Pure except for the component-availability lookup.

Mirrors ``agent/recommender.py:_component_enabled_for_profile`` (authoritative enablement —
deliberately NOT ``get_active_components``, which ignores global/user governance).

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.5.
"""
from __future__ import annotations

from typing import Optional

from components.builtin.memory.types import SCOPES, MemoryConfig

COMPONENT_ID = "memory"


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def memory_component_enabled(profile: Optional[dict], profile_type: str,
                             user_uuid: Optional[str], manager=None) -> bool:
    """Authoritative "is the memory component enabled for this profile" check.

    Thin wrapper over ``components.enablement`` — THE one rule (global admin gate +
    per-user override -> explicit componentConfig -> per-user default -> manifest
    ``enabled_for``). The chain used to be re-implemented here; the copy is gone.
    """
    try:
        from trusted_data_agent.components import enablement
        if manager is None:
            from trusted_data_agent.components.manager import get_component_manager
            manager = get_component_manager()
        return enablement.is_enabled(
            manager.get_component(COMPONENT_ID), profile, user_uuid,
            profile_type=profile_type, require_action=True,
        )
    except Exception:
        return False


def resolve_config(profile: Optional[dict], profile_type: str,
                   user_uuid: Optional[str], manager=None) -> MemoryConfig:
    """Component disabled ⇒ ``off`` (dormant; baseline preserved). Enabled ⇒ ``recall`` by
    default (read-only, 0 background tokens); ``recall_write`` only when the user opts into
    background learning (``componentConfig.memory.learn``)."""
    if not memory_component_enabled(profile, profile_type, user_uuid, manager):
        return MemoryConfig(mode="off")
    block = ((profile or {}).get("componentConfig") or {}).get(COMPONENT_ID, {}) or {}
    learn = bool(block.get("learn", False))
    mode = "recall_write" if learn else "recall"
    scope = block.get("scope", "profile")
    if scope not in SCOPES:
        scope = "profile"
    max_items = _as_int(block.get("maxItems", block.get("max_items")), 8)
    max_items = max(1, min(50, max_items))
    return MemoryConfig(mode=mode, scope=scope, max_items=max_items)
