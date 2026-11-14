"""PROP-008 increment B4a — the agent face: the `action` component (TDA_Action).

The REAL handler over the B3 harness: a real SQLite graph/trust/review store, real signing,
the real perform seam, a SQLite twin of the target. One stand-in, stated: the handler's graph
resolution (`_graph`, the knowledge-graph component's own lookup) is pointed at the signed
test graph — resolving it for real would read the config manager, which bootstraps user rows.

  §G  gating: offered only while OBDA-MODEL · OBDA-ACT is on; every refusal is recovery-shaped (spec.error +
      error_code, headless) — never a success on the Optimize path
  §L  list_actions: only the actions the grant names, with their trust state
  §R  resolution: a named ungranted action is refused naming the grant; one action on the
      named entity resolves without asking; several are refused listing them
  §I  interactive enforce: PREPARE — the consequence computed by the database (before →
      after, count, bound, checks) and a Confirm; NOTHING written; the model is told it is
      not done; JSON-string dict args decode
  §C  confirm: performs only the invocation the person saw (hash), with a signed receipt and
      the confirming person; a different hash writes nothing
  §A  autonomous: no one to click — enforce performs; review parks; observe records
  §U  undo: the ledger's before-image becomes an invocation of the SAME contract, prepared
      with an Undo control, confirmed like any act; an insert's undo is refused
  §K  the pack default keeps the component off; the companion skill is linked

Run: PYTHONPATH=src:. python3 test/test_action_agent_face.py
"""
import asyncio
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT); sys.path.insert(0, HERE)

import test_action_enforcement as H   # the B3 harness (temp stores, signing, the warehouse twin)
from test_action_enforcement import (APP_CONFIG, AP, AA, AT, RQ, RAW, NAME, U, TMP, store, ready, Warehouse)
H.PASS = H.FAIL = 0
PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
def _run(c): return asyncio.run(c)

sys.path.insert(0, os.path.join(ROOT, "components", "builtin", "action"))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
from components.builtin.action.handler import ActionComponentHandler

AGENT = {"id": "profile-agent", "tag": "AGT",
         "authorityConfig": {"actions": {"types": [NAME]}},
         "componentConfig": {"action": {"enabled": True}}}

def handler_for(kg):
    h = ActionComponentHandler()
    h._graph = lambda ctx: (store(kg), kg)
    return h

def ctx(profile=AGENT):
    return {"user_uuid": U, "profile_id": profile["id"], "profile_config": profile,
            "session_id": "s1", "current_turn_number": 3}

def call(h, args, profile=AGENT):
    return _run(h.process(args, ctx(profile)))

def bind(wh):
    AP.open_connection = lambda user_uuid, connection_id: wh.connect()

def autonomous(origin="scheduler"):
    return AA.set_turn_authority({"applied": True, "origin": origin})


def t_gating():
    print("[§G] gating")
    h = ActionComponentHandler()
    _saved_hg = _OP.profile_has_graph
    _OP.profile_has_graph = lambda *a, **k: True
    check("offered where a graph is bound to the agent (availability is the component chain's)", h.is_profile_relevant({"id": "p"}) is True)
    _OP.profile_has_graph = lambda *a, **k: False
    _OP.force_act(False)
    try:
        check("not offered to an agent with no graph bound", h.is_profile_relevant({"id": "p"}) is False)
        p = call(handler_for("kg-x"), {"action": "list_actions"})
        check("a call with OBDA-MODEL · OBDA-ACT off is a recovery-shaped refusal", p.spec.get("error") and p.metadata["error_code"] == "act_off"
              and p.metadata.get("suppress_inline"))
    finally:
        _OP.profile_has_graph = _saved_hg
        _OP.force_act(True)
    ok, why = h.validate_arguments({"action": "delete"})
    check("an unknown verb is refused naming the verbs", not ok and "list_actions" in why)
    ok, _ = h.validate_arguments({"action": "undo"})
    check("undo needs the receipt hash", not ok)


