# LLM Provider Architecture

**One platform, eleven providers, one recovery policy.** Every model call an agent makes goes
through `src/trusted_data_agent/llm/`: a client built from the agent's own LLM configuration, a
call that is retried, continued or served by the agent's *signed* fallback when the provider
fails, usage read back the same way for every provider, and a failure the user is told about
in one honest sentence. A call site never branches on the provider.

This is the reference for the whole `llm/` package (status 2026-10-30). The prompt cache has
its own contract: [PROMPT_CACHE_ARCHITECTURE.md](PROMPT_CACHE_ARCHITECTURE.md). Pricing and
the consumption ledger: [COST_TRACKING_ARCHITECTURE.md](COST_TRACKING_ARCHITECTURE.md).

---

## 1. The contract — rules every call obeys

| # | Rule | Where it is enforced |
|---|---|---|
| 1 | **The turn's model is the agent's model.** A call uses the client and provider/model of the agent that runs the turn (including an `@TAG` / `profile_id` override), never the process-global default and never another user's. | `executor.profile_llm_instance`, `_profile_llm_baseline`, `mcp_adapter.turn_llm` (§4) |
| 2 | **Recovery is classified, bounded and recorded.** Retry only what a retry can change, honour the provider's `retry-after`, continue a truncated answer at most twice, serve an exhausted primary from the agent's **signed** fallback only, never retry or re-route a refusal. Every act is a recorded `RecoveryStep`. | `resilience.py` (policy) · `handler.call_llm_api` · `resilient_chat.wrap` (§5) |
| 3 | **A call is priced at the model that served it.** A fallback call returns the fallback's provider/model; the persisted turn names the model that served. | `_call_fallback` (depth 1) · `uderia_served_by` · `executor.served_provider_model()` |
| 4 | **One usage per call, input = the TOTAL prompt.** Usage is normalised per provider (cached prefix included); a stream that repeats cumulative usage is counted once. | `cache_policy.read_usage` · `streaming.openai_compatible_stream` · `stream_usage.apply` (§6) |
| 5 | **Capabilities are learned from the provider's own refusal, never from a model list.** A disabled thinking budget, a 1h cache TTL, an affinity field, `stream_options`: each is retried once without, remembered per process, and never sent again. | `thinking_support` · `_NO_TTL_1H` · `_AFFINITY_REFUSED` · `_CACHE_POINT_REFUSED` · `_NO_STREAM_OPTIONS` |
| 6 | **A failed turn says what failed, never the raw error.** | `agent/turn_failure.answer` (§9) |
| 7 | **Off means byte-identical.** The resilience envelope, prompt caching and answer streaming are each behind a switch; with the switch off the request and the persisted turn are what they were before the feature. | `APP_CONFIG.RESILIENCE_ENVELOPE` · `PROMPT_CACHING` · `ANSWER_STREAMING` (§11); frozen by `test_resilience_baseline.py` |

---

## 2. Providers

The provider is a string on the LLM configuration (`llm_configurations` in the user's
preferences, `config_manager.get_llm_configurations`). Credentials are stored Fernet-encrypted
per user and provider (`user_credentials`, `auth/encryption.encrypt_credentials` /
`decrypt_credentials`); an LLM configuration's own `credentials` are merged over them (that is
where OpenRouter-style keys live). When nothing is stored, `langchain_adapter._load_credentials_for_provider`
and `configuration_service` fall back to environment variables.

