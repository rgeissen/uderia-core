"""Gate: the ontology content hash served at a KG content REVISION is always exact.

``composition.kg_graph_hash`` validates a cached hash with one row read of
``kg_content_rev`` — a counter that SQLite triggers bump on every write to a graph's rows
(``core/trust/kg_revision.py``). The cache is only acceptable if it can never serve a hash
the graph no longer has, so every check here compares against the uncached computation
(``kg_content_hash(*load_kg_graph(...))``) after a different kind of write — including the
ones the old fingerprint comment calls out: in-place updates that touch no timestamp, raw
SQL from another connection, and a table rebuild that silently drops the triggers.

Run:  PYTHONPATH=src python test/test_kg_revision.py
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.trust import composition as comp  # noqa: E402
from trusted_data_agent.core.trust import attestation as att  # noqa: E402
from trusted_data_agent.core.trust import kg_revision  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _db():
    p = os.path.join(tempfile.mkdtemp(), "kg.db")
    c = sqlite3.connect(p)
    c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE kg_entities (id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT,
            name TEXT, entity_type TEXT, properties_json TEXT DEFAULT '{}');
        CREATE TABLE kg_relationships (id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT,
            source_entity_id INTEGER, target_entity_id INTEGER, relationship_type TEXT);
        INSERT INTO kg_entities(kg_id, name, entity_type, properties_json) VALUES
            ('g1','Customer','table','{"rows":10}'), ('g1','Order','table','{"rows":20}'),
            ('g2','Other','table','{}');
        INSERT INTO kg_relationships(kg_id, source_entity_id, target_entity_id, relationship_type)
            VALUES ('g1', 1, 2, 'has');
    """)
    c.commit(); c.close()
    return p


def truth(kg, p):
    return comp.kg_content_hash(*comp.load_kg_graph(kg, p))


class _Reads:
    """Counts full-graph reads, so a 'hit' is proven to have read nothing."""
    def __enter__(self):
        self.n = 0
        self._orig = comp._fetch_kg_rows, comp._kg_fingerprint

        def fetch(*a, **k):
            self.n += 1; return self._orig[0](*a, **k)

        def fp(*a, **k):
            self.n += 1; return self._orig[1](*a, **k)
        comp._fetch_kg_rows, comp._kg_fingerprint = fetch, fp
        return self

    def __exit__(self, *exc):
        comp._fetch_kg_rows, comp._kg_fingerprint = self._orig


def raw(p, sql, args=()):
    c = sqlite3.connect(p); c.execute(sql, args); c.commit(); c.close()


def t_exact_across_writes():
    p = _db()
    h0, n0 = comp.kg_graph_hash("g1", p)
    check("first hash equals the uncached computation", h0 == truth("g1", p) and n0 == 2)
    with _Reads() as r:
        h1, _ = comp.kg_graph_hash("g1", p)
    check("an unchanged graph is served at its revision with ZERO graph reads",
          h1 == h0 and r.n == 0)

    raw(p, "UPDATE kg_entities SET properties_json='{\"rows\":11}' WHERE name='Customer'")
    h2, _ = comp.kg_graph_hash("g1", p)
    check("an in-place property update (no timestamp) moves the hash", h2 != h0)
    check("...to exactly the uncached value", h2 == truth("g1", p))

    raw(p, "INSERT INTO kg_entities(kg_id, name, entity_type) VALUES ('g1','Item','table')")
    raw(p, "INSERT INTO kg_relationships(kg_id, source_entity_id, target_entity_id, "
           "relationship_type) VALUES ('g1', 2, 4, 'contains')")
    h3, n3 = comp.kg_graph_hash("g1", p)
    check("inserted entity + edge are seen", h3 == truth("g1", p) and n3 == 3 and h3 != h2)

    raw(p, "UPDATE kg_relationships SET relationship_type='holds' WHERE relationship_type='contains'")
    check("an edge retyped in place is seen", comp.kg_graph_hash("g1", p)[0] == truth("g1", p))

    raw(p, "DELETE FROM kg_relationships WHERE relationship_type='holds'")
    raw(p, "DELETE FROM kg_entities WHERE name='Item'")
    h5, n5 = comp.kg_graph_hash("g1", p)
    check("deletes are seen (back to the earlier content)", h5 == h2 and n5 == 2)

    raw(p, "UPDATE kg_entities SET kg_id='g1' WHERE name='Other'")   # moved between graphs
    check("an entity moved INTO the graph is seen", comp.kg_graph_hash("g1", p)[0] == truth("g1", p))
    check("...and the graph it left is re-read too", comp.kg_graph_hash("g2", p)[0] == truth("g2", p))

    raw(p, "UPDATE kg_entities SET name='Client' WHERE name='Customer'")
    check("a rename is seen", comp.kg_graph_hash("g1", p)[0] == truth("g1", p))


