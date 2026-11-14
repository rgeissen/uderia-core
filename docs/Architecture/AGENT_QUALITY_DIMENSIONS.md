# Agent Quality Dimensions

Every agent turn (and flow run) is graded by the eval layer against a set of **quality
dimensions**. This document is the canonical, consumer-facing reference for what each
dimension *measures* — the plain-language meaning behind each score shown in the
**Agent Roster** ("Live Pass Rate by Dimension", the compare heatmap) and the
**Live Status → Quality** card.

> **Presentation rule (MANDATORY):** dimensions are shown to the user with **logical
> names, never the internal scorer key.** `tool_success_rate` → **Tool Success Rate**,
> `no_tool_loops` → **No Tool Loops**, etc. The internal key is a physical detail and
> must not leak to the consumer.

## Refinement pairs — when two measures of one property disagree

A few dimensions measure the same property at different resolutions: `grounded_overlap`
counts literal token overlap, `groundedness` reads meaning and refines it. Where both
decided a turn, the pair's standing — not worst-of — decides what the coarse check
contributes to its **station**: a corroborated failure reads *needs you now*, a **disputed**
one (coarse flagged, judge cleared) reads *worth a look* and is named as such.

The dimension's own band is never rewritten — a chip always shows what was measured. Only
the station contribution changes, and a disputed turn is still amber, still counted, still
named. Declared once in `agent/assurance_points.py:REFINEMENT_PAIRS`; see
TRUST_STATUS_COMMUNICATION.md Rule 3.0c for why the disagreement is reported rather than
resolved by weighting.

## Two axes — where a dimension is counted, and what it is about

**Counted at JUDGED, all of them.** Measurement is a lane: a dimension is taken *after*
the answer shipped and changes nothing, so it is counted at one station — JUDGED, whose
subject is exactly that. The other seven Trust Circle stations report what their
**mechanisms** did during the turn: a gate that escalated, a guard that redacted, a
boundary that denied a tool. A red `grounded_overlap` therefore colours JUDGED, while
GROUNDED reports what its nine mechanisms did — two true answers to two different questions.
TRUSTED remains the sole aggregate over everything. See
TRUST_STATUS_COMMUNICATION.md Rule 3b.6.

**Grouped by the MOMENT it was taken.** Within JUDGED, dimensions are grouped by *when in
the turn* they measure — the process the agent actually runs:

| Moment | What it measures |
|---|---|
| **Plan** | how the agent decided what to do |
| **Retrieve** | what it pulled in, and whether that was worth pulling |
| **Act** | what it did with its tools |
| **Compose** | how the answer was built from what it had |
| **Deliver** | what actually reached the reader |
| **The run** | properties of the whole turn — cost, latency, volume, termination |
| **Flow run** | flow-agents only: the health of the flow run itself |

