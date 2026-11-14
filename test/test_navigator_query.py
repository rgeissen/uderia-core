#!/usr/bin/env python3
"""The Database Navigator's `query` action, end to end — real handler, real SQLite, real execution.

WHY THIS GATE EXISTS
P1 of docs/roadmap/DAB_P1_NAVIGATOR_QUERY.md: an agent may run its OWN read-only SELECT
against a registered Database Connection, with every protection an MCP SQL tool gets.
Added naively the action would have reached every profile that has the navigator on,
every connection the owner has, and none of the SQL protections. This gate drives the real
handler against real SQLite files and asserts: opt-in only; the allow-list of sources;
direct connections only; writes refused before execution and by the database; rows bounded
for the model while the full result reaches the result-set registry; PII masked for the
card only; the result carries its statement number; and the signatures record the new
capability only for an agent that has it.

Run: python3 test/test_navigator_query.py     (no server, no network)
"""
import asyncio
import logging
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
logging.disable(logging.CRITICAL)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.core import agent_authority as aa  # noqa: E402
from trusted_data_agent.agent import result_sets as rs  # noqa: E402
from trusted_data_agent.dbcatalog import service as svcmod, cache as CH, resolution as RS  # noqa: E402
from trusted_data_agent.components.manager import get_component_manager  # noqa: E402
from trusted_data_agent.core import profile_config_contract as pcc  # noqa: E402
from trusted_data_agent.core.trust import deployment as dep  # noqa: E402

go = asyncio.run
tmp = tempfile.mkdtemp()
DB1, DB2 = os.path.join(tmp, "shop.db"), os.path.join(tmp, "crm.db")
c = sqlite3.connect(DB1)
c.execute("CREATE TABLE business (id INTEGER, city TEXT, stars REAL, email TEXT, note TEXT)")
c.executemany("INSERT INTO business VALUES (?,?,?,?,?)",
              [(i, "Indianapolis" if i % 2 else "Tampa", 3.0 + (i % 3), f"u{i}@x.org",
                "line one\nline two" if i == 1 else "n") for i in range(1, 151)])
c.execute("CREATE TABLE review (id INTEGER, name TEXT, stars REAL)")
c.execute("INSERT INTO review VALUES (1, 'Maria Lopez', 4.0)")
c.execute("CREATE TABLE product (id INTEGER, name TEXT)")
c.execute("INSERT INTO product VALUES (1, 'Oak desk')")
c.execute("CREATE TABLE event (id INTEGER)")
c.executemany("INSERT INTO event VALUES (?)", [(i,) for i in range(700)])
c.commit(); c.close()
c = sqlite3.connect(DB2); c.execute("CREATE TABLE account (id INTEGER, name TEXT)")
c.execute("INSERT INTO account VALUES (1, 'Acme')"); c.commit(); c.close()


class Store:
    conns = [{"id": "c1", "name": "shop", "driver": "sqlite", "database": DB1, "is_default": True},
             {"id": "c2", "name": "crm", "driver": "sqlite", "database": DB2}]

    def list(self, uid):
        return self.conns

    def resolve(self, uid, cid):
        return next((dict(x) for x in self.conns if x["id"] == cid), None)


svcmod._SERVICE = svcmod.NavigatorService(store=Store(), cache=CH.CatalogCache())
cm = get_component_manager()
h = cm.get_component("database_navigator").handler
SID = "sess-navq"


def profile(query=None, **nav):
    cfg = {"enabled": True, **nav}
    if query is not None:
        cfg["query"] = query
    return {"id": "p-navq", "profile_type": "llm_only", "componentConfig": {"database_navigator": cfg}}


def ctx(prof):
    return {"user_uuid": "u", "profile_id": prof["id"], "profile_config": prof, "session_id": SID,
            "STATE": {"structured_tools": {}, "mcp_tools": {}}}


def q(prof, sql, **kw):
    # a raising handler must fail an assertion BY NAME, never abort the run
    try:
        return go(h.process({"action": "query", "sql": sql, **kw}, ctx(prof))).spec
    except Exception as exc:  # noqa: BLE001
        return {"error": f"crashed: {type(exc).__name__}: {exc}", "message": ""}


def rows_in(db, table):
    return sqlite3.connect(db).execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


