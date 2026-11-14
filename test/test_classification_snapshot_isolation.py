#!/usr/bin/env python3
"""The persisted classification snapshot is the RECORD; APP_STATE is scratch.

WHY THIS GATE EXISTS
Operator report (2026-09-08): "@NIBRSO reads `changed` — tools removed:
TDA_Canvas, TDA_Charting, …", recurring on Optimize agents.

Measured on two live profiles bound to the SAME MCP server, classified two
seconds apart:

    @NIBRS  (llm_only + MCP)  63 tools, 0 prompts, 15 TDA_* persisted
    @NIBRSO (tool_enabled)    48 tools, 12 prompts, 0 TDA_* persisted
                              ------
                              63 - 48 = exactly 7 system + 8 component TDA_*

The adapter is explicit that these must never be stored:
    "CLIENT_SIDE_TOOLS (TDA_*) ... must not be sent to the LLM for
     classification or stored in classification_results."

ROOT CAUSE — reference aliasing through the config cache:
  1. `config_manager.load_config()` serves an in-memory cache, so
     `get_profile_classification()` returns a LIVE REFERENCE into the
     user's stored profile.
  2. `load_profile_classification_into_state` assigned those dicts straight
     onto APP_STATE (no copy).
  3. It then injected "System Tools" and "Component Tools" INTO that same
     object — mutating the persisted profile.
  4. The next save of that user's config (any `update_profile`, e.g. clearing
     `needs_reclassification`) wrote the runtime-only TDA_* tools to disk.

Because the DEPLOYMENT half of the trust envelope hashes this snapshot
(`trust.deployment.catalog_from_classification`), an agent then read
`changed` over a drift that was never a deployment change at all.

THE PROPERTY: loading a classification into APP_STATE must not mutate the
object it was loaded from — while the runtime injection itself is preserved.

Run: python3 test/test_classification_snapshot_isolation.py   (no server)
"""
import copy
import os
import re
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"\n        {detail}" if detail else ""))


# ── the persisted snapshot a real profile carries (MCP tools only) ───────────
def make_snapshot():
    return {
        "tools": {"All Tools": [
            {"name": "base_readQuery", "description": "", "arguments": [], "disabled": False},
            {"name": "base_tableList", "description": "", "arguments": [], "disabled": False},
        ]},
        "prompts": {"All Prompts": [
            {"name": "base_query", "description": "", "arguments": [], "disabled": False},
        ]},
        "resources": {},
        "tool_scopes": {"base_readQuery": "database"},
        "classified_with_mode": "light",
        "last_classified": "2026-09-08T14:18:25.165974+00:00",
    }


class FakeConfigManager:
    """Faithful in the ONE dimension under test: it hands back a LIVE REFERENCE
    into its own store, exactly as the real cache-backed manager does. A fake
    that returned a copy here would make the gate measure nothing."""

    def __init__(self, profile):
        self.profiles = {profile["id"]: profile}

    def get_profile(self, profile_id, user_uuid=None):
        return self.profiles.get(profile_id)

    def get_profile_classification(self, profile_id, user_uuid=None):
        p = self.profiles.get(profile_id)
        return p.get("classification_results", {}) if p else {}

    def get_master_classification_profile_id(self, *a, **k):
        return None


