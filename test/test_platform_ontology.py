"""CI-pure tests for the platform-ontology segment projection (Tier 5 Step 1).

Run: PYTHONPATH=src python test/test_platform_ontology.py

The Ontology is an *additional-value* surface: it never re-draws per-agent config.
It emits agents (IFOC-typed) + the knowledge/trust layer (accessible collections,
active KGs, memory) + coordinator topology, and surfaces config-level facts as
findings. CORRECTNESS RULES under test:
  • a collection grounds an agent ONLY when knowledge is ENABLED and the collection
    is in the user's ACCESSIBLE set (the stale `ragCollections` default is ignored);
  • a reference to an inaccessible collection is a broken binding (finding), never grounding;
  • CONFIGURED = has a real capability (accessible collection · active KG · live MCP
    tools · coordinator experts); an empty shell is UNCONFIGURED (not a trust risk).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trusted_data_agent.core.platform_ontology import projection as proj  # noqa: E402

_passed = 0
_failed = 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {msg}")


class _MemItem:
    def __init__(self, profile_id):
        self.profile_id = profile_id


class _FakeCM:
    def __init__(self, profiles, llms, mcps, collections):
        self._p, self._l, self._m, self._c = profiles, llms, mcps, collections

    def get_profiles(self, u):
        return self._p

    def get_llm_configurations(self, u):
        return self._l

    def get_mcp_servers(self, u):
        return self._m

    def get_rag_collections(self, u):
        return self._c

    def get_vector_store_configurations(self, u):
        return []

    def get_kg_backend_configurations(self, u):
        return []

    def get_context_window_types(self, u):
        return []


def _run(cm, graphs, mem, kg_boom=False, sessions=None, packs=None, skills=None, assignments=None):
    import trusted_data_agent.core.config_manager as CM
    import trusted_data_agent.core.platform_ontology.projection as P
    o_cm, o_g, o_m, o_s, o_a = CM.get_config_manager, P._list_graphs, P._list_memory, P._list_skills, P._list_kg_assignments
    try:
        CM.get_config_manager = lambda: cm
        P._list_graphs = (lambda u: (_ for _ in ()).throw(RuntimeError("kg backend down"))) if kg_boom else (lambda u: graphs)
        P._list_memory = lambda u: mem
        P._list_skills = lambda: (skills or [])
        P._list_kg_assignments = lambda u: (assignments or [])   # never the real auth DB in CI
        return proj.build_segment("u", sessions, packs)
    finally:
        CM.get_config_manager, P._list_graphs, P._list_memory, P._list_skills, P._list_kg_assignments = o_cm, o_g, o_m, o_s, o_a


def _nodes(spec):
    return {n["entity_id"]: n for n in spec["nodes"]}


def _find(spec, fid):
    return next((f for f in spec["findings"] if f["id"] == fid), None)


MCP = [{"id": "m1", "name": "Teradata MCP"}]
LLMS = [{"id": "l1", "name": "Gemini Flash"}]
# Accessible collections for this user (owned/subscribed): 1, 2, 9. NOT 99.
COLS = [{"id": 1, "name": "Sales KB"}, {"id": 2, "name": "Ops KB"}, {"id": 9, "name": "Stale KB"}]


def _profiles():
    return [
        # grounded via ENABLED + ACCESSIBLE collections
        {"id": "p1", "tag": "OPTIM", "name": "Optimizer", "profile_type": "tool_enabled",
         "llmConfigurationId": "l1", "mcpServerId": "m1", "useMcpTools": False,
         "knowledgeConfig": {"enabled": True, "collections": [{"id": 1}, {"id": 2}]}},
        # grounded via one accessible collection
        {"id": "p2", "tag": "FOCUS", "name": "Focuser", "profile_type": "rag_focused",
         "llmConfigurationId": "l1", "knowledgeConfig": {"enabled": True, "collections": [{"id": 1}]}},
        # UNCONFIGURED empty shell (no knowledge, tools off)
        {"id": "p3", "tag": "CHAT", "name": "Chatter", "profile_type": "llm_only",
         "llmConfigurationId": "l1", "mcpServerId": "m1", "useMcpTools": False,
         "knowledgeConfig": {"enabled": False, "collections": []}, "ragCollections": [1]},
        # grounded via LIVE MCP tools (no collections)
        {"id": "p4", "tag": "TOOLS", "name": "Tooler", "profile_type": "tool_enabled",
         "llmConfigurationId": "l1", "mcpServerId": "m1", "useMcpTools": True,
         "knowledgeConfig": {"enabled": False, "collections": []}},
        # BROKEN binding: enabled but references an inaccessible collection (99) → unconfigured
        {"id": "p5", "tag": "BROKEN", "name": "Broken", "profile_type": "rag_focused",
         "llmConfigurationId": "l1", "knowledgeConfig": {"enabled": True, "collections": [{"id": 99}]}},
        # coordinator grounded via experts
        {"id": "coord", "tag": "COORD", "name": "Coordinator", "profile_type": "genie",
         "llmConfigurationId": "l1", "genieConfig": {"slaveProfiles": ["p1", "p2"]}},
    ]


# ---------------------------------------------------------------------------
def t_config_model():
    graphs = [{"kg_id": "kg1", "profile_id": "p1", "kg_name": "Restaurants",
               "total_entities": 86, "total_relationships": 128, "is_active": True,
               "lifecycle_status": "draft", "conformance_status": "conformant",
               "entity_types": {"domain": 4}}]
    spec = _run(_FakeCM(_profiles(), LLMS, MCP, COLS), graphs, [])
    nd = _nodes(spec)

    # IFOC-typed agents
    check(nd["agent:p1"]["type"] == "agent_optimize", "tool_enabled → agent_optimize")
    check(nd["agent:coord"]["type"] == "agent_coordinate", "genie → agent_coordinate")

    # Collections: ONLY enabled + accessible drawn. 1 & 2 yes; 9 not referenced; 99 inaccessible.
    check("collection:1" in nd and "collection:2" in nd, "accessible enabled collections drawn")
    check("collection:9" not in nd, "unreferenced collection not drawn")
    check("collection:99" not in nd, "inaccessible collection NOT drawn (the bug we fixed)")

    # config_state per the rules
    check(nd["agent:p1"]["properties"]["config_state"] == "grounded", "p1 grounded via collections")
    check(nd["agent:p2"]["properties"]["config_state"] == "grounded", "p2 grounded via collection")
    check(nd["agent:p3"]["properties"]["config_state"] == "unconfigured", "p3 empty shell → unconfigured (NOT grounded via stale ragCollections)")
    check(nd["agent:p4"]["properties"]["config_state"] == "grounded", "p4 grounded via live MCP tools")
    check(any("live tools" in g for g in nd["agent:p4"]["properties"]["grounded_on"]), "p4 grounding names MCP tools")
    check(nd["agent:p5"]["properties"]["config_state"] == "unconfigured", "p5 broken binding → unconfigured")
    check(nd["agent:coord"]["properties"]["config_state"] == "grounded", "coordinator grounded via experts")
    check(nd["agent:coord"]["properties"]["expert_count"] == 2, "coordinator expert_count")

    # p3's stale ragCollections=[1] must NOT have created a grounding edge
    p3_edges = [l for l in spec["links"] if l["source"] == nd["agent:p3"]["id"] and l["type"] == "grounds_on"]
    check(not p3_edges, "p3 has NO grounding edge (ragCollections ignored)")
    return spec


def t_findings(spec):
    # CONFIG: unconfigured is INFO (not a trust-risk warning), names the shells.
    unc = _find(spec, "unconfigured")
    check(unc and unc["severity"] == "info", "unconfigured finding is info, not warn")
    check(unc and "CHAT" in unc["text"] and "BROKEN" in unc["text"], "unconfigured names the shells")
    check(unc and "agent:p3" in unc["focus"], "unconfigured focus includes a shell")
    # configured summary
    cfg = _find(spec, "configured")
    check(cfg and "4 of 6" in cfg["text"], "configured summary (4 of 6: p1,p2,p4,coord)")
    # MISCONFIG: broken binding is a warning
    bad = _find(spec, "inaccessible_collections")
    check(bad and bad["severity"] == "warn" and "BROKEN" in bad["text"], "inaccessible-collection warning names @BROKEN")
    # blast-radius: collection 1 grounds p1 + p2 (shared)
    sc = _find(spec, "shared_col_1")
    check(sc and "2 agents" in sc["text"], "shared-collection finding (1 grounds 2)")
    # coverage + conformance
    check(_find(spec, "coverage") and "4 business domain" in _find(spec, "coverage")["text"], "coverage finding")
    check(_find(spec, "conformance_ok") is not None, "conformance-ok finding")
    # GAP: collection 9 is unused (in accessible set, no agent uses it)
    gap = _find(spec, "unused_collections")
    check(gap and "Stale KB" in gap["text"], "unused-collection gap (Stale KB)")


def t_spec_contract(spec):
    for k in ("nodes", "links", "title", "entity_type_colors", "entity_type_counts",
              "total_nodes", "truncated", "findings"):
        check(k in spec, f"spec carries '{k}'")
    check(spec["entity_type_colors"]["agent_optimize"] == "#9333ea", "IFOC purple")
    check(spec["is_platform_ontology"] is True, "platform marker")
    n = len(spec["nodes"])
    check(all(isinstance(l["source"], int) and 0 <= l["source"] < n
              and isinstance(l["target"], int) and 0 <= l["target"] < n for l in spec["links"]),
          "links are valid integer indices")
    # config objects ARE first-class nodes now (Anatomy department), each mapped
    # to what uses it.
    llm_nodes = [x for x in spec["nodes"] if x["entity_id"].startswith("llm:")]
    check(bool(llm_nodes), "llm config nodes present")
    check(all(n["properties"]["taxonomy"]["category"] == "Anatomy" for n in llm_nodes),
          "llm nodes → Anatomy department")


def t_taxonomy_and_connectivity(spec):
    nd = {n["entity_id"]: n for n in spec["nodes"]}
    # Taxonomy spine present
    check("segment:root" in nd, "taxonomy root node present")
    check(any(n["type"] == "class_group" for n in spec["nodes"]), "IFOC class-group nodes present")
    # Every node classified
    check(all((n["properties"].get("taxonomy") or {}).get("category") for n in spec["nodes"]),
          "every node carries a taxonomy category")
    check(nd["agent:p1"]["properties"]["taxonomy"]["category"] == "Agents", "agent → Agents category")
    check(nd["collection:1"]["properties"]["taxonomy"]["category"] == "Intelligence", "collection → Intelligence category")
    check(nd["collection:1"]["properties"]["taxonomy"]["subcategory"] == "Knowledge Repositories", "knowledge collection subcategory")
    # Facet exposed
    check("taxonomy" in spec and "Agents" in spec["taxonomy"] and "Intelligence" in spec["taxonomy"],
          "spec exposes taxonomy facet (Agents + Intelligence)")
    # NO ISLANDS — every node reachable from the root through the taxonomy spine.
    n = len(spec["nodes"])
    adj = {i: set() for i in range(n)}
    for l in spec["links"]:
        adj[l["source"]].add(l["target"])
        adj[l["target"]].add(l["source"])
    root = nd["segment:root"]["id"]
    seen, stack = {root}, [root]
    while stack:
        x = stack.pop()
        for y in adj[x]:
            if y not in seen:
                seen.add(y)
                stack.append(y)
    check(len(seen) == n, f"NO ISLANDS: all {n} nodes reachable from root (reached {len(seen)})")


def t_sessions():
    sessions = [
        {"id": "s1", "name": "Sales analysis", "profile_id": "p1", "profile_tag": "OPTIM",
         "last_updated": "2026-06-29T10:00:00", "models_used": ["openai/gpt"]},
        {"id": "s2", "name": "Quick chat", "profile_id": "p3", "profile_tag": "CHAT",
         "last_updated": "2026-06-30T09:00:00"},
        # orphan run — its profile no longer exists; must still be reachable (linked to root)
        {"id": "s3", "name": "Old run", "profile_id": "deleted", "last_updated": "2026-06-01T00:00:00"},
    ]
    spec = _run(_FakeCM(_profiles(), LLMS, MCP, COLS), [], [], sessions=sessions)
    nd = _nodes(spec)
    check("session:s1" in nd and nd["session:s1"]["type"] == "session", "session node drawn")
    check(nd["session:s1"]["properties"]["taxonomy"]["category"] == "Sessions", "session → Sessions category")
    check("Sessions" in spec["taxonomy"], "Sessions facet exposed")
    # session links to the agent it ran under
    ran = [l for l in spec["links"] if l["type"] == "ran" and l["target"] == nd["session:s1"]["id"]]
    check(ran and any(l["source"] == nd["agent:p1"]["id"] for l in ran), "session s1 linked to its agent @OPTIM")
    # orphan session (deleted profile) still reachable from root — NO ISLANDS
    n = len(spec["nodes"])
    adj = {i: set() for i in range(n)}
    for l in spec["links"]:
        adj[l["source"]].add(l["target"]); adj[l["target"]].add(l["source"])
    root = nd["segment:root"]["id"]
    seen, stack = {root}, [root]
    while stack:
        x = stack.pop()
        for y in adj[x]:
            if y not in seen:
                seen.add(y); stack.append(y)
    check(nd["session:s3"]["id"] in seen, "orphan session reachable from root (no island)")
    check(len(seen) == n, f"all {n} nodes reachable with sessions present")


def t_planner_vs_knowledge_repos():
    # A planner repository (champion strategies) is a distinct node type from a
    # knowledge repository (reference docs), driven by the collection's repository_type.
    cols = [{"id": 1, "name": "Sales KB", "repository_type": "knowledge"},
            {"id": 5, "name": "Proven Strategies", "repository_type": "planner"}]
    profs = [
        {"id": "p1", "tag": "OPTIM", "name": "Opt", "profile_type": "tool_enabled",
         "llmConfigurationId": "l1",
         "knowledgeConfig": {"enabled": True, "collections": [{"id": 1}]},
         "ragCollections": [5]},
    ]
    spec = _run(_FakeCM(profs, LLMS, MCP, cols), [], [])
    nd = _nodes(spec)
    check(nd["collection:1"]["type"] == "collection", "knowledge repo → type collection")
    check(nd["collection:5"]["type"] == "collection_planner", "planner repo → type collection_planner")
    check(nd["collection:5"]["properties"]["taxonomy"]["subcategory"] == "Planner Repositories", "planner repo subcategory")
    check(nd["collection:1"]["properties"]["taxonomy"]["subcategory"] == "Knowledge Repositories", "knowledge repo subcategory")
    check(spec["entity_type_colors"].get("collection_planner") == "#818cf8", "planner repo has its own colour")
    # planner repo linked via plans_with; knowledge via grounds_on
    pl = [l for l in spec["links"] if l["type"] == "plans_with" and l["target"] == nd["collection:5"]["id"]]
    check(pl, "planner repo linked via plans_with")


def t_rag_focused_without_enabled_flag():
    # Regression: a rag_focused (Focus) profile whose knowledgeConfig has collections
    # but NO `enabled` key — the exact shape an agent-pack import produces (and what
    # @UDERIA had in production).  FocusEngine grounds on `collections` regardless of
    # `enabled`, so the ontology MUST draw the knowledge repo + grounds_on edge.
    cols = [{"id": 272, "name": "Uderia Documentation", "repository_type": "knowledge"}]
    profs = [
        {"id": "u1", "tag": "UDERIA", "name": "Uderia Docs", "profile_type": "rag_focused",
         "llmConfigurationId": "l1",
         # note: NO "enabled" key — mirrors production
         "knowledgeConfig": {"maxDocs": 10, "collections": [{"id": 272, "name": "Uderia Documentation"}]}},
        # control: llm_only WITHOUT enabled must STILL be gated (knowledge is a supplement there)
        {"id": "c1", "tag": "CHAT", "name": "Chat", "profile_type": "llm_only",
         "llmConfigurationId": "l1",
         "knowledgeConfig": {"collections": [{"id": 272}]}},
    ]
    spec = _run(_FakeCM(profs, LLMS, MCP, cols), [], [])
    nd = _nodes(spec)
    check("collection:272" in nd, "rag_focused knowledge repo drawn WITHOUT an enabled flag (the bug)")
    edges = [l for l in spec["links"]
             if l["source"] == nd["agent:u1"]["id"] and l["target"] == nd["collection:272"]["id"]
             and l["type"] == "grounds_on"]
    check(bool(edges), "grounds_on edge from @UDERIA → Uderia Documentation present")
    check(nd["agent:u1"]["properties"]["config_state"] == "grounded", "@UDERIA is grounded (not an empty shell)")
    # control: llm_only without enabled must NOT draw a grounding edge to 272
    c1_edges = [l for l in spec["links"]
                if l["source"] == nd["agent:c1"]["id"] and l["type"] == "grounds_on"]
    check(not c1_edges, "llm_only without enabled stays gated (enabled still matters for supplement types)")


def t_skills():
    profs = [
        {"id": "p1", "tag": "OPTIM", "name": "Opt", "profile_type": "tool_enabled",
         "llmConfigurationId": "l1", "knowledgeConfig": {"enabled": True, "collections": [{"id": 1}]},
         "skillsConfig": {"skills": {"teradata-sql-expert": {"active": True}, "muted": {"active": False}}}},
        {"id": "p2", "tag": "FOCUS", "name": "Foc", "profile_type": "rag_focused",
         "llmConfigurationId": "l1", "knowledgeConfig": {"enabled": True, "collections": [{"id": 1}]},
         "skillsConfig": {"skills": {"teradata-sql-expert": {"active": True}}}},
    ]
    skills = [{"skill_id": "teradata-sql-expert", "name": "Teradata SQL Expert",
               "display_name": "Teradata SQL Expert", "description": "SQL help", "is_builtin": True}]
    spec = _run(_FakeCM(profs, LLMS, MCP, COLS), [], [], skills=skills)
    nd = _nodes(spec)
    check("skill:teradata-sql-expert" in nd, "active skill drawn as a node")
    check("skill:muted" not in nd, "inactive skill NOT drawn")
    check(nd["skill:teradata-sql-expert"]["properties"]["taxonomy"]["category"] == "Anatomy", "skill → Anatomy category")
    check(nd["skill:teradata-sql-expert"]["properties"]["taxonomy"]["subcategory"] == "Skills", "skill subcategory")
    uses = [l for l in spec["links"] if l["type"] == "uses" and l["target"] == nd["skill:teradata-sql-expert"]["id"]]
    check(len(uses) == 2, "shared skill linked to both agents that enabled it")
    # explicit (opted-in) vs auto (companion) split
    check(set(nd["skill:teradata-sql-expert"]["properties"]["explicit_by"]) == {"OPTIM", "FOCUS"}, "skill explicit_by both agents")
    check(nd["skill:teradata-sql-expert"]["properties"]["auto_by"] == [], "skill auto_by empty (no auto entries)")


def t_packs():
    packs = [{"installation_id": 7, "name": "Virtual Account Team", "pack_type": "genie",
              "version": "1.0", "coordinator_profile_id": "coord", "coordinator_tag": "COORD",
              "profiles_count": 3, "experts_count": 2, "is_owned": True},
             # orphan pack — coordinator not a current profile → reachable via root
             {"installation_id": 8, "name": "Orphan Pack", "pack_type": "genie",
              "coordinator_profile_id": "gone", "profiles_count": 1, "is_owned": False}]
    spec = _run(_FakeCM(_profiles(), LLMS, MCP, COLS), [], [], packs=packs)
    nd = _nodes(spec)
    check("pack:7" in nd and nd["pack:7"]["type"] == "agent_pack", "agent pack drawn as node")
    check(nd["pack:7"]["properties"]["taxonomy"]["category"] == "Anatomy", "pack → Anatomy category")
    check(nd["pack:7"]["properties"]["taxonomy"]["subcategory"] == "Agent Packs", "pack subcategory Agent Packs")
    b = [l for l in spec["links"] if l["type"] == "bundles" and l["source"] == nd["pack:7"]["id"]]
    check(b and any(l["target"] == nd["agent:coord"]["id"] for l in b), "pack linked to its coordinator agent")
    # no islands incl. the orphan pack
    n = len(spec["nodes"])
    adj = {i: set() for i in range(n)}
    for l in spec["links"]:
        adj[l["source"]].add(l["target"]); adj[l["target"]].add(l["source"])
    root = nd["segment:root"]["id"]
    seen, stack = {root}, [root]
    while stack:
        x = stack.pop()
        for y in adj[x]:
            if y not in seen:
                seen.add(y); stack.append(y)
    check(nd["pack:8"]["id"] in seen, "orphan pack reachable from root")
    check(len(seen) == n, f"all {n} nodes reachable with packs present")


def t_multi_user():
    # Admin platform scope: aggregate several users under per-user nodes, with Users as
    # the top taxonomy layer. Each user is built from the SAME fake config (so both have
    # the same profile ids) — the per-user namespacing must keep them separate.
    import trusted_data_agent.core.config_manager as CM
    import trusted_data_agent.core.platform_ontology.projection as P
    o_cm, o_g, o_m, o_s, o_a = CM.get_config_manager, P._list_graphs, P._list_memory, P._list_skills, P._list_kg_assignments
    try:
        CM.get_config_manager = lambda: _FakeCM(_profiles(), LLMS, MCP, COLS)
        P._list_graphs = lambda u: []
        P._list_memory = lambda u: []
        P._list_skills = lambda: []
        P._list_kg_assignments = lambda u: []
        users = [{"uuid": "ua", "username": "alice", "tier": "user"},
                 {"uuid": "ub", "username": "bob", "tier": "developer"}]
        spec = proj.build_multi_user_segment(users)
    finally:
        CM.get_config_manager, P._list_graphs, P._list_memory, P._list_skills, P._list_kg_assignments = o_cm, o_g, o_m, o_s, o_a

    nd = _nodes(spec)
    check(spec.get("is_multi_user") is True, "spec flagged multi-user")
    check("platform:root" in nd, "platform root present")
    check("user:ua" in nd and "user:ub" in nd, "one node per user")
    check(nd["user:ua"]["properties"]["taxonomy"]["category"] == "Users", "user → Users top category")
    check("Users" in spec["taxonomy"], "Users is the top taxonomy layer")
    # per-user namespacing: the same profile id exists under BOTH users, separately
    check("u:ua:agent:p1" in nd and "u:ub:agent:p1" in nd, "same agent namespaced per user (no collision)")
    check(nd["u:ua:agent:p1"]["properties"]["owner_username"] == "alice", "node stamped with owner")
    # user linked to platform root; agent linked to its user (via class group)
    owns = [l for l in spec["links"] if l["type"] == "owns" and l["source"] == nd["platform:root"]["id"]]
    check(len(owns) == 2, "platform root owns both users")
    # NO ISLANDS across the whole aggregate
    n = len(spec["nodes"])
    adj = {i: set() for i in range(n)}
    for l in spec["links"]:
        adj[l["source"]].add(l["target"]); adj[l["target"]].add(l["source"])
    root = nd["platform:root"]["id"]
    seen, stack = {root}, [root]
    while stack:
        x = stack.pop()
        for y in adj[x]:
            if y not in seen:
                seen.add(y); stack.append(y)
    check(len(seen) == n, f"NO ISLANDS across multi-user aggregate (reached {len(seen)}/{n})")


def t_memory_department():
    # Memory is a top-tier department: Global pool + per-agent (Agents) local memories.
    mem = [_MemItem(None), _MemItem(None), _MemItem("p1")]  # 2 global, 1 for @OPTIM
    spec = _run(_FakeCM(_profiles(), LLMS, MCP, COLS), [], mem)
    nd = _nodes(spec)
    check("Memory" in spec["taxonomy"], "Memory is a top-tier department")
    check("memory:__global__" in nd, "global memory node present")
    check(nd["memory:__global__"]["properties"]["taxonomy"]["category"] == "Memory", "global memory → Memory dept")
    check(nd["memory:__global__"]["properties"]["taxonomy"]["subcategory"] == "Agent Memory", "global memory → Agent Memory")
    check("memory:p1" in nd, "per-agent memory node present")
    check(nd["memory:p1"]["properties"]["taxonomy"]["subcategory"] == "Agent Memory", "per-agent memory → Agent Memory sub-group")
    check(nd["memory:p1"]["name"] == "@OPTIM", "per-agent memory named by its agent tag")
    check(nd["memory:p1"]["properties"]["owner_profile_id"] == "p1", "per-agent memory stamps owner")


def t_empty():
    spec = _run(_FakeCM([], [], [], []), [], [])
    check(spec["nodes"] == [] and spec["findings"] == [], "empty user → empty graph + no findings")


def t_source_failure_degrades():
    spec = _run(_FakeCM(_profiles(), LLMS, MCP, COLS), [], [], kg_boom=True)
    nd = _nodes(spec)
    check("agent:p1" in nd, "agents present despite KG source failure")
    check(not any(x["type"] == "knowledge_graph" for x in spec["nodes"]), "no KG nodes (failed soft)")
    check(nd["agent:p1"]["properties"]["config_state"] == "grounded", "p1 still grounded via collections")



def t_kg_ownership_vs_grounding():
    # The two facts, two edges: `owns` from kg_metadata.profile_id (provenance),
    # `grounds_on` from kg_profile_assignments (runtime), exactly as the KG component
    # resolves it. The bug: the edge read "grounds_on" but was derived from ownership,
    # so an expert assigned to the coordinator's graph showed NO graph at all.
    profs = [
        {"id": "own", "tag": "COORD", "name": "Owner", "profile_type": "genie", "llmConfigurationId": "l1",
         "genieConfig": {"slaveProfiles": ["exp"]}},
        {"id": "exp", "tag": "NIBRS", "name": "Expert", "profile_type": "llm_only", "llmConfigurationId": "l1"},
        {"id": "solo", "tag": "SOLO", "name": "Solo owner, no rows", "profile_type": "llm_only", "llmConfigurationId": "l1"},
        {"id": "off", "tag": "OFF", "name": "Owner of an inactive graph", "profile_type": "llm_only", "llmConfigurationId": "l1"},
        {"id": "dang", "tag": "DANG", "name": "Assigned to an owner with no active graph", "profile_type": "llm_only", "llmConfigurationId": "l1"},
    ]
    graphs = [
        {"kg_id": "kgP", "profile_id": "own", "kg_name": "Policing", "is_active": True,
         "conformance_status": "conformant", "entity_types": {"domain": 2}},
        {"kg_id": "kgS", "profile_id": "solo", "kg_name": "Solo graph", "is_active": True, "entity_types": {"domain": 1}},
        {"kg_id": "kgOff", "profile_id": "off", "kg_name": "Parked graph", "is_active": False, "entity_types": {"domain": 3}},
    ]
    assignments = [
        {"kg_id": "kgP", "kg_owner_profile_id": "own", "assigned_profile_id": "own", "is_active": True},
        {"kg_id": "kgP", "kg_owner_profile_id": "own", "assigned_profile_id": "exp", "is_active": True},
        {"kg_id": "kgOff", "kg_owner_profile_id": "off", "assigned_profile_id": "dang", "is_active": True},
        {"kg_id": "kgS", "kg_owner_profile_id": "solo", "assigned_profile_id": "exp", "is_active": False},  # inactive row: ignored
    ]
    spec = _run(_FakeCM(profs, LLMS, MCP, []), graphs, [], assignments=assignments)
    nd = _nodes(spec)
    def edges(src, typ):
        return {l["target"] for l in spec["links"] if l["source"] == nd[src]["id"] and l["type"] == typ}
    kgP, kgS, kgOff = nd["kg:kgP"]["id"], nd["kg:kgS"]["id"], nd["kg:kgOff"]["id"]

    # owner with an active self-assignment: BOTH edges
    check(edges("agent:own", "owns") == {kgP}, "owner owns its graph")
    check(edges("agent:own", "grounds_on") == {kgP}, "owner grounds on its own active graph")
    # assigned expert: grounds_on WITHOUT owns (the reported bug)
    check(edges("agent:exp", "grounds_on") == {kgP}, "assigned expert grounds on the coordinator's graph (was missing)")
    check(edges("agent:exp", "owns") == set(), "assigned expert does not own it")
    check("Policing" in nd["agent:exp"]["properties"]["grounded_on"], "expert's grounding names the assigned graph")
    # inactive row is ignored — the expert does NOT also ground on kgS
    check(kgS not in edges("agent:exp", "grounds_on"), "an inactive assignment row draws nothing")
    # no rows at all → runtime falls back to the profile's own active graph
    check(edges("agent:solo", "grounds_on") == {kgS} and edges("agent:solo", "owns") == {kgS}, "no assignment rows → own active graph (runtime fallback)")
    # owner of an INACTIVE graph: owns without grounding
    check(edges("agent:off", "owns") == {kgOff}, "inactive graph is still owned (provenance)")
    check(edges("agent:off", "grounds_on") == set(), "inactive graph is not grounded on")
    # active row pointing at an owner with NO active graph: grounds on nothing, no fallback
    check(edges("agent:dang", "grounds_on") == set(), "active row → owner with no active graph grounds on NOTHING (mirrors runtime)")
    # node facts: owner stays the owner (the inspect deep-link resolves by owner); used_by = grounding population
    check(nd["kg:kgP"]["properties"]["owner_tag"] == "COORD" and nd["kg:kgP"]["properties"]["owner_profile_id"] == "own", "graph node keeps its OWNER facts")
    check(sorted(nd["kg:kgP"]["properties"]["used_by"]) == ["COORD", "NIBRS"], "used_by is the GROUNDING population, not the owner")
    check(nd["kg:kgP"]["properties"]["shared"] is True, "a graph grounding two agents is shared")
    check(nd["kg:kgOff"]["properties"].get("used_by") in (None, []), "an inactive graph grounds nobody")
    # estate coverage counts each drawn graph ONCE, however many agents ground on it
    f = _find(spec, "coverage")
    check(f is not None and "3 knowledge graph" in f["text"] and "6 business domain" in f["text"], f"coverage counts each graph once ({f and f['text']})")
    shared = _find(spec, "shared_kg_kgP")
    check(shared is not None and "@COORD" in shared["text"] and "@NIBRS" in shared["text"], "shared-KG finding names the grounding agents")

    # a graph whose OWNER no longer exists (pack reinstalled under new ids) but which an
    # agent is still assigned to: drawn (the runtime grounds on it), owner said to be
    # missing — never a raw id wearing a tag's clothes — and surfaced as a finding.
    graphs2 = graphs + [{"kg_id": "kgGone", "profile_id": "profile-deleted", "kg_name": "Orphan", "is_active": True}]
    asg2 = assignments + [{"kg_id": "kgGone", "kg_owner_profile_id": "profile-deleted", "assigned_profile_id": "dang", "is_active": True}]
    asg2 = [a for a in asg2 if not (a["assigned_profile_id"] == "dang" and a["kg_id"] == "kgOff")]
    spec2 = _run(_FakeCM(profs, LLMS, MCP, []), graphs2, [], assignments=asg2)
    nd2 = _nodes(spec2)
    check("kg:kgGone" in nd2, "orphaned graph is drawn because an agent grounds on it")
    check(nd2["kg:kgGone"]["properties"]["owner_missing"] is True and nd2["kg:kgGone"]["properties"]["owner_tag"] is None, "orphan: owner_missing, no fake owner tag")
    check(nd2["kg:kgP"]["properties"]["owner_missing"] is False, "a graph with a live owner is not marked orphaned")
    g2 = {l["target"] for l in spec2["links"] if l["source"] == nd2["agent:dang"]["id"] and l["type"] == "grounds_on"}
    check(g2 == {nd2["kg:kgGone"]["id"]}, "agent grounds on the orphaned graph (runtime truth)")
    orphan = _find(spec2, "orphaned_kgs")
    check(orphan is not None and orphan["severity"] == "warn" and orphan["focus"] == ["kg:kgGone"], "orphaned-graph finding names the graph")
    check(_find(spec, "orphaned_kgs") is None, "no orphan finding when every owner exists")

    # the pure resolver's truth table
    r = proj.resolve_grounding_kgs(assignments, {"own": graphs[0], "solo": graphs[1], "off": None})
    check(r == {"own": graphs[0], "exp": graphs[0], "dang": None}, "resolver: active rows only; owner's ACTIVE graph; None when the owner has none")
    check(proj.resolve_grounding_kgs([], {}) == {}, "resolver: no rows → empty")
    check(proj.resolve_grounding_kgs([{"kg_owner_profile_id": "x", "assigned_profile_id": "", "is_active": True}], {"x": graphs[0]}) == {}, "resolver: blank assignee ignored")

if __name__ == "__main__":
    spec = t_config_model()
    t_findings(spec)
    t_spec_contract(spec)
    t_taxonomy_and_connectivity(spec)
    t_sessions()
    t_planner_vs_knowledge_repos()
    t_rag_focused_without_enabled_flag()
    t_multi_user()
    t_memory_department()
    t_skills()
    t_packs()
    t_empty()
    t_source_failure_degrades()
    t_kg_ownership_vs_grounding()
    print(f"\nplatform_ontology: {_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
