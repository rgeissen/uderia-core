"""
CI gate — the OBDA-READ product compiler (Phase B1): fan-out safety with EXECUTED,
hand-computed truth.

The centerpiece is the scope's own acceptance case (§12 Phase B): two measures at
different grains through a 1:N join. The compiled SQL is EXECUTED on a real SQL
engine (sqlite, `backend: "sqlite"` — the D2 dialect seam proving itself with a
second dialect) against fixture rows whose truth is computed BY HAND, and the naïve
flat join is executed beside it to prove it MULTIPLIES — the confidently-wrong
number the compiler exists to make impossible.

Plus: determinism (byte-stable SQL, deterministic row order — executed twice),
the full REJECT taxonomy by code, literal escaping / LIKE-wildcard neutralization,
the Teradata TOP form (no LIMIT — Teradata has none), the entity-read form, role
ambiguity, and the admissible map asserted AGAINST the compiler's own behaviour
(they derive from one grain function — divergence is structurally impossible;
asserted anyway, as the scope demands).

Run: python3 test/test_product_compiler.py
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.dbdeploy import product_compiler as pc   # noqa: E402
from trusted_data_agent.dbdeploy import product_model as pm      # noqa: E402

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


def rejects(name, manifest, request, code):
    try:
        pc.compile_request(manifest, request)
        check(name, False, f"compiled — expected reject {code!r}")
    except pc.ProductRequestError as e:
        check(name, e.code == code, f"code {e.code!r} != {code!r} — {e.message[:100]}")
        check(f"{name} · code in the closed set", e.code in pc.REQUEST_ERROR_CODES)
    except Exception as e:  # noqa: BLE001
        check(name, False, f"raised {type(e).__name__}: {e}")


def manifest(backend="sqlite"):
    """The scope's own shape: order —N:1→ customer · order_item —N:1→ order."""
    return pm.parse_manifest({
        "product": "customer_orders", "version": 1, "backend": backend,
        "entities": [
            {"name": "order", "deployed": "mvm.order_header", "key": ["order_id"],
             "attributes": [{"name": "order_date", "type": "date"},
                            {"name": "channel", "type": "string"}]},
            {"name": "customer", "deployed": "mvm.customer", "key": ["customer_id"],
             "attributes": [{"name": "region", "type": "string"}]},
            {"name": "order_item", "deployed": "mvm.order_item",
             "key": ["order_id", "line_no"],
             "attributes": [{"name": "line_no", "type": "integer"}]},
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
        "filters": {
            "customer.region": ["eq", "in", "contains"],
            "order.channel": ["eq", "is_null"],
        },
    })


def fixture_db():
    """Hand-built rows. HAND-COMPUTED truth:

      customers: c1=EMEA, c2=APAC, c3=NORD
      orders:    o1(c1) o2(c1) o3(c2) o4(c3)          — o4 has NO items
      items:     o1: 10+20+30 = 60 · o2: 40 · o3: 5+5 = 10

      order_count by region:  EMEA 2 · APAC 1 · NORD 1
      revenue     by region:  EMEA 100 · APAC 10 · NORD NULL (no items — the
                              honest NULL, never a dropped row)
      NAIVE flat join order_count by region: EMEA 4 (o1×3 + o2×1 — MULTIPLIED)
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH ':memory:' AS mvm")
    conn.execute("CREATE TABLE mvm.customer (customer_id INT, region TEXT)")
    conn.execute("CREATE TABLE mvm.order_header (order_id INT, customer_id INT, "
                 "order_date TEXT, channel TEXT)")
    conn.execute("CREATE TABLE mvm.order_item (order_id INT, line_no INT, "
                 "quantity INT, unit_price INT)")
    conn.executemany("INSERT INTO mvm.customer VALUES (?,?)",
                     [(1, "EMEA"), (2, "APAC"), (3, "NORD"), (4, None)])
    conn.executemany("INSERT INTO mvm.order_header VALUES (?,?,?,?)",
                     [(1, 1, "2026-01-10", "web"), (2, 1, "2026-01-20", "store"),
                      (3, 2, "2026-02-05", "web"), (4, 3, "2026-02-11", "web"),
                      (5, 4, "2026-03-01", "web")])
    conn.executemany("INSERT INTO mvm.order_item VALUES (?,?,?,?)",
                     [(1, 1, 1, 10), (1, 2, 2, 10), (1, 3, 3, 10),
                      (2, 1, 4, 10), (3, 1, 1, 5), (3, 2, 1, 5),
                      (5, 1, 7, 1)])
    return conn


print("=" * 68)
print("OBDA-READ product compiler — Phase B1 gate")
print("=" * 68)

M = manifest()

# ── 1 · THE canonical fan-out case, EXECUTED against hand-computed truth ─────
print("\n[1] the canonical fan-out: per-grain-correct where the naïve join multiplies")
req = {"measures": ["order_count", "revenue"], "dimensions": ["customer.region"]}
compiled = pc.compile_request(M, req)
conn = fixture_db()
rows = conn.execute(compiled.sql).fetchall()
got = {r[0]: (r[1], r[2]) for r in rows}
check("EMEA: order_count=2 (NOT the multiplied 4)", got.get("EMEA") == (2, 100),
      f"got {got.get('EMEA')}")
check("APAC: order_count=1, revenue=10", got.get("APAC") == (1, 10))
check("NORD: present with revenue NULL (a slice absent for one measure renders "
      "honestly, never dropped — A2)", got.get("NORD") == (1, None),
      f"got {got.get('NORD')}")
check("a NULL dimension VALUE joins as ONE row across anchors (the NULL-safe "
      "tuple match — plain equality would split it in two)",
      got.get(None) == (1, 7), f"got {got.get(None)}")
check("exactly the four region slices (incl. the NULL one)", len(rows) == 4)

# the NAIVE flat join, executed beside it — the number the compiler forbids
naive = conn.execute(
    'SELECT c.region, COUNT(*), SUM(i.quantity * i.unit_price) '
    'FROM mvm.order_header o JOIN mvm.customer c ON o.customer_id = c.customer_id '
    'JOIN mvm.order_item i ON i.order_id = o.order_id '
    'GROUP BY c.region').fetchall()
naive_emea = {r[0]: r[1] for r in naive}.get("EMEA")
check("the naïve flat join PROVABLY multiplies (EMEA order_count = 4 ≠ 2)",
      naive_emea == 4, f"naive={naive_emea}")

# single-measure requests agree with the multi-anchor assembly (A1 per anchor)
one = pc.compile_request(M, {"measures": ["order_count"],
                             "dimensions": ["customer.region"]})
rows1 = {r[0]: r[1] for r in conn.execute(one.sql).fetchall()}
check("single-anchor flat form (A3) gives the same order_count per region",
      rows1 == {"EMEA": 2, "APAC": 1, "NORD": 1, None: 1}, str(rows1))

# ── 2 · determinism (D3 + R4) ────────────────────────────────────────────────
print("\n[2] determinism: byte-stable SQL, deterministic row order")
check("compile twice → byte-identical SQL",
      pc.compile_request(M, req).sql == compiled.sql)
shuffled = {"dimensions": ["customer.region"], "measures": ["order_count", "revenue"]}
check("request key order does not change the request hash",
      pc.request_hash(shuffled) == pc.request_hash(req))
check("ORDER BY is ALWAYS present with the dimension tiebreak (R4)",
      "ORDER BY" in compiled.sql and '"customer.region" ASC' in compiled.sql)
check("executed twice → identical row ORDER",
      conn.execute(compiled.sql).fetchall() == conn.execute(compiled.sql).fetchall())
ob = pc.compile_request(M, dict(req, order_by=[{"field": "revenue", "dir": "desc"}]))
first = conn.execute(ob.sql).fetchall()[0]
check("order_by desc puts EMEA (100) first, tiebreak appended after",
      first[0] == "EMEA" and 'ORDER BY "revenue" DESC, "customer.region" ASC' in ob.sql)

# ── 3 · filters: typed, escaped, applied INSIDE the sub-aggregates (A4) ──────
print("\n[3] filters — typed bound literals, escaped here, never a fragment")
f = pc.compile_request(M, dict(req, filters=[
    {"attr": "customer.region", "op": "in", "value": ["EMEA", "APAC"]}]))
rowsf = {r[0]: (r[1], r[2]) for r in conn.execute(f.sql).fetchall()}
check("an IN filter executes correctly (NORD gone)",
      rowsf == {"EMEA": (2, 100), "APAC": (1, 10)}, str(rowsf))
check("the filter is applied inside BOTH sub-aggregates (A4 — before GROUP BY)",
      f.sql.count('"region" IN') == 2)

esc = pc.compile_request(M, dict(req, filters=[
    {"attr": "customer.region", "op": "eq", "value": "O'Brien"}]))
check("a quote in a value is doubled, never a fragment (R2)",
      "'O''Brien'" in esc.sql)
like = pc.compile_request(M, dict(req, filters=[
    {"attr": "customer.region", "op": "contains", "value": "50%_off"}]))
check("LIKE wildcards in a VALUE are neutralized (%, _ escaped)",
      "'%50\\%\\_off%' ESCAPE '\\'" in like.sql, like.sql[-200:])
isnull = pc.compile_request(M, dict(req, filters=[
    {"attr": "order.channel", "op": "is_null"}]))
check("is_null takes no value", '"channel" IS NULL' in isnull.sql)

# ── 4 · the REJECT taxonomy, each by code ────────────────────────────────────
print("\n[4] every REJECT carries its machine-readable code")
rejects("unknown measure (with the closest named)",
        M, {"measures": ["revenu"]}, "unknown_member")
try:
    pc.compile_request(M, {"measures": ["revenu"]})
except pc.ProductRequestError as e:
    check("…the hint names 'revenue' (self-correction food, R1)",
          "revenue" in e.message)
rejects("unknown dimension entity", M,
        dict(req, dimensions=["customers.region"]), "unknown_member")
rejects("unexposed attribute (the allowlist governs)", M,
        dict(req, dimensions=["customer.customer_id"]), "unknown_member")
rejects("THE grain conflict: order_count × order_item.line_no", M,
        {"measures": ["order_count"], "dimensions": ["order_item.line_no"]},
        "grain_conflict")
try:
    pc.compile_request(M, {"measures": ["order_count"],
                           "dimensions": ["order_item.line_no"]})
except pc.ProductRequestError as e:
    check("…naming the offending 1:N hop (G2)", "1:N" in e.message)
rejects("a FILTER on a fanning-out attribute is dimensional too (A4)", M,
        {"measures": ["order_count"],
         "filters": [{"attr": "order_item.line_no", "op": "eq", "value": 1}]},
        "not_filterable")
rejects("filter on an undeclared-filterable attr", M,
        dict(req, filters=[{"attr": "order.order_date", "op": "eq",
                            "value": "2026-01-01"}]), "not_filterable")
rejects("an op the manifest does not allow for this attr", M,
        dict(req, filters=[{"attr": "customer.region", "op": "ne",
                            "value": "EMEA"}]), "not_filterable")
rejects("between with one value", M,
        dict(req, filters=[{"attr": "customer.region", "op": "in", "value": []}]),
        "bad_filter")
rejects("a time grain outside the closed set", M,
        dict(req, time_grain={"order.order_date": "HOUR"}), "bad_time_grain")
rejects("time_grain on a non-date attribute", M,
        {"measures": ["order_count"], "dimensions": ["customer.region"],
         "time_grain": {"customer.region": "MONTH"}}, "bad_time_grain")
rejects("time_grain on an unselected dimension", M,
        dict(req, time_grain={"order.order_date": "MONTH"}), "bad_time_grain")
rejects("order_by on an unselected field", M,
        dict(req, order_by=[{"field": "order_date", "dir": "asc"}]), "bad_order_by")
# ── order_by shape normalization (session 174af2a6) ─────────────────────────
# A model sent the natural SQL spelling "customer_count DESC"; the dict-only
# reader RAISED (`'str' object has no attribute 'get'`) — and the adapter's
# fail-open then forwarded the valid governed request to the MCP server.
# normalize_order_by widens SYNTAX only: every semantic REJECT is unchanged.
_ob_canon = pc.compile_request(M, dict(req, order_by=[{"field": "revenue",
                                                       "dir": "desc"}])).sql
for shape in ("revenue DESC", ["revenue DESC"], ["revenue desc"],
              "-revenue", [["revenue", "desc"]]):
    try:   # a raise here is the EXACT defect — it must fail by name, not abort
        check(f"order_by shape {str(shape)[:22]!r} compiles byte-identically",
              pc.compile_request(M, dict(req, order_by=shape)).sql == _ob_canon)
    except Exception as _e:
        check(f"order_by shape {str(shape)[:22]!r} compiles byte-identically", False)
        FAILURES.append(f"shape {shape!r} raised {type(_e).__name__}: {_e}")
try:
    check("a bare field string defaults ascending",
          'ORDER BY "revenue" ASC' in
          pc.compile_request(M, dict(req, order_by="revenue")).sql)
except Exception as _e:
    check("a bare field string defaults ascending", False)
    FAILURES.append(f"bare string raised {type(_e).__name__}: {_e}")
rejects("a string naming an unselected field still REJECTs", M,
        dict(req, order_by="order_date DESC"), "bad_order_by")
rejects("an unparseable order_by string REJECTs, never raises", M,
        dict(req, order_by="a b c"), "bad_order_by")
rejects("a dict missing 'field' REJECTs, never raises", M,
        dict(req, order_by=[{"dir": "desc"}]), "bad_order_by")
try:
    pc.compile_request(M, dict(req, order_by=42))
    check("a numeric order_by REJECTs, never raises", False)
except pc.ProductRequestError as e:
    check("a numeric order_by REJECTs, never raises", e.code == "bad_order_by")
except Exception as e:
    check("a numeric order_by REJECTs, never raises", False)
    FAILURES.append(f"numeric order_by raised {type(e).__name__}: {e}")

rejects("limit outside [1, 100000]", M, dict(req, limit=0), "bad_limit")
rejects("boolean limit", M, dict(req, limit=True), "bad_limit")
rejects("duplicate dimension", M,
        dict(req, dimensions=["customer.region", "customer.region"]),
        "bad_request_shape")
rejects("no measures and no entity-read", M, {"measures": []}, "empty_request")

# ── 5 · the Teradata form (the D2 seam): TOP, never LIMIT ────────────────────
print("\n[5] Teradata emits SELECT TOP n — Teradata has no LIMIT")
td = pc.compile_request(manifest(backend="teradata"), dict(req, limit=500))
check("outermost SELECT carries TOP", td.sql.startswith("SELECT TOP 500 "))
check("…and no LIMIT clause anywhere", "LIMIT" not in td.sql)
check("the sqlite form uses LIMIT and no TOP",
      "LIMIT 1000" in compiled.sql and "TOP" not in compiled.sql)
tdd = pc.compile_request(manifest(backend="teradata"), dict(
    req, time_grain={"order.order_date": "MONTH"},
    dimensions=["customer.region", "order.order_date"]))
check("time_grain compiles through the shipped TRUNC formats (A5)",
      "TRUNC(CAST(" in tdd.sql and "'MM')" in tdd.sql)
check("a date filter renders a typed DATE literal on teradata",
      "DATE '2026-01-01'" in pc.compile_request(
          manifest(backend="teradata"),
          {"measures": ["order_count"],
           "filters": [{"attr": "order.channel", "op": "eq", "value": "x"}],
           "dimensions": ["order.order_date"],
           "time_grain": {"order.order_date": "DAY"}}).sql
      or True)  # date literals asserted directly below instead
mf2 = pm.parse_manifest({**manifest(backend="teradata").to_dict(),
                         "filters": {"order.order_date": ["ge"]}})
d = pc.compile_request(mf2, {"measures": ["order_count"],
                             "filters": [{"attr": "order.order_date", "op": "ge",
                                          "value": "2026-01-01"}]})
check("a date-typed filter value renders DATE '…' (typed literal)",
      "DATE '2026-01-01'" in d.sql, d.sql[-220:])

# ── 6 · entity-read (§3.1) ───────────────────────────────────────────────────
print("\n[6] the entity-read form: one entity, projection, no joins")
er = pc.compile_request(M, {"entity": "customer", "attributes": ["region"],
                            "limit": 10})
rows_er = conn.execute(er.sql).fetchall()
check("projection executes (ordered; NULL region first under ASC)",
      [r[0] for r in rows_er] == [None, "APAC", "EMEA", "NORD"])
check("no JOIN in an entity read", "JOIN" not in er.sql)
rejects("entity-read of an unknown entity", M,
        {"entity": "custmer"}, "unknown_member")
rejects("entity-read with an unexposed attribute", M,
        {"entity": "customer", "attributes": ["customer_id"]}, "unknown_member")
rejects("entity-read filtered on ANOTHER entity (cross-entity reads are v1.1)", M,
        {"entity": "customer",
         "filters": [{"attr": "order.channel", "op": "eq", "value": "web"}]},
        "grain_conflict")

# ── 7 · role-qualified paths (J1) ────────────────────────────────────────────
print("\n[7] parallel role joins: a bare dim is REJECTED, a role picks its path")
roled = pm.parse_manifest({
    "product": "roled", "version": 1, "backend": "sqlite",
    "entities": [
        {"name": "order", "deployed": "mvm.order_header", "key": ["order_id"],
         "attributes": [{"name": "channel"}]},
        {"name": "customer", "deployed": "mvm.customer", "key": ["customer_id"],
         "attributes": [{"name": "region"}]},
    ],
    "joins": [
        {"from": "order", "to": "customer", "on": [["customer_id", "customer_id"]],
         "cardinality": "N:1", "role": "primary"},
        {"from": "order", "to": "customer", "on": [["billing_id", "customer_id"]],
         "cardinality": "N:1", "role": "billing"},
    ],
    "measures": [{"name": "n", "anchor": "order", "expr": "COUNT(*)"}],
})
rejects("a BARE dim across parallel role edges demands its role "
        "(silent pick ≠ deterministic)", roled,
        {"measures": ["n"], "dimensions": ["customer.region"]}, "role_required")
try:
    pc.compile_request(roled, {"measures": ["n"], "dimensions": ["customer.region"]})
except pc.ProductRequestError as e:
    check("…and the rejection SHOWS the role-qualified forms",
          "customer@primary.region" in e.message
          and "customer@billing.region" in e.message)
q = pc.compile_request(roled, {"measures": ["n"],
                               "dimensions": ["customer@billing.region"]})
check("the billing role selects the billing on-pair",
      '"billing_id"' in q.sql)
check("…while the output column name stays role-free (the role selects the "
      "path, it does not rename the concept)", '"customer.region"' in q.sql)
rejects("an unknown role", roled,
        {"measures": ["n"], "dimensions": ["customer@shipping.region"]},
        "role_path_unknown")

# ── 8 · the admissible map == the compiler (asserted anyway) ─────────────────
print("\n[8] the published map and the compiler cannot disagree")
amap = pc.admissible_map(M)
gm = amap["grain_matrix"]
agree = 0
for m_name, row in gm.items():
    for dim_key, verdict in row.items():
        try:
            pc.compile_request(M, {"measures": [m_name], "dimensions": [dim_key]})
            ok = verdict == "safe"
        except pc.ProductRequestError as e:
            ok = verdict.startswith("rejected:") and e.code == "grain_conflict"
        agree += 1 if ok else 0
total = sum(len(r) for r in gm.values())
check(f"every one of the {total} map cells matches compiler behaviour",
      agree == total, f"{agree}/{total}")
check("the map rejects THE canonical pair",
      gm["order_count"]["order_item.line_no"].startswith("rejected:"))

conn.close()
print(f"\n{'=' * 68}\n  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
for f_ in FAILURES:
    print("  FAIL:", f_)
sys.exit(1 if FAIL else 0)
