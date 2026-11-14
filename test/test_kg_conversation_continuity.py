#!/usr/bin/env python3
"""KG enrichment — conversational continuity + the join backbone (CI-pure).

Production, 2026-09-19 (session 0aa3a650, @IDAIRL on airlines_core, gemini-3.1-flash-lite):
turn 1 "What are my top 5 customers based on miles flown" joined `mileage_accrual` to
`ffp_member` on `booking_segment_id = ffp_member_id`; turn 2 "Give me the names of those
customers" took 7 LLM rounds and 4 SQL statements (2 errors) and paired names to member ids
positionally. Reading the persisted enrichment named FOUR defects, each fixed at its root and
pinned here:

  A  the seed cut held PHYSICAL column twins of logical attributes, and a physical seed never
     expanded (only the logical layer is structurally expandable)      → twin collapse + twin following
  B  the FK column between two tables both in the subgraph was budgeted out by the relevance
     cap, so the DECLARED FOREIGN KEYS block was empty and the guard had nothing to enforce
                                                                        → join-backbone columns never capped
  C  `_declared_join_pairs` could not see a column-level FK at all (the subgraph's edges carried
     no endpoint types) and derived a key only from a table's `business_key`
                                                                        → types on edges; key from the edge
  D  seeding was query-only: "those customers" seeded every `*_name` column in the model and
     not the table the previous turn was about                          → previous-turn carry-over

  + the preflight guard checks a same-name key on EACH side of the ON (the turn-1 join named the
    key column on the wrong side and passed), the `query` lookup ranks on evidence and searches
    terms when the phrase matched only prose, and the seams thread `session_id`.

Run:  PYTHONPATH=src python3 test/test_kg_conversation_continuity.py
"""
import ast
import asyncio
import inspect
import os
import pathlib
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.kgstore import sqlite_backend as sb                 # noqa: E402
from trusted_data_agent.kgstore.sqlite_backend import SQLiteKGBackend       # noqa: E402
from components.builtin.knowledge_graph import handler as kgh               # noqa: E402
from trusted_data_agent.agent.preflight import joins                        # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


# ── a LAYERED graph on a real temp backend: logical entity/attribute + physical table/column,
#    `implements` physical→logical, FK edges on BOTH layers (the industry-model shape) ──────────
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
c = sqlite3.connect(tmp)
live = sqlite3.connect(os.path.join(ROOT, "tda_auth.db"))
for (ddl,) in live.execute("SELECT sql FROM sqlite_master WHERE type IN ('table','index') AND tbl_name IN ('kg_entities','kg_relationships') AND sql IS NOT NULL"):
    c.execute(ddl)
live.close(); c.commit(); c.close()
be = SQLiteKGBackend({"db_path": tmp}) if "config" in inspect.signature(SQLiteKGBackend.__init__).parameters else SQLiteKGBackend(tmp)

ents, rels = [], []
def table(logical, physical, cols, pk):
    ents.append({"name": logical, "entity_type": "entity", "properties": {"layer": "logical", "primary_key": pk,
                 "description": f"{logical.split('.')[-1]} master"}})
    ents.append({"name": physical, "entity_type": "table", "properties": {"layer": "physical"}})
    rels.append({"source_name": physical, "source_type": "table", "target_name": logical, "target_type": "entity", "relationship_type": "implements"})
    for cname, desc in cols:
        la, pc = f"{logical}.{cname}", f"{physical}.{cname}"
        ents.append({"name": la, "entity_type": "attribute", "properties": {"layer": "logical", "description": desc, "is_primary_key": cname == pk}})
        ents.append({"name": pc, "entity_type": "column", "properties": {"layer": "physical", "description": desc, "is_primary_key": cname == pk}})
        rels.append({"source_name": logical, "source_type": "entity", "target_name": la, "target_type": "attribute", "relationship_type": "contains"})
        rels.append({"source_name": physical, "source_type": "table", "target_name": pc, "target_type": "column", "relationship_type": "contains"})
        rels.append({"source_name": pc, "source_type": "column", "target_name": la, "target_type": "attribute", "relationship_type": "implements"})
def fk(a, b):   # both layers, like the walker
    for pre in ("loyalty", "airlines_core"):
        st = "attribute" if pre == "loyalty" else "column"
        rels.append({"source_name": f"{pre}.{a}", "source_type": st, "target_name": f"{pre}.{b}", "target_type": st, "relationship_type": "foreign_key"})

# mileage_accrual: many columns that MENTION miles (they fill a cap of 6), the FK to ffp_member among the rest
table("loyalty.mileage_accrual", "airlines_core.mileage_accrual",
      [("mileage_accrual_id", "id"), ("base_miles", "Base miles flown on the segment"), ("elite_bonus_miles", "elite bonus miles"),
       ("promotional_bonus_miles", "promo miles"), ("total_miles_credited", "total miles"), ("miles_expiry_date", "miles expiry"),
       ("ffp_member_id", "Unique identifier of the FFP member who earned the miles"), ("booking_segment_id", "segment"),
       ("accrual_date", "date"), ("posting_date", "date")], pk="mileage_accrual_id")
table("loyalty.ffp_member", "airlines_core.ffp_member",
      [("ffp_member_id", "FFP member id"), ("member_first_name", "First name"), ("member_last_name", "Last name"),
       ("home_airport_code", "home airport"), ("tier_status_id", "tier"), ("postal_code", "postal")], pk="ffp_member_id")
table("loyalty.miles_transfer", "airlines_core.miles_transfer",
      [("miles_transfer_id", "id"), ("sender_ffp_member_id", "sender"), ("receiver_ffp_member_id", "receiver"), ("miles_amount", "miles moved")], pk="miles_transfer_id")
table("loyalty.booking_segment", "airlines_core.booking_segment",
      [("booking_segment_id", "id"), ("flight_number", "flight")], pk="booking_segment_id")
fk("mileage_accrual.ffp_member_id", "ffp_member.ffp_member_id")
fk("mileage_accrual.booking_segment_id", "booking_segment.booking_segment_id")
fk("miles_transfer.sender_ffp_member_id", "ffp_member.ffp_member_id")
fk("miles_transfer.receiver_ffp_member_id", "ffp_member.ffp_member_id")
be.import_bulk("k", "p", "u", ents, rels)
G = be._get_graph("k")
by_name = {d.get("name"): n for n, d in G.nodes(data=True)}
def sub_names(out): return {e["name"] for e in out["entities"]}

