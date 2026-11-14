# Needs Input — a turn that stops to ask (PROP-011)

> **The contract.** When an agent cannot go on without something only a person can give, the
> turn **stops**, the person is **asked** through the channel the turn came from, and their
> answer **resumes** the work, recorded as theirs. The question is ONE object with an id; the
> turn ends in ONE state, `input_required` — interrupted, not terminal. Where Uderia can
> tell that input is needed, **code** asks; where only the model can tell, its act is honoured
> by code from the moment it is made. The design and its decisions:
> PROP-011 (`docs/proposals/PROP-011-needs-input.md`). Phase 1 (this document) is built; phase 2
> (the model's act on three carriers) follows; phases 3–4 (unattended runs, chains, elicitation,
> the measure) are designed there.

## 1. The pieces, and which is the framework's

| Piece | Where | Owner |
|---|---|---|
| The state split — `is_settled · is_terminal · is_interrupted · is_failure` | `core/turn_states.py` | framework |
| The records and the resume binding | `core/input_request_store.py`, `schema/46_input_requests.sql` | framework |
| The turn-scoped ledger: policy, raising, the guarantees, re-entry | `agent/input_requests.py` | framework |
| The one resume rule every face calls | `core/input_resume.py` | framework |
| The REST faces | `api/rest_routes.py` (`input_requests.*`, `tasks.input`) | framework |
| The chat face | `api/routes.py` (`/ask_stream` with `input_answer`; `GET /session/<id>` serves `pending_input_request`) | framework |
| The card, the composer rule, the split panel | `components/builtin/needs_input/` (`system` component, `instances: agent`) | the component |

**Disabling the component removes the surface, never the state.** With `needs_input` off, a
turn that stopped to ask still ends `input_required`, the question is still appended to the
answer (text and HTML), the chokepoints still refuse every further tool, and the person can
still answer in the composer or over REST. What goes is the card's buttons and (phase 2) the
model's own act. The same split as the OBDA planes: the plane is a component, the policy is not.

## 2. The question object

```
InputRequest  id "ir-…" · user_uuid · session_id · turn · task_id
              origin     interactive · rest · scheduler · flow · ask · coordinate · genie_child
              raised_by  code · model · data_source · child
              profile_id · profile_tag · source (op id · tool · playbook anchor · MCP server) · parent_id
              kind       choose · provide · confirm · approve · auth
              items      1–4, each {header ≤12, question ≤300, options[2–8] | schema (flat primitives), multi_select, other_allowed}
              why · default · digest (sha256 of the canonical question)
              status     pending · accepted · declined · cancelled · expired · superseded
              answer · answered_by (a person · policy(never) · relay(quote)) · answered_via (chat · rest · inbox) · answered_at
              expires_at · created_at
```

