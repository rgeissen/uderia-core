"""
Canvas DB — CRUD + version history for the persisted Canvas component (Option A).

Mirrors the flow_db.py pattern: isolated synchronous-SQLite module with a
self-applying schema migration (runs once per process at import). Callers run
these in Quart's thread-pool executor when invoked from async routes.

Identity model: a canvas is keyed by canvas_id (stable, user-scoped) so it can be
opened from any session. The natural upsert key is the normalized title, which
preserves the LLM's existing "call TDA_Canvas with the SAME title to update it"
behavior — now durable instead of an in-memory, page-lifetime JS Map.
"""

import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trusted_data_agent.core.versioning import prune_versions
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")

_DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"
_SCHEMA_APPLIED = False  # module-level guard so migration runs once per process


def _ensure_schema() -> None:
    """Apply 40_canvases.sql if the canvases table does not yet exist."""
    global _SCHEMA_APPLIED
    if _SCHEMA_APPLIED:
        return
    try:
        conn = sqlite_util.connect(str(_DB_PATH))
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='canvases'"
        ).fetchone()
        if not exists:
            sql_path = Path(__file__).resolve().parents[3] / "schema" / "40_canvases.sql"
            if sql_path.exists():
                conn.executescript(sql_path.read_text())
                logger.info("canvas_db: applied 40_canvases.sql")
            else:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS canvases (
                        canvas_id    TEXT PRIMARY KEY,
                        user_id      TEXT NOT NULL,
                        title        TEXT NOT NULL,
                        title_key    TEXT NOT NULL,
                        content      TEXT NOT NULL,
                        language     TEXT,
                        sources      TEXT,
                        session_id   TEXT,
                        created_at   TEXT DEFAULT (datetime('now')),
                        updated_at   TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_canvases_user_id ON canvases(user_id);
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_canvases_user_title ON canvases(user_id, title_key);
                    CREATE TABLE IF NOT EXISTS canvas_versions (
                        version_id   TEXT PRIMARY KEY,
                        canvas_id    TEXT NOT NULL REFERENCES canvases(canvas_id) ON DELETE CASCADE,
                        user_id      TEXT NOT NULL,
                        content      TEXT NOT NULL,
                        language     TEXT,
                        version_num  INTEGER NOT NULL,
                        label        TEXT,
                        created_at   TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_canvas_versions_canvas_id ON canvas_versions(canvas_id);
                    CREATE INDEX IF NOT EXISTS idx_canvas_versions_user_id ON canvas_versions(user_id);
                """)
                logger.info("canvas_db: created canvas tables (inline fallback)")
            conn.commit()
        conn.close()
        _SCHEMA_APPLIED = True
    except Exception as exc:
        logger.error(f"canvas_db._ensure_schema failed: {exc}", exc_info=True)


_ensure_schema()  # run once at import time


def _get_conn() -> sqlite3.Connection:
    conn = sqlite_util.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_title(title: Optional[str]) -> str:
    return (title or "Canvas").strip().lower()


def _row_to_canvas(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Version helpers (internal)
# ---------------------------------------------------------------------------

def _record_version(
    conn: sqlite3.Connection,
    canvas_id: str,
    user_id: str,
    content: str,
    language: Optional[str],
    label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Append a version row for this canvas. De-duplicates: if the content is
    identical to the most recent version, no row is created (returns None).
    Must be called inside an open connection/transaction.
    """
    last = conn.execute(
        "SELECT content, version_num FROM canvas_versions "
        "WHERE canvas_id = ? ORDER BY version_num DESC LIMIT 1",
        (canvas_id,),
    ).fetchone()

    if last and last["content"] == content:
        return None

    version_num = (last["version_num"] + 1) if last else 1
    version_id = f"cv-{uuid.uuid4().hex[:12]}"
    auto_label = label or f"v{version_num}"
    now = _now()
    conn.execute(
        """INSERT INTO canvas_versions
           (version_id, canvas_id, user_id, content, language, version_num, label, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (version_id, canvas_id, user_id, content, language, version_num, auto_label, now),
    )
    # Resource efficiency: bound history to the shared retention cap.
    prune_versions(conn, "canvas_versions", "canvas_id", canvas_id)
    return {
        "version_id": version_id,
        "canvas_id": canvas_id,
        "version_num": version_num,
        "label": auto_label,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# Canvas CRUD
# ---------------------------------------------------------------------------

@_signals.announces("canvas", "created", user="user_id", ids=("canvas_id",))
def create_canvas(
    user_id: str,
    title: str,
    content: str,
    language: Optional[str] = None,
    sources: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new canvas (records v1). Raises sqlite3.IntegrityError on duplicate title."""
    canvas_id = f"canvas-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO canvases
               (canvas_id, user_id, title, title_key, content, language, sources, session_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (canvas_id, user_id, title or "Canvas", _normalize_title(title),
             content, language, sources, session_id, now, now),
        )
        _record_version(conn, canvas_id, user_id, content, language, label="v1")
        conn.commit()
    return get_canvas(canvas_id, user_id)


def upsert_canvas(
    user_id: str,
    title: str,
    content: str,
    language: Optional[str] = None,
    sources: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create-or-update by normalized title (the LLM's existing identity model).
    If a canvas with this title exists for the user, update it (recording a new
    version when content changed); otherwise create a fresh canvas.
    Returns {"canvas": <record>, "created": bool}.
    """
    existing = get_canvas_by_title(user_id, title)
    if existing:
        updated = update_canvas(
            existing["canvas_id"], user_id,
            content=content, language=language, sources=sources,
        )
        return {"canvas": updated, "created": False}
    created = create_canvas(user_id, title, content, language, sources, session_id)
    return {"canvas": created, "created": True}


def get_canvas(canvas_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single canvas by ID (user-scoped)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM canvases WHERE canvas_id = ? AND user_id = ?",
            (canvas_id, user_id),
        ).fetchone()
    return _row_to_canvas(row) if row else None


def get_canvas_by_title(user_id: str, title: str) -> Optional[Dict[str, Any]]:
    """Fetch a canvas by normalized title (user-scoped) — the upsert lookup."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM canvases WHERE user_id = ? AND title_key = ?",
            (user_id, _normalize_title(title)),
        ).fetchone()
    return _row_to_canvas(row) if row else None


def list_canvases(user_id: str) -> List[Dict[str, Any]]:
    """List all canvases for a user, most recently updated first (no content body)."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT canvas_id, user_id, title, language, sources, session_id, created_at, updated_at "
            "FROM canvases WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


class CanvasTitleTaken(ValueError):
    """Another canvas of this user already carries the requested title."""


@_signals.announces("canvas", "updated", user="user_id", ids=("canvas_id",))
def update_canvas(
    canvas_id: str,
    user_id: str,
    content: Optional[str] = None,
    title: Optional[str] = None,
    language: Optional[str] = None,
    sources: Optional[str] = None,
    record_version: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Update mutable fields of a canvas. When `content` changes, a new version is
    recorded (de-duplicated) unless ``record_version`` is False — the editor's
    autosave stores what the user types without adding a version per pause, and
    records one when the user leaves the canvas. Returns the updated record, or
    None if not found. Raises CanvasTitleTaken when a rename collides with
    another canvas of the same user (titles are the agent's identity for upserts).
    """
    existing = get_canvas(canvas_id, user_id)
    if not existing:
        return None

    if title is not None:
        other = get_canvas_by_title(user_id, title)
        if other and other["canvas_id"] != canvas_id:
            raise CanvasTitleTaken(title)

    updates: Dict[str, Any] = {"updated_at": _now()}
    if title is not None:
        updates["title"] = title
        updates["title_key"] = _normalize_title(title)
    if language is not None:
        updates["language"] = language
    if sources is not None:
        updates["sources"] = sources
    if content is not None:
        updates["content"] = content

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [canvas_id, user_id]

    with _get_conn() as conn:
        conn.execute(
            f"UPDATE canvases SET {set_clause} WHERE canvas_id = ? AND user_id = ?",
            values,
        )
        if record_version and content is not None:
            # _record_version de-duplicates against the latest version, so this
            # also covers a checkpoint after autosaves (the stored content already
            # equals the editor's, but the history does not have it yet).
            _record_version(conn, canvas_id, user_id, content, language or existing["language"])
        conn.commit()
    return get_canvas(canvas_id, user_id)


@_signals.announces("canvas", "deleted", user="user_id", ids=("canvas_id",))
def delete_canvas(canvas_id: str, user_id: str) -> bool:
    """Delete a canvas and (via cascade) all its versions. Returns True if deleted."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM canvases WHERE canvas_id = ? AND user_id = ?",
            (canvas_id, user_id),
        )
        conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def list_versions(canvas_id: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Return all versions for a canvas, oldest-first. Includes `content` so the
    renderer's version dropdown can preview/restore without an extra round-trip.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT version_id, canvas_id, user_id, content, language, version_num, label, created_at "
            "FROM canvas_versions WHERE canvas_id = ? AND user_id = ? ORDER BY version_num ASC",
            (canvas_id, user_id),
        ).fetchall()
    return [dict(r) for r in rows]


@_signals.announces("canvas", "restored", user="user_id", ids=("canvas_id",))
def restore_version(version_id: str, canvas_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Restore a canvas's content from a prior version. Sets the canvas content to
    that version and records a new version (so the restore itself is tracked).
    Returns the updated canvas, or None if not found.
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT content, language, version_num FROM canvas_versions "
            "WHERE version_id = ? AND canvas_id = ? AND user_id = ?",
            (version_id, canvas_id, user_id),
        ).fetchone()
        if not row:
            return None
        now = _now()
        conn.execute(
            "UPDATE canvases SET content = ?, language = COALESCE(?, language), updated_at = ? "
            "WHERE canvas_id = ? AND user_id = ?",
            (row["content"], row["language"], now, canvas_id, user_id),
        )
        _record_version(
            conn, canvas_id, user_id, row["content"], row["language"],
            label=f"Restored v{row['version_num']}",
        )
        conn.commit()
    return get_canvas(canvas_id, user_id)
