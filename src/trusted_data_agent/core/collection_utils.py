"""
Shared utilities for collection import and export.

Extracted from rest_routes.py to be reusable by both the REST API endpoints
and the AgentPackManager. All collection import/export logic is centralized here.

Key features:
- Batched JSONL format for memory-efficient export/import of large collections
- Backward-compatible import (reads both documents.json and documents.jsonl)
- Configurable parameters for agent pack vs standalone use
"""

import asyncio
import json
import logging
import re
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

app_logger = logging.getLogger("quart.app")

BATCH_SIZE = 5000  # Chunks per batch for ChromaDB reads/writes


async def import_collection_from_zip(
    zip_path: Path,
    user_uuid: str,
    display_name: str | None = None,
    mcp_server_id: str | None = None,
    skip_reload: bool = False,
    populate_knowledge_docs: bool = False,
    vector_store_config_id: str | None = None,
) -> dict:
    """Import a collection ZIP into Uderia.

    Supports two document formats inside the ZIP:
    - documents.json  (legacy full-load — entire collection in one JSON object)
    - documents.jsonl (batched — one JSON object per line, each up to BATCH_SIZE chunks)

    Import always processes in batches via chroma_collection.add().

    Args:
        zip_path: Path to the collection ZIP file.
        user_uuid: Owner user UUID.
        display_name: Override collection name. If None, uses metadata name + " (Imported)".
        mcp_server_id: MCP server ID for planner repositories. If None, uses
                       APP_CONFIG.CURRENT_MCP_SERVER_ID for planner repos.
        skip_reload: If True, skip retriever reload after import (caller handles it).
        populate_knowledge_docs: If True, populate knowledge_documents table
                                  (needed for knowledge repo document listing UI).

    Returns:
        {"collection_id": int, "collection_name": str, "document_count": int}

    Raises:
        ValueError: On validation errors (missing files, bad format).
        RuntimeError: On import failures (ChromaDB errors, DB errors).
    """
    import shutil
    from chromadb.utils import embedding_functions
    from trusted_data_agent.core.embedding_cache import sentence_transformer_embedding_function

    from trusted_data_agent.agent.rag_retriever import get_rag_retriever
    from trusted_data_agent.core.config import APP_CONFIG, APP_STATE

    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir)

    try:
        # Extract ZIP
        extract_path = temp_path / "extracted"
        extract_path.mkdir()

        with zipfile.ZipFile(zip_path, 'r') as zipf:
            zipf.extractall(extract_path)

        # Read metadata
        metadata_file = extract_path / "collection_metadata.json"
        if not metadata_file.exists():
            raise ValueError("Invalid collection ZIP: collection_metadata.json missing")

        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        # Validate export version
        export_version = metadata.get('export_version', '1.0')
        if export_version not in ("1.0", "1.1"):
            raise ValueError(f"Unsupported export version: {export_version}")

        # Determine document format
        documents_jsonl = extract_path / "documents.jsonl"
        documents_json = extract_path / "documents.json"

        if not documents_jsonl.exists() and not documents_json.exists():
            raise ValueError("Invalid collection ZIP: neither documents.json nor documents.jsonl found")

        use_jsonl = documents_jsonl.exists()

        # Generate unique collection name for ChromaDB
        original_name = metadata['name']
        import uuid as _uuid
        new_collection_name = f"col_{user_uuid}_{int(time.time())}_{_uuid.uuid4().hex[:6]}"

        # Determine display name
        if display_name is None:
            final_display_name = f"{original_name} (Imported)"
        else:
            final_display_name = display_name

        # Get the RAG retriever
        retriever = get_rag_retriever()
        if not retriever:
            raise RuntimeError("RAG retriever not initialized")

        # Setup embedding function (still needed for planner path)
        embedding_model = metadata.get('embedding_model', 'all-MiniLM-L6-v2')
        embedding_func = sentence_transformer_embedding_function(embedding_model)

        repo_type = metadata.get('repository_type', 'knowledge')
        backend_type = metadata.get('backend_type', 'chromadb')

        # If a vector_store_config_id override is provided, resolve the actual backend from it
        if vector_store_config_id and repo_type == 'knowledge':
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                all_vs = get_config_manager().get_vector_store_configurations(user_uuid)
                vs_config = next((c for c in all_vs if c.get("id") == vector_store_config_id), None)
                if vs_config:
                    backend_type = vs_config.get("backend_type", backend_type)
                    app_logger.info(f"Import: vector_store_config_id override → backend_type={backend_type}")
                else:
                    app_logger.warning(f"Import: vector_store_config_id '{vector_store_config_id}' not found, using metadata backend_type")
                    vector_store_config_id = None  # fall back to normal resolution
            except Exception as e:
                app_logger.warning(f"Import: failed to resolve vector_store_config_id override: {e}")
                vector_store_config_id = None

        app_logger.info(f"Importing {repo_type} collection (backend: {backend_type}): {new_collection_name}")

        # Resolve backend for knowledge repos; planner repos go through raw chroma_client
        use_backend = (repo_type == 'knowledge')
        backend = None
        chroma_collection = None

        if use_backend:
            from trusted_data_agent.vectorstore import (
                get_backend_for_collection, CollectionConfig, VectorDocument
            )
            coll_meta_stub = {
                "backend_type": backend_type,
                "backend_config": "{}",
                "vector_store_config_id": vector_store_config_id,
            }
            backend = await get_backend_for_collection(coll_meta_stub, user_uuid=user_uuid)
            config = CollectionConfig(name=new_collection_name, embedding_model=embedding_model)
            await backend.get_or_create_collection(config)
        else:
            chroma_client = retriever.client
            chroma_collection = chroma_client.create_collection(
                name=new_collection_name,
                metadata={"hnsw:space": "cosine", "repository_type": repo_type},
                embedding_function=embedding_func,
            )

        # Determine MCP server binding early (needed for per-case metadata stamping)
        if repo_type == 'planner':
            if mcp_server_id is not None:
                assigned_mcp_server_id = mcp_server_id
            else:
                assigned_mcp_server_id = APP_CONFIG.CURRENT_MCP_SERVER_ID
        else:
            assigned_mcp_server_id = None

        # Import documents (batched)
        total_added = 0
        all_metadatas_for_docs = []  # Collect for knowledge_documents population
        # source document_id -> fresh local id, stable for the whole import so every
        # chunk of one document keeps one id and the registry agrees with the vectors.
        doc_id_map: dict = _LocalIdMap(new_collection_name)

        if use_jsonl:
            app_logger.info("Importing from documents.jsonl (batched format)")
            with open(documents_jsonl, 'r') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    batch = json.loads(line)
                    if use_backend:
                        batch_added = await _add_batch_to_backend(
                            backend, new_collection_name, batch,
                            populate_knowledge_docs, all_metadatas_for_docs,
                            doc_id_map=doc_id_map,
                        )
                    else:
                        batch_added = _add_batch_to_chroma(
                            chroma_collection, batch, populate_knowledge_docs, all_metadatas_for_docs,
                            mcp_server_id=assigned_mcp_server_id if repo_type == 'planner' else None,
                            doc_id_map=doc_id_map,
                        )
                    total_added += batch_added
                    app_logger.info(f"  Batch {line_num}: added {batch_added} documents (total: {total_added})")
        else:
            app_logger.info("Importing from documents.json (legacy format)")
            with open(documents_json, 'r') as f:
                documents_data = json.load(f)

            ids = documents_data.get('ids', [])
            documents_list = documents_data.get('documents', [])
            metadatas_list = documents_data.get('metadatas', [])
            embeddings_list = documents_data.get('embeddings', [])

            if ids and documents_list:
                for start in range(0, len(ids), BATCH_SIZE):
                    end = min(start + BATCH_SIZE, len(ids))
                    batch = {
                        'ids': ids[start:end],
                        'documents': documents_list[start:end],
                        'metadatas': metadatas_list[start:end] if metadatas_list else [],
                        'embeddings': embeddings_list[start:end] if embeddings_list else [],
                    }
                    if use_backend:
                        batch_added = await _add_batch_to_backend(
                            backend, new_collection_name, batch,
                            populate_knowledge_docs, all_metadatas_for_docs,
                            doc_id_map=doc_id_map,
                        )
                    else:
                        batch_added = _add_batch_to_chroma(
                            chroma_collection, batch, populate_knowledge_docs, all_metadatas_for_docs,
                            mcp_server_id=assigned_mcp_server_id if repo_type == 'planner' else None,
                            doc_id_map=doc_id_map,
                        )
                    total_added += batch_added

        # Verify import
        if use_backend:
            document_count = await backend.count(new_collection_name)
        else:
            document_count = chroma_collection.count()
        app_logger.info(f"Imported collection has {document_count} documents")

        if assigned_mcp_server_id:
            app_logger.info(f"Associating planner collection with MCP server: {assigned_mcp_server_id}")

        # Resolve vector store config for imported collection
        resolved_vs_config_id = vector_store_config_id  # Use override if provided
        import_warnings = []

        if not resolved_vs_config_id:
            # No override — resolve from export metadata (original behavior)
            vs_config_data = metadata.get("vector_store_config")

            if vs_config_data:
                try:
                    from trusted_data_agent.core.config_manager import get_config_manager
                    config_manager = get_config_manager()
                    existing_configs = config_manager.get_vector_store_configurations(user_uuid)

                    # Try to match by name + backend_type
                    match = next((c for c in existing_configs
                                  if c.get("backend_type") == vs_config_data.get("backend_type")
                                  and c.get("name") == vs_config_data.get("name")), None)

                    if match:
                        resolved_vs_config_id = match["id"]
                        app_logger.info(f"Import: matched vector store config '{match['name']}' for collection")
                    else:
                        # Create new config (without credentials)
                        import random, string
                        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                        new_vs_config = {
                            "id": f"vs-{int(time.time() * 1000)}-{suffix}",
                            "name": vs_config_data.get("name", f"Imported {vs_config_data.get('backend_type', 'unknown').title()}"),
                            "backend_type": vs_config_data.get("backend_type", "chromadb"),
                            "backend_config": vs_config_data.get("backend_config", {}),
                            "credentials": {},
                            "created_at": datetime.now(timezone.utc).isoformat()
                        }
                        config_manager.add_vector_store_configuration(new_vs_config, user_uuid)
                        resolved_vs_config_id = new_vs_config["id"]
                        app_logger.info(f"Import: created vector store config '{new_vs_config['name']}' for collection")

                        if vs_config_data.get("backend_type") != "chromadb":
                            import_warnings.append(
                                f"Vector store '{new_vs_config['name']}' was created without credentials. "
                                f"Configure credentials in Anatomy → Vector Stores before using this collection."
                            )
                except Exception as e:
                    app_logger.warning(f"Could not resolve vector store config during import: {e}")
            elif backend_type == "chromadb":
                resolved_vs_config_id = "vs-default-chromadb"
        else:
            app_logger.info(f"Import: using override vector_store_config_id='{resolved_vs_config_id}'")

        # Create collection in database
        from trusted_data_agent.core.collection_db import CollectionDatabase, get_collection_db

        db = CollectionDatabase()
        new_collection = {
            "name": final_display_name,
            "collection_name": new_collection_name,
            "description": metadata.get('description', ''),
            "repository_type": repo_type,
            "mcp_server_id": assigned_mcp_server_id,
            "chunking_strategy": metadata.get('chunking_strategy', 'recursive'),
            "chunk_size": metadata.get('chunk_size', 1000),
            "chunk_overlap": metadata.get('chunk_overlap', 200),
            "embedding_model": embedding_model,
            "backend_type": backend_type,
            "backend_config": metadata.get('backend_config', '{}'),
            "vector_store_config_id": resolved_vs_config_id,
            "owner_user_id": user_uuid,
            "enabled": True,
            "visibility": "private",
            # CDC fields: import_epoch reset (chunks are fresh), model unlocked so user
            # can run Re-index to verify/lock after importing with their preferred model.
            # source_uri / sync_enabled are user-private — not restored from export.
            "sync_interval": metadata.get('sync_interval', 'daily'),
            "embedding_model_locked": 0,
        }

        collection_id = db.create_collection(new_collection)

        # Populate knowledge_documents table if requested.
        # The exporter's document registry carries the facts chunk metadata cannot —
        # above all each document's real content_hash, which is what the knowledge trust
        # signature is taken over. A zip written before the registry existed simply has
        # none, and the population falls back to what the chunk metadata can supply.
        imported_doc_count = 0
        if populate_knowledge_docs and all_metadatas_for_docs:
            doc_registry = None
            registry_path = extract_path / "document_registry.json"
            if registry_path.exists():
                try:
                    with open(registry_path) as rf:
                        doc_registry = json.load(rf)
                    app_logger.info(
                        f"Import: document registry found ({len(doc_registry)} document(s))")
                except Exception as e:
                    app_logger.warning(f"Import: unreadable document registry: {e}")
            else:
                app_logger.info(
                    "Import: no document registry in this archive — document metadata will "
                    "be derived from chunk metadata and content hashes left unset.")
            imported_doc_count = _populate_knowledge_documents(
                collection_id, all_metadatas_for_docs,
                registry=doc_registry, doc_id_map=doc_id_map)

        # For planner repositories, update APP_STATE
        if repo_type == 'planner':
            collection_db = get_collection_db()
            imported_collection_meta = collection_db.get_collection_by_id(collection_id)
            rag_collections = APP_STATE.get("rag_collections", [])
            rag_collections.append(imported_collection_meta)
            APP_STATE["rag_collections"] = rag_collections
            app_logger.info(f"Added planner collection to APP_STATE (total: {len(rag_collections)})")

        # Reload retriever
        if not skip_reload:
            try:
                retriever.reload_collections_for_mcp_server()
                app_logger.info(f"Reloaded RAG collections after import")

                if collection_id in retriever.collections:
                    actual_count = retriever.collections[collection_id].count()
                    app_logger.info(f"Verified: collection {collection_id} has {actual_count} documents")
                    document_count = actual_count
                elif use_backend and backend:
                    actual_count = await backend.count(new_collection_name)
                    app_logger.info(f"Verified (via backend): collection {collection_id} has {actual_count} documents")
                    document_count = actual_count
            except Exception as e:
                app_logger.warning(f"Failed to verify import: {e}", exc_info=True)

        # Persist counts to database
        if document_count > 0 or imported_doc_count > 0:
            db.update_counts(
                collection_id,
                document_count=imported_doc_count if imported_doc_count > 0 else None,
                chunk_count=document_count if document_count > 0 else None,
            )

        result = {
            "collection_id": collection_id,
            "collection_name": final_display_name,
            "document_count": document_count,
        }
        if import_warnings:
            result["warnings"] = import_warnings
        return result

    except (ValueError, RuntimeError):
        raise
    except Exception as e:
        app_logger.error(f"Failed to import collection: {e}", exc_info=True)
        raise RuntimeError(f"Failed to import collection: {e}")
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


