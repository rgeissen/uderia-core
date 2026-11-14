"""A declared prompt parameter with no value never erases a runtime placeholder. CI-pure.

FOUND (2026-10-29, the Optimize lane's captured strategic prompts): on every governed
Optimize agent the strategic planner's "### Available Capabilities" read `[]` — the planner
planned without its tool catalog. Root cause: databases bootstrapped before 2026-06-04 carry
`prompt_parameters(WORKFLOW_META_PLANNING_PROMPT, available_tools, json, default '[]')`; the
loader substituted every declared parameter's default, so `{available_tools}` became `[]`
BEFORE the planner's `.format(available_tools=…)`. Commit 74cb89ad removed the row from the
seed, which only a NEW database reads. The same rule blanked three more placeholders the code
fills at runtime: the coordinator's `{available_profiles}` (its system prompt listed no
experts), the classifier's `{task_description}` (it classified an empty task), and
`{error_message}`. Fix at the rule, not the rows: a parameter with no value (an empty default,
no override) leaves its placeholder for the caller; an explicit override always substitutes.
"""
import os, pathlib, sqlite3, sys, tempfile, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)
from trusted_data_agent.agent import prompt_loader as PL

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
ROOT = pathlib.Path(__file__).resolve().parents[1]

print("\n── §1 what counts as no value")
check("None, '', '[]', '{}' (and whitespace around them) are unset", all(PL._is_unset_parameter(v) for v in (None, "", "[]", "{}", "  ", " [] ")))
check("a real value is set — including 0, false and a non-empty list", not any(PL._is_unset_parameter(v) for v in ("base_readQuery", "0", "false", "[1]", "Teradata MCP")))

print("\n── §2 the loader's parameter resolution (scratch database, the rows a pre-June database carries)")
db = os.path.join(tempfile.mkdtemp(), "tda_auth.db")
c = sqlite3.connect(db)
c.executescript("""
CREATE TABLE prompts (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE global_parameters (parameter_name TEXT, default_value TEXT);
CREATE TABLE prompt_parameters (prompt_id INTEGER, parameter_name TEXT, default_value TEXT);
CREATE TABLE global_parameter_overrides (prompt_id INTEGER, parameter_name TEXT, override_value TEXT, user_uuid TEXT, profile_id TEXT);
INSERT INTO prompts VALUES (1, 'WORKFLOW_META_PLANNING_PROMPT'), (2, 'GENIE_COORDINATOR_PROMPT'), (3, 'TASK_CLASSIFICATION_PROMPT');
INSERT INTO global_parameters VALUES ('mcp_system_name', 'Teradata MCP'), ('empty_global', '');
INSERT INTO prompt_parameters VALUES
  (1, 'available_tools', '[]'), (1, 'task', ''), (1, 'mcpTool_listTables', 'base_tableList'),
  (2, 'available_profiles', ''), (3, 'task_description', '');
INSERT INTO global_parameter_overrides VALUES (1, 'task', 'pinned by the profile', NULL, 'prof-1');
""")
c.commit(); c.close()
L = PL.PromptLoader.__new__(PL.PromptLoader)
L.db_path = db
L._prompt_cache = PL._TTLCache(max_size=50, ttl=60)
L._parameter_cache = PL._TTLCache(max_size=50, ttl=60)
p1 = L.get_prompt_parameters("WORKFLOW_META_PLANNING_PROMPT")
check("the stale `available_tools` row (json '[]') is not a value: it is not offered for substitution", "available_tools" not in p1, sorted(p1))
check("a configured parameter still is (mcpTool_listTables → base_tableList)", p1.get("mcpTool_listTables") == "base_tableList")
check("a global with a value still is; a global with none is not", p1.get("mcp_system_name") == "Teradata MCP" and "empty_global" not in p1)
check("an explicit override always substitutes, even over an empty default", L.get_prompt_parameters("WORKFLOW_META_PLANNING_PROMPT", profile_id="prof-1").get("task") == "pinned by the profile")
check("the coordinator's and the classifier's runtime inputs are not offered either",
      "available_profiles" not in L.get_prompt_parameters("GENIE_COORDINATOR_PROMPT") and "task_description" not in L.get_prompt_parameters("TASK_CLASSIFICATION_PROMPT"))

print("\n── §3 the placeholders survive to the caller, who fills them")
tpl = "### Available Capabilities\n{available_tools}\n\nUse {mcpTool_listTables} to list tables. Goal: {workflow_goal}"
out = L._resolve_parameters(tpl, p1)
check("the strategic template keeps {available_tools} and resolves the configured tool name",
      "{available_tools}" in out and "base_tableList" in out and "{mcpTool_listTables}" not in out)
check("…so the planner's format fills in the real catalog", "--- Available Tools ---" in out.replace("{workflow_goal}", "").format(available_tools="--- Available Tools ---"))
coord = L._resolve_parameters("Experts:\n{available_profiles}\n", L.get_prompt_parameters("GENIE_COORDINATOR_PROMPT"))
check("the coordinator's replace() finds its placeholder", coord.replace("{available_profiles}", "@TCOPT, @TCFOC") == "Experts:\n@TCOPT, @TCFOC\n")
cls = L._resolve_parameters("Classify: {task_description}", L.get_prompt_parameters("TASK_CLASSIFICATION_PROMPT"))
check("the classifier's format fills the task", cls.format(task_description="sum the sales") == "Classify: sum the sales")

print("\n── §4 the callers still fill these placeholders (if one stops, this rule is moot — re-check)")
planner = (ROOT / "src/trusted_data_agent/agent/planner.py").read_text()
genie = (ROOT / "src/trusted_data_agent/agent/genie_coordinator.py").read_text()
check("the planner fills available_tools at format time", '"available_tools": tools_context' in planner and "WORKFLOW_META_PLANNING_PROMPT.format(**_tv)" in planner)
check("the classifier is filled with format(task_description=…)", "format(task_description=task_description)" in planner)
check("the coordinator replaces {available_profiles}", 'system_prompt.replace("{available_profiles}", tool_descriptions)' in genie)
loader = (ROOT / "src/trusted_data_agent/agent/prompt_loader.py").read_text()
check("the rule runs before the overrides step (an override always wins)",
      loader.index("_is_unset_parameter(v)") < loader.index("# 3. Load parameter overrides"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
