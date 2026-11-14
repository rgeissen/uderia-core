"""
Module tests for the R1.0 session-access consolidation
(src/trusted_data_agent/core/session_manager.py public API +
 src/trusted_data_agent/core/artifact_detectors.py reroute).

Verifies the backend-agnostic public read/write API and the rerouted artifact
detectors against REAL session files, isolated to a temp directory (no network,
no live app, no auth DB dependency — config_manager is stubbed).

Run:
    PYTHONPATH=src python test/test_session_access.py
"""
import os
import sys
import json
import shutil
import asyncio
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import session_manager as sm  # noqa: E402
from trusted_data_agent.core import artifact_detectors as ad  # noqa: E402
from trusted_data_agent.core.session_backends import (  # noqa: E402
    get_session_backend,
    reset_session_backend,
)

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def run(coro):
    return _loop.run_until_complete(coro)


# ─── results ──────────────────────────────────────────────────────────────────
_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


# ─── isolation: point session storage at a temp dir ───────────────────────────
_TMP = tempfile.mkdtemp(prefix="tda_test_sessions_")


def _isolate():
    """Initialize a fresh, isolated session backend.

    Honors a pre-set ``TDA_SESSION_BACKEND=postgres`` (the postgres harness sets
    it + ``TDA_SESSION_PG_DSN`` before importing this module); otherwise defaults
    to the file backend pointed at a temp dir.
    """
    if os.environ.get("TDA_SESSION_BACKEND", "file") != "postgres":
        os.environ["TDA_SESSION_BACKEND"] = "file"
        os.environ["TDA_SESSIONS_DIR"] = _TMP
    reset_session_backend(None)            # rebuild backend from env on next access
    run(sm._init_session_index())          # create the index/table on the fresh backend


# ─── stub config_manager so detectors don't touch the auth DB ─────────────────
class _StubConfigMgr:
    def get_profiles(self, user_uuid):
        return []  # no profile-config based detection; exercises direct + workflow paths


def _stub_config_manager():
    from trusted_data_agent.core import config_manager
    config_manager.get_config_manager = lambda: _StubConfigMgr()


# ─── session body factory ─────────────────────────────────────────────────────
def make_session(sid, user, *, name="S", rag_collection_id=None, profile_id=None,
                 profile_tags_used=None, archived=False, workflow=None,
                 input_tokens=10, output_tokens=5, provider="p", model="m",
                 profile_type="tool_enabled"):
    return {
        "id": sid,
        "user_uuid": user,
        "name": name,
        "created_at": "2026-06-01T00:00:00",
        "last_updated": "2026-06-01T00:00:00",
        "rag_collection_id": rag_collection_id,
        "profile_id": profile_id,
        "profile_tag": None,
        "profile_tags_used": profile_tags_used or [],
        "archived": archived,
        "is_archived": archived,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "provider": provider,
        "model": model,
        "profile_type": profile_type,
        "models_used": [],
        "genie_metadata": {},
        "last_turn_data": {"workflow_history": workflow or []},
        "session_history": [],
        "chat_object": [],
    }


async def _seed():
    """Write a deterministic fixture set, then rebuild the index."""
    # user A: 2 active (one direct-collection, one workflow-collection), 1 archived
    await sm.save_session("userA", "a1", make_session(
        "a1", "userA", name="A-direct", rag_collection_id=42, profile_id="pid1",
        profile_tags_used=["TAG1"]))
    await sm.save_session("userA", "a2", make_session(
        "a2", "userA", name="A-workflow", profile_id="pid2",
        workflow=[{"turn": 1, "task_id": "t-xyz", "rag_source_collection_id": 42}]))
    await sm.save_session("userA", "a3", make_session(
        "a3", "userA", name="A-archived", rag_collection_id=42, archived=True))
    # user B: 1 active referencing a different collection
    await sm.save_session("userB", "b1", make_session(
        "b1", "userB", name="B-only", rag_collection_id=99, profile_id="pidB"))
    # Deterministically (re)build the index from files
    await sm._rebuild_session_index()


# ─── tests ────────────────────────────────────────────────────────────────────
async def t_roundtrip():
    body = make_session("rt1", "userR", name="round")
    ok = await sm.save_session("userR", "rt1", body)
    check("save_session returns True", ok is True)
    got = await sm.read_session("userR", "rt1")
    check("read_session round-trips id", got and got.get("id") == "rt1")
    check("read_session round-trips name", got and got.get("name") == "round")
    check("read_session missing -> None", (await sm.read_session("userR", "nope")) is None)


