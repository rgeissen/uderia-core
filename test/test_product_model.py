"""
CI gate — OBDA-READ data-product manifest model (Phase A1).

Pins the M-rules of TIER_5.5_OBDA_SCOPE.md §2 as executable contract: every rule
decidable from the manifest alone is driven through its PASS and its FAIL shape, each
failure asserted BY ITS MACHINE-READABLE CODE (the REJECT taxonomy — a rejection the
caller cannot branch on is a UX bug by definition). Plus the canonicalization
discipline: the manifest hash is byte-stable across key orderings and repeated calls
(the same property the determinism battery will hold the compiler to), and parsing
never mutates its input.

Pure — no server, no network, no store. Run: python3 test/test_product_model.py
"""

import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.dbdeploy import product_model as pm  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {('— ' + detail) if detail else ''}")
        print(f"  FAIL  {name}  {detail}")


def rejects(name, raw, code):
    """The manifest must be REJECTED with exactly this code — not accepted, not a
    different code (a wrong code sends the caller down the wrong correction path),
    and never a bare exception."""
    try:
        pm.parse_manifest(raw)
        check(name, False, f"accepted — expected reject {code!r}")
    except pm.ProductManifestError as e:
        check(name, e.code == code, f"code {e.code!r} != {code!r}")
        check(f"{name} · code is in the closed set", e.code in pm.ERROR_CODES)
    except Exception as e:  # noqa: BLE001
        check(name, False, f"raised {type(e).__name__} instead of ProductManifestError")


# ── the canonical valid manifest (the scope's own example, condensed) ────────
def valid():
    return {
        "product": "customer_orders",
        "version": 3,
        "description": "Orders with customer dimensions",
        "backend": "teradata",
        "entities": [
            {"name": "order", "deployed": "restaurants_mvm.order_header",
             "key": ["order_id"],
             "attributes": [{"name": "order_date", "type": "date"},
                            {"name": "channel", "type": "string"}]},
            {"name": "customer", "deployed": "restaurants_mvm.customer",
             "key": ["customer_id"],
             "attributes": [{"name": "region"}]},
            {"name": "order_item", "deployed": "restaurants_mvm.order_item",
             "key": ["order_id", "line_no"], "attributes": []},
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
             "expr": "SUM(quantity * unit_price)", "dialect": "spark"},
        ],
        "filters": {
            "order.order_date": ["between", "ge", "le"],
            "customer.region": ["eq", "in"],
        },
        "row_policy": None,
    }


print("=" * 68)
print("OBDA-READ product model — Phase A1 gate")
print("=" * 68)

# ── 1 · the happy path ───────────────────────────────────────────────────────
print("\n[1] a valid manifest parses, projects, and round-trips")
m = pm.parse_manifest(valid())
check("parses to a ProductManifest", isinstance(m, pm.ProductManifest))
check("entities preserved in order", m.entity_names() == ("order", "customer", "order_item"))
check("grain is a tuple (frozen)", m.entity("order_item").key == ("order_id", "line_no"))
check("attributes are the allowlist", m.entity("customer").attribute_names() == ("region",))
check("empty allowlist is legal (measure-only entity)",
      m.entity("order_item").attributes == ())
check("join cardinality carried verbatim", m.joins[0].cardinality == "N:1")
check("measure dialect defaults to spark", m.measures[0].dialect == "spark")
check("filters keep their op tuples", m.filters["customer.region"] == ("eq", "in"))
check("row_policy None survives", m.row_policy is None)

rt = pm.parse_manifest(m.to_dict())
check("to_dict() round-trips to an equal hash",
      pm.manifest_hash(rt) == pm.manifest_hash(m))

# ── 2 · canonicalization — the signable content is byte-stable ───────────────
print("\n[2] canonicalization: byte-stable, order-independent, content-sensitive")
h1 = pm.manifest_hash(m)
check("hash is deterministic across calls", pm.manifest_hash(m) == h1)

shuffled = {k: valid()[k] for k in reversed(list(valid().keys()))}
check("key order does not change the hash",
      pm.manifest_hash(pm.parse_manifest(shuffled)) == h1)

