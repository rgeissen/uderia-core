"""
Audit integrity gate (CI-pure; throwaway SQLite databases, a local UDP socket).

  - Append-only triggers on every audit table: UPDATE refused, DELETE of a row
    younger than the retention period refused, older rows purgeable.
  - audit_logs: the only update allowed is the database's own user_id -> NULL
    on account deletion, and deleting a user keeps its audit records.
  - Retention defaults to and never goes below 365 days.
  - SIEM export: JSON lines to a file and to syslog over UDP; off by default;
    a broken sink never fails the audited action.

Run: PYTHONPATH=src python test/test_audit_integrity.py
"""
import json
import os
import socket
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import audit_integrity as ai

PASS = FAIL = 0
SAVED = {k: os.environ.get(k) for k in ("TDA_AUDIT_RETENTION_DAYS", "TDA_AUDIT_SYSLOG", "TDA_AUDIT_JSONL")}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name} {detail}")


def refused(conn, sql, params=()):
    try:
        conn.execute(sql, params)
        conn.commit()
        return False
    except sqlite3.DatabaseError as e:
        conn.rollback()
        return "audit records" in str(e)


for k in SAVED:
    os.environ.pop(k, None)

# 1. Retention
check("default retention 365", ai.retention_days() == 365)
os.environ["TDA_AUDIT_RETENTION_DAYS"] = "30"
check("cannot go below 365", ai.retention_days() == 365)
os.environ["TDA_AUDIT_RETENTION_DAYS"] = "2555"
check("longer retention honoured", ai.retention_days() == 2555)
os.environ.pop("TDA_AUDIT_RETENTION_DAYS")

# 2. Triggers on a database shaped like production
db = os.path.join(tempfile.mkdtemp(), "auth.db")
conn = sqlite3.connect(db)
conn.execute("PRAGMA foreign_keys=ON")
conn.executescript("""
CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT);
CREATE TABLE audit_logs (id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL, resource TEXT, status TEXT NOT NULL, ip_address TEXT, user_agent TEXT,
  details TEXT, timestamp DATETIME NOT NULL);
CREATE TABLE sso_sync_events (id TEXT PRIMARY KEY, user_uuid TEXT, synced_at TEXT NOT NULL);
CREATE TABLE kg_lifecycle_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT, action TEXT, created_at TEXT NOT NULL);
CREATE TABLE pack_lifecycle_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, installation_id INTEGER, action TEXT, created_at TEXT NOT NULL);
""")
done = ai.install_triggers(conn)
check("triggers on all four tables", sorted(done) == sorted(ai._AUDIT_TABLES), str(done))
check("re-install is idempotent", sorted(ai.install_triggers(conn)) == sorted(done))

conn.execute("INSERT INTO users VALUES ('u1','alice')")
# SQLAlchemy stores naive 'YYYY-MM-DD HH:MM:SS.ffffff'; the governance tables store ISO with offset
conn.execute("INSERT INTO audit_logs VALUES ('a-new','u1','login','/login','success','1.2.3.4','ua','ok', datetime('now'))")
conn.execute("INSERT INTO audit_logs VALUES ('a-old','u1','login','/login','success','1.2.3.4','ua','ok', datetime('now','-400 days'))")
conn.execute("INSERT INTO kg_lifecycle_audit (kg_id, action, created_at) VALUES ('kg1','publish', strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))")
conn.execute("INSERT INTO kg_lifecycle_audit (kg_id, action, created_at) VALUES ('kg1','draft', strftime('%Y-%m-%dT%H:%M:%f+00:00','now','-500 days'))")
conn.execute("INSERT INTO pack_lifecycle_audit (installation_id, action, created_at) VALUES (1,'publish', strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))")
conn.execute("INSERT INTO sso_sync_events VALUES ('s1','u1', strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))")
conn.commit()

check("audit_logs: edit refused", refused(conn, "UPDATE audit_logs SET details='forged' WHERE id='a-new'"))
check("audit_logs: status edit refused", refused(conn, "UPDATE audit_logs SET status='success' WHERE id='a-new'"))
check("audit_logs: recent delete refused", refused(conn, "DELETE FROM audit_logs WHERE id='a-new'"))
check("audit_logs: bulk wipe refused", refused(conn, "DELETE FROM audit_logs"))
check("audit_logs: row older than retention purgeable",
      not refused(conn, "DELETE FROM audit_logs WHERE id='a-old'"))
check("kg audit: edit refused", refused(conn, "UPDATE kg_lifecycle_audit SET action='x'"))
check("kg audit: recent delete refused", refused(conn, "DELETE FROM kg_lifecycle_audit WHERE action='publish'"))
check("kg audit: old row purgeable (ISO+offset timestamps)",
      not refused(conn, "DELETE FROM kg_lifecycle_audit WHERE action='draft'"))
check("pack audit: delete refused", refused(conn, "DELETE FROM pack_lifecycle_audit"))
check("sso sync: edit refused", refused(conn, "UPDATE sso_sync_events SET user_uuid='x'"))
check("audit_logs: reassigning user_id refused",
      refused(conn, "UPDATE audit_logs SET user_id='someone-else' WHERE id='a-new'"))

# Account deletion keeps the audit record and only nulls the user reference
check("deleting the user is allowed", not refused(conn, "DELETE FROM users WHERE id='u1'"))
row = conn.execute("SELECT user_id, action, details FROM audit_logs WHERE id='a-new'").fetchone()
check("audit record survives the account", row is not None and row[1] == "login" and row[2] == "ok", str(row))
check("user reference nulled", row is not None and row[0] is None)