| Provider string | Service | Credential keys | Env fallback | Notes |
|---|---|---|---|---|
| `Google` | Gemini via the `google-genai` SDK (`genai.Client`) | `apiKey` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Native PDF/image input; thinking budget (§7); implicit cache |
| `Anthropic` | Claude (`AsyncAnthropic`) | `apiKey` | `ANTHROPIC_API_KEY` | Native PDF/image input; cache breakpoints |
| `OpenAI` | `AsyncOpenAI` | `apiKey` | `OPENAI_API_KEY` | Model list filtered to ids containing `gpt`; `gpt-5`/`5.4` models get `max_completion_tokens` |
| `Azure` | `AsyncAzureOpenAI` | `azure_api_key`, `azure_endpoint`, `azure_api_version`, `azure_deployment_name` | `AZURE_OPENAI_*` | The model list is the deployment name |
| `Amazon` | AWS Bedrock (`boto3` `bedrock-runtime`; LangChain `ChatBedrockConverse`) | `aws_access_key_id`, `aws_secret_access_key`, `aws_region`, `listing_method` (`foundation_models` \| `inference_profiles`) | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | Request body per model family (`anthropic`, `amazon`, `cohere`, `meta`, `ai21`, `mistral`); an inference-profile ARN is passed through untouched (`_normalize_bedrock_model_id`) |
| `Friendli` | Friendli.AI, OpenAI-compatible | `friendli_token`, `friendli_endpoint_url` (optional) | `FRIENDLI_TOKEN`, `FRIENDLI_ENDPOINT_URL` | Serverless `https://api.friendli.ai/serverless/v1`; a **dedicated endpoint** when `friendli_endpoint_url` is set (`/v1` appended, model id entered by hand, no model list; the LangChain lane caps `max_tokens` at 8192 and disables function calling) |
| `OpenRouter` | `https://openrouter.ai/api/v1`, OpenAI-compatible | `openrouter_api_key` | `OPENROUTER_API_KEY` | Live pricing from its model list (§10) |
| `XAI` | xAI Grok, `https://api.x.ai/v1` | `xai_api_key` | `XAI_API_KEY` | |
| `Celeris` | Celeris-1 diffusion LLM, OpenAI-compatible | `celeris_api_key`, `celeris_base_url` (optional) | `CELERIS_API_KEY` | One endpoint rule for every client: `client_factory.celeris_base_url` (default `CELERIS_DEFAULT_BASE_URL`); falls back to `CELERIS_DEFAULT_MODELS` when `/models` is unavailable; repeats cumulative stream usage (§6) |
| `MLX` | Local `mlx_lm.server` (Apple Silicon), OpenAI-compatible | `mlx_host` (default `http://localhost:8080`), optional `mlx_max_tokens`, `mlx_timeout_seconds` (default 900), `mlx_disable_tools` | `MLX_HOST` | No client retries (`max_retries=0`); every request states its output ceiling (`mlx_max_output_tokens`) |
| `Ollama` | Local Ollama (`handler.OllamaClient`; LangChain `ChatOllama`) | `ollama_host` (client factory) / `host` (model list; the LangChain lane accepts both) | `OLLAMA_HOST` | Default `http://localhost:11434` |

`langchain_adapter.get_supported_providers()` lists the same eleven. Provider colours for the UI
live in `core/provider_colors.py`.

---

## 3. Two call lanes

| Lane | Entry point | Used by |
|---|---|---|
| **Handler** | `handler.call_llm_api(llm_instance, prompt, …)` → `(text, input_tokens, output_tokens, provider, model)`, reached through `executor._call_llm_and_update_tokens` | Optimize (strategic/tactical planning, final report, self-correction), Ideate without tools, Focus synthesis without component tools, the MCP adapter's in-turn LLM tasks |
| **LangChain** | `langchain_adapter.create_langchain_llm(llm_config_id, user_uuid, …)` → a LangChain chat model | Ideate + tools (ReAct, `conversation_engine`), Focus with component tools, Coordinate (`coordinate_engine`), extensions |

**The handler lane** is one function with one branch per provider. Before the branches it builds
the system prompt (`_get_full_system_prompt`), resolves the cache plan, normalises history roles
(`model` → `assistant`), and logs the full context. Each branch reads one response shape, streamed
or not (§6). Native multimodal input: Google (file parts), Anthropic (image + PDF blocks),
OpenAI/Azure (images), Bedrock Claude; a failed native attempt falls back to text. After the call
it records the session tokens (`session_manager.update_token_count`) and the model used with its
`planning_phase` (`update_models_used`). A `raise_on_error=False` caller gets `("", 0, 0, …)` on
failure; the reason is in the log.

**The LangChain lane** builds the provider's own class (`ChatOpenAI`, `ChatAnthropic`, a
thinking-aware `ChatGoogleGenerativeAI` subclass, `AzureChatOpenAI`, `ChatBedrockConverse`,
`ChatOllama`). The instance is then re-classed, in this order, never replaced:

1. `stream_usage.apply` — one usage per streamed call (OpenAI-compatible only, §6);
2. `fault_injection.attach` — the fire drill (a no-op unless `TDA_FAULT_INJECT=1`, §9);
3. `resilient_chat.wrap` — the resilience envelope, when it is on (§5).

Re-classing (`object.__setattr__(inst, "__class__", Sub)`) keeps the provider's constructor,
fields and `bind_tools`. An instance marked `_no_function_calling` (Friendli dedicated endpoint,
MLX with `mlx_disable_tools`) tells the conversation agent to skip `bind_tools`. LangChain message
content is read through `response_text.text_from_content`, which keeps only plain text parts, so a
reasoning model's `thinking` block can never leak into an answer.

---

## 4. Choosing the model for a turn

- **The agent's configuration.** The executor reads the active profile's `llmConfigurationId` into
  `current_provider` / `current_model` / `thinking_budget`. An Optimize profile may also carry
  `dualModelConfig` (`strategicModelId`, `tacticalModelId`); when they differ, separate strategic
  and tactical clients are built and `_call_llm_and_update_tokens` picks one by `planning_phase`.
- **The baseline client.** `STATE['llm']` and the per-user instance were built from the user's
  **default** profile. `_profile_llm_baseline()` returns that profile's LLM configuration (not the
  per-user provider, which a `profile_id` override rewrites). When the turn's provider/model differs
  from the baseline, the executor builds `profile_llm_instance` from the pooled factory, with the
  stored credentials merged with the configuration's own.
- **The `@TAG` / `profile_id` override (Optimize).** `optimize_engine.apply_override_state_overlay`
  puts the override's client, `llm_provider` and `llm_model` into a per-turn
  `ChainMap(override_slots, APP_STATE)` and onto `executor.profile_llm_instance`. Process-global
  `APP_STATE` is never written, so concurrent turns cannot see each other's model.
  `restore_override_app_state` reverts only the per-user provider/model, and snapshots
  `turn_served_provider/model` first, so persistence (`served_provider_model()`) names the model that
  actually served.
- **In-turn tasks of the MCP adapter** (filtering, core LLM tasks, the final report) use
  `mcp_adapter.turn_llm(STATE, user_uuid)`: the overlay's client at **any depth** of stacked
  overlays (`_overlay_map`; the governed tool surface wraps the override's overlay in a second
  one), else the per-user registry, the process-global slot only as a last resort.
- **A deferred client.** `switch_profile_context(validate_llm=False)` may park a placeholder
  `{"placeholder": True, provider, model}`. `call_llm_api` materialises it on first use
  (`_materialize_placeholder_llm`, pooled, no network call) and raises an actionable error when no
  credentials exist for the provider.

---

## 5. Resilience — the envelope

`resilience.py` is the pure policy (no I/O, no SDK import). The handler and
`resilient_chat` are the two actors; `core/provider_health.py` is the breaker;
`agent/resilience_envelope.py` holds what one turn accumulated.

**Classification** (`classify(exc)`, duck-typed across every SDK):

| Kind | Reasons |
|---|---|
| retryable | `rate_limit` · `overloaded` · `server_error` · `timeout` · `connection` |
| terminal | `auth` · `bad_request` · `context_too_long` · `not_found` · `stream_ceiling` · `unknown` (an unknown error is terminal: retrying a bug is noise) |
| refusal | the provider declined on content grounds (`ProviderRefusal`, or a refused finish reason) |

`out_of_credits(exc)` (HTTP 402 or a quota/credit message) is not a class of its own, so an empty
account stays retryable and a signed fallback can take over. It decides what the user is told, and
it counts against the breaker.

**What happens:**

- **Retry** with `backoff(attempt)` = base·2^(n−1) + jitter, capped at `MAX_BACKOFF_SECONDS` (30);
  a provider's `retry-after` is honoured verbatim up to 60 s, and beyond that the call is not retried.
  Attempts: `LLM_API_MAX_RETRIES` (5, base delay 2 s); a `timeout` is capped at
  `TIMEOUT_MAX_ATTEMPTS` (2) and, with a fallback bound, goes to the fallback after the first attempt
  (`route_to_fallback_now`).