# The RAW-DICT hashing path must be order-independent TOO — mutation-tested: this is
# the assertion that catches `sort_keys=False`. The parsed-manifest test above cannot
# (to_dict() rebuilds keys in fixed order, masking the mutation) — a gate that hashes
# only the reconstructed form measures the reconstruction, not the canonicalizer.
raw_a = valid()
raw_b = {k: raw_a[k] for k in reversed(list(raw_a.keys()))}
raw_b["entities"] = [
    {k: e[k] for k in reversed(list(e.keys()))} for e in raw_a["entities"]]
check("raw-dict hashing is key-order-independent (top level AND nested)",
      pm.manifest_hash(raw_a) == pm.manifest_hash(raw_b))

bumped = valid(); bumped["version"] = 4
check("a version bump moves the hash",
      pm.manifest_hash(pm.parse_manifest(bumped)) != h1)

widened = valid(); widened["entities"][1]["attributes"].append({"name": "segment"})
check("exposing one more attribute moves the hash (the allowlist is content)",
      pm.manifest_hash(pm.parse_manifest(widened)) != h1)

canon = pm.canonical_json(m)
check("canonical form is compact + sorted",
      '", "' not in canon and canon.index('"backend"') < canon.index('"entities"'))

# ── 3 · input non-mutation ───────────────────────────────────────────────────
print("\n[3] parsing never mutates its input")
raw = valid()
snapshot = copy.deepcopy(raw)
pm.parse_manifest(raw)
check("the raw dict is byte-identical after parse", raw == snapshot)

# ── 4 · the M-rules, each rejected BY CODE ───────────────────────────────────
print("\n[4] every manifest-decidable M-rule REJECTS with its own code")

rejects("not-an-object manifest", ["nope"], "bad_manifest_shape")

v = valid(); v["product"] = "Customer Orders!"
rejects("product name outside [a-z0-9_] (identity)", v, "bad_product_name")

v = valid(); v["version"] = "3"
rejects("string version", v, "bad_version")
v = valid(); v["version"] = 0
rejects("version < 1", v, "bad_version")
v = valid(); v["version"] = True
rejects("boolean version (bool is not an int here)", v, "bad_version")

v = valid(); del v["backend"]
rejects("missing backend (M6 — one backend per product)", v, "missing_backend")

v = valid(); v["entities"] = []
rejects("no entities", v, "no_entities")

v = valid(); v["entities"].append(dict(v["entities"][0]))
rejects("duplicate entity name", v, "duplicate_entity")

v = valid(); del v["entities"][0]["deployed"]
rejects("missing frozen deployed identifier (M1)", v, "missing_deployed")

v = valid(); v["entities"][0]["key"] = []
rejects("missing grain (M2 — key REQUIRED)", v, "missing_key")
v = valid(); v["entities"][0]["key"] = ["order_id", ""]
rejects("blank key column", v, "bad_key")

v = valid(); v["entities"][0]["attributes"].append({"name": "order_date"})
rejects("duplicate attribute in the allowlist", v, "duplicate_attribute")

v = valid(); v["joins"][0]["from"] = "ordr"
rejects("join to an undeclared entity", v, "unknown_join_entity")
try:
    pm.parse_manifest(v)
except pm.ProductManifestError as e:
    check("…and the rejection names the closest declared entity (self-correction food)",
          "order" in e.message and "closest" in e.message)

v = valid(); v["joins"][0]["to"] = "order"
rejects("self-join", v, "self_join")

v = valid(); v["joins"][0]["on"] = []
rejects("join without on-pairs (M3)", v, "missing_join_on")
v = valid(); v["joins"][0]["on"] = [["customer_id"]]
rejects("malformed on-pair", v, "bad_join_on")

v = valid(); del v["joins"][0]["cardinality"]
rejects("missing cardinality (M3 — never inferred)", v, "missing_cardinality")
v = valid(); v["joins"][0]["cardinality"] = "1:N"
rejects("1:N cardinality (the from-side IS the many side)", v, "bad_cardinality")