saved_cl = getattr(APP_CONFIG, "COMPUTE_LINEAGE", False)
APP_CONFIG.COMPUTE_LINEAGE = True
try:
    # ── [1] no opt-in → refused, nothing runs
    rs.begin_turn(SID)
    s = q(profile(), "SELECT * FROM business")
    check("[1] without the opt-in the query is refused", s.get("error") == "query_not_enabled", str(s.get("error")))
    check("[1] the refusal says how to get it", "allow queries" in s.get("message", ""))
    check("[1] nothing reached the registry", rs.statements(SID) == [])
    s = q(profile(query={"enabled": False}), "SELECT 1")
    check("[1] query.enabled false is also refused", s.get("error") == "query_not_enabled")
    check("[1] validate_arguments requires sql", h.validate_arguments({"action": "query"})[0] is False)

    # ── [2] opt-in, default source: rows, bound, registry, masking, statement
    rs.begin_turn(SID)
    P = profile(query={"enabled": True})
    s = q(P, "SELECT id, city, email, note FROM business ORDER BY id")
    check("[2] the query ran", not s.get("error") and s.get("row_count") == 150, str(s.get("message"))[:160])
    check("[2] the default source is used", (s.get("source") or {}).get("id") == "conn:c1")
    check("[2] the dialect is reported", (s.get("source") or {}).get("dialect") == "sqlite")
    check("[2] the model gets the default 100 rows and is told there are more",
          s.get("shown") == 100 and s.get("truncated") is True and "more" in s.get("message", ""))
    check("[2] a newline inside a value stays one value", s["rows"][0][3] == "line one\nline two", repr(s["rows"][0][3]))
    check("[2] values keep their types", s["rows"][0][0] == 1)
    st = rs.statements(SID)
    check("[2] the FULL result reached the registry", len(st) == 1 and st[0]["row_count"] == 150, str([x.get("row_count") for x in st]))
    check("[2] the result names its statement number", s.get("statement") == 1)
    check("[2] email is masked for the card", "email" in (s.get("masked_columns") or []))
    check("[2] …but the model reads the value", s["rows"][0][2] == "u1@x.org")
    s = q(P, "SELECT id FROM business", limit=9999)
    check("[2] a limit above the result returns the whole result", s.get("shown") == 150 and s.get("truncated") is False)
    s = q(P, "SELECT id FROM event", limit=9999)
    check("[2] limit is clamped to 500", s.get("shown") == 500 and s.get("row_count") == 700 and s.get("truncated") is True,
          f"shown={s.get('shown')} rows={s.get('row_count')}")
    s = q(P, "SELECT id FROM business", limit=5)
    check("[2] an explicit small limit is honoured", s.get("shown") == 5 and s.get("truncated") is True)

    # ── [3] the allow-list of sources
    s = q(P, "SELECT * FROM account", source_id="conn:c2")
    check("[3] a source outside the allow-list is refused", s.get("error") == "source_not_allowed", str(s.get("error")))
    check("[3] the refusal names the allowed source", "conn:c1" in s.get("message", ""))
    P2 = profile(query={"enabled": True, "sources": ["conn:c1", "conn:c2"]})
    s = q(P2, "SELECT name FROM account", source_id="conn:c2")
    check("[3] an allowed second source is queried", not s.get("error") and s["rows"] == [["Acme"]], str(s.get("message"))[:120])
    P3 = profile(query={"enabled": True, "sources": ["conn:gone"]})
    s = q(P3, "SELECT 1")
    check("[3] an allowed id that does not exist here is refused (fails closed)",
          s.get("error") in ("source_unavailable", "source_not_allowed"), str(s.get("error")))

    # ── [4] writes never run
    before = rows_in(DB1, "business")
    for bad in ("DELETE FROM business", "UPDATE business SET stars = 0",
                "SELECT 1; DROP TABLE business", "PRAGMA user_version = 3"):
        s = q(P, bad)
        check(f"[4] refused: {bad}", bool(s.get("error")), str(s.get("error")))
    s = q(P, "WITH d AS (DELETE FROM business RETURNING 1) SELECT COUNT(*) FROM d")
    check("[4] a write reads as refused, never as a failed query", s.get("error") == "not_read_only"
          and "nothing was executed" in s.get("message", ""), f"{s.get('error')} {s.get('message', '')[:80]}")
    s = q(P, "PRAGMA user_version = 3")
    check("[4] a write the DATABASE refuses also reads as not read-only", s.get("error") == "not_read_only",
          f"{s.get('error')} {s.get('message', '')[:80]}")
    check("[4] the database is unchanged", rows_in(DB1, "business") == before
          and sqlite3.connect(DB1).execute("PRAGMA user_version").fetchone()[0] == 0)

    # ── [5] authority reaches the component: deny_writes and the exclusive surface
    tok = aa.set_current_principal(aa.AgentPrincipal(owner_uuid="u", profile_id="p-navq",
                                                     scope=aa.parse_scope({"autonomous": True, "denied_tools": ["database_navigator"]})))
    try:
        s = q(P, "SELECT 1 AS x")
        check("[5] a denied component cannot query", s.get("error") == "refused" and "denied" in s.get("message", ""), str(s.get("message"))[:100])
    finally:
        aa.reset_current_principal(tok)
    saved_obda = _OP.plane_available("read")
    _OP.force("read", True)
    tok = aa.set_current_principal(aa.AgentPrincipal(owner_uuid="u", profile_id="p-navq",
                                                     scope=aa.parse_scope({"autonomous": True, "dataSurface": {"products": ["p"], "exclusive": True}})))
    try:
        s = q(P, "SELECT id FROM business")
        check("[5] an exclusive data surface refuses the agent's own SQL", s.get("error") == "refused"
              and "query_product" in s.get("message", ""), str(s.get("message"))[:120])
    finally:
        aa.reset_current_principal(tok)
        _OP.force("read", saved_obda)

    # ── [6] an MCP source is refused, naming its own tool
    orig_sources = svcmod._SERVICE.sources

    def with_mcp(uid, prof, *a, **k):
        r = orig_sources(uid, prof, *a, **k)
        r["sources"] = list(r["sources"]) + [{"id": "mcp:srv", "kind": "mcp", "label": "TD MCP", "driver": "mcp",
                                              "capabilities": [], "is_default": False, "detail": ""}]
        return r
    svcmod._SERVICE.sources = with_mcp
    try:
        s = q(profile(query={"enabled": True, "sources": ["mcp:srv"]}), "SELECT 1")
        check("[6] an MCP source is refused and its own tool is named", s.get("error") == "source_is_mcp"
              and "own SQL tool" in s.get("message", ""), str(s.get("message"))[:120])
    finally:
        svcmod._SERVICE.sources = orig_sources