- **Continue** a truncated answer (`max_tokens`, `length`, `MAX_TOKENS`): the partial becomes the
  assistant's prior turn, an instruction asks it to continue exactly from where it stopped, at most
  `MAX_CONTINUATIONS` (2) times; `join_continuation` trims a repeated tail. On the LangChain lane only
  a final answer without tool calls is continued.
- **Fall back** only to the profile's `llmFallbackConfigurationId`, and only if the user owns that
  configuration and it is not the primary (`resilience_envelope.resolve_fallback`). It is an
  instance binding in the profile contract (`profile_config_contract.IMPORT_BINDING_KEYS`) and enters
  the deployment half of the trust envelope, so the fallback is itself attested. The fallback runs
  through the same `call_llm_api` at `_fallback_depth=1` (a fallback never falls back). On the
  LangChain lane the fallback model is built with `create_langchain_llm(..., _is_fallback=True)`; the
  agent's tools are re-bound in the fallback provider's format and the result is stamped
  `uderia_served_by`.
- **Refusal** is terminal: never retried, never handed to a fallback. The LangChain lane returns
  `refusal_answer(...)`; the handler raises `ProviderRefusal` for the caller.
- **The breaker** is per provider name, process-wide: 3 consecutive retryable failures within 120 s
  open it for 60 s, doubling per re-trip up to 600 s. When it is half-open, one call is the probe.
  An open breaker sends the call to the signed fallback at once; without a fallback the call is still
  attempted once. Terminal errors mean the provider answered (`record_probe_answered`), except an
  empty account (`_record_terminal`).
- **Timeouts are on silence, per attempt.** With the envelope on, each attempt runs under
  `streaming.activity_limit(LLM_CALL_TIMEOUT_SECONDS, LLM_STREAM_MAX_SECONDS)` (120 s of silence,
  600 s absolute), and the Anthropic/Google SDK clients get `LLM_ATTEMPT_TIMEOUT_SECONDS` (45 s).
  A cut is a classifiable `LLMCallTimeout` (`no_response` / `stalled` retryable, `ceiling` terminal).
  The executor's whole-call limit is widened to a backstop the envelope can never hit first.

**The record.** Each act is a `RecoveryStep` (`retry · continuation · fallback · refusal`),
recorded into the task-local `TurnResilience`, broadcast live as an `llm_recovery` Live Status
step, sealed into the provenance chain, and stamped on the persisted turn as `turn["resilience"]`
(absent when nothing was recovered). The SIGNED station reads it as the `llm_recovery` assurance
point: retries and continuations are evidence (`pass`); an answer from the signed fallback is inside
the signature (`pass`, naming who served); only a model outside the declared fallback is `warn`; a
refusal or a turn no model answered is `na` (the failure is graded at JUDGED by `clean_termination`).

**Envelope off** (`RESILIENCE_ENVELOPE` false): the handler keeps the pre-envelope loop: it retries
Anthropic `InternalServerError`/`RateLimitError` and OpenAI `APIError` with exponential backoff and
re-raises; no continuation, no fallback, no breaker. That loop is frozen by `test_resilience_baseline.py`.

---

## 6. Streaming and usage accounting

**Streaming** (`streaming.py`, "the Governed Stream"). When the caller passes `on_delta` (a
`DeltaSink`: `delta(text)`, `reset()`), each provider branch streams through the sink and returns
the same response shape its non-streaming call returns, so the parsing code is single-source.
Helpers: `anthropic_stream`, `openai_compatible_stream`, `google_stream`, `ollama_stream`,
`bedrock_anthropic_stream`. A provider or Bedrock family without a stream takes its normal call and
the sink receives the whole text once, with the note `provider_no_stream`. A retry or a withdrawn
partial calls `sink.reset()`. Every chunk marks activity (`note_activity`), which is what the silence
limit counts from.

**Usage invariants:**

