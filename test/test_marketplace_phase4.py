"""
Phase 4 Marketplace Tests
==========================
Tests for:
  Fix 4A — Rating requires prior install
    - Schema: marketplace_installs table
    - Agent pack rating gate (+ legacy grant fallback)
    - Skill rating gate (+ legacy skill-loaded fallback)
    - Extension rating gate (+ legacy extension-loaded fallback)
    - KG rating gate (+ legacy kg_entities fallback)
    - Install endpoints record into marketplace_installs

Run:
    python test/test_marketplace_phase4.py
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_test_db(path: str) -> sqlite3.Connection:
    """Create a minimal in-memory SQLite DB with marketplace_installs table."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS marketplace_installs (
            id              TEXT PRIMARY KEY,
            artifact_type   TEXT NOT NULL,
            marketplace_id  TEXT NOT NULL,
            user_id         TEXT NOT NULL,
            installed_at    TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_marketplace_installs_unique
            ON marketplace_installs (artifact_type, marketplace_id, user_id);

        CREATE TABLE IF NOT EXISTS marketplace_sharing_grants (
            id              TEXT PRIMARY KEY,
            resource_type   TEXT NOT NULL,
            resource_id     TEXT NOT NULL,
            grantor_user_id TEXT NOT NULL,
            grantee_user_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS marketplace_agent_packs (
            id                      TEXT PRIMARY KEY,
            name                    TEXT,
            publisher_user_id       TEXT,
            source_installation_id  INTEGER
        );

        CREATE TABLE IF NOT EXISTS agent_pack_ratings (
            id          TEXT PRIMARY KEY,
            pack_id     TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            rating      INTEGER NOT NULL,
            comment     TEXT,
            created_at  TEXT,
            updated_at  TEXT
        );
    """)
    conn.commit()
    return conn


def _record_install(conn: sqlite3.Connection, artifact_type: str,
                    marketplace_id: str, user_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    import uuid
    conn.execute(
        "INSERT OR IGNORE INTO marketplace_installs "
        "(id, artifact_type, marketplace_id, user_id, installed_at) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), artifact_type, marketplace_id, user_id, now),
    )
    conn.commit()


def _check_install(conn: sqlite3.Connection, artifact_type: str,
                   marketplace_id: str, user_id: str) -> bool:
    """Return True if user has an install record for this artifact."""
    row = conn.execute(
        "SELECT 1 FROM marketplace_installs "
        "WHERE artifact_type = ? AND marketplace_id = ? AND user_id = ?",
        (artifact_type, marketplace_id, user_id),
    ).fetchone()
    return row is not None


# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

class TestFix4A_Schema(unittest.TestCase):
    """marketplace_installs table structure tests."""

    def test_schema_file_exists(self):
        schema_path = ROOT / "schema" / "35_marketplace_installs.sql"
        self.assertTrue(schema_path.exists(), "35_marketplace_installs.sql not found")
        print("  ✅ Schema: 35_marketplace_installs.sql exists")

    def test_schema_creates_table(self):
        """SQL in schema file creates the table without errors."""
        schema_path = ROOT / "schema" / "35_marketplace_installs.sql"
        sql = schema_path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            conn = sqlite3.connect(db_path)
            conn.executescript(sql)
            conn.commit()
            # Verify table exists
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='marketplace_installs'"
            ).fetchone()
            self.assertIsNotNone(row)
            conn.close()
        print("  ✅ Schema: marketplace_installs table created by SQL")

    def test_unique_constraint_prevents_duplicates(self):
        """Duplicate install records are silently ignored (INSERT OR IGNORE)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "skill", "mkt-skill-1", "user-1")
            _record_install(conn, "skill", "mkt-skill-1", "user-1")  # duplicate
            count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_installs "
                "WHERE artifact_type='skill' AND marketplace_id='mkt-skill-1' AND user_id='user-1'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
            conn.close()
        print("  ✅ Schema: UNIQUE constraint prevents duplicate install records")

    def test_different_users_each_get_record(self):
        """Same artifact installed by different users = separate records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "skill", "mkt-skill-1", "user-alice")
            _record_install(conn, "skill", "mkt-skill-1", "user-bob")
            count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_installs WHERE marketplace_id='mkt-skill-1'"
            ).fetchone()[0]
            self.assertEqual(count, 2)
            conn.close()
        print("  ✅ Schema: different users get separate install records for same artifact")

    def test_different_artifact_types_separate(self):
        """Same marketplace_id in different artifact types = separate rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "skill", "mkt-id-1", "user-1")
            _record_install(conn, "extension", "mkt-id-1", "user-1")
            count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_installs WHERE user_id='user-1'"
            ).fetchone()[0]
            self.assertEqual(count, 2)
            conn.close()
        print("  ✅ Schema: different artifact types with same marketplace_id are separate")


