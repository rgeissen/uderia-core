"""PROP-008 increment B3 — performing a signed action: authority, posture, the transaction,
the sealed receipt, the acted lane.

Real SQLite for the graph, the trust store and the review queue (temp DB, real schema), the
real GraphStore, the real A1–A6 gate and real signing; the target table is a SQLite twin of
fitness_db.Customers reached through the runner's DB-API connection. No doubles for the thing
under test.

  §A  the SQL gate's refusing side: a raw write stays denied under deny_writes WITH a grant;
      actions_exclusive refuses every raw write (naming the granted actions) and still admits
      a read; a grant alone (no exclusive, no deny_writes) is the baseline; OBDA-MODEL · OBDA-ACT off drops it
  §P  perform: OBDA-MODEL · OBDA-ACT off · unknown action · unsigned / changed action · ungranted agent are
      refused before any connection is touched; a granted agent writes one row with its
      ledger row; a person (no agent) writes with their own authority; a compile refusal and
      a failing postcondition are `refused` records with nothing written
  §O  the posture ladder: observe writes nothing; review checks the preconditions and parks —
      nothing written until the owner approves; approval writes exactly what was parked; an
      approval after the contract changed is refused (parked_drifted) and writes nothing; a
      parked write whose precondition fails is refused at park time
  §R  the receipt: only PERFORMED invocations are sealed as `action.performed`; offline
      verification re-derives each from the turn's `actions` stamp; a tampered stamp is a
      NAMED mismatch; a receipt with no stamp is a mismatch
  §L  the lane: acted / refused counted from the action record, the `acted` headline; a turn
      with no actions carries neither field (byte-identical baseline)
  §W  wiring: the turn boundary starts/ends the record, the persistence chokepoint stamps it,
      the review route performs a parked action BEFORE recording the approval

Run: PYTHONPATH=src:. python3 test/test_action_enforcement.py
"""
import ast
import asyncio
import copy
import json
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
def _run(coro): return asyncio.run(coro)

os.environ.pop("TDA_ACT", None)
from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.kgstore import factory as _factory
from trusted_data_agent.auth.database import _migrate_to_multi_kg
from trusted_data_agent.core import action_types as AT
from trusted_data_agent.core import action_perform as AP
from trusted_data_agent.core import agent_authority as AA
from trusted_data_agent.core import review_queue as RQ
from trusted_data_agent.core.trust import actions as A, store as S, composition as C
from trusted_data_agent.agent import obda as OB
from components.builtin.knowledge_graph.graph_store import GraphStore

_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP)
_c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read())
_migrate_to_multi_kg(_c); _c.commit(); _c.close()
APP_CONFIG.AUTH_DB_PATH = TMP
APP_CONFIG.KG_SEMANTIC_SEEDING = False
_OP.force_act(True)
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})

U, P = "u1", "p1"
def store(kg): return GraphStore(P, U, kg_id=kg, db_path=TMP)

WALK = [{"name": "fitness_db", "entity_type": "database", "properties": {"layer": "logical"}},
        {"name": "Customers", "entity_type": "entity", "properties": {"layer": "logical", "business_key": "CustomerID"}},
        {"name": "Customers.CustomerID", "entity_type": "attribute", "properties": {"layer": "logical"}},
        {"name": "Customers.LoyaltyTier", "entity_type": "attribute", "properties": {"layer": "logical"}},
        {"name": "Customers.Status", "entity_type": "attribute", "properties": {"layer": "logical"}},
        {"name": "fitness_db.Customers", "entity_type": "table", "properties": {"layer": "physical", "infrastructure": "teradata"}}]
RELS = [{"source_name": "fitness_db", "target_name": "Customers", "relationship_type": "contains"},
        {"source_name": "Customers", "target_name": "Customers.CustomerID", "relationship_type": "contains"},
        {"source_name": "Customers", "target_name": "Customers.LoyaltyTier", "relationship_type": "contains"},
        {"source_name": "Customers", "target_name": "Customers.Status", "relationship_type": "contains"},
        {"source_name": "fitness_db.Customers", "target_name": "Customers", "relationship_type": "implements"}]
NAME = "set_customer_tier"
RAW = {"action": NAME, "version": 1, "backend": "teradata",
       "target": {"entity": "Customers", "deployed": "fitness_db.Customers", "key": ["CustomerID"]},
       "operation": "update",
       "attributes": [{"name": "LoyaltyTier", "type": "string", "required": True, "value_domain": ["bronze", "silver", "gold"]}],
       "conditions": {"pre": [{"id": "not_closed", "expr": "Status <> 'closed'"}]},
       "bound": {"max_rows": 1},
       "authority": {"required_role": "UDERIA_ACT_FITNESS", "posture": "enforce"}}

