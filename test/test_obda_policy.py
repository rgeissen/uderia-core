"""The OBDA policy — "contracts only", one per plane — the gate.

Availability is the component's (test_act_admin_switch); the policy is what the deployment
REQUIRES of every agent once a plane exists: off (each agent's own authority), observe
(nothing refused, what enforce would refuse is recorded), enforce (reads only through signed
data products, model changes only through proposals a person publishes, row changes only
through signed actions; command tools refused). Applied ONCE at `derive_principal`; every
consumer of the scope inherits it.

  §1  the ladder and the one normalizer: unknown reads off, never enforce
  §2  apply_policy is pure and writes enforce INTO the scope; observe and off change no field
  §3  the chokepoint under enforce: raw SQL, raw writes and command tools refused, reads not
  §4  the chokepoint under observe: allowed, and a `policy_observed` event names the plane
  §5  the policy applies to EVERY turn (interactive included) and to the one derive seam
  §6  the knowledge-graph component refuses a direct edit under enforce, records under observe
  §7  the stamp: no policy key at off (the baseline hash), the planes under policy otherwise
  §8  the surfaces: admin route, startup loader, masters payload, the Trust panel, the station

Run: PYTHONPATH=src python3 test/test_obda_policy.py
"""
import asyncio
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)

import logging  # noqa: E402
logging.disable(logging.CRITICAL)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
def run(fn):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc()
        check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")
def src(p): return open(os.path.join(ROOT, p), encoding="utf-8").read()

from trusted_data_agent.core import obda_planes as P  # noqa: E402
from trusted_data_agent.core import agent_authority as A  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402

GRANTS = {"dataSurface": {"products": ["sales"], "exclusive": False}, "actions": {"types": ["close"]}}
ALL = {"read": True, "model": True, "act": True}


print("\n[§1] the ladder and the one normalizer")
def t_ladder():
    check("the policy ladder is off · observe · enforce", P.POLICY_LADDER == ("off", "observe", "enforce"))
    n = P.normalize_policy({"read": "ENFORCE", "act": "bogus", "nope": "enforce"})
    check("modes normalise (case), unknown reads OFF — never enforce; unknown planes are dropped",
          n == {"read": "enforce", "model": "off", "act": "off"}, str(n))
    check("a non-dict reads all off", P.normalize_policy(None) == {"read": "off", "model": "off", "act": "off"})
    saved = dict(getattr(APP_CONFIG, "OBDA_POLICY", {}))
    try:
        P.set_policy({"act": "observe"})
        check("set_policy writes the normalised map onto the live config", APP_CONFIG.OBDA_POLICY == {"read": "off", "model": "off", "act": "observe"})
        check("policy() reads it; policy_active() sees the observed plane", P.policy("act") == "observe" and P.policy("read") == "off" and P.policy_active())
        P.set_policy({})
        check("all off ⇒ not active", not P.policy_active())
    finally:
        APP_CONFIG.OBDA_POLICY = saved
run(t_ladder)


