#!/usr/bin/env python3
"""
Build uderia_expert.agentpack from all Uderia platform documentation.

Phases:
  1. Scan  — docs/**/*.md + README.md, filtered by corpus.json (unclassified → error)
  2. Chunk — RecursiveCharacterTextSplitter(1000/200)
  3. Embed — SentenceTransformer("all-MiniLM-L6-v2")
  4. Pack  — collection ZIP (collection_metadata.json + documents.jsonl)
  5. Manifest — format_version 1.1, single rag_focused profile
  6. Bundle — uderia_expert.agentpack
  7. Import (--import) — POST to platform + PATCH source_uri/sync_enabled per doc

Usage:
    # Build only
    python agent_packs/uderia_expert/build.py

    # Build + auto-import + wire CDC sync
    UDERIA_USERNAME=admin UDERIA_PASSWORD=admin \\
      python agent_packs/uderia_expert/build.py --import

    # Force full rebuild (re-embeds everything)
    python agent_packs/uderia_expert/build.py --import --force

    # Verify a republish against production before it commits anything
    python agent_packs/uderia_expert/build.py --import --dry-run

    # Import, then sign bottom-up and re-sync marketplace subscribers (one pass)
    python agent_packs/uderia_expert/build.py --import --sign

Import guards:
    The server refuses a republish that would destroy or weaken something on the target,
    and names which one. Each has an explicit opt-in — never pass one to "make the error
    go away", the refusal is the feature:
      posture_downgrade  → --allow-posture-downgrade   (target's trust posture is stronger
                                                        than the manifest declares)
      dropped_tags       → --force-drop-tags           (a profile in the target is absent
                                                        from the new manifest)

Prerequisites:
    pip install sentence-transformers langchain-text-splitters requests numpy

Output:
    agent_packs/uderia_expert/output/uderia_expert.agentpack
"""

import argparse
import fnmatch
import io
import hashlib
import json
import os
import re
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

# ── Constants ─────────────────────────────────────────────────────────────────

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 5000       # Chunks per JSONL batch (matches collection_utils.py)
EMBED_BATCH_SIZE = 64

PACK_NAME = "Uderia Expert"
PACK_DESCRIPTION = "RAG-powered assistant over all Uderia platform documentation. Ask anything about architecture, configuration, API, RAG templates, connectors, or the IFOC methodology."
PACK_AUTHOR = "Uderia"
PACK_VERSION = "1.0.0"

COLLECTION_REF  = "uderia_knowledge"
COLLECTION_NAME = "Uderia Documentation"

PROFILE_TAG  = "UDERIA"
PROFILE_NAME = "Uderia Expert"

# Canonical project root: two levels above this file (agent_packs/uderia_expert/build.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Shared component-enablement policy (memory-on, action-components-off) — one source
# of truth across all packs so imported profiles don't inherit permissive defaults.
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))  # collection_utils.local_document_id (Phase 7)
from agent_packs.common.component_config import focused_component_config
from agent_packs.common.trust_posture import posture


# ── Document scanning ─────────────────────────────────────────────────────────

CORPUS_MANIFEST = Path(__file__).parent / "corpus.json"


class CorpusUnclassified(RuntimeError):
    """A markdown file matched no rule in corpus.json — every doc needs a deliberate decision."""


def load_corpus_rules(manifest_path: Path = CORPUS_MANIFEST) -> list[dict]:
    rules = json.loads(manifest_path.read_text(encoding="utf-8"))["rules"]
    for r in rules:
        if r.get("action") not in ("include", "exclude") or not r.get("pattern"):
            raise ValueError(f"corpus.json: malformed rule {r!r}")
    return rules


def classify_doc(rel_path: str, rules: list[dict]) -> dict | None:
    """The FIRST rule whose pattern matches the project-root-relative path, or None."""
    for r in rules:
        if fnmatch.fnmatchcase(rel_path, r["pattern"]):
            return r
    return None


def candidate_docs(project_root: Path) -> list[str]:
    """Every markdown file the corpus could contain: docs/**/*.md + README.md."""
    out = []
    docs_root = project_root / "docs"
    if docs_root.exists():
        out += [str(p.relative_to(project_root)) for p in sorted(docs_root.rglob("*.md"))]
    if (project_root / "README.md").exists():
        out.append("README.md")
    return out


