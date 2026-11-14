"""Multi-source bench fixture: fitness_db split across two logins (stage 0.1).

Creates, idempotently, on the Teradata the local instance has a Database Connection to:

- two view databases over ``fitness_db`` -- ``ms_fixture_sales`` (Sales, SaleDetails) and
  ``ms_fixture_crm`` (Customers, Products, ServiceTickets). Distinct DATABASES, not a table
  split inside one database, because routing tracks which server holds which database;
- two logins, ``ms_fixture_sales_agent`` and ``ms_fixture_crm_agent``, each granted SELECT on
  its own view database only.

Nothing in ``fitness_db`` is changed. Passwords are generated here (only when a login is
created) and written to ``~/.uderia/ms_fixture_passwords.json`` (0600) -- never printed, never
in the repo.

    python test/performance/fixtures/multi_source/setup_fixture.py            # create / verify
    python test/performance/fixtures/multi_source/setup_fixture.py --verify   # verify only
    python test/performance/fixtures/multi_source/setup_fixture.py --drop     # remove it all

Credentials for the admin connection come from the instance's encrypted store via
``tdrun`` (the same resolver the policing scripts use).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "agent_packs", "policing-data-products", "scripts"))

SOURCE_DB = "fitness_db"
SPLIT = {
    "ms_fixture_sales": ["Sales", "SaleDetails"],
    "ms_fixture_crm": ["Customers", "Products", "ServiceTickets"],
}
PASSWORD_FILE = os.path.expanduser("~/.uderia/ms_fixture_passwords.json")


def login_of(view_db: str) -> str:
    return f"{view_db}_agent"


def _exists(cur, name: str, kind: str = "db") -> bool:
    if kind == "user":
        cur.execute("SELECT COUNT(*) FROM DBC.UsersV WHERE UserName = ?", [name])
    else:
        cur.execute("SELECT COUNT(*) FROM DBC.DatabasesV WHERE DatabaseName = ?", [name])
    return cur.fetchone()[0] > 0


def _password() -> str:
    alphabet = string.ascii_letters + string.digits
    # Teradata: must start with a letter.
    return "M" + "".join(secrets.choice(alphabet) for _ in range(23))


def _load_passwords() -> dict:
    try:
        with open(PASSWORD_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_passwords(pw: dict) -> None:
    os.makedirs(os.path.dirname(PASSWORD_FILE), exist_ok=True)
    fd = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(pw, f, indent=2)


def create(cur) -> None:
    pw = _load_passwords()
    for view_db, tables in SPLIT.items():
        if not _exists(cur, view_db):
            cur.execute(f"CREATE DATABASE {view_db} FROM DBC AS PERM = 0")
            print(f"created database {view_db}")
        # the view database must hold SELECT WITH GRANT OPTION to expose the base tables
        cur.execute(f"GRANT SELECT ON {SOURCE_DB} TO {view_db} WITH GRANT OPTION")
        for t in tables:
            cur.execute(
                f"REPLACE VIEW {view_db}.{t} AS LOCKING ROW FOR ACCESS "
                f"SELECT * FROM {SOURCE_DB}.{t}")
        print(f"views in {view_db}: {', '.join(tables)}")

        user = login_of(view_db)
        if not _exists(cur, user, "user"):
            p = _password()
            cur.execute(
                f"CREATE USER {user} FROM DBC AS PERM = 0, SPOOL = 2e9, "
                f"PASSWORD = \"{p}\"")
            pw[user] = p
            _save_passwords(pw)
            print(f"created login {user} (password in {PASSWORD_FILE})")
        elif user not in pw:
            print(f"WARNING: login {user} exists but its password is not in {PASSWORD_FILE}")
        cur.execute(f"GRANT SELECT ON {view_db} TO {user}")


def drop(cur) -> None:
    pw = _load_passwords()
    for view_db, tables in SPLIT.items():
        user = login_of(view_db)
        if _exists(cur, user, "user"):
            cur.execute(f"DROP USER {user}")
            print(f"dropped login {user}")
        pw.pop(user, None)
        if _exists(cur, view_db):
            cur.execute(f"DELETE DATABASE {view_db}")
            cur.execute(f"DROP DATABASE {view_db}")
            print(f"dropped database {view_db}")
    _save_passwords(pw)


def verify() -> bool:
    """Log in AS each fixture login: its own views read, the other side and fitness_db refuse."""
    import teradatasql
    import tdrun
    host = tdrun.creds()["host"]
    pw = _load_passwords()
    ok = True
    for view_db, tables in SPLIT.items():
        user = login_of(view_db)
        if user not in pw:
            print(f"FAIL {user}: no password on file")
            ok = False
            continue
        other = next(d for d in SPLIT if d != view_db)
        with teradatasql.connect(host=host, user=user, password=pw[user], logmech="TD2") as con:
            cur = con.cursor()
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM {view_db}.{t}")
                print(f"ok   {user}: {view_db}.{t} = {cur.fetchone()[0]} rows")
            for target in (f"{other}.{SPLIT[other][0]}", f"{SOURCE_DB}.{tables[0]}"):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {target}")
                    print(f"FAIL {user}: could read {target}")
                    ok = False
                except Exception as e:  # expected: 3523 no access
                    code = "3523" if "3523" in str(e) else str(e)[:80]
                    print(f"ok   {user}: {target} refused ({code})")
            cur.execute("SELECT DataBaseName FROM dbc.DatabasesVX")
            visible = sorted(r[0].strip() for r in cur.fetchall())
            print(f"     {user} sees databases: {visible}")
            if other in visible or SOURCE_DB in visible:
                print(f"FAIL {user}: database list shows the other side")
                ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true", help="verify only")
    ap.add_argument("--drop", action="store_true", help="remove the fixture")
    a = ap.parse_args()
    import tdrun
    if not a.verify:
        with tdrun.connect() as con:
            cur = con.cursor()
            (drop if a.drop else create)(cur)
    if a.drop:
        return 0
    return 0 if verify() else 1


if __name__ == "__main__":
    sys.exit(main())
