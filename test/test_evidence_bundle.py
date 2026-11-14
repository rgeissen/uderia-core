"""PROP-009 increment 5 — portable evidence bundles and the two offline verifiers.

CI-pure: a temp SQLite store, real Ed25519 signatures (the deployment provenance key), the
real ProvenanceChain, the real bundle builder with its one I/O seam (the artefact resolver)
replaced by a faithful fake that computes the content hash the way every resolver does. The
browser verifier runs under Node (WebCrypto). No server, no network.

  §A  Content capture records what ``content_hash`` hashed and never changes its output.
  §B  A bundle carries the signed bytes when the artefact still produces them and is within
      the limit, and names why when not (changed since signed · larger than the limit · not
      requested). An answer brings its chain, its bill of materials and the attestation of
      every part AS SIGNED WHEN ANSWERED.
  §C  scripts/verify_evidence.py verifies a clean bundle and FAILS each tamper by name:
      one byte of content, a signature, a part of the bill of materials, a chain step, the
      embedded audit pack; a wrong fingerprint is refused, a missing one leaves the signer
      unconfirmed.
  §D  static/js/evidenceVerify.js reaches the same verdict as the Python verifier on every
      bundle of §C — including a bill of materials with non-ASCII text and integral and
      exponent floats, which only verifies if numbers are taken from the bundle TEXT.
  §E  The offline verifier page inlines the module verbatim and is current.
  §F  The wiring: the export route reads the caller's own sessions, gates artefacts by
      ownership, names what it skipped; the answer's export IS the bundle (appended, never
      re-serialized); the verify button is not an "open" control; scripts load in order.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from trusted_data_agent.core.trust import store as S  # noqa: E402
from trusted_data_agent.core.trust import evidence as EV  # noqa: E402
from trusted_data_agent.core.trust import evidence_bundle as B  # noqa: E402
from trusted_data_agent.core.trust import attestation as A  # noqa: E402
from trusted_data_agent.core import provenance as P  # noqa: E402
import verify_evidence as VE  # noqa: E402
import build_evidence_verifier_page as PAGE  # noqa: E402

PASSED = 0
FAILED = 0
OWNER = "owner-1"


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    S._MIGRATED_PATHS.discard(path)
    return path


# ── §A content capture ───────────────────────────────────────────────────────

def t_capture():
    text = "  the skill body ✓\n"   # surrounding whitespace is content
    plain = A.content_hash(text)
    obj = {"b": 1, "a": [1.0, "x"]}
    plain_obj = A.content_hash(obj)
    cap = A.ContentCapture(limit=None)
    with A.content_capture(cap):
        h1 = A.content_hash(text)
        h2 = A.content_hash(obj)
    import hashlib
    check("A1 capture never changes the hash of a string — sha256 of exactly its bytes",
          h1 == plain == hashlib.sha256(text.encode("utf-8")).hexdigest())
    check("A2 capture never changes the hash of an object", h2 == plain_obj)
    check("A3 the captured bytes are what was hashed", cap.seen[h1][1] == text
          and A.content_hash(cap.seen[h2][1]) == h2, cap.seen)
    small = A.ContentCapture(limit=4)
    with A.content_capture(small):
        A.content_hash(text)
    check("A4 above the limit only the size is kept", small.seen[plain][1] is None
          and small.seen[plain][0] == len(text.encode("utf-8")))
    after = A.ContentCapture()
    with A.content_capture(after):
        pass
    A.content_hash("outside")
    check("A5 nothing is recorded once the capture closes", not after.seen)


# ── fixtures: a signed skill, a sealed answer ────────────────────────────────

SKILL_TEXT = "Always answer in SQL. Café ✓"
LIVE = {"skill:sk1": SKILL_TEXT}


def _fake_resolve(kind, aid, owner):
    if f"{kind}:{aid}" in LIVE:
        A.content_hash(LIVE[f"{kind}:{aid}"])


def _fixture(db):
    S._db_path, saved = (lambda p=None: db), S._db_path
    vh = A.content_hash(SKILL_TEXT)
    env = S.sign("skill", "sk1", vh, signer_uuid=OWNER)
    hid = env.get("history_id")
    bom = {"version": 1, "profile_id": "p1", "profile_tag": "TAG",
           "agent": {"kind": "agent", "artifact_id": "p1", "state": "unsigned", "weight": 1.0},
           "deployment": {"kind": "agent_deployment", "artifact_id": "p1", "state": "unsigned",
                          "tiny": 1e-05},
           "parts": [{"kind": "skill", "artifact_id": "sk1", "label": "Café ✓ sql",
                      "live_hash": vh, "attested_hash": vh, "history_id": hid,
                      "state": "trusted", "score": 2.0}]}
    tok = EV._TURN_BOM.set(copy.deepcopy(bom))
    try:
        ch = P.ProvenanceChain(session_id="s1", turn_number=1, user_uuid=OWNER, profile_type="llm_only")
        ch.add_step("query_intake", "hello", "q")
        ch.add_step("turn_complete", "the answer", "a")
        sealed = ch.finalize()
    finally:
        EV._TURN_BOM.reset(tok)
    turn = {"turn": 1, "bom": bom, "user_query": "hello", "final_summary_text": "the answer", **sealed}
    # Faithful to production: the persistence chokepoint indexes every sealed answer, which
    # enters it in the evidence ledger (PROP-009 §4.5). A bundle of an un-indexed answer is a
    # separate, named case (B15).
    from trusted_data_agent.core.trust import evidence_index as EI
    assert EI.record_turn(OWNER, "s1", turn), "fixture: the answer must index"
    return saved, vh, hid, turn


def _bundle(turn, content="limit"):
    return B.build(OWNER, turns=[("s1", turn)], artifacts=[("skill", "sk1")], content=content,
                   include_receipt=True)


def _as_text(b):
    # Quart's jsonify — Python's json — is what produces the text a browser receives.
    return json.dumps(b)


# ── §B the bundle ────────────────────────────────────────────────────────────

def t_bundle(state):
    db = _tmp_db()
    saved_resolve = B._resolve_live
    B._resolve_live = _fake_resolve
    try:
        saved, vh, hid, turn = _fixture(db)
        try:
            b = _bundle(turn)
            atts = b["attestations"]
            check("B1 an answer's part comes AS SIGNED WHEN ANSWERED",
                  any(a["source"] == "as_signed_when_answered" and a["history_id"] == hid for a in atts), atts)
            check("B2 an artefact asked for comes as its CURRENT attestation",
                  any(a["source"] == "current" for a in atts))
            inc = [a for a in atts if a["content"].get("included")]
            check("B3 the signed bytes travel and hash to the signed version",
                  inc and all(A.content_hash(a["content"]["data"]) == a["version_hash"] for a in inc), atts)
            check("B4 the answer brings its chain and its bill of materials",
                  b["turns"][0]["provenance_chain"] and b["turns"][0]["bom"] == turn["bom"])
            check("B5 the receipt travels only on request",
                  b["turns"][0]["receipt"]["answer"] == "the answer"
                  and "receipt" not in B.build(OWNER, turns=[("s1", turn)])["turns"][0])
            check("B6 the deployment key travels with the bundle",
                  any(k["fingerprint"] == b["deployment_key_fingerprint"] and k["public_key_pem"]
                      for k in b["keys"]), b["keys"])
            none = _bundle(turn, content="none")
            check("B7 content not requested is named",
                  all(a["content"]["reason"] == "content not requested" for a in none["attestations"]))
            LIVE["skill:sk1"] = SKILL_TEXT + " edited"
            changed = _bundle(turn)
            check("B8 an artefact changed since signed carries its hash only, and says so",
                  all(not a["content"]["included"] and "changed since it was signed" in a["content"]["reason"]
                      for a in changed["attestations"]), changed["attestations"])
            LIVE["skill:sk1"] = SKILL_TEXT
            saved_limit = B.content_limit
            B.content_limit = lambda: 5
            big = _bundle(turn)
            B.content_limit = saved_limit
            check("B9 above the limit the size and the reason are named",
                  all("larger than the bundle limit" in a["content"]["reason"] and a["content"]["bytes"]
                      for a in big["attestations"]), big["attestations"])
            allc = _bundle(turn, content="all")
            check("B10 'all' carries content whatever its size", all(a["content"]["included"] for a in allc["attestations"]))
            state["bundle"] = b
            state["fp"] = b["deployment_key_fingerprint"]
            # A re-sign AFTER the answer: the answer's evidence still names what it was made with.
            new_text = SKILL_TEXT + " v2"
            LIVE["skill:sk1"] = new_text
            S.sign("skill", "sk1", A.content_hash(new_text), signer_uuid=OWNER)
            later = _bundle(turn)
            then = [a for a in later["attestations"] if a["source"] == "as_signed_when_answered"]
            now = [a for a in later["attestations"] if a["source"] == "current"]
            check("B11 after a re-sign the answer's part is still the version it was made with",
                  then and then[0]["version_hash"] == vh and then[0]["history_id"] == hid, then)
            check("B12 ...whose bytes are no longer held, and that is said",
                  then and not then[0]["content"]["included"]
                  and "changed since it was signed" in then[0]["content"]["reason"], then)
            check("B13 ...while the current attestation carries the new version and its bytes",
                  now and now[0]["version_hash"] == A.content_hash(new_text) and now[0]["content"]["included"], now)
            rl = VE.verify_bundle(json.loads(_as_text(later)), state["fp"])
            check("B14 a bundle spanning a re-sign verifies (both signatures are real)", rl["ok"], rl)
            LIVE["skill:sk1"] = SKILL_TEXT
            # B15: an answer the ledger never recorded (indexed around it) is named, not passed.
            stray = dict(turn, turn=7)
            ub = B.build(OWNER, turns=[("s1", stray)], content="limit")
            ru = VE.verify_bundle(json.loads(_as_text(ub)), state["fp"])
            check("B15 an answer missing from the ledger is named and fails",
                  not ru["ok"] and ru["ledger"] and any("#7: not in the ledger" in n
                                                       for n in ru["ledger"]["notes"]), ru.get("ledger"))
        finally:
            S._db_path = saved
    finally:
        B._resolve_live = saved_resolve
        os.unlink(db)


# ── §C the Python verifier ───────────────────────────────────────────────────

def _tampers(b):
    """name → a tampered copy. Every one must FAIL."""
    out = {}
    t = copy.deepcopy(b)
    a = next(x for x in t["attestations"] if x["content"].get("included"))
    a["content"]["data"] = a["content"]["data"][:-1] + ("x" if a["content"]["data"][-1] != "x" else "y")
    out["content byte"] = t
    t = copy.deepcopy(b)
    sig = t["attestations"][0]["signature"]
    t["attestations"][0]["signature"] = ("A" if sig[0] != "A" else "B") + sig[1:]
    out["attestation signature"] = t
    t = copy.deepcopy(b)
    t["turns"][0]["bom"]["parts"][0]["state"] = "changed"
    out["bill of materials"] = t
    t = copy.deepcopy(b)
    t["turns"][0]["provenance_chain"][0]["content_hash"] = "0" * 64
    out["chain step"] = t
    t = copy.deepcopy(b)
    t["turns"][0]["bom"]["parts"][0]["score"] = 2.5
    out["a number in the bill of materials"] = t
    t = copy.deepcopy(b)
    t["turns"][0]["receipt"]["answer"] = "the answer, edited"
    out["the exported answer text"] = t
    t = copy.deepcopy(b)
    t["turns"][0]["receipt"]["user_query"] = "hello?"
    out["the exported question text"] = t
    return out


def _with_audit_pack(b):
    """A bundle carrying an audit pack signed exactly as POST /v1/assurance/audit-pack signs it."""
    from trusted_data_agent.eval.online import sign_payload
    import hashlib
    pack = {"generated_at": "2026-10-08T00:00:00+00:00", "posture": [{"tag": "Café", "rate": 1.0}],
            "scores": {"pass_rate": 0.75, "tiny": 1e-07}}
    canonical = json.dumps(pack, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    sig = sign_payload(h)
    out = copy.deepcopy(b)
    out["audit_pack"] = pack
    out["audit_pack_attestation"] = {"content_sha256": h, "signed": sig["signed"], "signature": sig["signature"],
                                     "key_fingerprint": sig["key_fingerprint"]}
    return out


def t_python_verifier(state):
    b, fp = state["bundle"], state["fp"]
    r = VE.verify_bundle(json.loads(_as_text(b)), fp)
    check("C1 a clean bundle verifies and the signer is confirmed",
          r["ok"] and r["failed"] == 0 and r["signer_confirmed"] and r["checked"] >= 3, r)
    r0 = VE.verify_bundle(json.loads(_as_text(b)), None)
    check("C2 without a fingerprint it verifies but the signer is NOT confirmed",
          r0["ok"] and not r0["signer_confirmed"], r0)
    rw = VE.verify_bundle(json.loads(_as_text(b)), "f" * 64)
    check("C3 a different fingerprint is refused", not rw["ok"], rw)
    for name, t in _tampers(b).items():
        rt = VE.verify_bundle(json.loads(_as_text(t)), fp)
        check(f"C4 tamper '{name}' fails", not rt["ok"] and rt["failed"] >= 1, rt)
    ap = _with_audit_pack(b)
    ra = VE.verify_bundle(json.loads(_as_text(ap)), fp)
    check("C5 an embedded audit pack verifies", ra["ok"] and ra["audit_pack"]["ok"] is True, ra.get("audit_pack"))
    apt = copy.deepcopy(ap)
    apt["audit_pack"]["scores"]["pass_rate"] = 0.76
    rat = VE.verify_bundle(json.loads(_as_text(apt)), fp)
    check("C6 an edited audit pack fails", not rat["ok"] and rat["audit_pack"]["ok"] is False, rat.get("audit_pack"))
    unsigned = copy.deepcopy(b)
    unsigned["attestations"][0]["signed"] = False
    ru = VE.verify_bundle(json.loads(_as_text(unsigned)), fp)
    check("C7 a record without a signature is not counted as verified", ru["attestations"][0]["ok"] is None)
    try:
        VE.verify_bundle({"format": "something else"})
        check("C8 a non-bundle is refused", False)
    except ValueError:
        check("C8 a non-bundle is refused", True)
    state["cases"] = {"clean": (_as_text(b), fp), "nofp": (_as_text(b), ""), "wrongfp": (_as_text(b), "f" * 64),
                      "audit": (_as_text(ap), fp), "audit_tampered": (_as_text(apt), fp),
                      **{f"tamper:{k}": (_as_text(v), fp) for k, v in _tampers(b).items()}}


# ── §D the browser verifier, same verdicts ───────────────────────────────────

NODE_RUNNER = r"""
const fs = require('fs');
const EV = require(process.argv[2]);
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
(async () => {
  const out = {};
  for (const [name, [text, fp]] of Object.entries(cases)) {
    try {
      const r = await EV.verifyBundle(text, fp);
      out[name] = { ok: r.ok, failed: r.failed, checked: r.checked, signer: r.signer_confirmed,
                    audit: r.audit_pack ? r.audit_pack.ok : null };
    } catch (e) { out[name] = { error: e.message }; }
  }
  try { await EV.verifyBundle(JSON.stringify({ format: 'x' })); out.nonbundle = 'accepted'; }
  catch (e) { out.nonbundle = 'refused'; }
  out.pyDumps = EV.pyDumps(EV.parseRaw('{"b":[1.0,1e-05,2],"a":"Café \\u2713 \\"q\\"\\n"}'));
  process.stdout.write(JSON.stringify(out));
})();
"""


def t_js_verifier(state):
    node = shutil.which("node")
    if not node:
        check("D0 node is available for the browser-verifier parity check", False, "node not found")
        return
    d = tempfile.mkdtemp()
    try:
        cases = os.path.join(d, "cases.json")
        with open(cases, "w", encoding="utf-8") as f:
            json.dump(state["cases"], f)
        runner = os.path.join(d, "run.js")
        with open(runner, "w") as f:
            f.write(NODE_RUNNER)
        p = subprocess.run([node, runner, str(ROOT / "static" / "js" / "evidenceVerify.js"), cases],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            check("D0 the browser verifier runs under Node", False, p.stderr[-800:])
            return
        res = json.loads(p.stdout)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    for name, (text, fp) in state["cases"].items():
        py = VE.verify_bundle(json.loads(text), fp or None)
        js = res.get(name) or {}
        check(f"D1 '{name}': the browser verdict equals the Python verdict",
              js.get("ok") == py["ok"] and js.get("failed") == py["failed"]
              and js.get("checked") == py["checked"] and js.get("signer") == py["signer_confirmed"],
              {"js": js, "py": {k: py[k] for k in ("ok", "failed", "checked", "signer_confirmed")}})
    check("D2 a clean bundle verifies in the browser", res["clean"]["ok"] is True and res["clean"]["signer"] is True)
    check("D3 every tamper fails in the browser",
          all(v.get("ok") is False for k, v in res.items() if k.startswith("tamper:")), res)
    check("D4 the browser refuses a non-bundle", res.get("nonbundle") == "refused")
    expect = json.dumps(json.loads('{"b":[1.0,1e-05,2],"a":"Café \\u2713 \\"q\\"\\n"}'),
                        sort_keys=True, separators=(",", ":"))
    check("D5 the browser re-encodes JSON exactly as Python does (numbers, escapes, key order)",
          res.get("pyDumps") == expect, {"js": res.get("pyDumps"), "py": expect})


# ── §E the offline page ──────────────────────────────────────────────────────

def t_page():
    page = (ROOT / "static" / "trust" / "verify-evidence.html").read_text(encoding="utf-8")
    module = (ROOT / "static" / "js" / "evidenceVerify.js").read_text(encoding="utf-8").rstrip("\n")
    check("E1 the page inlines the verifier module verbatim", PAGE.inline_copy(page) == module)
    check("E2 the page is current with its builder", page == PAGE.render())
    check("E3 the page loads nothing from the network", not re.search(r'<script[^>]+src=|<link[^>]+href=|fetch\(', page))
    check("E4 the page asks for the fingerprint separately",
          'id="fp"' in page and "not from the bundle" in page)
    # The landing page links the stories, not the references (240c01bb — "the stories are the
    # entry points"), so the verifier is reached from its own story: The Anatomy of an Answer's
    # Evidence.
    check("E5 the trust site links the verifier (from the evidence story)",
          'href="verify-evidence.html"' in (ROOT / "static" / "trust" / "the-anatomy-of-an-answers-evidence.html").read_text(encoding="utf-8"))


# ── §F wiring ────────────────────────────────────────────────────────────────

def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def t_wiring():
    r = _src("src/trusted_data_agent/api/evidence_routes.py")
    seg = r[r.index('"/v1/evidence/export"'):]
    check("F1 the export reads the caller's OWN sessions", "session_manager.get_session(user.id, sid)" in seg)
    check("F2 artefacts are gated by ownership, failing closed",
          "_visible, kind, aid, user.id" in seg and "return False" in r[r.index("def _visible"):r.index("@evidence_bp.route(\"/v1/evidence/export\"")])
    check("F3 what is not exported is named, never dropped silently", 'out["skipped"] = skipped' in seg)
    # F2b: the visibility rule, executed — owner-scoped kinds (the store read is scoped)
    # pass; every other kind follows the Trust Membrane's ownership check; an error denies.
    try:
        from trusted_data_agent.api import evidence_routes as ER
        from trusted_data_agent.api import trust_routes as TR
        real = TR._assert_owner
        try:
            TR._assert_owner = lambda kind, aid, user: (False, "not yours")
            denied = ER._visible("ontology", "kg1", "u")
            TR._assert_owner = lambda kind, aid, user: (True, None)
            allowed = ER._visible("ontology", "kg1", "u")
            TR._assert_owner = lambda kind, aid, user: (_ for _ in ()).throw(RuntimeError("db"))
            errored = ER._visible("ontology", "kg1", "u")
            agent = ER._visible("agent", "p1", "u")
        finally:
            TR._assert_owner = real
        check("F2b another owner's artefact is not exported", denied is False)
        check("F2b the owner's artefact is", allowed is True)
        check("F2b an ownership error denies (fails closed)", errored is False)
        check("F2b an owner-scoped kind needs no ownership lookup", agent is True)
    except ImportError as e:
        check("F2b the evidence routes import", False, e)
    check("F4 the audit preset embeds the deployment's signed audit pack",
          "assurance_audit_pack()" in seg and 'out["audit_pack_attestation"]' in seg)
    check("F5 content is one of limit · all · none", '("limit", "all", "none")' in seg)
    cap = _src("static/js/handlers/answerCapsule.js")
    ex = cap[cap.index("async function exportReceipt"):cap.index("function resolveIds")]
    check("F6 the answer's export IS the evidence bundle",
          "exportBundleText(" in ex and "include_receipt: true" in ex and "Export evidence" in cap)
    check("F7 the evaluation is appended to the bundle TEXT, never re-serialized",
          "appendTopLevel(text, 'evaluation'" in ex)
    ev = _src("static/js/handlers/evidenceView.js")
    check("F8 the verify button is not an 'open' control (it would open a popover)",
          'class="ev-act ev-turn-verify"' in ev and "ev-open ev-turn-verify" not in ev)
    check("F9 the estate checks in the browser through the export, not one request per row",
          "exportBundleText({ artifacts:" in ev[ev.index("async function verifyShown"):ev.index("async function exportShown")])
    vs = ev[ev.index("function exportBundleText"):ev.index("function downloadText")]
    check("F10 the bundle stays text on this side (no JSON.parse of the bundle)", "JSON.parse" not in vs)
    t = _src("templates/index.html")
    i1, i2 = t.find("js/evidenceVerify.js"), t.find("js/handlers/evidenceView.js")
    check("F11 the verifier loads before the view, both inside the Ontology Suite guard",
          0 < i1 < i2 and t.rfind("{% if trust_membrane_available %}", 0, i1) > t.rfind("{% endif %}", 0, i1))
    check("F12 the bundle limit is a config value", "EVIDENCE_CONTENT_LIMIT_BYTES" in _src("src/trusted_data_agent/core/config.py"))
    readme = _src("src/trusted_data_agent/core/trust/README.md")
    check("F13 the removal manifest names the new files",
          all(x in readme for x in ("evidence_bundle.py", "evidenceVerify.js", "verify-evidence.html")))


# ── §G the exported question and answer are checked, never only shown ────────

def t_receipt_text():
    import hashlib
    sha = lambda x: hashlib.sha256(x.encode("utf-8")).hexdigest()
    # G1-G2: the export carries the field the chain sealed (Optimize keeps two).
    chain = [{"step_type": "turn_complete", "content_hash": sha("sealed text")}]
    ans, sealed = B._sealed_answer({"final_summary": "a display summary",
                                    "final_summary_text": "sealed text", "provenance_chain": chain})
    check("G1 the receipt carries the answer field the chain sealed", ans == "sealed text" and sealed, ans)
    ans, sealed = B._sealed_answer({"final_summary_text": "unsealed", "provenance_chain": chain})
    check("G2 an answer the chain does not cover is flagged, not passed off", ans == "unsealed" and not sealed)
    # G3: a flagged answer is reported as NOT checked, never as verified or as altered.
    notes = []
    errs = VE.check_receipt_text({"answer": "x", "answer_not_sealed": "older record"}, chain, notes)
    check("G3 a flagged answer reads 'NOT checked'", not errs and any("NOT checked" in n for n in notes), notes)
    # G4-G5: a long answer with astral characters — Python and the browser agree on the
    # sealed prefix (code points, not UTF-16 units) and both say only the prefix is checked.
    long = ("Café 🌍 " * 900)[:5000]
    lchain = [{"step_type": "turn_complete", "content_hash": sha(long[:4096])}]
    notes = []
    errs = VE.check_receipt_text({"answer": long}, lchain, notes)
    check("G4 Python: a long answer matches on its sealed prefix, and says only the prefix is checked",
          not errs and any("first 4096 characters" in n for n in notes), notes)
    node = shutil.which("node")
    if node:
        js = ("const V=require(process.argv[1]);const [r,c]=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "const n=[];V.checkReceiptText(r,c,n).then(e=>console.log(JSON.stringify({e,n})));")
        p = subprocess.run([node, "-e", js, str(ROOT / "static" / "js" / "evidenceVerify.js")],
                           input=json.dumps([{"answer": long}, lchain]), capture_output=True, text=True, timeout=60)
        try:
            jr = json.loads(p.stdout)
        except Exception:
            jr = {"e": ["no output"], "n": [p.stderr[-300:]]}
        check("G5 the browser agrees on the sealed prefix of a long answer with emoji",
              not jr["e"] and any("first 4096 characters" in n for n in jr["n"]), jr)


# ── §H every signable kind's bytes reach the bundle ──────────────────────────

def t_capture_every_kind():
    import hashlib
    from trusted_data_agent.core.trust import extensions as X, actions as Ac, products as Pr
    old = lambda d: hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"),
                                              default=str).encode("utf-8")).hexdigest()
    d = {"entities": [{"name": "Lead", "properties": {"z": 1, "a": "Café ✓"}}], "relationships": [], "n": 1.0}
    for name, mod in (("ontology_extension", X), ("action_type", Ac), ("data_product", Pr)):
        check(f"H1 {name}: its hash is unchanged (no signature moves)", mod._canon_hash(d) == old(d))
        cap = A.ContentCapture(limit=None)
        with A.content_capture(cap):
            h = mod._canon_hash(d)
        check(f"H2 {name}: the bundle's capture sees the bytes it hashed "
              "(else every export of this kind reads 'changed since signed')", h in cap.seen)
    # No trust module hashes canonical JSON outside attestation.content_hash.
    trust = ROOT / "src" / "trusted_data_agent" / "core" / "trust"
    rogue = [f"{f.name}:{i}" for f in sorted(trust.glob("*.py")) if f.name != "attestation.py"
             for i, line in enumerate(f.read_text().splitlines(), 1)
             if "hashlib.sha256(json.dumps(" in line and "json.dumps(d).encode" not in line]
    check("H3 no trust module hashes canonical JSON outside content_hash", not rogue, rogue)


# ── §J an extension is named in words on every trust surface ────────────────

def t_extension_label():
    from trusted_data_agent.core.trust import extensions as X
    lab = X.extension_label("ext-00000000-0000-0000-0000-000000000000")
    check("J1 an unknown extender reads a short id, never the raw ext-<uuid>",
          lab == "extension 00000000" and "ext-" not in lab, lab)
    comp = (ROOT / "src" / "trusted_data_agent" / "core" / "trust" / "composition.py").read_text()
    check("J2 the trust composition labels an extension through extension_label",
          "_ext.extension_label(ext_id)" in comp and 'f"extension {ext_id}"' not in comp)


# ── §I the fifth trust story: its inline verifier and its published sample ───

STORY = ROOT / "static" / "trust" / "the-anatomy-of-an-answers-evidence.html"
SAMPLE = ROOT / "static" / "trust" / "samples" / "evidence-answer-sample.json"
STORY_FP = "a6c5cf5c09865269e3d3b660837435aff471e602b840fb31a08777a7a0549a57"


def _story_tamper(text, mode):
    """The page's own tamper() in Python: each edits the exported TEXT."""
    if mode == "answer":
        return text.replace("There are 179,193 robberies", "There are 179,194 robberies", 1)
    if mode == "model":
        return text.replace('"model": "gemini-3.1-flash-lite"', '"model": "gpt-5-mini"', 1)
    if mode == "signature":
        sig = next(a for a in json.loads(text)["attestations"] if a["kind"] == "ontology")["signature"]
        return text.replace(sig, ("C" if sig[0] == "B" else "B") + sig[1:])
    return text


