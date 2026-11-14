"""
M4.5 module tests — centralized DatabaseConnectionStore.

CI-pure: in-memory fake config_manager + fake credential layer (no DB, no Fernet).
Covers create/list/get/resolve/update/delete, masking, secret isolation,
driver validation, and legacy Canvas-connection unification + migration.

Run:
    python test/test_database_connections.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.database_connections import (  # noqa: E402
    DatabaseConnectionStore,
    DatabaseConnectionError,
    MASK,
)

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


class FakeCM:
    def __init__(self):
        self.store = {}  # user -> list

    def get_database_connections(self, user):
        return self.store.setdefault(user, [])

    def add_database_connection(self, rec, user):
        self.store.setdefault(user, []).append(rec)
        return True

    def update_database_connection(self, cid, updates, user):
        for r in self.store.get(user, []):
            if r.get("id") == cid:
                r.update(updates)
                return True
        return False

    def remove_database_connection(self, cid, user):
        before = self.store.get(user, [])
        after = [r for r in before if r.get("id") != cid]
        self.store[user] = after
        return len(after) != len(before)


class FakeCrypto:
    def __init__(self):
        self.store = {}  # user -> {provider: creds}

    def encrypt(self, user, provider, creds):
        self.store.setdefault(user, {})[provider] = dict(creds)

    def decrypt(self, user, provider):
        return dict(self.store.get(user, {}).get(provider, {}))

    def delete(self, user, provider):
        self.store.get(user, {}).pop(provider, None)

    def list_providers(self, user):
        return list(self.store.get(user, {}).keys())


def fresh():
    cm, cr = FakeCM(), FakeCrypto()
    return DatabaseConnectionStore(config_manager=cm, crypto=cr), cm, cr


U = "user-1"


# ── tests ─────────────────────────────────────────────────────────────────────

def test_create_and_secret_isolation():
    print("\n[test_create_and_secret_isolation]")
    store, cm, cr = fresh()
    rec = store.create(U, {"name": "Prod TD", "driver": "teradata", "host": "td.acme",
                           "database": "DWH", "username": "etl", "password": "s3cret"})
    check("id assigned (dbconn_)", rec["id"].startswith("dbconn_"))
    check("password masked in returned record", rec["password"] == MASK)
    check("has_password flag", rec["has_password"] is True)
    # secret NOT in metadata
    meta = cm.get_database_connections(U)[0]
    check("secret absent from metadata record", "password" not in meta)
    check("secret stored encrypted-side", cr.decrypt(U, rec["id"])["password"] == "s3cret")
    # resolve exposes full creds
    full = store.resolve(U, rec["id"])
    check("resolve returns real password", full["password"] == "s3cret")
    check("resolve carries metadata", full["host"] == "td.acme" and full["username"] == "etl")


def test_list_masks():
    print("\n[test_list_masks]")
    store, _, _ = fresh()
    store.create(U, {"name": "c", "driver": "postgresql", "host": "h", "password": "p"})
    rows = store.list(U)
    check("listed", len(rows) == 1)
    check("masked in list", rows[0]["password"] == MASK)
    check("source central", rows[0]["source"] == "central")


def test_validation():
    print("\n[test_validation]")
    store, _, _ = fresh()
    try:
        store.create(U, {"name": "x", "driver": "oracle"})
        check("unsupported driver rejected", False)
    except DatabaseConnectionError:
        check("unsupported driver rejected", True)
    try:
        store.create(U, {"name": "", "driver": "teradata"})
        check("missing name rejected", False)
    except DatabaseConnectionError:
        check("missing name rejected", True)


def test_update():
    print("\n[test_update]")
    store, _, cr = fresh()
    rec = store.create(U, {"name": "c", "driver": "teradata", "host": "h1", "password": "p1"})
    upd = store.update(U, rec["id"], {"host": "h2", "password": "p2"})
    check("metadata updated", upd["host"] == "h2")
    check("secret updated", cr.decrypt(U, rec["id"])["password"] == "p2")
    check("update unknown -> None", store.update(U, "nope", {"host": "x"}) is None)


def test_delete():
    print("\n[test_delete]")
    store, cm, cr = fresh()
    rec = store.create(U, {"name": "c", "driver": "teradata", "password": "p"})
    check("delete ok", store.delete(U, rec["id"]) is True)
    check("metadata gone", cm.get_database_connections(U) == [])
    check("secret gone", cr.decrypt(U, rec["id"]) == {})
    check("delete missing -> False", store.delete(U, "nope") is False)


def test_legacy_unification_and_migration():
    print("\n[test_legacy_unification_and_migration]")
    store, cm, cr = fresh()
    # seed a legacy canvas connection
    cr.encrypt(U, "canvas_conn_abc", {"name": "Legacy TD", "driver": "teradata",
                                      "host": "old.acme", "user": "u", "password": "legacy"})
    rows = store.list(U)
    legacy = next((r for r in rows if r["source"] == "canvas_legacy"), None)
    check("legacy surfaced in unified list", legacy is not None)
    check("legacy id is provider", legacy["id"] == "canvas_conn_abc")
    check("legacy username mapped from 'user'", legacy["username"] == "u")
    check("legacy migratable", legacy.get("migratable") is True)
    # resolve legacy directly
    full = store.resolve(U, "canvas_conn_abc")
    check("resolve legacy full creds", full["password"] == "legacy" and full["user"] == "u")
    # migrate
    n = store.migrate_legacy(U)
    check("migrated 1", n == 1)
    rows2 = store.list(U)
    check("legacy no longer duplicated after migrate",
          sum(1 for r in rows2 if r["source"] == "canvas_legacy") == 0)
    central = next(r for r in rows2 if r["source"] == "central")
    check("migrated record resolvable", store.resolve(U, central["id"])["password"] == "legacy")
    check("idempotent migrate (no dup)", store.migrate_legacy(U) == 0)


def test_resolve_bare_canvas_id():
    print("\n[test_resolve_bare_canvas_id]")
    # Canvas holds bare ids (stored as canvas_conn_{id}); the store must resolve them.
    store, _, cr = fresh()
    cr.encrypt(U, "canvas_conn_mycanvas", {"driver": "teradata", "host": "h", "user": "u", "password": "pw"})
    full = store.resolve(U, "mycanvas")  # bare id, no prefix
    check("bare canvas id resolves via fallback", full is not None and full["password"] == "pw")
    full2 = store.resolve(U, "canvas_conn_mycanvas")  # prefixed form
    check("prefixed canvas id resolves", full2 is not None and full2["password"] == "pw")


def test_resolve_unknown():
    print("\n[test_resolve_unknown]")
    store, _, _ = fresh()
    check("resolve unknown -> None", store.resolve(U, "dbconn_missing") is None)
    check("resolve unknown bare -> None", store.resolve(U, "nope") is None)


def main():
    for fn in [
        test_create_and_secret_isolation,
        test_list_masks,
        test_validation,
        test_update,
        test_delete,
        test_legacy_unification_and_migration,
        test_resolve_bare_canvas_id,
        test_resolve_unknown,
    ]:
        fn()
    print(f"\n{'='*50}\nM4.5 DB connections: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
