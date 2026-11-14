"""
Local filesystem backend: platform local (``tda_files/``) and any admin-mounted path, which
is how a network filesystem is used. Writes are atomic (temporary file, then rename).
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .base import FileStoreBackend, FileStoreCapability, FileStoreError, ObjectNotFound, check_key


def default_root() -> Path:
    env = os.environ.get("TDA_FILES_DIR", "").strip()
    if env:
        return Path(env)
    from trusted_data_agent.core.utils import get_project_root
    return get_project_root() / "tda_files"


class LocalFileStoreBackend(FileStoreBackend):
    backend_type = "local"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        root = (self.config.get("root") or "").strip()
        self.root = Path(root).expanduser() if root else default_root()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def capabilities(self) -> Set[FileStoreCapability]:
        return {FileStoreCapability.PUT, FileStoreCapability.GET, FileStoreCapability.DELETE,
                FileStoreCapability.EXISTS}

    def _path(self, object_key: str) -> Path:
        owner, oid = check_key(object_key).split("/")
        p = (self.root / owner / oid).resolve()
        if self.root.resolve() not in p.parents:
            raise FileStoreError("object key escapes the store root")
        return p

    def put(self, object_key: str, data: bytes) -> None:
        p = self._path(object_key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise FileStoreError(f"could not write to {self.root}: {e.strerror or e}") from e

    def get(self, object_key: str) -> bytes:
        p = self._path(object_key)
        try:
            return p.read_bytes()
        except FileNotFoundError as e:
            raise ObjectNotFound(object_key) from e
        except OSError as e:
            raise FileStoreError(f"could not read from {self.root}: {e.strerror or e}") from e

    def delete(self, object_key: str) -> None:
        p = self._path(object_key)
        try:
            p.unlink()
        except FileNotFoundError:
            return
        except OSError as e:
            raise FileStoreError(f"could not delete from {self.root}: {e.strerror or e}") from e
        try:
            p.parent.rmdir()          # tidy an owner folder that is now empty
        except OSError:
            pass

    def exists(self, object_key: str) -> bool:
        return self._path(object_key).exists()

    def test_connection(self) -> Dict[str, Any]:
        t0 = time.monotonic()
        key = f"_probe/{uuid.uuid4().hex}"
        payload = os.urandom(1024)
        try:
            self.initialize()
            self.put(key, payload)
            ok = self.get(key) == payload
            self.delete(key)
            ms = int((time.monotonic() - t0) * 1000)
            if not ok:
                return {"success": False, "message": "the probe read back differently than it was written"}
            return {"success": True, "message": f"Wrote, read back and deleted a 1 KB probe in {ms} ms at {self.root}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": str(e)}
