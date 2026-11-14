#!/usr/bin/env python3
"""Prompts are plain text (9 Nov 2026) — the conversion of a database the former encryption left,
the release sync of the defaults, and the licence check that activates the enterprise package.

Until then every stored prompt was a Fernet token under a key derived from the licence's
signature and tier; ``agent/prompt_migration.py`` converts those rows once, in one transaction,
without touching history or versions. ``agent/prompt_defaults.py`` seeds a fresh database from
``schema/default_prompts/*.txt`` and lets the base prompts no person edited follow the release.
``core/license_file.validate`` is the one check of a licence file; ``LICENSED_TIERS`` names the
tiers that activate the enterprise package. CI-pure: temp databases, a throwaway key pair.

Run:  PYTHONPATH=src:. python test/test_prompt_migration.py
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {name} {detail}")


from trusted_data_agent.agent import prompt_defaults as PD  # noqa: E402
from trusted_data_agent.agent import prompt_encryption as PE  # noqa: E402
from trusted_data_agent.agent import prompt_migration as PM  # noqa: E402
from trusted_data_agent.core import license_file as LF  # noqa: E402

schema = open(os.path.join(ROOT, "schema/01_core_tables.sql"), encoding="utf-8").read()
TRIGGER_SQL = re.search(r"CREATE TRIGGER(?: IF NOT EXISTS)? create_prompt_version_on_update[\s\S]*?END;", schema).group(0)

OLD = ("aa" * 384, "App Developer")
NEW = ("bb" * 384, "Enterprise")
K_OLD = PE.derive_tier_key({"signature": OLD[0], "tier": OLD[1]})
K_NEW = PE.derive_tier_key({"signature": NEW[0], "tier": NEW[1]})
PLAIN = {1: "You are the strategic planner.", 2: "You write the final report.", 3: "[MIGRATE]"}


def fresh_db(key=K_OLD, record=None):
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE prompts (id INTEGER PRIMARY KEY, name TEXT, content TEXT, version INTEGER DEFAULT 1,
                              updated_by TEXT, updated_at TEXT);
        CREATE TABLE prompt_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_id INTEGER, version INTEGER,
                              content TEXT, changed_by TEXT, change_reason TEXT, created_at TEXT);
        CREATE TABLE prompt_overrides (id INTEGER PRIMARY KEY, prompt_id INTEGER, user_uuid TEXT, profile_id TEXT,
                              content TEXT, is_active INTEGER DEFAULT 1);
    """)
    c.execute(TRIGGER_SQL.rstrip(";"))
    for pid, txt in PLAIN.items():
        c.execute("INSERT INTO prompts (id, name, content) VALUES (?, ?, ?)",
                  (pid, f"P{pid}", txt if txt == "[MIGRATE]" else PE.encrypt_prompt(txt, key)))
    c.execute("INSERT INTO prompt_versions (prompt_id, version, content) VALUES (1, 0, ?)", (PE.encrypt_prompt("an older planner", key),))
    c.execute("INSERT INTO prompt_overrides (id, prompt_id, user_uuid, content) VALUES (1, 2, 'u1', ?)", (PE.encrypt_prompt("my own report style", key),))
    if record:
        c.execute(f"CREATE TABLE {PM.RECORD_TABLE} (id INTEGER PRIMARY KEY CHECK (id = 1), signature TEXT, tier TEXT, recorded_at TEXT)")
        c.execute(f"INSERT INTO {PM.RECORD_TABLE} VALUES (1, ?, ?, 'now')", record)
    c.commit(); c.close()
    return path


def state(path):
    c = sqlite3.connect(path)
    s = {"versions": c.execute("SELECT id, version FROM prompts ORDER BY id").fetchall(),
         "history": c.execute("SELECT COUNT(*) FROM prompt_versions").fetchone()[0],
         "trigger": c.execute("SELECT sql FROM sqlite_master WHERE name = ?", (PM.VERSION_TRIGGER,)).fetchone(),
         "record": bool(c.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (PM.RECORD_TABLE,)).fetchone()),
         "prompts": dict(c.execute("SELECT id, content FROM prompts").fetchall()),
         "history_rows": dict(c.execute("SELECT id, content FROM prompt_versions").fetchall()),
         "overrides": dict(c.execute("SELECT id, content FROM prompt_overrides").fetchall())}
    c.close()
    return s


# §E ─ what counts as encrypted ───────────────────────────────────────────────────────────────
print("§E is_encrypted")
check("a stored Fernet token is recognised", PM.is_encrypted(PE.encrypt_prompt("x", K_OLD)))
check("prose is never taken for a token", not PM.is_encrypted("You are the strategic planner. Z0FBQU is not you.")
      and not PM.is_encrypted("[MIGRATE]") and not PM.is_encrypted("") and not PM.is_encrypted(None))

