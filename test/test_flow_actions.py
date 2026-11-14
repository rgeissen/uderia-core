"""PROP-008 increment C2a — a signed action performed by a flow node (§6).

The REAL seam (`action_perform.perform_unattended`) and the REAL flow node executor over the
B3 harness: a real SQLite graph/trust/review store, real signing, a SQLite twin of the target.
Stand-ins, stated: the agent's graph lookup (the knowledge-graph component's `_get_store`) is
pointed at the signed test graph, and the config manager returns the test agent — resolving
either for real would read the user's stored configuration.

  §V  save-time validation (pure): a flow with no action node is untouched whatever the
      switch; a node still being drafted is not judged; OBDA-MODEL · OBDA-ACT off, no agent, an unknown agent
      and an ungranted action are each refused by name; the grant is read through the node's
      agent, else the flow's default, by id or by tag
  §U  perform_unattended: always acts as an agent (no agent ⇒ refused); the posture decides —
      enforce writes with a signed receipt naming its face, review parks, observe records;
      an ungranted action and OBDA-MODEL · OBDA-ACT off are refusals, never an exception
  §N  the task_action node: resolves `$name` references from upstream outputs, performs,
      returns the outcome downstream nodes branch on; a refusal is a node ERROR (error edges
      apply); review parks and is a success
  §W  wiring: dispatch, the persistence boundary, the run start and the REST error mapping

Run: PYTHONPATH=src:. python3 test/test_flow_actions.py
"""
import asyncio
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

import test_action_enforcement as H   # the B3 harness
from test_action_enforcement import APP_CONFIG, AP, NAME, U, store, ready, Warehouse
H.PASS = H.FAIL = 0
PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))

from trusted_data_agent.core import flow_validation as FV

AGENT = {"id": "profile-agent", "tag": "AGT", "profile_type": "llm_only",
         "authorityConfig": {"actions": {"types": [NAME]}}}
OTHER = {"id": "profile-other", "tag": "OTH", "profile_type": "llm_only", "authorityConfig": {}}
PROFILES = [AGENT, OTHER]


def flow(nodes, default=None):
    d = {"nodes": nodes, "edges": []}
    if default:
        d["settings"] = {"defaultProfileId": default}
    return d


def anode(action=NAME, profile=None, label="Set tier"):
    data = {"action": action, "invocation": {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}}}
    if profile:
        data["profile_id"] = profile
    return {"id": "n1", "type": "task_action", "label": label, "data": data}


def t_validation():
    print("[§V] save-time validation")
    plain = flow([{"id": "a", "type": "task_tool", "data": {"tool_name": "x"}}])
    check("a flow with no action node is untouched (OBDA-MODEL · OBDA-ACT off)", FV.action_node_errors(plain, PROFILES, act_enabled=False) == [])
    check("…and with ACT on", FV.action_node_errors(plain, None, act_enabled=True) == [])
    check("has_action_nodes is false for it", FV.has_action_nodes(plain) is False)
    draft = flow([anode(action="")])
    check("a node still being drafted (no action named) is not judged",
          FV.action_node_errors(draft, PROFILES, act_enabled=True) == [])
    e = FV.action_node_errors(flow([anode(profile="profile-agent")]), PROFILES, act_enabled=False)
    check("OBDA-ACT off refuses a named action node, naming the plane", len(e) == 1 and "OBDA-ACT is not enabled" in e[0], e)
    e = FV.action_node_errors(flow([anode()]), PROFILES, act_enabled=True)
    check("no agent (no node agent, no flow default) is refused", len(e) == 1 and "names no agent" in e[0], e)
    e = FV.action_node_errors(flow([anode(profile="profile-ghost")]), PROFILES, act_enabled=True)
    check("an agent that is not one of yours is refused", len(e) == 1 and "not one of your agents" in e[0], e)
    e = FV.action_node_errors(flow([anode(profile="profile-other")]), PROFILES, act_enabled=True)
    check("an ungranted action is refused, naming the agent and what it IS granted",
          len(e) == 1 and "@OTH is not granted" in e[0] and "granted: none" in e[0] and "Actions panel" in e[0], e)
    check("a granted node agent passes", FV.action_node_errors(flow([anode(profile="profile-agent")]), PROFILES, act_enabled=True) == [])
    check("…by tag (@AGT) too", FV.action_node_errors(flow([anode(profile="@AGT")]), PROFILES, act_enabled=True) == [])
    check("the flow default supplies the agent when the node names none",
          FV.action_node_errors(flow([anode()], default="profile-agent"), PROFILES, act_enabled=True) == [])
    e = FV.action_node_errors(flow([anode(profile="profile-other")], default="profile-agent"), PROFILES, act_enabled=True)
    check("the node's own agent wins over the flow default", len(e) == 1 and "@OTH" in e[0], e)
    two = flow([anode(profile="profile-other", label="A"), dict(anode(profile="profile-ghost", label="B"), id="n2")])
    check("every failing node is named", len(FV.action_node_errors(two, PROFILES, act_enabled=True)) == 2)
    # the impure wrapper raises the named error
    import trusted_data_agent.core.config_manager as CM
    orig = CM.get_config_manager
    class _CM:
        def get_profiles(self, uid=None, user_uuid=None): return PROFILES
        def get_profile(self, pid, uid): return {p["id"]: p for p in PROFILES}.get(pid)
    CM.get_config_manager = lambda: _CM()
    try:
        raised = None
        try: FV.check_action_nodes(flow([anode(profile="profile-other")]), U)
        except FV.ActionNodeError as ex: raised = ex
        check("check_action_nodes raises ActionNodeError with the reason and code",
              raised is not None and raised.code == "action_not_granted" and "@OTH" in str(raised))
        check("ActionNodeError is a ValueError (the run's existing refusal path)", isinstance(raised, ValueError))
        FV.check_action_nodes(flow([anode(profile="profile-agent")]), U)
        check("a granted flow passes the wrapper", True)
    finally:
        CM.get_config_manager = orig


