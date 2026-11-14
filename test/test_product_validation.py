"""
CI gate — OBDA-READ sign-time validation gate V1–V6 (Phase A2).

Every V-rule of TIER_5.5_OBDA_SCOPE.md §5 driven through its PASS and its FAIL
shape, with the grain-safety matrix asserted against HAND-COMPUTED truth (the
canonical fan-out pair included), the determinism battery proven to actually compile
twice, structural-vs-schema honesty (S2) pinned, and the report proven byte-stable —
it is signable content, so a report that hashes differently across two runs would
poison the attestation.

Pure — resolver/schema/compile injected, no server, no store.
Run: python3 test/test_product_validation.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.dbdeploy import product_model as pm      # noqa: E402
from trusted_data_agent.dbdeploy import product_validation as pv  # noqa: E402

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


def status(report, cid):
    for c in report.checks:
        if c.id == cid:
            return c.status
    return "(absent)"


def mode(report, cid):
    for c in report.checks:
        if c.id == cid:
            return c.mode
    return "(absent)"


# ── fixtures ─────────────────────────────────────────────────────────────────
def multi():
    """order —N:1→ customer, order_item —N:1→ order (the scope's own shape)."""
    return pm.parse_manifest({
        "product": "customer_orders", "version": 1, "backend": "teradata",
        "entities": [
            {"name": "order", "deployed": "mvm.order_header", "key": ["order_id"],
             "attributes": [{"name": "order_date", "type": "date"},
                            {"name": "channel"}]},
            {"name": "customer", "deployed": "mvm.customer", "key": ["customer_id"],
             "attributes": [{"name": "region"}]},
            {"name": "order_item", "deployed": "mvm.order_item",
             "key": ["order_id", "line_no"],
             "attributes": [{"name": "line_no"}]},
        ],
        "joins": [
            {"from": "order", "to": "customer",
             "on": [["customer_id", "customer_id"]], "cardinality": "N:1"},
            {"from": "order_item", "to": "order",
             "on": [["order_id", "order_id"]], "cardinality": "N:1"},
        ],
        "measures": [
            {"name": "order_count", "anchor": "order", "expr": "COUNT(*)"},
            {"name": "revenue", "anchor": "order_item",
             "expr": "SUM(quantity * unit_price)"},
        ],
    })


def single():
    """The v0-equivalent single-entity shape — Phase A's signable form."""
    return pm.parse_manifest({
        "product": "orders_only", "version": 2, "backend": "teradata",
        "entities": [
            {"name": "order", "deployed": "mvm.order_header", "key": ["order_id"],
             "attributes": [{"name": "order_date", "type": "date"},
                            {"name": "channel"}]},
        ],
        "measures": [
            {"name": "order_count", "anchor": "order", "expr": "COUNT(*)"},
            {"name": "aov", "anchor": "order",
             "expr": "SUM(total_amount) / NULLIF(COUNT(*), 0)"},
        ],
    })


SCHEMA = {
    "mvm.order_header": {"order_id", "customer_id", "order_date", "channel",
                         "total_amount"},
    "mvm.customer": {"customer_id", "region"},
    "mvm.order_item": {"order_id", "line_no", "quantity", "unit_price"},
}
RESOLUTION = {"order": "mvm.order_header", "customer": "mvm.customer",
              "order_item": "mvm.order_item"}


print("=" * 68)
print("OBDA-READ validation gate V1–V6 — Phase A2 gate")
print("=" * 68)

# ── 1 · V1 resolution agreement ──────────────────────────────────────────────
print("\n[1] V1 — every entity resolves to exactly its frozen deployed table")
r = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA)
check("V1 passes when the resolver agrees with the frozen identifiers",
      status(r, "V1") == "pass" and mode(r, "V1") == "schema")

moved = dict(RESOLUTION, order="mvm.order_header_v2")
r = pv.validate_product(multi(), resolution=moved, schema_columns=SCHEMA)
check("V1 FAILS when a re-deploy moved a table (the ratchet, M1)",
      status(r, "V1") == "fail")
check("…and the product is not signable", r.signable is False)
check("…and the blocker names the drifted entity",
      any("order" in b and "re-validate" in b for b in r.blockers))

r = pv.validate_product(multi(), resolution=dict(RESOLUTION, order=None))
check("V1 FAILS when an entity does not resolve at all", status(r, "V1") == "fail")

