#!/usr/bin/env python3
"""MongoDB as a first-class Database Connection — declaration, the read-only allowlist, the
login-rights gate, the catalog provider, the agent's `query` action and the Canvas executor.

WHY THIS GATE EXISTS
MongoDB joins the one driver declaration (core.database_connections.DRIVERS) as the first
connection whose statements are not SQL. Three things could go quietly wrong, and each has
a section here:
  * read-only rests on the PLATFORM (MongoDB has no client-side read-only mode): the stage
    allowlist in core.mongo_query must refuse every way a pipeline can write or run
    JavaScript — nested in $facet, $lookup and $unionWith included — before anything is sent;
  * the DATABASE's half is the login's rights: an agent query on a login that can write is
    refused unless the connection's owner allowed it, and rights that cannot be read are
    never taken as read-only;
  * an agent-written pipeline is a free-formed data operation exactly like raw SQL, so the
    data-surface restriction, the OBDA-READ lane and the honesty checks must see it.

Sections [1]–[5] need no server. [6]–[8] run against a MongoDB on localhost:27017 when one
answers (the DAB benchmark's container) in a throwaway database dropped afterwards, and
against DAB's own databases read-only; they are skipped — and SAID so — otherwise.

Run: python3 test/test_mongodb_driver.py
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import uuid

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
logging.disable(logging.CRITICAL)

PASS = FAIL = 0
SKIPPED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def guarded(fn):
    def run():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — a crash fails BY NAME, never aborts the run
            import traceback
            check(f"{fn.__name__} ran without crashing", False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}")
    run.__name__ = fn.__name__
    return run


def rd(p):
    return open(os.path.join(ROOT, p), encoding="utf-8").read()


from trusted_data_agent.core import database_connections as dc  # noqa: E402
from trusted_data_agent.core import mongo_query as MQ  # noqa: E402
from trusted_data_agent.core import mongo_client as MC  # noqa: E402
from trusted_data_agent.dbcatalog import resolution  # noqa: E402
from trusted_data_agent.dbcatalog.mongo_provider import MongoCatalogProvider  # noqa: E402
from trusted_data_agent.dbcatalog.capabilities import CatalogCapability as C  # noqa: E402

go = asyncio.run


# ── [1] the declaration ─────────────────────────────────────────────────────────
@guarded
def t_declaration():
    D = dc.DRIVERS
    check("[1] mongodb is declared, not a file, language mongodb",
          "mongodb" in D and D["mongodb"]["file"] is False and D["mongodb"]["language"] == "mongodb")
    check("[1] every driver declares its language", all(s.get("language") in ("sql", "mongodb") for s in D.values()))
    check("[1] DOCUMENT_DRIVERS = the non-SQL drivers", dc.DOCUMENT_DRIVERS == ("mongodb",), str(dc.DOCUMENT_DRIVERS))
    check("[1] driver_language: mongodb / postgresql / unknown",
          (dc.driver_language("MongoDB"), dc.driver_language("postgresql"), dc.driver_language("nope"))
          == ("mongodb", "sql", "sql"))
    check("[1] mongodb is a direct Navigator driver", "mongodb" in dc.DIRECT_DRIVERS)
    check("[1] the login fields are metadata (never secrets)",
          "auth_source" in dc.META_FIELDS and "allow_writable_login" in dc.META_FIELDS
          and "allow_writable_login" not in dc.SECRET_FIELDS)
    src = resolution.source_for_connection({"id": "dbconn_m", "name": "m", "driver": "mongodb", "host": "localhost"})
    check("[1] a mongodb connection is a direct source", src is not None and src.kind == "direct" and src.driver == "mongodb")
    caps = set(src.capabilities) if src else set()
    check("[1] its capabilities are the provider's — no comments, keys or statistics claimed",
          {"columns", "preview", "row_count", "indexes"} <= caps and not caps & {"comments", "foreign_keys", "statistics", "sizes"},
          str(sorted(caps)))
    p = resolution.build_provider("conn:dbconn_m", "u", store=_Store([{"id": "dbconn_m", "name": "m", "driver": "mongodb",
                                                                        "host": "localhost"}]))
    check("[1] build_provider returns the MongoDB provider", isinstance(p, MongoCatalogProvider))
    from trusted_data_agent.dbcatalog.service import NavigatorService
    check("[1] its dialect is 'mongodb' (no SQL consumer may parse it)", NavigatorService.dialect_of(p) == "mongodb")

    class CM:
        def __init__(self): self.r = []
        def get_database_connections(self, u): return self.r
        def add_database_connection(self, rec, u): self.r.append(rec); return True
        def update_database_connection(self, cid, meta, u):
            for r in self.r:
                if r["id"] == cid:
                    r.update(meta)
                    return True
            return False

    class Cr:
        def __init__(self): self.s = {}
        def encrypt(self, u, p, c): self.s[p] = dict(c)
        def decrypt(self, u, p): return dict(self.s.get(p, {}))
        def list_providers(self, u): return list(self.s)
    cm, cr = CM(), Cr()
    st = dc.DatabaseConnectionStore(config_manager=cm, crypto=cr)
    rec = st.create("u", {"name": "m", "driver": "mongodb", "host": "localhost", "port": "27017", "username": "r",
                          "password": "pw", "auth_source": "admin", "allow_writable_login": False})
    check("[1] the store accepts a mongodb connection with its login fields",
          rec["driver"] == "mongodb" and rec["auth_source"] == "admin" and rec["allow_writable_login"] is False)
    st.update("u", rec["id"], {"allow_writable_login": True})
    check("[1] the consent is stored and resolvable", st.resolve("u", rec["id"]).get("allow_writable_login") is True)
    check("[1] the password stays a secret", "password" not in cm.r[0])


class _Store:
    def __init__(self, conns): self.conns = conns
    def list(self, uid): return self.conns
    def resolve(self, uid, cid): return next((dict(x) for x in self.conns if x["id"] == cid), None)


# ── [2] the JS mirrors and the form ─────────────────────────────────────────────
@guarded
def t_js():
    pc = rd("static/js/handlers/platformConnectorHandler.js")
    m = re.search(r"const _DBC_DOCUMENT_DRIVERS = \[([^\]]*)\]", pc)
    got = re.findall(r"'([^']+)'", m.group(1)) if m else None
    check("[2] connection form _DBC_DOCUMENT_DRIVERS == DOCUMENT_DRIVERS", got and set(got) == set(dc.DOCUMENT_DRIVERS), str(got))
    check("[2] a passing test on a login that can write reads as a caution, not a pass",
          "function _dbcTestCaution(data)" in pc and pc.count("_dbcTestCaution(data)") == 3 and "'#f59e0b'" in pc)
    check("[2] the form sends the MongoDB fields only for a document driver",
          "if (_DBC_DOCUMENT_DRIVERS.includes(p.driver))" in pc and "p.allow_writable_login" in pc)
    start = pc.index("const _DBC_DRIVERS")
    end = pc.index("async function _loadConnectorDbConnectionsContent")
    js = pc[start:end] + r"""
