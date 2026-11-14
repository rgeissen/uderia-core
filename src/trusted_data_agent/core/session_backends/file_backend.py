"""File-based session persistence backend (default; zero-config).

Owns the historical on-disk layout — one JSON body per session at
``<sessions_dir>/<user_uuid>/<session_id>.json`` — plus the SQLite summary
index at ``<sessions_dir>/session_index.db``. This is a verbatim extraction of
the logic that previously lived in ``session_manager``; behaviour is identical.
"""
import os
import json
import asyncio
import logging
import tempfile
from pathlib import Path

import aiofiles

from trusted_data_agent.core.config import APP_CONFIG, sessions_filtered_by_user
from trusted_data_agent.core import sqlite_util
from trusted_data_agent.core.utils import get_project_root
from trusted_data_agent.core.session_backends.base import SessionPersistenceBackend

logger = logging.getLogger("quart.app")

# Default location (overridable via TDA_SESSIONS_DIR / factory). Kept module-level
# so callers that only need the default path (e.g. uploads) can import it.
DEFAULT_SESSIONS_DIR = get_project_root() / "tda_sessions"

# Fields the summary index materializes from a session body.
_INDEX_KEYS = frozenset({
    "id", "name", "created_at", "last_updated", "profile_tag", "profile_id",
    "archived", "archived_at", "is_temporary", "temporary_purpose",
    "models_used", "profile_tags_used", "genie_metadata",
    "input_tokens", "output_tokens", "provider", "model", "profile_type",
    "user_uuid",
})


def _compute_session_status(wf) -> str:
    """Derive session status ('empty'|'success'|'partial'|'failed') from workflow."""
    if not isinstance(wf, list):
        return "unknown"
    valid_turns = [t for t in wf if isinstance(t, dict) and t.get("isValid", True)]
    if not valid_turns:
        return "empty"
    from trusted_data_agent.core import turn_states as _ts
    has_failure = any(_ts.is_failure(t.get("status")) or t.get("error_message")
                      for t in valid_turns)
    has_success = any(t.get("final_summary") for t in valid_turns)
    if has_failure and has_success:
        return "partial"
    if has_failure:
        return "failed"
    return "success"


