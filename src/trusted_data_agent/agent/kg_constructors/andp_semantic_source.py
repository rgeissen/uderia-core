"""
andp_semantic_source — read a deployed **AI-Native Data Product**'s semantic module
(and the data dictionary) into a :class:`SemanticSnapshot`.

Everything DB-touching lives here so
:mod:`~trusted_data_agent.agent.kg_constructors.andp_semantic_walker` stays pure
and unit-testable.  The split mirrors ``industry_model_source`` / ``industry_model_walker``.

DISCOVERY follows the ANDP consumer contract, in the standard's own order:

    1. product registry  — ``governance.data_product_registry`` (preferred)
    2. convention        — ``{Product}_Semantic``
    3. dictionary scan   — any database holding a ``data_product_map``

Each fallback is *reported* on the snapshot (``discovery_method``) rather than
silently taken, because "the registry was missing" is a finding about the estate,
not an implementation detail.

CATALOG DRIFT
-------------
Products built to the same standard differ in catalog columns (the reference
deployment has ``ontology_iri`` on one product and not the other, ``primary_tables``
on one ``data_product_map`` and not the other).  We therefore ``SELECT *`` and
lower-case the keys, letting the walker read through aliases — instead of a
hardcoded column list that breaks on the next product.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .andp_semantic_walker import SemanticSnapshot

logger = logging.getLogger("quart.app")

#: Catalog tables the walker consumes.  A product missing one degrades (the
#: walker simply emits less) rather than failing the whole build.
CATALOG_TABLES = (
    "data_product_map",
    "entity_metadata",
    "column_metadata",
    "table_relationship",
)

#: The product-description assets, and where the standard puts them:
#: ``snapshot field -> (owning module, candidate table names)``.
#: Names are matched case-insensitively across candidates because conformant
#: products differ in casing and pluralisation.
_KNOWLEDGE_TABLES = {
    "business_glossary": ("memory", ("Business_Glossary", "business_glossary")),
    "query_cookbook": ("memory", ("Query_Cookbook", "query_cookbook")),
    "design_decisions": ("memory", ("Design_Decision", "design_decision",
                                    "Design_Decisions", "design_decisions")),
    "module_registry": ("memory", ("Module_Registry", "module_registry")),
    "naming_standards": ("semantic", ("naming_standard", "Naming_Standard",
                                      "naming_standards")),
    "data_lineage": ("observability", ("data_lineage", "Data_Lineage")),
    "quality_metrics": ("observability", ("data_quality_metric",
                                          "Data_Quality_Metric",
                                          "data_quality_metrics")),
}


#: v2 consumer surfaces.  These are read because they change what an agent should
#: DO — which object it may query, and whether the product's own validation says it
#: is fit for analytical use.  The orientation ladder is deliberately NOT ingested:
#: it is a route through the catalog, and for our agent the graph IS that route.
_ACCESS_OBJECT_CANDIDATES = ("access_object",)
_TRUST_GATE_CANDIDATES = ("validation_latest",)


def fetch_access_objects(cursor, semantic_database: str) -> List[Dict[str, Any]]:
    """Which deployed object a consumer should actually query, per entity."""
    real = find_table(cursor, semantic_database, *_ACCESS_OBJECT_CANDIDATES)
    if not real:
        return []
    try:
        rows = _rows(cursor, f"SELECT * FROM {semantic_database}.{real}")
    except Exception as exc:
        logger.warning("ANDP: could not read %s.%s — %s", semantic_database, real, exc)
        return []
    return [r for r in rows if _active(r)]


def fetch_trust_gate(cursor, observability_database: str,
                     product: str) -> Optional[Dict[str, Any]]:
    """The product's own validation verdict, or ``None`` when it publishes none.

    ``None`` is NOT "trusted": a product that has never been validated has made no
    claim, and saying otherwise would manufacture assurance out of an absence.
    """
    if not observability_database:
        return None
    real = find_table(cursor, observability_database, *_TRUST_GATE_CANDIDATES)
    if not real:
        return None
    try:
        rows = _rows(cursor, f"SELECT * FROM {observability_database}.{real}")
    except Exception as exc:
        logger.warning("ANDP: could not read %s.%s — %s",
                       observability_database, real, exc)
        return None
    for row in rows:
        if _s(row.get("product_prefix")).lower() == product.lower():
            return row
    return rows[0] if len(rows) == 1 else None


class SourceError(RuntimeError):
    """Raised when a product's semantic module cannot be located or read."""


