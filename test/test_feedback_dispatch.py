"""CI-pure tests for the universal vote-signal dispatcher (Increment 0).

Covers agent/feedback/{types,dispatcher} and the champion consumer with the app
config/state monkeypatched — no server, no live model, no network, 0 tokens.

The rules under test are the §0 contract of docs/roadmap/VOTE_SIGNAL_PLAN.md:
  R1 zero votes / master off  ⇒ zero consumer calls
  R5 reversible              ⇒ all six vote transitions behave
  R6 fire-and-forget         ⇒ never raises, per-consumer isolation, strong-ref'd task
  R10 one dispatcher         ⇒ no vote logic left inline in session_manager
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.feedback import dispatcher as D  # noqa: E402
from trusted_data_agent.agent.feedback.types import VoteEvent  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG, APP_STATE  # noqa: E402

# ── isolate the eval store BEFORE any dispatch ────────────────────────────────
# Several tests drive the REAL consumer chain, which includes the annotation
# consumer — and that writes to the eval store. Without this the suite silently
# appends fixture rows ("session s1, turn 3") to the deployment's real
# tda_eval.db, polluting the very data judge calibration reads. Redirected once
# at import so no test in this file can reach the real store by any path.
import tempfile as _tf  # noqa: E402
from trusted_data_agent.eval import store as _eval_store  # noqa: E402

_fd, _ISOLATED_DB = _tf.mkstemp(suffix="-test-eval.db")
os.close(_fd)
_eval_store.reset_eval_store(_eval_store.EvalStore(db_path=_ISOLATED_DB))

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


def _event(**kw):
    base = dict(user_uuid="u1", session_id="s1", turn_id=3, vote="down",
                prev_vote=None, turn={}, profile_tag="OPTIM",
                profile_type="tool_enabled")
    base.update(kw)
    return VoteEvent(**base)


class _FakeRetriever:
    def __init__(self):
        self.calls = []

    async def update_case_feedback(self, case_id, score):
        self.calls.append((case_id, score))
        return True


class _Sandbox:
    """Swap the consumer registry + app state for one test, then restore."""

    def __init__(self, consumers=None, rag_enabled=True, retriever=None, signals=None):
        self.consumers = consumers
        self.rag_enabled = rag_enabled
        self.retriever = retriever
        self.signals = signals

    def __enter__(self):
        # Load the REAL consumers before snapshotting. Registration happens at module
        # import, which fires once per process — so snapshotting an empty registry and
        # restoring it later would permanently un-register the shipped consumers for
        # every subsequent test (the import is then a no-op). Faithful harness > a
        # harness that quietly empties the thing under test.
        D._ensure_consumers_loaded()
        self._c = list(D._CONSUMERS)
        self._loaded = D._consumers_loaded
        self._rag = getattr(APP_CONFIG, "RAG_ENABLED", None)
        self._sig = getattr(APP_CONFIG, "FEEDBACK_SIGNALS", None)
        self._ret = APP_STATE.get("rag_retriever_instance")
        if self.consumers is not None:
            D._CONSUMERS[:] = self.consumers
            D._consumers_loaded = True  # don't import the real package over the fake
        APP_CONFIG.RAG_ENABLED = self.rag_enabled
        if self.signals is not None:
            APP_CONFIG.FEEDBACK_SIGNALS = self.signals
        if self.retriever is not None:
            APP_STATE["rag_retriever_instance"] = self.retriever
        return self

    def __exit__(self, *a):
        D._CONSUMERS[:] = self._c
        D._consumers_loaded = self._loaded
        APP_CONFIG.RAG_ENABLED = self._rag
        if self._sig is not None:
            APP_CONFIG.FEEDBACK_SIGNALS = self._sig
        if self._ret is None:
            APP_STATE.pop("rag_retriever_instance", None)
        else:
            APP_STATE["rag_retriever_instance"] = self._ret
        return False


# ── the event type ────────────────────────────────────────────────────────────
def t_event_semantics():
    check("up scores +1", _event(vote="up").score == 1)
    check("down scores -1", _event(vote="down").score == -1)
    check("cleared scores 0", _event(vote=None).score == 0)
    check("is_up", _event(vote="up").is_up and not _event(vote="down").is_up)
    check("is_down", _event(vote="down").is_down)
    check("is_cleared", _event(vote=None).is_cleared)
    check("was_down reads PREVIOUS state",
          _event(vote="up", prev_vote="down").was_down
          and not _event(vote="down", prev_vote=None).was_down)
    check("changed detects a re-sent identical vote",
          not _event(vote="up", prev_vote="up").changed
          and _event(vote="up", prev_vote=None).changed)
    check("frozen (immutable snapshot)",
          isinstance(getattr(VoteEvent, "__dataclass_params__", None), object)
          and VoteEvent.__dataclass_params__.frozen)
    # R8/design: the turn snapshot is what consumers read; profile_id is NOT a field
    # (a workflow_history turn does not carry one — see types.py rationale).
    check("no profile_id field (would silently lie)",
          "profile_id" not in VoteEvent.__dataclass_fields__)
    check("carries per-turn profile_tag + profile_type",
          "profile_tag" in VoteEvent.__dataclass_fields__
          and "profile_type" in VoteEvent.__dataclass_fields__)


# ── the dispatcher ────────────────────────────────────────────────────────────
def t_dispatch_runs_consumers_in_order():
    seen = []

    async def a(ev):
        seen.append("a")

    async def b(ev):
        seen.append("b")

    with _Sandbox(consumers=[("a", a), ("b", b)]):
        run(D.dispatch_vote(_event()))
    check("all consumers ran, in registration order", seen == ["a", "b"])


def t_consumer_isolation():
    seen = []

    async def bomb(ev):
        raise RuntimeError("boom")

    async def after(ev):
        seen.append("after")

    with _Sandbox(consumers=[("bomb", bomb), ("after", after)]):
        # R6: dispatch_vote must not raise, and a failing consumer must not stop later
        # ones. The consumer failure is logged with a traceback by design — silence it
        # here so an EXPECTED failure does not read as suite noise.
        import logging
        lg = logging.getLogger("quart.app")
        prev = lg.disabled
        lg.disabled = True
        raised = False
        try:
            run(D.dispatch_vote(_event()))
        except Exception:
            raised = True
        finally:
            lg.disabled = prev
    check("dispatch_vote never raises on a failing consumer", not raised)
    check("a later consumer still runs after one fails", seen == ["after"])


def t_master_switch_off():
    seen = []

    async def a(ev):
        seen.append("a")

    with _Sandbox(consumers=[("a", a)], signals={"enabled": False}):
        run(D.dispatch_vote(_event()))
    check("master off ⇒ zero consumer calls (R1 baseline)", seen == [])


def t_signal_enabled_defensive():
    with _Sandbox(consumers=[], signals={"enabled": True}):
        check("known key read from config", D.signal_enabled("enabled") is True)
        # Absent key falls back to the shipped default, never raises.
        check("absent key ⇒ shipped default (judge OFF)",
              D.signal_enabled("judge_on_downvote") is False)
        check("absent key ⇒ shipped default (annotate ON)",
              D.signal_enabled("annotate_eval") is True)
        check("unknown key is False, not an exception",
              D.signal_enabled("no_such_flag") is False)
    with _Sandbox(consumers=[], signals="not-a-dict"):
        check("malformed block degrades to defaults",
              D.signal_enabled("enabled") is True)


def t_depth_guard():
    seen = []

    async def annotation(ev):
        seen.append("annotation")

    async def acting(ev):
        seen.append("acting")

    for depth, expect in ((0, ["annotation", "acting"]),
                          (1, ["annotation", "acting"]),
                          (2, ["annotation"]),
                          (5, ["annotation"])):
        seen.clear()
        with _Sandbox(consumers=[("annotation", annotation), ("acting", acting)]):
            run(D.dispatch_vote(_event(depth=depth)))
        check(f"depth {depth} ⇒ {expect}", seen == expect)


def t_registration_is_idempotent_and_ordered():
    with _Sandbox(consumers=[]):
        async def v1(ev):
            pass

        async def v2(ev):
            pass

        async def other(ev):
            pass

        D.register_consumer("x")(v1)
        D.register_consumer("y")(other)
        D.register_consumer("x")(v2)  # re-register (module reload)
        names = D.registered_consumers()
        check("re-registration does not duplicate", names == ["x", "y"])
        check("re-registration replaces IN PLACE (order preserved)",
              D._CONSUMERS[0][1] is v2)


def t_schedule_is_strong_reffed():
    async def _drive():
        seen = []

        async def a(ev):
            seen.append("a")

        with _Sandbox(consumers=[("a", a)]):
            task = D.schedule_vote_dispatch(_event())
            # R6: the task must be tracked immediately (a bare create_task may be GC'd).
            tracked = task in D._DISPATCH_TASKS
            await asyncio.sleep(0)
            await task
            return tracked, seen, task

    tracked, seen, task = run(_drive())
    check("scheduled task is strong-ref'd while in flight", tracked)
    check("scheduled dispatch actually ran the consumer", seen == ["a"])
    check("task discarded from the set on completion", task not in D._DISPATCH_TASKS)


# ── the champion consumer ─────────────────────────────────────────────────────
def _champion():
    from trusted_data_agent.agent.feedback.consumers.champion import champion
    return champion


def t_champion_scores_a_seeded_turn():
    for vote, score in (("down", -1), ("up", 1), (None, 0)):
        r = _FakeRetriever()
        with _Sandbox(retriever=r):
            run(_champion()(_event(vote=vote, turn={"case_id": "abc"})))
        check(f"vote {vote!r} ⇒ update_case_feedback(abc, {score})",
              r.calls == [("abc", score)])


def t_champion_skips_caseless_turn():
    r = _FakeRetriever()
    with _Sandbox(retriever=r):
        # An engine whose turn was never seeded (no case_id) must not reach the
        # retriever at all — update_case_feedback scans every collection on disk.
        run(_champion()(_event(turn={"profile_type": "llm_only"})))
        run(_champion()(_event(turn={"case_id": ""})))
        run(_champion()(_event(turn={"case_id": None})))
    check("caseless turn ⇒ retriever untouched", r.calls == [])


def t_champion_respects_rag_master_and_missing_retriever():
    r = _FakeRetriever()
    with _Sandbox(rag_enabled=False, retriever=r):
        run(_champion()(_event(turn={"case_id": "abc"})))
    check("RAG master off ⇒ no scoring", r.calls == [])

    with _Sandbox(rag_enabled=True):
        APP_STATE.pop("rag_retriever_instance", None)
        raised = False
        try:
            run(_champion()(_event(turn={"case_id": "abc"})))
        except Exception:
            raised = True
    check("absent retriever ⇒ clean no-op, no raise", not raised)


def t_champion_keys_on_case_not_profile_type():
    # Verified against real session files: the RAG worker stamps case_id on whatever
    # turn it seeded (a rag_focused turn carries one), so keying on profile type would
    # silently drop real feedback.
    r = _FakeRetriever()
    with _Sandbox(retriever=r):
        run(_champion()(_event(profile_type="rag_focused",
                               turn={"case_id": "focus-case"})))
    check("a non-Optimize seeded turn is still scored",
          r.calls == [("focus-case", -1)])


# ── the six vote transitions, end to end through the real consumer ────────────
def t_all_six_transitions():
    transitions = [
        (None, "up", 1), (None, "down", -1),
        ("up", None, 0), ("down", None, 0),
        ("up", "down", -1), ("down", "up", 1),
    ]
    ok = True
    for prev, new, score in transitions:
        r = _FakeRetriever()
        with _Sandbox(retriever=r):
            run(D.dispatch_vote(_event(vote=new, prev_vote=prev,
                                       turn={"case_id": "c1"})))
        if r.calls != [("c1", score)]:
            ok = False
            print(f"  FAIL: transition {prev}->{new} scored {r.calls}")
    check("all six vote transitions score correctly (R5)", ok)


def t_idempotent_resend():
    r = _FakeRetriever()
    with _Sandbox(retriever=r):
        ev = _event(vote="down", prev_vote="down", turn={"case_id": "c1"})
        run(D.dispatch_vote(ev))
        run(D.dispatch_vote(ev))
    check("a re-sent identical vote is idempotent (same score, no error)",
          r.calls == [("c1", -1), ("c1", -1)])


# ── structural: R10 one dispatcher ────────────────────────────────────────────
def _src(rel):
    p = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", rel)
    with open(p, encoding="utf-8") as f:
        return f.read()


def t_no_vote_logic_left_in_session_manager():
    sm = _src("core/session_manager.py")
    # The old inline consumer must be GONE — not merely bypassed.
    check("session_manager no longer calls update_case_feedback",
          "update_case_feedback" not in sm)
    check("session_manager no longer reads the rag retriever for votes",
          "rag_retriever_instance" not in sm)
    check("session_manager dispatches through the ONE seam",
          "schedule_vote_dispatch" in sm)
    # The dispatch must be scheduled, never awaited inline (R6: the response must
    # not wait on a consumer).
    check("dispatch is not awaited inline in update_turn_feedback",
          not re.search(r"await\s+dispatch_vote", sm))
    check("propagation depth is internal-only (keyword-only, underscored)",
          "*, _depth: int = 0" in sm)


def t_snapshot_is_taken_after_the_update():
    # A consumer must see the turn AS PERSISTED (with the new vote), and must not
    # alias live session state.
    sm = _src("core/session_manager.py")
    i_apply = sm.find('turn["feedback"] = vote')
    i_snap = sm.find("turn_snapshot = copy.deepcopy(turn)")
    check("snapshot exists and is a deep copy", i_snap > 0)
    check("snapshot is taken AFTER the vote is applied", 0 < i_apply < i_snap)


def t_suite_never_touches_the_real_eval_store():
    """This suite drives the real consumer chain, so it MUST be writing to the
    isolated store — otherwise every run appends fixtures to the deployment's own
    annotation table (it did, until this was caught: 72 fixture rows)."""
    from trusted_data_agent.eval.store import get_eval_store
    check("the eval store is redirected to a temp DB",
          str(get_eval_store().db_path) == _ISOLATED_DB)
    rows = get_eval_store().list_annotations(limit=500)
    check("…and the dispatches in this suite landed there",
          any(r.get("session_id") == "s1" for r in rows))


def t_config_block_present():
    check("FEEDBACK_SIGNALS exists on APP_CONFIG",
          isinstance(getattr(APP_CONFIG, "FEEDBACK_SIGNALS", None), dict))
    cfg = APP_CONFIG.FEEDBACK_SIGNALS
    check("every dispatcher default has a config key",
          set(D.DEFAULT_SIGNALS) == set(cfg))
    check("config and dispatcher defaults agree", D.DEFAULT_SIGNALS == cfg
          or all(cfg[k] == D.DEFAULT_SIGNALS[k] for k in cfg))
    check("the only token-spending consumer ships OFF (R7)",
          cfg.get("judge_on_downvote") is False)


def main():
    for fn in (t_event_semantics,
               t_dispatch_runs_consumers_in_order,
               t_consumer_isolation,
               t_master_switch_off,
               t_signal_enabled_defensive,
               t_depth_guard,
               t_registration_is_idempotent_and_ordered,
               t_schedule_is_strong_reffed,
               t_champion_scores_a_seeded_turn,
               t_champion_skips_caseless_turn,
               t_champion_respects_rag_master_and_missing_retriever,
               t_champion_keys_on_case_not_profile_type,
               t_all_six_transitions,
               t_idempotent_resend,
               t_no_vote_logic_left_in_session_manager,
               t_snapshot_is_taken_after_the_update,
               t_suite_never_touches_the_real_eval_store,
               t_config_block_present):
        fn()
    print(f"\ntest_feedback_dispatch: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