async def t_enumeration():
    summaries = await sm.get_user_sessions("userA", include_archived=False)
    ids = {s["id"] for s in summaries}
    check("get_user_sessions excludes archived", ids == {"a1", "a2"})
    all_s = await sm.get_user_sessions("userA", include_archived=True)
    check("get_user_sessions include_archived -> 3", {s["id"] for s in all_s} == {"a1", "a2", "a3"})


async def t_bodies_scoping():
    bodies = await sm.get_user_session_bodies("userA", include_archived=False)
    ids = {b["id"] for b in bodies}
    check("get_user_session_bodies scoped to user A active", ids == {"a1", "a2"})
    check("get_user_session_bodies returns FULL bodies (has workflow_history)",
          all("last_turn_data" in b for b in bodies))
    # scoping: user A bodies never include user B
    check("get_user_session_bodies excludes other users", "b1" not in ids)
    b_bodies = await sm.get_user_session_bodies("userB")
    check("get_user_session_bodies(userB) only b1", {b["id"] for b in b_bodies} == {"b1"})


async def t_all_bodies():
    allb = await sm.get_all_session_bodies(include_archived=True)
    ids = {b["id"] for b in allb}
    check("get_all_session_bodies spans users + archived", {"a1", "a2", "a3", "b1"} <= ids)
    active = await sm.get_all_session_bodies(include_archived=False)
    check("get_all_session_bodies active excludes archived a3",
          "a3" not in {b["id"] for b in active})


async def t_all_bodies_fallback():
    # Simulate index unavailable -> backend directory-scan fallback path
    backend = get_session_backend()
    saved = backend._index_ready
    try:
        backend._index_ready = False
        allb = await sm.get_all_session_bodies(include_archived=True)
        ids = {b["id"] for b in allb}
        check("get_all_session_bodies fallback scan still finds all",
              {"a1", "a2", "a3", "b1"} <= ids)
    finally:
        backend._index_ready = saved


async def t_find_owner():
    check("find_session_owner a2 -> userA", (await sm.find_session_owner("a2")) == "userA")
    check("find_session_owner b1 -> userB", (await sm.find_session_owner("b1")) == "userB")
    check("find_session_owner unknown -> None", (await sm.find_session_owner("zzz")) is None)


async def t_workflow_history():
    wh = await sm.get_workflow_history("userA", "a2")
    check("get_workflow_history returns the turn list", isinstance(wh, list) and len(wh) == 1)
    check("get_workflow_history turn content", wh and wh[0].get("task_id") == "t-xyz")
    check("get_workflow_history empty for no-workflow session",
          (await sm.get_workflow_history("userA", "a1")) == [])
    check("get_workflow_history missing session -> []",
          (await sm.get_workflow_history("userA", "nope")) == [])
    # string-serialized fallback
    s = make_session("wstr", "userW")
    s["last_turn_data"]["workflow_history"] = "[{'turn': 7}]"
    await sm.save_session("userW", "wstr", s)
    wh2 = await sm.get_workflow_history("userW", "wstr")
    check("get_workflow_history parses python-repr string", wh2 == [{"turn": 7}])


async def t_collection_detector():
    det = ad.CollectionDetector()
    # active only
    res = await det.find_sessions("42", "userA", include_archived=False)
    active_ids = {s["session_id"] for s in res["active"]}
    check("CollectionDetector finds direct rag_collection_id (a1)", "a1" in active_ids)
    check("CollectionDetector finds workflow rag_source_collection_id (a2)", "a2" in active_ids)
    check("CollectionDetector excludes archived when not requested", "a3" not in active_ids)
    # include archived
    res2 = await det.find_sessions("42", "userA", include_archived=True)
    arch_ids = {s["session_id"] for s in res2["archived"]}
    check("CollectionDetector surfaces archived a3 when requested", "a3" in arch_ids)
    # user B does not match collection 42
    resB = await det.find_sessions("42", "userB", include_archived=True)
    check("CollectionDetector userB has no match for collection 42",
          not resB["active"] and not resB["archived"])


