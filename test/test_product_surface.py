"""
Gate — the OBDA-READ data surface (Phase C1, scope §7.1 E1–E4): structural confinement.

"Cannot query outside its slice" made literal, and proven at every layer:

* the scope model (parse, baseline byte-identity, is_restricted, describe);
* E1 at the enforcement decision: a SURFACE-ONLY autonomous agent's raw SQL is
  denied fail-closed — with the ONE exception that makes the compile-never-execute
  doctrine work: the statement the surface itself compiled this turn (the
  turn-scoped governed-SQL registry, shared lifecycle with the principal);
* E2/E4 at the REAL query_product handler: off-surface products REJECT
  `not_on_surface`; non-🟢 products REJECT `unsigned_product` for a surface-only
  consumer; enforcement errors fail CLOSED;
* E3: interactive turns (no principal) and non-exclusive surfaces stay baseline.

Run: python3 test/test_product_surface.py   (repo root; E2/E4 use tda_auth.db)
"""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

from trusted_data_agent.core import agent_authority as aa   # noqa: E402
from trusted_data_agent.core.config import APP_CONFIG as _CFG  # noqa: E402

# This suite tests the data surface, so the OBDA-READ master must be ON: since a94081c4
# ("the additive imperative enforced at ONE seam", 2026-08-21) `parse_scope` DROPS the
# surface when the master is off — master off means OBDA-READ never shipped, which is itself
# gate-asserted in test_agent_authority. This suite predates that seam and sat red for
# days because it exercised the surface at the default (off). test_agent_authority is
# the master-off half; this file is the surface-semantics half.
_OP.force("read", True)

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


print("=" * 68)
print("OBDA-READ data surface — Phase C1 gate")
print("=" * 68)

# ── 1 · the scope model ──────────────────────────────────────────────────────
print("\n[1] parse + baseline byte-identity")
base = aa.parse_scope(None)
check("no authorityConfig → no surface, unrestricted",
      base.data_surface_products is None and not base.data_surface_exclusive
      and not base.is_restricted)
check("a config without dataSurface parses exactly as before (baseline)",
      aa.parse_scope({"deny_writes": True}).data_surface_products is None)
s = aa.parse_scope({"dataSurface": {"products": ["fitness_sales", "b"],
                                    "exclusive": True}})
check("products + exclusive parse",
      s.data_surface_products == frozenset({"fitness_sales", "b"})
      and s.data_surface_exclusive and not s.data_surface_advisory)
check("exclusive counts as RESTRICTED (it narrows what is reachable)",
      s.is_restricted)
check("…and describe names it", any("surface-only" in d for d in aa.describe_scope(s)))
additive = aa.parse_scope({"dataSurface": {"products": ["a"]}})
check("products WITHOUT exclusive is additive capability, NOT a restriction (E3)",
      not additive.is_restricted)
adv = aa.parse_scope({"dataSurface": {"products": ["a"], "exclusive": True,
                                      "advisory": True}})
check("advisory downgrades exclusive to an observation (not restricted, not described)",
      not adv.is_restricted and not any("surface-only" in d
                                        for d in aa.describe_scope(adv)))
check("a malformed dataSurface (non-dict) is ignored, never a crash",
      aa.parse_scope({"dataSurface": "yes"}).data_surface_products is None)
check("round-trip: to_dict carries the surface",
      s.to_dict()["data_surface"] == {"products": ["b", "fitness_sales"],
                                      "exclusive": True, "advisory": False})

# ── 2 · E1: raw SQL denied fail-closed — except the compiled contract ────────
print("\n[2] E1 — surface-only denies raw SQL; the compiled statement is the one exception")
GOV = 'SELECT TOP 10 "x" FROM "db"."t" GROUP BY "x"'
ok, why = aa.is_tool_allowed(s, "base_readQuery",
                             arguments={"sql": "SELECT * FROM anywhere"})
check("raw SQL under a surface-only scope is DENIED", ok is False)
check("…naming the correction (query_product) and the surface's products",
      "query_product" in why and "fitness_sales" in why)
ok, _ = aa.is_tool_allowed(s, "base_tableList",
                           arguments={"database_name": "db"})
check("a non-SQL tool stays allowed (discovery is not the escape hatch)", ok is True)
ok, _ = aa.is_tool_allowed(s, "TDA_Canvas", arguments={"sql": "SELECT 1"})
check("NO TDA_ exemption from the surface rule — a component executing SQL is "
      "the same escape hatch by another name", ok is False)
ok, _ = aa.is_tool_allowed(additive, "base_readQuery",
                           arguments={"sql": "SELECT 1"})