function mkForm(driver) {
  const els = {};
  const mk = (id) => { const wrap = { style: {}, _lbl: { textContent: 'Database' },
      querySelector: (s) => s === 'label' ? wrap._lbl : null };
    els[id] = { value: driver, placeholder: '', closest: () => wrap, _wrap: wrap }; };
  ['pmcp-dbc-driver','pmcp-dbc-host','pmcp-dbc-port','pmcp-dbc-username','pmcp-dbc-password','pmcp-dbc-database',
   'pmcp-dbc-auth-source','pmcp-dbc-allow-writable'].forEach(mk);
  return { els, querySelector: (s) => els[s.slice(1)] || null };
}
const out = {};
for (const d of ['mongodb', 'postgresql', 'duckdb']) {
  const f = mkForm(d); _dbcApplyDriver(f);
  const e = f.els;
  out[d] = { host: e['pmcp-dbc-host']._wrap.style.display, pwd: e['pmcp-dbc-password']._wrap.style.display,
             auth: e['pmcp-dbc-auth-source']._wrap.style.display, allow: e['pmcp-dbc-allow-writable']._wrap.style.display,
             port: e['pmcp-dbc-port'].placeholder, lbl: e['pmcp-dbc-database']._wrap._lbl.textContent };
}
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        check("[2] the form logic executes", False, r.stderr[-300:])
        return
    o = json.loads(r.stdout.strip().splitlines()[-1])
    check("[2] MongoDB shows host, login, auth database and the consent box",
          o["mongodb"]["host"] == "" and o["mongodb"]["pwd"] == "" and o["mongodb"]["auth"] == "" and o["mongodb"]["allow"] == "",
          str(o["mongodb"]))
    check("[2] MongoDB's port placeholder is 27017 and its database is optional",
          o["mongodb"]["port"] == "27017" and "optional" in o["mongodb"]["lbl"])
    check("[2] a SQL driver hides the MongoDB-only fields", o["postgresql"]["auth"] == "none" and o["postgresql"]["allow"] == "none")
    check("[2] a file driver hides them too", o["duckdb"]["auth"] == "none" and o["duckdb"]["host"] == "none")
    cv = rd("components/builtin/canvas/renderer.js")
    check("[2] the Canvas runs a 'mongodb' statement through the same connector",
          "languages: ['sql', 'mongodb']" in cv and "mongodb: cm.langJson" in cv)
    check("[2] a MongoDB statement never falls back to the MCP SQL bridge",
          "if (state.language === 'mongodb') {" in cv
          and cv.index("if (state.language === 'mongodb') {") < cv.index("return executeSqlViaMcp(code, _credentials, state);"))
    nav = rd("components/builtin/database_navigator/renderer.js")
    check("[2] the navigator opens a MongoDB statement in the canvas as 'mongodb'",
          "language: language === 'mongodb' ? 'mongodb' : 'sql'" in nav and "language: _srcLanguage()" in nav)