1. **Input is the total prompt.** `cache_policy.read_usage` rebuilds the total for providers that
   report the cached prefix outside `input_tokens` (Anthropic, Bedrock) and returns
   `{input, output, cached, written}`. The cache reading goes to the task-local
   `handler.LAST_CALL_USAGE` and the turn accumulator (`agent/prompt_cache.record_call`).
2. **One usage per stream, handler lane.** `openai_compatible_stream` asks for
   `stream_options={"include_usage": True}` (learned off per model if refused) and keeps the *last*
   usage it sees; a missing usage reads as `None` and the handler keeps the answer.
3. **One usage per stream, LangChain lane.** LangChain sums `usage_metadata` across chunks. A server
   that repeats cumulative usage (Celeris on 3 of ~4 chunks) was billed ~3× input / ~2× output.
   `stream_usage.apply` re-classes every `BaseChatOpenAI` so `_astream`/`_stream` strip usage from
   each chunk and stamp one field-wise-max merge on the final chunk (the stream is delayed by one
   chunk; the final chunk keeps its `finish_reason`). Other providers report incremental usage, where
   the sum is right, and are not touched.
4. **Priced at the served model** (rule 3 above; COST_TRACKING invariants 6–8).

---

## 7. Thinking support

`thinking_budget` on an LLM configuration is a tri-state: `None` (not set), `0` (disabled), `-1`
(dynamic), or a token count. On the handler lane it is applied to Gemini models whose version is
2.5 or higher; a session-name call always asks for `0`.

Some Gemini models refuse a **disabled** budget with `400 INVALID_ARGUMENT`, and the API does not
say which. `thinking_support` learns it from the refusal: `_google_generate_content` retries once
without the thinking config, and only if that succeeds records the model
(`record_disable_refusal`). Later calls consult `supports_disabled_thinking` and omit the budget,
raising a small output ceiling to `THINKING_HEADROOM_TOKENS` (1024) so a capped call still has room to
reason and answer. The LangChain lane does the same in `_thinking_aware_google_cls`. The result is
always "thinking enabled", never a failed call. There is deliberately no model list and no version rule.

---

## 8. Clients, the pool and lazy SDKs

- `client_factory.create_llm_client(provider, model, credentials, validate=False)` is the single
  source for handler-lane clients. `get_or_create_llm_client` pools them per
  `provider:model:sha256(credentials)[:16]` (at most 50, oldest evicted; `clear_client_pool`).
  `validate=True` always builds fresh.
- `client_factory.test_llm_credentials` makes one minimal call per provider; it backs
  `POST /v1/llm_configurations/<id>/test`.
- `lazy_sdk.sdk(name)` stands in for `google.genai`, `anthropic`, `openai` and `boto3` and imports
  them on first attribute read, so a process that talks to one provider never imports the others.
  An `except` clause naming several providers' classes uses `loaded_attrs(...)`, which resolves only
  the SDKs already imported. Matching an error therefore never imports another SDK.

---

## 9. Failure honesty and the fire drill

**`agent/turn_failure.answer(exc)`** is the sentence every engine's failure path delivers
(Optimize, the ReAct agent, Focus, the coordinator). It reads the failure class through
`resilience.classify` / `out_of_credits` and returns "The language model provider did not answer:
…" with what the user can do and the HTTP status, or "The request stopped on an internal error
(…)". It never contains `str(exc)`; the raw error stays in the server log and the turn record. The
turn is recorded as failed, and JUDGED's `clean_termination` grades it.

**Fault injection** (`fault_injection.py`) is for a test server only. With `TDA_FAULT_INJECT=1`,
a call whose messages contain `[fault:402|401|429|503|timeout]` raises an SDK-shaped error
(`InjectedProviderError` with `status_code`, or `InjectedTimeout`). The seams are the handler's
attempt loop (`check`, inside the `try`, so retries, the breaker and the fallback treat it like a
real failure) and every LangChain model's generation calls (`attach`, beneath the resilience
wrapper). Calls without a marker are untouched, so agents still pass their activation tests.

---

## 10. Model lists, recommendations and live pricing

