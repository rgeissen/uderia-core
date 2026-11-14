# Campaign 2 on MiMo v2.6 Flash — accepted as the baseline (2026-10-28, `20261028-194317`)

| Lever | Right / scored | Answered right | $ / correct | p50 | LLM calls / case |
|---|---|---|---|---|---|
| `@OCFL` (warm, MiMo) | **23 / 23** | 1.00 | $0.0024 | 2.8 s | 2.85 |
| `@OCFL-NORAG` (cold, MiMo) | **21 / 21** | 0.955 | $0.0034 | 3.1 s | 2.92 |
| `@OCWEAK` (Nova 2 Lite) | **22 / 22** | 0.917 | $0.0056 | 2.5 s | 2.78 |

Against run 6 on Flash-Lite ($0.0011 / correct RECORDED — but Google was priced by the fallback,
OC-F12, so ≈ $0.0038 at list price): MiMo is right on every scored case at a lower true cost.
Misses, recorded for the next run: the R family's token bands (O7: R3 135K, R4 114K vs a band
written on Flash-Lite), three self-corrections on R3/R4, Nova repeating identical SQL in a
self-correction (O2 on R2–R5).

### OC-F19 · The strategic planner planned without its tool catalog — a stale prompt parameter erased the placeholder

_Status: fixed · pinned: `test_prompt_runtime_placeholders.py` · case: `A1` (also `A3`, `A6`) · live: `20261029-181934`_

Found while measuring the prompt cache (`docs/Architecture/PROMPT_CACHE_ARCHITECTURE.md`): in every
captured strategic call of `@OCFL-NORAG` the section `### Available Capabilities` read `[]` — the
planner saw the prompts, never the tools. The decrypted template itself carried the `[]`: a
database bootstrapped before 2026-06-04 holds `prompt_parameters(WORKFLOW_META_PLANNING_PROMPT,
available_tools, json, '[]')`, and the loader substituted every declared parameter's default, so
`{available_tools}` was gone before the planner's `.format(available_tools=…)`. Commit 74cb89ad
removed the row from the seed — which only a new database reads. The same rule erased three more
placeholders the code fills at runtime: the coordinator's `{available_profiles}` (its system
prompt listed no experts — routing worked only because the expert tools describe themselves), the
classifier's `{task_description}` (it classified an empty task), `{error_message}` (unused today).

**Fix at the rule:** `prompt_loader.get_prompt_parameters` offers a parameter for substitution
only when it carries a value — an empty default (`''`, `[]`, `{}`, NULL) is a declared runtime
input and leaves its placeholder for the caller; an explicit override always substitutes. Live
re-run: the catalog is present (17–25 governed tools, 2–5K tokens), A1/A3/A6 right, $0.0123
against $0.0137 before, p50 9.9 s against 16.7 s (three cases: a sign, not a measurement).

### OC-F18 · The first cross-lane comparison: the ReAct lane is as right and as cheap as cold Optimize, 2.6× slower — and has no champion lift

_Status: recorded (measurement) · lever `@OCREACT` (Ideate + tools: same model, MCP entry, posture, primer and governed surface as `@OCFL`) · runs `20261029-151322` (OCFL + OCREACT, $0.40) and `20261029-154523` (OCFL-NORAG, $0.20, cap reached one case short) · case: `A2`_

| Lane (MiMo v2.6 Flash, 25 shared cases) | Right | $ for the cases | p50 latency | p50 input tokens |
|---|---|---|---|---|
| Optimize, cold (`@OCFL-NORAG`) | 21/21 | $0.13 | 10.6 s | 30.8 K |
| ReAct (`@OCREACT`) | 21/21 | $0.15 | **27.5 s** | 48.1 K |
| Optimize, warm (`@OCFL`, seeded) | 22/22 | $0.07 | 5.4 s | 9.4 K |

Paired case by case the ReAct lane costs **1.1×** cold Optimize and is right on every scored case;
it is **~2.6× slower**. Measured on the 24 paired cases (a first write-up blamed both on re-sent
context — corrected the same day): **cost** — ReAct sent **1.8×** the input (1.44 M vs 0.78 M
tokens), and 70 % of it (1.02 M) was served from the provider's prompt cache (each ReAct step
repeats the same prefix; Optimize's prompts differ per call — 210 cached tokens), so the near-parity
in price DEPENDS on caching: on a model/provider without it the ReAct lane pays ≈ 1.8× on input.
**Latency** — 90 % of ReAct's time is LLM calls (tools 8 %), a median 6.8 s per call; size explains
only part (5.4 s under 15 K input, 6.8 s above), and even a small ReAct call takes more than twice
Optimize's (≤ 2.3 s per call, bounded from turn time — Optimize records no per-call duration). **The
per-call overhead — IDENTIFIED (same day): OpenRouter's upstream routing.** OpenRouter serves MiMo
v2.6 Flash from several upstreams and picks one per request. A temporary diagnostic in the live
server (removed afterwards) showed every ReAct request leaves at once and the wait is at the
provider (first token 2.4–12.9 s, headers 2.2–5.4 s); the captured live request (4 messages, a
66 KB system prompt, 23 tool schemas = 56 KB, ≈ 27.5 K tokens) replayed directly was as slow, and
OpenRouter's own generation stats name the upstream: **Xiaomi** (latency 1.3–14.6 s, highly
variable, 5–175 hidden reasoning tokens) — for the request as sent and with the tools removed or
the system prompt cut. Single-turn test calls of comparable size were served by **Makora** (0.3–0.8 s,
0 reasoning). Pinning the live request to Makora (`provider: {order: ["Makora"], allow_fallbacks:
true}`) → first token 1.4–1.6 s, 0 reasoning; `provider.sort: "latency"` still picked Xiaomi. Not the
platform's code path, not LangChain, not the event loop (a loop watchdog saw ≈ 0.9 s blocks only
BETWEEN calls), not the client retrying. The large saving is the champion repository —
warm Optimize answered the accuracy family at 20–70× less than ReAct — and the ReAct lane has no
champion lift at all.

