"""Imported documents carry instance-local ids on BOTH import paths (2026-09-04).

`knowledge_documents.document_id` is unique across the table. The fresh-install path minted
its own ids (randomly); the in-place update kept the exporting instance's ids, so on an
instance hosting both a publisher and a subscriber the subscriber's rows hit
ON CONFLICT(document_id) and updated the PUBLISHER's rows — observed live: 20 publisher rows
re-stamped, zero rows for the copy, the copy's chunks written twice. Now ONE deterministic
derivation keyed on the target vector collection's name serves both paths; the in-place
path sweeps chunks left under a source id; the plain upload route refreshes a filename in
place instead of duplicating it. CI-pure."""
import ast, asyncio, os, pathlib, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core import collection_utils as cu
from trusted_data_agent.core.agent_pack_manager import AgentPackManager as M

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

def t_derivation():
    a = cu.local_document_id("col_A", "src-1"); b = cu.local_document_id("col_A", "src-1")
    check("deterministic in (scope, source id)", a == b and len(a) == 36, (a, b))
    check("a different scope yields a different id — publisher and subscriber never share one", cu.local_document_id("col_B", "src-1") != a, None)
    check("a different source yields a different id", cu.local_document_id("col_A", "src-2") != a, None)
    check("never the source id itself", a != "src-1", None)
    m = cu._LocalIdMap("col_A")
    got = m.setdefault("src-1", "IGNORED-RANDOM-DEFAULT")
    check("the map ignores the caller's random default and derives", got == a and m["src-1"] == a, got)
    check("the map is stable across calls", m.setdefault("src-1", "other") == a, None)
    metas = [{"document_id": "src-1", "x": 1}, {"document_id": "src-2"}, {"no": "doc"}]
    cu._remap_document_ids(metas, m)
    check("_remap_document_ids writes the derived ids through the map", metas[0]["document_id"] == a and metas[1]["document_id"] == cu.local_document_id("col_A", "src-2") and "document_id" not in metas[2], metas)

def t_localizer():
    incoming = {"src-1": {"filename": "a.md", "content_hash": "h1", "chunks": [
        {"chunk_id": "src-1_chunk_0_abc", "content": "c0", "metadata": {"document_id": "src-1", "chunk_index": 0}, "embedding": [0.1]},
        {"chunk_id": "src-1_chunk_1_def", "content": "c1", "metadata": {"document_id": "src-1", "chunk_index": 1}, "embedding": None}]}}
    out = M._localize_incoming(incoming, "col_A")
    local = cu.local_document_id("col_A", "src-1")
    check("the map is re-keyed to the local id", list(out) == [local], list(out))
    check("the source id is kept on the entry", out[local]["source_document_id"] == "src-1", out[local].keys())
    check("every chunk's metadata document_id is the local id", all(c["metadata"]["document_id"] == local for c in out[local]["chunks"]), None)
    check("chunk ids are rewritten under the local id", [c["chunk_id"] for c in out[local]["chunks"]] == [f"{local}_chunk_0_abc", f"{local}_chunk_1_def"], [c["chunk_id"] for c in out[local]["chunks"]])
    check("content, embeddings and hash survive", out[local]["chunks"][0]["content"] == "c0" and out[local]["chunks"][0]["embedding"] == [0.1] and out[local]["content_hash"] == "h1", None)
    check("the input is not mutated", incoming["src-1"]["chunks"][0]["metadata"]["document_id"] == "src-1" and incoming["src-1"]["chunks"][0]["chunk_id"] == "src-1_chunk_0_abc", None)
    check("empty input is empty output", M._localize_incoming({}, "col_A") == {}, None)

class FakeBackend:
    def __init__(self, chunks_by_doc):  # {doc_id: [chunk ids]}
        self.store = {cid: d for d, cids in chunks_by_doc.items() for cid in cids}; self.added = []; self.deleted = []
    async def get(self, coll, where=None, include_documents=False, include_metadata=False, limit=None):
        want = where.value if hasattr(where, "value") else None
        class R: pass
        r = R()
        r.documents = [type("D", (), {"id": cid, "metadata": {"document_id": d}})() for cid, d in self.store.items() if where is None or d == want]
        return r
    async def delete(self, coll, ids):
        self.deleted.extend(ids); [self.store.pop(i, None) for i in ids]
    async def add(self, coll, docs):
        for d in docs: self.store[d.id] = d.metadata.get("document_id"); self.added.append(d.id)

