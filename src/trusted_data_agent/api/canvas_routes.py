"""
Canvas Routes — REST API for the persisted Canvas component (Option A).

Endpoints:
  GET    /api/v1/canvases                                List user's canvases
  POST   /api/v1/canvases                                Create / upsert (by title)
  GET    /api/v1/canvases/<id>                            Get a canvas (with versions)
  PUT    /api/v1/canvases/<id>                            Update a canvas (records a version)
  DELETE /api/v1/canvases/<id>                            Delete a canvas + versions
  GET    /api/v1/canvases/<id>/versions                  List version history
  POST   /api/v1/canvases/<id>/versions/<vid>/restore    Restore a prior version
"""

import logging
from quart import Blueprint, jsonify, request

from trusted_data_agent.api.operations import Arg, operation

from trusted_data_agent.auth.middleware import require_auth
from trusted_data_agent.core.canvas_db import (
    CanvasTitleTaken,
    create_canvas,
    get_canvas_by_title,
    delete_canvas,
    get_canvas,
    list_canvases,
    list_versions,
    restore_version,
    update_canvas,
    upsert_canvas,
)
def _assert_component_available(component_id: str, user_uuid: str):
    """403 unless the component is available to this user (global admin disable +
    per-user override). Deliberately NOT per-profile: an instance is user-owned data,
    not a property of whichever agent happens to be active.

    Applied to capability-CREATING mutations only. Reads, updates and deletes of
    already-created instances stay open so a user can always inspect and clean up their
    own data after an admin disables the component.
    Precedent: api/component_instance_marketplace_routes.py.
    """
    try:
        from trusted_data_agent.components.settings import is_component_available
        if not is_component_available(component_id, user_uuid):
            return jsonify({
                "error": f"The '{component_id}' component is disabled for your account.",
                "code": "component_not_available",
            }), 403
    except Exception:
        return None      # fail open — availability lookup must not break the endpoint
    return None

logger = logging.getLogger("quart.app")

canvas_bp = Blueprint("canvas", __name__)


@canvas_bp.route("/v1/canvases", methods=["GET"])
@operation("canvases.list", effect="read", scope="own",
           summary="The caller's canvases — documents and code kept from conversations: title, language, when changed",
           summary_fields=("canvas_id", "title", "language", "updated_at"))
@require_auth
async def list_canvases_endpoint(current_user):
    """List all canvases for the authenticated user."""
    canvases = list_canvases(current_user.id)
    return jsonify({"canvases": canvases, "count": len(canvases)})


@canvas_bp.route("/v1/canvases", methods=["POST"])
@require_auth
async def create_canvas_endpoint(current_user):
    """Create a canvas, or update in place when a canvas with the same title exists."""
    _gate = _assert_component_available("canvas", current_user.id)
    if _gate:
        return _gate
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    content = body.get("content")

    # mode "create": a new canvas by the user (the panel's "New canvas"). Never
    # upserts — a title already in use is refused, so a new canvas can never
    # overwrite an existing one — and it may start empty.
    if body.get("mode") == "create":
        title = str(body.get("title") or "").strip()
        if not title or len(title) > 200:
            return jsonify({"error": "'title' is required (at most 200 characters)"}), 400
        if content is not None and not isinstance(content, str):
            return jsonify({"error": "'content' must be text"}), 400
        if get_canvas_by_title(user_uuid, title):
            return jsonify({"error": f"A canvas named '{title}' already exists", "code": "title_taken"}), 409
        canvas = create_canvas(user_uuid, title, content or "", body.get("language"),
                               body.get("sources"), body.get("session_id"))
        canvas["versions"] = list_versions(canvas["canvas_id"], user_uuid)
        return jsonify(canvas), 201

    if not content or not isinstance(content, str) or not content.strip():
        return jsonify({"error": "'content' is required"}), 400

    result = upsert_canvas(
        user_uuid,
        title=body.get("title") or "Canvas",
        content=content,
        language=body.get("language"),
        sources=body.get("sources"),
        session_id=body.get("session_id"),
    )
    canvas = result["canvas"]
    canvas["versions"] = list_versions(canvas["canvas_id"], user_uuid)
    return jsonify(canvas), (201 if result["created"] else 200)


