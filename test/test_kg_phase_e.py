"""
KG program — Phase E gate (declared-key preference · usage telemetry · Neo4j parity).

  #7  Declared foreign keys render as VERIFIED join keys, preferred over name matches;
      name-coincidence joinables are labelled a hint (not a key) and cross-tagged
      [declared FK] when a real FK backs them.
  #8  Usage telemetry lives in a SEPARATE table (never touches kg_entities), fed to seed
      ranking as a prior that is 0.0 on an un-instrumented graph (byte-identical), can
      never displace a real lexical match, and is recorded fire-and-forget.
  #11 Neo4j parity: adaptive extraction is the ONE shared pure function over a DiGraph
      (not a BFS stub); search matches properties too (recall parity with SQLite); the
      ADAPTIVE_SUBGRAPH capability is advertised by SQLite and by Neo4j-with-NetworkX.

Run:  PYTHONPATH=src python3 test/test_kg_phase_e.py
"""
import asyncio
import inspect
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore import sqlite_backend as sb              # noqa: E402
from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend    # noqa: E402
from trusted_data_agent.kgstore import usage as kgusage                  # noqa: E402
from trusted_data_agent.kgstore.capabilities import KGStoreCapability    # noqa: E402
from components.builtin.knowledge_graph import handler as kgh            # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {('— ' + str(detail)) if detail else ''}")


def run(c):
    return asyncio.get_event_loop().run_until_complete(c)


HCLS = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_format_subgraph_for_prompt")][0]

print("\n[#7] declared foreign keys are preferred over name coincidence")
sub = {"entities": [
    {"id": 1, "entity_type": "entity", "name": "Sales", "properties": {}},
    {"id": 2, "entity_type": "entity", "name": "Customers", "properties": {}},
    {"id": 3, "entity_type": "attribute", "name": "Sales.CustomerID", "properties": {}},
    {"id": 4, "entity_type": "attribute", "name": "Sales.SaleID", "properties": {}},
    {"id": 5, "entity_type": "attribute", "name": "Customers.CustomerID", "properties": {}},
    {"id": 6, "entity_type": "attribute", "name": "Customers.SaleID", "properties": {}},   # name coincidence, NO fk
], "relationships": [
    {"relationship_type": "contains", "source_id": 1, "target_id": 3, "source_name": "Sales", "source_type": "entity", "target_name": "Sales.CustomerID", "target_type": "attribute"},
    {"relationship_type": "contains", "source_id": 1, "target_id": 4, "source_name": "Sales", "source_type": "entity", "target_name": "Sales.SaleID", "target_type": "attribute"},
    {"relationship_type": "contains", "source_id": 2, "target_id": 5, "source_name": "Customers", "source_type": "entity", "target_name": "Customers.CustomerID", "target_type": "attribute"},
    {"relationship_type": "contains", "source_id": 2, "target_id": 6, "source_name": "Customers", "source_type": "entity", "target_name": "Customers.SaleID", "target_type": "attribute"},
    {"relationship_type": "foreign_key", "source_id": 3, "target_id": 5, "source_name": "Sales.CustomerID", "source_type": "attribute", "target_name": "Customers.CustomerID", "target_type": "attribute"},
]}
txt = HCLS._format_subgraph_for_prompt(HCLS.__new__(HCLS), sub, {}, surface="off")
check("a DECLARED FOREIGN KEYS block renders when FK edges exist", "DECLARED FOREIGN KEYS (declared join columns" in txt)
di, ji = txt.find("DECLARED FOREIGN KEYS (declared join columns"), txt.find("JOINABLE COLUMNS")
check("declared keys are listed before name-based joinables", 0 <= di < ji)
dblock = txt[di:ji]
check("the real FK column (CustomerID) is a declared join key", "CustomerID: Customers, Sales" in dblock)
check("the FK-declared table pair is named", "Customers ⇄ Sales" in dblock)
jblock = txt[ji:ji + 400]
check("JOINABLE COLUMNS is labelled a hint, not a verified key", "NOT a verified key" in jblock)
check("a real-FK-backed joinable is cross-tagged [declared FK]", "CustomerID: Customers, Sales [declared FK]" in jblock)
# Re-pointed 2026-09-04: Customers and Sales HAVE a declared key, so a column they merely
# share by name (SaleID) is no longer offered as a hint at all — it is named as NOT a key.
check("a name-coincidence column between keyed tables (SaleID) is named as NOT a join key", "NOT JOIN KEYS: SaleID" in txt and "  SaleID:" not in jblock, txt[-400:])
nofk = {"entities": sub["entities"][:5], "relationships": [r for r in sub["relationships"] if r["relationship_type"] != "foreign_key"]}
check("no DECLARED block when there are no FK edges (regression-safe on FK-less graphs)",
      "DECLARED FOREIGN KEYS (declared join columns" not in HCLS._format_subgraph_for_prompt(HCLS.__new__(HCLS), nofk, {}, surface="off"))

