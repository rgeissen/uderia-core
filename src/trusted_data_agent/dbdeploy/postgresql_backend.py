"""
PostgresDeploymentBackend — the second deployment backend (PROP-008 C3).

Runs a delta (``execute_delta`` → ``execute_ddl``) against PostgreSQL and reads the
catalogue back in the shapes the planner and the readback consume.  A Teradata
"database" maps to a PostgreSQL SCHEMA inside the connection's database: the landing
rule, the platform sibling ``UDERIA_EXT_<root>`` and the dictionary all speak schemas.

Every statement runs in its own transaction (autocommit) — the delta contract is
resumable per statement and never stops on a failed one, the same as the Teradata
backend; PostgreSQL could run DDL transactionally (declared as a capability) but a
half-landed delta resuming from the catalogue is the platform's model on every backend.

psycopg2 is synchronous; every call runs on a worker thread so the event loop never
waits on the database.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from .backend import DeploymentBackend, DeploymentError
from .capabilities import DeploymentCapability as C
from .types import DDLPlan, DeployResult

logger = logging.getLogger("dbdeploy.postgresql")


def _q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def _split(identifier: str) -> Tuple[str, str]:
    s, _, t = str(identifier).partition(".")
    return s, t


class PostgresDeploymentBackend(DeploymentBackend):

    def __init__(self, connection_config: Optional[Dict[str, Any]] = None):
        self.config = dict(connection_config or {})

    @property
    def backend_type(self) -> str:
        return "postgresql"

    def capabilities(self):
        return {C.EXECUTE_DDL, C.DRY_RUN, C.CREATE_DATABASE, C.DROP_OBJECTS, C.COLLECT_STATISTICS,
                C.TRANSACTIONAL_DDL}

    # ── the connection ──
    def _connect(self):
        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover
            raise DeploymentError("psycopg2 is not installed — pip install psycopg2-binary") from exc
        cfg = self.config
        kw: Dict[str, Any] = {"host": cfg.get("host") or "localhost", "port": int(cfg.get("port") or 5432),
                              "user": cfg.get("username") or cfg.get("user"), "password": cfg.get("password"),
                              "dbname": cfg.get("database") or "postgres", "connect_timeout": 15}
        if cfg.get("ssl"):
            kw["sslmode"] = "require"
        conn = psycopg2.connect(**{k: v for k, v in kw.items() if v not in (None, "")})
        conn.autocommit = True
        return conn

    def _run(self, sql: str, params: Optional[tuple] = None, fetch: bool = True) -> List[tuple]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if (fetch and cur.description is not None) else []
        finally:
            conn.close()

    async def _query(self, sql: str, params: Optional[tuple] = None) -> List[tuple]:
        return await asyncio.to_thread(self._run, sql, params, True)

    async def _execute_sql(self, sql: str) -> List[tuple]:
        """One statement, its own transaction (the verifier and the deploy both use it)."""
        return await asyncio.to_thread(self._run, sql, None, True)

    async def initialize(self) -> None:
        return None

    async def test_connection(self) -> Dict[str, object]:
        try:
            await self._query("SELECT 1")
            return {"success": True, "message": "PostgreSQL connection OK"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": str(exc)}

    async def system_version(self) -> Optional[str]:
        """``PostgreSQL 17.11`` — the version a rule is verified against."""
        try:
            rows = await self._query("SHOW server_version")
            return f"PostgreSQL {str(rows[0][0]).split()[0]}" if rows else None
        except Exception as exc:  # noqa: BLE001
            logger.info("[PG-deploy] version not read: %s", exc)
            return None

    # ── DDL ──
    async def execute_ddl(self, plan: DDLPlan, *, dry_run: bool = False, stop_on_error: bool = True,
                          include_statistics: bool = True, replace_existing: bool = False) -> DeployResult:
        result = DeployResult(dry_run=dry_run, generated_sql=plan.sql_script())
        if dry_run:
            result.planned = [f"{s.kind}:{s.identifier}" for s in plan.ordered()]
            result.success = True
            return result
        if replace_existing:
            raise DeploymentError("postgresql: replace_existing is not supported by the delta backend")
        for stmt in plan.ordered():
            if not include_statistics and stmt.kind == "statistics":
                result.skipped.append((stmt.kind, stmt.identifier))
                continue
            try:
                await asyncio.to_thread(self._run, stmt.sql, None, False)
                result.executed.append((stmt.kind, stmt.identifier))
            except Exception as exc:  # noqa: BLE001
                result.errors.append((stmt.identifier, str(exc).strip().splitlines()[0][:300]))
                if stop_on_error:
                    break
        result.success = not result.errors
        return result

    async def collect_statistics(self, plan: DDLPlan, *, only_identifiers=None) -> DeployResult:
        res = DeployResult()
        for st in plan.ordered():
            if st.kind != "statistics" or (only_identifiers and st.identifier not in only_identifiers):
                continue
            try:
                await asyncio.to_thread(self._run, st.sql, None, False)
                res.executed.append((st.kind, st.identifier))
            except Exception as exc:  # noqa: BLE001
                res.errors.append((st.identifier, str(exc)))
        res.success = not res.errors
        return res

    # ── schemas ("databases" in the delta's vocabulary) ──
    def preview_create_database(self, name: str, *, parent: Optional[str] = None,
                                perm_bytes: Optional[int] = None) -> str:
        return f"CREATE SCHEMA {_q(name)};"

    async def database_exists(self, name: str) -> bool:
        rows = await self._query("SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s", (name,))
        return bool(rows)

    async def ensure_database(self, name: str, *, parent: Optional[str] = None, perm_bytes: Optional[int] = None) -> bool:
        if await self.database_exists(name):
            return False
        await asyncio.to_thread(self._run, f"CREATE SCHEMA {_q(name)}", None, False)
        return True

    async def database_owned(self, name: str) -> Optional[bool]:
        """The login owns the schema, or may CREATE in it.  A schema that does not exist is
        not owned (the sibling is provisioned instead); an unreadable answer is ``None``."""
        try:
            rows = await self._query(
                "SELECT pg_catalog.pg_get_userbyid(nspowner) = current_user "
                "OR pg_catalog.has_schema_privilege(current_user, nspname, 'CREATE') "
                "FROM pg_catalog.pg_namespace WHERE nspname = %s", (name,))
        except Exception as exc:  # noqa: BLE001
            logger.info("[PG-deploy] ownership of %s not read: %s", name, exc)
            return None
        return bool(rows[0][0]) if rows else False

    async def list_database_objects(self, name: str) -> List[Tuple[str, str]]:
        rows = await self._query(
            "SELECT CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'table' WHEN 'v' THEN 'view' "
            "WHEN 'm' THEN 'view' ELSE 'other' END, c.relname FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind IN ('r','p','v','m')", (name,))
        return [(k, n) for k, n in rows]

    async def drop_database(self, name: str, *, owned: Optional[Set[str]] = None) -> DeployResult:
        result = DeployResult()
        if not await self.database_exists(name):
            result.skipped.append(("database", name))
            result.success = True
            return result
        if owned is not None:
            own = {str(o).strip().lower() for o in owned}
            strangers = [f"{k} {n}" for k, n in await self.list_database_objects(name) if n.lower() not in own]
            if strangers:
                result.errors.append((name, "schema holds object(s) the deploy plan does not own — not dropped: "
                                            + ", ".join(sorted(strangers)[:10])))
                return result
        try:
            await asyncio.to_thread(self._run, f"DROP SCHEMA {_q(name)} CASCADE", None, False)
            result.executed.append(("database", name))
            result.success = True
        except Exception as exc:  # noqa: BLE001
            result.errors.append((name, str(exc)))
        return result

    async def drop_objects(self, identifiers: List[str]) -> DeployResult:
        result = DeployResult()
        for ident in identifiers:
            s, t = _split(ident)
            try:
                await asyncio.to_thread(self._run, f"DROP TABLE IF EXISTS {_q(s)}.{_q(t)}", None, False)
                result.executed.append(("table", ident))
            except Exception as exc:  # noqa: BLE001
                result.errors.append((ident, str(exc)))
        result.success = not result.errors
        return result

    # ── the dictionary (what plan_delta reads as *before*) ──
    async def describe_tables(self, database: str, tables: Optional[List[str]] = None) -> Dict[str, Dict[str, object]]:
        cols = await self._query(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s ORDER BY table_name, ordinal_position", (database,))
        want = {t.lower() for t in tables} if tables else None
        out: Dict[str, Dict[str, object]] = {}
        for t, c, dt in cols:
            if want is not None and t.lower() not in want:
                continue
            out.setdefault(f"{database}.{t}", {"columns": {}, "foreign_keys": []})["columns"][c] = dt
        # what each object IS: a reference can be declared on a table, never on a view
        try:
            for t, tt in await self._query(
                    "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = %s", (database,)):
                key = f"{database}.{t}"
                if key in out:
                    out[key]["kind"] = "view" if str(tt or "").upper() == "VIEW" else "table"
        except Exception:  # noqa: BLE001 — an unread kind is an unknown kind
            pass
        for t, col, ps, pt, pc in await self._foreign_keys(database):
            key = f"{database}.{t}"
            if key in out:
                out[key]["foreign_keys"].append((col, f"{ps}.{pt}", pc))
        return out

    async def _foreign_keys(self, schema: str, table: Optional[str] = None) -> List[tuple]:
        """Single-column foreign keys: (table, column, parent schema, parent table, parent column)."""
        sql = ("SELECT c.relname, a.attname, pn.nspname, pc.relname, pa.attname "
               "FROM pg_catalog.pg_constraint k "
               "JOIN pg_catalog.pg_class c ON c.oid = k.conrelid "
               "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
               "JOIN pg_catalog.pg_class pc ON pc.oid = k.confrelid "
               "JOIN pg_catalog.pg_namespace pn ON pn.oid = pc.relnamespace "
               "JOIN pg_catalog.pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1] "
               "JOIN pg_catalog.pg_attribute pa ON pa.attrelid = k.confrelid AND pa.attnum = k.confkey[1] "
               "WHERE k.contype = 'f' AND n.nspname = %s AND array_length(k.conkey, 1) = 1")
        params: tuple = (schema,)
        if table:
            sql += " AND c.relname = %s"
            params = (schema, table)
        return await self._query(sql, params)

    # ── the readback (A4 shape, PostgreSQL facts) ──
    async def read_design(self, identifiers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Per table: the exact name, every column (nullability, exact case), the primary key,
        single-column foreign keys, index leading columns, whether it was analyzed, the
        comment, and the database encoding.  A fact that cannot be read is ``None``."""
        out: Dict[str, Dict[str, Any]] = {}
        try:
            enc_rows = await self._query("SELECT pg_catalog.pg_encoding_to_char(encoding) FROM pg_catalog.pg_database "
                                         "WHERE datname = current_database()")
            encoding = enc_rows[0][0] if enc_rows else None
        except Exception:  # noqa: BLE001
            encoding = None
        for ident in identifiers:
            s, t = _split(ident)
            facts: Dict[str, Any] = {"encoding": encoding}
            try:
                rel = await self._query(
                    "SELECT c.oid, c.reltuples >= 0, pg_catalog.obj_description(c.oid, 'pg_class') "
                    "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r','p')", (s, t))
            except Exception as exc:  # noqa: BLE001
                logger.info("[PG-deploy] %s not read: %s", ident, exc)
                continue
            if not rel:
                continue
            oid, analyzed, comment = rel[0]
            facts.update({"name_exact": True, "analyzed": bool(analyzed), "comment": comment})
            try:
                cols = await self._query(
                    "SELECT attname, pg_catalog.format_type(atttypid, atttypmod), attnotnull FROM pg_catalog.pg_attribute "
                    "WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped ORDER BY attnum", (oid,))
                facts["columns"] = {n: {"type": ty, "nullable": not nn, "charset": None} for n, ty, nn in cols}
                facts["exact_columns"] = [n for n, _ty, _nn in cols]
            except Exception:  # noqa: BLE001
                facts["columns"], facts["exact_columns"] = None, None
            try:
                pk = await self._query(
                    "SELECT a.attname FROM pg_catalog.pg_constraint k "
                    "CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY AS u(attnum, ord) "
                    "JOIN pg_catalog.pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum "
                    "WHERE k.conrelid = %s AND k.contype = 'p' ORDER BY u.ord", (oid,))
                facts["primary_key"] = [r[0] for r in pk]
            except Exception:  # noqa: BLE001
                facts["primary_key"] = None
            try:
                facts["foreign_keys"] = [(col, f"{ps}.{pt}", pc) for _t, col, ps, pt, pc in await self._foreign_keys(s, t)]
            except Exception:  # noqa: BLE001
                facts["foreign_keys"] = None
            try:
                ix = await self._query(
                    "SELECT array_agg(a.attname ORDER BY u.ord) FROM pg_catalog.pg_index i "
                    "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS u(attnum, ord) "
                    "JOIN pg_catalog.pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = u.attnum "
                    "WHERE i.indrelid = %s GROUP BY i.indexrelid", (oid,))
                facts["indexes"] = [list(r[0]) for r in ix]
            except Exception:  # noqa: BLE001
                facts["indexes"] = None
            out[ident] = facts
        return out
