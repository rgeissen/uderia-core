"""
Fusion Optimizer hardening (Aug 2026) — the @OPGGT-driven batch.

Every assertion here pins a defect found by driving the Entry-Frontier Optimize
profile (@OPGGT, gemini-3.1-flash-lite, ONE MCP tool, KG-enabled) through real
turns and reading the persisted trace as ground truth:

  S1  CorrectionHandler returned after the FIRST can_handle() match; the
      deterministic TeradataLimitStrategy matched ANY "Syntax error", declined
      (no trailing LIMIT) and thereby ABORTED all correction — the LLM fallback
      was unreachable (live: session f93bef6d, Error 3707, "technical error").
  S2  The column_not_found regex never matched Teradata's real messages
      (3810 `Column/Parameter 'db.S.Price' does not exist`, 5628
      `Column product_name not found in DB.tbl`) → every column error paid a
      Generic LLM round; and the column prompt told the model to "describe the
      table" with a tool the profile does not have. Now: KG-backed deterministic
      fix (same table, unique close match, 0 LLM) else schema FACTS for the LLM.
  S3  A correction proposal `{"prompt_name": "base_readQuery"}` (a TOOL) was
      executed as a sub-prompt and failed (session 174af2a6).
  S4  A bare `result_of_phase_N` EMBEDDED in a SQL string reached the database
      verbatim (session f93bef6d).
  Q1  KG seed scoring rewarded prefix matches only: `TotalRevenue` scored 0 for
      "revenue", fell below the seed cut, and the planner never saw the metric
      or its SaleDetails join path → invented `Price * StockQuantity`.
  Q2  Metric lines rendered description only, never the formula; no structured
      table_columns/metrics facts for deterministic consumers.
  Q3  A wrong-but-error-free champion (`Price * StockQuantity`) was seeded and
      replayed on every later "revenue" question while the ontology declared
      the metric's formula the whole time.
  C1  The strategic planning call's system prompt duplicated the full capability
      catalog already in the planning prompt (~2.5K tokens / turn).
  C2  Gemini cached_content_token_count was never read (cache hits billed full).

Run:  PYTHONPATH=src python3 test/test_fusion_hardening_aug.py
"""
import asyncio
import inspect
import os
import json
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.agent import phase_executor as pe          # noqa: E402
from trusted_data_agent.agent import rag_retriever as rr           # noqa: E402
from trusted_data_agent.agent import executor as ex                # noqa: E402
from trusted_data_agent.llm import handler as llm                  # noqa: E402
from components.builtin.knowledge_graph import handler as kgh      # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {('— ' + str(detail)) if detail else ''}")


# ---------------------------------------------------------------- doubles
class FakeExecutor:
    def __init__(self, tools=("base_readQuery",), prompts=(), table_columns=None):
        self.kg_enrichment_event = None
        if table_columns is not None:
            self.kg_enrichment_event = {"enrichments": [{"table_columns": table_columns}]}
        self.dependencies = {"STATE": {"mcp_tools": {t: object() for t in tools},
                                       "mcp_prompts": {p: object() for p in prompts}}}
        self.correction_history = []
        self.source = "test"
        self.user_uuid = "u"
        self.session_id = "s"
        self.original_user_input = "q"
        self.llm_calls = 0
        self.llm_response = '{"arguments": {"sql": "SELECT 1"}}'
        self._last_call_metadata = {}
        self.turn_input_tokens = 0
        self.turn_output_tokens = 0

        class _R:
            def get_tactical_self_correction_prompt(self_):
                return "{tool_definition}{failed_command}{error_message}{user_question}{tools_context}{prompts_context}"

            def get_tactical_self_correction_column_error_prompt(self_):
                return "{user_question}{tool_name}{failed_arguments}{invalid_column_name}{tools_context}{prompts_context}"
        self.prompt_resolver = _R()

    def _get_correction_catalog(self):
        return "tools", "prompts"

    async def _call_llm_and_update_tokens(self, *a, **k):
        self.llm_calls += 1
        return self.llm_response, 10, 5


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# monkeypatch session_manager.get_session used by _call_correction_llm
async def _no_session(*a, **k):
    return None
pe.session_manager.get_session = _no_session


def err(data, msg=""):
    return {"status": "error", "error": "Tool returned malformed JSON string", "data": data, "error_message": msg}


