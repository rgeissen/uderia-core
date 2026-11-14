"""
Shared helpers for component-instance export / import (portability).

ONE serialization + cross-reference-remap core, reused by every instance-bearing
component (flow, dashboard, …) and — by design — by the marketplace layer that
sits on top of the same `export_instance` / `import_instance` contract:

    Publish        →  export_instance()                         → listing (+ content_hash)
    Install / Fork →  import_instance(mode="create", mapping=…)  → independent copy
    Subscribe      →  import_instance(mode="create") + link
    Republish sync →  import_instance(mode="update", target_id=…, mapping=…)
                      → re-materialize IN PLACE, preserving the subscriber's
                        instance id so their sessions / references stay valid.

Cross-references travel in the export as TAGS (profiles), never raw per-user IDs,
and are remapped to the importing user's real IDs on materialization. A
deterministic `content_hash` over the portable content (excluding volatile fields)
lets the marketplace detect that a publisher republished new content.

The component-specific "where do my profile references live in the spec" walk
stays in each handler; only the generic remap primitives live here, so there is
exactly one implementation of inventory / preflight / remap / hashing / dedupe.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

#: Bumped only on a breaking change to the envelope shape.
PORTABILITY_SCHEMA_VERSION = "1.0"


def content_hash(envelope: Dict[str, Any]) -> str:
    """Deterministic hash of an envelope's PORTABLE content.

    Excludes volatile fields (``exported_at``, the hash itself) so two exports of
    unchanged content hash identically. Marketplace subscribe compares this against
    the stored listing hash to decide whether a republish changed anything.
    """
    material = {
        "component_id": envelope.get("component_id"),
        "schema_version": envelope.get("schema_version"),
        "spec": envelope.get("spec"),
        "refs": envelope.get("refs"),
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def stamp_content_hash(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Compute and set ``envelope['content_hash']`` in place; return the envelope."""
    envelope["content_hash"] = content_hash(envelope)
    return envelope


def build_profile_inventory(user_uuid: str) -> Dict[str, Any]:
    """Resolve the importing user's profiles into lookup structures used by both
    preflight (report) and commit (remap)."""
    from trusted_data_agent.core.config_manager import get_config_manager

    profiles = get_config_manager().get_profiles(user_uuid) or []
    by_tag = {p.get("tag"): p for p in profiles if p.get("tag")}
    by_id = {p.get("id"): p for p in profiles if p.get("id")}
    default = next((p for p in profiles if p.get("isDefault") or p.get("is_default")), None)
    candidates = [
        {
            "id": p.get("id"),
            "tag": p.get("tag"),
            "profile_type": p.get("profile_type"),
            "color": p.get("color"),
            "name": p.get("profileName") or p.get("name") or p.get("tag"),
        }
        for p in profiles if p.get("id")
    ]
    return {
        "by_tag": by_tag,
        "by_id": by_id,
        "default_id": (default or {}).get("id"),
        "candidates": candidates,
        "profiles": profiles,
    }


def profile_tag_for_id(profile_id: Optional[str], inv: Dict[str, Any]) -> Optional[str]:
    """Current tag for a profile id (for export id→tag), or None if unknown."""
    p = inv["by_id"].get(profile_id)
    return p.get("tag") if p else None


def export_profile_ref(profile_id: Optional[str], inv: Dict[str, Any],
                       ref_profiles: Dict[str, Dict]) -> Optional[str]:
    """Resolve a profile id → tag for export and record it in ``ref_profiles``
    (keyed by tag so the import dialog can render one mapping row per profile).
    Returns the tag to store in the spec, or the raw id (flagged unresolved) when
    the profile no longer exists. Returns None for a falsy id."""
    if not profile_id:
        return None
    prof = inv["by_id"].get(profile_id)
    if prof and prof.get("tag"):
        tag = prof["tag"]
        ref_profiles.setdefault(tag, {
            "tag": tag,
            "profile_type": prof.get("profile_type"),
            "color": prof.get("color"),
            "name": prof.get("profileName") or prof.get("name") or tag,
        })
        return tag
    # Deleted profile — keep the id but flag it so the dialog forces a remap.
    ref_profiles.setdefault(profile_id, {"tag": profile_id, "unresolved": True})
    return profile_id


def preflight_profiles(ref_profiles: List[Dict], inv: Dict[str, Any]) -> List[Dict]:
    """Build the per-profile preflight rows the import dialog renders: each ref is
    auto-matched to the user's same-tag profile, or flagged ``unmatched`` with the
    user's default profile suggested."""
    rows = []
    for ref in ref_profiles or []:
        tag = ref.get("tag")
        match = inv["by_tag"].get(tag)
        rows.append({
            "ref_tag": tag,
            "profile_type": ref.get("profile_type"),
            "color": ref.get("color"),
            "unresolved_source": bool(ref.get("unresolved")),
            "status": "auto" if match else "unmatched",
            "target_id": match.get("id") if match else None,
            "suggested_id": (match.get("id") if match else inv["default_id"]),
        })
    return rows


def resolve_profile_ref(value: Optional[str], mapping: Optional[Dict[str, Any]],
                        inv: Dict[str, Any]) -> Optional[str]:
    """Map an exported profile reference (a tag, or a still-valid id) → a real
    profile id for the importing user. Resolution order:
        explicit dialog mapping → user owns this tag → already a valid id → None.
    Returning None means 'inherit the instance/flow default'."""
    profiles_map = (mapping or {}).get("profiles") or {}
    if value in profiles_map and profiles_map[value]:
        return profiles_map[value]
    if value in inv["by_tag"]:
        return inv["by_tag"][value]["id"]
    if value in inv["by_id"]:
        return value
    return None


def dedupe_name(base: str, existing_names) -> str:
    """Return ``base`` or ``base (N)`` so an imported instance never collides."""
    base = (base or "Imported").strip() or "Imported"
    existing = set(existing_names or [])
    if base not in existing:
        return base
    n = 2
    while f"{base} ({n})" in existing:
        n += 1
    return f"{base} ({n})"
