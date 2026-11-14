"""
dbcatalog — ONE catalog contract over the ways Uderia can see a database's objects.

The Database Navigator component reads a normalised catalog (databases → objects →
columns, keys, foreign keys, indexes, statistics, sizes, comments) through
:class:`CatalogProvider`. Two provider families implement it, capability-negotiated
exactly as the vector-store and graph-store layers are:

  * **direct** — the user's own Database Connection reads the DBMS dictionary itself
    (Teradata ``DBC.*`` views; ``information_schema`` / ``sqlite_master`` elsewhere).
    On Teradata this is the RICHER path: declared foreign keys, primary indexes,
    statistics age, sizes and view text are things no MCP tool set returns.
  * **mcp** — the profile's MCP data-source tools, resolved to catalog ROLES by name
    and description (never a hard-coded tool name), so any connected data source
    still yields a catalog.

Everything an operator experiences as "intelligence" (object roles, key and join
inference, PII and text-date flags, ranked search, next actions) lives in
:mod:`intelligence` and runs on the NORMALISED catalog — provider-independent, pure,
zero tokens.
"""
from .capabilities import CatalogCapability
from .types import (CatalogSource, ColumnInfo, DatabaseInfo, ForeignKey, IndexInfo,
                    ObjectDetail, ObjectInfo, PreviewResult)
from .base import CatalogProvider, CatalogError

__all__ = [
    "CatalogCapability", "CatalogSource", "ColumnInfo", "DatabaseInfo", "ForeignKey",
    "IndexInfo", "ObjectDetail", "ObjectInfo", "PreviewResult",
    "CatalogProvider", "CatalogError",
]
