"""
Trust attestation persistence + signing (Tier 5.2).

The ``trust_attestations`` table holds one CURRENT attestation per artifact (agents *and*
ingredients — ingredient rows are the reuse substrate). Every write is also appended to
``trust_attestation_history``, so a re-sign or a revocation never erases what was signed
before (PROP-009 §4.1): a past answer that used an earlier version can still be traced to the
signature that covered it. ``trust_signing_keys`` records every public key that has signed
here, so a signature made by a rotated key still verifies and is labelled as such.

**Owner scoping (PROP-009 §9.1).** Agent attestations — the portable signature (``agent``)
and the deployment attestation (``agent_deployment``) — are keyed by
``(kind, artifact_id, owner_uuid)``, because the bootstrap agents share their ids across
accounts: keyed by id alone, one account's signature was another account's badge, and one
account's re-sign or revocation erased another's. Every other kind has platform-unique ids
(graphs, collections, flows, packs) or is platform-wide by design (skills, extensions) and
keeps ``owner_uuid = ''``. A scoped lookup with no owner reads only the unassigned bucket
(``''``) — rows the migration could not attribute — so a caller that forgets the owner sees
*unsigned*, never another account's signature.

The tables are **self-applying** (created/migrated on first use); all access goes through the
canonical ``sqlite_util.connect`` (process-wide serialization). Signing reuses
``eval.online.sign_payload`` — the same Ed25519 provenance key behind turn and governance
attestations — so a trust attestation is offline-verifiable with the committed verifier.

Functions accept an optional ``db_path`` so tests can inject a temp DB; in production it
resolves from ``APP_CONFIG.AUTH_DB_PATH``.

Design: docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md §9; PROP-009 §4.1.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Dict, Iterable, List, Optional, Set

from trusted_data_agent.core import sqlite_util

from trusted_data_agent.core.trust import ledger as _ledger

logger = logging.getLogger("quart.app")

#: Kinds whose artifact ids are NOT platform-unique (profile ids, which bootstrap copies into
#: every account unchanged) — attestations of these are keyed by owner as well.
OWNER_SCOPED_KINDS = frozenset({"agent", "agent_deployment"})

_COLUMNS = ("kind", "artifact_id", "owner_uuid", "version_hash", "verdict_json", "signer_uuid",
            "signer_fingerprint", "signature", "signer_public_key", "signed", "signed_at",
            "history_id")

_DDL_CURRENT = """
CREATE TABLE IF NOT EXISTS trust_attestations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL,   -- 'agent' | 'ontology' | 'skill' | ...
    artifact_id       TEXT    NOT NULL,
    owner_uuid        TEXT    NOT NULL DEFAULT '',  -- set for OWNER_SCOPED_KINDS, '' otherwise
    version_hash      TEXT    NOT NULL,   -- ingredient content hash, or agent composition hash
    verdict_json      TEXT,               -- validator verdict / conformance report
    signer_uuid       TEXT,
    signer_fingerprint TEXT,
    signature         TEXT,               -- Ed25519 signature (base64), from sign_payload
    signer_public_key TEXT,               -- signer's provenance PUBLIC key (PEM) — carried so a
                                          -- cross-instance copy verifies against the SIGNER's key
    signed            INTEGER NOT NULL DEFAULT 0,
    signed_at         REAL    NOT NULL,
    history_id        INTEGER,            -- the history row that produced this current row
    UNIQUE(kind, artifact_id, owner_uuid) -- one CURRENT attestation per artifact (and owner)
);
"""

_DDL_HISTORY = """
CREATE TABLE IF NOT EXISTS trust_attestation_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL,
    artifact_id       TEXT    NOT NULL,
    owner_uuid        TEXT    NOT NULL DEFAULT '',
    event             TEXT    NOT NULL,   -- sign | record | reinstate | revoke | retire | migrated
    version_hash      TEXT,
    verdict_json      TEXT,
    signer_uuid       TEXT,
    signer_fingerprint TEXT,
    signature         TEXT,
    signer_public_key TEXT,
    signed            INTEGER NOT NULL DEFAULT 0,
    signed_at         REAL,
    recorded_at       REAL    NOT NULL,
    actor_uuid        TEXT                -- who performed a revoke/retire (when known)
);
CREATE INDEX IF NOT EXISTS idx_trust_hist_artifact
    ON trust_attestation_history(kind, artifact_id, owner_uuid);
CREATE INDEX IF NOT EXISTS idx_trust_hist_hash
    ON trust_attestation_history(version_hash);
