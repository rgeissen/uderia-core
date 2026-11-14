"""
TeradataDeploymentBackend — deploy a DDL plan and bulk-load demo data into a
live Teradata system, one teradatasql session per login.

Connection mechanics: ONE ``teradatasql`` session per backend instance — per login — on a
dedicated single-thread executor, and an ``_execute_sql`` wrapper with stale-connection
detection + serialized reconnect. Never teradataml's ``create_context``: that context is
process-GLOBAL, so with two logins in one process (a model operator landing, an investigator's
action writing) every statement ran as whichever login had connected last — a ledger CREATE
"through the operator" ran as the writer, and an action's write could run under the
operator's rights (Act II rehearsal, Oct 2026). A PAT-only configuration (no password) is the
one case still served through teradataml's context, and only then.

Deployment targets **ordinary relational tables** (the PDM) — never EVS/vector
objects — so raw ``CREATE``/``DROP`` DDL is correct here (the EVS object-ownership
rule does not apply to platform-owned relational objects).

``execute_ddl(dry_run=True)`` short-circuits before any I/O, so it is safe and
unit-testable without a live system.  Real execution (M3 adds ``bulk_load``)
requires a reachable Teradata and is covered by an integration test.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .backend import DeploymentBackend, DeploymentError
from .capabilities import DeploymentCapability
from .types import DDLPlan, DeployResult, LoadPlan, LoadResult

logger = logging.getLogger("dbdeploy.teradata")


class TeradataDeploymentBackend(DeploymentBackend):
    """Deploy relational DDL + data to Teradata."""

    def __init__(self, connection_config: Optional[Dict[str, Any]] = None):
        cfg = connection_config or {}
        self._config = cfg
        self._host: str = cfg.get("host", "")
        self._username: str = cfg.get("username", "")
        self._password: str = cfg.get("password", "")
        self._pat_token: str = cfg.get("pat_token", "")
        self._pem_file: str = cfg.get("pem_file", "")
        self._database: str = cfg.get("database", "")
        # Deploy-specific knobs (CREATE DATABASE parent + perm space). Configured values win;
        # otherwise ``provisioning()`` decides — the login's OWN space as the parent (a login can
        # be granted CREATE DATABASE on itself; ``dbc`` works only for a superuser), and a size
        # read from what that parent has free, never a fixed figure it may not have.
        self._database_parent_cfg: Optional[str] = (cfg.get("database_parent") or "").strip() or None
        self._database_perm_cfg: Optional[int] = (int(cfg["database_perm_bytes"])
                                                  if cfg.get("database_perm_bytes") not in (None, "") else None)
        self._database_parent: str = self._database_parent_cfg or self._database or self._username or "dbc"
        self._database_perm_bytes: int = self._database_perm_cfg or 10_000_000_000

        self._initialized = False
        self._conn = None        # this backend's OWN teradatasql session (its login, never another's)
        self._tdml = False       # True only for a PAT-only configuration (teradataml's global context)
        self._reconnect_lock = asyncio.Lock()
        self._last_reconnect_ts = 0.0
        self._td_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="td-deploy")

    # ── identity & capabilities ───────────────────────────────────────────────

    @property
    def backend_type(self) -> str:
        return "teradata"

    def capabilities(self) -> Set[DeploymentCapability]:
        return {
            DeploymentCapability.EXECUTE_DDL,
            DeploymentCapability.DRY_RUN,
            DeploymentCapability.CREATE_DATABASE,
            DeploymentCapability.DROP_OBJECTS,
            DeploymentCapability.SOFT_RI,
            DeploymentCapability.COLLECT_STATISTICS,
            DeploymentCapability.VIEWS,
            DeploymentCapability.BULK_LOAD,
        }

    # ── thread plumbing (teradataml SingletonThreadPool) ──────────────────────

    async def _run_in_td_thread(self, fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        if kwargs:
            fn = functools.partial(fn, **kwargs)
        return await loop.run_in_executor(self._td_executor, fn, *args)

    def _connect_sync(self):
        """A teradatasql session for THIS backend's login (runs on the backend's thread)."""
        import teradatasql  # type: ignore[import]
        kw: Dict[str, Any] = {"host": self._host, "user": self._username, "password": self._password}
        port = self._config.get("port")
        if port not in (None, ""):
            kw["dbs_port"] = str(port)
        if self._database:
            kw["database"] = self._database
        return teradatasql.connect(**kw)

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self._username and self._password:
            try:
                self._conn = await self._run_in_td_thread(self._connect_sync)
            except ImportError as exc:
                raise DeploymentError("The 'teradatasql' package is required for the Teradata deployment backend.") from exc
            self._tdml = False
            logger.info("[TD-deploy] session opened (host=%s, database=%s, username=%s)",
                        self._host, self._database, self._username)
            self._initialized = True
            return
        if not self._pat_token:
            raise DeploymentError(
                "Teradata deployment requires (username + password) or a pat_token in config."
            )
        try:
            from teradataml import create_context  # type: ignore[import]
        except ImportError as exc:
            raise DeploymentError(
                "The 'teradataml' package is required for a PAT-authenticated Teradata deployment."
            ) from exc
        ctx_kwargs: dict = {"host": self._host, "username": self._username}
        if self._database:
            ctx_kwargs["database"] = self._database
        logger.info("[TD-deploy] create_context(host=%s, database=%s, username=%s) — PAT, process-global context",
                    self._host, self._database, self._username)
        await self._run_in_td_thread(create_context, **ctx_kwargs)
        self._tdml = True
        self._initialized = True

    def role_session(self, role: str):
        """A SEPARATE session of this backend's login acting under ``role`` — validation's
        live probes run exactly as perform writes (the login must HOLD the role, then
        ``SET ROLE``; ``core/action_perform._teradata_session``). Before, the probes ran on the
        backend's plain session: under no role a write login may neither read nor plan a write
        on the table, so A3/A5 failed — and before the per-login sessions they "held" only
        because another login's rights answered. Sync (called from a worker thread); the caller
        closes it. ``None`` for a PAT-only backend (no session of its own to open)."""
        if self._tdml:
            return None
        conn = self._connect_sync()
        cur = conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM DBC.RoleMembersV WHERE Grantee = USER AND RoleName = ?", [role])
            held = int(((cur.fetchall() or [[0]])[0] or [0])[0] or 0)
            if not held:
                raise DeploymentError(f"the connection's login {self._username} does not hold role {role} — "
                                      f"grant exactly: GRANT {role} TO {self._username};")
            cur.execute(f'SET ROLE "{role}"')
        except Exception:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
        backend = self

        class _RoleSession:
            def run(self, sql, params=None):
                c = conn.cursor()
                try:
                    c.execute(sql, list(params)) if params is not None else c.execute(sql)
                    return [tuple(r) for r in c.fetchall()] if c.description else []
                finally:
                    c.close()

            def close(self):
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

            login = backend._username
        return _RoleSession()

    class _Rows:
        """A statement's rows, read eagerly (the cursor is closed before it is returned)."""
        def __init__(self, rows):
            self._r = rows

        def fetchall(self):
            return self._r

    def _exec_sync(self, sql: str, params: Optional[Sequence[Any]] = None):
        """One statement on THIS backend's session (runs on the backend's thread). Binds are
        ONE row: a sequence for teradatasql, a tuple for teradataml (TDML_2008)."""
        if self._tdml:
            from teradataml import execute_sql  # type: ignore[import]
            return execute_sql(sql, tuple(params)) if params is not None else execute_sql(sql)
        cur = self._conn.cursor()
        try:
            if params is not None:
                cur.execute(sql, list(params))
            else:
                cur.execute(sql)
            rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
        return self._Rows(rows)

    @staticmethod
    def _is_connection_lost(exc: BaseException) -> bool:
        msg = str(exc).lower()
        if isinstance(exc, AttributeError):
            return "'nonetype'" in msg and "cursor" in msg
        if "operational" in type(exc).__name__.lower():
            return (
                "not a valid connection pool handle" in msg
                or "socket" in msg
                or "connection refused" in msg
                or "connection reset" in msg
                or "broken pipe" in msg
            )
        return "not a valid connection pool handle" in msg

    async def _reconnect_all(self) -> None:
        async with self._reconnect_lock:
            old, self._conn = self._conn, None
            if old is not None:
                try:
                    await self._run_in_td_thread(old.close)
                except Exception:  # noqa: BLE001
                    pass
            self._initialized = False
            await self.initialize()

    async def _execute_sql(self, sql: str) -> Any:
        logger.info("[TD-deploy-SQL] %s", sql)
        await self.initialize()
        try:
            return await self._run_in_td_thread(self._exec_sync, sql)
        except Exception as exc:
            if not self._is_connection_lost(exc):
                raise
            await self._reconnect_all()
            return await self._run_in_td_thread(self._exec_sync, sql)

    # ── deployment ────────────────────────────────────────────────────────────

    async def execute_ddl(
        self,
        plan: DDLPlan,
        *,
        dry_run: bool = False,
        stop_on_error: bool = True,
        include_statistics: bool = True,
        replace_existing: bool = False,
    ) -> DeployResult:
        script = plan.sql_script()
        result = DeployResult(dry_run=dry_run, generated_sql=script)

        # Dry-run: no I/O, no import, no connection. Safe preview path.
        if dry_run:
            result.planned = [f"{s.kind}:{s.identifier}" for s in plan.ordered()]
            result.success = True
            return result

        await self.initialize()
        # Idempotent re-deploy: drop the plan's OWN views + tables first so a
        # CREATE over existing objects (Error 3803) can't happen.  Scoped strictly
        # to the plan's identifiers; FK-safe (views first, then multi-pass tables).
        if replace_existing:
            result.replaced = await self._drop_plan_objects(plan)

        # Track created namespaces/tables so we can clean up on failure
        # (Teradata DDL is implicitly committed — no transaction rollback).
        created: List[str] = []  # "kind:db.obj"
        for stmt in plan.ordered():
            # Statistics are deferred to a post-load phase when requested —
            # collecting on empty tables gives the optimizer nothing.
            if not include_statistics and stmt.kind == "statistics":
                result.skipped.append((stmt.kind, stmt.identifier))
                continue
            try:
                await self._execute_sql(stmt.sql)
                result.executed.append((stmt.kind, stmt.identifier))
                if stmt.kind in ("database", "table"):
                    created.append(f"{stmt.kind}:{stmt.identifier}")
            except Exception as exc:  # noqa: BLE001
                # Soft-RI foreign keys are optimizer hints (WITH NO CHECK OPTION),
                # not enforced integrity.  A single hub table can exceed Teradata's
                # per-table referential-constraint cap (Error 3974) — that must NOT
                # abort a whole deploy or roll back already-created tables.  Skip
                # the offending FK (like dangling/filtered FKs) and keep going.
                if stmt.kind == "foreign_key":
                    logger.warning("[TD-deploy] skipping foreign key %s: %s",
                                   stmt.identifier, exc)
                    result.skipped.append((stmt.kind, stmt.identifier))
                    continue
                # A metric VIEW is derived — compiled from a Spark expression in the
                # source model — and optional: one expression Teradata refuses must
                # not abort the deploy and roll back every table.  Skip it, name it.
                if stmt.kind == "view":
                    logger.warning("[TD-deploy] skipping metric view %s: %s",
                                   stmt.identifier, exc)
                    result.skipped.append((stmt.kind, stmt.identifier))
                    result.skipped_reasons = getattr(result, "skipped_reasons", {})
                    result.skipped_reasons[stmt.identifier] = str(exc).splitlines()[0][:300]
                    continue
                result.errors.append((stmt.identifier, str(exc)))
                if stop_on_error:
                    cleaned = await self._cleanup(created)
                    result.cleaned_up = cleaned
                    result.success = False
                    return result
        result.success = not result.errors
        return result

    _CREATE_VIEW_RE = re.compile(r"^\s*CREATE\s+VIEW\b", re.IGNORECASE)

    async def replace_views(self, plan: DDLPlan) -> DeployResult:
        """Views-only repair via Teradata's atomic ``REPLACE VIEW``.

        ``REPLACE VIEW`` creates the view when absent and swaps the definition in
        one statement when present — so a definition Teradata refuses leaves the
        EXISTING view standing (drop-then-create would leave a hole).  Only
        ``view`` statements run; a refusal is skipped and named, exactly as
        :meth:`execute_ddl` treats a metric view during a deploy.
        """
        result = DeployResult(generated_sql="\n".join(s.sql for s in plan.by_kind("view")))
        result.skipped_reasons = {}
        await self.initialize()
        for stmt in plan.by_kind("view"):
            sql = self._CREATE_VIEW_RE.sub("REPLACE VIEW", stmt.sql, count=1)
            try:
                await self._execute_sql(sql)
                result.executed.append(("view", stmt.identifier))
            except Exception as exc:  # noqa: BLE001 — one refused expression never stops the rest
                logger.warning("[TD-deploy] REPLACE VIEW %s refused: %s", stmt.identifier, exc)
                result.skipped.append(("view", stmt.identifier))
                result.skipped_reasons[stmt.identifier] = str(exc).splitlines()[0][:300]
        result.success = True
        return result

    @staticmethod
    def _is_missing_object(exc: BaseException) -> bool:
        """True if the error means the object simply doesn't exist (safe to ignore
        when dropping for idempotency)."""
        msg = str(exc).lower()
        return ("does not exist" in msg
                or "[error 3807]" in msg     # Object 'x' does not exist
                or "[error 3802]" in msg)    # Database 'x' does not exist

    async def _drop_plan_objects(self, plan: DDLPlan) -> List[Tuple[str, str]]:
        """Drop the plan's OWN views + tables so a re-deploy starts clean.

        Strictly scoped to the plan's identifiers (never arbitrary objects).
        Views first (they depend on tables); then tables via a multi-pass drop —
        a soft-RI parent can't drop while a child references it (Error 5313), so
        each pass drops the now-unreferenced tables until none remain.  Missing
        objects (first deploy) are ignored.
        """
        dropped: List[Tuple[str, str]] = []

        # 1. views (depend on tables)
        for stmt in plan.by_kind("view"):
            try:
                await self._execute_sql(f'DROP VIEW {self._qualified(stmt.identifier)};')
                dropped.append(("view", stmt.identifier))
            except Exception as exc:  # noqa: BLE001
                if not self._is_missing_object(exc):
                    logger.warning("[TD-deploy] pre-drop view %s failed: %s", stmt.identifier, exc)

        # 2. tables — multi-pass (children free their parents each pass)
        remaining = [s.identifier for s in plan.by_kind("table")]
        max_passes = len(remaining) + 1
        for _ in range(max_passes):
            if not remaining:
                break
            still: List[str] = []
            progressed = False
            for ident in remaining:
                try:
                    await self._execute_sql(f'DROP TABLE {self._qualified(ident)};')
                    dropped.append(("table", ident))
                    progressed = True
                except Exception as exc:  # noqa: BLE001
                    if self._is_missing_object(exc):
                        progressed = True   # already gone — count as progress
                    else:
                        still.append(ident)  # referenced parent — retry next pass
            remaining = still
            if not progressed:
                # No progress (FK cycle): stop. CREATE will surface the real error.
                logger.warning("[TD-deploy] pre-drop stalled on %d table(s): %s",
                                len(remaining), remaining[:5])
                break
        return dropped

    async def _cleanup(self, created: List[str]) -> List[str]:
        """Best-effort drop of objects created before a failure (newest first)."""
        cleaned: List[str] = []
        for entry in reversed(created):
            kind, _, ident = entry.partition(":")
            try:
                if kind == "table":
                    await self._execute_sql(f'DROP TABLE {self._qualified(ident)};')
                elif kind == "database":
                    await self._execute_sql(f'DELETE DATABASE {self._q(ident)} ALL;')
                    await self._execute_sql(f'DROP DATABASE {self._q(ident)};')
                cleaned.append(entry)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[TD-deploy] cleanup failed for %s: %s", entry, exc)
        return cleaned

    # ── database provisioning (abstraction capability) ────────────────────────

    @staticmethod
    def _rows(result) -> list:
        if result is None:
            return []
        if hasattr(result, "to_pandas"):
            try:
                pdf = result.to_pandas()
                return [] if pdf.empty else [tuple(r) for r in pdf.itertuples(index=False)]
            except Exception:  # noqa: BLE001
                return []
        if hasattr(result, "fetchall"):
            try:
                return list(result.fetchall())
            except Exception:  # noqa: BLE001
                return []
        return []

    def _create_database_sql(self, name: str, parent: str, perm: int) -> str:
        return f"CREATE DATABASE {self._q(name)} FROM {self._q(parent)} AS PERM = {int(perm)};"

    def preview_create_database(self, name, *, parent=None, perm_bytes=None) -> str:
        p = (parent or self._database_parent or "dbc")
        perm = int(perm_bytes if perm_bytes is not None else self._database_perm_bytes)
        return self._create_database_sql(name, p, perm)

    async def database_exists(self, name: str) -> bool:
        await self.initialize()
        safe = str(name).replace("'", "''")
        try:
            res = await self._execute_sql(
                f"SELECT 1 AS X FROM DBC.DatabasesV WHERE DatabaseName = '{safe}';"
            )
        except Exception as exc:  # noqa: BLE001
            raise DeploymentError(
                f"Could not check whether database '{name}' exists: {exc}"
            ) from exc
        return len(self._rows(res)) > 0

    #: A new database takes at most this, and at most half of what its parent has free.
    SIBLING_MAX_PERM = 10_000_000_000

    async def provisioning(self, name: str, *, parent: Optional[str] = None,
                           perm_bytes: Optional[int] = None) -> Dict[str, Any]:
        """Where and how large a database ``name`` would be created — decided, never guessed:
        the parent is the caller's, else the configured one, else the connection's default
        database, else the LOGIN itself; the size is the caller's, else the configured one,
        else half of what the parent has free (capped). ``{"sql", "parent", "perm_bytes",
        "basis"}`` — the basis in words for the preview."""
        await self.initialize()
        p = parent or self._database_parent_cfg or self._database or self._username or "dbc"
        p_basis = ("given" if parent else "configured" if self._database_parent_cfg
                   else "the connection's default database" if self._database
                   else "the landing login's own space")
        if perm_bytes is not None:
            perm, s_basis = int(perm_bytes), "given"
        elif self._database_perm_cfg is not None:
            perm, s_basis = int(self._database_perm_cfg), "configured"
        else:
            free = None
            try:
                rows = self._rows(await self._execute_sql(
                    "SELECT SUM(MaxPerm) - SUM(CurrentPerm) FROM DBC.DiskSpaceV "
                    f"WHERE DatabaseName = '{str(p).replace(chr(39), chr(39) * 2)}';"))
                free = int(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else None
            except Exception as exc:  # noqa: BLE001 — an unread space is the fixed default
                logger.info("[TD-deploy] free space of %s unread: %s", p, exc)
            if free is not None and free > 0:
                perm = min(self.SIBLING_MAX_PERM, free // 2)
                s_basis = f"half of the {free // 1_000_000:,} MB {p} has free"
            else:
                perm, s_basis = self.SIBLING_MAX_PERM, "the default"
        return {"sql": self._create_database_sql(name, p, perm), "parent": p, "perm_bytes": perm,
                "basis": f"under {p} ({p_basis}), {perm // 1_000_000:,} MB ({s_basis})"}

    async def ensure_database(self, name, *, parent=None, perm_bytes=None) -> bool:
        """Create database ``name`` iff it does not already exist (idempotent).

        On failure the **exact Teradata error** is wrapped in a ``DeploymentError``
        so it propagates transparently to the caller (and the frontend messaging
        system).  Returns True if created, False if it already existed.
        """
        await self.initialize()
        if await self.database_exists(name):
            return False
        prov = await self.provisioning(name, parent=parent, perm_bytes=perm_bytes)
        p, perm = prov["parent"], int(prov["perm_bytes"])
        try:
            await self._execute_sql(self._create_database_sql(name, p, perm))
        except Exception as exc:  # noqa: BLE001
            raise DeploymentError(
                f"Failed to create database '{name}' (parent '{p}', PERM {perm} bytes): {exc}"
            ) from exc
        return True

    async def read_access(self, grantees: List[str], roles: List[str]):
        """What the dictionary says ``grantees`` (logins or roles) hold, and who holds
        ``roles`` — the input of ``dbdeploy.access.judge``. Read on this backend's own
        session; an unreadable dictionary is said (``readable=False``), never a pass."""
        from trusted_data_agent.dbdeploy.access import Held, held_from_rows
        await self.initialize()
        names = sorted({str(g).strip() for g in grantees if g})
        rls = sorted({str(r).strip() for r in roles if r})
        q = lambda xs: ", ".join("'" + x.replace("'", "''") + "'" for x in xs)  # noqa: E731
        try:
            rights = []
            if names:
                rights += self._rows(await self._execute_sql(
                    "SELECT TRIM(UserName), TRIM(DatabaseName), TRIM(TableName), TRIM(AccessRight) "
                    f"FROM DBC.AllRightsV WHERE UserName IN ({q(names)});"))
                rights += self._rows(await self._execute_sql(
                    "SELECT TRIM(RoleName), TRIM(DatabaseName), TRIM(TableName), TRIM(AccessRight) "
                    f"FROM DBC.AllRoleRightsV WHERE RoleName IN ({q(names)});"))
            members = self._rows(await self._execute_sql(
                f"SELECT TRIM(RoleName), TRIM(Grantee) FROM DBC.RoleMembersV WHERE RoleName IN ({q(rls)});")) if rls else []
            return held_from_rows(rights, members)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TD-deploy] access rights could not be read: %s", exc)
            return Held(readable=False)

    async def database_owned(self, name: str) -> Optional[bool]:
        """Owner in ``DBC.DatabasesV`` equals the login, or the login holds CREATE TABLE
        (``CT``) on the database in ``DBC.AllRightsV``.  An absent database is not owned."""
        await self.initialize()
        safe = str(name).replace("'", "''")
        try:
            who = self._rows(await self._execute_sql("SELECT USER;"))
            login = str(who[0][0]).strip().lower() if who else ""
            own = self._rows(await self._execute_sql(
                f"SELECT OwnerName FROM DBC.DatabasesV WHERE DatabaseName = '{safe}';"))
            if not own:
                return False
            if login and str(own[0][0]).strip().lower() == login:
                return True
            ct = self._rows(await self._execute_sql(
                f"SELECT COUNT(*) FROM DBC.AllRightsV WHERE DatabaseName = '{safe}' "
                f"AND AccessRight = 'CT' AND UserName = USER;"))
            return bool(ct and int(ct[0][0] or 0) > 0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TD-deploy] ownership of %s could not be read: %s", name, exc)
            return None

    async def describe_tables(self, database: str, tables: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        await self.initialize()
        safe = str(database).replace("'", "''")
        want = {str(t).lower() for t in (tables or [])}
        out: Dict[str, Dict[str, Any]] = {}
        cols = self._rows(await self._execute_sql(
            f"SELECT TableName, ColumnName, ColumnType, ColumnLength FROM DBC.ColumnsV "
            f"WHERE DatabaseName = '{safe}' ORDER BY TableName, ColumnId;"))
        for row in cols:
            tname = str(row[0]).strip()
            if want and tname.lower() not in want:
                continue
            ident = f"{database}.{tname}"
            entry = out.setdefault(ident, {"columns": {}, "foreign_keys": []})
            entry["columns"][str(row[1]).strip()] = str(row[2] or "").strip()
        if not out:
            return out
        # what each object IS: a reference can be declared on a table, never on a view
        try:
            for row in self._rows(await self._execute_sql(
                    f"SELECT TableName, TableKind FROM DBC.TablesV WHERE DatabaseName = '{safe}';")):
                ident = f"{database}.{str(row[0]).strip()}"
                if ident in out:
                    out[ident]["kind"] = "view" if str(row[1] or "").strip().upper() == "V" else "table"
        except Exception as exc:  # noqa: BLE001 — an unread kind is an unknown kind
            logger.info("[TD-deploy] object kinds for %s skipped: %s", database, exc)
        try:
            fks = self._rows(await self._execute_sql(
                f"SELECT ChildTable, ChildKeyColumn, ParentDB, ParentTable, ParentKeyColumn "
                f"FROM DBC.All_RI_ChildrenV WHERE ChildDB = '{safe}';"))
        except Exception as exc:  # noqa: BLE001 — RI listing is a refinement; the columns decide
            logger.info("[TD-deploy] RI listing for %s skipped: %s", database, exc)
            fks = []
        for row in fks:
            ident = f"{database}.{str(row[0]).strip()}"
            if ident in out:
                out[ident]["foreign_keys"].append(
                    (str(row[1]).strip(), f"{str(row[2]).strip()}.{str(row[3]).strip()}", str(row[4]).strip()))
        return out

    # ── the action gate (PROP-008 B1) ─────────────────────────────────────────
    def run_sync_query(self, sql: str, params: Optional[List[Any]] = None) -> list:
        """One READ statement with bind parameters, from a worker thread (the action gate
        is a pure, synchronous function run off the event loop). The statement runs on the
        backend's own pinned teradataml thread — never on the caller's — and this thread
        waits for it. Must not be called FROM the td thread (it would wait on itself)."""
        if not self._initialized:
            raise DeploymentError("the deployment backend is not initialized")
        logger.info("[TD-deploy-SQL] %s", sql)
        # ONE row of binds (teradataml refuses a flat list with TDML_2008 — found by the first
        # live run; `_exec_sync` hands each driver the shape it takes)
        binds = tuple(params) if params is not None else None
        fut = self._td_executor.submit(self._exec_sync, sql, binds)
        return self._rows(fut.result(timeout=120))

    async def unique_indexes(self, identifier: str) -> List[Tuple[str, ...]]:
        """The column sets the dictionary declares UNIQUE on a table (a unique primary or
        secondary index, or a primary key) — ``[(col, …), …]`` in column order."""
        await self.initialize()
        db, table = identifier.split(".", 1)
        rows = self._rows(await self._run_in_td_thread(
            self._exec_sync,
            "SELECT IndexNumber, ColumnName, ColumnPosition FROM DBC.IndicesV "
            "WHERE DatabaseName = ? AND TableName = ? AND UniqueFlag = 'Y' "
            "ORDER BY IndexNumber, ColumnPosition", (db, table)))   # ONE row of binds, as a tuple
        by: Dict[Any, List[str]] = {}
        for idx, col, _pos in rows:
            by.setdefault(idx, []).append(str(col).strip())
        return [tuple(v) for v in by.values()]

    async def execute_statement(self, sql: str) -> None:
        """One DDL/DML statement (the action ledger's CREATE TABLE)."""
        await self.initialize()
        await self._execute_sql(sql)

    # ── the readback (PROP-008 A4) ────────────────────────────────────────────
    async def amp_count(self) -> Optional[int]:
        await self.initialize()
        try:
            rows = self._rows(await self._execute_sql("SELECT HASHAMP() + 1;"))
            return int(rows[0][0]) if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.info("[TD-deploy] AMP count not read: %s", exc)
            return None

    async def system_version(self) -> Optional[str]:
        """``Teradata 20.00.24.60`` — the version a rule is verified against."""
        await self.initialize()
        try:
            rows = self._rows(await self._execute_sql("SELECT InfoData FROM DBC.DBCInfoV WHERE InfoKey = 'VERSION';"))
            return f"Teradata {str(rows[0][0]).strip()}" if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.info("[TD-deploy] version not read: %s", exc)
            return None

    async def read_design(self, identifiers: List[str]) -> Dict[str, Dict[str, Any]]:
        """The dictionary's design facts per table: SET/MULTISET + FALLBACK from ``SHOW TABLE``
        (DBC.TablesV has no SET flag), the primary index from DBC.IndicesV, nullability +
        charset from DBC.ColumnsV, soft RI from DBC.All_RI_ChildrenV, statistics from
        DBC.StatsV, the comment from DBC.TablesV.  Each fact read apart, so one failing read
        leaves the others — a missing fact is ``None`` (unverified), never a guess."""
        await self.initialize()
        out: Dict[str, Dict[str, Any]] = {}
        for ident in identifiers:
            db, _, tb = str(ident).rpartition(".")
            if not db or not tb:
                continue
            sdb, stb = db.replace("'", "''"), tb.replace("'", "''")
            obs: Dict[str, Any] = {"multiset": None, "fallback": None, "pi_columns": None, "pi_unique": None,
                                   "nopi": None, "columns": {}, "foreign_keys": None, "statistics": None, "comment": None}
            try:
                rows = self._rows(await self._execute_sql(f"SHOW TABLE {self._q(db)}.{self._q(tb)};"))
                text = " ".join(str(r[0]) for r in rows if r and r[0] is not None).replace("\r", "\n")
                up = text.upper()
                if "MULTISET TABLE" in up:
                    obs["multiset"] = True
                elif "SET TABLE" in up:
                    obs["multiset"] = False
                if "NO FALLBACK" in up:
                    obs["fallback"] = False
                elif "FALLBACK" in up:
                    obs["fallback"] = True
                if "NO PRIMARY INDEX" in up:
                    obs["nopi"] = True; obs["pi_columns"] = []
            except Exception as exc:  # noqa: BLE001
                logger.info("[TD-deploy] SHOW TABLE %s not read: %s", ident, exc)
            try:
                rows = self._rows(await self._execute_sql(
                    f"SELECT UniqueFlag, ColumnName FROM DBC.IndicesV WHERE DatabaseName = '{sdb}' AND TableName = '{stb}' "
                    f"AND IndexType IN ('P', 'Q') ORDER BY ColumnPosition;"))
                if rows:
                    obs["pi_columns"] = [str(r[1]).strip() for r in rows]
                    obs["pi_unique"] = str(rows[0][0]).strip().upper() == "Y"
                    obs["nopi"] = False
                elif obs["nopi"] is None:
                    obs["nopi"] = True; obs["pi_columns"] = []
            except Exception as exc:  # noqa: BLE001
                logger.info("[TD-deploy] indices of %s not read: %s", ident, exc)
            try:
                rows = self._rows(await self._execute_sql(
                    f"SELECT ColumnName, ColumnType, Nullable, CharType FROM DBC.ColumnsV WHERE DatabaseName = '{sdb}' "
                    f"AND TableName = '{stb}' ORDER BY ColumnId;"))
                for r in rows:
                    ct = str(r[3]).strip() if r[3] is not None else ""
                    obs["columns"][str(r[0]).strip()] = {
                        "type": str(r[1] or "").strip(),
                        "nullable": None if r[2] is None else str(r[2]).strip().upper() == "Y",
                        "charset": {"1": "LATIN", "2": "UNICODE", "3": "KANJISJIS", "4": "GRAPHIC", "5": "KANJI1"}.get(ct) if ct and ct != "0" else None}
            except Exception as exc:  # noqa: BLE001
                logger.info("[TD-deploy] columns of %s not read: %s", ident, exc)
            try:
                rows = self._rows(await self._execute_sql(
                    f"SELECT ChildKeyColumn, ParentDB, ParentTable, ParentKeyColumn FROM DBC.All_RI_ChildrenV "
                    f"WHERE ChildDB = '{sdb}' AND ChildTable = '{stb}';"))
                obs["foreign_keys"] = [(str(r[0]).strip(), f"{str(r[1]).strip()}.{str(r[2]).strip()}", str(r[3]).strip()) for r in rows]
            except Exception as exc:  # noqa: BLE001
                logger.info("[TD-deploy] RI of %s not read: %s", ident, exc)
            try:
                rows = self._rows(await self._execute_sql(
                    f"SELECT ColumnName FROM DBC.StatsV WHERE DatabaseName = '{sdb}' AND TableName = '{stb}' AND ColumnName IS NOT NULL;"))
                obs["statistics"] = [[c.strip() for c in str(r[0]).split(",") if c.strip()] for r in rows]
            except Exception as exc:  # noqa: BLE001
                logger.info("[TD-deploy] statistics of %s not read: %s", ident, exc)
            try:
                rows = self._rows(await self._execute_sql(
                    f"SELECT CommentString FROM DBC.TablesV WHERE DatabaseName = '{sdb}' AND TableName = '{stb}';"))
                if rows:
                    obs["comment"] = "" if rows[0][0] is None else str(rows[0][0]).strip()
            except Exception as exc:  # noqa: BLE001
                logger.info("[TD-deploy] comment of %s not read: %s", ident, exc)
            # uniqueness in the DATA (the key probe), only for a unique PI with rows
            if obs.get("pi_columns") and obs.get("pi_unique"):
                try:
                    cols = ", ".join(self._q(c) for c in obs["pi_columns"])
                    q = self._q(db) + "." + self._q(tb)
                    n = self._rows(await self._execute_sql(f"SELECT COUNT(*) FROM {q};"))
                    g = self._rows(await self._execute_sql(f"SELECT COUNT(*) FROM (SELECT {cols} FROM {q} GROUP BY {cols}) x;"))
                    if n and g and int(n[0][0] or 0) > 0:
                        obs["pi_unique_in_data"] = int(n[0][0]) == int(g[0][0])
                except Exception as exc:  # noqa: BLE001
                    logger.info("[TD-deploy] key uniqueness of %s not measured: %s", ident, exc)
            out[ident] = obs
        return out

    async def measure_pi_skew(self, identifier: str, pi_columns: List[str]) -> Optional[Dict[str, Any]]:
        """The verified ``pi_skew_detection`` statement: rows per AMP under HASHAMP(HASHBUCKET(
        HASHROW(cols))) and how far the fullest AMP lies above the average (every AMP counted,
        an AMP with no rows as empty)."""
        if not pi_columns:
            return None
        await self.initialize()
        db, _, tb = str(identifier).rpartition(".")
        cols = ", ".join(self._q(c) for c in pi_columns)
        q = self._q(db) + "." + self._q(tb)
        sql = (f"WITH amp_rows AS (SELECT HASHAMP(HASHBUCKET(HASHROW({cols}))) AS amp_no, COUNT(*) AS row_count FROM {q} GROUP BY 1), "
               f"metrics AS (SELECT HASHAMP() + 1 AS total_amps, COUNT(*) AS amps_with_rows, SUM(row_count) AS total_rows, "
               f"MAX(row_count) AS max_rows, CAST(SUM(row_count) AS FLOAT) / (HASHAMP() + 1) AS avg_rows FROM amp_rows) "
               f"SELECT total_amps, amps_with_rows, total_rows, max_rows, avg_rows, "
               f"CAST((max_rows - avg_rows) * 100 / NULLIFZERO(avg_rows) AS DECIMAL(9,2)) AS skew_pct FROM metrics;")
        try:
            rows = self._rows(await self._execute_sql(sql))
        except Exception as exc:  # noqa: BLE001
            logger.info("[TD-deploy] skew of %s not measured: %s", identifier, exc)
            return None
        if not rows or rows[0][2] in (None, 0):
            return {"amps": int(rows[0][0]) if rows and rows[0][0] is not None else None, "total_rows": 0,
                    "max_rows": 0, "avg_rows": 0.0, "skew_pct": None}
        r = rows[0]
        return {"amps": int(r[0]), "amps_with_rows": int(r[1]), "total_rows": int(r[2]), "max_rows": int(r[3]),
                "avg_rows": float(r[4]), "skew_pct": None if r[5] is None else float(r[5])}

    async def list_database_objects(self, name: str) -> List[Tuple[str, str]]:
        await self.initialize()
        safe = str(name).replace("'", "''")
        kinds = {"T": "table", "O": "table", "V": "view", "M": "macro", "P": "procedure"}
        res = await self._execute_sql(
            f"SELECT TableKind, TableName FROM DBC.TablesV WHERE DatabaseName = '{safe}';")
        out: List[Tuple[str, str]] = []
        for row in self._rows(res):
            kind = kinds.get(str(row[0]).strip().upper(), str(row[0]).strip().lower())
            out.append((kind, str(row[1]).strip()))
        return out

    async def drop_database(self, name: str, *, owned=None) -> DeployResult:
        result = DeployResult()
        await self.initialize()
        if not await self.database_exists(name):
            result.skipped.append(("database", name))
            result.success = True
            return result
        if owned is not None:
            own = {str(o).strip().lower() for o in owned}
            strangers = [f"{k} {n}" for k, n in await self.list_database_objects(name)
                         if n.lower() not in own and not n.lower().endswith(self.STAGE_SUFFIX)]
            if strangers:
                result.errors.append((name, "database holds object(s) the deploy plan does not own — "
                                            "not dropped: " + ", ".join(sorted(strangers)[:10])
                                            + (" …" if len(strangers) > 10 else "")))
                return result
        try:
            await self._execute_sql(f'DELETE DATABASE {self._q(name)} ALL;')
            await self._execute_sql(f'DROP DATABASE {self._q(name)};')
            result.executed.append(("database", name))
            result.success = True
        except Exception as exc:  # noqa: BLE001
            result.errors.append((name, str(exc)))
        return result

    async def bulk_load(self, load_plan: LoadPlan) -> LoadResult:
        """Bulk-load each spec's ``records`` into its (already-created) table.

        Uses the Teradata driver's native **array binding** (``cursor.executemany``
        with a single parameterised ``INSERT``) — one request per table, packing
        all rows.  This avoids ``copy_to_sql``'s per-table pandas + metadata
        reflection overhead (the dominant cost when loading many small tables) and
        surfaces the *real* per-row error (e.g. numeric overflow) via Teradata's
        ``teradata_get_errors`` escape function, instead of a generic batch error.

        Tables are created by the DDL plan first, so the array INSERT never
        auto-creates a table (which would lose our PRIMARY INDEX / types).
        """
        result = LoadResult()
        await self.initialize()

        # Assemble per-table payloads (pure — no I/O) so the I/O boundary stays
        # a single, interceptable thread call.
        # Per table: the column order + the SPEC itself; the parameter rows are built
        # inside the thread one table at a time and the spec's records are released as
        # soon as that table is loaded.  Building every table's parameter rows up front
        # held the whole model twice (dicts + lists, ~0.75 GB for a 200k-row model) for
        # the entire load — and a long-lived server that redeploys model after model
        # was OOM-killed on the eleventh.  The rows are the caller's to keep: the
        # load owns them from here (a LoadPlan is consumed by its load).
        payloads: List[Tuple[str, str, str, List[str], Any, bool]] = []
        for spec in load_plan.tables:
            rows = spec.records or []
            col_order = [c for c, _ in spec.columns] or (list(rows[0].keys()) if rows else [])
            payloads.append((f"{spec.target_database}.{spec.target_table}",
                             spec.target_database, spec.target_table, col_order, spec,
                             self._stage_load(spec.referenced_by, len(rows))))

        rows_by_table, errors = await self._run_in_td_thread(self._executemany_payloads, payloads)
        result.rows_by_table = rows_by_table
        result.errors = errors
        result.success = not errors
        return result

    def _open_load_connection(self):
        """A teradatasql DBAPI connection for the array-bound load.

        A session of THIS backend's own login — never teradataml's process-global context,
        which belongs to whichever login connected last. PAT-only configurations keep the
        context's raw connection (the one case served through teradataml).
        """
        if self._tdml:
            from teradataml import get_context  # type: ignore[import]
            return get_context().raw_connection()
        return self._connect_sync()

    @staticmethod
    def _drop_if_exists(cur, qualified: str) -> None:
        """DROP a table, ignoring 'does not exist' (Error 3807) — never anything else."""
        try:
            cur.execute(f"DROP TABLE {qualified}")
        except Exception as exc:  # noqa: BLE001
            if "3807" not in str(exc):
                raise

    @staticmethod
    def _extract_td_error(cur, exc: Exception) -> str:
        """Surface the real row-level error behind a generic batch failure."""
        try:
            cur.execute("{fn teradata_nativesql}{fn teradata_get_errors}")
            msgs = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]
            joined = "; ".join(m for m in msgs if m)
            if joined:
                return joined
        except Exception:  # noqa: BLE001
            pass
        return str(exc).splitlines()[0] if str(exc) else repr(exc)

    LOAD_BATCH_ROWS = 2000
    # Stage an RI parent through a constraint-free copy once the direct path
    # would cost more than the two DDL round-trips staging adds (~2 s).  The
    # direct array INSERT into a referenced table runs at ~50 rows/s; a
    # reference table of 40 rows is faster loaded directly.
    STAGE_MIN_ROWS = 100
    STAGE_SUFFIX = "_ldstg"

    @classmethod
    def _stage_load(cls, referenced_by: int, n_rows: int) -> bool:
        return referenced_by > 0 and n_rows >= cls.STAGE_MIN_ROWS

    def _executemany_payloads(self, payloads):
        """Blocking array-bound INSERT, one request per table. Runs in the td thread.

        Why two paths.  Teradata executes an array-bound INSERT into a table that
        other tables REFERENCE (an RI parent — every hub/master table in an industry
        model) row-at-a-time, even with ``WITH NO CHECK OPTION``: measured 23 s for
        660 rows into ``party`` (64 incoming FKs) against 0.3 s into a
        constraint-free copy of the same table, and 1.6 s for the same rows via a
        staging copy + ``INSERT … SELECT`` (a set operation the RI path exempts).
        So an RI parent with enough rows is loaded through a per-table staging
        table created ``AS <table> WITH NO DATA`` (same columns, types and primary
        index; RI constraints and statistics are not copied) and dropped after.
        Tables nothing references — the leaf/event tables — take the direct path.
        """
        rows_by_table: Dict[str, int] = {}
        errors: List[Tuple[str, str]] = []
        conn = self._open_load_connection()
        try:
            cur = conn.cursor()
            for key, database, table, cols, spec_or_rows, staged in payloads:
                # a spec (production) or a ready list of parameter rows (tests)
                if hasattr(spec_or_rows, "records"):
                    param_rows = [[r.get(c) for c in cols] for r in (spec_or_rows.records or [])]
                    spec_or_rows.records = None     # loaded below; not held twice, not held after
                else:
                    param_rows = spec_or_rows
                if not param_rows:
                    rows_by_table[key] = 0
                    continue
                collist = ", ".join(self._q(c) for c in cols)
                placeholders = ", ".join(["?"] * len(cols))
                target = f"{self._q(database)}.{self._q(table)}"
                stage = f"{self._q(database)}.{self._q(table + self.STAGE_SUFFIX)}"
                ins_into = stage if staged else target
                ins = f'INSERT INTO {ins_into} ({collist}) VALUES ({placeholders})'
                # Chunked: one array-bound request per LOAD_BATCH_ROWS rows.  A single
                # request for a 2,000-row × 60-column table is fine; 20k rows in one
                # parcel is not (driver message limits), and a failure then names the
                # chunk rather than voiding the whole table.
                loaded = 0
                t0 = time.monotonic()
                try:
                    if staged:
                        self._drop_if_exists(cur, stage)
                        cur.execute(f"CREATE MULTISET TABLE {stage} AS {target} WITH NO DATA")
                    for start in range(0, len(param_rows), self.LOAD_BATCH_ROWS):
                        chunk = param_rows[start:start + self.LOAD_BATCH_ROWS]
                        cur.executemany(ins, chunk)
                        loaded += len(chunk)
                    if staged:
                        cur.execute(f"INSERT INTO {target} ({collist}) SELECT {collist} FROM {stage}")
                        self._drop_if_exists(cur, stage)
                    rows_by_table[key] = loaded
                    dt = time.monotonic() - t0
                    logger.info("[TD-load] %s: %d rows x %d cols in %.1fs (%.0f rows/s)%s",
                                key, loaded, len(cols), dt, loaded / dt if dt > 0 else 0,
                                " [staged]" if staged else "")
                    del param_rows
                except Exception as exc:  # noqa: BLE001
                    # A failure INSIDE the staging table never reached the target;
                    # a failed INSERT-SELECT is all-or-nothing.  Either way the
                    # target holds 0 of these rows, and the stage is cleaned up.
                    rows_by_table[key] = 0 if staged else loaded
                    errors.append((key, self._extract_td_error(cur, exc)))
                    if staged:
                        self._drop_if_exists(cur, stage)
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return rows_by_table, errors

    async def collect_statistics(self, plan, *, only_identifiers=None):
        """Run the plan's ``COLLECT STATISTICS`` statements (post-load phase).

        ``only_identifiers`` (a set of ``db.table`` keys) restricts collection to
        tables that loaded successfully — never collect stats on a table whose
        load errored.
        """
        result = DeployResult()
        await self.initialize()
        for stmt in plan.by_kind("statistics"):
            if only_identifiers is not None and stmt.identifier not in only_identifiers:
                result.skipped.append((stmt.kind, stmt.identifier))
                continue
            try:
                await self._execute_sql(stmt.sql)
                result.executed.append((stmt.kind, stmt.identifier))
            except Exception as exc:  # noqa: BLE001
                result.errors.append((stmt.identifier, str(exc)))
        result.success = not result.errors
        return result

    async def clear_tables(self, identifiers: List[str]) -> DeployResult:
        """``DELETE FROM db.t ALL`` per table — Teradata's fast path (no transient
        journal), so a 200k-row model empties in seconds.  Order does not matter:
        the FKs are soft RI (``WITH NO CHECK OPTION``)."""
        result = DeployResult()
        await self.initialize()
        for ident in identifiers:
            try:
                await self._execute_sql(f'DELETE FROM {self._qualified(ident)} ALL;')
                result.executed.append(("table", ident))
            except Exception as exc:  # noqa: BLE001
                result.errors.append((ident, str(exc)))
        result.success = not result.errors
        return result

    async def drop_objects(self, identifiers: List[str]) -> DeployResult:
        result = DeployResult()
        await self.initialize()
        for ident in identifiers:
            try:
                await self._execute_sql(f'DROP TABLE {self._qualified(ident)};')
                result.executed.append(("table", ident))
            except Exception as exc:  # noqa: BLE001
                result.errors.append((ident, str(exc)))
        result.success = not result.errors
        return result

    async def test_connection(self) -> Dict[str, object]:
        try:
            await self.initialize()
            await self._execute_sql("SELECT 1;")
            return {"success": True, "message": "Teradata connection OK"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": str(exc)}

    # ── identifier helpers ────────────────────────────────────────────────────

    @staticmethod
    def _q(ident: str) -> str:
        return '"' + str(ident).replace('"', '""') + '"'

    @classmethod
    def _qualified(cls, dotted: str) -> str:
        """Quote a possibly db.table identifier."""
        return ".".join(cls._q(p) for p in str(dotted).split("."))
