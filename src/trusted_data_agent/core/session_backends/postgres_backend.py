"""PostgreSQL session persistence backend (multi-server).

One row per session: the full body lives in a ``body`` JSONB column and the
summary/index fields are denormalized into typed columns, so the table *is* the
index — there is no separate index store and turn-append writes one row (no
whole-session re-serialize at the storage layer beyond the JSONB document).

Selected via ``TDA_SESSION_BACKEND=postgres``; the DSN comes from
``TDA_SESSION_PG_DSN`` (falling back to ``TDA_AUTH_DB_URL`` when that is a
postgres URL). The connection pool is created lazily on first use.
"""
import os
import json
import asyncio
import logging

import asyncpg

from trusted_data_agent.core.config import APP_CONFIG, sessions_filtered_by_user
from trusted_data_agent.core.session_backends.base import SessionPersistenceBackend
from trusted_data_agent.core.session_backends.file_backend import _compute_session_status

logger = logging.getLogger("quart.app")

_TABLE = "uderia_sessions"

# Summary columns selected for listing (mirrors the file backend's summary shape).
_SUMMARY_COLS = (
    "session_id, user_uuid, name, created_at, last_updated, profile_tag, "
    "profile_id, archived, archived_at, is_temporary, temporary_purpose, "
    "models_used, profile_tags_used, genie_metadata, total_tokens, turn_count, "
    "status, provider, model, profile_type"
)


def _dsn_from_env() -> str:
    dsn = os.environ.get("TDA_SESSION_PG_DSN") or os.environ.get("TDA_AUTH_DB_URL")
    if not dsn or not dsn.startswith(("postgres://", "postgresql://")):
        raise RuntimeError(
            "PostgresSessionBackend requires TDA_SESSION_PG_DSN (or a postgres "
            "TDA_AUTH_DB_URL).")
    return dsn


async def _register_json(conn: asyncpg.Connection):
    # Make JSONB columns encode/decode as native Python objects.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog")


def _summary_row_to_dict(row) -> dict:
    return {
        "id": row["session_id"],
        "user_uuid": row["user_uuid"],
        "name": row["name"] or "Unnamed Session",
        "created_at": row["created_at"] or "Unknown",
        "last_updated": row["last_updated"] or row["created_at"] or "Unknown",
        "profile_tag": row["profile_tag"],
        "profile_id": row["profile_id"],
        "archived": bool(row["archived"]),
        "archived_at": row["archived_at"],
        "is_temporary": bool(row["is_temporary"]),
        "temporary_purpose": row["temporary_purpose"],
        "models_used": row["models_used"] or [],
        "profile_tags_used": row["profile_tags_used"] or [],
        "genie_metadata": row["genie_metadata"] or {},
        "total_tokens": row["total_tokens"] or 0,
        "turn_count": row["turn_count"] or 0,
        "status": row["status"] or "unknown",
        "provider": row["provider"] or "Unknown",
        "model": row["model"] or "Unknown",
        "profile_type": row["profile_type"],
    }


def _projection(data: dict) -> tuple:
    """Compute the denormalized summary fields from a session body."""
    input_tokens = data.get("input_tokens", 0) or 0
    output_tokens = data.get("output_tokens", 0) or 0
    total_tokens = input_tokens + output_tokens
    wf = data.get("last_turn_data", {}).get("workflow_history", [])
    if isinstance(wf, list) and wf:
        turn_count = len([t for t in wf if isinstance(t, dict) and t.get("isValid", True)])
    else:
        turn_count = data.get("turn_count", 0)
    return total_tokens, turn_count, _compute_session_status(wf)


