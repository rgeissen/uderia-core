"""
A base update replaces what a walk produces — and nothing an extension or a contract owns.

Found Oct 2026 walking Act II: asked "if the base model is updated, does the Lead survive?",
an experiment on a copy of the shape showed the Lead and its attributes kept but the schema,
table and columns its landing wrote DELETED — the concept survived, the model no longer knew
where it lives. The keep rule read `layer` only, and a landing writes physical-layer nodes.
Mapping every reader of "what is the base" found the same split elsewhere: signed action types
and data products (logical layer, authored by a person) were deleted by the same update; a
marketplace base listing carried a landed table as BASE (an unpublished extension's too); the
released view kept a pinned table of an unpublished extension and missed a published one landed
after the pin.

  §A  the one keep rule (kgstore.types.is_kept_on_replace) and realisations_of — pure
  §B  a base update keeps the extension whole: the Lead, its landed schema/table/columns, an
      attribute ALTERed onto a base table, the action type + its ledger, the data product —
      every edge intact or re-linked; twice is the same as once
  §C  a walk that reads the warehouse sees the landed table: ONE node, still the extension's
      (its marks carried), still hidden while the extension is unpublished
  §D  what a base update cannot keep is NAMED: a dropped container stamped on the concept it
      concerns; a realisation whose concept is gone listed as orphaned
  §E  the generic backend (every non-SQLite store) applies the same rule
  §F  the marketplace: a realisation is the extension's, never the base's — it does not
      travel; the extension's conformance still composes with its own realisation
  §G  the released view: a published extension WITH its realisation; an unpublished one with
      neither; the pinned base never carries one

Run: python test/test_extension_survives_base_update.py
"""
import asyncio
import os
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src")); sys.path.insert(0, ROOT)
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail != "" else ""))


def _run(coro): return asyncio.run(coro)


os.environ.pop("TDA_ACT", None)
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402
from trusted_data_agent.kgstore import factory as _factory  # noqa: E402
from trusted_data_agent.kgstore import types as T  # noqa: E402
from trusted_data_agent.kgstore.base import KGStoreBackend  # noqa: E402
from components.builtin.knowledge_graph.graph_store import GraphStore  # noqa: E402
from trusted_data_agent.auth.database import _migrate_to_multi_kg  # noqa: E402

_fd, TMP = tempfile.mkstemp(suffix=".db"); os.close(_fd)
_c = sqlite3.connect(TMP)
_c.executescript(open(os.path.join(ROOT, "schema", "21_knowledge_graph.sql")).read())
_migrate_to_multi_kg(_c); _c.commit(); _c.close()
APP_CONFIG.AUTH_DB_PATH = TMP
APP_CONFIG.KG_SEMANTIC_SEEDING = False
_OP.force_act(True)
_factory.reset_instance_cache()
_factory.get_backend_for_profile = lambda profile_id, user_uuid, config_id=None: _factory.get_backend("sqlite", {"db_path": TMP})
U, P = "u1", "p1"
EXT = "ext-u1"


def store(kg): return GraphStore(P, U, kg_id=kg, db_path=TMP)
def nodes(st): return {(e["name"], e["entity_type"]): e for e in _run(st.list_entities(limit=99999))}
def names(st): return sorted(n for n, _ in nodes(st))
def edges(st): return sorted((r["source_name"], r["relationship_type"], r["target_name"]) for r in _run(st.list_relationships(limit=99999)))


# the walked base: an entity with a table, and a second entity whose table an authored
# attribute is ALTERed onto
WALK = [{"name": "core.CaseObject", "entity_type": "entity", "properties": {"layer": "logical"}},
        {"name": "core.CaseObject.object_id", "entity_type": "attribute", "properties": {"layer": "logical"}},
        {"name": "core.Customers", "entity_type": "entity", "properties": {"layer": "logical"}},
        {"name": "Policing_Core", "entity_type": "schema", "properties": {"layer": "physical"}},
        {"name": "Policing_Core.CaseObject", "entity_type": "table", "properties": {"layer": "physical"}},
        {"name": "Policing_Core.Customers", "entity_type": "table", "properties": {"layer": "physical"}}]
WALK_R = [{"source_name": "core.CaseObject", "source_type": "entity", "target_name": "core.CaseObject.object_id", "target_type": "attribute", "relationship_type": "contains"},
          {"source_name": "Policing_Core", "source_type": "schema", "target_name": "Policing_Core.CaseObject", "target_type": "table", "relationship_type": "contains"},
          {"source_name": "Policing_Core", "source_type": "schema", "target_name": "Policing_Core.Customers", "target_type": "table", "relationship_type": "contains"},
          {"source_name": "Policing_Core.CaseObject", "source_type": "table", "target_name": "core.CaseObject", "target_type": "entity", "relationship_type": "implements"}]