r = pv.validate_product(multi(),
                        resolution=dict(RESOLUTION, order=["a.t1", "a.t2"]))
check("V1 FAILS on an ambiguous (2-table) resolution", status(r, "V1") == "fail")

r = pv.validate_product(multi(), resolution=None, schema_columns=SCHEMA)
check("V1 without a resolver runs in STRUCTURAL mode, honestly labelled (S2)",
      status(r, "V1") == "pass" and mode(r, "V1") == "structural")

# ── 2 · V2 join columns exist ────────────────────────────────────────────────
print("\n[2] V2 — join columns exist in the deployed schema")
r = pv.validate_product(multi(), schema_columns=SCHEMA)
check("V2 passes on the real columns", status(r, "V2") == "pass")

bad_schema = dict(SCHEMA)
bad_schema["mvm.customer"] = {"cust_id", "region"}   # customer_id gone
r = pv.validate_product(multi(), schema_columns=bad_schema)
check("V2 FAILS when a join column is absent from the deployed table",
      status(r, "V2") == "fail" and not r.signable)

r = pv.validate_product(multi(), schema_columns=None)
check("V2 without a schema catalog is structural, honestly labelled",
      status(r, "V2") == "pass" and mode(r, "V2") == "structural")

# ── 3 · V3 join graph: connected + unique tree ───────────────────────────────
print("\n[3] V3 — connected, and every restriction resolves to a unique tree")
r = pv.validate_product(multi())
check("V3 passes on the tree-shaped product", status(r, "V3") == "pass")

disconnected = pm.parse_manifest({
    "product": "island", "version": 1, "backend": "teradata",
    "entities": [
        {"name": "a", "deployed": "d.a", "key": ["id"],
         "attributes": [{"name": "x"}]},
        {"name": "b", "deployed": "d.b", "key": ["id"],
         "attributes": [{"name": "y"}]},
    ],
    "measures": [{"name": "n", "anchor": "a", "expr": "COUNT(*)"}],
})
r = pv.validate_product(disconnected)
check("V3 FAILS on a disconnected member graph",
      status(r, "V3") == "fail" and not r.signable)

cyclic = pm.parse_manifest({
    "product": "cycle", "version": 1, "backend": "teradata",
    "entities": [
        {"name": "a", "deployed": "d.a", "key": ["id"], "attributes": [{"name": "x"}]},
        {"name": "b", "deployed": "d.b", "key": ["id"], "attributes": [{"name": "y"}]},
        {"name": "c", "deployed": "d.c", "key": ["id"], "attributes": [{"name": "z"}]},
    ],
    "joins": [
        {"from": "a", "to": "b", "on": [["b_id", "id"]], "cardinality": "N:1"},
        {"from": "b", "to": "c", "on": [["c_id", "id"]], "cardinality": "N:1"},
        {"from": "a", "to": "c", "on": [["c_id", "id"]], "cardinality": "N:1"},
    ],
    "measures": [{"name": "n", "anchor": "a", "expr": "COUNT(*)"}],
})
r = pv.validate_product(cyclic)
check("V3 FAILS on a cycle (two distinct paths = a mis-declared product, J1)",
      status(r, "V3") == "fail" and not r.signable)

roled = pm.parse_manifest({
    "product": "roled", "version": 1, "backend": "teradata",
    "entities": [
        {"name": "order", "deployed": "d.o", "key": ["id"],
         "attributes": [{"name": "order_date"}]},
        {"name": "customer", "deployed": "d.c", "key": ["id"],
         "attributes": [{"name": "region"}]},
    ],
    "joins": [
        {"from": "order", "to": "customer", "on": [["cust_id", "id"]],
         "cardinality": "N:1", "role": "primary"},
        {"from": "order", "to": "customer", "on": [["billing_id", "id"]],
         "cardinality": "N:1", "role": "billing"},
    ],
    "measures": [{"name": "n", "anchor": "order", "expr": "COUNT(*)"}],
})
r = pv.validate_product(roled)
check("V3: parallel ROLE-qualified joins are alternatives, not a cycle (J1)",
      status(r, "V3") == "pass")

# ── 4 · V4 expressions transpile; measures stay on their anchor ──────────────
print("\n[4] V4 — expressions parse + transpile; anchor-column rule")
r = pv.validate_product(multi(), schema_columns=SCHEMA)
check("V4 passes on well-formed spark expressions", status(r, "V4") == "pass")

