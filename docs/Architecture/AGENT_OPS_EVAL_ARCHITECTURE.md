# Agent Ops & Evaluation Architecture

*Agent observability, evaluation **and enforcement** for Uderia (WS3 + the Tier 1 grounding work). Built on Uderia's existing signed-trace substrate rather than re-capturing traces, and built for **all five profile classes** — not just the Planner/Executor (Optimize) class.*

> **Scope — Pillar 2 of 2: Agent Trust & Assurance.** This document is the **AI-governance** pillar of Uderia's security & trust model (proving the *AI's output* is truthful, safe, in-scope, and verifiably good — EU AI Act / NIST AI RMF). It pairs with **Pillar 1, Deployment Security** ([SECURITY_ARCHITECTURE.md](SECURITY_ARCHITECTURE.md): identity, secrets, data, IP, and action integrity — ISO 27001 / SOC 2). The pillars meet at one cryptographic substrate: the **same Ed25519 key that signs each execution trajectory (the EPC) also signs the evaluation verdict that grades it** (§6.4). Both pillars are summarized in the README [Security & Trust](../capabilities/SECURITY_AND_TRUST.md) capability page.

> **Companion plans:** `docs/Strategy/WS3_AGENT_EVAL_OBSERVABILITY_PLAN.md` (rationale and phase sequencing) · `TIER_1.1_GROUNDING_GATE.md` + `TIER_1.4_BENCHMARK.md` (the grounding gate + anti-hallucination benchmark, §6a/§6b).

> **Proof-pack:** [A6 — Eval & Observability](../proof-packs/A6_EVAL_OBSERVABILITY.md) — the publishable evidence artifact for assessment requirement A6, including a **reproducible Ed25519-signed-attestation sample + standalone offline verifier** that proves eval verdicts are cryptographically bound to the trajectory they grade.

---

## 0. The spine: observe → enforce → prove

The eval layer began as an **observer** — it scored what already happened and surfaced quality, but nothing it computed could change a turn. The Tier 1 grounding work extends the same substrate along one axis: from *observing* drift to **enforcing** against it on the request path, and then to **proving** the result with a published benchmark. One set of primitives, three jobs:

| Stage | What it does | When | Where |
|---|---|---|---|
| **Observe** | Score trajectory/cost/safety/quality; roster, regression gate, calibration | post-hoc, off-path | §4–§12 (the eval engine) |
| **Enforce** | A synchronous **grounding gate** that can pass · annotate · withhold an answer for leaving the KG-declared scope — *before* the user sees it | on the request path, opt-in | **§6a** |
| **Prove** | A published **anti-hallucination benchmark** that measures the enforcement delta on a real KG | offline, admin-run | **§6b** |

The enforcement layer is not a parallel system: the live gate's "out of scope" is the **exact same code** as the post-hoc `scope_adherence` scorer (shared primitives, §6a), and enforcement's hard decisions are made by the **same `scope_faithfulness` judge** the roster scores with. Observe and enforce are two consumers of one grounding core, so they can never disagree.

---

## 1. Why this exists, and the design choices behind it

Agent observability/eval is simultaneously 2026 table stakes and the direct antidote to the #1 reason agent projects get cancelled — "inadequate risk controls / unclear value." A regulated buyer will not deploy an agent they cannot prove didn't regress or hallucinate.

An evaluation layer earns that trust only if its verdicts are reproducible, bound to what they grade, and enforced by the same definitions that measure. Four design choices follow:

| Design choice | What Uderia does |
|---|---|
| **Deterministic-first scoring** | A **profile-class-aware deterministic scorer suite** grades trajectory/cost/safety in code (CI-pure, 0 tokens). Judges are reserved for the open-ended residue. |
| **Signed, tamper-evident trajectories** | Every turn already produces an **Ed25519-signed Execution Provenance Chain (EPC)**. Eval verdicts are cryptographically bound to the exact trajectory they grade. |
| **Agent-as-entity, not run firehose** | An **agent health roster** (profiles are first-class, IFOC-classed) leads the UX. |
| **Observe *and* enforce on one substrate** | The synchronous **grounding gate** (§6a) shares the eval scorers' primitives and the eval judges' panel, so what the dashboard flags off-path is exactly what the gate withholds on-path. |

The four rest on one substrate: deterministic, plan-structure-aware trajectory scoring on a signed chain, and a gate that enforces against the *same* scoring.

---

## 2. Information architecture — two lenses + config

Agent ops is **operational**, not a config screen, so it does not live in Administration. Uderia separates three jobs by **unit of analysis**:

```
Sidebar topic        Unit of analysis     Question answered
──────────────────────────────────────────────────────────────────────────
Awareness            session / turn       "What happened in this run?"      (instance lens)
  └─ the "Awareness" panel (executionDashboard.js; formerly "Session Performance")
Trust                profile / flow       "Is this agent good / regressing?" (entity lens)
  └─ the "Trust" panel (agentPerformanceHandler.js; formerly "Agent Performance")
Administration       deployment           configuration & governance only
            ↕
   bridged by the trajectory verdict overlay (a session trace is the raw material;
   agent performance aggregates many traces and grades them — one substrate, two front doors)
```

- **Awareness** (rail topic; formerly "Executions" → "Sessions"): instance lens — run history, live + historical trajectory, tokens/cost/latency. Existing `executionDashboard.js`; panel title "Awareness", tagline "Your agents' session operations".
- **Trust** (rail topic; formerly "Agents"): entity lens — quality, regression, calibration per agent. `agentPerformanceHandler.js`; panel title "Trust", tagline "Your agents' trust, quality & reliability". An **agent is a profile OR a flow** — the lens has two agent types (profile-agents and flow-agents; see §12a).
- **Admin**: configuration/governance only — the interim Evaluation tab was removed.

The two performance topics are **siblings** (parallel nouns: Sessions / Agents), making the unit-of-analysis split legible in the rail. Both share one data layer.

**Consumer-facing dimension presentation** — how the scored dimensions below are *shown* to the user (logical labels instead of internal scorer keys, the theme-compliant hover tooltip, and the context-scoped **Assurance Guide** with per-turn status, the Admin-thresholds toggle, and the Not applicable / Not set / Abstained distinction) is documented separately in **[AGENT_QUALITY_DIMENSIONS.md](AGENT_QUALITY_DIMENSIONS.md)**. Single source of truth for labels/descriptions + the guide/tooltip: `static/js/handlers/qualityDimensions.js` (`window.QualityDimensions`), consumed identically by the Agent Roster (`agentPerformanceHandler.js`) and the Live Status Quality card (`ui.js`). When a scorer is added or its `applies_to`/threshold changes, update that module too.

---

## 3. The substrate it builds on (do NOT rebuild)

| Substrate | Source | What it gives eval |
|---|---|---|
| **Execution Provenance Chain (EPC)** | `core/provenance.py` | Ed25519-signed, SHA-256 hash-chained step trajectory per turn; `chain_tip_hash` to bind attestations to. |
| **Per-turn `workflow_history[-1]`** | session files | `execution_trace` (tool calls/args/results), `raw_llm_plan`/`original_plan` (plan structure), tokens/cost, self-corrections, knowledge/genie metadata. |
| **Single execution seam** | `agent/execution_service.py::run_agent_execution` | One defensive hook point covering all 5 engines + genie coordinator (the Next-Step Recommender already hooks here). |
| **11 LLM providers** | `llm/handler.py` | A **diverse-family judge panel is free** — judges can come from different model families with no extra integration. |
| **KG-declared deployed scope** | `components/builtin/knowledge_graph/` | The agent's bound KG knows the *trusted, governed* databases it should answer from. `handler._derive_scope_databases` / `_derive_full_kg_scope_dbs` resolve it to a set of deployed database names; `get_trusted_scope(profile_id, user_uuid)` caches it per KG. This is the ground truth both `scope_adherence` (observe) and the grounding gate (enforce) measure against — see §6a. |

The eval layer reads this substrate via a normalized view (`RunRecord`); it never re-instruments execution. The grounding gate reads the **same** `RunRecord` (via a thin adapter) so its on-path view is byte-identical to the off-path scorer's.

---

## 4. Core data model

[`eval/types.py`](../../src/trusted_data_agent/eval/types.py)

| Type | Role |
|---|---|
| `ScoreKind` | `DETERMINISTIC` (code grader, always runs, CI-pure) · `JUDGE` (LLM, open-ended dims only) · `METRIC` (measured number — cost/steps). |
| `Verdict` | One scorer's result for one dimension: `passed` (`True`/`False`/`None`=unknown/N-A), `score` (0..1), `explanation`, `evidence[]`, `threshold`. **`None` is first-class** — a scorer abstains rather than fabricating. |
| `EvalCase` | A golden case as **data**: `query`, `profile_class`, `cohort`, optional `reference_answer`, optional `flow_id` (flow-agent cases — §12a), and declarative `assertions{}`. |
| `RunRecord` | Profile-class-agnostic accessor view over a `workflow_history` turn dict: `final_answer_text`, `tool_calls`/`tool_names` (excludes framework tools), `plan`/`planned_tools`, `self_correction_count`, `retrieved_documents`, `slaves_invoked`, `turn_cost_usd`, `provenance_steps`/`provenance_meta`, `step_types`. **Accessors are defensive against engine shape variance** — e.g. the Optimize engine stores the plan as a *list* of phases (not `{phases:[…]}`), and `knowledge_retrieval_event`/`genie_metadata`/`provenance_meta` may be list-shaped; every accessor that assumes a dict guards with `isinstance` so an unexpected shape can never crash the online hook (regression: `t_real_shapes`). **`duration_ms`** reads the top-level key (every engine now stamps it from the canonical `executor.turn_start_time` → `_turn_duration_ms()`); for turns persisted before that, it falls back to the per-class completion event ("execution complete"), taking the **max whole-turn candidate** (`total_duration_ms`, or `duration_ms` on a completion-typed event) so a sub-step's own timing (e.g. a 78 ms retrieval step) is never mistaken for the turn latency — keeping `latency` populated for all five classes across historical data. **Genie accessors (§12b):** `final_answer_text` falls back to `final_response` (the coordinator's answer key); `slaves_invoked()` reads the top-level per-expert summary first; `genie_rollup` exposes the inclusive (coordinator + experts) cost/tokens for the *delegated-cost annotation* — `turn_cost_usd`/`total_tokens` stay **self-cost** (coordinator-only), so portfolio sums never double-count. |
| `FlowRunRecord` | The flow-agent analogue ([`eval/flow_types.py`](../../src/trusted_data_agent/eval/flow_types.py)) over a flow run + `node_runs` — `completed`, `executed_nodes`/`failed_nodes`, `executed_identifiers`, `step_count`, `cost_usd`. Used when `profile_class == "flow"`. See §12a. |

`ALL_PROFILE_CLASSES = (llm_only, rag_focused, tool_enabled, genie, conversation_with_tools)` — the five profile classes the evaluation must cover; flows are the sixth eval class (`"flow"`), a distinct **agent type** (§12a). `FRAMEWORK_TOOLS` (`TDA_FinalReport`/`TDA_SystemLog`/`TDA_ContextReport`/`TDA_CurrentDate`) are excluded from trajectory scoring.

**Declarative assertion keys** (on `EvalCase.assertions`):
- *Profile-agents:* `require_tools_subset` · `must_not_call` · `max_self_corrections` · `max_cost_usd` · `optimal_steps`/`max_steps` · `expect_retrieval` · `require_citations` · `min_grounded` · `expect_slaves` · `max_slaves` · `must_contain`/`must_not_contain`/`answer_regex`/`max_answer_length`.
- *Flow-agents:* `expect_complete` · `require_nodes` · `forbid_nodes` · `max_cost_usd` · `max_steps` (§12a).

---

## 5. Scoring layer

### 5.1 Scorer abstraction + registry

[`eval/scorer.py`](../../src/trusted_data_agent/eval/scorer.py): `Scorer` ABC declares `dimension`, `kind`, `applies_to` (frozenset of classes; empty = all profile classes), and `gating` (gate-eligible). `register_scorer` + `scorers_for(profile_class, dimensions, kinds)` resolve the right suite per case. **Flows are special-cased**: `scorers_for("flow")` returns *only* scorers whose `applies_to` contains `"flow"` (never the empty-set "all" scorers, which assume a turn shape) — so flow-agents get only the flow scorers (§12a). **Adding a metric = one class + one `register_scorer` call.**

**3-band metric verdicts.** A `Verdict` carries an optional `band` ∈ {`healthy`, `review`, `danger`, `None`} alongside `passed` — the same 4-class taxonomy the roster uses (§11.1), but per metric. `scorer.banded_metric(scorer, *, value, gate, healthy, higher_is_better, …)` is the shared helper every threshold-bearing scorer routes through: it takes up to **two cut points** (the `gate`/Danger boundary and the `healthy`/green-amber split), is `higher_is_better`-aware (so `min_*` and `max_*` metrics share one code path), and sets `passed = (band != "danger")` — **Review (amber) passes the gate but is flagged; only Danger (red) fails** ("warn, don't gate"). One cut point = legacy single pass/fail line (no amber); none = informational (`passed=None`, no band). Curated cases set the boundaries via assertions (`<key>` = Danger, `<key>__healthy` = Healthy); the admin global defaults (§6.4) flatten into the same keys.

### 5.2 Deterministic scorers — the deterministic core

[`eval/scorers/deterministic.py`](../../src/trusted_data_agent/eval/scorers/deterministic.py) — 19 scorers, CODE only, no LLM, reproducible, CI-pure. (Three drive the Task Transparency framework — `TaskCountScorer`/`ParallelEfficiencyScorer`/`StepCountScorer` — see §5.2c.)

