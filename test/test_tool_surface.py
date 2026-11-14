"""S6 — The Governed Tool Surface gate.

Executes the real seams: the pure core (catalog builders · the identifier scorer · backbone ·
select's offered/held/denied split · find over the held set · asked_denied), the SECOND ranker
(meaning: `semantic_pick`'s one rule, select/find widened by an injected embedder, the stamp's
`ranker`), the finder guard (no TDA_FindTools on the agent ⇒ nothing held), the runtime (arm on
governed mode only — there is NO deployment switch · stamp · broadcast), the Optimize overlay
(structured_tools filtered + tools_context rebuilt, mcp_tools whole, a find rebuilds; full
descriptions on every governed turn), the ReAct dynamic model (binds offered ∪ found; the
ToolNode holds every tool), the TDA_FindTools component (inert unless governed; a find widens; a
denied tool is never found), the persistence stamp, the assurance mechanism at BOUNDED, the
contract (portable, UNSIGNED), and the frontend contract. Mode `all` ⇒ every seam is a no-op.
"""
import asyncio, json, logging, os, pathlib, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.setdefault("HF_HUB_OFFLINE", "1"); os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
from trusted_data_agent.agent import tool_semantics as SEM
SEM._embedder_state = "unavailable"   # the pure sections run on words alone; [13]–[15] inject an embedder

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:320]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
_LOOP = asyncio.new_event_loop()
def run(c): return _LOOP.run_until_complete(c)

from trusted_data_agent.agent import tool_surface as TS
from trusted_data_agent.core.agent_authority import AgentScope

ST = {"Base": [{"name": "base_readQuery", "description": "run a SQL query"}, {"name": "base_tableList", "description": "list tables of a database"},
               {"name": "base_writeQuery", "description": "run a write SQL statement"}, {"name": "base_tableDDL", "description": "table ddl", "disabled": True}],
      "DBA": [{"name": "dba_tableSpace", "description": "space used per table"}, {"name": "dba_sessionInfo", "description": "active sessions"}],
      "Data Quality": [{"name": "qlty_columnSummary", "description": "column statistics"}, {"name": "qlty_missingValues", "description": "rows with missing values"}],
      "Security": [{"name": "sec_userGrants", "description": "grants held by a user"}, {"name": "sec_roleMembers", "description": "members of a role"}],
      "Component Tools": [{"name": "TDA_Charting", "description": "chart"}, {"name": "TDA_FindTools", "description": "find"}]}
CAT = TS.catalog_from_structured(ST, {"qlty_columnSummary": "column"})
NAMES = [t.name for t in CAT]

print("[1] the catalog builders")
check("structured: every enabled tool, the disabled one excluded, categories + scopes carried",
      "base_tableDDL" not in NAMES and len(CAT) == 11 and next(t for t in CAT if t.name == "qlty_columnSummary").scope == "column"
      and next(t for t in CAT if t.name == "dba_tableSpace").category == "DBA", NAMES)
class _LT:
    def __init__(self, n, d=""): self.name, self.description = n, d
LC = TS.catalog_from_langchain([_LT("base_readQuery", "run sql"), _LT("TDA_Charting", "chart")], TS.categories_from_structured(ST))
check("langchain: names/descriptions + the category from the classification snapshot", [t.name for t in LC] == ["base_readQuery", "TDA_Charting"] and LC[0].category == "Base" and LC[1].category == "Component Tools")

print("[2] the scorer is an identifier-token rule (class-level, never a list)")
check("tokens split snake and camel case, fold plurals, drop stop + generic words (table/column/database carry no identity)", TS._tokens("dba_tableSpace") == {"dba", "space"} and "ticket" in TS._tokens("tickets") and TS._tokens("the and for") == set())
terms = TS.question_terms("which users hold grants on the security tables", ["fitness_db.Customers"])
check("question terms ∪ enrichment leaf tokens", {"user", "grant", "security", "customer"} <= terms, terms)
check("a name hit outweighs a description hit", TS.score(TS.ToolInfo("sec_userGrants", "Security", "x"), terms) > TS.score(TS.ToolInfo("x_y", "Other", "grants for users"), terms))
check("backbone = the platform's DISCOVERY_TOOLS + the SQL execution tool", TS.is_backbone(TS.ToolInfo("base_tableList")) and TS.is_backbone(TS.ToolInfo("base_readQuery")) and not TS.is_backbone(TS.ToolInfo("dba_tableSpace")))

print("[3] select: offered / held / denied")
s = TS.select("how many customers have missing values?", CAT, enrichment_names=["fitness_db.Customers"], scope=None)
check("backbone + framework always offered", {"base_readQuery", "base_tableList", "TDA_Charting", "TDA_FindTools"} <= set(s.offered))
check("the question's tool offered by name; a sibling the question does not address stays held", "qlty_missingValues" in s.offered and "qlty_columnSummary" in s.held, s.offered)
s_cat = TS.select("run a data quality check on the customers", CAT)
check("a category the question NAMES lifts its whole category", {"qlty_missingValues", "qlty_columnSummary"} <= set(s_cat.offered), s_cat.offered)
check("the rest held — never lost", {"dba_tableSpace", "dba_sessionInfo", "sec_userGrants", "sec_roleMembers", "base_writeQuery"} <= set(s.held), s.held)
check("offered ∪ held ∪ denied == the catalog, disjoint", sorted(s.offered + s.held + s.denied) == sorted(NAMES) and not (set(s.offered) & set(s.held)))
check("every tool carries a reason", all(n in s.reasons for n in NAMES))
scope = AgentScope(deny_writes=True, denied_tools=frozenset({"sec_userGrants"}))
s2 = TS.select("show me the user grants and write the result", CAT, scope=scope)
check("authority: an explicit deny and a write tool under deny_writes are DENIED — even when the question names them",
      set(s2.denied) == {"sec_userGrants", "base_writeQuery"} and "sec_userGrants" not in s2.offered and "base_writeQuery" not in s2.offered, (s2.denied, s2.offered))
