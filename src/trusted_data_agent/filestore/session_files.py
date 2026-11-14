"""
Chat attachments are files (PROP-010 §4.8) — the ONE accessor for a conversation's uploads.

Before this module, five places built ``tda_sessions/<user>/uploads/<session>/`` by hand and
opened plaintext files by path. Now:

- the bytes of every attachment live in the file store (the agent's store, encrypted),
  registered as a file with ``lifetime: conversation`` until the user keeps it;
- the session's manifest — its per-session index, which also holds the extracted text and
  the attachment-source record — is sealed (``manifest.enc``), bound to its user and session;
- readers get the manifest as a dict (same entry shape as before, minus ``path``) and bytes
  through ``read_bytes``; nobody receives a plaintext path.

Legacy uploads (a plaintext ``manifest.json`` + files) are still read until
``migrate_legacy`` has moved them — once, idempotently, at startup.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import crypto, db

logger = logging.getLogger("quart.app")

MANIFEST_SEALED = "manifest.enc"
MANIFEST_LEGACY = "manifest.json"


def _safe(part: str) -> str:
    return "".join(c for c in (part or "") if c.isalnum() or c in "-_")


def sessions_root() -> Path:
    from trusted_data_agent.core.session_backends.file_backend import DEFAULT_SESSIONS_DIR
    env = os.environ.get("TDA_SESSIONS_DIR", "").strip()
    return Path(env) if env else DEFAULT_SESSIONS_DIR


def upload_dir(user_uuid: str, session_id: str, create: bool = False) -> Path:
    d = sessions_root() / _safe(user_uuid) / "uploads" / _safe(session_id)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _context(user_uuid: str, session_id: str) -> str:
    return f"manifest:{_safe(user_uuid)}:{_safe(session_id)}"


def load_manifest(user_uuid: str, session_id: str) -> Optional[Dict[str, Any]]:
    """The session's manifest, or ``None`` when the conversation has no attachments. A sealed
    manifest that does not open is treated as absent (and logged) — never as an exception in a
    turn."""
    d = upload_dir(user_uuid, session_id)
    sealed = d / MANIFEST_SEALED
    rotating = d / f"{MANIFEST_SEALED}.rotating"
    if sealed.exists():
        try:
            return crypto.open_record(sealed.read_bytes(), _context(user_uuid, session_id))
        except Exception as e:  # noqa: BLE001
            # A key rotation that committed but crashed before swapping this manifest in
            # leaves the re-sealed copy beside it: it opens with the current key, so promote it.
            if rotating.exists():
                try:
                    rec = crypto.open_record(rotating.read_bytes(), _context(user_uuid, session_id))
                    os.replace(rotating, sealed)
                    return rec
                except Exception:  # noqa: BLE001
                    pass
            logger.error(f"[files] the attachment manifest of session {session_id} does not open: {e}")
            return None
    legacy = d / MANIFEST_LEGACY
    if legacy.exists():
        try:
            return json.loads(legacy.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to load upload manifest: {e}")
            return None
    return None


def save_manifest(user_uuid: str, session_id: str, manifest: Dict[str, Any]) -> None:
    d = upload_dir(user_uuid, session_id, create=True)
    tmp = d / f".{MANIFEST_SEALED}.{uuid.uuid4().hex}.tmp"
    tmp.write_bytes(crypto.seal_record(manifest, _context(user_uuid, session_id)))
    os.replace(tmp, d / MANIFEST_SEALED)


def _session_meta(user_uuid: str, session_id: str) -> Dict[str, Any]:
    """The session's title and agent, read from its stored file without the async manager."""
    try:
        p = sessions_root() / _safe(user_uuid) / f"{_safe(session_id)}.json"
        if p.exists():
            s = json.loads(p.read_text(encoding="utf-8"))
            return {"title": s.get("name"), "profile_id": s.get("profile_id"), "profile_tag": s.get("profile_tag")}
    except Exception:  # noqa: BLE001
        pass
    return {}


