"""
Database Navigator routes — the panel's face of the navigator.

  GET  /api/v1/database-navigator/sources     ?profile_id=            the sources this agent can read + the resolved default
  GET  /api/v1/database-navigator/databases   ?source=&refresh=       databases (system ones flagged)
  GET  /api/v1/database-navigator/objects     ?source=&database=      objects of a database + a deterministic summary
  GET  /api/v1/database-navigator/object      ?source=&database=&name=&profile_id=   one object: dictionary + intelligence + ontology
  GET  /api/v1/database-navigator/search      ?source=&q=&database=   ranked hits across databases, objects and columns
  POST /api/v1/database-navigator/preview     {source, database, name, limit, unmask}   sample rows (PII masked by default)
  POST /api/v1/database-navigator/count       {source, database, name}                  exact COUNT(*)
  POST /api/v1/database-navigator/refresh     {source?}                                 drop the cached catalog

Every endpoint is gated on the component being available to the caller (the same
admin + per-user governance every component honours) and answers only from the
caller's OWN connections or the caller's active MCP session — the cache is keyed by
user, so no user ever sees another's tree. All reads route through ONE
``NavigatorService``, shared with the ``TDA_DatabaseNavigator`` tool.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from quart import Blueprint, jsonify, request

from trusted_data_agent.auth.middleware import require_auth

logger = logging.getLogger("quart.app")

dbnav_bp = Blueprint("database_navigator", __name__)
COMPONENT_ID = "database_navigator"


def _gate(current_user) -> Optional[Tuple[Any, int]]:
    """403 unless the Database Navigator component is available to this user."""
    try:
        from trusted_data_agent.components.settings import is_component_available
        uid = _uid(current_user) or None
        if not is_component_available(COMPONENT_ID, uid):
            return jsonify({"status": "error", "code": "component_disabled",
                            "message": "The Database Navigator component is not enabled for you."}), 403
    except Exception as exc:  # noqa: BLE001 — a broken gate must fail CLOSED
        logger.warning("[dbnav] availability check failed: %s", exc)
        return jsonify({"status": "error", "code": "component_gate_error",
                        "message": "Could not verify component availability."}), 403
    return None


def _uid(current_user) -> str:
    return str(getattr(current_user, "id", None) or (current_user or {}).get("id") or "")


def _profile(uid: str, profile_id: Optional[str]) -> Dict[str, Any]:
    if not (uid and profile_id):
        return {}
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        return get_config_manager().get_profile(profile_id, uid) or {}
    except Exception:  # noqa: BLE001
        return {}


async def _mcp_context(uid: str, profile_id: Optional[str], profile: Dict[str, Any]):
    """(available_tools, invoke, label, server_id) for the caller's ACTIVE MCP catalog —
    argument names read LIVE from the server (the classification snapshot can be stale)."""
    try:
        from trusted_data_agent.core.config import APP_STATE
        from trusted_data_agent.dbcatalog.mcp_provider import (available_tools_from_state, available_tools_live,
                                                             reconcile_state_schema)
        from trusted_data_agent.mcp_adapter.adapter import invoke_mcp_tool
        server_id = str(profile.get("mcpServerId") or "") or None
        try:
            tools = await available_tools_live(APP_STATE, server_id, uid)
        except Exception:  # noqa: BLE001
            tools = available_tools_from_state(APP_STATE)
        for t in tools:
            if t.get("live"):
                reconcile_state_schema(APP_STATE, t["name"], t["args"])

        async def _invoke(tool, args):
            res, _i, _o = await invoke_mcp_tool(APP_STATE, {"tool_name": tool, "arguments": args},
                                                user_uuid=uid, session_id=None, profile_id=profile_id)
            return res
        return tools, _invoke, str(profile.get("mcpServerName") or "MCP data source"), str(profile.get("mcpServerId") or "active")
    except Exception:  # noqa: BLE001
        return [], None, "MCP data source", "active"


async def _provider(uid: str, source_id: str, profile_id: Optional[str]):
    from trusted_data_agent.dbcatalog.service import get_navigator_service
    svc = get_navigator_service()
    prof = _profile(uid, profile_id)
    tools, invoke, mlabel, _mid = await _mcp_context(uid, profile_id, prof)
    return svc, svc.provider(uid, source_id, mcp_invoke=invoke, available_tools=tools, mcp_label=mlabel)


def _err(exc: Exception, status: int = 400):
    from trusted_data_agent.dbcatalog.base import CatalogError
    if isinstance(exc, CatalogError):
        return jsonify({"status": "error", "code": "catalog", "message": str(exc)}), status
    logger.error("[dbnav] %s", exc, exc_info=True)
    return jsonify({"status": "error", "code": "internal", "message": str(exc)[:300]}), 500


@dbnav_bp.route("/v1/database-navigator/sources", methods=["GET"])
@require_auth
async def dbnav_sources(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    profile_id = (request.args.get("profile_id") or "").strip() or None
    prof = _profile(uid, profile_id)
    from trusted_data_agent.dbcatalog.service import get_navigator_service
    tools, _invoke, mlabel, mid = await _mcp_context(uid, profile_id, prof)
    res = get_navigator_service().sources(uid, prof, tools, mlabel, mid)
    res["status"] = "success"
    res["profile_id"] = profile_id
    return jsonify(res), 200


@dbnav_bp.route("/v1/database-navigator/databases", methods=["GET"])
@require_auth
async def dbnav_databases(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    source = (request.args.get("source") or "").strip()
    if not source:
        return jsonify({"status": "error", "message": "source is required"}), 400
    try:
        svc, prov = await _provider(uid, source, request.args.get("profile_id"))
        dbs = await svc.databases(uid, prov, refresh=request.args.get("refresh") in ("1", "true"))
        return jsonify({"status": "success", "source": {"id": prov.source_id, "kind": prov.provider_kind, "label": prov.label,
                                                        "capabilities": sorted(c.value for c in prov.capabilities())},
                        "databases": [d.to_dict() for d in dbs],
                        "summary": {"count": len(dbs), "system": sum(1 for d in dbs if d.is_system)}}), 200
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@dbnav_bp.route("/v1/database-navigator/objects", methods=["GET"])
@require_auth
async def dbnav_objects(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    source, database = (request.args.get("source") or "").strip(), (request.args.get("database") or "").strip()
    if not (source and database):
        return jsonify({"status": "error", "message": "source and database are required"}), 400
    try:
        from trusted_data_agent.dbcatalog import intelligence as I
        svc, prov = await _provider(uid, source, request.args.get("profile_id"))
        objs = await svc.objects(uid, prov, database, refresh=request.args.get("refresh") in ("1", "true"))
        return jsonify({"status": "success", "database": database, "objects": [o.to_dict() for o in objs],
                        "summary": I.summarize_objects(objs)}), 200
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@dbnav_bp.route("/v1/database-navigator/object", methods=["GET"])
@require_auth
async def dbnav_object(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    source = (request.args.get("source") or "").strip()
    database, name = (request.args.get("database") or "").strip(), (request.args.get("name") or "").strip()
    if not (source and database and name):
        return jsonify({"status": "error", "message": "source, database and name are required"}), 400
    try:
        profile_id = (request.args.get("profile_id") or "").strip() or None
        svc, prov = await _provider(uid, source, profile_id)
        detail = await svc.describe(uid, prov, database, name, profile_id=profile_id,
                                    refresh=request.args.get("refresh") in ("1", "true"))
        out = detail.to_dict()
        out["status"] = "success"
        return jsonify(out), 200
    except Exception as exc:  # noqa: BLE001
        return _err(exc, 404 if "not in the" in str(exc) else 400)


@dbnav_bp.route("/v1/database-navigator/search", methods=["GET"])
@require_auth
async def dbnav_search(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    source, q = (request.args.get("source") or "").strip(), (request.args.get("q") or "").strip()
    if not source:
        return jsonify({"status": "error", "message": "source is required"}), 400
    try:
        svc, prov = await _provider(uid, source, request.args.get("profile_id"))
        r = await svc.search(uid, prov, q, database=(request.args.get("database") or "").strip() or None,
                             limit=max(1, min(int(request.args.get("limit") or 40), 200)),
                             include_system=request.args.get("system") in ("1", "true"))
        r["status"] = "success"
        return jsonify(r), 200
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@dbnav_bp.route("/v1/database-navigator/preview", methods=["POST"])
@require_auth
async def dbnav_preview(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    data = await request.get_json(force=True, silent=True) or {}
    source, database, name = (data.get("source") or "").strip(), (data.get("database") or "").strip(), (data.get("name") or "").strip()
    if not (source and database and name):
        return jsonify({"status": "error", "message": "source, database and name are required"}), 400
    try:
        profile_id = (data.get("profile_id") or "").strip() or None
        svc, prov = await _provider(uid, source, profile_id)
        pv = await svc.preview(uid, prov, database, name, int(data.get("limit") or 50),
                               unmask=bool(data.get("unmask")), profile_id=profile_id)
        out = pv.to_dict()
        out["status"] = "success"
        return jsonify(out), 200
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@dbnav_bp.route("/v1/database-navigator/count", methods=["POST"])
@require_auth
async def dbnav_count(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    data = await request.get_json(force=True, silent=True) or {}
    source, database, name = (data.get("source") or "").strip(), (data.get("database") or "").strip(), (data.get("name") or "").strip()
    if not (source and database and name):
        return jsonify({"status": "error", "message": "source, database and name are required"}), 400
    try:
        svc, prov = await _provider(uid, source, (data.get("profile_id") or "").strip() or None)
        out = await svc.count(uid, prov, database, name)
        out["status"] = "success"
        return jsonify(out), 200
    except Exception as exc:  # noqa: BLE001
        return _err(exc)


@dbnav_bp.route("/v1/database-navigator/refresh", methods=["POST"])
@require_auth
async def dbnav_refresh(current_user):
    gate = _gate(current_user)
    if gate:
        return gate
    uid = _uid(current_user)
    data = await request.get_json(force=True, silent=True) or {}
    from trusted_data_agent.dbcatalog.service import get_navigator_service
    n = get_navigator_service().refresh(uid, (data.get("source") or "").strip() or None)
    return jsonify({"status": "success", "dropped": n}), 200
