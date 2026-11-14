import os
"""update_in_place brings a consumer's pack-owned knowledge graphs in line with the
manifest — in place where it can, and topology-aware where it must (2026-09-04).

A consumer's graph is a frozen copy with no source of its own; the pack is its source.
The first version refreshed "the graph active for the shipped profile" and stopped —
correct while a pack ships one graph per profile, and silently destructive the day a
pack SPLITS one shared graph into several: both entries resolved to the same consumer
graph, the second refresh overwrote the first, both analysts stayed active on a graph
holding one product, and `kgs_refreshed: 2` was reported.

Rules pinned here, each with the mutation that would reintroduce a defect:
  1. one graph per profile, unchanged topology: refreshed in place, id kept, clear
     precedes import (the original contract);
  2. SPLIT: the shared graph stays with the entry the manifest names as its owner (else
     the first in manifest order); every other entry gets a graph of its own under its
     manifest owner, and the profile's ACTIVE assignment moves onto it;
  3. MERGE: the surviving entry's assigned_profiles pull the other profiles onto the kept
     graph; the pack graph they left is kept, inactive, and reported;
  4. a consumer's own graph (not the pack's) is never displaced and never touched;
  5. a profile's rows to pack graphs the manifest no longer assigns it to are removed;
  6. the assignments table is unique on (owner, assigned, user), not on kg_id — a row
     for the pair that points at another PACK graph is re-pointed, never duplicated;
  7. the created graphs are recorded as pack resources and reported.
Real sqlite for kg_metadata + kg_profile_assignments (the exact live shape incl. the
partial unique index); a fake store for the graph content.
"""
import ast, asyncio, json, os, pathlib, sqlite3, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from trusted_data_agent.core.agent_pack_manager import AgentPackManager

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

U = "u-1"
T2P = {"NIBRS": "p-n", "CASEDF": "p-c", "PDOCS": "p-d", "POLICE": "p-p"}