Design rules (from methodology research): **grade what matters, not the path** (tool assertions use *superset*/*must-not-call* semantics, never exact-sequence); one scorer = one dimension; abstain (`passed=None`) when the assertion is absent.

| Scorer | Dimension | Kind | Applies to | Gating |
|---|---|---|---|---|
| `CompletedScorer` | `completed` | det | all | ✓ |
| `AnswerPresentScorer` | `answer_present` | det | all | ✓ |
| `FormatConstraintScorer` | `format` | det | all | ✓ |
| `CostCeilingScorer` | `cost` | metric | all | ✓ |
| `TaskCountScorer` | `tasks` | metric | **all 5 + flow** | ✓ |
| `StepEfficiencyScorer` | `efficiency` | metric | all | ✓ |
| `ParallelEfficiencyScorer` | `parallel_efficiency` | metric | tool_enabled, genie, **flow** | ✓ |
| `StepCountScorer` | `steps` | metric | **tool_enabled only** (planner) | ✓ |
| `SafetyScorer` | `safety` | det | all | ✓ |
| `ChainIntegrityScorer` | `provenance_integrity` | det | all | — |
| `ToolSubsetScorer` | `tool_subset` | det | tool_enabled, conversation_with_tools | ✓ |
| `MustNotCallScorer` | `must_not_call` | det | tool classes | ✓ |
| `SelfCorrectionScorer` | `self_correction` | metric | tool classes | ✓ |
| `PlanAdherenceScorer` | `plan_adherence` | det | tool_enabled | — (soft) |
| `RetrievalPerformedScorer` | `retrieval_performed` | det | rag_focused | ✓ |
| `GroundednessOverlapScorer` | `grounded_overlap` | det | rag_focused | — (pre-signal) |

> **What `grounded_overlap` is allowed to measure.** The support set is the retrieved
> chunks **and the turn's attachments** (`RunRecord.support_chunk_texts`; since Oct 2026 —
> a Focus agent answers from an attachment when its repositories hold nothing, and an
> attachment deleted since makes every support reader abstain, see FILES_ARCHITECTURE §3, invariant 6)
> **plus the question** (parity with `grounding/corpus.assess` — echoing the
> user's own terms is not invention), and the scorer **abstains** whenever the delivered
> text is not a model claim: the grounding gate withheld or annotated it, or the honesty
> gate rewrote it. In those cases the delivered text is Uderia's own notice — which
> quotes the very spans it is reporting, so scoring it fails by construction and counts
> one shortfall twice at the same station. It is deliberately **not** gated on "does the
> answer make a groundable claim": that was tried and reverted, because a fluent
> fabrication carries no number, quote or identifier and would have escaped the one check
> that exists to catch it. Separating a refusal from a fabrication is the judge's job.
>
> **Refinement pairs (contract Rule 3.0c).** `groundedness` is declared as the judge that
> REFINES this check (`assurance_points.REFINEMENT_PAIRS`). Where both decided a turn, the
> pair's standing — not worst-of — sets what the coarse check contributes to its station:
> corroborated failure → danger, judge-only failure → danger, **disputed** (coarse flagged,
> judge cleared) → *worth a look*, both clean → quiet. The dimension's own band is never
> rewritten; only its station contribution (`sc`) changes, and the disagreement is named.
> Computed server-side in two places from ONE declaration: `read_turn` (per-turn, for the
> dial) and `analysis.online_quality_by_agent` → `turn_disputed_dims` (per-window, for the
> ring, since only the aggregator can tell "the judge passed" from "the judge never ran").

| `CitationPresentScorer` | `citations` | det | rag_focused | ✓ |
| `RoutingCorrectnessScorer` | `routing` | det | genie | ✓ |
| `CoordinationEfficiencyScorer` | `coordination_efficiency` | det | genie | ✓ |

`cost`, `efficiency`, `self_correction`, `tasks`, `steps` and `parallel_efficiency` are banded metrics (§5.1) — they read two cut points and grade green/amber/red.

#### 5.2a Reliability & quality scorers

[`eval/scorers/reliability.py`](../../src/trusted_data_agent/eval/scorers/reliability.py) — 27 further deterministic signals derived **entirely from the trace already persisted** (no new instrumentation): the provenance chain, `execution_trace` / `conversation_agent_events`, `knowledge_retrieval_event.similarity_score`, `kg_enrichment_event.scope_databases`, `context_window_snapshot`, `duration_ms`, `turn_cost`, and the gate stamps the runtime recorded. Two families — *was the process sound?* and *was the output sound?* (Per-dimension meaning: [AGENT_QUALITY_DIMENSIONS.md](AGENT_QUALITY_DIMENSIONS.md).)

| Scorer | Dimension | Kind | Applies to | Gating |
|---|---|---|---|---|
| `LatencyScorer` | `latency` | metric (banded) | all | ✓ |
| `TokenUsageScorer` | `tokens` | metric (banded) | all | ✓ |
| `CacheEfficiencyScorer` | `cache_efficiency` | metric (informational) | all | — |
| `BudgetAdherenceScorer` | `budget_adherence` | metric (informational) | tool classes, genie | — |
| `CleanTerminationScorer` | `clean_termination` | det | all | — |
| `ContextHealthScorer` | `context_health` | metric | all | — |
| `ToolSuccessRateScorer` | `tool_success_rate` | metric (banded) | tool classes | ✓ |
| `ToolLoopScorer` | `no_tool_loops` | det (banded) | tool classes | ✓ |
| `ErrorRecoveryScorer` | `error_recovery` | metric | tool classes | — |
| `ScopeAdherenceScorer` | `scope_adherence` | metric (banded) | tool classes | — |
| `ToolEconomyScorer` | `tool_calls` | metric (banded) | tool classes | — |
| `KgSeedUtilizationScorer` | `kg_seed_utilization` | metric (informational) | tool classes | — |
| `EnrichmentRecallScorer` | `enrichment_recall` | metric (informational) | tool classes | — |
| `OutputSanityScorer` | `output_sanity` | det | all | ✓ |
| `NumericFidelityScorer` | `numeric_fidelity` | det (banded) | tool_enabled, rag_focused | — |
| `PlanExecutionCoverageScorer` | `plan_execution` | det | tool_enabled | — |
| `RetrievalRelevanceScorer` | `retrieval_relevance` | metric (banded) | rag_focused | ✓ |
| `ExpertSuccessScorer` | `expert_success` | det | genie | ✓ |
| `GovernedDataRatioScorer` | `governed_data_ratio` | metric (banded) | tool classes | — |
| `ContextUtilizationScorer` | `context_utilization` | metric (advisory) | all but genie | — |
| `KnowledgeFreshnessScorer` | `knowledge_freshness` | metric | all but genie | ✓ |
| `HonestAbsenceScorer` | `honest_absence` | det | tool classes | ✓ |
| `SurfaceViolationScorer` | `surface_violation` | metric (informational) | tool classes | — |
| `CitationFidelityScorer` | `citation_fidelity` | metric (banded) | tool classes, rag_focused, genie | — |
| `IdentifierProvenanceScorer` | `identifier_provenance` | det | tool classes, rag_focused, genie | — |
| `LibraryReadFirstScorer` | `library_read_first` | metric (informational) | tool classes, rag_focused | — |
| `ActionSafetyScorer` | `action_safety` | metric (informational) | tool classes | — |

"Tool classes" = `tool_enabled` + `conversation_with_tools`. Several of these grade a **property** through the assurance registry rather than re-deriving it (`property = "<name>"` on the class — see [ASSURANCE_SUBSTRATE_ARCHITECTURE.md](ASSURANCE_SUBSTRATE_ARCHITECTURE.md) § *The reverse link*), and a bound third-party property gets a generic `PropertyScorer` from [`eval/scorers/plugin_properties.py`](../../src/trusted_data_agent/eval/scorers/plugin_properties.py) ([ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](ASSURANCE_MODULE_PLANE_ARCHITECTURE.md) §5). **Census (Nov 2026): 63 registered dimensions** — 19 deterministic + 28 reliability + 10 judges + 6 flow — plus any bound pack property.

**`governed_data_ratio` (Jul 2026, extended Aug 2026) — the OBDA-READ governed-data incentive signal.** Of a turn's data operations, the fraction that went through the **governed, deterministic lane** (a product/metric compiled to ratio-safe SQL — including a raw-SQL call whose statement matches a governed compile's output from the same turn, `execute_governed`) vs **free-formed** raw SQL the agent wrote itself. Higher is better; non-gating — it *rewards* the governed lane rather than blocking, creating an incentive gradient toward signed data products. Abstains when the turn did no data ops, when the OBDA-READ component is off for the agent (`obda_planes.plane_enabled` — a disabled OBDA-READ must not band the scorecard), and on a **confined** turn (every raw attempt was DENIED by the data surface — nothing executed, and a denial is deliberately *neither* lane: counting it free-formed would punish the agent for being protected). Its sibling **`surface_violation`** (BOUNDED, ACT) counts the surface's own denials — non-gating by design, a steady nonzero being a producer-side *product gap made measurable*. Both read the **same trace the OBDA-READ Live Status card is built from** — observability and eval are two ends of one signal, sharing the pure classifier `agent/obda.py`. Every governed consumption additionally seals an offline-verifiable receipt into the turn's EPC (§6.4c). Full architecture: [OBDA_ARCHITECTURE.md](OBDA_ARCHITECTURE.md).

**`scope_adherence` + `tool_calls` (Jun 2026) — the KG-grounded scope/efficiency pair.** Both are deterministic, 0-token, **non-gating** (they band the Quality card red/amber but never fail a release gate — *observe, don't constrain*; the prompt stays flexible, the rigor lives in the eval layer). `scope_adherence` counts data-source calls that reach **outside the KG-declared deployed scope** — the KG enrichment now stamps `scope_databases` (derived deterministically from the resolved `deployed:` identifiers) onto its metadata → `kg_enrichment_event` → `RunRecord.kg_scope_databases()`; the scorer compares each call's `database_name`/SQL `FROM`-qualifier against that set (system catalogs like DBC allowed), and **abstains** when no deployable scope was declared (logical-only / no enrichment). `tool_calls` is raw call volume — it catches brute-force/retry-storm thrash that `no_tool_loops` (identical-args repeat) misses because the calls aren't byte-identical. Together they pinpoint the failure mode where an agent ignores a complete KG enrichment and hunts unrelated databases (the canonical *"average order delivery time" → maintenance_work_order* case: `scope_adherence=3 danger`, `tool_calls=19 danger`).

> **Shared primitives with the grounding gate (§6a).** `scope_adherence`'s scope math (`dbs_touched`, `scope_diff`) was **extracted** into the neutral `agent/grounding/primitives.py`, which BOTH this scorer and the on-path grounding gate import. So the off-path "this turn drifted" verdict and the on-path "withhold this answer" decision are computed by the *same* code — they can never diverge on what "out of scope" means. The scorer observes; the gate enforces; one definition.

The banded scorers plus `cost`/`steps`/`self_correction` from §5.2 are the metrics exposed as sliders in **Administration → App Config → Trust → Quality bands**, grouped by moment (§6.4).

#### 5.2b KPI harmonization across profile classes

A deep per-class assessment (*what can each class's execution signals support?*) drives a harmonization layer so the card reads the same for every class:

- **Universal core** — `cost`, `latency`, **`tokens`** (`TokenUsageScorer`, banded by `max_tokens`), `completed`, `answer_present`, `output_sanity`, `safety`, `clean_termination` — computed for **all five** classes. `clean_termination` derives "terminal answer produced" from answer+status when there's no signed provenance chain (ReAct / Ideate / Focus), so it's no longer Optimize-only.
- **Tasks vs steps (see §5.2c)** — `tasks` (`TaskCountScorer`) is the universal execution-work KPI across **all five classes + flows**; `steps` (`StepCountScorer`) is the **Optimize-only planner** plan-size signal. `efficiency` is purely the `optimal_tasks/actual` convergence ratio and abstains without a target.
- **Exposure** — the Scorecard chips **group by the MOMENT a measurement was taken** (`Plan · Retrieve · Act · Compose · Deliver · The run · Flow run` — declared in `eval/stages.py`, SERVED on `/v1/assurance/vocabulary`, rendered by `ui.js:_stageGroupsForCard`), each moment rendering only if the class produced a check in it. The earlier KPI-tier taxonomy is retired. Per-metric formatting (`_fmtMetricScore`) renders counts as `1`, rates as `0%`, cost `$…`, latency `4.1s`, tokens with commas, parallel speedup `1.50×`.

Per-class scorer counts (`scorers_for(class)`, incl. judges, Oct 2026): `llm_only`=19, `conversation_with_tools`=41, `rag_focused`=28, `tool_enabled`=45, `genie`=24, `flow`=8 (flows have no judges).

#### 5.2c Task Transparency Classification Framework

[`eval/tasks.py`](../../src/trusted_data_agent/eval/tasks.py) — the agent-type-consistent view of *what work an agent did*. It turns a turn (or flow run) into discrete, typed **Tasks**, deterministically (CI-pure, 0 tokens) from the **signed provenance chain** (profile-agents) or **node runs** (flow-agents). `classify_tasks(run)` is **polymorphic over `RunRecord` and `FlowRunRecord`**, so the same KPIs render identically on every agent's Quality card.

**A Task = one discrete unit of executed work**, with a *class* and a *mode*:

| Task class | Meaning | Derived from |
|---|---|---|
| `planning` | strategic planning — the **whole** planning phase collapses to **1 task** | `strategic_plan` + every `plan_rewrite` (merged) |
| `retrieval` | a knowledge / RAG search | `rag_search`/`rag_results` |
| `tool` | a real MCP / component / connector invocation ("doing") | `tool_call`/`agent_tool_call` for non-framework tools; flow `task_tool`/`task_function` |
| `reasoning` | an LLM generation/synthesis ("thinking") | the **final-report synthesis** (`TDA_FinalReport`); `rag_synthesis`/`llm_call`/`coordinator_synthesis`; ReAct `conversation_llm_step` events; flow `task_llm`/`decision`/`task_prompt`/`task_extension` |
| `coordination` | consulting a sub-agent / expert | genie `slaves_invoked` |
| `correction` | re-doing failed work — **rework** (the quality class the Live Status filter omits) | `self_correction` / `error:*` steps |

**Modes:** `sequential` · `parallel` (provably overlapped a sibling, or part of a parallelising orchestrator wave). `CONTEXT`/`SYSTEM` Live-Status events are scaffolding (context assembly, session naming) — overhead, **not** tasks.

**What is deliberately NOT a task** (so the count reflects real work, not bookkeeping):
- **`tactical_decision`** — per-phase routing that is usually **FASTPATH** (args pre-known from the strategic plan → *no* LLM call). Counting it would over-count reasoning on the common path; the phase's real work is its tool call, and the genuine reasoning is the strategic plan + the final synthesis.
- **Framework scaffolding tools** — `TDA_SystemLog` / `TDA_CurrentDate` / `TDA_ContextReport` / `TDA_SystemOrchestration` (the orchestrator's wrapped calls are added separately as a parallel wave). `TDA_FinalReport` is the exception — it IS the answer synthesis, so it's classified as `reasoning`, not `tool`.

**steps ≠ tasks (a deliberate two-layer model).** `steps` is a **planner** concept — the count of planned phases in the Optimize strategic meta-plan — so it applies to **`tool_enabled` only**. Each planned *step* decomposes into execution *tasks*. Example: an Optimize turn with a 2-phase plan (one data query + final report) reads **`steps`=2, `tasks`=3 (1 planning · 1 tool · 1 reasoning)** — the planner call, the data tool, and the synthesis; the two FASTPATH tactical decisions are not tasks.

**The three task KPIs (all banded, all honest):**

| Dimension | Applies to | Quality lever | Banded by |
|---|---|---|---|
| `tasks` | **all 5 classes + flow** | work VOLUME (bloat → cost/latency); explanation carries the composition breakdown | `max_tasks` |
| `efficiency` | all 5 classes (convergence) | detours/loops (`optimal_tasks/actual`) | `optimal_tasks` (alias `optimal_steps`) |
| `parallel_efficiency` | **parallel-capable: `tool_enabled`, `genie`, `flow`** | LATENCY — speedup `tasks / waves` from concurrency | `min_parallel_efficiency` |
| `steps` | `tool_enabled` only | plan SIZE (planner) | `max_steps` |

**Parallelism is honest by construction.** A wave = tasks that ran concurrently, established by (1) an explicit orchestrator group (`TDA_SystemOrchestration` wrapping N>1 calls via `asyncio.gather`), else (2) provable wall-clock overlap of provenance/node timestamps. Profile-agent provenance steps are appended **serially**, so timestamp overlap rarely appears — `parallel_efficiency` therefore **abstains** (`passed=None`) for most profile turns rather than reporting a fabricated `1.0`. **Flow-agents are the strongest case**: their node runs carry real `started_at`/`completed_at`, so parallel branches yield a genuine speedup (e.g. 2 overlapping SQL nodes + 1 LLM node → `1.50×`). Chips live in the **Tasks** tier; speedup formats as `1.50×`. Admin sliders: `max_tasks`, `min_parallel_efficiency` (Tasks group), `max_steps` (Planning group).

**Composition transparency (exposed end-to-end).** The `tasks` verdict carries the class split as structured `evidence` (`["1 planning", "2 tool", "2 reasoning"]` via `TaskSet.breakdown()`), so the split is visible — not just the bare count — for **every applicable profile class + flows**:
- **Quality card chip** (`ui.js:_renderVerdictChips`) renders the split inline after the count: *`tasks ·metric 5 · 1 planning · 2 tool · 2 reasoning`*.
- **Agent Performance UX** — `build_quality_span` emits per-class counts (`eval.task.<class>`), `online_quality_by_agent`/`online_quality_by_flow` average them into `task_composition`, and the agent-detail view renders a **Work Mix** stacked composition bar + legend (`agentPerformanceHandler.js:taskMix`, IFOC/Live-Status-aligned colors) showing the agent's average tasks-per-turn and work mix.

### 5.3 LLM-judge layer

The deterministic layer covers correctness *structure*; judges refine the *quality* code can only approximate.

**Panel-of-LLMs** ([`eval/judge.py`](../../src/trusted_data_agent/eval/judge.py)): a diverse-family panel beats a single judge at lower variance — and Uderia's 11 providers make it free. `build_panel()` selects up to N configs across **distinct provider families** (mitigates self-preference / correlated error). `parse_judge_response()` robustly parses `{verdict, score, evidence, reasoning}` (via `robust_json_parse`); `consensus()` takes a **majority vote** ignoring abstentions (ties → fail; all-unknown → `None`). `JudgePanel` ABC has a live `LLMHandlerPanel` and a deterministic `FakePanel` (CI).

**Per-configuration opt-out (`judge_panel_enabled`).** A model can be excellent at answering and poor — or simply unusable — as a judge. **Scope, stated plainly:** one panel serves *both* judge layers, so excluding a model removes it from the in-flight judge a grounding gate escalates to (whose ruling can withhold an answer) as well as from the post-hoc scoring panel. That is deliberate — the two layers ask the same models the same kind of question — but it means this is not a measurement-only setting. And a member that never responds is *worse than absent*: every judge waits out its per-call timeout, so **one silent model sets the latency of the whole evaluation layer** (observed in production: a single member contributing no vote produced three 20 s timeouts on one turn). Any LLM configuration can therefore be excluded from the panel, from the LLM configuration editor's **Evaluation role** control.

The rule is stated once in `judge.judge_panel_enabled(cfg)` and read by `build_panel`, which is the **single selection path** behind all four judge consumers — the online hook, the manual "Score this turn", the CLI, and the grounding gate's in-path escalation — so one flag governs every one of them, including the judge that can withhold an answer.

| Stored value | Behaviour |
|---|---|
| key absent | **participates** — every configuration that predates the option is unchanged |
| `true` | participates |
| `false` | excluded from the panel; the model answers exactly as before |
| anything else | participates — the failure direction that matters is silently *losing* a judge |

Exclusion is per **configuration**, not per provider family (a second config of the same family can still be picked), and it is an explicit operator decision rather than an auto-eviction — a model that is merely slow today may be the panel's most valuable member tomorrow. When exclusion empties the panel, `build_panel` says so by name instead of reporting the credentials problem it isn't; judges then abstain (§6.4).

**The 10 decomposed judges** ([`eval/scorers/judges.py`](../../src/trusted_data_agent/eval/scorers/judges.py)) — each scores ONE dimension in isolation, cites evidence, and may answer "unknown":

| Judge | Dimension | Applies to | Abstains unless |
|---|---|---|---|
| `AnswerCorrectnessJudge` | `answer_correctness` | all | `reference_answer` present |
| `GroundednessJudge` | `groundedness` | rag_focused | retrieved docs present |
| `InstructionFollowingJudge` | `instruction_following` | llm_only, conversation_with_tools | answer present |
| `ToolArgOptimalityJudge` | `tool_arg_optimality` | tool classes | tool calls present |
| `AnswerGroundednessJudge` | `answer_groundedness` | tool classes | tool results present |
| `ScopeFaithfulnessJudge` | `scope_faithfulness` | tool classes | KG scope declared |
| `SynthesisQualityJudge` | `synthesis_quality` | genie | slaves invoked |
| `ContentSafetyJudge` | `content_safety` | all classes | answer present |
| `SourceConsistencyJudge` | `source_consistency` | rag_focused | ≥2 distinct retrieved documents |
| `GrainFaithfulnessJudge` | `grain_faithfulness` | tool classes | answer present (also the honesty gate's optional in-path escalation, `honestyConfig.grainJudge`) |

`ContentSafetyJudge` (Jul 2026) is the **semantic complement to the deterministic outbound guardrail** — it covers the residue only meaning can catch (sarcastic/coded abuse, harm-enabling instructions, indirect personal identification via quasi-identifiers), advisory on the sampled judge layer and never blocking the answer path; professional criticism is explicitly not a failure.

`AnswerGroundednessJudge` (Jun 2026) is the **tool-class analogue of `GroundednessJudge`** (which is RAG-only): "is every factual claim and number in the ANSWER supported by the TOOL RESULTS the agent actually obtained?" — judging *support* only, not whether the data answers the question (`instruction_following` owns that). It's the judge that catches a **fabricated value** a code grader (`numeric_fidelity`) can only approximate — on the canonical *"230-day average"* case it returned a unanimous 0✓/3✗ fail ("cites a table and average that do not exist in the tool results").

`ScopeFaithfulnessJudge` (Jun 2026) is the **outcome companion of the deterministic `scope_adherence` scorer** and the **decision-maker for the grounding gate's enforce mode** (§6a). It reads only the QUESTION, the ANSWER, and the KG-declared TRUSTED SCOPE description (`RunRecord.kg_scope_description()`) — **not** the tool-call databases — so it is independent of the deterministic mechanism signal and judges the delivered *outcome*: is the answer's **subject matter** within the trusted scope, or did it drift to an out-of-scope domain (a governance hallucination)? Critically, its rubric judges **domain only, never value correctness** — an answer about in-scope concepts is *faithful* even if its specific numbers can't be verified (that's `answer_groundedness`'s job), and an honest "that data isn't represented in the trusted scope" is *faithful*, not a failure. That independence is what makes it a valid ground-truth oracle for the anti-hallucination benchmark (§6b) and the right authority to make the gate's withhold/deliver call.

`GroundednessJudge` (evidence contract, Oct 2026) is the **decision-maker for the grounding gate's corpus family** (RAG_TRUST_HARDENING §1.2 C2: the deterministic trigger does recall, this judge does precision). Three rules, each learned from a withheld-but-grounded answer on production (session 727292c3): **(1) it reads the ONE support text the trigger read** — `RunRecord.retrieved_chunk_texts()` (chunk content + the citation line the model was shown), never a second rendering of the chunks; a judge handed `docs[:8]` clipped to 5000 characters ruled two terms "entirely absent" that sat in chunk 9. **(2) Evidence is budgeted by WHOLE chunks in retrieval order** (`ONLINE_EVAL_JUDGE_EVIDENCE_CHARS`, 24K; the answer under judgment by `ONLINE_EVAL_JUDGE_ANSWER_CHARS`, 12K — never silently cut), and an unavoidable truncation is **stated** in the prompt ("N of M retrieved documents shown … answer *unknown* rather than *fail*"), so a claim the judge cannot find is an abstention, never an absence. **(3) The gate hands the judge the spans its trigger could not find** (`grounding_trigger_spans`, a FLAGGED SPANS section: "rule on each — a faithful paraphrase of the documents IS supported"), so the adjudication leads with the findings the user will be shown. `SourceConsistencyJudge` shares the budgeted evidence (`_budgeted_evidence`). Gate: `test_eval_judges.py::t_groundedness_evidence`.

**Tool-arg hygiene (Jun 2026):** LangChain injects framework plumbing (`ToolRuntime`, callback/run managers) into a tool's input dict via `InjectedToolArg`. `conversation_agent._clean_tool_args()` strips these at the `on_tool_start` capture point (by key name + value type-name) so the persisted `conversation_agent_events` arguments are only what the model chose — the giant `"ToolRuntime(state=…)"` blobs no longer pollute the trace or mislead `tool_arg_optimality`.

**Judge cost in the manual overlay — full allocation (Jun → Jul 2026):** `POST /v1/evals/score-turn` sums the panel's per-model `usage`, prices it via `CostManager`, and returns `cost_usd`/`input_tokens`/`output_tokens` so the trajectory overlay shows what the LLM-judge pass cost. But a manual re-score spends **real** judge tokens on the user's credentials, so — design imperative — it must be **allocated exactly like any other spend**, not merely reported. It therefore routes through the canonical cost sink `_record_score_usage` (§6.4a) after computing the cost: session + turn totals (authoritative for plan reload), the consumption/billing ledger, and a live KPI `token_update`. It also stamps the judge cost onto the persisted quality span (`eval.judged`/`eval.cost_usd`/`eval.input_tokens`/`eval.output_tokens`) so a plan reload's Quality card echoes it via the `turn-score` GET. Deterministic-only scoring spent 0 tokens → clean no-op.

Judges are **non-gating by default** until calibrated. Sync `score()` abstains; the runner invokes async `score_async(case, run, panel)` via `make_judge_fn(panel)`.

**Calibration** ([`eval/calibration.py`](../../src/trusted_data_agent/eval/calibration.py)): `calibrate(pairs)` computes **Cohen's κ** + raw agreement + precision/recall over (human, judge) label pairs (abstentions excluded). A judge is only promoted to *gating* once κ ≥ `DEFAULT_KAPPA_GATING_FLOOR` (0.6, substantial agreement). Human labels come from the SME annotation queue (§7). **The loop is closed end to end (Aug 2026):** an annotation stores `dimension` + `judge_passed` — the ruling the human SAW, frozen at annotation time so a later re-score cannot rewrite what was agreed with — and `pairs_from_annotations`/`report_from_annotations` derive per-dimension κ from the stored queue (`agree` → human == judge; `disagree` → the opposite; rows without a frozen ruling are notes, never evidence). `DEFAULT_MIN_PAIRS` (10) is a measurability floor: below it κ is reported but can never clear the gate — κ over three pairs is noise wearing a statistic's clothes. Served at `GET /v1/evals/judge-calibration`; the one-click work queue (`GET /v1/evals/judge-queue`) lists recent judge rulings from the online spans with Agree/Disagree in the Trust view's Judge Calibration section. `status: calibrated` means **eligible** for promotion to gating — promotion itself stays a deliberate governance act; nothing flips a scorer automatically.

---

## 6. Evaluation methods

### 6.1 Offline runs — pass^k reliability

[`eval/runner.py`](../../src/trusted_data_agent/eval/runner.py) · [`eval/aggregate.py`](../../src/trusted_data_agent/eval/aggregate.py)

`run_dataset(store, dataset_id, executor, k, …)` executes each case **k times** through a **pluggable executor** (sync/async; tests inject a fake, production injects the REST executor) and scores every rep. A rep **passes** iff all *gating* verdicts are `True`. `aggregate_run` computes:

- **pass^k** — fraction of cases that pass on *all k* reps (τ-bench reliability; surfaces the long tail single-shot accuracy hides).
- **Bootstrap 95% CI** (deterministic LCG — reproducible).
- `per_dimension`, `per_cohort` / `per_cohort_pass_k`, `per_profile_class`, `case_pass_k`.

The executor decoupling means "execute + score" exercises the **real engines** — no special eval path. [`eval/rest_executor.py`](../../src/trusted_data_agent/eval/rest_executor.py) drives the live REST query API (auth → create session → submit the **bare question** with `profile_id` in the body — never an `@TAG` in the prompt, which would pollute retrieval — → poll → read the resulting turn). For flow-agent cases, [`eval/flow_runner.py`](../../src/trusted_data_agent/eval/flow_runner.py)'s `FlowEvalExecutor` runs the flow via `POST /flows/<id>/execute` (binds a session at run time) → poll → read the run; `CompositeEvalExecutor` routes each case to the right executor by agent type, so a dataset may mix both (the CLI `run` uses it by default). See §12a.

### 6.2 Regression gate

[`eval/gate.py`](../../src/trusted_data_agent/eval/gate.py): `gate_run(store, run_id, baseline_run_id, min_pass_k, cohort_thresholds, regression_epsilon)` blocks a release on: per-cohort floors, baseline regression (epsilon), an over-fit smell (100% on ≥10 cases), and CI significance. Returns `{passed, failures[], warnings[]}`. The CLI maps this to a **CI exit code**.

### 6.3 Trace mining

[`eval/mining.py`](../../src/trusted_data_agent/eval/mining.py): `mine_cases_from_sessions(...)` seeds golden cases from **production traces** (via WS1's session-storage public API), defaulting to **anomalous** turns (errored / invalid / self-correction ≥ 2) and pre-filling assertions (e.g. `require_tools_subset` from the observed trajectory). Golden sets grow from real failures, not imagination.

### 6.4 Online evaluation (tail sampling)

[`eval/online.py`](../../src/trusted_data_agent/eval/online.py) — the same deterministic scorers grade **every live turn** (when enabled), so quality surfaces from real traffic. Deterministic scoring is free + non-blocking, so it's unconditional; the token-costing **judge layer is the only stochastic part**.

- **Seam (backgrounded — never blocks a turn):** `execution_service._dispatch_online_eval(...)` fires `evaluate_turn_online(...)` as a **tracked background task** right after the recommender — it is NOT awaited in the turn's critical path. The answer is already delivered; scoring is pure observability. (A strong reference is held per task to prevent GC and discarded on completion.) Inside the hook: deterministic scoring runs first (instant, always persists the bands), then — only if judges are enabled — the judge layer runs **bounded by `asyncio.wait_for(...)`**, so a stalled provider can never hold the task open. **A timeout no longer discards the layer**: verdicts land in a sink as each judge completes, so the cut-off keeps every ruling that finished (and was billed), and each judge it silenced is recorded as an abstention *naming the cut-off* — a reader can tell "never applicable" from "tried and lost". *(History: this was `verdicts.extend(await wait_for(...))`, where the `extend` never ran on timeout — completed rulings died with the cancelled coroutine while their tokens were still charged. Every judge run this shipped ever paid for kept zero verdicts; see the Aug 2026 changelog entry.)* The hook also selects the turn **by `turn_id`** (not `wf[-1]`) since a fast next turn may have appended. Skipped for genie slave sub-turns. Flows hook in at `flow_executor._run_and_record`. *(History: this was awaited inline with no timeout; a stalled judge LLM call could hang the whole turn — fixed Jun 2026 by backgrounding + the timeouts below.)*
  - **Live Quality card (dual-channel emit).** The `turn_score_complete` event populates the after-execution Quality card. Because the hook is now backgrounded, the per-request `/ask_stream` (which `processStream` reads) has already closed by the time scoring finishes — so the hook ALSO broadcasts the event over the **per-user notification channel** (`APP_STATE["notification_queues"]`, an always-open EventSource). `notifications.js` renders it session-guarded; the overlay (`injectTrajectoryEvalOverlay`) is idempotent, so the two channels can't double-render. Without this, the card would only appear on a plan reload.
- **Judge sampling (the only stochastic bit):** when `ONLINE_EVAL_INCLUDE_JUDGES` is on, `should_judge()` judges every turn that is *anomalous* (status/error/self-corrections) **or deterministically FLAGGED** — deterministic scoring now runs FIRST, and any failed check or danger-band metric triggers the panel via `deterministic_flagged(verdicts)` (judges refine what the free layer condemned; previously the decision preceded scoring, so at rate 0 the one turn every deterministic check failed earned no judge) — plus a `ONLINE_EVAL_JUDGE_SAMPLE_RATE` fraction of the clean rest (admin-controlled; **rate 0 = the honest "judges only when something is wrong" policy**). Deterministic scoring is never sampled. **Both levels of the judge layer are concurrent.** The panel calls every judge *model* concurrently (`asyncio.gather`), each bounded by `ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS` — a hung/failing model degrades to an empty response (→ `unknown` in consensus). And `run_judges` runs every applicable *judge* concurrently, with per-judge isolation (a judge that raises becomes a named abstention rather than losing the panel). This matters more than it looks: the judges used to run sequentially inside a cap on the whole layer, so N judges serialised to N call windows and **one hung model consumed the entire budget inside the first judge**, starving the rest.

**The layer budget is satisfiable by construction.** The effective cap is `max(ONLINE_EVAL_TIMEOUT_SECONDS, ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS + margin)`, so it always leaves room for one full-length judge call. Shipped defaults were a 20 s per-call timeout inside a 25 s layer cap — a combination that, with the sequential loop, guaranteed a total loss whenever a single model hung. The floor makes the relationship a property of the code rather than of whoever last edited the two numbers independently.
- **Per-profile JUDGED-station config (`judgeConfig`, PROP-002 follow-up):** the pure `online.resolve_judge_prefs(profile, include_default=…, default_rate=…)` resolves `judgeConfig.judges` (`inherit` default · `on` · `off` — a profile can opt its turns INTO judging even when the deployment default is off, never the reverse of the `ONLINE_EVAL_ENABLED` scoring master), `judgeConfig.sampleRate` (clamped [0,1] override; malformed values fall back — a typo must not disable judging) and `judgeConfig.inboxOnCondemnation` (default ON). Registered in `profile_config_contract` (portable, **unsigned** — measurement config). Edited in the profile editor's **Trust Configuration → Judges** card.
- **The judge-condemnation incident (PROP-001, surfaced):** a DELIVERED answer whose groundedness-family judge verdict fails records a **danger `judge_condemnation` incident** in the Assurance Inbox (+ live notice); a turn the gate already withheld records nothing (no double count). Gated per profile by `inboxOnCondemnation`; runs once per turn from the online hook only.
- **OTLP-shaped quality spans:** `build_quality_span()` emits a vendor-neutral span (`name="agent.turn.eval"`) through a **pluggable exporter** (`JsonlExporter` default, `MemoryExporter` for tests). Attributes carry per-dimension verdicts plus agent identity (`agent.profile_tag`/`agent.profile_id`) and owning `user.uuid` (for My-vs-System scoping). Swap the exporter for a real OTLP exporter without touching the hook.
- **Signed attestation (EPC tie-in):** `sign_attestation()` binds the verdict digest to the turn's `chain_tip_hash` and signs it with the **same Ed25519 provenance key** — a tamper-evident quality annotation auditable alongside the trajectory it grades. Degrades gracefully (`signed=False`) if the key is unavailable.
  - **Third consumer of the same substrate — the `Trust Membrane (Tier 5.2)`.** `eval/online.py:sign_payload()` (the shared signer behind both EPC and eval attestations) is reused *verbatim* by `core/trust/store.py:sign()` to sign **trust attestations** (a per-artifact + composed-agent trust badge). So one Ed25519 provenance key now signs three things — the execution trajectory (EPC), the eval verdict that grades it, and the trust badge that says an agent is made of validated ingredients — all offline-verifiable with the same committed [Tier 3.1 verifier](../proof-packs/samples/verify_attestation.py).
- **Flows** ride the same machinery on their own seam: `evaluate_flow_run_online()` is called from `flow_executor._run_and_record` (flows execute on a separate path from `run_agent_execution`), scores the run with the flow scorers, and emits an `agent.flow.eval` span keyed by flow identity (`agent.kind='flow'`, attestation bound to the run id). See §12a.
- **Admin global default thresholds (banded):** `default_assertions()` reads `APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS` and seeds **every** live/manual case's assertions, so the threshold-bearing scorers band/gate live traffic instead of abstaining. Config is the 3-band shape `{metric: {healthy, danger}}` (a bare scalar = legacy single Danger line); `default_assertions()` flattens it to `<key>` (Danger/gate) + `<key>__healthy` (Healthy split). A curated `EvalCase`'s own assertions always override. Admin-controlled at runtime (no restart) via `agent_eval.default_thresholds` in `/v1/admin/app-config`, edited in the **Administration → App Config → Trust** panel, under **Quality bands**: each slider renders beneath the MOMENT its dimension measures (Plan · Retrieve · Act · Compose · Deliver · The run · Flow run) — grouping derived at runtime via `QualityDimensions.stageOfThreshold`, thresholdSliders' stage-host mode, so a new scorer's slider appears without a UI edit. The UI is a set of **banded dual-handle sliders** ([`static/js/handlers/thresholdSliders.js`](../../static/js/handlers/thresholdSliders.js)) — one rounded track per metric with two draggable handles and the Healthy/Review/Danger bands painted green/amber/red (direction-aware: green-left for `max_*`/lower-is-better, green-right for `min_*`/higher-is-better), a **Strict / Balanced / Lenient / Clear** one-click preset bar, and a per-metric **Off** (keeps it informational). The module owns the state (`init`/`setThresholds`/`getThresholds`/`applyPreset`); `adminManager` loads via `setThresholds(default_thresholds)` and saves via `getThresholds()` — the stored `{healthy, danger}` shape is unchanged, and `normalize()` completes legacy single-bound configs + orders the handles per direction. The panel carries its **own "Save Trust Settings" button**, persisting the whole `agent_eval` block (online toggle + judges + thresholds) via `saveFeatureSettings()` from where it's edited.

> **⚠ Save-handler allowlist must stay in sync with the slider metrics.** The `POST /v1/admin/app-config` handler ([`admin_routes.py`](../../src/trusted_data_agent/api/admin_routes.py)) coerces `default_thresholds` through a hardcoded `_int_keys`/`_float_keys` allowlist and **silently drops any key not in it**. A new banded metric must be added to one of those sets or it can never be persisted from the admin UI (the slider renders but every save throws it away). Fixed Jun 2026 to include `max_tool_calls`/`max_out_of_scope_dbs` (the new pair) **and** `max_tasks`/`min_parallel_efficiency` (a pre-existing omission from when the Tasks tier was added). `config.py` seeds `max_out_of_scope_dbs`/`max_tool_calls` so they band out-of-box on a fresh start.

**Persistence (Jun 2026):** the whole `agent_eval` block (online toggle, judges, judge rate, banded thresholds) is **persisted to the `system_settings` table** (key `agent_eval`, JSON) on every save and **restored into `APP_CONFIG` at startup** by `_sync_agent_eval_to_config()` (mirrors the `tts_mode` pattern). Persisted values override the `config.py` seed; absent → the seed stands. So an admin's saved Balanced set now survives a restart instead of resetting. (`SystemSettings.setting_value` widened `String(255)` → `Text` so the 14-threshold JSON fits.)

> **Apply semantics:** a save updates `APP_CONFIG` in memory (immediate — no restart) for **new** turns (online auto-score) and **Re-score**, AND persists the `agent_eval` block to `system_settings` (above), so it survives a restart. It does **not** retroactively rewrite already-persisted turn scores — those are frozen snapshots (an old turn shows the thresholds in force when it ran; click **Re-score** to re-grade with the current set). On a fresh install with no saved settings, the `config.py` seed (`ONLINE_EVAL_DEFAULT_THRESHOLDS`) stands. *(Other `APP_CONFIG` blocks that aren't wired to `system_settings` — e.g. `RAG_ENABLED` — still reset to their `config.py` defaults on restart; only `agent_eval` is persisted.)*

Deterministic scoring is on by default (`ONLINE_EVAL_ENABLED=True`); judges off (`ONLINE_EVAL_INCLUDE_JUDGES=False`).

### 6.4b What makes a turn "failed" — and what it deliberately does not measure

The roster's live pass rate, its sparkline, the KPI dashboard, the timeline and the runs
ledger all sit on ONE predicate: `analysis.turn_failed()` / `conviction_count()`, built
from `convicting_dimensions()` + `gate_withheld()`.

**A turn fails iff** a check a **RULE measured** landed in the danger band — **unless** the
judge that refines it corrected that (Rule 3.0c `disputed`, named by `judge_cleared()` and
rendered as a `judge-cleared` mark) — **or** a gate **withheld** the answer. Amber never
convicts (`banded_metric`: *"Amber (review) PASSES the gate but is flagged; only Danger
(red) fails"*), and **a judge alone never convicts**: a model opined, no rule measured it.

**Why `Scorer.gating` no longer decides a turn's fate.** The predicate previously convicted
only on *gate-eligible* dimensions. Laid against 18 real turns the two rules disagreed on
exactly **two**, and both disagreements were the gate-eligible rule losing information: a
turn where `grounded_overlap` AND `groundedness` — the declared refinement pair, i.e. the
strongest grounding evidence available — **both** found the answer unsupported scored
**clean**, because each is individually advisory; and a turn the grounding gate withheld
with zero failing measurements, which no measurement-only rule can express. `Scorer.gating`
keeps its real job: the Scorecard's gating-vs-flagged split.

**A coarse check with no refining judge stands alone at full weight** — the exception is
only for a *declared* pair (`REFINEMENT_PAIRS`, one entry today), because a pair is a claim
that two checks measure ONE property. It is the only mechanism by which a model's opinion
can change a turn's verdict.

**It is a QUALITY reading, not an intervention one.** That is deliberate and it is the
only honest choice: on a real 18-turn sample, 16 turns had no mechanism act at all, so an
intervention-driven rate would have read **89%** on an agent whose grounding measurements
were its worst feature. The two axes are independent — see Rule 3bb in
[TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md), which carries the 2×2 and
the worked numbers.

**Resolved against the CURRENT registry, never the stored scalar.**
`eval.gating_failures` is a derived value written onto the span at scoring time, and a
sink outlives a policy change: spans written before `Verdict.gating` became a derived
property convict on ADVISORY dimensions, beside newer spans that correctly do not. One
live agent read **44%** with 3 of 10 failures convicted by advisory checks alone;
re-derived under gate-eligibility, **61%**; under the current rule, **8 failed of 18**. So the predicate reads the span's own `eval.dim.*.passed` attributes
— the primary facts, always present — through `is_gating_dimension`, falling back to the
stored scalar only for a span carrying no per-dimension verdicts. Nothing is rewritten:
the Ed25519-signed record keeps every attribute it was written with; only the derived
aggregate is recomputed. (Precedent: `GET /v1/evals/turn-score` already back-fills
`gating` when replaying older spans.) A gate asserts no consumer reads the scalar
directly.

**The two interventions are treated asymmetrically, on purpose.**

| intervention | treatment | why |
|---|---|---|
| inbound refusal (`eval.gate_only` span) | **excluded** from the quality denominator entirely | *"it must reach the gate record WITHOUT diluting a quality denominator it was never measured against"* — a refused request never produced an answer |
| grounding block | **counted as a failure** | a turn whose answer was withheld must not read clean |

The second has a consequence worth stating: on a blocked turn the delivered text is
Uderia's own withholding notice, so the delivered-text scorers **abstain**
(`platform_authored_answer()`). The turn is then counted as a quality failure on evidence
Uderia deliberately declined to gather — both blocked turns in the sample had zero
failing gate-eligible measurements. Intended, and documented rather than discovered.

### 6.4c The human label — a user's vote as calibration evidence

Every deterministic scorer and every judge is Uderia grading itself. The **one**
signal that comes from outside is the user's per-turn upvote/downvote, and it lands on a
turn that is already fully instrumented — a signed trajectory, per-dimension verdicts,
gate stamps. That makes it the cheapest ground truth Uderia will ever get, which
is why it is recorded here rather than only where it acts.

**Where it enters:** the vote dispatcher's `annotation` consumer
([FEEDBACK_SIGNAL_ARCHITECTURE.md](FEEDBACK_SIGNAL_ARCHITECTURE.md)) writes an
`eval_annotations` row per vote — `user_upvote` / `user_downvote` /
`user_vote_cleared`, suffixed `:propagated` when it arrived by genie propagation so a
second-hand label can be weighted or excluded rather than mistaken for a direct one.

**Append-only.** A flip writes a NEW row; readers take the latest per `(session, turn)`.
The sequence of what the user believed over time is preserved, matching the sink's own
immutability rule.

**`judge_passed` is frozen at vote time** — the ruling the human actually SAW, read from
the turn's span through `eval/sink.py`. Never re-derived: a re-score can flip a verdict
and would silently rewrite what the human was reacting to. Two cases record `NULL`
rather than a pass, because in both there was no ruling on screen:

- the turn was **never scored** (the online hook is backgrounded; a fast voter beats it);
- the turn was **refused at the door** (`eval.gate_only`) and deliberately never measured.

**`eval/sink.py` is THE span reader.** `rest_routes._read_online_spans` delegates to it,
so the scoping and tail semantics the dashboards rely on cannot drift from the ones the
annotation consumer uses to find the same span.

**Reading it back:** `analysis.vote_alignment(spans, votes)` returns
`{upvotes, downvotes, disagreements}`, where a *disagreement* is a turn the scorers
passed and the user downvoted, or one they failed and the user upvoted.
`online_quality_by_agent(..., votes=…)` and `agent_runs(..., votes=…)` attach the
optional keys; **omitted entirely when no votes are supplied**, so every existing caller
is byte-identical. A vote on a refused turn is counted (it is real feedback) while that
turn stays out of the measured denominator exactly as before.

**A vote never enters a measurement** (Rule 3bb.4) — not the pass rate, not a band, not
the TRUSTED composite. It is the calibration *input*, and the disagreement count is its
whole point: it is the only place a human says the thresholds are wrong for this
deployment. Reading it is the intended first step before tuning Config → Trust →
Quality bands, and the same rows are the natural κ pairs for judge calibration (§6.5).

**Judge diagnosis on downvote** is the one consumer that spends tokens, and it adds no
judge path of its own: it calls `evaluate_turn_online(force_judges=True)`, which skips
the sampling decision and **nothing else** — the same timeouts, the same span, the same
Live-Status step and the same canonical sink described in §6.4a. It never overrides a
profile's own judge opt-out (`resolve_judge_prefs` still wins), never runs on a
propagated vote, runs once per turn per process, and ships **off**.

### 6.4d The EPC's OBDA-READ receipt + the strategic_plan verification fix (Aug 2026)

**The consumption receipt.** Every governed data-product consumption seals an
`obda.consumption` step into the turn's EPC: `{product, version, request_hash,
sql_hash, row_count, trust_state}` — the CLOSED `RECEIPT_FIELDS` tuple, canonical
JSON via `obda.receipt_content()`. `provenance.verify_content` re-derives the
receipt from the turn's stamped `obda.operations` with the **same function**, so the
sealed receipt and the displayed record are checked against each other; a receipt
matching no stamped operation is a **named mismatch**, never a silent skip. The
answer capsule renders it as the *Data products* row; "Export receipt" carries it
inside the signed chain, offline-verifiable.

**The strategic_plan verification fix** (found by the O2 live acceptance, and
pre-existing): the `strategic_plan` EPC step seals the **post-refinement** plan
(`executor.meta_plan`, deep-copied to the turn's `original_plan` at the same
moment), but `verify_content` compared against the **pre-rewrite** `raw_llm_plan` —
the two only coincide on turns where every rewrite pass was a no-op, so a genuinely
rewritten plan read as a FALSE "content hash mismatch". The branch now checks the
sealed artifact (`original_plan`) first, keeps `raw_llm_plan` as an any-era
fallback, and a genuinely tampered plan still names its mismatch.

### 6.4a Judge-cost allocation — one canonical sink, all engines (design imperative)

Every judge LLM call in the stack spends real tokens on the user's credentials, so **cost must be allocated consistently at all times** — the same way a normal turn's tokens are. There is exactly **one** sink that does this: `eval/online.py:_record_score_usage(user_uuid, session_id, turn_id, in_tok, out_tok, cost_usd, event_handler)`. It performs, idempotently, the full allocation:

- **cost** via `CostManager`,
- **session totals** (`update_token_count`) + **turn totals** (`update_turn_token_counts`, authoritative for plan reload). Two correctness details: (a) turn tokens are accumulated with `additive=True` so the read-modify-write happens *inside* the session lock — a concurrent background biller (memory extraction fires ~simultaneously on the same turn) can't clobber it; (b) the judge's cost is added via `added_cost_usd` (its accurate per-judge-model price) rather than re-pricing the whole turn at the *session* model's rates — so `turn_cost` stays exactly equal to the sum of the per-event costs even when the judge panel spans different-priced provider families,
- **consumption/billing ledger** (`ConsumptionManager.update_turn_tokens`, in cents),
- a **live KPI `token_update`** — emitted on the request `event_handler` when one is open, and **always** broadcast over the per-user notification channel (`APP_STATE["notification_queues"]`, session-tagged) so backgrounded/REST callers with no open stream still update the live counters. Each judge run gets a **distinct per-run `call_id`** (`turn_score:<turn>:<seq>`), identical across that run's dual delivery (idempotent) but different across runs — so two judge runs on one turn (grounding + online) or a re-score no longer collapse into a single ledger entry. `token_update` carries **absolute** totals (the frontend *sets*, never adds), so double-delivery is a no-op. `notifications.js` renders it **session-guarded** (closes cross-session KPI pollution).

Because the fix lives *in the sink*, every judge-spend site inherits it automatically — there is **no per-engine code**. The three sites that spend judge tokens all converge here:

| Judge-spend site | Routes to the sink via | Engine coverage |
|---|---|---|
| **Online eval** (`evaluate_turn_online`) | direct `_record_score_usage` | all 5 IFOC engines + genie experts — one engine-agnostic seam at `execution_service.run_agent_execution`, *after* any engine (genie or `PlanExecutor`) produced the payload |
| **Grounding gate** (`agent/grounding/runtime.py`) | `_attribute_judge_cost` → `defer_judge_usage` → *(flushed after the turn saves)* → `_record_score_usage` | all 5 — 4 engines via `executor._run_grounding_gate_full` (uniform `session_id`/`turn_id`/`event_handler`), genie directly at `coordinate_engine.py` with the same context |
| **"Score this turn" button** (`POST /v1/evals/score-turn`) | `_record_score_usage` + span cost attrs | any persisted turn, any engine — engine-agnostic by construction (reads the turn via `RunRecord`, whose accessors are defensive across every engine shape) |

> **Grounding-gate deferral (H5).** The grounding judge runs *inline during answer assembly* — **before** the turn is persisted to `workflow_history`. Calling the sink there would find no turn row, so the turn-total billing and the persisted judge step would silently drop while session totals still incremented (breaking `session == Σ turns`). So the grounding path **defers**: `_attribute_judge_cost` stashes the usage via `defer_judge_usage(session, turn, …)`, and `execution_service` calls `flush_deferred_judge_usage(...)` at the post-engine seam once the turn is saved, replaying it through the sink with the turn row present.
>
> **Partial-timeout billing.** The panel's `usage` is summed and billed for **whatever actually spent**, independent of whether the judges completed. A judge timeout/error clears the `include_judges` flag (those verdicts are dropped), but dimensions that finished before the timeout already spent real tokens — the cost summation is no longer gated on `include_judges`, and the grounding path attributes the panel spend in a `finally` so a timeout can't lose it.

The CLI (`eval/cli.py`) is correctly **exempt** — it runs offline with no session to bill.

**Live cross-engine verification (Jul 2026):** a real turn per class was scored with judges on and a reference answer (so `AnswerCorrectnessJudge`, which applies to all classes, fires). The consumption ledger moved by the **exact** judge token count and the reload `turn-score` GET echoed the exact cost for **all five** engines — Ideate `llm_only` (Δ677 = 487+190), Focus `rag_focused` (Δ297 = 237+60), Optimize `tool_enabled` (Δ350 = 256+94), Coordinate `genie` (Δ315 = 250+65), and Conversation via the shared sink. Note: the dimension judges **abstain at zero cost** when their inputs are absent (no reference answer / no retrieved docs / no tool calls) — correct behavior, not a gap. When measuring `score-turn` accounting in isolation, re-score a **settled** turn: a turn's own fire-and-forget tasks (online-eval, memory extraction, next-step recommender) bill *after* `status=complete` and would otherwise race the before/after ledger snapshot.

#### The judge run is a visible, reload-safe Live Status step (transparency imperative)

Consistent allocation is necessary but not sufficient — the cost must also be **visible as a discrete event** whose sum reconciles to the turn total. Cost transparency is a core guarantee of the framework: **the sum of the Live Status panel's per-event costs must equal the turn's total cost.** Before Jul 2026 that invariant was broken for judges — `grounding_gate.to_event()` carries no `cost_usd`, and `turn_score_complete` was a UI overlay that was never persisted — so a judge run inflated `turn_cost` while contributing no cost-bearing event, leaving the panel's event-sum short (the `_getTurnCost` `[Cost Verification]` check would warn).

The same canonical sink therefore also emits **one cost-bearing `judge_evaluation_complete` Live Status step** per judge run (step title *"Agent Quality Judged"*, carrying `input_tokens`/`output_tokens`/`cost_usd`/`judge_models`). It is:

- **broadcast live** over the always-open per-user notification channel (session-guarded in `notifications.js` → `updateStatusWindow`), so it appears the moment the judge runs — covering all three callers uniformly, including the backgrounded online hook and the REST re-score button that have no open request stream;
- **persisted** into the turn's `system_events` via `append_system_events_to_turn`, so it **re-renders identically on a plan reload** and its cost re-sums into the turn total.

There is no double-count: it is the *sole* cost-bearing judge event `_getTurnCost` sees (the grounding-gate verdict event and the `turn_score_complete` overlay carry no cost), and it is emitted only over the notification channel (never also via `event_handler`) so a live stream can't render it twice. Deterministic-only scoring spends 0 tokens → the whole step path is a clean no-op. **Live-verified:** a fresh turn auto-scored by the online hook *and* manually re-scored produced **two** persisted "Agent Quality Judged" steps, and the panel's event-sum reconciled to the backend `turn_cost` exactly (Δ = 0). The Δ = 0 identity holds **for any judge-model pricing** (not only when it matches the session model) because `turn_cost` adds the judge's own per-model cost via `added_cost_usd` rather than re-pricing the turn's token totals at the session rate.

---

## 6a. Grounding gate — synchronous on-path enforcement (Tier 1.1)

> Roadmap: `TIER_1.1_GROUNDING_GATE.md`. Targets A2 (knowledge-grounded agents) · T6 (anti-hallucination grounding) · P2 (AI guardrails).

> **The answer gates as a whole** — the grounding gate's scope and corpus families, the honesty gate, the guardrails, leaked-SQL recovery and the turn-failure answer — are documented in [ANSWER_GATES_ARCHITECTURE.md](ANSWER_GATES_ARCHITECTURE.md). This section keeps the eval-side view: how the gate shares primitives, judges and cost accounting with the scorers.

Everything above *observes*. The grounding gate is the one place the eval substrate **acts on the request path**: before an answer reaches the user, it can **pass · annotate · withhold** the answer for leaving the KG-declared trusted scope. It closes the loop Uderia already owned three-quarters of — *KG enrichment stamps the deployed scope → deterministic scorers detect drift → nothing enforced it* — the documented 230-day failure (the agent got a complete scope-correct enrichment, ignored it, brute-forced 19 calls across unrelated databases, and answered confidently-wrong).

### 6a.1 Module boundary (pure core + thin I/O adapter)

[`agent/grounding/`](../../src/trusted_data_agent/agent/grounding/) — deliberately a sibling of `eval/`, not inside it, because it runs on the request path and must stay dependency-light:

| File | Role |
|---|---|
| `primitives.py` | The **shared** scope/number math (`dbs_touched`, `scope_diff`, `answer_out_of_scope_refs`, `norm_nums`). **Extracted from `eval/scorers/reliability.py`** so the live gate and the post-hoc `scope_adherence` scorer import one definition of "out of scope" (§5.2a). Pure, no runtime deps. `answer_out_of_scope_refs` is **recall-first / allowlist-based** (§6a.3a). |
| `types.py` | `GroundingConfig` (mode/thresholds), `GroundingInputs` (scope **databases + entities**, tool calls, answer, grounding text), `GroundingVerdict` (`decision` ∈ pass/annotate/block, `needs_judge`, `answer_oos_refs`, `numeric_fidelity`, `annotation_md`). |
| `gate.py` | `GroundingGate.evaluate(inputs) → GroundingVerdict` — **pure, deterministic, 0-token**. No I/O, no LLM, no logging. |
| `corpus.py` | The **corpus family** (knowledge lane): `assess()` extracts the answer's checkable claims — numbers (via `norm_nums`), verbatim quotes, identifiers, acronym expansions — and reports those the retrieved chunks do not support; `apply_second_chance()` widens support to the corpus before anything is flagged. Recall only; the `groundedness` judge does precision. Shares the `answer_support` property with `grounded_overlap` (ASSURANCE_SUBSTRATE). |
| `coordinator_figures.py` | Figures a **coordinator** states that nothing it received supports — the question, every expert's answer, every component result (fires on ≥2). Oct 2026, rung 1 of the trust campaign's Coordinate gap: a **pure measure, not yet wired into a gate** — read today by the campaign's `calibrate.py` (`from_turn`). |
| `enforce.py` | `resolve_enforced_verdict(deterministic, judge_passed, reason)` — the **pure** judge-backed-enforce decision (§6a.3), CI-tested without a live model. |
| `adapter.py` | The only module that knows the turn shape: builds `GroundingInputs` from a turn via **`eval.types.RunRecord`** (same normalized view the scorer reads → byte-identical extraction across all engines), resolves config (global flag → profile `groundingConfig` → admin thresholds), and applies the verdict to the answer text / HTML. |
| `runtime.py` | The **engine-agnostic orchestration** `run_grounding(...)` — deterministic gate → (enforce) judge → apply — taking explicit inputs (answer, KG enrichment, profile, tool events, user/session/turn, event_handler) rather than an executor. **One implementation for all five engines** (charter #14): Optimize/Ideate/Focus/Conversation reach it via `executor._run_grounding_gate_full`; **Coordinate/genie** (which has no `PlanExecutor`) calls it directly. |

### 6a.2 Modes + the trusted-scope substrate

Per-profile `groundingConfig.mode`, lowest→highest: `off` → `observe` (default; compute + emit, **never alter the answer** — byte-identical to pre-gate behaviour) → `annotate` (deliver + attach a calm inline note) → `enforce` (withhold a judge-confirmed drift). The gate **abstains (PASS)** whenever no deployable KG scope was declared, so non-KG turns no-op across all engines.

Scope comes from two sources, unioned: the **per-query KG enrichment** (`kg_enrichment_event.scope_databases`) AND an **always-on trusted scope** derived from the profile's bound KG (`get_trusted_scope`, §3) — so the gate is never blind on an out-of-domain query that triggered no enrichment. The always-on union is applied in **one shared place** — `grounding/adapter.py:ensure_trusted_scope`, which `runtime.run_grounding` calls before building inputs — so it reaches **all five engines** identically, including **Coordinate/genie** (whose coordinator never pre-stamps scope and would otherwise carry `None` on an out-of-domain query, leaving the gate blind to the exact drift it exists to catch). `executor._apply_trusted_scope` delegates to the same helper, so the live KG-display event and the gate's scope can never diverge. A **discovery-KG fallback** derives scope from the logical `database` root node when no physical layer resolves (`_derive_scope_databases`), so logical-only discovery KGs are protected too — without ever widening an industry-model KG's precise physical scope.

### 6a.3 Judge-backed enforce (the post-benchmark architecture)

The first benchmark run (§6b) **disproved** a deterministic-block gate: blocking on an out-of-scope *tool call* over-blocks honest refusals (false positives) and **misses answer-drift** that made no out-of-scope tool call (the oos08 governance-leak class — false negatives). The `scope_faithfulness` judge got every disagreement case right and never punished an honest "not in scope". So enforcement is **tiered** — a cheap deterministic trigger gates an LLM-judge decision (a research-validated guardrail pattern):

1. **The deterministic gate never blocks.** In `enforce`, on a trigger — an out-of-scope **tool call** OR an out-of-scope identifier surfaced in the **answer** (`answer_oos_refs`) — it returns `PASS` with `needs_judge=True`. Numbers are observe-only (never trigger; numeric mismatch false-positives on correctly-formatted in-scope answers).
2. **The `scope_faithfulness` judge makes the real call** (`executor._enforce_grounding_with_judge`): **drifted → BLOCK** (withhold) · **faithful → PASS** (intact — this is how honest refusals survive) · **abstain / unavailable / timeout → ANNOTATE** (fail-safe: keep the answer, attach the note — never withhold on uncertainty, never break a turn).

`observe`/`annotate`/`off` never reach the judge → **zero added latency/cost by default**; only the opt-in `enforce` path pays a judge call, and only when the trigger fires. The judge call is bounded by `ONLINE_EVAL_TIMEOUT_SECONDS` and its token cost is routed through `CostManager` exactly like the online-eval judges (`_attribute_judge_cost` → `_record_score_usage`: session/turn totals + an idempotent `token_update` + consumption).

### 6a.3a Recall-first answer trigger (allowlist, not blocklist)

In the tiered model the trigger's job is **recall** (don't miss drift → don't starve the judge) and the judge's is **precision** (clear false alarms). A *false* trigger costs one ~$0.0006 judge call, never a wrong verdict; a *missed* trigger delivers drift unflagged. So `answer_out_of_scope_refs` is tuned for recall, **allowlist-based** rather than matching known out-of-scope phrasings (which proved a leaky regex arms race — the live benchmark kept surfacing new prose forms: `restaurants_mvm_test database`, `'val' database`, `schema is 'X'`, `'vendor_agreements' table`):

1. **Extract every identifier that is *structurally* a data reference** — a qualified `<db>.<table>`, a **quoted** identifier next to a container word (`database`/`schema`/`catalog`/`table`, either order + a short copula), or a **technical** (snake_case / has-a-digit) identifier next to one. The structural requirement is all that keeps plain English ("the database") from becoming a candidate.
2. **Flag any candidate not in the allowlist** = `scope_databases ∪ scope_entities ∪ SYSTEM_DBS`. `scope_entities` is the KG's in-scope concept/table names (`handler._derive_scope_entities`, already stamped on the enrichment; read via `RunRecord.kg_scope_entities()` → `GroundingInputs.scope_entities`).

So an in-scope table named in prose is **recognized** and stays silent, while an **unrecognized** container reference trips the judge — even in a phrasing no blocklist anticipated. It is **self-maintaining** (as the KG grows, the allowlist grows → fewer false triggers) and degrades cleanly to database-only when `scope_entities` is empty. Quoting is structural evidence on its own, so it catches short non-technical fabricated names (`'val'`) the earlier technical-only filter missed.

### 6a.4 All-engine wiring, observability, audit

Charter rule: **functionality must support all five engines and must not change baseline behaviour.** One shared async entrypoint — `executor._run_grounding_gate_full(answer_text, trace_extra)` — runs the deterministic gate and, only when it set `needs_judge`, the judge step. Every engine seam calls it before its `final_answer` yield (you cannot withhold an answer after it has streamed): Optimize + Coordinate through the shared final-answer path, Ideate / Focus / Conversation through their own seams. Because the default is `observe`, a non-opted-in profile is **byte-identical** to pre-gate behaviour.

Each run emits a `grounding_gate` Live-Status SSE event and stamps the verdict onto the persisted turn (`turn_summary["grounding"]`) for plan-reload/audit. The Live-Status card (`ui.js:_renderGroundingGateDetails`) surfaces the verdict, declared scope, out-of-scope databases (from tool calls) **and** the recall-first answer references (`answer_oos_refs`), plus — in enforce — the **scope-judge adjudication** (drifted → withheld · faithful → delivered · unavailable → annotated). It renders **live** (via the SSE event) and **on reload** (`handleReloadPlanClick` synthesizes a `grounding_gate` event from the persisted `turnData.grounding` into the list the turn's reload renderer replays — `system_events` for standard turns, `genie_events` for genie — both of which dispatch `grounding_gate`). The whole path **fails open**: any gate error returns the original answer untouched. A confirmed BLOCK records the withheld-claim action in the Ed25519 provenance chain. **All five engines** stamp + reload, including Coordinate/genie — which runs the same `grounding.runtime.run_grounding`, stamps `turn_data["grounding"]`, and emits the live event over the notification channel (it has no per-request `/ask_stream` status yield).

### 6a.5 Governance (admin)

No card of its own — the master `GROUNDING_GATE_ENABLED` toggle sits in **Administration → App Config → Trust → Enforcement** (*Grounding Enforcement*) (folded into the `agent_eval` block, restart-persisted via `_sync_agent_eval_to_config`) and the **same** `max_out_of_scope_dbs` / `min_numeric_fidelity` sliders that band the eval scorers now also feed the gate. Per-profile `mode` is set in the **profile editor → Agent posture → Grounding** (`groundingConfig`): an Observe / Annotate / Enforce selector + an Allow-out-of-scope toggle (`configurationHandler.js` load/save). Eval *observes*, the gate *enforces* — two halves of one grounding story sharing one set of thresholds.

---

## 6b. Anti-hallucination benchmark — the published proof (Tier 1.4)

> Roadmap: `TIER_1.4_BENCHMARK.md`. The measured number is what flips A2/T6 from "built" to a defensible GREEN.

The benchmark turns the grounding system into a **published, reproducible proof**. [`eval/benchmarks/grounding/`](../../src/trusted_data_agent/eval/benchmarks/grounding/):

| File | Role |
|---|---|
| `report.py` | **Pure** scoring + aggregation core (CI-pure, 0-token): confusion matrix + headline numbers + markdown/JSON. The single source of truth both the admin UI/API and the published report render from. |
| `dataset.py` + `dataset/fitness_db.json` | 30-case dataset (12 in-scope · 12 out-of-scope · 6 ambiguous) + loader/validator. |
| `harness.py` | The **live** (not CI-pure) admin/operator harness: drive each case once in `observe`, capture the baseline answer + the gate's trigger signal, label ground truth via the `scope_faithfulness` judge (`/v1/evals/score-turn`), aggregate. |

**What it measures (post-rearchitecture framing).** Since enforce is now the `scope_faithfulness` judge gated by a deterministic trigger, the benchmark scores the **trigger's recall of judge-confirmed drift** — not a "counterfactual block". A **single observe run** yields, in one pass: the baseline answer + the trigger signal (`needs_judge`, recomputed CI-pure by `trigger_decision`, which is **pinned to the live gate's `needs_judge`** by a unit test) + the judge ground-truth label. The confusion matrix then reads:

| | judge: drifted | judge: faithful |
|---|---|---|
| **trigger FIRES → judge adjudicates** | TP | FP (extra judge call — never a wrong verdict) |
| **trigger PASSES → delivered as-is** | FN (the honest gap — drift the cheap trigger missed) | TN |

Headline: **trigger recall** (of all judge-confirmed drifts, what share reaches the judge to be caught), **trigger precision** (judge-call efficiency), baseline drift rate, and the net drop in unadjudicated drift reaching the user (observe → judge-backed enforce). Honest by construction (charter): FN and FP are published, not hidden — the answer-reference trigger (`answer_oos_refs`) exists precisely to lift recall on the oos08 class the tool-call signal missed.

**Surfaces.** Today the benchmark is a **CLI / operator harness** (`python -m trusted_data_agent.eval.benchmarks.grounding.harness …`, admin credentials, never a consumer surface) that drives a running server and writes the externally-publishable `REPORT_<kg>_<date>.md` (matrix · headline · per-case detail) — the A2/T6 GREEN proof. Because the harness renders from the same pure `report.py` core that a view in the runtime would, the two can never disagree. **Deferred (not yet built):** a `POST /v1/evals/grounding-benchmark/run` API + a **Grounding Benchmark** admin panel (matrix/headline/trend/cost, run-from-UI) — a tracked enhancement so the proof has a home in the runtime alongside Agent Performance.

**The corpus twin — [`eval/benchmarks/corpus_grounding/`](../../src/trusted_data_agent/eval/benchmarks/corpus_grounding/).** The same shape for the knowledge lane's corpus trigger (`agent/grounding/corpus.py`): an 18-case dataset (`dataset/corpus_v1.json`) in three classes — `supported` (must not fire), `unsupported` (must fire), `second_chance` (unsupported on the first retrieval, cleared by the second-chance search). Because the corpus trigger is deterministic given (answer, chunks, question), this benchmark needs **no judge and no tokens** — it is CI-pure and reproducible offline. Headline: recall, false-positive rate, second-chance recovery, net precision. Gates: `test_corpus_grounding.py`, `test_corpus_grounding_benchmark.py`.

---

## 6c. The closed trust loop — behavioral trust, circuit breaker, policy backtest (Jul 2026)

Three sibling modules turn the eval stream from *reporting* into a **closed
loop**: measured conduct changes trust state, trust state can change
enforcement, and any policy change can be rehearsed against sealed history
first. All pure/deterministic/0-token; all default-off or read-only.

### 6c.1 Behavioral Trust Score — `eval/trust_behavior.py`

Fuses **Trust-Membrane integrity** (signed/changed/unsigned — resolved
defensively per profile via `core.trust.composition.resolve_profile_trust`, so
the removable Ontology Suite's absence degrades to *unknown*, never an error)
with the **live track record** read from the same online quality spans
`analysis.py` consumes. Per agent:

- **Conduct score** = recency-weighted gating pass rate (half-life
  `half_life_days`, window `window_turns`) blended with a trust-critical
  dimension rate (the `trust_dims` list in `DEFAULTS` — six today:
  `scope_adherence`, `answer_groundedness`, `governed_data_ratio`,
  `no_tool_loops`, `output_sanity`, `content_safety`; weight
  `trust_dim_weight`, abstain-safe). The Assurance Guide reads that list
  from `DEFAULTS` (`assurance_points.POINT_WEIGHTS` → the entry's `weights`
  row; each check answers `weighs on`), so the Guide cannot name a different
  set than the blend uses — this paragraph once listed five.
- **State ladder** (volume-gated + stale-decayed):
  `provisional` (< `min_turns_proven` evidence) · `at_risk` (< `at_risk_floor`)
  · `watch` (< `healthy_floor`) · `proven` · `trusted`
  (≥ `trusted_floor` **and** ≥ `min_turns_trusted`; evidence older than
  `stale_days` caps trusted → proven).
- **Composite** = behavior capped by integrity — a signature never promotes bad
  conduct; `unsigned` caps at proven, `deployment_unattested` caps at proven (the
  fourth Trust-Membrane state — a deployment never attested here must not out-score
  a portable-signed agent; without a cap it could reach `trusted`, inverting the
  ladder), `changed` caps at watch, unknown applies no cap but is reported honestly
  (`compose()`, `_INTEGRITY_CAP`).

Surfaces: `GET /v1/evals/agents/trust-behavior` (scope-gated), the roster's
**seal + behavior ring** glyph and the **Trust Posture** matrix
(`agentPerformanceHandler.js`) — all gated on the admin `trust_behavior.enabled`
flag (default **false** → UI byte-identical). System scope keys cells **`tag::uuid`**
(`assess_agents(per_user=True)`, mirroring `online_quality_by_agent`) so two users'
same-tag agents never merge, and the requester's `integrity_by_tag` caps only the
requester's own cells; the response carries `breaker_mode` and `self_uuid`, and the
frontend resolves both key shapes through one `_tbAgent()` helper. Config block
persisted via `system_settings` (`_sync_trust_behavior_to_config`). CI:
`test/test_trust_behavior.py` (56).

### 6c.2 Trust-budget circuit breaker — `eval/online.py:_run_trust_breaker`

Opt-in closed-loop response (`trust_behavior.breaker_mode`:
`off` default · `notify` · `freeze_autonomy`). Runs at the END of
`evaluate_turn_online` (after span export so the turn counts toward the trailing
record), on the **transition edge** into `at_risk` only (per process lifetime —
one alert per bad streak, not one per turn). The pure decision is
`trust_behavior.breaker_decision()` — trips exactly on the volume-gated
`at_risk` state, so a new agent's few bad turns can never trip it. Actions:
broadcast a `trust_breaker` notification (rendered as a toast,
`notifications.js`), and in `freeze_autonomy` set the profile's
`authorityConfig.autonomous=False` + `frozen_by_breaker` + `frozen_reason` —
suspending **unattended** use only (interactive use untouched; visible and
reversible in the Agent Authority card). Fully defensive: any failure degrades
to a log line, never touches the turn.

### 6c.3 Policy backtesting — `eval/backtest.py`

Replays a **proposed** trust policy over already-persisted `workflow_history`
turns: guardrail projections are **exact** (the same
`guardrails.policy.evaluate_inbound/evaluate_outbound` engine on the stored
query/answer text); grounding projections are **trigger-level** from the
recorded gate stamp (`needs_judge` / `out_of_scope_dbs` / `answer_oos_refs`) —
the judge is an LLM and is honestly **not** simulated, so a flagged turn reads
`would_escalate_judge`, never a certain block; no stamp → abstain. Endpoint
`POST /v1/evals/backtest` (bounded sessions/turns, optional `profile_tag`
filter); samples are capped and never carry raw PII. UI: the **Policy backtest**
panel inside Agents → Trust Posture. CI: `test/test_backtest.py` (34).

Related same-wave hardening outside `eval/`: the **memory firewall**
(`components/builtin/memory/store.py:screen_memory_content` — injection-bearing
memory writes stored quarantined), the **retrieval firewall**
(`knowledge_context` CW module — injection-bearing retrieved chunks defused with
an untrusted-data label), the **tool-result firewall**
(`agent/guardrails/runtime.py:scan_tool_result` — poisoned tool outputs flagged
observe-only at the `invoke_mcp_tool` seam), and **trust-aware routing**
(`coordinate_engine.py`: `genieConfig.requireTrustedExperts` routes only to 🟢
experts, fail-closed per expert). Operator narrative:
[AGENT_TRUST_AND_ASSURANCE.md §2.11](../manuals/AGENT_TRUST_AND_ASSURANCE.md).

### 6c.4 Assurance Inbox + HITL review + live red-team

The remaining closed-loop internals — the trust actions become *worked items*,
and enforcement can defer to a human.

- **Trust events / Assurance Inbox** — `core/trust_events.py` (NOT part of the
  removable Ontology Suite; it covers guardrail/breaker events that exist without
  the trust membrane): a self-applying `trust_events` table for **stored**
  out-of-band incidents (breaker trips, memory quarantines, retrieval defuses,
  tool-result flags, live-redteam failures) recorded fail-open by their emit
  sites, plus **pure derivation** (`derive_turn_events`) of incidents already on
  the turn (grounding blocks, guardrail actions, honesty rewrites) and a
  multi-turn `derive_session_sentinel`. `broadcast_notice()` pushes a live
  `trust_event_notice` (banner + in-place inbox refresh); loop-guarded so
  sync/worker emit sites no-op cleanly. REST `GET /v1/assurance/inbox`,
  `.../inbox/<id>/ack`. CI: `test/test_trust_events.py` (29).
- **HITL review (`review` mode)** — not a fourth strictness level but a
  **property of enforcement** (the editor renders it as Enforce + "keep withheld
  originals for my review"; `annotate` is likewise Observe + "disclose findings"):
  withhold/redact exactly like `enforce`, but park the ORIGINAL as OWED work in
  `core/review_queue.py` (self-applying `review_items`, owner-scoped, content only
  on `include_content=true`). Distinct from the **recovery ledger**: plain
  `enforce` retains a FULL withholding's original as a `retained` entry —
  recoverable, dispositionable, but never owed, never counted in `pending`, no
  oversight claimed (`APP_CONFIG.REVIEW_RETAIN_ENFORCED_BLOCKS`, default on;
  routine PII redactions never auto-park; queue bounded: 90-day TTL on
  settled+retained, per-user retained cap, pending never pruned). `review` is a
  runtime-level composite detected from the RAW profile mode (config
  normalization would silently degrade the unknown mode to observe — the exact
  trap, guarded), with agent-level inheritance (`postureConfig.mode` via
  `core/posture_modes.effective_posture`, the ONE resolver — also stamped as the
  computed `posture` field on `GET /v1/profiles`), which substitutes `enforce`
  for config resolution then parks + re-tags `mode:"review"`.
  Approve/uphold via `POST /v1/assurance/reviews/<id>/approve|reject`; release is
  **append-only** (delivered history never rewritten — the approved original
  surfaces in the answer capsule). A `review_decision` audit event is appended to
  the turn (live + reload-safe).
  **Release-to-conversation (opt-in per item):** approve accepts
  `{"release_to_conversation": true}` (default false = record only) — the owner
  EXPLICITLY releases the withheld original into the conversation, forward-only:
  (a) the `review_release` context module (12th builtin CW module, all IFOC
  classes via the registry-default merge, self-gating to empty) injects the
  released originals into subsequent turns' context under an explicit
  "[Owner-released after human review]" label with the release turns surfaced in
  the CW snapshot metadata; (b) the exact PII values of released originals join a
  **session-scoped outbound guardrail allowlist**
  (`guardrails/runtime._released_allowlist` → `policy.evaluate_outbound(...,
  allowed_values=)`) so the agent can STATE what the owner released without
  re-parking a new review item — exact values only, never category-level,
  toxicity never allowlisted, consulted only when a PII finding actually fired
  (clean-turn hot path untouched); (c) the choice is recorded as a
  `review_release` trust event and echoed as `released_to_context` on the item,
  the audit event, and the notification. The sealed turn itself is never
  rewritten. CI: `test/test_review_queue.py` (66).
- **Deterministic red-team self-test** — `eval/redteam.py` runs a curated attack
  corpus through the real 0-token detectors (injection/PII/toxicity) → per-
  category recall + benign false-positive rate. `POST /v1/assurance/resilience-test`.
  CI: `test/test_redteam.py` (11).
- **Live red-team** — `eval/live_redteam.py` (pure corpus + deterministic scorers)
  drives REAL adversarial turns through a target profile via the internal self-
  call path and scores whether the guards held (injection obeyed / PII echoed /
  scope drift). Opt-in + hard-capped (token cost); scoring is reproducible + free.
  `POST /v1/assurance/live-redteam`; a failure lands in the Assurance Inbox.
  CI: `test/test_live_redteam.py` (23).
- **Signed audit pack** — `POST /v1/assurance/audit-pack` assembles posture +
  eval aggregates + incidents + resilience test + an EU AI Act/NIST/GDPR control
  map, Ed25519-signed with the deployment provenance key (offline-verifiable).
- **Trust history** — `GET /v1/assurance/trust-history/<tag>` merges an agent's
  attestation + incidents + review decisions into an audit-facing changelog
  (Trust History section in the roster detail).

### 6c.5 Trust visualisation (R1–R7)

Pure rendering over persisted data, no backend behaviour change: the **scope
perimeter + drift overlay** (KG inspector `dispatchKGAnimation` `grounding_gate`
branch — scope as a dashed boundary, out-of-scope refs crossing it in red); the
**provenance chain drawn as a chain** with inline offline verify (answer capsule);
**per-node trust glyphs** in the coordination trace tree; the **judged-vs-
deterministic** chip border (dashed = a model opined) + **per-dimension micro-
sparklines** (`online_quality_by_agent.dimension_series`). The **`content_safety`
judge** (`eval/scorers/judges.py`) is the semantic complement to the deterministic
guardrail (all classes, advisory) — see [AGENT_QUALITY_DIMENSIONS.md](AGENT_QUALITY_DIMENSIONS.md).

---

## 7. Analysis layer

[`eval/analysis.py`](../../src/trusted_data_agent/eval/analysis.py) — pure functions consumed by both the dashboard and the CLI:

| Function | Output |
|---|---|
| `cluster_failures(scores)` | Groups failing reps by their **set of failing dimensions** → recurring failure shapes (signature, count, example cases), worst-first. |
| `cluster_online_failures(spans)` | Same clustering over OTLP spans. |
| `pass_rate_by_dimension` / `online_pass_rate_by_dimension` | Per-dimension pass rates (dashboard bars). |
| `online_quality_by_agent(spans)` | Per-profile-agent: turns, failures, pass rate, recency sparkline series, **per-dimension** pass rates (`dimensions`) **and per-dimension decided-turn counts (`dimension_counts`)** — drives the profile cards. The counts are the aggregate-bar denominator: deterministic dims decide every turn, judge dims only on sampled turns, so the roster shows an `n=` chip (amber + dimmed when `n≤2`) so a 1-of-1 judge verdict isn't read as a systemic failure. **Excludes `agent.kind=='flow'` spans.** |
| `online_quality_by_flow(spans)` | The flow-agent analogue, keyed on `agent.flow_id` — drives the flow cards (§12a). Also emits `dimension_counts`. |
| `diff_runs(baseline, candidate)` | pass^k delta + per-dimension/cohort/class deltas split into **regressions** and **improvements** — experiment diffing. |
| `agent_kpi_dashboard(spans)` | Portfolio-wide KPI roll-up: `overall`/`recent` pass-rate trend, top `failing_checks` (by fail rate), and a per-`agents` scoreboard — drives the cross-agent KPI charts (§11.1). |
| `agent_timeline(spans, tag, bucket_seconds=86400)` | Day-bucketed `pass_rate`/`avg_cost`/`avg_latency` series for one agent over time + a recent series — drives the per-agent timeline card (§11.1). |
| `agent_runs(spans, tag)` | Every scored run for one agent (newest first) with its **full per-dimension verdicts** — drives the Runs Ledger (§11.1). A coordinator run carries its expert children as a nested **master/slave tree** (each expert matched to its own scored span by `slave_session_id`, with timing for the waterfall; a best-effort synthetic node when an expert wasn't scored). Self-cost per node; `rollup_*` are display-only. |
| `online_quality_by_agent` (extended) | Now also returns per-agent **`avg_cost`/`avg_latency_ms`/`avg_tokens`/`avg_tasks`** (the comparison metric picker) and **`direct_turns`/`expert_turns`** (invocation split for the roster "N as expert" note). |

SME annotations (agree/disagree with a verdict) persist to `eval_annotations` and feed judge calibration.

---

## 8. Persistence

[`eval/store.py`](../../src/trusted_data_agent/eval/store.py) — `EvalStore` (SQLite, content-hashed + versioned datasets):

| Table | Holds |
|---|---|
| `eval_datasets` | id, name, version, content_hash, is_holdout |
| `eval_cases` | query, profile_class/tag/id, cohort, reference_answer, assertions, source_turn_ref |
| `eval_runs` | dataset_id/version, k, baseline_run_id, summary (JSON) |
| `eval_scores` | run_id, case_id, rep_index, dimension, kind, passed, score, threshold, explanation, evidence |
| `eval_annotations` | case_id/session_id/turn, reviewer, label, note |

The **online sink** is a separate NDJSON file (`ONLINE_EVAL_SINK`, default `tda_eval/online_eval.jsonl`) — the durable stream the regression dashboard tails. Offline (curated, gated) and online (live, sampled) are cleanly separated. Local store files (`tda_eval.db*`, `tda_eval/`) are gitignored.

---

## 9. REST API surface

All under `/api/v1`, JWT-auth, in [`api/rest_routes.py`](../../src/trusted_data_agent/api/rest_routes.py).

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/evals/datasets` | List / create datasets |
| GET/POST | `/evals/datasets/<id>/cases` | List / add cases |
| POST | `/evals/datasets/<id>/mine` | Seed cases from production traces |
| GET | `/evals/runs` | List runs (newest first). **Offline EvalStore reads (runs/scores/clusters/diff) + annotations require `VIEW_ALL_SESSIONS`** — the store is instance-global (no user column; CLI-written) and annotations feed judge calibration |
| GET | `/evals/runs/<run_id>` | Run summary |
| GET | `/evals/runs/<run_id>/scores` | Per-rep scores |
| GET | `/evals/runs/<run_id>/clusters` | Failure-mode clusters |
| GET | `/evals/diff?baseline=&candidate=` | Experiment diff |
| POST | `/evals/gate` | Regression gate (409 if it fails) |
| POST | `/evals/score-turn` | Ad-hoc score a session turn (deterministic; `include_judges` for live panel) — powers the manual "Score this turn". Persists the result + returns `judges_available` |
| GET | `/evals/turn-score?session_id=&turn=` | Read-only: latest **persisted** verdicts + cost for a turn (no re-score, no tokens) — auto-displays a turn's existing score on open |
| GET | `/evals/online` | Tail the raw quality spans — scoped to the caller; `?scope=system` behind `VIEW_ALL_SESSIONS` (spans carry session/turn ids + judge evidence) |
| GET | `/evals/online/dashboard?scope=my\|system` | Aggregated regression view (scope-gated); returns `by_agent` (profile-agents), `by_flow` (flow-agents), `by_profile_class`, clusters, dimension rates |
| GET | `/evals/agents/kpi-dashboard?scope=my\|system` | Portfolio KPI roll-up (`agent_kpi_dashboard`) — trend, failing checks, scoreboard, cost/latency — for the cross-agent charts |
| GET | `/evals/agents/<tag>/timeline?scope=&bucket=` | Day-bucketed pass-rate / cost / latency series for one agent (`agent_timeline`) |
| GET | `/evals/agents/<tag>/runs?scope=&runs=` | Every scored run for one agent + full verdicts + nested master/slave tree (`agent_runs`) — the Runs Ledger |
| GET/POST | `/evals/annotations` | SME annotation queue (feeds calibration; `dimension` + `judge_passed` on the POST freeze the κ pair) |
| GET | `/evals/judge-calibration` | Per-dimension judge↔human κ report over the annotations (status: calibrated / below_floor / insufficient) |
| GET | `/evals/judge-queue` | Recent judge rulings (from the online spans, registry-JUDGE dims only) awaiting a human agree/disagree — the κ work queue |
| POST | `/evals/backtest` | Policy backtest — replay a proposed grounding/guardrail policy over persisted turns (§6c.3; deterministic, 0-token) |
| GET | `/evals/agents/trust-behavior?scope=` | Behavioral Trust Score per agent — integrity × track-record fusion (§6c.1) |
| GET | `/evals/scoring-thresholds` | The live scoring bands as the Quality Dimensions Guide reads them (any authenticated reader) |
| GET | `/evals/library-outcomes?tag=&scope=` | Read → ran per skill-library section across scored turns (`library_read_first`; SKILL_ARCHITECTURE §4a) |

**Assurance surface (§6c.4):**

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/assurance/inbox` (+ `POST /<id>/ack`, `GET /count`) | Trust incidents — stored (breaker/quarantine/defuse/tool-result/redteam) + derived (grounding/guardrail/honesty/sentinel) |
| POST | `/v1/assurance/inbox/derived/ack` | Acknowledge a DERIVED observation or the session sentinel — a stored disposition keyed `(session_id, turn_id, event_type)`; the sealed trace is never rewritten |
| GET / POST | `/v1/assurance/reviews` · `/<id>/approve\|reject` | HITL review queue + disposition (append-only release; approve accepts `{release_to_conversation}` for forward-only release into context) |
| DELETE | `/v1/assurance/reviews/<id>` | The owner withdraws a PENDING item of their own (403 for a designated approver, 409 once decided) |
| POST | `/v1/assurance/resilience-test` | Deterministic red-team self-test (in-path detector recall, 0 tokens) |
| POST | `/v1/assurance/live-redteam` | Live red-team — real probe turns (opt-in, budget-capped) |
| POST | `/v1/assurance/audit-pack` | Signed, offline-verifiable evidence bundle (posture + eval + incidents + control map) |
| GET | `/v1/assurance/trust-history/<tag>` | Per-agent trust changelog (sign-offs + incidents + review decisions) |
| GET | `/v1/assurance/agent-story/<tag>` | The agent's trust story assembled from existing records (profile, corpus scans, attestation, spans, reviews, incidents) + loop-closure receipts; owner-scoped |
| GET / POST | `/v1/assurance/trust-trip/<tag>` · `/reset` | The trip logbook (append-only leg markers) and starting a new leg |
| POST | `/v1/assurance/retrieval-anomaly` | Index-poisoning detection over recent turns (retrieval frequency × query diversity) → an Inbox item for human disposition, never auto-quarantine |
| GET | `/v1/assurance/vocabulary` | The registry served as Guide entries — stations, mechanisms, properties, moments, terms ([ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md) §6c) |

The binding studio's routes (`/v1/assurance/bindings`, `/modules`, `/controls/<key>/posture`) are listed in [ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](ASSURANCE_MODULE_PLANE_ARCHITECTURE.md) §7.

The admin `agent_eval` block (online toggle, judges, judge rate, **and the banded `default_thresholds`**) plus the `trust_behavior` block round-trip through `GET/POST /v1/admin/app-config` (§6.4, §15).

**Scope gating:** `scope=system` requires the `VIEW_ALL_SESSIONS` feature (enforced server-side, 403 otherwise); `scope=my` filters spans to the requesting user via the `user.uuid` span attribute. The flow roster additionally reads `GET /v1/flows` for flow identity + composed profiles.

---

## 10. CLI

[`eval/cli.py`](../../src/trusted_data_agent/eval/cli.py) — "evals as unit tests":

```
python -m trusted_data_agent.eval.cli list
python -m trusted_data_agent.eval.cli mine   --dataset core
python -m trusted_data_agent.eval.cli run    --dataset core --k 3 [--judges --judge-models 3]
python -m trusted_data_agent.eval.cli gate   --run <id> --baseline <id> --min-pass-k 0.9 --cohort risky=0.95
python -m trusted_data_agent.eval.cli report --run <id>
```

`gate` returns a **non-zero exit code** on failure — wire it straight into CI.

---

## 11. Frontend / UX

### 11.1 Agent Performance view

[`static/js/handlers/agentPerformanceHandler.js`](../../static/js/handlers/agentPerformanceHandler.js) — `window.agentPerformance.load()`, mounted by the `agents-view` switch hook in `ui.js`. Matches the command-center design language (gradient hero, `rounded-xl` metric/section cards, `var(--card-bg)`/`var(--border-primary)`), fully theme-aware, monochrome inline SVG icons, no emoji.

**4-class health taxonomy (single source of truth).** Aggregate health — every roster pill, KPI counter, metric bar, and chart — uses one banded scale: 🟢 **Healthy** (pass rate ≥ 0.80) · 🟡 **Review** (0.50–0.79, "Evaluation Recommended") · 🔴 **Danger** (< 0.50) · ⚪ **Inactive** (no data). `HEALTH` map + `healthClassFor()` in `agentPerformanceHandler.js` are the canonical constants (`HEALTHY=0.8`, `REVIEW=0.5`); `agentKpiCharts.healthColor()` mirrors them (cross-ref comment locks the ramp). **Atomic surfaces stay 2/3-state by design** — a single binary verdict chip is pass/fail/abstain, per-turn spark bars are pass/fail, agree/disagree diff pills are binary; only rolled-up health is 4-class. The per-metric 3-band model (§5.1) reuses the same colors at the metric level on the Quality card.

**Agent-centric, not run-centric:**

- **Cross-agent KPI charts** ([`static/js/handlers/agentKpiCharts.js`](../../static/js/handlers/agentKpiCharts.js)) — dependency-free inline-SVG 2×2 grid above the roster: pass-rate **trend**, top **failing checks**, agent **scoreboard**, and **cost/latency**, all banded by `healthColor`. Sourced from `GET /evals/agents/kpi-dashboard`.
- **Portfolio KPIs** — Agents Evaluated / Healthy / Needs Attention (= Review + Danger) / Live Turns Scored / Gating Failures (profile-agents + flow-agents both count).
- **Agent health roster** — one card per agent of **either type**: profile-agents are IFOC-colored (Ideate=green, Focus=blue, Optimize=purple, Coordinate=orange); **flow-agents** get an amber **"Flow"** badge, show their name (no `@`), a **"uses @X · @Y"** line of composed profiles (or "no fixed profile"), and "flow run" units. Each card has a headline pass rate (live, else offline pass^k), pass/fail micro-sparkline, a **4-class status pill** (Healthy/Review/Danger/Inactive), and **honest "unevaluated" empty states** (never fakes numbers); sorted worst-health-first. Card → agent detail.
  - **Filter pills** (`All · Ideate · Focus · Optimize · Coordinate · Flows`, only families present) + **search** (tag/name) + live count; client-side, instant, focus-preserving. The roster grid is its own scroll area (`max-height: 52vh`) so Experiments and Annotations stay reachable.
- **Agent detail** — live vs offline quality, per-dimension bars, an offline-cohort pass^k table (banded), a **per-agent timeline card** (`renderAgentTimeline` → `GET /evals/agents/<tag>/timeline`), and the **Runs Ledger** + **Coordination Trace Tree** (below). Flow detail shows the flow scorers' dimensions.
- **Runs Ledger** (`loadRunsLedger` → `GET /evals/agents/<tag>/runs`) — every scored run newest-first. Each row: band dot + relative time + invocation chip + a compact **dimension band-strip** (one tiny square per verdict, green/amber/red — scan many runs and spot a regression at a glance) + cost/latency; expands to the full tier-grouped verdict chips + an **"open in Sessions"** deep-link (`openSessionTrace` → switches to the chat lens, loads the session, opens the turn's trajectory + Quality overlay). Filter pills: band (healthy/review/danger) + invocation (direct / as-expert).
- **Coordination Trace Tree + waterfall** (`traceTree`/`waterfall`) — a coordinator run expands into a Jaeger/LangSmith-style tree: root coordinator node → indented expert child nodes (connector rails), each band-colored with its **own** verdicts/cost/duration; an inline **timing waterfall** positions experts by real `started_at`/`completed_at` (honest parallel-vs-sequential detection; sequential layout when timing is absent) and shows the rolled-up turn total. This is the concrete master/slave visualization (§12b).
- **Compare** (`renderCompare`) — roster "⇄ Compare agents" toggle → per-card selection checkboxes → sticky "Compare →" bar → a panel with a **metric picker** (pass_rate / avg_tasks / avg_tokens / avg_cost / avg_latency / turns) rendering side-by-side grouped bars (best ★) + a **quality heatmap** (agents × dimensions, 4-class band cells). Driven entirely by the extended `online_quality_by_agent` cells.
- **Experiments & Datasets** — runs table → run detail (clusters + per-class/cohort/dimension) + experiment diff.

All three are dependency-free inline SVG/HTML with a one-time injected theme-aware stylesheet (`#agent-perf-ux-css`, `.ap-*`): CSS variables, monochrome icons, `prefers-reduced-motion`, hover elevation, fade-in, animated bars, focus-visible rings.
- **Judge Calibration & Annotation Queue** — agree/disagree to calibrate judges.

IFOC mapping is mandatory (never show raw `tool_enabled` etc.).

### 11.2 My vs System scoping

Mirrors the Awareness panel: `#agent-tab-system` is hidden and revealed only for admins (`/auth/me/features` → `view_all_sessions`). `scope=my` (default) filters to the requesting user's own agents; `scope=system` (admin) aggregates all users (server-enforced). In system scope the roster surfaces every profile-agent with activity (from `by_agent`), not just the viewer's profiles. Flow-agents come from `GET /v1/flows` (the viewer's flows) merged with `by_flow` online quality.

### 11.3 The Awareness panel + the trajectory verdict overlay (the bridge)

The turn's **Scorecard** ([`ui.js::injectTrajectoryEvalOverlay`](../../static/js/ui.js)) carries the verdict chips — the same verdicts the Agents lens aggregates. It is **adopted into the Trust Circle's JUDGED station panel** (`AssuranceDial.placeScorecard`): JUDGED is where measurements are counted, so its reading and the checks behind it sit in one place instead of two surfaces answering the same question. Adoption runs from whichever side renders second (the dial re-seats it after its own innerHTML wipe, the same rescue/re-seat pattern as the control cluster); with no dial present the card renders in the trace as before. **Collapsed by default** — the headline status line is shown ("all gating checks passed · N checks" + chevron); the chips reveal on click. Chips color by band (`_renderVerdictChips`): 🟢 pass / 🔴 fail / ⚪ abstain, plus 🟡 **review** for banded metrics in the amber band (passing-but-flagged), and **group by the moment measured** (Plan · Retrieve · Act · Compose · Deliver · The run · Flow run — `_stageGroupsForCard`, reading the served model) with values formatted in natural units (`_fmtMetricScore`: counts `1`, rates `0%`, cost `$…`, latency `4.1s`, tokens with commas). **Honest headline:** a turn with no gating failures/reviews but non-gating health signals (a tool errored and self-correction recovered it, `tool_success_rate < 1` / `self_correction > 0`) reads amber *"passed · recovered from a tool error · N self-correction(s)"* — a recovered error is Review, never green "all checks passed". It renders in **both** moments:

- **After execution (live):** the online hook emits a `turn_score_complete` Live-Status event (carrying verdicts + judge cost); `processStream` renders the same card immediately. This mirrors `next_step_generation_complete` — the judge token cost flows through the rigorous allocation (`_record_score_usage`: session/turn totals + an idempotent `token_update` `call_id="turn_score"` that rolls into the title-bar KPIs + the consumption ledger), so cost shows on the card and the counters, and survives reload via `turn_cost`.
- **On reload:** wired in `eventHandlers.js::handleReloadPlanClick`'s `finally` (covers **every** profile branch — a `return` in the `try` still hits `finally`); auto-loads the turn's existing score via `/evals/turn-score` (no re-score, no tokens) and shows the cost note.

One **scoring button** lets a user (re)score on demand, and its state decides the act: an answer with no score yet offers **Score this answer** (code checks, no tokens); a scored answer offers **Ask the AI judges** (code checks + the judge panel, uses tokens); a judged answer offers **Ask the AI judges again**. Its icon (target / scales), tooltip and accessible label name the act and its cost. *Why one button (Oct 2026):* every answer is already scored by code after delivery, so a second code-only score rarely adds anything, and a separate judges switch that did nothing until a second button was pressed read as two controls for one act. The free code-only re-score of an already-scored answer (useful only after a threshold change) is no longer offered from the card. The button lives in the **assurance band header** (`.ad-ctl-slot`) with the Guide button, not on the card. Self-skips flow/error turns; never disturbs the rendered trace. The note beneath the headline (`_qualityScoreNote`) states, in order, **which scorers ran** (the agent's class), **when** (*taken after the answer shipped*), **how it was triggered**, **whether judges ran**, and **what it cost** — so the reader never has to infer that a measurement did not affect the answer they are looking at. This is the concrete Sessions↔Agents bridge: a session trace is the raw material, graded by the same scorers the entity lens rolls up.

---

## 12. Profile-class coverage matrix

All five classes are covered — every class has deterministic signals, so "deterministic-first" holds across the board; judges add the open-ended residue.

| Class (IFOC) | Trace signals | Deterministic scorers | Judge(s) |
|---|---|---|---|
| `llm_only` (Ideate) | answer, format | completed, answer_present, format, cost, efficiency, safety | instruction_following, answer_correctness, content_safety |
| `rag_focused` (Focus) | retrieved docs, citations, answer | + retrieval_performed, grounded_overlap, citations | groundedness, source_consistency, answer_correctness, content_safety |
| `tool_enabled` (Optimize) | plan structure, tool calls/args, self-corrections | + tool_subset, must_not_call, self_correction, plan_adherence | tool_arg_optimality, answer_groundedness, scope_faithfulness, grain_faithfulness, answer_correctness, content_safety |
| `genie` (Coordinate) | slaves invoked, synthesis | + routing, coordination_efficiency | synthesis_quality, answer_correctness, content_safety |
| `conversation_with_tools` (Ideate + tools) | tool calls, answer | tool_subset, must_not_call, self_correction (+ cross-cutting) | tool_arg_optimality, instruction_following, answer_groundedness, scope_faithfulness, grain_faithfulness, answer_correctness, content_safety |

`content_safety` (Jul 2026) applies to **all** profile classes; the `answer_correctness` judge also applies to every class (abstains without a reference answer).

Cross-cutting scorers (completed, answer_present, format, cost, efficiency, safety, provenance_integrity) apply to all. The reliability/quality suite (§5.2a) layers further signals on top — cross-cutting (latency, clean_termination, context_health, error_recovery, output_sanity) plus per-class (tool_success_rate, no_tool_loops, numeric_fidelity, plan_execution for the tool classes; retrieval_relevance for Focus; expert_success for Coordinate).

---

## 12a. Flow-agents (the second agent type)

The Agents lens has **two agent types**, not one:

| | Profile-agent | Flow-agent |
|---|---|---|
| What it is | An IFOC-classed profile | A flow (DAG of nodes) |
| Session | Bound to a session per turn | **Not session-bound until execution** — binds to a session at run time, and *must* to run |
| Profiles | Is one | Has **0 or many** (each node may carry its own `node.data.profile_id`) |
| Graded on | Turn trajectory (tools/plan/answer) | **Flow structure** — run completion, node success, branch correctness, budget |
| Eval class | `llm_only`/`rag_focused`/… | `"flow"` |

Because a flow may have **no** profile or **several**, its quality cannot be folded into "a profile" — a flow is judged **as a whole agent**, profile-agnostically. This is why flows get their own roster cards (a "Flow" badge + "Flows" filter pill, amber `#f59e0b`), never an IFOC color.

**The universal task KPIs cross over to flows.** While turn-shaped profile scorers never run on a flow, the Task Transparency framework (§5.2c) is agent-type-consistent: `classify_tasks` is polymorphic, so `scorers_for("flow")` returns the 6 flow scorers **plus** `tasks` and `parallel_efficiency` (their `applies_to` lists `"flow"`). Flow nodes carry real `started_at`/`completed_at`, making flows the **strongest** `parallel_efficiency` case (parallel branches yield a genuine speedup). So the Tasks tier of the Quality card reads consistently for profile-agents and flow-agents alike.

**`FlowRunRecord`** ([`eval/flow_types.py`](../../src/trusted_data_agent/eval/flow_types.py)) normalizes a flow run + its `node_runs`: `completed`, `executed_nodes`/`failed_nodes`, `executed_identifiers` (match by id/label/type), `step_count` (incl. loop iterations), `cost_usd`, tokens, error. Input = the `GET /v1/flows/runs/<run_id>` shape.

**Flow scorers** ([`eval/scorers/flow.py`](../../src/trusted_data_agent/eval/scorers/flow.py), `applies_to={"flow"}`, CI-pure deterministic):

| Scorer | Dimension | Gating | Asserted by |
|---|---|---|---|
| `FlowCompletedScorer` | `flow_completed` | ✓ | `expect_complete` (default true) |
| `FlowNodesSucceededScorer` | `flow_nodes_succeeded` | ✓ | — (no node errored) |
| `FlowNodesReachedScorer` | `flow_nodes_reached` | ✓ | `require_nodes` (subset) |
| `FlowForbiddenNodesScorer` | `flow_forbidden_nodes` | ✓ | `forbid_nodes` (wrong-branch detection) |
| `FlowCostScorer` | `flow_cost` | metric | `max_cost_usd` |
| `FlowEfficiencyScorer` | `flow_efficiency` | metric | `max_steps` |

`scorers_for("flow")` returns ONLY these — flows are a distinct agent type, so turn-shaped profile scorers never run against a flow record. Flows have no judge scorers.

**Offline** ([`eval/flow_runner.py`](../../src/trusted_data_agent/eval/flow_runner.py)): `FlowEvalExecutor` runs a flow exactly as a user does — `POST /v1/flows/<id>/execute` (binds a session at run time) → poll → read the run record. `CompositeEvalExecutor` routes each case to the flow executor (`profile_class == "flow"`, carrying `flow_id`) or the turn executor, so a dataset may mix both agent types. Flows inherit the same pass^k / gate / diff machinery. The CLI `run` uses the composite by default.

**Online**: a gated, defensive hook in `flow_executor._run_and_record` (the single post-run seam) calls `evaluate_flow_run_online` → flow scorers → an `agent.flow.eval` OTLP span keyed by flow identity (`agent.kind='flow'`, `agent.flow_id`, signed attestation bound to the run id). Off by default; never affects the run. `online_quality_by_flow` aggregates these and explicitly **excludes flow spans from the profile `by_agent`** (and vice-versa), so a flow's effective profile isn't double-counted.

**Roster**: flow-agents appear as their own cards (online `by_flow` + `GET /v1/flows` for identity), with the "Flows" filter pill and flow-run units ("3 flow runs" vs "3 live turns").

---

## 12b. Genie coordinator/expert (master/slave) scoring

A genie turn is a tree: a **coordinator** (master) delegating to 0..N **experts** (slaves, each a profile-agent running in a child session). The scoring model:

| Concern | Treatment | Why |
|---|---|---|
| **Quality verdicts** (groundedness, citations, tool_subset, …) | **Isolated per agent** — never merged into the master | A Focus expert's `groundedness` is a Focus-class dimension; merging would conflate agent types + double-count. |
| **Coordination verdicts** (`routing`, `expert_success`, `coordination_efficiency`) | **On the master** — the rolled-up expert-*outcome* signal | "Did I call the right experts and did they succeed?" is the coordinator's job. |
| **Resource metrics** (cost/tokens/latency) | **Self-cost** on every node (coordinator + each expert) | Portfolio sums never double-count; the coordinator's inclusive figure is a display-only `genie_rollup` annotation (`+ N expert $Y · turn total $Z`). |
| **Experts** | **Auto-scored** (deterministic is free) in their own class; **not** skipped | Expert quality is signal the master can't capture. The recommender *is* still skipped for slaves. |

**Persistence** (`coordinate_engine.execute_genie`): the coordinator turn stores `final_summary_text` (full answer → `answer_present` holds), `slaves_invoked` (per-expert `{profile_tag, slave_session_id, status, input/output_tokens, cost_usd, started_at/completed_at/duration_ms}` via `_summarize_slaves`, read from each expert's child-session turn), and `genie_rollup` (inclusive cost/tokens). `turn_cost`/`turn_*_tokens` stay coordinator-only (feed consumption — experts bill their own child sessions).

**Online hook** (`execution_service.run_agent_execution`): online eval runs for experts too (the `_is_genie_slave` skip was removed for eval; the recommender keeps it), tagging the span `agent.invocation='expert'` + `agent.parent_session_id`. Coordinator spans carry `eval.experts` (the per-expert summary) + `eval.expert_count` so `agent_runs` can build the master/slave tree. `online_quality_by_agent` counts `direct_turns`/`expert_turns` and the roster shows "· N as expert".

> **Regression fixed (Jun 2026):** `execute_genie` referenced an undefined `executor` on the latency `duration_ms` line; the `NameError` was swallowed by the persistence `try/except`, so `update_last_turn_data` never ran → **all** genie turns were dropped from `workflow_history` (broke plan-reload 404 + online scoring). Fixed with a local turn timer.

---

## 13. Security & governance

- **Online eval** is gated globally by `ONLINE_EVAL_ENABLED` (deterministic scoring **on** by default — free + non-blocking; the token-costing judge layer `ONLINE_EVAL_INCLUDE_JUDGES` is **off** by default). It **never blocks or mutates a turn** — it runs as a backgrounded, timeout-bounded task (§6.4).
- **System scope** requires `VIEW_ALL_SESSIONS`, enforced server-side (not just a hidden tab).
- **Signed attestations** reuse the Ed25519 provenance key — verdicts are tamper-evident.
- **Judges** are non-gating until calibrated against human labels (κ floor).
- **Live judges** (in `score-turn`/CLI) are opt-in and clearly token-costing; default is deterministic (0 tokens).
- **Genie experts auto-scored, never double-charged** — experts are scored deterministically (free) and tagged `agent.invocation='expert'`; self-cost accounting (§12b) means an expert's cost appears once on its own span and only as a display annotation on the coordinator, so portfolio totals stay correct.
- **Grounding gate (§6a)** is gated globally by `GROUNDING_GATE_ENABLED` (master kill-switch in the `agent_eval` block) and per-profile `groundingConfig.mode` (default `observe` — never alters an answer). It **fails open** (a gate error delivers the original answer) and, in `enforce`, fails **safe to annotate** on any judge unavailability — it can never break a turn. The judge call's token cost routes through `CostManager`/consumption like every other LLM call. A confirmed withhold is recorded in the Ed25519 provenance chain (audit: "agent attempted out-of-scope claim, gate withheld").

---

## 14. Extending the system

| To add… | Do this |
|---|---|
| A **deterministic scorer / metric** | Subclass `Scorer` in `scorers/deterministic.py`, set `dimension`/`kind`/`applies_to`/`gating`, `register_scorer(...)`. |
| An **LLM judge** | Subclass `JudgeScorer` in `scorers/judges.py`, set `dimension`/`applies_to`/`rubric`, implement `build_messages`, register. |
| An **assertion key** | Read it in the relevant scorer's `score()`; abstain (`None`) when absent. |
| A **new online exporter** (e.g. real OTLP) | Implement `OnlineEvalExporter.export(span)`, call `set_exporter(...)`. |
| A **judge provider** | Nothing — `build_panel` picks up any configured LLM family automatically. |
| A **grounding signal** (a new trigger or scope source) | Add the pure primitive to `agent/grounding/primitives.py` (shared by the scorer + gate) and wire it into `gate.py`'s trigger; keep the enforce decision in `enforce.py`. |

---

## 15. Deployment & configuration

**Config flags** ([`core/config.py`](../../src/trusted_data_agent/core/config.py)):

Deterministic scoring is free, so when enabled it scores **every** turn/run; only the token-costing **judge layer** is stochastic. All four are **admin-controlled at runtime** via `GET/POST /v1/admin/app-config` (`agent_eval` block) + the App Config admin panel — the `config.py` values are just startup defaults, not static flags.

| Setting | Default | Meaning |
|---|---|---|
| `ONLINE_EVAL_ENABLED` | `True` | Score every executed turn / flow run (deterministic). |
| `ONLINE_EVAL_INCLUDE_JUDGES` | `False` | Add the LLM-judge panel to automatic scoring (costs tokens). |
| `ONLINE_EVAL_JUDGE_SAMPLE_RATE` | `0.05` | Fraction of turns to *also* judge (1.0 = judge every turn). |
| `ONLINE_EVAL_ALWAYS_ANOMALOUS` | `True` | Always judge errored / heavy-self-correction turns. |
| `ONLINE_EVAL_SINK` | `tda_eval/online_eval.jsonl` | OTLP-shaped quality span sink. |
| `ONLINE_EVAL_DEFAULT_THRESHOLDS` | seeds `max_out_of_scope_dbs`/`max_tool_calls` | Banded global thresholds `{metric: {healthy, danger}}` that make the threshold scorers band/gate live traffic (§6.4). Seeded so the two scope/efficiency metrics band out-of-box; runtime-mutable via `agent_eval.default_thresholds` and **persisted to `system_settings`** (survives restart; §6.4). |
| `ONLINE_EVAL_TIMEOUT_SECONDS` | `25` | Cap on the backgrounded judge layer per turn — bounds it so it can't leak a coroutine. **Floored** at the per-call timeout + margin, so it always leaves room for one full judge call; a timeout keeps the rulings that completed (§6.4). |
| `ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS` | `20` | Per-judge LLM call timeout in the panel — a stalled model degrades to `unknown` instead of hanging the panel. Raising it raises the layer cap with it (§6.4). |
| `GROUNDING_GATE_ENABLED` | `False` | Master switch for the grounding gate (§6a). Off → the gate never runs (answers byte-identical). On → per-profile `groundingConfig.mode` applies (default `observe`). In the `agent_eval` block; restart-persisted. |

Three scoring paths share the deterministic core; judges are an option on all three: **automatic** (online hook — judges via the admin `agent_eval` settings), **manual** ("Score this turn" overlay — judges toggle), **offline** (CLI `run --judges`). Flows have only deterministic scorers (no judges). The admin settings live in **Administration → App Config → Trust**: *Scoring* (automatic scoring, LLM judges, judge sample rate), *Quality bands* (the banded thresholds), *Enforcement* (the grounding / guardrail masters, the Behavioral Trust Score and circuit breaker, the OBDA policy). The former single *Agent Evaluation* card under Operations is gone.

**Restart requirements:** the `agent_eval` settings are runtime-mutable (no restart); other backend changes + `templates/index.html` require a **server restart**; JS/CSS are browser-reload only.

---

## 16. Testing

CI-pure (no server, no live LLM), runnable as `PYTHONPATH=src python test/test_eval_*.py` (eval) plus `test/test_grounding.py`, `test/test_grounding_benchmark.py`, `test/test_kg_scope_derivation.py` (grounding gate + benchmark, §6a/§6b):

| Suite | Asserts | Covers |
|---|---|---|
| `test_eval_deterministic.py` | 47 | 16 core scorers + banded metrics + store + real-shape hardening (list-plan / list-metadata across classes) |
| `test_eval_reliability.py` | 48 | the 13 reliability/quality scorers (incl. `scope_adherence` + `tool_calls`) + banded gating + tasks/steps wiring |
| `test_eval_tasks.py` | 34 | Task Transparency framework: per-class classification, planning-collapse, rework, ReAct/genie supplements, flow node parity + real parallelism, orchestrator waves, steps≠tasks, honest abstain, composition evidence + span aggregation |
| `test_eval_runner.py` | 15 | pass^k, aggregation, gate, mining |
| `test_eval_judges.py` | 47 | parsing, consensus, dispatch, abstain, calibration, runner wiring + no-duplicate regression (FakePanel) + `scope_faithfulness` (domain-only rubric) + `answer_groundedness` |
| `test_eval_online.py` | 54 | judge sampling, every-turn scoring, judges-on hook + `turn_score_complete`, signed attestation, OTLP span, exporter, admin default thresholds + 3-band (healthy/review/danger + legacy scalar), judge-cost sink accounting |
| `test_eval_analysis.py` | 54 | clustering, diff, by_agent/by_flow/by_dimension, KPI dashboard, timeline, dedup, store list/annotations, REST surface |
| `test_eval_cli_rest.py` | 17 | CLI exit codes, REST endpoint presence, executor |
| `test_eval_flow.py` | 41 | FlowRunRecord, 6 flow scorers, dispatch isolation (+ universal task KPIs cross-over), flow span / `by_flow`, online flow hook (fake `flow_db`) |
| `test_eval_genie_ux.py` | 42 | Genie master/slave (§12b): RunRecord genie accessors + SELF-cost, genie scorers (answer_present / delegated-cost annotation / expert_success), `agent_runs` ledger + nested tree + synthetic fallback + no-double-count, `online_quality_by_agent` averages + invocation split, span attrs, REST surface |
| `test_grounding.py` | 102 | Grounding gate (§6a): shared primitives (incl. the prose-mention out-of-scope trigger, oos08 closed), pure `GroundingGate.evaluate` over the mode×scope matrix, judge-backed-enforce decision (`resolve_enforced_verdict`: drift→BLOCK / faithful→PASS / abstain→ANNOTATE), adapter extraction (ReAct + Optimize shapes via `RunRecord`), config resolution, annotate/block HTML, fail-open, ensure-trusted-scope |
| `test_grounding_benchmark.py` | 34 | Benchmark (§6b): confusion-matrix + headline math pinned to the design mock, `trigger_decision` **pinned to the live gate's `needs_judge`** (incl. prose-drift), caught-policy, per-class, markdown render, dataset load/validate (36 cases, 12/18/6) |
| `test_kg_scope_derivation.py` | 17 | KG deployed-scope derivation (the gate/scorer ground truth): physical-identifier primary path, discovery `database`-root fallback, no-widen guarantee, always-on full-KG trusted scope |
| **Total** | **552** | (399 eval + 153 grounding/benchmark/scope) |

**Closed-trust-loop CI suites (§6c, the assurance-inbox / review / red-team internals):** `test_trust_behavior.py` (48) · `test_backtest.py` (34) · `test_trust_events.py` (29) · `test_review_queue.py` (40, incl. the tool-result-firewall scan) · `test_redteam.py` (11) · `test_live_redteam.py` (23) — 185 assertions across the six trust-program suites, cited inline in §6c.

> `test_eval_runner.py`'s 3 mining checks are sensitive to test ordering (env-var + session-backend singleton leak between suites) and may show as failures when the whole directory runs in one process; the product code is correct in isolation. Run that suite standalone to verify.

---

## 17. File reference summary

**Backend — eval package** ([`src/trusted_data_agent/eval/`](../../src/trusted_data_agent/eval/)):
`types.py` (incl. `Verdict.band`) · `scorer.py` (incl. `banded_metric`) · **`tasks.py`** (Task Transparency framework — `classify_tasks`, `TaskSet`, polymorphic over `RunRecord`/`FlowRunRecord`) · `store.py` · `scorers/{deterministic,reliability,judges,flow,plugin_properties}.py` · `aggregate.py` · `runner.py` · `gate.py` · `mining.py` · `rest_executor.py` · `flow_runner.py` · `cli.py` · `judge.py` · `calibration.py` · `online.py` (incl. `default_assertions`; span attrs `agent.invocation`/`agent.parent_session_id`/`eval.experts`) · `analysis.py` (incl. `agent_kpi_dashboard`, `agent_timeline`, **`agent_runs`**, per-agent averages + invocation split) · `flow_types.py`. Genie scoring (§12b): `types.py` (genie accessors), `scorers/{deterministic,reliability}.py` (self-cost + delegated annotation).

**Backend — grounding gate (§6a) + benchmark (§6b):** [`agent/grounding/`](../../src/trusted_data_agent/agent/grounding/): `primitives.py` (shared with `eval/scorers/reliability.py`) · `corpus.py` (corpus family) · `coordinator_figures.py` (pure measure) · `types.py` · `gate.py` (pure decision) · `enforce.py` (judge-backed-enforce decision) · `adapter.py` (RunRecord-backed extraction + apply). [`eval/benchmarks/grounding/`](../../src/trusted_data_agent/eval/benchmarks/grounding/): `report.py` (pure matrix/headline + `trigger_decision`) · `dataset.py` + `dataset/fitness_db.json` · `harness.py` (live admin harness). Scope substrate: `components/builtin/knowledge_graph/handler.py` (`_derive_scope_databases`, `_derive_full_kg_scope_dbs`, `get_trusted_scope`).

**Integration:** `agent/engines/coordinate_engine.py` (genie turn persistence — `final_summary_text`/`slaves_invoked`/`genie_rollup`, `_summarize_slaves`; and the genie grounding-gate call to `run_grounding`) · `agent/execution_service.py` (online turn-hook seam — expert scoring + invocation/parent tags) · `agent/grounding/runtime.py` (the engine-agnostic grounding orchestration `run_grounding` → judge-backed enforce → `_attribute_judge_cost` → `_record_score_usage`) · `agent/executor.py` (grounding-gate seam `_run_grounding_gate_full`, which delegates to `run_grounding`; the four standard engines `agent/engines/{ideate,focus,conversation}_engine.py` + Optimize call the shared seam) · `core/flow_executor.py` (online flow-hook seam) · `core/config.py` (`ONLINE_EVAL_*`, `GROUNDING_GATE_ENABLED`) · `core/provenance.py` (signing key) · `api/rest_routes.py` (eval endpoints incl. `/evals/agents/<tag>/runs`, `/evals/score-turn` which routes judge cost through `_record_score_usage`) · `api/routes.py` (turn-details surfaces `is_genie_slave` for the slave overlay note) · `api/admin_routes.py` (`agent_eval` app-config incl. grounding master toggle).

**Frontend:** `static/js/handlers/agentPerformanceHandler.js` (Agents view — profile + flow cards, timeline, **Runs Ledger**, **Coordination Trace Tree + waterfall**, **Compare** + injected `#agent-perf-ux-css`) · `static/js/handlers/agentKpiCharts.js` (cross-agent inline-SVG KPI charts + timeline card) · `static/js/ui.js` (`injectTrajectoryEvalOverlay`, collapsible tier-grouped `_renderVerdictChips`, `_fmtMetricScore`, `agents-view` switch hook) · `static/js/eventHandlers.js` (overlay wiring) · `static/js/handlers/thresholdSliders.js` (banded dual-handle threshold sliders + presets) · `static/js/adminManager.js` (`agent_eval` load/save via the slider module) · `static/js/executionDashboard.js` (the Awareness panel, existing) · `templates/index.html` (`agents-view`, rail topic, the App Config → Trust panel).

**Tests:** `test/test_eval_*.py` (10 suites, 399 assertions) + `test/test_grounding.py` (102), `test/test_grounding_benchmark.py` (34), `test/test_kg_scope_derivation.py` (17) — 552 total; plus the six closed-trust-loop suites (§6c, 185 assertions).

---

## 18. Design principles (methodology grounding)

1. **Deterministic > probabilistic.** Grade in code wherever possible; judges only for the open-ended residue.
2. **Grade what matters, not the path.** Subset/superset/must-not-call, never exact-sequence.
3. **Abstention is first-class.** A scorer/judge returns `None` rather than fabricating a verdict.
4. **Decompose metrics.** One isolated judge per dimension, evidence-cited, with an "unknown" escape; diverse-family panel + majority consensus.
5. **pass^k, not pass^1.** Reliability surfaces the tail single-shot accuracy hides.
6. **Calibrate before trusting.** A judge stays advisory until it agrees with humans (Cohen's κ).
7. **Golden sets from production failures**, not imagination (trace mining).
8. **One substrate, two lenses.** Sessions (instance) and Agents (entity) over the same signed trace — bridged by the trajectory overlay.
9. **Never break a turn (or a flow run).** Online eval is fully wrapped, deterministic-only, off by default — on both the turn and flow seams.
10. **Honest UX.** Empty/"unevaluated" states over fake numbers.
11. **An agent is a profile OR a flow.** Flow-agents are graded as a whole (run/node/branch/budget), profile-agnostically — a flow may compose 0 or many profiles, so its quality is never folded into one. See §12a.
12. **Observe and enforce share one definition.** The on-path grounding gate imports the *same* primitives as the off-path `scope_adherence` scorer and defers its hard decisions to the *same* `scope_faithfulness` judge — so what the dashboard flags and what the gate withholds can never diverge. See §6a.
13. **Tiered enforcement: cheap trigger, judged decision.** A deterministic signal is the recall filter; an LLM judge makes the block/deliver call. Never block deterministically (it punishes honest refusals); never run the judge when nothing triggered (it wastes tokens). See §6a.3.
14. **Enforce fails safe, never breaks a turn.** A gate malfunction delivers the original answer (fail-open); judge unavailability degrades to an annotation (fail-safe), never a block-on-uncertainty. Default `observe` is byte-identical to no gate. See §6a.4.
15. **Prove it.** Enforcement quality is a published, reproducible benchmark with FN/FP shown, not a claim. See §6b.
