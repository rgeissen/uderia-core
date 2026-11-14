"""
Platform Ontology REST — the "Ontology" nav view's data endpoints (Tier 5 Step 1).

Part of the **Ontology suite** enterprise feature (bundled with the Trust
Membrane). This is an **isolated, optional blueprint**: registered only when the
enterprise gate is on AND the module is present (see
``main.py:_register_optional_enterprise_suite``). If the suite is excluded from a
build, this file is absent, the optional import fails, and the platform runs
cleanly without it. Removal manifest: ``core/trust/README.md``.

Endpoints (mounted under /api):
  GET /v1/platform-ontology/segment  — the caller's estate segment (graph-spec)
  GET /v1/platform-ontology/users    — admin scope-selector user list
"""

from __future__ import annotations

import logging

from quart import Blueprint, jsonify, request

from trusted_data_agent.auth.middleware import require_auth

app_logger = logging.getLogger("quart.app")

platform_ontology_bp = Blueprint("platform_ontology", __name__)


@platform_ontology_bp.route("/v1/platform-ontology/segment", methods=["GET"])
@require_auth
async def platform_ontology_segment(current_user):
    """Return the calling user's ontology segment as a renderer-ready graph-spec.

    A build-on-read projection over the source-of-truth tables (profiles, LLM/MCP
    configs, collections, knowledge graphs, agent memory), scoped to the user.
    Same spec shape as ``GET /v1/knowledge-graph/graph-spec`` so the existing
    renderer mounts it.
    """
    # Defense-in-depth: the blueprint is only registered when enabled, but keep
    # the per-endpoint gate so the endpoint 404s even if that ever changes.
    from trusted_data_agent.core.enterprise import is_enterprise_ontology_enabled
    if not is_enterprise_ontology_enabled():
        return jsonify({"error": "Not found"}), 404
    user_uuid = current_user.id
    try:
        import asyncio
        from trusted_data_agent.core.platform_ontology import build_segment, build_multi_user_segment, _list_users
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.auth.admin import get_user_tier

        is_admin = (get_user_tier(current_user) == "admin")
        # ?users=  →  '' / 'me' (self), 'all' (every user), or a comma list of uuids.
        # Non-admins are always scoped to themselves (server-enforced).
        scope_raw = (request.args.get("users") or "").strip()
        multi_targets = None
        if is_admin and scope_raw and scope_raw != "me":
            all_users = await asyncio.to_thread(_list_users)
            if scope_raw == "all":
                multi_targets = all_users
            else:
                wanted = {x for x in scope_raw.split(",") if x}
                multi_targets = [u for u in all_users if u["uuid"] in wanted]

        async def _sessions_for(uid):
            try:
                return ((await session_manager.get_all_sessions(uid, limit=40)) or {}).get("sessions") or []
            except Exception as se:
                app_logger.warning(f"Ontology: session fetch failed for {uid}: {se}")
                return []

        async def _packs_for(uid):
            try:
                from trusted_data_agent.core.agent_pack_manager import AgentPackManager
                from trusted_data_agent.core.utils import get_project_root
                return await AgentPackManager(db_path=str(get_project_root() / "tda_auth.db")).list_packs(uid)
            except Exception as pe:
                app_logger.warning(f"Ontology: agent-pack fetch failed for {uid}: {pe}")
                return []

        if multi_targets is not None and len(multi_targets) >= 2:
            # Multi-user platform scope → Users become the top taxonomy layer. Per-user
            # sessions/packs are omitted here to keep the aggregate bounded (one click
            # could span the whole platform); each user's structure is still complete.
            spec = await asyncio.to_thread(build_multi_user_segment, multi_targets)
        else:
            # Single scope — self, or one specific user an admin selected.
            uid = multi_targets[0]["uuid"] if (multi_targets and len(multi_targets) == 1) else user_uuid
            sessions, packs = await _sessions_for(uid), await _packs_for(uid)
            spec = await asyncio.to_thread(build_segment, uid, sessions, packs)

        return jsonify({"status": "success", "spec": spec, "is_admin": is_admin})
    except Exception as e:
        app_logger.error(f"Platform ontology segment failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@platform_ontology_bp.route("/v1/platform-ontology/users", methods=["GET"])
@require_auth
async def platform_ontology_users(current_user):
    """List platform users for the admin ontology scope selector (admin only)."""
    from trusted_data_agent.core.enterprise import is_enterprise_ontology_enabled
    if not is_enterprise_ontology_enabled():
        return jsonify({"error": "Not found"}), 404
    try:
        import asyncio
        from trusted_data_agent.auth.admin import get_user_tier
        if get_user_tier(current_user) != "admin":
            return jsonify({"status": "error", "message": "Admin only"}), 403
        from trusted_data_agent.core.platform_ontology import _list_users
        users = await asyncio.to_thread(_list_users)
        return jsonify({"status": "success", "users": users})
    except Exception as e:
        app_logger.error(f"Platform ontology users failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
