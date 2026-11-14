"""
Flow Routes — REST API for the Flow component.

Endpoints:
  GET    /api/v1/flows                     List user's flows
  POST   /api/v1/flows                     Create flow (optionally from a topology template)
  GET    /api/v1/flows/templates           Topology template catalog + default skeletons (PROP-007)
  GET    /api/v1/flows/templates/<key>     One template's live preview: skeleton + pre-fill for a source profile (PROP-007)
  GET    /api/v1/flows/<id>                Get flow
  PUT    /api/v1/flows/<id>                Update flow (full or patch)
  DELETE /api/v1/flows/<id>                Delete flow
  POST   /api/v1/flows/<id>/patch          Apply graph patch operation
  POST   /api/v1/flows/<id>/execute        Execute flow (returns run_id)
  GET    /api/v1/flows/<id>/runs           List runs
  GET    /api/v1/flows/runs/<run_id>       Get run status + events
  POST   /api/v1/flows/runs/<run_id>/cancel Cancel run
"""

import asyncio
import json
import logging
from quart import Blueprint, jsonify, request, Response

from trusted_data_agent.auth.middleware import require_auth, get_current_user
from trusted_data_agent.api.operations import Arg, operation
from trusted_data_agent.auth.security import verify_auth_token
from trusted_data_agent.core.flow_db import (
    LoopError,
    apply_patch,
    clamp_scope_rounds,
    create_flow,
    delete_flow,
    fork_flow,
    get_flow,
    get_node_runs,
    get_run,
    list_flow_variables,
    list_flows,
    replace_flow_variables,
    list_platform_flows,
    list_runs,
    list_versions,
    restore_version,
    set_flow_template_flag,
    snapshot_flow,
    update_flow,
    update_flow_metadata,
    update_run_status,
)
from trusted_data_agent.core.flow_executor import (
    FlowDefinition,
    _resolve_profile_ref,
    cancel_run as cancel_active_run,
    execute_flow,
    generate_empty_flow,
    # Per-run SSE pub/sub now lives with the run lifecycle in flow_executor so the
    # canonical emitter can feed it for EVERY run regardless of initiator. The
    # stream endpoint subscribes/replays against these shared registries.
    _run_subscribers,
    _run_event_buffer,
)
from trusted_data_agent.core.flow_layout import apply_layout
from trusted_data_agent.core.flow_topologies import (
    TEMPLATE_VERSION,
    FlowSlotUnfilledError,
    FlowTemplateUnavailable,
    catalog as topology_catalog,
    decorate_flow_record,
    default_name as default_topology_name,
    generate as generate_topology,
    preview as preview_topology,
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

flow_bp = Blueprint("flow", __name__)


from trusted_data_agent.core.flow_validation import ActionNodeError  # noqa: E402


@flow_bp.errorhandler(ActionNodeError)
async def _flow_action_node_error(exc):
    """PROP-008 §6 — an action node its agent may not perform is refused at every save path
    (create · update · patch · import) with the reason, before anything is stored."""
    return jsonify({"status": "error", "error": exc.code, "message": str(exc)}), 400


def _label_for_patch(op: str, payload: dict) -> str:
    """Derive a human-readable version label describing a patch operation.

    Auto-snapshots are taken after every structural mutation; a descriptive
    label (e.g. "Added LLM node") gives the Flow history menu meaningful
    context instead of an opaque "vN".
    """
    payload = payload or {}

    def _node_kind(node: dict) -> str:
        if not isinstance(node, dict):
            return ""
        # Prefer the user-facing label, fall back to the node type.
        return (node.get("label") or node.get("type") or "").strip()

    if op in ("add_node", "insert_node"):
        kind = _node_kind(payload.get("node", {}))
        verb = "Inserted" if op == "insert_node" else "Added"
        return f"{verb} {kind} node".replace("  ", " ").strip() if kind else f"{verb} node"
    if op == "remove_node":
        return "Removed node"
    if op == "update_node":
        return "Edited node"
    if op == "add_edge":
        return "Connected nodes"
    if op == "remove_edge":
        return "Disconnected nodes"
    if op == "create_scope":
        return "Made a loop"
    if op == "update_scope":
        up = payload.get("updates") or {}
        if set(up) == {"max_iterations"}:
            return f"Set loop to up to {clamp_scope_rounds(up['max_iterations'])} rounds"
        if "exit_when" in up:
            return "Changed when the loop stops"
        return "Edited a loop"
    if op == "delete_scope":
        return "Removed a loop"
    if op == "update_global_params":
        return "Updated parameters"
    if op == "update_settings":
        return "Changed settings"
    # Fallback: humanise the raw op name (e.g. "do_thing" → "Do thing").
    return op.replace("_", " ").capitalize() if op else "Edit"


# ---------------------------------------------------------------------------
# Flow CRUD
# ---------------------------------------------------------------------------

@flow_bp.route("/v1/flows", methods=["GET"])
@operation("flows.list", effect="read", scope="own",
           summary="The caller's flows — multi-step automations — with each one's open slots",
           query={"include_templates": Arg("boolean", "include the caller's own reusable templates (default true)")},
           summary_fields=("flow_id", "name", "description"))
@require_auth
async def list_flows_endpoint(current_user):
    """List all flows for the authenticated user."""
    user_uuid = current_user.id
    include_templates = request.args.get("include_templates", "true").lower() == "true"
    flows = list_flows(user_uuid, include_templates=include_templates)
    # PROP-007: surface template identity + the open-slot checklist on every row
    # (one resolver — the executor's — so the list and the run never disagree).
    _resolver = lambda r: _resolve_profile_ref(r, user_uuid)  # noqa: E731
    flows = [decorate_flow_record(f, _resolver) for f in flows]
    return jsonify({"flows": flows, "count": len(flows)})


@flow_bp.route("/v1/flows", methods=["POST"])
@operation("flows.create", effect="write", scope="own",
           summary="Create a flow from a topology template, its slots filled, or an empty one",
           body={"name": "its name", "description": "what it automates",
                 "template": "the template's key (flows.templates lists them); omit for an empty flow",
                 "params": Arg("object", "the template's parameters, e.g. {\"stages\": 3} — clamped to its ranges"),
                 "fills": Arg("object", "its slots, by slot id or role → an agent's @TAG, e.g. {\"analyst\": \"@SALES\"}"),
                 "source_tag": "an agent whose experts pre-fill a coordinator template (@TAG)"},
           notes="Its graph is then edited with the flow component (TDA_Flow), not here.")
@require_auth
async def create_flow_endpoint(current_user):
    """Create a new flow."""
    _gate = _assert_component_available("flow", current_user.id)
    if _gate:
        return _gate
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    description = body.get("description", "")
    template_key = body.get("template")
    fill_report = None

    if template_key and not body.get("definition"):
        # PROP-007 — create FROM a topology template: `template` (key), `params`
        # (clamped to the row's ranges), `fills` (slot node id or role → @TAG). The
        # result is a COPY stamped `definition.template`; nothing links back.
        _resolver = lambda r: _resolve_profile_ref(r, user_uuid)  # noqa: E731
        try:
            definition, fill_report = generate_topology(
                template_key, body.get("params") or {}, body.get("fills") or {}, _resolver)
        except FlowTemplateUnavailable as exc:
            return jsonify({"error": "flow_template_unavailable", "message": str(exc)}), 400
        name = body.get("name") or default_topology_name(template_key, body.get("source_tag"))
    else:
        name = body.get("name", "Untitled Flow")
        definition = body.get("definition") or generate_empty_flow()
        # Validate if definition provided
        if body.get("definition"):
            flow_def = FlowDefinition(definition)
            errors = flow_def.validate()
            if errors:
                return jsonify({"error": "Invalid flow definition", "details": errors}), 400

    flow = create_flow(user_uuid, name, description, definition)
    flow = decorate_flow_record(flow, lambda r: _resolve_profile_ref(r, user_uuid))
    if fill_report is not None:
        flow["fill_report"] = fill_report
    return jsonify(flow), 201


@flow_bp.route("/v1/flows/templates", methods=["GET"])
@operation("flows.templates", effect="read", scope="instance",
           summary="The flow topology templates a flow can be created from: their parameters, ranges and the agent slots to fill",
           summary_fields=("key", "label", "available"))
@require_auth
async def list_flow_templates_endpoint(current_user):
    """PROP-007 — the topology catalog the picker renders: every declared shape (an
    unshipped one is included GREYED with its reason, never hidden), its parameters
    with ranges/defaults, its slot roles, and — for each available shape — a laid-out
    default skeleton for the live preview. Pure registry read; creates nothing."""
    rows = topology_catalog()
    for row in rows:
        if not row.get("available"):
            continue
        try:
            skel, _ = generate_topology(row["key"], None, None, None)
            apply_layout(skel)
            row["skeleton"] = skel
        except Exception as exc:  # a preview failure must not break the catalog
            logger.warning(f"[FlowTemplates] skeleton for {row['key']!r} failed: {exc}")
    return jsonify({"templates": rows, "count": len(rows), "version": TEMPLATE_VERSION})


@flow_bp.route("/v1/flows/templates/<key>", methods=["GET"])
@operation("flows.template", effect="read", scope="own",
           summary="Preview one template: the flow it would create for these parameters, pre-filled from an agent, with the slots still open",
           path={"key": "the template's key (flows.templates lists them)"},
           query={"source_profile_id": "an agent whose experts pre-fill the slots (@TAG)"},
           notes="Each of the template's parameters may also be passed by name, e.g. stages=4.")
@require_auth
async def preview_flow_template_endpoint(current_user, key):
    """PROP-007 — the picker's LIVE preview for one template: the laid-out skeleton
    for the requested parameters, with the pre-fill a `source_profile_id` would give
    (a coordinator's experts by the three rules; a single agent as a suggestion), the
    slot checklist and the open-slot list. Query: each parameter by name
    (`?stages=4`), `source_profile_id`, and `touched` (comma list of the parameters
    the consumer set by hand — the expert count never overrides one of those). Creates
    nothing; ONE generator + ONE pre-fill shared with POST /v1/flows and TDA_Flow."""
    user_uuid = current_user.id
    args = request.args
    params = {k: v for k, v in args.items() if k not in ("source_profile_id", "touched")}
    touched = {t.strip() for t in (args.get("touched") or "").split(",") if t.strip()}
    source = None
    profiles = []
    source_id = (args.get("source_profile_id") or "").strip()
    if source_id:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            cm = get_config_manager()
            resolved = _resolve_profile_ref(source_id, user_uuid) or source_id
            source = cm.get_profile(resolved, user_uuid)
            profiles = cm.get_profiles(user_uuid) or []
        except Exception as exc:  # a pre-fill source that cannot be read is no pre-fill
            logger.warning(f"[FlowTemplates] source profile {source_id!r} unreadable: {exc}")
            source, profiles = None, []
    # Recently used agents become suggestion chips on open slots (never fills).
    recent = []
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.core.session_manager import get_all_sessions
        from trusted_data_agent.core.flow_topologies import recent_agents
        if not profiles:
            profiles = get_config_manager().get_profiles(user_uuid) or []
        listed = await get_all_sessions(user_uuid, limit=40)
        recent = recent_agents((listed or {}).get("sessions", []), profiles)
    except Exception as exc:  # suggestions are a convenience — never fail the preview
        logger.debug(f"[FlowTemplates] recent agents unavailable: {exc}")
    try:
        out = preview_topology(key, params, source, profiles, touched, recent)
    except FlowTemplateUnavailable as exc:
        return jsonify({"error": "flow_template_unavailable", "message": str(exc)}), 400
    apply_layout(out["definition"])
    out["source_tag"] = (source or {}).get("tag") if source else None
    return jsonify(out)


@flow_bp.route("/v1/flows/<flow_id>", methods=["GET"])
@operation("flows.get", effect="read", scope="own",
           summary="One flow in full: its nodes, edges, agents and open slots",
           path={"flow_id": "the flow (flows.list lists them)"})
@require_auth
async def get_flow_endpoint(current_user, flow_id: str):
    """Get a single flow."""
    user_uuid = current_user.id
    flow = get_flow(flow_id, user_uuid)
    if not flow:
        return jsonify({"error": "Flow not found"}), 404
    return jsonify(decorate_flow_record(flow, lambda r: _resolve_profile_ref(r, user_uuid)))


@flow_bp.route("/v1/flows/<flow_id>", methods=["PUT"])
@operation("flows.update", effect="write", scope="own",
           summary="Rename a flow or change its description",
           path={"flow_id": "the flow (flows.list lists them)"}, body={"name": "its new name", "description": "what it automates"},
           notes="Its graph is edited with the flow component (TDA_Flow), not here.")
@require_auth
async def update_flow_endpoint(current_user, flow_id: str):
    """Update a flow's metadata or full definition."""
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name or len(name) > 200:
            return jsonify({"error": "'name' must be 1-200 characters"}), 400
        body["name"] = name

    # Validate definition if provided
    if "definition" in body:
        flow_def = FlowDefinition(body["definition"])
        errors = flow_def.validate()
        if errors:
            return jsonify({"error": "Invalid flow definition", "details": errors}), 400

    updated = update_flow(
        flow_id, user_uuid,
        name=body.get("name"),
        description=body.get("description"),
        definition=body.get("definition"),
    )
    if not updated:
        return jsonify({"error": "Flow not found"}), 404
    return jsonify(updated)


@flow_bp.route("/v1/flows/<flow_id>", methods=["DELETE"])
@operation("flows.delete", effect="write", scope="own",
           summary="Delete a flow, its versions and its run history", path={"flow_id": "the flow (flows.list lists them)"})
@require_auth
async def delete_flow_endpoint(current_user, flow_id: str):
    """Delete a flow and all its runs."""
    user_uuid = current_user.id
    deleted = delete_flow(flow_id, user_uuid)
    if not deleted:
        return jsonify({"error": "Flow not found"}), 404
    return jsonify({"deleted": True, "flow_id": flow_id})

# ---------------------------------------------------------------------------
# Platform Flows (is_template=1) — blueprints visible to all users
# ---------------------------------------------------------------------------

@flow_bp.route("/v1/flows/platform", methods=["GET"])
@operation("flows.platform", effect="read", scope="shared",
           summary="The platform's shared flows — ready-made automations any user can fork",
           summary_fields=("flow_id", "name", "description"))
@require_auth
async def list_platform_flows_endpoint(current_user):
    """List all platform/blueprint flows (is_template=1)."""
    flows = list_platform_flows()
    return jsonify({"flows": flows, "count": len(flows)})


@flow_bp.route("/v1/flows/<flow_id>/toggle-platform", methods=["POST"])
@operation("flows.toggle_platform", effect="human_only", scope="instance", tier="admin",
           summary="Make a flow one of the platform's shared flows, or stop sharing it — an administrator publishing to every user",
           path={"flow_id": "the flow (flows.list lists them)"})
@require_auth
async def toggle_platform_endpoint(current_user, flow_id: str):
    """Promote or demote a flow as a platform template (admin only)."""
    if getattr(current_user, "tier", "") != "admin" and not getattr(current_user, "is_admin", False):
        return jsonify({"error": "Admin access required"}), 403
    body = await request.get_json(silent=True) or {}
    is_template = bool(body.get("is_template", True))
    flow = set_flow_template_flag(flow_id, is_template)
    if not flow:
        return jsonify({"error": "Flow not found"}), 404
    action = "promoted to Platform Flows" if is_template else "removed from Platform Flows"
    return jsonify({"flow": flow, "message": f"Flow {action}"})


@flow_bp.route("/v1/flows/<flow_id>/fork", methods=["POST"])
@operation("flows.fork", effect="write", scope="shared",
           summary="Copy a platform flow (or one of the caller's own) into the caller's flows",
           path={"flow_id": "the flow to copy (flows.platform lists the shared ones)"},
           body={"name": "the copy's name"})
@require_auth
async def fork_flow_endpoint(current_user, flow_id: str):
    """Fork a platform flow, or one of the user's own, into the current user's Profile Flows."""
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    name = body.get("name")
    forked = fork_flow(flow_id, user_uuid, name=name)
    if not forked:
        return jsonify({"error": "Flow not found"}), 404
    return jsonify({"flow": forked}), 201


@flow_bp.route("/v1/flows/<flow_id>/platform-meta", methods=["PUT"])
@require_auth
async def update_platform_flow_meta_endpoint(current_user, flow_id: str):
    """Admin-only: update metadata (name, description) of a platform flow."""
    if getattr(current_user, "tier", "") != "admin" and not getattr(current_user, "is_admin", False):
        return jsonify({"error": "Admin access required"}), 403
    body = await request.get_json(silent=True) or {}
    flow = update_flow_metadata(
        flow_id,
        name=body.get("name"),
        description=body.get("description"),
    )
    if not flow:
        return jsonify({"error": "Flow not found"}), 404
    return jsonify({"flow": flow})

# ---------------------------------------------------------------------------
# Graph Patch Protocol
# ---------------------------------------------------------------------------

@flow_bp.route("/v1/flows/<flow_id>/patch", methods=["POST"])
@require_auth
async def patch_flow_endpoint(current_user, flow_id: str):
    """
    Apply a graph patch operation (bidirectional canvas ↔ conversation protocol).

    Patch ops:
      add_node, remove_node, update_node
      add_edge, remove_edge
      create_scope, update_scope, delete_scope
      update_global_params

    Returns the updated flow definition.
    """
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    op = body.get("op")
    if not op:
        return jsonify({"error": "Missing 'op' field"}), 400

    try:
        updated, layout_positions = apply_patch(flow_id, user_uuid, body)
    except LoopError as exc:
        return jsonify({"error": exc.message, "code": exc.code}), 400
    if not updated:
        return jsonify({"error": "Flow not found"}), 404

    # Auto-snapshot after every structural mutation (backend deduplicates identical defs).
    # Pass a descriptive label so the Flow history menu shows what each version changed.
    snapshot_label = _label_for_patch(op, body.get("payload") or {})
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: snapshot_flow(flow_id, user_uuid, label=snapshot_label))
    except Exception:
        pass

    # Return layout positions whenever the server applied layout (any op, any mode).
    # Callers that manage their own animation (insert_node, add_node plain-drop) already
    # read from definition.nodes; including node_positions here lets the generic path
    # in patchFlow() animate remove_node / add_edge / remove_edge when auto-layout is ON.
    response_data: dict = {
        "flow_id": flow_id,
        "op": op,
        "definition": updated["definition"],
        "layout_applied": layout_positions is not None,
    }
    if layout_positions:
        response_data["node_positions"] = layout_positions
        response_data["all_edges"] = updated["definition"].get("edges", [])

    return jsonify(response_data)


