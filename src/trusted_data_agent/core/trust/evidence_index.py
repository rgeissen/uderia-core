"""
Evidence index (PROP-009 §4.3) — the content-free audit record of every turn that carries a
bill of materials, and the usage index from a signed part to the answers that used it.

Two tables, in the same database as the attestations:

  ``evidence_turns``  one row per (owner, session, turn): the profile, the chain tip, the key
                      fingerprint, and the bill of materials as JSON (hashes, labels and
                      attestation references — never answer text or artefact content). It
                      OUTLIVES the session (decision §9.3): when a session file is gone, the
                      evidence still says what the turn used.
  ``evidence_parts``  one row per signed part a turn used — the agent, its deployment, each
                      ingredient, each data product it read, each action it performed — with
                      the content hash it used and the state at that moment. Answers "which
                      answers used this artefact, and how many since its current version".

Written once per turn at the persistence chokepoint (``session_manager.update_last_turn_data``)
— never by scanning session files. A re-save of the same turn replaces its rows. Every write
fails soft: losing an index row never breaks a turn (the sealed BOM in the chain is the
record; this index is how it is found).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from trusted_data_agent.core import sqlite_util
from trusted_data_agent.core.trust import store as _store
from trusted_data_agent.core.trust.evidence import all_parts

logger = logging.getLogger("quart.app")

_DDL = """
CREATE TABLE IF NOT EXISTS evidence_turns (
    owner_uuid       TEXT    NOT NULL,
    session_id       TEXT    NOT NULL,
    turn             INTEGER NOT NULL,
    profile_id       TEXT,
    profile_tag      TEXT,
    chain_tip_hash   TEXT,
    key_fingerprint  TEXT,
    bom_json         TEXT,
    answered_at      TEXT,
    recorded_at      REAL    NOT NULL,
    PRIMARY KEY (owner_uuid, session_id, turn)
);
CREATE TABLE IF NOT EXISTS evidence_parts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_uuid     TEXT    NOT NULL,
    session_id     TEXT    NOT NULL,
    turn           INTEGER NOT NULL,
    role           TEXT    NOT NULL,
    kind           TEXT    NOT NULL,
    artifact_id    TEXT    NOT NULL,
    label          TEXT,
    live_hash      TEXT,
    attested_hash  TEXT,
    history_id     INTEGER,
    state          TEXT,
    answered_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_evidence_parts_artifact ON evidence_parts (kind, artifact_id);
CREATE INDEX IF NOT EXISTS idx_evidence_parts_turn ON evidence_parts (owner_uuid, session_id, turn);
"""

_READY: set = set()


def _connect(db_path: Optional[str] = None):
    path = _store._db_path(db_path)
    conn = sqlite_util.connect(path)
    if path not in _READY:
        conn.executescript(_DDL)
        conn.commit()
        _READY.add(path)
    return conn


# ── what a turn used ──────────────────────────────────────────────────────────

def _role(part: Dict[str, Any]) -> str:
    kind = part.get("kind")
    if kind == "agent":
        return "agent"
    if kind == "agent_deployment":
        return "deployment"
    return "ingredient"


def _ontology_ids(bom: Dict[str, Any]) -> List[str]:
    return [str(p.get("artifact_id")) for p in (bom.get("parts") or [])
            if isinstance(p, dict) and p.get("kind") == "ontology" and p.get("artifact_id")]


def data_product_refs(turn: Dict[str, Any], bom: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The data products a turn read, from its sealed consumption receipts. A product is
    identified by ``<kg_id>::<name>``; the receipt names only the product, so the graph is
    the agent's ontology from the bill of materials (a product lives in the graph the agent
    grounds on). Without exactly one ontology the reference is kept by name only."""
    try:
        from trusted_data_agent.agent.obda import consumption_receipts
        receipts = consumption_receipts(turn.get("obda") if isinstance(turn, dict) else None)
    except Exception:  # noqa: BLE001 — OBDA-READ absent
        return []
    kgs = _ontology_ids(bom or {})
    out = []
    for r in receipts:
        name = str(r.get("product"))
        aid = f"{kgs[0]}::{name}" if len(kgs) == 1 else name
        out.append({"kind": "data_product", "artifact_id": aid, "label": name,
                    "version": r.get("version"), "state": r.get("trust_state"),
                    "row_count": r.get("row_count"), "request_hash": r.get("request_hash"),
                    "sql_hash": r.get("sql_hash")})
    return out