**Found and fixed on the way (campaign, not product):** the first table compared ReAct with
`@OCFL`, which is WARM by design (its repository holds every accuracy case's own oracle SQL) —
20–70× "cheaper" answers that were replays; the lane table now uses the COLD lever whenever it ran.
And ReAct · A2 scored wrong though it was right: it charted the five products in order and its text
named only #1 and #5 ("the chart above"); the oracle now reads a successful chart's rows too
(`run_campaign.scorable_answer`, lane-neutral). Both pinned in `test_optimize_campaign_suite.py` §9.

**What it points at (proposals, not built):** (1) the ReAct lane's latency — an OpenRouter
routing preference per LLM configuration (upstream order / no reasoning), measured on this bench
before it ships; the deferred "trim the knowledge block from
post-tool steps" can be MEASURED on this bench before it ships; (2) an exact signed seed on the ReAct lane could run its stored SQL directly (the Optimize
lane's OC-F3 replay, 0 planning tokens) — the one lever that moved the price by an order of magnitude.

### OC-F14 · A tool failure RAISED by a FastMCP 4 server lost the database's message — every correction ran blind

_Status: fixed · pinned: `test_mcp_raised_tool_error.py` · case: `R2` (also `R3`, `R4`) · live: `20261029-073843`, `074316`_

The baseline's "misses for the next run" — Nova repeating identical SQL (O2 on R2–R5), MiMo's R3/R4
token bands (O7: 135K and 114K), three self-corrections on R3/R4 — are ONE defect, and it came in
with the 8888 upgrade (cut over 2026-10-28 18:55; this run started 19:43). FastMCP 3 answered a
failed call with an `isError` RESULT carrying the database's text; FastMCP 4 RAISES it as
`McpError`, delivered inside two TaskGroup ExceptionGroups, and the adapter kept `str(e)` of the
outer group. Measured on 8888 with `SELECT AVG(Age) FROM fitness_db.Customers`: the leaf is
`McpError: Internal error: … [Error 5628] … Column Age not found in fitness_db.Customers.`; the
trace said "unhandled errors in a TaskGroup (1 sub-exception)". So every strategy that reads the
database's words (column / table / database not found, the DB time ceiling) never matched, every
failure fell to the generic LLM correction (~17K input tokens each), and that LLM — told nothing —
re-proposed the identical statement (R2 on Nova: the same SQL three times, then "unable").

Fix, two rules: (1) `adapter.tool_error_text` reads the exception's leaves, and a raised failure
that is not the connection is reported exactly like an `isError` result (error = first line, data
= full text) — the transport rule (`transient_transport_error`) was already leaf-based and is
unchanged; (2) a correction proposing the identical call that just failed is refused before it
runs (`phase_executor._same_call_args`, SQL compared case- and whitespace-insensitively) — the
same call returns the same error, so neither the resend nor a further correction round is paid.

**Live (`20261029-073843` and `074316`, R2–R4 × three levers × two rounds):** the database's
message reaches the trace on every failure ("[Error 5628] … Column Age not found in
fitness_db.Customers"), the fact-based column strategy fires on it and reads the real columns
with 0 LLM, and the identical-call refusal fired 3 times ("not resent"). What R2 still pays on
Nova is Nova: tools it invents (`base_tableDescribe`, `base_tableDescription` — 0.79 from
`base_columnDescription`, under the calibrated 0.8 near-miss cutoff, so it stands and the chain
repairs it) and an AVG over a date column, answered honestly as "cannot be determined".

**Not only this lane.** The ReAct wrapper (Ideate + tools, `tool_enforcement.failed_tool_result`)
handed the model the same wrapper text, so its deterministic dialect retries (LIMIT/TOP Error 3706,
reserved alias 3707) and the multi-server router (B10) read nothing on 8888 either; it now reads
the same leaf text (`tool_enforcement.tool_error_text`, the one definition both lanes use). Every
agent on a FastMCP 4 endpoint — production 8888 included — was affected from the cutover until
this fix is deployed; trust-campaign turns on 8888 since 2026-10-28 18:55 carry the same blind
corrections.

### OC-F17 · A SQL runner was FASTPATHed with no statement — "No SQL provided" read as success

_Status: fixed · pinned: `test_fastpath_sql_required.py` · case: `R3` (also `R5`) · live: `20261029-075104`, `075541` · cost follow-up: `proposals/PASS-6-column-loop-as-one-statement.md`_

R3 on `@OCFL-NORAG` (`20261029-073843`), then on `@OCFL` and `@OCWEAK` (`074316`): the plan left
the SQL to be written (`base_readQuery`, `arguments: {}`). FastMCP 4's schema declares `sql`
OPTIONAL (`anyOf [string, null], default null`), so FASTPATH's required-arguments check passed on
an empty dict; the tool ran without a statement, the server answered "No SQL provided" with
`status: success`, the phase completed and the report said the revenue query had not run — three
wrong answers at the lane's cheapest price. A second regression from the 8888 upgrade (the old
schema marked `sql` required). The guard that already existed covered only a statement the
`schema_before_sql` pass had withdrawn; `phase_executor._takes_sql` widens it to every tool whose
schema takes `sql`: no statement in the plan → the tactical step writes one. The loop's own
FASTPATH had the same hole (R5: `base_readQuery({})` once per column, six "No SQL provided"
successes) and takes the same rule.

**Live:** the rejection fired on every R3/R5 turn of `075104` and `075541`; 0 "No SQL provided"
results; R3 right on all three levers. R5 is now answered with real statistics but costs a
tactical call per column (8–13 LLM calls, O9) — right and expensive, where it was cheap and wrong.
The deterministic shape (one statement over the numeric columns) is proposal PASS-6, to be
replayed before it is built.

### OC-F13 · A data question answered with the planning prompt's greeting — the planner re-plans

_Status: fixed · pinned: `test_conversational_replan.py` · case: `E3` (also `A1`; control `E4`)_

The meta-planning prompt's example for small talk is `{"plan_type": "conversational", "response":
"I'm doing well, thank you for asking! How can I help you with your {mcp_system_name} today?"}`.
MiMo copied it for data questions — A1 "How many customers do we have?", E3 "What is today's date
according to the database?" — 3 of 10 targeted runs, and the planner accepted `plan_type:
conversational` unconditionally, so the greeting WAS the answer (no plan, no tool, O1). Model
behaviour by origin, but the problem space is covered deterministically, so it is the lane's to
carry: `conversational_guard.goal_is_small_talk` asks of the QUESTION (a greeting/thanks/farewell
phrase and nothing else once filler words are removed; consent — "yes", "ok" — deliberately is
not small talk), and `Planner._screen_conversational` re-plans once with a correction note through
the same priced retry the parse error uses (`_strategic_retry`). Measured before the fix: 3/10
greetings. After (`20261029-072623`…`075104`, 9 rounds): 0 greetings in 38 A1/E3 turns, the re-plan fired 7
times and each recovered the right answer at one extra planning call; the E4 control ("Hello,
how are you today?") kept the one-call shortcut 16/16.

### OC-F15 · A final report with no JSON went through a correction that invented its input

_Status: fixed · pinned: `test_final_report_format_retry.py` · case: `A1` · live: the shape did not recur in 12 A1 turns after the fix; pinned on the captured response_

Twice in the 20 OC-F13 turns MiMo answered the report call with prose and no JSON ("Could not
extract a canonical answer."). The adapter's error went to the generic correction, whose "text
sanitization" LLM call proposed the QUESTION as `synthesized_answer` (or `{"TDA_LLMTask": {}}`),
and only then was the report re-run — two extra LLM calls and two `TDA_FinalReport` in the trace
(O1). `_invoke_final_report_task` now repeats the report ONCE inside the tool when the response
carries no JSON object or list (`report_needs_format_retry`), with the format restated, a fresh
stream reader, and both calls' tokens returned as the tool's one priced event (O6). JSON that
fails validation is not this case — the coercion and the fallback extractor own it.

### OC-F16 · A truncated strategic plan ended the turn before its retry could run

_Status: fixed · pinned: `test_strategic_plan_retry.py` · case: `E3` · live: the shape did not recur in 6 E3 turns after the fix; pinned on the captured response_

E3 on `@OCFL` (`20261029-073222`): the planning call billed 51 output tokens and delivered `[{`.
A guard older than the parse-error retry raised "LLM returned an empty or invalid response" for
anything under three characters, so the turn ended as an unrecoverable error with no answer, while
the retry for exactly this case sat on the next lines. `planner.parse_plan_response` reads an
empty or truncated response as "no plan" like any unparseable text, so it gets the one retry; only
a retry that also yields no plan ends the turn.

# Working-model rotation — MiMo v2.6 Flash replaces Gemini 3.1 Flash-Lite (2026-10-28)

_Operator decision. The baseline below was written on Flash-Lite; the next accepted run on MiMo
rewrites it._

| | Gemini 3.1 Flash-Lite (until now) | MiMo v2.6 Flash (from now) |
|---|---|---|
| Arena (llm.uderia.com) | 1432–1447 | ~1448 |
| $ / M in · out | 0.25 · 1.50 (direct Google) | 0.14 · 0.28 (OpenRouter) |
| Status | retired by Google 2027-05-07 | current |

**Smoke test** (`20261028-173022`, `@OCFL`, A1 · E1 · E2 · E3 · M1): 4/4 graded cases right,
fastpath on every one, 0 waste, $0.0041 for the five, p50 2.1 s; M1 in 4 LLM calls where the
Flash-Lite baseline took 6. E3 ("today's date according to the database") answered once with
a greeting and no plan ("I'm doing well, thank you for asking!"); three re-runs answered the
database date correctly — model behaviour, 1 in 4, recorded for the full run to watch.

### OC-F12 · Direct-Google calls are priced by a fallback — Google spend under-reported ~3.5×

_Status: fixed (widened to every model in use) · pinned: `test_cost_vendor_mirror.py`, `test_pricing_notice.mjs`_

`llm_model_costs` holds `gemini-3.1-flash-lite` only under provider **OpenRouter**
(`google/gemini-3.1-flash-lite`, $0.25/$1.50). A call made through the **Google** provider finds
no row and is priced by the fallback. `consumption_turns` since 2026-10-27: 634 Google turns,
12.4M in / 0.25M out, recorded **$1.00** — at list price ≈ **$3.47**. The campaigns' judge panel
also carries `Google/gemini-3.1-flash-lite` (forced on at rate 1.0 during a run) and judge calls
are not in that table. So the platform under-states what Google bills, which is how the cost
the operator saw did not appear in Uderia's own figures. **Widened, at the operator's direction ("all models must be in the
pricing table").** A coverage check over every model in use found **11 of 21 on the fallback**
(MiMo itself among them — OpenRouter rows last synced 2026-07-31). Fix: one rule,
`CostManager.ensure_price` (existing row incl. the vendor's OpenRouter mirror → live price API
→ LiteLLM exact id → $0 local → Azure base model, declared or inferred), run at SELECTION (an
LLM configuration's create/update returns `pricing`; the dialog warns), DAILY (platform job
`platform-cost-price-sync`, whose sync ends with a coverage phase), ON DEMAND
(`GET /v1/costs/coverage`), and at BILLING (a fallback logs once per model; the turn carries
`cost_basis: estimated`). Live on this instance: 11 unpriced → 4 — every fill at the published
price, the two Azure deployments marked `inferred`. Left for the operator: `XAI/grok-4-1-fast-non-reasoning`
(a judge; no public price source), `Friendli/deepseek-ai/DeepSeek-V3.2` (a dedicated endpoint),
`Anthropic/claude-3-5-haiku-20241022` (retired), and `Unknown/unknown`.

**The ledger, traced (same day).** `Unknown/unknown` was three things: turns refused before any
LLM call (0 tokens, $0 — no model to price), and failed Ideate + tools turns whose error record
carried no `provider`/`model` (the B4 timeout's $0.14 booked to no model — now recorded with the
model that served it). Production's `flow/flow-run` is a flow's display-only summary turn
(0 tokens; the cost lives in the node turns). And judge calls WERE billed — but added into the
turn's row, so a Google judge read as the turn model's spend. Now each judge model is its own
`status: judge` line under its own model and price; coverage counts only rows that spent
something and never the flow placeholder. Pinned by `test_consumption_attribution.py`.

---

# Campaign 1, run 6 — every fix in place; accepted as the baseline (2026-10-28)

| Lever | Right / scored | Declined | $ / correct | p50 | LLM calls / case |
|---|---|---|---|---|---|
| `@OCFL` (warm, Flash-Lite) | **22 / 22** | 0 | $0.0011 | 2.4 s | 2.7 |
| `@OCFL-NORAG` (cold) | **20 / 21** | 0 | $0.0022 | 3.2 s | 3.0 |
| `@OCWEAK` (Nova 2 Lite) | **22 / 22** | 1 | $0.0051 | 3.0 s | 2.9 |

_`tda_eval/optimize_campaign/20261027-225340` + the re-run `…231050` (A7, R7 on the weak lever).
Over the day the cold lane went from 4 of 21 answered right (19 %) to 20 of 21 (95 %) and its cost
per correct answer from $0.0215 to $0.0022. Token bands clean after OC-F10. Case-scoring defects
fixed on the way (R7 wording, "an issue occurred" read as a decline, the honest-absence check
counting the budget footer). A7 on Nova was a one-off misread of a scalar ("5" reported as
"5,000") — numeric fidelity flagged it; right on the re-run. The remaining real gap is R3, the
fan-out bait, on the cold and weak lanes: `proposals/PASS-5-fanout-without-a-graph.md`._

---

# Campaign 1, run 4 — after the three passes (2026-10-27, bare `/optimize-campaign` iteration)

| Lever | Answered right | Cost (25+ cases) | $ / correct | p50 | LLM calls / case |
|---|---|---|---|---|---|
| `@OCFL` (warm, Flash-Lite) | 86 % (18 right, 3 declined) | $0.035 | $0.0020 | 2.3 s | 3.2 |
| `@OCFL-NORAG` (cold) | 63 % (12 right, 7 declined) — was 50 % | $0.068 | $0.0057 — was $0.0074 | 4.6 s | 4.2 |
| `@OCWEAK` (Nova 2 Lite) | 91 % (20 right, 1 wrong) | $0.149 | $0.0074 | 2.9 s | 3.0 |

_`tda_eval/optimize_campaign/20261027-210555`. No wrong figure on the working model; the cold
lane's answered-right rose 13 points after `placeholder_sql` / `database_before_sql`. Two
engine defects found, both fixed and pinned below._

### OC-F8 · A delegated prompt's write-back overwrote a sibling phase's call — the turn's price under-stated

_Status: fixed · pinned: `test_sub_prompt_totals.py` · case: `A11`_

Invariant O6 (price honesty) failed on two turns: `turn_cost` / `turn_input_tokens` were
lower than the sum of the priced calls in the trace by exactly one call each (8,211 and
19,387 tokens). Both turns ran parallel phases, and one phase's self-correction switched to a
prompt: `_run_sub_prompt` copied the parent's running totals into the sub-executor and wrote
them back over the parent's when it finished, so a call the sibling phase made in between was
overwritten — a lost update. Now a delta merge (`_merge_sub_totals`) over the values the
sub-executor started from; the turn-cost gate asserts it too. Re-run on the same cases
(A2, A6, A11, E2c, M3 on two levers, parallel phases and `placeholder_sql` firing): O6 clean.

### OC-F9 · The strategic planner was handed an empty turn history on every follow-up

_Status: fixed · pinned: `test_strategic_history_source.py` · case: `M1`_

PASS-4's evidence, read properly: on eight follow-ups whose first turn had answered right
("100 customers, with 59 service tickets awaiting resolution"), the planner wrote *"the
previous counts are not available in the conversation history"*. The strategic prompt's
`turn_action_history` is filled by `ContextBuilder._format_strategic_history` (the default
path), which read `session["workflow_history"]["workflow_history"]`; the session keeps its
turns at `session["last_turn_data"]["workflow_history"]` — where every other reader, and the
legacy builder this one replaced, reads them. So on the default path the planner planned every
follow-up of every session with no earlier turn in view. PASS-4 (a deterministic follow-up
pass) is withdrawn: the defect was the missing context, not the lack of a rule. **Live, after
the fix** (M1–M3 on all three levers): every follow-up whose first turn was right answered from
it — M1 41, M2 908.01, M3 32.33 — where all eight earlier ones had declined; M1's turn 2 ran no
query at all. The one miss was a case defect ("97:3" read as wrong; M3 re-worded).

### OC-F10 · The history fix carried 20K characters of telemetry per turn into every plan

_Status: fixed · pinned: `test_strategic_history_source.py` (§2b) · case: `E1`_

Run 5 (every fix in place) read O7: E1's input rose from 8.6K to 22.8K tokens. With OC-F9 fixed
the strategic history now carried real turns — and the formatter copied each turn whole minus
a deny-list written before the provenance chain, the tool surface, the context-window and
strategic snapshots, the bill of materials and the budget existed: ~20K characters of telemetry
a turn, plus full tool results. It is now an ALLOW-list (`_compact_turn_for_planner`: the
question, the answer, the plan, each tool call with its arguments and a result capped to five
rows and 300-character cells). On the real session: ~74K characters → 5.7K for two turns, the
figures a follow-up computes from intact.

### OC-F11 · The campaign ran into the platform's per-user consumption quota

_Status: fixed (runner) · case: —_

Run 5 hit "Hourly limit exceeded (1000 requests/hour)" for the campaign's user mid-run; half the
cases were recorded as turn-fetch errors. The quota is the operator's policy and is not
touched; the runner now recognises the refusal and stops every lever cleanly with a QUOTA line
naming the cases left, instead of recording them as failures. Budget a full three-lever run at
~180 requests, and do not start one within an hour of another.

### PASS-3 widened · a champion-guided plan naming a database no champion names

_Status: fixed · pinned: `test_database_before_sql_pass.py` · case: `A1`_

The warm lane's remaining wrong-database turns were champion-GUIDED: three retrieved champions
all read `fitness_db`, and the plan queried `DEMO_Customer360_db` (Teradata's public demo
database — not in the MCP catalog, the system prompts or the database; the model's training
prior). `database_before_sql` stood down on any guided plan. It now reads a third way: the
retrieved champions' databases (`schema_facts.databases_in_case`) are the ones the turn has a
reason to believe in, and a guided plan naming a database none of them uses is deferred.
Replay over the saved warm-lane plans bound to the seeded repository: every plan it would
defer had ended wrong or declined.

---

# Campaign 1 — the working model, every case (2026-10-27)

**Agents:** `@OCFL` (Gemini 3.1 Flash-Lite, the champion repository bound, governed surface),
on the campaign's own MCP entry (`uderia.com:8888`, fitness_db), with the observe posture,
`autonomous · deny_writes · enforce_interactive`, a one-line session primer naming the
database. 24 cases (12 accuracy with oracles, 4 efficiency, 7 robustness, 1 multi-turn).

**Result (the COLD lane — one seeded champion, nothing learned):** 4 of 21 oracle-checked answers correct (19 %), $0.086 for 24 cases, $0.0215 per correct answer, p50 latency 6.2 s, 4.25 LLM calls per case, 20 self-corrections paid, 1 landed waste. Right: A12 (a year filter), E1 (the fully qualified table named in the question — FASTPATH, 2 calls, $0.0006), R1 (the misspelled table, corrected once), R6 (the nonexistent tool, repaired after one paid correction). Every other data question failed the same way: a plan that searches for the table with the navigator component, then SQL written against a placeholder or an invented database, run verbatim, a 19K-token self-correction that switches to the `base_query` prompt, and an answer that says it could not. **The seeded champion was retrieved on E2 (`rag_case_count = 1`) and ignored** — see OC-F3. Run dir `tda_eval/optimize_campaign/20261027-182406`.

---

## What the lane got right

| Behaviour | Evidence |
|---|---|
| **FASTPATH is taken wherever the plan knew the tool and its arguments** | `fastpath` fired on 24/24 cases; E1 answered in 2 LLM calls for $0.0006 — the lane's floor |
| **The registry reads the lane** | every pass that fired was a registered one (O8 clean on 24/24): plan_validation ×12, schema_before_sql ×3, charting_cleanup ×2, argument_refinement ×5, final_report_guarantee ×2, context_distillation ×1 (A7, a wide result) |
| **Price honesty** | `turn_cost` equalled the per-call sum on every turn (O6 clean 24/24); the primer was priced apart ($0.0005 each) |
| **No runaway correction, no duplicate SQL** | O2 clean on 23/24 (A11 paid 4 corrections, the ceiling is 3) |
| **The honest decline** | R2 (a column that does not exist) ended with "cannot determine" and no figure; E3 and R5 produced no comparable figure and said so |

---

## Defects found

### OC-F3 · A retrieved champion is not replayed unless the repository is trust-LOCKED — and a seeded planner is not locked by signing it

_Status: fixed 2026-10-29 (operator decision: signed repositories replay too) · pinned: `test_signed_seed_replay.py` §5 · case: `E2` · rule: `planner.planner_replayable` — locked, OR signed with the attested hash equal to the live hash (`attestation.resolve_state`); a case learned after signing breaks the match, so nothing is replayed under a signature that does not cover it_

E2 asks the seeded champion's question word for word. The retriever returned it
(`rag_case_count = 1`, collection 16) and the planner still produced its own plan — the
navigator search, `FROM result_of_phase_2.table_name`, the paid correction, "unable".
`planner.exact_signed_champion` replays a seed verbatim only when the case is
`template_generated` (it is) AND the collection is `trust_locked` (it is NOT: signing the
repository through `/v1/trust/planner/16/sign-off` set its state to `trusted` and left
`trust_locked = 0`). So on an unlocked, signed repository the proven strategy is only a
few-shot example, and the working model ignored it on both the exact and the paraphrased
question. Two readings: the campaign must LOCK its repository (the next run does); and the
lane should say, when a champion was retrieved and not used, that it was offered as an
example only — today nothing in the turn distinguishes "no champion" from "champion
ignored".

### OC-F4 · An agent with no planner repository bound retrieves from EVERY planner repository the user owns

_Status: fixed 2026-10-29 (operator decision: none bound means none) · pinned: `test_planner_binding_scope.py` · live: `@TCOPT` (`ragCollections: []`) logs "RAG retrieval skipped: no planner repository bound", RAG cases 0 · rule: `planner.planner_binding_scope` — the runtime reads exactly what the trust composition lists (`composition.bound_repository_ids`); only an explicit `"*"` reads everything · was: open (product) · evidence: run 2, `@OCFL-NORAG` with `ragCollections: []` — champions
served from collection 1 (the operator's "Default Collection - Teradata MCP", 248 cases), 15
(the server's learning collection) and 16 · case: `E2`_

`planner.py` reads an empty `ragCollections` as "no filter" and searches all of the user's
planner repositories. So the lever built to be cold replayed the operator's own champions,
and its answers rested on repositories its trust composition does not list (an agent's
ingredients are the repositories it BINDS). The campaign's cold lever now binds an empty,
locked repository — the only way to make an agent retrieve nothing. For the product: the
editor's "no planner repository" should mean none, or the composition should name what the
agent actually drew on.

### OC-F5 · The lane learns into the server's default collection, and every agent on that server reads it back

_Status: fixed 2026-10-29 (operator decision: option B — each agent learns into its own planner
repository) · branch `optimize/agent-planner-notebook` · pinned: `test_agent_learning.py` (35),
`test_agent_learning_editor.mjs` (10) · case: `A1` (`@OCFL-NORAG`, the cold lever) · live:
`marketplace_learning_probe.py` (27) · was: open (design, recorded) · evidence: run 2 —
`rag_source_collection_id = 15` on `@OCFL` turns; 116 "RAG case storage" lines into collection 15
during the campaign_

**Fix (`core/agent_learning.py`, rule in `docs/RAG/RAG.md` → "Where an agent learns").** A planner
collection carrying an agent's id in `learning_profile_id` is that agent's learning repository; new
Optimize agents get one at creation; such an agent stores lessons only there (locked ⇒ frozen) and
reads it beside its bindings, never the shared default; it is never a default; it is not a bound
ingredient, so learning never moves the agent's signature; removing the agent removes it.
Existing agents keep the shared notebook until opted in (`POST /v1/profiles/<id>/learning-repository`).

**Live (2026-10-29).** Campaign levers given own LOCKED repositories (18–21) with no trust state
moving; `@OCFL-NORAG` then retrieved no champions (it had read #15) and every turn was "locked —
not stored", 0 writes to a shared repository. A new REST-created agent was provisioned its own
repository at creation, learned one turn into it, retrieved it on a reworded question (1 champion,
its only readable repository) while `@OCFL` stayed filtered to `{16}`, stayed `trusted` through
the lesson, and took its repository with it when deleted. Marketplace probe: a pack never ships
the repository; a subscriber and a forker each get an agent with its own empty, consumer-owned
repository; the publisher's lessons are invisible to them; a sync keeps the agent and its
repository; a fork of a published learning repository is ordinary; unsubscribing and uninstalling
remove the consumer's repositories and never touch the publisher's. **Found by the probe and fixed
on the way:** unsubscribe (and unpublish) remove a subscriber's agents in one bulk write that
bypasses `remove_profile`, so their repositories were left behind (`retire_learning_of_removed`);
a login that creates a new default collection bound it to every Optimize agent, own-repository
ones included (guarded); the planner-binding heal re-pointed an unreadable binding to the shared
default (now dropped for such an agent); an agent repository's case files outlived it (removed).

A successful turn is stored as a champion in the MCP server's default planner collection
(15, auto-created for the campaign's entry). On the next case any agent on that server —
even one bound to a different repository — may retrieve it, so a run warms itself as it
goes and a "cold" measurement drifts warm within one run. The campaign reads
`rag_source_collection_id` per turn to tell replay from planning; a true cold run needs the
learning sink disabled or redirected for the campaign agents (phase 2).

### OC-F1 · Two concurrent turns of one user on different MCP entries collided — a tool call went to the other turn's server

_Status: fixed · pinned: `test_turn_server_pin.py` · case: `A2`_

While the trust campaign's write-escape re-run (`@TCOPT`, MCP entry `server-9698827f`) and
this campaign's run (`@OCFL`, `server-078897bf`) ran at the same time for the same user, an
Optimize turn's later tool calls failed with *"Couldn't find a server with name
'server-9698827f-…', expected one of ['server-078897bf-…']"* — the trust campaign's server id
on the Optimize agent's connection. The first calls of the same turn went to the right
server; the failing ones came after the planner's self-correction switched to the prompt
`base_query` (a delegated sub-execution). The per-turn `@TAG` overlay names its own server
(`adapter.py`, `STATE.maps[0]['mcp_server_id']`) on the primary path; the sub-execution
path fell back to `get_user_mcp_server_id(user_uuid)`, a USER-level value the other
concurrent turn had just set. CLAUDE.md records the overlay as the fix for exactly this
hazard on the tool catalog; the server id has a second, per-user channel the overlay does not
cover on every path. **Root cause:** every REST query runs `switch_profile_context`, which
writes the PER-USER server id; the overlay pinned the server only on the branch that loads
its own client, and the adapter's fallback read the per-user value — which the other
concurrent turn had just rewritten. **Fix:** the turn's PRIMARY server is armed at the
execution seam in a context variable (`tool_identity.set_turn_primary_server`, the sibling
of the multi-source alias map, inherited by every task the turn spawns), every overlay
installation names the turn's server, the default path reads the default agent's own
server, and `mcp_adapter.turn_server_id` is the ONE resolution: overlay slot → armed
primary → the per-user value only outside a turn. Pinned CI-pure (fails without the fix:
the per-user value won inside a child task after another turn's switch). **Re-run:** both
campaigns deliberately concurrent on the fixed server — 0 "Couldn't find a server" lines
where the first run had collided; warm 12 right, cold 6, trust cases clean.

### OC-F2 · `enforce_interactive` with `autonomous: false` denies every tool, not every write

_Status: recorded (configuration semantics) · case: `A1`_

The campaign agents were first provisioned with the trust campaign's posture —
`autonomous: false · deny_writes: true · enforce_interactive: true` — and lost all 24 cases
before a query ran: under any principal a non-autonomous agent is denied every tool
(`agent_authority.is_tool_allowed`: *"this agent is not permitted to act autonomously"*),
and `enforce_interactive` puts a principal on interactive turns. So the trust campaign's
re-run of its write-escape cases, which read as "refused at the chokepoint", was refused by
a blanket denial and proved nothing about statement classification; its record is
corrected and the re-run is owed on `autonomous: true`. For the product: the authority
reading on such a turn says "autonomous run (scheduler)" for a REST interactive turn — the
origin word is wrong — and nothing tells an operator that ticking "enforce on interactive
turns" on a non-autonomous agent turns the agent off.

---

## Pass proposals (the gap, before the weak model even ran)

These are plan shapes the working model produced that a pure rule on the plan could
repair, each paid for today with an LLM self-correction or a wrong answer.

### PASS-1 · A placeholder identifier in planned SQL is run verbatim — IMPLEMENTED

_Status: fixed · pinned: `test_placeholder_sql_pass.py` · case: `E2c` · pass `placeholder_sql`_

_Evidence: A1 — `SELECT COUNT(*) AS customer_count FROM [FOUND_DATABASE].Customers`; first
attempt `FROM result_of_phase_1`. Replay over 122 distinct saved plans: 15 deferred, 14 of
them turns that ended wrong (27 paid self-corrections). Live, cold lane, A1 after the pass:
`placeholder_sql` fired, the deferred statement was written by the tactical step with the
inventory in view, RIGHT, 0 self-corrections._

The planner writes a phase-2 statement that names phase 1's output as a table
(`[FOUND_DATABASE]`, `result_of_phase_1`, `{database}`), FASTPATH runs it, the database
answers with a syntax error, and a self-correction LLM call (19K tokens) proposes switching
to a prompt — the answer is still "unable". The rule: a SQL phase whose FROM/JOIN names a
bracketed or `result_of_phase_N` identifier is not runnable; either bind it from the named
phase's result (the executor already has the placeholder-resolution machinery for
arguments) or defer the phase until the real name is known, as `schema_before_sql` does for
columns. Replay over every saved `raw_llm_plan` tells how often the working model does this.

### PASS-2 · A component tool called as a SQL runner, with an argument it does not have — IMPLEMENTED (as plan-validation correction 3c)

_Status: fixed · pinned: `test_unknown_tool_correction.py` · case: `R6` · `plan_validation` correction `unknown_tool`:
a planned tool the catalog lacks → a component with an action suffix is renamed to the component, a SELECT under an
invented runner (`TDA_ExecuteSQL`, `base_query`, the navigator's `query`) goes to the read tool with the query moved to
`sql`, a near miss within difflib 0.8 to the real name; 5 of 9 distinct shapes in the saved plans are repaired at 0 tokens_

_Evidence: A2 — `TDA_DatabaseNavigator {"query": "SELECT TOP 5 product_name, SUM(revenue) …
FROM sales_db.sales_data …"}` accepted, returned nothing, a chart drawn from one garbage row._

`plan_validation` strips hallucinated arguments for MCP tools against their schema; a
component tool with an unknown argument (`query` is not in the navigator's schema) went
through, the component "succeeded" with no data, and the turn ended with a non-answer and
no self-correction at all. The rule: the same argument check for component tools, and a
phase whose only tool is a navigator/search component but whose argument is a SELECT is a
SQL phase in disguise — rewrite it to `base_readQuery` (or refuse it) deterministically.

### PASS-3 · The planner invents database names it has never seen — IMPLEMENTED

_Status: fixed · pinned: `test_database_before_sql_pass.py` · case: `A8` · pass `database_before_sql`;
replay over 122 saved plans: 26 deferred, 24 of them turns that ended wrong. Live attempt on the
cold lane after the pass (A2, A3, A4, A8, R7): no plan carried an invented database on that
attempt (the shape is the model's to produce — A8 took the placeholder pass instead and was
right), so the replay stands as the calibration and the campaign's drift watch will read the
pass the first time the shape recurs._

_Evidence: A1 (`DEMO_Customer360_db`), A2 (`sales_db.sales_data`, `Sales_DB.ProductSales`),
A3 — on an agent whose primer named `fitness_db` and its five tables._

The strategic prompt's context on the first data turn holds no database inventory: the
conversation-history module contributed 138 tokens (the primer's reply, not its statement),
the tool catalog names no database, no champion matched, no graph is bound. Preflight checks
a statement's databases against *the databases this session observed* — but the session has
observed none, and when the SQL goes through a component (PASS-2) preflight never runs. The
rule, by analogy with `schema_before_sql`: a plan whose SQL names a database the session has
not observed gets a `base_databaseList` phase inserted first and its query rewritten after
the real names are read. Cheaper still: the primer's statement should reach the planner.

---

## Observations

- The session primer sent through REST (`is_session_primer: true`) is persisted and costs
  ~8.7K input tokens per session on this agent; its statement contributes almost nothing to
  the next turn's planner context (138 tokens of history). Operators who prime with "educate
  yourself on the DDLs" get value through the tool results in the workflow history, not
  through the statement.
- The seeded champion (`how many customers do we have?`) is in the repository (1 row);
  whether E2/E2b retrieved it is in the run's E rows below.

---

# Campaign 1, runs 2–3 — the warm lane, the cold lane, the weak model (2026-10-27)

| Lever | Model | Repository | Right / scored | Declined | Answered right | Cost (24 cases) | $ / correct | p50 | LLM calls / case | Self-corrections |
|---|---|---|---|---|---|---|---|---|---|---|
| `@OCFL` | Flash-Lite | locked, 16 champions | 17 / 17 | 3 | 85 % | $0.023 | $0.0014 | 2.3 s | 2.5 | 5 |
| `@OCFL-NORAG` | Flash-Lite | empty, locked (cold) | 9 / 10 | 8 | 50 % | $0.067 | $0.0074 | 4.3 s | 3.5 | 17 |
| `@OCWEAK` | Nova 2 Lite | locked, 16 champions | 19 / 20 | 1 | 90 % | $0.129 | $0.0068 | 2.5 s | 2.9 | 12 |

_Run 3, `tda_eval/optimize_campaign/20261027-185737`, re-scored with the hardened oracle
(a decline is a non-answer, never a wrong figure; the budget footer and the question's own
figures are not the answer's). Accepted as `baseline.json`._

**What the levers say**

- **The champion lift is the whole story on this model.** Warm vs cold on Flash-Lite: 85 % vs
  50 % answered right, 5× cheaper per correct answer, a third of the self-corrections. Every
  accuracy question asked word for word replays at 0 planner tokens (517 tokens, $0.0001).
- **The weak model is carried.** On the warm lane Nova 2 Lite answered 90 % right — the gap
  table has NO row where the working model is right and the weak one wrong; two rows go the
  other way (E2b, M1), where Nova planned the SQL the primer named while Flash-Lite wrote a
  placeholder. The lane's machinery, not the model, decides the warm lane's outcome.
- **Weak is not cheap here.** Nova 2 Lite via OpenRouter cost 5.6× Flash-Lite for the same
  cases ($0.129 vs $0.023) — the price column follows the pricing table, and the
  leaderboard's "weakest" is not the cheapest.
- **The cold lane fails one way.** Eight declines on `@OCFL-NORAG` are PASS-1/PASS-3: a
  placeholder identifier or an invented database, run verbatim, one paid correction, "unable".
  R5 (per-column statistics) exhausted 7–9 self-corrections on both the cold and the weak
  lever and never reached the column-iteration orchestrator.

**Defects the warm runs added**

### OC-F6 · Plan hydration covers one plan shape only — a follow-up computed from the previous answer is not it

_Status: closed 2026-10-29 — superseded by OC-F9, measured; PASS-4 not built. On MiMo
(`20261028-194317`) every follow-up is right at the lane's floor of two LLM calls (plan + report):
M1 turn 2 computes 41 from the history with no tool, M2 runs one `AVG` query, M3 has the database
compute `97/3`; only Nova on M1 paid three more (a `TDA_LLMTask`). A compute-only plan would save
no LLM call, so the pass would carry weight for nothing. Re-open if a model regresses M1–M3._
_Was: recorded (design gap → proposal PASS-4) · evidence: run 3 M1 on all three levers —
turn 2 ("What is the difference between those two counts?") re-queried or declined;
`plan_hydration` fired on 0 of 73 turns · case: `M1`_

Turn 1 returned two counts (100 customers, 59 unresolved tickets). Turn 2 asks for their
difference. `_hydrate_plan_from_previous_turn` fires only when the plan's FIRST phase is a
LOOP over a dangling `result_of_phase_N` from the previous turn — that is its whole scope,
and it is honest to it. The documentation's promise ("inject previous results, skip
re-fetch", 30–50 % multi-turn saving) is broader than the code. On M1, Flash-Lite answered
"the values were not provided in your request" (the previous answer's figures sit in its
history and it did not use them), Nova re-ran a query and computed 41. **PASS-4 (proposal):**
a follow-up whose figures are all present in the previous turn's answer or tool results is a
compute-only plan — no data phase, the figures injected as the compute step's inputs — the
deterministic trigger being the question's figure references resolving entirely against the
previous turn's `collected_data`.

### OC-F7 · The budget finish line's figures were graded as the answer's claims — FIXED, pinned

_Status: fixed · pinned: `test_answer_structure.py` · case: `R4`_

Every budgeted agent's answer ends with `_finished within budget — 1 of 12 steps used ·
~71k of ~150k tokens used · 10 of 300 s used_`. `primitives.answer_nums` — THE answer-side
number extraction for the grounding gate and the numeric-fidelity scorer — read those
figures as claims, so an honest "could not be retrieved" scored `numeric_fidelity = danger`
(0.57) on every declined turn of the campaign. `strip_budget_line` now removes the line
before extraction; the pin asserts each footer figure is not a claim and the answer's own
figure beside it still is.

