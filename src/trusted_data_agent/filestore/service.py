"""
The file service — the ONE write path and the ONE read path for files (PROP-010).

Every file that enters a store passes ``put_file``: size and quota, store resolution (the
agent of the turn decides), envelope encryption, the keyed fingerprint, the row, the live
announcement. Every byte that leaves passes ``read_bytes``; every byte that leaves the
PLATFORM passes ``download`` (policy, conversion, audit). Nothing else in the platform opens
a store.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from . import crypto, db, formats
from . import settings as fs_settings
from .base import FileStoreError, ObjectNotFound
from .factory import backend_for_store, resolve_store_id, user_tier

logger = logging.getLogger("quart.app")


class FilesError(Exception):
    def __init__(self, message: str, status: int = 400, code: Optional[str] = None, extra: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code or "files_error"
        self.extra = extra or {}


# ── classification ───────────────────────────────────────────────────────────

IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
TABLE_EXT = {"csv", "tsv", "xlsx"}
DOC_EXT = {"md", "markdown", "txt", "pdf", "docx"}
TEXT_EXT = {"md", "markdown", "txt", "csv", "tsv", "json", "sql", "py", "js", "ts", "html", "css", "xml",
            "yaml", "yml", "ttl", "nt", "rdf", "jsonld", "sh", "r"}
CANVAS_EXT = {"md", "markdown", "txt", "json", "sql", "py", "js", "ts", "html", "css", "xml", "yaml", "yml",
              "ttl", "nt", "rdf", "jsonld", "sh", "r"}
KNOWLEDGE_EXT = {"pdf", "docx", "md", "markdown", "txt"}

TYPE_LABELS = {"csv": "CSV", "tsv": "TSV", "xlsx": "XLSX", "md": "Markdown", "markdown": "Markdown", "txt": "Text",
               "pdf": "PDF", "docx": "Word", "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "gif": "GIF",
               "webp": "WebP", "svg": "SVG", "json": "JSON", "sql": "SQL", "ttl": "RDF (Turtle)",
               "nt": "RDF (N-Triples)", "rdf": "RDF (XML)", "jsonld": "JSON-LD", "py": "Python",
               "js": "JavaScript", "html": "HTML", "zip": "ZIP"}

MEDIA = {"csv": "text/csv", "tsv": "text/tab-separated-values", "md": "text/markdown", "txt": "text/plain",
         "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "png": "image/png",
         "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp",
         "svg": "image/svg+xml", "json": "application/json", "sql": "text/plain", "ttl": "text/turtle",
         "nt": "application/n-triples", "rdf": "application/rdf+xml", "jsonld": "application/ld+json",
         "zip": "application/zip", "html": "text/html"}

# The only types a download may announce; anything else goes out as octet-stream.
SAFE_DOWNLOAD_TYPES = set(MEDIA.values()) - {"text/html", "image/svg+xml"}


def ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def category(row: Dict[str, Any]) -> str:
    if row.get("source_kind") == "upload":
        return "upload"
    e = ext_of(row.get("filename") or "")
    if e in IMAGE_EXT:
        return "image"
    if e in TABLE_EXT:
        return "table"
    if e in DOC_EXT:
        return "document"
    return "export"


def media_type_for(filename: str, given: Optional[str] = None) -> str:
    return given or MEDIA.get(ext_of(filename), "application/octet-stream")


def safe_filename(name: str, fallback: str = "file") -> str:
    name = os.path.basename(str(name or "")).strip().replace("\x00", "")
    name = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]+", "_", name)
    name = re.sub(r"\s+", " ", name)[:180].strip(" .")
    return name or fallback


def slug(text: str, n: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return (s[:n].rstrip("_") or "file")


def _object_key(user_uuid: str, file_id: str) -> str:
    owner = re.sub(r"[^A-Za-z0-9_-]", "", user_uuid or "")[:80] or "anon"
    return f"{owner}/{file_id.replace('-', '')}"


def attachable_exts() -> set:
    try:
        from trusted_data_agent.api.routes import allowed_upload_extensions
        return {e.lstrip(".") for e in allowed_upload_extensions()}
    except Exception:  # noqa: BLE001
        return {"pdf", "txt", "docx", "md", "jpg", "jpeg", "png", "gif", "webp"}


def _announce(user_uuid: str, action: str, ids=None, session_id: Optional[str] = None) -> None:
    try:
        from trusted_data_agent.core.ui_notify import announce_files_change
        announce_files_change(user_uuid, action, ids, session_id)
    except Exception:  # noqa: BLE001
        pass


# ── write ────────────────────────────────────────────────────────────────────

def put_file(*, user_uuid: str, data: bytes, filename: str, source_kind: str, media_type: Optional[str] = None,
             lifetime: str = "kept", origin_kind: str = "turn", store_id: Optional[str] = None,
             profile_id: Optional[str] = None, profile: Optional[Dict[str, Any]] = None,
             enforce_quota: bool = True, announce: bool = True, file_id: Optional[str] = None,
             **meta) -> Dict[str, Any]:
    """Store one file. ``meta`` carries the provenance columns (session_id, turn_id, question,
    profile_tag, profile_type, session_title, bom_json, data_derived, source_anchor, pair_id,
    scheduled_*, flow_*, component_id, instance_id, source_json)."""
    if not isinstance(data, (bytes, bytearray)):
        raise FilesError("file content must be bytes")
    data = bytes(data)
    filename = safe_filename(filename)
    tier = user_tier(user_uuid)
    if not fs_settings.files_enabled_for_tier(tier) and lifetime == "kept" and source_kind != "upload":
        raise FilesError("Keeping files is turned off for your tier. An admin can turn it on in "
                         "Administration → Components → Files → Policy.", 403, "files_disabled")
    limit = fs_settings.max_file_bytes()
    if len(data) > limit:
        raise FilesError(f"'{filename}' is larger than the {limit // (1024 * 1024)} MB limit", 413, "too_large")
    if enforce_quota and lifetime == "kept":
        q = fs_settings.quota_bytes_for_user(user_uuid, tier)
        used = db.user_bytes(user_uuid)
        if q and used + len(data) > q:
            raise FilesError(f"Files is full ({_size_label(used)} of {_size_label(q)}). Delete files or ask an admin "
                             "for more space.", 507, "quota_exceeded", {"used": used, "quota": q})

    sid = resolve_store_id(profile_id, user_uuid, explicit=store_id, profile=profile)
    try:
        cfg, backend = backend_for_store(sid)
    except Exception as e:  # noqa: BLE001
        raise FilesError(f"The store for this agent cannot be opened: {e}", 503, "store_unavailable") from e
    cap = store_capacity_bytes(cfg)
    if cap:
        held = db.store_bytes(sid)
        if held + len(data) > cap:
            raise FilesError(f"The store {cfg.get('name')} is full ({_size_label(held)} of the {_size_label(cap)} provisioned "
                             "for Files). An admin can add capacity or move files to another store.", 507, "store_full",
                             {"store_id": sid})
    encrypt = bool(cfg.get("encrypt", True)) or fs_settings.require_encryption()
    fid = file_id or str(uuid.uuid4())
    key = _object_key(user_uuid, fid)
    if encrypt:
        blob, wrapped = crypto.encrypt_object(data, fid)
    else:
        blob, wrapped = data, None
    try:
        backend.put(key, blob)
    except FileStoreError as e:
        raise FilesError(f"The store {cfg.get('name')} could not take the file: {e}", 503, "store_unavailable") from e

    now = db.now_iso()
    row = {
        "id": fid, "user_uuid": user_uuid, "store_config_id": sid, "object_key": key,
        "filename": filename, "media_type": media_type_for(filename, media_type), "size_bytes": len(data),
        "content_hmac": crypto.fingerprint(data), "wrapped_key": wrapped,
        "key_version": crypto.key_version_of(wrapped), "source_kind": source_kind,
        "lifetime": lifetime, "kept_at": now if lifetime == "kept" else None, "origin_kind": origin_kind,
        "profile_id": profile_id, "created_at": now,
    }
    for k, v in meta.items():
        if k in db.FILE_COLUMNS and k not in row:
            row[k] = (int(v) if k == "data_derived" else v)
    # A writer that names the agent by id alone (+ Add in the Files panel) still records which
    # agent it was: the tag and class are the label every surface reads when the agent is gone.
    if profile_id and not (row.get("profile_tag") and row.get("profile_type")):
        a = ({"tag": profile.get("tag"), "profile_type": profile.get("profile_type")} if profile
             else agents_of(user_uuid).get(profile_id)) or {}
        row["profile_tag"] = row.get("profile_tag") or a.get("tag")
        row["profile_type"] = row.get("profile_type") or a.get("profile_type")
    for k in ("bom_json", "source_json"):
        if isinstance(row.get(k), (dict, list)):
            row[k] = json.dumps(row[k], ensure_ascii=False)
    try:
        db.insert_file(row)
    except Exception:
        try:
            backend.delete(key)
        except Exception:  # noqa: BLE001
            pass
        raise
    if announce:
        _announce(user_uuid, "kept" if lifetime == "kept" else "attached", [fid], row.get("session_id"))
    return db.get_file(fid)


def _size_label(n: int) -> str:
    n = int(n or 0)
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            v = n / div
            return f"{v:.1f} {unit}" if v < 10 else f"{v:.0f} {unit}"
    return f"{n} B"


# ── read ─────────────────────────────────────────────────────────────────────

def read_bytes(row: Dict[str, Any]) -> bytes:
    """The plaintext of one file. Raises ``FilesError(503)`` when its store cannot be reached
    and ``FilesError(410)`` when the object is gone."""
    try:
        _cfg, backend = backend_for_store(row["store_config_id"])
        blob = backend.get(row["object_key"])
    except ObjectNotFound as e:
        raise FilesError("The file's content is no longer in its store.", 410, "object_missing") from e
    except (FileStoreError, LookupError) as e:
        raise FilesError(f"The store holding this file cannot be reached: {e}", 503, "store_unavailable") from e
    if row.get("wrapped_key"):
        try:
            return crypto.decrypt_object(blob, row["wrapped_key"], row["id"])
        except crypto.FileCryptoError as e:
            raise FilesError("The file does not decrypt with this deployment's key.", 500, "decrypt_failed") from e
    return blob


def owned(file_id: str, user_uuid: str, include_deleted: bool = True) -> Dict[str, Any]:
    row = db.get_file(file_id, user_uuid)
    if not row or (row.get("deleted_at") and not include_deleted):
        raise FilesError("File not found", 404, "not_found")
    return row


# ── lifecycle ────────────────────────────────────────────────────────────────

def keep(file_id: str, user_uuid: str) -> Dict[str, Any]:
    row = owned(file_id, user_uuid, include_deleted=False)
    if row["lifetime"] != "kept":
        db.update_file(file_id, lifetime="kept", kept_at=db.now_iso())
        _announce(user_uuid, "kept", [file_id], row.get("session_id"))
    return db.get_file(file_id)


def rename(file_id: str, user_uuid: str, name: str) -> Dict[str, Any]:
    row = owned(file_id, user_uuid, include_deleted=False)
    new = safe_filename(name, row["filename"])
    old_ext = ext_of(row["filename"])
    if old_ext and ext_of(new) != old_ext:
        new = f"{new}.{old_ext}"
    db.update_file(file_id, filename=new, media_type=media_type_for(new, row.get("media_type")))
    _announce(user_uuid, "renamed", [file_id])
    return db.get_file(file_id)


def soft_delete(file_id: str, user_uuid: str) -> Dict[str, Any]:
    row = owned(file_id, user_uuid, include_deleted=False)
    days = fs_settings.retention_days()
    if days == 0:
        purge(row)
        _announce(user_uuid, "deleted", [file_id])
        return {**row, "purged": True}
    db.update_file(file_id, deleted_at=db.now_iso(), purge_after=db.purge_after_from(days))
    _announce(user_uuid, "deleted", [file_id])
    return db.get_file(file_id)


def restore(file_id: str, user_uuid: str) -> Dict[str, Any]:
    row = owned(file_id, user_uuid)
    if row.get("deleted_at"):
        db.update_file(file_id, deleted_at=None, purge_after=None)
        _announce(user_uuid, "restored", [file_id])
    return db.get_file(file_id)


def purge(row: Dict[str, Any], forget: bool = True) -> None:
    """Delete the object and the row (with its wrapped key). An encrypted file is unreadable
    afterwards even from a store backup — the key is gone. ``forget`` also removes the text
    read from it at upload from every conversation's manifest (``session_files.forget_files``);
    a batch purge passes False and forgets once per user."""
    try:
        _cfg, backend = backend_for_store(row["store_config_id"])
        backend.delete(row["object_key"])
    except Exception as e:  # noqa: BLE001 — the row (and key) go regardless
        logger.warning(f"[files] object {row.get('object_key')} could not be deleted from {row.get('store_config_id')}: {e}")
    db.delete_file_row(row["id"])
    if forget:
        _forget({row.get("user_uuid"): [row["id"]]})


def _forget(by_user: Dict[str, List[str]]) -> None:
    from . import session_files
    for user_uuid, ids in by_user.items():
        if user_uuid and ids:
            session_files.forget_files(user_uuid, ids)


def _purge_all(rows) -> int:
    """Purge ``rows`` and forget them from the manifests once per user (not once per file)."""
    by_user: Dict[str, List[str]] = {}
    n = 0
    for row in rows:
        purge(row, forget=False)
        by_user.setdefault(row.get("user_uuid"), []).append(row["id"])
        n += 1
    _forget(by_user)
    return n


def purge_due() -> int:
    return _purge_all(db.files_due_for_purge())


def purge_conversation(user_uuid: str, session_id: str) -> int:
    """A conversation was deleted: its unkept attachments go now, exactly when the session's
    uploads folder always went. Kept files stay."""
    n = _purge_all(db.query_files(user_uuid, session_id=session_id, lifetime="conversation")
                   + db.query_files(user_uuid, session_id=session_id, lifetime="conversation", deleted=True))
    if n:
        _announce(user_uuid, "deleted", None, session_id)
    return n


def purge_user(user_uuid: str) -> int:
    return _purge_all(db.all_files_for_user(user_uuid))


def mark_seen(file_id: str, user_uuid: str) -> None:
    row = db.get_file(file_id, user_uuid)
    if row and not row.get("seen_at"):
        db.update_file(file_id, seen_at=db.now_iso())


# ── describe ─────────────────────────────────────────────────────────────────

def _evidence_summary(bom: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(bom, dict):
        return None
    parts = [p for p in [bom.get("agent"), bom.get("deployment")] + list(bom.get("parts") or []) if isinstance(p, dict)]
    if not parts:
        return None
    unsigned = [p for p in parts if (p.get("state") or "") not in ("trusted",)]
    n = len(parts)
    if not unsigned:
        return f"{n} parts · all signed when answered"
    return f"{n} parts · {len(unsigned)} not signed when answered"


def _json_or_none(text: Optional[str]) -> Optional[Any]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def agents_of(user_uuid: str) -> Dict[str, Dict[str, Any]]:
    """The user's agents by id — ``{id: {tag, profile_type}}``."""
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        return {p["id"]: {"tag": p.get("tag"), "profile_type": p.get("profile_type")}
                for p in (get_config_manager().get_profiles(user_uuid) or []) if p.get("id")}
    except Exception:  # noqa: BLE001
        return {}


