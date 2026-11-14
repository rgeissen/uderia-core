"""PROP-009 increment 1 — attestation history, owner-scoped agent attestations, signing keys.

CI-pure: temp SQLite DBs, the real store, the real migration. No server, no network.

What it asserts, and why each rule exists:

  §A  The migration rebuilds a pre-scoping table WITHOUT changing any attested hash, signer,
      signature or signed flag — every badge reads exactly as before (the frozen-baseline
      rule: an infrastructure change must not move a signature).
  §B  Owner attribution is the one rule `attribute_owner` states: a sole holder wins (covers
      a signature reinstated from a pack, where the signer is the publisher); a shared
      bootstrap id goes to the signer only if the signer holds it; anything else stays
      unassigned and is LISTED, never guessed.
  §C  Two accounts' same-id agents have separate attestations: one signing, re-signing,
      revoking or deleting never touches the other's.
  §D  A caller that forgets the owner reads the unassigned bucket only — never another
      account's signature (fail-safe: unsigned, not borrowed-green).
  §E  History: every sign / reinstate / revoke / retire is appended; a re-sign does not erase
      the earlier signature; a superseded signature is still findable by its hash and still
      verifies.
  §F  Signing keys: the local key is registered on sign; a rotated key retires the older one;
      a reinstated attestation registers the publisher's key as carried; verify() names the
      key status.
  §G  Every production call site that reads or writes an owner-scoped kind passes an owner
      (AST scan) — the structural guard that keeps §D from being the normal path.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trusted_data_agent.core.trust import store as S  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, cond, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        print(f"  FAIL: {name}" + (f" — {detail}" if detail else ""))


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    S._MIGRATED_PATHS.discard(path)
    return path


_OLD_DDL = """
CREATE TABLE trust_attestations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, artifact_id TEXT NOT NULL,
    version_hash TEXT NOT NULL, verdict_json TEXT, signer_uuid TEXT, signer_fingerprint TEXT,
    signature TEXT, signed INTEGER NOT NULL DEFAULT 0, signed_at REAL NOT NULL,
    UNIQUE(kind, artifact_id));