print("\n[#7b] a declared pair names its join columns; shared-by-name columns between keyed tables are NOT keys (2026-09-04)")
# The policing case: the column-level key edge is on the physical layer and OUT of view;
# the pair is declared entity↔entity; both tables carry a declared key; BH010 is shared
# by name only. The agent joined on BH010 and the product ran for three hours.
sub2 = {"entities": [
    {"id": 1, "entity_type": "entity", "name": "P.Incident", "properties": {"business_key": "ORI,INCNUM"}},
    {"id": 2, "entity_type": "entity", "name": "P.Arrestee", "properties": {"business_key": "ORI, INCNUM, V6006"}},
    {"id": 3, "entity_type": "entity", "name": "P.Other", "properties": {}},
    {"id": 4, "entity_type": "attribute", "name": "P.Incident.BH010", "properties": {}},
    {"id": 5, "entity_type": "attribute", "name": "P.Arrestee.BH010", "properties": {}},
    {"id": 6, "entity_type": "attribute", "name": "P.Incident.XX", "properties": {}},
    {"id": 7, "entity_type": "attribute", "name": "P.Other.XX", "properties": {}},
], "relationships": [
    {"relationship_type": "contains", "source_id": 1, "target_id": 4, "source_name": "P.Incident", "source_type": "entity", "target_name": "P.Incident.BH010", "target_type": "attribute"},
    {"relationship_type": "contains", "source_id": 2, "target_id": 5, "source_name": "P.Arrestee", "source_type": "entity", "target_name": "P.Arrestee.BH010", "target_type": "attribute"},
    {"relationship_type": "contains", "source_id": 1, "target_id": 6, "source_name": "P.Incident", "source_type": "entity", "target_name": "P.Incident.XX", "target_type": "attribute"},
    {"relationship_type": "contains", "source_id": 3, "target_id": 7, "source_name": "P.Other", "source_type": "entity", "target_name": "P.Other.XX", "target_type": "attribute"},
    {"relationship_type": "foreign_key", "source_id": 2, "target_id": 1, "source_name": "P.Arrestee", "source_type": "entity", "target_name": "P.Incident", "target_type": "entity"},
]}
txt2 = HCLS._format_subgraph_for_prompt(HCLS.__new__(HCLS), sub2, {}, surface="off")
d2 = txt2[txt2.find("DECLARED FOREIGN KEYS"):]
check("a declared pair names its join columns from the tables' declared keys", "P.Arrestee ⇄ P.Incident (FK declared between these tables) — join on (ORI, INCNUM)" in d2, d2[:400])
check("the wider key is named and its extra column marked as not joining", "P.Arrestee is keyed on (ORI, INCNUM, V6006) — the extra column(s) do not join" in d2, d2[:500])
check("a column shared by name between keyed tables is named as NOT a join key", "NOT JOIN KEYS: BH010" in txt2, txt2[-500:])
check("that column no longer appears as a joinable hint", "  BH010:" not in txt2, txt2[-600:])
check("a shared column between tables WITHOUT a declared key stays a hint", "  XX: P.Incident, P.Other" in txt2, txt2[-600:])
check("the NOT-keys line says why", "multiplies rows without bound" in txt2, None)

print("\n[#E-bridge] a type_correspondence is a class-level correspondence to READ, never a JOIN")
tc = {"entities": [
    {"id": 1, "entity_type": "product_bridge", "name": "P.bridge.Offence__CaseObject",
     "properties": {"linkage_mechanism": "type_correspondence",
                    "basis": "Robbery (120) and Burglary (220) offences correspond to the CLASSES of digital evidence.",
                    "from_object_class": "Offence", "to_object_class": "CaseObject",
                    "target_resolved": True}},
], "relationships": []}
tctxt = HCLS._format_subgraph_for_prompt(HCLS.__new__(HCLS), tc, {}, surface="off")
check("a type_correspondence renders as a CONCEPTUAL CORRESPONDENCE, not a 'verified join'",
      "CONCEPTUAL CORRESPONDENCE at the CLASS/KIND level" in tctxt and "not a verified join" not in tctxt, tctxt)
