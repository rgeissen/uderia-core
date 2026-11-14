"""CI-pure tests for judge diagnosis on downvote (Increment 6).

Covers agent/feedback/consumers/diagnosis.py and the `force_judges` parameter — no
server, no model, 0 tokens (the judge call itself is monkeypatched; what is asserted
is WHEN it is made and with what).

This is the only consumer that spends tokens, so the guards are the test:
default OFF, downvote-only, never on a propagated vote, once per turn, and never
overriding a profile's own judge opt-out.
"""
import asyncio
import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tempfile as _tf  # noqa: E402
from trusted_data_agent.eval import store as _eval_store  # noqa: E402

_fd, _ISOLATED_DB = _tf.mkstemp(suffix="-test-eval.db")
os.close(_fd)
_eval_store.reset_eval_store(_eval_store.EvalStore(db_path=_ISOLATED_DB))

from trusted_data_agent.agent.feedback.consumers import diagnosis as DG  # noqa: E402
from trusted_data_agent.agent.feedback.types import VoteEvent  # noqa: E402
from trusted_data_agent.eval import online as ONLINE  # noqa: E402

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


def _event(vote="down", **kw):
    base = dict(user_uuid="u1", session_id="s1", turn_id=3, vote=vote,
                prev_vote=None, turn={}, profile_tag="OPTIM",
                profile_type="tool_enabled")
    base.update(kw)
    return VoteEvent(**base)


class _Ctx:
    """Capture the judge call; control the flags."""

    def __init__(self, flag=True, online=True):
        self.flag, self.online = flag, online
        self.calls = []

    def __enter__(self):
        from trusted_data_agent.core.config import APP_CONFIG
        self._sig = APP_CONFIG.FEEDBACK_SIGNALS
        self._oe = getattr(APP_CONFIG, "ONLINE_EVAL_ENABLED", True)
        APP_CONFIG.FEEDBACK_SIGNALS = dict(self._sig, judge_on_downvote=self.flag)
        APP_CONFIG.ONLINE_EVAL_ENABLED = self.online
        self._orig = ONLINE.evaluate_turn_online

        async def _fake(**kw):
            self.calls.append(kw)
            return {"ok": True}

        ONLINE.evaluate_turn_online = _fake
        # Stub the config manager too. resolve_profile() otherwise reaches the REAL
        # one, which bootstraps a user_preferences row for the fixture uuid — i.e. a
        # CI-pure suite writing to the deployment's auth DB (it only failed on a
        # foreign key). Same lesson as the eval-store isolation.
        import trusted_data_agent.core.config_manager as CM
        self._cm = CM.get_config_manager

        class _Mgr:
            def get_profiles(self, uuid):
                return [{"tag": "OPTIM", "id": "p-optim"}]

        CM.get_config_manager = lambda: _Mgr()
        DG._SEEN.clear()
        return self

    def __exit__(self, *a):
        from trusted_data_agent.core.config import APP_CONFIG
        import trusted_data_agent.core.config_manager as CM
        APP_CONFIG.FEEDBACK_SIGNALS = self._sig
        APP_CONFIG.ONLINE_EVAL_ENABLED = self._oe
        ONLINE.evaluate_turn_online = self._orig
        CM.get_config_manager = self._cm
        DG._SEEN.clear()
        return False


# ── the force_judges parameter ────────────────────────────────────────────────
def t_force_judges_signature_and_default():
    sig = inspect.signature(ONLINE.evaluate_turn_online)
    check("evaluate_turn_online accepts force_judges", "force_judges" in sig.parameters)
    check("…defaulting to False (byte-identical baseline)",
          sig.parameters["force_judges"].default is False)
    src = inspect.getsource(ONLINE.evaluate_turn_online)
    # It must skip the SAMPLER, not the profile's own opt-out.
    i_wanted = src.find('if jprefs["wanted"]:')
    i_force = src.find("if force_judges:")
    check("force_judges is evaluated INSIDE the profile's judge opt-in",
          0 < i_wanted < i_force)
    check("…and the sampler is the branch it replaces",
          "should_judge(" in src[i_force:i_force + 900])


def t_force_judges_adds_no_second_judge_path():
    """The one-sink imperative: no private judge call, no private cost path."""
    src = inspect.getsource(ONLINE.evaluate_turn_online)
    dsrc = inspect.getsource(DG)
    check("the consumer calls the shared hook, not a judge directly",
          "evaluate_turn_online(" in dsrc)
    check("…and imports no judge/panel machinery of its own",
          "make_judge_fn" not in dsrc and "build_panel" not in dsrc)
    check("…nor any cost sink of its own",
          "_record_score_usage" not in dsrc and "CostManager" not in dsrc)
    check("the shared hook still owns the canonical sink",
          "_record_score_usage" in src or "_summary" in src)


