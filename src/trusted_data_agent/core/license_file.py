"""Which licence file this deployment runs on — ONE rule for every reader.

``tda_keys/license.key`` is the REPOSITORY's licence: tracked in git and renewed there, so every
checkout starts on it. A deployment with a licence of its own (prod's Enterprise licence) keeps
it in ``tda_keys/license.local.key`` — gitignored, so a ``git pull`` can never overwrite it, and
the repository's licence can be renewed in git without touching that deployment.

Precedence (first that applies):

1. ``TDA_LICENSE_FILE`` — an explicit path (e.g. a mounted secret);
2. ``tda_keys/license.local.key`` — the deployment's own licence, when present;
3. ``tda_keys/license.key`` — the repository's licence.

Every reader (the startup licence check, the enterprise package's activation, the one-time
conversion of prompts the former encryption left) resolves the file here, so they can never
disagree about which licence a deployment runs on. ``validate`` is the ONE check of a licence
file; ``LICENSED_TIERS`` names the tiers that activate the enterprise package.
"""
from __future__ import annotations

import os
from typing import List, Optional

LOCAL_NAME = "license.local.key"
REPO_NAME = "license.key"
ENV_VAR = "TDA_LICENSE_FILE"


def keys_dir() -> str:
    from trusted_data_agent.core.utils import get_project_root
    return os.path.join(str(get_project_root()), "tda_keys")


def license_path(directory: Optional[str] = None) -> str:
    """The licence file this deployment runs on (see the module docstring for the order)."""
    env = (os.environ.get(ENV_VAR) or "").strip()
    if env:
        return env
    d = directory or keys_dir()
    local = os.path.join(d, LOCAL_NAME)
    return local if os.path.exists(local) else os.path.join(d, REPO_NAME)


def previous_paths(directory: Optional[str] = None) -> List[str]:
    """Where the licence a renewal replaced may be kept: next to the active file first, then
    beside either named file (``generate_license.py`` writes ``<file>.previous``)."""
    d = directory or keys_dir()
    seen, out = set(), []
    for p in (license_path(d) + ".previous",
              os.path.join(d, LOCAL_NAME + ".previous"),
              os.path.join(d, REPO_NAME + ".previous")):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


# ── validation (the one place a licence file is checked) ──────────────────────────────────────

#: The tiers that activate the enterprise package. Every other tier (and no licence) runs the
#: public core alone — the open-core decision of 9 Nov 2026 retired the four earlier tiers.
LICENSED_TIERS = ("Enterprise", "Partner")
PUBLIC_KEY_NAME = "public_key.pem"


class LicenseError(RuntimeError):
    """The licence file is missing, unreadable, forged or expired."""


def validate(path: Optional[str] = None, public_key_path: Optional[str] = None) -> dict:
    """Verify the licence file's RSA-PSS signature with the deployment's public key and its
    expiry; return its payload (holder, tier, issued_at, expires_at, …). Raises LicenseError."""
    import json
    from datetime import datetime, timezone

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    path = path or license_path()
    pub = public_key_path or os.path.join(keys_dir(), PUBLIC_KEY_NAME)
    if not os.path.exists(path):
        raise LicenseError(f"no licence file at {path}")
    if not os.path.exists(pub):
        raise LicenseError(f"no public key at {pub}; the licence cannot be verified")
    try:
        with open(pub, "rb") as f:
            public_key = serialization.load_pem_public_key(f.read())
    except Exception as e:  # noqa: BLE001
        raise LicenseError("the public key is not a readable PEM key") from e
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = json.load(f)
        payload = content["payload"]
        signature = bytes.fromhex(content["signature"])
        public_key.verify(signature, json.dumps(payload, sort_keys=True).encode("utf-8"),
                          padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                          hashes.SHA256())
        expires = datetime.fromisoformat(payload["expires_at"])
    except (InvalidSignature, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
        raise LicenseError(f"the licence file {path} is invalid or corrupt") from e
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise LicenseError(f"the licence for {payload.get('holder', '?')} expired on {payload['expires_at'][:10]}")
    return dict(payload)


def load_validated(log=None) -> Optional[dict]:
    """This deployment's validated licence payload, or None. No licence file is not an error
    (the public core needs none); an invalid or expired one is logged as an error and ignored,
    so a deployment keeps starting — as the public core — and the log says why."""
    path = license_path()
    if not os.path.exists(path):
        return None
    try:
        payload = validate(path)
    except LicenseError as e:
        if log is not None:
            log.error(f"Licence ignored: {e}. The deployment runs as the public core (Community).")
        return None
    if log is not None:
        log.info(f"Licence validated for {payload.get('holder', '?')} (tier {payload.get('tier', '?')})")
    return payload
