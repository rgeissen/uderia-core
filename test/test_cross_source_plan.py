"""Multi-source implementation plan, stage 3 — the cross-source plan shape (Optimize).

    [3a] structured holdings: each server's TABLES (and columns when the catalog is small) read
         with its database list through the server's own tools, cached with the list, bounded;
         rendered in the one Data sources note both lanes read; seeded into the session's column
         store; nothing for a single-server agent
    [3b] the cross-source plan shape, decided by code: a statement over two servers is split into
         one fetch per server + the ORIGINAL statement as a combine (TDA_Compute op sql) — by the
         planner pass `cross_source_shape` (p<N>) and, for a statement the planner did not see, by
         the phase executor at the refusal (s<N>); a shape the splitter declines is withdrawn to
         the tactical step (planner) or ends the phase with the refusal (executor) — a one-server
         substitute is NEVER the phase's answer; no LLM round is spent on either path

The stage-0 finding behind 3a (traced on real turns): the planner knew each server's DATABASES
but not their tables, so it invented tables (`purchases`) or put real ones in the wrong database
(`ms_fixture_sales.Customers`), and no correction could repair that.

No server, no network (fake MCP servers). Run: python test/test_cross_source_plan.py
"""
import ast
import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from trusted_data_agent.agent import source_routing as sr  # noqa: E402
from trusted_data_agent.core import agent_mcp_servers as ams  # noqa: E402
from trusted_data_agent.core import tool_identity as ti  # noqa: E402
from trusted_data_agent.core.agent_mcp_servers import AgentServer  # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f" — {detail}" if detail != "" else ""))


U = "user-xsrc"
SERVERS = (AgentServer("srvA", None, ("*",), ()), AgentServer("srvB", "crm", ("*",), ()))
NAMES = {"srvA": "sales", "srvB": "crm"}

# What each fake server's login reads: database → table → columns
CATALOG = {
    "srvA": {"ms_sales": {"Sales": ["SaleID", "CustomerID", "TotalAmount", "SaleDate"],
                          "SaleDetails": ["SaleID", "ProductID", "Quantity", "UnitPrice"]}},
    "srvB": {"ms_crm": {"Customers": ["CustomerID", "FirstName", "LastName", "State"],
                        "Products": ["ProductID", "ProductName", "ProductType"],
                        "ServiceTickets": ["TicketID", "CustomerID", "Status"]}},
}


class armed:
    def __init__(self, servers=SERVERS):
        self.servers = servers

    def __enter__(self):
        self.t1 = ti.set_turn_servers({s.alias: s.server_id for s in self.servers if s.alias})
        self.t2 = ams.set_turn_source_labels("srvA", NAMES)
        self.t3 = sr.set_turn(U, self.servers)
        return self

    def __exit__(self, *a):
        sr.reset_turn(self.t3)
        ams.reset_turn_source_labels(self.t2)
        ti.reset_turn_servers(self.t1)


def _res(payload, error=False):
    return SimpleNamespace(isError=error, content=[SimpleNamespace(text=json.dumps(payload))])


class FakeSession:
    """A fake MCP server: a database list, a table list and a column description tool, with the
    argument names the Teradata MCP server declares. Records every call."""
    def __init__(self, sid, calls, broken=False):
        self.sid, self.calls, self.broken = sid, calls, broken

    async def list_tools(self):
        T = lambda n, d, a: SimpleNamespace(name=n, description=d,
                                            inputSchema={"properties": {k: {} for k in a}})
        return SimpleNamespace(tools=[
            T("base_databaseList", "List all databases in the Teradata system.", ["scope"]),
            T("base_tableList", "Lists all tables in a database.", ["database_name"]),
            T("base_columnDescription", "Shows detailed column description of a table.",
              ["database_name", "obj_name"]),
            T("base_readQuery", "Execute a SQL query.", ["sql"]),
        ])

    async def call_tool(self, name, args):
        self.calls.append((self.sid, name, dict(args)))
        if self.broken and name != "base_databaseList":
            raise ConnectionError("server went away")
        cat = CATALOG[self.sid]
        if name == "base_databaseList":
            return _res({"status": "success", "results":
                         [{"DataBaseName": d} for d in list(cat) + ["DBC"]]})
        if name == "base_tableList":
            db = args["database_name"]
            return _res({"status": "success", "results":
                         [{"DataBaseName": db, "TableName": t} for t in cat.get(db, {})]
                         + [{"DataBaseName": "DBC", "TableName": "Noise"}]})   # a list tool that says too much
        if name == "base_columnDescription":
            cols = cat.get(args["database_name"], {}).get(args["obj_name"], [])
            return _res({"status": "success", "results": [{"TableName": args["obj_name"], "ColumnName": c}
                                                          for c in cols]})
        return _res({"status": "error", "error": "unexpected"}, error=True)


class FakeClient:
    def __init__(self, sid, calls, broken=False):
        self.sid, self.calls, self.broken = sid, calls, broken

    def session(self, sid):
        client = self

        class _Ctx:
            async def __aenter__(self_inner):
                return FakeSession(client.sid, client.calls, client.broken)

            async def __aexit__(self_inner, *a):
                return False
        return _Ctx()


def _fresh():
    sr._reset_all()
    sr._LIST_TOOL.clear()
    sr._ROLES.clear()


def probe(broken=(), calls=None):
    calls = [] if calls is None else calls
    with patch("trusted_data_agent.core.configuration_service.pooled_mcp_client",
               lambda sid, user=None: FakeClient(sid, calls, sid in broken)):
        asyncio.run(sr.ensure_lists(U, ["srvA", "srvB"]))
        asyncio.run(sr.ensure_catalogs(U, ["srvA", "srvB"]))
    return calls


