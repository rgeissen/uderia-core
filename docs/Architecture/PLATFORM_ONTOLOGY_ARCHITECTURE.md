# Platform Ontology Architecture

> **Not to be confused with [ONTOLOGY_ARCHITECTURE.md](ONTOLOGY_ARCHITECTURE.md).** That document defines the canonical **Knowledge-Graph topology** that an individual KG (built by a constructor or skill) conforms to. **This** document describes the **Platform Ontology** — the platform-wide, agent-centric navigator that maps your *entire estate* (agents, knowledge, memory, sessions, components, configuration) into one living semantic map.

The Platform Ontology is the **"Ontology"** view in the left navigation. It is **Tier 5, Step 1** of the Platform Ontology vision (`docs/roadmap/TIER_5_PLATFORM_ONTOLOGY.md`).

**Last updated:** 2026-10-30

> **One-glance overview:** [**The Uderia Agent — An Anatomy of Trust**](../../static/trust/agent-anatomy-of-trust.html) — a single diagram of the agent model that ties the pieces together: the five faculties (Configuration/DNA · Memory · Session · Intelligence · Components), the Grounding & Guardrails foundations, and the enclosing Trust Membrane. Open the `.html` in a browser (it lives in `static/trust/`, so a running server also serves it at `/static/trust/agent-anatomy-of-trust.html`). (Its identity-vs-authority framing is defined in [AGENT_AUTHORITY_ARCHITECTURE.md §0](AGENT_AUTHORITY_ARCHITECTURE.md).)

---

## 1. What it is

A **per-user, build-on-read, agent-centric semantic map** of everything in a user's estate. It answers questions no single config screen can: *which agents are grounded? what does each remember? which model/connector/store is a single point of failure? what's configured but unused?*

- **For users** it is surfaced as the **Agent Ontology** — *your* agents and what they know.
- **For admins** it becomes the **Platform Ontology** — selectable across any/certain/all users (the top taxonomy layer becomes **Users**).

**Design imperatives:**

| Principle | Meaning |
|---|---|
| **Surfaces value, never re-draws config** | The ontology shows *systemic / trust / semantic* relationships. It never duplicates a configuration screen — every artefact **links out** to the screen that manages it. |
| **Build on read** | There is **no materialized store**. The graph is projected from live sources on every load, so it is **always in sync** — nothing to migrate, nothing to invalidate. |
| **Agent-centric** | Agents are the centerpiece hub; everything else (knowledge, memory, config) is shown in relation to the agents that use it. |
| **Progressive disclosure** | Single-click = details; double-click = expand-in-place. The navigator tree and the graph share one drill-down mechanism. |
| **Honest** | Counts are real and per-agent; an empty estate reads as empty; nothing is fabricated. |

---

## 2. Architecture

```
┌──────────────────────────┐     GET /v1/platform-ontology/segment      ┌───────────────────────────────┐
│  ontologyPanelHandler.js │ ──────────────────────────────────────────▶│  core/platform_ontology/      │
│  (navigator + inspect +  │ ◀────────────────── spec (nodes/links/      │  projection.py                │
│   command center + tabs) │                     taxonomy/findings)      │  build_segment(...)           │
└───────────┬──────────────┘                                             │  build_multi_user_segment(...)│
            │ renderKGFull(spec)  (is_platform_ontology=true)            └───────────────────────────────┘
            ▼                                                                         ▲
┌──────────────────────────────────────┐                          live sources (no materialized store):
│ components/builtin/knowledge_graph/   │      config_manager · GraphStore · MemoryStore · SkillManager ·
│ renderer.js (shared D3 force graph)   │      ExtensionManager · flow_db · sessions · agent packs ·
│  — agent-centric hub layout for onto  │      vector-store & kg-store configs · platform connectors
└──────────────────────────────────────┘
```

- **Projection (backend, off-loop):** `build_segment(user_uuid, sessions, packs)` is **synchronous** and pure; the async REST route fetches the async-only inputs (sessions, packs) and runs the projection via `asyncio.to_thread`. Every read is wrapped in `_safe(...)` so any single source failing degrades that slice rather than breaking the whole map.
- **Rendering:** the ontology re-uses the **shared KG renderer** (`renderKGFull`), guarded throughout by `spec.is_platform_ontology` so the KG inspector is never affected. A dedicated agent-centric clustering layout, dot-graph styling, and labels-on-hover apply only in this mode.

---

## 3. The taxonomy (departments)