check("a NON-exclusive surface leaves the raw lane available (E3)", ok is True)
ok, _ = aa.is_tool_allowed(adv, "base_readQuery", arguments={"sql": "SELECT 1"})
check("advisory mode denies nothing (observe)", ok is True)

# the registry: the compiled contract is executable, exactly and only it
tok = aa.set_current_principal(aa.AgentPrincipal(
    owner_uuid="u1", profile_id="p1", scope=s))
try:
    aa.register_governed_sql(GOV + ";")
    ok, _ = aa.is_tool_allowed(s, "base_readQuery", arguments={"sql": GOV})
    check("the registered compiled statement IS executable", ok is True)
    ok, _ = aa.is_tool_allowed(s, "base_readQuery",
                               arguments={"sql": "  " + GOV.replace(" ", "\n ", 2)})
    check("…whitespace-reflowed too (the scanner's matching rule)", ok is True)
    ok, _ = aa.is_tool_allowed(s, "base_readQuery",
                               arguments={"sql": GOV + " WHERE 1=1"})
    check("an EDITED statement is denied — editing forfeits the contract", ok is False)
    # enforce_tool end-to-end (the chokepoint's actual entry)
    allowed, _ = aa.enforce_tool("base_readQuery",
                                 arguments={"sql": "SELECT * FROM secrets"})
    check("enforce_tool denies un-governed raw SQL for the ambient principal",
          allowed is False)
    allowed, _ = aa.enforce_tool("base_readQuery", arguments={"sql": GOV})
    check("enforce_tool permits the registered contract", allowed is True)
finally:
    aa.reset_current_principal(tok)
# lifecycle: a NEW principal (new turn) starts with an empty registry
tok = aa.set_current_principal(aa.AgentPrincipal(
    owner_uuid="u1", profile_id="p1", scope=s))
try:
    ok, _ = aa.is_tool_allowed(s, "base_readQuery", arguments={"sql": GOV})
    check("the registry RESETS with the principal — last turn's statement does "
          "not carry over", ok is False)
finally:
    aa.reset_current_principal(tok)
allowed, _ = aa.enforce_tool("base_readQuery", arguments={"sql": "SELECT 1"})
check("no principal (interactive turn) → baseline, nothing denied (E3)",
      allowed is True)

# ── 3 · E2/E4 at the REAL handler ────────────────────────────────────────────
print("\n[3] E2/E4 — the real query_product handler under a surface-only principal")
conn = sqlite3.connect("tda_auth.db")
row = conn.execute("SELECT kg_id, profile_id, user_uuid FROM kg_metadata "
                   "WHERE name LIKE '%Fitness%' AND is_active=1").fetchone()
conn.close()
if not row:
    print("  (no Fitness KG — E2/E4 handler section skipped)")
