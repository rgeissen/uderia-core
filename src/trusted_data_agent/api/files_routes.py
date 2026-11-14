"""
REST face of Files and file stores (PROP-010 §4.9).

  User (own files only):
    GET    /v1/files                         list / search, with facet counts and storage use
    GET    /v1/files/kept                    the anchors kept in a turn (drives "Kept · Open")
    POST   /v1/files                         + Add: keep local files directly (multipart)
    POST   /v1/files/keep                    keep part of an answer (answer · table · chart · component)
    GET    /v1/files/<id>                    metadata + provenance
    PATCH  /v1/files/<id>                    rename
    DELETE /v1/files/<id>                    move to Recently deleted
    POST   /v1/files/<id>/restore            out of Recently deleted
    POST   /v1/files/<id>/keep               an attachment → kept (survives its conversation)
    POST   /v1/files/<id>/seen               clear the "New" marker
    GET    /v1/files/<id>/preview            a safe, bounded representation
    GET    /v1/files/<id>/download           THE governed exit (?format=, ?with_evidence=1)
    POST   /v1/files/download                a zip of several (each authorized and audited)
    POST   /v1/files/<id>/attach             attach a kept file to a conversation (by reference)
    POST   /v1/files/<id>/to-canvas          the text of a code/text file, for a new canvas
    POST   /v1/files/<id>/to-knowledge       add a kept document to a knowledge repository (its own flow)
    GET    /v1/files/knowledge-targets       the caller's knowledge repositories
    POST   /v1/files/save                    Save to Files for a browser-built export (multipart)
    POST   /v1/files/exports/recorded        record a browser-built download in the audit trail
    POST   /v1/files/dashboard-snapshot      a dashboard as one PDF, kept in Files
    GET    /v1/file-stores/available         the stores the caller's agents may be bound to

  Admin (Administration → Components → Files):
    GET/POST          /v1/file-stores/configurations
    GET/PUT/DELETE    /v1/file-stores/configurations/<id>
    POST              /v1/file-stores/configurations/<id>/test
    POST              /v1/file-stores/configurations/<id>/default
    GET               /v1/file-stores/backends
    GET/PUT           /v1/file-stores/settings
    POST              /v1/file-stores/migrate   {from, to, dry_run}
    GET               /v1/file-stores/capacity  provisioned · held · free · promised · top users
    PUT/DELETE        /v1/file-stores/user-quotas/<user_uuid>   a per-user quota override
"""
from __future__ import annotations

from trusted_data_agent.api.operations import Arg, operation

import asyncio
import base64
import json
import logging
from typing import Optional
from urllib.parse import quote

from quart import Blueprint, Response, jsonify, request

from trusted_data_agent.auth.middleware import require_admin, require_auth
from trusted_data_agent.filestore import db as fdb
from trusted_data_agent.filestore import service as fsvc
from trusted_data_agent.filestore import settings as fset
from trusted_data_agent.filestore.catalog import BACKENDS, MASK, SECRET_KEYS
from trusted_data_agent.filestore.factory import BACKEND_TYPES, build_backend, user_tier

logger = logging.getLogger("quart.app")

files_bp = Blueprint("files_bp", __name__)

COMPONENT_ID = "files"


def _err(e: fsvc.FilesError):
    body = {"status": "error", "message": e.message, "code": e.code}
    body.update(e.extra or {})
    return jsonify(body), e.status


def _component_available(user_uuid: str) -> bool:
    try:
        from trusted_data_agent.components.settings import is_component_available
        return bool(is_component_available(COMPONENT_ID, user_uuid))
    except Exception:  # noqa: BLE001 — fail open, like the component gate itself
        return True


def _require_component(user_uuid: str):
    if not _component_available(user_uuid):
        return jsonify({"status": "error", "code": "component_disabled",
                        "message": "Files is turned off for you. An admin can turn it on in Administration → Components → Files."}), 403
    return None


def _disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "'").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


# ── user ─────────────────────────────────────────────────────────────────────

@files_bp.route("/v1/files", methods=["GET"])
@operation("files.list", effect="read", scope="own",
           summary="The caller's files — uploads, kept answers, tables, charts and exports — newest first, searchable by name",
           query={"q": "words in the file name", "kind": Arg("string", "only one kind",
                                                          enum=("all", "upload", "document", "table", "image", "export", "deleted")),
                  "profile_id": "only files an agent produced", "limit": Arg("integer", "at most this many (default 200)")},
           summary_fields=("id", "filename", "can_knowledge"))