check("TDA_* is never denied by the surface", "TDA_Charting" in s2.offered)
s3 = TS.select("anything", CAT, scope=scope, mode="all")
check("mode all: everything not denied is offered, nothing held", not s3.held and set(s3.denied) == {"sec_userGrants", "base_writeQuery"} and len(s3.offered) == len(NAMES) - 2)

print("[4] find: the held set with the same scorer, never the denied set, recovery-shaped")
r = TS.find(s2, CAT, "which sessions are active")
check("a matching held tool is found and WIDENED (recorded)", [t["name"] for t in r["found"]] == ["dba_sessionInfo"] and s2.widened == ["dba_sessionInfo"] and "dba_sessionInfo" not in s2.held and s2.finds == 1, r)
try:
    r2 = TS.find(s2, CAT, "user grants")
    _find_err = None
except Exception as _e:  # noqa: BLE001 — a search that reaches the denied set must FAIL BY NAME, not crash the gate
    r2, _find_err = {"found": [{"name": "?"}], "denied_matching": []}, _e
check("find never raises (a search over the denied set is a named failure, not a crash)", _find_err is None, _find_err)
check("a denied tool the query names is NEVER found — it is a named finding", r2["found"] == [] and r2["denied_matching"] == ["sec_userGrants"] and s2.asked_denied == ["sec_userGrants"] and not (set(t["name"] for t in r2["found"]) & set(s2.denied)), r2)
r3 = TS.find(s2, CAT, "zebra")
check("nothing found → the closest held names, so the model can rephrase", r3["found"] == [] and r3["closest"], r3)
TS.note_asked_denied(s2, "base_writeQuery")
check("a denied tool the model CALLED is recorded too", "base_writeQuery" in s2.asked_denied)
st = s2.state()
check("state carries counts + every list", st["counts"]["denied"] == 2 and st["counts"]["widened"] == 1 and st["counts"]["total"] == len(NAMES) and "reasons" in st)

print("[5] arming: governed mode is the ONLY control (no deployment switch); release; stamp; broadcast")
check("profile_mode: absent/all → all, governed → governed", TS.profile_mode({}) == "all" and TS.profile_mode({"toolSurface": {"mode": "governed"}}) == "governed" and TS.profile_mode({"toolSurface": "x"}) == "all")
check("there is no switch: no TOOL_SURFACE on APP_CONFIG, no enabled() on the module, none in compose", not hasattr(APP_CONFIG, "TOOL_SURFACE") and not hasattr(TS, "enabled") and "TDA_TOOL_SURFACE" not in src("docker-compose.yml") and "TOOL_SURFACE" not in src("src/trusted_data_agent/core/config.py").replace("Governed Tool Surface", ""))
check("mode all → not armed", TS.arm("q", CAT, profile={}) is None and TS.current() is None)
check("armed_governed reads the profile alone", TS.armed_governed({"toolSurface": {"mode": "governed"}}) and not TS.armed_governed({}))
class _Q:
    def __init__(self): self.items = []
    def put_nowait(self, x): self.items.append(x)
q = _Q(); APP_STATE.setdefault("notification_queues", {})["u-ts"] = {q}
async def _drive():
    tok = TS.arm("how many customers have missing values?", CAT, profile={"toolSurface": {"mode": "governed"}}, user_uuid="u-ts", session_id="s-ts")
    st = TS.stamp_for_turn(); r = TS.run_find("active sessions"); st2 = TS.stamp_for_turn(); TS.reset_turn(tok)
    return tok, st, r, st2
tok, st, r, st2 = run(_drive())
check("armed with the switch on + governed; the stamp carries the split", tok is not None and st["counts"]["offered"] >= 4 and st["mode"] == "governed")
check("run_find widens the ambient surface and the stamp follows", r["found"] and st2["widened"] == ["dba_sessionInfo"])
check("released after the turn", TS.current() is None and TS.stamp_for_turn() is None)
check("broadcast: one tool_surface notice at arming and one per find, session-keyed", len(q.items) == 2 and all(i["type"] == "tool_surface" and i["payload"]["session_id"] == "s-ts" for i in q.items), q.items)
APP_STATE["notification_queues"].pop("u-ts", None)
check("run_find without a surface is an honest note, never an error", TS.run_find("x")["note"].startswith("no governed tool surface"))
ES = src("src/trusted_data_agent/agent/execution_service.py")
check("execution_service releases the surface contextvar in finally", "_ts_mod.reset_turn(_surface_token)" in ES)

