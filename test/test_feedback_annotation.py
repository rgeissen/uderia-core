"""CI-pure tests for the vote → eval-annotation consumer (Increment 1).

Covers agent/feedback/consumers/annotation.py, eval/sink.py and the EvalStore
reviewer filter against a temp DB + a temp span sink — no server, no model, 0 tokens.

Rules under test:
  R3  a vote is a PREFERENCE — recorded beside measurements, never inside one
  R5  every transition (incl. clear + flip) writes its own row
  R8  a user's votes are readable only as their own
  R9  append-only — a flip adds a row, never mutates one
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.feedback import dispatcher as D  # noqa: E402
from trusted_data_agent.agent.feedback.consumers import annotation as A  # noqa: E402
from trusted_data_agent.agent.feedback.types import VoteEvent  # noqa: E402
from trusted_data_agent.eval import sink as S  # noqa: E402
from trusted_data_agent.eval import store as ST  # noqa: E402

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
    base = dict(user_uuid="user-a", session_id="s1", turn_id=3, vote="down",
                prev_vote=None, turn={}, profile_tag="OPTIM",
                profile_type="tool_enabled")
    base.update(kw)
    return VoteEvent(**base)


def _tmp_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    st = ST.EvalStore(db_path=path)
    ST.reset_eval_store(st)
    return st, path


def _span(session_id="s1", turn_id=3, *, failed=False, user="user-a",
          gate_only=False, tag="OPTIM"):
    attrs = {"session.id": session_id, "turn.id": turn_id, "user.uuid": user,
             "agent.profile_tag": tag, "eval.profile_class": "tool_enabled",
             "eval.dim.cost.passed": not failed}
    if gate_only:
        attrs["eval.gate_only"] = True
    return {"attributes": attrs, "events": []}


def _write_sink(spans):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for sp in spans:
            f.write(json.dumps(sp) + "\n")
    return path


# ── the sink reader ───────────────────────────────────────────────────────────
def t_sink_reader():
    p = _write_sink([_span("s1", 1), _span("s1", 2, user="user-b"), _span("s1", 2)])
    check("reads all spans", len(S.read_spans(path=p)) == 3)
    check("user scoping", len(S.read_spans(path=p, user_uuid="user-b")) == 1)
    check("finds a turn's span", S.find_turn_span("s1", 1, path=p) is not None)
    check("unscored turn ⇒ None", S.find_turn_span("s1", 99, path=p) is None)
    check("absent session ⇒ None", S.find_turn_span("nope", 1, path=p) is None)
    check("missing sink ⇒ [] not a raise", S.read_spans(path="/nonexistent/x.jsonl") == [])

    # latest wins (a manual re-score appends a newer span for the same turn)
    p2 = _write_sink([_span("s1", 1, failed=True), _span("s1", 1, failed=False)])
    got = S.find_turn_span("s1", 1, path=p2)
    check("latest span wins", S.span_passed(got) is True)
    # a corrupt line must not kill the read
    with open(p2, "a", encoding="utf-8") as f:
        f.write("{not json\n")
    check("corrupt line skipped", len(S.read_spans(path=p2)) == 2)


def t_span_passed_semantics():
    check("passing span ⇒ True", S.span_passed(_span(failed=False)) is True)
    check("failing span ⇒ False", S.span_passed(_span(failed=True)) is False)
    check("no span ⇒ None (honest absence, NOT a pass)", S.span_passed(None) is None)
    check("gate-only (refused) span ⇒ None — it was never measured",
          S.span_passed(_span(gate_only=True)) is None)


# ── label vocabulary (one encoder + decoder, same module) ─────────────────────
def t_label_vocabulary():
    check("up label", A.label_for("up") == "user_upvote")
    check("down label", A.label_for("down") == "user_downvote")
    check("cleared label", A.label_for(None) == "user_vote_cleared")
    check("round-trip up", A.vote_from_label("user_upvote") == "up")
    check("round-trip down", A.vote_from_label("user_downvote") == "down")
    check("cleared decodes to no vote", A.vote_from_label("user_vote_cleared") is None)
    check("propagated suffix still decodes",
          A.vote_from_label("user_downvote:propagated") == "down")
    check("a non-vote annotation is not a vote label",
          not A.is_vote_label("judge_disagree") and A.is_vote_label("user_upvote"))
    check("reviewer key carries the FULL uuid (scoping filter)",
          A.reviewer_for("abcdef12-3456") == "user:abcdef12-3456")


# ── the consumer ──────────────────────────────────────────────────────────────
def t_records_every_transition():
    st, _ = _tmp_store()
    sink = _write_sink([])
    _prev_sink = S.sink_path
    S.sink_path = lambda path=None: sink
    try:
        for prev, new in ((None, "up"), ("up", "down"), ("down", None)):
            run(A.annotation(_event(vote=new, prev_vote=prev)))
        rows = st.list_annotations(limit=50)
        labels = [r["label"] for r in rows]  # newest first
        check("a row per transition, append-only (R9)", len(rows) == 3)
        check("newest-first order with the cleared vote last written",
              labels == ["user_vote_cleared", "user_downvote", "user_upvote"])
        check("session + turn recorded",
              all(r["session_id"] == "s1" and r["turn"] == 3 for r in rows))
        check("reviewer scoped to the voter",
              all(r["reviewer"] == "user:user-a" for r in rows))
    finally:
        S.sink_path = _prev_sink


def t_judge_passed_is_frozen_from_the_span():
    for failed, expect in ((False, 1), (True, 0)):
        st, _ = _tmp_store()
        sink = _write_sink([_span("s1", 3, failed=failed)])
        _prev = S.sink_path
        S.sink_path = lambda path=None, _s=sink: _s
        try:
            run(A.annotation(_event()))
            row = st.list_annotations(limit=5)[0]
            check(f"judge_passed frozen as {expect} for failed={failed}",
                  row["judge_passed"] == expect)
        finally:
            S.sink_path = _prev

    # Never scored ⇒ NULL, never a pass.
    st, _ = _tmp_store()
    sink = _write_sink([])
    _prev = S.sink_path
    S.sink_path = lambda path=None: sink
    try:
        run(A.annotation(_event()))
        row = st.list_annotations(limit=5)[0]
        check("unscored turn ⇒ judge_passed NULL (not a pass)",
              row["judge_passed"] is None)
    finally:
        S.sink_path = _prev


def t_propagated_votes_are_marked():
    st, _ = _tmp_store()
    sink = _write_sink([])
    _prev = S.sink_path
    S.sink_path = lambda path=None: sink
    try:
        run(A.annotation(_event(depth=1, propagated=True)))
        row = st.list_annotations(limit=5)[0]
        check("propagated label is distinguishable",
              row["label"] == "user_downvote:propagated")
        check("still decodes to the vote", A.vote_from_label(row["label"]) == "down")
        check("note records the depth", "depth=1" in (row["note"] or ""))
    finally:
        S.sink_path = _prev


def t_flag_off_records_nothing():
    st, _ = _tmp_store()
    prev = getattr(D, "_CONSUMERS")
    from trusted_data_agent.core.config import APP_CONFIG
    _sig = APP_CONFIG.FEEDBACK_SIGNALS
    APP_CONFIG.FEEDBACK_SIGNALS = dict(_sig, annotate_eval=False)
    try:
        run(A.annotation(_event()))
        check("annotate_eval off ⇒ no row", st.list_annotations(limit=5) == [])
    finally:
        APP_CONFIG.FEEDBACK_SIGNALS = _sig
        assert prev is D._CONSUMERS


def t_applies_to_every_engine():
    st, _ = _tmp_store()
    sink = _write_sink([])
    _prev = S.sink_path
    S.sink_path = lambda path=None: sink
    try:
        for cls in ("llm_only", "rag_focused", "tool_enabled", "genie",
                    "conversation_with_tools", None):
            run(A.annotation(_event(profile_type=cls)))
        check("every engine class is annotated (universal layer)",
              len(st.list_annotations(limit=50)) == 6)
    finally:
        S.sink_path = _prev


# ── the read side ─────────────────────────────────────────────────────────────
def t_latest_votes_and_scoping():
    st, _ = _tmp_store()
    r_a, r_b = A.reviewer_for("user-a"), A.reviewer_for("user-b")
    st.add_annotation(reviewer=r_a, label="user_upvote", session_id="s1", turn=1)
    st.add_annotation(reviewer=r_a, label="user_downvote", session_id="s1", turn=1)  # flip
    st.add_annotation(reviewer=r_a, label="user_downvote", session_id="s1", turn=2)
    st.add_annotation(reviewer=r_a, label="user_upvote", session_id="s2", turn=1)
    st.add_annotation(reviewer=r_a, label="user_vote_cleared", session_id="s2", turn=1)
    st.add_annotation(reviewer=r_b, label="user_downvote", session_id="s1", turn=9)
    st.add_annotation(reviewer="judge-cal", label="judge_disagree", session_id="s1", turn=3)

    votes = A.latest_votes("user-a")
    check("latest vote wins on a flip", votes.get(("s1", "1")) == "down")
    check("a plain vote is read", votes.get(("s1", "2")) == "down")
    check("a CLEARED vote yields no key (R5)", ("s2", "1") not in votes)
    check("another user's vote is invisible (R8)", ("s1", "9") not in votes)
    check("a non-vote annotation is ignored", ("s1", "3") not in votes)
    check("exactly the caller's live votes", set(votes) == {("s1", "1"), ("s1", "2")})
    check("other user reads only their own",
          A.latest_votes("user-b") == {("s1", "9"): "down"})
    check("no uuid ⇒ no votes", A.latest_votes("") == {})


def t_store_reviewer_filter_is_sql_side():
    st, _ = _tmp_store()
    for i in range(30):
        st.add_annotation(reviewer="user:noise", label="user_upvote",
                          session_id="s9", turn=i)
    st.add_annotation(reviewer="user:me", label="user_downvote",
                      session_id="s1", turn=1)
    # With a Python-side filter and a small limit, the one row that matters would be
    # truncated behind the noise — the exact way a per-user read goes wrong.
    rows = st.list_annotations(limit=5, reviewer="user:me")
    check("reviewer filter applies in SQL, not after truncation",
          len(rows) == 1 and rows[0]["session_id"] == "s1")


def main():
    for fn in (t_sink_reader,
               t_span_passed_semantics,
               t_label_vocabulary,
               t_records_every_transition,
               t_judge_passed_is_frozen_from_the_span,
               t_propagated_votes_are_marked,
               t_flag_off_records_nothing,
               t_applies_to_every_engine,
               t_latest_votes_and_scoping,
               t_store_reviewer_filter_is_sql_side):
        fn()
    print(f"\ntest_feedback_annotation: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
