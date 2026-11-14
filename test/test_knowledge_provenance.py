"""CI-pure tests — RAG Trust Hardening Wave 4: source provenance + the two new scorers.

Covers: `knowledge_provenance.enrich_chunks` (trust state resolution incl. honest
absence, staleness from CDC metadata, sync_off honesty, sensitivity pass-through,
fail-open, per-collection caching), `ContextUtilizationScorer` (used vs ignored
retrieval, abstentions, banding), and `KnowledgeFreshnessScorer` (stalest-doc age,
CDC-absent abstention, banding). Temp DB; no server, 0 tokens.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def _db():
    from trusted_data_agent.core.collection_db import CollectionDatabase
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT)")
    conn.execute("""CREATE TABLE collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, collection_name TEXT,
        mcp_server_id TEXT, enabled INTEGER DEFAULT 1, created_at TEXT,
        description TEXT, owner_user_id TEXT, repository_type TEXT,
        embedding_model TEXT, user_id TEXT, marketplace_tags TEXT,
        marketplace_category TEXT, marketplace_long_description TEXT)""")
    conn.execute("""CREATE TABLE knowledge_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, collection_id INTEGER,
        document_id TEXT UNIQUE, filename TEXT, document_type TEXT, title TEXT,
        author TEXT, source TEXT, category TEXT, tags TEXT, file_size INTEGER,
        page_count INTEGER, content_hash TEXT, created_at TEXT, updated_at TEXT,
        metadata TEXT, source_uri TEXT, ingest_epoch INTEGER,
        sync_enabled INTEGER DEFAULT 0, last_checked_at TEXT, chunk_count INTEGER)""")
    conn.commit()
    conn.close()
    db = CollectionDatabase(path)  # _ensure_columns adds sensitivity/scan/quarantine
    ago9 = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    conn = db._get_connection()
    conn.execute("INSERT INTO collections (id, name, collection_name, created_at, "
                 "repository_type, embedding_model, owner_user_id) "
                 "VALUES (1, 'c', 'cn', 'now', 'knowledge', 'm', 'u1')")
    conn.execute("UPDATE collections SET sensitivity = 'confidential' WHERE id = 1")
    conn.execute("INSERT INTO knowledge_documents (collection_id, document_id, filename, "
                 "content_hash, created_at, last_checked_at, sync_enabled) "
                 "VALUES (1, 'doc-fresh', 'a.txt', 'h', 'now', ?, 1)", (ago9,))
    conn.execute("INSERT INTO knowledge_documents (collection_id, document_id, filename, "
                 "content_hash, created_at, sync_enabled) "
                 "VALUES (1, 'doc-nosync', 'b.txt', 'h', 'now', 0)")
    conn.commit()
    conn.close()
    return db


def t_enrich():
    import trusted_data_agent.core.collection_db as cdb_mod
    from trusted_data_agent.core import knowledge_provenance as kp
    db = _db()
    orig = cdb_mod.get_collection_db
    cdb_mod.get_collection_db = lambda: db
    try:
        chunks = [
            {"content": "x", "collection_id": 1, "document_id": "doc-fresh"},
            {"content": "y", "collection_id": 1, "document_id": "doc-nosync"},
        ]
        out = kp.enrich_chunks(chunks)
        check("staleness from CDC last_checked_at",
              out[0].get("verified_days_ago") == 9 and out[0].get("sync_off") is False)
        check("sync_off honesty", out[1].get("sync_off") is True)
        check("sensitivity passed through",
              out[0].get("sensitivity") == "confidential")
        check("trust state resolved (unsigned in a fresh store)",
              out[0].get("source_trust") in ("unsigned", None))
        check("input not mutated", "verified_days_ago" not in chunks[0])
        # The repository's NAME rides on each chunk (the key every retriever uses for it),
        # never its internal vector-store id — without it the capsule named a document's
        # title or file in the repository's place.
        check("the repository name is stamped, never the internal store id",
              out[0].get("collection_name") == "c" and out[1].get("collection_name") == "c")
        kept = kp.enrich_chunks([{"collection_id": 1, "document_id": "doc-fresh", "collection_name": "As given"}])
        check("…and a name the retriever already set is kept", kept[0].get("collection_name") == "As given")
        # fail-open: enrichment never breaks on garbage
        check("fail-open on malformed chunks",
              kp.enrich_chunks([{"collection_id": "not-an-int"}, "not-a-dict"]) is not None)
        check("empty input → empty output", kp.enrich_chunks([]) == [])
    finally:
        cdb_mod.get_collection_db = orig


def t_context_utilization():
    from trusted_data_agent.eval.scorers.reliability import ContextUtilizationScorer
    from trusted_data_agent.eval.types import RunRecord, EvalCase
    s = ContextUtilizationScorer()
    case = EvalCase(id="t", query="q", profile_class="rag_focused")

    used_turn = {"final_summary_text":
                 "The retention policy keeps records for ninety days per compliance.",
                 "knowledge_retrieval_event": {"chunks": [
                     {"content": "Retention policy: records are kept for ninety days "
                                 "to satisfy compliance requirements."}]}}
    v = s.score(case, RunRecord(used_turn))
    check("answer drawn from chunks scores high", v.score is not None and v.score >= 0.7)

    ignored_turn = {"final_summary_text":
                    "Quantum entanglement enables superluminal correlations across particles.",
                    "knowledge_retrieval_event": {"chunks": [
                        {"content": "Retention policy: records kept ninety days."}]}}
    v = s.score(case, RunRecord(ignored_turn))
    check("retrieved-then-ignored scores low", v.score is not None and v.score <= 0.3)

    v = s.score(case, RunRecord({"final_summary_text": "hello"}))
    check("no retrieval → abstain", v.passed is None and "no retrieved" in v.explanation)

    case_banded = EvalCase(id="t", query="q", profile_class="rag_focused",
                           assertions={"min_context_utilization": 0.5,
                                       "min_context_utilization__healthy": 0.75})
    v = s.score(case_banded, RunRecord(ignored_turn))
    check("banded: ignored retrieval fails the gate", v.passed is False
          and v.band == "danger")
    v = s.score(case_banded, RunRecord(used_turn))
    check("banded: used retrieval passes", v.passed is True)


def t_knowledge_freshness():
    from trusted_data_agent.eval.scorers.reliability import KnowledgeFreshnessScorer
    from trusted_data_agent.eval.types import RunRecord, EvalCase
    s = KnowledgeFreshnessScorer()
    case = EvalCase(id="t", query="q", profile_class="rag_focused")

    turn = {"final_summary_text": "a",
            "knowledge_retrieval_event": {"chunks": [
                {"content": "x", "verified_days_ago": 3, "sync_off": False},
                {"content": "y", "verified_days_ago": 94, "sync_off": True}]}}
    v = s.score(case, RunRecord(turn))
    check("stalest doc drives the metric", v.score == 94.0
          and "94 day(s)" in v.explanation and "sync off" in v.explanation)

    v = s.score(case, RunRecord({"final_summary_text": "a",
                                 "knowledge_retrieval_event": {"chunks": [{"content": "x"}]}}))
    check("no CDC metadata → honest abstain", v.passed is None)

    case_banded = EvalCase(id="t", query="q", profile_class="rag_focused",
                           assertions={"max_knowledge_age_days": 30,
                                       "max_knowledge_age_days__healthy": 7})
    v = s.score(case_banded, RunRecord(turn))
    check("banded: 94d fails a 30d gate", v.passed is False and v.band == "danger")


def t_registered():
    import trusted_data_agent.eval.scorers  # noqa: F401 — populate the registry
    from trusted_data_agent.eval.scorer import scorers_for
    for cls in ("rag_focused", "llm_only", "tool_enabled", "conversation_with_tools"):
        dims = {s.dimension for s in scorers_for(cls)}
        check(f"both dims registered for {cls}",
              "context_utilization" in dims and "knowledge_freshness" in dims)
    dims_genie = {s.dimension for s in scorers_for("genie")}
    check("genie excluded (no direct retrieval)",
          "context_utilization" not in dims_genie)


def main():
    for t in (t_enrich, t_context_utilization, t_knowledge_freshness, t_registered):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
