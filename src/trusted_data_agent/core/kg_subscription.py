"""Knowledge-graph subscription copies — ONE materializer, two sources.

A subscribed knowledge graph is a read-only LOCAL COPY under the subscriber's own profile
(``kg_metadata.is_subscribed=1``, ``source_subscription_id``; ``GraphStore`` refuses writes),
rebuilt from the publisher's export when the source moves. It cannot be a reference: every
KG read is scoped to the reading user's account (``GraphStore(profile_id, user_uuid, kg_id)``,
``kg_profile_assignments … AND user_uuid = ?``), so a subscriber's profile pointing at the
publisher's ``kg_id`` would fail every lookup.

Two sources produce such copies:

* ``SOURCE_KG`` — a KG-marketplace listing (``marketplace_knowledge_graphs``); the export is
  the file the listing stored at publish; the fan-out runs on (re)publish of that listing.
* ``SOURCE_AGENT_PACK`` — an agent-pack listing (``marketplace_agent_packs``); the export is the
  publisher's LIVE graph as ``export_pack`` serializes it; the fan-out runs when the publisher
  updates the pack in place.

``kg_subscriptions.source_kind`` says which, so the two fan-outs never touch each other's rows.
Everything here was lifted verbatim from ``api/kg_marketplace_routes.py`` (P0 of
docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md) — the routes now delegate, so the KG marketplace
behaves byte-identically and the pack path reuses the same three primitives.

The caller owns the connection and the commit; these functions issue the statements.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

SOURCE_KG = "kg"
SOURCE_AGENT_PACK = "agent_pack"
NAME_SUFFIX = "(subscribed)"


def _has_active_kg(db_path: str, profile_id: str, user_uuid: str) -> bool:
    ci = sqlite_util.connect(str(db_path))
    try:
        return ci.execute(
            "SELECT 1 FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? AND is_active = 1 LIMIT 1",
            (profile_id, user_uuid),
        ).fetchone() is not None
    finally:
        ci.close()


def materialize_copy(conn, *, db_path: str, user_uuid: str, target_profile_id: str,
                     source_kg_id: str, source_marketplace_id: str, source_kind: str,
                     export_data: Dict[str, Any], export_hash: Optional[str],
                     fallback_name: str = "") -> Dict[str, Any]:
    """Create the subscriber's local copy of a source graph and record the subscription.

    Order matters and is kept from the route: ``is_subscribed`` is set AFTER ``import_bulk``
    so the store's read-only gate does not block the materialization itself. The copy is
    installed ACTIVE unless the target profile already has an active graph (never displace
    a subscriber's own). Raises on failure after undoing the metadata row it created.
    Returns ``{subscription_id, local_kg_id, kg_name, active, entities_added,
    relationships_added}``.
    """
    from components.builtin.knowledge_graph.graph_store import GraphStore

    cursor = conn.cursor()
    new_kg_id = str(_uuid.uuid4())
    store = GraphStore(target_profile_id, user_uuid, kg_id=new_kg_id)
    has_active = _has_active_kg(db_path, target_profile_id, user_uuid)

    kg_name = export_data.get("kg_name") or fallback_name or "Knowledge graph"
    # Tag the name so subscribers can distinguish from forks of the same listing
    if NAME_SUFFIX not in kg_name:
        kg_name = f"{kg_name} {NAME_SUFFIX}"
    kg_description = export_data.get("kg_description") or ""
    kg_database = export_data.get("kg_database_name") or ""
    entities = export_data.get("entities", []) or []
    relationships = export_data.get("relationships", []) or []

    subscription_id = str(_uuid.uuid4())
    now_ts = datetime.now(timezone.utc).isoformat()

    store.set_kg_metadata(
        name=kg_name, database_name=kg_database,
        description=kg_description or None,
        is_active=not has_active,
    )
    try:
        for ent in entities:
            if ent.get("source") not in ("marketplace",):
                ent["source"] = "marketplace"
        import_result = store.import_bulk(entities, relationships)
    except Exception:
        try:
            rb = sqlite_util.connect(str(db_path))
            rb.execute("DELETE FROM kg_metadata WHERE kg_id = ? AND user_uuid = ?", (new_kg_id, user_uuid))
            rb.commit()
            rb.close()
        except Exception:
            pass
        # A rolled-back copy was never established, so normally there is nothing to
        # purge — the rule "every kg_metadata delete purges that id's derived state"
        # is kept absolute rather than carrying an exemption list.
        try:
            from trusted_data_agent.core import kg_derived_state as _kgds
            _kgds.purge_for_kg(new_kg_id)
        except Exception:  # noqa: BLE001
            pass
        raise

    # Trust Membrane portability: a signed source reinstates onto the copy when its content
    # is byte-identical to what was signed (the honesty gate).
    try:
        from trusted_data_agent.core.trust import portability as _trust_portability
        _trust_portability.reinstate_kind("ontology", new_kg_id, export_data.get("trust"))
    except Exception:
        pass
    # PROP-008 A5: the publisher's extension signatures travel too — reinstated AFTER the
    # base (their content binds the base's attested hash).
    try:
        from trusted_data_agent.core.trust import extensions as _trust_ext
        _trust_ext.reinstate_claims(new_kg_id, export_data.get("trust_extensions"))
    except Exception:
        pass

    cursor.execute(
        "UPDATE kg_metadata SET is_subscribed = 1, source_subscription_id = ? "
        "WHERE kg_id = ? AND user_uuid = ?",
        (subscription_id, new_kg_id, user_uuid),
    )
    cursor.execute(
        """
        INSERT INTO kg_subscriptions (
            id, user_uuid, source_kg_id, source_marketplace_id,
            target_profile_id, local_kg_id, enabled,
            subscribed_at, last_synced_at, last_synced_export_hash, last_sync_error,
            source_kind
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, ?)
        """,
        (
            subscription_id, user_uuid,
            source_kg_id or "",   # kg_id may be NULL on legacy listings
            source_marketplace_id,
            target_profile_id, new_kg_id,
            now_ts, now_ts, export_hash, source_kind,
        ),
    )
    try:
        cursor.execute(
            """
            INSERT OR IGNORE INTO kg_profile_assignments
              (kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (new_kg_id, target_profile_id, target_profile_id, user_uuid, 0 if has_active else 1),
        )
    except sqlite3.OperationalError:
        pass
    return {
        "subscription_id": subscription_id,
        "local_kg_id": new_kg_id,
        "kg_name": kg_name,
        "active": not has_active,
        "entities_added": (import_result or {}).get("entities_added", 0),
        "relationships_added": (import_result or {}).get("relationships_added", 0),
    }


def rebuild_copy(conn, sub, export_data: Dict[str, Any], export_hash: Optional[str]) -> Dict[str, int]:
    """Rebuild a subscriber's local copy from the source's latest export.

    clear_graph + import_bulk through the abstraction layer (whatever backend the profile
    uses), the attestation re-synced (a publisher (re)sign is picked up; a source no longer
    signed clears the copy's), metadata refreshed with the ``(subscribed)`` suffix kept, the
    sync stamps bumped. Activation on ``kg_profile_assignments`` is deliberately untouched —
    the subscriber's per-profile activation survives a sync. Raises on any failure.
    """
    cur = conn.cursor()
    local_kg_id = sub["local_kg_id"]
    user_uuid = sub["user_uuid"]
    profile_id = sub["target_profile_id"]
    now_ts = datetime.now(timezone.utc).isoformat()
    entities = export_data.get("entities", []) or []
    relationships = export_data.get("relationships", []) or []

    try:
        _meta = conn.execute("SELECT kg_store_config_id FROM kg_metadata WHERE kg_id = ?", (local_kg_id,)).fetchone()
        cfg_id = _meta["kg_store_config_id"] if _meta and "kg_store_config_id" in _meta.keys() else None
    except Exception:
        cfg_id = None

    from components.builtin.knowledge_graph.graph_store import GraphStore
    gs = GraphStore(profile_id=profile_id, user_uuid=user_uuid,
                    kg_id=local_kg_id, kg_store_config_id=cfg_id,
                    allow_subscribed_write=True)  # the sync IS the one legitimate write
    for ent in entities:
        if ent.get("source") != "marketplace":
            ent["source"] = "marketplace"
    for rel in relationships:
        if rel.get("source") != "marketplace":
            rel["source"] = "marketplace"
    # The sync replaces the PUBLISHER's layer and keeps the subscriber's own authored
    # layer (PROP-008 A1 / §3a) — one operation, not clear + import.
    import_result = gs.replace_model_layer(entities, relationships)
    entities_added = import_result.get("entities_added", len(entities))
    relationships_added = import_result.get("relationships_added", 0)

    try:
        from trusted_data_agent.core.trust import portability as _trust_portability
        from trusted_data_agent.core.trust import store as _trust_store
        if not _trust_portability.reinstate_kind("ontology", local_kg_id, export_data.get("trust")):
            _trust_store.revoke("ontology", local_kg_id)
    except Exception:
        pass
    try:   # A5: the extension claims follow the base, by content (a stale one is revoked)
        from trusted_data_agent.core.trust import extensions as _trust_ext
        _trust_ext.reinstate_claims(local_kg_id, export_data.get("trust_extensions"))
    except Exception:
        pass

    publisher_name = export_data.get("kg_name") or ""
    metadata_name = f"{publisher_name} {NAME_SUFFIX}" if publisher_name else None
    if metadata_name:
        cur.execute(
            "UPDATE kg_metadata SET name = ?, description = ?, database_name = ?, updated_at = ? "
            "WHERE kg_id = ? AND user_uuid = ?",
            (metadata_name, export_data.get("kg_description") or None,
             export_data.get("kg_database_name") or None, now_ts, local_kg_id, user_uuid),
        )
    cur.execute(
        "UPDATE kg_subscriptions "
        "SET last_synced_at = ?, last_synced_export_hash = ?, last_sync_error = NULL "
        "WHERE id = ?",
        (now_ts, export_hash, sub["id"]),
    )
    return {"entities_added": entities_added, "relationships_added": relationships_added}


def delete_copy(conn, sub) -> None:
    """Hard-delete a subscriber's local copy: graph data through the abstraction layer (so a
    non-SQLite backend is cleaned too), then the SQLite-only metadata rows. The caller owns
    the connection and the commit."""
    cur = conn.cursor()
    local_kg_id = sub["local_kg_id"]
    user_uuid = sub["user_uuid"]
    sub_id = sub["id"]
    profile_id = sub["target_profile_id"]
    try:
        _meta = conn.execute("SELECT kg_store_config_id FROM kg_metadata WHERE kg_id = ?", (local_kg_id,)).fetchone()
        cfg_id = _meta["kg_store_config_id"] if _meta and "kg_store_config_id" in _meta.keys() else None
    except Exception:
        cfg_id = None
    try:
        from components.builtin.knowledge_graph.graph_store import GraphStore
        gs = GraphStore(profile_id=profile_id, user_uuid=user_uuid, kg_id=local_kg_id, kg_store_config_id=cfg_id)
        gs.clear_graph()
    except Exception as e:
        logger.warning(f"[KG Subscription] Backend clear_graph failed for {local_kg_id}: {e}; falling back to direct SQL")
        cur.execute("DELETE FROM kg_relationships WHERE kg_id = ?", (local_kg_id,))
        cur.execute("DELETE FROM kg_entities      WHERE kg_id = ?", (local_kg_id,))
    cur.execute("DELETE FROM kg_profile_assignments WHERE kg_id = ? AND user_uuid = ?", (local_kg_id, user_uuid))
    cur.execute("DELETE FROM kg_metadata WHERE kg_id = ? AND user_uuid = ? AND is_subscribed = 1", (local_kg_id, user_uuid))
    cur.execute("DELETE FROM kg_subscriptions WHERE id = ?", (sub_id,))
    # Derived state (version history, usage telemetry) dies with its KG — explicit
    # per site because KG deletion has no single chokepoint, ONE registry for what is
    # purged (core.kg_derived_state). Fail-soft — never blocks the unsubscribe.
    try:
        from trusted_data_agent.core import kg_derived_state as _kgds
        _kgds.purge_for_kg(local_kg_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[KG Subscription] derived state for {local_kg_id}: {e}")


def record_sync_error(conn, subscription_id: str, error_message: str) -> None:
    """Stamp last_sync_error on a subscription row (best-effort, swallows errors)."""
    try:
        conn.execute("UPDATE kg_subscriptions SET last_sync_error = ? WHERE id = ?",
                     (str(error_message)[:500], subscription_id))
        conn.commit()
    except Exception:
        pass