def _rows(cursor, sql: str, params: Optional[Sequence] = None) -> List[Dict[str, Any]]:
    """Run ``sql`` and return rows as dicts with lower-cased, trimmed keys."""
    cursor.execute(sql, params) if params else cursor.execute(sql)
    cols = [d[0].strip().lower() for d in cursor.description]
    out: List[Dict[str, Any]] = []
    for row in cursor.fetchall():
        out.append({c: v for c, v in zip(cols, row)})
    return out


def _table_exists(cursor, database: str, table: str) -> bool:
    return find_table(cursor, database, table) is not None


def find_table(cursor, database: str, *candidates: str) -> Optional[str]:
    """Return the real name of the first candidate present in ``database``.

    Matching is case-insensitive because products built to the same standard differ
    in casing (the reference deployment has ``Business_Glossary`` on one product and
    ``business_glossary`` on another).  Returns the *stored* name so subsequent SQL
    quotes what actually exists.
    """
    if not database:
        return None
    safe_db = database.replace("'", "''")
    wanted = {c.strip().lower(): c for c in candidates if c and c.strip()}
    if not wanted:
        return None
    rows = _rows(
        cursor,
        f"SELECT TableName FROM DBC.TablesV WHERE DatabaseName = '{safe_db}'",
    )
    present = {}
    for r in rows:
        name = str(r.get("tablename") or "").strip()
        if name:
            present.setdefault(name.lower(), name)
    for key in wanted:
        if key in present:
            return present[key]
    return None


def discover_semantic_database(cursor, product: str) -> Tuple[str, str]:
    """Locate a product's semantic database.

    Returns:
        (database_name, discovery_method) — method ∈ {"registry", "convention",
        "dictionary_scan"}.

    Raises:
        SourceError: if no semantic module can be found for ``product``.
    """
    # 1 — the standard's preferred entry point
    try:
        reg = _rows(
            cursor,
            "SELECT semantic_database FROM governance.data_product_registry "
            "WHERE is_active = 1 AND is_deleted = 0 AND product_name = ?",
            (product,),   # bound, never interpolated — the product name is request input
        )
        if reg and str(reg[0].get("semantic_database") or "").strip():
            return str(reg[0]["semantic_database"]).strip(), "registry"
    except Exception:
        # No registry deployed on this estate — an expected, reportable state.
        logger.debug("ANDP: no data_product_registry; falling back to convention")

    # 2 — naming convention
    candidate = f"{product}_Semantic"
    if _table_exists(cursor, candidate, "data_product_map"):
        return candidate, "convention"

    # 3 — dictionary scan for any database carrying the catalog
    hits = _rows(
        cursor,
        "SELECT DatabaseName FROM DBC.TablesV "
        "WHERE TableName = 'data_product_map' AND DatabaseName LIKE ?",
        (f"{product}%",),
    )
    if len(hits) == 1:
        return str(hits[0]["databasename"]).strip(), "dictionary_scan"
    if len(hits) > 1:
        names = sorted(str(h["databasename"]).strip() for h in hits)
        raise SourceError(
            f"Ambiguous semantic module for '{product}': {names}. "
            "Pass semantic_database explicitly."
        )
    raise SourceError(
        f"No semantic module found for product '{product}'. Looked for a registry "
        f"row, '{candidate}.data_product_map', and any '{product}%' database "
        "carrying a data_product_map."
    )


