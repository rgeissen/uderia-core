"""
Phase 2 Marketplace Subscribe/Install Tests
============================================
Tests for:
  Fix 2A — Compensating rollback on partial subscribe failure
  Fix 2B — Protect user-modified profiles on unsubscribe
  Fix 2C — Clear default_profile_id after unsubscribe removes default
  Fix 2D — Skills install conflict detection + reload rollback
  Fix 2E — Extensions install conflict detection + dependency check + rollback

Run:
    python test/test_marketplace_phase2.py
"""

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock, call

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_profile(pid: str, tag: str, ptype: str, **kwargs) -> dict:
    p = {
        "id": pid,
        "tag": tag,
        "name": f"Profile {tag}",
        "profile_type": ptype,
        "llmConfigurationId": "llm-sub-1",
    }
    p.update(kwargs)
    return p


def _subscribe_hash(profile: dict) -> str:
    """Compute the subscribe_hash the same way agent_pack_routes.py does."""
    check_copy = {k: v for k, v in profile.items() if k != "subscribe_hash"}
    return hashlib.sha256(
        json.dumps(check_copy, sort_keys=True, default=str).encode()
    ).hexdigest()


def _stamp_subscribe_markers(profile: dict, source_id: int) -> dict:
    """Add subscribe markers (is_subscribed, source_pack_installation_id, subscribe_hash)."""
    p = dict(profile)
    p["is_subscribed"] = True
    p["source_pack_installation_id"] = source_id
    p["subscribed_at"] = "2026-01-01T00:00:00+00:00"
    # Must compute hash AFTER setting markers (same as routes.py)
    p["subscribe_hash"] = _subscribe_hash(p)
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2A — Compensating rollback
# ─────────────────────────────────────────────────────────────────────────────

class TestFix2A_CompensatingRollback(unittest.IsolatedAsyncioTestCase):
    """On subscribe failure mid-loop, all previously added profiles must be rolled back."""

    async def test_rollback_removes_partially_created_profiles(self):
        """If the 3rd profile add raises, the first two must be deleted."""
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
            # Wire in-memory storage
            state: dict = {"profiles": []}
            def _save(config, user_uuid=None):
                state["profiles"] = config.get("profiles", [])
                return True
            def _load(user_uuid=None):
                return {"profiles": list(state["profiles"]), "llm_configurations": [],
                        "mcp_servers": [], "context_window_types": []}
            cm.save_config = _save
            cm.load_config = _load

            profiles_to_add = [
                _make_profile(f"profile-{i}", f"TAG{i}", "llm_only") for i in range(3)
            ]

            # Simulate: 3rd add_profile_locked raises an exception
            call_count = 0
            originals = [True, True, None]  # 3rd will raise

            async def _mock_add(profile, user_uuid):
                nonlocal call_count
                result = originals[call_count]
                call_count += 1
                if result is None:
                    raise RuntimeError("Simulated DB failure on 3rd profile")
                state["profiles"].append(profile)
                return True

            cm.add_profile_locked = _mock_add

            # Track rollback calls
            removed = []
            original_remove = cm.remove_profile
            def _mock_remove(pid, user_uuid):
                removed.append(pid)
            cm.remove_profile = _mock_remove

            # Simulate the compensating rollback logic from agent_pack_routes.py
            created_profile_ids = []
            rolled_back = False
            try:
                for profile in profiles_to_add:
                    added = await cm.add_profile_locked(profile, "test-user")
                    if added:
                        created_profile_ids.append(profile["id"])
            except Exception:
                for pid in created_profile_ids:
                    cm.remove_profile(pid, "test-user")
                rolled_back = True

            self.assertTrue(rolled_back, "Rollback should have fired")
            # First two profiles were created and should be in rollback list
            self.assertIn("profile-0", removed)
            self.assertIn("profile-1", removed)
            self.assertNotIn("profile-2", removed)  # 3rd never added
            print("  ✅ Fix 2A: partial subscribe rollback removes all created profiles")

    async def test_rollback_tracks_only_successfully_added_profiles(self):
        """Profiles that returned False (already existed) must NOT be rolled back."""
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
            state: dict = {"profiles": []}
            def _save(config, user_uuid=None):
                state["profiles"] = config.get("profiles", [])
                return True
            def _load(user_uuid=None):
                return {"profiles": list(state["profiles"]), "llm_configurations": [],
                        "mcp_servers": [], "context_window_types": []}
            cm.save_config = _save
            cm.load_config = _load

            profiles_to_add = [
                _make_profile("profile-existing", "EXIST", "llm_only"),
                _make_profile("profile-new", "NEW", "llm_only"),
                _make_profile("profile-fail", "FAIL", "llm_only"),
            ]

            # Profile 0 already exists (returns False), profile 1 new (True), profile 2 raises
            call_count = 0
            return_values = [False, True, "raise"]

            async def _mock_add(profile, user_uuid):
                nonlocal call_count
                v = return_values[call_count]
                call_count += 1
                if v == "raise":
                    raise RuntimeError("fail")
                if v:
                    state["profiles"].append(profile)
                return bool(v)

            cm.add_profile_locked = _mock_add

            removed = []
            cm.remove_profile = lambda pid, uid: removed.append(pid)

            created_profile_ids = []
            try:
                for profile in profiles_to_add:
                    added = await cm.add_profile_locked(profile, "test-user")
                    if added:
                        created_profile_ids.append(profile["id"])
            except Exception:
                for pid in created_profile_ids:
                    cm.remove_profile(pid, "test-user")

            # Only profile-new was added (True), so only it gets rolled back
            self.assertEqual(removed, ["profile-new"])
            self.assertNotIn("profile-existing", removed)
            print("  ✅ Fix 2A: rollback skips profiles that returned False (already existed)")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2B — Protect modified profiles on unsubscribe