def agent_label(row: Dict[str, Any], agents: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[Optional[str], Optional[str]]:
    """``(tag, profile_type)`` of the agent that placed the file — as the agent is NOW (a renamed
    tag reads renamed), else as it was when the file was stored (an agent since deleted). THE
    one reading every file surface takes: a file whose row held only the agent's id once read
    "No agent" while its agent existed."""
    pid = row.get("profile_id")
    if pid:
        live = (agents if agents is not None else agents_of(row.get("user_uuid") or "")).get(pid)
        if live and live.get("tag"):
            return live.get("tag"), live.get("profile_type")
    return row.get("profile_tag"), row.get("profile_type")


def describe(row: Dict[str, Any], store_names: Optional[Dict[str, Dict[str, Any]]] = None,
             agents: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    e = ext_of(row["filename"])
    a_tag, a_type = agent_label(row, agents)
    store = (store_names or {}).get(row["store_config_id"]) or {}
    attachable = e in attachable_exts()
    bom = None
    if row.get("bom_json"):
        try:
            bom = json.loads(row["bom_json"])
        except Exception:  # noqa: BLE001
            bom = None
    model = None
    if isinstance(bom, dict):
        summ = ((bom.get("deployment") or {}).get("summary") or {})
        if summ.get("model"):
            model = summ.get("model")
    return {
        "id": row["id"], "filename": row["filename"], "media_type": row.get("media_type"),
        "size_bytes": row.get("size_bytes") or 0, "size_label": _size_label(row.get("size_bytes") or 0),
        "category": category(row), "type_label": TYPE_LABELS.get(e, (e.upper() or "File")), "ext": e,
        "source_kind": row.get("source_kind"), "origin_kind": row.get("origin_kind"),
        "lifetime": row.get("lifetime"), "kept_at": row.get("kept_at"),
        "profile_id": row.get("profile_id"), "profile_tag": a_tag,
        "profile_type": a_type, "model": model,
        "session_id": row.get("session_id"), "session_title": row.get("session_title"),
        "turn_id": row.get("turn_id"), "question": row.get("question"),
        "scheduled_task_id": row.get("scheduled_task_id"), "flow_id": row.get("flow_id"),
        "flow_run_id": row.get("flow_run_id"), "flow_node_id": row.get("flow_node_id"),
        "component_id": row.get("component_id"), "instance_id": row.get("instance_id"),
        "source": _json_or_none(row.get("source_json")),
        "source_anchor": row.get("source_anchor"), "pair_id": row.get("pair_id"),
        "data_derived": bool(row.get("data_derived")), "encrypted": bool(row.get("wrapped_key")),
        "store": {"id": row["store_config_id"], "name": store.get("name") or row["store_config_id"],
                  "backend_type": store.get("backend_type")},
        "is_new": not row.get("seen_at") and row.get("origin_kind") == "scheduled_run",
        "created_at": row.get("created_at"), "deleted_at": row.get("deleted_at"), "purge_after": row.get("purge_after"),
        "evidence": _evidence_summary(bom), "has_bom": bom is not None,
        "formats": formats.formats_for(row["filename"], row.get("media_type")),
        "attachable": attachable,
        "attach_note": None if attachable else f"A turn can't read .{e or 'this type of'} files, so this file can't be attached. "
                                               "Download it" + (", or open it in Canvas." if e in CANVAS_EXT else "."),
        "can_canvas": e in CANVAS_EXT, "can_knowledge": e in KNOWLEDGE_EXT,
        "can_preview": e in TEXT_EXT or e in IMAGE_EXT or e in {"pdf", "docx", "xlsx"},
    }


def store_index() -> Dict[str, Dict[str, Any]]:
    return {s["id"]: s for s in db.list_store_configs()}


# ── list / search ────────────────────────────────────────────────────────────

CATEGORIES = ("table", "document", "image", "export", "upload")


def list_files(user_uuid: str, *, q: str = "", kind: str = "all", origin: Optional[str] = None,
               profile_id: Optional[str] = None, session_id: Optional[str] = None,
               scheduled_task_id: Optional[str] = None, since: Optional[str] = None,
               only_new: bool = False, sort: str = "newest", limit: int = 200, offset: int = 0,
               flow_id: Optional[str] = None, component_id: Optional[str] = None) -> Dict[str, Any]:
    live = db.query_files(user_uuid)
    gone = db.query_files(user_uuid, deleted=True)
    ql = (q or "").strip().lower()
    agents = agents_of(user_uuid)

    def match(r):
        if ql:
            tag, ptype = agent_label(r, agents)
            hay = " ".join(str(v or "") for v in (r.get("filename"), r.get("session_title"), r.get("question"), tag, ptype))
            hay += " " + {"tool_enabled": "optimize", "llm_only": "ideate", "rag_focused": "focus", "genie": "coordinate"}.get(ptype or "", "")
            if ql not in hay.lower():
                return False
        if origin and r.get("origin_kind") != origin:
            return False
        if profile_id and r.get("profile_id") != profile_id:
            return False
        if session_id and r.get("session_id") != session_id:
            return False
        if scheduled_task_id and r.get("scheduled_task_id") != scheduled_task_id:
            return False
        if flow_id and r.get("flow_id") != flow_id:
            return False
        if component_id and r.get("component_id") != component_id:
            return False
        if since and (r.get("created_at") or "") < since:
            return False
        if only_new and (r.get("seen_at") or r.get("origin_kind") != "scheduled_run"):
            return False
        return True

    live_m = [r for r in live if match(r)]
    gone_m = [r for r in gone if match(r)]
    facets = {"all": len(live_m), "deleted": len(gone_m)}
    for c in CATEGORIES:
        facets[c] = sum(1 for r in live_m if category(r) == c)
    rows = gone_m if kind == "deleted" else [r for r in live_m if kind in ("all", "", None) or category(r) == kind]
    if sort == "name":
        rows.sort(key=lambda r: (r.get("filename") or "").lower())
    elif sort == "size":
        rows.sort(key=lambda r: -(r.get("size_bytes") or 0))
    stores = store_index()
    total = len(rows)
    page = rows[offset:offset + max(1, min(limit, 500))]
    used = db.user_bytes(user_uuid)
    quota = fs_settings.quota_bytes_for_user(user_uuid, user_tier(user_uuid))
    new_count = sum(1 for r in live if r.get("origin_kind") == "scheduled_run" and not r.get("seen_at"))
    described = [describe(r, stores, agents) for r in page]
    names = _task_names({d["scheduled_task_id"] for d in described if d.get("scheduled_task_id")})
    for d in described:
        if d.get("scheduled_task_id"):
            d["scheduled_task_name"] = names.get(d["scheduled_task_id"])
    return {"files": described, "total": total, "facets": facets,
            "storage": {"used_bytes": used, "quota_bytes": quota, "used_label": _size_label(used),
                        "quota_label": _size_label(quota) if quota else None,
                        "fraction": (used / quota) if quota else None,
                        "warn": bool(quota) and used / quota >= fs_settings.capacity_warn_fraction()},
            "file_count": len(live), "new_count": new_count}


def _task_names(ids: set) -> Dict[str, str]:
    """Scheduled task names for the series headers — read once per listing."""
    if not ids:
        return {}
    try:
        from trusted_data_agent.core import sqlite_util
        conn = sqlite_util.connect(fs_settings._db_path())
        try:
            q = f"SELECT id, name FROM scheduled_tasks WHERE id IN ({','.join('?' for _ in ids)})"
            return {r[0]: r[1] for r in conn.execute(q, tuple(ids)).fetchall()}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — no scheduler table on this instance
        return {}


def kept_anchors(user_uuid: str, session_id: str, turn_id: Optional[int] = None) -> List[Dict[str, Any]]:
    rows = db.query_files(user_uuid, session_id=session_id)
    out = []
    for r in rows:
        if turn_id is not None and r.get("turn_id") != turn_id:
            continue
        if r.get("source_anchor") or r.get("source_kind") == "upload":
            out.append({"id": r["id"], "turn_id": r.get("turn_id"), "anchor": r.get("source_anchor"),
                        "filename": r["filename"], "lifetime": r.get("lifetime"), "source_kind": r.get("source_kind"),
                        "pair_id": r.get("pair_id")})
    return out


def find_duplicate(user_uuid: str, data: bytes) -> Optional[Dict[str, Any]]:
    h = crypto.fingerprint(data)
    for r in db.query_files(user_uuid, extra_where="content_hmac=? AND lifetime='kept'", params=(h,)):
        return r
    return None


# ── provenance ───────────────────────────────────────────────────────────────

async def provenance(row: Dict[str, Any], user_uuid: str) -> Dict[str, Any]:
    from trusted_data_agent.core import session_manager
    session_exists = False
    session_title = None   # the conversation's name now — the row keeps the name it had at save time
    if row.get("session_id"):
        try:
            sess = await session_manager.get_session(user_uuid, row["session_id"])
            session_exists = bool(sess)
            session_title = (sess or {}).get("name") or None
        except Exception:  # noqa: BLE001
            session_exists = False
    uses = []
    titles: Dict[str, Optional[str]] = {}
    for u in db.uses_of(row["id"]):
        sid = u["session_id"]
        if sid not in titles:
            try:
                s = await session_manager.get_session(user_uuid, sid)
                titles[sid] = (s or {}).get("name") if s else None
            except Exception:  # noqa: BLE001
                titles[sid] = None
        uses.append({"session_id": sid, "turn_id": u.get("turn_id"), "session_title": titles[sid],
                     "exists": titles[sid] is not None, "used_at": u.get("used_at")})
    bom = None
    if row.get("bom_json"):
        try:
            bom = json.loads(row["bom_json"])
        except Exception:  # noqa: BLE001
            pass
    elif session_exists and row.get("turn_id") is not None:
        # a file saved DURING its turn (the agent's TDA_Files save) has no bill of materials
        # yet — the turn writes it at its end; copy it on the first read after that
        bom = await _backfill_bom(row, user_uuid)
    return {"session_exists": session_exists, "session_title": session_title, "uses": uses, "bom": bom,
            "evidence": _evidence_summary(bom),
            "evidence_live": session_exists and row.get("turn_id") is not None}


async def _backfill_bom(row: Dict[str, Any], user_uuid: str) -> Optional[Dict[str, Any]]:
    try:
        from trusted_data_agent.core import session_manager
        session = await session_manager.get_session(user_uuid, row["session_id"])
        turn = _find_turn(session, int(row["turn_id"])) if session else None
        bom = (turn or {}).get("bom")
        if isinstance(bom, dict):
            db.update_file(row["id"], bom_json=json.dumps(bom, ensure_ascii=False),
                           data_derived=int(bool(row.get("data_derived")) or _turn_read_data(turn)))
            return bom
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[files] bill of materials not backfilled: {e}")
    return None


def record_use(file_id: str, user_uuid: str, session_id: str, turn_id: Optional[int]) -> None:
    """A turn (or a flow) read this file — its "Used in" grows, announced live like any other
    change, so an open Files panel shows the use without a reload."""
    try:
        db.record_use(file_id, user_uuid, session_id, turn_id)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[files] use not recorded: {e}")
        return
    _announce(user_uuid, "used", [file_id], session_id)


# ── download (the one governed exit) ─────────────────────────────────────────

def check_download_policy(row: Dict[str, Any], user_uuid: str) -> None:
    pol = fs_settings.download_policy(user_tier(user_uuid))
    if pol == "disabled":
        raise FilesError("Downloads are off for your tier. You can still use the file in a conversation. "
                         "An admin can change this in Administration → Components → Files → Policy.",
                         403, "download_disabled")
    if pol == "allowed_except_data_derived" and row.get("data_derived"):
        raise FilesError("Downloads of files that came from a data source are off for your tier. You can still ask "
                         "about this file in a conversation. An admin can change this in Administration → "
                         "Components → Files → Policy.", 403, "download_data_derived")


def convert(row: Dict[str, Any], data: bytes, fmt: str) -> Tuple[bytes, str, str]:
    name = row["filename"]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    e = ext_of(name)
    if fmt in ("", None, "original") or fmt == e or (fmt == "md" and e in ("md", "markdown")):
        if e in ("csv", "tsv") and fmt != "original":
            return formats.csv_excel_safe(data), name, MEDIA.get(e, "text/csv")
        return data, name, row.get("media_type") or media_type_for(name)
    if fmt == "csv" and e in ("csv", "tsv"):
        return formats.csv_excel_safe(data), f"{stem}.csv", "text/csv"
    if fmt == "xlsx" and e in ("csv", "tsv"):
        if not formats.xlsx_available():
            raise FilesError("XLSX is not available on this deployment (openpyxl is not installed); download the CSV.", 400, "format_unavailable")
        return formats.csv_to_xlsx(data, stem), f"{stem}.xlsx", MEDIA["xlsx"]
    if fmt in ("docx", "pdf") and e in ("md", "markdown", "txt"):
        text = data.decode("utf-8", errors="replace")
        if fmt == "docx":
            return formats.markdown_to_docx(text, stem), f"{stem}.docx", MEDIA["docx"]
        return formats.markdown_to_pdf(text, stem), f"{stem}.pdf", MEDIA["pdf"]
    raise FilesError(f"{fmt.upper()} is not offered for {TYPE_LABELS.get(e, e or 'this file')}", 400, "format_unavailable")


async def evidence_bundle_text(row: Dict[str, Any], user_uuid: str) -> Tuple[Optional[str], Optional[str]]:
    """``(bundle_json, note)``. The live PROP-009 bundle when the conversation exists; else the
    stored bill of materials, labelled as such (it is a record, not a verifiable bundle)."""
    if row.get("session_id") and row.get("turn_id") is not None:
        try:
            import asyncio
            from trusted_data_agent.core import session_manager
            from trusted_data_agent.core.trust import evidence_bundle as _bundle
            from trusted_data_agent.core.trust import evidence_index as _index
            from trusted_data_agent.core.trust import evidence_view as _view
            sess = await session_manager.get_session(user_uuid, row["session_id"])
            turn = _view.find_turn(sess, row["turn_id"]) if sess else None
            if turn is not None:
                labels = await asyncio.to_thread(_index.labels, user_uuid)
                out = await asyncio.to_thread(_bundle.build, user_uuid, turns=[(row["session_id"], turn)], artifacts=[],
                                              content="limit", labels=labels, title=row["filename"], include_receipt=True)
                return json.dumps(out, ensure_ascii=False, indent=1), None
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[files] live evidence bundle unavailable: {e}")
    if row.get("bom_json"):
        return row["bom_json"], ("The conversation this file came from no longer exists, so this is the record of what the "
                                 "answer was made from, stored when the file was kept. It is not a verifiable bundle.")
    return None, None


async def download(row: Dict[str, Any], user_uuid: str, fmt: Optional[str] = None,
                   with_evidence: bool = False) -> Tuple[bytes, str, str]:
    """Authorize, decrypt, convert, record. ``(bytes, filename, media_type)``."""
    if row.get("user_uuid") != user_uuid:
        raise FilesError("File not found", 404, "not_found")
    if row.get("deleted_at"):
        raise FilesError("This file is in Recently deleted. Restore it to download it.", 409, "deleted")
    check_download_policy(row, user_uuid)
    data = read_bytes(row)
    out, name, media = convert(row, data, fmt or "")
    if with_evidence:
        bundle, note = await evidence_bundle_text(row, user_uuid)
        if bundle is None:
            raise FilesError("This file has no evidence to include — it was not kept from an answer.", 400, "no_evidence")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name, out)
            if note:
                z.writestr("evidence_record.json", bundle)
                z.writestr("README.txt", note + "\n")
            else:
                z.writestr("evidence.json", bundle)
                z.writestr("README.txt",
                           f"{name} and the evidence of the answer it was kept from (PROP-009).\n\n"
                           "Verify evidence.json offline with scripts/verify_evidence.py, or open it in the\n"
                           "platform's in-browser verifier. It is byte-for-byte the bundle the answer's\n"
                           "'Export receipt' produces.\n")
        stem = name.rsplit(".", 1)[0]
        out, name, media = buf.getvalue(), f"{stem}_with_evidence.zip", "application/zip"
    _audit(user_uuid, row, fmt or "original", with_evidence, len(out))
    if row.get("origin_kind") == "scheduled_run":
        mark_seen(row["id"], user_uuid)
    return out, name, media


def _audit(user_uuid: str, row: Dict[str, Any], fmt: str, with_evidence: bool, size: int) -> None:
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        log_audit_event(user_uuid, "file_download", f"Downloaded {row['filename']} as {fmt}"
                        + (" with evidence" if with_evidence else ""), resource=f"file:{row['id']}",
                        metadata={"file_id": row["id"], "format": fmt, "with_evidence": with_evidence, "bytes": size,
                                  "store": row.get("store_config_id"), "session_id": row.get("session_id"),
                                  "turn_id": row.get("turn_id"), "data_derived": bool(row.get("data_derived"))})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] download audit failed: {e}")


