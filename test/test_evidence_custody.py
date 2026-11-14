"""PROP-009 increment 7 — key custody (opt-in) and external anchoring of the ledger.

CI-pure: real Ed25519 keys (the deployment key and a throwaway "external" key behind a real
signing subprocess), temp SQLite stores, a temp file sink, a faked HTTP layer. No network.

  §A  The default is byte-identical: with the local signer every signature — a chain step,
      ``sign_payload``, ``sign_attestation`` — equals the direct key call, and the fingerprint
      and public key are unchanged.
  §B  The command signer: signatures come from the external process and verify against ITS
      public key; the fingerprint and public key the platform reports are the external key's;
      the local key file is never loaded; a chain sealed with it verifies.
  §C  A signer is never trusted blindly: a command that returns a wrong signature, fails, or
      has an unreadable public key yields NO signature — never a bad one.
  §D  Anchoring: off by default; the file sink appends a signed checkpoint; an unmoved tip is
      not re-anchored (unless forced); a failed publish is recorded, not hidden; the webhook
      and RFC 3161 sinks send what they should; the RFC 3161 request is byte-identical to
      ``openssl ts -query``; the ledger check re-proves every anchored tip.
  §E  The surfaces: custody and anchoring reported by the ledger endpoint, the honest-limit
      wording follows custody, the anchor loop starts only when anchoring is on.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from trusted_data_agent.core import provenance as P  # noqa: E402
from trusted_data_agent.core import signing as SG  # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.core.trust import store as S  # noqa: E402
from trusted_data_agent.core.trust import ledger as L  # noqa: E402
from trusted_data_agent.core.trust import anchoring as AN  # noqa: E402
from trusted_data_agent.core.trust import attestation as A  # noqa: E402
from trusted_data_agent.eval import online as O  # noqa: E402

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


def _set(**kw):
    saved = {k: getattr(APP_CONFIG, k, None) for k in kw}
    for k, v in kw.items():
        setattr(APP_CONFIG, k, v)
    return saved


def _restore(saved):
    for k, v in saved.items():
        setattr(APP_CONFIG, k, v)


def _verify_with(pem: bytes, sig_b64: str, msg: str) -> bool:
    try:
        serialization.load_pem_public_key(pem).verify(base64.b64decode(sig_b64), msg.encode("utf-8"))
        return True
    except Exception:  # noqa: BLE001
        return False


# ── §A the default is byte-identical ─────────────────────────────────────────

def t_default():
    saved = _set(EVIDENCE_SIGNER="local")
    try:
        key = P._get_provenance_signing_key()
        if key is None:
            check("A0 the deployment key is available for this check", False)
            return
        msg = "ledger:v1:checkpoint:7:" + "a" * 64
        direct = base64.b64encode(key.sign(msg.encode("utf-8"))).decode("ascii")
        env = O.sign_payload(msg)
        check("A1 sign_payload is byte-identical to the direct key call", env["signature"] == direct and env["signed"])
        raw = key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
        check("A2 the fingerprint is the key file's", P.get_key_fingerprint() == hashlib.sha256(raw).hexdigest()
              == env["key_fingerprint"])
        att = O.sign_attestation("tip", [], "llm_only")
        payload = f"tip|{O._verdict_digest([])}|llm_only"
        check("A3 sign_attestation is byte-identical",
              att["signature"] == base64.b64encode(key.sign(payload.encode())).decode("ascii"))
        ch = P.ProvenanceChain(session_id="s", turn_number=1, user_uuid="u", profile_type="llm_only")
        ch.add_step("query_intake", "hi", "q")
        sealed = ch.finalize()
        st = sealed["provenance_chain"][0]
        check("A4 a chain step's signature is byte-identical",
              st["signature"] == base64.b64encode(key.sign(st["chain_hash"].encode())).decode("ascii"))
        check("A5 the local signer reports its key as held on this host",
              SG.get_signer().describe()["key_on_host"] is True and SG.get_signer().name == "local")
    finally:
        _restore(saved)


# ── §B / §C the command signer ───────────────────────────────────────────────

SIGNER_SCRIPT = r"""
import sys, base64
from cryptography.hazmat.primitives import serialization
key = serialization.load_pem_private_key(open(sys.argv[1], "rb").read(), password=None)
msg = sys.stdin.buffer.read()
mode = sys.argv[2] if len(sys.argv) > 2 else "ok"
if mode == "fail":
    sys.stderr.write("hsm unavailable"); sys.exit(3)
