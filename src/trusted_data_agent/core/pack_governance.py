"""
Agent-pack governance lifecycle (Tier 3.2 / P7) — packs as **governed data products**.

Reuses the storage-agnostic Tier-4.3 state machine (`core/governance_lifecycle`) to give an
agent pack a **draft → propose → approve → publish (release) → deprecate** lifecycle plus an
**SLA tier**. Composes with Tier 5.2: the *publish gate maps the pack's TRUST state to
conformance* — a **governed** data product must be **trusted** (🟢 → conformant, 🟠 → partial,
⚪ → nonconformant). Default policy (no approval, block-untrusted OFF) reproduces today's
behaviour byte-for-byte.

Lifecycle columns are additive, self-applying `ALTER`s on ``agent_pack_installations``; audit
rows go to ``pack_lifecycle_audit``. Deprecation archives the pack's sessions (reusing the
unified artifact-relationships archive path). Emits an OTLP-shaped governance span.

Decision logic stays in the pure primitives — this module is the storage + effects glue,
mirroring ``core/ontology_governance.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from trusted_data_agent.core import sqlite_util
from trusted_data_agent.core.governance_lifecycle import (
    LifecycleStatus, ApprovalStatus, LifecycleAction, GovernancePolicy,
    validate_transition, build_audit, CONFORMANT, PARTIAL, NONCONFORMANT,
)

logger = logging.getLogger("quart.app")

PACK_KIND = "agent_pack"
SLA_TIERS = ("gold", "silver", "bronze")

_LIFECYCLE_COLS = {
    "lifecycle_status": "VARCHAR(20) DEFAULT 'draft'",
    "approval_status": "VARCHAR(20) DEFAULT 'none'",
    "sla_tier": "VARCHAR(20)",
    "proposed_at": "TEXT",
    "approved_by": "VARCHAR(36)",
    "approved_at": "TEXT",
    "released_at": "TEXT",
    "deprecated_at": "TEXT",
}
_STATE_COLS = ["lifecycle_status", "approval_status", "sla_tier", "proposed_at",
               "approved_by", "approved_at", "released_at", "deprecated_at",
               "owner_user_id", "name"]

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS pack_lifecycle_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id INTEGER NOT NULL,
    action          TEXT, actor_tier TEXT, is_owner INTEGER,
    from_status     TEXT, to_status TEXT, from_approval TEXT, to_approval TEXT,
    conformance     TEXT, allowed INTEGER, reason TEXT, badge TEXT,
    created_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (APP_CONFIG.AUTH_DB_PATH or "tda_auth.db").replace("sqlite:///", "")


def _connect(db_path: Optional[str] = None):
    """Connect + self-apply the audit table and the additive lifecycle columns."""
    conn = sqlite_util.connect(_db_path(db_path))
    conn.execute(_AUDIT_DDL)
    try:
        from trusted_data_agent.core.audit_integrity import install_triggers
        install_triggers(conn, ["pack_lifecycle_audit"])
    except Exception as e:
        logger.error(f"pack_lifecycle_audit triggers not installed: {e}")
    for col, decl in _LIFECYCLE_COLS.items():
        try:
            conn.execute(f"ALTER TABLE agent_pack_installations ADD COLUMN {col} {decl}")
        except Exception:
            pass  # already present
    conn.commit()
    return conn


# ── settings (admin policy; defaults reproduce today) ────────────────────────

def _setting(key: str, default: str, db_path: Optional[str] = None) -> str:
    try:
        conn = sqlite_util.connect(_db_path(db_path))
        try:
            row = conn.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key=?", (key,)).fetchone()
            return row[0] if row else default
        finally:
            conn.close()
    except Exception:
        return default


def get_pack_governance_policy(db_path: Optional[str] = None) -> GovernancePolicy:
    """Admin-set pack governance policy. Defaults: no approval required, block-untrusted OFF
    (so publishing stays free — byte-identical baseline)."""
    require = _setting("pack_require_approval", "false", db_path).lower() in ("true", "1", "yes", "on")
    block = _setting("pack_block_untrusted_publish", "false", db_path).lower() in ("true", "1", "yes", "on")
    try:
        approvers = frozenset(json.loads(_setting("pack_approver_tiers", '["admin"]', db_path)) or ["admin"])
    except Exception:
        approvers = frozenset({"admin"})
    return GovernancePolicy(require_approval=require, approver_tiers=approvers,
                            block_nonconformant_publish=block)


# ── state read/write ─────────────────────────────────────────────────────────

def get_pack_lifecycle(installation_id, db_path: Optional[str] = None) -> Dict[str, Any]:
    """The pack's lifecycle state + trust gate, or {} if not found."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {', '.join(_STATE_COLS)} FROM agent_pack_installations WHERE id=?",
            (int(installation_id),)).fetchone()
        if not row:
            return {}
        d = dict(zip(_STATE_COLS, row))
        d["lifecycle_status"] = d.get("lifecycle_status") or "draft"
        d["approval_status"] = d.get("approval_status") or "none"
        d["trust_gate"] = _pack_conformance(installation_id, db_path=db_path)
        return d
    finally:
        conn.close()