async def download_many(ids: List[str], user_uuid: str) -> Tuple[bytes, List[Dict[str, str]]]:
    """A zip of several files, each authorized and audited on its own; refusals are named."""
    buf = io.BytesIO()
    skipped: List[Dict[str, str]] = []
    used_names: set = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fid in ids[:200]:
            try:
                row = owned(fid, user_uuid, include_deleted=False)
                data, name, _m = await download(row, user_uuid, None)
            except FilesError as e:
                skipped.append({"id": fid, "reason": e.message})
                continue
            base, n = name, 2
            while name in used_names:
                stem, _, ext = base.rpartition(".")
                name = f"{stem} ({n}).{ext}" if stem else f"{base} ({n})"
                n += 1
            used_names.add(name)
            z.writestr(name, data)
        if skipped:
            z.writestr("NOT_INCLUDED.txt", "\n".join(f"{s['id']}: {s['reason']}" for s in skipped) + "\n")
    return buf.getvalue(), skipped


# ── preview (safe, bounded — never the raw file inline) ──────────────────────

PREVIEW_ROWS = 200
PREVIEW_TEXT = 256 * 1024


def preview(row: Dict[str, Any]) -> Dict[str, Any]:
    e = ext_of(row["filename"])
    try:
        data = read_bytes(row)
    except FilesError as err:
        return {"kind": "unavailable", "message": err.message, "code": err.code}
    if e in ("csv", "tsv"):
        header, rows = formats.read_csv(data)
        return {"kind": "table", "columns": header, "rows": rows[:PREVIEW_ROWS], "total_rows": len(rows)}
    if e == "xlsx":
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            grid = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
            return {"kind": "table", "columns": grid[0] if grid else [], "rows": grid[1:PREVIEW_ROWS + 1],
                    "total_rows": max(0, len(grid) - 1)}
        except Exception:  # noqa: BLE001
            return {"kind": "none"}
    if e in ("md", "markdown"):
        return {"kind": "markdown", "text": data[:PREVIEW_TEXT].decode("utf-8", errors="replace"),
                "truncated": len(data) > PREVIEW_TEXT}
    if e in IMAGE_EXT:
        import base64
        if e == "svg":
            return {"kind": "image", "media_type": "image/svg+xml",
                    "data_url": "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")}
        return {"kind": "image", "media_type": MEDIA.get(e, "image/png"),
                "data_url": f"data:{MEDIA.get(e, 'image/png')};base64," + base64.b64encode(data).decode("ascii")}
    if e == "pdf":
        try:
            from PyPDF2 import PdfReader
            r = PdfReader(io.BytesIO(data))
            text = "\n".join((p.extract_text() or "") for p in r.pages[:5])
            return {"kind": "document", "pages": len(r.pages), "text": text[:PREVIEW_TEXT]}
        except Exception:  # noqa: BLE001
            return {"kind": "none"}
    if e == "docx":
        try:
            from docx import Document
            d = Document(io.BytesIO(data))
            return {"kind": "document", "text": "\n".join(p.text for p in d.paragraphs)[:PREVIEW_TEXT]}
        except Exception:  # noqa: BLE001
            return {"kind": "none"}
    if e in TEXT_EXT:
        return {"kind": "code", "language": e, "text": data[:PREVIEW_TEXT].decode("utf-8", errors="replace"),
                "truncated": len(data) > PREVIEW_TEXT}
    return {"kind": "none"}


