"""The pack ledger must label each profile with the tag it was created under (2026-09-04).

Observed on production, twice, with nothing having touched the profiles: the fresh
install recorded ids in CREATION order (non-genie first, coordinator last) zipped
against tags in MANIFEST order. A manifest listing its coordinator third of four
labelled the NIBRS analyst as the coordinator and the coordinator as NIBRS; the next
update_in_place then wrote the coordinator's manifest entry onto the analyst (the
analyst also lost its graph: the assignment rows key on the id, which was right).

Four rules, each with the mutation that would reintroduce the defect:
  1. the recording is aligned BY TAG, whatever the creation order;
  2. update_in_place heals a mislabelled ledger from the live tags — but only to a
     tag the manifest ships, so an out-of-band rename is still updated as installed;
  3. a profile whose type the pack changes sheds the previous type's exclusive block;
  4. the graph lookup reads the assignments table's real columns.
CI-pure: real sqlite for the assignments table, fakes for config.
"""
import ast, asyncio, os, sqlite3, sys, tempfile, pathlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from trusted_data_agent.core.agent_pack_manager import AgentPackManager as M

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1
    else:
        FAIL += 1; print(f"  FAIL: {name}: {detail}")

# the exact manifest order of the Aug-28 and Sep-3 production installs
PROFILES = [{"tag": "CASEDF", "profile_type": "llm_only", "role": "expert"},
            {"tag": "PDOCS", "profile_type": "rag_focused", "role": "expert"},
            {"tag": "POLICE", "profile_type": "genie", "role": "coordinator"},
            {"tag": "NIBRS", "profile_type": "llm_only", "role": "expert"}]
# creation order: non-genie in manifest order, then the coordinator
T2P = {"CASEDF": "p-casedf", "PDOCS": "p-pdocs", "NIBRS": "p-nibrs", "POLICE": "p-police"}

def t_recording_aligned():
    ids, tags, roles = M._aligned_recording(PROFILES, T2P)
    check("each id is recorded under the tag it was created as", dict(zip(ids, tags)) == {v: k for k, v in T2P.items()}, dict(zip(ids, tags)))
    check("the coordinator role lands on the coordinator's id", dict(zip(ids, roles))["p-police"] == "coordinator", dict(zip(ids, roles)))
    # the old zip, reproduced: creation-ordered ids against manifest-ordered tags
    created = ["p-casedf", "p-pdocs", "p-nibrs", "p-police"]
    old = dict(zip(created, [p["tag"] for p in PROFILES]))
    check("the retired zip mislabels exactly the two profiles seen on production", old["p-nibrs"] == "POLICE" and old["p-police"] == "NIBRS", old)
    ids, tags, roles = M._aligned_recording(PROFILES, {"CASEDF": "p-casedf"})
    check("a profile that was not created is omitted, never shifting the others", ids == ["p-casedf"] and tags == ["CASEDF"], (ids, tags))

def _ledger(**tag_by_id):
    return [{"resource_type": "profile", "resource_id": pid, "resource_tag": tag,
             "resource_role": "coordinator" if tag == "POLICE" else "expert"} for pid, tag in tag_by_id.items()] + \
           [{"resource_type": "knowledge_graph", "resource_id": "kg-1", "resource_tag": "G"}]

