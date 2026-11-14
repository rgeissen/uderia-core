/*
 * qualityDimensions.js — single source of truth for agent-quality dimension presentation.
 *
 * The eval layer scores turns/runs against internal scorer keys (e.g. "tool_success_rate",
 * "no_tool_loops"). Those physical keys must NEVER reach the consumer. This module maps each
 * key to a LOGICAL, human-readable label plus a plain-language explanation, and provides two
 * transparency surfaces used identically by BOTH consumer views (the Agent Roster —
 * agentPerformanceHandler.js — and the Live Status Quality card — ui.js):
 *
 *   1. A theme-compliant custom TOOLTIP shown on hover/focus of any [data-dim] element
 *      (short explanation, no native gray box). Frictionless — meaning appears instantly,
 *      no click required.
 *   2. A one-click in-app DIMENSION GUIDE glossary modal (the "document") with the full set
 *      of dimensions grouped by tier, searchable, deep-linkable per dimension. Any [data-dim]
 *      element opens the guide at that dimension on click; a [data-dim-guide] button opens it
 *      at the top.
 *
 * Loaded as a plain script (window.QualityDimensions) BEFORE both consumers so it is available
 * to the IIFE handler and the ES-module ui.js alike. Uderia styleguide compliant: CSS variables
 * for all colours (light + dark), monochrome SVG icons, no emoji.
 */