print("[6] the Optimize overlay: structured_tools filtered, tools_context rebuilt, mcp_tools whole")
from trusted_data_agent.agent.executor import PlanExecutor
class _T:
    def __init__(self, name, desc): self.name, self.description, self.args = name, desc, {}
MT = {t.name: _T(t.name, t.description) for t in CAT}
ex = PlanExecutor.__new__(PlanExecutor)
ex.dependencies = {"STATE": {"structured_tools": ST, "mcp_tools": MT, "structured_prompts": {}, "tool_scopes": {}}}
ex.original_user_input = "how many customers have missing values?"
ex.user_uuid = "u"; ex.session_id = "s"; ex.kg_enrichment_event = {"entity_names": ["fitness_db.Customers"]}
ex._get_profile_config = lambda: {"toolSurface": {"mode": "governed"}}
ov = ex._arm_tool_surface_optimize()
check("armed → a STATE overlay is installed", ov is not None and ex.dependencies["STATE"] is ov)
check("tools_context renders the OFFERED tools only", "`qlty_missingValues`" in ov["tools_context"] and "`dba_tableSpace`" not in ov["tools_context"] and "`sec_userGrants`" not in ov["tools_context"], ov["tools_context"][:300])
check("structured_tools filtered to offered (disabled entries kept as disabled); a category with nothing offered is gone", "Security" not in ov["structured_tools"] and "DBA" not in ov["structured_tools"] and [t["name"] for t in ov["structured_tools"]["Data Quality"]] == ["qlty_missingValues"], {k: [t["name"] for t in v] for k, v in ov["structured_tools"].items()})
check("mcp_tools is NOT filtered — a held tool still executes when named", ov["mcp_tools"] is MT and "dba_tableSpace" in ov["mcp_tools"])
check("the base state is untouched (no global mutation)", ex._surface_base_state["structured_tools"] is ST and len(ST["DBA"]) == 2)
TS.run_find("space per table"); ex._apply_surface_overlay()
check("a find rebuilds the overlay: the found tool now renders", "`dba_tableSpace`" in ex.dependencies["STATE"]["tools_context"])
ev = ex.surface_event()
check("the Live Status event: type tool_surface + a counts summary", ev["type"] == "tool_surface" and "offered" in ev["details"]["summary"] and ev["details"]["counts"]["widened"] == 1)
TS._TURN.set(None)   # the [6] surface was armed inside the executor helper (no token here) — clear it
ex2 = PlanExecutor.__new__(PlanExecutor); ex2.dependencies = {"STATE": {"structured_tools": ST, "mcp_tools": MT}}; ex2.original_user_input = "q"; ex2.user_uuid = "u"; ex2.session_id = "s"; ex2.kg_enrichment_event = None
ex2._get_profile_config = lambda: {}
check("mode all → no overlay, STATE untouched", ex2._arm_tool_surface_optimize() is None and ex2.dependencies["STATE"]["structured_tools"] is ST)
PL = src("src/trusted_data_agent/agent/planner.py"); EX = src("src/trusted_data_agent/agent/executor.py")
check("the executor arms BEFORE context-window assembly (the module renders the surface), on the tool_enabled path only",
      EX.index("self._arm_tool_surface_optimize()") < EX.index("assembled_context = await self._run_context_window_assembly(profile_type, profile_config)") and "if profile_type == 'tool_enabled':" in EX.split("self._arm_tool_surface_optimize()")[0][-600:])
check("the planner RE-SELECTS after the KG enrichment and before it reads the catalog", PL.index("_reselect_tool_surface()") > PL.index("kg_enrichment_text = await self.executor._get_kg_enrichment()") and PL.index("_reselect_tool_surface()") < PL.index("tools_context = _plan_state.get('tools_context'"))
# the module renders the surface, and a re-select with the enrichment's vocabulary GROWS the offered set
from components.builtin.context_window.module_registry import get_module_registry, reset_module_registry
from components.builtin.context_window.base import AssemblyContext
reset_module_registry(); mod = get_module_registry().get_handler("tool_definitions")
tok = TS.arm("how many customers", CAT, profile={"toolSurface": {"mode": "governed"}}, user_uuid="u", session_id="s-m")
actx = AssemblyContext(profile_type="tool_enabled", profile_id="p", session_id="s", user_uuid="u", session_data={"current_query": "q"}, dependencies={"structured_tools": ST}, is_first_turn=True)
c = run(mod.contribute(100000, actx))
check("tool_definitions renders ONLY the surface (held tools absent, offered present)", "`base_readQuery`" in c.content and "`dba_tableSpace`" not in c.content and "`sec_userGrants`" not in c.content, c.content[:300])
ex3 = PlanExecutor.__new__(PlanExecutor); ex3.dependencies = {"STATE": {"structured_tools": ST, "mcp_tools": MT, "structured_prompts": {}}}; ex3.original_user_input = "how many customers"
ex3.user_uuid = "u"; ex3.session_id = "s"; ex3.kg_enrichment_event = {"entity_names": ["dba.tableSpace"]}; ex3.context_builder = None
ex3._surface_base_state = ex3.dependencies["STATE"]; ex3._get_profile_config = lambda: {"toolSurface": {"mode": "governed"}}
grew = ex3._reselect_tool_surface()
check("re-select with the enrichment's vocabulary GROWS the offered set (never shrinks), rebuilds the overlay", grew and "dba_tableSpace" in TS.current().surface.offered and "dba_tableSpace" not in TS.current().surface.held and "`dba_tableSpace`" in ex3.dependencies["STATE"]["tools_context"], TS.current().surface.offered)
check("re-select with nothing new → False, no churn", ex3._reselect_tool_surface() is False)
TS.reset_turn(tok)
c2 = run(mod.contribute(100000, actx))
check("no surface → the module renders every enabled tool (the freeze)", "`dba_tableSpace`" in c2.content and "`sec_userGrants`" in c2.content)
EX = src("src/trusted_data_agent/agent/executor.py")
check("the CW assembly reads the overlay through .get (a ChainMap is not a dict)", "hasattr(app_state, 'get')" in EX and "isinstance(app_state, dict)" not in EX)

