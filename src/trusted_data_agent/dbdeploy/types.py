"""
dbdeploy output artifacts — the pure converter's return shapes.

These are plain dataclasses with no behaviour beyond serialisation helpers, so
they are trivial to assert against in CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# DDL phases — statements execute in this order (databases before tables before
# comments before foreign keys before statistics).  Stable sort preserves
# insertion order within a phase.
PHASE_DATABASE = 10
PHASE_TABLE = 20
PHASE_COMMENT = 30
PHASE_FOREIGN_KEY = 40
PHASE_VIEW = 45          # metric views — depend on tables existing
PHASE_STATISTICS = 50


@dataclass
class DDLStatement:
    """A single DDL statement plus enough metadata to order and audit it."""

    kind: str            # database | table | comment | foreign_key | statistics
    identifier: str      # the object the statement targets (for logging/cleanup)
    sql: str
    phase: int


@dataclass
class DDLPlan:
    """An ordered set of DDL statements that realises the physical model."""

    statements: List[DDLStatement] = field(default_factory=list)

    def add(self, kind: str, identifier: str, sql: str, phase: int) -> None:
        self.statements.append(DDLStatement(kind=kind, identifier=identifier, sql=sql, phase=phase))

    def ordered(self) -> List[DDLStatement]:
        # Stable sort by phase preserves insertion order within each phase.
        return sorted(self.statements, key=lambda s: s.phase)

    def by_kind(self, kind: str) -> List[DDLStatement]:
        return [s for s in self.ordered() if s.kind == kind]

    def sql_script(self) -> str:
        return "\n".join(s.sql for s in self.ordered())


@dataclass
class LoadTableSpec:
    """How to load one table's demo data.

    ``columns`` is the ordered (name, teradata_type) list so the loader can
    coerce CSV text into typed columns.  ``csv_ref`` is filled at deploy time
    (the converter is I/O-free and does not fetch the CSV).
    """

    target_database: str
    target_table: str
    columns: List[Tuple[str, str]] = field(default_factory=list)
    pk_columns: List[str] = field(default_factory=list)
    csv_ref: Optional[str] = None                       # source URL/identifier (provenance)
    records: Optional[List[Dict]] = None                 # rows to load (populated by a DataSource)
    referenced_by: int = 0                               # incoming FK constraints (this table is an RI parent)


@dataclass
class LoadPlan:
    tables: List[LoadTableSpec] = field(default_factory=list)


@dataclass
class KGPhysicalDelta:
    """Physical-layer entities/relationships to import into the KG.

    Shapes match ``GraphStore.import_bulk(entities, relationships)`` exactly:

        entity       = {name, entity_type, properties, source, source_detail}
        relationship = {source_name, source_type, target_name, target_type,
                        relationship_type, cardinality?, metadata?, source}
    """

    entities: List[Dict] = field(default_factory=list)
    relationships: List[Dict] = field(default_factory=list)


@dataclass
class DeployResult:
    """Outcome of executing a DDL plan against a backend."""

    dry_run: bool = False
    generated_sql: str = ""
    planned: List[str] = field(default_factory=list)              # identifiers that would run (dry-run)
    executed: List[Tuple[str, str]] = field(default_factory=list)  # (kind, identifier)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)    # (identifier, error)
    cleaned_up: List[str] = field(default_factory=list)            # objects dropped after a failure
    replaced: List[Tuple[str, str]] = field(default_factory=list)  # objects dropped pre-create (idempotent re-deploy)
    success: bool = False

    def summary(self) -> Dict[str, int]:
        return {
            "executed": len(self.executed),
            "skipped": len(self.skipped),
            "errors": len(self.errors),
            "cleaned_up": len(self.cleaned_up),
            "replaced": len(self.replaced),
        }


@dataclass
class LoadResult:
    """Outcome of bulk-loading demo data."""

    rows_by_table: Dict[str, int] = field(default_factory=dict)
    errors: List[Tuple[str, str]] = field(default_factory=list)
    success: bool = False

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_table.values())


@dataclass
class ConversionResult:
    """Everything the converter produces from one model + dialect."""

    dialect: str
    ddl: DDLPlan = field(default_factory=DDLPlan)
    load: LoadPlan = field(default_factory=LoadPlan)
    kg_delta: KGPhysicalDelta = field(default_factory=KGPhysicalDelta)
    warnings: List[str] = field(default_factory=list)
    physical_model: Optional[object] = None  # PhysicalModel (typed loosely to avoid import cycle)
    # Database provisioning is a backend-abstraction concern (ensure_database),
    # NOT raw DDL.  The converter only *names* what's needed:
    root_database: Optional[str] = None          # container/root that owns the domain databases
    databases: List[str] = field(default_factory=list)  # per-domain database names (created under root)

    def as_summary(self) -> Dict[str, int]:
        return {
            "databases": len(self.databases),
            "tables": len(self.ddl.by_kind("table")),
            "foreign_keys": len(self.ddl.by_kind("foreign_key")),
            "comments": len(self.ddl.by_kind("comment")),
            "views": len(self.ddl.by_kind("view")),
            "statistics": len(self.ddl.by_kind("statistics")),
            "load_tables": len(self.load.tables),
            "kg_entities": len(self.kg_delta.entities),
            "kg_relationships": len(self.kg_delta.relationships),
            "warnings": len(self.warnings),
        }
