#!/usr/bin/env python3
"""The schema ledger: a backup before an upgrade, refusal when it cannot be taken, a ledger of
every start, the fingerprint's blind spot measured, and drift reported.

Drives the real module against temporary SQLite files; CI-pure (no server, no network).
"""
import ast
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "src")

passed = 0
failures = []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  ok  {name}")
    else:
        failures.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising section is a named failure, not a crash
        failures.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
        print(f"  FAIL  {fn.__name__} raised {type(exc).__name__}: {exc}")


from trusted_data_agent.core import schema_ledger as L  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="ledger_"))


def fresh_db(name, tables=("CREATE TABLE users (id INTEGER, name TEXT)",)):
    p = TMP / name
    if p.exists():
        p.unlink()
    for b in p.parent.glob(p.name + ".bak-*"):
        b.unlink()
    con = sqlite3.connect(p)
    for t in tables:
        con.execute(t)
    if tables:
        con.execute("INSERT INTO users VALUES (1, 'ana')")
    con.commit()
    con.close()
    return p


def ledger_rows(p):
    con = sqlite3.connect(p)
    try:
        return con.execute("SELECT upgrade, backup_path, changed_without_backup, fingerprint FROM schema_ledger ORDER BY id").fetchall()
    finally:
        con.close()


def t_new_and_existing():
    print("\n[1] new database, existing database, unchanged restart")
    p = TMP / "new.db"
    plan = L.prepare(p, mode="required", fp="A")
    check("a database that does not exist yet → no upgrade, no backup", not plan["upgrade"] and plan["backup"] is None
          and plan["note"] == "new database", plan)
    fresh_db("new.db")
    L.record(p, plan)
    check("…the start is recorded", len(ledger_rows(p)) == 1)

    p = fresh_db("existing.db")
    plan = L.prepare(p, mode="required", fp="A")
    check("an existing database with no ledger → an upgrade, backed up", plan["upgrade"] and plan["backup"]
          and Path(plan["backup"]).exists(), plan)
    con = sqlite3.connect(plan["backup"])
    check("the backup holds the data", con.execute("SELECT name FROM users").fetchall() == [("ana",)])
    con.close()
    check("the backup is named as an upgrade backup next to the database",
          Path(plan["backup"]).name.startswith("existing.db.bak-") and plan["backup"].endswith(L.BACKUP_SUFFIX))
    L.record(p, plan)
    plan2 = L.prepare(p, mode="required", fp="A")
    check("restart with the same fingerprint → no upgrade, no backup", not plan2["upgrade"] and plan2["backup"] is None, plan2)
    L.record(p, plan2)
    plan3 = L.prepare(p, mode="required", fp="B")
    check("a changed fingerprint → upgrade and backup", plan3["upgrade"] and plan3["backup"], plan3)
    check("…the previous fingerprint is carried", plan3["previous"] == "A")
    L.record(p, plan3)
    rows = ledger_rows(p)
    check("the ledger lists the three starts in order", [r[0] for r in rows] == [1, 0, 1] and [r[3] for r in rows] == ["A", "A", "B"], rows)

    p = fresh_db("empty.db", tables=())
    plan = L.prepare(p, mode="required", fp="A")
    check("an empty database file is not an upgrade (nothing to lose)", not plan["upgrade"] and plan["backup"] is None, plan)


def t_retention():
    print("\n[2] retention keeps the newest upgrade backups only")
    p = fresh_db("ret.db")
    manual = p.parent / (p.name + ".bak-20260101-000000")          # a maintenance backup
    manual.write_bytes(b"x")
    base = datetime.datetime(2026, 10, 2, 10, 0, 0)
    for i in range(5):
        L.take_backup(p, now=base + datetime.timedelta(minutes=i))
    kept = L.backups(p)
    check(f"{L.BACKUP_KEEP} upgrade backups kept, the newest", len(kept) == L.BACKUP_KEEP
          and kept[0].name.endswith("100400-000000" + L.BACKUP_SUFFIX), [k.name for k in kept])
    check("a maintenance backup is never touched", manual.exists())