def t_heal():
    manifest = {"CASEDF", "PDOCS", "POLICE", "NIBRS"}
    # the production swap: ledger says p-nibrs=POLICE / p-police=NIBRS, live says the opposite
    old = _ledger(**{"p-casedf": "CASEDF", "p-pdocs": "PDOCS", "p-nibrs": "POLICE", "p-police": "NIBRS"})
    live = [{"id": "p-nibrs", "tag": "NIBRS", "profile_type": "llm_only"},
            {"id": "p-police", "tag": "POLICE", "profile_type": "genie"},
            {"id": "p-casedf", "tag": "CASEDF", "profile_type": "llm_only"}]
    healed, notes = M._heal_ledger_tags(old, live, manifest)
    by = {r["resource_id"]: r for r in healed if r["resource_type"] == "profile"}
    check("the swap is healed from the live tags", by["p-nibrs"]["resource_tag"] == "NIBRS" and by["p-police"]["resource_tag"] == "POLICE", by)
    check("the coordinator role follows the genie profile", by["p-police"]["resource_role"] == "coordinator" and by["p-nibrs"]["resource_role"] == "expert", by)
    check("two corrections, both named", len(notes) == 2 and all("ledger corrected" in n for n in notes), notes)
    check("the input rows are not mutated", old[2]["resource_tag"] == "POLICE", old[2])
    check("non-profile rows pass through untouched", any(r["resource_type"] == "knowledge_graph" and r["resource_tag"] == "G" for r in healed), healed)
    # an out-of-band rename: the live tag is NOT one the manifest ships -> recorded tag kept
    old2 = _ledger(**{"p-nibrs": "NIBRS"})
    healed, notes = M._heal_ledger_tags(old2, [{"id": "p-nibrs", "tag": "NIBRSX", "profile_type": "llm_only"}], manifest)
    check("a rename to a tag the manifest does not know keeps the recorded tag", healed[0]["resource_tag"] == "NIBRS" and notes == [], (healed[0], notes))
    # a consistent ledger is a no-op
    healed, notes = M._heal_ledger_tags(old2, [{"id": "p-nibrs", "tag": "NIBRS", "profile_type": "llm_only"}], manifest)
    check("a consistent ledger yields no note", notes == [] and healed[0]["resource_tag"] == "NIBRS", notes)
    # a profile missing from the live copy keeps its row (the repair path re-creates it)
    healed, notes = M._heal_ledger_tags(old2, [], manifest)
    check("a missing live profile keeps its ledger row", healed[0]["resource_tag"] == "NIBRS" and notes == [], notes)
    # a kept recorded tag must not shadow a corrected one
    old3 = _ledger(**{"p-a": "NIBRS", "p-b": "CASEDF"})
    healed, notes = M._heal_ledger_tags(old3, [{"id": "p-a", "tag": "ZZ", "profile_type": "llm_only"},
                                              {"id": "p-b", "tag": "NIBRS", "profile_type": "llm_only"}], manifest)
    by = {r["resource_id"]: r["resource_tag"] for r in healed if r["resource_type"] == "profile"}
    check("a corrected tag is never duplicated by a row kept on its recorded tag", by == {"p-a": "ZZ", "p-b": "NIBRS"}, by)

class Cfg:
    def __init__(self, live):
        self.live = {p["id"]: dict(p) for p in live}; self.removed = []
    def get_profile(self, pid, u): return self.live.get(pid)
    def update_profile(self, pid, data, u):
        if pid not in self.live: return False
        self.live[pid].update(data); return True
    def add_profile(self, data, u): self.live[data["id"]] = dict(data); return True
    def remove_profile_keys(self, pid, keys, u):
        self.removed.append((pid, tuple(keys)))
        for k in keys: self.live[pid].pop(k, None)
        return True

def t_update_keeps_the_classification_snapshot():
    """The classification snapshot is deployment-local: the reachable catalog the trust
    envelope's deployment half hashes. `_build_profile` emits it EMPTY, and the in-place
    merge wrote that over a consumer's live snapshot on every update — three signed agents
    on the reference consumer read `changed` ("tools removed … +55") and one attested over
    the emptied snapshot read `trusted` for a catalog it did not have (found live,
    2026-09-07 — restoring the snapshot brought the other two back to `trusted` on their
    EXISTING attestations, so nothing but the import had moved)."""
    m = M.__new__(M)
    live_snapshot = {"tools": {"base_readQuery": {"category": "Query"}}, "prompts": {},
                     "resources": {}, "last_classified": "2026-09-01T00:00:00Z",
                     "classified_with_mode": "light"}
    cfg = Cfg([{"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "tools": ["a"],
                "classification_results": dict(live_snapshot)}])
    shipped = {"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "tools": ["a", "b"],
               "classification_results": {"tools": {}, "prompts": {}, "resources": {},
                                          "last_classified": None, "classified_with_mode": None}}
    m._upsert_pack_profile(cfg, {"tag": "NIBRS"}, shipped, "p1", "u", {})
    check("an in-place update keeps the consumer's classification snapshot",
          cfg.live["p1"].get("classification_results") == live_snapshot, cfg.live["p1"].get("classification_results"))
    check("...while the shipped configuration still lands", cfg.live["p1"].get("tools") == ["a", "b"], cfg.live["p1"].get("tools"))