# ── keep from a turn (server-side: the file is built from the persisted record) ─────

def _turn_read_data(turn: Dict[str, Any]) -> bool:
    tools = [t if isinstance(t, str) else (t or {}).get("name", "") for t in (turn.get("tools_used") or [])]
    data_tools = {"TDA_DataProduct", "TDA_Compute", "TDA_DatabaseNavigator", "TDA_Action"}
    if any(t and (not t.startswith("TDA_") or t in data_tools) for t in tools):
        return True
    return bool(turn.get("knowledge_accessed") or turn.get("obda") or turn.get("compute_lineage"))


def _find_turn(session: Dict[str, Any], turn_id: int) -> Optional[Dict[str, Any]]:
    wh = ((session or {}).get("last_turn_data") or {}).get("workflow_history") or []
    for t in wh:
        try:
            if int(t.get("turn")) == int(turn_id):
                return t
        except (TypeError, ValueError):
            continue
    return None


def _pick_table(html: str, anchor: Optional[str], headers: Optional[List[str]]):
    tables = formats.tables_in_html(html)
    if headers:
        want = [" ".join(str(h).split()) for h in headers]
        for t in tables:
            if t["header"] == want:
                return t
    m = re.match(r"^table:(\d+)$", anchor or "")
    if m and int(m.group(1)) < len(tables):
        return tables[int(m.group(1))]
    return None