#: The document registry's field set — written by the export, read by the import.
#: ONE list, because the two halves are a pair: a field the exporter forgets is a field
#: the importer silently substitutes a default for, which is how a corpus arrives looking
#: populated and hashing differently from its source. `test_document_registry` asserts
#: that every field the import reads appears here.
_REGISTRY_FIELDS = (
    "filename", "document_type", "title", "author", "source",
    "category", "tags", "file_size", "page_count", "content_hash",
    "chunk_count", "ingest_epoch",
    # The ingest-firewall record travels with the documents. It is what makes a signature
    # mean "integrity AND screened", and the findings belong to the CONTENT, which is
    # byte-identical in a copy. The quarantine set travels for a stronger reason: it is
    # the publisher's exclusion decision, and a copy that silently un-quarantined a
    # document would drop a safety control.
    "scan_json", "quarantined", "quarantine_reason",
)


#: Namespace for instance-local document ids derived from an imported copy.
_DOC_ID_NS = uuid.UUID("6f2a2a4e-3f39-4a2e-9b6a-7e6c1d3e8a11")


def local_document_id(scope: str, source_id: str) -> str:
    """THE derivation of an imported document's instance-local id — one function for the
    fresh-install path and the in-place update, so an update finds exactly the rows the
    install created. Deterministic in (scope, source id); ``scope`` is the target vector
    collection's NAME (known before the first batch is written and stored on the row —
    the row id itself only exists after the batches). Different scope, different id, so
    a publisher and a subscriber on ONE instance never share a document id."""
    return str(uuid.uuid5(_DOC_ID_NS, f"uderia:doc:{scope}:{source_id}"))


