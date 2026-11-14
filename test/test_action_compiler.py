"""PROP-008 increment B2 — the write compiler + the one-transaction runner (pure, CI).

  §C  compile: one parameterised statement, values only as bind parameters, the WHERE
      built from the grain alone, byte-stable across calls; recovery-shaped refusals
      (unknown attribute with the closest name, a grain an update cannot set, a missing
      required attribute, a value outside its domain naming the domain, a bad type);
      coercion per declared type; a date written into a character column in the
      column's own sampled shape (unknown shape refused); the invocation hash is
      key-order invariant and moves with the values, the statement hash does not
  §K  the checks: the operation's defaults first, then the bound, then the author's;
      count predicates over the keyed rows only; NULL counts as not satisfied
  §B  B1 parity: the representative statement is byte-identical to the literal B1 bytes
      for every operation, and the gate's A5 statement IS the compiler's
  §S  executable on SQLite against hand-computed truth: an update lands one row with its
      ledger row (before-image); a failing postcondition rolls the write AND the ledger
      back; a missing row, an existing insert key and a non-unique grain are refused
      before anything is written; upsert inserts then updates; commit=False rolls back;
      a driver error fails closed and the connection's autocommit is restored

Run: PYTHONPATH=src:. python3 test/test_action_compiler.py
"""
import copy
import datetime as dt
import decimal
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)

from trusted_data_agent.dbdeploy import action_model as AM
from trusted_data_agent.dbdeploy import action_validation as AV
from trusted_data_agent.dbdeploy import action_compiler as AC
from trusted_data_agent.dbdeploy import action_runner as AR

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else: FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))

UPDATE = {
    "action": "set_customer_tier", "version": 1, "backend": "teradata",
    "target": {"entity": "Customers", "deployed": "fitness_db.Customers", "key": ["CustomerID"]},
    "operation": "update",
    "attributes": [{"name": "LoyaltyTier", "type": "string", "required": True,
                    "value_domain": ["bronze", "silver", "gold"]},
                   {"name": "Visits", "type": "integer"},
                   {"name": "JoinedOn", "type": "date"}],
    "conditions": {"pre": [{"id": "not_closed", "expr": "Status <> 'closed'"}]},
    "bound": {"max_rows": 1},
    "authority": {"required_role": "UDERIA_ACT_FITNESS", "posture": "enforce"},
}
M = AM.parse_manifest(UPDATE)

def refusal(fn):
    try:
        fn(); return None
    except AC.ActionInvocationError as e:
        return e

def compile_(m=M, inv=None, **kw):
    return AC.compile_action(m, inv if inv is not None else {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}}, **kw)


