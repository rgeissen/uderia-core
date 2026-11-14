"""
Module tests for the Next-Step Recommender (src/trusted_data_agent/agent/recommender.py).

Self-contained, no network / no live app. Heavy/lazy dependencies (component
manager, RAG retriever, LLM handler) are stubbed so each tier is exercised
deterministically.

Run:
    PYTHONPATH=src python test/test_recommender.py
"""
import os
import sys
import types
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent import recommender  # noqa: E402

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def run(coro):
    return _loop.run_until_complete(coro)


# ─── stubs ────────────────────────────────────────────────────────────────────
_ALL_TYPES = ["llm_only", "rag_focused", "tool_enabled", "genie"]


class _FakeComp:
    def __init__(self, cid, ctype="action", enabled_for=None):
        self.component_id = cid
        self.component_type = ctype
        self.profile_defaults = {"enabled_for": enabled_for if enabled_for is not None else list(_ALL_TYPES)}


class _FakeManager:
    """Mimics ComponentManager.get_component / get_active_components."""
    def __init__(self, ids, enabled_for_map=None):
        self._ids = ids
        self._ef = enabled_for_map or {}

    def get_component(self, comp_id):
        if comp_id in self._ids:
            return _FakeComp(comp_id, enabled_for=self._ef.get(comp_id))
        return None

    def get_active_components(self, profile):
        cc = profile.get("componentConfig") or {}
        return [_FakeComp(cid) for cid in self._ids
                if cc.get(cid, {}).get("enabled", True)]


# components the platform/admin has globally disabled (drives is_component_available stub)
_globally_disabled = set()


def install_components(ids, enabled_for_map=None, globally_disabled=None):
    import trusted_data_agent.components.manager as mgr
    import trusted_data_agent.components.settings as cs
    mgr.get_component_manager = lambda *a, **k: _FakeManager(ids, enabled_for_map)
    _globally_disabled.clear()
    if globally_disabled:
        _globally_disabled.update(globally_disabled)
    cs.is_component_available = lambda comp_id, user_uuid=None: comp_id not in _globally_disabled


# Every retrieve_examples call the fake retriever receives (kwargs), for assertions.
_rag_calls = []


def install_rag(cases, ctx_raises=False, delay=0.0):
    """Inject fake rag_retriever + rag_access_context modules so Tier B is deterministic.

    The access context is stubbed on its REAL module (`rag_access_context`) — the
    recommender once imported it from `rag_retriever`, where it does not exist, and a
    stub planted on the wrong module let that ImportError pass for months (the
    swallowed error made every turn scan every collection unscoped). See
    test_tier_b_access_scope.
    """
    fake = types.ModuleType("trusted_data_agent.agent.rag_retriever")

    class _FakeRetriever:
        async def retrieve_examples(self, **kwargs):
            _rag_calls.append(kwargs)
            if delay:
                await asyncio.sleep(delay)
            return list(cases)

    fake.get_rag_retriever = lambda: (_FakeRetriever() if cases is not None else None)
    sys.modules["trusted_data_agent.agent.rag_retriever"] = fake

    ctx_mod = types.ModuleType("trusted_data_agent.agent.rag_access_context")

    class _FakeCtx:
        def __init__(self, user_id, retriever):
            if ctx_raises:
                raise RuntimeError("no access context")
            self.user_id = user_id
            self.retriever = retriever
    ctx_mod.RAGAccessContext = _FakeCtx
    sys.modules["trusted_data_agent.agent.rag_access_context"] = ctx_mod
    _rag_calls.clear()


def uninstall_rag():
    sys.modules.pop("trusted_data_agent.agent.rag_retriever", None)
    sys.modules.pop("trusted_data_agent.agent.rag_access_context", None)


def install_llm(returned_text):
    """Inject fakes for the lazy imports inside _build_llm_chips."""
    handler = types.ModuleType("trusted_data_agent.llm.handler")

    async def _call(*a, **k):
        return (returned_text, 10, 5, "FakeProvider", "fake-model")
    handler.call_llm_api = _call
    sys.modules["trusted_data_agent.llm.handler"] = handler

    cf = types.ModuleType("trusted_data_agent.llm.client_factory")

    async def _client(*a, **k):
        return object()
    cf.get_or_create_llm_client = _client
    sys.modules["trusted_data_agent.llm.client_factory"] = cf

    cs = types.ModuleType("trusted_data_agent.core.configuration_service")

    async def _creds(*a, **k):
        return {"credentials": {"api_key": "x"}}
    cs.retrieve_credentials_for_provider = _creds
    sys.modules["trusted_data_agent.core.configuration_service"] = cs

    cm = types.ModuleType("trusted_data_agent.core.config_manager")

    class _CM:
        def get_llm_configurations(self, uuid):
            return [{"id": "llm1", "provider": "FakeProvider", "model": "fake-model",
                     "credentials": {"api_key": "x"}}]
    cm.get_config_manager = lambda: _CM()
    sys.modules["trusted_data_agent.core.config_manager"] = cm