# ================================================================ S1
print("\n[S1] a declining deterministic strategy continues the chain")
exe = FakeExecutor()
h = pe.CorrectionHandler(exe)
e3707 = err("Error: [Teradata Database] [Error 3707] Syntax error, expected something like an 'UDFCALLNAME' keyword between '(' and the 'TD_SYSFNLIB' keyword.")
action = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Sales WHERE x IN (SELECT MIN(d) FROM TABLE(TD_SYSFNLIB.DateRange('a','b')) AS T)"}}
lim = pe.TeradataLimitStrategy(exe)
check("TeradataLimitStrategy claims the syntax error (broad gate, by design)…", lim.can_handle(e3707["data"]))
check("…and DECLINES it (None, no events) because the SQL carries no trailing LIMIT",
      run(lim.generate_correction(action, e3707)) == (None, []))
corrected, events = run(h.attempt_correction(action, e3707))
check("chain reaches the Generic LLM strategy on a non-LIMIT syntax error", exe.llm_calls == 1, exe.llm_calls)
check("…and returns its correction", corrected is not None and corrected.get("arguments", {}).get("sql") == "SELECT 1", corrected)
src = inspect.getsource(pe.CorrectionHandler.attempt_correction)
check("attempt_correction continues on (None, []) — the decline contract is in code",
      "corrected_action is None and not events" in src and "continue" in src)
# a genuine LLM failure (None WITH events) still ends the chain
exe2 = FakeExecutor(); exe2.llm_response = "not json at all"
h2 = pe.CorrectionHandler(exe2)
corrected, events = run(h2.attempt_correction(action, e3707))
check("an LLM strategy that tried and failed (None + events) still ends the chain", corrected is None and events and exe2.llm_calls == 1)

# ================================================================ S2
print("\n[S2] Teradata column errors are recognised and resolved against the KG")
rx = pe.RECOVERABLE_TOOL_ERRORS["column_not_found"]
m = re.search(rx, "Column/Parameter 'fitness_db.S.Price' does not exist.", re.IGNORECASE)
check("3810 shape matches, captures the qualified ref", m and m.group(1) == "fitness_db.S.Price", m and m.group(1))
m = re.search(rx, "[Error 5628] Column product_name not found in FITNESS_DB.sales.", re.IGNORECASE)
check("5628 shape matches, captures the bare column", m and m.group(1) == "product_name", m and m.group(1))
m = re.search(rx, "Column 'foo' does not exist", re.IGNORECASE)
check("generic shape still matches", m and m.group(1) == "foo")
check("a table-not-found message does NOT match the column regex",
      re.search(rx, "Object 'fitness_db.tickets' does not exist.", re.IGNORECASE) is None)

schema = {"fitness_db.Sales": ["SaleID", "CustomerID", "SaleDate", "TotalAmount", "SalesPersonID"],
          "fitness_db.SaleDetails": ["SaleDetailID", "SaleID", "ProductID", "Quantity", "UnitPrice"],
          "fitness_db.Products": ["ProductID", "ProductName", "Price", "StockQuantity", "Brand", "ProductType"]}
