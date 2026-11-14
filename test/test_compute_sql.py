"""PROP-006 phase 1 — the combine step (`TDA_Compute` op "sql"), end to end.

Drives the REAL handler, the REAL engine (in-process DuckDB) and the REAL SQL checkpoint. Each
section names the requirement (build brief A1–A11) or regression trap (assessment C1–C8) it proves.
No server, no network, no database but DuckDB in memory.
"""
import asyncio, copy, json, logging, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
logging.disable(logging.CRITICAL)
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

_SAVED = {k: getattr(APP_CONFIG, k, None) for k in ("COMPUTE_LINEAGE", "COMPUTE_SQL", "COMPUTE_SQL_INCOMPLETE", "OBDA_ENABLED")}
APP_CONFIG.COMPUTE_LINEAGE = True
APP_CONFIG.COMPUTE_SQL = True
APP_CONFIG.COMPUTE_SQL_INCOMPLETE = "refuse"

from trusted_data_agent.agent import compute as C
from trusted_data_agent.agent import compute_sql as CS
from trusted_data_agent.agent import result_sets as RS
from trusted_data_agent.components.manager import get_component_manager, ComponentManager

cm = get_component_manager()
comp = cm.get_component("compute")
H = comp.handler

BOOKS = [{"book_id": "bid_1", "title": "A"}, {"book_id": "bid_2", "title": "B"}, {"book_id": "bid_3", "title": "C"}]
REVIEWS = [{"book_id": 1, "rating": 4}, {"book_id": 2, "rating": 5}, {"book_id": 2, "rating": 3}, {"book_id": 9, "rating": 1}]

def st(n, rows, statement="SELECT * FROM t", truncated=False):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols: cols.append(k)
    return {"n": n, "tool": "base_readQuery", "statement": statement, "columns": cols,
            "row_count": len(rows), "rows": rows, "truncated": truncated}

def fresh(session, *results):
    """A turn whose queries returned ``results`` (each a (sql, rows) pair), registered through the
    real registry exactly as the tool-result seams register them."""
    RS.begin_turn(session)
    for sql, rows in results:
        RS.record(session, "base_readQuery", {"sql": sql}, {"status": "success", "results": rows})

def call(session, sql, user="u1"):
    return asyncio.run(H.process({"op": "sql", "sql": sql}, {"session_id": session, "user_uuid": user}))

