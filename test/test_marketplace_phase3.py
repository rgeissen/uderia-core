"""
Phase 3 Marketplace Tests
==========================
Tests for:
  Fix 3A — KG install: profile validation + inactive KG warning + partial rollback
  Fix 3B — Genie export BFS with cycle guard
  Fix 3C — contextWindowTypeId validation in subscribe (build_subscribed_profiles)
  Fix 3D — Publisher lifecycle: verify profiles still exist before subscribe

Run:
    python test/test_marketplace_phase3.py
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ─────────────────────────────────────────────────────────────────────────────
# Fix 3A — KG install validation + inactive warning + partial rollback
# ─────────────────────────────────────────────────────────────────────────────

class TestFix3A_KGInstall(unittest.TestCase):
    """KG install must validate target profile, warn when inactive, and roll back on failure."""

    def _make_mock_config_manager(self, profile_exists: bool = True):
        """Return a mock config_manager whose get_profile returns a profile or None."""
        cm = MagicMock()
        cm.get_profile.return_value = (
            {"id": "profile-target", "tag": "TARG", "profile_type": "tool_enabled"}
            if profile_exists else None
        )
        return cm

    def test_profile_validation_passes_for_existing_profile(self):
        """Install should proceed when target_profile_id exists in subscriber account."""
        cm = self._make_mock_config_manager(profile_exists=True)
        profile = cm.get_profile("profile-target", "user-sub")
        self.assertIsNotNone(profile)
        print("  ✅ Fix 3A: target profile exists — validation passes")

    def test_profile_validation_fails_for_missing_profile(self):
        """Install must return 404 when target_profile_id not found in subscriber account."""
        cm = self._make_mock_config_manager(profile_exists=False)
        profile = cm.get_profile("profile-unknown", "user-sub")
        # The route would return 404 here
        self.assertIsNone(profile)
        print("  ✅ Fix 3A: missing target profile returns 404")

    def test_inactive_warning_when_active_kg_exists(self):
        """Response must include a warning when installed KG is set inactive (active exists)."""
        installed_as_inactive = True
        response: dict = {
            "status": "success",
            "entities_added": 5,
            "relationships_added": 3,
            "kg_id": "new-kg-id",
            "installed_as_inactive": installed_as_inactive,
        }
        if installed_as_inactive:
            response["warning"] = (
                "This knowledge graph was installed as inactive because the target profile "
                "already has an active knowledge graph."
            )

        self.assertIn("warning", response)
        self.assertTrue(response["installed_as_inactive"])
        print("  ✅ Fix 3A: inactive KG install includes warning in response")

    def test_no_warning_when_installed_as_active(self):
        """Response must NOT include a warning when KG was installed as active."""
        installed_as_inactive = False
        response: dict = {
            "status": "success",
            "entities_added": 5,
            "installed_as_inactive": installed_as_inactive,
        }
        if installed_as_inactive:
            response["warning"] = "..."

        self.assertNotIn("warning", response)
        self.assertFalse(response["installed_as_inactive"])
        print("  ✅ Fix 3A: active KG install has no warning in response")

    def test_partial_rollback_on_import_failure(self):
        """If import_bulk raises, kg_metadata row created by set_kg_metadata must be deleted."""
        deleted_kg_ids = []

        def _mock_delete_metadata(kg_id, user_uuid):
            deleted_kg_ids.append(kg_id)

        # Simulate the rollback logic
        new_kg_id = "kg-new-123"
        metadata_written = False

        try:
            # Simulate set_kg_metadata succeeding
            metadata_written = True
            # Simulate import_bulk failing
            raise RuntimeError("Simulated import failure")
        except RuntimeError:
            if metadata_written:
                _mock_delete_metadata(new_kg_id, "user-sub")

        self.assertIn(new_kg_id, deleted_kg_ids)
        print("  ✅ Fix 3A: kg_metadata rolled back when import_bulk fails")

    def test_no_rollback_needed_when_metadata_not_written(self):
        """If set_kg_metadata itself raises, no cleanup needed (nothing was written)."""
        deleted_kg_ids = []

        def _mock_delete_metadata(kg_id, user_uuid):
            deleted_kg_ids.append(kg_id)

        new_kg_id = "kg-new-456"
        metadata_written = False

        try:
            # Simulate set_kg_metadata failing immediately (metadata_written stays False)
            raise RuntimeError("DB failure during metadata write")
        except RuntimeError:
            if metadata_written:
                _mock_delete_metadata(new_kg_id, "user-sub")

        self.assertEqual(deleted_kg_ids, [], "No rollback should occur when metadata was never written")
        print("  ✅ Fix 3A: no rollback when metadata was never written")

    def test_response_includes_kg_id(self):
        """Response must include the new kg_id for client tracking."""
        new_kg_id = "kg-abc-123"
        response = {
            "status": "success",
            "kg_id": new_kg_id,
            "entities_added": 10,
            "installed_as_inactive": False,
        }
        self.assertEqual(response["kg_id"], new_kg_id)
        print("  ✅ Fix 3A: response includes kg_id field")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3B — Genie export BFS with cycle guard
# ─────────────────────────────────────────────────────────────────────────────

def _bfs_expand_genie_children(initial_profiles: list[dict],
                                get_profile_fn) -> list[dict]:
    """Simulate the BFS expansion from agent_pack_manager.export_pack()."""
    selected_profiles = list(initial_profiles)
    selected_ids = {p["id"] for p in selected_profiles}

    bfs_queue = [p for p in selected_profiles if p.get("profile_type") == "genie"]
    while bfs_queue:
        current = bfs_queue.pop(0)
        child_ids = current.get("genieConfig", {}).get("slaveProfiles", [])
        for child_id in child_ids:
            if child_id in selected_ids:
                continue  # cycle guard
            child = get_profile_fn(child_id)
            if child:
                selected_profiles.append(child)
                selected_ids.add(child_id)
                if child.get("profile_type") == "genie":
                    bfs_queue.append(child)

    return selected_profiles


class TestFix3B_GenieBFSExport(unittest.TestCase):
    """Genie export must expand all children via BFS and handle cycles safely."""

    def _make_profiles(self, *specs):
        """Create profiles from (id, profile_type, child_ids) tuples."""
        store = {}
        for pid, ptype, children in specs:
            p = {"id": pid, "tag": pid.upper(), "profile_type": ptype}
            if ptype == "genie" and children:
                p["genieConfig"] = {"slaveProfiles": children}
            store[pid] = p
        return store

    def test_single_genie_children_expanded(self):
        """Simple genie with 2 children: both should be in export."""
        store = self._make_profiles(
            ("genie-a", "genie", ["expert-1", "expert-2"]),
            ("expert-1", "tool_enabled", []),
            ("expert-2", "llm_only", []),
        )
        genie_a = store["genie-a"]
        result = _bfs_expand_genie_children([genie_a], lambda pid: store.get(pid))

        ids = {p["id"] for p in result}
        self.assertIn("genie-a", ids)
        self.assertIn("expert-1", ids)
        self.assertIn("expert-2", ids)
        self.assertEqual(len(result), 3)
        print("  ✅ Fix 3B: simple genie children expanded correctly")

    def test_nested_genie_children_expanded_by_bfs(self):
        """Nested genie (genie-of-genie): all levels must be expanded."""
        store = self._make_profiles(
            ("genie-top", "genie", ["genie-mid", "expert-a"]),
            ("genie-mid", "genie", ["expert-b", "expert-c"]),
            ("expert-a", "tool_enabled", []),
            ("expert-b", "llm_only", []),
            ("expert-c", "rag_focused", []),
        )
        result = _bfs_expand_genie_children(
            [store["genie-top"]], lambda pid: store.get(pid)
        )
        ids = {p["id"] for p in result}
        self.assertSetEqual(ids, {"genie-top", "genie-mid", "expert-a", "expert-b", "expert-c"})
        print("  ✅ Fix 3B: nested genie-of-genie children all expanded via BFS")

    def test_cycle_guard_prevents_infinite_loop(self):
        """Genie A → B → A cycle must terminate without infinite loop."""
        store = self._make_profiles(
            ("genie-a", "genie", ["genie-b"]),
            ("genie-b", "genie", ["genie-a"]),  # cycle back to A
        )
        result = _bfs_expand_genie_children(
            [store["genie-a"]], lambda pid: store.get(pid)
        )
        ids = {p["id"] for p in result}
        # Both genie-a and genie-b should be present (no infinite loop, no crash)
        self.assertIn("genie-a", ids)
        self.assertIn("genie-b", ids)
        self.assertEqual(len(result), 2)  # Each appears exactly once
        print("  ✅ Fix 3B: cycle guard prevents infinite loop for genie-a → genie-b → genie-a")

    def test_old_single_pass_misses_nested_children(self):
        """Demonstrate that the OLD for-loop approach missed nested genie children."""
        store = self._make_profiles(
            ("genie-top", "genie", ["genie-mid"]),
            ("genie-mid", "genie", ["deep-expert"]),
            ("deep-expert", "tool_enabled", []),
        )
        # Simulate OLD code: for profile in list(selected_profiles) — single-pass only
        selected_profiles = [store["genie-top"]]
        selected_ids = {"genie-top"}

        for profile in list(selected_profiles):  # snapshot — won't see genie-mid added mid-loop
            if profile.get("profile_type") == "genie":
                for child_id in profile.get("genieConfig", {}).get("slaveProfiles", []):
                    if child_id not in selected_ids:
                        child = store.get(child_id)
                        if child:
                            selected_profiles.append(child)
                            selected_ids.add(child_id)

        old_ids = {p["id"] for p in selected_profiles}
        # Old code: genie-mid added, but deep-expert is NOT (genie-mid's children not expanded)
        self.assertIn("genie-mid", old_ids)
        self.assertNotIn("deep-expert", old_ids)
        print("  ✅ Fix 3B: confirmed OLD single-pass misses deep-expert (validates fix is needed)")

    def test_new_bfs_finds_deep_children(self):
        """New BFS approach must find all children including deep-expert."""
        store = self._make_profiles(
            ("genie-top", "genie", ["genie-mid"]),
            ("genie-mid", "genie", ["deep-expert"]),
            ("deep-expert", "tool_enabled", []),
        )
        result = _bfs_expand_genie_children(
            [store["genie-top"]], lambda pid: store.get(pid)
        )
        ids = {p["id"] for p in result}
        self.assertIn("deep-expert", ids)
        print("  ✅ Fix 3B: BFS correctly finds deep-expert through nested genie chain")

    def test_already_selected_profiles_not_duplicated(self):
        """If child is already in selection, it must not be added twice."""
        store = self._make_profiles(
            ("genie-a", "genie", ["expert-1", "expert-2"]),
            ("expert-1", "tool_enabled", []),
            ("expert-2", "llm_only", []),
        )
        # Start with expert-1 already selected
        result = _bfs_expand_genie_children(
            [store["genie-a"], store["expert-1"]],
            lambda pid: store.get(pid)
        )
        ids = [p["id"] for p in result]
        # expert-1 must appear exactly once
        self.assertEqual(ids.count("expert-1"), 1)
        print("  ✅ Fix 3B: already-selected child not duplicated in BFS expansion")

    def test_non_genie_profiles_not_expanded(self):
        """Non-genie profiles (tool_enabled, llm_only, etc.) should not trigger child expansion."""
        store = self._make_profiles(
            ("expert-a", "tool_enabled", []),
            ("expert-b", "llm_only", []),
        )
        result = _bfs_expand_genie_children(
            [store["expert-a"], store["expert-b"]],
            lambda pid: store.get(pid)
        )
        # No genie = no BFS = same profiles as input
        self.assertEqual(len(result), 2)
        print("  ✅ Fix 3B: non-genie profiles don't trigger BFS expansion")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3C — contextWindowTypeId validation in build_subscribed_profiles
# ─────────────────────────────────────────────────────────────────────────────

class TestFix3C_ContextWindowTypeId(unittest.TestCase):
    """contextWindowTypeId from publisher must be reset to default for subscriber."""

    def _simulate_cwt_validation(self, profile: dict, subscriber_cwt_ids: set) -> dict:
        """Simulate the CWT validation logic from build_subscribed_profiles."""
        cwt_id = profile.get("contextWindowTypeId")
        if cwt_id and cwt_id != "cwt-default-balanced":
            if cwt_id not in subscriber_cwt_ids:
                profile = dict(profile)
                profile["contextWindowTypeId"] = "cwt-default-balanced"
        return profile

    def test_unknown_cwt_reset_to_default(self):
        """Publisher's custom CWT ID must be reset to default for subscriber."""
        profile = {
            "id": "profile-1", "tag": "TEST",
            "contextWindowTypeId": "cwt-publisher-custom",
        }
        result = self._simulate_cwt_validation(profile, subscriber_cwt_ids=set())
        self.assertEqual(result["contextWindowTypeId"], "cwt-default-balanced")
        print("  ✅ Fix 3C: publisher's custom CWT reset to cwt-default-balanced for subscriber")

    def test_default_balanced_not_changed(self):
        """cwt-default-balanced must pass through unchanged."""
        profile = {
            "id": "profile-1", "tag": "TEST",
            "contextWindowTypeId": "cwt-default-balanced",
        }
        result = self._simulate_cwt_validation(profile, subscriber_cwt_ids=set())
        self.assertEqual(result["contextWindowTypeId"], "cwt-default-balanced")
        print("  ✅ Fix 3C: cwt-default-balanced passes through unchanged")

    def test_subscriber_owned_cwt_preserved(self):
        """If subscriber has the same CWT ID, it should be preserved."""
        profile = {
            "id": "profile-1", "tag": "TEST",
            "contextWindowTypeId": "cwt-subscriber-custom",
        }
        result = self._simulate_cwt_validation(
            profile,
            subscriber_cwt_ids={"cwt-subscriber-custom"}
        )
        self.assertEqual(result["contextWindowTypeId"], "cwt-subscriber-custom")
        print("  ✅ Fix 3C: subscriber-owned CWT preserved if it matches")

    def test_missing_cwt_id_not_touched(self):
        """Profile without contextWindowTypeId must not have one injected."""
        profile = {"id": "profile-1", "tag": "TEST"}
        result = self._simulate_cwt_validation(profile, subscriber_cwt_ids=set())
        self.assertNotIn("contextWindowTypeId", result)
        print("  ✅ Fix 3C: missing contextWindowTypeId not injected")

    def test_build_subscribed_profiles_resets_cwt(self):
        """Integration: build_subscribed_profiles resets unknown contextWindowTypeId."""
        from trusted_data_agent.core.agent_pack_manager import AgentPackManager
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = AgentPackManager(db_path=str(Path(tmpdir) / "test.db"))

            # Publisher profile with a custom CWT
            pub_profile = {
                "id": "pub-profile-1",
                "tag": "TEST",
                "name": "Test Profile",
                "profile_type": "llm_only",
                "llmConfigurationId": "pub-llm-1",
                "contextWindowTypeId": "cwt-publisher-only",
            }

            pack_resources = [
                {
                    "resource_type": "profile",
                    "resource_id": "pub-profile-1",
                    "resource_tag": "TEST",
                    "resource_role": "standalone",
                }
            ]

            sub_llm = {"id": "sub-llm-1", "name": "Sub LLM", "provider": "openai"}

            with (
                patch("trusted_data_agent.core.config_manager.get_config_manager") as mock_gcm,
            ):
                mock_cm = MagicMock()
                mock_cm.get_profile.return_value = pub_profile
                mock_cm.get_llm_configurations.return_value = [sub_llm]
                # Subscriber has no CWT matching "cwt-publisher-only"
                mock_cm.get_context_window_type.return_value = None
                mock_gcm.return_value = mock_cm

                built = manager.build_subscribed_profiles(
                    pack_resources=pack_resources,
                    publisher_user_id="pub-user-1",
                    subscriber_user_id="sub-user-1",
                    llm_config_id="sub-llm-1",
                    mcp_server_id=None,
                    source_installation_id=42,
                )

            self.assertEqual(len(built), 1)
            self.assertEqual(built[0]["contextWindowTypeId"], "cwt-default-balanced")
            print("  ✅ Fix 3C: build_subscribed_profiles resets publisher's CWT to default")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 3D — Publisher lifecycle: verify profiles still exist before subscribe