foreign = pm.parse_manifest({
    "product": "foreign_col", "version": 1, "backend": "teradata",
    "entities": [
        {"name": "order", "deployed": "mvm.order_header", "key": ["order_id"],
         "attributes": [{"name": "channel"}]},
        {"name": "customer", "deployed": "mvm.customer", "key": ["customer_id"],
         "attributes": [{"name": "region"}]},
    ],
    "joins": [{"from": "order", "to": "customer",
               "on": [["customer_id", "customer_id"]], "cardinality": "N:1"}],
    # references `region`, a CUSTOMER column — not the anchor's (M5)
    "measures": [{"name": "bad", "anchor": "order", "expr": "SUM(region)"}],
})
r = pv.validate_product(foreign, schema_columns=SCHEMA)
check("V4 FAILS a measure referencing a non-anchor column (M5)",
      status(r, "V4") == "fail" and not r.signable)
r = pv.validate_product(foreign, schema_columns=None)
check("…but without a schema catalog the same check is structural (unverifiable, honest)",
      status(r, "V4") == "pass" and mode(r, "V4") == "structural")

# ── 5 · V5 the grain-safety matrix (hand-computed truth) ─────────────────────
print("\n[5] V5 — the grain matrix, incl. the canonical fan-out pair")
r = pv.validate_product(multi())
gm = r.grain_matrix
# revenue anchors at order_item: order_item→order (N:1 safe) →customer (N:1 safe)
check("revenue × customer.region is SAFE (all hops N:1 away from the anchor)",
      gm["revenue"]["customer.region"] == "safe")
check("revenue × order.order_date is SAFE", gm["revenue"]["order.order_date"] == "safe")
# order_count anchors at order: order→order_item traverses order_item—N:1→order
# BACKWARDS = 1:N = the fan-out. THE canonical confidently-wrong-number pair.
check("order_count × order_item.line_no is REJECTED (the canonical fan-out)",
      gm["order_count"]["order_item.line_no"].startswith("rejected:"))
check("…and the rejection NAMES the offending 1:N hop (G2 — never a bare no)",
      "1:N" in gm["order_count"]["order_item.line_no"])
check("order_count × customer.region is SAFE (N:1 chain)",
      gm["order_count"]["customer.region"] == "safe")
check("a measure's own anchor attributes are safe (D == A)",
      gm["order_count"]["order.order_date"] == "safe")
check("V5 always passes and reports the exclusion count honestly (G3)",
      status(r, "V5") == "pass" and "excluded" in
      next(c.detail for c in r.checks if c.id == "V5"))

# ── 6 · V6 the determinism battery ───────────────────────────────────────────
print("\n[6] V6 — the battery compiles twice, byte-identical, or the product is unsignable")
calls = {"n": 0}
def counting_compile(mf, req):
    calls["n"] += 1
    return pv.single_entity_compile_fn(mf, req)

r = pv.validate_product(single(), compile_fn=counting_compile)
check("single-entity product: battery runs and V6 passes", status(r, "V6") == "pass")
check("…the product is SIGNABLE", r.signable is True and r.blockers == ())
check("…a battery hash was recorded", len(r.battery_hash) == 64)
n_battery = len(pv._battery_requests(single()))
check("…every battery request was compiled TWICE (byte-equality is measured, "
      "not assumed)", calls["n"] == 2 * n_battery, f"calls={calls['n']}")

# Phase B2: the DEFAULT battery runs on the fan-out-safe product compiler, so a
# multi-entity product is now genuinely verified — and therefore SIGNABLE. (Under
# Phase A this asserted skip+unsignable; the restriction was capability, not
# policy, and it lifted when compiler and battery landed together — the settled
# option (a) sequencing.)
r = pv.validate_product(multi())
check("multi-entity product: the battery RUNS on the product compiler (V6 pass)",
      status(r, "V6") == "pass")
check("…and the product is SIGNABLE (the Phase-B widening)",
      r.signable is True and len(r.battery_hash) == 64, str(r.blockers))
bat = pv._battery_requests(multi())
check("the battery is built FROM the admissible map: per-measure + the "
      "multi-anchor common set + grand total + entity reads",
      len(bat) == 7, f"{len(bat)}: {bat}")
common = next(b for b in bat if b.get("measures") == ["order_count", "revenue"]
              and b.get("dimensions"))
