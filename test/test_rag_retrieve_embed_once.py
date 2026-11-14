"""
Regression gate — planner-repository retrieval cost is independent of how many
collections are loaded (Aug 2026, prod: ~27 s per turn on the event loop).

Two properties of RAGRetriever.retrieve_examples:
  1. The query is embedded ONCE per embedding model and handed to ChromaDB as a
     vector (`query_embeddings`) — never re-embedded per collection via `query_texts`.
  2. The synchronous ChromaDB calls (encode + query) run OFF the event loop
     (asyncio.to_thread), so a slow store can never freeze every other session.
Plus: with an access context only the accessible collections are queried, and an
embedding failure degrades to per-collection `query_texts` (never to no retrieval).

Run:  PYTHONPATH=src python test/test_rag_retrieve_embed_once.py
"""
import ast
import asyncio
import os
import sys
import threading
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.agent.rag_retriever import RAGRetriever  # noqa: E402

_passed = 0


def check(name, cond, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        print(f"\n{_passed} passed, 1 failed  — {name}")
        sys.exit(1)  # battery exit-code contract: a failing suite must exit non-zero


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


MAIN = threading.main_thread()


class _FakeEF:
    """Records every encode call and the thread it ran on."""
    def __init__(self):
        self.calls = []
        self.fail = False

    def __call__(self, texts):
        self.calls.append((list(texts), threading.current_thread() is MAIN))
        if self.fail:
            raise RuntimeError("encoder down")
        return [[0.1, 0.2, 0.3] for _ in texts]


class _FakeCollection:
    def __init__(self, hits=0):
        self.queries = []
        self.hits = hits

    def query(self, **kw):
        self.queries.append((kw, threading.current_thread() is MAIN))
        n = self.hits
        return {"ids": [[f"c{i}" for i in range(n)]],
                "metadatas": [[{"user_query": f"q{i}", "strategy_type": "successful",
                                "full_case_data": '{"metadata": {}}', "is_most_efficient": True}
                               for i in range(n)]],
                "distances": [[0.1] * n],
                "documents": [["d"] * n]}


def _make_retriever(n_collections, ef, model="all-MiniLM-L6-v2", hits=0):
    r = types.SimpleNamespace()
    r.collections = {i: _FakeCollection(hits=hits) for i in range(1, n_collections + 1)}
    r.embedding_model_name = model
    r._get_embedding_function = lambda name: ef
    r.get_collection_metadata = lambda cid: {"repository_type": "planner", "embedding_model": model}
    r.retrieve_examples = types.MethodType(RAGRetriever.retrieve_examples, r)
    return r


class _Ctx:
    def __init__(self, accessible):
        self.accessible_collections = set(accessible)
        self.user_id = "u"

    def build_query_filter(self, collection_id, extra_filter=None, **conditions):
        return {"$and": [{"k": {"$eq": 1}}]}


print("=== retrieve_examples: embed once, off-loop, scoped ===")

# 1. 50 collections, no context: ONE encode, every query carries the vector.
ef = _FakeEF()
r = _make_retriever(50, ef)
out = run(r.retrieve_examples(query="how many databases", k=3, repository_type="planner"))
check("50 collections -> query embedded exactly once", len(ef.calls) == 1, str(len(ef.calls)))
check("encode ran OFF the event-loop thread", ef.calls and ef.calls[0][1] is False)
qs = [c.queries for c in r.collections.values()]
check("every collection queried once", all(len(q) == 1 for q in qs))
check("queries carry query_embeddings, never query_texts",
      all("query_embeddings" in q[0][0] and "query_texts" not in q[0][0] for q in qs))
check("chroma query ran OFF the event-loop thread", all(q[0][1] is False for q in qs))
check("over-fetch + include preserved",
      all(q[0][0].get("n_results", 0) >= 3 and "metadatas" in q[0][0].get("include", []) for q in qs))
check("empty result -> []", out == [])

# 2. With an access context only accessible collections are touched (the scope IS the cost bound).
ef = _FakeEF()
r = _make_retriever(50, ef)
run(r.retrieve_examples(query="q", k=3, repository_type="planner", rag_context=_Ctx({2, 7})))
touched = [cid for cid, c in r.collections.items() if c.queries]
check("only accessible collections queried", sorted(touched) == [2, 7], str(touched))
check("still a single encode", len(ef.calls) == 1)

# 2b. The allowed set is matched by VALUE, whatever the id type (prod 727292c3, Oct 2026).
# The grounding gate's second-chance retrieval passed `{'272'}` (strings) while the
# retriever's targets are keyed by int, so every collection was skipped and the path
# read "No candidate cases found" on every turn — silently dead for every profile.
ef = _FakeEF()
r = _make_retriever(10, ef)
run(r.retrieve_examples(query="q", k=3, repository_type="planner", allowed_collection_ids={"2", "7"}))
touched = [cid for cid, c in r.collections.items() if c.queries]
check("string ids select the int-keyed collections", sorted(touched) == [2, 7], str(touched))
ef = _FakeEF()
r = _make_retriever(10, ef)
run(r.retrieve_examples(query="q", k=3, repository_type="planner", allowed_collection_ids={"2", 7},
                        rag_context=_Ctx({2, 7, 9})))
touched = [cid for cid, c in r.collections.items() if c.queries]
check("mixed-type allowed ids intersect the access context by value", sorted(touched) == [2, 7], str(touched))

# 3. Encoder failure degrades to per-collection query_texts — never to silence.
ef = _FakeEF(); ef.fail = True
r = _make_retriever(3, ef, hits=1)
out = run(r.retrieve_examples(query="q", k=3, min_score=0.0, repository_type="planner"))
check("encoder failure -> queries fall back to query_texts",
      all("query_texts" in c.queries[0][0] for c in r.collections.values()))
check("encoder failure is attempted once, not per collection", len(ef.calls) == 1, str(len(ef.calls)))
check("fallback still returns candidates", len(out) >= 1, str(len(out)))

# 4. Structural: no synchronous `collection.query(` remains inside retrieve_examples.
src_path = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "agent", "rag_retriever.py")
tree = ast.parse(open(src_path).read())
fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "retrieve_examples")
sync_query_calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "query"
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "collection"]
check("no synchronous collection.query(...) call in retrieve_examples (must go through to_thread)",
      len(sync_query_calls) == 0, str(len(sync_query_calls)))