print("[A] a physical seed expands through its logical twin; twins never compete for a slot")
phys = by_name["airlines_core.mileage_accrual.base_miles"]
out = sb._adaptive_subgraph_from_graph(G, [phys], [phys], max_nodes=500, query_terms=["miles"], per_table_cap=6)
check("a physical column seed brings its LOGICAL table into the subgraph",
      "loyalty.mileage_accrual" in sub_names(out), sorted(sub_names(out))[:6])
check("the twin itself is a seed (provenance) and tier-0 (never capped)",
      out["provenance"].get(by_name["loyalty.mileage_accrual.base_miles"]) == "seed" and "loyalty.mileage_accrual.base_miles" in sub_names(out))
legacy = sb._adaptive_subgraph_from_graph(G, [by_name["loyalty.mileage_accrual"]], [by_name["loyalty.mileage_accrual"]], max_nodes=500)
check("legacy (unscoped) call on a logical seed still returns every column (byte-identical coverage)",
      sum(1 for n in sub_names(legacy) if n.startswith("loyalty.mileage_accrual.")) == 10)
pool = [{"id": 1, "name": "airlines_core.mileage_accrual.base_miles", "entity_type": "column", "properties": {}},
        {"id": 2, "name": "loyalty.mileage_accrual.base_miles", "entity_type": "attribute", "properties": {}},
        {"id": 3, "name": "airlines_core.ffp_member", "entity_type": "table", "properties": {}},
        {"id": 4, "name": "loyalty.ffp_member", "entity_type": "entity", "properties": {}},
        {"id": 5, "name": "airlines_core.only_physical.col", "entity_type": "column", "properties": {}},
        {"id": 6, "name": "loyalty.loyalty_mileage_accrual", "entity_type": "metric", "properties": {}}]
sem = {"airlines_core.mileage_accrual.base_miles": 0.7}
kept, dropped = kgh._collapse_layer_twins(pool, sem)
check("a physical twin collapses onto its logical node, order kept", [e["id"] for e in kept] == [2, 4, 5, 6] and dropped == 2, [e["id"] for e in kept])
check("a physical node with NO logical twin in the pool is untouched", any(e["id"] == 5 for e in kept))
check("a semantic score the physical twin earned moves onto the logical node", sem.get("loyalty.mileage_accrual.base_miles") == 0.7, sem)
check("a metric / concept is never a twin of anything", kgh._layer_twin_key(pool[5]) is None)
check("empty pool → empty", kgh._collapse_layer_twins([], None) == ([], 0))
# a model whose views are named differently from its entities (CaseObject deployed as
# v_CaseObject) shares no NAME between the layers: the twin is what the logical node
# deploys as (local session f622f5ef turn 2 — 2 of 15 seed slots went to physical copies)
vpool = [{"id": 1, "name": "Policing_Core.v_CaseObject", "entity_type": "table", "properties": {}},
         {"id": 2, "name": "Policing Core.CaseObject", "entity_type": "entity",
          "properties": {"deployed_table": "Policing_Core.v_CaseObject"}},
         {"id": 3, "name": "Policing_Core.v_ChainOfCustody.evidence_object_id", "entity_type": "column", "properties": {}},
         {"id": 4, "name": "Policing Core.ChainOfCustody.evidence_object_id", "entity_type": "attribute",
          "properties": {"deployed_column": "Policing_Core.v_ChainOfCustody.evidence_object_id"}},
         {"id": 5, "name": "Policing_Core.v_Other", "entity_type": "table", "properties": {}}]
vsem = {"Policing_Core.v_CaseObject": 0.5}
vkept, vdropped = kgh._collapse_layer_twins(vpool, vsem)
check("a twin is recognised by the deployed identifier when the names differ", [e["id"] for e in vkept] == [2, 4, 5] and vdropped == 2, [e["id"] for e in vkept])
check("its semantic score moves onto the logical node", vsem.get("Policing Core.CaseObject") == 0.5, vsem)
check("a physical node no pooled logical node deploys as is untouched", any(e["id"] == 5 for e in vkept))

print("[B] a join-backbone column is never budgeted out")
ma, fm = by_name["loyalty.mileage_accrual"], by_name["loyalty.ffp_member"]
out = sb._adaptive_subgraph_from_graph(G, [ma, fm], [ma, fm], max_nodes=500, query_terms=["customers", "miles", "flown"], per_table_cap=6)
ma_cols = {n.split(".")[-1] for n in sub_names(out) if n.startswith("loyalty.mileage_accrual.")}
check("under a cap of 6 filled by miles-columns, the FK to a table IN the subgraph is still kept",
      "ffp_member_id" in ma_cols, sorted(ma_cols))
check("…and the primary key", "mileage_accrual_id" in ma_cols)
check("the FK to a table NOT in the subgraph is not forced (booking_segment_id is ordinary)",
      "booking_segment_id" not in ma_cols or len(ma_cols) <= 8, sorted(ma_cols))
check("the FK edge therefore travels in the subgraph's relationships",
      any(r["relationship_type"] == "foreign_key" and r["source_name"] == "loyalty.mileage_accrual.ffp_member_id" for r in out["relationships"]))
check("edges carry their endpoint TYPES", all(r.get("source_type") and r.get("target_type") for r in out["relationships"]))
solo_bb = {G.nodes[b]["name"] for b in sb._join_backbone_columns(G, {ma})}
check("with ffp_member absent, ffp_member_id is not a backbone column (the rule is about the PAIR); the pk still is",
      "loyalty.mileage_accrual.ffp_member_id" not in solo_bb and "loyalty.mileage_accrual.mileage_accrual_id" in solo_bb, sorted(solo_bb))
bb = sb._join_backbone_columns(G, {ma, fm})
check("the backbone is bounded per table", len([b for b in bb if G.nodes[b]["name"].startswith("loyalty.mileage_accrual.")]) <= sb._BACKBONE_CAP)
check("an empty table set yields no backbone", sb._join_backbone_columns(G, set()) == set())