print("\n[§2] apply_policy is pure and writes enforce INTO the scope")
def t_apply():
    sc = A.parse_scope(GRANTS, planes=ALL)
    off = A.apply_policy(sc, {"read": "off", "model": "off", "act": "off"})
    check("all off returns the scope unchanged (identity)", off is sc)
    e = A.apply_policy(sc, {"read": "enforce", "model": "enforce", "act": "enforce"})
    check("read enforce: an EXCLUSIVE surface over the granted products", e.data_surface_exclusive and not e.data_surface_advisory and e.data_surface_products == frozenset({"sales"}))
    check("act enforce: an EXCLUSIVE write surface over the granted actions", e.actions_exclusive and e.actions_allowed == frozenset({"close"}))
    check("the modes are recorded on the scope", (e.policy_read, e.policy_model, e.policy_act) == ("enforce", "enforce", "enforce"))
    check("an enforced policy is a restriction", e.is_restricted)
    bare = A.apply_policy(A.parse_scope({}, planes=ALL), {"read": "enforce", "model": "off", "act": "enforce"})
    check("no grants + enforce ⇒ EMPTY surfaces: nothing readable but through a product, nothing writable but through an action",
          bare.data_surface_products == frozenset() and bare.data_surface_exclusive and bare.actions_allowed == frozenset() and bare.actions_exclusive)
    o = A.apply_policy(sc, {"read": "observe", "model": "observe", "act": "observe"})
    check("observe changes no field (not exclusive, not restricted) but is recorded", not o.data_surface_exclusive and not o.actions_exclusive and not o.is_restricted and o.policy_act == "observe")
    check("the original scope is untouched (pure)", not sc.data_surface_exclusive and sc.policy_act == "off")
    asif = A._as_if_enforced(o)
    check("as-if-enforced derives the enforce shape for every observed plane", asif is not None and asif.data_surface_exclusive and asif.actions_exclusive)
    check("…and is None when no plane is observed", A._as_if_enforced(e) is None and A._as_if_enforced(sc) is None)
    check("describe_scope names an enforced policy as a constraint, in words", "deployment policy: reads, model changes, writes contracts-only" in A.describe_scope(e)
          and not any("deployment policy" in x for x in A.describe_scope(o)))
run(t_apply)


print("\n[§3] the chokepoint under enforce")
def t_enforce():
    sc = A.parse_scope(GRANTS, planes=ALL)
    e = A.apply_policy(sc, {"read": "enforce", "model": "off", "act": "enforce"})
    ok, why = A.is_tool_allowed(e, "base_readQuery", arguments={"sql": "SELECT 1"})
    check("raw SQL is refused as surface-only, naming TDA_DataProduct", not ok and A.SURFACE_DENIAL_MARKER in why and "TDA_DataProduct" in why)
    ok, why = A.is_tool_allowed(e, "base_readQuery", is_write=True, arguments={"sql": "UPDATE t SET a=1"})
    check("a raw write is refused", not ok and (A.ACTION_DENIAL_MARKER in why or A.SURFACE_DENIAL_MARKER in why))
    ok, why = A.is_tool_allowed(e, "shell_execute")
    check("a command tool is refused under the write policy, naming it", not ok and "contracts-only" in why and "commands" in why)
    ok, _ = A.is_tool_allowed(A.apply_policy(sc, {"read": "off", "model": "off", "act": "enforce"}), "base_tableList")
    check("a plain read tool stays allowed under a write-only policy", ok)
    ok, _ = A.is_tool_allowed(A.apply_policy(sc, {"read": "off", "model": "off", "act": "enforce"}), "TDA_FinalReport")
    check("framework tools are never command-gated", ok)
    ok, _ = A.is_tool_allowed(A.apply_policy(sc, {"read": "off", "model": "off", "act": "observe"}), "shell_execute")
    check("observe refuses nothing", ok)
    check("the governed registry still permits the compiled statement under read enforce (the surface's own rule)",
          A.is_tool_allowed(e, "base_readQuery", arguments={"sql": "SELECT 1"})[0] is False)
run(t_enforce)