async def keep_from_turn(user_uuid: str, *, session_id: str, turn_id: int, kind: str,
                         anchor: Optional[str] = None, headers: Optional[List[str]] = None,
                         client_bytes: Optional[bytes] = None, client_filename: Optional[str] = None,
                         client_media_type: Optional[str] = None, allow_duplicate: bool = False,
                         title: Optional[str] = None, origin_kind: str = "turn",
                         extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Keep part of an answer. ``kind``: ``answer`` (Markdown of the whole answer), ``table``
    (CSV of a table, built from the persisted answer), ``chart`` (the browser's PNG + the CSV of
    the chart's own data, as one pair), ``component`` (a component's export, supplied by its
    renderer). Raises ``FilesError(409, 'duplicate')`` with the existing file unless
    ``allow_duplicate``."""
    from trusted_data_agent.core import session_manager
    session = await session_manager.get_session(user_uuid, session_id)
    if not session:
        raise FilesError("Conversation not found", 404, "not_found")
    turn = _find_turn(session, turn_id)
    if not turn:
        raise FilesError("That answer is not in this conversation", 404, "not_found")
    question = (turn.get("user_query") or "").strip()
    base = slug(title or question or session.get("name") or "answer")
    prov = {
        "session_id": session_id, "turn_id": int(turn_id), "question": question[:500] or None,
        "session_title": session.get("name"), "profile_tag": turn.get("profile_tag"),
        "profile_type": _profile_type_of(turn), "bom_json": turn.get("bom"), "bom_hash": None,
        "data_derived": _turn_read_data(turn),
    }
    prov.update({k: v for k, v in (extra or {}).items() if k in db.FILE_COLUMNS})
    profile_id = (turn.get("bom") or {}).get("profile_id") if isinstance(turn.get("bom"), dict) else None
    profile_id = profile_id or session.get("profile_id")
    html = turn.get("final_summary_html") or ""
    made: List[Tuple[bytes, str, str, str]] = []   # (data, filename, source_kind, anchor)

    if kind == "answer":
        text = (turn.get("final_summary_text") or "").strip()
        if not text:
            raise FilesError("This answer has no text to keep", 400, "empty")
        made.append((text.encode("utf-8"), f"{base}.md", "answer", "answer"))
    elif kind == "table":
        t = _pick_table(html, anchor, headers)
        if not t or not t["header"]:
            raise FilesError("That table is not in the stored answer", 404, "not_found")
        name = slug(title) if title else slug("_".join(t["header"][:3]) or base)
        made.append((formats.rows_to_csv(t["header"], t["rows"]), f"{name}.csv", "table", anchor or "table:0"))
    elif kind == "chart":
        charts = formats.charts_in_html(html)
        m = re.match(r"^chart:(\d+)$", anchor or "")
        spec = charts[int(m.group(1))] if (m and int(m.group(1)) < len(charts)) else (charts[0] if charts else None)
        header, rows = formats.chart_rows(spec) if spec else ([], [])
        name = slug(title) if title else base
        if client_bytes:
            made.append((client_bytes, f"{name}.png", "chart", anchor or "chart:0"))
        if header:
            made.append((formats.rows_to_csv(header, rows), f"{name}.csv", "chart", anchor or "chart:0"))
        if not made:
            raise FilesError("That chart is not in the stored answer", 404, "not_found")
    elif kind == "component":
        if not client_bytes or not client_filename:
            raise FilesError("A component export needs its file", 400, "empty")
        made.append((client_bytes, client_filename, "component", anchor or "component"))
    else:
        raise FilesError(f"Unknown kind '{kind}'", 400, "bad_kind")

    if not allow_duplicate:
        for data, _n, _s, _a in made:
            dup = find_duplicate(user_uuid, data)
            if dup:
                raise FilesError("Already kept", 409, "duplicate", {"file": describe(dup, store_index())})

    pair = str(uuid.uuid4()) if len(made) > 1 else None
    out = []
    for data, filename, skind, anch in made:
        out.append(put_file(user_uuid=user_uuid, data=data, filename=filename, source_kind=skind,
                            profile_id=profile_id, source_anchor=f"{turn_id}:{anch}", pair_id=pair,
                            origin_kind=origin_kind, announce=False, **prov))
    _announce(user_uuid, "kept", [r["id"] for r in out], session_id)
    stores = store_index()
    return {"files": [describe(r, stores) for r in out]}


def _profile_type_of(turn: Dict[str, Any]) -> Optional[str]:
    pt = turn.get("profile_type")
    mapping = {"conversation_with_tools": "llm_only", "conversation": "llm_only", "llm_only": "llm_only",
               "rag_focused": "rag_focused", "genie": "genie", "tool_enabled": "tool_enabled"}
    return mapping.get(pt, pt)


async def add_direct(user_uuid: str, filename: str, data: bytes, profile_id: Optional[str]) -> Dict[str, Any]:
    """+ Add in the Files panel: a local file kept directly (same limits as chat uploads)."""
    e = ext_of(filename)
    if "." + e not in {"." + x for x in attachable_exts()} | {".csv", ".tsv", ".json", ".xlsx", ".zip"}:
        raise FilesError(f"Files of type .{e or '?'} cannot be added", 400, "unsupported")
    row = put_file(user_uuid=user_uuid, data=data, filename=filename, source_kind="upload",
                   origin_kind="upload", profile_id=profile_id, lifetime="kept")
    return describe(row, store_index())


# ── saved exports (PROP-010 phase 3) ─────────────────────────────────────────

# What an export may be saved as. HTML and SVG are left out: they would be the one stored
# type a viewer renders as a page, and every export site already has a safer format.
EXPORT_EXT = (TEXT_EXT | IMAGE_EXT | {"pdf", "docx", "xlsx", "zip", "txt"}) - {"html", "svg"}


async def save_export(user_uuid: str, data: bytes, filename: str, *, component_id: Optional[str] = None,
                      instance_id: Optional[str] = None, session_id: Optional[str] = None,
                      turn_id: Optional[int] = None, profile_id: Optional[str] = None,
                      source: Optional[Dict[str, Any]] = None, data_derived: Optional[bool] = None,
                      allow_duplicate: bool = False, source_kind: str = "export") -> Dict[str, Any]:
    """Save to Files — the ONE way an export reaches a store. A turn's export carries the turn's
    provenance (``keep_from_turn``); a component's export carries the component and its instance;
    a conversation-level export (session JSON, provenance) carries the conversation, and is
    marked data-derived when any of its turns read a data source. Answers ``409 duplicate``."""
    if not data:
        raise FilesError("The export is empty", 400, "empty")
    e = ext_of(filename)
    if e not in EXPORT_EXT:
        raise FilesError(f"An export of type .{e or '?'} cannot be saved to Files", 400, "unsupported")
    extra = {"component_id": component_id, "instance_id": instance_id,
             "source_json": json.dumps(source, ensure_ascii=False) if source else None}
    if session_id and turn_id is not None:
        out = await keep_from_turn(user_uuid, session_id=session_id, turn_id=int(turn_id), kind="component",
                                   client_bytes=data, client_filename=filename, allow_duplicate=allow_duplicate,
                                   extra={k: v for k, v in extra.items() if v is not None})
        return out["files"][0]
    if not allow_duplicate:
        dup = find_duplicate(user_uuid, data)
        if dup:
            raise FilesError("Already kept", 409, "duplicate", {"file": describe(dup, store_index())})
    meta: Dict[str, Any] = {k: v for k, v in extra.items() if v is not None}
    if session_id:
        from trusted_data_agent.core import session_manager
        session = await session_manager.get_session(user_uuid, session_id)
        if not session:
            raise FilesError("Conversation not found", 404, "not_found")
        turns = ((session.get("last_turn_data") or {}).get("workflow_history") or [])
        meta.update({"session_id": session_id, "session_title": session.get("name")})
        profile_id = profile_id or session.get("profile_id")
        if data_derived is None:
            data_derived = any(_turn_read_data(t) for t in turns if isinstance(t, dict))
    row = put_file(user_uuid=user_uuid, data=data, filename=filename, source_kind=source_kind,
                   origin_kind="export", profile_id=profile_id, data_derived=bool(data_derived), **meta)
    return describe(row, store_index())


# ── the agent's own file acts (TDA_Files, PROP-010 phase 4) ───────────────────

AGENT_SAVE_EXT = (TEXT_EXT - {"html", "css", "js", "ts"}) | {"md"}
AGENT_READ_ROWS = 200            # rows handed to the model; the whole table goes to the turn's result sets
AGENT_READ_TEXT = 20_000         # characters handed to the model


async def agent_save(user_uuid: str, *, filename: str, session_id: Optional[str], turn_id: Optional[int],
                     profile_id: Optional[str], question: Optional[str] = None, content: Optional[str] = None,
                     statement: Any = None) -> Tuple[Dict[str, Any], bool]:
    """The agent keeps something it produced in this turn. ``content``: text the model wrote (a
    report, a script); ``statement``: a data statement of THIS turn (the result-set registry's
    number, or "latest"), kept as CSV from the rows the platform holds — the model never retypes
    them. Returns ``(file, already)``; a duplicate returns the existing file, ``already=True``."""
    from trusted_data_agent.agent import result_sets
    name = safe_filename(filename or "")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    source = None
    data_derived = False
    if statement not in (None, ""):
        st = result_sets.get(session_id, statement) if session_id else None
        if not st:
            raise FilesError("There is no such query result in this turn"
                             + ("" if result_sets.enabled() else " — the turn's result sets are off on this deployment")
                             + ". Save the content instead, or run the query first.", 404, "no_statement")
        cols = [str(c) for c in (st.get("columns") or [])]
        rows = [[("" if (r or {}).get(c) is None else str(r.get(c))) for c in cols] for r in (st.get("rows") or [])]
        data = formats.rows_to_csv(cols, rows)
        name = f"{slug(stem or st.get('tool') or 'result', 80)}.csv"
        source = {"statement": st.get("n"), "tool": st.get("tool"), "statement_text": (st.get("statement") or "")[:1000],
                  "row_count": st.get("row_count"), "rows_kept": len(rows), "truncated": bool(st.get("truncated"))}
        data_derived = True
    else:
        if not (content or "").strip():
            raise FilesError("Nothing to save: give 'content', or 'statement' to keep a query result", 400, "empty")
        e = ext_of(name) or "md"
        if e not in AGENT_SAVE_EXT:
            raise FilesError(f"The agent can save text files (Markdown, CSV, JSON, SQL, text …), not .{e}", 400, "unsupported")
        name = f"{slug(stem or 'note', 80)}.{e}"
        data = content.encode("utf-8")
    dup = find_duplicate(user_uuid, data)
    if dup and not dup.get("deleted_at"):
        return describe(dup, store_index()), True
    meta: Dict[str, Any] = {"question": (question or "")[:500] or None, "source_json": source}
    if session_id:
        from trusted_data_agent.core import session_manager
        session = await session_manager.get_session(user_uuid, session_id)
        if session:
            meta["session_id"] = session_id
            meta["session_title"] = session.get("name")
            if turn_id is not None:
                meta["turn_id"] = int(turn_id)
    anchor = f"{turn_id}:agent:{slug(stem, 40)}" if turn_id is not None else None
    row = put_file(user_uuid=user_uuid, data=data, filename=name, source_kind="agent", origin_kind="turn",
                   profile_id=profile_id, data_derived=data_derived, source_anchor=anchor,
                   **{k: v for k, v in meta.items() if v is not None})
    return describe(row, store_index()), False


def find_for_agent(user_uuid: str, file_id: Optional[str] = None, name: Optional[str] = None) -> Dict[str, Any]:
    """The kept file the agent named — by id, else by exact then partial file name (newest)."""
    if file_id:
        return owned(file_id, user_uuid, include_deleted=False)
    want = (name or "").strip().lower()
    if not want:
        raise FilesError("Name the file: 'file_id' from 'list', or its 'name'", 400, "no_file")
    live = db.query_files(user_uuid)
    exact = [r for r in live if (r.get("filename") or "").lower() == want]
    part = exact or [r for r in live if want in (r.get("filename") or "").lower()]
    if not part:
        raise FilesError(f"No kept file is called '{name}'. Call 'list' to see the files.", 404, "not_found")
    if len(part) > 1 and not exact:
        names = ", ".join(r["filename"] for r in part[:6])
        raise FilesError(f"Several files match '{name}' ({names}). Pass the file_id from 'list'.", 409, "ambiguous")
    return part[0]


def agent_read(row: Dict[str, Any]) -> Dict[str, Any]:
    """A kept file as the model reads it: a table's columns and first rows (and all of it for
    the turn's result sets), a document's text. Bounded; truncation said."""
    e = ext_of(row["filename"])
    data = read_bytes(row)
    if e in ("csv", "tsv", "xlsx"):
        header, rows = formats.read_table(data, e)
        recs = [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header)} for r in rows]
        return {"kind": "table", "columns": header, "row_count": len(rows), "rows": recs[:AGENT_READ_ROWS],
                "all_rows": recs, "truncated": len(rows) > AGENT_READ_ROWS}
    if e in ("pdf", "docx"):
        from trusted_data_agent.llm.document_upload import DocumentUploadHandler
        text = DocumentUploadHandler().extract_text_from_bytes(data, row["filename"]).get("content", "")
    elif e in TEXT_EXT:
        text = data.decode("utf-8", errors="replace")
    else:
        raise FilesError(f"A {TYPE_LABELS.get(e, e or 'binary')} file can't be read as text. Ask the user to attach "
                         "it to the conversation instead.", 400, "not_readable")
    return {"kind": "text", "text": text[:AGENT_READ_TEXT], "chars": len(text), "truncated": len(text) > AGENT_READ_TEXT}


TILE_KIND_WORDS = {"chart": "Chart", "table": "Table", "kpi": "KPI", "narrative": "Narrative",
                   "knowledge_graph": "Knowledge graph", "flow": "Flow", "explain_plan": "Explain plan",
                   "action": "Action"}


def snapshot_dashboard(user_uuid: str, dashboard_id: str, images: Dict[str, bytes]) -> Dict[str, Any]:
    """*Save snapshot to Files* — the dashboard as one PDF. The tiles, their order, titles,
    refresh times and agents are read from the dashboard itself; only the pictures of drawn
    tiles (charts, graphs) come from the browser, because only the browser draws them. A tile
    without a picture is written from its stored table or text. The snapshot records the
    dashboard and each tile's refresh time (``source_json``)."""
    from datetime import datetime, timezone
    import sqlite3
    from trusted_data_agent.auth.database import DATABASE_URL
    from trusted_data_agent.core import sqlite_util
    conn = sqlite_util.connect(DATABASE_URL.replace("sqlite:///", ""))   # the dashboards' own database
    try:
        conn.row_factory = sqlite3.Row
        dash = conn.execute("SELECT * FROM dashboards WHERE id=? AND user_id=?", (dashboard_id, user_uuid)).fetchone()
        if not dash:
            raise FilesError("Dashboard not found", 404, "not_found")
        tiles = conn.execute("SELECT * FROM dashboard_tiles WHERE dashboard_id=? ORDER BY sort_order ASC, created_at ASC",
                             (dashboard_id,)).fetchall()
    finally:
        conn.close()
    if not tiles:
        raise FilesError("This dashboard has no tiles yet", 400, "empty")
    now = datetime.now(timezone.utc)
    entries, record = [], []
    for t in tiles:
        kind = t["component_id"] or "chart"
        html = t["html"] or ""
        tables = formats.tables_in_html(html) if html else []
        entries.append({
            "title": t["title"], "kind": TILE_KIND_WORDS.get(kind, kind.replace("_", " ").title()),
            "refreshed": (t["last_refreshed"] or "").replace("T", " ")[:16] or None,
            "profile_tag": t["profile_tag"] or None, "image": images.get(t["id"]),
            "table": tables[0] if tables else None,
            "text": formats.html_to_text(html) if html and html.lstrip().startswith("<") else (html or None),
        })
        record.append({"tile_id": t["id"], "title": t["title"], "kind": kind, "last_refreshed": t["last_refreshed"],
                       "refresh_status": t["refresh_status"], "profile_tag": t["profile_tag"] or None,
                       "source_session_id": t["source_session_id"] or None,
                       "captured_as": "picture" if images.get(t["id"]) else ("table" if tables else "text")})
    taken = now.strftime("%Y-%m-%d %H:%M UTC")
    pdf = formats.dashboard_snapshot_pdf(dash["name"], taken, entries)
    profiles = {t["profile_id"] for t in tiles if t["profile_id"]}
    data_derived = any((t["component_id"] in ("chart", "table", "kpi")) or t["mcp_tool_name"] for t in tiles)
    row = put_file(user_uuid=user_uuid, data=pdf, filename=f"{slug(dash['name'])}_snapshot_{now.strftime('%Y%m%d_%H%M')}.pdf",
                   source_kind="snapshot", origin_kind="export", component_id="dashboard", instance_id=dashboard_id,
                   # a dashboard spans agents: its snapshot is placed by the one agent all tiles share, else the default
                   profile_id=next(iter(profiles)) if len(profiles) == 1 else None,
                   source_json={"dashboard_id": dashboard_id, "dashboard_name": dash["name"], "taken_at": now.isoformat(),
                                "tiles": record},
                   data_derived=data_derived, session_title=dash["name"])
    return describe(row, store_index())


def record_promotion(row: Dict[str, Any], user_uuid: str, collection_id: int, result: Dict[str, Any]) -> None:
    """A kept document added to a knowledge repository — recorded in the audit trail."""
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        log_audit_event(user_uuid, "file_to_knowledge", f"Added {row['filename']} to knowledge repository {collection_id}",
                        resource=f"file:{row['id']}",
                        metadata={"file_id": row["id"], "collection_id": collection_id,
                                  "document_id": result.get("document_id"), "status": result.get("status"),
                                  "chunks": result.get("chunks_stored")})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] knowledge promotion audit failed: {e}")