print("[B1b] identity columns rank ahead of key-ish and noise columns")
fm_out = sb._adaptive_subgraph_from_graph(G, [fm], [fm], max_nodes=500, query_terms=["miles"], per_table_cap=4)
fm_cols = {n.split(".")[-1] for n in sub_names(fm_out) if n.startswith("loyalty.ffp_member.")}
check("under a cap of 4 with no term match, the NAME columns are in (pk + names before postal_code/home_airport_code)",
      {"ffp_member_id", "member_first_name", "member_last_name"} <= fm_cols and "postal_code" not in fm_cols, sorted(fm_cols))
check("the tier ladder: name 2 < key 3 < noise 4",
      sb._column_tier(G, by_name["loyalty.ffp_member.member_first_name"], set(), []) == 2
      and sb._column_tier(G, by_name["loyalty.ffp_member.tier_status_id"], set(), []) == 3
      and sb._column_tier(G, by_name["loyalty.ffp_member.home_airport_code"], set(), []) == 3
      and sb._column_tier(G, by_name["loyalty.mileage_accrual.accrual_date"], set(), []) == 4)
check("a key-ish leaf that also ends in 'name' is not an identity column (`name_code`)",
      sb._IDENTITY_LEAF_RE.search("display_name") and not (sb._IDENTITY_LEAF_RE.search("name_code") and not sb._KEY_LEAF_RE.search("name_code")))

print("[B2] under a node budget the seeder's ORDER decides which table gets its columns")
mt = by_name["loyalty.miles_transfer"]
tight = sb._adaptive_subgraph_from_graph(G, [fm, mt], [fm, mt], max_nodes=8, query_terms=["names"], per_table_cap=6)
tight2 = sb._adaptive_subgraph_from_graph(G, [mt, fm], [mt, fm], max_nodes=8, query_terms=["names"], per_table_cap=6)
fm_first = sum(1 for n in sub_names(tight) if n.startswith("loyalty.ffp_member."))
mt_first = sum(1 for n in sub_names(tight2) if n.startswith("loyalty.miles_transfer."))
check("the first-ranked seed table receives the columns when the budget cannot cover both",
      fm_first > 0 and mt_first > 0 and fm_first >= sum(1 for n in sub_names(tight) if n.startswith("loyalty.miles_transfer.")),
      (fm_first, mt_first))

print("[C] the declared join pair's key comes from the FK edge")
jp = kgh._declared_join_pairs(out)
pairs = {tuple(j["tables"]): j for j in jp}
p = pairs.get(("loyalty.ffp_member", "loyalty.mileage_accrual"))
check("the pair is declared and keyed on the edge's column", p is not None and p["key"] == ["ffp_member_id"], p)
check("the pair carries its edge columns oriented to (a, b)", p and p["edges"] == [["ffp_member_id", "ffp_member_id"]], p and p["edges"])
three = sb._adaptive_subgraph_from_graph(G, [fm, mt], [fm, mt], max_nodes=500, query_terms=["miles"], per_table_cap=6)
jp2 = {tuple(j["tables"]): j for j in kgh._declared_join_pairs(three)}
q = jp2.get(("loyalty.ffp_member", "loyalty.miles_transfer"))
check("TWO FKs between one pair (sender + receiver) → declared, edges named, NO key (never enforced)",
      q is not None and q["key"] == [] and len(q["edges"]) == 2, q)
# an edge shape WITHOUT endpoint types (an older backend) resolves them from the entities by id
noty = {"entities": out["entities"], "relationships": [{k: v for k, v in r.items() if k not in ("source_type", "target_type")} for r in out["relationships"]]}
check("endpoint types are recovered from the subgraph's entities when the edge lacks them",
      {tuple(j["tables"]): j["key"] for j in kgh._declared_join_pairs(noty)}.get(("loyalty.ffp_member", "loyalty.mileage_accrual")) == ["ffp_member_id"])
# a business_key still wins where the product declares one (the ANDP shape, unchanged)
andp = {"entities": [{"id": 1, "entity_type": "entity", "name": "P.Incident", "properties": {"business_key": "ORI,INCNUM"}},
                     {"id": 2, "entity_type": "entity", "name": "P.Arrestee", "properties": {"business_key": "ORI, INCNUM, V6006"}}],
        "relationships": [{"relationship_type": "foreign_key", "source_id": 2, "target_id": 1, "source_name": "P.Arrestee", "source_type": "entity", "target_name": "P.Incident", "target_type": "entity"}]}
check("a declared business_key is untouched by the edge rule", kgh._declared_join_pairs(andp)[0]["key"] == ["ORI", "INCNUM"])
h = kgh.KnowledgeGraphHandler.__new__(kgh.KnowledgeGraphHandler)
txt = h._format_subgraph_for_prompt(three, {}, surface="off", query="miles")
check("the render names the alternatives for a two-edge pair, never a single key",
      "join on ONE of: loyalty.ffp_member.ffp_member_id = loyalty.miles_transfer.receiver_ffp_member_id OR" in txt
      or "join on ONE of:" in txt, txt[txt.find("DECLARED FOREIGN"):txt.find("DECLARED FOREIGN") + 500])
txt1 = h._format_subgraph_for_prompt(out, {}, surface="off", query="miles")
check("…and 'join on (ffp_member_id)' for the single-edge pair", "loyalty.ffp_member ⇄ loyalty.mileage_accrual (FK declared between these tables) — join on (ffp_member_id)" in txt1)