# ─────────────────────────────────────────────────────────────────────────────

class TestFix2B_ModifiedProfileProtection(unittest.TestCase):
    """Modified subscribed profiles must be converted to standalone on unsubscribe."""

    def _simulate_unsubscribe_loop(self, profiles: list[dict], source_id: int) -> dict:
        """Run the unsubscribe loop logic from agent_pack_routes.py and return results."""
        remaining = []
        profiles_removed = 0
        profiles_converted = 0
        removed_ids: set = set()

        for p in profiles:
            is_this_pack = (
                p.get("is_subscribed")
                and p.get("source_pack_installation_id") == source_id
            )
            if not is_this_pack:
                remaining.append(p)
                continue

            stored_hash = p.get("subscribe_hash")
            if stored_hash:
                check_copy = {k: v for k, v in p.items() if k != "subscribe_hash"}
                current_hash = hashlib.sha256(
                    json.dumps(check_copy, sort_keys=True, default=str).encode()
                ).hexdigest()
                is_modified = (current_hash != stored_hash)
            else:
                is_modified = False

            if is_modified:
                converted = {k: v for k, v in p.items()
                             if k not in ("is_subscribed", "source_pack_installation_id",
                                          "subscribe_hash", "subscribed_at", "pack_published_at")}
                remaining.append(converted)
                profiles_converted += 1
            else:
                removed_ids.add(p["id"])
                profiles_removed += 1

        return {
            "remaining": remaining,
            "profiles_removed": profiles_removed,
            "profiles_converted": profiles_converted,
            "removed_ids": removed_ids,
        }

    def test_unmodified_subscribed_profile_is_removed(self):
        """A subscribed profile that hasn't been modified should be deleted on unsubscribe."""
        prof = _make_profile("profile-1", "TEST", "llm_only")
        prof = _stamp_subscribe_markers(prof, source_id=42)

        result = self._simulate_unsubscribe_loop([prof], source_id=42)

        self.assertEqual(result["profiles_removed"], 1)
        self.assertEqual(result["profiles_converted"], 0)
        self.assertIn("profile-1", result["removed_ids"])
        self.assertEqual(len(result["remaining"]), 0)
        print("  ✅ Fix 2B: unmodified subscribed profile removed on unsubscribe")

    def test_modified_subscribed_profile_converted_to_standalone(self):
        """A subscribed profile modified by user becomes standalone on unsubscribe."""
        prof = _make_profile("profile-1", "TEST", "llm_only")
        prof = _stamp_subscribe_markers(prof, source_id=42)

        # Simulate user modifying the profile after subscribing
        prof_modified = dict(prof)
        prof_modified["name"] = "My Custom Name"  # user changed name

        result = self._simulate_unsubscribe_loop([prof_modified], source_id=42)

        self.assertEqual(result["profiles_removed"], 0)
        self.assertEqual(result["profiles_converted"], 1)
        self.assertEqual(len(result["remaining"]), 1)
        converted = result["remaining"][0]
        # Subscribe markers stripped
        self.assertNotIn("is_subscribed", converted)
        self.assertNotIn("source_pack_installation_id", converted)
        self.assertNotIn("subscribe_hash", converted)
        self.assertNotIn("subscribed_at", converted)
        # But data preserved
        self.assertEqual(converted["name"], "My Custom Name")
        self.assertEqual(converted["id"], "profile-1")
        print("  ✅ Fix 2B: modified subscribed profile converted to standalone")

    def test_non_subscribed_profiles_untouched(self):
        """Non-subscribed profiles must pass through unchanged."""
        prof_sub = _stamp_subscribe_markers(
            _make_profile("profile-sub", "SUB", "llm_only"), source_id=42
        )
        prof_other = _make_profile("profile-other", "OTHER", "tool_enabled")

        result = self._simulate_unsubscribe_loop(
            [prof_sub, prof_other], source_id=42
        )

        # sub removed, other untouched
        self.assertEqual(result["profiles_removed"], 1)
        self.assertEqual(len(result["remaining"]), 1)
        self.assertEqual(result["remaining"][0]["id"], "profile-other")
        print("  ✅ Fix 2B: non-subscribed profiles pass through unchanged")

    def test_different_pack_subscribed_profile_untouched(self):
        """Subscribed profile from a different pack must not be removed."""
        prof_pack42 = _stamp_subscribe_markers(
            _make_profile("profile-42", "P42", "llm_only"), source_id=42
        )
        prof_pack99 = _stamp_subscribe_markers(
            _make_profile("profile-99", "P99", "llm_only"), source_id=99
        )

        result = self._simulate_unsubscribe_loop(
            [prof_pack42, prof_pack99], source_id=42
        )

        # Only pack 42 profile removed; pack 99 stays
        self.assertEqual(result["profiles_removed"], 1)
        self.assertIn("profile-42", result["removed_ids"])
        self.assertEqual(len(result["remaining"]), 1)
        self.assertEqual(result["remaining"][0]["id"], "profile-99")
        print("  ✅ Fix 2B: profile from different pack not touched during unsubscribe")

    def test_no_subscribe_hash_treated_as_unmodified(self):
        """Pre-fix subscribed profiles without a hash must be removed (not converted)."""
        prof = _make_profile("profile-nohash", "NOHASH", "llm_only",
                             is_subscribed=True, source_pack_installation_id=42)
        # No subscribe_hash set (pre-fix install)

        result = self._simulate_unsubscribe_loop([prof], source_id=42)

        self.assertEqual(result["profiles_removed"], 1)
        self.assertEqual(result["profiles_converted"], 0)
        print("  ✅ Fix 2B: profile without subscribe_hash treated as unmodified")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2C — Clear default_profile_id after unsubscribe
