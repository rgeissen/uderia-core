"""
REST API routes for knowledge graph marketplace.

Knowledge graphs represent entity-relationship models (database schemas, business concepts,
domain ontologies) that can be published to and shared via the marketplace.

Marketplace:
- POST /v1/knowledge-graph/<profile_id>/publish    — Publish KG to marketplace
- GET  /v1/marketplace/knowledge-graphs             — Browse marketplace
- GET  /v1/marketplace/knowledge-graphs/<id>        — Detail + ratings
- POST /v1/marketplace/knowledge-graphs/<id>/install — Install into target profile
- POST /v1/marketplace/knowledge-graphs/<id>/fork   — Fork (alias for install)
- POST /v1/marketplace/knowledge-graphs/<id>/rate   — Rate/review
- DELETE /v1/marketplace/knowledge-graphs/<id>      — Unpublish
"""

import hashlib
import json
import logging
import shutil
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from quart import Blueprint, jsonify, request
from trusted_data_agent.api.operations import Arg, operation

from trusted_data_agent.auth.admin import get_current_user_from_request

kg_marketplace_bp = Blueprint("kg_marketplace", __name__)
app_logger = logging.getLogger("quart.app")

_DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"


def _get_user_uuid_from_request():
    """Extract user ID from request (from auth token or header)."""
    user = get_current_user_from_request()
    if user:
        return user.id
    return None


def pack_managed_refusal(sub):
    """A copy made by an AGENT-PACK subscription (``kg_subscriptions.source_kind =
    'agent_pack'``) belongs to the pack subscription: it is synced by the pack's own sync
    (``POST /v1/marketplace/agent-packs/<listing>/sync``) and removed by the pack
    unsubscribe. The KG-marketplace sync/unsubscribe REFUSE it — acting here would sync a
    graph against a KG listing that does not exist, or remove one member of a subscription
    the pack would recreate on its next sync. Returns the refusal payload, or None for a
    KG-listing copy. Pure; a legacy row without the column reads as a KG copy."""
    try:
        kind = sub["source_kind"] if "source_kind" in sub.keys() else None
    except Exception:
        kind = None
    if kind != "agent_pack":
        return None
    listing = sub["source_marketplace_id"]
    return {
        "error": "managed_by_agent_pack",
        "message": "This knowledge graph is a copy made by an agent-pack subscription. "
                   "Sync it with the pack (POST /v1/marketplace/agent-packs/"
                   f"{listing}/sync) and remove it by unsubscribing from the pack.",
        "pack_listing_id": listing,
        "subscription_id": sub["id"],
    }


def _get_conn():
    conn = sqlite_util.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# PUBLISH
# ============================================================================

@kg_marketplace_bp.route("/v1/knowledge-graph/<profile_id>/publish", methods=["POST"])
@operation("knowledge_graph.marketplace_publish", effect="human_only", scope="own", governs=True,
           summary="Publish a graph to the marketplace — other people can then install, fork or subscribe to it",
           path={"profile_id": "the agent the graph belongs to"}, body={"kg_id": "the knowledge graph (knowledge_graph.list lists them)"})