exe = FakeExecutor(table_columns=schema)
# (a) same-table case/name slip → deterministic rewrite, 0 LLM
fa = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT TOP 5 product_name, SUM(x) FROM fitness_db.Products GROUP BY product_name"}}
fix, facts = pe._resolve_column_against_kg(exe, fa, "product_name")
check("same-table case slip → deterministic fix (0 LLM)", fix and fix["good"] == "ProductName" and fix["table"] == "fitness_db.Products", fix)
check("…rewrites EVERY occurrence in the SQL", fix and fix["sql"].count("ProductName") == 2 and "product_name" not in fix["sql"], fix and fix["sql"])
fa = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT S.SaleDat FROM fitness_db.Sales S"}}
fix, facts = pe._resolve_column_against_kg(exe, fa, "fitness_db.S.SaleDat")
check("alias-qualified near-miss resolves through the alias → the aliased table", fix and fix["good"] == "SaleDate" and "S.SaleDate" in fix["sql"], fix)
# (b) the column lives in ANOTHER table → no mechanical fix, but FACTS for the LLM
fa = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT SUM(S.UnitPrice * S.Quantity) FROM fitness_db.Customers C JOIN fitness_db.Sales S ON C.CustomerID = S.CustomerID"}}
fix, facts = pe._resolve_column_against_kg(exe, fa, "fitness_db.S.UnitPrice")
check("a column that belongs to a different table is NOT mechanically substituted", fix is None)
check("…the LLM gets the real columns of the referenced table", "fitness_db.Sales: SaleID, CustomerID" in facts, facts)
check("…and is told where the column actually lives", "'UnitPrice' exists in: fitness_db.SaleDetails" in facts, facts)
# (c) no KG → baseline (no fix, no facts)
fix, facts = pe._resolve_column_against_kg(FakeExecutor(), fa, "fitness_db.S.UnitPrice")
check("no KG schema → baseline behaviour (nothing injected)", fix is None and facts == "")
# end-to-end through the strategy: deterministic path must not call the LLM
exe = FakeExecutor(table_columns=schema)
col = pe.ColumnNotFoundStrategy(exe)
fa = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT productname FROM fitness_db.Products"}}
corrected, events = run(col.generate_correction(fa, err("[Error 5628] Column productname not found in FITNESS_DB.Products.")))
check("ColumnNotFoundStrategy: deterministic path calls NO LLM", exe.llm_calls == 0 and corrected["arguments"]["sql"] == "SELECT ProductName FROM fitness_db.Products", corrected)
check("…and reports correction_type kg_column_resolution", any((ev[0].get("details") or {}).get("correction_type") == "kg_column_resolution" for ev in events))
exe = FakeExecutor(table_columns=schema)
col = pe.ColumnNotFoundStrategy(exe)
fa = {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT S.UnitPrice FROM fitness_db.Sales S"}}
run(col.generate_correction(fa, err("Column/Parameter 'fitness_db.S.UnitPrice' does not exist.")))
check("ColumnNotFoundStrategy: other-table case goes to the LLM WITH schema facts", exe.llm_calls == 1)
check("chain order: Column strategy sits before Generic", [s.__class__.__name__ for s in pe.CorrectionHandler(exe).strategies][-2:] == ["DatabaseNotFoundStrategy", "GenericCorrectionStrategy"])

# ================================================================ S3
print("\n[S3] correction output is validated against the catalog")
exe = FakeExecutor(tools=("base_readQuery",), prompts=("dba_databaseHealthAssessment",))
gen = pe.GenericCorrectionStrategy(exe)
exe.llm_response = '{"prompt_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}}'
corrected, events = run(gen.generate_correction({"tool_name": "base_readQuery", "arguments": {}}, err("boom")))
check("a TOOL labelled as prompt_name is re-keyed to tool_name", corrected and corrected.get("tool_name") == "base_readQuery" and "prompt_name" not in corrected, corrected)
exe.llm_response = '{"prompt_name": "TDA_KnowledgeGraph", "arguments": {"action": "query"}}'
corrected, events = run(gen.generate_correction({"tool_name": "base_readQuery", "arguments": {}}, err("boom")))
check("a TDA_ component tool labelled as prompt is re-keyed too", corrected and corrected.get("tool_name") == "TDA_KnowledgeGraph")
exe.llm_response = '{"prompt_name": "no_such_prompt", "arguments": {}}'
corrected, events = run(gen.generate_correction({"tool_name": "base_readQuery", "arguments": {}}, err("boom")))
check("a prompt that exists nowhere is rejected (None) instead of run", corrected is None and any("not in the catalog" in str(ev[0].get("details")) for ev in events))
exe.llm_response = '{"prompt_name": "dba_databaseHealthAssessment", "arguments": {}}'
corrected, events = run(gen.generate_correction({"tool_name": "base_readQuery", "arguments": {}}, err("boom")))
check("a real prompt passes through unchanged", corrected and corrected.get("prompt_name") == "dba_databaseHealthAssessment")

# …and a proposed TOOL (fusion bench 2026-09-24: `base_listTables` reached the MCP server as an
# unknown tool and ended the phase, so "how many sales in the past 30 days?" was refused).
pe._is_connector_tool = lambda n: n.startswith("google__")    # no registry in CI
exe = FakeExecutor(tools=("base_readQuery", "base_tableList", "base_columnDescription"))
gen = pe.GenericCorrectionStrategy(exe)
def _prop(tool):
    exe.llm_response = json.dumps({"tool_name": tool, "arguments": {"database_name": "fitness_db"}})
    return run(gen.generate_correction({"tool_name": "base_readQuery", "arguments": {}}, err("boom")))
corrected, events = _prop("base_listTables")
check("a tool named with the catalog tool's words in another order is re-keyed to it (0 LLM)",
      corrected and corrected.get("tool_name") == "base_tableList"
      and any("base_tableList" in str(ev[0].get("details")) for ev in events), corrected)
corrected, events = _prop("base_readquery")
check("…and a case slip resolves to the catalog's spelling", corrected and corrected.get("tool_name") == "base_readQuery", corrected)
corrected, events = _prop("base_listColumns")
check("a tool that exists nowhere is refused before it reaches a server, naming the closest tools",
      corrected is None and any("not in the catalog" in str(ev[0].get("details")) and "base_" in str(ev[0].get("details"))
                                for ev in events), events)
corrected, events = _prop("base_tableList")
check("a real tool passes through unchanged", corrected and corrected.get("tool_name") == "base_tableList")
corrected, events = _prop("TDA_KnowledgeGraph")
check("a TDA_ framework/component tool is never refused here", corrected and corrected.get("tool_name") == "TDA_KnowledgeGraph")
corrected, events = _prop("google__read_emails")
check("a platform connector tool (outside the MCP catalog) is never refused", corrected and corrected.get("tool_name") == "google__read_emails")
check("an ambiguous word match is not guessed",
      pe._catalog_tool_for("base_listTables", {"base_tableList", "base_listTables_v2", "base_tablesList"}) is None)

# ================================================================ S4
print("\n[S4] an embedded bare phase reference resolves to the phase's scalar")
class _Ex:
    _resolve_arguments = ex.PlanExecutor._resolve_arguments
    _unwrap_single_value_from_result = ex.PlanExecutor._unwrap_single_value_from_result
    _find_value_by_key = ex.PlanExecutor._find_value_by_key
    _substitute_sql_date_binds = ex.PlanExecutor._substitute_sql_date_binds
    _turn_date_context = ex.PlanExecutor._turn_date_context
    _rewrite_phantom_date_source = ex.PlanExecutor._rewrite_phantom_date_source
    _DATE_BIND_START = ex.PlanExecutor._DATE_BIND_START
    _DATE_BIND_END = ex.PlanExecutor._DATE_BIND_END
    _DATE_BIND_NOW = ex.PlanExecutor._DATE_BIND_NOW
    def __init__(self, ws):
        self.workflow_state = ws; self.events_to_yield = []; self.meta_plan = []
    def _format_sse_with_depth(self, *a, **k):
        return a
e = _Ex({"result_of_phase_1": {"status": "success", "results": [{"current_date": "2026-08-24"}]},
         "result_of_phase_2": {"status": "success", "results": [{"a": 1}, {"a": 2}]}})
out = e._resolve_arguments({"sql": "SELECT * FROM t WHERE d <= 'result_of_phase_1' AND d >= 'result_of_phase_1'"})
check("scalar phase result substituted at EVERY embedded occurrence", out["sql"] == "SELECT * FROM t WHERE d <= '2026-08-24' AND d >= '2026-08-24'", out["sql"])
out = e._resolve_arguments({"sql": "SELECT * FROM t WHERE x IN (result_of_phase_2)"})
check("a multi-row result is NOT flattened into the string", "result_of_phase_2" in out["sql"])
out = e._resolve_arguments({"sql": "result_of_phase_1"})
check("a whole-value reference keeps its existing (structured) resolution", out["sql"] == {"status": "success", "results": [{"current_date": "2026-08-24"}]} or out["sql"] == "2026-08-24", out["sql"])

# ================================================================ Q1
print("\n[Q1] KG seed scoring: a whole word of the identifier scores; matched metrics are reserved")
check("_ident_tokens splits camelCase/snake_case", kgh._ident_tokens("TotalRevenue") == ["total", "revenue"] and kgh._ident_tokens("unit_price") == ["unit", "price"])
metric = {"entity_type": "metric", "name": "TotalRevenue", "properties": {"description": "The sum of all SaleDetails UnitPrice multiplied by Quantity", "formula": "SUM(SaleDetails.UnitPrice * SaleDetails.Quantity)"}}
prod_col = {"entity_type": "attribute", "name": "fitness_db.Products.ProductName", "properties": {}}
terms = kgh._scoring_terms("what are the top 5 products based on revenue generated?")
check("'revenue' now scores TotalRevenue (was 0)", kgh._match_score(metric, terms) > 0, kgh._match_score(metric, terms))
check("a whole-word identifier match scores the +70 tier", kgh._match_score(metric, terms) >= 70, kgh._match_score(metric, terms))
# the reserved-seed rescue: 20 prefix-matched columns beat the metric on tie-break order → still seeded
cols = [{"entity_type": "attribute", "name": f"fitness_db.Products.Product{i}", "properties": {}} for i in range(20)]
ranked = kgh._rank_seeds(cols + [metric], "list products by revenue", limit=15)
check("a matched metric survives the 15-seed cut via its reserved slot", any(e.get("name") == "TotalRevenue" for e in ranked))
unmatched = {"entity_type": "metric", "name": "StockLevel", "properties": {"description": "inventory"}}
ranked = kgh._rank_seeds(cols + [unmatched], "list products by revenue", limit=15)
check("an UNMATCHED metric is never force-seeded", not any(e.get("name") == "StockLevel" for e in ranked))
check("metric is a reserved seed type with a bounded slot", "metric" in kgh._RESERVED_SEED_TYPES and kgh._RESERVED_SLOTS_BY_TYPE.get("metric") == 2)

# ================================================================ Q2
print("\n[Q2] metric formula rendered; structured schema facts stamped on the event")
sub = {"entities": [
    {"id": 1, "entity_type": "entity", "name": "SaleDetails", "properties": {}},
    {"id": 2, "entity_type": "attribute", "name": "SaleDetails.UnitPrice", "properties": {}},
    {"id": 3, "entity_type": "attribute", "name": "SaleDetails.Quantity", "properties": {}},
    {"id": 4, "entity_type": "metric", "name": "TotalRevenue", "properties": metric["properties"]},
], "relationships": [
    {"relationship_type": "contains", "source_id": 1, "target_id": 2, "source_name": "SaleDetails", "target_name": "SaleDetails.UnitPrice"},
    {"relationship_type": "contains", "source_id": 1, "target_id": 3, "source_name": "SaleDetails", "target_name": "SaleDetails.Quantity"},
    {"relationship_type": "measures", "source_id": 4, "target_id": 2, "source_name": "TotalRevenue", "target_name": "SaleDetails.UnitPrice"},
    {"relationship_type": "relates_to", "source_id": 4, "target_id": 1, "source_name": "TotalRevenue", "target_name": "SaleDetails"},
]}
tc = kgh._table_columns_map(sub, {})
check("table_columns map built from contains edges", tc == {"SaleDetails": ["UnitPrice", "Quantity"]}, tc)
mf = kgh._metric_facts(sub)
check("metric facts carry formula + source tables", mf and mf[0]["formula"].startswith("SUM(") and mf[0]["source_tables"] == ["SaleDetails"], mf)
class _H(kgh.KnowledgeGraphHandler if hasattr(kgh, "KnowledgeGraphHandler") else object):
    pass
try:
    handler_cls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_format_subgraph_for_prompt")][0]
    txt = handler_cls._format_subgraph_for_prompt(handler_cls.__new__(handler_cls), sub, {}, surface="off")
    check("rendered metric line carries the formula", "formula: SUM(SaleDetails.UnitPrice * SaleDetails.Quantity)" in txt, txt[-400:])
    check("metric group header states the formula is authoritative", "authoritative definitions" in txt)
    # A COMPILABLE metric's line carries its declared measure/dimension NAMES — the
    # compile_metric contract. In prod (d9a60903) the line showed formula/deployed/base
    # table but never the names, so the model asked for a COLUMN ('fi_product_revenue')
    # on a metric whose measures are display names and hit a dead-end refusal.
    from components.builtin.knowledge_graph.resolver import EntityCapabilities as _EC
    _cap = _EC(compilable=True, metric_base_table="db.sales",
               metric_measures=["Total Fi Product Revenue", "Row Count"], metric_dimensions=["Dealer Name"])
    txt_c = handler_cls._format_subgraph_for_prompt(handler_cls.__new__(handler_cls), sub, {"TotalRevenue": _cap}, surface="off")
    check("compilable metric line names its measures, quoted (declared order when the question matched none)",
          "measures: 'Total Fi Product Revenue', 'Row Count'" in txt_c, txt_c[-600:])
    check("compilable metric line names its dimensions", "dimensions: 'Dealer Name'" in txt_c)
    check("metric header says to use the NAMES on the line and that a column name is not a measure",
          "NAMES listed on its line" in txt_c and "column name is not a measure name" in txt_c)
    # Bounded and the remainder COUNTED — asserted against the constant that decides it,
    # never against its current value, so tuning the bound cannot break a property that holds.
    _lim = kgh._METRIC_NAMES_SHOWN
    _wide = _EC(compilable=True, metric_measures=[f"m{i}" for i in range(_lim + 8)])
    txt_w = handler_cls._format_subgraph_for_prompt(handler_cls.__new__(handler_cls), sub, {"TotalRevenue": _wide}, surface="off")
    check("a wide metric is bounded and the remainder COUNTED, never dropped silently",
          f"'m{_lim - 1}'" in txt_w and f"'m{_lim}'" not in txt_w and "(+8 more)" in txt_w, txt_w[-400:])
    check("a non-compilable metric renders no names line (no bait)", "measures: " not in txt)
except Exception as exc:  # noqa: BLE001
    check("render smoke", False, exc)

# ================================================================ Q3
print("\n[Q3] a champion contradicting an ontology metric named by the question is not offered")
kg_metrics = [{"name": "TotalRevenue", "formula": "SUM(SaleDetails.UnitPrice * SaleDetails.Quantity)", "source_tables": ["SaleDetails"]}]
bad = {"successful_strategy": {"phases": [{"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT TOP 5 ProductName, SUM(Price * StockQuantity) AS TotalRevenue FROM fitness_db.Products GROUP BY ProductName"}}]}}
good = {"successful_strategy": {"phases": [{"phase": 1, "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT P.ProductName, SUM(D.UnitPrice*D.Quantity) FROM fitness_db.SaleDetails D JOIN fitness_db.Products P ON P.ProductID=D.ProductID GROUP BY 1"}}]}}
q = "what are the top 5 products based on revenue generated?"
check("the Price*StockQuantity champion contradicts TotalRevenue", rr.champion_contradicts_metric(bad, q, kg_metrics) == "TotalRevenue")
check("a champion over the metric's source table is offered", rr.champion_contradicts_metric(good, q, kg_metrics) is None)
check("a question that does not name the metric never filters", rr.champion_contradicts_metric(bad, "which products are in stock?", kg_metrics) is None)
check("no metric facts → fail open", rr.champion_contradicts_metric(bad, q, []) is None and rr.champion_contradicts_metric(bad, q, None) is None)
ref_only = {"successful_strategy": {"phases": [{"phase": 1, "arguments": {"sql": "result_of_phase_1"}}]}}
check("a runtime-reference SQL (governed seam) is never judged", rr.champion_contradicts_metric(ref_only, q, kg_metrics) is None)
check("retrieve_examples accepts kg_metrics and applies the filter at the champion seam",
      "kg_metrics" in inspect.signature(rr.RAGRetriever.retrieve_examples).parameters
      and "champion_contradicts_metric(full_case_data, query, kg_metrics)" in inspect.getsource(rr.RAGRetriever.retrieve_examples))