class _LocalIdMap(dict):
    """source document id -> local id, minted deterministically for one scope.

    A dict so every existing `doc_id_map.setdefault(src, uuid4())` call keeps working —
    the default the caller passes is ignored in favour of the derivation, which is the
    point: the id must not depend on WHEN or in which batch a document was first seen."""

    def __init__(self, scope: str):
        super().__init__()
        self.scope = scope

    def setdefault(self, key, default=None):
        if key not in self:
            super().__setitem__(key, local_document_id(self.scope, str(key)))
        return self[key]


def _remap_document_ids(metadatas: list, doc_id_map: dict) -> None:
    """Mint a fresh, INSTANCE-LOCAL document id for each source document, in place.

    A ``document_id`` is a UUID identifying a document *in this instance* — the same
    identity model the platform already applies to profile, collection and knowledge-graph
    ids, every one of which is remapped on import. Document ids were simply never included
    in that remapping, so an import carried the EXPORTING instance's ids.

    That collided with ``knowledge_documents.document_id TEXT UNIQUE`` (unique across the
    whole table, not per collection), so every registry row for an imported copy was
    silently discarded by ``INSERT OR IGNORE`` — and the populate step counted documents
    ATTEMPTED rather than inserted, so it logged success while writing nothing. Observed
    live: a forked corpus with 104 chunks and a completely empty document registry, whose
    trust badge could then never match the publisher's.

    Deliberately NOT fixed by relaxing the constraint to ``(collection_id, document_id)``:
    seven accessors in ``collection_db`` address a document by id alone — including
    ``ON CONFLICT(document_id)``, which requires a unique index on exactly that column and
    raises under a composite one. The identity model is right; the import was wrong.

    The map is stable across batches, so every chunk of one document keeps one id, and the
    registry restore reuses the same map — registry and vectors therefore always agree,
    which is what document deletion and the CDC content diff rely on.
    """
    for meta in metadatas or []:
        src = meta.get("document_id")
        if not src:
            continue
        meta["document_id"] = doc_id_map.setdefault(str(src), str(uuid.uuid4()))