# ---------------------------------------------------------------------------
# Manual Layout (Sugiyama–Barycenter, on demand)
# ---------------------------------------------------------------------------

@flow_bp.route("/v1/flows/<flow_id>/layout", methods=["POST"])
@require_auth
async def layout_flow_endpoint(current_user, flow_id: str):
    """Re-compute layout with Sugiyama–Barycenter and persist. Always applies regardless of autoLayout setting."""
    from trusted_data_agent.core.flow_layout import compute_layout
    user_uuid = current_user.id
    flow = get_flow(flow_id, user_uuid)
    if not flow:
        return jsonify({"error": "Flow not found"}), 404
    definition = flow["definition"]
    node_positions = compute_layout(definition)
    for n in definition.get("nodes", []):
        if n["id"] in node_positions:
            n["position"] = node_positions[n["id"]]
    updated = update_flow(flow_id, user_uuid, definition=definition)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: snapshot_flow(flow_id, user_uuid, label="Layout"))
    except Exception:
        pass
    return jsonify({
        "flow_id": flow_id,
        "node_positions": node_positions,
        "definition": updated["definition"],
    })


# ---------------------------------------------------------------------------
# Flow Versions
# ---------------------------------------------------------------------------

@flow_bp.route("/v1/flows/<flow_id>/versions", methods=["GET"])
@operation("flows.versions", effect="read", scope="own",
           summary="A flow's saved versions, oldest first", path={"flow_id": "the flow (flows.list lists them)"},
           summary_fields=("version_id", "version_num", "label", "created_at"))
