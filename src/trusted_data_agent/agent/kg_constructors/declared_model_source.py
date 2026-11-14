"""
declared_model_source — read an ISO/IEC 11179 metadata registry (+ the data
dictionary) from Teradata into a :class:`DeclaredModelSnapshot`.

Everything DB-touching lives here so :mod:`declared_model_walker` stays pure and
unit-testable.  Mirrors the ``andp_semantic_source`` / ``andp_semantic_walker`` split.

The registry is five tables in a catalog database (default ``Policing_Core_Semantic``);
after reading them we read ``DBC.ColumnsV`` for every distinct ``deployed_object`` an
entity binds to, which makes the type rule enforceable.  ``DBC.ColumnsV`` covers VIEWS
as well as tables — the reason the zero-copy NIBRS view layer is type-correct.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .walker_util import _s
from .declared_model_walker import DeclaredModelSnapshot

logger = logging.getLogger("quart.app")

#: The 11179 registry tables the walker consumes.
CATALOG_TABLES = (
    "object_class",
    "data_element",
    "value_domain",
    "permissible_value",
    "association",
)

#: DBC.ColumnsV ColumnType code → base type name — ONE definition, shared with the
#: Database Navigator's dictionary provider (`dbcatalog.teradata_types`).
from trusted_data_agent.dbcatalog.teradata_types import TD_TYPES as _TD_TYPES  # noqa: E402
from trusted_data_agent.dbcatalog.teradata_types import compose_td_type as _compose_td_type_shared  # noqa: E402


class SourceError(RuntimeError):
    """Raised when the registry cannot be located or read."""


def _rows(cursor, sql: str) -> List[Dict[str, Any]]:
    """Run ``sql`` and return rows as dicts with lower-cased, trimmed keys."""
    cursor.execute(sql)
    cols = [d[0].strip().lower() for d in cursor.description]
    return [{c: v for c, v in zip(cols, row)} for row in cursor.fetchall()]


def _table_exists(cursor, database: str, table: str) -> bool:
    safe_db = database.replace("'", "''")
    safe_tb = table.replace("'", "''")
    rows = _rows(
        cursor,
        f"SELECT 1 AS present FROM DBC.TablesV "
        f"WHERE DatabaseName = '{safe_db}' AND TableName = '{safe_tb}'",
    )
    return bool(rows)


def _read_catalog(cursor, catalog_database: str, table: str) -> List[Dict[str, Any]]:
    """Read one registry table, degrading to [] (never raising) when it is absent."""
    if not _table_exists(cursor, catalog_database, table):
        logger.warning("declared_model: registry table %s.%s not found — skipped",
                       catalog_database, table)
        return []
    try:
        return _rows(cursor, f"SELECT * FROM {catalog_database}.{table}")
    except Exception as exc:
        logger.warning("declared_model: could not read %s.%s — %s",
                       catalog_database, table, exc)
        return []


def _compose_td_type(row: Dict[str, Any]) -> str:
    """Compose a readable deployed type from a DBC.ColumnsV row (delegates to the
    shared definition in `dbcatalog.teradata_types`)."""
    return _compose_td_type_shared(row)


def _deployed_objects(object_classes: Sequence[Dict[str, Any]],
                      default_db: str) -> List[Tuple[str, str]]:
    """Distinct (database, object) an entity binds to, in first-seen order."""
    seen: "Dict[Tuple[str, str], None]" = {}
    for row in object_classes:
        if not isinstance(row, dict):
            continue
        parts = [p.strip().strip('"') for p in _s(row.get("deployed_object")).split(".")
                 if p.strip()]
        if not parts:
            continue
        db, obj = (parts[-2], parts[-1]) if len(parts) >= 2 else (default_db, parts[0])
        if db and obj:
            seen.setdefault((db, obj), None)
    return list(seen.keys())


#: HELP COLUMN result column -> the DBC.ColumnsV column name ``compose_td_type`` reads.
_HELP_COLUMN_MAP = {
    "column name": "columnname",
    "type": "columntype",
    "max length": "columnlength",
    "decimal total digits": "decimaltotaldigits",
    "decimal fractional digits": "decimalfractionaldigits",
    "nullable": "nullable",
}


def help_rows_to_dictionary(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Reshape ``HELP COLUMN db.obj.*`` rows into the ``DBC.ColumnsV`` shape.

    HELP COLUMN pads every text field to its display width (``'CV  '``, a 90-char
    column name), so values are trimmed — and its legacy ``Column Name`` field is
    TRUNCATED to 30 characters (``exceptional_clearance_date_tex``), so the full
    identifier is taken from ``Column Dictionary Name`` (or ``Column SQL Name``)
    when the row carries it. Pure; the unit-test target for the view fallback below.
    """
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d: Dict[str, Any] = {}
        low = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v)
               for k, v in r.items()}
        for k, v in low.items():
            key = _HELP_COLUMN_MAP.get(k)
            if key:
                d[key] = v
        full = low.get("column dictionary name") or low.get("column sql name")
        if full:
            d["columnname"] = full
        if d.get("columnname"):
            out.append(d)
    return out


def _view_column_rows(cursor, db: str, obj: str) -> List[Dict[str, Any]]:
    """The resolved column types of a VIEW.

    ``DBC.ColumnsV`` carries NULL ``ColumnType`` for view columns unless the QVCI
    feature is enabled (it is off by default — Error 9719 on ``DBC.ColumnsQV``), so
    a view read through it alone types every column ``UNKNOWN`` and counts every one
    as a type mismatch. ``HELP COLUMN db.view.*`` resolves the view's types on any
    release, so it is the fallback whenever the dictionary read came back untyped.
    """
    safe = f'"{db}"."{obj}"'
    return help_rows_to_dictionary(_rows(cursor, f"HELP COLUMN {safe}.*"))