def uninstall_llm():
    for m in ("trusted_data_agent.llm.handler",
              "trusted_data_agent.llm.client_factory",
              "trusted_data_agent.core.configuration_service",
              "trusted_data_agent.core.config_manager"):
        sys.modules.pop(m, None)


# ─── tiny assert harness ──────────────────────────────────────────────────────
_passed = 0


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        print(f"\n{_passed} passed, 1 failed  — {name}")
        sys.exit(1)  # battery exit-code contract: a failing suite must exit non-zero


# ─── helpers ──────────────────────────────────────────────────────────────────
def test_helpers():
    print("helpers")
    check("strip_tag removes @TAG", recommender._strip_tag("@OPTIM how many?") == "how many?")
    check("strip_tag no-op", recommender._strip_tag("plain text") == "plain text")
    check("norm case/punct", recommender._norm("How MANY, dbs?") == recommender._norm("how many dbs"))
    sh = recommender._result_shape({"a": [{"x": 1}, {"x": 2}], "b": "scalar"})
    check("result_shape tabular", sh["tabular"] is True and sh["rows"] == 2, str(sh))
    check("result_shape empty", recommender._result_shape({})["tabular"] is False)
    check("result_shape non-dict", recommender._result_shape(None)["tabular"] is False)


def test_config_merge():
    print("config merge")
    d = recommender.get_recommender_config(None)
    check("default enabled", d["enabled"] is True)
    check("default tiers", set(d["tiers"]) == {"action", "followup"})
    merged = recommender.get_recommender_config(
        {"recommenderConfig": {"maxSuggestions": 99, "tiers": ["action"]}})
    check("max clamp upper not applied at merge (only floor)", merged["maxSuggestions"] == 99)
    floored = recommender.get_recommender_config({"recommenderConfig": {"maxSuggestions": 0}})
    check("max floor=1", floored["maxSuggestions"] == 1)
    bad = recommender.get_recommender_config({"recommenderConfig": {"maxSuggestions": "x"}})
    check("max bad->4", bad["maxSuggestions"] == 4)


