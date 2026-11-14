#!/usr/bin/env python3
"""Knowledge-graph `query` lookup resilience gate (CI-pure, fake store).

Production, 2026-09-04 (session 661402ae, turn 2): the agent passed its whole question
— "property crime offense codes multiple offenders incident trends" — as the lookup;
a substring match over names found nothing, the miss was a dead end ("No entities
found matching …") with no remedy, and it rendered as a dead card in the delivered
answer.  The component now absorbs the sentence: phrase first, then its terms ranked by
how many a node satisfies; a true miss is recovery-shaped and never a card.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from components.builtin.knowledge_graph import handler as kgh  # noqa: E402
from components.builtin.knowledge_graph.graph_store import GraphStore as _GS  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


ENTITIES = [
    {"id": 1, "name": "NIBRS.Incident", "entity_type": "entity", "properties": {"description": "One reported incident"}},
    {"id": 2, "name": "NIBRS.Incident.INCDATE", "entity_type": "attribute", "properties": {"description": "INCIDENT DATE", "deployed_type": "CHAR(11)"}},
    {"id": 3, "name": "NIBRS.Offense", "entity_type": "entity", "properties": {"description": "One offense within an incident"}},
    {"id": 4, "name": "NIBRS.Offense.V20061", "entity_type": "attribute", "properties": {"description": "UCR OFFENSE CODE", "allowed_values": {"220": "Burglary"}}},
    {"id": 5, "name": "NIBRS.Offender", "entity_type": "entity", "properties": {"description": "One offender of an incident"}},
    {"id": 6, "name": "NIBRS.Property", "entity_type": "entity", "properties": {"description": "Property involved in an offense"}},
    {"id": 7, "name": "NIBRS.Victim", "entity_type": "entity", "properties": {"description": "One victim"}},
]


class Store:
    """The GraphStore surface `query` uses, over a fixed fixture. search = substring
    over name + properties (what the SQLite backend's LIKE does), case-insensitive."""
    calls: list = []

    def __init__(self, entities=None, fail_search=False):
        self.entities = entities if entities is not None else ENTITIES
        self.fail_search = fail_search

    async def search_entities(self, q, limit=10, entity_type=None):
        Store.calls.append(q)
        if self.fail_search:
            raise RuntimeError("backend down")
        ql = q.lower()
        out = []
        for e in self.entities:
            if entity_type and e["entity_type"] != entity_type:
                continue
            hay = (e["name"] + " " + str(e.get("properties") or {})).lower()
            if ql in hay:
                out.append(e)
        return out[:limit]

    async def get_stats(self):
        types = {}
        for e in self.entities:
            types[e["entity_type"]] = types.get(e["entity_type"], 0) + 1
        return {"total_entities": len(self.entities), "entity_types": types}

    async def list_entities(self, entity_type=None, limit=200):
        return [e for e in self.entities if not entity_type or e["entity_type"] == entity_type][:limit]
# Pinned to the REAL GraphStore's calling convention (sync vs async), both ways —
# a double unfaithful in the dimension under test measures nothing (Sep 2026).
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__))); _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from kg_double_fidelity import double_mismatches as _dm
from components.builtin.knowledge_graph.graph_store import GraphStore as _RealGS
check("Store double matches the real GraphStore calling convention", not _dm(_RealGS, Store), _dm(_RealGS, Store))


print("[0] the fake mirrors GraphStore's calling convention in the dimension under test")
for m in ("search_entities", "get_stats", "list_entities"):
    check(f"{m} async on both", inspect.iscoroutinefunction(getattr(Store, m)) == inspect.iscoroutinefunction(getattr(_GS, m)))

cls = [v for k, v in vars(kgh).items() if inspect.isclass(v) and hasattr(v, "_handle_query")][0]


def handler(store):
    h = cls.__new__(cls)
    h._get_store = lambda ctx: store
    return h


def query(store, **args):
    Store.calls = []
    return asyncio.run(handler(store)._handle_query(args, {"user_uuid": "u1"}))


print("[1] a phrase that names nothing is searched by its terms")
r = query(Store(), query_text="property crime offense codes multiple offenders incident trends")
names = [e["name"] for e in r.metadata["entities"]]
check("the production sentence now returns entities", r.metadata["result_count"] > 0, names)
check("search mode says how the match was made", r.metadata["search_mode"] == "terms")
check("the terms are the meaningful words, noise dropped, bounded",
      r.metadata["query_terms"] == ["property", "crime", "offense", "codes", "offenders", "incident"], r.metadata["query_terms"])
check("nodes for the words the question is ABOUT are present",
      {"NIBRS.Offense", "NIBRS.Offender", "NIBRS.Incident", "NIBRS.Property"} <= set(names), names)