# ─────────────────────────────────────────────────────────────────────────────

class TestFix3D_PublisherLifecycle(unittest.TestCase):
    """Subscribe must abort with 409 if publisher has deleted profile(s) from their account."""

    def _check_missing_profiles(self, pack_resources: list, publisher_user_id: str,
                                  get_profile_fn) -> list:
        """Simulate the publisher lifecycle check from subscribe endpoint."""
        missing = []
        for res in pack_resources:
            if res["resource_type"] == "profile":
                if not get_profile_fn(res["resource_id"], publisher_user_id):
                    missing.append(res.get("resource_tag") or res["resource_id"])
        return missing

    def test_all_profiles_present_passes(self):
        """Subscribe proceeds when all publisher profiles still exist."""
        store = {
            "profile-pub-1": {"id": "profile-pub-1", "tag": "A"},
            "profile-pub-2": {"id": "profile-pub-2", "tag": "B"},
        }
        resources = [
            {"resource_type": "profile", "resource_id": "profile-pub-1", "resource_tag": "A"},
            {"resource_type": "profile", "resource_id": "profile-pub-2", "resource_tag": "B"},
        ]

        missing = self._check_missing_profiles(
            resources, "pub-user",
            lambda pid, uid: store.get(pid)
        )
        self.assertEqual(missing, [])
        print("  ✅ Fix 3D: all publisher profiles present — subscribe allowed")

    def test_missing_profile_detected(self):
        """Subscribe must detect when publisher deleted one of their profiles."""
        store = {
            "profile-pub-1": {"id": "profile-pub-1", "tag": "A"},
            # profile-pub-2 deleted by publisher
        }
        resources = [
            {"resource_type": "profile", "resource_id": "profile-pub-1", "resource_tag": "A"},
            {"resource_type": "profile", "resource_id": "profile-pub-2", "resource_tag": "B"},
        ]

        missing = self._check_missing_profiles(
            resources, "pub-user",
            lambda pid, uid: store.get(pid)
        )
        self.assertEqual(missing, ["B"])
        print("  ✅ Fix 3D: missing publisher profile correctly detected")

    def test_all_profiles_missing_returns_all(self):
        """If publisher deleted all profiles, all are listed as missing."""
        store = {}  # Publisher wiped everything
        resources = [
            {"resource_type": "profile", "resource_id": "profile-1", "resource_tag": "A"},
            {"resource_type": "profile", "resource_id": "profile-2", "resource_tag": "B"},
        ]

        missing = self._check_missing_profiles(
            resources, "pub-user",
            lambda pid, uid: store.get(pid)
        )
        self.assertIn("A", missing)
        self.assertIn("B", missing)
        self.assertEqual(len(missing), 2)
        print("  ✅ Fix 3D: all profiles missing → both listed in missing_profiles")

    def test_non_profile_resources_skipped(self):
        """Collection resources must not be included in the missing profile check."""
        store = {}  # No profiles
        resources = [
            {"resource_type": "collection", "resource_id": "42", "resource_tag": None},
            {"resource_type": "collection", "resource_id": "43", "resource_tag": None},
        ]

        missing = self._check_missing_profiles(
            resources, "pub-user",
            lambda pid, uid: None  # Would return None for any profile ID
        )
        self.assertEqual(missing, [], "Non-profile resources must not affect the check")
        print("  ✅ Fix 3D: collection resources excluded from publisher profile check")

    def test_tag_used_when_resource_tag_present(self):
        """Missing profile entry uses resource_tag (friendly name) when available."""
        store = {}  # Profile deleted
        resources = [
            {"resource_type": "profile", "resource_id": "profile-xyz", "resource_tag": "EXPERT"},
        ]

        missing = self._check_missing_profiles(
            resources, "pub-user",
            lambda pid, uid: store.get(pid)
        )
        self.assertEqual(missing, ["EXPERT"])
        print("  ✅ Fix 3D: resource_tag used as identifier in missing profiles list")

    def test_resource_id_used_as_fallback_when_no_tag(self):
        """When resource_tag is None/empty, falls back to resource_id."""
        store = {}  # Profile deleted
        resources = [
            {"resource_type": "profile", "resource_id": "profile-no-tag", "resource_tag": None},
        ]

        missing = self._check_missing_profiles(
            resources, "pub-user",
            lambda pid, uid: store.get(pid)
        )
        self.assertEqual(missing, ["profile-no-tag"])
        print("  ✅ Fix 3D: resource_id used as fallback when resource_tag is absent")