def collect_source_files(project_root: Path, rules: list[dict] | None = None) -> list[dict]:
    """Return {abs_path, filename, category, rel_path} for every doc corpus.json INCLUDES.

    Raises CorpusUnclassified when a candidate matches no rule, so a new doc can never
    enter (or silently stay out of) the documentation corpus without a decision."""
    rules = load_corpus_rules() if rules is None else rules
    files, unclassified = [], []
    for rel_path in candidate_docs(project_root):
        rule = classify_doc(rel_path, rules)
        if rule is None:
            unclassified.append(rel_path)
            continue
        if rule["action"] != "include":
            continue
        parts = Path(rel_path).parts
        category = "root" if rel_path == "README.md" else (parts[1] if len(parts) > 2 else "docs")
        files.append({
            "abs_path": str(project_root / rel_path),
            "filename": Path(rel_path).name,
            "category": category,
            "rel_path": rel_path,
        })
    if unclassified:
        raise CorpusUnclassified(
            "corpus.json does not classify these docs — add an include or exclude rule "
            "(with a reason) for each:\n  " + "\n  ".join(unclassified))
    return files


def pack_rel_paths(pack_path: Path) -> set[str] | None:
    """The documents an existing .agentpack was built from (None if unreadable)."""
    try:
        with zipfile.ZipFile(pack_path) as outer:
            inner_name = next(n for n in outer.namelist() if n.endswith(".zip"))
            with zipfile.ZipFile(io.BytesIO(outer.read(inner_name))) as inner:
                lines = inner.read("documents.jsonl").decode("utf-8").splitlines()
        paths = set()
        for line in lines:
            if not line.strip():
                continue
            rec = json.loads(line)
            metas = rec.get("metadatas") or ([rec["metadata"]] if rec.get("metadata") else [])
            for m in metas:
                uri = (m or {}).get("source_uri") or ""
                if uri.startswith("file://"):
                    paths.add(uri[len("file://"):])
        return paths
    except Exception:
        return None


# ── Text utilities ─────────────────────────────────────────────────────────────

def extract_title(content: str, filename: str) -> str:
    """Extract first H1 heading from markdown, falling back to filename stem."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return Path(filename).stem.replace("_", " ").replace("-", " ")


# ── Core processing ───────────────────────────────────────────────────────────

def process_docs(source_files: list[dict], model: SentenceTransformer) -> tuple[bytes, dict]:
    """Chunk, embed, and pack all source files into a collection ZIP (in-memory bytes).

    Returns (zip_bytes, stats_dict).
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    all_ids: list[str] = []
    all_chunks: list[str] = []
    all_metadatas: list[dict] = []

    skipped = 0
    created_at = datetime.now(timezone.utc).isoformat()

    for doc_info in source_files:
        abs_path = doc_info["abs_path"]
        filename = doc_info["filename"]
        category = doc_info["category"]
        rel_path = doc_info["rel_path"]  # root-relative, e.g. docs/Architecture/foo.md

        try:
            content_bytes = Path(abs_path).read_bytes()
        except OSError as e:
            print(f"  WARN: Cannot read {abs_path}: {e}")
            skipped += 1
            continue

        # source_content_hash matches what knowledge_sync.py stores in
        # knowledge_documents.content_hash (sha256 of the raw file bytes).
        # update_in_place uses this to detect unchanged documents.
        source_content_hash = hashlib.sha256(content_bytes).hexdigest()
        content = content_bytes.decode("utf-8", errors="replace")

        if not content.strip():
            skipped += 1
            continue

        title = extract_title(content, filename)
        chunks = splitter.split_text(content)
        if not chunks:
            skipped += 1
            continue

        # Stable document ID derived from rel_path (project-root-relative).
        # rel_path is portable across machines so a build on the developer's
        # laptop and a build on the production host produce the same doc_id
        # for the same source file.  abs_path was previously used but caused
        # the update_in_place diff to see all docs as remove+add when the
        # build moved between hosts (different filesystem layouts).
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, rel_path))
        tags = f"uderia,{category}"

        for i, chunk_text in enumerate(chunks):
            content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()[:8]
            chunk_id = f"{doc_id}_chunk_{i}_{content_hash}"

            all_ids.append(chunk_id)
            all_chunks.append(chunk_text)
            all_metadatas.append({
                "document_id": doc_id,
                "filename": filename,
                "title": title,
                "category": category,
                "source_uri": f"file://{rel_path}",
                "chunk_index": i,
                "chunk_method": "recursive",
                "document_type": "markdown",
                "tags": tags,
                "source": "build",
                "created_at": created_at,
                # File-content hash so update_in_place can match against
                # knowledge_documents.content_hash (set to file SHA by CDC sync).
                "source_content_hash": source_content_hash,
            })

    if not all_chunks:
        raise RuntimeError("No chunks generated — check that docs/ and README.md exist.")

    unique_docs = len({m["document_id"] for m in all_metadatas})
    print(f"  Documents : {len(source_files) - skipped} processed, {skipped} skipped")
    print(f"  Chunks    : {len(all_chunks)}")

    # ── Embed ──────────────────────────────────────────────────────────────────
    print(f"  Embedding : {EMBEDDING_MODEL} …")
    embeddings = model.encode(
        all_chunks,
        show_progress_bar=True,
        batch_size=EMBED_BATCH_SIZE,
        normalize_embeddings=False,
    )
    embeddings_list = embeddings.tolist()
    print(f"  Vectors   : {len(embeddings_list)} × {len(embeddings_list[0])}d")

    # ── Collection ZIP ─────────────────────────────────────────────────────────
    collection_metadata = {
        "export_version": "1.0",
        "name": COLLECTION_NAME,
        "description": PACK_DESCRIPTION,
        "repository_type": "knowledge",
        "embedding_model": EMBEDDING_MODEL,
        "chunking_strategy": "recursive",
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "document_count": unique_docs,
        "exported_at": created_at,
    }

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("collection_metadata.json", json.dumps(collection_metadata, indent=2))

        jsonl_lines = []
        total = len(all_ids)
        for start in range(0, total, BATCH_SIZE):
            end = min(start + BATCH_SIZE, total)
            batch = {
                "ids":        all_ids[start:end],
                "documents":  all_chunks[start:end],
                "metadatas":  all_metadatas[start:end],
                "embeddings": embeddings_list[start:end],
            }
            jsonl_lines.append(json.dumps(batch, allow_nan=False))
        zf.writestr("documents.jsonl", "\n".join(jsonl_lines) + "\n")

    zip_bytes = buf.getvalue()
    size_mb = len(zip_bytes) / (1024 * 1024)
    print(f"  Collection : {size_mb:.1f} MB ({len(jsonl_lines)} JSONL batch(es))")

    stats = {
        "documents": unique_docs,
        "chunks": len(all_chunks),
        "zip_size_mb": round(size_mb, 2),
    }
    return zip_bytes, stats