@canvas_bp.route("/v1/canvases/<canvas_id>", methods=["GET"])
@operation("canvases.get", effect="read", scope="own",
           summary="One canvas with its content and sources", path={"canvas_id": "the canvas (canvases.list lists them)"})
@require_auth
async def get_canvas_endpoint(current_user, canvas_id: str):
    """Get a single canvas with its version history."""
    user_uuid = current_user.id
    canvas = get_canvas(canvas_id, user_uuid)
    if not canvas:
        return jsonify({"error": "Canvas not found"}), 404
    canvas["versions"] = list_versions(canvas_id, user_uuid)
    return jsonify(canvas)


@canvas_bp.route("/v1/canvases/<canvas_id>", methods=["PUT"])
@operation("canvases.update", effect="write", scope="own",
           summary="Rename a canvas", path={"canvas_id": "the canvas (canvases.list lists them)"}, body={"title": Arg("string", "its new title", required=True)},
           notes="Its content is written with the canvas component (TDA_Canvas), not here.")
@require_auth
async def update_canvas_endpoint(current_user, canvas_id: str):
    """Update a canvas. A content change records a new version."""
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    title = body.get("title")
    if title is not None:
        title = str(title).strip()
        if not title or len(title) > 200:
            return jsonify({"error": "'title' must be 1-200 characters"}), 400
    try:
        updated = update_canvas(
            canvas_id, user_uuid,
            content=body.get("content"),
            title=title,
            language=body.get("language"),
            sources=body.get("sources"),
            record_version=body.get("record_version", True) is not False,
        )
    except CanvasTitleTaken:
        return jsonify({"error": f"A canvas named '{title}' already exists", "code": "title_taken"}), 409
    if not updated:
        return jsonify({"error": "Canvas not found"}), 404
    updated["versions"] = list_versions(canvas_id, user_uuid)
    return jsonify(updated)


@canvas_bp.route("/v1/canvases/<canvas_id>", methods=["DELETE"])
@operation("canvases.delete", effect="write", scope="own",
           summary="Delete a canvas and its versions", path={"canvas_id": "the canvas (canvases.list lists them)"})
@require_auth
async def delete_canvas_endpoint(current_user, canvas_id: str):
    """Delete a canvas and all its versions."""
    deleted = delete_canvas(canvas_id, current_user.id)
    if not deleted:
        return jsonify({"error": "Canvas not found"}), 404
    return jsonify({"deleted": True, "canvas_id": canvas_id})


@canvas_bp.route("/v1/canvases/<canvas_id>/versions", methods=["GET"])
@operation("canvases.versions", effect="read", scope="own",
           summary="A canvas's saved versions", path={"canvas_id": "the canvas (canvases.list lists them)"},
           summary_fields=("version_id", "created_at"))
@require_auth
async def list_versions_endpoint(current_user, canvas_id: str):
    """List version history for a canvas (oldest first)."""
    user_uuid = current_user.id
    if not get_canvas(canvas_id, user_uuid):
        return jsonify({"error": "Canvas not found"}), 404
    versions = list_versions(canvas_id, user_uuid)
    return jsonify({"versions": versions, "count": len(versions)})


@canvas_bp.route("/v1/canvases/<canvas_id>/versions/<version_id>/restore", methods=["POST"])
@operation("canvases.version_restore", effect="write", scope="own",
           summary="Put a saved version back as the canvas's content",
           path={"canvas_id": "the canvas (canvases.list lists them)", "version_id": "the version (canvases.versions lists them)"})
@require_auth
async def restore_version_endpoint(current_user, canvas_id: str, version_id: str):
    """Restore a canvas's content from a prior version."""
    user_uuid = current_user.id
    updated = restore_version(version_id, canvas_id, user_uuid)
    if not updated:
        return jsonify({"error": "Canvas or version not found"}), 404
    updated["versions"] = list_versions(canvas_id, user_uuid)
    return jsonify(updated)