async def t_concurrent_transaction():
    # Two+ concurrent transactions on the SAME session must serialize via the
    # lock provider — no lost updates. Runs on every backend.
    base = make_session("ctx1", "userC", name="concurrent")
    base["counter"] = 0
    await sm.save_session("userC", "ctx1", base)

    async def bump(times):
        for _ in range(times):
            async with sm._session_transaction("userC", "ctx1") as data:
                if data is None:
                    continue
                v = data.get("counter", 0)
                await asyncio.sleep(0)  # yield: invites interleaving if unlocked
                data["counter"] = v + 1

    await asyncio.gather(bump(10), bump(10), bump(10))
    final = await sm.read_session("userC", "ctx1")
    check("concurrent transactions serialize (counter == 30)",
          final and final.get("counter") == 30)


async def t_save_updates_index():
    # save_session must refresh the summary index (fire-and-forget upsert)
    s = make_session("idx1", "userI", name="orig", profile_id="pidX")
    await sm.save_session("userI", "idx1", s)
    await sm._upsert_session_index("idx1", s)  # deterministic (avoid create_task race)
    summ = {x["id"]: x for x in await sm.get_user_sessions("userI", include_archived=True)}
    check("save_session indexes the session", "idx1" in summ)
    check("index carries profile_id", summ.get("idx1", {}).get("profile_id") == "pidX")


async def t_archive_via_delete_session():
    # Seed a session, archive it through delete_session (backend-backed), verify state + index
    s = make_session("arch1", "userZ", name="to-archive", profile_id="pidZ")
    await sm.save_session("userZ", "arch1", s)
    await sm._upsert_session_index("arch1", s)

    active_before = {x["id"] for x in await sm.get_user_sessions("userZ", include_archived=False)}
    check("session active before archive", "arch1" in active_before)

    ok = await sm.delete_session("userZ", "arch1", archived_reason="unit test")
    check("delete_session returns True", ok is True)

    body = await sm.read_session("userZ", "arch1")
    check("archived body marks archived=True", body and body.get("archived") is True)
    check("archived body records reason", body and body.get("archived_reason") == "unit test")

    # delete_session refreshes the index fire-and-forget (create_task); settle it
    # deterministically so the listing assertions don't race the scheduled upsert.
    await sm._upsert_session_index("arch1", body)
    active_after = {x["id"] for x in await sm.get_user_sessions("userZ", include_archived=False)}
    check("archived session excluded from active listing", "arch1" not in active_after)
    all_after = {x["id"] for x in await sm.get_user_sessions("userZ", include_archived=True)}
    check("archived session present when include_archived=True", "arch1" in all_after)

    # idempotent: archiving a missing session succeeds
    ok2 = await sm.delete_session("userZ", "does-not-exist")
    check("delete_session on missing -> True (idempotent)", ok2 is True)


async def t_profile_detector():
    # Historical detection resolves the queried profile_id -> tag via config_manager,
    # then matches that tag against session.profile_tags_used. Stub the resolution.
    from trusted_data_agent.core import config_manager

    class _Mgr:
        def get_profiles(self, user_uuid):
            return [{"id": "pid1", "tag": "TAG1"}]
    config_manager.get_config_manager = lambda: _Mgr()

    det = ad.ProfileDetector()
    res = await det.find_sessions("pid1", "userA", include_archived=False)
    ids = {s["session_id"] for s in res["active"]}
    check("ProfileDetector finds current profile_id (a1)", "a1" in ids)

    # historical-only session: current profile_id != pid1, but used pid1's tag (TAG1)
    await sm.save_session("userA", "a4", make_session(
        "a4", "userA", name="A-historical", profile_id="other", profile_tags_used=["TAG1"]))
    await sm._rebuild_session_index()
    res2 = await det.find_sessions("pid1", "userA", include_archived=False)
    hist = {s["session_id"]: s["relationship_type"] for s in res2["active"]}
    check("ProfileDetector finds historical-only session via resolved tag (a4)", "a4" in hist)
    check("ProfileDetector labels a4 as historical_profile",
          hist.get("a4") == "historical_profile")


def main():
    backend = os.environ.get("TDA_SESSION_BACKEND", "file")
    print(f"== session-access parity tests (backend={backend}) ==")
    _isolate()
    _stub_config_manager()
    run(_seed())
    for t in (t_roundtrip, t_enumeration, t_bodies_scoping, t_all_bodies,
              t_all_bodies_fallback, t_find_owner, t_workflow_history,
              t_save_updates_index, t_archive_via_delete_session,
              t_concurrent_transaction,
              t_collection_detector, t_profile_detector):
        print(f"\n[{t.__name__}]")
        run(t())
    print(f"\n== {_passed} passed, {_failed} failed ==")
    shutil.rmtree(_TMP, ignore_errors=True)
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