# ── Manifest ──────────────────────────────────────────────────────────────────

def build_manifest(stats: dict) -> dict:
    """Build manifest.json for format_version 1.1 (single rag_focused profile)."""
    return {
        "format_version": "1.1",
        "name": PACK_NAME,
        "description": PACK_DESCRIPTION,
        "author": PACK_AUTHOR,
        "version": PACK_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tags": ["uderia", "documentation", "platform", "rag"],
        "profiles": [
            {
                "tag": PROFILE_TAG,
                "name": PROFILE_NAME,
                "description": "Answers questions about the Uderia platform using the full documentation corpus.",
                "profile_type": "rag_focused",
                "role": "standalone",
                "collection_refs": [COLLECTION_REF],
                "classification_mode": "light",
                "knowledgeConfig": {
                    "maxDocs": 10,
                    "maxTokens": 8000,
                    "minRelevanceScore": 0.25,
                    "maxChunksPerDocument": 3,
                },
                # Explicit component enablement (shared policy): memory on, action
                # components off — so the imported profile is authoritative instead of
                # inheriting the platform's permissive defaults (which would auto-inject
                # 6 companion skills, ~5.5K tokens/turn).
                "componentConfig": focused_component_config(),
                # Trust posture: DELIBERATELY OMITTED, not forgotten — `posture()` with no
                # arguments returns {} and the spread is a no-op. This is the pack stating it
                # has no posture opinion, which is a different act from declaring one:
                #   * fresh install  → the profile gets the platform default (observe on every
                #     gate), which is what this pack wants — every gate is deterministic and
                #     0-token at observe, and enforcement is a per-deployment call.
                #   * update_in_place → the subscriber's OWN posture is PRESERVED. A declared
                #     value would instead REPLACE theirs, and be refused with `posture_downgrade`
                #     whenever it is weaker than what they run.
                # That second point is why declaring `observe` explicitly would be wrong rather
                # than equivalent: it is an assertion that the agent should not enforce, and it
                # collides with any subscriber who hardened their copy (this pack previously
                # declared annotate and was refused against a deployment running review).
                # See agent_packs/common/trust_posture.py and docs/roadmap/TIER_5.2_POSTURE.md.
                **posture(),
            }
        ],
        "collections": [
            {
                "ref": COLLECTION_REF,
                "name": COLLECTION_NAME,
                "repository_type": "knowledge",
                "description": PACK_DESCRIPTION,
                "file": f"collections/{COLLECTION_REF}.zip",
                "documents": stats.get("documents", 0),
                "chunks": stats.get("chunks", 0),
            }
        ],
    }


