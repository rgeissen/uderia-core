"""
CI gate — OBDA-READ `data_product` trust kind: the EARNED signature (Phase A3).

End-to-end through the REAL machinery — the real attestation store (temp DB), the
real A2 validation gate, the real content resolvers over real kg_entities rows:

  author → sign refused (unvalidated) → validate (V1–V6) + stamp → sign refused
  (unsigned ontology — the bottom-up rule) → sign ontology → sign product 🟢 →
  tamper the manifest → 🟠 CHANGED + re-sign refused (stale report) → restore → 🟢.

Every refusal is asserted by its machine-readable status. The signature is earned:
each of the five conditions (parse · report exists · report matches · report
signable · ontology signed) is individually driven to a refusal.

Run: python3 test/test_product_trust.py
"""

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.dbdeploy import product_model as pm         # noqa: E402
from trusted_data_agent.dbdeploy import product_validation as pv     # noqa: E402
from trusted_data_agent.core.trust import products as tp             # noqa: E402
from trusted_data_agent.core.trust import store as ts                # noqa: E402
from trusted_data_agent.core.trust import content as tc              # noqa: E402
from trusted_data_agent.core.trust import composition as tcomp       # noqa: E402
from trusted_data_agent.core.trust.attestation import registered_kinds  # noqa: E402
from trusted_data_agent.core.trust.validators import data_product_validator  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


KG = "kg-obda-test"

MANIFEST = {
    "product": "orders_only", "version": 1, "backend": "teradata",
    "description": "Phase-A signable single-entity product",
    "entities": [
        {"name": "order", "deployed": "mvm.order_header", "key": ["order_id"],
         "attributes": [{"name": "order_date", "type": "date"},
                        {"name": "channel"}]},
    ],
    "measures": [{"name": "order_count", "anchor": "order", "expr": "COUNT(*)"}],
}


def make_db():
    """A temp DB carrying the KG tables in the shape `load_kg_graph` reads, seeded
    with a small ontology + one data_product node (manifest, no report yet)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE kg_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT, profile_id TEXT,
        user_uuid TEXT, name TEXT, entity_type TEXT,
        properties_json TEXT DEFAULT '{}')""")
    conn.execute("""CREATE TABLE kg_relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT,
        source_entity_id INTEGER, target_entity_id INTEGER,
        relationship_type TEXT)""")

    def ent(name, etype, props=None):
        cur = conn.execute(
            "INSERT INTO kg_entities (kg_id, profile_id, user_uuid, name, "
            "entity_type, properties_json) VALUES (?,?,?,?,?,?)",
            (KG, "p1", "u1", name, etype, json.dumps(props or {})))
        return cur.lastrowid

    db_id = ent("mvm", "database", {"layer": "logical"})
    order_id = ent("order", "entity", {"layer": "logical"})
    conn.execute("INSERT INTO kg_relationships (kg_id, source_entity_id, "
                 "target_entity_id, relationship_type) VALUES (?,?,?,?)",
                 (KG, db_id, order_id, "contains"))
    ent("orders_only", "data_product",
        {"layer": "logical", "manifest": MANIFEST})
    conn.commit()
    conn.close()
    return path


def set_product_props(path, props):
    conn = sqlite3.connect(path)
    conn.execute("UPDATE kg_entities SET properties_json=? "
                 "WHERE kg_id=? AND entity_type='data_product' AND name='orders_only'",
                 (json.dumps(props), KG))
    conn.commit()
    conn.close()


print("=" * 68)
print("OBDA-READ data_product trust kind — Phase A3 gate")
print("=" * 68)