"""

_DDL_KEYS = """
CREATE TABLE IF NOT EXISTS trust_signing_keys (
    fingerprint     TEXT PRIMARY KEY,
    public_key_pem  TEXT,
    origin          TEXT NOT NULL,        -- 'local' (this deployment's key) | 'carried'
    first_seen      REAL NOT NULL,
    last_seen       REAL NOT NULL,
    retired_at      REAL                  -- local keys only: when a newer local key replaced it
);
"""

#: Events that establish a CURRENT attestation (as opposed to ending one).
#: ``renew`` is a signature re-issued on the owner's behalf by a scheduled sync under the
#: owner's standing policy (core/trust/renewal.py) — it establishes a current attestation
#: exactly like ``sign``, and is named differently so history never reads it as a human act.
_ESTABLISHING_EVENTS = ("sign", "record", "reinstate", "migrated", "renew")


def owner_key(kind: str, owner_uuid: Optional[str]) -> str:
    """The owner component of an attestation's identity: the owner for a scoped kind
    (``''`` when the caller has none — the unassigned bucket), always ``''`` otherwise."""
    if kind in OWNER_SCOPED_KINDS:
        return str(owner_uuid) if owner_uuid else ""
    return ""


def _db_path(db_path: Optional[str]) -> str:
    if db_path:
        return db_path
    from trusted_data_agent.core.config import APP_CONFIG
    return (APP_CONFIG.AUTH_DB_PATH or "tda_auth.db").replace("sqlite:///", "")


#: TC-66 — db paths whose schema migration has been verified this process. The migration
#: probes once per (process, path) and a real failure is logged, never swallowed.
_MIGRATED_PATHS: set = set()


def _profile_owners(conn) -> Dict[str, Set[str]]:
    """profile id → the set of accounts holding a profile with that id (bootstrap ids are
    held by many). Read from ``user_preferences`` when it exists in this DB; a DB without
    it (a test DB) yields ``{}`` and every scoped row stays unassigned."""
    out: Dict[str, Set[str]] = {}
    try:
        rows = conn.execute(
            "SELECT user_id, preferences_json FROM user_preferences").fetchall()
    except Exception:  # noqa: BLE001 — no user_preferences table here
        return out
    for uid, pj in rows:
        try:
            profiles = (json.loads(pj or "{}") or {}).get("profiles") or []
        except Exception:  # noqa: BLE001
            continue
        for p in profiles:
            pid = p.get("id") if isinstance(p, dict) else None
            if pid:
                out.setdefault(str(pid), set()).add(str(uid))
    return out


def attribute_owner(kind: str, artifact_id: str, signer_uuid: Optional[str],
                    owners_by_profile: Dict[str, Set[str]]) -> str:
    """Who owns a pre-scoping attestation row (pure; the migration's one rule).

    * a non-scoped kind → ``''``;
    * exactly one account holds a profile with this id → that account (this covers a
      signature reinstated from a pack, where the signer is the publisher);
    * several accounts hold it (a bootstrap id) → the signer, if the signer is one of them —
      since July 2026 only the owner can sign or attest their own agent;
    * otherwise → ``''`` (unassigned, listed for review — never assigned by guess).
    """
    if kind not in OWNER_SCOPED_KINDS:
        return ""
    holders = owners_by_profile.get(str(artifact_id)) or set()
    if len(holders) == 1:
        return next(iter(holders))
    if signer_uuid and str(signer_uuid) in holders:
        return str(signer_uuid)
    return ""


def _migrate(conn, path: str) -> None:
    """Bring a DB to the current schema. Idempotent; each step probes before acting.

    1. ``signer_public_key`` (pre-portability DBs);
    2. owner scoping — the table-level ``UNIQUE(kind, artifact_id)`` cannot be altered in
       SQLite, so the table is REBUILT once, in one transaction, copying every row, then
       scoped rows are attributed (``attribute_owner``);
    3. ``history_id`` on the current table;
    4. one ``migrated`` history row for every current attestation that has none, so a
       bill of materials can reference every attestation that exists today.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trust_attestations)").fetchall()}
    if "signer_public_key" not in cols:
        conn.execute("ALTER TABLE trust_attestations ADD COLUMN signer_public_key TEXT")
        conn.commit()
        cols.add("signer_public_key")
    if "owner_uuid" not in cols:
        owners = _profile_owners(conn)
        conn.execute("BEGIN")
        try:
            conn.execute("ALTER TABLE trust_attestations RENAME TO trust_attestations_v1")
            conn.execute(_DDL_CURRENT)
            old = conn.execute(
                "SELECT id, kind, artifact_id, version_hash, verdict_json, signer_uuid, "
                "signer_fingerprint, signature, signer_public_key, signed, signed_at "
                "FROM trust_attestations_v1").fetchall()
            unassigned = 0
            for (rid, kind, aid, vh, vj, su, sf, sig, spk, signed, sat) in old:
                own = attribute_owner(kind, aid, su, owners)
                if kind in OWNER_SCOPED_KINDS and not own:
                    unassigned += 1
                conn.execute(
                    "INSERT INTO trust_attestations (id, kind, artifact_id, owner_uuid, "
                    "version_hash, verdict_json, signer_uuid, signer_fingerprint, signature, "
                    "signer_public_key, signed, signed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, kind, aid, own, vh, vj, su, sf, sig, spk, signed, sat))
            conn.execute("DROP TABLE trust_attestations_v1")
            conn.execute("COMMIT")
            logger.info(f"trust.store: owner-scoped {len(old)} attestation row(s) in {path}"
                        + (f"; {unassigned} agent row(s) could not be attributed and are "
                           "listed by unassigned_agent_attestations()" if unassigned else ""))
        except Exception:
            conn.execute("ROLLBACK")
            raise
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trust_attestations)").fetchall()}
    if "history_id" not in cols:
        conn.execute("ALTER TABLE trust_attestations ADD COLUMN history_id INTEGER")
        conn.commit()
    # This deployment's own key is the signer of every attestation made here so far.
    try:
        from trusted_data_agent.core import provenance as _prov
        _pem = _prov.get_provenance_public_key_pem()
        _fp = _prov.get_key_fingerprint()
        if _pem and _fp:
            register_key(_fp, _pem, "local", conn=conn)
    except Exception:  # noqa: BLE001 — no key here (a test DB, a keyless instance)
        pass
    # PROP-009 §4.5: the ledger, entered once with everything that exists (backfilled),
    # BEFORE anything below appends a live entry.
    _ledger.ensure(conn)
    if _ledger.backfill(conn):
        conn.commit()
    # Seed history for current rows that predate it.
    missing = conn.execute(
        "SELECT id, kind, artifact_id, owner_uuid, version_hash, verdict_json, signer_uuid, "
        "signer_fingerprint, signature, signer_public_key, signed, signed_at "
        "FROM trust_attestations WHERE history_id IS NULL").fetchall()
    if missing:
        now = time.time()
        for (rid, kind, aid, own, vh, vj, su, sf, sig, spk, signed, sat) in missing:
            hid = _append_history(conn, kind, aid, own, "migrated", vh, vj, su, sf, sig, spk,
                                  bool(signed), sat)
            conn.execute("UPDATE trust_attestations SET history_id=? WHERE id=?", (hid, rid))
        conn.commit()


def _connect(db_path: Optional[str] = None):
    path = _db_path(db_path)
    conn = sqlite_util.connect(path)
    # The current table is created by _DDL_CURRENT only on a NEW db; an existing table
    # keeps its shape until _migrate brings it forward.
    conn.execute(_DDL_CURRENT)
    conn.executescript(_DDL_HISTORY)
    conn.execute(_DDL_KEYS)
    _ledger.ensure(conn)
    if path not in _MIGRATED_PATHS:
        try:
            _migrate(conn, path)
            _MIGRATED_PATHS.add(path)
        except Exception as e:  # noqa: BLE001 — never block the connection itself
            logger.warning(f"trust.store: schema migration failed for {path}: {e}")
    return conn


def _sign_payload_str(kind: str, version_hash: str) -> str:
    """The signed payload — **content-bound, id-free**: attests that a *{kind}* with this
    exact content hash is validated. Deliberately excludes the artifact_id (and the owner)
    so the signature is **portable** — the same attestation survives a marketplace id remap
    (publish → subscribe/install), and remains verifiable cross-instance given the signer's
    public key. Security is unaffected: ``version_hash`` binds the content, so a copied
    attestation whose hash no longer matches the target's live content resolves 🟠 CHANGED.
    """
    return f"{kind}|{version_hash}"


def _legacy_payload_str(kind: str, artifact_id: str, version_hash: str) -> str:
    """The pre-portability id-bound payload — kept only so attestations signed before the
    content-bound switch still verify (verify() falls back to this)."""
    return f"{kind}|{artifact_id}|{version_hash}"


def register_key(fingerprint: Optional[str], public_key_pem: Optional[str], origin: str,
                 db_path: Optional[str] = None, conn=None) -> None:
    """Record a public key that has signed here (never raises).

    A ``local`` key seen for the first time while another ``local`` key is unretired means
    the deployment's provenance key was rotated: the older one is stamped ``retired_at`` so
    its signatures still verify and read as *made with a retired key*. A ``carried`` key
    (from a reinstated or imported attestation) is recorded for listing, never retired.
    """
    if not fingerprint:
        return
    own_conn = conn is None
    try:
        c = conn or _connect(db_path)
        try:
            if isinstance(public_key_pem, (bytes, bytearray)):
                public_key_pem = bytes(public_key_pem).decode("utf-8", "strict")
            now = time.time()
            row = c.execute("SELECT origin FROM trust_signing_keys WHERE fingerprint=?",
                            (fingerprint,)).fetchone()
            if row:
                # a carried key later seen as this deployment's own is promoted to local
                if origin == "local" and row[0] != "local":
                    c.execute("UPDATE trust_signing_keys SET origin='local' WHERE fingerprint=?",
                              (fingerprint,))
                c.execute("UPDATE trust_signing_keys SET last_seen=?, "
                          "public_key_pem=COALESCE(public_key_pem, ?) WHERE fingerprint=?",
                          (now, public_key_pem, fingerprint))
            else:
                if origin == "local":
                    c.execute("UPDATE trust_signing_keys SET retired_at=? "
                              "WHERE origin='local' AND retired_at IS NULL", (now,))
                c.execute("INSERT INTO trust_signing_keys (fingerprint, public_key_pem, origin, "
                          "first_seen, last_seen) VALUES (?,?,?,?,?)",
                          (fingerprint, public_key_pem, origin, now, now))
            c.commit()
        finally:
            if own_conn:
                c.close()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"trust.store.register_key: {e}")


