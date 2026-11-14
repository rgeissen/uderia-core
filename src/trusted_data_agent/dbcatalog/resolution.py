"""Source resolution — which way of seeing a database does THIS agent use?

Order, stated once and reported back as ``reason`` so the UI can say why:

  1. the source the agent's ``componentConfig.database_navigator.sourceId`` names
  2. the user's DEFAULT Database Connection
  3. the user's first Database Connection
  4. the profile's MCP data-source tools (when they resolve to at least a table lister)
  5. nothing — an honest empty state that names which of the two to set up

A connection is a first-class source, never a fallback: on Teradata it is the richer
path. Both families are listed so the picker can switch between them.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .base import CatalogError, CatalogProvider
from .capabilities import CatalogCapability as C
from .generic_provider import GenericCatalogProvider
from .mcp_provider import McpCatalogProvider, resolve_roles_by_shape
from .mongo_provider import MongoCatalogProvider, mongo_capabilities
try:  # enterprise package
    from .teradata_provider import TeradataCatalogProvider
except ImportError:
    TeradataCatalogProvider = None  # type: ignore[assignment,misc]
from .types import CatalogSource

logger = logging.getLogger("quart.app")

from trusted_data_agent.core.database_connections import DIRECT_DRIVERS  # noqa: E402 — the one declaration
_TD_CAPS = sorted(c.value for c in TeradataCatalogProvider("", "", {}).capabilities()) if TeradataCatalogProvider else []


def _generic_caps(driver: str) -> List[str]:
    if driver == "mongodb":
        return mongo_capabilities()
    try:
        return sorted(c.value for c in GenericCatalogProvider("", "", {"driver": driver}).capabilities())
    except CatalogError:
        return []


def canvas_connection_id(conn_id: str) -> str:
    """A Database Connection's id as the Canvas lists it — the ONE mapping ("Open in
    canvas" binds a statement to the same connection). The Canvas reads the same
    Database Connection store, so the id is the store's; only the navigator's `conn:`
    namespace is dropped."""
    cid = str(conn_id or "")
    return cid[len("conn:"):] if cid.startswith("conn:") else cid


def source_for_connection(conn: Dict[str, Any]) -> Optional[CatalogSource]:
    driver = str(conn.get("driver") or "").strip().lower()
    if driver not in DIRECT_DRIVERS:
        return None
    caps = _TD_CAPS if driver == "teradata" else _generic_caps(driver)
    host = conn.get("host") or conn.get("jdbc_url") or ""
    return CatalogSource(id=f"conn:{conn.get('id')}", kind="direct", label=str(conn.get("name") or conn.get("id")),
                         driver=driver, capabilities=caps, is_default=bool(conn.get("is_default")),
                         detail=f"{driver} · {host}" if host else driver,
                         connection_id=canvas_connection_id(conn.get("id")))


def mcp_source(available_tools: List[Dict[str, Any]], label: str = "MCP data source",
               server_id: str = "active") -> Optional[CatalogSource]:
    roles = resolve_roles_by_shape(available_tools or [])
    if not roles.get("list_tables"):
        return None
    prov = McpCatalogProvider(f"mcp:{server_id}", label, invoke=None, roles=roles)  # type: ignore[arg-type]
    caps = sorted(c.value for c in prov.capabilities())
    src = CatalogSource(id=f"mcp:{server_id}", kind="mcp", label=label, driver="mcp", capabilities=caps,
                        detail="via MCP tools: " + ", ".join(sorted({r["tool"] for r in roles.values() if r})))
    return src


def resolve_sources(user_uuid: str, profile_config: Optional[Dict[str, Any]] = None,
                    available_tools: Optional[List[Dict[str, Any]]] = None, mcp_label: str = "",
                    mcp_server_id: str = "", store=None) -> Dict[str, Any]:
    """→ ``{"sources": [CatalogSource…], "default": id|None, "reason": str}``."""
    if store is None:
        from trusted_data_agent.core.database_connections import get_database_connection_store
        store = get_database_connection_store()
    sources: List[CatalogSource] = []
    try:
        conns = store.list(user_uuid) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[dbcatalog] connection store unavailable: %s", exc)
        conns = []
    for c in conns:
        s = source_for_connection(c)
        if s:
            sources.append(s)
    if available_tools:
        m = mcp_source(available_tools, mcp_label or "MCP data source", mcp_server_id or "active")
        if m:
            sources.append(m)

    cfg = ((profile_config or {}).get("componentConfig") or {}).get("database_navigator") or {}
    wanted = str(cfg.get("sourceId") or "").strip()
    ids = {s.id: s for s in sources}
    default, reason = None, ""
    if wanted and wanted in ids:
        default, reason = wanted, "chosen in the agent's component settings"
    elif wanted:
        reason = f"the configured source '{wanted}' is not available; "
    if default is None:
        d = next((s for s in sources if s.kind == "direct" and s.is_default), None)
        if d:
            default, reason = d.id, reason + "your default database connection"
    if default is None:
        d = next((s for s in sources if s.kind == "direct"), None)
        if d:
            default, reason = d.id, reason + "your first database connection"
    if default is None:
        d = next((s for s in sources if s.kind == "mcp"), None)
        if d:
            default, reason = d.id, reason + "the agent's MCP data-source tools"
    if default is None:
        reason = reason + ("no source: add a Database Connection (Components → Connectors → Database "
                           "Connection) or bind the agent to an MCP server that lists tables")
    for s in sources:
        s.reason = reason if s.id == default else ""
    return {"sources": [s.to_dict() for s in sources], "default": default, "reason": reason.strip()}


def build_provider(source_id: str, user_uuid: str, *, store=None, mcp_invoke: Optional[Callable] = None,
                   available_tools: Optional[List[Dict[str, Any]]] = None, mcp_label: str = "",
                   runner: Optional[Callable] = None, dialect_hint: str = "") -> CatalogProvider:
    """Instantiate the provider behind a source id. Secrets are resolved here and
    nowhere else (``store.resolve`` is the one path that exposes them)."""
    sid = str(source_id or "")
    if sid.startswith("conn:"):
        if store is None:
            from trusted_data_agent.core.database_connections import get_database_connection_store
            store = get_database_connection_store()
        conn_id = sid[5:]
        creds = store.resolve(user_uuid, conn_id)
        if not creds:
            raise CatalogError(f"connection '{conn_id}' not found — it may have been deleted; pick another source")
        driver = str(creds.get("driver") or "teradata").strip().lower()
        label = str(creds.get("name") or conn_id)
        kw = {"runner": runner} if runner else {}
        if driver == "teradata":
            if TeradataCatalogProvider is None:
                raise CatalogError("the Teradata catalog provider is part of the enterprise package")
            return TeradataCatalogProvider(sid, label, creds, **kw)
        if driver == "mongodb":
            return MongoCatalogProvider(sid, label, creds)
        return GenericCatalogProvider(sid, label, creds, **kw)
    if sid.startswith("mcp:"):
        if mcp_invoke is None:
            raise CatalogError("the MCP source needs a tool invoker (no active MCP session)")
        roles = resolve_roles_by_shape(available_tools or [])
        if not roles.get("list_tables"):
            raise CatalogError("the agent's MCP server exposes no tool that lists tables")
        return McpCatalogProvider(sid, mcp_label or "MCP data source", mcp_invoke, roles, dialect_hint)
    raise CatalogError(f"unknown source '{source_id}'")