def t_sweep():
    """The patch sweeps chunks left under a SOURCE id (the pre-fix collision) before adding."""
    import types
    from trusted_data_agent.core import agent_pack_manager as apm
    local = cu.local_document_id("col_A", "src-1")
    incoming = {local: {"filename": "a.md", "content_hash": "h", "document_type": "md", "title": "A", "author": "", "category": "", "tags": "",
                        "source_document_id": "src-1", "chunks": [{"chunk_id": f"{local}_chunk_0_x", "content": "c", "metadata": {"document_id": local, "chunk_index": 0}, "embedding": [0.1]}]}}
    # "other" is an OWNED document (a row exists); "aug-old" is an orphan from a previous
    # publisher id — no row owns it, and it is not among the ids this update carries
    be = FakeBackend({"src-1": ["src-1_chunk_0_x", "src-1_chunk_1_y"], "other": ["o_chunk_0"], "aug-old": ["old_chunk_0", "old_chunk_1"]})
    rows = []
    class FakeCollDb:
        def upsert_document_metadata(self, **kw): rows.append(kw)
        def upsert_document_metadata_preserving_cdc(self, **kw): rows.append(kw)
        def _get_connection(self): raise AssertionError("no rows to remove in this case")
    class FakeRetriever:
        async def _get_knowledge_backend(self, cid): return be
        def get_collection_metadata(self, cid): return {"collection_name": "col_A"}
    m = M.__new__(M)
    orig = apm.get_collection_db if hasattr(apm, "get_collection_db") else None
    import trusted_data_agent.core.collection_db as cdb
    saved = cdb.get_collection_db; cdb.get_collection_db = lambda: FakeCollDb()
    try:
        res = asyncio.run(m._diff_and_patch_collection(7, {"collection_name": "col_A"}, pathlib.Path("/nonexistent"), incoming, {"other": {"document_id": "other"}}, [local], [], [], FakeRetriever()))
    finally:
        cdb.get_collection_db = saved
    check("chunks left under the source id are swept", "src-1_chunk_0_x" in be.deleted and "src-1_chunk_1_y" in be.deleted, be.deleted)
    check("chunks of an OWNED document (a row exists) are untouched", "o_chunk_0" in be.store, be.store)
    check("orphan chunks under a PREVIOUS publisher id — no row, not carried by this update — are swept too", "old_chunk_0" in be.deleted and "old_chunk_1" in be.deleted, be.deleted)
    check("the localized chunk is added under the local id", be.store.get(f"{local}_chunk_0_x") == local, be.store)
    check("the row is written under the LOCAL id (never the publisher's)", rows and rows[0]["document_id"] == local, rows)
    check("counts report the add", res["added"] == 1, res)

SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent"
def _calls_in(path, fn):
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            return {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")) for c in ast.walk(n) if isinstance(c, ast.Call)}
    return None

def t_wiring():
    import inspect as _i
    # The decorator trap: a helper inserted between @staticmethod and the def it decorated
    # silently turns the original into an instance method ("takes 1 positional argument
    # but 2 were given" — seen live on the first deploy). Pin both.
    for _n in ("_read_collection_zip_documents", "_localize_incoming"):
        check(f"{_n} is a staticmethod", isinstance(_i.getattr_static(M, _n), staticmethod), type(_i.getattr_static(M, _n)))
    check("the in-place path localizes what it reads from the ZIP", "_localize_incoming" in (_calls_in(SRC / "core/agent_pack_manager.py", "_handle_update_in_place") or set()), None)
    src = (SRC / "core/collection_utils.py").read_text()
    check("the fresh import seeds its id map with the deterministic derivation", "doc_id_map: dict = _LocalIdMap(new_collection_name)" in src, None)
    check("no random document id is minted for an imported copy any more", "doc_id_map.setdefault(str(src), str(uuid.uuid4()))" in src and "_LocalIdMap" in src, "the setdefault default must be overridden by the map, not by each call site")
    calls = _calls_in(SRC / "api/knowledge_routes.py", "upload_knowledge_document") or set()
    check("the plain upload route looks up an existing document by filename", "get_document_by_filename" in calls, sorted(calls)[:8])
    rs = (SRC / "api/knowledge_routes.py").read_text()
    i = rs.index("async def upload_knowledge_document("); j = rs.find("@knowledge_api_bp.route", i); body = rs[i:j if j > 0 else None]
    check("an unchanged file is skipped, not re-embedded", '"status": "unchanged"' in body, None)
    check("a changed file reuses its document id on both chunking paths", "document_id = _reuse_document_id or str(uuid4())" in body and '"document_id": _reuse_document_id' in body, None)
    check("the previous version's chunks are cleared before the new ingest", body.index("await _b.delete(_cn, _stale)") < body.index("Server-side chunking path"), None)


def t_registry_hash():
    """The export registry's byte hash is authoritative when chunks carry no source_content_hash."""
    import zipfile, json, tempfile
    tmp = pathlib.Path(tempfile.mkdtemp()) / "c.zip"
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("documents.jsonl", json.dumps({"ids": ["d1_chunk_0_a"], "documents": ["hello"], "metadatas": [{"document_id": "d1", "chunk_index": 0, "filename": "a.md"}], "embeddings": [None]}) + "\n")
        zf.writestr("document_registry.json", json.dumps({"d1": {"filename": "a.md", "content_hash": "BYTEHASH"}}))
    got = M._read_collection_zip_documents(tmp)
    check("the registry hash is used", got["d1"]["content_hash"] == "BYTEHASH", got)
    check("and it counts as authoritative (no conservative re-ingest)", got["d1"]["has_source_content_hash"] is True, got)
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr("documents.jsonl", json.dumps({"ids": ["d1_chunk_0_a"], "documents": ["hello"], "metadatas": [{"document_id": "d1", "chunk_index": 0}], "embeddings": [None]}) + "\n")
    got = M._read_collection_zip_documents(tmp)
    check("without registry or stamp: the chunk-text fallback, flagged non-authoritative", got["d1"]["has_source_content_hash"] is False and len(got["d1"]["content_hash"]) == 64, got)
    rs = (SRC / "api/knowledge_routes.py").read_text()
    check("the plain route's row insert records chunk_count", "created_at, chunk_count)" in rs, None)

for fn in (t_derivation, t_localizer, t_sweep, t_registry_hash, t_wiring):
    try: fn()
    except Exception as exc:
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
