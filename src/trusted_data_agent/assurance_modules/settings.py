"""
Governance for the assurance-module plane.

Mirrors `components/settings.py` — deliberately THAT one and not `extensions/settings.py`,
which short-circuits on `mode == "all"` before consulting its disable list, so a disabled
extension is still available in the default mode. Here the disable list is authoritative
regardless of mode: this plane governs code that runs beside the answer, and a governance
switch that can be bypassed by a mode is not a governance switch.

MASTER OFF BY DEFAULT. `third_party_assurance_enabled` ships `false`, so a deployment that
does nothing runs exactly what it ran before — no discovery, no registration, byte-identical
behaviour.
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from trusted_data_agent.core import sqlite_util

logger = logging.getLogger("quart.app")

_DDL = """
CREATE TABLE IF NOT EXISTS assurance_module_settings (
    id            INTEGER PRIMARY KEY,
    setting_key   TEXT NOT NULL UNIQUE,
    setting_value TEXT NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by    TEXT
);
"""

_DEFAULTS = {
    "third_party_assurance_enabled": "false",
    "assurance_module_marketplace_enabled": "false",
    "disabled_assurance_modules": "[]",
}


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (APP_CONFIG.AUTH_DB_PATH or "tda_auth.db").replace("sqlite:///", "")


def _conn(db_path: Optional[str]):
    c = sqlite_util.connect(_db_path(db_path))
    c.execute(_DDL)
    for k, v in _DEFAULTS.items():
        c.execute("INSERT OR IGNORE INTO assurance_module_settings "
                  "(setting_key, setting_value) VALUES (?, ?)", (k, v))
    c.commit()
    return c


def get_settings(db_path: Optional[str] = None) -> dict:
    """Every setting, with the shipped defaults filled in. Fails open to the defaults —
    which are the SAFE values, since the master is off."""
    out = dict(_DEFAULTS)
    try:
        conn = _conn(db_path)
        try:
            for k, v in conn.execute(
                    "SELECT setting_key, setting_value FROM assurance_module_settings"):
                out[k] = v
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("assurance-modules: settings unreadable (%s) — the shipped "
                       "defaults apply, which means the plane stays off", exc)
    return {
        "third_party_assurance_enabled":
            str(out["third_party_assurance_enabled"]).lower() == "true",
        "assurance_module_marketplace_enabled":
            str(out["assurance_module_marketplace_enabled"]).lower() == "true",
        "disabled_assurance_modules": _as_list(out["disabled_assurance_modules"]),
    }


def _as_list(v) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    try:
        parsed = json.loads(v or "[]")
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except Exception:  # noqa: BLE001
        return []


def save_settings(settings: dict, admin_uuid: Optional[str] = None,
                  db_path: Optional[str] = None) -> None:
    conn = _conn(db_path)
    try:
        for k in _DEFAULTS:
            if k not in settings:
                continue
            v = settings[k]
            val = (json.dumps(v) if isinstance(v, list)
                   else ("true" if v is True else ("false" if v is False else str(v))))
            conn.execute(
                "INSERT INTO assurance_module_settings (setting_key, setting_value, "
                "updated_by) VALUES (?, ?, ?) ON CONFLICT(setting_key) DO UPDATE SET "
                "setting_value=excluded.setting_value, updated_by=excluded.updated_by, "
                "updated_at=CURRENT_TIMESTAMP", (k, val, admin_uuid))
        conn.commit()
    finally:
        conn.close()


def is_third_party_enabled(db_path: Optional[str] = None) -> bool:
    return bool(get_settings(db_path)["third_party_assurance_enabled"])


def is_marketplace_enabled(db_path: Optional[str] = None) -> bool:
    s = get_settings(db_path)
    # The marketplace cannot be more permissive than the plane it feeds.
    return bool(s["assurance_module_marketplace_enabled"]
                and s["third_party_assurance_enabled"])


def is_module_available(pack_id: str, db_path: Optional[str] = None) -> bool:
    """Whether a pack may load. The disable list wins REGARDLESS of any other setting."""
    s = get_settings(db_path)
    if not s["third_party_assurance_enabled"]:
        return False
    return pack_id not in s["disabled_assurance_modules"]
