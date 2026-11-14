"""MongoDB catalog provider — a document store read through the same contract as the
relational providers. A database is a database, a collection reads as a table (a view as
a view), and a collection's FIELDS are its columns.

MongoDB declares no schema, so fields are SAMPLED: ``describe`` reads up to
``SAMPLE_SIZE`` documents and reports every field it saw, typed by every BSON type it was
seen with, with the share of sampled documents that carry it. The detail says so in
``unavailable`` ("a declared schema — fields sampled from N documents") — a sampled field
list is never presented as the collection's schema.

Statements (the agent's ``query``, preview, count) run through ``core.mongo_client``,
the same executor the Canvas uses, with the read-only stage allowlist applied.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Set

from .base import CatalogError, CatalogProvider
from .capabilities import CatalogCapability as C
from .types import ColumnInfo, DatabaseInfo, IndexInfo, ObjectDetail, ObjectInfo, PreviewResult

logger = logging.getLogger("quart.app")

SAMPLE_SIZE = 200
_SYSTEM_DBS = frozenset({"admin", "local", "config"})


class MongoCatalogProvider(CatalogProvider):
    provider_kind = "direct"
    driver = "mongodb"

    def __init__(self, source_id: str, label: str, creds: Dict[str, Any],
                 client_factory: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 sample_size: int = SAMPLE_SIZE):
        self._id, self._label, self._creds = source_id, label, dict(creds)
        self._sample = max(10, int(sample_size))
        if client_factory is None:
            from trusted_data_agent.core.mongo_client import client_for
            client_factory = client_for
        self._client_factory = client_factory

    @property
    def source_id(self) -> str:
        return self._id

    @property
    def label(self) -> str:
        return self._label

    def capabilities(self) -> Set[C]:
        # no comments, no foreign keys, no statistics: MongoDB declares none of them
        return {C.LIST_DATABASES, C.LIST_OBJECTS, C.COLUMNS, C.INDEXES, C.VIEW_TEXT, C.ROW_COUNT,
                C.PREVIEW, C.OBJECT_KINDS}

    # ── plumbing ────────────────────────────────────────────────────────────────

    async def _call(self, fn: Callable, *a, **kw):
        try:
            return await asyncio.to_thread(fn, *a, **kw)
        except CatalogError:
            raise
        except Exception as exc:  # noqa: BLE001 — a driver error becomes a user-facing sentence
            from trusted_data_agent.core.mongo_query import MongoQueryError
            if isinstance(exc, MongoQueryError):
                raise
            raise CatalogError(f"MongoDB: {str(exc)[:300]}") from exc

    def _client(self):
        return self._client_factory(self._creds)

    @property
    def _conn_db(self) -> str:
        return str(self._creds.get("database") or "").strip()

    # ── databases / collections ────────────────────────────────────────────────

    def _list_databases(self) -> List[DatabaseInfo]:
        client = self._client()
        names = [self._conn_db] if self._conn_db else client.list_database_names()
        out = []
        for n in names:
            try:
                # MongoDB's own bookkeeping (system.views, system.profile …) is not the user's data
                count = len([c for c in client[n].list_collection_names() if not c.startswith("system.")])
            except Exception:  # noqa: BLE001 — a login may list a database it cannot read
                count = None
            out.append(DatabaseInfo(name=n, kind="database", object_count=count, is_system=n in _SYSTEM_DBS))
        return out

    async def list_databases(self) -> List[DatabaseInfo]:
        return await self._call(self._list_databases)

    def _check_db(self, database: str) -> str:
        if self._conn_db and database != self._conn_db:
            raise CatalogError(f"this connection is scoped to database '{self._conn_db}' — '{database}' is outside it")
        if not database or "\x00" in database:
            raise CatalogError("name a database")
        return database

    def _list_objects(self, database: str) -> List[ObjectInfo]:
        db = self._client()[self._check_db(database)]
        out = []
        for c in db.list_collections():
            name = str(c.get("name") or "")
            if not name or name.startswith("system."):
                continue
            ctype = str(c.get("type") or "collection")
            info = ObjectInfo(database=database, name=name, kind="view" if ctype == "view" else "table",
                              extra={"document_kind": ctype})
            if ctype != "view":
                try:
                    # the collection's metadata count — cheap, and labelled as not exact
                    info.row_count = int(db[name].estimated_document_count(maxTimeMS=5000))
                    info.row_count_source = "statistics"
                except Exception:  # noqa: BLE001
                    pass
            out.append(info)
        out.sort(key=lambda o: o.name.lower())
        return out

    async def list_objects(self, database: str) -> List[ObjectInfo]:
        return await self._call(self._list_objects, database)

    # ── describe ────────────────────────────────────────────────────────────────

    def _describe(self, database: str, name: str) -> ObjectDetail:
        from trusted_data_agent.core import mongo_query as MQ
        objs = {o.name.lower(): o for o in self._list_objects(database)}
        info = objs.get(name.lower())
        if info is None:
            raise CatalogError(f"{database}.{name} is not in the catalog — refresh and try again.")
        name = info.name
        db = self._client()[database]
        coll = db[name]
        detail = ObjectDetail(info=info)
        # sampled documents: $sample reads a random spread; a view cannot $sample on every
        # server version, so it reads the first documents instead
        try:
            if info.kind == "view":
                docs = list(coll.find({}, limit=self._sample, max_time_ms=15000))
            else:
                docs = list(coll.aggregate([{"$sample": {"size": self._sample}}], maxTimeMS=15000))
        except Exception as exc:  # noqa: BLE001
            docs = []
            detail.unavailable.append(f"field sample ({str(exc)[:120]})")
        fields = MQ.sample_fields(docs)
        n = len(docs)
        unique_fields: Set[str] = set()
        indexes: List[IndexInfo] = []
        try:
            for iname, spec in (coll.index_information() or {}).items():
                keys = [str(k) for k, _d in (spec.get("key") or [])]
                uniq = bool(spec.get("unique")) or iname == "_id_"
                kind = "primary" if iname == "_id_" else ("unique" if uniq else "secondary")
                indexes.append(IndexInfo(name=iname, kind=kind, columns=keys, unique=uniq))
                if uniq and len(keys) == 1:
                    unique_fields.add(keys[0])
        except Exception as exc:  # noqa: BLE001 — a view has no indexes; a login may not read them
            if info.kind != "view":
                detail.unavailable.append(f"indexes ({str(exc)[:80]})")
        detail.indexes = indexes
        cols = []
        for pos, f in enumerate(fields, start=1):
            types = f["types"] or ["null"]
            cols.append(ColumnInfo(
                name=f["name"], data_type="|".join(types), nullable=f["present"] < n or "null" in types,
                position=pos, is_primary=f["name"] == "_id", is_unique=f["name"] in unique_fields,
                stats={"sampled_documents": n, "present_in": f["present"],
                       "presence": round(f["present"] / n, 3) if n else None}))
        detail.columns = cols
        if info.kind == "view":
            try:
                opts = next((c for c in db.list_collections(filter={"name": name})), {}) or {}
                o = opts.get("options") or {}
                if o.get("pipeline") is not None:
                    detail.view_text = MQ.to_text({"collection": o.get("viewOn") or "", "pipeline": o["pipeline"]})
            except Exception:  # noqa: BLE001
                pass
        total = info.row_count
        if total is not None and n >= total:
            read = f"read from all {n:,} document{'' if n == 1 else 's'}"
        else:
            read = (f"sampled from {n:,} document{'' if n == 1 else 's'}"
                    + (f" of about {total:,}" if total is not None else ""))
        detail.unavailable.append(f"a declared schema — fields {read}")
        return detail

    async def describe_object(self, database: str, name: str) -> ObjectDetail:
        self._check_db(database)
        return await self._call(self._describe, database, name)

    # ── data ────────────────────────────────────────────────────────────────────

    def _run(self, statement: Any, max_rows: int) -> Dict[str, Any]:
        from trusted_data_agent.core import mongo_client as MC
        return MC.run_statement(self._creds, statement, max_rows=max_rows, readonly=True)

    async def preview(self, database: str, name: str, limit: int = 50,
                      columns: Optional[List[str]] = None) -> PreviewResult:
        self._check_db(database)
        limit = max(1, min(int(limit or 50), 500))
        pipeline: List[Dict[str, Any]] = []
        if columns:
            pipeline.append({"$project": {c: 1 for c in columns}})
        stmt = {"collection": name, "pipeline": pipeline}
        if not self._conn_db:
            stmt["database"] = database
        out = await self._call(self._run, stmt, limit)
        return PreviewResult(columns=out["columns"], rows=out["rows"], truncated=out["truncated"],
                             sql=out["text"])

    async def run_select(self, statement: str, max_rows: int):
        """An agent's MongoDB statement (JSON: collection + pipeline) → ``(columns, rows)``,
        read-only by the stage allowlist, bounded on the server."""
        out = await self._call(self._run, statement, max_rows)
        return out["columns"], out["rows"]

    def _count(self, database: str, name: str) -> int:
        return int(self._client()[database][name].count_documents({}, maxTimeMS=60000))

    async def row_count(self, database: str, name: str) -> Optional[int]:
        self._check_db(database)
        return await self._call(self._count, database, name)

    # ── the login's rights (the database's half of read-only) ─────────────────────

    async def login_rights(self, refresh: bool = False) -> Dict[str, Any]:
        """``{can_write, reason, user}`` for this connection's login (cached ten minutes)."""
        from trusted_data_agent.core import mongo_client as MC
        return await asyncio.to_thread(MC.login_rights, self._creds, refresh=refresh)

    @property
    def writable_login_allowed(self) -> bool:
        """The connection owner's explicit consent to agent queries on a login that can write."""
        from trusted_data_agent.core.mongo_query import writable_login_allowed
        return writable_login_allowed(self._creds)

    async def test(self) -> bool:
        try:
            await self.list_databases()
            return True
        except Exception:  # noqa: BLE001
            return False


def mongo_capabilities() -> List[str]:
    return sorted(c.value for c in MongoCatalogProvider("", "", {}, client_factory=lambda c: None).capabilities())

