"""
The evidence ledger (PROP-009 §4.5) — an append-only, hash-chained record of every attestation
event and every answer's sealed record, so that editing, inserting or removing history after the
fact is DETECTABLE.

Each entry binds one fact by its digest and links to the entry before it::

    entry_hash = sha256("ledger:v1:<seq>:<entry_type>:<ref>:<digest>:<prev_hash>")

  attestation  ref ``h:<history id>``; digest = sha256 of the history row's canonical record
               (``history_record``) — every field that says what was attested, by whom, when.
  turn         ref = sha256(owner|session|turn) (no identifier leaks into a deployment-wide
               table); digest = sha256("turn:v1:<chain tip>:<bill-of-materials digest>").
               Appended when the turn's record is first written or changes — never twice for
               the same content.

The entry is written in the SAME transaction as the fact it records, after the fact's own
write (so the database's write lock is already held and two writers cannot take one sequence
number). ``verify`` walks the chain and cross-checks it against the tables it records:

  break       a sequence gap, a broken link, or an entry whose hash does not recompute;
  edited      a history row or turn record that no longer matches the digest it was entered with;
  removed     an entry whose history row or turn record is gone (pruning);
  unledgered  a history row written after the ledger began that has no entry (written around it).

A **checkpoint** is the tip signed with the deployment key; exports carry one, and a later
``verify(checkpoints=[...])`` proves the chain still contains every earlier tip — a truncated or
rewritten ledger cannot reproduce a tip it no longer has. Honest limit, stated wherever this is
shown: the host administrator holds the signing key and could rebuild the whole chain; external
anchoring of checkpoints (PROP-009 §4.6) is what removes that.

Existing history and turn records are entered once, as ``backfilled``, when the ledger is first
created: tamper-evident from that moment, and said so.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("quart.app")

GENESIS = "0" * 64
VERSION = "ledger:v1"

DDL = """
CREATE TABLE IF NOT EXISTS trust_ledger (
    seq          INTEGER PRIMARY KEY,
    entry_type   TEXT    NOT NULL,   -- 'attestation' | 'turn'
    ref          TEXT    NOT NULL,
    digest       TEXT    NOT NULL,
    prev_hash    TEXT    NOT NULL,
    entry_hash   TEXT    NOT NULL,
    recorded_at  REAL    NOT NULL,
    backfilled   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_trust_ledger_ref ON trust_ledger (ref);
"""

#: Fields of a history row the ledger binds, and how each is normalized so the value written
#: and the value read back (SQLite REAL / INTEGER affinity) hash identically.
_FLOAT = ("signed_at", "recorded_at")
_INT = ("id", "signed")
HISTORY_FIELDS = ("id", "kind", "artifact_id", "owner_uuid", "event", "version_hash",
                  "verdict_json", "signer_uuid", "signer_fingerprint", "signature",
                  "signer_public_key", "signed", "signed_at", "recorded_at", "actor_uuid")


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def history_record(row: Dict[str, Any]) -> Dict[str, Any]:
    """The canonical record of one history row — what the ledger digest binds."""
    out = {}
    for f in HISTORY_FIELDS:
        v = row.get(f)
        if isinstance(v, (bytes, bytearray)):
            v = bytes(v).decode("utf-8", "replace")
        if v is not None and f in _FLOAT:
            v = float(v)
        elif v is not None and f in _INT:
            v = int(bool(v)) if f == "signed" else int(v)
        elif v is not None and f not in _FLOAT and f not in _INT:
            v = str(v)
        out[f] = v
    return out


def record_digest(record: Dict[str, Any]) -> str:
    return _sha(canonical(record))


def turn_ref(owner_uuid: str, session_id: str, turn: Any) -> str:
    return _sha(f"turn-ref:{owner_uuid}|{session_id}|{turn}")


def turn_digest(chain_tip_hash: Optional[str], bom_digest: Optional[str]) -> str:
    return _sha(f"turn:v1:{chain_tip_hash or ''}:{bom_digest or ''}")


def entry_hash(seq: int, entry_type: str, ref: str, digest: str, prev_hash: str) -> str:
    return _sha(f"{VERSION}:{int(seq)}:{entry_type}:{ref}:{digest}:{prev_hash}")


def ensure(conn) -> None:
    conn.executescript(DDL)


def _tip(conn):
    row = conn.execute("SELECT seq, entry_hash FROM trust_ledger ORDER BY seq DESC LIMIT 1").fetchone()
    return (int(row[0]), row[1]) if row else (0, GENESIS)


def append(conn, entry_type: str, ref: str, digest: str, backfilled: bool = False,
           at: Optional[float] = None) -> int:
    """Append one entry in the caller's transaction. Returns its sequence number."""
    seq, prev = _tip(conn)
    seq += 1
    conn.execute(
        "INSERT INTO trust_ledger (seq, entry_type, ref, digest, prev_hash, entry_hash, "
        "recorded_at, backfilled) VALUES (?,?,?,?,?,?,?,?)",
        (seq, entry_type, ref, digest, prev, entry_hash(seq, entry_type, ref, digest, prev),
         at if at is not None else time.time(), 1 if backfilled else 0))
    return seq


def append_history(conn, row: Dict[str, Any]) -> int:
    rec = history_record(row)
    return append(conn, "attestation", f"h:{rec['id']}", record_digest(rec))


def append_turn(conn, owner_uuid: str, session_id: str, turn: Any,
                chain_tip_hash: Optional[str], bom_digest: Optional[str]) -> Optional[int]:
    """Enter a turn's record — only when it is new or its content changed."""
    ref = turn_ref(owner_uuid, session_id, turn)
    dg = turn_digest(chain_tip_hash, bom_digest)
    last = conn.execute("SELECT digest FROM trust_ledger WHERE ref=? ORDER BY seq DESC LIMIT 1",
                        (ref,)).fetchone()
    if last and last[0] == dg:
        return None
    return append(conn, "turn", ref, dg)


def _history_rows(conn) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute(f"SELECT {', '.join(HISTORY_FIELDS)} FROM trust_attestation_history "
                            "ORDER BY id").fetchall()
    except Exception:  # noqa: BLE001 — no history table on this db
        return []
    return [dict(zip(HISTORY_FIELDS, r)) for r in rows]


def _turn_rows(conn) -> List[tuple]:
    try:
        return conn.execute("SELECT owner_uuid, session_id, turn, chain_tip_hash, bom_json "
                            "FROM evidence_turns").fetchall()
    except Exception:  # noqa: BLE001 — no evidence index on this db
        return []


def _bom_digest_of(bom_json: Optional[str]) -> Optional[str]:
    if not bom_json:
        return None
    try:
        from trusted_data_agent.core.trust.evidence import bom_digest
        return bom_digest(json.loads(bom_json))
    except Exception:  # noqa: BLE001
        return None


def backfill(conn) -> int:
    """Enter every existing history row and turn record once, when the ledger is empty."""
    if conn.execute("SELECT 1 FROM trust_ledger LIMIT 1").fetchone():
        return 0
    n = 0
    now = time.time()
    for row in _history_rows(conn):
        rec = history_record(row)
        append(conn, "attestation", f"h:{rec['id']}", record_digest(rec), backfilled=True, at=now)
        n += 1
    for (own, sid, tn, tip, bj) in _turn_rows(conn):
        append(conn, "turn", turn_ref(own, sid, tn), turn_digest(tip, _bom_digest_of(bj)),
               backfilled=True, at=now)
        n += 1
    return n


def entries(conn, refs: Optional[Iterable[str]] = None, since_seq: Optional[int] = None) -> List[Dict[str, Any]]:
    cols = ("seq", "entry_type", "ref", "digest", "prev_hash", "entry_hash", "recorded_at", "backfilled")
    if since_seq is not None:
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM trust_ledger WHERE seq>=? ORDER BY seq",
                            (int(since_seq),)).fetchall()
    elif refs is not None:
        refs = list(refs)
        if not refs:
            return []
        q = ",".join("?" * len(refs))
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM trust_ledger WHERE ref IN ({q}) ORDER BY seq",
                            refs).fetchall()
    else:
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM trust_ledger ORDER BY seq").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def latest_entry(conn, ref: str) -> Optional[Dict[str, Any]]:
    got = [e for e in entries(conn, refs=[ref])]
    return got[-1] if got else None