# §C ─ the conversion ─────────────────────────────────────────────────────────────────────────
print("§C convert")
db = fresh_db(record=OLD)
before = state(db)
r = PM.decrypt_stored_prompts(db, candidates=None, keys_dir=tempfile.mkdtemp())   # the record names the key
after = state(db)
check("convert: every encrypted row is converted (2 prompts + 1 history + 1 override)",
      r["status"] == "converted" and r["converted"] == 4 and r["unreadable"] == 0, str(r))
check("convert: the plaintexts are back", after["prompts"][1] == PLAIN[1] and after["prompts"][2] == PLAIN[2]
      and after["history_rows"][1] == "an older planner" and after["overrides"][1] == "my own report style", after["prompts"])
check("convert: the [MIGRATE] placeholder is left as it is", after["prompts"][3] == "[MIGRATE]")
check("convert: no history row was added and no version moved", after["history"] == before["history"] and after["versions"] == before["versions"])
check("convert: the version trigger is back, with its own SQL", after["trigger"] == before["trigger"])
check("convert: the record table is gone — the conversion is over", before["record"] and not after["record"])
check("convert: a second start finds nothing to do", PM.decrypt_stored_prompts(db, candidates=[])["status"] == "nothing")
check("convert: no database is reported, not an error", PM.decrypt_stored_prompts(os.path.join(tempfile.gettempdir(), "nope-xyz.db"))["status"] == "no_db")

# §P ─ no record: the licence files are the candidates (current, then .previous) ─────────────
print("§P licence files")
kd = tempfile.mkdtemp()
with open(os.path.join(kd, "license.key"), "w") as f:
    json.dump({"payload": {"tier": NEW[1]}, "signature": NEW[0]}, f)
with open(os.path.join(kd, "license.key.previous"), "w") as f:
    json.dump({"payload": {"tier": OLD[1]}, "signature": OLD[0]}, f)
os.environ.pop("TDA_LICENSE_FILE", None)
cands = PM.candidate_licences(None, kd)
check("candidates: the current licence first, the replaced one next", cands == [NEW, OLD], cands)
db2 = fresh_db()   # keyed by OLD, no record
r = PM.decrypt_stored_prompts(db2, keys_dir=kd)
check("previous: rows keyed by the replaced licence convert from license.key.previous", r["status"] == "converted" and r["converted"] == 4, str(r))

# §U ─ a row no licence opens is left, counted, and the rest still converts ───────────────────
print("§U unreadable")
db3 = fresh_db()
c = sqlite3.connect(db3); c.execute("UPDATE prompts SET content = ? WHERE id = 2", (PE.encrypt_prompt("lost", K_NEW),)); c.commit(); c.close()
r = PM.decrypt_stored_prompts(db3, candidates=[OLD])
s3 = state(db3)
# (the UPDATE above fired the version trigger: a fifth encrypted row, the old text, joined the history)
check("unreadable: partial — four converted, one left encrypted", r["status"] == "partial" and r["converted"] == 4 and r["unreadable"] == 1, str(r))
check("unreadable: the row nobody can open is left as it is", PM.is_encrypted(s3["prompts"][2]) and s3["prompts"][1] == PLAIN[1])
check("unreadable: the trigger is back", s3["trigger"] == before["trigger"])
r = PM.decrypt_stored_prompts(db3, candidates=[OLD, NEW])
check("unreadable: the right licence later converts the rest", r["status"] == "converted" and r["converted"] == 1 and not PM.is_encrypted(state(db3)["prompts"][2]), str(r))

# §R ─ retiring history no licence opens: an operator's act, history only, dry run first ─────
print("§R retire")
db6 = fresh_db()
c = sqlite3.connect(db6)
c.execute("INSERT INTO prompt_versions (prompt_id, version, content, changed_by) VALUES (1, 5, ?, 'system')", (PE.encrypt_prompt("a lost version", K_NEW),))
c.execute("UPDATE prompts SET content = ? WHERE id = 2", (PE.encrypt_prompt("lost base", K_NEW),))   # the trigger files the OLD text (K_OLD) as history
c.commit(); c.close()
PM.decrypt_stored_prompts(db6, candidates=[OLD])            # converts what OLD opens; the two K_NEW rows stay
r = PM.retire_unreadable_history(db6, candidates=[OLD], dry_run=True)
check("retire: the dry run counts the unreadable history row and leaves the unreadable base prompt alone",
      r["status"] == "dry_run" and r["retired"] == 1 and r["left"] == 1, r)
check("retire: the dry run wrote nothing", len(PM.encrypted_rows(sqlite3.connect(db6))) == 2)
r = PM.retire_unreadable_history(db6, candidates=[OLD])
s6 = state(db6)
check("retire: the history row carries the marker, the base prompt is untouched",
      r["status"] == "retired" and r["retired"] == 1 and r["left"] == 1
      and any(v.startswith("[unreadable:") for v in s6["history_rows"].values()) and PM.is_encrypted(s6["prompts"][2]), (r, s6["history_rows"]))
check("retire: a second run finds nothing", PM.retire_unreadable_history(db6, candidates=[OLD])["status"] == "nothing")

