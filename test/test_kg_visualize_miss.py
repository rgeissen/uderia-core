"""
CI-pure tests: a KG lookup that matches nothing must not be reported as an empty graph.

Observed live on a graph holding 3,867 entities. The agent asked to visualise an
exhibit identifier — a VALUE inside the data, not an entity NAME — and the component
replied "Knowledge graph is empty. Use 'add_entity' to populate it". Two harms, one
message: the agent stopped trusting the enrichment it had already been handed and
brute-forced the rest of the turn, and the operator reading the transcript was told
something untrue about their own deployment.

The two cases need OPPOSITE responses — populate it, versus use a different name — so
the fix is to tell them apart and make the miss recovery-shaped.

Run: python3 test/test_kg_visualize_miss.py
"""
import asyncio
import os
import sys

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


class FakeStore:
    """Minimal store: only what the miss path reads."""

    def __init__(self, total=0, types=None, names=None, stats_raises=False,
                 list_raises=False, ignores_limit=False):
        self._total, self._types = total, types or {}
        self._names = names or []
        self._stats_raises, self._list_raises = stats_raises, list_raises
        # a backend that ignores `limit` is exactly what the caller's own slice
        # defends against; without this the slice is an untestable no-op.
        self._ignores_limit = ignores_limit

    async def get_stats(self):
        if self._stats_raises:
            raise RuntimeError("backend down")
        return {"total_entities": self._total, "entity_types": self._types}

    async def list_entities(self, limit=10):
        if self._list_raises:
            raise RuntimeError("backend down")
        rows = self._names if self._ignores_limit else self._names[:limit]
        return [{"name": n} for n in rows]
# Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
# a double unfaithful in the dimension under test measures nothing (Sep 2026).
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from kg_double_fidelity import double_mismatches as _dm
from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
check("FakeStore double matches the real GraphStore calling convention", not _dm(_RealGS, FakeStore), _dm(_RealGS, FakeStore))


def _miss(store, entity_name):
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as H
    return asyncio.run(H._visualize_nothing_to_show(H, store, entity_name))


POPULATED = dict(total=3867, types={"entity": 73, "attribute": 1822},
                 names=["CASE_Source.Provenance Record", "CASE_Source.Object Node"])


def t_populated_graph_is_never_called_empty():
    p = _miss(FakeStore(**POPULATED), "20181119-001-001-01")
    check("a populated graph is not reported as empty",
          "is empty" not in p.html, p.html)
    check("it says so explicitly", "NOT empty" in p.html, p.html)
    check("and states the real size", "3867" in p.html, p.html)
    check("it does not tell the operator to populate a full graph",
          "add_entity" not in p.html, p.html)


def t_miss_names_the_thing_that_missed():
    p = _miss(FakeStore(**POPULATED), "20181119-001-001-01")
    check("the requested name is quoted back", "20181119-001-001-01" in p.html, p.html)
    check("the entity types are offered", "attribute (1822)" in p.html, p.html)
    check("real names are offered as a remedy",
          "CASE_Source.Provenance Record" in p.html, p.html)
    check("the reply is recovery-shaped", "NEXT STEP" in p.html, p.html)


def t_truly_empty_graph_still_says_so():
    p = _miss(FakeStore(total=0), "anything")
    check("an empty graph is still called empty", "is empty" in p.html, p.html)
    check("and populating it is still the offered remedy",
          "add_entity" in p.html, p.html)


def t_no_entity_name_is_not_a_miss():
    # visualize with no name on an empty graph: the empty message is correct.
    p = _miss(FakeStore(total=0), None)
    check("no name + empty graph reads as empty", "is empty" in p.html, p.html)
    # visualize with no name on a populated graph that drew nothing: claim neither.
    p2 = _miss(FakeStore(**POPULATED), None)
    check("no name + populated graph claims neither emptiness nor content",
          "is empty" not in p2.html and "NOT empty" not in p2.html, p2.html)


def t_unreadable_backend_degrades_without_lying():
    p = _miss(FakeStore(total=5, stats_raises=True), "x")
    check("an unreadable graph is not declared empty", "is empty" not in p.html, p.html)
    check("nor declared populated", "NOT empty" not in p.html, p.html)
    p2 = _miss(FakeStore(**dict(POPULATED, list_raises=True)), "x")
    check("a failed sample still yields the size and types",
          "3867" in p2.html and "NEXT STEP" in p2.html, p2.html)


def t_metadata_carries_the_truth():
    p = _miss(FakeStore(**POPULATED), "nope")
    m = p.metadata
    check("metadata records the real graph size",
          m.get("graph_total_entities") == 3867, m)
    check("and that nothing matched", m.get("matched") is False, m)
    check("and what was asked for", m.get("requested_entity") == "nope", m)
    check("node_count stays zero", m.get("node_count") == 0, m)


def t_sample_is_bounded():
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler as H
    many = [f"E{i}" for i in range(200)]
    p = _miss(FakeStore(total=200, types={"entity": 200}, names=many,
                        ignores_limit=True), "nope")
    listed = sum(1 for n in many if n + "," in p.html or n + "." in p.html)
    check("the offered sample is bounded", listed <= H._MISS_SAMPLE,
          f"{listed} names listed")


def t_hostile_name_is_escaped():
    p = _miss(FakeStore(**POPULATED), "<script>alert(1)</script>")
    check("a hostile requested name cannot inject markup",
          "<script>" not in p.html, p.html[:200])


for fn in [t_populated_graph_is_never_called_empty, t_miss_names_the_thing_that_missed,
           t_truly_empty_graph_still_says_so, t_no_entity_name_is_not_a_miss,
           t_unreadable_backend_degrades_without_lying, t_metadata_carries_the_truth,
           t_sample_is_bounded, t_hostile_name_is_escaped]:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — a raising test is a NAMED failure
        FAIL += 1
        FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

print(f"\n{PASS} passed, {FAIL} failed")
for f in FAILURES:
    print("  FAIL:", f)
sys.exit(1 if FAIL else 0)
