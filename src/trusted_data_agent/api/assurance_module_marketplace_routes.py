"""
PROP-003 · Phase 3 — the assurance-module marketplace.

    publish     an installed, user-created pack becomes a listing (+ its trust envelope)
    browse      public listings, plus the ones shared with you
    install     the pack is materialized into ~/.tda/assurance_modules/ and the
                publisher's signature is REINSTATED only if the copy is byte-identical
    rate        gated behind a real install (the shared marketplace_installs table)

The contract is the extension plane's, verbatim, because the sharing semantics are the
same. What differs is what an install BUYS: availability, never authority. A pack arrives
bound to nothing, and `conformance.check_binding` still refuses it until it is signed
HERE — a publisher's signature says "this is what I published", not "this deployment
trusts it in its answer path". That distinction is the reason a third-party assurance
marketplace is safe to offer at all.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from quart import Blueprint, jsonify, request

from trusted_data_agent.auth.middleware import require_auth
from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

assurance_module_marketplace_bp = Blueprint("assurance_module_marketplace", __name__)

_SCHEMA = "43_marketplace_assurance_modules.sql"
_BLOBS = Path("marketplace_data") / "assurance_modules"


def _db_path() -> str:
    from trusted_data_agent.core.config import APP_CONFIG
    return (APP_CONFIG.AUTH_DB_PATH or "tda_auth.db").replace("sqlite:///", "")


def _conn():
    c = sqlite_util.connect(_db_path())
    return c


def _ensure_schema():
    """Self-apply on import (idempotent) — zero deployment steps, the house pattern."""
    try:
        p = Path(__file__).resolve().parents[3] / "schema" / _SCHEMA
        if not p.exists():
            return
        conn = sqlite_util.connect(_db_path())
        conn.executescript(p.read_text())
        conn.commit()
        conn.close()
    except Exception as e:  # pragma: no cover — never block import
        logger.warning(f"[assurance-marketplace] schema ensure failed: {e}")


_ensure_schema()


def _user_pack_dir(pack_id: str) -> Path:
    return Path.home() / ".tda" / "assurance_modules" / pack_id


def _read_pack(pack_id: str):
    """The pack's payload as published: its manifest plus every source file."""
    d = _user_pack_dir(pack_id)
    mf = d / "manifest.json"
    if not mf.exists():
        return None, None
    payload = {"manifest": json.loads(mf.read_text()),
               "sources": {p.name: p.read_text() for p in sorted(d.glob("*.py"))}}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return payload, hashlib.sha256(blob.encode()).hexdigest()


def _gate():
    """The marketplace is never more permissive than the plane it feeds."""
    from trusted_data_agent.assurance_modules import settings as _ms
    return _ms.is_marketplace_enabled()


def _rows(sql, args=()):
    conn = _conn()
    try:
        cur = conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


@assurance_module_marketplace_bp.route(
    "/v1/assurance-modules/<pack_id>/publish", methods=["POST"])
@require_auth
async def publish(current_user, pack_id):
    if not _gate():
        return jsonify({"error": "the assurance-module marketplace is disabled"}), 403
    try:
        data = await request.get_json() or {}
    except Exception:  # noqa: BLE001
        data = {}
    visibility = str(data.get("visibility") or "public")
    if visibility not in ("public", "targeted"):
        return jsonify({"error": "visibility must be public or targeted"}), 400
    user_ids = data.get("user_ids") or []
    if visibility == "targeted" and not user_ids:
        return jsonify({"error": "targeted visibility needs at least one recipient"}), 400

    payload, src_hash = _read_pack(pack_id)
    if payload is None:
        # Only a USER pack publishes: a builtin belongs to the platform, and re-publishing
        # one would put a second, forkable copy of platform machinery in circulation.
        return jsonify({"error": f"'{pack_id}' is not an installed user pack"}), 404

    existing = _rows("SELECT id FROM marketplace_assurance_modules WHERE pack_id = ? "
                     "AND publisher_user_id = ?", (pack_id, current_user.id))
    if existing:
        return jsonify({"error": "already published", "id": existing[0]["id"]}), 409

    manifest = dict(payload["manifest"])
    # Carry the publisher's signature so a subscriber can see WHO signed it. It is a
    # provenance claim, never an authorization: the installing deployment still signs
    # for itself before the pack can be bound.
    try:
        from trusted_data_agent.core.trust import portability as _tp
        claim = _tp.capture("assurance_module", pack_id)
        if claim:
            manifest["trust"] = claim
    except Exception:  # noqa: BLE001 — the trust suite is removable
        pass

    mid = str(uuid.uuid4())
    tiers = sorted({str(a.get("tier") or "declarative")
                    for a in (manifest.get("assessors") or [])})
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO marketplace_assurance_modules (id, pack_id, name, description, "
            "version, author, tiers, property_count, assessor_count, publisher_user_id, "
            "visibility, manifest_json, source_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, pack_id, manifest.get("display_name") or pack_id,
             manifest.get("description") or "", manifest.get("version") or "0.0.0",
             manifest.get("author") or "", json.dumps(tiers),
             len(manifest.get("properties") or []),
             len(manifest.get("assessors") or []),
             current_user.id, visibility, json.dumps(manifest), src_hash))
        if visibility == "targeted":
            for uid in user_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO marketplace_sharing_grants "
                    "(resource_type, resource_id, grantor_user_id, grantee_user_id) "
                    "VALUES ('assurance_module', ?, ?, ?)", (mid, current_user.id, uid))
        conn.commit()
    finally:
        conn.close()

    d = _BLOBS / mid
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.json").write_text(json.dumps(payload))
    return jsonify({"published": True, "id": mid, "tiers": tiers}), 200


