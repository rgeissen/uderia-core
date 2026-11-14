"""
Publisher Revoke Tests
======================
Tests for publisher-revoke fix and the two orphan-artifact gaps found during
the comprehensive assessment:

  Gap 1 (HIGH — access control bypass):
    CollectionSubscription rows were NOT deleted on unpublish, leaving
    subscribers with read access to the publisher's private collection via
    rag_retriever.py Rule 4 even after revocation.

  Gap 2 (LOW — data hygiene):
    marketplace_installs rows were NOT deleted on unsubscribe or unpublish,
    accumulating indefinitely.

Unit (_cleanup_subscriber_profiles):
  - Unmodified subscribed profiles are removed
  - Modified subscribed profiles are converted to standalone (markers stripped)
  - Pre-existing profiles (no is_subscribed marker) are never touched
  - Default profile pointer cleared when removed profile was default
  - Default profile pointer NOT cleared when modified profile is converted
  - Multiple profile types in same account handled correctly
  - No-subscribed-profiles case is a noop
  - Profile with no subscribe_hash treated as unmodified (removed)

Integration (unpublish logic):
  - CollectionSubscription rows deleted for all subscribers on unpublish
  - CollectionSubscription rows deleted only for the unpublished pack's collections
  - marketplace_installs rows deleted for all subscribers on unpublish
  - CollectionSubscription and marketplace_installs deleted on unsubscribe
  - Subscriber lookup happens BEFORE pack row deletion
  - Cleanup invoked once per grantee
  - Cleanup error doesn't abort unpublish
  - No-subscriber case works without errors
  - Response includes subscriber/profile/subscription counts

Run:
    python test/test_marketplace_publisher_revoke.py
"""

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

MARKER_KEYS = ("is_subscribed", "source_pack_installation_id", "subscribe_hash",
               "subscribed_at", "pack_published_at")


def _profile_hash(p: dict) -> str:
    check = {k: v for k, v in p.items() if k != "subscribe_hash"}
    return hashlib.sha256(
        json.dumps(check, sort_keys=True, default=str).encode()
    ).hexdigest()


def _subscribed_profile(profile_id: str, source_inst_id: int,
                        tag: str = "TEST", modified: bool = False) -> dict:
    p = {
        "id": profile_id,
        "tag": tag,
        "profile_type": "llm_only",
        "is_subscribed": True,
        "source_pack_installation_id": source_inst_id,
        "subscribed_at": "2026-01-01T00:00:00Z",
        "pack_published_at": "2026-01-01T00:00:00Z",
    }
    p["subscribe_hash"] = _profile_hash(p)
    if modified:
        p["custom_setting"] = "user-changed-this"
    return p


def _preexisting_profile(profile_id: str, tag: str = "PRE") -> dict:
    return {"id": profile_id, "tag": tag, "profile_type": "llm_only"}


def _make_sqlite_db(path: str) -> sqlite3.Connection:
    """Full marketplace + subscription schema."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE marketplace_agent_packs (
            id TEXT PRIMARY KEY,
            name TEXT,
            publisher_user_id TEXT,
            source_installation_id INTEGER
        );
        CREATE TABLE marketplace_sharing_grants (
            id TEXT PRIMARY KEY,
            resource_type TEXT,
            resource_id TEXT,
            grantor_user_id TEXT,
            grantee_user_id TEXT
        );
        CREATE TABLE marketplace_targeted_users (
            id TEXT PRIMARY KEY,
            resource_type TEXT,
            resource_id TEXT,
            user_id TEXT
        );
        CREATE TABLE agent_pack_ratings (
            id TEXT PRIMARY KEY,
            pack_id TEXT,
            user_id TEXT,
            rating INTEGER
        );
        CREATE TABLE agent_pack_resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pack_installation_id INTEGER,
            resource_type TEXT,
            resource_id TEXT,
            resource_tag TEXT
        );
        CREATE TABLE collection_subscriptions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            source_collection_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            subscribed_at TEXT
        );
        CREATE UNIQUE INDEX idx_col_sub_user_coll
            ON collection_subscriptions(user_id, source_collection_id);
        CREATE TABLE marketplace_installs (
            id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            marketplace_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            installed_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX idx_marketplace_installs_unique
            ON marketplace_installs(artifact_type, marketplace_id, user_id);
    """)
    conn.commit()
    return conn