"""


def _legacy_db(rows, prefs=None) -> str:
    """A DB in the PRE-PROP-009 shape (and pre-portability: no signer_public_key column),
    plus optional user_preferences {user_id: [profile ids]}."""
    path = _tmp_db()
    c = sqlite3.connect(path)
    c.executescript(_OLD_DDL)
    for r in rows:
        c.execute("INSERT INTO trust_attestations (kind, artifact_id, version_hash, verdict_json, "
                  "signer_uuid, signer_fingerprint, signature, signed, signed_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", r)
    if prefs is not None:
        c.execute("CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, preferences_json TEXT)")
        for uid, pids in prefs.items():
            c.execute("INSERT INTO user_preferences VALUES (?,?)",
                      (uid, json.dumps({"profiles": [{"id": p} for p in pids]})))
    c.commit()
    c.close()
    return path


# ── §A migration preserves every reading ─────────────────────────────────────

def t_migration_preserves():
    rows = [
        ("ontology", "kg1", "h-onto", json.dumps({"status": "conformant"}), "u1", "fp1", "sigA", 1, 10.0),
        ("skill", "sk1", "h-skill", None, "u1", "fp1", "sigB", 1, 11.0),
        ("agent", "profile-default-chat", "h-agent", None, "alice", "fp1", "sigC", 1, 12.0),
        ("agent_deployment", "profile-default-chat", "h-dep", None, "bob", "fp1", "sigD", 1, 13.0),
        ("agent", "profile-only-carol", "h-carol", None, "publisher", "fpP", "sigE", 1, 14.0),
        ("agent", "profile-orphan", "h-orph", None, "ghost", "fp1", "sigF", 1, 15.0),
        ("knowledge", "7", "h-k", None, "u1", "fp1", "", 0, 16.0),      # unsigned row
    ]
    prefs = {"alice": ["profile-default-chat"], "bob": ["profile-default-chat"],
             "carol": ["profile-only-carol"]}
    path = _legacy_db(rows, prefs)
    try:
        before = {(r[0], r[1]): r for r in rows}
        # first touch migrates
        S.load_attestation("ontology", "kg1", db_path=path)
        c = sqlite3.connect(path)
        cols = {r[1] for r in c.execute("PRAGMA table_info(trust_attestations)")}
        check("A1 owner_uuid column exists after migration", "owner_uuid" in cols)
        check("A1 history_id column exists after migration", "history_id" in cols)
        check("A1 signer_public_key column exists after migration", "signer_public_key" in cols)
        n = c.execute("SELECT COUNT(*) FROM trust_attestations").fetchone()[0]
        check("A2 every row copied", n == len(rows), f"{n} vs {len(rows)}")
        for (kind, aid, vh, vj, su, sf, sig, signed, sat, own) in c.execute(
                "SELECT kind, artifact_id, version_hash, verdict_json, signer_uuid, "
                "signer_fingerprint, signature, signed, signed_at, owner_uuid "
                "FROM trust_attestations"):
            b = before[(kind, aid)]
            check(f"A3 {kind}/{aid} hash/signer/signature/signed unchanged",
                  (vh, vj, su, sf, sig, signed, sat) == (b[2], b[3], b[4], b[5], b[6], b[7], b[8]))
        owners = dict(((k, a), o) for k, a, o in c.execute(
            "SELECT kind, artifact_id, owner_uuid FROM trust_attestations"))
        c.close()
        # §B attribution, through the migration
        check("B1 shared bootstrap id → the signer who holds it (agent)",
              owners[("agent", "profile-default-chat")] == "alice")
        check("B1 shared bootstrap id → the signer who holds it (deployment)",
              owners[("agent_deployment", "profile-default-chat")] == "bob")
        check("B2 sole holder wins even when the signer is the publisher",
              owners[("agent", "profile-only-carol")] == "carol")
        check("B3 nobody holds it → unassigned, never guessed",
              owners[("agent", "profile-orphan")] == "")
        check("B4 non-scoped kinds stay ''", owners[("ontology", "kg1")] == ""
              and owners[("skill", "sk1")] == "")
        # readings through the store: scoped reads need the owner
        check("A4 ontology attested hash unchanged", S.attested_hash("ontology", "kg1", db_path=path) == "h-onto")
        check("A4 alice's agent reads her hash",
              S.attested_hash("agent", "profile-default-chat", db_path=path, owner_uuid="alice") == "h-agent")
        check("A4 bob's deployment reads his hash",
              S.attested_hash("agent_deployment", "profile-default-chat", db_path=path, owner_uuid="bob") == "h-dep")
        check("A4 unsigned row still unsigned", S.attested_hash("knowledge", "7", db_path=path) is None)
        # history seeded
        hist = S.history("ontology", "kg1", db_path=path)
        check("A5 a 'migrated' history row seeded for each current attestation",
              len(hist) == 1 and hist[0]["event"] == "migrated" and hist[0]["version_hash"] == "h-onto")
        rec = S.load_attestation("ontology", "kg1", db_path=path)
        check("A5 current row points at its history row", rec["history_id"] == hist[0]["id"])
        # idempotent: a second process (cleared cache) migrates nothing new
        S._MIGRATED_PATHS.discard(path)
        S.load_attestation("ontology", "kg1", db_path=path)
        check("A6 migration idempotent (history not duplicated)",
              len(S.history("ontology", "kg1", db_path=path)) == 1)
        un = S.unassigned_agent_attestations(db_path=path)
        check("B5 unassigned rows are listed for review",
              [(u["kind"], u["artifact_id"]) for u in un] == [("agent", "profile-orphan")])
        check("B5 the review list says why (no account holds it)",
              un and un[0]["holders"] == [] and "no account holds" in un[0]["reason"])
        check("B6 assign_owner resolves an unassigned row",
              S.assign_owner("agent", "profile-orphan", "dave", db_path=path)
              and S.attested_hash("agent", "profile-orphan", db_path=path, owner_uuid="dave") == "h-orph")
        check("B6 assign_owner refuses a row that is not unassigned",
              not S.assign_owner("agent", "profile-default-chat", "zed", db_path=path))
    finally:
        os.unlink(path)


# ── §C/§D two accounts, one id ───────────────────────────────────────────────

def t_owner_isolation():
    path = _tmp_db()
    try:
        S.record_attestation("agent", "profile-default-chat", "hA", signed=True, signature="s",
                             signer_uuid="alice", db_path=path, owner_uuid="alice", event="sign")
        check("C1 bob does not see alice's signature",
              S.attested_hash("agent", "profile-default-chat", db_path=path, owner_uuid="bob") is None)
        S.record_attestation("agent", "profile-default-chat", "hB", signed=True, signature="s",
                             signer_uuid="bob", db_path=path, owner_uuid="bob", event="sign")
        check("C2 bob signing does not replace alice's",
              S.attested_hash("agent", "profile-default-chat", db_path=path, owner_uuid="alice") == "hA")
        S.revoke("agent", "profile-default-chat", db_path=path, owner_uuid="bob")
        check("C3 bob revoking leaves alice signed",
              S.attested_hash("agent", "profile-default-chat", db_path=path, owner_uuid="alice") == "hA")
        S.record_attestation("agent_deployment", "profile-default-chat", "dB", signed=True,
                             signature="s", db_path=path, owner_uuid="bob")
        n = S.retire("profile-default-chat", ("agent", "agent_deployment"), db_path=path,
                     owner_uuid="bob")
        check("C4 deleting bob's agent retires only bob's rows", n == 1)
        check("C4 alice still signed after bob's deletion",
              S.attested_hash("agent", "profile-default-chat", db_path=path, owner_uuid="alice") == "hA")
        check("D1 owner-less read sees neither account",
              S.attested_hash("agent", "profile-default-chat", db_path=path) is None)
        check("D2 owner is ignored for non-scoped kinds",
              S.owner_key("ontology", "alice") == "" and S.owner_key("agent", "alice") == "alice")
        S.record_attestation("ontology", "kg9", "ho", signed=True, signature="s", db_path=path,
                             owner_uuid="alice")
        check("D3 a non-scoped attestation is readable with any (or no) owner",
              S.attested_hash("ontology", "kg9", db_path=path) == "ho"
              and S.attested_hash("ontology", "kg9", db_path=path, owner_uuid="bob") == "ho")
    finally:
        os.unlink(path)


# ── §E history ───────────────────────────────────────────────────────────────

def t_history():
    path = _tmp_db()
    try:
        h1 = S.record_attestation("ontology", "kgH", "v1", signed=True, signature="s1",
                                  db_path=path, event="sign")
        h2 = S.record_attestation("ontology", "kgH", "v2", signed=True, signature="s2",
                                  db_path=path, event="sign")
        check("E1 re-sign makes v2 current", S.attested_hash("ontology", "kgH", db_path=path) == "v2")
        hist = S.history("ontology", "kgH", db_path=path)
        check("E2 both signatures remain in history",
              [h["version_hash"] for h in hist] == ["v2", "v1"])
        check("E3 the superseded version is findable by its hash",
              [h["id"] for h in S.signed_history_for_hash("ontology", "v1", db_path=path)] == [h1])
        check("E4 current_history_id points at the newest sign",
              S.current_history_id("ontology", "kgH", db_path=path) == h2)
        S.revoke("ontology", "kgH", db_path=path, actor_uuid="admin")
        hist = S.history("ontology", "kgH", db_path=path)
        check("E5 revoke appended, not deleted from history",
              hist[0]["event"] == "revoke" and hist[0]["actor_uuid"] == "admin" and len(hist) == 3)
        check("E5 revoked → current_history_id None",
              S.current_history_id("ontology", "kgH", db_path=path) is None)
        check("E6 history_entry returns the row a bill of materials references",
              (S.history_entry(h1, db_path=path) or {}).get("version_hash") == "v1")
        S.retire("kgH", ("ontology",), db_path=path)
        check("E7 retiring an absent row appends nothing",
              len(S.history("ontology", "kgH", db_path=path)) == 3)
    finally:
        os.unlink(path)


# ── §E/§F real signatures + keys ─────────────────────────────────────────────

def t_real_signing_and_keys():
    try:
        from trusted_data_agent.core import provenance
        pem = provenance.get_provenance_public_key_pem()
    except Exception:
        pem = None
    if not pem:
        print("  (skipping real-signing section: no provenance key in this checkout)")
        return
    path = _tmp_db()
    try:
        env1 = S.sign("skill", "skX", "c1", db_path=path)
        check("F1 sign returns its history id", isinstance(env1.get("history_id"), int))
        fp = env1.get("key_fingerprint")
        check("F2 local key registered and active", S.key_status(fp, db_path=path) == "active")
        S.sign("skill", "skX", "c2", db_path=path)
        old = S.signed_history_for_hash("skill", "c1", db_path=path)[0]
        check("E8 a superseded signature still verifies from history",
              S.verify_record(old).get("verified") is True)
        v = S.verify("skill", "skX", db_path=path)
        check("F3 verify names the key status", v.get("verified") and v.get("key_status") == "active")
        # rotation: a new local fingerprint retires the old one
        S.register_key("fp-new-local", "PEM", "local", db_path=path)
        check("F4 rotation retires the earlier local key", S.key_status(fp, db_path=path) == "retired")
        check("F4 the new key is active", S.key_status("fp-new-local", db_path=path) == "active")
        check("F5 the old signature still verifies after rotation",
              S.verify("skill", "skX", db_path=path).get("verified") is True)
        # carried key
        S.record_attestation("ontology", "kgC", "hc", signed=True, signature="x",
                             signer_fingerprint="fp-publisher", signer_public_key="PUBPEM",
                             db_path=path, event="reinstate")
        check("F6 a reinstated attestation registers the publisher key as carried",
              S.key_status("fp-publisher", db_path=path) == "carried")
        check("F7 unknown fingerprint reads unknown", S.key_status("nope", db_path=path) == "unknown")
        check("F8 list_keys lists all three", len(S.list_keys(db_path=path)) == 3)
    finally:
        os.unlink(path)


# ── §G every production caller of a scoped kind passes an owner ──────────────

_STORE_FNS = {"attested_hash", "load_attestation", "sign", "verify", "revoke", "retire",
              "record_attestation", "current_history_id", "history"}
_SCOPED_LITERALS = {"agent", "agent_deployment"}
_SCOPED_NAMES = {"AGENT_KIND", "DEPLOYMENT_KIND"}


def _mentions_scoped(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and n.value in _SCOPED_LITERALS:
            return True
        if isinstance(n, ast.Name) and n.id in _SCOPED_NAMES:
            return True
        if isinstance(n, ast.Attribute) and n.attr in _SCOPED_NAMES:
            return True
    return False


def t_callers_pass_owner():
    src = ROOT / "src" / "trusted_data_agent"
    offenders = []
    scanned = 0
    for py in src.rglob("*.py"):
        if py.name == "store.py" and py.parent.name == "trust":
            continue
        try:
            tree = ast.parse(py.read_text())
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else None
            if name not in _STORE_FNS:
                continue
            base = f.value
            base_name = base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else "")
            if "store" not in base_name.lower():
                continue
            if not any(_mentions_scoped(a) for a in node.args) and \
               not any(_mentions_scoped(k.value) for k in node.keywords if k.arg != "owner_uuid"):
                # a generic call (kind is a variable) is checked below
                kind_var = node.args[0] if (name != "retire" and node.args) else None
                if not (isinstance(kind_var, ast.Name) and kind_var.id in {"kind", "_kind"}):
                    continue
            scanned += 1
            if not any(k.arg == "owner_uuid" for k in node.keywords):
                offenders.append(f"{py.relative_to(ROOT)}:{node.lineno} {name}")
    check("G1 the scan found the scoped call sites (non-vacuous)", scanned >= 10, f"{scanned}")
    check("G2 every scoped / generic-kind store call passes owner_uuid", not offenders,
          "; ".join(offenders[:20]))


def main():
    for fn in (t_migration_preserves, t_owner_isolation, t_history, t_real_signing_and_keys,
               t_callers_pass_owner):
        try:
            fn()
        except Exception as e:  # a raising section is a NAMED failure, never an abort
            global FAILED
            FAILED += 1
            import traceback
            print(f"  FAIL: {fn.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