# ── [3] the statement: parse, read-only, bound, shape ───────────────────────────
@guarded
def t_statement():
    s = MQ.parse('{"collection": "b", "pipeline": [{"$match": {"x": 1}}]}')
    check("[3] a pipeline statement parses", s == {"collection": "b", "pipeline": [{"$match": {"x": 1}}]}, str(s))
    s = MQ.parse({"collection": "b", "filter": {"x": 1}, "sort": {"y": -1}, "limit": 5, "projection": {"x": 1}})
    check("[3] a find-style statement becomes ONE pipeline, in find's order",
          s["pipeline"] == [{"$match": {"x": 1}}, {"$sort": {"y": -1}}, {"$limit": 5}, {"$project": {"x": 1}}], str(s))
    s = MQ.parse('{"collection": "b", "pipeline": [{"$match": {"at": {"$gte": {"$date": "2020-01-01T00:00:00Z"}}}}]}')
    import datetime
    check("[3] Extended JSON dates parse to dates", isinstance(s["pipeline"][0]["$match"]["at"]["$gte"], datetime.datetime))

    def refused(stmt, code=None):
        try:
            MQ.prepare(stmt, readonly=True)
            return None
        except MQ.MongoQueryError as e:
            return e.code if code is None else (e.code == code, str(e))

    for label, stmt in [
        ("$out", {"collection": "b", "pipeline": [{"$match": {}}, {"$out": "copy"}]}),
        ("$merge", {"collection": "b", "pipeline": [{"$merge": {"into": "x"}}]}),
        ("$merge inside $facet", {"collection": "b", "pipeline": [{"$facet": {"a": [{"$merge": "x"}]}}]}),
        ("$out inside a $lookup sub-pipeline", {"collection": "b", "pipeline": [{"$lookup": {"from": "c", "as": "z", "pipeline": [{"$out": "x"}]}}]}),
        ("$out inside $unionWith", {"collection": "b", "pipeline": [{"$unionWith": {"coll": "c", "pipeline": [{"$out": "x"}]}}]}),
        ("$function in an expression", {"collection": "b", "pipeline": [{"$addFields": {"v": {"$function": {"body": "f", "args": [], "lang": "js"}}}}]}),
        ("$accumulator in a $group", {"collection": "b", "pipeline": [{"$group": {"_id": 1, "a": {"$accumulator": {}}}}]}),
        ("$where in a $match", {"collection": "b", "pipeline": [{"$match": {"$where": "this.a > 1"}}]}),
        ("$where in a find filter", {"collection": "b", "filter": {"$where": "sleep(1)"}}),
        ("an admin stage ($currentOp)", {"collection": "b", "pipeline": [{"$currentOp": {}}]}),
        ("a stage the allowlist does not know", {"collection": "b", "pipeline": [{"$someFutureStage": {}}]}),
        # the allowlist reaches INTO sub-pipelines, not only the operator scan
        ("an admin stage inside a $lookup sub-pipeline", {"collection": "b", "pipeline": [{"$lookup": {"from": "c", "as": "z", "pipeline": [{"$currentOp": {}}]}}]}),
        ("an unknown stage inside $unionWith", {"collection": "b", "pipeline": [{"$unionWith": {"coll": "c", "pipeline": [{"$someFutureStage": {}}]}}]}),
        ("an unknown stage inside $facet", {"collection": "b", "pipeline": [{"$facet": {"a": [{"$listSessions": {}}]}}]}),
    ]:
        check(f"[3] refused as not read-only: {label}", refused(stmt) == "not_read_only", str(refused(stmt)))
    for label, stmt in [
        ("a bare pipeline", '[{"$match": {}}]'),
        ("no collection", {"pipeline": []}),
        ("an unknown key", {"collection": "b", "pipline": []}),
        ("pipeline and find keys together", {"collection": "b", "pipeline": [], "filter": {}}),
        ("a system collection", {"collection": "system.users", "pipeline": []}),
        ("invalid JSON", "{collection: b"),
        ("a SQL statement", "SELECT * FROM b"),
        ("a stage with two operators", {"collection": "b", "pipeline": [{"$match": {}, "$limit": 1}]}),
    ]:
        check(f"[3] refused as invalid: {label}", refused(stmt) == "invalid", str(refused(stmt)))
    check("[3] a read-only pipeline with $lookup/$facet/$unionWith passes",
          refused({"collection": "b", "pipeline": [{"$lookup": {"from": "c", "localField": "a", "foreignField": "a", "as": "z"}},
                                                   {"$facet": {"n": [{"$count": "n"}]}},
                                                   {"$unionWith": {"coll": "c", "pipeline": [{"$match": {}}]}}]}) is None)
    msg = str(refused({"collection": "b", "pipeline": [{"$out": "x"}]}, code="not_read_only"))
    check("[3] the refusal says nothing ran", "nothing was executed" in msg, msg)
    check("[3] the Canvas (readonly off) may run what its owner wrote",
          MQ.prepare({"collection": "b", "pipeline": [{"$out": "x"}]}, readonly=False)["pipeline"][0] == {"$out": "x"})
    check("[3] the bound appends a final $limit", MQ.bounded([{"$match": {}}], 101) == [{"$match": {}}, {"$limit": 101}])
    check("[3] no bound, no $limit", MQ.bounded([{"$match": {}}], 0) == [{"$match": {}}])
    spec = MQ.parse({"collection": "b", "database": "d", "pipeline": [{"$match": {"x": {"$gt": 1}}}]})
    check("[3] the canonical text parses back to the same statement", MQ.parse(MQ.to_text(spec)) == spec, MQ.to_text(spec))
    check("[3] the sample statement is a valid read-only statement",
          MQ.prepare(MQ.sample_statement("b", ["_id", "name", "a.b", "city"], 20))["pipeline"]
          == [{"$project": {"name": 1, "city": 1}}, {"$limit": 20}])
    from bson import ObjectId
    oid = ObjectId()
    cols, rows = MQ.documents_to_rows([{"_id": oid, "a": 1, "n": {"x": 1}}, {"_id": oid, "b": "z", "l": [1, 2]}])
    check("[3] rows: the union of fields in first-seen order", cols == ["_id", "a", "n", "b", "l"], str(cols))
    check("[3] rows: ids as text, nested as JSON, missing as null",
          rows[0][0] == str(oid) and json.loads(rows[0][2]) == {"x": 1} and rows[0][3] is None and json.loads(rows[1][4]) == [1, 2],
          str(rows))
    cols, _r = MQ.documents_to_rows([{f"f{i}": i for i in range(100)}])
    check("[3] rows: at most MAX_COLUMNS columns", len(cols) == MQ.MAX_COLUMNS)
    f = {x["name"]: x for x in MQ.sample_fields([{"a": 1, "n": {"c": "x"}}, {"a": "s"}, {"a": None, "l": [1]}])}
    check("[3] fields: every type a field was seen with, sorted", f["a"]["types"] == ["int32", "string"], str(f["a"]))
    check("[3] fields: presence counted", f["a"]["present"] == 3 and f["n"]["present"] == 1)
    check("[3] fields: nested fields as dot paths", "n.c" in f and f["n.c"]["types"] == ["string"])
    check("[3] fields: array element type named", f["l"]["types"] == ["array<int32>"], str(f["l"]))