print("[C1b] a shared column that is a FOREIGN key in every table sharing it is a fan-out, not a hint")
fan = {"entities": [
    {"id": 1, "name": "loyalty.ffp_member", "entity_type": "entity", "properties": {"primary_key": "ffp_member_id"}},
    {"id": 2, "name": "finance.accounts_receivable", "entity_type": "entity", "properties": {"primary_key": "accounts_receivable_id"}},
    {"id": 3, "name": "loyalty.ffp_member.ffp_member_id", "entity_type": "attribute", "properties": {"is_primary_key": True}},
    {"id": 4, "name": "loyalty.ffp_member.company_code_id", "entity_type": "attribute", "properties": {"is_foreign_key": True, "semantic_type": "Company Code Id (Foreign Key)"}},
    {"id": 5, "name": "finance.accounts_receivable.company_code_id", "entity_type": "attribute", "properties": {"is_foreign_key": True}},
    {"id": 6, "name": "finance.accounts_receivable.ffp_member_id", "entity_type": "attribute", "properties": {"is_foreign_key": True}},
    {"id": 7, "name": "finance.accounts_receivable.customer_name", "entity_type": "attribute", "properties": {}},
], "relationships": [
    {"relationship_type": "contains", "source_name": "loyalty.ffp_member", "target_name": "loyalty.ffp_member.ffp_member_id", "source_id": 1, "target_id": 3},
    {"relationship_type": "contains", "source_name": "loyalty.ffp_member", "target_name": "loyalty.ffp_member.company_code_id", "source_id": 1, "target_id": 4},
    {"relationship_type": "contains", "source_name": "finance.accounts_receivable", "target_name": "finance.accounts_receivable.company_code_id", "source_id": 2, "target_id": 5},
    {"relationship_type": "contains", "source_name": "finance.accounts_receivable", "target_name": "finance.accounts_receivable.ffp_member_id", "source_id": 2, "target_id": 6},
    {"relationship_type": "contains", "source_name": "finance.accounts_receivable", "target_name": "finance.accounts_receivable.customer_name", "source_id": 2, "target_id": 7},
]}
ftxt = h._format_subgraph_for_prompt(fan, {}, surface="off", query="customers")
_fo_i = ftxt.find("NOT JOIN KEYS (fan-out)")
_jo_i = ftxt.find("JOINABLE COLUMNS")
_fo_block = ftxt[_fo_i:_fo_i + 500] if _fo_i >= 0 else ""
_jo_block = ftxt[_jo_i:_fo_i if _fo_i > _jo_i else _jo_i + 400] if _jo_i >= 0 else ""
check("company_code_id (an FK in BOTH tables) is named as a fan-out, not offered as a hint",
      "  company_code_id: finance.accounts_receivable, loyalty.ffp_member" in _fo_block
      and "company_code_id:" not in _jo_block, (_jo_block, _fo_block))
check("the rule is stated once, then one line per column (no paragraph per column)",
      _fo_block.count("multiplies rows") == 1, _fo_block)
check("ffp_member_id (the pk on one side) stays a legitimate hint",
      "ffp_member_id:" in _jo_block, _jo_block)

print("[C2] the preflight guard checks a same-name key on EACH side of the ON")
joins._reset_all()
SID = "s-cont"
joins.record_join_keys(SID, [{"tables": j["leaves"], "key": j["key"]} for j in jp if j["key"]])
BAD = "SELECT TOP 5 M.ffp_member_id, SUM(A.base_miles) FROM airlines_core.mileage_accrual A JOIN airlines_core.ffp_member M ON A.booking_segment_id = M.ffp_member_id GROUP BY 1"
GOOD = "SELECT TOP 5 M.ffp_member_id, SUM(A.base_miles) FROM airlines_core.mileage_accrual A JOIN airlines_core.ffp_member M ON A.ffp_member_id = M.ffp_member_id GROUP BY 1"
v = joins.join_violations(SID, BAD)
check("the prod turn-1 join (key column on the WRONG side) is a violation", v and v[0]["missing"] == ["ffp_member_id"], v)
check("the declared-key join passes", joins.join_violations(SID, GOOD) == [])
sj = joins.sql_joins(BAD)[0]
check("sql_joins reports the columns per side", sj["by_table"] == {"mileage_accrual": ["booking_segment_id"], "ffp_member": ["ffp_member_id"]}, sj)
check("a CAST on one side stays unprovable (fail-open)", joins.join_violations(SID, GOOD.replace("M.ffp_member_id GROUP", "CAST(M.ffp_member_id AS INTEGER) GROUP")) == [])

print("[D] the previous turn's entities carry into the next turn's seeds")
PREV = {"turn_number": 1, "user_query": "top 5 customers by miles flown",
        "kg_enrichment_event": {"enrichments": [{"entity_names": [
            "loyalty.ffp_member", "loyalty.mileage_accrual", "loyalty.miles_balance", "finance.legal_entity",
            "loyalty.mileage_accrual.base_miles", "loyalty.ffp_member.ffp_member_id", "loyalty.miles_balance.ffp_member_id",
            "loyalty.mileage_accrual.booking_segment_id", "finance.legal_entity.lei_code"],
            "seeds": [{"name": "loyalty.miles_balance"}, {"name": "loyalty.ffp_member"}]}]},
        "conversation_agent_events": [
            {"type": "conversation_tool_invoked", "payload": {"tool_name": "TDA_KnowledgeGraph", "arguments": {"action": "query", "query_text": "miles flown"}}},
            {"type": "conversation_tool_completed", "payload": {"tool_name": "TDA_KnowledgeGraph"}},
            {"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT TOP 5 M.ffp_member_id, SUM(A.base_miles) FROM airlines_core.mileage_accrual A JOIN airlines_core.ffp_member M ON A.booking_segment_id = M.ffp_member_id GROUP BY 1"}}},
            {"type": "conversation_tool_completed", "payload": {"tool_name": "base_readQuery"}}]}
co = kgh.previous_turn_carry_over([PREV])
check("the entities the previous turn's SQL referenced are carried, tables first",
      co and co["source"] == "used" and co["entities"][:2] == ["loyalty.ffp_member", "loyalty.mileage_accrual"], co)
check("descriptive columns the answer projected come before the key columns",
      co and co["entities"].index("loyalty.mileage_accrual.base_miles") < co["entities"].index("loyalty.ffp_member.ffp_member_id"), co)
carried_only = {**PREV, "conversation_agent_events": [],
                "kg_enrichment_event": {"enrichments": [{"entity_names": PREV["kg_enrichment_event"]["enrichments"][0]["entity_names"],
                                                        "carry_over": {"from_turn": 1, "entities": ["loyalty.ffp_member", "loyalty.ffp_member.member_first_name"]}}]}}
co3 = kgh.previous_turn_carry_over([carried_only])
check("a turn that read nothing hands on what it was itself carrying (continuity is transitive)",
      co3 and co3["source"] == "carried" and co3["entities"] == ["loyalty.ffp_member", "loyalty.ffp_member.member_first_name"], co3)
