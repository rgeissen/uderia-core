"""A declared standard becomes nodes and edges in the graph.

The product declares its domain BY REFERENCE — an `ontology_iri` on an entity — and the
meaning lives in the published standard. This turns the reference into a
`business_concept` per class and an `is_a` from the entity that declares it.

The two properties worth guarding hardest are both about honesty:
  * a declared IRI the standard does not define is COUNTED, never invented;
  * a resync must produce the same graph, or the concepts silently disappear the first
    time someone updates the product.

CI-pure: no network, no DB.
"""
import ast, asyncio, inspect, sys, textwrap
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_passed = _failed = 0
def check(name, cond, detail=""):
    global _passed, _failed
    if cond: _passed += 1; print(f"  ok  {name}")
    else: _failed += 1; print(f"  FAIL: {name}" + (f": {detail}" if detail else ""))

from trusted_data_agent.agent.kg_constructors import ontology_alignment as oa

UCO = "https://ontology.unifiedcyberontology.org/uco/identity/"
OTHER = "https://example.org/other#"


def _ent(name, iri=None, etype="entity", **props):
    p = dict(props)
    if iri:
        p["ontology_iri"] = iri
    return {"name": name, "entity_type": etype, "properties": p}


def _cls(iri, name=None, sup=(), props=()):
    return {"iri": iri, "name": name or iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1],
            "label": None, "definition": None,
            "subclass_of": list(sup), "properties": list(props)}


def t_declared_iris():
    ents = [_ent("A", UCO + "Identity"), _ent("B", UCO + "Identity"),
            _ent("C"), _ent("D", OTHER + "Thing"),
            _ent("T", UCO + "Ignored", etype="table")]
    got = oa.declared_iris(ents)
    check("each declared IRI appears once, in order",
          got == [UCO + "Identity", OTHER + "Thing"], str(got))
    # A physical table carries the deployed realization, not the declaration; reading one
    # would fetch a standard on behalf of a node that never claimed it.
    check("only logical entities declare", UCO + "Ignored" not in got)
    check("no entities yields nothing", oa.declared_iris([]) == [])


def t_a_class_becomes_a_concept_the_agent_can_see():
    ents = [_ent("Person", UCO + "Identity")]
    classes = {UCO + "Identity": _cls(UCO + "Identity", "Identity", props=[
        {"name": "hashValue", "iri": UCO + "hashValue", "required": True},
        {"name": "note", "iri": UCO + "note"}])}
    c, r, s = oa.align(ents, classes)
    check("one concept per class", len(c) == 1 and c[0]["name"] == "Identity")
    # business_concept is in SEMANTIC_TYPES, so the class reaches the agent's context
    # rather than being visible only in the inspector.
    check("it is a business_concept", c[0]["entity_type"] == "business_concept")
    p = c[0]["properties"]
    check("it carries its IRI — the authoritative identity", p["iri"] == UCO + "Identity")
    check("it is marked as read from a standard, not authored by the product",
          p["source_kind"] == "published_standard")
    check("it is logical", p["layer"] == "logical")
    check("it names the namespace it came from", p["standard_namespace"] == UCO)
    check("declared properties travel", [x["name"] for x in p["declared_properties"]]
          == ["hashValue", "note"])
    # Required-ness is the difference between "has a hashValue" and "hashValue is
    # REQUIRED and this object has none" — a conformance finding, not an absence.
    check("required properties are called out",
          p.get("required_properties") == ["hashValue"], str(p.get("required_properties")))
    check("the entity is linked to the class it declares",
          [(x["source_name"], x["relationship_type"], x["target_name"]) for x in r]
          == [("Person", "is_a", "Identity")])
    check("stats count the class and the link",
          s["standard_classes"] == 1 and s["standard_entities_linked"] == 1)


def t_is_a_not_implements():
    """`implements` is documented as physical -> logical and conformance reads it that
    way; reusing it here would give one edge two meanings."""
    c, r, s = oa.align([_ent("P", UCO + "Identity")],
                       {UCO + "Identity": _cls(UCO + "Identity")})
    check("the edge is is_a", all(x["relationship_type"] == "is_a" for x in r))
    check("no implements edge is emitted",
          not any(x["relationship_type"] == "implements" for x in r))


