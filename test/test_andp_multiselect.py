"""
CI-pure tests for building SEVERAL data products into one knowledge graph.

Two rules live here, both of which fail silently if broken:

  * ``overall_status`` — a build where one of three products failed is NOT a
    success. The resulting graph looks exactly like a complete one, and an agent
    bound to it fails only later, on a question it can no longer answer.
  * ``reresolve_bridges`` — a bridge resolves against whatever existed when its
    product was walked, so a sequence is one-directional: build NIBRS then CASE and
    CASE's bridges see NIBRS, but a bridge NIBRS declared against CASE can never
    resolve. That is an artefact of build ORDER, not a fact about the products.

The re-resolution pass may only ever UPGRADE a dangling bridge. Inventing an edge
to make a dangling bridge disappear would destroy the exact signal it carries — a
declared link whose target does not exist is the finding.

No database, no network — the store is a stub.

Run: python3 test/test_andp_multiselect.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.agent.kg_constructors.andp_semantic import (  # noqa: E402
    aggregate_stats,
    overall_status,
    reresolve_bridges,
    union_scope,
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


def ok(p):
    return {"product": p, "status": "success"}


def bad(p, err="boom"):
    return {"product": p, "status": "failed", "error": err}


# ── the status rule ─────────────────────────────────────────────────────────
def t_overall_status():
    check("all built -> success", overall_status([ok("A"), ok("B")]) == "success")
    check("one of three failed -> PARTIAL, never success",
          overall_status([ok("A"), bad("B"), ok("C")]) == "partial",
          overall_status([ok("A"), bad("B"), ok("C")]))
    check("all failed -> error", overall_status([bad("A"), bad("B")]) == "error")
    check("single success -> success", overall_status([ok("A")]) == "success")
    check("single failure -> error", overall_status([bad("A")]) == "error")
    check("nothing attempted -> error, not success", overall_status([]) == "error")
    check("None is safe", overall_status(None) == "error")
    check("junk rows are ignored, not counted as built",
          overall_status(["nope", None, ok("A")]) == "success")


# ── the bridge re-resolution pass ───────────────────────────────────────────
class FakeStore:
    """Just enough store to drive the pass; records what it was asked to change."""

    def __init__(self, entities):
        self._entities = entities
        self.updated = []
        self.linked = []

    async def list_entities(self, *a, **k):
        return self._entities

    async def update_entity(self, entity_id, properties=None, **k):
        self.updated.append((entity_id, properties))
        for e in self._entities:
            if e["id"] == entity_id:
                e["properties"] = properties
        return True

    async def add_relationship(self, src, tgt, rel_type, **k):
        self.linked.append((src, tgt, rel_type))
        return 1


def _ent(i, name, label):
    return {"id": i, "name": name, "entity_type": "entity",
            "properties": {"label": label}}


def _bridge(i, name, target, resolved=False):
    return {"id": i, "name": name, "entity_type": "product_bridge",
            "properties": {"label": name, "target_reference": target,
                           "target_resolved": resolved,
                           "resolution": f"target '{target}' not found in this graph"}}


def t_dangling_bridge_is_upgraded():
    """The order artefact: a bridge declared before its target existed."""
    store = FakeStore([
        _ent(1, "NIBRS_Source.Incident", "Incident"),
        _ent(2, "CASE_Source.Investigation", "Investigation"),
        _bridge(3, "NIBRS_Source.bridge:Incident To Investigation", "Investigation"),
    ])
    out = asyncio.run(reresolve_bridges(store))
    check("the dangling bridge was checked", out["checked"] == 1, str(out))
    check("it resolved once the other product was present", out["resolved"] == 1, str(out))
    check("the node was updated", len(store.updated) == 1)
    props = store.updated[0][1]
    check("target_resolved flipped true", props.get("target_resolved") is True)
    check("the resolution text says WHY it resolved late",
          "after the full selection" in (props.get("resolution") or ""),
          props.get("resolution"))
    check("an edge to the real entity was added",
          store.linked and store.linked[0][1] == 2, str(store.linked))


def t_unresolvable_stays_dangling():
    """A declared link whose target does not exist IS the finding — never invented."""
    store = FakeStore([
        _ent(1, "NIBRS_Source.Incident", "Incident"),
        _bridge(2, "CASE_Source.bridge:To Nowhere", "incident_Current"),
    ])
    out = asyncio.run(reresolve_bridges(store))
    check("checked but not resolved", out["checked"] == 1 and out["resolved"] == 0, str(out))
    check("nothing was written", store.updated == [] and store.linked == [])


def t_ambiguous_target_is_refused():
    """Two entities share the label — the walker's rule is unique-hit-or-nothing."""
    store = FakeStore([
        _ent(1, "A_Source.Incident", "Incident"),
        _ent(2, "B_Source.Incident", "Incident"),
        _bridge(3, "C_Source.bridge:X", "Incident"),
    ])
    out = asyncio.run(reresolve_bridges(store))
    check("ambiguity is not guessed", out["resolved"] == 0, str(out))
    check("nothing was linked arbitrarily", store.linked == [])