class FileSessionBackend(SessionPersistenceBackend):
    def __init__(self, sessions_dir: Path | None = None):
        self.sessions_dir = Path(sessions_dir) if sessions_dir else DEFAULT_SESSIONS_DIR
        self.index_db = self.sessions_dir / "session_index.db"
        self._index_ready = False
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not create session directory '{self.sessions_dir}': {e}")

    # ── path resolution ──────────────────────────────────────────────────────
    def _session_path(self, user_uuid: str, session_id: str) -> Path | None:
        if not user_uuid or not session_id:
            logger.error("Attempted to get session path with missing user_uuid or session_id.")
            return None
        safe_user_uuid = "".join(c for c in user_uuid if c.isalnum() or c in ['-', '_'])
        safe_session_id = "".join(c for c in session_id if c.isalnum() or c in ['-', '_'])
        return self.sessions_dir / safe_user_uuid / f"{safe_session_id}.json"

    def _find_path(self, user_uuid: str, session_id: str) -> Path | None:
        safe_session_id = "".join(c for c in session_id if c.isalnum() or c in ['-', '_'])
        if sessions_filtered_by_user():
            path = self._session_path(user_uuid, session_id)
            if path and path.is_file():
                return path
            return None
        # Filtering off: search all user directories for the session id.
        if not self.sessions_dir.is_dir():
            return None
        for user_dir in self.sessions_dir.iterdir():
            if user_dir.is_dir():
                potential = user_dir / f"{safe_session_id}.json"
                if potential.is_file():
                    return potential
        return None

    # ── full body ────────────────────────────────────────────────────────────
    async def load(self, user_uuid: str, session_id: str) -> dict | None:
        session_path = self._find_path(user_uuid, session_id)
        if not session_path:
            logger.warning(f"Session file not found for session_id: {session_id}")
            return None
        try:
            if session_path.is_file():
                async with aiofiles.open(session_path, 'r', encoding='utf-8') as f:
                    return json.loads(await f.read())
            return None
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Error loading session file '{session_path}': {e}", exc_info=True)
            return None

    async def save(self, user_uuid: str, session_id: str, data: dict) -> bool:
        session_path = self._session_path(user_uuid, session_id)
        if not session_path:
            logger.error(f"Cannot save session '{session_id}' for user '{user_uuid}': Invalid path.")
            return False
        try:
            session_path.parent.mkdir(parents=True, exist_ok=True)
            json_content = json.dumps(data, indent=2)
            # Atomic write: temp file in the same dir, then os.replace (atomic on POSIX).
            temp_fd, temp_path = tempfile.mkstemp(
                dir=str(session_path.parent), suffix='.tmp', prefix='.session_')
            try:
                os.close(temp_fd)
                async with aiofiles.open(temp_path, 'w', encoding='utf-8') as f:
                    await f.write(json_content)
                os.replace(temp_path, str(session_path))
            except BaseException:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise
            return True
        except OSError as e:
            logger.error(f"Error saving session file '{session_path}': {e}", exc_info=True)
            return False

    async def delete_record(self, user_uuid: str, session_id: str) -> bool:
        path = self._find_path(user_uuid, session_id)
        if not path:
            return True  # already gone (idempotent)
        try:
            path.unlink()
            return True
        except OSError as e:
            logger.error(f"Error deleting session file '{path}': {e}")
            return False

    async def exists(self, user_uuid: str, session_id: str) -> bool:
        return self._find_path(user_uuid, session_id) is not None

    # ── summary index ────────────────────────────────────────────────────────
    @property
    def index_ready(self) -> bool:
        return self._index_ready

    async def _run_index(self, fn):
        """Run a sync ``fn(conn)`` against the session-index DB through the process-wide SQLite
        lock (``sqlite_util``) on a worker thread.

        The index DB shares the process-global unix VFS mutex with every other SQLite file
        (``tda_auth.db`` etc.), so it MUST route through the same ``_DB_LOCK`` as everything else.
        ``aiosqlite`` would run each connection on its own thread OUTSIDE that lock and can
        deadlock via the VFS-mutex⇄file-lock inversion — the exact freeze the lock layer exists
        to prevent. See docs/Architecture/SQLITE_CONCURRENCY.md."""
        def _work():
            conn = sqlite_util.connect(str(self.index_db))
            try:
                return fn(conn)
            finally:
                conn.close()
        return await asyncio.to_thread(_work)

    async def init_index(self) -> None:
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)

            def _work(db):
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS session_index (
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
                        models_used TEXT DEFAULT '[]',
                        profile_tags_used TEXT DEFAULT '[]',
                        genie_metadata TEXT DEFAULT '{}',
                        total_tokens INTEGER DEFAULT 0,
                        turn_count INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'unknown',
                        provider TEXT,
                        model TEXT,
                        profile_type TEXT
                    )
                """)
                for col, typedef in [
                    ("total_tokens", "INTEGER DEFAULT 0"),
                    ("turn_count", "INTEGER DEFAULT 0"),
                    ("status", "TEXT DEFAULT 'unknown'"),
                    ("provider", "TEXT"),
                    ("model", "TEXT"),
                    ("profile_type", "TEXT"),
                ]:
                    try:
                        db.execute(f"ALTER TABLE session_index ADD COLUMN {col} {typedef}")
                    except Exception:
                        pass
                db.execute("CREATE INDEX IF NOT EXISTS idx_si_user ON session_index(user_uuid)")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_si_user_updated "
                    "ON session_index(user_uuid, last_updated DESC)")
                db.commit()

            await self._run_index(_work)
            self._index_ready = True
            logger.info("Session index database initialized")
        except Exception as e:
            self._index_ready = False
            logger.error(f"Failed to initialize session index: {e}", exc_info=True)

    async def ensure_index_ready(self) -> None:
        """Init the index and rebuild it if missing/empty/stale (startup hook)."""
        await self.init_index()
        needs_rebuild = (not self.index_db.exists()) or self.index_db.stat().st_size < 4096
        if not needs_rebuild:
            try:
                def _work(db):
                    row = db.execute("SELECT COUNT(*) FROM session_index").fetchone()
                    if row[0] == 0:
                        return True
                    tok = db.execute("SELECT MAX(total_tokens) FROM session_index").fetchone()
                    if tok and (tok[0] is None or tok[0] == 0):
                        logger.info("Session index missing enrichment columns, rebuilding...")
                        return True
                    return False
                needs_rebuild = await self._run_index(_work)
            except Exception:
                needs_rebuild = True
        if needs_rebuild:
            logger.info("Session index is empty or missing, rebuilding from session files...")
            await self.rebuild_summaries()

    async def upsert_summary(self, session_id: str, data: dict) -> None:
        if not self._index_ready:
            return
        try:
            genie_metadata = data.get("genie_metadata", {})
            input_tokens = data.get("input_tokens", 0) or 0
            output_tokens = data.get("output_tokens", 0) or 0
            total_tokens = input_tokens + output_tokens
            wf = data.get("last_turn_data", {}).get("workflow_history", [])
            if isinstance(wf, list) and wf:
                turn_count = len([t for t in wf if isinstance(t, dict) and t.get("isValid", True)])
            else:
                turn_count = data.get("turn_count", 0)
            status = _compute_session_status(wf)
            params = (
                session_id,
                data.get("user_uuid", ""),
                data.get("name", "New Chat"),
                data.get("created_at"),
                data.get("last_updated"),
                data.get("profile_tag"),
                data.get("profile_id"),
                1 if data.get("archived") else 0,
                data.get("archived_at"),
                1 if data.get("is_temporary") else 0,
                data.get("temporary_purpose"),
                json.dumps(data.get("models_used", [])),
                json.dumps(data.get("profile_tags_used", [])),
                json.dumps(genie_metadata),
                total_tokens,
                turn_count,
                status,
                data.get("provider"),
                data.get("model"),
                data.get("profile_type"),
            )

            def _work(db):
                db.execute("""
                    INSERT INTO session_index
                        (session_id, user_uuid, name, created_at, last_updated,
                         profile_tag, profile_id, archived, archived_at,
                         is_temporary, temporary_purpose, models_used,
                         profile_tags_used, genie_metadata,
                         total_tokens, turn_count, status, provider, model, profile_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        name=excluded.name,
                        last_updated=excluded.last_updated,
                        profile_tag=excluded.profile_tag,
                        profile_id=excluded.profile_id,
                        archived=excluded.archived,
                        archived_at=excluded.archived_at,
                        is_temporary=excluded.is_temporary,
                        temporary_purpose=excluded.temporary_purpose,
                        models_used=excluded.models_used,
                        profile_tags_used=excluded.profile_tags_used,
                        genie_metadata=excluded.genie_metadata,
                        total_tokens=excluded.total_tokens,
                        turn_count=excluded.turn_count,
                        status=excluded.status,
                        provider=excluded.provider,
                        model=excluded.model,
                        profile_type=excluded.profile_type
                """, params)
                db.commit()

            await self._run_index(_work)
        except Exception as e:
            logger.warning(f"Failed to upsert session index for {session_id}: {e}")

    async def delete_summary(self, session_id: str) -> None:
        if not self._index_ready:
            return
        try:
            def _work(db):
                db.execute("DELETE FROM session_index WHERE session_id=?", (session_id,))
                db.commit()
            await self._run_index(_work)
        except Exception as e:
            logger.warning(f"Failed to delete session index entry for {session_id}: {e}")

    async def query_summaries(self, user_uuid, include_archived: bool = False,
                              filter_by_user: bool = True) -> list | None:
        if not self._index_ready:
            return None
        try:
            def _work(db):
                import sqlite3 as _sqlite3
                db.row_factory = _sqlite3.Row
                if filter_by_user:
                    if include_archived:
                        cursor = db.execute(
                            "SELECT * FROM session_index WHERE user_uuid=? ORDER BY last_updated DESC",
                            (user_uuid,))
                    else:
                        cursor = db.execute(
                            "SELECT * FROM session_index WHERE user_uuid=? AND archived=0 ORDER BY last_updated DESC",
                            (user_uuid,))
                else:
                    if include_archived:
                        cursor = db.execute("SELECT * FROM session_index ORDER BY last_updated DESC")
                    else:
                        cursor = db.execute(
                            "SELECT * FROM session_index WHERE archived=0 ORDER BY last_updated DESC")
                return cursor.fetchall()

            rows = await self._run_index(_work)
            summaries = []
            for row in rows:
                summaries.append({
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
                    "models_used": json.loads(row["models_used"] or "[]"),
                    "profile_tags_used": json.loads(row["profile_tags_used"] or "[]"),
                    "genie_metadata": json.loads(row["genie_metadata"] or "{}"),
                    "total_tokens": row["total_tokens"] or 0,
                    "turn_count": row["turn_count"] or 0,
                    "status": row["status"] or "unknown",
                    "provider": row["provider"] or "Unknown",
                    "model": row["model"] or "Unknown",
                    "profile_type": row["profile_type"],
                })
            return summaries
        except Exception as e:
            logger.warning(f"Session index query failed, will fall back to file scan: {e}")
            return None

    async def _read_metadata_only(self, session_file: Path) -> dict | None:
        async with aiofiles.open(session_file, 'r', encoding='utf-8') as f:
            content = await f.read()
        full_data = json.loads(content)
        del content
        metadata = {k: full_data[k] for k in _INDEX_KEYS if k in full_data}
        wf = full_data.get("last_turn_data", {}).get("workflow_history", [])
        if isinstance(wf, list) and wf:
            metadata["turn_count"] = len([t for t in wf if isinstance(t, dict) and t.get("isValid", True)])
        del full_data
        return metadata if metadata else None

    async def rebuild_summaries(self) -> None:
        if not self._index_ready:
            logger.warning("Cannot rebuild session index: not initialized")
            return
        count = errors = 0
        try:
            if not self.sessions_dir.is_dir():
                return
            for session_file in self.sessions_dir.glob("**/*.json"):
                try:
                    data = await self._read_metadata_only(session_file)
                    if data:
                        session_id = data.get("id", session_file.stem)
                        await self.upsert_summary(session_id, data)
                        count += 1
                        if count % 50 == 0:
                            await asyncio.sleep(0)
                except Exception as e:
                    errors += 1
                    logger.debug(f"Skipped {session_file.name} during index rebuild: {e}")
            logger.info(f"Session index rebuilt: {count} sessions indexed, {errors} errors")
        except Exception as e:
            logger.error(f"Session index rebuild failed: {e}", exc_info=True)

    # ── slow-path scans (index unavailable) ──────────────────────────────────
    async def scan_summaries(self, user_uuid, include_archived: bool = False,
                             filter_by_user: bool = True) -> list:
        """Build summaries directly from files (mirrors the historical slow path).

        Returns summaries WITHOUT genie-metadata DB enrichment — the caller
        (session_manager.get_all_sessions) applies that, backend-agnostically.
        """
        if filter_by_user:
            safe_user = "".join(c for c in user_uuid if c.isalnum() or c in ['-', '_'])
            user_dir = self.sessions_dir / safe_user
            if not user_dir.is_dir():
                try:
                    user_dir.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    logger.error(f"Failed to create user session directory: {user_dir}. Error: {e}")
                    return []
            scan_dirs = [user_dir]
        else:
            if not self.sessions_dir.is_dir():
                return []
            scan_dirs = [d for d in self.sessions_dir.iterdir() if d.is_dir()]

        summaries = []
        for session_dir in scan_dirs:
            for session_file in session_dir.glob("**/*.json"):
                try:
                    async with aiofiles.open(session_file, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                    # NOTE: archived filtering is applied by the caller
                    # (get_all_sessions) for both fast and slow paths.
                    summaries.append({
                        "id": data.get("id", session_file.stem),
                        "name": data.get("name", "Unnamed Session"),
                        "created_at": data.get("created_at", "Unknown"),
                        "models_used": data.get("models_used", []),
                        "profile_tags_used": data.get("profile_tags_used", []),
                        "last_updated": data.get("last_updated", data.get("created_at", "Unknown")),
                        "archived": data.get("archived", False),
                        "archived_at": data.get("archived_at"),
                        "profile_tag": data.get("profile_tag"),
                        "profile_id": data.get("profile_id"),
                        "is_temporary": data.get("is_temporary", False),
                        "temporary_purpose": data.get("temporary_purpose"),
                        "genie_metadata": data.get("genie_metadata", {}),
                        "user_uuid": data.get("user_uuid"),
                    })
                except (json.JSONDecodeError, OSError, KeyError) as e:
                    logger.error(f"Error loading summary from session file '{session_file}': {e}")
                    summaries.append({
                        "id": session_file.stem,
                        "name": f"Error Loading ({session_file.stem})",
                        "created_at": "Unknown",
                    })
        return summaries

    async def scan_all_bodies(self, include_archived: bool = True) -> list:
        bodies = []
        if not self.sessions_dir.is_dir():
            return bodies
        for user_dir in self.sessions_dir.iterdir():
            if not user_dir.is_dir():
                continue
            owner = user_dir.name
            for sf in user_dir.glob("*.json"):
                body = await self.load(owner, sf.stem)
                if body is None:
                    continue
                if body.get("archived") and not include_archived:
                    continue
                bodies.append(body)
        return bodies
