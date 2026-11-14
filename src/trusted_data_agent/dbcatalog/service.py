"""NavigatorService — the ONE implementation behind both faces of the Database
Navigator: the ``TDA_DatabaseNavigator`` tool the agent calls and the REST endpoints
the panel calls. Each answer is built once here: provider resolution, the cache,
the deterministic intelligence, the ontology overlay, the trusted scope and PII
masking. A face renders; it never re-derives.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .base import CatalogError, CatalogProvider
from .cache import CACHE
from .capabilities import CatalogCapability as C
from . import intelligence as I
from .resolution import build_provider, resolve_sources
from .types import ColumnInfo, DatabaseInfo, ObjectDetail, ObjectInfo, PreviewResult
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")

MASK = "•••"


def mask_value(v: Any) -> Any:
    """Keep the SHAPE of a value (first character, length class), never its content."""
    if v is None or v == "":
        return v
    s = str(v)
    return (s[0] + MASK) if len(s) > 1 else MASK


def mask_preview(preview: PreviewResult, pii_columns: Sequence[str]) -> PreviewResult:
    low = {c.lower() for c in pii_columns}
    idx = [i for i, c in enumerate(preview.columns) if c.lower() in low]
    if not idx:
        return preview
    preview.rows = [[mask_value(v) if i in idx else v for i, v in enumerate(r)] for r in preview.rows]
    preview.masked_columns = [preview.columns[i] for i in idx]
    return preview


# Connection driver name → sqlglot dialect name. Only the names that differ are listed.
SQLGLOT_DIALECT = {"postgresql": "postgres", "psycopg2": "postgres", "pg": "postgres",
                   "mariadb": "mysql", "mssql": "tsql", "sqlserver": "tsql"}
# A source whose statements are NOT SQL names its query language instead (no SQL consumer
# may parse it): MongoDB statements are JSON (core.mongo_query).


class NavigatorService:
    def __init__(self, *, store=None, runner=None, cache=CACHE, ontology_store_factory=None,
                 trusted_scope_fn=None):
        self._store, self._runner, self._cache = store, runner, cache
        self._ontology_store_factory = ontology_store_factory
        self._trusted_scope_fn = trusted_scope_fn

    # ── sources ───────────────────────────────────────────────────────────────

    def sources(self, user_uuid: str, profile_config: Optional[Dict[str, Any]] = None,
                available_tools: Optional[List[Dict[str, Any]]] = None, mcp_label: str = "",
                mcp_server_id: str = "") -> Dict[str, Any]:
        return resolve_sources(user_uuid, profile_config, available_tools, mcp_label, mcp_server_id,
                               store=self._store)

    def provider(self, user_uuid: str, source_id: str, *, mcp_invoke=None, available_tools=None,
                 mcp_label: str = "", dialect_hint: str = "") -> CatalogProvider:
        return build_provider(source_id, user_uuid, store=self._store, mcp_invoke=mcp_invoke,
                              available_tools=available_tools, mcp_label=mcp_label, runner=self._runner,
                              dialect_hint=dialect_hint)

    @staticmethod
    def dialect_of(provider: CatalogProvider) -> str:
        """The provider's SQL dialect, named as sqlglot names it (every SQL consumer —
        preflight, rewrites, table extraction — parses with sqlglot)."""
        d = getattr(provider, "driver", "") or ("teradata" if provider.provider_kind == "direct" else "")
        return SQLGLOT_DIALECT.get((d or "teradata").lower(), (d or "teradata").lower())

    # ── reads (cached) ────────────────────────────────────────────────────────

    async def databases(self, user_uuid: str, provider: CatalogProvider, refresh: bool = False) -> List[DatabaseInfo]:
        if not refresh:
            hit = self._cache.get(user_uuid, provider.source_id, "databases")
            if hit is not None:
                return hit
        dbs = await provider.list_databases()
        return self._cache.put(user_uuid, provider.source_id, "databases", value=dbs)

    async def objects(self, user_uuid: str, provider: CatalogProvider, database: str,
                      refresh: bool = False) -> List[ObjectInfo]:
        if not refresh:
            hit = self._cache.get(user_uuid, provider.source_id, "objects", database)
            if hit is not None:
                return hit
        objs = await provider.list_objects(database)
        return self._cache.put(user_uuid, provider.source_id, "objects", database, value=objs)

    async def _peers(self, user_uuid: str, provider: CatalogProvider, database: str,
                     exclude: str, limit: int = 40) -> List[Tuple[ObjectInfo, List[ColumnInfo]]]:
        """Column lists of the sibling tables already DESCRIBED (cached) — join inference
        never triggers a dictionary read per sibling; it grows as the user browses."""
        out = []
        for o in await self.objects(user_uuid, provider, database):
            if o.name.lower() == exclude.lower() or o.kind != "table":
                continue
            hit = self._cache.get(user_uuid, provider.source_id, "detail", database, o.name)
            if hit is not None:
                out.append((o, hit.columns))
            if len(out) >= limit:
                break
        return out

    async def describe(self, user_uuid: str, provider: CatalogProvider, database: str, name: str,
                       *, profile_id: Optional[str] = None, refresh: bool = False,
                       ontology_store=None, scope_dbs: Optional[Sequence[str]] = None) -> ObjectDetail:
        asked = database
        database = await self._resolve_database(user_uuid, provider, database, name)
        detail = None if refresh else self._cache.get(user_uuid, provider.source_id, "detail", database, name)
        if detail is None:
            detail = await provider.describe_object(database, name)
            self._cache.put(user_uuid, provider.source_id, "detail", database, detail.info.name, value=detail)
        ontology: Dict[str, Any] = {"present": False}
        if ontology_store is None and self._ontology_store_factory and profile_id:
            try:
                ontology_store = self._ontology_store_factory(profile_id, user_uuid)
            except Exception:  # noqa: BLE001
                ontology_store = None
        if ontology_store is not None:
            from .overlay import ontology_overlay
            ontology = await ontology_overlay(ontology_store, database, detail.info.name, detail.columns)
        if scope_dbs is None and self._trusted_scope_fn and profile_id:
            try:
                scope_dbs, _ents = await self._trusted_scope_fn(profile_id, user_uuid)
            except Exception:  # noqa: BLE001
                scope_dbs = None
        peers = await self._peers(user_uuid, provider, database, detail.info.name)
        I.enrich_detail(detail, peers, provider.capabilities(), self.dialect_of(provider), scope_dbs,
                        bool(ontology.get("present")))
        detail.ontology = ontology
        # ontology-declared PII is evidence the dictionary lacks → the flag joins the column
        for c in detail.columns:
            if c.ontology.get("is_pii") and "pii" not in c.flags:
                c.flags.append("pii")
        if asked and str(asked).lower() != str(database).lower():
            detail.intelligence["resolved_database"] = {"asked": asked, "used": database}
        detail.intelligence["source"] = {"id": provider.source_id, "kind": provider.provider_kind,
                                         "label": provider.label,
                                         "capabilities": sorted(x.value for x in provider.capabilities())}
        return detail

    # ── search ────────────────────────────────────────────────────────────────
    #: How many user databases a scan reads when the source has no dictionary search, and when a
    #: described object's database has to be found. A source with more is searched by name only
    #: in what has been browsed, and says so (``scope``).
    SCAN_MAX_DATABASES = 25

    async def _user_databases(self, user_uuid: str, provider: CatalogProvider) -> List[DatabaseInfo]:
        return [d for d in await self.databases(user_uuid, provider) if not d.is_system]

    async def _resolve_database(self, user_uuid: str, provider: CatalogProvider,
                                database: Optional[str], name: str) -> str:
        """The source's own name for ``database``. A name the source does not have (a label from a
        description — "books_database" for PostgreSQL's ``public``) is resolved to the one
        database that holds ``name``; otherwise refused NAMING the source's databases, never "not
        in the catalog — refresh", which sends the agent to retry a name that cannot exist."""
        dbs = await self.databases(user_uuid, provider)
        by_lower = {d.name.lower(): d.name for d in dbs}
        if database and str(database).lower() in by_lower:
            return by_lower[str(database).lower()]
        users = [d for d in dbs if not d.is_system][: self.SCAN_MAX_DATABASES]
        holders = []
        for d in users:
            try:
                objs = await self.objects(user_uuid, provider, d.name)
            except Exception:  # noqa: BLE001 — one unreadable database is not the answer
                continue
            if any(o.name.lower() == str(name or "").lower() for o in objs):
                holders.append(d.name)
        if len(holders) == 1:
            return holders[0]
        from .base import CatalogError
        where = (f" '{name}' is in: {', '.join(holders)} — pass one of them as database." if holders
                 else f" No database of this source holds an object named '{name}'.")
        raise CatalogError(
            (f"'{database}' is not a database of this source" if database else "No database was given")
            + f"; its databases are: {', '.join(d.name for d in users) or '(none)'}." + where)


    async def search(self, user_uuid: str, provider: CatalogProvider, q: str, *, database: Optional[str] = None,
                     limit: int = 30, include_system: bool = False) -> Dict[str, Any]:
        q = (q or "").strip()
        terms = I.search_terms(q)
        if not terms:
            return {"query": q, "hits": [], "scope": "none"}
        dbs = await self.databases(user_uuid, provider)
        objects: List[ObjectInfo] = []
        columns: Dict[str, List[ColumnInfo]] = {}
        scope = "cached"
        if provider.has(C.SEARCH):
            scope = "dictionary"
            term = max(terms, key=len)
            objects = await provider.search_objects(term)
            for c in await provider.search_columns(term):
                key = f"{c['database']}.{c['object']}"
                columns.setdefault(key, []).append(ColumnInfo(name=c["column"], data_type=c.get("data_type", ""),
                                                              comment=c.get("comment", "")))
        else:
            # No dictionary search: list the source's user databases (bounded) so the answer is
            # about the source, not about what happened to be browsed — "0 objects matching"
            # over nothing scanned is a false absence.
            users = await self._user_databases(user_uuid, provider)
            for d in users[: self.SCAN_MAX_DATABASES]:
                try:
                    await self.objects(user_uuid, provider, d.name)
                except Exception:  # noqa: BLE001
                    continue
            scope = "listed" if len(users) <= self.SCAN_MAX_DATABASES else "partial"
        # what the user has already browsed always joins the pool (cheap and exact)
        seen = {(o.database.lower(), o.name.lower()) for o in objects}
        for d in dbs:
            cached = self._cache.get(user_uuid, provider.source_id, "objects", d.name)
            if cached:
                for o in cached:
                    if (o.database.lower(), o.name.lower()) not in seen:
                        objects.append(o)
                        seen.add((o.database.lower(), o.name.lower()))
                    det = self._cache.get(user_uuid, provider.source_id, "detail", d.name, o.name)
                    if det is not None and o.qualified not in columns:
                        columns[o.qualified] = det.columns
        if database:
            for o in await self.objects(user_uuid, provider, database):
                if (o.database.lower(), o.name.lower()) not in seen:
                    objects.append(o)
                    seen.add((o.database.lower(), o.name.lower()))
        for qualified, cols in columns.items():
            I.annotate_columns(cols, str(qualified).split(".")[-1])
        # system databases (DBC, TD_SYSFNLIB, val …) hold the SYSTEM, not the estate — a
        # dictionary-wide search must not rank `val.customer` above the user's own tables
        if not include_system:
            system = {d.name.lower() for d in dbs if d.is_system}
            dbs = [d for d in dbs if not d.is_system]
            objects = [o for o in objects if o.database.lower() not in system]
            columns = {k: v for k, v in columns.items() if k.partition(".")[0].lower() not in system}
        hits = I.rank_search(q, dbs, objects, columns, limit=limit)
        if database:
            hits.sort(key=lambda h: (0 if str(h.get("database", "")).lower() == database.lower() else 1, -h["score"]))
        return {"query": q, "hits": hits, "scope": scope, "system_hidden": not include_system}

    # ── data ──────────────────────────────────────────────────────────────────

    async def query(self, user_uuid: str, provider: CatalogProvider, sql: str,
                    max_rows: int) -> Dict[str, Any]:
        """An agent's read-only query → ``{columns, rows, truncated, elapsed_ms}``, at most
        ``max_rows`` rows. The protections (authority, preflight, registry, firewall…) are
        the caller's, through ``tool_enforcement.guarded_sql_call``; this is execution only."""
        t0 = time.monotonic()
        cols, rows = await provider.run_select(sql, max_rows + 1)
        return {"columns": list(cols), "rows": [list(r) for r in rows[:max_rows]],
                "truncated": len(rows) > max_rows, "elapsed_ms": int((time.monotonic() - t0) * 1000)}

    async def preview(self, user_uuid: str, provider: CatalogProvider, database: str, name: str,
                      limit: int = 50, *, unmask: bool = False, profile_id: Optional[str] = None) -> PreviewResult:
        if not provider.has(C.PREVIEW):
            raise CatalogError(f"{provider.label} cannot preview rows (no read-only SQL path on this source)")
        detail = await self.describe(user_uuid, provider, database, name, profile_id=profile_id)
        if detail.info.kind not in ("table", "view"):
            raise CatalogError(f"{detail.info.qualified} is a {detail.info.kind}; only tables and views preview")
        t0 = time.monotonic()
        pv = await provider.preview(detail.info.database or database, detail.info.name, limit)
        pv.elapsed_ms = int((time.monotonic() - t0) * 1000)
        pii = [c.name for c in detail.columns if "pii" in c.flags]
        return pv if unmask else mask_preview(pv, pii)

    async def count(self, user_uuid: str, provider: CatalogProvider, database: str, name: str) -> Dict[str, Any]:
        if not provider.has(C.ROW_COUNT):
            raise CatalogError(f"{provider.label} cannot count rows on this source")
        database = await self._resolve_database(user_uuid, provider, database, name)
        t0 = time.monotonic()
        n = await provider.row_count(database, name)
        det = self._cache.get(user_uuid, provider.source_id, "detail", database, name)
        if det is not None and n is not None:
            det.info.row_count, det.info.row_count_source = n, "exact"
        return {"row_count": n, "elapsed_ms": int((time.monotonic() - t0) * 1000), "source": "exact"}

    @_signals.announces("database_navigator", "refreshed", user="user_uuid", ids=("source_id",))
    def refresh(self, user_uuid: str, source_id: Optional[str] = None) -> int:
        return self._cache.invalidate(user_uuid, source_id)


def _default_ontology_store(profile_id: str, user_uuid: str):
    """The agent's bound ontology, resolved exactly as the KG component resolves it
    (owner via the assignment table, Model B released view honoured). None when the
    KG component is absent or the agent has no graph."""
    try:
        from trusted_data_agent.components.manager import get_component_manager
        h = get_component_manager().get_handler("TDA_KnowledgeGraph")
        if h is None:
            return None
        active_owner = h._get_active_kg_owner(profile_id, user_uuid)
        kg_pid = active_owner or profile_id
        store, _g = h._resolve_grounding_store(kg_pid, user_uuid, profile_id)
        return store
    except Exception:  # noqa: BLE001
        return None


async def _default_trusted_scope(profile_id: str, user_uuid: str):
    from trusted_data_agent.components.manager import get_component_trusted_scope
    return await get_component_trusted_scope(profile_id, user_uuid)


_SERVICE: Optional[NavigatorService] = None


def get_navigator_service() -> NavigatorService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = NavigatorService(ontology_store_factory=_default_ontology_store,
                                    trusted_scope_fn=_default_trusted_scope)
    return _SERVICE