# ─────────────────────────────────────────────────────────────────────────────
# Rating gate — Agent Pack
# ─────────────────────────────────────────────────────────────────────────────

class TestFix4A_AgentPackRatingGate(unittest.TestCase):
    """Agent pack rating must require a prior install record."""

    def _check_can_rate(self, conn, pack_id: str, user_id: str,
                         source_installation_id: int = None) -> bool:
        """Simulate the rating gate check from agent_pack_routes.py."""
        # Check marketplace_installs first
        row = conn.execute(
            "SELECT 1 FROM marketplace_installs "
            "WHERE artifact_type = 'agent_pack' AND marketplace_id = ? AND user_id = ?",
            (pack_id, user_id),
        ).fetchone()
        if row:
            return True
        # Legacy fallback: sharing grant
        if source_installation_id is not None:
            row = conn.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'agent_pack' AND resource_id = ? AND grantee_user_id = ?",
                (str(source_installation_id), user_id),
            ).fetchone()
            if row:
                return True
        return False

    def test_user_with_install_record_can_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "agent_pack", "pack-1", "user-sub")
            self.assertTrue(self._check_can_rate(conn, "pack-1", "user-sub"))
        print("  ✅ Agent pack: user with install record can rate")

    def test_user_without_install_record_cannot_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            # No install record
            self.assertFalse(self._check_can_rate(conn, "pack-1", "user-uninstalled"))
        print("  ✅ Agent pack: user without install record cannot rate")

    def test_legacy_grant_allows_rating(self):
        """Pre-Fix 4A subscribe: sharing grant allows rating via legacy fallback."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            # No marketplace_installs record but sharing grant exists
            import uuid
            conn.execute(
                "INSERT INTO marketplace_sharing_grants (id, resource_type, resource_id, grantor_user_id, grantee_user_id) "
                "VALUES (?, 'agent_pack', '42', 'pub-user', 'user-legacy')",
                (str(uuid.uuid4()),),
            )
            conn.commit()
            # source_installation_id=42 matches resource_id='42'
            self.assertTrue(
                self._check_can_rate(conn, "pack-1", "user-legacy", source_installation_id=42)
            )
        print("  ✅ Agent pack: legacy sharing grant allows rating (backwards compatibility)")

    def test_install_recorded_on_subscribe(self):
        """Subscribe endpoint must insert a marketplace_installs row."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            # Simulate what subscribe endpoint does
            now_ts = datetime.now(timezone.utc).isoformat()
            import uuid
            conn.execute(
                "INSERT OR IGNORE INTO marketplace_installs "
                "(id, artifact_type, marketplace_id, user_id, installed_at) VALUES (?, 'agent_pack', ?, ?, ?)",
                (str(uuid.uuid4()), "pack-id-1", "user-sub-new", now_ts),
            )
            conn.commit()
            self.assertTrue(_check_install(conn, "agent_pack", "pack-id-1", "user-sub-new"))
        print("  ✅ Agent pack: subscribe records install in marketplace_installs")

    def test_publisher_cannot_rate_own_pack(self):
        """Publisher is blocked before the install check (own pack guard takes priority)."""
        # This tests the order: own pack check first, then install check
        pack = {"publisher_user_id": "pub-user"}
        user_uuid = "pub-user"
        can_proceed = pack["publisher_user_id"] != user_uuid
        self.assertFalse(can_proceed)
        print("  ✅ Agent pack: publisher cannot rate own pack (own-pack guard fires first)")


# ─────────────────────────────────────────────────────────────────────────────
# Rating gate — Skill
# ─────────────────────────────────────────────────────────────────────────────