class PostgresSessionBackend(SessionPersistenceBackend):
    def __init__(self, dsn: str | None = None):
        self._dsn = dsn or _dsn_from_env()
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()
        self._index_ready = False

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        dsn=self._dsn,
                        min_size=int(os.environ.get("TDA_SESSION_PG_MIN", "1")),
                        max_size=int(os.environ.get("TDA_SESSION_PG_MAX", "10")),
                        init=_register_json,
                    )
        return self._pool

    async def close(self):
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ── full body ────────────────────────────────────────────────────────────
    async def load(self, user_uuid: str, session_id: str) -> dict | None:
        pool = await self._ensure_pool()
        if sessions_filtered_by_user():
            row = await pool.fetchrow(
                f"SELECT body FROM {_TABLE} WHERE session_id=$1 AND user_uuid=$2",
                session_id, user_uuid)
        else:
            row = await pool.fetchrow(
                f"SELECT body FROM {_TABLE} WHERE session_id=$1", session_id)
        return row["body"] if row else None

    async def save(self, user_uuid: str, session_id: str, data: dict) -> bool:
        pool = await self._ensure_pool()
        total_tokens, turn_count, status = _projection(data)
        try:
            await pool.execute(f"""
                INSERT INTO {_TABLE}
                    (session_id, user_uuid, name, created_at, last_updated,
                     profile_tag, profile_id, archived, archived_at,
                     is_temporary, temporary_purpose, models_used,
                     profile_tags_used, genie_metadata,
                     total_tokens, turn_count, status, provider, model, profile_type, body)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
                ON CONFLICT (session_id) DO UPDATE SET
                    user_uuid=EXCLUDED.user_uuid,
                    name=EXCLUDED.name,
                    created_at=EXCLUDED.created_at,
                    last_updated=EXCLUDED.last_updated,
                    profile_tag=EXCLUDED.profile_tag,
                    profile_id=EXCLUDED.profile_id,
                    archived=EXCLUDED.archived,
                    archived_at=EXCLUDED.archived_at,
                    is_temporary=EXCLUDED.is_temporary,
                    temporary_purpose=EXCLUDED.temporary_purpose,
                    models_used=EXCLUDED.models_used,
                    profile_tags_used=EXCLUDED.profile_tags_used,
                    genie_metadata=EXCLUDED.genie_metadata,
                    total_tokens=EXCLUDED.total_tokens,
                    turn_count=EXCLUDED.turn_count,
                    status=EXCLUDED.status,
                    provider=EXCLUDED.provider,
                    model=EXCLUDED.model,
                    profile_type=EXCLUDED.profile_type,
                    body=EXCLUDED.body
            """,
                session_id,
                data.get("user_uuid", "") or user_uuid,
                data.get("name", "New Chat"),
                data.get("created_at"),
                data.get("last_updated"),
                data.get("profile_tag"),
                data.get("profile_id"),
                1 if data.get("archived") else 0,
                data.get("archived_at"),
                1 if data.get("is_temporary") else 0,
                data.get("temporary_purpose"),
                data.get("models_used", []),
                data.get("profile_tags_used", []),
                data.get("genie_metadata", {}),
                total_tokens,
                turn_count,
                status,
                data.get("provider"),
                data.get("model"),
                data.get("profile_type"),
                data,
            )
            return True
        except Exception as e:
            logger.error(f"PostgresSessionBackend.save failed for {session_id}: {e}", exc_info=True)
            return False

    async def delete_record(self, user_uuid: str, session_id: str) -> bool:
        pool = await self._ensure_pool()
        try:
            await pool.execute(f"DELETE FROM {_TABLE} WHERE session_id=$1", session_id)
            return True
        except Exception as e:
            logger.error(f"PostgresSessionBackend.delete_record failed for {session_id}: {e}")
            return False

    async def exists(self, user_uuid: str, session_id: str) -> bool:
        pool = await self._ensure_pool()
        row = await pool.fetchrow(f"SELECT 1 FROM {_TABLE} WHERE session_id=$1", session_id)
        return row is not None

    # ── summary index (the table itself) ─────────────────────────────────────
    @property
    def index_ready(self) -> bool:
        return self._index_ready

    async def init_index(self) -> None:
        pool = await self._ensure_pool()
        try:
            await pool.execute(f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    session_id TEXT PRIMARY KEY,
                    user_uuid TEXT NOT NULL,
                    name TEXT DEFAULT 'New Chat',
                    created_at TEXT,
                    last_updated TEXT,
                    profile_tag TEXT,
                    profile_id TEXT,
                    archived INTEGER DEFAULT 0,
                    archived_at TEXT,
                    is_temporary INTEGER DEFAULT 0,
                    temporary_purpose TEXT,
                    models_used JSONB DEFAULT '[]'::jsonb,
                    profile_tags_used JSONB DEFAULT '[]'::jsonb,
                    genie_metadata JSONB DEFAULT '{{}}'::jsonb,
                    total_tokens INTEGER DEFAULT 0,
                    turn_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'unknown',
                    provider TEXT,
                    model TEXT,
                    profile_type TEXT,
                    body JSONB NOT NULL
                )
            """)
            await pool.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_user_updated "
                f"ON {_TABLE}(user_uuid, last_updated DESC)")
            await pool.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_archived ON {_TABLE}(archived)")
            self._index_ready = True
            logger.info("Postgres session table initialized")
        except Exception as e:
            self._index_ready = False
            logger.error(f"Failed to initialize Postgres session table: {e}", exc_info=True)

    async def ensure_index_ready(self) -> None:
        # The table is authoritative; just ensure it exists. No rebuild needed.
        await self.init_index()

    async def query_summaries(self, user_uuid, include_archived: bool = False,
                              filter_by_user: bool = True) -> list | None:
        if not self._index_ready:
            return None
        pool = await self._ensure_pool()
        try:
            clauses, args = [], []
            if filter_by_user:
                args.append(user_uuid)
                clauses.append(f"user_uuid=${len(args)}")
            if not include_archived:
                clauses.append("archived=0")
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = await pool.fetch(
                f"SELECT {_SUMMARY_COLS} FROM {_TABLE}{where} ORDER BY last_updated DESC", *args)
            return [_summary_row_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Postgres summary query failed: {e}")
            return None

    async def upsert_summary(self, session_id: str, data: dict) -> None:
        # save() already persists body + summary columns. A standalone summary
        # refresh (the fire-and-forget index update) only needs to recompute the
        # derived columns — a light UPDATE, no body rewrite. (No-op if the row
        # doesn't exist yet, which never happens: upsert_summary always follows a
        # save in the call paths.)
        pool = await self._ensure_pool()
        total_tokens, turn_count, status = _projection(data)
        try:
            await pool.execute(f"""
                UPDATE {_TABLE} SET
                    name=$2, last_updated=$3, profile_tag=$4, profile_id=$5,
                    archived=$6, archived_at=$7, is_temporary=$8, temporary_purpose=$9,
                    models_used=$10, profile_tags_used=$11, genie_metadata=$12,
                    total_tokens=$13, turn_count=$14, status=$15,
                    provider=$16, model=$17, profile_type=$18
                WHERE session_id=$1
            """,
                session_id,
                data.get("name", "New Chat"),
                data.get("last_updated"),
                data.get("profile_tag"),
                data.get("profile_id"),
                1 if data.get("archived") else 0,
                data.get("archived_at"),
                1 if data.get("is_temporary") else 0,
                data.get("temporary_purpose"),
                data.get("models_used", []),
                data.get("profile_tags_used", []),
                data.get("genie_metadata", {}),
                total_tokens,
                turn_count,
                status,
                data.get("provider"),
                data.get("model"),
                data.get("profile_type"),
            )
        except Exception as e:
            logger.warning(f"Postgres upsert_summary failed for {session_id}: {e}")

    async def delete_summary(self, session_id: str) -> None:
        await self.delete_record("", session_id)

    async def rebuild_summaries(self) -> None:
        # Authoritative table — summary columns recomputed from body for safety.
        if not self._index_ready:
            return
        pool = await self._ensure_pool()
        try:
            rows = await pool.fetch(f"SELECT session_id, body FROM {_TABLE}")
            for r in rows:
                body = r["body"]
                total_tokens, turn_count, status = _projection(body)
                await pool.execute(
                    f"UPDATE {_TABLE} SET total_tokens=$2, turn_count=$3, status=$4 "
                    f"WHERE session_id=$1", r["session_id"], total_tokens, turn_count, status)
            logger.info(f"Postgres session summaries refreshed: {len(rows)} rows")
        except Exception as e:
            logger.error(f"Postgres rebuild_summaries failed: {e}", exc_info=True)

    # ── scans (used if the index is flagged unavailable) ─────────────────────
    async def scan_summaries(self, user_uuid, include_archived: bool = False,
                             filter_by_user: bool = True) -> list:
        pool = await self._ensure_pool()
        clauses, args = [], []
        if filter_by_user:
            args.append(user_uuid)
            clauses.append(f"user_uuid=${len(args)}")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await pool.fetch(
            f"SELECT {_SUMMARY_COLS} FROM {_TABLE}{where} ORDER BY last_updated DESC", *args)
        return [_summary_row_to_dict(r) for r in rows]

    async def scan_all_bodies(self, include_archived: bool = True) -> list:
        pool = await self._ensure_pool()
        if include_archived:
            rows = await pool.fetch(f"SELECT body FROM {_TABLE}")
        else:
            rows = await pool.fetch(f"SELECT body FROM {_TABLE} WHERE archived=0")
        return [r["body"] for r in rows]