def store_upload(user_uuid: str, session_id: str, file_id: str, filename: str, content: bytes,
                 content_type: Optional[str], profile_id: Optional[str] = None,
                 profile_tag: Optional[str] = None) -> Dict[str, Any]:
    """Put one attachment's bytes into the file store as a conversation-lifetime file."""
    from . import service
    meta = _session_meta(user_uuid, session_id)
    profile_id = profile_id or meta.get("profile_id")
    profile = None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        profile = get_config_manager().get_profile(profile_id, user_uuid) if profile_id else None
    except Exception:  # noqa: BLE001
        profile = None
    return service.put_file(
        user_uuid=user_uuid, data=content, filename=filename, media_type=content_type,
        source_kind="upload", lifetime="conversation", origin_kind="upload", file_id=file_id,
        profile_id=profile_id, profile=profile, enforce_quota=False,
        session_id=session_id, session_title=meta.get("title"),
        profile_tag=profile_tag or (profile or {}).get("tag") or meta.get("profile_tag"),
        profile_type=(profile or {}).get("profile_type"))


def read_bytes(user_uuid: str, session_id: str, file_id: str,
               manifest: Optional[Dict[str, Any]] = None) -> Optional[bytes]:
    """One attachment's plaintext, or ``None`` (absent, deleted, unreadable) — owner/session
    scoped: the file must be the user's and listed in this session's manifest."""
    m = manifest if manifest is not None else load_manifest(user_uuid, session_id)
    entry = (m or {}).get(file_id)
    if not isinstance(entry, dict):
        return None
    ref = entry.get("file_ref")
    if ref:
        from . import service
        row = db.get_file(ref, user_uuid)
        if not row or row.get("deleted_at"):
            return None
        try:
            return service.read_bytes(row)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[files] attachment {file_id} could not be read: {getattr(e, 'message', e)}")
            return None
    path = entry.get("path")
    if path:  # a legacy upload not migrated yet
        d = upload_dir(user_uuid, session_id).resolve()
        p = Path(path)
        try:
            if p.exists() and d in p.resolve().parents:
                return p.read_bytes()
        except OSError:
            return None
    return None


def entry_state(user_uuid: str, entry: Any) -> Dict[str, Any]:
    """What an attachment is now: ``live``, ``deleted`` (in Recently deleted, restorable until
    ``purge_after``) or ``gone``. A legacy upload not yet migrated (a ``path``, no file) is live."""
    if not isinstance(entry, dict):
        return {"state": "gone"}
    ref = entry.get("file_ref")
    if not ref:
        return {"state": "live" if entry.get("path") else "gone"}
    row = db.get_file(ref, user_uuid)
    if not row:
        return {"state": "gone", "file_id": ref}
    if row.get("deleted_at"):
        return {"state": "deleted", "file_id": ref, "purge_after": row.get("purge_after"), "lifetime": row.get("lifetime")}
    return {"state": "live", "file_id": ref, "lifetime": row.get("lifetime"), "source_kind": row.get("source_kind")}


def readable_manifest(user_uuid: str, session_id: str) -> Optional[Dict[str, Any]]:
    """THE manifest a turn reads: the stored one without the attachments whose file was deleted
    from Files. The text read at upload lives in the manifest, so without this a deleted file's
    content still reached any turn that named it (a REST client can name any id)."""
    m = load_manifest(user_uuid, session_id)
    if m is None:
        return None
    return {k: v for k, v in m.items() if entry_state(user_uuid, v)["state"] == "live"}


