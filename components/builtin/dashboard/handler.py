"""
Dashboard Component Handler.

Processes TDA_Dashboard tool calls into dashboard render payloads.
Delegates persistence to dashboard_db.py (created on first use via _ensure_tables).
"""

import json
import logging
import re
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trusted_data_agent.components.base import (
    BaseComponentHandler,
    ComponentRenderPayload,
    RenderTarget,
)

logger = logging.getLogger("quart.app")

# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_path() -> str:
    from trusted_data_agent.auth.database import DATABASE_URL
    return DATABASE_URL.replace("sqlite:///", "")


def _announce(user_uuid: str, action: str, ids=None, aspect=None) -> None:
    """The dashboard store's write seam is this handler's SQL and dashboard_routes' SQL (two
    writers, one table); both announce through the one component announcement."""
    try:
        from trusted_data_agent.core.ui_notify import announce_component_change
        announce_component_change(user_uuid, "dashboard", action, ids, aspect=aspect)
    except Exception:  # noqa: BLE001
        pass


def _conn():
    c = sqlite_util.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _esc_html(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _content_to_html(content) -> str:
    """Normalise TDA_Dashboard *content* to an HTML string.

    IDEAT/LangChain profiles pass pre-rendered HTML from the MCP tool result.
    OPTIM/Planner-Executor profiles pass the raw JSON result dict from tools
    like ``base_readQuery``:  ``[{"status":"success","results":[{...},...]}]``
    or a plain list of row dicts.  This function converts both to a
    ``<table>`` so the renderer's ``_parseHtmlTable`` always gets valid HTML.
    """
    if not content:
        return ""

    # Already an HTML string — return as-is
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("<"):
            return content
        # A markdown table anywhere in the text — the model often wraps it in prose
        # ("Here is the data:\n| a | b |") or a fenced block; the table is the content.
        md = _extract_markdown_table(stripped)
        if md is not None:
            return _table_html(md[0], md[1])
        # Might be a JSON string — try to decode it
        try:
            content = json.loads(stripped)
        except (ValueError, TypeError):
            return content  # plain text — hand back unchanged

    # Unwrap a single tool-result envelope: {"status":…, "results":[…]}
    if isinstance(content, dict):
        content = content.get("results", [content])

    # Unwrap a list of tool-result envelopes
    if isinstance(content, list):
        rows: list = []
        for item in content:
            if isinstance(item, dict):
                if "results" in item and isinstance(item["results"], list):
                    rows.extend(item["results"])
                elif "status" in item and len(item) == 1:
                    pass  # status-only sentinel — skip
                else:
                    rows.append(item)
            elif isinstance(item, (list, tuple)):
                rows.append(list(item))
        content = rows

    if not isinstance(content, list) or not content:
        return ""

    # Row dicts — the column set is the UNION in first-seen order (a sparse result must
    # not lose the columns its first row happens to omit).
    row_dicts = [r for r in content if isinstance(r, dict)]
    if row_dicts:
        cols: list = []
        for r in row_dicts:
            for c in r.keys():
                if c not in cols:
                    cols.append(c)
        return _table_html(cols, [[row.get(c) for c in cols] for row in row_dicts])

    # Row lists — [["col","col"],[v,v],…]: the first row is the header when it is all
    # text and a later row carries a number (the CSV / bare-array shape).
    row_lists = [r for r in content if isinstance(r, list)]
    if len(row_lists) >= 2 and _looks_like_header(row_lists[0], row_lists[1:]):
        return _table_html(row_lists[0], row_lists[1:])
    return ""


def _chart_type_from_text(text) -> str:
    """The chart type a user's own words ask for, or '' — word-bounded so 'airline',
    'timeline' and 'share price' are not read as chart requests."""
    q = (text or "").lower()
    if not q:
        return ""
    if re.search(r"\b(pie|donut)\b", q):
        return "pie"
    if re.search(r"\bscatter\b", q):
        return "scatter"
    if re.search(r"\b(line|trend|trends)\b|\bover.{0,6}time\b", q):
        return "line"
    if re.search(r"\b(bar|column)\b", q):
        return "bar"
    return ""


_NULL_TOKENS = {"null", "none", "nan", "n/a", "na", "nil", "(null)"}


def _norm_cell(v) -> str:
    """One cell as the renderer should see it: absent → '', a warehouse CHAR value
    unpadded, internal whitespace collapsed, the word null/None/NaN made empty so it never
    becomes a category on the axis. Numbers keep their full text (no rounding here)."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    s = " ".join(str(v).split())
    return "" if s.lower() in _NULL_TOKENS else s


_NUM_RE = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")


def _is_num_text(s: str) -> bool:
    t = re.sub(r"[\s$€£¥%,]", "", s or "")
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    return bool(_NUM_RE.match(t))


def _looks_like_header(first: list, rest: list) -> bool:
    cells = [_norm_cell(c) for c in first]
    if not cells or any(c == "" or _is_num_text(c) for c in cells):
        return False
    return any(_is_num_text(_norm_cell(c)) for r in rest for c in (r if isinstance(r, list) else []))


def _extract_markdown_table(text: str):
    """The first markdown table in `text` as (headers, rows), or None. Tolerates leading /
    trailing prose, a ``` fence, a missing alignment row, and escaped pipes."""
    lines = text.splitlines()
    block: list = []
    for line in lines + [""]:
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            block.append(s)
            continue
        if block:
            break            # the table ended
        block = []
    data_lines = [l for l in block if not re.match(r"^\|[\s|:=-]+\|$", l)]
    if len(data_lines) < 2:
        return None
    split = lambda l: [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", l.strip("|"))]
    headers = split(data_lines[0])
    rows = [split(l) for l in data_lines[1:]]
    return headers, rows


def _table_html(cols: list, rows: list) -> str:
    """One HTML table builder for every content shape — cells normalised, short rows padded
    to the header width, fully empty rows dropped."""
    width = len(cols)
    th = "".join(f"<th>{_esc_html(_norm_cell(c))}</th>" for c in cols)
    trs = ""
    for r in rows:
        cells = [_norm_cell(c) for c in list(r)[:width]] + [""] * max(0, width - len(r))
        if not any(cells):
            continue
        trs += "<tr>" + "".join(f"<td>{_esc_html(c)}</td>" for c in cells) + "</tr>"
    if not trs:
        return ""
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


# ── Handler ───────────────────────────────────────────────────────────────────

class DashboardComponentHandler(BaseComponentHandler):
    """
    Handler for the TDA_Dashboard component.

    Supports CRUD on dashboards and tiles, returning inline card payloads
    that render in chat with an "Open in Canvas" button (same UX as Flow/KG).
    """

    @property
    def component_id(self) -> str:
        return "dashboard"

    @property
    def tool_name(self) -> str:
        return "TDA_Dashboard"

    # Opt into the generic per-instance export/import contract (see base.py +
    # components/instance_portability.py). Shares the serialization + remap core
    # with flow, so the marketplace layer can reuse it identically.
    supports_instance_io = True

    async def process(
        self,
        arguments: Dict[str, Any],
        context: Dict[str, Any],
    ) -> ComponentRenderPayload:
        action = arguments.get("action", "")
        user_uuid = context.get("user_uuid", "")
        profile_id = context.get("profile_id", "")
        profile_tag = context.get("profile_tag", "")
        session_id = context.get("session_id", "")

        user_input = context.get("user_input", "")
        current_turn_number = context.get("current_turn_number")

        try:
            if action == "create":
                return await self._handle_create(arguments, user_uuid)
            elif action == "open":
                return await self._handle_open(arguments, user_uuid)
            elif action == "close":
                return self._handle_close()
            elif action == "list":
                return await self._handle_list(user_uuid)
            elif action == "add_tile":
                return await self._handle_add_tile(
                    arguments, user_uuid, session_id, profile_id, profile_tag, user_input,
                    current_turn_number,
                )
            elif action == "visualize":
                return self._handle_visualize(
                    arguments, session_id, profile_id, profile_tag, user_input,
                    current_turn_number,
                )
            elif action == "refresh":
                return await self._handle_refresh(arguments, user_uuid)
            elif action == "delete":
                return await self._handle_delete(arguments, user_uuid)
            else:
                return self._error_payload(f"Unknown action: {action!r}")
        except Exception as exc:
            logger.error(f"[DashboardHandler] action={action} error: {exc}", exc_info=True)
            return self._error_payload(str(exc))

    # ── Actions ───────────────────────────────────────────────────────────────

    async def _handle_create(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        name = args.get("name") or "My Dashboard"
        description = args.get("description") or ""
        dashboard_id = f"dash-{uuid.uuid4().hex[:12]}"
        now = _now()

        with _conn() as conn:
            conn.execute(
                """INSERT INTO dashboards
                   (id, user_id, name, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dashboard_id, user_uuid, name, description, now, now),
            )
            conn.commit()

        logger.info(f"[DashboardHandler] Created dashboard {dashboard_id} for {user_uuid}")
        _announce(user_uuid, "created", [dashboard_id])

        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "panel_open",
                "dashboard_id": dashboard_id,
                "name": name,
            },
            title=f"{name} · created · opened in the canvas",
            # an act with nothing to show in the chat (the panel opens) — no evidence row
            metadata={"tool_name": "TDA_Dashboard", "answer_surface": {"row": False}},
        )

    async def _handle_open(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        dashboard_id = args.get("dashboard_id", "")
        if not dashboard_id:
            return self._error_payload("dashboard_id is required for 'open'")

        dashboard, tiles = self._load_dashboard(dashboard_id, user_uuid)
        if dashboard is None:
            return self._error_payload(f"Dashboard {dashboard_id!r} not found")

        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "open",
                "dashboard": dict(dashboard),
                "tiles": [dict(t) for t in tiles],
            },
            title=f"{dashboard['name']} · {len(tiles)} tile{'' if len(tiles) == 1 else 's'} · opened in the canvas",
        )

    def _handle_close(self) -> ComponentRenderPayload:
        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={"view": "close"},
            title="Dashboard",
        )

    async def _handle_list(self, user_uuid: str) -> ComponentRenderPayload:
        with _conn() as conn:
            rows = conn.execute(
                """SELECT d.id, d.name, d.description, d.updated_at,
                          COUNT(t.id) AS tile_count
                   FROM dashboards d
                   LEFT JOIN dashboard_tiles t ON t.dashboard_id = d.id
                   WHERE d.user_id = ?
                   GROUP BY d.id
                   ORDER BY d.updated_at DESC""",
                (user_uuid,),
            ).fetchall()

        dashboards = [dict(r) for r in rows]

        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={"view": "list", "dashboards": dashboards},
            title=f"{len(dashboards)} dashboard{'' if len(dashboards) == 1 else 's'}",
        )

    async def _handle_add_tile(
        self,
        args: Dict,
        user_uuid: str,
        session_id: str,
        profile_id: str,
        profile_tag: str,
        user_input: str = "",
        current_turn_number: int = None,
    ) -> ComponentRenderPayload:
        dashboard_id = args.get("dashboard_id", "")
        query   = args.get("query") or ""
        # Normalise content: OPTIM passes raw JSON; IDEAT passes HTML.
        # _content_to_html handles both so all downstream logic sees HTML.
        content = _content_to_html(args.get("content") or "")

        if not dashboard_id:
            # LLM called add_tile without a dashboard_id (user never asked to pin).
            # Silently render an inline visualize card instead — the user can choose
            # a dashboard via the selector in the card footer.
            logger.info("[DashboardHandler] add_tile without dashboard_id → falling through to visualize")
            return self._handle_visualize(args, session_id, profile_id, profile_tag, user_input, current_turn_number)

        # HTML table content is always shown via the selector card so the user
        # can choose which dashboard to pin it to — never auto-add a raw table.
        if re.search(r'<table[\s>]', content, re.IGNORECASE):
            logger.info("[DashboardHandler] add_tile with table content → falling through to visualize (user selects dashboard)")
            return self._handle_visualize(args, session_id, profile_id, profile_tag, user_input, current_turn_number)

        tile_title = args.get("tile_title") or "Untitled Tile"
        logger.info(f"[DashboardHandler] add_tile args: title={tile_title!r}, query={query[:60]!r}, content_len={len(content)}, content_preview={content[:80]!r}")
        tile_width = max(1, min(12, int(args.get("tile_width") or 6)))
        tile_height = max(1, int(args.get("tile_height") or 4))

        # Status: ok immediately if LLM passed content; pending otherwise (needs refresh)
        initial_status = "ok" if content else "pending"

        # Verify dashboard exists and belongs to user
        with _conn() as conn:
            dash = conn.execute(
                "SELECT id, name FROM dashboards WHERE id=? AND user_id=?",
                (dashboard_id, user_uuid),
            ).fetchone()

        if not dash:
            return self._error_payload(f"Dashboard {dashboard_id!r} not found")

        # Auto-position: place after last tile
        pos_col, pos_row = self._next_position(dashboard_id, tile_width)

        tile_id = f"tile-{uuid.uuid4().hex[:12]}"
        now = _now()

        with _conn() as conn:
            # Get current max sort_order
            max_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM dashboard_tiles WHERE dashboard_id=?",
                (dashboard_id,),
            ).fetchone()[0]

            conn.execute(
                """INSERT INTO dashboard_tiles
                   (id, dashboard_id, user_id, title, component_id,
                    spec, html, source_prompt, source_session_id, source_turn_id,
                    profile_id, profile_tag,
                    pos_col, pos_row, tile_width, tile_height,
                    sort_order, refresh_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'chart',
                           '{}', ?, ?, ?, ?,
                           ?, ?,
                           ?, ?, ?, ?,
                           ?, ?, ?, ?)""",
                (
                    tile_id, dashboard_id, user_uuid, tile_title,
                    content, query, session_id, current_turn_number,
                    profile_id, profile_tag,
                    pos_col, pos_row, tile_width, tile_height,
                    max_order + 1, initial_status, now, now,
                ),
            )
            # Touch dashboard updated_at
            conn.execute(
                "UPDATE dashboards SET updated_at=? WHERE id=?",
                (now, dashboard_id),
            )
            conn.commit()

        _announce(user_uuid, "updated", [dashboard_id], aspect="tiles")
        logger.info(f"[DashboardHandler] Added tile {tile_id} to dashboard {dashboard_id}")

        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "tile_added",
                "dashboard_id": dashboard_id,
                "dashboard_name": dash["name"],
                "tile_id": tile_id,
                "tile_title": tile_title,
                "content": content,
                "query": query,
                "source_prompt": query,
                "session_id": session_id,
                "profile_id": profile_id,
                "profile_tag": profile_tag,
            },
            title=f"{tile_title} · added to {dash['name']}",
        )

    def _handle_visualize(
        self,
        args: Dict,
        session_id: str,
        profile_id: str,
        profile_tag: str,
        user_input: str = "",
        current_turn_number: int = None,
    ) -> ComponentRenderPayload:
        """Show an auto-viz card in chat without pinning to a dashboard.
        The user can pin via the 'Add to Dashboard' button on the card."""
        query      = args.get("query") or ""
        # Normalise: OPTIM may pass raw JSON result dicts; convert to HTML table.
        content    = _content_to_html(args.get("content") or "")
        tile_title = args.get("tile_title") or "Visualization"
        chart_type = args.get("chart_type") or ""
        # source_prompt: the clean user question used to re-run this tile.
        # Prefer the cached original user input over the LLM's 'query' arg,
        # which is often left empty or polluted with result data.
        source_prompt = user_input or query

        # Programmatic fallback (approach 1): detect chart type from the user's own
        # words when the LLM omits chart_type — the query arg first, else the cached
        # user input (the arg is often left empty). Deterministic, no LLM round-trip.
        if not chart_type:
            chart_type = _chart_type_from_text(query) or _chart_type_from_text(user_input)

        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "visualization",
                "tile_title": tile_title,
                "content": content,
                "query": query,
                "source_prompt": source_prompt,
                "chart_type": chart_type,
                "session_id": session_id,
                "source_turn_id": current_turn_number,
                "profile_id": profile_id,
                "profile_tag": profile_tag,
            },
            title=f"{tile_title} · not yet on a dashboard",
        )

    async def _handle_refresh(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        dashboard_id = args.get("dashboard_id", "")
        tile_id = args.get("tile_id")  # optional — if None, refresh all

        if not dashboard_id:
            return self._error_payload("dashboard_id is required for 'refresh'")

        with _conn() as conn:
            dash = conn.execute(
                "SELECT name FROM dashboards WHERE id=? AND user_id=?",
                (dashboard_id, user_uuid),
            ).fetchone()

        if not dash:
            return self._error_payload(f"Dashboard {dashboard_id!r} not found")

        scope = "tile" if tile_id else "all"

        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={
                "view": "refresh",
                "dashboard_id": dashboard_id,
                "dashboard_name": dash["name"],
                "tile_id": tile_id,
                "scope": scope,
            },
            title=f"Refreshing {dash['name']}",
        )

    async def _handle_delete(self, args: Dict, user_uuid: str) -> ComponentRenderPayload:
        dashboard_id = args.get("dashboard_id", "")
        if not dashboard_id:
            return self._error_payload("dashboard_id is required for 'delete'")

        with _conn() as conn:
            dash = conn.execute(
                "SELECT name FROM dashboards WHERE id=? AND user_id=?",
                (dashboard_id, user_uuid),
            ).fetchone()

            if not dash:
                return self._error_payload(f"Dashboard {dashboard_id!r} not found")

            name = dash["name"]
            conn.execute(
                "DELETE FROM dashboards WHERE id=? AND user_id=?",
                (dashboard_id, user_uuid),
            )
            conn.commit()

        logger.info(f"[DashboardHandler] Deleted dashboard {dashboard_id}")
        _announce(user_uuid, "deleted", [dashboard_id])

        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={"view": "deleted", "dashboard_id": dashboard_id, "name": name},
            title=f"{name} · deleted",
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_dashboard(self, dashboard_id: str, user_uuid: str):
        with _conn() as conn:
            dash = conn.execute(
                "SELECT * FROM dashboards WHERE id=? AND user_id=?",
                (dashboard_id, user_uuid),
            ).fetchone()

            if not dash:
                return None, []

            tiles = conn.execute(
                "SELECT * FROM dashboard_tiles WHERE dashboard_id=? ORDER BY sort_order ASC, created_at ASC",
                (dashboard_id,),
            ).fetchall()

        return dash, tiles

    def _next_position(self, dashboard_id: str, new_width: int) -> tuple:
        """
        Simple auto-layout: place new tile after the last tile, wrapping to next row.
        Returns (pos_col, pos_row).
        """
        with _conn() as conn:
            tiles = conn.execute(
                "SELECT pos_col, pos_row, tile_width, tile_height FROM dashboard_tiles WHERE dashboard_id=?",
                (dashboard_id,),
            ).fetchall()

        if not tiles:
            return 0, 0

        # Find the maximum row extent
        max_row_end = max(t["pos_row"] + t["tile_height"] for t in tiles)

        # Try to fit on the last row
        last_row = max(t["pos_row"] for t in tiles)
        last_row_tiles = [t for t in tiles if t["pos_row"] == last_row]
        last_col_end = max(t["pos_col"] + t["tile_width"] for t in last_row_tiles)

        if last_col_end + new_width <= 12:
            return last_col_end, last_row

        # Wrap to next row
        return 0, max_row_end

    def _error_payload(self, message: str) -> ComponentRenderPayload:
        return ComponentRenderPayload(
            component_id="dashboard",
            render_target=RenderTarget.INLINE,
            spec={"view": "error", "message": message},
            title="Dashboard Error",
        )

    # ------------------------------------------------------------------
    # Instance portability (export / import) — see instance_portability.py
    # ------------------------------------------------------------------
    #
    # A dashboard's only remappable cross-reference is each tile's execution
    # PROFILE (stored as both id and tag). Export emits the tag; import remaps it.
    # Tile DATA SOURCES (mcp_tool_name) are environment refs (informational, like
    # flow tools). PROVENANCE — source_session_id / source_turn_id — is per-user
    # and meaningless to an importer, so it is reset; imported tiles keep their
    # viz spec + last snapshot, and the first refresh re-binds to the importer's
    # mapped profile (matching the dashboard refresh-provenance model).

    async def list_instances(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        user_uuid = context.get("user_uuid", "")
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, name, updated_at FROM dashboards WHERE user_id=? ORDER BY updated_at DESC",
                (user_uuid,),
            ).fetchall()
        return [{"id": r["id"], "name": r["name"], "updated_at": r["updated_at"]} for r in rows]

    async def export_instance(self, instance_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        from trusted_data_agent.components import instance_portability as ip

        user_uuid = context.get("user_uuid", "")
        dash, tiles = self._load_dashboard(instance_id, user_uuid)
        if dash is None:
            raise ValueError(f"Dashboard '{instance_id}' not found")
        dash = dict(dash)
        inv = ip.build_profile_inventory(user_uuid)

        ref_profiles: Dict[str, Dict] = {}
        tool_refs: List[str] = []
        out_tiles: List[Dict] = []
        for row in tiles:
            t = dict(row)
            pid = t.get("profile_id")
            if pid:
                tag = ip.export_profile_ref(pid, inv, ref_profiles)
            else:
                tag = t.get("profile_tag") or None
                if tag:
                    ref_profiles.setdefault(tag, {"tag": tag, "unresolved": tag not in inv["by_tag"]})
            if t.get("mcp_tool_name"):
                tool_refs.append(t["mcp_tool_name"])
            out_tiles.append({
                "title": t.get("title"),
                "component_id": t.get("component_id"),
                "spec": t.get("spec"),
                "html": t.get("html"),
                "source_prompt": t.get("source_prompt"),
                "profile_tag": tag or "",
                "pos_col": t.get("pos_col"), "pos_row": t.get("pos_row"),
                "tile_width": t.get("tile_width"), "tile_height": t.get("tile_height"),
                "mcp_tool_name": t.get("mcp_tool_name"),
                "mcp_args_json": t.get("mcp_args_json"),
                "sort_order": t.get("sort_order"),
            })

        envelope = {
            "uderia_artifact": "component_instance",
            "component_id": "dashboard",
            "schema_version": ip.PORTABILITY_SCHEMA_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "name": dash.get("name") or "Dashboard",
            "description": dash.get("description") or "",
            "spec": {
                "dashboard": {
                    "auto_refresh_s": dash.get("auto_refresh_s", 0),
                    "theme": dash.get("theme", "auto"),
                    "layout_config": dash.get("layout_config", "{}"),
                },
                "tiles": out_tiles,
            },
            "refs": {"profiles": list(ref_profiles.values()), "tools": sorted(set(tool_refs))},
            "summary": {"tiles": len(out_tiles)},
        }
        return ip.stamp_content_hash(envelope)

    async def import_instance(
        self,
        payload: Dict[str, Any],
        context: Dict[str, Any],
        *,
        mode: str = "create",
        target_id: Optional[str] = None,
        mapping: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        from trusted_data_agent.components import instance_portability as ip

        user_uuid = context.get("user_uuid", "")
        if (payload or {}).get("component_id") != "dashboard":
            raise ValueError("This file is not a dashboard export")
        spec = payload.get("spec") or {}
        tiles = spec.get("tiles") or []
        inv = ip.build_profile_inventory(user_uuid)
        ref_profiles = (payload.get("refs") or {}).get("profiles") or []

        # ---- Preflight ----
        if dry_run:
            n = len(tiles)
            return {
                "status": "preflight",
                "component_id": "dashboard",
                "name": payload.get("name") or "Imported Dashboard",
                "description": payload.get("description") or "",
                "summary": payload.get("summary") or {"tiles": n},
                "summary_text": f"{n} {'tile' if n == 1 else 'tiles'}",
                "modes": ["create", "update"],
                "profiles": ip.preflight_profiles(ref_profiles, inv),
                "candidates": inv["candidates"],
                "tools": (payload.get("refs") or {}).get("tools") or [],
            }

        # ---- Commit ----
        dmeta = spec.get("dashboard") or {}
        now = _now()
        warnings: List[str] = []
        if (payload.get("refs") or {}).get("tools"):
            warnings.append("Tile data sources (MCP tools) must exist on the mapped profile to refresh.")

        def _materialize_tiles(conn, dashboard_id):
            # source_session_id / source_turn_id reset (provenance is per-user).
            for i, t in enumerate(tiles):
                tag = t.get("profile_tag") or ""
                pid = ip.resolve_profile_ref(tag, mapping, inv) if tag else None
                resolved_tag = (inv["by_id"].get(pid) or {}).get("tag", tag) if pid else ""
                conn.execute(
                    """INSERT INTO dashboard_tiles
                       (id, dashboard_id, user_id, title, component_id, spec, html,
                        source_prompt, source_session_id, source_turn_id,
                        profile_id, profile_tag, pos_col, pos_row, tile_width, tile_height,
                        mcp_tool_name, mcp_args_json, sort_order, refresh_status, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?, ?,'','', ?,?,?,?,?,?, ?,?,?, 'ok', ?, ?)""",
                    (
                        f"tile-{uuid.uuid4().hex[:12]}", dashboard_id, user_uuid,
                        t.get("title") or "Untitled", t.get("component_id") or "chart",
                        t.get("spec") or "{}", t.get("html") or "", t.get("source_prompt") or "",
                        pid or "", resolved_tag or "",
                        int(t.get("pos_col") or 0), int(t.get("pos_row") or 0),
                        int(t.get("tile_width") or 6), int(t.get("tile_height") or 4),
                        t.get("mcp_tool_name"), t.get("mcp_args_json"),
                        int(t.get("sort_order") or i), now, now,
                    ),
                )

        # In-place re-materialization (marketplace subscribe-sync): keep the id.
        if mode == "update" and target_id:
            with _conn() as conn:
                dash = conn.execute("SELECT id, name FROM dashboards WHERE id=? AND user_id=?", (target_id, user_uuid)).fetchone()
                if not dash:
                    raise ValueError(f"Dashboard '{target_id}' not found for update")
                name = payload.get("name") or dash["name"]
                conn.execute(
                    "UPDATE dashboards SET name=?, description=?, auto_refresh_s=?, theme=?, layout_config=?, updated_at=? WHERE id=?",
                    (name, payload.get("description") or "", int(dmeta.get("auto_refresh_s") or 0),
                     dmeta.get("theme") or "auto", dmeta.get("layout_config") or "{}", now, target_id),
                )
                conn.execute("DELETE FROM dashboard_tiles WHERE dashboard_id=?", (target_id,))
                _materialize_tiles(conn, target_id)
                conn.commit()
            return {"status": "success", "mode": "update", "dashboard_id": target_id, "instance_id": target_id, "name": name, "warnings": warnings}

        # Create a fresh dashboard (file import / install / fork).
        with _conn() as conn:
            existing = {r["name"] for r in conn.execute("SELECT name FROM dashboards WHERE user_id=?", (user_uuid,)).fetchall()}
        name = ip.dedupe_name(payload.get("name") or "Imported Dashboard", existing)
        dashboard_id = f"dash-{uuid.uuid4().hex[:12]}"
        with _conn() as conn:
            conn.execute(
                """INSERT INTO dashboards (id, user_id, name, description, auto_refresh_s, theme, layout_config, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (dashboard_id, user_uuid, name, payload.get("description") or "",
                 int(dmeta.get("auto_refresh_s") or 0), dmeta.get("theme") or "auto",
                 dmeta.get("layout_config") or "{}", now, now),
            )
            _materialize_tiles(conn, dashboard_id)
            conn.commit()
        return {"status": "success", "mode": "create", "dashboard_id": dashboard_id, "instance_id": dashboard_id, "name": name, "warnings": warnings}