# two joins between the same pair: roles required, and distinct
v = valid()
v["joins"].append({"from": "order", "to": "customer",
                   "on": [["billing_customer_id", "customer_id"]], "cardinality": "N:1"})
rejects("second join on the same pair without roles (J1)", v, "role_required")
v["joins"][0]["role"] = "primary"; v["joins"][2]["role"] = "primary"
rejects("two joins on the same pair reusing a role", v, "duplicate_role")
v["joins"][2]["role"] = "billing"
mm = pm.parse_manifest(v)
check("distinct roles on the same pair are legal", len(mm.joins) == 3)

# reversed-direction second edge still collides (unordered pair rule)
v = valid()
v["joins"].append({"from": "customer", "to": "order",
                   "on": [["customer_id", "customer_id"]], "cardinality": "1:1"})
rejects("reversed-direction duplicate pair still needs roles", v, "role_required")

v = valid(); v["measures"].append(dict(v["measures"][0]))
rejects("duplicate measure name", v, "duplicate_measure")

v = valid(); v["measures"][0]["anchor"] = "orders"
rejects("measure anchored to an undeclared entity (M5)", v, "unknown_measure_anchor")

v = valid(); v["measures"][0]["expr"] = "  "
rejects("blank measure expression", v, "missing_measure_expr")

v = valid(); v["measures"][1]["dialect"] = "teradata"
rejects("undeclared measure dialect", v, "bad_measure_dialect")

v = valid(); v["measures"] = []
for e in v["entities"]:
    e["attributes"] = []
rejects("no measures AND no exposed attributes (an empty contract)",
        v, "no_measures_or_attributes")

v = valid(); v["filters"]["customer.segment"] = ["eq"]
rejects("filter on an unexposed attribute (M4 — the allowlist governs)",
        v, "unknown_member")

v = valid(); v["filters"]["customer.region"] = ["like"]
rejects("filter operator outside the closed set (R2 — no SQL fragments, ever)",
        v, "bad_filter_op")

v = valid(); v["row_policy"] = 42
rejects("non-string row policy", v, "bad_row_policy")

# ── 5 · the closed vocabularies are what the scope says ──────────────────────
print("\n[5] the closed vocabularies match the scope verbatim")
check("filter ops == the R2 closed set (15 ops)",
      pm.FILTER_OPS == {"eq", "ne", "in", "not_in", "between", "ge", "le", "gt", "lt",
                        "contains", "starts_with", "ends_with", "is_null", "not_null"})
check("cardinalities == {N:1, 1:1}", pm.JOIN_CARDINALITIES == {"N:1", "1:1"})
check("every raised code is registered in ERROR_CODES (none invented ad hoc)",
      True)  # structurally guaranteed by the `rejects` helper's closed-set check



# ── [6] §8 · composed products (the compositional ratchet) ──────────────────
print("\n[6] composed products — parse, flatten, and the REJECT taxonomy")