def run_load(profile):
    """Drive the REAL loader with its collaborators stubbed."""
    from trusted_data_agent.core import configuration_service as cs

    cm = FakeConfigManager(profile)
    saved = {
        "get_config_manager": cs.get_config_manager,
        "_regenerate_contexts": cs._regenerate_contexts,
    }
    cs.get_config_manager = lambda: cm
    cs._regenerate_contexts = lambda *a, **k: None

    # a component manager that reports two action tools, as a real instance does
    import trusted_data_agent.components.manager as cmgr
    saved_gcm = cmgr.get_component_manager

    class _Def:
        tool_definition = {"name": "TDA_Charting", "description": "", "args": {}}

    class _Def2:
        tool_definition = {"name": "TDA_Canvas", "description": "", "args": {}}

    class _CM:
        def get_action_tool_status(self, profile):
            return [(_Def(), False), (_Def2(), True)]

        # faithful to the real manager: the gated definition, via the REAL gating code
        def tool_definition_for(self, comp_def, profile_config=None):
            from trusted_data_agent.components.manager import ComponentManager
            return ComponentManager._gated_tool_definition(comp_def.tool_definition, profile_config)

    cmgr.get_component_manager = lambda: _CM()

    cs.APP_STATE.setdefault("structured_tools", {})
    try:
        ok = cs.load_profile_classification_into_state(profile["id"], "u-1")
        return ok, cs.APP_STATE
    finally:
        cs.get_config_manager = saved["get_config_manager"]
        cs._regenerate_contexts = saved["_regenerate_contexts"]
        cmgr.get_component_manager = saved_gcm


# ── 1 · the persisted snapshot is not mutated ────────────────────────────────
profile = {"id": "p-1", "tag": "NIBRS", "profile_type": "llm_only",
           "useMcpTools": True, "mcpServerId": "srv-1",
           "classification_mode": "light",
           "classification_results": make_snapshot()}
before = copy.deepcopy(profile["classification_results"])

ok, state = run_load(profile)
after = profile["classification_results"]

check("the loader reports cached results were loaded", ok is True, f"got {ok!r}")
check("the persisted snapshot is byte-identical after loading", after == before,
      f"added keys: {sorted(set(after.get('tools', {})) - set(before.get('tools', {})))}")
check("no 'System Tools' category leaks into the persisted snapshot",
      "System Tools" not in after.get("tools", {}))
check("no 'Component Tools' category leaks into the persisted snapshot",
      "Component Tools" not in after.get("tools", {}))

persisted_names = {t["name"] for cat in after.get("tools", {}).values() for t in cat}
check("no TDA_* tool is persisted in the snapshot",
      not any(n.startswith("TDA_") for n in persisted_names),
      f"persisted TDA_*: {sorted(n for n in persisted_names if n.startswith('TDA_'))}")
check("the snapshot still holds exactly its MCP tools",
      persisted_names == {"base_readQuery", "base_tableList"}, f"{sorted(persisted_names)}")

# ── 2 · the runtime injection is PRESERVED (no regression) ───────────────────
runtime_names = {t["name"] for cat in state["structured_tools"].values() for t in cat}
check("APP_STATE still receives the system tools (runtime unchanged)",
      "System Tools" in state["structured_tools"])
check("APP_STATE still receives the component tools (runtime unchanged)",
      "Component Tools" in state["structured_tools"])
check("APP_STATE holds the MCP tools too",
      {"base_readQuery", "base_tableList"} <= runtime_names)
check("APP_STATE holds TDA_* tools at runtime",
      any(n.startswith("TDA_") for n in runtime_names))
check("the per-profile component `disabled` flag survives the copy",
      any(t.get("name") == "TDA_Canvas" and t.get("disabled") is True
          for t in state["structured_tools"].get("Component Tools", [])))

# ── 3 · APP_STATE and the record are DIFFERENT objects ───────────────────────
check("structured_tools is not the persisted object",
      state["structured_tools"] is not after.get("tools"))
check("structured_prompts is not the persisted object",
      state["structured_prompts"] is not after.get("prompts"))
check("structured_resources is not the persisted object",
      state["structured_resources"] is not after.get("resources"))
check("tool_scopes is not the persisted object",
      state["tool_scopes"] is not after.get("tool_scopes"))

# a nested mutation must not reach the record either (deep, not shallow, copy)
state["structured_tools"]["All Tools"][0]["disabled"] = True
check("a nested runtime mutation does not reach the record",
      after["tools"]["All Tools"][0]["disabled"] is False)