def seed(kg, status="published"):
    st = store(kg); st.set_kg_metadata("Core", is_active=False); st.import_bulk(WALK, WALK_R)
    A = {"layer": "authored", "extension_id": EXT, "status": status}
    add = lambda n, t, p: _run(st.add_entity(n, t, p))  # noqa: E731
    rel = lambda a, b, t: _run(st.add_relationship(a, b, t))  # noqa: E731
    byname = lambda n, t: st.get_entity_by_name(n, t)["id"]  # noqa: E731
    lead = add("core.Lead", "entity", dict(A, deployed_table="UDERIA_EXT_Policing_Core.Lead"))
    lid = add("core.Lead.lead_id", "attribute", dict(A))
    tier = add("core.Customers.loyalty_tier", "attribute", dict(A))
    rel(lead, lid, "contains"); rel(lead, byname("core.CaseObject", "entity"), "relates_to")
    rel(tier, byname("core.Customers", "entity"), "relates_to")
    R = {"layer": "physical", "authored_deploy": True}
    sch = add("UDERIA_EXT_Policing_Core", "schema", dict(R))
    tbl = add("UDERIA_EXT_Policing_Core.Lead", "table", dict(R, realises="core.Lead", schema="UDERIA_EXT_Policing_Core"))
    col = add("UDERIA_EXT_Policing_Core.Lead.lead_id", "column", dict(R, realises="core.Lead.lead_id"))
    alt = add("Policing_Core.Customers.loyalty_tier", "column", dict(R, realises="core.Customers.loyalty_tier"))
    rel(sch, tbl, "contains"); rel(tbl, col, "contains"); rel(tbl, lead, "implements"); rel(col, lid, "implements")
    rel(byname("Policing_Core.Customers", "table"), alt, "contains"); rel(alt, tier, "implements")
    act = add("record_lead", "action_type", {"layer": "logical", "manifest_hash": "m1"})
    led = add("UDERIA_ACT_Lead", "ddl_artifact", {"layer": "physical", "purpose": "action_ledger"})
    rel(act, lead, "acts_on"); rel(act, led, "contains")
    prod = add("Open cases", "data_product", {"layer": "logical", "manifest": {"p": 1}})
    rel(prod, byname("core.CaseObject", "entity"), "member")
    return st


EXT_NODES = ["UDERIA_EXT_Policing_Core", "UDERIA_EXT_Policing_Core.Lead", "UDERIA_EXT_Policing_Core.Lead.lead_id",
             "Policing_Core.Customers.loyalty_tier", "core.Lead", "core.Lead.lead_id", "core.Customers.loyalty_tier",
             "record_lead", "UDERIA_ACT_Lead", "Open cases"]
EXT_EDGES = [("UDERIA_EXT_Policing_Core", "contains", "UDERIA_EXT_Policing_Core.Lead"),
             ("UDERIA_EXT_Policing_Core.Lead", "implements", "core.Lead"),
             ("UDERIA_EXT_Policing_Core.Lead", "contains", "UDERIA_EXT_Policing_Core.Lead.lead_id"),
             ("UDERIA_EXT_Policing_Core.Lead.lead_id", "implements", "core.Lead.lead_id"),
             ("Policing_Core.Customers", "contains", "Policing_Core.Customers.loyalty_tier"),
             ("Policing_Core.Customers.loyalty_tier", "implements", "core.Customers.loyalty_tier"),
             ("core.Lead", "relates_to", "core.CaseObject"),
             ("record_lead", "acts_on", "core.Lead"), ("record_lead", "contains", "UDERIA_ACT_Lead"),
             ("Open cases", "member", "core.CaseObject")]

print("[§A] one keep rule")
check("a realisation, an action type, its ledger, a data product and the authored/observed layers are kept; the walk is not",
      T.is_kept_on_replace({"entity_type": "table", "properties": {"layer": "physical", "authored_deploy": True}})
      and T.is_kept_on_replace({"entity_type": "action_type", "properties": {"layer": "logical"}})
      and T.is_kept_on_replace({"entity_type": "ddl_artifact", "properties": {"layer": "physical", "purpose": "action_ledger"}})
      and T.is_kept_on_replace({"entity_type": "data_product", "properties": {"layer": "logical"}})
      and T.is_kept_on_replace({"entity_type": "entity", "properties": {"layer": "authored"}})
      and T.is_kept_on_replace({"entity_type": "business_concept", "properties": {"layer": "observed"}})
      and not T.is_kept_on_replace({"entity_type": "table", "properties": {"layer": "physical"}})
      and not T.is_kept_on_replace({"entity_type": "entity", "properties": {"layer": "logical"}}))
