"""Every platform-connector tool execution leaves an audit record.

The shell connector's enable dialog told the admin "All executions are audit-logged", and
nothing wrote one: the connector process logged errors to stderr only. Now the one dispatch
every engine reaches a connector through (core/platform_connector_registry.
invoke_connector_tool) writes an audit row per execution: user, connector, tool, arguments
(credential-like keys redacted, long values truncated), outcome, duration.

CI-pure: in-memory SQLite, a fake invocation strategy. No connector process, no Docker.

Run:  PYTHONPATH=src python3 test/test_connector_audit.py
"""
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trusted_data_agent.auth.database as auth_db
from trusted_data_agent.auth.models import AuditLog, Base, User
import trusted_data_agent.core.platform_connector_registry as reg

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine, tables=[User.__table__, AuditLog.__table__])
auth_db.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
db = auth_db.SessionLocal()
db.add(User(id="u-1", username="u1", email="u1@x.test", password_hash="x"))
db.commit()


class FakeStrategy(reg.ConnectorInvocationStrategy):
    def __init__(self):
        self.mode = "ok"

    async def invoke_tool(self, server, tool_name, args, env):
        if self.mode == "raise":
            raise RuntimeError("container failed to start")
        if self.mode == "slow":
            await asyncio.sleep(5)
        if self.mode == "error":
            return {"status": "error", "error": "exit code 2"}
        return {"status": "success", "results": [{"stdout": "ok"}]}


fake = FakeStrategy()
reg._INVOCATION_STRATEGIES["fake_type"] = fake
reg.get_server = lambda sid: {"id": sid, "connector_type": "fake_type"}
reg.get_server_credentials = lambda sid: {}


def rows():
    return db.query(AuditLog).filter_by(action=reg.CONNECTOR_AUDIT_ACTION).order_by(AuditLog.timestamp).all()


async def run():
    r = await reg.invoke_connector_tool("uderia-shell", "shell__exec_command",
                                        {"command": "ls -la /tmp", "api_key": "sk-live-123"}, user_uuid="u-1")
    check("the tool's result is returned unchanged", r.get("status") == "success", r)
    rs = rows()
    check("one execution, one audit row", len(rs) == 1, len(rs))
    row = rs[0]
    meta = json.loads(row.metadata_json or "{}")
    check("the row names the user", row.user_id == "u-1")
    check("the row names the connector and the tool (namespace stripped)",
          row.resource == "connector:uderia-shell/exec_command", row.resource)
    check("the row records the command", meta.get("args", {}).get("command") == "ls -la /tmp", meta)
    check("a credential-like argument is redacted", meta.get("args", {}).get("api_key") == "[redacted]", meta)
    check("the outcome is success", row.status == "success")
    check("the duration is recorded", isinstance(meta.get("duration_ms"), int), meta)

    fake.mode = "error"
    await reg.invoke_connector_tool("uderia-shell", "exec_command", {"command": "false"}, user_uuid="u-1")
    row = rows()[-1]
    check("a failed execution is recorded as a failure, with its error",
          row.status == "failure" and "exit code 2" in (json.loads(row.metadata_json).get("error") or ""))

    fake.mode = "raise"
    r = await reg.invoke_connector_tool("uderia-shell", "exec_command", {"command": "x"}, user_uuid="u-1")
    check("an execution that raises is still recorded", rows()[-1].status == "failure" and r["status"] == "error")

    fake.mode = "slow"
    saved = reg._INVOKE_TIMEOUT
    reg._INVOKE_TIMEOUT = 0.05
    try:
        await reg.invoke_connector_tool("uderia-shell", "exec_command", {"command": "sleep 99"}, user_uuid="u-1")
    finally:
        reg._INVOKE_TIMEOUT = saved
    check("a timed-out execution is recorded", "timed out" in (json.loads(rows()[-1].metadata_json).get("error") or ""))

    fake.mode = "ok"
    await reg.invoke_connector_tool("uderia-files", "write_file", {"content": "x" * 5000}, user_uuid="u-1")
    content = json.loads(rows()[-1].metadata_json)["args"]["content"]
    check("a long argument is truncated, with its length", len(content) < 2100 and "5000 chars" in content, len(content))
    check("every execution so far left exactly one row", len(rows()) == 5, len(rows()))

    def broken(*a, **k):
        raise RuntimeError("audit store down")
    import trusted_data_agent.auth.audit as audit
    saved_log = audit.log_audit_event
    audit.log_audit_event = broken
    try:
        r = await reg.invoke_connector_tool("uderia-shell", "exec_command", {"command": "ls"}, user_uuid="u-1")
    finally:
        audit.log_audit_event = saved_log
    check("an audit failure never breaks the tool", r.get("status") == "success", r)

asyncio.run(run())

manifest = json.loads((_ROOT / "mcp_servers/builtin/uderia-shell/manifest.json").read_text(encoding="utf-8"))
ack = manifest.get("security_acknowledgment_text", "")
check("the shell enable dialog states the audit record it now gets", "audit log" in ack, ack)

print(f"\ntest_connector_audit: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