def _db():
    path = os.path.join(tempfile.mkdtemp(), "auth.db")
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE kg_metadata (kg_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, user_uuid TEXT NOT NULL,
        name TEXT NOT NULL, database_name TEXT, description TEXT, is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE kg_profile_assignments (id INTEGER PRIMARY KEY AUTOINCREMENT, kg_id TEXT,
        kg_owner_profile_id TEXT NOT NULL, assigned_profile_id TEXT NOT NULL, user_uuid TEXT NOT NULL,
        is_active BOOLEAN DEFAULT 0 CHECK(is_active IN (0, 1)),
        UNIQUE(kg_owner_profile_id, assigned_profile_id, user_uuid));
    CREATE UNIQUE INDEX idx_kg_one_active_per_profile
        ON kg_profile_assignments(assigned_profile_id, user_uuid) WHERE is_active = 1;
    """)
    c.commit(); c.close()
    return path


def _graph(path, kg_id, owner, name, assigned, active_meta=1):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO kg_metadata VALUES (?,?,?,?,NULL,NULL,?,'t','t')", (kg_id, owner, U, name, active_meta))
    for apid, act in assigned:
        c.execute("INSERT INTO kg_profile_assignments (kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active) "
                  "VALUES (?,?,?,?,?)", (kg_id, owner, apid, U, act))
    c.commit(); c.close()


def _rows(path):
    c = sqlite3.connect(path)
    r = sorted(tuple(x) for x in c.execute("SELECT kg_id, kg_owner_profile_id, assigned_profile_id, is_active FROM kg_profile_assignments"))
    m = sorted(tuple(x) for x in c.execute("SELECT kg_id, profile_id, is_active FROM kg_metadata"))
    c.close(); return r, m


class FakeStore:
    calls = []
    db_path = None
    def __init__(self, profile_id, user_uuid, kg_id): self.profile_id, self.kg_id = profile_id, kg_id
    async def list_entities(self, limit=0): return [{"name": "old"}]
    async def list_relationships(self, limit=0): return []
    def clear_graph(self): FakeStore.calls.append(("clear", self.kg_id)); return {}
    def import_bulk(self, entities, relationships):
        FakeStore.calls.append(("import", self.kg_id, len(entities))); return {"entities_added": len(entities), "relationships_added": len(relationships)}
    def replace_model_layer(self, entities, relationships, keep_layers=None):
        # PROP-008 A1: an in-place refresh is ONE replace (the consumer's authored layer kept)
        FakeStore.calls.append(("replace", self.kg_id, len(entities))); return {"entities_added": len(entities), "relationships_added": len(relationships)}
    def set_source_validation(self, summary, validated_at=None):
        FakeStore.calls.append(("stamp", self.kg_id, summary, validated_at)); return True
    def set_kg_metadata(self, name, database_name=None, description=None, is_active=True):
        # the real store deactivates the owner's other graphs when activating — mirrored
        c = sqlite3.connect(FakeStore.db_path)
        if is_active:
            c.execute("UPDATE kg_metadata SET is_active=0 WHERE profile_id=? AND user_uuid=?", (self.profile_id, U))
        c.execute("INSERT OR REPLACE INTO kg_metadata VALUES (?,?,?,?,?,?,?,'t','t')",
                  (self.kg_id, self.profile_id, U, name, database_name, description, 1 if is_active else 0))
        c.commit(); c.close()
        FakeStore.calls.append(("meta", self.kg_id, self.profile_id, bool(is_active)))


def _manager(path):
    m = AgentPackManager.__new__(AgentPackManager)
    m.db_path = path; m._graph_store_cls = FakeStore
    FakeStore.calls = []; FakeStore.db_path = path
    return m


def _pack(entries):
    tmp = pathlib.Path(tempfile.mkdtemp())
    for i, e in enumerate(entries):
        e.setdefault("ref", f"kg_{i+1}"); e.setdefault("file", f"{e['ref']}.json")
        payload = {"name": e.get("name"), "entities": [{"name": f"{e['ref']}-n"}] * (i + 2), "relationships": []}
        payload.update(e.pop("kg_data", {}))
        (tmp / e["file"]).write_text(json.dumps(payload))
    return {"knowledge_graphs": entries}, tmp


def _run(m, manifest, tmp, owned, warnings=None):
    warnings = [] if warnings is None else warnings
    res = asyncio.run(m._refresh_pack_kgs_in_place(
        manifest, tmp, T2P, [{"resource_type": "knowledge_graph", "resource_id": k} for k in owned], U, warnings))
    return res, warnings


COMBINED = [{"name": "Combined", "profile_tag": "NIBRS", "owner_profile_tag": "NIBRS",
             "assigned_profiles": [{"tag": "NIBRS", "is_active": True}, {"tag": "CASEDF", "is_active": True}]}]
SPLIT = [{"name": "N", "profile_tag": "NIBRS", "owner_profile_tag": "NIBRS", "assigned_profiles": [{"tag": "NIBRS", "is_active": True}]},
         {"name": "C", "profile_tag": "CASEDF", "owner_profile_tag": "CASEDF", "assigned_profiles": [{"tag": "CASEDF", "is_active": True}]}]


def t_pick():
    check("the active pack-owned graph wins", AgentPackManager._pick_pack_kg([("a", 0), ("b", 1)], {"a", "b"}) == "b", None)
    check("an owned inactive graph is used when none is active", AgentPackManager._pick_pack_kg([("a", 0)], {"a"}) == "a", None)
    check("a graph the pack does not own is never chosen", AgentPackManager._pick_pack_kg([("x", 1)], {"a"}) is None, None)


def t_keepers():
    plans = [{"ref": "kg_1", "kg_id": "G", "owner_pid": "p-c"}, {"ref": "kg_2", "kg_id": "G", "owner_pid": "p-n"}]
    check("the entry whose manifest owner owns the graph keeps it, whatever its order", AgentPackManager._split_keepers(plans, {"G": "p-n"}) == {"G": "kg_2"}, None)
    check("no owner match: the first entry in manifest order keeps it", AgentPackManager._split_keepers(plans, {"G": "p-x"}) == {"G": "kg_1"}, None)
    check("entries on different graphs each keep their own", AgentPackManager._split_keepers(
        [{"ref": "a", "kg_id": "G", "owner_pid": "p-n"}, {"ref": "b", "kg_id": "H", "owner_pid": "p-c"}], {}) == {"G": "a", "H": "b"}, None)


def t_unchanged_topology():
    path = _db(); _graph(path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    m = _manager(path); manifest, tmp = _pack([dict(e) for e in COMBINED])
    res, w = _run(m, manifest, tmp, {"G"})
    check("one graph per profile is refreshed in place", res["refreshed"] == 1 and res["created"] == [] and res["moved"] == 0, (res, w))
    check("the id is kept; ONE replace (never clear + import), the stamp follows", FakeStore.calls == [("replace", "G", 2), ("stamp", "G", None, None)], FakeStore.calls)
    rows, meta = _rows(path)
    check("assignments are untouched when the manifest agrees with them", rows == [("G", "p-n", "p-c", 1), ("G", "p-n", "p-n", 1)], rows)
    check("no warning on the clean case", w == [], w)
    c = sqlite3.connect(path); nm = c.execute("SELECT name FROM kg_metadata WHERE kg_id='G'").fetchone()[0]; c.close()
    check("the refreshed graph's name follows the manifest", nm == "Combined", nm)


def t_split():
    path = _db(); _graph(path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    m = _manager(path); manifest, tmp = _pack([dict(e) for e in SPLIT])
    res, w = _run(m, manifest, tmp, {"G"})
    check("the shared graph is refreshed once and the other entry gets a graph of its own", res["refreshed"] == 1 and len(res["created"]) == 1 and res["moved"] == 1, (res, w))
    new = res["created"][0]
    rows, meta = _rows(path)
    check("the split graph is owned by its manifest owner and active for it (metadata)", (new, "p-c", 1) in meta, meta)
    check("the moved profile is ACTIVE on its new graph and no longer on the shared one",
          rows == sorted([("G", "p-n", "p-n", 1), (new, "p-c", "p-c", 1)]), rows)
    check("the kept graph refreshed, the new one imported under its own id",
          ("replace", "G", 2) in FakeStore.calls and ("import", new, 3) in FakeStore.calls
          and not any(c[0] in ("clear", "replace") and c[1] == new for c in FakeStore.calls), FakeStore.calls)
    check("neither graph is reported orphaned", not any("no pack profile is active" in x for x in w), w)
    c = sqlite3.connect(path); names = dict(c.execute("SELECT kg_id, name FROM kg_metadata")); c.close()
    check("after the split the kept graph is renamed to what it now holds, the new one named from its entry",
          names["G"] == "N" and names[new] == "C" and res["names"] == {"G": "N", new: "C"}, (names, res))


def t_split_order_independent():
    path = _db(); _graph(path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    m = _manager(path); manifest, tmp = _pack([dict(SPLIT[1]), dict(SPLIT[0])])  # CASEDF entry first
    res, w = _run(m, manifest, tmp, {"G"})
    rows, meta = _rows(path)
    new = res["created"][0]
    check("the graph stays with its owner's entry even when that entry is listed second",
          ("G", "p-n", "p-n", 1) in rows and (new, "p-c", "p-c", 1) in rows and (new, "p-c", 1) in meta, (rows, meta))


def t_split_orphan_owner():
    # the local dev estate: the combined graph's owner is a profile the pack no longer ships
    path = _db(); _graph(path, "G", "p-x", "Combined", [("p-x", 1), ("p-n", 1), ("p-c", 1)])
    m = _manager(path); manifest, tmp = _pack([dict(e) for e in SPLIT])
    res, w = _run(m, manifest, tmp, {"G"})
    rows, _ = _rows(path)
    new = res["created"][0]
    check("no owner match: the first entry keeps the graph, the second is split off",
          res["refreshed"] == 1 and ("G", "p-x", "p-n", 1) in rows and (new, "p-c", "p-c", 1) in rows and ("G", "p-x", "p-c", 1) not in rows, (res, rows))
    check("a profile the pack does not ship keeps its row", ("G", "p-x", "p-x", 1) in rows, rows)


def t_merge():
    path = _db(); _graph(path, "N", "p-n", "N", [("p-n", 1)]); _graph(path, "C", "p-c", "C", [("p-c", 1)])
    m = _manager(path); manifest, tmp = _pack([dict(e) for e in COMBINED])
    res, w = _run(m, manifest, tmp, {"N", "C"})
    rows, _ = _rows(path)
    check("merge: the surviving entry's graph is refreshed and the other profile moves onto it",
          res["refreshed"] == 1 and res["created"] == [] and res["moved"] == 1 and rows == [("N", "p-n", "p-c", 1), ("N", "p-n", "p-n", 1)], (res, rows))
    check("the graph the profile left is kept and reported, not deleted", any("KG C" in x and "kept" in x for x in w), w)


def t_consumer_graph_never_displaced():
    path = _db(); _graph(path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 0)]); _graph(path, "X", "p-c", "Mine", [("p-c", 1)])
    m = _manager(path); manifest, tmp = _pack([dict(e) for e in SPLIT])
    res, w = _run(m, manifest, tmp, {"G"})
    rows, meta = _rows(path)
    new = res["created"][0]
    check("a profile active on its OWN graph is not moved by the pack", ("X", "p-c", "p-c", 1) in rows and res["moved"] == 0, (rows, res))
    # the table is unique on (owner, assigned): the consumer's own graph X is owned by p-c,
    # so a second p-c-owned graph cannot be assigned to p-c at all — the honest outcome is
    # no row, the graph created inactive, and both facts said rather than a silent overwrite
    check("the pair's existing row to the consumer's graph is not re-pointed, and the refusal is named",
          not any(r[0] == new for r in rows) and any("keeps its own assignment" in x for x in w), (rows, w))
    check("the split graph is created inactive because its owner already grounds elsewhere, and says so",
          (new, "p-c", 0) in meta and any("created inactive" in x for x in w), (meta, w))
    check("the consumer's graph content was never touched", not any(c[1] == "X" for c in FakeStore.calls), FakeStore.calls)


def t_consumer_graph_other_owner_never_displaced():
    # the consumer grounds CASEDF on a graph of ANOTHER owner (so the pair row for the split
    # graph CAN exist) — this is the scenario where only the activation guard stands
    # between the pack and the consumer's own choice
    path = _db(); _graph(path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 0)]); _graph(path, "X", "p-y", "Mine", [("p-y", 1), ("p-c", 1)])
    m = _manager(path); manifest, tmp = _pack([dict(e) for e in SPLIT])
    res, w = _run(m, manifest, tmp, {"G"})
    rows, _ = _rows(path)
    new = res["created"][0]
    check("the split graph is assigned to its profile but NOT activated over the consumer's own graph",
          ("X", "p-y", "p-c", 1) in rows and (new, "p-c", "p-c", 0) in rows and res["moved"] == 0, (rows, res))
    check("the consumer's graph content was never touched", not any(c[1] == "X" for c in FakeStore.calls), FakeStore.calls)


def t_repoint_same_owner():
    # the pair (owner, assigned) is unique: a second graph under the SAME owner re-points the row
    path = _db(); _graph(path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    m = _manager(path)
    entries = [dict(SPLIT[0]), dict(SPLIT[1], owner_profile_tag="NIBRS")]
    manifest, tmp = _pack(entries)
    res, w = _run(m, manifest, tmp, {"G"})
    rows, _ = _rows(path)
    new = res["created"][0]
    check("the existing (owner, assigned) row is re-pointed at the split graph, never duplicated",
          rows == sorted([("G", "p-n", "p-n", 1), (new, "p-n", "p-c", 1)]), rows)


def t_missing_and_noop():
    path = _db(); _graph(path, "G", "p-n", "Combined", [("p-n", 1)])
    m = _manager(path)
    manifest, tmp = _pack([dict(COMBINED[0])]); manifest["knowledge_graphs"].append({"name": "M", "profile_tag": "NIBRS", "file": "missing.json"})
    res, w = _run(m, manifest, tmp, {"G"})
    check("a missing file is a warning, not an abort", res["refreshed"] == 1 and any("missing" in x for x in w), (res, w))
    EMPTY = {"refreshed": 0, "created": [], "moved": 0, "names": {}}
    check("no manifest graphs is a no-op", asyncio.run(m._refresh_pack_kgs_in_place({}, tmp, {}, [], U, [])) == EMPTY, None)
    m2 = _manager(_db()); manifest, tmp = _pack([dict(e) for e in SPLIT])
    res, w = _run(m2, manifest, tmp, set())
    check("a consumer with no pack graph at all is left alone", res == EMPTY and FakeStore.calls == [], (res, FakeStore.calls))


SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent/core/agent_pack_manager.py"
def t_validation_stamp():
    """The stamp follows the pack's content: the publisher's stamp verbatim (its timestamp
    kept), else derived from stamped nodes, else CLEARED — on the refresh AND the split-create path."""
    def stamps(kg=None):
        return [c for c in FakeStore.calls if c[0] == "stamp" and (kg is None or c[1] == kg)]
    carried = {"validated_at": "2026-09-05T07:59:27+00:00", "tables": 35, "keys_checked": 26, "keys_not_unique": 0,
               "shapes": 42, "date_ranges": 38, "blank_ratios": 1342, "probe_errors": 0, "seconds": 280.5}
    m = _manager(_db()); _graph(m.db_path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    manifest, tmp = _pack([dict(COMBINED[0], kg_data={"source_validation": carried})])
    _run(m, manifest, tmp, ["G"])
    st = stamps("G")
    check("a carried stamp is applied after the import with the PUBLISHER's timestamp",
          st == [("stamp", "G", {k: v for k, v in carried.items() if k != "validated_at"}, carried["validated_at"])], st)
    check("the stamp lands after the replace, never before", FakeStore.calls.index(("replace", "G", 2)) < FakeStore.calls.index(st[0]), FakeStore.calls)
    m = _manager(_db()); _graph(m.db_path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    ents = [{"name": "t", "properties": {"row_count": 12, "key_unique": True}}, {"name": "c", "properties": {"sampled_shape": "DD-MMM-YYYY"}}]
    manifest, tmp = _pack([dict(COMBINED[0], kg_data={"entities": ents})])
    _run(m, manifest, tmp, ["G"])
    st = stamps("G")
    check("no carried stamp but stamped nodes → the stamp is DERIVED from the content (timestamp = now)",
          len(st) == 1 and st[0][2] == {"tables": 1, "keys_checked": 1, "keys_not_unique": 0, "shapes": 1, "date_ranges": 0, "blank_ratios": 0} and st[0][3] is None, st)
    m = _manager(_db()); _graph(m.db_path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    manifest, tmp = _pack([dict(COMBINED[0])])
    _run(m, manifest, tmp, ["G"])
    check("an unvalidated pack CLEARS the stamp (never a stale badge)", stamps("G") == [("stamp", "G", None, None)], stamps("G"))
    m = _manager(_db()); _graph(m.db_path, "G", "p-n", "Combined", [("p-n", 1), ("p-c", 1)])
    manifest, tmp = _pack([dict(SPLIT[0], kg_data={"source_validation": carried}), dict(SPLIT[1], kg_data={"source_validation": dict(carried, tables=38)})])
    res, _ = _run(m, manifest, tmp, ["G"])
    new = res["created"][0]
    check("the split-off graph is stamped too, with its own entry's stamp",
          any(c[1] == new and c[2] and c[2].get("tables") == 38 and c[3] == carried["validated_at"] for c in stamps()), stamps())


def t_wiring():
    src = SRC.read_text()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_handle_update_in_place")
    body = ast.get_source_segment(src, fn)
    check("update_in_place refreshes graphs through the one method", "_refresh_pack_kgs_in_place(" in body, None)
    check("every refreshed or split-off graph is recorded as a pack resource under its current name",
          '"resource_type": "knowledge_graph"' in body and 'kg_result["names"].items()' in body[:body.index("_carry_forward_resources")], None)
    check("the response reports created and moved graphs", '"kgs_created": len(kgs_created)' in body and '"kgs_moved"' in body, None)
    check("resources are reconciled BEFORE the attestations are reinstated (a created graph must be a member first)",
          body.index("reconcile_pack_resources(") < body.index("_reinstate_member_trust("), None)
    tree = ast.parse(src)
    def seg(name, kind=(ast.FunctionDef, ast.AsyncFunctionDef)):
        return ast.get_source_segment(src, next(n for n in ast.walk(tree) if isinstance(n, kind) and n.name == name))
    for fn_name in ("_refresh_pack_kgs_in_place", "_create_pack_kg"):
        b = seg(fn_name)
        # the refresh path writes through replace_model_layer (PROP-008 A1), the create path through import_bulk
        _write = "replace_model_layer(" if "replace_model_layer(" in b else "import_bulk("
        check(f"{fn_name} stamps the validation AFTER its write", "_stamp_pack_kg_validation(" in b and b.index(_write) < b.index("_stamp_pack_kg_validation("), None)
    fresh = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and "_bulk_result = _store.import_bulk(" in (ast.get_source_segment(src, n) or ""))
    fb = ast.get_source_segment(src, fresh)
    check("the fresh-install path stamps the validation after its import_bulk",
          fb.index("_bulk_result = _store.import_bulk(") < fb.index("self._stamp_pack_kg_validation(_store, _kg_data)"), fresh.name)
    check("the export carries the publisher's stamp in the graph file", '"source_validation": _kg_validation' in src and "get_source_validation()" in src, None)


for fn in (t_pick, t_keepers, t_unchanged_topology, t_split, t_split_order_independent, t_split_orphan_owner,
           t_merge, t_consumer_graph_never_displaced, t_consumer_graph_other_owner_never_displaced, t_repoint_same_owner, t_missing_and_noop,
           t_validation_stamp, t_wiring):
    try: fn()
    except Exception as exc:
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")
# ── a graph that grounds MORE THAN ONE agent still resolves on the consumer ──
# Regression, found live 2026-09-08. `profile_tag` is ONE tag — whichever assigned
# profile the export walked first. When the policing pack began grounding its Optimize
# twin on the NIBRS ontology, that tag could name the twin; a consumer with no
# assignment for the twin then resolved the entry to nothing, skipped it, and
# `_prune_pack_kg_assignments` deleted the consumer's REAL binding. An in-place update
# left @NIBRS with no ontology at all.
SRC = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent",
                        "core", "agent_pack_manager.py"), encoding="utf-8").read()
_i = SRC.index("async def _refresh_pack_kgs_in_place")
_body = SRC[_i:_i + 9000]
_owner_first = "for _tag in ([entry.get(\"owner_profile_tag\"), entry.get(\"profile_tag\")]"
check("the refresh resolves a manifest entry by the graph's OWNER first",
      _owner_first in _body, _body[:400])
check("...falling back to every listed assignee, so an older manifest still resolves",
      'ap.get("tag") for ap in (entry.get("assigned_profiles") or [])' in _body)
check("...and it tries each candidate against the consumer's own graphs",
      "_kg = self._pack_kg_for_profile(_pid, user_uuid, owned)" in _body)
check("an entry that resolves to nothing is skipped, never guessed at",
      "if not pid or not kg_id:" in _body and "continue" in _body)
check("the export writes the OWNER as profile_tag when it knows it",
      '"profile_tag": _kg_owner_tag or _kg_profile.get("tag"),' in SRC)
_old_lookup = ('pid = tag_to_profile_id.get(entry.get("profile_tag"))'
               '\n            if not pid:')
check("the single-tag lookup that caused it is gone", _old_lookup not in _body)

print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