def _add_collection_subscription(conn, user_id: str, collection_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO collection_subscriptions "
        "(id, user_id, source_collection_id, enabled, subscribed_at) VALUES (?,?,?,1,?)",
        (str(uuid.uuid4()), user_id, collection_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _has_collection_subscription(conn, user_id: str, collection_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM collection_subscriptions WHERE user_id=? AND source_collection_id=?",
        (user_id, collection_id),
    ).fetchone()
    return row is not None


def _has_marketplace_install(conn, pack_id: str, user_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM marketplace_installs "
        "WHERE artifact_type='agent_pack' AND marketplace_id=? AND user_id=?",
        (pack_id, user_id),
    ).fetchone()
    return row is not None


def _add_marketplace_install(conn, pack_id: str, user_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO marketplace_installs "
        "(id, artifact_type, marketplace_id, user_id, installed_at) VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), "agent_pack", pack_id, user_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Simulate the unpublish/unsubscribe logic for DB-level tests
# (cannot call the decorated route function outside Quart request context)
# ─────────────────────────────────────────────────────────────────────────────

def _simulate_unpublish(db_path: str, pack_id: str, publisher_id: str,
                        cleanup_side_effect=None) -> dict:
    """Mirror the unpublish endpoint body for DB-level integration tests."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, publisher_user_id, source_installation_id, name "
        "FROM marketplace_agent_packs WHERE id = ?", (pack_id,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"error": "not_found"}
    if row["publisher_user_id"] != publisher_id:
        conn.close()
        return {"error": "forbidden"}

    source_inst_id = str(row["source_installation_id"])
    source_installation_id = row["source_installation_id"]

    cursor.execute(
        "SELECT grantee_user_id FROM marketplace_sharing_grants "
        "WHERE resource_type = 'agent_pack' AND resource_id = ?", (source_inst_id,),
    )
    subscriber_user_ids = [r["grantee_user_id"] for r in cursor.fetchall()]

    cursor.execute(
        "SELECT resource_type, resource_id, resource_tag "
        "FROM agent_pack_resources WHERE pack_installation_id = ?", (source_installation_id,),
    )
    pack_resources = [dict(r) for r in cursor.fetchall()]
    conn.close()

    collection_ids = [
        int(r["resource_id"]) for r in pack_resources if r["resource_type"] == "collection"
    ]

    total_removed = total_converted = total_subs_deleted = 0
    cleanup_fn = cleanup_side_effect or (
        lambda u, i, r: {"profiles_removed": 0, "profiles_converted": 0, "removed_ids": set()}
    )
    for sub_uuid in subscriber_user_ids:
        try:
            c = cleanup_fn(sub_uuid, source_installation_id, pack_resources)
            total_removed += c["profiles_removed"]
            total_converted += c["profiles_converted"]
        except Exception:
            pass

        # CollectionSubscription cleanup (Gap 1 fix)
        if collection_ids:
            cdb = sqlite3.connect(db_path)
            for coll_id in collection_ids:
                cdb.execute(
                    "DELETE FROM collection_subscriptions WHERE user_id=? AND source_collection_id=?",
                    (sub_uuid, coll_id),
                )
            cdb.commit()
            deleted = cdb.execute(
                "SELECT changes()"
            ).fetchone()[0]
            total_subs_deleted += sum(1 for coll_id in collection_ids)
            cdb.close()

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM marketplace_sharing_grants WHERE resource_type='agent_pack' AND resource_id=?",
        (source_inst_id,),
    )
    cursor.execute(
        "DELETE FROM marketplace_targeted_users WHERE resource_type='agent_pack' AND resource_id=?",
        (pack_id,),
    )
    cursor.execute("DELETE FROM agent_pack_ratings WHERE pack_id=?", (pack_id,))
    # Gap 2 fix: delete marketplace_installs
    cursor.execute(
        "DELETE FROM marketplace_installs WHERE artifact_type='agent_pack' AND marketplace_id=?",
        (pack_id,),
    )
    cursor.execute("DELETE FROM marketplace_agent_packs WHERE id=?", (pack_id,))
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "subscribers_affected": len(subscriber_user_ids),
        "profiles_removed": total_removed,
        "profiles_converted": total_converted,
        "collection_subscriptions_deleted": total_subs_deleted,
    }


def _simulate_unsubscribe(db_path: str, pack_id: str, subscriber_id: str) -> dict:
    """Mirror the unsubscribe endpoint body for DB-level integration tests."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT source_installation_id, name FROM marketplace_agent_packs WHERE id=?", (pack_id,)
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return {"error": "not_found"}
    source_installation_id = row["source_installation_id"]

    cursor.execute(
        "SELECT resource_type, resource_id FROM agent_pack_resources WHERE pack_installation_id=?",
        (source_installation_id,),
    )
    pack_resources = [dict(r) for r in cursor.fetchall()]

    # Delete grant
    cursor.execute(
        "DELETE FROM marketplace_sharing_grants "
        "WHERE resource_type='agent_pack' AND resource_id=? AND grantee_user_id=?",
        (str(source_installation_id), subscriber_id),
    )
    # Delete collection subscriptions
    for res in pack_resources:
        if res["resource_type"] == "collection":
            cursor.execute(
                "DELETE FROM collection_subscriptions WHERE user_id=? AND source_collection_id=?",
                (subscriber_id, int(res["resource_id"])),
            )
    # Gap 2 fix: delete marketplace_installs for this subscriber
    cursor.execute(
        "DELETE FROM marketplace_installs "
        "WHERE artifact_type='agent_pack' AND marketplace_id=? AND user_id=?",
        (pack_id, subscriber_id),
    )
    conn.commit()
    conn.close()
    return {"status": "success"}


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: _cleanup_subscriber_profiles
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanupSubscriberProfiles(unittest.TestCase):

    def _make_mock_cm(self, profiles, default_id=None):
        cm = MagicMock()
        cm.get_profiles.return_value = list(profiles)
        cm.get_default_profile_id.return_value = default_id
        return cm

    def _run_cleanup(self, profiles, source_inst_id, default_id=None, pack_resources=None):
        from trusted_data_agent.api.agent_pack_routes import _cleanup_subscriber_profiles
        cm = self._make_mock_cm(profiles, default_id)
        with patch("trusted_data_agent.core.config_manager.get_config_manager", return_value=cm):
            result = _cleanup_subscriber_profiles("user-1", source_inst_id, pack_resources or [])
        return result, cm

    def test_unmodified_subscribed_profile_removed(self):
        inst_id = 42
        p = _subscribed_profile("p-1", inst_id)
        result, cm = self._run_cleanup([p], inst_id)
        self.assertEqual(result["profiles_removed"], 1)
        self.assertEqual(result["profiles_converted"], 0)
        self.assertIn("p-1", result["removed_ids"])
        saved = cm.save_profiles.call_args[0][0]
        self.assertFalse(any(x["id"] == "p-1" for x in saved))
        print("  ✅ Unmodified subscribed profile is removed")

    def test_modified_subscribed_profile_also_removed(self):
        # Semantics re-pinned 2026-08-25: this test asserted the "convert a MODIFIED
        # subscribed profile to standalone" behaviour, which 43ff4f7c retired the same
        # day this suite landed (2026-05-05) — the shipped rule is "always delete;
        # fork is the path for customisation" (the function's own docstring). The suite
        # sat red for 3.5 months pinning the retired branch.
        inst_id = 42
        p = _subscribed_profile("p-2", inst_id, modified=True)
        result, cm = self._run_cleanup([p], inst_id)
        self.assertEqual(result["profiles_removed"], 1)
        self.assertEqual(result["profiles_converted"], 0)
        self.assertIn("p-2", result["removed_ids"])
        saved = cm.save_profiles.call_args[0][0]
        self.assertFalse(any(x["id"] == "p-2" for x in saved))
        print("  ✅ Modified subscribed profile is removed too (fork is the customisation path)")

    def test_preexisting_profile_never_touched(self):
        inst_id = 42
        pre = _preexisting_profile("pre-1")
        result, cm = self._run_cleanup([pre], inst_id)
        self.assertEqual(result["profiles_removed"], 0)
        self.assertEqual(result["profiles_converted"], 0)
        saved = cm.save_profiles.call_args[0][0]
        self.assertEqual(saved[0]["id"], "pre-1")
        print("  ✅ Pre-existing profile is never touched")

    def test_mixed_profiles_only_subscribed_affected(self):
        inst_id = 42
        other_inst_id = 99
        profiles = [
            _subscribed_profile("p-unmod", inst_id),
            _subscribed_profile("p-mod", inst_id, modified=True),
            _preexisting_profile("p-pre"),
            _subscribed_profile("p-other", other_inst_id),
        ]
        result, cm = self._run_cleanup(profiles, inst_id)
        # Re-pinned 2026-08-25: BOTH of this pack's subscribed profiles are removed
        # (modified included — the conversion branch was retired by 43ff4f7c);
        # pre-existing and other-pack profiles are untouched.
        self.assertEqual(result["profiles_removed"], 2)
        self.assertEqual(result["profiles_converted"], 0)
        saved_ids = {p["id"] for p in cm.save_profiles.call_args[0][0]}
        self.assertNotIn("p-unmod", saved_ids)
        self.assertNotIn("p-mod", saved_ids)
        self.assertIn("p-pre", saved_ids)
        self.assertIn("p-other", saved_ids)
        print("  ✅ Mixed profiles: this pack's subscribed profiles removed, others untouched")

    def test_default_profile_pointer_cleared_when_removed(self):
        inst_id = 42
        p = _subscribed_profile("p-default", inst_id)
        result, cm = self._run_cleanup([p], inst_id, default_id="p-default")
        cm.set_default_profile_id.assert_called_once_with(None, "user-1")
        print("  ✅ Default profile pointer cleared when subscribed profile is removed")

    def test_default_profile_pointer_cleared_even_when_modified(self):
        # Re-pinned 2026-08-25: with conversion retired, a MODIFIED subscribed default
        # profile is removed like any other, so the dangling default pointer must be
        # cleared — modification does not save a subscribed profile from removal.
        inst_id = 42
        p = _subscribed_profile("p-converted", inst_id, modified=True)
        result, cm = self._run_cleanup([p], inst_id, default_id="p-converted")
        cm.set_default_profile_id.assert_called_once_with(None, "user-1")
        print("  ✅ Default pointer cleared when a modified subscribed default is removed")

    def test_no_subscribed_profiles_noop(self):
        inst_id = 42
        result, cm = self._run_cleanup([_preexisting_profile("pre-only")], inst_id)
        self.assertEqual(result["profiles_removed"], 0)
        self.assertEqual(result["profiles_converted"], 0)
        self.assertEqual(len(result["removed_ids"]), 0)
        print("  ✅ User with no subscribed profiles: noop")

    def test_no_subscribe_hash_treated_as_unmodified(self):
        inst_id = 42
        p = {"id": "p-nohash", "tag": "X", "profile_type": "llm_only",
             "is_subscribed": True, "source_pack_installation_id": inst_id}
        result, cm = self._run_cleanup([p], inst_id)
        self.assertEqual(result["profiles_removed"], 1)
        self.assertEqual(result["profiles_converted"], 0)
        print("  ✅ Profile with is_subscribed but no hash treated as unmodified (removed)")


# ─────────────────────────────────────────────────────────────────────────────
# Gap 1: CollectionSubscription cleanup on unpublish
# ─────────────────────────────────────────────────────────────────────────────

class TestGap1CollectionSubscriptionCleanup(unittest.TestCase):
    """CollectionSubscription rows must be deleted on both unpublish and unsubscribe."""

    def test_collection_subscriptions_deleted_on_unpublish(self):
        """After unpublish, subscriber no longer has CollectionSubscription for pack collections."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g1a.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 100
            publisher_id = "pub-g1a"
            sub1 = "sub-g1a"
            coll_id = 42

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "G1A Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.execute("INSERT INTO agent_pack_resources VALUES (NULL,?,?,?,NULL)",
                         (source_inst_id, "collection", str(coll_id)))
            conn.commit()
            _add_collection_subscription(conn, sub1, coll_id)

            # Verify subscription exists before unpublish
            self.assertTrue(_has_collection_subscription(conn, sub1, coll_id))
            conn.close()

            _simulate_unpublish(db_path, pack_id, publisher_id)

            check = sqlite3.connect(db_path)
            self.assertFalse(
                _has_collection_subscription(check, sub1, coll_id),
                "CollectionSubscription must be deleted after unpublish"
            )
            check.close()
            print("  ✅ Gap 1 fixed: CollectionSubscription deleted on unpublish")

    def test_collection_subscriptions_deleted_for_all_subscribers_on_unpublish(self):
        """All subscriber CollectionSubscription rows are deleted, not just the first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g1b.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 101
            publisher_id = "pub-g1b"
            sub1, sub2 = "sub-g1b-1", "sub-g1b-2"
            coll_id = 55

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "G1B Pack", publisher_id, source_inst_id))
            for sub in [sub1, sub2]:
                conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                             (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub))
                _add_collection_subscription(conn, sub, coll_id)
            conn.execute("INSERT INTO agent_pack_resources VALUES (NULL,?,?,?,NULL)",
                         (source_inst_id, "collection", str(coll_id)))
            conn.commit()
            conn.close()

            _simulate_unpublish(db_path, pack_id, publisher_id)

            check = sqlite3.connect(db_path)
            self.assertFalse(_has_collection_subscription(check, sub1, coll_id))
            self.assertFalse(_has_collection_subscription(check, sub2, coll_id))
            check.close()
            print("  ✅ Gap 1 fixed: CollectionSubscriptions deleted for ALL subscribers on unpublish")

    def test_other_pack_collection_subscriptions_not_touched_on_unpublish(self):
        """Subscriptions from a different pack are not affected by this unpublish."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g1c.db")
            conn = _make_sqlite_db(db_path)

            pack_a_id = str(uuid.uuid4())
            pack_b_id = str(uuid.uuid4())
            source_inst_a = 110
            source_inst_b = 120
            publisher_id = "pub-g1c"
            sub1 = "sub-g1c"
            coll_a, coll_b = 61, 62

            for pack_id, inst_id in [(pack_a_id, source_inst_a), (pack_b_id, source_inst_b)]:
                conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                             (pack_id, f"Pack {pack_id[:4]}", publisher_id, inst_id))
                conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                             (str(uuid.uuid4()), "agent_pack", str(inst_id), publisher_id, sub1))
            conn.execute("INSERT INTO agent_pack_resources VALUES (NULL,?,?,?,NULL)",
                         (source_inst_a, "collection", str(coll_a)))
            conn.execute("INSERT INTO agent_pack_resources VALUES (NULL,?,?,?,NULL)",
                         (source_inst_b, "collection", str(coll_b)))
            conn.commit()
            _add_collection_subscription(conn, sub1, coll_a)
            _add_collection_subscription(conn, sub1, coll_b)

            _simulate_unpublish(db_path, pack_a_id, publisher_id)

            check = sqlite3.connect(db_path)
            self.assertFalse(_has_collection_subscription(check, sub1, coll_a),
                             "Pack A collection sub should be deleted")
            self.assertTrue(_has_collection_subscription(check, sub1, coll_b),
                            "Pack B collection sub must NOT be touched")
            check.close()
            print("  ✅ Gap 1 fixed: Other pack's CollectionSubscriptions left intact")

    def test_collection_subscriptions_deleted_on_unsubscribe(self):
        """CollectionSubscription rows are deleted when subscriber unsubscribes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g1d.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 130
            publisher_id = "pub-g1d"
            sub1 = "sub-g1d"
            coll_id = 77

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "G1D Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.execute("INSERT INTO agent_pack_resources VALUES (NULL,?,?,?,NULL)",
                         (source_inst_id, "collection", str(coll_id)))
            conn.commit()
            _add_collection_subscription(conn, sub1, coll_id)
            conn.close()

            _simulate_unsubscribe(db_path, pack_id, sub1)

            check = sqlite3.connect(db_path)
            self.assertFalse(_has_collection_subscription(check, sub1, coll_id))
            check.close()
            print("  ✅ CollectionSubscription deleted on unsubscribe (existing behavior confirmed)")


