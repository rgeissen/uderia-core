"""CI-pure tests — RAG Trust Hardening Wave 2 §2.3: quarantine — detection to disposition.

Covers: set/unset round-trip with reason, the retrieval-exclusion set (per-collection
scoping, fail-open), visibility (quarantined docs stay listed with reason), the retrieval
seam's filter logic (the exact predicate `retrieve_examples` applies), and manifest
integration. Temp DB; no server, no vector store, 0 tokens.
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.collection_db import CollectionDatabase  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0


def check(label, cond):
    global PASS_COUNT, FAIL_COUNT
    if cond:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def _db() -> CollectionDatabase:
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
    for cid in (1, 2):
        conn.execute("INSERT INTO collections (id, name, collection_name, created_at, "
                     "repository_type, owner_user_id) VALUES (?, ?, ?, 'now', 'knowledge', 'u1')",
                     (cid, f"c{cid}", f"cn{cid}"))
    for cid, doc in ((1, "a"), (1, "b"), (2, "c")):
        conn.execute("INSERT INTO knowledge_documents (collection_id, document_id, "
                     "filename, content_hash, created_at) VALUES (?, ?, ?, 'h', 'now')",
                     (cid, f"doc-{doc}", f"{doc}.txt"))
    conn.commit()
    conn.close()
    return CollectionDatabase(path)


def t_quarantine_roundtrip():
    db = _db()
    check("quarantine sets flag + reason",
          db.set_document_quarantine("doc-a", True, "poisoned chunk detected"))
    d = db.get_document_by_id("doc-a")
    check("doc stays visible with reason (Q1)",
          d["quarantined"] == 1 and d["quarantine_reason"] == "poisoned chunk detected")
    check("exclusion set contains it", db.get_quarantined_doc_ids() == {"doc-a"})
    check("reversible", db.set_document_quarantine("doc-a", False)
          and db.get_quarantined_doc_ids() == set()
          and db.get_document_by_id("doc-a")["quarantine_reason"] is None)
    check("unknown doc → False", db.set_document_quarantine("nope", True, "x") is False)


def t_exclusion_scoping():
    db = _db()
    db.set_document_quarantine("doc-a", True, "r1")
    db.set_document_quarantine("doc-c", True, "r2")
    check("global set", db.get_quarantined_doc_ids() == {"doc-a", "doc-c"})
    check("collection-scoped set", db.get_quarantined_doc_ids({1}) == {"doc-a"}
          and db.get_quarantined_doc_ids({2}) == {"doc-c"})
    check("empty scope treated as all (defensive)",
          db.get_quarantined_doc_ids(None) == {"doc-a", "doc-c"})
    db.db_path = "/nonexistent/x.db"  # break the handle after construction
    check("fail-open on a broken DB", db.get_quarantined_doc_ids() == set())


def t_retrieval_seam_predicate():
    """The exact filter retrieve_examples applies: drop candidates whose document_id is
    in the exclusion set — chunks without a document_id are never dropped (fail-open)."""
    db = _db()
    db.set_document_quarantine("doc-a", True, "r")
    quarantined = db.get_quarantined_doc_ids({1, 2})
    candidates = [
        {"document_id": "doc-a", "collection_id": 1, "content": "poisoned"},
        {"document_id": "doc-b", "collection_id": 1, "content": "fine"},
        {"document_id": None, "collection_id": 2, "content": "no-id chunk"},
        {"collection_id": 2, "content": "missing key"},
    ]
    kept = [c for c in candidates
            if str(c.get("document_id") or "") not in quarantined]
    check("quarantined chunk dropped", all(c.get("document_id") != "doc-a" for c in kept))
    check("clean + id-less chunks kept (never over-drop)", len(kept) == 3)


def t_manifest_integration():
    db = _db()
    db.set_document_quarantine("doc-b", True, "owner action")
    m = db.get_scan_manifest(1)
    check("manifest lists quarantined docs with reasons",
          len(m["quarantined"]) == 1
          and m["quarantined"][0]["document_id"] == "doc-b"
          and m["quarantined"][0]["reason"] == "owner action")
    h1 = m["scan_report_hash"]
    db.set_document_quarantine("doc-b", False)
    check("manifest hash independent of quarantine (quarantine binds via content.py, "
          "scan hash covers scans only)", db.get_scan_manifest(1)["scan_report_hash"] == h1)


def main():
    for t in (t_quarantine_roundtrip, t_exclusion_scoping,
              t_retrieval_seam_predicate, t_manifest_integration):
        t()
    print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
    return 1 if FAIL_COUNT else 0


if __name__ == "__main__":
    sys.exit(main())