@require_auth
async def list_versions_endpoint(current_user, flow_id: str):
    """List all snapshots for a flow (oldest first)."""
    user_uuid = current_user.id
    if not get_flow(flow_id, user_uuid):
        return jsonify({"error": "Flow not found"}), 404
    versions = list_versions(flow_id, user_uuid)
    return jsonify({"versions": versions, "count": len(versions)})


@flow_bp.route("/v1/flows/<flow_id>/versions", methods=["POST"])
@operation("flows.version_save", effect="write", scope="own",
           summary="Save the flow as it is now as a named version (nothing saved when unchanged)",
           path={"flow_id": "the flow (flows.list lists them)"}, body={"label": "a name for this version"})
@require_auth
async def snapshot_flow_endpoint(current_user, flow_id: str):
    """Create a named version snapshot of the flow's current definition."""
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: snapshot_flow(flow_id, user_uuid, label=body.get("label"))
    )
    if result is None:
        return jsonify({"status": "unchanged", "message": "Definition unchanged — no snapshot created"}), 200
    return jsonify(result), 201


@flow_bp.route("/v1/flows/<flow_id>/versions/<version_id>/restore", methods=["POST"])
@operation("flows.version_restore", effect="write", scope="own",
           summary="Put a saved version back as the flow's current graph",
           path={"flow_id": "the flow (flows.list lists them)", "version_id": "the version (flows.versions lists them)"},
           notes="Save the current graph first (flows.version_save) if it may be wanted again.")
