"""Gate: a question spanning both subject areas of a declared class-level correspondence
LEADS the KG enrichment with that correspondence (Sep 2026).

The class: an integrated model with two subject areas, NO per-record key between them and
ONE declared correspondence — and a question that names something in each. Measured on
the policing core model (Ideate lane, 9 runs): with the bridge rendered only as an entity
line near the bottom of the block, a weak model wrote the cross-area JOIN 3 of 9 times and,
refused by the preflight guard, gave up twice; the pack skill that had prevented it was
restating the graph. The graph leads with the correspondence now — and the fixture here
is a RETAIL model, so the mechanism is proven generic and the policing pack is only one
instance of it.
"""
import ast, sys, os, inspect, asyncio
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
from components.builtin.knowledge_graph import handler as kgh  # noqa: E402

P = F = 0
def check(name, cond, detail=None):
    global P, F
    if cond: P += 1
    else:
        F += 1; print(f"  FAIL: {name}" + (f" — {detail}" if detail is not None else ""))

def ent(name, area, dep):
    return {"name": name, "entity_type": "entity",
            "properties": {"subject_area": area, "deployed_table": dep}}

def attr(name):
    return {"name": name, "entity_type": "attribute", "properties": {}}

BRIDGE = {"name": "Retail.bridge.Order__Session", "entity_type": "product_bridge",
          "properties": {"label": "Order__Session", "linkage_mechanism": "type_correspondence",
                         "basis": "Order kinds correspond to the web session kinds that produce them.",
                         "from_object_class": "Order", "to_object_class": "Session",
                         "from_columns": "channel_code", "to_columns": "session_kind",
                         "from_values": ["WEB", "APP"], "to_values": ["checkout", "browse"]}}
SUB = {"entities": [ent("Retail.Order", "store", "retail.v_order"),
                    ent("Retail.Customer", "store", "retail.v_customer"),
                    ent("Retail.Session", "web", "retail.v_session"),
                    ent("Retail.Page", "web", "retail.v_page"),
                    attr("Retail.Order.channel_code"), attr("Retail.Session.session_kind"),
                    BRIDGE],
       "relationships": [{"relationship_type": "relates_to", "source_type": "product_bridge",
                          "source_name": "Retail.bridge.Order__Session", "target_name": "Retail.Order"},
                         {"relationship_type": "relates_to", "source_type": "product_bridge",
                          "source_name": "Retail.bridge.Order__Session", "target_name": "Retail.Session"}]}
bridges = kgh._class_bridges(SUB)

print("[1] the detector fires on the CLASS — both areas reached — never on one")
check("the fixture declares one two-area correspondence", len(bridges) == 1 and sorted(bridges[0]["areas"]) == ["store", "web"], bridges)
both = kgh._spanning_correspondences([{"name": "Retail.Order"}, {"name": "Retail.Session"}], SUB, bridges)
check("seeds in both areas → the correspondence leads", [b["name"] for b in both] == ["Order__Session"], both)
via_attr = kgh._spanning_correspondences([{"name": "Retail.Order.channel_code"}, {"name": "Retail.Page"}], SUB, bridges)
check("an ATTRIBUTE seed resolves through its owning entity's area", len(via_attr) == 1, via_attr)
one = kgh._spanning_correspondences([{"name": "Retail.Order"}, {"name": "Retail.Customer"}], SUB, bridges)
check("seeds in ONE area → nothing leads (a within-area question is not the class)", one == [], one)
check("no seeds → nothing", kgh._spanning_correspondences([], SUB, bridges) == [], None)
check("the bridge node itself as a seed carries no area and fires nothing alone",
      kgh._spanning_correspondences([{"name": "Retail.bridge.Order__Session"}], SUB, bridges) == [], None)
noareas = {"entities": [{"name": "Fit.Sales", "entity_type": "entity", "properties": {"deployed_table": "fit.sales"}},
                        {"name": "Fit.Customers", "entity_type": "entity", "properties": {"deployed_table": "fit.customers"}}],
           "relationships": []}
check("a graph declaring NO subject areas (ANDP / industry / discovery) yields nothing → byte-identical",
      kgh._spanning_correspondences([{"name": "Fit.Sales"}, {"name": "Fit.Customers"}], noareas, kgh._class_bridges(noareas)) == [], None)

