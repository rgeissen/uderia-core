"""
Google Cloud Storage backend — one bucket.

``google-cloud-storage`` is an optional dependency: without it the backend constructs, and
``test_connection`` says so instead of raising (the S3 pattern). Credentials: a service
account key (its JSON, stored sealed with the store's settings), else the host's application
default credentials. No signed URLs are offered: a signed URL would be a download that
bypasses the platform's download route and could not be decrypted by the client anyway
(PROP-010 §4.2).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Optional, Set

from .base import FileStoreBackend, FileStoreCapability, FileStoreError, ObjectNotFound, check_key


class GCSFileStoreBackend(FileStoreBackend):
    backend_type = "gcs"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.bucket_name = (self.config.get("bucket") or "").strip()
        self.prefix = (self.config.get("prefix") or "").strip().strip("/")
        self._bucket = None

    def capabilities(self) -> Set[FileStoreCapability]:
        # Google encrypts every object at rest; the platform's own envelope encryption sits on top
        return {FileStoreCapability.PUT, FileStoreCapability.GET, FileStoreCapability.DELETE,
                FileStoreCapability.EXISTS, FileStoreCapability.SERVER_SIDE_ENCRYPTION}

    def _b(self):
        if self._bucket is None:
            try:
                from google.cloud import storage  # type: ignore
            except ImportError as e:
                raise FileStoreError("google-cloud-storage is not installed (pip install google-cloud-storage)") from e
            if not self.bucket_name:
                raise FileStoreError("no bucket configured")
            project = (self.config.get("project") or "").strip() or None
            raw = (self.config.get("credentials_json") or "").strip()
            if raw:
                try:
                    info = json.loads(raw)
                except ValueError as e:
                    raise FileStoreError("the service account key is not valid JSON") from e
                client = storage.Client.from_service_account_info(info, project=project or info.get("project_id"))
            else:
                client = storage.Client(project=project)
            self._bucket = client.bucket(self.bucket_name)
        return self._bucket

    def _key(self, object_key: str) -> str:
        k = check_key(object_key)
        return f"{self.prefix}/{k}" if self.prefix else k

    @staticmethod
    def _not_found(e: Exception) -> bool:
        return type(e).__name__ == "NotFound" or getattr(e, "code", None) == 404

    def put(self, object_key: str, data: bytes) -> None:
        try:
            self._b().blob(self._key(object_key)).upload_from_string(data, content_type="application/octet-stream")
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FileStoreError(f"could not write to bucket {self.bucket_name}: {e}") from e

    def get(self, object_key: str) -> bytes:
        try:
            return self._b().blob(self._key(object_key)).download_as_bytes()
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            if self._not_found(e):
                raise ObjectNotFound(object_key) from e
            raise FileStoreError(f"could not read from bucket {self.bucket_name}: {e}") from e

    def delete(self, object_key: str) -> None:
        try:
            self._b().blob(self._key(object_key)).delete()
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            if self._not_found(e):
                return
            raise FileStoreError(f"could not delete from bucket {self.bucket_name}: {e}") from e

    def exists(self, object_key: str) -> bool:
        try:
            return bool(self._b().blob(self._key(object_key)).exists())
        except FileStoreError:
            raise
        except Exception:  # noqa: BLE001
            return False

    def test_connection(self) -> Dict[str, Any]:
        t0 = time.monotonic()
        key = f"_probe/{uuid.uuid4().hex}"
        payload = os.urandom(1024)
        try:
            self.put(key, payload)
            ok = self.get(key) == payload
            self.delete(key)
            ms = int((time.monotonic() - t0) * 1000)
            if not ok:
                return {"success": False, "message": "the probe read back differently than it was written"}
            return {"success": True, "message": f"Wrote, read back and deleted a 1 KB probe in {ms} ms in bucket {self.bucket_name}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": str(e)}