def t_the_standards_own_hierarchy_travels():
    obj, ident = UCO + "UcoObject", UCO + "Identity"
    classes = {ident: _cls(ident, "Identity", sup=[obj]), obj: _cls(obj, "UcoObject")}
    c, r, s = oa.align([_ent("P", ident)], classes)
    subs = [(x["source_name"], x["target_name"]) for x in r
            if x["source_type"] == "business_concept"]
    check("a subclass edge is emitted between ingested classes",
          subs == [("Identity", "UcoObject")], str(subs))
    check("counted", s["standard_subclass_edges"] == 1)
    # An ancestor outside the fetched set must not become a phantom node.
    c2, r2, _ = oa.align([_ent("P", ident)], {ident: _cls(ident, "Identity", sup=[obj])})
    check("an unfetched ancestor yields no node and no edge",
          [x["name"] for x in c2] == ["Identity"]
          and not any(x["source_type"] == "business_concept" for x in r2))


def t_an_undefined_declaration_is_counted_never_invented():
    """A fabricated definition presented as a published standard is worse than none."""
    ents = [_ent("Known", UCO + "Identity"), _ent("Unknown", OTHER + "Ghost")]
    c, r, s = oa.align(ents, {UCO + "Identity": _cls(UCO + "Identity")})
    check("no node is created for the undefined class",
          [x["name"] for x in c] == ["Identity"])
    check("no edge points at it", all(x["target_name"] != "Ghost" for x in r))
    check("it is reported", s["standard_iris_unresolved"] == 1)
    # Nothing fetched at all is not silence either.
    _, _, s2 = oa.align(ents, {})
    check("an unreachable standard reports every declaration",
          s2["standard_iris_unresolved"] == 2 and s2["standard_classes"] == 0)


def t_two_standards_may_use_one_local_name():
    """A KG node name is unique only WITHIN its type, so a bare local name would MERGE
    two different classes into one node — the graph would then assert that a product's
    entity is a kind of something it never declared."""
    a, b = UCO + "Identity", OTHER + "Identity"
    c, r, s = oa.align([_ent("P", a), _ent("Q", b)],
                       {a: _cls(a, "Identity"), b: _cls(b, "Identity")})
    names = [x["name"] for x in c]
    check("two classes yield two nodes", len(c) == 2, str(names))
    check("their names differ", len(set(names)) == 2, str(names))
    iris = {x["name"]: x["properties"]["iri"] for x in c}
    check("each node keeps its own IRI", set(iris.values()) == {a, b})
    for rel in r:
        if rel["source_type"] == "entity":
            check(f"{rel['source_name']} links to the class IT declared",
                  iris[rel["target_name"]] == rel["metadata"]["iri"])
    check("the collision-free name is used only where earned",
          "Identity" in names, str(names))


def t_a_product_declares_a_CURIE_not_an_absolute_iri():
    """The form the reference deployment actually uses.

    Every one of the 15 declaring entities on the policing products says
    `uco-core:UcoObject`, not the expanded IRI, while `extract_classes` keys by absolute
    IRI. A first version of this gate used absolute IRIs throughout, so it passed while
    the pass produced NOTHING against the real product — the same shape as the SHACL
    escape one increment earlier: a fixture faithful to the code rather than to its
    target measures nothing.
    """
    curie, absolute = "uco-core:UcoObject", UCO.replace("identity", "core") + "UcoObject"
    expand = lambda x: absolute if x == curie else (x if x.startswith("http") else None)
    ents = [_ent("Object", curie)]
    classes = {absolute: _cls(absolute, "UcoObject")}

    _, r_none, s_none = oa.align(ents, classes)
    check("without expansion the declaration cannot resolve — the defect, pinned",
          s_none["standard_entities_linked"] == 0 and s_none["standard_iris_unresolved"] == 1)

    c, r, s = oa.align(ents, classes, expand=expand)
    check("with expansion the entity links to the class it named",
          s["standard_entities_linked"] == 1 and not s["standard_iris_unresolved"])
    check("the node carries the ABSOLUTE iri — the authoritative identity",
          c[0]["properties"]["iri"] == absolute)
    meta = (r[0].get("metadata") or {}) if r else {}
    check("the edge records what the product SAID as well as what it resolves to",
          meta.get("iri") == absolute and meta.get("declared_as") == curie, str(meta))

    # An absolute declaration must not gain a spurious `declared_as`.
    _, r2, _ = oa.align([_ent("A", absolute)], classes, expand=expand)
    check("an already-absolute declaration records no restatement",
          bool(r2) and "declared_as" not in (r2[0].get("metadata") or {}))

    # An unexpandable CURIE is reported, never silently matched.
    _, _, s3 = oa.align([_ent("B", "nope:Thing")], classes, expand=expand)
    check("an unresolvable prefix is reported", s3["standard_iris_unresolved"] == 1)


