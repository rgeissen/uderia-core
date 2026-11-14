#!/usr/bin/env python3
"""The Automation Builder — flows and scheduled tasks described for agents, and the role that uses them.

  §A  the descriptors — each flow and scheduler operation with its effect; a schedule is
      standing authority to act and spend, so creating or changing one is `governs` (always a
      person's approval); a deletion is always reviewed; the live run stream is not an operation
  §B  what is NOT on offer — where a scheduled task's results are SENT (email, webhook: an exit
      for the user's data), a scheduled database action, a pinned session; a flow's raw graph
      (edited with TDA_Flow); a run's debugging options — refused by bind
  §C  a fork is a copy of a platform flow or one's own — another user's private flow was
      copyable by id, definition and all (behavioural, on a scratch database)
  §D  credentials inside a JSON-text field are withheld — a task's `output_config` carries a
      bearer token and a webhook URL as text, which the key-based redaction never saw
  §E  the role — active; never a person's act; composes no agent and changes no knowledge;
      the Guide reads what runs on its own and changes nothing
  §F  names resolve in code — a flow (the caller's or a platform one) and a scheduled task

No server, no LLM, no writes to the instance. Run: python test/test_platform_automation.py
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("SECRET_KEY", "platform-automation-test")
logging.disable(logging.CRITICAL)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)[:400]) if detail else ''}")


from trusted_data_agent.main import create_app  # noqa: E402
from trusted_data_agent.api.operations import build_catalog  # noqa: E402
from trusted_data_agent.core import platform_operations as P  # noqa: E402
from trusted_data_agent.core import platform_roles as R  # noqa: E402

cat = build_catalog(create_app())
by = cat.by_op

# ── §A descriptors ──────────────────────────────────────────────────────────────
print("§A descriptors")
EFFECTS = {
    "flows.list": "read", "flows.get": "read", "flows.templates": "read", "flows.template": "read",
    "flows.platform": "read", "flows.versions": "read", "flows.runs": "read", "flows.run_status": "read",
    "flows.variables": "read",
    "flows.create": "write", "flows.update": "write", "flows.delete": "write", "flows.fork": "write",
    "flows.version_save": "write", "flows.version_restore": "write", "flows.run": "write", "flows.run_cancel": "write",
    "flows.toggle_platform": "human_only",
    "scheduled_tasks.list": "read", "scheduled_tasks.get": "read", "scheduled_tasks.runs": "read",
    "scheduler.status": "read",
    "scheduled_tasks.create": "write", "scheduled_tasks.update": "write", "scheduled_tasks.delete": "write",
    "scheduled_tasks.run_now": "write",
}
for op_id, eff in EFFECTS.items():
    check(f"{op_id} is described as {eff}", op_id in by and by[op_id].effect == eff, by.get(op_id) and by[op_id].effect)
for op_id in ("scheduled_tasks.create", "scheduled_tasks.update"):
    check(f"{op_id} always needs a person (standing authority)",
          op_id in by and by[op_id].governs and P.always_reviewed(by[op_id]) == "it changes what an agent may do")
for op_id in ("flows.delete", "scheduled_tasks.delete"):
    check(f"{op_id} always needs a person (it deletes)", op_id in by and P.always_reviewed(by[op_id]) == "it deletes")
for op_id in ("flows.run", "scheduled_tasks.run_now", "flows.create", "flows.run_cancel"):
    check(f"{op_id} follows the agent's write mode", op_id in by and P.always_reviewed(by[op_id]) is None)
check("promoting a flow to every user is an administrator's act, never an agent's",
      by["flows.toggle_platform"].tier == "admin" and P.availability(by["flows.toggle_platform"])[0] is False)
check("the run's live stream is never offered (an effect of never — plumbing, not an operation)",
      by.get("flows.run_stream") is not None and by["flows.run_stream"].effect == "never"
      and "flows.run_stream" not in [o.op for o in P._listed(cat, "admin", None)])
check("required arguments of a schedule are required", all(by["scheduled_tasks.create"].body[a].required
                                                           for a in ("name", "profile_id", "prompt", "schedule")))

# ── §B not on offer ─────────────────────────────────────────────────────────────
print("§B not on offer")
NOT_OFFERED = {
    "scheduled_tasks.create": ("output_channel", "output_config", "action_spec", "session_id"),
    "scheduled_tasks.update": ("output_channel", "output_config", "action_spec", "session_id"),
    "flows.create": ("definition",),
    "flows.update": ("definition",),
    "flows.run": ("start_node_id", "run_mode", "session_id", "node_session_mode"),
}
for op_id, names in NOT_OFFERED.items():
    o = by[op_id]
    accepted = set(o.path_params) | set(o.query) | set(o.body)
    check(f"{op_id} does not take {names}", not (accepted & set(names)), sorted(accepted & set(names)))
    for n in names:
        placed, err = P.bind(o, {**{k: "1" for k in o.path_params}, n: "x"})
        check(f"bind refuses {op_id}({n}=…)", placed is None and "does not take" in (err or ""), err)

# ── §C fork ─────────────────────────────────────────────────────────────────────
print("§C fork")
from trusted_data_agent.core import flow_db  # noqa: E402

_tmp = tempfile.mkdtemp()
_db = Path(_tmp) / "flows.db"
_c = sqlite3.connect(_db)
_c.executescript((ROOT / "schema/39_flow_system.sql").read_text())
for fid, owner, tmpl in (("f-mine", "alice", 0), ("f-theirs", "bob", 0), ("f-platform", "admin", 1)):
    _c.execute("INSERT INTO flows (flow_id, user_id, name, description, definition, is_template) VALUES (?,?,?,?,?,?)",
               (fid, owner, f"{fid} name", "", json.dumps({"nodes": [], "edges": [], "secret": owner}), tmpl))
_c.commit()
_c.close()
_real_path, _real_schema = flow_db._DB_PATH, flow_db._SCHEMA_APPLIED
flow_db._DB_PATH, flow_db._SCHEMA_APPLIED = _db, True
try:
    check("another user's private flow cannot be forked (reads as not found)", flow_db.fork_flow("f-theirs", "alice") is None)
    plat = flow_db.fork_flow("f-platform", "alice", name="Copy")
    check("a platform flow can be forked", bool(plat) and plat.get("user_id") == "alice" and plat.get("name") == "Copy", plat)
    mine = flow_db.fork_flow("f-mine", "alice")
    check("the caller's own flow can be forked", bool(mine) and mine.get("name") == "f-mine name (copy)", mine)
    check("an unknown flow cannot be forked", flow_db.fork_flow("f-none", "alice") is None)
    with sqlite3.connect(_db) as _c2:
        n = _c2.execute("SELECT COUNT(*) FROM flows WHERE user_id='alice' AND definition LIKE '%bob%'").fetchone()[0]
    check("nothing of bob's reached alice", n == 0, n)
finally:
    flow_db._DB_PATH, flow_db._SCHEMA_APPLIED = _real_path, _real_schema

# ── §D redaction inside JSON text ───────────────────────────────────────────────
print("§D redaction")
task = {"id": "t1", "name": "Morning", "output_channel": "webhook",
        "output_config": json.dumps({"webhook_url": "https://hooks.slack.com/services/T/B/x", "bearer_token": "abc",
                                     "to_address": "a@b.c"})}
out = P.redact({"tasks": [task]})["tasks"][0]
inner = json.loads(out["output_config"])
check("a bearer token inside a JSON-text field is withheld", inner["bearer_token"] == P.WITHHELD, inner)
check("a webhook URL is withheld (whoever holds it can post)", inner["webhook_url"] == P.WITHHELD, inner)
check("the rest of the field is kept", inner["to_address"] == "a@b.c" and out["name"] == "Morning")
check("text that only looks like JSON is left as it is", P.redact({"note": "{not json"}) == {"note": "{not json"})
check("a JSON array in text is left as it is", P.redact({"v": "[1, 2]"}) == {"v": "[1, 2]"})
check("a plain webhook_url key is withheld", P.redact({"webhook_url": "https://x"}) == {"webhook_url": P.WITHHELD})

# ── §E the role ─────────────────────────────────────────────────────────────────
print("§E the role")
reg = R.registry()
ab, guide = reg.roles["automation-builder"], reg.roles["guide"]
check("the Automation Builder is active, reads and writes", ab.active and set(ab.effects) == {"read", "write"})
for op_id, eff in EFFECTS.items():
    listed, why = R.role_state([ab], op_id, eff)
    check(f"the Automation Builder lists {op_id}", listed, why)
check("it composes no agent and changes no knowledge",
      not any(ab.lists(o.op) for o in cat.operations if o.effect == "write"
              and o.op.split(".")[0] in ("profiles", "rag", "knowledge", "files", "skills", "components")))
GUIDE_READS = ("flows.list", "flows.get", "flows.runs", "flows.run_status", "scheduled_tasks.list",
               "scheduled_tasks.runs", "scheduler.status")
for op_id in GUIDE_READS:
    check(f"the Guide reads {op_id}", R.role_state([guide], op_id, "read") == (True, None))
check("the Guide changes nothing that runs on its own",
      not any(guide.lists(o.op) for o in cat.operations if o.effect == "write"
              and o.op.split(".")[0] in ("flows", "scheduled_tasks")))
check("the Guide is taught only the reading automation playbook",
      guide.teaches("platform-playbooks", "automation/status")
      and not any(guide.teaches("platform-playbooks", f"automation/{a}") for a in ("create-flow", "run-flow", "schedule")))
found = [f["op"] for f in P.find("schedule a question every weekday for an agent", "user", cat=cat, roles=[ab], writes="review")["found"]]
check("find, scoped to the Automation Builder, finds scheduling among its first two", "scheduled_tasks.create" in found[:2], found)

# ── §F resolvers ────────────────────────────────────────────────────────────────
print("§F resolvers")
_lf, _lp = flow_db.list_flows, flow_db.list_platform_flows
flow_db.list_flows = lambda u, include_templates=True: [{"flow_id": "f-1", "name": "Quarterly answer"}] if u == "u" else []
flow_db.list_platform_flows = lambda: [{"flow_id": "f-p", "name": "Weekly report"}]
try:
    check("a flow by name → its id", P.resolve_flow("quarterly answer", "u") == ("f-1", None))
    check("a platform flow by name (forkable)", P.resolve_flow("Weekly report", "u") == ("f-p", None))
    v, err = P.resolve_flow("Monthly", "u")
    check("an unknown flow is refused, naming the user's", v is None and "Quarterly answer (f-1)" in err, err)
finally:
    flow_db.list_flows, flow_db.list_platform_flows = _lf, _lp
from trusted_data_agent.core import task_scheduler as TS  # noqa: E402
_lt = TS.list_tasks
TS.list_tasks = lambda u, profile_id=None: [{"id": "t-1", "name": "Morning orders"}] if u == "u" else []
try:
    check("a scheduled task by name → its id", P.resolve_task("morning orders", "u") == ("t-1", None))
    check("another user's task is not this user's", P.resolve_task("Morning orders", "other")[0] is None)
finally:
    TS.list_tasks = _lt
check("flow_id and task_id are resolved in code", P.RESOLVERS.get("flow_id") is P.resolve_flow
      and P.RESOLVERS.get("task_id") is P.resolve_task)

print(f"{'FAILED' if failed else 'ALL PASS'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
