"""CI-pure tests for per-document retrieval feedback (Increment 4).

Covers core/retrieval_feedback.py (temp DB) and the consumer that feeds it — no
server, no model, 0 tokens.

The contract: RECORD ONLY. Nothing here may influence retrieval, reranking or
scoring; the rows exist so a repeatedly-rejected source becomes visible in Corpus
Health for a human to act on.
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core import retrieval_feedback as RF  # noqa: E402
from trusted_data_agent.agent.feedback.consumers import retrieval as C  # noqa: E402
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


def _tmpdb():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ── the store ─────────────────────────────────────────────────────────────────
def t_record_and_summarise():
    db = _tmpdb()
    n = RF.record("u1", document_ids=["docA", "docB"], vote=-1, collection_id=7,
                  query_text="what is the policy", session_id="s1", turn=1, db_path=db)
    check("one row per served document", n == 2)
    s = RF.doc_summary("u1", 7, db_path=db)
    check("both documents counted", set(s) == {"docA", "docB"})
    check("a downvote is a down, net -1",
          s["docA"] == {"up": 0, "down": 1, "net": -1})

    RF.record("u1", document_ids=["docA"], vote=1, collection_id=7,
              session_id="s2", turn=1, db_path=db)
    s = RF.doc_summary("u1", 7, db_path=db)
    check("a later positive turn nets out", s["docA"] == {"up": 1, "down": 1, "net": 0})


def t_a_flip_is_counted_once():
    """The reason the summary resolves the latest row per (turn, document): a user who
    changes their mind must be counted once, in their current position."""
    db = _tmpdb()
    for v in (-1, 1, -1):          # down → up → down, all on the SAME turn
        RF.record("u1", document_ids=["docA"], vote=v, collection_id=7,
                  session_id="s1", turn=1, db_path=db)
    s = RF.doc_summary("u1", 7, db_path=db)
    check("three votes on one turn count as ONE", s["docA"]["down"] + s["docA"]["up"] == 1)
    check("…in the user's CURRENT position", s["docA"] == {"up": 0, "down": 1, "net": -1})


def t_a_cleared_vote_cancels():
    db = _tmpdb()
    RF.record("u1", document_ids=["docA"], vote=-1, collection_id=7,
              session_id="s1", turn=1, db_path=db)
    RF.record("u1", document_ids=["docA"], vote=0, collection_id=7,
              session_id="s1", turn=1, db_path=db)
    s = RF.doc_summary("u1", 7, db_path=db)
    check("a retracted vote leaves no up and no down",
          s.get("docA") == {"up": 0, "down": 0, "net": 0})


def t_scoping():
    db = _tmpdb()
    RF.record("u1", document_ids=["docA"], vote=-1, collection_id=7, session_id="s1",
              turn=1, db_path=db)
    RF.record("u2", document_ids=["docA"], vote=-1, collection_id=7, session_id="s9",
              turn=1, db_path=db)
    RF.record("u1", document_ids=["docA"], vote=-1, collection_id=8, session_id="s3",
              turn=1, db_path=db)
    check("another user's votes are invisible (R8)",
          RF.doc_summary("u1", 7, db_path=db)["docA"]["down"] == 1)
    check("another collection's votes stay in their own collection",
          RF.doc_summary("u1", 8, db_path=db)["docA"]["down"] == 1)
    check("no collection filter ⇒ the user's whole record",
          RF.doc_summary("u1", db_path=db)["docA"]["down"] == 2)
    check("an unknown user reads nothing", RF.doc_summary("nobody", 7, db_path=db) == {})
    check("no uuid ⇒ nothing", RF.doc_summary("", 7, db_path=db) == {})


def t_scoping_holds_when_two_users_collide_on_one_key():
    """The case that makes the scoping filter LOAD-BEARING.

    The summary resolves the latest row per (session, turn, document). If that
    resolution is not itself scoped to the user, two users whose rows share that key
    are ranked against EACH OTHER: the later writer wins and the earlier user's own
    vote silently disappears from their reading — or worse, they read a verdict they
    never cast. Fixtures that give each user a different session id never collide, so
    they cannot detect this; this one does.
    """
    db = _tmpdb()
    RF.record("u1", document_ids=["docA"], vote=-1, collection_id=7,
              session_id="shared", turn=1, db_path=db)
    RF.record("u2", document_ids=["docA"], vote=1, collection_id=7,
              session_id="shared", turn=1, db_path=db)   # later id, opposite vote

    s1 = RF.doc_summary("u1", 7, db_path=db)
    s2 = RF.doc_summary("u2", 7, db_path=db)
    check("the earlier voter still reads their OWN downvote",
          s1.get("docA") == {"up": 0, "down": 1, "net": -1})
    check("…and never the other user's upvote", s1.get("docA", {}).get("up") == 0)
    check("the later voter reads their own upvote",
          s2.get("docA") == {"up": 1, "down": 0, "net": 1})
    check("…and never the other user's downvote", s2.get("docA", {}).get("down") == 0)


def t_lowest_rated_is_a_curation_queue():
    db = _tmpdb()
    RF.record("u1", document_ids=["bad"], vote=-1, collection_id=7, session_id="s1", turn=1, db_path=db)
    RF.record("u1", document_ids=["bad"], vote=-1, collection_id=7, session_id="s2", turn=1, db_path=db)
    RF.record("u1", document_ids=["meh"], vote=-1, collection_id=7, session_id="s3", turn=1, db_path=db)
    RF.record("u1", document_ids=["good"], vote=1, collection_id=7, session_id="s4", turn=1, db_path=db)
    low = RF.lowest_rated("u1", 7, db_path=db)
    check("worst first", [r["document_id"] for r in low] == ["bad", "meh"])
    check("a document nobody complained about is NOT a finding",
          all(r["document_id"] != "good" for r in low))
    check("the limit is honoured", len(RF.lowest_rated("u1", 7, limit=1, db_path=db)) == 1)


def t_store_fails_open():
    # The failures below are the POINT of the test and are logged with a warning by
    # design — silenced here so expected noise does not read as suite noise.
    import logging
    lg = logging.getLogger("quart.app")
    prev, lg.disabled = lg.disabled, True
    try:
        check("an unwritable path records nothing rather than raising",
              RF.record("u1", document_ids=["d"], vote=-1,
                        db_path="/nonexistent/dir/x.db") == 0)
        check("…and the summary degrades to empty",
              RF.doc_summary("u1", db_path="/nonexistent/dir/x.db") == {})
    finally:
        lg.disabled = prev
    check("no documents ⇒ no rows, no error",
          RF.record("u1", document_ids=[], vote=-1, db_path=_tmpdb()) == 0)


# ── document-id extraction ────────────────────────────────────────────────────
def t_document_id_extraction():
    check("top-level document_id", C.document_ids([{"document_id": "a"}]) == ["a"])
    check("falls back to id", C.document_ids([{"id": "b"}]) == ["b"])
    check("…then to metadata.document_id",
          C.document_ids([{"metadata": {"document_id": "c"}}]) == ["c"])
    check("document_id wins over id",
          C.document_ids([{"document_id": "a", "id": "b"}]) == ["a"])
    check("an unidentifiable chunk is SKIPPED, never given an invented key",
          C.document_ids([{"text": "no id here"}]) == [])
    check("a non-dict chunk is ignored", C.document_ids(["nope", None]) == [])
    check("no chunks ⇒ nothing", C.document_ids(None) == [])
    # Five chunks of one document must not outvote one chunk of another.
    many = [{"document_id": "a"}, {"document_id": "a"}, {"document_id": "b"}]
    check("one vote per DOCUMENT, however many of its chunks were served",
          C.document_ids(many) == ["a", "b"])


# ── the consumer ──────────────────────────────────────────────────────────────
class _Rec:
    def __init__(self):
        self.calls = []

    def record(self, user_uuid, **kw):
        self.calls.append({"user_uuid": user_uuid, **kw})
        return len(kw.get("document_ids") or [])


class _Ctx:
    def __init__(self, rec, flag=True):
        self.rec, self.flag = rec, flag

    def __enter__(self):
        from trusted_data_agent.core import retrieval_feedback as _rf
        from trusted_data_agent.core.config import APP_CONFIG
        self._orig = _rf.record
        _rf.record = self.rec.record
        self._sig = APP_CONFIG.FEEDBACK_SIGNALS
        APP_CONFIG.FEEDBACK_SIGNALS = dict(self._sig, retrieval_feedback=self.flag)
        return self

    def __exit__(self, *a):
        from trusted_data_agent.core import retrieval_feedback as _rf
        from trusted_data_agent.core.config import APP_CONFIG
        _rf.record = self._orig
        APP_CONFIG.FEEDBACK_SIGNALS = self._sig
        return False


def _event(turn, vote="down"):
    return VoteEvent(user_uuid="u1", session_id="s1", turn_id=2, vote=vote,
                     prev_vote=None, turn=turn, profile_tag="FOCUS",
                     profile_type="rag_focused")


def _focus_turn(n=2):
    return {"knowledge_retrieval_event": {
                "documents": [{"document_id": f"doc{i}"} for i in range(n)]},
            "rag_source_collection_id": 7,
            "user_query": "what is the refund policy"}


def t_consumer_records_served_documents():
    rec = _Rec()
    with _Ctx(rec):
        run(C.retrieval(_event(_focus_turn(3))))
    check("one call carrying every served document", len(rec.calls) == 1)
    c = rec.calls[0] if rec.calls else {}
    check("…all three documents", c.get("document_ids") == ["doc0", "doc1", "doc2"])
    check("…with the vote", c.get("vote") == -1)
    check("…the collection", c.get("collection_id") == 7)
    check("…the query", c.get("query_text") == "what is the refund policy")
    check("…and the turn coordinates", c.get("session_id") == "s1" and c.get("turn") == 2)


def t_consumer_is_engine_agnostic():
    """Keyed on the turn having RETRIEVED, not on profile type — the signal is about
    the documents, not the engine that read them."""
    rec = _Rec()
    with _Ctx(rec):
        for cls in ("rag_focused", "tool_enabled", "llm_only", "conversation_with_tools"):
            run(C.retrieval(VoteEvent(user_uuid="u1", session_id="s1", turn_id=1,
                                      vote="up", prev_vote=None, turn=_focus_turn(1),
                                      profile_type=cls)))
    check("every engine that retrieved records feedback", len(rec.calls) == 4)
    check("…as an upvote where the user said so", all(c["vote"] == 1 for c in rec.calls))


def t_consumer_abstains_without_retrieval():
    rec = _Rec()
    with _Ctx(rec):
        run(C.retrieval(_event({})))                                   # no event at all
        run(C.retrieval(_event({"knowledge_retrieval_event": {"documents": []}})))
        run(C.retrieval(_event({"knowledge_retrieval_event": {
            "documents": [{"text": "chunk with no id"}]}})))
    check("a turn that retrieved nothing records nothing", rec.calls == [])


def t_consumer_flag_off():
    rec = _Rec()
    with _Ctx(rec, flag=False):
        run(C.retrieval(_event(_focus_turn())))
    check("retrieval_feedback off ⇒ nothing recorded", rec.calls == [])


def t_cleared_vote_is_recorded_as_zero():
    rec = _Rec()
    with _Ctx(rec):
        run(C.retrieval(_event(_focus_turn(1), vote=None)))
    check("a retraction is recorded (score 0) so the summary can cancel it",
          len(rec.calls) == 1 and rec.calls[0]["vote"] == 0)


def main():
    for fn in (t_record_and_summarise,
               t_a_flip_is_counted_once,
               t_a_cleared_vote_cancels,
               t_scoping,
               t_scoping_holds_when_two_users_collide_on_one_key,
               t_lowest_rated_is_a_curation_queue,
               t_store_fails_open,
               t_document_id_extraction,
               t_consumer_records_served_documents,
               t_consumer_is_engine_agnostic,
               t_consumer_abstains_without_retrieval,
               t_consumer_flag_off,
               t_cleared_vote_is_recorded_as_zero):
        fn()
    print(f"\ntest_retrieval_feedback: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
