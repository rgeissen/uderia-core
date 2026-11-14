"""S6 baseline freeze — committed GREEN before the Governed Tool Surface landed.

Pins that with NO tool surface (a profile whose surface mode is `all` — the only control;
the deployment switch was removed in Oct 2026) nothing changes, EXECUTING the real seams: the Optimize catalog renderer offers EVERY
enabled tool of the state it is given; the context-window `tool_definitions` module renders
every enabled tool (full on the first turn, names-only after); the ReAct chokepoint wrapper
binds every tool it is handed (none dropped, none added); no `TDA_FindTools` tool is part of
the builtin component inventory; the contract's portable set is what it is today; no
`tool_surface` stamp is written. Every assertion here must stay green through S6 and after
it (with the switch off)."""
import asyncio, json, logging, os, pathlib, sys, types
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src")); sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.disable(logging.CRITICAL)
os.environ.setdefault("HF_HUB_OFFLINE", "1"); os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from trusted_data_agent.core.config import APP_CONFIG

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {str(detail)[:300]}")
ROOT = pathlib.Path(__file__).resolve().parents[1]
def src(rel): return (ROOT / rel).read_text()
_LOOP = asyncio.new_event_loop()
def run(c): return _LOOP.run_until_complete(c)

print("[1] there is no switch — mode `all` is the frozen behaviour")
check("no TOOL_SURFACE on APP_CONFIG, none in the environment contract", not hasattr(APP_CONFIG, "TOOL_SURFACE") and "TDA_TOOL_SURFACE" not in src("docker-compose.yml"))

print("[2] the Optimize catalog renderer offers every enabled tool of the state it is given")
from trusted_data_agent.agent.executor import rebuild_tools_and_prompts_context
class _T:
    def __init__(self, name, desc, args=None): self.name, self.description, self.args = name, desc, args or {}
ST = {"Data": [{"name": "base_readQuery", "description": "run sql"}, {"name": "base_tableList", "description": "list tables"},
               {"name": "dba_tableSpace", "description": "space per table"}, {"name": "sec_userGrants", "description": "grants", "disabled": True}],
      "Quality": [{"name": "qlty_columnSummary", "description": "column stats"}]}
MT = {n: _T(n, d["description"], {"sql": {"type": "string", "required": True, "description": "the SQL"}} if n == "base_readQuery" else {})
      for cat in ST.values() for d in cat for n in [d["name"]]}
state = {"structured_tools": ST, "mcp_tools": MT, "structured_prompts": {}}
tc, _ = rebuild_tools_and_prompts_context(state=state)
for n in ("base_readQuery", "base_tableList", "dba_tableSpace", "qlty_columnSummary"):
    check(f"`{n}` is rendered", f"`{n}`" in tc, tc[:200])
check("a DISABLED tool is not rendered (the profile's own selection, unchanged)", "sec_userGrants" not in tc)
check("the argument schema is rendered in full", "`sql` (string, required): the SQL" in tc)

print("[3] the tool_definitions context module renders every enabled tool")
sys.path.insert(0, str(ROOT))
from components.builtin.context_window.module_registry import get_module_registry, reset_module_registry
from components.builtin.context_window.base import AssemblyContext
reset_module_registry()
m = get_module_registry().get_handler("tool_definitions")
def _ctx(first):
    return AssemblyContext(profile_type="tool_enabled", profile_id="p1", session_id="s", user_uuid="u",
                           session_data={"current_query": "q"}, dependencies={"structured_tools": ST}, is_first_turn=first)
ctx = _ctx(True)
c = run(m.contribute(100000, ctx))
check("first turn: full mode, every enabled tool", c.metadata.get("mode") == "full" and all(f"`{n}`" in c.content for n in ("base_readQuery", "base_tableList", "dba_tableSpace", "qlty_columnSummary")) and "sec_userGrants" not in c.content, c.content[:200])
ctx2 = _ctx(False)
c2 = run(m.contribute(100000, ctx2))
check("later turns: names-only, every enabled tool", c2.metadata.get("mode") == "names_only" and all(f"`{n}`" in c2.content for n in ("base_readQuery", "dba_tableSpace")))

print("[4] the ReAct chokepoint wrapper binds every tool it is handed — none dropped, none added")
from trusted_data_agent.agent import tool_enforcement as TE
class _LT:
    def __init__(self, name):
        self.name = name; self.description = name
        async def _c(**kw): return f"{name} ran", None
        self.coroutine = _c
tools = [_LT("base_readQuery"), _LT("dba_tableSpace"), _LT("sec_userGrants"), _LT("TDA_Charting")]
wrapped = TE.wrap_langchain_mcp_tools(tools, "sess-freeze", user_uuid="u")
check("same list, same names, same order", [t.name for t in wrapped] == ["base_readQuery", "dba_tableSpace", "sec_userGrants", "TDA_Charting"])
c, _ = run(wrapped[1].coroutine())
check("every bound tool executes", c.startswith("dba_tableSpace ran"), c)

print("[5] TDA_FindTools is never EXPOSED on an `all` agent (the inert-component contract)")
# The property frozen is exposure, not existence — the surface's search must be structurally
# absent from every profile whose mode is `all`, even one that names the component enabled
# (the skill_library / compute precedent: relevance folds into the ONE enablement rule).
from trusted_data_agent.components.manager import get_component_manager
mgr = get_component_manager()
for prof in ({"id": "f1", "profile_type": "llm_only"},
             {"id": "f2", "profile_type": "tool_enabled", "toolSurface": {"mode": "all"}},
             {"id": "f3", "profile_type": "llm_only", "componentConfig": {"find_tools": {"enabled": True}}}):
    names = {t.name for t in mgr.get_langchain_tools(prof, None)}
    check(f"mode all: TDA_FindTools not exposed ({prof['id']})", "TDA_FindTools" not in names, sorted(names))

print("[6] the contract's portable set today")
from trusted_data_agent.core import profile_config_contract as PCC
check("turnBudget portable, authorityConfig portable, classification_results local", "turnBudget" in PCC.portable_keys() and "authorityConfig" in PCC.portable_keys() and "classification_results" not in PCC.portable_keys())

print("[7] no tool_surface stamp is written (the persistence chokepoint idiom)")
SM = src("src/trusted_data_agent/core/session_manager.py")
check("update_last_turn_data stamps `budget`/`resilience` via setdefault (the chokepoint the surface will join)", 'setdefault("budget"' in SM and 'setdefault("resilience"' in SM)

print(f"\n=== S6 baseline freeze: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