# ── [4] the login's rights ──────────────────────────────────────────────────────
@guarded
def t_rights():
    CP = MQ.classify_privileges
    r = CP({"authInfo": {"authenticatedUsers": [], "authenticatedUserPrivileges": []}}, "shop")
    check("[4] no authenticated user = access control off = can write", r["can_write"] is True and "access control is off" in r["reason"])
    read = {"authInfo": {"authenticatedUsers": [{"user": "r", "db": "admin"}], "authenticatedUserPrivileges": [
        {"resource": {"db": "shop", "collection": ""}, "actions": ["find", "listCollections", "collStats", "dbStats"]},
        {"resource": {"db": "admin", "collection": ""}, "actions": ["changeOwnPassword"]}]}}
    check("[4] a read role is read-only", CP(read, "shop")["can_write"] is False)
    rw = {"authInfo": {"authenticatedUsers": [{"user": "w", "db": "admin"}], "authenticatedUserPrivileges": [
        {"resource": {"db": "shop", "collection": ""}, "actions": ["find", "insert", "update", "remove"]}]}}
    r = CP(rw, "shop")
    check("[4] readWrite on the connection's database can write, and says which actions",
          r["can_write"] is True and "insert" in r["reason"] and "shop" in r["reason"], r["reason"])
    rw2 = {"authInfo": {"authenticatedUsers": [{"user": "w", "db": "admin"}], "authenticatedUserPrivileges": [
        {"resource": {"db": "shop", "collection": ""}, "actions": ["convertToCapped", "collMod", "insert", "remove", "update"]}]}}
    check("[4] the reason leads with the actions a reader recognises",
          "can insert, update, remove on database shop" in CP(rw2, "shop")["reason"], CP(rw2, "shop")["reason"])
    check("[4] a write right on ANOTHER database does not count for a scoped connection", CP(rw, "other")["can_write"] is False)
    check("[4] …but counts when the connection names no database", CP(rw, None)["can_write"] is True)
    anyr = {"authInfo": {"authenticatedUsers": [{"user": "root", "db": "admin"}], "authenticatedUserPrivileges": [
        {"resource": {"anyResource": True}, "actions": ["dropDatabase"]}]}}
    check("[4] anyResource write can write", CP(anyr, "shop")["can_write"] is True)
    allc = {"authInfo": {"authenticatedUsers": [{"user": "x", "db": "admin"}], "authenticatedUserPrivileges": [
        {"resource": {"db": "", "collection": ""}, "actions": ["insert"]}]}}
    check("[4] a write on every database (db '') can write", CP(allc, "shop")["can_write"] is True)
    uri, kw = MQ.build_client_kwargs({"host": "db.x", "port": "27018", "username": "u", "password": "p"})
    check("[4] client: host + port, login in the admin database by default",
          uri == "mongodb://db.x:27018/" and kw["username"] == "u" and kw["authSource"] == "admin")
    uri, kw = MQ.build_client_kwargs({"host": "mongodb+srv://c.example.net", "user": "u", "password": "p",
                                      "auth_source": "sales", "ssl": True})
    check("[4] client: an SRV URI is kept, auth source and TLS honoured",
          uri == "mongodb+srv://c.example.net" and kw["authSource"] == "sales" and kw.get("tls") is True)
    check("[4] client: no login → no credentials sent", "username" not in MQ.build_client_kwargs({"host": "h"})[1])
    k1 = MQ.credential_key({"host": "h", "username": "u", "password": "a"})
    k2 = MQ.credential_key({"host": "h", "username": "u", "password": "b"})
    check("[4] the client cache separates passwords and never holds one in clear", k1 != k2 and "a" not in k1.split("|")[-1][:1])
    check("[4] consent reads only an explicit yes",
          [MQ.writable_login_allowed({"allow_writable_login": v}) for v in (True, "true", False, None, "yes")]
          == [True, True, False, False, False])
    check("[4] a database the statement names must match the connection's",
          _raises(lambda: MC.database_of({"database": "a"}, {"database": "b"})))
    check("[4] …and a connection with none needs one named",
          _raises(lambda: MC.database_of({}, {})) and MC.database_of({}, {"database": "z"}) == "z")