print("\n[§4] the chokepoint under observe records what enforce would refuse")
def t_observe():
    from trusted_data_agent.core import trust_events as T
    rec = []
    saved = T.record_event
    T.record_event = lambda *a, **k: rec.append((a[0], a[1], k.get("payload") or {})) or "id"
    o = A.apply_policy(A.parse_scope(GRANTS, planes=ALL), {"read": "observe", "model": "off", "act": "observe"})
    tok = A.set_current_principal(A.AgentPrincipal(owner_uuid="owner", profile_id="p1", scope=o))
    try:
        ok, _ = A.enforce_tool("base_readQuery", is_write=True, arguments={"sql": "DELETE FROM t"})
        check("a raw write is ALLOWED under observe", ok)
        check("…and recorded as policy_observed for the owner, naming the write plane and the tool",
              rec and rec[-1][1] == "policy_observed" and rec[-1][0] == "owner" and rec[-1][2].get("plane") == "act" and rec[-1][2].get("tool") == "base_readQuery", str(rec[-1:]))
        n = len(rec)
        ok, _ = A.enforce_tool("base_readQuery", arguments={"sql": "SELECT 1"})
        check("raw SQL under read observe is allowed and recorded for the read plane", ok and len(rec) == n + 1 and rec[-1][2].get("plane") == "read")
        n = len(rec)
        ok, _ = A.enforce_tool("base_tableList")
        check("a call enforce would allow records nothing", ok and len(rec) == n)
        ok, _ = A.enforce_tool("shell_execute")
        check("a command tool under write observe is allowed and recorded", ok and len(rec) == n + 1)
    finally:
        A.set_current_principal(None)
        T.record_event = saved
    from trusted_data_agent.core import trust_events as TE
    from trusted_data_agent.agent import assurance_points as AP
    check("policy_observed is registered as a warning in both severity maps", TE._FALLBACK_SEVERITY.get("policy_observed") == "warning" and AP.DERIVED_EVENT_SEVERITY.get("policy_observed") == "warning")
run(t_observe)


print("\n[§5] the policy applies to every turn, from the one derive seam")
def t_scope():
    saved = dict(P._FORCED_POLICY)
    try:
        P.force_policy("act", "observe")
        check("an active policy makes interactive turns derive a principal", A.enforces_interactive({"id": "x"}) is True)
        P.force_policy("act", None)
        check("…and with every plane off the opt-in rule stands alone", A.enforces_interactive({"id": "x"}) is False and A.enforces_interactive({"authorityConfig": {"enforce_interactive": True}}) is True)
        es = src("src/trusted_data_agent/agent/execution_service.py")
        check("execution_service derives through enforces_interactive (the one rule)", "_aid.enforces_interactive(active_profile)" in es)
        aa = src("src/trusted_data_agent/core/agent_authority.py")
        seg = aa[aa.index("def derive_principal("):aa.index("def _looks_like_write(")]
        check("derive_principal applies the policy once, after the planes", "apply_policy(scope, _planes.policy_map())" in seg)
        hg = P.profile_has_graph; P.profile_has_graph = lambda *a, **k: True
        P.force_policy("act", "enforce"); P.force_act(True); P.force("read", True)
        pr = A.derive_principal({"id": "p", "authorityConfig": GRANTS}, "owner")
        check("a derived principal carries the enforced write surface", pr.scope.actions_exclusive and pr.scope.policy_act == "enforce")
    finally:
        P._FORCED_POLICY.update(saved); P.force_act(None); P.force("read", None); P.profile_has_graph = hg
run(t_scope)


print("\n[§6] the knowledge-graph component under the model policy")
def t_model():
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler, _KG_DIRECT_EDITS
    from trusted_data_agent.core import trust_events as T
    check("the direct edits are the four graph writes, never the proposals",
          _KG_DIRECT_EDITS == frozenset({"add_entity", "add_relationship", "declare_concept", "restore"}))
    h = KnowledgeGraphHandler()
    rec = []; saved = T.record_event
    T.record_event = lambda *a, **k: rec.append(a[1]) or "id"
    try:
        check("no principal ⇒ no refusal (a person's own edit)", h._policy_write_refusal("add_entity", {}) is None)
        for mode, want in (("off", None), ("observe", None), ("enforce", "refused")):
            sc = A.apply_policy(A.parse_scope({}, planes=ALL), {"read": "off", "model": mode, "act": "off"})
            tok = A.set_current_principal(A.AgentPrincipal(owner_uuid="o", profile_id="p", scope=sc))
            try:
                r = h._policy_write_refusal("add_entity", {"profile_id": "p", "user_uuid": "o"})
            finally:
                A.set_current_principal(None)
            if want is None:
                check(f"model {mode}: the edit proceeds", r is None)
            else:
                check("model enforce: the edit is refused, recovery-shaped (propose; a person publishes)",
                      r is not None and r.spec.get("refused") == "policy_contracts_only" and "TDA_ModelProposal" in r.spec.get("error", "") and r.metadata.get("plane") == "model")
        check("model observe recorded a policy_observed event", "policy_observed" in rec)
        hs = src("components/builtin/knowledge_graph/handler.py")
        check("the refusal sits after the released-mode rule on every direct edit", "if action in _KG_DIRECT_EDITS:" in hs and hs.index("_released_write_refusal(action, context)") < hs.index("_policy_write_refusal(action, context)"))
    finally:
        T.record_event = saved