check("…the multi-anchor request carries the COMMON safe dims only "
      "(order_item.line_no is excluded — it fans out for order_count)",
      common["dimensions"] == ["customer.region", "order.channel",
                               "order.order_date"], str(common))

readonly = pm.parse_manifest({
    "product": "read_only", "version": 1, "backend": "teradata",
    "entities": [{"name": "customer", "deployed": "mvm.customer",
                  "key": ["customer_id"], "attributes": [{"name": "region"}]}],
})
r = pv.validate_product(readonly)
check("an attributes-only (entity-read) product is now verifiable + signable",
      status(r, "V6") == "pass" and r.signable is True, str(r.blockers))

r = pv.validate_product(multi(), compile_fn=lambda m, q: None)
check("a capability that returns None still SKIPS honestly (not signable)",
      status(r, "V6") == "skip" and not r.signable)

def rejecting_compile(mf, q):
    from trusted_data_agent.dbdeploy.product_compiler import ProductRequestError
    raise ProductRequestError("grain_conflict", "synthetic divergence")
r = pv.validate_product(multi(), compile_fn=rejecting_compile)
check("a REJECT from inside the battery is a V6 FAIL naming the divergence "
      "(the map and the compiler disagreed) — never a skip",
      status(r, "V6") == "fail" and not r.signable
      and "diverged" in next(c.detail for c in r.checks if c.id == "V6"))

flip = {"n": 0}
def unstable_compile(mf, req):
    flip["n"] += 1
    return f"SELECT {flip['n']}"          # different every call — not deterministic
r = pv.validate_product(single(), compile_fn=unstable_compile)
check("a NON-deterministic compile fails V6 (the defining move — signed means "
      "deterministic)", status(r, "V6") == "fail" and not r.signable)

r = pv.validate_product(single(), compile_fn=None)
check("no compile capability at all → not signable (S1: V6 is required)",
      not r.signable)

# ── 7 · the report is signable content: byte-stable, no environment ──────────
print("\n[7] the report hashes byte-stable across independent runs")
r1 = pv.validate_product(single(), resolution={"order": "mvm.order_header"},
                         schema_columns=SCHEMA)
r2 = pv.validate_product(single(), resolution={"order": "mvm.order_header"},
                         schema_columns=SCHEMA)
check("two independent runs hash identically (no timestamps, no randomness)",
      pv.report_hash(r1) == pv.report_hash(r2))
check("the report embeds the manifest hash it validated",
      r1.manifest_hash == pm.manifest_hash(single()))
r3 = pv.validate_product(single(), resolution=None, schema_columns=SCHEMA)
check("a different evidence mode changes the report hash (structural vs schema "
      "is part of what is attested — S2)",
      pv.report_hash(r3) != pv.report_hash(r1))



# ── V7 · live probes ────────────────────────────────────────────────────────
print("\n[V7] live probes — empirical verification of the declarations")

class FakeDB:
    """A recording, scriptable read-only executor. `dups` maps a substring of the
    probe SQL to a dup_groups count; unmatched zero-probes return 0; battery
    probes return one row. `broken` substrings raise."""
    def __init__(self, dups=None, broken=()):
        self.dups = dups or {}
        self.broken = tuple(broken)
        self.executed = []
    def __call__(self, sql):
        self.executed.append(sql)
        for b in self.broken:
            if b in sql:
                raise RuntimeError("simulated execution failure")
        if "dup_groups" in sql:
            for frag, n in self.dups.items():
                if frag in sql:
                    return [{"dup_groups": n}]
            return [{"dup_groups": 0}]
        return [{"c": 1}]

# The probe plan is deterministic and shaped as declared.
plan = pv.build_probe_plan(multi())
kinds = {}
for pr in plan:
    kinds[pr["kind"]] = kinds.get(pr["kind"], 0) + 1
check("plan covers all three probe kinds",
      set(kinds) == {"battery_syntax", "join_cardinality", "entity_key"}, str(kinds))
check("one cardinality probe per N:1 (the one-side)", kinds["join_cardinality"] == 2)
check("one key probe per entity", kinds["entity_key"] == 3)
bat = [pr for pr in plan if pr["kind"] == "battery_syntax"]
check("battery probes execute with limit 1 — TOP form on Teradata",
      bat and all(prb["sql"].startswith("SELECT TOP 1") for prb in bat),
      (bat[0]["sql"][:60] if bat else "none"))