def t_refusal():
    print("\n[3] a backup that cannot be taken refuses the upgrade (required), or is reported")
    p = fresh_db("refuse.db")
    real = shutil.disk_usage
    L.shutil.disk_usage = lambda _p: type("u", (), {"free": 10})()
    try:
        def state():
            con = sqlite3.connect(p)
            try:
                return (con.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(),
                        con.execute("SELECT * FROM users").fetchall())
            finally:
                con.close()
        before = state()
        try:
            L.prepare(p, mode="required", fp="Z")
            check("required → SchemaBackupRefused", False)
        except L.SchemaBackupRefused as exc:
            check("required → SchemaBackupRefused", True)
            check("…the message names the remedy and the switch",
                  "TDA_SCHEMA_BACKUP=best_effort" in str(exc) and "no backup could be taken" in str(exc), str(exc))
        check("…the database is untouched (schema and rows)", state() == before)
        check("…and no partial backup is left", L.backups(p) == [])
        plan = L.prepare(p, mode="best_effort", fp="Z")
        check("best_effort → proceeds, the failure named", plan["upgrade"] and plan["backup"] is None
              and plan["note"].startswith("backup failed"), plan)
    finally:
        L.shutil.disk_usage = real
    plan = L.prepare(p, mode="off", fp="Z")
    check("off → no backup, said so", plan["backup"] is None and "TDA_SCHEMA_BACKUP=off" in plan["note"], plan)
    os.environ["TDA_SCHEMA_BACKUP"] = "nonsense"
    check("an unknown mode reads as required (fail safe)", L.backup_mode() == "required")
    os.environ.pop("TDA_SCHEMA_BACKUP")
    check("the default is required", L.backup_mode() == "required")


def t_blind_spot():
    print("\n[4] a schema change the fingerprint did not predict is recorded")
    p = fresh_db("blind.db")
    L.record(p, L.prepare(p, mode="off", fp="F"))
    plan = L.prepare(p, mode="required", fp="F")
    check("same fingerprint → no upgrade", not plan["upgrade"])
    con = sqlite3.connect(p)
    con.execute("ALTER TABLE users ADD COLUMN email TEXT")
    con.commit(); con.close()
    row = L.record(p, plan)
    check("the start changed the schema without a backup → recorded", row and row["changed_without_backup"] == 1, row)
    plan = L.prepare(p, mode="required", fp="F")
    row = L.record(p, plan)
    check("…and the next quiet start is not", row and row["changed_without_backup"] == 0, row)


def t_drift():
    print("\n[5] drift: what the schema files declare and the database lacks")
    d = TMP / "schema"
    (d / "migrations").mkdir(parents=True, exist_ok=True)
    (d / "01_a.sql").write_text("CREATE TABLE users (id INTEGER, name TEXT, email TEXT);\n"
                                "CREATE TABLE audit (id INTEGER);\n")
    (d / "02_b.sql").write_text("-- adds to a table only the ORM creates\nALTER TABLE orm_only ADD COLUMN x TEXT;\n")
    p = fresh_db("drift.db")
    out = L.drift(p, d)
    check("a declared table the database lacks", out["missing_tables"] == ["audit"], out)
    check("a declared column the database lacks", out["missing_columns"] == {"users": ["email"]}, out)
    check("a file that could not be evaluated alone is named, not guessed", out["unchecked_files"] == ["02_b.sql"], out)
    check("drift never raises (a broken path)", "error" in L.drift(TMP / "nope" / "x.db", d) or True)
    rec = L.record(p, L.prepare(p, mode="off", fp="D"))
    import json as _json
    check("each ledger row carries the drift of the database it describes",
          rec and _json.loads(rec["drift_json"]) == L.drift(p), rec and rec["drift_json"][:80])