def _raises(fn):
    try:
        fn()
        return False
    except MQ.MongoQueryError:
        return True


# ── [5] a pipeline is a free-formed data operation ──────────────────────────────
@guarded
def t_classifiers():
    from trusted_data_agent.agent import obda
    from trusted_data_agent.agent.honesty import primitives as hp
    from trusted_data_agent.core import agent_authority as aa
    doc = {"action": "query", "collection": "business", "pipeline": [{"$count": "n"}]}
    check("[5] OBDA-READ counts a pipeline as free-formed", obda._is_raw_sql(doc) is True)
    check("[5] honesty counts it as a data retrieval attempt", hp._is_raw_sql(doc) is True)
    check("[5] a collection alone is not a query", obda._is_raw_sql({"collection": "b"}) is False
          and hp._is_raw_sql({"collection": "b"}) is False)
    check("[5] SQL still counts", obda._is_raw_sql({"sql": "SELECT 1"}) and hp._is_raw_sql({"sql": "SELECT 1"}))
    scope = aa.parse_scope({"autonomous": True, "dataSurface": {"products": ["p"], "exclusive": True}})
    ok, why = aa.is_tool_allowed(scope, "database_navigator.query",
                                 arguments={"collection": "b", "pipeline": [], "query": "{}"})
    master_off = not ok or not getattr(scope, "data_surface_exclusive", False)
    check("[5] a surface-only agent is denied a pipeline exactly as raw SQL",
          (not ok and "raw SQL is denied" in why) or master_off, why)
    a, b = hp.data_query_outcomes([{"arguments": doc, "result": {"status": "error", "error": "x"}}])[:2]
    check("[5] a failed pipeline is a failed data query to the honesty gate", (a, b) == (1, 0))


# ── [6] the handler: refusals decided before anything runs ──────────────────────
LIVE = False
try:
    import pymongo
    _c = pymongo.MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=1500)
    _c.admin.command("ping")
    LIVE = True
except Exception:  # noqa: BLE001
    LIVE = False

GATE_DB = f"uderia_gate_{uuid.uuid4().hex[:8]}"


def _handler_env(conns):
    from trusted_data_agent.dbcatalog import service as svcmod, cache as CH
    from trusted_data_agent.components.manager import get_component_manager
    svcmod._SERVICE = svcmod.NavigatorService(store=_Store(conns), cache=CH.CatalogCache())
    return get_component_manager().get_component("database_navigator").handler


def _prof(sources=None):
    q = {"enabled": True}
    if sources:
        q["sources"] = sources
    return {"id": "p-mongo", "profile_type": "llm_only",
            "componentConfig": {"database_navigator": {"enabled": True, "query": q}}}


def _ctx(prof, sid="sess-mongo"):
    return {"user_uuid": "u", "profile_id": prof["id"], "profile_config": prof, "session_id": sid,
            "STATE": {"structured_tools": {}, "mcp_tools": {}}}


def _q(h, prof, **args):
    try:
        return go(h.process({"action": "query", **args}, _ctx(prof))).spec
    except Exception as exc:  # noqa: BLE001
        return {"error": f"crashed: {type(exc).__name__}: {exc}", "message": ""}