def fetch_physical_columns(cursor, databases: Sequence[str]
                           ) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """Read deployed column types from the data dictionary.

    **This is the authority for types** — a product's catalog declares types that
    drift from what was actually deployed (see the walker's module docstring).
    Keys are lower-cased ``(database, table, column)`` so lookups are
    case-insensitive, which Teradata identifiers require.
    """
    if not databases:
        return {}
    in_list = ", ".join("'" + d.replace("'", "''") + "'" for d in databases)
    rows = _rows(
        cursor,
        "SELECT DatabaseName, TableName, ColumnName, ColumnType, ColumnLength, "
        "DecimalTotalDigits, DecimalFractionalDigits, Nullable "
        f"FROM DBC.ColumnsV WHERE DatabaseName IN ({in_list})",
    )
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for r in rows:
        db = str(r.get("databasename") or "").strip()
        tbl = str(r.get("tablename") or "").strip()
        col = str(r.get("columnname") or "").strip()
        if not (db and tbl and col):
            continue
        out[(db.lower(), tbl.lower(), col.lower())] = {
            "datatype": _render_type(r),
            "nullable": str(r.get("nullable") or "").strip().upper() == "Y",
            # The KEYS are lower-cased for case-insensitive lookup, which loses the
            # deployed casing — and a physical node name must be the deployed
            # identifier byte-for-byte (ONTOLOGY §2.4). So the original casing rides
            # the value, for any consumer that emits a node rather than just a type.
            "database": db, "table": tbl, "column": col,
        }
    return out


#: The v2 standard publishes column facts through a catalogue VIEW that unions the
#: deployed dictionary (types, and descriptions carried as COMMENTs) with whatever
#: ``column_metadata`` curates.  A conformant v2 product may therefore leave
#: ``column_metadata`` EMPTY: read the catalogue, or the agent receives names and
#: types and no meaning at all — silently, which is worse than an error.
COLUMN_CATALOGUE_CANDIDATES = ("column_catalogue", "column_catalog")

#: catalogue column -> the ``column_metadata`` field the walker already consumes.
#: Everything not listed here already shares its name with the curated table.
_CATALOGUE_RENAMES = {
    "catalogue_database": "database_name",
    "catalogue_table": "table_name",
}

#: A catalogue row that carries none of these and is undocumented describes a column
#: the product says nothing about.  It belongs to the physical layer (where the
#: undocumented-coverage statistic is computed), not to the logical model.
_GOVERNANCE_FIELDS = ("is_pii", "is_sensitive", "data_classification",
                      "allowed_values_json")


def fetch_column_catalogue(cursor, semantic_database: str) -> List[Dict[str, Any]]:
    """Read the v2 column catalogue, mapped onto the ``column_metadata`` contract.

    Returns ``[]`` when the product publishes no catalogue (every v1 product, and
    any composition that omits it) so the caller simply keeps what it already read.

    A row is kept when the product documents it **or** it carries a governance fact.
    An undocumented, ungoverned column is not a logical attribute — the product
    declares nothing about it — and admitting one would both inflate the logical
    model and destroy the undocumented-coverage finding.
    """
    real = find_table(cursor, semantic_database, *COLUMN_CATALOGUE_CANDIDATES)
    if not real:
        return []
    try:
        rows = _rows(cursor, f"SELECT * FROM {semantic_database}.{real}")
    except Exception as exc:  # a missing catalogue must never kill a build
        logger.warning("ANDP: could not read %s.%s — %s", semantic_database, real, exc)
        return []

    out: List[Dict[str, Any]] = []
    for row in rows:
        mapped = {_CATALOGUE_RENAMES.get(k, k): v for k, v in row.items()}
        documented = str(mapped.get("is_documented") or "").strip() not in ("", "0", "False", "N")
        governed = any(
            mapped.get(f) not in (None, "", 0, "0") for f in _GOVERNANCE_FIELDS
        )
        if not documented and not governed:
            continue
        out.append(mapped)
    return out


#: Teradata ColumnType codes → SQL type names (the subset these products use).
_TD_TYPES = {
    "CF": "CHAR", "CV": "VARCHAR", "CO": "CLOB",
    "I": "INTEGER", "I1": "BYTEINT", "I2": "SMALLINT", "I8": "BIGINT",
    "D": "DECIMAL", "N": "NUMBER", "F": "FLOAT",
    "DA": "DATE", "TS": "TIMESTAMP", "SZ": "TIMESTAMP WITH TIME ZONE",
    "AT": "TIME", "BF": "BYTE", "BV": "VARBYTE", "BO": "BLOB",
    "JN": "JSON", "XM": "XML",
}