def t_fingerprint():
    print("\n[6] the fingerprint follows DDL, not unrelated edits")
    src = TMP / "src"
    src.mkdir(exist_ok=True)
    f = src / "m.py"
    f.write_text('def x():\n    cur.execute("ALTER TABLE t ADD COLUMN a TEXT")\n    return 1\n')
    a = L.ddl_lines(src)
    f.write_text('def x():\n    cur.execute("ALTER TABLE t ADD COLUMN a TEXT")\n    return 2  # unrelated\n')
    check("an unrelated edit leaves the DDL lines unchanged", L.ddl_lines(src) == a, a)
    f.write_text('def x():\n    cur.execute("ALTER TABLE t ADD COLUMN a INTEGER")\n    return 2\n')
    check("an edited DDL statement changes them", L.ddl_lines(src) != a)
    check("whitespace does not", L.ddl_lines(src) == ['m.py: cur.execute("ALTER TABLE t ADD COLUMN a INTEGER")'])
    fp1 = L.fingerprint({"a.sql": "1"}, ["x"], ["o"], "v1.24")
    check("deterministic", fp1 == L.fingerprint({"a.sql": "1"}, ["x"], ["o"], "v1.24"))
    check("a schema file's content moves it", fp1 != L.fingerprint({"a.sql": "2"}, ["x"], ["o"], "v1.24"))
    check("the ORM DDL moves it", fp1 != L.fingerprint({"a.sql": "1"}, ["x"], ["o2"], "v1.24"))
    check("the release version moves it", fp1 != L.fingerprint({"a.sql": "1"}, ["x"], ["o"], "v1.25"))
    import trusted_data_agent.core.version as V
    real = V.get_app_version
    try:
        V.get_app_version = lambda: "v1.24-17-gabc1234"
        check("a commit after the tag is not a new release", L.release_version() == "v1.24")
    finally:
        V.get_app_version = real
    check("the real sources yield DDL lines and ORM tables", len(L.ddl_lines()) > 50 and len(L.orm_ddl()) > 5,
          (len(L.ddl_lines()), len(L.orm_ddl())))


def t_guards():
    print("\n[7] guards")
    check("an in-memory database is a no-op", L.prepare("sqlite:///:memory:", fp="A")["note"] == "not a file database"
          and L.record(":memory:", {"fingerprint": "A"}) is None)
    bad = TMP / "corrupt.db"
    bad.write_bytes(b"not a database at all" * 100)
    plan = L.prepare(bad, mode="required", fp="A")
    check("an unreadable database never raises from prepare (the bootstrap decides)", plan["note"].startswith("prepare failed"), plan)


def t_wiring():
    print("\n[8] the bootstrap is wrapped, the refusal is not swallowed")
    src = Path("src/trusted_data_agent/auth/database.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "init_database")
    body = fn.body
    i_prepare = next((i for i, n in enumerate(body) if "schema_ledger.prepare" in ast.unparse(n)), None)
    i_try = next((i for i, n in enumerate(body) if isinstance(n, ast.Try)), None)
    check("prepare runs before the bootstrap's try (a refusal propagates)", i_prepare is not None and i_try is not None
          and i_prepare < i_try, (i_prepare, i_try))
    try_src = ast.unparse(body[i_try])
    check("record runs at the end of a successful bootstrap", "schema_ledger.record(DATABASE_URL, ledger_plan)" in try_src
          and try_src.index("schema_ledger.record") < try_src.rindex("return True"))
    main = Path("src/trusted_data_agent/main.py").read_text()
    check("startup re-raises a failed init_database exception (fatal)", "init_database()" in main and "raise  # Fatal" in main)
    admin = Path("src/trusted_data_agent/api/admin_routes.py").read_text()
    check("the ledger is readable by an admin", '"/v1/admin/schema/ledger"' in admin
          and admin.index('"/v1/admin/schema/ledger"') < admin.index("@require_admin\nasync def schema_ledger_report"))
    compose = Path("docker-compose.yml").read_text()
    check("compose forwards the switch", "TDA_SCHEMA_BACKUP=${TDA_SCHEMA_BACKUP:-required}" in compose)
    check("upgrade backups are gitignored", "tda_auth.db.bak-*" in Path(".gitignore").read_text())


for fn in (t_new_and_existing, t_retention, t_refusal, t_blind_spot, t_drift, t_fingerprint, t_guards, t_wiring):
    run(fn)

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 68)
print(f"RESULT: {'FAILURES' if failures else 'ALL PASS'}  ({passed} passed, {len(failures)} failed)")
print("=" * 68)
sys.exit(1 if failures else 0)
