"""The document registry survives a copy — ids are local, content identity is portable.

Forking a signed agent pack left every expert unsigned. The corpus was the cause: the
import carried the EXPORTING instance's document ids, which collided with
`knowledge_documents.document_id TEXT UNIQUE` (unique across the table, not per
collection), so every registry row was silently discarded by `INSERT OR IGNORE` — while
the populate step counted documents ATTEMPTED and logged success over an empty table.

Four properties, each of which was broken:
  * a document id is minted locally on import (the platform already remaps every other
    identity: profile, collection, knowledge-graph);
  * the registry is therefore written, not silently dropped;
  * `content_hash` — what the knowledge trust signature is taken over — is carried
    VERBATIM from the exporter, because it cannot be reconstructed here;
  * the count is what was written, and a shortfall says so.

CI-pure: no server, no network, a temp SQLite file.
"""
import json, os, sqlite3, sys, tempfile, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_passed = _failed = 0
def check(name, cond, detail=""):
    global _passed, _failed
    if cond: _passed += 1; print(f"  ok  {name}")
    else: _failed += 1; print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))

from trusted_data_agent.core import collection_utils as cu

DDL = """
CREATE TABLE knowledge_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  collection_id INTEGER NOT NULL,
  document_id TEXT UNIQUE NOT NULL,
  filename TEXT NOT NULL,
  document_type TEXT, title TEXT, author TEXT, source TEXT, category TEXT,
  tags TEXT, file_size INTEGER, page_count INTEGER, content_hash TEXT,
  created_at TEXT NOT NULL, updated_at TEXT, metadata TEXT,
  source_uri TEXT, ingest_epoch INTEGER, sync_enabled INTEGER DEFAULT 0,
  last_checked_at TEXT, chunk_count INTEGER DEFAULT 0,
  scan_json TEXT, quarantined INTEGER DEFAULT 0, quarantine_reason TEXT)
"""

def _db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    sqlite3.connect(path).executescript(DDL)
    import trusted_data_agent.auth.database as authdb
    authdb.DATABASE_URL = f"sqlite:///{path}"
    return path

def _chunks(doc_id, n=3, **extra):
    return [{"document_id": doc_id, "chunk_index": i, "filename": "a.md",
             "document_type": "md", "title": "A", **extra} for i in range(n)]

def _rows(path, cid):
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    r = [dict(x) for x in c.execute(
        "SELECT * FROM knowledge_documents WHERE collection_id=?", (cid,))]
    c.close(); return r


def t_remap_is_local_and_stable():
    m = {}
    a = _chunks("SRC-1", 2); b = _chunks("SRC-1", 2); c = _chunks("SRC-2", 1)
    cu._remap_document_ids(a, m); cu._remap_document_ids(b, m); cu._remap_document_ids(c, m)
    new1 = {x["document_id"] for x in a} | {x["document_id"] for x in b}
    check("one source document keeps ONE new id across batches", len(new1) == 1)
    check("the new id is not the source id", new1 != {"SRC-1"})
    check("a second document gets a DIFFERENT id",
          c[0]["document_id"] not in new1)
    def _is_uuid(v):
        try: uuid.UUID(str(v)); return True
        except (ValueError, AttributeError, TypeError): return False
    check("the new id is a uuid", _is_uuid(list(new1)[0]), str(list(new1)[0]))
    # A chunk with no document_id is left alone rather than invented for.
    bare = [{"chunk_index": 0}]
    cu._remap_document_ids(bare, m)
    check("a chunk without a document_id is untouched", "document_id" not in bare[0])


def t_same_corpus_into_two_collections():
    """THE regression: importing one corpus twice must populate BOTH registries."""
    path = _db()
    src = "SRC-DOC"
    reg = {src: {"filename": "policy.md", "content_hash": "REALHASH", "title": "Policy",
                 "document_type": "md", "author": "", "source": "", "category": "",
                 "tags": "", "file_size": 42, "page_count": 1, "chunk_count": 3,
                 "ingest_epoch": 111}}
    for cid in (1, 2):
        m = {}
        metas = _chunks(src, 3)
        cu._remap_document_ids(metas, m)
        n = cu._populate_knowledge_documents(cid, metas, registry=reg, doc_id_map=m)
        check(f"collection {cid}: one document written", n == 1, f"got {n}")
    r1, r2 = _rows(path, 1), _rows(path, 2)
    check("both collections have a registry row", len(r1) == 1 and len(r2) == 1,
          f"{len(r1)} / {len(r2)}")
    check("their document ids DIFFER (identity is local)",
          r1[0]["document_id"] != r2[0]["document_id"])
    check("their content_hash is IDENTICAL (identity is portable)",
          r1[0]["content_hash"] == r2[0]["content_hash"] == "REALHASH")
    check("registry fields are used, not chunk metadata",
          r1[0]["filename"] == "policy.md" and r1[0]["file_size"] == 42)
    os.unlink(path)