print("[7] the ReAct dynamic model: binds offered ∪ found, the ToolNode holds every tool")
from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor
class _Bindable:
    def __init__(self): self.bound = []
    def bind_tools(self, tools): self.bound.append([t.name for t in tools]); return self
class _LCT:
    def __init__(self, n):
        self.name = n; self.description = n
        async def _c(**kw): return f"{n} ran", None
        self.coroutine = _c
lc_tools = [_LCT(n) for n in ("base_readQuery", "dba_tableSpace", "sec_userGrants", "TDA_FindTools")]
llm = _Bindable()
tok = TS.arm("how many customers", TS.catalog_from_langchain(lc_tools), profile={"toolSurface": {"mode": "governed"}}, user_uuid="u", session_id="s-r")
surf = TS.current()
ca = ConversationAgentExecutor.__new__(ConversationAgentExecutor)
ca.llm_instance = llm; ca.mcp_tools = lc_tools; ca.tool_surface = surf
CA = src("src/trusted_data_agent/agent/conversation_agent.py")
check("with a surface the agent is built on a DYNAMIC model over ALL tools", "create_react_agent(model=_bound_model, tools=all_tools)" in CA and "def _bound_model(state, runtime):" in CA)
# drive the dynamic-model function the way LangGraph would
import re as _re
body = _re.search(r"        def _bound_model\(state, runtime\):\n(.*?)\n\n        return create_react_agent", CA, _re.S).group(1)
ns = {"self": ca, "surface": surf.surface, "all_tools": lc_tools}
exec("def _bound_model(state, runtime):\n" + body, ns)
ns["_bound_model"](None, None)
check("first call binds offered only (backbone + TDA_*), the held tools' schemas stay out of the prefix", llm.bound[-1] == ["base_readQuery", "TDA_FindTools"], llm.bound)
TS.run_find("table space"); ns["_bound_model"](None, None)
check("after a find the next call binds the found tool too", "dba_tableSpace" in llm.bound[-1] and "sec_userGrants" not in llm.bound[-1], llm.bound)
TS.reset_turn(tok)
check("no surface → the static form (today's) — the build reads unchanged", "if self.tool_surface is None:\n            return create_react_agent(\n                model=self.llm_instance,\n                tools=self.mcp_tools\n            )" in CA)
check("the prompt's AVAILABLE TOOLS mirrors the surface and names the held count", "more tools are held back for this turn" in CA)
CE = src("src/trusted_data_agent/agent/engines/conversation_engine.py")
check("the conversation engine arms over the effective tools after the enrichment and passes the surface", "_ts.arm(executor.original_user_input" in CE and "tool_surface=_surface" in CE and CE.index("_ts.arm(") > CE.index("kg_enrichment_text = await executor._get_kg_enrichment()"))

print("[8] TDA_FindTools: inert unless governed; a find through the real handler")
from trusted_data_agent.components.manager import get_component_manager
mgr = get_component_manager()
names_all = {t.name for t in mgr.get_langchain_tools({"id": "p", "profile_type": "tool_enabled"}, None)}
names_gov = {t.name for t in mgr.get_langchain_tools({"id": "p", "profile_type": "tool_enabled", "toolSurface": {"mode": "governed"}}, None)}
check("mode all → NOT exposed; governed → exposed", "TDA_FindTools" not in names_all and "TDA_FindTools" in names_gov, (sorted(names_all), sorted(names_gov)))
h = mgr.get_component("find_tools")
handler = getattr(h, "handler", None) or mgr.get_handler("find_tools") if hasattr(mgr, "get_handler") else None
if handler is None:
    from components.builtin.find_tools.handler import FindToolsHandler
    handler = FindToolsHandler()
