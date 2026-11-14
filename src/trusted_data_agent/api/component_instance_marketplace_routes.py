"""
REST routes for the COMPONENT-INSTANCE marketplace.

Publishes/forks instances of components that declare ``supports_instance_io``
(a flow, a dashboard, …) — the marketplace layer on top of the instance
portability contract (``export_instance`` / ``import_instance``):

    publish  = export_instance()                              -> listing (envelope + content_hash)
    fork     = import_instance(mode="create", mapping=...)    -> independent copy (remap on fork)
    (Phase B) subscribe + sync = import_instance(mode="update", target_id=...)

An instance is DATA (flow definition / dashboard spec), never code, so there is
no code-execution trust concern. Generic over component_id, so flow + dashboard
(+ future canvas) share one path. Gated so you can only publish/fork instances of
a component that is ACTIVATED for you. Mirrors the extensions / KG marketplaces.
"""

import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from quart import Blueprint, jsonify, request

from trusted_data_agent.auth.admin import get_current_user_from_request
from trusted_data_agent.components.manager import get_component_manager
from trusted_data_agent.components.settings import (
    is_component_available,
    is_component_marketplace_enabled,
)

component_instance_marketplace_bp = Blueprint("component_instance_marketplace", __name__)
app_logger = logging.getLogger("quart.app")

_DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"


def _get_conn():
    conn = sqlite_util.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema():
    """Self-apply the marketplace schema on import (idempotent), so the feature is
    zero-deploy-step like the other lazily-created component tables."""
    try:
        schema_path = Path(__file__).resolve().parents[3] / "schema" / "41_marketplace_component_instances.sql"
        if not schema_path.exists():
            return
        conn = sqlite_util.connect(str(_DB_PATH))
        with open(schema_path, "r") as f:
            conn.executescript(f.read())
        conn.commit()
        conn.close()
    except Exception as e:  # pragma: no cover - never block import
        app_logger.warning(f"[CI-Marketplace] schema ensure failed: {e}")


_ensure_schema()


def _user_uuid():
    user = get_current_user_from_request()
    return user.id if user else None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _handler_for(component_id):
    """Return the component handler iff it supports instance IO, else None."""
    mgr = get_component_manager()
    comp = mgr.get_component(component_id) if mgr else None
    handler = getattr(comp, "handler", None) if comp else None
    if not handler or not getattr(handler, "supports_instance_io", False):
        return None
    return handler


def _component_meta(component_id):
    """Display metadata (name/icon/accent) for a component, from its manifest."""
    mgr = get_component_manager()
    comp = mgr.get_component(component_id) if mgr else None
    if not comp:
        return {"component_id": component_id, "display_name": component_id}
    d = comp.to_api_dict()
    return {
        "component_id": component_id,
        "display_name": d.get("display_name") or component_id,
        "icon_svg": d.get("icon_svg") or "",
        "icon_accent": d.get("icon_accent") or "",
    }


