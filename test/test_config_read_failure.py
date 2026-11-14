"""
A configuration that could not be READ is never answered with the template — and a pooled
connection that cannot read its file is discarded.

Found Oct 2026 on production: for nine minutes every ORM read of tda_auth.db failed with
`sqlite3.DatabaseError: file is not a database` until a restart. load_config fell through the
failed read to the NEW-USER bootstrap: it cached the template as the user's configuration and
tried to save it over theirs — only the failing writes prevented the loss, and the cached
template would have been persisted by the next ordinary save had the database recovered first.

  §A  three outcomes kept apart (EXECUTED against a stubbed session):
        read failed            → ConfigUnavailableError; nothing cached, nothing saved
        stored but unreadable  → ConfigUnavailableError; the damaged record is left as it is
        read ok, no record     → the new-user bootstrap (as before)
        read ok, a migration fails → the configuration READ is returned (never the template)
  §B  the engine treats an unreadable-file error as a disconnect: the connection and the pool
      are invalidated, so the next request opens a fresh connection (EXECUTED on a real file);
      an ordinary SQL error is not touched

Run: python test/test_config_read_failure.py
"""
import contextlib
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [os.path.join(ROOT, "src"), ROOT]
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail!r}"[:300] if detail != "" else ""))


from trusted_data_agent.core import config_manager as CM  # noqa: E402
import trusted_data_agent.auth.database as DB  # noqa: E402

print("[§A] a failed read is not 'no configuration'")


class _Q:
    def __init__(self, row): self.row = row
    def filter_by(self, **k): return self
    def first(self): return self.row


class _Session:
    def __init__(self, row=None, fail=None): self.row, self.fail = row, fail
    def query(self, _m):
        if self.fail:
            raise self.fail
        return _Q(self.row)


class _Pref:
    def __init__(self, js): self.preferences_json = js


def _with_session(sess):
    @contextlib.contextmanager
    def _ctx():
        yield sess
    return _ctx


def run(sess, mgr=None):
    saved = []
    mgr = mgr or CM.ConfigManager.__new__(CM.ConfigManager)
    mgr._user_configs = {}
    mgr.save_config = lambda cfg, uid=None: saved.append(uid) or True
    mgr.ensure_default_vector_store_config = lambda uid: None
    mgr.migrate_inline_vector_store_configs = lambda uid: None
    mgr.ensure_default_kg_backend_config = lambda uid: None
    mgr._load_bootstrap_template = lambda: {"profiles": [{"tag": "BOOT"}], "llm_configurations": []}
    mgr._sync_new_default_profiles = lambda c: c
    mgr._migrate_mcp_server_transport = lambda c: False
    orig = DB.get_db_session
    DB.get_db_session = _with_session(sess)
    try:
        try:
            out, err = mgr.load_config("u1"), None
        except CM.ConfigUnavailableError as e:
            out, err = None, e
    finally:
        DB.get_db_session = orig
    return out, err, saved, mgr._user_configs


out, err, saved, cache = run(_Session(fail=sqlite3.DatabaseError("file is not a database")))
check("the database read FAILED → ConfigUnavailableError, not the template",
      err is not None and out is None, (out, err))
check("…nothing cached as the user's configuration, nothing saved over theirs", not cache and not saved, (cache, saved))

out, err, saved, cache = run(_Session(row=_Pref("{not json")))
check("a stored configuration that does not parse → ConfigUnavailableError; the damaged record is not replaced",
      err is not None and not saved and not cache, (err, saved))

out, err, saved, cache = run(_Session(row=None))
check("the read worked and there is NO record → the new-user bootstrap (as before), saved",
      err is None and out and out["profiles"][0]["tag"] == "BOOT" and saved == ["u1"], (out, saved))

mine = {"profiles": [{"tag": "PXPROD"}], "component_profile_defaults": {}}
mgr = CM.ConfigManager.__new__(CM.ConfigManager)
out, err, saved, cache = run(_Session(row=_Pref(json.dumps(mine))), mgr)
check("the read worked → the user's own configuration", err is None and out["profiles"][0]["tag"] == "PXPROD" and not saved, (out, saved))


def _boom(uid):
    raise sqlite3.DatabaseError("database is locked")


mgr2 = CM.ConfigManager.__new__(CM.ConfigManager)
mgr2._user_configs = {}
saved2 = []
mgr2.save_config = lambda cfg, uid=None: saved2.append(uid) or True
mgr2.ensure_default_vector_store_config = _boom
mgr2.migrate_inline_vector_store_configs = lambda uid: None
mgr2.ensure_default_kg_backend_config = lambda uid: None
mgr2._load_bootstrap_template = lambda: {"profiles": [{"tag": "BOOT"}]}
mgr2._sync_new_default_profiles = lambda c: c
mgr2._migrate_mcp_server_transport = lambda c: False
_orig = DB.get_db_session
DB.get_db_session = _with_session(_Session(row=_Pref(json.dumps(mine))))
try:
    out2 = mgr2.load_config("u1")
finally:
    DB.get_db_session = _orig
check("a MIGRATION failing after a good read still returns the configuration read — never the template",
      out2["profiles"][0]["tag"] == "PXPROD" and not saved2, (out2, saved2))
src = open(os.path.join(ROOT, "src/trusted_data_agent/core/config_manager.py"), encoding="utf-8").read()
lc = src[src.index("    def load_config("):src.index("    def _load_bootstrap_template(")]
check("the old fall-through is gone: no exception path reaches the bootstrap",
      'raise ConfigUnavailableError(f"the configuration of user' in lc
      and lc.index("raise ConfigUnavailableError") < lc.index("No configuration found for user"))

print("[§B] a pooled connection that cannot read its file is discarded")
from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402

d = tempfile.mkdtemp(); path = os.path.join(d, "t.db")
c = sqlite3.connect(path); c.execute("CREATE TABLE s(k TEXT)"); c.execute("INSERT INTO s VALUES('a')"); c.commit(); c.close()
good = open(path, "rb").read()
made = []


def creator():
    conn = sqlite3.connect(path, check_same_thread=False)
    made.append(id(conn))
    return conn


eng = create_engine("sqlite://", creator=creator, poolclass=QueuePool, pool_size=2, pool_pre_ping=True)
event.listen(eng, "handle_error", DB.invalidate_on_unreadable_file)


def q(sql="SELECT COUNT(*) FROM s"):
    with eng.connect() as cx:
        return cx.execute(text(sql)).scalar()


q(); n0 = len(made)
check("a healthy pool reuses its connection", q() == 1 and len(made) == n0)
with open(path, "r+b") as f:
    f.write(b"X" * 100)
try:
    q(); raised = None
except Exception as e:  # noqa: BLE001
    raised = str(e)
check("an unreadable file still fails the statement that met it", raised and "file is not a database" in raised, raised)
with open(path, "r+b") as f:
    f.write(good[:100])
n1 = len(made)
check("…and the NEXT request runs on a NEW connection (the pool was invalidated), and succeeds",
      q() == 1 and len(made) > n1, (n1, len(made)))
n2 = len(made)
try:
    q("SELECT nope FROM s")
except Exception:  # noqa: BLE001
    pass
q()
check("an ordinary SQL error does not discard the pool", len(made) == n2, (n2, len(made)))
check("the engine registers the hook", "event.listen(engine, \"handle_error\", invalidate_on_unreadable_file)"
      in open(os.path.join(ROOT, "src/trusted_data_agent/auth/database.py"), encoding="utf-8").read())

print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
