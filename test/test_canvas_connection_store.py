#!/usr/bin/env python3
"""The Canvas reads and writes connections through the ONE Database Connection store.

WHY THIS GATE EXISTS
Canvas connections were written straight to legacy `canvas_conn_<id>` credentials, and
the central Database Connection store only UNIONED them for reading. So a connection
created or edited in the Canvas never became a central record, the Canvas and the
Database Navigator named one connection two ways (`<id>` vs `canvas_conn_<id>`), and
"Open in canvas" from a navigator result needed a translation table. Two defects rode
along, both found while moving the routes:
  * the Canvas edit form is filled with the MASKED password and sends it back on save;
    the legacy route stored the mask as the password, so editing any other field of a
    connection broke its login;
  * deleting a central record that had been migrated from a legacy one left the legacy
    credential behind, so the "deleted" connection reappeared in every list.

Now: the Canvas routes are thin adapters over `DatabaseConnectionStore` (`list`, `get`,
`save`, `delete`, all accepting every id form), a masked secret is never stored, editing
a legacy connection promotes it into the store, and a delete removes it for good.

Run: python3 test/test_canvas_connection_store.py     (no server, no network)
"""
import ast
import logging
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
logging.disable(logging.CRITICAL)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


from trusted_data_agent.core.database_connections import (  # noqa: E402
    DatabaseConnectionStore, DatabaseConnectionError, MASK)


class FakeCM:
    def __init__(self):
        self.recs = {}

    def get_database_connections(self, u):
        return self.recs.setdefault(u, [])

    def add_database_connection(self, rec, u):
        self.recs.setdefault(u, []).append(rec)
        return True

    def update_database_connection(self, cid, upd, u):
        for r in self.recs.get(u, []):
            if r.get("id") == cid:
                r.update(upd)
                return True
        return False

    def remove_database_connection(self, cid, u):
        before = self.recs.get(u, [])
        self.recs[u] = [r for r in before if r.get("id") != cid]
        return len(self.recs[u]) != len(before)


class FakeCrypto:
    def __init__(self):
        self.s = {}

    def encrypt(self, u, p, c):
        self.s.setdefault(u, {})[p] = dict(c)

    def decrypt(self, u, p):
        return dict(self.s.get(u, {}).get(p, {}))

    def delete(self, u, p):
        self.s.get(u, {}).pop(p, None)

    def list_providers(self, u):
        return list(self.s.get(u, {}))


def fresh():
    cm, cr = FakeCM(), FakeCrypto()
    return DatabaseConnectionStore(config_manager=cm, crypto=cr), cm, cr


U = "u1"
LEGACY = {"name": "old pg", "driver": "postgresql", "host": "h", "database": "d", "user": "bob", "password": "secret"}

# ── [1] a masked secret sent back unchanged keeps the stored one
st, cm, cr = fresh()
rec = st.create(U, {"name": "pg", "driver": "postgresql", "username": "al", "password": "pw1"})
st.save(U, rec["id"], {"name": "pg renamed", "password": MASK})
check("[1] saving the masked password keeps the stored password", st.resolve(U, rec["id"])["password"] == "pw1",
      str(st.resolve(U, rec["id"]).get("password")))
check("[1] …and the other field is updated", st.resolve(U, rec["id"])["name"] == "pg renamed")
st.save(U, rec["id"], {"password": "pw2"})
check("[1] a real new password replaces it", st.resolve(U, rec["id"])["password"] == "pw2")
r2 = st.create(U, {"name": "x", "driver": "sqlite", "password": MASK})
check("[1] a mask is never stored on create either", "password" not in st.resolve(U, r2["id"]))

# ── [2] save by every id form
st, cm, cr = fresh()
new = st.save(U, None, {"name": "n", "driver": "sqlite", "database": "/tmp/x.db"})
check("[2] no id → a new central record", new["id"].startswith("dbconn_") and st.get(U, new["id"]) is not None)
try:
    st.save(U, "dbconn_missing", {"name": "z"})
    check("[2] an unknown id is an error, not a silent create", False, "no error")
except DatabaseConnectionError:
    check("[2] an unknown id is an error, not a silent create", True)