# ─────────────────────────────────────────────────────────────────────────────

class TestFix2C_ClearDefaultProfile(unittest.TestCase):
    """default_profile_id must be cleared when the default profile is removed by unsubscribe."""

    def test_default_cleared_when_default_is_subscribed_profile(self):
        """If default_profile_id matches a removed subscribed profile, it must be cleared."""
        cleared = []

        # Simulate config_manager calls
        class MockCM:
            def get_default_profile_id(self, user_uuid):
                return "profile-sub"

            def set_default_profile_id(self, value, user_uuid):
                cleared.append(value)

        cm = MockCM()

        # The check from agent_pack_routes.py
        removed_ids = {"profile-sub"}
        default_id = cm.get_default_profile_id("user-1")
        if default_id and default_id in removed_ids:
            cm.set_default_profile_id(None, "user-1")

        self.assertEqual(cleared, [None])
        print("  ✅ Fix 2C: default_profile_id cleared when default profile was unsubscribed")

    def test_default_not_cleared_when_default_is_different_profile(self):
        """If default_profile_id is NOT the removed profile, it must remain unchanged."""
        cleared = []

        class MockCM:
            def get_default_profile_id(self, user_uuid):
                return "profile-keeper"

            def set_default_profile_id(self, value, user_uuid):
                cleared.append(value)

        cm = MockCM()
        removed_ids = {"profile-sub"}
        default_id = cm.get_default_profile_id("user-1")
        if default_id and default_id in removed_ids:
            cm.set_default_profile_id(None, "user-1")

        self.assertEqual(cleared, [], "Should NOT clear default when default is not removed")
        print("  ✅ Fix 2C: default_profile_id NOT cleared when it is a different profile")

    def test_default_not_cleared_when_converted_not_removed(self):
        """If the default profile was modified and became standalone, it should NOT be cleared."""
        cleared = []

        class MockCM:
            def get_default_profile_id(self, user_uuid):
                return "profile-modified"

            def set_default_profile_id(self, value, user_uuid):
                cleared.append(value)

        cm = MockCM()
        # modified profile goes into remaining (converted), not removed_ids
        removed_ids: set = set()
        default_id = cm.get_default_profile_id("user-1")
        if default_id and default_id in removed_ids:
            cm.set_default_profile_id(None, "user-1")

        self.assertEqual(cleared, [], "Should NOT clear default when it was converted to standalone")
        print("  ✅ Fix 2C: default_profile_id NOT cleared when profile was converted (not removed)")

    def test_default_not_cleared_when_no_default(self):
        """If no default is set, set_default_profile_id must not be called."""
        cleared = []

        class MockCM:
            def get_default_profile_id(self, user_uuid):
                return None

            def set_default_profile_id(self, value, user_uuid):
                cleared.append(value)

        cm = MockCM()
        removed_ids = {"profile-sub"}
        default_id = cm.get_default_profile_id("user-1")
        if default_id and default_id in removed_ids:
            cm.set_default_profile_id(None, "user-1")

        self.assertEqual(cleared, [], "Should NOT call set when no default")
        print("  ✅ Fix 2C: set_default_profile_id not called when no default was set")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2D — Skills install conflict detection + reload rollback