check("an entity the enrichment showed but the SQL never touched is NOT carried (legal_entity)",
      co and not any("legal_entity" in n for n in co["entities"]), co)
check("the origin turn is named", co and co["from_turn"] == 1)
check("bounded", co and len(co["entities"]) <= kgh._CARRY_OVER_LIMIT)
seeded_only = {**PREV, "conversation_agent_events": []}
co2 = kgh.previous_turn_carry_over([seeded_only])
check("a turn that read nothing falls back to its top seeds, saying so", co2 and co2["source"] == "seeded" and co2["entities"] == ["loyalty.miles_balance", "loyalty.ffp_member"], co2)
check("no history → None; a history with no enrichment → None",
      kgh.previous_turn_carry_over([]) is None and kgh.previous_turn_carry_over([{"turn_number": 1}]) is None and kgh.previous_turn_carry_over(None) is None)
check("a session primer is never a topic", kgh.previous_turn_carry_over([PREV, {"turn_number": 2, "is_session_primer": True}]) and kgh.previous_turn_carry_over([{**PREV, "is_session_primer": True}]) is None)
check("an intervening turn without enrichment is skipped, the last usable one wins (bounded lookback)",
      kgh.previous_turn_carry_over([PREV, {"turn_number": 2}])["from_turn"] == 1
      and kgh.previous_turn_carry_over([PREV, {"turn_number": 2}, {"turn_number": 3}, {"turn_number": 4}]) is None)
opt = {"turn_number": 3, "kg_enrichment_event": PREV["kg_enrichment_event"],
       "execution_trace": [{"action": {"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM airlines_core.miles_balance"}}, "result": {}}]}
check("the Optimize trace shape is read too", kgh.previous_turn_carry_over([opt])["entities"][0] == "loyalty.miles_balance")
check("malformed turns never raise", kgh.previous_turn_carry_over([None, "x", {"kg_enrichment_event": "bad", "conversation_agent_events": 3}]) is None)

print("[D2] get_context_enrichment seeds the carried entities ahead of the lexical ranking")
enr_src = inspect.getsource(kgh.KnowledgeGraphHandler.get_context_enrichment)
sig = inspect.signature(kgh.KnowledgeGraphHandler.get_context_enrichment).parameters
check("the enricher accepts session_id and workflow_history", "session_id" in sig and "workflow_history" in sig)
check("a session id resolves the history through the ONE session reader", "get_workflow_history(" in enr_src)
check("carried entities go in front of the ranked seeds, behind the seeded products",
      "entities = entities[:_n_prod] + carried + entities[_n_prod:]" in enr_src)
check("the event carries the carry-over for reload + the card", 'metadata["carry_over"]' in enr_src)
check("a carry-over failure never costs the turn its enrichment (fail-open, WARNING once)",
      "kg-carry-over" in enr_src and "_warn_once" in enr_src)
tf = inspect.getsource(kgh._theatre_facts)
check("the theatre names the channel", '"carry_over" if nm in carried' in tf)

print("[E] the seams thread session_id")
def _calls_in(path, fn):
    tree = ast.parse(pathlib.Path(path).read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            return n
    return None
def _kw_passed(node, callee, kw):
    for c in ast.walk(node):
        if isinstance(c, ast.Call) and (getattr(c.func, "id", "") == callee or getattr(c.func, "attr", "") == callee):
            if any(k.arg == kw for k in c.keywords) or any(isinstance(k.value, ast.Dict) is False and k.arg is None for k in c.keywords):
                return True
    return False
ex = _calls_in(ROOT / "src/trusted_data_agent/agent/executor.py", "_get_kg_enrichment")
check("executor (legacy path) passes session_id", ex is not None and _kw_passed(ex, "get_component_context_enrichment", "session_id"))
cw = _calls_in(ROOT / "components/builtin/context_window/modules/knowledge_graph/handler.py", "contribute")
check("the context-window module passes session_id (signature-gated)", cw is not None and "session_id" in ast.get_source_segment(pathlib.Path(ROOT / "components/builtin/context_window/modules/knowledge_graph/handler.py").read_text(), cw))
mg = pathlib.Path(ROOT / "src/trusted_data_agent/components/manager.py").read_text()
check("the manager forwards session_id only to enrichers that accept it", '"session_id" in _params' in mg and "session_id=session_id" in mg)
gc = _calls_in(ROOT / "src/trusted_data_agent/agent/genie_coordinator.py", "__init__")
check("the coordinator passes its session", "session_id=getattr(self, \"parent_session_id\", None)" in pathlib.Path(ROOT / "src/trusted_data_agent/agent/genie_coordinator.py").read_text())

print("[F] the lookup ranks on evidence and searches terms when the phrase matched only prose")
class Store:
    def __init__(self, entities): self.entities = entities
    async def search_entities(self, q, limit=10, entity_type=None):
        ql = q.lower(); out = []
        for e in self.entities:
            if entity_type and e["entity_type"] != entity_type: continue
            if ql in (e["name"] + " " + str(e.get("properties") or {})).lower(): out.append(e)
        return out[:limit]
    async def get_stats(self): return {"total_entities": len(self.entities), "entity_types": {}}
    async def list_entities(self, entity_type=None, limit=200): return self.entities[:limit]
LOOK = [{"id": 1, "name": "inventory.cabin_class.ffp_accrual_multiplier", "entity_type": "attribute", "properties": {"description": "Multiplier applied to base miles flown to calculate FFP credit"}},
        {"id": 2, "name": "airlines_core.cabin_class.ffp_accrual_multiplier", "entity_type": "column", "properties": {"description": "Multiplier applied to base miles flown to calculate FFP credit"}},
        {"id": 3, "name": "loyalty.mileage_accrual.base_miles", "entity_type": "attribute", "properties": {"description": "Base miles earned on the segment"}},
        {"id": 4, "name": "airlines_core.mileage_accrual.base_miles", "entity_type": "column", "properties": {"description": "Base miles earned on the segment"}},
        {"id": 5, "name": "loyalty.mileage_accrual.total_miles_credited", "entity_type": "attribute", "properties": {"description": "Total miles credited"}},
        {"id": 6, "name": "loyalty.ffp_member", "entity_type": "entity", "properties": {"description": "FFP member master"}}]
hq = kgh.KnowledgeGraphHandler.__new__(kgh.KnowledgeGraphHandler)
hq._get_store = lambda ctx: Store(LOOK)
def look(q): return asyncio.run(hq._handle_query({"query_text": q}, {"user_uuid": "u"}))
r = look("miles flown")
names = [e["name"] for e in r.metadata["entities"]]
check("a prose-only phrase hit also searches the terms (mode says so)", r.metadata["search_mode"] == "phrase+terms", r.metadata["search_mode"])
check("identifier-level matches outrank the prose mention", names.index("loyalty.mileage_accrual.base_miles") < names.index("inventory.cabin_class.ffp_accrual_multiplier"), names)
check("physical twins are collapsed in the lookup too", "airlines_core.mileage_accrual.base_miles" not in names and "airlines_core.cabin_class.ffp_accrual_multiplier" not in names, names)
r2 = look("miles_flown")
n2 = [e["name"] for e in r2.metadata["entities"]]
check("an identifier-shaped phrase splits on its underscores for the term search (the two-term rule keeps its order)",
      "loyalty.mileage_accrual.base_miles" in n2 and r2.metadata["search_mode"] == "terms" and r2.metadata["query_terms"] == ["miles", "flown"], (n2, r2.metadata["query_terms"]))
r3 = look("base_miles")
check("a phrase that NAMES a node stays a plain phrase hit (no term search)", r3.metadata["search_mode"] == "phrase" and [e["name"] for e in r3.metadata["entities"]] == ["loyalty.mileage_accrual.base_miles"], r3.metadata)
check("the card says why more rows appear", "matched only descriptions" in r.html)

print("[R] enrichment recall — the inverse of seed utilization — and the missed telemetry")
from trusted_data_agent.kgstore import usage as _usage
CALLS = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT TOP 5 B.ffp_member_id, C.customer_name FROM airlines_core.boarding_event B JOIN airlines_core.accounts_receivable C ON B.ffp_member_id = C.customer_account_number"}},
         {"tool_name": "TDA_KnowledgeGraph", "arguments": {"action": "query", "query_text": "miles_flown"}},
         {"tool_name": "base_columnDescription", "arguments": {"database_name": "airlines_core", "object_name": "ffp_member"}},
         {"tool_name": "base_readQuery", "arguments": {"sql": "WITH x AS (SELECT * FROM airlines_core.miles_balance) SELECT * FROM x"}}]