# ---------------------------------------------------------------------------------------------
print("\n[3a.1] the catalog is read through each server's own tools and cached")
_fresh()
calls = probe()
tA, cA = sr.catalog_of(U, "srvA")
tB, cB = sr.catalog_of(U, "srvB")
check("server A's tables are its own", tA == {"ms_sales": ("Sales", "SaleDetails")}, tA)
check("server B's tables are its own", tB == {"ms_crm": ("Customers", "Products", "ServiceTickets")}, tB)
check("a list tool that answers for more than the database asked is filtered to it",
      "Noise" not in str(tA) + str(tB))
check("system catalogs are never listed", not any(c[1] == "base_tableList" and c[2].get("database_name") == "DBC"
                                                  for c in calls))
check("columns are read when the catalog is small", cB.get("ms_crm.Customers") == tuple(CATALOG["srvB"]["ms_crm"]["Customers"]))
check("the describe call uses the argument names the server declares",
      any(c[1] == "base_columnDescription" and set(c[2]) == {"database_name", "obj_name"} for c in calls))
n = len(calls)
probe(calls=calls)
check("a second turn reads nothing again (cached with the list)", len(calls) == n, len(calls) - n)

print("\n[3a.2] bounds")
_fresh()
with patch.object(sr, "CATALOG_COLUMN_TABLES", 2):
    probe()
tB, cB = sr.catalog_of(U, "srvB")
check("a server with more tables than the column bound keeps its tables…", len(tB["ms_crm"]) == 3)
check("…without columns", cB == {}, cB)
tA, cA = sr.catalog_of(U, "srvA")
check("a server within the bound still gets its columns", len(cA) == 2)
_fresh()
with patch.object(sr, "CATALOG_MAX_DBS", 1), \
        patch.dict(CATALOG["srvA"], {"ms_sales_hist": {"Archive": ["SaleID"]}}):
    calls = probe()
tA, _ = sr.catalog_of(U, "srvA")
tB, _ = sr.catalog_of(U, "srvB")
check("a server with more user databases than the bound keeps the database list only",
      tA == {} and not any(c[0] == "srvA" and c[1] == "base_tableList" for c in calls), (tA, calls))
check("…while a server within the bound still gets its tables", bool(tB))
_fresh()
with patch.object(sr, "CATALOG_MAX_TABLES", 1):
    probe()
tB, _ = sr.catalog_of(U, "srvB")
check("the table bound caps what a server keeps", sum(len(v) for v in tB.values()) == 1, tB)

print("\n[3a.3] failure")
_fresh()
calls = probe(broken=("srvB",))
tB, _ = sr.catalog_of(U, "srvB")
check("a catalog that cannot be read stays unread (the database list still stands)",
      tB is None and sr.known_list(U, "srvB") == frozenset({"ms_crm", "dbc"}), (tB, sr.known_list(U, "srvB")))
n = len(calls)
probe(broken=("srvB",), calls=calls)
check("…and is not re-read at once", not any(c[0] == "srvB" and c[1] == "base_tableList" for c in calls[n:]))
h = sr._entry(U, "srvB")
h.catalog_failed_at = time.monotonic() - sr.FAILED_PROBE_TTL - 1
n = len(calls)
probe(calls=calls)
check("…but is after the back-off", sr.catalog_of(U, "srvB")[0] is not None)

print("\n[3a.4] the catalog expires with the list")
_fresh()
probe()
h = sr._entry(U, "srvA")
h.ts = time.monotonic() - sr.HOLDINGS_TTL - 1
check("an expired list takes its catalog with it", sr.catalog_of(U, "srvA") == (None, {}))

print("\n[3a.5] the note the planner reads")
_fresh()
probe()
with armed():
    note = sr.turn_sources_note() or ""
    hold = sr.holdings_for_turn()
    cols = sr.turn_catalog_columns()
check("the note lists server B's tables under its database, with columns",
      "ms_crm.Customers (CustomerID, FirstName, LastName, State)" in note, note)
check("…and server A's", "ms_sales.Sales (SaleID, CustomerID, TotalAmount, SaleDate)" in note)
check("each table sits under its own server's line",
      note.index("ms_sales.Sales") < note.index("reach server 'crm'") < note.index("ms_crm.Customers"))
check("the note states the one-server-per-statement rule once a catalog is shown",
      "One statement reads the tables of ONE server" in note)
check("holdings_for_turn is structured per server",
      hold["primary"]["tables"] == {"ms_sales": ["Sales", "SaleDetails"]}
      and hold["crm"]["databases"] == ["ms_crm"] and hold["crm"]["label"] == "crm", hold)
check("turn_catalog_columns covers both servers", set(cols) == {"ms_sales.Sales", "ms_sales.SaleDetails",
                                                                "ms_crm.Customers", "ms_crm.Products",
                                                                "ms_crm.ServiceTickets"}, cols)
_fresh()
sr.learn_list(U, "srvA", ["ms_sales"]); sr.learn_list(U, "srvB", ["ms_crm"])
with armed():
    note = sr.turn_sources_note() or ""
check("without a catalog the note is the database list only (no table lines, no rule)",
      "    " not in note and "ONE server" not in note and "ms_crm" in note, note)
with patch.object(sr, "CATALOG_MAX_COLUMNS_SHOWN", 2):
    _fresh(); probe()
    with armed():
        note = sr.turn_sources_note() or ""
check("a long column list is bounded and says how many more", "(CustomerID, FirstName, +2 more)" in note, note)

print("\n[3a.6] single-server agent")
_fresh()
probe()
with armed(servers=SERVERS[:1]):
    check("no note", sr.turn_sources_note() is None)
    check("no holdings", sr.holdings_for_turn() == {})
    check("no columns seeded", sr.turn_catalog_columns() == {})

print("\n[3a.7] wiring")
src_es = open(os.path.join(ROOT, "src/trusted_data_agent/agent/execution_service.py")).read()
i = src_es.index("_srt.ensure_lists(")
seg = src_es[i:i + 1400]
check("the turn seam reads the catalogs after the lists", "_srt.ensure_catalogs(" in seg)
check("…skipping a server already found unreachable", "is_unavailable" in seg)
check("…and seeds the session's column store from them (origin: the source's own dictionary)",
      "record_columns(session_id, _cat_cols, origin=\"dictionary\")" in seg)