def t_the_strategy_expands_before_filtering_classes():
    """`extract_classes(only=…)` filters on absolute IRIs. Handing it the declared CURIE
    matches no class at all, so the whole pass yields nothing — silently."""
    from trusted_data_agent.agent.kg_constructors import andp_semantic as strat
    src = inspect.getsource(strat.ANDPSemanticStrategy._align_standards)
    check("the declared references are expanded", "expand_iri" in src)
    check("the expanded form is what filters the classes",
          "only=abs_iris" in src, src[src.find("extract_classes"):][:80])
    check("and the resolver is handed to align",
          "expand=standards.expand_iri" in src)


def t_pure_and_deterministic():
    ents = [_ent("P", UCO + "Identity")]
    before = [dict(e, properties=dict(e["properties"])) for e in ents]
    classes = {UCO + "Identity": _cls(UCO + "Identity")}
    a1 = oa.align(ents, classes)
    a2 = oa.align(ents, classes)
    check("the input is not mutated", ents == before)
    check("the same input yields the same output", a1 == a2)


def t_both_paths_align_or_a_resync_deletes_the_concepts():
    """`apply-update` clears the graph and re-imports whatever `rebuild` returns. An
    alignment produced only on the build path would therefore be silently deleted the
    first time anyone updates the product — the same defect class recorded for the
    observation layer.

    This DRIVES rebuild rather than grepping it: a grep for the call is satisfied by an
    early `return` placed above it, i.e. by code that never runs. (That exact mutation
    escaped a first version of this gate.)
    """
    from trusted_data_agent.agent.kg_constructors import andp_semantic as strat
    from trusted_data_agent.semantic_interchange import standards, ontology_classes

    iri = UCO + "Identity"

    snap = strat.SemanticSnapshot(
        product="p", semantic_database="d",
        entity_metadata=[{"entity_name": "Person", "database_name": "bd",
                          "table_name": "person", "ontology_iri": iri}])

    real_fetch, real_extract = standards.fetch_ontology, ontology_classes.extract_classes

    async def _fake_fetch(urls, **kw):
        return object(), []
    standards.fetch_ontology = _fake_fetch
    ontology_classes.extract_classes = lambda g, **kw: {iri: _cls(iri, "Identity")}
    try:
        s = strat.ANDPSemanticStrategy.__new__(strat.ANDPSemanticStrategy)
        async def _fetch(ctx, opts):
            return snap
        s._fetch = _fetch
        ents, rels = asyncio.get_event_loop().run_until_complete(
            s.rebuild({"product": "p", "semantic_database": "d"}))
    finally:
        standards.fetch_ontology, ontology_classes.extract_classes = real_fetch, real_extract

    concepts = [e for e in ents if e["entity_type"] == "business_concept"]
    check("a resync reproduces the standard's classes",
          [c["name"] for c in concepts] == ["Identity"], str([e["name"] for e in ents]))
    check("and the link from the entity that declares one",
          any(r["relationship_type"] == "is_a" and r["target_name"] == "Identity"
              for r in rels))

    helper = inspect.getsource(strat.ANDPSemanticStrategy._align_standards)
    check("the alignment is one shared implementation, not a copy per path",
          "alignment.align(" in helper)
    body = "\n".join(l for l in inspect.getsource(strat).split("\n")
                     if "alignment.align(" in l)
    check("aligned in exactly one place", body.count("alignment.align(") == 1, body)


