"""CI-pure tests for the vote → RAG champion mechanism (Increment 2).

Drives the REAL ``RAGRetriever.update_case_feedback`` end to end against a fake
ChromaDB collection and real temp case files — no server, no DB, no network.

Increment 2 completes the mechanism's missing half (a downvote already demoted and
re-elected; an upvote did nothing but store a number) and fixes an ordering defect
the upvote half exposed:

    Re-election filters on ``user_feedback_score >= 0`` READ FROM THE STORE, and it
    used to run BEFORE the new score was written. So a just-downvoted case was still
    eligible, could win its OWN re-election, and was then stamped
    ``is_most_efficient=False`` by the write that followed — leaving the query with
    NO champion at all. An upvote had the mirror problem: election ranks by feedback
    first, so promoting against the stale score would simply not promote.
"""
import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.rag_retriever import RAGRetriever  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


_LOOP = asyncio.new_event_loop()


def run(coro):
    return _LOOP.run_until_complete(coro)


# ── a ChromaDB collection just faithful enough for this method ────────────────
def _match(where, meta):
    if "$and" in where:
        return all(_match(c, meta) for c in where["$and"])
    if "$or" in where:
        return any(_match(c, meta) for c in where["$or"])
    for key, cond in where.items():
        val = meta.get(key)
        if "$eq" in cond and val != cond["$eq"]:
            return False
        if "$gte" in cond and not (val is not None and val >= cond["$gte"]):
            return False
    return True


class _FakeCollection:
    def __init__(self, cases):
        self.cases = {cid: dict(m) for cid, m in cases.items()}

    def get(self, where=None, ids=None, include=None):
        # COPIES, exactly as a real ChromaDB read returns freshly-deserialised dicts.
        # This is load-bearing for the ordering test: with aliased dicts, mutating the
        # returned metadata would be instantly visible to a subsequent query, so the
        # pre-write re-election defect could not reproduce and the mutation escaped.
        # A fake that is unfaithful in the dimension under test measures nothing.
        if ids is not None:
            got = {cid: self.cases[cid] for cid in ids if cid in self.cases}
        elif where is not None:
            got = {cid: m for cid, m in self.cases.items() if _match(where, m)}
        else:
            got = dict(self.cases)
        return {"ids": list(got.keys()), "metadatas": [dict(got[c]) for c in got]}

    def update(self, ids=None, metadatas=None):
        for cid, m in zip(ids, metadatas):
            self.cases[cid] = dict(m)


Q = "how many databases"


def _retriever(cases, files=None):
    """A retriever with the real feedback methods bound, real case files on disk."""
    tmp = Path(tempfile.mkdtemp())
    for cid, meta in cases.items():
        raw = cid.replace("case_", "")
        payload = {"metadata": dict(meta)}
        if files and cid in files:
            payload["metadata"].update(files[cid])
        (tmp / f"case_{raw}.json").write_text(json.dumps(payload), encoding="utf-8")

    coll = _FakeCollection(cases)
    r = types.SimpleNamespace()
    r.collections = {1: coll}
    r.feedback_cache = {}
    r._get_collection_dir = lambda cid: tmp
    for name in ("update_case_feedback", "_reevaluate_champion_for_query",
                 "record_champion_replay_outcome"):
        setattr(r, name, types.MethodType(getattr(RAGRetriever, name), r))
    return r, coll, tmp


def _champion(coll):
    return [c for c, m in coll.cases.items() if m.get("is_most_efficient")]


# ── the ordering defect: a downvote must leave a champion behind ──────────────
def t_downvote_leaves_a_champion():
    # A is champion and has FEWER tokens, so under the old (pre-write) ordering it
    # won its own re-election — and the query was left with no champion at all.
    cases = {
        "case_A": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 100, "is_most_efficient": True},
        "case_B": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 300, "is_most_efficient": False},
    }
    r, coll, _ = _retriever(cases)
    run(r.update_case_feedback("case_A", -1))

    check("the downvoted case is demoted",
          coll.cases["case_A"]["is_most_efficient"] is False)
    check("…its score is stored", coll.cases["case_A"]["user_feedback_score"] == -1)
    check("A CHAMPION SURVIVES the downvote (the ordering defect)",
          _champion(coll) == ["case_B"])


def t_downvote_with_no_alternative_leaves_none_standing():
    cases = {"case_A": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                        "output_tokens": 100, "is_most_efficient": True}}
    r, coll, _ = _retriever(cases)
    run(r.update_case_feedback("case_A", -1))
    check("the only case, downvoted, is demoted and not resurrected",
          coll.cases["case_A"]["is_most_efficient"] is False and _champion(coll) == [])


# ── the missing half: an upvote PROMOTES ─────────────────────────────────────
def t_upvote_promotes():
    # B is worse on tokens, so only the feedback rank can promote it — which means
    # the election must see the NEW score.
    cases = {
        "case_A": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 100, "is_most_efficient": True},
        "case_B": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 900, "is_most_efficient": False},
    }
    r, coll, _ = _retriever(cases)
    run(r.update_case_feedback("case_B", 1))
    check("an upvote promotes the case to champion", _champion(coll) == ["case_B"])
    check("…and the previous champion is demoted, not left duplicated",
          coll.cases["case_A"]["is_most_efficient"] is False)


def t_upvote_respects_user_scope():
    # Another user's case must never be touched by this user's vote (R8).
    cases = {
        "case_A": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 100, "is_most_efficient": True},
        "case_Z": {"user_query": Q, "user_uuid": "u2", "user_feedback_score": 0,
                   "output_tokens": 10, "is_most_efficient": True},
    }
    r, coll, _ = _retriever(cases)
    run(r.update_case_feedback("case_A", 1))
    check("the voter's own case is champion", coll.cases["case_A"]["is_most_efficient"] is True)
    check("another user's champion is untouched (R8)",
          coll.cases["case_Z"]["is_most_efficient"] is True)