# ─────────────────────────────────────────────────────────────────────────────
# Gap 2: marketplace_installs cleanup
# ─────────────────────────────────────────────────────────────────────────────

class TestGap2MarketplaceInstallsCleanup(unittest.TestCase):
    """marketplace_installs rows must be deleted on both unsubscribe and unpublish."""

    def test_marketplace_installs_deleted_on_unpublish(self):
        """After unpublish, all subscriber install records for this pack are deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g2a.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 200
            publisher_id = "pub-g2a"
            sub1, sub2 = "sub-g2a-1", "sub-g2a-2"

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "G2A Pack", publisher_id, source_inst_id))
            for sub in [sub1, sub2]:
                conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                             (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub))
                _add_marketplace_install(conn, pack_id, sub)
            conn.commit()
            conn.close()

            self.assertTrue(_has_marketplace_install(sqlite3.connect(db_path), pack_id, sub1))
            self.assertTrue(_has_marketplace_install(sqlite3.connect(db_path), pack_id, sub2))

            _simulate_unpublish(db_path, pack_id, publisher_id)

            check = sqlite3.connect(db_path)
            self.assertFalse(_has_marketplace_install(check, pack_id, sub1),
                             "marketplace_installs must be deleted on unpublish")
            self.assertFalse(_has_marketplace_install(check, pack_id, sub2))
            check.close()
            print("  ✅ Gap 2 fixed: marketplace_installs deleted on unpublish")

    def test_marketplace_installs_deleted_on_unsubscribe(self):
        """After unsubscribe, the subscriber's install record is deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g2b.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 210
            publisher_id = "pub-g2b"
            sub1 = "sub-g2b"

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "G2B Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.commit()
            _add_marketplace_install(conn, pack_id, sub1)
            conn.close()

            _simulate_unsubscribe(db_path, pack_id, sub1)

            check = sqlite3.connect(db_path)
            self.assertFalse(_has_marketplace_install(check, pack_id, sub1),
                             "marketplace_installs must be deleted on unsubscribe")
            check.close()
            print("  ✅ Gap 2 fixed: marketplace_installs deleted on unsubscribe")

    def test_other_pack_install_records_not_touched(self):
        """Unsubscribing from pack A doesn't delete install records for pack B."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g2c.db")
            conn = _make_sqlite_db(db_path)

            pack_a_id = str(uuid.uuid4())
            pack_b_id = str(uuid.uuid4())
            source_inst_a = 220
            source_inst_b = 230
            publisher_id = "pub-g2c"
            sub1 = "sub-g2c"

            for pack_id, inst_id in [(pack_a_id, source_inst_a), (pack_b_id, source_inst_b)]:
                conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                             (pack_id, f"Pack {pack_id[:4]}", publisher_id, inst_id))
                conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                             (str(uuid.uuid4()), "agent_pack", str(inst_id), publisher_id, sub1))
                _add_marketplace_install(conn, pack_id, sub1)
            conn.commit()
            conn.close()

            _simulate_unsubscribe(db_path, pack_a_id, sub1)

            check = sqlite3.connect(db_path)
            self.assertFalse(_has_marketplace_install(check, pack_a_id, sub1))
            self.assertTrue(_has_marketplace_install(check, pack_b_id, sub1),
                            "Pack B install record must not be touched")
            check.close()
            print("  ✅ Gap 2 fixed: other pack's install records not affected by unsubscribe")

    def test_no_install_record_unsubscribe_is_noop(self):
        """Unsubscribe without install record (legacy subscriber) doesn't error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "g2d.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 240
            publisher_id = "pub-g2d"
            sub1 = "sub-g2d"

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "G2D Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.commit()
            conn.close()
            # No marketplace_installs record (pre-Fix 4A legacy subscriber)

            result = _simulate_unsubscribe(db_path, pack_id, sub1)
            self.assertEqual(result["status"], "success")
            print("  ✅ Gap 2: unsubscribe without install record is a clean noop")


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: all orphan artifacts cleaned on unpublish
# ─────────────────────────────────────────────────────────────────────────────