finally:
    APP_CONFIG.COMPUTE_LINEAGE = saved_cl

# ── [6b] an ambiguous `name` is personal data when its table is about people — one rule
#         for the query card, the preview and search (judged by the table, never globally)
APP_CONFIG.COMPUTE_LINEAGE = True
try:
    rs.begin_turn(SID)
    s = q(profile(query={"enabled": True}), "SELECT name, stars FROM review")
    check("[6b] a reviewer's name is masked for the card", "name" in (s.get("masked_columns") or []), str(s.get("masked_columns")))
    check("[6b] …and the model still reads it", s.get("rows") == [["Maria Lopez", 4.0]], str(s.get("rows")))
    s = q(profile(query={"enabled": True}), "SELECT name FROM product")
    check("[6b] a product's name is not masked", (s.get("masked_columns") or []) == [], str(s.get("masked_columns")))
    s = q(profile(query={"enabled": True}), "WITH r AS (SELECT name FROM review) SELECT name FROM r")
    check("[6b] the table behind a CTE still decides", "name" in (s.get("masked_columns") or []), str(s.get("masked_columns")))
    s = q(profile(query={"enabled": True}), "WITH customers AS (SELECT name FROM product) SELECT name FROM customers")
    check("[6b] a CTE's own name is not a table (a product list called 'customers' is not masked)",
          (s.get("masked_columns") or []) == [], str(s.get("masked_columns")))
finally:
    APP_CONFIG.COMPUTE_LINEAGE = saved_cl
from trusted_data_agent.dbcatalog import intelligence as I  # noqa: E402
from trusted_data_agent.dbcatalog.types import ColumnInfo  # noqa: E402
check("[6b] column flags (preview + search) share the rule: review.name is pii",
      "pii" in I.column_flags(ColumnInfo(name="name", data_type="VARCHAR(50)"), "customers"))
check("[6b] …product.name is not", "pii" not in I.column_flags(ColumnInfo(name="name", data_type="VARCHAR(50)"), "product"))
check("[6b] …and a name with no table context is not", I._pii_reason("name") is None)

# ── [6c] the dialect is named as sqlglot names it
check("[6c] a postgresql connection reports the postgres dialect",
      svcmod.NavigatorService.dialect_of(type("P", (), {"driver": "postgresql", "provider_kind": "direct"})()) == "postgres")
check("[6c] sqlite is unchanged",
      svcmod.NavigatorService.dialect_of(type("P", (), {"driver": "sqlite", "provider_kind": "direct"})()) == "sqlite")

# ── [7] advertised only to an agent that opted in
nav = cm.get_component("database_navigator")
off = cm.tool_definition_for(nav, profile())
on = cm.tool_definition_for(nav, profile(query={"enabled": True}))
check("[7] without the opt-in: no `sql` argument, no 'query' in the tool text",
      "sql" not in off["args"] and "'query'" not in off["description"] and "'query'" not in off["args"]["action"]["description"])
