"""
File-store governance (PROP-010 §4.5) — Administration → Components → Files → Policy.

Key/value in ``file_store_settings`` like ``kgstore/settings.py``; a missing row reads as its
default. Admin bypasses the tier gates (keep, stores, download policy, quota), as for KG
stores. ``require_encryption`` is locked on in hardened mode whatever the row says.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

TIERS = ("user", "developer", "admin")
DOWNLOAD_POLICIES = ("allowed", "allowed_except_data_derived", "disabled")
PLATFORM_LOCAL_ID = "platform-local"

_DEFAULTS: Dict[str, str] = {
    "files_enabled_user": "true",
    "files_enabled_developer": "true",
    "allowed_stores_user": json.dumps([PLATFORM_LOCAL_ID]),
    "allowed_stores_developer": json.dumps([PLATFORM_LOCAL_ID]),
    "download_policy_user": "allowed",
    "download_policy_developer": "allowed",
    "quota_mb_user": "1024",
    "quota_mb_developer": "5120",
    "quota_mb_admin": "0",               # 0 = no limit
    "require_encryption": "true",
    "max_file_mb": "50",
    "retention_days": "30",
    "platform_local_capacity_mb": "0",   # 0 = no limit (the disk's own size still applies)
    "user_quota_overrides": "{}",        # {user_uuid: MB} — 0 = no limit for that user
    "capacity_warn_percent": "80",
}


def _db_path() -> str:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
    except Exception:  # noqa: BLE001
        return "tda_auth.db"


def _rows() -> Dict[str, str]:
    from . import db
    db.ensure_tables()
    try:
        conn = sqlite_util.connect(_db_path())
        conn.row_factory = sqlite3.Row
        try:
            return {r["setting_key"]: r["setting_value"] for r in conn.execute(
                "SELECT setting_key, setting_value FROM file_store_settings").fetchall()}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}


def _raw(key: str) -> str:
    return _rows().get(key, _DEFAULTS.get(key, ""))


def _bool(v: str) -> bool:
    return str(v).strip().lower() not in ("false", "0", "no", "off", "disabled", "")


def _int(v: str, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def hardened() -> bool:
    try:
        from trusted_data_agent.core.key_policy import hardened_mode
        return hardened_mode()
    except Exception:  # noqa: BLE001
        return False


# ── reads ────────────────────────────────────────────────────────────────────

def files_enabled_for_tier(tier: str) -> bool:
    if tier == "admin":
        return True
    return _bool(_raw(f"files_enabled_{tier}") or "true")


def allowed_stores(tier: str) -> Optional[List[str]]:
    """Store ids this tier's agents may be bound to; ``None`` = every store (admin)."""
    if tier == "admin":
        return None
    try:
        v = json.loads(_raw(f"allowed_stores_{tier}") or "[]")
        return [str(x) for x in v] if isinstance(v, list) else [PLATFORM_LOCAL_ID]
    except Exception:  # noqa: BLE001
        return [PLATFORM_LOCAL_ID]


def is_store_allowed(store_id: str, tier: str) -> bool:
    allowed = allowed_stores(tier)
    return allowed is None or store_id in allowed


def download_policy(tier: str) -> str:
    if tier == "admin":
        return "allowed"
    v = _raw(f"download_policy_{tier}")
    return v if v in DOWNLOAD_POLICIES else "allowed"


def quota_bytes(tier: str) -> int:
    """0 = no limit."""
    mb = _int(_raw(f"quota_mb_{tier}") or "0", 0)
    return max(0, mb) * 1024 * 1024