@require_auth
async def restore_version_endpoint(current_user, flow_id: str, version_id: str):
    """Restore a flow's definition from a snapshot."""
    user_uuid = current_user.id
    loop = asyncio.get_event_loop()
    updated = await loop.run_in_executor(
        None, lambda: restore_version(version_id, flow_id, user_uuid)
    )
    if not updated:
        return jsonify({"error": "Version or flow not found"}), 404
    return jsonify(updated)


# ---------------------------------------------------------------------------
# Global Flow Variables (cross-flow, user-level — Flowise-style $vars)
# ---------------------------------------------------------------------------

@flow_bp.route("/v1/flow-variables", methods=["GET"])
@operation("flows.variables", effect="read", scope="own",
           summary="The caller's flow variables — plain constants every flow can use")
@require_auth
async def list_flow_variables_endpoint(current_user):
    """List the authenticated user's global flow variables."""
    variables = list_flow_variables(current_user.id)
    return jsonify({"variables": variables, "count": len(variables)})


@flow_bp.route("/v1/flow-variables", methods=["PUT"])
@require_auth
async def replace_flow_variables_endpoint(current_user):
    """Replace the user's entire global-variable set.

    Body: {"variables": {"name": "value", ...}}. These are plaintext constants
    available to every flow — NOT a secrets store (route secrets through
    credential configuration instead).
    """
    body = await request.get_json(silent=True) or {}
    variables = body.get("variables", {})
    if not isinstance(variables, dict):
        return jsonify({"error": "'variables' must be an object"}), 400
    saved = replace_flow_variables(current_user.id, variables)
    return jsonify({"variables": saved, "count": len(saved)})