def stamp_planner_server_key(metadatas, mcp_server_id: str | None) -> int:
    """Re-key planner cases to the server of the collection they are being written INTO.

    A planner repository is loaded for sessions on the MCP server it is keyed to, and
    retrieval additionally filters every case on ``metadata.mcp_server_id == the
    collection's key`` (rag_retriever.retrieve_examples, the safety layer). A case that
    arrives carrying the PUBLISHER's server key — every case in a pack export does — is
    therefore stored but never retrieved on the consumer. The fresh install has always
    re-keyed here; the in-place update's case patch did not (found 2026-09-16: the ten
    seeds a top-up added reached every consumer's store and none of its retrievals),
    so the rule is one function both paths call. Returns the number of rows re-keyed.
    The key is instance-local and never part of the case's content hash
    (trust.content strips it), so re-keying moves no signature.
    """
    if not mcp_server_id or not metadatas:
        return 0
    n = 0
    for meta in metadatas:
        if isinstance(meta, dict) and meta.get("mcp_server_id") != mcp_server_id:
            meta["mcp_server_id"] = mcp_server_id
            n += 1
    return n


def _add_batch_to_chroma(
    chroma_collection,
    batch: dict,
    collect_metadatas: bool,
    all_metadatas: list,
    mcp_server_id: str | None = None,
    doc_id_map: dict | None = None,
) -> int:
    """Add a batch of documents to ChromaDB collection.

    Returns the number of documents added.
    """
    ids = batch.get('ids', [])
    documents = batch.get('documents', [])
    metadatas = batch.get('metadatas', [])
    embeddings = batch.get('embeddings', [])

    if not ids or not documents:
        return 0

    # Instance-local identity first — same reason as the backend path.
    if doc_id_map is not None:
        _remap_document_ids(metadatas, doc_id_map)

    # Sanitize metadata: ChromaDB requires flat values, no None
    if metadatas:
        for meta in metadatas:
            for key in list(meta.keys()):
                if meta[key] is None:
                    meta[key] = ""

    # Stamp the TARGET server on planner cases so retrieval filters match (ONE rule,
    # shared with the in-place update's case patch — see `stamp_planner_server_key`).
    if metadatas:
        stamp_planner_server_key(metadatas, mcp_server_id)

    # Collect metadatas for knowledge_documents population
    if collect_metadatas and metadatas:
        all_metadatas.extend(metadatas)

    chroma_collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas if metadatas else None,
        embeddings=embeddings if embeddings else None,
    )

    return len(ids)


