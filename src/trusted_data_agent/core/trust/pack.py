"""
Agent-pack composition resolver for the Trust Membrane.

A pack is "an agent whose ingredients are its members" — profiles → `agent`, collections →
`knowledge`/`planner` (by repository_type), KGs → `ontology`; skills are covered transitively
under their profile members. This is the single place that maps a pack's resources to
``IngredientRef``s and computes its composition hash, shared by:
  * the sign-off endpoint + badge state (api/trust_routes),
  * marketplace portability (core/agent_pack_manager on import).

Everything is defensive: any failure returns ``None`` rather than raising.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from . import store as _store
from . import content as _content
from . import composition as _comp
from .attestation import IngredientRef, composition_hash


def collection_kind(collection_id: str) -> str:
    """A collection member's trust kind: 'knowledge' or 'planner' (default)."""
    try:
        from trusted_data_agent.core.collection_db import get_collection_db
        coll = get_collection_db().get_collection_by_id(int(collection_id))
        return "knowledge" if (coll or {}).get("repository_type") == "knowledge" else "planner"
    except Exception:
        return "planner"


def agent_live_hash(profile_id: str, user_uuid: Optional[str]) -> Optional[str]:
    """An agent member's live hash — routed through the ONE canonical definition so a pack
    member resolves identically to the same agent seen through the badge or a re-sign.

    TC-1: hashes in TAG space (like the badge and ``sign_agent``); without the id→tag resolver
    a coordinator member's slaveProfiles hash in raw-id space, so the publisher's id-space hash
    never matches the subscriber's remapped-id-space hash and the member could never reinstate.
    """
    try:
        profile = _comp._load_profile(profile_id, user_uuid)
        refs = _comp.agent_ingredient_refs(profile_id, user_uuid, profile=profile)
        id_to_tag = _comp.profile_id_to_tag(user_uuid) if user_uuid else None
        return _comp.agent_version_hash(refs, profile, id_to_tag=id_to_tag)
    except Exception:
        return None


def owner(installation_id: str) -> Optional[str]:
    """The owner_user_id of an installed pack (to resolve user-scoped members)."""
    try:
        from trusted_data_agent.core import sqlite_util
        from trusted_data_agent.core.agent_pack_db import _DEFAULT_DB_PATH
        conn = sqlite_util.connect(_DEFAULT_DB_PATH)
        try:
            row = conn.execute(
                "SELECT owner_user_id FROM agent_pack_installations WHERE id=?",
                (int(installation_id),)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def member_refs(installation_id: str) -> Tuple[Optional[List[IngredientRef]], Optional[str]]:
    """The pack's members as IngredientRefs, plus the owner. (None, owner) if no members."""
    try:
        from trusted_data_agent.core.agent_pack_db import AgentPackDB
        own = owner(installation_id)
        resources = AgentPackDB().get_resources_for_pack(int(installation_id))
        if not resources:
            return None, own
        refs: List[IngredientRef] = []
        for r in resources:
            rtype, rid = r.get("resource_type"), str(r.get("resource_id"))
            if rtype == "profile":
                kind, live = "agent", agent_live_hash(rid, own)
            elif rtype == "knowledge_graph":
                kind, live = "ontology", _content.live_hash("ontology", rid)
            elif rtype == "collection":
                kind = collection_kind(rid)
                live = _content.live_hash(kind, rid)
            else:
                continue
            # an agent member is the pack owner's agent (owner-scoped, PROP-009 §4.1)
            refs.append(IngredientRef(kind, rid, live or "",
                                      _store.attested_hash(kind, rid, owner_uuid=own)))
        return refs, own
    except Exception:
        return None, None


def composition_hash_for(installation_id: str) -> Optional[str]:
    """The pack's composition hash (over its members' content), or None."""
    refs, _ = member_refs(installation_id)
    return composition_hash(refs) if refs else None
