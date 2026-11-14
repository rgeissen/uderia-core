"""
External key management for the file store (PROP-010 §4.3, "later": an external KMS).

By default every file's data key is wrapped by a key-encryption key derived from the
platform master key (``crypto.kek``). With an external key manager configured, NEW data keys
are wrapped by a key that never leaves that service instead — AWS KMS, Azure Key Vault,
Google Cloud KMS or HashiCorp Vault Transit — so reading a file needs the key manager to
agree, and revoking the key there makes every file it wrapped unreadable here.

The wrapped key names the provider and the key that wrapped it::

    kms1.<provider>.<base64url key reference>.<base64url ciphertext>

so switching to another key (or provider) never strands the files wrapped before: they are
unwrapped with the key they name, using that provider's saved credentials, until the admin
re-wraps them (``service.rewrap_to_active``). The payload sent to the key manager binds the
data key to its file (``UFK1`` + a hash of the file id + the key), checked on unwrap, so a
wrapped key copied onto another file does not open it — the same binding the platform wrap
gets from AES-GCM's associated data, for providers that offer none.

What stays under the platform key: content fingerprints and sealed records (attachment
manifests, store settings, this configuration). The configuration is sealed under the
platform key and resealed by ``maintenance/rotate_encryption_key.py``.

Unwrapped data keys are cached in memory for a short time (``CACHE_TTL``) so an attachment
read on every turn does not cost a key-manager call each time.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

PREFIX = "kms1."
PLATFORM = "platform"
MASK = "••••••"
CACHE_TTL = 300.0
CACHE_SIZE = 512
_PAYLOAD_MAGIC = b"UFK1"

# provider → label · hint · SDK (module, package) · the field that names the key · fields
PROVIDERS: Dict[str, Dict[str, Any]] = {
    PLATFORM: {
        "label": "Platform key",
        "hint": "Derived from this deployment's master key — no external service",
        "sdk": None, "key_field": None, "fields": [],
    },
}

def _secret_keys() -> frozenset:
    return frozenset(f["key"] for p in PROVIDERS.values() for f in p["fields"] if f.get("secret"))


SECRET_KEYS = _secret_keys()


class KeyManagerError(Exception):
    """The key manager refused or could not be reached; the message is safe to show an admin."""


# ── configuration (sealed in file_store_settings) ────────────────────────────

def _settings():
    from . import settings
    return settings


def active_provider() -> str:
    p = _settings()._raw("kek_provider") or PLATFORM
    return p if p in PROVIDERS else PLATFORM


def saved_configs() -> Dict[str, Dict[str, Any]]:
    """Every provider's saved settings, secrets included (server-side use only)."""
    sealed = _settings()._raw("kek_configs")
    if not sealed:
        return {}
    from . import crypto
    try:
        v = json.loads(crypto.open_text(sealed, "kek-configs"))
        return v if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001 — unreadable means unconfigured, never a crash on read
        return {}


def masked(config: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (MASK if k in SECRET_KEYS and v else v) for k, v in (config or {}).items()}