def verify(conn, checkpoints: Optional[List[Dict[str, Any]]] = None, detail: int = 50) -> Dict[str, Any]:
    """Walk the chain and cross-check it against the tables it records. Never raises."""
    out = {"intact": True, "entries": 0, "tip_seq": 0, "tip_hash": GENESIS, "backfilled": 0,
           "first_live_seq": None, "breaks": [], "edited": [], "removed": [], "unledgered": [],
           "checkpoints": []}
    try:
        rows = entries(conn)
    except Exception as e:  # noqa: BLE001
        out.update(intact=None, error=f"the ledger could not be read: {e}")
        return out
    prev, expect = GENESIS, 1
    by_ref: Dict[str, Dict[str, Any]] = {}
    hash_at: Dict[int, str] = {}
    for r in rows:
        if r["seq"] != expect:
            out["breaks"].append({"seq": r["seq"], "why": f"sequence gap — expected {expect}"})
        if r["prev_hash"] != prev:
            out["breaks"].append({"seq": r["seq"], "why": "does not link to the entry before it"})
        if entry_hash(r["seq"], r["entry_type"], r["ref"], r["digest"], r["prev_hash"]) != r["entry_hash"]:
            out["breaks"].append({"seq": r["seq"], "why": "entry hash does not recompute"})
        prev, expect = r["entry_hash"], r["seq"] + 1
        by_ref[r["ref"]] = r
        hash_at[r["seq"]] = r["entry_hash"]
        if r["backfilled"]:
            out["backfilled"] += 1
        elif out["first_live_seq"] is None:
            out["first_live_seq"] = r["seq"]
    out["entries"] = len(rows)
    if rows:
        out["tip_seq"], out["tip_hash"] = rows[-1]["seq"], rows[-1]["entry_hash"]
    hist = {f"h:{r['id']}": r for r in _history_rows(conn)}
    for ref, r in hist.items():
        e = by_ref.get(ref)
        if e is None:
            out["unledgered"].append({"ref": ref, "kind": r.get("kind"), "event": r.get("event")})
        elif record_digest(history_record(r)) != e["digest"]:
            out["edited"].append({"ref": ref, "seq": e["seq"], "kind": r.get("kind"), "event": r.get("event")})
    turns = {turn_ref(o, s, t): (tip, bj) for (o, s, t, tip, bj) in _turn_rows(conn)}
    for ref, (tip, bj) in turns.items():
        e = by_ref.get(ref)
        if e is None:
            out["unledgered"].append({"ref": ref, "kind": "turn"})
        elif turn_digest(tip, _bom_digest_of(bj)) != e["digest"]:
            out["edited"].append({"ref": ref, "seq": e["seq"], "kind": "turn"})
    for ref, e in by_ref.items():
        if (e["entry_type"] == "attestation" and ref not in hist) or (e["entry_type"] == "turn" and ref not in turns):
            out["removed"].append({"ref": ref, "seq": e["seq"], "entry_type": e["entry_type"]})
    for cp in checkpoints or []:
        try:
            seq, tip = int(cp.get("seq")), cp.get("tip_hash")
        except Exception:  # noqa: BLE001
            out["checkpoints"].append({"ok": False, "why": "unreadable checkpoint"})
            continue
        have = hash_at.get(seq)
        out["checkpoints"].append({"seq": seq, "ok": have == tip,
                                   "why": None if have == tip else
                                   ("the ledger no longer reaches this checkpoint" if have is None
                                    else "the ledger was rewritten since this checkpoint")})
    bad = out["breaks"] or out["edited"] or out["removed"] or out["unledgered"] or \
        any(not c["ok"] for c in out["checkpoints"])
    out["intact"] = not bad
    for k in ("breaks", "edited", "removed", "unledgered"):
        out[f"{k}_count"] = len(out[k])
        out[k] = out[k][:detail]
    return out


def checkpoint(conn) -> Dict[str, Any]:
    """The tip, signed with the deployment key (``ledger:v1:checkpoint:<seq>:<tip>``)."""
    seq, tip = _tip(conn)
    payload = f"{VERSION}:checkpoint:{seq}:{tip}"
    out = {"seq": seq, "tip_hash": tip, "payload": payload, "at": time.time(),
           "signed": False, "signature": None, "key_fingerprint": None}
    try:
        from trusted_data_agent.eval.online import sign_payload
        s = sign_payload(payload)
        out.update(signed=bool(s.get("signed")), signature=s.get("signature") or None,
                   key_fingerprint=s.get("key_fingerprint"))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"trust.ledger: checkpoint not signed: {e}")
    return out