check("tables_read: SQL FROM/JOIN + catalog objects, CTE names excluded, TDA_ ignored, deduped, in order",
      _usage.tables_read(CALLS) == [("airlines_core", "boarding_event"), ("airlines_core", "accounts_receivable"), ("airlines_core", "ffp_member"), ("airlines_core", "miles_balance")], _usage.tables_read(CALLS))
SHOWN = ["loyalty.ffp_member", "loyalty.ffp_member.member_first_name", "airlines_core.mileage_accrual.base_miles", "loyalty.miles_balance"]
check("unshown_tables: leaf-matched across logical/deployed names",
      _usage.unshown_tables(SHOWN, CALLS) == ["airlines_core.boarding_event", "airlines_core.accounts_receivable"], _usage.unshown_tables(SHOWN, CALLS))
check("no enrichment ⇒ no miss to attribute", _usage.unshown_tables([], CALLS) == [] and _usage.unshown_tables(SHOWN, []) == [])
check("malformed calls never raise", _usage.unshown_tables(SHOWN, [None, "x", {"tool_name": "base_readQuery", "arguments": "not a dict"}]) == [])
# a logical entity deployed under ANOTHER name (`v_Incident`) is shown under that name too
# (local session 34f5d1b6: every turn that read v_Incident scored a miss on a shown table)
_VE = [{"entity_names": ["Policing Core.Incident", "Policing Core.Offence", "Policing Core.Incident.ori"],
        "entity_facts": {"Policing Core.Incident": {"type": "entity", "deployed": "Policing_Core.v_Incident"},
                         "Policing Core.Offence": {"type": "entity", "deployed": "Policing_Core.v_Offence"}}}]
_VC = [{"tool_name": "base_readQuery", "arguments": {"sql": "SELECT COUNT(*) FROM Policing_Core.v_Incident I JOIN Policing_Core.v_Offence O ON I.ori = O.ori JOIN Policing_Core.v_Agency A ON I.ori = A.ori"}}]
check("deployed_names reads the event's own facts", _usage.deployed_names(_VE) == ["Policing_Core.v_Incident", "Policing_Core.v_Offence"], _usage.deployed_names(_VE))
check("without deployed names the v_ tables read as misses (the old blind spot)",
      _usage.unshown_tables(_VE[0]["entity_names"], _VC) == ["policing_core.v_incident", "policing_core.v_offence", "policing_core.v_agency"])
check("with them, a shown entity's deployed table is not a miss — a really unshown one still is",
      _usage.unshown_tables(_VE[0]["entity_names"], _VC, _usage.deployed_names(_VE)) == ["policing_core.v_agency"],
      _usage.unshown_tables(_VE[0]["entity_names"], _VC, _usage.deployed_names(_VE)))