def t_no_registry_leaves_hash_unset():
    """A fabricated constant hash is worse than none — the CDC diff would read two
    different documents as identical."""
    path = _db()
    m = {}
    a = _chunks("S1", 2); b = _chunks("S2", 2)
    cu._remap_document_ids(a, m); cu._remap_document_ids(b, m)
    cu._populate_knowledge_documents(3, a + b, registry=None, doc_id_map=m)
    rows = _rows(path, 3)
    check("both documents written without a registry", len(rows) == 2, f"got {len(rows)}")
    check("content_hash is NULL, not a fabricated constant",
          all(r["content_hash"] is None for r in rows),
          str([r["content_hash"] for r in rows]))
    check("filename still derived from chunk metadata",
          all(r["filename"] == "a.md" for r in rows))
    os.unlink(path)


def t_count_is_what_was_written():
    """The lie that hid this: `INSERT OR IGNORE` is silent, so a count of documents
    ATTEMPTED reports success over an empty table."""
    path = _db()
    m = {}
    metas = _chunks("S1", 2)
    cu._remap_document_ids(metas, m)
    check("first import writes 1", cu._populate_knowledge_documents(4, metas, doc_id_map=m) == 1)
    # Re-run the SAME ids: every insert is now ignored, and the count must say 0.
    n = cu._populate_knowledge_documents(5, metas, doc_id_map={})
    check("a fully-ignored insert reports 0, not the attempted count", n == 0, f"got {n}")
    check("and nothing was written for that collection", len(_rows(path, 5)) == 0)
    os.unlink(path)


def t_registry_and_vectors_agree():
    """Document deletion and the CDC diff both join registry to chunks on document_id."""
    path = _db()
    m = {}
    metas = _chunks("SRC", 4)
    cu._remap_document_ids(metas, m)
    cu._populate_knowledge_documents(6, metas, doc_id_map=m)
    row = _rows(path, 6)[0]
    chunk_ids = {c["document_id"] for c in metas}
    check("the registry row's id is the id written on every chunk",
          chunk_ids == {row["document_id"]}, f"{chunk_ids} vs {row['document_id']}")
    check("chunk_count reflects the chunks seen", row["chunk_count"] == 4)
    os.unlink(path)


def t_trust_hash_of_a_copy_equals_the_source():
    """The property the whole change exists for: `knowledge_content` signs sorted
    content_hash values, so a faithful copy hashes identically and can reinstate."""
    path = _db()
    reg = {f"SRC-{i}": {"filename": f"d{i}.md", "content_hash": f"H{i}",
                        "document_type": "md", "title": "", "author": "", "source": "",
                        "category": "", "tags": "", "file_size": 1, "page_count": 1,
                        "chunk_count": 1, "ingest_epoch": 1} for i in range(3)}
    for cid in (7, 8):
        m = {}
        metas = []
        for i in range(3):
            ch = _chunks(f"SRC-{i}", 1)
            metas += ch
        cu._remap_document_ids(metas, m)
        cu._populate_knowledge_documents(cid, metas, registry=reg, doc_id_map=m)
    h7 = sorted((r["content_hash"] or "") for r in _rows(path, 7))
    h8 = sorted((r["content_hash"] or "") for r in _rows(path, 8))
    check("the signed content (sorted content hashes) is identical", h7 == h8 and h7 == ["H0","H1","H2"],
          f"{h7} vs {h8}")
    ids7 = {r["document_id"] for r in _rows(path, 7)}
    ids8 = {r["document_id"] for r in _rows(path, 8)}
    check("while the local identities are disjoint", not (ids7 & ids8))
    os.unlink(path)



def t_scan_record_and_quarantine_travel():
    """A signature means "integrity AND screened", so the firewall record travels — and
    the quarantine set travels for a stronger reason: it is the publisher's exclusion
    decision, and a copy that silently un-quarantined a document would drop a control."""
    path = _db()
    reg = {"S1": {"filename": "a.md", "content_hash": "H1",
                  "scan_json": '{"injection": ["obey"], "pii": {"email": 2}}',
                  "quarantined": 1, "quarantine_reason": "planted instruction"},
           "S2": {"filename": "b.md", "content_hash": "H2", "scan_json": '{"pii": {}}',
                  "quarantined": 0, "quarantine_reason": None}}
    m = {}
    metas = _chunks("S1", 1) + _chunks("S2", 1)
    cu._remap_document_ids(metas, m)
    cu._populate_knowledge_documents(9, metas, registry=reg, doc_id_map=m)
    rows = {r["content_hash"]: r for r in _rows(path, 9)}
    check("the scan record is restored", rows["H1"]["scan_json"] is not None)
    check("a quarantined document arrives quarantined", rows["H1"]["quarantined"] == 1)
    check("with its reason", rows["H1"]["quarantine_reason"] == "planted instruction")
    check("a clean document is not quarantined", rows["H2"]["quarantined"] == 0)
    os.unlink(path)


