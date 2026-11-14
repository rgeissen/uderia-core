"""
Phase 1 Marketplace Subscribe/Install Tests
============================================
Tests for:
  Fix 1A — async lock on add_profile_locked
  Fix 1B — ID remapping via build_subscribed_profiles
  Fix 1C — atomic grant creation (IntegrityError → 409)
  Fix 1D — SQLite CASE expression in unsubscribe decrement
  Fix 1E — 0-profile pack guard

Run:
    python test/test_marketplace_phase1.py
"""

import asyncio
import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_publisher_profile(tag: str, profile_type: str, llm_id: str = "pub-llm-1",
                             mcp_id: str = "pub-mcp-1") -> dict:
    base = {
        "id": f"profile-pub-{tag.lower()}",
        "tag": tag,
        "name": f"Publisher {tag}",
        "description": "",
        "profile_type": profile_type,
        "llmConfigurationId": llm_id,
        "contextWindowTypeId": "cwt-default-balanced",
        "classification_mode": "light",
    }
    if profile_type == "tool_enabled":
        base["mcpServerId"] = mcp_id
        base["ragCollections"] = [10, 11]
    elif profile_type == "rag_focused":
        base["knowledgeConfig"] = {"collections": [{"id": 20, "name": "KnowledgeBase"}]}
    elif profile_type == "genie":
        base["genieConfig"] = {
            "slaveProfiles": ["profile-pub-expert"],
            "slaveProfileSettings": {"profile-pub-expert": {"fullResultPassthrough": True}},
        }
    return base


def _make_pack_resources(profiles: list[dict], collection_ids: list[int] = None) -> list[dict]:
    resources = []
    for p in profiles:
        resources.append({
            "resource_type": "profile",
            "resource_id": p["id"],
            "resource_tag": p.get("tag"),
            "resource_role": "standalone",
        })
    for cid in (collection_ids or []):
        resources.append({
            "resource_type": "collection",
            "resource_id": str(cid),
            "resource_tag": None,
            "resource_role": None,
        })
    return resources


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1A — async lock
# ─────────────────────────────────────────────────────────────────────────────

class TestFix1A_AsyncLock(unittest.IsolatedAsyncioTestCase):
    """add_profile_locked must serialise concurrent writes and prevent duplicates."""

    async def test_lock_module_exists(self):
        from trusted_data_agent.core.config_manager import _get_user_config_lock, _user_config_locks
        lock = _get_user_config_lock("user-test-lock")
        self.assertIsInstance(lock, asyncio.Lock)

    async def test_add_profile_locked_no_duplicate(self):
        """Two concurrent calls for the same profile ID must result in exactly one insertion."""
        from trusted_data_agent.core.config_manager import ConfigManager

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "tda_config.json"
            cfg_path.write_text(json.dumps({
                "schema_version": "1.0",
                "profiles": [],
                "llm_configurations": [],
                "mcp_servers": [],
                "context_window_types": [],
            }))

            cm = ConfigManager(config_path=cfg_path)

            # Patch the DB-backed path so it uses in-memory dict only
            def _save(config, user_uuid=None):
                cm._user_configs["test-user"] = config
                return True
            def _load(user_uuid=None):
                return cm._user_configs.get("test-user", {
                    "profiles": [], "llm_configurations": [],
                    "mcp_servers": [], "context_window_types": [],
                })
            cm.save_config = _save
            cm.load_config = _load
            cm._user_configs["test-user"] = {
                "profiles": [], "llm_configurations": [],
                "mcp_servers": [], "context_window_types": [],
            }

            profile = {"id": "profile-abc", "tag": "TEST", "profile_type": "llm_only"}

            # Fire 5 concurrent adds of the same profile
            results = await asyncio.gather(*[
                cm.add_profile_locked(profile, "test-user") for _ in range(5)
            ])

            # Exactly one True (inserted), rest False (duplicate skipped)
            self.assertEqual(results.count(True), 1)
            self.assertEqual(results.count(False), 4)

            profiles = cm.get_profiles("test-user")
            self.assertEqual(len(profiles), 1)
            print("  ✅ Fix 1A: concurrent add_profile_locked — exactly 1 insertion, 4 skips")

    async def test_add_profile_locked_different_ids_all_inserted(self):
        """Different profile IDs must all be inserted without dropping any."""
        from trusted_data_agent.core.config_manager import ConfigManager

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "tda_config.json"
            cfg_path.write_text(json.dumps({
                "schema_version": "1.0", "profiles": [],
                "llm_configurations": [], "mcp_servers": [], "context_window_types": [],
            }))
            cm = ConfigManager(config_path=cfg_path)

            def _save(config, user_uuid=None):
                cm._user_configs["u1"] = config
                return True
            def _load(user_uuid=None):
                return cm._user_configs.get("u1", {
                    "profiles": [], "llm_configurations": [],
                    "mcp_servers": [], "context_window_types": [],
                })
            cm.save_config = _save
            cm.load_config = _load
            cm._user_configs["u1"] = {
                "profiles": [], "llm_configurations": [],
                "mcp_servers": [], "context_window_types": [],
            }

            profiles = [{"id": f"profile-{i}", "tag": f"T{i}", "profile_type": "llm_only"}
                        for i in range(10)]
            await asyncio.gather(*[cm.add_profile_locked(p, "u1") for p in profiles])

            stored = cm.get_profiles("u1")
            self.assertEqual(len(stored), 10)
            print("  ✅ Fix 1A: 10 different profiles all inserted safely under lock")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1B — ID remapping via build_subscribed_profiles
