"""Teradata direct catalog provider — reads the data dictionary (``DBC.*``) itself.

This is the RICH path: declared referential integrity (``All_RI_ChildrenV``),
primary/unique/secondary indexes (``IndicesV``), collected statistics with their age
(``StatsV``), sizes (``TableSizeV``), comments and view text (``TablesV``). None of
that is reachable through the ``base_*`` MCP tools, which is why the traditional
connection is a first-class source and not a fallback.

Every read goes through the injected ``runner`` (``sql_runner.run_rows`` in
production, a fake in CI). The CORE reads (databases, objects, columns) raise a
recovery-shaped :class:`CatalogError`; the ENRICHMENT reads (sizes, stats, indexes,
RI, view text) degrade to "not available from this source" and are named in
``ObjectDetail.unavailable`` so the renderer says so instead of rendering a blank.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set

from .base import CatalogError, CatalogProvider
from .capabilities import CatalogCapability as C
from .sql_runner import rows_as_dicts, run_rows, single_select_guard
from .teradata_types import compose_td_type
from .types import (ColumnInfo, DatabaseInfo, ForeignKey, IndexInfo, ObjectDetail,
                    ObjectInfo, PreviewResult)

logger = logging.getLogger("quart.app")

#: Databases that hold the SYSTEM, not the estate. Listed, flagged, hidden by default.
TD_SYSTEM_DBS = frozenset({
    "dbc", "sys_calendar", "syslib", "sysudtlib", "systemfe", "sysspatial", "sysbar",
    "sysadmin", "sysjdbc", "sqlj", "tdstats", "tdqcd", "tdwm", "tdmaps", "tdpuser",
    "tdadmin", "tdbcmgmt", "tdstatsstage", "td_sysfnlib", "td_sysgpl", "td_syssecurity",
    "td_analytics_db", "td_server_db", "td_metric_svc", "td_sysxml", "tdmaps",
    "crashdumps", "dbcmngr", "externalap", "locklogshredder", "pdcrinfo", "pdcradm",
    "pdcrdata", "pdcrstg", "pdcrtpcd", "viewpoint", "val", "all", "default", "public",
    "console", "dr_migrate", "gsstools", "ldap", "mldb", "tdmaps", "tddbadmin",
    "sysusrlib", "sysudtlib", "systemfe", "tdsqlj", "sysudtlib", "tdsysfnlib",
    "sysdba", "dbqlmaint", "syslogtbl",
})

_KIND = {"T": "table", "O": "table", "Q": "table", "V": "view", "M": "macro",
         "P": "procedure", "E": "procedure", "F": "function", "R": "function",
         "J": "join_index", "N": "hash_index", "G": "trigger", "A": "aggregate_function",
         "B": "combined", "D": "join_index", "H": "instance", "I": "join_index",
         "K": "foreign_server", "L": "user_function", "S": "ordered_set", "U": "udt",
         "X": "authorization", "Y": "glop", "Z": "uif"}

_OBJECT_KINDS = ("T", "O", "Q", "V", "M", "P", "E", "F")
_IDENT_RE = re.compile(r'^[^"\x00-\x1f]{1,128}$')


def _lit(s: str) -> str:
    return str(s).replace("'", "''")


def _q(ident: str) -> str:
    if not _IDENT_RE.match(str(ident)):
        raise CatalogError(f"refusing an identifier the dictionary cannot hold: {ident!r}")
    return '"' + str(ident).replace('"', '""') + '"'


def _i(v: Any) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


# ── the dictionary reads (module-level so a gate can assert their shape) ─────────

def sql_databases() -> str:
    return ("SELECT TRIM(DatabaseName) AS db, DBKind, TRIM(CommentString) AS cmt, "
            "PermSpace, TRIM(OwnerName) AS owner FROM DBC.DatabasesV ORDER BY 1")


def sql_database_object_counts() -> str:
    kinds = ",".join(f"'{k}'" for k in _OBJECT_KINDS)
    return (f"SELECT TRIM(DatabaseName) AS db, COUNT(*) AS n FROM DBC.TablesV "
            f"WHERE TableKind IN ({kinds}) GROUP BY 1")


def sql_database_sizes() -> str:
    return "SELECT TRIM(DatabaseName) AS db, SUM(CurrentPerm) AS b FROM DBC.TableSizeV GROUP BY 1"


def sql_objects(db: str) -> str:
    kinds = ",".join(f"'{k}'" for k in _OBJECT_KINDS)
    return (f"SELECT TRIM(TableName) AS name, TableKind AS kind, TRIM(CommentString) AS cmt, "
            f"CreateTimestamp AS created, LastAlterTimestamp AS altered "
            f"FROM DBC.TablesV WHERE DatabaseName = '{_lit(db)}' AND TableKind IN ({kinds}) ORDER BY 1")


def sql_object_sizes(db: str) -> str:
    return (f"SELECT TRIM(TableName) AS name, SUM(CurrentPerm) AS b FROM DBC.TableSizeV "
            f"WHERE DatabaseName = '{_lit(db)}' GROUP BY 1")


def sql_object_stats_rowcounts(db: str) -> str:
    return (f"SELECT TRIM(TableName) AS name, MAX(RowCount) AS rc, MAX(LastCollectTimestamp) AS ts "
            f"FROM DBC.StatsV WHERE DatabaseName = '{_lit(db)}' GROUP BY 1")


def sql_object_column_counts(db: str) -> str:
    return (f"SELECT TRIM(TableName) AS name, COUNT(*) AS n FROM DBC.ColumnsV "
            f"WHERE DatabaseName = '{_lit(db)}' GROUP BY 1")


def sql_columns(db: str, name: str) -> str:
    return (f"SELECT TRIM(ColumnName) AS columnname, ColumnType, ColumnLength, DecimalTotalDigits, "
            f"DecimalFractionalDigits, Nullable, ColumnId, TRIM(DefaultValue) AS defaultvalue, "
            f"TRIM(CommentString) AS cmt, IdColType, TRIM(ColumnFormat) AS fmt "
            f"FROM DBC.ColumnsV WHERE DatabaseName = '{_lit(db)}' AND TableName = '{_lit(name)}' "
            f"ORDER BY ColumnId")


def sql_indexes(db: str, name: str) -> str:
    return (f"SELECT TRIM(IndexName) AS iname, IndexType, UniqueFlag, TRIM(ColumnName) AS col, "
            f"ColumnPosition, IndexNumber FROM DBC.IndicesV "
            f"WHERE DatabaseName = '{_lit(db)}' AND TableName = '{_lit(name)}' "
            f"ORDER BY IndexNumber, ColumnPosition")


def sql_fks_out(db: str, name: str) -> str:
    return (f"SELECT IndexId AS idx_id, TRIM(IndexName) AS idx_name, TRIM(ChildDB) AS child_db, TRIM(ChildTable) AS child_tbl, "
            f"TRIM(ChildKeyColumn) AS child_col, TRIM(ParentDB) AS parent_db, TRIM(ParentTable) AS parent_tbl, "
            f"TRIM(ParentKeyColumn) AS parent_col FROM DBC.All_RI_ChildrenV "
            f"WHERE ChildDB = '{_lit(db)}' AND ChildTable = '{_lit(name)}' ORDER BY IndexId")


def sql_fks_in(db: str, name: str) -> str:
    return (f"SELECT IndexId AS idx_id, TRIM(IndexName) AS idx_name, TRIM(ChildDB) AS child_db, TRIM(ChildTable) AS child_tbl, "
            f"TRIM(ChildKeyColumn) AS child_col, TRIM(ParentDB) AS parent_db, TRIM(ParentTable) AS parent_tbl, "
            f"TRIM(ParentKeyColumn) AS parent_col FROM DBC.All_RI_ChildrenV "
            f"WHERE ParentDB = '{_lit(db)}' AND ParentTable = '{_lit(name)}' ORDER BY ChildDB, ChildTable, IndexId")


def sql_column_stats(db: str, name: str) -> str:
    return (f"SELECT TRIM(ColumnName) AS col, UniqueValueCount, NullCount, RowCount, "
            f"LastCollectTimestamp AS ts FROM DBC.StatsV "
            f"WHERE DatabaseName = '{_lit(db)}' AND TableName = '{_lit(name)}'")


def sql_view_text(db: str, name: str) -> str:
    return (f"SELECT RequestText FROM DBC.TablesV "
            f"WHERE DatabaseName = '{_lit(db)}' AND TableName = '{_lit(name)}'")


def sql_preview(db: str, name: str, limit: int, columns: Optional[List[str]] = None) -> str:
    cols = ", ".join(_q(c) for c in columns) if columns else "*"
    return f"SELECT TOP {int(limit)} {cols} FROM {_q(db)}.{_q(name)}"


def sql_search_objects(term: str, limit: int) -> str:
    kinds = ",".join(f"'{k}'" for k in _OBJECT_KINDS)
    return (f"SELECT TOP {int(limit)} TRIM(DatabaseName) AS db, TRIM(TableName) AS name, TableKind AS kind, "
            f"TRIM(CommentString) AS cmt FROM DBC.TablesV WHERE TableKind IN ({kinds}) "
            f"AND (UPPER(TableName) LIKE '%{_lit(term).upper()}%' OR UPPER(CommentString) LIKE '%{_lit(term).upper()}%') "
            f"ORDER BY DatabaseName, TableName")


def sql_search_columns(term: str, limit: int) -> str:
    return (f"SELECT TOP {int(limit)} TRIM(DatabaseName) AS db, TRIM(TableName) AS name, "
            f"TRIM(ColumnName) AS columnname, ColumnType, ColumnLength, DecimalTotalDigits, "
            f"DecimalFractionalDigits, TRIM(CommentString) AS cmt FROM DBC.ColumnsV "
            f"WHERE UPPER(ColumnName) LIKE '%{_lit(term).upper()}%' ORDER BY DatabaseName, TableName, ColumnId")


def sql_row_count(db: str, name: str) -> str:
    return f"SELECT COUNT(*) AS n FROM {_q(db)}.{_q(name)}"


async def _empty():
    return []


def _group_fks(rows: List[Dict[str, Any]]) -> List[ForeignKey]:
    groups: Dict[Any, ForeignKey] = {}
    for r in rows:
        key = (_s(r.get("child_db")), _s(r.get("child_tbl")), r.get("idx_id"), _s(r.get("parent_db")), _s(r.get("parent_tbl")))
        fk = groups.get(key)
        if fk is None:
            fk = ForeignKey(child_database=key[0], child_table=key[1], child_columns=[],
                            parent_database=key[3], parent_table=key[4], parent_columns=[],
                            name=_s(r.get("idx_name")), declared=True, confidence=1.0,
                            reason="declared referential integrity")
            groups[key] = fk
        fk.child_columns.append(_s(r.get("child_col")))
        fk.parent_columns.append(_s(r.get("parent_col")))
    return list(groups.values())


class TeradataCatalogProvider(CatalogProvider):
    provider_kind = "direct"

    def __init__(self, source_id: str, label: str, creds: Dict[str, Any],
                 runner: Callable = run_rows):
        self._id, self._label, self._creds, self._run = source_id, label, dict(creds), runner

    @property
    def source_id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    def capabilities(self) -> Set[C]:
        return {C.LIST_DATABASES, C.LIST_OBJECTS, C.COLUMNS, C.COMMENTS, C.FOREIGN_KEYS,
                C.INDEXES, C.STATISTICS, C.SIZES, C.VIEW_TEXT, C.ROW_COUNT, C.PREVIEW,
                C.OBJECT_KINDS, C.SEARCH}

    async def _rows(self, sql: str) -> List[Dict[str, Any]]:
        cols, rows = await self._run(self._creds, sql)
        return rows_as_dicts(cols, rows)

    async def _rows_soft(self, sql: str, what: str, unavailable: Optional[List[str]] = None):
        """An enrichment read: a failure is NAMED, never fatal."""
        try:
            return await self._rows(sql)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[dbcatalog/teradata] %s unavailable: %s", what, exc)
            if unavailable is not None:
                unavailable.append(what)
            return []

    # ── databases ───────────────────────────────────────────────────────────────

    async def list_databases(self) -> List[DatabaseInfo]:
        rows, crows, srows = await asyncio.gather(
            self._rows(sql_databases()),
            self._rows_soft(sql_database_object_counts(), "object counts"),
            self._rows_soft(sql_database_sizes(), "sizes"))
        counts = {r["db"]: _i(r.get("n")) for r in crows}
        sizes = {r["db"]: _i(r.get("b")) for r in srows}
        out: List[DatabaseInfo] = []
        for r in rows:
            name = _s(r.get("db"))
            if not name:
                continue
            low = name.lower()
            out.append(DatabaseInfo(
                name=name, kind="user" if _s(r.get("dbkind")).upper() == "U" else "database",
                comment=_s(r.get("cmt")), object_count=counts.get(name),
                size_bytes=sizes.get(name), owner=_s(r.get("owner")),
                is_system=low in TD_SYSTEM_DBS or low.startswith(("td_", "sys", "tdwm", "dbc")),
            ))
        return out

    # ── objects ─────────────────────────────────────────────────────────────────

    async def list_objects(self, database: str) -> List[ObjectInfo]:
        rows, srows, strows, cnrows = await asyncio.gather(
            self._rows(sql_objects(database)),
            self._rows_soft(sql_object_sizes(database), "sizes"),
            self._rows_soft(sql_object_stats_rowcounts(database), "statistics"),
            self._rows_soft(sql_object_column_counts(database), "column counts"))
        sizes = {r["name"]: _i(r.get("b")) for r in srows}
        stats = {r["name"]: r for r in strows}
        ccount = {r["name"]: _i(r.get("n")) for r in cnrows}
        out: List[ObjectInfo] = []
        for r in rows:
            name = _s(r.get("name"))
            if not name:
                continue
            st = stats.get(name) or {}
            rc = _i(st.get("rc"))
            out.append(ObjectInfo(
                database=database, name=name, kind=_KIND.get(_s(r.get("kind")).upper(), "other"),
                comment=_s(r.get("cmt")), size_bytes=sizes.get(name),
                row_count=rc, row_count_source="statistics" if rc is not None else "",
                created_at=_s(r.get("created")), altered_at=_s(r.get("altered")),
                column_count=ccount.get(name),
                extra={"stats_collected_at": _s(st.get("ts"))} if st else {},
            ))
        return out

    # ── one object, everything the dictionary knows ────────────────────────────

    async def describe_object(self, database: str, name: str) -> ObjectDetail:
        objs = {o.name.lower(): o for o in await self.list_objects(database)}
        info = objs.get(name.lower())
        if info is None:
            raise CatalogError(f"{database}.{name} is not in the dictionary — the object may have "
                               f"been dropped or renamed; refresh the catalog.")
        name = info.name
        unavailable: List[str] = []
        detail = ObjectDetail(info=info, unavailable=unavailable)

        # columns (core) + every enrichment read, concurrently
        crow, idx_rows, fko_rows, fki_rows, st_rows, vt_rows = await asyncio.gather(
            self._rows(sql_columns(database, name)),
            self._rows_soft(sql_indexes(database, name), "indexes", unavailable),
            self._rows_soft(sql_fks_out(database, name), "foreign keys", unavailable),
            self._rows_soft(sql_fks_in(database, name), "referencing keys", unavailable),
            self._rows_soft(sql_column_stats(database, name), "statistics", unavailable),
            self._rows_soft(sql_view_text(database, name), "view text", unavailable)
            if info.kind in ("view", "macro") else _empty())
        cols: List[ColumnInfo] = []
        for r in crow:
            cname = _s(r.get("columnname"))
            if not cname:
                continue
            dtype = compose_td_type(r) if _s(r.get("columntype")) else ""
            cols.append(ColumnInfo(
                name=cname, data_type=dtype,
                nullable=(None if r.get("nullable") in (None, "") else _s(r.get("nullable")).upper() == "Y"),
                position=_i(r.get("columnid")), comment=_s(r.get("cmt")),
                default=_s(r.get("defaultvalue")),
                is_identity=bool(_s(r.get("idcoltype"))),
            ))
        if info.kind == "view" and cols and not any(c.data_type for c in cols):
            unavailable.append("column types (the dictionary does not store a view's column types)")
        detail.columns = cols
        by_name = {c.name.lower(): c for c in cols}

        # indexes
        groups: Dict[Any, IndexInfo] = {}
        for r in idx_rows:
            key = r.get("indexnumber")
            it = _s(r.get("indextype")).upper()
            kind = {"P": "primary", "Q": "primary", "K": "primary_key", "U": "unique",
                    "S": "secondary", "J": "join_index", "N": "hash_index"}.get(it, "other")
            uniq = _s(r.get("uniqueflag")).upper() == "Y"
            g = groups.get(key)
            if g is None:
                g = IndexInfo(name=_s(r.get("iname")) or kind, kind=kind, columns=[], unique=uniq)
                groups[key] = g
            col = _s(r.get("col"))
            if col:
                g.columns.append(col)
                c = by_name.get(col.lower())
                if c is not None:
                    if kind in ("primary", "primary_key"):
                        c.is_primary = True
                    if uniq:
                        c.is_unique = True
        detail.indexes = list(groups.values())

        # referential integrity, both directions
        detail.foreign_keys_out = _group_fks(fko_rows)
        detail.foreign_keys_in = _group_fks(fki_rows)

        # statistics
        latest = ""
        for r in st_rows:
            c = by_name.get(_s(r.get("col")).lower())
            ts = _s(r.get("ts"))
            if ts > latest:
                latest = ts
            if c is None:
                continue
            c.stats = {k: v for k, v in {
                "unique_values": _i(r.get("uniquevaluecount")),
                "null_count": _i(r.get("nullcount")),
                "row_count": _i(r.get("rowcount")),
                "collected_at": ts,
            }.items() if v not in (None, "")}
        detail.stats_collected_at = latest or _s(info.extra.get("stats_collected_at"))

        # view text
        if vt_rows:
            detail.view_text = _s(vt_rows[0].get("requesttext"))
        return detail

    # ── data ────────────────────────────────────────────────────────────────────

    async def preview(self, database: str, name: str, limit: int = 50,
                      columns: Optional[List[str]] = None) -> PreviewResult:
        limit = max(1, min(int(limit or 50), 500))
        sql = single_select_guard(sql_preview(database, name, limit, columns))
        cols, rows = await self._run(self._creds, sql, limit)
        return PreviewResult(columns=cols, rows=rows, truncated=len(rows) >= limit, sql=sql)

    async def search_objects(self, term: str, limit: int = 200) -> List[ObjectInfo]:
        term = _s(term)[:64]
        if len(term) < 2:
            return []
        rows = await self._rows_soft(sql_search_objects(term, limit), "search")
        return [ObjectInfo(database=_s(r.get("db")), name=_s(r.get("name")),
                           kind=_KIND.get(_s(r.get("kind")).upper(), "other"), comment=_s(r.get("cmt")))
                for r in rows if _s(r.get("name"))]

    async def search_columns(self, term: str, limit: int = 300) -> List[dict]:
        term = _s(term)[:64]
        if len(term) < 2:
            return []
        rows = await self._rows_soft(sql_search_columns(term, limit), "search")
        return [{"database": _s(r.get("db")), "object": _s(r.get("name")), "column": _s(r.get("columnname")),
                 "data_type": compose_td_type(r) if _s(r.get("columntype")) else "", "comment": _s(r.get("cmt"))}
                for r in rows if _s(r.get("columnname"))]

    async def run_select(self, sql: str, max_rows: int):
        """An agent's statement: exactly one SELECT (the structural guard), bounded, on a
        connection the database itself holds read-only where the driver supports it."""
        body = single_select_guard(sql, allow_explain=True)
        return await self._run(self._creds, body, max_rows, readonly=True)

    async def existing_functions(self, names) -> Optional[set]:
        from trusted_data_agent.dbcatalog.teradata_types import existing_functions_sql
        sql = existing_functions_sql(names)
        if not sql:
            return set()
        _cols, rows = await self._run(self._creds, sql, 1000)
        return {str(r[0]).strip().upper() for r in rows if r and r[0] is not None}

    async def row_count(self, database: str, name: str) -> Optional[int]:
        sql = single_select_guard(sql_row_count(database, name))
        cols, rows = await self._run(self._creds, sql, 1)
        return _i(rows[0][0]) if rows and rows[0] else None