def t_compile():
    print("[§C] compile")
    c = compile_()
    check("one parameterised update on the target, the WHERE from the grain alone",
          c.sql == 'UPDATE "fitness_db"."Customers" SET "LoyaltyTier" = ? WHERE "CustomerID" = ?', c.sql)
    check("params bind the SET values then the grain", c.params == ("gold", 7), c.params)
    check("no literal: the statement passes the gate's shape check", AV.statement_shape(c.sql, M.deployed) == [],
          AV.statement_shape(c.sql, M.deployed))
    check("a value never appears in the statement", "gold" not in c.sql and "7" not in c.sql)
    check("byte-stable across calls", compile_().sql == c.sql and compile_().params == c.params
          and compile_().invocation_hash == c.invocation_hash)
    check("only the supplied attributes are written", "Visits" not in c.sql and "JoinedOn" not in c.sql)
    two = compile_(inv={"key": {"CustomerID": 7}, "values": {"Visits": "12", "LoyaltyTier": "gold"}})
    check("columns follow declared order whatever the invocation's order",
          two.sql.index('"LoyaltyTier"') < two.sql.index('"Visits"') and two.params == ("gold", 12, 7), two.params)
    check("statement hash is the A5 digest of the text", c.statement_hash == AC.statement_hash(c.sql))

    a = compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "Visits": 3}})
    b = compile_(inv={"values": {"Visits": 3, "LoyaltyTier": "gold"}, "key": {"CustomerID": 7}})
    check("invocation hash invariant to key order", a.invocation_hash == b.invocation_hash)
    s = compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "silver", "Visits": 3}})
    check("a different value moves the invocation hash, not the statement hash",
          s.invocation_hash != a.invocation_hash and s.statement_hash == a.statement_hash)

    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "LoyalTier": "x"}}))
    check("an unknown attribute is refused naming the writable set and the closest",
          e and e.code == "unknown_attribute" and "LoyaltyTier" in e.message and "closest" in e.message, e and e.message)
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"CustomerID": 8, "LoyaltyTier": "gold"}}))
    check("an update cannot set its grain", e and e.code == "key_not_settable", e and e.code)
    e = refusal(lambda: compile_(inv={"key": {}, "values": {"LoyaltyTier": "gold"}}))
    check("an update without its grain is refused naming it", e and e.code == "key_missing" and "CustomerID" in e.message)
    e = refusal(lambda: compile_(inv={"key": {"Id": 7}, "values": {"LoyaltyTier": "gold"}}))
    check("a key column outside the grain is refused", e and e.code == "unknown_key")
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"Visits": 1}}))
    check("a missing required attribute is named", e and e.code == "missing_required" and "LoyaltyTier" in e.message)
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "platinum"}}))
    check("a value outside the domain is refused naming the domain",
          e and e.code == "outside_domain" and "bronze, silver, gold" in e.message, e and e.message)
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": None}}))
    check("a required attribute cannot be nulled", e and e.code == "null_required")
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "Visits": "a dozen"}}))
    check("a non-integer for an integer is refused, never zeroed", e and e.code == "bad_value")
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "Visits": True}}))
    check("a boolean is not an integer", e and e.code == "bad_value")
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "JoinedOn": "07/10/2026"}}))
    check("a date must be ISO", e and e.code == "bad_value")
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}}))
    check("an invocation with nothing to write refuses (missing the required)", e and e.code in ("missing_required", "nothing_to_write"))
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}, "sql": "x"}))
    check("anything but key/values is refused", e and e.code == "bad_invocation_shape")
    e = refusal(lambda: compile_(dialect="oracle"))
    check("an unknown dialect is refused", e and e.code == "bad_dialect")

    d = compile_(inv={"key": {"CustomerID": "7"}, "values": {"LoyaltyTier": "gold", "JoinedOn": "2026-10-07"}},
                 column_types={"CustomerID": "I", "JoinedOn": "DA"})
    check("the grain is coerced through the dictionary type", d.key == {"CustomerID": 7})
    check("a date binds as a date", d.values["JoinedOn"] == dt.date(2026, 10, 7))
    t = compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "JoinedOn": "2026-10-07"}},
                 column_types={"JoinedOn": "CF"}, text_date_shapes={"JoinedOn": "DD-MMM-YYYY"})
    check("a date into a character column is written in the column's sampled shape",
          t.values["JoinedOn"] == "07-OCT-2026", t.values)
    e = refusal(lambda: compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "JoinedOn": "2026-10-07"}},
                                  column_types={"JoinedOn": "CV"}))
    check("a text-date column with no known shape is refused, never guessed", e and e.code == "unknown_text_date_shape")

    # coercion table
    raw = copy.deepcopy(UPDATE)
    raw["attributes"] += [{"name": "Price", "type": "decimal"}, {"name": "Active", "type": "boolean"},
                          {"name": "SeenAt", "type": "timestamp"}, {"name": "Code", "type": "integer", "value_domain": [0, 1]}]
    mm = AM.parse_manifest(raw)
    cc = AC.compile_action(mm, {"key": {"CustomerID": 1}, "values": {"LoyaltyTier": "gold", "Price": "12.50",
                           "Active": "true", "SeenAt": "2026-10-07 14:30:00", "Code": "1"}})
    check("decimal coerces exactly", cc.values["Price"] == decimal.Decimal("12.50"))
    check("boolean coerces to 1/0", cc.values["Active"] == 1)
    check("timestamp coerces", cc.values["SeenAt"] == dt.datetime(2026, 10, 7, 14, 30))
    check("an integer domain admits its text form", cc.values["Code"] == 1)