async def publish_knowledge_graph(profile_id: str):
    """
    Publish a knowledge graph to the marketplace.

    Body (JSON):
    {
        "name": "My KG",
        "description": "...",
        "domain": "finance",
        "version": "1.0.0",
        "author": "...",
        "tags": ["sql", "warehouse"],
        "visibility": "public" | "targeted",
        "user_ids": ["uuid1"]  (required if targeted)
    }
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.kg.settings import is_kg_marketplace_enabled
        if not is_kg_marketplace_enabled():
            return jsonify({"error": "Knowledge graph marketplace has been disabled by the administrator."}), 403

        data = (await request.get_json()) or {}
        visibility = data.get("visibility", "public")
        user_ids = data.get("user_ids", [])
        kg_id_param = (data.get("kg_id") or "").strip() or None
        # PROP-008 A5: an EXTENSION is its own listing — only this extension's published
        # layer travels, and the listing declares the base listing it `extends`
        extension_id = (data.get("extension_id") or "").strip() or None

        if visibility not in ("public", "targeted"):
            return jsonify({"error": "Visibility must be 'public' or 'targeted'"}), 400
        if visibility == "targeted" and not user_ids:
            return jsonify({"error": "Targeted publish requires at least one user"}), 400

        # Prevent republishing KGs from subscribed profiles
        from trusted_data_agent.core.config_manager import get_config_manager as _get_cfg_mgr
        _profile = _get_cfg_mgr().get_profile(profile_id, user_uuid)
        if _profile and _profile.get("is_subscribed") and not extension_id:
            return jsonify({"error": "Knowledge graphs from subscribed profiles cannot be republished."}), 403

        # Instantiate the correct KG — use explicit kg_id when provided (multi-KG)
        from components.builtin.knowledge_graph.graph_store import GraphStore
        if kg_id_param:
            # the graph must be the caller's own — the store reads a graph by id for whoever
            # asks, so without this a user could publish another user's graph (Nov 2026)
            _oc = sqlite_util.connect(str(_DB_PATH))
            try:
                _own = _oc.execute("SELECT 1 FROM kg_metadata WHERE kg_id = ? AND user_uuid = ?",
                                   (kg_id_param, user_uuid)).fetchone()
            finally:
                _oc.close()
            if not _own:
                return jsonify({"error": "Knowledge graph not found"}), 404
            store = GraphStore(profile_id, user_uuid, kg_id=kg_id_param)
        else:
            store = GraphStore(profile_id, user_uuid)

        # A subscribed COPY is the publisher's graph: it may not be republished as a base,
        # but the subscriber's own extension of it may — as an extension listing
        extends_marketplace_id = None
        try:
            _km = store.get_kg_metadata() or {}
        except Exception:  # noqa: BLE001
            _km = {}
        if _km.get("is_subscribed"):
            if not extension_id:
                return jsonify({"error": "A subscribed knowledge graph cannot be republished as a base — "
                                         "publish your extension of it instead (extension_id)."}), 403
            try:
                _sc = _get_conn(); _sc.row_factory = sqlite3.Row
                _srow = _sc.execute("SELECT source_marketplace_id FROM kg_subscriptions WHERE id = ?",
                                    (_km.get("source_subscription_id"),)).fetchone()
                _sc.close()
                extends_marketplace_id = _srow["source_marketplace_id"] if _srow else None
            except Exception:  # noqa: BLE001
                extends_marketplace_id = None
        elif extension_id:
            # the extender owns the base too: `extends` is the base's own listing when it has one
            try:
                _sc = _get_conn(); _sc.row_factory = sqlite3.Row
                _brow = _sc.execute(
                    "SELECT id FROM marketplace_knowledge_graphs WHERE kg_id = ? AND publisher_user_id = ? "
                    "AND (extension_id IS NULL OR extension_id = '')", (store.kg_id, user_uuid)).fetchone()
                _sc.close()
                extends_marketplace_id = _brow["id"] if _brow else None
            except Exception:  # noqa: BLE001
                extends_marketplace_id = None

        stats = await store.get_stats()
        if stats.get("total_entities", 0) == 0:
            return jsonify({"error": "Knowledge graph is empty. Add entities before publishing."}), 400

        conn = _get_conn()
        cursor = conn.cursor()

        # Duplicate check: scoped to specific kg_id if provided, else to profile; an
        # extension listing is its own row beside the base's (keyed by extension_id)
        if extension_id:
            cursor.execute(
                "SELECT id FROM marketplace_knowledge_graphs WHERE kg_id = ? AND publisher_user_id = ? AND extension_id = ?",
                (store.kg_id, user_uuid, extension_id),
            )
        elif kg_id_param:
            cursor.execute(
                "SELECT id FROM marketplace_knowledge_graphs WHERE kg_id = ? AND publisher_user_id = ? "
                "AND (extension_id IS NULL OR extension_id = '')",
                (kg_id_param, user_uuid),
            )
        else:
            cursor.execute(
                "SELECT id FROM marketplace_knowledge_graphs WHERE source_profile_id = ? AND publisher_user_id = ? AND (kg_id IS NULL OR kg_id = '')",
                (profile_id, user_uuid),
            )
        existing = cursor.fetchone()
        # Republish-in-place: when the publisher (us) republishes their own
        # KG, update the existing listing rather than rejecting with 409.
        # This preserves the marketplace_id that subscribers' kg_subscriptions
        # rows point to, and triggers an auto-sync fanout.
        republish_marketplace_id = existing["id"] if existing else None

        # Export KG data — complete snapshot (count-derived, never truncated).
        # A bare list_entities(limit=N) is ORDER BY entity_type alphabetically, so
        # a large model exceeding the cap silently drops the last types (metric,
        # schema, table…) — export_graph() sizes the fetch to the live counts.
        entities, relationships = await store.export_graph()

        # PROP-008 A5: the authored layer travels as PUBLISHED nodes only (a draft stays
        # home); an extension listing carries exactly its own extension, a base listing
        # carries the base plus every published extension
        trust_extensions = {}
        try:
            from trusted_data_agent.core.trust import extensions as _trust_ext
            trust_extensions = _trust_ext.capture_claims(store.kg_id, entities)
            if extension_id:
                ext_nodes = _trust_ext.extension_nodes(entities, extension_id, published_only=True)
                if not ext_nodes:
                    conn_probe = None
                    return jsonify({"error": f"extension '{extension_id}' has no published node on this graph — "
                                             "publish it on the Govern dialog first"}), 400
                _keep = {(n.get("name"), n.get("entity_type")) for n in ext_nodes}
                # the decisions linked to the published nodes ride along
                for e in entities:
                    pr = e.get("properties") or {}
                    if e.get("entity_type") == "design_decision" and _trust_ext.extension_id_of(pr) == extension_id:
                        _keep.add((e.get("name"), e.get("entity_type")))
                entities = [e for e in entities if (e.get("name"), e.get("entity_type")) in _keep]
                relationships = _trust_ext.extension_edges(relationships, entities)
                trust_extensions = {k: v for k, v in trust_extensions.items() if k == extension_id}
            else:
                _b_e, _b_r, _, _ = _trust_ext.split_layers(entities, relationships)
                _p_e, _p_r = _trust_ext.portable_authored(entities, relationships, user_uuid)
                entities, relationships = _b_e + _p_e, _b_r + _p_r
        except Exception as _xe:  # noqa: BLE001 — the trust suite is removable
            app_logger.debug(f"KG publish: authored layer carried as-is ({_xe})")

        # Resolve KG metadata (name, description from kg_metadata table)
        kg_meta_name = ""
        kg_meta_description = ""
        kg_meta_database = ""
        try:
            km = store.get_kg_metadata()
            if km:
                kg_meta_name = km.get("name") or ""
                kg_meta_description = km.get("description") or ""
                kg_meta_database = km.get("database_name") or ""
        except Exception:
            pass

        # Build import-ready export with full metadata
        export_data = {
            "export_version": "2.0",
            "kg_id": store.kg_id,
            "kg_name": kg_meta_name,
            "kg_description": kg_meta_description,
            "kg_database_name": kg_meta_database,
            "profile_id": profile_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "entities": entities,
            "relationships": relationships,
        }
        if extension_id:
            export_data["extension_id"] = extension_id
            export_data["extends_marketplace_id"] = extends_marketplace_id
        if trust_extensions:
            export_data["trust_extensions"] = trust_extensions

        # Trust Membrane portability: capture the KG's attestation (if signed) so it
        # travels with the export and can be reinstated on the subscriber's new kg_id.
        # (An extension listing carries no base claim — the base is not in it.)
        try:
            from trusted_data_agent.core.trust import portability as _trust_portability
            _trust_claim = None if extension_id else _trust_portability.capture("ontology", store.kg_id)
            if _trust_claim:
                export_data["trust"] = _trust_claim
        except Exception:
            pass

        content_hash = hashlib.sha256(
            json.dumps(export_data, sort_keys=True, default=str).encode()
        ).hexdigest()

        # Extract type breakdowns from stats (an extension listing counts its own payload)
        entity_types = stats.get("entity_types", {})
        relationship_types = stats.get("relationship_types", {})
        if extension_id:
            entity_types, relationship_types = {}, {}
            for e in entities:
                entity_types[e.get("entity_type")] = entity_types.get(e.get("entity_type"), 0) + 1
            for r in relationships:
                relationship_types[r.get("relationship_type")] = relationship_types.get(r.get("relationship_type"), 0) + 1
            stats = {**stats, "total_entities": len(entities), "total_relationships": len(relationships)}

        # Republish reuses the existing marketplace_id; new publish mints one
        marketplace_id = republish_marketplace_id or str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Resolve profile name for fallback
        profile_name = profile_id
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()
            profiles = config_manager.get_profiles(user_uuid)
            profile = next((p for p in profiles if p.get("id") == profile_id), None)
            if profile:
                profile_name = profile.get("name", profile_id)
        except Exception:
            pass

        name = data.get("name") or kg_meta_name or profile_name
        tags = data.get("tags", [])

        # Build manifest for preview (without full entity/relationship data)
        manifest = {
            "name": name,
            "description": data.get("description", "") or kg_meta_description,
            "domain": data.get("domain", ""),
            "version": data.get("version", "1.0.0"),
            "author": data.get("author", "Unknown"),
            "source_profile_id": profile_id,
            "kg_id": store.kg_id,
            "kg_name": kg_meta_name,
            "kg_database_name": kg_meta_database,
            "entity_count": stats.get("total_entities", 0),
            "relationship_count": stats.get("total_relationships", 0),
            "entity_types": entity_types,
            "relationship_types": relationship_types,
            "tags": tags,
            "extension_id": extension_id,
            "extends_marketplace_id": extends_marketplace_id,
        }

        # Add kg_id column if it exists in the table (safe migration check)
        try:
            cursor.execute("SELECT kg_id FROM marketplace_knowledge_graphs LIMIT 0")
            has_kg_id_col = True
        except Exception:
            has_kg_id_col = False

        if has_kg_id_col:
            if republish_marketplace_id:
                # UPDATE existing listing in place — preserves install_count,
                # download_count, subscriber_count, published_at, and most
                # importantly the marketplace_id that subscribers reference.
                cursor.execute(
                    """UPDATE marketplace_knowledge_graphs
                       SET kg_id = ?, name = ?, description = ?, version = ?,
                           author = ?, domain = ?,
                           entity_count = ?, relationship_count = ?,
                           entity_types_json = ?, relationship_types_json = ?,
                           tags_json = ?, visibility = ?, manifest_json = ?,
                           content_hash = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        store.kg_id, name,
                        data.get("description", "") or kg_meta_description,
                        data.get("version", "1.0.0"),
                        data.get("author", "Unknown"),
                        data.get("domain", ""),
                        stats.get("total_entities", 0),
                        stats.get("total_relationships", 0),
                        json.dumps(entity_types),
                        json.dumps(relationship_types),
                        json.dumps(tags),
                        visibility, json.dumps(manifest), content_hash, now,
                        republish_marketplace_id,
                    ),
                )
            else:
                cursor.execute(
                    """INSERT INTO marketplace_knowledge_graphs
                       (id, source_profile_id, kg_id, name, description, version, author,
                        domain, entity_count, relationship_count,
                        entity_types_json, relationship_types_json, tags_json,
                        publisher_user_id, visibility, manifest_json, content_hash,
                        download_count, install_count, published_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
                    (
                        marketplace_id, profile_id, store.kg_id,
                        name,
                        data.get("description", "") or kg_meta_description,
                        data.get("version", "1.0.0"),
                        data.get("author", "Unknown"),
                        data.get("domain", ""),
                        stats.get("total_entities", 0),
                        stats.get("total_relationships", 0),
                        json.dumps(entity_types),
                        json.dumps(relationship_types),
                        json.dumps(tags),
                        user_uuid, visibility,
                        json.dumps(manifest), content_hash,
                        now, now,
                    ),
                )
        else:
            if republish_marketplace_id:
                cursor.execute(
                    """UPDATE marketplace_knowledge_graphs
                       SET name = ?, description = ?, version = ?, author = ?,
                           domain = ?, entity_count = ?, relationship_count = ?,
                           entity_types_json = ?, relationship_types_json = ?,
                           tags_json = ?, visibility = ?, manifest_json = ?,
                           content_hash = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        name,
                        data.get("description", "") or kg_meta_description,
                        data.get("version", "1.0.0"),
                        data.get("author", "Unknown"),
                        data.get("domain", ""),
                        stats.get("total_entities", 0),
                        stats.get("total_relationships", 0),
                        json.dumps(entity_types),
                        json.dumps(relationship_types),
                        json.dumps(tags),
                        visibility, json.dumps(manifest), content_hash, now,
                        republish_marketplace_id,
                    ),
                )
            else:
                cursor.execute(
                    """INSERT INTO marketplace_knowledge_graphs
                       (id, source_profile_id, name, description, version, author,
                        domain, entity_count, relationship_count,
                        entity_types_json, relationship_types_json, tags_json,
                        publisher_user_id, visibility, manifest_json, content_hash,
                        download_count, install_count, published_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
                    (
                        marketplace_id, profile_id,
                        name,
                        data.get("description", "") or kg_meta_description,
                        data.get("version", "1.0.0"),
                        data.get("author", "Unknown"),
                        data.get("domain", ""),
                        stats.get("total_entities", 0),
                        stats.get("total_relationships", 0),
                        json.dumps(entity_types),
                        json.dumps(relationship_types),
                        json.dumps(tags),
                        user_uuid, visibility,
                        json.dumps(manifest), content_hash,
                        now, now,
                    ),
                )

        # A5: an extension listing records what it extends and which layer it carries
        if has_kg_id_col and extension_id:
            try:
                cursor.execute("UPDATE marketplace_knowledge_graphs SET extension_id = ?, extends_marketplace_id = ? WHERE id = ?",
                               (extension_id, extends_marketplace_id, marketplace_id))
            except sqlite3.OperationalError:
                pass

        # Store export files
        marketplace_data = Path(__file__).resolve().parents[3] / "marketplace_data" / "knowledge_graphs" / marketplace_id
        marketplace_data.mkdir(parents=True, exist_ok=True)
        (marketplace_data / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (marketplace_data / "kg_export.json").write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")

        # Stamp export_content_hash on the listing — subscribers compare this
        # against their last_synced_export_hash to detect drift / sync need.
        export_hash_for_subscribers = _compute_export_hash(marketplace_id)
        try:
            cursor.execute(
                "UPDATE marketplace_knowledge_graphs SET export_content_hash = ? WHERE id = ?",
                (export_hash_for_subscribers, marketplace_id),
            )
        except sqlite3.OperationalError:
            # Column missing on a pre-migration install — fanout will be a no-op
            pass

        # Targeted sharing grants — only for valid, active users
        if visibility == "targeted" and user_ids:
            for uid in user_ids:
                cursor.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1", (uid,))
                if not cursor.fetchone():
                    continue
                cursor.execute(
                    "INSERT OR IGNORE INTO marketplace_sharing_grants "
                    "(id, resource_type, resource_id, grantor_user_id, grantee_user_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(_uuid.uuid4()), "knowledge_graph", marketplace_id, user_uuid, uid, now),
                )

        conn.commit()
        conn.close()

        # On republish, trigger a non-blocking auto-sync fanout to all
        # subscribers of this listing.  Failures are recorded per-subscription
        # in last_sync_error and do not affect the publisher's response.
        if republish_marketplace_id and export_hash_for_subscribers:
            try:
                await _schedule_auto_sync(marketplace_id, export_hash_for_subscribers)
            except Exception as fanout_err:
                app_logger.warning(f"Auto-sync schedule failed (non-fatal): {fanout_err}")

        action = "republished" if republish_marketplace_id else "published"
        app_logger.info(
            f"Knowledge graph {action} from profile '{profile_id}' "
            f"(id={marketplace_id}, visibility={visibility})"
        )
        return jsonify({
            "status": "success",
            "marketplace_id": marketplace_id,
            "republished": bool(republish_marketplace_id),
            "message": (
                "Knowledge graph republished — subscribers will be auto-synced"
                if republish_marketplace_id
                else "Knowledge graph published to marketplace"
            ),
        }), 200

    except Exception as e:
        app_logger.error(f"Knowledge graph publish failed: {e}", exc_info=True)
        return jsonify({"error": f"Publish failed: {e}"}), 500


# ============================================================================
# BROWSE
# ============================================================================

@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs", methods=["GET"])
@operation("marketplace.knowledge_graphs", effect="read", scope="shared",
           summary="The knowledge graphs published in the marketplace that the caller may see — what each covers, by whom, how rated",
           query={"search": "words in a graph's name, description or domain",
                  "domain": "only one domain (default all)",
                  "sort_by": Arg("string", "order", enum=("recent", "rating", "installs", "downloads", "name")),
                  "visibility": Arg("string", "public listings, those targeted at the caller, or both", enum=("public", "targeted", "all")),
                  "page": Arg("integer", "page number (default 1)"), "per_page": Arg("integer", "per page (default 12, max 50)")},
           summary_fields=("id", "name", "description", "publisher_username", "entity_count"))
async def browse_marketplace_knowledge_graphs():
    """
    Browse published knowledge graphs with pagination, search, and sorting.

    Query params: page, per_page, search, sort_by, domain
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        page = int(request.args.get("page", 1))
        per_page = min(int(request.args.get("per_page", 12)), 50)
        search = request.args.get("search", "").strip()
        sort_by = request.args.get("sort_by", "recent")
        domain_filter = request.args.get("domain", "all")
        # 'public' (default) → only public listings; 'targeted' → only listings
        # the user has been granted access to; 'all' → both (legacy union).
        visibility_filter = request.args.get("visibility", "public").strip().lower()
        if visibility_filter not in ("public", "targeted", "all"):
            visibility_filter = "public"

        conn = _get_conn()

        if visibility_filter == "public":
            where_clauses = ["kg.visibility = 'public'"]
            params = []
        elif visibility_filter == "targeted":
            # Show targeted listings the user has a grant for OR is the publisher of
            where_clauses = [
                "kg.visibility = 'targeted' AND (kg.id IN "
                "(SELECT resource_id FROM marketplace_sharing_grants WHERE resource_type = 'knowledge_graph' AND grantee_user_id = ?) "
                "OR kg.publisher_user_id = ?)"
            ]
            params = [user_uuid, user_uuid]
        else:  # 'all'
            # 'all' is the union of what 'public' and 'targeted' return — including
            # the caller's OWN targeted listings, or the name would not be true.
            where_clauses = [
                "(kg.visibility = 'public' OR (kg.visibility = 'targeted' AND (kg.id IN "
                "(SELECT resource_id FROM marketplace_sharing_grants WHERE resource_type = 'knowledge_graph' AND grantee_user_id = ?) "
                "OR kg.publisher_user_id = ?)))"
            ]
            params = [user_uuid, user_uuid]

        if domain_filter != "all":
            where_clauses.append("kg.domain = ?")
            params.append(domain_filter)

        if search:
            where_clauses.append("(kg.name LIKE ? OR kg.description LIKE ? OR kg.domain LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])

        where_sql = " AND ".join(where_clauses)

        sort_map = {
            "downloads": "kg.download_count DESC",
            "installs": "kg.install_count DESC",
            "rating": "avg_rating DESC",
            "recent": "kg.published_at DESC",
            "name": "kg.name ASC",
        }
        order_sql = sort_map.get(sort_by, "kg.published_at DESC")

        cursor = conn.cursor()

        # Total count
        cursor.execute(f"SELECT COUNT(*) FROM marketplace_knowledge_graphs kg WHERE {where_sql}", params)
        total = cursor.fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)

        # Fetch page with ratings JOIN
        offset = (page - 1) * per_page
        query = f"""
            SELECT kg.*,
                   u.username AS publisher_username,
                   u.display_name AS publisher_display_name,
                   COALESCE(r.avg_rating, 0) AS avg_rating,
                   COALESCE(r.rating_count, 0) AS rating_count
            FROM marketplace_knowledge_graphs kg
            LEFT JOIN users u ON kg.publisher_user_id = u.id
            LEFT JOIN (
                SELECT kg_marketplace_id, AVG(rating) AS avg_rating, COUNT(*) AS rating_count
                FROM knowledge_graph_ratings
                GROUP BY kg_marketplace_id
            ) r ON r.kg_marketplace_id = kg.id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
        """
        cursor.execute(query, params + [per_page, offset])
        rows = cursor.fetchall()

        # Batch-check which KGs the current user has installed (for rating gate)
        # and which they currently subscribe to (for the Subscribe / Sync UI).
        if rows:
            kg_ids = [row["id"] for row in rows]
            placeholders = ",".join("?" * len(kg_ids))
            cursor.execute(
                f"SELECT marketplace_id FROM marketplace_installs "
                f"WHERE artifact_type = 'knowledge_graph' AND user_id = ? AND marketplace_id IN ({placeholders})",
                [user_uuid] + kg_ids,
            )
            installed_ids = {r[0] for r in cursor.fetchall()}

            # Subscription state for these listings (one user may have
            # multiple subs of the same listing on different profiles —
            # surface the first one + a count for UI affordance).
            cursor.execute(
                f"SELECT id, source_marketplace_id, last_synced_export_hash "
                f"FROM kg_subscriptions "
                f"WHERE user_uuid = ? AND source_marketplace_id IN ({placeholders})",
                [user_uuid] + kg_ids,
            )
            subscriptions_by_listing = {}
            for r in cursor.fetchall():
                subscriptions_by_listing.setdefault(r["source_marketplace_id"], []).append({
                    "id": r["id"],
                    "last_synced_export_hash": r["last_synced_export_hash"],
                })
        else:
            installed_ids = set()
            subscriptions_by_listing = {}

        knowledge_graphs = []
        for row in rows:
            sub_list = subscriptions_by_listing.get(row["id"], [])
            export_hash = (row["export_content_hash"] if "export_content_hash" in row.keys() else None)
            kg = {
                "id": row["id"],
                "source_profile_id": row["source_profile_id"],
                "name": row["name"],
                "description": row["description"],
                "version": row["version"],
                "author": row["author"],
                "domain": row["domain"],
                "entity_count": row["entity_count"],
                "relationship_count": row["relationship_count"],
                "entity_types": json.loads(row["entity_types_json"]) if row["entity_types_json"] else {},
                "relationship_types": json.loads(row["relationship_types_json"]) if row["relationship_types_json"] else {},
                "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
                "publisher_username": row["publisher_display_name"] or row["publisher_username"],
                "download_count": row["download_count"],
                "install_count": row["install_count"],
                # Phase-2: subscriber_count + export_content_hash present after
                # migration 37; older DBs silently default to 0/None.
                "subscriber_count": (row["subscriber_count"] if "subscriber_count" in row.keys() else 0),
                "export_content_hash": export_hash,
                "average_rating": round(row["avg_rating"], 1),
                "rating_count": row["rating_count"],
                "visibility": row["visibility"],
                "published_at": row["published_at"],
                "is_publisher": row["publisher_user_id"] == user_uuid,
                # A5: an extension listing names the base it extends (None on a base listing)
                "extension_id": (row["extension_id"] if "extension_id" in row.keys() else None),
                "extends": (row["extends_marketplace_id"] if "extends_marketplace_id" in row.keys() else None),
                "is_installed": row["id"] in installed_ids,
                "is_subscribed": bool(sub_list),
                "subscription_count": len(sub_list),
                "subscription_id": sub_list[0]["id"] if sub_list else None,
                # When the subscription's hash matches the listing's
                # export_content_hash, the local copy is up to date —
                # frontend uses this to enable/disable the Sync button.
                "is_sync_available": (
                    bool(sub_list)
                    and export_hash is not None
                    and any(s["last_synced_export_hash"] != export_hash for s in sub_list)
                ),
            }
            knowledge_graphs.append(kg)

        conn.close()

        return jsonify({
            "status": "success",
            "knowledge_graphs": knowledge_graphs,
            "total": total,
            "total_pages": total_pages,
            "page": page,
            "per_page": per_page,
        }), 200

    except Exception as e:
        app_logger.error(f"Browse marketplace knowledge graphs failed: {e}", exc_info=True)
        return jsonify({"error": f"Browse failed: {e}"}), 500


# ============================================================================
# DETAIL
# ============================================================================

@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs/<marketplace_id>", methods=["GET"])
@operation("marketplace.knowledge_graph", effect="read", scope="shared",
           summary="One marketplace graph — what it contains, its versions and its ratings",
           path={"marketplace_id": "the listing (marketplace.knowledge_graphs lists them)"})
async def get_marketplace_kg_detail(marketplace_id: str):
    """Get detail for a single marketplace knowledge graph."""
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        conn = _get_conn()
        cursor = conn.cursor()

        cursor.execute(
            """SELECT kg.*,
                      u.username AS publisher_username,
                      u.display_name AS publisher_display_name,
                      COALESCE(r.avg_rating, 0) AS avg_rating,
                      COALESCE(r.rating_count, 0) AS rating_count
               FROM marketplace_knowledge_graphs kg
               LEFT JOIN users u ON kg.publisher_user_id = u.id
               LEFT JOIN (
                   SELECT kg_marketplace_id, AVG(rating) AS avg_rating, COUNT(*) AS rating_count
                   FROM knowledge_graph_ratings GROUP BY kg_marketplace_id
               ) r ON r.kg_marketplace_id = kg.id
               WHERE kg.id = ?""",
            (marketplace_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Knowledge graph not found"}), 404

        if row["visibility"] == "targeted" and row["publisher_user_id"] != user_uuid:
            cursor.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'knowledge_graph' AND resource_id = ? AND grantee_user_id = ?",
                (marketplace_id, user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"error": "Knowledge graph not found"}), 404

        # Get user's own rating
        cursor.execute(
            "SELECT rating, comment FROM knowledge_graph_ratings WHERE kg_marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_uuid),
        )
        user_rating_row = cursor.fetchone()

        # User's existing subscriptions to this listing (multiple possible —
        # one per target profile)
        cursor.execute(
            "SELECT id, target_profile_id, local_kg_id, last_synced_export_hash, last_synced_at "
            "FROM kg_subscriptions WHERE user_uuid = ? AND source_marketplace_id = ?",
            (user_uuid, marketplace_id),
        )
        subscription_rows = [dict(r) for r in cursor.fetchall()]

        conn.close()

        export_hash = row["export_content_hash"] if "export_content_hash" in row.keys() else None
        kg = {
            "id": row["id"],
            "source_profile_id": row["source_profile_id"],
            "name": row["name"],
            "description": row["description"],
            "version": row["version"],
            "author": row["author"],
            "domain": row["domain"],
            "entity_count": row["entity_count"],
            "relationship_count": row["relationship_count"],
            "entity_types": json.loads(row["entity_types_json"]) if row["entity_types_json"] else {},
            "relationship_types": json.loads(row["relationship_types_json"]) if row["relationship_types_json"] else {},
            "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
            "manifest_json": json.loads(row["manifest_json"]) if row["manifest_json"] else {},
            "publisher_username": row["publisher_display_name"] or row["publisher_username"],
            "download_count": row["download_count"],
            "install_count": row["install_count"],
            "subscriber_count": (row["subscriber_count"] if "subscriber_count" in row.keys() else 0),
            "export_content_hash": export_hash,
            "average_rating": round(row["avg_rating"], 1),
            "rating_count": row["rating_count"],
            "visibility": row["visibility"],
            "published_at": row["published_at"],
            "is_publisher": row["publisher_user_id"] == user_uuid,
            "user_rating": dict(user_rating_row) if user_rating_row else None,
            "is_subscribed": bool(subscription_rows),
            "subscription_count": len(subscription_rows),
            "subscriptions": subscription_rows,
            "is_sync_available": (
                bool(subscription_rows)
                and export_hash is not None
                and any(s["last_synced_export_hash"] != export_hash for s in subscription_rows)
            ),
        }

        return jsonify({"status": "success", "knowledge_graph": kg}), 200

    except Exception as e:
        app_logger.error(f"Get marketplace KG detail failed: {e}", exc_info=True)
        return jsonify({"error": f"Failed to get knowledge graph detail: {e}"}), 500


# ============================================================================
# INSTALL
# ============================================================================

@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs/<marketplace_id>/install", methods=["POST"])
@operation("marketplace.knowledge_graph_install", effect="human_only", scope="shared",
           summary="Install a marketplace graph as the caller's own copy",
           path={"marketplace_id": "the listing"})
async def install_marketplace_kg(marketplace_id: str):
    """
    Install a marketplace knowledge graph into the user's target profile.

    Body (JSON): {"target_profile_id": "profile-uuid"}
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.kg.settings import is_kg_marketplace_enabled
        if not is_kg_marketplace_enabled():
            return jsonify({"error": "Knowledge graph marketplace has been disabled by the administrator."}), 403

        data = (await request.get_json()) or {}
        target_profile_id = data.get("target_profile_id")
        if not target_profile_id:
            return jsonify({"error": "target_profile_id is required"}), 400
        kg_store_config_id = data.get("kg_store_config_id") or None

        # Fix 3A: Validate that the target profile exists and belongs to the subscriber
        from trusted_data_agent.core.config_manager import get_config_manager
        _cfg = get_config_manager()
        target_profile = _cfg.get_profile(target_profile_id, user_uuid)
        if not target_profile:
            return jsonify({
                "error": "target_profile_id not found or does not belong to your account",
            }), 404

        conn = _get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM marketplace_knowledge_graphs WHERE id = ?", (marketplace_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Knowledge graph not found in marketplace"}), 404

        if row["visibility"] == "targeted" and row["publisher_user_id"] != user_uuid:
            cursor.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'knowledge_graph' AND resource_id = ? AND grantee_user_id = ?",
                (marketplace_id, user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"error": "Knowledge graph not found in marketplace"}), 404

        # Read exported KG data
        marketplace_data = Path(__file__).resolve().parents[3] / "marketplace_data" / "knowledge_graphs" / marketplace_id
        export_path = marketplace_data / "kg_export.json"
        if not export_path.exists():
            conn.close()
            return jsonify({"error": "Knowledge graph export data not found"}), 404

        export_data = json.loads(export_path.read_text(encoding="utf-8"))
        entities = export_data.get("entities", [])
        relationships = export_data.get("relationships", [])

        import uuid as _uuid_inst
        from components.builtin.knowledge_graph.graph_store import GraphStore

        # PROP-008 A5: an EXTENSION listing never creates a graph — it lands on the
        # caller's local copy of the base it `extends` (`target_kg_id`, or the one
        # subscribed copy of that base), replacing its namesake layer only
        _ext_id = export_data.get("extension_id") or (row["extension_id"] if "extension_id" in row.keys() else None)
        if _ext_id:
            _extends = export_data.get("extends_marketplace_id") or (
                row["extends_marketplace_id"] if "extends_marketplace_id" in row.keys() else None)
            target_kg_id = (data.get("target_kg_id") or "").strip() or None
            if not target_kg_id and _extends:
                cursor.execute("SELECT local_kg_id FROM kg_subscriptions WHERE user_uuid = ? AND source_marketplace_id = ? "
                               "ORDER BY subscribed_at DESC LIMIT 1", (user_uuid, _extends))
                _sub = cursor.fetchone()
                target_kg_id = _sub["local_kg_id"] if _sub else None
            if not target_kg_id:
                conn.close()
                return jsonify({"error": "base_required", "code": "base_required",
                                "message": "this listing is an extension — subscribe to (or install) the base it "
                                           "extends first, then install the extension onto that copy (target_kg_id)",
                                "extends": _extends}), 409
            _ci = sqlite_util.connect(str(_DB_PATH)); _ci.row_factory = sqlite3.Row
            _trow = _ci.execute("SELECT profile_id FROM kg_metadata WHERE kg_id = ? AND user_uuid = ? LIMIT 1",
                                (target_kg_id, user_uuid)).fetchone()
            _ci.close()
            if not _trow:
                conn.close()
                return jsonify({"error": "target_kg_id not found or does not belong to your account"}), 404
            try:
                _store = GraphStore(_trow["profile_id"], user_uuid, kg_id=target_kg_id, allow_subscribed_write=True)
                _res = _store.merge_authored_layer(entities, relationships)
                try:
                    from trusted_data_agent.core.trust import extensions as _trust_ext
                    _reinst = _trust_ext.reinstate_claims(target_kg_id, export_data.get("trust_extensions"))
                except Exception:  # noqa: BLE001
                    _reinst = {}
            except Exception as _ie:
                conn.close()
                app_logger.error(f"extension install failed: {_ie}", exc_info=True)
                return jsonify({"error": f"Install failed: {_ie}"}), 500
            now_ts = datetime.now(timezone.utc).isoformat()
            cursor.execute("UPDATE marketplace_knowledge_graphs SET install_count = install_count + 1 WHERE id = ?", (marketplace_id,))
            cursor.execute("INSERT OR IGNORE INTO marketplace_installs (id, artifact_type, marketplace_id, user_id, installed_at) "
                           "VALUES (?, 'knowledge_graph', ?, ?, ?)", (str(_uuid.uuid4()), marketplace_id, user_uuid, now_ts))
            conn.commit(); conn.close()
            return jsonify({"status": "success", "kg_id": target_kg_id, "extension_id": _ext_id,
                            "extensions_replaced": _res.get("extensions_replaced", []),
                            "entities_added": _res.get("entities_added", 0),
                            "relationships_added": _res.get("relationships_added", 0),
                            "trust_reinstated": {k: bool(v) for k, v in (_reinst or {}).items()},
                            "message": f"Extension '{row['name']}' installed onto your copy of the base"}), 200

        # Always create a fresh KG — never overwrite an existing one
        new_kg_id = str(_uuid_inst.uuid4())
        store = GraphStore(target_profile_id, user_uuid, kg_id=new_kg_id,
                           kg_store_config_id=kg_store_config_id)

        # Check if the target profile already has an active KG
        _ci = sqlite_util.connect(str(_DB_PATH))
        _ci.row_factory = sqlite3.Row
        _has_active = _ci.execute(
            "SELECT 1 FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? AND is_active = 1 LIMIT 1",
            (target_profile_id, user_uuid)
        ).fetchone() is not None
        _ci.close()

        installed_as_inactive = _has_active  # Will be set inactive because active already exists

        # Register KG metadata before importing so entities are scoped to new_kg_id
        kg_name = export_data.get("kg_name") or row["name"]
        kg_description = export_data.get("kg_description") or ""
        kg_database_name = export_data.get("kg_database_name") or ""

        # Warn if a KG with the same name already exists in this profile (silent duplicate guard)
        _dup_conn = sqlite_util.connect(str(_DB_PATH))
        _dup_conn.row_factory = sqlite3.Row
        _name_collision = _dup_conn.execute(
            "SELECT 1 FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? AND name = ? LIMIT 1",
            (target_profile_id, user_uuid, kg_name),
        ).fetchone() is not None
        _dup_conn.close()

        # Fix 3A: Partial rollback guard — track whether metadata was written
        metadata_written = False
        try:
            store.set_kg_metadata(
                name=kg_name,
                database_name=kg_database_name,
                description=kg_description or None,
                is_active=not _has_active,
                kg_store_config_id=kg_store_config_id,
            )
            metadata_written = True

            # Set source to 'marketplace' for provenance tracking
            for ent in entities:
                if ent.get("source") not in ("marketplace",):
                    ent["source"] = "marketplace"

            result = store.import_bulk(entities, relationships)
        except Exception as import_err:
            # Fix 3A: Roll back orphaned KG metadata if import_bulk failed
            if metadata_written:
                try:
                    _rb = sqlite_util.connect(str(_DB_PATH))
                    _rb.execute(
                        "DELETE FROM kg_metadata WHERE kg_id = ? AND user_uuid = ?",
                        (new_kg_id, user_uuid),
                    )
                    _rb.commit()
                    _rb.close()
                    # Purge any derived state with it. This rollback removes a KG that
                    # was never established, so there is normally nothing to purge --
                    # but the rule that derived state dies with its KG is kept absolute
                    # rather than carrying an exemption list, which is where the next
                    # deletion site would hide. A no-op DELETE costs nothing.
                    try:
                        from trusted_data_agent.core import kg_derived_state as _kgds
                        _kgds.purge_for_kg(new_kg_id)
                    except Exception:  # noqa: BLE001
                        pass
                    app_logger.warning(
                        f"Rolled back orphaned kg_metadata for kg_id={new_kg_id} "
                        f"after import_bulk failure"
                    )
                except Exception as rb_err:
                    app_logger.error(f"KG metadata rollback failed: {rb_err}", exc_info=True)
            conn.close()
            raise import_err

        # Trust Membrane portability: reinstate the publisher's attestation on the new
        # kg_id iff the imported content matches what was signed (honesty gate).
        try:
            from trusted_data_agent.core.trust import portability as _trust_portability
            _trust_portability.reinstate_kind("ontology", new_kg_id, export_data.get("trust"))
        except Exception:
            pass
        try:   # A5: the published extensions' claims follow the base
            from trusted_data_agent.core.trust import extensions as _trust_ext
            _trust_ext.reinstate_claims(new_kg_id, export_data.get("trust_extensions"))
        except Exception:
            pass

        # Increment install_count + record install for rating gate (Fix 4A)
        now_ts = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "UPDATE marketplace_knowledge_graphs SET install_count = install_count + 1 WHERE id = ?",
            (marketplace_id,),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO marketplace_installs "
            "(id, artifact_type, marketplace_id, user_id, installed_at) "
            "VALUES (?, 'knowledge_graph', ?, ?, ?)",
            (str(_uuid.uuid4()), marketplace_id, user_uuid, now_ts),
        )
        conn.commit()
        conn.close()

        app_logger.info(
            f"User {user_uuid} installed marketplace KG into profile '{target_profile_id}' "
            f"(marketplace_id={marketplace_id}, kg_id={new_kg_id}, "
            f"entities={result.get('entities_added', 0)}, "
            f"relationships={result.get('relationships_added', 0)}, "
            f"installed_as_inactive={installed_as_inactive})"
        )

        response = {
            "status": "success",
            "message": f"Knowledge graph '{row['name']}' installed successfully",
            "entities_added": result.get("entities_added", 0),
            "relationships_added": result.get("relationships_added", 0),
            "kg_id": new_kg_id,
            "installed_as_inactive": installed_as_inactive,
        }
        # Fix 3A: Emit advisory warning when KG was installed as inactive
        if installed_as_inactive:
            response["warning"] = (
                "This knowledge graph was installed as inactive because the target profile "
                "already has an active knowledge graph. Activate it via the Knowledge Graph "
                "settings in the profile configuration."
            )
        # M5: Warn on duplicate name (both warnings can coexist)
        if _name_collision:
            response["name_collision_warning"] = (
                f"A knowledge graph named '{kg_name}' already exists in this profile. "
                "Both graphs are now present — rename or remove the duplicate if unintended."
            )
        return jsonify(response), 200

    except Exception as e:
        app_logger.error(f"Install marketplace KG failed: {e}", exc_info=True)
        return jsonify({"error": f"Install failed: {e}"}), 500


# ============================================================================
# FORK (alias for install, increments download_count)
# ============================================================================

@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs/<marketplace_id>/fork", methods=["POST"])
@operation("marketplace.knowledge_graph_fork", effect="human_only", scope="shared",
           summary="Fork a marketplace graph — an editable copy the caller owns",
           path={"marketplace_id": "the listing"})
async def fork_marketplace_kg(marketplace_id: str):
    """
    Fork a marketplace knowledge graph (deep copy into target profile).

    Body (JSON): {"target_profile_id": "profile-uuid"}
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.kg.settings import is_kg_marketplace_enabled
        if not is_kg_marketplace_enabled():
            return jsonify({"error": "Knowledge graph marketplace has been disabled by the administrator."}), 403

        data = (await request.get_json()) or {}
        target_profile_id = data.get("target_profile_id")
        if not target_profile_id:
            return jsonify({"error": "target_profile_id is required"}), 400
        kg_store_config_id = data.get("kg_store_config_id") or None

        conn = _get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM marketplace_knowledge_graphs WHERE id = ?", (marketplace_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Knowledge graph not found in marketplace"}), 404

        if row["visibility"] == "targeted" and row["publisher_user_id"] != user_uuid:
            cursor.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'knowledge_graph' AND resource_id = ? AND grantee_user_id = ?",
                (marketplace_id, user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"error": "Knowledge graph not found in marketplace"}), 404

        # Read exported KG data
        marketplace_data = Path(__file__).resolve().parents[3] / "marketplace_data" / "knowledge_graphs" / marketplace_id
        export_path = marketplace_data / "kg_export.json"
        if not export_path.exists():
            conn.close()
            return jsonify({"error": "Knowledge graph export data not found"}), 404

        export_data = json.loads(export_path.read_text(encoding="utf-8"))
        entities = export_data.get("entities", [])
        relationships = export_data.get("relationships", [])
        if export_data.get("extension_id"):
            conn.close()
            return jsonify({"error": "extension_listing", "code": "extension_listing",
                            "message": "this listing is an extension of another graph — install it onto your "
                                       "copy of the base (POST …/install with target_kg_id); it is not a graph to fork"}), 409

        import uuid as _uuid_fork
        from components.builtin.knowledge_graph.graph_store import GraphStore

        # Always create a fresh KG — never overwrite an existing one
        new_kg_id = str(_uuid_fork.uuid4())
        store = GraphStore(target_profile_id, user_uuid, kg_id=new_kg_id,
                           kg_store_config_id=kg_store_config_id)

        # Check if the target profile already has an active KG
        _cf = sqlite_util.connect(str(_DB_PATH))
        _cf.row_factory = sqlite3.Row
        _has_active_f = _cf.execute(
            "SELECT 1 FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? AND is_active = 1 LIMIT 1",
            (target_profile_id, user_uuid)
        ).fetchone() is not None
        _cf.close()

        # Register KG metadata before importing
        kg_name_f = export_data.get("kg_name") or row["name"]
        kg_description_f = export_data.get("kg_description") or ""
        kg_database_name_f = export_data.get("kg_database_name") or ""

        # M5: Detect duplicate name before writing metadata
        _dup_f = sqlite_util.connect(str(_DB_PATH))
        _dup_f.row_factory = sqlite3.Row
        _name_collision_f = _dup_f.execute(
            "SELECT 1 FROM kg_metadata WHERE profile_id = ? AND user_uuid = ? AND name = ? LIMIT 1",
            (target_profile_id, user_uuid, kg_name_f),
        ).fetchone() is not None
        _dup_f.close()

        store.set_kg_metadata(
            name=kg_name_f,
            database_name=kg_database_name_f,
            description=kg_description_f or None,
            is_active=not _has_active_f,
            kg_store_config_id=kg_store_config_id,
        )

        for ent in entities:
            if ent.get("source") not in ("marketplace",):
                ent["source"] = "marketplace"

        result = store.import_bulk(entities, relationships)

        # Trust Membrane portability: reinstate the attestation on the forked kg_id.
        try:
            from trusted_data_agent.core.trust import portability as _trust_portability
            _trust_portability.reinstate_kind("ontology", new_kg_id, export_data.get("trust"))
        except Exception:
            pass
        try:   # A5: the published extensions' claims follow the base
            from trusted_data_agent.core.trust import extensions as _trust_ext
            _trust_ext.reinstate_claims(new_kg_id, export_data.get("trust_extensions"))
        except Exception:
            pass

        # Fork increments download_count; record install for rating gate (same as install path)
        now_ts = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "UPDATE marketplace_knowledge_graphs SET download_count = download_count + 1 WHERE id = ?",
            (marketplace_id,),
        )
        cursor.execute(
            "INSERT OR IGNORE INTO marketplace_installs "
            "(id, artifact_type, marketplace_id, user_id, installed_at) "
            "VALUES (?, 'knowledge_graph', ?, ?, ?)",
            (str(_uuid.uuid4()), marketplace_id, user_uuid, now_ts),
        )
        conn.commit()
        conn.close()

        app_logger.info(
            f"User {user_uuid} forked marketplace KG into profile '{target_profile_id}' "
            f"(marketplace_id={marketplace_id}, kg_id={new_kg_id})"
        )
        fork_response = {
            "status": "success",
            "message": f"Knowledge graph '{row['name']}' forked successfully",
            "entities_added": result.get("entities_added", 0),
            "relationships_added": result.get("relationships_added", 0),
        }
        if _name_collision_f:
            fork_response["name_collision_warning"] = (
                f"A knowledge graph named '{kg_name_f}' already exists in this profile. "
                "Both graphs are now present — rename or remove the duplicate if unintended."
            )
        return jsonify(fork_response), 200

    except Exception as e:
        app_logger.error(f"Fork marketplace KG failed: {e}", exc_info=True)
        return jsonify({"error": f"Fork failed: {e}"}), 500