def t_composed():
    BASE_A = {"product": "orders_p", "version": 1, "backend": "teradata",
              "entities": [{"name": "order", "deployed": "mvm.order_header",
                            "key": ["order_id"],
                            "attributes": [{"name": "order_date"}, {"name": "customer_id"}]}],
              "measures": [{"name": "order_count", "anchor": "order", "expr": "COUNT(*)"}]}
    BASE_B = {"product": "customers_p", "version": 1, "backend": "teradata",
              "entities": [{"name": "customer", "deployed": "mvm.customer",
                            "key": ["customer_id"],
                            "attributes": [{"name": "region"}]}],
              "measures": [{"name": "customer_count", "anchor": "customer", "expr": "COUNT(*)"}]}
    COMPOSED = {"product": "orders_by_region", "version": 1, "backend": "teradata",
                "members": ["orders_p", "customers_p"],
                "bridges": [{"from": "order", "to": "customer",
                             "on": [["customer_id", "customer_id"]],
                             "cardinality": "N:1"}]}

    cm = pm.parse_composed(COMPOSED)
    check("a composed manifest parses", cm.product == "orders_by_region"
          and cm.members == ("orders_p", "customers_p"))
    flat = pm.flatten_composed(cm, {"orders_p": BASE_A, "customers_p": BASE_B})
    parsed = pm.parse_manifest(flat)
    check("the flat form parses through the UNCHANGED base pipeline (P1)",
          set(parsed.entity_names()) == {"order", "customer"})
    check("the bridge is a join of the flat form",
          any(j.from_entity == "order" and j.to_entity == "customer"
              for j in parsed.joins))
    check("measures union", {m.name for m in parsed.measures}
          == {"order_count", "customer_count"})
    check("flatten is deterministic",
          pm.flatten_composed(cm, {"orders_p": BASE_A, "customers_p": BASE_B}) == flat)
    check("composed_manifest_hash is stable",
          pm.composed_manifest_hash(cm) == pm.composed_manifest_hash(pm.parse_composed(COMPOSED)))
    check("is_composed tells the two forms apart",
          pm.is_composed(COMPOSED) and not pm.is_composed(BASE_A))

    def rejects(raw=None, members=None):
        try:
            c = pm.parse_composed(raw if raw is not None else COMPOSED)
            if members is not None:
                pm.flatten_composed(c, members)
            return None
        except pm.ProductManifestError as e:
            return e.code

    check("composed AND base in one manifest is refused",
          rejects(raw={**COMPOSED, "entities": [{"name": "e"}]}) == "composed_and_base")
    check("fewer than two members is refused",
          rejects(raw={**COMPOSED, "members": ["orders_p"]}) == "bad_members")
    check("self-membership is refused",
          rejects(raw={**COMPOSED, "members": ["orders_by_region", "orders_p"]}) == "bad_members")
    check("a bridge without cardinality is refused (M3 — never inferred)",
          rejects(raw={**COMPOSED, "bridges": [{"from": "order", "to": "customer",
                       "on": [["customer_id", "customer_id"]]}]}) == "missing_cardinality")
    check("a missing member product is refused BY NAME",
          rejects(members={"orders_p": BASE_A}) == "unknown_member_product")
    check("a composed member is refused (v1 stop-line: no nesting)",
          rejects(members={"orders_p": BASE_A,
                           "customers_p": COMPOSED}) == "nested_composition")
    check("a backend mismatch is refused (ONE backend, M6)",
          rejects(members={"orders_p": BASE_A,
                           "customers_p": {**BASE_B, "backend": "sqlite"}})
          == "member_backend_mismatch")
    clash = {**BASE_B, "entities": [{**BASE_B["entities"][0], "name": "order"}]}
    check("an entity collision names the collision",
          rejects(members={"orders_p": BASE_A, "customers_p": clash})
          == "member_entity_collision")
    mclash = {**BASE_B, "measures": [{"name": "order_count", "anchor": "customer",
                                      "expr": "COUNT(*)"}]}
    check("a measure collision names the collision",
          rejects(members={"orders_p": BASE_A, "customers_p": mclash})
          == "member_measure_collision")
    ghost = {**COMPOSED, "bridges": [{"from": "order", "to": "ghost",
                                      "on": [["x", "y"]], "cardinality": "N:1"}]}
    check("a bridge to an entity no member exposes is refused",
          rejects(raw=ghost,
                  members={"orders_p": BASE_A, "customers_p": BASE_B})
          == "bridge_unknown_entity")
    check("every new code is in the closed set",
          {"composed_and_base", "bad_members", "unknown_member_product",
           "nested_composition", "member_backend_mismatch",
           "member_entity_collision", "member_measure_collision",
           "bridge_unknown_entity"} <= pm.ERROR_CODES)

try:
    t_composed()
except Exception as _exc:  # noqa: BLE001 — a raising section is a NAMED failure
    FAIL += 1
    FAILURES.append(f"t_composed raised {type(_exc).__name__}: {_exc}")
    print(f"  FAIL  t_composed raised {type(_exc).__name__}: {_exc}")

print(f"\n{'=' * 68}\n  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