def t_list_resolve():
    print("[§L/§R] list and resolution")
    kg = "kg-face"; ready(kg); wh = Warehouse(); bind(wh); h = handler_for(kg)
    p = call(h, {"action": "list_actions"})
    check("list_actions shows the granted action with its trust state",
          [a["action"] for a in p.spec["actions"]] == [NAME] and p.spec["actions"][0]["state"] == "trusted"
          and "LoyaltyTier" in p.spec["text"], p.spec)
    other = dict(AGENT, authorityConfig={"actions": {"types": ["elsewhere"]}})
    p = call(h, {"action": "list_actions"}, profile=other)
    check("an agent granted nothing on this graph lists nothing", p.spec["actions"] == [])
    p = call(h, {"action": "perform", "action_type": "elsewhere", "key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("a named ungranted action is refused naming the grant",
          p.metadata.get("error_code") == "unknown_action" and NAME in p.spec["error"], p.spec)
    a = ActionComponentHandler()
    name, _ = a._resolve_action({"entity": "customers"}, [{"action": "x", "entity": "Customers"}, {"action": "y", "entity": "Sales"}])
    check("one action on the named entity resolves without asking", name == "x")
    name, why = a._resolve_action({}, [{"action": "x", "entity": "Customers"}, {"action": "y", "entity": "Customers"}])
    check("several applicable actions are refused, listing them", name is None and "x" in why and "y" in why)


def t_interactive():
    print("[§I] interactive enforce: prepare, never write")
    kg = "kg-int"; ready(kg); wh = Warehouse(); bind(wh); h = handler_for(kg)
    p = call(h, {"action": "perform", "entity": "Customers",
                 "key": json.dumps({"CustomerID": 7}), "values": json.dumps({"LoyaltyTier": "gold"})})
    check("JSON-string dict arguments decode and the act is PREPARED", p.spec.get("prepared") is True, p.spec)
    check("NOTHING is written by a prepare", wh.tier(7) == ("bronze",) and wh.ledger() == [])
    cons = p.spec["consequence"]
    check("the consequence is the database's answer: before → after", any("LoyaltyTier bronze → gold" in l for l in cons["lines"]), cons)
    check("…with the count against the bound and every check", cons["rows"] == 1 and cons["bound"] == 1 and cons["checks_hold"]
          and {c["id"] for c in cons["checks"]} >= {"row_exists", "within_bound", "not_closed", "one_row_affected"})
    check("the model is told it is NOT done", "PREPARED, NOT DONE" in p.spec["text"] and "never say it was done" in p.spec["text"])
    check("the row carries a Confirm control with an accessible name", "class='act-confirm'" in p.html and "aria-label='Confirm" in p.html)
    pend = p.metadata["pending"]
    check("the pending act names what a Confirm performs (hash, invocation, turn)",
          pend["invocation_hash"] and pend["invocation"]["values"] == {"LoyaltyTier": "gold"} and pend["turn_id"] == 3)
    check("the row is an Action row, open", p.metadata["answer_surface"] == {"open": True, "word": "Action"})
    bad = call(h, {"action": "perform", "key": {"CustomerID": 7}, "values": {"LoyaltyTier": "platinum"}})
    check("a value outside the domain is refused naming it", bad.metadata.get("error_code") == "outside_domain"
          and "bronze" in bad.spec["error"], bad.spec)
    closed = call(h, {"action": "perform", "key": {"CustomerID": 8}, "values": {"LoyaltyTier": "gold"}})
    check("a failing precondition is refused at prepare, naming the check",
          closed.metadata.get("error_code") == "not_closed", closed.spec)

    print("[§C] confirm")
    rec = AP.confirm_action(store(kg), kg, NAME, pend["invocation"], expected_invocation_hash=pend["invocation_hash"],
                            user_uuid=U, profile=AGENT, connection_id="dbconn_test", session_id="s1", turn_id=3, db_path=TMP)
    check("Confirm performs exactly the prepared invocation", rec["outcome"] == "performed" and wh.tier(7) == ("gold",)
          and rec["invocation_hash"] == pend["invocation_hash"], rec)
    check("…names the confirming person and carries a signed receipt over the receipt content",
          rec["confirmed_by"] == "user:u1" and rec["signed_receipt"]["content"] == AP.receipt_content(rec)
          and "content_hash" in rec["signed_receipt"])
    wh2 = Warehouse(); bind(wh2)
    r2 = AP.confirm_action(store(kg), kg, NAME, {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "silver"}},
                           expected_invocation_hash=pend["invocation_hash"], user_uuid=U, profile=AGENT,
                           connection_id="dbconn_test", db_path=TMP)
    check("a confirmation whose values differ from what was prepared writes nothing",
          r2["outcome"] == "refused" and r2["refused_check"] == "parked_drifted" and wh2.tier(7) == ("bronze",), r2)

    print("[§U] undo")
    bind(wh)
    u = call(h, {"action": "undo", "invocation_hash": rec["invocation_hash"]})
    check("undo prepares the before-image through the same contract", u.spec.get("prepared") and
          u.metadata["pending"]["invocation"]["values"] == {"LoyaltyTier": "bronze"} and u.metadata["pending"]["undo"], u.spec)
    check("…with an Undo control, nothing written yet", "aria-label='Undo" in u.html and wh.tier(7) == ("gold",))
    upend = u.metadata["pending"]
    ur = AP.confirm_action(store(kg), kg, NAME, upend["invocation"], expected_invocation_hash=upend["invocation_hash"],
                           user_uuid=U, profile=AGENT, connection_id="dbconn_test", db_path=TMP)
    check("confirming the undo restores the row, with its own receipt",
          ur["outcome"] == "performed" and wh.tier(7) == ("bronze",) and ur["invocation_hash"] != rec["invocation_hash"])
    nu = call(h, {"action": "undo", "invocation_hash": "f" * 64})
    check("an unknown receipt cannot be undone", nu.metadata.get("error_code") == "not_found")
    ins = {"action": "add_customer", "version": 1, "backend": "teradata",
           "target": {"entity": "Customers", "deployed": "fitness_db.Customers", "key": ["CustomerID"]},
           "operation": "insert", "attributes": [{"name": "CustomerID", "type": "integer", "required": True},
                                                   {"name": "LoyaltyTier", "type": "string", "required": True}],
           "bound": {"max_rows": 1}, "authority": {"required_role": "UDERIA_ACT_FITNESS", "posture": "enforce"}}
    _run(AT.author_action(store(kg), ins, actor="u1"))
    try:
        AP.undo_invocation(store(kg), kg, "add_customer", "x", user_uuid=U, connect_fn=wh.connect)
        e = None
    except AP.ActionPerformError as ex:
        e = ex.code
    check("an insert's undo (a delete) is refused in v1", e == "not_undoable")


