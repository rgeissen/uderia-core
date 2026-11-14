#!/usr/bin/env python3
"""Gate for THE one component-enablement rule (components/enablement.py).

Guards the defect class recorded in
docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md: the rule had four
independent re-derivations that disagreed, so a component could be reported disabled
by every UI while its tool stayed bound to the model, and an admin's global disable
did not disable.

Sections:
  [A] the pure truth table                (decide())
  [B] every derivation agrees             (resolver / runtime / recommender / memory)
  [C] the global admin gate actually gates the RUNTIME surface
  [D] the retired second manifest key stays retired
  [E] effect-site enforcement
  [F] baseline: no componentConfig behaves as before the collapse
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


class FakeComp:
    def __init__(self, cid="dashboard", ctype="action", enabled_for=("genie",)):
        self.component_id = cid
        self.component_type = ctype
        self.profile_defaults = {"enabled_for": list(enabled_for)}
        self.manifest = {}


# ---------------------------------------------------------------- [A] truth table
def t_decide_truth_table():
    from trusted_data_agent.components import enablement as e
    comp = FakeComp(enabled_for=("genie", "llm_only"))

    def d(profile, **kw):
        kw.setdefault("available", True)
        return e.decide(comp, profile, **kw)

    # step 1 — availability is decisive and cannot be overridden by anything below it
    check("global gate off -> False even with explicit enabled:true",
          d({"profile_type": "genie",
             "componentConfig": {"dashboard": {"enabled": True}}}, available=False) is False)

    # step 2 — explicit profile config beats the per-user default and the manifest
    check("explicit true wins over manifest miss",
          d({"profile_type": "tool_enabled",
             "componentConfig": {"dashboard": {"enabled": True}}}) is True)
    check("explicit false wins over manifest hit",
          d({"profile_type": "genie",
             "componentConfig": {"dashboard": {"enabled": False}}}) is False)
    check("explicit false wins over a per-user default of true",
          d({"profile_type": "genie", "componentConfig": {"dashboard": {"enabled": False}}},
            user_defaults={"dashboard": True}) is False)

    # step 3 — per-user default applies only when there is no explicit entry
    check("per-user default applies when entry absent",
          d({"profile_type": "tool_enabled"}, user_defaults={"dashboard": True}) is True)
    check("per-user default can disable a manifest-on component",
          d({"profile_type": "genie"}, user_defaults={"dashboard": False}) is False)
    check("per-user default for a DIFFERENT component is ignored",
          d({"profile_type": "genie"}, user_defaults={"chart": False}) is True)

    # step 4 — manifest default is per profile TYPE
    check("manifest default hit", d({"profile_type": "genie"}) is True)
    check("manifest default miss", d({"profile_type": "rag_focused"}) is False)

    # shape robustness — a malformed entry must not be read as "enabled"
    check("absent componentConfig falls through to the manifest",
          d({"profile_type": "genie", "componentConfig": None}) is True)
    check("non-dict entry falls through, never True-by-accident",
          d({"profile_type": "rag_focused", "componentConfig": {"dashboard": "yes"}}) is False)
    check("None component -> False", e.decide(None, {}, available=True) is False)

    # require_action — chips/companion skills only
    sysc = FakeComp(cid="context_window", ctype="system", enabled_for=("genie",))
    check("require_action filters a system component",
          e.decide(sysc, {"profile_type": "genie"}, available=True, require_action=True) is False)
    check("without require_action a system component resolves honestly",
          e.decide(sysc, {"profile_type": "genie"}, available=True) is True)


# ------------------------------------------------------- [B] every derivation agrees
def t_all_derivations_agree():
    from trusted_data_agent.agent.recommender import _component_enabled_for_profile
    from trusted_data_agent.components import enablement as e
    from trusted_data_agent.components.manager import get_component_manager
    import components.builtin.memory.adapter as mem

    mgr = get_component_manager()
    types = ["llm_only", "rag_focused", "tool_enabled", "genie"]
    cases = [("absent", {}), ("on", {"enabled": True}), ("off", {"enabled": False})]
    cids = sorted(c.component_id for c in mgr.get_all_components())
    check("component inventory non-empty", len(cids) >= 8, str(cids))

    mismatches = []
    for pt in types:
        for cid in cids:
            comp = mgr.get_component(cid)
            for label, cc in cases:
                prof = {"profile_type": pt, "componentConfig": ({cid: cc} if cc else {})}
                resolver = e.is_enabled(comp, prof, None, profile_type=pt)
                runtime = cid in {c.component_id for c in mgr.get_active_components(prof)}
                # the runtime additionally applies the PROP-005 relevance gate, which may
                # only ever REMOVE a component — never add one.
                if resolver is False and runtime is True:
                    mismatches.append(("runtime-leak", pt, cid, label))
                if comp.component_type == "action":
                    # the action-affordance reading (chips, companion skills) narrows by the
                    # handler's TOOL gate too (PROP-010 offers_tool) — it may only REMOVE
                    rec = _component_enabled_for_profile(mgr, cid, prof, pt, None)
                    if rec != (resolver and e.offers_tool(comp, prof)):
                        mismatches.append(("recommender", pt, cid, label, rec, resolver))
                if cid == "memory":
                    m = mem.memory_component_enabled(prof, pt, None, mgr)
                    if m != resolver:
                        mismatches.append(("memory-adapter", pt, cid, label, m, resolver))
    check("no derivation disagrees with the resolver", not mismatches, str(mismatches[:4]))

    # PROP-010 — a tool gate narrows the action affordances only: Files stays ENABLED for an
    # agent (its panel, Keep, attachments) while its tool and companion skill wait for the opt-in
    files = mgr.get_component("files")
    if files is not None:
        off = {"profile_type": "llm_only", "componentConfig": {"files": {"enabled": True}}}
        on = {"profile_type": "llm_only", "componentConfig": {"files": {"enabled": True, "agentTool": True}}}
        check("files: enabled without the agent opt-in", e.is_enabled(files, off, None) is True)
        check("files: no action affordance (companion skill) without the opt-in",
              _component_enabled_for_profile(mgr, "files", off, "llm_only", None) is False)
        check("files: the affordance follows the opt-in",
              _component_enabled_for_profile(mgr, "files", on, "llm_only", None) is True)
        names = lambda p: {t["name"] for t in mgr.get_tool_definitions(p)}
        check("files: TDA_Files is not advertised without the opt-in", "TDA_Files" not in names(off))
        check("files: TDA_Files is advertised with it", "TDA_Files" in names(on))
        status = {c.component_id: dis for c, dis in mgr.get_action_tool_status(off)}
        check("files: the Optimize tool status reads the tool disabled without the opt-in", status.get("files") is True)
        check("files: its instructions are withheld without the opt-in",
              "TDA_Files" not in mgr.get_instructions_text(off) and "TDA_Files" in mgr.get_instructions_text(on))
    check("matrix actually exercised", len(types) * len(cids) * len(cases) >= 100)


def t_memory_default_on():
    """The Aug-2026 decision: memory is enabled by default for every profile type —
    unless a profile explicitly overrides it."""
    from trusted_data_agent.components import enablement as e
    from trusted_data_agent.components.manager import get_component_manager
    mgr = get_component_manager()
    comp = mgr.get_component("memory")
    check("memory component present", comp is not None)
    for pt in ["llm_only", "rag_focused", "tool_enabled", "genie"]:
        check(f"memory default-on for {pt}",
              e.is_enabled(comp, {"profile_type": pt}, None, profile_type=pt) is True)
    check("memory explicit override still wins",
          e.is_enabled(comp, {"profile_type": "genie",
                              "componentConfig": {"memory": {"enabled": False}}},
                       None, profile_type="genie") is False)


# --------------------------------------------- [C] the global gate gates the RUNTIME
def t_global_gate_reaches_the_runtime_surface():
    """The headline defect: get_active_components never consulted the admin gate, so a
    globally-disabled component stayed bound to the model."""
    import trusted_data_agent.components.settings as settings
    from trusted_data_agent.components.manager import get_component_manager
    mgr = get_component_manager()
    prof = {"profile_type": "genie", "componentConfig": {"dashboard": {"enabled": True}}}

    orig = settings.is_component_available
    try:
        check("baseline: explicitly-enabled component is active",
              "dashboard" in {c.component_id for c in mgr.get_active_components(prof, "u1")})
        settings.is_component_available = lambda cid, uuid=None: cid != "dashboard"
        active = {c.component_id for c in mgr.get_active_components(prof, "u1")}
        check("global disable removes it from the runtime surface", "dashboard" not in active)
        tools = {t["name"] for t in mgr.get_tool_definitions(prof, "u1")}
        check("global disable removes the TOOL from the LLM surface",
              "TDA_Dashboard" not in tools, str(sorted(tools)))
        check("other components are unaffected", "chart" in active or "TDA_Charting" in tools)
    finally:
        settings.is_component_available = orig


# ------------------------------------------------ [D] the retired key stays retired
def t_second_manifest_key_is_gone():
    import glob
    import json
    offenders = [f for f in glob.glob("components/builtin/*/manifest.json")
                 if "default_enabled" in (json.load(open(f)).get("profile_defaults") or {})]
    check("no builtin manifest carries the retired default_enabled key",
          not offenders, str(offenders))
    every = glob.glob("components/builtin/*/manifest.json")
    check("manifests were actually read", len(every) >= 8, str(len(every)))
    check("every manifest declares enabled_for",
          all("enabled_for" in (json.load(open(f)).get("profile_defaults") or {}) for f in every))


def t_no_site_rederives_the_rule():
    """Structural: the collapsed sites must delegate, not keep a private copy."""
    mgr = open("src/trusted_data_agent/components/manager.py").read()
    check("get_active_components delegates to the resolver",
          "enablement.filter_enabled(" in mgr)
    check("get_active_components no longer defaults an absent entry to True",
          'comp_settings.get("enabled", True)' not in mgr)
    rec = open("src/trusted_data_agent/agent/recommender.py").read()
    check("recommender delegates", "enablement.is_enabled(" in rec)
    check("recommender keeps no private chain",
          'comp.profile_defaults.get("enabled_for"' not in rec)
    mem = open("components/builtin/memory/adapter.py").read()
    check("memory adapter delegates", "enablement.is_enabled(" in mem)
    rest = open("src/trusted_data_agent/api/rest_routes.py").read()
    check("REST profile view delegates", "enablement.decide(" in rest)


# ------------------------------------------------------- [E] effect-site enforcement
def t_effect_site_guard():
    from trusted_data_agent.components import enablement as e
    from trusted_data_agent.components.manager import get_component_manager
    mgr = get_component_manager()
    comp = mgr.get_component("dashboard")
    on = {"profile_type": "genie", "componentConfig": {"dashboard": {"enabled": True}}}
    off = {"profile_type": "genie", "componentConfig": {"dashboard": {"enabled": False}}}
    check("enabled -> allowed", e.enforce_component_call(comp, on) is None)
    deny = e.enforce_component_call(comp, off)
    check("disabled -> denied", isinstance(deny, str) and deny)
    check("denial names the component", "dashboard" in (deny or ""))
    check("denial is recovery-shaped (tells the agent what to do)",
          "not retry" in (deny or "").lower() or "enable" in (deny or "").lower())

    adapter = open("src/trusted_data_agent/mcp_adapter/adapter.py").read()
    check("Optimize chokepoint calls the guard",
          "enforce_component_call(" in adapter)
    check("Optimize chokepoint returns a terminal error shape",
          '"denied_reason": "component_not_enabled"' in adapter)
    mgr_src = open("src/trusted_data_agent/components/manager.py").read()
    check("ReAct chokepoint calls the guard", "enforce_component_call(" in mgr_src)


def t_rest_creation_gated():
    """Capability-creating mutations gated; reads and cleanup left open."""
    dash = open("src/trusted_data_agent/api/dashboard_routes.py").read()
    check("dashboard create gated", "_assert_component_available(\"dashboard\"" in dash)
    check("dashboard gate applied twice (create + add_tile)",
          dash.count('_assert_component_available("dashboard"') == 2,
          str(dash.count('_assert_component_available("dashboard"')))
    flow = open("src/trusted_data_agent/api/flow_routes.py").read()
    check("flow create+execute gated",
          flow.count('_assert_component_available("flow"') == 2)
    canvas = open("src/trusted_data_agent/api/canvas_routes.py").read()
    check("canvas create gated", '_assert_component_available("canvas"' in canvas)
    rest = open("src/trusted_data_agent/api/rest_routes.py").read()
    check("memory CREATE gated", 'is_component_available("memory"' in rest)
    check("scheduler uses the shared predicate, not its own copy",
          'is_component_available("scheduler"' in rest)
    check("memory reads/erase are NOT gated (data-subject rights)",
          rest.count('is_component_available("memory"') == 1,
          str(rest.count('is_component_available("memory"')))


# ------------------------------------------- [G] the vocabulary: Auto and active (Nov 2026)
def t_auto_and_active():
    from trusted_data_agent.components import enablement as E
    comp = FakeComp(cid="memory", enabled_for=("llm_only",))
    comp.manifest = {"acts_every_turn": True}
    check("[G] no stored switch = Auto (the platform holds it)", E.is_auto(comp, {"profile_type": "llm_only"}) is True)
    check("[G] a stored switch is the owner's, on or off",
          E.is_auto(comp, {"componentConfig": {"memory": {"enabled": True}}}) is False
          and E.is_auto(comp, {"componentConfig": {"memory": {"enabled": False}}}) is False)
    check("[G] an entry without a switch (settings only) is still Auto", E.is_auto(comp, {"componentConfig": {"memory": {"intensity": "high"}}}) is True)
    check("[G] acts_every_turn reads the manifest declaration, default False",
          E.acts_every_turn(comp) is True and E.acts_every_turn(FakeComp(cid="chart")) is False)
    import json, glob
    every = sorted((m for f in glob.glob("components/builtin/*/manifest.json")
                    for m in [json.load(open(f))] if m.get("acts_every_turn") is True), key=lambda m: m["component_id"])
    check("[G] exactly context_window · memory · needs_input declare acts_every_turn",
          [m["component_id"] for m in every] == ["context_window", "memory", "needs_input"])
    check("[G] a declaration never changes the chain: enabled is decided as before",
          E.decide(comp, {"profile_type": "llm_only"}, available=True) is True
          and E.decide(comp, {"profile_type": "genie"}, available=True) is False)


# --------------------------------------------------------------- [F] baseline intact
def t_baseline_no_component_config():
    """A profile with no componentConfig must resolve exactly as it did before the
    collapse — the change is meant to fix governance, not to move the default."""
    from trusted_data_agent.components.manager import get_component_manager
    mgr = get_component_manager()
    for pt in ["llm_only", "rag_focused", "tool_enabled", "genie"]:
        active = {c.component_id for c in mgr.get_active_components({"profile_type": pt})}
        expected = {c.component_id for c in mgr.get_all_components()
                    if pt in (c.profile_defaults.get("enabled_for") or [])
                    and mgr._component_relevant(c, {"profile_type": pt})}
        check(f"no-config baseline for {pt}", active == expected,
              f"{sorted(active ^ expected)}")
        check(f"{pt}: the six action components + memory are on",
              {"chart", "canvas", "dashboard", "flow",
               "knowledge_graph", "scheduler", "memory"} <= active,
              str(sorted(active)))
        # REGRESSION GUARD (caught during this change): a relevance-gated component must
        # stay ELIGIBLE in the manifest. Before the collapse an absent entry defaulted to
        # True, so skill_library could declare enabled_for=[] and still be offered to a
        # profile with an active progressive skill; once absent means "ask the manifest",
        # an empty enabled_for silences it permanently and the relevance hook can never
        # fire. Eligibility is the manifest's job; narrowing is the hook's.
        check(f"{pt}: skill_library stays eligible so its relevance hook can decide",
              pt in ((mgr.get_component("skill_library").profile_defaults or {})
                     .get("enabled_for") or []))
        check(f"{pt}: skill_library is still NOT offered without a progressive skill",
              "skill_library" not in active, str(sorted(active)))


def t_frontend_gate_exists_and_fails_closed():
    src = open("static/js/handlers/componentGate.js").read()
    check("gate reads the server-resolved field", "profile_enabled" in src)
    # Strip comments first — prose ABOUT the code is not the code. (A bare grep here
    # matched the word "componentConfig" inside a docstring and reported a violation
    # that did not exist; the same trap is recorded in test_quality_card_partition.)
    code = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", src)
    check("gate does not re-derive the rule",
          "enabled_for" not in code and "componentConfig" not in code)
    # Fail-closed and no-caching-of-failures are asserted BEHAVIOURALLY by
    # test/test_component_gate.mjs, which executes this module against a stubbed fetch.
    # A substring rule here let a fail-OPEN mutation escape (another line satisfied it).
    check("the executed gate suite exists", os.path.exists("test/test_component_gate.mjs"))

    ui = open("static/js/ui.js").read()
    check("table interception is gated on the component, not on hasRenderer alone",
          "ComponentGate" in ui and "isEnabled('dashboard'" in ui)
    check("no gate module -> no affordance",
          "Promise.resolve(false)" in ui)
    def _fn(src, name):
        i = src.index(f"function {name}("); j = src.find("\nfunction ", i + 10)
        return src[i:j if j > 0 else len(src)]
    check("origin derived once per path, never twice in one (each table loop; the pin act's gate; its click; "
          "the model-gap form's suggestion)",
          all(_fn(ui, n).count("_deriveTableOrigin(") == 1 for n in ("_wirePinActs", "_onPinAct", "_onGapProposeAct"))
          and ui.count("_deriveTableOrigin(") == 1 + len(re.findall(r"Tables\.forEach\(\(table", ui)) + 3)

    # "Pin to dashboard" is the footer act of an answer row, revealed only under the gate;
    # the floating hover button dashboardHandler.js used to inject is gone
    dh = open("static/js/handlers/dashboardHandler.js").read()
    wire = _fn(ui, "_wirePinActs")
    check("the pin act is gated and revealed only when the gate says yes",
          "isEnabled('dashboard'" in wire and "if (ok && btn.isConnected) btn.hidden = false" in wire)
    check("the pin act is marked synchronously so a re-render never wires it twice",
          "if (btn.dataset.wired) return;" in wire and wire.index("btn.dataset.wired = '1'") < wire.index("isEnabled('dashboard'"))
    check("no floating pin button is injected over cards any more",
          "dash-pin-btn" not in dh and "MutationObserver" not in dh)

    tpl = open("templates/index.html").read()
    check("componentGate.js is loaded before its consumer",
          tpl.index("componentGate.js") < tpl.index("dashboardHandler.js"))


for fn in [t_decide_truth_table, t_all_derivations_agree, t_memory_default_on,
           t_global_gate_reaches_the_runtime_surface, t_second_manifest_key_is_gone,
           t_no_site_rederives_the_rule, t_effect_site_guard, t_rest_creation_gated,
           t_baseline_no_component_config, t_frontend_gate_exists_and_fails_closed, t_auto_and_active]:
    try:
        fn()
    except Exception as exc:            # a raising test is a NAMED failure, never a silent abort
        failed += 1
        print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
