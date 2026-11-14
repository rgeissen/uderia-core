"""
dbdeploy — convert a declarative logical/physical data model into a target
relational backend and (optionally) deploy it.

Sibling abstraction to ``vectorstore`` and ``kgstore``.  Two layers:

    * Converter (pure, deterministic, no I/O): ``model.json`` →
      :class:`physical_model.PhysicalModel` → dialect emitter → a
      :class:`types.ConversionResult` carrying a DDL plan, a CSV load plan, and a
      Knowledge-Graph physical-layer delta.  The converter is dialect-aware but
      I/O-free — the primary unit-test target.

    * Deployment backend (I/O, dialect-agnostic — added in M2): executes a DDL
      plan and bulk-loads data against a live backend (Teradata first), behind a
      capability-negotiated ABC.

M1 ships the pure converter only.
"""

from .types import (  # noqa: F401
    DDLStatement,
    DDLPlan,
    LoadTableSpec,
    LoadPlan,
    KGPhysicalDelta,
    ConversionResult,
    DeployResult,
    LoadResult,
)
from .physical_model import PhysicalModel, extract_physical_model  # noqa: F401
from .design_standard import (  # noqa: F401
    TeradataStandard, DEFAULT_TERADATA_STANDARD, STANDARD_VERSION, DesignRule, RULES, rules_report,
)
from .probes import probe_table, choose_pi, refine_columns, PIDecision, TableProbe  # noqa: F401
from .readback import readback_report, planned_from_sql, planned_from_ddl, ReadbackReport  # noqa: F401
from .converter import convert  # noqa: F401
from .metric_compiler import (  # noqa: F401
    compile_metric,
    translate_expr,
    resolve_metric_from_entity,
    MetricCompileResult,
    MetricCompileError,
)
from .capabilities import DeploymentCapability, REQUIRED_CAPABILITIES  # noqa: F401
from .backend import DeploymentBackend, DeploymentError, DeploymentNotSupported  # noqa: F401
from .datasource import (  # noqa: F401
    DataSource,
    SyntheticDataSource,
    ShippedCsvDataSource,
    populate_load_plan,
)
from .csv_coerce import coerce_csv, coerce_value  # noqa: F401
from . import factory  # noqa: F401

# Register built-in deployment backends at import time.
try:  # enterprise package
    from .teradata_backend import TeradataDeploymentBackend  # noqa: F401,E402
    factory.register_backend("teradata", TeradataDeploymentBackend)
except ImportError:
    pass

from .postgresql_backend import PostgresDeploymentBackend  # noqa: F401,E402

factory.register_backend("postgresql", PostgresDeploymentBackend)

__all__ = [
    "DDLStatement",
    "DDLPlan",
    "LoadTableSpec",
    "LoadPlan",
    "KGPhysicalDelta",
    "ConversionResult",
    "DeployResult",
    "LoadResult",
    "PhysicalModel",
    "extract_physical_model",
    "TeradataStandard",
    "DEFAULT_TERADATA_STANDARD",
    "STANDARD_VERSION", "DesignRule", "RULES", "rules_report",
    "probe_table", "choose_pi", "refine_columns", "PIDecision", "TableProbe",
    "readback_report", "planned_from_sql", "planned_from_ddl", "ReadbackReport",
    "convert",
    "DeploymentCapability",
    "REQUIRED_CAPABILITIES",
    "DeploymentBackend",
    "DeploymentError",
    "DeploymentNotSupported",
    "DataSource",
    "SyntheticDataSource",
    "ShippedCsvDataSource",
    "populate_load_plan",
    "coerce_csv",
    "coerce_value",
    "TeradataDeploymentBackend",
    "factory",
]