def govern_export(user_uuid: str, *, what: str, filename: str, size: int,
                  data_derived: bool = False, resource: Optional[str] = None) -> None:
    """A server-built export (a knowledge graph, a metric, a component instance, a conversation's
    provenance) leaves the platform under the SAME rule as a stored file: the tier's download
    policy decides, and the export is recorded as ``file_download``. Raises ``FilesError``."""
    check_download_policy({"data_derived": data_derived}, user_uuid)
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        log_audit_event(user_uuid, "file_download", f"Exported {filename} ({what})", resource=resource or f"export:{what}",
                        metadata={"export": what, "filename": filename, "bytes": size,
                                  "data_derived": bool(data_derived), "server_built": True})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] export audit failed: {e}")


def record_browser_export(user_uuid: str, *, what: str, filename: str, size: int,
                          session_id: Optional[str] = None) -> None:
    """A browser-built export cannot be governed (its data is already in the browser); it is
    recorded, so the audit trail names what left and when (PROP-010 §8)."""
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        log_audit_event(user_uuid, "file_download", f"Downloaded {filename} ({what})", resource=f"export:{what}",
                        metadata={"export": what, "filename": filename, "bytes": size,
                                  "session_id": session_id, "browser_built": True})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] browser export audit failed: {e}")


# ── rotation and migration ───────────────────────────────────────────────────

