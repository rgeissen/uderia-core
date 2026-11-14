"""
Knowledge Repository REST API Endpoints

Provides endpoints for creating and managing Knowledge repositories.
Follows the same patterns as Planner repositories but with document-centric operations.

Endpoints:
- POST /v1/knowledge/repositories/{collection_id}/documents - Upload document to Knowledge repository
- GET /v1/knowledge/repositories/{collection_id}/documents - List documents in repository
- GET /v1/knowledge/repositories/{collection_id}/documents/{document_id} - Get document details
- DELETE /v1/knowledge/repositories/{collection_id}/documents/{document_id} - Delete document
- POST /v1/knowledge/repositories/{collection_id}/search - Search within Knowledge repository
"""

import os
import logging
import tempfile
import time
from datetime import datetime, timezone
from uuid import uuid4
from quart import Blueprint, request, jsonify, Response

from trusted_data_agent.api.operations import Arg, operation
import hashlib
import asyncio
import json

from trusted_data_agent.core.config import APP_STATE
from trusted_data_agent.core.collection_db import get_collection_db
from trusted_data_agent.auth.middleware import require_auth
from trusted_data_agent.agent.rag_retriever import get_rag_retriever
from trusted_data_agent.llm.document_upload import DocumentUploadHandler
from trusted_data_agent.agent.repository_constructor import (
    create_repository_constructor,
    RepositoryType,
    ChunkingStrategy
)
from trusted_data_agent.vectorstore.capabilities import VectorStoreCapability
from trusted_data_agent.vectorstore.types import ServerSideChunkingConfig

# Create blueprint
knowledge_api_bp = Blueprint('knowledge_api', __name__)

app_logger = logging.getLogger('tda')


async def _verify_chunks_indexed(backend, collection_name: str, filename: str, collection_id: int) -> int:
    """Verify that at least one chunk was stored after an upload.

    Returns the chunk count. Raises ValueError if count is 0 (upload appeared
    successful but nothing was indexed). Returns -1 if verification is skipped
    or the count cannot be determined — callers should log but not block.

    Server-side chunking backends (Teradata EVS) are all-or-nothing: if
    add_document_files() returned without exception and the VS is in READY
    state, data is guaranteed present. Calling count() on a REST-only
    initialized Teradata backend returns 0 (no SQL connection) even when
    chunks exist, so we skip verification for those backends.
    """
    from trusted_data_agent.vectorstore.capabilities import VectorStoreCapability
    if hasattr(backend, 'has_capability') and backend.has_capability(VectorStoreCapability.SERVER_SIDE_CHUNKING):
        # EVS atomicity guarantees: no exception from add_document_files() = data present
        return -1
    try:
        count = await backend.count(collection_name)
    except Exception as e:
        app_logger.warning(
            f"[UPLOAD] Could not verify chunk count for '{filename}' "
            f"(collection {collection_id}): {e}"
        )
        return -1
    if count == 0:
        raise ValueError(
            f"Document '{filename}' was processed but no chunks were stored in "
            f"collection {collection_id}. The upload may have failed silently — "
            "please try again or check server logs."
        )
    return count


