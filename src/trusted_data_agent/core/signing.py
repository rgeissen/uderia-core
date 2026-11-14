"""
Who signs — the one seam behind every Ed25519 signature the platform makes (PROP-009 §4.6).

Three call sites sign: a provenance-chain step (``ProvenanceChain._sign``), an arbitrary payload
(``eval.online.sign_payload`` — attestations, audit packs, ledger checkpoints), and a turn-verdict
attestation (``eval.online.sign_attestation``). All of them obtain the key, its public half and
its fingerprint here.

  local    (default) the key file in ``tda_keys/`` — exactly the behaviour before this module:
           Ed25519 is deterministic, so every signature is byte-identical to what the direct key
           call produced.
  command  the key is held OUTSIDE this host (an HSM, a cloud KMS, a Vault transit key); this
           process runs ``EVIDENCE_SIGNER_COMMAND`` with the message on stdin and reads a
           base64 signature from stdout. The public key comes from ``EVIDENCE_SIGNER_PUBLIC_KEY``
           (a PEM file). Every returned signature is VERIFIED against that public key before it
           is used — a signer that returns a wrong signature yields none, never a bad one. The
           server can request signatures but cannot read the key, so it cannot quietly re-sign
           history with a key it does not hold. Typical commands: ``gcloud kms asymmetric-sign``
           (EC_SIGN_ED25519), a Vault transit sign (ed25519), ``pkcs11-tool --sign -m EDDSA``.
           Each signature is one process call — for a busy deployment, point it at a local
           signing agent rather than a cold CLI.

Selection: ``APP_CONFIG.EVIDENCE_SIGNER`` (env ``TDA_EVIDENCE_SIGNER``), resolved on every call so
a configuration change takes effect without a restart; the signer object is cached per
configuration.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import shlex
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger("quart.app")


def _raw_fingerprint(public_key) -> str:
    from cryptography.hazmat.primitives import serialization
    raw = public_key.public_bytes(encoding=serialization.Encoding.Raw,
                                  format=serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


class Signer:
    """One way of producing Ed25519 signatures."""

    name = "base"
    key_on_host = True

    def sign(self, message: bytes) -> Optional[bytes]:
        raise NotImplementedError

    def public_key(self):
        raise NotImplementedError

    def public_key_pem(self) -> Optional[bytes]:
        k = self.public_key()
        if k is None:
            return None
        from cryptography.hazmat.primitives import serialization
        return k.public_bytes(encoding=serialization.Encoding.PEM,
                              format=serialization.PublicFormat.SubjectPublicKeyInfo)

    def fingerprint(self) -> Optional[str]:
        k = self.public_key()
        return _raw_fingerprint(k) if k is not None else None

    def available(self) -> bool:
        return self.public_key() is not None

    def sign_b64(self, message: str) -> str:
        """The base64 signature of ``message`` (UTF-8), or ``""`` when nothing can sign —
        the degraded mode every call site already handles."""
        try:
            sig = self.sign(message.encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[signing] {self.name} signer failed: {e}")
            return ""
        return base64.b64encode(sig).decode("ascii") if sig else ""

    def describe(self) -> dict:
        return {"signer": self.name, "key_on_host": self.key_on_host,
                "available": self.available(), "fingerprint": self.fingerprint()}


class LocalFileSigner(Signer):
    """The key file in ``tda_keys/`` (the platform's original behaviour)."""

    name = "local"
    key_on_host = True

    def _key(self):
        from trusted_data_agent.core import provenance
        return provenance._get_provenance_signing_key()

    def sign(self, message: bytes) -> Optional[bytes]:
        k = self._key()
        return k.sign(message) if k is not None else None

    def public_key(self):
        k = self._key()
        return k.public_key() if k is not None else None


class CommandSigner(Signer):
    """An external signer: the private key never enters this process."""

    name = "command"
    key_on_host = False

    def __init__(self, command: str, public_key_path: str, timeout: float = 10.0):
        self.command = command
        self.public_key_path = public_key_path
        self.timeout = timeout
        self._pub = None
        self._pub_error: Optional[str] = None

    def public_key(self):
        if self._pub is None and self._pub_error is None:
            try:
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                with open(self.public_key_path, "rb") as f:
                    k = serialization.load_pem_public_key(f.read())
                if not isinstance(k, Ed25519PublicKey):
                    raise ValueError("the configured public key is not Ed25519")
                self._pub = k
            except Exception as e:  # noqa: BLE001
                self._pub_error = str(e)
                logger.error(f"[signing] command signer public key unreadable: {e}")
        return self._pub

    def sign(self, message: bytes) -> Optional[bytes]:
        pub = self.public_key()
        if pub is None or not self.command:
            return None
        proc = subprocess.run(shlex.split(self.command), input=message, capture_output=True,
                              timeout=self.timeout, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"exit {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[-300:]}")
        out = proc.stdout.strip()
        try:
            sig = base64.b64decode(out, validate=True)
        except Exception:  # noqa: BLE001 — some tools print raw bytes
            sig = proc.stdout
        # Never use a signature the configured key does not confirm.
        pub.verify(sig, message)
        return sig

    def describe(self) -> dict:
        d = super().describe()
        d.update(command=self.command.split()[0] if self.command else None,
                 public_key_path=self.public_key_path, error=self._pub_error)
        return d


_CACHE: dict = {}


def _config() -> Tuple[str, str, str, float]:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return (str(getattr(APP_CONFIG, "EVIDENCE_SIGNER", "local") or "local").lower(),
                str(getattr(APP_CONFIG, "EVIDENCE_SIGNER_COMMAND", "") or ""),
                str(getattr(APP_CONFIG, "EVIDENCE_SIGNER_PUBLIC_KEY", "") or ""),
                float(getattr(APP_CONFIG, "EVIDENCE_SIGNER_TIMEOUT_SECONDS", 10) or 10))
    except Exception:  # noqa: BLE001
        return ("local", "", "", 10.0)


def get_signer() -> Signer:
    cfg = _config()
    s = _CACHE.get(cfg)
    if s is None:
        if cfg[0] == "command":
            s = CommandSigner(cfg[1], cfg[2], cfg[3])
        else:
            if cfg[0] != "local":
                logger.warning(f"[signing] unknown EVIDENCE_SIGNER {cfg[0]!r} — using the local key")
            s = LocalFileSigner()
        _CACHE.clear()
        _CACHE[cfg] = s
    return s