def rewrap_all(old_master: str, new_master: str) -> Tuple[int, int]:
    """Rewrap every data key from ``old_master`` to ``new_master``. ``(rewrapped, failed)``."""
    ok = bad = 0
    for r in db.all_wrapped_files():
        try:
            db.update_file(r["id"], wrapped_key=crypto.rewrap(r["wrapped_key"], r["id"], old_master, new_master))
            ok += 1
        except Exception:  # noqa: BLE001
            bad += 1
    return ok, bad


def key_usage() -> List[Dict[str, Any]]:
    """How many files each key wraps (the platform key, or a key manager's key)."""
    from . import kms
    return kms.usage(db.all_wrapped_files())


def rewrap_to_active(admin_uuid: Optional[str] = None, dry_run: bool = True) -> Dict[str, Any]:
    """Re-wrap every data key that is not wrapped by the ACTIVE key encryption — after the
    admin moved to an external key manager, another key, or back to the platform key. Each
    key is unwrapped with the key it names and wrapped with the active one; no file object
    is rewritten. A key that cannot be unwrapped is named and left as it is."""
    from . import kms
    active = kms.active_provider()
    cfg = kms.saved_configs().get(active) or {}
    field = kms.PROVIDERS[active]["key_field"]
    target = (active, ((cfg.get(field) or "").strip() or None) if field else None)
    todo = [r for r in db.all_wrapped_files() if kms.describe_wrapping(r["wrapped_key"]) != target]
    if dry_run:
        return {"files": len(todo), "to": kms.PROVIDERS[active]["label"], "key": target[1]}
    ok, failed = 0, []
    for r in todo:
        try:
            new = crypto.wrap_key(crypto.unwrap_key(r["wrapped_key"], r["id"]), r["id"])
            db.update_file(r["id"], wrapped_key=new, key_version=crypto.key_version_of(new))
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed.append({"file_id": r["id"], "reason": str(e)[:200]})
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        log_audit_event(admin_uuid, "file_keys_rewrapped",
                        f"Re-wrapped {ok} file key(s) under {kms.PROVIDERS[active]['label']}"
                        + (f"; {len(failed)} could not be unwrapped" if failed else ""),
                        resource="file_store:keys", metadata={"to": active, "key": target[1], "rewrapped": ok,
                                                              "failed": len(failed)})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] re-wrap audit failed: {e}")
    return {"rewrapped": ok, "failed": failed, "to": kms.PROVIDERS[active]["label"], "key": target[1]}