# ── 4 · structural: the aliasing site copies ─────────────────────────────────
SRC = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                   "core", "configuration_service.py")
src = open(SRC, encoding="utf-8").read()
for key in ("structured_tools", "structured_prompts", "structured_resources", "tool_scopes"):
    m = re.search(r"APP_STATE\['" + key + r"'\]\s*=\s*(.+)", src)
    check(f"APP_STATE['{key}'] is assigned a copy, never the stored object",
          bool(m) and "deepcopy" in m.group(1), m.group(1)[:80] if m else "not found")

# ── 5 · the adapter still refuses to store TDA_* at the source ───────────────
ADP = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                   "mcp_adapter", "adapter.py")
adp = open(ADP, encoding="utf-8").read()
check("the adapter derives the client-side exclusion set",
      "client_side_names = {t['name'] for t in CLIENT_SIDE_TOOLS}" in adp)
# The property, executed (the literal it used to grep moved into the per-server helper that the
# primary AND every additional server share — PROP-006): a client-side tool handed to the
# snapshot builder never reaches the snapshot, and the primary hands it the exclusion set.
try:
    from trusted_data_agent.mcp_adapter.adapter import _structure_capabilities, _SimpleTool
    _st, _, _ = _structure_capabilities(
        [_SimpleTool(name="base_readQuery", description="", args={}),
         _SimpleTool(name="TDA_FinalReport", description="", args={})],
        [], [], {}, True, [], [], {}, exclude={"TDA_FinalReport"})
    _names = [t["name"] for grp in _st.values() for t in grp]
    check("the adapter skips client-side tools when building the snapshot",
          _names == ["base_readQuery"], _names)
except Exception as _e:  # noqa: BLE001
    check("the adapter skips client-side tools when building the snapshot", False, repr(_e))
check("the primary server hands the snapshot builder the client-side exclusion set",
      "exclude=client_side_names" in adp)

# ── 6 · the PERSISTENCE chokepoint strips the injected categories ───────────
# The second shape of the same leak, found on production AFTER the deepcopy fix:
# the adapter persists `STATE['structured_tools']` AS-IS, and APP_STATE is
# process-global — so when a classification short-circuits ("Forcing light
# classification mode"), whatever a PREVIOUS profile's activation injected is still
# there and is written into THIS profile's record. A per-caller guard would have
# caught one shape and not the other; the invariant belongs at the one chokepoint.
from trusted_data_agent.core.config_manager import ConfigManager  # noqa: E402

cm = ConfigManager.__new__(ConfigManager)
_stored = {}
_target = {"id": "p-9", "tag": "IDGGT", "classification_mode": "light"}
cm.get_profiles = lambda uu=None: [_target]
cm.save_profiles = lambda profiles, uu=None: _stored.update({"profiles": profiles}) or True

# exactly what the adapter hands over when APP_STATE still carries an injection
app_state_shaped = {
    "tools": {
        "All Tools": [{"name": "base_readQuery", "description": "", "arguments": []}],
        "System Tools": [{"name": "TDA_FinalReport", "description": "", "arguments": []}],
        "Component Tools": [{"name": "TDA_Charting", "description": "", "arguments": []}],
    },
    "prompts": {"All Prompts": [{"name": "base_query", "description": "", "arguments": []}]},
    "resources": {},
    "tool_scopes": {},
    "classified_with_mode": "light",
}
handed_in = copy.deepcopy(app_state_shaped)
ok = cm.save_profile_classification("p-9", app_state_shaped, "u-1")
saved = _target.get("classification_results", {})

check("the save reports success", ok is True)
check("'System Tools' never reaches the record",
      "System Tools" not in saved.get("tools", {}))
check("'Component Tools' never reaches the record",
      "Component Tools" not in saved.get("tools", {}))
check("the genuine MCP category is preserved",
      list(saved.get("tools", {})) == ["All Tools"], f"{list(saved.get('tools', {}))}")