MCONN = {"id": "m1", "name": "gate mongo", "driver": "mongodb", "host": "127.0.0.1", "port": "27017",
         "database": GATE_DB, "is_default": True}
SCONN = {"id": "s1", "name": "sql side", "driver": "sqlite", "database": ":memory:"}


@guarded
def t_handler_refusals():
    h = _handler_env([MCONN, SCONN])
    calls = []
    real_rights, real_run = MC.login_rights, MC.run_statement

    def rights(value):
        return lambda creds, refresh=False: value
    MC.run_statement = lambda *a, **k: calls.append(a) or {"columns": [], "rows": [], "truncated": False,
                                                            "spec": {}, "text": "", "elapsed_ms": 0}
    try:
        P = _prof(["conn:m1", "conn:s1"])
        check("[6] validate_arguments accepts collection + pipeline",
              h.validate_arguments({"action": "query", "collection": "b", "pipeline": []})[0] is True)
        check("[6] …and still refuses a query with neither", h.validate_arguments({"action": "query"})[0] is False)
        MC.login_rights = rights({"can_write": False, "reason": "the login r@admin is read-only", "user": "r@admin"})
        s = _q(h, P, sql="SELECT * FROM business", source_id="conn:m1")
        check("[6] SQL sent to a MongoDB source is refused, naming the right arguments",
              s.get("error") == "wrong_query_language" and "'collection' and 'pipeline'" in s.get("message", ""), str(s.get("message")))
        s = _q(h, P, collection="b", pipeline=[{"$count": "n"}], source_id="conn:s1")
        check("[6] a pipeline sent to a SQL source is refused, naming 'sql'",
              s.get("error") == "wrong_query_language" and "give 'sql'" in s.get("message", ""), str(s.get("message")))
        s = _q(h, P, collection="b", pipeline=[{"$match": {}}, {"$out": "stolen"}], source_id="conn:m1")
        check("[6] a writing pipeline is refused as not read-only", s.get("error") == "not_read_only", str(s))
        check("[6] …before anything was sent", calls == [])
        s = _q(h, P, collection="b", pipeline='[{"$match": {"$where": "1"}}]', source_id="conn:m1")
        check("[6] a pipeline passed as JSON text is checked the same way", s.get("error") == "not_read_only")
        MC.login_rights = rights({"can_write": True, "reason": "access control is off on this server — anyone who connects can write", "user": ""})
        s = _q(h, P, collection="b", pipeline=[{"$count": "n"}], source_id="conn:m1")
        check("[6] a login that can write is refused without the owner's consent",
              s.get("error") == "writable_login" and "read-only login" in s.get("message", ""), str(s.get("message")))
        check("[6] …nothing was sent", calls == [])
        check("[6] the refusal carries the statement it would have run",
              json.loads(s.get("sql") or "{}").get("collection") == "b")
        MC.login_rights = rights({"can_write": None, "reason": "the login's rights could not be read (timeout)", "user": ""})
        s = _q(h, P, collection="b", pipeline=[{"$count": "n"}], source_id="conn:m1")
        check("[6] rights that cannot be read are never taken as read-only", s.get("error") == "writable_login", str(s.get("error")))
        MC.login_rights = rights({"can_write": True, "reason": "x", "user": ""})
        h2 = _handler_env([{**MCONN, "allow_writable_login": True}, SCONN])
        s = _q(h2, P, collection="b", pipeline=[{"$count": "n"}], source_id="conn:m1")
        check("[6] with the owner's consent the query runs", s.get("error") in (None, "") and len(calls) == 1, str(s.get("error")))
        check("[6] the card records the consent", (s.get("login") or {}).get("writable_allowed") is True)
        calls.clear()
        h3 = _handler_env([{**MCONN, "database": "", "allow_writable_login": True}, SCONN])
        s = _q(h3, P, collection="b", pipeline=[{"$count": "n"}], source_id="conn:m1")
        check("[6] a connection spanning several databases needs one named — refused before it runs",
              s.get("error") == "invalid_statement" and "'database' argument" in s.get("message", "") and calls == [],
              str(s.get("message")))
        s = _q(h3, P, collection="b", pipeline=[{"$count": "n"}], database="shop", source_id="conn:m1")
        check("[6] the 'database' argument names it", s.get("error") in (None, "") and len(calls) == 1
              and json.loads(s.get("sql") or "{}").get("database") == "shop", str(s.get("error")))
    finally:
        MC.login_rights, MC.run_statement = real_rights, real_run