# ================================================================ C1 / C2
print("\n[C1/C2] planning call strips the duplicate catalog; cached tokens are read and discounted")
check("call_llm_api exposes strip_capabilities", "strip_capabilities" in inspect.signature(llm.call_llm_api).parameters)
check("_get_full_system_prompt honours strip_capabilities", "strip_capabilities" in inspect.signature(llm._get_full_system_prompt).parameters
      and "listed in the task prompt below" in inspect.getsource(llm._get_full_system_prompt))
from trusted_data_agent.agent import planner as pl  # noqa: E402
psrc = inspect.getsource(pl.Planner._generate_meta_plan)
check("the strategic planning call passes strip_capabilities=True", "strip_capabilities=True" in psrc)
check("the strategic step details carry cached_tokens", '"cached_tokens"' in psrc)
check("planner no longer logs the 2000-char tools_context at INFO / .error", "[DEBUG] tools_context preview" not in psrc and "app_logger.error(\"[DEBUG]" not in psrc)
check("mcp_system_name is read through the cached accessor (planner + handler)",
      "get_mcp_system_name()" in psrc and "get_mcp_system_name()" in inspect.getsource(llm._get_full_system_prompt))
check("get_mcp_system_name returns a string and caches", isinstance(llm.get_mcp_system_name(), str) and llm._MCP_SYSTEM_NAME_CACHE["value"] is not None)
hsrc = inspect.getsource(llm.call_llm_api)
check("Google usage reads cached_content_token_count into the task-local side channel", "cached_content_token_count" in hsrc and "LAST_CALL_USAGE.set" in hsrc)
esrc = inspect.getsource(ex.PlanExecutor._call_llm_and_update_tokens)
# S2 (Sep 2026) moved the discount out of the executor into the ONE cache-aware pricing
# rule (`CostManager.calculate_cost_with_cache` → `cache_policy.priced_cost`, which still
# takes CACHED_INPUT_PRICE_FACTOR as the unlisted-provider default). The property this
# pinned — cached tokens are discounted and stamped — holds; the site moved.
import inspect as _insp
from trusted_data_agent.core import cost_manager as _cmm
check("executor discounts cached tokens through the one cache-aware rule and stamps cached_tokens",
      "calculate_cost_with_cache(" in esrc and '"cached_tokens": _cached_tokens' in esrc
      and "CACHED_INPUT_PRICE_FACTOR" in _insp.getsource(_cmm.CostManager.calculate_cost_with_cache))
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
check("CACHED_INPUT_PRICE_FACTOR defaults to 0.25", getattr(APP_CONFIG, "CACHED_INPUT_PRICE_FACTOR", None) == 0.25)