# §S ─ the defaults follow the release, never a person's hand ────────────────────────────────
print("§S sync_defaults")
DEF = {"P1": "planner v2", "P2": "report v2", "P3": "classifier v1", "P4": "not in the database"}
db4 = fresh_db()
PM.decrypt_stored_prompts(db4, candidates=[OLD])
c = sqlite3.connect(db4)
c.execute("UPDATE prompts SET content = 'edited by hand', updated_by = 'user-7' WHERE id = 2")   # a person's edit (trigger: v2 + history)
hist_before = c.execute("SELECT COUNT(*) FROM prompt_versions").fetchone()[0]
r = PD.sync_defaults(c, DEF)
rows = dict(c.execute("SELECT name, content FROM prompts").fetchall())
vers = dict(c.execute("SELECT name, version FROM prompts").fetchall())
hist = c.execute("SELECT prompt_id, version, content, changed_by FROM prompt_versions ORDER BY id").fetchall()
check("sync: an unedited base prompt follows the release", rows["P1"] == "planner v2" and r["updated"] == 1, (rows, r))
check("sync: its replaced text is kept in the history, version bumped",
      any(h[0] == 1 and h[2] == PLAIN[1] for h in hist) and vers["P1"] == 2, (hist, vers))
check("sync: a prompt a person edited is kept", rows["P2"] == "edited by hand" and r["kept"] == 1)
check("sync: the [MIGRATE] placeholder is seeded with a version-1 row", rows["P3"] == "classifier v1" and r["seeded"] == 1
      and any(h[0] == 3 and h[1] == 1 and h[2] == "classifier v1" for h in hist))
check("sync: a default the database does not know is ignored", "P4" not in rows)
r2 = PD.sync_defaults(c, DEF)
check("sync: a second run changes nothing", r2["updated"] == 0 and r2["seeded"] == 0 and r2["unchanged"] == 2, r2)
c.execute("UPDATE prompts SET content = ?, updated_by = 'system' WHERE id = 1", (PE.encrypt_prompt("still locked", K_NEW),))
r3 = PD.sync_defaults(c, DEF)
check("sync: a row still encrypted is never overwritten", PM.is_encrypted(dict(c.execute("SELECT name, content FROM prompts").fetchall())["P1"]) and r3["kept"] >= 1, r3)
c.close()
shipped = PD.load_defaults(ROOT)
check("the shipped defaults are plain text files, fifteen or more, none a token",
      len(shipped) >= 15 and all(not PM.is_encrypted(v) and v.strip() for v in shipped.values()), len(shipped))
check("the shipped set carries the planner and master prompts",
      {"WORKFLOW_META_PLANNING_PROMPT", "WORKFLOW_TACTICAL_PROMPT", "MASTER_SYSTEM_PROMPT"} <= set(shipped))

# §L ─ the licence check ─────────────────────────────────────────────────────────────────────
print("§L licence")
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402
kd2 = tempfile.mkdtemp()
priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pub_path = os.path.join(kd2, "public_key.pem")
open(pub_path, "wb").write(priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))


def issue(tier, days, name="license.key", tamper=False):
    now = datetime.now(timezone.utc)
    payload = {"holder": "t", "tier": tier, "issued_at": now.isoformat(), "expires_at": (now + timedelta(days=days)).isoformat()}
    sig = priv.sign(json.dumps(payload, sort_keys=True).encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    if tamper:
        payload["tier"] = "Partner"
    path = os.path.join(kd2, name)
    json.dump({"payload": payload, "signature": sig.hex()}, open(path, "w"))
    return path


check("a valid Enterprise licence validates to its payload", LF.validate(issue("Enterprise", 30), pub_path)["tier"] == "Enterprise")
check("Partner is a licensed tier, Community and the retired tiers are not",
      "Partner" in LF.LICENSED_TIERS and "Enterprise" in LF.LICENSED_TIERS
      and not {"Community", "App Developer", "Prompt Engineer", "Enterprise Light", "Standard"} & set(LF.LICENSED_TIERS))
for name, kw in (("an expired licence", dict(tier="Enterprise", days=-1)), ("a tampered payload", dict(tier="Enterprise", days=30, tamper=True))):
    try:
        LF.validate(issue(**kw), pub_path); ok = False
    except LF.LicenseError:
        ok = True
    check(f"{name} is refused as LicenseError", ok)
try:
    LF.validate(os.path.join(kd2, "missing.key"), pub_path); ok = False
except LF.LicenseError:
    ok = True
check("a missing file is refused as LicenseError (load_validated turns it into None)", ok)
from trusted_data_agent.core import enterprise as EN  # noqa: E402
from trusted_data_agent.core.config import APP_STATE  # noqa: E402
APP_STATE["license_info"] = None
check("no licence reads Community and is not licensed", EN.license_tier() == "Community" and not EN.is_licensed())
APP_STATE["license_info"] = {"tier": "Partner"}
check("a Partner licence is licensed", EN.is_licensed())
APP_STATE["license_info"] = {"tier": "App Developer"}
check("a retired tier is not licensed", not EN.is_licensed())
APP_STATE["license_info"] = None

print(f"\ntest_prompt_migration: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
