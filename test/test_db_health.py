"""Database health: corruption made loud, the daily verified backup. CI-pure.

FOUND (production, 2026-10-29): tda_auth.db went `file is not a database` at 11:05 UTC and the
platform ran on it for eight hours — every unreadable read was handled (the pool reconnected)
but only logged; logins failed and nobody was told. The recovery point was an upgrade backup
that happened to exist. core/db_health.py: the pool hook alerts (state, CRITICAL, a live banner
on every open stream — never a read of the broken database); a daily platform job takes a
verified backup that is also the daily integrity read of the live file.
"""
import datetime, os, pathlib, sqlite3, sys, tempfile, time, types, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)
from trusted_data_agent.core import db_health as DH
from trusted_data_agent.core import schema_ledger as SL

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
ROOT = pathlib.Path(__file__).resolve().parents[1]
src = lambda rel: (ROOT / rel).read_text()

sent = []
DH._broadcast = lambda summary: sent.append(summary)
def reset():
    DH._STATE.update({"state": "ok", "first_seen": None, "last_seen": None, "count": 0, "last_error": None,
                      "last_alert_at": 0.0, "last_backup": None}); sent.clear()

print("\n── §1 loud: an unreadable read is recorded and announced, rate-limited")
reset()
DH.report_unreadable("file is not a database")
st = DH.status()
check("the state reads unreadable, with the error, the count and when it began",
      st["state"] == "unreadable" and st["count"] == 1 and "not a database" in st["last_error"] and st["first_seen"])
check("the first read is announced to every open stream", len(sent) == 1 and "unreadable" in sent[0] and "restore" in sent[0])
for _ in range(5):
    DH.report_unreadable("database disk image is malformed")
check("further reads inside the interval are counted, not re-announced", DH.status()["count"] == 6 and len(sent) == 1)
DH._STATE["last_alert_at"] = time.monotonic() - DH.ALERT_INTERVAL_S - 1
DH.report_unreadable("disk i/o error")
check("…and re-announced once the interval has passed", len(sent) == 2)
_orig = DH._broadcast
DH._broadcast = lambda s: 1 / 0
DH._STATE["last_alert_at"] = 0.0
try:
    DH.report_unreadable("x"); raised = False
except Exception:
    raised = True
DH._broadcast = _orig
check("an alert never raises into the failing request", not raised)
DH.report_readable()
check("a verified full read clears the state", DH.status()["state"] == "ok" and DH.status()["count"] == 0)

print("\n── §2 the pool's error hook is the trigger")
from trusted_data_agent.auth import database as ADB
reset()
ctx = types.SimpleNamespace(original_exception=Exception("database disk image is malformed"), is_disconnect=False)
ADB.invalidate_on_unreadable_file(ctx)
check("an unreadable-file error invalidates the connection AND reports", ctx.is_disconnect is True and DH.status()["state"] == "unreadable")
reset()
ADB.invalidate_on_unreadable_file(types.SimpleNamespace(original_exception=Exception("no such table: foo"), is_disconnect=False))
check("an ordinary SQL error reports nothing", DH.status()["state"] == "ok" and not sent)

print("\n── §3 the daily backup: consistent, verified, pruned")
tmp = pathlib.Path(tempfile.mkdtemp())
db = tmp / "tda_auth.db"
c = sqlite3.connect(db); c.execute("PRAGMA journal_mode=WAL")
c.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, blob TEXT)")
c.executemany("INSERT INTO users VALUES (?, ?, ?)", [(f"u{i}", f"user{i}", "x" * 400) for i in range(3000)])
c.commit(); c.close()
dest = tmp / "backups"
reset()
r = DH.run_backup(db, dest, keep=2, now=datetime.datetime(2026, 10, 30, 2, 30, 0))
bk = pathlib.Path(r["path"])
with sqlite3.connect(f"file:{bk}?mode=ro", uri=True) as chk:
    ok = chk.execute("PRAGMA quick_check").fetchone()[0] == "ok" and chk.execute("select count(*) from users").fetchone()[0] == 3000