# ============================================================================
# SUBSCRIBE / UNSUBSCRIBE / SYNC
# ============================================================================
#
# Subscriptions are a live-tracking copy of a marketplace KG.  Compared to
# Fork (independent copy), a Subscribe yields a read-only local KG that gets
# rebuilt every time the publisher republishes.  Design rationale and edge
# cases live in docs/Architecture/KG_SUBSCRIPTION_PLAN.md.
#
# Storage shape:
#   - kg_subscriptions row links subscriber → publisher's marketplace listing
#   - local_kg_id is a fresh kg_metadata row in the subscriber's profile,
#     marked is_subscribed=1 (read-only via graph_store gates)
#   - sync = raw-SQL DELETE+INSERT inside the existing local_kg_id; activation
#     state on kg_profile_assignments is preserved across sync


def _compute_export_hash(marketplace_id: str):
    """Read kg_export.json from disk and return its sha256, or None if missing."""
    export_path = (
        Path(__file__).resolve().parents[3]
        / "marketplace_data" / "knowledge_graphs" / marketplace_id / "kg_export.json"
    )
    if not export_path.exists():
        return None
    return hashlib.sha256(export_path.read_bytes()).hexdigest()


def _fanout_auto_sync(marketplace_id: str, new_export_hash: str):
    """
    Rebuild every subscriber's local KG against the publisher's latest export.

    Called on (re)publish.  Runs synchronously inside this helper because the
    workload is bounded (per-subscriber rebuild is fast: bulk DELETE + bulk
    INSERT in SQLite).  Async-by-asyncio.create_task wrapping is done at the
    caller site so the publisher's HTTP response returns before the fanout
    finishes — failures are stamped on the subscription row and surfaced via
    /v1/knowledge-graph/subscriptions sync_status.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM kg_subscriptions WHERE source_marketplace_id = ? AND enabled = 1 "
            "AND source_kind = 'kg'",
            (marketplace_id,),
        )
        subs = cur.fetchall()
        if not subs:
            conn.close()
            return 0

        # Read the publisher's latest export once
        export_data, export_hash = _read_export_with_hash(marketplace_id)
        if export_data is None:
            conn.close()
            return 0
        if new_export_hash and export_hash != new_export_hash:
            # Hash drift between caller's expectation and on-disk truth — log
            # and proceed with on-disk (more authoritative)
            app_logger.warning(
                f"_fanout_auto_sync: hash mismatch for {marketplace_id} "
                f"(caller={new_export_hash[:16]}…, on-disk={export_hash[:16]}…)"
            )

        succeeded = 0
        for sub in subs:
            # Skip if already at this hash
            if sub["last_synced_export_hash"] == export_hash:
                continue
            try:
                _rebuild_subscription_kg(conn, sub, export_data, export_hash)
                conn.commit()
                succeeded += 1
            except Exception as rebuild_err:
                # Roll back this sub's transaction; record error; move on
                conn.rollback()
                _record_sync_error(conn, sub["id"], rebuild_err)
                app_logger.warning(
                    f"_fanout_auto_sync: subscription {sub['id']} rebuild failed: {rebuild_err}"
                )
        conn.close()
        return succeeded
    except Exception as e:
        app_logger.error(f"_fanout_auto_sync failed for {marketplace_id}: {e}", exc_info=True)
        return 0


async def _schedule_auto_sync(marketplace_id: str, export_hash: str):
    """Schedule a non-blocking auto-sync fanout.  Caller awaits nothing."""
    import asyncio
    loop = asyncio.get_event_loop()
    # Run the synchronous fanout in a thread so we don't block the loop on
    # SQLite I/O during a publish that returns 200 to the user.
    loop.run_in_executor(None, _fanout_auto_sync, marketplace_id, export_hash)


def _read_export_with_hash(marketplace_id: str):
    """Load kg_export.json for a marketplace listing and return (data, sha256_hex).

    Returns (None, None) if missing.  The hash is computed over the raw bytes
    so it matches whatever the publisher stamped onto
    marketplace_knowledge_graphs.export_content_hash at publish time.
    """
    export_path = (
        Path(__file__).resolve().parents[3]
        / "marketplace_data" / "knowledge_graphs" / marketplace_id / "kg_export.json"
    )
    if not export_path.exists():
        return None, None
    raw = export_path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _kg_subscription_response_row(row, target_profile=None):
    """Shape a kg_subscriptions row + optional joined profile/marketplace data
    into the response object the frontend expects."""
    return {
        "id":                       row["id"],
        "source_kg_id":             row["source_kg_id"],
        "source_marketplace_id":    row["source_marketplace_id"],
        "target_profile_id":        row["target_profile_id"],
        "local_kg_id":              row["local_kg_id"],
        "enabled":                  bool(row["enabled"]),
        "subscribed_at":            row["subscribed_at"],
        "last_synced_at":           row["last_synced_at"],
        "last_synced_export_hash":  row["last_synced_export_hash"],
        "last_sync_error":          row["last_sync_error"] if "last_sync_error" in row.keys() else None,
    }


@kg_marketplace_bp.route(
    "/v1/marketplace/knowledge-graphs/<marketplace_id>/subscribe",
    methods=["POST"],
)
@operation("marketplace.knowledge_graph_subscribe", effect="human_only", scope="shared",
           summary="Subscribe to a marketplace graph — a read-only copy that follows its publisher",
           path={"marketplace_id": "the listing"})
async def subscribe_marketplace_kg(marketplace_id: str):
    """
    Subscribe to a marketplace knowledge graph.

    Body (JSON):
      {
        "target_profile_id": "profile-uuid",
        "force":             true|false   (optional — proceed even if a fork
                                            of the same source already exists
                                            on this profile)
      }

    Returns 200 with the new subscription record on success.
    Returns 409 on idempotency / fork-conflict / publisher-data-drift.
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.kg.settings import (
            is_kg_marketplace_enabled, is_kg_subscriptions_enabled,
        )
        if not is_kg_marketplace_enabled():
            return jsonify({"error": "Knowledge graph marketplace has been disabled by the administrator."}), 403
        if not is_kg_subscriptions_enabled():
            return jsonify({"error": "Knowledge graph subscriptions have been disabled by the administrator."}), 403

        data = (await request.get_json()) or {}
        target_profile_id = data.get("target_profile_id")
        force = bool(data.get("force"))
        if not target_profile_id:
            return jsonify({"error": "target_profile_id is required"}), 400

        # Validate target profile belongs to subscriber
        from trusted_data_agent.core.config_manager import get_config_manager
        target_profile = get_config_manager().get_profile(target_profile_id, user_uuid)
        if not target_profile:
            return jsonify({"error": "target_profile_id not found or does not belong to your account"}), 404

        conn = _get_conn()
        cursor = conn.cursor()

        # Marketplace listing exists?
        cursor.execute(
            "SELECT * FROM marketplace_knowledge_graphs WHERE id = ?",
            (marketplace_id,),
        )
        listing = cursor.fetchone()
        if not listing:
            conn.close()
            return jsonify({"error": "Knowledge graph not found in marketplace"}), 404

        # Self-subscribe block — publisher would just use their own KG
        if listing["publisher_user_id"] == user_uuid:
            conn.close()
            return jsonify({"error": "Cannot subscribe to your own published knowledge graph"}), 400

        # Targeted-visibility access check (mirrors fork at line 575+)
        if listing["visibility"] == "targeted":
            cursor.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'knowledge_graph' AND resource_id = ? AND grantee_user_id = ?",
                (marketplace_id, user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"error": "Knowledge graph not found in marketplace"}), 404

        # Idempotency — already subscribed under this profile?
        cursor.execute(
            "SELECT * FROM kg_subscriptions "
            "WHERE user_uuid = ? AND source_marketplace_id = ? AND target_profile_id = ?",
            (user_uuid, marketplace_id, target_profile_id),
        )
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return jsonify({
                "status": "already_subscribed",
                "error":  "You are already subscribed to this knowledge graph on the selected profile.",
                "subscription": _kg_subscription_response_row(existing),
            }), 409

        # Fork-conflict check — if a non-subscribed copy of the same source_kg_id
        # already exists on this profile, require force=true
        source_kg_id = listing["kg_id"] if "kg_id" in listing.keys() else None
        if source_kg_id and not force:
            # Best-effort detection: a forked KG carries the same name and
            # was sourced from this marketplace via marketplace_installs.
            # If marketplace_installs has a row for this user + marketplace_id,
            # they previously forked it.
            cursor.execute(
                "SELECT 1 FROM marketplace_installs "
                "WHERE artifact_type = 'knowledge_graph' "
                "  AND marketplace_id = ? AND user_id = ? LIMIT 1",
                (marketplace_id, user_uuid),
            )
            if cursor.fetchone():
                conn.close()
                return jsonify({
                    "status": "fork_conflict",
                    "error":  "You have previously forked this knowledge graph. "
                              "Subscribing creates a separate live-tracking copy. "
                              "Pass force=true to proceed.",
                }), 409

        # Read + verify the publisher's export
        export_data, export_hash = _read_export_with_hash(marketplace_id)
        if export_data is None:
            conn.close()
            return jsonify({"error": "Knowledge graph export data not found"}), 404

        # If publisher has stamped a hash, verify it matches what's on disk —
        # this catches drift between the listing row and the file.
        listing_hash = listing["export_content_hash"] if "export_content_hash" in listing.keys() else None
        if listing_hash and listing_hash != export_hash:
            app_logger.warning(
                f"Marketplace KG {marketplace_id}: stored hash {listing_hash[:16]}… "
                f"differs from on-disk hash {export_hash[:16]}…  Proceeding with on-disk."
            )

        # Materialize into a fresh local KG — ONE primitive shared with agent-pack
        # subscriptions (core.kg_subscription; P0 of PACK_SUBSCRIBE_COMPLETION.md). The
        # order inside is kept: is_subscribed=1 is set AFTER import_bulk so the store's
        # read-only gate does not block the materialization itself.
        from trusted_data_agent.core import kg_subscription as _kgsub
        try:
            _made = _kgsub.materialize_copy(
                conn, db_path=str(_DB_PATH), user_uuid=user_uuid,
                target_profile_id=target_profile_id, source_kg_id=source_kg_id,
                source_marketplace_id=marketplace_id, source_kind=_kgsub.SOURCE_KG,
                export_data=export_data, export_hash=export_hash,
                fallback_name=listing["name"],
            )
        except Exception as mat_err:
            conn.close()
            app_logger.error(f"Subscribe: materialization failed: {mat_err}", exc_info=True)
            return jsonify({"error": f"Subscribe failed: {mat_err}"}), 500
        subscription_id = _made["subscription_id"]
        new_kg_id = _made["local_kg_id"]
        kg_name = _made["kg_name"]
        _has_active = not _made["active"]
        import_result = {"entities_added": _made["entities_added"],
                         "relationships_added": _made["relationships_added"]}
        cursor.execute(
            "UPDATE marketplace_knowledge_graphs SET subscriber_count = subscriber_count + 1 "
            "WHERE id = ?",
            (marketplace_id,),
        )

        conn.commit()
        conn.close()

        app_logger.info(
            f"User {user_uuid} subscribed to marketplace KG '{kg_name}' "
            f"(marketplace_id={marketplace_id}, local_kg_id={new_kg_id}, "
            f"entities={import_result.get('entities_added', 0)}, "
            f"relationships={import_result.get('relationships_added', 0)})"
        )

        return jsonify({
            "status": "ok",
            "message": f"Subscribed to '{listing['name']}'",
            "subscription_id": subscription_id,
            "local_kg_id": new_kg_id,
            "entities_added": import_result.get("entities_added", 0),
            "relationships_added": import_result.get("relationships_added", 0),
            "installed_as_inactive": _has_active,
            "last_synced_export_hash": export_hash,
        }), 200

    except Exception as e:
        app_logger.error(f"Subscribe marketplace KG failed: {e}", exc_info=True)
        return jsonify({"error": f"Subscribe failed: {e}"}), 500