check("deployed_names tolerates any event shape", _usage.deployed_names([None, {}, {"entity_facts": "x"}, {"entity_facts": {"a": None}}]) == [])
from trusted_data_agent.eval.scorers.reliability import EnrichmentRecallScorer
from trusted_data_agent.eval.types import RunRecord, EvalCase
from trusted_data_agent.eval.scorer import scorers_for
sc = EnrichmentRecallScorer()
def _rr(turn): return RunRecord(turn)
case = EvalCase(id="c", query="q", profile_class="conversation_with_tools")
def evs(calls): return sum(([{"type": "conversation_tool_invoked", "payload": {"tool_name": c["tool_name"], "arguments": c["arguments"]}}, {"type": "conversation_tool_completed", "payload": {"tool_name": c["tool_name"]}}] for c in calls), [])
T = {"kg_enrichment_event": {"enrichments": [{"entity_names": SHOWN}]}, "conversation_agent_events": evs(CALLS)}
v = sc.score(case, _rr(T))
check("scores shown/read over the tables read (2 of 4 shown)", v.score == 0.5 and v.passed is None, (v.score, v.passed, v.explanation))
check("names the misses in the evidence", any("missed: airlines_core.boarding_event" in e for e in v.evidence), v.evidence)
check("abstains with no enrichment (never a pass)", sc.score(case, _rr({"conversation_agent_events": evs(CALLS)})).passed is None and sc.score(case, _rr({"conversation_agent_events": evs(CALLS)})).score is None)
check("abstains when no tables were read", sc.score(case, _rr({"kg_enrichment_event": T["kg_enrichment_event"], "conversation_agent_events": evs([CALLS[1]])})).score is None)
full = {"kg_enrichment_event": {"enrichments": [{"entity_names": SHOWN + ["airport.boarding_event", "finance.accounts_receivable"]}]}, "conversation_agent_events": evs(CALLS)}
check("a fully-shown turn scores 1.0", sc.score(case, _rr(full)).score == 1.0)
_VT = {"kg_enrichment_event": {"enrichments": _VE}, "conversation_agent_events": evs(_VC)}
_vv = sc.score(case, _rr(_VT))
check("the scorer reads the deployed names: 2 of 3 shown (v_Agency the one real miss)",
      round(_vv.score, 3) == 0.667 and any("missed: policing_core.v_agency" in e for e in _vv.evidence), (_vv.score, _vv.evidence))
check("registered for the tool classes only", all(any(x.dimension == "enrichment_recall" for x in scorers_for(c)) for c in ("tool_enabled", "conversation_with_tools"))
      and not any(x.dimension == "enrichment_recall" for x in scorers_for("rag_focused")))
check("non-gating", sc.gating is False)
from trusted_data_agent.agent import assurance_points as AP
from trusted_data_agent.eval import stages as _st
check("owned by trusted_scope (GROUNDED) and staged at RETRIEVE",
      "enrichment_recall" in AP.POINT_DIMENSIONS["trusted_scope"] and _st.stage_of("enrichment_recall") == _st.RETRIEVE and not AP.partition_conflicts())
qd = pathlib.Path(ROOT / "static/js/handlers/qualityDimensions.js").read_text()
check("the Guide knows it (DIM, LONG, INFORMATIONAL, applies-to)",
      "enrichment_recall:    { label:" in qd and "enrichment_recall: 'For KG-grounded" in qd
      and "'enrichment_recall'" in qd.split("const INFORMATIONAL = new Set([")[1].split("])")[0]
      and "enrichment_recall:       ['conversation_with_tools', 'tool_enabled']" in qd)
ring = pathlib.Path(ROOT / "static/js/handlers/agentPerformanceHandler.js").read_text()
check("the ring's STATION_DIMS.grounded mirrors it", "'enrichment_recall'" in ring.split("const STATION_DIMS")[1].split("};")[0])
# the missed telemetry: recorded on the graph's own table entity, never on a table the graph lacks
import tempfile as _tf, sqlite3 as _sq
_udb = _tf.NamedTemporaryFile(suffix=".db", delete=False).name
_orig = _usage._db_path; _usage._db_path = lambda: _udb
try:
    _usage.record_missed_async("kgX", SHOWN, CALLS, ["loyalty.ffp_member", "airport.boarding_event", "loyalty.miles_balance"])
    import time as _time; _time.sleep(0.5)
    st = _usage.stats("kgX")
    check("missed recorded on the graph's boarding_event entity (leaf match), not on accounts_receivable (graph lacks it)",
          st.get("airport.boarding_event", {}).get("missed") == 1 and "finance.accounts_receivable" not in st, st)
    pri = _usage.priors("kgX")
    check("a missed table carries the strongest prior", pri.get("airport.boarding_event") == 1.0, pri)
finally:
    _usage._db_path = _orig
ex_src = inspect.getsource(kgh) if False else pathlib.Path(ROOT / "src/trusted_data_agent/agent/executor.py").read_text()
_seam = ex_src.split("def _record_kg_used")[1].split("def _run_obda_scan")[0]
check("the executor records misses beside `used` at the one telemetry seam, with the deployed names",
      "self._record_kg_missed(kid, names, tool_calls,\n                                           _usage.deployed_names([e]))" in _seam
      and "_usage.unshown_tables(shown_names, tool_calls, deployed)" in _seam
      and "deployed_table" in _seam)
try:
    _usage._db_path = lambda: _udb
    _usage.record_missed_async("kgV", _VE[0]["entity_names"], _VC,
                               ["Policing Core.Incident", "Policing Core.Agency"], _usage.deployed_names(_VE),
                               {"Policing Core.Incident": "Policing_Core.v_Incident", "Policing Core.Agency": "Policing_Core.v_Agency"})
    _time.sleep(0.5)
    _sv = _usage.stats("kgV")
    check("missed telemetry: a shown entity's deployed table is never bumped as missed",
          "Policing Core.Incident" not in _sv, _sv)
    check("missed telemetry: a REAL miss lands on the logical entity through its deployed name (v_Agency → Agency)",
          _sv.get("Policing Core.Agency", {}).get("missed") == 1, _sv)
finally:
    _usage._db_path = _orig

print("[S] the FK-holder fan-out is named as a rule and handed to the guard")
_ents = [{"id": 1, "name": "loyalty.ffp_member", "entity_type": "entity", "properties": {"deployed_table": "airlines_core.ffp_member"}},
         {"id": 2, "name": "loyalty.mileage_redemption", "entity_type": "entity", "properties": {"deployed_table": "airlines_core.mileage_redemption"}},
         {"id": 3, "name": "loyalty.member_benefit", "entity_type": "entity", "properties": {"deployed_table": "airlines_core.member_benefit"}},
         {"id": 11, "name": "loyalty.ffp_member.ffp_member_id", "entity_type": "attribute", "properties": {}},
         {"id": 12, "name": "loyalty.mileage_redemption.ffp_member_id", "entity_type": "attribute", "properties": {}},
         {"id": 13, "name": "loyalty.member_benefit.ffp_member_id", "entity_type": "attribute", "properties": {}},
         {"id": 14, "name": "loyalty.mileage_redemption.general_ledger_id", "entity_type": "attribute", "properties": {}}]