tree = ast.parse(open(os.path.join(ROOT, "src/trusted_data_agent/agent/source_routing.py")).read())
fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_probe_catalog")
names = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)} | \
        {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
check("the catalog resolves its tools by role, never by name", "resolve_roles_by_shape" in names
      and "base_tableList" not in ast.unparse(fn))


# =============================================================================== [3b]
from trusted_data_agent.agent import cross_source as cs  # noqa: E402
from trusted_data_agent.agent import result_sets as rs  # noqa: E402
from trusted_data_agent.agent import planner as pl  # noqa: E402
from trusted_data_agent.agent import phase_executor as pe  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
import trusted_data_agent.mcp_adapter.adapter as _adp  # noqa: E402

HOLD = {"ms_sales": "srvA", "ms_crm": "srvB"}
COLS = {"ms_sales.Sales": CATALOG["srvA"]["ms_sales"]["Sales"],
        "ms_sales.SaleDetails": CATALOG["srvA"]["ms_sales"]["SaleDetails"],
        "ms_crm.Customers": CATALOG["srvB"]["ms_crm"]["Customers"],
        "ms_crm.ServiceTickets": CATALOG["srvB"]["ms_crm"]["ServiceTickets"]}
holder = lambda d: HOLD.get(str(d).lower())  # noqa: E731

Q_JOIN = ("SELECT TOP 5 C.State, SUM(S.TotalAmount) AS TotalRevenue FROM ms_crm.Customers C "
          "JOIN ms_sales.Sales S ON C.CustomerID = S.CustomerID GROUP BY C.State ORDER BY TotalRevenue DESC")
Q_IN = ("SELECT SUM(TotalAmount) FROM ms_sales.Sales WHERE CustomerID IN "
        "(SELECT DISTINCT CustomerID FROM ms_crm.ServiceTickets WHERE Status = 'Open')")
Q_WHERE = ("SELECT AVG(S.TotalAmount) FROM ms_sales.Sales S JOIN ms_crm.Customers C ON S.CustomerID = C.CustomerID "
           "WHERE C.State = 'NY' AND S.TotalAmount > 0")
Q_NOALIAS = ("SELECT Customers.FirstName, SUM(Sales.TotalAmount) AS Total FROM ms_sales.Sales JOIN ms_crm.Customers "
             "ON Sales.CustomerID = Customers.CustomerID GROUP BY Customers.FirstName ORDER BY Total DESC")
Q_LEFT = ("SELECT C.State, COUNT(S.SaleID) AS n FROM ms_crm.Customers C LEFT JOIN ms_sales.Sales S "
          "ON C.CustomerID = S.CustomerID WHERE S.TotalAmount > 10 GROUP BY C.State")

print("\n[3b.1] the splitter")
sp = cs.split_statement(Q_JOIN, holder, COLS)
check("a two-server join splits into one fetch per server, FROM order", sp and [f.server_id for f in sp.fetches] == ["srvB", "srvA"])
check("each fetch reads ONLY the columns the statement uses of its table",
      sp and sp.fetches[0].sql == "SELECT C.State, C.CustomerID FROM ms_crm.Customers AS C"
      and sp.fetches[1].sql == "SELECT S.TotalAmount, S.CustomerID FROM ms_sales.Sales AS S", sp and [f.sql for f in sp.fetches])
comb = sp and sp.combine_sql(["p1", "p2"])
check("the combine is the ORIGINAL statement over the fetches — join, aggregate, ordering kept",
      comb == "SELECT C.State, SUM(S.TotalAmount) AS TotalRevenue FROM p1 AS C JOIN p2 AS S ON C.CustomerID = S.CustomerID "
              "GROUP BY C.State ORDER BY TotalRevenue DESC LIMIT 5", comb)
check("…rendered for the combine engine (TOP → LIMIT)", comb and "LIMIT 5" in comb and "TOP" not in comb)
check("the names are the caller's (s<N> at run time)", sp and "FROM s7 AS C JOIN s8 AS S" in sp.combine_sql(["s7", "s8"]))
check("describe names where each table is",
      sp and cs.describe(sp, lambda s: {"srvA": "sales", "srvB": "crm"}[s]) == "ms_crm.Customers is on server 'crm'; ms_sales.Sales is on server 'sales'")
sp = cs.split_statement(Q_IN, holder, COLS)
check("an IN (SELECT …) subquery on the other server becomes its own fetch, filtered there",
      sp and sp.fetches[1].kind == "in_subquery"
      and sp.fetches[1].sql == "SELECT DISTINCT CustomerID FROM ms_crm.ServiceTickets WHERE Status = 'Open'", sp and [f.sql for f in sp.fetches])
check("…and the combine reads the fetched column from it",
      sp and sp.combine_sql(["p1", "p2"]) == "SELECT SUM(TotalAmount) FROM p1 AS Sales WHERE CustomerID IN (SELECT CustomerID FROM p2)", sp and sp.combine_sql(["p1", "p2"]))
sp = cs.split_statement(Q_WHERE, holder, COLS)
check("a one-table WHERE conjunct is pushed into its fetch (INNER joins)",
      sp and sp.fetches[0].sql.endswith("WHERE S.TotalAmount > 0") and sp.fetches[1].sql.endswith("WHERE C.State = 'NY'"), sp and [f.sql for f in sp.fetches])
check("…and leaves the combine", sp and "WHERE" not in sp.combine_sql(["p1", "p2"]))
sp = cs.split_statement(Q_LEFT, holder, COLS)
check("under a LEFT JOIN nothing is pushed down (a filter on the optional side changes meaning)",
      sp and all("WHERE" not in f.sql for f in sp.fetches) and "WHERE S.TotalAmount > 10" in sp.combine_sql(["p1", "p2"]))
sp = cs.split_statement(Q_NOALIAS, holder, COLS)
check("tables qualified by name (no alias) split too", sp and sp.fetches[1].alias == "Customers" and "p2 AS Customers" in sp.combine_sql(["p1", "p2"]))
sp = cs.split_statement("SELECT State, SUM(TotalAmount) FROM ms_crm.Customers C JOIN ms_sales.Sales S ON C.CustomerID = S.CustomerID GROUP BY State", holder, COLS)
check("an unqualified column is attributed through the catalog when ONE table has it", sp and sp.fetches[0].columns == ("State", "CustomerID"))
check("…and declined when several could hold it",
      cs.split_statement("SELECT CustomerID, SUM(TotalAmount) FROM ms_crm.Customers C JOIN ms_sales.Sales S ON C.CustomerID = S.CustomerID GROUP BY CustomerID", holder, COLS) is None
      and "could belong to several tables" in (cs.why_declined("SELECT CustomerID, SUM(TotalAmount) FROM ms_crm.Customers C JOIN ms_sales.Sales S ON C.CustomerID = S.CustomerID GROUP BY CustomerID", holder, COLS) or ""))
check("one server → nothing to split (None, no reason)",
      cs.split_statement("SELECT SUM(TotalAmount) FROM ms_sales.Sales S JOIN ms_sales.SaleDetails D ON S.SaleID = D.SaleID", holder, COLS) is None
      and cs.why_declined("SELECT SUM(TotalAmount) FROM ms_sales.Sales S JOIN ms_sales.SaleDetails D ON S.SaleID = D.SaleID", holder, COLS) is None)
for name, q, why in [
    ("a table whose holder is unknown", Q_JOIN.replace("ms_crm", "ms_other"), "no single server"),
    ("an unqualified table", Q_JOIN.replace("ms_crm.Customers", "Customers"), "not qualified"),
    ("a CTE", "WITH t AS (SELECT * FROM ms_crm.Customers) SELECT t.State, SUM(S.TotalAmount) FROM t JOIN ms_sales.Sales S ON t.CustomerID = S.CustomerID GROUP BY t.State", "CTE"),
    ("SELECT *", "SELECT * FROM ms_crm.Customers C JOIN ms_sales.Sales S ON C.CustomerID = S.CustomerID", "SELECT *"),
    ("two tables of one server", "SELECT C.State, SUM(D.Quantity) FROM ms_crm.Customers C JOIN ms_sales.Sales S ON C.CustomerID = S.CustomerID JOIN ms_sales.SaleDetails D ON D.SaleID = S.SaleID GROUP BY C.State", "two tables of one server"),
    ("a subquery that is not IN (SELECT …)", "SELECT S.TotalAmount FROM ms_sales.Sales S WHERE EXISTS (SELECT 1 FROM ms_crm.Customers C WHERE C.CustomerID = S.CustomerID)", "only an IN"),
    ("a join without ON", "SELECT C.State, S.TotalAmount FROM ms_crm.Customers C CROSS JOIN ms_sales.Sales S", "without an ON"),
    ("a UNION", "SELECT State FROM ms_crm.Customers UNION SELECT CustomerID FROM ms_sales.Sales", "plain SELECT"),
]:
    got = cs.why_declined(q, holder, COLS) or ""
    check(f"declined: {name}", cs.split_statement(q, holder, COLS) is None and why in got, got)
# a raw-SQL "combine" over an earlier phase's rows (the shape the 3a note draws out of a planner)
Q_S1 = "SELECT SUM(TotalAmount) AS TotalPurchases FROM ms_sales.Sales WHERE CustomerID IN (SELECT CustomerID FROM s1)"
sp = cs.split_statement(Q_S1, holder, COLS)
check("a statement reading an earlier phase's rows as a table is a combine over ONE fetch",
      sp and len(sp.fetches) == 1 and sp.phase_refs == (1,) and sp.fetches[0].sql == "SELECT Sales.TotalAmount, Sales.CustomerID FROM ms_sales.Sales AS Sales", sp)
check("…the phase result is read as p<N>, the subquery as written",
      sp and sp.combine_sql(["p2"]) == "SELECT SUM(TotalAmount) AS TotalPurchases FROM p1 AS Sales WHERE CustomerID IN (SELECT CustomerID FROM p1)".replace("FROM p1 AS Sales", "FROM p2 AS Sales"), sp and sp.combine_sql(["p2"]))
check("…and at run time as the caller names it", sp and "IN (SELECT CustomerID FROM s9)" in sp.combine_sql(["s4"], lambda n: f"s{n + 8}"))
check("references_phase_result recognises s<N>, p<N> and result_of_phase_N, never a real table",
      cs.references_phase_result(Q_S1) and cs.references_phase_result("SELECT * FROM p2") and
      cs.references_phase_result("SELECT AVG(x) FROM result_of_phase_1") and not cs.references_phase_result(Q_JOIN)
      and not cs.references_phase_result("SELECT * FROM ms_sales.s1"))
sp = cs.split_statement("SELECT AVG(TotalAmount) FROM s2", holder, COLS)
check("a pure combine written as SQL (no real table) has no fetch", sp and sp.fetches == () and sp.combine_sql([]) == "SELECT AVG(TotalAmount) FROM p2 AS s2")
check("the split is deterministic", cs.split_statement(Q_JOIN, holder, COLS) == cs.split_statement(Q_JOIN, holder, COLS))
check("split_statement never raises on garbage", cs.split_statement("not sql at all ((", holder, COLS) is None
      and cs.split_statement(None, holder, COLS) is None)

print("\n[3b.2] the refusal carries the split, machine-readable")
_fresh()
probe()
with armed():
    check("holder_of names the ONE server holding a database", sr.holder_of(U, "ms_crm", ["srvA", "srvB"]) == "srvB"
          and sr.holder_of(U, "nowhere", ["srvA", "srvB"]) is None)
    d = asyncio.run(sr.plan_before("base_readQuery", "srvA", {"sql": Q_JOIN}, lane=sr.SOURCE_LANE_OPTIMIZE))
    check("plan_before refuses a spanning statement…", d.action == sr.REFUSE and d.refusal and "different MCP servers" in d.refusal, d)
    check("…with `split`: database → the server holding it", d.split == {"ms_crm": "srvB", "ms_sales": "srvA"}, d.split)
    d2 = asyncio.run(sr.plan_before("base_readQuery", "srvA", {"sql": "SELECT * FROM ms_crm.Customers"}, lane=sr.SOURCE_LANE_OPTIMIZE))
    check("a re-route carries no split", d2.action == sr.REROUTE and d2.split is None, d2)
src_adp = open(os.path.join(ROOT, "src/trusted_data_agent/mcp_adapter/adapter.py")).read()
i = src_adp.index("if d.action == _sr.REFUSE:")
seg = src_adp[i:i + 700]
check("the adapter stamps the code and the split on the refused result", "SPANS_CODE" in seg and '"split"' in seg and "d.split" in seg)


class FakeExecutor:
    def __init__(self, session_id, llm_responses=None, tools=("base_readQuery", "crm__base_readQuery", "TDA_Compute")):
        self.kg_enrichment_event = None
        self.dependencies = {"STATE": {"mcp_tools": {t: object() for t in tools}, "mcp_prompts": {}}}
        self.correction_history = []
        self.source = "test"
        self.user_uuid = U
        self.session_id = session_id
        self.active_profile_id = "p"
        self.original_user_input = "Which 5 states generated the most revenue?"
        self.workflow_goal_prompt = ""
        self.llm_prompts = []
        self.llm_responses = list(llm_responses or [])
        self._last_call_metadata = {}
        self.turn_input_tokens = self.turn_output_tokens = 0
        self.turn_cost_usd = 0.0
        self.current_phase_index = 0
        self.execution_depth = 0
        self.workflow_state = {}
        self.turn_action_history = []
        self.last_tool_output = None
        self.provenance = None
        self.is_synthesis_from_history = False
        self.events = []
        self.meta_plan = None
        self.current_provider = self.current_model = "x"

        class _R:
            def get_tactical_self_correction_prompt(self_):
                return "{tool_definition}{failed_command}{error_message}{user_question}{tools_context}{prompts_context}"
        self.prompt_resolver = _R()

    def _check_cancellation(self):
        pass

    def _get_correction_catalog(self):
        return "tools", "prompts"

    def _get_phase_state(self, n):
        return {"correction_history": []}

    def _log_system_event(self, e, *a, **k):
        self.events.append(e)

    def _format_sse_with_depth(self, data, event=None):
        return data

    def _add_to_structured_data(self, x):
        pass

    async def _call_llm_and_update_tokens(self, prompt=None, **k):
        self.llm_prompts.append(prompt)
        return (self.llm_responses.pop(0) if self.llm_responses else '{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}'), 10, 5


class _CompMgr:
    def get_active_components(self, *a, **k):
        return [SimpleNamespace(component_id="compute")]


def with_combine(fn, on=True):
    with patch.object(APP_CONFIG, "COMPUTE_LINEAGE", on), patch.object(APP_CONFIG, "COMPUTE_SQL", on), \
            patch("trusted_data_agent.components.manager.get_component_manager", lambda: _CompMgr()):
        return fn()


def run_pass(plan, servers=SERVERS, on=True):
    exe = FakeExecutor("cs-plan")
    exe.meta_plan = json.loads(json.dumps(plan))
    planner = pl.Planner(exe)
    with armed(servers):
        with_combine(lambda: list(planner._rewrite_plan_for_cross_source_shape()), on)
    return exe


BASE_PLAN = [
    {"phase": 1, "goal": "Top states by revenue.", "relevant_tools": ["base_readQuery"], "arguments": {"sql": Q_JOIN}},
    {"phase": 2, "goal": "Report.", "relevant_tools": ["TDA_FinalReport"], "arguments": {"source_data": "result_of_phase_1"}},
]

print("\n[3b.3] the planner pass")
_fresh()
probe()
exe = run_pass(BASE_PLAN)
plan = exe.meta_plan
check("the spanning phase becomes two fetch phases + a combine",
      [p["phase"] for p in plan] == [1, 2, 3, 4] and [p["relevant_tools"][0] for p in plan]
      == ["crm__base_readQuery", "base_readQuery", "TDA_Compute", "TDA_FinalReport"], [(p["phase"], p["relevant_tools"]) for p in plan])
check("each fetch runs on ITS server's tool, and says which server (3f-ready)",
      plan[0]["_server"] == "srvB" and plan[1]["_server"] == "srvA" and plan[0]["arguments"]["sql"].startswith("SELECT C.State"))
check("the combine names the fetch phases as p<N>", plan[2]["arguments"] == {"op": "sql", "sql": cs.split_statement(Q_JOIN, holder, COLS).combine_sql(["p1", "p2"])}, plan[2]["arguments"])
check("a later reference to the original phase now names the combine", plan[3]["arguments"]["source_data"] == "result_of_phase_3", plan[3])
check("the rewrite is named", any((e.get("details") or {}).get("pass") == "cross_source_shape" and "split into 2" in str(e) for e in exe.events))
exe2 = FakeExecutor("cs-plan2"); exe2.meta_plan = json.loads(json.dumps(plan))
with armed():
    with_combine(lambda: list(pl.Planner(exe2)._rewrite_plan_for_cross_source_shape()))
check("idempotent on replan (a split plan is left alone)", exe2.meta_plan == plan)
exe = run_pass(BASE_PLAN, servers=SERVERS[:1])
check("a single-server agent: byte-identical plan", exe.meta_plan == BASE_PLAN)
exe = run_pass(BASE_PLAN, on=False)
check("with the combine step off: byte-identical plan", exe.meta_plan == BASE_PLAN)
exe = run_pass([{"phase": 1, "goal": "g", "relevant_tools": ["base_readQuery"], "arguments": {"sql": {"source": "result_of_phase_0", "key": "sql"}}}])
check("a runtime-reference SQL is not touched", exe.meta_plan[0]["arguments"] == {"sql": {"source": "result_of_phase_0", "key": "sql"}})
exe = run_pass([{"phase": 1, "goal": "g", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT SUM(TotalAmount) FROM ms_sales.Sales"}}])
check("a one-server statement is not touched", exe.meta_plan[0]["arguments"]["sql"] == "SELECT SUM(TotalAmount) FROM ms_sales.Sales")
q_decl = "SELECT S.TotalAmount FROM ms_sales.Sales S WHERE EXISTS (SELECT 1 FROM ms_crm.Customers C WHERE C.CustomerID = S.CustomerID)"
exe = run_pass([{"phase": 1, "goal": "g", "relevant_tools": ["base_readQuery"], "arguments": {"sql": q_decl}}])
p0 = exe.meta_plan[0]
check("a shape the splitter declines is withdrawn to the tactical step with the split named",
      "sql" not in p0["arguments"] and p0["_sql_draft"] == q_decl and "different MCP servers" in p0["goal"]
      and "TDA_Compute" in p0["goal"] and "ms_crm is on server 'crm'" in p0["goal"], p0)
check("…never a one-server substitute written by the pass", "ms_sales.Sales" in p0["_sql_draft"] and len(exe.meta_plan) == 1)
exe = run_pass([
    {"phase": 1, "goal": "open tickets", "relevant_tools": ["crm__base_readQuery"], "arguments": {"sql": "SELECT DISTINCT CustomerID FROM ms_crm.ServiceTickets WHERE Status = 'Open'"}},
    {"phase": 2, "goal": "sum", "relevant_tools": ["base_readQuery"], "arguments": {"sql": Q_S1}},
    {"phase": 3, "goal": "report", "relevant_tools": ["TDA_FinalReport"], "arguments": {"a": "result_of_phase_2"}}])
p2 = exe.meta_plan
check("a phase reading an earlier phase's rows as a table: fetch its real table, combine over both",
      [q["phase"] for q in p2] == [1, 2, 3, 4] and p2[1]["relevant_tools"] == ["base_readQuery"] and p2[1]["_server"] == "srvA"
      and p2[2]["arguments"] == {"op": "sql", "sql": "SELECT SUM(TotalAmount) AS TotalPurchases FROM p2 AS Sales WHERE CustomerID IN (SELECT CustomerID FROM p1)"}
      and p2[3]["arguments"] == {"a": "result_of_phase_3"}, p2)
exe = run_pass([{"phase": 1, "goal": "g", "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT AVG(TotalAmount) FROM s1"}}])
check("a forward reference (a phase that has not run) is not a split — withdrawn to the tactical step",
      "_sql_draft" in exe.meta_plan[0] and exe.meta_plan[0]["relevant_tools"] == ["base_readQuery"], exe.meta_plan[0])
exe = run_pass([
    {"phase": 1, "goal": "count tickets", "relevant_tools": ["crm__base_readQuery"], "arguments": {"sql": "SELECT COUNT(*) AS n FROM ms_crm.ServiceTickets"}},
    {"phase": 2, "goal": "join", "relevant_tools": ["base_readQuery"], "arguments": {"sql": Q_JOIN}},
    {"phase": 3, "goal": "report", "relevant_tools": ["TDA_FinalReport"], "arguments": {"a": "result_of_phase_2", "b": "result_of_phase_1"}}])
check("earlier phases keep their numbers, later references follow the shift",
      [p["phase"] for p in exe.meta_plan] == [1, 2, 3, 4, 5] and (exe.meta_plan[4]["arguments"] if len(exe.meta_plan) > 4 else None)
      == {"a": "result_of_phase_4", "b": "result_of_phase_1"}, [(p.get("phase"), p.get("relevant_tools"), p.get("arguments")) for p in exe.meta_plan])

print("\n[3b.4] the scheduler orders the split")
from trusted_data_agent.agent.executor import PlanExecutor as _PX  # noqa: E402
groups = _PX._build_phase_groups(plan)
_num = lambda x: x.get("phase") if isinstance(x, dict) else x  # noqa: E731
shape = [sorted(_num(x) for x in (g if isinstance(g, list) else [g])) for g in groups]
check("the two fetches run in one parallel group, the combine after them, the report last", shape == [[1, 2], [3], [4]], shape)

plan_sum = plan[:3] + [{"phase": 4, "goal": "total", "relevant_tools": ["TDA_Compute"], "arguments": {"op": "sum", "column": "TotalRevenue"}},
                       {"phase": 5, "goal": "report", "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
shape = [sorted(_num(x) for x in (g if isinstance(g, list) else [g])) for g in _PX._build_phase_groups(plan_sum)]
check("a compute phase runs AFTER an earlier combine (it reads the combine's result as the latest statement)",
      shape == [[1, 2], [3], [4], [5]], shape)
check("…declared as a dependency, not an accident", 3 in _PX._phase_compute_deps(plan_sum[3], plan_sum))

print("\n[3b.4b] an invented server prefix is re-keyed at the tactical seam (0 LLM)")
KNOWN = ["base_readQuery", "crm__base_readQuery", "base_tableList", "crm__base_tableList"]
with armed():
    check("`sales__base_readQuery` on an agent whose first server is 'sales' → the plain tool",
          pe._resolve_unknown_tool("sales__base_readQuery", KNOWN) == "base_readQuery")
    check("a real alias with a tool that server lacks is NOT re-keyed", pe._resolve_unknown_tool("crm__base_nothing", KNOWN) is None)
    check("…even when the first server has that tool (a crm call never goes to sales)",
          pe._resolve_unknown_tool("crm__base_readQuery", ["base_readQuery", "crm__base_tableList"]) is None)
    check("a prefix that is neither an alias nor the first server's word, base on two servers → ambiguous, None",
          pe._resolve_unknown_tool("other__base_readQuery", KNOWN) is None)
    check("…but with the base under ONE catalog name it resolves", pe._resolve_unknown_tool("other__base_tableList", ["base_tableList"]) == "base_tableList")
    check("a known name is left alone", pe._resolve_unknown_tool("base_readQuery", KNOWN) is None)
    check("case and word order still resolve", pe._resolve_unknown_tool("Base_ReadQuery", KNOWN) == "base_readQuery")
src_pe2 = open(os.path.join(ROOT, "src/trusted_data_agent/agent/phase_executor.py")).read()
i = src_pe2.find("_rk = _resolve_unknown_tool(_tn, _known)")
check("the tactical seam re-keys before the action is returned (and never a TDA_ tool)",
      i > 0 and i < src_pe2.index("return normalized_action, input_tokens, output_tokens")
      and 'not _tn.startswith("TDA_")' in src_pe2[max(0, i - 600):i])

print("\n[3b.5] the phase executor at the refusal")
REFUSAL = {"status": "error", "error": "This statement reads databases on different MCP servers (…)",
           "data": "This statement reads databases on different MCP servers (…)", "code": cs.SPANS_CODE,
           "split": {"ms_crm": "srvB", "ms_sales": "srvA"}}


class FakeMCP:
    """Scripted adapter: a spanning statement is refused; a one-server fetch answers rows AND
    registers them (as the real adapter does); TDA_Compute answers the combine."""
    def __init__(self, combine=None, fail_fetch=False):
        self.calls, self.fail_fetch = [], fail_fetch
        self.combine = combine or {"status": "success", "results": [{"State": "CA", "TotalRevenue": 10.0}],
                                   "metadata": {"tool_name": "TDA_Compute"}}

    async def __call__(self, STATE, command, **kw):
        self.calls.append(json.loads(json.dumps(command)))
        name, args = command.get("tool_name"), command.get("arguments") or {}
        if name == "TDA_Compute":
            return self.combine, 0, 0
        sql = str(args.get("sql") or "")
        if "ms_crm" in sql and "ms_sales" in sql:
            return dict(REFUSAL), 0, 0
        if self.fail_fetch and "ms_crm" in sql:
            return {"status": "error", "error": "boom", "data": "boom"}, 0, 0
        res = {"status": "success", "results": [{"CustomerID": "1", "State": "CA"}] if "ms_crm" in sql
               else [{"CustomerID": "1", "TotalAmount": 10.0}]}
        rs.record(kw.get("session_id"), name, args, res, source="mcp:" + ("srvB" if "ms_crm" in sql else "srvA"))
        return res, 0, 0


_real_invoke = _adp.invoke_mcp_tool


def with_fake(mcp, fn):
    _adp.invoke_mcp_tool = mcp
    try:
        return with_combine(fn)
    finally:
        _adp.invoke_mcp_tool = _real_invoke


def drive(exe, sql, mcp):
    action = {"tool_name": "base_readQuery", "arguments": {"sql": sql}}
    phase = {"phase": 1, "goal": "g", "relevant_tools": ["base_readQuery"]}

    async def _go():
        out = []
        async for ev in pe.PhaseExecutor(exe)._execute_tool(action, phase, is_fast_path=True):
            out.append(ev)
        return out
    with armed():
        return with_fake(mcp, lambda: asyncio.run(_go()))


rs._SESSIONS.pop("cs-rt1", None)
exe = FakeExecutor("cs-rt1")
mcp = FakeMCP()
with armed():
    got = with_fake(mcp, lambda: asyncio.run(pe.PhaseExecutor(exe)._cross_source_runtime_split(
        {"tool_name": "base_readQuery", "arguments": {"sql": Q_JOIN}}, REFUSAL)))
ccmd, cres, recs, split = got
check("the runtime split fetches per server through EACH server's own tool",
      [c["tool_name"] for c in mcp.calls[:2]] == ["crm__base_readQuery", "base_readQuery"], [c["tool_name"] for c in mcp.calls])
check("…then combines them by their statement numbers (s<N>)",
      ccmd and ccmd["tool_name"] == "TDA_Compute" and "FROM s1 AS C JOIN s2 AS S" in ccmd["arguments"]["sql"], ccmd)
check("…and returns the combine's rows as the result", cres["results"][0]["TotalRevenue"] == 10.0 and len(recs) == 2)
rs._SESSIONS.pop("cs-rt2", None)
exe = FakeExecutor("cs-rt2")
mcp = FakeMCP(fail_fetch=True)
with armed():
    got = with_fake(mcp, lambda: asyncio.run(pe.PhaseExecutor(exe)._cross_source_runtime_split(
        {"tool_name": "base_readQuery", "arguments": {"sql": Q_JOIN}}, REFUSAL)))
check("a failed fetch: no combine, the fetches recorded, nothing invented", got[0] is None and len(got[2]) == 1)
with armed():
    got = with_fake(FakeMCP(), lambda: asyncio.run(pe.PhaseExecutor(FakeExecutor("cs-rt3"))._cross_source_runtime_split(
        {"tool_name": "base_readQuery", "arguments": {"sql": q_decl}}, REFUSAL)))
check("a declined shape: None, nothing ran", got is None)

# the real retry loop, end to end
rs._SESSIONS.pop("cs-loop1", None)
exe = FakeExecutor("cs-loop1")
mcp = FakeMCP()
drive(exe, Q_JOIN, mcp)
check("loop: the phase's answer is the COMBINE's result", exe.last_tool_output and exe.last_tool_output.get("results") == [{"State": "CA", "TotalRevenue": 10.0}], exe.last_tool_output)
check("loop: 0 LLM rounds", exe.llm_prompts == [])
check("loop: the fetches are in the trace (a fast-path drive logs the phase's own action itself)",
      [h["action"]["tool_name"] for h in exe.turn_action_history][-2:] == ["crm__base_readQuery", "base_readQuery"], [h["action"]["tool_name"] for h in exe.turn_action_history])
check("loop: the split is named as a self-correction", any((e.get("details") or {}).get("correction_type") == "cross_source_split" for e in exe.events))
rs._SESSIONS.pop("cs-loop2", None)
exe = FakeExecutor("cs-loop2", llm_responses=['{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT S.TotalAmount FROM ms_sales.Sales S"}}'])
mcp = FakeMCP()
mcp_calls_before = 0
drive(exe, q_decl.replace("ms_sales.Sales S WHERE", "ms_sales.Sales S WHERE 1=1 AND"), mcp)
check("loop, declined shape: the phase ENDS with the refusal — never a one-server substitute",
      exe.last_tool_output and exe.last_tool_output.get("status") == "error" and exe.last_tool_output.get("code") == cs.SPANS_CODE, exe.last_tool_output)
check("…and no LLM correction round is spent on it", exe.llm_prompts == [] and all(c["tool_name"] != "TDA_Compute" for c in mcp.calls))
check("…the record says why", any((e.get("details") or {}).get("correction_type") == "cross_source_unsplit" for e in exe.events))

print("\n[3b.5b] an empty phase result is still a result; a correction over phase results is the combine")
rs._SESSIONS.pop("cs-empty", None)
with_combine(lambda: rs.record("cs-empty", "crm__base_readQuery", {"sql": "SELECT CustomerID FROM ms_crm.Customers WHERE City = 'JP'"},
                               {"status": "success", "results": [], "metadata": {"columns": [{"name": "CustomerID", "type": "int"}], "row_count": 0}},
                               source="mcp:srvB"))
with_combine(lambda: rs.record("cs-empty", "base_readQuery", {"sql": "SELECT Sales.TotalAmount, Sales.CustomerID FROM ms_sales.Sales AS Sales"},
                               {"status": "success", "results": [{"TotalAmount": 10.0, "CustomerID": "1"}]}, source="mcp:srvA"))
sts = {st["n"]: st for st in rs.statements("cs-empty")}
check("a zero-row result that declares its columns is registered (row_count 0, its columns)",
      1 in sts and sts[1]["row_count"] == 0 and sts[1]["columns"] == ["CustomerID"], sts.get(1))
from trusted_data_agent.agent import compute_sql as csql  # noqa: E402
out = csql.run("SELECT SUM(TotalAmount) AS t FROM s2 AS Sales WHERE CustomerID IN (SELECT CustomerID FROM s1)", sts)
check("…and a combine over it is an honest empty (no invented total)", out.get("status") != "error"
      and (out.get("results") or [{}])[0].get("t") in (None, 0, 0.0), out)
check("a row-less result with NO declared columns is still not a statement",
      with_combine(lambda: rs.record("cs-empty", "base_readQuery", {"sql": "x"}, {"status": "success", "results": []})) is None)
check("a row-less ERROR is never a statement",
      with_combine(lambda: rs.record("cs-empty", "base_readQuery", {"sql": "x"}, {"status": "error", "metadata": {"columns": [{"name": "a"}]}})) is None)
check("only_phase_results: SQL over p/s tables only", cs.only_phase_results("SELECT SUM(TotalAmount) FROM p2 WHERE CustomerID IN (SELECT CustomerID FROM p1)")
      and cs.only_phase_results("SELECT * FROM s3") and not cs.only_phase_results(Q_S1) and not cs.only_phase_results(Q_JOIN)
      and not cs.only_phase_results("nonsense (("))
rs._SESSIONS.pop("cs-corr", None)
exe = FakeExecutor("cs-corr", llm_responses=['{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) FROM p2 WHERE CustomerID IN (SELECT CustomerID FROM p1)"}}'])
with armed():
    corrected, evs = with_combine(lambda: asyncio.run(pe.PhaseExecutor(exe)._attempt_tool_self_correction(
        {"tool_name": "TDA_Compute", "arguments": {"op": "sql", "sql": "SELECT 1 FROM p1"}},
        {"status": "error", "error": "phase 1 has no result this turn", "data": "phase 1 has no result this turn"}, phase_num=3)))
check("a correction that writes SQL over phase results is re-keyed to the combine step",
      corrected == {"tool_name": "TDA_Compute", "arguments": {"op": "sql", "sql": "SELECT SUM(TotalAmount) FROM p2 WHERE CustomerID IN (SELECT CustomerID FROM p1)"}}, corrected)
check("…named", any("combine step" in json.dumps(e) for e, _ in evs))
exe = FakeExecutor("cs-corr2", llm_responses=['{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) FROM ms_sales.Sales"}}'])
with armed():
    corrected, evs = with_combine(lambda: asyncio.run(pe.PhaseExecutor(exe)._attempt_tool_self_correction(
        {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}, {"status": "error", "error": "e", "data": "e"}, phase_num=3)))
check("a correction over a real table is left as the query", corrected and corrected["tool_name"] == "base_readQuery")
exe = FakeExecutor("cs-corr3", llm_responses=['{"tool_name": "sales__base_readQuery", "arguments": {"sql": "SELECT SUM(TotalAmount) FROM ms_sales.Sales"}}'])
with armed():
    corrected, evs = with_combine(lambda: asyncio.run(pe.PhaseExecutor(exe)._attempt_tool_self_correction(
        {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}, {"status": "error", "error": "e", "data": "e"}, phase_num=3)))
check("a correction naming an invented server prefix is re-keyed too", corrected and corrected["tool_name"] == "base_readQuery", corrected)

print("\n[3b.6] wiring")
src_pl = open(os.path.join(ROOT, "src/trusted_data_agent/agent/planner.py")).read()
order = [src_pl.index(f'_pass("{n}"') for n in ("compute_insertion", "cross_source_shape", "plan_validation")]
check("the pass runs after compute_insertion and before plan_validation", order == sorted(order))
src_pe = open(os.path.join(ROOT, "src/trusted_data_agent/agent/phase_executor.py")).read()
i = src_pe.index("_span_unsplit = False")
check("the executor consults the split at the refusal, before any correction", "_cross_source_runtime_split(" in src_pe[i:i + 800]
      and src_pe.index("if _span_unsplit:") < src_pe.index("if attempt < max_retries - 1:\n                    correction_details"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