def t_update_flags_reclassification_when_the_binding_moves():
    """ONE rule for staleness, shared with the editor: a pack that rebinds an agent's
    MCP server (per-agent `mcp_bindings`) makes the classification snapshot stale, and
    the in-place update must SAY so — before this, the consumer's signed agent read
    `changed` after a rebind with nothing telling its owner why."""
    from trusted_data_agent.core.config_manager import reclassification_needed as R
    classified = {"classification_results": {"tools": {"A": [{"name": "t"}]}, "last_classified": "2026-09-01"},
                  "classification_mode": "light", "mcpServerId": "s1", "llmConfigurationId": "l1"}
    check("binding moved on a classified profile -> stale", R(classified, {"mcpServerId": "s2"}) is True)
    check("same binding -> not stale", R(classified, {"mcpServerId": "s1"}) is False)
    check("binding absent from the write -> not a change", R(classified, {"tools": ["x"]}) is False)
    check("mode change -> stale", R(classified, {"classification_mode": "full"}) is True)
    check("LLM change under light mode -> not stale (the LLM is not used)", R(classified, {"llmConfigurationId": "l2"}) is False)
    check("LLM change under full mode -> stale", R({**classified, "classification_mode": "full"}, {"llmConfigurationId": "l2"}) is True)
    never = {**classified, "classification_results": {"tools": {}, "last_classified": None}}
    check("a never-classified profile has nothing to go stale", R(never, {"mcpServerId": "s2"}) is False)
    check("tolerates absent inputs", R(None, None) is False)
    # the pack seam applies it
    m = M.__new__(M)
    cfg = Cfg([{"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "mcpServerId": "s1",
                "classification_results": {"tools": {"A": [{"name": "t"}]}, "last_classified": "2026-09-01"}}])
    m._upsert_pack_profile(cfg, {"tag": "NIBRS"}, {"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "mcpServerId": "s2"}, "p1", "u", {})
    check("the in-place update flags reclassification when it moves the binding", cfg.live["p1"].get("needs_reclassification") is True, cfg.live["p1"])
    cfg2 = Cfg([{"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "mcpServerId": "s1",
                 "classification_results": {"tools": {"A": [{"name": "t"}]}, "last_classified": "2026-09-01"}}])
    m._upsert_pack_profile(cfg2, {"tag": "NIBRS"}, {"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "mcpServerId": "s1"}, "p1", "u", {})
    check("...and not when the binding is unchanged", not cfg2.live["p1"].get("needs_reclassification"), cfg2.live["p1"])
    # the editor's endpoint applies the SAME rule (AST: a call, not a comment)
    import ast as _ast
    tree = _ast.parse(open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "api", "rest_routes.py")).read())
    fn = next(n for n in _ast.walk(tree) if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == "update_profile")
    calls = {n.func.id for n in _ast.walk(fn) if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}
    check("the editor's update endpoint uses the shared rule", "reclassification_needed" in calls, calls)

def t_import_boundary():
    """The set of keys a pack import may write onto an existing profile is declared ONCE
    (`profile_config_contract.import_writable_keys`) and enforced at the merge; and
    `_build_profile` — the source — cannot write a key outside it, checked on the AST."""
    from trusted_data_agent.core import profile_config_contract as pcc
    import ast as _ast, inspect as _inspect, textwrap as _tw
    w = set(pcc.import_writable_keys())
    check("every portable key is import-writable", set(pcc.portable_keys()) <= w, set(pcc.portable_keys()) - w)
    check("every instance binding is import-writable", set(pcc.IMPORT_BINDING_KEYS) <= w)
    check("no deployment-local key is import-writable", not (set(pcc.DEPLOYMENT_LOCAL_KEYS) & w), set(pcc.DEPLOYMENT_LOCAL_KEYS) & w)
    # the merge drops a stray key, whatever it is called
    m = M.__new__(M)
    cfg = Cfg([{"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "mcpServerId": "s1"}])
    m._upsert_pack_profile(cfg, {"tag": "NIBRS"}, {"id": "p1", "tag": "NIBRS", "profile_type": "llm_only",
                                                   "mcpServerId": "s1", "zz_some_local_state": {"x": 1},
                                                   "needs_reclassification": True}, "p1", "u", {})
    check("a key outside the boundary never reaches the live profile", "zz_some_local_state" not in cfg.live["p1"], cfg.live["p1"])
    check("...nor does a shipped reclassification flag (it is set by the rule, never carried)", not cfg.live["p1"].get("needs_reclassification"), cfg.live["p1"])
    check("...while keys inside it still land", cfg.live["p1"].get("mcpServerId") == "s1")
    # the source: every key _build_profile writes into profile_data is inside the boundary
    # (plus the snapshot it legitimately initialises for a NEW profile)
    tree = _ast.parse(_tw.dedent(_inspect.getsource(M._build_profile)))
    written = set()
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Assign):
            for tgt in n.targets:
                if isinstance(tgt, _ast.Name) and tgt.id == "profile_data" and isinstance(n.value, _ast.Dict):
                    written |= {k.value for k in n.value.keys if isinstance(k, _ast.Constant)}
                if (isinstance(tgt, _ast.Subscript) and isinstance(tgt.value, _ast.Name)
                        and tgt.value.id == "profile_data" and isinstance(tgt.slice, _ast.Constant)):
                    written.add(tgt.slice.value)
    check("_build_profile writes a known set of keys (the scan is not vacuous)", len(written) >= 12, written)
    stray = written - w - {"classification_results"}
    check("_build_profile writes no key outside the import boundary", not stray, stray)

def t_type_change_sheds():
    m = M.__new__(M)
    cfg = Cfg([{"id": "p1", "tag": "NIBRS", "profile_type": "genie", "genieConfig": {"slaveProfiles": ["x"]}, "color": "#1"}])
    t2p = {}
    m._upsert_pack_profile(cfg, {"tag": "NIBRS"}, {"id": "p1", "tag": "NIBRS", "profile_type": "llm_only", "tools": ["a"]}, "p1", "u", t2p)
    check("a genie -> llm_only change sheds genieConfig", "genieConfig" not in cfg.live["p1"] and cfg.removed == [("p1", ("genieConfig",))], (cfg.live["p1"], cfg.removed))
    check("keys the manifest is silent on still survive (absence preserves)", cfg.live["p1"].get("color") == "#1", cfg.live["p1"])
    cfg = Cfg([{"id": "p2", "tag": "POLICE", "profile_type": "genie", "genieConfig": {"slaveProfiles": ["old"]}}])
    m._upsert_pack_profile(cfg, {"tag": "POLICE"}, {"id": "p2", "tag": "POLICE", "profile_type": "genie", "genieConfig": {"slaveProfiles": ["new"]}}, "p2", "u", {})
    check("same type: nothing shed, the block is replaced", cfg.removed == [] and cfg.live["p2"]["genieConfig"] == {"slaveProfiles": ["new"]}, (cfg.removed, cfg.live["p2"]))
    cfg = Cfg([{"id": "p3", "tag": "A", "profile_type": "llm_only"}])
    m._upsert_pack_profile(cfg, {"tag": "A"}, {"id": "p3", "tag": "A", "profile_type": "genie", "genieConfig": {}}, "p3", "u", {})
    check("llm_only -> genie with no residue has nothing to shed", cfg.removed == [], cfg.removed)
    # THE FAITHFUL-COPY RULE: a coordinator that once held an analyst's entry keeps `tools`
    # and `useMcpTools` (capability-SIGNED) — the publisher's entry has neither, so the copy
    # sheds them; a posture block the entry omits is PRESERVED (absence never downgrades).
    cfg = Cfg([{"id": "p4", "tag": "POLICE", "profile_type": "genie", "tools": ["base_readQuery"], "useMcpTools": True,
                "groundingConfig": {"mode": "enforce"}, "genieConfig": {"slaveProfiles": ["x"]}}])
    m._upsert_pack_profile(cfg, {"tag": "POLICE"}, {"id": "p4", "tag": "POLICE", "profile_type": "genie", "genieConfig": {"slaveProfiles": ["y"]}}, "p4", "u", {})
    check("capability-signed keys the entry does not carry are shed", "tools" not in cfg.live["p4"] and "useMcpTools" not in cfg.live["p4"], cfg.live["p4"])
    check("a posture block the entry omits is preserved", cfg.live["p4"].get("groundingConfig") == {"mode": "enforce"}, cfg.live["p4"])
    check("keys the entry carries are simply replaced", cfg.live["p4"]["genieConfig"] == {"slaveProfiles": ["y"]}, cfg.live["p4"])
    # unsigned but unguarded portable keys are the pack's too: a leftover skillsConfig adds
    # skill INGREDIENTS to the composition; knowledgeConfig carries a floor and a flag → kept
    cfg = Cfg([{"id": "p5", "tag": "POLICE", "profile_type": "genie", "skillsConfig": {"skills": [{"id": "x", "active": True}]},
                "knowledgeConfig": {"enabled": True, "sufficiencyThreshold": 0.4}, "recommenderConfig": {"enabled": False}}])
    m._upsert_pack_profile(cfg, {"tag": "POLICE"}, {"id": "p5", "tag": "POLICE", "profile_type": "genie", "genieConfig": {}}, "p5", "u", {})
    check("a leftover skillsConfig the entry omits is shed", "skillsConfig" not in cfg.live["p5"], cfg.live["p5"])
    check("an unguarded recommenderConfig the entry omits is shed", "recommenderConfig" not in cfg.live["p5"], cfg.live["p5"])
    check("a guarded knowledgeConfig the entry omits is preserved", cfg.live["p5"].get("knowledgeConfig") == {"enabled": True, "sufficiencyThreshold": 0.4}, cfg.live["p5"])
    from trusted_data_agent.core import profile_config_contract as _pcc
    check("the policy reads the registry: skillsConfig unguarded, groundingConfig guarded", M._pack_owned_absence(_pcc.spec_for("skillsConfig")) and not M._pack_owned_absence(_pcc.spec_for("groundingConfig")) and not M._pack_owned_absence(None), None)

def t_kg_lookup_real_columns():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    c = sqlite3.connect(path)
    c.executescript(pathlib.Path(os.path.join(os.path.dirname(__file__), "..", "schema", "22_kg_profile_assignments.sql")).read_text())
    c.execute("ALTER TABLE kg_profile_assignments ADD COLUMN kg_id TEXT")  # the runtime migration (auth/database.py)
    c.execute("CREATE TABLE kg_metadata (kg_id TEXT, profile_id TEXT, user_uuid TEXT, is_active INTEGER)")
    c.execute("INSERT INTO kg_profile_assignments (kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active) VALUES (?,?,?,?,?)", ("kg-1", "p-nibrs", "p-nibrs", "u", 1))
    c.execute("INSERT INTO kg_profile_assignments (kg_id, kg_owner_profile_id, assigned_profile_id, user_uuid, is_active) VALUES (?,?,?,?,?)", ("kg-1", "p-nibrs", "p-casedf", "u", 1))
    c.execute("INSERT INTO kg_metadata VALUES (?,?,?,?)", ("kg-legacy", "p-old", "u", 1))
    c.commit(); c.close()
    m = M.__new__(M); m.db_path = path
    check("the graph assigned to a profile is found through assigned_profile_id", m._pack_kg_for_profile("p-nibrs", "u", {"kg-1"}) == "kg-1", None)
    check("an assignee (not owner) also resolves", m._pack_kg_for_profile("p-casedf", "u", {"kg-1"}) == "kg-1", None)
    check("a graph the pack does not own is never returned", m._pack_kg_for_profile("p-nibrs", "u", {"other"}) is None, None)
    check("a legacy owner-only row on kg_metadata still resolves", m._pack_kg_for_profile("p-old", "u", {"kg-legacy"}) == "kg-legacy", None)
    check("another user's assignment is invisible", m._pack_kg_for_profile("p-nibrs", "someone-else", {"kg-1"}) is None, None)

SRC = pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent/core/agent_pack_manager.py"
def _calls_in(fn):
    tree = ast.parse(SRC.read_text())
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn:
            return {(s.func.attr if isinstance(s.func, ast.Attribute) else getattr(s.func, "id", "")) for s in ast.walk(n) if isinstance(s, ast.Call)}
    return None

def t_wiring():
    calls = _calls_in("import_pack")
    check("the fresh install records through the aligned helper", calls is not None and "_aligned_recording" in calls, calls and sorted(calls)[:5])
    calls = _calls_in("_handle_update_in_place")
    check("update_in_place heals the ledger before reading it", calls is not None and "_heal_ledger_tags" in calls, None)
    src = SRC.read_text()
    i = src.index("def _handle_update_in_place")
    body = src[i:]
    check("the heal runs before the by-tag map is built", body.index("_heal_ledger_tags(") < body.index("existing_pack_profile_id_by_tag = {"), None)
    check("no creation-ordered id list is zipped with manifest tags any more", "profile_ids=created_profile_ids" not in src, None)
    kg = src[src.index("def _pack_kg_for_profile"):src.index("def _pick_pack_kg")]
    # Property, not an exact source string: the first version pinned the legacy query's
    # indentation and broke when Model B (Sep 2026) wrapped it in a column-tolerant fallback
    # while the property — the lookup names the REAL columns — held perfectly.
    check("the graph lookup names the real assignment column", "assigned_profile_id = ?" in kg and "WHERE profile_id = ? AND user_uuid = ?" in kg
          and "(profile_id, user_uuid)).fetchall()" in kg, None)
    check("the graph lookup opens SQLite through sqlite_util", "sqlite_util.connect" in kg and "_sq.connect" not in kg, None)

for fn in (t_recording_aligned, t_heal, t_update_keeps_the_classification_snapshot, t_update_flags_reclassification_when_the_binding_moves, t_import_boundary, t_type_change_sheds, t_kg_lookup_real_columns, t_wiring):
    try: fn()
    except Exception as exc:
        FAIL += 1; print(f"  FAIL: {fn.__name__} raised {type(exc).__name__}: {exc}")

# ── collections are paired by IDENTITY, never by the ref's position ─────────
# A manifest ref (`collection_N`) is a coordinate of one export's walk; the walk
# follows the profiles, and a re-registration or a new member reorders it. Paired
# positionally, production's fork sync patched the core-model planner INTO the
# consumer's NIBRS planner copy and created the NIBRS planner anew (2026-09-15).
class _CollDB:
    def __init__(self, rows): self.rows = rows
    def get_collection_by_id(self, c): return self.rows.get(c)
_rows = {535: {"repository_type": "knowledge", "name": "Policing Data Products — Documentation"},
         536: {"repository_type": "planner", "name": "Policing Data Products — Planner"}}
_man = [{"ref": "collection_1", "name": "Policing Data Products — Documentation", "repository_type": "knowledge"},
        {"ref": "collection_2", "name": "Policing Core Model — Planner", "repository_type": "planner"},
        {"ref": "collection_3", "name": "Policing Data Products — Planner", "repository_type": "planner"}]
_pair = M._pair_collections_by_identity(_man, {"collection_1": 535, "collection_2": 536}, _CollDB(_rows))
check("the reordered manifest pairs each entry with the collection it IS",
      _pair == {"collection_1": 535, "collection_3": 536}, _pair)
check("...and the genuinely new collection is left unpaired (to be created), never given "
      "the positional ref's store", "collection_2" not in _pair, _pair)
_same = [{"ref": "collection_1", "name": "Policing Data Products — Documentation", "repository_type": "knowledge"},
         {"ref": "collection_2", "name": "Policing Data Products — Planner", "repository_type": "planner"}]
check("an unchanged order pairs exactly as before",
      M._pair_collections_by_identity(_same, {"collection_1": 535, "collection_2": 536}, _CollDB(_rows))
      == {"collection_1": 535, "collection_2": 536})
_noname = [{"ref": "collection_1", "repository_type": "knowledge"}, {"ref": "collection_2", "repository_type": "planner"}]
check("a manifest entry without a name falls back to the positional ref",
      M._pair_collections_by_identity(_noname, {"collection_1": 535, "collection_2": 536}, _CollDB(_rows))
      == {"collection_1": 535, "collection_2": 536})
_dup = dict(_rows); _dup[538] = {"repository_type": "planner", "name": "Policing Data Products — Planner"}
_p2 = M._pair_collections_by_identity(_man, {"collection_1": 535, "collection_2": 536, "collection_3": 538}, _CollDB(_dup))
check("an AMBIGUOUS identity (two copies of one name) falls back to position for THAT "
      "entry, a different identity still gets no store, and no collection is claimed twice",
      _p2 == {"collection_1": 535, "collection_3": 538}, _p2)
_p3 = M._pair_collections_by_identity(
    [{"ref": "collection_2", "name": "Policing Core Model — Planner", "repository_type": "planner"}],
    {"collection_2": 536}, _CollDB(_rows))
check("a named entry with no identity match never takes a positional store of a DIFFERENT "
      "identity (the core planner is created, not patched into the NIBRS copy)", _p3 == {}, _p3)
check("a type mismatch is not an identity match (a knowledge repo never pairs with a planner "
      "of the same name)",
      M._pair_collections_by_identity([{"ref": "collection_1", "name": "Policing Data Products — Planner", "repository_type": "knowledge"}],
                                     {"collection_9": 536}, _CollDB(_rows)) == {}, None)
_src = open(os.path.join(os.path.dirname(__file__), "..", "src", "trusted_data_agent", "core", "agent_pack_manager.py")).read()
_uip = _src[_src.find("async def _handle_update_in_place("):]
check("the in-place update routes its ref→id map through the identity pairing BEFORE any "
      "validation or plan reads it",
      "old_coll_ref_to_id = self._pair_collections_by_identity(" in _uip
      and _uip.find("old_coll_ref_to_id = self._pair_collections_by_identity(") < _uip.find("Step 2: Validation"), None)

print(f"{PASS} passed, {FAIL} failed"); sys.exit(1 if FAIL else 0)