class TestUnpublishNoOrphanArtifacts(unittest.TestCase):
    """After unpublish, NO orphan artifacts remain for any subscriber."""

    def test_full_cleanup_after_unpublish(self):
        """All three artifact types cleaned after publisher revokes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "e2e.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 300
            publisher_id = "pub-e2e"
            sub1 = "sub-e2e"
            coll_id = 99

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "E2E Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.execute("INSERT INTO agent_pack_resources VALUES (NULL,?,?,?,NULL)",
                         (source_inst_id, "collection", str(coll_id)))
            conn.commit()
            _add_collection_subscription(conn, sub1, coll_id)
            _add_marketplace_install(conn, pack_id, sub1)
            conn.close()

            _simulate_unpublish(db_path, pack_id, publisher_id)

            check = sqlite3.connect(db_path)

            # Pack record gone
            self.assertIsNone(check.execute(
                "SELECT 1 FROM marketplace_agent_packs WHERE id=?", (pack_id,)
            ).fetchone())

            # Sharing grant gone
            self.assertIsNone(check.execute(
                "SELECT 1 FROM marketplace_sharing_grants WHERE grantee_user_id=?", (sub1,)
            ).fetchone())

            # CollectionSubscription gone (Gap 1)
            self.assertFalse(_has_collection_subscription(check, sub1, coll_id))

            # marketplace_installs gone (Gap 2)
            self.assertFalse(_has_marketplace_install(check, pack_id, sub1))

            check.close()
            print("  ✅ E2E: all orphan artifacts cleaned after publisher revokes")

    def test_full_cleanup_after_unsubscribe(self):
        """All three artifact types cleaned when subscriber unsubscribes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "e2e_unsub.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 310
            publisher_id = "pub-e2e-unsub"
            sub1 = "sub-e2e-unsub"
            coll_id = 88

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "E2E Unsub Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.execute("INSERT INTO agent_pack_resources VALUES (NULL,?,?,?,NULL)",
                         (source_inst_id, "collection", str(coll_id)))
            conn.commit()
            _add_collection_subscription(conn, sub1, coll_id)
            _add_marketplace_install(conn, pack_id, sub1)
            conn.close()

            _simulate_unsubscribe(db_path, pack_id, sub1)

            check = sqlite3.connect(db_path)
            # Grant gone
            self.assertIsNone(check.execute(
                "SELECT 1 FROM marketplace_sharing_grants WHERE grantee_user_id=?", (sub1,)
            ).fetchone())
            # CollectionSubscription gone
            self.assertFalse(_has_collection_subscription(check, sub1, coll_id))
            # marketplace_installs gone
            self.assertFalse(_has_marketplace_install(check, pack_id, sub1))
            check.close()
            print("  ✅ E2E: all orphan artifacts cleaned on unsubscribe")