def _render_type(row: Dict[str, Any]) -> str:
    """Render a dictionary row as a readable SQL type (``VARCHAR(2)``)."""
    code = str(row.get("columntype") or "").strip().upper()
    name = _TD_TYPES.get(code, code or "UNKNOWN")
    if name in ("CHAR", "VARCHAR", "BYTE", "VARBYTE"):
        length = row.get("columnlength")
        if length:
            return f"{name}({int(length)})"
    if name in ("DECIMAL", "NUMBER"):
        total = row.get("decimaltotaldigits")
        frac = row.get("decimalfractionaldigits")
        if total not in (None, "", -128):
            return f"{name}({int(total)},{int(frac or 0)})"
    return name


def find_crosswalk_tables(cursor, database: str) -> List[str]:
    """Crosswalk tables in a product's semantic module.

    The ANDP standard does not name a cross-product link table, so products invent
    their own (the reference product calls its one ``nibrs_crosswalk``).  Matching on
    the shared *word* rather than a hardcoded name means the next product's crosswalk
    is picked up without a code change — and a product with none simply yields [].
    """
    if not database:
        return []
    safe_db = database.replace("'", "''")
    rows = _rows(
        cursor,
        f"SELECT TableName FROM DBC.TablesV WHERE DatabaseName = '{safe_db}'",
    )
    out = []
    for r in rows:
        name = str(r.get("tablename") or "").strip()
        low = name.lower()
        # the view wrapping a crosswalk is excluded: on the reference product it
        # appends hardcoded sample ids, so the TABLE is the honest source
        if "crosswalk" in low and not low.startswith("v_"):
            out.append(name)
    return sorted(out)


def fetch_crosswalks(cursor, database: str) -> List[Dict[str, Any]]:
    """Read every crosswalk row a product declares, tagged with its source table."""
    out: List[Dict[str, Any]] = []
    for table in find_crosswalk_tables(cursor, database):
        try:
            for row in _rows(cursor, f"SELECT * FROM {database}.{table}"):
                if _active(row):
                    row["_crosswalk_table"] = table
                    out.append(row)
        except Exception as exc:
            logger.warning("ANDP: could not read crosswalk %s.%s — %s", database, table, exc)
    return out