# ---------------------------------------------------------------------------
# Tool discovery (for the task_tool node picker)
# ---------------------------------------------------------------------------

def _builtin_flow_tools():
    """Always-available client-side builtins (handled directly in invoke_mcp_tool,
    so they work even with no MCP server connected)."""
    return [
        {"name": "TDA_CurrentDate", "description": "Returns today's date (YYYY-MM-DD). No arguments.", "category": "Built-in"},
        {"name": "TDA_DateRange", "description": "Computes a date range. Args: duration (int), unit (e.g. 'days').", "category": "Built-in"},
    ]


def _component_flow_tools(profile=None):
    """Component tools (TDA_Charting, TDA_Dashboard, …) — profile-filtered when a
    profile is given (its componentConfig), else all loaded component tools."""
    out = []
    try:
        from trusted_data_agent.components.manager import get_component_manager
        cm = get_component_manager()
        if profile is not None:
            for t in (cm.get_tool_definitions(profile) or []):
                if t.get("name"):
                    out.append({"name": t["name"], "description": t.get("description", "") or "", "category": "Components"})
        else:
            for tool_name, comp_id in dict(getattr(cm, "_tool_to_component", {})).items():
                if not tool_name:
                    continue
                desc = ""
                try:
                    comp = cm.components.get(comp_id)
                    # through the gate resolver — never the raw manifest text ([[gate:x]] markup)
                    desc = ((cm.tool_definition_for(comp, None) or {}).get("description", "") or "") \
                        if comp and comp.tool_definition else ""
                except Exception:
                    desc = ""
                out.append({"name": tool_name, "description": desc, "category": "Components"})
    except Exception as exc:
        logger.warning(f"[FlowTools] could not enumerate component tools: {exc}")
    return out