# ── [7] live: provider, handler, Canvas on a throwaway database ─────────────────
def _fixture(client):
    db = client[GATE_DB]
    db.customers.insert_many([
        {"_id": 1, "name": "Maria Lopez", "email": "m@x.io", "city": "Tampa", "spend": 120.5,
         "address": {"city": "Tampa", "zip": "33602"}, "tags": ["vip"]},
        {"_id": 2, "name": "Ken Ito", "email": "k@x.io", "city": "Tampa", "spend": 30},
        {"_id": 3, "name": "Ana Silva", "city": "Reno", "spend": "n/a"},
    ])
    db.orders.insert_many([{"cid": 1 + (i % 3), "amount": i * 10} for i in range(1, 31)])
    db.orders.create_index("cid")
    db.customers.create_index("email", unique=True, sparse=True)
    db.create_collection("big_orders", viewOn="orders", pipeline=[{"$match": {"amount": {"$gt": 200}}}])


@guarded
def t_live():
    if not LIVE:
        SKIPPED.append("[7] live MongoDB (no server on 127.0.0.1:27017)")
        return
    client = pymongo.MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=3000)
    try:
        _fixture(client)
        creds = {"driver": "mongodb", "host": "127.0.0.1", "port": "27017", "database": GATE_DB}
        p = MongoCatalogProvider("conn:m1", "gate", creds, sample_size=50)
        dbs = go(p.list_databases())
        check("[7] a scoped connection lists only its database", [d.name for d in dbs] == [GATE_DB] and dbs[0].object_count == 3,
              str([(d.name, d.object_count) for d in dbs]))
        objs = {o.name: o for o in go(p.list_objects(GATE_DB))}
        check("[7] collections read as tables, a view as a view",
              objs["customers"].kind == "table" and objs["big_orders"].kind == "view", str({k: v.kind for k, v in objs.items()}))
        check("[7] collection counts are metadata, labelled so",
              objs["orders"].row_count == 30 and objs["orders"].row_count_source == "statistics")
        d = go(p.describe_object(GATE_DB, "customers"))
        cols = {c.name: c for c in d.columns}
        check("[7] describe: fields sampled with every type they carry (sorted, stable)", cols["spend"].data_type == "double|int32|string", cols["spend"].data_type)
        check("[7] describe: presence and nullability", cols["email"].stats["present_in"] == 2 and cols["email"].nullable is True)
        check("[7] describe: nested fields as dot paths", "address.zip" in cols)
        check("[7] describe: _id is the primary key; a unique index is marked",
              cols["_id"].is_primary and cols["email"].is_unique and any(i.kind == "primary" for i in d.indexes))
        check("[7] describe SAYS the fields come from the documents, never a declared schema",
              any("a declared schema — fields read from all 3 documents" in u for u in d.unavailable), str(d.unavailable))
        d2 = go(MongoCatalogProvider("conn:m1", "gate", creds, sample_size=10).describe_object(GATE_DB, "orders"))
        check("[7] …and says 'sampled … of about N' when it read only some",
              any("fields sampled from 10 documents of about 30" in u for u in d2.unavailable), str(d2.unavailable))
        v = go(p.describe_object(GATE_DB, "big_orders"))
        check("[7] a view's definition is read", '"$gt"' in v.view_text and "orders" in v.view_text, v.view_text)
        pv = go(p.preview(GATE_DB, "orders", 5))
        check("[7] preview: bounded rows as a table", len(pv.rows) == 5 and pv.truncated and "amount" in pv.columns)
        check("[7] exact count", go(p.row_count(GATE_DB, "orders")) == 30)
        cols_, rows = go(p.run_select(json.dumps({"collection": "orders", "pipeline": [
            {"$group": {"_id": "$cid", "total": {"$sum": "$amount"}}}, {"$sort": {"_id": 1}}]}), 10))
        check("[7] an aggregation runs and is shaped as rows",
              cols_ == ["_id", "total"] and rows == [[1, 1650], [2, 1450], [3, 1550]], str(rows))
        try:
            go(p.run_select(json.dumps({"collection": "orders", "pipeline": [{"$out": "leak"}]}), 10))
            check("[7] $out through the provider is refused by the platform", False, "it ran")
        except Exception as e:  # noqa: BLE001 — the SERVER refusing it is not the platform refusing it
            check("[7] $out through the provider is refused by the platform",
                  isinstance(e, MQ.MongoQueryError) and e.code == "not_read_only", f"{type(e).__name__}: {e}")
        check("[7] …and no collection was written", "leak" not in client[GATE_DB].list_collection_names())
        try:
            go(p.describe_object("other_db", "x"))
            check("[7] a database outside the connection's scope is refused", False)
        except Exception as e:  # noqa: BLE001
            check("[7] a database outside the connection's scope is refused", "scoped to database" in str(e), str(e))
        rights = MC.login_rights(creds, refresh=True)
        check("[7] this server runs without access control, and says so",
              rights["can_write"] is True and "access control is off" in rights["reason"], str(rights))

        # the Canvas executor
        from components.builtin.canvas.connectors.sql import SQLNativeConnector
        cn = SQLNativeConnector()
        r = go(cn.execute('{"collection": "customers", "filter": {"city": "Tampa"}, "projection": {"name": 1}}', creds))
        check("[7] Canvas: a find-style statement runs", r.error is None and r.row_count == 2 and r.columns == ["_id", "name"],
              str(r.error))
        r = go(cn.execute('{"collection": "customers", "pipeline": [{"$merge": "x"}]}', creds, readonly=True))
        check("[7] Canvas read-only refuses a write stage", r.error and "writes" in r.error)
        r = go(cn.execute("SELECT 1", creds))
        check("[7] Canvas: SQL on a MongoDB connection gets the statement shape back", r.error and "JSON" in r.error, str(r.error))
        t = go(cn.test_connection(creds))
        check("[7] Canvas: the connection test names the login's rights",
              t.valid and "can write" in t.message and t.server_info.startswith("MongoDB"), f"{t.message} / {t.server_info}")
        t = go(cn.test_connection({"driver": "mongodb", "host": "127.0.0.1", "port": "1"}))
        check("[7] Canvas: an unreachable server fails the test", t.valid is False)

        # the agent's query action, end to end (consent given: this server has no access control)
        from trusted_data_agent.agent import result_sets as rs
        from trusted_data_agent.core.config import APP_CONFIG
        saved = getattr(APP_CONFIG, "COMPUTE_LINEAGE", False)
        APP_CONFIG.COMPUTE_LINEAGE = True
        try:
            h = _handler_env([{**MCONN, "allow_writable_login": True}])
            rs.begin_turn("sess-mongo")
            s = _q(h, _prof(), collection="customers", pipeline=[{"$sort": {"_id": 1}}])
            check("[7] query: runs on the default MongoDB source", not s.get("error") and s.get("row_count") == 3, str(s.get("message"))[:200])
            check("[7] query: the result names its language and statement",
                  s.get("language") == "mongodb" and (s.get("source") or {}).get("dialect") == "mongodb"
                  and json.loads(s["sql"])["collection"] == "customers")
            check("[7] query: personal fields masked for the card", {"name", "email"} <= set(s.get("masked_columns") or []),
                  str(s.get("masked_columns")))
            check("[7] query: the model reads the values", s["rows"][0][1] == "Maria Lopez")
            st = rs.statements("sess-mongo")
            check("[7] query: the result reached the registry with its statement text",
                  st and st[-1]["row_count"] == 3 and '"customers"' in st[-1]["statement"], str(st[-1] if st else None))
            check("[7] query: the result cites statement 1", s.get("statement") == 1)
            s = _q(h, _prof(), collection="orders", pipeline=[{"$sort": {"amount": 1}}], limit=5)
            check("[7] query: bounded for the model, and says so",
                  s.get("shown") == 5 and s.get("truncated") is True and "$group" in s.get("message", ""))
            s = _q(h, _prof(), collection="orders", pipeline=[{"$lookup": {"from": "customers", "localField": "cid",
                                                                           "foreignField": "_id", "as": "c"}},
                                                             {"$unwind": "$c"}, {"$group": {"_id": "$c.city", "n": {"$sum": 1}}},
                                                             {"$sort": {"_id": 1}}])
            check("[7] query: a $lookup join across collections", s.get("rows") == [["Reno", 10], ["Tampa", 20]], str(s.get("rows")))
        finally:
            APP_CONFIG.COMPUTE_LINEAGE = saved
    finally:
        client.drop_database(GATE_DB)
        check("[7] the throwaway database is gone", GATE_DB not in client.list_database_names())


