"""An agent's MCP tool/prompt selection has ONE home — the profile (2026-09-29).

Reported: "the mcp prompts still show as activated in the profile configuration even though
I assume they are deactivated". Three defects, one family:

  1. the resource panel's eye icon changed only the process-wide APP_STATE list — never the
     profile — and its endpoint had no authentication, so the change reached every user, came
     back on the next profile switch, and the editor kept showing the prompt enabled;
  2. the profile editor saved `0 selected === 0 available` as ['*'], so a save while the prompt
     list had failed (the failure was swallowed as "does not support prompts") switched every
     prompt back on;
  3. nothing refused to RUN a prompt the agent had switched off.

Each section carries the mutation that would reintroduce its defect. CI-pure: a fake config
manager, node for the shipped editor rule.
"""
import ast, os, re, subprocess, sys, pathlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import profile_selection as ps

ROOT = pathlib.Path(__file__).resolve().parent.parent
PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")


# ── [1] toggle_selection — the list arithmetic ───────────────────────────────
U = ["p1", "p2", "p3"]
check("off from a literal list removes it", ps.toggle_selection(["p1", "p2"], "p1", False, U) == ["p2"])
check("off from '*' expands to the universe minus it", ps.toggle_selection(["*"], "p2", False, U) == ["p1", "p3"])
try:
    ps.toggle_selection(["*"], "p2", False, [])
    check("off from '*' without a universe is refused, never guessed", False, "no raise")
except ps.SelectionUnknown:
    check("off from '*' without a universe is refused, never guessed", True)
check("on adds to a literal list", ps.toggle_selection(["p1"], "p2", True, U) == ["p1", "p2"])
check("on that completes the universe collapses to '*'", ps.toggle_selection(["p1", "p2"], "p3", True, U) == ["*"])
check("on while '*' stays '*'", ps.toggle_selection(["*"], "p1", True, U) == ["*"])
check("on from [] (all off) adds exactly one", ps.toggle_selection([], "p1", True, U) == ["p1"])
check("on is idempotent", ps.toggle_selection(["p1"], "p1", True, []) == ["p1"])
check("the last one off leaves [] (none), not '*'", ps.toggle_selection(["p1"], "p1", False, U) == [])


# ── [2] is_selected / toggle_updates — primary and additional servers ────────
PROFILE = {"id": "prof-1", "tag": "OPT", "mcpServerId": "srv-a", "tools": ["*"], "prompts": ["p1", "p2"],
           "additionalMcpServers": [{"id": "srv-b", "alias": "crm", "tools": ["*"], "prompts": ["q1"]}],
           "additionalMcpClassification": {"srv-b": {"prompts": {"General": [{"name": "q1"}, {"name": "q2"}]},
                                                     "tools": {"General": [{"name": "t9"}]}}}}
check("is_selected: selected primary prompt", ps.is_selected(PROFILE, "p1", "prompts") is True)
check("is_selected: unselected primary prompt", ps.is_selected(PROFILE, "p3", "prompts") is False)
check("is_selected: additional server prompt by its qualified name", ps.is_selected(PROFILE, "crm__q1", "prompts") is True)
check("is_selected: additional server unselected", ps.is_selected(PROFILE, "crm__q2", "prompts") is False)
check("is_selected: an unknown alias reads as a primary name (primary names may contain '__')",
      ps.is_selected(PROFILE, "zzz__q1", "prompts") is False)
check("is_selected: no server at all → None", ps.is_selected({"id": "x"}, "p1", "prompts") is None)

upd = ps.toggle_updates(PROFILE, "p1", "prompts", False, lambda s: U)
check("primary toggle writes the profile's own list", upd == {"prompts": ["p2"]}, upd)
upd = ps.toggle_updates(PROFILE, "crm__q2", "prompts", True, lambda s: ["q1", "q2"])
check("additional toggle writes that server's entry (collapses to '*')",
      upd["additionalMcpServers"][0]["prompts"] == ["*"], upd)
check("…and never mutates the stored profile", PROFILE["additionalMcpServers"][0]["prompts"] == ["q1"])


# ── [3] plan_toggle — the refusals ───────────────────────────────────────────
def refused(profile, name, kind, enable, uni, status):
    try:
        ps.plan_toggle(profile, name, kind, enable, lambda s: uni)
        return False
    except ps.ToggleRefused as e:
        return e.status == status
