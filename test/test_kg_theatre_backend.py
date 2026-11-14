"""
KG execution theatre — the backend half, CI-pure.

Three seams the theatre narrates from, each asserted at its root:

1. The adaptive extractor tags HOW every node entered the subgraph (`provenance`),
   recorded as the phases run — never reconstructed.
2. The enrichment metadata carries the ER facts (`seeds` with the scoring walk's own
   reasons, `entity_relationships` with keys + cardinality, `entity_keys`,
   `attribute_facts`, `entity_facts`) — and `_match_score` IS `_match_reasons`'
   score, so the ranker and the narration cannot disagree.
3. The live stage sink (`core/kg_stage.py`) emits over the notification queue without
   ever awaiting or raising: no sink ⇒ no-op; a full queue drops the notice; a stage
   reports once per turn even when the enrichment re-runs on a fit round.

Run: python3 test/test_kg_theatre_backend.py
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


# ── 1. extractor provenance ──────────────────────────────────────────────────
def t_extractor_provenance():
    import networkx as nx
    from trusted_data_agent.kgstore.sqlite_backend import _adaptive_subgraph_from_graph as f
    G = nx.DiGraph()
    G.add_node(1, name="Incident", entity_type="entity", properties={})
    G.add_node(2, name="Offence", entity_type="entity", properties={})
    G.add_node(3, name="Offence.offence_code", entity_type="attribute", properties={})
    G.add_node(4, name="Core", entity_type="database", properties={})
    G.add_node(5, name="Victim", entity_type="entity", properties={})
    G.add_node(6, name="Robbery", entity_type="business_concept", properties={})
    G.add_edge(1, 2, relationship_type="foreign_key", metadata={})
    G.add_edge(1, 5, relationship_type="foreign_key", metadata={})
    G.add_edge(2, 3, relationship_type="contains", metadata={})
    G.add_edge(4, 1, relationship_type="contains", metadata={})
    G.add_edge(2, 6, relationship_type="relates_to", metadata={})
    out = f(G, [2], max_nodes=50)
    prov = out.get("provenance") or {}
    check("provenance is on the subgraph", isinstance(prov, dict) and prov)
    check("the seed is tagged seed", prov.get(2) == "seed")
    check("a table reached through a key is fk_chain", prov.get(1) == "fk_chain" and prov.get(5) == "fk_chain")
    check("the database context is tagged database", prov.get(4) == "database")
    check("an expanded attribute is tagged column", prov.get(3) == "column")
    check("a semantic neighbour is tagged semantic", prov.get(6) == "semantic")
    check("every tagged node is in the subgraph", set(prov) <= {e["id"] for e in out["entities"]})
    # a seed given as an ATTRIBUTE tags its owning entity as seed too
    out2 = f(G, [3], max_nodes=50)
    check("an attribute seed lifts its owner to seed", (out2.get("provenance") or {}).get(2) == "seed")
    # the legacy shape is untouched: entities + relationships still there
    check("entities/relationships unchanged in shape", all(k in out for k in ("entities", "relationships")))


# ── 2. metadata facts + one scoring walk ────────────────────────────────────
def t_match_reasons_is_the_score():
    from components.builtin.knowledge_graph.handler import _match_score, _match_reasons, _scoring_terms
    ent = {"name": "Policing Core.Offence.offence_code", "entity_type": "attribute",
           "properties": {"description": "UCR offense code", "allowed_values": [{"value": "120", "label": "Robbery"}]}}
    terms = _scoring_terms("How many robbery offences per month")
    score, reasons, matched = _match_reasons(ent, terms)
    check("_match_score is the reasons walk's score", _match_score(ent, terms) == score)
    check("the decoded value is a reason", "decoded value" in reasons)
    check("the owning entity is a reason", "owning entity" in reasons)
    check("matched terms are the question's", "robbery" in matched or "robberies" in matched)
    check("no reason is repeated", len(reasons) == len(set(reasons)))
    z_score, z_reasons, z_matched = _match_reasons({"name": "X.y", "properties": {}}, terms)
    check("nothing matched → 0, no reasons", z_score == 0 and z_reasons == [] and z_matched == [])


def _subgraph():
    return {
        "entities": [
            {"id": 1, "name": "Policing Core.Incident", "entity_type": "entity",
             "properties": {"deployed_table": "Policing_Core.v_Incident", "subject_area": "crime", "row_count": 10, "key_unique": True, "description": "An incident"}},
            {"id": 2, "name": "Policing Core.Offence", "entity_type": "entity",
             "properties": {"deployed_table": "Policing_Core.v_Offence", "subject_area": "crime"}},
            {"id": 3, "name": "Policing Core.Offence.offence_code", "entity_type": "attribute",
             "properties": {"deployed_type": "VARCHAR(3)", "semantic_type": "code", "allowed_values": [{"value": "120", "label": "Robbery"}]}},
            {"id": 4, "name": "Policing Core.Offence.offence_seq", "entity_type": "attribute",
             "properties": {"deployed_type": "BYTEINT", "semantic_type": "identifier"}},
            {"id": 5, "name": "Policing Core.Incident.incident_date_text", "entity_type": "attribute",
             "properties": {"deployed_type": "CHAR(11)", "semantic_type": "date", "sampled_shape": "DD-MMM-YYYY", "is_pii": True}},
            {"id": 6, "name": "Policing Core.CaseObject", "entity_type": "entity",
             "properties": {"deployed_table": "Policing_Core.v_CaseObject", "subject_area": "evidence"}},
        ],
        "relationships": [
            {"id": 1, "source_id": 1, "target_id": 2, "source_name": "Policing Core.Incident", "target_name": "Policing Core.Offence",
             "relationship_type": "foreign_key", "cardinality": "1:N",
             "metadata": {"relationship_name": "Incident__Offence", "from_columns": "ori, incident_number", "to_columns": "ori, incident_number"}},
            {"id": 2, "source_id": 2, "target_id": 3, "source_name": "Policing Core.Offence", "target_name": "Policing Core.Offence.offence_code",
             "relationship_type": "contains", "cardinality": None, "metadata": {}},
            {"id": 3, "source_id": 2, "target_id": 6, "source_name": "Policing Core.Offence", "target_name": "Policing Core.CaseObject",
             "relationship_type": "relates_to", "cardinality": None, "metadata": {"linkage_mechanism": "type_correspondence"}},
        ],
        "provenance": {1: "fk_chain", 2: "seed", 3: "column", 5: "column", 6: "joinable"},
        "_bridge_ids": [6],
        "omitted_columns": {"Policing Core.Incident": 14},
    }


def t_theatre_facts():
    from components.builtin.knowledge_graph.handler import _theatre_facts
    sg = _subgraph()
    seeds = [sg["entities"][2], sg["entities"][1], {"id": 99, "name": "Not.Rendered", "entity_type": "entity", "properties": {}}]
    out = _theatre_facts(sg, seeds, {"bridged": [{"name": "Policing Core.Offence", "score": 0.6}]}, "How many robberies per month")
    check("question terms carried", "robbery" in out["question_terms"] or "robberies" in out["question_terms"])
    names = [s["name"] for s in out["seeds"]]
    check("only RENDERED seeds are narrated", "Not.Rendered" not in names and "Policing Core.Offence.offence_code" in names)
    seed = next(s for s in out["seeds"] if s["name"] == "Policing Core.Offence.offence_code")
    check("a seed carries reasons + terms + channel", seed["reasons"] and seed["terms"] and seed["channel"] == "lexical")
    check("a semantically bridged seed says so", next(s for s in out["seeds"] if s["name"] == "Policing Core.Offence")["channel"] == "semantic")
    check("provenance is keyed by name, bridge overrides", out["provenance"]["Policing Core.Offence"] == "seed" and out["provenance"]["Policing Core.CaseObject"] == "bridge")
    rels = out["entity_relationships"]
    check("entity-level relationships only (no contains)", all(r["type"] != "contains" for r in rels) and len(rels) == 2)
    fk = next(r for r in rels if r["type"] == "foreign_key")
    check("keys + cardinality + name travel", fk["cardinality"] == "1:N" and fk["from_columns"] == ["ori", "incident_number"] and fk["name"] == "Incident__Offence")
    check("the correspondence carries its mechanism", any(r["mechanism"] == "type_correspondence" for r in rels))
    check("the ONE side's columns are its key", out["entity_keys"]["Policing Core.Incident"] == ["ori", "incident_number"])
    check("the N side's columns are its foreign key", out["entity_foreign_keys"]["Policing Core.Offence"] == ["ori", "incident_number"])
    check("an identifier attribute joins the key", "offence_seq" in out["entity_keys"]["Policing Core.Offence"])
    # the DECLARED key is the key: complete, in declared order, and no identifier beside it
    sg2 = _subgraph()
    for e in sg2["entities"]:
        if e["name"] == "Policing Core.Offence":
            e["properties"]["business_key"] = "ori, incident_number, offence_seq"
        if e["name"] == "Policing Core.CaseObject":
            e["properties"]["business_key"] = "object_id"
    sg2["entities"].append({"id": 7, "name": "Policing Core.CaseObject.source_bundle_id", "entity_type": "attribute",
                            "properties": {"semantic_type": "identifier"}})
    out2 = _theatre_facts(sg2, seeds, None, "q")
    check("a declared key is drawn complete, even when a key column was budgeted out of the subgraph",
          out2["entity_keys"]["Policing Core.Offence"] == ["ori", "incident_number", "offence_seq"])
    check("an identifier that is not in the declared key is not drawn as one",
          out2["entity_keys"]["Policing Core.CaseObject"] == ["object_id"])
    af = out["attribute_facts"]["Policing Core.Offence"]["offence_code"]
    check("attribute facts: type, semantic, coded", af["type"] == "VARCHAR(3)" and af["semantic"] == "code" and af["coded"] is True)
    dt = out["attribute_facts"]["Policing Core.Incident"]["incident_date_text"]
    check("attribute facts: shape + pii", dt["shape"] == "DD-MMM-YYYY" and dt["pii"] is True)
    ef = out["entity_facts"]["Policing Core.Incident"]
    check("entity facts: deployed, area, rows, key_unique", ef["deployed"] == "Policing_Core.v_Incident" and ef["area"] == "crime" and ef["rows"] == 10 and ef["key_unique"] is True)
    check("omitted columns carried", out["omitted_columns"] == {"Policing Core.Incident": 14})
    # fails soft on junk
    junk = _theatre_facts({"entities": [{"id": 1}], "relationships": [{"source_id": 1, "target_id": 9}]}, [], None, "")
    check("junk input yields a dict, not a throw", isinstance(junk, dict))


# ── 3. the live stage sink ──────────────────────────────────────────────────
class _Q:
    def __init__(self, full=False):
        self.items = []
        self.full = full

    def put_nowait(self, m):
        if self.full:
            raise RuntimeError("full")
        self.items.append(m)


def t_stage_sink():
    from trusted_data_agent.core import kg_stage
    from trusted_data_agent.core.config import APP_STATE
    check("no sink → no-op, no raise", kg_stage.emit_stage("seed", {"count": 1}) is False)
    q, qfull = _Q(), _Q(full=True)
    APP_STATE.setdefault("notification_queues", {})["u1"] = {q, qfull}
    tok = kg_stage.set_stage_sink("u1", "s1")
    try:
        check("sink is armed", kg_stage.sink_active())
        ok = kg_stage.emit_stage("understand", {"terms": ["a"]})
        check("delivered to the healthy queue; the full one dropped, not raised", ok and len(q.items) == 1)
        m = q.items[0]
        check("message shape: type + session + stage + ts", m["type"] == "kg_stage" and m["payload"]["session_id"] == "s1" and m["payload"]["stage"] == "understand" and "ts" in m["payload"])
        check("a stage reports once per turn (fit rounds re-run the enrichment)", kg_stage.emit_stage("understand", {}) is False and len(q.items) == 1)
        check("an unknown stage is refused", kg_stage.emit_stage("nonsense", {}) is False)
        check("another stage still reports", kg_stage.emit_stage("inject", {"entities": 3}) and len(q.items) == 2)
    finally:
        kg_stage.reset_stage_sink(tok)
        APP_STATE["notification_queues"].pop("u1", None)
    check("after reset, silent again", kg_stage.emit_stage("seed", {}) is False)
    kg_stage.reset_stage_sink(tok)  # double reset is not an error


def t_sink_is_task_local():
    from trusted_data_agent.core import kg_stage
    from trusted_data_agent.core.config import APP_STATE
    seen = {}

    async def turn(user, sid):
        q = _Q(); APP_STATE.setdefault("notification_queues", {})[user] = {q}
        tok = kg_stage.set_stage_sink(user, sid)
        await asyncio.sleep(0.01)
        kg_stage.emit_stage("seed", {})
        await asyncio.sleep(0.01)
        kg_stage.reset_stage_sink(tok)
        seen[user] = [m["payload"]["session_id"] for m in q.items]
        APP_STATE["notification_queues"].pop(user, None)

    async def main():
        await asyncio.gather(turn("A", "sA"), turn("B", "sB"))
    asyncio.run(main())
    check("two concurrent turns never cross sessions", seen == {"A": ["sA"], "B": ["sB"]})


def t_execution_service_arms_the_sink():
    import ast, inspect
    from trusted_data_agent.agent import execution_service as es
    src = inspect.getsource(es)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_agent_execution")
    calls = [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call)]
    check("the turn boundary arms the sink", any("set_stage_sink" in c for c in calls))
    check("and resets it (in the finally, beside the principal)", any("reset_stage_sink" in c for c in calls))


def t_handler_emits_at_every_stage():
    import inspect
    from components.builtin.knowledge_graph import handler as h
    src = inspect.getsource(h.KnowledgeGraphHandler.get_context_enrichment)
    for stage in ("understand", "seed", "expand", "inject"):
        check(f"get_context_enrichment reports the {stage} stage", f'emit_stage("{stage}"' in src)
    check("theatre facts are stamped on the metadata", "_theatre_facts(" in src)


if __name__ == "__main__":
    for fn in [t_extractor_provenance, t_match_reasons_is_the_score, t_theatre_facts, t_stage_sink,
               t_sink_is_task_local, t_execution_service_arms_the_sink, t_handler_emits_at_every_stage]:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAIL += 1
            FAILURES.append(f"{fn.__name__}: raised {type(exc).__name__}: {exc}")
    for f in FAILURES:
        print("  ✗", f)
    print(f"=== KG theatre backend: {PASS} passed, {FAIL} failed ===")
    sys.exit(1 if FAIL else 0)