if mode == "wrong":
    msg = msg + b"x"
sys.stdout.write(base64.b64encode(key.sign(msg)).decode())
"""


def _external_key(d):
    k = Ed25519PrivateKey.generate()
    priv = os.path.join(d, "ext.pem")
    pub = os.path.join(d, "ext.pub.pem")
    with open(priv, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))
    with open(pub, "wb") as f:
        f.write(k.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo))
    script = os.path.join(d, "signer.py")
    with open(script, "w") as f:
        f.write(SIGNER_SCRIPT)
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, pub, script, hashlib.sha256(raw).hexdigest()


def t_command():
    d = tempfile.mkdtemp()
    try:
        priv, pub, script, fp = _external_key(d)
        cmd = f"{sys.executable} {script} {priv}"
        saved = _set(EVIDENCE_SIGNER="command", EVIDENCE_SIGNER_COMMAND=cmd, EVIDENCE_SIGNER_PUBLIC_KEY=pub)
        loaded = []
        real_loader = P._get_provenance_signing_key
        P._get_provenance_signing_key = lambda: loaded.append(1) or real_loader()
        try:
            env = O.sign_payload("hello")
            pem = open(pub, "rb").read()
            check("B1 the signature comes from the external signer and verifies with its key",
                  env["signed"] and _verify_with(pem, env["signature"], "hello"), env)
            check("B2 the fingerprint reported is the external key's", env["key_fingerprint"] == fp == P.get_key_fingerprint())
            check("B3 the public key reported is the external key's", P.get_provenance_public_key_pem() == pem)
            ch = P.ProvenanceChain(session_id="s", turn_number=1, user_uuid="u", profile_type="llm_only")
            ch.add_step("query_intake", "hi", "q")
            ch.add_step("turn_complete", "a", "done")
            sealed = ch.finalize()
            check("B4 a chain sealed by the external signer names its key", sealed["provenance_meta"]["key_fingerprint"] == fp)
            check("B5 ...and verifies", P.verify_chain(sealed).get("valid") is True, P.verify_chain(sealed))
            check("B6 the local key file is never loaded under the external signer", not loaded, loaded)
            check("B7 the signer reports its key as held OUTSIDE this host",
                  SG.get_signer().describe()["key_on_host"] is False)
            _set(EVIDENCE_SIGNER_COMMAND=cmd + " wrong")
            env2 = O.sign_payload("hello")
            check("C1 a wrong signature from the signer is never used", env2["signature"] == "" and not env2["signed"], env2)
            _set(EVIDENCE_SIGNER_COMMAND=cmd + " fail")
            env3 = O.sign_payload("hello")
            check("C2 a failing signer yields no signature", env3["signature"] == "" and not env3["signed"])
            ch2 = P.ProvenanceChain(session_id="s", turn_number=2, user_uuid="u", profile_type="llm_only")
            ch2.add_step("query_intake", "hi", "q")
            check("C3 a chain under a failing signer is unsigned, not mis-signed",
                  all(s["signature"] == "" for s in ch2.finalize()["provenance_chain"]))
            _set(EVIDENCE_SIGNER_COMMAND=cmd, EVIDENCE_SIGNER_PUBLIC_KEY=os.path.join(d, "missing.pem"))
            env4 = O.sign_payload("hello")
            check("C4 an unreadable public key yields no signature", not env4["signed"] and env4["key_fingerprint"] is None, env4)
        finally:
            P._get_provenance_signing_key = real_loader
            _restore(saved)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── §D anchoring ─────────────────────────────────────────────────────────────

def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    S._MIGRATED_PATHS.discard(path)
    return path


def t_anchor():
    saved = _set(EVIDENCE_ANCHOR="", EVIDENCE_ANCHOR_TARGET="")
    try:
        check("D1 anchoring is off by default", AN.anchor_now()["anchored"] is False and not AN.enabled())
    finally:
        _restore(saved)
    db = _tmp_db()
    d = tempfile.mkdtemp()
    sink = os.path.join(d, "anchors.jsonl")
    saved = _set(EVIDENCE_ANCHOR="file", EVIDENCE_ANCHOR_TARGET=sink)
    try:
        S.sign("skill", "a", A.content_hash("a"), signer_uuid="u", db_path=db)
        r = AN.anchor_now(db_path=db)
        lines = open(sink).read().splitlines()
        rec = json.loads(lines[0]) if lines else {}
        check("D2 the file sink appends one signed checkpoint", r["anchored"] and len(lines) == 1
              and rec.get("signed") and rec.get("seq") == 1 and rec.get("tip_hash") == r["tip_hash"], (r, rec))
        r2 = AN.anchor_now(db_path=db)
        check("D3 an unmoved tip is not anchored again", r2["anchored"] is False and len(open(sink).read().splitlines()) == 1)
        r3 = AN.anchor_now(force=True, db_path=db)
        check("D4 ...unless forced", r3["anchored"] and len(open(sink).read().splitlines()) == 2)
        S.sign("skill", "b", A.content_hash("b"), signer_uuid="u", db_path=db)
        AN.anchor_now(db_path=db)
        c = S._connect(db)
        try:
            anchored = AN.anchors(c)
            v = L.verify(c, checkpoints=[{"seq": a["seq"], "tip_hash": a["tip_hash"]} for a in anchored if a["ok"]])
        finally:
            c.close()
        check("D5 the ledger reaches every anchored tip", v["intact"] and all(cp["ok"] for cp in v["checkpoints"]), v)
        c = S._connect(db)
        try:
            c.execute("DELETE FROM trust_attestation_history WHERE id=2")
            c.execute("DELETE FROM trust_ledger WHERE seq=2")
            c.commit()
            v2 = L.verify(c, checkpoints=[{"seq": a["seq"], "tip_hash": a["tip_hash"]} for a in AN.anchors(c) if a["ok"]])
        finally:
            c.close()
        check("D6 a ledger truncated after anchoring no longer reaches the anchored tip",
              not v2["intact"] and any(not cp["ok"] for cp in v2["checkpoints"]), v2["checkpoints"])
        _set(EVIDENCE_ANCHOR_TARGET=os.path.join(d, "no", "such", "dir.jsonl"))
        S.sign("skill", "c", A.content_hash("c"), signer_uuid="u", db_path=db)
        r4 = AN.anchor_now(db_path=db)
        c = S._connect(db)
        try:
            last = AN.anchors(c)[0]
        finally:
            c.close()
        check("D6b a tip rewritten at a reused sequence number is published again, not skipped",
              r4.get("reason") != "the tip has not moved since the last anchor", r4)
        check("D7 a failed publish is recorded, not hidden", r4["anchored"] is False and r4.get("error")
              and last["ok"] is False and last["error"], (r4, last))
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)
        os.unlink(db)
    # webhook + rfc3161 through a faked HTTP layer
    import httpx
    sent = []

    class _R:
        def __init__(self, code, content=b"", text=""):
            self.status_code, self.content, self.text = code, content, text

    def fake_post(url, **kw):
        sent.append((url, kw))
        if url.endswith("/tsa"):
            granted = AN._tlv(0x30, AN._tlv(0x30, AN._tlv(0x02, b"\x00")) + AN._tlv(0x30, b""))
            return _R(200, granted)
        return _R(201)

    real = httpx.post
    httpx.post = fake_post
    db = _tmp_db()
    try:
        S.sign("skill", "a", A.content_hash("a"), signer_uuid="u", db_path=db)
        saved = _set(EVIDENCE_ANCHOR="webhook", EVIDENCE_ANCHOR_TARGET="https://log.example/in",
                     EVIDENCE_ANCHOR_TOKEN="t0k")
        rw = AN.anchor_now(db_path=db)
        url, kw = sent[-1]
        check("D8 the webhook sink POSTs the signed checkpoint with its bearer",
              rw["anchored"] and kw["json"]["format"] == "uderia.evidence.ledger.checkpoint"
              and kw["json"]["signed"] and kw["headers"]["Authorization"] == "Bearer t0k", kw)
        _restore(saved)
        saved = _set(EVIDENCE_ANCHOR="rfc3161", EVIDENCE_ANCHOR_TARGET="https://tsa.example/tsa")
        rt = AN.anchor_now(force=True, db_path=db)
        url, kw = sent[-1]
        c = S._connect(db)
        try:
            cp_payload = L.checkpoint(c)["payload"]
            receipt = c.execute("SELECT receipt FROM trust_ledger_anchors ORDER BY id DESC LIMIT 1").fetchone()[0]
        finally:
            c.close()
        body = kw["content"]
        check("D9 the RFC 3161 sink asks for a timestamp of sha256(the signed checkpoint payload)",
              rt["anchored"] and hashlib.sha256(cp_payload.encode()).digest() in body
              and kw["headers"]["Content-Type"] == "application/timestamp-query", kw.get("headers"))
        check("D10 the authority's token is kept as the receipt", receipt and base64.b64decode(receipt)[:1] == b"\x30")
        _restore(saved)
    finally:
        httpx.post = real
        os.unlink(db)
    # D13: a checkpoint that cannot be signed is never published — the failure is recorded.
    db = _tmp_db()
    d = tempfile.mkdtemp()
    sink = os.path.join(d, "anchors.jsonl")
    saved = _set(EVIDENCE_ANCHOR="file", EVIDENCE_ANCHOR_TARGET=sink, EVIDENCE_SIGNER="command",
                 EVIDENCE_SIGNER_COMMAND="true", EVIDENCE_SIGNER_PUBLIC_KEY=os.path.join(d, "missing.pem"))
    try:
        S.sign("skill", "a", A.content_hash("a"), signer_uuid="u", db_path=db)
        ru = AN.anchor_now(db_path=db)
        c = S._connect(db)
        try:
            lastu = AN.anchors(c)[0]
        finally:
            c.close()
        check("D13 an unsigned checkpoint is never published, and that is recorded",
              ru["anchored"] is False and not os.path.exists(sink) and lastu["ok"] is False
              and "could not be signed" in (lastu["error"] or ""), (ru, lastu))
    finally:
        _restore(saved)
        shutil.rmtree(d, ignore_errors=True)
        os.unlink(db)
    check("D11 PKIStatus is read from a response", AN.response_status(
        AN._tlv(0x30, AN._tlv(0x30, AN._tlv(0x02, b"\x02")))) == 2 and AN.response_status(b"junk") is None)
    if shutil.which("openssl"):
        dg = hashlib.sha256(b"x").digest()
        p = subprocess.run(["openssl", "ts", "-query", "-digest", dg.hex(), "-sha256", "-cert", "-no_nonce"],
                           capture_output=True)
        check("D12 the RFC 3161 request is byte-identical to openssl's", p.returncode == 0
              and p.stdout == AN.timestamp_request(dg), p.stderr[-200:])


# ── §E surfaces ──────────────────────────────────────────────────────────────

def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def t_surfaces():
    r = _src("src/trusted_data_agent/api/evidence_routes.py")
    seg = r[r.index("def _ledger_status"):]
    check("E1 the ledger endpoint reports custody and anchoring",
          'v["custody"]' in seg and 'v["anchoring"]' in seg)
    check("E2 the ledger check re-proves the anchored tips", "_anchoring.anchors(conn)" in seg)
    check("E3 anchoring now is an admin act", '"/v1/evidence/ledger/anchor"' in r
          and "_can_view_all(user)" in r[r.index('"/v1/evidence/ledger/anchor"'):])
    m = _src("src/trusted_data_agent/main.py")
    check("E4 the anchor loop starts only when anchoring is on", "anchor_loop" in m
          and "_anchoring.enabled()" in m[m.index("anchor_loop") - 400:m.index("anchor_loop")])
    ev = _src("static/js/handlers/evidenceView.js")
    check("E5 the honest limit follows custody", "key_on_host" in ev and "held outside this host" in ev)
    b = _src("src/trusted_data_agent/core/trust/evidence_bundle.py")
    check("E6 a bundle states its custody", '"custody"' in b)
    for f, name in (("src/trusted_data_agent/core/provenance.py", "provenance"),
                    ("src/trusted_data_agent/eval/online.py", "online")):
        s = _src(f)
        check(f"E7 {name} signs only through the signer seam (no direct key.sign outside the local branch)",
              "get_signer" in s and (name == "provenance" or "key.sign(" not in s))


# ── §F rotation: an older answer keeps verifying after a custody switch ──────────

def t_rotation():
    from trusted_data_agent.core.trust import evidence_view as EVV
    db = _tmp_db()
    d = tempfile.mkdtemp()
    saved_path = S._db_path
    S._db_path = lambda p=None: p or db
    saved = _set(EVIDENCE_SIGNER="local")
    try:
        local_fp = P.get_key_fingerprint()
        local_pem = P.get_provenance_public_key_pem()
        S.register_key(local_fp, local_pem, "local")
        ch = P.ProvenanceChain(session_id="s", turn_number=1, user_uuid="u", profile_type="llm_only")
        ch.add_step("query_intake", "hi", "q")
        ch.add_step("turn_complete", "a", "done")
        turn = {"turn": 1, **ch.finalize()}
        check("F1 the answer verifies under the key it was signed with", EVV._chain(turn)["valid"] is True)
        priv, pub, script, fp = _external_key(d)
        _set(EVIDENCE_SIGNER="command", EVIDENCE_SIGNER_COMMAND=f"{sys.executable} {script} {priv}",
             EVIDENCE_SIGNER_PUBLIC_KEY=pub)
        S.register_key(P.get_key_fingerprint(), P.get_provenance_public_key_pem(), "local")
        check("F2 the switch retires the earlier key", S.key_status(local_fp) == "retired"
              and S.key_status(fp) == "active")
        r = EVV._chain(turn)
        check("F3 after the switch the older answer still verifies — with the key it names",
              r["valid"] is True and r["key_status"] == "retired", r)
        check("F4 ...whereas the current key alone would not verify it",
              P.verify_chain({"provenance_chain": turn["provenance_chain"],
                              "provenance_meta": turn["provenance_meta"]}).get("valid") is False)
        check("F5 the public key a fingerprint names is read back", S.public_key_pem(local_fp)
              and S.public_key_pem("nope") is None)
    finally:
        _restore(saved)
        S._db_path = saved_path
        shutil.rmtree(d, ignore_errors=True)
        os.unlink(db)


# ── §G the ledger's reading follows anchoring ────────────────────────────────

def t_words():
    from trusted_data_agent.core.trust import evidence_view as EVV
    base = {"intact": True, "entries": 4, "checkpoints": []}
    w = EVV.ledger_words(dict(base, anchoring={"enabled": True, "anchors": 3, "last_ok": True}))
    check("G1 published checkpoints are named", w["attention"] == "pass"
          and "3 checkpoints published outside this host" in w["words"], w)
    w = EVV.ledger_words(dict(base, anchoring={"enabled": True, "anchors": 3, "last_ok": False,
                                               "last_error": "HTTP 503"}))
    check("G2 a failed last anchor reads warn and says why", w["attention"] == "warn"
          and "the last anchor failed: HTTP 503" in w["words"], w)
    w = EVV.ledger_words(dict(base, anchoring={"enabled": False}))
    check("G3 anchoring off adds nothing", w["words"] == "ledger intact · 4 entries" and w["attention"] == "pass", w)
    w = EVV.ledger_words({"intact": False, "entries": 4, "breaks_count": 1, "checkpoints": [],
                          "anchoring": {"enabled": True, "last_ok": False}})
    check("G4 a broken ledger stays danger whatever the anchor", w["attention"] == "danger", w)
    from trusted_data_agent.core.trust import evidence_bundle as B
    host = B._limits({"key_on_host": True, "anchoring": None})
    ext = B._limits({"key_on_host": False, "anchoring": "rfc3161"})
    check("G5 a bundle's limit says a host-held key could re-sign history", "could rewrite and re-sign" in host)
    check("G6 ...and never claims that under an external key, naming the anchor sink",
          "could rewrite" not in ext and "cannot read the key" in ext and "(rfc3161)" in ext, ext)
    saved = _set(EVIDENCE_SIGNER="local", EVIDENCE_ANCHOR="", EVIDENCE_ANCHOR_TARGET="")
    try:
        cu = B._custody()
    finally:
        _restore(saved)
    check("G7 a bundle states its custody", cu == {"signer": "local", "key_on_host": True, "anchoring": None}, cu)


def main():
    t_default()
    t_command()
    t_anchor()
    t_surfaces()
    t_rotation()
    t_words()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