def t_already_resolved_is_left_alone():
    store = FakeStore([
        _ent(1, "NIBRS_Source.Incident", "Incident"),
        _bridge(2, "CASE_Source.bridge:ok", "Incident", resolved=True),
    ])
    out = asyncio.run(reresolve_bridges(store))
    check("a resolved bridge is not revisited", out["checked"] == 0, str(out))
    check("and not rewritten", store.updated == [])


def t_pass_never_fails_a_build():
    class Broken:
        async def list_entities(self, *a, **k):
            raise RuntimeError("backend down")

    out = asyncio.run(reresolve_bridges(Broken()))
    check("a broken store degrades quietly", out == {"checked": 0, "resolved": 0}, str(out))

    class HalfBroken(FakeStore):
        async def update_entity(self, *a, **k):
            raise RuntimeError("write failed")

    store = HalfBroken([
        _ent(1, "NIBRS_Source.Incident", "Incident"),
        _bridge(2, "CASE_Source.bridge:x", "Incident"),
    ])
    out = asyncio.run(reresolve_bridges(store))
    check("a failed write is not counted as resolved", out["resolved"] == 0, str(out))


def t_no_bridges_is_a_noop():
    store = FakeStore([_ent(1, "A.B", "B")])
    out = asyncio.run(reresolve_bridges(store))
    check("no bridges -> nothing checked", out == {"checked": 0, "resolved": 0}, str(out))


# ── the build-wide reporting rules ──────────────────────────────────────────
def t_aggregate_stats():
    """The response's stats must count every product built — the last-product
    shape read a CASE+NIBRS card as NIBRS-only."""
    rows = [
        {"product": "CASE", "status": "success",
         "stats": {"entities_logical": 15, "type_mismatches": 0,
                   "glossary_terms": 3, "semantic_database": "CASE_Semantic"}},
        {"product": "NIBRS", "status": "success",
         "stats": {"entities_logical": 26, "type_mismatches": 1425,
                   "glossary_terms": 5}},
    ]
    agg = aggregate_stats(rows)
    check("stats: numeric fields sum across products",
          agg["entities_logical"] == 41 and agg["glossary_terms"] == 8, agg)
    check("stats: a divergence count sums too (attribution is the card's job)",
          agg["type_mismatches"] == 1425, agg)
    check("stats: non-numeric fields are not summable and are dropped",
          "semantic_database" not in agg, agg)
    check("stats: single product == its own stats (numeric fields)",
          aggregate_stats(rows[1:]) == {"entities_logical": 26,
                                        "type_mismatches": 1425,
                                        "glossary_terms": 5})

    failed = rows + [{"product": "X", "status": "failed",
                      "stats": {"entities_logical": 999}}]
    check("stats: a FAILED product contributes nothing — its walk was discarded",
          aggregate_stats(failed)["entities_logical"] == 41)
    check("stats: booleans are not counts (isinstance(True, int) holds)",
          "flag" not in aggregate_stats([{"status": "success",
                                          "stats": {"flag": True}}]))
    check("stats: empty/malformed input -> {}",
          aggregate_stats([]) == {} and aggregate_stats([None, "x"]) == {})


def t_union_scope():
    """The trusted-scope line must state what the gate enforces: the WHOLE
    graph's scope, not the last product's."""
    rows = [
        {"product": "CASE", "status": "success",
         "scope_databases": ["CASE_Source_Domain"]},
        {"product": "NIBRS", "status": "success",
         "scope_databases": ["NIBRS_Source_Domain", "CASE_Source_Domain"]},
    ]
    check("scope: union of every built product, build order, deduped",
          union_scope(rows) == ["CASE_Source_Domain", "NIBRS_Source_Domain"],
          union_scope(rows))
    check("scope: a failed product's scope is not enforced and not shown",
          union_scope(rows + [{"status": "failed",
                               "scope_databases": ["GHOST_DB"]}])
          == ["CASE_Source_Domain", "NIBRS_Source_Domain"])
    check("scope: non-strings dropped, empty -> []",
          union_scope([{"status": "success", "scope_databases": [None, ""]}]) == []
          and union_scope([]) == [])


def t_response_wiring():
    """The endpoint must SERVE the build-wide fields — a rule that lives in this
    module but is bypassed at the response literal is decoration (the
    Python-rule-asserted-in-JS lesson, in reverse)."""
    import os
    p = os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                     "api", "rest_routes.py")
    body = open(p, encoding="utf-8").read()
    check("wiring: response stats are the aggregate",
          '_andp_aggregate_stats(per_product)' in body)
    check("wiring: response scope is the union",
          '_andp_union_scope(per_product)' in body)
    check("wiring: per-product entries carry their own stats",
          '"stats": step.stats' in body)


for fn in [t_overall_status, t_dangling_bridge_is_upgraded, t_unresolvable_stays_dangling,
           t_ambiguous_target_is_refused, t_already_resolved_is_left_alone,
           t_pass_never_fails_a_build, t_no_bridges_is_a_noop,
           t_aggregate_stats, t_union_scope, t_response_wiring]:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