def t_autonomous():
    print("[§A] autonomous: the posture decides")
    kg = "kg-auto"; ready(kg); wh = Warehouse(); bind(wh); h = handler_for(kg)
    tok = autonomous()
    try:
        p = call(h, {"action": "perform", "key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    finally:
        AA.reset_turn_authority(tok)
    check("enforce on an autonomous turn performs: one row, receipt", p.spec.get("outcome") == "performed"
          and wh.tier(7) == ("gold",) and p.spec["invocation_hash"], p.spec)
    kg = "kg-auto-rev"; ready(kg, posture="review"); wh = Warehouse(); bind(wh); h = handler_for(kg)
    tok = autonomous("flow")
    try:
        p = call(h, {"action": "perform", "key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    finally:
        AA.reset_turn_authority(tok)
    check("review on an autonomous turn parks for the owner, nothing written",
          p.spec.get("outcome") == "parked" and p.spec.get("review_id") and wh.tier(7) == ("bronze",), p.spec)
    kg = "kg-int-obs"; ready(kg, posture="observe"); wh = Warehouse(); bind(wh); h = handler_for(kg)
    p = call(h, {"action": "perform", "key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("observe records and writes nothing (interactive too)", p.spec.get("outcome") == "observed" and wh.tier(7) == ("bronze",))
    kg = "kg-int-rev"; ready(kg, posture="review"); wh = Warehouse(); bind(wh); h = handler_for(kg)
    p = call(h, {"action": "perform", "key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("review on an interactive turn parks (the owner approves in the Inbox), never a Confirm",
          p.spec.get("outcome") == "parked" and "act-confirm" not in p.html)


def t_wiring():
    print("[§K] pack default + companion skill")
    from agent_packs.common.component_config import ACTION_COMPONENTS, focused_component_config
    check("packs keep the action component off", "action" in ACTION_COMPONENTS and focused_component_config()["action"] == {"enabled": False})
    reg = json.load(open(os.path.join(ROOT, "skills/skill_registry.json")))
    check("the companion skill is linked by component_id",
          any(s.get("skill_id") == "obda-act-expert" and s.get("component_id") == "action" for s in reg["skills"]))
    man = json.load(open(os.path.join(ROOT, "components/builtin/action/manifest.json")))
    check("the component is enabled for no profile type by default", man["profile_defaults"]["enabled_for"] == [])


def t_tool_output():
    print("[§T] what the agent reports of a tool call (Live Status)")
    from langchain_core.messages import ToolMessage
    from trusted_data_agent.agent.conversation_agent import ConversationAgentExecutor as CX
    m = ToolMessage(content='{"text": "PREPARED"}', tool_call_id="t1", name=TOOL if "TOOL" in globals() else "TDA_Action",
                    artifact={"html": '<div class="act-row"><button class="act-confirm">Confirm</button></div>'})
    txt = CX._tool_output_text(m)
    check("the preview is the tool's TEXT — never the message's repr with its rendered card (a live Confirm in Live Status)",
          txt == '{"text": "PREPARED"}' and "act-confirm" not in txt and "artifact" not in txt, txt)
    check("…text blocks are joined; a plain string passes through",
          CX._tool_output_text(ToolMessage(content=[{"type": "text", "text": "a"}, "b"], tool_call_id="t2")) == "ab"
          and CX._tool_output_text("plain") == "plain")
    import inspect
    names = ("_tool_output_text", "_tool_output_refusal", "_tool_output_is_error", "_tool_output_row_count")
    check("the helpers the tool-end path calls ON THE INSTANCE are static (a lost decorator failed every tool call)",
          all(isinstance(inspect.getattr_static(CX, n), staticmethod) for n in names),
          [n for n in names if not isinstance(inspect.getattr_static(CX, n), staticmethod)])


for fn in (t_gating, t_list_resolve, t_interactive, t_autonomous, t_wiring, t_tool_output):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc(); check(f"{fn.__name__} raised", False, repr(exc))
try: os.remove(TMP)
except OSError: pass
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