(function () {
    'use strict';
    if (window.QualityDimensions) return; // idempotent

    // ── dimension metadata ───────────────────────────────────────────────────────
    // Descriptions are the plain-language meaning of each scorer, sourced from the
    // scorer docstrings in src/trusted_data_agent/eval/scorers/*.py. Keep them to one
    // clear sentence — the tooltip shows this text; the guide adds system context.
    const DIM = {
        // ── Core ──────────────────────────────────────────────────────────────────
        completed:            { label: 'Completed',              desc: 'The turn ran to completion and produced a final result without erroring out.' },
        answer_present:       { label: 'Answer Present',         desc: 'A non-empty answer was actually returned to the user.' },
        output_sanity:        { label: 'Output Sanity',         desc: 'The answer leaks no internals — no stack traces, raw error/tool JSON, a provider’s raw error, encrypted-content placeholders, or code reprs.' },
        clean_termination:    { label: 'Clean Termination',     desc: 'The turn reached a proper terminal state — no truncated, abandoned or failed run.' },
        guessed_past_ambiguity: { label: 'Guessed Past Ambiguity', desc: 'The turn went on past an ambiguity the platform had seen (two of your things with one name) without stopping to ask.' },
        safety:               { label: 'Safety',                desc: 'No error or guardrail-violation steps occurred during execution.' },
        format:               { label: 'Format Compliance',     desc: 'The answer honoured the requested format constraints (required/forbidden content, length, pattern).' },
        // ── Resource ──────────────────────────────────────────────────────────────
        cost:                 { label: 'Cost',                  desc: 'LLM spend for this turn (USD), kept within the configured ceiling.' },
        tokens:               { label: 'Token Usage',           desc: 'Total input + output tokens consumed by the turn, kept within budget.' },
        cache_efficiency:     { label: 'Cache Efficiency',      desc: 'Share of the turn\'s prompt tokens the provider served from its prompt cache.' },
        budget_adherence:     { label: 'Budget Adherence',      desc: 'Whether the turn stayed inside its declared budget — and finished gracefully when it could not.' },
        latency:              { label: 'Latency',               desc: 'Wall-clock time from query to answer, kept within the target.' },
        // ── Tasks ─────────────────────────────────────────────────────────────────
        tasks:                { label: 'Task Volume',           desc: 'How many discrete work tasks the agent executed this turn (planning, tools, reasoning…). Flags bloat when too high.' },
        efficiency:           { label: 'Task Efficiency',       desc: 'Convergence ratio — optimal tasks ÷ tasks actually run. Catches loops and detours.' },
        parallel_efficiency:  { label: 'Parallel Efficiency',   desc: 'Latency speed-up from running parallelisable work concurrently versus a serial chain.' },
        steps:                { label: 'Plan Size',             desc: 'How many phases the strategic planner emitted. Each step decomposes into one or more execution tasks.' },
        // ── Tools ─────────────────────────────────────────────────────────────────
        tool_subset:          { label: 'Required Tools Called', desc: 'Every tool the task required was actually called (extra calls are fine).' },
        must_not_call:        { label: 'Forbidden Tools Avoided', desc: 'The agent avoided every tool it was told not to call.' },
        surface_violation:    { label: 'Data-Surface Denials', desc: 'Raw-SQL attempts denied because the agent is confined to its signed data products.' },
        self_correction:      { label: 'Self-Correction Overhead', desc: 'How much the agent had to retry or fix its own tool calls. Lower is better.' },
        tool_success_rate:    { label: 'Tool Success Rate',     desc: 'Fraction of tool calls that succeeded without a tool error.' },
        no_tool_loops:        { label: 'No Tool Loops',         desc: 'The agent did not stall by calling the same tool with identical arguments repeatedly.' },
        tool_calls:           { label: 'Tool Call Volume',      desc: 'Total number of tool calls — catches brute-force / retry thrash that loop detection misses. Flags bloat, never blocks.' },
        kg_seed_utilization:  { label: 'KG Seed Utilization',   desc: 'Of the knowledge-graph entities the question reached only by meaning (the semantic seeding channel), the share the turn\'s data calls then actually used.' },
        action_safety:        { label: 'Action Write Safety',   desc: 'Of the signed-action writes this turn put to the database, the share whose every check (preconditions, bound, postconditions) held.' },
        library_read_first:   { label: 'Library Read First',    desc: 'Of the SQL statements that used a function a skill-library section teaches, the share the agent ran after opening that section.' },
        enrichment_recall:    { label: 'KG Enrichment Recall',  desc: 'Of the tables the turn\'s data calls read, the share the knowledge-graph enrichment had shown the agent. A table the agent found on its own is a retrieval miss.' },
        // ── Knowledge ─────────────────────────────────────────────────────────────
        retrieval_performed:  { label: 'Retrieval Performed',   desc: 'Knowledge retrieval actually ran when the task expected it.' },
        grounded_overlap:     { label: 'Grounding Overlap',     desc: 'Fraction of answer sentences whose content overlaps the retrieved context — a coarse hallucination pre-signal.' },
        citations:            { label: 'Citations Present',    desc: 'The answer itself carries attribution — a Sources block or citation markers — not just the attached Source Documents panel.' },
        retrieval_relevance:  { label: 'Retrieval Relevance',   desc: 'Average similarity of the retrieved chunks — were they actually on-topic.' },
        numeric_fidelity:     { label: 'Numeric Fidelity',      desc: 'Numbers stated in the answer actually appear in the tool results / retrieved data. Anti-hallucination signal.' },
        scope_adherence:      { label: 'Scope Adherence',       desc: 'The agent stayed within the data scope its knowledge-graph enrichment declared — no wandering to unrelated databases.' },
        governed_data_ratio:  { label: 'Governed Data Ratio',   desc: 'Of the turn’s data operations, the fraction routed through the governed, deterministic path rather than free-formed SQL.' },
        honest_absence:       { label: 'Honest Absence',        desc: 'When the answer reports that something does not exist, the turn actually established it — rather than reporting a failed lookup as a negative result.' },
        grain_faithfulness:   { label: 'Grain Faithfulness',    desc: 'Judge check that the answer addresses the question at the grain and subject asked — not a silently substituted coarser one.' },
        citation_fidelity:    { label: 'Citation Fidelity',      desc: 'Of the skill-library sections the answer says it consulted, the fraction it actually opened this turn.' },
        identifier_provenance:{ label: 'Identifier Provenance', desc: 'Every identifier the answer presents as established exists in the skill library — not composed on the spot.' },
        context_utilization:  { label: 'Context Utilization',   desc: 'The answer actually drew on the retrieved knowledge — not answered from model memory while ignoring the chunks.' },
        knowledge_freshness:  { label: 'Knowledge Freshness',   desc: 'Age of the stalest document the answer’s retrieval drew on (from CDC sync metadata).' },
        source_consistency:   { label: 'Source Consistency',    desc: 'The retrieved documents agree on the facts the answer states — or the answer surfaces their disagreement.' },
        // ── Planning ──────────────────────────────────────────────────────────────
        plan_adherence:       { label: 'Plan Adherence',        desc: 'The agent stayed within its own planned set of tools (soft, non-gating).' },
        plan_execution:       { label: 'Plan Execution Coverage', desc: 'The tools the planner planned were actually executed.' },
        provenance_integrity: { label: 'Provenance Integrity',  desc: 'The signed execution-provenance chain is present and sealed — tamper-evidence is available.' },
        context_health:       { label: 'Context Health',        desc: 'No data was dropped under budget pressure (context condensation / RAG-offload / distillation).' },
        error_recovery:       { label: 'Error Recovery',        desc: 'When errors occurred, the turn still recovered and completed (resilience).' },
        // ── Coordination ──────────────────────────────────────────────────────────
        routing:              { label: 'Routing Correctness',   desc: 'The coordinator consulted the right experts for the query.' },
        coordination_efficiency: { label: 'Coordination Efficiency', desc: 'No redundant experts — none consulted twice, count within limit.' },
        expert_success:       { label: 'Expert Success',        desc: 'Every expert the coordinator consulted returned successfully.' },
        // ── Quality (LLM-judge) ───────────────────────────────────────────────────
        answer_correctness:   { label: 'Answer Correctness',    desc: 'Judge assessment of whether the answer is factually correct against the reference.' },
        answer_groundedness:  { label: 'Answer Groundedness',   desc: 'Judge check that the answer’s claims and numbers are supported by the tool results.' },
        groundedness:         { label: 'Groundedness (RAG)',    desc: 'Judge check that the answer’s claims are supported by the retrieved documents.' },
        scope_faithfulness:   { label: 'Scope Faithfulness',    desc: 'Judge check that the answer stayed faithful to the knowledge-graph trusted scope and did not drift.' },
        content_safety:       { label: 'Content Safety',        desc: 'Judge check for harmful content a keyword filter would miss — coded or sarcastic abuse, harm-enabling instructions, indirect personal identification.' },
        instruction_following:{ label: 'Instruction Following', desc: 'Judge assessment of whether the answer followed the user’s instructions.' },
        synthesis_quality:    { label: 'Synthesis Quality',     desc: 'Judge assessment of how well multiple sources / experts were synthesised into one answer.' },
        tool_arg_optimality:  { label: 'Tool Argument Quality', desc: 'Judge assessment of whether tool arguments were well-chosen and minimal.' },
        // ── Flow-agents ───────────────────────────────────────────────────────────
        flow_completed:       { label: 'Flow Completed',        desc: 'The flow run finished successfully end-to-end.' },
        flow_nodes_succeeded: { label: 'Nodes Succeeded',       desc: 'All executed flow nodes completed without error.' },
        flow_nodes_reached:   { label: 'Required Nodes Reached', desc: 'Every node the run was expected to execute was reached.' },
        flow_forbidden_nodes: { label: 'Forbidden Nodes Avoided', desc: 'No node executed that should not have — correct branching.' },
        flow_cost:            { label: 'Flow Cost',             desc: 'Total LLM spend across the flow run, within ceiling.' },
        flow_efficiency:      { label: 'Flow Efficiency',       desc: 'Node-execution efficiency for the flow run.' },
    };

    // Longer, code-grounded explanations shown ONLY in the Assurance Guide (the glossary).
    // Tooltips stay short (DIM.desc); the guide earns its click with more detail — how the
    // score is computed, what makes it band/fail, and when it abstains (N/A). Sourced from
    // the scorer implementations in src/trusted_data_agent/eval/scorers/*.py. `long(k)` falls
    // back to DIM.desc for any key without a LONG entry.
    const LONG = {
        completed: 'Passes only when the turn finished with status “success”, produced a valid result, and recorded no error. A red here means the run threw or was left in a non-terminal / invalid state — the most fundamental failure.',
        answer_present: 'Checks that the final answer is non-empty after trimming. Catches turns that succeed mechanically but hand the user nothing — an empty synthesis or a swallowed result.',
        output_sanity: 'Scans the answer for leaked internals — stack tracebacks, raw tool/error JSON, a model provider’s raw error (“Error code: 402 - {…}”), “[ENCRYPTED CONTENT]” placeholders, or Python object reprs — and fails if any appear. Those are implementation details that should never reach the user.',
        guessed_past_ambiguity: 'PROP-011 (needs input). The platform records every ambiguity its own code saw on the turn — a name matching two of your things, a data source that elicited, a child agent that asked. A turn that carried on past one without stopping to ask (input_required) guessed, and fails this check; a turn that stopped passes. Deterministic, no judge. Abstains when code saw nothing: the agent’s own judgement about what to ask is not measured here (a raiser set to observe is what makes this readable).',
        clean_termination: 'Confirms the turn reached a real terminal state rather than being truncated, abandoned or failed mid-run. A turn the record calls failed fails it whatever its trace says — this is where a model provider that never answered (no credits, unreachable, rejected credentials) is read: the user gets one sentence naming the failure, and this check turns JUDGED red. Otherwise it reads the terminal step of the trace, or answer-present + non-error status, so it applies to every profile class.',
        safety: 'Counts execution steps flagged as errors or guardrail violations, and also fails if a tool on the case’s must-not-call list was invoked. Zero is the pass line; any violation is a gating failure.',
        format: 'Only runs when the case declares format constraints (required / forbidden substrings, a regex, or a max length), then checks the answer against each. Abstains when the case sets no format rules, so it never penalises a free-form answer.',
        cost: 'Reports the turn’s USD LLM spend and bands it against the configured ceiling. For a coordinator it scores the coordinator’s OWN cost and annotates the expert / rollup total (display only) so portfolio sums never double-count. With no ceiling set it still shows the number but does not band.',
        tokens: 'Total input + output tokens consumed by the turn, banded against the token budget. A universal resource KPI across every class; a red here is the classic sign of an oversized context or a runaway loop.',
        budget_adherence: 'The Visible Budget (S5): the agent declares how much one turn may spend — steps, tokens, seconds — and the model is shown what is left at every tool boundary. 1.0 = the turn finished within its budget; 0.5 = a unit ran out and the turn finished GRACEFULLY (the next tool call was refused naming what is left, the answer says what remains, and the dock offers Continue where it stopped); 0.0 = the turn was KILLED by a hard stop and the answer does not say what is left. Informational, never gating. Abstains when no budget is declared on the agent or the budget is off. Owned by the Turn budget mechanism on BOUNDED.',
        cache_efficiency: 'Of the prompt tokens the turn sent, the share the provider served from its prompt cache — the stable prefix (system prompt + history) read at the cached rate instead of being re-processed. Informational: it is never banded and never fails a turn. It abstains when caching is not armed, when the provider has no prompt cache, or on the first call of a session; a share that stays at zero across a working session on a provider that caches is the sign to look at (the Prompt cache mechanism on SIGNED says whether an unchanged prefix missed).',
        latency: 'Wall-clock time from query submission to the final answer, banded against the latency target. Sourced from the turn’s own duration, so it reflects real user-perceived responsiveness.',
        tasks: 'How many discrete work units the agent executed this turn, classified by the Task Transparency framework (planning, retrieval, tool, reasoning, coordination, correction) — the chip shows the composition split. Banded by a max; high volume flags bloat or thrash even when the turn “succeeded”.',
        efficiency: 'A convergence ratio — optimal tasks ÷ tasks actually run (capped at 1.0). 1.0 means the agent did exactly the necessary work; lower means loops or detours. Abstains unless the case declares an optimal-tasks target; the raw count still lives in Task Volume.',
        parallel_efficiency: 'Latency speed-up from running parallelisable work concurrently — tasks ÷ execution waves (1.0 = fully sequential, N = N-way parallel). Applies only to classes that can genuinely parallelise (Optimize, Coordinate, flows) and abstains when no concurrency is structurally observable, so it never fabricates a 1.0 from a serial chain.',
        steps: 'The number of phases the strategic planner emitted — a planner concept, so it applies to Optimize only. Distinct from Task Volume: each planned step decomposes into one or more execution tasks (tasks ≥ steps). Banded to flag over-planning.',
        tool_subset: 'Superset check — every tool the case requires must have been called; extra calls are fine, since agents legitimately find alternative routes. Abstains when the case lists no required tools. It grades coverage of what matters, never an exact call sequence.',
        must_not_call: 'Fails if the agent invoked any tool the case explicitly forbids (e.g. a write tool on a read-only task). Abstains when no forbidden tools are declared. A hard correctness / safety guardrail.',
        surface_violation: 'OBDA-READ structural confinement: counts raw-SQL attempts a surface-only agent made that were denied fail-closed at the tool chokepoint. Informational, never gating — after the first denial the agent self-corrects to query_product, so the expected steady state is zero; a persistent nonzero is not a misbehaving agent but a PRODUCT GAP (the consumer needed something its signed products do not offer — producer backlog, made measurable). Abstains when no denial occurred.',
        self_correction: 'Counts how many times the agent had to retry or repair its own failed tool calls this turn. Zero is ideal; a high count signals brittle tool use or bad arguments. Banded by a max, and shown as a raw count even when no limit is set.',
        tool_success_rate: 'Fraction of tool calls that returned without an error. 100% is clean; anything lower means the agent hit tool errors even if it later recovered — which is why a “passing” turn can still be flagged for review.',
        no_tool_loops: 'Stall detection — fails if the same tool was called with byte-identical arguments repeatedly, indicating the agent is spinning rather than progressing. Complements the volume signals, which catch thrash with varying arguments.',
        tool_calls: 'Total number of tool calls in the turn. Catches brute-force or retry thrash that loop detection misses because the arguments differ each time. Non-gating: it flags bloat for review but never fails the turn on its own.',
        action_safety: 'For agents granted signed actions (OBDA-ACT) — of the writes the turn put to the database (each run in one transaction with its checks), the share whose every check held: the preconditions, the row bound, the postconditions. A write whose check failed was rolled back, so nothing harmful landed — but the agent proposed a write its own contract refuses, which is what this counts. An act refused before it compiled (not granted, not signed, no connection) reached no table and is not counted. Reads the same fold as every other action surface, so a prepared write a person later confirmed counts once, as how it ended. Informational; abstains when no write reached the database.',
        library_read_first: 'For agents carrying a progressive skill library of executed SQL (teradata-patterns) — of the statements the turn ran that call a function a library section teaches as a table operator (NPATH, SESSIONIZE, TD_ARIMAESTIMATE, …), the share that came AFTER the agent opened a section teaching it. The explanation names four cells: read and ran, read and failed, unread and ran, unread and failed. The rate says whether the agent consults the manual; the cells say whether consulting it mattered — the evidence for deciding to force a read. Scalar functions (TRUNC, TO_CHAR) are not counted, because they appear in almost any SQL. Informational, never a verdict: an agent may know a function without the manual. Abstains when no statement used a library operator.',
        enrichment_recall: 'For KG-grounded turns — the inverse of KG Seed Utilization: of the tables the turn\'s data tool calls actually READ (the FROM/JOIN tables of its SQL, the object of a catalog call), the fraction the knowledge-graph enrichment had SHOWN the agent beforehand. A table the agent had to find by itself — a guessed join, a catalog hunt, a follow-up whose seeding forgot the conversation — is a retrieval defect whatever the answer turned out to be, and this is the number that names it. Matched by table leaf, because the graph names the logical table and the SQL names the deployed one. Informational: abstains when no enrichment fired or the turn read no tables; every miss also feeds the graph\'s ranking prior so the table is offered next time.',
        kg_seed_utilization: 'For KG-grounded turns — the knowledge graph has two recall channels: lexical (the words the question used) and semantic (what it meant). This measures, of the entities the semantic channel reached ONLY by meaning, the fraction the turn\'s data tool calls then referenced (an entity counts as used when its name appears in a query or a tool argument — never in a result). Informational: it abstains when the channel was off, still warming or errored, when nothing was reached only by meaning, or when the turn ran no data calls. A value that stays at 0 for a week says the channel is surfacing the wrong entities; no value at all would say nothing.',
        retrieval_performed: 'For Focus (RAG) agents — confirms knowledge retrieval actually ran when the task expected it (documents returned, or a retrieval / knowledge step present). Abstains when the case explicitly does not expect retrieval.',
        grounded_overlap: 'A cheap, deterministic anti-hallucination pre-signal for Focus agents: the fraction of answer sentences whose significant words overlap the retrieved documents **and the question** (≥50% overlap counts as supported), banded against a minimum. Echoing the user’s own terms is therefore not counted as invention. It ABSTAINS when the delivered text is not the model’s claim — when the grounding gate withheld or annotated the answer, or the honesty gate rewrote it, what was delivered is the platform’s own notice (which quotes the very spans it is reporting, so scoring it would fail by construction and count one shortfall twice). Deliberately coarse: a refusal and a fluent fabrication look alike to a lexical check, so a low score here is a prompt to read the LLM Groundedness judge, which refines it — not a verdict on its own. Where BOTH decided a turn, the pair’s standing sets what this check CONTRIBUTES: if the judge cleared what this check flagged, the turn is DISPUTED — JUDGED reads “worth a look” rather than “needs you now”, and says so. This number is never rewritten; only its contribution changes.',
        citations: 'Does the ANSWER text attribute its sources? Recognizes Uderia’s canonical citation grammar — the answer’s own “Sources:” block and “(Collection: …)” markers — plus generic forms ([1], (source …), references). Deliberately distinct from the always-attached Source Documents panel: that proves retrieval happened; this proves the generated answer carries in-answer attribution, which a weak model can silently drop. Activate via Config → Trust → Quality bands → the “Citations required” flag; off by default (abstains).',
        retrieval_relevance: 'For Focus agents — the mean similarity score of the retrieved chunks, i.e. whether retrieval surfaced on-topic material rather than noise. Low relevance often explains a weak or off-target answer.',
        numeric_fidelity: 'Anti-hallucination heuristic — numbers stated in the answer should also appear in the tool results / retrieved data; a low ratio flags fabricated figures. Non-gating without an explicit threshold, so it informs rather than blocks.',
        scope_adherence: 'For KG-grounded turns — counts data-source calls that reached OUTSIDE the scope the knowledge-graph enrichment declared (system catalogs excepted); a high count means the agent wandered into unrelated databases. Abstains when no deployable scope was declared, since there is then nothing to adhere to.',
        honest_absence: 'The inverse polarity of grounding, and the only check in the stack that catches it. Every other layer asks whether what the answer ASSERTS is supported by evidence; this asks whether what it DENIES is. A turn whose every data query failed and which then reports “there are none” produces a confident false negative — not an error, not a refusal, but a fluent answer a reader cannot tell apart from a competent negative result: the grounding gate passes it (nothing out of scope), the guardrails pass it (no PII, no injection), and Tool Success Rate records the failed query as a trajectory signal while the answer still reads authoritative. Passes when the absence claim is backed by a query that SUCCEEDED — including the legitimate empty result, which is never treated as a failure. Abstains when the turn ran no raw-SQL data queries, or when the answer claims no absence at all. Reads the honesty gate’s own decision, so the score and the gate that can rewrite an answer never disagree about the same turn.',
        citation_fidelity: 'A provenance check, and the only one that reads a claim the agent makes about ITSELF. An agent carrying a progressive-disclosure skill keeps a lean always-resident root and fetches the corpus section by section on demand — so it can assert “I have fetched `master-design` and the `domain` module documentation” and have called the tool zero times. The claim reads as provenance and is not one, and nothing else in the stack contradicts it: the grounding gate passes it (nothing out of scope), the guardrails pass it (no PII, no injection), and the answer reads exactly like one written from the source. Deterministic and 0-token because the anchor index is a CLOSED VOCABULARY fixed when the skill is packaged — this never guesses whether a phrase names a section, only whether a KNOWN section name appears in a completed read claim the turn’s record does not support. An intention (“I will read `domain` next”) and a negation (“I could not open it”) are not claims and never fire it. Abstains when the agent has no library, and when the answer cites nothing — a turn that made no provenance claim has no fidelity to report, and scoring it 1.0 would flatter every agent that never cites. Non-gating: an unbacked citation is a provenance defect, not proof the answer is wrong, and the honesty gate has already disclosed it to the reader. Reads the gate’s own assessment, so the score and the control that appended that disclosure can never disagree about the same turn.',
        identifier_provenance: 'The companion to Citation Fidelity, covering what that check structurally cannot: a fabrication that cites nothing. A skill may DECLARE a closed vocabulary — an identifier pattern whose members must exist in its own library — and an id matching the pattern but absent from the library is provably invented. Boolean, because an identifier is real or it is not; there is no line to move, so it adds no threshold. The case it was built from: an agent designed five modules and settled five decisions, four of whose ids do not appear anywhere in the corpus, and not one turn made a read claim — so the citation check abstained on every one of them. The one turn that DID open a section is the one turn that produced a real id. Abstains when the skill declares no closed vocabulary (a skill saying its agent MAY name new things), and when an unbacked citation was reported instead — that verdict takes precedence and covers the same answer. Non-gating: an invented name is a provenance defect, not proof the reasoning is wrong, and the gate has already disclosed it.',
        governed_data_ratio: 'An OBDA-READ signal — of the turn’s data operations, the fraction routed through the governed, deterministic compiler (e.g. compile_metric) rather than free-formed SQL. Higher means more of the answer rests on governed, reproducible data access. Abstains when the turn performs no data operations.',
        context_utilization: 'RAG Trust Hardening: the fraction of the answer’s distinct content words that appear in the retrieved chunk text — a deterministic check that the answer actually USED the retrieved knowledge instead of ignoring it and answering from model weights (the documented “retrieved then ignored” failure mode). Advisory; banded only when an admin sets a threshold. Abstains when nothing was retrieved.',
        knowledge_freshness: 'RAG Trust Hardening: the age (days since last CDC verification) of the stalest document the turn’s retrieval actually cited. An answer grounded in stale documentation looks identical to a fresh one without this signal. Informational unless an admin sets a maximum age; abstains honestly when CDC metadata is absent (sync off / legacy documents).',
        source_consistency: 'RAG Trust Hardening (LLM judge, advisory): when two or more documents were retrieved, do they materially disagree on a fact the answer states as settled — without the answer saying so? Surfacing the disagreement beats silently picking a side. Never gates; fires only with ≥2 distinct retrieved documents.',
        plan_adherence: 'Soft and non-gating — the fraction of tool calls that stayed within the agent’s own planned tool set. Off-plan is not necessarily wrong (agents find valid alternatives), so this informs rather than blocks. Optimize only; abstains without a plan.',
        plan_execution: 'For Optimize — were the tools the planner actually planned then executed? It measures coverage of the plan by the execution trace; a low value means the agent deviated from or abandoned its plan. Informational.',
        provenance_integrity: 'A structural tamper-evidence check — the signed Execution Provenance Chain is present and sealed, with steps recorded. This is the cheap check; full cryptographic verification is a separate action. Abstains when the run carries no provenance chain.',
        context_health: 'A data-loss-under-pressure signal — fires when the turn had to drop recent context or tool detail to fit the budget (context condensation, RAG-offload, or distillation). Healthy means nothing was sacrificed; degradation warns the answer may have been formed on trimmed context.',
        error_recovery: 'A resilience signal — when errors did occur during the turn, did it nonetheless recover and complete? Informational: it distinguishes a turn that hit and survived a problem from one that failed outright.',
        routing: 'For coordinators — the expected experts were consulted (the expected set is a subset of the experts actually invoked). Abstains when the case names no expected experts. A miss means the query was routed to the wrong specialists.',
        coordination_efficiency: 'For coordinators — no redundant expert calls: the number of experts invoked stays within the allowed max and none was consulted twice. Flags wasteful fan-out.',
        expert_success: 'For coordinators — every expert the coordinator consulted returned successfully. A failed expert undermines the synthesised answer even if the coordinator itself completed.',
        answer_correctness: 'LLM-judge — is the answer factually correct against the reference / ground truth for the case? The core quality judgement for tasks that have a known-good answer.',
        answer_groundedness: 'LLM-judge for tool classes — are the answer’s claims and numbers actually supported by the tool results the agent obtained? The non-RAG analogue of the Groundedness judge; it targets support, not whether the question was addressed.',
        groundedness: 'LLM-judge for RAG — are the answer’s claims supported by the retrieved documents? Catches confident statements the sources don’t actually back.',
        scope_faithfulness: 'LLM-judge — did the answer stay faithful to the knowledge-graph trusted scope, or drift to sources / entities outside it? The judgement half of the grounding gate; it distinguishes an honest “not in scope” refusal from a confident out-of-scope answer.',
        grain_faithfulness: 'LLM-judge — did the answer address the question at the GRAIN and SUBJECT asked, or did it silently substitute a coarser grain or a different measure and present the result as the asked answer? Born from a live failure: “which customer has the most tickets?” answered with a state-level customer count — admissible, governed, fluent, and answering a different question. An explicitly DISCLOSED substitution passes: disclosure is the honest form. Two lanes: post-hoc it grades sampled/judged tool-class turns; in-path the honesty gate escalates its heuristic grain trigger to it when the agent opts in (honestyConfig.grainJudge — optional, off by default). Factual correctness is deliberately out of scope — other checks own it.',
        content_safety: 'LLM-judge — the semantic complement to the deterministic outbound guardrail. The in-path detectors catch explicit PII and closed-set toxicity formulas at zero cost; this judge covers the residue only meaning can catch: sarcastic or coded abuse, demeaning framing without trigger words, harm-enabling instructions, and indirect personal identification (quasi-identifiers that single out a real person). Advisory: it bands the Quality card and feeds the behavioral trust score — it never blocks the answer path. Professional criticism of ideas, systems, or data quality is not a failure.',
        instruction_following: 'LLM-judge — did the answer do what the user actually asked, honouring constraints, requested format, and intent? Isolated from correctness, so a right-but-non-responsive answer is still flagged.',
        synthesis_quality: 'LLM-judge — how well were multiple sources or expert results combined into one coherent answer (completeness, consistency, no contradiction)? Most relevant to coordinators and multi-document answers.',
        tool_arg_optimality: 'LLM-judge — were the arguments passed to tools well-chosen and minimal (the right query, filters, and parameters) rather than wasteful or sloppy? A quality lens on HOW tools were used, beyond whether they succeeded.',
        flow_completed: 'For flow-agents — the whole flow run finished successfully end-to-end, not merely individual nodes.',
        flow_nodes_succeeded: 'For flow-agents — every node that executed completed without error. A single failed node lowers this even if the flow reached its end.',
        flow_nodes_reached: 'For flow-agents — every node the run was expected to execute was actually reached (superset semantics; extra nodes are fine). Catches branches that skipped required work.',
        flow_forbidden_nodes: 'For flow-agents — no node that should NOT have run was executed, i.e. the flow took the correct branch. The wrong-branch detector.',
        flow_cost: 'For flow-agents — total LLM spend summed across all nodes of the run, banded against the ceiling.',
        flow_efficiency: 'For flow-agents — how efficiently the run executed its nodes; the flow analogue of task efficiency.',
    };

    // ── the MEASUREMENT PROCESS MODEL — SERVED, never declared here ───────────
    // Which stage of a turn each scored dimension measures, and the order the stages
    // run in. The single declaration is `eval/stages.py`; this file holds the RECEIVED
    // copy and nothing else.
    //
    // It replaced a local `TIER` map (Core/Resource/Tools/Knowledge/Planning/…) that had
    // two faults. It filed by MECHANISM rather than subject — a group literally called
    // "Quality" meaning "scored by an LLM panel", so `groundedness` sat there not
    // because it is about quality but because a model grades it (and judge-vs-rule is
    // already its own channel). And its groups had no inherent order, which is why it
    // needed a hand-picked `TIER_ORDER` beside it. A process model has an order by
    // construction: every surface renders in the sequence the turn actually ran.
    //
    // Filled by `ensureVocabulary`. Empty until then — `stagesReady()` lets a caller
    // tell "not loaded" from "no stage", which `stageOf` alone cannot.
    const STAGE_OF = {};            // dimension → stage key
    let STAGE_ORDER = [];           // turn order; the model's main asset
    const STAGE_LABEL = {};         // stage key → user-facing word
    const STAGE_DESC = {};          // stage key → what it measures
    // Dimensions graded by the LLM-judge panel (vs deterministic checks) — surfaced as
    // honest "how it is scored" metadata in the guide.
    // ── what an operator can actually CHANGE ──────────────────────────────────
    // Two different things look alike in this Guide and are NOT equally under your
    // control:
    //   * WHICH dimensions a Trust Circle station watches — fixed in code (the station
    //     key lists in agentPerformanceHandler / trust_behavior). Not configurable.
    //   * A dimension's PASS LINE — configurable here, but only for DETERMINISTIC
    //     dimensions: an LLM-judge dimension has no threshold to move, because a model
    //     returns the verdict.
    // Keys mirror the scorers' own `_cuts(case, "<key>")` argument and the admin
    // coercion allow-list (api/admin_routes.py). `dflt` is the CODE fallback used when
    // the admin slider is unset — most are unset by default, which means the dimension
    // is measured but not banded until someone tunes it.
    const TUNING = {
        grounded_overlap:    { key: 'min_grounded' },
        scope_adherence:     { key: 'max_out_of_scope_dbs' },
        numeric_fidelity:    { key: 'min_numeric_fidelity' },
        retrieval_relevance: { key: 'min_retrieval_relevance' },
        context_utilization: { key: 'min_context_utilization' },
        citation_fidelity:   { key: 'min_citation_fidelity' },
        knowledge_freshness: { key: 'max_knowledge_age_days' },
        governed_data_ratio: { key: 'min_governed_data_ratio' },
        tool_success_rate:   { key: 'min_tool_success_rate' },
        no_tool_loops:       { key: 'max_tool_repeats' },
        tool_calls:          { key: 'max_tool_calls' },
        self_correction:     { key: 'max_self_corrections' },
        parallel_efficiency: { key: 'min_parallel_efficiency' },
        steps:               { key: 'max_steps' },
        tasks:               { key: 'max_tasks' },
        latency:             { key: 'max_latency_ms' },
        tokens:              { key: 'max_tokens' },
        cost:                { key: 'max_cost_usd' },
        citations:           { key: 'require_citations', flag: true },
    };

    //: Dimensions that report a NUMBER and never return a verdict (`passed=None` on every
    //: path in their scorer). Saying "pass/fail is structural" for these would claim a
    //: decision that does not exist — the same over-claim class as reporting an absence of
    //: failure as a success. Mirrors the reasoning behind `_HAS_THRESHOLD` above.
    const INFORMATIONAL = new Set([
        'plan_execution', 'plan_adherence', 'efficiency', 'context_health', 'error_recovery',
        'surface_violation',  // OBDA-READ Phase C: a denial COUNT (product-gap signal), never a verdict
        'kg_seed_utilization',  // a FRACTION of bridged entities used — measured, never judged
        'enrichment_recall',    // a FRACTION of read tables the enrichment had shown — measured, never judged
        'action_safety',        // OBDA-ACT: a FRACTION of action writes whose checks held — measured, never judged
        'library_read_first',   // a FRACTION of library-operator statements run after a read — measured, never judged
        'cache_efficiency',     // S2: the cached SHARE of prompt tokens — a cost fact, never a verdict
        'budget_adherence',     // S5: within / graceful / killed against the declared budget — a fact, never a verdict
    ]);

    // ── the metric spec: ONE source for label / unit / direction ────────────────
    // `thresholdSliders.js` (admin page) renders the SAME metrics and used to carry its
    // own copy of these three fields. A silent divergence there would have the Guide
    // print a band backwards — `Healthy <= 75%` for a higher-is-better metric — which is
    // worse than printing nothing. This module is the canonical dimension registry and
    // loads first on every page, so it owns the spec and the slider reads it; the grammar
    // gate asserts the two key sets agree in BOTH directions.
    //   dir 'lower'  → smaller is better: Healthy <= h, Danger > d
    //   dir 'higher' → larger  is better: Healthy >= h, Danger < d
    const THRESHOLD_SPEC = {
        max_cost_usd:             { label: 'Cost',              unit: '$',  dir: 'lower'  },
        max_tokens:               { label: 'Tokens',            unit: '',   dir: 'lower'  },
        max_latency_ms:           { label: 'Latency',           unit: 'ms', dir: 'lower'  },
        max_tasks:                { label: 'Tasks',             unit: '',   dir: 'lower'  },
        min_parallel_efficiency:  { label: 'Parallel speedup',  unit: '×', dir: 'higher' },
        max_steps:                { label: 'Plan steps',        unit: '',   dir: 'lower'  },
        max_self_corrections:     { label: 'Self-corrections',  unit: '',   dir: 'lower'  },
        min_tool_success_rate:    { label: 'Tool success',      unit: '%',  dir: 'higher' },
        max_tool_repeats:         { label: 'Repeat calls',      unit: '',   dir: 'lower'  },
        max_tool_calls:           { label: 'Tool calls',        unit: '',   dir: 'lower'  },
        max_out_of_scope_dbs:     { label: 'Out-of-scope DBs',  unit: '',   dir: 'lower'  },
        min_numeric_fidelity:     { label: 'Numeric fidelity',  unit: '%',  dir: 'higher' },
        min_grounded:             { label: 'Grounded',          unit: '%',  dir: 'higher' },
        min_retrieval_relevance:  { label: 'Retrieval sim',     unit: '%',  dir: 'higher' },
        min_context_utilization:  { label: 'Context use',       unit: '%',  dir: 'higher' },
        min_citation_fidelity:    { label: 'Citation fidelity', unit: '%',  dir: 'higher' },
        max_knowledge_age_days:   { label: 'Knowledge age',     unit: 'd',  dir: 'lower'  },
        min_governed_data_ratio:  { label: 'Governed data',     unit: '%',  dir: 'higher' },
    };

    /** Render one cut point in its own units. Mirrors the admin slider's readout so the
     *  two surfaces never print the same number differently. */
    function fmtThreshold(key, v) {
        if (v == null) return '—';
        const sp = THRESHOLD_SPEC[key] || {};
        const n = Number(v);
        if (!isFinite(n)) return String(v);
        if (sp.unit === '$') return '$' + n.toFixed(2);
        if (sp.unit === '%') return Math.round(n * 100) + '%';
        if (sp.unit === 'ms') return n >= 60000 ? (n / 60000).toFixed(1).replace(/\.0$/, '') + 'min' : n >= 1000 ? (n / 1000).toFixed(1) + 's' : n + 'ms';
        if (sp.unit === 'd') return n + (n === 1 ? ' day' : ' days');
        if (sp.unit === '×') return n.toFixed(2).replace(/\.?0+$/, '') + '×';
        return n.toLocaleString();
    }

    /** "Healthy <= $0.30 · Danger > $0.60", direction-aware; omits a cut that is unset. */
    function fmtBand(key, band) {
        if (!band) return '';
        const dir = (THRESHOLD_SPEC[key] || {}).dir || 'lower';
        const parts = [];
        if (band.healthy != null) {
            parts.push('Healthy ' + (dir === 'lower' ? '≤ ' : '≥ ') + fmtThreshold(key, band.healthy));
        }
        if (band.danger != null) {
            parts.push('Danger ' + (dir === 'lower' ? '> ' : '< ') + fmtThreshold(key, band.danger));
        }
        return parts.join(' · ');
    }

    // ── live bands ──────────────────────────────────────────────────────────────
    // The Guide reports what IS configured, not what ships. Stating the shipped default
    // ("unset by default") is a constant wearing a live reading's clothes: on a tuned
    // deployment every one of those lines was false, and false in the direction that
    // matters — it told an operator nothing was banded while everything was.
    //
    // Fetched once per page and cached; `invalidateThresholds()` drops the cache so an
    // admin who saves new bands does not keep reading the old ones in the same session.
    // While it is in flight the line renders as PENDING, never as a guess.
    let _thresh = null;            // {key: {healthy?, danger?}}  — null until loaded
    let _threshFallback = null;    // {key: {danger}}  — the scorers' own code fallbacks
    let _threshPromise = null;
    let _threshFailed = false;

    function ensureThresholds() {
        if (_thresh || _threshFailed) return Promise.resolve(_thresh);
        if (_threshPromise) return _threshPromise;           // in-flight dedupe
        const tok = (() => { try { return localStorage.getItem('tda_auth_token'); } catch (e) { return null; } })();
        _threshPromise = fetch('/api/v1/evals/scoring-thresholds', {
            headers: tok ? { Authorization: 'Bearer ' + tok } : {},
        })
            .then((r) => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
            .then((d) => {
                _thresh = (d && d.thresholds) || {};
                _threshFallback = (d && d.code_fallbacks) || {};
                return _thresh;
            })
            .catch((e) => {
                // Fail toward SILENCE about the numbers, never toward the old lie: the
                // line says it could not read them rather than asserting a default.
                console.warn('QualityDimensions: could not read live scoring bands', e);
                _threshFailed = true;
                return null;
            })
            .finally(() => { _threshPromise = null; });
        return _threshPromise;
    }

    function invalidateThresholds() {
        _thresh = null; _threshFallback = null; _threshFailed = false;
    }

    const ADMIN_PATH = 'Administration → App Config → Trust → Quality bands';

    /** The one line that answers "can I change what makes this pass — and what is it now?" */
    function tuningLine(k) {
        // A guarantee and a moment have no pass line: one is a promise, the other a point
        // in the turn. "A yes/no check — nothing to configure" answered a question that
        // was never asked of them, in a measurement's vocabulary.
        const c = classOf(k);
        if (c === 'stage' || c === 'station' || c === 'property') return '';
        // A pass line belongs to a SCORED measure — a grade compared after the answer. A
        // registered MECHANISM (any of the four kinds) has none: what it lets you change is
        // its own `you can change` / `fixed` rows, served for every one of them. Giving it
        // the measure's "a yes/no check — nothing to configure" put a row under "after the
        // turn" that is about nothing after the turn (on 14 mechanisms it was that group's
        // only row) and contradicted the settings above it (the grounding gate has a mode).
        if (ENTRY_CLASS[k]) return '';
        if (JUDGE_DIMS.has(k)) {
            return '<div class="qd-tune qd-tune-judge">No pass line to set — an LLM panel returns '
                 + 'the verdict. You can turn judges on/off and change how often they sample, '
                 + 'but not a threshold.</div>';
        }
        const t = TUNING[k];
        if (!t && INFORMATIONAL.has(k)) {
            return '<div class="qd-tune qd-tune-info">No pass/fail at all — this reports a number '
                 + 'for context and never convicts a turn. Nothing to configure.</div>';
        }
        if (!t) {
            return '<div class="qd-tune">A yes/no check — it either holds or it does not. '
                 + 'No score, and nothing to configure.</div>';
        }
        const tail = ' · set as <code>' + esc(t.key) + '</code> under ' + ADMIN_PATH + '.';

        // A boolean expectation, not a band: its live state is on/off.
        if (t.flag) {
            const on = !!(_thresh && _thresh[t.key]);
            return '<div class="qd-tune" data-qd-tune="' + esc(k) + '">Currently: <strong>'
                 + (_thresh ? (on ? 'required' : 'not required') : '…')
                 + '</strong>' + (on ? '' : ' — the check abstains while it is off') + tail + '</div>';
        }
        if (!_thresh) {
            return '<div class="qd-tune qd-tune-pending" data-qd-tune="' + esc(k) + '">'
                 + (_threshFailed
                     ? 'Could not read the live bands for <code>' + esc(t.key) + '</code>. Set them under ' + ADMIN_PATH + '.'
                     : 'Reading the live bands…') + '</div>';
        }
        const band = _thresh[t.key];
        const live = fmtBand(t.key, band);
        if (live) {
            return '<div class="qd-tune" data-qd-tune="' + esc(k) + '">Bands now: <strong>'
                 + esc(live) + '</strong>' + tail + '</div>';
        }
        // Unset — but a scorer may still band it in code, and saying "not banded" there
        // would be the same over-claim in the opposite direction.
        const fb = _threshFallback && _threshFallback[t.key];
        const fbTxt = fb ? fmtBand(t.key, fb) : '';
        return '<div class="qd-tune" data-qd-tune="' + esc(k) + '">Bands now: <strong>not set</strong>'
             + (fbTxt ? ' — the scorer falls back to <strong>' + esc(fbTxt) + '</strong>'
                      : ' — measured, but not banded until you set it')
             + tail + '</div>';
    }

    //: The LLM judges — a mirror of the scorer registry's `kind == judge`, pinned
    //: equal by test_assurance_points. A judge added on the backend and not here
    //: would render as deterministic, which is the one thing this mark exists to
    //: prevent: a model's opinion presented as a rule's measurement.
    const JUDGE_DIMS = new Set(['source_consistency', 'answer_correctness', 'answer_groundedness', 'groundedness',
        'scope_faithfulness', 'instruction_following', 'synthesis_quality', 'tool_arg_optimality',
        'content_safety', 'grain_faithfulness']);

    // ── lookups (graceful fallback so an underscore key can never leak) ────────────
    const titleCase = (k) => String(k || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    const label = (k) => (DIM[k] && DIM[k].label) || titleCase(k);
    const desc = (k) => (DIM[k] && DIM[k].desc) || '';           // short — used by the tooltip
    const long = (k) => LONG[k] || desc(k);                       // fuller — used by the guide
    /** The stage a dimension is measured at, or '' when unknown.
     *
     *  '' is deliberately NOT an "other" bucket: an unstaged dimension is a registry gap
     *  the backend gate fails on, and quietly filing it under a catch-all is how a new
     *  scorer lands in a group nobody reads. A surface that gets '' should say the check
     *  is unclassified rather than invent a home for it. */
    const stageOf = (k) => STAGE_OF[k] || '';
    /** Has the served model arrived? `stageOf` returning '' cannot distinguish "the
     *  vocabulary has not loaded" from "this dimension has no stage", and a grouping
     *  surface that cannot tell them apart renders a confident wrong answer — the exact
     *  defect `stationsReady` exists for, one model over. */
    function stagesReady() { return STAGE_ORDER.length > 0; }
    /** The stages in TURN ORDER, filtered to those the given dimensions actually reach.
     *  An empty stage is omitted, never rendered as a heading over nothing: on a real
     *  turn an ABSENT stage is a fact about the agent (a knowledge agent has no `act`),
     *  and an empty header would read as "this ran and measured nothing". */
    function groupByStage(keys) {
        const seen = new Set(keys || []);
        const out = [];
        STAGE_ORDER.forEach((st) => {
            const members = [...seen].filter((k) => STAGE_OF[k] === st);
            if (members.length) out.push({ stage: st, label: STAGE_LABEL[st] || st,
                                           desc: STAGE_DESC[st] || '', keys: members });
        });
        return out;
    }
    /** Dimensions the served model does not place — surfaced, never hidden. */
    const unstaged = (keys) => (keys || []).filter((k) => !STAGE_OF[k]);
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    // ── one-time CSS (Uderia styleguide: CSS vars, monochrome, theme-aware) ────────
    function injectCss() {
        if (document.getElementById('qd-css')) return;
        const s = document.createElement('style');
        s.id = 'qd-css';
        s.textContent = `
        [data-dim]{cursor:help;}
        #qd-tooltip{position:fixed;z-index:100050;max-width:288px;pointer-events:none;
            background:var(--bg-secondary,#1a1a1a);color:var(--text-primary,#e5e7eb);
            border:1px solid var(--border-primary,rgba(127,127,127,.35));border-radius:8px;
            padding:9px 11px;box-shadow:0 8px 28px rgba(0,0,0,.28);
            font-size:12px;line-height:1.45;opacity:0;transform:translateY(3px);
            transition:opacity .12s ease,transform .12s ease;}
        #qd-tooltip.qd-show{opacity:1;transform:translateY(0);}
        #qd-tooltip .qd-tt-title{font-weight:700;color:var(--text-primary,#f3f4f6);margin-bottom:2px;}
        #qd-tooltip .qd-tt-desc{color:var(--text-muted,#9ca3af);}
        #qd-tooltip .qd-tt-note{color:var(--text-primary,#e5e7eb);margin-top:5px;
            padding-top:5px;border-top:1px solid var(--border-secondary,rgba(127,127,127,.2));}
        #qd-tooltip .qd-tt-hint{color:var(--teradata-orange,#F15F22);margin-top:6px;
            font-size:10.5px;font-weight:600;letter-spacing:.02em;
            display:flex;align-items:center;gap:4px;}
        .qd-guide-btn{display:inline-flex;align-items:center;gap:5px;cursor:pointer;
            font-size:11px;font-weight:600;padding:2px 9px;border-radius:9999px;
            color:var(--text-muted,#9ca3af);background:transparent;
            border:1px solid var(--border-secondary,rgba(127,127,127,.25));
            transition:color .12s ease,border-color .12s ease,background .12s ease;}
        .qd-guide-btn:hover{color:var(--teradata-orange,#F15F22);
            border-color:rgba(241,95,34,.5);background:rgba(241,95,34,.10);}
        #qd-modal-backdrop{position:fixed;inset:0;z-index:100040;
            background:rgba(0,0,0,.6);
            display:flex;align-items:center;justify-content:center;padding:20px;
            opacity:0;transition:opacity .15s ease;}
        #qd-modal-backdrop.qd-show{opacity:1;}
        #qd-modal{width:100%;max-width:660px;max-height:82vh;display:flex;flex-direction:column;
            background:var(--bg-primary,#171717);color:var(--text-primary,#e5e7eb);
            border:1px solid var(--border-primary,rgba(127,127,127,.35));border-radius:14px;
            box-shadow:0 24px 60px rgba(0,0,0,.5);transform:scale(.97);transition:transform .15s ease;}
        #qd-modal-backdrop.qd-show #qd-modal{transform:scale(1);}
        .qd-modal-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;
            padding:16px 18px 12px;border-bottom:1px solid var(--border-secondary,rgba(127,127,127,.2));}
        .qd-modal-title{font-size:15px;font-weight:700;color:var(--text-primary);}
        .qd-modal-sub{font-size:11.5px;color:var(--text-muted);margin-top:2px;}
        .qd-modal-close{flex-shrink:0;cursor:pointer;color:var(--text-muted);background:transparent;
            border:1px solid var(--border-secondary,rgba(127,127,127,.25));border-radius:8px;
            width:28px;height:28px;display:inline-flex;align-items:center;justify-content:center;
            transition:color .12s ease,border-color .12s ease;}
        .qd-modal-close:hover{color:var(--text-primary);border-color:var(--border-primary);}
        .qd-search-wrap{padding:12px 18px 0;}
        .qd-search{width:100%;font-size:13px;padding:8px 11px;border-radius:8px;
            color:var(--text-primary);background:var(--bg-secondary,rgba(127,127,127,.06));
            border:1px solid var(--border-secondary,rgba(127,127,127,.25));outline:none;}
        .qd-search:focus{border-color:var(--teradata-orange,#F15F22);}
        .qd-modal-body{overflow-y:auto;padding:12px 18px 18px;}
        .qd-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:10.5px;color:var(--text-muted);
            padding:6px 0 12px;margin-bottom:6px;border-bottom:1px solid var(--border-secondary,rgba(127,127,127,.18));}
        .qd-legend .qd-dot{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:4px;vertical-align:middle;}
        .qd-tier-h{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
            color:var(--text-muted);margin:16px 0 3px;}
        .qd-tier-sub{font-size:11px;color:var(--text-muted);opacity:.8;margin-bottom:8px;}
        .qd-row{padding:8px 10px;border-radius:8px;margin-bottom:5px;
            border:1px solid var(--border-secondary,rgba(127,127,127,.16));background:var(--bg-secondary,rgba(127,127,127,.04));}
        .qd-row.qd-hi{border-color:var(--teradata-orange,#F15F22);}
        .qd-row-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
        .qd-row-label{font-size:13px;font-weight:600;color:var(--text-primary);}
        .qd-tag{font-size:9.5px;font-weight:600;padding:1px 6px;border-radius:9999px;
            color:var(--text-muted);border:1px solid var(--border-secondary,rgba(127,127,127,.28));}
        .qd-row-desc{font-size:12px;line-height:1.5;color:var(--text-muted);margin-top:4px;}
        .qd-facet-wrap{padding:0 18px;}
        .qd-facets{display:flex;align-items:center;gap:6px;flex-wrap:wrap;
            padding:8px 0 2px;border-bottom:1px solid var(--border-secondary);}
        .qd-facet-lead{font-size:10px;letter-spacing:.06em;text-transform:uppercase;
            color:var(--text-muted);opacity:.75;margin-right:2px;}
        .qd-facet-sep{width:1px;height:14px;background:var(--border-secondary);margin:0 4px;}
        .qd-facet{display:inline-flex;align-items:center;gap:5px;font-size:11px;
            padding:2px 9px;border-radius:9999px;cursor:pointer;
            border:1px solid var(--border-secondary);background:transparent;
            color:var(--text-muted);}
        .qd-facet:hover{color:var(--text-primary);border-color:var(--text-muted);}
        .qd-facet.on{color:var(--teradata-orange,#F15F22);
            border-color:var(--teradata-orange,#F15F22);
            background:rgba(241,95,34,.08);}
        .qd-facet:focus-visible{outline:2px solid var(--teradata-orange,#F15F22);outline-offset:1px;}
        .qd-facet-n{font-size:10px;opacity:.7;font-variant-numeric:tabular-nums;}
        .qd-facet-clear{border-style:dashed;}
        /* IFOC engine tags — colour mirrors the engines' canonical palette; the dot
           is decorative colour only (the hover names the engine), so no text sits on
           the colour and light/dark themes both keep contrast. */
        .qd-ifoc{display:inline-flex;align-items:center;gap:3px;margin-left:2px;}
        .qd-ifoc-dot{display:inline-block;width:8px;height:8px;border-radius:9999px;
            border:1px solid var(--border-primary);flex:none;cursor:default;}
        .qd-facet .qd-ifoc-dot{width:7px;height:7px;}
        /* The station assignment chip — every measurement is counted at exactly one
           station (the partition); clicking opens that station's own Guide entry. */
        .qd-st-chip{font-size:9px;letter-spacing:.08em;font-weight:700;
            font-family:var(--font-mono, ui-monospace, monospace);
            padding:1px 6px;border-radius:4px;cursor:pointer;
            border:1px solid var(--border-secondary);background:transparent;
            color:var(--text-muted);}
        .qd-st-chip:hover{color:var(--teradata-orange,#F15F22);
            border-color:var(--teradata-orange,#F15F22);}
        .qd-st-chip:focus-visible{outline:2px solid var(--teradata-orange,#F15F22);
            outline-offset:1px;}
        .qd-class-h{display:flex;align-items:baseline;gap:8px;font-size:13px;font-weight:700;
            color:var(--text-primary);margin:16px 0 2px;letter-spacing:.01em;}
        .qd-class-n{font-size:11px;font-weight:500;color:var(--text-muted);
            font-variant-numeric:tabular-nums;}
        .qd-row-rel{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-top:6px;}
        .qd-rel-lead{font-size:10px;letter-spacing:.06em;text-transform:uppercase;
            color:var(--text-muted);opacity:.75;}
        .qd-rel-chip{font-size:11px;padding:1px 8px;border-radius:9999px;cursor:pointer;
            border:1px solid var(--border-secondary);background:transparent;
            color:var(--text-primary);}
        .qd-rel-chip:hover{border-color:var(--teradata-orange,#F15F22);
            color:var(--teradata-orange,#F15F22);}
        .qd-rel-chip:focus-visible{outline:2px solid var(--teradata-orange,#F15F22);
            outline-offset:1px;}
        /* The judge tag: quieter than the name it qualifies, and NOT a colour of its
           own — colour is attention priority everywhere on these surfaces, and "a model
           opined here" is not an attention level. Same wording the Quality card and the
           runs ledger use. */
        .qd-rel-kind{opacity:.55;font-size:10px;}
        .qd-mod{display:inline-flex;align-items:center;gap:2px;font-weight:600;
            color:var(--text-primary);background:none;border:0;padding:0;font:inherit;}
        .qd-mod-route{cursor:pointer;}
        .qd-mod-route:hover{text-decoration:underline;text-underline-offset:3px;}
        .qd-linked{outline:1px solid var(--teradata-orange,#F15F22);outline-offset:2px;
            border-radius:3px;}

        .qd-rel-same,.qd-mod .qd-mark{cursor:pointer;}
        .qd-rel-same,.qd-mark{display:inline-flex;align-items:center;justify-content:center;
            width:12px;height:12px;margin-left:5px;border-radius:2px;flex:0 0 auto;
            font-size:8.5px;font-weight:700;line-height:1;letter-spacing:0;
            /* Dark chip, white letter, in BOTH themes. Inverting --text-primary gave a
               WHITE chip with a dark letter in dark mode: the opposite reading. A fixed
               dark wash keeps one badge everywhere; the hairline separates it from a dark
               card, where chip and background would otherwise merge. */
            background:rgba(15,23,42,.85);color:#fff;
            border:1px solid var(--border-secondary);}
        /* Judges also carry a DOTTED border. Dash and dot are different strokes: a
           SPARSE DASH is the absence family (na, pending, unknown) on every trust
           surface, and stays theirs. A DOT is the kind channel — "a model opined".
           Never the sole carrier: the ·judge tag rides beside it, so a reader who
           cannot resolve 1px stroke styles loses nothing. */
        .qd-rel-chip.qd-judge{border-style:dotted;}
    .qd-tune { font-size: 11px; line-height: 1.5; margin-top: 6px; padding: 5px 8px;
        border-radius: 5px; color: var(--text-muted);
        background: var(--hover-bg-strong, rgba(127,127,127,0.08));
        border-left: 2px solid var(--border-primary); }
    .qd-tune code { font-family: var(--font-mono, ui-monospace, monospace); font-size: 10.5px;
        color: var(--text-primary); }
    .qd-tune strong { color: var(--text-primary); font-weight: 600; }
    /* A judge dimension has nothing to tune — dashed edge marks the difference in SHAPE,
       so the distinction survives without spending a colour (see the trust grammar). */
    .qd-tune-judge { border-left-style: dashed; }
    /* Informational: dotted, matching the abstained border already used by qd-status —
       one vocabulary for "this produced no verdict", not a third invention. */
    .qd-tune-info { border-left-style: dotted; }
        .qd-row-key{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px;
            color:var(--text-muted);opacity:.7;}
        .qd-empty{font-size:12.5px;color:var(--text-muted);padding:22px 4px;text-align:center;}
        .qd-scope{display:flex;flex-wrap:wrap;gap:6px;padding:10px 18px 0;}
        .qd-scope-btn{font-size:11px;font-weight:600;padding:3px 11px;border-radius:9999px;cursor:pointer;
            color:var(--text-muted);background:transparent;border:1px solid var(--border-secondary,rgba(127,127,127,.25));
            transition:color .12s ease,border-color .12s ease,background .12s ease;}
        .qd-scope-btn:hover{color:var(--text-primary);}
        .qd-scope-btn.on{color:var(--teradata-orange,#F15F22);border-color:rgba(241,95,34,.5);background:rgba(241,95,34,.10);}
        .qd-status{display:inline-flex;align-items:center;gap:5px;font-size:9.5px;font-weight:700;
            padding:1px 8px;border-radius:9999px;white-space:nowrap;}
        .qd-status .qd-sdot{width:8px;height:8px;border-radius:50%;display:inline-block;}
        .qd-row-bound{font-size:11px;color:var(--text-muted);margin-top:6px;padding-top:6px;
            border-top:1px dashed var(--border-secondary,rgba(127,127,127,.2));
            display:flex;flex-wrap:wrap;gap:4px 10px;}
        .qd-row-bound b{color:var(--text-primary);font-weight:600;}
        .qd-row-bound .qd-fail{color:var(--trust-danger, #ef4444);font-weight:600;}
        .qd-row-gap{border-left:2px solid var(--trust-warn,#b45309);padding-left:.5rem}
    .qd-row-note{font-size:11px;color:var(--text-muted);margin-top:5px;font-style:italic;}
        .qd-row-lim{display:flex;gap:8px;align-items:baseline;margin-top:5px;font-size:11px;}
        .qd-lim-lead{flex:0 0 auto;font-family:var(--font-mono,ui-monospace,monospace);
            font-size:9px;letter-spacing:.08em;text-transform:uppercase;
            color:var(--text-muted);opacity:.75;min-width:82px;}
        .qd-lim-body{min-width:0;color:var(--text-muted);text-wrap:pretty;}
        /* ── the one anatomy: hierarchy by WEIGHT, three sizes ─────────────────
           title + can strongest · description next · relations regular · meta quiet.
           Two visual grammars for links and labels: a chip is always a link to an
           entry, a tag is always a classification. Engine dots and the M/S badges
           keep their own meanings, as the legend defines them. */
        .qd-row{padding:12px 14px 10px;gap:7px;display:grid;}
        .qd-row-top{gap:8px;}
        .qd-row-label{font-size:14px;letter-spacing:-.005em;}
        .qd-tag-kind{color:var(--teradata-orange,#F15F22);border-color:rgba(241,95,34,.35);
            background:rgba(241,95,34,.07);text-transform:uppercase;letter-spacing:.06em;
            font-size:9px;}
        .qd-row-desc{margin-top:0;font-size:12.5px;color:var(--text-secondary,var(--text-muted));}
        .qd-can{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
        .qd-can-lead{font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;
            letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);opacity:.8;}
        .qd-can-q{font-size:13px;font-weight:500;color:var(--text-primary);}
        .qd-pill{font-size:11.5px;font-weight:500;padding:2px 10px;border-radius:9999px;
            border:1px solid var(--border-primary,rgba(127,127,127,.4));color:var(--text-primary);
            cursor:default;}
        /* Solid for the hard stop — the one fate that deserves weight on the page. A
           fixed dark wash with a white word, in BOTH themes, like the M/S badges. */
        .qd-pill-stop{background:rgba(15,23,42,.85);color:#fff;border-color:rgba(15,23,42,.85);}
        .qd-pill-none{border-style:dashed;color:var(--text-muted);}
        /* ── the groups: one grid for every fact, three type sizes ──────────────
           A group is a soft band and a list of rows. Every row has the lead anchored in
           a 112px column on the left and its value wrapping in the column to the right —
           chips, a list, a table or a sentence alike — so the eye reads the terms as one
           list and a fifth chip never wraps under the lead. */
        .qd-rel-grp{display:grid;gap:0;}
        .qd-rel-grp-h{font-family:var(--font-mono,ui-monospace,monospace);font-size:9px;
            letter-spacing:.1em;text-transform:uppercase;color:var(--text-muted);
            border-bottom:1px solid var(--border-primary,rgba(127,127,127,.3));
            padding:0 8px 4px;margin:12px 0 0;}
        .qd-rel-grp .qd-row-rel,.qd-rel-grp .qd-row-lim{position:relative;margin-top:0;
            padding:6px 8px 6px 128px;gap:5px;align-items:flex-start;min-height:28px;}
        .qd-rel-grp .qd-row-rel+.qd-row-rel{border-top:1px solid var(--border-secondary,rgba(127,127,127,.12));}
        /* The folded after-the-turn group: its header is the summary line, with the counts. */
        .qd-rel-fold>summary{cursor:pointer;list-style:none;display:flex;align-items:center;gap:6px;}
        .qd-rel-fold>summary::-webkit-details-marker{display:none;}
        .qd-rel-fold>summary::before{content:'';width:0;height:0;border-left:5px solid var(--text-muted);
            border-top:4px solid transparent;border-bottom:4px solid transparent;transition:transform .12s;}
        .qd-rel-fold[open]>summary::before{transform:rotate(90deg);}
        .qd-fold-n{text-transform:none;letter-spacing:0;opacity:.8;}
        /* A property beside the module consulted for it, in the lead column's place. */
        .qd-rel-grp .qd-pair-k{position:absolute;left:8px;top:8px;width:112px;font-size:11px;
            line-height:1.25;color:var(--text-muted);}
        .qd-mod-none{font-size:11.5px;color:var(--text-muted);}
        /* A guarantee's MEMBERS — what its colour is the worst of. One edge, one mark, and
           the colour rule written on the header, so "worst of its members" points at rows. */
        .qd-mem{border-left:2px solid var(--teradata-orange,#F15F22);padding-left:8px;}
        .qd-mem>.qd-rel-grp-h{display:flex;align-items:center;gap:6px;color:var(--text-primary);}
        .qd-mem-rule{color:var(--text-muted);text-transform:none;letter-spacing:0;font-weight:400;}
        .qd-rel-grp .qd-rel-lead,.qd-rel-grp .qd-lim-lead{position:absolute;left:8px;top:9px;
            width:112px;min-width:0;font-size:9.5px;line-height:1.2;opacity:.85;}
        .qd-rel-grp .qd-rel-chip,.qd-rel-grp .qd-mod,.qd-rel-grp .qd-st-chip{margin:1px 0;}
        .qd-rel-grp .qd-mod{font-size:11.5px;}
        .qd-rel-lead.u-tip,.qd-lim-lead.u-tip,.qd-rel-k.u-tip{cursor:help;
            border-bottom:1px dotted var(--border-primary,rgba(127,127,127,.5));}
        /* The kind and count of what a row lists, beside the lead. */
        .qd-kindn{font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;
            letter-spacing:.06em;text-transform:uppercase;color:var(--text-muted);
            border-right:1px solid var(--border-primary,rgba(127,127,127,.4));
            padding:3px 8px 3px 0;margin-right:4px;white-space:nowrap;}
        /* A fact row's body: a sentence, a trigger list, a settings table or the folded why. */
        .qd-fact{flex:1 1 100%;min-width:0;font-size:12px;line-height:1.5;
            color:var(--text-secondary,var(--text-primary));padding-top:1px;}
        .qd-fact-pre{font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;
            letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);display:block;margin-bottom:2px;}
        .qd-fact-never{font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;
            letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);margin-right:4px;}
        /* Numbered lists (triggers, steps): the text aligns with every other value in the
           column and the number HANGS in the gutter to its left — the app's base styles
           strip list markers, so the number is drawn, never inherited. */
        .qd-trig,.qd-steps{margin:0;padding-left:0;display:grid;gap:3px;counter-reset:n;}
        .qd-trig li,.qd-steps li{position:relative;list-style:none;padding-left:0;counter-increment:n;}
        .qd-trig li::before,.qd-steps li::before{content:counter(n);position:absolute;left:-22px;top:2px;
            width:14px;height:14px;border-radius:50%;font-family:var(--font-mono,ui-monospace,monospace);
            font-size:9px;line-height:14px;text-align:center;color:var(--text-muted);
            border:1px solid var(--border-primary,rgba(127,127,127,.4));}
        .qd-set{margin:0;display:grid;grid-template-columns:max-content minmax(0,1fr);gap:3px 14px;align-items:baseline;}
        .qd-set dt code{font-family:var(--font-mono,ui-monospace,monospace);font-size:10.5px;
            color:var(--text-primary);background:var(--hover-bg-strong,rgba(127,127,127,.08));
            border-radius:3px;padding:0 5px;}
        .qd-set dd{margin:0;}
        /* A reading block: the surface under the lead, a label → phrase table beside it. */
        .qd-rel-grp .qd-lead-sub{position:absolute;left:8px;top:22px;width:112px;
            font-size:10px;line-height:1.25;color:var(--text-muted);opacity:.8;}
        .qd-sub{margin:0;display:grid;grid-template-columns:max-content minmax(0,1fr);gap:3px 14px;align-items:baseline;}
        .qd-sub dt{font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;letter-spacing:.06em;
            text-transform:uppercase;color:var(--text-muted);padding-top:2px;white-space:nowrap;}
        .qd-sub dd{margin:0;color:var(--text-secondary,var(--text-primary));}
        .qd-why summary{cursor:pointer;color:var(--text-muted);font-style:italic;list-style:none;
            display:inline-flex;gap:6px;align-items:center;}
        .qd-why summary::-webkit-details-marker{display:none;}
        .qd-why summary::before{content:'';width:0;height:0;border-left:5px solid var(--text-muted);
            border-top:4px solid transparent;border-bottom:4px solid transparent;transition:transform .12s;}
        .qd-why[open] summary::before{transform:rotate(90deg);}
        .qd-why p{margin:4px 0 0;color:var(--text-secondary,var(--text-primary));}
        /* The tuning line inside a fact row loses its box: the row is the box. */
        .qd-fact .qd-tune{margin-top:0;padding:0;background:none;border-left:0;font-size:12px;}
        .qd-meta{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:2px;
            padding-top:6px;border-top:1px solid var(--border-secondary,rgba(127,127,127,.12));}
        .qd-meta .qd-tag{text-transform:none;letter-spacing:0;}
        /* The id mark: the wire key off the reading line — shown on hover, copied on click.
           The key itself stays in the DOM (search, assistive tech, the copy fallback). */
        .qd-id{margin-left:auto;display:inline-flex;align-items:center;gap:4px;
            font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;letter-spacing:.08em;
            text-transform:uppercase;color:var(--text-muted);background:none;
            border:1px solid var(--border-secondary,rgba(127,127,127,.25));border-radius:4px;
            padding:1px 6px;cursor:pointer;}
        .qd-id:hover{color:var(--text-primary);border-color:var(--border-primary);}
        .qd-id.qd-id-done{color:var(--teradata-orange,#F15F22);border-color:var(--teradata-orange,#F15F22);}
        .qd-id .qd-row-key{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;}
        /* The folded facet bar: group names first, the open group's pills beneath. */
        .qd-facet-group.open{color:var(--text-primary);border-color:var(--text-muted);}
        .qd-facet-group.open.on{color:var(--teradata-orange,#F15F22);
            border-color:var(--teradata-orange,#F15F22);}
        .qd-facet-open{border-bottom:1px solid var(--border-secondary);padding:6px 0 6px;}
        /* The glossary: every lead word, defined once. */
        .qd-gloss{padding:8px 10px 10px;margin-bottom:10px;border-radius:8px;
            border:1px solid var(--border-secondary,rgba(127,127,127,.2));
            background:var(--bg-secondary,rgba(127,127,127,.04));}
        .qd-gloss-h{font-size:12.5px;font-weight:700;color:var(--text-primary);}
        .qd-gloss-sub{font-size:11px;color:var(--text-muted);margin:2px 0 8px;}
        .qd-gloss-h2{margin-top:10px;font-size:11.5px;}
        .qd-gloss-dl{margin:0;display:grid;grid-template-columns:112px minmax(0,1fr);gap:4px 10px;
            font-size:11.5px;}
        .qd-gloss-dl dt{font-family:var(--font-mono,ui-monospace,monospace);font-size:9.5px;
            letter-spacing:.06em;text-transform:uppercase;color:var(--text-muted);padding-top:2px;}
        .qd-gloss-dl dd{margin:0;color:var(--text-secondary,var(--text-primary));}
        .qd-scope-gloss{margin-left:auto;}
        @media (prefers-reduced-motion: reduce){
            #qd-tooltip,#qd-modal-backdrop,#qd-modal{transition:none;}
        }`;
        document.head.appendChild(s);
    }

    // ── custom tooltip ────────────────────────────────────────────────────────────
    let tipEl = null, hideTimer = null;
    function ensureTip() {
        if (tipEl) return tipEl;
        injectCss();
        tipEl = document.createElement('div');
        tipEl.id = 'qd-tooltip';
        tipEl.setAttribute('role', 'tooltip');
        document.body.appendChild(tipEl);
        return tipEl;
    }
    const HINT_SVG = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>';
    function showTip(el) {
        const k = el.getAttribute('data-dim');
        if (!k) return;
        const note = el.getAttribute('data-dim-note');
        const t = ensureTip();
        clearTimeout(hideTimer);
        // A MODULE carries `data-dim` because it ROUTES to the property it assesses — but
        // the tooltip is built from that key, so hovering "Data scope adherence" described
        // "Answer scope". An element must describe what it IS, and name where it goes as a
        // separate fact; the two were collapsed into one and the destination won.
        if (el.classList && el.classList.contains('qd-mod')) {
            const mk = el.getAttribute('data-mod');
            t.innerHTML = `<div class="qd-tt-title">${esc(MODULE_LABEL[mk] || mk)}`
                + `<span class="qd-rel-kind"> ·module</span></div>`
                + `<div class="qd-tt-desc">${esc(MODULE_DESC[mk]
                    || 'The code that performs this assessment. A module has no entry of '
                     + 'its own — it is bound to a property, and a remap changes which '
                     + 'module runs without changing what is assessed.')}</div>`
                + (MODULE_ORIGIN[mk] && MODULE_ORIGIN[mk] !== 'platform'
                    ? `<div class="qd-tt-note">From the <b>${esc(MODULE_ORIGIN[mk])}</b> `
                      + `pack — third-party, and signed before it could be bound.</div>` : '')
                + `<div class="qd-tt-note">Opens <b>${esc(label(k))}</b>, the property it `
                + `assesses.</div>`;
            t.style.left = '-9999px'; t.style.top = '0';
            t.classList.add('qd-show');
            position(t, el);
            return;
        }
        // The kind rides the title, marking only the exception — the same `·judge` tag
        // the Guide's chip rows and the roster's chips carry, so one measurement is
        // never called a judge on one surface and a rule on another.
        t.innerHTML = `<div class="qd-tt-title">${esc(label(k))}${JUDGE_DIMS.has(k)
            ? `<span class="qd-rel-kind"> ·judge${decidesInPath(k) ? ' ·in-flight' : ''}</span>` : ''}</div>`
            + `<div class="qd-tt-desc">${esc(desc(k) || 'No description available.')}</div>`
            + (note ? `<div class="qd-tt-note">${esc(note)}</div>` : '')
            + `<div class="qd-tt-hint">Click for full definition ${HINT_SVG}</div>`;
        t.style.left = '-9999px'; t.style.top = '0';
        t.classList.add('qd-show');
        position(t, el);
    }
    function position(t, el) {
        const r = el.getBoundingClientRect();
        const tw = t.offsetWidth, th = t.offsetHeight;
        const gap = 8, vw = window.innerWidth, vh = window.innerHeight;
        let top = r.top - th - gap;                 // prefer above
        if (top < 8) top = r.bottom + gap;          // flip below if no room
        if (top + th > vh - 8) top = Math.max(8, vh - th - 8);
        let left = r.left + r.width / 2 - tw / 2;    // centre on trigger
        left = Math.min(Math.max(8, left), vw - tw - 8);
        t.style.left = left + 'px';
        t.style.top = top + 'px';
    }
    function hideTip() {
        if (!tipEl) return;
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => { if (tipEl) tipEl.classList.remove('qd-show'); }, 60);
    }

    // ── glossary modal (the "document") ───────────────────────────────────────────
    const CLOSE_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    const GUIDE_SVG = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 16v-5"/><path d="M12 8h.01"/></svg>';

    // Metric value formatting + direction, mirroring the consumers' formatters, so the
    // guide can show "measured X · admin fail line: above/below Y" honestly.
    const _PCT_DIMS = new Set(['tool_success_rate', 'numeric_fidelity', 'grounded_overlap',
        'plan_execution', 'retrieval_relevance', 'governed_data_ratio', 'efficiency',
        'context_utilization']);
    // Higher-is-better metrics (min_* gates) — a value BELOW the fail line is danger.
    const HIGHER_IS_BETTER = new Set([..._PCT_DIMS, 'parallel_efficiency']);
    // Dimensions that have an admin-CONFIGURABLE threshold (mirror thresholdSliders.js METRICS →
    // dimension). Only these earn the "Not set" pill ("an admin COULD set a threshold"); a dim
    // that measured a value but has NO configurable threshold (plan_adherence, plan_execution,
    // context_health, a fired error_recovery, coordination_efficiency's no-limit branch) is
    // informational-by-design → "Abstained", NOT a false config-gap promise.
    const _HAS_THRESHOLD = new Set([
        'cost', 'tokens', 'latency', 'tasks', 'parallel_efficiency', 'steps', 'self_correction',
        'tool_success_rate', 'no_tool_loops', 'tool_calls', 'scope_adherence', 'numeric_fidelity',
        'grounded_overlap', 'retrieval_relevance', 'governed_data_ratio',
        'context_utilization', 'knowledge_freshness', 'citations',
    ]);
    function fmtValue(dim, v) {
        if (v == null) return '';
        const n = Number(v);
        if (dim === 'cost' || dim === 'flow_cost') return '$' + n.toFixed(4);
        if (dim === 'latency') return n >= 1000 ? (n / 1000).toFixed(1) + 's' : Math.round(n) + 'ms';
        if (dim === 'tokens') return n.toLocaleString();
        if (dim === 'parallel_efficiency') return n.toFixed(2) + '×';
        if (_PCT_DIMS.has(dim)) return Math.round(n * 100) + '%';
        return Number.isInteger(n) ? n.toLocaleString() : n.toFixed(n < 10 ? 2 : 0);
    }

    // Which IFOC classes each dimension applies to — mirrors the scorers' `applies_to`
    // (src/trusted_data_agent/eval/scorers/*.py). Only restricted dimensions are listed;
    // anything absent applies to every class. Keep in sync when a scorer's scope changes.
    const _CLS = {
        TOOL: ['tool_enabled', 'conversation_with_tools'],
        KNOW: ['rag_focused', 'llm_only', 'tool_enabled', 'conversation_with_tools'],
        DATA: ['tool_enabled', 'rag_focused'],
        IDEATE: ['llm_only', 'conversation_with_tools'],
        PARALLEL: ['tool_enabled', 'genie', 'flow'],
        OPT: ['tool_enabled'],
        FOCUS: ['rag_focused'],
        GENIE: ['genie'],
        FLOW: ['flow'],
    };
    const APPLIES_TO = {
        steps: _CLS.OPT, plan_adherence: _CLS.OPT, plan_execution: _CLS.OPT,
        tool_subset: _CLS.TOOL, must_not_call: _CLS.TOOL, self_correction: _CLS.TOOL,
        tool_success_rate: _CLS.TOOL, no_tool_loops: _CLS.TOOL, scope_adherence: _CLS.TOOL,
        tool_calls: _CLS.TOOL, error_recovery: _CLS.TOOL, governed_data_ratio: _CLS.TOOL,
        kg_seed_utilization: _CLS.TOOL,
        enrichment_recall: _CLS.TOOL,
        surface_violation: _CLS.TOOL,
        action_safety: _CLS.TOOL,
        budget_adherence: ['tool_enabled', 'conversation_with_tools', 'genie'],
        honest_absence: _CLS.TOOL,
        citation_fidelity: ['tool_enabled', 'conversation_with_tools', 'rag_focused', 'genie'],
        identifier_provenance: ['tool_enabled', 'conversation_with_tools', 'rag_focused', 'genie'],
        tool_arg_optimality: _CLS.TOOL, answer_groundedness: _CLS.TOOL, scope_faithfulness: _CLS.TOOL,
        grain_faithfulness: _CLS.TOOL,
        retrieval_performed: _CLS.FOCUS, grounded_overlap: _CLS.FOCUS, citations: _CLS.FOCUS,
        retrieval_relevance: _CLS.FOCUS, groundedness: _CLS.FOCUS, source_consistency: _CLS.FOCUS,
        routing: _CLS.GENIE, coordination_efficiency: _CLS.GENIE, expert_success: _CLS.GENIE,
        synthesis_quality: _CLS.GENIE,
        parallel_efficiency: _CLS.PARALLEL, numeric_fidelity: _CLS.DATA,
        context_utilization: _CLS.KNOW, knowledge_freshness: _CLS.KNOW,
        instruction_following: _CLS.IDEATE,
        flow_completed: _CLS.FLOW, flow_nodes_succeeded: _CLS.FLOW, flow_nodes_reached: _CLS.FLOW,
        flow_forbidden_nodes: _CLS.FLOW, flow_cost: _CLS.FLOW, flow_efficiency: _CLS.FLOW,

        // Generated from the scorers' own `applies_to`, because a scorer that is
        // RESTRICTED by class while this map is silent makes the Guide treat it as
        // applying everywhere — so a dimension that is structurally absent reads
        // "Abstained" (applies, did not fire) instead of "Not applicable". That is
        // the three-state distinction this file exists to keep honest, lost in the
        // gap between two maps. The integrity gate now asserts they agree.
        answer_groundedness:     ['conversation_with_tools', 'tool_enabled'],
        citations:               ['rag_focused'],
        coordination_efficiency: ['genie'],
        error_recovery:          ['conversation_with_tools', 'tool_enabled'],
        expert_success:          ['genie'],
        flow_cost:               ['flow'],
        flow_efficiency:         ['flow'],
        flow_nodes_reached:      ['flow'],
        flow_nodes_succeeded:    ['flow'],
        governed_data_ratio:     ['conversation_with_tools', 'tool_enabled'],
        grounded_overlap:        ['rag_focused'],
        groundedness:            ['rag_focused'],
        kg_seed_utilization:     ['conversation_with_tools', 'tool_enabled'],
        enrichment_recall:       ['conversation_with_tools', 'tool_enabled'],
        library_read_first:      ['conversation_with_tools', 'rag_focused', 'tool_enabled'],
        knowledge_freshness:     ['conversation_with_tools', 'llm_only', 'rag_focused', 'tool_enabled'],
        must_not_call:           ['conversation_with_tools', 'tool_enabled'],
        no_tool_loops:           ['conversation_with_tools', 'tool_enabled'],
        numeric_fidelity:        ['rag_focused', 'tool_enabled'],
        plan_adherence:          ['tool_enabled'],
        plan_execution:          ['tool_enabled'],
        scope_adherence:         ['conversation_with_tools', 'tool_enabled'],
        scope_faithfulness:      ['conversation_with_tools', 'tool_enabled'],
        grain_faithfulness:      ['conversation_with_tools', 'tool_enabled'],
        self_correction:         ['conversation_with_tools', 'tool_enabled'],
        source_consistency:      ['rag_focused'],
        tasks:                   ['conversation_with_tools', 'flow', 'genie', 'llm_only', 'rag_focused', 'tool_enabled'],
    };
    function appliesToClass(dim, cls) {
        if (!cls) return true;               // class unknown → don't claim inapplicability
        const a = APPLIES_TO[dim];
        return !a || a.indexOf(cls) !== -1;
    }
    // IFOC class → human label (for "only for Optimize, Coordinate" reason lines).
    const IFOC_LABEL = {
        llm_only: 'Ideate', rag_focused: 'Focus', tool_enabled: 'Optimize',
        genie: 'Coordinate', conversation_with_tools: 'Ideate + Tools', flow: 'Flow',
    };
    // IFOC class → engine colour. Mirrors the canonical mapping in
    // configurationHandler.js (KG_IFOC_CONFIG / ifocTagConfig — grep the symbols);
    // that module is an ES module and this is a plain pre-loaded script, so the values
    // are mirrored rather than imported. `conversation_with_tools` renders as Ideate
    // (its normalized class); `flow` is not an IFOC engine and keeps a neutral slate.
    const IFOC_COLOR = {
        llm_only: '#4ade80', rag_focused: '#3b82f6', tool_enabled: '#9333ea',
        genie: '#F15F22', conversation_with_tools: '#4ade80', flow: '#64748b',
    };
    const ENGINE_ORDER = ['llm_only', 'rag_focused', 'tool_enabled', 'genie', 'flow'];
    //: Which classes a REGISTERED entry (mechanism) applies to — served by the
    //: vocabulary endpoint from each AssurancePoint's `applies_to`. Empty/absent =
    //: every class, same convention as APPLIES_TO.
    const ENTRY_APPLIES = {};
    //: Which STATION a registered entry belongs to — served by the vocabulary
    //: endpoint. For a scored dimension the station is derived (see stationOf).
    const ENTRY_STATION = {};
    //: Of a mechanism's dimensions, the ones it CONSULTS IN-PATH (registry `POINT_JUDGES`,
    //: served per mechanism). The rest merely grade it afterwards — a distinction the
    //: single "measured by" list erased.
    const ENTRY_JUDGES = {};
    //: The measures a WINDOW aggregate weights (registry `POINT_WEIGHTS`, served per
    //: mechanism as `weights`), and the reverse. A third relation, kept apart from the
    //: two above on purpose: `graded by` would say these measures grade the aggregate
    //: (they feed it), `reads` would say a module assesses it (none does — it is a blend
    //: over the record, not an assessment of this turn).
    const ENTRY_WEIGHTS = {};
    const WEIGHS_ON = {};

    /** The IFOC engines an entry is relevant for, normalized and in canonical order.
     *  Absent restriction = every IFOC engine (never `flow` — flow dims declare it). */
    function enginesFor(k) {
        const a = ENTRY_APPLIES[k] || APPLIES_TO[k] || null;
        const list = a && a.length ? a : ['llm_only', 'rag_focused', 'tool_enabled', 'genie'];
        const seen = new Set();
        list.forEach((c) => seen.add(c === 'conversation_with_tools' ? 'llm_only' : c));
        return ENGINE_ORDER.filter((c) => seen.has(c));
    }

    /** The scored dimension a threshold key tunes (the reverse of TUNING), or null. */
    function dimOfThreshold(tk) {
        for (const d of Object.keys(TUNING)) {
            if (TUNING[d] && TUNING[d].key === tk) return d;
        }
        return null;
    }

    /** The STAGE of the turn a threshold key's dimension measures — how the admin Trust
     *  page groups its sliders. Derived from the served process model via the TUNING
     *  reverse map, so the admin page regroups itself whenever the model changes and no
     *  second list has to be kept in step.
     *
     *  Replaced `stationOfThreshold`. Grouping thresholds by STATION said a slider
     *  belonged to a guarantee, which stopped being true when measurement became a lane:
     *  the sliders tune MEASUREMENTS, and a measurement's home is its stage. */
    function stageOfThreshold(tk) {
        const d = dimOfThreshold(tk);
        return d ? stageOf(d) : '';
    }

    /** The guarantee an entry BEARS ON — a relation, no longer a home.
     *
     *  ⚠ This is not where a measurement is counted. Every scored dimension is counted
     *  at JUDGED (measurement is a lane), and its shelf is the MOMENT it is taken at
     *  (`stageOf`). What survives here is the genuinely useful third fact: which
     *  guarantee a check bears on — which is what the `grades` reverse row renders, and
     *  what a station entry lists under `graded by`.
     *
     *  A mechanism carries its station from the registry; a dimension inherits it from
     *  the mechanism that consults it. `'scored'` is returned when no mechanism does —
     *  meaning "bears on no single guarantee", not "counted at a residue".
     */
    function stationOf(k) {
        if (classOf(k) === 'station') return null;
        if (ENTRY_STATION[k]) return ENTRY_STATION[k];
        for (const m of (USED_BY[k] || [])) {
            if (ENTRY_STATION[m]) return ENTRY_STATION[m];
        }
        return 'scored';   // bears on no single guarantee
    }

    // Status taxonomy — a scored band, OR one of THREE distinct "no grade" states, because
    // they mean different things and imply different actions:
    //   na        = "Not applicable": structurally doesn't apply to this IFOC class.
    //   notset    = "Not set": applies here, but no threshold/target/assertion is configured —
    //               the admin/author COULD set one to make it grade. A config gap.
    //   abstained = "Abstained": applies AND is configured, but there was no signal to measure
    //               this turn (e.g. Error Recovery didn't fire because no error occurred).
    // ONE colour vocabulary (PROP-002): band colours are the trust tokens
    // (healthy IS pass-green, review IS worth-a-look amber) — never a private,
    // theme-invariant hex beside the ring's. The no-grade family stays muted.
    const BAND = {
        healthy:   { c: 'var(--trust-pass, #10b981)', t: 'Healthy' },
        review:    { c: 'var(--trust-warn, #f59e0b)', t: 'Review' },
        danger:    { c: 'var(--trust-danger, #ef4444)', t: 'Danger' },
        notset:    { c: 'var(--text-muted, #64748b)', t: 'Not set' },
        abstained: { c: 'var(--trust-na, #94a3b8)', t: 'Abstained' },
        na:        { c: 'rgba(127,127,127,.6)', t: 'Not applicable' },
    };
    // Config-gap reasons (admin/author could fix) vs no-signal reasons (nothing happened).
    const _NOTSET_RE = /(target|assertion|constraint|required|not required|expect|ceiling|no limit|no ceiling|threshold)/i;
    const _NOSIGNAL_RE = /(no error|no retriev|no document|no answer|no task|no plan|no concurren|no provenance|no tool call|no number|no signal|no data|nothing to|not measurable|no strategic)/i;
    function abstentionKind(info, k) {
        // A metric that measured a value but has no admin gate → "Not set" ONLY if it actually
        // has a configurable threshold; otherwise it's informational-by-design → "Abstained"
        // (never falsely tell the user an admin could configure a threshold that doesn't exist).
        if (info && info.score != null && info.threshold == null) {
            return _HAS_THRESHOLD.has(k) ? 'notset' : 'abstained';
        }
        const x = (info && info.explanation) || '';
        if (_NOSIGNAL_RE.test(x)) return 'abstained';
        if (_NOTSET_RE.test(x)) return 'notset';
        return 'abstained'; // ran but produced no verdict for an unclassified reason
    }
    // Resolve the displayed status of a dimension for a given turn/agent context + class.
    function statusFor(k, info, cls) {
        const b = info && info.band;
        if (b === 'healthy' || b === 'review' || b === 'danger') return b;
        if (cls && !appliesToClass(k, cls)) return 'na';   // structural — wrong agent type
        return abstentionKind(info, k);                     // applies: not-set vs abstained
    }
    function statusPill(band) {
        const b = BAND[band] ? band : null;
        if (!b) return '';
        const { c, t } = BAND[b];
        const grey = (b === 'na');
        const bg = grey ? 'rgba(127,127,127,.12)' : `color-mix(in srgb, ${c} 10%, transparent)`;
        const bd = grey ? 'var(--border-secondary,rgba(127,127,127,.25))' : `color-mix(in srgb, ${c} 20%, transparent)`;
        // Distinct border styles so the three "no grade" states read apart at a glance:
        // Not set = dashed, Abstained = dotted, Not applicable = solid grey.
        const style = b === 'notset' ? 'border-style:dashed;' : b === 'abstained' ? 'border-style:dotted;' : '';
        return `<span class="qd-status" style="color:${c};background:${bg};border:1px solid ${bd};${style}"><span class="qd-sdot" style="background:${c}"></span>${t}</span>`;
    }

    // Guide state: the context (this turn's / this agent's dimensions + their live band,
    // measured value, and admin fail line), the scope, and whether the admin boundaries
    // are revealed by the global toggle.
    let _guideCtx = null;         // { rows: {key: {band, score, threshold, explanation}}, label, cls }
    let _guideScope = 'all';      // 'context' | 'all'
    let _guideShowThresh = false; // global "Admin thresholds" toggle
    let _guideCls = null;         // the context's IFOC class (drives Applicable vs Not set)
    // The two facets, as filters. Empty set = no filter on that facet, so the default is
    // "show everything" and each pill NARROWS — a filter that starts restrictive hides
    // things the reader never asked to hide.
    let _guideEntry = null;       // the entry the Guide was opened AT — the 'entry' scope shows it alone
    let _fGroup = 'class';        // the ONE facet group whose pills are open (the others fold to their names)
    let _fClass = new Set();      // measure | control | boundary | attestation
    let _fMeasured = new Set();   // atomic | composite | unmeasured
    let _fEngine = new Set();     // llm_only | rag_focused | tool_enabled | genie | flow
    let _fRole = new Set();       // decides | reports — a judge a gate can consult, or not
    let _fStage = new Set();      // the MOMENT of the turn a check is taken at
    // How it SCORED on this turn. Only meaningful in context scope — a band is a reading,
    // and outside a turn there is nothing to read. This is the facet that lets "2 of 20
    // below the line" be a link to those two rather than to all twenty.
    let _fBand = new Set();       // healthy | review | danger | na

    // One measured-value + admin-fail-line line for a context dimension (shown only when
    // the global thresholds toggle is on and the dimension carries a measured value).
    // Only shown where an admin boundary actually exists (a banded metric with a fail line).
    // Deterministic pass/fail checks and un-gated metrics have no admin threshold, so under
    // the "Admin thresholds" toggle they render nothing — a binary "Measured 1" is noise.
    function boundaryLine(k, info) {
        if (!info || info.threshold == null) return '';
        const dir = HIGHER_IS_BETTER.has(k) ? 'below' : 'above';
        const parts = [];
        if (info.score != null) parts.push(`Measured <b>${esc(fmtValue(k, info.score))}</b>`);
        parts.push(`<span>admin fail line: <span class="qd-fail">${dir} ${esc(fmtValue(k, info.threshold))}</span></span>`);
        return `<div class="qd-row-bound">${parts.join(' · ')}</div>`;
    }

    // The honest "why no grade" line — shown for the two no-grade states so the user never
    // has to decode a label: Not applicable → which agent types it IS for; Not set → the exact
    // abstention reason from the verdict (e.g. "no optimal-tasks target", "no errors occurred").
    function reasonLine(k, info, status) {
        if (status === 'na') {
            const a = APPLIES_TO[k];
            const names = a ? a.map((c) => IFOC_LABEL[c] || c).join(', ') : '';
            return `<div class="qd-row-note">Doesn’t apply to this agent type${names ? ` — only for ${esc(names)}` : ''}.</div>`;
        }
        if (status === 'notset') {
            const why = info && info.explanation ? info.explanation : 'no threshold or target configured yet';
            return `<div class="qd-row-note">Not set — ${esc(why)}. An admin can configure a threshold/target to grade it.</div>`;
        }
        if (status === 'abstained') {
            const why = info && info.explanation ? info.explanation : 'no signal to measure this turn';
            return `<div class="qd-row-note">Abstained — ${esc(why)}. It applies and is configured, but didn’t fire this turn.</div>`;
        }
        return '';
    }

    /** Judges a MECHANISM can consult during a turn — the union of every mechanism's
     *  declared escalation set (`POINT_JUDGES`, served on each entry).
     *
     *  The distinction the list could not show: five judges are OWNED by GROUNDED and
     *  GUARDED, but owning a guarantee means "this judge measures whether it held", not
     *  "this judge runs inside its gate". Only two are wired to a gate, and only one can
     *  fire on any turn. Both directions were already on the entries (`escalates to` /
     *  `decides for`) — but answering "which of the nine?" meant opening nine of them.
     */
    function decidesInPath(k) {
        for (const m in ENTRY_JUDGES) {
            if ((ENTRY_JUDGES[m] || []).indexOf(k) >= 0) return true;
        }
        return false;
    }

    /** WHEN a judge runs — the question an operator actually has about it.
     *
     *  Two independent facts, both derived, neither assumed:
     *    in-flight — a mechanism DECLARES it (`POINT_JUDGES`), so a gate can consult it
     *                during the turn and act on the ruling;
     *    after     — it is a registered scored dimension, so the scoring layer produces
     *                a verdict for it once the answer has shipped.
     *
     *  Today every judge is scored after the turn and two are also consulted in-flight,
     *  so `inflight` (a judge wired to a gate but never scored) is empty — the taxonomy
     *  still carries it, because a bucket that cannot be expressed is a bucket that will
     *  be mis-filed the day it exists.
     */
    function judgeRuns(k) {
        if (!JUDGE_DIMS.has(k)) return null;
        const inflight = decidesInPath(k), after = !!DIM[k];
        return inflight && after ? 'both' : inflight ? 'inflight' : 'after';
    }
    //: Mark the EXCEPTION, never the rule. Every judge is scored after the turn — that
    //: is the constant — so only the two a gate can also consult in-flight carry a
    //: qualifier. `inflight` (wired to a gate, never scored) is empty today and renders
    //: nothing; it exists so the day it happens it cannot be mis-filed as "both".
    const RUNS_LABEL = { both: 'also in-flight', inflight: 'in-flight only', after: '' };
    const RUNS_FACET = { both: 'Also in-flight', inflight: 'In-flight only', after: 'After the turn only' };

    //: No MEMBER mark. A ring-segment glyph stood on the members group and beside the colour
    //: rule; in orange it read as a loading spinner (operator, 2026-10-30), and it wrapped onto
    //: a line of its own. The link it drew is carried by the words: the rule and the group
    //: both say "members · N", and the group keeps its orange edge.
    //: The colour rule as the registry serves it (`STATION_READS`). The renderer adds the
    //: count to THIS phrase, so the gate pins that the server still says it.
    const MEMBERS_RULE = 'worst of its members';
    //: JUDGED's own words for the same rule — its members are the measures it counts.
    const JUDGED_RULE = 'worst grade of its measures';
    //: Membership is ONE partition: every member belongs to exactly one station. A
    //: mechanism to its guarantee; every scored measure to JUDGED (`allocate_measurements`
    //: counts each there once) — which is why a guarantee's "not members" are JUDGED's.
    const MEMBER_TIP = 'A member is what a station\u2019s colour is the worst of, and each belongs to exactly one '
        + 'station. At a guarantee: whatever upholds, bounds, attests or evidences it. At JUDGED: every scored '
        + 'measure, counted there once.';
    /** JUDGED's members — the measures placed at a moment — scoped to the opened class. */
    function judgedMembers() {
        const all = Object.keys(STAGE_OF);
        return { all, inScope: all.filter((d) => appliesToGuideCls(d)) };
    }
    /** Does this guarantee's served reading say it is the worst of its members? Three
     *  single-member stations state their reading in their own words ("sealed, or not"),
     *  and the members header must not claim a rule the reading row does not. */
    function readsWorstOfMembers(k) {
        const r = ENTRY_READS[k] || {};
        const blk = r.answer || r.turn;
        return !!(blk && Array.isArray(blk.rows) && blk.rows.some(([, v]) => v === MEMBERS_RULE));
    }

    /** A guarantee's relations, grouped by MEMBERSHIP — not by when a row speaks.
     *
     *  The time cut put `evidenced by` (members: they colour this station) in one folded
     *  group with `graded by` (not members: counted at JUDGED), both under the noun
     *  "measures", four lines below "worst of its members" — and the rows that phrase
     *  meant were never gathered anywhere. Membership is the cut the colour rule needs;
     *  the time fact survives as a note on the one member row taken after the turn.
     *
     *  JUDGED has no members (nothing runs there): its rows render as before, open. */
    function membersHtml(k, R) {
        const V = R.byVerb || {};
        const after = (V['evidenced by'] || '').replace(/(<span class="qd-kindn[^>]*>[^<]*<\/span>)/,
            '$1<span class="qd-lead-sub">taken after the turn</span>');
        const rows = (V['upheld by'] || '') + (V['bounded by'] || '') + (V['attested by'] || '') + after;
        const n = (CONTROLS[k] || []).length;
        const name = esc(label(k));
        const outside = (R.graded || '') + (R.counts || '') + (R.grades || '');
        const nOut = (outside.match(/class="qd-rel-chip/g) || []).length;
        const rule = readsWorstOfMembers(k)
            ? `<span class="qd-mem-rule"> \u2014 the worst of these colours ${name}</span>` : '';
        const head = rows
            ? `<div class="qd-rel-grp qd-mem"><div class="qd-rel-grp-h u-tip" data-tip="${esc(MEMBER_TIP)}">`
              + `${n === 1 ? 'member' : 'members'} \u00b7 ${n}${rule}</div>${rows}</div>`
            : '';
        // JUDGED: its members are the measures it counts, shelved by the moment each is
        // taken at — the moments ARE the members group, so the full set is one click away
        // and never a wall of chips.
        if (k === 'station_' + 'scored') {
            const { all, inScope } = judgedMembers();
            if (!all.length || !R.counts) return outside;   // stage model not landed: as before
            const scope = inScope.length < all.length ? ` \u00b7 ${inScope.length} of ${all.length} for this class` : '';
            const counts = R.counts.replace(/(<span class="qd-kindn[^>]*>[^<]*<\/span>)/,
                `$1<span class="qd-lead-sub">${all.length} measures, by moment</span>`);
            return `<div class="qd-rel-grp qd-mem"><div class="qd-rel-grp-h u-tip" data-tip="${esc(MEMBER_TIP)}">`
                + `members \u00b7 ${inScope.length}${scope}<span class="qd-mem-rule"> \u2014 the worst grade of these colours ${name}</span>`
                + `</div>${counts}</div>` + (R.graded || '') + (R.grades || '');
        }
        if (!outside) return head;
        if (!rows) return outside;
        return head + `<details class="qd-rel-grp qd-rel-fold"><summary class="qd-rel-grp-h">not members \u00b7 graded by ${nOut}`
            + `<span class="qd-fold-n"> \u2014 JUDGED\u2019s members: they colour JUDGED, not ${name}</span></summary>${outside}</details>`;
    }

    function rowHtml(k, info) {
        // WHEN a judge runs is what an operator is looking for when they scan this list —
        // a judge a gate consults can stop an answer; one scored afterwards cannot.
        // A GUARANTEE and a MOMENT are not measured by anything — one is a promise, the
        // other a point in the turn — so "how is it graded", "what can I tune" and "did
        // it fire" are not questions about them. Rendering a moment as `deterministic`,
        // as "a yes/no check", and as "didn't fire this turn" answered three questions
        // nobody asked of it, in the vocabulary of a different kind of entry.
        const grouping = (classOf(k) === 'stage' || classOf(k) === 'station'
                          || classOf(k) === 'property');
        const runs = judgeRuns(k);
        const kindTag = grouping ? '' : runs
            ? `LLM judge${RUNS_LABEL[runs] ? ' · ' + RUNS_LABEL[runs] : ''}`
            : 'deterministic';
        // Status + reason only when a turn/agent context is present; a generic glossary
        // open shows pure definitions (no live status).
        const status = (_guideCtx && !grouping) ? statusFor(k, info, _guideCls) : null;
        // The MOMENT this check is taken at — its shelf, and the one placement that
        // varies between checks. It used to be the check's STATION, which said "this
        // check's home is GROUNDED"; that stopped being true when measurement became a
        // lane (its counting home is JUDGED for every check alike — a constant, not
        // worth a chip). What the check is ABOUT is not lost: it rides the `grades`
        // reverse row below, which is where a relation belongs.
        //
        // Same `data-dim` channel as every other link, so the chip gets the moment
        // entry's own hover tooltip and opens the Guide there on click.
        const stg = stageOf(k);
        const stChip = stg
            ? `<button type="button" class="qd-st-chip" data-dim="stage_${esc(stg)}">${esc(STAGE_LABEL[stg] || stg)}</button>`
            : '';
        // The IFOC engine tags — one dot per engine this entry is relevant for, in the
        // engines' own colours; hover names the engine (u-tip, never a native title).
        const engines = enginesFor(k);
        const engDots = `<span class="qd-ifoc" role="img" aria-label="Engines: ${
            esc(engines.map((c) => IFOC_LABEL[c] || c).join(', '))}">${
            engines.map((c) => `<span class="qd-ifoc-dot u-tip" data-tip="${
                esc(IFOC_LABEL[c] || c)}" style="background:${IFOC_COLOR[c]}"></span>`).join('')}</span>`;
        // ── ONE anatomy for every kind, in a fixed order ───────────────────────
        //   title    name · kind tag · engine dots · this-turn status
        //   can      what it can do to the answer (fates · the guarantee's question)
        //   desc     the description, and the this-turn reason beneath it
        //   lanes    a property's assessors, which carry their own time words
        //   in the turn / after the turn — the SAME relation rows, limits and tuning the
        //            entry always carried, shelved by when the related thing speaks
        //   meta     the wire key, deterministic/judge, the measured facet — quiet, last
        // Every slot, every link and every term is the one the row carried before; what
        // changed is order, grouping and weight. Seven layouts became one.
        const cls = classOf(k);
        const parts = relationParts(k);
        const R = parts.rows || {};
        const takenAt = stChip ? `<div class="qd-row-rel">${leadHtml('taken at')}${kindCount('taken at', 1)}${stChip}</div>` : '';
        const bound = _guideShowThresh ? boundaryLine(k, info) : '';
        const facts = factRows(k);
        // The tuning line keeps its own node (the live-band patcher replaces it in place)
        // and sits in a row like every other fact.
        const tune = tuningLine(k);
        const threshold = tune ? factRow('threshold', tune) : '';
        const grp = (h, body) => body ? `<div class="qd-rel-grp"><div class="qd-rel-grp-h">${h}</div>${body}</div>` : '';
        // The after-the-turn group FOLDS on a mechanism or a guarantee: it names the
        // measures that grade it, and those live at JUDGED anyway. The fold's one line
        // carries the counts, so nothing is lost and a third of the entry's height goes.
        // It stays OPEN where it is the entry's whole content: a measure, and JUDGED.
        const foldAfter = (body) => {
            if (!body) return '';
            const n = (body.match(/class="qd-rel-chip/g) || []).length;
            // The row's lead reads "graded by", or "graded by · 5 of 7 for this class" when the
            // Guide is opened for one agent — the summary reads both (it fell back to "7 linked").
            const lineOf = (lead) => {
                const m = body.match(new RegExp(`qd-rel-lead[^>]*>${lead}(?: \u00b7 (\\d+) of (\\d+) for this class)?<[\\s\\S]*?</div>`));
                if (!m) return null;
                return m[1] ? `${lead} ${m[1]} of ${m[2]}` : `${lead} ${m[0].split('class="qd-rel-chip').length - 1}`;
            };
            const sum = ['evidenced by', 'graded by'].map(lineOf).filter(Boolean);
            if (!sum.length && n) sum.push(`${n} linked`);
            return `<details class="qd-rel-grp qd-rel-fold"><summary class="qd-rel-grp-h">after the turn<span class="qd-fold-n">${sum.length ? ' \u00b7 ' + sum.join(' \u00b7 ') : ''}</span></summary>${body}</details>`;
        };
        // ROWS IN THE ORDER THE THING WORKS. A mechanism: which guarantee · how it decides ·
        // the module that checks · what the check finds · the judge asked about a hit ·
        // the settings · why. A guarantee: how it reads (on a turn · on the ring · its
        // checks · why) ahead of its members. A check: who asks it, then what it grades.
        // The registry lists relations in the order they were added, which put the judge
        // before the module; the Guide orders them by when they happen.
        let turn, after, reads = '', members = null;
        if (cls === 'station') {
            reads = grp('how it reads', readsRows(k) + facts.fires + facts.settings + facts.why);
            members = membersHtml(k, R);
        } else {
            // The parent row leads: what a mechanism upholds, what a property is assessed for.
            // `weights` sits where `reads` sits: both name what the reading is made of,
            // before what it does with it (fires when).
            turn = (R.assessedFor || '') + (R.upholds || '') + howRow(k) + propertiesRow(k) + modulesRow(k) + (R.weights || '')
                 + facts.fires + (R.escalates || '') + (R.decides || '') + facts.settings + facts.why;
            after = afterModules(k) + (R.graded || '') + (R.counts || '') + (R.grades || '') + (R.weighsOn || '')
                  + (R.countedAt || '') + takenAt + threshold + bound;
        }
        // Group headers only when BOTH groups have rows: a lone group under a time header
        // is a label explaining nothing. The flat form is the same rows in the same order.
        // A PROPERTY keeps it open too: what grades it is half of what its entry is for.
        const stayOpen = !ENTRY_CLASS[k] || k === 'station_' + 'scored' || ENTRY_CLASS[k] === 'measure'
            || ENTRY_CLASS[k] === 'property';
        // The modules this mechanism consults during the turn: a fold of its own, BEFORE
        // "after the turn" — the code that checks, apart from what the gate is about.
        const mods = members != null ? '' : modulesFold(k);
        const relations = members != null ? reads + members : reads + ((turn && after)
            ? grp('in the turn', turn) + mods + (stayOpen ? grp('after the turn', after) : foldAfter(after))
            : (turn || after || parts.note)
                // A lone group keeps the group's grid (the lead column) without a header:
                // a time header over one group explains nothing, but unwrapped rows lost
                // the layout and stacked each lead above its value.
                ? ((turn || after) ? `<div class="qd-rel-grp">${turn + after}</div>` : '') + mods + parts.note
                : `${relatedChips(k)}${propertiesRow(k)}${modulesRow(k)}${mods}${limitsRows(k)}${takenAt}${threshold}${bound}`);
        const measured = (grouping || !ENTRY_CLASS[k]) ? '' : (MEASURED_LABEL[measuredOf(k)] || '');
        // The wire key leaves the reading line: an "id" mark shows it on hover and copies
        // it on click. Search still matches on it. Nobody reads `grounding_gate`; they
        // paste it.
        const wire = ENTRY_NAME[k] || k;
        const metaItems = [
            kindTag ? `<span class="qd-tag">${esc(kindTag)}</span>` : '',
            measured ? `<span class="qd-tag">${esc(measured)}</span>` : '',
            `<button type="button" class="qd-id u-tip" data-qd-copy="${esc(wire)}" data-tip="id ${esc(wire)} — the key this entry carries in payloads, events and links. Click to copy.">`
            + `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>`
            + `id<span class="qd-row-key">${esc(wire)}</span></button>`,
        ].filter(Boolean).join('');
        return `<div class="qd-row" data-qd-key="${esc(k)}">
            <div class="qd-row-top">
                <span class="qd-row-label">${esc(label(k))}</span>
                <span class="qd-tag qd-tag-kind u-tip" data-tip="${esc(CLASS_NOTE[cls] || '')}">${esc(KIND_WORD[cls] || cls)}</span>
                ${engDots}
                ${statusPill(status)}
            </div>
            ${canLine(k, cls)}
            <div class="qd-row-desc">${esc(long(k) || 'No description available.')}</div>
            ${reasonLine(k, info, status)}
            ${lanesRow(k)}
            ${relations}
            <div class="qd-meta">${metaItems}</div>
        </div>`;
    }
    /** The keys the current scope shows — ONE rule for the list and the facet counts.
     *    entry    the entry the Guide was opened at, alone (falls back to everything while
     *             the vocabulary that defines it is still landing — never an empty modal)
     *    context  the turn's rows
     *    all      the whole catalogue */
    function _scopeKeys() {
        if (_guideScope === 'entry' && _guideEntry && DIM[_guideEntry]) return [_guideEntry];
        if (_guideScope === 'context' && _guideCtx) return Object.keys(_guideCtx.rows).filter((k) => DIM[k]);
        return Object.keys(DIM);
    }
    function bodyHtml(filter) {
        const q = (filter || '').trim().toLowerCase();
        const ctx = _guideCtx;
        const rows = ctx ? ctx.rows : {};
        const inScope = _scopeKeys();
        const match = (k) => !q || label(k).toLowerCase().includes(q)
            || long(k).toLowerCase().includes(q) || k.toLowerCase().includes(q)
            || String(STAGE_LABEL[stageOf(k)] || '').toLowerCase().includes(q);
        // Facet filters compose with search and with each other; an empty set means "no
        // opinion on this facet", so pills only ever narrow.
        const passes = (k) => match(k)
            && (!_fClass.size || _fClass.has(classOf(k)))
            && (!_fMeasured.size || _fMeasured.has(measuredOf(k)))
            && (!_fBand.size || _fBand.has(((rows[k] || {}).band) || 'na'))
            // Engine pills widen within the facet (any selected engine matches) and
            // narrow against the others — the same composition every facet uses.
            && (!_fEngine.size || enginesFor(k).some((c) => _fEngine.has(c)))
            // Judge role narrows to judges by construction: a deterministic check is
            // neither "can decide" nor "reports only" — it is not a judge at all.
            && (!_fRole.size || (JUDGE_DIMS.has(k) && _fRole.has(judgeRuns(k))))
            // The MOMENT the check is taken at. Narrows like the others; an entry the
            // served model does not place (a mechanism, a guarantee) is excluded when a
            // moment is selected — which is correct: the question "show me what happens
            // at Compose" is about measurements.
            && (!_fStage.size || _fStage.has(stageOf(k)));
        const infoOf = (k) => rows[k] || null;

        // Grouped by CLASS first — the reader's question is "what kind of thing is
        // this?" before "what subject does it cover?". Tier grouping is kept INSIDE
        // Measures, where it earns its keep at 52 entries; the other classes are small
        // enough that a second level would be noise.
        const byClass = {};
        inScope.forEach((k) => { if (passes(k)) (byClass[classOf(k)] = byClass[classOf(k)] || []).push(k); });

        const blocks = ['station', 'stage', 'property', 'measure', 'control', 'boundary', 'attestation']
            .filter((c) => byClass[c] && byClass[c].length).map((c) => {
                const head = `<div class="qd-class-h">${esc(CLASS_LABEL[c])}`
                    + `<span class="qd-class-n">${byClass[c].length}</span></div>`
                    + `<div class="qd-tier-sub">${esc(CLASS_NOTE[c])}</div>`;
                if (c !== 'measure') {
                    return head + byClass[c].map((k) => rowHtml(k, infoOf(k))).join('');
                }
                // Measurements shelve by the STAGE of the turn they measure, in the
                // order a turn runs. A registered mechanism has no stage (it is not a
                // measurement) and is listed first, unshelved — filing it under a
                // catch-all group would read as a leftover rather than the subject.
                const groups = groupByStage(byClass[c]);
                const placed = new Set(groups.flatMap((g) => g.keys));
                const loose = byClass[c].filter((k) => !placed.has(k))
                    .map((k) => rowHtml(k, infoOf(k))).join('');
                return head + loose + groups.map((g) =>
                    `<div class="qd-tier-h">${esc(g.label)}</div>`
                    + `<div class="qd-tier-sub">${esc(g.desc)}</div>`
                    + g.keys.map((k) => rowHtml(k, infoOf(k))).join('')).join('');
            }).join('');

        if (blocks) return blocks;
        const why = q ? `match “${esc(filter)}”` : 'match these filters';
        return `<div class="qd-empty">Nothing here ${why}.</div>`;
    }

    /** The facet filter row. Each pill narrows; none is on by default. */
    function facetBar(inScope) {
        const n = (fn, v) => inScope.filter((k) => fn(k) === v).length;
        const pill = (kind, v, text, count, on, dot) => count
            ? `<button type="button" class="qd-facet${on ? ' on' : ''}" data-qd-facet="${kind}"`
              + ` data-qd-facet-v="${esc(v)}" aria-pressed="${on}">`
              + `${dot ? `<span class="qd-ifoc-dot" style="background:${dot}" aria-hidden="true"></span>` : ''}`
              + `${esc(text)}<span class="qd-facet-n">${count}</span></button>` : '';
        const cls = ['station', 'stage', 'property', 'measure', 'control', 'boundary', 'attestation']
            .map((c) => pill('class', c, CLASS_LABEL[c], n(classOf, c), _fClass.has(c))).join('');
        const meas = ['atomic', 'composite', 'unmeasured']
            .map((m) => pill('measured', m, MEASURED_LABEL[m], n(measuredOf, m),
                             _fMeasured.has(m))).join('');
        // The engine facet — which IFOC engine an entry is relevant for. Counts use
        // membership (an entry can serve several engines), so pills widen within the
        // facet like the band pills do, and the dot repeats the row tags' colour code.
        const eng = ENGINE_ORDER
            .map((c) => pill('engine', c, IFOC_LABEL[c] || c,
                             inScope.filter((k) => enginesFor(k).includes(c)).length,
                             _fEngine.has(c), IFOC_COLOR[c])).join('');
        const bandOf = (k) => ((_guideCtx && _guideCtx.rows[k]) || {}).band || 'na';
        const bands = _guideCtx ? ['danger', 'review', 'healthy', 'na']
            .map((b) => pill('band', b, BAND_LABEL[b], inScope.filter((k) => bandOf(k) === b).length,
                             _fBand.has(b))).join('') : '';
        // Which judges a gate can CONSULT — the question the list could not answer
        // ("how can I see which judges are consulted by a gate and which are not?").
        // Offered only where judges are in scope: a facet with nothing to select is a
        // dead control.
        // The MOMENT facet — filtering by the Guide's own primary grouping. Every other
        // facet existed while the one thing the shelf is organised by could not be
        // selected. Offered only when the served model has landed AND some entry in
        // scope is placed: a facet with nothing to select is a dead control.
        const moments = stagesReady()
            ? STAGE_ORDER.map((st) => pill('stage', st, STAGE_LABEL[st] || st,
                  inScope.filter((k) => stageOf(k) === st).length, _fStage.has(st))).join('')
            : '';
        const judgesIn = inScope.filter((k) => JUDGE_DIMS.has(k));
        const role = judgesIn.length ? ['both', 'inflight', 'after']
            .map((r) => pill('role', r, RUNS_FACET[r],
                             judgesIn.filter((k) => judgeRuns(k) === r).length, _fRole.has(r)))
            .join('') : '';
        const any = _fClass.size || _fMeasured.size || _fBand.size || _fEngine.size
                 || _fRole.size || _fStage.size;
        // The six groups FOLD to their names. All of their pills remain — one group's are
        // open at a time (Kind by default), the others open on click. Twenty-nine pills
        // standing open above a list the reader has not yet read was the first thing the
        // Guide showed, and it was the first thing a reader had to look past.
        const GROUPS = [
            ['class', 'Kind', cls, _fClass.size], ['stage', 'Moment', moments, _fStage.size],
            ['measured', 'Measured by', meas, _fMeasured.size], ['engine', 'Engine', eng, _fEngine.size],
            ['role', 'Judge runs', role, _fRole.size], ['band', 'This turn', bands, _fBand.size],
        ].filter(([, , pills]) => pills);
        if (!GROUPS.some(([g]) => g === _fGroup)) _fGroup = GROUPS.length ? GROUPS[0][0] : 'class';
        const groupBtns = GROUPS.map(([g, name, pills, on]) =>
            `<button type="button" class="qd-facet qd-facet-group${_fGroup === g ? ' open' : ''}${on ? ' on' : ''}"`
            + ` data-qd-facet="group" data-qd-facet-v="${g}" aria-expanded="${_fGroup === g}">`
            + `${esc(name)}<span class="qd-facet-n">${on ? `${on} on · ` : ''}${(pills.match(/<button/g) || []).length}</span></button>`).join('');
        return `<div class="qd-facets">
            <span class="qd-facet-lead">Filter</span>${groupBtns}
            ${any ? '<button type="button" class="qd-facet qd-facet-clear" data-qd-facet="clear">Clear</button>' : ''}
        </div><div class="qd-facets qd-facet-open">
            ${_fGroup === 'class' ? `<span class="qd-facet-lead">Kind</span>${cls}` : ''}
            ${_fGroup === 'stage' && moments ? `<span class="qd-facet-lead">Moment</span>${moments}` : ''}
            ${_fGroup === 'measured' ? `<span class="qd-facet-lead">Measured by</span>${meas}` : ''}
            ${_fGroup === 'engine' ? `<span class="qd-facet-lead">Engine</span>${eng}` : ''}
            ${_fGroup === 'role' && role ? `<span class="qd-facet-lead">Judge runs</span>${role}` : ''}
            ${_fGroup === 'band' && bands ? `<span class="qd-facet-lead">This turn</span>${bands}` : ''}
        </div>`;
    }

    let modalKeydown = null;
    function closeGuide() {
        const bd = document.getElementById('qd-modal-backdrop');
        if (!bd) return;
        bd.classList.remove('qd-show');
        if (modalKeydown) { document.removeEventListener('keydown', modalKeydown); modalKeydown = null; }
        setTimeout(() => { if (bd && bd.parentNode) bd.parentNode.removeChild(bd); }, 160);
    }
    // context: { rows: {key: {band, score, threshold}}, label } — the dimensions used by the
    // selected turn / agent, each with its live status (+ measured value & admin fail line).
    // When present the guide opens scoped to them and offers an "All dimensions" toggle plus a
    // global "Admin thresholds" toggle (only when admin boundaries actually apply).
    function openGuide(highlightKey, context) {
        injectCss();
        // Fetch-and-repaint rather than await: the registered dimensions render immediately and
        // the mechanisms fill in, so the Guide is never blocked on a network round trip.
        ensureVocabulary().then(() => {
            const l = document.getElementById('qd-modal-list');
            const sEl = document.querySelector('#qd-modal .qd-search');
            const fw = document.querySelector('#qd-modal .qd-facet-wrap');
            if (l) l.innerHTML = bodyHtml(sEl ? sEl.value : '');
            if (fw && fw._repaint) fw._repaint();
            // The glossary's Terms arrive with the vocabulary; repaint it in place.
            const gw = document.querySelector('#qd-modal .qd-gloss-wrap');
            if (gw) gw.innerHTML = glossaryHtml();
            // Re-mark the chosen entry — this repaint lands AFTER the first paint's
            // highlight and was destroying it (see _applyHighlight).
            if (l && l.dataset.qdHighlight) _applyHighlight(l, l.dataset.qdHighlight);
        });
        closeGuide();
        hideTip();
        _guideCtx = (context && context.rows && Object.keys(context.rows).length) ? context : null;
        // Opened AT an entry, the Guide shows that entry alone first — the catalogue, the
        // turn's rows, every facet and the search are one click away, never the default.
        // Opening the whole list and scrolling to a highlight made every arrival a search.
        _guideEntry = highlightKey || null;
        _guideScope = _guideEntry ? 'entry' : (_guideCtx ? 'context' : 'all');
        _guideShowThresh = false;
        _fClass = new Set();
        _fMeasured = new Set();
        _fBand = new Set();
        _fEngine = new Set();
        _fRole = new Set();
        _fStage = new Set();
        _fGroup = 'class';
        _guideCls = _guideCtx ? (_guideCtx.cls || null) : null;
        // Link-back guarantee: following a chip must LAND on the entry. The entry scope
        // shows the chosen key whatever the turn's rows hold, and `_scopeKeys` falls back
        // to everything while the vocabulary defining it is still landing — a modal that
        // opens empty is a broken link wearing a working one's clothes.
        // A pre-set narrowing applies only where it can mean something — with no context
        // there are no bands, and a filter that hides everything is worse than none.
        if (_guideCtx && Array.isArray(_guideCtx.bands) && _guideCtx.bands.length) {
            _guideCtx.bands.forEach((b) => _fBand.add(b));
        }
        const ctxN = _guideCtx ? Object.keys(_guideCtx.rows).length : 0;
        const allN = Object.keys(DIM).length;
        const hasThresholds = !!(_guideCtx && Object.values(_guideCtx.rows).some((r) => r && r.threshold != null));
        // The old subtitle ("what each score measures … graded on these signals") became
        // false when the mechanisms joined: a boundary is not a score and is not graded.
        const sub = _guideCtx
            ? (ctxN === 1
                ? `${esc(_guideCtx.label || 'This turn')} — with live status. `
                  + 'Its related entries are linked below.'
                : `Showing the ${ctxN} that applied to ${esc(_guideCtx.label || 'this turn')}, with live status.`)
            : 'Every measure, control and check behind an agent\u2019s answer — '
              + 'what it means, and how it is derived.';
        // The scope bar: the entry alone · the turn's rows · everything. Each button names
        // what it shows and how many. "Reading this guide" opens the lead-word glossary.
        const scopeBar = `<div class="qd-scope">
            ${_guideEntry ? `<button type="button" class="qd-scope-btn${_guideScope === 'entry' ? ' on' : ''}" data-qd-scope="entry">This entry</button>` : ''}
            ${_guideCtx ? `<button type="button" class="qd-scope-btn${_guideScope === 'context' ? ' on' : ''}" data-qd-scope="context">${esc(_guideCtx.label || 'This turn')} · ${ctxN}</button>` : ''}
            ${(_guideEntry || _guideCtx) ? `<button type="button" class="qd-scope-btn${_guideScope === 'all' ? ' on' : ''}" data-qd-scope="all">Everything · ${allN}</button>` : ''}
            ${hasThresholds ? `<button type="button" class="qd-scope-btn u-tip" data-qd-thresh aria-pressed="false" data-tip="Show the admin-configured pass/fail boundaries for each metric">Admin thresholds</button>` : ''}
            <button type="button" class="qd-scope-btn qd-scope-gloss u-tip" data-qd-gloss aria-pressed="false" data-tip="The lead words on every entry — upholds, escalates to, graded by, reads — defined once">Reading this guide</button>
        </div>`;
        const bd = document.createElement('div');
        bd.id = 'qd-modal-backdrop';
        bd.innerHTML = `<div id="qd-modal" role="dialog" aria-modal="true" aria-label="Assurance guide">
            <div class="qd-modal-head">
                <div>
                    <div class="qd-modal-title">Assurance Guide</div>
                    <div class="qd-modal-sub">${sub}</div>
                </div>
                <button class="qd-modal-close" type="button" aria-label="Close guide">${CLOSE_SVG}</button>
            </div>
            ${scopeBar}
            <div class="qd-facet-wrap"></div>
            <div class="qd-search-wrap"><input class="qd-search" type="text" placeholder="Search measures, controls and checks…" aria-label="Search the assurance guide" /></div>
            <div class="qd-modal-body">
                <div class="qd-gloss-wrap" hidden>${glossaryHtml()}</div>
                <div class="qd-legend">
                    <span><span class="qd-dot" style="background:var(--trust-pass, #10b981);"></span>Healthy</span>
                    <span><span class="qd-dot" style="background:var(--trust-warn, #f59e0b);"></span>Review</span>
                    <span><span class="qd-dot" style="background:var(--trust-danger, #ef4444);"></span>Danger</span>
                    ${_guideCtx ? `<span><span class="qd-dot" style="background:#64748b;"></span>Not set</span>
                    <span><span class="qd-dot" style="background:#94a3b8;"></span>Abstained</span>` : ''}
                    <span><span class="qd-dot" style="background:rgba(127,127,127,.4);"></span>Not applicable</span>
                    <span><span class="qd-mark" role="img" aria-label="Module">M</span>&nbsp;Module — the code that performs an assessment; opens the property it assesses</span>
                    <span><span class="qd-rel-same" role="img" aria-label="Same assessment">S</span>&nbsp;Same assessment — both read one module, so they cannot disagree</span>
                </div>
                <div id="qd-modal-list">${bodyHtml('')}</div>
            </div>
        </div>`;
        document.body.appendChild(bd);
        // eslint-disable-next-line no-unused-expressions
        bd.offsetHeight; // force reflow so the .qd-show transition plays
        bd.classList.add('qd-show');

        const list = bd.querySelector('#qd-modal-list');
        wireModuleLinks(list);   // delegated, so it survives every re-render
        const search = bd.querySelector('.qd-search');
        // ONE repaint for search, scope, facets and thresholds — and the chosen entry keeps
        // its mark through all of them (it used to vanish on the first facet click).
        const repaint = () => {
            list.innerHTML = bodyHtml(search.value);
            if (list.dataset.qdHighlight) _applyHighlight(list, list.dataset.qdHighlight, { scroll: false });
        };
        bd.querySelector('.qd-modal-close').addEventListener('click', closeGuide);
        bd.addEventListener('mousedown', (e) => { if (e.target === bd) closeGuide(); });
        search.addEventListener('input', repaint);
        bd.querySelectorAll('[data-qd-scope]').forEach((b) => b.addEventListener('click', () => {
            _guideScope = b.getAttribute('data-qd-scope');
            bd.querySelectorAll('[data-qd-scope]').forEach((x) => x.classList.toggle('on', x.getAttribute('data-qd-scope') === _guideScope));
            paintFacets();
            repaint();
        }));
        // ── the facet bar ────────────────────────────────────────────────────
        // Re-rendered on every change because the COUNTS shift with scope and with each
        // other: a pill reading "Controls 9" while the scope shows 4 would be a filter
        // lying about what it would give you.
        const facetWrap = bd.querySelector('.qd-facet-wrap');
        function scopeKeys() { return _scopeKeys(); }
        function paintFacets() {
            if (!facetWrap) return;
            facetWrap.innerHTML = facetBar(scopeKeys());
            facetWrap.querySelectorAll('[data-qd-facet]').forEach((b) => {
                b.addEventListener('click', () => {
                    const kind = b.getAttribute('data-qd-facet');
                    if (kind === 'clear') { _fClass.clear(); _fMeasured.clear(); _fBand.clear(); _fEngine.clear(); _fRole.clear(); _fStage.clear(); }
                    else if (kind === 'group') { _fGroup = b.getAttribute('data-qd-facet-v') || 'class'; }
                    else {
                        const set = kind === 'class' ? _fClass
                            : (kind === 'band' ? _fBand
                            : (kind === 'engine' ? _fEngine
                            : (kind === 'stage' ? _fStage
                            : (kind === 'role' ? _fRole : _fMeasured))));
                        const v = b.getAttribute('data-qd-facet-v');
                        if (set.has(v)) set.delete(v); else set.add(v);
                    }
                    paintFacets();
                    repaint();
                });
            });
        }
        paintFacets();
        if (facetWrap) facetWrap._repaint = paintFacets;

        // "Reading this guide": the lead-word glossary, folded above the legend. Toggled,
        // never re-rendered with the list, so it survives a search or a facet change.
        const glossBtn = bd.querySelector('[data-qd-gloss]');
        const glossWrap = bd.querySelector('.qd-gloss-wrap');
        if (glossBtn && glossWrap) glossBtn.addEventListener('click', () => {
            glossWrap.hidden = !glossWrap.hidden;
            glossBtn.classList.toggle('on', !glossWrap.hidden);
            glossBtn.setAttribute('aria-pressed', String(!glossWrap.hidden));
            // The body is scrolled to the chosen entry; the glossary sits above it, so
            // opening it must bring it into view or nothing visible changes.
            if (!glossWrap.hidden && glossWrap.scrollIntoView) glossWrap.scrollIntoView({ block: 'start' });
        });
        const threshBtn = bd.querySelector('[data-qd-thresh]');
        if (threshBtn) threshBtn.addEventListener('click', () => {
            _guideShowThresh = !_guideShowThresh;
            threshBtn.classList.toggle('on', _guideShowThresh);
            threshBtn.setAttribute('aria-pressed', String(_guideShowThresh));
            repaint();
        });
        modalKeydown = (e) => { if (e.key === 'Escape') closeGuide(); };
        document.addEventListener('keydown', modalKeydown);

        // The live bands arrive after the modal does. Patch the tuning lines IN PLACE
        // rather than re-rendering the list: a full re-render would throw away the
        // reader's scroll position, their search text and the highlighted row — the
        // three things that put them in front of the line they came to read.
        if (!_thresh && !_threshFailed) {
            ensureThresholds().then(() => {
                const m = document.getElementById('qd-modal');
                if (!m) return;                       // closed while the fetch was in flight
                m.querySelectorAll('.qd-tune[data-qd-tune]').forEach((node) => {
                    const tmp = document.createElement('div');
                    tmp.innerHTML = tuningLine(node.getAttribute('data-qd-tune'));
                    if (tmp.firstElementChild) node.replaceWith(tmp.firstElementChild);
                });
            });
        }

        if (highlightKey) {
            // Stamped on the list so the async vocabulary repaint can re-mark it; a
            // later openGuide overwrites the stamp, so it can never mark a stale entry.
            // Stamped even when the entry is not in DIM YET — a mechanism or station key
            // only exists after the vocabulary fetch, and the repaint is what reveals it.
            list.dataset.qdHighlight = highlightKey;
            _applyHighlight(list, highlightKey);
        } else if (!_guideCtx) {
            setTimeout(() => search.focus(), 60);
        }
    }

    /** Mark and reveal the entry the reader clicked to get here. Idempotent, so every
     *  repaint can re-run it — the vocabulary fetch repaints the list AFTER openGuide's
     *  first paint, and that repaint was silently wiping the highlight and the scroll:
     *  the Guide arrived correctly scoped but with no sign of WHICH entry was chosen. */
    function _applyHighlight(list, key, opts) {
        if (!list || !key) return;
        const row = list.querySelector(`[data-qd-key="${CSS.escape(key)}"]`);
        if (!row) return;
        list.querySelectorAll('.qd-row.qd-hi').forEach((r) => r.classList.remove('qd-hi'));
        row.classList.add('qd-hi');
        if (!(opts && opts.scroll === false)) row.scrollIntoView({ block: 'center' });
    }

    // Parse the nearest [data-dim-context] container into { rows, label }. The value is a JSON
    // array of {k, b, s?, t?} (key, band, score, threshold) so a context — including each
    // dimension's measured value and admin fail line — travels entirely in the DOM, robust
    // across concurrent cards with no shared global state. Falls back to the legacy
    // "key:band" CSV form for safety.
    function readContext(el) {
        const c = el && el.closest && el.closest('[data-dim-context]');
        if (!c) return null;
        const raw = c.getAttribute('data-dim-context') || '';
        const rows = {};
        try {
            const arr = JSON.parse(raw);
            if (Array.isArray(arr)) arr.forEach((o) => {
                if (o && o.k) rows[o.k] = { band: o.b || 'na', score: (o.s != null ? o.s : null),
                                            threshold: (o.t != null ? o.t : null), explanation: o.x || null };
            });
        } catch (_) {
            raw.split(',').forEach((p) => {
                const i = p.indexOf(':');
                const k = (i < 0 ? p : p.slice(0, i)).trim();
                const b = (i < 0 ? 'na' : p.slice(i + 1)).trim() || 'na';
                if (k) rows[k] = { band: b, score: null, threshold: null, explanation: null };
            });
        }
        return Object.keys(rows).length
            ? { rows, label: c.getAttribute('data-dim-context-label') || '',
                cls: c.getAttribute('data-dim-context-cls') || null,
                // A caller can open the Guide already narrowed — "2 of 20 below the line"
                // should land on those two, not make the reader find them.
                bands: (c.getAttribute('data-dim-filter') || '').split(',')
                    .map((x) => x.trim()).filter(Boolean) }
            : null;
    }

    // ── global delegation (works for every consumer view, present or future) ───────
    // A mark INSIDE an entry with its own tooltip (the S on a chip) speaks alone: the
    // entry's card steps aside while the pointer is on the mark, and returns on the name.
    // Both used to show at once — "Honesty — Catches an answer…" over "Same assessment…"
    // (operator, 2026-10-30).
    const ownTipInside = (e, el) => {
        const t = e.target.closest && e.target.closest('.u-tip');
        return !!(t && t !== el && el.contains(t));
    };
    function initDelegation() {
        document.addEventListener('mouseover', (e) => {
            const el = e.target.closest && e.target.closest('[data-dim]');
            if (!el) return;
            if (ownTipInside(e, el)) { hideTip(); return; }
            showTip(el);
        }, true);
        document.addEventListener('mouseout', (e) => {
            const el = e.target.closest && e.target.closest('[data-dim]');
            if (el) hideTip();
        }, true);
        document.addEventListener('focusin', (e) => {
            const el = e.target.closest && e.target.closest('[data-dim]');
            if (el) showTip(el);
        });
        document.addEventListener('focusout', hideTip);
        window.addEventListener('scroll', hideTip, true);
        // click / keyboard → open the guide at that dimension (or the top for a guide button),
        // scoped to the nearest [data-dim-context] container (the selected turn / agent).
        document.addEventListener('click', (e) => {
            const gb = e.target.closest && e.target.closest('[data-dim-guide]');
            if (gb) { e.preventDefault(); openGuide(null, readContext(gb)); return; }
            // The id mark: copy the wire key. The clipboard may refuse (older desktop
            // apps, some app views); then the key is selected instead, never lost.
            const cp = e.target.closest && e.target.closest('[data-qd-copy]');
            if (cp) {
                e.preventDefault();
                const key = cp.getAttribute('data-qd-copy') || '';
                const done = () => { cp.classList.add('qd-id-done'); setTimeout(() => cp.classList.remove('qd-id-done'), 900); };
                const fallback = () => {
                    const node = cp.querySelector('.qd-row-key');
                    try { const r = document.createRange(); r.selectNodeContents(node); const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); } catch (_) { /* nothing to select */ }
                };
                if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(key).then(done, fallback);
                else fallback();
                return;
            }
            const el = e.target.closest && e.target.closest('[data-dim]');
            if (el) { e.preventDefault(); hideTip(); openGuide(el.getAttribute('data-dim'), readContext(el)); }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            const el = e.target.closest && e.target.closest('[data-dim][tabindex]');
            if (el) { e.preventDefault(); openGuide(el.getAttribute('data-dim'), readContext(el)); }
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { injectCss(); initDelegation(); });
    } else { injectCss(); initDelegation(); }

    // ── context helpers (scope the guide to a turn / agent + carry live status) ─────
    // Band of a verdict-like item: explicit band → else pass/fail → else N/A.
    const bandOfItem = (it) => it.band
        || (it.passed === false ? 'danger' : it.passed === true ? 'healthy' : 'na');
    // items: [{key|dimension, band?, passed?, score?, threshold?, explanation?}] →
    // JSON [{k,b,s?,t?,x?}]. The abstention reason (x) is carried only for non-scored rows,
    // so a "Not set" pill can show WHY (e.g. "no optimal-tasks target"); keeps the payload lean.
    function _ctxJson(items) {
        const rows = (items || []).map((it) => {
            const k = it.key || it.dimension;
            if (!k) return null;
            const band = bandOfItem(it);
            const o = { k, b: band };
            if (it.score != null) o.s = it.score;
            if (it.threshold != null) o.t = it.threshold;
            if (band === 'na' && it.explanation) o.x = String(it.explanation).slice(0, 160);
            return o;
        }).filter(Boolean);
        return rows.length ? JSON.stringify(rows) : '';
    }
    // For HTML-string builders (e.g. the roster): the attribute string to place on a container.
    function contextAttrs(items, label, cls) {
        const j = _ctxJson(items);
        return j ? `data-dim-context="${esc(j)}" data-dim-context-label="${esc(label || '')}"${cls ? ` data-dim-context-cls="${esc(cls)}"` : ''}` : '';
    }
    // For DOM builders (e.g. the Live Status card): stamp the context onto an element.
    function setContext(el, items, label, cls) {
        if (!el) return;
        const j = _ctxJson(items);
        if (j) {
            el.setAttribute('data-dim-context', j);
            el.setAttribute('data-dim-context-label', label || '');
            if (cls) el.setAttribute('data-dim-context-cls', cls); else el.removeAttribute('data-dim-context-cls');
        } else {
            el.removeAttribute('data-dim-context'); el.removeAttribute('data-dim-context-label'); el.removeAttribute('data-dim-context-cls');
        }
    }

    // ── public API ────────────────────────────────────────────────────────────────
    // guideButton(text?) → HTML for a discoverable "Dimension guide" pill.
    function guideButton(text) {
        return `<button type="button" class="qd-guide-btn u-tip" data-dim-guide aria-label="Open the assurance guide" data-tip="What do these mean?">${GUIDE_SVG}<span>${esc(text || 'Guide')}</span></button>`;
    }
    /**
     * Register non-dimension entries so the Guide can represent them.
     *
     * The rule that produced this: **any item an instrument lists must be lookupable
     * here.** The assurance dial shows mechanisms — an inbound guard, a preflight check,
     * review parking — that are real, enforced, and in several cases scored by NO single
     * dimension. Leaving them unlinkable made the Guide look arbitrary: some rows opened,
     * some did nothing, and nothing on screen explained the difference.
     *
     * Their descriptions say which dimensions measure them, or that several do and no
     * one score stands for the whole, or that none do and why — plus how the reading is
     * derived, because a reading without its derivation invites the reader to guess, and
     * the guess is usually "a model decided". For most of these it is deterministic code.
     *
     * Merged, never overwriting: a real scored dimension always wins, so this can never
     * quietly redefine one.
     */
    //: Which dimensions a registered mechanism relates to. Kept OUT of its prose so
    //: they can be rendered as chips: a reader looking at "grounding gate" should be
    //: able to step into `answer_groundedness`, read it, and step back — which is what
    //: the Trust Circle's `?` explainer already does with `data-dim` chips. Naming them
    //: in a sentence makes them a dead end.
    const RELATED = {};
    const ENTRY_NAME = {};
    // module -> the entries that read it. `graded by` says a measurement grades this
    // mechanism; it does NOT say whether the two are the SAME computation, which is the
    // difference the whole symmetry programme is about. Of the nine dimensions grading
    // the grounding gate, three read the module the gate acted on — they cannot disagree
    // with it — and six are independent readings. Nothing said which.
    let SHARED_MODULES = {};
    const MODULE_LABEL = {};
    /** How each module assesses, and whose it is. Filled from the served lanes rows. */
    const MODULE_DESC = {};
    const MODULE_ORIGIN = {};
    /** Hovering either end of a shared assessment lights the other.
     *
     *  The S badge said a module is shared and only a tooltip said which. The arrow makes
     *  the direction visible and the highlight names the target — without pretending the
     *  badge navigates, because the chip it sits in already routes to the sibling ENTRY.
     */
    function wireModuleLinks(root) {
        if (!root || root.__qdLinked) return;
        root.__qdLinked = true;
        // The partners of one end, within its ENTRY: an S's M and an M's S's live in the same
        // entry — and since modules and graders moved into folds, often inside a CLOSED one.
        const partners = (el) => {
            const entry = el.closest('.qd-row') || root;
            return [...entry.querySelectorAll(`[data-mod="${CSS.escape(el.dataset.mod)}"]`)];
        };
        // A partner hidden in a closed fold is shown by lighting the fold's header: the reader
        // sees WHERE it is without the layout jumping under the pointer.
        const target = (el) => {
            const fold = el.closest('details:not([open])');
            return fold ? fold.querySelector('summary') : el;
        };
        const paint = (el, on) => {
            if (!el || !el.dataset.mod) return;
            partners(el).forEach((p) => target(p).classList.toggle('qd-linked', on));
        };
        const at = (e) => (e.target.closest && e.target.closest('[data-mod]'));
        root.addEventListener('mouseover', (e) => paint(at(e), true));
        root.addEventListener('mouseout', (e) => paint(at(e), false));
        root.addEventListener('focusin', (e) => paint(at(e), true));
        root.addEventListener('focusout', (e) => paint(at(e), false));
        // A click on the LETTER follows the link: the folds holding its partners open, the
        // partners light, the first one other than itself comes into view. A click on the
        // NAME still opens that entry (the document handler) — the letter is the link's end,
        // the name is the entry's.
        root.addEventListener('click', (e) => {
            const letter = e.target.closest && e.target.closest('.qd-rel-same, .qd-mod .qd-mark');
            if (!letter) return;
            const end = letter.closest('[data-mod]');
            if (!end) return;
            e.preventDefault();
            e.stopPropagation();
            const ps = partners(end);
            ps.forEach((p) => { let d = p.closest('details'); while (d) { d.open = true; d = d.parentElement && d.parentElement.closest('details'); } });
            ps.forEach((p) => p.classList.add('qd-linked'));
            const other = ps.find((p) => p !== end && !end.contains(p) && !p.contains(end));
            if (other && other.scrollIntoView) other.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            clearTimeout(root.__qdLinkTimer);
            root.__qdLinkTimer = setTimeout(() => ps.forEach((p) => p.classList.remove('qd-linked')), 2200);
        });
    }

    /** Every module this entry reads, each with the property it assesses. */
    function modulesOf(k) {
        const out = [];
        for (const [mod, ks] of Object.entries(SHARED_MODULES)) {
            if (ks.indexOf(k) < 0) continue;
            const prop = ks.find((o) => /^property_/.test(o)) || null;
            out.push({ mod, prop });
        }
        return out;
    }

    /** A module NAME, marked as one.
     *
     *  Modules were rendered as bare text, indistinguishable from an entry name a reader
     *  could open — and on a measurement or a control they were not rendered at all, so
     *  the S badge asserted a shared assessment the entry never named. Both are fixed
     *  here: the M badge says what the name IS, and the name itself opens the PROPERTY
     *  the module assesses (a module has no entry of its own).
     *
     *  NO ARROW. It carried the route honestly while modules and siblings shared a row,
     *  but once they moved into separate rows the glyph sat BETWEEN two chips and read as
     *  "points at the next one" — a chain that does not exist. The route survives as the
     *  button itself; direction is carried by the hover link, which names the real target.
     */
    function moduleName(mod, label, prop) {
        // Same badge form as the S: a letter is the channel, so it survives greyscale.
        // No `u-tip` here: the button already shows the module tooltip, and two tooltip
        // systems on one hover render two boxes.
        const body = `${esc(label || mod)}<span class="qd-mark" role="img"
            aria-label="Module">M</span>`;
        return prop
            ? `<button type="button" class="qd-mod qd-mod-route" data-dim="${esc(prop)}"
                data-mod="${esc(mod)}">${body}</button>`
            : `<span class="qd-mod" data-mod="${esc(mod)}">${body}</span>`;
    }

    /** The module a MEASURE reads — the other side of every S badge. A mechanism names its
     *  properties instead (`propertiesRow`) and folds its modules (`modulesFold`); a
     *  property's `assessed by` lanes row is its own module list (Rule 3.0). */
    function modulesRow(k) {
        if (ENTRY_CLASS[k] === 'property' || (PROPERTIES_OF[k] || []).length) return '';
        const mods = modulesOf(k);
        if (!mods.length) return '';
        return `<div class="qd-row-rel">${leadHtml('reads')}${kindCount('reads', mods.length)}${
            mods.map((m) => moduleName(m.mod, MODULE_LABEL[m.mod], m.prop)).join('')}</div>`;
    }

    /** What a mechanism assesses: its PROPERTIES, each opening its own entry. The stable
     *  half of the relation — a module is the current implementation and can be rebound
     *  per lane; the property is what the mechanism is about. (It listed modules under
     *  "reads", which skipped the property and named the code where the meaning belongs.)
     *  NOT on a property: its `assessed by` lanes row is its own module list. */
    function propertiesRow(k) {
        if (ENTRY_CLASS[k] === 'property') return '';
        // The lead IS the kind, so the row carries no kind label beside it (it read
        // "PROPERTIES · PROPERTIES · 3"); the chips are the count. No S badge: the
        // fold below pairs each property with its module (chipRow omits it on this row).
        return chipRow('properties', PROPERTIES_OF[k] || [], k);
    }

    /** One property beside the module bound to it in a lane, the lane's power on hover.
     *  An unbound lane says so — the row never vanishes. */
    const MOD_MODE = { decides: 'can act on the answer', escalates: 'can only escalate to a judge',
                       grades: 'reports only' };
    function modPair(p, l) {
        const tip = MOD_MODE[l.mode] || '';
        return `<div class="qd-row-rel qd-mod-pair"><span class="qd-pair-k${tip ? ' u-tip' : ''}"${
                   tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(label(p))}</span>${
               l.assessor ? moduleName(l.assessor, l.label, p)
                          : `<em class="qd-mod-none">${esc(l.note || 'nothing bound')}</em>`}</div>`;
    }

    /** The modules of the properties this mechanism does NOT consult during the turn: they
     *  are used after it, by the measures that score them — so they sit in "after the turn",
     *  above "graded by", where the S on those measures finds them. (Without these rows, an S
     *  on Honest absence pointed at a module the Honesty gate's entry never named.) */
    function afterModules(k) {
        if (ENTRY_CLASS[k] === 'property') return '';
        const consulted = new Set(ENTRY_CONSULTS[k] || []);
        return (PROPERTIES_OF[k] || []).filter((p) => !consulted.has(p)).map((p) => {
            const l = (LANES[p] || []).find((x) => x.lane === 'POST_HOC') || {};
            // nothing scores it after the turn either: the property's own entry says why
            return l.assessor ? modPair(p, l) : '';
        }).join('');
    }

    /** The modules this mechanism consults DURING the turn, one per property, folded like
     *  "after the turn". Only what THIS mechanism consults (`consults`, the declaration the
     *  dial's mark reads): a property it owns but does not consult in flight has its
     *  module on the property's own entry. An unbound lane says so, never vanishes. */
    function modulesFold(k) {
        if (ENTRY_CLASS[k] === 'property') return '';
        const ps = ENTRY_CONSULTS[k] || [];
        if (!ps.length) return '';
        const rows = ps.map((p) => modPair(p, (LANES[p] || []).find((x) => x.lane === 'IN_FLIGHT') || {})).join('');
        return `<details class="qd-rel-grp qd-rel-fold"><summary class="qd-rel-grp-h">modules during the turn`
             + `<span class="qd-fold-n"> \u00b7 ${ps.length}</span></summary>${rows}</details>`;
    }

    /** The module two entries share, or null. */
    function sharedModule(a, b) {
        if (!a || !b || a === b) return null;
        for (const [mod, ks] of Object.entries(SHARED_MODULES)) {
            if (ks.indexOf(a) >= 0 && ks.indexOf(b) >= 0) return mod;
        }
        return null;
    }

    //: ONE verb pair per member kind: how a guarantee names the member, and how the member
    //: names its guarantee. The return label was a constant `upholds`, so a measure (Prompt
    //: cache, Recovery) read "upholds SIGNED" while SIGNED listed it as "evidenced by" —
    //: one relation, two roles. Kinds come from the registry's own `POINT_CLASS`.
    const MEMBER_VERBS = {
        control: ['upheld by', 'upholds'], boundary: ['bounded by', 'bounds'],
        attestation: ['attested by', 'attests'], measure: ['evidenced by', 'evidence for'],
    };
    /** How a member names the guarantee it stands under — its kind's verb. */
    function memberVerb(k) {
        return (MEMBER_VERBS[ENTRY_CLASS[k]] || MEMBER_VERBS.control)[1];
    }

    /** The entries this one sits UNDER — every parent, by every relation.
     *
     *  NOTHING STORES A PARENT. Every relation is served ONCE, on the parent, as its list
     *  of children (a guarantee's `controls` and `counts`, a mechanism's `dims` and
     *  `properties`, a moment's measures); a parent is that list read backwards. A
     *  per-entry `parents` field would be a second copy of each relation, free to
     *  disagree with the first. A guarantee is a root and returns none.
     *
     *    mechanism → the guarantee it upholds
     *    measure   → the mechanisms it grades or decides for · the moment it is taken at
     *    moment    → the guarantee that counts it
     *    property  → the mechanisms it is assessed for
     */
    function parentsOf(k) {
        const out = [...(UPHOLDS[k] || []), ...(USED_BY[k] || []),
                     ...(COUNTED_AT[k] || []), ...(ASSESSED_FOR[k] || [])];
        const st = stageOf(k);
        if (st) out.push('stage_' + st);
        return [...new Set(out)];
    }

    //: station → the mechanisms that UPHOLD it, and the reverse. A separate
    //: relation from RELATED (which is 'what grades this'), because a control
    //: upholding a guarantee and a measurement grading it are opposite roles —
    //: the distinction the whole vocabulary exists to keep.
    const CONTROLS = {};
    const UPHOLDS = {};
    //: mechanism → the dimensions it COUNTS. Opposite direction from RELATED: these are
    //: its output, not its assessors. Only `evaluation` has any, and it now counts every
    //: scored dimension rather than the residue it held under the retired partition.
    const COUNTS = {};
    //: The reverses the Guide had no row for: the guarantee that counts a moment, and the
    //: mechanisms a property is assessed for (the inverse of a mechanism's `properties`).
    const COUNTED_AT = {};
    const PROPERTIES_OF = {};
    //: The properties a mechanism CONSULTS during the turn (served `consults`).
    const ENTRY_CONSULTS = {};
    const ASSESSED_FOR = {};
    //: The mechanisms the registry records as deliberately off a turn's path. Still a
    //: member of their station; marked on its row so the Guide and the dial differ visibly.
    const ENTRY_EXCLUDED = new Set();
    //: The REVERSE index: which mechanisms a dimension measures.
    //:
    //: Forward links alone are a one-way trip — you can step from a mechanism into a
    //: dimension and then have nowhere to go. Building the reverse as entries register
    //: means a dimension can point back at everything that relies on it, which is what
    //: makes the Guide navigable rather than a list you re-enter from outside.
    const USED_BY = {};

    /**
     * The two facets every Guide entry carries.
     *
     *   CLASS      what it IS — determines what SETTING it takes:
     *              measure (threshold) · control (mode) · boundary (scope) ·
     *              attestation (signature). If two entries take the same kind of
     *              action they are the same class; that test produced these four.
     *   MEASURED   how many scored dimensions measure it — determines what a CLICK
     *              gives you: atomic (its band IS the reading) · composite (no single
     *              score stands for it; the chips are the reading) · unmeasured (the
     *              reading comes from the mechanism's own state).
     *
     * They are independent: every class contains all three measurements, which is how
     * we know this is a second facet rather than a fifth class.
     *
     * A scored dimension needs no entry here — it is a Measure, and it IS its own
     * dimension, so `measure`/`atomic` is the default rather than 52 repeated rows.
     */
    const ENTRY_CLASS = {};
    const GRADING = {};
    const LANES = {};
    const SYMMETRY = {};
    const ENTRY_MEASURED = {};
    //: WHEN a mechanism acts, and WHAT an operator can change (registry `MECHANISM_LIMITS`,
    //: with every number DERIVED from the constant that decides it). The Guide said what a
    //: mechanism is and how its reading is derived, and never these two — so a reader
    //: choosing a mode could not tell that the grounding trigger fires on a SINGLE
    //: unsupported number, nor that its sensitivity is not configurable at all.
    const ENTRY_FIRES = {}, ENTRY_TUNABLE = {};
    //: WHAT a mechanism can do to the answer (registry `MECHANISM_FATES`, served per
    //: entry) and the QUESTION a guarantee answers (the station registry's `q`). Both
    //: feed the "can" line — the one slot that answers "does this withhold, or only
    //: classify?" without the reader decoding a relation word.
    const ENTRY_FATES = {}, ENTRY_Q = {};
    //: HOW it decides / counts / reads (the registry's derivation — its own row now, not a
    //: sentence inside the description), what cannot be tuned (`fixed`), the design's
    //: reasoning (`why`, folded), and a station's three reading rows (`reads`: on a turn ·
    //: on the ring · its checks).
    const ENTRY_DERIVATION = {}, ENTRY_FIXED = {}, ENTRY_WHY = {}, ENTRY_READS = {}, ENTRY_HOW = {};
    //: The KIND of thing a relation row lists, by its lead — rendered beside the lead with
    //: the count, so the nouns a sentence refers to ("the controls under in the turn")
    //: are on the rows themselves. Derived from the row at render time, never typed.
    // `grades` lists a mix (mechanisms and guarantees), so it carries no single noun.
    const KIND_OF_LEAD = { 'upheld by': 'control', 'bounded by': 'boundary', 'evidenced by': 'measure',
                           'attested by': 'attestation', 'graded by': 'measure',
                           'escalates to': 'judge', 'decides for': 'gate', 'reads': 'module',
                           'weights': 'measure', 'weighs on': 'measure',
                           'upholds': 'guarantee', 'bounds': 'guarantee', 'attests': 'guarantee',
                           'evidence for': 'guarantee', 'counts': 'moment', 'taken at': 'moment',
                           'counted at': 'guarantee', 'assessed for': 'mechanism' };
    const KIND_PLURAL = { control: 'controls', boundary: 'boundaries', measure: 'measures',
                          attestation: 'attestations', judge: 'judges', gate: 'gates',
                          module: 'modules', guarantee: 'guarantees', moment: 'moments',
                          mechanism: 'mechanisms', property: 'properties' };
    function kindCount(lead, n) {
        const kind = KIND_OF_LEAD[lead];
        if (!kind || !n) return '';
        const word = n === 1 ? kind : (KIND_PLURAL[kind] || kind + 's');
        const tip = (typeof KIND_TIP !== 'undefined' && (KIND_TIP.find((t) => t[0] === kind) || [])[1]) || '';
        return `<span class="qd-kindn${tip ? ' u-tip' : ''}"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(word)}${n > 1 ? ` · ${n}` : ''}</span>`;
    }
    //: The reader's word for each entry class, singular — the title-line tag. The plural
    //: CLASS_LABEL names a SHELF; a tag names ONE thing.
    const KIND_WORD = { property: 'Property', station: 'Guarantee', stage: 'Moment', measure: 'Measure',
                        control: 'Control', boundary: 'Boundary', attestation: 'Attestation' };
    //: The fates in the reader's words. `stopped` is the hard one and renders solid.
    const FATE_WORD = { stopped: 'stops the answer', changed: 'changes the answer',
                        noted: 'adds a note', refused: 'refuses a step' };
    const FATE_TIP = {
        stopped: 'The answer is withheld — and kept for a person when the gate is set to review.',
        changed: 'The answer is revised, redacted or replaced before delivery.',
        noted: 'A disclosure is appended; the answer itself is untouched.',
        refused: 'A step the agent wanted — a query, a tool call, a document, an action — is refused. The answer goes on with less.',
    };
    //: Every lead word the Guide prints, defined ONCE. The leads are precise terms and
    //: they stay; what was missing was the definition at the place a reader meets them.
    //: Rendered as the platform tooltip (`u-tip`) on each lead and as the glossary the
    //: header's "Reading this guide" opens.
    const LEAD_TIP = {
        'upholds': 'The guarantee this control exists to keep.',
        'bounds': 'The guarantee this boundary sets a limit for.',
        'attests': 'The guarantee this attestation is a cryptographic fact for.',
        'evidence for': 'The guarantee this measure puts a fact on the record for. It keeps nothing itself.',
        'upheld by': 'The mechanisms that run during the turn to keep this guarantee. They can act on the answer.',
        'bounded by': 'The standing limits this guarantee is measured against. Set before the turn; they act on nothing.',
        'evidenced by': 'Members that only observe a fact about the turn and put it on the record: measures. They neither act nor limit.',
        'attested by': 'Cryptographic facts. Verified, never measured.',
        'escalates to': 'The judges this mechanism consults before it decides. They can change its verdict.',
        'graded by': 'The measures that score it after the turn. They report only and never change a verdict.',
        'reads': 'The module — the code that computes this measure. A module shared with a mechanism means the two cannot disagree.',
        'properties': 'What this mechanism assesses. Each opens its own entry, which names the module bound to it during and after the turn.',
        'weights': 'The checks this aggregate counts as trust-critical: a failure here is a trust failure, not a quality dip. Every other scored check shares the remaining weight.',
        'weighs on': 'The aggregate that counts this check as trust-critical.',
        'counts': 'The measures this station tallies.',
        'decides for': 'The mechanism that consults this measure before deciding.',
        'grades': 'What this measure scores after the turn.',
        'assessed by': 'Which module assesses this property in each lane, and what that module may do.',
        'taken at': 'The moment of the turn this measure is taken at.',
        'counted at': 'The guarantee whose tally counts the measures taken at this moment.',
        'assessed for': 'The mechanisms that act on what this property\u2019s module finds.',
        'how it decides': 'How this control reaches its verdict during the turn.',
        'how it reads': 'How this point’s reading is derived.',
        'fires when': 'The triggers. Any one of them is enough.',
        'then': 'What happens to a hit before anything is reported.',
        'also': 'A side effect worth knowing.',
        'ignores': 'What the gate looks past, because it is not a claim.',
        'you can change': 'The settings that govern it, each with its effect.',
        'fixed': 'What you might expect to tune and cannot, with the reason.',
        'why': 'The design’s reasoning behind these rules.',
        'threshold': 'The pass line an admin can set for it, or why there is none.',
        'for one answer': 'What colours this station in the Live Status dial, the instrument under one answer.',
        'for an agent': 'What colours this station in the Trust Circle, the ring that reads an agent over its recent turns: the worse of configuration (could it act?) and execution (what did it do?), raised by open inbox items.',
        'its measures': 'Where the measures about this guarantee are counted.',
    };
    const LEAD_ORDER = ['upholds', 'bounds', 'attests', 'evidence for', 'how it decides', 'how it reads', 'properties', 'reads', 'weights', 'fires when', 'then', 'also', 'ignores',
                        'escalates to', 'decides for', 'you can change', 'fixed', 'why',
                        'upheld by', 'bounded by', 'evidenced by', 'attested by', 'for one answer', 'for an agent', 'its measures',
                        'graded by', 'grades', 'weighs on', 'counts', 'counted at', 'taken at', 'threshold',
                        'assessed for', 'assessed by'];
    //: The KINDS, defined once beside the leads: the umbrella word "mechanism" and the
    //: four things it stands for, and the check that is not one.
    const KIND_TIP = [
        ['member', MEMBER_TIP],
        ['mechanism', 'The umbrella word for the four kinds that stand under a guarantee: a control, a boundary, an attestation or a measure.'],
        ['control', 'Runs during the turn and can act on the answer. Set by a mode.'],
        ['boundary', 'Standing scope, set before the turn. It limits what the agent can reach and acts on nothing.'],
        ['attestation', 'A cryptographic fact. Verified, never measured.'],
        ['measure', 'Taken after the answer; it cannot act. The scored ones are graded against thresholds and counted at JUDGED; a few registered ones also give a reading on the Live Status dial for one answer.'],
        ['judge', 'A measure an LLM panel returns. A gate may ask one before deciding; the rest grade afterwards.'],
        ['module', 'The code that performs an assessment. A module shared between a gate and a measure means the two cannot disagree.'],
    ];
    //: The two surfaces and the ring's two readings, defined once beside the kinds.
    const RING_TIP = [
        ['Live Status dial', 'The instrument under one answer: what ran for that answer and how it went.'],
        ['Trust Circle', 'The ring of eight segments that reads an agent, or the whole estate, over its recent turns.'],
        ['configuration', 'Could it act? How the station\u2019s gate is set on each agent in view: enforce shows green, watch shows hollow green (watching), off shows not set. With several agents, the weakest decides.'],
        ['execution', 'What did it do? What happened in the recent turns: a guarantee station\u2019s own acts, as text; JUDGED\u2019s grades; REVIEWED\u2019s waiting items; PROVEN\u2019s sealed turns.'],
        ['open inbox items', 'Findings attributed to the station that nobody has decided yet. They can only raise a station\u2019s attention, never lower it.'],
    ];
    /** A lead word with its definition on hover. `text` lets a row qualify the lead
     *  ("graded by · 2 of 5 for this class") while the tooltip stays keyed on the term. */
    function leadHtml(lead, text, cls) {
        const tip = LEAD_TIP[lead];
        return `<span class="${cls || 'qd-rel-lead'}${tip ? ' u-tip' : ''}"${tip ? ` data-tip="${esc(tip)}"` : ''}>${esc(text || lead)}</span>`;
    }
    //: The TERMS the rows may use, served with the vocabulary (registry `GUIDE_TERMS`).
    //: The rule: a term is used on screen only if the screen explains it. The registry
    //: gate bans the code's own words from the rows; this list explains the rest.
    let TERMS = [];
    function registerTerms(list) {
        if (!Array.isArray(list)) return;
        TERMS = list.filter((t) => t && t.term && t.def).map((t) => [String(t.term), String(t.def)]);
    }
    /** The glossary block the header's "Reading this guide" opens: every lead, the kinds,
     *  the ring's two readings, and the terms the rows use — each defined once. */
    function glossaryHtml() {
        return `<div class="qd-gloss"><div class="qd-gloss-h">Reading this guide</div>
            <div class="qd-gloss-sub">The lead words on every entry, defined once. Each also shows its definition on hover.</div>
            <dl class="qd-gloss-dl">${LEAD_ORDER.filter((l) => LEAD_TIP[l]).map((l) =>
                `<dt>${esc(l)}</dt><dd>${esc(LEAD_TIP[l])}</dd>`).join('')}</dl>
            <div class="qd-gloss-h qd-gloss-h2">The kinds</div>
            <dl class="qd-gloss-dl">${KIND_TIP.map(([w, d]) => `<dt>${esc(w)}</dt><dd>${esc(d)}</dd>`).join('')}</dl>
            <div class="qd-gloss-h qd-gloss-h2">On the ring</div>
            <dl class="qd-gloss-dl">${RING_TIP.map(([w, d]) => `<dt>${esc(w)}</dt><dd>${esc(d)}</dd>`).join('')}</dl>
            ${TERMS.length ? `<div class="qd-gloss-h qd-gloss-h2">Terms used on the rows</div>
            <dl class="qd-gloss-dl">${TERMS.map(([w, d]) => `<dt>${esc(w)}</dt><dd>${esc(d)}</dd>`).join('')}</dl>` : ''}</div>`;
    }
    /** The "can" line — what this entry can do to an answer, in five words or fewer.
     *  A mechanism's fates come from the registry; a guarantee shows the question it
     *  answers; a check says whether a gate consults it before deciding. A moment and a
     *  property carry no verdict of their own, so they carry no line. */
    function canLine(k, c) {
        if (c === 'station') {
            const q = ENTRY_Q[k];
            return q ? `<div class="qd-can"><span class="qd-can-lead">asks</span><span class="qd-can-q">${esc(q)}</span></div>` : '';
        }
        if (c === 'stage' || c === 'property') return '';
        let pills;
        if (ENTRY_CLASS[k]) {
            const f = ENTRY_FATES[k];
            if (!Array.isArray(f)) return '';           // an older payload: an honest absence, never a guess
            pills = f.length
                ? f.map((x) => `<span class="qd-pill${x === 'stopped' ? ' qd-pill-stop' : ''} u-tip" data-tip="${esc(FATE_TIP[x] || '')}">${esc(FATE_WORD[x] || x)}</span>`).join('')
                : `<span class="qd-pill qd-pill-none u-tip" data-tip="Taken after the answer, or a verified fact. It cannot touch the answer.">reports only</span>`;
        } else {
            pills = decidesInPath(k)
                ? `<span class="qd-pill u-tip" data-tip="A gate consults this check before it decides, so its verdict can change the answer's fate.">can change a verdict, when asked first</span>`
                : `<span class="qd-pill qd-pill-none u-tip" data-tip="Scored after the answer. It grades; it never acts.">reports only</span>`;
        }
        return `<div class="qd-can"><span class="qd-can-lead">can</span>${pills}</div>`;
    }
    const CLASS_LABEL = { property: 'Properties',
                          station: 'Guarantees', stage: 'Moments', measure: 'Measures',
                          control: 'Controls', boundary: 'Boundaries',
                          attestation: 'Attestations' };
    const CLASS_NOTE = {
        property: 'What is actually assessed — the thing a guarantee promises, a control upholds and a measurement grades. This is the unit a module is bound to.',
        // A station is neither a measure nor a control: it is the GUARANTEE those
        // mechanisms exist to uphold. Filing it as an oversized mechanism would lose
        // exactly the distinction a reader needs when a card names one.
        station: 'What the agent guarantees. Upheld by the mechanisms beneath it, and '
               + 'it can never read better than the weakest of them.',
        // The sixth class. The Guide could explain a GUARANTEE and a MECHANISM but not a
        // MOMENT OF THE TURN — while a moment is how the 52 measurements are grouped. A
        // reader who saw a shelf headed "Compose" had nowhere to ask what it meant.
        stage: 'A point in the turn. Measurements are grouped by the moment they are '
             + 'taken, so a card reads in the order the turn actually ran.',
        measure: 'Taken after the answer — it cannot change it. Tuned by a threshold.',
        control: 'Runs during the turn and can alter or withhold the answer. Set by a mode.',
        boundary: 'Standing configuration limiting what the agent can reach.',
        attestation: 'A cryptographic fact — you verify it; there is no scale.',
    };
    const MEASURED_LABEL = { atomic: 'One dimension', composite: 'Several dimensions',
                             unmeasured: 'Not scored' };
    // Same four words as the legend already shown — it was a key with no control;
    // making it filterable is what turns a count into a link.
    const BAND_LABEL = { danger: 'Danger', review: 'Review', healthy: 'Healthy',
                         na: 'Not applicable' };
    const classOf = (k) => ENTRY_CLASS[k] || 'measure';
    // A GATE counts what its entry shows — the measures using its module and its judges —
    // so the tag, the "Measured by" filter and the after-the-turn section agree. (The served
    // count reads the filing list, which no longer appears on a gate's entry.)
    const measuredOf = (k) => {
        if (!isMechanism(k)) return ENTRY_MEASURED[k] || 'atomic';
        const j = ENTRY_JUDGES[k] || [];
        const n = moduleSharers(k).filter((d) => j.indexOf(d) < 0).length + j.length;
        return n > 1 ? 'composite' : (n === 1 ? 'atomic' : 'unmeasured');
    };

    /** Chips for the dimensions a mechanism spans — clickable, so the reader can step
     *  in and back out. They route through the Guide's own `data-dim` delegation, which
     *  means this adds no second navigation channel (contract Rule 3b.3). */
    /** Does entry `k` apply to the class the Guide was opened for? True with no class
     *  context or no restriction. EXACT membership against the served `applies_to`, as the
     *  registry (`AssurancePoint.applies`) and the scorers (`Scorer.applies_to`) decide it —
     *  `conversation_with_tools` is listed explicitly wherever it applies, so folding it
     *  into Ideate here would say Preflight SQL applies to a plain Ideate agent. */
    function appliesToGuideCls(k) {
        if (!_guideCls) return true;
        const raw = ENTRY_APPLIES[k] || APPLIES_TO[k] || null;
        if (!raw || !raw.length) return true;
        return raw.includes(_guideCls);
    }
    function chipRow(lead, keys, owner) {
        if (!Array.isArray(keys) || !keys.length) return '';
        // S and M are the two ends of ONE link: the S says "this one uses the same module",
        // the M names that module, and pointing at either lights the other. So the S stands
        // wherever the pair shares a module — except on a mechanism's `properties` row, where
        // the fold already pairs each property with its module side by side (an S there was a
        // second link for the same pair, and read as a claim).
        const same = (d) => (lead === 'properties' ? null : sharedModule(owner, d));
        // Class scope: opened for one agent, a chip whose entry does not apply to that
        // agent's class is dimmed, dashed and tagged — the dial's station rows mark the
        // same mechanisms the same way, so the two lists can be read against each other.
        const off = keys.filter((d) => !appliesToGuideCls(d)).length;
        const leadTxt = off ? `${lead} · ${keys.length - off} of ${keys.length} for this class` : lead;
        // A judge is marked INSIDE the row, because that is where the reader is: an
        // entry header saying "LLM judge" does not help someone scanning a list of
        // twenty-nine chips for the four a model opined on.
        //
        // TWO reinforcing marks: the `·judge` TEXT tag and a DOTTED border. Dash and
        // dot are different strokes and mean different things: a SPARSE DASH is the
        // absence family on every trust surface (na · pending · unknown) and stays
        // theirs — PROP-002 retired the dashed judge chip for exactly that collision.
        // A DOT is the kind channel. The text tag is the carrier
        // the retirement chose, and it is already what the Quality card and the runs
        // ledger use — one nomenclature, everywhere.
        return `<div class="qd-row-rel">${leadHtml(lead, leadTxt)}${kindCount(lead, keys.length)}${
            keys.map((d) => `<button type="button" class="qd-rel-chip${JUDGE_DIMS.has(d) ? ' qd-judge' : ''}${appliesToGuideCls(d) ? '' : ' qd-rel-offclass'}" data-dim="${esc(d)}"
                >${esc(label(d))}${JUDGE_DIMS.has(d)
                    ? `<span class="qd-rel-kind"> ·judge${decidesInPath(d) ? ' ·in-flight' : ''}</span>` : ''}${
                    appliesToGuideCls(d) ? '' : `<span class="qd-rel-kind"> ·not this class</span>`}${
                    ENTRY_EXCLUDED.has(d) ? `<span class="qd-rel-kind"> ·not on a turn</span>` : ''}${
                    // SYMMETRIC, wherever the relation is named. Restricting this to a
                    // measurement's `grades` row made the link one-way: Grounding Overlap
                    // said "same assessment as the Grounding gate", and the gate's own
                    // entry said nothing back — a dead end one hop later, which is the
                    // bidirectional rule this file already states for relatedChips.
                    //
                    // The objection it was restricted for ("it would badge only 3 of the
                    // gate's 7 chips") was the argument FOR it: which of the measurements
                    // grading a gate read the same assessment it acted on is exactly the
                    // fact nothing else carries. Corroboration versus coincidence.
                    same(d)
                        ? `<span class="qd-rel-same u-tip" role="img" aria-label="Same assessment"
                            data-mod="${esc(same(d))}"
                            data-tip="Same assessment — both read ${esc(same(d))}, one module, so these two can never disagree about what was found. Click the S to show the module.">S</span>`
                        : ''}</button>`).join('')}</div>`;
    }

    /** Both directions, so the reader can step in and step back.
     *
     *  forward — the dimensions that measure this mechanism
     *  reverse — the mechanisms this dimension measures
     *
     *  A one-way link is a dead end one hop later, which is the same complaint as a row
     *  with no link at all, just deferred.
     *
     *  A dimension with NEITHER row says so. Most of the 52 are owned by no single
     *  mechanism — they grade the run as a whole and reach the record through the
     *  scored verdict set (Evaluation, the union) — and silence there was ambiguous:
     *  a reader could not tell "nothing owns this" from "the links did not render"
     *  (the contract's Rule 3.2 — an absent slot must never be mistakable for a clean
     *  one). Scoped to native dimensions: a registered mechanism without links
     *  (memory_firewall) explains its own exclusion in prose, and repeating that here
     *  would be one slot restating another.
     */
    /** The two operating facts, when the registry serves them: WHEN it acts, and WHAT
     *  you can change. "The mode, and nothing else" is the most useful thing most
     *  mechanisms can say — it stops a reader inferring that the quality bands (which
     *  only colour a chip) must loosen a gate. */
    function limitsRows(k) {
        return factRows(k).fires + factRows(k).settings;
    }
    /** A fact row: a lead and a body in the same grid as the relation rows. */
    function factRow(lead, bodyHtml) {
        return bodyHtml ? `<div class="qd-row-rel qd-row-fact">${leadHtml(lead)}<div class="qd-fact">${bodyHtml}</div></div>` : '';
    }
    /** The operating facts as ROWS, from the registry's structured limits:
     *    fires     `fires when` (the triggers, any one of) · `then` · `also` · `ignores`,
     *              or a `none` row when the point does not fire
     *    settings  `you can change` (setting → effect) · `fixed` (what cannot be tuned, and why)
     *    why       the design's reasoning, folded
     *  An older payload that serves prose strings renders them as one row each — the
     *  facts are never dropped, only the structure. */
    function factRows(k) {
        const f = ENTRY_FIRES[k], t = ENTRY_TUNABLE[k], x = ENTRY_FIXED[k], w = ENTRY_WHY[k];
        let fires = '';
        if (typeof f === 'string') fires = factRow('fires when', esc(f));
        else if (f && typeof f === 'object') {
            if (Array.isArray(f.when) && f.when.length) {
                fires += factRow('fires when', `<span class="qd-fact-pre">any one of</span><ol class="qd-trig">${
                    f.when.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`);
            } else if (f.none) {
                fires += factRow('fires when', `<span class="qd-fact-never">never</span> ${esc(f.none)}`);
            }
            for (const key of ['then', 'also', 'ignores']) if (f[key]) fires += factRow(key, esc(f[key]));
        }
        const dl = (pairs) => `<dl class="qd-set">${pairs.map(([s, e]) => `<dt><code>${esc(s)}</code></dt><dd>${esc(e)}</dd>`).join('')}</dl>`;
        let settings = '';
        if (typeof t === 'string') settings = factRow('you can change', esc(t));
        else if (Array.isArray(t) && t.length) settings = factRow('you can change', dl(t));
        if (Array.isArray(x) && x.length) settings += factRow('fixed', dl(x));
        const why = w ? factRow('why', `<details class="qd-why"><summary>the reasoning behind these rules</summary><p>${esc(w)}</p></details>`) : '';
        return { fires, settings, why };
    }
    /** How a mechanism decides, counts or reads — the registry's derivation as a row. */
    function howRow(k) {
        const steps = ENTRY_HOW[k], d = ENTRY_DERIVATION[k];
        if (!(steps && steps.length) && !d) return '';
        const c = ENTRY_CLASS[k];
        const lead = c === 'control' ? 'how it decides' : 'how it reads';
        // Steps, one line each, in the order they happen; a single step is a line.
        if (steps && steps.length > 1) {
            return factRow(lead, `<ol class="qd-steps">${steps.map((x) => `<li>${esc(x)}</li>`).join('')}</ol>`);
        }
        return factRow(lead, esc((steps && steps[0]) || d));
    }
    /** A station's three reading blocks — phrases, not sentences:
     *    for one answer   (sub-label: the Live Status dial)  reading · scale
     *    for an agent     (sub-label: the Trust Circle)      configuration · execution ·
     *                                                        several agents · open finding
     *    its checks       where they count
     *  Each block is a lead with the surface beneath it and a small label → phrase table.
     *  The member kinds and counts are on the rows beneath, so nothing is restated here.
     *  An older payload (three strings) renders as three plain rows. */
    function readsRows(k) {
        const r = ENTRY_READS[k];
        if (!r) return '';
        const blockRow = (lead, blk) => {
            if (!blk) return '';
            if (typeof blk === 'string') return factRow(lead, esc(blk));
            const rows = Array.isArray(blk.rows) ? blk.rows : [];
            // The colour rule carries the member count — the same "members · N" as its group.
            const nMem = (CONTROLS[k] || []).length;
            const nJ = (k === 'station_' + 'scored') ? judgedMembers().inScope.length : 0;
            const dd = (v) => (v === MEMBERS_RULE && nMem) ? `${esc(v)} \u00b7 ${nMem}`
                : (v === JUDGED_RULE && nJ) ? `${esc(v)} \u00b7 ${nJ}` : esc(v);
            const table = `<dl class="qd-sub">${rows.map(([l, v]) =>
                `<dt>${esc(l)}</dt><dd>${dd(v)}</dd>`).join('')}</dl>`;
            return `<div class="qd-row-rel qd-row-fact">${leadHtml(lead)}${
                blk.where ? `<span class="qd-lead-sub">${esc(blk.where)}</span>` : ''}<div class="qd-fact">${table}</div></div>`;
        };
        return blockRow('for one answer', r.answer || r.turn) + blockRow('for an agent', r.agent || r.ring)
             + factRow('its measures', esc(r.checks || ''));
    }

    // ── a PROPERTY's lanes — which module assesses it, and what it may do ──────
    // This is what a property entry is FOR. A reader arriving from a mechanism row is
    // asking "which module produced that finding, and can it act?" — and after a remap
    // this shows the NEW module, because it renders the live binding rather than a
    // description of one.
    function lanesRow(k) {
        const lanes = LANES[k];
        if (!Array.isArray(lanes) || !lanes.length) return '';
        // ONE in-flight row and one post-hoc row. The unconditional and triggered lanes
        // are ALTERNATIVES, so listing the unused one as "nothing bound" would read as a
        // gap that is not one.
        const WHEN = { IN_FLIGHT: 'During the turn', POST_HOC: 'After the turn' };
        // `decides` can move the answer; `escalates` can only ask a judge; `grades`
        // changes nothing. Three different powers, and a reader must not have to infer
        // which one a module has.
        const MODE = { decides: 'can act on the answer',
                       escalates: 'can only escalate to a judge',
                       grades: 'reports only' };
        const rows = lanes.map((l) => {
            const bound = !!l.assessor;
            return `<div class="qd-lane${bound ? '' : ' qd-lane-empty'}">`
                 + `<span class="qd-lane-when">${esc(WHEN[l.lane] || l.lane)}</span>`
                 + `<span class="qd-lane-mod">${bound
                        // Already at the module's home, so no arrow: it would point at
                        // this very entry, and a self-route is a dead end wearing a link's
                        // clothes. The tag still says WHAT the name is.
                        ? moduleName(l.assessor, l.label, null)
                        : '<em>' + esc(l.note || '') + '</em>'}</span>`
                 + (l.mode ? `<span class="qd-lane-mode">${esc(MODE[l.mode] || l.mode)}</span>` : '')
                 + `</div>`;
        }).join('');
        const sym = SYMMETRY[k];
        // The symmetry state in words, not a wire token. `shared` is the good outcome and
        // must not read like jargon; `gap` must not read like a design.
        const SYM = {
            shared: 'One module serves both lanes, so the two can never disagree about '
                  + 'what was found.',
            split: 'Two different modules serve the two lanes — they can disagree.',
            gap: 'One lane is bound and the other is not yet.',
            by_design_score_only: 'Assessed only after the turn, by design.',
            by_design_act_only: 'Assessed only during the turn, by design.',
            unbound: 'No module is bound to this property.',
        };
        // The whole-property sentence is SUPPRESSED where a lane carries a by-design
        // note: "no module is bound to this property" beside "there is no ground truth to
        // grade a refusal against" reads as though both absences are gaps, when one is a
        // design. The lane notes already say more, and no slot may restate another
        // (Rule 3.0).
        const hasDesignNote = lanes.some((l) => !l.assessor && l.note
            && !/nothing is bound/.test(l.note));
        return `<div class="qd-rel">${leadHtml('assessed by', 'assessed by', 'qd-rel-k')}`
             + `<div class="qd-lanes">${rows}</div></div>`
             + (SYM[sym] && !hasDesignNote
                 ? `<div class="qd-row-note">${esc(SYM[sym])}</div>` : '');
    }

    /** The relation rows in ONE string — every lead, both directions. The row renderer
     *  uses `relationParts` to shelve the same rows by WHEN they speak; this flat form is
     *  the test seam and the fallback when an entry has only one group. */
    function relatedChips(k) {
        const p = relationParts(k);
        return p.turn + p.after + p.note;
    }
    /** The relation rows, split by WHEN the related thing speaks:
     *    turn  — what holds, decides or consults DURING the turn (the members a guarantee is
     *            upheld / bounded / reported / attested by; what a mechanism upholds; the
     *            judges it escalates to; the mechanism a judge decides for)
     *    after — what only GRADES, after the answer (graded by · counts · grades)
     *    note  — the honest "nothing here" sentence, when there are no rows at all
     *  Same leads, same chips, same order within each group as before; the grouping is the
     *  only thing added, so a reader sees the time cut without decoding the lead words. */
    //: A GATE (a mechanism: control · boundary · attestation · measure-class mechanism),
    //: a scored MEASURE (no entry class of its own), and the entries sharing a module with k.
    const MECH_CLASSES = new Set(['control', 'boundary', 'attestation', 'measure']);
    const isMechanism = (k) => MECH_CLASSES.has(ENTRY_CLASS[k]);
    const isMeasureDim = (k) => !ENTRY_CLASS[k];
    function moduleSharers(k) {
        const out = [];
        for (const ks of Object.values(SHARED_MODULES)) {
            if (ks.indexOf(k) < 0) continue;
            for (const o of ks) {
                if (o === k || out.indexOf(o) >= 0 || /^property_/.test(o)) continue;
                // from a gate: the measures; from a measure: the gates
                if (isMechanism(k) ? isMeasureDim(o) : isMechanism(o)) out.push(o);
            }
        }
        return out;
    }

    function relationParts(k) {
        // TWO relations, not one — and conflating them lost the causal direction. A
        // mechanism's dimension list held both the judges it ESCALATES TO in-path
        // (decisive: `groundedness` is what returned 0✓/3✗ and withheld the answer) and
        // the scorers that GRADE it afterwards (report-only). Under a single "measured
        // by" heading they read as one kind of thing, and an operator concluded the
        // measurements open and close the gates. `judges` is declared in the registry
        // (`POINT_JUDGES`) and served per mechanism; absent ⇒ nothing is decisive here,
        // which is true of every mechanism except the grounding gate.
        const judges = (ENTRY_JUDGES[k] || []);
        // A GATE's "graded by" names only the measures with a REAL link to it: the ones that
        // use its module (the S), read from the module data — never the registry's filing
        // list, which attaches every measure ABOUT a guarantee to some gate on it, so the
        // Grounding gate looked responsible for Numeric Fidelity, Citations Present and
        // others it never touches (operator, 2026-10-30). Those stay where they belong: on
        // their guarantee ("its measures") and as JUDGED's members. Display only — every
        // reading that needs a measure's guarantee still reads the filing list.
        const graded = (isMechanism(k) ? moduleSharers(k) : (RELATED[k] || []))
            .filter((d) => judges.indexOf(d) < 0);
        // LABELS. "measured by" was the ambiguous one — over a list that mixed decisive
        // judges with post-hoc scorers it read as an INPUT list, and an operator
        // concluded the measurements open and close the gates. "graded by" carries the
        // after-the-fact role a grade always implies: a grade is awarded ON work already
        // done and never becomes part of it. With `escalates to` beside it the decisive
        // half has its own name, so the pair is unambiguous.
        //
        // An active-voice variant ("checks on it" / "checks") was tried to dodge the
        // passive entirely and REVERTED — the passive was never the problem, the
        // conflated list was.
        // The REVERSE direction carries the same distinction, or the pair contradicts
        // itself: `groundedness` read "grades: Grounding gate" while the gate read
        // "escalates to: Groundedness" — one relation described two ways, and the
        // decisive half was the one that got demoted.
        const back = USED_BY[k] || [];
        const decidesFor = back.filter((m) => (ENTRY_JUDGES[m] || []).indexOf(k) >= 0);
        // The same rule from the measure's side, so the link is never one-way: a measure
        // "grades" a GATE only where it uses that gate's module (wherever the filing list
        // put it); its guarantee and its property stay.
        const gradesFor = isMeasureDim(k)
            ? [...new Set([...back.filter((m) => !isMechanism(m)), ...moduleSharers(k).filter(isMechanism)])]
                .filter((m) => decidesFor.indexOf(m) < 0)
            : back.filter((m) => decidesFor.indexOf(m) < 0);
        // A GUARANTEE is upheld by controls and graded by measurements — two rows, two
        // roles. The station entry used to list its mechanisms under "graded by", which
        // said a control grades a guarantee (it does not: it upholds it) and left the
        // station's real measurements off its own entry entirely. Both are shown now,
        // and both link, so a reader can step from a guarantee into either population.
        // `counts` is what a mechanism PRODUCES, not what grades it — the only mechanism
        // that COUNTS anything is `evaluation`, and calling its output "graded by" would say
        // the checks assess the layer that computes them.
        // A station's mechanisms are NOT all controls, so one verb cannot serve them
        // all. Five of the eight have none: JUDGED's two are `measure`, SIGNED's and
        // PROVEN's are `attestation`, BOUNDED mixes `boundary` with `control`. Saying
        // "Evaluation upholds JUDGED" inverts the direction exactly as "graded by"
        // would have — a measure reports on a guarantee, it does not hold it up.
        //
        // Derived from the registry's own `POINT_CLASS` (already registered per entry),
        // so a new mechanism class gets its verb without touching this renderer.
        const MECH_VERB = Object.fromEntries(
            Object.entries(MEMBER_VERBS).map(([c, [onGuarantee]]) => [c, onGuarantee]));
        const mechs = CONTROLS[k] || [];
        const byVerb = new Map();
        for (const m of mechs) {
            const verb = MECH_VERB[ENTRY_CLASS[m]] || 'upheld by';
            if (!byVerb.has(verb)) byVerb.set(verb, []);
            byVerb.get(verb).push(m);
        }
        const byVerbHtml = {};
        byVerb.forEach((list, verb) => { byVerbHtml[verb] = chipRow(verb, list); });
        const mechRows = [...byVerb.entries()]
            .sort((a, b) => Object.values(MECH_VERB).indexOf(a[0])
                          - Object.values(MECH_VERB).indexOf(b[0]))
            .map(([verb]) => byVerbHtml[verb]).join('');

        // IN THE TURN: the members that hold a guarantee, what a mechanism upholds, the
        // judges it consults, the mechanism a judge decides for.
        // Each row is also exposed on its own (`rows`), so the entry renderer can place
        // them in the order the gate WORKS rather than the order the relations were added.
        const rows = {
            mech: mechRows,
            byVerb: byVerbHtml,
            // The SAME relation read from the member's side, in the member's own verb — a
            // measure is evidence for its guarantee; only a control upholds one.
            upholds: chipRow(memberVerb(k), UPHOLDS[k] || [], k),
            escalates: chipRow('escalates to', judges, k),
            decides: chipRow('decides for', decidesFor, k),
            // The checks a window aggregate weights, and — from the check's side — the
            // aggregate it weighs on. One relation, named at both ends.
            weights: chipRow('weights', ENTRY_WEIGHTS[k] || [], k),
            weighsOn: chipRow('weighs on', WEIGHS_ON[k] || [], k),
            graded: chipRow('graded by', graded, k),
            counts: chipRow('counts', COUNTS[k] || [], k),
            grades: chipRow('grades', gradesFor, k),
            countedAt: chipRow('counted at', COUNTED_AT[k] || [], k),
            assessedFor: chipRow('assessed for', ASSESSED_FOR[k] || [], k),
        };
        const turn = rows.assessedFor + rows.mech + rows.upholds + rows.weights + rows.escalates + rows.decides;
        // AFTER THE TURN: what grades, what is counted, what this check grades or weighs on.
        const after = rows.graded + rows.counts + rows.grades + rows.weighsOn + rows.countedAt;
        if (turn || after) return { turn, after, note: '', rows };
        return { turn: '', after: '', note: (function noRelationNote() {
        // A registered mechanism with no relations used to render NOTHING, so a reader
        // could not tell "nothing measures this" from "the links did not render" — the
        // ambiguity Rule 3.2 bans, and which the unregistered branch below already
        // handles with a note. Silence is only honest when it is spoken.
        if (ENTRY_CLASS[k]) {
            // One sentence used to serve all six ungraded mechanisms: "it is a yes/no
            // control". It was wrong for an attestation, a boundary and a MEASUREMENT
            // mislabelled as a mechanism — and it presented every absence as correct,
            // concealing three controls that run and are never graded at all.
            //
            // The reading is now SERVED (assurance_points.grading_state): `by_design`
            // means nothing should grade this, `gap` means nothing does yet. Those are
            // different answers, and a reader who cannot tell them apart cannot tell a
            // finished design from an unfinished one.
            const g = GRADING[k] || {};
            if (g.state === 'by_design') {
                return '<div class="qd-row-note"><b>Nothing grades this, and nothing '
                     + 'should.</b> ' + esc(g.reason || '') + '</div>';
            }
            if (g.state === 'gap') {
                return '<div class="qd-row-note qd-row-gap"><b>Nothing grades this yet.</b> '
                     + (g.reason ? esc(g.reason)
                                 : 'No reason is recorded for the absence, which is itself '
                                 + 'worth knowing.') + '</div>';
            }
            // A PROPERTY whose lane already carries a by-design note needs no second
            // sentence: the lanes row directly beneath states WHY nothing grades it, and
            // repeating it in mechanism language ("no scored dimension measures this one")
            // restates another slot (Rule 3.0) in the wrong vocabulary — a property is not
            // a mechanism, and the reader cannot tell a finished design from a gap.
            if (ENTRY_CLASS[k] === 'property') {
                const ls = LANES[k] || [];
                if (ls.some((l) => l && l.note)) return '';
                return '<div class="qd-row-note qd-row-gap"><b>Nothing grades this yet.</b> '
                     + 'It is assessed during the turn, but no scored dimension measures '
                     + 'it afterwards.</div>';
            }
            return '<div class="qd-row-note">No scored dimension measures this one.</div>';
        }
        return '<div class="qd-row-note">No in-path mechanism consults this one — it is '
             + 'taken after the answer, counted at JUDGED like every measurement, and '
             + 'folds into the TRUSTED standing.</div>';
        })() };
    }

    //: Fetched once. The Guide must show the same vocabulary wherever it is opened from
    //: — a reader who arrives from the Quality card and one who arrives from the dial are
    //: asking the same question.
    //:
    //: The latch memoises the IN-FLIGHT PROMISE, not a boolean, and it only survives a
    //: run that actually registered entries. A boolean set at call entry made this a
    //: race with two failure modes, both silent: a second caller got `Promise.resolve()`
    //: while the fetch was still open and read an EMPTY vocabulary, and a failed fetch
    //: (401, offline) latched that emptiness for the rest of the page session because
    //: the `catch` below resolves. Every consumer then degraded — the Quality card put
    //: all 20 checks in the JUDGED residue, attributing GROUNDED's failures to the wrong
    //: station. A latch must record that the work SUCCEEDED, never that it was attempted.
    let _vocabPromise = null;
    /** Did the vocabulary actually land? The station partition is served per mechanism,
     *  so an empty `ENTRY_STATION` means every `stationOf` answer is the 'scored' default
     *  — indistinguishable from a real answer at the call site. Consumers that group by
     *  station ask this before treating a grouping as authoritative. */
    function stationsReady() { return Object.keys(ENTRY_STATION).length > 0; }
    function ensureVocabulary() {
        if (_vocabPromise) return _vocabPromise;
        try {
            const token = localStorage.getItem('tda_auth_token');
            _vocabPromise = fetch('/api/v1/assurance/vocabulary',
                { headers: token ? { Authorization: `Bearer ${token}` } : {} })
                .then((r) => (r.ok ? r.json() : null))
                .then((d) => {
                    if (d && d.entries) registerEntries(d.entries);
                    if (d && d.shared_modules) SHARED_MODULES = d.shared_modules;
                    if (d && d.stages) registerStages(d.stages);
                    if (d && Array.isArray(d.terms)) registerTerms(d.terms);
                })
                .catch(() => { /* the Guide still works with the registered dimensions */ })
                .then(() => {
                    // Retryable unless it worked: a later caller (post-login, network
                    // back) gets a real attempt rather than this run's empty result.
                    //
                    // Keyed on the ENTRIES landing, not on both models. Both ride the
                    // same response, so a payload with entries but no stage block means
                    // the server does not serve stages — retrying cannot change that,
                    // and requiring both here would refetch on every call forever. The
                    // stage model then simply stays empty and every grouping surface
                    // degrades to a flat list, which is its designed absence behaviour.
                    if (!stationsReady()) _vocabPromise = null;
                });
            return _vocabPromise;
        } catch (_) { _vocabPromise = null; return Promise.resolve(); }
    }

    /** Register mechanism entries. Called from TWO producers with DIFFERENT completeness:
     *  the assurance dial registers the current turn's points (`guide_entry` — dims, no
     *  station) on every render, and the vocabulary endpoint serves the full registry
     *  (dims AND station) when the Guide first needs it.
     *
     *  So registration is ADDITIVE and order-independent. It used to bail on the whole
     *  entry when the key was already known — "a real dimension always wins", which is
     *  right for the DEFINITION and silently became "whoever registered first wins" for
     *  every other fact. The dial always renders before the Guide opens, so a mechanism
     *  present on the turn was registered station-less and the richer vocabulary entry
     *  behind it was DISCARDED: `authority` kept its dims (its measures rendered) and
     *  lost its station, so `tool_subset`/`must_not_call` fell through to the JUDGED
     *  residue while `tool_enforcement` — absent from that turn, so never pre-registered
     *  — read BOUNDED correctly. One partition, two answers, decided by load order.
     *
     *  The rule now: the first DEFINITION wins (a real dimension is never overwritten by
     *  a mechanism blurb), and every other field is filled in when missing. A later,
     *  richer payload can complete an entry; it can never downgrade one. */
    /** Receive the measurement process model. Assignment, not merge: the server holds
     *  the ONE declaration (`eval/stages.py`), so a later payload replaces an earlier one
     *  wholesale rather than accumulating — a stage removed upstream must disappear here,
     *  which a merge would silently prevent.
     *
     *  Absence-tolerant in the honest direction: a payload with no stage block leaves the
     *  model EMPTY, so `stagesReady()` stays false and every grouping surface falls back
     *  to an ungrouped list. Inventing a grouping from a partial payload is the failure
     *  this shape exists to avoid. */
    function registerStages(m) {
        if (!m || !Array.isArray(m.order) || !m.order.length) return;
        STAGE_ORDER = m.order.slice();
        Object.keys(STAGE_OF).forEach((k) => delete STAGE_OF[k]);
        Object.assign(STAGE_OF, m.dimensions || {});
        Object.assign(STAGE_LABEL, m.labels || {});
        Object.assign(STAGE_DESC, m.descriptions || {});
    }

    function registerEntries(entries) {
        if (!Array.isArray(entries)) return;
        entries.forEach((e) => {
            if (!e || !e.k) return;
            // DIM entries are OBJECTS ({label, desc}), not strings. Assigning a string
            // silently broke BOTH lookups: `label()` fell back to title-case and
            // `desc()` returned '', so the tooltip read "No description available" for a
            // mechanism that has a perfectly good one.
            if (!DIM[e.k]) {
                DIM[e.k] = { label: e.label || titleCase(e.k), desc: e.desc || e.long || '' };
            }
            if (e.long && !LONG[e.k]) LONG[e.k] = e.long;
            if (Array.isArray(e.dims) && e.dims.length) {
                if (!RELATED[e.k]) RELATED[e.k] = e.dims.slice();
                e.dims.forEach((d) => {
                    (USED_BY[d] = USED_BY[d] || []).indexOf(e.k) < 0 && USED_BY[d].push(e.k);
                });
            }
            // The station's OTHER population: the mechanisms that uphold it. Additive
            // like every other field here — a richer payload completes an entry, a
            // poorer one never downgrades it.
            if (Array.isArray(e.counts) && e.counts.length && !COUNTS[e.k]) {
                COUNTS[e.k] = e.counts.slice();
                e.counts.forEach((m) => {
                    (COUNTED_AT[m] = COUNTED_AT[m] || []).indexOf(e.k) < 0 && COUNTED_AT[m].push(e.k);
                });
            }
            if (Array.isArray(e.properties) && e.properties.length && !PROPERTIES_OF[e.k]) {
                PROPERTIES_OF[e.k] = e.properties.slice();
                e.properties.forEach((p) => {
                    (ASSESSED_FOR[p] = ASSESSED_FOR[p] || []).indexOf(e.k) < 0 && ASSESSED_FOR[p].push(e.k);
                });
            }
            if (e.excluded) ENTRY_EXCLUDED.add(e.k);
            if (Array.isArray(e.controls) && e.controls.length) {
                if (!CONTROLS[e.k]) CONTROLS[e.k] = e.controls.slice();
                e.controls.forEach((m) => {
                    (UPHOLDS[m] = UPHOLDS[m] || []).indexOf(e.k) < 0 && UPHOLDS[m].push(e.k);
                });
            }
            if (e.cls && !ENTRY_CLASS[e.k]) ENTRY_CLASS[e.k] = e.cls;
            // The NAME a reader would use. A property's entry key carries a
            // `property_` prefix — a namespacing device for this registry, not a
            // name anyone types or reads; rendering it put an implementation
            // detail where every other class shows its real identifier.
            if (e.property && !ENTRY_NAME[e.k]) ENTRY_NAME[e.k] = e.property;
            (e.lanes || []).forEach((l) => {
                if (l && l.assessor && l.label && !MODULE_LABEL[l.assessor]) {
                    MODULE_LABEL[l.assessor] = l.label;
                }
                // What the module DOES, and where it came from — the same two slots for a
                // platform module and a pack one, so the tooltip explains a module rather
                // than repeating its name.
                if (l && l.assessor && l.assessor_desc && !MODULE_DESC[l.assessor]) {
                    MODULE_DESC[l.assessor] = l.assessor_desc;
                }
                if (l && l.assessor && l.assessor_origin && !MODULE_ORIGIN[l.assessor]) {
                    MODULE_ORIGIN[l.assessor] = l.assessor_origin;
                }
            });
            // Additive, like every other field: a richer payload completes an entry, a
            // poorer one never downgrades it (the first-writer-wins defect).
            if (e.grading && !GRADING[e.k]) GRADING[e.k] = e.grading;
            if (e.lanes && !LANES[e.k]) LANES[e.k] = e.lanes;
            if (Array.isArray(e.consults) && !ENTRY_CONSULTS[e.k]) ENTRY_CONSULTS[e.k] = e.consults.slice();
            if (e.symmetry && !SYMMETRY[e.k]) SYMMETRY[e.k] = e.symmetry;
            if (e.measured && !ENTRY_MEASURED[e.k]) ENTRY_MEASURED[e.k] = e.measured;
            if (e.fires && !ENTRY_FIRES[e.k]) ENTRY_FIRES[e.k] = e.fires;
            // An EMPTY fates list is a declaration ("reports only"), so it is stored
            // whenever the key is present — only a missing key leaves the slot unknown.
            if (Array.isArray(e.fates) && !ENTRY_FATES[e.k]) ENTRY_FATES[e.k] = e.fates.slice();
            if (e.q && !ENTRY_Q[e.k]) ENTRY_Q[e.k] = e.q;
            if (e.derivation && !ENTRY_DERIVATION[e.k]) ENTRY_DERIVATION[e.k] = e.derivation;
            if (Array.isArray(e.how) && e.how.length && !ENTRY_HOW[e.k]) ENTRY_HOW[e.k] = e.how.slice();
            if (Array.isArray(e.fixed) && e.fixed.length && !ENTRY_FIXED[e.k]) ENTRY_FIXED[e.k] = e.fixed.slice();
            if (e.why && !ENTRY_WHY[e.k]) ENTRY_WHY[e.k] = e.why;
            if (e.reads && typeof e.reads === 'object' && !ENTRY_READS[e.k]) ENTRY_READS[e.k] = e.reads;
            if (e.tunable && !ENTRY_TUNABLE[e.k]) ENTRY_TUNABLE[e.k] = e.tunable;
            // The station + applicability the vocabulary serves per mechanism — what
            // drives the station chip and the engine dots/facet for registered entries.
            if (e.station && !ENTRY_STATION[e.k]) ENTRY_STATION[e.k] = e.station;
            if (Array.isArray(e.judges) && e.judges.length && !ENTRY_JUDGES[e.k]) {
                ENTRY_JUDGES[e.k] = e.judges.slice();
            }
            if (Array.isArray(e.weights) && e.weights.length && !ENTRY_WEIGHTS[e.k]) {
                ENTRY_WEIGHTS[e.k] = e.weights.slice();
                e.weights.forEach((d) => {
                    if (!WEIGHS_ON[d]) WEIGHS_ON[d] = [];
                    if (WEIGHS_ON[d].indexOf(e.k) < 0) WEIGHS_ON[d].push(e.k);
                });
            }
            if (Array.isArray(e.applies_to) && e.applies_to.length && !ENTRY_APPLIES[e.k]) {
                ENTRY_APPLIES[e.k] = e.applies_to.slice();
            }
        });
    }

    window.QualityDimensions = {
        DIM, LONG, JUDGE_DIMS, BAND, THRESHOLD_SPEC,
        label, desc, long, titleCase, esc, appliesToClass,
        // The measurement process model (served; see STAGE_OF).
        stageOf, stagesReady, groupByStage, unstaged,
        stageOrder: () => STAGE_ORDER.slice(),
        stageLabel: (s) => STAGE_LABEL[s] || s,
        stageDesc: (s) => STAGE_DESC[s] || '',
        fmtThreshold, fmtBand, ensureThresholds, invalidateThresholds,
        openGuide, closeGuide, guideButton, contextAttrs, setContext, registerEntries,
        // Exported so the gate can drive the receiving seam directly — a grouping model
        // tested only through a live fetch is a grouping model that is not tested.
        registerStages, registerTerms,
        // Which scored dimensions measure an entry — so a card can scope the Guide to the
        // checks behind ITS guarantee rather than opening the whole catalogue.
        relatedOf: (k) => (RELATED[k] || []).slice(),
        controlsOf: (k) => (CONTROLS[k] || []).slice(),
        countsOf: (k) => (COUNTS[k] || []).slice(),
        upholdsOf: (k) => (UPHOLDS[k] || []).slice(),
        // Every parent of an entry, read backwards from the parents' child lists.
        parentsOf,
        // The partition + engine relevance, for any surface that needs them: the ONE
        // the guarantee an entry bears on (none → 'scored', station entries →
        // null) and the IFOC engines it applies to (normalized, canonical order).
        // `stationOfThreshold` is the admin Trust page's grouping seam (sliders group
        // by the partition, never by a hand-copied list); `ensureVocabulary` lets a
        // consumer await the mechanism links the station derivation needs, and
        // `stationsReady` lets it tell a real answer from the 'scored' default —
        // `stationOf` alone cannot, which is how an empty vocabulary rendered as a
        // confident (and wrong) all-JUDGED card. ONE latch, owned here: a consumer
        // that keeps its own "did I load it yet" boolean re-creates that defect.
        stationOf, stationsReady, enginesFor, dimOfThreshold, stageOfThreshold,
        ensureVocabulary,
        // The cross-link renderer, exported as a TEST SEAM. Its gates asserted the
        // bidirectional-link property by grepping for the exact `chipRow('measured by',
        // …)` source line — pinning an implementation, not a behaviour — so renaming an
        // ambiguous label broke two assertions while the property they name held
        // perfectly. Exported so they can assert what they mean.
        relatedChips,
    };
})();