@assurance_module_marketplace_bp.route("/v1/marketplace/assurance-modules",
                                       methods=["GET"])
@require_auth
async def browse(current_user):
    if not _gate():
        return jsonify({"modules": [], "disabled": True}), 200
    rows = _rows(
        "SELECT m.* FROM marketplace_assurance_modules m WHERE m.visibility = 'public' "
        "OR m.publisher_user_id = ? OR EXISTS (SELECT 1 FROM marketplace_sharing_grants g "
        "WHERE g.resource_type = 'assurance_module' AND g.resource_id = m.id "
        "AND g.grantee_user_id = ?) ORDER BY m.published_at DESC",
        (current_user.id, current_user.id))
    out = []
    for r in rows:
        manifest = {}
        try:
            manifest = json.loads(r.get("manifest_json") or "{}")
        except Exception:  # noqa: BLE001
            pass
        claim = manifest.get("trust") or {}
        out.append({
            "id": r["id"], "pack_id": r["pack_id"], "name": r["name"],
            "description": r["description"], "version": r["version"],
            "author": r["author"], "tiers": json.loads(r.get("tiers") or "[]"),
            "property_count": r["property_count"],
            "assessor_count": r["assessor_count"],
            "install_count": r["install_count"],
            "is_mine": r["publisher_user_id"] == current_user.id,
            # The publisher's own seal, read-only. Says who signed what was published —
            # not that this deployment may bind it.
            "publisher_signed": bool(claim.get("signed")),
        })
    return jsonify({"modules": out}), 200


@assurance_module_marketplace_bp.route(
    "/v1/marketplace/assurance-modules/<mid>/install", methods=["POST"])