def t_the_choice_survives_a_resync():
    from trusted_data_agent.agent.kg_constructors import andp_semantic as strat
    d = strat._descriptor_from({"product": "p", "align_standards": False})
    check("the build-time choice is stamped on the descriptor",
          d.get("align_standards") is False, str(d.get("align_standards")))
    check("it defaults on",
          strat._descriptor_from({"product": "p"}).get("align_standards") is True)
    # A multi-product descriptor keeps only `products`, so rebuild must read the flag
    # from the entries or a graph built with it off silently regains it.
    src = inspect.getsource(strat.ANDPSemanticStrategy.rebuild)
    check("rebuild resolves the flag from the product entries",
          "align_standards" in src and "products" in src)


def t_no_declaration_means_no_network_and_no_change():
    """A product that declares no standard must build byte-identically to before this
    pass existed — including doing no network work at all."""
    from trusted_data_agent.agent.kg_constructors import andp_semantic as strat
    s = strat.ANDPSemanticStrategy.__new__(strat.ANDPSemanticStrategy)
    ents = [_ent("A"), _ent("B")]
    c, r, st = asyncio.get_event_loop().run_until_complete(
        s._align_standards(ents, {"product": "p"}))
    check("nothing is emitted", c == [] and r == [])
    check("no stats are claimed", st == {})
    # The opt-out short-circuits before any resolution.
    c2, r2, st2 = asyncio.get_event_loop().run_until_complete(
        s._align_standards([_ent("A", UCO + "Identity")],
                           {"product": "p", "align_standards": False}))
    check("the opt-out emits nothing", c2 == [] and r2 == [] and st2 == {})
    src = inspect.getsource(strat.ANDPSemanticStrategy._align_standards)
    i_opt, i_fetch = src.index("align_standards"), src.index("fetch_ontology")
    check("the opt-out is checked BEFORE any fetch", i_opt < i_fetch)


def t_alignment_names_no_standard():
    """The same neutrality rule the resolver carries: this module takes a graph and
    returns nodes; it must not know what any particular standard is."""
    import re
    banned = re.compile(r"(uco|caseontology|nibrs|dpv|dcat|police|crime)", re.I)
    for fn in (oa.declared_iris, oa.align, oa.concept_name, oa._properties_for):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        node = tree.body[0]
        body = node.body[1:] if (node.body and isinstance(node.body[0], ast.Expr)
                                 and isinstance(node.body[0].value, ast.Constant)
                                 and isinstance(node.body[0].value.value, str)) else node.body
        lits = [n.value for stmt in body for n in ast.walk(stmt)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        hits = sorted({l for l in lits if banned.search(l)})
        check(f"{fn.__name__} names no specific standard", not hits, str(hits))


def main():
    print("=" * 70); print("Ontology alignment — a declared standard becomes graph"); print("=" * 70)
    for t in (t_declared_iris, t_a_class_becomes_a_concept_the_agent_can_see,
              t_a_product_declares_a_CURIE_not_an_absolute_iri,
              t_the_strategy_expands_before_filtering_classes,
              t_is_a_not_implements, t_the_standards_own_hierarchy_travels,
              t_an_undefined_declaration_is_counted_never_invented,
              t_two_standards_may_use_one_local_name, t_pure_and_deterministic,
              t_both_paths_align_or_a_resync_deletes_the_concepts,
              t_the_choice_survives_a_resync,
              t_no_declaration_means_no_network_and_no_change,
              t_alignment_names_no_standard):
        try:
            t()
        except Exception as exc:
            global _failed; _failed += 1
            print(f"  FAIL: {t.__name__} raised {exc.__class__.__name__}: {exc}")
    print(f"\n{_passed} passed, {_failed} failed  ({_passed+_failed} assertions)")
    return 1 if _failed else 0

if __name__ == "__main__":
    sys.exit(main())