def t_scan_report_hash_is_content_keyed():
    """THE property that lets a copy reinstate: the scan report describes content and
    findings, so two byte-identical corpora must hash identically even though every
    local document id differs."""
    path = _db()
    from trusted_data_agent.core.collection_db import CollectionDatabase
    reg = {f"S{i}": {"filename": f"d{i}.md", "content_hash": f"H{i}",
                     "scan_json": '{"injection": [], "pii": {"email": %d}}' % i,
                     "quarantined": 1 if i == 0 else 0,
                     "quarantine_reason": "flagged" if i == 0 else None} for i in range(3)}
    for cid in (10, 11):
        m = {}
        metas = []
        for i in range(3):
            metas += _chunks(f"S{i}", 1)
        cu._remap_document_ids(metas, m)
        cu._populate_knowledge_documents(cid, metas, registry=reg, doc_id_map=m)
    db = CollectionDatabase(db_path=path)
    a, b = db.get_scan_manifest(10), db.get_scan_manifest(11)
    ids_a = {r["document_id"] for r in _rows(path, 10)}
    ids_b = {r["document_id"] for r in _rows(path, 11)}
    check("the two copies share no local document id", not (ids_a & ids_b))
    check("both report the same coverage", a["scanned"] == b["scanned"] == 3,
          f"{a.get('scanned')} / {b.get('scanned')}")
    check("scan_report_hash is IDENTICAL across the copies",
          a["scan_report_hash"] == b["scan_report_hash"],
          f"{a['scan_report_hash'][:12]} vs {b['scan_report_hash'][:12]}")
    check("the quarantine entry carries a portable content key",
          all(q.get("content_hash") for q in a["quarantined"]),
          str(a["quarantined"]))
    # A real change in findings must still move the hash, or it would sign nothing.
    reg2 = dict(reg); reg2["S1"] = {**reg["S1"], "scan_json": '{"injection": ["obey"]}'}
    m = {}; metas = []
    for i in range(3): metas += _chunks(f"S{i}", 1)
    cu._remap_document_ids(metas, m)
    cu._populate_knowledge_documents(12, metas, registry=reg2, doc_id_map=m)
    c = db.get_scan_manifest(12)
    check("a changed finding DOES move the hash",
          c["scan_report_hash"] != a["scan_report_hash"])
    os.unlink(path)



def t_export_and_import_agree_on_the_field_set():
    """The two halves of the registry are a pair. A field the EXPORT forgets is one the
    IMPORT silently substitutes a default for — which is how a corpus arrives looking
    populated while hashing differently from its source. Rather than a fixture per field
    (which is what let a dropped field escape once), assert the invariant: every field the
    import reads must be one the export writes."""
    import ast, inspect
    src = inspect.getsource(cu._populate_knowledge_documents)
    tree = ast.parse(src.lstrip())
    read = set()
    for n in ast.walk(tree):
        # entry.get("x")  /  _f("x", …)
        if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
           and isinstance(n.args[0].value, str):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == "get" and \
               isinstance(f.value, ast.Name) and f.value.id == "entry":
                read.add(n.args[0].value)
            elif isinstance(f, ast.Name) and f.id == "_f":
                read.add(n.args[0].value)
    check("the import reads a non-trivial field set", len(read) >= 8, str(sorted(read)))
    missing = sorted(read - set(cu._REGISTRY_FIELDS))
    check("every field the import reads is one the export writes", not missing,
          f"exported set is missing {missing}")
    # and the fields that carry the signature are actually in it
    for f in ("content_hash", "scan_json", "quarantined", "quarantine_reason"):
        check(f"the export carries {f}", f in cu._REGISTRY_FIELDS)


def main():
    print("=" * 70); print("Document registry — a corpus survives being copied"); print("=" * 70)
    for t in (t_remap_is_local_and_stable, t_same_corpus_into_two_collections,
              t_no_registry_leaves_hash_unset, t_count_is_what_was_written,
              t_registry_and_vectors_agree, t_trust_hash_of_a_copy_equals_the_source,
              t_scan_record_and_quarantine_travel, t_scan_report_hash_is_content_keyed,
              t_export_and_import_agree_on_the_field_set):
        t()
    print(f"\n{_passed} passed, {_failed} failed  ({_passed+_failed} assertions)")
    return 1 if _failed else 0

if __name__ == "__main__":
    sys.exit(main())