ok, msg = handler.validate_arguments({"query": ""})
check("validate: query required", ok is False and "query" in msg)
tok = TS.arm("how many customers", CAT, profile={"toolSurface": {"mode": "governed"}}, scope=AgentScope(denied_tools=frozenset({"sec_userGrants"})), user_uuid="u", session_id="s-h")
pl = run(handler.process({"query": "user grants and active sessions"}, {"profile": {}}))
check("the handler finds the matching held tool, names the denied match, and is headless", pl.metadata.get("found") == ["dba_sessionInfo"] and "sec_userGrants" in pl.spec["denied_matching"] and pl.metadata.get("suppress_inline") is True and pl.html == "", (pl.spec, pl.metadata))
check("the surface recorded the widening and the denied ask", TS.current().surface.widened == ["dba_sessionInfo"] and TS.current().surface.asked_denied == ["sec_userGrants"])
TS.reset_turn(tok)
pl2 = run(handler.process({"query": "x"}, {}))
check("no surface → the honest note", "no governed tool surface" in str(pl2.spec.get("message") or pl2.metadata.get("message")), (pl2.spec, pl2.metadata))
from agent_packs.common import component_config as PCF
check("the pack helper never defaults find_tools OFF (the finder follows the surface — a pack must not ship an agent that holds tools with no way back)", "find_tools" not in PCF.ACTION_COMPONENTS and "find_tools" not in PCF.focused_component_config())

print("[9] the persistence stamp + the assurance mechanism at BOUNDED")
SM = src("src/trusted_data_agent/core/session_manager.py")
check("update_last_turn_data stamps tool_surface via setdefault from stamp_for_turn", 'turn_data.setdefault("tool_surface", _surface_stamp)' in SM and "_tsurf.stamp_for_turn()" in SM)
from trusted_data_agent.agent import assurance_points as AP
pt = next(p for p in AP.REGISTRY if p.key == "tool_surface")
check("station bounded · constrain · boundary · turn_key tool_surface · owns nothing · post-door", pt.station == "bounded" and pt.power == "constrain" and AP.POINT_CLASS["tool_surface"] == "boundary" and pt.turn_key == "tool_surface" and AP.POINT_DIMENSIONS["tool_surface"] == () and "tool_surface" in AP._POST_DOOR_POINTS)
check("partition conflict-free", AP.partition_conflicts() == [])
r = AP._read_tool_surface({}, "tool_enabled")
check("no stamp → na, never a pass", r.state == "na")
r = AP._read_tool_surface({"tool_surface": {"counts": {"offered": 6, "total": 11, "denied": 2}, "asked_denied": []}}, "tool_enabled")
check("governed, nothing asked-denied → pass naming the split", r.state == "pass" and "offered 6 of 11" in r.detail and "2 denied" in r.detail)
r = AP._read_tool_surface({"tool_surface": {"counts": {"offered": 6, "total": 11}, "asked_denied": ["base_writeQuery"]}}, "tool_enabled")
check("a denied tool asked for → warn, acted, named", r.state == "warn" and r.acted and "base_writeQuery" in r.detail)


print("[9b] review fixes: authority fails CLOSED on the surface, the widen bound, the denied-call seam, the widen hook")
class _Scope:  # a scope whose authority check RAISES
    pass
import trusted_data_agent.core.agent_authority as _AA
_orig_allowed = _AA.is_tool_allowed
def _raising(scope, name):
    if name == "base_writeQuery": raise RuntimeError("authority store unreadable")
    return (name != "sec_userGrants", "denied by scope" if name == "sec_userGrants" else "")
_AA.is_tool_allowed = _raising
try:
    d = TS.denied_names(CAT, _Scope())
    check("a tool whose authority cannot be resolved is DENIED on the surface (fail closed), with the reason; a resolved denial keeps its own reason; framework tools never",
          "unresolved" in d.get("base_writeQuery", "") and d.get("sec_userGrants") == "denied by scope" and "TDA_Charting" not in d and "base_readQuery" not in d, d)
finally:
    _AA.is_tool_allowed = _orig_allowed
BIG = list(CAT) + [TS.ToolInfo(f"sec_extraGrant{i}", "Security", "grants held by a user") for i in range(TS.MAX_WIDEN + 4)]
s3 = TS.select("space", BIG, scope=None)
r3 = TS.find(s3, BIG, "grants", limit=50)
check("find's limit is BOUNDED by MAX_WIDEN whatever the caller asks (more candidates matched than the bound)", len(r3["found"]) == TS.MAX_WIDEN and len(s3.held) > 0, (len(r3["found"]), len(s3.held)))
# the chokepoints' seam: a denied CALL becomes the named finding on the ambient surface
APP_CONFIG.TOOL_SURFACE = True
tok = TS.arm("show me grants", CAT, profile={"toolSurface": {"mode": "governed"}}, scope=None)
TS.current().surface.denied.append("sec_userGrants")
check("note_denied_call: a denied tool the model called is recorded once, on the surface, from any chokepoint", TS.note_denied_call("sec_userGrants") is True and TS.current().surface.asked_denied == ["sec_userGrants"] and TS.note_denied_call("sec_userGrants") is False and TS.note_denied_call("base_readQuery") is False)
# the widen hook: a find that widened tells the engine
hits = []
TS.current().on_widen = lambda: hits.append(1)
rr = TS.run_find("sessions")
check("run_find calls the engine's on_widen exactly when something was found", len(hits) == (1 if rr["found"] else 0) and rr["found"])
TS.run_find("zzzz-nothing")
check("…and not on an empty find", len(hits) == 1)
TS.reset_turn(tok)
TEs = src("src/trusted_data_agent/agent/tool_enforcement.py"); ADs = src("src/trusted_data_agent/mcp_adapter/adapter.py"); EXs2 = src("src/trusted_data_agent/agent/executor.py")
check("both chokepoints record a denied call on the surface (ReAct wrapper + MCP adapter) — through the one gate",
      "_tsurf.note_denied_call(tool_name)" in TEs.split("def sql_gate_before")[1].split("\ndef ")[0]
      and "sql_gate_before(" in ADs and "sql_gate_before(" in TEs.split("def _make_guarded_coroutine")[1])
