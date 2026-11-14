"""
Key policy — the one place that decides what counts as an insecure key
configuration, and whether the server may start with one.

Two modes:

* default — insecure settings are logged as warnings and the server starts.
  This is the historical behaviour; existing deployments are unaffected.
* hardened — ``TDA_SECURITY_PROFILE=hardened`` (or ``TDA_ENV=production``).
  The server refuses to start while any finding below is open, and names
  every one of them with the setting that clears it.

Findings:

1. ``TDA_ENCRYPTION_KEY`` unset, the development default, or shorter than
   32 characters. It derives the Fernet keys for stored credentials, SSO
   secrets and TTS keys.
2. The Ed25519 evidence-signing key held on the host unencrypted: the local
   signer is in use and ``TDA_PROVENANCE_KEY_PASSPHRASE`` is not set. Either
   set the passphrase (the key file is then encrypted at rest) or move the key
   to an HSM/KMS with ``TDA_EVIDENCE_SIGNER=command``.

The master key is read with the exact semantics the modules always used
(``os.environ.get(name, default)``), so no existing ciphertext changes meaning.
"""

from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger("quart.app")

DEFAULT_DEV_KEY = "dev-master-key-change-in-production"
MIN_MASTER_KEY_LENGTH = 32
_HARDENED_PROFILES = ("hardened", "federal", "cjis")


def master_key() -> str:
    """
    The platform master key, read as every consumer always read it, with one
    addition: an EMPTY value counts as unset. docker-compose forwards the
    setting as ``${TDA_ENCRYPTION_KEY:-}``, so an instance without a key gets
    an empty string; treating that as a key would encrypt every new secret
    under "" and make every existing one unreadable.
    """
    return os.environ.get("TDA_ENCRYPTION_KEY", "").strip() or DEFAULT_DEV_KEY


def provenance_passphrase() -> bytes | None:
    value = os.environ.get("TDA_PROVENANCE_KEY_PASSPHRASE", "")
    return value.encode("utf-8") if value else None


def hardened_mode() -> bool:
    if os.environ.get("TDA_SECURITY_PROFILE", "").strip().lower() in _HARDENED_PROFILES:
        return True
    return os.environ.get("TDA_ENV", "").strip().lower() == "production"


def _signer_name() -> str:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return (getattr(APP_CONFIG, "EVIDENCE_SIGNER", "local") or "local").strip().lower()
    except Exception:
        return os.environ.get("TDA_EVIDENCE_SIGNER", "local").strip().lower() or "local"


def findings() -> List[str]:
    """Every open insecure-key finding, each naming the setting that clears it."""
    out: List[str] = []
    key = master_key()
    if key == DEFAULT_DEV_KEY:
        out.append("TDA_ENCRYPTION_KEY is not set; stored credentials are encrypted with the "
                   "public development default. Generate one with "
                   "maintenance/rotate_encryption_key.py.")
    elif len(key) < MIN_MASTER_KEY_LENGTH:
        out.append(f"TDA_ENCRYPTION_KEY is shorter than {MIN_MASTER_KEY_LENGTH} characters.")
    if _signer_name() == "local" and provenance_passphrase() is None:
        out.append("The Ed25519 evidence-signing key is stored on the host unencrypted. Set "
                   "TDA_PROVENANCE_KEY_PASSPHRASE to encrypt it at rest, or set "
                   "TDA_EVIDENCE_SIGNER=command to keep it in an HSM or KMS.")
    return out


class InsecureKeyConfiguration(RuntimeError):
    """Raised at startup in hardened mode while a key finding is open."""


def enforce_at_startup() -> List[str]:
    """
    Log every finding. In hardened mode, raise so the server does not start.
    Returns the findings (empty when the configuration is sound).
    """
    found = findings()
    if not found:
        if hardened_mode():
            logger.info("Key policy: hardened mode, no insecure key settings found.")
        return found
    for f in found:
        (logger.error if hardened_mode() else logger.warning)(f"Key policy: {f}")
    if hardened_mode():
        raise InsecureKeyConfiguration(
            "Refusing to start in hardened mode (TDA_SECURITY_PROFILE / TDA_ENV=production):\n  - "
            + "\n  - ".join(found)
        )
    return found
