"""
Dashboard Routes — REST API for the Dashboard Canvas component.

Endpoints:
  GET    /api/v1/dashboards                           List user's dashboards
  POST   /api/v1/dashboards                           Create dashboard
  GET    /api/v1/dashboards/<id>                      Get dashboard + tiles
  PUT    /api/v1/dashboards/<id>                      Update dashboard metadata
  DELETE /api/v1/dashboards/<id>                      Delete dashboard
  GET    /api/v1/dashboards/<id>/tiles                List tiles
  POST   /api/v1/dashboards/<id>/tiles                Add tile (from component payload)
  PUT    /api/v1/dashboards/<id>/tiles/<tile_id>      Update tile (title, position, size, spec)
  DELETE /api/v1/dashboards/<id>/tiles/<tile_id>      Delete tile
  POST   /api/v1/dashboards/<id>/tiles/<tile_id>/refresh  Trigger tile refresh (returns task_id)
  PUT    /api/v1/dashboards/<id>/layout               Bulk update tile layout positions
"""

import json
import logging
import pathlib
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid
from datetime import datetime, timezone

from quart import Blueprint, jsonify, request

from trusted_data_agent.api.operations import Arg, operation

from trusted_data_agent.auth.middleware import require_auth
from trusted_data_agent.components import signals as _signals
from trusted_data_agent.core.ui_notify import announce_component_change as _signals_announce

logger = logging.getLogger("quart.app")

dashboard_bp = Blueprint("dashboard", __name__)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_path() -> str:
    from trusted_data_agent.auth.database import DATABASE_URL
    return DATABASE_URL.replace("sqlite:///", "")


def _conn():
    c = sqlite_util.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_dict(row) -> dict:
    return dict(row) if row else None


async def _load_session_workflow_history(session_id: str, user_uuid: str):
    """Load and normalise a session's workflow_history (list of turn dicts).
    Returns [] on any failure. Shared by the turn- and session-scoped stampers.
    Routes through the session_manager public API (backend-agnostic) rather than
    reading session files directly."""
    if not session_id or not user_uuid:
        return []
    try:
        from trusted_data_agent.core import session_manager
        return await session_manager.get_workflow_history(user_uuid, session_id)
    except Exception as exc:
        logger.warning(f"[dashboard_routes] _load_session_workflow_history failed: {exc}")
        return []

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



def _single_data_tool_in_trace(trace) -> tuple[str | None, str | None]:
    """If a turn's execution_trace used EXACTLY ONE successful non-TDA_ data tool,
    return (tool_name, args_json); otherwise (None, None). This is the per-turn
    primitive both stampers build on."""
    if not isinstance(trace, list):
        return None, None
    data_tools = [
        e for e in trace
        if isinstance(e.get("action"), dict)
        and not e["action"].get("tool_name", "").startswith("TDA_")
        and isinstance(e.get("result"), dict)
        and e["result"].get("status") == "success"
    ]
    if len(data_tools) == 1:
        sole = data_tools[0]["action"]
        return sole.get("tool_name"), json.dumps(sole.get("arguments") or {})
    return None, None


async def _mcp_stamp_for_turn(session_id: str, user_uuid: str, turn_id) -> tuple[str | None, str | None]:
    """Stamp from the SPECIFIC turn that produced the pinned visual — NOT the
    session's most-recent data turn.

    A single session accumulates many turns with different profiles (e.g. an
    Ideate `conversation_with_tools` turn that charts from LLM knowledge with NO
    data tool, followed by an Optimize `tool_enabled` turn that runs
    `base_readQuery`). The legacy session-scoped stamp returns the newest
    qualifying tool for ANY tile pinned from the session, so the earlier Ideate
    tile inherits the later Optimize turn's `base_readQuery` — showing a
    misleading "direct data refresh" bolt whose replay would run the WRONG query.

    Binding the stamp to the originating turn (matched by its `turn` number,
    which the frontend captures from the assistant message's data-turn-id) fixes
    this: a turn that used no single data tool gets NO stamp (LLM re-run only).
    """
    if turn_id is None or turn_id == "":
        return None, None
    try:
        turn_num = int(turn_id)
    except (TypeError, ValueError):
        return None, None

    wh = await _load_session_workflow_history(session_id, user_uuid)
    for turn in wh:
        if isinstance(turn, dict) and turn.get("turn") == turn_num:
            return _single_data_tool_in_trace(turn.get("execution_trace", []))
    # Turn not found (history trimmed / mismatch) — do NOT fall back to a
    # different turn's tool; absence of a confident match means LLM re-run.
    return None, None


async def _mcp_stamp_for_session(session_id: str, user_uuid: str) -> tuple[str | None, str | None]:
    """Return (mcp_tool_name, mcp_args_json) when the session's most-recent data
    turn used exactly one non-TDA_ data tool successfully.

    Called from the add_tile REST route after the turn is fully saved.

    Stamping rules (checked newest → oldest turn):
      1. Turn with TDA_Dashboard + exactly 1 data tool → stamp that tool
         (viz session: the dashboard rendered the data-tool's output).
      2. Cross-turn: TDA_Dashboard turn later + 1 data tool in an earlier turn.
      3. Turn with exactly 1 data tool and NO TDA_Dashboard → also stamp
         (plain data-table pin: user pins the raw collateral-report table
          directly — direct MCP replay is correct and expected).
    Not stamped: multi-data-tool turns, RAG-only turns, errored turns.

    Bug fixes vs. original implementation:
      • workflow_history in last_turn_data is persisted as a Python-repr string
        (not JSON); we now parse it with ast.literal_eval before scanning.
      • The TDA_Dashboard requirement is relaxed to case 3 above so that raw
        table pins (no TDA_Dashboard call) also get mcp_tool_name stamped.
    """
    if not session_id or not user_uuid:
        return None, None
    try:
        wh = await _load_session_workflow_history(session_id, user_uuid)
        if not wh:
            return None, None

        # Scan turns newest→oldest.
        # Strategy (priority order):
        #   a) Turn that BOTH ran a single data tool AND called TDA_Dashboard.
        #   b) Cross-turn: TDA_Dashboard seen, then find the prior single-data-tool turn.
        #   c) Any turn with exactly 1 data tool (no TDA_Dashboard required) —
        #      covers plain table pins where the user pinned the raw collateral result.
        last_data_tool_entry = None  # most-recent single-data-tool turn seen so far
        found_dashboard = False       # whether we passed a TDA_Dashboard turn yet
        plain_single_tool_entry = None  # first (newest) single-data-tool non-dashboard turn

        for turn in reversed(wh):
            if not isinstance(turn, dict):
                continue
            trace = turn.get("execution_trace", [])
            if not isinstance(trace, list):
                continue

            data_tools = [
                e for e in trace
                if isinstance(e.get("action"), dict)
                and not e["action"].get("tool_name", "").startswith("TDA_")
                and isinstance(e.get("result"), dict)
                and e["result"].get("status") == "success"
            ]
            dashboard_called = any(
                isinstance(e.get("action"), dict)
                and e["action"].get("tool_name") == "TDA_Dashboard"
                for e in trace
            )

            # Case (a): perfect match — data + dashboard in same turn.
            if len(data_tools) == 1 and dashboard_called:
                sole = data_tools[0]["action"]
                return sole.get("tool_name"), json.dumps(sole.get("arguments") or {})

            if dashboard_called:
                found_dashboard = True

            if len(data_tools) == 1 and not dashboard_called:
                last_data_tool_entry = data_tools[0]
                # Track the newest plain single-tool turn for case (c).
                if plain_single_tool_entry is None:
                    plain_single_tool_entry = data_tools[0]

        # Case (b): cross-turn dashboard + prior data tool.
        if found_dashboard and last_data_tool_entry is not None:
            sole = last_data_tool_entry["action"]
            return sole.get("tool_name"), json.dumps(sole.get("arguments") or {})

        # Case (c): no TDA_Dashboard in session — plain table pin.
        # Allow direct MCP replay when a single data tool produced the result.
        if plain_single_tool_entry is not None:
            sole = plain_single_tool_entry["action"]
            return sole.get("tool_name"), json.dumps(sole.get("arguments") or {})

        # No qualifying pattern found (multi-data-tool, RAG-only, errored turns, etc.)

    except Exception as exc:
        logger.warning(f"[dashboard_routes] _mcp_stamp_for_session failed: {exc}")
    return None, None


