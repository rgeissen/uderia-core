"""
The file-store backend contract (PROP-010 §4.2).

A backend stores opaque objects under opaque keys. It never sees a filename, a type or a
user's words — those live in Uderia's database — and, when the store encrypts, it never sees
plaintext either. Mirrors ``kgstore/base.py``: one ABC, capability negotiation, a
``test_connection`` that never raises.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional, Set


class FileStoreCapability(str, Enum):
    PUT = "put"
    GET = "get"
    DELETE = "delete"
    EXISTS = "exists"
    STREAMING = "streaming"
    PRESIGNED_GET = "presigned_get"
    SERVER_SIDE_ENCRYPTION = "server_side_encryption"


REQUIRED = {FileStoreCapability.PUT, FileStoreCapability.GET, FileStoreCapability.DELETE, FileStoreCapability.EXISTS}

_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}/[A-Za-z0-9_-]{1,80}$")


class FileStoreError(Exception):
    """A store refused or failed an operation; the message is safe to show an admin."""


class ObjectNotFound(FileStoreError):
    pass


def check_key(object_key: str) -> str:
    """Object keys are ``<owner>/<id>`` from a closed alphabet — never a path a caller chose."""
    if not isinstance(object_key, str) or not _KEY_RE.match(object_key):
        raise FileStoreError("invalid object key")
    return object_key


class FileStoreBackend(ABC):
    backend_type: str = ""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = dict(config or {})

    def initialize(self) -> None:
        """Prepare the backend (create a folder, open a client). Must not dial a remote
        service; ``test_connection`` does that."""

    @abstractmethod
    def capabilities(self) -> Set[FileStoreCapability]:
        ...

    @abstractmethod
    def put(self, object_key: str, data: bytes) -> None:
        ...

    @abstractmethod
    def get(self, object_key: str) -> bytes:
        """The object's bytes; ``ObjectNotFound`` when absent."""

    @abstractmethod
    def delete(self, object_key: str) -> None:
        """Remove the object; absent is not an error."""

    @abstractmethod
    def exists(self, object_key: str) -> bool:
        ...

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """``{"success": bool, "message": str}`` — writes, reads back and deletes a probe.
        Never raises."""

    def assert_required_capabilities(self) -> None:
        missing = REQUIRED - set(self.capabilities())
        if missing:
            raise FileStoreError(f"backend {self.backend_type} lacks {sorted(m.value for m in missing)}")