@knowledge_api_bp.route("/v1/knowledge/preview-chunking", methods=["POST"])
@require_auth
async def preview_document_chunking(current_user: dict):
    """
    Preview how a document will be chunked without storing it.
    
    This endpoint allows users to experiment with different chunking strategies
    before committing to creating a Knowledge repository.
    
    Multipart Form Data:
        - file: Document file (PDF, TXT, DOCX, MD)
        - chunking_strategy: Chunking strategy (fixed_size, paragraph, sentence, semantic)
        - chunk_size: Size of chunks in characters (default: 1000)
        - chunk_overlap: Overlap between chunks (default: 200)
    
    Returns:
        JSON with chunks array containing text segments
    """
    try:
        # Get uploaded file
        files = await request.files
        if 'file' not in files:
            return jsonify({"status": "error", "message": "No file provided"}), 400
        
        file = files['file']
        if not file or not file.filename:
            return jsonify({"status": "error", "message": "Invalid file"}), 400
        
        # Get form parameters
        form = await request.form
        chunking_strategy_str = form.get('chunking_strategy', 'semantic')
        chunk_size = int(form.get('chunk_size', 1000))
        chunk_overlap = int(form.get('chunk_overlap', 200))
        
        # Validate chunking strategy
        try:
            chunking_strategy = ChunkingStrategy[chunking_strategy_str.upper()]
        except KeyError:
            return jsonify({
                "status": "error",
                "message": f"Invalid chunking_strategy: {chunking_strategy_str}. Valid options: fixed_size, paragraph, sentence, semantic"
            }), 400
        
        # Save file temporarily
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1])
        file_content = file.read()
        temp_file.write(file_content)
        temp_file.flush()
        temp_file.close()
        
        try:
            # Extract text from document - optimized for preview
            file_extension = os.path.splitext(temp_file.name)[1].lower()
            
            # Fast extraction for preview - limit PDF to first 20 pages
            if file_extension == '.pdf':
                try:
                    from PyPDF2 import PdfReader
                    with open(temp_file.name, 'rb') as f:
                        pdf_reader = PdfReader(f)
                        # Only extract first 20 pages for instant preview
                        max_pages = min(20, len(pdf_reader.pages))
                        pages_text = [pdf_reader.pages[i].extract_text() for i in range(max_pages)]
                        # Join with double newline to preserve paragraph boundaries
                        document_text = "\n\n".join(pages_text)
                        is_preview_truncated = len(pdf_reader.pages) > max_pages
                except Exception as e:
                    return jsonify({"status": "error", "message": f"PDF extraction failed: {str(e)}"}), 400
            else:
                # For non-PDF, use standard extraction
                doc_handler = DocumentUploadHandler()
                extracted = doc_handler._extract_text_from_document(temp_file.name)
                document_text = extracted.get('content', '')
                is_preview_truncated = False
            
            if not document_text:
                return jsonify({
                    "status": "error",
                    "message": "No text content extracted from document"
                }), 400
            
            # Limit to reasonable size for preview (50K chars)
            preview_text = document_text[:50000] if len(document_text) > 50000 else document_text
            if len(document_text) > 50000:
                is_preview_truncated = True
            
            # Create document processor to chunk the document
            from trusted_data_agent.agent.repository_constructor import DocumentProcessor
            doc_processor = DocumentProcessor(
                chunking_strategy=chunking_strategy,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap
            )
            
            # Chunk the preview text
            metadata = {"filename": file.filename, "source": "preview"}
            chunk_objects = doc_processor.process_document(preview_text, metadata)
            
            # Format chunks for preview
            preview_chunks = [
                {
                    "text": chunk.content,
                    "length": len(chunk.content),
                    "index": chunk.chunk_index
                }
                for chunk in chunk_objects
            ]
            
            return jsonify({
                "status": "success",
                "chunks": preview_chunks,
                "total_chunks": len(preview_chunks),
                "total_characters": len(preview_text),
                "full_document_characters": len(document_text),
                "is_preview_truncated": is_preview_truncated,
                "average_chunk_size": len(preview_text) // len(preview_chunks) if preview_chunks else 0,
                "chunking_strategy": chunking_strategy_str,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "preview_note": "Showing first 20 pages" if is_preview_truncated and file_extension == '.pdf' else None
            }), 200
            
        finally:
            # Clean up temp file
            if os.path.exists(temp_file.name):
                os.unlink(temp_file.name)
                
    except Exception as e:
        app_logger.error(f"[Knowledge Preview] Error: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


async def upload_knowledge_document_stream(current_user: dict, collection_id: int):
    """
    Upload a document to a Knowledge repository with SSE progress updates.
    This version streams progress updates to prevent SSE timeout on slow servers.
    """
    # Extract ALL request data BEFORE starting the generator (must be in request context)
    files = await request.files
    if 'file' not in files:
        return jsonify({"status": "error", "message": "No file provided"}), 400
    
    file = files['file']
    if not file or not file.filename:
        return jsonify({"status": "error", "message": "Invalid file"}), 400
    
    # Read file content now
    file_content = file.read()
    filename = file.filename
    
    # Get all form parameters now
    form = await request.form
    title = form.get('title', filename)
    author = form.get('author', '')
    category = form.get('category', '')
    tags_str = form.get('tags', '')
    tags = [t.strip() for t in tags_str.split(',') if t.strip()]
    
    chunking_strategy_str = form.get('chunking_strategy', 'semantic')
    chunk_size = int(form.get('chunk_size', 1000))
    chunk_overlap = int(form.get('chunk_overlap', 200))
    embedding_model = form.get('embedding_model', 'all-MiniLM-L6-v2')
    source_uri = form.get('source_uri', '') or None
    sync_enabled = 1 if form.get('sync_enabled', '').lower() == 'true' and source_uri else 0
    
    def format_sse(data: dict, event: str = "message") -> str:
        """Format data as Server-Sent Event."""
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"
    
    async def generate_upload_stream():
        """Generator function that yields SSE progress updates."""
        progress_queue = asyncio.Queue()
        
        async def progress_callback(message: str, percentage: int):
            """Callback to report progress during upload."""
            await progress_queue.put(format_sse({
                "type": "progress",
                "message": message,
                "percentage": percentage
            }, "progress"))
        
        try:
            # Send initial progress
            yield format_sse({"type": "start", "message": "Starting upload..."}, "progress")
            
            # Query collection from database
            from trusted_data_agent.core.collection_db import CollectionDatabase
            db = CollectionDatabase()
            conn = db._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT collection_name, repository_type, owner_user_id, chunking_strategy,
                       chunk_size, chunk_overlap,
                       optimized_chunking, ss_chunk_size, header_height, footer_height,
                       embedding_model, embedding_model_locked
                FROM collections WHERE id = ?
            """, (collection_id,))
            
            result = cursor.fetchone()
            conn.close()
            
            if not result:
                yield format_sse({"type": "error", "message": f"Collection {collection_id} not found"}, "error")
                return
            
            collection_name = result['collection_name']
            repository_type = result['repository_type']
            owner_user_id = result['owner_user_id']
            # Collection-level server-side chunking defaults
            coll_optimized = bool(result['optimized_chunking']) if result['optimized_chunking'] is not None else True
            coll_ss_chunk_size = result['ss_chunk_size'] if result['ss_chunk_size'] is not None else 2000
            coll_header_height = result['header_height'] if result['header_height'] is not None else 0
            coll_footer_height = result['footer_height'] if result['footer_height'] is not None else 0
            
            if repository_type != 'knowledge':
                yield format_sse({"type": "error", "message": f"Collection {collection_id} is not a Knowledge repository"}, "error")
                return
            
            user_id = current_user.id
            if owner_user_id != user_id:
                yield format_sse({"type": "error", "message": "Access denied"}, "error")
                return
            
            # Get retriever instance
            retriever = get_rag_retriever()
            if not retriever:
                yield format_sse({"type": "error", "message": "RAG retriever not initialized"}, "error")
                return
            
            # Save file temporarily (needed by both client-side and server-side paths)
            yield format_sse({"type": "progress", "message": "Saving file...", "percentage": 2}, "progress")

            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1])
            temp_file.write(file_content)
            temp_file.flush()
            temp_file.close()

            # Calculate file hash
            content_hash = hashlib.sha256(file_content).hexdigest()

            # ── CDC: hash check — skip re-embedding if content unchanged ──────
            _cdc_db = get_collection_db()
            _existing_doc = _cdc_db.get_document_by_filename(collection_id, filename)
            if _existing_doc and _existing_doc.get("content_hash") == content_hash:
                _cdc_db.mark_document_checked(_existing_doc["document_id"])
                os.unlink(temp_file.name)
                yield format_sse({
                    "type": "complete",
                    "status": "unchanged",
                    "message": f"'{filename}' — content unchanged, skipped re-embedding",
                    "document_id": _existing_doc["document_id"],
                    "chunks_stored": 0,
                }, "complete")
                return

            # Reuse existing document_id on update, generate new one on insert
            _cdc_document_id = _existing_doc["document_id"] if _existing_doc else str(uuid4())
            _ingest_epoch = int(time.time())

            # ── CDC: embedding model mismatch guard ───────────────────────────
            # Warn (non-blocking) when the collection has a locked embedding
            # model that differs from the model being used for this upload.
            # Mixed embeddings degrade search quality — user should reindex.
            _coll_embedding_model = result.get("embedding_model") or "all-MiniLM-L6-v2"
            _coll_model_locked = bool(result.get("embedding_model_locked"))
            if _coll_model_locked and embedding_model != _coll_embedding_model:
                yield format_sse({
                    "type": "model_mismatch",
                    "collection_embedding_model": _coll_embedding_model,
                    "upload_embedding_model": embedding_model,
                    "message": (
                        f"Warning: collection uses '{_coll_embedding_model}' but upload uses "
                        f"'{embedding_model}'. Mixed embeddings degrade search quality. "
                        f"Use the Re-index button to rebuild all documents with a single model."
                    ),
                }, "model_mismatch")

            # ── Server-side chunking path (streaming) ─────────────────────────
            # Pass the raw file to the backend SDK — skip text extraction &
            # local chunking entirely.
            if chunking_strategy_str == "server_side":
                backend = await retriever._get_knowledge_backend(collection_id)

                if not backend or not backend.has_capability(VectorStoreCapability.SERVER_SIDE_CHUNKING):
                    yield format_sse({
                        "type": "error",
                        "message": "Selected backend does not support server-side chunking."
                    }, "error")
                    os.unlink(temp_file.name)
                    return

                document_id = _cdc_document_id  # preserve ID across updates

                yield format_sse({
                    "type": "progress",
                    "message": "Uploading file to Teradata for server-side chunking & embedding...",
                    "percentage": 10
                }, "progress")

                # Run server-side chunking as a background task.
                # The progress_callback feeds real EVS status phases into
                # the queue so we can relay them as SSE events.
                #
                # IMPORTANT: The wrapper task handles ALL critical DB
                # writes (document metadata + count persistence) so they
                # execute even when the SSE connection drops during long
                # Teradata processing (15-30+ min for large PDFs).
                import asyncio as _asyncio
                progress_queue = _asyncio.Queue()

                def _on_ingest_progress(update):
                    progress_queue.put_nowait(update)

                # Shared dict for the wrapper to communicate results back
                # to the SSE generator (if it's still alive).
                _ingest_result: dict = {"success": False}

                async def _ingest_and_persist():
                    """Wrapper: ingest file → persist metadata & counts.

                    Runs as an asyncio task so DB writes happen even if
                    the SSE generator is abandoned by client disconnect.
                    """
                    await backend.add_document_files(
                        collection_name=collection_name,
                        file_paths=[temp_file.name],
                        chunking_config=ServerSideChunkingConfig(
                            optimized_chunking=form.get("optimized_chunking", str(coll_optimized).lower()).lower() == "true",
                            chunk_size=int(form.get("chunk_size", coll_ss_chunk_size)),
                            header_height=int(form.get("header_height", coll_header_height)),
                            footer_height=int(form.get("footer_height", coll_footer_height)),
                        ),
                        progress_callback=_on_ingest_progress,
                    )

                    # Verify chunks were actually stored before writing DB record
                    try:
                        await _verify_chunks_indexed(backend, collection_name, filename, collection_id)
                    except ValueError as verify_err:
                        app_logger.error(
                            f"[UPLOAD] Server-side ingest of '{filename}' completed but "
                            f"no chunks stored: {verify_err}"
                        )
                        return  # Skip DB write — don't register a document with 0 chunks

                    # ── Persist document metadata via CDC upsert ────────
                    try:
                        get_collection_db().upsert_document_metadata(
                            document_id=document_id,
                            collection_id=collection_id,
                            filename=filename,
                            content_hash=content_hash,
                            ingest_epoch=_ingest_epoch,
                            chunk_count=0,  # deferred — updated by _deferred_chunk_count
                            source_uri=source_uri,
                            sync_enabled=sync_enabled,
                            document_type=os.path.splitext(filename)[1].lstrip('.'),
                            title=title,
                            author=author,
                            source='upload',
                            category=category,
                            tags=','.join(tags),
                            file_size=len(file_content),
                        )
                    except Exception as meta_err:
                        app_logger.warning(
                            f"Failed to persist document metadata for "
                            f"'{filename}': {meta_err}"
                        )

                    # ── RAG Trust Hardening Wave 2: ingest firewall (observe-only) ──
                    from trusted_data_agent.agent.guardrails import corpus_screen
                    corpus_screen.scan_and_store(document_id, filename, file_content,
                                                 collection_id=collection_id)

                    # ── Authoritative count recompute ────────────────────
                    get_collection_db().sync_collection_counts(collection_id)

                    # ── Clean up temp file ────────────────────────────────
                    try:
                        os.unlink(temp_file.name)
                    except OSError:
                        pass

                    _ingest_result["success"] = True
                    app_logger.info(
                        f"Server-side ingest of '{filename}' complete "
                        f"(collection={collection_id})"
                    )

                ingest_task = _asyncio.create_task(_ingest_and_persist())

                last_pct = 10
                last_phase = "Processing"
                ingest_start = time.monotonic()
                while not ingest_task.done():
                    try:
                        update = await _asyncio.wait_for(progress_queue.get(), timeout=10)
                        pct = max(update.percentage, last_pct)  # never go backwards
                        last_pct = pct
                        last_phase = update.phase
                        minutes = update.elapsed_seconds // 60
                        seconds = update.elapsed_seconds % 60
                        time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
                        yield format_sse({
                            "type": "progress",
                            "message": f"{update.phase} ({time_str} elapsed)",
                            "percentage": pct
                        }, "progress")
                    except _asyncio.TimeoutError:
                        # No status from backend in 10s — send heartbeat with
                        # updated elapsed time so the UI shows the clock ticking.
                        elapsed = int(time.monotonic() - ingest_start)
                        minutes = elapsed // 60
                        seconds = elapsed % 60
                        time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
                        yield format_sse({
                            "type": "progress",
                            "message": f"{last_phase} ({time_str} elapsed)",
                            "percentage": last_pct
                        }, "progress")

                # Retrieve result — raises if the task failed
                try:
                    await ingest_task
                except Exception as ingest_err:
                    # Clean up the failed EVS object on Teradata so it
                    # doesn't leave a ghost dictionary entry.
                    try:
                        await backend.delete_collection(collection_name)
                        app_logger.info(f"Cleaned up failed VS '{collection_name}' on Teradata")
                    except Exception as cleanup_err:
                        app_logger.warning(
                            f"Could not clean up failed VS '{collection_name}': {cleanup_err}"
                        )

                    # If this was the first upload (no documents yet), remove
                    # the orphaned local collection record — an empty knowledge
                    # repo with no backend VS is useless.
                    try:
                        from trusted_data_agent.core.collection_db import CollectionDatabase
                        _cleanup_db = CollectionDatabase()
                        _cleanup_conn = _cleanup_db._get_connection()
                        _cleanup_cur = _cleanup_conn.cursor()
                        _cleanup_cur.execute(
                            "SELECT COUNT(*) as cnt FROM knowledge_documents WHERE collection_id = ?",
                            (collection_id,),
                        )
                        doc_count = _cleanup_cur.fetchone()["cnt"]
                        _cleanup_conn.close()
                        if doc_count == 0:
                            get_collection_db().delete_collection(collection_id)
                            app_logger.info(
                                f"Removed empty orphaned collection {collection_id} "
                                f"('{collection_name}')"
                            )
                    except Exception as local_err:
                        app_logger.warning(
                            f"Could not clean up local collection {collection_id}: {local_err}"
                        )

                    yield format_sse({
                        "type": "error",
                        "message": f"Server-side chunking failed: {ingest_err}"
                    }, "error")
                    return

                # Document metadata + document count already persisted
                # inside _ingest_and_persist().  Schedule deferred chunk
                # count (backend may not be queryable immediately after
                # EVS reports READY).
                async def _deferred_chunk_count(
                    _backend, _coll_name, _coll_id, _user_uuid,
                    initial_delay=30, interval=30, max_retries=3,
                ):
                    """Background: retry count() until non-zero, then persist & notify.

                    Schedule: 30s → 60s → 90s (3 attempts over ~90s window).
                    """
                    await asyncio.sleep(initial_delay)
                    for attempt in range(max_retries):
                        if attempt > 0:
                            await asyncio.sleep(interval)
                        try:
                            total = await _backend.count(_coll_name)
                            if total and total > 0:
                                # "Max wins" guard: only update if the
                                # new count exceeds the persisted value.
                                # Prevents an older deferred task from
                                # overwriting a newer, higher count.
                                current = get_collection_db().get_collection_by_id(_coll_id)
                                current_chunks = (current or {}).get("chunk_count", 0) or 0
                                if total <= current_chunks:
                                    app_logger.info(
                                        f"Deferred count for collection "
                                        f"{_coll_id}: {total} <= current "
                                        f"{current_chunks}, skipping"
                                    )
                                    return
                                get_collection_db().update_counts(
                                    _coll_id, chunk_count=total
                                )
                                app_logger.info(
                                    f"Deferred chunk count for collection "
                                    f"{_coll_id}: {total} chunks"
                                )
                                # Notify frontend to refresh cards
                                queues = APP_STATE.get(
                                    "notification_queues", {}
                                ).get(_user_uuid, set())
                                for q in queues:
                                    asyncio.create_task(q.put({
                                        "type": "knowledge_counts_updated",
                                        "payload": {
                                            "collection_id": _coll_id,
                                            "chunk_count": total,
                                        },
                                    }))
                                return
                        except Exception as exc:
                            app_logger.debug(
                                f"Deferred count attempt {attempt+1} for "
                                f"collection {_coll_id}: {exc}"
                            )
                    app_logger.warning(
                        f"Deferred chunk count gave up after {max_retries} "
                        f"attempts for collection {_coll_id}"
                    )

                asyncio.create_task(
                    _deferred_chunk_count(
                        backend, collection_name, collection_id, user_id,
                    )
                )

                yield format_sse({
                    "type": "complete",
                    "status": "success",
                    "message": f"Document '{filename}' ingested via server-side chunking",
                    "document_id": document_id,
                    "chunks_stored": 0,
                    "chunking_mode": "server_side",
                    "deferred_count": True,
                }, "complete")
                return

            # ── Client-side chunking path ─────────────────────────────────────
            # Validate chunking strategy
            try:
                chunking_strategy = ChunkingStrategy[chunking_strategy_str.upper()]
            except KeyError:
                yield format_sse({"type": "error", "message": f"Invalid chunking_strategy: {chunking_strategy_str}"}, "error")
                os.unlink(temp_file.name)
                return

            # Process document
            yield format_sse({"type": "progress", "message": "Extracting text from document...", "percentage": 3}, "progress")

            doc_handler = DocumentUploadHandler()
            prepared_doc = doc_handler.prepare_document_for_llm(
                file_path=temp_file.name,
                provider_name="Ollama",
                model_name="",
                effective_config={"enabled": True, "use_native_upload": False}
            )

            if 'content' not in prepared_doc or not prepared_doc['content']:
                yield format_sse({"type": "error", "message": "Failed to extract text from document"}, "error")
                os.unlink(temp_file.name)
                return

            document_content = prepared_doc['content']

            # Create repository constructor (backend-abstracted path)
            backend = await retriever._get_knowledge_backend(collection_id)
            constructor = create_repository_constructor(
                repository_type=RepositoryType.KNOWLEDGE,
                chroma_client=retriever.client,
                storage_dir=retriever.rag_cases_dir / f"collection_{collection_id}",
                embedding_model=embedding_model,
                chunking_strategy=chunking_strategy,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                backend=backend,
            )

            # Construct repository entry with progress updates
            result = await constructor.construct_async(
                collection_id=collection_id,
                content=document_content,
                collection_name=collection_name,
                filename=filename,
                document_type=os.path.splitext(filename)[1].lstrip('.'),
                title=title,
                author=author,
                category=category,
                tags=tags,
                source='upload',
                file_size=len(file_content),
                content_hash=content_hash,
                ingest_epoch=_ingest_epoch,
                document_id=_cdc_document_id,
                save_original=True,
                progress_callback=progress_callback
            )

            # Drain any remaining progress messages
            while not progress_queue.empty():
                try:
                    progress_msg = progress_queue.get_nowait()
                    yield progress_msg
                except asyncio.QueueEmpty:
                    break

            # Clean up temp file
            os.unlink(temp_file.name)

            if result['status'] == 'success':
                # Verify chunks were actually stored before writing DB record
                try:
                    await _verify_chunks_indexed(backend, collection_name, filename, collection_id)
                except ValueError as verify_err:
                    yield format_sse({"type": "error", "message": str(verify_err)}, "error")
                    return

                chunks_stored = result.get('chunks_stored', 0)

                # ── CDC: delete stale chunks from previous ingest epoch ────────
                # New chunks are already stored; now remove any chunks written
                # during an earlier ingest for this document (identified by a
                # lower ingest_epoch in their metadata).
                if _existing_doc:
                    try:
                        from trusted_data_agent.vectorstore.filters import FieldFilter, AndFilter, FilterOp
                        stale_filter = AndFilter([
                            FieldFilter("document_id", FilterOp.EQ, _cdc_document_id),
                            FieldFilter("ingest_epoch", FilterOp.LT, _ingest_epoch),
                        ])
                        stale_result = await backend.get(
                            collection_name,
                            where=stale_filter,
                            include_documents=False,
                            include_metadata=False,
                            limit=10_000,
                        )
                        stale_ids = [d.id for d in stale_result.documents] if stale_result and stale_result.documents else []
                        if stale_ids:
                            await backend.delete(collection_name, ids=stale_ids)
                            app_logger.info(
                                f"[CDC] Removed {len(stale_ids)} stale chunks for "
                                f"'{filename}' (doc={_cdc_document_id})"
                            )
                    except Exception as cleanup_err:
                        # Non-fatal: stale chunks will be swept on next write
                        app_logger.warning(
                            f"[CDC] Stale chunk cleanup skipped for '{filename}': {cleanup_err}"
                        )

                # ── Persist document metadata via CDC upsert ──────────────────
                get_collection_db().upsert_document_metadata(
                    document_id=_cdc_document_id,
                    collection_id=collection_id,
                    filename=filename,
                    content_hash=content_hash,
                    ingest_epoch=_ingest_epoch,
                    chunk_count=chunks_stored,
                    source_uri=source_uri,
                    sync_enabled=sync_enabled,
                    document_type=result['metadata'].get('document_type', ''),
                    title=title,
                    author=author,
                    source='upload',
                    category=category,
                    tags=','.join(tags),
                    file_size=len(file_content),
                )

                # ── RAG Trust Hardening Wave 2: ingest firewall (observe-only) ──
                from trusted_data_agent.agent.guardrails import corpus_screen
                corpus_screen.scan_and_store(_cdc_document_id, filename, file_content,
                                             collection_id=collection_id)

                # ── Authoritative count recompute ─────────────────────────────
                get_collection_db().sync_collection_counts(collection_id)

                write_status = "updated" if _existing_doc else "created"
                yield format_sse({
                    "type": "complete",
                    "status": write_status,
                    "message": f"Successfully uploaded {filename}",
                    "document_id": _cdc_document_id,
                    "chunks_stored": chunks_stored
                }, "complete")
            else:
                yield format_sse({
                    "type": "error",
                    "message": result.get('message', 'Upload failed')
                }, "error")
                
        except Exception as e:
            app_logger.error(f"Error in streaming upload: {e}", exc_info=True)
            yield format_sse({"type": "error", "message": str(e)}, "error")
    
    return Response(generate_upload_stream(), mimetype="text/event-stream")


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/documents", methods=["POST"])
@require_auth
async def upload_knowledge_document(current_user: dict, collection_id: int):
    """
    Upload a document to a Knowledge repository with SSE progress updates.
    
    Multipart Form Data:
        - file: Document file (PDF, TXT, DOCX, etc.)
        - title: Document title (optional)
        - author: Document author (optional)
        - category: Document category (optional)
        - tags: Comma-separated tags (optional)
        - chunking_strategy: Chunking strategy (fixed_size, paragraph, sentence, semantic)
        - chunk_size: Size of chunks in characters (default: 1000)
        - chunk_overlap: Overlap between chunks (default: 200)
        - embedding_model: Embedding model to use (default: all-MiniLM-L6-v2)
        - stream: Set to 'true' for SSE progress updates (optional)
    """
    # Check if client wants SSE streaming
    form = await request.form
    use_streaming = form.get('stream', '').lower() == 'true'
    
    if use_streaming:
        return await upload_knowledge_document_stream(current_user, collection_id)
    
    # Otherwise use original JSON response
    try:
        # Query collection directly from database
        from trusted_data_agent.core.collection_db import CollectionDatabase
        db = CollectionDatabase()
        conn = db._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT collection_name, repository_type, owner_user_id, chunking_strategy,
                   chunk_size, chunk_overlap,
                   optimized_chunking, ss_chunk_size, header_height, footer_height
            FROM collections WHERE id = ?
        """, (collection_id,))

        result = cursor.fetchone()
        conn.close()

        if not result:
            return jsonify({"status": "error", "message": f"Collection {collection_id} not found"}), 404

        collection_name = result['collection_name']
        repository_type = result['repository_type']
        owner_user_id = result['owner_user_id']
        # Collection-level server-side chunking defaults
        coll_optimized = bool(result['optimized_chunking']) if result['optimized_chunking'] is not None else True
        coll_ss_chunk_size = result['ss_chunk_size'] if result['ss_chunk_size'] is not None else 2000
        coll_header_height = result['header_height'] if result['header_height'] is not None else 0
        coll_footer_height = result['footer_height'] if result['footer_height'] is not None else 0
        
        if repository_type != 'knowledge':
            return jsonify({
                "status": "error",
                "message": f"Collection {collection_id} is not a Knowledge repository"
            }), 400
        
        user_id = current_user.id
        if owner_user_id != user_id:
            return jsonify({"status": "error", "message": "Access denied"}), 403
        
        # Get retriever instance for storage directory
        retriever = get_rag_retriever()
        if not retriever:
            return jsonify({"status": "error", "message": "RAG retriever not initialized"}), 500
        
        # Get uploaded file
        files = await request.files
        if 'file' not in files:
            return jsonify({"status": "error", "message": "No file provided"}), 400
        
        file = files['file']
        if not file or not file.filename:
            return jsonify({"status": "error", "message": "Invalid file"}), 400
        
        # Get form parameters
        form = await request.form
        title = form.get('title', file.filename)
        author = form.get('author', '')
        category = form.get('category', '')
        tags_str = form.get('tags', '')
        tags = [t.strip() for t in tags_str.split(',') if t.strip()]
        
        chunking_strategy_str = form.get('chunking_strategy', 'semantic')
        chunk_size = int(form.get('chunk_size', 1000))
        chunk_overlap = int(form.get('chunk_overlap', 200))
        embedding_model = form.get('embedding_model', 'all-MiniLM-L6-v2')

        # Save file temporarily (needed by both client-side and server-side paths)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1])
        file_content = file.read()
        temp_file.write(file_content)
        temp_file.flush()
        temp_file.close()

        # Calculate file hash
        content_hash = hashlib.sha256(file_content).hexdigest()

        # ── Existing document by filename: refresh in place, never duplicate ──
        # The streaming route has had this contract since CDC landed; this route
        # did not, so every re-upload of a changed file added a SECOND document
        # (observed: a 10-file corpus at 20 rows after one refresh). Same rule:
        # unchanged content is skipped; changed content reuses the document id,
        # its old chunks and row are cleared, and the new ingest replaces them.
        _existing_doc = get_collection_db().get_document_by_filename(collection_id, file.filename)
        if _existing_doc and _existing_doc.get("content_hash") == content_hash:
            get_collection_db().mark_document_checked(_existing_doc["document_id"])
            os.unlink(temp_file.name)
            return jsonify({
                "status": "unchanged",
                "message": f"'{file.filename}' — content unchanged, skipped re-embedding",
                "document_id": _existing_doc["document_id"],
                "chunks_stored": 0,
            }), 200
        _reuse_document_id = _existing_doc["document_id"] if _existing_doc else None
        if _existing_doc:
            try:
                _b = await retriever._get_knowledge_backend(collection_id)
                _cn = collection_name
                from trusted_data_agent.vectorstore.filters import FieldFilter as _FF, FilterOp as _FO
                _got = await _b.get(_cn, where=_FF("document_id", _FO.EQ, _reuse_document_id),
                                    include_documents=False, include_metadata=False)
                _stale = [d.id for d in (_got.documents if _got else [])]
                if _stale:
                    await _b.delete(_cn, _stale)
                _conn = get_collection_db()._get_connection()
                try:
                    _conn.execute("DELETE FROM knowledge_documents WHERE collection_id = ? AND document_id = ?",
                                  (collection_id, _reuse_document_id))
                    _conn.commit()
                finally:
                    _conn.close()
                get_collection_db().increment_counts(collection_id, document_delta=-1)
                app_logger.info(f"Refreshing '{file.filename}' in place (doc={_reuse_document_id}, {len(_stale)} old chunks cleared)")
            except Exception as _e:
                app_logger.warning(f"Could not clear the previous version of '{file.filename}': {_e}")

        try:
            # ── Server-side chunking path ──────────────────────────────────────
            # Pass the raw file to the backend SDK — skip text extraction &
            # local chunking entirely.  Follows the Chatbot PDF pattern:
            #   VectorStore.create(document_files=[...], chunk_size=500, ...)
            if chunking_strategy_str == "server_side":
                backend = await retriever._get_knowledge_backend(collection_id)

                if not backend or not backend.has_capability(VectorStoreCapability.SERVER_SIDE_CHUNKING):
                    return jsonify({
                        "status": "error",
                        "message": f"Selected backend does not support server-side chunking. "
                                   f"Use a client-side chunking strategy instead."
                    }), 400

                document_id = _reuse_document_id or str(uuid4())

                await backend.add_document_files(
                    collection_name=collection_name,
                    file_paths=[temp_file.name],
                    chunking_config=ServerSideChunkingConfig(
                        optimized_chunking=form.get("optimized_chunking", str(coll_optimized).lower()).lower() == "true",
                        chunk_size=int(form.get("chunk_size", coll_ss_chunk_size)),
                        header_height=int(form.get("header_height", coll_header_height)),
                        footer_height=int(form.get("footer_height", coll_footer_height)),
                    ),
                )

                app_logger.info(
                    f"Successfully uploaded document '{file.filename}' to Knowledge "
                    f"repository {collection_id} (server-side chunking)"
                )

                # Verify chunks were actually stored before writing DB record
                await _verify_chunks_indexed(backend, collection_name, file.filename, collection_id)

                # Store document metadata in knowledge_documents table
                from trusted_data_agent.core.collection_db import CollectionDatabase
                db = CollectionDatabase()
                conn = db._get_connection()
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO knowledge_documents
                    (collection_id, document_id, filename, document_type, title, author,
                     source, category, tags, file_size, content_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    collection_id,
                    document_id,
                    file.filename,
                    os.path.splitext(file.filename)[1].lstrip('.'),
                    title,
                    author,
                    'upload',
                    category,
                    ','.join(tags),
                    len(file_content),
                    content_hash,
                    datetime.now(timezone.utc).isoformat(),
                ))
                conn.commit()
                conn.close()

                # Persist counts — fetch total chunk count from backend
                try:
                    total_chunks = await backend.count(collection_name)
                except Exception:
                    total_chunks = None
                get_collection_db().increment_counts(collection_id, document_delta=1)
                if total_chunks is not None:
                    get_collection_db().update_counts(collection_id, chunk_count=total_chunks)

                return jsonify({
                    "status": "success",
                    "message": f"Document '{file.filename}' ingested via server-side chunking",
                    "chunks_stored": total_chunks or 0,
                    "metadata": {
                        "document_id": document_id,
                        "filename": file.filename,
                        "document_type": os.path.splitext(file.filename)[1].lstrip('.'),
                        "chunking_mode": "server_side",
                    }
                }), 200

            # ── Client-side chunking path (unchanged) ──────────────────────────
            # Validate chunking strategy enum
            try:
                chunking_strategy = ChunkingStrategy[chunking_strategy_str.upper()]
            except KeyError:
                return jsonify({
                    "status": "error",
                    "message": f"Invalid chunking_strategy: {chunking_strategy_str}"
                }), 400

            # Process document using DocumentUploadHandler
            doc_handler = DocumentUploadHandler()

            # For Knowledge repos, we always extract text
            prepared_doc = doc_handler.prepare_document_for_llm(
                file_path=temp_file.name,
                provider_name="Ollama",  # Force text extraction
                model_name="",
                effective_config={"enabled": True, "use_native_upload": False}
            )

            # Check if content was extracted
            if 'content' not in prepared_doc or not prepared_doc['content']:
                return jsonify({
                    "status": "error",
                    "message": "Failed to extract text from document"
                }), 400

            document_content = prepared_doc['content']

            # Create repository constructor (backend-abstracted path)
            backend = await retriever._get_knowledge_backend(collection_id)
            constructor = create_repository_constructor(
                repository_type=RepositoryType.KNOWLEDGE,
                chroma_client=retriever.client,
                storage_dir=retriever.rag_cases_dir / f"collection_{collection_id}",
                embedding_model=embedding_model,
                chunking_strategy=chunking_strategy,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                backend=backend,
            )

            # Construct repository entry
            result = await constructor.construct_async(
                collection_id=collection_id,
                content=document_content,
                collection_name=collection_name,
                filename=file.filename,
                document_type=os.path.splitext(file.filename)[1].lstrip('.'),
                title=title,
                author=author,
                category=category,
                tags=tags,
                source='upload',
                file_size=len(file_content),
                content_hash=content_hash,
                save_original=True,
                **({"document_id": _reuse_document_id} if _reuse_document_id else {}),
            )

            if result['status'] == 'success':
                app_logger.info(f"Successfully uploaded document '{file.filename}' to Knowledge repository {collection_id}")

                # Verify chunks were actually stored before writing DB record
                await _verify_chunks_indexed(backend, collection_name, file.filename, collection_id)

                # Store document metadata in database
                from trusted_data_agent.core.collection_db import CollectionDatabase
                db = CollectionDatabase()

                doc_data = {
                    'collection_id': collection_id,
                    'document_id': result['metadata']['document_id'],
                    'filename': file.filename,
                    'document_type': result['metadata']['document_type'],
                    'title': title,
                    'author': author,
                    'source': 'upload',
                    'category': category,
                    'tags': tags,
                    'file_size': len(file_content),
                    'content_hash': content_hash,
                    'created_at': datetime.now(timezone.utc).isoformat()
                }

                # Insert into knowledge_documents table
                conn = db._get_connection()
                cursor = conn.cursor()
                # chunk_count on the ROW: sync_collection_counts sums it, and a row
                # inserted without it made every resync report zero chunks.
                cursor.execute("""
                    INSERT INTO knowledge_documents
                    (collection_id, document_id, filename, document_type, title, author,
                     source, category, tags, file_size, content_hash, created_at, chunk_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    doc_data['collection_id'],
                    doc_data['document_id'],
                    doc_data['filename'],
                    doc_data['document_type'],
                    doc_data['title'],
                    doc_data['author'],
                    doc_data['source'],
                    doc_data['category'],
                    ','.join(doc_data['tags']),
                    doc_data['file_size'],
                    doc_data['content_hash'],
                    doc_data['created_at'],
                    int(result.get('chunks_stored') or (result.get('metadata') or {}).get('chunk_count') or 0),
                ))
                conn.commit()
                conn.close()

                # Persist counts
                chunks_stored = result.get('chunks_stored', 0)
                get_collection_db().increment_counts(
                    collection_id, document_delta=1, chunk_delta=chunks_stored)

                return jsonify(result), 200
            else:
                return jsonify(result), 400

        finally:
            # Cleanup temp file
            try:
                os.unlink(temp_file.name)
            except:
                pass
    
    except Exception as e:
        app_logger.error(f"Error uploading document to Knowledge repository: {e}", exc_info=True)

        # ATOMIC CREATION: Delete repository if this was the first upload and it failed
        # This prevents orphaned empty repositories from cluttering the UI
        try:
            from trusted_data_agent.core.collection_db import CollectionDatabase
            db = CollectionDatabase()
            conn = db._get_connection()
            cursor = conn.cursor()

            # Check if repository has any documents
            cursor.execute("""
                SELECT COUNT(*) as doc_count FROM knowledge_documents
                WHERE collection_id = ?
            """, (collection_id,))

            doc_count = cursor.fetchone()['doc_count']
            conn.close()

            if doc_count == 0:
                # No documents exist - this was the first upload attempt
                # Delete the orphaned repository
                app_logger.warning(f"First document upload failed for collection {collection_id}. Deleting orphaned repository...")

                # Delete collection from database
                db.delete_collection(collection_id)

                # Remove from APP_STATE
                from trusted_data_agent.core.config import APP_STATE
                APP_STATE["rag_collections"] = [
                    c for c in APP_STATE.get("rag_collections", [])
                    if c.get("id") != collection_id
                ]

                app_logger.info(f"Deleted orphaned repository {collection_id} (0 documents)")

                # Return more helpful error message
                return jsonify({
                    "status": "error",
                    "message": f"Upload failed and repository was deleted: {str(e)}",
                    "hint": "The repository had no documents and was automatically cleaned up. Please fix the issue and try creating a new repository."
                }), 500
        except Exception as cleanup_error:
            app_logger.error(f"Failed to cleanup orphaned repository: {cleanup_error}", exc_info=True)

        return jsonify({"status": "error", "message": str(e)}), 500


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/documents", methods=["GET"])
@operation("knowledge.documents", effect="read", scope="own",
           summary="The documents in one knowledge repository: name, type, size, chunks and when each was added",
           path={"collection_id": "the knowledge repository's id (rag.collections lists them)"},
           notes="An administrator may read any repository's documents; everyone else their own and subscribed ones.",
           summary_fields=("document_id", "filename", "chunk_count"))
@require_auth
async def list_knowledge_documents(current_user: dict, collection_id: int):
    """
    List all documents in a Knowledge repository.
    
    Returns document metadata (not full content).
    """
    try:
        # Query collection and documents directly from database to avoid APP_STATE sync issues
        from trusted_data_agent.core.collection_db import CollectionDatabase
        db = CollectionDatabase()
        conn = db._get_connection()
        cursor = conn.cursor()
        
        # Verify collection exists and is a Knowledge repository
        cursor.execute("""
            SELECT repository_type, owner_user_id FROM collections
            WHERE id = ?
        """, (collection_id,))
        
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return jsonify({"status": "error", "message": f"Collection {collection_id} not found"}), 404
        
        repository_type = result['repository_type']
        owner_user_id = result['owner_user_id']
        
        if repository_type != 'knowledge':
            conn.close()
            return jsonify({
                "status": "error",
                "message": f"Collection {collection_id} is not a Knowledge repository"
            }), 400
        
        user_id = current_user.id
        is_admin = getattr(current_user, 'tier', '') == 'admin'

        if owner_user_id != user_id and not is_admin:
            # Also allow subscribed users — collection is in their accessible set
            cursor.execute(
                "SELECT 1 FROM collection_subscriptions WHERE source_collection_id = ? AND user_id = ? LIMIT 1",
                (collection_id, user_id)
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"status": "error", "message": "Access denied"}), 403
        
        # Query documents from same connection
        cursor.execute("""
            SELECT * FROM knowledge_documents
            WHERE collection_id = ?
            ORDER BY created_at DESC
        """, (collection_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        documents = []
        for row in rows:
            doc = dict(row)
            # Parse tags
            if doc['tags']:
                doc['tags'] = [t.strip() for t in doc['tags'].split(',')]
            else:
                doc['tags'] = []
            documents.append(doc)
        
        return jsonify({
            "status": "success",
            "collection_id": collection_id,
            "documents": documents,
            "count": len(documents)
        }), 200
    
    except Exception as e:
        app_logger.error(f"Error listing Knowledge documents: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/documents/<document_id>", methods=["DELETE"])
@operation("knowledge.document_delete", effect="write", scope="own",
           summary="Remove a document from a knowledge repository — its chunks leave retrieval",
           path={"collection_id": "the knowledge repository (rag.collections lists them)", "document_id": "the document's id (knowledge.documents lists them)"},
           notes="Reversible only by adding the document again. Agents that answer from the repository "
                 "stop finding it at once; the repository's signature reads changed.")
@require_auth
async def delete_knowledge_document(current_user: dict, collection_id: int, document_id: str):
    """Delete a document from Knowledge repository."""
    try:
        retriever = get_rag_retriever()
        if not retriever:
            return jsonify({"status": "error", "message": "RAG retriever not initialized"}), 500
        
        # Query collection directly from database
        from trusted_data_agent.core.collection_db import CollectionDatabase
        db = CollectionDatabase()
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT repository_type, owner_user_id FROM collections
            WHERE id = ?
        """, (collection_id,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return jsonify({"status": "error", "message": f"Collection {collection_id} not found"}), 404
        
        if result['repository_type'] != 'knowledge':
            conn.close()
            return jsonify({"status": "error", "message": "Not a Knowledge repository"}), 400
        
        user_id = current_user.id
        if result['owner_user_id'] != user_id:
            conn.close()
            return jsonify({"status": "error", "message": "Access denied"}), 403
        
        # Delete all chunks for this document via backend
        chunk_ids = []
        backend = await retriever._get_knowledge_backend(collection_id)
        if backend:
            from trusted_data_agent.vectorstore import FieldFilter, FilterOp
            coll_meta = retriever.get_collection_metadata(collection_id)
            coll_name = coll_meta["collection_name"] if coll_meta else f"collection_{collection_id}"
            get_result = await backend.get(
                coll_name,
                where=FieldFilter("document_id", FilterOp.EQ, document_id),
                include_documents=False,
                include_metadata=False,
            )
            if get_result.documents:
                chunk_ids = [d.id for d in get_result.documents]
                await backend.delete(coll_name, chunk_ids)
                app_logger.info(f"Deleted {len(chunk_ids)} chunks via backend")
        
        # Delete from database (reuse connection)
        
        cursor.execute("""
            DELETE FROM knowledge_documents
            WHERE collection_id = ? AND document_id = ?
        """, (collection_id, document_id))
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        if deleted > 0:
            # Decrement persisted counts
            get_collection_db().increment_counts(
                collection_id, document_delta=-1, chunk_delta=-len(chunk_ids))

            return jsonify({
                "status": "success",
                "message": f"Document {document_id} deleted"
            }), 200
        else:
            return jsonify({
                "status": "error",
                "message": "Document not found"
            }), 404
    
    except Exception as e:
        app_logger.error(f"Error deleting Knowledge document: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/search", methods=["POST"])
@operation("knowledge.search", effect="read", scope="own",
           summary="Search one knowledge repository the way an agent retrieves from it: the passages a question finds and how close each is",
           path={"collection_id": "the knowledge repository (rag.collections lists them)"},
           body={"query": Arg("string", "the question or words to search for", required=True),
                 "k": Arg("integer", "how many passages (default 5)"),
                 "filter": Arg("object", "metadata to match, e.g. {\"category\": \"manual\"}")},
           notes="Each passage carries its text, its distance (lower is closer) and its document's "
                 "metadata (filename, title, document_id).",
           summary_fields=("chunk_id", "distance"))
@require_auth
async def search_knowledge_repository(current_user: dict, collection_id: int):
    """
    Search within a Knowledge repository.
    
    Body:
        {
            "query": "search text",
            "k": 5,  # number of results
            "filter": {"category": "manual"}  # optional metadata filter
        }
    """
    try:
        retriever = get_rag_retriever()
        if not retriever:
            return jsonify({"status": "error", "message": "RAG retriever not initialized"}), 500
        
        # Query collection directly from database
        from trusted_data_agent.core.collection_db import CollectionDatabase
        db = CollectionDatabase()
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT repository_type, owner_user_id FROM collections
            WHERE id = ?
        """, (collection_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return jsonify({"status": "error", "message": f"Collection {collection_id} not found"}), 404
        
        if result['repository_type'] != 'knowledge':
            return jsonify({"status": "error", "message": "Not a Knowledge repository"}), 400
        
        user_id = current_user.id
        if result['owner_user_id'] != user_id:
            return jsonify({"status": "error", "message": "Access denied"}), 403
        
        # Get request body
        data = await request.get_json()
        query = data.get('query', '')
        k = data.get('k', 5)
        metadata_filter = data.get('filter', {})
        
        if not query:
            return jsonify({"status": "error", "message": "query is required"}), 400
        
        # Search via backend
        backend = await retriever._get_knowledge_backend(collection_id)
        if not backend:
            return jsonify({"status": "error", "message": "Collection not loaded"}), 404

        coll_meta = retriever.get_collection_metadata(collection_id)
        coll_name = coll_meta["collection_name"] if coll_meta else f"collection_{collection_id}"

        where_filter = None
        if metadata_filter:
            from trusted_data_agent.vectorstore import from_chromadb_where
            where_filter = from_chromadb_where(metadata_filter)

        from trusted_data_agent.vectorstore import get_embedding_provider
        emb_model = coll_meta.get("embedding_model", "all-MiniLM-L6-v2") if coll_meta else "all-MiniLM-L6-v2"
        emb_provider = get_embedding_provider(emb_model)

        from trusted_data_agent.vectorstore.types import SearchMode
        _search_mode = SearchMode(coll_meta.get("search_mode", "semantic")) if coll_meta else SearchMode.SEMANTIC
        _kw_weight = float(coll_meta.get("hybrid_keyword_weight", 0.3)) if coll_meta else 0.3

        # For Teradata backends the factory caches one instance per connection.
        # BM25 / scoring params are stored in backend_config per-collection and
        # may change after creation (e.g. enable_bm25). Refresh _config from the
        # latest DB value so the query path sees current td_bm25_enabled etc.
        if coll_meta and coll_meta.get("backend_type") == "teradata":
            import json as _json
            _raw = coll_meta.get("backend_config") or "{}"
            _fresh = _json.loads(_raw) if isinstance(_raw, str) else _raw
            if hasattr(backend, "_config"):
                backend._config.update(_fresh)

        query_result = await backend.query(
            coll_name, query_text=query, n_results=k, where=where_filter,
            embedding_provider=emb_provider,
            search_mode=_search_mode,
            keyword_weight=_kw_weight,
        )

        # Format results
        search_results = []
        for doc, distance in query_result:
            search_results.append({
                'chunk_id': doc.id,
                'content': doc.content,
                'metadata': doc.metadata,
                'distance': distance,
            })
        
        return jsonify({
            "status": "success",
            "query": query,
            "results": search_results,
            "count": len(search_results)
        }), 200
    
    except Exception as e:
        app_logger.error(f"Error searching Knowledge repository: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@knowledge_api_bp.route("/v1/knowledge/collections/<int:collection_id>/chunks", methods=["GET"])
@require_auth
async def get_knowledge_chunks(current_user: dict, collection_id: int):
    """
    Get all chunks from a Knowledge repository with pagination and search.
    
    Query Parameters:
        - limit: Number of results per page (default: 50)
        - offset: Starting offset for pagination (default: 0)
        - q: Search query text (optional, min 3 characters)
        - light: If 'true', exclude full document text (default: true)
        - sort_by: Field to sort by (default: None)
        - sort_order: 'asc' or 'desc' (default: 'asc')
    
    Returns:
        {
            "chunks": [
                {
                    "id": "chunk_uuid",
                    "document_id": "doc_uuid",
                    "chunk_index": 0,
                    "content": "chunk text content",
                    "token_count": 150,
                    "metadata": {
                        "source_filename": "document.pdf",
                        "page": 1,
                        ...
                    }
                }
            ],
            "total": 1234,
            "collection_id": 5,
            "collection_name": "My Knowledge Base"
        }
    """
    try:
        retriever = get_rag_retriever()
        if not retriever:
            return jsonify({"error": "RAG retriever not initialized"}), 500
        
        # Verify collection exists and user has access
        from trusted_data_agent.core.collection_db import CollectionDatabase
        db = CollectionDatabase()
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name, repository_type, owner_user_id FROM collections
            WHERE id = ?
        """, (collection_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return jsonify({"error": f"Collection {collection_id} not found"}), 404
        
        collection_name = result['name']
        repository_type = result['repository_type']
        owner_user_id = result['owner_user_id']
        
        if repository_type != 'knowledge':
            return jsonify({"error": "Not a Knowledge repository"}), 400
        
        user_id = current_user.id
        is_admin = getattr(current_user, 'tier', '') == 'admin'
        if owner_user_id != user_id and not is_admin:
            db2 = CollectionDatabase()
            conn2 = db2._get_connection()
            row = conn2.execute(
                "SELECT 1 FROM collection_subscriptions WHERE source_collection_id = ? AND user_id = ? LIMIT 1",
                (collection_id, user_id)
            ).fetchone()
            conn2.close()
            if not row:
                return jsonify({"error": "Access denied"}), 403

        # Get pagination parameters
        limit = request.args.get('limit', default=50, type=int)
        offset = request.args.get('offset', default=0, type=int)
        query_text = request.args.get('q', default='', type=str)
        light = request.args.get('light', default='true', type=str).lower() == 'true'
        sort_by = request.args.get('sort_by', default=None, type=str)
        sort_order = request.args.get('sort_order', default='asc', type=str)
        suspicious_only = request.args.get('suspicious_only', default='false', type=str).lower() == 'true'
        # Screen filter — three populations, because the binary toggle hid a ~60x gap:
        # a real corpus showed 7,773 chunks / 235 flagged / 4 that actually block sign-off.
        #   all      — no filter
        #   flagged  — any detector matched (injection OR PII OR exfil-shaped URL)
        #   blocking — ONLY the findings that gate a signature, i.e. prompt-injection.
        #              Mirrors knowledge_validator ("PII findings never block signing"),
        #              so this filter and the sign-off refusal can never disagree.
        # `suspicious_only=true` stays accepted and means `flagged` (older callers).
        screen_filter = (request.args.get('screen_filter', default='', type=str) or '').lower()
        if screen_filter not in ('all', 'flagged', 'blocking'):
            screen_filter = 'flagged' if suspicious_only else 'all'
        filtering = screen_filter in ('flagged', 'blocking')
        suspicious_only = filtering   # the fetch paths below key off this

        def _keep(screen) -> bool:
            """Does this chunk belong in the requested population?"""
            if not screen:
                return False
            if screen_filter == 'blocking':
                return bool(screen.get('injection'))
            return True

        # Chunk-level screening (RAG Trust Hardening — chunk browser visibility):
        # deterministic read-time screen with the SAME detectors as the ingest firewall.
        # Master-gated like every screening surface; annotation only, never a mutation.
        try:
            from trusted_data_agent.core.config import APP_CONFIG
            screening_enabled = bool(getattr(APP_CONFIG, "GUARDRAILS_ENABLED", False))
        except Exception:
            screening_enabled = False
        if filtering and not screening_enabled:
            return jsonify({"error": "Corpus screening is gated by the guardrails master switch (currently off)."}), 400
        from trusted_data_agent.agent.guardrails.corpus_screen import screen_if_flagged
        scan_capped = False

        # Get backend for this knowledge collection
        backend = await retriever._get_knowledge_backend(collection_id)
        if not backend:
            return jsonify({"error": "Collection not loaded"}), 404

        coll_meta_full = retriever.get_collection_metadata(collection_id)
        coll_name_full = coll_meta_full["collection_name"] if coll_meta_full else f"collection_{collection_id}"

        chunks = []
        total = 0

        # If search query provided (min 3 chars), do semantic search
        if query_text and len(query_text) >= 3:
            try:
                from trusted_data_agent.vectorstore import get_embedding_provider
                emb_model = coll_meta_full.get("embedding_model", "all-MiniLM-L6-v2") if coll_meta_full else "all-MiniLM-L6-v2"
                emb_provider = get_embedding_provider(emb_model)

                from trusted_data_agent.vectorstore.types import SearchMode
                _sm = SearchMode(coll_meta_full.get("search_mode", "semantic")) if coll_meta_full else SearchMode.SEMANTIC
                _kw = float(coll_meta_full.get("hybrid_keyword_weight", 0.3)) if coll_meta_full else 0.3

                query_result = await backend.query(
                    coll_name_full,
                    query_text=query_text,
                    n_results=min(limit, 100),
                    embedding_provider=emb_provider,
                    search_mode=_sm,
                    keyword_weight=_kw,
                )
                for doc, distance in query_result:
                    metadata = doc.metadata or {}
                    content = doc.content
                    screen = screen_if_flagged(content) if screening_enabled else None
                    if filtering and not _keep(screen):
                        continue
                    entry = {
                        "id": doc.id,
                        "document_id": metadata.get("document_id", ""),
                        "chunk_index": metadata.get("chunk_index", 0),
                        "content": content if not light else content[:200] + "..." if len(content) > 200 else content,
                        "token_count": metadata.get("token_count", 0),
                        "metadata": metadata,
                        "similarity_score": 1.0 - distance,
                    }
                    if screen:
                        entry["screen"] = screen
                    chunks.append(entry)
                total = len(chunks) if suspicious_only else len(query_result.documents)
            except Exception as qe:
                app_logger.warning(f"Query failed for knowledge collection '{collection_name}': {qe}")
        else:
            # Get chunks with pagination
            try:
                # Metadata-field sorts require fetching all rows (can't sort JSON in SQL)
                needs_full_fetch = sort_by and sort_by in ['document_id', 'chunk_index', 'token_count']

                if suspicious_only:
                    # Suspicious-only filter: screen the whole corpus (bounded), keep
                    # flagged chunks, then paginate the flagged set. Deterministic +
                    # 0-token — the regex screen is cheaper than the fetch itself.
                    SCAN_CAP = 20000
                    get_result = await backend.get(coll_name_full)
                    flagged = []
                    for i, doc in enumerate(get_result.documents):
                        if i >= SCAN_CAP:
                            scan_capped = True
                            break
                        metadata = doc.metadata or {}
                        content = doc.content
                        screen = screen_if_flagged(content)
                        if not _keep(screen):
                            continue
                        flagged.append({
                            "id": doc.id,
                            "document_id": metadata.get("document_id", ""),
                            "chunk_index": metadata.get("chunk_index", 0),
                            "content": content if not light else content[:200] + "..." if len(content) > 200 else content,
                            "token_count": metadata.get("token_count", 0),
                            "metadata": metadata,
                            "screen": screen,
                        })
                    flagged.sort(key=lambda c: (
                        str((c.get("metadata") or {}).get("source_filename") or c.get("document_id") or ""),
                        c.get("chunk_index") or 0))
                    total = len(flagged)
                    chunks = flagged[offset:offset + limit]
                elif needs_full_fetch:
                    # Full fetch + in-memory sort + slice
                    get_result = await backend.get(coll_name_full)
                    total = get_result.total_count

                    all_chunks = []
                    for doc in get_result.documents:
                        metadata = doc.metadata or {}
                        content = doc.content
                        entry = {
                            "id": doc.id,
                            "document_id": metadata.get("document_id", ""),
                            "chunk_index": metadata.get("chunk_index", 0),
                            "content": content if not light else content[:200] + "..." if len(content) > 200 else content,
                            "token_count": metadata.get("token_count", 0),
                            "metadata": metadata,
                        }
                        if screening_enabled:
                            screen = screen_if_flagged(content)
                            if screen:
                                entry["screen"] = screen
                        all_chunks.append(entry)

                    reverse = sort_order.lower() == 'desc'
                    try:
                        all_chunks.sort(key=lambda x: (x.get(sort_by) is None, x.get(sort_by)), reverse=reverse)
                    except Exception as e:
                        app_logger.debug(f"Sorting failed: {e}")

                    chunks = all_chunks[offset:offset + limit]
                else:
                    # Server-side pagination — only fetch the requested page
                    total = await backend.count(coll_name_full)
                    get_result = await backend.get(
                        coll_name_full,
                        offset=offset,
                        limit=limit,
                        include_documents=True,
                        include_metadata=True,
                    )

                    for doc in get_result.documents:
                        metadata = doc.metadata or {}
                        content = doc.content
                        entry = {
                            "id": doc.id,
                            "document_id": metadata.get("document_id", ""),
                            "chunk_index": metadata.get("chunk_index", 0),
                            "content": content if not light else content[:200] + "..." if len(content) > 200 else content,
                            "token_count": metadata.get("token_count", 0),
                            "metadata": metadata,
                        }
                        if screening_enabled:
                            screen = screen_if_flagged(content)
                            if screen:
                                entry["screen"] = screen
                        chunks.append(entry)

            except Exception as ge:
                app_logger.error(f"Failed to get chunks for collection '{collection_name}': {ge}", exc_info=True)
        
        return jsonify({
            "chunks": chunks,
            "total": total,
            "query": query_text,
            "collection_id": collection_id,
            "collection_name": collection_name,
            "screening_enabled": screening_enabled,
            "suspicious_only": suspicious_only,
            "screen_filter": screen_filter,
            "scan_capped": scan_capped,
        }), 200

    except Exception as e:
        app_logger.error(f"Error getting knowledge chunks for collection ID {collection_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to get knowledge chunks"}), 500


@knowledge_api_bp.route("/v1/knowledge/collections/<int:collection_id>/chunks/<chunk_id>", methods=["GET"])
@require_auth
async def get_single_chunk(current_user: dict, collection_id: int, chunk_id: str):
    """
    Get a single chunk by ID with full content.
    
    Returns:
        {
            "chunk": {
                "id": "chunk_uuid",
                "document_id": "doc_uuid",
                "chunk_index": 0,
                "content": "full chunk text content",
                "token_count": 150,
                "metadata": {...}
            }
        }
    """
    try:
        retriever = get_rag_retriever()
        if not retriever:
            return jsonify({"error": "RAG retriever not initialized"}), 500
        
        # Verify collection exists and user has access
        from trusted_data_agent.core.collection_db import CollectionDatabase
        db = CollectionDatabase()
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name, repository_type, owner_user_id FROM collections
            WHERE id = ?
        """, (collection_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result:
            return jsonify({"error": f"Collection {collection_id} not found"}), 404
        
        repository_type = result['repository_type']
        owner_user_id = result['owner_user_id']
        
        if repository_type != 'knowledge':
            return jsonify({"error": "Not a Knowledge repository"}), 400
        
        user_id = current_user.id
        is_admin = getattr(current_user, 'tier', '') == 'admin'
        if owner_user_id != user_id and not is_admin:
            db2 = CollectionDatabase()
            conn2 = db2._get_connection()
            row = conn2.execute(
                "SELECT 1 FROM collection_subscriptions WHERE source_collection_id = ? AND user_id = ? LIMIT 1",
                (collection_id, user_id)
            ).fetchone()
            conn2.close()
            if not row:
                return jsonify({"error": "Access denied"}), 403

        # Get chunk via backend
        backend = await retriever._get_knowledge_backend(collection_id)
        if not backend:
            return jsonify({"error": "Collection not loaded"}), 404

        coll_meta_chunk = retriever.get_collection_metadata(collection_id)
        coll_name_chunk = coll_meta_chunk["collection_name"] if coll_meta_chunk else f"collection_{collection_id}"

        try:
            get_result = await backend.get(coll_name_chunk, ids=[chunk_id])

            if not get_result.documents:
                return jsonify({"error": "Chunk not found"}), 404

            doc = get_result.documents[0]
            metadata = doc.metadata or {}
            chunk = {
                "id": doc.id,
                "document_id": metadata.get("document_id", ""),
                "chunk_index": metadata.get("chunk_index", 0),
                "content": doc.content,
                "token_count": metadata.get("token_count", 0),
                "metadata": metadata,
            }
            # Deterministic read-time screen (same detectors as the ingest firewall)
            # so the detail window can mark the chunk with its problem tags.
            try:
                from trusted_data_agent.core.config import APP_CONFIG
                if bool(getattr(APP_CONFIG, "GUARDRAILS_ENABLED", False)):
                    from trusted_data_agent.agent.guardrails.corpus_screen import screen_if_flagged
                    screen = screen_if_flagged(doc.content)
                    if screen:
                        chunk["screen"] = screen
            except Exception:
                pass  # annotation is best-effort — never fail the chunk read

            return jsonify({"chunk": chunk}), 200

        except Exception as e:
            app_logger.error(f"Failed to get chunk {chunk_id}: {e}", exc_info=True)
            return jsonify({"error": "Failed to get chunk"}), 500
        
    except Exception as e:
        app_logger.error(f"Error getting chunk {chunk_id} from collection {collection_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to get chunk"}), 500


@knowledge_api_bp.route("/v1/knowledge/test-connection", methods=["POST"])
@require_auth
async def test_knowledge_backend_connection(current_user: dict):
    """Validate Teradata connection credentials without creating a collection.

    Uses VSManager.health() from the teradatagenai SDK to verify connectivity.
    """
    data = await request.get_json()
    backend_type = data.get("backend_type", "chromadb")
    backend_config = data.get("backend_config", {})

    if backend_type == "chromadb":
        return jsonify({"status": "success", "message": "ChromaDB is local — no connection needed"}), 200

    if backend_type != "teradata":
        return jsonify({"status": "error", "message": f"Unknown backend: {backend_type}"}), 400

    # Validate required fields
    has_user_pass = backend_config.get("username") and backend_config.get("password")
    has_pat = backend_config.get("pat_token")
    if not (has_user_pass or has_pat):
        return jsonify({
            "status": "error",
            "message": "Provide either (username + password) or pat_token"
        }), 400
    if not backend_config.get("host"):
        return jsonify({"status": "error", "message": "host is required"}), 400

    try:
        from trusted_data_agent.vectorstore.teradata_backend import TeradataVectorBackend
    except ImportError:
        return jsonify({
            "status": "error",
            "message": "Teradata SDK not installed. Run: pip install teradatagenai teradataml"
        }), 500

    # Use a temporary backend instance with its own dedicated thread so
    # the test-connection does not overwrite the global teradataml context
    # used by active backends (which would cause connection-lost cascades).
    test_backend = TeradataVectorBackend(backend_config)
    try:
        await test_backend.initialize()

        # Health check via the backend's own dedicated thread
        from teradatagenai import VSManager  # type: ignore[import]
        health = await test_backend._run_in_td_thread(VSManager.health)

        return jsonify({
            "status": "success",
            "message": "Connection successful",
            "server_info": {
                "host": backend_config["host"],
                "database": backend_config.get("database"),
                "vs_health": str(health),
            }
        }), 200

    except Exception as e:
        app_logger.warning(f"Teradata test-connection failed: {e}")
        return jsonify({"status": "error", "message": f"Connection failed: {e}"}), 400
    finally:
        await test_backend.shutdown()


# ── Teradata BM25 / hybrid search management ─────────────────────────────────

async def _get_teradata_collection(collection_id: int, current_user: dict):
    """Load and auth-check a Teradata knowledge collection.

    Returns ``(coll_row, backend_config_dict)`` or raises ``ValueError`` with
    an HTTP-friendly message if the collection is not found, not Teradata, or
    not owned by the caller.
    """
    db = get_collection_db()
    coll = db.get_collection_by_id(collection_id)
    if not coll:
        raise ValueError(f"Collection {collection_id} not found")

    if coll.get("owner_user_id") != current_user.id:
        raise PermissionError("Access denied")

    if coll.get("backend_type") != "teradata":
        raise ValueError("Native BM25 is only available for Teradata collections")

    backend_config_raw = coll.get("backend_config") or "{}"
    if isinstance(backend_config_raw, str):
        try:
            backend_config = json.loads(backend_config_raw)
        except json.JSONDecodeError:
            backend_config = {}
    else:
        backend_config = backend_config_raw

    return coll, backend_config


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/hybrid/enable", methods=["POST"])
@require_auth
async def enable_teradata_bm25(current_user: dict, collection_id: int):
    """Trigger server-side BM25 model build on a Teradata collection.

    Body (all fields optional):
        {
            "scoring_method": "rrf",          # "rrf" | "weighted_sum" | "weighted_rrf"
            "sparse_weight":  0.3             # float 0.0–1.0
        }

    On success, persists ``td_bm25_enabled=true``, ``td_scoring_method``, and
    ``td_sparse_weight`` into ``backend_config`` in the database.
    """
    try:
        coll, backend_config = await _get_teradata_collection(collection_id, current_user)
    except PermissionError as e:
        return jsonify({"status": "error", "message": str(e)}), 403
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    data = await request.get_json() or {}
    scoring_method = data.get("scoring_method", backend_config.get("td_scoring_method", "rrf"))
    sparse_weight = float(data.get("sparse_weight", backend_config.get("td_sparse_weight", 0.3)))

    collection_name = coll["collection_name"]

    try:
        from trusted_data_agent.vectorstore.factory import get_backend_for_collection
        backend = await get_backend_for_collection(coll, user_uuid=coll.get("owner_user_id"))

        app_logger.info(
            f"[BM25] Building BM25 model for collection '{collection_name}' "
            f"(scoring={scoring_method}, sparse_weight={sparse_weight})"
        )
        await backend.enable_bm25(
            collection_name,
            scoring_method=scoring_method,
            sparse_weight=sparse_weight,
        )

        # Persist BM25 state into backend_config
        backend_config["td_bm25_enabled"] = True
        backend_config["td_scoring_method"] = scoring_method
        backend_config["td_sparse_weight"] = sparse_weight

        db = get_collection_db()
        db.update_collection(collection_id, {"backend_config": json.dumps(backend_config)})

        app_logger.info(f"[BM25] BM25 enabled for collection '{collection_name}'")
        return jsonify({
            "status": "enabled",
            "collection_id": collection_id,
            "scoring_method": scoring_method,
            "sparse_weight": sparse_weight,
        }), 200

    except AttributeError:
        return jsonify({
            "status": "error",
            "message": "enable_bm25() is not supported by this backend",
        }), 400
    except Exception as e:
        app_logger.error(f"[BM25] enable_bm25 failed for collection {collection_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/hybrid/status", methods=["GET"])
@require_auth
async def get_teradata_bm25_status(current_user: dict, collection_id: int):
    """Return the current BM25 / hybrid-search configuration for a collection.

    Response:
        {
            "bm25_enabled":    false,
            "scoring_method":  "rrf",
            "sparse_weight":   0.3
        }
    """
    try:
        _coll, backend_config = await _get_teradata_collection(collection_id, current_user)
    except PermissionError as e:
        return jsonify({"status": "error", "message": str(e)}), 403
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    return jsonify({
        "bm25_enabled": bool(backend_config.get("td_bm25_enabled", False)),
        "scoring_method": backend_config.get("td_scoring_method", "rrf"),
        "sparse_weight": float(backend_config.get("td_sparse_weight", 0.3)),
    }), 200


# ── CDC endpoints ─────────────────────────────────────────────────────────────

@knowledge_api_bp.route(
    "/v1/knowledge/repositories/<int:collection_id>/documents/<document_id>",
    methods=["PATCH"]
)
@operation("knowledge.document_source", effect="human_only", scope="own",
           summary="Set where a document syncs from (a URL or path the server reads) — the owner's to choose: an agent could point the server at anything it can reach",
           path={"collection_id": "the knowledge repository (rag.collections lists them)", "document_id": "the document's id"},
           body={"source_uri": "the URL or path to sync from", "sync_enabled": Arg("boolean", "keep it in step with its source")})
@require_auth
async def patch_knowledge_document(current_user: dict, collection_id: int, document_id: str):
    """Update CDC sync configuration for a document.

    Body (JSON):
        source_uri    — URI to sync from (null to clear)
        sync_enabled  — true/false
        sync_interval — 'hourly', '6h', 'daily', 'weekly' (optional, updates collection)
    """
    try:
        db = get_collection_db()
        collection = db.get_collection_by_id(collection_id)
        if not collection:
            return jsonify({"error": "Collection not found"}), 404
        if collection["owner_user_id"] != current_user.id:
            return jsonify({"error": "Access denied"}), 403

        body = await request.get_json(silent=True) or {}
        source_uri = body.get("source_uri")
        sync_enabled = 1 if body.get("sync_enabled") else 0
        sync_interval = body.get("sync_interval")

        # Disable sync if URI is cleared
        if not source_uri:
            sync_enabled = 0

        db.update_document_sync_config(
            document_id=document_id,
            source_uri=source_uri,
            sync_enabled=sync_enabled,
            sync_interval=sync_interval,
        )
        return jsonify({"status": "ok", "document_id": document_id}), 200

    except Exception as e:
        app_logger.error(f"PATCH document sync config failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@knowledge_api_bp.route(
    "/v1/knowledge/repositories/<int:collection_id>/sync",
    methods=["POST"]
)
@operation("knowledge.sync", effect="write", scope="own",
           summary="Sync a knowledge repository now: every document kept in step with its source is re-read, changed ones re-ingested",
           path={"collection_id": "the knowledge repository (rag.collections lists them)"},
           notes="Reads only the sources the owner set. A sync that changes content changes the repository's signature reading unless the owner's standing policy renews it.")
@require_auth
async def trigger_knowledge_sync(current_user: dict, collection_id: int):
    """Manually trigger a CDC sync run for all sync-enabled documents in a collection.

    Returns:
        {checked, updated, unchanged, errors, duration_seconds}
    """
    try:
        db = get_collection_db()
        collection = db.get_collection_by_id(collection_id)
        if not collection:
            return jsonify({"error": "Collection not found"}), 404
        if collection["owner_user_id"] != current_user.id:
            return jsonify({"error": "Access denied"}), 403

        from trusted_data_agent.core.knowledge_sync import sync_knowledge_collection
        # Manual trigger always force-checks all documents (older_than_seconds=0).
        # The 1-hour throttle only applies to scheduled background runs.
        result = await sync_knowledge_collection(collection_id, current_user.id, older_than_seconds=0, advance_schedule=False, trigger="manual")
        return jsonify(result), 200

    except Exception as e:
        app_logger.error(f"Knowledge sync trigger failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@knowledge_api_bp.route(
    "/v1/knowledge/repositories/<int:collection_id>/reindex",
    methods=["POST"]
)
@require_auth
async def reindex_knowledge_collection(current_user: dict, collection_id: int):
    """Re-embed all documents in a collection using the current embedding model.

    Chooses strategy automatically:
        in_place   — collection has marketplace listing or active subscribers
        shadow_swap — private unsubscribed collections (same ingest path,
                      strategy name preserved for future isolation enhancement)

    Body (optional JSON):
        { "strategy": "in_place" | "shadow_swap" }   (overrides auto-select)

    Returns:
        { strategy, reindexed, skipped, errors, duration_seconds }
    """
    try:
        db = get_collection_db()
        collection = db.get_collection_by_id(collection_id)
        if not collection:
            return jsonify({"error": "Collection not found"}), 404
        if collection["owner_user_id"] != current_user.id:
            return jsonify({"error": "Access denied"}), 403

        # Strategy decision (auto unless caller overrides)
        body = {}
        try:
            body = await request.get_json(silent=True) or {}
        except Exception:
            pass

        sub_count = db.get_subscription_count(collection_id)
        is_marketplace = bool(collection.get("is_marketplace_listed"))
        auto_strategy = "in_place" if (sub_count > 0 or is_marketplace) else "shadow_swap"
        strategy = body.get("strategy") or auto_strategy

        # Unlock embedding model so re-index proceeds unguarded, then re-lock after
        db.update_collection(collection_id, {"embedding_model_locked": 0})

        app_logger.info(
            f"[REINDEX] collection={collection_id} strategy={strategy} "
            f"subscribers={sub_count} marketplace={is_marketplace}"
        )

        from trusted_data_agent.core.knowledge_sync import reindex_knowledge_collection as _do_reindex
        result = await _do_reindex(collection_id, current_user.id, strategy=strategy)
        return jsonify(result), 200

    except ValueError as ve:
        return jsonify({"error": str(ve)}), 404
    except Exception as e:
        app_logger.error(f"Reindex request failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@knowledge_api_bp.route(
    "/v1/knowledge/repositories/<int:collection_id>",
    methods=["PATCH"]
)
@operation("knowledge.repository_settings", effect="write", scope="own",
           summary="Change a knowledge repository's settings: its description, how often it syncs, its sensitivity label, whether its embedding model is locked",
           path={"collection_id": "the knowledge repository (rag.collections lists them)"},
           body={"description": "what the repository holds",
                 "sync_interval": Arg("string", "how often its documents sync from their source", enum=("hourly", "6h", "daily", "weekly")),
                 "sensitivity": Arg("string", "its data label", enum=("public", "internal", "confidential")),
                 "embedding_model_locked": Arg("boolean", "keep its embedding model fixed")},
           notes="Where the repository syncs from and its standing trust policy (acknowledging findings, "
                 "renewing signatures after a sync) are the owner's to set — not offered here.")
@require_auth
async def patch_knowledge_collection(current_user: dict, collection_id: int):
    """Update mutable collection settings.

    Accepted fields:
        embedding_model_locked  — 0 or 1
        sync_interval           — 'hourly', '6h', 'daily', 'weekly'
        description             — free-text description
        sync_auto_ack_findings  — 0/1: after a sync that changed content, acknowledge new
                                  injection findings on the changed documents automatically
        sync_auto_resign        — 0/1: after a sync that changed content, renew the
                                  repository's signature, the signatures of the agents
                                  that bind it and the seal of each pack that ships them
                                  (renewal only — never a first signature), then carry
                                  the renewal to the packs' subscribers (the publisher's
                                  own fan-out reinstates it onto their copies)

    Returns:
        { "status": "ok", "updated": {...} }
    """
    try:
        db = get_collection_db()
        collection = db.get_collection_by_id(collection_id)
        if not collection:
            return jsonify({"error": "Collection not found"}), 404
        if collection["owner_user_id"] != current_user.id:
            return jsonify({"error": "Access denied"}), 403

        body = await request.get_json(silent=True) or {}
        ALLOWED = {"embedding_model_locked", "sync_interval", "description", "source_root",
                   "next_sync_at", "sensitivity",
                   # the owner's standing policy after a sync that changed content
                   # (core/trust/renewal.py): acknowledge new findings · renew signatures
                   "sync_auto_ack_findings", "sync_auto_resign"}
        if "sensitivity" in body and body.get("sensitivity") not in (None, "public", "internal", "confidential"):
            return jsonify({"error": "sensitivity must be public | internal | confidential | null"}), 400
        updates = {k: v for k, v in body.items() if k in ALLOWED}
        for flag in ("sync_auto_ack_findings", "sync_auto_resign"):
            if flag in updates:
                updates[flag] = 1 if updates[flag] in (True, 1, "1", "true", "on") else 0

        if not updates:
            return jsonify({"error": "No updatable fields provided", "allowed": sorted(ALLOWED)}), 400

        db.update_collection(collection_id, updates)
        return jsonify({"status": "ok", "updated": updates}), 200

    except Exception as e:
        app_logger.error(f"PATCH collection failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════════
# RAG Trust Hardening Wave 2 — corpus hygiene: quarantine + scan manifest
# ═════════════════════════════════════════════════════════════════════════════

def _owned_document(document_id: str, user_id: str):
    """(doc, error_response) — resolve a knowledge document + verify the caller owns
    its collection. Ownership is the collection's owner_user_id."""
    from trusted_data_agent.core.collection_db import get_collection_db
    db = get_collection_db()
    doc = db.get_document_by_id(document_id)
    if not doc:
        return None, (jsonify({"error": "document not found"}), 404)
    coll = db.get_collection_by_id(int(doc["collection_id"]))
    owner = (coll or {}).get("owner_user_id") or (coll or {}).get("user_id")
    if owner and owner != user_id:
        return None, (jsonify({"error": "Access denied"}), 403)
    return doc, None


@knowledge_api_bp.route("/v1/knowledge/documents/<document_id>/quarantine", methods=["POST"])
@operation("knowledge.document_quarantine", effect="write", scope="own",
           summary="Quarantine a document: it stays listed in its repository but no agent retrieves it",
           path={"document_id": "the document's id (knowledge.documents lists them)"},
           body={"reason": "why — recorded with the act"},
           notes="Reversible, but only by the owner (knowledge.document_release). The repository's signature reads changed until signed again.")
@require_auth
async def quarantine_knowledge_document(current_user, document_id):
    """Q1/Q2 — quarantine a document: it stays fully visible in the repository manager
    (badge + reason) but is EXCLUDED from retrieval at the shared retriever seam.
    Reversible (see /unquarantine); the act is recorded as a trust event and flips the
    corpus signature 🟠 until re-signed (the ratchet working, not a bug — Q3).
    Body: {"reason": "..."} (optional)."""
    from trusted_data_agent.core.collection_db import get_collection_db
    from trusted_data_agent.core import trust_events
    user_id = current_user.id
    doc, err = _owned_document(document_id, user_id)
    if err:
        return err
    body = await request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip() or "owner action"
    if not get_collection_db().set_document_quarantine(document_id, True, reason):
        return jsonify({"error": "quarantine failed"}), 500
    summary = (f"Document '{doc.get('filename')}' quarantined from retrieval "
               f"(collection {doc.get('collection_id')}): {reason}")
    trust_events.record_event(user_id, "corpus_quarantine", summary, severity="info",
                              payload={"document_id": document_id,
                                       "collection_id": doc.get("collection_id"),
                                       "reason": reason})
    trust_events.broadcast_notice(user_id, "corpus_quarantine", summary, severity="info")
    return jsonify({"status": "quarantined", "document_id": document_id,
                    "note": "excluded from retrieval; visible in the repository manager; "
                            "the corpus signature reads changed until re-signed"}), 200


@knowledge_api_bp.route("/v1/knowledge/documents/<document_id>/unquarantine", methods=["POST"])
@operation("knowledge.document_release", effect="human_only", scope="own",
           summary="Release a quarantined document back into retrieval — the owner's judgement, never an agent's",
           path={"document_id": "the document's id"})
@require_auth
async def unquarantine_knowledge_document(current_user, document_id):
    """Reverse a quarantine — the document re-enters retrieval. Audited."""
    from trusted_data_agent.core.collection_db import get_collection_db
    from trusted_data_agent.core import trust_events
    user_id = current_user.id
    doc, err = _owned_document(document_id, user_id)
    if err:
        return err
    if not get_collection_db().set_document_quarantine(document_id, False):
        return jsonify({"error": "unquarantine failed"}), 500
    summary = f"Document '{doc.get('filename')}' released from quarantine (re-enters retrieval)"
    trust_events.record_event(user_id, "corpus_quarantine", summary, severity="info",
                              payload={"document_id": document_id, "released": True,
                                       "collection_id": doc.get("collection_id")})
    trust_events.broadcast_notice(user_id, "corpus_quarantine", summary, severity="info")
    return jsonify({"status": "released", "document_id": document_id}), 200


@knowledge_api_bp.route("/v1/knowledge/documents/<document_id>/acknowledge-finding", methods=["POST"])
@operation("knowledge.document_acknowledge_finding", effect="human_only", scope="own",
           summary="Acknowledge a security finding on a document with a reason — a recorded human judgement that enters the repository's signature",
           path={"document_id": "the document's id"}, body={"reason": "why the finding is acceptable"})
@require_auth
async def acknowledge_document_finding(current_user, document_id):
    """G2 — the owner explicitly acknowledges a critical (injection) finding with a
    REQUIRED reason (e.g. a security corpus legitimately stores injection examples).
    The acknowledgment freezes into the scan record — and therefore into the earned
    signature. Body: {"reason": "..."} (required)."""
    from trusted_data_agent.core.collection_db import get_collection_db
    from trusted_data_agent.core import trust_events
    user_id = current_user.id
    doc, err = _owned_document(document_id, user_id)
    if err:
        return err
    body = await request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "a reason is required — the acknowledgment is a "
                                 "recorded human judgement"}), 400
    if not get_collection_db().acknowledge_document_finding(document_id, reason):
        return jsonify({"error": "no scan record to acknowledge on this document"}), 409
    summary = f"Finding on '{doc.get('filename')}' acknowledged by the owner: {reason}"
    trust_events.record_event(
        user_id, "corpus_finding_ack", summary,
        severity="info", payload={"document_id": document_id, "reason": reason,
                                  "collection_id": doc.get("collection_id")})
    # Live half of the standing rule (record + broadcast) — the quarantine/unquarantine
    # siblings above already do this; without it, resolving a finding never reaches an
    # open Trust Circle and the ring keeps reporting the pre-resolution reading.
    trust_events.broadcast_notice(user_id, "corpus_finding_ack", summary, severity="info")
    return jsonify({"status": "acknowledged", "document_id": document_id}), 200


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/scan-manifest", methods=["GET"])
@operation("knowledge.health", effect="read", scope="own",
           summary="A knowledge repository's health: how many documents are screened, the injection and exfiltration findings, the personal data found (labels and counts), the quarantined documents",
           path={"collection_id": "the knowledge repository (rag.collections lists them)"})