check("nodes satisfying two terms rank above nodes satisfying one",
      set(names[:3]) == {"NIBRS.Offender", "NIBRS.Offense", "NIBRS.Offense.V20061"}
      and names.index("NIBRS.Property") > 2 and names.index("NIBRS.Incident") > 2, names)
check("the singular of a plural term is searched too ('offenders' → 'offender')",
      any(c == "offender" for c in Store.calls), Store.calls)
check("the card says the phrase missed and which terms hit", "matching its terms" in r.html and "offense" in r.html)
check("a hit is not suppressed inline", not r.metadata.get("suppress_inline"))
check("the matched terms are reported", "offense" in r.metadata["matched_terms"] and "crime" not in r.metadata["matched_terms"])

print("[2] a phrase that names a node still matches as a phrase (no regression)")
r = query(Store(), query_text="Victim")
check("phrase mode, one hit", r.metadata["search_mode"] == "phrase" and [e["name"] for e in r.metadata["entities"]] == ["NIBRS.Victim"])
check("no term search was run", Store.calls == ["Victim"], Store.calls)

print("[3] a true miss is recovery-shaped, never a dead card")
r = query(Store(), query_text="zebra unicorn")
check("no entities", r.metadata["result_count"] == 0 and r.metadata["entities"] == [])
check("the card is suppressed inline", r.metadata.get("suppress_inline") is True)
check("the reply names what was tried", "'zebra unicorn'" in r.metadata["message"] and "zebra, unicorn" in r.metadata["message"])
check("the reply says the graph is NOT empty and how big", "NOT empty" in r.html and "7 entities" in r.html)
check("the reply says what a lookup matches", "entity NAMES" in r.html)
check("the reply offers real names as the next step", "NIBRS.Incident" in r.html)
check("the reply says the graph is supportive, never required", "never required" in r.html)
check("the message rides the metadata for the model", r.metadata["message"].startswith("No entity matches") and r.metadata["sample_names"])
r = query(Store(), query_text="zebra", entity_type="attribute")
check("a typed miss samples that type", all(".V" in n or "INCDATE" in n for n in r.metadata["sample_names"]), r.metadata["sample_names"])

print("[4] edge cases never raise")
r = query(Store(entities=[]), query_text="anything")
check("an empty graph says so", "empty" in r.html.lower() and r.metadata["graph_total_entities"] == 0)
r = query(Store(fail_search=True), query_text="offense codes")
check("a failing backend degrades to the miss reply, no exception", r.metadata["result_count"] == 0)
r = query(Store(), query_text="the and for")
check("a phrase of only stop words is a plain miss (no term search)", r.metadata["search_mode"] == "miss" and r.metadata["query_terms"] == [])
r = query(Store(), query_text="")
check("no query_text → the stats reply, as before", "stats" in r.metadata)

print("[5] the pure term helper")
check("noise and duplicates dropped, order kept, capped at 8",
      kgh._lookup_terms("incident incident trends over the last year offense a b c d e f g h") == ["incident", "year", "offense"]
      or len(kgh._lookup_terms("aaa bbb ccc ddd eee fff ggg hhh iii jjj")) == 8)
check("empty → []", kgh._lookup_terms("") == [] and kgh._lookup_terms(None) == [])

print("[6] a text-deployed DATE column carries the ordering trap in the enrichment")
parts = kgh._governance_parts({"label": "INCDATE", "description": "INCIDENT DATE", "deployed_type": "CHAR(11)",
                               "declared_type": "DATE"}, complete=True)
check("the date-as-text rule is rendered", any("STRING order, not date order" in p and "CAST(" in p for p in parts), parts)
parts = kgh._governance_parts({"label": "V4019", "description": "SEX OF VICTIM", "deployed_type": "VARCHAR(2)",
                               "allowed_values": {"0": "Female"}}, complete=True)
check("a non-date text column does not get it", not any("date order" in p for p in parts), parts)
parts = kgh._governance_parts({"label": "INCDATE", "description": "INCIDENT DATE", "deployed_type": "DATE"}, complete=True)
check("a date deployed as DATE does not get it", not any("date order" in p for p in parts), parts)
check("declared DATE alone is enough", kgh._looks_like_date_attr({"declared_type": "DATE"}))
check("a name ending in _dt is enough", kgh._looks_like_date_attr({"label": "arrest_dt"}) and not kgh._looks_like_date_attr({"label": "update"}))

print(f"\n{'ALL PASS' if failed == 0 else 'FAILURES'} — {passed + failed} assertions ({passed} passed, {failed} failed)")
sys.exit(1 if failed else 0)