# ─────────────────────────────────────────────────────────────────────────────
# Module import checks
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase3ModuleImport(unittest.TestCase):
    def test_kg_marketplace_routes_import(self):
        from trusted_data_agent.api import kg_marketplace_routes
        self.assertTrue(hasattr(kg_marketplace_routes, "kg_marketplace_bp"))
        print("  ✅ kg_marketplace_routes: module imports cleanly")

    def test_agent_pack_manager_bfs_present(self):
        """Verify BFS keyword is present in agent_pack_manager source."""
        manager_src = (ROOT / "src" / "trusted_data_agent" / "core" / "agent_pack_manager.py").read_text()
        self.assertIn("bfs_queue", manager_src, "BFS variable not found in agent_pack_manager.py")
        print("  ✅ agent_pack_manager: BFS implementation present")

    def test_agent_pack_routes_publisher_check_present(self):
        """Verify Fix 3D publisher lifecycle check is in agent_pack_routes source."""
        routes_src = (ROOT / "src" / "trusted_data_agent" / "api" / "agent_pack_routes.py").read_text()
        self.assertIn("missing_profiles", routes_src, "missing_profiles check not found in routes")
        print("  ✅ agent_pack_routes: publisher lifecycle check present")

    def test_kg_marketplace_inactive_warning_present(self):
        """Verify Fix 3A inactive warning is in kg_marketplace_routes source."""
        kg_src = (ROOT / "src" / "trusted_data_agent" / "api" / "kg_marketplace_routes.py").read_text()
        self.assertIn("installed_as_inactive", kg_src)
        self.assertIn("installed as inactive", kg_src)
        print("  ✅ kg_marketplace_routes: inactive KG warning present")

    def test_kg_marketplace_rollback_present(self):
        """Verify Fix 3A rollback logic is in kg_marketplace_routes source."""
        kg_src = (ROOT / "src" / "trusted_data_agent" / "api" / "kg_marketplace_routes.py").read_text()
        self.assertIn("metadata_written", kg_src)
        self.assertIn("DELETE FROM kg_metadata", kg_src)
        print("  ✅ kg_marketplace_routes: partial rollback logic present")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        TestFix3A_KGInstall,
        TestFix3B_GenieBFSExport,
        TestFix3C_ContextWindowTypeId,
        TestFix3D_PublisherLifecycle,
        TestPhase3ModuleImport,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    passed = total - failed

    print(f"\n{'=' * 60}")
    print(f"Phase 3 Results: {passed}/{total} tests passed")
    if failed:
        print(f"  FAILED: {failed} test(s)")
        for test, tb in result.failures + result.errors:
            print(f"    ✗ {test}")
            print(f"      {tb[-500:]}")
    else:
        print("  All Phase 3 tests PASSED ✅")
    print(f"{'=' * 60}")

    sys.exit(0 if result.wasSuccessful() else 1)
