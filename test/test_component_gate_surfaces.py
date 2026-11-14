#!/usr/bin/env python3
"""Every place a component's tool text reaches a model resolves its [[gate:x]] blocks.

WHY THIS GATE EXISTS
Found 2026-09-24 while designing the Database Navigator `query` action: the
[[gate:obda]] convention was honoured by ONE of the three surfaces that hand a
component's tool definition to a model. `get_tool_definitions` (the planner
catalog) resolved the gates; `get_langchain_tools` (the ReAct lane — Ideate +
tools, Focus + tools, every Coordinate expert of those classes) and the
Optimize tools context (`configuration_service` → `structured_tools`) read
`comp_def.tool_definition` raw. With OBDA-READ OFF, the Knowledge Graph tool
therefore reached those lanes with the literal `[[gate:obda]]` markup AND the
data-product capability text — advertising a door that refuses, the exact
defect the gates exist to prevent.

The rule: every surface reads the tool definition through
`ComponentManager.tool_definition_for(comp, profile_config)`. This gate
asserts the surfaces AGREE, that no gate markup ever leaks, that a closed
gate's content is absent and an open gate's content present, and that the
profile reaches the gate resolver (per-agent gates depend on it).

Run: python3 test/test_component_gate_surfaces.py     (no server, no network)
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


import logging  # noqa: E402
logging.disable(logging.CRITICAL)

from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.components.manager import ComponentManager, get_component_manager  # noqa: E402

cm = get_component_manager()
KG = "TDA_KnowledgeGraph"
READ = "TDA_DataProduct"          # the OBDA-READ component's own tool (the product face)
MARKUP = "[[gate:"

# the OBDA-READ component is offered only to an agent with a graph bound — this test's
# profile has none, so the binding reads as present (the relevance rule itself is gated
# in test_obda_components)
from trusted_data_agent.core import obda_planes as _planes  # noqa: E402
_planes.profile_has_graph = lambda *a, **k: True


def profile_all_on():
    """Every action component enabled, so every tool definition is exercised."""
    cfg = {cid: {"enabled": True} for cid, c in cm.components.items()
           if c.tool_definition and c.component_type == "action"}
    return {"id": "gate-test-profile", "profile_type": "llm_only", "useMcpTools": True, "componentConfig": cfg}


def surfaces(profile):
    """→ {tool_name: {surface: text}} for the three surfaces a model reads."""
    out = {}
    for t in cm.get_tool_definitions(profile):
        out.setdefault(t["name"], {})["catalog"] = json.dumps(t, sort_keys=True)
    for t in cm.get_langchain_tools(profile, {"user_uuid": "gate-test"}):
        schema = json.dumps(t.args_schema.model_json_schema(), sort_keys=True)
        out.setdefault(t.name, {})["react_description"] = t.description
        out[t.name]["react_schema"] = schema
    # the REAL builder of the Optimize tools context (configuration_service), not a copy
    from trusted_data_agent.core.configuration_service import component_tools_category
    for entry in component_tools_category(cm, profile):
        if entry["disabled"]:
            continue
        out.setdefault(entry["name"], {})["optimize_context"] = json.dumps(entry, sort_keys=True)
    return out


prof = profile_all_on()
saved = _OP.plane_available("read")
try:
    # ── [1] OBDA-READ off for the agent (its component toggle): no markup anywhere, no
    # data-product capability anywhere — availability is the component chain's, never a switch
    prof_off = dict(prof, componentConfig=dict(prof["componentConfig"], obda_read={"enabled": False}))
    s_off = surfaces(prof_off)
    check("[1] the KG tool is present on every surface",
          set(s_off.get(KG, {})) >= {"catalog", "react_description", "react_schema", "optimize_context"},
          str(sorted(s_off.get(KG, {}))))
    for tool, by in s_off.items():
        for surf, text in by.items():
            check(f"[1] no gate markup · {tool} · {surf}", MARKUP not in text)
    for surf, text in s_off.get(KG, {}).items():
        check(f"[1] closed gate's content absent · KG · {surf}", "query_product" not in text)
    check("[1] OBDA-READ off: the product tool is on no surface", READ not in s_off, str(sorted(s_off)))

    # ── [2] OBDA-READ on: content present, still no markup
    _OP.force("read", True)
    s_on = surfaces(prof)
    for tool, by in s_on.items():
        for surf, text in by.items():
            check(f"[2] no gate markup · {tool} · {surf}", MARKUP not in text)
    # the product content is the OBDA-READ component's own: its tool carries it on every
    # surface, the knowledge-graph tool carries it on none (the actions stay unadvertised aliases)
    check("[2] OBDA-READ on: the product tool is present on every surface",
          set(s_on.get(READ, {})) >= {"catalog", "react_description", "react_schema", "optimize_context"},
          str(sorted(s_on.get(READ, {}))))
    for surf, text in s_on.get(READ, {}).items():
        check(f"[2] open gate's content present · {READ} · {surf}", "query_product" in text)
    for surf, text in s_on.get(KG, {}).items():
        check(f"[2] the KG tool sells no product on any surface · {surf}", "query_product" not in text)

    # ── [3] the surfaces agree: ReAct and the catalog show the same description
    for tool, by in s_on.items():
        if "catalog" in by and "react_description" in by:
            check(f"[3] ReAct description == catalog description · {tool}",
                  json.loads(by["catalog"]).get("description", "") == by["react_description"])
finally:
    _OP.force("read", saved)

# ── [4] the profile reaches the gate resolver (per-agent gates depend on it)
seen = []
orig = ComponentManager._instruction_gate_open
try:
    ComponentManager._instruction_gate_open = staticmethod(lambda name, pc=None: seen.append((name, pc)) or True)
    marker_prof = {"marker": 1}
    ComponentManager.apply_instruction_gates("a [[gate:obda]]b[[/gate]] c", marker_prof)
    check("[4] apply_instruction_gates hands the profile to the resolver", seen and seen[-1][1] is marker_prof, str(seen))
    seen.clear()
    ComponentManager._gated_tool_definition({"name": "X", "description": "[[gate:obda]]d[[/gate]]",
                                             "args": {"a": {"description": "[[gate:obda]]e[[/gate]]"}}}, marker_prof)
    check("[4] tool description AND arg descriptions resolve with the profile",
          len(seen) == 2 and all(p is marker_prof for _, p in seen), str(seen))
finally:
    ComponentManager._instruction_gate_open = orig

# ── [5] text without markup is returned untouched (byte-identical baseline)
plain = {"name": "X", "description": "plain", "args": {"a": {"description": "p"}}}
check("[5] a definition without gates is returned as the same object",
      ComponentManager._gated_tool_definition(plain, {"any": 1}) is plain)
check("[5] an unknown gate keeps its content (fail-visible)",
      ComponentManager.apply_instruction_gates("[[gate:no_such_gate]]kept[[/gate]]") == "kept")


# ── [6] structural: no surface reads the raw tool definition for model text
def fn_src(path, name):
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(open(path).read(), node)
    return ""


mgr = os.path.join(ROOT, "src/trusted_data_agent/components/manager.py")
cfgs = os.path.join(ROOT, "src/trusted_data_agent/core/configuration_service.py")
lc = fn_src(mgr, "get_langchain_tools")
check("[6] get_langchain_tools builds tool_def through tool_definition_for",
      re.search(r"tool_def\s*=\s*self\.tool_definition_for\(", lc) is not None)
check("[6] get_langchain_tools never assigns the raw definition",
      re.search(r"tool_def\s*=\s*comp\.tool_definition\b", lc) is None)
cs = open(cfgs).read()
check("[6] the Optimize tools context is built by component_tools_category",
      "component_tools_category(comp_manager, activating_profile" in cs
      and re.search(r"tool_def\s*=\s*comp_def\.tool_definition\b", cs) is None)
gtd = fn_src(mgr, "get_tool_definitions")
check("[6] the planner catalog reads through tool_definition_for",
      "self.tool_definition_for(comp, profile_config)" in gtd)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — every model-facing surface resolves component gates"))
sys.exit(1 if FAIL else 0)