# ── Dashboard CRUD ────────────────────────────────────────────────────────────

@dashboard_bp.route("/v1/dashboards", methods=["GET"])
@operation("dashboards.list", effect="read", scope="own",
           summary="The caller's dashboards: name, description, how many tiles, which is the default",
           summary_fields=("id", "name", "tile_count", "is_default"))
@require_auth
async def list_dashboards(current_user):
    """List all dashboards for the authenticated user.

    Optional query params:
      source_session_id  ─ session ID of a viz card
      source_prompt      ─ source_prompt stored on the tile
    When both are provided each dashboard entry gains a ``pinned`` boolean
    indicating whether it already contains a tile from that viz card.
    """
    user_uuid = current_user.id
    source_session_id = request.args.get("source_session_id", "").strip()
    source_prompt     = request.args.get("source_prompt", "").strip()

    with _conn() as conn:
        rows = conn.execute(
            """SELECT d.id, d.name, d.description, d.is_default,
                      d.auto_refresh_s, d.theme, d.created_at, d.updated_at,
                      COUNT(t.id) AS tile_count
               FROM dashboards d
               LEFT JOIN dashboard_tiles t ON t.dashboard_id = d.id
               WHERE d.user_id = ?
               GROUP BY d.id
               ORDER BY d.updated_at DESC""",
            (user_uuid,),
        ).fetchall()

    dashboards = [_row_to_dict(r) for r in rows]

    # When a viz card identity is supplied, flag which dashboards already contain it.
    if source_session_id and source_prompt:
        with _conn() as conn:
            pinned_ids = {
                row[0] for row in conn.execute(
                    """SELECT DISTINCT dashboard_id FROM dashboard_tiles
                       WHERE user_id=? AND source_session_id=? AND source_prompt=?""",
                    (user_uuid, source_session_id, source_prompt),
                ).fetchall()
            }
        for d in dashboards:
            d["pinned"] = d["id"] in pinned_ids

    return jsonify({"dashboards": dashboards})


@dashboard_bp.route("/v1/dashboards", methods=["POST"])
@operation("dashboards.create", effect="write", scope="own",
           summary="Create an empty dashboard — its tiles are pinned from conversations (the dashboard component)",
           body={"name": "its name", "description": "what it shows"})