async def _add_batch_to_backend(
    backend,
    collection_name: str,
    batch: dict,
    collect_metadatas: bool,
    all_metadatas: list,
    doc_id_map: dict | None = None,
) -> int:
    """Add a batch of documents to a VectorStoreBackend.

    Returns the number of documents added.
    """
    from trusted_data_agent.vectorstore import VectorDocument

    ids = batch.get('ids', [])
    documents = batch.get('documents', [])
    metadatas = batch.get('metadatas', [])
    embeddings = batch.get('embeddings', [])

    if not ids or not documents:
        return 0

    # Instance-local identity BEFORE the metadata is written or collected, so the
    # vectors and the registry are built from the same ids (see _remap_document_ids).
    if doc_id_map is not None:
        _remap_document_ids(metadatas, doc_id_map)

    docs = [
        VectorDocument(
            id=ids[i],
            content=documents[i] or "",
            metadata={k: v for k, v in (metadatas[i] or {}).items() if v is not None},
            # Treat missing or empty embeddings as None so the backend re-embeds
            embedding=(embeddings[i] or None) if (embeddings and i < len(embeddings)) else None,
        )
        for i in range(len(ids))
    ]

    if collect_metadatas and metadatas:
        all_metadatas.extend(m for m in metadatas if m)

    await backend.add(collection_name, docs)
    return len(ids)