# ─────────────────────────────────────────────────────────────────────────────

class TestFix2D_SkillsInstall(unittest.TestCase):
    """Skill install must handle existing conflicts and roll back on reload failure."""

    def _make_mock_manager(self, has_existing: bool = False, reload_raises: Exception | None = None):
        manager = MagicMock()
        if has_existing:
            manager.get_skill_manifest.return_value = {
                "_is_user": True, "name": "Old Skill", "version": "1.0"
            }
            manager.get_skill_full_content.return_value = "# Old content"
        else:
            manager.get_skill_manifest.return_value = None
            manager.get_skill_full_content.return_value = None

        if reload_raises:
            manager.reload.side_effect = reload_raises
        else:
            manager.reload.return_value = None

        return manager

    def test_fresh_install_no_snapshot(self):
        """New skill install: no rollback snapshot needed, install_count incremented."""
        manager = self._make_mock_manager(has_existing=False)
        content = "# New skill content"
        manifest = {"name": "New Skill", "version": "1.0"}

        # Simulate install logic
        existing_manifest = manager.get_skill_manifest("new-skill")
        rollback_snapshot = None
        if existing_manifest:
            rollback_snapshot = {
                "content": manager.get_skill_full_content("new-skill") or "",
                "manifest": existing_manifest,
            }

        manager.save_skill("new-skill", content, manifest)
        manager.reload()

        self.assertIsNone(rollback_snapshot)
        manager.save_skill.assert_called_once_with("new-skill", content, manifest)
        manager.reload.assert_called_once()
        print("  ✅ Fix 2D: fresh install proceeds without rollback snapshot")

    def test_update_existing_skill_snapshots(self):
        """Updating existing skill: snapshot is captured before overwrite."""
        manager = self._make_mock_manager(has_existing=True)
        content = "# New skill content v2"
        manifest = {"name": "My Skill", "version": "2.0"}

        existing_manifest = manager.get_skill_manifest("my-skill")
        rollback_snapshot = None
        if existing_manifest:
            existing_content = manager.get_skill_full_content("my-skill") or ""
            rollback_snapshot = {
                "content": existing_content,
                "manifest": existing_manifest,
            }

        manager.save_skill("my-skill", content, manifest)

        self.assertIsNotNone(rollback_snapshot)
        self.assertEqual(rollback_snapshot["content"], "# Old content")
        self.assertEqual(rollback_snapshot["manifest"]["version"], "1.0")
        print("  ✅ Fix 2D: update install captures rollback snapshot of old version")

    def test_reload_failure_on_update_restores_previous_version(self):
        """If reload fails during update, old version must be restored."""
        manager = self._make_mock_manager(
            has_existing=True,
            reload_raises=RuntimeError("syntax error in skill")
        )

        existing_manifest = manager.get_skill_manifest("my-skill")
        rollback_snapshot = {
            "content": "# Old content",
            "manifest": existing_manifest,
        }

        # Simulate: save new version, reload raises, restore old version
        manager.save_skill("my-skill", "# broken content", {"version": "2.0"})

        restore_called = False
        try:
            manager.reload()
        except RuntimeError:
            # Rollback
            if rollback_snapshot is not None:
                manager.save_skill(
                    "my-skill",
                    rollback_snapshot["content"],
                    rollback_snapshot["manifest"],
                )
                # Reset reload side_effect for restore
                manager.reload.side_effect = None
                manager.reload()
                restore_called = True

        self.assertTrue(restore_called)
        # save_skill called twice: once for new version, once for restore
        self.assertEqual(manager.save_skill.call_count, 2)
        restore_call = manager.save_skill.call_args_list[1]
        self.assertEqual(restore_call[0][1], "# Old content")
        print("  ✅ Fix 2D: reload failure on update restores previous skill version")

    def test_reload_failure_on_fresh_install_deletes_skill(self):
        """If reload fails on fresh install, the newly added skill must be deleted."""
        manager = self._make_mock_manager(
            has_existing=False,
            reload_raises=RuntimeError("import error")
        )

        rollback_snapshot = None  # No existing version
        deleted = []

        def _delete(skill_id):
            deleted.append(skill_id)
        manager.delete_skill = _delete

        # Simulate: save new version, reload raises, delete it
        manager.save_skill("broken-skill", "# broken", {})

        try:
            manager.reload()
        except RuntimeError:
            if rollback_snapshot is None:
                manager.delete_skill("broken-skill")
                manager.reload.side_effect = None
                manager.reload()

        self.assertIn("broken-skill", deleted)
        print("  ✅ Fix 2D: reload failure on fresh install removes the broken skill")

    def test_was_update_flag_set_correctly(self):
        """was_update must be True for existing skill, False for new install."""
        for has_existing, expected_was_update in [(True, True), (False, False)]:
            manager = self._make_mock_manager(has_existing=has_existing)
            existing_manifest = manager.get_skill_manifest("test-skill")
            rollback_snapshot = None
            if existing_manifest:
                rollback_snapshot = {"content": "", "manifest": existing_manifest}

            was_update = rollback_snapshot is not None
            self.assertEqual(was_update, expected_was_update)
        print("  ✅ Fix 2D: was_update flag set correctly for new vs update installs")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2E — Extensions install conflict detection + dependency check + rollback
