"""Concept declaration — deterministic, product-neutral, fails closed (2026-09-04)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from components.builtin.knowledge_graph.concepts import build_declaration, predicate_columns, predicate_is_boolean, slug

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

ENT = {"name": "Prod.Rec", "entity_type": "entity", "properties": {"product": "Prod", "deployed_table": "Prod_Domain.Rec", "label": "Rec"}}
ATTRS = ["V20081", "V20082", "V20111", "V20061", "STATE"]
PRED = "(V20081='2' OR V20082='2' OR V20111='58' OR V20061 IN ('262','265'))"

def t_predicate_parsing():
    check("columns named by a predicate", predicate_columns(PRED) == ["V20081", "V20082", "V20111", "V20061"], predicate_columns(PRED))
    check("an alias qualifier and string literals are not columns", predicate_columns("i.V1 = 'V2' AND TRIM(V3) <> ''") == ["V1", "V3"], predicate_columns("i.V1 = 'V2' AND TRIM(V3) <> ''"))
    check("a boolean predicate parses", predicate_is_boolean(PRED)[0], predicate_is_boolean(PRED))
    check("a statement is refused", not predicate_is_boolean("1=1; DROP TABLE x")[0], None)
    check("unbalanced parentheses are refused", not predicate_is_boolean("(V1='2'")[0], None)
    check("an empty predicate is refused", not predicate_is_boolean("")[0], None)

def t_declaration():
    d = build_declaration(concept="digital channel", definition="an incident with a computer or cyberspace element", predicate=PRED, entity=ENT, entity_attributes=ATTRS)
    check("no errors on a grounded predicate", d["errors"] == [], d["errors"])
    types = [e["entity_type"] for e in d["entities"]]
    check("term, metric and recipe are declared", types == ["business_concept", "metric", "query_recipe"], types)
    term, metric, recipe = d["entities"]
    check("the term carries definition, predicate and columns", term["properties"]["predicate"] == PRED and term["properties"]["columns"] == ["V20081", "V20082", "V20111", "V20061"], term["properties"])
    check("the metric is compilable: source + measures the compiler reads", metric["properties"]["metric_source"] == "Prod_Domain.Rec" and metric["properties"]["measures"][0]["expr"].startswith("SUM(CASE WHEN"), metric["properties"])
    check("the metric is named for the concept", metric["name"] == "Prod.digital_channel_count", metric["name"])
    check("the recipe is the proven statement", recipe["properties"]["sql"] == f"SELECT COUNT(*) AS digital_channel_count FROM Prod_Domain.Rec WHERE {PRED}", recipe["properties"]["sql"])
    kinds = {(r["source_type"], r["relationship_type"], r["target_type"]) for r in d["relationships"]}
    check("the metric measures the entity; the term relates to it and to each column", ("metric", "measures", "entity") in kinds and ("business_concept", "relates_to", "attribute") in kinds and sum(1 for r in d["relationships"] if r["target_type"] == "attribute") == 4, kinds)
    check("the Memory-module statements are returned, never run", "INSERT INTO Prod_Memory.Business_Glossary" in d["memory_module_sql"] and "Query_Cookbook" in d["memory_module_sql"], d["memory_module_sql"][:80])
    check("every relationship type is one the vocabulary admits", {r["relationship_type"] for r in d["relationships"]} <= {"relates_to", "measures"}, None)

def t_fails_closed():
    d = build_declaration(concept="x", definition="y", predicate="V99999='1' OR V20081='2'", entity=ENT, entity_attributes=ATTRS)
    check("an undeclared column refuses the declaration, naming it", d["entities"] == [] and any("V99999" in e for e in d["errors"]), d["errors"])
    d = build_declaration(concept="x", definition="y", predicate="1=1", entity=ENT, entity_attributes=ATTRS)
    check("a predicate naming no column is refused", d["entities"] == [] and d["errors"], d["errors"])
    d = build_declaration(concept="", definition="y", predicate=PRED, entity=ENT, entity_attributes=ATTRS)
    check("a nameless concept is refused", d["entities"] == [], d["errors"])
    check("slug is stable", slug("Digital  Channel!") == "digital_channel", slug("Digital  Channel!"))

def t_neutral_product():
    ent = {"name": "Customers", "entity_type": "entity", "properties": {"deployed_table": "fitness_db.Customers"}}
    d = build_declaration(concept="vip", definition="high spenders", predicate="State='CA'", entity=ent, entity_attributes=["State", "Email"])
    check("an unqualified graph declares without a product prefix", d["entities"][0]["name"] == "term:vip" and d["memory_module_sql"] == "", (d["entities"][0]["name"], d["memory_module_sql"]))

for fn in (t_predicate_parsing, t_declaration, t_fails_closed, t_neutral_product):
    try: fn()
    except Exception as exc:
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")


def t_store_round_trip():
    """The handler's action on the REAL SQLite backend: the declared nodes exist, the
    metric compiles through the platform's own resolver, an undeclared column is refused
    and writes nothing."""
    import asyncio, tempfile, inspect
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend
    from components.builtin.knowledge_graph import handler as kgh
    import sqlite3
    from trusted_data_agent.auth.database import _migrate_to_multi_kg
    schema = os.path.join(os.path.dirname(__file__), "..", "schema", "21_knowledge_graph.sql")
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False); tmp.close()
    conn = sqlite3.connect(tmp.name); conn.executescript(open(schema).read()); conn.commit(); _migrate_to_multi_kg(conn); conn.commit(); conn.close()
    b = SQLiteKGBackend(config={"db_path": tmp.name}); b.initialize()
    KG, P, U = "kg-concepts", "p1", "u1"

    class Store:  # the GraphStore surface the action uses, over the raw backend
        kg_id = KG
        async def add_entity(self, name, entity_type, properties=None, source="manual"):
            return b.add_entity(KG, P, U, name, entity_type, properties or {}, source=source)
        def get_entity_by_name(self, name, entity_type=None):  # SYNC on the real GraphStore
            return b.get_entity_by_name(KG, name, entity_type)
        async def search_entities(self, q, limit=10, entity_type=None):
            return b.search_entities(KG, q, limit, entity_type)
        async def add_relationship(self, s, t, rtype, cardinality=None, metadata=None):
            return b.add_relationship(KG, P, U, s, t, rtype, cardinality, metadata)
        def extract_subgraph_adaptive(self, **kw):
            return b.extract_subgraph_adaptive(KG, **kw)
    # Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
    # a double unfaithful in the dimension under test measures nothing (Sep 2026).
    import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
    from kg_double_fidelity import double_mismatches as _dm
    from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
    check("Store double matches the real GraphStore calling convention", not _dm(_RealGS, Store), _dm(_RealGS, Store))
    st = Store()
    # The fake must be FAITHFUL in the dimension under test: GraphStore mixes sync reads
    # with async writes, and a fake that awaits everything passed a handler that awaited
    # a dict on production (2026-09-04). Pin each method's calling convention to the real one.
    from components.builtin.knowledge_graph.graph_store import GraphStore as _GS
    for _m in ("add_entity", "get_entity_by_name", "search_entities", "add_relationship"):
        check(f"fake store mirrors GraphStore's convention for {_m}",
              inspect.iscoroutinefunction(getattr(Store, _m)) == inspect.iscoroutinefunction(getattr(_GS, _m)),
              f"{_m}: fake async={inspect.iscoroutinefunction(getattr(Store, _m))} real async={inspect.iscoroutinefunction(getattr(_GS, _m))}")
    async def seed():
        eid = await st.add_entity("Prod.Rec", "entity", {"product": "Prod", "deployed_table": "Prod_Domain.Rec", "layer": "logical"})
        for c in ("V20081", "V20111", "V20061"):
            cid = await st.add_entity(f"Prod.Rec.{c}", "attribute", {"label": c, "layer": "logical"})
            await st.add_relationship(eid, cid, "contains")
    asyncio.run(seed())
    cls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_handle_declare_concept")][0]
    h = cls.__new__(cls); h._get_store = lambda ctx: st  # component_id / tool_name are constant properties
    ok = asyncio.run(h._handle_declare_concept({"concept_name": "digital channel", "definition": "computer or cyberspace element",
                                                "predicate": "(V20081='2' OR V20111='58' OR V20061 IN ('262'))", "entity": "Rec"}, {"user_uuid": U}))
    check("the action declares without error", not (ok.spec or {}).get("error"), ok.spec)
    term = b.get_entity_by_name(KG, "Prod.term:digital channel"); metric = b.get_entity_by_name(KG, "Prod.digital_channel_count"); recipe = b.get_entity_by_name(KG, "Prod.recipe:Count digital channel")
    check("term, metric and recipe exist on the store", bool(term and metric and recipe), (bool(term), bool(metric), bool(recipe)))
    check("the term is linked to the entity and its three columns", (ok.metadata or {}).get("links") == 3 + 4, (ok.metadata or {}).get("links"))
    from trusted_data_agent.dbdeploy.metric_compiler import resolve_metric_from_entity, compile_metric
    mdef, src = resolve_metric_from_entity(metric["properties"])
    check("the platform's resolver reads the declared metric", mdef["measures"] and src == "Prod_Domain.Rec", (mdef, src))
    cr = compile_metric(mdef, source_table=src, selected_dimensions=[], selected_measures=["digital_channel_count"])
    check("the declared metric compiles through the platform's compiler", "SUM(CASE WHEN" in cr.sql.upper() and "Prod_Domain" in cr.sql and "Rec" in cr.sql, cr.sql[:200])
    bad = asyncio.run(h._handle_declare_concept({"concept_name": "x", "definition": "y", "predicate": "V99999='1'", "entity": "Rec"}, {"user_uuid": U}))
    check("an undeclared column is refused, naming it", "V99999" in str((bad.spec or {}).get("error")), bad.spec)
    check("a refusal writes nothing", b.get_entity_by_name(KG, "Prod.term:x") is None, None)
    os.unlink(tmp.name)


try:
    t_store_round_trip()
except Exception as exc:
    FAIL += 1; print(f"  FAIL: t_store_round_trip raised {type(exc).__name__}: {exc}")
print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
