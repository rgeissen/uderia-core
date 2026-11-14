# Answer Gates — Architecture

**Between the model's draft and the user stand four deterministic gates.** The grounding
gate checks the answer against the data and the knowledge the turn actually had. The honesty
gate checks that what the answer claims about its own evidence is true. The guardrails screen
what goes in and what comes out. A failed turn says so in one honest sentence. Each gate runs
on every engine through one shared runtime. Each costs no tokens (one opt-in judge aside),
fails open, and leaves a stamp on the turn that the Assurance dial reads.

This document is the reference for the CURRENT code in `agent/grounding/`, `agent/honesty/`,
`agent/guardrails/`, `agent/leaked_sql.py` and `agent/turn_failure.py`. Background and design
history: `TIER_1.1_GROUNDING_GATE.md`,
`TIER_1.3_GUARDRAILS.md`,
`RAG_TRUST_HARDENING.md` and
[AGENT_OPS_EVAL_ARCHITECTURE.md §6a](AGENT_OPS_EVAL_ARCHITECTURE.md#6a-grounding-gate--synchronous-on-path-enforcement-tier-11).
Related contracts:

- SQL refused *before* it runs: [PREFLIGHT_SQL_ARCHITECTURE.md](PREFLIGHT_SQL_ARCHITECTURE.md).
- What an agent may do at all: [AGENT_AUTHORITY_ARCHITECTURE.md](AGENT_AUTHORITY_ARCHITECTURE.md).
- How the per-turn readings are tallied: [ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md).
- How every reading must speak: [TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md).

---

## 1. The gates at a glance

| Gate | Entry point | Can | Turn stamp | Live event | Station |
|---|---|---|---|---|---|
| **Inbound guard** | `guardrails.runtime.scan_inbound` | block the request before any LLM call | `guardrail_inbound` | `guardrail` | GUARDED |
| **Grounding gate** | `grounding.runtime.run_grounding` | annotate · withhold (judge-confirmed) | `grounding` | `grounding_gate` | GROUNDED |
| **Outbound guard** | `guardrails.runtime.scan_outbound` | redact PII · defang URLs · withhold toxic text | `guardrail` | `guardrail` | GUARDED |
| **Honesty gate** (SQL · RAG) | `honesty.runtime.run_honesty_async` · `run_honesty_rag` | replace · append a disclosure | `honesty` | `honesty` | GROUNDED |
| **Citation check** | `honesty.runtime.run_honesty_citation` | append a disclosure | `honesty_citation` | `honesty` (step "Citation Check") | GROUNDED (folded into Honesty) |
| **Turn failure** | `turn_failure.answer` | replace the raw error with one sentence | the turn's `status`/`error` | `error` | JUDGED (`clean_termination`) |

Two neighbours sit on the same seam but are **not gates**:

- **Leaked-SQL recovery** (`leaked_sql.py`, ReAct only). It turns a SELECT the model wrote
  instead of running into a real, guarded tool call (§7).
- **The Outcome Loop** (`outcome_loop.py`, S7). It gives a fixable grounding finding one
  bounded revision before delivery (§4.5).

The executor engines reach every gate through one wrapper each:
`_run_guardrail_inbound`, `_run_grounding_gate_full`, `_run_guardrail_outbound`,
`_run_honesty_gate`, `_run_honesty_gate_rag` and `_run_citation_gate`, all on `PlanExecutor`.
The Coordinate engine has no `PlanExecutor`, so it calls the runtimes directly with the same
contract. Every wrapper resolves the profile through `_resolve_gate_profile`. If that lookup
fails, the gate degrades to observe/off: the failure is logged and counted, never silent.

## 2. The order per engine

Every gate runs **before** the answer is saved to the histories and before `final_answer` is
yielded. An answer cannot be withheld after it has streamed. When the Governed Stream is on,
`final_answer` replaces whatever streamed (§6.4). The text after each gate is the next gate's
input, so the stored `chat_object`, the HTML and the stamp all describe one delivered answer.

| Engine | Order at the end-of-turn seam |
|---|---|
| **Optimize** (`executor._format_and_yield_final_answer`) | grounding (+ outcome loop) → outbound → OBDA scan → honesty SQL → citation → number lineage → budget finish line → model gap |
| **Ideate** (`ideate_engine`) | grounding (+ outcome loop) → outbound → honesty RAG → sensitivity note → model gap |
| **Ideate + tools / ReAct** (`conversation_engine`) | *inside the agent:* leaked component-call recovery → **leaked-SQL recovery** → auto-canvas; *at the seam:* grounding (+ outcome loop) → outbound → OBDA scan → honesty SQL → citation → HTML rebuilt once (`_apply_gate_verdicts_to_html`) → number lineage → budget finish line → model gap → data visuals dropped under a contained honesty verdict (`honesty.adapter.suppress_components`) |
| **Focus** (`focus_engine`) | grounding over the retrieved chunks (+ outcome loop) → outbound → honesty RAG → citation → sensitivity note → model gap. The **no-results** early return still runs the outbound guard, the assurance stamps and the provenance seal (trust campaign C19). |
| **Coordinate** (`coordinate_engine`) | inbound (own call, `profile_class="genie"`) … coordination … grounding (+ outcome loop) → outbound → citation → model gap. No SQL or RAG honesty runs on the coordinator: each expert runs its own gates on its own turn. |

The **inbound guard** runs once per turn, at the start of `PlanExecutor.run()` for the four
executor engines. It runs before context assembly and before any LLM call, so a blocked request
costs nothing. Coordinate runs its own scan at the same moment.

## 3. Modes and where they are set

All gates share ONE ladder, `core/posture_modes.LADDER`: `off < observe < annotate < enforce`.
They also share the runtime composite **`review`**, which means enforce *with retention*: the
gate acts exactly as `enforce` and also parks the original for a human. No gate keeps its own
copy of the ladder. A local copy once let `review` degrade silently to `observe`.

- **`observe`** — the gate measures and stamps. The answer is byte-identical. This is the
  default everywhere.
- **`annotate`** — the gate keeps the answer and adds a disclosure (a note, inline marks).
- **`enforce`** — the gate acts: it withholds, redacts, defangs or replaces.
- **`review`** — enforce, plus the original parked in the Assurance Inbox (§9).

**Where the mode comes from (most specific wins):**

| Layer | Key / setting | Notes |
|---|---|---|
| Deployment master switch | `APP_CONFIG.GROUNDING_GATE_ENABLED`, `APP_CONFIG.GUARDRAILS_ENABLED` | Both default **off**. Set in Administration → App Config → Trust → *Enforcement*. Off means the gate never runs and stamps `ran: false`. The honesty gate has no master switch. |
| Agent posture | `postureConfig.mode` | Profile editor → Trust Configuration → *Agent posture*. Every gate without its own mode inherits it (`posture_modes.gate_raw_or_default`). |
| Per gate | `groundingConfig.mode`, `groundingConfig.corpus.mode`, `honestyConfig.mode`, `guardrailConfig.inbound.mode`, `guardrailConfig.outbound.mode` | The table `posture_modes.GATE_MODE_SOURCES`. The corpus family follows the grounding mode unless it is set itself. `effective_posture` reports each gate's mode and its source. |
| Per-gate switches | `guardrailConfig.inbound.injection`, `outbound.pii` / `toxicity` / `exfil`; `groundingConfig.minNumericFidelity` / `maxOutOfScopeDbs`; `honestyConfig.grainJudge`; `knowledgeConfig.sufficiencyThreshold` | Absent values inherit the admin thresholds (`config.resolved_thresholds`) or the defaults. |
| Outcome Loop | `APP_CONFIG.OUTCOME_LOOP` (env `TDA_OUTCOME_LOOP`, default off) + `outcomeConfig` | §4.5 |
| Retention default | `APP_CONFIG.REVIEW_RETAIN_ENFORCED_BLOCKS` (default `True`) | A full withholding under plain `enforce` is kept as `retained` (§9). |

The editor writes ladder values only. It offers *Observe* / *Enforce* radios, plus *Disclose
findings to the reader* (stores `annotate`) and *Keep withheld originals … for my review*
(stores `review`).

`postureConfig`, `groundingConfig`, `guardrailConfig` and `honestyConfig` are **signed** and
downgrade-guarded in `core/profile_config_contract.py`. A pack or an import that weakens a mode
or relaxes a threshold moves the agent's signature. `authorityConfig` is a separate,
unsigned key: see [AGENT_AUTHORITY_ARCHITECTURE.md](AGENT_AUTHORITY_ARCHITECTURE.md).

Readers never show the storage tokens. `posture_modes.POSTURE_LABELS` maps each mode to the
words a reader sees (`watch`, `watch + disclose`, `enforce`, `enforce + keep for review`).
`POSTURE_STATE` maps the mode to how it reads: protecting modes read pass, watching modes read
`watching`, never amber.

## 4. The grounding gate (`agent/grounding/`)

One gate, **two trigger families**, one shared decision ladder:

- **scope** — the answer or its tool calls reach outside the knowledge graph's declared
  deployable scope.
- **corpus** — the answer states specifics that the retrieved knowledge does not support.

A family runs only when it has something to check against: a declared scope for scope, retrieved
or attached text for corpus. A turn with neither abstains.

### 4.1 Inputs

`adapter.build_inputs` builds `GroundingInputs` from a turn-shaped dict through
`eval.types.RunRecord`, the same normalised view the post-hoc scorers read. The live gate and
the Scorecard therefore extract identically on every engine. Each engine passes its own
tool-call source in `trace_extra`:

- `execution_trace` for Optimize,
- `conversation_agent_events` for ReAct and Coordinate,
- `knowledge_retrieval_event` for Focus.

Engines that did not pass retrieved chunks get the `knowledge_context` module's cached chunks,
merged in by `_run_grounding_gate_full`.

- **Scope.** `adapter.ensure_trusted_scope` unions the per-query enrichment scope with the
  agent's bound-graph scope, so the gate is never blind on a query that triggered no enrichment.
  This includes Coordinate.
- **Vouched numbers.** The user's question, the user's earlier words in the session
  (`adapter.session_context_text`, the last 12 turns) and KG-declared codes are never
  "ungrounded" (`RunRecord.vouched_numbers`).
- **Attachments** (`attached_texts`). Document text the turn's attachments gave the model joins
  the support set as `attachment_evidence` (`RunRecord.support_chunk_texts`, the one definition
  both lanes share). Attachments **never make a turn groundable by themselves**: they are added
  only after `adapter.has_groundable_content` has passed.

### 4.2 Primitives (`primitives.py`, `corpus.py`)

These are pure functions shared with the eval scorers, so the gate and the Scorecard can never
disagree about what a number or a reference is.

- **`NUM_RE`** — a number token. A leading hyphen counts as a minus only when nothing
  alphanumeric precedes it. `5m-1h`, `10-100`, `2024-2025` and `ISO-8601` are ranges or
  compounds, not negative figures.
- **`answer_nums`** — THE answer-side number extraction. It is `norm_nums` after
  `strip_ordinals` (heading and list numbering) and `strip_budget_line` (the turn budget's finish
  line). The scope family, the corpus family and the numeric-fidelity scorer all read it.
  Support-side text keeps plain `norm_nums`, because a number there only vouches.
- **`answer_out_of_scope_refs`** — a recall-first allowlist trigger. It extracts every
  identifier that is structurally a data reference: `db.table`, a quoted or technical name next
  to *database / schema / table*. It flags any that is not in
  `scope ∪ scope_entities ∪ SYSTEM_DBS`. A token that occurs verbatim in the turn's own results
  is a value, not a source.
- **`corpus.assess`** — the corpus family. It checks four claim classes against the chunks plus
  the question:
  - **T1, numbers** (`answer_nums`).
  - **T2, verbatim quotes** of at least `QUOTE_MIN_CHARS` (15). Marks are paired in reading
    order, and a short pair is consumed, never skipped.
  - **T3, identifiers.** At least `IDENTIFIER_FLOOR` (3) distinct misses are needed.
  - **T4, acronym expansions.**

  `_strip_structure` first blanks headings, bolded list labels, table header rows and bolded
  table row labels, so an answer's own scaffolding is never a claim. Table cells stay.
- **Second chance** (C1). Up to `SECOND_CHANCE_CAP` (5) unsupported spans each get a targeted
  retrieval over the agent's bound knowledge collections (`runtime._build_second_chance`).
  A span the corpus supports is cleared before anything is flagged.

### 4.3 Decisions

`GroundingGate.evaluate` is pure: no I/O, no logging, no LLM.

| Mode | Scope trigger (out-of-scope call or answer reference) | Numeric fidelity below `min_numeric_fidelity` | Corpus trigger |
|---|---|---|---|
| observe | PASS (recorded) | PASS (recorded) | PASS (recorded) |
| annotate | ANNOTATE | ANNOTATE | ANNOTATE (spans marked inline) |
| enforce | `needs_judge` → **`scope_faithfulness`** judge | ANNOTATE (never a judge call: enforce ⊇ annotate) | `needs_judge` → **`groundedness`** judge |

The deterministic layer **never blocks**. In `enforce` a trigger escalates to the family's judge
(`runtime._enforce_with_judge`, bounded by `ONLINE_EVAL_TIMEOUT_SECONDS`, its cost attributed
through the shared judge-cost sink). `enforce.resolve_enforced_verdict` folds the ruling:

- *unfaithful* → **BLOCK**: the answer is withheld and the note, led by the judge's own reason,
  replaces it.
- *faithful* → **PASS**: an honest "not in scope" survives.
- *abstained, unavailable or timed out* → **ANNOTATE**: fail-safe, never withhold on
  uncertainty.

`judged` and `judge_passed` record the adjudication as facts.

Two more escalation sources join the corpus family. Both only ever escalate, never decide:

- **`context_use`**, read through the assurance registry. Off unless an admin set
  `min_context_utilization`.
- A bound third-party module routed to `grounding_gate` (PROP-003, `assurance.inflight.augment`).

### 4.4 Applying the verdict

- **Text.** `adapter.apply_to_answer` leaves the answer unchanged on PASS, appends the note as a
  blockquote on ANNOTATE, and makes the note the answer on BLOCK.
- **HTML.** `adapter.apply_to_html` places a `scope` note through the one Answer-Surface renderer.
  On ANNOTATE it also wraps the unsupported spans in `<mark class="claim-unsupported">`.
- **Withholding.** A BLOCK goes through ONE parking seam, `runtime._park_review`. It parks as
  `pending` in review mode, or as `retained` under plain enforce while
  `REVIEW_RETAIN_ENFORCED_BLOCKS` is on.

Every verdict leaves through `runtime._verdict_event`, which stamps `ran` and `acted` and
records `mode: review` for an armed review gate (`posture_modes.stamped_mode`). The three
non-verdict paths are stamped too, so each is distinguishable:

- off: `action: "off"`,
- nothing to ground: `action: "na"`,
- failed open: `action: "error"`.

### 4.5 The Outcome Loop (S7)

`outcome_loop.run_loop` wraps the grounding seam on every engine. It is the executor's
`_run_grounding_gate_full`, and Coordinate's `_genie_grade`. It runs only when two things hold:

- the deployment switch `OUTCOME_LOOP` is on and the agent's `outcomeConfig` enables it;
- a finding is revisable: `unsupported_claim`, `out_of_scope_ref` or `ungrounded_number`.

The turn then gets **one** bounded revision call with a targeted note. The same gate re-grades
the revision deterministically (`skip_judge=True`, never parked). A revision that clears is
delivered clean, with `outcome_delivered: "revision"` and the HTML re-rendered from it. One that
does not clear delivers the honest annotated first draft. Gate: `test_outcome_loop.py`.

## 5. The honesty gate (`agent/honesty/`)

The honesty gate checks the answer's claims *about its own evidence*. It runs three branches,
each stamped separately because each applies to a different population:

| Branch | Runs on | Stamp |
|---|---|---|
| `sql` — `run_honesty` / `run_honesty_async` | Optimize, ReAct | `honesty` |
| `rag` — `run_honesty_rag` (retrieval sufficiency) | Ideate, Focus | `honesty` |
| `citation` — `run_honesty_citation` (progressive skill libraries) | Optimize, ReAct, Focus, Coordinate; abstains without a library | `honesty_citation` |

The checks are deterministic and cost no tokens. There is one exception: the opt-in
`grain_faithfulness` judge (`honestyConfig.grainJudge`) can withdraw a heuristic
grain-substitution verdict.

### 5.1 Kinds and their acts

Checked in this order. The first verdict wins.

| Kind | Fires when | observe | annotate | enforce |
|---|---|---|---|---|
| `unbacked_act` | The answer says a write was prepared, awaits confirmation or was parked, and the turn holds no action record (`action_perform.turn_actions`) | record | append note | replace |
| `unsourced_record` | No data query **succeeded** (none ran, or every one failed); the answer presents a record (table rows, `**Field:** value` lines); and its values are mostly absent from everything the model was given (≥ 2 values and ≥ 40 %, or all of at most 2). Abstains on a declared example and when `support_text` is `None`. | record | append note | replace (+ park on review) |
| `claimed_effect_after_denial` | A write call was **denied** this turn and the answer reports its effect | record | record | replace (+ park) |
| `fabricated_after_failure` | Every data query failed and the answer states a quantity without disclosing the failure | record | record | replace (+ park) |
| `retrieval_failed` | The answer asserts non-existence and every data query failed | record | record | replace (+ park) |
| `empty_relation` | A zero from a filtered query, on a relation the turn never showed to hold data | record | record | replace (+ park) |
| `unverified_literal` | A zero on a filter value nothing showed exists in that column (the preflight value stamp, [PREFLIGHT_SQL §2.2](PREFLIGHT_SQL_ARCHITECTURE.md)) | record | record | replace (+ park) |
| `sample_extrapolation` | A population total spoken from a bounded sample | record | append | append |
| `grain_substitution` | A refused grain answered at a coarser one without saying so (`trigger` = `reject` or `entity`) | record | append | append |
| `correspondence_overreach` | A kind the declared class-level bridge does not bind, presented as if it did | record | append | append |
| `rag` | Retrieval ran, found (near-)nothing (S1: zero qualifying chunks; S2: below the opt-in `sufficiencyThreshold`), and the answer asserts specifics the attachments do not carry | record | record | replace with "the knowledge base does not cover this" (+ park) |
| `unread_citation` · `fabricated_identifier` | The answer cites a library section it never opened, or names an identifier outside the library's closed vocabulary | record | append | append (+ park on review) |

A legitimate empty result never trips the absence kinds: a query that **succeeded** with zero
rows is an honest negative.

`unsourced_record` gets its `support_text` (the knowledge block, the document context and the
conversation) only from the ReAct seam, and only when the agent has a non-`TDA_` data tool.
The Optimize seam passes none, so the check abstains there.

### 5.2 What the user is told

The replacements are worded for **why** the evidence failed. Raw errors are PII-scrubbed
(`gate._redact`, through `guardrails.primitives.redact_pii`). The honesty rewrite runs after the
outbound guard, so it must scrub for itself.

- **`_failure_message`** picks the wording:
  - **`_denied_message`** when every failed query was refused by the agent's authority: "the
    agent's authority refused the query … What this agent may do is set by its owner". A
    refusal is never called a retryable error.
  - **`_honest_message`** otherwise: "a retrieval error … does NOT confirm that the data does
    not exist".
- **`primitives._short_error`** lifts the message out of a tool's JSON result. Braces are never
  spliced into an answer.

**HTML.** `honesty.adapter.apply_to_html` rebuilds the body for a contained answer, or places a
`honesty` note for an appended disclosure. A seam adopts the gate's text only when the event
says `answer_contained` or `disclosure_appended`. An observe verdict records without speaking.

## 6. The guardrails (`agent/guardrails/`)

The guardrails are deterministic and cost no tokens. Every function follows the same shape:
the *assess* half is pure (`policy.scan_outbound`, `primitives.detect_*`), and the *decide* half
is the only one that can move content (`policy.decide_outbound`, `policy.evaluate_inbound`).

### 6.1 Inbound — prompt injection

`detect_prompt_injection` folds confusables before it matches. In enforce or review, a hit
**blocks**. `adapter.build_block_turn` then builds a self-contained refusal turn with zero
tokens, zero cost and `guardrail_blocked: true`, so the recommender and online eval skip it.
The injected text never reaches a model.

In review the original request is parked as `guardrail_inbound`, which is **never releasable**
(§9). Under observe or annotate the detection is shown live and the turn continues. A clean scan
emits a non-persisted `assurance_progress` event, so the dial moves before the end of the turn.

### 6.2 Outbound — PII, toxicity, exfiltration URLs

| Finding | enforce / review | annotate | observe |
|---|---|---|---|
| Toxicity (threat and violence patterns) | **BLOCK**: the answer is replaced by the withholding notice | note | recorded |
| PII (`aws_key`, `api_token`, `email`, `ssn`, `credit_card` with Luhn check, `iban` with checksum, `phone`, `ip_address`) | **REDACT**: masked, delivered | note | recorded |
| Exfil-shaped URL that is novel (not in the question or the retrieved chunks, the X1 whitelist) | **DEFANG**: rendered non-clickable, never deleted | note | recorded |

The precedence is toxicity > PII > exfil, and all findings are reported together. PII findings
carry labels only, never values.

Third-party modules (PROP-003) may only **add** findings (`policy.merge_findings`), never remove
them. Values the owner released after review are not re-flagged in that session
(`runtime._released_allowlist`). Toxicity is never allowlisted.

**Retention.** A plain-enforce toxicity BLOCK is retained (`retained_id`). Routine REDACTs are
never parked, because the parked text would be the raw PII. Review mode parks every modified
answer.

### 6.3 Observe reads `warn`

Under observe a guard that caught something leaves the answer untouched. It still reports what
it saw: `assurance_points._guard_reading` returns **`warn`** with "caught pii — recorded, not
altered (observe mode)". It does not say "screened — clean". The answer capsule reads it the same
way (trust campaign C17). Gates: `test_assurance_points.py`, `test_capsule_stamp_readings.mjs`.

### 6.4 The other guardrail lanes (same detectors, observe-only)

- **Tool-result firewall** — `runtime.scan_tool_result`, called from both tool chokepoints
  (`mcp_adapter/adapter.py` and `tool_enforcement.scan_result`). A tool output carrying an
  injection pattern is logged, recorded as a `tool_result_injection` trust event and broadcast.
  The result is **never altered**. `TDA_*` tools are exempt.
- **Ingest firewall** — `corpus_screen.scan_and_store`, run at every document-ingestion path.
  It records labels and counts, never blocks, and feeds the earned signature and quarantine.
- **Retrieval firewall** — in `rag_retriever`, stamped `retrieval_hygiene`. A chunk carrying an
  instruction is kept but labelled untrusted.
- **Governed Stream** — `runtime.stream_policy` derives the in-flight `StreamGuard`
  (`answer_stream.py`) from the SAME outbound resolution. The stream redacts PII and withdraws
  on toxicity only under an acting mode, so the stream never disagrees with the final answer.

## 7. Leaked-SQL recovery (`agent/leaked_sql.py`) — a recovery, not a gate

A weak tool-calling model sometimes answers a data question with the SELECT it meant to run,
as a fenced block, and calls no tool. `ConversationAgentExecutor._recover_leaked_sql` handles
this. It runs inside the ReAct agent after the leaked component-call recovery and **before**
auto-canvas, which would otherwise file the statement as a code canvas. It acts only when all of
these hold:

- the response **is** one SQL block, with at most `MAX_SURROUNDING_CHARS` (240) of text around it
  (`sole_sql_block`);
- the block is exactly one read-only statement (`is_single_read_statement`, which reuses
  `preflight.catalog.sql_is_write`);
- no non-`TDA_` tool ran this turn (`ran_data_tool`);
- a bound tool can run it (`find_sql_tool`: `base_readQuery` under any server alias via
  `tool_identity.base_name`, else the first non-component tool with a `sql` argument).

The statement runs through the **bound tool object**, the same guarded coroutine a model call
reaches, so Agent Authority, preflight and every rewrite apply. The loop's own
`conversation_tool_invoked` / `conversation_tool_completed` events are emitted, and a provenance
step is added. Downstream, the turn then reads as one that ran a real data call: the grounding
gate grades the rows, `unsourced_record` abstains, and OBDA and the scorers see the query.

The answer is rendered deterministically by `recovered_answer`: up to `RENDER_ROW_CAP` (25) rows
as a table, then the statement that ran. It costs **zero LLM tokens**, so the recovery cannot
hallucinate. A failed run is reported as a failure with the statement, never as a row. A write,
a second statement, a SELECT inside a longer explanation, or a turn that already ran a data tool
is left alone. The recovery runs at most once per turn (`_sql_recovery_done`).

## 8. Turn-failure honesty (`agent/turn_failure.py`)

When a turn fails, `turn_failure.answer(exc)` is the one sentence every engine's failure path
delivers:

- the ReAct catch-all,
- the Optimize "Unrecoverable Error",
- the Focus synthesis failure,
- the coordinator's catch-all in `genie_coordinator`.

The sentence names the failure **class**, read by `describe` through `llm.resilience.classify`
and `out_of_credits` (`credits`, `auth`, `rate_limit`, `overloaded`, `timeout`,
`context_too_long`, …). It also names what to do, and ends "Nothing was computed for this
question." It **never contains `str(exc)`**. The raw error stays in the server log and in the
turn's `error` field. An unclassified failure reads "The request stopped on an internal error
(<type>)".

A failed turn is recorded as failed. `CleanTerminationScorer` (`clean_termination`) fails any
turn whose record says `failed` or `error`, so JUDGED reads red, never SIGNED. A raw provider
error that slips through anyway is caught after the turn: the `output_sanity` scorer reads
`guardrails.primitives.detect_leaked_internals`. The outbound guard itself does not use these
patterns.

The fire drill: `llm/fault_injection.py` (`TDA_FAULT_INJECT=1`, test servers only) raises
real-shaped provider errors so every engine's failure path can be exercised live. Gates:
`test_turn_failure.py`, `test_fault_injection.py`.

## 9. What the gates leave behind

### 9.1 The stamps and the dial

Every engine writes the stamps onto `turn_summary` / `turn_data`: `grounding`, `guardrail`,
`guardrail_inbound`, `honesty` and `honesty_citation`. They are persisted, so the Live Status
card is re-rendered from them on reload. `agent/assurance_points.py` reads them, one reader per
point:

| Point | Reader | Reads |
|---|---|---|
| `inbound_guard`, `outbound_guard` (GUARDED) | `_read_inbound`, `_read_outbound` via `_guard_reading` | no stamp / `ran:false` → `na` · `error` → `unknown` · block → `danger` · redact or annotate → `warn` · observe catch → `warn` · clean → `pass` |
| `grounding_gate` (GROUNDED) | `_read_grounding` (`_grounding_finding` names the family and count) | block → `danger` · annotate → `warn` · pass names the family that ran; corpus spans recorded but not acted on are named with the reason |
| `honesty_gate` (GROUNDED) | `_read_honesty` = worst of `_read_honesty_sql` and `_read_honesty_citation`, driver named | each kind in its own words; contained → `danger`; flagged or disclosed → `warn` |
| `review_parking` (REVIEWED) | `_read_review_parking` | a `review_id` → `warn` ("parked — nothing was lost") · armed with nothing parked → `pass` · no gate in review → `na` |

During the turn, `PlanExecutor._PROGRESS_STATION` maps each gate to the dial's live event type,
so a station settles as its gate runs. The colours and wording follow
[TRUST_STATUS_COMMUNICATION.md](TRUST_STATUS_COMMUNICATION.md).

### 9.2 No learning from a withheld answer

`optimize_engine.turn_seedable` refuses to seed a champion case in two situations:

- a gate withheld the answer (`answer_withheld_by_trust_gate`: a grounding block, an honesty
  block or an outbound block);
- the honesty gate found anything, in any mode (`block`, `flag` or `disclosed`).

A trust-blocked guided turn also counts as a failure against the champion that guided it
(`record_champion_replay_outcome`). Gate: `test_fusion_quick_wins.py`.

## 10. Review and release

`core/review_queue.py` holds parked items in the `review_items` table. Statuses are `pending`,
`approved`, `rejected` and `retained`.

| Source | Parked by | Releasable into the conversation |
|---|---|---|
| `grounding` | grounding BLOCK | yes |
| `guardrail_outbound` | outbound redact/withhold under review; toxicity BLOCK under enforce | yes |
| `guardrail_inbound` | inbound BLOCK under review | **never**: the original is the attack |
| `honesty`, `honesty_rag`, `honesty_citation` | honesty replace or append under review | no |

`RELEASABLE_SOURCES` is an allow-list, so a new source stays unreleasable until someone decides
otherwise. The REST endpoints are `GET /v1/assurance/reviews`,
`POST /v1/assurance/reviews/<id>/<decision>` and `DELETE /v1/assurance/reviews/<id>`, and the
UI is the Assurance Inbox.

A plain *approve* is audit-only. `decide(..., release_to_context=True)` sets
`released_to_context`, and from then on two things happen:

- **The `review_release` context module** (`components/builtin/context_window/modules/review_release/`)
  injects the released original into **subsequent** turns of that session. It sits under a
  header naming it as an owner-released human decision. The sealed, delivered turn is never
  rewritten. The module applies to every profile class and contributes nothing when nothing was
  released.
- **The outbound guard's released-value allowlist** stops the released PII values being masked
  again, so the agent can state what the owner released.

## 11. Invariants

1. **Observe is byte-identical.** No gate alters the answer under `observe`. Both master switches
   default off, and the honesty gate defaults to observe.
2. **Fail open, never silent.** Any gate error returns the original answer. The stamp then says
   `action: "error"` and reads `unknown`, never pass.
3. **One ladder.** Every mode is normalised by `posture_modes`. `review` = enforce + retention on
   every acting path of every gate.
4. **The deterministic layer never withholds on judgement.** Grounding blocks only on a judge's
   *unfaithful* ruling. Uncertainty annotates.
5. **One extraction.** `answer_nums`, `RunRecord.support_chunk_texts` and the trusted scope are
   shared by the live gate and the post-hoc scorers.
6. **Gates run before delivery.** They run before the history save and the `final_answer`
   yield, in a fixed order per engine. Each gate reads the previous gate's output.
7. **A refusal is not a missing record, and a raw error is never an answer.**
8. **A pending question makes every gate abstain.** When the turn stopped to ask (PROP-011),
   grounding, honesty and the RAG honesty check abstain with that reason — there is no answer to
   grade, and the question must reach the person verbatim; the record reads `input_required`.

## 12. Known gaps (recorded, not fixed here)

- (Fixed 2026-10-30.) The RAG branch's verdict is `acted` like every other branch, its `annotate`
  APPENDS the disclosure (`\n\nNote: …`, `disclosure_appended`), and the Ideate and Focus seams
  change the delivered HTML only for an enforce replacement or an appended disclosure — an observe
  flag is a live card, never a banner (the Optimize and Ideate-with-tools seams' rule). Pinned in
  `test_honesty_gate.py`.
- **Coordinator-own figures are observed, not gated.** `grounding/coordinator_figures.assess_turn`
  reads figures a coordinator states that no expert or component supports — ONE rule for the trust
  campaign's scorer (`from_turn`) and the Coordinate engine, which since 2026-10-30 stamps it on
  every turn as `turn_data['coordinator_figures']` (`mode: observe`; the answer untouched). It
  abstains when a component ran whose result the record does not carry (the Database Navigator's
  metadata-only events). Next rung (proposal `test/trust_campaign/proposals/CONTROL-genie-G4.md`):
  re-run G4/G6 live, then decide annotate vs withhold.
- `_read_review_parking` scans `guardrail`, `guardrail_inbound`, `grounding` and `honesty` for a
  `review_id`, not `honesty_citation`.

## 13. File map

| Path | Role |
|---|---|
| `agent/grounding/primitives.py` | `NUM_RE`, `norm_nums`, `answer_nums`, `strip_ordinals`, `strip_budget_line`, `scope_diff`, `answer_out_of_scope_refs` |
| `agent/grounding/corpus.py` | Corpus family T1–T4, `_strip_structure`, second-chance helpers |
| `agent/grounding/types.py` · `gate.py` · `enforce.py` | Value types, the pure gate, the judge fold |
| `agent/grounding/adapter.py` | Inputs via `RunRecord`, config resolution, text/HTML application, trusted scope |
| `agent/grounding/runtime.py` | `run_grounding`, judge escalation, `_park_review` |
| `agent/grounding/coordinator_figures.py` | Measurement only (§12) |
| `agent/honesty/gate.py` · `primitives.py` | Kinds, messages, evidence readers |
| `agent/honesty/runtime.py` · `adapter.py` | Branch entry points, parking, mode resolution, HTML, component suppression |
| `agent/guardrails/primitives.py` · `policy.py` · `types.py` | Detectors, assess/decide, value types |
| `agent/guardrails/runtime.py` · `adapter.py` | `scan_inbound`, `scan_outbound`, `scan_tool_result`, `stream_policy`, block turn, HTML |
| `agent/guardrails/corpus_screen.py` | Ingest firewall |
| `agent/leaked_sql.py` + `conversation_agent._recover_leaked_sql` | Leaked-SELECT recovery |
| `agent/turn_failure.py` | The failure sentence |
| `agent/outcome_loop.py` | S7 one-revision loop |
| `agent/executor.py` | Gate wrappers, inbound scan in `run()`, Optimize seam in `_format_and_yield_final_answer` |
| `agent/engines/*.py` | Per-engine seams (§2) |
| `agent/assurance_points.py` | Readers (§9.1) |
| `core/posture_modes.py` | The ladder, inheritance, labels |
| `core/review_queue.py` + `review_release` context module | Parking and release |

## 14. Tests

All are CI-pure (no server, no database, no live model).

- **Grounding:** `test_grounding.py` · `test_grounding_annotation.py` · `test_corpus_grounding.py`
  · `test_answer_structure.py` · `test_outcome_loop.py` · `test_focus_attachments.py`.
- **Honesty:** `test_honesty_gate.py` · `test_honesty_claimed_effect.py`.
- **Guardrails:** `test_guardrails.py` · `test_exfil_guard.py` · `test_ingest_firewall.py`
  · `test_answer_stream.py`.
- **Recovery and failure:** `test_leaked_sql_recovery.py` · `test_turn_failure.py`
  · `test_fault_injection.py` · `test_focus_no_results_trust_tail.py`.
- **Modes, review, readers and learning:** `test_posture_modes.py` · `test_review_queue.py`
  · `test_assurance_points.py` · `test_capsule_stamp_readings.mjs` · `test_fusion_quick_wins.py`
  · `test_coordinator_figures.py`.

Run in `test/trust_battery.sh`: all of the above except `test_exfil_guard.py`,
`test_ingest_firewall.py` and `test_fusion_quick_wins.py`. The benchmarks
(`test_grounding_benchmark.py`, `test_corpus_grounding_benchmark.py`) also stay outside the
battery.
