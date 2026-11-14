"""PROP-009 increment 6 — the hash-chained evidence ledger.

CI-pure: temp SQLite stores, real Ed25519 signatures, the real store, evidence index, bundle
builder and both verifiers (the browser one under Node). No server, no network.

  §A  The chain: entries link from genesis, hashes recompute, a turn's unchanged record is
      never entered twice.
  §B  Every attestation event is entered in the SAME transaction as its history row — sign,
      record, revoke, retire, the migration's own seeding — and a database that predates the
      ledger is entered once, as backfilled, before anything live.
  §C  verify detects each way history can be altered after the fact: an edited row, a removed
      row, a row written around the ledger, an edited or removed entry, a truncated ledger and
      a rewritten one (both against an earlier checkpoint). A clean ledger reads intact.
  §D  An answer's record is entered when written and again only when it changes; an edited
      record is detected.
  §E  A bundle carries a signed checkpoint and the segment that proves its records under it;
      both verifiers accept it and FAIL an edited record, an edited entry, a broken checkpoint.
  §F  The words, the REST scoping (which records moved is admin-only), the estate chip.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from trusted_data_agent.core.trust import store as S  # noqa: E402
from trusted_data_agent.core.trust import ledger as L  # noqa: E402
from trusted_data_agent.core.trust import evidence as EV  # noqa: E402
from trusted_data_agent.core.trust import evidence_index as IX  # noqa: E402
from trusted_data_agent.core.trust import evidence_bundle as B  # noqa: E402
from trusted_data_agent.core.trust import evidence_view as V  # noqa: E402
from trusted_data_agent.core.trust import attestation as A  # noqa: E402
from trusted_data_agent.core import provenance as P  # noqa: E402
import verify_evidence as VE  # noqa: E402

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


def _verify(db, cps=None):
    c = S._connect(db)
    try:
        return L.verify(c, checkpoints=cps)
    finally:
        c.close()


def _exec(db, sql, args=()):
    c = S._connect(db)
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


# ── §A the chain ─────────────────────────────────────────────────────────────

def t_chain():
    db = _tmp_db()
    try:
        c = S._connect(db)
        try:
            L.append(c, "attestation", "h:x", "d1")
            L.append(c, "attestation", "h:y", "d2")
            c.commit()
            es = L.entries(c)
        finally:
            c.close()
        check("A1 the first entry links to genesis", es[0]["prev_hash"] == L.GENESIS and es[0]["seq"] == 1)
        check("A2 each entry links to the one before", es[1]["prev_hash"] == es[0]["entry_hash"] and es[1]["seq"] == 2)
        check("A3 an entry hash recomputes from its fields",
              all(L.entry_hash(e["seq"], e["entry_type"], e["ref"], e["digest"], e["prev_hash"]) == e["entry_hash"] for e in es))
        c = S._connect(db)
        try:
            a = L.append_turn(c, OWNER, "s1", 1, "tip1", "bd1")
            b = L.append_turn(c, OWNER, "s1", 1, "tip1", "bd1")
            d = L.append_turn(c, OWNER, "s1", 1, "tip2", "bd1")
            c.commit()
        finally:
            c.close()
        check("A4 an unchanged turn record is entered once", a and b is None and d, (a, b, d))
        check("A5 a turn ref names no identifier", OWNER not in L.turn_ref(OWNER, "s1", 1)
              and "s1" not in L.turn_ref(OWNER, "s1", 1))
    finally:
        os.unlink(db)


# ── §B every attestation event, in its own transaction ───────────────────────

def t_store():
    db = _tmp_db()
    try:
        env = S.sign("skill", "sk1", A.content_hash("a"), signer_uuid=OWNER, db_path=db)
        hid = env["history_id"]
        c = S._connect(db)
        try:
            e = L.latest_entry(c, f"h:{hid}")
            row = c.execute(f"SELECT {', '.join(L.HISTORY_FIELDS)} FROM trust_attestation_history WHERE id=?", (hid,)).fetchone()
        finally:
            c.close()
        check("B1 a sign-off is entered with its history row", e is not None and not e["backfilled"])
        check("B2 the entry digest is the row's canonical record",
              e and e["digest"] == L.record_digest(L.history_record(dict(zip(L.HISTORY_FIELDS, row)))))
        S.revoke("skill", "sk1", db_path=db, actor_uuid=OWNER)
        S.sign("skill", "sk2", A.content_hash("b"), signer_uuid=OWNER, db_path=db)
        S.retire("sk2", ["skill"], db_path=db)
        S.record_attestation("skill", "sk3", A.content_hash("c"), db_path=db)
        v = _verify(db)
        check("B3 revoke, retire and record are entered too", v["intact"] and v["entries"] == 5 and not v["unledgered"], v)
        # A database whose history predates the ledger.
        _exec(db, "DROP TABLE trust_ledger")
        S._MIGRATED_PATHS.discard(db)
        v2 = _verify(db)
        check("B4 an existing history is entered once, as backfilled", v2["intact"] and v2["backfilled"] == 5
              and v2["first_live_seq"] is None, v2)
        S._MIGRATED_PATHS.discard(db)
        v3 = _verify(db)
        check("B5 the backfill never repeats", v3["entries"] == 5, v3)
        S.sign("skill", "sk4", A.content_hash("d"), signer_uuid=OWNER, db_path=db)
        v4 = _verify(db)
        check("B6 a live entry follows the backfilled ones", v4["intact"] and v4["first_live_seq"] == 6, v4)
        # The migration's own seeding goes through the same seam.
        _exec(db, "UPDATE trust_attestations SET history_id=NULL WHERE artifact_id='sk4'")
        S._MIGRATED_PATHS.discard(db)
        v5 = _verify(db)
        check("B7 the migration's seeded history row is entered", v5["intact"] and v5["entries"] == 7, v5)
    finally:
        os.unlink(db)


# ── §C detection ─────────────────────────────────────────────────────────────

def _seeded():
    db = _tmp_db()
    for i in range(4):
        S.sign("skill", f"k{i}", A.content_hash(str(i)), signer_uuid=OWNER, db_path=db)
    return db


def t_detect():
    db = _seeded()
    try:
        v = _verify(db)
        check("C1 a clean ledger reads intact", v["intact"] and v["entries"] == 4, v)
        c = S._connect(db)
        cp = L.checkpoint(c)
        c.close()
        _exec(db, "UPDATE trust_attestation_history SET version_hash='forged' WHERE id=2")
        v = _verify(db)
        check("C2 an edited history row is named", not v["intact"] and v["edited_count"] == 1
              and v["edited"][0]["ref"] == "h:2", v)
    finally:
        os.unlink(db)
    db = _seeded()
    try:
        _exec(db, "DELETE FROM trust_attestation_history WHERE id=3")
        v = _verify(db)
        check("C3 a removed history row is named", not v["intact"] and v["removed_count"] == 1, v)
    finally:
        os.unlink(db)
    db = _seeded()
    try:
        _exec(db, "INSERT INTO trust_attestation_history (kind, artifact_id, owner_uuid, event, version_hash, "
                  "signed, recorded_at) VALUES ('skill','sneak','','sign','h',0,1.0)")
        v = _verify(db)
        check("C4 a row written around the ledger is named", not v["intact"] and v["unledgered_count"] == 1, v)
    finally:
        os.unlink(db)
    db = _seeded()
    try:
        _exec(db, "UPDATE trust_ledger SET digest='0' WHERE seq=2")
        v = _verify(db)
        check("C5 an edited ledger entry breaks the chain", not v["intact"] and v["breaks_count"] >= 1, v)
    finally:
        os.unlink(db)
    db = _seeded()
    try:
        _exec(db, "DELETE FROM trust_ledger WHERE seq=2")
        v = _verify(db)
        check("C6 a removed ledger entry is a gap", not v["intact"] and any("gap" in b["why"] for b in v["breaks"]), v)
    finally:
        os.unlink(db)
    db = _seeded()
    try:
        c = S._connect(db)
        cp = L.checkpoint(c)
        c.close()
        check("C7 a checkpoint is the tip, signed", cp["seq"] == 4 and cp["signed"] and cp["signature"], cp)
        v = _verify(db, [cp])
        check("C8 an intact ledger reaches its earlier checkpoint", v["intact"] and v["checkpoints"][0]["ok"], v)
        _exec(db, "DELETE FROM trust_attestation_history WHERE id=4")
        _exec(db, "DELETE FROM trust_ledger WHERE seq=4")
        v = _verify(db, [cp])
        check("C9 a truncated ledger no longer reaches the checkpoint — the internal checks alone pass",
              not v["intact"] and not v["checkpoints"][0]["ok"] and not v["breaks"] and not v["removed"], v)
    finally:
        os.unlink(db)
    db = _seeded()
    try:
        c = S._connect(db)
        cp = L.checkpoint(c)
        c.close()
        # Rewrite history AND rebuild the chain consistently — invisible to internal checks.
        _exec(db, "UPDATE trust_attestation_history SET version_hash='forged' WHERE id=2")
        c = S._connect(db)
        try:
            c.execute("DELETE FROM trust_ledger")
            for r in L._history_rows(c):
                L.append(c, "attestation", f"h:{r['id']}", L.record_digest(L.history_record(r)))
            c.commit()
        finally:
            c.close()
        v = _verify(db, [cp])
        check("C10 a rewritten ledger passes internally but fails against an earlier checkpoint",
              not v["intact"] and not v["breaks"] and not v["edited"]
              and v["checkpoints"][0]["why"] == "the ledger was rewritten since this checkpoint", v)
    finally:
        os.unlink(db)


# ── §D answers ────────────────────────────────────────────────────────────────

def _turn(bom):
    tok = EV._TURN_BOM.set(copy.deepcopy(bom))
    try:
        ch = P.ProvenanceChain(session_id="s1", turn_number=1, user_uuid=OWNER, profile_type="llm_only")
        ch.add_step("query_intake", "hello", "q")
        sealed = ch.finalize()
    finally:
        EV._TURN_BOM.reset(tok)
    return {"turn": 1, "bom": bom, "user_query": "hello", "final_summary_text": "a", **sealed}


def t_turns():
    db = _tmp_db()
    try:
        S._connect(db).close()
        bom = {"version": 1, "profile_id": "p1", "agent": {"kind": "agent", "artifact_id": "p1", "state": "unsigned"},
               "parts": [{"kind": "skill", "artifact_id": "s", "state": "trusted", "w": 1.0}]}
        t = _turn(bom)
        IX.record_turn(OWNER, "s1", t, db_path=db)
        IX.record_turn(OWNER, "s1", t, db_path=db)
        v = _verify(db)
        check("D1 an answer's record is entered once however often it is saved", v["intact"] and v["entries"] == 1, v)
        t2 = copy.deepcopy(t)
        t2["bom"]["parts"][0]["state"] = "changed"
        IX.record_turn(OWNER, "s1", t2, db_path=db)
        v = _verify(db)
        check("D2 a changed record is entered again", v["intact"] and v["entries"] == 2, v)
        _exec(db, "UPDATE evidence_turns SET chain_tip_hash='forged'")
        v = _verify(db)
        check("D3 an edited answer record is named", not v["intact"] and v["edited_count"] == 1
              and v["edited"][0]["kind"] == "turn", v)
        _exec(db, "DELETE FROM evidence_turns")
        v = _verify(db)
        check("D4 a removed answer record is named", not v["intact"] and v["removed_count"] >= 1, v)
    finally:
        os.unlink(db)


# ── §E bundles ───────────────────────────────────────────────────────────────

NODE = r"""
const fs = require('fs');
const EV = require(process.argv[2]);
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
(async () => {
  const out = {};
  for (const [n, [text, fp]] of Object.entries(cases)) {
    try { const r = await EV.verifyBundle(text, fp); out[n] = { ok: r.ok, failed: r.failed, checked: r.checked, ledger: r.ledger ? r.ledger.ok : null }; }
    catch (e) { out[n] = { error: e.message }; }
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def t_bundle():
    db = _tmp_db()
    saved_db, saved_resolve = S._db_path, B._resolve_live
    S._db_path = lambda p=None: db
    text = "skill body"
    B._resolve_live = lambda kind, aid, owner: A.content_hash(text)
    try:
        S.sign("skill", "sk1", A.content_hash(text), signer_uuid=OWNER)
        env = S.sign("skill", "sk1", A.content_hash(text), signer_uuid=OWNER)
        bom = {"version": 1, "profile_id": "p1", "agent": {"kind": "agent", "artifact_id": "p1", "state": "unsigned"},
               "parts": [{"kind": "skill", "artifact_id": "sk1", "history_id": env["history_id"], "state": "trusted",
                          "label": "Café", "w": 1.0}]}
        t = _turn(bom)
        # An unrelated entry INSIDE the segment (between the attestation and the answer), so
        # removing it is caught only by the check that each entry links to the one before.
        S.sign("skill", "between", A.content_hash("b"), signer_uuid=OWNER)
        IX.record_turn(OWNER, "s1", t)
        # An unrelated later entry, so the segment's last entry covers none of the bundle's
        # records — cutting it is caught only by the check that the segment ends at the tip.
        S.sign("skill", "unrelated", A.content_hash("u"), signer_uuid=OWNER)
        b = B.build(OWNER, turns=[("s1", t)], artifacts=[("skill", "sk1")])
        led = b["ledger"]
        check("E1 a bundle carries a signed checkpoint and a complete segment",
              led and led["checkpoint"]["signed"] and led["complete_segment"]
              and led["entries"][-1]["entry_hash"] == led["checkpoint"]["tip_hash"], led)
        check("E2 every attestation carries its ledger record", all(a.get("ledger_record") for a in b["attestations"]))
        check("E3 the answer carries its ledger ref", b["turns"][0]["ledger_ref"] == L.turn_ref(OWNER, "s1", 1))
        fp = b["deployment_key_fingerprint"]
        txt = json.dumps(b)
        r = VE.verify_bundle(json.loads(txt), fp)
        check("E4 the Python verifier accepts the ledger proof", r["ok"] and r["ledger"]["ok"] is True, r.get("ledger"))
        cases = {"clean": (txt, fp)}
        t1 = copy.deepcopy(b)
        t1["attestations"][0]["ledger_record"]["recorded_at"] += 1.0
        cases["record edited"] = (json.dumps(t1), fp)
        t2 = copy.deepcopy(b)
        t2["ledger"]["entries"][0]["digest"] = "0" * 64
        cases["entry edited"] = (json.dumps(t2), fp)
        t3 = copy.deepcopy(b)
        t3["ledger"]["checkpoint"]["tip_hash"] = "0" * 64
        cases["checkpoint edited"] = (json.dumps(t3), fp)
        t3b = copy.deepcopy(b)
        sig = t3b["ledger"]["checkpoint"]["signature"]
        t3b["ledger"]["checkpoint"]["signature"] = ("A" if sig[0] != "A" else "B") + sig[1:]
        cases["checkpoint signature broken"] = (json.dumps(t3b), fp)
        t4 = copy.deepcopy(b)
        t4["ledger"]["entries"] = t4["ledger"]["entries"][:-1]
        cases["segment cut short"] = (json.dumps(t4), fp)
        t4b = copy.deepcopy(b)
        ents = t4b["ledger"]["entries"]
        mid = [i for i, e in enumerate(ents) if 0 < i < len(ents) - 1
               and e["ref"] not in {f"h:{a['history_id']}" for a in b["attestations"]} | {b["turns"][0]["ledger_ref"]}]
        check("E5a the segment holds an unrelated entry between the bundle's records", bool(mid), ents)
        if mid:
            del ents[mid[0]]
        cases["entry removed from the middle"] = (json.dumps(t4b), fp)
        t5 = copy.deepcopy(b)
        t5["turns"][0]["bom"]["parts"][0]["w"] = 1.5
        cases["answer record edited"] = (json.dumps(t5), fp)
        for name, (tx, f) in cases.items():
            if name == "clean":
                continue
            rr = VE.verify_bundle(json.loads(tx), f)
            check(f"E5 '{name}' fails the ledger check", rr["ledger"]["ok"] is False and not rr["ok"], rr.get("ledger"))
        node = shutil.which("node")
        if not node:
            check("E6 node available for the browser parity check", False)
            return
        d = tempfile.mkdtemp()
        try:
            cf = os.path.join(d, "c.json")
            with open(cf, "w", encoding="utf-8") as f:
                json.dump(cases, f)
            rf = os.path.join(d, "r.js")
            with open(rf, "w") as f:
                f.write(NODE)
            p = subprocess.run([node, rf, str(ROOT / "static/js/evidenceVerify.js"), cf],
                               capture_output=True, text=True, timeout=120)
            res = json.loads(p.stdout) if p.returncode == 0 else {}
        finally:
            shutil.rmtree(d, ignore_errors=True)
        for name, (tx, f) in cases.items():
            py = VE.verify_bundle(json.loads(tx), f)
            js = res.get(name) or {}
            check(f"E6 '{name}': the browser ledger verdict equals the Python one",
                  js.get("ok") == py["ok"] and js.get("ledger") == py["ledger"]["ok"] and js.get("checked") == py["checked"],
                  {"js": js, "py": {"ok": py["ok"], "ledger": py["ledger"]["ok"], "checked": py["checked"]}})
    finally:
        S._db_path, B._resolve_live = saved_db, saved_resolve
        os.unlink(db)


# ── §F words, scoping, surfaces ──────────────────────────────────────────────

def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def t_surfaces():
    w = V.ledger_words({"intact": True, "entries": 10, "backfilled": 4})
    check("F1 intact reads in words, naming what was entered when it began",
          w["attention"] == "pass" and w["words"] == "ledger intact · 10 entries · 4 entered when it began", w)
    w = V.ledger_words({"intact": False, "entries": 10, "edited_count": 1, "removed_count": 2,
                        "checkpoints": [{"ok": False}]})
    check("F2 not intact names every way it moved, as danger",
          w["attention"] == "danger" and "1 record edited after entry" in w["words"]
          and "2 records removed" in w["words"] and "1 earlier checkpoint no longer reached" in w["words"], w)
    check("F3 an unreadable ledger is never 'intact'", V.ledger_words({"intact": None})["attention"] == "na")
    r = _src("src/trusted_data_agent/api/evidence_routes.py")
    seg = r[r.index("def _ledger_status"):]
    check("F4 which records moved is returned only with VIEW_ALL_SESSIONS",
          "_can_view_all(user)" in seg and 'for k in ("breaks", "edited", "removed", "unledgered"):' in seg)
    s = _src("src/trusted_data_agent/core/trust/store.py")
    ah = s[s.index("def _append_history"):s.index("def record_attestation")]
    check("F5 the ledger entry is written after the history row, in its transaction",
          ah.index("INSERT INTO trust_attestation_history") < ah.index("_ledger.append_history(conn"))
    mg = s[s.index("def _migrate"):s.index("def _connect")]
    check("F6 the backfill runs before the migration's own seeding",
          mg.index("_ledger.backfill(conn)") < mg.index("missing = conn.execute("))
    check("F7 the migration seeds history only through the one seam",
          "INSERT INTO trust_attestation_history" not in mg)
    ev = _src("static/js/handlers/evidenceView.js")
    check("F8 the estate shows the ledger reading with its honest limit",
          "ledgerChip(ESTATE.ledger)" in ev and "could rebuild the whole chain" in ev)
    check("F9 the module is in the removal manifest",
          "ledger.py" in _src("src/trusted_data_agent/core/trust/README.md"))


def main():
    t_chain()
    t_store()
    t_detect()
    t_turns()
    t_bundle()
    t_surfaces()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