Every node is tagged `properties.taxonomy = {category, subcategory}`. The categories are the **departments**, rendered as the top layer of the navigator tree and as the clustering force in the graph.

| Department | Subcategories | Contents |
|---|---|---|
| **Agents** *(hub)* | IFOC class (Ideate / Focus / Optimize / Coordinate) | The agents themselves — the centerpiece the graph clusters around. |
| **Memory** | **Agent Memory**, **Context Window** | *Agent Memory* = durable user facts (global pool + each agent's local pool). *Context Window* = session working-memory types, linked to the agents bound to them. Two genuinely different concepts kept distinct. |
| **Sessions** | per-day (`YYYY-MM-DD`) | Recent conversation runs, linked to the agent they ran under. |
| **Intelligence** | Knowledge Graphs · Knowledge Repositories · Planner Repositories · Domains | The trusted-knowledge layer. Planner (champion-strategy) repos are a distinct type/colour from knowledge (reference-doc) repos. |
| **Components** | **User Interface**, **Connectors** | The runtime capabilities an agent can use: UI components (chart/canvas/dashboard/flow/KG/memory/scheduler…) and platform connectors (browser/files/Google/Slack…). |
| **Anatomy** | MCP Servers · LLMs · Vector Store Backends · Graph Store Backends · Skills · Extensions · Agent Packs | A mirror of the **Anatomy view's tab bar** — the setup artifacts as first-class nodes, each **mapped (linked) to whatever uses it**. |
| **Users** *(admin scope only)* | — | When an admin spans multiple users, Users becomes the top layer and every user's whole world hangs beneath it. |

### Anatomy mapping (the "proper mapping to nodes")

| Anatomy node | Linked to |
|---|---|
| **LLMs** | the agents that use each model |
| **MCP Servers** | the agents with that server's tools enabled |
| **Vector Store Backends** | the collections they back (built-in `ChromaDB (local)` + named configs) |
| **Graph Store Backends** | the KGs they back (built-in `SQLite (local)` + named configs) |
| **Skills** | the agents they're active on |
| **Extensions** | the agents that own the flows using them (extensions bind to flow nodes, not agents) — plus a `used_in_flows` key-fact |
| **Agent Packs** | their coordinator agent |

> Vector/Graph store backend nodes lead their detail panel with the **data-sovereignty fact** — *where this knowledge physically lives*.

### Knowledge-graph edges — ownership is not grounding (Sep 2026)

An agent ↔ knowledge-graph relation is **two facts with two edges**, each read from the one table that decides it:

| Edge | Source of truth | Meaning |
|---|---|---|
| `owns` | `kg_metadata.profile_id` | Provenance — the agent that created/maintains the graph. Drawn whether or not the graph is active. |
| `grounds_on` | `kg_profile_assignments` (the ACTIVE row → that owner's ACTIVE graph; no active row → the agent's own active graph) | The runtime fact — the graph the KG component enriches this agent with and the grounding gate scopes it by. Mirrors `handler._get_active_kg_owner` exactly, including the case where an active row points at an owner with no active graph (the agent grounds on nothing; no fallback). |

A pack expert assigned to its coordinator's graph therefore shows `grounds_on` without `owns`; an owner whose graph is deactivated shows `owns` without `grounds_on`. The graph node's `used_by` (the *Grounds* row of its detail panel, and the shared-blast-radius finding) is the grounding population, never the owner. Before this split the edge was labelled `grounds_on` but derived from ownership, so every assigned agent showed no graph at all. Pure resolver: `projection.resolve_grounding_kgs`; gate: `test/test_platform_ontology.py::t_kg_ownership_vs_grounding`.

---

## 4. The graph (Map view)

- **Agent-centric hub layout:** Agents occupy the centre, fanned into four IFOC sub-wedges; Memory / Sessions / Intelligence / Components / Anatomy cluster in a ring around them. Cross-department edges (e.g. an LLM used by three agents) are drawn without dragging a node out of its neighbourhood.
- **Dot graph + labels-on-hover** to stay legible at scale; faint edges to de-clutter.
- **Reduce-then-render** sampling (inherited from the KG inspector) keeps large estates legible.
- **Double-click expands a node in place** (agent → its config composition + Components group; KG → layers → classes → members; collection → docs; memory → items). The same `_childrenOf` engine drives both the graph injection and the navigator tree, so they always agree.

### Per-agent composition (the `Components` group)

Expanding an agent reveals **Components → { User Interface, Connectors }** plus its Model and data-source Connector. Component dots are coloured by **activation** (an activated component shows its real accent; grey only when off). Per-agent KG/Memory tiles show the **agent's own** count (not estate-wide), and **Agent Memory** always drills to two sub-nodes — **Agents** (this agent's pool) and **Global Memory** — each linked to the real memory node in the graph.

