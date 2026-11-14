"""
SQL Native Connector — executes SQL against real databases.

Supports multiple drivers with graceful ImportError handling:
  - PostgreSQL via asyncpg
  - MySQL via aiomysql
  - SQLite via aiosqlite
  - DuckDB via duckdb (sync, wrapped in run_in_executor; read-only at the database level)
  - MongoDB via pymongo (a JSON statement: collection + aggregation pipeline — core.mongo_query;
    read-only is a stage allowlist, since MongoDB has no client-side read-only mode)
  - Teradata via teradatasql (sync, wrapped in run_in_executor)
  - JDBC via JayDeBeApi (sync, wrapped in run_in_executor — requires Java runtime)
"""

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from .base import BaseCanvasConnector, ExecutionResult, ConnectionTestResult

logger = logging.getLogger("quart.app")

# Thread pool for sync drivers (Teradata)
_sync_executor = ThreadPoolExecutor(max_workers=2)


class SQLNativeConnector(BaseCanvasConnector):
    """Executes SQL against PostgreSQL, MySQL, SQLite, Teradata, or any JDBC-compatible database."""

    id = 'sql_native'
    name = 'SQL (Native)'
    # The driver list is DECLARED once (core.database_connections.DRIVERS); this is its view.
    try:
        from trusted_data_agent.core.database_connections import SUPPORTED_DRIVERS as _DRV
        supported_drivers = list(_DRV)
    except Exception:  # noqa: BLE001 — standalone import (tests of the connector alone)
        supported_drivers = ['postgresql', 'mysql', 'sqlite', 'duckdb', 'mongodb', 'teradata', 'jdbc']

    async def execute(self, code: str, credentials: dict, *, max_rows: int = 0,
                      readonly: bool = False) -> ExecutionResult:
        """Execute SQL using the appropriate driver.

        ``max_rows`` > 0 fetches at most that many rows (plus one to detect truncation) instead
        of the whole result; ``readonly`` opens the connection read-only at the DATABASE level
        (SQLite ``mode=ro``, a Postgres read-only transaction, MySQL ``READ ONLY``) — defence in
        depth behind the caller's SELECT-only guard. Both default off: the Canvas path is
        unchanged."""
        driver = credentials.get('driver', 'postgresql')
        start = time.monotonic()

        try:
            if driver == 'postgresql':
                return await self._execute_postgresql(code, credentials, start, max_rows, readonly)
            elif driver == 'mysql':
                return await self._execute_mysql(code, credentials, start, max_rows, readonly)
            elif driver == 'sqlite':
                return await self._execute_sqlite(code, credentials, start, max_rows, readonly)
            elif driver == 'duckdb':
                return await self._execute_duckdb(code, credentials, start, max_rows, readonly)
            elif driver == 'mongodb':
                return await self._execute_mongodb(code, credentials, start, max_rows, readonly)
            elif driver == 'teradata':
                return await self._execute_teradata(code, credentials, start)
            elif driver == 'jdbc':
                return await self._execute_jdbc(code, credentials, start)
            else:
                return ExecutionResult(
                    error=f"Unsupported driver: {driver}",
                    execution_time_ms=int((time.monotonic() - start) * 1000),
                )
        except Exception as e:
            return ExecutionResult(
                error=str(e),
                execution_time_ms=int((time.monotonic() - start) * 1000),
            )

    async def test_connection(self, credentials: dict) -> ConnectionTestResult:
        """Test database connectivity."""
        driver = credentials.get('driver', 'postgresql')

        try:
            if driver == 'postgresql':
                return await self._test_postgresql(credentials)
            elif driver == 'mysql':
                return await self._test_mysql(credentials)
            elif driver == 'sqlite':
                return await self._test_sqlite(credentials)
            elif driver == 'duckdb':
                return await self._test_duckdb(credentials)
            elif driver == 'mongodb':
                return await self._test_mongodb(credentials)
            elif driver == 'teradata':
                return await self._test_teradata(credentials)
            elif driver == 'jdbc':
                return await self._test_jdbc(credentials)
            else:
                return ConnectionTestResult(valid=False, message=f"Unsupported driver: {driver}")
        except ImportError as e:
            return ConnectionTestResult(
                valid=False,
                message=f"Driver not installed: {e}. Install with: pip install {self._pip_package(driver)}",
            )
        except Exception as e:
            return ConnectionTestResult(valid=False, message=str(e))

    # ── PostgreSQL ────────────────────────────────────────────────────────────

    async def _execute_postgresql(self, code: str, creds: dict, start: float,
                                  max_rows: int = 0, readonly: bool = False) -> ExecutionResult:
        try:
            import asyncpg
        except ImportError:
            return ExecutionResult(error="asyncpg not installed. Run: pip install asyncpg")

        conn = await asyncpg.connect(
            host=creds.get('host', 'localhost'),
            port=int(creds.get('port', 5432)),
            database=creds.get('database', ''),
            user=creds.get('user', ''),
            password=creds.get('password', ''),
            ssl=creds.get('ssl', False) or None,
        )
        try:
            truncated = False
            if max_rows or readonly:
                async with conn.transaction(readonly=readonly):
                    if max_rows:
                        cur = await conn.cursor(code)
                        rows = await cur.fetch(max_rows + 1)
                        truncated = len(rows) > max_rows
                        rows = rows[:max_rows]
                    else:
                        rows = await conn.fetch(code)
            else:
                rows = await conn.fetch(code)
            elapsed = int((time.monotonic() - start) * 1000)
            if rows:
                columns = list(rows[0].keys())
                header = '\t'.join(columns)
                body = '\n'.join('\t'.join(str(v) for v in row.values()) for row in rows)
                return ExecutionResult(
                    result=f"{header}\n{body}",
                    row_count=len(rows),
                    execution_time_ms=elapsed,
                    columns=columns, rows=[list(r.values()) for r in rows], truncated=truncated,
                )
            return ExecutionResult(result='Query executed successfully (0 rows)', row_count=0, execution_time_ms=elapsed,
                                   columns=[], rows=[])
        finally:
            await conn.close()

    async def _test_postgresql(self, creds: dict) -> ConnectionTestResult:
        import asyncpg
        conn = await asyncpg.connect(
            host=creds.get('host', 'localhost'),
            port=int(creds.get('port', 5432)),
            database=creds.get('database', ''),
            user=creds.get('user', ''),
            password=creds.get('password', ''),
            ssl=creds.get('ssl', False) or None,
        )
        try:
            version = await conn.fetchval('SELECT version()')
            return ConnectionTestResult(valid=True, message='Connected', server_info=version)
        finally:
            await conn.close()

    # ── MySQL ─────────────────────────────────────────────────────────────────

    async def _execute_mysql(self, code: str, creds: dict, start: float,
                             max_rows: int = 0, readonly: bool = False) -> ExecutionResult:
        try:
            import aiomysql
        except ImportError:
            return ExecutionResult(error="aiomysql not installed. Run: pip install aiomysql")

        conn = await aiomysql.connect(
            host=creds.get('host', 'localhost'),
            port=int(creds.get('port', 3306)),
            db=creds.get('database', ''),
            user=creds.get('user', ''),
            password=creds.get('password', ''),
        )
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if readonly:
                    await cur.execute("START TRANSACTION READ ONLY")
                await cur.execute(code)
                truncated = False
                if max_rows:
                    rows = await cur.fetchmany(max_rows + 1)
                    truncated = len(rows) > max_rows
                    rows = rows[:max_rows]
                else:
                    rows = await cur.fetchall()
                elapsed = int((time.monotonic() - start) * 1000)
                if rows:
                    columns = list(rows[0].keys())
                    header = '\t'.join(columns)
                    body = '\n'.join('\t'.join(str(v) for v in row.values()) for row in rows)
                    return ExecutionResult(
                        result=f"{header}\n{body}",
                        row_count=len(rows),
                        execution_time_ms=elapsed,
                        columns=columns, rows=[list(r.values()) for r in rows], truncated=truncated,
                    )
                return ExecutionResult(
                    result=f'Query executed successfully ({cur.rowcount} rows affected)',
                    row_count=cur.rowcount,
                    execution_time_ms=elapsed,
                )
        finally:
            conn.close()

    async def _test_mysql(self, creds: dict) -> ConnectionTestResult:
        import aiomysql
        conn = await aiomysql.connect(
            host=creds.get('host', 'localhost'),
            port=int(creds.get('port', 3306)),
            db=creds.get('database', ''),
            user=creds.get('user', ''),
            password=creds.get('password', ''),
        )
        try:
            async with conn.cursor() as cur:
                await cur.execute('SELECT VERSION()')
                row = await cur.fetchone()
                return ConnectionTestResult(valid=True, message='Connected', server_info=row[0] if row else None)
        finally:
            conn.close()

    # ── SQLite ────────────────────────────────────────────────────────────────

    async def _execute_sqlite(self, code: str, creds: dict, start: float,
                              max_rows: int = 0, readonly: bool = False) -> ExecutionResult:
        try:
            import aiosqlite
        except ImportError:
            return ExecutionResult(error="aiosqlite not installed. Run: pip install aiosqlite")

        db_path = creds.get('database', ':memory:')
        target, uri = db_path, False
        if readonly and db_path != ':memory:':
            from pathlib import Path as _P
            target, uri = f"{_P(db_path).resolve().as_uri()}?mode=ro", True
        async with aiosqlite.connect(target, uri=uri) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(code)
            truncated = False
            if max_rows:
                rows = await cursor.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
            else:
                rows = await cursor.fetchall()
            elapsed = int((time.monotonic() - start) * 1000)

            if rows and cursor.description:
                columns = [d[0] for d in cursor.description]
                header = '\t'.join(columns)
                body = '\n'.join('\t'.join(str(row[c]) for c in columns) for row in rows)
                return ExecutionResult(
                    result=f"{header}\n{body}",
                    row_count=len(rows),
                    execution_time_ms=elapsed,
                    columns=columns, rows=[[row[c] for c in columns] for row in rows], truncated=truncated,
                )
            return ExecutionResult(
                result=f'Query executed successfully ({cursor.rowcount} rows affected)',
                row_count=max(cursor.rowcount, 0),
                execution_time_ms=elapsed,
                columns=[d[0] for d in (cursor.description or [])], rows=[],
            )

    async def _test_sqlite(self, creds: dict) -> ConnectionTestResult:
        import aiosqlite
        db_path = creds.get('database', ':memory:')
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute('SELECT sqlite_version()')
            row = await cursor.fetchone()
            return ConnectionTestResult(valid=True, message='Connected', server_info=f"SQLite {row[0]}" if row else None)

    # ── DuckDB ────────────────────────────────────────────────────────────────

    @staticmethod
    def _duckdb_path(creds: dict) -> str:
        path = str(creds.get('database') or '').strip()
        if not path:
            raise ValueError("a DuckDB connection needs the database file path")
        # duckdb.connect() CREATES a missing file — a mistyped path would silently become
        # an empty database whose tables "do not exist". Refuse it and say which path.
        if path != ':memory:':
            from pathlib import Path as _P
            if not _P(path).expanduser().is_file():
                raise FileNotFoundError(f"DuckDB file not found: {path}")
            path = str(_P(path).expanduser())
        return path

    async def _execute_duckdb(self, code: str, creds: dict, start: float,
                              max_rows: int = 0, readonly: bool = False) -> ExecutionResult:
        try:
            import duckdb
        except ImportError:
            return ExecutionResult(error="duckdb not installed. Run: pip install duckdb")
        path = self._duckdb_path(creds)

        def _run():
            # read_only=True is the database's own refusal of any write — the same
            # guarantee SQLite's mode=ro gives the Navigator. A read-only open also lets
            # several readers share a file another process holds.
            conn = duckdb.connect(path, read_only=bool(readonly))
            try:
                cur = conn.execute(code)
                if not cur.description:
                    return None, [], False
                columns = [d[0] for d in cur.description]
                if max_rows:
                    rows = cur.fetchmany(max_rows + 1)
                    truncated = len(rows) > max_rows
                    rows = rows[:max_rows]
                else:
                    rows, truncated = cur.fetchall(), False
                return columns, [list(r) for r in rows], truncated
            finally:
                conn.close()

        loop = asyncio.get_event_loop()
        columns, rows, truncated = await loop.run_in_executor(_sync_executor, _run)
        elapsed = int((time.monotonic() - start) * 1000)
        if columns:
            header = '\t'.join(columns)
            body = '\n'.join('\t'.join(str(v) for v in r) for r in rows)
            return ExecutionResult(result=f"{header}\n{body}", row_count=len(rows), execution_time_ms=elapsed,
                                   columns=columns, rows=rows, truncated=truncated)
        return ExecutionResult(result='Query executed successfully', row_count=0, execution_time_ms=elapsed,
                               columns=[], rows=[])

    async def _test_duckdb(self, creds: dict) -> ConnectionTestResult:
        import duckdb
        path = self._duckdb_path(creds)

        def _run():
            conn = duckdb.connect(path, read_only=True)
            try:
                return conn.execute('SELECT version()').fetchone()[0]
            finally:
                conn.close()

        version = await asyncio.get_event_loop().run_in_executor(_sync_executor, _run)
        return ConnectionTestResult(valid=True, message='Connected', server_info=f"DuckDB {version}")

    # ── MongoDB ───────────────────────────────────────────────────────────────

    async def _execute_mongodb(self, code: str, creds: dict, start: float,
                               max_rows: int = 0, readonly: bool = False) -> ExecutionResult:
        """A JSON statement (collection + pipeline, or a find-style filter) as ONE aggregate.
        ``readonly`` applies the stage allowlist before anything is sent; without it the
        Canvas runs what its owner wrote, as it does for SQL."""
        try:
            from trusted_data_agent.core import mongo_client as MC
            from trusted_data_agent.core.mongo_query import MongoQueryError
        except ImportError as e:
            return ExecutionResult(error=f"MongoDB support unavailable: {e}")
        try:
            out = await asyncio.get_event_loop().run_in_executor(
                _sync_executor, lambda: MC.run_statement(creds, code, max_rows=max_rows, readonly=bool(readonly)))
        except MongoQueryError as e:
            return ExecutionResult(error=str(e), execution_time_ms=int((time.monotonic() - start) * 1000))
        elapsed = int((time.monotonic() - start) * 1000)
        cols, rows = out["columns"], out["rows"]
        if cols:
            header = '\t'.join(cols)
            body = '\n'.join('\t'.join('' if v is None else str(v) for v in r) for r in rows)
            return ExecutionResult(result=f"{header}\n{body}", row_count=len(rows), execution_time_ms=elapsed,
                                   columns=cols, rows=rows, truncated=out["truncated"])
        return ExecutionResult(result='Query executed successfully (0 documents)', row_count=0,
                               execution_time_ms=elapsed, columns=[], rows=[])

    async def _test_mongodb(self, creds: dict) -> ConnectionTestResult:
        from trusted_data_agent.core import mongo_client as MC

        def _run():
            version = MC.server_version(creds)
            rights = MC.login_rights(creds, refresh=True)
            return version, rights

        version, rights = await asyncio.get_event_loop().run_in_executor(_sync_executor, _run)
        if rights.get("can_write") is False:
            note = "login is read-only"
        elif rights.get("can_write"):
            note = f"this login can write — {rights.get('reason')}"
        else:
            note = rights.get("reason") or "the login's rights could not be read"
        return ConnectionTestResult(valid=True, message=f"Connected · {note}", server_info=f"MongoDB {version}")

    # ── Teradata ──────────────────────────────────────────────────────────────

    async def _execute_teradata(self, code: str, creds: dict, start: float) -> ExecutionResult:
        try:
            import teradatasql
        except ImportError:
            return ExecutionResult(error="teradatasql not installed. Run: pip install teradatasql")

        def _run():
            with teradatasql.connect(
                host=creds.get('host', ''),
                user=creds.get('user', ''),
                password=creds.get('password', ''),
                database=creds.get('database', ''),
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute(code)
                    if cur.description:
                        columns = [d[0] for d in cur.description]
                        rows = [list(r) for r in cur.fetchall()]
                        header = '\t'.join(columns)
                        body = '\n'.join('\t'.join(str(v) for v in row) for row in rows)
                        return header, body, len(rows), columns, rows
                    return None, f'{cur.rowcount} rows affected', max(cur.rowcount, 0), None, None

        loop = asyncio.get_event_loop()
        header, body, count, columns, rows = await loop.run_in_executor(_sync_executor, _run)
        elapsed = int((time.monotonic() - start) * 1000)

        if header:
            return ExecutionResult(result=f"{header}\n{body}", row_count=count, execution_time_ms=elapsed,
                                   columns=columns, rows=rows)
        return ExecutionResult(result=f'Query executed successfully ({body})', row_count=count, execution_time_ms=elapsed)

    async def _test_teradata(self, creds: dict) -> ConnectionTestResult:
        import teradatasql

        def _run():
            with teradatasql.connect(
                host=creds.get('host', ''),
                user=creds.get('user', ''),
                password=creds.get('password', ''),
                database=creds.get('database', ''),
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT InfoData FROM DBC.DBCInfoV WHERE InfoKey = 'VERSION'")
                    row = cur.fetchone()
                    return row[0].strip() if row else 'Unknown'

        loop = asyncio.get_event_loop()
        version = await loop.run_in_executor(_sync_executor, _run)
        return ConnectionTestResult(valid=True, message='Connected', server_info=f"Teradata {version}")

    # ── JDBC (Generic) ───────────────────────────────────────────────────────

    async def _execute_jdbc(self, code: str, creds: dict, start: float) -> ExecutionResult:
        try:
            import jaydebeapi
        except ImportError:
            return ExecutionResult(error="JayDeBeApi not installed. Run: pip install JayDeBeApi (requires Java runtime)")

        jdbc_url = creds.get('jdbc_url', '')
        driver_class = creds.get('jdbc_driver_class', '')
        jar_path = creds.get('jdbc_driver_path', '')

        if not jdbc_url or not driver_class:
            return ExecutionResult(error="JDBC URL and Driver Class are required")

        def _run():
            jar_list = [p.strip() for p in jar_path.split(',') if p.strip()] if jar_path else None
            conn = jaydebeapi.connect(
                driver_class,
                jdbc_url,
                [creds.get('user', ''), creds.get('password', '')],
                jar_list,
            )
            try:
                cur = conn.cursor()
                cur.execute(code)
                if cur.description:
                    columns = [d[0] for d in cur.description]
                    rows = [list(r) for r in cur.fetchall()]
                    header = '\t'.join(columns)
                    body = '\n'.join('\t'.join(str(v) for v in row) for row in rows)
                    return header, body, len(rows), columns, rows
                rc = cur.rowcount if cur.rowcount >= 0 else 0
                return None, f'{rc} rows affected', rc, None, None
            finally:
                conn.close()

        loop = asyncio.get_event_loop()
        header, body, count, columns, rows = await loop.run_in_executor(_sync_executor, _run)
        elapsed = int((time.monotonic() - start) * 1000)

        if header:
            return ExecutionResult(result=f"{header}\n{body}", row_count=count, execution_time_ms=elapsed,
                                   columns=columns, rows=rows)
        return ExecutionResult(result=f'Query executed successfully ({body})', row_count=count, execution_time_ms=elapsed)

    async def _test_jdbc(self, creds: dict) -> ConnectionTestResult:
        import jaydebeapi

        jdbc_url = creds.get('jdbc_url', '')
        driver_class = creds.get('jdbc_driver_class', '')
        jar_path = creds.get('jdbc_driver_path', '')

        if not jdbc_url or not driver_class:
            return ConnectionTestResult(valid=False, message="JDBC URL and Driver Class are required")

        def _run():
            jar_list = [p.strip() for p in jar_path.split(',') if p.strip()] if jar_path else None
            conn = jaydebeapi.connect(
                driver_class,
                jdbc_url,
                [creds.get('user', ''), creds.get('password', '')],
                jar_list,
            )
            try:
                cur = conn.cursor()
                cur.execute('SELECT 1')
                cur.fetchone()
                return jdbc_url
            finally:
                conn.close()

        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(_sync_executor, _run)
        return ConnectionTestResult(valid=True, message='Connected', server_info=f"JDBC: {info}")

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _pip_package(driver: str) -> str:
        return {
            'postgresql': 'asyncpg',
            'mysql': 'aiomysql',
            'sqlite': 'aiosqlite',
            'duckdb': 'duckdb',
            'mongodb': 'pymongo',
            'teradata': 'teradatasql',
            'jdbc': 'JayDeBeApi',
        }.get(driver, driver)