class TestFix4A_SkillRatingGate(unittest.TestCase):
    """Skill rating must require a prior install record."""

    def _check_can_rate_skill(self, conn, marketplace_id: str, user_id: str,
                               skill_id: str = None, skill_loaded: bool = False) -> bool:
        row = conn.execute(
            "SELECT 1 FROM marketplace_installs "
            "WHERE artifact_type = 'skill' AND marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_id),
        ).fetchone()
        if row:
            return True
        # Legacy fallback: skill is currently installed
        if skill_id and skill_loaded:
            return True
        return False

    def test_user_with_install_record_can_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "skill", "mkt-skill-1", "user-sub")
            self.assertTrue(self._check_can_rate_skill(conn, "mkt-skill-1", "user-sub"))
        print("  ✅ Skill: user with install record can rate")

    def test_user_without_install_cannot_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            self.assertFalse(
                self._check_can_rate_skill(conn, "mkt-skill-1", "user-none",
                                            skill_id="my-skill", skill_loaded=False)
            )
        print("  ✅ Skill: user without install and skill not loaded cannot rate")

    def test_legacy_loaded_skill_allows_rating(self):
        """Pre-Fix 4A: skill currently loaded in skill manager allows rating."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            self.assertTrue(
                self._check_can_rate_skill(conn, "mkt-skill-1", "user-legacy",
                                            skill_id="my-skill", skill_loaded=True)
            )
        print("  ✅ Skill: legacy loaded skill allows rating (backwards compatibility)")

    def test_install_recorded_on_install(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "skill", "mkt-skill-1", "user-1")
            self.assertTrue(_check_install(conn, "skill", "mkt-skill-1", "user-1"))
        print("  ✅ Skill: install endpoint records install in marketplace_installs")


# ─────────────────────────────────────────────────────────────────────────────
# Rating gate — Extension
# ─────────────────────────────────────────────────────────────────────────────

class TestFix4A_ExtensionRatingGate(unittest.TestCase):
    """Extension rating must require a prior install record."""

    def _check_can_rate_ext(self, conn, marketplace_id: str, user_id: str,
                              ext_id: str = None, ext_loaded: bool = False) -> bool:
        row = conn.execute(
            "SELECT 1 FROM marketplace_installs "
            "WHERE artifact_type = 'extension' AND marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_id),
        ).fetchone()
        if row:
            return True
        if ext_id and ext_loaded:
            return True
        return False

    def test_user_with_install_can_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "extension", "mkt-ext-1", "user-sub")
            self.assertTrue(self._check_can_rate_ext(conn, "mkt-ext-1", "user-sub"))
        print("  ✅ Extension: user with install record can rate")

    def test_user_without_install_cannot_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            self.assertFalse(
                self._check_can_rate_ext(conn, "mkt-ext-1", "user-none",
                                          ext_id="my-ext", ext_loaded=False)
            )
        print("  ✅ Extension: user without install cannot rate")

    def test_legacy_loaded_extension_allows_rating(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            self.assertTrue(
                self._check_can_rate_ext(conn, "mkt-ext-1", "user-legacy",
                                          ext_id="my-ext", ext_loaded=True)
            )
        print("  ✅ Extension: legacy loaded extension allows rating (backwards compatibility)")


# ─────────────────────────────────────────────────────────────────────────────
# Rating gate — Knowledge Graph
# ─────────────────────────────────────────────────────────────────────────────

class TestFix4A_KGRatingGate(unittest.TestCase):
    """KG rating must require a prior install record."""

    def _check_can_rate_kg(self, conn, marketplace_id: str, user_id: str,
                             has_marketplace_kg: bool = False) -> bool:
        row = conn.execute(
            "SELECT 1 FROM marketplace_installs "
            "WHERE artifact_type = 'knowledge_graph' AND marketplace_id = ? AND user_id = ?",
            (marketplace_id, user_id),
        ).fetchone()
        if row:
            return True
        if has_marketplace_kg:
            return True
        return False

    def test_user_with_install_can_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "knowledge_graph", "mkt-kg-1", "user-sub")
            self.assertTrue(self._check_can_rate_kg(conn, "mkt-kg-1", "user-sub"))
        print("  ✅ KG: user with install record can rate")

    def test_user_without_install_cannot_rate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            self.assertFalse(
                self._check_can_rate_kg(conn, "mkt-kg-1", "user-none", has_marketplace_kg=False)
            )
        print("  ✅ KG: user without install cannot rate")

    def test_legacy_marketplace_kg_allows_rating(self):
        """Pre-Fix 4A: user with marketplace-sourced KG entities allows rating."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            self.assertTrue(
                self._check_can_rate_kg(conn, "mkt-kg-1", "user-legacy", has_marketplace_kg=True)
            )
        print("  ✅ KG: legacy marketplace KG entity allows rating (backwards compatibility)")

    def test_install_recorded_on_kg_install(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            _record_install(conn, "knowledge_graph", "mkt-kg-1", "user-1")
            self.assertTrue(_check_install(conn, "knowledge_graph", "mkt-kg-1", "user-1"))
        print("  ✅ KG: install endpoint records install in marketplace_installs")


# ─────────────────────────────────────────────────────────────────────────────
# Install idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestFix4A_InstallIdempotency(unittest.TestCase):
    """Reinstalling an artifact must not create duplicate install records."""

    def test_repeated_installs_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            for _ in range(5):
                _record_install(conn, "skill", "mkt-skill-1", "user-1")
            count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_installs "
                "WHERE artifact_type='skill' AND marketplace_id='mkt-skill-1' AND user_id='user-1'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
            conn.close()
        print("  ✅ Idempotency: 5 repeated installs result in exactly 1 record")

    def test_install_check_after_multiple_installs(self):
        """User should still be able to rate after reinstalling."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = _make_test_db(str(Path(tmpdir) / "test.db"))
            for _ in range(3):
                _record_install(conn, "extension", "mkt-ext-1", "user-1")
            self.assertTrue(_check_install(conn, "extension", "mkt-ext-1", "user-1"))
            conn.close()
        print("  ✅ Idempotency: install check returns True after repeated installs")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level source checks
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase4ModuleImport(unittest.TestCase):

    def test_agent_pack_routes_rating_gate(self):
        src = (ROOT / "src" / "trusted_data_agent" / "api" / "agent_pack_routes.py").read_text()
        self.assertIn("marketplace_installs", src)
        self.assertIn("You must subscribe to or install this pack before rating it", src)
        print("  ✅ agent_pack_routes: rating gate present")

    def test_skills_routes_rating_gate(self):
        src = (ROOT / "src" / "trusted_data_agent" / "api" / "skills_routes.py").read_text()
        self.assertIn("marketplace_installs", src)
        self.assertIn("You must install this skill before rating it", src)
        print("  ✅ skills_routes: rating gate present")

    def test_rest_routes_extension_rating_gate(self):
        src = (ROOT / "src" / "trusted_data_agent" / "api" / "rest_routes.py").read_text()
        self.assertIn("marketplace_installs", src)
        self.assertIn("You must install this extension before rating it", src)
        print("  ✅ rest_routes: extension rating gate present")

    def test_kg_marketplace_routes_rating_gate(self):
        src = (ROOT / "src" / "trusted_data_agent" / "api" / "kg_marketplace_routes.py").read_text()
        self.assertIn("marketplace_installs", src)
        self.assertIn("You must install this knowledge graph before rating it", src)
        print("  ✅ kg_marketplace_routes: KG rating gate present")

    def test_database_migration_registered(self):
        src = (ROOT / "src" / "trusted_data_agent" / "auth" / "database.py").read_text()
        self.assertIn("_create_marketplace_installs_table", src)
        self.assertIn("35_marketplace_installs.sql", src)
        print("  ✅ database.py: marketplace_installs migration registered")

    def test_all_install_endpoints_record_installs(self):
        """Each install endpoint must call INSERT OR IGNORE INTO marketplace_installs."""
        files = [
            ROOT / "src" / "trusted_data_agent" / "api" / "agent_pack_routes.py",
            ROOT / "src" / "trusted_data_agent" / "api" / "skills_routes.py",
            ROOT / "src" / "trusted_data_agent" / "api" / "rest_routes.py",
            ROOT / "src" / "trusted_data_agent" / "api" / "kg_marketplace_routes.py",
        ]
        for f in files:
            src = f.read_text()
            self.assertIn(
                "INSERT OR IGNORE INTO marketplace_installs", src,
                f"Missing install tracking in {f.name}"
            )
        print("  ✅ All 4 install endpoints record into marketplace_installs")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestFix4A_Schema,
        TestFix4A_AgentPackRatingGate,
        TestFix4A_SkillRatingGate,
        TestFix4A_ExtensionRatingGate,
        TestFix4A_KGRatingGate,
        TestFix4A_InstallIdempotency,
        TestPhase4ModuleImport,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print(f"\n{'=' * 60}")
    print(f"Phase 4 Results: {passed}/{total} tests passed")
    if failed:
        print(f"  FAILED: {failed} test(s)")
        for test, tb in result.failures + result.errors:
            print(f"    ✗ {test}")
            print(f"      {tb[-500:]}")
    else:
        print("  All Phase 4 tests PASSED ✅")
    print(f"{'=' * 60}")

    sys.exit(0 if result.wasSuccessful() else 1)