---

## 5. The navigator tree, inspect & link-out

- **Navigator tree** (left pane): departments → subcategories → nodes, with uniform expand-in-place at any depth.
- **Inspect panel** (single-click): rich **key facts for every artefact** + actions. It is strictly **understand + link-out** — it never edits config in place.
- **Deep links into Anatomy:** every Anatomy-department node carries an **"Open in Anatomy"** action that switches to the Anatomy view and activates the owning sub-tab (`_CONFIG_TAB` map → `.config-tab[data-tab=…]`, retried until the tab actually activates). Agents carry **"Configure @X"**, which opens the profile editor via the real edit-button path (robust against the async profile load).

---

## 6. View modes, lenses, insights & scope

- **Three modes:** **Map** (relationship graph), **Matrix** (agent × knowledge grid — who's grounded on what), **Trust** (a scoreboard of what to fix).
- **Lenses** (Map): Overview · Trust · Knowledge · Risk · Coordination — recolour/emphasise the graph for a question.
- **Findings / Insights:** auto-derived systemic findings (single points of failure, unconfigured agents, unused collections, non-conformant KGs…) rendered as the **Estate Insights band card** in the command center — a ranked click-to-locate list (hover reveals "locate →"; click opens the inspect panel).
- **Admin scope selector:** Just-me → any / certain / all users. Spans the projection via `build_multi_user_segment`; spans carry `agent.profile_tag` / `user.uuid` and scope is enforced server-side.

### Panel layout — the Trust scaffold (Jul 2026)
The view uses the platform's shared panel grammar (identical to the Trust/Anatomy/Awareness panels):
- **Command-center band** (full-bleed gradient, content constrained to `max-w-7xl`): title + sync status/Refresh, the **"Ask your world" hero search**, **5 estate KPI tiles** (`perf-kpi-card`: Agents mapped w/ grounded split · Estate nodes/connections · Business domains · Knowledge graphs w/ conformant fraction · Open insights w/ severity split), and **two band cards** — *Estate Composition* (top entity types as chart rows) and *Estate Insights* (see above).
- **Draggable divider** — a `perfPanelResizer` registration (`perfResize:ontology`) spanning the whole panel; supports the shared gestures (shift-click collapse · double-click maximize · persisted last state, default = maximized band).
- **Lower pane** on plain `--bg-primary`: **Map · Matrix · Trust as the canonical `ind-tab ind-tab--underline` strip**, sharing ONE row with the sub-toolbar (admin Scope selector, Map lens chips, and the **fit**/**export PNG** actions — renderer exposes `kgFitView()` / `kgExportPNG()`).
- **The stage stays full-width** (deliberate deviation from the constrained-content rule — a force-directed canvas needs the estate, not a text column); the search + type-filter-pills bar above the graph remains collapsible.

### Session-state preservation
Switching away to a node's split-view (a KG, a memory canvas, a dashboard) and back **preserves the focus + expanded set** — the ontology re-applies the active focus on the `kg:rendered` event once `_graphState` is live.

---

## 7. REST API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/platform-ontology/segment` | The whole spec (`?users=` for admin scope). |
| `GET` | `/v1/platform-ontology/users` | The selectable user list (admin scope picker). |

The renderer's graph actions are client-only (`kgFitView` / `kgExportPNG`); deep-links reuse existing Anatomy / component / KG routes.

---

## 8. Files

| File | Role |
|---|---|
| `src/trusted_data_agent/core/platform_ontology/projection.py` | Build-on-read projection (`build_segment`, `build_multi_user_segment`, taxonomy, node/link assembly, findings). |
| `src/trusted_data_agent/api/platform_ontology_routes.py` | `GET /v1/platform-ontology/segment` + `/users` (the `platform_ontology_bp` blueprint, registered only when the Ontology Suite is on — moved out of `rest_routes.py`). |
| `static/js/handlers/ontologyPanelHandler.js` | Navigator tree, inspect panel, KPI tiles + band cards, mode tab strip, lenses, scope, deep-links. |
| `components/builtin/knowledge_graph/renderer.js` | Shared D3 force graph; ontology mode guarded by `is_platform_ontology` (agent-centric layout, dot graph, `kgFitView`/`kgExportPNG`). |
| `templates/index.html` | Nav entry; the view itself lives in `templates/partials/ontology_view.html` (command-center band, tab strip, stage). |
| `test/test_platform_ontology.py` | Projection contract tests (taxonomy, connectivity, Anatomy mapping, empty-user contract, KG ownership vs grounding). |

---

## 9. Extension points

- **A new artefact type** becomes a node by: adding it to `_TAXONOMY` (or `_taxonomy_for`), giving it a colour in `PLATFORM_ENTITY_COLORS` + the frontend `TYPE_COLORS`, emitting nodes/links in a projection pass, and (optionally) an inspect branch + `_CONFIG_TAB` deep-link.
- **The projection is gated on `profile_ids`** for estate-wide config nodes, so an agent-less user stays empty and config nodes never become orphans.

## 10. Roadmap

This is **Step 1** of the Tier 5 Platform Ontology vision (`docs/roadmap/TIER_5_PLATFORM_ONTOLOGY.md`) — *"provide the enterprise ontology, and make agents trusted."*

**Step 2 — the Trust Membrane (`docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md`) (built)** — surfaces *here*: this navigator's **agent nodes are the home for the trust badge** (⚪ unsigned · 🟢 signed · 🟠 signed-but-changed), computed compositionally over each agent's ingredients. The agent-centric hub layout *is* compositional trust made visible — grade the centre, computed over everything radiating into it. The badge sits on the agent inspect panel and the Trust view (filterable by trust state × IFOC class), with the guided bottom-up signing flow.

> **Terminology (per the Trust Membrane §0):** to disambiguate this **platform-wide estate map** from a *domain* ontology (a KG), the roadmap recommends renaming this nav **"Platform Map"** (admin) / **"Agent Ontology"** stays the user-facing framing. Not yet renamed in code — a recommendation, tracked here.

Deferred follow-ons: flows as first-class ontology nodes (extensions currently point at the flow's owner agent), and the later vision steps (mediated agent identity, semantic interchange surfacing).

---

## 11. Packaging — the Ontology Suite (enterprise feature)

The Platform Ontology and the Trust Membrane ship together as **one packaged capability: the *Ontology Suite***. It is the enterprise differentiator (*"provide the enterprise ontology, and make agents trusted"*), governed by a **single switch** and **physically isolatable** as one unit.

**The Ontology Suite = three parts:**

| Part | What it is | Files |
|---|---|---|
| **Platform Ontology UX** | the "Ontology" nav view (this document) — agent-centric estate map | `core/platform_ontology/` · `static/js/handlers/ontologyPanelHandler.js` · `templates/partials/ontology_{nav,view}.html` |
| **Trust Membrane** | the compositional agent trust badge + sign-off — **now fully implemented**: all 10 signable categories validate, per-artefact badges + sign-off UX, and marketplace portability. See [TRUST_MEMBRANE_ARCHITECTURE.md](TRUST_MEMBRANE_ARCHITECTURE.md). | `core/trust/` (attestation·scanners·validators·content·composition·pack·store·portability) · `static/js/handlers/trustBadge.js` |
| **REST for both** | the ontology + trust endpoints | `api/platform_ontology_routes.py` (`/v1/platform-ontology/*`) · `api/trust_routes.py` (`/v1/trust/*` — badge, per-kind sign-off, offline verify, portability) |

**One switch** — `core/enterprise.py:is_enterprise_ontology_enabled()` (**True today** for all tiers; a one-line flip to `tier == "Enterprise"` makes the whole suite Enterprise-only). When off: the "Ontology" sidebar item isn't rendered, both REST families 404, and neither blueprint registers.

**Physically isolatable** — the suite is a contiguous, removable set. `main.py:_register_optional_enterprise_suite` uses optional imports, the template uses `{% raw %}{% include … ignore missing %}{% endraw %}`, and every JS/render seam is absence-tolerant, so removing the suite from a build leaves a working platform with **zero ontology/trust trace**. Removal manifest + the absence-tolerance contract: [`core/trust/README.md`](../../src/trusted_data_agent/core/trust/README.md).

> **Not in the suite** (always present): `core/enterprise.py` (the lightweight gate) and `components/builtin/knowledge_graph/renderer.js` (the graph renderer is **shared** with the KG inspector; its ontology-mode code is guarded by `spec.is_platform_ontology` and inert without the suite).
