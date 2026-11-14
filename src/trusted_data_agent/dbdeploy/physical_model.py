"""
physical_model — extract a canonical, dialect-neutral physical model from a
parsed Databricks Industry Data Model (``model.json``).

PURITY CONTRACT
---------------
No I/O.  Takes a parsed ``model.json`` dict + options, returns a
:class:`PhysicalModel`.  This is the single structured intermediate consumed by
the dialect emitters, so DDL generation and the KG physical-delta cannot drift.

The model encodes the logical↔physical correspondence attribute-by-attribute
(``name``/``business_glossary_term`` logical · ``column_name``/``type`` physical;
``table_name``; domain ``database_name``; ``model_conventions.deployment_catalog``).
We reuse that linkage rather than re-deriving it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class PhysicalModelError(ValueError):
    """Raised on a structurally invalid model."""


@dataclass(frozen=True)
class FKRef:
    """A resolved foreign-key reference (target side), in logical domain terms."""

    src_domain: str
    src_table: str
    src_column: str
    tgt_domain: str
    tgt_table: str
    tgt_column: str


@dataclass
class PhysColumn:
    name: str                       # physical column_name
    dbx_type: str                   # raw Databricks/Spark type string
    nullable: bool
    comment: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    is_primary_key: bool = False
    fk: Optional[FKRef] = None
    # Realism signal the model authors declared per attribute (consumed by the
    # semantic demo-data generator; the DDL emitters ignore both):
    #   value_regex   — an alternation (``a|b|c``) or a shape (``^[A-Z]{3}$``)
    #   glossary_term — the business_glossary_term the column implements
    value_regex: Optional[str] = None
    glossary_term: Optional[str] = None


@dataclass
class PhysTable:
    domain: str                     # logical data-domain name (e.g. "order")
    database_name: str              # target schema/database name (the single flat DB once flattened)
    name: str                       # deployed table_name (collision-prefixed in the flat model)
    comment: Optional[str] = None
    subdomain: Optional[str] = None
    table_type: Optional[str] = None
    columns: List[PhysColumn] = field(default_factory=list)
    pk_columns: List[str] = field(default_factory=list)
    # The original (logical) table name, set when the deployed ``name`` is
    # collision-prefixed during flattening.  Falls back to ``name``.  Used to
    # resolve the logical ``domain.table`` node an ``implements`` edge targets.
    logical_name: Optional[str] = None

    @property
    def logical(self) -> str:
        return self.logical_name or self.name


@dataclass
class PhysDatabase:
    name: str                       # database_name (target schema)
    domain: str                     # logical domain it realises
    comment: Optional[str] = None


@dataclass
class PhysMetric:
    name: str                       # view_name
    owner_domain: str
    owner_product: str
    source_schema: Optional[str]    # Teradata schema (domain database) of the source table
    source_table: Optional[str]     # source table name the metric aggregates
    dimensions: List[Dict[str, Any]] = field(default_factory=list)
    measures: List[Dict[str, Any]] = field(default_factory=list)

    def definition(self) -> Dict[str, Any]:
        return {"dimensions": self.dimensions, "measures": self.measures}


@dataclass
class PhysicalModel:
    industry: str
    flavour: str
    deployment_catalog: str         # from model_conventions (or <industry>_<flavour>)
    conventions: Dict[str, Any] = field(default_factory=dict)
    databases: List[PhysDatabase] = field(default_factory=list)
    tables: List[PhysTable] = field(default_factory=list)
    fks: List[FKRef] = field(default_factory=list)
    metrics: List[PhysMetric] = field(default_factory=list)
    # Diagnostics
    domains_skipped_by_division: int = 0
    fks_dropped_dangling: int = 0
    fks_dropped_filtered: int = 0
    # Set once flatten_to_single_database() has collapsed the physical layer into
    # a single database (division/domain become logical-only KG entities).
    flat: bool = False
    flat_collisions: List[str] = field(default_factory=list)  # logical names that needed a domain prefix


def flatten_to_single_database(model: "PhysicalModel", root_db: str) -> "PhysicalModel":
    """Collapse the physical layer into ONE database — the flat-namespace model.

    Division and domain are logical-only KG concepts; they are NEVER physical
    containers.  Every table is placed in ``root_db`` under its bare name.  A
    table whose name collides (case-insensitively) with another is disambiguated
    with a ``<domain>_`` prefix — rare: validated globally-unique across all 30
    MVM models (5,556 tables), with sparse collisions only in ~2/3 of ECM models.
    FK and metric references are rewritten to the deployed names.  ``logical_name``
    preserves the original so ``implements`` edges still target ``domain.table``.

    Mutates and returns ``model``.
    """
    import dataclasses
    from collections import Counter

    root = (root_db or model.deployment_catalog or f"{model.industry}_{model.flavour}").strip()

    # 1. case-insensitive collision detection across ALL table names
    ci_counts = Counter(t.name.lower() for t in model.tables)
    colliding = {n for n, k in ci_counts.items() if k > 1}

    # 2. assign deployed names (bare, or <domain>_ prefixed on collision); keep CI-unique
    rename: Dict[Tuple[str, str], str] = {}     # (domain, logical_name) -> deployed name
    by_logical: Dict[str, str] = {}             # logical_name -> deployed (fallback when unique)
    used_ci: set = set()
    collisions: List[str] = []
    for t in model.tables:
        t.logical_name = t.name
        deployed = t.name if t.name.lower() not in colliding else f"{t.domain}_{t.name}"
        base, n = deployed, 2
        while deployed.lower() in used_ci:     # defensive: guarantee global CI-uniqueness
            deployed = f"{base}_{n}"
            n += 1
        used_ci.add(deployed.lower())
        if deployed != t.logical_name:
            collisions.append(f"{t.domain}.{t.logical_name}")
        rename[(t.domain, t.logical_name)] = deployed
        by_logical.setdefault(t.logical_name, deployed)
        t.name = deployed
        t.database_name = root

    # 3. rewrite FK references (FKRef is frozen → rebuild) to deployed names
    model.fks = [
        dataclasses.replace(
            fk,
            src_table=rename.get((fk.src_domain, fk.src_table), fk.src_table),
            tgt_table=rename.get((fk.tgt_domain, fk.tgt_table), fk.tgt_table),
        )
        for fk in model.fks
    ]

    # 4. rewrite metric source tables → deployed names in the single schema
    for m in model.metrics:
        if m.source_table is not None:
            m.source_table = (rename.get((m.owner_domain, m.source_table))
                              or by_logical.get(m.source_table, m.source_table))
        m.source_schema = root

    # 5. collapse the database list to the single flat database
    model.databases = [PhysDatabase(name=root, domain="")]
    model.flat = True
    model.flat_collisions = collisions
    return model


# ── helpers ─────────────────────────────────────────────────────────────────

def _split_tags(raw: Any) -> List[str]:
    if not raw or not isinstance(raw, str):
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _split_pk(raw: Any) -> List[str]:
    """A product ``primary_key`` may be a single column or a comma list."""
    if not raw or not isinstance(raw, str):
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def _parse_fk_target(fk: Any) -> Optional[Tuple[str, str, str]]:
    if not fk or not isinstance(fk, str):
        return None
    parts = fk.strip().split(".")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        return None
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _conventions(model_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Merge model_conventions (model.* overlaid by model_requirements.*)."""
    conv: Dict[str, Any] = {}
    model = model_doc.get("model") or {}
    mr = model_doc.get("model_requirements") or {}
    if isinstance(model.get("model_conventions"), dict):
        conv.update(model["model_conventions"])
    if isinstance(mr.get("model_conventions"), dict):
        conv.update(mr["model_conventions"])
    return conv