check("a platform TDA_ tool is refused", refused(PROFILE, "TDA_Charting", "tools", False, ["TDA_Charting"], 400))
check("a missing profile is 404", refused(None, "p1", "prompts", False, U, 404))
check("a name no server of the agent owns is 404", refused({"id": "x"}, "p1", "prompts", False, U, 404))
check("an unknown-alias name the primary does not offer is 404", refused(PROFILE, "zzz__q1", "prompts", False, U, 404))
check("a name the server does not offer is 404", refused(PROFILE, "nope", "prompts", False, U, 404))
check("off from '*' with an unknown universe is 409", refused(PROFILE, "t1", "tools", False, [], 409))
check("a valid toggle plans the write", ps.plan_toggle(PROFILE, "p2", "prompts", False, lambda s: U) == {"prompts": ["p1"]})


# ── [4] universe_resolver — where "every name" comes from ───────────────────
class FakeCM:
    def __init__(self, profiles, snaps, master=None):
        self.p, self.s, self.m = profiles, snaps, master
    def get_profile(self, pid, u): return self.p.get(pid)
    def get_profile_classification(self, pid, u): return self.s.get(pid, {})
    def get_master_classification_profile_id(self, u, sid, strict=False): return self.m

snap = {"prompts": {"A": [{"name": "p1"}, {"name": "p2"}], "B": [{"name": "p3"}]}}
from trusted_data_agent.core.agent_mcp_servers import servers_of
prim, addl = servers_of(PROFILE)
cm = FakeCM({"prof-1": PROFILE}, {"prof-1": snap})
res = ps.universe_resolver(PROFILE, "prompts", "u", cm)
check("primary universe = own classification snapshot", res(prim) == ["p1", "p2", "p3"], res(prim))
check("additional universe = that server's own snapshot", res(addl) == ["q1", "q2"], res(addl))
res = ps.universe_resolver(PROFILE, "prompts", "u", cm, live_names=["p4", "crm__q7"])
check("live names of the primary are added; an alias-qualified live name is not",
      res(prim) == ["p1", "p2", "p3", "p4"], res(prim))
inh = dict(PROFILE, id="prof-2", inherit_classification=True)
cm2 = FakeCM({"prof-2": inh, "master": {"id": "master", "mcpServerId": "srv-a"}},
             {"master": {"prompts": {"A": [{"name": "m1"}]}}}, master="master")
check("an inheriting profile reads the master's snapshot",
      ps.universe_resolver(inh, "prompts", "u", cm2)(servers_of(inh)[0]) == ["m1"])
cm3 = FakeCM({"prof-1": PROFILE}, {"prof-1": {"tools": {"System Tools": [{"name": "TDA_X"}], "G": [{"name": "t1"}]}}})
check("TDA_ tools never count as part of the MCP universe",
      ps.universe_resolver(PROFILE, "tools", "u", cm3)(prim) == ["t1"])


# ── [5] prompts_listing_unsupported — "no prompts" vs "listing failed" ───────
class _Err:  # the MCP error shape: exc.error.code
    def __init__(self, code): self.code = code
class McpLike(Exception):
    def __init__(self, code, msg=""): super().__init__(msg); self.error = _Err(code)
check("-32601 is 'does not offer prompts'", ps.prompts_listing_unsupported(McpLike(-32601)))
check("'Method not found' text is 'does not offer prompts'", ps.prompts_listing_unsupported(Exception("Method not found")))
check("a timeout is a FAILURE, not 'no prompts'", not ps.prompts_listing_unsupported(TimeoutError("timed out")))
check("another MCP error is a failure", not ps.prompts_listing_unsupported(McpLike(-32603, "internal")))


# ── [6] disabled_prompt_refusal — the execution rule, fail-open ─────────────
import trusted_data_agent.core.config_manager as cmmod
_orig = cmmod.get_config_manager
cmmod.get_config_manager = lambda: FakeCM({"prof-1": PROFILE}, {})
try:
    msg = ps.disabled_prompt_refusal("prof-1", "u", "p3")
    check("a switched-off prompt is refused, naming the agent", bool(msg) and "@OPT" in msg and "p3" in msg, msg)
    check("a selected prompt runs", ps.disabled_prompt_refusal("prof-1", "u", "p1") is None)
    check("an unknown profile fails open", ps.disabled_prompt_refusal("missing", "u", "p3") is None)
    check("the system default fails open", ps.disabled_prompt_refusal("__system_default__", "u", "p3") is None)
finally:
    cmmod.get_config_manager = _orig