_ents = list(nodes(seed("kg-a")).values())
check("realisations_of: a concept's table and columns and the sibling schema holding only those",
      sorted(e["name"] for e in T.realisations_of(_ents, ["core.Lead", "core.Lead.lead_id"]))
      == ["UDERIA_EXT_Policing_Core", "UDERIA_EXT_Policing_Core.Lead", "UDERIA_EXT_Policing_Core.Lead.lead_id"])

print("[§B] a base update keeps the extension whole (SQLite)")
st = seed("kg-b")
before_e = edges(st)
res = st.replace_model_layer(WALK, WALK_R)
after = names(st)
check("every node of the extension and of its contracts survives", all(n in after for n in EXT_NODES),
      [n for n in EXT_NODES if n not in after])
check("…with every edge — kept, or re-linked by name to the re-walked base", all(e in edges(st) for e in EXT_EDGES),
      [e for e in EXT_EDGES if e not in edges(st)])
check("nothing dropped, nothing orphaned", not res.get("kept_edges_dropped") and not res.get("realisations_orphaned"),
      (res.get("kept_edges_dropped"), res.get("realisations_orphaned")))
st.replace_model_layer(WALK, WALK_R)
check("a second identical update changes nothing (no duplicate node or edge)", names(st) == after and len(edges(st)) == len(set(edges(st))) and sorted(before_e) == edges(st),
      sorted(set(before_e) ^ set(edges(st))))

print("[§C] a walk that reads the warehouse sees the landed table")
st = seed("kg-c", status="deployed")   # landed, NOT published
INTRO = WALK + [{"name": "UDERIA_EXT_Policing_Core.Lead", "entity_type": "table", "properties": {"layer": "physical", "row_count": 0}},
                {"name": "Policing_Core.Customers.loyalty_tier", "entity_type": "column", "properties": {"layer": "physical", "datatype": "VARCHAR(20)"}}]
INTRO_R = WALK_R + [{"source_name": "Policing_Core.Customers", "source_type": "table", "target_name": "Policing_Core.Customers.loyalty_tier", "target_type": "column", "relationship_type": "contains"}]
res = st.replace_model_layer(INTRO, INTRO_R)
nd = nodes(st)
tb = nd.get(("UDERIA_EXT_Policing_Core.Lead", "table"), {}).get("properties") or {}
cl = nd.get(("Policing_Core.Customers.loyalty_tier", "column"), {}).get("properties") or {}
check("one node each — the walk's content, the extension's marks carried", tb.get("row_count") == 0 and tb.get("authored_deploy") and tb.get("realises") == "core.Lead"
      and cl.get("datatype") == "VARCHAR(20)" and cl.get("realises") == "core.Customers.loyalty_tier" and res.get("realisation_marks_carried") == 2, (tb, cl, res.get("realisation_marks_carried")))
check("…so an unpublished extension's table stays out of the model's views",
      {"UDERIA_EXT_Policing_Core.Lead", "Policing_Core.Customers.loyalty_tier"} <= T.unpublished_extension_names(list(nd.values())))
check("…and a further update keeps it (it is still the extension's)",
      "UDERIA_EXT_Policing_Core.Lead" in [n for n, _ in nodes(st)] and (st.replace_model_layer(WALK, WALK_R) or True)
      and "UDERIA_EXT_Policing_Core.Lead" in names(st))

print("[§D] what an update cannot keep is named, never silent")
st = seed("kg-d")
NO_CUST = [e for e in WALK if "Customers" not in e["name"]]
NO_CUST_R = [r for r in WALK_R if "Customers" not in r["source_name"] + r["target_name"]]
res = st.replace_model_layer(NO_CUST, NO_CUST_R)
tier = nodes(st).get(("core.Customers.loyalty_tier", "attribute"), {}).get("properties") or {}
check("the base table the ALTERed column lived in is gone: the dropped edge is reported",
      any("Policing_Core.Customers --[contains]--> Policing_Core.Customers.loyalty_tier" in d for d in res.get("kept_edges_dropped") or []), res.get("kept_edges_dropped"))
check("…and stamped on the concept it concerns (the attribute the column realises)",
      any("Policing_Core.Customers --[contains]-->" in d for d in tier.get("dangling_edges") or []), tier.get("dangling_edges"))