def test_tier_a():
    print("Tier A action chips")
    install_components(["chart", "dashboard", "scheduler", "flow", "canvas", "knowledge_graph"])
    base = {"id": "p", "profile_type": "tool_enabled",
            "recommenderConfig": {"enabled": True, "tiers": ["action"], "maxSuggestions": 8}}

    tab = dict(base, componentConfig={k: {"enabled": True} for k in
               ["chart", "dashboard", "scheduler", "flow"]})
    payload = {"final_answer": "<p>x</p>", "final_answer_text": "5 dbs",
               "collected_data": {"q": [{"d": 1}, {"d": 2}]}}
    out = run(recommender.build_suggestions(tab, "u", "q", payload))
    labels = [c["label"] for c in out]
    check("tabular offers chart", "Chart this" in labels, str(labels))
    check("tabular offers dashboard", "Pin to dashboard" in labels, str(labels))
    check("tabular offers schedule", "Schedule this" in labels, str(labels))
    check("tabular offers flow", "Build a flow" in labels, str(labels))
    check("all action kind", all(c["kind"] == "action" for c in out))

    # chart already rendered -> suppressed, dashboard still offered
    p2 = dict(base, componentConfig={"chart": {"enabled": True}, "dashboard": {"enabled": True}})
    pay2 = {"final_answer": '<div data-component-id="chart"></div>',
            "final_answer_text": "chart", "collected_data": {"q": [{"x": 1}]}}
    l2 = [c["label"] for c in run(recommender.build_suggestions(p2, "u", "q", pay2))]
    check("chart suppressed when present", "Chart this" not in l2, str(l2))
    check("dashboard still offered", "Pin to dashboard" in l2, str(l2))

    # one act, one place: the answer already carries "Pin to dashboard" (a data row's table
    # or a pinnable component row) -> the dock does not offer it a second time
    pay_tbl = dict(payload, final_answer='<button class="ans-act ans-pin-table" data-pin-table hidden>Pin to dashboard</button>')
    l_tbl = [c["label"] for c in run(recommender.build_suggestions(tab, "u", "q", pay_tbl))]
    check("a direct pin on a table suppresses the dock chip", "Pin to dashboard" not in l_tbl and "Schedule this" in l_tbl, str(l_tbl))
    pay_cmp = dict(pay2, final_answer='<div data-component-id="chart"></div><button class="ans-act ans-pin" data-pin-component="chart" hidden>Pin</button>')
    l_cmp = [c["label"] for c in run(recommender.build_suggestions(p2, "u", "q", pay_cmp))]
    check("a direct pin on a chart row suppresses the dock chip", "Pin to dashboard" not in l_cmp, str(l_cmp))
    p_chart_only = dict(base, componentConfig={"chart": {"enabled": True}, "dashboard": {"enabled": True}})
    pay_chart_only = {"final_answer": '<div data-component-id="chart"></div>', "final_answer_text": "c", "collected_data": {}}
    check("a chart with no direct pin still gets the chip (older answers)",
          "Pin to dashboard" in [c["label"] for c in run(recommender.build_suggestions(p_chart_only, "u", "q", pay_chart_only))])

    # disabled component never offered
    p3 = dict(base, componentConfig={"chart": {"enabled": False}, "dashboard": {"enabled": True}})
    l3 = [c["label"] for c in run(recommender.build_suggestions(p3, "u", "q", payload))]
    check("disabled chart not offered", "Chart this" not in l3, str(l3))

    # canvas on code
    p4 = dict(base, componentConfig={"canvas": {"enabled": True}})
    pay4 = {"final_answer": "<p>c</p>", "final_answer_text": "```python\nprint(1)\n```",
            "collected_data": {}}
    l4 = [c["label"] for c in run(recommender.build_suggestions(p4, "u", "q", pay4))]
    check("canvas on code", "Open in canvas" in l4, str(l4))

    # KG expansion when graph already drawn
    p5 = dict(base, componentConfig={"knowledge_graph": {"enabled": True}})
    pay5 = {"final_answer": '<div data-component-id="knowledge_graph"></div>',
            "final_answer_text": "graph", "collected_data": {}}
    l5 = [c["label"] for c in run(recommender.build_suggestions(p5, "u", "q", pay5))]
    check("kg expansion", "Expand in graph" in l5, str(l5))

    # non-tabular, no code -> no action chips
    p6 = dict(base, componentConfig={"chart": {"enabled": True}, "dashboard": {"enabled": True}})
    pay6 = {"final_answer": "<p>hi</p>", "final_answer_text": "just chat", "collected_data": {}}
    check("non-tabular empty", run(recommender.build_suggestions(p6, "u", "hi", pay6)) == [])


def test_enablement_governance():
    """Regression: chips must match the editor's profile_enabled truth exactly."""
    print("enablement governance (the reported bug)")
    pay = {"final_answer": "<p>r</p>", "final_answer_text": "data",
           "collected_data": {"q": [{"a": 1}]}}

    # (a) ABSENT componentConfig entry must NOT default to enabled. It should follow
    #     the manifest's enabled_for for this profile_type.
    install_components(["chart", "dashboard"],
                      enabled_for_map={"chart": ["tool_enabled"],      # on for tool_enabled
                                       "dashboard": ["llm_only"]})       # off for tool_enabled
    prof = {"id": "p", "profile_type": "tool_enabled",
            "componentConfig": {},  # nothing explicitly set
            "recommenderConfig": {"enabled": True, "tiers": ["action"], "maxSuggestions": 8}}
    labels = [c["label"] for c in run(recommender.build_suggestions(prof, "u", "q", pay))]
    check("absent entry honors enabled_for (chart on)", "Chart this" in labels, str(labels))
    check("absent entry honors enabled_for (dashboard off)", "Pin to dashboard" not in labels, str(labels))

    # (b) The 'only chart/canvas keys' real-world profile shape: dashboard/flow absent.
    #     With manifest default off for this type, they must NOT be offered.
    install_components(["chart", "dashboard", "flow"],
                      enabled_for_map={"chart": ["tool_enabled"], "dashboard": [], "flow": []})
    prof2 = {"id": "p", "profile_type": "tool_enabled",
             "componentConfig": {"chart": {"enabled": True}, "canvas": {"enabled": True}},
             "recommenderConfig": {"enabled": True, "tiers": ["action"], "maxSuggestions": 8}}
    l2 = [c["label"] for c in run(recommender.build_suggestions(prof2, "u", "q", pay))]
    check("partial config: chart offered", "Chart this" in l2, str(l2))
    check("partial config: absent dashboard NOT offered", "Pin to dashboard" not in l2, str(l2))
    check("partial config: absent flow NOT offered", "Build a flow" not in l2, str(l2))

    # (c) Globally disabled component is never offered, even if enabled in the profile.
    install_components(["chart", "dashboard"], globally_disabled={"dashboard"})
    prof3 = {"id": "p", "profile_type": "tool_enabled",
             "componentConfig": {"chart": {"enabled": True}, "dashboard": {"enabled": True}},
             "recommenderConfig": {"enabled": True, "tiers": ["action"], "maxSuggestions": 8}}
    l3 = [c["label"] for c in run(recommender.build_suggestions(prof3, "u", "q", pay))]
    check("globally-disabled excluded", "Pin to dashboard" not in l3, str(l3))
    check("enabled+available offered", "Chart this" in l3, str(l3))
    install_components([])  # reset governance state


