"""PROP-008 increment C2b — a signed action on a schedule (§6).

The REAL scheduler store (a temp SQLite with `schema/28_scheduled_tasks.sql` applied and the
self-applying migrations run), the REAL run path (`_execute_task_async`) and the REAL
unattended seam over the B3 harness (signed action, SQLite twin of the target). Stand-ins,
stated: the agent's graph lookup and the config manager (resolving them for real would read
the user's stored configuration).

  §M  migrations: `action_spec` on tasks and `action_record` on runs are added once, idempotent
  §S  save: a prompt task is byte-identical (no spec stored); an action task needs no prompt;
      the spec is validated by the ONE shared rule — ungranted, no agent, OBDA-MODEL · OBDA-ACT off and a
      malformed spec are refused before anything is stored; an update cannot check a
      different agent than the one the task runs as
  §R  run: no LLM, no session, no tokens — the posture decides (enforce writes; review parks;
      observe records); each run records the act with its signed receipt; a refusal is an
      `error` run; a grant revoked after saving refuses the run and writes nothing
  §T  the tool face: `action_spec` exists only behind [[gate:act]] (OBDA-MODEL · OBDA-ACT off ⇒ the tool is
      unchanged); create without a prompt is admitted only with a spec
  §W  wiring: REST create/update map the refusal to 400 with its code

Run: PYTHONPATH=src:. python3 test/test_scheduler_actions.py
"""
import asyncio
import copy
import json
import os
import sqlite3
import sys
import tempfile

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

from pathlib import Path
from trusted_data_agent.core import task_scheduler as TS
import trusted_data_agent.core.config_manager as CM
from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler

AGENT = {"id": "profile-agent", "tag": "AGT", "authorityConfig": {"actions": {"types": [NAME]}},
         "componentConfig": {"action": {}}}
OTHER = {"id": "profile-other", "tag": "OTH", "authorityConfig": {}}
PROFILES = {p["id"]: p for p in (AGENT, OTHER)}

class _CM:
    def get_profile(self, pid, uid=None): return PROFILES.get(pid)
    def get_profiles(self, uid=None, user_uuid=None): return list(PROFILES.values())

DB = os.path.join(tempfile.mkdtemp(), "sched.db")
_c = sqlite3.connect(DB)
_c.executescript(open(os.path.join(ROOT, "schema", "28_scheduled_tasks.sql")).read())
_c.commit(); _c.close()
TS._DB_PATH = Path(DB)
CM.get_config_manager = lambda: _CM()
SPEC = {"action": NAME, "invocation": {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}}}


def cols(table):
    c = sqlite3.connect(DB)
    try: return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    finally: c.close()


def bind(kg, wh):
    st = store(kg)
    KnowledgeGraphHandler._get_store = lambda self, ctx: st
    AP.open_connection = lambda user_uuid, connection_id: wh.connect()


def t_migrations():
    print("[§M] migrations")
    TS._ensure_columns(); TS._ensure_columns()
    check("action_spec added to scheduled_tasks", "action_spec" in cols("scheduled_tasks"))
    check("action_record added to scheduled_task_runs", "action_record" in cols("scheduled_task_runs"))
    check("idempotent (a second run changes nothing)", list(cols("scheduled_tasks")).count("action_spec") == 1)