def t_story():
    story = STORY.read_text(encoding="utf-8")
    module = (ROOT / "static" / "js" / "evidenceVerify.js").read_text(encoding="utf-8").rstrip("\n")
    check("I1 the story inlines the verifier module verbatim", PAGE.inline_copy(story) == module)
    check("I2 the builder keeps the story in its list", STORY in PAGE.STORIES)
    text = SAMPLE.read_text(encoding="utf-8")
    for needle in ("There are 179,193 robberies", '"model": "gemini-3.1-flash-lite"'):
        check(f"I3 a tamper target occurs exactly once in the sample: {needle}", text.count(needle) == 1)
        check(f"I3b ...and the page tampers that exact text", needle in story)
    clean = VE.verify_bundle(json.loads(text), STORY_FP)
    check("I4 the published sample verifies, signer confirmed", clean["ok"] and clean["signer_confirmed"],
          {k: clean[k] for k in ("checked", "failed")})
    expect = {"answer": "the answer text does not match what was sealed",
              "model": "the bill of materials does not match what was sealed",
              "signature": "the signature does not verify"}
    for mode, phrase in expect.items():
        r = VE.verify_bundle(json.loads(_story_tamper(text, mode)), STORY_FP)
        notes = [n for e in r["attestations"] + r["turns"] for n in e["notes"]]
        check(f"I5 the story's '{mode}' tamper is refused by name", not r["ok"] and phrase in notes, notes[-3:])
    check("I6 the sample carries no signed content (only hashes and signatures are published)",
          all(not a["content"].get("included") for a in json.loads(text)["attestations"]))


def main():
    state = {}
    t_capture()
    t_bundle(state)
    if "bundle" in state:
        t_python_verifier(state)
    if "cases" in state:
        t_js_verifier(state)
    t_receipt_text()
    t_capture_every_kind()
    t_extension_label()
    t_story()
    t_page()
    t_wiring()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