def _populate_knowledge_documents(collection_id: int, all_metadatas: list,
                                  registry: dict | None = None,
                                  doc_id_map: dict | None = None) -> int:
    """Populate the knowledge_documents table from chunk metadata. Returns doc count.

    CDC fields set at import time:
      ingest_epoch   — reset to now (chunks were re-ingested, not original upload time)
      content_hash   — taken VERBATIM from the exported document registry when the zip
                       carries one. Otherwise left NULL: the only text available here is
                       chunk METADATA, which contains no text at all, so a "reconstruction"
                       hashes the empty string and gives every document in the collection
                       the SAME hash — worse than absent, because the CDC diff would then
                       read two different documents as identical.
      embedding_model_locked — collection-level; stays 0 (set by reindex when user re-locks)
      source_uri / sync_enabled — intentionally left NULL/0 (user-private, not exported)
    """
    from trusted_data_agent.auth.database import DATABASE_URL

    try:
        db_path = DATABASE_URL.replace('sqlite:///', '')
        conn = sqlite_util.connect(db_path)
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        import_epoch = int(datetime.now(timezone.utc).timestamp())

        # Group chunks by document_id to reconstruct content_hash
        doc_chunks: dict = {}
        for meta in all_metadatas:
            doc_id = meta.get("document_id", "")
            if doc_id:
                doc_chunks.setdefault(doc_id, []).append(meta)

        # this instance's fresh id -> the exporting instance's id, so a registry keyed
        # by the source id can be read while the rows are written under the local one.
        src_by_new = {new: src for src, new in (doc_id_map or {}).items()}

        seen_doc_ids: set = set()
        inserted = 0
        for doc_id, chunks in doc_chunks.items():
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)

            # Use first chunk's metadata for document-level fields
            meta = chunks[0]

            # The exporting instance's registry entry for this document, if the zip
            # carried one. `doc_id` is this instance's fresh id, so it is resolved back
            # through the same map the vectors were written with.
            entry = (registry or {}).get(src_by_new.get(doc_id, ""), {}) or {}

            def _f(key, default=""):
                """Registry first — it holds the real values; chunk metadata as fallback."""
                v = entry.get(key)
                return v if v not in (None, "") else meta.get(key, default)

            # Only a hash that means something. See the docstring: there is no chunk text
            # here to hash, so absent beats a fabricated constant.
            content_hash = entry.get("content_hash") or None

            chunk_count = len(chunks)

            cursor.execute("""
                INSERT OR IGNORE INTO knowledge_documents
                (collection_id, document_id, filename, document_type, title,
                 author, source, category, tags, file_size, content_hash,
                 ingest_epoch, chunk_count, source_uri, sync_enabled, created_at,
                 scan_json, quarantined, quarantine_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, ?)
            """, (
                collection_id,
                doc_id,
                _f("filename"),
                _f("document_type", "json"),
                _f("title") or _f("filename"),
                _f("author"),
                "import",
                _f("category"),
                _f("tags"),
                entry.get("file_size") or 0,
                content_hash,
                import_epoch,
                chunk_count,
                now,
                entry.get("scan_json"),
                1 if entry.get("quarantined") else 0,
                entry.get("quarantine_reason"),
            ))

            # INSERT OR IGNORE succeeds silently on a constraint violation, so the
            # only honest count is what the cursor actually wrote. Reporting documents
            # ATTEMPTED is how a total failure to populate this table went unnoticed:
            # the import logged "Populated 10 documents" over an empty registry.
            inserted += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

        conn.commit()
        conn.close()
        attempted = len(seen_doc_ids)
        if inserted != attempted:
            app_logger.warning(
                f"knowledge_documents: collection {collection_id} attempted {attempted} "
                f"document(s) but wrote {inserted} — the rest were rejected by a "
                f"constraint and SILENTLY IGNORED. The registry is incomplete, so "
                f"document management, CDC sync and the trust hash will all see a "
                f"corpus that does not match its vectors.")
        else:
            app_logger.info(
                f"Populated {inserted} knowledge documents for collection {collection_id}")
        return inserted

    except Exception as e:
        app_logger.warning(f"Failed to populate knowledge_documents: {e}", exc_info=True)
        return 0