@require_auth
async def list_files(current_user):
    a = request.args
    try:
        limit = int(a.get("limit") or 200)
        offset = int(a.get("offset") or 0)
    except ValueError:
        return jsonify({"status": "error", "message": "limit and offset must be numbers"}), 400
    out = await asyncio.to_thread(
        fsvc.list_files, current_user.id, q=a.get("q") or "", kind=a.get("kind") or "all",
        origin=a.get("origin") or None, profile_id=a.get("profile_id") or None,
        session_id=a.get("session_id") or None, scheduled_task_id=a.get("scheduled_task_id") or None,
        flow_id=a.get("flow_id") or None, component_id=a.get("component_id") or None,
        since=a.get("since") or None, only_new=a.get("new") in ("1", "true"), sort=a.get("sort") or "newest",
        limit=limit, offset=offset)
    out["status"] = "success"
    out["available"] = _component_available(current_user.id)
    return jsonify(out)


@files_bp.route("/v1/files/kept", methods=["GET"])
@require_auth
async def kept_in_turn(current_user):
    sid = request.args.get("session_id")
    if not sid:
        return jsonify({"status": "error", "message": "session_id is required"}), 400
    turn = request.args.get("turn_id")
    try:
        turn_id = int(turn) if turn not in (None, "") else None
    except ValueError:
        return jsonify({"status": "error", "message": "turn_id must be a number"}), 400
    return jsonify({"status": "success", "kept": await asyncio.to_thread(fsvc.kept_anchors, current_user.id, sid, turn_id)})


@files_bp.route("/v1/files/attachments", methods=["GET"])
@require_auth
async def attachment_states(current_user):
    """What each attachment of a conversation is now — ``live`` (with its lifetime), ``deleted``
    (restorable until ``purge_after``) or ``gone`` — keyed by the id its chip carries. Read from
    the conversation's own manifest, so files attached from Files are covered as well."""
    sid = request.args.get("session_id")
    if not sid:
        return jsonify({"status": "error", "message": "session_id is required"}), 400
    from trusted_data_agent.filestore import session_files

    def _states():
        m = session_files.load_manifest(current_user.id, sid)
        return m is not None, {k: session_files.entry_state(current_user.id, v) for k, v in (m or {}).items()}
    # `manifest: false` — no readable list: a chip then says nothing rather than "deleted"
    readable, states = await asyncio.to_thread(_states)
    return jsonify({"status": "success", "manifest": readable, "attachments": states})


@files_bp.route("/v1/files", methods=["POST"])
@require_auth
async def add_files(current_user):
    blocked = _require_component(current_user.id)
    if blocked:
        return blocked
    files = await request.files
    form = await request.form
    items = files.getlist("files")
    if not items:
        return jsonify({"status": "error", "message": "No files provided"}), 400
    if len(items) > 20:
        return jsonify({"status": "error", "message": "At most 20 files at once"}), 400
    added, refused = [], []
    for fs in items:
        try:
            data = fs.read()
            if not data:
                refused.append({"filename": fs.filename, "reason": "the file is empty"})
                continue
            added.append(await fsvc.add_direct(current_user.id, fs.filename or "file", data, form.get("profile_id") or None))
        except fsvc.FilesError as e:
            refused.append({"filename": fs.filename, "reason": e.message})
    status = 200 if added else 400
    return jsonify({"status": "success" if added else "error", "files": added, "refused": refused,
                    "message": None if added else (refused[0]["reason"] if refused else "Nothing was added")}), status