- `handler.list_models(provider, credentials)` (behind `POST /models`) returns
  `[{name, recommended}]`. "Recommended" matches the `recommended_models` table patterns (wildcards
  allowed); the UI's Recommended/All toggle filters on it. Friendli serverless falls back to
  `provider_available_models` when its live `/serverless/v1/models` call fails or no token is given.
- `_PROVIDER_PRICING_SPECS` (OpenRouter, Friendli) maps a public, credential-free model-list URL to
  an extractor. `fetch_live_pricing_sync` serves the cost sync (no event loop);
  `_persist_provider_pricing` refreshes opportunistically when the dropdown opens. Source tags come
  from `cost_manager.LIVE_PRICING_PROVIDERS`. Units differ: OpenRouter is per token (×1M), Friendli
  is already per 1M.

---

## 11. Switches

| Setting | Env | Default | Governs |
|---|---|---|---|
| `RESILIENCE_ENVELOPE` | `TDA_RESILIENCE_ENVELOPE` | off in code, **on** in `docker-compose.yml` | §5 |
| `LLM_ATTEMPT_TIMEOUT_SECONDS` | `TDA_LLM_ATTEMPT_TIMEOUT_SECONDS` | 45 | per-attempt SDK timeout (Anthropic, Google), only with the envelope on |
| `LLM_CALL_TIMEOUT_SECONDS` / `LLM_STREAM_MAX_SECONDS` | — | 120 / 600 | silence limit / absolute bound of one call |
| `LLM_API_MAX_RETRIES` / `LLM_API_BASE_DELAY` | — | 5 / 2 s | retry budget and backoff base |
| `LLM_MAX_OUTPUT_TOKENS` | — | 16,384 | output ceiling stated on every call (100 for a session name) |
| `PROMPT_CACHING` | `TDA_PROMPT_CACHING` | see PROMPT_CACHE §4 | what the cache plan sends |
| `ANSWER_STREAMING` | `TDA_ANSWER_STREAMING` | off | whether the answer is streamed through a sink |
| `ATTACHMENT_SOURCES` | `TDA_ATTACHMENT_SOURCES` | off | `image_sources`: the platform reads attachments itself (§12) |
| — | `TDA_FAULT_INJECT` | off | §9; never set in production |
| profile `llmFallbackConfigurationId` | — | none | the signed fallback |
| profile `dualModelConfig` | — | none | separate strategic/tactical models (Optimize) |

---

## 12. Attachments

- `document_upload.py` decides how an upload reaches a model: `DocumentUploadConfig.PROVIDER_CAPABILITIES`
  (native full for Google, Anthropic, Bedrock Claude; vision-only for OpenAI/Azure vision models; text
  extraction for the rest; a provider not listed, such as XAI or MLX, gets text extraction).
  Admin overrides are stored in `document_upload_config`
  (`document_upload_config_manager.DocumentUploadConfigManager`,
  `/v1/admin/config/document-upload[/<provider>]`).
- `image_sources.py` (S9, behind `ATTACHMENT_SOURCES`) has the platform read an attachment itself
  (OCR on an image, tables/text from a PDF, CSV rows, text). What it reads becomes a verified source for
  the grounding gate and the compute component; a model's transcription is recorded unverified. The OCR
  and PDF engines are optional extras.

---

## 13. Adding a provider — checklist

1. `client_factory.create_llm_client`: a branch building the client from its credential keys (and
   `test_llm_credentials`).
2. `handler.call_llm_api`: if it is OpenAI-compatible, add it to the two OpenAI-compatible lists (the
   outer provider list and the `OpenAI/Azure/Friendli/…` branch); otherwise write a branch that reads
   usage through `cache_policy.read_usage`, sets `_finish`, records the cache reading, and raises
   `ProviderRefusal` on a refused finish.
3. `handler.list_models`: a branch (and the request mapping in `api/routes.py` `get_models`).
4. `langchain_adapter`: a `_create_<name>_llm`, the dispatch in `create_langchain_llm`, the credential
   key and env variable in `_load_credentials_for_provider`, and `get_supported_providers`.
5. `cache_policy.capability`: what the provider's cache does (PROMPT_CACHE §2).
6. `core/provider_colors.py`, the provider dropdown in `templates/index.html`, and the configuration
   dialog's credential fields.