def action_refs(turn: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The writes a turn performed, from its sealed action receipts."""
    try:
        from trusted_data_agent.core.action_perform import performed_receipts
        recs = performed_receipts(turn.get("actions") if isinstance(turn, dict) else None)
    except Exception:  # noqa: BLE001 — OBDA-ACT absent
        return []
    out = []
    for r in recs:
        name = str(r.get("action"))
        kg = r.get("kg_id")
        out.append({"kind": "action_type",
                    "artifact_id": f"{kg}::{name}" if kg else name,
                    "label": name, "version": r.get("version"),
                    "rows_affected": r.get("rows_affected"), "posture": r.get("posture"),
                    "manifest_hash": r.get("manifest_hash")})
    return out


def record_turn(owner_uuid: str, session_id: str, turn: Dict[str, Any],
                db_path: Optional[str] = None) -> bool:
    """Index one persisted turn. A turn without a bill of materials is not indexed (it has
    no evidence to find). Returns True when written; never raises."""
    try:
        if not isinstance(turn, dict) or not owner_uuid or not session_id:
            return False
        bom = turn.get("bom")
        if not isinstance(bom, dict):
            return False
        turn_no = turn.get("turn")
        if turn_no is None:
            return False
        turn_no = int(turn_no)
        meta = turn.get("provenance_meta") or {}
        answered_at = turn.get("timestamp") or bom.get("resolved_at")
        rows = []
        for p in all_parts(bom):
            rows.append((_role(p), p.get("kind"), str(p.get("artifact_id")), p.get("label"),
                         p.get("live_hash"), p.get("attested_hash"), p.get("history_id"),
                         p.get("state")))
        for p in data_product_refs(turn, bom):
            rows.append(("data", p["kind"], p["artifact_id"], p["label"], None, None, None,
                         p.get("state")))
        for p in action_refs(turn):
            rows.append(("act", p["kind"], p["artifact_id"], p["label"],
                         p.get("manifest_hash"), None, None, "performed"))
        conn = _connect(db_path)
        try:
            conn.execute("DELETE FROM evidence_parts WHERE owner_uuid=? AND session_id=? AND turn=?",
                         (owner_uuid, session_id, turn_no))
            conn.execute(
                "INSERT OR REPLACE INTO evidence_turns (owner_uuid, session_id, turn, profile_id, "
                "profile_tag, chain_tip_hash, key_fingerprint, bom_json, answered_at, recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (owner_uuid, session_id, turn_no, bom.get("profile_id"), bom.get("profile_tag"),
                 meta.get("chain_tip_hash"), meta.get("key_fingerprint"),
                 json.dumps(bom, sort_keys=True, default=str), answered_at, time.time()))
            conn.executemany(
                "INSERT INTO evidence_parts (owner_uuid, session_id, turn, role, kind, artifact_id, "
                "label, live_hash, attested_hash, history_id, state, answered_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(owner_uuid, session_id, turn_no) + r + (answered_at,) for r in rows])
            # PROP-009 §4.5: enter the answer's record in the ledger (only when it is new or
            # changed), digesting exactly what was stored so verification re-derives it.
            from trusted_data_agent.core.trust import ledger as _ledger
            _ledger.ensure(conn)
            _ledger.backfill(conn)
            stored = conn.execute("SELECT chain_tip_hash, bom_json FROM evidence_turns WHERE "
                                  "owner_uuid=? AND session_id=? AND turn=?",
                                  (owner_uuid, session_id, turn_no)).fetchone()
            _ledger.append_turn(conn, owner_uuid, session_id, turn_no, stored[0],
                                _ledger._bom_digest_of(stored[1]))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:  # noqa: BLE001 — the index never breaks a turn
        logger.warning(f"evidence: index write skipped for {session_id}#{turn.get('turn')}: {e}")
        return False


# ── reads ─────────────────────────────────────────────────────────────────────

def turn_record(owner_uuid: str, session_id: str, turn_no: int,
                db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT profile_id, profile_tag, chain_tip_hash, key_fingerprint, bom_json, "
                "answered_at FROM evidence_turns WHERE owner_uuid=? AND session_id=? AND turn=?",
                (owner_uuid, session_id, int(turn_no))).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    try:
        bom = json.loads(row[4]) if row[4] else None
    except Exception:  # noqa: BLE001
        bom = None
    return {"profile_id": row[0], "profile_tag": row[1], "chain_tip_hash": row[2],
            "key_fingerprint": row[3], "bom": bom, "answered_at": row[5]}


def usage(kind: str, artifact_id: str, owner_uuid: Optional[str] = None,
          current_hash: Optional[str] = None, limit: int = 50,
          db_path: Optional[str] = None) -> Dict[str, Any]:
    """Which answers used this artefact. ``owner_uuid`` None = every owner (an administrator's
    view); otherwise only that owner's turns. ``since_current`` counts the turns that used the
    content hash the artefact has now (``current_hash``) — "how many since it changed"."""
    out = {"kind": kind, "artifact_id": str(artifact_id), "answers": 0, "since_current": None,
           "turns": []}

    def _where(prefix: str = "") -> str:
        w = f"{prefix}kind=? AND {prefix}artifact_id=?"
        return w + (f" AND {prefix}owner_uuid=?" if owner_uuid is not None else "")

    args: list = [kind, str(artifact_id)] + ([owner_uuid] if owner_uuid is not None else [])
    _distinct = "COUNT(DISTINCT owner_uuid || '|' || session_id || '|' || turn)"
    try:
        conn = _connect(db_path)
        try:
            out["answers"] = conn.execute(
                f"SELECT {_distinct} FROM evidence_parts WHERE {_where()}", args).fetchone()[0]
            if current_hash:
                out["since_current"] = conn.execute(
                    f"SELECT {_distinct} FROM evidence_parts WHERE {_where()} AND live_hash=?",
                    args + [current_hash]).fetchone()[0]
            rows = conn.execute(
                "SELECT p.owner_uuid, p.session_id, p.turn, p.role, p.live_hash, p.state, "
                "p.answered_at, t.profile_tag FROM evidence_parts p LEFT JOIN evidence_turns t "
                "ON t.owner_uuid=p.owner_uuid AND t.session_id=p.session_id AND t.turn=p.turn "
                f"WHERE {_where('p.')} GROUP BY p.owner_uuid, p.session_id, p.turn "
                "ORDER BY p.answered_at DESC, p.id DESC LIMIT ?",
                args + [int(limit)]).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"evidence: usage read failed for {kind}/{artifact_id}: {e}")
        return out
    for r in rows:
        out["turns"].append({"owner_uuid": r[0], "session_id": r[1], "turn": r[2], "role": r[3],
                             "live_hash": r[4], "state": r[5], "answered_at": r[6],
                             "profile_tag": r[7],
                             "same_as_current": bool(current_hash) and r[4] == current_hash})
    return out


def turns_for_owner(owner_uuid: str, limit: int = 500,
                    db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT session_id, turn, profile_tag, answered_at FROM evidence_turns "
                "WHERE owner_uuid=? ORDER BY answered_at DESC LIMIT ?",
                (owner_uuid, int(limit))).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    return [{"session_id": r[0], "turn": r[1], "profile_tag": r[2], "answered_at": r[3]}
            for r in rows]


def labels(owner_uuid: Optional[str] = None, db_path: Optional[str] = None) -> Dict[tuple, str]:
    """(kind, artifact_id) → the most recent label a turn recorded for it — how the estate
    list names an artefact without resolving every kind's own store."""
    try:
        conn = _connect(db_path)
        try:
            sql = ("SELECT kind, artifact_id, label FROM evidence_parts WHERE id IN "
                   "(SELECT MAX(id) FROM evidence_parts"
                   + (" WHERE owner_uuid=?" if owner_uuid is not None else "")
                   + " GROUP BY kind, artifact_id)")
            rows = conn.execute(sql, ([owner_uuid] if owner_uuid is not None else [])).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}
    return {(r[0], r[1]): r[2] for r in rows if r[2]}


def usage_counts(owner_uuid: Optional[str] = None, db_path: Optional[str] = None) -> Dict[tuple, Dict[str, Any]]:
    """(kind, artifact_id) → {answers, hashes: {live_hash: n}} in ONE query, so the estate list
    can say "used by N answers · M with the current version" without a query per row."""
    out: Dict[tuple, Dict[str, Any]] = {}
    try:
        conn = _connect(db_path)
        try:
            sql = ("SELECT kind, artifact_id, live_hash, "
                   "COUNT(DISTINCT owner_uuid || '|' || session_id || '|' || turn) "
                   "FROM evidence_parts"
                   + (" WHERE owner_uuid=?" if owner_uuid is not None else "")
                   + " GROUP BY kind, artifact_id, live_hash")
            rows = conn.execute(sql, ([owner_uuid] if owner_uuid is not None else [])).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return {}
    for kind, aid, h, n in rows:
        e = out.setdefault((kind, aid), {"answers": 0, "hashes": {}})
        e["answers"] += n
        e["hashes"][h] = e["hashes"].get(h, 0) + n
    return out