# ── Auto-import ───────────────────────────────────────────────────────────────

def _get_jwt(base_url: str, username: str, password: str) -> str:
    import requests
    resp = requests.post(
        f"{base_url}/api/v1/auth/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    if not token:
        raise RuntimeError(f"Login succeeded but no token in response: {resp.json()}")
    return token


def _report_import_error(resp) -> None:
    """Print the server's refusal in full before the exception swallows it.

    Every 400 from ``/agent-packs/import`` carries a machine-readable ``error`` code plus a
    message naming exactly what is wrong (which tags would drop, which posture keys would
    weaken, which collection is model-locked). ``raise_for_status()`` reports none of it —
    a bare "400 Client Error" leaves the operator with nothing to act on. Print it, and
    name the flag that resolves it where one exists.
    """
    try:
        body = resp.json()
    except Exception:
        body = None

    print("\n  IMPORT REFUSED")
    print("  " + "-" * 50)
    if isinstance(body, dict):
        code = body.get("error")
        if code:
            print(f"  error   : {code}")
        for line in body.get("validation_errors") or []:
            print(f"  detail  : {line}")
        if body.get("message"):
            print(f"  message : {body['message']}")
        remedy = {
            "dropped_tags": "--force-drop-tags  (removes the missing profiles and archives their sessions)",
            "posture_downgrade": "--allow-posture-downgrade  (accepts weaker protection on the target)",
        }.get(code)
        # posture_downgrade arrives as a validation_errors line rather than an `error` code,
        # so match on the text too — the remedy is the point of this function.
        if not remedy and "posture_downgrade" in resp.text:
            remedy = "--allow-posture-downgrade  (accepts weaker protection on the target)"
        if remedy:
            print(f"  resolve : re-run with {remedy}")
    if body is None:
        print(f"  body    : {resp.text[:2000]}")
    print("  " + "-" * 50 + "\n")


def _import_pack(
    base_url: str,
    jwt: str,
    pack_path: Path,
    dry_run: bool = False,
    allow_posture_downgrade: bool = False,
    force_drop_tags: bool = False,
) -> dict:
    """Import the pack via update_in_place (preserves IDs for marketplace subscribers).

    Falls back to ``replace`` only on the first install when no @UDERIA profile
    exists yet — the server tells us with a "no existing profiles match" error,
    which we catch and retry under ``replace``.

    ``allow_posture_downgrade`` / ``force_drop_tags`` are opt-in escape hatches for the two
    guards that deliberately refuse a destructive republish. Never default them to True: the
    refusal is the feature.
    """
    import requests
    flat = {
        "conflict_strategy": "update_in_place",
        "dry_run": "true" if dry_run else "false",
    }
    if allow_posture_downgrade:
        flat["allow_posture_downgrade"] = "true"
    if force_drop_tags:
        flat["force_drop_tags"] = "true"
    with open(pack_path, "rb") as fh:
        resp = requests.post(
            f"{base_url}/api/v1/agent-packs/import",
            headers={"Authorization": f"Bearer {jwt}"},
            files={"file": (pack_path.name, fh, "application/octet-stream")},
            data=flat,
            timeout=300,
        )

    # First-install fallback: update_in_place rejects fresh installs (no
    # matching profiles). Retry under replace so the pipeline still works on
    # an empty Uderia.  Only triggered when no dry_run was requested.
    if (
        not dry_run
        and resp.status_code == 400
        and "update_in_place" in resp.text
        and "no existing profiles match" in resp.text
    ):
        print("  Note   : no existing @UDERIA install — falling back to replace for first-time install")
        with open(pack_path, "rb") as fh:
            resp = requests.post(
                f"{base_url}/api/v1/agent-packs/import",
                headers={"Authorization": f"Bearer {jwt}"},
                files={"file": (pack_path.name, fh, "application/octet-stream")},
                data={"conflict_strategy": "replace"},
                timeout=300,
            )

    if not resp.ok:
        _report_import_error(resp)
    resp.raise_for_status()

    # A dry-run that fails validation answers 200 with status="dry_run_failed", so
    # raise_for_status() never fires — surface it the same way rather than printing a
    # success-shaped blob the operator has to read twice.
    body = resp.json()
    if isinstance(body, dict) and body.get("status") == "dry_run_failed":
        _report_import_error(resp)
    return body


def _find_collection_id(base_url: str, jwt: str, collection_name: str) -> int | None:
    import requests
    resp = requests.get(
        f"{base_url}/api/v1/rag/collections",
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    collections = data if isinstance(data, list) else data.get("collections", [])
    for coll in collections:
        if coll.get("collection_name") == collection_name or coll.get("name") == collection_name:
            return int(coll["id"])
    return None


def _find_collection_scope(base_url: str, jwt: str, collection_id: int) -> str | None:
    """The vector collection NAME of the installed repository — the scope an import mints
    its instance-local document ids under (collection_utils.local_document_id)."""
    import requests
    resp = requests.get(
        f"{base_url}/api/v1/rag/collections",
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    collections = data if isinstance(data, list) else data.get("collections", [])
    for coll in collections:
        if int(coll.get("id", -1)) == int(collection_id):
            return coll.get("collection_name")
    return None


def _list_documents(base_url: str, jwt: str, collection_id: int) -> list[dict]:
    import requests
    resp = requests.get(
        f"{base_url}/api/v1/knowledge/repositories/{collection_id}/documents",
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("documents", [])


def _patch_document_source(
    base_url: str,
    jwt: str,
    collection_id: int,
    doc_id: str | int,
    source_uri: str,
    sync_interval: str = "daily",
) -> None:
    import requests
    resp = requests.patch(
        f"{base_url}/api/v1/knowledge/repositories/{collection_id}/documents/{doc_id}",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Content-Type": "application/json",
        },
        json={
            "source_uri": source_uri,
            "sync_enabled": True,
            "sync_interval": sync_interval,
        },
        timeout=30,
    )
    resp.raise_for_status()


def _build_doc_id_to_path(source_files: list[dict], scope: str | None = None) -> dict[str, str]:
    """Map document_id → rel_path for CDC source_uri wiring.

    Keyed by document_id (= uuid5(NAMESPACE_URL, abs_path)) to be unique.  An
    earlier version keyed by filename, but several docs share filenames
    (README.md, QUICK_REFERENCE.md), causing different documents to be PATCHed
    with the same source_uri.  The next CDC sync would then read the same file
    for all of them and assign them identical content_hashes — which then
    masquerades as a content change in update_in_place's diff.

    Both id forms are mapped: the build-time id (uuid5 of the rel_path) AND the
    instance-local id the import mints from it under the target collection's name
    (``collection_utils.local_document_id``, the ONE derivation — never a copy). An
    installed copy carries the local form, so mapping only the build-time id would
    leave every document unwired and the daily sync silently off.
    """
    derive = None
    if scope:
        try:
            from trusted_data_agent.core.collection_utils import local_document_id as derive
        except Exception as exc:  # the platform package is not importable here
            print(f"  WARN   : cannot derive instance-local document ids ({exc}); "
                  "only build-time ids will be wired")
    mapping: dict[str, str] = {}
    for f in source_files:
        # Must match the doc_id derivation used at chunk-build time.
        doc_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f["rel_path"]))
        mapping[doc_id] = f["rel_path"]
        if derive is not None:
            mapping[derive(scope, doc_id)] = f["rel_path"]
    return mapping


def sign_and_sync() -> bool:
    """Phase 8 (--sign): sign the corpus, then @UDERIA, then the pack — bottom-up, the order
    the platform's gate requires — and re-sync marketplace subscribers so their copies
    reinstate the NEW signature (the import's own fan-out ran before it existed).

    A refusal is reported, never worked around: open prompt-injection findings on the
    corpus are named and the run stops — acknowledging one is a judgement recorded
    INTO the signature, so it is the owner's act (POST
    /v1/knowledge/documents/<id>/acknowledge-finding with a reason), not a build step.
    Returns True when everything signed and the subscribers synced."""
    import requests
    base_url = os.environ.get("UDERIA_BASE_URL", "http://localhost:5050").rstrip("/")
    jwt = _get_jwt(base_url, os.environ.get("UDERIA_USERNAME", "admin"),
                   os.environ.get("UDERIA_PASSWORD", "admin"))
    H = {"Authorization": f"Bearer {jwt}"}

    print(f"\n{'='*60}")
    print("Phase 8 — Trust sign-off (bottom-up) + subscriber sync")
    print(f"{'='*60}")

    def _sign(kind: str, artefact_id, label: str) -> bool:
        r = requests.post(f"{base_url}/api/v1/trust/{kind}/{artefact_id}/sign-off",
                          headers=H, json={}, timeout=180)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.ok and body.get("signed"):
            print(f"  Signed : {label}")
            return True
        v = body.get("validation") or {}
        crit = (v.get("detail") or {}).get("open_critical") or []
        if crit:
            print(f"  NOT signed — {label}: {len(crit)} document(s) carry unresolved findings:")
            for d in crit:
                print(f"           {d.get('filename')} {d.get('labels')} (document {d.get('document_id')})")
            print("           Quarantine each or acknowledge it with a reason, then re-run with --sign:")
            print(f"           POST {base_url}/api/v1/knowledge/documents/<document_id>/acknowledge-finding "
                  '{"reason": "..."}')
        else:
            print(f"  NOT signed — {label}: HTTP {r.status_code} {r.text[:240]}")
        return False

    collection_id = _find_collection_id(base_url, jwt, COLLECTION_NAME)
    profiles = requests.get(f"{base_url}/api/v1/profiles", headers=H, timeout=60).json()
    profiles = profiles.get("profiles", profiles) if isinstance(profiles, dict) else profiles
    profile_id = next((p["id"] for p in profiles if p.get("tag") == PROFILE_TAG), None)
    packs = requests.get(f"{base_url}/api/v1/agent-packs", headers=H, timeout=60).json()
    packs = packs.get("packs", packs) if isinstance(packs, dict) else packs
    installation_id = next((p.get("installation_id") for p in packs
                            if p.get("name") == PACK_NAME and p.get("is_owned")), None)
    if not (collection_id and profile_id and installation_id):
        print(f"  ERROR  : cannot resolve what to sign (collection={collection_id}, "
              f"profile={profile_id}, pack={installation_id})")
        return False

    if not _sign("knowledge", collection_id, f"knowledge repository '{COLLECTION_NAME}'"):
        return False
    if not _sign("agent", profile_id, f"@{PROFILE_TAG}"):
        return False
    if not _sign("agent-pack", installation_id, f"pack '{PACK_NAME}'"):
        return False

    r = requests.post(f"{base_url}/api/v1/agent-packs/{installation_id}/sync-subscribers",
                      headers=H, json={}, timeout=600)
    if r.status_code == 409:
        print("  Sync   : the pack has no marketplace listing — no subscribers to update")
        return True
    if not r.ok:
        print(f"  ERROR  : subscriber sync failed: HTTP {r.status_code} {r.text[:240]}")
        return False
    res = r.json()
    print(f"  Sync   : {res.get('synced', 0)} subscriber(s) synced, {res.get('failed', 0)} failed")
    return not res.get("failed")


def do_import(
    pack_path: Path,
    source_files: list[dict],
    dry_run: bool = False,
    allow_posture_downgrade: bool = False,
    force_drop_tags: bool = False,
) -> None:
    """Phase 7: import the agentpack and wire CDC sync on every document.

    When ``dry_run`` is True, only sends the validation/diff request to the
    server (conflict_strategy=update_in_place + dry_run=true) and prints the
    returned change plan. Skips CDC wiring, embedding lock, and initial sync.
    """
    base_url = os.environ.get("UDERIA_BASE_URL", "http://localhost:5050").rstrip("/")
    username = os.environ.get("UDERIA_USERNAME", "admin")
    password = os.environ.get("UDERIA_PASSWORD", "admin")

    print(f"\n{'='*60}")
    print("Phase 7 — Auto-import" + (" (DRY RUN)" if dry_run else ""))
    print(f"{'='*60}")
    print(f"  Target : {base_url}")

    # Authenticate
    print("  Auth   : logging in …")
    jwt = _get_jwt(base_url, username, password)
    print("  Auth   : OK")

    # Import pack
    print(f"  Import : uploading {pack_path.name} …")
    import_result = _import_pack(
        base_url,
        jwt,
        pack_path,
        dry_run=dry_run,
        allow_posture_downgrade=allow_posture_downgrade,
        force_drop_tags=force_drop_tags,
    )
    if dry_run:
        print("\n  DRY-RUN PLAN")
        print("  " + "-" * 50)
        print(json.dumps(import_result, indent=2, default=str))
        print("\n  No changes committed. Re-run without --dry-run to apply.")
        return
    print(f"  Import : {import_result}")

    # Find the collection by name
    print(f"  Lookup : finding collection '{COLLECTION_NAME}' …")
    collection_id = _find_collection_id(base_url, jwt, COLLECTION_NAME)
    if collection_id is None:
        print(f"  WARN   : Collection '{COLLECTION_NAME}' not found after import.")
        print("           CDC sync wiring skipped — run manually if needed.")
        return
    print(f"  Lookup : collection_id = {collection_id}")

    # List documents
    print("  Docs   : listing documents …")
    documents = _list_documents(base_url, jwt, collection_id)
    print(f"  Docs   : {len(documents)} document(s) found")

    if not documents:
        print("  WARN   : No documents returned — CDC wiring skipped.")
        return

    # Build document_id → rel_path mapping for source_uri wiring.
    # Keyed by doc_id (not filename) so docs with the same filename in
    # different subdirectories don't collide.
    doc_id_to_path = _build_doc_id_to_path(
        source_files, scope=_find_collection_scope(base_url, jwt, collection_id))

    # PATCH each document with source_uri + sync_enabled
    wired = 0
    skipped = 0
    for doc in documents:
        doc_id = doc.get("document_id") or doc.get("id")
        filename = doc.get("filename", "")
        rel_path = doc_id_to_path.get(doc_id)

        if not rel_path:
            print(f"  SKIP   : '{filename}' (doc={doc_id}) — no matching file path found")
            skipped += 1
            continue

        # Root-relative URI — resolved at sync time against the server's installation root
        source_uri = f"file://{rel_path}"
        try:
            _patch_document_source(base_url, jwt, collection_id, doc_id, source_uri)
            wired += 1
        except Exception as e:
            print(f"  ERROR  : PATCH failed for '{filename}' (doc={doc_id}): {e}")
            skipped += 1

    print(f"\n  CDC wiring complete: {wired} wired, {skipped} skipped")
    if skipped:
        print(f"  WARN   : {skipped} document(s) NOT wired — their daily sync is off. "
              "Their ids matched no corpus file (see SKIP lines above).")

    if wired == 0:
        print("  WARN   : No documents wired — skipping post-import steps.")
        return

    # Lock embedding model — all chunks were built in a single run with the same model
    print("  Lock   : setting embedding_model_locked=1 …")
    try:
        import requests as _req
        resp = _req.patch(
            f"{base_url}/api/v1/knowledge/repositories/{collection_id}",
            headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
            json={"embedding_model_locked": 1},
            timeout=15,
        )
        resp.raise_for_status()
        print("  Lock   : OK")
    except Exception as e:
        print(f"  WARN   : Could not lock embedding model: {e}")

    # Run initial sync to hash-check all files and clear the stale count
    print("  Sync   : initial hash-check to clear stale counter …")
    try:
        import requests as _req
        resp = _req.post(
            f"{base_url}/api/v1/knowledge/repositories/{collection_id}/sync",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=120,
        )
        resp.raise_for_status()
        sync_result = resp.json()
        print(f"  Sync   : checked={sync_result.get('checked')} "
              f"unchanged={sync_result.get('unchanged')} "
              f"updated={sync_result.get('updated')} "
              f"errors={sync_result.get('errors')}")
    except Exception as e:
        print(f"  WARN   : Initial sync failed: {e}")

    print(f"\n  APScheduler will auto-sync this collection daily.")
    print(f"  Manual sync: POST {base_url}/api/v1/knowledge/repositories/{collection_id}/sync")


# ── Main ──────────────────────────────────────────────────────────────────────

def _maybe_sign(args) -> None:
    """--sign after a real import (never after a dry run); a refusal fails the run."""
    if getattr(args, "sign", False):
        if args.dry_run:
            print("\n  --sign ignored on a dry run (nothing was imported to sign).")
        elif not sign_and_sync():
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build uderia_expert.agentpack from the Uderia documentation corpus (corpus.json)"
    )
    parser.add_argument(
        "--docs-root",
        type=str,
        default=str(PROJECT_ROOT),
        help="Project root (default: auto-detected from script location)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(Path(__file__).parent / "output"),
        help="Output directory (default: agent_packs/uderia_expert/output)",
    )
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="Auto-import into running Uderia instance + wire CDC sync",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if output already exists",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help=(
            "When used with --import: send conflict_strategy=update_in_place "
            "with dry_run=true and print the planned changes without committing. "
            "Useful for verifying a republish before it touches production data."
        ),
    )
    parser.add_argument(
        "--sign",
        action="store_true",
        help=(
            "When used with --import: after the import, sign the knowledge repository, "
            "@UDERIA and the pack (bottom-up), then re-sync marketplace subscribers so they "
            "reinstate the new signature. Refusals are reported, never worked around."
        ),
    )
    parser.add_argument(
        "--allow-posture-downgrade",
        dest="allow_posture_downgrade",
        action="store_true",
        help=(
            "When used with --import: accept an import that REDUCES the trust posture of "
            "the target profile (e.g. its guardrails run at 'review' and the manifest "
            "declares 'annotate'). Off by default — the refusal exists to stop a republish "
            "silently weakening a protection somebody turned on deliberately."
        ),
    )
    parser.add_argument(
        "--force-drop-tags",
        dest="force_drop_tags",
        action="store_true",
        help=(
            "When used with --import: allow profiles whose tags are absent from the new "
            "manifest to be REMOVED, archiving their sessions. Off by default."
        ),
    )
    args = parser.parse_args()

    project_root = Path(args.docs_root).resolve()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pack_path = output_dir / "uderia_expert.agentpack"


    # ── Phase 1: Scan ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Phase 1 — Scanning documentation")
    print(f"{'='*60}")
    print(f"  Root    : {project_root}")

    try:
        source_files = collect_source_files(project_root)
    except CorpusUnclassified as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"  Found   : {len(source_files)} markdown file(s) selected by {CORPUS_MANIFEST.name}")

    if not source_files:
        print("ERROR: No markdown files found. Check --docs-root.")
        sys.exit(1)

    # Show breakdown by category
    from collections import Counter
    counts = Counter(f["category"] for f in source_files)
    for cat, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"           {n:3d}  {cat}")

    # An existing pack is reused only if it was built from EXACTLY the documents the corpus
    # selects today. A pack from a different selection would re-import dropped docs (or miss
    # new ones) while looking up to date — rebuild instead.
    if pack_path.exists() and not args.force:
        built_from = pack_rel_paths(pack_path)
        selected = {f["rel_path"] for f in source_files}
        if built_from != selected:
            if built_from is None:
                print(f"\n  {pack_path.name} is unreadable — rebuilding.")
            else:
                print(f"\n  {pack_path.name} was built from a different document selection "
                      f"({len(built_from - selected)} to drop, {len(selected - built_from)} to add) — rebuilding.")
            args.force = True
    if pack_path.exists() and not args.force:
        print(f"\nSkipping build — {pack_path.name} already exists (use --force to rebuild).")
        if args.do_import:
            do_import(
                pack_path,
                source_files,
                dry_run=args.dry_run,
                allow_posture_downgrade=args.allow_posture_downgrade,
                force_drop_tags=args.force_drop_tags,
            )
            _maybe_sign(args)
        return

    # ── Phase 2–4: Chunk, embed, pack ─────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Phases 2–4 — Chunk / Embed / Pack")
    print(f"{'='*60}")

    print(f"  Loading embedding model: {EMBEDDING_MODEL} …")
    model = SentenceTransformer(EMBEDDING_MODEL)
    print(f"  Model ready ({model.get_sentence_embedding_dimension()}d)\n")

    collection_zip, stats = process_docs(source_files, model)

    # ── Phase 5: Manifest ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Phase 5 — Manifest")
    print(f"{'='*60}")

    manifest = build_manifest(stats)
    print(f"  Profile : {PROFILE_TAG} ({manifest['profiles'][0]['profile_type']})")
    print(f"  Collect : {COLLECTION_REF}")

    # ── Phase 6: Bundle .agentpack ─────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Phase 6 — Bundling .agentpack")
    print(f"{'='*60}")

    with zipfile.ZipFile(pack_path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        arcname = f"collections/{COLLECTION_REF}.zip"
        zf.writestr(arcname, collection_zip)
        size_mb = len(collection_zip) / (1024 * 1024)
        print(f"  Added   : {arcname} ({size_mb:.1f} MB)")

    final_mb = pack_path.stat().st_size / (1024 * 1024)

    print(f"\n{'='*60}")
    print("BUILD SUMMARY")
    print(f"{'='*60}")
    print(f"  Pack       : {PACK_NAME} v{PACK_VERSION}")
    print(f"  Documents  : {stats['documents']}")
    print(f"  Chunks     : {stats['chunks']}")
    print(f"  Output     : {pack_path}")
    print(f"  Size       : {final_mb:.1f} MB")

    if args.do_import:
        do_import(
            pack_path,
            source_files,
            dry_run=args.dry_run,
            allow_posture_downgrade=args.allow_posture_downgrade,
            force_drop_tags=args.force_drop_tags,
        )
        _maybe_sign(args)
    else:
        print(f"\nTo install manually:")
        print(f"  JWT=$(curl -s -X POST http://localhost:5050/api/v1/auth/login \\")
        print(f"    -H 'Content-Type: application/json' \\")
        print(f"    -d '{{\"username\":\"admin\",\"password\":\"admin\"}}' | jq -r '.token')")
        print(f"  curl -X POST http://localhost:5050/api/v1/agent-packs/import \\")
        print(f"    -H \"Authorization: Bearer $JWT\" \\")
        print(f"    -F 'file=@{pack_path}' \\")
        print(f"    -F 'data={{\"conflict_strategy\":\"replace\"}}'")
        print(f"\nOr use: python {__file__} --import")


if __name__ == "__main__":
    main()