@kg_marketplace_bp.route("/v1/knowledge-graph/subscriptions", methods=["GET"])
@operation("knowledge_graph.subscriptions", effect="read", scope="own",
           summary="The marketplace graphs the caller subscribes to, and whether each is up to date with its publisher")
async def list_my_kg_subscriptions():
    """
    List the current user's KG subscriptions with sync status.

    Each row reports:
      - subscription_id, source_marketplace_id, source_kg_id, target_profile_id,
        local_kg_id
      - subscribed_at, last_synced_at, last_synced_export_hash, last_sync_error
      - publisher's listing name, version, publisher_username (joined)
      - sync_status: 'up_to_date' | 'sync_available' | 'sync_failed' | 'orphaned'
        (orphaned = publisher unpublished, marketplace_id no longer exists)
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.*,
                   kg.name AS listing_name,
                   kg.version AS listing_version,
                   kg.export_content_hash AS listing_export_hash,
                   u.username AS publisher_username,
                   u.display_name AS publisher_display_name
            FROM kg_subscriptions s
            LEFT JOIN marketplace_knowledge_graphs kg
                   ON kg.id = s.source_marketplace_id
            LEFT JOIN users u
                   ON u.id = kg.publisher_user_id
            WHERE s.user_uuid = ?
            ORDER BY s.subscribed_at DESC
            """,
            (user_uuid,),
        )
        rows = cursor.fetchall()
        conn.close()

        out = []
        for r in rows:
            listing_hash = r["listing_export_hash"]
            listing_missing = r["listing_name"] is None
            if listing_missing:
                status = "orphaned"
            elif r["last_sync_error"]:
                status = "sync_failed"
            elif listing_hash and listing_hash != r["last_synced_export_hash"]:
                status = "sync_available"
            else:
                status = "up_to_date"

            out.append({
                "subscription_id":         r["id"],
                "source_kg_id":            r["source_kg_id"],
                "source_marketplace_id":   r["source_marketplace_id"],
                "target_profile_id":       r["target_profile_id"],
                "local_kg_id":             r["local_kg_id"],
                "enabled":                 bool(r["enabled"]),
                "subscribed_at":           r["subscribed_at"],
                "last_synced_at":          r["last_synced_at"],
                "last_synced_export_hash": r["last_synced_export_hash"],
                "last_sync_error":         r["last_sync_error"],
                "listing_name":            r["listing_name"],
                "listing_version":         r["listing_version"],
                "publisher_username":      r["publisher_display_name"] or r["publisher_username"],
                "sync_status":             status,
            })
        return jsonify({"status": "success", "subscriptions": out}), 200
    except Exception as e:
        app_logger.error(f"List KG subscriptions failed: {e}", exc_info=True)
        return jsonify({"error": f"List failed: {e}"}), 500