@require_auth
async def create_dashboard(current_user):
    """Create a new dashboard."""
    _gate = _assert_component_available("dashboard", current_user.id)
    if _gate:
        return _gate
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    name = (body.get("name") or "My Dashboard").strip()
    description = body.get("description") or ""
    is_default = 1 if body.get("is_default") else 0

    dashboard_id = f"dash-{uuid.uuid4().hex[:12]}"
    now = _now()

    with _conn() as conn:
        if is_default:
            conn.execute(
                "UPDATE dashboards SET is_default=0 WHERE user_id=?",
                (user_uuid,),
            )
        conn.execute(
            """INSERT INTO dashboards
               (id, user_id, name, description, is_default, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (dashboard_id, user_uuid, name, description, is_default, now, now),
        )
        conn.commit()

    _signals_announce("dashboard", "created", user_uuid, [dashboard_id])
    return jsonify({"dashboard_id": dashboard_id, "name": name, "created_at": now}), 201


@dashboard_bp.route("/v1/dashboards/<dashboard_id>", methods=["GET"])
@operation("dashboards.get", effect="read", scope="own",
           summary="One dashboard with its settings and tiles", path={"dashboard_id": "the dashboard (dashboards.list lists them)"})
@require_auth
async def get_dashboard(current_user, dashboard_id):
    """Get a dashboard with all its tiles."""
    user_uuid = current_user.id
    with _conn() as conn:
        dash = conn.execute(
            "SELECT * FROM dashboards WHERE id=? AND user_id=?",
            (dashboard_id, user_uuid),
        ).fetchone()

        if not dash:
            return jsonify({"error": "Dashboard not found"}), 404

        tiles = conn.execute(
            """SELECT * FROM dashboard_tiles
               WHERE dashboard_id=?
               ORDER BY sort_order ASC, created_at ASC""",
            (dashboard_id,),
        ).fetchall()

    result = _row_to_dict(dash)
    result["tiles"] = [_row_to_dict(t) for t in tiles]
    # Parse JSON fields
    for tile in result["tiles"]:
        if isinstance(tile.get("spec"), str):
            try:
                tile["spec"] = json.loads(tile["spec"])
            except (json.JSONDecodeError, TypeError):
                tile["spec"] = {}
    return jsonify(result)


@dashboard_bp.route("/v1/dashboards/<dashboard_id>", methods=["PUT"])
@operation("dashboards.update", effect="write", scope="own",
           summary="Rename a dashboard, change its description or its theme",
           path={"dashboard_id": "the dashboard (dashboards.list lists them)"},
           body={"name": "its new name", "description": "what it shows", "theme": "its colour theme"},
           notes="How often it refreshes on its own is the owner's — it re-runs every tile's agent on a timer.")
@require_auth
@_signals.announces_route("dashboard", "updated", ids=("dashboard_id",))
async def update_dashboard(current_user, dashboard_id):
    """Update dashboard metadata (name, description, theme, auto_refresh_s, is_default)."""
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    now = _now()

    with _conn() as conn:
        dash = conn.execute(
            "SELECT id FROM dashboards WHERE id=? AND user_id=?",
            (dashboard_id, user_uuid),
        ).fetchone()

        if not dash:
            return jsonify({"error": "Dashboard not found"}), 404

        updates = {}
        if "name" in body:
            name = str(body["name"] or "").strip()
            if not name or len(name) > 200:
                return jsonify({"error": "'name' must be 1-200 characters"}), 400
            updates["name"] = name
        if "description" in body:
            updates["description"] = body["description"]
        if "theme" in body:
            updates["theme"] = body["theme"]
        if "auto_refresh_s" in body:
            updates["auto_refresh_s"] = int(body["auto_refresh_s"])
        if "is_default" in body:
            if body["is_default"]:
                conn.execute(
                    "UPDATE dashboards SET is_default=0 WHERE user_id=?",
                    (user_uuid,),
                )
            updates["is_default"] = 1 if body["is_default"] else 0

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values()) + [now, dashboard_id, user_uuid]
            conn.execute(
                f"UPDATE dashboards SET {set_clause}, updated_at=? WHERE id=? AND user_id=?",
                values,
            )
            conn.commit()

    return jsonify({"status": "ok", "updated_at": now})


@dashboard_bp.route("/v1/dashboards/<dashboard_id>", methods=["DELETE"])
@operation("dashboards.delete", effect="write", scope="own",
           summary="Delete a dashboard and its tiles", path={"dashboard_id": "the dashboard (dashboards.list lists them)"})
@require_auth
@_signals.announces_route("dashboard", "deleted", ids=("dashboard_id",))
async def delete_dashboard(current_user, dashboard_id):
    """Delete a dashboard and all its tiles."""
    user_uuid = current_user.id
    with _conn() as conn:
        dash = conn.execute(
            "SELECT name FROM dashboards WHERE id=? AND user_id=?",
            (dashboard_id, user_uuid),
        ).fetchone()

        if not dash:
            return jsonify({"error": "Dashboard not found"}), 404

        conn.execute(
            "DELETE FROM dashboards WHERE id=? AND user_id=?",
            (dashboard_id, user_uuid),
        )
        conn.commit()

    return jsonify({"status": "deleted", "dashboard_id": dashboard_id})


# ── Tile CRUD ─────────────────────────────────────────────────────────────────

@dashboard_bp.route("/v1/dashboards/<dashboard_id>/tiles", methods=["GET"])
@operation("dashboards.tiles", effect="read", scope="own",
           summary="A dashboard's tiles: what each shows, the agent behind it, when it was last refreshed and how that went",
           path={"dashboard_id": "the dashboard (dashboards.list lists them)"},
           summary_fields=("id", "title", "component_id", "profile_tag", "refresh_status", "last_refreshed"))
@require_auth
async def list_tiles(current_user, dashboard_id):
    """List all tiles for a dashboard."""
    user_uuid = current_user.id
    with _conn() as conn:
        dash = conn.execute(
            "SELECT id FROM dashboards WHERE id=? AND user_id=?",
            (dashboard_id, user_uuid),
        ).fetchone()

        if not dash:
            return jsonify({"error": "Dashboard not found"}), 404

        tiles = conn.execute(
            "SELECT * FROM dashboard_tiles WHERE dashboard_id=? ORDER BY sort_order ASC",
            (dashboard_id,),
        ).fetchall()

    result = [_row_to_dict(t) for t in tiles]
    for tile in result:
        if isinstance(tile.get("spec"), str):
            try:
                tile["spec"] = json.loads(tile["spec"])
            except (json.JSONDecodeError, TypeError):
                tile["spec"] = {}
    return jsonify({"tiles": result})


@dashboard_bp.route("/v1/dashboards/<dashboard_id>/tiles", methods=["POST"])
@require_auth
@_signals.announces_route("dashboard", "updated", ids=("dashboard_id",), aspect="tiles")
async def add_tile(current_user, dashboard_id):
    """
    Add a tile to a dashboard from an existing component render payload
    (e.g. user pinned a chart from the chat).
    Body: { title, component_id, spec, html, source_prompt, source_session_id,
            profile_id, profile_tag, tile_width, tile_height }
    """
    _gate = _assert_component_available("dashboard", current_user.id)
    if _gate:
        return _gate
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    now = _now()

    with _conn() as conn:
        dash = conn.execute(
            "SELECT id FROM dashboards WHERE id=? AND user_id=?",
            (dashboard_id, user_uuid),
        ).fetchone()

        if not dash:
            return jsonify({"error": "Dashboard not found"}), 404

        tile_width = max(1, min(12, int(body.get("tile_width") or 6)))
        tile_height = max(1, int(body.get("tile_height") or 4))

        # Auto-position
        tiles = conn.execute(
            "SELECT pos_col, pos_row, tile_width, tile_height FROM dashboard_tiles WHERE dashboard_id=?",
            (dashboard_id,),
        ).fetchall()
        pos_col, pos_row = _auto_position(tiles, tile_width)

        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM dashboard_tiles WHERE dashboard_id=?",
            (dashboard_id,),
        ).fetchone()[0]

        spec_raw = body.get("spec") or {}
        if isinstance(spec_raw, dict):
            spec_raw = json.dumps(spec_raw)

        tile_id = f"tile-{uuid.uuid4().hex[:12]}"

        # Stamp mcp_tool_name/mcp_args_json so the tile can refresh its data
        # directly via MCP (bolt icon) instead of an LLM re-run. CRITICAL: bind
        # the stamp to the SPECIFIC turn that produced this visual (source_turn_id),
        # not the session's most-recent data turn.
        #
        # There is intentionally NO session-scoped fallback. An empty source_turn_id
        # means we cannot confidently attribute the visual to a single turn, and
        # session-scoped stamping (`_mcp_stamp_for_session`) is KNOWN-CONTAMINATING:
        # it returns the newest qualifying data tool in the session, so an Ideate
        # tile pinned (with an empty turn id) after an Optimize `base_readQuery`
        # turn would inherit that tool and show a wrong direct-refresh bolt whose
        # replay runs the Optimize query. When the turn is unknown we leave the tile
        # unstamped → safe LLM re-run. The frontend now derives source_turn_id at
        # click time (turn finalized), so legitimate bolts are still stamped.
        source_session_id = body.get("source_session_id") or ""
        source_turn_id = body.get("source_turn_id")
        if source_turn_id not in (None, ""):
            _mcp_tool_name, _mcp_args_json = await _mcp_stamp_for_turn(
                source_session_id, user_uuid, source_turn_id
            )
        else:
            _mcp_tool_name, _mcp_args_json = None, None
        if _mcp_tool_name:
            logger.info(
                f"[dashboard_routes] Stamping mcp_tool_name={_mcp_tool_name!r} on tile {tile_id} "
                f"(turn={source_turn_id!r})"
            )

        try:
            _source_turn_id_int = int(source_turn_id) if source_turn_id not in (None, "") else None
        except (TypeError, ValueError):
            _source_turn_id_int = None
        conn.execute(
            """INSERT INTO dashboard_tiles
               (id, dashboard_id, user_id, title, component_id,
                spec, html, source_prompt, source_session_id, source_turn_id,
                profile_id, profile_tag,
                pos_col, pos_row, tile_width, tile_height,
                sort_order, refresh_status, last_refreshed, created_at, updated_at,
                mcp_tool_name, mcp_args_json)
               VALUES (?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?,
                       ?, ?,
                       ?, ?, ?, ?,
                       ?, 'ok', ?, ?, ?,
                       ?, ?)""",
            (
                tile_id, dashboard_id, user_uuid,
                body.get("title") or "Untitled",
                body.get("component_id") or "chart",
                spec_raw,
                body.get("html") or "",
                body.get("source_prompt") or "",
                source_session_id,
                _source_turn_id_int,
                body.get("profile_id") or "",
                body.get("profile_tag") or "",
                pos_col, pos_row, tile_width, tile_height,
                max_order + 1, now, now, now,
                _mcp_tool_name, _mcp_args_json,
            ),
        )
        conn.execute(
            "UPDATE dashboards SET updated_at=? WHERE id=?",
            (now, dashboard_id),
        )
        conn.commit()

    return jsonify({"tile_id": tile_id, "dashboard_id": dashboard_id, "created_at": now}), 201


@dashboard_bp.route("/v1/dashboards/<dashboard_id>/tiles/<tile_id>", methods=["PUT"])
@operation("dashboards.tile_update", effect="write", scope="own",
           summary="Rename a tile, move it or resize it",
           path={"dashboard_id": "the dashboard (dashboards.list lists them)", "tile_id": "the tile (dashboards.tiles lists them)"},
           body={"title": "its title", "pos_col": Arg("integer", "column (0–11)"), "pos_row": Arg("integer", "row"),
                 "tile_width": Arg("integer", "width in columns (1–12)"), "tile_height": Arg("integer", "height in rows")},
           notes="What a tile shows and which question it asks its agent are the owner's to change.")
@require_auth
@_signals.announces_route("dashboard", "updated", ids=("dashboard_id",), aspect="tiles")
async def update_tile(current_user, dashboard_id, tile_id):
    """Update tile metadata and/or layout."""
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    now = _now()

    with _conn() as conn:
        tile = conn.execute(
            "SELECT id FROM dashboard_tiles WHERE id=? AND dashboard_id=? AND user_id=?",
            (tile_id, dashboard_id, user_uuid),
        ).fetchone()

        if not tile:
            return jsonify({"error": "Tile not found"}), 404

        updates = {}
        for field in ("title", "component_id", "html", "source_prompt",
                      "profile_id", "profile_tag", "pos_col", "pos_row",
                      "tile_width", "tile_height", "sort_order", "last_refreshed",
                      "refresh_status", "mcp_args_json"):
            if field in body:
                updates[field] = body[field]

        if "spec" in body:
            spec = body["spec"]
            updates["spec"] = json.dumps(spec) if isinstance(spec, dict) else spec

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values()) + [now, tile_id, dashboard_id, user_uuid]
            conn.execute(
                f"UPDATE dashboard_tiles SET {set_clause}, updated_at=? WHERE id=? AND dashboard_id=? AND user_id=?",
                values,
            )
            conn.execute(
                "UPDATE dashboards SET updated_at=? WHERE id=? AND user_id=?",
                (now, dashboard_id, user_uuid),
            )
            conn.commit()

    return jsonify({"status": "ok", "tile_id": tile_id, "updated_at": now})


@dashboard_bp.route("/v1/dashboards/<dashboard_id>/tiles/<tile_id>", methods=["DELETE"])
@operation("dashboards.tile_delete", effect="write", scope="own",
           summary="Remove a tile from a dashboard", path={"dashboard_id": "the dashboard (dashboards.list lists them)", "tile_id": "the tile (dashboards.tiles lists them)"})
@require_auth
@_signals.announces_route("dashboard", "updated", ids=("dashboard_id",), aspect="tiles")
async def delete_tile(current_user, dashboard_id, tile_id):
    """Delete a tile from a dashboard."""
    user_uuid = current_user.id
    with _conn() as conn:
        tile = conn.execute(
            "SELECT id FROM dashboard_tiles WHERE id=? AND dashboard_id=? AND user_id=?",
            (tile_id, dashboard_id, user_uuid),
        ).fetchone()

        if not tile:
            return jsonify({"error": "Tile not found"}), 404

        conn.execute(
            "DELETE FROM dashboard_tiles WHERE id=? AND dashboard_id=? AND user_id=?",
            (tile_id, dashboard_id, user_uuid),
        )
        conn.execute(
            "UPDATE dashboards SET updated_at=? WHERE id=?",
            (_now(), dashboard_id),
        )
        conn.commit()

    return jsonify({"status": "deleted", "tile_id": tile_id})



def _inject_fresh_data_into_spec(existing_spec: dict, html_result: str) -> str | None:
    """Replace only the data array in an existing G2Plot spec with rows parsed from
    a fresh HTML table.  Returns the updated JSON string, or None if parsing fails
    or the column names no longer match the spec's field mappings.

    Preserves ALL visual config (label type/style, legend, radius, colors, etc.)
    so MCP-only refresh never changes how the chart looks — only the values.
    """
    import re
    from html.parser import HTMLParser

    class _TE(HTMLParser):
        def __init__(self):
            super().__init__()
            self._stack: list = []; self._headers: list = []; self._rows: list = []
            self._cur_row: list = []; self._cur_cell: str = ""
        def handle_starttag(self, tag, attrs):
            self._stack.append(tag)
            if tag in ('th', 'td'): self._cur_cell = ""
            elif tag == 'tr' and 'tbody' in self._stack: self._cur_row = []
        def handle_endtag(self, tag):
            if self._stack and self._stack[-1] == tag: self._stack.pop()
            if tag == 'th': self._headers.append(self._cur_cell.strip())
            elif tag == 'td': self._cur_row.append(self._cur_cell.strip())
            elif tag == 'tr' and self._cur_row:
                self._rows.append(self._cur_row[:]); self._cur_row = []
        def handle_data(self, data):
            if self._stack and self._stack[-1] in ('th', 'td'): self._cur_cell += data

    te = _TE()
    try:
        te.feed(html_result)
    except Exception:
        return None

    columns = te._headers
    rows    = [r for r in te._rows if r]
    if not columns or not rows:
        return None

    opts = existing_spec.get("options", {})
    chart_type = existing_spec.get("type", "")

    # Collect the field names the spec references
    used_fields: set[str] = set()
    for fld in ("angleField", "colorField", "xField", "yField", "seriesField"):
        v = opts.get(fld)
        if v: used_fields.add(v)

    # If column names don't overlap with spec fields, the schema changed — bail.
    if used_fields and not used_fields.intersection(set(columns)):
        return None

    def _parse_num(v):
        try: return float(re.sub(r'[$,€£%\s]', '', str(v)))
        except (ValueError, TypeError): return 0.0

    def _is_numeric_col(vals):
        clean = [re.sub(r'[$,€£%\s]', '', v).strip() for v in vals if v]
        return bool(clean) and all(re.match(r'^-?\d+\.?\d*$', v) for v in clean)

    col_vals = [[r[i] for r in rows if i < len(r)] for i in range(len(columns))]
    numeric_flags = [_is_numeric_col(cv) for cv in col_vals]

    data = []
    for row in rows:
        obj = {}
        for i, col in enumerate(columns):
            raw = row[i] if i < len(row) else ""
            obj[col] = _parse_num(raw) if numeric_flags[i] else raw
        data.append(obj)

    # Sort descending by the numeric field (keeps ranking charts consistent)
    angle_or_y = opts.get("angleField") or opts.get("yField") or opts.get("xField")
    if angle_or_y and chart_type not in ("Line",):
        data.sort(key=lambda x: x.get(angle_or_y, 0) if isinstance(x.get(angle_or_y), (int, float)) else 0, reverse=True)
        if chart_type == "Bar":
            data = list(reversed(data))

    new_spec = json.loads(json.dumps(existing_spec))  # deep copy
    new_spec.setdefault("options", {})["data"] = data
    return json.dumps(new_spec)


def _smart_viz_spec(tile_title: str, html_result: str, chart_hint: str = "") -> dict:
    """
    Parse an HTML table result and build a G2Plot spec for intelligent visualization.

    Returns {"spec": json_str} with a G2Plot-compatible spec, or {"spec": "{}"} if no
    table is found (canvas renderer falls back to displaying tile.html as-is).

    Chart-type selection (hint keyword always wins):
      temporal + numeric  → Line
      2-col cat+num ≤7r   → Pie    (part-of-whole)
      2-col cat+num ≤14r  → Column (ranked comparison)
      2-col cat+num >14r  → Bar    (many categories)
      cat + num(s)        → Column
      single row          → KPI    (HTML passthrough)
      default             → Column
    """
    import re
    from html.parser import HTMLParser

    class _TableExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self._stack: list = []
            self._headers: list = []
            self._rows: list = []
            self._cur_row: list = []
            self._cur_cell: str = ""

        def handle_starttag(self, tag, attrs):
            self._stack.append(tag)
            if tag in ('th', 'td'):
                self._cur_cell = ""
            elif tag == 'tr' and 'tbody' in self._stack:
                self._cur_row = []

        def handle_endtag(self, tag):
            if self._stack and self._stack[-1] == tag:
                self._stack.pop()
            if tag == 'th':
                self._headers.append(self._cur_cell.strip())
            elif tag == 'td':
                self._cur_row.append(self._cur_cell.strip())
            elif tag == 'tr' and self._cur_row:
                self._rows.append(self._cur_row[:])
                self._cur_row = []

        def handle_data(self, data):
            if self._stack and self._stack[-1] in ('th', 'td'):
                self._cur_cell += data

    extractor = _TableExtractor()
    try:
        extractor.feed(html_result)
    except Exception:
        return {"spec": "{}"}

    columns = extractor._headers
    rows    = [r for r in extractor._rows if r]
    if not columns or not rows:
        return {"spec": "{}"}

    _DATE_PATS = [
        r'^\d{4}-\d{2}-\d{2}',
        r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)',
        r'^\d{4}Q\d', r'^Q[1-4]', r'^\d{4}$', r'^\d{2}/\d{4}$',
    ]

    def _is_numeric(vals):
        clean = [re.sub(r'[$,€£%\s]', '', v).strip() for v in vals if v]
        return bool(clean) and all(re.match(r'^-?\d+\.?\d*$', v) for v in clean)

    def _is_temporal(vals):
        sample = [v for v in vals[:5] if v]
        return bool(sample) and any(re.match(p, sample[0], re.I) for p in _DATE_PATS)

    def _parse_num(v):
        try:
            return float(re.sub(r'[$,€£%,\s]', '', str(v)))
        except (ValueError, TypeError):
            return 0.0

    col_vals = [[r[i] for r in rows if i < len(r)] for i in range(len(columns))]
    types = []
    for vals in col_vals:
        if _is_temporal(vals):  types.append('temporal')
        elif _is_numeric(vals): types.append('numeric')
        else:                   types.append('categorical')

    title_lower = (tile_title + " " + chart_hint).lower()
    hint = None
    if re.search(r'pie|donut|share|portion|breakdown', title_lower):                         hint = 'Pie'
    elif re.search(r'line|trend|over.?time|history|growth|monthly|weekly|daily|quarterly|yearly', title_lower): hint = 'Line'
    elif re.search(r'scatter|correlation|vs\.?\s|versus', title_lower):                      hint = 'Scatter'
    elif re.search(r'bar|column|compar|ranking|comparison', title_lower):                    hint = 'Column'

    num_cols  = sum(1 for t in types if t == 'numeric')
    cat_cols  = sum(1 for t in types if t == 'categorical')
    time_cols = sum(1 for t in types if t == 'temporal')
    row_count = len(rows)

    if hint:
        chart_type = hint
    elif row_count == 1 and num_cols >= 1:
        return {"spec": "{}"}  # KPI — keep HTML as-is
    elif time_cols >= 1 and num_cols >= 1:
        chart_type = 'Line'
    elif len(columns) == 2 and cat_cols == 1 and num_cols == 1:
        if row_count <= 7:    chart_type = 'Pie'
        elif row_count <= 14: chart_type = 'Column'
        else:                 chart_type = 'Bar'
    elif cat_cols >= 1 and num_cols >= 1:
        chart_type = 'Column'
    else:
        chart_type = 'Column'

    VIZ_COLORS = ['#F15F22','#9333ea','#3b82f6','#4ade80',
                  '#f59e0b','#ec4899','#06b6d4','#84cc16']
    cat_col  = next((columns[i] for i, t in enumerate(types) if t == 'categorical'), columns[0])
    num_col  = next((columns[i] for i, t in enumerate(types) if t == 'numeric'),     columns[-1])
    time_col = next((columns[i] for i, t in enumerate(types) if t == 'temporal'),    columns[0])

    data = []
    for row in rows:
        obj = {}
        for i, col in enumerate(columns):
            raw = row[i] if i < len(row) else ""
            obj[col] = _parse_num(raw) if types[i] == 'numeric' else raw
        data.append(obj)

    if chart_type in ('Pie', 'Column', 'Bar') and num_col:
        data.sort(key=lambda x: x.get(num_col, 0), reverse=True)

    label_style = {"fontSize": 11, "fill": "rgba(255,255,255,0.72)", "fontFamily": "inherit"}

    if chart_type == 'Pie':
        spec = {
            "type": "Pie",
            "options": {
                "data": data, "angleField": num_col, "colorField": cat_col,
                "color": VIZ_COLORS, "radius": 0.76, "innerRadius": 0.46,
                "label": {"type": "spider", "content": "{name}\n{percentage}", "style": label_style},
                "legend": {"position": "bottom", "flipPage": False},
                "interactions": [{"type": "element-active"}],
                "pieStyle": {"lineWidth": 2, "stroke": "rgba(0,0,0,0.2)"},
            },
        }
    elif chart_type == 'Bar':
        spec = {
            "type": "Bar",
            "options": {
                "data": list(reversed(data)), "xField": num_col, "yField": cat_col,
                "color": VIZ_COLORS[0], "barStyle": {"radius": [0, 4, 4, 0]},
                "label": {"position": "right", "style": label_style},
                "tooltip": {"fields": [cat_col, num_col]},
            },
        }
    elif chart_type == 'Line':
        spec = {
            "type": "Line",
            "options": {
                "data": data, "xField": time_col, "yField": num_col,
                "color": VIZ_COLORS[0], "lineStyle": {"lineWidth": 2.5},
                "point": {"size": 4}, "smooth": True,
                "tooltip": {"fields": [time_col, num_col]},
            },
        }
    else:  # Column
        spec = {
            "type": "Column",
            "options": {
                "data": data, "xField": cat_col, "yField": num_col,
                "color": VIZ_COLORS[0], "columnStyle": {"radius": [6, 6, 0, 0]},
                "label": {"position": "top", "style": label_style},
                "xAxis": {"label": {"autoRotate": True}},
                "tooltip": {"fields": [cat_col, num_col]},
            },
        }

    logger.debug(f"[SmartViz] title={tile_title!r} → chart_type={chart_type}, rows={row_count}")
    return {"spec": json.dumps(spec)}


@dashboard_bp.route("/v1/dashboards/<dashboard_id>/tiles/<tile_id>/refresh", methods=["POST"])
@operation("dashboards.tile_refresh", effect="write", scope="own",
           summary="Refresh a tile: its own agent answers its question again, under its own authority, in the tile's conversation",
           path={"dashboard_id": "the dashboard (dashboards.list lists them)", "tile_id": "the tile (dashboards.tiles lists them)"},
           notes="It runs an agent turn and spends tokens. The answering agent is the tile's own — never chosen here.")
@require_auth
@_signals.announces_route("dashboard", "refreshed", ids=("dashboard_id",), aspect="tiles")
async def refresh_tile(current_user, dashboard_id, tile_id):
    """Refresh a tile by re-executing its source_prompt inside the tile's original
    source_session_id.  This is a fully-featured REST execution — the query
    appears in the session history, fires SSE notifications to the UI exactly
    like a normal /v1/sessions/<id>/query call, and the task is pollable via
    /v1/tasks/<task_id>.

    Profile override priority: request body > tile.profile_id > user default.
    """
    import asyncio
    import copy

    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}

    with _conn() as conn:
        tile = conn.execute(
            "SELECT * FROM dashboard_tiles WHERE id=? AND dashboard_id=? AND user_id=?",
            (tile_id, dashboard_id, user_uuid),
        ).fetchone()

        if not tile:
            return jsonify({"error": "Tile not found"}), 404

        tile = _row_to_dict(tile)

    source_prompt = (tile.get("source_prompt") or "").strip()
    source_session_id = tile.get("source_session_id") or ""

    # Guard against degenerate source prompts. A stale pin (created before the
    # capture fix) could store the "You"/"Assistant" author label instead of the
    # real query — refreshing that silently re-runs junk (the agent just introduces
    # itself). Fail loudly so the UI tells the user to re-pin / edit the query,
    # rather than overwriting the tile with a useless greeting.
    _AUTHOR_LABELS = {"you", "assistant"}
    if not source_prompt or source_prompt.lower() in _AUTHOR_LABELS:
        logger.warning(
            f"[DashboardRoutes] refresh blocked: tile {tile_id} has an invalid "
            f"source_prompt {source_prompt!r}"
        )
        return jsonify({
            "error": "This tile has no valid source query to refresh from — it was "
                     "likely pinned before a fix. Re-pin the visual, or use the tile "
                     "menu (⋮) → Edit query to set its prompt.",
            "code": "invalid_source_prompt",
        }), 422

    from trusted_data_agent.core import session_manager as _sm
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.core.config import APP_STATE
    from trusted_data_agent.core.utils import generate_task_id
    from trusted_data_agent.agent import execution_service

    # Validate that the source session exists for this user.
    # Fall back to a fresh session if it was deleted / not found.
    session_id = source_session_id
    if session_id:
        session_data = await _sm.get_session(user_uuid, session_id)
        if not session_data:
            logger.warning(
                f"[DashboardRoutes] refresh: source_session_id {session_id!r} not found "
                f"for user {user_uuid} — creating a new session"
            )
            session_id = ""

    if not session_id:
        from trusted_data_agent.core.config import APP_CONFIG
        session_id = await _sm.create_session(
            user_uuid=user_uuid,
            provider=APP_CONFIG.CURRENT_PROVIDER,
            llm_instance=APP_STATE.get("llm"),
            charting_intensity="medium",
            is_temporary=False,
            profile_id=tile.get("profile_id") or "",
        )

    # Resolve profile override: body > tile > user default
    config_manager = get_config_manager()
    profile_id_override = (
        body.get("profile_id")
        or tile.get("profile_id")
        or None
    )
    if profile_id_override:
        profiles = config_manager.get_profiles(user_uuid)
        if not any(p.get("profile_id") == profile_id_override for p in profiles):
            profile_id_override = None  # unknown profile — fall back to session default

    # PILLAR 3 — presentation-locked refresh.  A chart tile is a single
    # visualization, but re-running the free-form source prompt produces a FULL
    # conversational answer (prose + chart + possibly more), and reconciling
    # "which part of that answer is this tile" is inherently fragile.  For chart
    # tiles without a deterministic MCP data source, scope the re-run so the
    # answer IS the chart — there is no narrative to scrape back out.
    # (MCP-stamped tiles never reach here; they refresh via /refresh-mcp.)
    effective_prompt = source_prompt
    if tile.get("component_id") == "chart" and not tile.get("mcp_tool_name"):
        _chart_type = ""
        try:
            _ts = tile.get("spec") or "{}"
            _ts = json.loads(_ts) if isinstance(_ts, str) else (_ts or {})
            _chart_type = (_ts.get("type") or "").strip()
        except Exception:
            pass
        _viz_word = f"{_chart_type} chart" if _chart_type else "chart"
        effective_prompt = (
            f"{source_prompt}\n\n"
            f"[Dashboard tile refresh] Reproduce ONLY the {_viz_word} for this "
            f"request using current data. Return just the visualization — no "
            f"narrative text, no bullet lists, no explanation."
        )

    # Mark tile as refreshing
    now = _now()
    with _conn() as conn:
        conn.execute(
            "UPDATE dashboard_tiles SET refresh_status='refreshing', updated_at=? WHERE id=?",
            (now, tile_id),
        )
        conn.commit()

    task_id = generate_task_id()
    from trusted_data_agent.core.config import prune_background_tasks
    prune_background_tasks()
    APP_STATE.setdefault("background_tasks", {})[task_id] = {
        "task_id": task_id,
        "user_uuid": user_uuid,
        "session_id": session_id,
        "profile_id_override": profile_id_override,
        "status": "pending",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "events": [],
        "intermediate_data": [],
        "result": None,
    }

    async def event_handler(event_data, event_type):
        """Mirror the REST /query event_handler: persist to task + fan out to UI."""
        task_entry = APP_STATE["background_tasks"].get(task_id)
        try:
            sanitized = json.loads(json.dumps(event_data, default=str))
        except Exception:
            sanitized = {}

        if task_entry:
            task_entry["events"].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_data": sanitized,
                "event_type": event_type,
            })
            if event_type == "tool_result" and isinstance(sanitized, dict):
                details = sanitized.get("details", {})
                if isinstance(details, dict) and details.get("status") == "success" and "results" in details:
                    task_entry["intermediate_data"].append({
                        "tool_name": details.get("metadata", {}).get("tool_name", "unknown_tool"),
                        "data": details["results"],
                    })
            task_entry["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Fan out to the user's SSE notification queues (same as REST /query)
        notification_queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
        if notification_queues:
            canonical = copy.deepcopy(sanitized)
            if "type" not in canonical:
                canonical["type"] = event_type or "notification"

            if event_type == "status_indicator_update":
                notification = {"type": "status_indicator_update", "payload": canonical}
            elif event_type == "session_name_update":
                notification = {"type": "session_name_update", "payload": canonical}
            else:
                notification = {
                    "type": "rest_task_update",
                    "payload": {
                        "task_id": task_id,
                        "session_id": session_id,
                        "event": canonical,
                    },
                }
            for queue in notification_queues:
                asyncio.create_task(queue.put(notification))

    async def background_wrapper():
        task_entry = APP_STATE["background_tasks"].get(task_id)
        final_result_payload = None
        try:
            if task_entry:
                task_entry["status"] = "processing"

            final_result_payload = await execution_service.run_agent_execution(
                user_uuid=user_uuid,
                session_id=session_id,
                user_input=effective_prompt,
                event_handler=event_handler,
                source="dashboard_refresh",
                task_id=task_id,
                profile_override_id=profile_id_override,
            )

            # Re-fetch after execution (execution_service may replace the dict)
            task_entry = APP_STATE["background_tasks"].get(task_id)
            if task_entry is None:
                APP_STATE["background_tasks"][task_id] = {}
                task_entry = APP_STATE["background_tasks"][task_id]
            task_entry["status"] = "complete"
            task_entry["result"] = final_result_payload
            task_entry["user_uuid"] = user_uuid
            task_entry["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Extract the freshest dashboard component_render event (view=visualization)
            # to update the tile with the new data.
            all_events = task_entry.get("events", [])
            viz_events = [
                e for e in all_events
                if e.get("event_type") == "component_render"
                and isinstance(e.get("event_data"), dict)
                and e["event_data"].get("component_id") == "dashboard"
                and isinstance(e["event_data"].get("spec"), dict)
                and e["event_data"]["spec"].get("view") == "visualization"
            ]

            if viz_events:
                viz_spec = viz_events[-1]["event_data"]["spec"]
                fresh_html = viz_spec.get("content") or ""
                if fresh_html:
                    existing_spec = {}
                    try:
                        raw = tile.get("spec") or "{}"
                        existing_spec = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        pass

                    spec_str = None
                    if existing_spec.get("type"):
                        spec_str = _inject_fresh_data_into_spec(existing_spec, fresh_html)
                    if not spec_str:
                        chart_hint = viz_spec.get("chart_type") or existing_spec.get("type", "")
                        spec_str = _smart_viz_spec(
                            tile.get("title", ""), fresh_html, chart_hint
                        ).get("spec") or "{}"

                    with _conn() as conn:
                        conn.execute(
                            "UPDATE dashboard_tiles SET html=?, spec=?, refresh_status='ok',"
                            " last_refreshed=?, updated_at=? WHERE id=?",
                            (fresh_html, spec_str, _now(), _now(), tile_id),
                        )
                        conn.commit()
                    logger.info(
                        f"[DashboardRoutes] tile {tile_id} refreshed via session "
                        f"{session_id}, html_len={len(fresh_html)}"
                    )
            else:
                # No visualize event — fall back to final_answer HTML if available
                tile_html = ""
                if isinstance(final_result_payload, dict):
                    tile_html = (
                        final_result_payload.get("final_answer")
                        or final_result_payload.get("final_answer_text")
                        or ""
                    )
                if tile_html:
                    existing_spec = {}
                    try:
                        raw = tile.get("spec") or "{}"
                        existing_spec = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        pass

                    # PILLAR 2 — component-aware extraction. A tile pinned from a
                    # chat chart carries component_id='chart'; its fresh visual is
                    # embedded as <div data-component-id="chart" data-spec='{type,
                    # options:{data,...}}'>.  Adopt that spec verbatim so the
                    # refreshed tile is the SAME chart with new values, rather than
                    # trying (and failing) to derive a chart from the prose answer.
                    _chart_handled = False
                    if tile.get("component_id") == "chart":
                        import re as _re_chart
                        import html as _html_mod
                        _cm = _re_chart.search(
                            r"""data-component-id=["']chart["'][^>]*?\sdata-spec=(?:'([^']*)'|"([^"]*)")""",
                            tile_html,
                        )
                        if _cm:
                            try:
                                _chart_spec = json.loads(
                                    _html_mod.unescape(_cm.group(1) or _cm.group(2) or "")
                                )
                            except Exception:
                                _chart_spec = None
                            if isinstance(_chart_spec, dict) and _chart_spec.get("type"):
                                with _conn() as conn:
                                    conn.execute(
                                        "UPDATE dashboard_tiles SET spec=?, refresh_status='ok',"
                                        " last_refreshed=?, updated_at=? WHERE id=?",
                                        (json.dumps(_chart_spec), _now(), _now(), tile_id),
                                    )
                                    conn.commit()
                                _chart_handled = True
                                logger.info(
                                    f"[DashboardRoutes] tile {tile_id} chart refreshed "
                                    f"from embedded chart spec ({_chart_spec.get('type')})"
                                )

                    # PILLAR 1 — non-destructive. For a chart tile that already had
                    # a visualization, never fall through to prose-derivation (which
                    # would overwrite the good chart with an empty/garbage spec). If
                    # the embedded chart spec could not be recovered, leave the tile's
                    # prior spec untouched and just stamp the refresh time.
                    if not _chart_handled and tile.get("component_id") == "chart" and existing_spec.get("type"):
                        with _conn() as conn:
                            conn.execute(
                                "UPDATE dashboard_tiles SET refresh_status='ok',"
                                " last_refreshed=?, updated_at=? WHERE id=?",
                                (_now(), _now(), tile_id),
                            )
                            conn.commit()
                        _chart_handled = True
                        logger.warning(
                            f"[DashboardRoutes] tile {tile_id} chart refresh produced no "
                            f"embedded chart — preserved prior visualization"
                        )

                    # For IDEAT/conversation_with_tools tiles the component_render
                    # event is not emitted (inline render_target only), so viz_events
                    # is always empty.  Recover the data by extracting the embedded
                    # data-spec attribute from the final-answer HTML.  Skipped when a
                    # chart tile was already handled above (Pillars 1/2).
                    if not _chart_handled:
                        import re as _re_emb
                        fresh_html_emb = ""
                        chart_hint_emb = ""
                        _m = _re_emb.search(
                            r"""data-component-id=["']dashboard["'][^>]*data-spec='([^']+)'"""
                            r"""|data-spec='([^']+)'[^>]*data-component-id=["']dashboard["']""",
                            tile_html,
                        )
                        if _m:
                            try:
                                _raw = _m.group(1) or _m.group(2)
                                _emb = json.loads(_raw)
                                fresh_html_emb = _emb.get("content") or ""
                                chart_hint_emb = _emb.get("chart_type") or ""
                            except Exception:
                                pass

                        spec_str = None
                        if fresh_html_emb:
                            # Rebuild chart from extracted table + chart_type hint
                            if existing_spec.get("type"):
                                spec_str = _inject_fresh_data_into_spec(existing_spec, fresh_html_emb)
                            if not spec_str:
                                hint = chart_hint_emb or existing_spec.get("type", "")
                                spec_str = _smart_viz_spec(tile.get("title", ""), fresh_html_emb, hint).get("spec") or "{}"
                        else:
                            if existing_spec.get("type"):
                                spec_str = _inject_fresh_data_into_spec(existing_spec, tile_html)
                            if not spec_str:
                                chart_hint = existing_spec.get("type", "")
                                spec_str = _smart_viz_spec(tile.get("title", ""), tile_html, chart_hint).get("spec") or "{}"

                        # Use the extracted table as html when available so the canvas
                        # tile has clean content; otherwise keep the full answer HTML.
                        store_html = fresh_html_emb or tile_html
                        with _conn() as conn:
                            conn.execute(
                                "UPDATE dashboard_tiles SET html=?, spec=?, refresh_status='ok',"
                                " last_refreshed=?, updated_at=? WHERE id=?",
                                (store_html, spec_str, _now(), _now(), tile_id),
                            )
                            conn.commit()
                else:
                    with _conn() as conn:
                        conn.execute(
                            "UPDATE dashboard_tiles SET refresh_status='ok',"
                            " last_refreshed=?, updated_at=? WHERE id=?",
                            (_now(), _now(), tile_id),
                        )
                        conn.commit()

            # Send rest_task_complete notification (same as REST /query)
            notification_queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
            if notification_queues and final_result_payload:
                try:
                    session_data = await _sm.get_session(user_uuid, session_id)
                    profile_tag = session_data.get("profile_tag") if session_data else None
                except Exception:
                    profile_tag = None

                completion = {
                    "type": "rest_task_complete",
                    "payload": {
                        "task_id": task_id,
                        "session_id": session_id,
                        "turn_id": final_result_payload.get("turn_id") if isinstance(final_result_payload, dict) else None,
                        "user_input": source_prompt,
                        "final_answer": final_result_payload.get("final_answer") if isinstance(final_result_payload, dict) else None,
                        "profile_tag": profile_tag,
                        "source": "dashboard_refresh",
                    },
                }
                for queue in notification_queues:
                    asyncio.create_task(queue.put(completion))

        except Exception as exc:
            logger.error(f"[DashboardRoutes] refresh task {task_id} failed: {exc}", exc_info=True)
            task_entry = APP_STATE["background_tasks"].get(task_id, {})
            task_entry["status"] = "error"
            task_entry["result"] = {"error": str(exc)}
            task_entry["user_uuid"] = user_uuid
            task_entry["last_updated"] = datetime.now(timezone.utc).isoformat()
            APP_STATE["background_tasks"][task_id] = task_entry
            with _conn() as conn:
                conn.execute(
                    "UPDATE dashboard_tiles SET refresh_status='error', updated_at=? WHERE id=?",
                    (_now(), tile_id),
                )
                conn.commit()
        finally:
            if task_id in APP_STATE.get("active_tasks", {}):
                del APP_STATE["active_tasks"][task_id]

    task_object = asyncio.create_task(background_wrapper())
    APP_STATE.setdefault("active_tasks", {})[task_id] = task_object

    return jsonify({
        "task_id": task_id,
        "session_id": session_id,
        "tile_id": tile_id,
        "dashboard_id": dashboard_id,
        "status_url": f"/api/v1/tasks/{task_id}",
    })


@dashboard_bp.route("/v1/dashboards/<dashboard_id>/tiles/<tile_id>/refresh-mcp", methods=["POST"])
@require_auth
@_signals.announces_route("dashboard", "refreshed", ids=("dashboard_id",), aspect="tiles")
async def refresh_tile_mcp(current_user, dashboard_id, tile_id):
    """Direct MCP tool replay — no LLM involved.
    Re-executes the stored mcp_tool_name with mcp_args_json, rebuilds the G2Plot
    spec preserving the original chart type, persists the result, and returns
    { html, spec } synchronously so the frontend can update without polling.
    """
    user_uuid = current_user.id

    with _conn() as conn:
        tile = conn.execute(
            "SELECT * FROM dashboard_tiles WHERE id=? AND dashboard_id=? AND user_id=?",
            (tile_id, dashboard_id, user_uuid),
        ).fetchone()

    if not tile:
        return jsonify({"error": "Tile not found"}), 404

    tile = _row_to_dict(tile)
    mcp_tool_name = tile.get("mcp_tool_name")
    mcp_args_json  = tile.get("mcp_args_json")

    if not mcp_tool_name:
        return jsonify({"error": "Tile has no MCP tool recorded — use regular refresh instead"}), 422

    try:
        mcp_args = json.loads(mcp_args_json) if mcp_args_json else {}
    except Exception:
        mcp_args = {}

    # Mark as refreshing
    with _conn() as conn:
        conn.execute(
            "UPDATE dashboard_tiles SET refresh_status='refreshing', updated_at=? WHERE id=?",
            (_now(), tile_id),
        )
        conn.commit()

    try:
        from trusted_data_agent.core.config import APP_STATE
        from trusted_data_agent.mcp_adapter import adapter as mcp_adapter

        command = {"tool_name": mcp_tool_name, "arguments": mcp_args}
        # PROP-006 (M11): replay on the server(s) of the profile the tile came from — its first
        # server, and for a multi-server agent its `<alias>__` tools and routing — never the
        # user's currently active server, which may be another profile's.
        _tile_profile = None
        try:
            if tile.get("profile_id"):
                from trusted_data_agent.core.config_manager import get_config_manager as _gcm
                _tile_profile = _gcm().get_profile(tile["profile_id"], user_uuid)
        except Exception:  # noqa: BLE001
            _tile_profile = None
        if _tile_profile and _tile_profile.get("mcpServerId"):
            from trusted_data_agent.agent.source_routing import profile_call_scope
            with profile_call_scope(_tile_profile, user_uuid, APP_STATE) as _state:
                tool_result, _, _ = await mcp_adapter.invoke_mcp_tool(
                    _state, command, user_uuid=user_uuid, session_id=None,
                )
        else:
            tool_result, _, _ = await mcp_adapter.invoke_mcp_tool(
                APP_STATE, command, user_uuid=user_uuid, session_id=None,
            )

        # Normalise result to HTML string — same logic as _content_to_html() in handler.py.
        # invoke_mcp_tool returns {"status":"success","results":[{...}],"metadata":{...}};
        # we must unwrap the envelope and convert rows to a <table> before _smart_viz_spec.
        from components.builtin.dashboard.handler import _content_to_html
        html = _content_to_html(tool_result)
        # Fallback for tools that return plain HTML or a string directly
        if not html and isinstance(tool_result, str):
            html = tool_result

        if not html:
            raise ValueError("MCP tool returned empty result")

        # Rebuild spec: preserve ALL visual config from the original tile and
        # only replace the data array.  This prevents _smart_viz_spec from
        # switching label types, legend position, center-label settings, etc.
        existing_spec = {}
        try:
            raw = tile.get("spec") or "{}"
            existing_spec = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            pass

        spec_str = None
        # Table tiles (component_id='table') must stay as tables — never auto-chart.
        is_table_tile = tile.get("component_id") == "table"
        if is_table_tile:
            spec_str = "{}"  # preserve table rendering; spec stays empty
        elif existing_spec.get("type"):
            spec_str = _inject_fresh_data_into_spec(existing_spec, html)

        if not spec_str:
            # Fallback: regenerate (schema changed or no existing spec)
            chart_type_hint = existing_spec.get("type", "")
            spec_str = _smart_viz_spec(tile.get("title", ""), html, chart_type_hint).get("spec") or "{}"

        now = _now()
        with _conn() as conn:
            conn.execute(
                "UPDATE dashboard_tiles SET html=?, spec=?, refresh_status='ok',"
                " last_refreshed=?, updated_at=? WHERE id=?",
                (html, spec_str, now, now, tile_id),
            )
            conn.commit()

        try:
            spec_obj = json.loads(spec_str)
        except Exception:
            spec_obj = {}

        logger.info(
            f"[DashboardRoutes] MCP refresh tile {tile_id}: tool={mcp_tool_name},"
            f" html_len={len(html)}, chart_type={spec_obj.get('type', 'html')}"
        )
        return jsonify({"html": html, "spec": spec_obj})

    except Exception as exc:
        logger.error(f"[DashboardRoutes] refresh_tile_mcp error: {exc}", exc_info=True)
        with _conn() as conn:
            conn.execute(
                "UPDATE dashboard_tiles SET refresh_status='error', updated_at=? WHERE id=?",
                (_now(), tile_id),
            )
            conn.commit()
        return jsonify({"error": str(exc)}), 500


@dashboard_bp.route("/v1/dashboards/<dashboard_id>/layout", methods=["PUT"])
@operation("dashboards.layout", effect="write", scope="own",
           summary="Arrange a dashboard: positions and sizes of several tiles at once",
           path={"dashboard_id": "the dashboard (dashboards.list lists them)"},
           body={"tiles": Arg("array", "one entry per tile: {id, pos_col, pos_row, tile_width, tile_height, sort_order}", required=True)})
@require_auth
@_signals.announces_route("dashboard", "updated", ids=("dashboard_id",), aspect="layout")
async def update_layout(current_user, dashboard_id):
    """
    Bulk update tile layout positions after a drag-and-drop or resize.
    Body: { tiles: [ { id, pos_col, pos_row, tile_width, tile_height, sort_order }, ... ] }
    """
    user_uuid = current_user.id
    body = await request.get_json(silent=True) or {}
    tile_updates = body.get("tiles") or []
    now = _now()

    with _conn() as conn:
        dash = conn.execute(
            "SELECT id FROM dashboards WHERE id=? AND user_id=?",
            (dashboard_id, user_uuid),
        ).fetchone()

        if not dash:
            return jsonify({"error": "Dashboard not found"}), 404

        for tu in tile_updates:
            tile_id = tu.get("id")
            if not tile_id:
                continue
            conn.execute(
                """UPDATE dashboard_tiles
                   SET pos_col=?, pos_row=?, tile_width=?, tile_height=?,
                       sort_order=?, updated_at=?
                   WHERE id=? AND dashboard_id=? AND user_id=?""",
                (
                    tu.get("pos_col", 0), tu.get("pos_row", 0),
                    tu.get("tile_width", 6), tu.get("tile_height", 4),
                    tu.get("sort_order", 0), now,
                    tile_id, dashboard_id, user_uuid,
                ),
            )

        conn.execute(
            "UPDATE dashboards SET updated_at=? WHERE id=?",
            (now, dashboard_id),
        )
        conn.commit()

    return jsonify({"status": "ok", "updated": len(tile_updates), "updated_at": now})


# ── Auto-position helper ──────────────────────────────────────────────────────

def _auto_position(tiles, new_width: int) -> tuple:
    """Simple first-fit packing: place tile after the last tile, wrap to next row."""
    if not tiles:
        return 0, 0

    tiles = [dict(t) if not isinstance(t, dict) else t for t in tiles]
    max_row_end = max(t["pos_row"] + t["tile_height"] for t in tiles)
    last_row = max(t["pos_row"] for t in tiles)
    last_row_tiles = [t for t in tiles if t["pos_row"] == last_row]
    last_col_end = max(t["pos_col"] + t["tile_width"] for t in last_row_tiles)

    if last_col_end + new_width <= 12:
        return last_col_end, last_row

    return 0, max_row_end