@require_auth
async def install(current_user, mid):
    if not _gate():
        return jsonify({"error": "the assurance-module marketplace is disabled"}), 403
    rows = _rows("SELECT * FROM marketplace_assurance_modules WHERE id = ?", (mid,))
    if not rows:
        return jsonify({"error": "not found"}), 404
    row = rows[0]
    if row["visibility"] == "targeted" and row["publisher_user_id"] != current_user.id:
        granted = _rows(
            "SELECT 1 FROM marketplace_sharing_grants WHERE resource_type = "
            "'assurance_module' AND resource_id = ? AND grantee_user_id = ?",
            (mid, current_user.id))
        if not granted:
            return jsonify({"error": "not found"}), 404

    blob = _BLOBS / mid / "pack.json"
    if not blob.exists():
        return jsonify({"error": "the published payload is missing"}), 410
    payload = json.loads(blob.read_text())
    check = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    if check != row["source_hash"]:
        # Integrity of the BLOB, distinct from the trust signature: this says the bytes
        # are the ones that were published, and refuses rather than installing something
        # the listing does not describe.
        return jsonify({"error": "the published payload does not match its recorded "
                                 "hash and was not installed"}), 409

    pack_id = row["pack_id"]
    target = _user_pack_dir(pack_id)
    if target.exists():
        return jsonify({"error": f"a pack named '{pack_id}' is already installed"}), 409
    target.mkdir(parents=True, exist_ok=True)
    try:
        (target / "manifest.json").write_text(
            json.dumps(payload["manifest"], indent=2))
        for name, src in (payload.get("sources") or {}).items():
            (target / name).write_text(src)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(target, ignore_errors=True)
        return jsonify({"error": f"install failed: {e}"}), 500

    # Reinstate the publisher's signature ONLY if this copy is byte-identical to what was
    # signed (the honesty gate). It does not admit the pack into the assurance path — the
    # bind gate still requires a signature valid on THIS deployment.
    reinstated = False
    try:
        from trusted_data_agent.core.trust import portability as _tp
        reinstated = bool(_tp.reinstate_kind(
            "assurance_module", pack_id, (payload["manifest"] or {}).get("trust")))
    except Exception:  # noqa: BLE001
        pass

    conn = _conn()
    try:
        conn.execute("UPDATE marketplace_assurance_modules SET install_count = "
                     "install_count + 1 WHERE id = ?", (mid,))
        # `installed_at` is NOT NULL with no default, and `INSERT OR IGNORE` swallows a
        # constraint failure as readily as a duplicate — so omitting it wrote no row at
        # all, silently, and the install-before-rate gate could then never open. The
        # timestamp is supplied explicitly, as every other plane does.
        conn.execute("INSERT OR IGNORE INTO marketplace_installs "
                     "(id, artifact_type, marketplace_id, user_id, installed_at) "
                     "VALUES (?,?,?,?,?)",
                     (str(uuid.uuid4()), "assurance_module", mid, current_user.id,
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()

    from trusted_data_agent.assurance_modules import get_assurance_module_manager
    mgr = get_assurance_module_manager()
    mgr.unload_all()
    mgr.discover()
    loaded = pack_id in mgr.packs
    return jsonify({
        "installed": True, "pack_id": pack_id, "loaded": loaded,
        "publisher_signature_reinstated": reinstated,
        # Said plainly, because it is the thing an installer most needs to know.
        "note": ("The pack is installed and bound to nothing. Sign it on this deployment, "
                 "then assign it under Config → Trust → Assessment modules."),
    }), 200


@assurance_module_marketplace_bp.route(
    "/v1/marketplace/assurance-modules/<mid>/rate", methods=["POST"])
@require_auth
async def rate(current_user, mid):
    try:
        data = await request.get_json() or {}
    except Exception:  # noqa: BLE001
        data = {}
    try:
        rating = int(data.get("rating") or 0)
    except Exception:  # noqa: BLE001
        rating = 0
    if rating < 1 or rating > 5:
        return jsonify({"error": "rating must be 1-5"}), 400
    installed = _rows("SELECT 1 FROM marketplace_installs WHERE artifact_type = "
                      "'assurance_module' AND marketplace_id = ? AND user_id = ?",
                      (mid, current_user.id))
    if not installed:
        # The shared install-before-rate gate: an opinion about a pack nobody ran is not
        # a rating, it is a vote.
        return jsonify({"error": "install this module before rating it"}), 403
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO assurance_module_ratings (module_marketplace_id, user_id, "
            "rating, comment) VALUES (?,?,?,?) "
            "ON CONFLICT(module_marketplace_id, user_id) DO UPDATE SET "
            "rating=excluded.rating, comment=excluded.comment",
            (mid, current_user.id, rating, str(data.get("comment") or "")))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"rated": True}), 200


@assurance_module_marketplace_bp.route(
    "/v1/marketplace/assurance-modules/<mid>", methods=["DELETE"])
@require_auth
async def unpublish(current_user, mid):
    rows = _rows("SELECT publisher_user_id FROM marketplace_assurance_modules "
                 "WHERE id = ?", (mid,))
    if not rows:
        return jsonify({"error": "not found"}), 404
    if rows[0]["publisher_user_id"] != current_user.id:
        return jsonify({"error": "not yours to unpublish"}), 403
    conn = _conn()
    try:
        conn.execute("DELETE FROM marketplace_assurance_modules WHERE id = ?", (mid,))
        conn.execute("DELETE FROM marketplace_sharing_grants WHERE resource_type = "
                     "'assurance_module' AND resource_id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()
    shutil.rmtree(_BLOBS / mid, ignore_errors=True)
    return jsonify({"unpublished": True}), 200