def t_dropped_triggers_never_serve_stale():
    p = _db()
    comp.kg_graph_hash("g1", p)
    c = sqlite3.connect(p)
    for name in kg_revision.TRIGGERS:
        c.execute(f"DROP TRIGGER IF EXISTS {name}")
    c.commit()
    # A write while the triggers are gone is NOT counted by any revision …
    c.execute("UPDATE kg_entities SET properties_json='{\"rows\":99}' WHERE name='Customer'")
    c.commit(); c.close()
    h, _ = comp.kg_graph_hash("g1", p)
    check("a write made while the triggers were missing is still seen (epoch moved)",
          h == truth("g1", p))
    c = sqlite3.connect(p)
    have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    c.close()
    check("the missing triggers were re-created", set(kg_revision.TRIGGERS) <= have)


def t_cross_graph_edges_are_never_cached_at_a_revision():
    p = _db()
    # g3's edge points at an entity of g2 — a rename in g2 changes g3's hash (the join
    # reads the name) but bumps only g2's revision, so g3 must never be served by revision.
    raw(p, "INSERT INTO kg_entities(kg_id, name, entity_type) VALUES ('g3','Local','table')")
    raw(p, "INSERT INTO kg_relationships(kg_id, source_entity_id, target_entity_id, "
           "relationship_type) VALUES ('g3', 4, 3, 'refers')")
    h0, _ = comp.kg_graph_hash("g3", p)
    raw(p, "UPDATE kg_entities SET name='Renamed' WHERE kg_id='g2'")
    h1, _ = comp.kg_graph_hash("g3", p)
    check("a foreign endpoint renamed in ANOTHER graph is seen", h1 == truth("g3", p) and h1 != h0)


def t_persisted_and_code_versioned():
    p = _db()
    h0, _ = comp.kg_graph_hash("g1", p)
    comp._KG_REV_CACHE.clear()
    with _Reads() as r:
        h1, n1 = comp.kg_graph_hash("g1", p)
    check("a restart (empty memory) is served from the persisted row with zero graph reads",
          h1 == h0 and n1 == 2 and r.n == 0)
    comp._KG_REV_CACHE.clear()
    saved = comp._KG_HASH_CODE_VERSION
    comp._KG_HASH_CODE_VERSION = "a-different-build"
    try:
        with _Reads() as r:
            h2, _ = comp.kg_graph_hash("g1", p)
        check("a persisted hash from different hashing code is NOT used", r.n > 0 and h2 == h0)
    finally:
        comp._KG_HASH_CODE_VERSION = saved
        comp._KG_REV_CACHE.clear()


def t_capture_sees_the_bytes():
    p = _db()
    h, _ = comp.kg_graph_hash("g1", p)   # warm every cache
    cap = att.ContentCapture(limit=None)
    with att.content_capture(cap):
        hc, _ = comp.kg_graph_hash("g1", p)
    check("under a content capture the hashed bytes are recorded (caches bypassed)",
          hc == h and h in cap.seen)


def t_degrades_without_kg_tables():
    p = os.path.join(tempfile.mkdtemp(), "empty.db")
    sqlite3.connect(p).close()
    c = sqlite3.connect(p)
    check("a database without KG tables yields no revision (fingerprint path)",
          kg_revision.revision(c, p, "g1") is None)
    c.close()