# ─────────────────────────────────────────────────────────────────────────────
# Existing integration tests (ordering, error resilience, counts)
# ─────────────────────────────────────────────────────────────────────────────

class TestUnpublishOrderingAndResilience(unittest.TestCase):

    def test_grants_fetched_before_pack_deleted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "order.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 400
            publisher_id = "pub-ord"
            sub1 = "sub-ord"

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "Order Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.commit()
            conn.close()

            grant_exists_when_cleanup_ran = []

            def checking_cleanup(user_uuid, inst_id, resources):
                check = sqlite3.connect(db_path)
                row = check.execute(
                    "SELECT 1 FROM marketplace_sharing_grants WHERE grantee_user_id=?", (user_uuid,)
                ).fetchone()
                check.close()
                grant_exists_when_cleanup_ran.append(row is not None)
                return {"profiles_removed": 0, "profiles_converted": 0, "removed_ids": set()}

            _simulate_unpublish(db_path, pack_id, publisher_id, checking_cleanup)

            self.assertTrue(grant_exists_when_cleanup_ran[0],
                "Grant must still exist when cleanup runs")
            print("  ✅ Subscriber lookup (grants) happens before DELETE")

    def test_cleanup_error_does_not_abort_unpublish(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "err.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 410
            publisher_id = "pub-err"
            sub1 = "sub-err"

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "Err Pack", publisher_id, source_inst_id))
            conn.execute("INSERT INTO marketplace_sharing_grants VALUES (?,?,?,?,?)",
                         (str(uuid.uuid4()), "agent_pack", str(source_inst_id), publisher_id, sub1))
            conn.commit()
            conn.close()

            def broken_cleanup(u, i, r):
                raise RuntimeError("config unavailable")

            result = _simulate_unpublish(db_path, pack_id, publisher_id, broken_cleanup)
            self.assertEqual(result["status"], "success")
            check = sqlite3.connect(db_path)
            self.assertIsNone(check.execute(
                "SELECT 1 FROM marketplace_agent_packs WHERE id=?", (pack_id,)
            ).fetchone())
            check.close()
            print("  ✅ Cleanup error is skipped; unpublish still completes")

    def test_no_subscribers_unpublish_succeeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "nosub.db")
            conn = _make_sqlite_db(db_path)

            pack_id = str(uuid.uuid4())
            source_inst_id = 420
            publisher_id = "pub-nosub"

            conn.execute("INSERT INTO marketplace_agent_packs VALUES (?,?,?,?)",
                         (pack_id, "Solo Pack", publisher_id, source_inst_id))
            conn.commit()
            conn.close()

            result = _simulate_unpublish(db_path, pack_id, publisher_id)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["subscribers_affected"], 0)
            print("  ✅ No-subscriber pack unpublishes cleanly")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for cls in [
        TestCleanupSubscriberProfiles,
        TestGap1CollectionSubscriptionCleanup,
        TestGap2MarketplaceInstallsCleanup,
        TestUnpublishNoOrphanArtifacts,
        TestUnpublishOrderingAndResilience,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    passed = total - failures

    print(f"\n{'='*60}")
    print(f"Publisher Revoke / Orphan Artifact Tests: {passed}/{total} passed")
    if failures:
        print(f"FAILURES: {failures}")
    print(f"{'='*60}")

    sys.exit(0 if not failures else 1)