st = seed("kg-d2")
carried = WALK + [{"name": "core.Lead", "entity_type": "entity", "properties": {"layer": "authored", "extension_id": EXT, "status": "published"}}]
res = st.replace_model_layer(carried, WALK_R)   # the publisher's newer extension, without lead_id
check("a realisation whose concept the carried extension no longer has is kept and listed as orphaned",
      "UDERIA_EXT_Policing_Core.Lead.lead_id" in names(st) and "UDERIA_EXT_Policing_Core.Lead.lead_id" in (res.get("realisations_orphaned") or []),
      res.get("realisations_orphaned"))

print("[§E] the generic backend (every store without a native replace) applies the same rule")
st = seed("kg-e")
be = _factory.get_backend("sqlite", {"db_path": TMP})
res = KGStoreBackend.replace_model_layer(be, "kg-e", P, U, WALK, WALK_R, T.KEPT_LAYERS_ON_REPLACE)
be.invalidate_cache("kg-e")
after = names(st)
check("the generic path keeps the extension and its contracts with their edges",
      all(n in after for n in EXT_NODES) and all(e in edges(st) for e in EXT_EDGES),
      ([n for n in EXT_NODES if n not in after], [e for e in EXT_EDGES if e not in edges(st)]))

print("[§F] the marketplace: a realisation is the extension's, never the base's")
from trusted_data_agent.core.trust import extensions as X  # noqa: E402
st = seed("kg-f")
ents = list(nodes(st).values())
rels = [dict(r) for r in _run(st.list_relationships(limit=99999))]
base_e, base_r, auth_e, _ = X.split_layers(ents, rels)
real_names = {"UDERIA_EXT_Policing_Core", "UDERIA_EXT_Policing_Core.Lead", "UDERIA_EXT_Policing_Core.Lead.lead_id", "Policing_Core.Customers.loyalty_tier"}
check("split_layers puts the realisation in the extension half", not (real_names & {e["name"] for e in base_e}) and real_names <= {e["name"] for e in auth_e})
pub_e, _ = X.portable_authored(ents, rels, U)
listed = {e["name"] for e in base_e + pub_e}
check("a base listing carries the published concept, not the landed table (a fact of THIS warehouse)",
      "core.Lead" in listed and not (real_names & listed), sorted(real_names & listed))
rep = X.extension_conformance(ents, rels, EXT)
check("the extension's conformance still composes with its own realisation (its 3 nodes + their 4 realisations)",
      rep.entity_count == len(base_e) + 3 + 4, (rep.entity_count, len(base_e)))

print("[§G] the released view")
from trusted_data_agent.core import kg_release as KR  # noqa: E402
ee, rr = KR.published_extension_layer("kg-f", TMP)
en = {e["name"] for e in ee}
check("a published extension reaches the released view WITH its realisation",
      {"core.Lead", "UDERIA_EXT_Policing_Core.Lead", "UDERIA_EXT_Policing_Core.Lead.lead_id"} <= en, sorted(en))
re_ = {(r["source_name"], r["relationship_type"], r["target_name"]) for r in rr}
check("…and the edges that locate it (implements, contains from the base table and the sibling schema)",
      ("UDERIA_EXT_Policing_Core.Lead", "implements", "core.Lead") in re_
      and ("Policing_Core.Customers", "contains", "Policing_Core.Customers.loyalty_tier") in re_
      and ("UDERIA_EXT_Policing_Core", "contains", "UDERIA_EXT_Policing_Core.Lead") in re_, sorted(re_))
seed("kg-g", status="deployed")
ee2, rr2 = KR.published_extension_layer("kg-g", TMP)
check("an unpublished (landed) extension reaches it with neither concept nor table", not ee2 and not rr2, [e["name"] for e in ee2])
snap = {"entities": list(nodes(store("kg-g")).values()), "relationships": [dict(r) for r in _run(store("kg-g").list_relationships(limit=99999))]}
be2, _ = KR._without_authored(snap)
check("the pinned base never carries a realisation (an unpublished table used to ride it into the released view)",
      not (real_names & {e["name"] for e in be2}))
check("the released view's fingerprint moves when an extension lands after the pin",
      KR._layer_fingerprint(ee, rr) != KR._layer_fingerprint([e for e in ee if not T.is_realisation(e.get("properties"))],
                                                             [r for r in rr if "UDERIA_EXT" not in r["source_name"] + r["target_name"] and "loyalty_tier" not in r["target_name"]]))

os.unlink(TMP)
print(f"\n{'ALL PASS' if not FAIL else 'FAILED'} — {PASS + FAIL} assertions ({PASS} passed, {FAIL} failed)")
sys.exit(1 if FAIL else 0)