@flow_bp.route("/v1/flows/tools", methods=["GET"])
@require_auth
async def list_flow_tools_endpoint(current_user):
    """Tools (and prompts) a flow node can call — **profile-scoped**.

    `?profile_id=<id>` returns the active tools/prompts for THAT profile (the same
    per-profile classification the Classification Results dialog uses), since the
    available MCP tools/prompts depend on the profile's MCP server. Without a
    profile_id, falls back to the globally-connected server + components + builtins
    (and no prompts — prompts are profile/connection specific).

    Returns `{tools: [{name, description, category}],
              prompts: [{name, description, category, arguments}], count}`.
    Deactivated tools/prompts are omitted (they cannot be invoked).
    """
    from trusted_data_agent.core.config import APP_STATE
    profile_id = (request.args.get("profile_id") or "").strip()
    tools, prompts, seen = [], [], set()
    for b in _builtin_flow_tools():
        seen.add(b["name"]); tools.append(b)

    if profile_id:
        # Per-profile: tools + prompts from the cached classification.
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            cm = get_config_manager()
            profile = cm.get_profile(profile_id, current_user.id)
            if profile:
                classification = cm.get_profile_classification(profile_id, current_user.id) or {}
                for category, items in (classification.get("tools") or {}).items():
                    for t in (items or []):
                        name = t.get("name") if isinstance(t, dict) else t
                        if not name or name in seen or (isinstance(t, dict) and t.get("disabled")):
                            continue
                        seen.add(name)
                        tools.append({"name": name, "description": (isinstance(t, dict) and t.get("description")) or "", "category": category})
                for category, items in (classification.get("prompts") or {}).items():
                    for p in (items or []):
                        name = p.get("name") if isinstance(p, dict) else p
                        if not name or (isinstance(p, dict) and p.get("disabled")):
                            continue
                        prompts.append({
                            "name": name,
                            "description": (isinstance(p, dict) and p.get("description")) or "",
                            "category": category,
                            "arguments": (isinstance(p, dict) and p.get("arguments")) or [],
                        })
                for c in _component_flow_tools(profile):
                    if c["name"] not in seen:
                        seen.add(c["name"]); tools.append(c)
        except Exception as exc:
            logger.warning(f"[FlowTools] profile classification lookup failed for {profile_id}: {exc}")
    else:
        # Global fallback (no profile): connected server tools + all components.
        structured = APP_STATE.get("structured_tools", {}) or {}
        for category, items in structured.items():
            for t in (items or []):
                name = t.get("name")
                if not name or name in seen:
                    continue
                seen.add(name)
                tools.append({"name": name, "description": t.get("description", "") or "", "category": category})
        for c in _component_flow_tools(None):
            if c["name"] not in seen:
                seen.add(c["name"]); tools.append(c)

    tools.sort(key=lambda x: (x["category"], x["name"]))
    prompts.sort(key=lambda x: (x["category"], x["name"]))
    return jsonify({"tools": tools, "prompts": prompts, "count": len(tools) + len(prompts)})


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@flow_bp.route("/v1/flows/<flow_id>/execute", methods=["POST"])
@operation("flows.run", effect="write", scope="own",
           summary="Run a flow now — its agents answer and its steps act; returns the run to follow",
           path={"flow_id": "the flow (flows.list lists them)"},
           body={"input_params": Arg("object", "values for the flow's inputs, by name"),
                 "max_tokens_per_run": Arg("integer", "stop the run past this many tokens (default: the flow's own limit)")},
           notes="It spends tokens and may write files or perform the actions its nodes hold (each under its own "
                 "agent's authority). Refused before anything runs while a slot is open. Follow it with flows.run_status.")