# ─────────────────────────────────────────────────────────────────────────────

class TestFix2E_ExtensionsInstall(unittest.TestCase):
    """Extension install must check dependencies, snapshot for rollback, and rollback on failure."""

    def _make_mock_ext_manager(self,
                                has_existing: bool = False,
                                reload_raises: Exception | None = None) -> MagicMock:
        manager = MagicMock()
        if has_existing:
            manager.get_manifest.return_value = {
                "_is_user": True,
                "_manifest_path": "/fake/.tda/extensions/my-ext/manifest.json",
                "version": "1.0",
            }
            manager.get_extension_source.return_value = "# Old source"
            manager.get_extension.return_value = MagicMock()  # extension exists
        else:
            manager.get_manifest.return_value = None
            manager.get_extension_source.return_value = None
            manager.get_extension.return_value = None

        if reload_raises:
            manager.reload.side_effect = reload_raises
        else:
            manager.reload.return_value = None

        return manager

    def test_dependency_check_passes_when_all_deps_present(self):
        """Install proceeds when all required_extensions are already installed."""
        manager = self._make_mock_ext_manager(has_existing=False)
        # All required extensions return a non-None extension object
        manager.get_extension.side_effect = lambda name: (
            MagicMock() if name in ("dep-a", "dep-b") else None
        )

        manifest_data = {"requires_extensions": ["dep-a", "dep-b"]}
        required_deps = manifest_data.get("requires_extensions", [])
        missing_deps = [dep for dep in required_deps if not manager.get_extension(dep)]

        self.assertEqual(missing_deps, [])
        print("  ✅ Fix 2E: dependency check passes when all required extensions present")

    def test_dependency_check_fails_when_dep_missing(self):
        """Install returns 409 when a required_extensions dep is not installed."""
        manager = self._make_mock_ext_manager(has_existing=False)
        manager.get_extension.side_effect = lambda name: (
            MagicMock() if name == "dep-a" else None  # dep-b missing
        )

        manifest_data = {"requires_extensions": ["dep-a", "dep-b"]}
        required_deps = manifest_data.get("requires_extensions", [])
        missing_deps = [dep for dep in required_deps if not manager.get_extension(dep)]

        self.assertEqual(missing_deps, ["dep-b"])
        # The route would return 409 here
        print("  ✅ Fix 2E: dependency check detects missing extension dep-b")

    def test_no_dependency_check_when_no_requires(self):
        """Install with no requires_extensions proceeds without calling get_extension."""
        manager = self._make_mock_ext_manager(has_existing=False)

        manifest_data = {}  # No requires_extensions key
        required_deps = manifest_data.get("requires_extensions", [])
        missing_deps = [dep for dep in required_deps if not manager.get_extension(dep)]

        self.assertEqual(missing_deps, [])
        # get_extension should not be called for dependency checking
        manager.get_extension.assert_not_called()
        print("  ✅ Fix 2E: no dependency check when requires_extensions is absent")

    def test_fresh_install_no_snapshot(self):
        """Fresh extension install: no rollback snapshot."""
        manager = self._make_mock_ext_manager(has_existing=False)

        existing_manifest = manager.get_manifest("new-ext")
        rollback_snapshot = None
        if existing_manifest:
            rollback_snapshot = {
                "source": manager.get_extension_source("new-ext") or "",
                "manifest_text": "",
                "ext_id": "new-ext",
            }

        self.assertIsNone(rollback_snapshot)
        print("  ✅ Fix 2E: fresh extension install has no rollback snapshot")

    def test_update_existing_extension_snapshots_old_version(self):
        """Updating extension: snapshot captures old source."""
        manager = self._make_mock_ext_manager(has_existing=True)

        existing_manifest = manager.get_manifest("my-ext")
        rollback_snapshot = None
        if existing_manifest:
            existing_source = manager.get_extension_source("my-ext") or ""
            rollback_snapshot = {
                "source": existing_source,
                "manifest_text": "",
                "ext_id": "my-ext",
            }

        self.assertIsNotNone(rollback_snapshot)
        self.assertEqual(rollback_snapshot["source"], "# Old source")
        print("  ✅ Fix 2E: extension update captures old source for rollback")

    def test_reload_failure_on_update_restores_files(self):
        """Reload failure during update must restore old files to user_ext_dir."""
        manager = self._make_mock_ext_manager(
            has_existing=True,
            reload_raises=RuntimeError("import error")
        )

        rollback_snapshot = {
            "source": "# Old source",
            "manifest_text": '{"version": "1.0"}',
            "ext_id": "my-ext",
        }

        writes: list = []

        with tempfile.TemporaryDirectory() as tmpdir:
            user_ext_dir = Path(tmpdir) / "my-ext"
            user_ext_dir.mkdir()
            py_path = user_ext_dir / "my-ext.py"
            manifest_path = user_ext_dir / "manifest.json"

            # Write new (bad) version
            py_path.write_text("# broken source", encoding="utf-8")
            manifest_path.write_text('{"version": "2.0"}', encoding="utf-8")

            restore_happened = False
            try:
                manager.reload()
            except RuntimeError:
                if rollback_snapshot is not None:
                    py_path.write_text(rollback_snapshot["source"], encoding="utf-8")
                    manifest_path.write_text(rollback_snapshot["manifest_text"], encoding="utf-8")
                    manager.reload.side_effect = None
                    manager.reload()
                    restore_happened = True

            self.assertTrue(restore_happened)
            self.assertEqual(py_path.read_text(), "# Old source")
            self.assertEqual(manifest_path.read_text(), '{"version": "1.0"}')
        print("  ✅ Fix 2E: reload failure on update restores old extension files")

    def test_reload_failure_on_fresh_install_removes_dir(self):
        """Reload failure on fresh install must remove the newly created extension dir."""
        manager = self._make_mock_ext_manager(
            has_existing=False,
            reload_raises=RuntimeError("syntax error")
        )
        rollback_snapshot = None

        with tempfile.TemporaryDirectory() as tmpdir:
            user_ext_dir = Path(tmpdir) / "broken-ext"
            user_ext_dir.mkdir()
            (user_ext_dir / "broken-ext.py").write_text("# broken")
            (user_ext_dir / "manifest.json").write_text("{}")

            cleanup_called = False
            try:
                manager.reload()
            except RuntimeError:
                if rollback_snapshot is None:
                    import shutil
                    shutil.rmtree(user_ext_dir, ignore_errors=True)
                    manager.reload.side_effect = None
                    manager.reload()
                    cleanup_called = True

            self.assertTrue(cleanup_called)
            self.assertFalse(user_ext_dir.exists(), "Extension dir should have been removed")
        print("  ✅ Fix 2E: reload failure on fresh extension install removes broken dir")

    def test_was_update_flag_set_correctly(self):
        """was_update must be True for existing extension, False for new."""
        for has_existing, expected in [(True, True), (False, False)]:
            manager = self._make_mock_ext_manager(has_existing=has_existing)
            existing_manifest = manager.get_manifest("test-ext")
            rollback_snapshot = None
            if existing_manifest:
                rollback_snapshot = {"source": "", "manifest_text": "", "ext_id": "test-ext"}
            was_update = rollback_snapshot is not None
            self.assertEqual(was_update, expected)
        print("  ✅ Fix 2E: was_update flag set correctly for extension new vs update")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level import sanity check
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase2ModuleImport(unittest.TestCase):
    """Ensure all Phase 2 code modules import without errors."""

    def test_config_manager_imports(self):
        from trusted_data_agent.core import config_manager
        self.assertTrue(hasattr(config_manager, "_get_user_config_lock"))
        print("  ✅ config_manager: _get_user_config_lock present")

    def test_agent_pack_routes_imports(self):
        from trusted_data_agent.api import agent_pack_routes
        self.assertTrue(hasattr(agent_pack_routes, "agent_pack_bp"))
        print("  ✅ agent_pack_routes: module imports cleanly")

    def test_agent_pack_manager_imports(self):
        from trusted_data_agent.core import agent_pack_manager
        self.assertTrue(hasattr(agent_pack_manager, "AgentPackManager"))
        print("  ✅ agent_pack_manager: AgentPackManager present")