def _publisher_name(conn, user_id):
    try:
        row = conn.execute("SELECT username, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if row:
            return row["username"] or (row["email"] or "").split("@")[0] or "Unknown"
    except Exception:
        pass
    return "Unknown"


def _ratings(conn, marketplace_id):
    row = conn.execute(
        "SELECT AVG(rating) AS avg, COUNT(*) AS cnt FROM component_instance_ratings WHERE instance_marketplace_id = ?",
        (marketplace_id,),
    ).fetchone()
    avg = round(row["avg"], 1) if row and row["avg"] is not None else 0.0
    cnt = row["cnt"] if row else 0
    return avg, cnt


def _has_targeted_access(conn, marketplace_id, user_uuid):
    row = conn.execute(
        "SELECT 1 FROM marketplace_sharing_grants WHERE resource_type='component_instance' AND resource_id=? AND grantee_user_id=?",
        (marketplace_id, user_uuid),
    ).fetchone()
    return row is not None


def _is_installed(conn, marketplace_id, user_uuid):
    row = conn.execute(
        "SELECT 1 FROM marketplace_installs WHERE artifact_type='component_instance' AND marketplace_id=? AND user_id=?",
        (marketplace_id, user_uuid),
    ).fetchone()
    return row is not None


def _subscription_state(conn, marketplace_id, user_uuid, listing_hash):
    """Subscription view for a (listing, user): subscribed? sync available?"""
    row = conn.execute(
        "SELECT local_instance_id, synced_content_hash FROM component_instance_subscriptions "
        "WHERE marketplace_id=? AND subscriber_user_id=?",
        (marketplace_id, user_uuid),
    ).fetchone()
    if not row:
        return {"is_subscribed": False, "is_sync_available": False, "local_instance_id": None}
    return {
        "is_subscribed": True,
        # Up to date when the publisher's content_hash matches what we last synced.
        "is_sync_available": bool(listing_hash) and (row["synced_content_hash"] != listing_hash),
        "local_instance_id": row["local_instance_id"],
    }


# ============================================================================
# PUBLISH  (export_instance -> listing; republish updates in place)
# ============================================================================

@component_instance_marketplace_bp.route(
    "/v1/components/<component_id>/instances/<instance_id>/publish", methods=["POST"]
)
async def publish_component_instance(component_id: str, instance_id: str):
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    if not is_component_marketplace_enabled():
        return jsonify({"status": "error", "message": "Component marketplace is disabled"}), 403
    # Activation gate — can only publish instances of a component activated for you.
    if not is_component_available(component_id, user_uuid):
        return jsonify({"status": "error", "message": f"The {component_id} component is not enabled for you"}), 403
    handler = _handler_for(component_id)
    if not handler:
        return jsonify({"status": "error", "message": f"'{component_id}' does not support publishing instances"}), 400

    body = await request.get_json(silent=True) or {}
    visibility = body.get("visibility", "public")
    if visibility not in ("public", "targeted"):
        return jsonify({"status": "error", "message": "visibility must be 'public' or 'targeted'"}), 400
    user_ids = body.get("user_ids") or []

    try:
        envelope = await handler.export_instance(instance_id, {"user_uuid": user_uuid})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 404
    except Exception as e:
        app_logger.error(f"[CI-Marketplace] export failed {component_id}/{instance_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Could not read the instance"}), 500

    # Trust Membrane portability: carry the instance's attestation (if signed) in the envelope.
    try:
        from trusted_data_agent.core.trust import portability as _trust_portability
        _ci_claim = _trust_portability.capture(component_id, str(instance_id))
        if _ci_claim:
            envelope["trust"] = _ci_claim
    except Exception:
        pass

    name = (body.get("name") or envelope.get("name") or "Untitled").strip()
    description = (body.get("description") or envelope.get("description") or "").strip()
    content_hash = envelope.get("content_hash", "")
    envelope_json = json.dumps(envelope)
    now = _now()

    with _get_conn() as conn:
        author = _publisher_name(conn, user_uuid)
        existing = conn.execute(
            "SELECT id FROM marketplace_component_instances WHERE publisher_user_id=? AND component_id=? AND instance_id=?",
            (user_uuid, component_id, instance_id),
        ).fetchone()
        if existing:
            marketplace_id = existing["id"]
            conn.execute(
                """UPDATE marketplace_component_instances
                   SET name=?, description=?, author=?, visibility=?, envelope_json=?, content_hash=?, updated_at=?
                   WHERE id=?""",
                (name, description, author, visibility, envelope_json, content_hash, now, marketplace_id),
            )
            # Republish: reset targeted grants below to match the new list.
            conn.execute(
                "DELETE FROM marketplace_sharing_grants WHERE resource_type='component_instance' AND resource_id=?",
                (marketplace_id,),
            )
        else:
            marketplace_id = str(_uuid.uuid4())
            conn.execute(
                """INSERT INTO marketplace_component_instances
                   (id, component_id, instance_id, name, description, author, publisher_user_id,
                    visibility, envelope_json, content_hash, published_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (marketplace_id, component_id, instance_id, name, description, author, user_uuid,
                 visibility, envelope_json, content_hash, now, now),
            )
        if visibility == "targeted":
            for uid in user_ids:
                valid = conn.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (uid,)).fetchone()
                if valid:
                    conn.execute(
                        "INSERT OR IGNORE INTO marketplace_sharing_grants "
                        "(id, resource_type, resource_id, grantor_user_id, grantee_user_id, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (str(_uuid.uuid4()), "component_instance", marketplace_id, user_uuid, uid, now),
                    )
        conn.commit()

    return jsonify({"status": "success", "marketplace_id": marketplace_id, "visibility": visibility})


# ============================================================================
# MY ASSETS  (the user's publishable instances + their listing status)
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/mine", methods=["GET"])
async def my_component_instances():
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    mgr = get_component_manager()
    items = []
    with _get_conn() as conn:
        listed = {}
        for r in conn.execute(
            "SELECT id, component_id, instance_id, visibility FROM marketplace_component_instances WHERE publisher_user_id=?",
            (user_uuid,),
        ).fetchall():
            listed[(r["component_id"], r["instance_id"])] = {"marketplace_id": r["id"], "visibility": r["visibility"]}

        for comp in (mgr.get_all_components() if mgr else []):
            handler = getattr(comp, "handler", None)
            if not handler or not getattr(handler, "supports_instance_io", False):
                continue
            cid = comp.component_id
            # Only surface instances of components ACTIVATED for this user.
            if not is_component_available(cid, user_uuid):
                continue
            meta = _component_meta(cid)
            try:
                instances = await handler.list_instances({"user_uuid": user_uuid})
            except Exception as e:
                app_logger.warning(f"[CI-Marketplace] list_instances failed for {cid}: {e}")
                instances = []
            for inst in instances:
                key = (cid, inst.get("id"))
                lst = listed.get(key)
                items.append({
                    "component_id": cid,
                    "component_name": meta["display_name"],
                    "icon_svg": meta["icon_svg"],
                    "icon_accent": meta["icon_accent"],
                    "instance_id": inst.get("id"),
                    "name": inst.get("name"),
                    "updated_at": inst.get("updated_at"),
                    "is_marketplace_listed": lst is not None,
                    "marketplace_id": lst["marketplace_id"] if lst else None,
                    "marketplace_visibility": lst["visibility"] if lst else None,
                })
    return jsonify({"status": "success", "instances": items})


# ============================================================================
# BROWSE
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances", methods=["GET"])
async def browse_component_instances():
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    page = max(1, int(request.args.get("page", 1)))
    per_page = min(50, max(1, int(request.args.get("per_page", 12))))
    search = (request.args.get("search") or "").strip()
    sort_by = request.args.get("sort_by", "recent")
    visibility = request.args.get("visibility", "public")
    component_filter = request.args.get("component_id")

    where = []
    params = []
    # Visibility scoping: public, or targeted-to-me, or my own.
    if visibility == "public":
        where.append("(m.visibility='public' OR m.publisher_user_id=? OR m.id IN "
                     "(SELECT resource_id FROM marketplace_sharing_grants WHERE resource_type='component_instance' AND grantee_user_id=?))")
        params += [user_uuid, user_uuid]
    else:  # 'all' / 'targeted' — only what I can see
        where.append("(m.visibility='public' OR m.publisher_user_id=? OR m.id IN "
                     "(SELECT resource_id FROM marketplace_sharing_grants WHERE resource_type='component_instance' AND grantee_user_id=?))")
        params += [user_uuid, user_uuid]
    if search:
        where.append("(m.name LIKE ? OR m.description LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if component_filter:
        where.append("m.component_id=?")
        params.append(component_filter)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order = {
        "installs": "m.install_count DESC",
        "recent": "m.published_at DESC",
        "name": "m.name ASC",
    }.get(sort_by, "m.published_at DESC")

    with _get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM marketplace_component_instances m{where_sql}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT m.* FROM marketplace_component_instances m{where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()
        results = []
        for r in rows:
            avg, cnt = _ratings(conn, r["id"])
            meta = _component_meta(r["component_id"])
            sub = _subscription_state(conn, r["id"], user_uuid, r["content_hash"])
            results.append({
                "id": r["id"],
                "component_id": r["component_id"],
                "component_name": meta["display_name"],
                "icon_svg": meta["icon_svg"],
                "icon_accent": meta["icon_accent"],
                "name": r["name"],
                "description": r["description"],
                "author": r["author"],
                "visibility": r["visibility"],
                "install_count": r["install_count"],
                "subscriber_count": r["subscriber_count"],
                "average_rating": avg,
                "rating_count": cnt,
                "published_at": r["published_at"],
                "is_publisher": r["publisher_user_id"] == user_uuid,
                "is_installed": _is_installed(conn, r["id"], user_uuid),
                "is_subscribed": sub["is_subscribed"],
                "is_sync_available": sub["is_sync_available"],
                "component_available": is_component_available(r["component_id"], user_uuid),
            })

    total_pages = max(1, (total + per_page - 1) // per_page)
    return jsonify({"status": "success", "instances": results, "page": page, "total_pages": total_pages, "total": total})


# ============================================================================
# DETAIL
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>", methods=["GET"])
async def get_component_instance_detail(marketplace_id: str):
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    with _get_conn() as conn:
        r = conn.execute("SELECT * FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r:
            return jsonify({"status": "error", "message": "Not found"}), 404
        if r["visibility"] == "targeted" and r["publisher_user_id"] != user_uuid and not _has_targeted_access(conn, marketplace_id, user_uuid):
            return jsonify({"status": "error", "message": "Not found"}), 404
        avg, cnt = _ratings(conn, marketplace_id)
        my = conn.execute(
            "SELECT rating, comment FROM component_instance_ratings WHERE instance_marketplace_id=? AND user_id=?",
            (marketplace_id, user_uuid),
        ).fetchone()
        meta = _component_meta(r["component_id"])
        return jsonify({
            "status": "success",
            "id": r["id"], "component_id": r["component_id"], "component_name": meta["display_name"],
            "name": r["name"], "description": r["description"], "author": r["author"],
            "visibility": r["visibility"], "install_count": r["install_count"],
            "average_rating": avg, "rating_count": cnt,
            "is_publisher": r["publisher_user_id"] == user_uuid,
            "component_available": is_component_available(r["component_id"], user_uuid),
            "my_rating": (my["rating"] if my else None),
            "my_comment": (my["comment"] if my else None),
        })


# ============================================================================
# INSTALL / FORK  (import_instance: dry_run preflight -> commit with mapping)
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>/install", methods=["POST"])
async def install_component_instance(marketplace_id: str):
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    body = await request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run"))
    mapping = body.get("mapping")

    with _get_conn() as conn:
        r = conn.execute("SELECT * FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r:
            return jsonify({"status": "error", "message": "Not found"}), 404
        if r["visibility"] == "targeted" and r["publisher_user_id"] != user_uuid and not _has_targeted_access(conn, marketplace_id, user_uuid):
            return jsonify({"status": "error", "message": "Not found"}), 404
        component_id = r["component_id"]
        envelope = json.loads(r["envelope_json"])

    # Optional rename from the remap dialog (commit only).
    if body.get("name"):
        envelope = {**envelope, "name": body["name"]}

    # Activation gate — can only fork instances of a component activated for you.
    if not is_component_available(component_id, user_uuid):
        meta = _component_meta(component_id)
        return jsonify({"status": "error", "message": f"Enable the {meta['display_name']} component to fork this", "needs_component": component_id}), 403
    handler = _handler_for(component_id)
    if not handler:
        return jsonify({"status": "error", "message": f"'{component_id}' does not support instance import"}), 400

    try:
        result = await handler.import_instance(
            envelope, {"user_uuid": user_uuid}, mode="create", mapping=mapping, dry_run=dry_run
        )
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        app_logger.error(f"[CI-Marketplace] fork failed {marketplace_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Fork failed"}), 500

    if dry_run:
        return jsonify(result)

    # Commit: record the install (rating gate) + bump count.
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO marketplace_installs (id, artifact_type, marketplace_id, user_id, installed_at) VALUES (?,?,?,?,?)",
            (str(_uuid.uuid4()), "component_instance", marketplace_id, user_uuid, _now()),
        )
        conn.execute("UPDATE marketplace_component_instances SET install_count = install_count + 1 WHERE id=?", (marketplace_id,))
        conn.commit()
    # Trust Membrane portability: reinstate the attestation on the forked instance.
    try:
        from trusted_data_agent.core.trust import portability as _trust_portability
        _new_id = result.get("target_id") or result.get("instance_id") or result.get("id")
        if _new_id:
            _trust_portability.reinstate_kind(component_id, str(_new_id), (envelope or {}).get("trust"))
    except Exception:
        pass
    return jsonify(result)


# ============================================================================
# SUBSCRIBE / SYNC / UNSUBSCRIBE  (Phase B — live-tracking)
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>/subscribe", methods=["POST"])
async def subscribe_component_instance(marketplace_id: str):
    """Subscribe = materialize a local copy (import_instance create + remap) and link
    it, capturing the mapping for later in-place Sync. Two-phase like fork."""
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    body = await request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run"))
    mapping = body.get("mapping")

    with _get_conn() as conn:
        r = conn.execute("SELECT * FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r:
            return jsonify({"status": "error", "message": "Not found"}), 404
        if r["visibility"] == "targeted" and r["publisher_user_id"] != user_uuid and not _has_targeted_access(conn, marketplace_id, user_uuid):
            return jsonify({"status": "error", "message": "Not found"}), 404
        component_id = r["component_id"]
        content_hash = r["content_hash"]
        envelope = json.loads(r["envelope_json"])
    if body.get("name"):
        envelope = {**envelope, "name": body["name"]}

    if not is_component_available(component_id, user_uuid):
        meta = _component_meta(component_id)
        return jsonify({"status": "error", "message": f"Enable the {meta['display_name']} component to subscribe", "needs_component": component_id}), 403
    handler = _handler_for(component_id)
    if not handler:
        return jsonify({"status": "error", "message": f"'{component_id}' does not support instance import"}), 400

    try:
        result = await handler.import_instance(envelope, {"user_uuid": user_uuid}, mode="create", mapping=mapping, dry_run=dry_run)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        app_logger.error(f"[CI-Marketplace] subscribe materialize failed {marketplace_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Subscribe failed"}), 500

    if dry_run:
        return jsonify(result)

    local_id = result.get("instance_id") or result.get("flow_id") or result.get("dashboard_id")
    # Trust Membrane portability: reinstate the attestation on the subscribed instance.
    try:
        from trusted_data_agent.core.trust import portability as _trust_portability
        if local_id:
            _trust_portability.reinstate_kind(component_id, str(local_id), (envelope or {}).get("trust"))
    except Exception:
        pass
    now = _now()
    with _get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM component_instance_subscriptions WHERE marketplace_id=? AND subscriber_user_id=?",
            (marketplace_id, user_uuid),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE component_instance_subscriptions SET local_instance_id=?, synced_content_hash=?, mapping_json=?, updated_at=? WHERE id=?",
                (local_id, content_hash, json.dumps(mapping or {}), now, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO component_instance_subscriptions "
                "(id, marketplace_id, subscriber_user_id, component_id, local_instance_id, synced_content_hash, mapping_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), marketplace_id, user_uuid, component_id, local_id, content_hash, json.dumps(mapping or {}), now, now),
            )
            conn.execute("UPDATE marketplace_component_instances SET subscriber_count = subscriber_count + 1 WHERE id=?", (marketplace_id,))
        # Record install so the subscriber can also rate.
        conn.execute(
            "INSERT OR IGNORE INTO marketplace_installs (id, artifact_type, marketplace_id, user_id, installed_at) VALUES (?,?,?,?,?)",
            (str(_uuid.uuid4()), "component_instance", marketplace_id, user_uuid, now),
        )
        conn.commit()
    return jsonify({**result, "subscribed": True})


@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>/sync", methods=["POST"])
async def sync_component_instance(marketplace_id: str):
    """Manual Sync — re-materialize the subscriber's local copy IN PLACE
    (import_instance mode=update, target_id) using the mapping captured at subscribe."""
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    with _get_conn() as conn:
        sub = conn.execute(
            "SELECT * FROM component_instance_subscriptions WHERE marketplace_id=? AND subscriber_user_id=?",
            (marketplace_id, user_uuid),
        ).fetchone()
        if not sub:
            return jsonify({"status": "error", "message": "Not subscribed"}), 404
        r = conn.execute("SELECT * FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r:
            return jsonify({"status": "error", "message": "Listing no longer exists"}), 404
        component_id = sub["component_id"]
        local_id = sub["local_instance_id"]
        mapping = json.loads(sub["mapping_json"] or "{}")
        content_hash = r["content_hash"]
        envelope = json.loads(r["envelope_json"])

    if content_hash and content_hash == sub["synced_content_hash"]:
        return jsonify({"status": "success", "up_to_date": True})
    if not is_component_available(component_id, user_uuid):
        return jsonify({"status": "error", "message": "Component not enabled"}), 403
    handler = _handler_for(component_id)
    if not handler:
        return jsonify({"status": "error", "message": "Component cannot import"}), 400

    try:
        result = await handler.import_instance(
            envelope, {"user_uuid": user_uuid}, mode="update", target_id=local_id, mapping=mapping, dry_run=False
        )
    except ValueError as e:
        # Local copy was deleted → drop the stale subscription.
        with _get_conn() as conn:
            conn.execute("DELETE FROM component_instance_subscriptions WHERE marketplace_id=? AND subscriber_user_id=?", (marketplace_id, user_uuid))
            conn.commit()
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        app_logger.error(f"[CI-Marketplace] sync failed {marketplace_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Sync failed"}), 500

    with _get_conn() as conn:
        conn.execute("UPDATE component_instance_subscriptions SET synced_content_hash=?, updated_at=? WHERE marketplace_id=? AND subscriber_user_id=?",
                     (content_hash, _now(), marketplace_id, user_uuid))
        conn.commit()
    return jsonify({**result, "synced": True})


@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>/subscribe", methods=["DELETE"])
async def unsubscribe_component_instance(marketplace_id: str):
    """Unsubscribe — drop the live link; the local copy stays as an independent instance."""
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    with _get_conn() as conn:
        sub = conn.execute(
            "SELECT id FROM component_instance_subscriptions WHERE marketplace_id=? AND subscriber_user_id=?",
            (marketplace_id, user_uuid),
        ).fetchone()
        if not sub:
            return jsonify({"status": "error", "message": "Not subscribed"}), 404
        conn.execute("DELETE FROM component_instance_subscriptions WHERE id=?", (sub["id"],))
        conn.execute("UPDATE marketplace_component_instances SET subscriber_count = MAX(0, subscriber_count - 1) WHERE id=?", (marketplace_id,))
        conn.commit()
    return jsonify({"status": "success"})


# ============================================================================
# RATE
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>/rate", methods=["POST"])
async def rate_component_instance(marketplace_id: str):
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    body = await request.get_json(silent=True) or {}
    try:
        rating = int(body.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "rating must be an integer 1-5"}), 400
    if not (1 <= rating <= 5):
        return jsonify({"status": "error", "message": "rating must be 1-5"}), 400
    comment = body.get("comment") or ""

    with _get_conn() as conn:
        r = conn.execute("SELECT publisher_user_id FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r:
            return jsonify({"status": "error", "message": "Not found"}), 404
        if r["publisher_user_id"] == user_uuid:
            return jsonify({"status": "error", "message": "You cannot rate your own listing"}), 400
        if not _is_installed(conn, marketplace_id, user_uuid):
            return jsonify({"status": "error", "message": "Fork this before rating it"}), 403
        existing = conn.execute(
            "SELECT id FROM component_instance_ratings WHERE instance_marketplace_id=? AND user_id=?",
            (marketplace_id, user_uuid),
        ).fetchone()
        now = _now()
        if existing:
            conn.execute("UPDATE component_instance_ratings SET rating=?, comment=?, updated_at=? WHERE id=?",
                         (rating, comment, now, existing["id"]))
            code = 200
        else:
            conn.execute("INSERT INTO component_instance_ratings (id, instance_marketplace_id, user_id, rating, comment, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                         (str(_uuid.uuid4()), marketplace_id, user_uuid, rating, comment, now, now))
            code = 201
        conn.commit()
    return jsonify({"status": "success"}), code


# ============================================================================
# UNPUBLISH
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>", methods=["DELETE"])
async def unpublish_component_instance(marketplace_id: str):
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    with _get_conn() as conn:
        r = conn.execute("SELECT publisher_user_id FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r:
            return jsonify({"status": "error", "message": "Not found"}), 404
        if r["publisher_user_id"] != user_uuid:
            return jsonify({"status": "error", "message": "Only the publisher can unpublish"}), 403
        conn.execute("DELETE FROM component_instance_ratings WHERE instance_marketplace_id=?", (marketplace_id,))
        conn.execute("DELETE FROM marketplace_sharing_grants WHERE resource_type='component_instance' AND resource_id=?", (marketplace_id,))
        conn.execute("DELETE FROM marketplace_installs WHERE artifact_type='component_instance' AND marketplace_id=?", (marketplace_id,))
        # Drop subscribers' live links (their materialized local copies stay — they
        # just stop tracking). Without this, subscribers dangle with is_subscribed=true
        # and a Sync that 404s.
        conn.execute("DELETE FROM component_instance_subscriptions WHERE marketplace_id=?", (marketplace_id,))
        conn.execute("DELETE FROM marketplace_component_instances WHERE id=?", (marketplace_id,))
        conn.commit()
    return jsonify({"status": "success"})


# ============================================================================
# TARGETED USERS (manage visibility grants)
# ============================================================================

@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>/targeted-users", methods=["GET"])
async def get_ci_targeted_users(marketplace_id: str):
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    with _get_conn() as conn:
        r = conn.execute("SELECT publisher_user_id FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r or r["publisher_user_id"] != user_uuid:
            return jsonify({"status": "error", "message": "Not found"}), 404
        rows = conn.execute(
            "SELECT g.grantee_user_id, u.username, u.email FROM marketplace_sharing_grants g "
            "LEFT JOIN users u ON u.id=g.grantee_user_id "
            "WHERE g.resource_type='component_instance' AND g.resource_id=?",
            (marketplace_id,),
        ).fetchall()
        users = [{"id": x["grantee_user_id"], "username": x["username"], "email": x["email"]} for x in rows]
    return jsonify({"status": "success", "users": users})


@component_instance_marketplace_bp.route("/v1/marketplace/component-instances/<marketplace_id>/targeted-users", methods=["PUT"])
async def update_ci_targeted_users(marketplace_id: str):
    user_uuid = _user_uuid()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    body = await request.get_json(silent=True) or {}
    new_ids = set(body.get("user_ids") or [])
    now = _now()
    with _get_conn() as conn:
        r = conn.execute("SELECT publisher_user_id FROM marketplace_component_instances WHERE id=?", (marketplace_id,)).fetchone()
        if not r or r["publisher_user_id"] != user_uuid:
            return jsonify({"status": "error", "message": "Not found"}), 404
        current = {x["grantee_user_id"] for x in conn.execute(
            "SELECT grantee_user_id FROM marketplace_sharing_grants WHERE resource_type='component_instance' AND resource_id=?",
            (marketplace_id,),
        ).fetchall()}
        for uid in current - new_ids:
            conn.execute("DELETE FROM marketplace_sharing_grants WHERE resource_type='component_instance' AND resource_id=? AND grantee_user_id=?",
                         (marketplace_id, uid))
        for uid in new_ids - current:
            valid = conn.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (uid,)).fetchone()
            if valid:
                conn.execute("INSERT OR IGNORE INTO marketplace_sharing_grants (id, resource_type, resource_id, grantor_user_id, grantee_user_id, created_at) VALUES (?,?,?,?,?,?)",
                             (str(_uuid.uuid4()), "component_instance", marketplace_id, user_uuid, uid, now))
        conn.commit()
    return jsonify({"status": "success"})