def forget_files(user_uuid: str, file_ids) -> int:
    """Files were purged: remove their entries — and the text read from them — from every
    conversation's manifest that references them (the upload's own and any it was attached to
    from Files). Returns the number of manifests rewritten. Never raises."""
    ids = {i for i in (file_ids or []) if i}
    root = sessions_root() / _safe(user_uuid) / "uploads"
    if not ids or not root.is_dir():
        return 0
    n = 0
    for d in root.iterdir():
        try:
            if not d.is_dir() or not ((d / MANIFEST_SEALED).exists() or (d / MANIFEST_LEGACY).exists()):
                continue
            m = load_manifest(user_uuid, d.name)
            if not m:
                continue
            drop = [k for k, v in m.items() if k in ids or (isinstance(v, dict) and v.get("file_ref") in ids)]
            if drop:
                for k in drop:
                    del m[k]
                save_manifest(user_uuid, d.name, m)
                n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[files] a purged file's entry could not be removed from session {d.name}: {e}")
    return n


def entry_has_text(entry: Any) -> bool:
    """Whether an attachment carries text the model was given: a document's extracted text, or
    an image the platform read (S9, verified). An image's own manifest text is a placeholder."""
    if not isinstance(entry, dict) or not entry.get("extracted_text"):
        return False
    return not entry.get("is_image") or bool((entry.get("source") or {}).get("verified"))


# ── the turn's attachments as evidence for the scores (PROP-010) ─────────────
# A turn records WHICH attachments it read, never their text: the after-the-turn scorers read
# the text through the conversation's guarded manifest when they run, so a file deleted from
# Files stops being evidence exactly as it stops being readable — its text is not kept alive
# in the conversation's history.
_TURN_ATTACHMENTS: "contextvars.ContextVar[Optional[list]]" = contextvars.ContextVar(
    "uderia_turn_attachments", default=None)


def begin_turn(attachments) -> contextvars.Token:
    files = [{"file_id": a["file_id"], "filename": a.get("filename") or ""}
             for a in (attachments or []) if isinstance(a, dict) and a.get("file_id")]
    return _TURN_ATTACHMENTS.set(files)


def end_turn(token: Optional[contextvars.Token]) -> None:
    try:
        if token is not None:
            _TURN_ATTACHMENTS.reset(token)
    except Exception:  # noqa: BLE001
        pass


def stamp_for_turn() -> Optional[Dict[str, Any]]:
    """The persisted ``attachment_evidence`` stamp: the attachments the turn read. None when the
    turn had none (an absent stamp is the baseline)."""
    files = _TURN_ATTACHMENTS.get()
    return {"files": list(files)} if files else None


def with_attachment_evidence(user_uuid: str, session_id: str, turn: Dict[str, Any]) -> Dict[str, Any]:
    """The turn as a scorer should read it: its ``attachment_evidence`` filled with the text of
    each attachment that is still readable (``texts``) and the names of those that are not
    (``lost`` — deleted from Files since). The full extracted text — a superset of the excerpt
    the model was shown. Returns the turn unchanged when it read no attachments. Never raises."""
    ev = turn.get("attachment_evidence") if isinstance(turn, dict) else None
    files = (ev or {}).get("files") if isinstance(ev, dict) else None
    if not files:
        return turn
    texts, lost = [], []
    try:
        manifest = readable_manifest(user_uuid, session_id) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[files] attachment evidence unreadable for session {session_id}: {e}")
        manifest = {}
    for f in files:
        entry = manifest.get((f or {}).get("file_id"))
        if entry is None:
            lost.append((f or {}).get("filename") or (f or {}).get("file_id") or "")
        elif entry_has_text(entry):
            texts.append(f"--- Document: {entry.get('filename') or f.get('filename')} ---\n{entry['extracted_text']}")
    return {**turn, "attachment_evidence": {**ev, "texts": texts, "lost": lost}}


