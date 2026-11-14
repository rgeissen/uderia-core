# Prompt Cache Architecture

How the platform gets the provider's prompt cache to serve the repeated part of every LLM
call: one plan for every provider, declared by the call site, applied by the handler, read
back from the provider's usage and priced. This is the live contract; the S2 roadmap entry
(`docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md` §S2) is its history.

## 1. The contract — four rules, every provider

| # | Rule | Who owns it |
|---|---|---|
| 1 | **Stable first, volatile last.** Nothing that changes with the turn may sit before the end of the stable prefix. The call site declares where that prefix ends. | the call site (`cache_prefix=`) |
| 2 | **A breakpoint where the provider needs one.** Under `breakpoints` the stable prefix of the prompt becomes its own cached block, beside the system and history breakpoints. | `cache_policy.mark_stable_blocks` · `mark_bedrock_body` |
| 3 | **An affinity key where the provider routes by one.** Under `automatic` the agent's key rides the request in the provider's own field, so consecutive calls reach the machine that holds the prefix. | `cache_policy.affinity_key` · `affinity_fields` |
| 4 | **Read what was served, price it.** Every provider's usage is normalised to `{input (TOTAL), output, cached, written}` and priced by one rule. | `cache_policy.read_usage` · `priced_cost` |

A call site never knows which provider it talks to: it says *where its stable prefix ends*
and nothing else. The handler turns that into whatever the provider's capability calls for.

## 2. Per provider

| Provider | Capability | What the plan sends | Measured on the strategic prompt (2026-10-29) |
|---|---|---|---|
| Anthropic (native) | `breakpoints` | system block · last history message · **the stable prefix as its own block** | (no local key; same shape as the broker row below) |
| Claude via OpenRouter | `breakpoints` | the same three breakpoints as content parts | **0 → 9,269** cached per call: the old single breakpoint sat on a 2.5K system prompt, under Haiku 4.5's 4,096-token minimum |
| Bedrock Claude | `breakpoints` | the Anthropic body with the same breakpoints | not measured here (no AWS key); request shape gated |
| Bedrock Nova | `breakpoints` | `cachePoint` after the system prompt and after the stable prefix | not measured here (no AWS key); request shape gated |
| OpenAI | `automatic` | `prompt_cache_key` = the agent's key | already ~8.2K of 9–12K cached; the key is neutral at low request rates and is OpenAI's guidance at volume |
| Azure OpenAI | `automatic` | `prompt_cache_key` (learned off if the API version refuses it) | not measured here (no local key) |
| xAI | `automatic` | header `x-grok-conv-id` = the agent's key | **128 → 8,320–8,960** cached |
| OpenRouter (open models) | `automatic` | **nothing** (see §2.1) | upstream-dependent: 0–6.9K |
| Google Gemini | `implicit` | nothing: order alone decides | ~8.2K cached, best effort (one run of two read 0) |
| Friendli · Celeris · MLX · Ollama · other Bedrock families | `none` | nothing; the reading says `capability: none` | n/a |

### 2.1 Why OpenRouter takes no affinity key

OpenRouter's own routing hashes the first system and first user message, and sticks to an
upstream **once that upstream has served from cache**. Its `session_id` replaces that with a
key that sticks **from the first request** to whichever upstream answered. Replaying the
Optimize lane's live call sequence (strategic → tactical → report, three times) on MiMo
v2.6 Flash, the key pinned the agent to upstreams that served 27 cached tokens over nine
calls in both runs; without it the broker's own stickiness reached an upstream that served
6.9K. Every MiMo upstream *prices* a cache read (`/models/…/endpoints`), so the catalogue
cannot tell which upstream really serves a partial prefix. For an open model on OpenRouter,
caching is the upstream's behaviour, and the platform leaves the broker's cache-aware routing
alone. Claude on OpenRouter is a `breakpoints` provider and is fully covered.

## 3. Declaring a stable prefix

```python
await executor._call_llm_and_update_tokens(prompt=p, ..., cache_prefix=stable)
#                  └─ handler.call_llm_api(..., cache_prefix=stable) ─ both signed fallbacks forward it
```

The handler **verifies before it trusts**: `stable_split` honours the declaration only if it
is a true prefix of the prompt, at least `MIN_STABLE_CHARS` long and shorter than the whole.
Anything else is ignored, and the request is sent exactly as without one. A wrong declaration
costs a cache miss, never a wrong prompt.

**Computing it from a template** (`cache_policy.template_stable_prefix`): render the template
once with every value *not declared stable* replaced by a sentinel, then keep what precedes
the first sentinel. It needs no knowledge of the template's text (an edited system prompt
moves the boundary with it) and it fails safe: a key added to the template later is volatile
until someone declares it stable.

### 3.1 Call sites