def test_gating():
    print("gating")
    install_components(["chart"])
    pay = {"final_answer": "x", "final_answer_text": "x", "collected_data": {"q": [{"a": 1}]}}
    check("profile disabled -> empty",
          run(recommender.build_suggestions(
              {"id": "p", "recommenderConfig": {"enabled": False}}, "u", "q", pay)) == [])
    check("no profile -> empty",
          run(recommender.build_suggestions(None, "u", "q", pay)) == [])
    # global gate
    from trusted_data_agent.core.config import APP_CONFIG
    APP_CONFIG.RECOMMENDER_ENABLED = False
    try:
        check("global gate -> empty",
              run(recommender.build_suggestions(
                  {"id": "p", "componentConfig": {"chart": {"enabled": True}},
                   "recommenderConfig": {"enabled": True, "tiers": ["action"]}},
                  "u", "q", pay)) == [])
    finally:
        APP_CONFIG.RECOMMENDER_ENABLED = True


def test_tier_b():
    print("Tier B RAG follow-ups")
    install_components([])  # no action chips, isolate Tier B
    profile = {"id": "p", "autocompleteCollections": ["*"],
               "recommenderConfig": {"enabled": True, "tiers": ["followup"],
                                     "maxSuggestions": 3, "relevanceThreshold": 0.5}}
    pay = {"final_answer": "x", "final_answer_text": "x", "collected_data": {}}

    # near-duplicate of the asked query (with @TAG + casing) must be dropped
    install_rag([
        {"case_id": "1", "user_query": "@OPTIM How many databases are on the system?"},
        {"case_id": "2", "user_query": "List the tables in the sales database"},
        {"case_id": "3", "user_query": "list the tables in the SALES database"},  # dup of 2
        {"case_id": "4", "user_query": "Show top 10 customers by revenue"},
    ])
    out = run(recommender.build_suggestions(profile, "u", "@OPTIM how many databases are on the system?", pay))
    labels = [c["label"] for c in out]
    check("near-dup of asked dropped",
          all("how many databases" not in lbl.lower() for lbl in labels), str(labels))
    check("internal dup collapsed", len([l for l in labels if l.lower().startswith("list the tables")]) == 1, str(labels))
    check("tag stripped in display", all(not l.startswith("@") for l in labels), str(labels))
    check("respects max=3", len(out) <= 3, str(labels))
    check("all followup kind", all(c["kind"] == "followup" for c in out))
    uninstall_rag()

    # autocompleteCollections [] disables Tier B
    install_rag([{"case_id": "9", "user_query": "anything"}])
    p_off = dict(profile, autocompleteCollections=[])
    check("collections [] disables Tier B",
          run(recommender.build_suggestions(p_off, "u", "q", pay)) == [])
    uninstall_rag()

    # retriever absent -> graceful empty
    install_rag(None)
    check("no retriever -> empty",
          run(recommender.build_suggestions(profile, "u", "q", pay)) == [])
    uninstall_rag()


