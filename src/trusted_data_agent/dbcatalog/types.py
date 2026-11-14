"""Normalised catalog types. Plain dataclasses with ``to_dict`` so a provider's
output, the cache, the intelligence layer and the REST payload share ONE shape."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class CatalogSource:
    """One way of seeing a database: a direct connection or an MCP server."""
    id: str                       # "conn:<connection id>" | "mcp:<server id>"
    kind: str                     # "direct" | "mcp"
    label: str
    driver: str = ""              # teradata | postgresql | mysql | sqlite | jdbc | mcp
    capabilities: List[str] = field(default_factory=list)
    is_default: bool = False
    detail: str = ""              # host / server name for the picker
    reason: str = ""              # why this source was chosen (resolution transparency)
    connection_id: str = ""       # a direct source's id as the Canvas SQL connector knows it

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DatabaseInfo:
    name: str
    kind: str = "database"        # database | user | schema
    comment: str = ""
    object_count: Optional[int] = None
    size_bytes: Optional[int] = None
    is_system: bool = False
    owner: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObjectInfo:
    database: str
    name: str
    kind: str = "table"           # table | view | macro | procedure | function | other
    comment: str = ""
    size_bytes: Optional[int] = None
    row_count: Optional[int] = None
    row_count_source: str = ""    # "statistics" | "exact" | ""
    created_at: str = ""
    altered_at: str = ""
    column_count: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def qualified(self) -> str:
        return f"{self.database}.{self.name}"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["qualified"] = self.qualified
        return d


@dataclass
class ColumnInfo:
    name: str
    data_type: str = ""
    nullable: Optional[bool] = None
    position: Optional[int] = None
    comment: str = ""
    default: str = ""
    is_primary: bool = False      # member of the primary index / primary key
    is_unique: bool = False       # member of a unique index
    is_identity: bool = False
    stats: Dict[str, Any] = field(default_factory=dict)   # unique_values, null_count, collected_at …
    flags: List[str] = field(default_factory=list)        # filled by intelligence (pii, text_date, …)
    ontology: Dict[str, Any] = field(default_factory=dict)  # overlay from the bound ontology

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ForeignKey:
    child_database: str
    child_table: str
    child_columns: List[str]
    parent_database: str
    parent_table: str
    parent_columns: List[str]
    name: str = ""
    declared: bool = True          # False for a join the intelligence layer INFERRED
    confidence: float = 1.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IndexInfo:
    name: str
    kind: str                      # primary | unique | secondary | primary_key | other
    columns: List[str]
    unique: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObjectDetail:
    info: ObjectInfo
    columns: List[ColumnInfo] = field(default_factory=list)
    foreign_keys_out: List[ForeignKey] = field(default_factory=list)   # this object references …
    foreign_keys_in: List[ForeignKey] = field(default_factory=list)    # … is referenced by
    indexes: List[IndexInfo] = field(default_factory=list)
    view_text: str = ""
    stats_collected_at: str = ""
    intelligence: Dict[str, Any] = field(default_factory=dict)
    ontology: Dict[str, Any] = field(default_factory=dict)
    unavailable: List[str] = field(default_factory=list)   # capability names this source lacks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "info": self.info.to_dict(),
            "columns": [c.to_dict() for c in self.columns],
            "foreign_keys_out": [f.to_dict() for f in self.foreign_keys_out],
            "foreign_keys_in": [f.to_dict() for f in self.foreign_keys_in],
            "indexes": [i.to_dict() for i in self.indexes],
            "view_text": self.view_text,
            "stats_collected_at": self.stats_collected_at,
            "intelligence": self.intelligence,
            "ontology": self.ontology,
            "unavailable": list(self.unavailable),
        }


@dataclass
class PreviewResult:
    columns: List[str]
    rows: List[List[Any]]
    truncated: bool = False
    masked_columns: List[str] = field(default_factory=list)
    sql: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
