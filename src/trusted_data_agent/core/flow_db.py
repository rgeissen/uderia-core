"""
Flow DB — CRUD operations for the Flow system.

Isolated module (mirrors agent_pack_db.py pattern) so flow_executor and
flow_routes import from one place. All operations are synchronous SQLite;
callers run them in Quart's thread-pool executor when needed.
"""

import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from trusted_data_agent.core.flow_layout import apply_layout
from trusted_data_agent.core.versioning import prune_versions
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")

_DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"
_SCHEMA_APPLIED = False  # module-level guard so migration runs once per process


def _ensure_schema() -> None:
    """Apply 39_flow_system.sql if the flows table does not yet exist."""
    global _SCHEMA_APPLIED
    if _SCHEMA_APPLIED:
        return
    try:
        conn = sqlite_util.connect(str(_DB_PATH))
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='flows'"
        ).fetchone()
        if not exists:
            sql_path = Path(__file__).resolve().parents[3] / "schema" / "39_flow_system.sql"
            if sql_path.exists():
                conn.executescript(sql_path.read_text())
                logger.info("flow_db: applied 39_flow_system.sql")
            else:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS flows (
                        flow_id      TEXT PRIMARY KEY,
                        user_id      TEXT NOT NULL,
                        name         TEXT NOT NULL,
                        description  TEXT,
                        definition   TEXT NOT NULL,
                        is_template  INTEGER DEFAULT 0,
                        is_reusable  INTEGER DEFAULT 0,
                        version      TEXT DEFAULT '1.0.0',
                        runs_session_id TEXT,
                        created_at   TEXT DEFAULT (datetime('now')),
                        updated_at   TEXT DEFAULT (datetime('now'))
                    );
                    CREATE TABLE IF NOT EXISTS flow_runs (
                        run_id          TEXT PRIMARY KEY,
                        flow_id         TEXT NOT NULL REFERENCES flows(flow_id) ON DELETE CASCADE,
                        user_id         TEXT NOT NULL,
                        status          TEXT DEFAULT 'queued',
                        session_id      TEXT,
                        input_params    TEXT,
                        output_result   TEXT,
                        execution_trace TEXT,
                        tokens_input    INTEGER DEFAULT 0,
                        tokens_output   INTEGER DEFAULT 0,
                        cost_usd        REAL DEFAULT 0,
                        started_at      TEXT,
                        completed_at    TEXT,
                        created_at      TEXT DEFAULT (datetime('now'))
                    );
                    CREATE TABLE IF NOT EXISTS flow_node_runs (
                        id           INTEGER PRIMARY KEY,
                        run_id       TEXT NOT NULL REFERENCES flow_runs(run_id) ON DELETE CASCADE,
                        node_id      TEXT NOT NULL,
                        node_type    TEXT,
                        scope_id     TEXT,
                        iteration    INTEGER DEFAULT 0,
                        status       TEXT DEFAULT 'pending',
                        input_state  TEXT,
                        output_state TEXT,
                        error_msg    TEXT,
                        task_id      TEXT,
                        session_id   TEXT,
                        tokens_input  INTEGER DEFAULT 0,
                        tokens_output INTEGER DEFAULT 0,
                        started_at   TEXT,
                        completed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_flows_user_id         ON flows(user_id);
                    CREATE INDEX IF NOT EXISTS idx_flow_runs_flow_id     ON flow_runs(flow_id);
                    CREATE INDEX IF NOT EXISTS idx_flow_runs_user_id     ON flow_runs(user_id);
                    CREATE INDEX IF NOT EXISTS idx_flow_runs_status      ON flow_runs(status);
                    CREATE INDEX IF NOT EXISTS idx_flow_node_runs_run_id ON flow_node_runs(run_id);
                    CREATE INDEX IF NOT EXISTS idx_flow_node_runs_node_id ON flow_node_runs(node_id);
                """)
                logger.info("flow_db: created flow tables (inline fallback)")
            conn.commit()
        conn.close()
        _SCHEMA_APPLIED = True
    except Exception as exc:
        logger.error(f"flow_db._ensure_schema failed: {exc}", exc_info=True)


_ensure_schema()  # run once at import time


def _get_conn() -> sqlite3.Connection:
    conn = sqlite_util.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_columns() -> None:
    """Self-applying additive migration (mirrors task_scheduler._ensure_columns).

    - flow_runs.session_id: binds each run to the session it ran in.
    - flows.runs_session_id: the ONE persistent session that all REST/headless runs
      of this flow append to (so the sidebar shows one entry per flow, not per run).
    Databases created before these columns are migrated in place at import time.
    """
    try:
        with _get_conn() as conn:
            run_cols = {row[1] for row in conn.execute("PRAGMA table_info(flow_runs)").fetchall()}
            if "session_id" not in run_cols:
                conn.execute("ALTER TABLE flow_runs ADD COLUMN session_id TEXT")
                logger.info("flow_db: migrated flow_runs — added session_id column.")
            flow_cols = {row[1] for row in conn.execute("PRAGMA table_info(flows)").fetchall()}
            if "runs_session_id" not in flow_cols:
                conn.execute("ALTER TABLE flows ADD COLUMN runs_session_id TEXT")
                logger.info("flow_db: migrated flows — added runs_session_id column.")
            conn.commit()
    except Exception as exc:
        logger.warning(f"flow_db._ensure_columns failed: {exc}")


_ensure_columns()  # run once at import time (after _ensure_schema)


def get_flow_runs_session_id(flow_id: str, user_id: str) -> Optional[str]:
    """The persistent per-flow 'runs' session id, or None if not set yet."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT runs_session_id FROM flows WHERE flow_id = ? AND user_id = ?",
            (flow_id, user_id),
        ).fetchone()
    return row["runs_session_id"] if row else None