def test_tier_b_access_scope():
    print("Tier B access scope — fail closed, bounded (Aug 2026 prod regression)")
    install_components([])
    profile = {"id": "p", "autocompleteCollections": ["*"],
               "recommenderConfig": {"enabled": True, "tiers": ["followup"],
                                     "maxSuggestions": 3, "relevanceThreshold": 0.5}}
    pay = {"final_answer": "x", "final_answer_text": "x", "collected_data": {}}

    # 1. The retriever is ALWAYS handed a real access context for the requesting user —
    #    rag_context=None means "scan every collection of every user".
    install_rag([{"case_id": "4", "user_query": "Show top 10 customers by revenue"}])
    out = run(recommender.build_suggestions(profile, "user-42", "list databases", pay))
    check("tier B produced a chip", len(out) == 1, str(out))
    check("retriever called once", len(_rag_calls) == 1, str(len(_rag_calls)))
    ctx = _rag_calls[0].get("rag_context")
    check("rag_context is NOT None", ctx is not None)
    check("rag_context is scoped to the requesting user", getattr(ctx, "user_id", None) == "user-42")
    check("planner repository type requested", _rag_calls[0].get("repository_type") == "planner")
    uninstall_rag()

    # 2. No access context ⇒ NO retrieval at all (never an unscoped scan).
    install_rag([{"case_id": "4", "user_query": "Show top 10 customers by revenue"}], ctx_raises=True)
    out = run(recommender.build_suggestions(profile, "user-42", "list databases", pay))
    check("no access context -> no suggestions", out == [], str(out))
    check("no access context -> retriever NEVER called", len(_rag_calls) == 0, str(len(_rag_calls)))
    uninstall_rag()

    # 3. A slow store is bounded — it can never hold the turn (the dial + the next
    #    request wait on this seam).
    from trusted_data_agent.core.config import APP_CONFIG
    _prev = getattr(APP_CONFIG, "RECOMMENDER_TIER_B_TIMEOUT_SECONDS", 5.0)
    APP_CONFIG.RECOMMENDER_TIER_B_TIMEOUT_SECONDS = 0.05
    try:
        install_rag([{"case_id": "4", "user_query": "Show top 10 customers by revenue"}], delay=0.5)
        import time as _t
        t0 = _t.monotonic()
        out = run(recommender.build_suggestions(profile, "user-42", "list databases", pay))
        dt = _t.monotonic() - t0
        check("slow retrieval -> empty, not a hang", out == [], str(out))
        check("bounded by the configured budget", dt < 0.4, f"{dt:.3f}s")
    finally:
        APP_CONFIG.RECOMMENDER_TIER_B_TIMEOUT_SECONDS = _prev
        uninstall_rag()


def test_tier_ordering_and_fill():
    print("cross-tier ordering + fill")
    install_components(["chart", "dashboard"])
    install_rag([{"case_id": "1", "user_query": "Drill into errors by day"},
                 {"case_id": "2", "user_query": "Compare to last month"}])
    profile = {"id": "p", "autocompleteCollections": ["*"],
               "componentConfig": {"chart": {"enabled": True}, "dashboard": {"enabled": True}},
               "recommenderConfig": {"enabled": True, "tiers": ["action", "followup"],
                                     "maxSuggestions": 4, "relevanceThreshold": 0.3}}
    pay = {"final_answer": "<p>rows</p>", "final_answer_text": "data",
           "collected_data": {"q": [{"a": 1}]}}
    out = run(recommender.build_suggestions(profile, "u", "errors", pay))
    check("action chips lead", out[0]["kind"] == "action", str([c["kind"] for c in out]))
    check("followups fill after actions", any(c["kind"] == "followup" for c in out), str([c["kind"] for c in out]))
    check("capped at 4", len(out) <= 4)
    uninstall_rag()


def test_tier_c():
    print("Tier C LLM enrichment (opt-in)")
    install_components([])
    install_rag([])  # no Tier B results
    install_llm('["Break this down by region", "Show the trend over the last year", "errors"]')
    profile = {"id": "p", "llmConfigurationId": "llm1", "autocompleteCollections": ["*"],
               "recommenderConfig": {"enabled": True, "tiers": ["followup", "llm"],
                                     "maxSuggestions": 3, "llmEnrichment": True}}
    pay = {"final_answer": "x", "final_answer_text": "ans", "collected_data": {}}
    out = run(recommender.build_suggestions(profile, "u", "errors", pay))
    labels = [c["label"] for c in out]
    check("llm chips produced", "Break this down by region" in labels, str(labels))
    check("llm dedup vs asked", "errors" not in [l.lower() for l in labels], str(labels))

    # llmEnrichment False -> no Tier C even if 'llm' tier listed
    p_off = dict(profile, recommenderConfig=dict(profile["recommenderConfig"], llmEnrichment=False))
    check("llmEnrichment off -> no Tier C",
          run(recommender.build_suggestions(p_off, "u", "errors", pay)) == [])
    uninstall_llm()
    uninstall_rag()


