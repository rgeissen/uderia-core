"""An agent bound to several databases is told which is which; a name the source does not have
is resolved or refused NAMING the real ones; search never reports "0 found" over nothing read.

Found on the first DataAgentBench run (bookreview, Gemma 4 12B on MLX, 2026-09-24): the agent
passed DAB's logical store name ("books_database") as the database of a PostgreSQL source whose
table lives in `public`; describe answered "not in the catalog — refresh and try again", and
`search 'book'` answered "0 objects" because it only looked at what had been browsed. The agent
ended the turn asking the user for the schema.

No server, no network, no API spend.
"""
import asyncio
import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

from trusted_data_agent.dbcatalog import (CatalogCapability as C, CatalogError, DatabaseInfo, ObjectInfo,  # noqa: E402
                                          cache as CH, service as SV)
from trusted_data_agent.dbcatalog.types import ObjectDetail, ColumnInfo  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f": {detail}" if detail is not None else ""))


class Prov:
    """A PostgreSQL-shaped source: system schemas + `public` (+ an optional second schema).
    No dictionary search (the generic information_schema provider has none)."""
    source_id, provider_kind, label, driver = "conn:pg", "direct", "PG", "postgresql"

    def __init__(self, schemas):
        self.schemas = schemas            # {schema: [table, ...]}
        self.listed = []

    def has(self, cap): return cap in (C.PREVIEW, C.ROW_COUNT)
    def capabilities(self): return {C.PREVIEW, C.ROW_COUNT}

    async def list_databases(self):
        return ([DatabaseInfo(name="information_schema", kind="schema", is_system=True),
                 DatabaseInfo(name="pg_catalog", kind="schema", is_system=True)]
                + [DatabaseInfo(name=s, kind="schema") for s in self.schemas])

    async def list_objects(self, database):
        self.listed.append(database)
        return [ObjectInfo(database=database, name=t, kind="table") for t in self.schemas.get(database, [])]

    async def describe_object(self, database, name):
        if name not in self.schemas.get(database, []):
            raise CatalogError(f"{database}.{name} is not in the catalog — refresh and try again.")
        return ObjectDetail(info=ObjectInfo(database=database, name=name, kind="table"),
                            columns=[ColumnInfo(name="book_id", data_type="text")])

    async def row_count(self, database, name):
        return 200 if name in self.schemas.get(database, []) else None


def svc():
    return SV.NavigatorService(store=None, runner=None, cache=CH.CatalogCache())


run = asyncio.run

print("[1] a database name the source does not have")
p, s = Prov({"public": ["books_info"]}), svc()
try:
    d = run(s.describe("u", p, "books_database", "books_info"))
except CatalogError as e:
    d = None
    check("…is resolved to the one database that holds the object", False, f"refused: {e}")
if d is not None:
    check("…is resolved to the one database that holds the object", d.info.qualified == "public.books_info", d.info.qualified)
    check("…and the resolution is recorded for the agent",
          d.intelligence.get("resolved_database") == {"asked": "books_database", "used": "public"}, d.intelligence.get("resolved_database"))
try:
    check("a name the source HAS is used as asked, case-insensitively, with no note",
          run(s.describe("u", p, "PUBLIC", "books_info")).intelligence.get("resolved_database") is None)
except CatalogError as e:
    check("a name the source HAS is used as asked, case-insensitively, with no note", False, f"refused: {e}")
try:
    run(svc().describe("u", Prov({"public": ["books_info"]}), "books_database", "nope"))
    check("an object nowhere is refused", False)
except CatalogError as e:
    m = str(e)
    check("…refused naming the source's databases, never 'refresh and try again'",
          "its databases are: public" in m and "refresh" not in m and "information_schema" not in m, m)
try:
    run(svc().describe("u", Prov({"a": ["t"], "b": ["t"]}), "x", "t"))
    check("an ambiguous object is refused", False)
except CatalogError as e:
    check("…naming where it is, never guessing one", "'t' is in: a, b" in str(e), str(e))
p = Prov({"public": ["books_info"]})
try:
    check("count resolves the same way", run(svc().count("u", p, "books_database", "books_info"))["row_count"] == 200)
except Exception as e:  # noqa: BLE001
    check("count resolves the same way", False, repr(e))

print("[2] search on a source with no dictionary search")
p, s = Prov({"public": ["books_info", "authors"], "sales": ["orders"]}), svc()
r = run(s.search("u", p, "book"))
check("lists the source's user databases first, so the table is found",
      [(h.get("database"), h.get("object")) for h in r["hits"]][:1] == [("public", "books_info")], r["hits"])
check("…and says it read the whole source", r["scope"] == "listed", r["scope"])
check("…never the system schemas", "information_schema" not in p.listed and "pg_catalog" not in p.listed, p.listed)
big = Prov({f"s{i}": [] for i in range(SV.NavigatorService.SCAN_MAX_DATABASES + 3)})
r = run(svc().search("u", big, "orders"))
check("a source with more databases than the bound says the read was partial",
      r["scope"] == "partial" and len(big.listed) == SV.NavigatorService.SCAN_MAX_DATABASES, (r["scope"], len(big.listed)))

print("[3] the agent is told its databases, by name, dialect and tables")
from components.builtin.database_navigator import handler as H   # noqa: E402

tmp = tempfile.mkdtemp()
dbf = os.path.join(tmp, "review_query.db")
con = sqlite3.connect(dbf); con.execute("CREATE TABLE review (rating REAL, purchase_id TEXT)"); con.commit(); con.close()