@require_auth
async def get_collection_scan_manifest(current_user, collection_id):
    """U6 — the Corpus Health rollup: scan coverage (unscreened counted honestly),
    findings by category (labels + counts only — never PII values), the PII manifest,
    quarantine state, and the scan_report_hash the earned signature binds."""
    from trusted_data_agent.core.collection_db import get_collection_db
    db = get_collection_db()
    coll = db.get_collection_by_id(collection_id)
    if not coll:
        return jsonify({"error": "collection not found"}), 404
    owner = coll.get("owner_user_id") or coll.get("user_id")
    if owner and owner != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    return jsonify(db.get_scan_manifest(collection_id)), 200


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/retrieval-feedback",
                        methods=["GET"])
@operation("knowledge.feedback", effect="read", scope="own",
           summary="How the caller rated the answers each document of a repository served — the lowest-rated documents first",
           path={"collection_id": "the knowledge repository (rag.collections lists them)"})
@require_auth
async def get_collection_retrieval_feedback(current_user, collection_id):
    """The users' own ratings of the answers each document served — a CURATION
    signal for Corpus Health (docs/roadmap/VOTE_SIGNAL_PLAN.md §9).

    Scoped to the caller: a vote is that user's preference and is never readable as
    another's. Records only — nothing here changes retrieval or scoring; the lowest-
    rated documents are surfaced for a human to look at, never auto-quarantined.
    """
    from trusted_data_agent.core.collection_db import get_collection_db
    from trusted_data_agent.core import retrieval_feedback
    db = get_collection_db()
    coll = db.get_collection_by_id(collection_id)
    if not coll:
        return jsonify({"error": "collection not found"}), 404
    owner = coll.get("owner_user_id") or coll.get("user_id")
    if owner and owner != current_user.id:
        return jsonify({"error": "Access denied"}), 403

    summary = retrieval_feedback.doc_summary(current_user.id, collection_id)
    lowest = retrieval_feedback.lowest_rated(current_user.id, collection_id, limit=5)
    # Resolve display names best-effort; a document deleted since the vote keeps its
    # id rather than vanishing from the reading.
    names = {}
    try:
        for d in db.get_all_documents_in_collection(collection_id) or []:
            did = str(d.get("document_id") or d.get("id") or "")
            if did:
                names[did] = d.get("filename") or d.get("source_uri") or did
    except Exception:  # noqa: BLE001 — names are a nicety, the counts are the fact
        pass
    for row in lowest:
        row["name"] = names.get(row["document_id"], row["document_id"])
    return jsonify({
        "collection_id": collection_id,
        "rated_documents": len(summary),
        "lowest_rated": lowest,
    }), 200