def key_status(fingerprint: Optional[str], db_path: Optional[str] = None) -> str:
    """``active`` (this deployment's current key) · ``retired`` (an earlier key of this
    deployment) · ``carried`` (another signer's key, seen on a reinstated attestation) ·
    ``unknown``. Never raises."""
    if not fingerprint:
        return "unknown"
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT origin, retired_at FROM trust_signing_keys "
                               "WHERE fingerprint=?", (fingerprint,)).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return "unknown"
    if not row:
        return "unknown"
    origin, retired_at = row
    if origin == "local":
        return "retired" if retired_at else "active"
    return "carried"


def public_key_pem(fingerprint: Optional[str], db_path: Optional[str] = None) -> Optional[str]:
    """The PEM of a key that has signed here, by its fingerprint — ``None`` when unknown."""
    if not fingerprint:
        return None
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT public_key_pem FROM trust_signing_keys WHERE fingerprint=?",
                               (fingerprint,)).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    pem = row[0] if row else None
    return bytes(pem).decode("utf-8") if isinstance(pem, (bytes, bytearray)) else pem


def list_keys(db_path: Optional[str] = None) -> List[dict]:
    """Every public key that has signed here, newest first."""
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT fingerprint, public_key_pem, origin, first_seen, last_seen, retired_at "
                "FROM trust_signing_keys ORDER BY first_seen DESC").fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    keys = ("fingerprint", "public_key_pem", "origin", "first_seen", "last_seen", "retired_at")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["status"] = ("retired" if d["retired_at"] else "active") if d["origin"] == "local" \
            else "carried"
        out.append(d)
    return out