check("Optimize arms the widen hook to its overlay rebuild", "_ts_now.on_widen = self._apply_surface_overlay" in EXs2)
print("[10] the contract: portable, UNSIGNED")
from trusted_data_agent.core import profile_config_contract as PCC
spec = next(s for s in PCC.REGISTRY if s.key == "toolSurface")
check("portable, not signed (what the model sees, not what it may do)", "toolSurface" in PCC.portable_keys() and not spec.signed)
base = {"profile_type": "tool_enabled", "tag": "X"}
check("governed↔all never moves the signature", PCC.signature_for(dict(base)) == PCC.signature_for(dict(base, toolSurface={"mode": "governed"})))

print("[11] the frontend contract")
UI = src("static/js/ui.js"); EH = src("static/js/eventHandlers.js"); NT = src("static/js/notifications.js"); IDX = src("templates/index.html"); CH = src("static/js/handlers/configurationHandler.js"); CSS = src("static/css/main.css")
check("ui: tool_surface card renderer wired at both live dispatch sites, the reload branch, the assurance category", UI.count("case 'tool_surface'") == 2 and 'type === "tool_surface"' in UI and "_renderToolSurfaceDetails" in UI and "type === 'tool_surface'" in [l for l in UI.splitlines() if ") return 'assurance';" in l][0])
check("ui: chips per set, each hoverable for its reason, the denied ask named", "ts-offered" in UI and "ts-held" in UI and "ts-denied" in UI and "ts-widened" in UI and "data-tip=\"${escapeHtml(String(reasons[n]" in UI and "Asked for, denied" in UI)
check("reload: synthesized from turnData.tool_surface only when the trace lacks it; titled Tool Surface", "type: 'tool_surface'" in EH and "s.action.metadata?.type === 'tool_surface'" in EH and "if (eventType === 'tool_surface') return 'Tool Surface';" in EH)
check("dial: tool_surface lights BOUNDED; notifications keep the dial current, session-guarded", "tool_surface: 'bounded'" in src("static/js/handlers/assuranceDial.js") and "case 'tool_surface'" in NT and "p.session_id === state.currentSessionId" in NT)
check("editor: the 'Tools in context' select beside the MCP server, in the surface contract, load + save", 'id="profile-modal-tool-surface"' in IDX and "toolSurfaceField:" in CH and "profileData.toolSurface = { mode: v === 'governed' ? 'governed' : 'all' }" in CH and "profile.toolSurface.mode === 'governed'" in CH)
check("editor: a NEW agent starts on Relevant per turn; a stored agent keeps its own (absent = all)", ": 'governed';" in CH.split("#profile-modal-tool-surface")[1][:400] and "? ((profile.toolSurface && profile.toolSurface.mode === 'governed') ? 'governed' : 'all')" in CH)
check("editor: the two options read as the person sees them; the tooltip no longer names a deployment switch", 'Tools in context' in IDX and '<option value="all">All enabled tools</option>' in IDX and '<option value="governed">Relevant per turn</option>' in IDX and "deployment master switch" not in IDX.split('id="profile-field-tool-surface"')[1][:2500])
check("the Live Status card says what decided (Ranked by: words · meaning · the finder)", "Ranked by" in UI and "semantic_added" in UI and "no Tool Finder on this agent" in UI and "warming up" in UI)
check("bootstrap: the template's tool_enabled agents start on Relevant per turn", all((p.get("toolSurface") or {}).get("mode") == "governed" for p in json.loads(src("tda_config.json")).get("profiles", []) if p.get("profile_type") == "tool_enabled"))
MAINJS = src("static/js/main.js")
_pin = lambda text, rx: max((tuple(int(x) for x in m.split(".")) for m in _re.findall(rx, text)), default=(0,))
check("version pins bumped with the content (configurationHandler, ui.js, main.js) — at least, so a later bump keeps them", _pin(MAINJS, r"configurationHandler\.js\?v=([\d.]+)") >= (2, 194) and _pin(MAINJS, r"ui\.js\?v=([\d.]+)") >= (1, 261) and _pin(IDX, r"ui\.js\?v=([\d.]+)") >= (1, 261) and _pin(IDX, r"main\.js'\) \}\}\?v=([\d.]+)") >= (10, 111) and "ui.js?v=1.260" not in EH)
check("chip CSS on theme tokens", ".ts-chip.ts-denied" in CSS and "var(--trust-danger" in CSS.split(".ts-chip.ts-denied")[1][:120])

print("[12] mode all ⇒ no-ops")
check("arm/run_find/stamp on an `all` agent", TS.arm("q", CAT, profile={"toolSurface": {"mode": "all"}}) is None and TS.stamp_for_turn() is None and "note" in TS.run_find("x"), (TS.current(), TS.run_find("x")))

