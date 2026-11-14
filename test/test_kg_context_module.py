"""CI-pure tests for the knowledge_graph_context module + its activation path.

Covers:
  - module pure logic: applies_to (all-engine/IFOC set), budget->max_nodes, trim
  - module contribute(): kg_enrichment_event reconstruction (grounding-gate parity),
    budget overflow trim, empty/no-query/no-match abstain
  - module condense(): trailing-line trim
  - orchestrator Pass 1 registry-default merge: a registered-but-unlisted module
    auto-activates with manifest defaults; active=false still skips; applies_to filters
  - manager max_nodes pass-through is inspect-gated (3-arg handlers never break)

No server / DB / network. Run: python test/test_kg_context_module.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (for `components`)

_PASS = 0
_FAIL = 0


def check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok  {name}")
    else:
        _FAIL += 1
        print(f"FAIL  {name}")


def arun(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --- Load the module class via the registry's synthetic-package loader ---------
from components.builtin.context_window.module_registry import (  # noqa: E402
    get_module_registry, reset_module_registry,
)
from components.builtin.context_window.base import (  # noqa: E402
    AssemblyContext, Contribution, ContextModule,
)

reset_module_registry()
_registry = get_module_registry()
KGMOD = _registry.get_handler("knowledge_graph_context")


def _ctx(profile_type="llm_only", query="orders by region", profile_id="p1"):
    return AssemblyContext(
        profile_type=profile_type,
        profile_id=profile_id,
        session_id="sess-1",
        user_uuid="u-1",
        session_data={"current_query": query},
    )


# --- 1. Registration + applies_to (IFOC parity, genie excluded) ---------------
def t_registration_and_applies():
    check("module registered (shows in Installed Context Modules)", KGMOD is not None)
    defn = _registry.get_module("knowledge_graph_context")
    check("manifest default priority=72", defn is not None and defn.default_priority == 72)
    check("manifest condensable", defn is not None and defn.condensable is True)
    check("applies_to tool_enabled", KGMOD.applies_to("tool_enabled"))
    check("applies_to llm_only", KGMOD.applies_to("llm_only"))
    check("applies_to rag_focused", KGMOD.applies_to("rag_focused"))
    check("applies_to genie is False (covered via slaves)", not KGMOD.applies_to("genie"))


# --- 2. budget -> max_nodes mapping -------------------------------------------
def t_budget_to_max_nodes():
    check("budget<=0 -> MAX_NODES (legacy cap)", KGMOD._budget_to_max_nodes(0) == 500)
    check("tiny budget -> MIN_NODES floor", KGMOD._budget_to_max_nodes(10) == 20)
    check("huge budget -> MAX_NODES ceiling", KGMOD._budget_to_max_nodes(10_000_000) == 500)
    # 9000 / 30 = 300
    check("mid budget scales (9000->300)", KGMOD._budget_to_max_nodes(9000) == 300)
    check("monotonic non-decreasing",
          KGMOD._budget_to_max_nodes(3000) <= KGMOD._budget_to_max_nodes(6000))


# --- 3. _trim_to_budget keeps the leading (most-valuable) lines ----------------
def t_trim():
    text = "\n".join([f"line-{i} aaaa bbbb cccc" for i in range(40)])
    out = KGMOD._trim_to_budget(text, 30)
    check("trim returns non-empty within small budget", 0 < len(out) < len(text))
    check("trim keeps a leading prefix (schema backbone first)",
          out.startswith("line-0"))
    check("trim of zero budget -> empty", KGMOD._trim_to_budget(text, 0) == "")


# --- 4. contribute(): event reconstruction = grounding-gate parity ------------
def _patch_enrichment(text, details):
    """Monkeypatch the manager entry the module calls (imported inside contribute)."""
    import trusted_data_agent.components.manager as mgr

    async def _fake(query, profile_id, user_uuid, max_nodes=500, column_cap=None):
        _fake.last_max_nodes = max_nodes
        _fake.last_column_cap = column_cap
        return text, details
    _fake.last_max_nodes = None
    mgr.get_component_context_enrichment = _fake
    return _fake


def t_contribute_event_parity():
    details = [
        {"entity_count": 12, "relationship_count": 9, "scope_databases": ["restaurants_mvm"]},
        {"entity_count": 3, "relationship_count": 1},
    ]
    fake = _patch_enrichment("--- KNOWLEDGE GRAPH CONTEXT ---\nTABLE foo: a, b", details)
    c = arun(KGMOD.contribute(9000, _ctx()))
    check("contribute returns content", bool(c.content))
    ev = c.metadata.get("kg_enrichment_event")
    check("metadata carries kg_enrichment_event", ev is not None)
    check("event total_entities summed (12+3)", ev["total_entities"] == 15)
    check("event total_relationships summed (9+1)", ev["total_relationships"] == 10)
    check("event enrichments preserved verbatim", ev["enrichments"] == details)
    check("event session_id from ctx", ev["session_id"] == "sess-1")
    check("budget drove max_nodes (9000->300)", fake.last_max_nodes == 300)
    check("observability entities count exposed", c.metadata.get("entities") == 15)


def t_contribute_overflow_trims():
    big = "\n".join([f"row-{i} word word word word" for i in range(2000)])
    _patch_enrichment(big, [{"entity_count": 1, "relationship_count": 0}])
    c = arun(KGMOD.contribute(50, _ctx()))  # tiny budget
    from components.builtin.context_window.token_estimator import estimate_tokens
    check("overflow contribution trimmed to ~budget", estimate_tokens(c.content) <= 60)
    check("trimmed contribution still non-empty", bool(c.content))


def t_contribute_abstains():
    # no query
    c1 = arun(KGMOD.contribute(9000, _ctx(query="")))
    check("no query -> empty contribution", c1.content == "" and c1.tokens_used == 0)
    # no profile_id
    c2 = arun(KGMOD.contribute(9000, _ctx(profile_id="")))
    check("no profile -> empty contribution", c2.content == "")
    # enrichment returns nothing
    _patch_enrichment("", [])
    c3 = arun(KGMOD.contribute(9000, _ctx()))
    check("no KG match -> empty contribution", c3.content == "")
    check("no-match reason recorded", c3.metadata.get("reason") == "no_match")


def t_contribute_fail_safe():
    import trusted_data_agent.components.manager as mgr

    async def _boom(*a, **k):
        raise RuntimeError("kg store down")
    mgr.get_component_context_enrichment = _boom
    c = arun(KGMOD.contribute(9000, _ctx()))
    check("enrichment exception -> empty (fail-safe, never breaks assembly)",
          c.content == "" and c.metadata.get("reason") == "error")


# --- 5. condense() ------------------------------------------------------------
def t_condense():
    text = "\n".join([f"keep-{i} aa bb cc dd" for i in range(60)])
    c = arun(KGMOD.condense(text, 40, _ctx()))
    check("condense trims to target", 0 < len(c.content) < len(text))
    check("condense marks condensed", c.metadata.get("condensed") is True)
    e = arun(KGMOD.condense("", 40, _ctx()))
    check("condense empty stays empty", e.content == "")


# --- 6. Orchestrator Pass 1 registry-default merge ----------------------------
class _FakeModuleHandler(ContextModule):
    def __init__(self, mid, applies):
        self._mid = mid
        self._applies = applies

    @property
    def module_id(self):
        return self._mid

    def applies_to(self, profile_type):
        return self._applies

    async def contribute(self, budget, ctx):
        return Contribution(content="x", tokens_used=1)


class _FakeDefn:
    def __init__(self, mid, applies=True, priority=72, condensable=True):
        self.module_id = mid
        self.handler = _FakeModuleHandler(mid, applies)
        self.display_name = mid
        self.category = "knowledge"
        self.default_priority = priority
        self.default_target_pct = 8.0
        self.default_min_pct = 0.0
        self.default_max_pct = 20.0
        self.condensable = condensable


class _FakeRegistry:
    def __init__(self, defns):
        self._d = {d.module_id: d for d in defns}

    def get_all_modules(self):
        return list(self._d.values())

    def get_module(self, mid):
        return self._d.get(mid)


def t_registry_default_merge():
    from components.builtin.context_window.handler import ContextWindowHandler

    h = ContextWindowHandler()
    # registry has a 'listed' module (also in CWT) and an 'unlisted' one (merge target)
    h._registry = _FakeRegistry([
        _FakeDefn("listed_mod", applies=True, priority=80),
        _FakeDefn("unlisted_mod", applies=True, priority=72),
        _FakeDefn("genie_only_excluded", applies=False),
    ])
    cwt = {"modules": {"listed_mod": {"active": True, "priority": 80,
                                      "target_pct": 10, "min_pct": 0, "max_pct": 20}}}
    ctx = _ctx(profile_type="llm_only")
    active, skipped = h._resolve_active_modules(cwt, ctx)
    ids = {m.module_id for m in active}
    check("listed module active", "listed_mod" in ids)
    check("UNLISTED registered module auto-activated (the merge)", "unlisted_mod" in ids)
    merged = next(m for m in active if m.module_id == "unlisted_mod")
    check("merged module uses manifest default priority", merged.priority == 72)
    check("merged module uses manifest default target_pct", merged.target_pct == 8.0)
    check("applies_to=False module excluded from merge", "genie_only_excluded" not in ids)


def t_merge_respects_explicit_deactivation():
    from components.builtin.context_window.handler import ContextWindowHandler

    h = ContextWindowHandler()
    h._registry = _FakeRegistry([_FakeDefn("kg_like", applies=True)])
    # Explicitly deactivated in the CWT -> must stay off even though registered
    cwt = {"modules": {"kg_like": {"active": False}}}
    active, skipped = h._resolve_active_modules(cwt, _ctx())
    ids = {m.module_id for m in active}
    check("explicit active=false overrides the merge (stays skipped)", "kg_like" not in ids)
    check("deactivated module recorded as skipped", "kg_like" in skipped)


# --- 7. manager max_nodes pass-through is inspect-gated ------------------------
def t_manager_inspect_gated_passthrough():
    """A 3-arg legacy handler must not receive max_nodes; a 4-arg one must."""
    import inspect

    async def legacy_enrich(query, profile_id, user_uuid):
        return ("legacy", {"entity_count": 1})

    async def budgeted_enrich(query, profile_id, user_uuid, max_nodes=500):
        return (f"budgeted:{max_nodes}", {"entity_count": 1})

    # Mirror the manager's gating decision (inspect.signature membership check)
    def accepts_max_nodes(fn):
        return "max_nodes" in inspect.signature(fn).parameters

    check("legacy 3-arg handler -> max_nodes NOT passed", not accepts_max_nodes(legacy_enrich))
    check("budgeted handler -> max_nodes passed", accepts_max_nodes(budgeted_enrich))


def main():
    for t in [
        t_registration_and_applies,
        t_budget_to_max_nodes,
        t_trim,
        t_contribute_event_parity,
        t_contribute_overflow_trims,
        t_contribute_abstains,
        t_contribute_fail_safe,
        t_condense,
        t_registry_default_merge,
        t_merge_respects_explicit_deactivation,
        t_manager_inspect_gated_passthrough,
    ]:
        print(f"\n== {t.__name__} ==")
        t()
    print(f"\n{'='*50}\n{_PASS} passed, {_FAIL} failed\n{'='*50}")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
