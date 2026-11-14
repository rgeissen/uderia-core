"""CI-pure tests for memory deactivation on downvote (Increment 5).

Drives the REAL MemoryStore against an in-memory SQLite table and the real consumer
— no server, no model, 0 tokens.

The contract: a fact the extractor INFERRED from a turn the user then rejected must
stop being recalled. Deactivated, never deleted (R4), and fully reversible (R5).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # repo root (for `components`)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from components.builtin.memory.store import MemoryStore  # noqa: E402
from components.builtin.memory.types import MemoryItem, MemorySource  # noqa: E402
from trusted_data_agent.agent.feedback.consumers import memory as M  # noqa: E402
from trusted_data_agent.agent.feedback.types import VoteEvent  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _setup_store():
    import trusted_data_agent.auth.database as authdb
    from trusted_data_agent.auth.models import AgentMemory
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    AgentMemory.__table__.create(engine)
    authdb.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return MemoryStore()


def _seed(store, u="u1", session_id="s1", turn_id="3"):
    """One learned fact from the rated turn, plus everything that must survive it."""
    ids = {}
    ids["learned"] = store.add(u, MemoryItem(
        content="the user reports on EMEA", kind="fact", mem_key="region",
        source=MemorySource.LEARNED, session_id=session_id, turn_id=turn_id)).id
    ids["explicit"] = store.add(u, MemoryItem(
        content="remember I prefer metric tons", kind="preference", mem_key="units",
        source=MemorySource.EXPLICIT, session_id=session_id, turn_id=turn_id)).id
    ids["manual"] = store.add(u, MemoryItem(
        content="typed in by the user", kind="fact", mem_key="typed",
        source=MemorySource.MANUAL, session_id=session_id, turn_id=turn_id)).id
    ids["other_turn"] = store.add(u, MemoryItem(
        content="learned on a different turn", kind="fact", mem_key="other",
        source=MemorySource.LEARNED, session_id=session_id, turn_id="99")).id
    ids["other_session"] = store.add(u, MemoryItem(
        content="learned in another session", kind="fact", mem_key="othersess",
        source=MemorySource.LEARNED, session_id="s-other", turn_id=turn_id)).id
    ids["other_user"] = store.add("u2", MemoryItem(
        content="another user's fact", kind="fact", mem_key="theirs",
        source=MemorySource.LEARNED, session_id=session_id, turn_id=turn_id)).id
    return ids


def _active(store, item_id, user="u1"):
    for it in store.list_items(user, include_inactive=True):
        if it.id == item_id:
            return it.active
    return None


def _event(vote, prev=None, user="u1"):
    return VoteEvent(user_uuid=user, session_id="s1", turn_id=3, vote=vote,
                     prev_vote=prev, turn={}, profile_tag="IDEAT",
                     profile_type="llm_only")


# ── the rule, as a pure function ──────────────────────────────────────────────
def t_desired_active_rule():
    check("a downvote pauses", M.desired_active(_event("down")) is False)
    check("clearing a downvote resumes", M.desired_active(_event(None, "down")) is True)
    check("flipping down→up resumes", M.desired_active(_event("up", "down")) is True)
    check("a plain upvote does NOTHING (no force-learn)",
          M.desired_active(_event("up")) is None)
    check("clearing an upvote does nothing", M.desired_active(_event(None, "up")) is None)
    check("a re-sent downvote still pauses (idempotent)",
          M.desired_active(_event("down", "down")) is False)


# ── the store method ──────────────────────────────────────────────────────────
def t_store_scoping():
    store = _setup_store()
    ids = _seed(store)
    n = store.set_active_by_source("u1", session_id="s1", turn_id="3",
                                   source=MemorySource.LEARNED, active=False)
    check("exactly the turn's learned item is paused", n == 1)
    check("…the learned fact is inactive", _active(store, ids["learned"]) is False)
    check("an EXPLICIT 'remember this' survives a downvote",
          _active(store, ids["explicit"]) is True)
    check("a MANUAL entry survives", _active(store, ids["manual"]) is True)
    check("another turn's memory is untouched", _active(store, ids["other_turn"]) is True)
    check("another session's memory is untouched",
          _active(store, ids["other_session"]) is True)
    check("another user's memory is untouched (R8)",
          _active(store, ids["other_user"], "u2") is True)


def t_store_deactivates_never_deletes():
    store = _setup_store()
    ids = _seed(store)
    before = len(store.list_items("u1", include_inactive=True))
    store.set_active_by_source("u1", session_id="s1", turn_id="3",
                               source=MemorySource.LEARNED, active=False)
    after = store.list_items("u1", include_inactive=True)
    check("nothing is deleted (R4)", len(after) == before)
    check("…the paused row is still inspectable in the Memory manager",
          any(i.id == ids["learned"] and i.active is False for i in after))


def t_store_is_idempotent_and_guarded():
    store = _setup_store()
    _seed(store)
    kw = dict(session_id="s1", turn_id="3", source=MemorySource.LEARNED, active=False)
    check("first pause changes one row", store.set_active_by_source("u1", **kw) == 1)
    check("a second pause changes nothing (already paused)",
          store.set_active_by_source("u1", **kw) == 0)
    check("no user ⇒ no-op", store.set_active_by_source("", **kw) == 0)
    check("no session ⇒ no-op",
          store.set_active_by_source("u1", session_id="", turn_id="3",
                                     source=MemorySource.LEARNED, active=False) == 0)
    check("no turn ⇒ no-op",
          store.set_active_by_source("u1", session_id="s1", turn_id=None,
                                     source=MemorySource.LEARNED, active=False) == 0)


def t_turn_id_type_tolerance():
    """The vote carries an int turn id; the column stores a string."""
    store = _setup_store()
    _seed(store)
    check("an int turn id matches the stored string",
          store.set_active_by_source("u1", session_id="s1", turn_id=3,
                                     source=MemorySource.LEARNED, active=False) == 1)


# ── the consumer, end to end ──────────────────────────────────────────────────
class _Flag:
    def __init__(self, on=True):
        self.on = on

    def __enter__(self):
        from trusted_data_agent.core.config import APP_CONFIG
        self._sig = APP_CONFIG.FEEDBACK_SIGNALS
        APP_CONFIG.FEEDBACK_SIGNALS = dict(self._sig, memory_deactivate=self.on)
        return self

    def __exit__(self, *a):
        from trusted_data_agent.core.config import APP_CONFIG
        APP_CONFIG.FEEDBACK_SIGNALS = self._sig
        return False


def t_consumer_pause_and_resume():
    store = _setup_store()
    ids = _seed(store)
    with _Flag():
        run(M.memory(_event("down")))
        check("a downvote pauses the turn's learned memory",
              _active(store, ids["learned"]) is False)
        run(M.memory(_event(None, "down")))
        check("retracting the downvote resumes it (R5)",
              _active(store, ids["learned"]) is True)
        run(M.memory(_event("down")))
        run(M.memory(_event("up", "down")))
        check("flipping down→up also resumes it",
              _active(store, ids["learned"]) is True)


def t_consumer_upvote_is_inert():
    store = _setup_store()
    ids = _seed(store)
    with _Flag():
        run(M.memory(_event("up")))
    check("a plain upvote changes nothing", _active(store, ids["learned"]) is True)
    check("…and does not resurrect anything else",
          all(_active(store, i) is True for k, i in ids.items() if k != "other_user"))


def t_consumer_applies_to_every_engine():
    store = _setup_store()
    ids = _seed(store)
    with _Flag():
        for cls in ("llm_only", "rag_focused", "tool_enabled", "genie", None):
            run(M.memory(VoteEvent(user_uuid="u1", session_id="s1", turn_id=3,
                                   vote="down", prev_vote=None, turn={},
                                   profile_type=cls)))
    check("the rule is universal (the extractor runs behind every engine)",
          _active(store, ids["learned"]) is False)


def t_consumer_flag_off():
    store = _setup_store()
    ids = _seed(store)
    with _Flag(on=False):
        run(M.memory(_event("down")))
    check("memory_deactivate off ⇒ nothing paused",
          _active(store, ids["learned"]) is True)


def main():
    for fn in (t_desired_active_rule,
               t_store_scoping,
               t_store_deactivates_never_deletes,
               t_store_is_idempotent_and_guarded,
               t_turn_id_type_tolerance,
               t_consumer_pause_and_resume,
               t_consumer_upvote_is_inert,
               t_consumer_applies_to_every_engine,
               t_consumer_flag_off):
        fn()
    print(f"\ntest_feedback_memory: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