def ready(kg, posture="enforce", raw=None, sign=True, conn="dbconn_test"):
    """A graph with a signed ontology and a validated (+ signed) action — validated THROUGH a
    connection, so the action owns it (perform writes through it; nothing on the agent does)."""
    st = store(kg); st.set_kg_metadata("Fitness", is_active=True); st.import_bulk(WALK, RELS)
    S.sign("ontology", kg, C.kg_graph_hash(kg, TMP)[0], signer_uuid=U, db_path=TMP)
    r = copy.deepcopy(raw or RAW); r["authority"]["posture"] = posture
    _run(AT.author_action(store(kg), r, actor="u1"))
    _run(AT.validate(store(kg), r["action"], connection_id=conn))
    if sign:
        out = A.sign_action(kg, r["action"], signer_uuid=U, db_path=TMP)
        assert out.get("signed"), out
    return store(kg)

LEDGER_DDL = ('CREATE TABLE fitness_db."UDERIA_ACT_Customers" (performed_at TEXT, principal TEXT, action_name TEXT, '
              'action_version INTEGER, invocation_hash TEXT, statement_hash TEXT, posture TEXT, rows_affected INTEGER, '
              'key_json TEXT, before_json TEXT)')

class Warehouse:
    """A SQLite twin of the target; ``connect()`` returns a fresh connection to the SAME
    database (attached by file), as a real pool would."""
    def __init__(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        c = sqlite3.connect(self.path)
        c.execute('CREATE TABLE "Customers" ("CustomerID" INTEGER PRIMARY KEY, "LoyaltyTier" TEXT, "Status" TEXT)')
        c.execute(LEDGER_DDL.replace('fitness_db.', ''))
        c.executemany('INSERT INTO "Customers" VALUES (?,?,?)', [(7, "bronze", "open"), (8, "silver", "closed")])
        c.commit(); c.close()
        self.opened = 0
    def connect(self):
        self.opened += 1
        c = sqlite3.connect(":memory:", autocommit=True)
        c.execute(f"ATTACH '{self.path}' AS fitness_db")
        return c, "sqlite"
    def tier(self, cid):
        c = sqlite3.connect(self.path)
        try: return c.execute('SELECT "LoyaltyTier" FROM "Customers" WHERE "CustomerID"=?', (cid,)).fetchone()
        finally: c.close()
    def ledger(self):
        c = sqlite3.connect(self.path)
        try: return c.execute('SELECT principal, posture, rows_affected FROM "UDERIA_ACT_Customers"').fetchall()
        finally: c.close()

AGENT = {"id": "profile-agent", "tag": "AGT", "authorityConfig": {"actions": {"types": [NAME]}}}
OTHER = {"id": "profile-other", "tag": "OTH", "authorityConfig": {"actions": {"types": ["something_else"]}}}
INV = {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}}

def perform(st, kg, wh, inv=INV, profile=AGENT, **kw):
    return AP.perform_action(st, kg, NAME, inv, user_uuid=U, profile=profile, connect_fn=wh.connect,
                             session_id="s1", turn_id=1, db_path=TMP, **kw)

def code_of(fn):
    try: fn(); return None
    except AP.ActionPerformError as e: return e.code


def t_authority():
    print("[§A] the SQL gate's refusing side")
    w = {"sql": "UPDATE fitness_db.Customers SET LoyaltyTier = 'gold' WHERE CustomerID = 7"}
    r = {"sql": "SELECT * FROM fitness_db.Customers"}
    from trusted_data_agent.agent.tool_enforcement import classify_sql_write
    iw = classify_sql_write("base_readQuery", w)
    s = AA.parse_scope({"deny_writes": True, "actions": {"types": [NAME]}})
    ok, why = AA.is_tool_allowed(s, "base_readQuery", is_write=iw, arguments=w)
    check("a grant does not admit a raw write under deny_writes", not ok and "may not perform write" in why, why)
    s = AA.parse_scope({"actions": {"types": [NAME], "exclusive": True}})
    ok, why = AA.is_tool_allowed(s, "base_readQuery", is_write=iw, arguments=w)
    check("actions_exclusive refuses every raw write, naming the granted actions",
          not ok and AA.ACTION_DENIAL_MARKER in why and NAME in why, why)
    ok, _ = AA.is_tool_allowed(s, "base_readQuery", is_write=classify_sql_write("base_readQuery", r), arguments=r)
    check("…and still admits a read", ok)
    check("exclusive is a restriction", s.is_restricted)
    s = AA.parse_scope({"actions": {"types": [NAME]}})
    ok, _ = AA.is_tool_allowed(s, "base_readQuery", is_write=iw, arguments=w)
    check("a grant alone (no exclusive, no deny_writes) leaves raw writes as they were", ok and not s.is_restricted)
    _OP.force_act(False)
    try:
        s = AA.parse_scope({"actions": {"types": [NAME], "exclusive": True}}, planes={"read": True, "model": False, "act": False})
        ok, _ = AA.is_tool_allowed(s, "base_readQuery", is_write=iw, arguments=w)
        check("OBDA-ACT off for the agent drops the grant at the parse seam (baseline)", ok and s.actions_allowed is None)
    finally:
        _OP.force_act(True)
    res = AA.denied_tool_result("base_readQuery", "this agent is action-only — …")
    check("the denial is a terminal, audited result", res["metadata"]["agent_denied"] and res["metadata"]["terminal"])