else:
    kg_id, profile_id, user_uuid = row
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler
    h = KnowledgeGraphHandler()
    ctx = {"profile_id": profile_id, "user_uuid": user_uuid}

    # ── Self-sufficient fixture (the agent-face suite's recorded pattern) ─────
    # This suite originally piggybacked on live products (`fitness_sales`,
    # `phase_a_acceptance`) which were later honestly cleaned up — leaving three
    # assertions failing on "no data product named …". A gate must own its fixture.
    FIXTURE = "surface_gate_fixture"
    OFF_SURFACE = "surface_gate_absent"      # never authored — stays a name only
    from components.builtin.knowledge_graph.graph_store import GraphStore
    from trusted_data_agent.dbdeploy.product_model import parse_manifest as _pm
    _kg_pid = h._get_active_kg_owner(profile_id, user_uuid) or profile_id
    _gs = GraphStore(profile_id=_kg_pid, user_uuid=user_uuid)
    _fixture = _pm({
        "product": FIXTURE, "version": 1, "backend": "teradata",
        "description": "surface gate fixture (owned by test_product_surface)",
        "entities": [{"name": "Products", "deployed": "fitness_db.Products",
                      "key": ["ProductID"],
                      "attributes": [{"name": "ProductType"}]}],
        "measures": [{"name": "row_count", "anchor": "Products",
                      "expr": "COUNT(*)"}]})
    _props = {"layer": "logical", "manifest": _fixture.to_dict(),
              "version": 1, "backend": "teradata",
              "description": "surface gate fixture"}
    _old = _gs.get_entity_by_name(FIXTURE, "data_product")
    if _old:
        asyncio.run(_gs.update_entity(_old["id"], properties=_props))
    else:
        asyncio.run(_gs.add_entity(name=FIXTURE, entity_type="data_product",
                                   properties=_props, source="test_fixture"))

    # Trust seam: the E4 gate consumes `_product_trust_state`; resolving a REAL 🟢
    # would require signing an artifact on the user's live KG (and test_product_trust
    # already proves that chain end-to-end). Patch the READ, per scenario.
    import contextlib

    @contextlib.contextmanager
    def trust_reads(state):
        orig = KnowledgeGraphHandler._product_trust_state
        KnowledgeGraphHandler._product_trust_state = (
            lambda self, kgid, name: state)
        try:
            yield
        finally:
            KnowledgeGraphHandler._product_trust_state = orig

    def run(args, scope, surface):
        # Since the A/B/C tri-state (profile_obda, 2026-08-20) VISIBILITY/grant is
        # resolved from the PROFILE while enforcement stays with the ambient principal —
        # so the suite supplies both, via the handler's documented test seam
        # (`context["profile"]`). This suite predates that split and sat red because it
        # supplied only the principal: the handler refused obda_not_enabled_for_agent.
        run_ctx = dict(ctx)
        run_ctx["profile"] = {"authorityConfig": {"dataSurface": surface}}
        tok = aa.set_current_principal(aa.AgentPrincipal(
            owner_uuid=user_uuid, profile_id=profile_id, scope=scope))
        try:
            return asyncio.run(h.process(args, run_ctx))
        finally:
            aa.reset_current_principal(tok)

    SURF_ONE = {"products": [FIXTURE], "exclusive": True}
    on_surface = aa.parse_scope({"dataSurface": SURF_ONE})
    with trust_reads("trusted"):
        r = run({"action": "query_product", "product_name": FIXTURE,
                 "measures": ["row_count"]}, on_surface, SURF_ONE)
    m = r.metadata or {}
    check("an on-surface, signed (🟢) product COMPILES for a surface-only agent",
          bool(m.get("sql")) and m.get("lane") == "governed", str(m)[:160])
    # …and the compiled statement became executable through the registry (the loop)
    tok = aa.set_current_principal(aa.AgentPrincipal(
        owner_uuid=user_uuid, profile_id=profile_id, scope=on_surface))
    try:
        with trust_reads("trusted"):
            r2 = asyncio.run(h.process({"action": "query_product",
                                        "product_name": FIXTURE,
                                        "measures": ["row_count"]},
                                       {**ctx, "profile": {"authorityConfig":
                                                           {"dataSurface": SURF_ONE}}}))
        allowed, _ = aa.enforce_tool("base_readQuery",
                                     arguments={"sql": (r2.metadata or {}).get("sql")})
        check("…and its statement is immediately executable at the chokepoint "
              "(compile → register → execute, one turn)", allowed is True)
    finally:
        aa.reset_current_principal(tok)

    r = run({"action": "query_product", "product_name": OFF_SURFACE,
             "measures": ["row_count"]}, on_surface, SURF_ONE)
    check("an OFF-surface product REJECTS not_on_surface naming the surface (E2)",
          (r.metadata or {}).get("error_code") == "not_on_surface"
          and FIXTURE in ((r.metadata or {}).get("error") or ""),
          str(r.metadata)[:160])

    SURF_BOTH = {"products": [FIXTURE], "exclusive": True}
    both = aa.parse_scope({"dataSurface": SURF_BOTH})
    with trust_reads("changed"):
        r = run({"action": "query_product", "product_name": FIXTURE,
                 "measures": ["row_count"]}, both, SURF_BOTH)
    check("a non-🟢 product REJECTS unsigned_product for a surface-only consumer (E4)",
          (r.metadata or {}).get("error_code") == "unsigned_product",
          str(r.metadata)[:160])

    SURF_ADV = {**SURF_BOTH, "advisory": True}
    adv_scope = aa.parse_scope({"dataSurface": SURF_ADV})
    with trust_reads("changed"):
        r = run({"action": "query_product", "product_name": FIXTURE,
                 "measures": ["row_count"]}, adv_scope, SURF_ADV)
    check("advisory mode serves the non-🟢 product (observe, not enforce)",
          bool((r.metadata or {}).get("sql")))

    SURF_MIXED = {"products": [FIXTURE]}
    with trust_reads("changed"):
        r = run({"action": "query_product", "product_name": FIXTURE,
                 "measures": ["row_count"]},
                aa.parse_scope({"dataSurface": SURF_MIXED}), SURF_MIXED)
    check("a non-exclusive surface serves without the E4 gate (additive, E3)",
          bool((r.metadata or {}).get("sql")))

print(f"\n{'=' * 68}\n  {PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