comp = [pr for pr in plan if pr["id"] == "key[order_item]"]
check("a composite key probes ALL its columns (the COUNT(DISTINCT) form cannot)",
      comp and "order_id, line_no" in comp[0]["sql"], str(comp[:1]))
check("the plan is deterministic", plan == pv.build_probe_plan(multi()))

# Clean data → V7 passes, report mode "live".
db = FakeDB()
r = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA,
                        probe_fn=db)
check("clean probes → V7 pass", status(r, "V7") == "pass")
check("V7 evidence mode is 'live'", mode(r, "V7") == "live")
check("the report mode says live", r.mode == "live")
check("still signable", r.signable is True)
check("probes actually EXECUTED (not vacuous)", len(db.executed) >= 6, str(len(db.executed)))

# A contradicted N:1 → V7 fails NAMING the declaration → not signable.
db_bad = FakeDB(dups={"mvm.customer": 4})
r = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA,
                        probe_fn=db_bad)
check("contradicted cardinality → V7 fail", status(r, "V7") == "fail")
check("…and the product is NOT signable", r.signable is False)
_v7 = next(c for c in r.checks if c.id == "V7")
check("…the failure names the DECLARATION the data contradicts",
      "CONTRADICTS" in _v7.detail and "customer" in _v7.detail, _v7.detail[:160])
check("…and the blocker carries it", any("V7" in b for b in r.blockers))

# A probe that cannot execute → V7 fails (live was attempted; signing a
# half-probed report as 'live' would be dishonest — re-validate without a
# connection to sign structurally, an explicit choice).
db_err = FakeDB(broken=("mvm.order_item",))
r = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA,
                        probe_fn=db_err)
check("an erroring probe → V7 fail, named", status(r, "V7") == "fail")
check("…naming could-not-execute, not a contradiction",
      "could not execute" in next(c for c in r.checks if c.id == "V7").detail)

# No connection → V7 skip, mode structural, signable UNCHANGED (S2).
r = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA)
check("no connection → V7 skip (never blocks)", status(r, "V7") == "skip")
check("…and the report mode says structural, honestly", r.mode == "structural")
check("…and signability is unchanged", r.signable is True)

# The mode is attested content: live vs structural changes the report hash.
r_live = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA,
                             probe_fn=FakeDB())
r_struct = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA)
check("live vs structural is part of what is attested (different report hash)",
      pv.report_hash(r_live) != pv.report_hash(r_struct))
check("two live runs on the same data hash identically",
      pv.report_hash(r_live) == pv.report_hash(
          pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA,
                              probe_fn=FakeDB())))

# An unreadable probe result is a NAMED failure, never a silent pass.
class WeirdDB(FakeDB):
    def __call__(self, sql):
        self.executed.append(sql)
        if "dup_groups" in sql:
            return [{"dup_groups": "not-a-number"}]
        return [{"c": 1}]
r = pv.validate_product(multi(), resolution=RESOLUTION, schema_columns=SCHEMA,
                        probe_fn=WeirdDB())
check("an unreadable probe result fails by name",
      status(r, "V7") == "fail"
      and "unreadable" in next(c for c in r.checks if c.id == "V7").detail)

# A hostile identifier never reaches the executor — the probe is REFUSED.
import copy as _copy
hostile = pm.parse_manifest({
    "product": "hostile", "version": 1, "backend": "teradata",
    "entities": [{"name": "e", "deployed": "db.t; DROP TABLE x", "key": ["k"],
                  "attributes": [{"name": "k"}]}],
    "measures": [{"name": "n", "anchor": "e", "expr": "COUNT(*)"}]})
db_h = FakeDB()
okh, dh, _o = pv.run_live_probes(hostile, db_h)
# Two safety layers, asserted apart: the COMPILER path (battery) quotes and
# double-escapes identifiers, so the hostile string stays inert inside a quoted
# identifier; the hand-built dup-groups probes use BARE identifiers and must
# therefore REFUSE the shape instead of executing it.
check("hostile identifier → V7 fails (the refusal fired)", okh is False, dh[:120])
check("no BARE-identifier probe ever carried the hostile string",
      not any("dup_groups" in q and "DROP TABLE" in q for q in db_h.executed))
check("the compiler-path probes that did run kept it inside a QUOTED identifier",
      all('"' in q for q in db_h.executed if "DROP TABLE" in q))


print(f"\n{'=' * 68}\n  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