def _install_session_manager(turn_ops):
    """Fake session_manager so Tier C accounting runs deterministically."""
    sm = types.ModuleType("trusted_data_agent.core.session_manager")

    async def update_token_count(u, s, i, o):
        turn_ops.append(("session_tokens", i, o)); return True

    async def get_session(u, s):
        return {"input_tokens": 1000, "output_tokens": 500,
                "last_turn_data": {"workflow_history": [
                    {"turn": 7, "turn_input_tokens": 100, "turn_output_tokens": 50}]}}

    async def update_turn_token_counts(u, s, t, i, o, added_cost_usd=None, additive=False):
        # Tier C now bills its OWN cost as a priced increment (added_cost_usd + additive) rather
        # than re-pricing the whole turn — mirror the real signature (session_manager.py).
        turn_ops.append(("turn_tokens", t, i, o, added_cost_usd, additive)); return True

    async def append_system_events_to_turn(u, s, t, events):
        turn_ops.append(("system_events", t, len(events))); return True

    sm.update_token_count = update_token_count
    sm.get_session = get_session
    sm.update_turn_token_counts = update_turn_token_counts
    sm.append_system_events_to_turn = append_system_events_to_turn
    sys.modules["trusted_data_agent.core.session_manager"] = sm
    import trusted_data_agent.core as core_pkg
    core_pkg.session_manager = sm


def _install_consumption_stub():
    import contextlib
    db = types.ModuleType("trusted_data_agent.auth.database")

    @contextlib.contextmanager
    def get_db_session():
        yield object()
    db.get_db_session = get_db_session
    sys.modules["trusted_data_agent.auth.database"] = db

    cmmod = types.ModuleType("trusted_data_agent.auth.consumption_manager")

    class ConsumptionManager:
        def __init__(self, db): pass
        def update_turn_tokens(self, **kw): pass
    cmmod.ConsumptionManager = ConsumptionManager
    sys.modules["trusted_data_agent.auth.consumption_manager"] = cmmod


def _uninstall_accounting():
    for m in ("trusted_data_agent.core.session_manager",
              "trusted_data_agent.auth.database",
              "trusted_data_agent.auth.consumption_manager"):
        sys.modules.pop(m, None)


def test_tier_c_observability():
    """Tier C runs through Live Status events + cost engine + session history."""
    print("Tier C observability + accounting (Session-Name-Generated parity)")
    install_components([])
    install_rag([])  # no A/B → Tier C fills
    install_llm('["Drill into errors by day", "Compare to last week", "errors"]')
    turn_ops = []
    _install_session_manager(turn_ops)
    _install_consumption_stub()

    captured = []
    async def handler(data, event):
        captured.append((event, data))

    profile = {"id": "p", "llmConfigurationId": "llm1", "autocompleteCollections": ["*"],
               "recommenderConfig": {"enabled": True, "tiers": ["llm"],
                                     "maxSuggestions": 3, "llmEnrichment": True}}
    pay = {"final_answer": "<p>x</p>", "final_answer_text": "ans", "collected_data": {},
           "turn_id": 7, "provider": "FakeProvider", "model": "fake-model"}
    run(recommender.build_and_emit_suggestions("u", "sess-1", "errors", profile, pay, 7, handler))

    events = [e for e, _ in captured]
    check("single completion step event (no start — one event is sufficient)",
          "next_step_generation_complete" in events and "next_step_generation_start" not in events, str(events))
    check("token_update emitted (cost engine)", "token_update" in events, str(events))
    check("suggestions notification emitted",
          any(d.get("type") == "next_step_suggestions" for e, d in captured if e == "notification"), str(events))

    # Event ordering: complete → token_update → suggestions
    order = [e for e in events if e in
             ("next_step_generation_complete", "token_update")]
    check("ordering complete<token_update",
          order == ["next_step_generation_complete", "token_update"], str(order))

    tu = next(d for e, d in captured if e == "token_update")
    check("token_update uses recommender call_id (idempotent ledger)",
          tu.get("call_id") == "next_step_recommender", str(tu))
    check("token_update statement tokens", tu.get("statement_input") == 10 and tu.get("statement_output") == 5, str(tu))
    check("turn totals = existing + Tier C (100+10 / 50+5)",
          tu.get("turn_input") == 110 and tu.get("turn_output") == 55, str(tu))
    check("session totals surfaced", tu.get("total_input") == 1000 and tu.get("total_output") == 500, str(tu))

    ce = next(d for e, d in captured if e == "next_step_generation_complete")
    check("complete carries cost + count details",
          "cost_usd" in ce.get("details", {}) and "suggestion_count" in ce.get("details", {}), str(ce))

    # Session history persistence calls happened
    check("session token total updated", any(o[0] == "session_tokens" for o in turn_ops), str(turn_ops))
    check("turn token counts persisted", any(o[0] == "turn_tokens" and o[1] == 7 for o in turn_ops), str(turn_ops))
    check("single step event persisted for reload",
          any(o[0] == "system_events" and o[1] == 7 and o[2] == 1 for o in turn_ops), str(turn_ops))

    # Chips still delivered (the dedupe dropped the echo of the asked query)
    notif = next(d for e, d in captured if e == "notification")
    labels = [i["label"] for i in notif["payload"]["items"]]
    check("Tier C chips delivered", "Drill into errors by day" in labels, str(labels))
    check("Tier C dedupe vs asked", "errors" not in [l.lower() for l in labels], str(labels))

    _uninstall_accounting()
    uninstall_llm()
    uninstall_rag()