run(t_model)


print("\n[§7] the stamp")
def t_stamp():
    sc = A.parse_scope(GRANTS, planes=ALL)
    check("all off ⇒ no policy key in the stamp (the baseline hash)", "policy" not in A.apply_policy(sc, {"read": "off", "model": "off", "act": "off"}).to_dict())
    d = A.apply_policy(sc, {"read": "observe", "model": "off", "act": "enforce"}).to_dict()
    check("the stamp carries exactly the planes under policy", d.get("policy") == {"read": "observe", "act": "enforce"})
    check("build_authority_stamp carries it", "policy" in A.build_authority_stamp(A.AgentPrincipal("o", "p", A.apply_policy(sc, {"read": "enforce", "model": "off", "act": "off"})), "flow"))
run(t_stamp)


print("\n[§8] the surfaces")
def t_surfaces():
    adm = src("src/trusted_data_agent/api/admin_routes.py")
    check("the admin route serves obda_policy in the agent_eval block", adm.count("'obda_policy': dict(getattr(APP_CONFIG, 'OBDA_POLICY'") >= 2)
    check("…applies it through the ONE normalizer and persists it", "_planes.set_policy(ae['obda_policy'])" in adm and "'obda_policy': dict(getattr(APP_CONFIG, 'OBDA_POLICY', {}) or {}),\n                    'default_thresholds': APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS" in adm)
    db = src("src/trusted_data_agent/auth/database.py")
    check("the startup loader restores it", "_planes.set_policy(ae['obda_policy'])" in db)
    rr = src("src/trusted_data_agent/api/rest_routes.py")
    check("the deployment masters payload carries obda_policy for the stations", '"obda_policy": _planes.policy_map()' in rr)
    idx = src("templates/index.html")
    check("the Trust panel offers the three selects with the ladder", all(f'id="obda-policy-{p}"' in idx for p in ("read", "model", "act"))
          and idx.count('<option value="observe"') >= 3 and idx.count('<option value="enforce"') >= 3
          and idx.count('data-meaning=') >= 9 and all(f'id="obda-policy-{p}-meaning"' in idx for p in ("read", "model", "act")))
    js = src("static/js/adminManager.js")
    check("the admin script snapshots, loads and saves the policy", "obda_policy_read: 'obda-policy-read'" in js and "config.agent_eval.obda_policy = " in js and "sel.value = ['off', 'observe', 'enforce'].includes(pol[plane])" in js)
    ap = src("static/js/handlers/agentPerformanceHandler.js")
    check("the BOUNDED station reads the policy from the masters, enforced as a constraint, observed as a rehearsal",
          "_masters.obda_policy" in ap and "'OBDA policy — '" in ap and "(enforced)" in ap and "observed (a rehearsal:" in ap and "_polEvidence," in ap)
    check("a policy observation classifies to BOUNDED and has an inbox title", "/surface|policy/.test(t)) return 'bounded'" in ap and "policy_observed: 'Policy observation" in ap)
    cfg = src("src/trusted_data_agent/core/config.py")
    check("config declares OBDA_POLICY with every plane off", 'OBDA_POLICY = {"read": "off", "model": "off", "act": "off"}' in cfg)
run(t_surfaces)

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
sys.exit(1 if FAIL else 0)