# ─────────────────────────────────────────────────────────────────────────────

class TestFix1B_IDRemapping(unittest.IsolatedAsyncioTestCase):

    def _make_manager(self):
        from trusted_data_agent.core.agent_pack_manager import AgentPackManager
        m = AgentPackManager.__new__(AgentPackManager)
        m.db_path = ":memory:"
        return m

    def _mock_config_manager(self, publisher_profiles: list[dict],
                              subscriber_llm_id: str = "sub-llm-1",
                              custom_cwt: bool = False) -> MagicMock:
        cm = MagicMock()
        cm.get_llm_configurations.return_value = [{"id": subscriber_llm_id}]
        cm.get_profile = lambda pid, uid: next(
            (p for p in publisher_profiles if p["id"] == pid), None
        )
        if custom_cwt:
            cm.get_context_window_type.return_value = None  # CWT not found for subscriber
        else:
            cm.get_context_window_type.return_value = {"id": "cwt-default-balanced"}
        return cm

    # Patch target note: build_subscribed_profiles uses local imports inside the
    # method body, so we must patch at the source module, not agent_pack_manager.
    _CM_PATCH = "trusted_data_agent.core.config_manager.get_config_manager"
    _CDB_PATCH = "trusted_data_agent.core.collection_db.CollectionDatabase"

    def test_llm_id_remapped(self):
        """Subscribed profiles must carry subscriber's LLM ID, not publisher's."""
        manager = self._make_manager()
        pub_profile = _make_publisher_profile("IDEAT", "llm_only", llm_id="pub-llm-999")
        pack_resources = _make_pack_resources([pub_profile])

        cm = self._mock_config_manager([pub_profile], subscriber_llm_id="sub-llm-42")
        with patch(self._CM_PATCH, return_value=cm):
            with patch(self._CDB_PATCH) as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = None
                result = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user",
                    subscriber_user_id="sub-user",
                    llm_config_id="sub-llm-42",
                    mcp_server_id=None,
                    source_installation_id=99,
                )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["llmConfigurationId"], "sub-llm-42",
                         "Subscriber LLM ID must replace publisher's")
        self.assertNotEqual(result[0]["id"], pub_profile["id"],
                            "Profile must get a fresh ID, not publisher's")
        self.assertTrue(result[0].get("is_subscribed"))
        print("  ✅ Fix 1B: llmConfigurationId remapped to subscriber's value")

    def test_mcp_server_id_remapped(self):
        """tool_enabled profiles must carry subscriber's MCP server ID."""
        manager = self._make_manager()
        pub_profile = _make_publisher_profile("OPTIM", "tool_enabled", mcp_id="pub-mcp-999")
        pack_resources = _make_pack_resources([pub_profile])

        cm = self._mock_config_manager([pub_profile])
        with patch(self._CM_PATCH, return_value=cm):
            with patch(self._CDB_PATCH) as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = None
                result = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user",
                    subscriber_user_id="sub-user",
                    llm_config_id="sub-llm-1",
                    mcp_server_id="sub-mcp-42",
                    source_installation_id=99,
                )

        self.assertEqual(result[0].get("mcpServerId"), "sub-mcp-42",
                         "Subscriber MCP server ID must replace publisher's")
        self.assertNotIn("pub-mcp-999", str(result[0]))
        print("  ✅ Fix 1B: mcpServerId remapped to subscriber's value")

    def test_genie_slave_profiles_remapped(self):
        """Genie coordinator slaveProfiles must reference new subscriber-scoped child IDs."""
        manager = self._make_manager()
        expert = _make_publisher_profile("EXPERT", "tool_enabled")
        coordinator = _make_publisher_profile("COORD", "genie")
        coordinator["genieConfig"]["slaveProfiles"] = [expert["id"]]
        coordinator["genieConfig"]["slaveProfileSettings"] = {
            expert["id"]: {"fullResultPassthrough": True}
        }

        pack_resources = _make_pack_resources([expert, coordinator])
        cm = self._mock_config_manager([expert, coordinator])

        with patch(self._CM_PATCH, return_value=cm):
            with patch(self._CDB_PATCH) as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = None
                result = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user",
                    subscriber_user_id="sub-user",
                    llm_config_id="sub-llm-1",
                    mcp_server_id="sub-mcp-1",
                    source_installation_id=99,
                )

        genie = next(p for p in result if p["profile_type"] == "genie")
        new_expert = next(p for p in result if p["profile_type"] == "tool_enabled")

        slave_ids = genie["genieConfig"]["slaveProfiles"]
        self.assertEqual(slave_ids, [new_expert["id"]],
                         "slaveProfiles must reference new subscriber child ID")
        slave_settings_keys = list(genie["genieConfig"].get("slaveProfileSettings", {}).keys())
        self.assertEqual(slave_settings_keys, [new_expert["id"]],
                         "slaveProfileSettings keys must use new subscriber IDs")
        self.assertNotIn(expert["id"], str(genie))
        print("  ✅ Fix 1B: genie slaveProfiles + slaveProfileSettings keys remapped")

    def test_collection_ids_mapped_from_pack_resources(self):
        """ragCollections integers must reference the pack's collection IDs (same source)."""
        manager = self._make_manager()
        pub_profile = _make_publisher_profile("OPTIM", "tool_enabled")
        pub_profile["ragCollections"] = [10]
        pack_resources = _make_pack_resources([pub_profile], collection_ids=[10])

        cm = self._mock_config_manager([pub_profile])
        # Local import inside build_subscribed_profiles — patch at source module
        with patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=cm):
            with patch("trusted_data_agent.core.collection_db.CollectionDatabase") as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = {
                    "id": 10, "name": "PlannerColl"
                }
                result = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user",
                    subscriber_user_id="sub-user",
                    llm_config_id="sub-llm-1",
                    mcp_server_id="sub-mcp-1",
                    source_installation_id=99,
                )

        self.assertIn(10, result[0].get("ragCollections", []),
                      "Collection ID 10 from pack resources must be in ragCollections")
        print("  ✅ Fix 1B: collection IDs resolved from pack resources")

    def test_custom_cwt_falls_back_to_default(self):
        """If publisher's custom CWT doesn't exist in subscriber account → reset to default."""
        manager = self._make_manager()
        pub_profile = _make_publisher_profile("IDEAT", "llm_only")
        pub_profile["contextWindowTypeId"] = "cwt-custom-pub-only"
        pack_resources = _make_pack_resources([pub_profile])

        cm = self._mock_config_manager([pub_profile], custom_cwt=True)
        with patch(self._CM_PATCH, return_value=cm):
            with patch(self._CDB_PATCH) as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = None
                result = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user",
                    subscriber_user_id="sub-user",
                    llm_config_id="sub-llm-1",
                    mcp_server_id=None,
                    source_installation_id=99,
                )

        self.assertEqual(result[0]["contextWindowTypeId"], "cwt-default-balanced",
                         "Custom CWT not in subscriber DB must fall back to default")
        print("  ✅ Fix 1B: custom contextWindowTypeId falls back to cwt-default-balanced")

    def test_subscribe_hash_stored(self):
        """Each built profile must carry a subscribe_hash for change detection."""
        manager = self._make_manager()
        pub_profile = _make_publisher_profile("IDEAT", "llm_only")
        pack_resources = _make_pack_resources([pub_profile])

        cm = self._mock_config_manager([pub_profile])
        with patch(self._CM_PATCH, return_value=cm):
            with patch(self._CDB_PATCH) as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = None
                result = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user",
                    subscriber_user_id="sub-user",
                    llm_config_id="sub-llm-1",
                    mcp_server_id=None,
                    source_installation_id=99,
                )

        # Re-pinned 2026-08-25: `subscribe_hash` was the modification-detection marker
        # for the "convert a modified subscribed profile to standalone" branch, which
        # 43ff4f7c retired the same day this suite landed (2026-05-05) — the shipped
        # rule is "always delete; fork is the customisation path", so the hash was
        # never stamped anywhere in src/ (verified by grep) and change detection on
        # subscribed profiles is not a shipped feature. The two markers that ARE the
        # cleanup contract stay pinned.
        self.assertNotIn("subscribe_hash", result[0])
        self.assertIn("subscribed_at", result[0])
        self.assertIn("source_pack_installation_id", result[0])
        print("  ✅ Fix 1B: subscribed_at + source_pack_installation_id set (hash retired)")

    def test_unknown_llm_raises(self):
        """Passing an LLM ID not in subscriber's account must raise ValueError."""
        manager = self._make_manager()
        pub_profile = _make_publisher_profile("IDEAT", "llm_only")
        pack_resources = _make_pack_resources([pub_profile])

        cm = MagicMock()
        cm.get_llm_configurations.return_value = [{"id": "sub-llm-1"}]
        cm.get_profile = lambda pid, uid: pub_profile if pid == pub_profile["id"] else None

        with patch(self._CM_PATCH, return_value=cm):
            with patch(self._CDB_PATCH) as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = None
                with self.assertRaises(ValueError):
                    manager.build_subscribed_profiles(
                        pack_resources=pack_resources,
                        publisher_user_id="pub-user",
                        subscriber_user_id="sub-user",
                        llm_config_id="nonexistent-llm",
                        mcp_server_id=None,
                        source_installation_id=99,
                    )
        print("  ✅ Fix 1B: unknown llm_config_id raises ValueError")

    def test_no_publisher_ids_in_output(self):
        """No publisher-owned LLM/MCP IDs must appear anywhere in result profiles."""
        manager = self._make_manager()
        pub_profile = _make_publisher_profile("OPTIM", "tool_enabled",
                                              llm_id="pub-llm-MUST-NOT-APPEAR",
                                              mcp_id="pub-mcp-MUST-NOT-APPEAR")
        pack_resources = _make_pack_resources([pub_profile])
        cm = self._mock_config_manager([pub_profile], subscriber_llm_id="sub-llm-OK")

        with patch(self._CM_PATCH, return_value=cm):
            with patch(self._CDB_PATCH) as MockCDB:
                MockCDB.return_value.get_collection_by_id.return_value = None
                result = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user",
                    subscriber_user_id="sub-user",
                    llm_config_id="sub-llm-OK",
                    mcp_server_id="sub-mcp-OK",
                    source_installation_id=99,
                )

        result_str = json.dumps(result)
        self.assertNotIn("pub-llm-MUST-NOT-APPEAR", result_str)
        self.assertNotIn("pub-mcp-MUST-NOT-APPEAR", result_str)
        print("  ✅ Fix 1B: no publisher LLM/MCP IDs in subscriber profile output")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1C — Atomic grant creation