check("the agent is told never to SELECT FROM / JOIN the correspondence node",
      "never SELECT FROM or JOIN it" in tctxt, tctxt)
check("the rich basis travels with it (not just the mechanism code)",
      "Robbery (120) and Burglary (220) offences correspond" in tctxt, tctxt)
check("a type_correspondence does NOT suggest probe_bridge value-overlap", "probe_bridge" not in tctxt, tctxt)
check("a registry without membership columns renders no 'covers' clause (never an empty IN ())",
      "covers" not in tctxt and "IN ()" not in tctxt, tctxt)
tc2 = {"entities": [dict(tc["entities"][0], properties=dict(
    tc["entities"][0]["properties"], from_columns="offence_code", to_columns="primary_type",
    from_values=["120", "220"], to_values=["uco-observable:File"]))], "relationships": []}
tctxt2 = HCLS._format_subgraph_for_prompt(HCLS.__new__(HCLS), tc2, {}, surface="off")
check("the membership renders as DATA on the CODE column — a 'robbery' filter is written on "
      "Offence.offence_code, never on a class name",
      "covers Offence.offence_code IN ('120', '220')" in tctxt2
      and "CaseObject.primary_type IN ('uco-observable:File')" in tctxt2, tctxt2)

fk = {"entities": [
    {"id": 1, "entity_type": "product_bridge", "name": "P.bridge.A__B",
     "properties": {"linkage_mechanism": "synthetic_crosswalk_key", "basis": "A self-declared crosswalk key.",
                    "target_resolved": False, "target_reference": "B_Current"}},
], "relationships": []}
fktxt = HCLS._format_subgraph_for_prompt(HCLS.__new__(HCLS), fk, {}, surface="off")
check("a non-type_correspondence bridge keeps 'DECLARED LINK, not a verified join'",
      "DECLARED LINK, not a verified join" in fktxt, fktxt)
check("a non-type_correspondence bridge renders its basis too", "A self-declared crosswalk key." in fktxt, fktxt)
check("an unresolved bridge target is still named unusable for a join",
      "target does not exist in this graph" in fktxt and "B_Current" in fktxt, fktxt)

print("\n[#8] usage telemetry: separate table, zero-when-empty prior, cannot displace a match")
# fresh usage DB
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
orig_path = kgusage._db_path
kgusage._db_path = lambda: tmp
try:
    check("priors on an empty store are {} (byte-identical ranking)", kgusage.priors("kg") == {})
    kgusage.record("kg", ["A", "A", "B"], "rendered")   # A deduped within a call
    kgusage.record("kg", ["A"], "used")
    kgusage.record("kg", ["C"], "seeded")
    st = kgusage.stats("kg")
    check("rendered dedups within a call (A rendered once, not twice)", st["A"]["rendered"] == 1)
    check("used recorded", st["A"]["used"] == 1)
    p = kgusage.priors("kg")
    check("the most-used entity has the top prior", p["A"] == max(p.values()) and p["A"] == 1.0, p)
    check("used ≫ rendered ≫ seeded (A>B>C)", p["A"] > p["B"] > p["C"], p)
    # the prior only breaks ties: an unmatched hot entity never outranks a matched cold one
    terms_hot = [{"name": "A", "entity_type": "attribute", "properties": {}}]   # hot, but no lexical match to "sales"
    matched = [{"name": "sales_total", "entity_type": "attribute", "properties": {"label": "sales"}}]
    ranked = kgh._rank_seeds(matched + terms_hot, "sales", limit=15, usage_priors={"A": 1.0})
    check("a hot-but-unmatched entity does NOT outrank a lexical match", ranked[0]["name"] == "sales_total", [e["name"] for e in ranked])
    # with no priors, ranking is identical to before
    r_none = kgh._rank_seeds(matched + terms_hot, "sales", limit=15)
    r_empty = kgh._rank_seeds(matched + terms_hot, "sales", limit=15, usage_priors={})
    check("empty/None priors → identical ranking (no-op)", [e["name"] for e in r_none] == [e["name"] for e in r_empty])
    # a genuine near-tie IS broken by usage
    tieA = {"name": "colA", "entity_type": "attribute", "properties": {}}
    tieB = {"name": "colB", "entity_type": "attribute", "properties": {}}
    tied = kgh._rank_seeds([tieA, tieB], "nomatch", limit=15, usage_priors={"colB": 1.0})
    check("a near-tie is broken in favour of the more-used entity", tied[0]["name"] == "colB", [e["name"] for e in tied])
    check("record never raises on a bad kind", kgusage.record("kg", ["A"], "bogus") == 0)