def test_emit_contract():
    print("emit contract")
    install_components(["dashboard"])
    captured = []

    async def handler(data, event):
        captured.append((data, event))

    profile = {"id": "p", "componentConfig": {"dashboard": {"enabled": True}},
               "recommenderConfig": {"enabled": True, "tiers": ["action"]}}
    pay = {"final_answer": "<p>rows</p>", "final_answer_text": "d",
           "collected_data": {"q": [{"a": 1}]}, "turn_id": 7}
    run(recommender.build_and_emit_suggestions("u", "sess-1", "q", profile, pay, 7, handler))
    check("one event emitted", len(captured) == 1, str(captured))
    data, event = captured[0]
    check("event type notification", event == "notification")
    check("payload type", data.get("type") == "next_step_suggestions")
    p = data.get("payload", {})
    check("payload session_id", p.get("session_id") == "sess-1")
    check("payload turn_id", p.get("turn_id") == 7)
    check("payload items non-empty", len(p.get("items", [])) >= 1)

    # nothing emitted when no suggestions
    captured.clear()
    empty_profile = {"id": "p", "recommenderConfig": {"enabled": False}}
    run(recommender.build_and_emit_suggestions("u", "s", "q", empty_profile, pay, 1, handler))
    check("no emit when empty", len(captured) == 0)

    # never raises even if handler throws
    async def boom(data, event):
        raise RuntimeError("handler exploded")
    try:
        run(recommender.build_and_emit_suggestions("u", "s", "q", profile, pay, 1, boom))
        check("swallows handler error", True)
    except Exception as e:  # noqa
        check("swallows handler error", False, str(e))


def test_derive_flags():
    """derive_render_flags is the compact, stashable result-shape descriptor."""
    print("derive_render_flags (rebind descriptor)")
    f = recommender.derive_render_flags(
        {"final_answer": '<div data-component-id="chart"></div>',
         "final_answer_text": "```py\nprint(1)\n```",
         "collected_data": {"q": [{"a": 1}, {"a": 2}]}})
    check("flags tabular + rows", f["tabular"] is True and f["rows"] == 2, str(f))
    check("flags has_chart", f["has_chart"] is True, str(f))
    check("flags has_code", f["has_code"] is True, str(f))
    check("flags no kg/canvas", f["has_kg"] is False and f["has_canvas"] is False, str(f))
    check("flags has_direct_pin", recommender.derive_render_flags({"final_answer": "x data-pin-component=\"chart\""})["has_direct_pin"] is True
          and f["has_direct_pin"] is False, str(f))
    empty = recommender.derive_render_flags({})
    check("flags empty payload", empty["tabular"] is False and empty["has_chart"] is False, str(empty))
    check("flags JSON-serializable scalars",
          all(isinstance(v, (bool, int)) for v in empty.values()), str(empty))