7. Pricing: a row source in `cost_manager.ensure_price`; if the provider has a public pricing API, an
   entry in `_PROVIDER_PRICING_SPECS` and `LIVE_PRICING_PROVIDERS`.
8. Optional: `document_upload.PROVIDER_CAPABILITIES` if it accepts files natively.
9. Run the gates below with a fake client for the new branch; measure a real call before claiming caching
   or streaming works.

---

## 14. Files and gates

| File | Role |
|---|---|
| `llm/handler.py` | `call_llm_api` (every provider branch, retry loop, continuation, fallback), `list_models`, pricing specs, `OllamaClient`, placeholder materialisation |
| `llm/client_factory.py` | client construction, the pool, credential tests, `celeris_base_url` |
| `llm/langchain_adapter.py` | `create_langchain_llm`, per-provider LangChain classes, MLX limits, MCP tools for LangChain |
| `llm/resilience.py` · `llm/resilient_chat.py` | the recovery policy · the LangChain envelope |
| `core/provider_health.py` · `agent/resilience_envelope.py` | the breaker · the per-turn record, signed-fallback resolution, live step |
| `llm/streaming.py` · `llm/stream_usage.py` | provider streams, silence limits · one usage per LangChain stream |
| `llm/thinking_support.py` · `llm/response_text.py` | learned thinking refusal · answer text without reasoning |
| `llm/cache_policy.py` | the prompt cache plan ([PROMPT_CACHE_ARCHITECTURE.md](PROMPT_CACHE_ARCHITECTURE.md)) |
| `llm/lazy_sdk.py` · `llm/fault_injection.py` | lazy provider SDKs · the fire drill |
| `llm/document_upload.py` · `llm/document_upload_config_manager.py` · `llm/image_sources.py` | attachments |
| `agent/turn_failure.py` | the honest failure sentence |
| `agent/executor.py` | `_call_llm_and_update_tokens`, `_profile_llm_baseline`, `profile_llm_instance`, dual-model clients, `served_provider_model` |
| `agent/engines/optimize_engine.py` · `mcp_adapter/adapter.py` | the override overlay · `turn_llm`, `_overlay_map` |

Gates (CI-pure, in `test/trust_battery.sh`): `test_llm_resilience.py` (policy table, breaker, the
handler and LangChain envelope driven with the switch on), `test_resilience_baseline.py` (switch off ⇒
byte-identical), `test_resilience_ui.mjs`, `test_stream_usage.py`, `test_stream_activity_timeout.py`,
`test_turn_served_model.py`, `test_turn_llm_overlay.py`, `test_provider_globals_isolation.py`,
`test_google_thinking_support.py`, `test_thinking_budget_tristate.mjs`, `test_response_text.py`,
`test_lazy_imports.py`, `test_fault_injection.py`, `test_turn_failure.py`, `test_mlx_output_ceiling.py`,
`test_image_sources.py`, `test_prompt_cache_plan.py`. Provider-specific scripts outside the battery
(need credentials or a server): `test_bedrock_connectivity.py`, `test_bedrock_token_counting.py`,
`test_friendli_endpoint.py`, `test_document_upload_rest_api.py`.

---

## 15. Related documents

- [PROMPT_CACHE_ARCHITECTURE.md](PROMPT_CACHE_ARCHITECTURE.md): the cache plan the handler applies per provider.
- [COST_TRACKING_ARCHITECTURE.md](COST_TRACKING_ARCHITECTURE.md): pricing, `ensure_price`, the cost invariants (served model, one usage per stream).
- [ENGINE_MODULARIZATION_ARCHITECTURE.md](ENGINE_MODULARIZATION_ARCHITECTURE.md): which engine uses which lane.
- [FUSION_OPTIMIZER_ARCHITECTURE.md](FUSION_OPTIMIZER_ARCHITECTURE.md): the Optimize pipeline's planner and executor calls, dual-model planning.
- [ASSURANCE_INSTRUMENTS_ARCHITECTURE.md](ASSURANCE_INSTRUMENTS_ARCHITECTURE.md): the `llm_recovery` and `prompt_cache` points on SIGNED.