# ================================================================ Q1b / Q4 / S5
print("\n[Q1b] the reserved-slot rescue never evicts a better-ranked reserved row")
strong = {"entity_type": "metric", "name": "TotalRevenue", "properties": {"formula": "SUM(SaleDetails.UnitPrice * SaleDetails.Quantity)"}}
weak = {"entity_type": "metric", "name": "StockLevel", "properties": {"formula": "SUM(Products.StockQuantity)"}}
cols = [{"entity_type": "attribute", "name": f"fitness_db.Products.Product{i}", "properties": {}} for i in range(14)]
ranked = kgh._rank_seeds(cols + [strong, weak], "what are the top 5 products based on revenue generated?", limit=15)
names = [e["name"] for e in ranked]
check("TotalRevenue (70) survives; the weaker StockLevel is the one rescued INTO a non-reserved slot, never over it",
      "TotalRevenue" in names and len(ranked) == 15, names)
check("rescue displaces the weakest NON-reserved column, not the metric",
      names.count("TotalRevenue") == 1 and sum(1 for n in names if n.startswith("fitness_db.Products.Product")) == 13, names)

print("\n[Q4] bridge tables are discovered through shared column names")
class _Store:
    def __init__(self): self.calls = 0
    async def list_entities(self, entity_type=None, limit=None):
        if entity_type == "attribute":
            return [{"name": n} for n in ("SaleDetails.SaleID", "SaleDetails.ProductID", "SaleDetails.UnitPrice",
                                          "Sales.SaleID", "Sales.CustomerID", "Sales.SaleDate",
                                          "Customers.CustomerID", "Customers.State",
                                          "ServiceTickets.CustomerID", "ServiceTickets.ProductID")]
        return [{"id": 1, "name": "SaleDetails"}, {"id": 2, "name": "Sales"}, {"id": 3, "name": "Customers"}, {"id": 4, "name": "ServiceTickets"}]