for form in ("mycanvas", "canvas_conn_mycanvas"):
    st, cm, cr = fresh()
    cr.encrypt(U, "canvas_conn_mycanvas", LEGACY)
    try:
        out = st.save(U, form, {"name": "old pg (edited)", "password": MASK, "user": "bob"})
    except Exception as exc:  # noqa: BLE001 — a raising save must fail by name, not abort the run
        out = {"id": f"<raised {type(exc).__name__}: {exc}>"}
    check(f"[2] editing legacy id '{form}' promotes it into the store",
          out["id"].startswith("dbconn_") and out.get("migrated_from") == "canvas_conn_mycanvas", str(out))
    full = st.resolve(U, out["id"]) or {}
    check(f"[2] …keeping its password ({form})", full.get("password") == "secret", str(full.get("password")))
    check(f"[2] …its user as the store's username ({form})", full.get("username") == "bob", str(full))
    check(f"[2] …and the edit ({form})", full.get("name") == "old pg (edited)")
    ids = [r["id"] for r in st.list(U)]
    check(f"[2] the legacy entry no longer lists beside its promotion ({form})",
          "canvas_conn_mycanvas" not in ids and ids.count(out["id"]) == 1, str(ids))

# ── [3] delete by every id form, for good
st, cm, cr = fresh()
cr.encrypt(U, "canvas_conn_a", LEGACY)
check("[3] a legacy connection deletes by its bare id", st.delete(U, "a") and st.list(U) == [])
cr.encrypt(U, "canvas_conn_b", LEGACY)
check("[3] …and by its prefixed id", st.delete(U, "canvas_conn_b") and st.list(U) == [])
cr.encrypt(U, "canvas_conn_c", LEGACY)
st.migrate_legacy(U)
cid = next(r["id"] for r in st.list(U) if r["id"].startswith("dbconn_"))
st.delete(U, cid)
check("[3] a deleted migrated connection does not reappear as its legacy original", st.list(U) == [],
      str([r["id"] for r in st.list(U)]))
check("[3] a central id is never read as a legacy one", st.legacy_provider(U, "dbconn_x") is None)
check("[3] deleting what does not exist says so", st.delete(U, "nope") is False)

# ── [4] the Canvas route adapters
from trusted_data_agent.api import rest_routes as rr  # noqa: E402
view = rr._canvas_connection_view({"id": "dbconn_1", "name": "pg", "driver": "postgresql", "host": "h",
                                   "username": "al", "password": MASK, "has_password": True, "source": "central"})
check("[4] the view names the connection by the store's id", view["connection_id"] == "dbconn_1")
check("[4] the form reads `user` (the store says `username`)", view["credentials"].get("user") == "al"
      and "username" not in view["credentials"], str(view["credentials"]))
check("[4] the password stays masked", view["credentials"].get("password") == MASK and view["has_password"] is True)
pl = rr._canvas_payload({"name": "pg", "user": "al", "password": "x"})
check("[4] the form's `user` becomes the store's `username`", pl.get("username") == "al" and "user" not in pl, str(pl))

# ── [5] no Canvas route reads or writes the old credential namespace any more
src = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py")).read()
tree = ast.parse(src)
canvas_fns = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
              and (n.name.startswith("canvas_connection") or n.name == "canvas_execute")]
check("[5] the canvas connection routes and execute exist", len(canvas_fns) >= 5, str([f.name for f in canvas_fns]))
for fn in canvas_fns:
    body = ast.get_source_segment(src, fn) or ""
    check(f"[5] {fn.name} never names `canvas_conn_` itself", "canvas_conn_" not in body)
    check(f"[5] {fn.name} never calls encrypt/delete_credentials directly",
          "encrypt_credentials(" not in body and "delete_credentials(" not in body)

# ── [6] the navigator's "Open in canvas" id is the store's id
from trusted_data_agent.dbcatalog.resolution import canvas_connection_id, source_for_connection  # noqa: E402
check("[6] a central source maps to its store id", canvas_connection_id("conn:dbconn_1") == "dbconn_1")
check("[6] a legacy source maps to the id the Canvas now lists", canvas_connection_id("conn:canvas_conn_x") == "canvas_conn_x")
s = source_for_connection({"id": "dbconn_1", "name": "pg", "driver": "postgresql"})
check("[6] every direct source carries it", s is not None and s.connection_id == "dbconn_1")

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — the Canvas reads and writes the one Database Connection store"))
sys.exit(1 if FAIL else 0)