`validate_ask` is the ONE validator for every carrier of the act (`TDA_AskUser`, the planner's
`plan_type: input_required`, the self-correction `ASK_USER:`, the ` ```uderia-ask ``` ` block —
phase 2). A `provide` question may never ask for a credential (field names and formats that
denote one are refused; such input goes through Uderia's own credential and OAuth flows).

## 3. The state

A turn record (`workflow_history[i].status`) reads `input_required`; a background task reads
`input_required` **only for a client that declared it** (`X-Uderia-Accepts: input_required`
on the query, or `APP_CONFIG.INPUT_REQUIRED_STATE_DEFAULT`) — every other client sees
`complete` with the question as the answer text and `input_required: true` beside it, exactly
what it saw before plus a flag, so no poller waits forever. Every reader asks
`core/turn_states.py`: a loop that waits on `is_terminal` alone waits forever on a question;
`is_settled` is THE question a poller asks. The gate (`test_input_requests.py` §10) greps the
migrated readers for a list of terminal strings of their own.

`superseded` means exactly two things: the person moved on in the composer (a new turn that
does not answer the pending question — `execution_service` supersedes at turn start), or a
parent's answer reached a child by another turn (phase 3). `never` is terminal: the turn
completes with an honest sentence and the question is recorded `declined` by `policy(never)`.

## 4. Who raises, and the posture

**Code — the resolvers.** `platform_operations._resolve_owned` / `resolve_agent` and
`platform_profile_shape._pick`: two or more of the caller's things by one name raise
`Ambiguous` (before: misreported as *"no … of yours has the name"*, or the first taken
silently). `bind` catches it and asks the ledger: `enforce` → a `choose` question whose
option values are the ids code binds on re-entry, returned to `call` as `InputRequired` (a
`str` carrying the record, so every `(value, err)` reader keeps working); `call` answers the
model `{"status": "input_required", "request_id", "question", "message"}`. `observe` → the
ambiguity is recorded on the turn (`ambiguities_observed`) and the step fails honestly ("2 of
your repositories are named 'Sales' — name it by id: …") — what the `guessed_past_ambiguity`
measure reads (phase 4).

**The posture** is the agent's authority — `authorityConfig.inputPolicy` (portable, unsigned,
downgrade-guarded: `ask → never` and `enforce → observe` are each a `Downgrade`):

```
inputPolicy: { interactive: ask | never, unattended: park | fail, raisers: enforce | observe, maxPerTurn: 1, defaults: {} }
```

`APP_CONFIG.INPUT_RAISERS_FLOOR = "observe"` holds every agent at observe (the rollout dial);
`INPUT_EXPIRY_INTERACTIVE_HOURS` (168) and `INPUT_EXPIRY_UNATTENDED_HOURS` (72) are the expiry.

**The model — one explicit act, three carriers (phase 2).** The act is ONE object
(`{kind, question, why, options? | schema?}`), validated by `validate_ask` and raised by
`raise_from_act` into the same ledger the code raisers use (`raised_by: model`); each engine
carries it the way it already ends a turn without running anything:

| Engine | Carrier | Where it lands |
|---|---|---|
| Ideate + tools (ReAct) | the tool **`TDA_AskUser`** — the `needs_input` component's own tool (a system component may carry a tool where its manifest declares one: `manager._TOOL_BEARING_TYPES`), offered where `offers_tool` says the policy is not `never`; its result tells the model to end the answer with the question | `NeedsInputHandler.process` |
| Optimize — at planning | `{"plan_type": "input_required", "ask": {…}}` instead of a plan: no phases exist, the turn goes to summarization with the question as its answer | `planner.py`, beside the conversational answer |
| Optimize — mid-plan | the self-correction answer `ASK_USER: {…}` — raised, then returned on the `FINAL_ANSWER` path so the plan ends at that phase | `phase_executor.CorrectionHandler` |
| Ideate (no tools), Focus, ReAct | a fenced ` ```uderia-ask {…}``` ` block anywhere in the answer, lifted out before rendering (`extract_ask_block`); the prose stays | the three engines' seams, before the formatter |

The prompts teach each carrier (`WORKFLOW_META_PLANNING_PROMPT` 2a, `TACTICAL_SELF_CORRECTION_PROMPT`
format 4, `CONVERSATION_EXECUTION` / `RAG_FOCUSED_EXECUTION` the block, `CONVERSATION_WITH_TOOLS_EXECUTION`
rule 4); they are a hypothesis on a weak model — see §9.

## 5. The guarantees — enforced in code

1. **Nothing runs past a question.** `input_requests.refusal_for(tool)` at the three
   chokepoints — `tool_enforcement.sql_gate_before` (after authority, before any statement is
   read), `components/manager._run_component` (ReAct, in the tool's `(content, artifact)`
   shape), `mcp_adapter._invoke_mcp_tool_impl` (Optimize, every tool incl. `TDA_*`). The
   refusal is recovery-shaped (*"A question for the user is pending: … End your answer by
   asking it. Call no further tools."*); the turn's own reporting tools are exempt.
2. **The plan ends at the question.** `executor._run_plan` breaks before the next phase group
   when a question is pending (the refusal is the backstop, not the mechanism — a refused tool
   result there is what fires the self-correction strategies); the summarization still runs.
3. **The question reaches the person verbatim.** `finish_payload` appends it to
   `final_answer_text` and `final_answer` (HTML) when absent; `stamp_last_turn` appends it to
   the turn record and the LLM-facing `chat_object`; the card renders the record.
4. **The gates abstain.** `_run_grounding_gate_full`, `_run_honesty_gate`,
   `_run_honesty_gate_rag` return the text unchanged when `pending()` — a question makes no
   claim, and the honesty gate under `enforce` replaces answers, never questions.
5. **The turn records the truth, in ONE place.** `execution_service` finishes the turn after
   the engine: the result carries `input_request` (with the owner's binding), the record reads
   `input_required`, the stream carries an `input_required` event — no engine was edited for it.
6. **Budget.** `maxPerTurn` questions per turn; past it the agent is told to answer with what it
   has and an `input_budget_exceeded` notice reaches the Inbox.

## 6. Resuming — re-entry, with a binding that is a credential

The answer starts the **next turn** of the same session, bound to the question: the original
request, the question and the typed answer are the model's input (`reentry_block`), the
person's line is what they see (`display_message`), and the ledger is armed with the answer so
the step that asked receives the chosen value **in code** (`bound_answer` — the resolver
returns the chosen id without asking again). A REST question continues under the **same task
id**; the chat answers through the composer (`/ask_stream` with `input_answer`).

`InputRequest.resume` is a signed ticket (HS256 under the deployment's JWT secret, like every
other ticket) binding the principal, the session, the turn, the question's digest and an
expiry; `input_request_store.consume` flips `pending → accepted|declined|cancelled` in ONE
guarded UPDATE — single use. An answer presented by another person, for another request,
after expiry, with a changed question, or a second time is refused (`input_resume.accept`:
403 · 404 · 409 · 400). The binding is issued to the turn that raised the question and to the
owner's own reads (`GET /v1/input-requests/<id>`, the session load's `pending_input_request`).

The re-entry turn records `reentry_of`; the figures for the level-2 decision (decision 4) are
the turn's own tokens and latency beside the original turn's.

## 7. The faces

| Face | Asks | Answers |
|---|---|---|
| Chat | the card under the answer (`final_answer.input_request`; `rest_task_complete.input_request`; on reload `workflow_history[i].input_request` + `pending_input_request`) | the card → composer → `/ask_stream {input_answer}`; or typed text under the **composer rule**: an option's value or label, or free text where the question allows it, is the answer — anything else is a new prompt and the question is superseded (the card says so) |
| REST | task `status: input_required` (declared) with `result.input_request` | `POST /v1/input-requests/<id>/answer {decision, answer, resume}` · `/withdraw` · `POST /v1/tasks/<id>/input` — all `human_only` |
| The status strip | **Waiting for you** — a reading in the quiet posture colour, never amber (`uderia:input-required` / `uderia:input-answered`) | — |
| The split panel (Workspace → Needs Input) | this agent's questions: waiting for you, then asked and answered; the header counts pending across every agent, *All agents* widens | the same card, over REST |
| The Assurance Inbox | every pending question, counted in the badge as a pass chip (*N waiting for you*) | the same card, over REST |

## 7a. Phase 3 — chains, unattended runs, auth, a data source asks, remembered answers

**Chains — the question travels up, the answer down.** An agent this turn asked
(`platform_asking.ask`) or an expert a coordinator drove (`genie_coordinator.SlaveSessionTool`)
may end `input_required`; its result carries `input_request` whatever status the poller reads.
`input_requests.relay_child_question` makes it THIS turn's question — the same items verbatim,
`raised_by: child`, `parent_id` the child's request, `source: ask:@TAG:<child session>` /
`coordinate:@TAG:<child session>` — and the parent model is told to relay, never to answer.
On the parent's re-entry the step that relayed finds its continuation
(`continuation_for(prefix)`) and resumes the CHILD in-process (`input_resume.resume_child`:
the child's own re-entry turn, the answer bound) instead of asking it afresh; `accept` had
already written the person's answer onto the child's record as a relay (`answered_via: relay`,
`answered_by: relay(<parent id>)`). A declined answer reaches the child as a decline. An
asked child runs under origin `ask` (unattended: its `never` policy parks instead of closing).

**Unattended — the run waits.** A scheduled run whose turn stopped to ask records the run
`input_required` (not success — the question is not a result), tells the person through the
task's own channel (email / webhook / Google Mail, subject *— needs your input*, with a link)
and the Inbox, and waits; the question's `task_id` is `sched:<task>:<run>`, so the answer route
sends the answer back to the scheduler (`resume_task_run`: a new run, linked, in the same
session, delivering its answer as any run). The hourly platform job `platform-input-expiry`
(`expire_due(return_rows=True)`) ends an expired run `unanswered` and says so through the same
channel. A flow node whose turn stopped to ask raises `FlowNeedsInput`: the node run is recorded
`input_required` (its task and session), the run pauses `input_required` with its outputs so
far, and the answer route resumes the flow from that node (`resume_flow_for_input`: a downstream
partial run seeded from the paused run, the re-entry posted to the node's own session under the
engine's internal token — the public query route keeps `input_answer` only for an internal
token, `_request_is_internal`).

**`auth_required`.** A user connector (Google, Outlook, Teams, SharePoint, Slack) the person
has not connected used to answer *"not connected"* inside a `status: success` envelope and the
model carried on. `invoke_connector_tool` now stops the turn with an `auth` question
(`raise_auth_request`; the item carries `connector: {platform, server_id}`); the card's only
answer is **Connect** — that platform's own OAuth popup; its success message answers the
question and the request continues once connected. The turn reads `auth_required`
(`turn_status`, `input_status` on the result).

**A data source asks (MCP elicitation, the client side).** Every MCP client of a data source
(the pooled client, the ReAct tool loader) is built with `core/mcp_elicitation.langchain_callbacks()`:
an `elicitation/create` becomes a `provide` question (`elicit_items_from_schema`: the form's flat
schema, an `enum` field as options), `raised_by: data_source`, `source: mcp:<server>:<tool>`.
The protocol holds the call open, so the callback waits a bounded time
(`INPUT_ELICIT_HOLD_SECONDS`, 90) for the answer and otherwise answers `cancel` — the turn ends
`input_required`, and on the re-entry the same call finds `bound_answer(source)` and accepts at
once (the retry pattern). URL mode is relayed as the URL to open.

**Uderia as an MCP server.** `ask_profile` reports a question as a trailing line —
`[Needs input ir-…] <question> … — answer with ask_profile(answer_to="ir-…", answer="…")` — and
`answer_to`/`answer` answer it through the REST face (the client's own read carries the binding)
and poll the continuation.

**Remembered answers (decision 10).** A single `choose`/`confirm` card offers *just this once ·
use this for the rest of the session · always for this agent*. `session` writes
`session.input_defaults[key]`; `agent` writes the owner's `inputPolicy.defaults[key]`. The key
is `ambiguous:<kind>:<name>` for a code-raised choice and `q:<hash of the question>` for a
model-raised one; `bound_answer` and `raise_from_act` consult them before asking, and every use
is recorded on the turn (`input_defaults_used`). Never a typed value, an approval or an auth.

## 7b. Phase 4 — the assurance point and the measure

`needs_input` is a registered assurance point at **REVIEWED** (power `withhold`, fate
`stopped`, class `control`, on every class): a turn that stopped to ask reads `pass`, acted —
*asked the user* (a reading, never an alarm); a turn that went past a code-detected ambiguity
without asking reads `warn` — *went past N*; a question closed by policy or a remembered answer
used reads `pass`; nothing to ask reads `na`. REVIEWED now has two members (human review, needs
input) and reads the worst of them. The point owns the measure **`guessed_past_ambiguity`**
(`eval/scorers/reliability.GuessedPastAmbiguityScorer`, THE_RUN, non-gating): abstains when
code saw nothing, fails a turn that carried on past `ambiguities_observed`, passes one that
stopped. The scoreboard readings — asked · answered · declined · expired · by raiser · median
time to answer — are `GET /v1/input-requests/stats` (`input_request_store.stats_for_user`),
shown in the Needs Input panel. The trust campaign targets the point on every engine (N1, N2,
N3) so the coverage gate holds it.

## 7c. The fire drill and the live probe

A weak model does not take the act (§9), so the machinery around a question could not be
provoked on demand. **`agent/needs_input_drill.py`** is the lever — the provider
fire drill's idiom: with `TDA_NEEDS_INPUT_INJECT=1` (off by default; production never sets it)
an input carrying `[needs-input:choose|provide|confirm|auth|elicit]` raises that question in
code the moment the ledger is armed, exactly as a resolver would (a remembered answer first;
a re-entry turn raises nothing). **`test/needs_input_live_probe.py`** drives every face
through the real server with the drill on: A the answer route and the re-entry record, B a
decline, C a remembered answer, D `auth_required`, E a held elicitation, F the coordinator
and a relayed expert question, G a scheduled run waiting → webhook → resumed → expired
unanswered, H a flow paused at the node that asked and resumed there.

**Measured (2026-11-02, Nova 2 Lite, $0.18 for the full run): 31 of 31 checks.** The first
run missed F once, and the miss was the probe's: the relayed question was the Focus expert's
OWN `TDA_AskUser` call ("Which region are you referring to? North America · Europe · …" —
the model's act, taken under a coordinator, on the weakest model in use), and the probe
answered it with the drill's graph id, which the validator refused as it should (not among
the options). Answering from the question's own options, the expert is continued with the
answer and the coordinator's re-entry completes (two relays on that run). A coordinator may
relay more than once in a chain — each relay answers the child that asked. Found and fixed on
the way: the expiry job closed a run whose turn was still running (the question is raised at
the turn's start) and the turn then overwrote it — the job now closes only a run that is
waiting, and a turn finishing on an already-expired question records `unanswered`; and the
drill takes the resolver's remembered-answer step first, as a resolver does.

**The browser pass** — `test/needs_input_browser_pass.py` drives the faces a DOM stub cannot
reach in a real Chromium (Playwright, the installed Chrome as the channel) against the test
server with the drill on: sign in, a marked question in a new conversation, the card under
the answer (Send held until an option is picked, the composer bound), the answer on the card,
the re-entry answer in the same conversation, the split panel's history, and the agent
editor's *Needs input* section (Authority card: in a conversation · unattended · the
platform's own questions · questions per turn · standing answers; every runtime value
listed; weakening is the downgrade guard's). It found two defects the gates had not:
**(1)** on reload, a turn record's question is the question as RAISED (pending) — three
settled questions rendered pending, the composer bound itself to one, and the next message
was refused as *already accepted*; the session load now serves every question of the
conversation in its live state (`input_requests` by id) and a snapshot alone never renders
pending. **(2)** on the stream, the finish seam raises the `input_required` notice AFTER the
final answer was forwarded, so the final-answer event carries no `input_request` — the card
now rides the notice (`eventHandlers.js`), session-guarded, under the answer just rendered.
Both are pinned in `test_needs_input_ui.mjs`. Observed on the way: Nova 2 Lite, asked the
drill's question a second time on the re-entry, asked on its own through `TDA_AskUser` —
the pass declines such a question and says so, never counts it against Uderia.

## 7d. The carriers are variables — the prompt teaches the act only where it can be taken

The model's act is taught by four carrier texts (the fenced block for Ideate and for Focus,
the planner's Directive 2a, the self-correction's format 4). They are **not** in the prompt
files: each prompt carries one placeholder, `{needs_input_carrier}`, and the text lives in
`input_requests.CARRIERS`, substituted per turn from the ledger by the engine that renders the
prompt (`carrier_section(kind)` — the `{active_prompt_context_section}` idiom; the planner lists
it among the strategic template's stable keys, so the cache prefix keeps it). The text is the
empty string whenever the model may not ask — the component not in use for the agent, the
policy `never`, no ledger at all — so an agent without the component carries nothing of the
act: no ~150 tokens of instruction, no `TDA_AskUser`, and its resolvers record an ambiguity
instead of raising one (`policy_for` reads the component chain through
`component_enabled`: `interactive never · raisers observe · enabled false`). **One rule, three
effects**: the component toggle governs the prompt text, the tool and the posture together.
What stays general wording is the *when* — the balanced rule in the two conversation prompts
(use what you were given, ask when neither the history nor a tool can settle it); that holds
with or without the component, where asking means asking in prose. Gate: `test_input_requests.py`
§15 (the carriers by state; every substitution site).

## 8. Gates

`test/test_input_requests.py` (153: the state split, the store and the binding, the ledger and
its postures, the validators, re-entry, the resolvers, the REST outcome, `input_resume`, the
downgrade guard, the seams and the readers in the source; §11 the act's three carriers) and
`test/test_needs_input_ui.mjs` (33: the renderer executed under the DOM stub, the composer
rule, the wiring). Both in the battery. The component's manifest is held by
`test_component_bus.mjs`, `test_component_signals.py`, `test_component_instances.mjs` and
`test_component_tool_schema.py` like every other component's. The trust campaign carries the
planted ambiguities as family **N** (`test/trust_campaign/suite.py`: N1 the input only the
person has, N2 ambiguous between documents).

## 9. What the live check measured (2026-11-02)

One planted-ambiguity turn per engine on the campaign's weak model (Nova 2 Lite, $0.08 in
all), the prompts above deployed, through the REST face with `X-Uderia-Accepts`:

| Engine | What the model did | What Uderia did |
|---|---|---|
| Ideate | asked — in prose, naming the three gaps; no `uderia-ask` block | `complete`, an honest answer (a prose question is not a guess, and not a state) |
| Ideate + tools | fabricated two rows (`TDA_AskUser` was offered, not called) | the honesty gate (`unsourced_record`, review mode) withheld the record; `complete` |
| Focus | answered for one meaning of `observe` without asking | `complete`, clean |
| Optimize | planned past the ambiguity (`threshold = 'usual_value'`, `region = 'discussed_region'` — a guess in SQL) | the SQL failed, the recovery switched to a prompt, and a latent `dict in dict` at the phase-result slot killed the turn (fixed: the slot is a list); rerun: an honest failure sentence, `complete` |

**Reading.** The carriers are proven in code (§8) and the guarantees hold around them; the
model's *decision* to ask is, on this model, the prompt's hypothesis and nothing more — 0 of 4
used a carrier, 1 of 4 asked in prose, 2 of 4 guessed (both caught or failed honestly). This is
exactly why code raises first (§4) and why `guessed_past_ambiguity` (phase 4) measures the
model's decision instead of trusting it. A stronger model, or a one-shot example per carrier,
is the next measured step — not a prompt rule stated and assumed.