# ── the guards ────────────────────────────────────────────────────────────────
def t_default_off():
    from trusted_data_agent.core.config import APP_CONFIG
    check("judge_on_downvote ships OFF (R7 — the only token spender)",
          APP_CONFIG.FEEDBACK_SIGNALS.get("judge_on_downvote") is False)
    with _Ctx(flag=False) as c:
        run(DG.diagnosis(_event()))
    check("…so a downvote spends nothing by default", c.calls == [])


def t_downvote_only():
    with _Ctx() as c:
        run(DG.diagnosis(_event("up")))
        run(DG.diagnosis(_event(None)))
    check("an upvote and a cleared vote never pay for judges", c.calls == [])


def t_runs_on_a_downvote():
    with _Ctx() as c:
        run(DG.diagnosis(_event()))
    check("exactly one judge run", len(c.calls) == 1)
    kw = c.calls[0] if c.calls else {}
    check("…forced past the sampler", kw.get("force_judges") is True)
    check("…on the voted turn", kw.get("turn_id") == 3 and kw.get("session_id") == "s1")
    check("…for the voter", kw.get("user_uuid") == "u1")
    check("…carrying the profile resolved from the turn's own tag",
          (kw.get("profile") or {}).get("id") == "p-optim")


def t_never_on_a_propagated_vote():
    with _Ctx() as c:
        run(DG.diagnosis(_event(depth=1, propagated=True)))
    check("a coordinator's downvote does not ALSO pay on the expert turn",
          c.calls == [])


def t_once_per_turn():
    with _Ctx() as c:
        run(DG.diagnosis(_event()))
        run(DG.diagnosis(_event()))                       # re-sent
        run(DG.diagnosis(_event("up", prev_vote="down")))      # flip away
        run(DG.diagnosis(_event("down", prev_vote="up")))      # and back
    check("a down → up → down cycle spends ONCE", len(c.calls) == 1)


def t_a_different_turn_is_a_different_diagnosis():
    with _Ctx() as c:
        run(DG.diagnosis(_event()))
        run(DG.diagnosis(_event(turn_id=4)))
        run(DG.diagnosis(_event(session_id="s2")))
    check("each distinct turn earns its own diagnosis", len(c.calls) == 3)


def t_respects_the_scoring_master():
    with _Ctx(online=False) as c:
        run(DG.diagnosis(_event()))
    check("scoring master off ⇒ no judge run", c.calls == [])


def t_seen_set_is_bounded():
    with _Ctx() as c:
        for i in range(DG._SEEN_MAX + 5):
            DG._already_diagnosed("s", i)
        check("the dedup set cannot grow without bound",
              len(DG._SEEN) <= DG._SEEN_MAX)


# ── profile resolution ────────────────────────────────────────────────────────
def t_profile_resolution():
    import trusted_data_agent.core.config_manager as CM

    class _Mgr:
        def get_profiles(self, uuid):
            return [{"tag": "FOCUS", "id": "p-focus"}, {"tag": "@OPTIM", "id": "p-optim"}]

    orig = CM.get_config_manager
    CM.get_config_manager = lambda: _Mgr()
    try:
        check("resolves by the turn's own tag",
              (DG.resolve_profile("u1", "OPTIM") or {}).get("id") == "p-optim")
        check("…tolerating the @ prefix and case",
              (DG.resolve_profile("u1", "@focus") or {}).get("id") == "p-focus")
        check("an unknown tag ⇒ None (fall back to admin defaults, never guess)",
              DG.resolve_profile("u1", "NOPE") is None)
        check("no tag ⇒ None", DG.resolve_profile("u1", None) is None)
    finally:
        CM.get_config_manager = orig

    # A resolution failure must not stop the diagnosis — it degrades to defaults.
    def _boom():
        raise RuntimeError("config unavailable")

    CM.get_config_manager = _boom
    try:
        import logging
        lg = logging.getLogger("quart.app")
        prev, lg.disabled = lg.disabled, True
        try:
            check("a resolution failure degrades to None rather than raising",
                  DG.resolve_profile("u1", "OPTIM") is None)
        finally:
            lg.disabled = prev
    finally:
        CM.get_config_manager = orig


def main():
    for fn in (t_force_judges_signature_and_default,
               t_force_judges_adds_no_second_judge_path,
               t_default_off,
               t_downvote_only,
               t_runs_on_a_downvote,
               t_never_on_a_propagated_vote,
               t_once_per_turn,
               t_a_different_turn_is_a_different_diagnosis,
               t_respects_the_scoring_master,
               t_seen_set_is_bounded,
               t_profile_resolution):
        fn()
    print(f"\ntest_feedback_diagnosis: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
