"""RFC 7636 PKCE helper, shared by the OAuth login flow and the OIDC federation."""
from __future__ import annotations

import base64
import hashlib
import secrets


def make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    return verifier, challenge