async def export_collection_to_zip(
    collection_id: int,
    user_uuid: str,
    output_path: Path | None = None,
) -> Path:
    """Export a collection to a ZIP file using batched JSONL format.

    Reads from ChromaDB in batches of BATCH_SIZE, writing one JSON object per line
    to documents.jsonl. Peak memory is O(BATCH_SIZE) regardless of collection size.

    Args:
        collection_id: Database collection ID.
        user_uuid: User performing the export (for ownership check).
        output_path: Directory for output. If None, uses a temp directory.

    Returns:
        Path to the created ZIP file.

    Raises:
        ValueError: Collection not found or not owned by user.
        RuntimeError: Export failure.
    """
    import shutil

    from trusted_data_agent.agent.rag_retriever import get_rag_retriever
    from trusted_data_agent.core.collection_db import CollectionDatabase

    temp_dir = tempfile.mkdtemp()
    temp_export_dir = Path(temp_dir)

    try:
        # Get collection from database
        db = CollectionDatabase()
        collection = db.get_collection_by_id(collection_id)

        if not collection:
            raise ValueError(f"Collection {collection_id} not found")

        if collection['owner_user_id'] != user_uuid:
            raise ValueError("You don't own this collection")

        # Get collection handle (knowledge: via backend; planner: raw ChromaDB)
        collection_name = collection['collection_name']
        repo_type = collection.get('repository_type', 'knowledge')
        backend_type = collection.get('backend_type', 'chromadb')

        retriever = get_rag_retriever()
        if not retriever:
            raise RuntimeError("RAG retriever not initialized")

        use_backend = (repo_type == 'knowledge')
        chroma_collection = None
        backend = None

        if use_backend:
            backend = await retriever._get_knowledge_backend(collection_id)
            total_count = await backend.count(collection_name)
        else:
            try:
                chroma_collection = retriever.client.get_collection(name=collection_name)
            except Exception as e:
                raise ValueError(f"ChromaDB collection not found: {e}")
            total_count = chroma_collection.count()

        app_logger.info(f"Export: Collection {collection_id} has {total_count} documents")

        # Build collection metadata
        metadata = {
            "collection_id": collection_id,
            "name": collection['name'],
            "description": collection['description'],
            "repository_type": repo_type,
            "backend_type": backend_type,
            "backend_config": collection.get('backend_config', '{}'),
            "vector_store_config_id": collection.get('vector_store_config_id'),
            "mcp_server_id": collection.get('mcp_server_id'),
            "chunking_strategy": collection['chunking_strategy'],
            "chunk_size": collection['chunk_size'],
            "chunk_overlap": collection['chunk_overlap'],
            "embedding_model": collection['embedding_model'],
            # CDC metadata — sync_interval exported so importers can set up schedules;
            # embedding_model_locked exported so importers know the intended model.
            # source_uri and sync_enabled are intentionally NOT exported (user-private data).
            "sync_interval": collection.get('sync_interval', 'daily'),
            "embedding_model_locked": 0,  # always reset to 0 on import (importer must re-lock after reindex)
            "collection_name": collection_name,
            "document_count": total_count,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "export_version": "1.1"
        }

        # Embed resolved vector store config (without credentials) for portability
        vs_config_id = collection.get('vector_store_config_id')
        if vs_config_id:
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                config_manager = get_config_manager()
                vs_configs = config_manager.get_vector_store_configurations(user_uuid)
                vs_config = next((c for c in vs_configs if c.get("id") == vs_config_id), None)
                if vs_config:
                    metadata["vector_store_config"] = {
                        "name": vs_config.get("name"),
                        "backend_type": vs_config.get("backend_type"),
                        "backend_config": vs_config.get("backend_config", {}),
                    }
            except Exception as e:
                app_logger.warning(f"Could not resolve vector store config for export: {e}")

        metadata_file = temp_export_dir / "collection_metadata.json"
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

        # The DOCUMENT REGISTRY — one entry per document, keyed by its id in THIS
        # instance, carrying the facts that cannot be recovered from chunk metadata.
        #
        # `content_hash` above all: it is the hash of the document's real bytes, taken at
        # ingest, and it is what `trust.content.knowledge_content` signs — so a copy whose
        # registry is rebuilt by guesswork can never match the signature of the corpus it
        # was copied from. The importer cannot reconstruct it either: it only ever sees
        # chunk METADATA (the text lives in a separate list), so a reconstruction hashes
        # the empty string and returns one identical, meaningless hash for every document.
        #
        # Absent for a planner repo, and absent from any zip written before this existed —
        # the importer falls back to deriving what it can, and says so.
        registry_file = temp_export_dir / "document_registry.json"
        try:
            from trusted_data_agent.core.collection_db import get_collection_db as _gcdb
            reg_rows = _gcdb().get_all_documents_in_collection(int(collection_id)) or []
            registry = {
                str(r.get("document_id")): {
                    k: r.get(k) for k in _REGISTRY_FIELDS
                }
                for r in reg_rows if r.get("document_id")
            }
            if registry:
                with open(registry_file, "w") as f:
                    json.dump(registry, f, indent=2)
                app_logger.info(
                    f"Export: wrote document registry for {len(registry)} document(s)")
        except Exception as e:  # never block an export on the registry
            app_logger.warning(f"Export: could not write document registry: {e}")

        # Export documents in batched JSONL format
        documents_file = temp_export_dir / "documents.jsonl"

        with open(documents_file, 'w') as f:
            offset = 0
            batch_num = 0
            while offset < total_count:
                if use_backend:
                    get_result = await backend.get(
                        collection_name,
                        include_embeddings=True,
                        include_documents=True,
                        include_metadata=True,
                        limit=BATCH_SIZE,
                        offset=offset,
                    )
                    if not get_result.documents:
                        break
                    batch_ids = [d.id for d in get_result.documents]
                    embeddings_list = []
                    for d in get_result.documents:
                        emb = d.embedding
                        if emb is None:
                            embeddings_list.append([])
                        elif isinstance(emb, list):
                            embeddings_list.append(emb)
                        else:
                            embeddings_list.append(list(emb))
                    batch_obj = {
                        'ids': batch_ids,
                        'documents': [d.content for d in get_result.documents],
                        'metadatas': [d.metadata for d in get_result.documents],
                        'embeddings': embeddings_list,
                    }
                else:
                    batch_data = chroma_collection.get(
                        offset=offset,
                        limit=BATCH_SIZE,
                        include=['embeddings', 'documents', 'metadatas']
                    )
                    if not batch_data['ids']:
                        break
                    batch_ids = batch_data['ids']
                    embeddings_list = []
                    for emb in batch_data['embeddings']:
                        if isinstance(emb, list):
                            embeddings_list.append(emb)
                        else:
                            embeddings_list.append(emb.tolist() if hasattr(emb, 'tolist') else list(emb))
                    batch_obj = {
                        'ids': batch_ids,
                        'documents': batch_data['documents'],
                        'metadatas': batch_data['metadatas'],
                        'embeddings': embeddings_list,
                    }

                f.write(json.dumps(batch_obj, allow_nan=False) + '\n')
                batch_num += 1
                offset += len(batch_ids)
                app_logger.info(f"Export: Wrote batch {batch_num} ({offset}/{total_count} documents)")

        # Create ZIP file
        safe_name = re.sub(r'[<>:"/\\|?*]', '-', collection['name'])
        safe_name = safe_name.replace(' ', '_')
        safe_name = safe_name.strip('-_')
        zip_filename = f"collection_{collection_id}_{safe_name}.zip"

        if output_path:
            output_dir = Path(output_path)
            output_dir.mkdir(parents=True, exist_ok=True)
            zip_path = output_dir / zip_filename
        else:
            zip_path = temp_export_dir / zip_filename

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in temp_export_dir.rglob('*'):
                if file_path.is_file() and file_path != zip_path:
                    arcname = file_path.relative_to(temp_export_dir)
                    zipf.write(file_path, arcname)

        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        app_logger.info(f"Exported collection {collection_id} to {zip_filename} ({zip_size_mb:.2f} MB)")

        return zip_path

    except (ValueError, RuntimeError):
        raise
    except Exception as e:
        app_logger.error(f"Error exporting collection: {e}", exc_info=True)
        raise RuntimeError(f"Failed to export collection: {e}")