print("[13] the second ranker — meaning, pure: semantic_pick is ONE rule; select and find widen by it")
sims = {"dba_tableSpace": 0.52, "dba_sessionInfo": 0.44, "qlty_columnSummary": 0.41, "sec_roleMembers": 0.30, "base_readQuery": 0.60, "qlty_missingValues": 0.10}
check("the bar is max(floor, ratio × the question's best match over the WHOLE catalog) — a strong hit elsewhere raises it", TS.semantic_pick(sims, ["dba_tableSpace", "dba_sessionInfo", "qlty_columnSummary", "sec_roleMembers"]) == [("dba_tableSpace", 0.52)], TS.semantic_pick(sims, ["dba_tableSpace", "dba_sessionInfo"]))
check("below the floor nothing is picked whatever the ratio", TS.semantic_pick({"a": 0.17, "b": 0.16}, ["a", "b"]) == [] and TS.semantic_pick({"a": 0.18}, ["a"]) == [("a", 0.18)])
check("best first, at most cap, ties by name", [n for n, _ in TS.semantic_pick({c: 0.5 for c in "fedcba"}, list("abcdef"), cap=3)] == ["a", "b", "c"] and TS.semantic_pick({"a": 0.5}, ["a"], cap=0) == [])
check("None / empty → nothing (the words stand alone)", TS.semantic_pick(None, ["a"]) == [] and TS.semantic_pick({}, ["a"]) == [] and TS.semantic_pick({"a": 0.9}, []) == [])
s13 = TS.select("how much disk is each table consuming", CAT, semantic={"dba_tableSpace": 0.45, "dba_sessionInfo": 0.20, "qlty_missingValues": 0.30})
check("a held tool nearest in meaning is OFFERED with its score as the reason; the next one under the bar stays held", "dba_tableSpace" in s13.offered and s13.reasons["dba_tableSpace"] == "near in meaning to the question (0.45)" and "dba_sessionInfo" in s13.held and "qlty_missingValues" in s13.held, (s13.offered, s13.reasons.get("dba_tableSpace")))
check("the stamp's ranker says what decided: words, meaning used, the rule's constants, the finder present, the additions", s13.ranker["lexical"] is True and s13.ranker["semantic"] is True and s13.ranker["finder"] is True and s13.ranker["semantic_added"] == ["dba_tableSpace"] and s13.ranker["floor"] == SEM.FLOOR and s13.ranker["ratio"] == SEM.RATIO and s13.ranker["cap"] == SEM.CAP, s13.ranker)
check("a denied tool is never reached by meaning", "sec_userGrants" in TS.select("x", CAT, scope=AgentScope(denied_tools=frozenset({"sec_userGrants"})), semantic={"sec_userGrants": 0.99}).denied)
check("a lexical hit is not re-added (offered ∪ held ∪ denied stays a partition)", sorted(s13.offered + s13.held + s13.denied) == sorted(NAMES) and not (set(s13.offered) & set(s13.held)))
s13n = TS.select("how much disk is each table consuming", CAT, semantic=None)
check("no semantic input → words only, ranker.semantic False, the paraphrase stays held", s13n.ranker["semantic"] is False and "dba_tableSpace" in s13n.held and s13n.ranker["semantic_added"] == [])
check("state() carries the ranker", "ranker" in s13.state() and s13.state()["ranker"]["semantic_added"] == ["dba_tableSpace"])
s13f = TS.select("x", CAT)
r13 = TS.find(s13f, CAT, "storage footprint", semantic={"dba_tableSpace": 0.40, "sec_roleMembers": 0.39, "dba_sessionInfo": 0.05})
check("find: the words miss, meaning finds — reason carries the score; bounded by the limit", [t["name"] for t in r13["found"]] == ["dba_tableSpace", "sec_roleMembers"] and s13f.reasons["dba_tableSpace"] == "found by the model (near in meaning, 0.40)" and "dba_sessionInfo" in s13f.held, r13)
s13g = TS.select("x", CAT)
r13g = TS.find(s13g, CAT, "sessions", limit=1, semantic={"dba_tableSpace": 0.9})
check("find: a lexical hit fills the bound first; meaning only fills what is left", [t["name"] for t in r13g["found"]] == ["dba_sessionInfo"] and "dba_tableSpace" in s13g.held, r13g)

print("[14] the finder guard — a governed agent with no TDA_FindTools holds NOTHING")
CAT_NF = [t for t in CAT if t.name != TS.FIND_TOOL_NAME]
s14 = TS.select("how many customers", CAT_NF, scope=AgentScope(denied_tools=frozenset({"sec_userGrants"})), semantic={"dba_tableSpace": 0.9})
check("every non-denied tool offered, none held, the reason on each chip, denials kept, ranker.finder False", not s14.held and "sec_userGrants" in s14.denied and set(s14.offered) == set(n for n in [t.name for t in CAT_NF] if n != "sec_userGrants") and s14.reasons["qlty_columnSummary"] == "no Tool Finder on this agent — nothing is held" and s14.ranker["finder"] is False and s14.ranker["semantic_added"] == [], (s14.held, s14.ranker))
check("has_finder reads the catalog by the one name", TS.has_finder(CAT) and not TS.has_finder(CAT_NF))