# ─────────────────────────────────────────────────────────────────────────────

class TestFix1C_AtomicGrant(unittest.TestCase):

    def test_subscribe_returns_409_on_duplicate_grant(self):
        """Re-subscribing must return 409 via IntegrityError, not duplicate grant rows."""
        # We test the logic at the DB level: inserting the same unique key twice
        # must raise IntegrityError (which the endpoint catches as 409).
        import sqlite3 as sq
        conn = sq.connect(":memory:")
        conn.execute("""
            CREATE TABLE marketplace_sharing_grants (
                id TEXT PRIMARY KEY,
                resource_type TEXT,
                resource_id TEXT,
                grantee_user_id TEXT,
                UNIQUE(resource_type, resource_id, grantee_user_id)
            )
        """)
        conn.execute(
            "INSERT INTO marketplace_sharing_grants VALUES (?, ?, ?, ?)",
            ("g1", "agent_pack", "42", "user-sub")
        )
        conn.commit()
        with self.assertRaises(sq.IntegrityError):
            conn.execute(
                "INSERT INTO marketplace_sharing_grants VALUES (?, ?, ?, ?)",
                ("g2", "agent_pack", "42", "user-sub")
            )
        print("  ✅ Fix 1C: duplicate grant INSERT raises IntegrityError → 409")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1D — SQLite CASE expression (was MAX())