@require_auth
async def execute_flow_endpoint(current_user, flow_id: str):
    """
    Start executing a flow. Returns run_id immediately.
    Poll GET /v1/flows/runs/<run_id> for status.
    For live updates, subscribe to SSE via GET /v1/flows/runs/<run_id>/stream.
    """
    _gate = _assert_component_available("flow", current_user.id)
    if _gate:
        return _gate
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    input_params = body.get("input_params", {})
    # session_id of the chat the user triggered the run from.
    # Used as the default execution session for all LLM nodes unless a node
    # overrides with session_context="new" or session_context="pinned".
    caller_session_id = body.get("session_id")
    node_session_mode = body.get("node_session_mode", "compact")
    # Partial run (debugging): run only one node ('single') or a node + everything
    # downstream ('downstream'). Omit / 'full' runs the whole flow.
    start_node_id = body.get("start_node_id")
    run_mode = body.get("run_mode", "full")
    # Optional per-run token ceiling (input + output). Falls back to the flow's
    # own settings.max_tokens_per_run when omitted.
    max_tokens_per_run = body.get("max_tokens_per_run")
    logger.info(f"[FlowExecute] flow={flow_id} caller_session={caller_session_id!r} node_session_mode={node_session_mode!r} start_node={start_node_id!r} run_mode={run_mode!r} max_tokens={max_tokens_per_run!r}")

    # Fetch the caller session's profile so nodes without an explicit profile_id
    # inherit the session's profile rather than falling back to the user's default.
    caller_profile_id = None
    if caller_session_id:
        try:
            from trusted_data_agent.core.session_manager import get_session as _get_session
            caller_session = await _get_session(user_uuid, caller_session_id)
            caller_profile_id = (caller_session or {}).get("profile_id")
        except Exception as exc:
            logger.warning(f"[FlowExecute] Could not fetch caller session profile: {exc}")

    try:
        # The canonical run emitter is built inside execute_flow and fans every
        # event to the per-run SSE stream + the per-user notification channel —
        # callers no longer select a bus (that divergence was the root cause of
        # conversation-triggered runs not animating the open canvas).
        run_id = await execute_flow(
            flow_id=flow_id,
            user_id=user_uuid,
            input_params=input_params,
            caller_session_id=caller_session_id,
            node_session_mode=node_session_mode,
            caller_profile_id=caller_profile_id,
            start_node_id=start_node_id,
            run_mode=run_mode,
            max_tokens_per_run=max_tokens_per_run,
        )

        # `status_url` mirrors the query Task contract ({task_id, status_url}) so a
        # Task-aware REST client can follow a flow run the same way it follows a query.
        # `session_id` is the session the run is bound to — the caller's when supplied,
        # otherwise one auto-provisioned at execute time so the run is monitorable in
        # the session-scoped UX.
        _run = get_run(run_id)
        return jsonify({
            "run_id": run_id,
            "status": "queued",
            "status_url": f"/api/v1/flows/runs/{run_id}",
            "session_id": (_run or {}).get("session_id"),
        }), 202

    except FlowSlotUnfilledError as exc:
        # PROP-007 §4.2 — caught BEFORE ValueError (its parent): a required slot is
        # still open. Recovery-shaped: the slots are named, nothing ran, no run exists.
        return jsonify({
            "error": "flow_slots_unfilled",
            "message": str(exc),
            "open_slots": exc.open_slots,
        }), 409
    except ActionNodeError as exc:
        # PROP-008 §6 — nothing ran: an action node's agent may not perform its action
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Flow execution failed: {exc}", exc_info=True)
        return jsonify({"error": "Execution failed", "detail": str(exc)}), 500


@flow_bp.route("/v1/flows/<flow_id>/runs", methods=["GET"])
@operation("flows.runs", effect="read", scope="own",
           summary="A flow's recent runs, newest first: status, tokens, cost, when",
           path={"flow_id": "the flow (flows.list lists them)"}, query={"limit": Arg("integer", "at most this many (default 50)")},
           summary_fields=("run_id", "status", "started_at", "cost_usd"))
@require_auth
async def list_runs_endpoint(current_user, flow_id: str):
    """List execution runs for a flow."""
    user_uuid = current_user.id
    limit = int(request.args.get("limit", 50))
    runs = list_runs(flow_id, user_uuid, limit=limit)
    return jsonify({"runs": runs, "count": len(runs)})


@flow_bp.route("/v1/flows/<flow_id>/node-data", methods=["GET"])
@require_auth
async def flow_node_data_endpoint(current_user, flow_id: str):
    """Latest input/output data PER NODE across recent runs.

    Powers the inspector's per-node Data panel. Each node shows its most recent
    execution regardless of which run produced it — so running one node (a new run
    containing only that node) does NOT wipe other nodes' earlier results. Returns
    {run_id, node_data: {node_id: {status, input_state, output_state, tokens_*, ...}}}.
    """
    user_uuid = current_user.id
    if not get_flow(flow_id, user_uuid):
        return jsonify({"error": "Flow not found"}), 404
    runs = list_runs(flow_id, user_uuid, limit=25)  # newest first
    if not runs:
        return jsonify({"run_id": None, "node_data": {}})
    by_node = {}
    for run in runs:  # newest → oldest
        per_run = {}
        for r in get_node_runs(run["run_id"]):  # id ASC → last iteration wins within the run
            per_run[r["node_id"]] = r
        for node_id, r in per_run.items():
            if node_id not in by_node:  # a newer run already set this node → keep the newer
                by_node[node_id] = r
    return jsonify({"run_id": runs[0]["run_id"], "status": runs[0].get("status"), "node_data": by_node})


