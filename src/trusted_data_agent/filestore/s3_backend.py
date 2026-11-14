"""
S3-compatible backend: AWS S3, MinIO and most on-premise object stores.

``boto3`` is an optional dependency: without it the backend constructs, and
``test_connection`` says so instead of raising (the Neo4j pattern). Presigned URLs are
deliberately not offered — a presigned URL is a download that bypasses the platform's
download route and could not be decrypted by the client anyway (PROP-010 §4.2).
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional, Set

from .base import FileStoreBackend, FileStoreCapability, FileStoreError, ObjectNotFound, check_key


class S3FileStoreBackend(FileStoreBackend):
    backend_type = "s3"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.bucket = (self.config.get("bucket") or "").strip()
        self.prefix = (self.config.get("prefix") or "").strip().strip("/")
        self._client = None

    def capabilities(self) -> Set[FileStoreCapability]:
        caps = {FileStoreCapability.PUT, FileStoreCapability.GET, FileStoreCapability.DELETE,
                FileStoreCapability.EXISTS}
        if self.config.get("server_side_encryption"):
            caps.add(FileStoreCapability.SERVER_SIDE_ENCRYPTION)
        return caps

    def _c(self):
        if self._client is None:
            try:
                import boto3  # type: ignore
            except ImportError as e:
                raise FileStoreError("boto3 is not installed (pip install boto3)") from e
            kwargs = {}
            for src, dst in (("endpoint_url", "endpoint_url"), ("region", "region_name"),
                             ("access_key_id", "aws_access_key_id"), ("secret_access_key", "aws_secret_access_key")):
                v = (self.config.get(src) or "").strip() if isinstance(self.config.get(src), str) else self.config.get(src)
                if v:
                    kwargs[dst] = v
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key(self, object_key: str) -> str:
        k = check_key(object_key)
        return f"{self.prefix}/{k}" if self.prefix else k

    def put(self, object_key: str, data: bytes) -> None:
        if not self.bucket:
            raise FileStoreError("no bucket configured")
        extra = {"ServerSideEncryption": "AES256"} if self.config.get("server_side_encryption") else {}
        try:
            self._c().put_object(Bucket=self.bucket, Key=self._key(object_key), Body=data, **extra)
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FileStoreError(f"could not write to bucket {self.bucket}: {e}") from e

    def get(self, object_key: str) -> bytes:
        try:
            r = self._c().get_object(Bucket=self.bucket, Key=self._key(object_key))
            return r["Body"].read()
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
            if code in ("NoSuchKey", "404"):
                raise ObjectNotFound(object_key) from e
            raise FileStoreError(f"could not read from bucket {self.bucket}: {e}") from e

    def delete(self, object_key: str) -> None:
        try:
            self._c().delete_object(Bucket=self.bucket, Key=self._key(object_key))
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FileStoreError(f"could not delete from bucket {self.bucket}: {e}") from e

    def exists(self, object_key: str) -> bool:
        try:
            self._c().head_object(Bucket=self.bucket, Key=self._key(object_key))
            return True
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
            return {"success": True, "message": f"Wrote, read back and deleted a 1 KB probe in {ms} ms in bucket {self.bucket}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": str(e)}