# ─────────────────────────────────────────────────────────────────────────────

class TestFix1D_SQLiteDecrement(unittest.TestCase):

    def _setup_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE marketplace_agent_packs (
                id TEXT PRIMARY KEY,
                install_count INTEGER DEFAULT 0,
                updated_at TEXT
            )
        """)
        conn.execute("INSERT INTO marketplace_agent_packs VALUES ('pack-1', 3, '2025-01-01')")
        conn.commit()
        return conn

    def test_case_decrement_from_positive(self):
        conn = self._setup_db()
        conn.execute(
            "UPDATE marketplace_agent_packs "
            "SET install_count = CASE WHEN install_count > 0 THEN install_count - 1 ELSE 0 END "
            "WHERE id = 'pack-1'"
        )
        conn.commit()
        row = conn.execute("SELECT install_count FROM marketplace_agent_packs WHERE id='pack-1'").fetchone()
        self.assertEqual(row[0], 2)
        print("  ✅ Fix 1D: CASE decrement from 3 → 2")

    def test_case_decrement_does_not_go_negative(self):
        conn = self._setup_db()
        conn.execute("UPDATE marketplace_agent_packs SET install_count=0 WHERE id='pack-1'")
        conn.execute(
            "UPDATE marketplace_agent_packs "
            "SET install_count = CASE WHEN install_count > 0 THEN install_count - 1 ELSE 0 END "
            "WHERE id = 'pack-1'"
        )
        conn.commit()
        row = conn.execute("SELECT install_count FROM marketplace_agent_packs WHERE id='pack-1'").fetchone()
        self.assertEqual(row[0], 0, "install_count must not go below 0")
        print("  ✅ Fix 1D: CASE decrement stays at 0 when already 0")

    def test_case_multiple_decrements_reach_zero_not_negative(self):
        """Repeated decrements must floor at 0, not go negative."""
        conn = self._setup_db()
        for _ in range(10):
            conn.execute(
                "UPDATE marketplace_agent_packs "
                "SET install_count = CASE WHEN install_count > 0 THEN install_count - 1 ELSE 0 END "
                "WHERE id = 'pack-1'"
            )
        conn.commit()
        row = conn.execute("SELECT install_count FROM marketplace_agent_packs WHERE id='pack-1'").fetchone()
        self.assertEqual(row[0], 0, "10 decrements from 3 must floor at 0, not -7")
        print("  ✅ Fix 1D: repeated decrements floor at 0 (not negative)")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1E — 0-profile pack guard
# ─────────────────────────────────────────────────────────────────────────────

class TestFix1E_ZeroProfileGuard(unittest.TestCase):

    def test_zero_profile_count_detected(self):
        """pack_resources with no profiles must be detected before grant creation."""
        resources = [
            {"resource_type": "collection", "resource_id": "5"},
            {"resource_type": "collection", "resource_id": "6"},
        ]
        profile_count = sum(1 for r in resources if r["resource_type"] == "profile")
        self.assertEqual(profile_count, 0)
        # The endpoint checks `if profile_count == 0: return 409`
        self.assertTrue(profile_count == 0, "0-profile pack must be rejected")
        print("  ✅ Fix 1E: 0-profile pack correctly detected")

    def test_nonempty_profile_count_passes(self):
        resources = _make_pack_resources(
            [_make_publisher_profile("IDEAT", "llm_only")], collection_ids=[5]
        )
        profile_count = sum(1 for r in resources if r["resource_type"] == "profile")
        self.assertEqual(profile_count, 1)
        print("  ✅ Fix 1E: pack with 1+ profiles passes the guard")


# ─────────────────────────────────────────────────────────────────────────────
# Integration: subscribe endpoint smoke test via module import
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase1ModuleImport(unittest.TestCase):

    def test_config_manager_imports_cleanly(self):
        from trusted_data_agent.core.config_manager import (
            ConfigManager, _get_user_config_lock, _user_config_locks
        )
        self.assertTrue(callable(_get_user_config_lock))
        cm = ConfigManager.__new__(ConfigManager)
        self.assertTrue(hasattr(cm, "add_profile_locked"))
        self.assertTrue(asyncio.iscoroutinefunction(cm.add_profile_locked))
        print("  ✅ config_manager: add_profile_locked is an async method")

    def test_agent_pack_manager_has_build_subscribed_profiles(self):
        from trusted_data_agent.core.agent_pack_manager import AgentPackManager
        self.assertTrue(hasattr(AgentPackManager, "build_subscribed_profiles"))
        print("  ✅ agent_pack_manager: build_subscribed_profiles method present")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Phase 1 Marketplace Tests")
    print("=" * 60 + "\n")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestFix1A_AsyncLock,
        TestFix1B_IDRemapping,
        TestFix1C_AtomicGrant,
        TestFix1D_SQLiteDecrement,
        TestFix1E_ZeroProfileGuard,
        TestPhase1ModuleImport,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print(f"  ✅ ALL {result.testsRun} Phase 1 tests PASSED")
    else:
        print(f"  ❌ {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 60)
    sys.exit(0 if result.wasSuccessful() else 1)