@flow_bp.route("/v1/flows/runs/<run_id>", methods=["GET"])
@operation("flows.run_status", effect="read", scope="own",
           summary="One flow run: its status, its result and what each node did",
           path={"run_id": "the run (flows.run returns it; flows.runs lists them)"})
@require_auth
async def get_run_endpoint(current_user, run_id: str):
    """Get run status, output, and node-level execution trace."""
    user_uuid = current_user.id
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    # Verify user owns this run
    if run.get("user_id") != user_uuid:
        return jsonify({"error": "Not found"}), 404

    node_runs = get_node_runs(run_id)
    # Additive Task-Object-compatible aliases (originals retained): a client written
    # against the query Task Object (§4.1) recognizes a flow run without special-casing.
    #   last_updated <- most recent lifecycle timestamp
    #   result       <- final node-output state (alias of output_result)
    aliases = {
        "last_updated": run.get("completed_at") or run.get("started_at") or run.get("created_at"),
        "result": run.get("output_result"),
    }
    return jsonify({**run, **aliases, "node_runs": node_runs})


@flow_bp.route("/v1/flows/runs/<run_id>/cancel", methods=["POST"])
@operation("flows.run_cancel", effect="write", scope="own",
           summary="Stop a running flow before its next step", path={"run_id": "the run"})
@require_auth
async def cancel_run_endpoint(current_user, run_id: str):
    """Cancel an in-progress run."""
    user_uuid = current_user.id
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    if run.get("user_id") != user_uuid:
        return jsonify({"error": "Not found"}), 404

    # Signal the live executor (sets its cooperative cancel flag so the run loop
    # halts before the next dependency level / scope iteration). If the run has
    # already settled, no executor is registered — we still flip the DB status.
    signalled = cancel_active_run(run_id)
    update_run_status(run_id, "cancelled")
    return jsonify({"cancelled": True, "run_id": run_id, "signalled": signalled})


# ---------------------------------------------------------------------------
# SSE stream for live run events
# ---------------------------------------------------------------------------
#
# The per-run pub/sub registries (`_run_subscribers`, `_run_event_buffer`) and the
# `_broadcast_flow_event` publisher now live in `flow_executor` alongside the run
# lifecycle, so the canonical emitter can feed them for EVERY run regardless of
# initiator. This endpoint only subscribes + replays against the shared registries.


@flow_bp.route("/v1/flows/runs/<run_id>/stream", methods=["GET"])
@operation("flows.run_stream", effect="never", scope="own",
           summary="A run's live events as a browser stream — the page's plumbing; agents follow flows.run_status")
async def stream_run_endpoint(run_id: str):
    """
    SSE stream for live run events.
    Supports Bearer token in Authorization header OR ?token= query param
    (EventSource cannot set custom headers, so ?token= is required for browser SSE).
    Subscribe before or immediately after POSTing /execute.
    Stream closes when run reaches terminal status.
    """
    # Auth: try header first, then query param (EventSource can't set headers)
    current_user = get_current_user()
    if not current_user:
        token = request.args.get("token", "")
        if token:
            payload = verify_auth_token(token)
            if payload:
                from trusted_data_agent.auth.database import get_db_session
                from trusted_data_agent.auth.models import User
                try:
                    with get_db_session() as session:
                        current_user = session.query(User).filter_by(id=payload.get("user_id")).first()
                        if current_user:
                            session.expunge(current_user)
                except Exception:
                    current_user = None
    if not current_user:
        return jsonify({"error": "Authentication required"}), 401

    user_uuid = current_user.id
    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    if run.get("user_id") != user_uuid:
        return jsonify({"error": "Not found"}), 404

    queue: asyncio.Queue = asyncio.Queue(maxsize=400)
    _run_subscribers.setdefault(run_id, []).append(queue)

    async def generate():
        try:
            replayed_seq = 0
            # Catch-up: replay events emitted before this client subscribed
            # (e.g. flow_node_start, which fires before the browser opens the
            # stream). Dedup against the live queue via the per-event seq.
            for seq, message in list(_run_event_buffer.get(run_id, [])):
                yield f"data: {message}\n\n"
                replayed_seq = seq

            # If the run already finished, the replay carried the terminal event;
            # if the buffer had expired, synthesise closure from the run record.
            current = get_run(run_id)
            if current and current.get("status") in ("complete", "error", "cancelled"):
                if replayed_seq == 0:
                    yield f"data: {json.dumps({'type': 'flow_run_complete', 'payload': current})}\n\n"
                return

            while True:
                try:
                    seq, message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if seq <= replayed_seq:
                        continue  # already delivered during replay
                    yield f"data: {message}\n\n"
                    # Close stream on terminal events
                    evt = json.loads(message)
                    if evt.get("type") in ("flow_run_complete", "flow_run_error"):
                        break
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            subs = _run_subscribers.get(run_id, [])
            if queue in subs:
                subs.remove(queue)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