class Store:
    conns = [{"id": "c_pg", "name": "DAB bookreview · books_database", "driver": "postgresql", "host": "h", "database": "bookreview_db"},
             {"id": "c_sq", "name": "DAB bookreview · review_database", "driver": "sqlite", "database": dbf},
             {"id": "c_other", "name": "not allowed", "driver": "sqlite", "database": dbf}]
    def list(self, uid, **_k): return self.conns
    def get(self, uid, cid): return next((c for c in self.conns if c["id"] == cid), None)
    def resolve(self, uid, cid): return self.get(uid, cid)


PROFILE = {"componentConfig": {"database_navigator": {"enabled": True, "query": {"enabled": True, "sources": ["conn:c_pg", "conn:c_sq"]}}}}


class _CM:
    def __init__(self, prof): self.prof = prof
    def get_profile(self, pid, uid): return self.prof


def enrich(prof):
    service = SV.NavigatorService(store=Store(), cache=CH.CatalogCache())
    pg = Prov({"public": ["books_info"]})
    real_provider = service.provider

    def _provider(uid, sid, **kw):
        return pg if sid == "conn:c_pg" else real_provider(uid, sid, **kw)

    service.provider = _provider
    with patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=_CM(prof)), \
         patch("trusted_data_agent.dbcatalog.service.get_navigator_service", return_value=service), \
         patch("trusted_data_agent.core.database_connections.get_database_connection_store", return_value=Store()):
        return run(H.DatabaseNavigatorHandler().get_context_enrichment("q", "p1", "u1"))


t = enrich(PROFILE)
check("each allowed source is one line with its source_id and name",
      "source_id=conn:c_pg — DAB bookreview · books_database" in t and "source_id=conn:c_sq — DAB bookreview · review_database" in t, t)
check("…its dialect and the database it opens (a file by its name, never the path)",
      "PostgreSQL SQL; database bookreview_db" in t and "SQLite SQL; file review_query.db" in t and tmp not in t, t)
check("…and its tables as SQL names them", "objects: public.books_info" in t and "objects: main.review" in t, t)
check("a source the agent may NOT query is not listed", "not allowed" not in t and "c_other" not in t)
check("with several sources, the one-source-per-query rule is stated", "reads ONE source" in t)
check("an agent that may not query is told nothing",
      enrich({"componentConfig": {"database_navigator": {"enabled": True}}}) == "")
import json  # noqa: E402
m = json.load(open(os.path.join(ROOT, "components/builtin/database_navigator/manifest.json")))
check("the manifest declares the enrichment (or it is never asked)", m["backend"].get("supports_context_enrichment") is True)

print("[4] an object named without its source is answered by the one source that holds it")
# Found on the 12B 4-bit run (2026-09-25): the agent described `review` in 'review_query.db' with no
# source_id; the default (PostgreSQL) source refused, and the model repeated the identical call ~20
# times. And: a SELECT passed as `query_text` (the search argument) was refused on every attempt.


def process(prof, args):
    service = SV.NavigatorService(store=Store(), cache=CH.CatalogCache())
    pg = Prov({"public": ["books_info"]})
    real_provider = service.provider
    service.provider = lambda uid, sid, **kw: pg if sid == "conn:c_pg" else real_provider(uid, sid, **kw)
    with patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=_CM(prof)), \
         patch("trusted_data_agent.dbcatalog.service.get_navigator_service", return_value=service), \
         patch("trusted_data_agent.core.database_connections.get_database_connection_store", return_value=Store()):
        return run(H.DatabaseNavigatorHandler().process(args, {"user_uuid": "u1", "profile_id": "p1",
                                                               "session_id": ""}))


out = process(PROFILE, {"action": "describe", "database": "review_query.db", "object_name": "review"})
sp = out.spec
check("the default source does not hold it, the other one does → answered, no error",
      not sp.get("error") and sp.get("object") == "review", (sp.get("error"), sp.get("message")))
check("…from the source that holds it", (sp.get("source") or {}).get("id") == "conn:c_sq", sp.get("source"))
check("…and the answer names the source_id to pass next time",
      "source_id='conn:c_sq'" in str(sp.get("message")) and "is not in the default source" in str(sp.get("message")),
      sp.get("message"))
out = process(PROFILE, {"action": "describe", "database": "review_query.db", "object_name": "review",
                        "source_id": "conn:c_pg"})
check("an explicit source_id is never overridden — the refusal stands",
      out.spec.get("error") == "catalog" and (out.spec.get("source") or {}).get("label") == "PG", out.spec)
out = process(PROFILE, {"action": "describe", "database": "x", "object_name": "nowhere"})
check("an object no source holds keeps the original refusal", out.spec.get("error") == "catalog", out.spec)
out = process(PROFILE, {"action": "describe", "database": "public", "object_name": "books_info"})
check("an object the default source holds is not re-routed",
      not out.spec.get("error") and "rerouted_from" not in out.spec and (out.spec.get("source") or {}).get("label") == "PG",
      out.spec)
out = process(PROFILE, {"action": "query", "source_id": "conn:c_sq",
                        "query_text": "SELECT COUNT(*) AS n FROM review"})
check("a SELECT given as query_text is run as sql", not out.spec.get("error") and out.spec.get("row_count") == 1,
      (out.spec.get("error"), out.spec.get("message")))
check("…and the answer says so", "arrived in 'query_text'" in str(out.spec.get("message")), out.spec.get("message"))
out = process(PROFILE, {"action": "query", "source_id": "conn:c_sq", "query_text": "books about language"})
check("query_text that is not a SELECT stays the refusal", out.spec.get("error") == "query_needs_sql", out.spec)
check("validate accepts a SELECT in query_text, rejects search words",
      H.DatabaseNavigatorHandler().validate_arguments({"action": "query", "query_text": " select 1"})[0]
      and not H.DatabaseNavigatorHandler().validate_arguments({"action": "query", "query_text": "hi"})[0])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