def migration_plan(from_store: str, to_store: str) -> Dict[str, Any]:
    rows = db.files_in_store(from_store)
    return {"from": from_store, "to": to_store, "files": len(rows), "bytes": sum(r.get("size_bytes") or 0 for r in rows),
            "bytes_label": _size_label(sum(r.get("size_bytes") or 0 for r in rows))}


def migrate(from_store: str, to_store: str, admin_uuid: Optional[str]) -> Dict[str, Any]:
    if from_store == to_store:
        raise FilesError("Choose two different stores", 400, "same_store")
    _src_cfg, src = backend_for_store(from_store)
    _dst_cfg, dst = backend_for_store(to_store)
    moved = failed = 0
    errors: List[str] = []
    for r in db.files_in_store(from_store):
        try:
            blob = src.get(r["object_key"])
            dst.put(r["object_key"], blob)
            if dst.get(r["object_key"]) != blob:
                raise FileStoreError("read-back mismatch")
            db.update_file(r["id"], store_config_id=to_store)
            src.delete(r["object_key"])
            moved += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            if len(errors) < 5:
                errors.append(f"{r['filename']}: {e}")
    try:
        from trusted_data_agent.auth.audit import log_audit_event
        log_audit_event(admin_uuid, "file_store_migrate", f"Moved {moved} files from {from_store} to {to_store}",
                        success=failed == 0, resource=f"file_store:{to_store}",
                        metadata={"from": from_store, "to": to_store, "moved": moved, "failed": failed})
    except Exception:  # noqa: BLE001
        pass
    return {"moved": moved, "failed": failed, "errors": errors}


# ── capacity governance (what is provisioned, what is promised, what is used) ─

def store_capacity_bytes(cfg: Dict[str, Any]) -> int:
    """What the admin provisions for Files in this store; 0 = no limit set."""
    mb = cfg.get("capacity_mb")
    try:
        return int(mb) * 1024 * 1024 if mb else 0
    except (TypeError, ValueError):
        return 0


def _disk(cfg: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Physical size and free space of a folder store's disk; None for object stores."""
    if cfg.get("backend_type") != "local":
        return None
    try:
        import shutil
        from .factory import build_backend
        root = build_backend("local", cfg.get("config") or {}).root
        u = shutil.disk_usage(root)
        return {"total": int(u.total), "free": int(u.free), "used": int(u.used)}
    except Exception:  # noqa: BLE001
        return None


def _users_by_tier() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {"user": [], "developer": [], "admin": []}
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User
        with get_db_session() as sess:
            for u in sess.query(User).all():
                if getattr(u, "is_active", True) is False:
                    continue
                out.setdefault(u.profile_tier or "user", []).append(u.id)
    except Exception:  # noqa: BLE001
        pass
    return out


def _usernames(uuids: List[str]) -> Dict[str, str]:
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User
        with get_db_session() as sess:
            return {u.id: u.username for u in sess.query(User).filter(User.id.in_(list(uuids))).all()}
    except Exception:  # noqa: BLE001
        return {}


def capacity_report() -> Dict[str, Any]:
    """The admin's view of space: per store what is provisioned, held and physically free;
    across consumers what is promised (quotas) against what is provisioned; who uses most."""
    usage = db.store_usage()
    warn = fs_settings.capacity_warn_fraction()
    stores = []
    provisioned_total = 0
    unlimited_store = False
    for s in db.list_store_configs():
        cap = store_capacity_bytes(s)
        held = (usage.get(s["id"]) or {}).get("bytes", 0)
        disk = _disk(s)
        effective = cap or (disk["free"] + held if disk else 0)
        if cap:
            provisioned_total += cap
        else:
            unlimited_store = True
        frac = (held / effective) if effective else None
        stores.append({
            "id": s["id"], "name": s["name"], "backend_type": s["backend_type"],
            "provisioned_bytes": cap or None, "provisioned_label": _size_label(cap) if cap else None,
            "held_bytes": held, "held_label": _size_label(held), "files": (usage.get(s["id"]) or {}).get("files", 0),
            "disk_total_label": _size_label(disk["total"]) if disk else None,
            "disk_free_bytes": disk["free"] if disk else None, "disk_free_label": _size_label(disk["free"]) if disk else None,
            "fraction": frac, "state": ("full" if frac is not None and frac >= 1 else
                                        "warn" if frac is not None and frac >= warn else "ok"),
            # a provisioned figure larger than the disk behind it is a promise the disk cannot keep
            "over_disk": bool(cap and disk and cap > disk["free"] + held),
        })
    users = _users_by_tier()
    overrides = fs_settings.user_quota_overrides()
    promised = 0
    unlimited_users = 0
    tiers = []
    for tier in ("user", "developer", "admin"):
        ids = users.get(tier) or []
        q = fs_settings.quota_bytes(tier)
        n_over = sum(1 for u in ids if u in overrides)
        tier_promised = 0
        for u in ids:
            uq = overrides[u] * 1024 * 1024 if u in overrides else q
            if uq:
                tier_promised += uq
            else:
                unlimited_users += 1
        promised += tier_promised
        tiers.append({"tier": tier, "users": len(ids), "quota_bytes": q or None, "quota_label": _size_label(q) if q else "No limit",
                      "overrides": n_over, "promised_bytes": tier_promised, "promised_label": _size_label(tier_promised)})
    top = db.usage_by_user(10)
    names = _usernames([t["user_uuid"] for t in top])
    used_total = db.total_bytes()
    return {
        "stores": stores,
        "provisioned_bytes": provisioned_total or None,
        "provisioned_label": _size_label(provisioned_total) if provisioned_total else None,
        "has_unprovisioned_store": unlimited_store,
        "used_bytes": used_total, "used_label": _size_label(used_total),
        "promised_bytes": promised, "promised_label": _size_label(promised),
        "unlimited_users": unlimited_users,
        "overcommit": (promised / provisioned_total) if provisioned_total else None,
        "tiers": tiers,
        "overrides": [{"user_uuid": u, "username": None, "quota_mb": mb} for u, mb in overrides.items()],
        "top_users": [{**t, "username": names.get(t["user_uuid"]), "label": _size_label(t["bytes"])} for t in top],
        "warn_percent": int(round(warn * 100)),
    }


# ── scheduled runs land in Files (use case 4) ─────────────────────────────────

async def keep_scheduled_run(task: Dict[str, Any], session_id: str, run_id: Optional[str]) -> List[Dict[str, Any]]:
    """A scheduled task's run, kept: the answer as Markdown and every table it showed as CSV,
    named after the task and the run's date, grouped as one series (``scheduled_task_id``).
    Never raises; returns the kept files' descriptions (empty on any failure)."""
    try:
        from datetime import datetime, timezone
        from trusted_data_agent.core import session_manager
        user_uuid = task.get("user_uuid")
        session = await session_manager.get_session(user_uuid, session_id) if user_uuid and session_id else None
        wh = ((session or {}).get("last_turn_data") or {}).get("workflow_history") or []
        if not wh:
            return []
        turn = wh[-1]
        turn_id = int(turn.get("turn"))
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        base = f"{slug(task.get('name') or 'scheduled_task')}_{day}"
        extra = {"scheduled_task_id": task.get("id"), "scheduled_run_id": run_id}
        kept: List[Dict[str, Any]] = []
        if (turn.get("final_summary_text") or "").strip():
            kept += (await keep_from_turn(user_uuid, session_id=session_id, turn_id=turn_id, kind="answer",
                                          allow_duplicate=True, title=base, origin_kind="scheduled_run",
                                          extra=extra))["files"]
        for i, t in enumerate(formats.tables_in_html(turn.get("final_summary_html") or "")):
            if not t.get("header"):
                continue
            kept += (await keep_from_turn(user_uuid, session_id=session_id, turn_id=turn_id, kind="table",
                                          anchor=f"table:{i}", allow_duplicate=True,
                                          title=f"{base}_{slug('_'.join(t['header'][:3]), 32)}",
                                          origin_kind="scheduled_run", extra=extra))["files"]
        return kept
    except Exception as e:  # noqa: BLE001 — delivery must never fail the run
        logger.warning(f"[files] scheduled run of '{task.get('name')}' could not be kept: {getattr(e, 'message', e)}")
        return []
