"""
CI-pure tests for KG enrichment relevance and the governance payload.

Both behaviours here were live failures, observed in one turn:

    SELECT COUNT(*) FROM NIBRS_Source_Domain.Victim
    WHERE V40191 = 'F' OR V40192 = 'F' OR V40193 = 'F'

`V40191/2/3` are REAL columns — "SEX OF VICTIM-1/2/3" — but they belong to
Arrestee/Incident, not Victim. The agent didn't hallucinate; the enrichment handed
it a swarm of near-misses and never surfaced `Victim.V4019`, and it carried no
decoded values, so `= 'F'` was the only guess available.

Run: python3 test/test_kg_enrichment_relevance.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)

# The data-product contract assertions below exercise an OPT-IN feature: the OBDA-READ
# master switch defaults to False (Aug 2026), and with it off the enrichment renders
# no product contracts at all — by design.  A gate for a feature must turn the
# feature on, or it asserts the disabled path forever and reads as 7 failures.
from trusted_data_agent.core.config import APP_CONFIG  # noqa: E402

_OP.force("read", True)

from components.builtin.knowledge_graph.handler import (  # noqa: E402
    _RESERVED_SEED_SLOTS,
    _is_char_type,
    _format_codes,
    _governance_parts,
    _match_score,
    _query_terms,
    _rank_seeds,
    _scoring_terms,
    _singularize,
)

PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")


SEX_CODES = [
    {"value": "-9", "label": "Undetermined"}, {"value": "-6", "label": "Not applicable"},
    {"value": "0", "label": "Female"}, {"value": "1", "label": "Male"},
]


def attr(name, desc, codes=None, pii=False, cls=None):
    p = {"label": name.rpartition(".")[2], "description": desc}
    if codes:
        p["allowed_values"] = codes
    if pii:
        p["is_pii"] = True
    if cls:
        p["data_classification"] = cls
    return {"name": name, "entity_type": "attribute", "properties": p}


# ── 1. decoded values reach the prompt ──────────────────────────────────────
def t_codes_rendered():
    """A coded column without its codes is unanswerable except by luck."""
    check("codes render as value=label",
          _format_codes(SEX_CODES) == "-9=Undetermined, -6=Not applicable, 0=Female, 1=Male")
    # The PROPERTY, not the literal: this once pinned the exact marker string and broke
    # when the marker gained the action that resolves it, while everything it protects
    # still held. Two obligations now — say how many were withheld, and say how to get
    # them — because a truncation the agent cannot resolve is a dead end (it sent a live
    # agent to a document for the codes, which had different ones).
    _bounded = _format_codes(SEX_CODES, limit=2)
    _values = _bounded.split(" (+")[0]          # the marker carries its own '=' signs
    check("bounded to the limit", _values.count("=") == 2, _values)
    check("keeps the values in order", _bounded.startswith("-9=Undetermined, -6=Not applicable"), _bounded)
    check("says how many were withheld", "+2 more" in _bounded, _bounded)
    check("and names the way to get them", "TDA_KnowledgeGraph" in _bounded, _bounded)
    check("a complete render has no marker", "more" not in _format_codes(SEX_CODES, limit=None))
    for bad in (None, [], "notalist", [{"nope": 1}]):
        check(f"degrades on {type(bad).__name__}", _format_codes(bad) == "")

    parts = _governance_parts({"allowed_values": SEX_CODES, "is_pii": True,
                               "data_classification": "CONFIDENTIAL"})
    check("values are surfaced", any(p.startswith("values: ") and "0=Female" in p for p in parts))
    check("PII is surfaced", "PII" in parts)
    check("classification is surfaced", "CONFIDENTIAL" in parts)
    check("PUBLIC is not noise",
          "PUBLIC" not in _governance_parts({"data_classification": "PUBLIC"}))
    check("a plain column adds nothing", _governance_parts({"description": "x"}) == [])


# ── 2. the owning entity is the decisive signal ─────────────────────────────
def t_owner_beats_lookalike():
    """The live failure: identical text on two tables, only the owner distinguishes."""
    terms = _query_terms("How many victims are female?")
    check("stop words removed", terms == ["victims", "female"], str(terms))

    right = attr("NIBRS_Source.Victim.V4019",
                 "SEX OF VICTIM - V4019 indicates the sex of a victim.", SEX_CODES)
    wrong = attr("NIBRS_Source.Arrestee.V40191",
                 "SEX OF VICTIM-1 - V40191 indicates the sex of the victim.", SEX_CODES)
    check("the column of the asked-about entity outranks its look-alike",
          _match_score(right, terms) > _match_score(wrong, terms),
          f"right={_match_score(right, terms)} wrong={_match_score(wrong, terms)}")

    ranked = _rank_seeds([wrong, wrong, right, wrong], "How many victims are female?", limit=3)
    check("it is ranked first, not lost to arrival order",
          ranked[0]["name"] == "NIBRS_Source.Victim.V4019", ranked[0]["name"])


def t_incidental_mention_is_weak():
    """A column whose prose merely mentions the word must not crowd out a real match."""
    terms = _query_terms("How many victims are female?")
    passing = attr("NIBRS_Source.Arrestee.V40423",
                   "RELATIONSHIP VIC TO OFF 6-3 - relationship of victim to offender.")
    real = attr("NIBRS_Source.Victim.V4019", "SEX OF VICTIM.", SEX_CODES)
    check("an incidental mention scores far lower",
          _match_score(real, terms) > _match_score(passing, terms) + 30,
          f"real={_match_score(real, terms)} incidental={_match_score(passing, terms)}")


def t_value_labels_are_evidence():
    """'female' identifies a sex column through its decoded values, not its name."""
    terms = _query_terms("how many are female")
    coded = attr("X.T.C1", "some column", SEX_CODES)
    plain = attr("X.T.C2", "some column")
    check("a decoded value carrying the term scores higher",
          _match_score(coded, terms) > _match_score(plain, terms))


def t_exact_name_match_wins():
    terms = _query_terms("what is V4019")
    exact = attr("NIBRS_Source.Victim.V4019", "sex")
    prefix = attr("NIBRS_Source.Arrestee.V40191", "sex")
    check("an exact identifier match beats a prefix",
          _match_score(exact, terms) > _match_score(prefix, terms),
          f"exact={_match_score(exact, terms)} prefix={_match_score(prefix, terms)}")


# ── 3. ranking is stable and bounded ────────────────────────────────────────
def t_ranking_contract():
    terms_q = "orders"
    items = [{"name": f"X.E{i}", "entity_type": "entity", "properties": {}} for i in range(20)]
    check("limit respected", len(_rank_seeds(items, terms_q, limit=5)) == 5)
    check("empty input is safe", _rank_seeds([], terms_q) == [])
    check("no query still returns seeds", len(_rank_seeds(items, "", limit=4)) == 4)

    ent = {"name": "X.Order", "entity_type": "entity", "properties": {"label": "Order"}}
    col = attr("X.Other.note", "mentions order in passing")
    ranked = _rank_seeds([col, ent], "show me orders", limit=2)
    check("a matching entity outranks a passing mention in a field",
          ranked[0]["entity_type"] == "entity", ranked[0]["name"])

    a = {"name": "X.A", "entity_type": "entity", "properties": {}}
    b = {"name": "X.B", "entity_type": "entity", "properties": {}}
    check("equal scores keep arrival order (stable)",
          [e["name"] for e in _rank_seeds([a, b], "zzz", limit=2)] == ["X.A", "X.B"])


# ── 3b. the deployed type decides how a code is compared ────────────────────
def t_char_column_codes_are_quoted():
    """The most expensive silent failure this graph can prevent.

    `V4019` declares INTEGER and is deployed as VARCHAR(2). `WHERE V4019 = 0` makes
    the database CAST the column, and a blank casts to zero — so "how many victims
    are female?" answered 900,719 where the truth is 477,466, the difference being
    423,253 blank rows. Nothing errored and the number looked plausible; only a
    count against the real column exposed it.

    The agent copies the form it is shown, so a character column's codes are shown
    quoted and the caveat is stated outright.
    """
    for t in ("VARCHAR(2)", "CV", "CHAR(1)", "varchar(10)", "CLOB"):
        check(f"{t} is a character type", _is_char_type(t) is True, t)
    for t in ("INTEGER", "DECIMAL(5,2)", "DATE", "", None, "I"):
        check(f"{t!r} is not a character type", _is_char_type(t) is False, repr(t))

    char = {"allowed_values": SEX_CODES, "deployed_type": "VARCHAR(2)"}
    parts = _governance_parts(char)
    vals = next((p for p in parts if p.startswith("values: ")), "")
    check("codes are quoted for a character column", "'0'=Female" in vals, vals)
    check("the numeric-compare trap is stated",
          any("compare as strings" in p and "blank" in p for p in parts), str(parts))
    check("the deployed type is named", any("VARCHAR(2)" in p for p in parts))

    num = _governance_parts({"allowed_values": SEX_CODES, "deployed_type": "INTEGER"})
    nvals = next((p for p in num if p.startswith("values: ")), "")
    check("a numeric column is NOT quoted", "0=Female" in nvals and "'0'" not in nvals, nvals)
    check("and carries no string caveat",
          not any("compare as strings" in p for p in num), str(num))

    # A graph built before the walker stamped deployed_type must not gain a false
    # caveat — absent evidence is not evidence of a numeric column either way.
    old = _governance_parts({"allowed_values": SEX_CODES})
    check("no deployed_type -> unchanged, no invented caveat",
          "0=Female" in next((p for p in old if p.startswith("values: ")), "")
          and not any("compare as strings" in p for p in old), str(old))

    check("a char column with no codes says nothing about comparison",
          _governance_parts({"deployed_type": "VARCHAR(2)"}) == [])


# ── 4. plural questions against singular names ──────────────────────────────
def t_singularize():
    """People ask in the plural; entities are named in the singular."""
    for plural, want in (("investigations", "investigation"), ("bridges", "bridge"),
                         ("incidents", "incident"), ("agencies", "agency"),
                         ("matches", "match"), ("boxes", "box")):
        check(f"{plural} -> {want}", _singularize(plural) == want, repr(_singularize(plural)))
    for singular in ("victim", "class", "is", "bus", "address"):
        check(f"{singular} is left alone", _singularize(singular) is None,
              repr(_singularize(singular)))


def t_scoring_terms_cover_both_forms():
    """The pool and the ranking must speak the same language.

    Teaching only the SEARCH to singularize put bridges into the candidate set and
    then scored them zero, so they lost the tie and stayed invisible — a fix to one
    half of a two-stage pipeline is not a fix.
    """
    terms = _scoring_terms("show me the product bridges")
    check("keeps the asked form", "bridges" in terms, str(terms))
    check("adds the singular", "bridge" in terms, str(terms))
    check("no duplicates", len(terms) == len(set(terms)), str(terms))

    node = {"name": "CASE_Source.bridge:Investigation To Incident",
            "entity_type": "product_bridge",
            "properties": {"label": "bridge:Investigation To Incident"}}
    check("a plural query now scores the singular name",
          _match_score(node, terms) > 0, str(_match_score(node, terms)))
    check("the plural alone would not have",
          _match_score(node, ["bridges"]) == 0)


# ── 5. a declared bridge is never crowded out ───────────────────────────────
def _bridge(n=1):
    return {"name": f"CASE_Source.bridge:Investigation To Incident {n}",
            "entity_type": "product_bridge",
            "properties": {"label": f"bridge:Investigation To Incident {n}"}}


def t_matched_bridge_survives_the_cut():
    """Observed live: the bridge scored 100 and a dozen BH04x columns at 130 buried it.

    Lexical similarity is the wrong judge — the bridge is the standing caveat about
    whether two products may be joined at all.
    """
    q = "can I join CASE investigations to NIBRS incidents"
    # 15 high-scoring look-alikes, all outranking the bridge
    fillers = [{"name": f"NIBRS_Source.Incident.BH{i:03d}",
                "entity_type": "attribute",
                "properties": {"label": f"BH{i:03d}",
                               "description": "incident investigations incident"}}
               for i in range(20)]
    ranked = _rank_seeds(fillers + [_bridge()], q, limit=15)
    types = [e["entity_type"] for e in ranked]
    check("the bridge is kept despite being outranked",
          "product_bridge" in types, str(types[:4]))
    check("the seed budget is still respected", len(ranked) == 15, str(len(ranked)))
    check("it did not displace everything",
          types.count("attribute") >= 12, str(types.count("attribute")))


def t_unmatched_bridge_is_not_forced_in():
    """On a single-product question a bridge is noise, and noise trains people to skim."""
    q = "how many victims are female"
    fillers = [{"name": f"NIBRS_Source.Victim.V{i}", "entity_type": "attribute",
                "properties": {"label": f"V{i}", "description": "sex of victim female"}}
               for i in range(20)]
    ranked = _rank_seeds(fillers + [_bridge()], q, limit=15)
    check("an unmatched bridge stays out",
          all(e["entity_type"] != "product_bridge" for e in ranked))


def t_reserved_slots_are_bounded():
    q = "bridges"
    many = [_bridge(i) for i in range(10)]
    ranked = _rank_seeds(many, q, limit=3)
    check("limit still holds with many bridges", len(ranked) == 3, str(len(ranked)))
    fillers = [{"name": f"X.T.C{i}", "entity_type": "attribute",
                "properties": {"label": f"C{i}", "description": "bridges"}}
               for i in range(30)]
    ranked = _rank_seeds(fillers + many, "bridges", limit=15)
    n = sum(1 for e in ranked if e["entity_type"] == "product_bridge")
    check("bridges cannot take over the seed set",
          n <= max(_RESERVED_SEED_SLOTS, 4), str(n))
    check("empty input is still safe", _rank_seeds([], q) == [])



# ── OBDA-READ C4: a data product renders as a CONSUMPTION CONTRACT, not a bare node ──
def t_data_product_renders_as_contract():
    """Live C4 failure: the product listed as a plain node → the planner planned raw
    SQL 'to query the data product'. The consumption instruction is deterministic,
    so it is rendered in code — the planner meets the rule while planning."""
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as H
    fmt = lambda ents: H._format_subgraph_for_prompt(  # noqa: E731
        H.__new__(H), {"entities": ents, "relationships": []}, None)
    product = {"name": "fitness_sales", "entity_type": "data_product", "properties": {
        "description": "Sales with customer geography",
        "manifest": {"product": "fitness_sales",
                     "entities": {"Sales": {}, "Customers": {}},
                     "measures": {"total_revenue": {}, "sale_count": {}}},
        "validation_report": {"status": "verified_deterministic"}}}
    txt = fmt([product])
    check("products get their own contract block", "SIGNED DATA PRODUCTS" in txt, txt[:200])
    check("the instruction names query_product", "query_product" in txt)
    # The wording moved from a bare prohibition to one stated against the ALTERNATIVE:
    # the same enrichment also shows the underlying tables, and "use query_product"
    # alone left the model free to read those schemas as an equally valid route
    # (observed: "how many customers" planned as COUNT(CustomerID) FROM Customers with
    # the contract present). Assert the PROPERTY, not the old phrasing.
    check("hand-written SQL over the same tables is forbidden",
          "Do NOT hand-write SQL" in txt and "NOT an alternative route" in txt, txt[:300])
    check("measures are listed (the slice vocabulary)", "total_revenue" in txt and "sale_count" in txt)
    check("entities are listed", "Customers" in txt and "Sales" in txt)
    check("a validated product says so", "validated" in txt)
    unval = {**product, "properties": {**product["properties"], "validation_report": None}}
    check("an unvalidated product is not dressed as validated",
          "NOT VALIDATED" in fmt([unval]))
    plain = {"name": "Sales", "entity_type": "entity", "properties": {}}
    check("no product in the subgraph → no block (additive)",
          "SIGNED DATA PRODUCTS" not in fmt([plain]))



# ── v2 consumer surfaces must reach the PROMPT, not just the graph ──────────────

def t_v2_surfaces_reach_the_prompt():
    """The failure this repo has already paid for once, in the same formatter.

    `allowed_values` sat on 1,349 attributes and appeared ZERO times in the
    rendered prompt, so a coded column read as an opaque identifier.  The v2
    surfaces arrived the same way: `consumable_object` and `trust_status` were
    stamped on the nodes and rendered nowhere, and the agent duly queried the base
    table and never mentioned the product's verdict.  A fact in the graph that
    never reaches the prompt is not guidance.
    """
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as H
    sub = {"entities": [
        {"name": "P", "entity_type": "database",
         "properties": {"label": "P", "trust_status": "DEGRADED",
                        "agent_use_allowed": True}},
        {"name": "P.Victim", "entity_type": "entity",
         "properties": {"label": "Victim",
                        "consumable_object": "P_Domain.Victim_NoPII",
                        "access_note": "carries PII-classified columns"}},
    ], "relationships": []}
    out = H._format_subgraph_for_prompt(H, sub) if not isinstance(
        H._format_subgraph_for_prompt, staticmethod) else H._format_subgraph_for_prompt(sub)
    check("the consumable object reaches the prompt",
          "P_Domain.Victim_NoPII" in out, out[:300])
    check("it is stated as an instruction, not a footnote",
          "QUERY THIS OBJECT" in out, out[:300])
    check("the access note travels with it",
          "PII-classified" in out, out[:300])
    check("the product's verdict reaches the prompt",
          "DEGRADED" in out, out[:300])
    check("a degraded product is told to surface the degradation",
          "surface the degradation" in out, out[:300])


def t_deployed_identifier_leads_the_entity_line():
    """An industry model's logical entity is named `<domain>.<table>` — which reads like
    `<database>.<table>`. Live (manufacturing validation turn): with the deployed
    identifier buried after the description, the agent wrote
    `FROM production.routing_operation` (Error 3802) before correcting itself. The
    identifier to query must LEAD the line, and the block must say what a qualified
    logical name is."""
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as H
    from components.builtin.knowledge_graph.resolver import EntityCapabilities
    sub = {"entities": [
        {"name": "production.routing_operation", "entity_type": "entity",
         "properties": {"label": "routing_operation", "layer": "logical",
                        "description": "A single step of a routing: the operation performed at a work "
                                       "center, its sequence, standard times and the resources it needs."}},
    ], "relationships": []}
    caps = {"production.routing_operation": EntityCapabilities(deployed_as="manufacturing_core.routing_operation",
                                                              infrastructure="teradata")}
    out = H._format_subgraph_for_prompt(H.__new__(H), sub, caps)
    line = next(l for l in out.splitlines() if l.strip().startswith("- production.routing_operation"))
    check("the deployed identifier LEADS the parenthesis, before the description",
          line.index("deployed: manufacturing_core.routing_operation") < line.index("A single step"), line[:160])
    check("the ENTITY block says a <domain>.<table> name is not a database",
          "is NOT a database" in out and "'deployed:' identifier" in out, out[:300])
    # a graph with no deployed layer (or unqualified names) keeps the plain header
    plain = H._format_subgraph_for_prompt(H.__new__(H), {"entities": [
        {"name": "Customers", "entity_type": "entity", "properties": {"description": "x"}}], "relationships": []}, {})
    check("no note when nothing is deployed / names are unqualified", "is NOT a database" not in plain)
    # TABLE SCHEMAS: the line the model reads columns from.  Rendered whole, thirty
    # `production.routing_operation.<col>` names against one [deployed: …] tag taught
    # the model the wrong FROM clause.  The deployed identifier is the table, columns
    # are bare, the logical name is a label.
    sub2 = {"entities": [
        {"name": "manufacturing_core", "entity_type": "database", "properties": {}},
        {"name": "production", "entity_type": "domain", "properties": {}},
        {"name": "production.routing_operation", "entity_type": "entity", "properties": {"description": "d"}},
        {"name": "production.routing_operation.routing_operation_id", "entity_type": "attribute", "properties": {"data_type": "BIGINT"}},
        {"name": "production.routing_operation.sequence_number", "entity_type": "attribute", "properties": {"data_type": "INT"}},
        {"name": "Customers", "entity_type": "entity", "properties": {"description": "no deployed layer"}},
        {"name": "Customers.CustomerID", "entity_type": "attribute", "properties": {"data_type": "INTEGER"}},
    ], "relationships": [
        {"source_name": "manufacturing_core", "target_name": "production", "relationship_type": "contains", "source_type": "database", "target_type": "domain"},
        {"source_name": "production", "target_name": "production.routing_operation", "relationship_type": "contains", "source_type": "domain", "target_type": "entity"},
        {"source_name": "production.routing_operation", "target_name": "production.routing_operation.routing_operation_id", "relationship_type": "contains", "source_type": "entity", "target_type": "attribute"},
        {"source_name": "production.routing_operation", "target_name": "production.routing_operation.sequence_number", "relationship_type": "contains", "source_type": "entity", "target_type": "attribute"},
        {"source_name": "Customers", "target_name": "Customers.CustomerID", "relationship_type": "contains", "source_type": "entity", "target_type": "attribute"},
    ]}
    out2 = H._format_subgraph_for_prompt(H.__new__(H), sub2, caps)
    sch = out2[out2.index("TABLE SCHEMAS"):]
    sch = sch[:sch.find("\n\n")] if "\n\n" in sch else sch
    rl = next(l for l in sch.splitlines() if "routing_operation" in l)
    check("schema line is headed by the DEPLOYED identifier", rl.strip().startswith("manufacturing_core.routing_operation"), rl[:120])
    check("columns are bare (no `production.routing_operation.` prefix repeated per column)",
          "routing_operation_id(BIGINT)" in rl and "production.routing_operation.routing_operation_id" not in rl, rl[:200])
    check("the logical name survives as a label", "logical: production.routing_operation" in rl, rl[:160])
    cl = next(l for l in sch.splitlines() if "Customers" in l)
    check("a table with no deployed identifier keeps its own name + bare columns",
          cl.strip().startswith("Customers") and "CustomerID(INTEGER)" in cl and "logical:" not in cl, cl[:120])


def t_unvalidated_product_asserts_nothing():
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as H
    sub = {"entities": [{"name": "P", "entity_type": "database",
                         "properties": {"label": "P"}}], "relationships": []}
    out = H._format_subgraph_for_prompt(H, sub) if not isinstance(
        H._format_subgraph_for_prompt, staticmethod) else H._format_subgraph_for_prompt(sub)
    check("a product with no verdict claims none",
          "product validation" not in out, out[:200])



# ── The render drops what the graph holds (Aug 2026, found live on prod) ─────────
#
# Three failures of ONE shape, each a fact the graph carried and the renderer
# discarded, each measured on the deployed policing estate:
#
#   A  "How many victims are female?" answered 16,110 from `Victim.V60152`
#      (SEX OF ARRESTEE-2) instead of 477,466 from `Victim.V4019` (SEX OF VICTIM).
#      The SEEDER was correct — V4019 ranked first, 150 vs 145 — but the coded block
#      rendered bare `Table.Column: values: '0'=Female` lines in arrival order, so
#      five indistinguishable columns appeared and the first one won.
#   B  A chain-of-custody question was pointed at `CASE_Source_Domain.provenance`
#      while the entity declared `deployed_view = v_chain_of_custody`.
#   C  The published recipe that answers that question ranked FIRST among seeds and
#      rendered as its own question restated, with its SQL dropped.
#
# The fixes are all RENDER fixes on purpose: the consumer skill already says "query
# the object the product points you at" and "prefer adapting a recipe", and both
# session primers already say the same. A skill cannot name an object the enrichment
# never showed — which is why none of this may be answered with prompt text.

def _render(sub, **kw):
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as H
    return H._format_subgraph_for_prompt(H, sub, **kw)


def _sex_columns_subgraph():
    def col(name, desc, label):
        return {"name": name, "entity_type": "attribute",
                "properties": {"label": label, "description": desc,
                               "deployed_type": "VARCHAR(2)", "is_pii": True,
                               "data_classification": "CONFIDENTIAL",
                               "allowed_values": [{"value": "0", "label": "Female"},
                                                  {"value": "1", "label": "Male"}]}}
    ents = [{"name": "P.Victim", "entity_type": "entity", "properties": {"label": "Victim"}},
            col("P.Victim.V60152", "SEX OF ARRESTEE - 2 - V60152 is the Sex of Arrestee.", "V60152"),
            col("P.Victim.V50083", "SEX OF OFFENDER - 3 - V50083 is the Sex of Offender.", "V50083"),
            col("P.Victim.V4019", "SEX OF VICTIM - V4019 indicates the sex of the victim.", "V4019")]
    rels = [{"relationship_type": "contains", "source_name": "P.Victim",
             "target_name": c["name"], "source_type": "entity", "target_type": "attribute"}
            for c in ents[1:]]
    return {"entities": ents, "relationships": rels}


def t_coded_columns_carry_their_discriminator():
    out = _render(_sex_columns_subgraph(), query="how many victims are female?")
    check("the coded block names SEX OF VICTIM", "SEX OF VICTIM" in out, out[:400])
    check("and distinguishes the arrestee column", "SEX OF ARRESTEE - 2" in out, out[:400])
    # the ordinal must survive: arrestee 1/2/3 are three different columns
    check("the decoded values still render", "'0'=Female" in out, out[:400])


def t_coded_columns_are_ordered_by_relevance():
    out = _render(_sex_columns_subgraph(), query="how many victims are female?")
    # Resolved from the module, never spelled here: this assertion protects the ORDER,
    # and a hardcoded title makes a rename look like a regression (it did once).
    from components.builtin.knowledge_graph.handler import RELEVANT_COLUMNS_HEADING
    check("the heading is single-sourced", RELEVANT_COLUMNS_HEADING in out, out[:200])
    blk = out.split(RELEVANT_COLUMNS_HEADING)[-1]
    i_right, i_wrong = blk.find("V4019"), blk.find("V60152")
    check("the column that answers the question leads the coded block",
          i_right != -1 and i_wrong != -1 and i_right < i_wrong,
          f"V4019 at {i_right}, V60152 at {i_wrong}")


def t_coded_order_is_stable_without_a_query():
    # No query terms ⇒ arrival order, byte-identical to the pre-fix behaviour.
    sub = _sex_columns_subgraph()
    out = _render(sub)
    blk = out.split("CODED / SENSITIVE COLUMNS")[-1]
    check("with no question asked the block keeps arrival order",
          blk.find("V60152") < blk.find("V4019"), blk[:300])


def t_declared_view_is_offered():
    sub = {"entities": [
        {"name": "P.Provenance", "entity_type": "entity",
         # exactly the shape ANDP v2 produces: the consumable IS the base table, and
         # the typed view is declared separately — the case that rendered nothing.
         "properties": {"label": "Provenance", "deployed_view": "v_chain_of_custody",
                        "consumable_object": "P.Provenance"}},
        {"name": "P.Provenance.exhibit_number", "entity_type": "attribute",
         "properties": {"label": "exhibit_number"}}],
        "relationships": [{"relationship_type": "contains", "source_name": "P.Provenance",
                           "target_name": "P.Provenance.exhibit_number",
                           "source_type": "entity", "target_type": "attribute"}]}
    out = _render(sub)
    check("the object the product says to query is named",
          "v_chain_of_custody" in out, out[:400])
    check("and is stated once, not twice",
          out.count("v_chain_of_custody") == 1, out[:400])
    src = open(os.path.join(os.path.dirname(__file__), "..", "components", "builtin",
                            "knowledge_graph", "handler.py")).read()
    check("both renderings go through the ONE resolver",
          src.count("_preferred_query_object(") >= 3
          and "consumable_object\")\n                if consumable" not in src, "")


def t_query_object_qualification():
    from components.builtin.knowledge_graph.handler import _preferred_query_object as f
    check("a bare view is qualified with its own table's database",
          f({"deployed_view": "v_coc"}, "D.provenance") == "D.v_coc", "")
    check("an explicit consumable wins over the view",
          f({"deployed_view": "v_coc", "consumable_object": "D.other"}, "D.provenance") == "D.other", "")
    check("a consumable equal to the base table adds nothing",
          f({"consumable_object": "D.provenance"}, "D.provenance") == "", "")
    check("a product declaring neither renders exactly as before",
          f({}, "D.provenance") == "", "")


def t_proven_recipe_carries_its_sql():
    sql = ("SELECT exhibit_number, file_name FROM D.v_chain_of_custody "
           "WHERE exhibit_number = '20181119-001-001-01';")
    sub = {"entities": [{"name": "P.recipe:Chain of Custody", "entity_type": "query_recipe",
                         "properties": {"label": "Chain of Custody", "sql": sql,
                                        "description": "What objects match an exhibit?",
                                        "target_module": "D.v_chain_of_custody"}}],
           "relationships": []}
    out = _render(sub)
    check("a published recipe reaches the prompt as a STATEMENT",
          "SELECT exhibit_number" in out, out[:400])
    check("and names the object it queries", "D.v_chain_of_custody" in out, out[:400])
    check("under a header naming them the product's own published recipes",
          "PUBLISHED QUERIES" in out, out[:200])


def t_recipe_through_an_unverified_link_carries_the_caveat():
    """The bridge and the recipe are two halves of one fact.

    Found live, and caused BY the fix that surfaced recipes: an agent that had
    correctly refused to join CASE evidence to NIBRS incidents began performing
    that join, because the product publishes a recipe for it and the caveat lived
    on a different node. Rendering a recipe without its caveat promotes a
    self-declared synthetic key into a working join.
    """
    sql = ("SELECT c.exhibit_number FROM D.v_chain_of_custody c "
           "JOIN S.v_nibrs_crosswalk_join x ON x.case_entity_type = 'InvestigativeAction'")
    sub = {"entities": [
        {"name": "P.bridge:Investigation To Incident", "entity_type": "product_bridge",
         "properties": {"label": "Investigation To Incident",
                        "linkage_mechanism": "Synthetic Crosswalk Key",
                        "declared_in": "nibrs_crosswalk", "target_reference": "incident_Current",
                        "target_resolved": False,
                        "resolution": "target 'incident_Current' not found in this graph"}},
        {"name": "P.recipe:Join via Crosswalk", "entity_type": "query_recipe",
         "properties": {"label": "Join via Crosswalk", "sql": sql}}],
        "relationships": []}
    out = _render(sub)
    # Scoped to the recipe LINE throughout. The header mentions CAUTION, the SQL
    # contains the crosswalk name, and the BRIDGE node states the basis — so a
    # whole-render assertion passes with the caveat entirely absent. It did.
    line = next(l for l in out.split("\n") if "P.recipe:Join via Crosswalk" in l)
    check("the caveat is on the RECIPE, not merely somewhere in the render",
          "CAUTION:" in line, line)
    check("it names the object joined through", "nibrs_crosswalk" in line, line)
    check("and the declared basis, on that same line",
          "Synthetic Crosswalk Key" in line, line)
    check("and states the target did not resolve",
          "not found in this graph" in line, line)
    check("a recipe is never called a verified result",
          "known to work" not in out, out[:300])


def t_clean_recipe_is_not_caveated():
    sub = {"entities": [
        {"name": "P.bridge:Resolved", "entity_type": "product_bridge",
         "properties": {"label": "Resolved", "declared_in": "some_crosswalk",
                        "target_resolved": True}},
        # deliberately JOINS THROUGH the resolved bridge's object: a verified link
        # must not be caveated, and without this the "unresolved only" filter is an
        # equivalent mutant — nothing distinguishes it from "collect every bridge".
        {"name": "P.recipe:Plain", "entity_type": "query_recipe",
         "properties": {"label": "Plain",
                        "sql": "SELECT a FROM D.t JOIN S.some_crosswalk x ON x.id = t.id"}}],
        "relationships": []}
    out = _render(sub)
    # scoped to the recipe LINE: the section header legitimately mentions the word,
    # and asserting on the whole render would pass for the wrong reason.
    line = next(l for l in out.split("\n") if "P.recipe:Plain" in l)
    check("a recipe touching nothing unverified renders clean",
          "CAUTION" not in line, line)


def t_recipe_sql_is_bounded():
    from components.builtin.knowledge_graph.handler import _RECIPE_SQL_CHARS
    sub = {"entities": [{"name": "P.recipe:Big", "entity_type": "query_recipe",
                         "properties": {"label": "Big", "sql": "SELECT " + "x, " * 4000}}],
           "relationships": []}
    out = _render(sub)
    check("one pathological statement cannot eat the budget",
          len(out) < _RECIPE_SQL_CHARS + 600, f"rendered {len(out)} chars")


def t_short_desc_is_product_neutral():
    from components.builtin.knowledge_graph.handler import _short_desc
    check("no description renders no discriminator", _short_desc({}) == "", "")
    check("a short plain description survives whole",
          _short_desc({"description": "Total revenue for the period."})
          == "Total revenue for the period.", "")
    check("a long one is bounded and marked",
          _short_desc({"description": "word " * 80}).endswith("\u2026"), "")


def t_declared_join_is_qualified_by_the_key():
    """The 2026-09-03 finding: every segment table of a shipped product is keyed on
    (ORI, INCNUM) — INCNUM alone recurs across 132 agencies — yet the product's
    relationship rows can name ONE column per side, so they declare INCNUM, and the
    enrichment rendered that under "verified join keys — prefer these". The walker had
    carried the entity's `business_key` all along; the prompt never showed it. The
    agent joined one column short, rows multiplied, and a 48 s join ran past 300 s.

    Three things must reach the prompt: the table's key, a same-line warning where a
    declared column is narrower than that key, and the relationship row's own free
    text (the only place a one-column schema can say "join also on ORI").
    """
    ents = [
        {"name": "P.Incident", "entity_type": "entity",
         "properties": {"label": "Incident", "business_key": "ORI,INCNUM"}},
        {"name": "P.Victim", "entity_type": "entity",
         "properties": {"label": "Victim", "business_key": "ORI, INCNUM, V4006"}},
        {"name": "P.Incident.INCNUM", "entity_type": "attribute", "properties": {"label": "INCNUM", "deployed_type": "VARCHAR(12)"}},
        {"name": "P.Incident.ORI", "entity_type": "attribute", "properties": {"label": "ORI", "deployed_type": "VARCHAR(9)"}},
        {"name": "P.Victim.INCNUM", "entity_type": "attribute", "properties": {"label": "INCNUM", "deployed_type": "VARCHAR(12)"}},
        {"name": "P.Victim.ORI", "entity_type": "attribute", "properties": {"label": "ORI", "deployed_type": "VARCHAR(9)"}},
    ]
    rels = [{"relationship_type": "contains", "source_name": t, "target_name": c,
             "source_type": "entity", "target_type": "attribute"}
            for t, c in (("P.Incident", "P.Incident.INCNUM"), ("P.Incident", "P.Incident.ORI"),
                         ("P.Victim", "P.Victim.INCNUM"), ("P.Victim", "P.Victim.ORI"))]
    rels.append({"relationship_type": "foreign_key", "source_name": "P.Victim.INCNUM",
                 "target_name": "P.Incident.INCNUM", "source_type": "attribute",
                 "target_type": "attribute",
                 "properties": {"description": "Join also on ORI; many victims per incident"}})
    out = _render({"entities": ents, "relationships": rels})
    sch = out[out.find("TABLE SCHEMAS"):]
    check("the table line carries its declared key",
          "[key: ORI, INCNUM]" in sch, sch[:400])
    check("a three-column key renders whole",
          "[key: ORI, INCNUM, V4006]" in sch, sch[:400])
    fk = out[out.find("DECLARED FOREIGN KEYS"):]
    check("the declared column still renders",
          "INCNUM: P.Incident, P.Victim" in fk, fk[:400])
    check("a declared column narrower than the key is called out on the next line",
          "NOT the whole key" in fk and "(ORI, INCNUM)" in fk, fk[:500])
    check("the warning says what to do",
          "join on every key column" in fk, fk[:500])
    check("the relationship row's own note reaches the prompt",
          "Join also on ORI" in fk, fk[:500])
    check("the block no longer calls a declared column 'verified'",
          "verified join keys" not in fk, fk[:200])
    # a declared column that IS the whole key must not be warned about
    ents2 = [{"name": "P.Agency", "entity_type": "entity", "properties": {"label": "Agency", "business_key": "ORI"}},
             {"name": "P.Incident", "entity_type": "entity", "properties": {"label": "Incident", "business_key": "ORI,INCNUM"}},
             {"name": "P.Agency.ORI", "entity_type": "attribute", "properties": {"label": "ORI"}},
             {"name": "P.Incident.ORI", "entity_type": "attribute", "properties": {"label": "ORI"}}]
    rels2 = [{"relationship_type": "contains", "source_name": "P.Agency", "target_name": "P.Agency.ORI", "source_type": "entity", "target_type": "attribute"},
             {"relationship_type": "contains", "source_name": "P.Incident", "target_name": "P.Incident.ORI", "source_type": "entity", "target_type": "attribute"},
             {"relationship_type": "foreign_key", "source_name": "P.Incident.ORI", "target_name": "P.Agency.ORI", "source_type": "attribute", "target_type": "attribute"}]
    out2 = _render({"entities": ents2, "relationships": rels2})
    fk2 = out2[out2.find("DECLARED FOREIGN KEYS"):]
    check("a column that is narrower on ONE side still warns (Incident's key is wider)",
          "NOT the whole key" in fk2 and "Incident" in fk2, fk2[:400])
    ents3 = [dict(e) for e in ents2]; ents3[1] = {"name": "P.Incident", "entity_type": "entity", "properties": {"label": "Incident", "business_key": "ORI"}}
    out3 = _render({"entities": ents3, "relationships": rels2})
    check("a declared column that IS the whole key on both sides is not warned about",
          "NOT the whole key" not in out3, out3[out3.find("DECLARED"):][:300])




def t_positional_family_travels_with_its_representative():
    """A denormalised extract repeats a column per position ("LOCATION TYPE - 1/2/3"). One
    representative satisfies the concept, but a question asking for "any of the three"
    needs every sibling — an agent shown only "- 1" never learns the others exist
    (measured 2026-09-03: 140,609 for 145,193). Expanded ONLY when the question asks for
    positions; placed BEHIND every representative so a small render budget keeps the
    concepts before the siblings."""
    def col(leaf, desc):
        return {"name": f"NIBRS_Source.Incident.{leaf}", "entity_type": "attribute",
                "properties": {"label": leaf, "description": desc}}
    fam = [col(f"V2011{i}", f"LOCATION TYPE - {i} - V2011{i} is the location type of offence {i}") for i in (1, 2, 3)]
    weapon = [col(f"V2017{i}", f"WEAPON / FORCE 1 - {i} - V2017{i} is the weapon for offence {i}") for i in (1, 2, 3)]
    other = [col(f"V300{i}", f"PROPERTY DESCRIPTION - {i} - text") for i in range(10, 30)]
    pool = fam + weapon + other
    q_one = "how many incidents have a cyberspace location type?"
    names = [e["name"].split(".")[-1] for e in _rank_seeds(pool, q_one, limit=15)]
    check("a question naming one position seeds one representative", names.count("V20111") == 1 and "V20112" not in names[:1], names[:4])
    q_any = "how many incidents have a cyberspace location type in any of the three positions?"
    names = [e["name"].split(".")[-1] for e in _rank_seeds(pool, q_any, limit=15)]
    check("a positions question seeds the whole family", all(f"V2011{i}" in names for i in (1, 2, 3)), names)
    check("the family sits behind the representatives", names.index("V20112") > names.index("V20111"), names)
    check("a family the question did not name is not expanded", not all(f"V2017{i}" in names for i in (2, 3)) or names.index("V20172") > names.index("V20111"), names)
    from components.builtin.knowledge_graph import handler as kgh
    check("the family is bounded", kgh._FAMILY_CAP <= 9, kgh._FAMILY_CAP)
    check("the stem strips the position", kgh._title_stem(fam[2]) == kgh._title_stem(fam[0]) == "location type", kgh._title_stem(fam[2]))

def t_every_term_of_a_long_question_is_searched():
    """Pool completeness — the root cause under the trial's dominant miss (2026-09-03):
    the pool stopped growing at 600 ROWS while a question is searched term by term in
    question order, so on a thirty-word definition the words at the END (computer,
    location, cyberspace, identity) were never searched at all, and no ranking can seed
    what the pool does not contain. The bound is now on TERMS, at a level no real
    question reaches. Driven through the REAL search seam with a store that records
    every term it is asked for."""
    import asyncio, inspect
    from components.builtin.knowledge_graph import handler as kgh
    cls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_search_entities_for_query")][0]
    h = cls.__new__(cls)

    class Store:
        kg_id = None
        def __init__(self):
            self.terms = []
            self.n = 0
        async def search_entities(self, term, limit=25):
            self.terms.append(term)
            out = []
            for i in range(limit):  # every term finds a full page → the old ROW cap tripped after five terms
                self.n += 1
                out.append({"id": self.n, "name": f"NIBRS_Source.Incident.C{self.n}", "entity_type": "attribute",
                            "properties": {"label": f"C{self.n}", "description": f"row about {term}"}})
            return out
        def get_source_descriptor(self):
            return None
    # Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
    # a double unfaithful in the dimension under test measures nothing (Sep 2026).
    import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
    from kg_double_fidelity import double_mismatches as _dm
    from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
    check("Store double matches the real GraphStore calling convention", not _dm(_RealGS, Store), _dm(_RealGS, Store))

    import string
    words = " ".join("w" + a + b + c for a in string.ascii_lowercase[:4] for b in string.ascii_lowercase[:5] for c in string.ascii_lowercase[:2])  # 40 letter-only words (the tokenizer takes letters)
    q = f"how many incidents {words} cyberspace"
    st = Store()
    seeds = asyncio.run(h._search_entities_for_query(st, q, obda_surface=("off", frozenset()), usage_priors={}))
    check("the LAST concept of a forty-word question is searched", "cyberspace" in st.terms, st.terms[-3:])
    check("every non-stop word is searched", all(w in st.terms for w in words.split()), len(st.terms))
    check("the seeds are still a bounded cut", 0 < len(seeds) <= kgh._CONCEPT_SEED_CEILING, len(seeds))
    # the bound that remains is on TERMS, far beyond any real question
    q2 = "incidents " + " ".join("q" + a + b + c for a in string.ascii_lowercase for b in string.ascii_lowercase[:5] for c in string.ascii_lowercase[:1])[:1200]  # > the term cap
    st2 = Store()
    asyncio.run(h._search_entities_for_query(st2, q2, obda_surface=("off", frozenset()), usage_priors={}))
    check("a pathological question is bounded by the term cap", len(st2.terms) <= kgh._SEARCH_TERM_CAP + 1, len(st2.terms))

def t_compound_question_covers_every_concept():
    """2026-09-03, production, both lanes: the D1.1 question names three concepts —
    computer equipment suspected, a CYBERSPACE location, a cyber-intrinsic offence.
    The cut filled with the first and third (three slots spent on Arrestee/Offender/
    Victim copies of one Incident column) and the location column never reached the
    prompt; six of seven answers silently dropped that clause. A concept the cut does
    not represent must rescue its best candidate — bounded, and never a lexical hit
    from another product."""
    q = ("how many incidents involved a digital channel: the offender suspected of using "
         "computer equipment, or any offence location was cyberspace, or a cyber-intrinsic "
         "offence such as identity theft or hacking")
    def col(tbl, leaf, desc, values=None):
        return {"name": f"NIBRS_Source.{tbl}.{leaf}", "entity_type": "attribute",
                "properties": {"label": leaf, "description": desc,
                               "allowed_values": [{"value": "x", "label": v} for v in (values or [])]}}
    fillers = [col("Incident", f"V200{i}", "OFFENSE CODE - offender suspected of using computer equipment offence") for i in range(60, 84)]
    location = col("Incident", "V20111", "LOCATION TYPE - 1 - V20111 is the location type for the offense", ["Cyberspace", "Bank"])
    copies = [col(t, "V20111", "LOCATION TYPE - 1 - V20111 is the location type for the offense", ["Cyberspace"]) for t in ("Arrestee", "Offender", "Victim")]
    noise = {"name": "CASE_Source.View Metadata.is_cyber", "entity_type": "attribute",
             "properties": {"label": "is_cyber", "description": "cyberspace flag on a view"}}
    ranked = _rank_seeds(fillers + copies + [location, noise], q, limit=15, products=["nibrs_source", "case_source"])
    names = [e["name"] for e in ranked]
    check("the location column reaches the seeds", "NIBRS_Source.Incident.V20111" in names, names)
    check("a lexical hit on another product is not rescued", "CASE_Source.View Metadata.is_cyber" not in names, names)
    check("the seed budget still holds", len(ranked) == 15, len(ranked))
    # every concept gets its representative before any concept gets a second one
    q2 = "kappa lambda sigma omega"
    fill = [col("Incident", f"K{i}", "kappa leads this description") for i in range(15)]
    others = [col("Incident", "L1", "lambda leads this description"),
              col("Incident", "S1", "sigma leads this description"),
              col("Incident", "O1", "omega leads this description")]
    r2 = _rank_seeds(fill + others, q2, limit=15)
    leaves = [e["name"].split(".")[-1] for e in r2]
    check("each concept is represented", all(x in leaves for x in ("L1", "S1", "O1")), leaves)
    check("the rest of the budget follows the global order", leaves.count("K0") == 1 and len(leaves) == 15, leaves)
    # a concept from another product is not reached for
    far = col("Incident", "F1", "omega leads this description"); far["name"] = "OTHER_Product.Incident.F1"
    r4 = _rank_seeds(fill + others[:2] + [far], q2, limit=15, products=["nibrs_source", "other_product"])  # confinement keys on the DECLARED products
    check("a concept is not satisfied from another product", "OTHER_Product.Incident.F1" not in [e["name"] for e in r4], [e["name"] for e in r4][-3:])
    r5 = _rank_seeds(fill + others[:2] + [far], q2, limit=15)  # a graph declaring no products confines nothing
    check("with no declared products the far row is admitted", "OTHER_Product.Incident.F1" in [e["name"] for e in r5], [e["name"] for e in r5][-3:])
    # a concept already represented in the cut is left alone
    r3 = _rank_seeds([location] + fillers, "cyberspace location", limit=5)
    check("a represented concept triggers no rescue", r3[0]["name"] == "NIBRS_Source.Incident.V20111", [e["name"] for e in r3][:3])


def t_metric_names_are_relevance_gated():
    """WHICH metrics carry their declared names, and why the bar is what it is.

    Rendering them on every metric cost 12-18% of the WHOLE enrichment on every turn of
    a metric-bearing graph — paid whether or not compile_metric was ever called — and
    pushed two conversation-bench turns past the size bound. The names exist to make the
    FIRST call likely right; the refusal already makes the SECOND call certain."""
    from components.builtin.knowledge_graph import handler as H
    def _m(name, measures=(), desc=""):
        return {"entity_type": "metric", "name": name,
                "properties": {"description": desc,
                               "measures": [{"name": x} for x in measures]}}

    # No question ⇒ no relevance signal to gate on ⇒ every metric carries its names.
    ents = [_m("a.one"), _m("a.two")]
    check("no terms → every metric carries its names (silence would be worse than the bytes)",
          H._metrics_carrying_names(ents, []) == {"a.one", "a.two"})

    # The prod signal (session d9a60903): the question's word lives in a MEASURE name,
    # which the shared scorer never reads. Gating on that alone withheld the names from
    # exactly the turn they were built for.
    revenue = _m("dealer.dealer_retail_sale", ["Total Fi Product Revenue", "Row Count"])
    terms = H._scoring_terms("how much revenue did we generate with our cars")
    check("the shared scorer alone scores the prod metric ZERO (the defect this gate would have had)",
          H._match_score(revenue, terms) == 0)
    check("a declared measure NAME counts as the question reaching the metric",
          H._metric_relevance(revenue, terms) > 0)
    check("so the prod metric carries its names", "dealer.dealer_retail_sale" in
          H._metrics_carrying_names([revenue, _m("dealer.unrelated", ["Row Count"])], terms))

    # An incidental hit must not buy a line: the bar is the TOP few, not "nonzero".
    many = [revenue] + [_m(f"x.m{i}", ["Row Count"], "unrelated inventory counts") for i in range(9)]
    named = H._metrics_carrying_names(many, terms)
    check("bounded by the seeder's OWN reserved slots for the type, not by 'nonzero'",
          len(named) <= H._METRIC_NAMES_MAX_METRICS
          and H._METRIC_NAMES_MAX_METRICS == H._RESERVED_SLOTS_BY_TYPE["metric"])
    check("the strongest match is the one that is kept", "dealer.dealer_retail_sale" in named)

    # The weight is calibrated against the scorer's own tiers, not chosen freely: a
    # declared name is the node's own vocabulary carrying the term (the "decoded value"
    # class), so it must stay BELOW an identifier hit — a metric the question NAMES
    # outranks one whose measures merely contain the word.
    named_metric = _m("sales.revenue", ["Row Count"])          # the question names it
    measure_only = _m("x.unrelated", ["Total Revenue"])        # only its measure carries it
    t2 = H._scoring_terms("show me revenue")
    check("a metric the question NAMES outranks one whose measures merely mention the word",
          H._metric_relevance(named_metric, t2) > H._metric_relevance(measure_only, t2))
    check("the declared-name weight sits with 'decoded value', above 'formula'",
          H._METRIC_NAME_TERM_SCORE == 25)

    # The bound must truncate the LEAST relevant end. On the prod metric (32 measures)
    # 'Total Fi Product Revenue' is the 13th declared name, so a bound of 12 in declared
    # order showed twelve measures none of which was the asked-for one — worse than
    # showing none, because it invites picking a neighbour.
    wide = [f"Total {w}" for w in ("Apr", "Back End Gross", "Discount", "Doc Fee", "Down Payment",
                                   "Finance", "Front End Gross", "Monthly Payment", "Msrp",
                                   "Oem Incentive", "Sale Price", "Trade In")] + ["Total Fi Product Revenue"]
    t3 = H._scoring_terms("how much revenue did we generate")
    out = H._bounded_names(wide, 12, t3)
    check("a name the question matched survives the bound and leads",
          out.startswith("'Total Fi Product Revenue'"), out[:90])
    check("the rest keep declared order behind it", "'Total Apr', 'Total Back End Gross'" in out)
    check("no terms → declared order is preserved exactly",
          H._bounded_names(["b", "a", "c"], 3).startswith("'b', 'a', 'c'"))
    check("the remainder is still COUNTED, never dropped silently", "(+1 more)" in out, out[-40:])

    # A metric the question never reached carries none.
    cold = [_m("finance.unrelated", ["Row Count"], "accounts receivable")]
    check("a metric the question never reached carries no names",
          H._metrics_carrying_names(cold, H._scoring_terms("how many flights were late")) == set())

    # Fail open: a scorer that raises must never silently mute the names.
    import unittest.mock as _mock
    with _mock.patch.object(H, "_match_score", side_effect=RuntimeError("boom")):
        check("a raising scorer falls back to showing them — never a silent mute",
              H._metrics_carrying_names(ents, terms) == {"a.one", "a.two"})


def t_metric_header_clause_follows_the_lines():
    """The header promises "the NAMES listed on its line" — it may only say so when a
    line actually carries them, or it points at something that is not there."""
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as handler_cls
    from components.builtin.knowledge_graph.resolver import EntityCapabilities as _EC
    cap_named = _EC(compilable=True, metric_measures=["Total Revenue"])
    cap_bare = _EC(compilable=True, metric_measures=["Total Revenue"])
    sub = {"entities": [{"id": 1, "entity_type": "metric", "name": "sales.rev",
                         "properties": {"measures": [{"name": "Total Revenue"}]}}],
           "relationships": []}
    # No query ⇒ names render ⇒ the clause is earned.
    txt = handler_cls._format_subgraph_for_prompt(handler_cls.__new__(handler_cls), sub,
                                                  {"sales.rev": cap_named}, surface="off")
    check("names rendered → the header names them", "NAMES listed on its line" in txt
          and "measures: 'Total Revenue'" in txt)
    # A question that reaches nothing ⇒ no names ⇒ the clause must be withheld.
    txt2 = handler_cls._format_subgraph_for_prompt(handler_cls.__new__(handler_cls), sub,
                                                   {"sales.rev": cap_bare}, surface="off",
                                                   query="how many flights were late")
    check("no names rendered → the header does NOT promise them",
          "NAMES listed on its line" not in txt2 and "measures: 'Total Revenue'" not in txt2)
    check("but the metric is still offered as compilable", "compile_metric" in txt2)


# ── seeds the question did not reach (prod session ce559107, turn 2) ─────────
def t_function_words_are_not_terms():
    q = "Show me robberies per month — is robbery a rising or falling crime?"
    terms = _query_terms(q)
    check("`per` is not a question term", "per" not in terms, terms)
    check("the content words stay", {"robberies", "month", "robbery", "crime"} <= set(terms), terms)
    # `per` was a substring of decoded values ("Personal") and lit unrelated columns
    noise = attr("P.Property.description_code", "property description",
                 codes=[{"value": "1", "label": "Personal Property"}])
    check("a column reached only through `per` scores nothing", _match_score(noise, _scoring_terms(q)) == 0)


def t_unreached_rows_are_not_seeds():
    q = "how many robberies"
    hit = attr("P.Offence.offence_code", "UCR offence code",
               codes=[{"value": "120", "label": "Robbery"}])
    zero = {"name": "P.bridge.Offence__CaseObject", "entity_type": "product_bridge",
            "properties": {"label": "Offence__CaseObject", "description": "a class-level correspondence"}}
    ranked = _rank_seeds([zero, hit], q, limit=15)
    check("a row with no evidence does not fill a free slot",
          [e["name"] for e in ranked] == ["P.Offence.offence_code"], [e["name"] for e in ranked])
    ranked_p = _rank_seeds([zero, hit], q, limit=15, usage_priors={"P.bridge.Offence__CaseObject": 1.0})
    check("a usage prior orders reached rows, it never admits an unreached one",
          [e["name"] for e in ranked_p] == ["P.Offence.offence_code"], [e["name"] for e in ranked_p])
    check("when nothing scores, the pool is kept (a question the scorer cannot read)",
          len(_rank_seeds([zero], "zzz", limit=5)) == 1)


def t_weak_mention_yields_to_stronger_evidence():
    q = "robberies per month crime"
    domain = {"name": "P.crime", "entity_type": "domain", "properties": {"label": "crime"}}
    oris = [attr(f"P.T{i}.ori", "the agency reporting the crime") for i in range(4)]
    month = attr("P.Incident.incident_date_text", "the date of the incident; day, month and year")
    ranked = [e["name"] for e in _rank_seeds([domain] + oris + [month], q, limit=15)]
    check("passing mentions of a word something names exactly are dropped",
          not any(n.endswith(".ori") for n in ranked), ranked)
    check("a mention that is the BEST evidence for its word stays",
          "P.Incident.incident_date_text" in ranked, ranked)
    strong = attr("P.Offence.crime_kind", "crime kind", codes=[{"value": "1", "label": "Robbery"}])
    ranked2 = [e["name"] for e in _rank_seeds([strong, attr("P.X.note", "mentions crime and robbery")], "robbery crime", limit=5)]
    check("a row carrying any stronger signal is never dropped", "P.Offence.crime_kind" in ranked2, ranked2)


for fn in [t_metric_names_are_relevance_gated, t_metric_header_clause_follows_the_lines,
           t_coded_columns_carry_their_discriminator, t_coded_columns_are_ordered_by_relevance,
           t_coded_order_is_stable_without_a_query, t_declared_view_is_offered,
           t_query_object_qualification, t_proven_recipe_carries_its_sql,
           t_recipe_through_an_unverified_link_carries_the_caveat, t_clean_recipe_is_not_caveated,
           t_recipe_sql_is_bounded, t_short_desc_is_product_neutral,
           t_v2_surfaces_reach_the_prompt, t_unvalidated_product_asserts_nothing,t_codes_rendered, t_owner_beats_lookalike, t_incidental_mention_is_weak,
           t_value_labels_are_evidence, t_exact_name_match_wins, t_ranking_contract,
           t_char_column_codes_are_quoted,
           t_singularize, t_scoring_terms_cover_both_forms,
           t_matched_bridge_survives_the_cut, t_unmatched_bridge_is_not_forced_in,
           t_reserved_slots_are_bounded, t_data_product_renders_as_contract,
           t_deployed_identifier_leads_the_entity_line,
           t_declared_join_is_qualified_by_the_key, t_compound_question_covers_every_concept, t_every_term_of_a_long_question_is_searched, t_positional_family_travels_with_its_representative,
           t_function_words_are_not_terms, t_unreached_rows_are_not_seeds, t_weak_mention_yields_to_stronger_evidence]:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
