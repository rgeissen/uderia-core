"""
Azure Blob Storage backend — one container of an Azure storage account.

``azure-storage-blob`` is an optional dependency: without it the backend constructs, and
``test_connection`` says so instead of raising (the S3 pattern). Credentials, first match:
a connection string; an account URL with an account key or a SAS token; an account URL alone,
which uses the host's managed identity (``azure-identity``'s DefaultAzureCredential). No
SAS links are offered for files: a link would be a download that bypasses the platform's
download route and could not be decrypted by the client anyway (PROP-010 §4.2).
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional, Set

from .base import FileStoreBackend, FileStoreCapability, FileStoreError, ObjectNotFound, check_key


class AzureBlobFileStoreBackend(FileStoreBackend):
    backend_type = "azure_blob"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.container = (self.config.get("container") or "").strip()
        self.prefix = (self.config.get("prefix") or "").strip().strip("/")
        self._container_client = None

    def capabilities(self) -> Set[FileStoreCapability]:
        # Azure encrypts every blob at rest; the platform's own envelope encryption sits on top
        return {FileStoreCapability.PUT, FileStoreCapability.GET, FileStoreCapability.DELETE,
                FileStoreCapability.EXISTS, FileStoreCapability.SERVER_SIDE_ENCRYPTION}

    def _cfg(self, key: str) -> str:
        v = self.config.get(key)
        return v.strip() if isinstance(v, str) else ""

    def _c(self):
        if self._container_client is None:
            try:
                from azure.storage.blob import BlobServiceClient  # type: ignore
            except ImportError as e:
                raise FileStoreError("azure-storage-blob is not installed (pip install azure-storage-blob)") from e
            if not self.container:
                raise FileStoreError("no container configured")
            if self._cfg("connection_string"):
                service = BlobServiceClient.from_connection_string(self._cfg("connection_string"))
            elif self._cfg("account_url"):
                credential = self._cfg("account_key") or self._cfg("sas_token") or None
                if credential is None:
                    try:
                        from azure.identity import DefaultAzureCredential  # type: ignore
                    except ImportError as e:
                        raise FileStoreError("give an account key or SAS token, or install azure-identity to use "
                                             "the host's managed identity") from e
                    credential = DefaultAzureCredential()
                service = BlobServiceClient(account_url=self._cfg("account_url"), credential=credential)
            else:
                raise FileStoreError("give a connection string or an account URL")
            self._container_client = service.get_container_client(self.container)
        return self._container_client

    def _key(self, object_key: str) -> str:
        k = check_key(object_key)
        return f"{self.prefix}/{k}" if self.prefix else k

    @staticmethod
    def _not_found(e: Exception) -> bool:
        return type(e).__name__ == "ResourceNotFoundError" or getattr(e, "status_code", None) == 404

    def put(self, object_key: str, data: bytes) -> None:
        try:
            self._c().upload_blob(self._key(object_key), data, overwrite=True)
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FileStoreError(f"could not write to container {self.container}: {e}") from e

    def get(self, object_key: str) -> bytes:
        try:
            return self._c().download_blob(self._key(object_key)).readall()
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            if self._not_found(e):
                raise ObjectNotFound(object_key) from e
            raise FileStoreError(f"could not read from container {self.container}: {e}") from e

    def delete(self, object_key: str) -> None:
        try:
            self._c().delete_blob(self._key(object_key))
        except FileStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            if self._not_found(e):
                return
            raise FileStoreError(f"could not delete from container {self.container}: {e}") from e

    def exists(self, object_key: str) -> bool:
        try:
            return bool(self._c().get_blob_client(self._key(object_key)).exists())
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
            return {"success": True, "message": f"Wrote, read back and deleted a 1 KB probe in {ms} ms in container {self.container}"}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "message": str(e)}
