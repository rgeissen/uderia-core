"""Build-on-read projection of a user's platform-ontology segment.

DESIGN RULE (the reason this view exists): the Ontology must NOT re-draw per-agent
configuration — the profile editor already shows an agent's LLM/MCP/collections/KG,
Agent Performance shows its health, Workspace lists instances. Re-drawing that is
redundant. So this projection surfaces ONLY what no other screen can: the
**systemic + semantic + trust** layer of the estate —

  • TRUST / GROUNDING  — which agents are grounded (trustworthy) vs floating free;
                         whether their knowledge is conformant/governed.
  • SHARING / BLAST-RADIUS — which knowledge is shared across agents (reuse; what
                         breaks if you remove it).
  • KNOWLEDGE COVERAGE — what the whole estate collectively knows (domains across
                         all KGs) and which agent reaches it.
  • GAPS               — ungrounded agents, unused collections.

Config-level facts that are genuinely systemic (a model shared by N agents) are
surfaced as **findings**, never re-drawn as nodes. The graph stays a knowledge +
trust map, not a config diagram.

``build_segment(user_uuid)`` returns a renderer-ready graph-spec (same shape as
``GET /v1/knowledge-graph/graph-spec``) PLUS ``findings`` (the insight header) and
per-node inspect/act metadata. Pure-ish: only reads source-of-truth tables; never
writes; any single source failing degrades that slice rather than failing the whole.

See docs/roadmap/TIER_5_PLATFORM_ONTOLOGY.md §5/§5a.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("quart.app")

# Agents are coloured by IFOC class (the platform's own visual language) by
# splitting the node type per class — the renderer's type→colour map + filter
# pills then give per-class colour and an IFOC filter for free. Resource types
# carry knowledge/trust-meaningful colours; pure-config types are never nodes.
PLATFORM_ENTITY_COLORS: Dict[str, str] = {
    "segment": "#cbd5e1",           # the taxonomy root — your whole world
    "class_group": "#64748b",       # IFOC class grouping (taxonomy level)
    "user": "#e2e8f0",              # a platform user (admin multi-user scope, top layer)
    "session": "#fbbf24",           # a conversation run (Sessions department)
    "agent_ideate": "#4ade80",      # green
    "agent_focus": "#3b82f6",       # blue
    "agent_optimize": "#9333ea",    # purple
    "agent_coordinate": "#F15F22",  # orange
    "agent": "#94a3b8",             # fallback (unknown class)
    "knowledge_graph": "#10b981",   # emerald — the trusted-knowledge layer
    "collection": "#38bdf8",        # sky — knowledge repository (reference docs)
    "collection_planner": "#818cf8",  # indigo — planner repository (champion strategies)
    "memory": "#a855f7",            # purple — what an agent remembers
    "domain": "#f59e0b",            # amber — knowledge coverage (future expansion)
    "component": "#a78bfa",         # violet — a capability the agent can use
    "llm": "#8b5cf6",               # violet — Models
    "mcp_server": "#0ea5e9",        # sky — Connections (MCP servers)
    "platform_connector": "#06b6d4",  # cyan — Platform Connectors (a Components sub-class)
    "skill": "#14b8a6",             # teal — Capabilities (skills)
    "extension": "#eab308",         # amber — Capabilities (extensions)
    "agent_pack": "#ec4899",        # pink — Packages
    "vector_store": "#2dd4bf",      # teal — Vector Store backend (Anatomy)
    "kg_backend": "#34d399",        # emerald — Graph Store backend (Anatomy)
    "context_window": "#c084fc",    # lilac — Context Window (Memory subtype)
}

# IFOC class → (node type, human label).
_IFOC = {
    "llm_only": ("agent_ideate", "Ideate"),
    "conversation_with_tools": ("agent_ideate", "Ideate"),
    "rag_focused": ("agent_focus", "Focus"),
    "tool_enabled": ("agent_optimize", "Optimize"),
    "genie": ("agent_coordinate", "Coordinate"),
}

_IMPORTANCE = {
    "segment": 1.9, "class_group": 1.2, "user": 1.7,
    "agent_ideate": 1.0, "agent_focus": 1.0, "agent_optimize": 1.0,
    "agent_coordinate": 1.0, "agent": 1.0,
    "knowledge_graph": 0.6, "collection": 0.45, "collection_planner": 0.45, "memory": 0.35, "domain": 0.5,
}

# The overarching PLATFORM TAXONOMY — every node classifies into exactly one
# (category → subcategory). It is both the connectivity spine (root → category →
# member, so nothing is an island) and a selection facet on the ontology.
_AGENT_CLASS_LABEL = {
    "agent_ideate": "Ideate", "agent_focus": "Focus",
    "agent_optimize": "Optimize", "agent_coordinate": "Coordinate", "agent": "Agents",
}


# Canonical platform taxonomy: node type → (category, subcategory). The FOUR top
# categories mirror the platform's sidebar object departments — Agents · Sessions ·
# Intelligence · Components — so the ontology is navigated with the same mental model
# as the rest of the app. Configuration objects (models, connectors, skills,
# extensions, packs) are "Setup" items, so they live UNDER the agent (config
# composition on expand), classified to Agents/Components — never a top department.
_TAXONOMY = {
    "segment": ("Ontology", None),
    "class_group": ("Agents", None),                 # subcategory filled from ifoc_class
    "session": ("Sessions", None),
    "knowledge_graph": ("Intelligence", "Knowledge Graphs"),
    "collection": ("Intelligence", "Knowledge Repositories"),
    "collection_planner": ("Intelligence", "Planner Repositories"),
    "memory": ("Intelligence", "Memory"),
    "domain": ("Intelligence", "Domains"),
    "component": ("Components", None),                # subcategory from component category
    "instance": ("Components", "Instances"),
    "platform_connector": ("Components", "Connectors"),
    # Anatomy department — mirrors the Anatomy view's tab bar: the SETUP
    # artifacts as first-class nodes, mapped to whatever uses them.
    "skill": ("Anatomy", "Skills"),
    "extension": ("Anatomy", "Extensions"),
    "connector": ("Anatomy", "MCP Servers"),
    "mcp_server": ("Anatomy", "MCP Servers"),
    "llm": ("Anatomy", "LLMs"),
    "vector_store": ("Anatomy", "Vector Store Backends"),
    "kg_backend": ("Anatomy", "Graph Store Backends"),
    "agent_pack": ("Anatomy", "Agent Packs"),
    "context_window": ("Memory", "Context Window"),
}


def _taxonomy_for(ntype: str, props: dict) -> dict:
    """Classify a node into the platform taxonomy: {category, subcategory}."""
    if ntype.startswith("agent") and ntype not in _TAXONOMY:
        return {"category": "Agents", "subcategory": props.get("ifoc_class") or "Agents"}
    if ntype == "class_group":
        return {"category": "Agents", "subcategory": props.get("ifoc_class")}
    if ntype == "component":
        # Components department splits into two sub-classes: the user-facing components
        # (chart/canvas/dashboard/flow/KG/memory/scheduler…) live under "User Interface";
        # platform connectors (below) live under "Connectors".
        return {"category": "Components", "subcategory": "User Interface"}
    if ntype == "session":
        # Cluster sessions by day (the tree groups by subcategory, so this buckets the
        # Sessions department into per-day folders).
        day = str(props.get("last_updated") or props.get("created_at") or "")[:10] or "Undated"
        return {"category": "Sessions", "subcategory": day}
    if ntype == "memory":
        # Memory department has two distinct subtypes: Agent Memory (durable user facts —
        # the global pool + each agent's local pool) and Context Window (session working
        # memory, added below as its own subcategory). Memory nodes are Agent Memory.
        return {"category": "Memory", "subcategory": "Agent Memory"}
    cat, sub = _TAXONOMY.get(ntype, ("Other", None))
    return {"category": cat, "subcategory": sub}


def build_segment(user_uuid: str, sessions: Optional[List[dict]] = None,
                  packs: Optional[List[dict]] = None) -> Dict[str, Any]:
    """Derive the user's ontology segment + findings and return a renderer-ready spec.

    `sessions` / `packs` (optional) are lists fetched by the async REST route
    (get_all_sessions / list_packs are async; the projection is sync/off-loop). When
    present, sessions become Sessions-department nodes linked to the agent they ran
    under, and agent packs become Agents/Packs nodes linked to their coordinator.
    """
    from trusted_data_agent.core.config_manager import get_config_manager

    cm = get_config_manager()
    profiles = _safe(lambda: cm.get_profiles(user_uuid), [])

    # All collections in the estate (to detect unused ones — a gap no view shows).
    # collection_meta carries the per-collection facts the sidebar needs + the repo
    # type the Intelligence deep-link requires to open the right detail view.
    all_collections = {}
    collection_meta: Dict[Any, dict] = {}
    for col in _safe(lambda: cm.get_rag_collections(user_uuid), []):
        cid = col.get("id")
        if cid is not None:
            all_collections[cid] = col.get("name") or col.get("collection_name") or f"Collection {cid}"
            collection_meta[cid] = {
                "repository_type": col.get("repository_type") or "knowledge",
                "embedding_model": col.get("embedding_model") or col.get("embeddingModel"),
                "document_count": col.get("document_count") or col.get("chunk_count"),
                "owner_username": col.get("owner_username"),
                "backend_type": col.get("backend_type") or "chromadb",
                "vector_store_config_id": col.get("vector_store_config_id"),
            }

    # LLM / connector names — used ONLY to label "shared by N agents" findings.
    llm_name = {c.get("id"): (c.get("name") or "LLM") for c in _safe(lambda: cm.get_llm_configurations(user_uuid), [])}
    mcp_name = {s.get("id"): (s.get("name") or "Connector") for s in _safe(lambda: cm.get_mcp_servers(user_uuid), [])}

    # Knowledge graphs — TWO facts, TWO edges, one source each (Sep 2026):
    #   owns       — kg_metadata.profile_id: PROVENANCE, who created/maintains the graph.
    #   grounds_on — what the agent is grounded on AT RUNTIME. The KG component resolves
    #                it from kg_profile_assignments: the ACTIVE row names an owner, and
    #                the agent grounds on that owner's ACTIVE graph; with no active row it
    #                falls back to its OWN active graph (handler.get_context_enrichment).
    #                An assigned agent therefore grounds on a graph it never owned, and an
    #                owner whose graph is inactive owns it without grounding on it.
    #   Before this split the edge was labelled grounds_on but derived from ownership, so
    #   every pack expert grounded on the coordinator's graph showed no graph at all.
    all_kgs = _safe(lambda: _list_graphs(user_uuid), [])
    kgs_by_owner: Dict[str, List[dict]] = defaultdict(list)
    for kg in all_kgs:
        kgs_by_owner[kg.get("profile_id")].append(kg)
    active_kg_of_owner: Dict[str, Optional[dict]] = {
        pid: next((g for g in gs if g.get("is_active")), None) for pid, gs in kgs_by_owner.items()
    }
    grounding_kg = resolve_grounding_kgs(_safe(lambda: _list_kg_assignments(user_uuid), []),
                                         active_kg_of_owner)

    mem_count: Dict[Any, int] = defaultdict(int)
    for item in _safe(lambda: _list_memory(user_uuid), []):
        mem_count[getattr(item, "profile_id", None)] += 1

    # --- graph assembly -----------------------------------------------------
    nodes: List[dict] = []
    links: List[dict] = []
    index: Dict[str, int] = {}

    # tag lookup for friendly finding text
    tag_of = {p.get("id"): (p.get("tag") or p.get("name") or p.get("id")) for p in profiles}
    profile_ids = set(tag_of.keys())

    def add_node(entity_id: str, name: str, ntype: str, properties: dict) -> int:
        if entity_id in index:
            return index[entity_id]
        idx = len(nodes)
        index[entity_id] = idx
        nodes.append({
            "id": idx, "entity_id": entity_id, "name": name, "type": ntype,
            "properties": properties or {}, "importance": _IMPORTANCE.get(ntype, 0.3),
        })
        return idx

    def add_link(src_eid: str, tgt_eid: str, rtype: str) -> None:
        s, t = index.get(src_eid), index.get(tgt_eid)
        if s is None or t is None or s == t:
            return
        links.append({"source": s, "target": t, "type": rtype})

    # Usage maps (for sharing / blast-radius findings).
    collection_users: Dict[Any, List[str]] = defaultdict(list)   # cid -> [agent tag]
    kg_users: Dict[str, List[str]] = defaultdict(list)
    llm_users: Dict[str, List[str]] = defaultdict(list)
    mcp_users: Dict[str, List[str]] = defaultdict(list)
    unconfigured: List[str] = []                 # profile ids that are empty shells
    inaccessible_refs: List[tuple] = []          # (agent tag, collection id) — broken bindings
    domain_total = 0
    kg_count = 0
    kg_conformant = 0
    orphan_kgs: List[str] = []   # drawn graphs whose owning profile no longer exists

    # Per-agent ENABLED action components (capabilities the agent can use). Resolved via
    # the same authoritative governance the profile editor enforces. These feed the
    # expandable component combos + the inspect panel — what each agent can DO.
    comp_map = _safe(lambda: _agent_components(profiles, user_uuid), {})
    conn_map = _safe(lambda: _agent_connectors(profiles), {})   # platform connectors per agent

    # Pass 1: agent nodes (IFOC-typed) — so coordinate edges can target any agent.
    for p in profiles:
        pid = p.get("id")
        if not pid:
            continue
        ntype, label = _IFOC.get(p.get("profile_type") or "llm_only", ("agent", p.get("profile_type")))
        add_node(f"agent:{pid}", p.get("tag") or p.get("name") or pid, ntype, {
            "kind": "agent", "profile_id": pid, "name": p.get("name"), "tag": p.get("tag"),
            "ifoc_class": label, "profile_type": p.get("profile_type"),
            "color": p.get("color"), "is_default": bool(p.get("isDefault")),
            "description": p.get("description") or "",
            "components": comp_map.get(pid, []),
            "connectors": conn_map.get(pid, []),   # platform connectors (Components → Connectors)
        })

    # Taxonomy backbone: a root segment node → IFOC class groups → agents, so the
    # whole graph is one connected hierarchy (no islands) and the taxonomy is the
    # spine. Knowledge/component nodes hang off their agents (added in Pass 2), so
    # they reach the root through their agent. Skipped for an empty user (no agents).
    if any(p.get("id") for p in profiles):
        root_eid = "segment:root"
        add_node(root_eid, "Ontology", "segment", {"kind": "segment"})
        _groups: Dict[str, bool] = {}
        for p in profiles:
            pid = p.get("id")
            if not pid:
                continue
            atype = nodes[index[f"agent:{pid}"]]["type"]
            cls = _AGENT_CLASS_LABEL.get(atype, "Agents")
            geid = f"group:{cls}"
            if geid not in _groups:
                add_node(geid, cls, "class_group", {"kind": "class_group", "ifoc_class": cls})
                add_link(root_eid, geid, "contains")
                _groups[geid] = True
            add_link(geid, f"agent:{pid}", "contains")

    # Pass 2: grounding (the trust layer) + sharing tallies + coordinate edges.
    for p in profiles:
        pid = p.get("id")
        if not pid:
            continue
        aeid = f"agent:{pid}"
        atag = tag_of[pid]
        is_coord = (p.get("profile_type") == "genie")
        acc_collections: List[str] = []   # ENABLED + ACCESSIBLE knowledge collection names
        planner_names: List[str] = []     # accessible planner (champion-case) collections
        kg_names: List[str] = []
        _agent_cids = set()               # collections already drawn for this agent

        def _add_collection(cid, rel):
            """Draw a collection node + edge for this agent (dedup edges per agent).

            The node type is the collection's intrinsic repository_type — a planner
            (champion-strategy) repository is a distinct type/colour/subcategory from a
            knowledge (reference-doc) repository, regardless of which agent references it.
            """
            cname = all_collections[cid]
            meta = collection_meta.get(cid) or {}
            ntype = "collection_planner" if (meta.get("repository_type") == "planner") else "collection"
            if cid not in _agent_cids:
                collection_users[cid].append(atag)
            add_node(f"collection:{cid}", cname, ntype, {
                "kind": "collection", "collection_id": cid, **meta,
            })
            add_link(aeid, f"collection:{cid}", rel)
            _agent_cids.add(cid)
            return cname

        # Knowledge collections — grounds an agent ONLY when knowledge is ENABLED and
        # the collection is ACCESSIBLE (owned/subscribed). A reference to a collection
        # the user can't access is a misconfiguration (finding), never drawn.
        kc = p.get("knowledgeConfig") or {}
        # rag_focused (Focus) grounds on its collections UNCONDITIONALLY — knowledge IS
        # the profile's function and FocusEngine reads `collections` directly, ignoring
        # `enabled` (which rag_focused profiles don't set; agent-pack-imported ones never
        # carry it). For llm_only/tool_enabled the knowledge repo is a supplement that the
        # context module gates on `enabled`, so keep the gate for those types.
        knowledge_active = bool(kc.get("enabled")) or p.get("profile_type") == "rag_focused"
        if knowledge_active:
            for cref in (kc.get("collections") or []):
                cid = cref.get("id") if isinstance(cref, dict) else cref
                if cid is None:
                    continue
                if cid not in all_collections:
                    inaccessible_refs.append((atag, cid))
                    continue
                acc_collections.append(_add_collection(cid, "grounds_on"))

        # Planner collections — for PLANNING agents (tool_enabled/genie), `ragCollections`
        # is the champion-case repository the planner learns from (NOT a stale default for
        # these types). Accessibility-gated; non-planning agents ignore the field.
        if p.get("profile_type") in ("tool_enabled", "genie"):
            for cref in (p.get("ragCollections") or []):
                cid = cref.get("id") if isinstance(cref, dict) else cref
                if cid is None or cid in _agent_cids or cid not in all_collections:
                    continue
                planner_names.append(_add_collection(cid, "plans_with"))

        # Knowledge graphs — the node carries the OWNER facts (the inspect deep-link
        # resolves by owner); the estate coverage counts each drawn graph ONCE, however
        # many agents ground on it.
        def _kg_node(kg: dict) -> str:
            nonlocal domain_total, kg_count, kg_conformant
            kid = kg.get("kg_id")
            keid = f"kg:{kid}"
            if keid in index:
                return keid
            owner_pid = kg.get("profile_id")
            etypes = kg.get("entity_types") or {}
            doms = int(etypes.get("domain", 0) or 0)
            domain_total += doms
            kg_count += 1
            if (kg.get("conformance_status") or "").lower() == "conformant":
                kg_conformant += 1
            # An owner that is no longer one of this user's agents (a pack reinstalled
            # under new ids, a deleted profile) is a dangling ownership — said so on the
            # node, never rendered as a raw id wearing a tag's clothes.
            owner_missing = owner_pid not in profile_ids
            if owner_missing:
                orphan_kgs.append(keid)
            add_node(keid, kg.get("kg_name") or "Knowledge Graph", "knowledge_graph", {
                "kind": "knowledge_graph", "kg_id": kid,
                "owner_profile_id": owner_pid,
                "owner_tag": None if owner_missing else tag_of.get(owner_pid),
                "owner_missing": owner_missing,
                "entities": kg.get("total_entities"), "relationships": kg.get("total_relationships"),
                "domains": doms, "is_active": kg.get("is_active"),
                "lifecycle_status": kg.get("lifecycle_status"),
                "conformance_status": kg.get("conformance_status"),
            })
            return keid

        # owns — provenance, active or not.
        for kg in kgs_by_owner.get(pid, []):
            add_link(aeid, _kg_node(kg), "owns")
        # grounds_on — the runtime fact (active assignment → owner's active graph; no
        # active row → own active graph; an active row to an owner with no active graph
        # grounds on NOTHING, exactly as the runtime resolves it).
        gkg = grounding_kg[pid] if pid in grounding_kg else active_kg_of_owner.get(pid)
        if gkg:
            keid = _kg_node(gkg)
            kg_users[gkg.get("kg_id")].append(atag)
            add_link(aeid, keid, "grounds_on")
            kg_names.append(gkg.get("kg_name") or "Knowledge Graph")

        # Memory (what it remembers) → the agent's local memory pool, grouped under
        # Memory → Agents. Named by the agent tag. Created when the agent has remembered
        # items OR simply has the Agent Memory component ACTIVATED (so an activated agent
        # always has a memory node to point at, even before it remembers anything).
        cnt = mem_count.get(pid, 0)
        mem_on = any((c.get("id") == "memory" and c.get("enabled")) for c in comp_map.get(pid, []))
        if cnt > 0 or mem_on:
            add_node(f"memory:{pid}", f"@{atag}", "memory",
                     {"kind": "memory", "items": cnt, "scope": "agent",
                      "owner_tag": atag, "owner_profile_id": pid})
            add_link(aeid, f"memory:{pid}", "remembers")

        # Coordinator → expert edges
        slaves = (p.get("genieConfig") or {}).get("slaveProfiles") or []
        for sid in slaves:
            if sid in profile_ids:
                add_link(aeid, f"agent:{sid}", "coordinates")
        experts_n = sum(1 for s in slaves if s in profile_ids)

        # Live MCP tools — a real data capability, ONLY when explicitly enabled.
        mid = p.get("mcpServerId")
        has_mcp_tools = bool(mid) and (p.get("useMcpTools") is True) and (mid in mcp_name)

        # Config sharing (findings only, never nodes): LLM is always bound; the MCP
        # server only counts when the agent actually has its tools enabled.
        lid = p.get("llmConfigurationId")
        if lid:
            llm_users[lid].append(atag)
        if has_mcp_tools:
            mcp_users[mid].append(atag)

        # Configuration / grounding model. CONFIGURED = has ANY real, usable capability
        # (accessible collection · active KG · live MCP tools · coordinator experts).
        # An empty bootstrap shell is UNCONFIGURED — clearly marked, never a trust risk.
        grounded_on = list(acc_collections) + list(planner_names) + list(kg_names)
        if experts_n:
            grounded_on.append(f"{experts_n} expert{'s' if experts_n != 1 else ''}")
        if has_mcp_tools:
            grounded_on.append(f"live tools via {mcp_name.get(mid, 'MCP')}")
        configured = bool(grounded_on)
        props = nodes[index[aeid]]["properties"]
        props["configured"] = configured
        props["config_state"] = "grounded" if configured else "unconfigured"
        props["grounded"] = configured
        props["grounded_on"] = grounded_on
        props["is_coordinator"] = is_coord
        props["expert_count"] = experts_n
        props["has_mcp_tools"] = has_mcp_tools
        # Config-node ids for the agent-expand composition (model + connector nodes).
        props["llm_id"] = lid if (lid and lid in llm_name) else None
        props["connector_id"] = mid if (mid and mid in mcp_name) else None
        # Model + connector names + the profile id, so the sidebar shows relevant
        # detail and links to THIS profile's configuration.
        props["llm"] = llm_name.get(lid) if lid else None
        props["connector"] = mcp_name.get(mid) if (mid and mid in mcp_name) else None
        if not configured:
            unconfigured.append(pid)

    # Global memory pool — recalled by every agent. Always present (even with 0 items)
    # so the Memory department is a persistent top-level topic; linked to the root when
    # empty (no remembers edges yet) so it's never an island.
    if profile_ids:
        g_items = mem_count.get(None, 0)
        add_node("memory:__global__", "Global Memory", "memory",
                 {"kind": "memory", "items": g_items, "scope": "global"})
        if g_items > 0:
            for pid in profile_ids:
                add_link(f"agent:{pid}", "memory:__global__", "remembers")
        else:
            add_link("segment:root", "memory:__global__", "contains")

    # Components department — the FULL, dynamically-discovered catalog of action
    # components (sourced from the component manager via `comp_map`, never a hardcoded
    # list). Every component appears, whether or not any agent has it enabled: those in
    # use link to the agents that enabled them (`can_use`); the rest hang off the root so
    # the department always shows the complete catalogue (no island, none hidden). The
    # per-agent enabled/disabled composition is still shown separately on agent-expand.
    # Two distinct states per (component, agent): ACTIVATED (the profile turned it on) vs
    # AVAILABLE (governance permits it, but the profile hasn't activated it).
    comp_catalog: Dict[str, dict] = {}             # cid -> meta (dynamic; union over agents)
    comp_activated_by: Dict[str, list] = defaultdict(list)
    comp_available_by: Dict[str, list] = defaultdict(list)
    for p in profiles:
        pid = p.get("id")
        if not pid:
            continue
        for c in comp_map.get(pid, []):
            cid = c.get("id")
            if not cid:
                continue
            comp_catalog.setdefault(cid, c)
            if c.get("enabled"):
                comp_activated_by[cid].append(pid)
            if c.get("available"):
                comp_available_by[cid].append(pid)
    for cid, c in comp_catalog.items():
        ceid = f"component:{cid}"
        activated_pids = comp_activated_by.get(cid, [])
        available_pids = comp_available_by.get(cid, [])
        # Always carry the component's real accent; the UI greys the dot by INSTANTIATION
        # count (colour iff it has instances), not by enablement.
        accent = c.get("accent") or PLATFORM_ENTITY_COLORS.get("component")
        add_node(ceid, c.get("name") or cid, "component", {
            "kind": "component", "component_id": cid, "category": "component",
            "accent": accent,
            "activated_by": [tag_of.get(x) for x in activated_pids],
            "available_by": [tag_of.get(x) for x in available_pids],
            "used_by": [tag_of.get(x) for x in activated_pids],   # back-compat = activated
            "enabled_anywhere": bool(activated_pids),
        })
        # Activated agents link via `can_use`; agents where it's only available (not
        # activated) link via `available` so the distinction shows in the graph too.
        for pid in activated_pids:
            add_link(f"agent:{pid}", ceid, "can_use")
        for pid in available_pids:
            if pid not in activated_pids:
                add_link(f"agent:{pid}", ceid, "available")
        if not activated_pids and not available_pids:
            add_link("segment:root", ceid, "contains")   # nowhere available → catalogue only

    # Connectors (Components department, sub-class) — the platform connectors (browser/
    # files/web/Google/Slack…) as shared nodes, each linked to the agents that enabled it
    # (`can_use`). Full catalogue: connectors no agent enabled hang off the root.
    conn_catalog: Dict[str, dict] = {}
    conn_enabled_by: Dict[str, list] = defaultdict(list)
    for p in profiles:
        pid = p.get("id")
        if not pid:
            continue
        for c in conn_map.get(pid, []):
            cid = c.get("id")
            if not cid:
                continue
            conn_catalog.setdefault(cid, c)
            if c.get("enabled"):
                conn_enabled_by[cid].append(pid)
    for cid, c in conn_catalog.items():
        ceid = f"platform_connector:{cid}"
        pids = conn_enabled_by.get(cid, [])
        add_node(ceid, c.get("name") or cid, "platform_connector", {
            "kind": "platform_connector", "connector_id": cid,
            "requires_user_auth": c.get("requires_user_auth"),
            "used_by": [tag_of.get(x) for x in pids],
        })
        if pids:
            for pid in pids:
                add_link(f"agent:{pid}", ceid, "can_use")
        else:
            add_link("segment:root", ceid, "contains")

    # Skills (Components department) — a skill active on a profile becomes a shared node
    # linked to each agent that uses it. Two distinct states, mirroring the component
    # activated/available split: EXPLICIT (the user opted the skill in) vs AUTO (a
    # companion skill auto-assigned by its component's enablement).
    skill_meta = {s.get("skill_id"): s for s in _safe(lambda: _list_skills(), [])}
    skill_users: Dict[str, list] = defaultdict(list)
    skill_auto_by: Dict[str, list] = defaultdict(list)
    skill_explicit_by: Dict[str, list] = defaultdict(list)
    for p in profiles:
        pid = p.get("id")
        if not pid:
            continue
        sc = (p.get("skillsConfig") or {}).get("skills")
        entries = sc.items() if isinstance(sc, dict) else [
            ((e.get("skill_id") or e.get("id")), e) for e in (sc or []) if isinstance(e, dict)
        ]
        for sid, entry in entries:
            if sid and isinstance(entry, dict) and entry.get("active"):
                skill_users[sid].append(pid)
                (skill_auto_by if entry.get("auto") else skill_explicit_by)[sid].append(pid)
    for sid, pids in skill_users.items():
        meta = skill_meta.get(sid, {})
        seid = f"skill:{sid}"
        add_node(seid, meta.get("display_name") or meta.get("name") or sid, "skill", {
            "kind": "skill", "skill_id": sid, "description": meta.get("description"),
            "is_builtin": meta.get("is_builtin", True), "component_id": meta.get("component_id"),
            "used_by": [tag_of.get(x) for x in pids],
            "explicit_by": [tag_of.get(x) for x in skill_explicit_by.get(sid, [])],
            "auto_by": [tag_of.get(x) for x in skill_auto_by.get(sid, [])],
        })
        for pid in pids:
            add_link(f"agent:{pid}", seid, "uses")

    # Sessions department — recent conversation runs, each linked to the agent it ran
    # under (no island). Sessions are fetched async by the route and passed in; capped
    # to the most recent so the graph stays legible. Orphan runs (deleted profile) hang
    # off the root so they're still reachable.
    if sessions:
        def _sort_key(s):
            return s.get("last_updated") or s.get("created_at") or ""
        recent = sorted(sessions, key=_sort_key, reverse=True)[:40]
        for s in recent:
            sid = s.get("id") or s.get("session_id")
            if not sid:
                continue
            seid = f"session:{sid}"
            pid = s.get("profile_id")
            ptag = s.get("profile_tag") or (tag_of.get(pid) if pid else None)
            add_node(seid, s.get("name") or "Untitled session", "session", {
                "kind": "session", "session_id": sid, "profile_tag": ptag, "profile_id": pid,
                "last_updated": s.get("last_updated"), "created_at": s.get("created_at"),
                "models": s.get("models_used") or [],
                "is_genie_slave": bool((s.get("genie_metadata") or {}).get("is_genie_slave")),
                "archived": bool(s.get("archived")),
            })
            if pid and pid in profile_ids:
                add_link(f"agent:{pid}", seid, "ran")
            else:
                add_link("segment:root", seid, "contains")

    # Agent Packs (Agents department) — each installed pack linked to its coordinator
    # agent (experts chain off the coordinator via `coordinates` edges, so the pack's
    # full membership is reachable). Non-coordinator packs hang off the root.
    if packs:
        for pk in packs:
            pkid = pk.get("installation_id") or pk.get("id")
            if not pkid:
                continue
            peid = f"pack:{pkid}"
            coord = pk.get("coordinator_profile_id")
            add_node(peid, pk.get("name") or "Agent Pack", "agent_pack", {
                "kind": "agent_pack", "installation_id": pkid, "version": pk.get("version"),
                "author": pk.get("author"), "description": pk.get("description"),
                "pack_type": pk.get("pack_type"), "profiles_count": pk.get("profiles_count"),
                "experts_count": pk.get("experts_count"), "collections_count": pk.get("collections_count"),
                "coordinator_tag": pk.get("coordinator_tag"), "is_owned": bool(pk.get("is_owned")),
            })
            if coord and coord in profile_ids:
                add_link(peid, f"agent:{coord}", "bundles")
            else:
                add_link("segment:root", peid, "contains")

    # ──────────────────────────────────────────────────────────────────────
    # Anatomy department — mirror of the Anatomy view. The SETUP
    # artifacts become first-class nodes, each mapped (linked) to whatever uses
    # it. (Skills + Agent Packs already have nodes; only their taxonomy moved here.)
    # ──────────────────────────────────────────────────────────────────────
    def _cfg_link(eid: str, target_eids, rel: str) -> None:
        """Link a config node to each EXISTING target, else hang it off the root."""
        linked = False
        for t in target_eids:
            if t in index:
                add_link(eid, t, rel); linked = True
        if not linked:
            add_link("segment:root", eid, "contains")

    # Which agents use each LLM / MCP server / Context Window type.
    llm_pids: Dict[str, list] = defaultdict(list)
    mcp_pids: Dict[str, list] = defaultdict(list)
    cwt_pids: Dict[str, list] = defaultdict(list)
    for p in profiles:
        pid = p.get("id")
        if not pid or pid not in profile_ids:
            continue
        if p.get("llmConfigurationId"):
            llm_pids[p["llmConfigurationId"]].append(pid)
        if p.get("mcpServerId") and p.get("useMcpTools") is True:
            mcp_pids[p["mcpServerId"]].append(pid)
        cwt_pids[p.get("contextWindowTypeId") or "cwt-default-balanced"].append(pid)

    # LLMs — every configured model, linked to the agents that use it.
    for c in (_safe(lambda: cm.get_llm_configurations(user_uuid), []) if profile_ids else []):
        lid = c.get("id")
        if not lid:
            continue
        leid = f"llm:{lid}"
        pids = llm_pids.get(lid, [])
        add_node(leid, c.get("name") or "LLM", "llm", {
            "kind": "llm", "llm_id": lid, "provider": c.get("provider"),
            "model": c.get("model") or c.get("model_name") or c.get("modelName"),
            "used_by": [tag_of.get(x) for x in pids],
        })
        _cfg_link(leid, [f"agent:{x}" for x in pids], "uses_model")

    # MCP servers — every configured data-source connector, linked to the agents that
    # have its tools enabled.
    for s in (_safe(lambda: cm.get_mcp_servers(user_uuid), []) if profile_ids else []):
        sid = s.get("id")
        if not sid:
            continue
        seid = f"mcp_server:{sid}"
        pids = mcp_pids.get(sid, [])
        add_node(seid, s.get("name") or "MCP Server", "mcp_server", {
            "kind": "mcp_server", "server_id": sid,
            "used_by": [tag_of.get(x) for x in pids],
        })
        _cfg_link(seid, [f"agent:{x}" for x in pids], "connected_by")

    # Vector Store backends — one node per backend (a named config, or the built-in local
    # ChromaDB), linked to the collections it backs.
    vs_name = {v.get("id"): v for v in _safe(lambda: cm.get_vector_store_configurations(user_uuid), [])}
    vs_collections: Dict[str, list] = defaultdict(list)
    vs_label: Dict[str, tuple] = {}
    for cid, meta in (collection_meta.items() if profile_ids else []):
        vid = meta.get("vector_store_config_id")
        if vid and vid in vs_name:
            veid = f"vector_store:{vid}"; cfg = vs_name[vid]
            vs_label[veid] = (cfg.get("name") or "Vector Store", cfg.get("backend_type") or "chromadb")
        else:
            veid = "vector_store:chromadb-local"
            vs_label[veid] = ("ChromaDB (local)", "chromadb")
        vs_collections[veid].append(f"collection:{cid}")
    for veid, cols in vs_collections.items():
        label, btype = vs_label[veid]
        add_node(veid, label, "vector_store", {
            "kind": "vector_store", "backend_type": btype, "collection_count": len(cols),
        })
        _cfg_link(veid, cols, "backs")

    # Graph Store backends — one node per backend (a named KG-store config, or the built-in
    # local SQLite), linked to the KGs it backs.
    kg_cfg = {k.get("id"): k for k in _safe(lambda: cm.get_kg_backend_configurations(user_uuid), [])}
    kgb_graphs: Dict[str, list] = defaultdict(list)
    kgb_label: Dict[str, tuple] = {}
    for g in (_safe(lambda: _list_graphs(user_uuid), []) if profile_ids else []):
        gid = g.get("kg_id")
        if not gid:
            continue
        cfgid = g.get("kg_store_config_id")
        if cfgid and cfgid in kg_cfg:
            beid = f"kg_backend:{cfgid}"; cfg = kg_cfg[cfgid]
            kgb_label[beid] = (cfg.get("name") or "Graph Store", cfg.get("backend_type") or "sqlite")
        else:
            beid = "kg_backend:sqlite-local"
            kgb_label[beid] = ("SQLite (local)", "sqlite")
        kgb_graphs[beid].append(f"kg:{gid}")
    for beid, gs in kgb_graphs.items():
        label, btype = kgb_label[beid]
        add_node(beid, label, "kg_backend", {
            "kind": "kg_backend", "backend_type": btype, "kg_count": len(gs),
        })
        _cfg_link(beid, gs, "backs")

    # Extensions — installed extensions catalogue. Extensions attach to flow task_llm
    # nodes, so each links to the agents that own the flows using it (sessions don't carry
    # extensions); a `used_in_flows` key-fact names those flows.
    ext_flows: Dict[str, list] = defaultdict(list)
    ext_agents: Dict[str, set] = defaultdict(set)
    for f in (_safe(lambda: _list_flows(user_uuid), []) if profile_ids else []):
        fname = f.get("name") or "Flow"
        fpid = f.get("default_profile_id")
        for nd in ((f.get("definition") or {}).get("nodes") or []):
            for slot in ((nd.get("data") or {}).get("extensions") or []):
                xid = (slot.get("extension_id") or slot.get("id")) if isinstance(slot, dict) else slot
                if not xid:
                    continue
                ext_flows[xid].append(fname)
                if fpid and fpid in profile_ids:
                    ext_agents[xid].add(fpid)
    for x in (_safe(lambda: _list_extensions(), []) if profile_ids else []):
        xid = x.get("extension_id") or x.get("id")
        if not xid:
            continue
        xeid = f"extension:{xid}"
        add_node(xeid, x.get("display_name") or x.get("name") or xid, "extension", {
            "kind": "extension", "extension_id": xid, "category": x.get("category"),
            "description": x.get("description"), "used_in_flows": sorted(set(ext_flows.get(xid, []))),
        })
        _cfg_link(xeid, [f"agent:{pid}" for pid in ext_agents.get(xid, ())], "used_in_flow")

    # Context Window types — session working-memory configs (a Memory subtype), linked to
    # the agents bound to each.
    for t in (_safe(lambda: cm.get_context_window_types(user_uuid), []) if profile_ids else []):
        tid = t.get("id")
        if not tid:
            continue
        teid = f"context_window:{tid}"
        pids = cwt_pids.get(tid, [])
        add_node(teid, t.get("name") or "Context Window", "context_window", {
            "kind": "context_window", "cwt_id": tid, "description": t.get("description"),
            "used_by": [tag_of.get(x) for x in pids],
        })
        _cfg_link(teid, [f"agent:{x}" for x in pids], "uses_context_window")

    # Annotate shared resource nodes (degree ≥ 2 across agents).
    for cid, users in collection_users.items():
        if f"collection:{cid}" in index:
            nodes[index[f"collection:{cid}"]]["properties"]["used_by"] = users
            nodes[index[f"collection:{cid}"]]["properties"]["shared"] = len(set(users)) > 1
    for kid, users in kg_users.items():
        if f"kg:{kid}" in index:
            nodes[index[f"kg:{kid}"]]["properties"]["used_by"] = users
            nodes[index[f"kg:{kid}"]]["properties"]["shared"] = len(set(users)) > 1

    findings = _build_findings(
        agents_n=len([p for p in profiles if p.get("id")]),
        unconfigured=unconfigured, inaccessible_refs=inaccessible_refs, tag_of=tag_of,
        collection_users=collection_users, kg_users=kg_users,
        llm_users=llm_users, mcp_users=mcp_users,
        all_collections=all_collections, collection_name=all_collections,
        llm_name=llm_name, mcp_name=mcp_name,
        domain_total=domain_total, kg_count=kg_count, kg_conformant=kg_conformant,
        orphan_kgs=orphan_kgs,
    )

    # Tag EVERY node with its place in the platform taxonomy (the selection facet),
    # and collect the category → subcategories tree present in this segment.
    taxonomy_tree: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for n in nodes:
        tx = _taxonomy_for(n["type"], n.get("properties") or {})
        n["properties"]["taxonomy"] = tx
        cat = tx.get("category") or "Other"
        sub = tx.get("subcategory")
        taxonomy_tree[cat]  # ensure present even if no subcategory
        if sub:
            taxonomy_tree[cat][sub] += 1
    # Stable category order for the facet UI.
    _CAT_ORDER = ["Agents", "Memory", "Sessions", "Intelligence", "Components", "Anatomy", "Ontology", "Other"]
    taxonomy = {cat: dict(taxonomy_tree[cat]) for cat in _CAT_ORDER if cat in taxonomy_tree}

    type_counts: Dict[str, int] = defaultdict(int)
    for n in nodes:
        type_counts[n["type"]] += 1

    rel_counts: Dict[str, int] = defaultdict(int)
    for l in links:
        rel_counts[l["type"]] += 1

    return {
        "nodes": nodes,
        "links": links,
        "title": "Your Ontology",
        "entity_type_colors": PLATFORM_ENTITY_COLORS,
        "entity_type_counts": dict(type_counts),
        "shown_type_counts": dict(type_counts),
        "relationship_type_counts": dict(rel_counts),
        "shown_nodes": len(nodes), "shown_edges": len(links),
        "total_nodes": len(nodes), "total_edges": len(links),
        "truncated": False, "facets": {},
        "is_platform_ontology": True,
        "findings": findings,
        "taxonomy": taxonomy,
    }


def _build_findings(*, agents_n, unconfigured, inaccessible_refs, tag_of, collection_users,
                    kg_users, llm_users, mcp_users, all_collections, collection_name,
                    llm_name, mcp_name, domain_total, kg_count, kg_conformant,
                    orphan_kgs=()):
    """Deterministic systemic observations — the value the other screens can't show.

    Each finding: {id, severity, icon, text, focus:[entity_id...]}. ``focus`` lets a
    click on the finding centre/highlight the relevant nodes (understand → locate).
    ``unconfigured`` is a list of profile ids (empty shells); ``inaccessible_refs`` is
    a list of (agent tag, collection id) for broken bindings.
    """
    findings: List[dict] = []
    configured_n = max(0, agents_n - len(unconfigured))

    # CONFIGURATION — empty bootstrap shells are NOT a trust risk; they're "not set up
    # yet" and are clearly marked as such (info, not warning).
    if unconfigured:
        _tags = [tag_of.get(pid, pid) for pid in unconfigured]
        findings.append({
            "id": "unconfigured", "severity": "info", "icon": "wrench",
            "text": (f"{len(unconfigured)} of {agents_n} agent{'s' if agents_n != 1 else ''} "
                     f"not configured yet (no knowledge, tools or experts): "
                     f"{', '.join('@' + t for t in _tags[:6])}"
                     + (" …" if len(_tags) > 6 else "")),
            "focus": [f"agent:{pid}" for pid in unconfigured],
            "_tags": _tags,
        })
    if configured_n and agents_n:
        findings.append({
            "id": "configured", "severity": "good", "icon": "shield",
            "text": f"{configured_n} of {agents_n} agents are configured and grounded.",
            "focus": [], })

    # MISCONFIGURATION — an agent points at a collection it can't access (broken binding).
    if inaccessible_refs:
        by_tag = sorted({t for t, _ in inaccessible_refs})
        findings.append({
            "id": "inaccessible_collections", "severity": "warn", "icon": "unlink",
            "text": (f"{len(by_tag)} agent{'s' if len(by_tag) != 1 else ''} reference a "
                     f"collection they can't access (broken binding): "
                     f"{', '.join('@' + t for t in by_tag[:6])}"
                     + (" …" if len(by_tag) > 6 else "")),
            "focus": [f"agent:{pid}" for pid in unconfigured
                      if tag_of.get(pid) in {t for t, _ in inaccessible_refs}],
        })

    # MISCONFIGURATION — a knowledge graph whose owning agent no longer exists. Agents
    # assigned to it still ground on it (the runtime reads the assignment table), but
    # nobody can maintain it from a profile editor: reassign ownership or delete it.
    if orphan_kgs:
        n = len(orphan_kgs)
        findings.append({
            "id": "orphaned_kgs", "severity": "warn", "icon": "unlink",
            "text": (f"{n} knowledge graph{'s' if n != 1 else ''} whose owning agent no longer "
                     f"exists — still grounding the agents assigned to it, but no agent can "
                     f"maintain it. Reassign ownership or delete it."),
            "focus": list(orphan_kgs),
        })

    # TRUST — conformance of the knowledge agents stand on.
    if kg_count:
        if kg_conformant < kg_count:
            findings.append({
                "id": "conformance", "severity": "warn", "icon": "badge",
                "text": f"{kg_conformant}/{kg_count} knowledge graphs are conformant.",
                "focus": [], })
        else:
            findings.append({
                "id": "conformance_ok", "severity": "good", "icon": "badge",
                "text": f"All {kg_count} knowledge graphs are conformant.", "focus": []})

    # KNOWLEDGE COVERAGE — what the whole estate knows.
    if domain_total:
        findings.append({
            "id": "coverage", "severity": "info", "icon": "globe",
            "text": f"Your estate models {domain_total} business domain"
                    f"{'s' if domain_total != 1 else ''} across {kg_count} knowledge graph"
                    f"{'s' if kg_count != 1 else ''}.",
            "focus": [], })

    # SHARING / BLAST-RADIUS — what breaks if you remove it.
    for cid, users in collection_users.items():
        uset = sorted(set(users))
        if len(uset) > 1:
            findings.append({
                "id": f"shared_col_{cid}", "severity": "info", "icon": "share",
                "text": f"“{collection_name.get(cid, 'Collection ' + str(cid))}” grounds "
                        f"{len(uset)} agents: {', '.join('@' + t for t in uset[:6])}"
                        + (" …" if len(uset) > 6 else "") + " — removing it affects all of them.",
                "focus": [f"collection:{cid}"], })
    for kid, users in kg_users.items():
        uset = sorted(set(users))
        if len(uset) > 1:
            findings.append({
                "id": f"shared_kg_{kid}", "severity": "info", "icon": "share",
                "text": f"A knowledge graph grounds {len(uset)} agents: "
                        f"{', '.join('@' + t for t in uset[:6])} — shared blast-radius.",
                "focus": [f"kg:{kid}"], })

    # SHARING — config-level reuse (model/connector), as a finding (not a node).
    for lid, users in llm_users.items():
        uset = sorted(set(users))
        if len(uset) > 2:
            findings.append({
                "id": f"shared_llm_{lid}", "severity": "info", "icon": "cpu",
                "text": f"Model “{llm_name.get(lid, 'LLM')}” powers {len(uset)} agents "
                        f"— a single dependency for much of your estate.",
                "focus": [], })

    # GAPS — collections nobody uses.
    used_cids = set(collection_users.keys())
    unused = [(cid, name) for cid, name in all_collections.items() if cid not in used_cids]
    if unused:
        findings.append({
            "id": "unused_collections", "severity": "info", "icon": "unlink",
            "text": f"{len(unused)} collection{'s' if len(unused) != 1 else ''} "
                    f"not used by any agent: {', '.join(n for _, n in unused[:5])}"
                    + (" …" if len(unused) > 5 else ""),
            "focus": [], })

    return findings


# --- per-agent component capabilities ---------------------------------------

def _agent_components(profiles: List[dict], user_uuid: str) -> Dict[str, List[dict]]:
    """profile_id → [{id, name, accent}] of ENABLED action components for that agent.

    Uses the authoritative per-profile enablement (`_component_enabled_for_profile`),
    mirroring the profile editor exactly. Returns {} if the component layer is
    unavailable (the projection degrades — agents just carry no components).
    """
    from trusted_data_agent.components.manager import get_component_manager

    mgr = get_component_manager()
    try:
        from trusted_data_agent.components.settings import is_component_available
    except Exception:
        is_component_available = None
    action = [c for c in mgr.get_all_components()
              if getattr(c, "component_type", None) == "action"]
    out: Dict[str, List[dict]] = {}
    for p in profiles:
        pid = p.get("id")
        if not pid:
            continue
        ptype = p.get("profile_type") or "llm_only"
        cfg = p.get("componentConfig") or {}
        comps: List[dict] = []
        for c in action:
            cid = c.component_id
            # AVAILABLE = admin/user governance permits it for this profile.
            try:
                available = bool(is_component_available(cid, user_uuid)) if is_component_available else True
            except Exception:
                available = True
            # ACTIVATED = the profile turned it on (explicit), else the manifest default
            # for this profile type — the profile's own choice, independent of governance.
            prof_cfg = cfg.get(cid) or {}
            if "enabled" in prof_cfg:
                activated = bool(prof_cfg["enabled"])
            else:
                activated = ptype in (c.profile_defaults.get("enabled_for", []) or [])
            comps.append({
                "id": cid,
                "name": getattr(c, "display_name", cid),
                "accent": (getattr(c, "manifest", None) or {}).get("icon_accent"),
                "available": available,           # governance permits it
                "activated": activated,           # profile turned it on (or default)
                "enabled": bool(available and activated),   # effective (back-compat)
            })
        out[pid] = comps
    return out


def _agent_connectors(profiles: List[dict]) -> Dict[str, List[dict]]:
    """profile_id → [{id, name, enabled}] of PLATFORM connectors active for that agent.

    Platform connectors (browser/files/web/Google/Slack…) are a sub-class of components —
    admin-installed capability tools, opted into per profile. A connector is active for a
    profile when it auto-opts-in (and isn't explicitly opted out) or is explicitly opted
    in. Mirrors the registry's own get_effective_tools enablement rule.
    """
    from trusted_data_agent.core import platform_connector_registry as reg
    out: Dict[str, List[dict]] = {}
    for p in profiles:
        pid = p.get("id")
        if not pid:
            continue
        try:
            servers = reg.get_profile_server_settings(pid)
        except Exception:
            servers = []
        conns: List[dict] = []
        for s in servers:
            opted = s.get("opted_in")
            if s.get("auto_opt_in"):
                active = not (s.get("user_can_opt_out") and opted == 0)
            else:
                active = (opted == 1)
            conns.append({"id": s.get("id") or s.get("server_id"), "name": s.get("name"),
                          "requires_user_auth": s.get("requires_user_auth"), "enabled": bool(active)})
        out[pid] = conns
    return out


# --- isolated source readers (each fails soft) ------------------------------

def _safe(fn, default):
    try:
        return fn() or default
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"platform_ontology: source read failed: {e}")
        return default


def _list_graphs(user_uuid: str) -> List[dict]:
    from components.builtin.knowledge_graph.graph_store import GraphStore
    return GraphStore.list_all_graphs(user_uuid)


def _list_kg_assignments(user_uuid: str) -> List[dict]:
    """Every kg_profile_assignments row of this user — the runtime's grounding truth
    (the KG component reads the same table to decide which graph an agent uses)."""
    from trusted_data_agent.core import sqlite_util
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        db_path = APP_CONFIG.AUTH_DB_PATH.replace("sqlite:///", "")
    except Exception:
        db_path = "tda_auth.db"
    conn = sqlite_util.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT kg_id, kg_owner_profile_id, assigned_profile_id, is_active "
            "FROM kg_profile_assignments WHERE user_uuid = ?", (user_uuid,)).fetchall()
    finally:
        conn.close()
    return [{"kg_id": r[0], "kg_owner_profile_id": r[1], "assigned_profile_id": r[2],
             "is_active": bool(r[3])} for r in rows]


def resolve_grounding_kgs(assignments: List[dict],
                          active_kg_of_owner: Dict[str, Optional[dict]]) -> Dict[str, Optional[dict]]:
    """assigned profile → the graph it grounds on at runtime, for every profile that has an
    ACTIVE assignment row. Pure; mirrors handler._get_active_kg_owner + GraphStore.kg_id:
    the active row names an OWNER and the agent grounds on that owner's ACTIVE graph —
    the row's own kg_id is not consulted by the runtime, so it is not consulted here.
    A profile whose active row points at an owner with no active graph maps to None
    (it grounds on nothing — the caller must NOT fall back to the profile's own graph,
    because the runtime does not). Profiles with no active row are absent from the
    result; the caller applies the runtime's own-graph fallback for those."""
    out: Dict[str, Optional[dict]] = {}
    for row in assignments or []:
        if not row.get("is_active"):
            continue
        assigned = row.get("assigned_profile_id")
        if not assigned:
            continue
        out[assigned] = active_kg_of_owner.get(row.get("kg_owner_profile_id"))
    return out


def _list_memory(user_uuid: str):
    from components.builtin.memory.store import MemoryStore
    return MemoryStore().list_items(user_uuid)


def _list_skills() -> List[dict]:
    from trusted_data_agent.skills.manager import get_skill_manager
    return get_skill_manager().list_skills()


def _list_extensions() -> List[dict]:
    from trusted_data_agent.extensions.manager import get_extension_manager
    return get_extension_manager().list_extensions()


def _list_flows(user_uuid: str) -> List[dict]:
    try:
        from trusted_data_agent.core.flow_db import list_flows
    except ImportError:  # the Flow component is enterprise
        return []
    return list_flows(user_uuid, include_templates=False)


def _list_users() -> List[dict]:
    """All active platform users — {uuid, username, tier}. Admin scope only."""
    from trusted_data_agent.auth.database import get_db_session
    from trusted_data_agent.auth.models import User
    with get_db_session() as s:
        rows = s.query(User).filter_by(is_active=True).all()
        return [{"uuid": u.id, "username": u.username, "tier": u.profile_tier} for u in rows]


def build_multi_user_segment(users: List[dict],
                             sessions_by_user: Optional[Dict[str, list]] = None,
                             packs_by_user: Optional[Dict[str, list]] = None) -> Dict[str, Any]:
    """Aggregate several users' segments under per-user nodes (admin platform scope).

    The taxonomy top layer becomes **Users**: platform root → user → that user's whole
    segment (its 4 departments). Each user's nodes are namespaced (``u:<uuid>:<eid>``)
    so identical ids across users (e.g. every user's ``memory:__global__``) never
    collide; the per-user segment root is replaced by the user node so the class groups
    hang off the user. Returns the same spec shape as :func:`build_segment`.
    """
    sessions_by_user = sessions_by_user or {}
    packs_by_user = packs_by_user or {}
    nodes: List[dict] = []
    links: List[dict] = []
    index: Dict[str, int] = {}

    def add(node: dict) -> int:
        eid = node["entity_id"]
        if eid in index:
            return index[eid]
        idx = len(nodes)
        index[eid] = idx
        nodes.append({**node, "id": idx})
        return idx

    def link(s_eid: str, t_eid: str, rtype: str) -> None:
        s, t = index.get(s_eid), index.get(t_eid)
        if s is None or t is None or s == t:
            return
        links.append({"source": s, "target": t, "type": rtype})

    add({"entity_id": "platform:root", "name": "Platform", "type": "segment", "importance": 2.0,
         "properties": {"kind": "platform", "taxonomy": {"category": "Users", "subcategory": None}}})

    all_findings: List[dict] = []
    for u in users:
        uuid_ = u.get("uuid")
        uname = u.get("username") or uuid_
        if not uuid_:
            continue
        sub = _safe(lambda uid=uuid_: build_segment(uid, sessions_by_user.get(uid), packs_by_user.get(uid)),
                    {"nodes": [], "links": [], "findings": []})
        sub_nodes = sub.get("nodes", [])
        if not sub_nodes:
            continue   # users with no agents add nothing

        ueid = f"user:{uuid_}"
        add({"entity_id": ueid, "name": uname, "type": "user", "importance": 1.7,
             "properties": {"kind": "user", "user_uuid": uuid_, "username": uname, "tier": u.get("tier"),
                            "agents": sum(1 for n in sub_nodes if str(n["type"]).startswith("agent")),
                            "taxonomy": {"category": "Users", "subcategory": None}}})
        link("platform:root", ueid, "owns")

        pos2eid = {i: n["entity_id"] for i, n in enumerate(sub_nodes)}
        old2new: Dict[str, str] = {}
        for n in sub_nodes:
            if n["type"] == "segment":
                old2new[n["entity_id"]] = ueid    # per-user root → the user node
                continue
            new_eid = f"u:{uuid_}:{n['entity_id']}"
            old2new[n["entity_id"]] = new_eid
            props = dict(n.get("properties") or {})
            props["owner_username"] = uname
            props["owner_uuid"] = uuid_
            add({"entity_id": new_eid, "name": n["name"], "type": n["type"],
                 "importance": n.get("importance", 0.3), "properties": props})
        for l in sub.get("links", []):
            s = old2new.get(pos2eid.get(l["source"]))
            t = old2new.get(pos2eid.get(l["target"]))
            if s and t:
                link(s, t, l["type"])
        for f in sub.get("findings", []):
            nf = dict(f)
            nf["id"] = f"{uuid_}:{f.get('id')}"
            nf["focus"] = [old2new.get(x, x) for x in (f.get("focus") or [])]
            nf["text"] = f"@{uname} · {f.get('text', '')}"
            all_findings.append(nf)

    type_counts: Dict[str, int] = defaultdict(int)
    for n in nodes:
        type_counts[n["type"]] += 1
    rel_counts: Dict[str, int] = defaultdict(int)
    for l in links:
        rel_counts[l["type"]] += 1
    tax_tree: Dict[str, Any] = defaultdict(lambda: defaultdict(int))
    for n in nodes:
        tx = (n["properties"] or {}).get("taxonomy") or {}
        cat = tx.get("category") or "Other"
        tax_tree[cat]
        if tx.get("subcategory"):
            tax_tree[cat][tx["subcategory"]] += 1
    _ORDER = ["Users", "Agents", "Memory", "Sessions", "Intelligence", "Components", "Anatomy", "Ontology", "Other"]
    taxonomy = {c: dict(tax_tree[c]) for c in _ORDER if c in tax_tree}

    return {
        "nodes": nodes, "links": links,
        "title": "Platform Ontology",
        "entity_type_colors": PLATFORM_ENTITY_COLORS,
        "entity_type_counts": dict(type_counts), "shown_type_counts": dict(type_counts),
        "relationship_type_counts": dict(rel_counts),
        "shown_nodes": len(nodes), "shown_edges": len(links),
        "total_nodes": len(nodes), "total_edges": len(links),
        "truncated": False, "facets": {},
        "is_platform_ontology": True, "is_multi_user": True,
        "users": [{"uuid": u.get("uuid"), "username": u.get("username"), "tier": u.get("tier")} for u in users],
        "findings": all_findings, "taxonomy": taxonomy,
    }
