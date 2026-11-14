#!/usr/bin/env python3
"""A signed data product must tell the planner its ADMISSIBLE NAMES.

WHY THIS GATE EXISTS
Found in a guided walkthrough, on the simplest possible question ("How many
support tickets are there?"). The planner asked for a measure called
`ServiceTickets.TicketID`, was REJECTed, and self-corrected to `ticket_count`.
Across four runs it guessed four different names — `count`, `CustomerID`,
`TicketID`, `ServiceTickets.TicketID` — and every one cost an LLM round trip.

The model was not being careless. It was never told. A manifest stores
`entities` and `measures` as LISTS of objects, and the contract renderer tested
`isinstance(..., dict)`, so BOTH were silently skipped. What the planner
actually saw was:

    - support_load: Support load: tickets by customer geography …; validated

No measure names. No dimension names. Nothing to be right about.

That vocabulary is deterministic and known before the turn begins — so naming
it is Component → LLM priority 1, and paying a model to be told it is exactly
the shortcut the layering rule forbids.

Run: python3 test/test_product_contract_render.py      (no server, no network)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# OBDA-READ IS ADDITIVE: the master defaults OFF and this suite's SUBJECT is the
# enabled lane, so it declares the master explicitly (a separate process — the
# module default is untouched elsewhere). The master-off truth table lives in
# its own section/suite.
from trusted_data_agent.core.config import APP_CONFIG as _APP_CONFIG
from trusted_data_agent.core import obda_planes as _OP  # the planes' availability (test seam: force)
_OP.force("read", True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from components.builtin.knowledge_graph.handler import (  # noqa: E402
    KnowledgeGraphHandler, _dimension_names, _member_names)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


MANIFEST = {
    "product": "support_load",
    "description": "Support load: tickets by customer geography and product type",
    "entities": [
        {"name": "ServiceTickets", "attributes": [{"name": "Status"}, {"name": "TicketDate"}]},
        {"name": "Customers", "attributes": [{"name": "State"}, {"name": "City"}]},
        {"name": "Products", "attributes": [{"name": "ProductType"}, {"name": "Brand"}]},
    ],
    "measures": [
        {"name": "ticket_count", "anchor": "ServiceTickets", "expr": "COUNT(*)"},
        {"name": "customer_count", "anchor": "Customers", "expr": "COUNT(*)"},
    ],
}


def render(entities):
    h = KnowledgeGraphHandler.__new__(KnowledgeGraphHandler)
    return h._format_subgraph_for_prompt({"entities": entities, "relationships": []}, {})


def product_node(manifest, name="support_load", validated=True):
    props = {"manifest": manifest, "description": manifest.get("description")}
    if validated:
        props["validation_report"] = {"signable": True}
    return {"name": name, "entity_type": "data_product", "properties": props}


# ------------------------------------------------ the LIST shape is the real one
check("measures are named from the canonical LIST shape",
      (_member_names(MANIFEST["measures"]) or []) == ["ticket_count", "customer_count"],
      "this is how a manifest is actually stored; testing for a dict named none of them")
check("dimensions are QUALIFIED (Entity.Attribute)",
      "Customers.State" in (_dimension_names(MANIFEST["entities"]) or []),
      "unqualified is what a model reaches for and what gets rejected")
check("a dict shape still names its members", _member_names({"b": {}, "a": {}}) == ["a", "b"])
check("a plain list of strings still names its members", _member_names(["x", "y"]) == ["x", "y"])
for bad in (None, 42, "text", [1, 2], [{}]):
    check(f"unusable input {str(bad)[:12]} yields no names, never an error",
          not _member_names(bad) and not _dimension_names(bad),
          "a contract that raises is worse than one that under-reports")


# The cases above return early and never reach the exception guard, so on their own
# they assert nothing about it — a guard exercised by inputs that cannot trigger it
# is not a guard. These DO enter the body and raise inside it.
class _HostileDict(dict):
    def __iter__(self):
        raise RuntimeError("boom")

    def items(self):
        raise RuntimeError("boom")


class _HostileList(list):
    def __iter__(self):
        raise RuntimeError("boom")


for hostile in (_HostileDict(a=1), _HostileList([1])):
    kind = type(hostile).__name__
    try:
        check(f"{kind} that raises mid-walk degrades to no names",
              not _member_names(hostile) and not _dimension_names(hostile))
    except Exception as exc:
        check(f"{kind} that raises mid-walk degrades to no names", False,
              f"propagated {exc!r} — one malformed product would break the whole "
              "KG enrichment, not just its own line")

# ------------------------------------------------------ what the planner is shown
out = render([product_node(MANIFEST)])
check("the contract names every declared measure",
      "ticket_count" in out and "customer_count" in out,
      "the exact fact whose absence caused four different wrong guesses")
check("the contract names the dimensions in qualified form",
      "Customers.State" in out and "ServiceTickets.Status" in out)
check("the contract still directs the planner to query_product",
      "query_product" in out)
check("the preference is stated against the ALTERNATIVE",
      "Do NOT hand-write SQL over the same tables" in out
      and "NOT an alternative route" in out,
      "the same context shows the underlying tables; saying only 'use query_product' "
      "left the model free to read the schema as an equally valid route — observed")
check("a validated product says so", "validated" in out)

# the failure mode is a contract with a description and no vocabulary at all
vocabulary_line = next((l for l in out.splitlines() if l.strip().startswith("- support_load")), "")
check("the product line carries a vocabulary, not just prose",
      "measures:" in vocabulary_line,
      f"got {vocabulary_line.strip()[:120]!r} — this is the exact shape of the defect")

# -------------------------------------------------------------- no double render
check("a rendered product appears exactly once", out.count("support_load") == 1,
      "a second, poorer entry is what made the planner treat a product as an "
      "ordinary table and write raw SQL against it")

# ------------------------------------------------------- honest about not-validated
unval = render([product_node(MANIFEST, validated=False)])
check("an unvalidated product is flagged, not quietly offered",
      "NOT VALIDATED" in unval)

# --------------------------------------------- absent product ⇒ byte-identical
plain = [{"name": "Customers", "entity_type": "entity", "properties": {}}]
base = render(plain)
check("a graph with no product renders no contract block",
      "SIGNED DATA PRODUCTS" not in base,
      "the block is additive — a graph without products must be unchanged")

# ─── the substitution rule is stated AT PLAN TIME (session 7b9b5a25) ────────
# The REJECT tail states it at runtime — but a planner that silently plans an
# admissible query at the WRONG grain never reads a REJECT ("which customer has
# the most tickets?" planned straight onto customer_count × Customers.State,
# first shot, zero REJECTs). The contract itself must carry the rule.
withrule = render([product_node(MANIFEST)])
check("the contract states the ONLY-members rule",
      "These are the ONLY members" in withrule)
check("the contract forbids silent grain substitution at plan time",
      "NEVER silently answer at a different grain" in withrule)
check("…and names the honest alternatives (say so, or disclose the coarser grain)",
      "say so explicitly" in withrule and "coarser question" in withrule)
check("the rule renders ONLY with a contract block (additive)",
      "ONLY members" not in base,
      "a product-free graph must not carry product instructions")

# ─── OBDA-READ IS ADDITIVE: master off ⇒ NO contract surface at all ──────────────
# (operator imperative after d51cf073: "if obda is disabled the system must
#  behave as before" — yet the enrichment kept rendering contracts and telling
#  the planner "you MUST use query_product" with every consumption face refused)
_OP.force("read", False)
try:
    off = render([product_node(MANIFEST)])
    check("master OFF: no SIGNED DATA PRODUCTS block", "SIGNED DATA PRODUCTS" not in off)
    check("master OFF: no query_product instruction",
          "query_product" not in off,
          "telling the planner to use a refused face is the d51cf073 catastrophe")
    check("master OFF: no ONLY-members rule either", "ONLY members" not in off)
finally:
    _OP.force("read", True)

# ─── the OTHER instruction surfaces: the product text lives on OBDA-READ ─────
# The plan's "governed statement… signed, deterministic contract" language once came
# from surfaces the enrichment gate never touched: the knowledge-graph tool's
# instructions.json and manifest still SOLD query_product with the master off
# (session dd40d071). The data-product face is now its OWN component (obda_read,
# TDA_DataProduct): the knowledge-graph tool advertises no product action or
# argument at all — on any switch — and the plane's text is offered exactly where
# the plane is enabled (the one component chain). The knowledge-graph tool keeps
# the actions as unadvertised ALIASES for stored champion plans.
import json as _json
from trusted_data_agent.components.manager import ComponentManager as _CM
_instr = _json.load(open(os.path.join(os.path.dirname(__file__), "..", "components",
                                      "builtin", "knowledge_graph", "instructions.json")))
_man = _json.load(open(os.path.join(os.path.dirname(__file__), "..", "components",
                                    "builtin", "knowledge_graph", "manifest.json")))
_rinstr = _json.load(open(os.path.join(os.path.dirname(__file__), "..", "components",
                                       "builtin", "obda_read", "instructions.json")))
_rman = _json.load(open(os.path.join(os.path.dirname(__file__), "..", "components",
                                     "builtin", "obda_read", "manifest.json")))
for _sw in (False, True):
    _OP.force("read", _sw)
    try:
        for lvl in ("medium", "heavy"):
            got = _CM.apply_instruction_gates(_instr[lvl])
            check(f"master {'ON' if _sw else 'OFF'}: KG instructions[{lvl}] sell no products",
                  "query_product" not in got and "Data products" not in got)
            check(f"master {'ON' if _sw else 'OFF'}: KG instructions[{lvl}] keep pre-OBDA-READ compile_metric",
                  "compile_metric" in got)
        _gtd = _CM._gated_tool_definition(_man["tool_definition"])
        check(f"master {'ON' if _sw else 'OFF'}: no KG arg description sells query_product",
              "query_product" not in _json.dumps(_gtd).replace("query_product entity-read", ""),
              [k for k, v in (_gtd.get("args") or {}).items()
               if "query_product" in str((v or {}).get("description"))])
        check(f"master {'ON' if _sw else 'OFF'}: the KG action enum prose carries no OBDA-READ action",
              "'query_product'" not in (_gtd.get("args") or {}).get("action", {}).get("description", "")
              and "'list_products'" not in (_gtd.get("args") or {}).get("action", {}).get("description", ""))
    finally:
        _OP.force("read", True)
for lvl in ("medium", "heavy"):
    check(f"OBDA-READ component: instructions[{lvl}] carry the product text, unwrapped",
          "[[gate:" not in _rinstr[lvl] and "query_product" in _rinstr[lvl] and "EXACTLY as given" in _rinstr[lvl])
check("OBDA-READ component: TDA_DataProduct offers exactly list_products + query_product",
      _rman["tool_definition"]["name"] == "TDA_DataProduct"
      and "'list_products' | 'query_product'" == _rman["tool_definition"]["args"]["action"]["description"]
      and "[[gate:" not in _json.dumps(_rman))
check("an unknown gate keeps its content (fail-visible, with a warning)",
      _CM.apply_instruction_gates("a [[gate:nonsense]]kept[[/gate]] b") == "a kept b")
check("text without markers is byte-identical",
      _CM.apply_instruction_gates("plain text") == "plain text")

# ─── the compile_metric invitation requires an actual metric ─────────────────
_caps = {"x": 1}
_plain = [{"name": "Customers", "entity_type": "entity", "properties": {}}]
_with_metric = _plain + [{"name": "Revenue", "entity_type": "metric", "properties": {}}]
h2 = KnowledgeGraphHandler()
_no_m = h2._format_subgraph_for_prompt({"entities": _plain, "relationships": []}, _caps)
_ye_m = h2._format_subgraph_for_prompt({"entities": _with_metric, "relationships": []}, _caps)
check("no metric node → no compile_metric invitation",
      "compile_metric" not in _no_m,
      "the unconditional invitation lured compile_metric('Revenue') on a "
      "metric-less graph (dd40d071)")
check("a metric node present → the invitation renders", "compile_metric" in _ye_m)

# ------------------------------------- a manifest with no measures does not lie
bare = render([product_node({"product": "p", "description": "d",
                             "entities": [], "measures": []})])
check("a product with nothing declared claims no vocabulary",
      "measures:" not in bare and "dimensions:" not in bare,
      "an empty list must not render as an empty promise")


# ==========================================================================
# [C] A product must be SEEDED into the enrichment regardless of the question.
#
# Observed: "how many customers do we have?" — a question `customer_count`
# answers exactly — produced `SELECT COUNT(CustomerID) FROM fitness_db.Customers`
# and lane `free-form`, because the enrichment for that turn contained no
# SIGNED DATA PRODUCTS block at all. The planner did not reject the governed
# lane; it was never shown one.
#
# A product is named for its SUBJECT AREA; a question names entities and
# columns, so a product loses the lexical match essentially every time. An
# absent product is not a missing fact — it is a missing OPTION.
# ==========================================================================
import asyncio  # noqa: E402


class _FakeStore:
    """Search returns only column-ish matches — the realistic case."""

    def __init__(self, products):
        self._products = products
        self.listed_types = []

    async def search_entities(self, term, limit=None):
        return [{"id": f"col-{term}", "name": f"Customers.{term}",
                 "entity_type": "attribute", "properties": {}}]

    async def list_entities(self, entity_type=None, **kw):
        self.listed_types.append(entity_type)
        return list(self._products)


PRODUCT = {"id": "p1", "name": "support_load", "entity_type": "data_product",
           "properties": {"manifest": MANIFEST}}


def seeds_for(query, products=(PRODUCT,), surface=("mixed", frozenset({"support_load"}))):
    # the seed follows the PROFILE's grant (the tri-state): the harness declares
    # a C-mode profile listing support_load, the way a real participating
    # profile does; surface=None models Option A / an unthreaded caller
    h = KnowledgeGraphHandler.__new__(KnowledgeGraphHandler)
    store = _FakeStore(list(products))
    out = asyncio.run(h._search_entities_for_query(store, query,
                                                   obda_surface=surface))
    return out, store


for q in ("how many customers do we have?", "revenue last quarter",
          "list the tables", "hello"):
    got, _ = seeds_for(q)
    names = [e["name"] for e in got if e.get("entity_type") == "data_product"]
    check(f"a product is seeded for {q!r:<34}", names == ["support_load"],
          f"got {names} — the planner cannot choose a lane it was never shown")

# Option A (the default) — and an unthreaded caller — seed NOTHING
_got_a, _ = seeds_for("how many customers do we have?", surface=("off", frozenset()))
check("Option A seeds no products",
      not [e for e in _got_a if e.get("entity_type") == "data_product"])
_got_n, _ = seeds_for("how many customers do we have?", surface=None)
check("an unthreaded caller seeds no products (fail toward invisible)",
      not [e for e in _got_n if e.get("entity_type") == "data_product"])
# the LIST is the grant: a product not on it is not seeded
_got_l, _ = seeds_for("how many customers do we have?",
                      surface=("mixed", frozenset({"some_other_product"})))
check("a product not on the grant list is not seeded",
      not [e for e in _got_l if e.get("entity_type") == "data_product"])

# it must ask for products BY TYPE, not hope the text search found them
_, store = seeds_for("how many customers do we have?")
check("products are fetched by type, not left to the text search",
      "data_product" in store.listed_types,
      "the search is lexical and a product name never matches a column question")

# bounded — a graph with many products must not flood the enrichment
many = [dict(PRODUCT, id=f"p{i}", name=f"prod_{i}") for i in range(40)]
got, _ = seeds_for("anything", products=many,
                   surface=("mixed", frozenset(f"prod_{i}" for i in range(40))))
n = len([e for e in got if e.get("entity_type") == "data_product"])
check("seeding is bounded", 0 < n <= 10, f"seeded {n}")

# no duplicate when the search DID find it
dup = {"id": "p1", "name": "support_load", "entity_type": "data_product", "properties": {}}


class _FindsProduct(_FakeStore):
    async def search_entities(self, term, limit=None):
        return [dict(dup)]


def _seeds_dup():
    h = KnowledgeGraphHandler.__new__(KnowledgeGraphHandler)
    return asyncio.run(h._search_entities_for_query(_FindsProduct([dup]), "support load"))


ids = [e["id"] for e in _seeds_dup() if e.get("entity_type") == "data_product"]
check("a product found by search is not seeded twice", ids.count("p1") == 1,
      f"got {ids}")


# a graph with NO products is byte-identical to before
class _NoProducts(_FakeStore):
    async def list_entities(self, entity_type=None, **kw):
        return []


def _seeds_none():
    h = KnowledgeGraphHandler.__new__(KnowledgeGraphHandler)
    return asyncio.run(h._search_entities_for_query(_NoProducts([]), "customers"))


check("a graph with no products seeds none", not any(
    e.get("entity_type") == "data_product" for e in _seeds_none()))


# an unreadable product layer must never cost the turn its ordinary enrichment
class _BrokenProducts(_FakeStore):
    async def list_entities(self, entity_type=None, **kw):
        raise RuntimeError("backend down")


def _seeds_broken():
    h = KnowledgeGraphHandler.__new__(KnowledgeGraphHandler)
    return asyncio.run(h._search_entities_for_query(_BrokenProducts([]), "customers"))


try:
    check("an unreadable product layer degrades, never raises", bool(_seeds_broken()))
except Exception as exc:
    check("an unreadable product layer degrades, never raises", False, repr(exc))


# ==========================================================================
# [D] The enrichment event must NAME the products the planner was shown.
#
# The event carried `entity_types` only, so "a product was present" was visible
# and "WHICH product, and did its contract render" was not. During a live
# investigation that gap led twice to the wrong conclusion — that a product was
# absent from an enrichment which in fact contained it. An observability
# surface that cannot answer the question it exists for is worse than none.
#
# ONE rule, read by the renderer AND the event, so they can never disagree.
# ==========================================================================
from components.builtin.knowledge_graph.handler import _rendered_products  # noqa: E402

ENTS = [PRODUCT, {"id": "t1", "name": "Customers", "entity_type": "entity", "properties": {}}]
check("the shared rule selects only data products",
      [e["name"] for e in _rendered_products(ENTS)] == ["support_load"])
check("the shared rule is bounded",
      len(_rendered_products([dict(PRODUCT, id=str(i)) for i in range(50)])) <= 10)
for bad in (None, 42, [None, "x"]):
    check(f"the shared rule survives {str(bad)[:12]}", _rendered_products(bad) == []
          or all(isinstance(e, dict) for e in _rendered_products(bad)))

# renderer and event agree BY CONSTRUCTION, not by coincidence
src = open(os.path.join(os.path.dirname(__file__), "..", "components", "builtin",
                        "knowledge_graph", "handler.py")).read()
# (These four were greps of the pre-tri-state source forms — they broke when
#  the render/event/seed became PROFILE-mode driven while the property held.
#  The property, restated: renderer, event and seed all key on the SAME
#  resolved mode/surface, so they cannot disagree about what was shown.)
# (anchor on the assignment's own line start — a bare "_products = " split
#  matched `_obda_products = _profile_surface(` first)
check("the renderer reads the shared rule (mode-driven)",
      "_rendered_products(subgraph[\"entities\"]," in
      src.split("\n        _products = (", 1)[1][:400])
check("the EVENT's data_products carries the mode gate",
      '_obda_mode != "off"' in
      src.split('metadata["data_products"] = ', 1)[1][:300],
      "gating one side is how the prompt and the event disagree about what was shown")
check("the SEED carries the grant gate too",
      'if _sm != "off"' in
      src.split('products = (await store.list_entities', 1)[1][:200])
check("the event reads the SAME shared rule",
      "_rendered_products(subgraph[\"entities\"]," in
      src.split('metadata["data_products"] = ', 1)[1][:300],
      "recomputing the list in the event is how the two drift")
check("event and renderer key on ONE resolved mode (_obda_mode)",
      src.count("_obda_mode, _obda_products = _profile_surface(") == 1,
      "a second resolution is a second opinion")
check("neither side re-filters data_product inline",
      src.count('e.get("entity_type") == "data_product"') == 1,
      "exactly one place may decide what counts as a rendered product")


# ==========================================================================
# [E] Under an EXCLUSIVE data surface, the contract shows only the open doors.
#
# Observed (session a7711936, post-champion-reset): a surface-only agent chose
# the governed lane FREELY — and picked `fitness_sales`, an off-surface product,
# on a lexical cue ("customer geography"). Denied, it spiralled and concluded,
# falsely, that no product could count customers while support_load's contract
# — customer_count — sat in the pile of four. The runtime knows exactly which
# doors are open (the ambient principal); showing locked ones is a maze.
#
# The filter reads the SAME contextvar the tool chokepoints enforce with, so
# what the contract SHOWS and what the runtime ALLOWS cannot disagree.
# ==========================================================================
from trusted_data_agent.core import agent_authority as _aa  # noqa: E402
from components.builtin.knowledge_graph.handler import _mark_on_surface  # noqa: E402

# Each with its OWN manifest: the contract line renders the MANIFEST's product name,
# so a shallow dict(PRODUCT, name=...) would render three contracts all called
# "support_load" and the assertions would test a fixture artifact, not the filter.
P2 = product_node(dict(MANIFEST, product="fitness_sales"), name="fitness_sales")
P2["id"] = "p2"
P3 = product_node(dict(MANIFEST, product="agent_face_fixture"), name="agent_face_fixture")
P3["id"] = "p3"
ALL3 = [PRODUCT, P2, P3]

SURFACE_PROFILE = {"id": "x", "authorityConfig": {
    "dataSurface": {"products": ["support_load"], "exclusive": True, "advisory": False}}}


def under_principal(profile, fn):
    tok = _aa.set_current_principal(_aa.derive_principal(profile, "u1"))
    try:
        return fn()
    finally:
        _aa.reset_current_principal(tok)


# unconfined: byte-identical to before — every product is a contract, no off line
base = render(ALL3)
check("unconfined: all products render as contracts",
      all(n in base for n in ("support_load", "fitness_sales", "agent_face_fixture")))
check("unconfined: no off-surface line", "NOT on this agent's data surface" not in base)
check("unconfined: no surface-only headline", "SURFACE-ONLY" not in base)

# surface-only: one open door, the locked ones NAMED
out = under_principal(SURFACE_PROFILE, lambda: render(ALL3))
lines = [l for l in out.splitlines() if l.strip().startswith("- ")]
check("surface-only: ONLY the on-surface product is a contract",
      len(lines) == 1 and "support_load" in lines[0],
      f"got {len(lines)} contract lines")
check("surface-only: the headline states the confinement",
      "SURFACE-ONLY" in out and "raw SQL will be denied" in out)
check("surface-only: locked products are NAMED, never silently dropped",
      "NOT on this agent's data surface" in out
      and "fitness_sales" in out and "agent_face_fixture" in out,
      "a product that simply vanishes is indistinguishable from one that does not "
      "exist, and the agent goes looking for it by other means")
check("surface-only: an off-surface product is never a bare entity either",
      out.count("fitness_sales") == 1,
      "a second rendering is a door the runtime will slam")

# advisory surface must NOT filter — advisory observes, it does not confine
ADVISORY = {"id": "x", "authorityConfig": {
    "dataSurface": {"products": ["support_load"], "exclusive": True, "advisory": True}}}
adv = under_principal(ADVISORY, lambda: render(ALL3))
check("an ADVISORY surface does not filter the contract",
      all(n in adv for n in ("support_load", "fitness_sales")) and "SURFACE-ONLY" not in adv,
      "advisory mode denies nothing, so hiding options would misstate the runtime")

# additive (non-exclusive) surface must not filter either
ADDITIVE = {"id": "x", "authorityConfig": {
    "dataSurface": {"products": ["support_load"], "exclusive": False, "advisory": False}}}
addv = under_principal(ADDITIVE, lambda: render(ALL3))
check("a non-exclusive surface does not filter the contract",
      "fitness_sales" in addv and "SURFACE-ONLY" not in addv)

# the catalog marks and reorders — the mid-turn half of the same fact
items, note = under_principal(SURFACE_PROFILE, lambda: _mark_on_surface(
    [{"product": "fitness_sales"}, {"product": "support_load"}]))
check("catalog: on-surface first", items[0]["product"] == "support_load")
check("catalog: every row carries on_surface",
      [i.get("on_surface") for i in items] == [True, False])
check("catalog: the note states the rule", bool(note) and "SURFACE-ONLY" in note)
items2, note2 = _mark_on_surface([{"product": "support_load"}])
check("catalog unconfined: unchanged, no note",
      "on_surface" not in items2[0] and note2 is None)


# an authority layer that RAISES mid-read must degrade to unfiltered, never kill
# the enrichment — the guard existed but nothing exercised it, and its removal
# escaped the first mutation pass (a guard exercised by nothing is not a guard)
class _HostilePrincipal:
    @property
    def scope(self):
        raise RuntimeError("authority backend down")


_tok = _aa.set_current_principal(_HostilePrincipal())
try:
    hostile_out = render(ALL3)
    check("a raising principal read degrades to the unfiltered contract",
          "support_load" in hostile_out and "fitness_sales" in hostile_out
          and "SURFACE-ONLY" not in hostile_out)
    _hi, _hn = _mark_on_surface([{"product": "support_load"}])
    check("a raising principal read leaves the catalog unmarked",
          "on_surface" not in _hi[0] and _hn is None)
except Exception as exc:
    check("a raising principal read degrades to the unfiltered contract", False,
          f"propagated {exc!r} — one broken authority read would blank the whole enrichment")
finally:
    _aa.reset_current_principal(_tok)


# ==========================================================================
# [F] Under an EXCLUSIVE surface, the enrichment is the contract ALONE.
#
# The operator's cut: the remaining sections — TABLE SCHEMAS, joinable columns,
# entity listings, coded columns, and even the compile_metric preamble — exist
# solely to help an agent WRITE SQL, and a surface-only agent cannot execute
# SQL it writes (compile_metric output is not in the governed registry either;
# only query_product registers). Rendering them is not transparency, it is
# bait: with the schemas present the planner kept reaching for
# `SELECT COUNT(*) FROM Customers` even with a one-door contract in front of it
# — proven twice on identical context, once each way. Prevention beats recovery.
# ==========================================================================
TABLEISH = [
    PRODUCT,
    {"id": "db1", "name": "fitness_db", "entity_type": "database", "properties": {}},
    {"id": "t9", "name": "Inventory", "entity_type": "entity",
     "properties": {"description": "warehouse stock"}},
]

full = render(TABLEISH)
check("unconfined: ordinary entities still render", "Inventory" in full)
check("unconfined: the planning preamble still renders",
      "may inform your planning" in full)

cut = under_principal(SURFACE_PROFILE, lambda: render(TABLEISH))
check("surface-only: NO table/entity listing renders", "Inventory" not in cut,
      "a schema the agent cannot use is the bait that keeps beating the contract")
check("surface-only: the compile_metric preamble is withheld",
      "compile_metric" not in cut,
      "compile_metric SQL is not in the governed registry — advertising it invites "
      "compiling a statement the runtime then denies")
check("surface-only: the withholding is stated, not silent",
      "Schema details are withheld" in cut and "cannot execute raw SQL" in cut)
check("surface-only: the contract itself still renders fully",
      "customer_count" in cut and "Customers.State" in cut)
check("surface-only: the cut text is a fraction of the full text",
      len(cut) < len(full),)

# advisory/additive surfaces keep the full enrichment — they can write SQL
adv_cut = under_principal(ADVISORY, lambda: render(TABLEISH))
check("advisory surface keeps the full enrichment", "Inventory" in adv_cut)

# the event flags the cut so the Live-Status title can distinguish ANALYSED from
# INJECTED — without the flag the title reads "51 entities" over a turn that
# injected only the contract (the operator's finding, verbatim)
src2 = open(os.path.join(os.path.dirname(__file__), "..", "components", "builtin",
                         "knowledge_graph", "handler.py")).read()
check("the enrichment event carries surface_only under an exclusive surface",
      'metadata["surface_only"] = True' in src2)
check("the flag is gated on the SAME surface resolver as the text cut",
      src2.count("_surface_for_turn()") >= 3,
      "a second notion of 'confined' is how the flag and the cut drift apart")

print(f"\n{PASS} passed, {FAIL} failed  ({PASS + FAIL} assertions)"
      + ("" if FAIL else "  — the contract names its admissible members"))
sys.exit(1 if FAIL else 0)
