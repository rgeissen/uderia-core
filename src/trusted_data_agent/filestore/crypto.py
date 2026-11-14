"""
Envelope encryption for file-store objects (PROP-010 §4.3).

Every file gets its own random 256-bit data key. The content is encrypted with AES-256-GCM in
fixed-size chunks; each chunk's nonce is a per-file random prefix plus the chunk counter, and
its associated data binds the header, the counter and whether it is the last chunk, so a
reordered, dropped or truncated object fails to decrypt. The data key is wrapped by a
key-encryption key derived from the platform master key (``core.key_policy.master_key``) with
HKDF under a context of its own — it never shares a derivation with stored credentials.

Rotation rewraps data keys only (``rewrap``); no object is rewritten.

With an external key manager configured (``kms.py``), new data keys are wrapped by that
service instead; a wrapped key says which (``kms1.…``), and ``unwrap_key`` follows it. A key
wrapped externally does not depend on the master key, so master-key rotation leaves it alone.

The content fingerprint is a keyed HMAC of the plaintext, so provenance can name a file's
content while a store, holding only ciphertext, cannot tell when two files are identical.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
from typing import Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"UFS1"
KEY_VERSION = 1            # the platform wrap
KEY_VERSION_EXTERNAL = 2   # wrapped by an external key manager (kms.py)
CHUNK = 1024 * 1024
_HEADER = struct.Struct(">4sI8s")        # magic · chunk size · nonce prefix
_SALT = b"uderia-filestore"


class FileCryptoError(Exception):
    """An object or wrapped key that does not decrypt with this deployment's key."""


def _master(master_key: Optional[str] = None) -> bytes:
    if master_key is None:
        from trusted_data_agent.core.key_policy import master_key as _mk
        master_key = _mk()
    return master_key.encode("utf-8")


def _derive(info: bytes, master_key: Optional[str] = None) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_SALT, info=info).derive(_master(master_key))


def kek(master_key: Optional[str] = None) -> bytes:
    return _derive(b"uderia-filestore:v1:kek", master_key)


def fingerprint(data: bytes, master_key: Optional[str] = None) -> str:
    return hmac.new(_derive(b"uderia-filestore:v1:hmac", master_key), data, hashlib.sha256).hexdigest()


# ── objects ──────────────────────────────────────────────────────────────────

def encrypt_object(plaintext: bytes, file_id: str, master_key: Optional[str] = None) -> Tuple[bytes, str]:
    """``(ciphertext, wrapped_key)`` for one file."""
    dek = AESGCM.generate_key(bit_length=256)
    prefix = os.urandom(8)
    header = _HEADER.pack(MAGIC, CHUNK, prefix)
    aes = AESGCM(dek)
    out = [header]
    n = max(1, (len(plaintext) + CHUNK - 1) // CHUNK)
    for i in range(n):
        part = plaintext[i * CHUNK:(i + 1) * CHUNK]
        final = i == n - 1
        out.append(aes.encrypt(prefix + struct.pack(">I", i), part, header + struct.pack(">I?", i, final)))
    return b"".join(out), wrap_key(dek, file_id, master_key)


def decrypt_object(blob: bytes, wrapped_key: str, file_id: str, master_key: Optional[str] = None) -> bytes:
    if len(blob) < _HEADER.size:
        raise FileCryptoError("object too short")
    header = blob[:_HEADER.size]
    magic, chunk, prefix = _HEADER.unpack(header)
    if magic != MAGIC:
        raise FileCryptoError("not a file-store object")
    aes = AESGCM(unwrap_key(wrapped_key, file_id, master_key))
    body = blob[_HEADER.size:]
    step = chunk + 16
    n = max(1, (len(body) + step - 1) // step)
    parts = []
    try:
        for i in range(n):
            part = body[i * step:(i + 1) * step]
            parts.append(aes.decrypt(prefix + struct.pack(">I", i), part, header + struct.pack(">I?", i, i == n - 1)))
    except Exception as e:  # noqa: BLE001 — InvalidTag and friends
        raise FileCryptoError("object does not decrypt (tampered, truncated or another key)") from e
    return b"".join(parts)


# ── data keys ────────────────────────────────────────────────────────────────

def key_version_of(wrapped: Optional[str]) -> Optional[int]:
    if not wrapped:
        return None
    from . import kms
    return KEY_VERSION_EXTERNAL if kms.is_external(wrapped) else KEY_VERSION


def wrap_key(dek: bytes, file_id: str, master_key: Optional[str] = None) -> str:
    """Wrap a data key. At runtime (no ``master_key`` given) the ACTIVE key encryption decides:
    the platform key, or the external key manager the admin configured. An explicit
    ``master_key`` (rotation, migration tooling) always means the platform wrap."""
    if master_key is None:
        from . import kms
        if kms.active_provider() != kms.PLATFORM:
            try:
                return kms.wrap(dek, file_id)
            except kms.KeyManagerError as e:
                raise FileCryptoError(f"the key manager did not wrap the file's key: {e}") from e
    return _platform_wrap(dek, file_id, master_key)


def _platform_wrap(dek: bytes, file_id: str, master_key: Optional[str] = None) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(kek(master_key)).encrypt(nonce, dek, b"dek:" + file_id.encode("utf-8"))
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def unwrap_key(wrapped: str, file_id: str, master_key: Optional[str] = None) -> bytes:
    from . import kms
    if kms.is_external(wrapped):
        try:
            return kms.unwrap(wrapped, file_id)
        except kms.KeyManagerError as e:
            raise FileCryptoError(str(e)) from e
    try:
        raw = base64.urlsafe_b64decode(wrapped.encode("ascii"))
        return AESGCM(kek(master_key)).decrypt(raw[:12], raw[12:], b"dek:" + file_id.encode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise FileCryptoError("data key does not unwrap with this deployment's key") from e


def rewrap(wrapped: str, file_id: str, old_master: str, new_master: str) -> str:
    """Master-key rotation. A key held by an external key manager does not depend on the
    master key and is returned unchanged."""
    from . import kms
    if kms.is_external(wrapped):
        return wrapped
    return _platform_wrap(unwrap_key(wrapped, file_id, old_master), file_id, new_master)


# ── small records (session manifests, store settings) ────────────────────────

def seal_record(obj, context: str, master_key: Optional[str] = None) -> bytes:
    """A JSON-serialisable record, encrypted under a key derived for records and bound to
    ``context`` (e.g. the session it belongs to)."""
    nonce = os.urandom(12)
    key = _derive(b"uderia-filestore:v1:record", master_key)
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, body, context.encode("utf-8"))


def open_record(blob: bytes, context: str, master_key: Optional[str] = None):
    if not blob.startswith(MAGIC):
        raise FileCryptoError("not a sealed record")
    key = _derive(b"uderia-filestore:v1:record", master_key)
    try:
        body = AESGCM(key).decrypt(blob[4:16], blob[16:], context.encode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise FileCryptoError("record does not decrypt") from e
    return json.loads(body.decode("utf-8"))


def seal_text(text: str, context: str, master_key: Optional[str] = None) -> str:
    return base64.urlsafe_b64encode(seal_record(text, context, master_key)).decode("ascii")


def open_text(token: str, context: str, master_key: Optional[str] = None) -> str:
    return open_record(base64.urlsafe_b64decode(token.encode("ascii")), context, master_key)