check("no TDA_* tool is persisted by this path",
      not any(t["name"].startswith("TDA_")
              for cat in saved.get("tools", {}).values() for t in cat))
check("prompts are untouched", saved.get("prompts") == app_state_shaped["prompts"])
check("the caller's dict is NOT mutated (APP_STATE stays usable)",
      app_state_shaped == handed_in,
      "the runtime's own structures were modified")
check("a real classification still stamps a time",
      saved.get("last_classified") is not None)

# a snapshot that held ONLY injected categories is not a classification at all
_target.pop("classification_results", None)
cm.save_profile_classification("p-9", {
    "tools": {"System Tools": [{"name": "TDA_FinalReport"}],
              "Component Tools": [{"name": "TDA_Charting"}]},
    "prompts": {}, "resources": {}, "tool_scopes": {}}, "u-1")
saved2 = _target.get("classification_results", {})
check("an injection-only snapshot persists no tools", saved2.get("tools") == {})
check("...and keeps no timestamp (nothing was actually classified)",
      saved2.get("last_classified") is None)

# structural: the strip runs at the chokepoint, before the record is written
cm_src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                           "core", "config_manager.py"), encoding="utf-8").read()
i = cm_src.index("def save_profile_classification")
body = cm_src[i:i + 3000]
check("the chokepoint strips before storing",
      "_without_runtime_injected" in body
      and body.index("_without_runtime_injected") < body.index('profile["classification_results"]'))
check("the injected category names are declared once (module level, ONE literal)",
      cm_src.count('RUNTIME_INJECTED_CATEGORIES = ("System Tools", "Component Tools")') == 1
      and "_RUNTIME_INJECTED_CATEGORIES = RUNTIME_INJECTED_CATEGORIES" in cm_src)

# ── 7 · the GETTER hands out a copy — no reader can mutate the record ──────
# The third shape of the same leak (found live 2026-09-22): the resource-panel
# endpoint `GET /v1/profiles/<id>/resources` read the snapshot through
# `get_profile_classification`, wrote "System Tools" / "Component Tools" into
# the dict it got back — and REPLACED "Component Tools" with only the tools it
# found missing, so a record went from the 8 pre-S3 components to
# `[TDA_Compute]` — and the next profile save persisted it. Two readers fixed
# one at a time is a per-caller guard; the invariant belongs on the getter.
_rec = {"tools": {"All Tools": [{"name": "base_readQuery", "arguments": [{"name": "sql"}]}]},
        "prompts": {}, "last_classified": "2026-09-01T00:00:00Z"}
_target2 = {"id": "p-10", "classification_results": _rec}
cm.get_profiles = lambda uu=None: [_target2]
got = cm.get_profile_classification("p-10", "u-1")
check("the getter returns an EQUAL snapshot", got == _rec)
check("...that is NOT the stored object", got is not _rec)
got["tools"]["Component Tools"] = [{"name": "TDA_Compute"}]
got["tools"]["All Tools"].append({"name": "TDA_Charting"})
check("writing into what the getter returned does not reach the record (top level)",
      "Component Tools" not in _rec["tools"])
check("...nor nested (deep copy, not shallow)",
      [t["name"] for t in _rec["tools"]["All Tools"]] == ["base_readQuery"])
check("a missing profile still yields an empty dict",
      cm.get_profile_classification("nope", "u-1") == {})

# ── 8 · every injector's category literal is the ONE declared set ───────────
# A new injection site that invents a third category name would be persisted by
# the saver (it strips only the declared set) AND hashed by the trust reader.
_root = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent")
_inj_srcs = {
    "configuration_service.py": open(os.path.join(_root, "core", "configuration_service.py"), encoding="utf-8").read(),
    "rest_routes.py": open(os.path.join(_root, "api", "rest_routes.py"), encoding="utf-8").read(),
}
_declared = {"System Tools", "Component Tools"}
for _fn, _src in _inj_srcs.items():
    # both spellings: APP_STATE['structured_tools']["X Tools"] = … and structured_tools["X Tools"] = …
    _written = set(re.findall(r"""structured_tools['"]?\]?\["([A-Za-z ]+ Tools)"\] *=""", _src))
    _written = {w for w in _written if w.endswith(" Tools")}
    check(f"{_fn}: every category the injector writes is in the declared set",
          _written and _written <= _declared, f"writes {_written!r}")