def _write_state(conn, installation_id, **updates) -> None:
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        return
    cols = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE agent_pack_installations SET {cols} WHERE id=?",
                 (*updates.values(), int(installation_id)))
    conn.commit()


def _append_audit(conn, installation_id, a: dict) -> None:
    conn.execute(
        "INSERT INTO pack_lifecycle_audit (installation_id, action, actor_tier, is_owner, "
        "from_status, to_status, from_approval, to_approval, conformance, allowed, reason, "
        "badge, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (int(installation_id), a.get("action"), a.get("actor_tier"), 1 if a.get("is_owner") else 0,
         a.get("from_status"), a.get("to_status"), a.get("from_approval"), a.get("to_approval"),
         a.get("conformance"), 1 if a.get("allowed") else 0, a.get("reason"), a.get("badge"), _now()))
    conn.commit()


def set_sla(installation_id, sla_tier: Optional[str], db_path: Optional[str] = None) -> bool:
    """Set (or clear with None/'') the pack's SLA tier — a frictionless metadata update,
    independent of the lifecycle gate. Returns True on success."""
    tier = sla_tier if (sla_tier in SLA_TIERS) else None
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE agent_pack_installations SET sla_tier=? WHERE id=?",
                     (tier, int(installation_id)))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_pack_audit(installation_id, limit: int = 50, db_path: Optional[str] = None) -> list:
    conn = _connect(db_path)
    try:
        cols = ["action", "actor_tier", "from_status", "to_status", "conformance",
                "allowed", "reason", "created_at"]
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM pack_lifecycle_audit WHERE installation_id=? "
            "ORDER BY id DESC LIMIT ?", (int(installation_id), int(limit))).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


# ── the publish gate: pack TRUST → conformance ───────────────────────────────

def _pack_conformance(installation_id, db_path: Optional[str] = None) -> str:
    """Map the pack's Trust-Membrane state to the conformance gate:
    🟢 trusted → conformant · 🟠 changed → partial · ⚪ unsigned → nonconformant."""
    try:
        from trusted_data_agent.core.trust import store as _tstore, pack as _tpack
        from trusted_data_agent.core.trust.attestation import resolve_state, TrustState
        attested = _tstore.attested_hash(PACK_KIND, str(installation_id), db_path=db_path)
        if not attested:
            return NONCONFORMANT
        live = _tpack.composition_hash_for(str(installation_id))
        st = resolve_state(attested, live) if live else TrustState.CHANGED
        return {TrustState.TRUSTED: CONFORMANT, TrustState.CHANGED: PARTIAL}.get(st, NONCONFORMANT)
    except Exception:
        return NONCONFORMANT


# ── deprecation → archive the pack's sessions ────────────────────────────────

async def _archive_pack_sessions(installation_id, owner_user_id: str, db_path: Optional[str] = None) -> int:
    total = 0
    try:
        from trusted_data_agent.core.agent_pack_db import AgentPackDB
        from trusted_data_agent.core import session_manager
        for r in AgentPackDB(_db_path(db_path)).get_resources_for_pack(int(installation_id)):
            if r.get("resource_type") == "profile" and r.get("resource_id"):
                try:
                    res = await session_manager.archive_sessions_by_profile(
                        str(r["resource_id"]), owner_user_id)
                    total += int((res or {}).get("archived_count", 0)) if isinstance(res, dict) else 0
                except Exception as e:
                    logger.debug(f"pack_governance: archive profile failed: {e}")
    except Exception as e:
        logger.debug(f"pack_governance: session archive failed: {e}")
    return total