# 3. ORM relationship no longer cascades deletes into audit_logs
from trusted_data_agent.auth.models import User
rel = User.__mapper__.relationships["audit_logs"]
check("User.audit_logs does not cascade delete", "delete" not in rel.cascade)
check("User.audit_logs leaves rows to the database", rel.passive_deletes == "all")

# 4. Retention floor in the purge helper
src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "auth", "audit.py")).read()
check("purge clamps to retention", "max(days, retention_days())" in src)
check("audit events are exported", "export_event(" in src)

# 5. SIEM export
ai._reset_for_tests()
ai.export_event({"action": "noop"})  # off by default: must not raise
jsonl = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("127.0.0.1", 0))
sock.settimeout(2)
port = sock.getsockname()[1]
os.environ["TDA_AUDIT_JSONL"] = jsonl
os.environ["TDA_AUDIT_SYSLOG"] = f"udp://127.0.0.1:{port}"
ai._reset_for_tests()
ai.export_event({"action": "login", "user_id": "u1", "status": "success"})
lines = open(jsonl).read().splitlines()
check("JSON line written", len(lines) == 1 and json.loads(lines[0])["action"] == "login", str(lines))
try:
    data = sock.recv(4096).decode()
except socket.timeout:
    data = ""
check("syslog datagram received", "uderia-audit:" in data and '"action": "login"' in data, data[:120])
sock.close()

os.environ["TDA_AUDIT_JSONL"] = "/nonexistent-dir/x/audit.jsonl"
os.environ.pop("TDA_AUDIT_SYSLOG")
ai._reset_for_tests()
try:
    ai.export_event({"action": "x"})
    check("broken sink never raises", True)
except Exception as e:
    check("broken sink never raises", False, str(e))

# 6. The event's structured detail is stored, read back and append-only
#    (before Oct 2026 the model had no column: AuditLog(metadata=...) set SQLAlchemy's own
#    schema attribute on the instance and nothing reached the row; the reader then handed
#    that schema object to the JSON encoder, so GET /v1/auth/me/audit-logs answered 500)
conn.execute("INSERT INTO users VALUES ('u2','bob')")
conn.execute("ALTER TABLE audit_logs ADD COLUMN metadata_json TEXT")
ai.install_triggers(conn)
conn.execute("INSERT INTO audit_logs VALUES ('m-1','u2','file_download','f','success','1.2.3.4','ua','d', datetime('now'), '{\"bytes\": 9}')")
conn.commit()
check("metadata_json cannot be rewritten",
      refused(conn, "UPDATE audit_logs SET metadata_json='{}' WHERE id='m-1'"))
check("account deletion still nulls only the user reference",
      not refused(conn, "DELETE FROM users WHERE id='u2'")
      and conn.execute("SELECT user_id, metadata_json FROM audit_logs WHERE id='m-1'").fetchone() == (None, '{"bytes": 9}'))
from trusted_data_agent.auth.models import AuditLog
check("the model has a metadata_json column", "metadata_json" in AuditLog.__table__.columns)
check("the model has no `metadata` column (SQLAlchemy reserves the name)", "metadata" not in AuditLog.__table__.columns)

import subprocess
_e2e = r"""
import json, os, sqlite3, sys, tempfile
d = tempfile.mkdtemp()
legacy = os.path.join(d, 'legacy.db')
c = sqlite3.connect(legacy)
c.execute("CREATE TABLE audit_logs (id TEXT PRIMARY KEY, user_id TEXT, action TEXT NOT NULL, resource TEXT, status TEXT NOT NULL, ip_address TEXT, user_agent TEXT, details TEXT, timestamp DATETIME NOT NULL)")
c.commit(); c.close()
os.environ['TDA_AUTH_DB_URL'] = 'sqlite:///' + legacy
from trusted_data_agent.auth import database as db
db._run_audit_table_migrations(); db._run_audit_table_migrations()
cols = [r[1] for r in sqlite3.connect(legacy).execute('PRAGMA table_info(audit_logs)')]
from trusted_data_agent.auth.models import Base, User
for t in Base.metadata.sorted_tables:
    try: t.create(bind=db.engine, checkfirst=True)
    except Exception: pass
with db.get_db_session() as s:
    s.add(User(id='u-meta', username='meta', email='m@example.com', password_hash='x'))
from trusted_data_agent.auth import audit
audit.log_audit_event('u-meta', 'file_download', 'Downloaded x.csv', resource='file:1',
                      ip_address='1.2.3.4', user_agent='t', metadata={'bytes': 12, 'format': 'csv'})
audit.log_audit_event('u-meta', 'login', 'no metadata', ip_address='1.2.3.4', user_agent='t')
logs = audit.get_user_audit_logs('u-meta')
print(json.dumps({'cols': cols, 'logs': logs}))
"""
_r = subprocess.run([sys.executable, "-c", _e2e], capture_output=True, text=True,
                    env={**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src")},
                    cwd=os.path.join(os.path.dirname(__file__), ".."))
try:
    _out = json.loads(_r.stdout.strip().splitlines()[-1])
except Exception:
    _out = {}
    print(_r.stderr[-1500:])
check("the startup migration adds metadata_json to an existing table, idempotently",
      _out.get("cols", []).count("metadata_json") == 1, str(_out.get("cols")))
_by = {l["action"]: l for l in _out.get("logs", [])}
check("log_audit_event stores the metadata and the reader returns it",
      _by.get("file_download", {}).get("metadata") == {"bytes": 12, "format": "csv"}, str(_by.get("file_download")))
check("an event without metadata reads None, and the whole list is JSON",
      "login" in _by and _by["login"].get("metadata") is None)

ai._reset_for_tests()
for k, v in SAVED.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