def t_perform():
    print("[§P] perform")
    kg = "kg-p"; st = ready(kg); wh = Warehouse()
    _OP.force_act(False)
    try: check("OBDA-MODEL · OBDA-ACT off refuses", code_of(lambda: perform(st, kg, wh)) == "act_off")
    finally: _OP.force_act(True)
    check("an unknown action is refused",
          code_of(lambda: AP.perform_action(st, kg, "nope", INV, user_uuid=U, connect_fn=wh.connect, db_path=TMP)) == "not_found")
    check("an ungranted agent is refused, naming its grant",
          code_of(lambda: perform(st, kg, wh, profile=OTHER)) == "not_granted")
    check("…and no connection was touched by any refusal", wh.opened == 0, wh.opened)
    tok = AP.begin_turn()
    try:
        rec = perform(st, kg, wh)
        check("a granted agent's write is performed: one row", rec["outcome"] == "performed" and rec["rows_affected"] == 1
              and wh.tier(7) == ("gold",), (rec, wh.tier(7)))
        check("the record names the agent principal, the hashes and the posture",
              rec["principal"] == "agent:profile-agent" and rec["posture"] == "enforce"
              and len(rec["statement_hash"]) == 64 and len(rec["invocation_hash"]) == 64 and rec["manifest_hash"])
        check("the ledger row carries the principal", wh.ledger() == [("agent:profile-agent", "enforce", 1)], wh.ledger())
        check("the record is on the turn", [r["outcome"] for r in AP.turn_actions()] == ["performed"])
        rec_u = AP.perform_action(st, kg, NAME, {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "silver"}},
                                  user_uuid=U, connect_fn=wh.connect, db_path=TMP)
        check("a person performs with their own authority", rec_u["outcome"] == "performed" and rec_u["principal"] == "user:u1")
        bad = perform(st, kg, wh, inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "platinum"}})
        check("a compile refusal is a `refused` record naming the domain",
              bad["outcome"] == "refused" and bad["refused_check"] == "outside_domain" and "bronze" in bad["refusal"])
        closed = perform(st, kg, wh, inv={"key": {"CustomerID": 8}, "values": {"LoyaltyTier": "gold"}})
        check("a failing precondition is refused and writes nothing",
              closed["outcome"] == "refused" and closed["refused_check"] == "not_closed" and wh.tier(8) == ("silver",))
        check("every invocation is on the turn record",
              [r["outcome"] for r in AP.turn_actions()] == ["performed", "performed", "refused", "refused"])
    finally:
        AP.end_turn(tok)
    check("outside a turn the record is empty", AP.turn_actions() == [])

    kg2 = "kg-unsigned"; st2 = ready(kg2, sign=False)
    check("an unsigned action is refused", code_of(lambda: perform(st2, kg2, wh)) == "not_trusted")
    kg3 = "kg-changed"; st3 = ready(kg3)
    ed = copy.deepcopy(RAW); ed["bound"]["max_rows"] = 2
    _run(AT.author_action(store(kg3), ed, actor="u1"))
    check("a CHANGED action (edited after signing) is refused", code_of(lambda: perform(st3, kg3, wh)) == "not_trusted")

    post = copy.deepcopy(RAW); post["conditions"]["post"] = [{"id": "stays_put", "expr": "LoyaltyTier = 'bronze'"}]
    kg4 = "kg-post"; st4 = ready(kg4, raw=post); wh4 = Warehouse()
    r4 = perform(st4, kg4, wh4)
    check("a failing postcondition rolls back: refused, nothing written, no ledger row",
          r4["outcome"] == "refused" and r4["refused_check"] == "stays_put" and wh4.tier(7) == ("bronze",)
          and wh4.ledger() == [], r4)