@knowledge_api_bp.route("/v1/knowledge/repositories/<int:collection_id>/scan", methods=["POST"])
@operation("knowledge.scan", effect="write", scope="own",
           summary="Screen a knowledge repository's documents for injection, exfiltration and personal data — the unscreened ones, or all again",
           path={"collection_id": "the knowledge repository (rag.collections lists them)"},
           body={"rescan": Arg("boolean", "screen every document again, not only the unscreened")},
           notes="Records findings; changes no document. Needs the deployment's guardrails switched on.")
@require_auth
async def backfill_collection_scan(current_user, collection_id):
    """I3 backfill — screen the stored chunks of every unscanned (or all, with
    {"rescan": true}) document in this collection. Bounded + resumable (each doc's
    scan persists independently); requires GUARDRAILS_ENABLED. Chunk text is fetched
    from the vector store via the shared retriever backends."""
    from trusted_data_agent.core.collection_db import get_collection_db
    from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
    from trusted_data_agent.agent.guardrails import corpus_screen
    if not getattr(APP_CONFIG, "GUARDRAILS_ENABLED", False):
        return jsonify({"error": "guardrails master switch is off — enable it to scan"}), 409
    db = get_collection_db()
    coll = db.get_collection_by_id(collection_id)
    if not coll:
        return jsonify({"error": "collection not found"}), 404
    owner = coll.get("owner_user_id") or coll.get("user_id")
    if owner and owner != current_user.id:
        return jsonify({"error": "Access denied"}), 403
    body = await request.get_json(silent=True) or {}
    rescan = bool(body.get("rescan"))
    docs = db.get_all_documents_in_collection(collection_id)
    todo = [d for d in docs if rescan or not d.get("scan_json")]

    retriever = APP_STATE.get("rag_retriever_instance")
    scanned, failed = 0, 0
    for d in todo[:500]:  # bounded per call; re-invoke to resume
        try:
            text = ""
            if retriever is not None:
                try:
                    text = await retriever.get_document_text(collection_id, d["document_id"])
                except Exception:
                    text = ""
            scan = corpus_screen.screen_document(d.get("filename") or "", text or "")
            if not text:
                scan["mode"] = "metadata_only"  # honest: no chunk text reachable
            db.set_document_scan(d["document_id"], scan)
            scanned += 1
        except Exception:
            failed += 1
    return jsonify({"status": "ok", "scanned": scanned, "failed": failed,
                    "remaining": max(0, len(todo) - 500),
                    "manifest": db.get_scan_manifest(collection_id)}), 200
