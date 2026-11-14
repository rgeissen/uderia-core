"""
KG Store tier governance.

Mirrors vectorstore/settings.py — reads from the `kg_backend_settings`
table that is seeded by schema/38_kg_backend_settings.sql.

Two-level governance:
  1. Feature gate  — is the KG feature available for this tier at all?
  2. Backend allowlist — which backend types can this tier use?

Admin tier always bypasses both gates.
"""

import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
from typing import List, Optional

logger = logging.getLogger("quart.app")

# ── Defaults (used when the DB has no rows yet) ──────────────────────────────

_DEFAULT_SETTINGS = {
    "kg_enabled_user": "true",
    "kg_enabled_developer": "true",
    "allowed_backends_user": '["sqlite"]',
    "allowed_backends_developer": '["sqlite", "neo4j"]',
    # Ontology-as-governed-product policy (Tier 4.3). Defaults reproduce today's
    # behaviour: no approval required, but a non-conformant publish is blocked.
    "ontology_require_approval": "false",
    "ontology_approver_tiers": '["admin"]',
    "ontology_block_nonconformant_publish": "true",
}

ALL_KG_BACKENDS: List[str] = ["sqlite", "neo4j"]


def _get_db_path() -> str:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
    except Exception:
        return "tda_auth.db"


def _read_setting(key: str) -> str:
    """Read a single setting from kg_backend_settings, falling back to _DEFAULT_SETTINGS."""
    try:
        db_path = _get_db_path()
        conn = sqlite_util.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT setting_value FROM kg_backend_settings WHERE setting_key=?",
                (key,),
            ).fetchone()
            return row["setting_value"] if row else _DEFAULT_SETTINGS.get(key, "")
        finally:
            conn.close()
    except Exception:
        return _DEFAULT_SETTINGS.get(key, "")


def get_kgstore_settings() -> dict:
    """Return all KG store governance settings as a flat dict."""
    try:
        db_path = _get_db_path()
        conn = sqlite_util.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT setting_key, setting_value FROM kg_backend_settings"
            ).fetchall()
            result = dict(_DEFAULT_SETTINGS)
            for row in rows:
                result[row["setting_key"]] = row["setting_value"]
            return result
        finally:
            conn.close()
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_kgstore_settings(settings: dict, admin_uuid: Optional[str] = None) -> bool:
    """
    Persist a settings dict to kg_backend_settings.
    Only keys present in settings are updated; others are untouched.
    Returns True on success.
    """
    try:
        db_path = _get_db_path()
        conn = sqlite_util.connect(db_path)
        try:
            for key, value in settings.items():
                # Serialise lists to JSON
                stored = json.dumps(value) if isinstance(value, (list, dict)) else str(value).lower()
                conn.execute(
                    """
                    INSERT INTO kg_backend_settings (setting_key, setting_value, updated_by)
                    VALUES (?, ?, ?)
                    ON CONFLICT(setting_key) DO UPDATE SET
                        setting_value = excluded.setting_value,
                        updated_by = excluded.updated_by,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (key, stored, admin_uuid),
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to save KG store settings")
        return False


def is_kg_enabled_for_tier(user_tier: str) -> bool:
    """
    Return True if the KG feature is enabled for the given tier.
    Admin always has access.
    """
    if user_tier == "admin":
        return True
    key = f"kg_enabled_{user_tier}"
    val = _read_setting(key)
    return val.lower() not in ("false", "0", "no", "off", "disabled")


def get_ontology_governance_policy():
    """Return the platform ontology-governance policy as a GovernancePolicy.

    Admin-set; defaults reproduce today's behaviour (no approval, block non-conformant).
    """
    from trusted_data_agent.core.governance_lifecycle import GovernancePolicy
    require = _read_setting("ontology_require_approval").lower() in ("true", "1", "yes", "on")
    block = _read_setting("ontology_block_nonconformant_publish").lower() not in ("false", "0", "no", "off")
    try:
        approvers = frozenset(json.loads(_read_setting("ontology_approver_tiers")) or ["admin"])
    except Exception:
        approvers = frozenset({"admin"})
    return GovernancePolicy(require_approval=require, approver_tiers=approvers,
                            block_nonconformant_publish=block)


def is_backend_allowed(backend_type: str, user_tier: str) -> bool:
    """
    Return True if the given backend type is allowed for the tier.
    Admin always has access to all backends.
    """
    if user_tier == "admin":
        return True
    key = f"allowed_backends_{user_tier}"
    raw = _read_setting(key)
    try:
        allowed: List[str] = json.loads(raw)
        return backend_type in allowed
    except Exception:
        return False


def get_allowed_backends(user_tier: str) -> List[str]:
    """Return the list of backend types allowed for the given tier."""
    if user_tier == "admin":
        return list(ALL_KG_BACKENDS)
    key = f"allowed_backends_{user_tier}"
    raw = _read_setting(key)
    try:
        return json.loads(raw)
    except Exception:
        return ["sqlite"]