def set_flow_runs_session_id(flow_id: str, user_id: str, session_id: Optional[str]) -> None:
    """Persist (or clear) the per-flow 'runs' session id."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE flows SET runs_session_id = ? WHERE flow_id = ? AND user_id = ?",
            (session_id, flow_id, user_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Flow CRUD
# ---------------------------------------------------------------------------

def _normalize_decision_branches(definition: Dict[str, Any]) -> bool:
    """Enforce the decision-branch invariant at the persistence boundary.

    Every edge leaving a `decision` node must carry a lowercase ``"true"``/``"false"``
    condition (and a ``"conditional"`` type). This is load-bearing in two places:
      * Execution — ``_activate_edges`` only follows a decision edge whose ``condition``
        equals the evaluated result, so a branch with no condition never fires and the
        flow dead-ends when that branch should be taken.
      * Canvas — the renderer only colours/labels an edge as a True/False branch when
        its ``condition`` is ``"true"``/``"false"``; without it the branch shows no tag.

    The ``TDA_Flow`` instructions tell the LLM to set these, but LLM compliance is not a
    correctness guarantee — this boundary is. For each decision node we assign any unused
    ``true``/``false`` slot to its condition-less outgoing edges. When one branch is
    already set the other is unambiguous; when both are missing the first edge becomes the
    ``true`` branch (matching the node's primary 'true' handle). A well-formed decision has
    exactly two outgoing edges; any extra edge with no free slot is left untouched.

    Returns True if anything changed.
    """
    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []
    decision_ids = {n.get("id") for n in nodes if n.get("type") == "decision"}
    if not decision_ids:
        return False

    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for edge in edges:
        if edge.get("source") in decision_ids:
            by_source.setdefault(edge["source"], []).append(edge)

    changed = False
    for src_id, group in by_source.items():
        used = {e.get("condition") for e in group if e.get("condition") in ("true", "false")}
        free = [c for c in ("true", "false") if c not in used]
        for edge in group:
            if edge.get("condition") in ("true", "false"):
                continue
            if not free:
                logger.warning(
                    "Decision node '%s' has an outgoing edge '%s' with no free true/false "
                    "slot (more than two branches?); leaving it unconditioned.",
                    src_id, edge.get("id"),
                )
                continue
            slot = free.pop(0)
            edge["condition"] = slot
            if edge.get("type") in (None, "normal"):
                edge["type"] = "conditional"
            changed = True
            logger.info(
                "Normalized decision branch: node '%s' edge '%s' had no condition → '%s'.",
                src_id, edge.get("id"), slot,
            )
    return changed


def _canonicalize_node_references(definition: Dict[str, Any]) -> None:
    """Rewrite decision/function value references to canonical syntax in place, at the
    persistence boundary, so the stored flow never carries the broken reference forms a
    model invents. Repair-only here (errors are surfaced by the handler on the LLM
    path). Fails open so a validation hiccup never blocks a save."""
    try:
        from trusted_data_agent.core.flow_validation import validate_and_repair
        validate_and_repair(definition)
    except Exception as exc:  # pragma: no cover - never block persistence
        logger.warning(f"[flow_db] reference canonicalisation skipped: {exc}")


@_signals.announces("flow", "created", user="user_id", ids=("flow_id",))
def create_flow(user_id: str, name: str, description: str, definition: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new flow and return its record."""
    # New flows default to auto-layout ON regardless of how they were created
    # (empty template, or an LLM-supplied full definition that bypasses
    # generate_empty_flow). Done here at the universal create boundary so the
    # canvas opens with auto-layout active and re-arranges as the flow is built.
    definition.setdefault("settings", {}).setdefault("autoLayout", True)
    # Always layout on creation so Start/End are centred regardless of what the caller passed.
    apply_layout(definition)
    # Authoritative invariant: decision branches always carry true/false conditions.
    _normalize_decision_branches(definition)
    # Canonicalise node value references ({Node.col}/$col → col) so the STORED flow —
    # and therefore the canvas — never shows the broken syntax a model invents.
    _canonicalize_node_references(definition)
    # PROP-008 §6 — an action node its agent may not perform is refused before it is stored
    from trusted_data_agent.core.flow_validation import check_action_nodes
    check_action_nodes(definition, user_id)
    flow_id = f"flow-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO flows (flow_id, user_id, name, description, definition, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (flow_id, user_id, name, description or "", json.dumps(definition), now, now),
        )
        conn.commit()
    return get_flow(flow_id, user_id)