@files_bp.route("/v1/files/keep", methods=["POST"])
@require_auth
async def keep_from_answer(current_user):
    blocked = _require_component(current_user.id)
    if blocked:
        return blocked
    body = await request.get_json(silent=True) or {}
    try:
        turn_id = int(body.get("turn_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "turn_id is required"}), 400
    if not body.get("session_id") or not body.get("kind"):
        return jsonify({"status": "error", "message": "session_id and kind are required"}), 400
    client_bytes = None
    if body.get("data_base64"):
        try:
            client_bytes = base64.b64decode(body["data_base64"].split(",", 1)[-1], validate=False)
        except Exception:  # noqa: BLE001
            return jsonify({"status": "error", "message": "data_base64 is not valid base64"}), 400
    try:
        out = await fsvc.keep_from_turn(
            current_user.id, session_id=body["session_id"], turn_id=turn_id, kind=body["kind"],
            anchor=body.get("anchor"), headers=body.get("headers"), client_bytes=client_bytes,
            client_filename=body.get("filename"), client_media_type=body.get("media_type"),
            allow_duplicate=bool(body.get("allow_duplicate")), title=body.get("title"))
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", **out})


@files_bp.route("/v1/files/download", methods=["POST"])
@require_auth
async def download_many(current_user):
    body = await request.get_json(silent=True) or {}
    ids = [str(i) for i in (body.get("ids") or []) if i]
    if not ids:
        return jsonify({"status": "error", "message": "ids is required"}), 400
    data, skipped = await fsvc.download_many(ids, current_user.id)
    if len(skipped) == len(ids):
        return jsonify({"status": "error", "message": skipped[0]["reason"], "skipped": skipped}), 403
    resp = Response(data, mimetype="application/zip")
    resp.headers["Content-Disposition"] = _disposition("files.zip")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Files-Skipped"] = str(len(skipped))
    return resp


@files_bp.route("/v1/files/<file_id>", methods=["GET"])
@require_auth
async def get_file(current_user, file_id):
    try:
        row = fsvc.owned(file_id, current_user.id)
    except fsvc.FilesError as e:
        return _err(e)
    d = fsvc.describe(row, fsvc.store_index())
    d["provenance"] = await fsvc.provenance(row, current_user.id)
    return jsonify({"status": "success", "file": d})


@files_bp.route("/v1/files/<file_id>", methods=["PATCH"])
@require_auth
async def rename_file(current_user, file_id):
    body = await request.get_json(silent=True) or {}
    if not (body.get("filename") or "").strip():
        return jsonify({"status": "error", "message": "filename is required"}), 400
    try:
        row = await asyncio.to_thread(fsvc.rename, file_id, current_user.id, body["filename"])
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", "file": fsvc.describe(row, fsvc.store_index())})


@files_bp.route("/v1/files/<file_id>", methods=["DELETE"])
@require_auth
async def delete_file(current_user, file_id):
    try:
        row = await asyncio.to_thread(fsvc.soft_delete, file_id, current_user.id)
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", "purged": bool(row.get("purged")), "purge_after": row.get("purge_after")})


@files_bp.route("/v1/files/<file_id>/restore", methods=["POST"])
@require_auth
async def restore_file(current_user, file_id):
    try:
        row = await asyncio.to_thread(fsvc.restore, file_id, current_user.id)
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", "file": fsvc.describe(row, fsvc.store_index())})


@files_bp.route("/v1/files/<file_id>/keep", methods=["POST"])
@require_auth
async def keep_attachment(current_user, file_id):
    blocked = _require_component(current_user.id)
    if blocked:
        return blocked
    try:
        row = await asyncio.to_thread(fsvc.keep, file_id, current_user.id)
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", "file": fsvc.describe(row, fsvc.store_index())})


@files_bp.route("/v1/files/<file_id>/seen", methods=["POST"])
@require_auth
async def mark_seen(current_user, file_id):
    await asyncio.to_thread(fsvc.mark_seen, file_id, current_user.id)
    return jsonify({"status": "success"})


@files_bp.route("/v1/files/<file_id>/preview", methods=["GET"])
@require_auth
async def preview_file(current_user, file_id):
    try:
        row = fsvc.owned(file_id, current_user.id)
    except fsvc.FilesError as e:
        return _err(e)
    pv = await asyncio.to_thread(fsvc.preview, row)
    if row.get("origin_kind") == "scheduled_run":
        await asyncio.to_thread(fsvc.mark_seen, file_id, current_user.id)
    return jsonify({"status": "success", "preview": pv})


@files_bp.route("/v1/files/<file_id>/download", methods=["GET"])
@require_auth
async def download_file(current_user, file_id):
    try:
        row = fsvc.owned(file_id, current_user.id)
        data, name, media = await fsvc.download(row, current_user.id, request.args.get("format") or None,
                                                request.args.get("with_evidence") in ("1", "true"))
    except fsvc.FilesError as e:
        return _err(e)
    resp = Response(data, mimetype=media if media in fsvc.SAFE_DOWNLOAD_TYPES else "application/octet-stream")
    resp.headers["Content-Disposition"] = _disposition(name)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


@files_bp.route("/v1/files/<file_id>/attach", methods=["POST"])
@require_auth
async def attach_file(current_user, file_id):
    body = await request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    if not session_id:
        return jsonify({"status": "error", "message": "session_id is required"}), 400
    from trusted_data_agent.core import session_manager
    if not await session_manager.get_session(current_user.id, session_id):
        return jsonify({"status": "error", "message": "Session not found"}), 404
    try:
        row = fsvc.owned(file_id, current_user.id, include_deleted=False)
        entry = await asyncio.to_thread(_attach_by_reference, current_user.id, session_id, row)
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", "file": entry})


def _attach_by_reference(user_uuid: str, session_id: str, row: dict) -> dict:
    """Add a kept file to a conversation's attachments without copying it. It is read through
    the same accessor (and the same upload-time readers) as a fresh upload."""
    import os
    from trusted_data_agent.api.routes import IMAGE_EXTENSIONS, allowed_upload_extensions
    from trusted_data_agent.filestore import session_files
    from trusted_data_agent.llm.document_upload import DocumentUploadHandler
    ext = os.path.splitext(row["filename"])[1].lower()
    if ext not in allowed_upload_extensions():
        raise fsvc.FilesError(f"A turn can't read {ext or 'this type of'} files, so this file can't be attached.", 400, "not_attachable")
    data = fsvc.read_bytes(row)
    manifest = session_files.load_manifest(user_uuid, session_id) or {}
    is_image = ext in IMAGE_EXTENSIONS
    content_type = DocumentUploadHandler()._get_mime_type(ext)
    if is_image:
        text = f"[Image file: {row['filename']} - visual content not available in text mode]"
    else:
        text = DocumentUploadHandler().extract_text_from_bytes(data, row["filename"]).get("content", "")
    entry = {"file_ref": row["id"], "filename": row["filename"], "content_type": content_type,
             "file_size": len(data), "extracted_text": text, "is_image": is_image, "from_files": True}
    source_summary = None
    try:
        from trusted_data_agent.llm import image_sources as _isrc
        if _isrc.enabled():
            src = _isrc.extract(row["id"], row["filename"], None, content_type, data=data)
            entry["source"] = _isrc.to_record(src)
            if src.verified and src.usable:
                entry["extracted_text"] = text = _isrc.document_text_for(src)
            source_summary = src.to_dict()
    except Exception:  # noqa: BLE001
        pass
    manifest[row["id"]] = entry
    session_files.save_manifest(user_uuid, session_id, manifest)
    return {"file_id": row["id"], "original_filename": row["filename"], "content_type": content_type,
            "file_size": len(data), "extracted_text_preview": (text[:200] + "...") if len(text) > 200 else text,
            "extracted_text_length": len(text), "is_image": is_image, "from_files": True,
            **({"source": source_summary} if source_summary else {})}


@files_bp.route("/v1/files/<file_id>/to-canvas", methods=["POST"])
@require_auth
async def to_canvas(current_user, file_id):
    try:
        row = fsvc.owned(file_id, current_user.id, include_deleted=False)
        e = fsvc.ext_of(row["filename"])
        if e not in fsvc.CANVAS_EXT:
            raise fsvc.FilesError("Only text and code files open in Canvas", 400, "not_text")
        data = await asyncio.to_thread(fsvc.read_bytes, row)
    except fsvc.FilesError as err:
        return _err(err)
    lang = {"md": "markdown", "markdown": "markdown", "py": "python", "js": "javascript", "ts": "typescript",
            "sql": "sql", "json": "json", "html": "html", "css": "css", "yaml": "yaml", "yml": "yaml",
            "ttl": "turtle", "xml": "xml", "sh": "shell", "txt": "text"}.get(e, "text")
    return jsonify({"status": "success", "title": row["filename"].rsplit(".", 1)[0], "language": lang,
                    "content": data.decode("utf-8", errors="replace")})


@files_bp.route("/v1/files/knowledge-targets", methods=["GET"])
@operation("files.knowledge_targets", effect="read", scope="own",
           summary="The knowledge repositories a file can be added to — the caller's own",
           summary_fields=("id", "name", "document_count"))
@require_auth
async def knowledge_targets(current_user):
    """The knowledge repositories a kept document can be added to: the caller's own (the
    repository's upload flow refuses any other)."""
    from trusted_data_agent.core.collection_db import get_collection_db
    rows = await asyncio.to_thread(get_collection_db().get_all_collections, current_user.id)
    out = [{"id": c["id"], "name": c.get("name") or c.get("collection_name"),
            "document_count": c.get("document_count"), "description": c.get("description")}
           for c in rows if c.get("repository_type") == "knowledge" and c.get("owner_user_id") == current_user.id]
    out.sort(key=lambda c: (c["name"] or "").lower())
    return jsonify({"status": "success", "repositories": out})


@files_bp.route("/v1/files/<file_id>/to-knowledge", methods=["POST"])
@operation("files.to_knowledge", effect="write", scope="own",
           summary="Add a document from the caller's Files to one of their knowledge repositories — screened, chunked and embedded by the repository's own upload",
           path={"file_id": "the file (files.list lists them)"},
           body={"collection_id": Arg("string", "the knowledge repository (files.knowledge_targets lists them)", required=True),
                 "chunking_strategy": Arg("string", "how it is cut into passages", enum=("semantic", "paragraph", "sentence", "fixed_size", "none")),
                 "chunk_size": Arg("integer", "passage size in characters"), "chunk_overlap": Arg("integer", "characters shared by neighbours")},
           notes="Only documents (PDF, Word, Markdown, text). The same document added again replaces itself; unchanged content is skipped.")
@require_auth
async def to_knowledge(current_user, file_id):
    """Add a kept document to a knowledge repository through the repository's OWN upload flow —
    its owner check, ingest firewall, chunking, embedding and change tracking — dispatched in
    process with the caller's credentials, so Files adds no second path into a repository."""
    from io import BytesIO
    from quart import current_app
    from werkzeug.datastructures import FileStorage
    body = await request.get_json(silent=True) or {}
    try:
        collection_id = int(body.get("collection_id"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "collection_id is required"}), 400
    try:
        row = fsvc.owned(file_id, current_user.id, include_deleted=False)
        if fsvc.ext_of(row["filename"]) not in fsvc.KNOWLEDGE_EXT:
            raise fsvc.FilesError("Only documents (PDF, Word, Markdown, text) can be added to a knowledge repository",
                                  400, "not_a_document")
        data = await asyncio.to_thread(fsvc.read_bytes, row)
    except fsvc.FilesError as e:
        return _err(e)
    form = {"title": row["filename"].rsplit(".", 1)[0], "category": "files"}
    for k in ("chunking_strategy", "chunk_size", "chunk_overlap"):
        if body.get(k) not in (None, ""):
            form[k] = str(body[k])
    # The inner hop gets its OWN one-request credential, never the caller's: a browser's session
    # token would reach further than this one upload needs, and an agent's operation token
    # (TDA_Platform) is bound to THIS route and is refused on any other.
    from trusted_data_agent.auth.security import create_operation_token
    upload_path = f"/api/v1/knowledge/repositories/{collection_id}/documents"
    auth = "Bearer " + create_operation_token(current_user.id, method="POST", path=upload_path,
                                              op="files.to_knowledge")
    client = current_app.test_client()
    resp = await client.post(upload_path,
                             form=form, headers={"Authorization": auth},
                             files={"file": FileStorage(BytesIO(data), filename=row["filename"],
                                                        content_type=row.get("media_type") or "application/octet-stream")})
    out = await resp.get_json(silent=True) or {}
    if resp.status_code >= 400:
        return jsonify({"status": "error", "message": out.get("message") or "The repository did not take the document",
                        "code": "knowledge_refused"}), resp.status_code
    fsvc.record_promotion(row, current_user.id, collection_id, out)
    return jsonify({"status": out.get("status") or "success", "message": out.get("message"),
                    "document_id": out.get("document_id"), "chunks_stored": out.get("chunks_stored"),
                    "collection_id": collection_id}), 200


@files_bp.route("/v1/files/save", methods=["POST"])
@require_auth
async def save_export(current_user):
    """Save to Files for an export the browser built (a receipt, an evidence bundle, a chart
    PNG, a canvas file). The file carries whatever the caller names: the turn (with its
    provenance), the component and its instance, or the conversation."""
    blocked = _require_component(current_user.id)
    if blocked:
        return blocked
    files = await request.files
    form = await request.form
    fs = files.get("file")
    if not fs:
        return jsonify({"status": "error", "message": "No file provided"}), 400
    turn = form.get("turn_id")
    try:
        turn_id = int(turn) if turn not in (None, "") else None
    except ValueError:
        return jsonify({"status": "error", "message": "turn_id must be a number"}), 400
    source = None
    if form.get("source"):
        try:
            source = json.loads(form["source"])
        except ValueError:
            return jsonify({"status": "error", "message": "source must be JSON"}), 400
    try:
        out = await fsvc.save_export(
            current_user.id, fs.read(), form.get("filename") or fs.filename or "export",
            component_id=form.get("component_id") or None, instance_id=form.get("instance_id") or None,
            session_id=form.get("session_id") or None, turn_id=turn_id, profile_id=form.get("profile_id") or None,
            source=source, allow_duplicate=form.get("allow_duplicate") in ("1", "true"))
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", "file": out})


@files_bp.route("/v1/files/dashboard-snapshot", methods=["POST"])
@require_auth
async def dashboard_snapshot(current_user):
    """*Save snapshot to Files* on a dashboard: ``{dashboard_id, images: {tile_id: data-URL PNG}}``."""
    blocked = _require_component(current_user.id)
    if blocked:
        return blocked
    body = await request.get_json(silent=True) or {}
    if not body.get("dashboard_id"):
        return jsonify({"status": "error", "message": "dashboard_id is required"}), 400
    images = {}
    for tid, url in (body.get("images") or {}).items():
        if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
            continue
        try:
            images[str(tid)] = base64.b64decode(url.split(",", 1)[1], validate=False)
        except Exception:  # noqa: BLE001 — a tile without a readable picture is written from its data
            continue
    try:
        out = await asyncio.to_thread(fsvc.snapshot_dashboard, current_user.id, str(body["dashboard_id"]), images)
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", "file": out})


@files_bp.route("/v1/files/exports/recorded", methods=["POST"])
@require_auth
async def record_export(current_user):
    """A browser-built download, recorded (it cannot be governed: its data is in the browser)."""
    body = await request.get_json(silent=True) or {}
    if not body.get("filename"):
        return jsonify({"status": "error", "message": "filename is required"}), 400
    try:
        size = int(body.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    fsvc.record_browser_export(current_user.id, what=str(body.get("what") or "export")[:60],
                               filename=fsvc.safe_filename(body["filename"]), size=size,
                               session_id=body.get("session_id") or None)
    return jsonify({"status": "success"})


async def export_response(current_user_id: str, body, filename: str, mimetype: str, *, what: str,
                          data_derived: bool = False, component_id: Optional[str] = None,
                          instance_id: Optional[str] = None, session_id: Optional[str] = None,
                          profile_id: Optional[str] = None, resource: Optional[str] = None):
    """The ONE exit of a server-built export (PROP-010 phase 3). ``?to=files`` saves it to Files
    instead of sending it — the export's bytes, made by the server, with its component and
    instance; otherwise the tier's download policy decides and the export is recorded."""
    data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if (request.args.get("to") or "").lower() == "files":
        blocked = _require_component(current_user_id)
        if blocked:
            return blocked
        try:
            out = await fsvc.save_export(current_user_id, data, filename, component_id=component_id,
                                         instance_id=instance_id, session_id=session_id, profile_id=profile_id,
                                         data_derived=data_derived,
                                         allow_duplicate=request.args.get("allow_duplicate") in ("1", "true"))
        except fsvc.FilesError as e:
            return _err(e)
        return jsonify({"status": "success", "file": out})
    try:
        # in the request context, so the audit row carries the caller's address
        fsvc.govern_export(current_user_id, what=what, filename=filename, size=len(data),
                           data_derived=data_derived, resource=resource)
    except fsvc.FilesError as e:
        return _err(e)
    return Response(data, mimetype=mimetype, headers={"Content-Disposition": _disposition(filename),
                                                     "X-Content-Type-Options": "nosniff"})


@files_bp.route("/v1/file-stores/available", methods=["GET"])
@require_auth
async def available_stores(current_user):
    tier = user_tier(current_user.id)
    stores = [s for s in fdb.list_store_configs() if fset.is_store_allowed(s["id"], tier)]
    return jsonify({"status": "success", "default_store_id": fdb.default_store_id(),
                    "stores": [{"id": s["id"], "name": s["name"], "backend_type": s["backend_type"],
                                "encrypt": bool(s["encrypt"]) or fset.require_encryption(),
                                "is_default": s["is_default"]} for s in stores]})


# ── admin ────────────────────────────────────────────────────────────────────

def _agents_per_store() -> dict:
    """How many agents (across users) are bound to each store — read from the profiles."""
    out: dict = {}
    try:
        from trusted_data_agent.core import sqlite_util
        conn = sqlite_util.connect(fset._db_path())
        try:
            for (pj,) in conn.execute("SELECT preferences_json FROM user_preferences").fetchall():
                try:
                    for p in (json.loads(pj or "{}").get("profiles") or []):
                        sid = (p or {}).get("fileStoreConfigId")
                        if sid:
                            out[sid] = out.get(sid, 0) + 1
                except Exception:  # noqa: BLE001
                    continue
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass
    return out


def _store_view(s: dict, usage: dict, agents: dict) -> dict:
    u = usage.get(s["id"]) or {"files": 0, "bytes": 0}
    return {**s, "encrypt": bool(s["encrypt"]) or fset.require_encryption(), "encrypt_setting": bool(s["encrypt"]),
            "files": u["files"], "bytes": u["bytes"], "bytes_label": fsvc._size_label(u["bytes"]),
            "agents": agents.get(s["id"], 0),
            "backend_label": BACKEND_TYPES.get(s["backend_type"], {}).get("label", s["backend_type"]),
            # where the files go, in the backend's own words (folder · bucket · container)
            "location": (s.get("config") or {}).get(BACKENDS.get(s["backend_type"], {}).get("location") or "", None)}


@files_bp.route("/v1/file-stores/configurations", methods=["GET"])
@require_admin
async def list_store_configs(current_user):
    usage = await asyncio.to_thread(fdb.store_usage)
    agents = await asyncio.to_thread(_agents_per_store)
    stores = await asyncio.to_thread(fdb.list_store_configs)
    return jsonify({"status": "success", "stores": [_store_view(s, usage, agents) for s in stores]})


def _validate_store(body: dict, partial: bool = False) -> None:
    bt = body.get("backend_type")
    if not partial or bt is not None:
        if bt not in BACKEND_TYPES:
            raise ValueError(f"backend_type must be one of {', '.join(BACKEND_TYPES)}")
    cfg = body.get("config") or {}
    if not partial:
        from trusted_data_agent.filestore.catalog import validate_config
        validate_config(bt, cfg)
    if "encrypt" in body and not body["encrypt"] and fset.require_encryption():
        raise ValueError("encryption is required on this deployment" + (" (hardened mode)" if fset.hardened() else
                                                                         " — change it in the Policy tab first"))


@files_bp.route("/v1/file-stores/configurations", methods=["POST"])
@require_admin
async def create_store_config(current_user):
    body = await request.get_json(silent=True) or {}
    try:
        _validate_store(body)
        body.pop("id", None)
        s = await asyncio.to_thread(fdb.save_store_config, body, current_user.id)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    _audit_admin(current_user.id, "file_store_create", f"Created file store {s['name']}", s["id"])
    return jsonify({"status": "success", "store": s}), 201


@files_bp.route("/v1/file-stores/configurations/<store_id>", methods=["GET"])
@require_admin
async def get_store_config(current_user, store_id):
    s = fdb.get_store_config(store_id, with_secrets=False)
    if not s:
        return jsonify({"status": "error", "message": "Store not found"}), 404
    return jsonify({"status": "success", "store": _store_view(s, fdb.store_usage(), _agents_per_store())})


@files_bp.route("/v1/file-stores/configurations/<store_id>", methods=["PUT"])
@require_admin
async def update_store_config(current_user, store_id):
    body = await request.get_json(silent=True) or {}
    body["id"] = store_id
    try:
        _validate_store(body, partial=True)
        s = await asyncio.to_thread(fdb.save_store_config, body, current_user.id)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    from trusted_data_agent.filestore.factory import reset_cache
    reset_cache()
    _audit_admin(current_user.id, "file_store_update", f"Updated file store {s['name']}", store_id)
    return jsonify({"status": "success", "store": s})


@files_bp.route("/v1/file-stores/configurations/<store_id>", methods=["DELETE"])
@require_admin
async def delete_store_config(current_user, store_id):
    if store_id == fset.PLATFORM_LOCAL_ID:
        return jsonify({"status": "error", "message": "The platform local store cannot be deleted"}), 400
    s = fdb.get_store_config(store_id, with_secrets=False)
    if not s:
        return jsonify({"status": "error", "message": "Store not found"}), 404
    n = len(await asyncio.to_thread(fdb.files_in_store, store_id))
    if n:
        return jsonify({"status": "error", "code": "store_not_empty", "files": n,
                        "message": f"{s['name']} still holds {n} file{'s' if n != 1 else ''}. Move them to another store first."}), 409
    await asyncio.to_thread(fdb.delete_store_config, store_id)
    _audit_admin(current_user.id, "file_store_delete", f"Deleted file store {s['name']}", store_id)
    return jsonify({"status": "success"})


@files_bp.route("/v1/file-stores/configurations/<store_id>/test", methods=["POST"])
@require_admin
async def test_store_config(current_user, store_id):
    body = await request.get_json(silent=True) or {}
    if store_id == "_draft":
        # A store being added (or edited): test the settings in the form before anything is
        # saved. An edited store's secrets arrive masked — they are filled from the saved store.
        try:
            cfg_in = dict(body.get("config") or {})
            if body.get("existing_id"):
                saved = (fdb.get_store_config(str(body["existing_id"]), with_secrets=True) or {}).get("config") or {}
                for k, v in list(cfg_in.items()):
                    if k in SECRET_KEYS and v == MASK:
                        cfg_in[k] = saved.get(k, "")
            _validate_store({**body, "config": cfg_in, "encrypt": True})
            backend = build_backend(body["backend_type"], cfg_in, cache=False)
        except Exception as e:  # noqa: BLE001
            return jsonify({"status": "success", "success": False, "message": str(e)})
        return jsonify({"status": "success", **(await asyncio.to_thread(backend.test_connection))})
    cfg = fdb.get_store_config(store_id, with_secrets=True)
    if not cfg:
        return jsonify({"status": "error", "message": "Store not found"}), 404
    try:
        backend = build_backend(cfg["backend_type"], cfg.get("config") or {}, cache=False)
        res = await asyncio.to_thread(backend.test_connection)
    except Exception as e:  # noqa: BLE001
        res = {"success": False, "message": str(e)}
    await asyncio.to_thread(fdb.record_test, store_id, bool(res.get("success")), res.get("message") or "")
    return jsonify({"status": "success", **res})


@files_bp.route("/v1/file-stores/configurations/<store_id>/default", methods=["POST"])
@require_admin
async def set_default_store(current_user, store_id):
    if not fdb.get_store_config(store_id, with_secrets=False):
        return jsonify({"status": "error", "message": "Store not found"}), 404
    await asyncio.to_thread(fdb.set_default_store, store_id)
    _audit_admin(current_user.id, "file_store_default", f"Default file store set to {store_id}", store_id)
    return jsonify({"status": "success"})


@files_bp.route("/v1/file-stores/backends", methods=["GET"])
@require_admin
async def list_backends(current_user):
    from trusted_data_agent.filestore.catalog import public_list
    return jsonify({"status": "success", "backends": public_list()})


@files_bp.route("/v1/file-stores/settings", methods=["GET"])
@require_admin
async def get_settings(current_user):
    return jsonify({"status": "success", "settings": await asyncio.to_thread(fset.get_settings)})


@files_bp.route("/v1/file-stores/settings", methods=["PUT"])
@require_admin
async def put_settings(current_user):
    body = await request.get_json(silent=True) or {}
    try:
        out = await asyncio.to_thread(fset.save_settings, body, current_user.id)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    _audit_admin(current_user.id, "file_store_policy", "Updated the Files policy", "file_store_settings", body)
    return jsonify({"status": "success", "settings": out})


@files_bp.route("/v1/file-stores/capacity", methods=["GET"])
@require_admin
async def capacity(current_user):
    rep = await asyncio.to_thread(fsvc.capacity_report)
    names = fsvc._usernames([o["user_uuid"] for o in rep["overrides"]])
    for o in rep["overrides"]:
        o["username"] = names.get(o["user_uuid"])
    return jsonify({"status": "success", "capacity": rep})


@files_bp.route("/v1/file-stores/user-quotas/<user_uuid>", methods=["PUT", "DELETE"])
@require_admin
async def user_quota(current_user, user_uuid):
    try:
        if request.method == "DELETE":
            ov = await asyncio.to_thread(fset.set_user_quota, user_uuid, None, current_user.id)
        else:
            body = await request.get_json(silent=True) or {}
            ov = await asyncio.to_thread(fset.set_user_quota, user_uuid, body.get("quota_mb"), current_user.id)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    _audit_admin(current_user.id, "file_store_user_quota", f"Files quota for {user_uuid}: "
                 + ("tier default" if request.method == "DELETE" else f"{ov.get(user_uuid)} MB"), user_uuid)
    return jsonify({"status": "success", "overrides": ov})


@files_bp.route("/v1/file-stores/migrate", methods=["POST"])
@require_admin
async def migrate_files(current_user):
    body = await request.get_json(silent=True) or {}
    src, dst = body.get("from"), body.get("to")
    if not src or not dst:
        return jsonify({"status": "error", "message": "from and to are required"}), 400
    for sid in (src, dst):
        if not fdb.get_store_config(sid, with_secrets=False):
            return jsonify({"status": "error", "message": f"Store {sid} not found"}), 404
    if body.get("dry_run", True):
        return jsonify({"status": "success", "plan": await asyncio.to_thread(fsvc.migration_plan, src, dst)})
    try:
        res = await asyncio.to_thread(fsvc.migrate, src, dst, current_user.id)
    except fsvc.FilesError as e:
        return _err(e)
    return jsonify({"status": "success", **res})


# ── key encryption: the platform key or an external key manager ───────────────

@files_bp.route("/v1/file-stores/keys", methods=["GET"])
@require_admin
async def get_key_encryption(current_user):
    from trusted_data_agent.filestore import kms
    view = await asyncio.to_thread(kms.public_view)
    view["usage"] = await asyncio.to_thread(fsvc.key_usage)
    return jsonify({"status": "success", **view})


@files_bp.route("/v1/file-stores/keys/test", methods=["POST"])
@require_admin
async def test_key_encryption(current_user):
    from trusted_data_agent.filestore import kms
    body = await request.get_json(silent=True) or {}
    provider = body.get("provider") or ""
    if provider not in kms.PROVIDERS:
        return jsonify({"status": "error", "message": f"provider must be one of {', '.join(kms.PROVIDERS)}"}), 400
    cfg = kms.merge_masked(provider, body.get("config") or {})
    return jsonify({"status": "success", **(await asyncio.to_thread(kms.test, provider, cfg))})


@files_bp.route("/v1/file-stores/keys", methods=["PUT"])
@require_admin
async def save_key_encryption(current_user):
    """Make a key manager (or the platform key) wrap NEW files' keys. Saved only after the
    settings pass a live wrap/unwrap test here — never on the form's word."""
    from trusted_data_agent.filestore import kms
    body = await request.get_json(silent=True) or {}
    provider = body.get("provider") or ""
    if provider not in kms.PROVIDERS:
        return jsonify({"status": "error", "message": f"provider must be one of {', '.join(kms.PROVIDERS)}"}), 400
    cfg = kms.merge_masked(provider, body.get("config") or {})
    res = await asyncio.to_thread(kms.test, provider, cfg)
    if not res.get("success"):
        return jsonify({"status": "error", "code": "test_failed", "message": f"Not saved — the test failed: {res.get('message')}"}), 400
    await asyncio.to_thread(kms.save, provider, cfg, current_user.id)
    _audit_admin(current_user.id, "file_keys_provider", f"New file keys are wrapped by {kms.PROVIDERS[provider]['label']}",
                 "keys", {"provider": provider, "key": cfg.get(kms.PROVIDERS[provider]["key_field"] or "", None)})
    view = await asyncio.to_thread(kms.public_view)
    view["usage"] = await asyncio.to_thread(fsvc.key_usage)
    return jsonify({"status": "success", **view})


@files_bp.route("/v1/file-stores/keys/rewrap", methods=["POST"])
@require_admin
async def rewrap_keys(current_user):
    body = await request.get_json(silent=True) or {}
    res = await asyncio.to_thread(fsvc.rewrap_to_active, current_user.id, bool(body.get("dry_run", True)))
    return jsonify({"status": "success", **res})


def _audit_admin(admin_uuid, action, details, resource, metadata=None):
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        log_audit_event(admin_uuid, action, details, resource=f"file_store:{resource}", metadata=metadata)
    except Exception:  # noqa: BLE001
        pass