INSERT = copy.deepcopy(UPDATE)
INSERT.update({"action": "add_customer", "operation": "insert",
               "attributes": [{"name": "CustomerID", "type": "integer", "required": True},
                              {"name": "LoyaltyTier", "type": "string", "required": True, "value_domain": ["bronze", "silver", "gold"]},
                              {"name": "Status", "type": "string", "default": "open"}],
               "conditions": {}})
UPSERT = copy.deepcopy(INSERT); UPSERT.update({"action": "put_customer", "operation": "upsert"})


def t_checks():
    print("[§K] the checks")
    c = compile_()
    check("update pre: default, bound, then the author's", [k.id for k in c.pre] == ["row_exists", "within_bound", "not_closed"],
          [k.id for k in c.pre])
    check("update post: one row affected, then the rows bound", [k.id for k in c.post] == ["one_row_affected", "rows_within_bound"])
    rx = c.pre[0]
    check("row_exists counts the keyed rows only",
          rx.sql == 'SELECT COUNT(*) FROM "fitness_db"."Customers" WHERE "CustomerID" = ?' and rx.params == (7,) and rx.op == ">=")
    nc = c.pre[2]
    check("an authored predicate counts keyed rows that do NOT satisfy it (NULL counts)",
          "CASE WHEN (Status <> 'closed') THEN 1 ELSE 0 END) = 0" in nc.sql and nc.op == "==" and nc.expect == 0, nc.sql)
    check("the bound check carries max_rows", c.pre[1].op == "<=" and c.pre[1].expect == 1)
    ins = AC.compile_action(AM.parse_manifest(INSERT), {"values": {"CustomerID": 9, "LoyaltyTier": "gold"}})
    check("insert pre: the key must be absent", [(k.id, k.op, k.expect) for k in ins.pre] == [("row_absent", "==", 0)])
    check("insert post: exactly one row with the key", ins.post[0].id == "one_row_present" and ins.post[0].kind == "count")
    check("insert carries a declared default", ins.values["Status"] == "open" and any("default" in n for n in ins.notes))
    e = refusal(lambda: AC.compile_action(AM.parse_manifest(INSERT), {"values": {"CustomerID": 9}}))
    check("insert without a required attribute names it", e and e.code == "missing_required" and "LoyaltyTier" in e.message)
    check("the ledger insert binds everything but the database's clock",
          ins.ledger_sql.count("?") == len(ins.ledger_params(principal="p", posture="enforce", rows_affected=1, before=[]))
          and "CURRENT_TIMESTAMP(6)" in ins.ledger_sql)
    no_led = copy.deepcopy(UPDATE); no_led["ledger"] = {"mode": "target_audit_columns", "columns": ["updated_by"]}
    nl = AC.compile_action(AM.parse_manifest(no_led), {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("target_audit_columns: no platform ledger statement, and the note says why",
          nl.ledger_sql is None and any("audit columns" in n for n in nl.notes))


def t_parity():
    print("[§B] B1 parity")
    B1 = {
        "update": ('UPDATE "fitness_db"."Customers" SET "LoyaltyTier" = ?, "Visits" = ?, "JoinedOn" = ? WHERE "CustomerID" = ?',
                   ("LoyaltyTier", "Visits", "JoinedOn", "CustomerID")),
        "insert": ('INSERT INTO "fitness_db"."Customers" ("CustomerID", "LoyaltyTier", "Status") VALUES (?, ?, ?)',
                   ("CustomerID", "LoyaltyTier", "Status")),
        "upsert": ('MERGE INTO "fitness_db"."Customers" AS tgt USING (SELECT ? AS "CustomerID", ? AS "LoyaltyTier", ? AS "Status") AS src '
                   'ON tgt."CustomerID" = src."CustomerID" WHEN MATCHED THEN UPDATE SET "LoyaltyTier" = src."LoyaltyTier", '
                   '"Status" = src."Status" WHEN NOT MATCHED THEN INSERT ("CustomerID", "LoyaltyTier", "Status") '
                   'VALUES (src."CustomerID", src."LoyaltyTier", src."Status")', ("CustomerID", "LoyaltyTier", "Status")),
    }
    for op, raw in (("update", UPDATE), ("insert", INSERT), ("upsert", UPSERT)):
        got = AC.representative_statement(AM.parse_manifest(raw))
        check(f"{op}: the representative statement is byte-identical to B1", got == B1[op], got)
    check("the gate's A5 statement IS the compiler's", AV.representative_statement is AC.representative_statement)
    full = compile_(inv={"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold", "Visits": 1, "JoinedOn": "2026-01-01"}})
    check("a full invocation compiles to the very statement the gate proved",
          full.sql == AC.representative_statement(M)[0] and full.param_names == AC.representative_statement(M)[1])
    up = AC.compile_action(AM.parse_manifest(UPSERT), {"values": {"CustomerID": 1, "LoyaltyTier": "gold", "Status": "x"}})
    check("upsert compiles one MERGE on the target with no literal", AV.statement_shape(up.sql, M.deployed) == [])


# ── SQLite: executable against hand-computed truth ──────────────────────────

LEDGER_DDL = ('CREATE TABLE fitness_db."UDERIA_ACT_Customers" (performed_at TEXT, principal TEXT, action_name TEXT, '
              'action_version INTEGER, invocation_hash TEXT, statement_hash TEXT, posture TEXT, rows_affected INTEGER, '
              'key_json TEXT, before_json TEXT)')

def db(pk=True):
    conn = sqlite3.connect(":memory:", autocommit=True)
    conn.execute("ATTACH ':memory:' AS fitness_db")
    conn.execute(f'CREATE TABLE fitness_db."Customers" ("CustomerID" INTEGER {"PRIMARY KEY" if pk else ""}, '
                 f'"LoyaltyTier" TEXT, "Visits" INTEGER, "JoinedOn" TEXT, "Status" TEXT)')
    conn.execute(LEDGER_DDL)
    rows = [(7, "bronze", 1, None, "open"), (8, "silver", 2, None, "closed")]
    if not pk:
        rows.append((7, "bronze", 5, None, "open"))
    conn.executemany('INSERT INTO fitness_db."Customers" VALUES (?,?,?,?,?)', rows)
    return conn

def row(conn, cid):
    return conn.execute('SELECT "LoyaltyTier", "Visits", "Status" FROM fitness_db."Customers" WHERE "CustomerID" = ?', (cid,)).fetchall()

def ledger(conn):
    return conn.execute('SELECT action_name, principal, posture, rows_affected, key_json, before_json FROM fitness_db."UDERIA_ACT_Customers"').fetchall()

def run(conn, m, inv, commit=True):
    c = AC.compile_action(m, inv, dialect="sqlite")
    return AR.run_compiled(conn, c, principal="agent:p1", posture="enforce", commit=commit)


def t_sqlite():
    print("[§S] SQLite — executable against hand-computed truth")
    conn = db()
    o = run(conn, M, {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("an update lands one row", o.committed and o.rows_affected == 1 and row(conn, 7) == [("gold", 1, "open")], (o.to_dict(), row(conn, 7)))
    check("every check held and is recorded with what it observed",
          o.held and [c["id"] for c in o.checks] == ["row_exists", "within_bound", "not_closed", "one_row_affected", "rows_within_bound"]
          and all(c["holds"] for c in o.checks))
    lg = ledger(conn)
    check("the ledger row carries principal, posture, count and the before-image",
          len(lg) == 1 and lg[0][:4] == ("set_customer_tier", "agent:p1", "enforce", 1)
          and json.loads(lg[0][4]) == {"CustomerID": 7} and json.loads(lg[0][5])[0]["LoyaltyTier"] == "bronze", lg)
    check("the other row is untouched", row(conn, 8) == [("silver", 2, "closed")])
    check("autocommit restored after the run", conn.autocommit is True)

    # a failing authored precondition: row 8 is closed
    conn = db()
    o = run(conn, M, {"key": {"CustomerID": 8}, "values": {"LoyaltyTier": "gold"}})
    check("a failing precondition writes nothing and names the condition",
          not o.committed and o.refused_check == "not_closed" and row(conn, 8) == [("silver", 2, "closed")] and ledger(conn) == [],
          o.refusal)
    check("…and never reaches the statement", o.rows_affected is None)

    # a failing postcondition: the write ran, then rolled back WITH its ledger row
    raw = copy.deepcopy(UPDATE); raw["conditions"]["post"] = [{"id": "stays_bronze", "expr": "LoyaltyTier = 'bronze'"}]
    conn = db()
    o = run(conn, AM.parse_manifest(raw), {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("a failing postcondition rolls the write back",
          o.rolled_back and not o.committed and o.rows_affected == 1 and o.refused_check == "stays_bronze"
          and row(conn, 7) == [("bronze", 1, "open")], (o.to_dict(), row(conn, 7)))
    check("…and leaves no ledger row", ledger(conn) == [] and not o.ledger_written)

    conn = db()
    o = run(conn, M, {"key": {"CustomerID": 99}, "values": {"LoyaltyTier": "gold"}})
    check("a missing row is refused by row_exists before anything runs", o.refused_check == "row_exists" and o.rows_affected is None)

    conn = db(pk=False)
    o = run(conn, M, {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("a grain the data no longer keeps unique is refused by the bound (A3 measured, not enforced)",
          o.refused_check == "within_bound" and row(conn, 7) == [("bronze", 1, "open"), ("bronze", 5, "open")], o.refusal)

    conn = db()
    mi = AM.parse_manifest(INSERT)
    o = run(conn, mi, {"values": {"CustomerID": 9, "LoyaltyTier": "gold"}})
    check("an insert lands with its default", o.committed and row(conn, 9) == [("gold", None, "open")], row(conn, 9))
    o = run(conn, mi, {"values": {"CustomerID": 7, "LoyaltyTier": "gold"}})
    check("an insert on an existing key is refused by row_absent", o.refused_check == "row_absent" and row(conn, 7) == [("bronze", 1, "open")])

    conn = db()
    mu = AM.parse_manifest(UPSERT)
    o1 = run(conn, mu, {"values": {"CustomerID": 10, "LoyaltyTier": "silver"}})
    o2 = run(conn, mu, {"values": {"CustomerID": 10, "LoyaltyTier": "gold", "Status": "vip"}})
    check("upsert inserts, then updates the same row", o1.committed and o2.committed and row(conn, 10) == [("gold", None, "vip")],
          (o1.to_dict(), o2.to_dict(), row(conn, 10)))

    conn = db()
    o = run(conn, M, {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}}, commit=False)
    check("commit=False executes everything and rolls back", o.held and o.rolled_back and o.rows_affected == 1
          and row(conn, 7) == [("bronze", 1, "open")] and ledger(conn) == [])

    conn = db(); conn.execute('DROP TABLE fitness_db."UDERIA_ACT_Customers"')
    o = run(conn, M, {"key": {"CustomerID": 7}, "values": {"LoyaltyTier": "gold"}})
    check("a driver error (the ledger is gone) fails closed: rolled back, error named, nothing changed",
          o.error and o.rolled_back and not o.committed and row(conn, 7) == [("bronze", 1, "open")], o.to_dict())
    check("…and autocommit is restored", conn.autocommit is True)


for fn in (t_compile, t_checks, t_parity, t_sqlite):
    try: fn()
    except Exception as exc:  # noqa: BLE001
        import traceback; traceback.print_exc(); check(f"{fn.__name__} raised", False, repr(exc))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