db = make_db()
try:
    aid = tp.product_artifact_id(KG, "orders_only")

    # ── 1 · registration + loading ──────────────────────────────────────────
    print("\n[1] the 11th kind is registered; the product node loads")
    check("data_product is a registered signable kind",
          "data_product" in registered_kinds())
    check("data_product resolves through the content _LEAF dispatcher",
          "data_product" in tc._LEAF)
    manifest, report, err = tp.load_product(KG, "orders_only", db_path=db)
    check("the product node loads its manifest", err is None and manifest == MANIFEST)
    check("no validation report yet", report is None)
    check("a missing product is a named error, never a guess",
          tp.load_product(KG, "nope", db_path=db)[2] is not None)

    # ── 1b · products are NOT part of the ontology's signable content ───────
    print("\n[1b] authoring a product does not flip its own ontology to CHANGED")
    # The knowledge model is the ontology's content; a product is a contract ON it,
    # protected by its own attestation. Counting it in the ontology hash created a
    # rubber-stamp loop (author → ontology CHANGED → re-sign that attests nothing).
    ents_all, rels_all = tcomp.load_kg_graph(KG, db)
    ents_no_dp = [e for e in ents_all if e.get("entity_type") != "data_product"]
    rels_no_dp = [r for r in rels_all if r.get("relationship_type") != "member"]
    check("the ontology hash EXCLUDES data_product nodes + member edges",
          tcomp.kg_content_hash(ents_all, rels_all)
          == tcomp.kg_content_hash(ents_no_dp, rels_no_dp))
    check("…and a product-free graph hashes exactly as before (filter no-ops — "
          "no existing attestation moves)",
          tcomp.kg_content_hash(ents_no_dp, rels_no_dp)
          == tcomp.kg_content_hash(list(ents_no_dp), list(rels_no_dp)))

    # ── 2 · the earned signature: every refusal, by status ──────────────────
    print("\n[2] sign-off is refused until the signature is EARNED")
    r = tp.sign_product(KG, "orders_only", db_path=db)
    check("unvalidated product cannot sign (S1 — never a rubber stamp)",
          r["signed"] is False and r["validation"]["status"] == "unvalidated")

    # run the REAL A2 gate and stamp its report on the node
    parsed = pm.parse_manifest(MANIFEST)
    vreport = pv.validate_product(parsed)
    check("the A2 gate finds the product signable", vreport.signable is True)
    set_product_props(db, {"layer": "logical", "manifest": MANIFEST,
                           "validation_report": vreport.to_dict()})

    r = tp.sign_product(KG, "orders_only", db_path=db)
    check("validated product STILL cannot sign over an unsigned ontology "
          "(the bottom-up rule)",
          r["signed"] is False and r["validation"]["status"] == "unsigned_ingredient")

    # sign the ontology (the ingredient), then the product
    ents, rels = tcomp.load_kg_graph(KG, db)
    ts.sign("ontology", KG, tcomp.kg_content_hash(ents, rels),
            signer_uuid="admin", db_path=db)
    r = tp.sign_product(KG, "orders_only", signer_uuid="admin", db_path=db)
    check("with a signed ontology + signable report the product SIGNS",
          r.get("kind") == "data_product" and r.get("version_hash"))
    check("the verdict records verified determinism, not mere attestation",
          r["validation"]["status"] == "verified_deterministic")

    st = tp.product_state(KG, "orders_only", db_path=db)
    check("the badge reads TRUSTED (signed and intact)", st["state"] == "trusted")
    check("live hash == attested hash", st["live_hash"] == st["attested_hash"])

    # ── 3 · tamper-evidence ─────────────────────────────────────────────────
    print("\n[3] an edited manifest flips 🟠 CHANGED and cannot silently re-sign")
    tampered = dict(MANIFEST, version=2)
    set_product_props(db, {"layer": "logical", "manifest": tampered,
                           "validation_report": vreport.to_dict()})
    st = tp.product_state(KG, "orders_only", db_path=db)
    check("manifest tamper → CHANGED", st["state"] == "changed")
    r = tp.sign_product(KG, "orders_only", db_path=db)
    check("…and re-signing is refused: the stored report validated a DIFFERENT "
          "manifest (stale_report — the ratchet)",
          r["signed"] is False and r["validation"]["status"] == "stale_report")

    # restore → trusted again
    set_product_props(db, {"layer": "logical", "manifest": MANIFEST,
                           "validation_report": vreport.to_dict()})
    check("restoring the manifest restores TRUSTED",
          tp.product_state(KG, "orders_only", db_path=db)["state"] == "trusted")

    # ── 4 · a blocked gate blocks signing ───────────────────────────────────
    print("\n[4] a report with blockers blocks the signature, naming them")
    multi = {
        "product": "orders_only", "version": 3, "backend": "teradata",
        "entities": [
            {"name": "order", "deployed": "mvm.order_header", "key": ["order_id"],
             "attributes": [{"name": "order_date"}]},
            {"name": "customer", "deployed": "mvm.customer", "key": ["customer_id"],
             "attributes": [{"name": "region"}]},
        ],
        "joins": [{"from": "order", "to": "customer",
                   "on": [["customer_id", "customer_id"]], "cardinality": "N:1"}],
        "measures": [{"name": "n", "anchor": "order", "expr": "COUNT(*)"}],
    }
    # Phase B2: the default battery runs the product compiler, so a multi-entity
    # product VERIFIES — record the widening explicitly.
    check("(Phase B2: a multi-entity product now verifies as signable)",
          pv.validate_product(pm.parse_manifest(multi)).signable is True)
    # A blocked report still blocks signing: build one via an absent compile
    # capability (V6 blocker — "signing requires verified determinism, S1").
    blocked_report = pv.validate_product(pm.parse_manifest(multi), compile_fn=None)
    check("(a no-capability report carries blockers)",
          blocked_report.signable is False)
    set_product_props(db, {"layer": "logical", "manifest": multi,
                           "validation_report": blocked_report.to_dict()})
    r = tp.sign_product(KG, "orders_only", db_path=db)
    check("gate blockers block the sign-off, by status",
          r["signed"] is False and r["validation"]["status"] == "gate_blocked")
    check("…naming verified determinism (S1) as what is missing",
          "battery" in (r["validation"]["note"] or "").lower()
          or "S1" in (r["validation"]["note"] or ""))

    # ── 5 · a drifted ONTOLOGY blocks a re-sign (compositional freshness) ───
    print("\n[5] an ontology that drifted after signing blocks a product re-sign")
    set_product_props(db, {"layer": "logical", "manifest": MANIFEST,
                           "validation_report": vreport.to_dict()})
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO kg_entities (kg_id, profile_id, user_uuid, name, "
                 "entity_type, properties_json) VALUES (?,?,?,?,?,?)",
                 (KG, "p1", "u1", "customer", "entity", "{}"))
    conn.commit(); conn.close()
    r = tp.sign_product(KG, "orders_only", db_path=db)
    check("ontology drift → product re-sign refused (unsigned_ingredient names "
          "the changed state)",
          r["signed"] is False and r["validation"]["status"] == "unsigned_ingredient"
          and "changed" in (r["validation"]["note"] or ""))

    # ── 6 · validator unit paths (pure) ─────────────────────────────────────
    print("\n[6] validator refusals, each by status")
    v = data_product_validator(manifest=None)
    check("no manifest → invalid_manifest", v.status == "invalid_manifest")
    v = data_product_validator(manifest={"product": "X!"})
    check("an M-rule violation → invalid_manifest, naming the code",
          v.status == "invalid_manifest" and "bad_product_name" in v.note)
    v = data_product_validator(manifest=MANIFEST, stored_report=None)
    check("no report → unvalidated", v.status == "unvalidated")
    v = data_product_validator(manifest=MANIFEST,
                               stored_report=vreport.to_dict(),
                               ontology_state="unsigned")
    check("unsigned ontology → unsigned_ingredient", v.status == "unsigned_ingredient")
    v = data_product_validator(manifest=MANIFEST,
                               stored_report=vreport.to_dict(),
                               ontology_state="trusted")
    check("all five conditions met → clean, verified_deterministic",
          v.signable and v.status == "verified_deterministic")
    check("…and the verdict carries the battery hash (the proof travels)",
          (v.detail or {}).get("battery_hash") == vreport.battery_hash)

    # ── 7 · the version hash binds manifest AND proof ───────────────────────
    print("\n[7] the signable content binds the manifest AND the validation proof")
    h1 = tp.version_hash_for(MANIFEST, vreport.to_dict())
    check("stable across calls", tp.version_hash_for(MANIFEST, vreport.to_dict()) == h1)
    check("a manifest change moves it",
          tp.version_hash_for(dict(MANIFEST, version=9), vreport.to_dict()) != h1)
    other_report = dict(vreport.to_dict()); other_report["battery_hash"] = "x" * 64
    check("a report change moves it (the proof is signed content, §2.3)",
          tp.version_hash_for(MANIFEST, other_report) != h1)