def t_save():
    print("[§S] save")
    t = TS.create_task(U, "profile-agent", "daily report", "summarise sales", "0 9 * * *")
    check("a prompt task stores no spec (byte-identical baseline)", t.get("action_spec") is None and t["prompt"] == "summarise sales")
    t = TS.create_task(U, "profile-agent", "promote", "", "0 2 * * *", action_spec=SPEC)
    stored = json.loads(t["action_spec"])
    check("an action task needs no prompt (a descriptive one is filled)", t["prompt"].startswith("Perform the signed action"))
    check("the spec is stored canonical", stored == {"action": NAME, "invocation": SPEC["invocation"]}, stored)
    t2 = TS.create_task(U, "profile-agent", "promote2", "", "0 2 * * *", action_spec=json.dumps(SPEC))
    check("a JSON-string spec is accepted (the tool's dict arg arrives decoded or as text)", json.loads(t2["action_spec"])["action"] == NAME)
    def refused(fn):
        try: fn(); return None
        except TS.ActionTaskError as e: return e
    e = refused(lambda: TS.create_task(U, "profile-other", "x", "", "0 2 * * *", action_spec=SPEC))
    check("an ungranted agent is refused, naming it and what it is granted",
          e and e.code == "action_not_granted" and "@OTH is not granted" in str(e) and "granted: none" in str(e), str(e))
    e = refused(lambda: TS.create_task(U, "profile-ghost", "x", "", "0 2 * * *", action_spec=SPEC))
    check("no resolvable agent is refused", e and "names no agent" in str(e), str(e))
    e = refused(lambda: TS.create_task(U, "profile-agent", "x", "", "0 2 * * *", action_spec={"invocation": {}}))
    check("a spec that names no action is refused", e and "must name an action" in str(e))
    e = refused(lambda: TS.create_task(U, "profile-agent", "x", "", "0 2 * * *", action_spec={"action": NAME, "invocation": [1]}))
    check("a non-object invocation is refused", e and "must be an object" in str(e))
    e = refused(lambda: TS.create_task(U, "profile-agent", "x", "", "0 2 * * *", action_spec="{nope"))
    check("an unparseable spec is refused", e and "not JSON" in str(e))
    _OP.force_act(False)
    try:
        e = refused(lambda: TS.create_task(U, "profile-agent", "x", "", "0 2 * * *", action_spec=SPEC))
        check("OBDA-ACT off for the agent refuses an action task", e and "OBDA-ACT is not enabled" in str(e), str(e))
    finally:
        _OP.force_act(True)
    n = len(TS.list_tasks(U))
    check("nothing refused was stored", n == 3, n)
    other = TS.create_task(U, "profile-other", "other prompt", "hi", "0 1 * * *")
    e = refused(lambda: TS.update_task(other["id"], U, {"action_spec": SPEC, "profile_id": "profile-agent"}))
    check("an update checks the STORED agent — naming another agent in the body cannot pass the check",
          e and "@OTH" in str(e), str(e))
    u = TS.update_task(t["id"], U, {"action_spec": {"action": NAME, "invocation": {"key": {"CustomerID": 8}, "values": {}}}})
    check("an update to a granted spec is stored", json.loads(u["action_spec"])["invocation"]["key"] == {"CustomerID": 8})


def run(task_id):
    asyncio.run(TS._execute_task_async(task_id))
    return TS.list_runs(task_id, U)[0]


def t_run():
    print("[§R] run")
    kg = "kg-s-enf"; ready(kg); wh = Warehouse(); bind(kg, wh)
    t = TS.create_task(U, "profile-agent", "nightly", "", "0 2 * * *", action_spec=SPEC)
    import trusted_data_agent.agent.execution_service as ES
    orig = getattr(ES, "run_agent_execution", None)
    called = []
    async def _no_llm(*a, **k): called.append(1); raise AssertionError("an action task must not run the agent")
    ES.run_agent_execution = _no_llm
    try:
        r = run(t["id"])
    finally:
        if orig: ES.run_agent_execution = orig
    rec = r.get("action_record") or {}
    check("enforce writes the row", wh.tier(7) == ("gold",), wh.tier(7))
    check("no LLM ran, no tokens, no cost", not called and r.get("tokens_used") == 0 and r.get("cost_usd") == 0.0, r)
    check("the run is a success with the act in words", r["status"] == "success" and "performed" in (r.get("result_summary") or ""), r)
    check("the run records the act with its signed receipt and its face",
          rec.get("outcome") == "performed" and (rec.get("signed_receipt") or {}).get("content_hash") and rec.get("face") == "scheduler", rec)
    check("the receipt names the agent", rec.get("principal") == "agent:profile-agent")
    check("the task's last run reads success", TS.get_task(t["id"], U)["last_run_status"] == "success")
    r2 = run(t["id"])
    check("a second night is its own run with its own record", r2["id"] != r["id"] and (r2.get("action_record") or {}).get("invocation_hash"))
    s1, s2 = (r.get("action_record") or {}).get("signed_receipt") or {}, (r2.get("action_record") or {}).get("signed_receipt") or {}
    check("two runs with identical content still carry two DISTINCT signed receipts (the occurrence is signed)",
          s1.get("content") == s2.get("content") and s1.get("signature") and s1.get("signature") != s2.get("signature"))
    check("each receipt names its own run and its face",
          (s1.get("occurrence") or {}).get("ref") == r["id"] and (s2.get("occurrence") or {}).get("ref") == r2["id"]
          and (s1.get("occurrence") or {}).get("face") == "scheduler")
    import base64
    from trusted_data_agent.core import provenance
    try:
        provenance._get_provenance_signing_key().public_key().verify(base64.b64decode(s1["signature"]), s1["payload"].encode())
        verified = True
    except Exception as e:  # noqa: BLE001
        verified = repr(e)
    check("the signature verifies over {receipt, occurrence} with the public key alone", verified is True, verified)
    check("the signed payload carries the sealed content and the occurrence",
          json.loads(s1["payload"]) == {"receipt": json.loads(s1["content"]), "occurrence": s1["occurrence"]})
    import sqlite3 as _sq
    _n = lambda: _sq.connect(DB).execute("SELECT COUNT(*) FROM scheduled_task_runs WHERE task_id = ?", (t["id"],)).fetchone()[0]
    before = _n()
    check("deleting a task removes its run history (and the act records on it) — never orphaned",
          before >= 2 and TS.delete_task(t["id"], U) and _n() == 0, (before, _n()))
    kg = "kg-s-rev"; ready(kg, posture="review"); wh = Warehouse(); bind(kg, wh)
    t = TS.create_task(U, "profile-agent", "nightly-rev", "", "0 2 * * *", action_spec=SPEC)
    r = run(t["id"])
    check("review parks for the owner, nothing written, the run succeeds (the posture did its job)",
          (r.get("action_record") or {}).get("outcome") == "parked" and wh.tier(7) == ("bronze",) and r["status"] == "success", r)
    kg = "kg-s-obs"; ready(kg, posture="observe"); wh = Warehouse(); bind(kg, wh)
    t = TS.create_task(U, "profile-agent", "nightly-obs", "", "0 2 * * *", action_spec=SPEC)
    r = run(t["id"])
    check("observe records, writes nothing", (r.get("action_record") or {}).get("outcome") == "observed" and wh.tier(7) == ("bronze",))
    kg = "kg-s-revoke"; ready(kg); wh = Warehouse(); bind(kg, wh)
    t = TS.create_task(U, "profile-agent", "nightly-rv", "", "0 2 * * *", action_spec=SPEC)
    saved = copy.deepcopy(AGENT["authorityConfig"])
    AGENT["authorityConfig"] = {}
    try:
        r = run(t["id"])
    finally:
        AGENT["authorityConfig"] = saved
    check("a grant revoked after saving refuses the run: error, nothing written, the reason recorded",
          r["status"] == "error" and wh.tier(7) == ("bronze",)
          and (r.get("action_record") or {}).get("refused_check") == "not_granted"
          and "refused (not_granted)" in (r.get("result_summary") or ""), r)
    check("the task's last run reads error", TS.get_task(t["id"], U)["last_run_status"] == "error")
    check("a run status follows the act (pure)", TS._action_run_status({"outcome": "refused"}) == "error"
          and TS._action_run_status({"outcome": "parked"}) == "success" and TS._action_run_status({}) == "success")


