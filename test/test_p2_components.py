"""
Rigid module test for the P2 "minimal exposure" refactor.

Run per-component after each edit:
    PYTHONPATH=src python3 test/test_p2_components.py <component>
Or the whole suite:
    PYTHONPATH=src python3 test/test_p2_components.py

It exercises the REAL component-loading path (ComponentManager) plus on-disk
JSON, and enforces three things for every action component:

  1. CONTRACT PRESERVED  -- P2 must NOT change the correctness contract:
     the manifest tool_definition's REQUIRED args and the full ARG set are
     frozen against a golden snapshot (no arg removed/renamed/required-flipped).
  2. STRUCTURE + SUBSTITUTION -- instructions.json parses, has none/medium/heavy,
     medium & heavy are non-empty, and every manifest placeholder substitution
     resolves (no unresolved "{PLACEHOLDER}" leaks into the assembled text).
  3. P2 INTENT -- the specific heuristics the plan moves OUT are absent, the
     correctness lines/additions the plan keeps/adds are present, on the exact
     intensity level (medium/heavy/manifest) where they belong.
  4. VALIDATION SMOKE -- handlers that override validate_arguments still accept a
     minimal valid call and still reject a call missing a required arg.

A component section starts RED (pre-edit) and must go GREEN after its edit.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.components.manager import get_component_manager  # noqa: E402
from trusted_data_agent.components.base import BaseComponentHandler  # noqa: E402

ACTION_COMPONENTS = [
    "canvas", "chart", "dashboard", "flow",
    "knowledge_graph", "memory", "scheduler",
]

# ---- 1. Frozen correctness contract (captured pre-refactor) --------------
GOLDEN = {
    "canvas":          {"name": "TDA_Canvas",        "required": [],                                "all": ["action", "canvas_id", "content", "language", "sources", "title", "version_id"]},
    "chart":           {"name": "TDA_Charting",      "required": ["chart_type", "data", "mapping", "title"], "all": ["chart_type", "data", "mapping", "row_limit", "sort_by", "sort_direction", "title"]},
    "dashboard":       {"name": "TDA_Dashboard",     "required": ["action"],                        "all": ["action", "chart_type", "content", "dashboard_id", "description", "name", "query", "tile_height", "tile_id", "tile_title", "tile_width"]},
    "flow":            {"name": "TDA_Flow",          "required": ["action"],                        "all": ["action", "after_node_id", "description", "edge", "edge_id", "flow_id", "global_params", "input_params", "name", "node", "node_id", "node_updates", "run_id", "scope", "scope_id", "scope_updates"]},
    "knowledge_graph": {"name": "TDA_KnowledgeGraph","required": ["action"],                        "all": ["action", "depth", "entity_name", "entity_type", "label", "properties", "query_text", "relationship_type", "target_entity", "target_entity_type", "title", "version_id"]},
    "memory":          {"name": "TDA_Memory",        "required": ["action"],                        "all": ["action", "content", "kind", "query", "scope"]},
    "scheduler":       {"name": "TDA_Scheduler",     "required": ["action"],                        "all": ["action", "max_tokens_per_run", "name", "output_channel", "output_config", "overlap_policy", "prompt", "schedule", "session_context", "task_id"]},
}

# ---- 3. P2 intent assertions --------------------------------------------
# Each entry: (label, predicate(ctx) -> bool). ctx exposes:
#   ctx["medium"], ctx["heavy"]            -> RESOLVED instruction text (lowercased helper too)
#   ctx["raw"]                              -> raw instructions.json dict
#   ctx["manifest"]                        -> manifest.json dict
#   ctx["manifest_args"]                   -> flattened "<arg>: <description>" text
#   ctx["tactical"]                        -> manifest tactical_prompt_rule (or "")
def _has(text, *subs):  # all present (case-insensitive)
    t = text.lower()
    return all(s.lower() in t for s in subs)

def _absent(text, *subs):  # none present (case-insensitive)
    t = text.lower()
    return not any(s.lower() in t for s in subs)

INTENT = {
    "chart": [
        ("MOVED: 'NEXT AND ONLY' workflow opinion",         lambda c: _absent(c["medium"] + c["heavy"], "next and only", "actively look")),
        ("MOVED: 'prefer visual over table' opinion",       lambda c: _absent(c["medium"] + c["heavy"], "prefer visual", "easily readable in a table")),
        ("KEPT: exact results-array data contract",         lambda c: _has(c["medium"], "results") and _has(c["heavy"], "results")),
        ("KEPT: subset sort_by/row_limit guidance",         lambda c: _has(c["medium"], "row_limit")),
        ("KEPT: guidelines injection (strategic path)",     lambda c: "{G2PLOT_GUIDELINES}" in c["raw"]["medium"]),
        ("PRESERVED: tactical_prompt_rule (tactical path)", lambda c: _has(c["tactical"], "angle", "color", "x_axis")),
    ],
    "canvas": [
        ("ADDED: mermaid in supported languages",           lambda c: _has(c["medium"], "mermaid") and _has(c["heavy"], "mermaid")),
        ("ADDED: version_id documented in args list",       lambda c: _has(c["medium"], "version_id")),
        ("MOVED: 'users strongly prefer' opinion",          lambda c: _absent(c["heavy"], "users strongly prefer")),
        ("MOVED: when/not-to-use decision lists",           lambda c: _absent(c["medium"], "do not use tda_canvas for")),
        ("KEPT: complete-content hard constraint",          lambda c: _has(c["medium"], "complete") and _has(c["heavy"], "complete")),
    ],
    "dashboard": [
        ("MOVED: tile-sizing grid out of manifest args",    lambda c: _absent(c["manifest_args"], "use 12 for tables", "use 2 for kpis", "3-4 for kpis", "3–4 for kpis")),
        ("KEPT: manifest still states default sizes",       lambda c: _has(c["manifest_args"], "default 6", "default 4")),
        ("ADDED: refresh in heavy action list",             lambda c: _has(c["heavy"], "refresh")),
        ("KEPT: query is NL not SQL",                       lambda c: _has(c["medium"], "not sql") or _has(c["medium"], "natural language")),
        ("KEPT: don't add_tile without dashboard_id",       lambda c: _has(c["heavy"], "dashboard_id")),
    ],
    "flow": [
        ("MOVED: reference-syntax tutorial",                lambda c: _absent(c["heavy"], "referencing a previous node's value")),
        ("MOVED: 'proactively suggest flows' opinion",      lambda c: _absent(c["heavy"], "proactively suggest flows")),
        ("KEPT: insert_node shorthand contract",            lambda c: _has(c["medium"], "insert_node")),
        ("KEPT: profile_id is a TAG not raw id",            lambda c: _has(c["medium"] + c["heavy"], "tag")),
        ("KEPT: open != get correctness trap",              lambda c: _has(c["medium"], "open") and _has(c["medium"], "get")),
        # plan goal: flow heavy ~40-50% smaller than the 6138-char pre-refactor original
        ("SHRUNK: heavy >=40% smaller than 6138 original",  lambda c: len(c["heavy"]) <= int(6138 * 0.60)),
    ],
    "knowledge_graph": [
        ("MOVED: 'proactively offer to enrich' opinion",    lambda c: _absent(c["heavy"], "proactively offer to enrich")),
        ("KEPT: authoritative enrichment contract",         lambda c: _has(c["medium"], "authoritative") and _has(c["heavy"], "authoritative")),
        ("KEPT: graceful degradation",                      lambda c: _has(c["medium"], "absent") or _has(c["medium"], "supportive")),
        ("KEPT: compile_metric slice judgment",             lambda c: _has(c["medium"], "compile_metric")),
    ],
    "memory": [
        ("MOVED: 'Got it' phrasing script",                lambda c: _absent(c["heavy"], "got it")),
        ("KEPT: scope=global rule",                         lambda c: _has(c["medium"], 'scope="global"') and _has(c["heavy"], 'scope="global"')),
        ("KEPT: never store transient data",               lambda c: _has(c["medium"], "never store")),
    ],
    "scheduler": [
        ("ADDED: UTC note",                                lambda c: _has(c["medium"], "utc") and _has(c["heavy"], "utc")),
        ("MOVED: 'always ask' session_context coaching",    lambda c: _absent(c["medium"] + c["heavy"], "always ask the user which", "always clarify")),
        ("MOVED: proactive-scheduling suggestion script",   lambda c: _absent(c["heavy"], "proactively suggest scheduling", "want me to schedule this")),
        ("KEPT: cron + interval format examples",           lambda c: _has(c["medium"], "0 9 * * *", "interval:30m")),
        ("KEPT: schedule-a-flow correct-usage line",        lambda c: _has(c["medium"], "execute flow")),
    ],
}

# ---- 4. Validation smoke (only handlers that override validate_arguments) -
VALID_SMOKE = {
    "canvas": [(True, {"action": "create", "content": "x"}), (True, {"content": "x"})],
    "chart":  [(True, {"chart_type": "bar", "data": [{"a": 1}], "title": "t", "mapping": {"x_axis": "a", "y_axis": "a"}}),
               (False, {"chart_type": "bar", "data": [], "title": "t"})],  # empty data -> handler rejects
    "memory": [(True, {"action": "remember", "content": "I like X"}), (False, {"action": "remember"})],
    "scheduler": [(True, {"action": "list"}), (False, {"action": "create", "name": "x"})],  # missing schedule/prompt
    "knowledge_graph": [(True, {"action": "compile_metric", "metric_name": "m"}), (False, {"action": "compile_metric"})],
}


def check_component(cid):
    failures = []
    mgr = get_component_manager()
    comps = mgr.components
    comp = comps.get(cid)
    cdir = ROOT / "components" / "builtin" / cid

    if comp is None:
        return [f"{cid}: component not loaded by ComponentManager"]

    # JSON parse
    try:
        raw = json.loads((cdir / "instructions.json").read_text())
    except Exception as e:
        return [f"{cid}: instructions.json invalid JSON: {e}"]
    try:
        manifest = json.loads((cdir / "manifest.json").read_text())
    except Exception as e:
        return [f"{cid}: manifest.json invalid JSON: {e}"]

    # Structure
    for k in ("none", "medium", "heavy"):
        if k not in raw:
            failures.append(f"{cid}: instructions.json missing key '{k}'")
    if not raw.get("medium", "").strip():
        failures.append(f"{cid}: medium instructions empty")
    if not raw.get("heavy", "").strip():
        failures.append(f"{cid}: heavy instructions empty")

    medium = comp.get_instructions("medium")
    heavy = comp.get_instructions("heavy")

    # Substitution resolution (no unresolved placeholder leaks)
    subs = (manifest.get("instructions", {}) or {}).get("placeholder_substitutions", {})
    for ph in subs:
        if ph in medium or ph in heavy:
            failures.append(f"{cid}: unresolved placeholder {ph} in assembled text")

    # Contract preserved
    g = GOLDEN[cid]
    td = manifest.get("tool_definition", {})
    args = td.get("args", {})
    if td.get("name") != g["name"]:
        failures.append(f"{cid}: tool name changed {td.get('name')} != {g['name']}")
    req = sorted([k for k, v in args.items() if v.get("required")])
    if req != sorted(g["required"]):
        failures.append(f"{cid}: REQUIRED args changed {req} != {sorted(g['required'])}")
    missing = set(g["all"]) - set(args.keys())
    if missing:
        failures.append(f"{cid}: args removed from contract: {sorted(missing)}")

    # P2 intent
    manifest_args = "\n".join(f"{k}: {v.get('description','')}" for k, v in args.items())
    ctx = {
        "medium": medium, "heavy": heavy, "raw": raw, "manifest": manifest,
        "manifest_args": manifest_args, "tactical": manifest.get("tactical_prompt_rule", "") or "",
    }
    for label, pred in INTENT.get(cid, []):
        try:
            ok = pred(ctx)
        except Exception as e:
            ok = False
            label = f"{label} [pred error: {e}]"
        if not ok:
            failures.append(f"{cid}: INTENT failed -> {label}")

    # Validation smoke
    handler = comp.handler
    overrides_validate = handler is not None and type(handler).validate_arguments is not BaseComponentHandler.validate_arguments
    if cid in VALID_SMOKE and overrides_validate:
        for expect_ok, call in VALID_SMOKE[cid]:
            try:
                ok, msg = handler.validate_arguments(call)
            except Exception as e:
                failures.append(f"{cid}: validate_arguments raised on {call}: {e}")
                continue
            if ok != expect_ok:
                failures.append(f"{cid}: validate_arguments({call}) -> {ok}, expected {expect_ok} (msg={msg!r})")

    return failures


def main():
    targets = sys.argv[1:] or ACTION_COMPONENTS
    all_fail = []
    for cid in targets:
        if cid not in GOLDEN:
            print(f"SKIP unknown component: {cid}")
            continue
        fails = check_component(cid)
        if fails:
            print(f"FAIL  {cid}  ({len(fails)} issue(s))")
            for f in fails:
                print(f"    - {f}")
            all_fail += fails
        else:
            print(f"PASS  {cid}")
    print("-" * 60)
    if all_fail:
        print(f"RESULT: FAIL ({len(all_fail)} issue(s) across {len(targets)} component(s))")
        sys.exit(1)
    print(f"RESULT: PASS ({len(targets)} component(s))")


if __name__ == "__main__":
    main()