print("[2] the lead states the correspondence from the SAME bridge facts, and the rule")
lead = "\n".join(kgh._render_correspondence_lead(both))
check("names both areas", "store" in lead and "web" in lead, lead[:200])
check("says there is NO per-record key and the join is refused", "NO per-record key" in lead and "refused" in lead, None)
check("names the correspondence and its basis", "Order__Session" in lead and "Order kinds correspond" in lead, None)
check("renders what it covers, both sides, as data", "covers Order.channel_code IN ('WEB', 'APP')" in lead and "Session.session_kind IN ('checkout', 'browse')" in lead, lead)
check("states the rule: state it, each area from its own tables, no spanning figure, unlisted kind not bound",
      "STATING this correspondence" in lead and "own tables" in lead and "never one figure spanning both" in lead and "NOT bound" in lead, None)
check("contains no policing vocabulary (the mechanism is generic)", "NIBRS" not in lead and "Policing" not in lead and "robber" not in lead.lower(), None)

print("[3] the seam: the lead goes FIRST in the rendered block, and the event says so")
src = inspect.getsource(kgh.KnowledgeGraphHandler.get_context_enrichment) if hasattr(kgh, "KnowledgeGraphHandler") else open(kgh.__file__).read()
check("get_context_enrichment consults the detector with the question's seeds",
      "_spanning_correspondences(entities, subgraph, metadata.get(\"class_bridges\")" in src, None)
check("...and stamps the event with the bridges that led", 'metadata["spanning_bridges"]' in src, None)
check("...inserting the lead after the header line (first thing after the banner)",
      'head, sep, rest = text.partition("\\n")' in src and "_render_correspondence_lead(spanning)" in src, None)
# Executed: the real handler's render path with a fake store is heavy; the pure halves above are
# the contract, and the seam is pinned on the source. A byte-identity property for the no-lead case:
check("no spanning bridges → the text is left untouched (no stray insert)", "if spanning and text:" in src, None)

print("[4] place columns are identity columns (the Chicago class): kept under the cap")
import networkx as nx
from trusted_data_agent.kgstore import sqlite_backend as sb
G = nx.DiGraph()
for i, (leaf, props) in enumerate([("city", {}), ("state_abbr", {}), ("country", {}), ("region", {}),
                                    ("state_code", {}), ("core_city_code", {"allowed_values": [{"value": "1", "label": "Yes"}]}),
                                    ("population_group", {}), ("customer_name", {}), ("ori", {"is_primary_key": True})]):
    G.add_node(i, name=f"Retail.Agency.{leaf}", entity_type="attribute", properties=props)
tier = {G.nodes[n]["name"].rpartition(".")[2]: sb._column_tier(G, n, set(), []) for n in G.nodes}
for place in ("city", "state_abbr", "country", "region"):
    check(f"{place} sits in the identity tier (2), beside customer_name", tier[place] == 2, tier[place])
check("a CODE leaf is not a place name (state_code stays key-shaped, tier 3)", tier["state_code"] == 3, tier["state_code"])
check("a Yes/No flag ending in _code is not lifted (core_city_code: tier 3)", tier["core_city_code"] == 3, tier["core_city_code"])
check("an ordinary attribute stays tier 4", tier["population_group"] == 4, tier["population_group"])
check("the key stays tier 3", tier["ori"] == 3, tier["ori"])
# the class, end to end through the real scoped selection: a 15-column table capped at 12
# must keep `city` — before this tier it was one of the three cut.
G2 = nx.DiGraph(); G2.add_node(100, name="Retail.Agency", entity_type="entity", properties={})
leaves = ["core_city_code", "country_region", "nibrs_active", "agency_type_code", "state_abbr", "country_division",
          "agency_type", "population_group", "city", "core_city", "ori", "nibrs_active_code",
          "population_group_code", "country_region_code", "country_division_code"]
for i, leaf in enumerate(leaves):
    G2.add_node(i, name=f"Retail.Agency.{leaf}", entity_type="attribute", properties={}); G2.add_edge(100, i)
chosen = sb._scope_columns(G2, list(range(len(leaves))), set(), ["robberies", "chicago"], 12, 999)
names = {G2.nodes[n]["name"].rpartition(".")[2] for n in chosen}
check("a 15-column table capped at 12 keeps city AND state_abbr", "city" in names and "state_abbr" in names, sorted(names))
check("...and the cap still bites (12 of 15)", len(chosen) == 12, len(chosen))

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