def t_posture():
    print("[§O] the posture ladder")
    kg = "kg-obs"; st = ready(kg, posture="observe"); wh = Warehouse()
    r = perform(st, kg, wh)
    check("observe records what would be written and writes nothing",
          r["outcome"] == "observed" and wh.tier(7) == ("bronze",) and r["statement_hash"] and wh.ledger() == [])

    kg = "kg-rev"; st = ready(kg, posture="review"); wh = Warehouse()
    r = perform(st, kg, wh)
    check("review parks the write after the preconditions held — nothing written",
          r["outcome"] == "parked" and r["review_id"] and wh.tier(7) == ("bronze",) and wh.ledger() == [], r)
    item = RQ.get_item(U, r["review_id"], db_path=TMP)
    check("the parked item is an action item, owner-scoped and pending",
          item and item["source"] == "action" and item["status"] == "pending")
    check("an action item is never releasable into a conversation", not RQ.is_releasable_source("action"))
    done = AP.perform_parked(st, U, item, profile_lookup=lambda pid: AGENT, connect_fn=wh.connect, db_path=TMP)
    check("approval writes exactly what was parked", done["outcome"] == "performed" and wh.tier(7) == ("gold",)
          and done["statement_hash"] == r["statement_hash"] and done["invocation_hash"] == r["invocation_hash"], done)
    check("…and the ledger names the asking agent AND the approver",
          wh.ledger() and "agent:profile-agent" in wh.ledger()[0][0] and "approved by user:u1" in wh.ledger()[0][0], wh.ledger())

    kg = "kg-drift"; st = ready(kg, posture="review"); wh = Warehouse()
    r = perform(st, kg, wh)
    item = RQ.get_item(U, r["review_id"], db_path=TMP)
    ed = copy.deepcopy(RAW); ed["authority"]["posture"] = "review"; ed["bound"]["max_rows"] = 2
    _run(AT.author_action(store(kg), ed, actor="u1"))
    check("an approval after the contract changed is refused and writes nothing",
          code_of(lambda: AP.perform_parked(st, U, item, profile_lookup=lambda pid: AGENT,
                                            connect_fn=wh.connect, db_path=TMP)) == "parked_drifted"
          and wh.tier(7) == ("bronze",))

    # The parked row is data in a store: an approval must perform only what compiles to
    # the very statement and values that were parked, never what the row now says.
    kg = "kg-tamper"; st = ready(kg, posture="review"); wh = Warehouse()
    r = perform(st, kg, wh)
    item = RQ.get_item(U, r["review_id"], db_path=TMP)
    forged = dict(item); pl = json.loads(item["original_text"]); pl["statement_hash"] = "0" * 64
    forged["original_text"] = json.dumps(pl)
    check("a parked row whose statement hash was altered is refused, nothing written",
          code_of(lambda: AP.perform_parked(st, U, forged, profile_lookup=lambda pid: AGENT,
                                            connect_fn=wh.connect, db_path=TMP)) == "parked_drifted"
          and wh.tier(7) == ("bronze",))
    forged = dict(item); pl = json.loads(item["original_text"]); pl["invocation"]["values"]["LoyaltyTier"] = "silver"
    forged["original_text"] = json.dumps(pl)
    check("a parked row whose values were altered is refused, nothing written",
          code_of(lambda: AP.perform_parked(st, U, forged, profile_lookup=lambda pid: AGENT,
                                            connect_fn=wh.connect, db_path=TMP)) == "parked_drifted"
          and wh.tier(7) == ("bronze",))

    kg = "kg-revoke"; st = ready(kg, posture="review"); wh = Warehouse()
    r = perform(st, kg, wh)
    item = RQ.get_item(U, r["review_id"], db_path=TMP)
    check("an approval after the agent lost its grant is refused",
          code_of(lambda: AP.perform_parked(st, U, item, profile_lookup=lambda pid: OTHER,
                                            connect_fn=wh.connect, db_path=TMP)) == "not_granted"
          and wh.tier(7) == ("bronze",))

    kg = "kg-revpre"; st = ready(kg, posture="review"); wh = Warehouse()
    r = perform(st, kg, wh, inv={"key": {"CustomerID": 8}, "values": {"LoyaltyTier": "gold"}})
    check("a write whose precondition fails is refused at park time, never parked",
          r["outcome"] == "refused" and r["refused_check"] == "not_closed" and not r.get("review_id"))
    check("…a non-action item cannot be performed",
          code_of(lambda: AP.perform_parked(st, U, {"source": "grounding"}, connect_fn=wh.connect)) == "not_parked")