def user_quota_overrides() -> Dict[str, int]:
    try:
        v = json.loads(_raw("user_quota_overrides") or "{}")
        return {str(k): max(0, _int(x, 0)) for k, x in (v or {}).items()} if isinstance(v, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def quota_bytes_for_user(user_uuid: str, tier: str) -> int:
    """A per-user override wins over the tier's quota; 0 = no limit."""
    ov = user_quota_overrides()
    if user_uuid in ov:
        return ov[user_uuid] * 1024 * 1024
    return quota_bytes(tier)


def set_user_quota(user_uuid: str, mb: Optional[int], admin_uuid: Optional[str] = None) -> Dict[str, int]:
    ov = user_quota_overrides()
    if mb is None:
        ov.pop(user_uuid, None)
    else:
        if _int(mb, -1) < 0:
            raise ValueError("quota must be 0 (no limit) or a positive number of MB")
        ov[user_uuid] = _int(mb, 0)
    _write({"user_quota_overrides": json.dumps(ov)}, admin_uuid)
    return ov


def platform_local_capacity_mb() -> Optional[int]:
    v = _int(_raw("platform_local_capacity_mb"), 0)
    return v if v > 0 else None


def capacity_warn_fraction() -> float:
    return min(0.99, max(0.5, _int(_raw("capacity_warn_percent"), 80) / 100.0))


def require_encryption() -> bool:
    return True if hardened() else _bool(_raw("require_encryption"))


def max_file_bytes() -> int:
    return max(1, _int(_raw("max_file_mb"), 50)) * 1024 * 1024


def retention_days() -> int:
    return max(0, _int(_raw("retention_days"), 30))


def get_settings() -> Dict[str, Any]:
    """The whole policy, shaped for the admin UI."""
    rows = _rows()
    val = lambda k: rows.get(k, _DEFAULTS.get(k, ""))  # noqa: E731
    tiers = {}
    for t in TIERS:
        if t == "admin":
            tiers[t] = {"files_enabled": True, "allowed_stores": None, "download_policy": "allowed",
                        "quota_mb": _int(val("quota_mb_admin"), 0), "bypass": True}
            continue
        try:
            stores = json.loads(val(f"allowed_stores_{t}") or "[]")
        except Exception:  # noqa: BLE001
            stores = [PLATFORM_LOCAL_ID]
        pol = val(f"download_policy_{t}")
        tiers[t] = {"files_enabled": _bool(val(f"files_enabled_{t}")), "allowed_stores": stores,
                    "download_policy": pol if pol in DOWNLOAD_POLICIES else "allowed",
                    "quota_mb": _int(val(f"quota_mb_{t}"), 0), "bypass": False}
    return {
        "tiers": tiers,
        "require_encryption": require_encryption(),
        "require_encryption_locked": hardened(),
        "max_file_mb": _int(val("max_file_mb"), 50),
        "retention_days": _int(val("retention_days"), 30),
        "platform_local_capacity_mb": _int(val("platform_local_capacity_mb"), 0),
        "capacity_warn_percent": _int(val("capacity_warn_percent"), 80),
        "download_policies": list(DOWNLOAD_POLICIES),
    }


# ── writes ───────────────────────────────────────────────────────────────────

def save_settings(patch: Dict[str, Any], admin_uuid: Optional[str] = None) -> Dict[str, Any]:
    """Apply a patch shaped like ``get_settings()`` (only the keys given). Invalid values are
    refused with a message naming the field; nothing is written then."""
    writes: Dict[str, str] = {}
    for t, cfg in (patch.get("tiers") or {}).items():
        if t not in ("user", "developer") and not (t == "admin" and set(cfg) <= {"quota_mb"}):
            raise ValueError(f"tier '{t}' cannot be changed")
        if "files_enabled" in cfg:
            writes[f"files_enabled_{t}"] = "true" if cfg["files_enabled"] else "false"
        if "allowed_stores" in cfg:
            stores = cfg["allowed_stores"]
            if not isinstance(stores, list) or not all(isinstance(s, str) for s in stores):
                raise ValueError("allowed_stores must be a list of store ids")
            writes[f"allowed_stores_{t}"] = json.dumps(stores)
        if "download_policy" in cfg:
            if cfg["download_policy"] not in DOWNLOAD_POLICIES:
                raise ValueError(f"download_policy must be one of {', '.join(DOWNLOAD_POLICIES)}")
            writes[f"download_policy_{t}"] = cfg["download_policy"]
        if "quota_mb" in cfg:
            q = _int(cfg["quota_mb"], -1)
            if q < 0:
                raise ValueError("quota_mb must be 0 (no limit) or a positive number")
            writes[f"quota_mb_{t}"] = str(q)
    if "require_encryption" in patch:
        if hardened() and not patch["require_encryption"]:
            raise ValueError("encryption cannot be turned off in hardened mode")
        writes["require_encryption"] = "true" if patch["require_encryption"] else "false"
    if "max_file_mb" in patch:
        m = _int(patch["max_file_mb"], 0)
        if not 1 <= m <= 500:
            raise ValueError("max_file_mb must be between 1 and 500")
        writes["max_file_mb"] = str(m)
    if "platform_local_capacity_mb" in patch:
        c = _int(patch["platform_local_capacity_mb"] or 0, -1)
        if c < 0:
            raise ValueError("platform_local_capacity_mb must be 0 (no limit) or a positive number of MB")
        writes["platform_local_capacity_mb"] = str(c)
    if "capacity_warn_percent" in patch:
        w = _int(patch["capacity_warn_percent"], 0)
        if not 50 <= w <= 99:
            raise ValueError("capacity_warn_percent must be between 50 and 99")
        writes["capacity_warn_percent"] = str(w)
    if "retention_days" in patch:
        d = _int(patch["retention_days"], -1)
        if not 0 <= d <= 3650:
            raise ValueError("retention_days must be between 0 and 3650")
        writes["retention_days"] = str(d)
    _write(writes, admin_uuid)
    return get_settings()


def _write(writes: Dict[str, str], admin_uuid: Optional[str]) -> None:
    if not writes:
        return
    from . import db
    db.ensure_tables()
    conn = sqlite_util.connect(_db_path())
    try:
        for k, v in writes.items():
            conn.execute(
                "INSERT INTO file_store_settings (setting_key, setting_value, updated_by) VALUES (?, ?, ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, "
                "updated_by=excluded.updated_by, updated_at=CURRENT_TIMESTAMP", (k, v, admin_uuid))
        conn.commit()
    finally:
        conn.close()
