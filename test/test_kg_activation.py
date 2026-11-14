"""
Canonical KG activation test — GraphStore.activate_for_profile is the single
source of truth used by both constructors (and mirrors the assignments/activate
endpoint). This locks the behaviour that was previously broken: constructors
inserted a self-assignment WITHOUT deactivating the prior active KG, so a profile
ended up with multiple active assignments → inspect/queries resolved the wrong KG.

CI-pure: temp SQLite DB. Run:
    python test/test_kg_activation.py
"""

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (components/)

from components.builtin.knowledge_graph.graph_store import GraphStore  # noqa: E402

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


def make_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    GraphStore._ensure_kg_metadata_table(conn)  # creates kg_metadata (+ migrations)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_profile_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kg_id TEXT,
            kg_owner_profile_id TEXT NOT NULL,
            assigned_profile_id TEXT NOT NULL,
            user_uuid TEXT NOT NULL,
            is_active BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(kg_id, assigned_profile_id, user_uuid)
        )""")
    now = "2026-06-23T00:00:00Z"
    # Two KGs owned by profile P; kg-old currently active (metadata + assignment).
    for kid, active in (("kg-old", 1), ("kg-new", 0)):
        conn.execute(
            "INSERT INTO kg_metadata (kg_id, profile_id, user_uuid, name, is_active, created_at, updated_at) "
            "VALUES (?, 'P', 'U', ?, ?, ?, ?)",
            (kid, kid, active, now, now))
    conn.execute(
        "INSERT INTO kg_profile_assignments (kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active) "
        "VALUES ('kg-old', 'P', 'P', 'U', 1)")
    conn.commit()
    conn.close()
    return tmp.name


def active_assignment(path, profile="P"):
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT kg_id FROM kg_profile_assignments WHERE assigned_profile_id=? AND user_uuid='U' AND is_active=1",
        (profile,)).fetchall()
    conn.close()
    return [r[0] for r in rows]


def active_metadata(path):
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT kg_id FROM kg_metadata WHERE profile_id='P' AND user_uuid='U' AND is_active=1").fetchall()
    conn.close()
    return [r[0] for r in rows]


def test_activation_makes_sole_active():
    print("\n[1] activate_for_profile makes the new KG the SOLE active one")
    path = make_db()
    try:
        GraphStore.activate_for_profile("kg-new", "P", "U", db_path=path)
        check("only kg-new active in assignments", active_assignment(path) == ["kg-new"])
        check("kg-old deactivated in assignments", "kg-old" not in active_assignment(path))
        check("only kg-new active in metadata", active_metadata(path) == ["kg-new"])
    finally:
        os.unlink(path)


def test_inserts_missing_assignment():
    print("\n[2] inserts an assignment row when none exists")
    path = make_db()
    try:
        # kg-new has metadata but no assignment row yet.
        GraphStore.activate_for_profile("kg-new", "P", "U", db_path=path)
        conn = sqlite3.connect(path)
        n = conn.execute("SELECT COUNT(*) FROM kg_profile_assignments WHERE kg_id='kg-new' AND assigned_profile_id='P'").fetchone()[0]
        conn.close()
        check("assignment row inserted for kg-new", n == 1)
        check("kg-new is the active assignment", active_assignment(path) == ["kg-new"])
    finally:
        os.unlink(path)


def test_idempotent():
    print("\n[3] idempotent — re-activating the same KG keeps it sole-active")
    path = make_db()
    try:
        GraphStore.activate_for_profile("kg-new", "P", "U", db_path=path)
        GraphStore.activate_for_profile("kg-new", "P", "U", db_path=path)
        check("still exactly one active assignment", active_assignment(path) == ["kg-new"])
        check("still exactly one active metadata", active_metadata(path) == ["kg-new"])
    finally:
        os.unlink(path)


def test_non_owner_leaves_owner_metadata():
    print("\n[4] non-owner activation does not touch the owner's metadata flag")
    path = make_db()
    try:
        # Profile Q (not the owner P) activates kg-new for itself.
        GraphStore.activate_for_profile("kg-new", "Q", "U", db_path=path)
        check("Q's assignment is kg-new", active_assignment(path, "Q") == ["kg-new"])
        # Owner P's metadata active flag unchanged (still kg-old, since P didn't activate).
        check("owner P metadata still kg-old", active_metadata(path) == ["kg-old"])
    finally:
        os.unlink(path)


def main():
    test_activation_makes_sole_active()
    test_inserts_missing_assignment()
    test_idempotent()
    test_non_owner_leaves_owner_metadata()
    print(f"\n{'='*50}\nKG activation: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
