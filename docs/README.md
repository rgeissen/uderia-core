# Uderia Documentation

Uderia is a framework for trusted data agents; the Uderia platform is its runtime implementation, which makes it work out of the box ([what that means](value/VALUE_PROPOSITION.md#what-the-framework-is)).


The documentation is organised in three layers, so the value story, the description of what Uderia does and the description of how it is built can each change without disturbing the others:

| Layer | Question it answers | Where |
|---|---|---|
| **Value** | Why does this matter to my organisation? | [docs/value/](value/README.md): the [Value Proposition](value/VALUE_PROPOSITION.md) and two framings |
| **Capabilities** | What does the framework do, in neutral terms? | [docs/capabilities/](capabilities/README.md) |
| **Technology** | How is it built, operated and integrated? | [Architecture](#-architecture-documentation) · [Guides & Manuals](#-guides--manuals) · [Install](guides/INSTALL.md) · [User Guide](guides/USER_GUIDE.md) · [Docker](guides/DOCKER.md) · [REST API](RestAPI/restAPI.md) |

Release notes for users are in [WHATS_NEW.md](WHATS_NEW.md); the engineering change history is in [CHANGELOG.md](CHANGELOG.md).

---

## Where the old README sections went

Until November 2026 the root README held everything. Links to its sections now resolve to:

| Former README section | Now |
|---|---|
| Architecture Principles, the outcomes list | [value/VALUE_PROPOSITION.md](value/VALUE_PROPOSITION.md) |
| Key Features | [capabilities/KEY_FEATURES.md](capabilities/KEY_FEATURES.md) |
| Core Concepts (IFOC, Memory, Fusion Optimizer, RAG, stores, Marketplace, Skills, Extensions, Trust Membrane, Evidence, OBDA) | one file each under [capabilities/](capabilities/README.md) |
| Components | [capabilities/COMPONENTS.md](capabilities/COMPONENTS.md) |
| Security Architecture (both pillars, threat model, compliance) | [capabilities/SECURITY_AND_TRUST.md](capabilities/SECURITY_AND_TRUST.md) |
| System Architecture & Deployment | [Architecture/SYSTEM_OVERVIEW.md](Architecture/SYSTEM_OVERVIEW.md) |
| Architecture Documentation, Guides & Manuals | this page, below |
| Installation and Setup Guide, Running the Application, Command Line Options | [guides/INSTALL.md](guides/INSTALL.md) |
| Consumption Profiles and Usage Quotas | [guides/CONSUMPTION_PROFILES.md](guides/CONSUMPTION_PROFILES.md) |
| User Guide (interface, context management, REST API, operationalization, troubleshooting) | [guides/USER_GUIDE.md](guides/USER_GUIDE.md) |
| Docker Deployment | [guides/DOCKER.md](guides/DOCKER.md) |
| Appendix: Feature Update List | [WHATS_NEW.md](WHATS_NEW.md) |
| License, Author & Contributions | stay in the [root README](../README.md) |

Section anchors are unchanged within each file, so an old `README.md#anchor` link becomes `<new file>#anchor`.

---

## 📚 Architecture Documentation

Every subsystem ships a detailed architecture document — the live contract for that part of Uderia. The complete index of `docs/Architecture/`, grouped by area; point-in-time plans, test plans and the trust-grammar casebook are listed last under *Plans & historical records* (roadmap documents live in `docs/roadmap/` and are not listed here):

**Agentic & Execution Engine**
- [Fusion Optimizer](Architecture/FUSION_OPTIMIZER_ARCHITECTURE.md) — The Optimize pipeline: strategic planning, the 19-pass refinement pipeline, FASTPATH, 11 correction strategies, honest phase failure
- [Engine Modularization](Architecture/ENGINE_MODULARIZATION_ARCHITECTURE.md) — The engines package: one engine per IFOC class, dispatch, and the seams every engine shares (history, failure, stream, budget, surface)
- [Coordinator (Genie)](Architecture/COORDINATOR_ARCHITECTURE.md) — Routing, trust-aware routing, child sessions, synthesis pass-through, full-result pass-through, nesting, skills
- [Tool Surface](Architecture/TOOL_SURFACE_ARCHITECTURE.md) — Governed Tool Surface: per-agent all/relevant tool selection, word and meaning rankers, `TDA_FindTools`, authority-first, per-turn overlay
- [Multi-Source Agents](Architecture/MULTI_SOURCE_ARCHITECTURE.md) — Several sources per agent: source ids, the combine step, isolation, routing per server, the primary-server pin
- [Preflight SQL Guards](Architecture/PREFLIGHT_SQL_ARCHITECTURE.md) — Deterministic SQL checks at both chokepoints, mechanical rewrites, modes, and planner passes reading the same facts
- [LLM Providers](Architecture/LLM_PROVIDER_ARCHITECTURE.md) — The `llm/` layer: eleven providers, handler and LangChain lanes, per-turn model choice, resilience, signed fallback, usage accounting
- [Prompt Cache](Architecture/PROMPT_CACHE_ARCHITECTURE.md) — One cache plan for every provider: declared stable prefix, breakpoints, affinity keys, readings, pricing
- [Sessions](Architecture/SESSION_ARCHITECTURE.md) — Sessions end to end: record and three histories, file/Postgres storage, locks, archiving, REST tasks, SSE channels, browser isolation
- [Next-Step Recommender](Architecture/NEXT_STEP_RECOMMENDER_ARCHITECTURE.md) — Smart Next Steps: tiers A/B/C, enablement governance, profile rebind, the ephemeral dock
- [Feedback Signal](Architecture/FEEDBACK_SIGNAL_ARCHITECTURE.md) — The universal vote: one dispatcher, six consumers, the preference axis, governance flags
- [Needs Input](Architecture/NEEDS_INPUT_ARCHITECTURE.md) — A turn that stops to ask: the `input_required` state, the question record and its single-use resume binding, the raisers, relays through coordinators, schedules and flows

**Context & Knowledge**
- [Context Window](Architecture/CONTEXT_WINDOW_ARCHITECTURE.md) — Five-pass budget orchestrator, the 13 context modules, context-window types, ContextBuilder, snapshots and per-engine context handling
- [Knowledge Retrieval](Architecture/KNOWLEDGE_RETRIEVAL_ARCHITECTURE.md) — Focus/Ideate knowledge pipeline: ingestion, CDC sync and re-signing, scoring, attachments as evidence, synthesis, access control
- [Agent Memory](Architecture/AGENT_MEMORY_ARCHITECTURE.md) — `TDA_Memory`: deterministic cross-session recall, three write doors, memory firewall, the agent_memory store, governance and REST
- [Ontology](Architecture/ONTOLOGY_ARCHITECTURE.md) — Normative KG topology: layers (incl. authored), closed type/relation vocabularies, naming, conformance, reference model
- [Platform Ontology](Architecture/PLATFORM_ONTOLOGY_ARCHITECTURE.md) — The Ontology nav: agent-centric map of each user's estate, built on read, plus the Ontology Suite packaging
- [Signed Data Products (OBDA-READ)](Architecture/OBDA_ARCHITECTURE.md) — The V1–V7 gate, fan-out-safe compiler, three faces, data surface, receipts, component availability and policy
- [OBDA-MODEL & OBDA-ACT](Architecture/ACT_ARCHITECTURE.md) — OBDA-MODEL authoring and deployment, and OBDA-ACT signed actions: gate, compiler, runner, postures, approvals, receipts, every face
- [Open Standards](Architecture/OPEN_STANDARDS_ARCHITECTURE.md) — Every open standard implemented: SKOS/OWL/QB/PROV/DCAT both ways, reading declared standards, MCP, OTLP-shaped spans, OIDC/SAML, Ed25519
- [Knowledge Graph](Architecture/KNOWLEDGE_GRAPH_ARCHITECTURE.md) — The `TDA_KnowledgeGraph` component: data model, context enrichment, LLM entity management, multi-profile scoping, REST surface
- [KG Inspector](Architecture/KG_INSPECTOR_ARCHITECTURE.md) — How the KG viewer shows large graphs: reduce-then-render lenses, focus/expand, execution theatre, drafts, live redraw
- [KG Store Abstraction](Architecture/KG_STORE_ABSTRACTION_ARCHITECTURE.md) — Graph-store backends (SQLite+NetworkX, Neo4j): interface, capabilities, resolution chain, governance, pure KG helpers
- [Vector Store Abstraction](Architecture/VECTOR_STORE_ABSTRACTION_ARCHITECTURE.md) — Vector backend interface (ChromaDB/Teradata/Qdrant): capabilities, factory, hybrid search, governance, Teradata ingestion and resilience

**Components & Extensibility**
- [Component Framework](Architecture/COMPONENT_ARCHITECTURE.md) — The component system: 19 builtins, manifest contract, one enablement rule, signals bus/ledger, panels, Workspace, governance, portability
- [Canvas](Architecture/CANVAS_ARCHITECTURE.md) — `TDA_Canvas`: persisted, versioned code/document workspace — capabilities, connectors, DB connections, navigator/explain bridges, following edits elsewhere
- [Chart](Architecture/CHART_ARCHITECTURE.md) — `TDA_Charting`: the five-stage mapping pipeline, 17 G2Plot types, the lanes, pinning to a dashboard and keeping to Files
- [Dashboard](Architecture/DASHBOARD_ARCHITECTURE.md) — `TDA_Dashboard`: pin acts, visualize, tiles, three-tier refresh, ledger input, following edits elsewhere, PDF snapshot to Files
- [Database Navigator](Architecture/DATABASE_NAVIGATOR_ARCHITECTURE.md) — `TDA_DatabaseNavigator`: one catalog contract over direct connections or MCP, deterministic reads, ontology overlay, opt-in queries
- [EXPLAIN Plan](Architecture/EXPLAIN_PLAN_ARCHITECTURE.md) — `TDA_ExplainPlan`: Teradata plan parser and findings, the result-set registry, the navigator/canvas relation, the panel
- [Files](Architecture/FILES_ARCHITECTURE.md) — Files and file stores: one write/read/exit path, encryption, external key managers, attachments, exports, governance, gates
- [Flow Component](Architecture/FLOW_COMPONENT_ARCHITECTURE.md) — `TDA_Flow`: graph model, topology templates, file/judge/action nodes, executor, loops, live canvas sync and REST
- [Scheduler](Architecture/SCHEDULER_ARCHITECTURE.md) — `TDA_Scheduler` and APScheduler: user tasks, scheduled actions, delivery channels incl. Files, platform jobs, engine gates
- [Skills](Architecture/SKILL_ARCHITECTURE.md) — Skills: storage tiers, discovery, param/gate blocks, progressive libraries, per-engine injection, companion auto-assign, governance, packs, versions
- [Extensions](Architecture/EXTENSION_ARCHITECTURE.md) — `!name` post-processing extensions: four authoring tiers, manager, runner, activations, cost, governance, signing, marketplace, flow use
- [Connectors](Architecture/CONNECTOR_ARCHITECTURE.md) — Platform connectors (stdio, remote HTTP MCP, database): governance, namespaced tools, OAuth/token/credential auth, REST and UI
- [Marketplace](Architecture/MARKETPLACE_ARCHITECTURE.md) — The seven marketplace product types plus assurance modules: publish, subscribe, fork, sync, sharing, ratings, governance and trust portability
- [Agent Pack Governance](Architecture/PACK_GOVERNANCE_ARCHITECTURE.md) — The agent-pack release lifecycle, SLA tier, trust-gated publish, append-only audit and the deprecation rules
- [Banner Messaging](Architecture/BANNER_MESSAGING_SYSTEM.md) — Which user-facing message primitive to use: app, conversation and maintenance banners, confirm, prompt, panel notice

**Security & Trust — Pillar 1: Deployment Security**
- [Security Architecture](Architecture/SECURITY_ARCHITECTURE.md) — Deployment security: auth/SSO, tiers, secrets and file encryption, the licence, provenance chain, audit, hardened mode
- [Prompt Architecture](Architecture/PROMPT_ARCHITECTURE.md) — The system prompts: plain-text defaults shipped with the code, the release sync that never overwrites a person's edit, overrides and versions, the licence-activated editor, the one-time conversion of formerly encrypted rows
- [MCP Server](Architecture/MCP_SERVER_ARCHITECTURE.md) — Uderia as an MCP server: a 12-tool tier-clamped catalog for asking agents, signed data products and signed actions

**Security & Trust — Pillar 2: Agent Trust & Assurance**
- [Answer Gates](Architecture/ANSWER_GATES_ARCHITECTURE.md) — The runtime answer gates — grounding, honesty, guardrails, leaked-SQL recovery, turn failure — their per-engine order, modes, stamps and review flow
- [Agent Authority](Architecture/AGENT_AUTHORITY_ARCHITECTURE.md) — Agent Principal: subset-only scope, data/write surfaces, OBDA policy, fail-closed chokepoints, interactive opt-in, budgets
- [Trust Membrane](Architecture/TRUST_MEMBRANE_ARCHITECTURE.md) — Signed agents and artefacts: validators, portable vs deployment halves, owner-scoped history, renewal, learning outside the seal
- [Evidence](Architecture/EVIDENCE_ARCHITECTURE.md) — Per-answer evidence: bill of materials, usage index, portable bundles and verifiers, hash-chained ledger, key custody, anchoring
- [Trust Status Communication](Architecture/TRUST_STATUS_COMMUNICATION.md) — The ten-rule grammar every assurance surface uses to report configuration and execution status
- [Agent Ops & Evaluation](Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md) — Eval engine: 63 scorers, judges, online/offline scoring, regression gate, trust loop, eval REST/CLI/UX
- [Agent Quality Dimensions](Architecture/AGENT_QUALITY_DIMENSIONS.md) — Reference for all 63 scored dimensions: logical names, moments, labels, statuses, sample sizes
- [Assurance Instruments](Architecture/ASSURANCE_INSTRUMENTS_ARCHITECTURE.md) — The 26-point intervention registry (25 on a turn), per-turn tally, gate-outcome lift, dial, and Assurance Guide anatomy
- [Assurance Substrate](Architecture/ASSURANCE_SUBSTRATE_ARCHITECTURE.md) — One assessment for two lanes: properties, assessors, data bindings, conformance, in-flight consumers
- [Assurance Module Plane](Architecture/ASSURANCE_MODULE_PLANE_ARCHITECTURE.md) — Third-party assurance packs: tiers, signed-to-bind, binding studio, in-flight seam, controls, generic scorers
- [Trust Symmetry Map](Architecture/TRUST_SYMMETRY_MAP.md) — Scored analysis pairing mechanisms with measurements, with the live state as of Oct 2026

**Runtime & Operations**
- [Platform Operations](Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md) — The REST API described for agents: `@operation` descriptors, one policy, `TDA_Platform` find · call · ask, roles, writes under review
- [Cost Tracking](Architecture/COST_TRACKING_ARCHITECTURE.md) — Token metering to cost: per-call cache-aware pricing, price sources and coverage, session totals, consumption ledger, invariants
- [Branding](Architecture/BRANDING_ARCHITECTURE.md) — White-label brands chosen by Host header or env pin: model, template delivery, email, runbook, no-regression gate
- [SQLite Concurrency](Architecture/SQLITE_CONCURRENCY.md) — Process-wide SQLite serialization contract, schema ledger, connection reuse, unreadable-DB alerts and daily verified backups
- [Scale Assessment](Architecture/SCALE_ASSESSMENT.md) — March 2026 scale bottleneck assessment with current status per finding, WS1 session-storage hardening, and roadmap outcomes

**Plans & historical records** *(point-in-time; the documents above are the live contract)*
- [KG Subscription Plan](Architecture/KG_SUBSCRIPTION_PLAN.md) — Knowledge-graph marketplace *Subscribe*: the snapshot-with-sync materialization model (implemented May 2026)
- `docs/Architecture/INDUSTRY_MODEL_KG_CONSTRUCTOR_PLAN.md` — the Industry Data Model constructor: industry data model → knowledge graph walker, LDM/PDM layering, update/version lifecycle (implemented)
- `docs/Architecture/INDUSTRY_MODEL_KG_TESTPLAN.md` — Manual smoke-test for the constructor modal and the update/version UI (the browser-JS surfaces CI cannot reach)
- `docs/Architecture/INDUSTRY_MODEL_PDM_DEPLOYMENT_PLAN.md` — KG → real Teradata PDM: converter, deployment backend, demo-data load (implemented, live-verified)
  These three documents and the constructor they describe are in the development repository only, pending a licence review (Nov 2026).
- [Trust Status Communication — Casebook](Architecture/TRUST_STATUS_COMMUNICATION_CASEBOOK.md) — Every trust-grammar rule verbatim with the incident that produced it

> **Agent Trust & Assurance detail:** the grounding gate, honesty gate and AI guardrails are specified in [ANSWER_GATES_ARCHITECTURE.md](Architecture/ANSWER_GATES_ARCHITECTURE.md), and the signed-attestation eval in [AGENT_OPS_EVAL_ARCHITECTURE.md](Architecture/AGENT_OPS_EVAL_ARCHITECTURE.md); the per-tier history records are `TIER_1.1_GROUNDING_GATE.md` and `TIER_1.3_GUARDRAILS.md`. Both pillars are summarized in the [Security Architecture](capabilities/SECURITY_AND_TRUST.md#-security-architecture) section.

## 📘 Guides & Manuals

**Trust & assurance**
- [Agent Trust & Assurance — Administrator Operations Guide](manuals/AGENT_TRUST_AND_ASSURANCE.md) — Operator runbook for the trust stack: what each control does, where it lives in the UI, the maturity ladder, signing, attesting and evidence
- [Trust UX FAQ](manuals/TRUST_UX_FAQ.md) — Reading guide for the Trust Circle, assurance dial, Scorecard, Guide, answer capsule and status strip, with troubleshooting
- [Building Assurance Modules](manuals/BUILDING_ASSURANCE_MODULES.md) — Developer contract for building, signing and shipping third-party assurance modules and controls

**Agents that use Uderia**
- [Platform Roles](manuals/PLATFORM_ROLES.md) — What an agent with the Platform component can do with Uderia itself, role by role, and what stays a person's act
- [Platform Operations catalog](RestAPI/PLATFORM_OPERATIONS.md) — Generated list of every REST operation with its effect, scope and tier

**REST & MCP**
- [REST API Reference](RestAPI/restAPI.md) — Complete REST reference: auth, sessions, query/tasks, profiles, files, memory, OBDA, trust, marketplace, admin, UI-internal routes
- [Profile Override](RestAPI/PROFILE_OVERRIDE_FEATURE.md) — Per-query profile override via `profile_id`, the activation firewall, and why a REST prompt must not carry an `@TAG`
- [REST → MCP Tier Matrix](RestAPI/MCP_TIER_MATRIX.md) — Which REST endpoints the Uderia MCP server exposes as tools, at which consumer tier

**Knowledge & sharing**
- [RAG System](RAG/RAG.md) — Planner champion lifecycle: capture, seeding gates, champion selection, per-agent learning repositories, binding scope, maintenance scripts
- [Planner Repository Constructors](RAG_Templates/README.md) — User guide for the template plugins that generate planner repositories ([quick reference](RAG_Templates/QUICK_REFERENCE.md))
- [Intelligence Marketplace](Marketplace/README.md) — Publisher guidance for republishing a pack without breaking subscribers (`update_in_place`)

**Identity & local models**
- [OAuth Documentation](OAuth/README.md) — Index of the OAuth guides: getting started, provider setup, email verification, security
- [MLX Integration](MLX/README.md) — Local, on-device inference on Apple Silicon through the MLX LM server

**Workflow integrations**
- [n8n](n8n/README.md) — Developing, testing and deploying n8n workflows against the Uderia REST API, with templates ([deployment guide](n8n/DEPLOYMENT_GUIDE.md))
- [Apache Airflow](Airflow/Airflow.md) — The `tda_00_execute_questions` DAG: session/submit/poll per question with Bearer tokens and profile selection
- [Flowise](Flowise/Flowise.md) — The Flowise agent flow: sessions, asynchronous queries and polling against the REST API

**Administration & reference**
- [Feature Tagging](FeatureProfile/FEATURE_TAGGING_SYSTEM.md) — The 66 tier-tagged features, the `require_feature` decorator and the features endpoint
- [User Tiers](FeatureProfile/PROFILE_TIER_SYSTEM.md) — The `user` · `developer` · `admin` tiers, what each unlocks, and how an administrator changes a tier
- [Pane Visibility](FeatureProfile/PANE_VISIBILITY_QUICK_START.md) — Which tiers see each sidebar view (Administration → Panes)
- [Tool & Prompt Classification](FeatureProfile/PROFILE_CLASSIFICATION_USER_GUIDE.md) — Light and Full classification of an agent's MCP tools, prompts and resources
- [Per-User Runtime Context](FeatureProfile/PER_USER_RUNTIME_CONTEXT.md) — Why provider and model are held per user, not process-wide
- [Consumption Profiles (implementation)](Consumption%20Profiles/CONSUMPTION_PROFILES.md) — Quota profiles, rate-limit precedence, schema and endpoints; the operator view is [guides/CONSUMPTION_PROFILES.md](guides/CONSUMPTION_PROFILES.md)
- [Cost Management (implementation)](Finops/COST_MANAGEMENT_IMPLEMENTATION.md) — The cost tables, sync and analytics endpoints; the live contract is [Cost Tracking](Architecture/COST_TRACKING_ARCHITECTURE.md)
- [Prompt Schema](Schema/SCHEMA_DOCUMENTATION.md) — The prompt-management tables in `tda_auth.db` ([quick reference](Schema/QUICK_REFERENCE.md))

**Releases**
- [Release Process](RELEASE.md) — Version single source of truth, the atomic release bump, build stamping, and the schema-ledger backup on upgrade
- [Changelog](CHANGELOG.md) — Full change history, newest first

## Plans and records

These folders hold planning material and point-in-time records. They are kept for history and are not a description of Uderia as it ships; the documentation agent does not answer from them.

| Folder | What it holds |
|---|---|
| `roadmap/` | Tier plans and the mission roadmap, the north star |
| `proposals/` | Design proposals, including options that were rejected |
| [proof-packs/](proof-packs/README.md) | Externally reproducible evidence for shipped capabilities |
| `assessments/`, `Assessment/` | Deep code assessments and their implementation-status records |
| `Strategy/` | Internal strategy and commercial planning |
| `Testing/`, `Canvas/CANVAS_TEST_PLAN.md`, `Schema/PHASE*` | Test plans and phase completion logs |
| `design/`, `optimization/`, `component-audit/`, `Knowledge_Repositories/` | Design drafts, optimisation notes, audit notes, phase logs |