def merge_masked(provider: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """A form posts secrets back as the mask: fill them from the saved settings."""
    saved = saved_configs().get(provider) or {}
    out = {}
    for k, v in (config or {}).items():
        out[k] = saved.get(k, "") if (k in SECRET_KEYS and v == MASK) else v
    return out


def validate(provider: str, config: Dict[str, Any]) -> None:
    p = PROVIDERS.get(provider)
    if p is None:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    for f in p["fields"]:
        if f.get("required") and not str(config.get(f["key"]) or "").strip():
            raise ValueError(f"{p['label']} needs {f['label'].split(' (')[0].lower()}")


def save(provider: str, config: Dict[str, Any], admin_uuid: Optional[str] = None) -> None:
    """Make ``provider`` the key for NEW data keys, keeping every provider's saved settings
    (files wrapped before still name theirs). The caller has run a passing ``test``."""
    validate(provider, config)
    from . import crypto
    configs = saved_configs()
    if provider != PLATFORM:
        configs[provider] = {k: v for k, v in config.items() if k in {f["key"] for f in PROVIDERS[provider]["fields"]}}
    _settings()._write({"kek_provider": provider,
                        "kek_configs": crypto.seal_text(json.dumps(configs), "kek-configs")}, admin_uuid)
    clear_cache()


def sdk_note(provider: str) -> Optional[str]:
    sdk = (PROVIDERS.get(provider) or {}).get("sdk")
    if not sdk:
        return None
    module, package = sdk
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    return None if found else f"{package} is not installed on this deployment (pip install {package})"


def public_view() -> Dict[str, Any]:
    configs = saved_configs()
    active = active_provider()
    field = PROVIDERS[active]["key_field"]
    return {
        "active": active,
        "active_key": ((configs.get(active) or {}).get(field) or None) if field else None,
        "providers": [{"id": k, "label": p["label"], "hint": p["hint"], "fields": p["fields"],
                       "available": sdk_note(k) is None, "note": sdk_note(k),
                       "config": masked(configs.get(k) or {})} for k, p in PROVIDERS.items()],
    }


# ── the providers: encrypt / decrypt a small payload with the named key ─────

def _str(cfg: Dict[str, Any], key: str) -> str:
    v = cfg.get(key)
    return v.strip() if isinstance(v, str) else ""


# the external key managers register here (filestore/kms_providers.py, enterprise package)
_FACTORIES: Dict[str, Callable[..., Tuple[Callable[[bytes], bytes], Callable[[bytes], bytes]]]] = {}


def _client(provider: str, cfg: Dict[str, Any], key_ref: str):
    try:
        return _FACTORIES[provider](cfg, key_ref)
    except KeyManagerError:
        raise
    except KeyError as e:
        raise KeyManagerError(f"unknown key manager '{provider}'") from e
    except Exception as e:  # noqa: BLE001
        raise KeyManagerError(f"{PROVIDERS[provider]['label']} could not be opened: {e}") from e


# ── wrap / unwrap ────────────────────────────────────────────────────────────

def _binding(file_id: str) -> bytes:
    return hashlib.sha256(b"dek:" + file_id.encode("utf-8")).digest()[:16]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def is_external(wrapped: Optional[str]) -> bool:
    return isinstance(wrapped, str) and wrapped.startswith(PREFIX)


def describe_wrapping(wrapped: Optional[str]) -> Tuple[str, Optional[str]]:
    """``(provider, key_ref)`` of a wrapped key — ``("platform", None)`` for the platform wrap."""
    if not is_external(wrapped):
        return PLATFORM, None
    try:
        _, provider, ref, _ct = wrapped.split(".", 3)
        return provider, _unb64(ref).decode("utf-8")
    except Exception:  # noqa: BLE001
        return "unknown", None


def wrap(dek: bytes, file_id: str, provider: Optional[str] = None, config: Optional[Dict[str, Any]] = None) -> str:
    provider = provider or active_provider()
    cfg = config if config is not None else (saved_configs().get(provider) or {})
    key_ref = _str(cfg, PROVIDERS[provider]["key_field"])
    enc, _dec = _client(provider, cfg, key_ref)
    try:
        ct = enc(_PAYLOAD_MAGIC + _binding(file_id) + dek)
    except KeyManagerError:
        raise
    except Exception as e:  # noqa: BLE001
        raise KeyManagerError(f"{PROVIDERS[provider]['label']} did not wrap the key: {e}") from e
    return f"{PREFIX}{provider}.{_b64(key_ref.encode('utf-8'))}.{_b64(ct)}"


_CACHE: "OrderedDict[str, Tuple[bytes, float]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def unwrap(wrapped: str, file_id: str) -> bytes:
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(wrapped)
        if hit and hit[1] > now:
            _CACHE.move_to_end(wrapped)
            dek = hit[0]
        else:
            dek = None
    if dek is None:
        provider, key_ref = describe_wrapping(wrapped)
        if provider not in _FACTORIES or key_ref is None:
            raise KeyManagerError("the wrapped key names no known key manager")
        cfg = saved_configs().get(provider)
        if not cfg:
            raise KeyManagerError(f"this file's key is held by {PROVIDERS[provider]['label']} ({key_ref}), "
                                  "which is not configured on this deployment")
        _enc, dec = _client(provider, cfg, key_ref)
        try:
            payload = dec(_unb64(wrapped.split(".", 3)[3]))
        except KeyManagerError:
            raise
        except Exception as e:  # noqa: BLE001
            raise KeyManagerError(f"{PROVIDERS[provider]['label']} did not unwrap the key ({key_ref}): {e}") from e
        if not payload.startswith(_PAYLOAD_MAGIC) or len(payload) != 4 + 16 + 32:
            raise KeyManagerError("the key manager returned something that is not a wrapped data key")
        dek = payload[20:]
        with _CACHE_LOCK:
            _CACHE[wrapped] = (payload, now + CACHE_TTL)
            while len(_CACHE) > CACHE_SIZE:
                _CACHE.popitem(last=False)
        binding = payload[4:20]
    else:
        binding, dek = dek[4:20], dek[20:]
    if binding != _binding(file_id):
        raise KeyManagerError("this wrapped key belongs to another file")
    return dek


def test(provider: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap and unwrap a random probe key with these settings. Never raises."""
    import os
    if provider == PLATFORM:
        return {"success": True, "message": "The platform key needs no external service"}
    try:
        validate(provider, config)
        t0 = time.monotonic()
        probe = os.urandom(32)
        w = wrap(probe, "_probe", provider, config)
        enc, dec = _client(provider, config, describe_wrapping(w)[1] or "")
        payload = dec(_unb64(w.split(".", 3)[3]))
        if payload[20:] != probe:
            return {"success": False, "message": "the probe key came back different"}
        ms = int((time.monotonic() - t0) * 1000)
        return {"success": True, "message": f"Wrapped and unwrapped a probe key with {PROVIDERS[provider]['label']} in {ms} ms"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "message": str(e)}


def usage(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """How many files each key wraps — what a re-wrap would move."""
    counts: Dict[Tuple[str, Optional[str]], int] = {}
    for r in rows:
        k = describe_wrapping(r.get("wrapped_key"))
        counts[k] = counts.get(k, 0) + 1
    return [{"provider": p, "label": (PROVIDERS.get(p) or {}).get("label", p), "key": ref, "files": n}
            for (p, ref), n in sorted(counts.items(), key=lambda kv: -kv[1])]


# The external key managers, when the enterprise package is present (the public core wraps
# every data key under the platform key).
try:
    from trusted_data_agent.filestore import kms_providers as _kms_providers  # noqa: F401,E402
except ImportError:
    pass