def delete_upload(user_uuid: str, session_id: str, file_id: str) -> bool:
    """Remove a pending attachment from the conversation. Its file goes too unless the user
    kept it (then only this conversation's reference is removed). False when absent."""
    m = load_manifest(user_uuid, session_id)
    if not m or file_id not in m:
        return False
    entry = m[file_id]
    ref = entry.get("file_ref") if isinstance(entry, dict) else None
    if ref:
        row = db.get_file(ref, user_uuid)
        if row and row.get("lifetime") == "conversation" and row.get("session_id") == session_id:
            from . import service
            service.purge(row)
            service._announce(user_uuid, "deleted", [ref], session_id)
    elif isinstance(entry, dict) and entry.get("path"):
        p = Path(entry["path"])
        if p.exists() and upload_dir(user_uuid, session_id).resolve() in p.resolve().parents:
            p.unlink()
    del m[file_id]
    save_manifest(user_uuid, session_id, m)
    return True


def purge_session(user_uuid: str, session_id: str) -> None:
    """The conversation was deleted (archived): its unkept attachments go now, its manifest and
    folder too. Kept files stay."""
    try:
        from . import service
        service.purge_conversation(user_uuid, session_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[files] attachments of session {session_id} could not be purged: {e}")
    d = upload_dir(user_uuid, session_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


# ── migration of plaintext uploads ───────────────────────────────────────────

def migrate_session(user_uuid: str, session_id: str) -> Tuple[int, int]:
    """Move one session's plaintext uploads into the store. ``(moved, failed)``. Idempotent:
    an entry that already has a ``file_ref`` is left alone; a sealed manifest is left alone."""
    d = upload_dir(user_uuid, session_id)
    legacy = d / MANIFEST_LEGACY
    if not legacy.exists():
        return 0, 0
    try:
        manifest = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.error(f"[files] legacy manifest of {session_id} is unreadable, left in place: {e}")
        return 0, 1
    moved = failed = 0
    plaintext = []
    for fid, entry in list(manifest.items()):
        if not isinstance(entry, dict) or entry.get("file_ref"):
            continue
        p = Path(entry.get("path") or "")
        if not p.exists() or d.resolve() not in p.resolve().parents:
            manifest.pop(fid, None)          # nothing to move: the reference is dead already
            continue
        try:
            existing = db.get_file(fid, user_uuid)
            if not existing:
                store_upload(user_uuid, session_id, fid, entry.get("filename") or p.name, p.read_bytes(),
                             entry.get("content_type"))
            entry.pop("path", None)
            entry.pop("stored_filename", None)
            entry["file_ref"] = fid
            plaintext.append(p)
            moved += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            logger.error(f"[files] upload {fid} of session {session_id} could not be migrated: {e}")
    if failed:
        return moved, failed          # keep the plaintext and the legacy manifest; retry next start
    save_manifest(user_uuid, session_id, manifest)
    for p in plaintext:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        legacy.unlink()
    except OSError:
        pass
    return moved, failed


def migrate_legacy() -> Dict[str, int]:
    """Every session's plaintext uploads → the file store. Run once at startup; idempotent."""
    root = sessions_root()
    totals = {"sessions": 0, "moved": 0, "failed": 0}
    if not root.exists():
        return totals
    for legacy in root.glob(f"*/uploads/*/{MANIFEST_LEGACY}"):
        user_uuid = legacy.parents[2].name
        session_id = legacy.parent.name
        if not re.fullmatch(r"[A-Za-z0-9_-]+", user_uuid) or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
            continue
        m, f = migrate_session(user_uuid, session_id)
        totals["sessions"] += 1
        totals["moved"] += m
        totals["failed"] += f
    if totals["sessions"]:
        logger.info(f"[files] migrated plaintext uploads: {totals}")
    return totals


def block_bytes(block: Dict[str, Any]) -> bytes:
    """The bytes a native multimodal block carries — the ONE way a provider adapter reads an
    attachment. Blocks built by ``executor.load_multimodal_document_content`` carry ``data``;
    a ``path`` is honoured only for a block built elsewhere."""
    data = block.get("data")
    if data is not None:
        return bytes(data)
    with open(block["path"], "rb") as f:
        return f.read()