def _append_history(conn, kind: str, artifact_id: str, owner: str, event: str,
                    version_hash: Optional[str] = None, verdict_json: Optional[str] = None,
                    signer_uuid: Optional[str] = None, signer_fingerprint: Optional[str] = None,
                    signature: Optional[str] = None, signer_public_key: Optional[str] = None,
                    signed: bool = False, signed_at: Optional[float] = None,
                    actor_uuid: Optional[str] = None) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO trust_attestation_history (kind, artifact_id, owner_uuid, event, "
        "version_hash, verdict_json, signer_uuid, signer_fingerprint, signature, "
        "signer_public_key, signed, signed_at, recorded_at, actor_uuid) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, str(artifact_id), owner, event, version_hash, verdict_json, signer_uuid,
         signer_fingerprint, signature, signer_public_key, 1 if signed else 0, signed_at,
         now, actor_uuid))
    hid = int(cur.lastrowid)
    # PROP-009 §4.5: the same fact, entered in the hash-chained ledger in THIS transaction
    # (after the write above, so the write lock is held and no other writer takes the seq).
    _ledger.append_history(conn, {
        "id": hid, "kind": kind, "artifact_id": str(artifact_id), "owner_uuid": owner,
        "event": event, "version_hash": version_hash, "verdict_json": verdict_json,
        "signer_uuid": signer_uuid, "signer_fingerprint": signer_fingerprint,
        "signature": signature, "signer_public_key": signer_public_key,
        "signed": 1 if signed else 0, "signed_at": signed_at, "recorded_at": now,
        "actor_uuid": actor_uuid})
    return hid