def _delete_subscription_local_kg(conn, subscription_row):
    """Delegates to core.kg_subscription.delete_copy — ONE primitive for KG-listing and
    agent-pack subscriptions alike (P0 of docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md)."""
    from trusted_data_agent.core import kg_subscription as _kgsub
    return _kgsub.delete_copy(conn, subscription_row)


@kg_marketplace_bp.route(
    "/v1/marketplace/knowledge-graphs/<marketplace_id>/subscribe",
    methods=["DELETE"],
)
@operation("marketplace.knowledge_graph_unsubscribe", effect="human_only", scope="own",
           summary="Stop subscribing to a marketplace graph",
           path={"marketplace_id": "the listing"})
async def unsubscribe_marketplace_kg(marketplace_id: str):
    """
    Unsubscribe from a marketplace knowledge graph.

    Query params:
      ?subscription_id=<uuid>  — required when the user has multiple
                                 subscriptions of the same KG into different
                                 profiles.  Optional otherwise.

    On success the local kg_metadata + kg_entities + kg_relationships rows
    are hard-deleted and the kg_subscriptions row is removed.  Sessions
    referencing the subscribed KG will be archived (Phase 5).
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.kg.settings import is_kg_subscriptions_enabled
        if not is_kg_subscriptions_enabled():
            return jsonify({"error": "Knowledge graph subscriptions have been disabled by the administrator."}), 403

        subscription_id = request.args.get("subscription_id", "").strip() or None

        conn = _get_conn()
        cursor = conn.cursor()

        if subscription_id:
            cursor.execute(
                "SELECT * FROM kg_subscriptions WHERE id = ? AND user_uuid = ?",
                (subscription_id, user_uuid),
            )
            sub = cursor.fetchone()
            if not sub:
                conn.close()
                return jsonify({"error": "Subscription not found"}), 404
            if sub["source_marketplace_id"] != marketplace_id:
                conn.close()
                return jsonify({"error": "Subscription does not match marketplace_id"}), 400
            matches = [sub]
        else:
            cursor.execute(
                "SELECT * FROM kg_subscriptions "
                "WHERE user_uuid = ? AND source_marketplace_id = ?",
                (user_uuid, marketplace_id),
            )
            matches = cursor.fetchall()
            if not matches:
                conn.close()
                return jsonify({"error": "No active subscription found for this knowledge graph"}), 404
            if len(matches) > 1:
                # Ambiguous — caller must disambiguate
                conn.close()
                return jsonify({
                    "error": "Multiple subscriptions exist for this knowledge graph. Specify ?subscription_id=...",
                    "subscriptions": [_kg_subscription_response_row(r) for r in matches],
                }), 400

        # Apply cascade for the (single) matched subscription
        sub = matches[0]
        _refusal = pack_managed_refusal(sub)
        if _refusal:
            conn.close()
            return jsonify(_refusal), 409
        _delete_subscription_local_kg(conn, sub)
        cursor.execute(
            "UPDATE marketplace_knowledge_graphs "
            "SET subscriber_count = MAX(subscriber_count - 1, 0) WHERE id = ?",
            (marketplace_id,),
        )
        conn.commit()
        conn.close()

        # Phase 5 note: sessions don't bind to KG IDs directly — they look up
        # the active KG per-profile via kg_profile_assignments at query time.
        # Removing the assignment (done in _delete_subscription_local_kg above)
        # is sufficient — in-flight queries that cached the kg_id degrade
        # gracefully on the next lookup miss.  No session archival required.

        app_logger.info(
            f"User {user_uuid} unsubscribed from marketplace KG "
            f"(marketplace_id={marketplace_id}, local_kg_id={sub['local_kg_id']})"
        )
        return jsonify({
            "status": "ok",
            "message": "Unsubscribed",
            "subscription_id": sub["id"],
            "local_kg_id": sub["local_kg_id"],
        }), 200

    except Exception as e:
        app_logger.error(f"Unsubscribe marketplace KG failed: {e}", exc_info=True)
        return jsonify({"error": f"Unsubscribe failed: {e}"}), 500


def _rebuild_subscription_kg(conn, sub, export_data, export_hash, *, on_error_record=True):
    """Delegates to core.kg_subscription.rebuild_copy (see _delete_subscription_local_kg)."""
    from trusted_data_agent.core import kg_subscription as _kgsub
    return _kgsub.rebuild_copy(conn, sub, export_data, export_hash)


def _record_sync_error(conn, subscription_id, error_message):
    """Delegates to core.kg_subscription.record_sync_error."""
    from trusted_data_agent.core import kg_subscription as _kgsub
    return _kgsub.record_sync_error(conn, subscription_id, error_message)


@kg_marketplace_bp.route(
    "/v1/marketplace/knowledge-graphs/<marketplace_id>/sync",
    methods=["POST"],
)
@operation("marketplace.knowledge_graph_sync", effect="human_only", scope="own",
           summary="Bring a subscribed graph up to date with its publisher",
           path={"marketplace_id": "the listing"})
async def sync_marketplace_kg(marketplace_id: str):
    """
    Subscriber-initiated sync — rebuild the local copy from the latest export.

    Query params:
      ?subscription_id=<uuid>  — required when the user has multiple
                                 subscriptions of the same KG.

    Fast-path: if the publisher's export hash equals the subscriber's last
    synced hash, return immediately without touching tables.
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        from trusted_data_agent.kg.settings import is_kg_subscriptions_enabled
        if not is_kg_subscriptions_enabled():
            return jsonify({"error": "Knowledge graph subscriptions have been disabled by the administrator."}), 403

        subscription_id = request.args.get("subscription_id", "").strip() or None

        conn = _get_conn()
        cursor = conn.cursor()

        if subscription_id:
            cursor.execute(
                "SELECT * FROM kg_subscriptions WHERE id = ? AND user_uuid = ?",
                (subscription_id, user_uuid),
            )
            matches = [r for r in [cursor.fetchone()] if r]
        else:
            cursor.execute(
                "SELECT * FROM kg_subscriptions "
                "WHERE user_uuid = ? AND source_marketplace_id = ?",
                (user_uuid, marketplace_id),
            )
            matches = cursor.fetchall()

        if not matches:
            conn.close()
            return jsonify({"error": "No active subscription found"}), 404
        if len(matches) > 1:
            conn.close()
            return jsonify({
                "error": "Multiple subscriptions; specify ?subscription_id=...",
                "subscriptions": [_kg_subscription_response_row(r) for r in matches],
            }), 400

        sub = matches[0]
        _refusal = pack_managed_refusal(sub)
        if _refusal:
            conn.close()
            return jsonify(_refusal), 409

        # Read latest export
        export_data, export_hash = _read_export_with_hash(marketplace_id)
        if export_data is None:
            _record_sync_error(conn, sub["id"], "Export missing — publisher may have unpublished")
            conn.close()
            return jsonify({"error": "Knowledge graph export data not found"}), 404

        # Fast-path no-op
        if sub["last_synced_export_hash"] == export_hash:
            conn.close()
            return jsonify({
                "status": "up_to_date",
                "subscription_id": sub["id"],
                "local_kg_id": sub["local_kg_id"],
                "last_synced_export_hash": export_hash,
            }), 200

        try:
            result = _rebuild_subscription_kg(conn, sub, export_data, export_hash)
            conn.commit()
        except Exception as rebuild_err:
            conn.rollback()
            _record_sync_error(conn, sub["id"], rebuild_err)
            conn.close()
            app_logger.error(f"Sync rebuild failed for subscription {sub['id']}: {rebuild_err}", exc_info=True)
            return jsonify({"error": f"Sync failed: {rebuild_err}"}), 500
        finally:
            try: conn.close()
            except Exception: pass

        app_logger.info(
            f"User {user_uuid} synced subscription {sub['id']} "
            f"(local_kg_id={sub['local_kg_id']}, "
            f"entities={result['entities_added']}, relationships={result['relationships_added']})"
        )
        return jsonify({
            "status": "synced",
            "subscription_id": sub["id"],
            "local_kg_id": sub["local_kg_id"],
            "last_synced_export_hash": export_hash,
            "entities_added": result["entities_added"],
            "relationships_added": result["relationships_added"],
        }), 200

    except Exception as e:
        app_logger.error(f"Sync marketplace KG failed: {e}", exc_info=True)
        return jsonify({"error": f"Sync failed: {e}"}), 500