check("[7] with the opt-in: the `sql` argument and the action are advertised",
      "sql" in on["args"] and "'query'" in on["description"] and "'query'" in on["args"]["action"]["description"])
check("[7] instructions carry the query line only with the opt-in",
      "**query**" in cm.get_instructions_text(profile(query={"enabled": True}))
      and "**query**" not in cm.get_instructions_text(profile()))

# ── [8] the signatures record the new capability only for an agent that has it
check("[8] portable: the capability token appears with the opt-in",
      "database_navigator:query" in pcc._canon_component_config(profile(query={"enabled": True})["componentConfig"]))
check("[8] portable: absent without it",
      pcc._canon_component_config(profile()["componentConfig"]) == ["database_navigator"])
check("[8] portable: an opt-in on a DISABLED navigator is not a capability",
      "database_navigator:query" not in pcc._canon_component_config(
          {"database_navigator": {"enabled": False, "query": {"enabled": True}}}))
import trusted_data_agent.core.database_connections as dbc  # noqa: E402
orig_store = dbc.get_database_connection_store
dbc.get_database_connection_store = lambda: Store()
try:
    check("[8] deployment: nothing recorded without the opt-in", dep.navigator_query_sources(profile(), "u") is None)
    got = dep.navigator_query_sources(profile(query={"enabled": True}), "u")
    check("[8] deployment: the default source is recorded with its driver", got == [{"id": "conn:c1", "driver": "sqlite"}], str(got))
    got2 = dep.navigator_query_sources(profile(query={"enabled": True, "sources": ["conn:c1", "conn:c2"]}), "u")
    s_old = dep.build_deployment_signature(None, None, [], data_sources=got)
    s_new = dep.build_deployment_signature(None, None, [], data_sources=got2)
    check("[8] deployment: widening the allow-list changes the hash", dep.deployment_hash(s_old) != dep.deployment_hash(s_new))
    check("[8] deployment: the drift names the added database",
          any("conn:c2" in line for line in dep.describe_drift(s_old, s_new)), str(dep.describe_drift(s_old, s_new)))
    check("[8] deployment: enabling querying is named as drift",
          any("querying enabled" in line for line in dep.describe_drift(dep.build_deployment_signature(None, None, []), s_old)))
finally:
    dbc.get_database_connection_store = orig_store

# ── [9] the companion skill teaches querying only to an agent that may query
import re as _re  # noqa: E402
from trusted_data_agent.skills.manager import get_skill_manager, gate_skill_text  # noqa: E402
from trusted_data_agent.skills.models import SkillSpec  # noqa: E402
from trusted_data_agent.components.manager import ComponentManager  # noqa: E402
sm = get_skill_manager()


def skill_text(prof):
    return sm.resolve_skills([SkillSpec(name="database-navigator-expert")], profile_config=prof).get_user_context_block()


raw = open(os.path.join(ROOT, "skills/builtin/database-navigator-expert/database-navigator-expert.md")).read()
off_txt, on_txt = skill_text(profile()), skill_text(profile(query={"enabled": True}))
check("[9] without the opt-in the skill carries no query section", "Running your own query" not in off_txt)
check("[9] …and exactly the text it had before the section existed",
      off_txt.endswith(_re.sub(r"\[\[gate:[\w.-]+\]\].*?\[\[/gate\]\]", "", raw, flags=_re.DOTALL).strip()) or
      _re.sub(r"\[\[gate:[\w.-]+\]\].*?\[\[/gate\]\]", "", raw, flags=_re.DOTALL).strip() in off_txt)
check("[9] with the opt-in the section is there", "Running your own query" in on_txt)
check("[9] no gate marker ever reaches the model", "[[gate" not in off_txt and "[[/gate" not in on_txt)
check("[9] no profile = gates closed", "Running your own query" not in skill_text(None))
orig_gates = ComponentManager.apply_instruction_gates
ComponentManager.apply_instruction_gates = classmethod(lambda cls, text, pc=None: text)  # gates not evaluated
try:
    closed = gate_skill_text(raw, profile(query={"enabled": True}))
finally:
    ComponentManager.apply_instruction_gates = orig_gates
check("[9] if the gates cannot be evaluated, gated blocks are removed (fails closed)",
      "Running your own query" not in closed and "[[gate" not in closed)
check("[9] a skill without gate markers is untouched", gate_skill_text("plain text", None) == "plain text")

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — the navigator query is opt-in, allow-listed and fully guarded"))
sys.exit(1 if FAIL else 0)