def t_extension_summary_at_revision():
    from trusted_data_agent.core.trust import extensions as X
    from trusted_data_agent.kgstore import types as T
    import json as _json
    p = _db()
    pub = {"layer": T.AUTHORED_LAYER, T.EXTENSION_ID_PROP: "ext-u1", "status": T.AUTHORED_STATUS_PUBLISHED}
    raw(p, "INSERT INTO kg_entities(kg_id, name, entity_type, properties_json) VALUES ('g1','Lead','entity',?)",
        (_json.dumps(pub),))

    def truth_summary():
        ents, rels = comp.load_kg_graph("g1", p)
        return {e: {**c, "content_hash": X.extension_content_hash(ents, rels, e)}
                for e, c in X.extensions_on_graph(ents).items()}
    s0 = X.graph_summary("g1", p)
    check("the summary equals the uncached computation", s0 == truth_summary() and s0["ext-u1"]["published"] == 1)
    with _Reads() as r:
        s1 = X.graph_summary("g1", p)
    check("an unchanged graph's summary is served with ZERO graph reads", s1 == s0 and r.n == 0)
    raw(p, "UPDATE kg_entities SET properties_json=? WHERE name='Lead'",
        (_json.dumps({**pub, "label": "a lead"}),))
    s2 = X.graph_summary("g1", p)
    check("an edited extension node moves its content hash", s2 == truth_summary()
          and s2["ext-u1"]["content_hash"] != s0["ext-u1"]["content_hash"])
    kg_revision._MEMO.clear()
    with _Reads() as r:
        s3 = X.graph_summary("g1", p)
    check("a restart (empty memory) is served from kg_content_memo with zero graph reads",
          s3 == s2 and r.n == 0)
    kg_revision._MEMO.clear()
    saved = X._SUMMARY_CODE_VER
    X._SUMMARY_CODE_VER = "another-build"
    try:
        with _Reads() as r:
            s4 = X.graph_summary("g1", p)
        check("a summary persisted by different code is recomputed", r.n > 0 and s4 == s2)
    finally:
        X._SUMMARY_CODE_VER = saved
    cap = att.ContentCapture(limit=None)
    with att.content_capture(cap):
        X.graph_summary("g1", p)
    check("under a content capture the extension's hashed bytes are recorded",
          s2["ext-u1"]["content_hash"] in cap.seen)


def t_card_stats_at_revision():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from components.builtin.knowledge_graph.graph_store import GraphStore
    p = _db()

    def stats():
        c = sqlite3.connect(p)
        try:
            return GraphStore._graph_stats(c, "g1", p)
        finally:
            c.close()

    def truth():
        kg_revision._MEMO.clear()
        c = sqlite3.connect(p)
        try:
            c.execute("DELETE FROM kg_content_memo")
            c.commit()
            return GraphStore._graph_stats(c, "g1", p)
        finally:
            c.close()
    s0 = stats()
    check("card stats count the graph", s0["entity_types"] == {"table": 2} and s0["rel_count"] == 1)
    raw(p, "INSERT INTO kg_entities(kg_id, name, entity_type) VALUES ('g1','Item','table')")
    s1 = stats()
    check("a new entity is counted at the next revision", s1["entity_types"] == {"table": 3} and s1 == truth())
    raw(p, "INSERT INTO kg_entities(kg_id, name, entity_type, properties_json) VALUES "
           "('g1','Draft','entity','{\"layer\": \"authored\", \"status\": \"draft\"}')")
    s2 = stats()
    check("a draft is counted apart, exactly as an uncached read", s2 == truth() and s2["drafts"] == 1)


def main():
    for t in (t_exact_across_writes, t_dropped_triggers_never_serve_stale,
              t_cross_graph_edges_are_never_cached_at_a_revision,
              t_persisted_and_code_versioned, t_capture_sees_the_bytes,
              t_extension_summary_at_revision, t_card_stats_at_revision,
              t_degrades_without_kg_tables):
        print(f"\n{t.__name__}:")
        t()
    print(f"\n{'=' * 52}\nkg_revision: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