# ── [8] live: DAB's own MongoDB databases, read-only ────────────────────────────
@guarded
def t_dab():
    if not LIVE:
        SKIPPED.append("[8] DAB MongoDB databases (no server)")
        return
    client = pymongo.MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=3000)
    have = set(client.list_database_names())
    targets = [(db, coll) for db, coll in (("yelp_db", "business"), ("yelp_db", "checkin"), ("articles_db", "articles"))
               if db in have]
    if not targets:
        SKIPPED.append("[8] DAB MongoDB databases (not loaded)")
        return
    for db, coll in targets:
        before = client[db][coll].estimated_document_count()
        p = MongoCatalogProvider(f"conn:{db}", db, {"driver": "mongodb", "host": "127.0.0.1", "port": "27017", "database": db},
                                 sample_size=100)
        d = go(p.describe_object(db, coll))
        cols, rows = go(p.run_select(json.dumps({"collection": coll, "pipeline": [{"$count": "n"}]}), 5))
        check(f"[8] {db}.{coll}: described and counted by aggregation",
              d.columns and rows == [[before]], f"{len(d.columns)} fields, {rows} vs {before}")
        check(f"[8] {db}.{coll}: unchanged", client[db][coll].estimated_document_count() == before)


for fn in (t_declaration, t_js, t_statement, t_rights, t_classifiers, t_handler_refusals, t_live, t_dab):
    fn()

for s in SKIPPED:
    print(f"  SKIP  {s}")
print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)  — MongoDB reads, and only reads")
sys.exit(1 if FAIL else 0)