cmmod.get_config_manager = lambda: (_ for _ in ()).throw(RuntimeError("db down"))
try:
    check("an unreadable config fails open", ps.disabled_prompt_refusal("prof-1", "u", "p3") is None)
finally:
    cmmod.get_config_manager = _orig


# ── [7] wiring — on the AST / source ────────────────────────────────────────
routes = (ROOT / "src/trusted_data_agent/api/routes.py").read_text()
tree = ast.parse(routes)
fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
for name in ("toggle_tool_status", "toggle_prompt_status"):
    fn = fns.get(name)
    decos = [ast.unparse(d) for d in fn.decorator_list] if fn else []
    check(f"{name} requires authentication", "require_auth" in decos, decos)
helper = ast.unparse(fns["_toggle_selection"]) if "_toggle_selection" in fns else ""
check("the toggle persists on the profile", "update_profile(" in helper and "plan_toggle(" in helper)
check("the toggle no longer writes a set of names into APP_STATE",
      ".add(" not in helper and ".discard(" not in helper)
check("APP_STATE is refreshed only for the loaded profile", "is_loaded" in helper
      and helper.find("is_loaded") < helper.find("_regenerate_contexts"))

planner = (ROOT / "src/trusted_data_agent/agent/planner.py").read_text()
i = planner.find("async def _generate_meta_plan")
seg = planner[i:i + 3000]
check("the planner refuses a switched-off prompt before loading it",
      "disabled_prompt_refusal(" in seg and seg.find("disabled_prompt_refusal(") < seg.find("load_mcp_prompt("))
executor = (ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
j = executor.find("if is_delegated_prompt_phase:")
seg = executor[j:j + 4000]
check("a delegated prompt phase skips a switched-off prompt before running it",
      "disabled_prompt_refusal(" in seg and seg.find("disabled_prompt_refusal(") < seg.find("_run_sub_prompt("))
rest = (ROOT / "src/trusted_data_agent/api/rest_routes.py").read_text()
for route in ('"/v1/prompts/<prompt_name>/execute"', '"/v1/prompts/<prompt_name>/execute-raw"'):
    k = rest.find(route)
    check(f"{route} refuses a switched-off prompt", k >= 0 and "prompt_disabled" in rest[k:k + 6000])
m = rest.find('"/v1/mcp/resources"')
check("/v1/mcp/resources reports a failed prompt listing",
      "prompts_listing_unsupported(" in rest[m:m + 5000] and '"prompts_error"' in rest[m:m + 6000])


# ── [8] the editor's save rule — the shipped function, executed ──────────────
js = (ROOT / "static/js/handlers/configurationHandler.js").read_text()
mm = re.search(r"export function resolveSavedSelection\([\s\S]*?\n\}", js)
check("the editor declares resolveSavedSelection", bool(mm))
check("the editor save routes both lists through it",
      js.count("resolveSavedSelection(resourceLoad.tools") == 1 and js.count("resolveSavedSelection(resourceLoad.prompts") == 1)
check("no '0 === 0 → *' save remains", "selectedPrompts.length === allPrompts.length ? ['*']" not in js)
if mm:
    body = mm.group(0).replace("export function", "function")
    cases = [
        ("error keeps the stored list", ["error", [], [], ["p1"], True], ["p1"]),
        ("loading keeps the stored list", ["loading", [], [], ["p1"], True], ["p1"]),
        ("ok but nothing offered keeps the stored list", ["ok", [], [], [], True], []),
        ("a new profile defaults to all", ["error", [], [], None, False], ["*"]),
        ("all checked collapses to '*'", ["ok", ["a", "b"], ["a", "b"], ["a"], True], ["*"]),
        ("a subset saves the subset", ["ok", ["a"], ["a", "b"], ["*"], True], ["a"]),
        ("none checked saves []", ["ok", [], ["a", "b"], ["*"], True], []),
    ]
    import json
    script = body + "\nconst C=" + json.dumps([c[1] for c in cases]) + \
             ";console.log(JSON.stringify(C.map(a=>resolveSavedSelection(...a))));"
    try:
        out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30).stdout)
        for (name, _, want), got in zip(cases, out):
            check(f"editor save: {name}", got == want, got)
    except Exception as e:  # noqa: BLE001
        check("editor save rule executed under node", False, e)

api = (ROOT / "static/js/api.js").read_text()
check("the panel toggle sends the panel's profile", "profile_id: state.currentResourcePanelProfileId" in api)
check("the editor's cached profiles refresh after a panel toggle",
      "uderia:profile-selection-changed" in api and "uderia:profile-selection-changed" in js)

print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