Declared once in `eval/stages.py` and **served** to the frontend on
`/v1/assurance/vocabulary`; the frontend declares no group list of its own. The Assurance
Guide lists the moments as first-class entries, the admin Trust panel groups its threshold
sliders by them, and every roster surface orders by them — all through one resolver
(`QualityDimensions.stageOf` / the handler's `_dimStageOf`). `partition_gaps()` must be
empty: a scorer added without a moment fails the gate rather than landing in a default.

**Labelled by a guarantee.** Each dimension also records which guarantee it is *about* —
`grounded_overlap` is about GROUNDED — shown as the home chip on its Guide entry
(`station_dimensions()`). That is a label for navigation. It never counts.

## Where this is surfaced in the app

Both consumer surfaces render dimensions identically, driven by one shared module:

| Surface | File | What it shows |
|---|---|---|
| Agent Roster | `static/js/handlers/agentPerformanceHandler.js` | Per-dimension pass-rate bars, compare heatmap, run ledger, diff — all grouped/ordered **by moment** (`_dimStageOf`, reading the served model) |
| Trust Circle → JUDGED → Scorecard | `static/js/ui.js` (`_renderVerdictChips`, `_stageGroupsForCard`) | Per-verdict chips grouped **by moment**, adopted into the JUDGED station panel — the reading and the checks behind it in one place |
| **Shared source of truth** | `static/js/handlers/qualityDimensions.js` (`window.QualityDimensions`) | Logical labels, the theme-compliant hover **tooltip**, and the in-app **Assurance Guide** glossary |

**Single source of truth.** The label + one-line description for every dimension lives
once in `qualityDimensions.js` (`DIM`). Both surfaces call `window.QualityDimensions.label(key)`
and stamp `data-dim="<key>"` on the element. The shared module then provides, globally:

- a **hover/focus tooltip** (Uderia styleguide compliant — CSS variables, monochrome SVG,
  no emoji, light + dark) showing the logical name + a **short** explanation (`DIM.desc`) +
  the per-verdict runtime note where one exists (e.g. *"turn completed without error"*), and
- a one-click, searchable **Assurance Guide** modal (the long-form "document") reachable
  from a **Guide** pill on each section header, or by clicking any dimension. Each entry
  shows a **fuller** explanation (`LONG`) — how the score is computed, what makes it
  band/fail, and when it abstains — so the guide earns its click. This keeps tooltips
  short and frictionless while making the full reference one click away.

**Context-specific & status-aware.** The guide is not a static catalog dump. When opened
from a scored turn (the Live Status Quality card, a runs-ledger run) or an agent (the roster
detail), it opens **scoped to exactly the dimensions in play**, and each one shows its **live
status pill** (Healthy / Review / Danger / Not applicable) for that turn/agent. A one-click
scope toggle switches between *this turn/agent* and *all dimensions*. This works via a
`data-dim-context` attribute stamped on the surrounding container — a JSON array of
`{k, b, s?, t?}` (key, band, score, threshold) — so the context travels entirely in the DOM,
robust across concurrent cards with no shared global state. Short (`desc`) and long (`LONG`)
text both live in `qualityDimensions.js`.

**Admin thresholds (global toggle).** For a scored turn, banded metric verdicts carry the
admin-configured **fail line** (`Verdict.threshold`, the danger boundary) alongside the
measured `score`. When any context dimension has such a boundary, the guide shows a single
global **Admin thresholds** toggle; flipping it on reveals, per applicable dimension, the
*measured value* and the *admin fail line* with direction (e.g. Token Usage — “Measured
42,402 · admin fail line: above 40,000”). Default off, to keep the guide clean. Note: the
server stores only the danger gate on the verdict (the healthy/review split is computed and
discarded), so the guide honestly shows the fail line, not the amber split.

To keep the definitions accurate, the descriptions below are sourced from the scorer
docstrings in `src/trusted_data_agent/eval/scorers/*.py`. The table's "What it measures"
column is the **short** form (`DIM.desc`, shown in tooltips); the in-app guide shows the
**fuller** `LONG` text. When a scorer is added or its meaning changes, update **both**
`qualityDimensions.js` (`DIM` short + `LONG` fuller) and this table.

## Reading the statuses

A dimension is either **scored** (one of three bands) or in one of **three distinct
"no-grade" states** — they mean different things and imply different actions, so the guide
never merges them into a single "Not applicable":

| Status | Colour / style | Meaning |
|---|---|---|
| Healthy | green | Signal is good. |
| Review | amber | Passing but flagged — worth a look (non-gating). |
| Danger | red | Gating failure or a hard problem. |
| **Not set** | slate, dashed | The metric **applies** to this agent but has **no threshold / target / assertion configured** — an admin/author *could* set one to make it grade (e.g. `efficiency` with no optimal-tasks target; a metric that measured a value but has no gate). A config gap. |
| **Abstained** | slate, dotted | The metric applies **and is configured**, but there was **no signal to measure** this turn — it simply didn't fire (e.g. `error_recovery` when no error occurred; `numeric_fidelity` when the answer stated no numbers). |
| **Not applicable** | grey, solid | The metric **structurally doesn't apply** to this agent's IFOC class (e.g. `routing` only exists for Coordinate). |

The guide classifies the no-grade states from each verdict's own abstention reason and shows
that exact reason inline under the pill, so you read the truth rather than decode a label.
The class-applicability rules mirror the scorers' `applies_to` in
`src/trusted_data_agent/eval/scorers/*.py` (kept in sync in `qualityDimensions.js:APPLIES_TO`).

Most dimensions are **deterministic** (0-token checks derived from the execution trace);
a subset are graded by the **LLM-judge** panel. That distinction is shown in the guide.

### Aggregate bars carry their sample size (`n`)

The Agent Roster's **Live Pass Rate by Dimension** bars (and the compare heatmap) aggregate
one dimension across an agent's scored turns via
`analysis.pass_rate_by_dimension` — counting **only decided verdicts** (a bar exists ⟺ at
least one turn produced a True/False for that dimension; abstains/`Not set`/`Not applicable`
are excluded from the denominator).

This mixes denominators by design: **deterministic dims are decided every scored turn, but
LLM-judge dims are decided only on *sampled* turns** (`should_judge` — anomalous turns +
`ONLINE_EVAL_JUDGE_SAMPLE_RATE`). So a judge dim can show a stark `0%`/`100%` off a **single**
verdict, visually identical to a systemic failure across every turn. To keep this honest, the
backend also emits **`dimension_counts`** (the decided-turn count `n` per dimension) from
`online_quality_by_agent` / `online_quality_by_flow`, and the roster renders it:

- an `n=` chip appears on any bar whose dimension was decided on **fewer than all** scored turns;
- when the sample is **tentative** (`n ≤ 2`) the chip is **amber**, the bar fill is dimmed,
  the `%` is muted, and a tooltip explains that a single verdict drives the rate;
- the compare heatmap marks the same low-`n` cells with an amber ring + `· n turn(s)` in the tooltip.

`n` is the aggregate-bar analogue of the per-verdict `Abstained`/`Not set` states above: the
per-turn Quality card tells you *why one turn had no grade*; `n` tells you *how many turns the
aggregate rate actually rests on*. To make judge dims statistically meaningful, raise
`ONLINE_EVAL_JUDGE_SAMPLE_RATE` (Administration → App Config → Trust → Scoring → *Judge sample rate*) at the cost of judge tokens.

## Dimensions in detail

**63 dimensions are registered** (Nov 2026; `eval.scorer.all_scorers()`): 53 deterministic
— 19 in `deterministic.py`, 28 in `reliability.py`, 6 flow — and 10 LLM judges. A module
pack's bound property adds its own. A class sees only the subset whose `applies_to` covers it.

The sections below group by subject matter for **reading**. What the product groups by is
the moment (above); this is a reference index, not a second taxonomy.

### Core — did the turn produce a clean, valid answer at all

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Completed | `completed` | deterministic | The turn ran to completion and produced a final result without erroring out. |
| Answer Present | `answer_present` | deterministic | A non-empty answer was actually returned to the user. |
| Output Sanity | `output_sanity` | deterministic | The answer leaks no internals — no stack traces, raw error/tool JSON, encrypted-content placeholders, or code reprs. |
| Clean Termination | `clean_termination` | deterministic | The turn reached a proper terminal state — no truncated or abandoned run. A turn the record calls failed (`status` failed/error, or an `error_message`) fails it whatever its trace says; this is where a provider that never answered is read (trust campaign C18). A turn that stopped to ask the user (`input_required`, PROP-011) is a clean stop. |
| Guessed Past Ambiguity | `guessed_past_ambiguity` | deterministic | PROP-011 (needs input): the platform records every ambiguity its own code saw on the turn (`ambiguities_observed` — a name matching two of the user's things, a data source that elicited, a child agent that asked). A turn that went on past one without stopping to ask fails; one that stopped passes; abstains when code saw nothing (the model's own judgement is not measured here). Owned by the `needs_input` mechanism at REVIEWED. |
| Safety | `safety` | deterministic | No error or guardrail-violation steps occurred during execution. |
| Format Compliance | `format` | deterministic | The answer honoured the requested format constraints (required/forbidden content, length, pattern). |

### Resource — what the turn cost

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Cost | `cost` | deterministic | LLM spend for this turn (USD), kept within the configured ceiling. |
| Token Usage | `tokens` | deterministic | Total input + output tokens consumed by the turn, kept within budget. |
| Cache Efficiency | `cache_efficiency` | deterministic · informational | Share of the turn's prompt tokens the provider served from its prompt cache (S2, the Attested Cache). Never banded; abstains when caching is not armed, when the provider has no cache, or on a session's first call. Counted at JUDGED and carries no guarantee label (the residue) — the corroboration that the served prefix IS the attested prefix is the `prompt_cache` mechanism on SIGNED. |
| Latency | `latency` | deterministic | Wall-clock time from query to answer, kept within the target. |

### Tasks — how much work the agent did, and how efficiently

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Task Volume | `tasks` | deterministic | How many discrete work tasks the agent executed this turn (planning, tools, reasoning…). Flags bloat when too high. |
| Task Efficiency | `efficiency` | deterministic | Convergence ratio — optimal tasks ÷ tasks actually run. Catches loops and detours. |
| Parallel Efficiency | `parallel_efficiency` | deterministic | Latency speed-up from running parallelisable work concurrently versus a serial chain. |

### Tools — how well the agent used its tools

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Required Tools Called | `tool_subset` | deterministic | Every tool the task required was actually called (extra calls are fine). |
| Forbidden Tools Avoided | `must_not_call` | deterministic | The agent avoided every tool it was told not to call. |
| Data-Surface Denials | `surface_violation` | deterministic | Raw-SQL attempts denied because the agent is confined to its signed data products (OBDA-READ). Informational: a persistent nonzero is a product-gap signal, not a misbehaving agent. |
| Budget Adherence | `budget_adherence` | deterministic · informational | Whether the turn stayed inside the agent's declared turn budget — steps, tokens, seconds — (S5, the Visible Budget): 1.0 within · 0.5 exhausted but finished GRACEFULLY (the next tool call refused naming what is left, the answer says what remains) · 0.0 KILLED by a hard stop. Never banded; abstains when no budget is declared or the switch is off. Counted at JUDGED like every measurement; labelled BOUNDED — the `turn_budget` mechanism declares it, since a budget is a boundary and its adherence is a measurement of that boundary (unlike the S2–S4 mechanisms, which label nothing). |
| Self-Correction Overhead | `self_correction` | deterministic | How much the agent had to retry or fix its own tool calls. Lower is better. |
| Tool Success Rate | `tool_success_rate` | deterministic | Fraction of tool calls that succeeded without a tool error. |
| No Tool Loops | `no_tool_loops` | deterministic | The agent did not stall by calling the same tool with identical arguments repeatedly. |
| Tool Call Volume | `tool_calls` | deterministic | Total number of tool calls — catches brute-force / retry thrash that loop detection misses. Flags bloat, never blocks. |
| KG Seed Utilization | `kg_seed_utilization` | deterministic | Of the knowledge-graph entities the question reached only by meaning (semantic seeding), the share the turn's data calls then used. Informational; abstains when the channel was off/warming/errored, bridged nothing, or the turn ran no data calls. |
| KG Enrichment Recall | `enrichment_recall` | deterministic | Of the tables the turn's data calls READ, the share the knowledge-graph enrichment had SHOWN the agent (leaf-matched, and a shown entity counts under its DEPLOYED identifier too — `Incident` deployed as `v_Incident`). A table the agent found on its own is a retrieval miss and feeds the graph's ranking prior. Informational; abstains when no enrichment fired or no tables were read. |
| Action Write Safety | `action_safety` | deterministic | For agents granted signed actions (OBDA-ACT): of the writes the turn put to the database, the share whose every check held — preconditions, the row bound, postconditions. A failed check was rolled back (nothing landed) but means the agent proposed a write its own contract refuses. Acts refused before they compiled (not granted, not signed, no connection) reached no table and are not counted. Informational; abstains when no write reached the database. Labelled to the `actions` mechanism (BOUNDED; ACT moment; counted at JUDGED). Record: `turn['actions']` + `turn['action_confirmations']` through `action_perform.summarize`. |
| Library Read First | `library_read_first` | deterministic | Of the SQL statements that used a function a skill-library section teaches (as a table operator — NPATH, SESSIONIZE, TD_* …; scalars excluded), the share run AFTER the agent opened a section teaching it. Evidence names the four cells read/unread × ran/failed. Informational; abstains when no statement used a library operator. Record: `turn['library_use']` (`skills/library_use.py`). |
| Error Recovery | `error_recovery` | deterministic | When errors occurred, the turn still recovered and completed (resilience). |

### Knowledge — is the answer grounded in real retrieved data

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Retrieval Performed | `retrieval_performed` | deterministic | Knowledge retrieval actually ran when the task expected it. |
| Grounding Overlap | `grounded_overlap` | deterministic | Fraction of answer sentences whose content overlaps the retrieved context **and the question** — a coarse hallucination pre-signal. Abstains when the delivered text is the platform's own (gate withheld/annotated, honesty rewrote), since that is not a model claim. Deliberately coarse — the Groundedness judge refines it. |
| Citations Present | `citations` | deterministic | The answer *itself* carries attribution — Uderia's canonical `Sources:` block / `(Collection: …)` markers or generic citation forms — as opposed to the always-attached Source Documents panel, which proves retrieval, not in-answer attribution. **Activate:** Administration → App Config → Trust → Quality bands → Compose → the "Citations required" flag; off by default (abstains, shown as *Not set*). |
| Retrieval Relevance | `retrieval_relevance` | deterministic | Average similarity of the retrieved chunks — were they actually on-topic. |
| Numeric Fidelity | `numeric_fidelity` | deterministic | Numbers stated in the answer actually appear in the tool results / retrieved data. Anti-hallucination signal. **Since S3 (Sep 2026)** a figure that equals a `TDA_Compute` receipt's value, or that the turn's `compute_lineage` traced to a result row / a statement's row count, counts as grounded too (the verdict says "N by lineage — a compute receipt or a quoted row"), so the measure moves from *flagging* the agent's arithmetic to *verifying* it — a derived total that was computed by code is grounded, one the model did in its head is not. The answer's own numbering ("### 3. …", "2. …") is not a figure (`primitives.answer_nums`, shared with the grounding gate), and a retrieved chunk supports with its content AND its citation line, so citing the source document never reads as inventing a number (Oct 2026). |
| Scope Adherence | `scope_adherence` | deterministic | The agent stayed within the data scope its knowledge-graph enrichment declared — no wandering to unrelated databases. |
| Governed Data Ratio | `governed_data_ratio` | deterministic | Of the turn's data operations, the fraction routed through the governed, deterministic path rather than free-formed SQL. |
| Honest Absence | `honest_absence` | deterministic | When the answer reports that something does not exist, the turn actually established it. The inverse polarity of grounding and the only check that catches a **confident false negative** — every data query failed, yet the answer reports "there are none". Passes when a query SUCCEEDED (including a legitimate empty result); abstains with no raw-SQL queries or no absence claim. Boolean — no threshold. Reads the honesty gate's own decision, so a rewritten answer still scores as the failure it was. |
| Identifier Provenance | `identifier_provenance` | deterministic | Every identifier the answer presents as established exists in the skill library (F-002). The companion to Citation Fidelity, covering what that check structurally cannot: a fabrication that cites nothing. A skill DECLARES a closed vocabulary — an identifier pattern whose members must exist in its own corpus — and an id matching the pattern but absent from the corpus is provably invented. **Boolean**, so no threshold and no slider: an identifier is real or it is not. Compared case-insensitively — a mis-cased real id is a mis-citation, not an invention. Abstains when the skill declares no vocabulary, and when an unbacked citation was reported instead (that verdict takes precedence). Non-gating. |
| Citation Fidelity | `citation_fidelity` | deterministic | Of the skill-library sections the answer says it consulted, the fraction it actually opened this turn (F-001). A progressive-disclosure agent keeps a lean root and fetches sections on demand, so it can assert *"I have fetched `master-design`"* having called the tool zero times — a claim about its own process that no other check reads. Deterministic because the anchor index is a **closed vocabulary**: it never guesses whether a phrase names a section, only whether a KNOWN name appears in a completed read claim the record does not support. An intention (*"I will read it next"*) or a negation is not a claim. Abstains with no library and when nothing is cited. Non-gating — an unbacked citation is a provenance defect, not proof the answer is wrong. Banded by `min_citation_fidelity` (seeded 0.75/1.0). |
| Context Utilization | `context_utilization` | deterministic | The answer actually drew on the retrieved knowledge — the fraction of its content words present in the retrieved chunks (catches "retrieved then ignored, answered from weights"). Advisory; banded only when `min_context_utilization` is set. |
| Knowledge Freshness | `knowledge_freshness` | deterministic | Age (days since CDC verification) of the stalest document the answer's retrieval drew on. Informational unless `max_knowledge_age_days` is set; abstains honestly when CDC metadata is absent. |
| Source Consistency | `source_consistency` | LLM judge | The retrieved documents agree on the facts the answer states — or the answer explicitly surfaces their disagreement (advisory; fires only with ≥2 distinct retrieved documents). |

### Planning — integrity of the plan and its execution trail

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Plan Size | `steps` | deterministic | How many phases the strategic planner emitted. Each step decomposes into one or more execution tasks. |
| Plan Adherence | `plan_adherence` | deterministic | The agent stayed within its own planned set of tools (soft, non-gating). |
| Plan Execution Coverage | `plan_execution` | deterministic | The tools the planner planned were actually executed. |
| Provenance Integrity | `provenance_integrity` | deterministic | The signed execution-provenance chain is present and sealed — tamper-evidence is available. |
| Context Health | `context_health` | deterministic | No data was dropped under budget pressure (context condensation / RAG-offload / distillation). |

### Coordination — for coordinators (genie), routing and combining experts

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Routing Correctness | `routing` | deterministic | The coordinator consulted the right experts for the query. |
| Coordination Efficiency | `coordination_efficiency` | deterministic | No redundant experts — none consulted twice, count within limit. |
| Expert Success | `expert_success` | deterministic | Every expert the coordinator consulted returned successfully. |

### Quality — open-ended quality judged by an LLM panel

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Answer Correctness | `answer_correctness` | LLM judge | Whether the answer is factually correct against the reference. |
| Answer Groundedness | `answer_groundedness` | LLM judge | Whether the answer's claims and numbers are supported by the tool results. |
| Groundedness (RAG) | `groundedness` | LLM judge | Whether the answer's claims are supported by the retrieved documents. |
| Scope Faithfulness | `scope_faithfulness` | LLM judge | Whether the answer stayed faithful to the knowledge-graph trusted scope and did not drift. |
| Grain Faithfulness | `grain_faithfulness` | LLM judge | Whether the answer addresses the question at the grain and subject ASKED — not a silently substituted coarser one ("which customer…" answered by state). A disclosed substitution passes; disclosure is the honest form. Also the honesty gate's optional in-path escalation judge (`honestyConfig.grainJudge`). |
| Instruction Following | `instruction_following` | LLM judge | Whether the answer followed the user's instructions. |
| Synthesis Quality | `synthesis_quality` | LLM judge | How well multiple sources / experts were synthesised into one answer. |
| Tool Argument Quality | `tool_arg_optimality` | LLM judge | Whether tool arguments were well-chosen and minimal. |
| Content Safety | `content_safety` | LLM judge | The semantic complement to the deterministic outbound guardrail: harmful content a keyword filter would miss — sarcastic/coded abuse, demeaning framing without trigger words, harm-enabling instructions, indirect personal identification. All profile classes; advisory (bands the card + feeds behavioral trust, never blocks the answer path). |

### Flow — for flow-agents, the health of the flow run

| Logical name | Internal key | Scored by | What it measures |
|---|---|---|---|
| Flow Completed | `flow_completed` | deterministic | The flow run finished successfully end-to-end. |
| Nodes Succeeded | `flow_nodes_succeeded` | deterministic | All executed flow nodes completed without error. |
| Required Nodes Reached | `flow_nodes_reached` | deterministic | Every node the run was expected to execute was reached. |
| Forbidden Nodes Avoided | `flow_forbidden_nodes` | deterministic | No node executed that should not have — correct branching. |
| Flow Cost | `flow_cost` | deterministic | Total LLM spend across the flow run, within ceiling. |
| Flow Efficiency | `flow_efficiency` | deterministic | Node-execution efficiency for the flow run. |

## Related

- Eval architecture & scorers: [AGENT_OPS_EVAL_ARCHITECTURE.md](AGENT_OPS_EVAL_ARCHITECTURE.md)
- Scorer implementations: `src/trusted_data_agent/eval/scorers/{deterministic,reliability,judges,flow}.py`; a bound third-party property is graded by the generic `PropertyScorer` in `plugin_properties.py` (its dimension appears beside these — [ASSURANCE_MODULE_PLANE_ARCHITECTURE.md](ASSURANCE_MODULE_PLANE_ARCHITECTURE.md) §5)
- The registry that labels and counts them: [ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md) (§6a allocation, §6c the Guide)