print("[15] the runtime with an injected embedder: arm and run_find rank by meaning; the stamp names the model and its state")
class _FakeEmbedder:
    # one axis per meaning; a text's vector is which meanings its words touch
    AXES = [("space", {"space", "disk", "storage", "consuming", "footprint"}), ("session", {"session", "sessions", "active", "logged"}),
            ("missing", {"missing", "blank", "blanks", "null"}), ("grant", {"grant", "grants", "permission"}), ("chart", {"chart", "plot"})]
    model_name = "fake-axes"
    def embed_texts(self, texts):
        out = []
        for t in texts:
            words = set(TS._tokens(t)) | {w.lower() for w in t.replace(":", " ").replace(".", " ").split()}
            v = [1.0 if words & ax else 0.0 for _, ax in self.AXES] + [0.05]
            out.append(v)
        return out
    def embed_query(self, q): return self.embed_texts([q])[0]
SEM.set_embedder(_FakeEmbedder())
check("injected embedder → ready", SEM.ready() and SEM.state() == "ready")
sims15 = SEM.similarity("how much disk is each table consuming", CAT)
check("similarity: one score per tool, the paraphrase lands on the tool about space", sims15 and set(sims15) == set(NAMES) and max(sims15, key=sims15.get) == "dba_tableSpace", sims15)
calls = {"n": 0}; _orig_embed = SEM._embed
def _counting(texts): calls["n"] += 1; return _orig_embed(texts)
SEM._embed = _counting
SEM.similarity("anything", CAT); SEM.similarity("anything else", CAT)
check("the catalog is embedded ONCE per content signature; each further question costs one query encode", calls["n"] == 2, calls)
SEM._embed = _orig_embed
tok15 = TS.arm("how much disk is each table consuming", CAT, profile={"toolSurface": {"mode": "governed"}}, user_uuid="u15", session_id="s15")
st15 = TS.stamp_for_turn()
check("arm: the paraphrase is offered by meaning (the words alone held it); the stamp names the ranker", tok15 is not None and "dba_tableSpace" in st15["offered"] and st15["reasons"]["dba_tableSpace"].startswith("near in meaning") and st15["ranker"].get("semantic") is True and st15["ranker"].get("semantic_state") == "ready" and isinstance(st15["ranker"].get("semantic_model"), str), (st15["offered"], st15["ranker"]))
r15 = TS.run_find("who is logged on")
check("run_find: no word overlap, found by meaning", [t["name"] for t in r15["found"]] == ["dba_sessionInfo"] and TS.current().surface.reasons["dba_sessionInfo"].startswith("found by the model (near in meaning"), (r15, TS.current().surface.reasons.get("dba_sessionInfo")))
TS.reset_turn(tok15)
SEM.set_embedder(None); SEM._embedder_state = "unavailable"
tok15b = TS.arm("how much disk is each table consuming", CAT, profile={"toolSurface": {"mode": "governed"}})
st15b = TS.stamp_for_turn(); TS.reset_turn(tok15b)
check("embedder unavailable → words only, honestly stamped, the turn never waits", st15b["ranker"].get("semantic") is False and st15b["ranker"].get("semantic_state") == "unavailable" and "dba_tableSpace" in st15b["held"], st15b["ranker"])
SEM._embedder_state = "cold"; _resolved = {"n": 0}; _orig_resolve = SEM._resolve_sync
def _fake_resolve(): _resolved["n"] += 1; SEM._embedder_state = "unavailable"
SEM._resolve_sync = _fake_resolve
check("cold → similarity returns None at once and starts ONE warm (idempotent) — a model load never sits on a turn", SEM.similarity("q", CAT) is None and SEM.state() in ("warming", "unavailable"))
import time as _time
for _ in range(50):
    if _resolved["n"]: break
    _time.sleep(0.02)
SEM.warm(); SEM.warm()
check("warm() resolved once on a thread; later calls are no-ops", _resolved["n"] == 1, _resolved)
SEM._resolve_sync = _orig_resolve; SEM._embedder_state = "unavailable"
check("tool_text: the identifier split into words, the category, the description", SEM.tool_text("dba_tableSpace", "DBA", "space per table") == "dba table space: DBA. space per table")

print("[16] full descriptions for the offered few — the tool_definitions module under a surface")
from components.builtin.context_window.module_registry import get_module_registry, reset_module_registry
from components.builtin.context_window.base import AssemblyContext
reset_module_registry(); tdm = get_module_registry().get_handler("tool_definitions")
def _ctx16(first): return AssemblyContext(profile_type="tool_enabled", profile_id="p1", session_id="s", user_uuid="u", session_data={"current_query": "q"}, dependencies={"structured_tools": ST}, is_first_turn=first)
tok16 = TS.arm("rows with missing values", CAT, profile={"toolSurface": {"mode": "governed"}})
c16 = run(tdm.contribute(100000, _ctx16(False)))
check("governed, a LATER turn: full mode with the descriptions of the offered tools; held tools absent", c16.metadata.get("mode") == "full" and "`qlty_missingValues` (tool): rows with missing values" in c16.content and "dba_tableSpace" not in c16.content, (c16.metadata, c16.content[:200]))
TS.reset_turn(tok16)
c16b = run(tdm.contribute(100000, _ctx16(False)))
check("no surface, a later turn: names-only, every tool (the freeze)", c16b.metadata.get("mode") == "names_only" and "`dba_tableSpace`" in c16b.content)

print(f"\n=== S6 tool surface: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