try:
    print("[1] the switch: off serves nothing, on serves the op (A7)")
    APP_CONFIG.COMPUTE_SQL = False
    ok, why = H.validate_arguments({"op": "sql", "sql": "SELECT 1"})
    check("switch off: op 'sql' refused with today's message", not ok and why == f"op must be one of: {', '.join(C.OPS)}", why)
    td = cm.tool_definition_for(comp, {})
    check("switch off: no `sql` argument", "sql" not in td["args"])
    APP_CONFIG.COMPUTE_LINEAGE = False; APP_CONFIG.COMPUTE_SQL = True
    check("combine switch on but Compute with Lineage off: the gate stays closed",
          not ComponentManager._instruction_gate_open("compute_sql"))
    APP_CONFIG.COMPUTE_LINEAGE = True
    td = cm.tool_definition_for(comp, {})
    check("switch on: `sql` argument served", "sql" in td["args"])
    check("switch on: op lists sql", td["args"]["op"]["description"].endswith("| sql"))
    check("switch on: the tool description explains the combine", "s1, s2" in td["description"])
    check("switch on: the heavy instructions teach it", "op \"sql\"" in cm.apply_instruction_gates(comp.get_instructions("heavy"), {}))
    check("switch on: an op-less sql call names the fix", "needs `sql`" in H.validate_arguments({"op": "sql"})[1])
    check("no gate markup reaches the model", "[[gate:" not in json.dumps(td))

    print("[2] no new data access — two independent walls (A2)")
    two = {1: st(1, BOOKS), 2: st(2, REVIEWS)}
    for q, code in [("SELECT * FROM 'x.csv'", "not_a_result_set"), ("SELECT * FROM read_csv('/etc/hosts')", "not_a_result_set"),
                    ("SELECT * FROM books", "not_a_result_set"), ("SELECT * FROM main.s1", "not_a_result_set"),
                    ("DELETE FROM s1", "not_a_select"), ("SELECT 1; SELECT 2", "not_a_select"),
                    ("SELECT current_setting('temp_directory') AS h FROM s1", "engine_introspection"),
                    ("SELECT version() AS v FROM s1", "engine_introspection"),
                    ("SELECT * FROM s7", "no_such_statement"), ("SELECT 1 AS x", "no_result_set")]:
        try:
            CS.run(q, two); check(f"refused: {q}", False, "it ran")
        except CS.CombineError as e:
            check(f"refused before DuckDB: {q} ({code})", e.code == code, e.code)
        except Exception as e:  # a crash is not a refusal — report it BY NAME, never die silently
            check(f"refused before DuckDB: {q} ({code})", False, f"crashed: {type(e).__name__}: {e}")
    # the SECOND wall: even a statement the parser let through cannot reach a file
    _orig = CS.referenced_statements
    CS.referenced_statements = lambda sql: [1]
    try:
        r = CS.run("SELECT * FROM read_csv('/etc/hosts')", two)
        check("DuckDB itself refuses file access (external access disabled)",
              r["status"] == "error" and "ermission" in r["error"], r)
        r = CS.run("SELECT * FROM s1, (SELECT 1 FROM glob('/etc/*')) g", two)
        check("DuckDB itself refuses a glob", r["status"] == "error", r)
    finally:
        CS.referenced_statements = _orig

    print("[3] join keys measured on the actual rows (A9)")
    r = CS.run("SELECT a.title, AVG(b.rating) AS avg_rating FROM s1 a JOIN s2 b ON a.book_id = b.book_id "
               "GROUP BY a.title ORDER BY a.title", two)
    check("a consistent prefix is stripped and the keys meet", r["status"] == "success"
          and [(x["title"], x["avg_rating"]) for x in r["results"]] == [("A", 4.0), ("B", 4.0)], r.get("results"))
    check("the normalization is NAMED, never silent", any("affix stripped" in f for f in r["findings"]), r["findings"])
    check("the rewritten join is what ran", "REGEXP_REPLACE" in r["executed_sql"].upper(), r["executed_sql"])
    same = {1: st(1, [{"k": 1, "a": "x"}, {"k": 2, "a": "y"}]), 2: st(2, [{"k": 1, "b": 10}, {"k": 2, "b": 20}])}
    q = "SELECT s1.a, s2.b FROM s1 JOIN s2 ON s1.k = s2.k ORDER BY s1.a"
    r = CS.run(q, same)
    check("matching keys: the statement runs exactly as written", r["executed_sql"] == q and not r["findings"], r)
    none = {1: st(1, [{"k": "alpha"}, {"k": "beta"}]), 2: st(2, [{"k": "gamma"}, {"k": "delta"}])}
    r = CS.run("SELECT * FROM s1 JOIN s2 ON s1.k = s2.k", none)
    check("keys that never meet: reported for judgement, statement untouched",
          any("barely overlaps" in f for f in r["findings"]) and "REGEXP" not in r["executed_sql"].upper(), r["findings"])
    check("…and the empty join is named", any("returned no rows" in f for f in r["findings"]), r["findings"])
    mm = {1: st(1, [{"k": 1, "a": 1}, {"k": 1, "a": 2}]), 2: st(2, [{"k": 1, "b": 1}, {"k": 1, "b": 2}])}
    r = CS.run("SELECT * FROM s1 JOIN s2 ON s1.k = s2.k", mm)
    check("a key repeated on both sides is reported (rows multiply)", any("repeats on BOTH sides" in f for f in r["findings"]), r["findings"])

    print("[4] types come from the source; text is never silently parsed as a number (A10)")
    typed = {1: st(1, [{"id": "001", "v": "a"}, {"id": "002", "v": "b"}]), 2: st(2, [{"id": 1, "w": 5}, {"id": 2, "w": 6}])}
    r = CS.run("SELECT s1.v, s2.w FROM s1 JOIN s2 ON s1.id = s2.id ORDER BY s1.v", typed)
    check("'001' vs 1 meet only through the named numeric step", r["status"] == "success"
          and [x["w"] for x in r["results"]] == [5, 6] and any("numeric" in f for f in r["findings"]), r)

    print("[5] limits (A11)")
    _max = CS.MAX_INPUT_ROWS; CS.MAX_INPUT_ROWS = 3
    try:
        CS.run("SELECT * FROM s1 JOIN s2 ON s1.book_id = s2.book_id", two); check("inputs over the limit refused", False)
    except CS.CombineError as e:
        check("inputs over the limit refused, naming aggregate-at-source", e.code == "inputs_too_large" and "each source first" in str(e), str(e))
    finally:
        CS.MAX_INPUT_ROWS = _max
    _mo = CS.MAX_OUTPUT_ROWS; CS.MAX_OUTPUT_ROWS = 2
    try:
        r = CS.run("SELECT * FROM s2", two)
        check("output over the cap is cut and says so", r["truncated"] is True and len(r["results"]) == 2, r)
    finally:
        CS.MAX_OUTPUT_ROWS = _mo

    print("[6] incomplete inputs (A8, decision 7)")
    check("the registry's own cut is incomplete", CS.completeness(st(1, BOOKS, truncated=True))[0] is False)
    check("a row limit the rows exactly fill is incomplete",
          CS.completeness(st(1, BOOKS, statement="SELECT TOP 3 * FROM books"))[0] is False)
    check("a row limit NOT filled is complete", CS.completeness(st(1, BOOKS, statement="SELECT TOP 10 * FROM books"))[0] is True)
    fresh("inc", ("SELECT TOP 3 book_id, title FROM books", BOOKS), ("SELECT book_id, rating FROM reviews", REVIEWS))
    p = call("inc", "SELECT a.title FROM s1 a JOIN s2 b ON a.book_id = b.book_id")
    check("default policy REFUSES, naming the fix", p.metadata.get("error_code") == "incomplete_input"
          and "Narrow that query at its source" in p.metadata.get("error", ""), p.metadata)
    APP_CONFIG.COMPUTE_SQL_INCOMPLETE = "disclose"
    p = call("inc", "SELECT a.title FROM s1 a JOIN s2 b ON a.book_id = b.book_id")
    check("'disclose' policy runs and the disclosure is written by code",
          not p.metadata.get("error_code") and any("INCOMPLETE input" in n for n in p.spec.get("notes") or []), p.spec.get("notes"))
    APP_CONFIG.COMPUTE_SQL_INCOMPLETE = "refuse"

    print("[7] end to end through the handler and the checkpoint (A1, A3, A4, A5)")
    fresh("e2e", ("SELECT book_id, title FROM books", BOOKS), ("SELECT book_id, rating FROM reviews", REVIEWS))
    p = call("e2e", "SELECT a.title, AVG(b.rating) AS avg_rating FROM s1 a JOIN s2 b ON a.book_id = b.book_id GROUP BY a.title ORDER BY a.title")
    check("the combine succeeds", not p.metadata.get("error_code"), p.metadata)
    sts = RS.statements("e2e")
    check("its result is registered as the next statement", len(sts) == 3 and sts[2]["tool"] == CS.DERIVED_TOOL, [s["tool"] for s in sts])
    check("the result statement names its inputs (lineage reaches the sources)", sts[2].get("inputs") == [1, 2], sts[2].get("inputs"))
    rc = p.metadata["receipt"]
    check("the receipt carries the SQL that ran, the inputs, the applied normalization",
          rc["op"] == "sql" and "REGEXP_REPLACE" in rc["sql"].upper() and rc["statements"] == [1, 2]
          and rc["normalizations"] and rc["result_statement"] == 3, rc)
    check("the receipt hash is the hash of its sealed content", rc["receipt_hash"] == C.receipt_hash(rc))
    check("A14: the receipt seals when each input was queried",
          set(rc.get("input_times") or {}) == {"1", "2"} and all(str(v).endswith("+00:00") for v in rc["input_times"].values()),
          rc.get("input_times"))
    check("A14: changing an input's time changes the sealed hash",
          C.receipt_hash(dict(rc, input_times={"1": "x", "2": "y"})) != C.receipt_hash(rc))
    check("the lineage line names the inputs", "statement 1 + statement 2" in p.metadata["lineage"], p.metadata["lineage"])
    recs = RS._SESSIONS["e2e"]["receipts"]
    check("the receipt is recorded for sealing", any((x.get("receipt") or x).get("op") == "sql" for x in recs), recs[:1])
    p2 = call("e2e", "SELECT COUNT(*) AS n FROM s3")
    check("chainable: a combine reads a combine's result (A4)", not p2.metadata.get("error_code")
          and p2.spec["rows"] == [{"n": 2}] and RS.statements("e2e")[3].get("inputs") == [3], (p2.metadata, p2.spec.get("rows")))

    print("[7b] personal data stays masked on the card (A12)")
    PEOPLE = [{"customer_id": 1, "email": "ann@example.com", "first_name": "Ann"},
              {"customer_id": 2, "email": "bob@example.com", "first_name": "Bob"}]
    ORDERS = [{"customer_id": 1, "total": 30}, {"customer_id": 2, "total": 50}]
    fresh("pii", ("SELECT customer_id, email, first_name FROM crm.customers", PEOPLE),
          ("SELECT customer_id, total FROM shop.orders", ORDERS))
    p = call("pii", "SELECT c.email, c.first_name, o.total FROM s1 c JOIN s2 o ON c.customer_id = o.customer_id ORDER BY o.total")
    check("an e-mail column is masked on the card", "email" in (p.spec.get("masked_columns") or []), p.spec.get("masked_columns"))
    check("a name from a table about people is masked", "first_name" in (p.spec.get("masked_columns") or []), p.spec.get("masked_columns"))
    check("the figures are not masked", "total" not in (p.spec.get("masked_columns") or []))
    check("no raw address reaches the card", "ann@example.com" not in p.html and "bob@example.com" not in p.html, p.html[:300])
    check("the model still reads the values it needs", p.spec["rows"][0]["email"] == "ann@example.com")
    check("the model is told what the card masked", any("masked on the card" in n for n in p.spec.get("notes") or []))
    fresh("nopii", ("SELECT book_id, title FROM books", BOOKS), ("SELECT book_id, rating FROM reviews", REVIEWS))
    p = call("nopii", "SELECT COUNT(*) AS n FROM s2")
    check("nothing is masked when nothing is personal", not p.spec.get("masked_columns"))

    stamp = RS.stamp_for_turn("e2e")
    comb = next(x for x in stamp["statements"] if x["tool"] == CS.DERIVED_TOOL)
    check("the persisted record keeps the combine's inputs and time (the lineage dialog's second hop)",
          comb.get("inputs") == [1, 2] and str(comb.get("ts", "")).endswith("+00:00"), comb)
    plain = next(x for x in stamp["statements"] if x["n"] == 1)
    check("a statement persists no key it does not have (no empty inputs / phase)",
          "inputs" not in plain and "phase" not in plain, sorted(plain))

    print("[8] a combine receipt seals its own fields; others are untouched (C1)")
    base = dict(rc)
    check("changing the SQL changes the sealed hash", C.receipt_hash(dict(base, sql="SELECT 1")) != C.receipt_hash(base))
    check("the combine's content is the SQL field set", set(json.loads(C.receipt_content(base))) == set(C.SQL_RECEIPT_FIELDS))
    r = C.compute("sum", [{"a": 1}, {"a": 2}], column="a", statements=[1])["receipt"]
    check("an ordinary receipt still seals RECEIPT_FIELDS", set(json.loads(C.receipt_content(r))) == set(C.RECEIPT_FIELDS))

    print("[9] the lane scanner and honesty gate never read a combine as a data query (C2, C3)")
    from trusted_data_agent.agent import obda
    from trusted_data_agent.agent.honesty import primitives as HP
    calls = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}, "result": {"status": "success", "results": [{"a": 1}]}},
             {"tool_name": "TDA_Compute", "arguments": {"op": "sql", "sql": "SELECT * FROM s1"}, "result": {"status": "success"}},
             {"tool_name": CS.DERIVED_TOOL, "arguments": {"sql": "SELECT * FROM s1"}, "result": {"status": "success", "results": [{"a": 1}]}}]
    scan = obda.scan_obda(calls)
    check("free-formed count is the real query only", scan["freeform_count"] == 1 and scan["total_data_ops"] == 1, scan)
    check("honesty counts the real query only", HP.data_query_outcomes(
        [dict(c, status="success") for c in calls])[0] == 1)
    check("one predicate: TDA_Compute op sql is derived, other ops are not calls with sql anyway",
          obda.is_derived_call("TDA_Compute", {"op": "sql"}) and not obda.is_derived_call("TDA_Compute", {"op": "sum"})
          and obda.is_derived_call(CS.DERIVED_TOOL) and not obda.is_derived_call("base_readQuery", {"sql": "x"}))
    only_combine = calls[1:]
    check("a turn whose only 'query' is a combine has no data ops", obda.scan_obda(only_combine)["has_data_ops"] is False)

    print("[10] Agent Authority: a surface-only agent may combine; nothing else is waived (C4, decision 5)")
    from trusted_data_agent.core import agent_authority as AA
    _OP.force("read", True)
    sc = AA.parse_scope({"autonomous": True, "dataSurface": {"products": ["p"], "exclusive": True}})
    check("the combine is allowed", AA.is_tool_allowed(sc, CS.DERIVED_TOOL, arguments={"sql": "SELECT * FROM s1"})[0])
    check("the model's call is allowed", AA.is_tool_allowed(sc, "TDA_Compute", arguments={"op": "sql", "sql": "SELECT * FROM s1"})[0])
    check("raw SQL is still denied", not AA.is_tool_allowed(sc, "base_readQuery", arguments={"sql": "SELECT 1"})[0])
    sc2 = AA.parse_scope({"autonomous": True, "denied_tools": ["TDA_Compute"]})
    check("denying TDA_Compute denies the combine too (aliases)", not AA.is_tool_allowed(
        sc2, CS.DERIVED_TOOL, aliases=frozenset({"TDA_Compute", "compute"}), arguments={"sql": "SELECT 1"})[0])
    principal = AA.derive_principal({"authorityConfig": {"autonomous": True, "dataSurface": {"products": ["p"], "exclusive": True}}},
                                    "u1", "pid")
    tok = AA.set_current_principal(principal)
    try:
        fresh("surf", ("SELECT book_id, title FROM books", BOOKS), ("SELECT book_id, rating FROM reviews", REVIEWS))
        p = call("surf", "SELECT COUNT(*) AS n FROM s1")
        check("end to end under an exclusive-surface principal: the combine runs", not p.metadata.get("error_code"), p.metadata)
    finally:
        AA.reset_current_principal(tok)

    print("[11] the checkpoint call: dialect, namespace, aliases, no second scan (C5, C6, C7)")
    from trusted_data_agent.agent import tool_enforcement as TE
    seen = {}
    _g = TE.guarded_sql_call
    async def _spy(tool_name, arguments, execute, **kw):
        seen.update(kw, tool_name=tool_name)
        return await _g(tool_name, arguments, execute, **kw)
    TE.guarded_sql_call = _spy
    scans = []
    _scan = TE.scan_result
    TE.scan_result = lambda *a, **k: scans.append(a[0])
    try:
        fresh("cp", ("SELECT book_id, title FROM books", BOOKS))
        call("cp", "SELECT * FROM s1")
    finally:
        TE.guarded_sql_call = _g; TE.scan_result = _scan
    check("runs under the logical name, never TDA_*", seen.get("tool_name") == CS.DERIVED_TOOL, seen)
    check("DuckDB dialect, its own preflight namespace", seen.get("dialect") == "duckdb" and seen.get("source_id") == "combine", seen)
    check("deniable under the component's names", seen.get("aliases") == frozenset({"TDA_Compute", "compute"}), seen.get("aliases"))
    check("the result is not scanned a second time", seen.get("scan_result") is False and scans == [], (seen.get("scan_result"), scans))
    check("the registry's derived-tool name is the engine's", RS._DERIVED_TOOL == CS.DERIVED_TOOL)

    print("[12] classification is untouched by the combine (operator question)")
    from trusted_data_agent.core.config_manager import ConfigManager as ConfigurationManager
    from trusted_data_agent.core.trust.deployment import catalog_from_classification
    mcp = {"Database": [{"name": "base_readQuery", "arguments": [{"name": "sql"}]}]}
    def snapshot(switch):
        APP_CONFIG.COMPUTE_SQL = switch
        comp_tools = [{"name": t["name"], "description": t.get("description"),
                       "arguments": [{"name": k} for k in (t.get("args") or {})]} for t in cm.get_tool_definitions({})]
        return {"tools": dict(mcp, **{"Component Tools": comp_tools}), "prompts": {}, "last_classified": "t"}
    on, off = snapshot(True), snapshot(False)
    check("the component tools DO differ between the switch states (the test is not vacuous)",
          json.dumps(on["tools"]["Component Tools"]) != json.dumps(off["tools"]["Component Tools"]))
    check("the saved snapshot is byte-identical either way",
          json.dumps(ConfigurationManager._without_runtime_injected(on), sort_keys=True)
          == json.dumps(ConfigurationManager._without_runtime_injected(off), sort_keys=True))
    prof = lambda snap: {"id": "p", "profile_type": "tool_enabled", "mcpServerId": "srv", "classification_results": snap}
    check("the deployment catalog is byte-identical either way",
          catalog_from_classification(prof(on)) == catalog_from_classification(prof(off)))
    APP_CONFIG.COMPUTE_SQL = True
    print("[13] every lane attributes its results to their source")
    import ast, inspect
    from trusted_data_agent.agent import tool_enforcement as TE2
    RS.begin_turn("attr")
    async def _ex(args): return {"status": "success", "results": [{"a": 1}], "columns": ["a"]}
    asyncio.run(TE2.guarded_sql_call("database_navigator.query", {"sql": "SELECT a FROM t"}, _ex,
                                     session_id="attr", dialect="postgresql", source_id="conn:pg1"))
    check("a Navigator / connection query is attributed to its connection",
          RS.statements("attr")[-1].get("source_id") == "conn:pg1", RS.statements("attr")[-1].get("source_id"))
    class _FakeTool:
        name = "base_readQuery"; metadata = {"uderia_mcp_server_id": "srvA"}
        async def coroutine(self, **kw): return json.dumps({"status": "success", "results": [{"b": 2}]})
    ft = _FakeTool(); ft.coroutine = _FakeTool.coroutine.__get__(ft)
    TE2.wrap_langchain_mcp_tools([ft], "attr")
    asyncio.run(ft.coroutine(sql="SELECT b FROM u"))
    check("a ReAct MCP tool call is attributed to the server its tool came from",
          RS.statements("attr")[-1].get("source_id") == "mcp:srvA", RS.statements("attr")[-1])
    call("attr", "SELECT a FROM s1 UNION ALL SELECT b FROM s2")
    check("a combine result is attributed to the combine", RS.statements("attr")[-1].get("source_id") == "combine",
          RS.statements("attr")[-1].get("source_id"))
    src = inspect.getsource(__import__("trusted_data_agent.mcp_adapter.adapter", fromlist=["x"]))
    check("the Optimize adapter passes its server as the source", 'source=(f"mcp:{server_id}"' in src)
    lsrc = inspect.getsource(__import__("trusted_data_agent.llm.langchain_adapter", fromlist=["x"]).load_mcp_tools_for_langchain)
    check("the ReAct loader tags every tool with its server (tracing metadata, never schema)",
          '"uderia_mcp_server_id"' in lsrc and "args_schema" not in lsrc.split('"uderia_mcp_server_id"')[0][-300:])

    print("[14] isolation rules — operator and ontology, decided by code (A13, decision 8)")
    def reg(session, *items):
        RS.begin_turn(session)
        for srcid, sql, rows in items:
            RS.record(session, "base_readQuery", {"sql": sql}, {"status": "success", "results": rows}, source=srcid)
    reg("iso", ("mcp:hr", "SELECT emp_id, salary FROM hr.pay", [{"emp_id": 1, "salary": 9}]),
               ("conn:crm", "SELECT emp_id, deals FROM crm.reps", [{"emp_id": 1, "deals": 3}]))
    q = "SELECT p.salary, r.deals FROM s1 p JOIN s2 r ON p.emp_id = r.emp_id"
    check("no rule: the combine runs", not call("iso", q).metadata.get("error_code"))
    APP_CONFIG.COMBINE_ISOLATION = [{"sources": ["mcp:hr", "conn:crm"], "reason": "HR data never meets CRM"}]
    p = call("iso", q)
    check("a DEPLOYMENT rule refuses, naming the sources, the reason and who set it",
          p.metadata.get("error_code") == "isolation_policy" and "mcp:hr" in p.metadata["error"]
          and "HR data never meets CRM" in p.metadata["error"] and "deployment administrator" in p.metadata["error"], p.metadata)
    p = call("iso", "SELECT * FROM s1 UNION ALL SELECT emp_id, deals FROM s2")
    check("a UNION of the two is refused too (the data would still be combined)", p.metadata.get("error_code") == "isolation_policy")
    APP_CONFIG.COMBINE_ISOLATION = [{"sources": ["mcp:hr"]}, "junk", {"sources": []}]
    check("a malformed rule restricts nothing (never widened into a catch-all)", not call("iso", q).metadata.get("error_code"))
    APP_CONFIG.COMBINE_ISOLATION = []
    import trusted_data_agent.core.config_manager as _cmm
    class _CM:
        def get_profile(self, pid, uid): return {"id": pid, "combineIsolation": [{"sources": ["mcp:hr", "conn:crm"], "reason": "owner rule"}]}
    _g0 = _cmm.get_config_manager; _cmm.get_config_manager = lambda: _CM()
    try:
        p = asyncio.run(H.process({"op": "sql", "sql": q}, {"session_id": "iso", "user_uuid": "u1", "profile_id": "p1"}))
        check("an AGENT rule refuses too, named as the owner's", p.metadata.get("error_code") == "isolation_policy"
              and "agent's owner" in p.metadata["error"], p.metadata)
    finally:
        _cmm.get_config_manager = _g0
    from trusted_data_agent.agent.preflight import joins as J
    reg("onto", ("mcp:a", "SELECT inc_id FROM core.v_incident", [{"inc_id": 1}]),
                ("mcp:a", "SELECT obj_id FROM core.v_caseobject", [{"obj_id": 1}]))
    J.record_domains("onto", {"v_incident": "crime", "v_caseobject": "evidence"})
    p = call("onto", "SELECT * FROM s1 CROSS JOIN s2")
    check("the ONTOLOGY's cross-domain rule applies to a combine (same rule as the SQL checks)",
          p.metadata.get("error_code") == "cross_domain" and "v_incident" in p.metadata["error"]
          and "v_caseobject" in p.metadata["error"] and "was NOT run" in p.metadata["error"], p.metadata)
    p = call("onto", "SELECT inc_id AS id FROM s1 UNION ALL SELECT obj_id FROM s2")
    check("…but a UNION relates nothing across areas, as in the SQL checks", not p.metadata.get("error_code"), p.metadata)

    print("[14b] deployment rules name ENDPOINTS — they hold for every user reaching the system")
    from trusted_data_agent.core import source_endpoints as SE
    check("an HTTP MCP server's endpoint", SE.mcp_endpoint({"host": "HR.example.com", "port": 8890, "path": "mcp",
                                                          "transport": {"type": "http"}}) == "mcp:hr.example.com:8890/mcp")
    check("a stdio MCP server's endpoint", SE.mcp_endpoint({"transport": {"type": "stdio", "command": "uvx",
                                                           "args": ["td-mcp"]}}) == "mcp:stdio:uvx td-mcp")
    check("a database connection's endpoint carries no credential",
          SE.connection_endpoint({"driver": "postgresql", "host": "crm.local", "port": 5432, "database": "crm",
                                  "username": "u", "password": "secret"}) == "db:postgresql://crm.local:5432/crm")
    check("a file database's endpoint is its path", SE.connection_endpoint({"driver": "duckdb", "database": "/d/x.duckdb"})
          == "db:duckdb:file:/d/x.duckdb")
    EPS = {"mcp:hr-of-user1": "mcp:hr.example.com:8890/mcp", "mcp:hr-of-user2": "mcp:hr.example.com:8890/mcp",
           "conn:crm-1": "db:postgresql://crm.local:5432/crm"}
    resolved = []
    def _ep(sid):
        resolved.append(sid); return EPS.get(sid)
    rule = CS.normalize_rules([{"endpoints": ["mcp:hr.example.com:8890/mcp", "db:postgresql://crm.local:5432/crm"],
                                "reason": "HR never meets CRM"}], "deployment administrator")
    for uid_src in ("mcp:hr-of-user1", "mcp:hr-of-user2"):
        stmts = {1: dict(st(1, [{"k": 1}]), source_id=uid_src), 2: dict(st(2, [{"k": 1}]), source_id="conn:crm-1")}
        e = CS.isolation_refusal("SELECT * FROM s1 JOIN s2 ON s1.k = s2.k", stmts, rule, "", endpoint_of=_ep)
        check(f"the rule holds for a user whose HR server id is {uid_src}", e is not None and e.code == "isolation_policy", e)
    stmts_same = {1: dict(st(1, [{"k": 1}]), source_id="mcp:hr-of-user1"), 2: dict(st(2, [{"k": 1}]), source_id="mcp:hr-of-user2")}
    check("two ids reaching the SAME endpoint are one source, not a violation",
          CS.isolation_refusal("SELECT * FROM s1 JOIN s2 ON s1.k = s2.k", stmts_same, rule, "", endpoint_of=_ep) is None)
    resolved.clear()
    CS.isolation_refusal("SELECT * FROM s1 JOIN s2 ON s1.k = s2.k", stmts, [], "", endpoint_of=_ep)
    check("no rule ⇒ no endpoint is resolved (nothing extra for anyone without rules)", resolved == [], resolved)

    print("[14c] a combine of a combine is judged on the ORIGINAL sources")
    reg("chain", ("mcp:hr", "SELECT emp_id, salary FROM hr.pay", [{"emp_id": 1, "salary": 9}]),
                 ("mcp:hr", "SELECT emp_id, grade FROM hr.grades", [{"emp_id": 1, "grade": "A"}]),
                 ("conn:crm", "SELECT emp_id, deals FROM crm.reps", [{"emp_id": 1, "deals": 3}]))
    p = call("chain", "SELECT a.emp_id, a.salary, b.grade FROM s1 a JOIN s2 b ON a.emp_id = b.emp_id")
    check("an HR-only combine runs and carries its origin", not p.metadata.get("error_code")
          and RS.statements("chain")[-1].get("origin_sources") == ["mcp:hr"], RS.statements("chain")[-1].get("origin_sources"))
    APP_CONFIG.COMBINE_ISOLATION = [{"sources": ["mcp:hr", "conn:crm"]}]
    p = call("chain", "SELECT x.salary, c.deals FROM s4 x JOIN s3 c ON x.emp_id = c.emp_id")
    check("combining that result with CRM is refused — the origin, not 'combine', is judged",
          p.metadata.get("error_code") == "isolation_policy", p.metadata)
    APP_CONFIG.COMBINE_ISOLATION = []

    print("[15] deployment rules persist; an agent's rules enter the deployment half only when present")
    from trusted_data_agent.api import admin_routes as AR
    APP_CONFIG.COMBINE_ISOLATION = [{"sources": ["b", "a"], "reason": "r"}, {"sources": ["x"]}]
    check("the admin view is the combine step's own normalization",
          AR._combine_rules_normalized() and [sorted(r["sources"]) for r in AR._combine_rules_normalized()] == [["a", "b"]])
    check("the admin lists endpoints through a route that derives them by code",
          "list_endpoints" in inspect.getsource(AR.list_source_endpoints))
    APP_CONFIG.COMBINE_ISOLATION = []
    from trusted_data_agent.auth import database as DB
    check("a startup sync restores the rules", "COMBINE_ISOLATION" in inspect.getsource(DB._sync_combine_isolation_to_config)
          and "_sync_combine_isolation_to_config()" in inspect.getsource(DB))
    from trusted_data_agent.core.trust import deployment as DEP
    base = DEP.build_deployment_signature({"provider": "p", "model": "m"}, None, [])
    with_empty = DEP.build_deployment_signature({"provider": "p", "model": "m"}, None, [], combine_isolation=[])
    with_rule = DEP.build_deployment_signature({"provider": "p", "model": "m"}, None, [],
                                               combine_isolation=[{"sources": ["conn:b", "mcp:a"]}])
    check("no agent rule: the signature is byte-identical", DEP.deployment_hash(base) == DEP.deployment_hash(with_empty))
    check("an agent rule enters the deployment half", DEP.deployment_hash(with_rule) != DEP.deployment_hash(base)
          and with_rule["combine_isolation"] == [["conn:b", "mcp:a"]])
    check("removing it is named as drift", any("combine isolation rule removed" in d for d in DEP.describe_drift(with_rule, base)))
    real = DEP.deployment_signature({"id": "p-iso", "profile_type": "llm_only",
                                     "combineIsolation": [{"sources": ["mcp:a", "conn:b"]}]}, "u1")
    plain = DEP.deployment_signature({"id": "p-iso", "profile_type": "llm_only"}, "u1")
    check("the REAL deployment signature of a profile carries its rules (the wiring, not just the builder)",
          real.get("combine_isolation") == [["conn:b", "mcp:a"]] and "combine_isolation" not in plain,
          (real.get("combine_isolation"), sorted(plain)))
    print("[16] Optimize: a plan names a phase's result — parallel phases finish in any order (O1)")
    RS.begin_turn("ph")
    async def _phase(n, delay, rows):
        tok = RS.set_phase(n)
        try:
            await asyncio.sleep(delay)
            RS.record("ph", "base_readQuery", {"sql": f"SELECT * FROM t{n}"}, {"results": rows}, source=f"mcp:s{n}")
        finally:
            RS.reset_phase(tok)
    async def _both():
        await asyncio.gather(_phase(1, 0.05, BOOKS), _phase(2, 0.0, REVIEWS))   # phase 2 finishes FIRST
    asyncio.run(_both())
    check("phase 2 finished first and became statement 1", RS.statements("ph")[0].get("phase") == 2, RS.statements("ph")[0])
    check("each phase's result is found by its phase, whatever the order",
          RS.for_phase("ph", 1)["statement"] == "SELECT * FROM t1" and RS.for_phase("ph", 2)["statement"] == "SELECT * FROM t2")
    p = call("ph", "SELECT a.title, AVG(b.rating) AS r FROM p1 a JOIN p2 b ON a.book_id = b.book_id GROUP BY a.title ORDER BY a.title")
    check("p1/p2 resolve to the statements those phases produced", not p.metadata.get("error_code")
          and [x["title"] for x in p.spec["rows"]] == ["A", "B"], (p.metadata, p.spec.get("rows")))
    check("the resolution is named, never silent",
          any("p1 = statement 2" in n and "p2 = statement 1" in n for n in p.spec.get("notes") or []), p.spec.get("notes"))
    p = call("ph", "SELECT * FROM p9")
    check("an unknown phase is refused, naming the fix", p.metadata.get("error_code") == "no_such_phase", p.metadata)
    pj = asyncio.run(H.process({"op": "join", "statement": "p1", "statement_b": "p2", "on": "book_id"},
                               {"session_id": "ph", "user_uuid": "u1"}))
    check("join/diff statements may name a phase too", not pj.metadata.get("error_code")
          and pj.metadata["receipt"]["statements"] == [2, 1], pj.metadata)
    RS.begin_turn("nophase")
    RS.record("nophase", "base_readQuery", {"sql": "SELECT 1"}, {"results": [{"a": 1}]})
    check("outside a plan phase nothing is added (named exception: `phase` only in Optimize)",
          "phase" not in RS.statements("nophase")[0])
    import inspect as _insp
    from trusted_data_agent.agent import phase_executor as _pe
    # driven, not grepped: the REAL execute_phase over a stub whose phase body records a result
    class _StubPE:
        async def _execute_standard_phase(self, phase, **kw):
            RS.record("pe", "base_readQuery", {"sql": "SELECT 1"}, {"results": [{"a": 1}]})
            yield "event"
    RS.begin_turn("pe")
    async def _drive():
        async for _ in _pe.PhaseExecutor.execute_phase(_StubPE(), {"phase": 4, "goal": "g"}):
            pass
    asyncio.run(_drive())
    check("the real execute_phase marks the result with its phase number",
          RS.statements("pe")[-1].get("phase") == 4, RS.statements("pe")[-1])
    check("…and releases the mark afterwards", RS._PHASE.get() is None)

    print("[17] transparency: sources named on a multi-source turn only (A14, single-source rule)")
    from trusted_data_agent.core import source_endpoints as SE
    from trusted_data_agent.agent import compute_lineage as LIN
    one = [{"n": 1, "source_id": "mcp:a", "row_count": 3}, {"n": 2, "source_id": "mcp:a", "row_count": 2}]
    two = [{"n": 1, "source_id": "conn:hr", "row_count": 40, "ts": "t1"},
           {"n": 2, "source_id": "mcp:crm", "row_count": 25, "ts": "t2"}]
    comb = two + [{"n": 3, "source_id": "combine", "row_count": 4, "inputs": [1, 2],
                   "origin_sources": ["conn:hr", "mcp:crm"], "ts": "t3"}]
    check("one source is not multi-source", SE.multi_source(one) is False)
    check("two sources are", SE.multi_source(two) is True)
    check("a combine is, even over statements of one source",
          SE.multi_source([{"n": 1, "source_id": "mcp:a"}, {"n": 2, "source_id": "combine", "inputs": [1]}]) is True)
    check("no statements / not a list is not", SE.multi_source([]) is False and SE.multi_source(None) is False)
    names = {"conn:hr": "HR warehouse", "mcp:crm": "CRM server"}
    calls = []
    def _namer(sid):
        calls.append(sid); return names.get(sid)
    check("single-source: no sources block, and no source is even looked up",
          SE.turn_sources(one, "u1", namer=_namer) is None and calls == [])
    ts2 = SE.turn_sources(comb, "u1", namer=_namer)
    check("each source listed once, named, with its statements and rows",
          [(x["name"], x["statements"], x["rows"]) for x in ts2["sources"]]
          == [("HR warehouse", [1], 40), ("CRM server", [2], 25)], ts2)
    check("each combine lists its inputs with source name and time",
          ts2["combines"][0]["inputs"] == [
              {"n": 1, "source_id": "conn:hr", "source_name": "HR warehouse", "ts": "t1", "row_count": 40},
              {"n": 2, "source_id": "mcp:crm", "source_name": "CRM server", "ts": "t2", "row_count": 25}], ts2)
    check("a combine is not listed as a source of its own", all(x["source_id"] != "combine" for x in ts2["sources"]))
    check("each source is looked up once per turn (memoised)", sorted(calls) == ["conn:hr", "mcp:crm"], calls)
    check("the step title names a combine", LIN.step_title({"sources": ts2}) == "Number Lineage — Combined Results")
    check("the step title names several sources",
          LIN.step_title({"sources": SE.turn_sources(two, "u1", namer=_namer)}) == "Number Lineage — Several Sources")
    check("single-source: the step title is exactly as before",
          LIN.step_title({}) == "Number Lineage" and LIN.step_title(None) == "Number Lineage")
    # the read seam — driven, with the name lookup stubbed
    _orig_sn = SE.source_name
    looked = []
    SE.source_name = lambda sid, uid: (looked.append(sid), names.get(sid))[1]
    try:
        c1 = {"statements": [dict(x) for x in one]}
        before = json.dumps(c1, sort_keys=True)
        check("read seam: a single-source record is untouched and costs no lookup",
              SE.enrich_for_reader(c1, "u1") is False and json.dumps(c1, sort_keys=True) == before and looked == [])
        c2 = {"statements": [dict(x) for x in comb]}
        check("read seam: a multi-source record is flagged and every source named",
              SE.enrich_for_reader(c2, "u1") is True and c2.get("multi_source") is True
              and c2["statements"][0]["source_name"] == "HR warehouse"
              and c2["statements"][2]["origin_names"] == {"conn:hr": "HR warehouse", "mcp:crm": "CRM server"}, c2)
        check("read seam: a deleted source keeps its id (no name invented)",
              (lambda c: (SE.enrich_for_reader(c, "u1"), "source_name" not in c["statements"][1])[1])(
                  {"statements": [{"n": 1, "source_id": "conn:hr"}, {"n": 2, "source_id": "mcp:gone"}]}))
    finally:
        SE.source_name = _orig_sn
    import ast as _ast
    _routes = open("src/trusted_data_agent/api/routes.py").read()
    _fn = next(n for n in _ast.walk(_ast.parse(_routes)) if isinstance(n, _ast.AsyncFunctionDef) and n.name == "get_turn_details")
    check("the turn-details route reads through the one seam",
          any(isinstance(c, _ast.Call) and getattr(c.func, "id", "") == "enrich_for_reader" for c in _ast.walk(_fn)))
    _ex = open("src/trusted_data_agent/agent/executor.py").read()
    _cv = open("src/trusted_data_agent/agent/engines/conversation_engine.py").read()
    check("both engines title the lineage step from the one rule (live == reload)",
          "_lin_title(self._compute_lineage_event)" in _ex and "_lin_title(_compute_lineage_event)" in _cv
          and '"step": "Number Lineage"' not in _ex and '"step": "Number Lineage"' not in _cv)
    _m = next(n for n in _ast.walk(_ast.parse(_ex)) if isinstance(n, _ast.FunctionDef) and n.name == "_run_compute_lineage")
    check("the lineage seam attaches the turn's sources",
          any(isinstance(c, _ast.Call) and getattr(c.func, "id", "") == "turn_sources" for c in _ast.walk(_m)))

    print("[18] configuration: the Data sources view and an agent's own rules (world-class UX, server side)")
    ok_rules = SE.clean_agent_rules([{"sources": ["mcp:a", "conn:b"], "reason": " HR  "},
                                     {"sources": ["conn:b", "mcp:a"]}])
    check("agent rules are cleaned: sorted members, trimmed reason, a duplicate rule dropped",
          ok_rules == [{"sources": ["conn:b", "mcp:a"], "reason": "HR"}], ok_rules)
    for bad, why in [([{"sources": ["mcp:a"]}], "one source"), ([{"sources": ["mcp:a", "mcp:a"]}], "the same source twice"),
                     ([{"sources": ["mcp:a", "combine"]}], "not a source id"), ("x", "not a list"),
                     ([{"endpoints": ["a", "b"]}], "endpoints instead of sources")]:
        try:
            SE.clean_agent_rules(bad); refused = False
        except ValueError:
            refused = True
        check(f"a malformed agent rule is refused at the door ({why})", refused)
    check("None / [] are valid (no rules)", SE.clean_agent_rules(None) == [] and SE.clean_agent_rules([]) == [])
    view = SE.sources_view(
        [{"id": "s1", "name": "TD MCP", "host": "uderia.com", "port": 8888, "path": "/mcp", "transport": {"type": "http"}}],
        [{"id": "hr", "name": "HR", "driver": "teradata", "host": "hr-host", "port": 1025, "database": "hr", "password": "SECRET"},
         {"id": "x", "name": "X", "driver": "jdbc", "host": "h"}],
        [{"endpoints": ["db:teradata://hr-host:1025/hr", "mcp:uderia.com:8888/mcp"], "reason": "never"},
         {"endpoints": ["db:teradata://hr-host:1025/hr", "db:postgresql://elsewhere:5432/z"], "reason": "not mine"}],
        {"available": True})
    check("the view lists servers and connections with their endpoints",
          view["mcp_servers"][0]["endpoint"] == "mcp:uderia.com:8888/mcp"
          and view["connections"][0]["endpoint"] == "db:teradata://hr-host:1025/hr", view)
    check("no credential ever reaches the view", "SECRET" not in json.dumps(view))
    check("a non-direct driver is listed but not queryable", [c["queryable"] for c in view["connections"]] == [True, False])
    check("an admin rule is mapped onto the user's own sources, and says whether it binds two of them",
          view["admin_rules"][0]["yours"] == {"db:teradata://hr-host:1025/hr": ["conn:hr"], "mcp:uderia.com:8888/mcp": ["mcp:s1"]}
          and view["admin_rules"][0]["applies"] is True and view["admin_rules"][1]["applies"] is False, view["admin_rules"])
    _sv = {k: getattr(APP_CONFIG, k, None) for k in ("COMPUTE_LINEAGE", "COMPUTE_SQL")}
    try:
        APP_CONFIG.COMPUTE_LINEAGE, APP_CONFIG.COMPUTE_SQL = True, False
        cs = SE.combine_state()
        check("combining state names the switch that is off", cs["available"] is False and "TDA_COMPUTE_SQL" in cs["reason"], cs)
        APP_CONFIG.COMPUTE_LINEAGE = False
        check("…or its parent", "TDA_COMPUTE_LINEAGE" in SE.combine_state()["reason"])
        APP_CONFIG.COMPUTE_LINEAGE, APP_CONFIG.COMPUTE_SQL = True, True
        check("…and says nothing is off when both are on", SE.combine_state() == {"available": True, "reason": "", "incomplete_inputs": SE.combine_state()["incomplete_inputs"]})
    finally:
        for k, v in _sv.items():
            setattr(APP_CONFIG, k, v)
    _rr = open("src/trusted_data_agent/api/rest_routes.py").read()
    _tree = _ast.parse(_rr)
    _fns = {n.name: n for n in _ast.walk(_tree) if isinstance(n, _ast.AsyncFunctionDef)}
    for _name in ("create_profile", "update_profile"):
        _f = _fns.get(_name)
        check(f"{_name} validates the agent's rules at the door",
              _f is not None and any(isinstance(c, _ast.Call) and getattr(c.func, "id", "") == "_clean_combine_isolation" for c in _ast.walk(_f)))
    check("GET /v1/data-sources builds the view from the one store and the admin rules",
          "data_sources_view" in _fns and '"/v1/data-sources"' in _rr and "_se._connections(user_uuid)" in _rr)
    _se_src = open("src/trusted_data_agent/core/source_endpoints.py").read()
    check("source endpoints read connections through the ONE store (legacy canvas included), never the raw config",
          "get_database_connections(" not in _se_src)

    print("[19] results under the wrong numbers — one re-assignment repaired by code (found live, 2026-09-28)")
    # the live turn: two queries in parallel; the Navigator finished first, so its sales became
    # statement 1 and the MCP customer count statement 2 — the model wrote s1/s2 in CALL order
    SALES = [{"State": "MD", "SaleCount": 4}, {"State": "OR", "SaleCount": 6}, {"State": "ES", "SaleCount": 9}]
    CUST = [{"State": "MD", "CustomerCount": 2}, {"State": "OR", "CustomerCount": 2}, {"State": "ES", "CustomerCount": 3}]
    fresh("sw", ("SELECT c.State, COUNT(*) AS SaleCount FROM sales", SALES), ("SELECT State, COUNT(*) AS CustomerCount FROM c", CUST))
    live_sql = ("SELECT s2.State, s2.SaleCount, s1.CustomerCount, CAST(s2.SaleCount AS FLOAT) / s1.CustomerCount AS spc "
                "FROM s1 JOIN s2 ON s1.State = s2.State ORDER BY spc DESC LIMIT 3")
    r = call("sw", live_sql)
    check("the live statement is repaired and runs", not r.metadata.get("error_code"), r.metadata)
    rows = {x["State"]: x for x in r.spec.get("rows") or []}
    check("…with the RIGHT numbers (sales over customers, not a swap of them)",
          rows.get("ES", {}).get("SaleCount") == 9 and rows.get("ES", {}).get("CustomerCount") == 3
          and abs(rows["ES"]["spc"] - 3.0) < 1e-9, r.spec.get("rows"))
    check("the repair is named to the model and on the card",
          any("wrong numbers" in n and "s1 → s2" in n and "s2 → s1" in n for n in (r.spec.get("notes") or [])), r.spec.get("notes"))
    _ran = (r.metadata.get("receipt") or {}).get("sql", "")
    check("the receipt records the SQL that actually ran (re-pointed, qualifiers with it)",
          "FROM s2 JOIN s1 ON s2.State = s1.State" in _ran and "s1.SaleCount" in _ran and "s2.CustomerCount" in _ran, _ran)
    fix = CS.repair_table_assignment("SELECT a.SaleCount, b.CustomerCount FROM s2 a JOIN s1 b ON a.State = b.State",
                                     {n: st(n, rows_) for n, rows_ in [(1, SALES), (2, CUST)]})
    check("aliased tables are re-pointed; the aliases the columns use are kept",
          fix is not None and fix[1] == {"s1": "s2", "s2": "s1"} and "a.SaleCount" in fix[0].replace('"', ''), fix)
    same = {1: st(1, [{"k": 1, "v": 2}]), 2: st(2, [{"k": 1, "v": 3}])}
    check("two results with the same columns: never guessed", CS.repair_table_assignment("SELECT s1.nope FROM s1 JOIN s2 ON s1.k = s2.k", same) is None)
    # binds as written, and a swap would ALSO bind (same columns on both sides) — must stay as written
    check("a statement that binds as written is never re-pointed",
          CS.repair_table_assignment("SELECT s1.v, s2.k FROM s1 JOIN s2 ON s1.k = s2.k", same) is None)
    # does not bind, and TWO different re-assignments would: ambiguous — never guessed
    amb = {1: st(1, [{"a": 1}]), 2: st(2, [{"a": 1, "b": 2}]), 3: st(3, [{"a": 1, "b": 3}])}
    check("several re-assignments fit: never guessed (the model is told what each table is)",
          CS.repair_table_assignment("SELECT s1.b, s2.a, s3.a FROM s1 JOIN s2 ON s1.a = s2.a JOIN s3 ON s1.a = s3.a", amb) is None)
    r2 = call("sw", "SELECT s1.State, s1.Revenue FROM s1 JOIN s2 ON s1.State = s2.State")
    check("a column no result holds: refused, naming what each table is and why numbers can surprise",
          r2.metadata.get("error_code") and "s1 = the result of base_readQuery (State, SaleCount)" in r2.metadata.get("error", "")
          and "order results ARRIVED" in r2.metadata.get("error", ""), r2.metadata.get("error"))
    fresh("sw3", ("q1", SALES), ("q2", CUST), ("q3", [{"State": "MD", "Region": "East"}, {"State": "OR", "Region": "West"}, {"State": "ES", "Region": "Abroad"}]))
    r3 = call("sw3", "SELECT s3.SaleCount, s1.CustomerCount, s2.Region FROM s1 JOIN s2 ON s1.State = s2.State JOIN s3 ON s1.State = s3.State")
    check("three results under rotated numbers are re-pointed when exactly one rotation fits",
          not r3.metadata.get("error_code") and len(r3.spec.get("rows") or []) == 3, (r3.metadata.get("error"), r3.spec.get("rows")))

    print("[19b] a single table under the wrong number; re-runs of the same query are one answer (found live)")
    fresh("sw1", ("SELECT c.State, COUNT(*) AS SaleCount FROM sales", SALES), ("SELECT State, COUNT(*) AS CustomerCount FROM c", CUST))
    r = call("sw1", "SELECT s2.State, s2.SaleCount FROM s2 ORDER BY s2.SaleCount DESC LIMIT 3")
    check("one table named under the wrong number is re-pointed to the only result that holds its columns",
          not r.metadata.get("error_code") and [x["State"] for x in r.spec.get("rows") or []] == ["ES", "OR", "MD"], (r.metadata, r.spec.get("rows")))
    rc = r.metadata.get("receipt") or {}
    check("the receipt names the input that RAN, with its time and rows", rc.get("statements") == [1]
          and list(rc.get("input_times") or {}) == ["1"] and rc.get("row_count") == 3, rc)
    fresh("sw2", ("SELECT c.State, COUNT(*) AS SaleCount FROM sales", SALES), ("SELECT State, COUNT(*) AS CustomerCount FROM c", CUST),
          ("SELECT c.State, COUNT(*) AS SaleCount FROM sales", SALES))
    fix = CS.repair_table_assignment("SELECT s2.SaleCount FROM s2", {x["n"]: x for x in RS.statements("sw2")})
    check("a re-run of the same query is not ambiguity — the latest is taken", fix is not None and fix[1] == {"s2": "s3"}, fix)
    fresh("sw4", ("SELECT a FROM x", [{"State": "MD", "SaleCount": 1}]), ("SELECT b FROM y", CUST),
          ("SELECT c FROM z", [{"State": "MD", "SaleCount": 7}]))
    check("two DIFFERENT results holding the column: never guessed",
          CS.repair_table_assignment("SELECT s2.SaleCount FROM s2", {x["n"]: x for x in RS.statements("sw4")}) is None)
    r = call("sw4", "SELECT s2.SaleCount FROM s2")
    check("…and the refusal lists EVERY table this turn, not only the one named",
          all(f"s{n} = the result of" in r.metadata.get("error", "") for n in (1, 2, 3)), r.metadata.get("error"))
    # a re-point to OTHER inputs passes the same isolation check the originals did
    APP_CONFIG.COMBINE_ISOLATION = [{"sources": ["mcp:A", "mcp:C"], "reason": "A never meets C"}]
    RS.begin_turn("swi")
    RS.record("swi", "base_readQuery", {"sql": "qa"}, {"results": [{"State": "MD", "x": 1}]}, source="mcp:A")
    RS.record("swi", "base_readQuery", {"sql": "qb"}, {"results": [{"State": "MD", "y": 2}]}, source="mcp:B")
    RS.record("swi", "base_readQuery", {"sql": "qc"}, {"results": [{"State": "MD", "SaleCount": 3}]}, source="mcp:C")
    r = call("swi", "SELECT s1.x, s2.SaleCount FROM s1 JOIN s2 ON s1.State = s2.State")
    check("a re-point that would join two isolated sources is NOT applied", r.metadata.get("error_code") == "combine_failed"
          and "Tables available" in r.metadata.get("error", ""), r.metadata)
    APP_CONFIG.COMBINE_ISOLATION = []

    print("[20] numbers written as text load as numbers; codes stay text (decision 12, found live)")
    # the live shape: the Teradata MCP server returns every value as JSON text
    txt = {1: st(1, [{"State": "MD", "SaleCount": 4}, {"State": "ES", "SaleCount": 9}]),
           2: st(2, [{"State": "MD", "CustomerCount": "2"}, {"State": "ES", "CustomerCount": "3"}])}
    r = CS.run("SELECT s1.State, s1.SaleCount / s2.CustomerCount AS spc FROM s1 JOIN s2 ON s1.State = s2.State ORDER BY spc DESC", txt)
    check("the live statement divides without casts", r["status"] == "success"
          and [round(x["spc"], 6) for x in r["results"]] == [3.0, 2.0], r)
    check("…and the conversion is named", any("loaded as numbers" in f and "s2.CustomerCount" in f for f in r["findings"]), r.get("findings"))
    T = CS._numeric_text_type
    check("plain integers as text -> BIGINT", T(["2", "10", None, "-7"]) == "BIGINT")
    check("decimals as text -> DOUBLE", T(["2.5", "3"]) == "DOUBLE")
    check("text mixed with real numbers -> numeric", T([1, "2"]) == "BIGINT" and T([1.5, "2"]) == "DOUBLE")
    check("beyond BIGINT -> DOUBLE, never an overflow", T([str(2 ** 64)]) == "DOUBLE")
    for bad, why in [(["007", "8"], "a leading zero (a code)"), (["1,000"], "grouping"), ([" 5"], "a space"),
                     (["5", "abc"], "one non-number"), (["1e5"], "exponent form"), (["", "5"], "an empty string"),
                     (["+5"], "a plus sign"), ([True, "1"], "a boolean")]:
        check(f"stays text: {why}", T(bad) is None, T(bad))
    check("a column with no text is left to its own type (nothing to convert, nothing named)",
          T([1, 2]) is None and T([None, None]) is None and T([]) is None)
    r = CS.run("SELECT s1.v FROM s1 ORDER BY s1.v", {1: st(1, [{"v": 3}, {"v": 1}])})
    check("already-numeric columns: no conversion finding", not any("loaded as numbers" in f for f in r["findings"]), r.get("findings"))
    r = CS.run("SELECT s1.code FROM s1 ORDER BY s1.code", {1: st(1, [{"code": "007"}, {"code": "010"}])})
    check("codes keep their leading zeros", [x["code"] for x in r["results"]] == ["007", "010"], r)

finally:
    for k, v in _SAVED.items():
        setattr(APP_CONFIG, k, v) if v is not None else (hasattr(APP_CONFIG, k) and setattr(APP_CONFIG, k, v))

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)  — the combine step joins across sources, checked by code")
sys.exit(1 if FAIL else 0)
