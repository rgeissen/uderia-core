"""Retrieval feedback — per-document user ratings for the Focus lane.

The Optimize lane has learned from its own successes for a long time (the champion
store). The Focus lane never had an equivalent: a user could reject an answer built
from a bad document over and over and nothing accumulated. This records the votes
against the DOCUMENTS that served each answer, so a repeatedly-rejected source
becomes visible in Corpus Health as a curation item.

**Records only.** Nothing here influences retrieval, reranking or scoring — a
statistical signal surfaces for a human, it never acts alone (the same rule the
retrieval-anomaly detector follows, and for the same reason: a document can serve a
poor answer for reasons that are not the document's fault).

Fail-open throughout: a vote must never fail because this table is unavailable.
Self-applying table (sqlite_util pattern) — no migration step.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

_DDL_STATEMENTS = (
    """
CREATE TABLE IF NOT EXISTS retrieval_feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid     TEXT NOT NULL,
    collection_id TEXT,
    document_id   TEXT NOT NULL,
    query_text    TEXT,
    vote          INTEGER NOT NULL,
    session_id    TEXT,
    turn          INTEGER,
    created_at    REAL NOT NULL
)
""",
    """
CREATE INDEX IF NOT EXISTS ix_rf_doc
    ON retrieval_feedback(user_uuid, collection_id, document_id)
""",
    """
CREATE INDEX IF NOT EXISTS ix_rf_turn
    ON retrieval_feedback(user_uuid, session_id, turn)
""",
)

# The user query is kept for a future similarity-based reading; bounded so a long
# prompt cannot bloat the table.
QUERY_TEXT_MAX = 500


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (getattr(APP_CONFIG, "AUTH_DB_PATH", None) or "tda_auth.db").replace("sqlite:///", "")


def _connect(db_path: Optional[str] = None):
    conn = sqlite_util.connect(_db_path(db_path))
    for stmt in _DDL_STATEMENTS:
        conn.execute(stmt)
    conn.commit()
    return conn


def record(user_uuid: str, *, document_ids, vote: int,
           collection_id=None, query_text: Optional[str] = None,
           session_id: Optional[str] = None, turn=None,
           db_path: Optional[str] = None) -> int:
    """One row per document served by a rated turn. Returns the rows written.

    Append-only: a flip writes new rows, and :func:`doc_summary` resolves the current
    state per (turn, document). Fail-open — returns 0 on any error.
    """
    docs = [str(d) for d in (document_ids or []) if d]
    if not user_uuid or not docs:
        return 0
    try:
        now = time.time()
        q = (query_text or "")[:QUERY_TEXT_MAX]
        cid = None if collection_id is None else str(collection_id)
        t = None if turn is None else int(turn)
        conn = _connect(db_path)
        try:
            conn.executemany(
                "INSERT INTO retrieval_feedback (user_uuid, collection_id, document_id,"
                " query_text, vote, session_id, turn, created_at) VALUES (?,?,?,?,?,?,?,?)",
                [(user_uuid, cid, d, q, int(vote), session_id, t, now) for d in docs])
            conn.commit()
        finally:
            conn.close()
        return len(docs)
    except Exception as e:  # noqa: BLE001 — a vote must never fail on this table
        logger.warning(f"retrieval feedback record failed: {e}")
        return 0


def doc_summary(user_uuid: str, collection_id=None,
                db_path: Optional[str] = None) -> dict:
    """``{document_id: {"up": n, "down": n, "net": n}}`` for ONE user.

    Resolves the LATEST row per (session, turn, document) before counting, so a user
    who flips their vote is counted once, in their current position — a naive SUM
    would double-count every change of mind. A cleared vote (0) contributes nothing
    but correctly cancels the earlier up/down.
    """
    if not user_uuid:
        return {}
    try:
        conn = _connect(db_path)
        try:
            sql = (
                "SELECT rf.document_id, rf.vote FROM retrieval_feedback rf "
                "JOIN (SELECT session_id, turn, document_id, MAX(created_at) AS mx, MAX(id) AS mid"
                "      FROM retrieval_feedback WHERE user_uuid = ?"
                + ("" if collection_id is None else " AND collection_id = ?") +
                "      GROUP BY session_id, turn, document_id) latest "
                "  ON rf.session_id IS latest.session_id AND rf.turn IS latest.turn "
                " AND rf.document_id = latest.document_id AND rf.id = latest.mid "
                "WHERE rf.user_uuid = ?"
                + ("" if collection_id is None else " AND rf.collection_id = ?"))
            params = ([user_uuid] if collection_id is None
                      else [user_uuid, str(collection_id)])
            params += ([user_uuid] if collection_id is None
                       else [user_uuid, str(collection_id)])
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"retrieval feedback summary failed: {e}")
        return {}

    out: dict = {}
    for doc_id, vote in rows:
        cell = out.setdefault(str(doc_id), {"up": 0, "down": 0, "net": 0})
        if vote > 0:
            cell["up"] += 1
        elif vote < 0:
            cell["down"] += 1
    for cell in out.values():
        cell["net"] = cell["up"] - cell["down"]
    return out


def lowest_rated(user_uuid: str, collection_id=None, limit: int = 5,
                 db_path: Optional[str] = None) -> list:
    """The documents users rejected most, worst first — the curation queue.

    Only documents with a NEGATIVE net appear: a document nobody complained about is
    not a finding, and listing it would pad a curation list with noise.
    """
    summary = doc_summary(user_uuid, collection_id, db_path=db_path)
    ranked = [{"document_id": d, **c} for d, c in summary.items() if c["net"] < 0]
    ranked.sort(key=lambda r: (r["net"], -r["down"]))
    return ranked[:max(0, int(limit))]