def record_attestation(
    kind: str,
    artifact_id: str,
    version_hash: str,
    verdict: Optional[dict] = None,
    signer_uuid: Optional[str] = None,
    signer_fingerprint: Optional[str] = None,
    signature: Optional[str] = None,
    signed: bool = False,
    signed_at: Optional[float] = None,
    signer_public_key: Optional[str] = None,
    db_path: Optional[str] = None,
    owner_uuid: Optional[str] = None,
    event: str = "record",
) -> int:
    """Upsert the CURRENT attestation for ``(kind, artifact_id, owner)`` and append the same
    fact to history. Returns the history row id (0 if it could not be written)."""
    # The column is TEXT and PEM is ASCII — normalize here so no consumer ever sees
    # bytes (a bytes value slips through SQLite's dynamic typing, and then a pack
    # manifest carrying the captured claim fails json.dump; seen live).
    if isinstance(signer_public_key, (bytes, bytearray)):
        signer_public_key = bytes(signer_public_key).decode("utf-8", "strict")
    own = owner_key(kind, owner_uuid)
    vj = json.dumps(verdict) if verdict is not None else None
    sat = signed_at if signed_at is not None else time.time()
    conn = _connect(db_path)
    try:
        hid = _append_history(conn, kind, artifact_id, own, event, version_hash, vj,
                              signer_uuid, signer_fingerprint, signature, signer_public_key,
                              signed, sat)
        conn.execute(
            """
            INSERT INTO trust_attestations
                (kind, artifact_id, owner_uuid, version_hash, verdict_json, signer_uuid,
                 signer_fingerprint, signature, signer_public_key, signed, signed_at, history_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, artifact_id, owner_uuid) DO UPDATE SET
                version_hash=excluded.version_hash,
                verdict_json=excluded.verdict_json,
                signer_uuid=excluded.signer_uuid,
                signer_fingerprint=excluded.signer_fingerprint,
                signature=excluded.signature,
                signer_public_key=excluded.signer_public_key,
                signed=excluded.signed,
                signed_at=excluded.signed_at,
                history_id=excluded.history_id
            """,
            (kind, str(artifact_id), own, version_hash, vj, signer_uuid, signer_fingerprint,
             signature, signer_public_key, 1 if signed else 0, sat, hid),
        )
        if signer_fingerprint and signer_public_key and event == "reinstate":
            register_key(signer_fingerprint, signer_public_key, "carried", conn=conn)
        conn.commit()
        return hid
    finally:
        conn.close()


def _row_to_rec(row) -> dict:
    rec = dict(zip(_COLUMNS, row))
    rec["signed"] = bool(rec["signed"])
    if rec.get("verdict_json"):
        try:
            rec["verdict"] = json.loads(rec["verdict_json"])
        except Exception:
            rec["verdict"] = None
    return rec


