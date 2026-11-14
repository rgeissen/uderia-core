"""Generic direct catalog provider — PostgreSQL / MySQL / DuckDB via ``information_schema``,
SQLite via ``sqlite_master`` + PRAGMA. Which reader a driver gets is DECLARED once, in
``core.database_connections.DRIVERS`` (its ``catalog`` field) — never listed here. Same contract as the Teradata provider with a
smaller capability set (no sizes/statistics/indexes beyond the primary key); the
renderer reads the capability set, so what is missing is SAID, not blank."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set

from .base import CatalogError, CatalogProvider
from .capabilities import CatalogCapability as C
from .sql_runner import rows_as_dicts, run_rows, single_select_guard
from .types import (ColumnInfo, DatabaseInfo, ForeignKey, IndexInfo, ObjectDetail,
                    ObjectInfo, PreviewResult)

logger = logging.getLogger("quart.app")

_SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast", "mysql",
                             "performance_schema", "sys"})


def _lit(s: str) -> str:
    return str(s).replace("'", "''")


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _i(v: Any) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class GenericCatalogProvider(CatalogProvider):
    provider_kind = "direct"

    def __init__(self, source_id: str, label: str, creds: Dict[str, Any],
                 runner: Callable = run_rows):
        self._id, self._label, self._creds, self._run = source_id, label, dict(creds), runner
        from trusted_data_agent.core.database_connections import DRIVERS
        self.driver = str(creds.get("driver") or "").strip().lower()
        spec = DRIVERS.get(self.driver) or {}
        self._reader = spec.get("catalog")
        self._comments = bool(spec.get("comments"))
        if self._reader not in ("information_schema", "sqlite"):
            raise CatalogError(f"no dictionary reader for driver '{self.driver}' yet — "
                               f"use the profile's MCP tools for this source")

    @property
    def source_id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    def capabilities(self) -> Set[C]:
        caps = {C.LIST_DATABASES, C.LIST_OBJECTS, C.COLUMNS, C.FOREIGN_KEYS, C.INDEXES,
                C.VIEW_TEXT, C.ROW_COUNT, C.PREVIEW, C.OBJECT_KINDS}
        if self._comments:
            caps.add(C.COMMENTS)
        return caps

    def _q(self, ident: str) -> str:
        if '"' in ident or "`" in ident or "\x00" in ident:
            raise CatalogError(f"refusing identifier {ident!r}")
        return f"`{ident}`" if self.driver == "mysql" else f'"{ident}"'

    async def _rows(self, sql: str) -> List[Dict[str, Any]]:
        cols, rows = await self._run(self._creds, sql)
        return rows_as_dicts(cols, rows)

    async def _rows_soft(self, sql: str, what: str, unavailable: Optional[List[str]] = None):
        try:
            return await self._rows(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[dbcatalog/%s] %s unavailable: %s", self.driver, what, exc)
            if unavailable is not None:
                unavailable.append(what)
            return []

    # ── databases (= schemas) ───────────────────────────────────────────────────

    async def list_databases(self) -> List[DatabaseInfo]:
        if self._reader == "sqlite":
            n = _i((await self._rows("SELECT COUNT(*) AS n FROM sqlite_master WHERE type IN ('table','view')") or [{}])[0].get("n"))
            return [DatabaseInfo(name="main", kind="schema", object_count=n)]
        if self.driver == "duckdb":
            # DuckDB lists a schema once per attached catalog (the file, system, temp):
            # only the file's own catalog is this connection's data.
            rows = await self._rows("SELECT DISTINCT schema_name AS s FROM information_schema.schemata "
                                    "WHERE catalog_name = current_database() ORDER BY 1")
        else:
            rows = await self._rows("SELECT schema_name AS s FROM information_schema.schemata ORDER BY 1")
        counts = {r["s"]: _i(r.get("n")) for r in await self._rows_soft(
            "SELECT table_schema AS s, COUNT(*) AS n FROM information_schema.tables GROUP BY table_schema",
            "object counts")}
        return [DatabaseInfo(name=_s(r.get("s")), kind="schema", object_count=counts.get(_s(r.get("s"))),
                             is_system=_s(r.get("s")).lower() in _SYSTEM_SCHEMAS)
                for r in rows if _s(r.get("s"))]

    # ── objects ─────────────────────────────────────────────────────────────────

    async def list_objects(self, database: str) -> List[ObjectInfo]:
        if self._reader == "sqlite":
            rows = await self._rows("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")
            return [ObjectInfo(database="main", name=_s(r["name"]), kind=_s(r.get("type")) or "table")
                    for r in rows if _s(r.get("name"))]
        rows = await self._rows(
            f"SELECT table_name AS t, table_type AS k FROM information_schema.tables "
            f"WHERE table_schema = '{_lit(database)}' ORDER BY table_name")
        out = []
        for r in rows:
            k = _s(r.get("k")).upper()
            out.append(ObjectInfo(database=database, name=_s(r.get("t")),
                                  kind="view" if "VIEW" in k else "table"))
        return [o for o in out if o.name]

    # ── describe ────────────────────────────────────────────────────────────────

    async def describe_object(self, database: str, name: str) -> ObjectDetail:
        objs = {o.name.lower(): o for o in await self.list_objects(database)}
        info = objs.get(name.lower())
        if info is None:
            raise CatalogError(f"{database}.{name} is not in the catalog — refresh and try again.")
        name = info.name
        unavailable: List[str] = []
        detail = ObjectDetail(info=info, unavailable=unavailable)

        if self._reader == "sqlite":
            crow = await self._rows(f"PRAGMA table_info({self._q(name)})")
            cols = [ColumnInfo(name=_s(r.get("name")), data_type=_s(r.get("type")),
                               nullable=(_s(r.get("notnull")) in ("0", "")), position=_i(r.get("cid")),
                               default=_s(r.get("dflt_value")), is_primary=_s(r.get("pk")) not in ("0", ""))
                    for r in crow if _s(r.get("name"))]
            detail.columns = cols
            pk = [c.name for c in cols if c.is_primary]
            if pk:
                detail.indexes = [IndexInfo(name="primary key", kind="primary_key", columns=pk, unique=True)]
            for r in await self._rows_soft(f"PRAGMA foreign_key_list({self._q(name)})", "foreign keys", unavailable):
                detail.foreign_keys_out.append(ForeignKey(
                    child_database="main", child_table=name, child_columns=[_s(r.get("from"))],
                    parent_database="main", parent_table=_s(r.get("table")), parent_columns=[_s(r.get("to"))],
                    name=f"fk{_s(r.get('id'))}", reason="declared foreign key"))
            if info.kind == "view":
                vt = await self._rows_soft(f"SELECT sql FROM sqlite_master WHERE name = '{_lit(name)}'", "view text", unavailable)
                detail.view_text = _s(vt[0].get("sql")) if vt else ""
            return detail

        crow = await self._rows(
            f"SELECT column_name AS c, data_type AS t, is_nullable AS n, ordinal_position AS p, "
            f"column_default AS d, character_maximum_length AS l FROM information_schema.columns "
            f"WHERE table_schema = '{_lit(database)}' AND table_name = '{_lit(name)}' ORDER BY ordinal_position")
        cols = []
        for r in crow:
            t = _s(r.get("t")).upper()
            if _i(r.get("l")) and "CHAR" in t:
                t = f"{t}({_i(r.get('l'))})"
            cols.append(ColumnInfo(name=_s(r.get("c")), data_type=t,
                                   nullable=_s(r.get("n")).upper() == "YES", position=_i(r.get("p")),
                                   default=_s(r.get("d"))))
        detail.columns = [c for c in cols if c.name]
        by_name = {c.name.lower(): c for c in detail.columns}
        if self._comments:
            await self._read_comments(database, name, info, by_name, unavailable)

        # primary key + unique constraints
        kc = await self._rows_soft(
            f"SELECT tc.constraint_name AS cn, tc.constraint_type AS ct, kcu.column_name AS col, kcu.ordinal_position AS p "
            f"FROM information_schema.table_constraints tc "
            f"JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name "
            f"AND kcu.table_schema = tc.table_schema AND kcu.table_name = tc.table_name "
            f"WHERE tc.table_schema = '{_lit(database)}' AND tc.table_name = '{_lit(name)}' "
            f"AND tc.constraint_type IN ('PRIMARY KEY','UNIQUE') ORDER BY cn, p", "indexes", unavailable)
        groups: Dict[str, IndexInfo] = {}
        for r in kc:
            cn, ct = _s(r.get("cn")), _s(r.get("ct")).upper()
            g = groups.setdefault(cn, IndexInfo(name=cn, kind="primary_key" if "PRIMARY" in ct else "unique",
                                                columns=[], unique=True))
            col = _s(r.get("col"))
            g.columns.append(col)
            c = by_name.get(col.lower())
            if c is not None:
                c.is_unique = True
                if "PRIMARY" in ct:
                    c.is_primary = True
        detail.indexes = list(groups.values())

        # foreign keys, both directions
        if self.driver == "duckdb":
            # DuckDB's constraint_column_usage lists a foreign key's OWN columns, so the
            # PostgreSQL join names the child as its own parent; duckdb_constraints()
            # carries the referenced table and columns directly.
            fk_sql = (
                "SELECT constraint_name AS cn, schema_name AS cdb, table_name AS ct, "
                "unnest(constraint_column_names) AS cc, schema_name AS pdb, referenced_table AS pt, "
                "unnest(referenced_column_names) AS pc FROM duckdb_constraints() "
                "WHERE constraint_type = 'FOREIGN KEY' AND database_name = current_database() AND ({where}) "
                "ORDER BY cn")
            out_w = f"schema_name = '{_lit(database)}' AND table_name = '{_lit(name)}'"
            in_w = f"schema_name = '{_lit(database)}' AND referenced_table = '{_lit(name)}'"
        elif self.driver == "postgresql":
            fk_sql = (
                "SELECT tc.constraint_name AS cn, kcu.table_schema AS cdb, kcu.table_name AS ct, kcu.column_name AS cc, "
                "ccu.table_schema AS pdb, ccu.table_name AS pt, ccu.column_name AS pc "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name "
                "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name "
                "WHERE tc.constraint_type = 'FOREIGN KEY' AND ({where}) ORDER BY cn, kcu.ordinal_position")
            out_w = f"kcu.table_schema = '{_lit(database)}' AND kcu.table_name = '{_lit(name)}'"
            in_w = f"ccu.table_schema = '{_lit(database)}' AND ccu.table_name = '{_lit(name)}'"
        else:
            fk_sql = (
                "SELECT constraint_name AS cn, table_schema AS cdb, table_name AS ct, column_name AS cc, "
                "referenced_table_schema AS pdb, referenced_table_name AS pt, referenced_column_name AS pc "
                "FROM information_schema.key_column_usage WHERE referenced_table_name IS NOT NULL AND ({where}) "
                "ORDER BY cn, ordinal_position")
            out_w = f"table_schema = '{_lit(database)}' AND table_name = '{_lit(name)}'"
            in_w = f"referenced_table_schema = '{_lit(database)}' AND referenced_table_name = '{_lit(name)}'"
        detail.foreign_keys_out = self._group(await self._rows_soft(fk_sql.format(where=out_w), "foreign keys", unavailable))
        detail.foreign_keys_in = self._group(await self._rows_soft(fk_sql.format(where=in_w), "referencing keys", unavailable))

        if info.kind == "view":
            vt = await self._rows_soft(
                f"SELECT view_definition AS v FROM information_schema.views "
                f"WHERE table_schema = '{_lit(database)}' AND table_name = '{_lit(name)}'", "view text", unavailable)
            detail.view_text = _s(vt[0].get("v")) if vt else ""
        return detail

    async def _read_comments(self, database: str, name: str, info: ObjectInfo,
                             by_name: Dict[str, ColumnInfo], unavailable: List[str]) -> None:
        """Table and column comments. They are more than labels: the personal-data
        classifier reads a column's comment, so a reader that advertised comments and
        fetched none would leave a column documented as personal data unmasked."""
        db, nm = _lit(database), _lit(name)
        if self.driver == "duckdb":
            col_sql = (f"SELECT column_name AS c, comment AS cm FROM duckdb_columns() "
                       f"WHERE database_name = current_database() AND schema_name = '{db}' AND table_name = '{nm}'")
            obj_sql = (f"SELECT comment AS cm FROM duckdb_tables() WHERE database_name = current_database() "
                       f"AND schema_name = '{db}' AND table_name = '{nm}' UNION ALL "
                       f"SELECT comment AS cm FROM duckdb_views() WHERE database_name = current_database() "
                       f"AND schema_name = '{db}' AND view_name = '{nm}'")
        else:  # postgresql
            base = (f"FROM pg_catalog.pg_class cl JOIN pg_catalog.pg_namespace n ON n.oid = cl.relnamespace "
                    f"WHERE n.nspname = '{db}' AND cl.relname = '{nm}'")
            col_sql = (f"SELECT a.attname AS c, col_description(a.attrelid, a.attnum) AS cm "
                       f"FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class cl ON cl.oid = a.attrelid "
                       f"JOIN pg_catalog.pg_namespace n ON n.oid = cl.relnamespace "
                       f"WHERE n.nspname = '{db}' AND cl.relname = '{nm}' AND a.attnum > 0 AND NOT a.attisdropped")
            obj_sql = f"SELECT obj_description(cl.oid, 'pg_class') AS cm {base}"
        for r in await self._rows_soft(col_sql, "comments", unavailable):
            c = by_name.get(_s(r.get("c")).lower())
            if c is not None and _s(r.get("cm")):
                c.comment = _s(r.get("cm"))
        obj = await self._rows_soft(obj_sql, "table comment",
                                    None if "comments" in unavailable else unavailable)
        if obj and _s(obj[0].get("cm")):
            info.comment = _s(obj[0].get("cm"))

    @staticmethod
    def _group(rows: List[Dict[str, Any]]) -> List[ForeignKey]:
        groups: Dict[str, ForeignKey] = {}
        for r in rows:
            cn = _s(r.get("cn"))
            fk = groups.get(cn)
            if fk is None:
                fk = ForeignKey(child_database=_s(r.get("cdb")), child_table=_s(r.get("ct")), child_columns=[],
                                parent_database=_s(r.get("pdb")), parent_table=_s(r.get("pt")), parent_columns=[],
                                name=cn, reason="declared foreign key")
                groups[cn] = fk
            fk.child_columns.append(_s(r.get("cc")))
            fk.parent_columns.append(_s(r.get("pc")))
        return list(groups.values())

    # ── data ────────────────────────────────────────────────────────────────────

    def _qualified(self, database: str, name: str) -> str:
        return self._q(name) if self._reader == "sqlite" else f"{self._q(database)}.{self._q(name)}"

    async def preview(self, database: str, name: str, limit: int = 50,
                      columns: Optional[List[str]] = None) -> PreviewResult:
        limit = max(1, min(int(limit or 50), 500))
        cols = ", ".join(self._q(c) for c in columns) if columns else "*"
        sql = single_select_guard(f"SELECT {cols} FROM {self._qualified(database, name)} LIMIT {limit}")
        c, rows = await self._run(self._creds, sql, limit)
        return PreviewResult(columns=c, rows=rows, truncated=len(rows) >= limit, sql=sql)

    async def run_select(self, sql: str, max_rows: int):
        """An agent's statement: exactly one SELECT (the structural guard), bounded, on a
        connection the database itself holds read-only where the driver supports it."""
        from .service import SQLGLOT_DIALECT
        body = single_select_guard(sql, SQLGLOT_DIALECT.get(self.driver, self.driver), allow_explain=True)
        return await self._run(self._creds, body, max_rows, readonly=True)

    async def row_count(self, database: str, name: str) -> Optional[int]:
        sql = single_select_guard(f"SELECT COUNT(*) AS n FROM {self._qualified(database, name)}")
        _c, rows = await self._run(self._creds, sql, 1)
        return _i(rows[0][0]) if rows and rows[0] else None
