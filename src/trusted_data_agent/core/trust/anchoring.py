"""
External anchoring of the evidence ledger (PROP-009 §4.6) — publish signed checkpoints of the
ledger tip OUTSIDE this host, so that rewriting the past conflicts with what was already
published. Off unless ``EVIDENCE_ANCHOR`` is set; with it off nothing here runs.

Sinks (``APP_CONFIG.EVIDENCE_ANCHOR``, env ``TDA_EVIDENCE_ANCHOR``):

  file      append the checkpoint as one JSON line to ``EVIDENCE_ANCHOR_TARGET`` — a path on
            storage this host cannot rewrite (a WORM bucket mount, an append-only volume, a
            synced folder owned by someone else).
  webhook   POST the checkpoint as JSON to ``EVIDENCE_ANCHOR_TARGET`` (an append-only log, a
            transparency service, a ticketing system); ``EVIDENCE_ANCHOR_TOKEN`` adds a bearer.
  rfc3161   ask a Time-Stamp Authority at ``EVIDENCE_ANCHOR_TARGET`` to timestamp
            sha256(checkpoint payload) (RFC 3161). The authority's signed token is kept as the
            receipt; anyone can check it with ``openssl ts -verify``.

Every anchor is recorded in ``trust_ledger_anchors`` (seq, tip, sink, receipt, ok). The ledger
check (``GET /v1/evidence/ledger``) re-proves the ledger still contains every anchored tip. The
record of anchors lives on this host too; the EXTERNAL copy is the proof — the in-app check is
a convenience, and the reading says so.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("quart.app")

DDL = """
CREATE TABLE IF NOT EXISTS trust_ledger_anchors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    seq          INTEGER NOT NULL,
    tip_hash     TEXT    NOT NULL,
    sink         TEXT    NOT NULL,
    target       TEXT,
    ok           INTEGER NOT NULL,
    receipt      TEXT,
    error        TEXT,
    anchored_at  REAL    NOT NULL
);
"""

SINKS = ("file", "webhook", "rfc3161")


def config() -> Dict[str, Any]:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return {"sink": str(getattr(APP_CONFIG, "EVIDENCE_ANCHOR", "") or "").lower(),
                "target": str(getattr(APP_CONFIG, "EVIDENCE_ANCHOR_TARGET", "") or ""),
                "token": str(getattr(APP_CONFIG, "EVIDENCE_ANCHOR_TOKEN", "") or ""),
                "interval": int(getattr(APP_CONFIG, "EVIDENCE_ANCHOR_INTERVAL_SECONDS", 3600) or 3600)}
    except Exception:  # noqa: BLE001
        return {"sink": "", "target": "", "token": "", "interval": 3600}


def enabled() -> bool:
    c = config()
    return c["sink"] in SINKS and bool(c["target"])


# ── RFC 3161 (a minimal DER encoder/decoder — no dependency) ──────────────────

_SHA256_OID = bytes.fromhex("0609608648016503040201")   # OID 2.16.840.1.101.3.4.2.1


def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(body)) + body


def timestamp_request(digest: bytes, nonce: Optional[int] = None, cert_req: bool = True) -> bytes:
    """A DER ``TimeStampReq`` for a SHA-256 digest (RFC 3161 §2.4.1)."""
    alg = _tlv(0x30, _SHA256_OID + b"\x05\x00")
    imprint = _tlv(0x30, alg + _tlv(0x04, digest))
    body = _tlv(0x02, b"\x01") + imprint
    if nonce is not None:
        nb = nonce.to_bytes((nonce.bit_length() + 8) // 8 or 1, "big")
        body += _tlv(0x02, nb)
    if cert_req:
        body += _tlv(0x01, b"\xff")
    return _tlv(0x30, body)


def _read_tlv(buf: bytes, i: int):
    tag = buf[i]
    ln = buf[i + 1]
    j = i + 2
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[j:j + n], "big")
        j += n
    return tag, buf[j:j + ln], j + ln


def response_status(resp: bytes) -> Optional[int]:
    """The PKIStatus of a DER ``TimeStampResp`` (0 granted, 1 granted with mods, 2+ refused)."""
    try:
        tag, body, _ = _read_tlv(resp, 0)
        if tag != 0x30:
            return None
        tag, status_info, _ = _read_tlv(body, 0)
        if tag != 0x30:
            return None
        tag, status, _ = _read_tlv(status_info, 0)
        return int.from_bytes(status, "big") if tag == 0x02 else None
    except Exception:  # noqa: BLE001
        return None


# ── publishing ───────────────────────────────────────────────────────────────

def _publish(cp: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    sink, target = cfg["sink"], cfg["target"]
    record = {"format": "uderia.evidence.ledger.checkpoint", **cp}
    if sink == "file":
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        return {"ok": True, "receipt": f"appended to {target}"}
    import httpx
    if sink == "webhook":
        headers = {"Content-Type": "application/json"}
        if cfg.get("token"):
            headers["Authorization"] = f"Bearer {cfg['token']}"
        r = httpx.post(target, json=record, headers=headers, timeout=15)
        return {"ok": 200 <= r.status_code < 300, "receipt": f"HTTP {r.status_code}",
                "error": None if r.status_code < 300 else r.text[:300]}
    if sink == "rfc3161":
        digest = hashlib.sha256(cp["payload"].encode("utf-8")).digest()
        req = timestamp_request(digest, nonce=int(time.time() * 1000))
        r = httpx.post(target, content=req, headers={"Content-Type": "application/timestamp-query"},
                       timeout=20)
        status = response_status(r.content) if r.status_code == 200 else None
        ok = status in (0, 1)
        return {"ok": ok, "receipt": base64.b64encode(r.content).decode("ascii") if ok else None,
                "error": None if ok else f"HTTP {r.status_code}, PKIStatus {status}"}
    raise ValueError(f"unknown anchor sink {sink!r}")


def anchor_now(force: bool = False, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Sign the current tip and publish it. Skipped when the tip has not moved since the last
    successful anchor (unless ``force``). Never raises; the outcome is recorded."""
    cfg = config()
    if not enabled():
        return {"anchored": False, "reason": "anchoring is off"}
    from trusted_data_agent.core.trust import store as _store, ledger as _ledger
    conn = _store._connect(db_path)
    try:
        conn.executescript(DDL)
        cp = _ledger.checkpoint(conn)
        # Skip only when the SAME tip (sequence AND hash) was already published: a ledger
        # truncated and re-extended reuses a sequence number with a different hash, and that
        # rewritten tip must be published so it conflicts with what went out before.
        last = conn.execute("SELECT seq, tip_hash FROM trust_ledger_anchors WHERE ok=1 "
                            "ORDER BY id DESC LIMIT 1").fetchone()
        if not force and last and int(last[0]) == cp["seq"] and last[1] == cp["tip_hash"]:
            return {"anchored": False, "reason": "the tip has not moved since the last anchor", "seq": cp["seq"]}
        if not cp.get("signed"):
            res = {"ok": False, "error": "the checkpoint could not be signed"}
        else:
            try:
                res = _publish(cp, cfg)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "error": str(e)[:300]}
        conn.execute("INSERT INTO trust_ledger_anchors (seq, tip_hash, sink, target, ok, receipt, error, "
                     "anchored_at) VALUES (?,?,?,?,?,?,?,?)",
                     (cp["seq"], cp["tip_hash"], cfg["sink"], cfg["target"], 1 if res.get("ok") else 0,
                      res.get("receipt"), res.get("error"), time.time()))
        conn.commit()
    finally:
        conn.close()
    if not res.get("ok"):
        logger.warning(f"[evidence] ledger anchor to {cfg['sink']} failed: {res.get('error')}")
    return {"anchored": bool(res.get("ok")), "seq": cp["seq"], "tip_hash": cp["tip_hash"],
            "sink": cfg["sink"], "error": res.get("error")}


def anchors(conn, limit: int = 1000) -> List[Dict[str, Any]]:
    try:
        conn.executescript(DDL)
        rows = conn.execute("SELECT seq, tip_hash, sink, ok, anchored_at, error FROM trust_ledger_anchors "
                            "ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"seq": r[0], "tip_hash": r[1], "sink": r[2], "ok": bool(r[3]), "anchored_at": r[4],
             "error": r[5]} for r in rows]


async def anchor_loop() -> None:
    """Anchor every ``EVIDENCE_ANCHOR_INTERVAL_SECONDS`` while anchoring is on."""
    import asyncio
    while True:
        try:
            if enabled():
                await asyncio.to_thread(anchor_now)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[evidence] anchor loop: {e}")
        await asyncio.sleep(max(60, config()["interval"]))
