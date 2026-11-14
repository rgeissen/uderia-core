"""
Module tests — Agent Authority / the Agent Principal (Tier 5 Step 4 / P3).

NOTE: an agent's *identity* is its Profile; this module is its *authority* (what it may do
autonomously, on whose behalf) — see agent_authority.py. Persistence key: authorityConfig
(legacy identityConfig is still read for backward compatibility — asserted below).

CI-pure. Asserts: baseline (no config = unrestricted), the subset invariant (a scope only
restricts), the enforcement decision (denied tools / deny-writes / connector allow-list /
budget), actor stamps, and scope parsing.

Run:  python test/test_agent_authority.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

from trusted_data_agent.core.agent_authority import (  # noqa: E402
    AgentScope, AgentPrincipal, parse_scope, derive_principal,
    is_tool_allowed, is_run_within_budget, user_actor, AUTONOMOUS_ORIGINS,
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def main():
    print("=" * 70)
    print("Agent Authority (Tier 5 Step 4 / P3) — module tests")
    print("=" * 70)

    # ── baseline: no identityConfig → unrestricted, behaves as today ──
    base = parse_scope(None)
    check("no config → autonomous", base.autonomous is True)
    check("no config → not restricted", base.is_restricted is False)
    ok, _ = is_tool_allowed(base, "base_writeQuery")
    check("baseline allows any tool (incl. writes)", ok is True)
    check("empty dict → baseline too", parse_scope({}).is_restricted is False)

    # ── the reported scope must agree with the enforced one ─────────────────
    # Every surface that says what an agent may do reads these; a UI that instead
    # counted "authorityConfig is a non-empty dict" reported an agent with autonomy
    # on and zero constraints as *bounded*, which inverts the meaning of the badge.
    from trusted_data_agent.core.agent_authority import describe_scope, scope_for_profile
    empty_but_present = {"autonomous": True, "denied_tools": [], "deny_writes": False,
                         "allowed_connectors": None, "max_cost_per_run": None}
    sc = parse_scope(empty_but_present)
    check("a present-but-empty authorityConfig is NOT restricted", sc.is_restricted is False)
    check("...and describes as no constraints", describe_scope(sc) == [])
    check("description is empty exactly when unrestricted",
          bool(describe_scope(sc)) == sc.is_restricted)

    hardened = parse_scope({"deny_writes": True, "denied_tools": ["a", "b"],
                            "allowed_connectors": [], "max_cost_per_run": 0})
    d = describe_scope(hardened)
    check("read-only is described", "read-only" in d)
    check("denied tools are counted", "2 tools denied" in d)
    check("an empty connector allow-list reads as deny-all", "no connectors" in d)
    check("a $0 cap is a real cap, not 'no cap'", "$0/run cap" in d)
    check("description non-empty ⇔ restricted",
          bool(d) == hardened.is_restricted is True)
    check("autonomous:false is itself a constraint",
          describe_scope(parse_scope({"autonomous": False})) == ["no unattended use"])

    check("scope_for_profile reads authorityConfig",
          scope_for_profile({"authorityConfig": {"deny_writes": True}}).deny_writes is True)
    check("scope_for_profile falls back to the legacy identityConfig",
          scope_for_profile({"identityConfig": {"deny_writes": True}}).deny_writes is True)
    check("scope_for_profile on a bare profile → baseline",
          scope_for_profile({}).is_restricted is False)

    # ── denied_tools ──
    s = parse_scope({"denied_tools": ["base_writeQuery", "dba_dropTable"]})
    check("scope with denied_tools is restricted", s.is_restricted is True)
    ok, why = is_tool_allowed(s, "base_writeQuery")
    check("denied tool → not allowed", ok is False and "denied" in why)
    ok, _ = is_tool_allowed(s, "base_readQuery")
    check("non-denied tool → allowed", ok is True)

    # ── deny_writes (heuristic write detection) ──
    w = parse_scope({"deny_writes": True})
    check("deny_writes blocks a write tool", is_tool_allowed(w, "base_writeQuery")[0] is False)
    check("deny_writes blocks insert/update", is_tool_allowed(w, "sales_insertRow")[0] is False)
    check("deny_writes allows a read tool", is_tool_allowed(w, "base_readQuery")[0] is True)
    check("deny_writes never gates framework TDA_ tools", is_tool_allowed(w, "TDA_FinalReport")[0] is True)
    check("explicit is_write=False overrides the heuristic", is_tool_allowed(w, "base_writeQuery", is_write=False)[0] is True)

    # ── deny_writes reads the SQL, not just the tool name (GATE-H3) ──
    # The flagship hole: a mutation smuggled through a read-named tool.
    from trusted_data_agent.agent.tool_enforcement import classify_sql_write, check_authority
    from trusted_data_agent.agent import tool_enforcement as _te
    from trusted_data_agent.core import agent_authority as _aa
    check("classify: read SQL → None", classify_sql_write("base_readQuery", {"sql": "SELECT * FROM t"}) is None)
    check("classify: DELETE via read-named tool → True",
          classify_sql_write("base_readQuery", {"sql": "DELETE FROM orders"}) is True)
    check("classify: no sql arg → None", classify_sql_write("base_readQuery", {"foo": 1}) is None)
    check("classify: batch write → True",
          classify_sql_write("base_readQuery", {"sql": "SELECT 1 FROM x; DROP TABLE t"}) is True)
    check("classify: TDA_ exempt → None", classify_sql_write("TDA_FinalReport", {"sql": "DELETE FROM t"}) is None)
    # A write NESTED in a read-shaped statement (found 2026-09-24): a Postgres data-modifying
    # CTE is a SELECT at the top and a DELETE inside; SELECT … INTO creates a table. Both passed
    # the top-level check, so `deny_writes` could be bypassed through them.
    check("classify: data-modifying CTE → True",
          classify_sql_write("base_readQuery", {"sql": "WITH gone AS (DELETE FROM t RETURNING 1) SELECT COUNT(*) FROM gone"}) is True)
    check("classify: SELECT … INTO → True",
          classify_sql_write("base_readQuery", {"sql": "SELECT * INTO new_t FROM t"}) is True)
    check("classify: a read CTE stays a read",
          classify_sql_write("base_readQuery", {"sql": "WITH a AS (SELECT 1) SELECT * FROM a"}) is None)
    check("classify: a scalar subquery stays a read",
          classify_sql_write("base_readQuery", {"sql": "SELECT a, (SELECT MAX(b) FROM u) FROM t"}) is None)
    # End-to-end through the real enforcement seam, under an autonomous deny_writes principal.
    _tok = _aa.set_current_principal(_aa.AgentPrincipal(
        owner_uuid="u", profile_id="p", scope=parse_scope({"deny_writes": True})))
    try:
        ok_read, _ = check_authority("base_readQuery", arguments={"sql": "SELECT * FROM orders"})
        ok_del, why_del = check_authority("base_readQuery", arguments={"sql": "DELETE FROM orders"})
        check("deny_writes: SELECT via read tool passes", ok_read is True)
        check("deny_writes: DELETE via read tool DENIED (name-deep hole closed)", ok_del is False)
    finally:
        _aa.reset_current_principal(_tok)

    # ── connector allow-list ──
    c = parse_scope({"allowed_connectors": ["uderia-google"]})
    check("connector in allow-list → allowed", is_tool_allowed(c, "uderia-google", is_connector=True)[0] is True)
    check("connector not in allow-list → denied", is_tool_allowed(c, "uderia-slack", is_connector=True)[0] is False)
    check("allow-list does not gate non-connector tools", is_tool_allowed(c, "base_readQuery")[0] is True)

    # ── autonomous off = can't act at all ──
    n = parse_scope({"autonomous": False})
    check("autonomous:false → any action denied", is_tool_allowed(n, "base_readQuery")[0] is False)
    check("autonomous:false → is_restricted", n.is_restricted is True)

    # ── budget ──
    b = parse_scope({"max_cost_per_run": 0.05})
    check("under budget → ok", is_run_within_budget(b, 0.02)[0] is True)
    check("over budget → denied", is_run_within_budget(b, 0.09)[0] is False)
    check("no cap → always ok", is_run_within_budget(base, 999.0)[0] is True)

    # ── subset invariant: a scope NEVER grants — only the listed restriction fields exist ──
    check("scope has no 'grant'/'allow-extra' field",
          not any(k.startswith(("grant", "extra", "elevate")) for k in AgentScope().to_dict()))

    # ── principal + actor stamps (config key: authorityConfig) ──
    prof = {"id": "profile-abc", "authorityConfig": {"deny_writes": True}}
    p = derive_principal(prof, "owner-1")
    check("principal id = agent:<profile>", p.principal_id == "agent:profile-abc")
    check("principal carries owner", p.owner_uuid == "owner-1")
    check("principal scope parsed", p.scope.deny_writes is True)

    # ── backward-compat: legacy identityConfig is still honored; authorityConfig wins ──
    legacy = derive_principal({"id": "p", "identityConfig": {"deny_writes": True}}, "o")
    check("legacy identityConfig still read", legacy.scope.deny_writes is True)
    both = derive_principal(
        {"id": "p", "authorityConfig": {"deny_writes": True}, "identityConfig": {"deny_writes": False}}, "o")
    check("authorityConfig takes precedence over legacy identityConfig", both.scope.deny_writes is True)
    act = p.actor()
    check("agent actor type = agent", act["type"] == "agent" and act["principal_id"] == "agent:profile-abc")
    ua = user_actor("owner-1")
    check("user actor type = user", ua["type"] == "user" and ua["principal_id"] == "user:owner-1")
    check("agent + user actors are distinguishable", act["type"] != ua["type"])

    # ── autonomous origins set ──
    check("genie_child is an autonomous origin", "genie_child" in AUTONOMOUS_ORIGINS)
    check("scheduler is an autonomous origin", "scheduler" in AUTONOMOUS_ORIGINS)
    check("a plain user turn origin (None) is not autonomous", None not in AUTONOMOUS_ORIGINS)

    # ── the per-turn security context + fail-closed enforcement ──
    from trusted_data_agent.core.agent_authority import (
        set_current_principal, get_current_principal, reset_current_principal,
        enforce_tool, denied_tool_result,
    )
    check("no ambient principal → enforce allows (baseline)", enforce_tool("base_writeQuery")[0] is True)
    check("get_current_principal is None by default", get_current_principal() is None)

    restricted = AgentPrincipal("owner-1", "profile-x", parse_scope({"denied_tools": ["base_writeQuery"]}))
    tok = set_current_principal(restricted)
    try:
        check("ambient principal is read", get_current_principal() is restricted)
        check("enforce denies the scoped tool", enforce_tool("base_writeQuery")[0] is False)
        check("enforce allows a permitted tool", enforce_tool("base_readQuery")[0] is True)
    finally:
        reset_current_principal(tok)
    check("principal reset clears the context", get_current_principal() is None)
    check("after reset, enforce allows again (baseline)", enforce_tool("base_writeQuery")[0] is True)

    dr = denied_tool_result("base_writeQuery", "denied to this agent")
    check("denial result flags agent_denied", dr["metadata"]["agent_denied"] is True)
    check("denial result is terminal (no retry)", dr["metadata"]["terminal"] is True)
    check("denial result is an error status", dr["status"] == "error")
    check("a CATEGORICAL denial ends with report-this-limitation",
          "Do not retry; report this limitation" in dr["results"][0]["agent_policy"])

    # OBDA-READ Phase C4 — a SURFACE denial has a correction path, so it is RECOVERY-
    # SHAPED: the live acceptance proved the blanket "Do not retry; report this
    # limitation" made the model obey the stop clause over the fix, three
    # identical retries then a give-up, query_product never called.
    from trusted_data_agent.core.agent_authority import SURFACE_DENIAL_MARKER
    sd = denied_tool_result(
        "base_readQuery",
        f"this agent is {SURFACE_DENIAL_MARKER} — raw SQL is denied; use ...")
    sline = sd["results"][0]["agent_policy"]
    check("a surface denial spells out the NEXT STEP", "NEXT STEP" in sline)
    check("the next step names query_product", "query_product" in sline)
    check("the surface denial does NOT tell the model to give up",
          "report this limitation" not in sline)
    check("the surface denial is still terminal + audited",
          sd["metadata"]["agent_denied"] is True and sd["metadata"]["terminal"] is True)
    check("the E1 reason carries the canonical marker (one declaration)",
          SURFACE_DENIAL_MARKER == "surface-only")

    # ACT B4d — the WRITE surface's denial is recovery-shaped too, and names TDA_Action
    # CONDITIONALLY (the action component may be off for this agent).
    import trusted_data_agent.core.agent_authority as _AA
    ACTION_DENIAL_MARKER = _AA.ACTION_DENIAL_MARKER
    ok_, why = _AA.is_tool_allowed(_AA.AgentScope(actions_allowed=frozenset({"set_tier"}), actions_exclusive=True),
                               "base_readQuery", is_write=True)
    check("an action-only scope denies a raw write with the canonical marker", ok_ is False and ACTION_DENIAL_MARKER in why)
    aline = denied_tool_result("base_readQuery", why)["results"][0]["agent_policy"]
    check("the action-only denial spells out the NEXT STEP through a signed action",
          "NEXT STEP" in aline and "set_tier" in aline and "TDA_Action" in aline)
    check("…naming TDA_Action conditionally, with what to do when it is not offered",
          "if it is offered to you" in aline and "If TDA_Action is not offered" in aline)
    check("…and does NOT tell the model to give up", "report this limitation" not in aline)

    # ── the architectural proof: contextvars propagate across awaits AND isolate
    #    concurrent turns (the non-racy, all-engine basis of the design) ──
    import asyncio
    from trusted_data_agent.core.agent_authority import (
        set_current_principal, reset_current_principal, enforce_tool as _enf, get_current_principal as _gcp)

    async def _tool_call(name):          # mimics a tool chokepoint reading the ambient principal
        await asyncio.sleep(0)           # cross a real await boundary
        return _enf(name)[0]

    async def _turn(denied_tool, probe):  # mimics run_agent_execution setting the principal
        p = AgentPrincipal("u", "prof", parse_scope({"denied_tools": [denied_tool]}))
        tok = set_current_principal(p)
        try:
            return await _tool_call(probe)
        finally:
            reset_current_principal(tok)

    async def _main():
        return await asyncio.gather(
            _turn("toolA", "toolA"),   # denies its own scoped tool
            _turn("toolB", "toolA"),   # different scope — must NOT deny toolA
            _turn("toolA", "toolB"))   # different probe — must NOT deny toolB

    res = asyncio.run(_main())
    check("principal propagates across an await → denies its scoped tool", res[0] is False)
    check("concurrent turn with a different scope is isolated (not denied)", res[1] is True)
    check("concurrent turn 3 is isolated too", res[2] is True)
    check("no principal leaks after the turns", _gcp() is None)

    print("\n" + "=" * 70)
    t_budget_is_enforced_in_flight()
    t_authority_stamp_reaches_the_turn()
    t_enforced_interactive_reads_interactive()

    # ── OBDA-READ IS ADDITIVE: the master gates the data surface at the ONE parse seam
    # (operator-restated design imperative, after session d51cf073: with the master
    # off the enrichment still cut to contracts and the agent face still compiled —
    # the surface machinery had been added in later phases without the gate).
    print("\n[obda-additive] master off ⇒ the data surface does not exist")
    from trusted_data_agent.core.config import APP_CONFIG as _CFG
    _SURF_CFG = {"dataSurface": {"products": ["support_load"], "exclusive": True},
                 "deny_writes": True, "denied_tools": ["bad_tool"]}
    _prev = _OP.plane_available("read")
    try:
        _OP.force("read", False)
        sc = parse_scope(_SURF_CFG, planes={"read": False, "model": True, "act": True})
        check("master OFF: surface dropped at parse", sc.data_surface_products is None
              and sc.data_surface_exclusive is False)
        check("master OFF: NON-OBDA-READ authority survives (deny_writes, denied_tools)",
              sc.deny_writes is True and "bad_tool" in sc.denied_tools)
        _OP.force("read", True)
        sc2 = parse_scope(_SURF_CFG)
        check("master ON: surface parses exactly as declared",
              sc2.data_surface_products == frozenset({"support_load"})
              and sc2.data_surface_exclusive is True)
    finally:
        _OP.force("read", _prev)

    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


def t_budget_is_enforced_in_flight():
    """`max_cost_per_run` was declared, stored, surfaced in the editor — and enforced only
    POST-HOC. A budget checked once the money is spent is an audit record wearing a
    control's name (symmetry map Part D).

    The in-flight half uses the SAME predicate as the post-hoc record. A cap that can be
    read two ways is a cap nobody can reason about.
    """
    import asyncio
    from trusted_data_agent.core import agent_authority as A
    from trusted_data_agent.agent.executor import (
        PlanExecutor, AgentBudgetExceededError)

    class _Fake(PlanExecutor):
        def __init__(self, spent):
            self.turn_cost_usd = spent
            self.disabled_history = False

    def _principal(cap):
        return A.AgentPrincipal(owner_uuid="u", profile_id="p",
                                scope=A.parse_scope({"autonomous": True,
                                                     "max_cost_per_run": cap}))

    async def _call(spent, cap, autonomous=True):
        tok = A.set_current_principal(_principal(cap)) if autonomous else None
        try:
            await _Fake(spent)._call_llm_and_update_tokens("p", "r")
            return "called"
        except AgentBudgetExceededError:
            return "stopped"
        except Exception:
            return "called"      # got past the guard, failed later on the stub
        finally:
            if tok:
                A.reset_current_principal(tok)

    check("an autonomous run over its cap is STOPPED before the call is paid for",
          asyncio.run(_call(0.09, 0.05)) == "stopped")
    check("…and under its cap proceeds", asyncio.run(_call(0.01, 0.05)) == "called")
    # Byte-identical where no cap is configured, and on interactive turns — the scope
    # governs autonomous work, and cutting a user off mid-answer is not what a per-run
    # budget was for.
    check("no cap configured → no ceiling", asyncio.run(_call(99.0, None)) == "called")
    check("an interactive turn is never capped",
          asyncio.run(_call(99.0, 0.05, autonomous=False)) == "called")

    # ONE predicate, two moments.
    check("the in-flight half delegates to the post-hoc predicate",
          "return is_run_within_budget(principal.scope, cost_so_far)"
          in __import__("inspect").getsource(A.check_budget_before_call))
    # A named exception: "the agent ran out of budget" and "the provider fell over" call
    # for opposite responses from an operator, so a trace must distinguish them.
    check("the stop is a NAMED failure, not a generic one",
          issubclass(AgentBudgetExceededError, RuntimeError)
          and AgentBudgetExceededError is not RuntimeError)


def t_enforced_interactive_reads_interactive():
    """An INTERACTIVE turn under an enforced principal names itself interactive.

    Regression (trust campaign, B3 re-run 2026-10-27): with `authorityConfig.enforce_interactive`
    the principal applies to a REST interactive turn, whose stamp carries no origin — and the
    reading filled the gap with "scheduler", so the operator read "autonomous run (scheduler)"
    on a turn they started by hand. The applied boundary is right (pass); the origin word is not.
    """
    import trusted_data_agent.core.agent_authority as A
    from trusted_data_agent.agent import assurance_points as ap

    principal = AgentPrincipal(owner_uuid="u1", profile_id="p1",
                               scope=A.parse_scope({"deny_writes": True}))
    r = ap._read_authority({"authority": A.build_authority_stamp(principal, None)}, "tool_enabled")
    check("an enforced interactive turn reads pass (the boundary was in force)", r.state == "pass")
    check("...and never claims a scheduler run", "scheduler" not in r.detail)
    check("...and never calls itself autonomous", "autonomous" not in r.detail.lower())
    check("...it says it is interactive, under the agent principal",
          "interactive" in r.detail and "agent principal" in r.detail)
    check("...and keeps the constraints", "read-only" in r.detail)
    # an autonomous origin keeps its own words
    auto = ap._read_authority({"authority": A.build_authority_stamp(principal, "scheduler")}, "tool_enabled")
    check("a scheduled run still reads 'autonomous run (scheduler)'",
          "autonomous run (scheduler)" in auto.detail)
    # a stamp whose origin is not an autonomous one is not reworded as one
    odd = ap._read_authority({"authority": A.build_authority_stamp(principal, "rest")}, "tool_enabled")
    check("a non-autonomous origin is never called autonomous", "autonomous" not in odd.detail.lower())

    # ── limits the agent HAS but an interactive turn ran WITHOUT are named (B3 product item):
    # a `deny_writes` agent's chat turn read "acting as the user", as though nothing was configured
    restricted = A.parse_scope({"deny_writes": True, "denied_tools": ["base_writeQuery"]})
    inert = A.build_authority_stamp(None, None, configured=restricted)
    check("an interactive stamp carries the agent's limits as NOT in force", inert.get("not_in_force", {}).get("deny_writes") is True)
    check("…and still says the boundary was not applied", inert["applied"] is False)
    ri = ap._read_authority({"authority": inert}, "conversation_with_tools")
    check("the reading stays na — the turn ran as the user, no alarm invented", ri.state == "na")
    check("…and names the limits that were not in force",
          "NOT in force" in ri.detail and "read-only" in ri.detail and "1 tool denied" in ri.detail)
    check("…and names the switch that would apply them", "Apply to interactive turns too" in ri.detail)
    check("…with its own state word", ri.word == "agent limits not in force")
    check("an unrestricted agent's interactive stamp is byte-identical to the baseline",
          A.build_authority_stamp(None, None, configured=A.parse_scope({})) == {"applied": False, "origin": None})
    check("no configured scope → baseline reading",
          ap._read_authority({"authority": A.build_authority_stamp(None, None)}, "llm_only").detail
          == "Authority: interactive turn — acting as the user")
    from trusted_data_agent.agent import execution_service as _es
    _esrc = __import__("inspect").getsource(_es)
    check("both stamp copies carry the configured scope", _esrc.count("configured=_configured_scope") == 2)


def t_authority_stamp_reaches_the_turn():
    """The BOUNDED reading must read a SEALED stamp, not re-derive from a key nobody writes.

    Regression: `_read_authority` keyed on `turn["origin"]`, which `execution_service`
    puts on the returned payload and on the user MESSAGE but never on the turn — so every
    autonomous turn reported "interactive — acting as the user" (0 of 1540 production
    turns carried it). Found on a live genie coordinator whose BOUNDED station read
    dashed while its `session_history` correctly recorded `origin='genie_parent'`.
    """
    import trusted_data_agent.core.agent_authority as A
    from trusted_data_agent.agent import assurance_points as ap

    scope = A.parse_scope({"deny_writes": True, "denied_tools": ["base_writeQuery"]})
    principal = AgentPrincipal(owner_uuid="u1", profile_id="p1", scope=scope)

    # ── ONE builder: both copies of the stamp are the same object shape ──
    auto = A.build_authority_stamp(principal, "genie_parent")
    inter = A.build_authority_stamp(None, None)
    check("autonomous stamp records that the boundary was in force", auto["applied"] is True)
    check("autonomous stamp carries its origin", auto["origin"] == "genie_parent")
    check("autonomous stamp carries the principal", auto["principal_id"] == "agent:p1")
    # `applied` is the load-bearing field: an absent stamp cannot distinguish
    # "the boundary held" from "the boundary was never in force".
    check("interactive stamp says the boundary was NOT in force", inter["applied"] is False)

    # ── the reading reads the stamp ──
    r = ap._read_authority({"authority": auto}, "genie")
    check("a stamped autonomous turn reads pass", r.state == "pass")
    check("the reading names the origin", "genie_parent" in r.detail)
    # The stamp carries the constraints; dropping them would report an agent that is
    # confined as though it held the owner's full authority.
    check("the reading names the constraints", "read-only" in r.detail)

    # The dropped-field trap, asserted THROUGH THE READING and not only on the inverse:
    # `deny_writes` survives either shape, so "read-only" above cannot catch a reader that
    # deserializes with the wrong one. `data_surface` is the field whose key differs, so it
    # is the only assertion that fails when the reading loses the surface.
    surf = A.build_authority_stamp(
        AgentPrincipal(owner_uuid="u1", profile_id="p1",
                       scope=AgentScope(data_surface_products=frozenset({"p1"}),
                                        data_surface_exclusive=True)),
        "scheduler")
    check("the reading names a surface-only confinement from the stamp",
          "surface-only" in ap._read_authority({"authority": surf}, "genie").detail)

    r2 = ap._read_authority({"authority": inter}, "genie")
    check("a stamped interactive turn still reads na", r2.state == "na")

    # ── any-era fallback: turns persisted before the stamp landed ──
    check("legacy origin-only turn still reads pass",
          ap._read_authority({"origin": "flow"}, "flow").state == "pass")
    check("a bare turn reads interactive, which is the honest reading",
          ap._read_authority({}, "genie").state == "na")

    # ── the round-trip that the FIRST version of this fix got wrong ──
    # `to_dict()` emits `data_surface`; `parse_scope` reads `dataSurface`, so routing a
    # stamp through parse_scope silently DROPPED the surface — the constraint that most
    # narrows an agent. The inverse must live beside to_dict and survive every field.
    full = A.parse_scope({
        "autonomous": False, "deny_writes": True, "denied_tools": ["a", "b"],
        "allowed_connectors": [], "max_cost_per_run": 0.25,
        "dataSurface": {"products": ["p1"], "exclusive": True},
    })
    import trusted_data_agent.core.config as _cfg_mod
    _prev = _OP._FORCED["read"]
    _OP.force("read", True)
    try:
        full = A.parse_scope({
            "autonomous": False, "deny_writes": True, "denied_tools": ["a", "b"],
            "allowed_connectors": [], "max_cost_per_run": 0.25,
            "dataSurface": {"products": ["p1"], "exclusive": True},
        })
        back = A.scope_from_stamp(full.to_dict())
        check("round-trip preserves the data surface (the dropped-field trap)",
              back.data_surface_products == full.data_surface_products
              and back.data_surface_exclusive is True)
        check("round-trip preserves every other field",
              (back.autonomous, back.deny_writes, back.denied_tools,
               back.allowed_connectors, back.max_cost_per_run)
              == (full.autonomous, full.deny_writes, full.denied_tools,
                  full.allowed_connectors, full.max_cost_per_run))
        check("round-trip preserves the description, so no surface can disagree",
              A.describe_scope(back) == A.describe_scope(full))
        # A stamp records what was IN FORCE. Reading it must not re-resolve against
        # today's master switch — that is retroactive falsification of a sealed fact.
        stamp = full.to_dict()
        _OP.force("read", False)
        check("a sealed surface survives the master switch being turned off later",
              A.scope_from_stamp(stamp).data_surface_exclusive is True)
    finally:
        _OP.force("read", _prev)

    # ── 0 is a real cap, not "no cap" (the `0 == False` trap, asserted on the inverse too)
    check("a $0 cap survives the round-trip",
          A.scope_from_stamp(A.parse_scope({"max_cost_per_run": 0}).to_dict())
          .max_cost_per_run == 0.0)

    # ── the stamp reaches the turn through ONE chokepoint, not five engines ──
    import inspect
    from trusted_data_agent.core import session_manager as _sm
    src = inspect.getsource(_sm.update_last_turn_data)
    check("the persistence chokepoint stamps the turn", "get_turn_authority()" in src)
    check("the chokepoint never overwrites a more specific stamp",
          'setdefault("authority"' in src)
    from trusted_data_agent.agent import execution_service as _es
    esrc = inspect.getsource(_es)
    check("the stamp is sealed before the engine runs", "set_turn_authority(" in esrc)
    check("the stamp is released in the finally, so it cannot leak to another turn",
          "reset_turn_authority(" in esrc)
    check("both copies come from the ONE builder",
          esrc.count("build_authority_stamp(") == 2)


if __name__ == "__main__":
    main()