to_thread = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "to_thread"]
check("retrieve_examples uses asyncio.to_thread", len(to_thread) >= 2, str(len(to_thread)))


# ── content-identical candidates never take a second slot (2026-09-04) ──────────
# A consumer store held the same chunk twice under two document ids; every duplicate
# that reached the model's k slots displaced a distinct chunk (the release notes asked
# for ranked 20th behind seven duplicate pairs). Keyed on chunk TEXT, whatever the ids.
class _DupCollection(_FakeCollection):
    def query(self, **kw):
        n = kw.get("n_results", 3)
        self.calls.append(kw) if hasattr(self, "calls") else None
        texts = ["same text", "same text", "different text"]
        return {"ids": [[f"c{i}" for i in range(3)]],
                "metadatas": [[{"user_query": f"q{i}", "strategy_type": "successful",
                                "full_case_data": '{"metadata": {}, "content": "%s"}' % texts[i],
                                "is_most_efficient": True} for i in range(3)]],
                "distances": [[0.1, 0.1, 0.2]],
                "documents": [texts]}

_r = _make_retriever(1, _FakeEF(), hits=3)
for _cid in list(getattr(_r, "collections", {}) or {}):
    _r.collections[_cid] = _DupCollection(hits=3)
# the KNOWLEDGE branch: a candidate's content is the chunk text
_r.get_collection_metadata = lambda cid: {"repository_type": "knowledge", "embedding_model": "all-MiniLM-L6-v2"}
_r._is_chromadb_backend = lambda meta: True   # the fakes are Chroma-shaped; no second backend loop
_r._knowledge_backends = {}
_out = run(_r.retrieve_examples(query="q", k=3, min_score=0.0, repository_type="knowledge"))
_texts = [str(c.get("content") or c.get("document") or "") for c in _out]
check("a content-identical duplicate never takes a second slot", _texts.count("same text") <= 1, _texts)
check("a distinct candidate survives the dedup", "different text" in _texts, _texts)
# the base fake carries NO text for any candidate: three distinct planner cases with empty
# content must all survive — an empty text is not a duplicate of another empty text
_r2 = _make_retriever(1, _FakeEF(), hits=3)
_out2 = run(_r2.retrieve_examples(query="q", k=3, min_score=0.0, repository_type="planner"))
check("candidates with no text are never folded together (planner cases keep their plans)", len(_out2) == 3, len(_out2))

print(f"\n{_passed} passed, 0 failed  ({_passed} assertions)")
