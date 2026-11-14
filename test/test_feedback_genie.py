"""CI-pure tests for coordinator vote propagation (Increment 3).

Covers agent/feedback/consumers/genie.py and the `slave_turn` stamp that makes an
expert turn addressable — no server, no model, 0 tokens.

The rule under test is deliberately NARROW: a vote propagates only when exactly ONE
expert produced the answer. With two or more the answer is a synthesis and attribution
is ambiguous, so propagating would manufacture signal the user never gave.
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Isolate the eval store before any dispatch — this suite drives the real consumer
# chain, and the annotation consumer would otherwise write into the deployment's own
# tda_eval.db (see test_feedback_dispatch.py for the incident this prevents).
import tempfile as _tf  # noqa: E402
from trusted_data_agent.eval import store as _eval_store  # noqa: E402

_fd, _ISOLATED_DB = _tf.mkstemp(suffix="-test-eval.db")
os.close(_fd)
_eval_store.reset_eval_store(_eval_store.EvalStore(db_path=_ISOLATED_DB))

from trusted_data_agent.agent.feedback import dispatcher as D  # noqa: E402
from trusted_data_agent.agent.feedback.consumers import genie as G  # noqa: E402
from trusted_data_agent.agent.feedback.types import VoteEvent  # noqa: E402
from trusted_data_agent.core import session_manager as SM  # noqa: E402

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


def _slave(tag="OPTIM", sid="child-1", turn=1, status="success"):
    s = {"profile_tag": tag, "slave_session_id": sid, "status": status,
         "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}
    if turn is not None:
        s["slave_turn"] = turn
    return s


def _event(slaves, **kw):
    base = dict(user_uuid="u1", session_id="coord-s", turn_id=7, vote="down",
                prev_vote=None, turn={"slaves_invoked": slaves, "profile_type": "genie"},
                profile_tag="COORD", profile_type="genie")
    base.update(kw)
    return VoteEvent(**base)


class _Capture:
    """Replace update_turn_feedback so propagation is observable without a session."""

    def __enter__(self):
        self.calls = []
        self._orig = SM.update_turn_feedback

        async def _fake(user_uuid, session_id, turn_id, vote, *, _depth=0):
            self.calls.append({"user_uuid": user_uuid, "session_id": session_id,
                               "turn_id": turn_id, "vote": vote, "depth": _depth})
            return True

        SM.update_turn_feedback = _fake
        return self

    def __exit__(self, *a):
        SM.update_turn_feedback = self._orig
        return False


# ── the rule, as a pure function ──────────────────────────────────────────────
def t_eligible_slave_rule():
    check("exactly one successful, addressable expert is eligible",
          G.eligible_slave([_slave()]) is not None)
    check("TWO experts ⇒ never (a synthesis cannot be attributed)",
          G.eligible_slave([_slave(), _slave("FOCUS", "child-2", 1)]) is None)
    check("three experts ⇒ never",
          G.eligible_slave([_slave(), _slave("A", "c2"), _slave("B", "c3")]) is None)
    check("zero experts ⇒ nothing to propagate to",
          G.eligible_slave([]) is None)
    check("a FAILED expert is not credited or blamed",
          G.eligible_slave([_slave(status="error")]) is None)
    check("an expert with no child session is not addressable",
          G.eligible_slave([{"profile_tag": "X", "status": "success", "slave_turn": 1}]) is None)
    check("a legacy turn with no slave_turn is not addressable (never guess)",
          G.eligible_slave([_slave(turn=None)]) is None)
    check("a malformed entry is ignored, not crashed on",
          G.eligible_slave(["not-a-dict"]) is None)
    check("a non-list is ignored", G.eligible_slave(None) is None)
    check("turn 0 is a legal turn id, not a falsy skip",
          G.eligible_slave([_slave(turn=0)]) is not None)


# ── the consumer ──────────────────────────────────────────────────────────────
def t_single_expert_propagates():
    with _Capture() as cap:
        run(G.genie(_event([_slave(sid="child-9", turn=3)])))
    check("exactly one propagation", len(cap.calls) == 1)
    c = cap.calls[0] if cap.calls else {}
    check("…to the expert's own session", c.get("session_id") == "child-9")
    check("…and its own turn", c.get("turn_id") == 3)
    check("…carrying the same vote", c.get("vote") == "down")
    check("…at depth 1", c.get("depth") == 1)
    check("…for the same user", c.get("user_uuid") == "u1")


def t_multi_expert_never_propagates():
    with _Capture() as cap:
        run(G.genie(_event([_slave(), _slave("FOCUS", "child-2", 1)])))
    check("a synthesised answer propagates to NOBODY", cap.calls == [])


def t_clear_propagates_too():
    with _Capture() as cap:
        run(G.genie(_event([_slave()], vote=None, prev_vote="down")))
    check("a retracted vote reaches the expert (it must not keep the demotion)",
          len(cap.calls) == 1 and cap.calls[0]["vote"] is None)


def t_non_genie_turns_are_untouched():
    with _Capture() as cap:
        for cls in ("tool_enabled", "rag_focused", "llm_only", None):
            run(G.genie(_event([_slave()], profile_type=cls)))
    check("only a coordinator turn propagates", cap.calls == [])


def t_a_propagated_vote_does_not_hop_onward():
    with _Capture() as cap:
        run(G.genie(_event([_slave()], depth=1, propagated=True)))
    check("a propagated vote never reaches a sibling", cap.calls == [])


def t_flag_off():
    from trusted_data_agent.core.config import APP_CONFIG
    sig = APP_CONFIG.FEEDBACK_SIGNALS
    APP_CONFIG.FEEDBACK_SIGNALS = dict(sig, genie_propagation=False)
    try:
        with _Capture() as cap:
            run(G.genie(_event([_slave()])))
        check("genie_propagation off ⇒ no propagation", cap.calls == [])
    finally:
        APP_CONFIG.FEEDBACK_SIGNALS = sig


# ── end to end through the real dispatcher ───────────────────────────────────
def t_through_the_dispatcher():
    """The point of the design: propagation re-enters the ONE seam, so the expert's
    own engine consumer runs with no genie-specific engine code."""
    with _Capture() as cap:
        run(D.dispatch_vote(_event([_slave(sid="child-5", turn=2)])))
    check("the dispatcher propagates via the genie consumer",
          len(cap.calls) == 1 and cap.calls[0]["session_id"] == "child-5")


def t_depth_stop_is_the_backstop():
    """Even if a consumer were to mis-fire, the dispatcher stops acting consumers at
    MAX_DEPTH — only the recording one still runs."""
    with _Capture() as cap:
        run(D.dispatch_vote(_event([_slave()], depth=D.MAX_DEPTH, propagated=True)))
    check("at MAX_DEPTH nothing propagates", cap.calls == [])
    check("…and only depth-safe consumers are eligible at all",
          D._DEPTH_SAFE_CONSUMERS == frozenset({"annotation"}))


# ── the stamp that makes an expert turn addressable ──────────────────────────
def t_summarize_slaves_stamps_the_turn():
    from trusted_data_agent.agent.engines import coordinate_engine as CE

    class _SM:
        async def get_session(self, uuid, sid):
            return {"last_turn_data": {"workflow_history": [
                {"turn": 4, "status": "success", "turn_input_tokens": 7,
                 "turn_output_tokens": 3, "turn_cost": 0.02,
                 "profile_type": "tool_enabled"}]}}

    events = [{"type": "genie_slave_invoked",
               "payload": {"profile_tag": "OPTIM", "slave_session_id": "child-1"}}]
    slaves, *_ = run(CE._summarize_slaves(events, "u1", _SM()))
    check("the expert summary names its own turn", slaves and slaves[0].get("slave_turn") == 4)
    check("…and that entry is propagation-eligible", G.eligible_slave(slaves) is not None)

    # A child session with no persisted turn must not invent one.
    class _Empty:
        async def get_session(self, uuid, sid):
            return {"last_turn_data": {"workflow_history": []}}

    slaves2, *_ = run(CE._summarize_slaves(events, "u1", _Empty()))
    check("no persisted expert turn ⇒ no slave_turn, so no propagation",
          "slave_turn" not in slaves2[0] and G.eligible_slave(slaves2) is None)


def main():
    for fn in (t_eligible_slave_rule,
               t_single_expert_propagates,
               t_multi_expert_never_propagates,
               t_clear_propagates_too,
               t_non_genie_turns_are_untouched,
               t_a_propagated_vote_does_not_hop_onward,
               t_flag_off,
               t_through_the_dispatcher,
               t_depth_stop_is_the_backstop,
               t_summarize_slaves_stamps_the_turn):
        fn()
    print(f"\ntest_feedback_genie: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