def fetch_snapshot(cursor, product: str,
                   semantic_database: Optional[str] = None,
                   include_physical: bool = True,
                   include_product_knowledge: bool = True) -> SemanticSnapshot:
    """Read a deployed product into a :class:`SemanticSnapshot`.

    Args:
        cursor: an open DB-API cursor (Teradata).
        product: the data product's name, e.g. ``"NIBRS_Source"``.
        semantic_database: skip discovery and read this database directly.
        include_physical: read the data dictionary for authoritative column types.
        include_product_knowledge: also read the Memory and Observability modules
            (glossary, query recipes, design decisions, naming standards, lineage,
            quality readings) — the product's own description of itself.
    """
    if semantic_database:
        sem_db, method = semantic_database, "explicit"
        if not _table_exists(cursor, sem_db, "data_product_map"):
            raise SourceError(f"'{sem_db}' has no data_product_map — not a semantic module.")
    else:
        sem_db, method = discover_semantic_database(cursor, product)

    payload: Dict[str, List[Dict[str, Any]]] = {}
    missing: List[str] = []
    for table in CATALOG_TABLES:
        if not _table_exists(cursor, sem_db, table):
            missing.append(table)
            payload[table] = []
            continue
        try:
            rows = _rows(cursor, f"SELECT * FROM {sem_db}.{table}")
        except Exception as exc:  # a single unreadable catalog table must not kill the build
            logger.warning("ANDP: could not read %s.%s — %s", sem_db, table, exc)
            missing.append(table)
            rows = []
        # Honour the standard's soft-delete flag where present.
        payload[table] = [r for r in rows if _active(r)]

    if missing:
        logger.info("ANDP: product '%s' missing/unreadable catalog tables: %s",
                    product, ", ".join(missing))

    # ── v2: the column facts may live in the catalogue view, not the table ──
    # The catalogue already folds ``column_metadata`` in (COALESCE), so where both
    # exist the catalogue is the richer of the two and wins; a curated row the
    # catalogue does not cover is kept rather than dropped.
    column_source = "column_metadata"
    catalogue_rows = fetch_column_catalogue(cursor, sem_db)
    if catalogue_rows:
        covered = {
            (_s(r.get("database_name")).lower(), _s(r.get("table_name")).lower(),
             _s(r.get("column_name")).lower())
            for r in catalogue_rows
        }
        curated_only = [
            r for r in payload["column_metadata"]
            if (_s(r.get("database_name")).lower(), _s(r.get("table_name")).lower(),
                _s(r.get("column_name")).lower()) not in covered
        ]
        payload["column_metadata"] = catalogue_rows + curated_only
        column_source = ("column_catalogue" if not curated_only
                         else f"column_catalogue + {len(curated_only)} curated")

    databases = sorted({
        str(r.get("database_name") or "").strip()
        for r in payload["data_product_map"]
        if str(r.get("database_name") or "").strip()
    })

    physical: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    if include_physical and databases:
        physical = fetch_physical_columns(cursor, databases)

    # ── the product's own description of itself (Memory + Observability) ──
    knowledge: Dict[str, List[Dict[str, Any]]] = {k: [] for k in _KNOWLEDGE_TABLES}
    if include_product_knowledge:
        by_module = {
            _s(r.get("module_name")).lower(): _s(r.get("database_name"))
            for r in payload["data_product_map"]
            if _s(r.get("module_name"))
        }
        for field_name, (module, candidates) in _KNOWLEDGE_TABLES.items():
            # the semantic module's own tables live in sem_db even if unmapped
            database = by_module.get(module) or (sem_db if module == "semantic" else "")
            if not database:
                continue
            real = find_table(cursor, database, *candidates)
            if not real:
                continue
            try:
                knowledge[field_name] = _rows(cursor, f"SELECT * FROM {database}.{real}")
            except Exception as exc:
                logger.warning("ANDP: could not read %s.%s — %s", database, real, exc)

    obs_db = ""
    for r in payload["data_product_map"]:
        if _s(r.get("module_name")).lower() == "observability":
            obs_db = _s(r.get("database_name"))
            break

    return SemanticSnapshot(
        product=product,
        semantic_database=sem_db,
        data_product_map=payload["data_product_map"],
        entity_metadata=payload["entity_metadata"],
        column_metadata=payload["column_metadata"],
        table_relationship=payload["table_relationship"],
        physical_columns=physical,
        business_glossary=knowledge["business_glossary"],
        query_cookbook=knowledge["query_cookbook"],
        design_decisions=knowledge["design_decisions"],
        module_registry=knowledge["module_registry"],
        naming_standards=knowledge["naming_standards"],
        data_lineage=knowledge["data_lineage"],
        quality_metrics=knowledge["quality_metrics"],
        product_crosswalks=(fetch_crosswalks(cursor, sem_db)
                            if include_product_knowledge else []),
        access_objects=(fetch_access_objects(cursor, sem_db)
                        if include_product_knowledge else []),
        trust_gate=(fetch_trust_gate(cursor, obs_db, product)
                    if include_product_knowledge else None),
        source_detail=f"{product} · {sem_db} (discovery: {method}; columns: {column_source})",
    )


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _active(row: Dict[str, Any]) -> bool:
    """Respect ``is_active`` when the catalog carries it; absent → active."""
    if "is_active" not in row:
        return True
    value = row.get("is_active")
    if value is None:
        return True
    text = str(value).strip().lower()
    return text not in ("0", "n", "no", "false", "f")


def list_products(cursor) -> List[Dict[str, str]]:
    """Every ANDP product visible on this connection (for a picker).

    Found by scanning the dictionary for ``data_product_map`` — which works whether
    or not the estate has a product registry.
    """
    rows = _rows(
        cursor,
        "SELECT DatabaseName FROM DBC.TablesV WHERE TableName = 'data_product_map' "
        "ORDER BY DatabaseName",
    )
    out: List[Dict[str, str]] = []
    for r in rows:
        sem_db = str(r.get("databasename") or "").strip()
        if not sem_db:
            continue
        # "<Product>_Semantic" → "<Product>"; otherwise report the database as-is.
        product = sem_db[: -len("_Semantic")] if sem_db.lower().endswith("_semantic") else sem_db
        out.append({"product": product, "semantic_database": sem_db})
    return out
