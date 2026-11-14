#!/usr/bin/env python3
"""KG expansion reach — what one seed may pull into the context (CI-pure, no DB, no LLM).

Two rules, both from local session f622f5ef turn 2 ("What hacking evidence was collected
in this case?"), where the weakest seed pulled a whole star model in and the one entity
the question's MEANING reached lost its seed slot:

  1. The key chain follows the direction a join needs: a seed's own children and all of
     its ancestors, never a non-seed ancestor's other children (the seed's siblings).
  2. One seed slot is reserved for the best row reached by meaning alone; a strong
     lexical row never yields to it, and with the channel off nothing changes.

Run: python3 test/test_kg_expansion_reach.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import networkx as nx  # noqa: E402

from trusted_data_agent.kgstore.sqlite_backend import _adaptive_subgraph_from_graph  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "kgh_reach", os.path.join(ROOT, "components/builtin/knowledge_graph/handler.py"))
kgh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kgh)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}{' — ' + str(detail) if detail else ''}")


def star(card_style="1:N"):
    """Agency ─< Incident ─< {Offence, Victim, Weapon};  Offence ─< Suspected.
    `card_style` 1:N stores parent→child; M:1 stores child→parent; None declares nothing."""
    G = nx.DiGraph()
    names = ["Agency", "Incident", "Offence", "Victim", "Weapon", "Suspected"]
    for i, n in enumerate(names, 1):
        G.add_node(i, name=f"M.{n}", entity_type="entity", properties={})
    G.add_node(10, name="M.Offence.offence_code", entity_type="attribute", properties={})
    G.add_edge(3, 10, relationship_type="contains", cardinality=None, metadata={})
    for parent, child in ((1, 2), (2, 3), (2, 4), (2, 5), (3, 6)):
        if card_style == "1:N":
            G.add_edge(parent, child, relationship_type="foreign_key", cardinality="1:N", metadata={})
        elif card_style == "M:1":
            G.add_edge(child, parent, relationship_type="foreign_key", cardinality="M:1", metadata={})
        else:
            G.add_edge(parent, child, relationship_type="foreign_key", cardinality=None, metadata={})
    return G


def tables(sub):
    return sorted(e["name"].split(".")[-1] for e in sub["entities"] if e.get("entity_type") == "entity")


print("[1] the key chain follows the direction a join needs")
for style in ("1:N", "M:1"):
    sub = _adaptive_subgraph_from_graph(star(style), [10])           # the seed is an attribute of Offence
    t = tables(sub)
    check(f"{style}: the seed's entity, its child and every ancestor are reached",
          {"Offence", "Suspected", "Incident", "Agency"} <= set(t), t)
    check(f"{style}: a non-seed ancestor's other children (the seed's siblings) are not",
          "Victim" not in t and "Weapon" not in t, t)
    sub2 = _adaptive_subgraph_from_graph(star(style), [2])           # Incident itself is the seed
    check(f"{style}: a seed's own children are all reached", {"Offence", "Victim", "Weapon", "Agency"} <= set(tables(sub2)), tables(sub2))
    check(f"{style}: …but not its grandchildren through a non-seed child", "Suspected" not in tables(sub2), tables(sub2))
    sub3 = _adaptive_subgraph_from_graph(star(style), [10, 4])       # two seeds meet at their parent
    check(f"{style}: two seeded siblings both stay, joined through the parent",
          {"Offence", "Victim", "Incident"} <= set(tables(sub3)) and "Weapon" not in tables(sub3), tables(sub3))
sub = _adaptive_subgraph_from_graph(star(None), [10])
check("an edge that declares no direction is walked both ways, as before", len(tables(sub)) == 6, tables(sub))
G = star("1:N")
G.add_node(7, name="M.Email", entity_type="entity", properties={})
G.add_edge(7, 3, relationship_type="is_a", cardinality=None, metadata={})   # Email is_a Offence (shape only)
check("a specialisation is a child of its supertype: reached from a seeded supertype",
      "Email" in tables(_adaptive_subgraph_from_graph(G, [10])))
check("…and not from a non-seed supertype", "Email" not in tables(_adaptive_subgraph_from_graph(G, [6])),
      tables(_adaptive_subgraph_from_graph(G, [6])))

print("[2] one seed slot for the best meaning-only row")


def row(i, name, et="attribute", desc=""):
    return {"id": i, "name": name, "entity_type": et, "properties": {"description": desc}}


q = "what evidence was collected"
pool = [row(i, f"M.T{i}.evidence") for i in range(1, 16)]                      # fifteen exact-identifier rows (100)
weak = row(20, "M.Chain.evidence_id")                                          # an identifier-prefix row (40)
meaning = row(30, "M.Suspected.using", desc="computer equipment")              # no word in common
sem = {"M.Suspected.using": 0.45}
full = pool + [meaning]
out = kgh._rank_seeds(full, q, limit=15, semantic_scores=sem)
check("a cut the words fill with strong rows does not yield a slot", meaning not in out, [e["name"] for e in out][-3:])
mixed = pool[:14] + [weak, meaning]
out = kgh._rank_seeds(mixed, q, limit=15, semantic_scores=sem)
check("the best meaning-only row takes the place of the weakest weak-evidence row", meaning in out and weak not in out,
      [e["name"] for e in out][-3:])
out_off = kgh._rank_seeds(mixed, q, limit=15, semantic_scores=None)
check("channel off: the cut is the lexical cut", meaning not in out_off and weak in out_off)
check("below the floor the meaning row claims nothing",
      meaning not in kgh._rank_seeds(mixed, q, limit=15, semantic_scores={"M.Suspected.using": 0.30}))
short = pool[:3] + [meaning]
check("a free slot is simply taken", meaning in kgh._rank_seeds(short, q, limit=15, semantic_scores=sem))
two = pool[:14] + [weak, meaning, row(31, "M.Other.thing", desc="x")]
out2 = kgh._rank_seeds(two, q, limit=15, semantic_scores={"M.Suspected.using": 0.45, "M.Other.thing": 0.44})
check("ONE slot: a second meaning-only row does not take another",
      sum(1 for e in out2 if e["name"] in ("M.Suspected.using", "M.Other.thing")) == 1, [e["name"] for e in out2][-3:])
check("the yield floor is an identifier-word match", kgh._SEMANTIC_RESERVE_YIELD_BELOW == 70 and kgh._SEMANTIC_RESERVE_MIN == 0.40)

print(f"\n=== KG expansion reach: {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