check("a verified copy with every row, named by its time", bk.name == "tda_auth-20261030-023000.db" and ok)
check("the result and the state record it", r["kept"] == 1 and DH.status()["last_backup"]["ok"] is True and DH.status()["last_backup"]["path"] == str(bk))
DH.run_backup(db, dest, keep=2, now=datetime.datetime(2026, 10, 31, 2, 30, 0))
r3 = DH.run_backup(db, dest, keep=2, now=datetime.datetime(2026, 11, 1, 2, 30, 0))
names = sorted(p.name for p in dest.glob("tda_auth-*.db"))
check("only the newest `keep` are kept", names == ["tda_auth-20261031-023000.db", "tda_auth-20261101-023000.db"] and r3["pruned"] == ["tda_auth-20261030-023000.db"], names)
try:
    DH.run_backup(db, dest, keep=2, now=datetime.datetime(2026, 11, 1, 2, 30, 0)); dup = False
except FileExistsError:
    dup = True
check("an existing backup is never overwritten", dup)

print("\n── §4 a damaged live file fails the backup — the daily integrity read")
bad = tmp / "bad.db"
bad.write_bytes(db.read_bytes())
raw = bytearray(bad.read_bytes())
page = 4096
for off in range(page * 3, min(len(raw), page * 40), 1):   # trash interior b-tree pages, keep the header
    raw[off] = (raw[off] * 7 + 13) % 256
bad.write_bytes(bytes(raw))
reset()
try:
    DH.run_backup(bad, tmp / "bad_backups", keep=3, now=datetime.datetime(2026, 11, 2, 2, 30, 0)); failed = False
except Exception:
    failed = True
st = DH.status()
check("the copy of a damaged file fails", failed)
check("…the failure is recorded on the last backup and raises the unreadable state", st["last_backup"]["ok"] is False and st["state"] == "unreadable" and len(sent) == 1)
check("…and no half copy is left behind to be mistaken for a backup", not list((tmp / "bad_backups").glob("tda_auth-*.db")))
DH_free = DH.shutil.disk_usage
DH.shutil.disk_usage = lambda p: types.SimpleNamespace(free=10)
try:
    DH.run_backup(db, tmp / "full", keep=3); space = False
except OSError as e:
    space = "needs more" in str(e)
DH.shutil.disk_usage = DH_free
check("not enough free space refuses before writing", space and not list((tmp / "full").glob("tda_auth-*.db")))

print("\n── §5 one copy routine for the upgrade backup and the daily backup")
sl = src("src/trusted_data_agent/core/schema_ledger.py")
check("take_backup copies through vacuum_copy (VACUUM INTO + quick_check)", "    vacuum_copy(path, target)\n" in sl and sl.count('VACUUM INTO ?') == 1)
check("db_health.run_backup uses the same routine", "schema_ledger.vacuum_copy(source, target)" in src("src/trusted_data_agent/core/db_health.py"))
up = SL.take_backup(db, now=datetime.datetime(2026, 11, 3, 1, 0, 0))
check("the upgrade backup still works through it", up.exists() and up.name.endswith("-upgrade"))

print("\n── §6 wiring: the job, the endpoint, the banner")
ts = src("src/trusted_data_agent/core/task_scheduler.py")
check("the daily job is seeded at 02:30 UTC and dispatched", 'DB_BACKUP_JOB_ID = "platform-db-backup"' in ts and "'30 2 * * *'" in ts
      and "_ensure_db_backup_job()" in ts and "elif task_id == DB_BACKUP_JOB_ID:" in ts)
ar = src("src/trusted_data_agent/api/admin_routes.py")
check("admins read the health and can take a backup now", '"/v1/admin/db-health"' in ar and '"/v1/admin/db-health/backup"' in ar)
nj = src("static/js/notifications.js")
check("the browser shows the notice in the app-level banner", "case 'db_health':" in nj and "showAppBanner(dbMsg, 'error', 60000)" in nj)
check("the notice travels the one notice path (broadcast_notice), never a raw queue push",
      "broadcast_notice(user_uuid, \"db_unreadable\"" in src("src/trusted_data_agent/core/db_health.py"))
check("the backups are not committed", "backups/db/" in src(".gitignore"))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
