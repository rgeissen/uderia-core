"""
Embedding-cache gate — a cached model loads without asking the hub.

On 2026-11-03 huggingface.co answered 503 for hours; every embedder load retried five
HEADs per model file before falling back to the cache, and the production boot took
136 s to serve a model that was already on disk. The rule (core/embedding_cache.py):
a model whose snapshot is in the local cache loads with ``local_files_only``; an
uncached one downloads as before; decided per model from the cache's own state —
never a process-wide switch. Asserts, CI-pure (a temp cache dir, a stub wrapper):
  A. the repo id a model name resolves to
  B. the cache probe: a complete snapshot → cached; a missing model, a cached 404, a
     snapshot missing its ST layout → not cached; a local directory → cached
  C. the kwargs: offline only when cached; nothing when HF_HUB_OFFLINE already rules
  D. the factory passes the rule to ChromaDB's wrapper (resolved at call time)
  E. every construction site in the platform goes through the factory

Run:
    python test/test_embedding_offline.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


ROOT = Path(__file__).resolve().parents[1]


def _snapshot(cache: Path, repo: str, files, rev="abc123"):
    d = cache / f"models--{repo.replace('/', '--')}"
    snap = d / "snapshots" / rev
    snap.mkdir(parents=True, exist_ok=True)
    (d / "refs").mkdir(exist_ok=True)
    (d / "refs" / "main").write_text(rev)
    for f in files:
        (snap / f).parent.mkdir(parents=True, exist_ok=True)
        (snap / f).write_text("{}")
    return snap


def main():
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td) / "hub"
        cache.mkdir()
        os.environ["HF_HUB_CACHE"] = str(cache)
        os.environ.pop("HF_HUB_OFFLINE", None)
        import importlib
        import huggingface_hub.constants as C
        importlib.reload(C)   # the constants read the env at import
        import huggingface_hub.file_download as FD
        importlib.reload(FD)
        from trusted_data_agent.core import embedding_cache as EC
        importlib.reload(EC)

        print("[A. repo id]")
        check("a bare name lives under sentence-transformers", EC.hf_repo_id("all-mpnet-base-v2") == "sentence-transformers/all-mpnet-base-v2")
        check("a qualified name is already a repo id", EC.hf_repo_id("BAAI/bge-small-en") == "BAAI/bge-small-en")
        check("empty stays empty", EC.hf_repo_id("") == "")

        print("[B. the cache probe]")
        _snapshot(cache, "sentence-transformers/all-mpnet-base-v2", ["config.json", "modules.json", "1_Pooling/config.json"])
        check("a complete snapshot → cached", EC.model_is_cached("all-mpnet-base-v2") is True)
        check("a missing model → not cached", EC.model_is_cached("all-MiniLM-L6-v2") is False)
        _snapshot(cache, "sentence-transformers/half", ["config.json"])
        check("a snapshot without its ST layout → not cached (a partial download never loads offline)", EC.model_is_cached("half") is False)
        check("a local directory → cached (no hub at all)", EC.model_is_cached(td) is True)
        check("nonsense → False, never raises", EC.model_is_cached("") is False and EC.model_is_cached(None) is False)

        print("[C. the kwargs]")
        check("cached → local_files_only", EC.load_kwargs("all-mpnet-base-v2") == {"local_files_only": True})
        check("uncached → nothing (download as before)", EC.load_kwargs("all-MiniLM-L6-v2") == {})
        os.environ["HF_HUB_OFFLINE"] = "1"
        check("HF_HUB_OFFLINE already rules → nothing to add", EC.load_kwargs("all-mpnet-base-v2") == {})
        os.environ.pop("HF_HUB_OFFLINE")

        print("[D. the factory]")
        from chromadb.utils import embedding_functions as ef
        seen = []

        class _Stub:
            def __init__(self, model_name, **kw):
                seen.append((model_name, kw))
        real = ef.SentenceTransformerEmbeddingFunction
        ef.SentenceTransformerEmbeddingFunction = _Stub
        try:
            EC.sentence_transformer_embedding_function("all-mpnet-base-v2")
            EC.sentence_transformer_embedding_function("all-MiniLM-L6-v2", device="cpu")
        finally:
            ef.SentenceTransformerEmbeddingFunction = real
        check("a cached model is constructed offline", seen[0] == ("all-mpnet-base-v2", {"local_files_only": True}))
        check("an uncached model is constructed online, extra kwargs kept", seen[1] == ("all-MiniLM-L6-v2", {"device": "cpu"}))

    print("[E. every site]")
    direct = []
    for f in (ROOT / "src/trusted_data_agent").rglob("*.py"):
        if f.name == "embedding_cache.py":
            continue
        if "SentenceTransformerEmbeddingFunction(" in f.read_text(encoding="utf-8"):
            direct.append(str(f.relative_to(ROOT)))
    check(f"no construction site bypasses the rule ({', '.join(direct) or 'none'})", not direct)
    for f in ("agent/rag_retriever.py", "core/collection_utils.py", "agent/repository_constructor.py", "vectorstore/embedding_providers.py"):
        check(f"{f} uses the factory", "sentence_transformer_embedding_function(" in (ROOT / "src/trusted_data_agent" / f).read_text(encoding="utf-8"))

    print("=" * 70)
    print(f"  {_passed} passed, {_failed} failed  ({_passed + _failed} assertions)")
    print("=" * 70)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