| Call | Stable prefix today | Status |
|---|---|---|
| Strategic planner (`planner.py`, `_strategic_cache_prefix`) | skills block + chart note + the template up to its first volatile value. Stable keys: `mcp_system_name`, `reporting_tool_name`, `sql_consolidation_rule`, `available_prompts`, `component_tools`, and `available_tools` **only when every enabled tool is shown** (a governed surface offers a different few per turn) | **declared** — ~7.2K of 7–11K tokens shared turn to turn on the campaign agent |
| Tactical step | ~70 tokens shared: the phase goal and workflow state lead, the static instructions (~840 tokens) trail | not declared; would need the template reordered (instructions first) |
| Self-correction | the failed call leads its catalog | **fixed at the size, not the order:** the correction catalog follows the turn's governed surface and lists each tool's opening statement (`executor._get_correction_catalog`, `COMPACT_DESCRIPTION_CHARS`): a generic correction went 19.4K → 3.4K tokens, so reordering the template to cache it is no longer worth a prompt deployment |
| Final report (`TDA_FinalReport`) | small, volatile | not worth a block |
| Planner retries (parse-error, conversational re-plan) | the planning prompt plus a note | **declared** (the first attempt's stable prefix) and the duplicate catalog stripped like the first attempt: system prompt 24.6K → 2.3K tokens |
| ReAct lane (LangChain) | grows by whole messages; already ~70% cached on OpenRouter | its own helpers (`prompt_cache.priced`, the system-block marking); not on this plan yet |

The tactical and self-correction prompts are plain-text templates in `schema/default_prompts/`.
Reordering them is a prompt deployment (`update_prompt.py`), not a code change.

## 4. Switches and safety

- **Master switch** `APP_CONFIG.PROMPT_CACHING` (env `TDA_PROMPT_CACHING`; on in
  `docker-compose.yml`, off in a bare dev run). It governs what is **sent**: off means every
  request is byte-identical, with no breakpoint, no affinity field and no `cachePoint`. What is
  **read** is read either way (Gemini's implicit cache is priced whether or not it is on).
- **Per-agent opt-out**: `cacheConfig.enabled: false`; `cacheConfig.ttl` is `5m` | `1h`.
- **Learned refusals, never model lists**: `_NO_TTL_1H` (a model refusing the 1h TTL),
  `_AFFINITY_REFUSED` (a provider/API version refusing the affinity field, recognised by the
  field's own name in the error), `_CACHE_POINT_REFUSED` (a Bedrock model refusing cache
  markers). Each is retried once without, remembered, and never sent again.
- **Breakpoint budget**: at most three of Anthropic's four — system, last history message,
  stable prefix.
- **The key leaks nothing**: `affinity_key` is a hash of (owner, agent), one per agent, stable
  across turns and sessions, because every turn of an agent shares its system prompt and catalog.
- **Multimodal**: a question that is already content blocks (images, documents) is left unmarked.

## 5. Reading, pricing, assurance

- `read_usage` normalises every family; Anthropic and Bedrock report the cached prefix
  *outside* `input_tokens`, so the total is rebuilt (fresh + read + write). The Bedrock Claude
  stream keeps `cache_read_input_tokens` / `cache_creation_input_tokens` from `message_start`.
- `priced_cost`: cached reads at the provider's read factor (Claude 0.10, Fable/Mythos 5.1
  0.025, Gemini 0.25, OpenAI/Azure/OpenRouter 0.50, Nova 0.25); writes at
  `write_price_factor` (Claude 1.25× for 5m, 2× for 1h; Nova 1.0×).
- `prefix_hash` covers system + history + the declared stable prefix: the bytes the provider
  was asked to cache. An unchanged hash that read nothing, above the model's minimum and inside
  the TTL, is an assurance finding under `breakpoints` (`miss_is_finding`), and the
  provider's own choice elsewhere.
- Per turn: `workflow_history[n].cache` (`calls`, `cached_tokens`, `cached_share`, `hits`,
  `misses_found`), the `cache_efficiency` dimension, and the `prompt_cache` point on SIGNED
  ([ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md)).

## 6. Files and gates

| File | Role |
|---|---|
| `src/trusted_data_agent/llm/cache_policy.py` | the pure plan: capability, stable split, template lead, breakpoint shapes, Bedrock body, affinity, refusals, usage, price |
| `src/trusted_data_agent/llm/handler.py` | applies it per provider branch (`call_llm_api(cache_prefix=)`), the learned-refusal sets, the readings |
| `src/trusted_data_agent/llm/streaming.py` | the Bedrock Claude stream's cache counts |
| `src/trusted_data_agent/agent/executor.py` | `_call_llm_and_update_tokens(cache_prefix=)` |
| `src/trusted_data_agent/agent/planner.py` | `_STRATEGIC_STABLE_KEYS`, `_strategic_cache_prefix` |
| `src/trusted_data_agent/agent/prompt_cache.py` | the per-turn accumulator and the LangChain lanes' helpers |

Gates (all in the battery): `test_prompt_cache_plan.py` (the plan: shapes, per-provider
application with fakes, learned refusals, OFF ⇒ byte-identical, the planner's declaration;
mutation-tested), `test_prompt_cache.py` (the S2 policy, accumulator, scorer, wiring),
`test_prompt_cache_baseline.py` (the freeze).

**Measuring a provider again**: replay captured prompts from
`logs/llm_conversation_history.log` through the provider's own client, cold (a nonce at the
head of the system prompt), as the live sequence runs: strategic, tactical and report
interleaved, seconds apart. Read `prompt_tokens_details.cached_tokens` (or the provider's
equivalent) and, on OpenRouter, the serving upstream. One run per variant is not evidence;
the OpenRouter result in §2.1 reversed between a single back-to-back test and the replayed
sequence.