# ============================================================================
# RATE
# ============================================================================

@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs/<marketplace_id>/rate", methods=["POST"])
@operation("marketplace.knowledge_graph_rate", effect="human_only", scope="shared",
           summary="Rate a marketplace graph",
           path={"marketplace_id": "the listing"})
async def rate_marketplace_kg(marketplace_id: str):
    """
    Rate a marketplace knowledge graph.

    Body (JSON): {"rating": 5, "comment": "Excellent schema coverage!"}
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        data = await request.get_json()
        if not data or "rating" not in data:
            return jsonify({"error": "rating is required"}), 400

        rating = int(data["rating"])
        if rating < 1 or rating > 5:
            return jsonify({"error": "rating must be between 1 and 5"}), 400

        comment = data.get("comment", "").strip() or None

        conn = _get_conn()
        cursor = conn.cursor()

        # Verify KG exists
        cursor.execute(
            "SELECT id, publisher_user_id FROM marketplace_knowledge_graphs WHERE id = ?",
            (marketplace_id,),
        )
        kg_row = cursor.fetchone()
        if not kg_row:
            conn.close()
            return jsonify({"error": "Knowledge graph not found"}), 404

        # Cannot rate own KG
        if kg_row["publisher_user_id"] == user_uuid:
            conn.close()
            return jsonify({"error": "Cannot rate your own knowledge graph"}), 400

        # Fix 4A: Must have installed before rating
        cursor.execute(
            "SELECT 1 FROM marketplace_installs "
            "WHERE artifact_type = 'knowledge_graph' AND marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_uuid),
        )
        if not cursor.fetchone():
            # Legacy fallback: check if user has any marketplace-sourced KG entities
            cursor.execute(
                """SELECT 1 FROM kg_entities e
                   JOIN kg_metadata m ON m.kg_id = e.kg_id
                   WHERE m.user_uuid = ? AND e.source = 'marketplace' LIMIT 1""",
                (user_uuid,),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({
                    "error": "You must install this knowledge graph before rating it.",
                }), 403

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "SELECT id FROM knowledge_graph_ratings WHERE kg_marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_uuid),
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "UPDATE knowledge_graph_ratings SET rating = ?, comment = ?, updated_at = ? WHERE id = ?",
                (rating, comment, now, existing["id"]),
            )
            rating_id = existing["id"]
            message = "Rating updated successfully"
            status_code = 200
        else:
            rating_id = str(_uuid.uuid4())
            cursor.execute(
                "INSERT INTO knowledge_graph_ratings (id, kg_marketplace_id, user_id, rating, comment, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rating_id, marketplace_id, user_uuid, rating, comment, now, now),
            )
            message = "Rating submitted successfully"
            status_code = 201

        conn.commit()
        conn.close()

        return jsonify({"status": "success", "rating_id": rating_id, "message": message}), status_code

    except Exception as e:
        app_logger.error(f"KG rating failed: {e}", exc_info=True)
        return jsonify({"error": f"Rating failed: {e}"}), 500


# ============================================================================
# UNPUBLISH
# ============================================================================

@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs/<marketplace_id>", methods=["DELETE"])
@operation("marketplace.knowledge_graph_unpublish", effect="human_only", scope="own", governs=True,
           summary="Withdraw a graph the caller published from the marketplace",
           path={"marketplace_id": "the caller's listing"})
async def unpublish_knowledge_graph(marketplace_id: str):
    """Unpublish a knowledge graph from the marketplace (publisher only).

    Cascades on subscribers: every subscriber's local KG copy is hard-deleted
    via _delete_subscription_local_kg.  Subscriber sessions referencing the
    deleted local KG are archived (best-effort).  This mirrors the
    agent-pack unpublish cascade.
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"error": "Authentication required"}), 401

        conn = _get_conn()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, name FROM marketplace_knowledge_graphs WHERE id = ? AND publisher_user_id = ?",
            (marketplace_id, user_uuid),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Knowledge graph not found or you are not the publisher"}), 404

        # Cascade-clean subscribers BEFORE deleting the listing.  We capture
        # them in a list because _delete_subscription_local_kg removes rows
        # from kg_subscriptions, which would mutate the cursor result if we
        # iterated it directly.
        cursor.execute(
            "SELECT * FROM kg_subscriptions WHERE source_marketplace_id = ? AND source_kind = 'kg'",
            (marketplace_id,),
        )
        subscribers = list(cursor.fetchall())
        archive_targets = []  # (local_kg_id, user_uuid) pairs for session archival
        for sub in subscribers:
            try:
                _delete_subscription_local_kg(conn, sub)
                archive_targets.append((sub["local_kg_id"], sub["user_uuid"]))
            except Exception as sub_err:
                app_logger.warning(
                    f"Unpublish cascade: failed to delete subscription {sub['id']}: {sub_err}"
                )

        # Delete sharing grants
        cursor.execute(
            "DELETE FROM marketplace_sharing_grants WHERE resource_type = 'knowledge_graph' AND resource_id = ?",
            (marketplace_id,),
        )
        # Delete ratings
        cursor.execute(
            "DELETE FROM knowledge_graph_ratings WHERE kg_marketplace_id = ?",
            (marketplace_id,),
        )
        # Delete marketplace record
        cursor.execute("DELETE FROM marketplace_knowledge_graphs WHERE id = ?", (marketplace_id,))

        conn.commit()
        conn.close()

        # Clean up marketplace_data files
        marketplace_data = Path(__file__).resolve().parents[3] / "marketplace_data" / "knowledge_graphs" / marketplace_id
        if marketplace_data.exists():
            shutil.rmtree(marketplace_data, ignore_errors=True)

        # Phase 5 note: see unsubscribe path — sessions look up active KG per
        # profile dynamically, so removing kg_profile_assignments rows (done
        # by _delete_subscription_local_kg) is sufficient.  No session
        # archival needed here either.

        app_logger.info(
            f"Knowledge graph '{row['name']}' unpublished from marketplace "
            f"(id={marketplace_id}, subscribers_cleaned={len(archive_targets)})"
        )
        return jsonify({
            "status": "success",
            "message": "Knowledge graph unpublished",
            "subscribers_cleaned": len(archive_targets),
        }), 200

    except Exception as e:
        app_logger.error(f"Unpublish KG failed: {e}", exc_info=True)
        return jsonify({"error": f"Unpublish failed: {e}"}), 500