def load_attestation(kind: str, artifact_id: str, db_path: Optional[str] = None,
                     owner_uuid: Optional[str] = None) -> Optional[dict]:
    """Return the current attestation for ``(kind, artifact_id, owner)``, or None."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM trust_attestations "
            "WHERE kind=? AND artifact_id=? AND owner_uuid=?",
            (kind, str(artifact_id), owner_key(kind, owner_uuid)),
        ).fetchone()
        return _row_to_rec(row) if row is not None else None
    finally:
        conn.close()


def attested_hash(kind: str, artifact_id: str, db_path: Optional[str] = None,
                  owner_uuid: Optional[str] = None) -> Optional[str]:
    """Convenience: the recorded ``version_hash`` for an artifact, or None if unsigned.

    TC-5: a row whose Ed25519 signature was never produced (``signed=0`` — the provenance
    key was unavailable at sign time, see ``sign()``'s graceful degradation) is a row in a
    writable table, not a cryptographic attestation. It must not read 🟢 *anywhere* —
    ingredient, agent, deployment or pack composition all flow through this one accessor —
    so the recorded hash is surfaced only for a genuinely-signed row. This mirrors
    ``portability.capture``, which already refuses to travel an unsigned row.
    """
    rec = load_attestation(kind, artifact_id, db_path=db_path, owner_uuid=owner_uuid)
    return rec["version_hash"] if (rec and rec.get("signed")) else None


def sign(
    kind: str,
    artifact_id: str,
    version_hash: str,
    verdict: Optional[dict] = None,
    signer_uuid: Optional[str] = None,
    signed_at: Optional[float] = None,
    db_path: Optional[str] = None,
    owner_uuid: Optional[str] = None,
    event: str = "sign",
) -> dict:
    """Sign an artifact: Ed25519-sign ``kind|version_hash`` and persist (current + history).

    ``event`` names the act in history: ``sign`` (a human sign-off, the default) or ``renew``
    (re-issued by a scheduled sync under the owner's standing policy — see
    ``core/trust/renewal.py``). Any other value is refused: the history grammar is closed.

    Returns the verifiable envelope from ``sign_payload`` (``payload``,
    ``key_fingerprint``, ``signature``, ``signed``) plus ``history_id``. Degrades gracefully
    to an unsigned-but-recorded attestation if the provenance key is unavailable.

    Concurrency (TC-69, accepted-risk, documented): a caller hashes content and then signs
    it — content changing in between records a hash the next resolution reads as CHANGED
    (the fail-safe direction). Concurrent sign-offs on one artifact are last-write-wins via
    the ``ON CONFLICT`` upsert (each write atomic under sqlite_util's process lock); both
    remain in history.
    """
    if event not in ("sign", "renew"):
        raise ValueError(f"sign(): event must be 'sign' or 'renew', not {event!r}")
    from trusted_data_agent.eval.online import sign_payload
    env = sign_payload(_sign_payload_str(kind, version_hash))
    # Carry this instance's provenance PUBLIC key so a cross-instance copy can verify
    # against the SIGNER's key (not the consumer's local key).
    pub_pem = None
    try:
        from trusted_data_agent.core import provenance
        pub_pem = provenance.get_provenance_public_key_pem()
    except Exception:
        pub_pem = None
    if isinstance(pub_pem, (bytes, bytearray)):
        pub_pem = bytes(pub_pem).decode("utf-8", "strict")
    hid = record_attestation(
        kind, artifact_id, version_hash,
        verdict=verdict,
        signer_uuid=signer_uuid,
        signer_fingerprint=env.get("key_fingerprint"),
        signature=env.get("signature"),
        signed=bool(env.get("signed")),
        signed_at=signed_at,
        signer_public_key=pub_pem,
        db_path=db_path,
        owner_uuid=owner_uuid,
        event=event,
    )
    if env.get("signed") and env.get("key_fingerprint"):
        register_key(env.get("key_fingerprint"), pub_pem, "local", db_path=db_path)
    return {**env, "history_id": hid}


def verify(kind: str, artifact_id: str, db_path: Optional[str] = None,
           owner_uuid: Optional[str] = None) -> dict:
    """Offline-style verification: re-check the stored attestation's Ed25519 signature
    against the reconstructed payload, using the provenance *public* key.

    Returns ``{found, signed, verified, fingerprint, version_hash, key_source, key_status}``.
    Never raises.

    **Design limit (TC-65, documented so it is never mistaken for publisher
    authentication):** verification prefers the public key CARRIED IN THE ROW — anyone who
    can write ``trust_attestations`` can re-sign with their own key and store their own
    pubkey, and ``verified`` will read True. What verify() proves is that the signature is
    internally consistent with the named key, not that the key belongs to a trusted
    publisher; pinning signer identities is the (deliberately deferred) trusted-publisher
    policy. ``key_source`` names which key was used so callers can render the distinction.
    """
    rec = load_attestation(kind, artifact_id, db_path=db_path, owner_uuid=owner_uuid)
    if not rec:
        return {"found": False, "signed": False, "verified": False}
    out = verify_record(rec)
    out["key_status"] = key_status(rec.get("signer_fingerprint"), db_path=db_path)
    return out


def verify_record(rec: dict) -> dict:
    """Verify one attestation record (current or history row) — pure apart from reading
    this deployment's public key as the fallback. Never raises."""
    import base64
    if not (rec.get("signed") and rec.get("signature")):
        return {"found": True, "signed": False, "verified": False,
                "version_hash": rec.get("version_hash")}
    verified = False
    key_source = None
    try:
        from trusted_data_agent.core import provenance
        # Prefer the signer's carried public key (cross-instance); fall back to this
        # instance's provenance key (same-instance signatures without a stored pubkey).
        pub = None
        if rec.get("signer_public_key"):
            _pk = rec["signer_public_key"]
            if isinstance(_pk, str):          # stored as TEXT (current form)
                _pk = _pk.encode("utf-8")
            pub = provenance._load_public_key(_pk)
            key_source = "carried" if pub is not None else None
        if pub is None:
            pub = provenance._load_public_key(provenance.get_provenance_public_key_pem())
            key_source = "local" if pub is not None else None
        if pub is not None:
            sig = base64.b64decode(rec["signature"])
            # content-bound payload first; fall back to the legacy id-bound payload so
            # attestations signed before the portability switch still verify.
            for payload in (_sign_payload_str(rec["kind"], rec["version_hash"]),
                            _legacy_payload_str(rec["kind"], rec["artifact_id"],
                                                rec["version_hash"])):
                try:
                    pub.verify(sig, payload.encode("utf-8"))
                    verified = True
                    break
                except Exception:
                    continue
    except Exception as e:
        logger.debug(f"trust.store.verify: signature check failed: {e}")
    return {"found": True, "signed": True, "verified": verified,
            "fingerprint": rec.get("signer_fingerprint"), "version_hash": rec.get("version_hash"),
            "key_source": key_source}


def _end_current(conn, kind: str, artifact_id: str, owner: str, event: str,
                 actor_uuid: Optional[str]) -> int:
    """Remove a current row and record WHY in history (revoke / retire). Returns rows removed."""
    row = conn.execute(
        "SELECT version_hash, signer_uuid, signer_fingerprint FROM trust_attestations "
        "WHERE kind=? AND artifact_id=? AND owner_uuid=?",
        (kind, str(artifact_id), owner)).fetchone()
    if row is None:
        return 0
    _append_history(conn, kind, artifact_id, owner, event, version_hash=row[0],
                    signer_uuid=row[1], signer_fingerprint=row[2], actor_uuid=actor_uuid)
    cur = conn.execute(
        "DELETE FROM trust_attestations WHERE kind=? AND artifact_id=? AND owner_uuid=?",
        (kind, str(artifact_id), owner))
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def revoke(kind: str, artifact_id: str, db_path: Optional[str] = None,
           owner_uuid: Optional[str] = None, actor_uuid: Optional[str] = None) -> None:
    """End an artifact's attestation (it reverts to ⚪ unsigned). History keeps it."""
    conn = _connect(db_path)
    try:
        _end_current(conn, kind, artifact_id, owner_key(kind, owner_uuid), "revoke", actor_uuid)
        conn.commit()
    finally:
        conn.close()


def retire(artifact_id: str, kinds, db_path: Optional[str] = None,
           owner_uuid: Optional[str] = None) -> int:
    """#30 — a signature must not outlive its subject.

    Called from the artifact DELETION chokepoints (profile removal, collection
    removal, KG deletion) so every deletion path — pack uninstall, the REST delete
    endpoints, marketplace flows — retires the attestation with the artifact.
    Without this the row dangles, and because collection ids are small integers a
    LATER collection can inherit a dead signature: content-bound hashing makes it
    read 🟠 "signed then modified" — a history the new artifact never had.

    ``kinds`` is a list because a caller may not know which kind the artifact was
    signed under (a collection is `knowledge` or `planner` by repository_type);
    a row that does not exist is a clean no-op. For an owner-scoped kind only the
    named owner's row is retired — deleting a bootstrap agent in one account must
    never retire another account's signature. History keeps every retired row.
    Returns rows removed. Never raises — deletion of the artifact must not fail on
    trust cleanup.
    """
    removed = 0
    try:
        conn = _connect(db_path)
        try:
            for kind in kinds:
                removed += _end_current(conn, kind, artifact_id,
                                        owner_key(kind, owner_uuid), "retire", owner_uuid)
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — cleanup must never block the deletion itself
        pass
    return removed


# ── history reads ─────────────────────────────────────────────────────────────

_HIST_COLUMNS = ("id", "kind", "artifact_id", "owner_uuid", "event", "version_hash",
                 "verdict_json", "signer_uuid", "signer_fingerprint", "signature",
                 "signer_public_key", "signed", "signed_at", "recorded_at", "actor_uuid")


def _hist_rec(row) -> dict:
    rec = dict(zip(_HIST_COLUMNS, row))
    rec["signed"] = bool(rec["signed"])
    if rec.get("verdict_json"):
        try:
            rec["verdict"] = json.loads(rec["verdict_json"])
        except Exception:
            rec["verdict"] = None
    return rec


def history(kind: str, artifact_id: str, db_path: Optional[str] = None,
            owner_uuid: Optional[str] = None, limit: int = 200) -> List[dict]:
    """Every recorded event for one artifact (and owner), newest first."""
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"SELECT {', '.join(_HIST_COLUMNS)} FROM trust_attestation_history "
                "WHERE kind=? AND artifact_id=? AND owner_uuid=? ORDER BY id DESC LIMIT ?",
                (kind, str(artifact_id), owner_key(kind, owner_uuid), int(limit))).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    return [_hist_rec(r) for r in rows]


def history_entry(history_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """One history row by id (what a bill of materials references), or None."""
    try:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                f"SELECT {', '.join(_HIST_COLUMNS)} FROM trust_attestation_history WHERE id=?",
                (int(history_id),)).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None
    return _hist_rec(row) if row else None


def current_history_id(kind: str, artifact_id: str, db_path: Optional[str] = None,
                       owner_uuid: Optional[str] = None) -> Optional[int]:
    """The history row behind the CURRENT signed attestation, or None if unsigned."""
    rec = load_attestation(kind, artifact_id, db_path=db_path, owner_uuid=owner_uuid)
    if not rec or not rec.get("signed"):
        return None
    return rec.get("history_id")


def signed_history_for_hash(kind: str, version_hash: str, db_path: Optional[str] = None,
                            limit: int = 20) -> List[dict]:
    """Every signed history row of a kind with exactly this content hash, newest first —
    how an export finds the signature that covered a version that is no longer current."""
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"SELECT {', '.join(_HIST_COLUMNS)} FROM trust_attestation_history "
                "WHERE kind=? AND version_hash=? AND signed=1 AND event IN "
                f"({','.join('?' * len(_ESTABLISHING_EVENTS))}) ORDER BY id DESC LIMIT ?",
                (kind, version_hash, *_ESTABLISHING_EVENTS, int(limit))).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    return [_hist_rec(r) for r in rows]