def t_tool():
    print("[§T] the tool face")
    from trusted_data_agent.components.manager import ComponentManager
    sys.path.insert(0, os.path.join(ROOT, "components", "builtin", "scheduler"))
    from components.builtin.scheduler.handler import SchedulerComponentHandler as SH
    man = json.load(open(os.path.join(ROOT, "components", "builtin", "scheduler", "manifest.json")))
    tool = next(t for t in man["tool_definitions"] if t["name"] == "TDA_Scheduler") if "tool_definitions" in man else man["tool_definition"]
    _OP.force_act(False)
    try:
        off = ComponentManager._gated_tool_definition(tool, {"id": "p"})
        check("OBDA-MODEL · OBDA-ACT off: no action_spec argument reaches the schema", "action_spec" not in off["args"])
    finally:
        _OP.force_act(True)
    on = ComponentManager._gated_tool_definition(tool, {"id": "p"})
    check("ACT on: the argument is offered and says the posture decides", "action_spec" in on["args"]
          and "posture decides" in on["args"]["action_spec"]["description"])
    ok, why = SH().validate_arguments({"action": "create", "name": "n", "schedule": "0 1 * * *", "action_spec": SPEC})
    check("create without a prompt is admitted with a spec", ok, why)
    ok, why = SH().validate_arguments({"action": "create", "name": "n", "schedule": "0 1 * * *"})
    check("create without a prompt and without a spec is still refused", not ok and "prompt" in why)


def t_wiring():
    print("[§W] wiring")
    rr = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
    i = rr.index("from trusted_data_agent.core.task_scheduler import create_task")
    seg = rr[i:i + 3000]
    check("REST create passes the spec and drops the prompt requirement only with one",
          "action_spec=data.get(\"action_spec\") or None" in seg and 'if data.get("action_spec")' in seg)
    check("REST create maps the refusal to 400 with its code", "isinstance(e, ActionTaskError)" in seg and "e.code" in seg)
    j = rr.index("async def update_scheduled_task(")
    check("REST update maps it too", "isinstance(e, ActionTaskError)" in rr[j:j + 1500])
    ts = open(os.path.join(ROOT, "src/trusted_data_agent/core/task_scheduler.py")).read()
    check("the run goes through the ONE unattended seam", "AP.perform_unattended" in ts and 'face="scheduler"' in ts)
    check("the save goes through the ONE shared grant rule", "AP.unattended_grant_error" in ts)


if __name__ == "__main__":
    _OP.force_act(True)
    for fn in (t_migrations, t_save, t_run, t_tool, t_wiring):
        try:
            fn()
        except Exception as e:  # a raising section is a NAMED failure, never a silent abort
            import traceback; traceback.print_exc()
            check(f"{fn.__name__} raised", False, repr(e))
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