def t_unattended():
    print("[§U] perform_unattended — the posture decides")
    kg = "kg-u-enf"; ready(kg); wh = Warehouse()
    inv = {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}}
    r = AP.perform_unattended(U, AGENT, NAME, inv, face="flow", store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("enforce writes one row", r.get("outcome") == "performed" and wh.tier(7) == ("gold",), r)
    sr = r.get("signed_receipt") or {}
    check("a performed act carries a receipt signed on its own", sr.get("content_hash") and "content" in sr, sr)
    check("…and names its face", r.get("face") == "flow")
    check("the principal is the agent", r.get("principal") == "agent:profile-agent")
    r = AP.perform_unattended(U, AGENT, NAME, inv, face="scheduler", store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("the face is carried for the scheduler too", r.get("face") == "scheduler")
    r = AP.perform_unattended(U, AGENT, NAME, inv, face="nonsense", store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("an unknown face never invents one", r.get("face") == "flow")
    kg = "kg-u-rev"; ready(kg, posture="review"); wh = Warehouse()
    r = AP.perform_unattended(U, AGENT, NAME, inv, face="flow", store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("review parks for the owner and writes nothing", r.get("outcome") == "parked" and r.get("review_id")
          and wh.tier(7) == ("bronze",), r)
    check("…and carries no receipt (nothing was performed)", "signed_receipt" not in r)
    kg = "kg-u-obs"; ready(kg, posture="observe"); wh = Warehouse()
    r = AP.perform_unattended(U, AGENT, NAME, inv, face="flow", store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("observe records and writes nothing", r.get("outcome") == "observed" and wh.tier(7) == ("bronze",), r)
    kg = "kg-u-no"; ready(kg); wh = Warehouse()
    r = AP.perform_unattended(U, None, NAME, inv, face="scheduler", store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("no agent is refused (nobody present to lend authority), nothing written",
          r.get("outcome") == "refused" and r.get("refused_check") == "no_agent" and wh.tier(7) == ("bronze",), r)
    r = AP.perform_unattended(U, OTHER, NAME, inv, face="flow", store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("an ungranted agent is a refused record, never an exception",
          r.get("outcome") == "refused" and r.get("refused_check") == "not_granted" and wh.tier(7) == ("bronze",), r)
    _OP.force_act(False)
    try:
        r = AP.perform_unattended(U, AGENT, NAME, inv, face="flow", store=store(kg), kg_id=kg, connect_fn=wh.connect)
        check("OBDA-MODEL · OBDA-ACT off is a refused record", r.get("outcome") == "refused" and r.get("refused_check") == "act_off", r)
    finally:
        _OP.force_act(True)
    r = AP.perform_unattended(U, AGENT, NAME, {"key": {"CustomerID": 7}, "values": {"Nope": 1}}, face="flow",
                              store=store(kg), kg_id=kg, connect_fn=wh.connect)
    check("an invocation the compiler refuses is a refused record", r.get("outcome") == "refused" and wh.tier(7) == ("bronze",), r)


def t_node():
    print("[§N] the task_action node")
    from trusted_data_agent.core import flow_executor as FE
    import trusted_data_agent.core.config_manager as CM
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler
    kg = "kg-n"; ready(kg); wh = Warehouse()
    orig_cm, orig_gs, orig_oc = CM.get_config_manager, KnowledgeGraphHandler._get_store, AP.open_connection
    class _CM:
        def get_profile(self, pid, uid): return {p["id"]: p for p in PROFILES}.get(pid)
        def get_profiles(self, uid=None, user_uuid=None): return PROFILES
    st = store(kg)
    CM.get_config_manager = lambda: _CM()
    KnowledgeGraphHandler._get_store = lambda self, ctx: st
    AP.open_connection = lambda user_uuid, connection_id: wh.connect()
    try:
        node = {"id": "n1", "type": "task_action", "label": "Promote",
                "data": {"action": NAME, "profile_id": "profile-agent", "connection_id": "dbconn_test",
                         "invocation": {"key": {"CustomerID": "$cid"}, "values": {"LoyaltyTier": "$tier"}}}}
        out = asyncio.run(FE._execute_node(node, {"cid": 7, "tier": "gold"}, U, "run-1", None))
        o = out.get("output") or {}
        check("`$name` references resolve from upstream outputs and the act lands",
              o.get("outcome") == "performed" and wh.tier(7) == ("gold",), out)
        check("the output carries what downstream nodes branch on",
              o.get("rows_affected") == 1 and o.get("invocation_hash") and o.get("signed_receipt") and o.get("description"))
        check("an act costs no tokens", out.get("tokens_input") == 0 and out.get("cost_usd") == 0.0)
        bad = copy.deepcopy(node); bad["data"]["profile_id"] = "profile-other"
        err = None
        try: asyncio.run(FE._execute_node(bad, {"cid": 7, "tier": "silver"}, U, "run-2", None))
        except RuntimeError as e: err = str(e)
        check("a refusal is a node ERROR naming the check (error edges apply)",
              err and "refused (not_granted)" in err and wh.tier(7) == ("gold",), err)
        empty = copy.deepcopy(node); empty["data"]["action"] = ""
        err = None
        try: asyncio.run(FE._execute_node(empty, {}, U, "run-3", None))
        except RuntimeError as e: err = str(e)
        check("a node with no action configured is an error", err and "no action configured" in err)
        nodef = copy.deepcopy(node); nodef["data"].pop("profile_id")
        out = asyncio.run(FE._execute_node(nodef, {"cid": 7, "tier": "silver"}, U, "run-4", None,
                                           caller_profile_id="profile-agent"))
        check("the flow default agent (caller profile) acts when the node names none",
              (out.get("output") or {}).get("outcome") == "performed" and wh.tier(7) == ("silver",), out)
        kg2 = "kg-n-rev"; ready(kg2, posture="review"); wh2 = Warehouse(); st2 = store(kg2)
        KnowledgeGraphHandler._get_store = lambda self, ctx: st2
        AP.open_connection = lambda user_uuid, connection_id: wh2.connect()
        out = asyncio.run(FE._execute_node(node, {"cid": 7, "tier": "gold"}, U, "run-5", None))
        check("review parks and the node SUCCEEDS with outcome=parked (a branchable fact)",
              (out.get("output") or {}).get("outcome") == "parked" and wh2.tier(7) == ("bronze",), out)
    finally:
        CM.get_config_manager, KnowledgeGraphHandler._get_store, AP.open_connection = orig_cm, orig_gs, orig_oc


def t_wiring():
    print("[§W] wiring")
    src = lambda p: open(os.path.join(ROOT, p)).read()
    fe, fdb, fr = (src("src/trusted_data_agent/core/flow_executor.py"), src("src/trusted_data_agent/core/flow_db.py"),
                   src("src/trusted_data_agent/api/flow_routes.py"))
    check("the dispatcher routes task_action to its executor", 'node_type == "task_action"' in fe
          and "_execute_action_node(" in fe)
    check("the node performs through the ONE unattended seam", "AP.perform_unattended" in fe)
    i = fe.index("async def execute_flow(")
    check("the run refuses before any node runs", "check_action_nodes(definition, user_id)" in fe[i:i + 6000])
    check("create_flow and update_flow both check at the persistence boundary", fdb.count("check_action_nodes(definition, user_id)") == 2)
    check("REST maps ActionNodeError to a 400 with its code", "@flow_bp.errorhandler(ActionNodeError)" in fr
          and "exc.code" in fr)
    j = fr.index("except ActionNodeError as exc:")
    check("the execute endpoint names the refusal before the generic ValueError", j < fr.index("except ValueError as exc:", j))


if __name__ == "__main__":
    _OP.force_act(True)
    for fn in (t_validation, t_unattended, t_node, t_wiring):
        try:
            fn()
        except Exception as e:  # a raising section is a NAMED failure, never a silent abort
            import traceback; traceback.print_exc()
            check(f"{fn.__name__} raised", False, repr(e))
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
