"""
The file-store backends, declared once (PROP-010 §4.2).

Every place that needs to know a backend reads this table: the factory (which class), the
store validator (which fields are required), the store records (which fields are secret and
masked), the admin's backend list (is its SDK installed) and the admin's Add-store form
(which fields to draw). Adding a backend is one row here and one class in
``<type>_backend.py`` registered by the factory — no form, validator or mask to edit.

No imports from the rest of the package: ``db`` and ``factory`` both read it.
"""
from __future__ import annotations

import importlib.util
from typing import Any, Dict, List, Optional

# field: key · label · placeholder · secret (masked, kept when left masked) · required
# `one_of`: at least one of these keys must be set (a backend with alternative credentials)
BACKENDS: Dict[str, Dict[str, Any]] = {
    "local": {
        "label": "Local or mounted folder",
        "hint": "A path on the Uderia host, including a network share",
        "module": "trusted_data_agent.filestore.local_backend",
        "class": "LocalFileStoreBackend",
        "sdk": None,
        "location": "root",
        "fields": [
            {"key": "root", "label": "Folder on the Uderia host (a mounted share works)", "placeholder": "/mnt/files",
             "required": True},
        ],
    },
    "s3": {
        "label": "S3-compatible",
        "hint": "AWS S3, MinIO and most on-premise object stores",
        "module": "trusted_data_agent.filestore.s3_backend",
        "class": "S3FileStoreBackend",
        "sdk": ("boto3", "boto3"),
        "location": "bucket",
        "fields": [
            {"key": "endpoint_url", "label": "Endpoint (empty = AWS)", "placeholder": "https://s3.eu-central-1.amazonaws.com"},
            {"key": "region", "label": "Region", "placeholder": "eu-central-1"},
            {"key": "bucket", "label": "Bucket", "placeholder": "uderia-files", "required": True},
            {"key": "prefix", "label": "Prefix (optional)", "placeholder": "uderia/"},
            {"key": "access_key_id", "label": "Access key", "secret": True},
            {"key": "secret_access_key", "label": "Secret key", "secret": True},
        ],
    },
    "azure_blob": {
        "label": "Azure Blob Storage",
        "hint": "An Azure storage account container",
        "module": "trusted_data_agent.filestore.azure_backend",
        "class": "AzureBlobFileStoreBackend",
        "sdk": ("azure.storage.blob", "azure-storage-blob"),
        "location": "container",
        "fields": [
            {"key": "account_url", "label": "Account URL (empty when a connection string is given)",
             "placeholder": "https://myaccount.blob.core.windows.net"},
            {"key": "container", "label": "Container", "placeholder": "uderia-files", "required": True},
            {"key": "prefix", "label": "Prefix (optional)", "placeholder": "uderia/"},
            {"key": "connection_string", "label": "Connection string", "secret": True},
            {"key": "account_key", "label": "Account key", "secret": True},
            {"key": "sas_token", "label": "SAS token", "secret": True},
        ],
        # with none of the credentials, the host's managed identity is used (azure-identity)
        "one_of": ["connection_string", "account_url"],
    },
    "gcs": {
        "label": "Google Cloud Storage",
        "hint": "A Google Cloud Storage bucket",
        "module": "trusted_data_agent.filestore.gcs_backend",
        "class": "GCSFileStoreBackend",
        "sdk": ("google.cloud.storage", "google-cloud-storage"),
        "location": "bucket",
        "fields": [
            {"key": "bucket", "label": "Bucket", "placeholder": "uderia-files", "required": True},
            {"key": "prefix", "label": "Prefix (optional)", "placeholder": "uderia/"},
            {"key": "project", "label": "Project (optional)", "placeholder": "my-project"},
            # empty = the host's application default credentials
            {"key": "credentials_json", "label": "Service account key (JSON; empty = the host's default credentials)",
             "secret": True, "multiline": True},
        ],
    },
}

SECRET_KEYS = frozenset(f["key"] for b in BACKENDS.values() for f in b["fields"] if f.get("secret"))
MASK = "••••••"


def sdk_note(backend_type: str) -> Optional[str]:
    """None when the backend can run on this deployment, else what is missing, in words."""
    b = BACKENDS.get(backend_type) or {}
    sdk = b.get("sdk")
    if not sdk:
        return None
    module, package = sdk
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    return None if found else f"{package} is not installed on this deployment (pip install {package})"


def validate_config(backend_type: str, config: Dict[str, Any]) -> None:
    """Raise ``ValueError`` naming the first missing field."""
    b = BACKENDS.get(backend_type)
    if b is None:
        raise ValueError(f"backend_type must be one of {', '.join(BACKENDS)}")
    for f in b["fields"]:
        if f.get("required") and not str(config.get(f["key"]) or "").strip():
            raise ValueError(f"{b['label']} needs {f['label'].split(' (')[0].lower()}")
    alt = b.get("one_of")
    if alt and not any(str(config.get(k) or "").strip() for k in alt):
        labels = [next(f["label"].split(" (")[0].lower() for f in b["fields"] if f["key"] == k) for k in alt]
        raise ValueError(f"{b['label']} needs {' or '.join(labels)}")


def public_list() -> List[Dict[str, Any]]:
    """The admin's backend list: what each is, whether it can run here, the form it takes."""
    out = []
    for k, b in BACKENDS.items():
        note = sdk_note(k)
        out.append({"id": k, "label": b["label"], "hint": b["hint"], "available": note is None, "note": note,
                    "location": b.get("location"), "fields": b["fields"], "one_of": b.get("one_of")})
    return out
