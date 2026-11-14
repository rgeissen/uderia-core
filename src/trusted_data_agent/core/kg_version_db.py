"""
KG Version DB — version history for knowledge graphs.

Follows the same design pattern as flow_db / canvas_db versioning (self-applying
schema, snapshot / list_versions / restore helpers, dedup against the latest
version, shared retention cap via core.versioning) — adapted to KG's graph model:
a snapshot is the full graph (entities + relationships) serialized to JSON and
gzip-compressed (graphs compress ~10x).

This module is pure synchronous SQLite over tda_auth.db and is backend-agnostic:
it stores serialized graph payloads supplied by the caller. The caller (the KG
component handler) gathers entities/relationships from the active GraphStore
(any backend) and applies a restore via clear_graph() + import_bulk().

Resource efficiency:
  • hash dedup     — identical-to-latest snapshots are skipped (no row created)
  • gzip payload   — full-graph blobs stored compressed
  • retention cap  — only the most recent MAX_VERSIONS_RETAINED snapshots kept
  • coarse triggers— the handler snapshots only on explicit request and before
                     destructive ops, never per entity mutation
"""

import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from trusted_data_agent.core.versioning import (
    content_hash,
    decode_payload,
    encode_payload,
    now_iso,
    prune_versions,
)
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")

_DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"
_SCHEMA_APPLIED = False
_COMPRESS = True  # KG graphs are large — always compress the snapshot blob


def _ensure_schema() -> None:
    global _SCHEMA_APPLIED
    if _SCHEMA_APPLIED:
        return
    try:
        conn = sqlite_util.connect(str(_DB_PATH))
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_versions'"
        ).fetchone()
        if not exists:
            sql_path = Path(__file__).resolve().parents[3] / "schema" / "41_kg_versions.sql"
            if sql_path.exists():
                conn.executescript(sql_path.read_text())
                logger.info("kg_version_db: applied 41_kg_versions.sql")
            else:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS kg_versions (
                        version_id   TEXT PRIMARY KEY,
                        kg_id        TEXT NOT NULL,
                        user_id      TEXT NOT NULL,
                        payload      TEXT NOT NULL,
                        compressed   INTEGER DEFAULT 1,
                        content_hash TEXT NOT NULL,
                        entity_count INTEGER DEFAULT 0,
                        rel_count    INTEGER DEFAULT 0,
                        version_num  INTEGER NOT NULL,
                        label        TEXT,
                        created_at   TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_kg_versions_kg_id   ON kg_versions(kg_id);
                    CREATE INDEX IF NOT EXISTS idx_kg_versions_user_id ON kg_versions(user_id);
                """)
                logger.info("kg_version_db: created kg_versions table (inline fallback)")
            conn.commit()
        conn.close()
        _SCHEMA_APPLIED = True
    except Exception as exc:
        logger.error(f"kg_version_db._ensure_schema failed: {exc}", exc_info=True)


_ensure_schema()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite_util.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _serialize(entities: List[Dict], relationships: List[Dict]) -> str:
    # sort_keys gives a stable serialization so dedup hashing is deterministic.
    return json.dumps(
        {"entities": entities or [], "relationships": relationships or []},
        sort_keys=True,
        ensure_ascii=False,
    )


@_signals.announces("knowledge_graph", "snapshot", user="user_id", ids=("kg_id",), aspect="versions")
def snapshot_kg(
    kg_id: str,
    user_id: str,
    entities: List[Dict],
    relationships: List[Dict],
    label: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Record the supplied graph as a new version. De-duplicates: if the serialized
    graph is identical to the latest version, no row is created (returns None).
    """
    payload_json = _serialize(entities, relationships)
    h = content_hash(payload_json)

    with _get_conn() as conn:
        last = conn.execute(
            "SELECT content_hash, version_num FROM kg_versions "
            "WHERE kg_id = ? ORDER BY version_num DESC LIMIT 1",
            (kg_id,),
        ).fetchone()
        if last and last["content_hash"] == h:
            return None  # dedup — graph unchanged since the last snapshot

        version_num = (last["version_num"] + 1) if last else 1
        version_id = f"kgv-{uuid.uuid4().hex[:12]}"
        auto_label = label or f"v{version_num}"
        now = now_iso()
        conn.execute(
            """INSERT INTO kg_versions
               (version_id, kg_id, user_id, payload, compressed, content_hash,
                entity_count, rel_count, version_num, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (version_id, kg_id, user_id,
             encode_payload(payload_json, _COMPRESS), 1 if _COMPRESS else 0, h,
             len(entities or []), len(relationships or []),
             version_num, auto_label, now),
        )
        prune_versions(conn, "kg_versions", "kg_id", kg_id)
        conn.commit()

    return {
        "version_id": version_id,
        "kg_id": kg_id,
        "version_num": version_num,
        "label": auto_label,
        "entity_count": len(entities or []),
        "rel_count": len(relationships or []),
        "created_at": now,
    }


def list_versions(kg_id: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Return version metadata (oldest first) WITHOUT the heavy payload — the list
    view never needs the full graph, only counts and labels.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT version_id, kg_id, user_id, entity_count, rel_count, "
            "version_num, label, created_at "
            "FROM kg_versions WHERE kg_id = ? AND user_id = ? ORDER BY version_num ASC",
            (kg_id, user_id),
        ).fetchall()
    return [dict(r) for r in rows]


def get_version_graph(version_id: str, kg_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Decode and return {entities, relationships} for a single version (for restore)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT payload, compressed, version_num FROM kg_versions "
            "WHERE version_id = ? AND kg_id = ? AND user_id = ?",
            (version_id, kg_id, user_id),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(decode_payload(row["payload"], row["compressed"]))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    return {
        "entities": data.get("entities", []),
        "relationships": data.get("relationships", []),
        "version_num": row["version_num"],
    }


@_signals.announces("knowledge_graph", "deleted", user="user_id", ids=("kg_id",), aspect="versions")
def delete_version(version_id: str, kg_id: str, user_id: str) -> bool:
    """Remove ONE snapshot of the caller's graph. Returns whether a row went."""
    _ensure_schema()
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM kg_versions WHERE version_id = ? AND kg_id = ? AND user_id = ?",
                           (version_id, kg_id, user_id))
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def delete_versions_for_kg(kg_id: str) -> int:
    """Remove all version history for a KG (called when the KG itself is deleted)."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM kg_versions WHERE kg_id = ?", (kg_id,))
        conn.commit()
    return cur.rowcount