def _emit_span(installation_id, action, decision, from_status, conformance,
               actor_tier, user_uuid, result) -> None:
    try:
        from trusted_data_agent.eval import online
        online.emit_governance_span({
            "artifact_kind": "agent_pack",
            "artifact_id": str(installation_id),
            "action": action.value if hasattr(action, "value") else str(action),
            "allowed": decision.allowed,
            "reason": decision.reason,
            "from_status": from_status.value if hasattr(from_status, "value") else str(from_status),
            "to_status": result.get("status"),
            "conformance": conformance,
            "actor_tier": actor_tier,
            "user_uuid": user_uuid,
        })
    except Exception as e:
        logger.debug(f"pack_governance: span emit failed: {e}")


# ── the orchestrator ─────────────────────────────────────────────────────────

async def transition_pack(
    installation_id,
    action,
    *,
    user_uuid: str,
    actor_tier: str = "admin",
    is_owner: bool = True,
    policy: Optional[GovernancePolicy] = None,
    sla_tier: Optional[str] = None,
    override_nonconformant: bool = False,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a lifecycle transition. Always returns a result dict — denied transitions are
    audited + reported, never raised."""
    action = LifecycleAction(action)
    if policy is None:
        policy = get_pack_governance_policy(db_path)

    conn = _connect(db_path)
    try:
        state = get_pack_lifecycle(installation_id, db_path=db_path)
        if not state:
            return {"allowed": False, "reason": "pack not found", "action": action.value}
        cur_status = LifecycleStatus(state.get("lifecycle_status") or "draft")
        cur_approval = ApprovalStatus(state.get("approval_status") or "none")
        conformance = _pack_conformance(installation_id, db_path=db_path) if action is LifecycleAction.PUBLISH else None

        decision = validate_transition(
            action, current_status=cur_status, approval_status=cur_approval, policy=policy,
            actor_tier=actor_tier, is_owner=is_owner, conformance=conformance,
            override_nonconformant=override_nonconformant)

        audit = build_audit(action, decision, actor_tier=actor_tier, is_owner=is_owner,
                            from_status=cur_status, from_approval=cur_approval, conformance=conformance)
        _append_audit(conn, installation_id, audit.to_dict())

        result: Dict[str, Any] = {
            "allowed": decision.allowed, "reason": decision.reason, "action": action.value,
            "badge": decision.badge, "status": cur_status.value,
            "approval_status": cur_approval.value, "trust_gate": conformance or state.get("trust_gate"),
        }

        if decision.allowed:
            now = _now()
            if action is LifecycleAction.PUBLISH:
                _write_state(conn, installation_id, lifecycle_status=LifecycleStatus.PUBLISHED.value,
                             approval_status=ApprovalStatus.NONE.value, released_at=now)
                result.update(status=LifecycleStatus.PUBLISHED.value, approval_status="none", released_at=now)
            elif action is LifecycleAction.DEPRECATE:
                _write_state(conn, installation_id, lifecycle_status=LifecycleStatus.DEPRECATED.value,
                             deprecated_at=now)
                result.update(status=LifecycleStatus.DEPRECATED.value)
                result["sessions_archived"] = await _archive_pack_sessions(
                    installation_id, state.get("owner_user_id") or user_uuid, db_path=db_path)
            else:  # propose / approve / reject
                updates: Dict[str, Any] = {}
                if decision.next_approval is not None:
                    updates["approval_status"] = decision.next_approval.value
                    result["approval_status"] = decision.next_approval.value
                if decision.next_status is not None:
                    updates["lifecycle_status"] = decision.next_status.value
                    result["status"] = decision.next_status.value
                if action is LifecycleAction.PROPOSE:
                    updates["proposed_at"] = now
                if action is LifecycleAction.APPROVE:
                    updates.update(approved_by=user_uuid, approved_at=now)
                _write_state(conn, installation_id, **updates)

            if sla_tier and sla_tier in SLA_TIERS:
                _write_state(conn, installation_id, sla_tier=sla_tier)
                result["sla_tier"] = sla_tier

        _emit_span(installation_id, action, decision, cur_status, conformance, actor_tier, user_uuid, result)
        return result
    finally:
        conn.close()