@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs/<marketplace_id>/targeted-users", methods=["GET"])
@operation("marketplace.knowledge_graph_audience", effect="read", scope="own",
           summary="Who a graph the caller published is offered to",
           path={"marketplace_id": "the caller's listing"})
async def get_kg_targeted_users(marketplace_id: str):
    """Return the list of targeted users for a published KG (publisher only)."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required"}), 401
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT publisher_user_id FROM marketplace_knowledge_graphs WHERE id = ?", (marketplace_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Knowledge graph not found"}), 404
        if row["publisher_user_id"] != user_uuid:
            conn.close()
            return jsonify({"error": "Only the publisher can view targeted users"}), 403
        cursor.execute(
            """SELECT g.grantee_user_id AS user_id, u.username, u.display_name
               FROM marketplace_sharing_grants g
               LEFT JOIN users u ON g.grantee_user_id = u.id
               WHERE g.resource_type = 'knowledge_graph' AND g.resource_id = ?""",
            (marketplace_id,),
        )
        users = [
            {"user_id": r["user_id"], "username": r["username"], "display_name": r["display_name"] or r["username"]}
            for r in cursor.fetchall()
        ]
        conn.close()
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        app_logger.error(f"Failed to get KG targeted users: {e}", exc_info=True)
        return jsonify({"error": f"Failed: {e}"}), 500


@kg_marketplace_bp.route("/v1/marketplace/knowledge-graphs/<marketplace_id>/targeted-users", methods=["PUT"])
@operation("marketplace.knowledge_graph_audience_set", effect="human_only", scope="own", governs=True,
           summary="Choose who a published graph is offered to",
           path={"marketplace_id": "the caller's listing"})
async def update_kg_targeted_users(marketplace_id: str):
    """Update the targeted users list for a published KG (publisher only)."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required"}), 401
    try:
        data = (await request.get_json()) or {}
        new_user_ids = set(data.get("user_ids", []))
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT publisher_user_id FROM marketplace_knowledge_graphs WHERE id = ?", (marketplace_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Knowledge graph not found"}), 404
        if row["publisher_user_id"] != user_uuid:
            conn.close()
            return jsonify({"error": "Only the publisher can update targeted users"}), 403
        cursor.execute(
            "SELECT grantee_user_id FROM marketplace_sharing_grants WHERE resource_type = 'knowledge_graph' AND resource_id = ?",
            (marketplace_id,),
        )
        existing_ids = {r["grantee_user_id"] for r in cursor.fetchall()}
        to_remove = existing_ids - new_user_ids
        if to_remove:
            placeholders = ",".join("?" * len(to_remove))
            cursor.execute(
                f"DELETE FROM marketplace_sharing_grants WHERE resource_type = 'knowledge_graph' AND resource_id = ? AND grantee_user_id IN ({placeholders})",
                [marketplace_id] + list(to_remove),
            )
        now = datetime.now(timezone.utc).isoformat()
        for uid in new_user_ids - existing_ids:
            cursor.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1", (uid,))
            if not cursor.fetchone():
                continue
            cursor.execute(
                "INSERT OR IGNORE INTO marketplace_sharing_grants (id, resource_type, resource_id, grantor_user_id, grantee_user_id, created_at) VALUES (?, 'knowledge_graph', ?, ?, ?, ?)",
                (str(_uuid.uuid4()), marketplace_id, user_uuid, uid, now),
            )
        conn.commit()
        cursor.execute(
            """SELECT g.grantee_user_id AS user_id, u.username, u.display_name
               FROM marketplace_sharing_grants g
               LEFT JOIN users u ON g.grantee_user_id = u.id
               WHERE g.resource_type = 'knowledge_graph' AND g.resource_id = ?""",
            (marketplace_id,),
        )
        users = [
            {"user_id": r["user_id"], "username": r["username"], "display_name": r["display_name"] or r["username"]}
            for r in cursor.fetchall()
        ]
        conn.close()
        app_logger.info(f"Updated targeted users for KG {marketplace_id}: {len(users)} users")
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        app_logger.error(f"Failed to update KG targeted users: {e}", exc_info=True)
        return jsonify({"error": f"Failed: {e}"}), 500