# the resource-panel endpoint works on a COPY of the stored snapshot
_rr = _inj_srcs["rest_routes.py"]
_i = _rr.index("async def get_profile_resources")
_body = _rr[_i:_i + 20000]
check("get_profile_resources copies the stored snapshot before enriching it",
      "structured_tools = copy.deepcopy(classification_results.get('tools', {}))" in _body)
check("...and MERGES the component category instead of replacing it",
      'list(structured_tools.get("Component Tools") or []) + _comp_category' in _body)

# ── 9 · the trust reader ignores the injected categories ────────────────────
# A record written before the strip (or mutated by a reader before the getter
# copied) still carries them; hashing them made a platform release ("TDA_Compute
# appeared") read as deployment drift on 10 of 57 production attestations.
from trusted_data_agent.core.trust import deployment as D  # noqa: E402
_clean = {"mcpServerId": "s1", "classification_results": {
    "tools": {"All Tools": [{"name": "base_readQuery", "arguments": [{"name": "sql"}]}]},
    "prompts": {}, "last_classified": "2026-09-01T00:00:00Z"}}
_polluted = copy.deepcopy(_clean)
_polluted["classification_results"]["tools"]["System Tools"] = [
    {"name": "TDA_FinalReport", "arguments": []}]
_polluted["classification_results"]["tools"]["Component Tools"] = [
    {"name": "TDA_Compute", "arguments": [{"name": "op"}]}]
check("the deployment catalog of a polluted record equals the clean one",
      D.catalog_from_classification(_polluted) == D.catalog_from_classification(_clean))
check("...so their deployment hashes agree",
      D.deployment_hash(D.build_deployment_signature(None, D.catalog_from_classification(_polluted), []))
      == D.deployment_hash(D.build_deployment_signature(None, D.catalog_from_classification(_clean), [])))
_p2 = copy.deepcopy(_polluted)
_p2["classification_results"]["tools"]["Component Tools"] = [
    {"name": n, "arguments": []} for n in ("TDA_Canvas", "TDA_Charting", "TDA_Dashboard")]
check("a platform release that swaps the component set is NO drift",
      D.describe_drift(D.build_deployment_signature(None, D.catalog_from_classification(_polluted), []),
                       D.build_deployment_signature(None, D.catalog_from_classification(_p2), [])) == [])
_mcp_tda = copy.deepcopy(_clean)
_mcp_tda["classification_results"]["tools"]["All Tools"].append(
    {"name": "TDA_something", "arguments": []})
check("an MCP tool that merely LOOKS platform-named stays in the surface (no name heuristic)",
      "TDA_something()" in D.catalog_from_classification(_mcp_tda)["tools"])
_only_inj = {"mcpServerId": "s1", "classification_results": {
    "tools": {"System Tools": [{"name": "TDA_FinalReport", "arguments": []}]},
    "prompts": {}, "last_classified": "2026-09-01T00:00:00Z"}}
check("a record holding ONLY injected categories is an empty catalog, never unresolved",
      D.catalog_from_classification(_only_inj) == {"tools": [], "prompts": []})
check("the reader takes the category set from the ONE declaration",
      "from trusted_data_agent.core.config_manager import RUNTIME_INJECTED_CATEGORIES"
      in open(os.path.join(_root, "core", "trust", "deployment.py"), encoding="utf-8").read())

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — the classification snapshot is isolated from runtime injection"))
sys.exit(1 if FAIL else 0)