handler_cls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_bridge_table_ids")][0]
hh = handler_cls.__new__(handler_cls)
sub2 = {"entities": [{"id": 1, "entity_type": "entity", "name": "SaleDetails"}, {"id": 3, "entity_type": "entity", "name": "Customers"}]}
ids = run(hh._bridge_table_ids(_Store(), sub2))
check("SaleDetails↔Customers gets Sales (SaleID / CustomerID) — and ServiceTickets, which also bridges via ProductID/CustomerID", set(ids) == {2, 4}, ids)
sub3 = {"entities": [{"id": 2, "entity_type": "entity", "name": "Sales"}, {"id": 3, "entity_type": "entity", "name": "Customers"}]}
check("tables that already share a column need no bridge", run(hh._bridge_table_ids(_Store(), sub3)) == [])
check("a single table needs no bridge", run(hh._bridge_table_ids(_Store(), {"entities": sub3["entities"][:1]})) == [])
check("get_context_enrichment re-extracts with bridge seeds", "_bridge_table_ids(store, subgraph)" in inspect.getsource(handler_cls.get_context_enrichment))

print("\n[S5] SQL date binds are wired from the turn's date context; metric refusal carries the formula")
e = _Ex({"result_of_phase_1": [{"status": "success", "metadata": {"tool_name": "TDA_CurrentDate"}, "results": [{"current_date": "2026-08-24"}]}],
         "result_of_phase_2": [{"status": "success", "metadata": {"tool_name": "TDA_DateRange"}, "start_date": "2026-07-25", "end_date": "2026-08-23", "results": [{"date": "2026-07-25"}]}]})