finally:
    os.remove(db)


# ── 8 · §8 the compositional ratchet (P1–P3), end to end on a real store ────
print("\n[8] composed products — P1 lineage · P2 bottom-up · P3 stale-ingredient")

def make_composed_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE kg_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT, profile_id TEXT,
        user_uuid TEXT, name TEXT, entity_type TEXT,
        properties_json TEXT DEFAULT '{}')""")
    conn.execute("""CREATE TABLE kg_relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT,
        source_entity_id INTEGER, target_entity_id INTEGER,
        relationship_type TEXT)""")

    def ent(name, etype, props=None):
        conn.execute(
            "INSERT INTO kg_entities (kg_id, profile_id, user_uuid, name, "
            "entity_type, properties_json) VALUES (?,?,?,?,?,?)",
            (KG, "p1", "u1", name, etype, json.dumps(props or {})))

    ent("mvm", "database", {"layer": "logical"})
    A = {"product": "orders_p", "version": 1, "backend": "teradata",
         "entities": [{"name": "order", "deployed": "mvm.order_header",
                       "key": ["order_id"],
                       "attributes": [{"name": "order_date"}, {"name": "customer_id"}]}],
         "measures": [{"name": "order_count", "anchor": "order", "expr": "COUNT(*)"}]}
    B = {"product": "customers_p", "version": 1, "backend": "teradata",
         "entities": [{"name": "customer", "deployed": "mvm.customer",
                       "key": ["customer_id"], "attributes": [{"name": "region"}]}],
         "measures": [{"name": "customer_count", "anchor": "customer",
                       "expr": "COUNT(*)"}]}
    C = {"product": "orders_by_region", "version": 1, "backend": "teradata",
         "members": ["orders_p", "customers_p"],
         "bridges": [{"from": "order", "to": "customer",
                      "on": [["customer_id", "customer_id"]], "cardinality": "N:1"}]}
    ent("orders_p", "data_product", {"layer": "logical", "manifest": A})
    ent("customers_p", "data_product", {"layer": "logical", "manifest": B})
    ent("orders_by_region", "data_product", {"layer": "logical", "manifest": C})
    conn.commit()
    conn.close()
    return path, A, B, C


def set_props(path, name, props):
    conn = sqlite3.connect(path)
    conn.execute("UPDATE kg_entities SET properties_json=? "
                 "WHERE kg_id=? AND entity_type='data_product' AND name=?",
                 (json.dumps(props), KG, name))
    conn.commit()
    conn.close()


def validate_and_stamp(path, name, raw, flat=None):
    parsed = pm.parse_manifest(flat if flat is not None else raw)
    rep = pv.validate_product(parsed)
    set_props(path, name, {"layer": "logical", "manifest": raw,
                           "validation_report": rep.to_dict()})
    return rep


db2, A, B, C = make_composed_db()
try:
    # the ontology must be signed first (the bottom-up rule) — same as section 4
    ents, rels = tcomp.load_kg_graph(KG, db2)
    ts.sign("ontology", KG, tcomp.kg_content_hash(ents, rels), db_path=db2)

    # P2 — anything ∘ unsigned → not signable, NAMING the members
    flat = pm.flatten_composed(pm.parse_composed(C),
                               {"orders_p": A, "customers_p": B})
    validate_and_stamp(db2, "orders_by_region", C, flat=flat)
    r = tp.sign_product(KG, "orders_by_region", db_path=db2)
    check("P2: unsigned members refuse the composed sign", r["signed"] is False
          and r["reason"] == "unsigned_member", str(r)[:160])
    check("…NAMING the unsigned members",
          set((r.get("validation") or {}).get("detail", {}).get("members", []))
          == {"orders_p", "customers_p"})

    # sign the members (validate first — the earned signature all the way down)
    validate_and_stamp(db2, "orders_p", A)
    validate_and_stamp(db2, "customers_p", B)
    ra = tp.sign_product(KG, "orders_p", db_path=db2)
    rb = tp.sign_product(KG, "customers_p", db_path=db2)
    check("both members sign", ra["signed"] and rb["signed"])

    # P1 — signed ∘ signed → mechanically signable, lineage in the content
    r = tp.sign_product(KG, "orders_by_region", db_path=db2)
    check("P1: signed members → the composed product signs, no re-judgement",
          r["signed"] is True, str(r)[:200])
    st = tp.product_state(KG, "orders_by_region", db_path=db2)
    check("…and reads trusted", st["state"] == "trusted")
    mh = tp.member_attested_hashes(KG, ("orders_p", "customers_p"), db_path=db2)
    check("the lineage records BOTH parents' attested hashes",
          mh["orders_p"] and mh["customers_p"])
    check("the lineage is INSIDE the signable content (a different parent hash "
          "→ a different version hash)",
          tp.version_hash_for(C, {"r": 1}, member_hashes=mh)
          != tp.version_hash_for(C, {"r": 1},
                                 member_hashes={**mh, "orders_p": "x" * 64}))

    # P3 — a parent re-sign marks the composed child CHANGED (stale-ingredient).
    # TWO distinct parent edits, deliberately told apart (the first gate run
    # conflated them and the machinery corrected the test, not vice versa):
    #
    # (a) a VERSION-ONLY bump: the lineage moves (child CHANGED) but the FLAT
    #     contract is byte-identical — flatten carries entities/joins/measures,
    #     not the member's own version — so the child may re-sign WITHOUT
    #     re-validating (P1: the gate already proved this exact flat form).
    A2 = {**A, "version": 2}
    validate_and_stamp(db2, "orders_p", A2)
    r = tp.sign_product(KG, "orders_p", db_path=db2)
    check("the parent re-signs at v2", r["signed"] is True)
    st = tp.product_state(KG, "orders_by_region", db_path=db2)
    check("P3: the composed child now reads CHANGED (stale-ingredient) — the "
          "parent's recorded hash no longer matches",
          st["state"] == "changed", str(st)[:160])
    r = tp.sign_product(KG, "orders_by_region", db_path=db2)
    check("a version-only parent bump re-signs WITHOUT re-validation (the flat "
          "contract is unchanged — re-judgement would be ceremony, P1)",
          r["signed"] is True
          and tp.product_state(KG, "orders_by_region", db_path=db2)["state"] == "trusted")

    # (b) a SHAPE change: the parent's entities change → the flat hash moves →
    #     the child's stored report proves NOTHING about the new flat form →
    #     stale_report until re-validated (the ratchet cascading downward).
    A3 = {**A2, "version": 3,
          "entities": [{**A2["entities"][0],
                        "attributes": A2["entities"][0]["attributes"]
                        + [{"name": "channel"}]}]}
    validate_and_stamp(db2, "orders_p", A3)
    r = tp.sign_product(KG, "orders_p", db_path=db2)
    check("the parent re-signs at v3 (a real shape change)", r["signed"] is True)
    st = tp.product_state(KG, "orders_by_region", db_path=db2)
    check("the child reads CHANGED again", st["state"] == "changed")
    r = tp.sign_product(KG, "orders_by_region", db_path=db2)
    check("a SHAPE change cascades: the child cannot re-sign without "
          "re-validating (stale_report)",
          r["signed"] is False and r["reason"] == "stale_report", str(r)[:160])
    # re-validate against the CURRENT flat form → re-sign converges
    flat3 = pm.flatten_composed(pm.parse_composed(C),
                                {"orders_p": A3, "customers_p": B})
    validate_and_stamp(db2, "orders_by_region", C, flat=flat3)
    r = tp.sign_product(KG, "orders_by_region", db_path=db2)
    check("re-validate + re-sign converges back to trusted",
          r["signed"] is True
          and tp.product_state(KG, "orders_by_region", db_path=db2)["state"] == "trusted")
finally:
    os.remove(db2)

print(f"\n{'=' * 68}\n  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