def unassigned_agent_attestations(db_path: Optional[str] = None) -> List[dict]:
    """Owner-scoped rows the migration could not attribute (``owner_uuid = ''``) — listed
    for an administrator's review, never assigned by guess. No caller that passes an owner
    ever reads them."""
    try:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM trust_attestations WHERE owner_uuid='' "
                f"AND kind IN ({','.join('?' * len(OWNER_SCOPED_KINDS))})",
                tuple(sorted(OWNER_SCOPED_KINDS))).fetchall()
            owners = _profile_owners(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        rec = _row_to_rec(r)
        holders = sorted(owners.get(str(rec["artifact_id"])) or [])
        rec["holders"] = holders
        # why it could not be attributed — the review list says so in words
        rec["reason"] = ("no account holds this agent any more (its profile was deleted)"
                         if not holders else
                         f"{len(holders)} accounts hold this agent id and none of them signed it")
        out.append(rec)
    return out


def assign_owner(kind: str, artifact_id: str, owner_uuid: str,
                 db_path: Optional[str] = None, actor_uuid: Optional[str] = None) -> bool:
    """An administrator's resolution of an unassigned row: give it to ``owner_uuid``.
    Refuses (False) if the kind is not scoped, the row is not unassigned, or that owner
    already has a current attestation for this artifact."""
    if kind not in OWNER_SCOPED_KINDS or not owner_uuid:
        return False
    try:
        conn = _connect(db_path)
        try:
            if conn.execute("SELECT 1 FROM trust_attestations WHERE kind=? AND artifact_id=? "
                            "AND owner_uuid=?", (kind, str(artifact_id), owner_uuid)).fetchone():
                return False
            cur = conn.execute("UPDATE trust_attestations SET owner_uuid=? WHERE kind=? AND "
                               "artifact_id=? AND owner_uuid=''",
                               (owner_uuid, kind, str(artifact_id)))
            if not cur.rowcount:
                return False
            conn.execute("UPDATE trust_attestation_history SET owner_uuid=? WHERE kind=? AND "
                         "artifact_id=? AND owner_uuid=''", (owner_uuid, kind, str(artifact_id)))
            _append_history(conn, kind, artifact_id, owner_uuid, "assigned",
                            actor_uuid=actor_uuid)
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust.store.assign_owner failed: {e}")
        return False


def list_current(db_path: Optional[str] = None, owner_uuid: Optional[str] = None,
                 kinds: Optional[Iterable[str]] = None) -> List[dict]:
    """Every current attestation (optionally limited to kinds). With ``owner_uuid`` set,
    owner-scoped kinds are limited to that owner; other kinds are returned whole (their
    ownership is decided by the caller, who knows which artifacts the user can see)."""
    try:
        conn = _connect(db_path)
        try:
            sql = f"SELECT {', '.join(_COLUMNS)} FROM trust_attestations"
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    ks = set(kinds) if kinds else None
    out = []
    for r in rows:
        rec = _row_to_rec(r)
        if ks is not None and rec["kind"] not in ks:
            continue
        if owner_uuid is not None and rec["kind"] in OWNER_SCOPED_KINDS \
                and rec["owner_uuid"] != str(owner_uuid):
            continue
        out.append(rec)
    return out