out = e._resolve_arguments({"sql": "SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate >= CAST(:start_date AS DATE) AND SaleDate <= CAST(:end_date AS DATE) AND x < :today"})
check("start/end/today binds substituted with the resolved dates",
      out["sql"] == "SELECT COUNT(*) FROM fitness_db.Sales WHERE SaleDate >= CAST('2026-07-25' AS DATE) AND SaleDate <= CAST('2026-08-23' AS DATE) AND x < '2026-08-24'", out["sql"])
check("a workaround event is recorded", any((ev[0].get("details") or {}).get("correction_type") == "sql_date_bind_wiring" for ev in e.events_to_yield))
out = e._resolve_arguments({"sql": "SELECT * FROM t WHERE ts > '2026-01-01 10:00:00' AND k = :unknown_bind"})
check("a time literal (hh:mm) is untouched and an unknown bind is left for the tool to report",
      out["sql"] == "SELECT * FROM t WHERE ts > '2026-01-01 10:00:00' AND k = :unknown_bind", out["sql"])
e0 = _Ex({})
check("no date context in the turn → SQL untouched", e0._resolve_arguments({"sql": "SELECT 1 WHERE d > :start_date"})["sql"] == "SELECT 1 WHERE d > :start_date")
from components.builtin.knowledge_graph import metric_api as mapi  # noqa: E402
check("a formula-only metric refusal names the formula and the way forward", "Its declared formula is" in inspect.getsource(mapi.resolve_metric_semantics))
schema_j = {"fitness_db.Sales": ["SaleID", "CustomerID"], "fitness_db.SaleDetails": ["SaleID", "UnitPrice"], "fitness_db.Customers": ["CustomerID"]}
fix, facts = pe._resolve_column_against_kg(FakeExecutor(table_columns=schema_j),
                                           {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT Revenue FROM fitness_db.Sales"}}, "Revenue")
check("schema facts list the JOINABLE columns (the only valid join keys)", "JOINABLE COLUMNS" in facts and "saleid: fitness_db.SaleDetails, fitness_db.Sales" in facts, facts)
rendered = handler_cls._format_subgraph_for_prompt(handler_cls.__new__(handler_cls), sub, {}, surface="off")
check("metric header says NOT compilable when no metric is compilable (no compile_metric bait)", "NOT" in rendered and "do not call compile_metric" in rendered)

sub_j = {"entities": [
    {"id": 1, "entity_type": "entity", "name": "Sales", "properties": {}},
    {"id": 2, "entity_type": "entity", "name": "SaleDetails", "properties": {}},
    {"id": 3, "entity_type": "attribute", "name": "Sales.SaleID", "properties": {}},
    {"id": 4, "entity_type": "attribute", "name": "SaleDetails.SaleID", "properties": {}},
], "relationships": [
    {"relationship_type": "contains", "source_id": 1, "target_id": 3, "source_name": "Sales", "target_name": "Sales.SaleID"},
    {"relationship_type": "contains", "source_id": 2, "target_id": 4, "source_name": "SaleDetails", "target_name": "SaleDetails.SaleID"},
]}
rj = handler_cls._format_subgraph_for_prompt(handler_cls.__new__(handler_cls), sub_j, {}, surface="off")
check("JOINABLE COLUMNS renders on a layered graph (leaf comparison — the block was silently dead before)",
      "JOINABLE COLUMNS" in rj and "SaleID: SaleDetails, Sales" in rj, rj[-300:])

print("\n[S6] temporal-context phases are consumed before a raw-SQL phase; a figure after total failure is flagged")
plan = [{"phase": 1, "relevant_tools": ["TDA_CurrentDate"], "arguments": {}},
        {"phase": 2, "relevant_tools": ["TDA_DateRange"], "arguments": {"start_date": "result_of_phase_1", "date_phrase": "past 30 days"}},
        {"phase": 3, "relevant_tools": ["base_readQuery"], "arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Sales WHERE d >= :start_date"}},
        {"phase": 4, "relevant_tools": ["TDA_FinalReport"], "arguments": {}}]
deps = ex.PlanExecutor._phase_discovery_deps(plan[2], plan)
check("the SQL phase depends on BOTH temporal-context phases (no more [2,3] race)", deps == {1, 2}, deps)
groups = ex.PlanExecutor._build_phase_groups(plan)
check("phase groups serialise date-range before the query", [sorted(p.get("phase") for p in g) for g in groups] == [[1], [2], [3], [4]], groups)
from trusted_data_agent.agent.honesty import gate as hg  # noqa: E402
calls_failed = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM fitness_db.Sales WHERE d >= :start_date"},
                 "result": {"status": "error", "data": "A value is required for bind parameter 'start_date'"}}]
v = hg.assess("There were 30 days of sales activity recorded between July 25, 2026, and August 23, 2026.", calls_failed)
check("a quantitative claim after the only data query failed → fabricated_after_failure (danger)", v and v.kind == "fabricated_after_failure" and v.band == "danger", v)
v = hg.assess("I am unable to provide the number of sales for the past 30 days due to an error during data retrieval.", calls_failed)
check("an honest failure disclosure (with a number in it) is NOT flagged", v is None, v)
calls_ok = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) c FROM fitness_db.Sales"}, "result": {"status": "success", "results": [{"c": 30}]}}]
check("a figure backed by a successful query is untouched", hg.assess("There were 30 sales in the period.", calls_ok) is None)
check("no data queries at all (Ideate/Focus) → abstain", hg.assess("There were 30 sales in the period.", []) is None)

print(f"\n=== Fusion hardening (Aug 2026) results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