def _normalize_divisions(value: Any) -> Optional[frozenset]:
    if not value:
        return None
    if isinstance(value, str):
        value = [value]
    cleaned = frozenset(str(v).strip().lower() for v in value if str(v).strip())
    return cleaned or None


# ── extraction ──────────────────────────────────────────────────────────────

def extract_physical_model(
    model_doc: Dict[str, Any],
    *,
    industry: str,
    flavour: str,
    divisions: Any = None,
) -> PhysicalModel:
    """Walk a parsed ``model.json`` into a :class:`PhysicalModel`.

    Args:
        model_doc: full parsed ``model.json``.
        industry, flavour: provenance + default catalog name.
        divisions: optional iterable restricting to specific divisions.

    Raises:
        PhysicalModelError: if the document lacks a ``model`` body.
    """
    if not isinstance(model_doc, dict):
        raise PhysicalModelError("model_doc must be a dict (parsed model.json)")
    model = model_doc.get("model")
    if not isinstance(model, dict):
        raise PhysicalModelError("model.json missing a 'model' object")

    conv = _conventions(model_doc)
    catalog = (conv.get("deployment_catalog") or f"{industry}_{flavour}").strip()
    selected = _normalize_divisions(divisions)

    pm = PhysicalModel(
        industry=industry,
        flavour=flavour,
        deployment_catalog=catalog,
        conventions=conv,
    )

    domains = model.get("domains") or []

    # Pre-pass: every (domain, table) regardless of the division filter, plus the
    # domain→database_name map, so FK resolution can tell a *filtered* target
    # (exists but excluded) from a *dangling* one (absent from the model).
    all_tables: set = set()
    db_by_domain: Dict[str, str] = {}
    for d in domains:
        if not isinstance(d, dict):
            continue
        dn = (d.get("name") or "").strip()
        if not dn:
            continue
        db_by_domain[dn] = (d.get("database_name") or dn).strip()
        for p in d.get("products") or []:
            if not isinstance(p, dict):
                continue
            tn = (p.get("table_name") or p.get("name") or "").strip()
            if tn:
                all_tables.add((dn, tn))

    included_tables: set = set()
    fk_specs: List[FKRef] = []

    for domain in domains:
        if not isinstance(domain, dict):
            continue
        dom_name = (domain.get("name") or "").strip()
        if not dom_name:
            continue
        division = (domain.get("division") or "unspecified").strip().lower()
        if selected is not None and division not in selected:
            pm.domains_skipped_by_division += 1
            continue

        db_name = db_by_domain[dom_name]
        pm.databases.append(PhysDatabase(
            name=db_name, domain=dom_name,
            comment=(domain.get("description") or None),
        ))

        for product in domain.get("products") or []:
            if not isinstance(product, dict):
                continue
            table_name = (product.get("table_name") or product.get("name") or "").strip()
            if not table_name:
                continue
            pk_cols = _split_pk(product.get("primary_key"))
            table = PhysTable(
                domain=dom_name,
                database_name=db_name,
                name=table_name,
                comment=(product.get("description") or None),
                subdomain=(product.get("subdomain") or None),
                table_type=(product.get("type") or None),
                pk_columns=pk_cols,
            )

            for attr in product.get("attributes") or []:
                if not isinstance(attr, dict):
                    continue
                col_name = (attr.get("column_name") or attr.get("name") or "").strip()
                if not col_name:
                    continue
                tags = _split_tags(attr.get("tags"))
                is_pk = col_name in pk_cols or "primary_key" in tags
                fk_parsed = _parse_fk_target(attr.get("foreign_key_to"))
                fk_ref = None
                if fk_parsed:
                    fk_ref = FKRef(
                        src_domain=dom_name, src_table=table_name, src_column=col_name,
                        tgt_domain=fk_parsed[0], tgt_table=fk_parsed[1], tgt_column=fk_parsed[2],
                    )
                    fk_specs.append(fk_ref)
                table.columns.append(PhysColumn(
                    name=col_name,
                    dbx_type=(attr.get("type") or "STRING").strip(),
                    nullable=not is_pk,
                    comment=(attr.get("description") or None),
                    tags=tags,
                    is_primary_key=is_pk,
                    fk=fk_ref,
                    value_regex=((attr.get("value_regex") or "").strip() or None),
                    glossary_term=((attr.get("business_glossary_term") or "").strip() or None),
                ))

            pm.tables.append(table)
            included_tables.add((dom_name, table_name))

    # ── metric views (parsed definition from the metric YAML) ──
    try:
        from trusted_data_agent.agent.kg_constructors.industry_model_walker import parse_metric_definition
    except ImportError:  # the Industry Data Model constructor is not in this distribution: counts-only metric views
        def parse_metric_definition(sql):
            return None
    for mv in model.get("metric_views") or []:
        if not isinstance(mv, dict):
            continue
        view_name = (mv.get("view_name") or "").strip()
        od = (mv.get("owner_domain") or "").strip()
        op = (mv.get("owner_product") or "").strip()
        if not view_name or not od:
            continue
        mdef = parse_metric_definition(mv.get("sql")) or {}
        # Source = the table the metric aggregates: from the YAML `source`
        # (catalog.schema.table → schema.table) else the owner table.
        src_schema, src_table = None, None
        src = mdef.get("source")
        if src:
            parts = [p for p in str(src).split(".") if p]
            if len(parts) >= 2:
                src_schema, src_table = parts[-2], parts[-1]
        if not src_table:
            src_schema, src_table = db_by_domain.get(od, od), op
        pm.metrics.append(PhysMetric(
            name=view_name, owner_domain=od, owner_product=op,
            source_schema=src_schema, source_table=src_table,
            dimensions=mdef.get("dimensions") or [], measures=mdef.get("measures") or [],
        ))

    # Resolve FKs against included tables; classify drops.
    for fk in fk_specs:
        tgt = (fk.tgt_domain, fk.tgt_table)
        if tgt not in included_tables:
            if tgt in all_tables:
                pm.fks_dropped_filtered += 1
            else:
                pm.fks_dropped_dangling += 1
            continue
        if (fk.src_domain, fk.src_table) not in included_tables:
            pm.fks_dropped_filtered += 1
            continue
        pm.fks.append(fk)

    return pm