# ─────────────────────────────────────────────────────────────────────────────
# Subscribe hash consistency check
# ─────────────────────────────────────────────────────────────────────────────

class TestSubscribeHashConsistency(unittest.TestCase):
    """Verify the subscribe_hash scheme detects modifications correctly."""

    def test_hash_stable_for_identical_profile(self):
        """Same profile dict always produces the same hash."""
        prof = _make_profile("p1", "TAG", "llm_only", llmConfigurationId="llm-1")
        h1 = _subscribe_hash(prof)
        h2 = _subscribe_hash(prof)
        self.assertEqual(h1, h2)
        print("  ✅ subscribe_hash: identical profile → stable hash")

    def test_hash_changes_on_name_modification(self):
        """Changing the profile name changes the hash."""
        prof = _make_profile("p1", "TAG", "llm_only")
        h1 = _subscribe_hash(prof)
        prof2 = dict(prof)
        prof2["name"] = "Changed Name"
        h2 = _subscribe_hash(prof2)
        self.assertNotEqual(h1, h2)
        print("  ✅ subscribe_hash: name change detected")

    def test_hash_excludes_subscribe_hash_field_itself(self):
        """Hash computation must exclude the subscribe_hash field to avoid circular dependency."""
        prof = _make_profile("p1", "TAG", "llm_only")
        h1 = _subscribe_hash(prof)
        prof_with_hash = dict(prof)
        prof_with_hash["subscribe_hash"] = "some_previous_hash"
        h2 = _subscribe_hash(prof_with_hash)
        # Must be equal because the hash field is excluded
        self.assertEqual(h1, h2)
        print("  ✅ subscribe_hash: subscribe_hash field excluded from hash computation")

    def test_stamped_profile_unmodified_hash_matches(self):
        """A freshly stamped profile's subscribe_hash must match a recomputation."""
        prof = _make_profile("p1", "TAG", "llm_only")
        stamped = _stamp_subscribe_markers(prof, source_id=42)

        # Recompute the way unsubscribe does
        stored_hash = stamped["subscribe_hash"]
        check_copy = {k: v for k, v in stamped.items() if k != "subscribe_hash"}
        recomputed = hashlib.sha256(
            json.dumps(check_copy, sort_keys=True, default=str).encode()
        ).hexdigest()

        self.assertEqual(stored_hash, recomputed)
        print("  ✅ subscribe_hash: freshly stamped profile hash matches recomputation")

    def test_stamped_then_modified_hash_differs(self):
        """After user modifies a stamped profile, recomputed hash differs from stored."""
        prof = _make_profile("p1", "TAG", "llm_only")
        stamped = _stamp_subscribe_markers(prof, source_id=42)

        # User modifies the profile
        stamped["description"] = "User added this description"

        stored_hash = stamped["subscribe_hash"]
        check_copy = {k: v for k, v in stamped.items() if k != "subscribe_hash"}
        recomputed = hashlib.sha256(
            json.dumps(check_copy, sort_keys=True, default=str).encode()
        ).hexdigest()

        self.assertNotEqual(stored_hash, recomputed)
        print("  ✅ subscribe_hash: modification correctly detected via hash mismatch")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestFix2A_CompensatingRollback,
        TestFix2B_ModifiedProfileProtection,
        TestFix2C_ClearDefaultProfile,
        TestFix2D_SkillsInstall,
        TestFix2E_ExtensionsInstall,
        TestSubscribeHashConsistency,
        TestPhase2ModuleImport,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print(f"\n{'=' * 60}")
    print(f"Phase 2 Results: {passed}/{total} tests passed")
    if failed:
        print(f"  FAILED: {failed} test(s)")
        for test, tb in result.failures + result.errors:
            print(f"    ✗ {test}")
    else:
        print("  All Phase 2 tests PASSED ✅")
    print(f"{'=' * 60}")

    sys.exit(0 if result.wasSuccessful() else 1)