finally:
    kgusage._db_path = orig_path
check("_USAGE_PRIOR_MAX is below a leading-prefix match (+40) so a match always wins", kgh._USAGE_PRIOR_MAX < 40)
esrc = inspect.getsource(HCLS.get_context_enrichment)
check("enrichment records seeded + rendered fire-and-forget, gated by record_usage", "_record_usage_async" in esrc and "record_usage" in inspect.signature(HCLS.get_context_enrichment).parameters)
check("usage lives in its OWN table, never kg_entities", "kg_entity_usage" in open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "kgstore", "usage.py")).read() and "kg_entities" not in inspect.getsource(kgusage.record))

print("\n[#11] Neo4j parity via the ONE shared adaptive function; property search; capability")
check("the adaptive algorithm is a module-level pure function", callable(getattr(sb, "_adaptive_subgraph_from_graph", None)))
check("SQLite delegates to the shared function (not a private copy)", "_adaptive_subgraph_from_graph(" in inspect.getsource(SQLiteKGBackend.extract_subgraph_adaptive))
be = SQLiteKGBackend({"db_path": tempfile.NamedTemporaryFile(suffix=".db", delete=False).name}) if "config" in inspect.signature(SQLiteKGBackend.__init__).parameters else SQLiteKGBackend(tempfile.NamedTemporaryFile(suffix=".db", delete=False).name)
check("SQLite advertises ADAPTIVE_SUBGRAPH (NetworkX present)", KGStoreCapability.ADAPTIVE_SUBGRAPH in be.capabilities())
# neo4j source-level parity (no live instance): the stub is gone, it builds a DiGraph and calls the shared fn
nsrc = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "kgstore", "neo4j_backend.py")).read()
check("Neo4j adaptive calls the shared function (no BFS stub as the primary path)", "_adaptive_subgraph_from_graph(" in nsrc and "self._to_networkx(kg_id)" in nsrc)
check("Neo4j has a _to_networkx builder (entity_id-keyed, mirrors SQLite node attrs)", "def _to_networkx" in nsrc and 'name=e.get("name"), entity_type=e.get("entity_type")' in nsrc)
check("Neo4j search now matches properties too (recall parity)", "toString(e.properties)" in nsrc)
check("Neo4j advertises ADAPTIVE_SUBGRAPH only with NetworkX", "if _HAS_NX:" in nsrc and "KGStoreCapability.ADAPTIVE_SUBGRAPH" in nsrc)
# prove the shared function runs the SAME on any DiGraph: build one by hand and extract
import networkx as _nx
G = _nx.DiGraph()
G.add_node(1, name="Victim", entity_type="entity", properties={}, source="x")
for i in range(30):
    G.add_node(100 + i, name=f"Victim.C{i}", entity_type="attribute", properties={}, source="x")
    G.add_edge(1, 100 + i, rel_id=i, relationship_type="contains", cardinality=None, metadata={}, source="x")
G.add_node(200, name="V4019", entity_type="attribute", properties={"label": "V4019"}, source="x")
G.add_edge(1, 200, rel_id=999, relationship_type="contains", cardinality=None, metadata={}, source="x")
out = sb._adaptive_subgraph_from_graph(G, [1, 200], [1, 200], max_nodes=500, query_terms=["victim"], per_table_cap=6)
check("the shared function scopes columns on a hand-built DiGraph (backend-agnostic)",
      any(e["name"] == "V4019" for e in out["entities"]) and (out.get("omitted_columns") or {}).get("Victim", 0) >= 20, out.get("omitted_columns"))

print(f"\n=== KG Phase E results: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