# ── reversibility (R5) ────────────────────────────────────────────────────────
def t_clearing_a_downvote_restores_eligibility():
    cases = {
        "case_A": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 100, "is_most_efficient": True},
        "case_B": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 300, "is_most_efficient": False},
    }
    r, coll, _ = _retriever(cases)
    run(r.update_case_feedback("case_A", -1))
    check("downvote handed the crown to B", _champion(coll) == ["case_B"])
    run(r.update_case_feedback("case_A", 0))
    check("clearing the vote restores the score", coll.cases["case_A"]["user_feedback_score"] == 0)
    check("…and A competes again, winning on tokens (R5 reversible)",
          _champion(coll) == ["case_A"])


def t_flip_down_to_up():
    cases = {
        "case_A": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 100, "is_most_efficient": True},
        "case_B": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                   "output_tokens": 900, "is_most_efficient": False},
    }
    r, coll, _ = _retriever(cases)
    run(r.update_case_feedback("case_B", -1))
    run(r.update_case_feedback("case_B", 1))
    check("a down→up flip fully undoes the demotion",
          coll.cases["case_B"]["user_feedback_score"] == 1 and _champion(coll) == ["case_B"])


# ── the replay-failure counter ────────────────────────────────────────────────
def _consumer():
    from trusted_data_agent.agent.feedback.consumers.champion import champion
    return champion


def _event(vote, case_id="case_A"):
    from trusted_data_agent.agent.feedback.types import VoteEvent
    return VoteEvent(user_uuid="u1", session_id="s1", turn_id=1, vote=vote,
                     prev_vote=None, turn={"case_id": case_id},
                     profile_tag="OPTIM", profile_type="tool_enabled")


class _Recorder:
    """Stands in for the retriever at the consumer boundary."""

    def __init__(self):
        self.scored = []
        self.replay = []

    async def update_case_feedback(self, case_id, score):
        self.scored.append((case_id, score))

    async def record_champion_replay_outcome(self, case_id, succeeded=False, is_transient=False):
        self.replay.append((case_id, succeeded))


class _Ctx:
    def __init__(self, retriever, upvote_flag=True):
        self.r = retriever
        self.flag = upvote_flag

    def __enter__(self):
        from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
        self._rag = getattr(APP_CONFIG, "RAG_ENABLED", None)
        self._sig = getattr(APP_CONFIG, "FEEDBACK_SIGNALS", None)
        self._ret = APP_STATE.get("rag_retriever_instance")
        APP_CONFIG.RAG_ENABLED = True
        APP_CONFIG.FEEDBACK_SIGNALS = dict(self._sig or {}, champion_upvote=self.flag)
        APP_STATE["rag_retriever_instance"] = self.r
        return self

    def __exit__(self, *a):
        from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
        APP_CONFIG.RAG_ENABLED = self._rag
        if self._sig is not None:
            APP_CONFIG.FEEDBACK_SIGNALS = self._sig
        if self._ret is None:
            APP_STATE.pop("rag_retriever_instance", None)
        else:
            APP_STATE["rag_retriever_instance"] = self._ret
        return False


def t_upvote_resets_the_replay_counter():
    rec = _Recorder()
    with _Ctx(rec):
        run(_consumer()(_event("up")))
    check("an upvote scores the case", rec.scored == [("case_A", 1)])
    check("…and resets the replay-failure counter (a human endorsement outranks the heuristic)",
          rec.replay == [("case_A", True)])


def t_downvote_never_touches_the_replay_counter():
    rec = _Recorder()
    with _Ctx(rec):
        run(_consumer()(_event("down")))
        run(_consumer()(_event(None)))
    check("a downvote scores the case", ("case_A", -1) in rec.scored)
    check("…but NEVER increments the replay counter — that counter means the run "
          "really failed, and taste must not retire a working plan through it",
          rec.replay == [])


def t_upvote_promotion_flag_off():
    rec = _Recorder()
    with _Ctx(rec, upvote_flag=False):
        run(_consumer()(_event("up")))
    check("champion_upvote off ⇒ the score still lands (pre-existing behaviour)",
          rec.scored == [("case_A", 1)])
    check("…and the counter reset is skipped", rec.replay == [])


def t_replay_counter_reset_is_real():
    """Through the REAL method: an upvote clears a live failure count."""
    cases = {"case_A": {"user_query": Q, "user_uuid": "u1", "user_feedback_score": 0,
                        "output_tokens": 100, "is_most_efficient": True}}
    r, coll, tmp = _retriever(cases, files={"case_A": {"replay_failure_count": 1}})
    run(r.record_champion_replay_outcome("case_A", succeeded=True))
    meta = json.loads((tmp / "case_A.json").read_text())["metadata"]
    check("the stored replay-failure count is cleared",
          int(meta.get("replay_failure_count", -1)) == 0)


def main():
    for fn in (t_downvote_leaves_a_champion,
               t_downvote_with_no_alternative_leaves_none_standing,
               t_upvote_promotes,
               t_upvote_respects_user_scope,
               t_clearing_a_downvote_restores_eligibility,
               t_flip_down_to_up,
               t_upvote_resets_the_replay_counter,
               t_downvote_never_touches_the_replay_counter,
               t_upvote_promotion_flag_off,
               t_replay_counter_reset_is_real):
        fn()
    print(f"\ntest_feedback_champion: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