def test_rebind():
    """build_rebind_suggestions re-filters the SAME result for a DIFFERENT profile, 0-token A+B only."""
    print("rebind (cross-profile re-filter against a fixed result)")
    # chart/scheduler/flow only enabled for rag_focused; dashboard for both.
    install_components(
        ["chart", "dashboard", "scheduler", "flow"],
        enabled_for_map={"chart": ["rag_focused"],
                         "dashboard": ["tool_enabled", "rag_focused"],
                         "scheduler": ["rag_focused"],
                         "flow": ["rag_focused"]})
    flags = {"tabular": True, "rows": 3, "has_chart": False,
             "has_canvas": False, "has_code": False, "has_kg": False}
    optim = {"id": "o", "profile_type": "tool_enabled", "componentConfig": {},
             "recommenderConfig": {"enabled": True, "tiers": ["action"], "maxSuggestions": 8}}
    focus = {"id": "f", "profile_type": "rag_focused", "componentConfig": {},
             "recommenderConfig": {"enabled": True, "tiers": ["action"], "maxSuggestions": 8}}
    lo = [c["label"] for c in run(recommender.build_rebind_suggestions(optim, "u", "q", flags))]
    lf = [c["label"] for c in run(recommender.build_rebind_suggestions(focus, "u", "q", flags))]
    check("rebind: Optimize → dashboard only (its class)", lo == ["Pin to dashboard"], str(lo))
    check("rebind: Focus → chart+dashboard+schedule+flow (its class)",
          set(lf) == {"Chart this", "Pin to dashboard", "Schedule this", "Build a flow"}, str(lf))

    # Tier C must NEVER run on a profile toggle, even if the profile opts in.
    install_llm('["should not appear"]')
    install_rag([])
    cprof = {"id": "c", "profile_type": "rag_focused", "llmConfigurationId": "llm1",
             "autocompleteCollections": ["*"], "componentConfig": {},
             "recommenderConfig": {"enabled": True, "tiers": ["llm"],
                                   "maxSuggestions": 3, "llmEnrichment": True}}
    lc = [c["label"] for c in run(recommender.build_rebind_suggestions(
        cprof, "u", "q", {"tabular": False, "rows": 0}))]
    check("rebind never fires Tier C", lc == [], str(lc))
    uninstall_llm()
    uninstall_rag()

    # Gates honored: recommender-disabled / global-off → empty.
    off = dict(optim, recommenderConfig={"enabled": False})
    check("rebind respects profile gate", run(recommender.build_rebind_suggestions(off, "u", "q", flags)) == [])
    check("rebind no profile → empty", run(recommender.build_rebind_suggestions(None, "u", "q", flags)) == [])
    install_components([])


def test_emit_rebindable():
    """build_and_emit_suggestions carries profile_id + rebindable and stashes rebind context."""
    print("emit: profile_id + rebindable + context stash (empty-but-rebindable)")
    install_components([])       # no action chips → this profile produces nothing
    install_rag([])              # no follow-ups either
    ops = []
    sm = types.ModuleType("trusted_data_agent.core.session_manager")

    async def set_turn_rebind_context(u, s, t, c):
        ops.append(("ctx", t, c)); return True
    sm.set_turn_rebind_context = set_turn_rebind_context
    sys.modules["trusted_data_agent.core.session_manager"] = sm
    import trusted_data_agent.core as core_pkg
    core_pkg.session_manager = sm

    captured = []

    async def handler(data, event):
        captured.append((event, data))

    profile = {"id": "P", "profile_type": "tool_enabled", "autocompleteCollections": ["*"],
               "recommenderConfig": {"enabled": True, "tiers": ["action", "followup"], "maxSuggestions": 4}}
    pay = {"final_answer": "<p>hi</p>", "final_answer_text": "chat", "collected_data": {}, "turn_id": 3}
    run(recommender.build_and_emit_suggestions("u", "s1", "@OPTIM hi", profile, pay, 3, handler))

    notif = [d for e, d in captured if e == "notification" and d.get("type") == "next_step_suggestions"]
    check("emits even when empty-but-rebindable", len(notif) == 1, str(captured))
    p = notif[0]["payload"]
    check("payload carries profile_id", p.get("profile_id") == "P", str(p))
    check("payload rebindable=True", p.get("rebindable") is True, str(p))
    check("payload items empty (this profile)", p.get("items") == [], str(p))
    check("rebind context stashed on the turn", any(o[0] == "ctx" and o[1] == 3 for o in ops), str(ops))
    stashed = ops[-1][2] if ops else {}
    check("stashed context has flags + (tag-stripped) query",
          "flags" in stashed and stashed.get("query") == "hi", str(stashed))

    sys.modules.pop("trusted_data_agent.core.session_manager", None)
    uninstall_rag()
    install_components([])


if __name__ == "__main__":
    print("=== Next-Step Recommender module tests ===")
    test_helpers()
    test_config_merge()
    test_tier_a()
    test_enablement_governance()
    test_gating()
    test_tier_b()
    test_tier_b_access_scope()
    test_tier_ordering_and_fill()
    test_tier_c()
    test_tier_c_observability()
    test_emit_contract()
    test_derive_flags()
    test_rebind()
    test_emit_rebindable()
    print(f"\n{_passed} passed, 0 failed  ({_passed} assertions)")