def fetch_physical_columns(cursor, objects: Sequence[Tuple[str, str]]
                           ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """Read the data dictionary for each deployed object → the type-rule map.

    ``{(db, object, column) lower-cased: {"datatype", "nullable", "column": realcase}}``.
    Tables come from ``DBC.ColumnsV``; a VIEW (whose ColumnsV rows carry no type)
    is resolved through ``HELP COLUMN`` — see :func:`_view_column_rows`. An object
    that cannot be read degrades (logged), never raising into a build.
    """
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for db, obj in objects:
        safe_db, safe_ob = db.replace("'", "''"), obj.replace("'", "''")
        try:
            rows = _rows(
                cursor,
                "SELECT ColumnName, ColumnType, ColumnLength, DecimalTotalDigits, "
                "DecimalFractionalDigits, Nullable FROM DBC.ColumnsV "
                f"WHERE DatabaseName = '{safe_db}' AND TableName = '{safe_ob}'",
            )
        except Exception as exc:
            logger.warning("declared_model: DBC.ColumnsV read failed for %s.%s — %s",
                           db, obj, exc)
            continue
        if rows and not any(_s(r.get("columntype")) for r in rows):
            try:
                rows = _view_column_rows(cursor, db, obj)
            except Exception as exc:
                logger.warning("declared_model: HELP COLUMN failed for view %s.%s — "
                               "its columns stay untyped: %s", db, obj, exc)
        for row in rows:
            col = _s(row.get("columnname"))
            if not col:
                continue
            out[(db.lower(), obj.lower(), col.lower())] = {
                "datatype": _compose_td_type(row),
                "nullable": _s(row.get("nullable")).upper() != "N",
                "column": col,
            }
    return out


def fetch_snapshot(cursor, catalog_database: str, model_name: str,
                   core_database: Optional[str] = None,
                   source_detail: str = "") -> DeclaredModelSnapshot:
    """Read the whole ISO/IEC 11179 registry + the data dictionary."""
    catalog_database = _s(catalog_database)
    if not catalog_database:
        raise SourceError("catalog_database is required (where the 11179 tables live)")

    tables = {t: _read_catalog(cursor, catalog_database, t) for t in CATALOG_TABLES}
    if not tables["object_class"]:
        raise SourceError(
            f"no object_class rows in {catalog_database} — is the registry seeded?")

    default_db = _s(core_database) or catalog_database.rsplit("_", 1)[0]
    physical = fetch_physical_columns(
        cursor, _deployed_objects(tables["object_class"], default_db))

    return DeclaredModelSnapshot(
        model_name=_s(model_name),
        catalog_database=catalog_database,
        object_classes=tables["object_class"],
        data_elements=tables["data_element"],
        value_domains=tables["value_domain"],
        permissible_values=tables["permissible_value"],
        associations=tables["association"],
        physical_columns=physical,
        source_detail=source_detail,
    )


def list_registries(cursor) -> List[Dict[str, Any]]:
    """Every ISO/IEC 11179 registry visible on this connection (for a picker).

    A registry is a database holding ALL five catalog tables — a database with only
    some of them is not a registry the walker could read, so it is not offered.
    Found by scanning the dictionary, exactly as ``andp_semantic_source.list_products``
    finds a product: deterministic, so the picker shows what is actually deployed.

    Each entry carries what the constructor needs to build without further typing:
    the catalog database, the core database its entities deploy into (read from the
    registry's own ``deployed_object`` column, so it is a fact rather than a naming
    convention — the ``<catalog>_Semantic`` -> ``<catalog>`` rule is the fallback for an
    empty registry), the entity count and the subject areas.
    """
    names = ", ".join(f"'{t}'" for t in CATALOG_TABLES)
    rows = _rows(
        cursor,
        "SELECT DatabaseName, COUNT(DISTINCT TableName) AS n FROM DBC.TablesV "
        f"WHERE TableName IN ({names}) GROUP BY DatabaseName "
        f"HAVING COUNT(DISTINCT TableName) = {len(CATALOG_TABLES)} ORDER BY DatabaseName",
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        cat = _s(r.get("databasename"))
        if not cat:
            continue
        entry: Dict[str, Any] = {
            "catalog_database": cat,
            "core_database": cat.rsplit("_", 1)[0] if "_" in cat else cat,
            "entities": 0,
            "subject_areas": [],
        }
        try:
            classes = _rows(cursor, f"SELECT deployed_object, subject_area FROM {cat}.object_class")
        except Exception as exc:  # a registry that cannot be read is still listed, honestly
            logger.warning("declared_model: could not read %s.object_class — %s", cat, exc)
            out.append(entry)
            continue
        entry["entities"] = len(classes)
        entry["subject_areas"] = sorted({_s(c.get("subject_area")).lower()
                                         for c in classes if _s(c.get("subject_area"))})
        deployed = [_s(c.get("deployed_object")) for c in classes]
        dbs = [d.rsplit(".", 1)[0] for d in deployed if "." in d]
        if dbs:
            # the database the MOST entities deploy into is the model's anchor
            entry["core_database"] = max(set(dbs), key=dbs.count)
        out.append(entry)
    return out