def get_flow(flow_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single flow by ID (user-scoped)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM flows WHERE flow_id = ? AND user_id = ?",
            (flow_id, user_id),
        ).fetchone()
    if not row:
        return None
    return _row_to_flow(row)


def list_flows(user_id: str, include_templates: bool = True) -> List[Dict[str, Any]]:
    """List all flows for a user, ordered by most recently updated."""
    with _get_conn() as conn:
        if include_templates:
            rows = conn.execute(
                "SELECT * FROM flows WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM flows WHERE user_id = ? AND is_template = 0 ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
    return [_row_to_flow(r) for r in rows]


@_signals.announces("flow", "updated", user="user_id", ids=("flow_id",))
def update_flow(
    flow_id: str,
    user_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    definition: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Update mutable fields of a flow. Returns updated record or None if not found."""
    existing = get_flow(flow_id, user_id)
    if not existing:
        return None

    updates = {"updated_at": _now()}
    if name is not None:
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    if definition is not None:
        # Authoritative invariant: decision branches always carry true/false conditions.
        # apply_patch routes every structural mutation (add_edge, insert_node, …) through
        # here, so this enforces well-formed branches on every persisted edit.
        _normalize_decision_branches(definition)
        # Canonicalise node value references on every persisted edit too, so an
        # add_node / update_node that introduces a condition stores clean syntax.
        _canonicalize_node_references(definition)
        from trusted_data_agent.core.flow_validation import check_action_nodes
        check_action_nodes(definition, user_id)
        updates["definition"] = json.dumps(definition)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [flow_id, user_id]

    with _get_conn() as conn:
        conn.execute(
            f"UPDATE flows SET {set_clause} WHERE flow_id = ? AND user_id = ?",
            values,
        )
        conn.commit()
    return get_flow(flow_id, user_id)


def _should_auto_layout(definition: Dict[str, Any]) -> bool:
    """Return True when the flow's settings have autoLayout enabled."""
    return bool(definition.get("settings", {}).get("autoLayout", False))


# Loop rules live in ONE module (core/flow_loops.py); these names are kept for callers.
from trusted_data_agent.core.flow_loops import (  # noqa: E402
    SCOPE_MAX_ROUNDS, SCOPE_MIN_ROUNDS, LoopError, clamp_rounds as clamp_scope_rounds,
)
from trusted_data_agent.core import flow_loops  # noqa: E402


def apply_patch(flow_id: str, user_id: str, patch: Dict[str, Any]) -> tuple:
    """
    Apply a graph patch operation to a flow's definition.

    Patch format: {"op": "add_node"|"remove_node"|"update_node"|
                           "add_edge"|"remove_edge"|"update_settings"|
                           "create_scope"|"update_scope",
                   "payload": {...}}

    Returns (flow_record, layout_positions) where layout_positions is a
    {node_id: {x, y}} dict when layout was applied, or None otherwise.
    """
    flow = get_flow(flow_id, user_id)
    if not flow:
        return None, None

    definition = flow["definition"]
    op = patch.get("op")
    payload = patch.get("payload", {})
    _layout_positions: Optional[dict] = None

    if op == "add_node":
        node = payload.get("node", {})
        if node.get("id") and not any(n["id"] == node["id"] for n in definition.get("nodes", [])):
            definition.setdefault("nodes", []).append(node)
        # TDA_Flow builds graphs programmatically — always lay out so positions are valid.
        apply_layout(definition)
        _layout_positions = {n["id"]: n.get("position", {}) for n in definition.get("nodes", [])}

    elif op == "remove_node":
        node_id = payload.get("node_id")
        smart_delete = definition.get("settings", {}).get("smartDelete", True)
        if smart_delete:
            # Collect non-cycle predecessors and successors of the deleted node
            non_cycle = [e for e in definition.get("edges", [])
                         if e.get("type") != "cycle" and not e.get("animated")]
            preds = [e["source"] for e in non_cycle if e.get("target") == node_id]
            succs = [e["target"] for e in non_cycle if e.get("source") == node_id]
            # Bridge: connect each predecessor to each successor.
            # Skip when BOTH sides have multiple nodes — avoids an N×M edge explosion.
            if preds and succs and not (len(preds) > 1 and len(succs) > 1):
                existing_pairs = {(e.get("source"), e.get("target")) for e in definition.get("edges", [])}
                for pred in preds:
                    for succ in succs:
                        if (pred, succ) not in existing_pairs and pred != succ:
                            definition.setdefault("edges", []).append({
                                "id": f"e-bridge-{uuid.uuid4().hex[:8]}",
                                "source": pred, "target": succ, "type": "normal",
                            })
        definition["nodes"] = [n for n in definition.get("nodes", []) if n["id"] != node_id]
        definition["edges"] = [
            e for e in definition.get("edges", [])
            if e.get("source") != node_id and e.get("target") != node_id
        ]
        if _should_auto_layout(definition):
            apply_layout(definition)
            _layout_positions = {n["id"]: n.get("position", {}) for n in definition.get("nodes", [])}

    elif op == "update_node":
        node_id = payload.get("node_id")
        updates = payload.get("updates", {})
        for node in definition.get("nodes", []):
            if node["id"] == node_id:
                # Deep-merge the nested `data` dict instead of replacing it, so a
                # PARTIAL update (e.g. just {data: {extensions}} from the slot
                # rail) doesn't wipe the node's other data (prompt, profile_id,
                # output_as, condition, …). Top-level keys still replace.
                new_data = updates.get("data")
                if isinstance(new_data, dict) and isinstance(node.get("data"), dict):
                    merged_data = {**node["data"], **new_data}
                    node.update({k: v for k, v in updates.items() if k != "data"})
                    node["data"] = merged_data
                else:
                    node.update(updates)
                break
        # Never re-layout on update — caller placed the node intentionally.

    elif op == "add_edge":
        edge = payload.get("edge", {})
        edge_id = edge.get("id") or f"e-{uuid.uuid4().hex[:8]}"
        edge["id"] = edge_id
        if not any(e["id"] == edge_id for e in definition.get("edges", [])):
            definition.setdefault("edges", []).append(edge)
        if _should_auto_layout(definition):
            apply_layout(definition)
            _layout_positions = {n["id"]: n.get("position", {}) for n in definition.get("nodes", [])}

    elif op == "remove_edge":
        edge_id = payload.get("edge_id")
        definition["edges"] = [e for e in definition.get("edges", []) if e["id"] != edge_id]
        if _should_auto_layout(definition):
            apply_layout(definition)
            _layout_positions = {n["id"]: n.get("position", {}) for n in definition.get("nodes", [])}

    elif op == "insert_node":
        # Atomic palette-drop-onto-edge: add node + remove old edge + add 2 new edges.
        # Produces a single snapshot (vs 4 separate patch calls).
        node          = payload.get("node", {})
        removed_eid   = payload.get("removed_edge_id")
        edge1_data    = payload.get("edge1", {})
        edge2_data    = payload.get("edge2", {})
        # Add node only if it doesn't already exist (idempotent for drag-onto-edge reuse)
        if node.get("id") and not any(n["id"] == node["id"] for n in definition.get("nodes", [])):
            definition.setdefault("nodes", []).append(node)
        # Remove the replaced edge
        if removed_eid:
            definition["edges"] = [e for e in definition.get("edges", []) if e["id"] != removed_eid]
        # Add the two replacement edges
        for new_edge in [edge1_data, edge2_data]:
            eid = new_edge.get("id") or f"e-{uuid.uuid4().hex[:8]}"
            new_edge["id"] = eid
            if new_edge.get("source") and new_edge.get("target") and \
               not any(e["id"] == eid for e in definition.get("edges", [])):
                definition.setdefault("edges", []).append(new_edge)
        # Animation always needs server-authoritative positions.
        apply_layout(definition)
        _layout_positions = {n["id"]: n.get("position", {}) for n in definition.get("nodes", [])}

    elif op == "update_settings":
        settings_patch = payload.get("settings", {})
        definition.setdefault("settings", {}).update(settings_patch)

    # Loops go through core/flow_loops.py — the same rules for a hand-made loop, a
    # template loop and one the TDA_Flow tool sends. A refused edit raises LoopError
    # (the route answers 400 with its code; the tool returns it as a recovery message).
    elif op == "create_scope":
        if isinstance(payload.get("scope"), dict) and payload["scope"]:
            flow_loops.adopt_scope(definition, payload["scope"])
        else:
            flow_loops.make_loop(
                definition, payload.get("node_ids") or [], name=payload.get("name"),
                max_rounds=payload.get("max_rounds", flow_loops.DEFAULT_ROUNDS),
                exit_when=payload.get("exit_when", "default"),
                history_window=payload.get("history_window"))
        apply_layout(definition)
        _layout_positions = {n["id"]: n.get("position", {}) for n in definition.get("nodes", [])}

    elif op == "update_scope":
        flow_loops.update_loop(definition, payload.get("scope_id"), payload.get("updates", {}) or {})

    elif op == "delete_scope":
        flow_loops.remove_loop(definition, payload.get("scope_id"))
        apply_layout(definition)
        _layout_positions = {n["id"]: n.get("position", {}) for n in definition.get("nodes", [])}

    elif op == "update_global_params":
        definition["global_params"] = payload.get("params", {})

    else:
        logger.warning(f"apply_patch: unknown op '{op}' — ignored")

    return update_flow(flow_id, user_id, definition=definition), _layout_positions


@_signals.announces("flow", "deleted", user="user_id", ids=("flow_id",))
def delete_flow(flow_id: str, user_id: str) -> bool:
    """Delete a flow and all its runs. Returns True if deleted."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM flows WHERE flow_id = ? AND user_id = ?",
            (flow_id, user_id),
        )
        conn.commit()
    return cur.rowcount > 0


def list_platform_flows() -> List[Dict[str, Any]]:
    """List all platform/blueprint flows (is_template=1) across all users."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM flows WHERE is_template = 1 ORDER BY updated_at DESC"
        ).fetchall()
    return [_row_to_flow(r) for r in rows]


def set_flow_template_flag(flow_id: str, is_template: bool) -> Optional[Dict[str, Any]]:
    """Promote or demote a flow as a platform template (admin-scoped, no user restriction)."""
    now = _now()
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE flows SET is_template = ?, updated_at = ? WHERE flow_id = ?",
            (1 if is_template else 0, now, flow_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM flows WHERE flow_id = ?", (flow_id,)).fetchone()
    return _row_to_flow(row) if row else None


@_signals.announces("flow", "created", user="target_user_id", ids=("flow_id",))
def fork_flow(source_flow_id: str, target_user_id: str, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fork a platform flow (``is_template``) or one of the user's own into their Profile Flows.

    Another user's private flow is not forkable — it reads as not found (None), never as
    forbidden, so an id cannot be probed. (Until Nov 2026 any flow id was copied, definition
    and all, into whoever asked.)"""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM flows WHERE flow_id = ?", (source_flow_id,)
        ).fetchone()
    if not row:
        return None
    source = _row_to_flow(row)
    if source.get("user_id") != target_user_id and not source.get("is_template"):
        return None
    fork_name = name or f"{source['name']} (copy)"
    return create_flow(target_user_id, fork_name, source.get("description", ""), source["definition"])


def update_flow_metadata(flow_id: str, name: Optional[str] = None, description: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Update flow name/description without user restriction (admin use for platform flows)."""
    updates: Dict[str, Any] = {"updated_at": _now()}
    if name is not None:
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [flow_id]
    with _get_conn() as conn:
        cur = conn.execute(
            f"UPDATE flows SET {set_clause} WHERE flow_id = ?", values
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM flows WHERE flow_id = ?", (flow_id,)).fetchone()
    return _row_to_flow(row) if row else None


def _row_to_flow(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    try:
        d["definition"] = json.loads(d["definition"])
    except (json.JSONDecodeError, TypeError):
        d["definition"] = {}
    return d


# ---------------------------------------------------------------------------
# Flow Versions (snapshots)
# ---------------------------------------------------------------------------

_VERSIONS_TABLE_APPLIED = False


def _ensure_versions_table() -> None:
    """Create flow_versions table if it does not yet exist (self-applying migration)."""
    global _VERSIONS_TABLE_APPLIED
    if _VERSIONS_TABLE_APPLIED:
        return
    try:
        conn = sqlite_util.connect(str(_DB_PATH))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS flow_versions (
                version_id   TEXT PRIMARY KEY,
                flow_id      TEXT NOT NULL REFERENCES flows(flow_id) ON DELETE CASCADE,
                user_id      TEXT NOT NULL,
                definition   TEXT NOT NULL,
                version_num  INTEGER NOT NULL,
                label        TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_flow_versions_flow_id ON flow_versions(flow_id);
            CREATE INDEX IF NOT EXISTS idx_flow_versions_user_id ON flow_versions(user_id);
        """)
        conn.commit()
        conn.close()
        _VERSIONS_TABLE_APPLIED = True
    except Exception as exc:
        logger.error(f"flow_db._ensure_versions_table failed: {exc}", exc_info=True)


@_signals.announces("flow", "snapshot", user="user_id", ids=("flow_id",), aspect="versions")
def snapshot_flow(flow_id: str, user_id: str, label: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Snapshot the current definition of a flow as a new version.
    Returns None (without error) when the definition is unchanged from the last snapshot.
    """
    _ensure_versions_table()
    flow = get_flow(flow_id, user_id)
    if not flow:
        return None

    current_def_str = json.dumps(flow["definition"], sort_keys=True)

    with _get_conn() as conn:
        last_row = conn.execute(
            "SELECT definition, version_num FROM flow_versions "
            "WHERE flow_id = ? ORDER BY version_num DESC LIMIT 1",
            (flow_id,),
        ).fetchone()

        # Deduplicate: skip if definition is identical to last snapshot
        if last_row and last_row["definition"] == current_def_str:
            return None

        version_num = (last_row["version_num"] + 1) if last_row else 1
        version_id = f"fv-{uuid.uuid4().hex[:12]}"
        auto_label = label or f"v{version_num}"
        now = _now()
        conn.execute(
            """INSERT INTO flow_versions
               (version_id, flow_id, user_id, definition, version_num, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (version_id, flow_id, user_id, current_def_str, version_num, auto_label, now),
        )
        # Resource efficiency: bound history to the shared retention cap.
        prune_versions(conn, "flow_versions", "flow_id", flow_id)
        conn.commit()

    return {
        "version_id": version_id,
        "flow_id": flow_id,
        "version_num": version_num,
        "label": auto_label,
        "created_at": now,
    }


def list_versions(flow_id: str, user_id: str) -> List[Dict[str, Any]]:
    """Return all snapshots for a flow ordered oldest-first."""
    _ensure_versions_table()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT version_id, flow_id, user_id, version_num, label, created_at "
            "FROM flow_versions WHERE flow_id = ? AND user_id = ? ORDER BY version_num ASC",
            (flow_id, user_id),
        ).fetchall()
    return [dict(r) for r in rows]


def restore_version(version_id: str, flow_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Restore a flow's definition from a snapshot. Returns the updated flow or None."""
    _ensure_versions_table()
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT definition FROM flow_versions "
            "WHERE version_id = ? AND flow_id = ? AND user_id = ?",
            (version_id, flow_id, user_id),
        ).fetchone()
    if not row:
        return None
    try:
        definition = json.loads(row["definition"])
    except (json.JSONDecodeError, TypeError):
        return None
    return update_flow(flow_id, user_id, definition=definition)


# ---------------------------------------------------------------------------
# Flow Run CRUD
# ---------------------------------------------------------------------------

def create_run(flow_id: str, user_id: str, input_params: Optional[Dict] = None,
               session_id: Optional[str] = None) -> Dict[str, Any]:
    """Create a new flow run record in 'queued' state.

    session_id binds the run to a chat session (reused caller session or an
    auto-provisioned one) so the run is monitorable in the session-scoped UX.
    """
    run_id = f"frun-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO flow_runs
               (run_id, flow_id, user_id, status, session_id, input_params, created_at)
               VALUES (?, ?, ?, 'queued', ?, ?, ?)""",
            (run_id, flow_id, user_id, session_id, json.dumps(input_params or {}), now),
        )
        conn.commit()
    return get_run(run_id)


def find_node_run_by_task(task_id: str) -> Optional[Dict[str, Any]]:
    """PROP-011 phase 3: the node run whose LLM turn was this REST task — `{run_id, node_id,
    node_type, session_id, status, flow_id, user_id}` — or None. The answer route reads it to
    resume a paused flow at the node that asked."""
    if not task_id:
        return None
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT n.run_id, n.node_id, n.node_type, n.session_id, n.status, r.flow_id, r.user_id
               FROM flow_node_runs n JOIN flow_runs r ON r.run_id = n.run_id
               WHERE n.task_id = ? ORDER BY n.id DESC LIMIT 1""", (task_id,)).fetchone()
    if not row:
        return None
    keys = ("run_id", "node_id", "node_type", "session_id", "status", "flow_id", "user_id")
    return {k: row[i] for i, k in enumerate(keys)}


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single flow run by ID."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM flow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_run(row)


def list_runs(flow_id: str, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """List recent runs for a flow."""
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM flow_runs
               WHERE flow_id = ? AND user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (flow_id, user_id, limit),
        ).fetchall()
    return [_row_to_run(r) for r in rows]


def update_run_status(
    run_id: str,
    status: str,
    output_result: Optional[Dict] = None,
    execution_trace: Optional[List] = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd: float = 0.0,
) -> None:
    """Update run status and final metrics."""
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """UPDATE flow_runs SET
               status = ?,
               output_result = ?,
               execution_trace = ?,
               tokens_input = tokens_input + ?,
               tokens_output = tokens_output + ?,
               cost_usd = cost_usd + ?,
               completed_at = CASE WHEN ? IN ('complete', 'error', 'cancelled') THEN ? ELSE completed_at END,
               started_at = CASE WHEN ? = 'running' AND started_at IS NULL THEN ? ELSE started_at END
               WHERE run_id = ?""",
            (
                status,
                json.dumps(output_result) if output_result is not None else None,
                json.dumps(execution_trace) if execution_trace is not None else None,
                tokens_input, tokens_output, cost_usd,
                status, now,
                status, now,
                run_id,
            ),
        )
        conn.commit()


def append_node_run(
    run_id: str,
    node_id: str,
    node_type: str,
    scope_id: Optional[str],
    iteration: int,
    status: str,
    input_state: Optional[Dict],
    output_state: Optional[Dict],
    error_msg: Optional[str] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    """Record execution of a single node."""
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO flow_node_runs
               (run_id, node_id, node_type, scope_id, iteration, status,
                input_state, output_state, error_msg, task_id, session_id,
                tokens_input, tokens_output, started_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, node_id, node_type, scope_id, iteration, status,
                json.dumps(input_state or {}),
                json.dumps(output_state or {}),
                error_msg, task_id, session_id,
                tokens_input, tokens_output,
                started_at or _now(),
                completed_at,
            ),
        )
        conn.commit()


def reconcile_orphaned_runs() -> int:
    """Settle runs left in a non-terminal state by a prior process.

    In-flight executors live only in process memory (``_active_executors`` in
    flow_routes). A crash or restart therefore leaves their DB rows stuck in
    ``queued``/``running`` forever, with a dead SSE stream — the run history and
    Live Status panel then lie about work that can never finish.

    Called once at startup, this marks every such run ``cancelled`` (the closest
    terminal status the frontend treats as final — see renderer.js run-status
    polling) and records the reason in ``execution_trace`` when none exists yet.
    Returns the number of runs reconciled.
    """
    marker = json.dumps([{
        "status": "interrupted",
        "reason": "Run did not finish — the server restarted while it was in progress.",
    }])
    now = _now()
    with _get_conn() as conn:
        cur = conn.execute(
            """UPDATE flow_runs
               SET status = 'cancelled',
                   completed_at = COALESCE(completed_at, ?),
                   execution_trace = COALESCE(execution_trace, ?)
               WHERE status IN ('queued', 'running')""",
            (now, marker),
        )
        conn.commit()
        count = cur.rowcount or 0
    if count:
        logger.info(f"[flow_db] reconciled {count} orphaned flow run(s) → cancelled")
    return count


def get_node_runs(run_id: str) -> List[Dict[str, Any]]:
    """Fetch all node execution records for a run."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_node_runs WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
    return [_row_to_node_run(r) for r in rows]


def _row_to_run(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for field in ("input_params", "output_result", "execution_trace"):
        try:
            d[field] = json.loads(d[field]) if d.get(field) else None
        except (json.JSONDecodeError, TypeError):
            d[field] = None
    return d


def _row_to_node_run(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for field in ("input_state", "output_state"):
        try:
            d[field] = json.loads(d[field]) if d.get(field) else {}
        except (json.JSONDecodeError, TypeError):
            d[field] = {}
    return d


# ---------------------------------------------------------------------------
# Global Flow Variables (cross-flow, user-level constants — Flowise-style $vars)
# ---------------------------------------------------------------------------

_FLOW_VARS_TABLE_APPLIED = False


def _ensure_flow_variables_table() -> None:
    """Create flow_variables table if it does not yet exist (self-applying migration)."""
    global _FLOW_VARS_TABLE_APPLIED
    if _FLOW_VARS_TABLE_APPLIED:
        return
    try:
        conn = sqlite_util.connect(str(_DB_PATH))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS flow_variables (
                user_id    TEXT NOT NULL,
                name       TEXT NOT NULL,
                value      TEXT,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, name)
            );
            CREATE INDEX IF NOT EXISTS idx_flow_variables_user_id ON flow_variables(user_id);
        """)
        conn.commit()
        conn.close()
        _FLOW_VARS_TABLE_APPLIED = True
    except Exception as exc:
        logger.error(f"flow_db._ensure_flow_variables_table failed: {exc}", exc_info=True)


def list_flow_variables(user_id: str) -> List[Dict[str, Any]]:
    """Return all global variables for a user as [{name, value}] ordered by name."""
    _ensure_flow_variables_table()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT name, value FROM flow_variables WHERE user_id = ? ORDER BY name ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_flow_variables_dict(user_id: str) -> Dict[str, str]:
    """Return a user's global variables as a {name: value} dict (for execution context)."""
    return {r["name"]: r["value"] for r in list_flow_variables(user_id)}


def replace_flow_variables(user_id: str, variables: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Replace a user's entire global-variable set with the given {name: value} dict.

    Blank names are dropped. Returns the persisted list.
    """
    _ensure_flow_variables_table()
    clean = {}
    for name, value in (variables or {}).items():
        key = (name or "").strip()
        if key:
            clean[key] = "" if value is None else str(value)
    now = _now()
    with _get_conn() as conn:
        conn.execute("DELETE FROM flow_variables WHERE user_id = ?", (user_id,))
        if clean:
            conn.executemany(
                "INSERT INTO flow_variables (user_id, name, value, updated_at) VALUES (?, ?, ?, ?)",
                [(user_id, k, v, now) for k, v in clean.items()],
            )
        conn.commit()
    return list_flow_variables(user_id)