def t_receipt():
    print("[§R] the sealed receipt")
    from trusted_data_agent.core.provenance import ProvenanceChain, verify_content
    kg = "kg-rcpt"; st = ready(kg); wh = Warehouse()
    tok = AP.begin_turn()
    try:
        perform(st, kg, wh)
        perform(st, kg, wh, inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "platinum"}})
        ch = ProvenanceChain(session_id="s", turn_number=1, user_uuid="u", profile_type="llm_only")
        env = ch.finalize()
        stamp = [{k: v for k, v in r.items() if not k.startswith("_")} for r in AP.turn_actions()]
    finally:
        AP.end_turn(tok)
    steps = [s["step_type"] for s in env.get("provenance_chain") or []]
    check("only the PERFORMED invocation is sealed", steps.count("action.performed") == 1, steps)
    v = verify_content(env, {"actions": stamp})
    check("offline verification re-derives the receipt from the turn's stamp", v["verified"] >= 1 and not v["mismatches"], v)
    bad = copy.deepcopy(stamp)
    for r in bad:
        if r["outcome"] == "performed":
            r["rows_affected"] = 99
    vb = verify_content(env, {"actions": bad})
    check("a tampered stamp is a NAMED mismatch", vb["mismatches"] and "action.performed" in vb["mismatches"][0], vb)
    check("a receipt with no stamp is a mismatch", verify_content(env, {})["mismatches"])
    check("the receipt content is a closed field set", set(json.loads(AP.receipt_content(stamp[0]))) == set(AP.RECEIPT_FIELDS))
    ch2 = ProvenanceChain(session_id="s", turn_number=2, user_uuid="u", profile_type="llm_only")
    check("a turn with no actions seals no action step",
          not any(s["step_type"] == "action.performed" for s in ch2.finalize().get("provenance_chain") or []))


def t_lane():
    print("[§L] the acted lane")
    recs = [{"outcome": "performed"}, {"outcome": "refused"}, {"outcome": "parked"}, {"outcome": "observed"}]
    sm = OB.scan_obda([], action_records=recs)
    check("acted and refused are counted from the record (parked/observed are neither)",
          sm["acted_count"] == 1 and sm["refused_count"] == 1)
    ev = OB.build_event(sm)
    check("a turn whose only activity was an action reads `acted`", ev and ev["lane"] == "acted", ev)
    check("a refused-only turn still produces a card", OB.build_event(OB.scan_obda([], action_records=[{"outcome": "refused"}])))
    plain = OB.scan_obda([{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT 1"}, "result": {"results": []}}])
    check("a turn without actions carries neither field (baseline)",
          "acted_count" not in plain and "acted_count" not in (OB.build_event(plain) or {}))


def t_wiring():
    print("[§W] wiring")
    es = open(os.path.join(ROOT, "src/trusted_data_agent/agent/execution_service.py")).read()
    check("the turn boundary starts and ends the action record",
          "_act_perf.begin_turn()" in es and "_act_perf.end_turn(_actions_token)" in es)
    sm = open(os.path.join(ROOT, "src/trusted_data_agent/core/session_manager.py")).read()
    check("the persistence chokepoint stamps the record", 'turn_data.setdefault("actions"' in sm)
    pv = open(os.path.join(ROOT, "src/trusted_data_agent/core/provenance.py")).read()
    check("finalize seals performed receipts through the shared content function",
          'self.add_step("action.performed", _act_perf.receipt_content(_rec)' in pv)
    ex = open(os.path.join(ROOT, "src/trusted_data_agent/agent/executor.py")).read()
    check("the lane scan reads the turn's actions", "scan_obda(tool_calls or [], action_records=_acts)" in ex)
    rr = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
    tree = ast.parse(rr)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "assurance_review_decide")
    src = ast.get_source_segment(rr, fn)
    perf, dec = src.find("_perform_parked_action("), src.find("review_queue.decide(")
    check("the review route performs a parked action BEFORE recording the approval", 0 < perf < dec, (perf, dec))
    check("…and a refused write returns 409 and records nothing",
          src.find('"action_not_performed"') != -1 and src.find('"action_not_performed"') < dec)


if __name__ == "__main__":
    for fn in (t_authority, t_perform, t_posture, t_receipt, t_lane, t_wiring):
        try: fn()
        except Exception as exc:  # noqa: BLE001
            import traceback; traceback.print_exc(); check(f"{fn.__name__} raised", False, repr(exc))
    try: os.remove(TMP)
    except OSError: pass
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