_rels = [{"source_id": 12, "target_id": 11, "source_name": "loyalty.mileage_redemption.ffp_member_id", "target_name": "loyalty.ffp_member.ffp_member_id", "relationship_type": "foreign_key"},
         {"source_id": 13, "target_id": 11, "source_name": "loyalty.member_benefit.ffp_member_id", "target_name": "loyalty.ffp_member.ffp_member_id", "relationship_type": "foreign_key"},
         {"source_id": 2, "target_id": 12, "source_name": "loyalty.mileage_redemption", "target_name": "loyalty.mileage_redemption.ffp_member_id", "relationship_type": "contains"}]
_fo = kgh._fanout_keys({"entities": _ents, "relationships": _rels})
check("two FK holders of one owner's key → ONE fan-out key naming column, owner (deployed leaf) and both holders",
      len(_fo) == 1 and _fo[0]["column"] == "ffp_member_id" and _fo[0]["owner"] == "ffp_member"
      and _fo[0]["tables"] == ["member_benefit", "mileage_redemption"], _fo)
_one = kgh._fanout_keys({"entities": _ents, "relationships": _rels[:1] + _rels[2:]})
check("a key ONE table holds as a foreign key is not a fan-out (that is the ordinary join)", _one == [], _one)
check("endpoint types recovered from the entities when the edge carries none (the column-edge graph shape)",
      kgh._fanout_keys({"entities": _ents, "relationships": [dict(r, source_type=None, target_type=None) for r in _rels]}) == _fo)
# A 1:1 extension table — its declared PRIMARY key IS the foreign-key column — holds at most
# one row per key value, so a join to it on that key cannot multiply. It is not a holder;
# with it excluded the key has one holder left and is no fan-out at all. A composite key
# that merely CONTAINS the column still admits many rows and keeps the table a holder.
_ext = [dict(e, properties=dict(e["properties"], primary_key="ffp_member_id")) if e["id"] == 3 else e for e in _ents]
_ext_fo = kgh._fanout_keys({"entities": _ext, "relationships": _rels})
check("a holder whose own single-column PK is the column is not a holder (1:1 extension) → no fan-out remains",
      _ext_fo == [], _ext_fo)
_comp = [dict(e, properties=dict(e["properties"], primary_key="ffp_member_id, member_benefit_id")) if e["id"] == 3 else e for e in _ents]
_comp_fo = kgh._fanout_keys({"entities": _comp, "relationships": _rels})
check("a composite PK containing the column keeps the table a holder (many rows per key are still possible)",
      _comp_fo == _fo, _comp_fo)
_other = [dict(e, properties=dict(e["properties"], primary_key="member_benefit_id")) if e["id"] == 3 else e for e in _ents]
check("a surrogate PK (the real airlines shape) keeps the table a holder", kgh._fanout_keys({"entities": _other, "relationships": _rels}) == _fo)
_hs = pathlib.Path(ROOT / "components/builtin/knowledge_graph/handler.py").read_text()
_md = _hs.split('metadata["fanout_keys"] = ', 1)
check("the metadata the guard reads is DERIVED from the same _fanout_keys computation the renderer uses — never a second list",
      len(_md) == 2 and "_fanout_keys(subgraph)" in _md[1][:300]
      and "enr.get(\"fanout_keys\")" in pathlib.Path(ROOT / "src/trusted_data_agent/agent/preflight/joins.py").read_text(), _md[1][:120] if len(_md) == 2 else None)

print("[G] the chat's table interception cannot lose a table")
ui = pathlib.Path(ROOT / "static/js/ui.js").read_text()
check("the wrapper id carries a monotonic sequence (unique across messages in one tick)",
      "const wrapperId = `dash-tbl-${Date.now()}-${idx}-${++_dashTableSeq}`;" in ui and "let _dashTableSeq = 0;" in ui)
check("a wrapper the renderer left without a card gives the table back",
      "_tableHome.replaceChild(table, wrapper);" in ui and "!wrapper.querySelector('.dash-viz-card')" in ui)
check("the Live Status card + title name the carried entities", "Carried over" in ui and "carried from the previous turn" in ui)

print("[H] the carry speaks the logical model (local session 34f5d1b6, turn 3)")
_facts = {"Policing Core.Incident": {"type": "entity", "deployed": "Policing_Core.v_Incident"},
          "Policing Core.Offence": {"type": "entity", "deployed": "Policing_Core.v_Offence"},
          "Policing Core.Property": {"type": "entity", "deployed": "Policing_Core.v_Property"}}
_names = ["Policing Core.Incident", "Policing Core.Offence", "Policing Core.Property",
          "Policing Core.Incident.ori", "Policing Core.Incident.incident_date_text",
          "Policing Core.Offence.offence_code", "Policing Core.Property.ori", "Policing Core.Property.incident_number"]
_sql = ("SELECT SUBSTR(I.incident_date_text, 4, 3) AS m, COUNT(*) FROM Policing_Core.v_Incident I "
        "JOIN Policing_Core.v_Offence O ON I.ori = O.ori AND I.incident_number = O.incident_number "
        "WHERE O.offence_code = '120' GROUP BY m")
_vprev = {"turn_number": 2, "kg_enrichment_event": {"enrichments": [{"entity_names": _names, "entity_facts": _facts}]},
          "conversation_agent_events": [{"type": "conversation_tool_invoked", "payload": {"tool_name": "base_readQuery", "arguments": {"sql": _sql}}}]}
_vc = kgh.previous_turn_carry_over([_vprev])
check("a deployed table read is carried as its LOGICAL entity, never a physical twin",
      _vc and "Policing Core.Incident" in _vc["entities"] and "Policing Core.Offence" in _vc["entities"]
      and not any("v_" in x for x in _vc["entities"]), _vc)
check("a deployed table read is not an enrichment miss when its logical entity was shown", _vc and _vc["missed"] == [], _vc)
check("a shared column leaf of a table the turn never read is not carried",
      _vc and not any(x.startswith("Policing Core.Property") for x in _vc["entities"]), _vc)
check("a column of a table it did read stays", _vc and "Policing Core.Incident.incident_date_text" in _vc["entities"], _vc)
_old = {**_vprev, "kg_enrichment_event": {"enrichments": [{"entity_names": _names}]}}
check("an older event without facts is carried as before", kgh.previous_turn_carry_over([_old]) is not None)

print(f"\n=== KG conversation continuity: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
