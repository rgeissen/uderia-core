# src/trusted_data_agent/core/config.py
import os
import asyncio
from dotenv import load_dotenv

from trusted_data_agent.core.bounded_cache import BoundedLRUCache

load_dotenv()

# Bounds for caches that grow with load (per-user / per-config). Env-tunable.
_LLM_POOL_MAX = int(os.environ.get("TDA_LLM_POOL_MAX", "256"))
_TTS_CACHE_MAX = int(os.environ.get("TDA_TTS_CACHE_MAX", "256"))

class AppConfig:
    """
    Holds static configuration settings for the application.
    These values are typically set at startup and rarely change during runtime.
    """
    # --- Feature Flags & Behavior ---
    GITHUB_API_ENABLED = False # If True, enables GitHub API calls to fetch star count. Default is False to avoid rate limiting during development.
    CHARTING_ENABLED = True # Master switch to enable or disable the agent's ability to generate charts.
    DEFAULT_CHARTING_INTENSITY = "medium" # Controls how proactively the agent suggests charts. Options: "none", "medium", "heavy".
    ALLOW_SYNTHESIS_FROM_HISTORY = True # If True, allows the planner to generate an answer directly from conversation history without using tools.
    VOICE_CONVERSATION_ENABLED = True # Master switch for the Text-to-Speech (TTS) feature.
    SUB_PROMPT_FORCE_SUMMARY = False # If True, forces sub-executors for prompts to generate their own final summary. Default is False.
    ENABLE_SQL_CONSOLIDATION_REWRITE = True # If True, enables an LLM-based plan rewrite rule to consolidate sequential SQL queries.
    GRANTED_PROMPTS_FOR_EFFICIENCY_REPLANNING = ["base_teradataQuery"] # A list of complex prompts that are exempt from the "Re-planning for Efficiency" optimization.
    CONDENSE_SYSTEMPROMPT_HISTORY = True # If True, sends a condensed list of tools/prompts in the system prompt for subsequent turns in a conversation to save tokens.
    USE_CONTEXT_WINDOW_MANAGER = True # If True, uses the new Context Window Manager (system component) for budget-aware context assembly. If False, uses legacy inline assembly code paths.
    
    # GLOBAL APPLICATION STATE (not per-user) - loaded from environment or global settings file
    ENABLE_MCP_CLASSIFICATION = os.environ.get('TDA_ENABLE_MCP_CLASSIFICATION', 'true').lower() == 'true' # If True, uses LLM to classify MCP tools/prompts into categories. If False, uses single categories (stored keys 'All Tools', 'All Prompts', 'All Resources'; the panel shows them as "MCP Tools" etc. — ui.js:mcpCategoryLabel) for faster configuration. This is a GLOBAL setting affecting all users.


    # --- Database ---
    AUTH_DB_PATH = None  # Absolute path to tda_auth.db; set by init_database() at startup

    # --- Connection & Model State ---
    SERVICES_CONFIGURED = False # Master flag indicating if the core services (LLM, MCP) have been successfully configured.
    ACTIVE_PROVIDER = None
    ACTIVE_MODEL = None
    MCP_SERVER_CONNECTED = False # Runtime flag indicating if a connection to the MCP server is active.
    CHART_MCP_CONNECTED = False # Runtime flag indicating if a connection to the Charting server is active.
    CURRENT_PROVIDER = None # Stores the name of the currently configured LLM provider (e.g., "Google").
    CURRENT_MODEL = None # Stores the name of the currently configured LLM model (e.g., "gemini-1.5-flash").
    CURRENT_MCP_SERVER_ID = None # Stores the unique ID of the active MCP server (decoupled from name).
    CURRENT_AWS_REGION = None # Stores the AWS region, used specifically for the "Amazon" provider.
    CURRENT_AZURE_DEPLOYMENT_DETAILS = None # Stores Azure-specific details {endpoint, deployment_name, api_version}.
    
    # --- Profile Classification State ---
    CURRENT_PROFILE_ID = None # ID of the currently active profile
    CURRENT_PROFILE_CLASSIFICATION_MODE = None # Classification mode of current profile: 'light' or 'full'
    CURRENT_FRIENDLI_DETAILS = None # Stores Friendli.ai specific details {token, endpoint_url}.
    CURRENT_MODEL_PROVIDER_IN_PROFILE = None # For Amazon Bedrock, stores the model provider if using an inference profile ARN.

    # --- LLM & Agent Configuration ---
    # MCP_SYSTEM_NAME is now read dynamically from tda_config.json and global_parameters table
    LLM_API_MAX_RETRIES = 5 # The maximum number of times to retry a failed LLM API call.
    LLM_API_BASE_DELAY = 2 # The base delay in seconds for exponential backoff on API retries.
    CONTEXT_DISTILLATION_MAX_ROWS = 500 # The maximum number of rows from a tool's result to include in the LLM context.
    CONTEXT_DISTILLATION_MAX_CHARS = 10000 # The maximum number of characters from a tool's result to include in the LLM context.
    DETAILED_DESCRIPTION_THRESHOLD = 200 # A heuristic character count for the PlanExecutor to distinguish between a generic vs. a detailed task description from the planner.
    REPORT_DISTILLATION_MAX_ROWS = 100  # Report distillation Level 1: per-result-set row limit
    REPORT_DISTILLATION_MAX_CHARS = 50_000  # Report distillation Level 1: per-result-set character limit (~12,500 tokens)
    REPORT_DISTILLATION_TOTAL_BUDGET = 200_000  # Report distillation Level 2 trigger: total character budget (~50,000 tokens)
    REPORT_DISTILLATION_AGGRESSIVE_ROWS = 25  # Report distillation Level 2 reduction: aggressive row limit per result set

    # Document context limits
    DOCUMENT_CONTEXT_MAX_CHARS = 50_000  # Total character limit across all uploaded document attachments
    DOCUMENT_PER_FILE_MAX_CHARS = 20_000  # Per-document character truncation limit

    # Execution limits
    MAX_EXECUTION_DEPTH = 5  # Maximum nesting depth for delegated prompt execution
    MAX_PHASE_RETRY_ATTEMPTS = 5  # Maximum retry attempts per phase before LLM recovery

    # Langchain agent limits
    LANGCHAIN_MAX_ITERATIONS = 25  # Max tool invocations for conversation agent (LangGraph recursion_limit = 2x)

    # Knowledge chunking defaults
    KNOWLEDGE_CHUNK_SIZE = 1_000  # Default chunk size in characters for document segmentation
    KNOWLEDGE_CHUNK_OVERLAP = 200  # Overlap between consecutive document chunks

    # LLM output limits
    LLM_MAX_OUTPUT_TOKENS = 16_384  # Max output tokens for LLM generation calls

    # The Governed Stream (S1) — provisional answer text streamed to the UI while the
    # answer is generated, guarded in flight by the same PII detector the outbound
    # guardrail applies post-hoc, with the final answer staying authoritative. Master
    # switch; OFF by default ⇒ every provider takes its non-streaming API and every
    # payload is byte-identical to today. Env override TDA_ANSWER_STREAMING=1.
    # See docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S1.
    ANSWER_STREAMING = os.environ.get("TDA_ANSWER_STREAMING", "0").strip().lower() in ("1", "true", "yes", "on")
    ANSWER_STREAM_FLUSH_MS = 40   # delta coalescing window (one SSE event per window at most)

    # The Attested Cache (S2) — provider prompt caching of the STABLE PREFIX (system prompt +
    # history): explicit breakpoints on Anthropic-shaped providers, order-only elsewhere; the
    # cached share is read from every provider's usage and priced at its cached rate; a miss
    # on an unchanged prefix is an assurance finding. Master switch; OFF by default ⇒ no
    # cache_control reaches any provider and every request is byte-identical. Per-profile
    # opt-out + ttl in `cacheConfig`. Env override TDA_PROMPT_CACHING=1.
    # See docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S2.
    PROMPT_CACHING = os.environ.get("TDA_PROMPT_CACHING", "0").strip().lower() in ("1", "true", "yes", "on")

    # S3 — Compute with Lineage: the TDA_Compute component (exact, closed-set arithmetic over
    # the turn's result sets, in-process, never model-written code), the per-turn result-set
    # registry it reads, the receipts sealed into the provenance chain, and the number
    # lineage on the delivered answer (a figure traces to a compute receipt or a result row;
    # one that traces to neither is disclosed). Master switch; OFF by default ⇒ the component
    # is not served, nothing is recorded, no figure is marked — byte-identical. Env override
    # TDA_COMPUTE_LINEAGE=1. See docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S3.
    COMPUTE_LINEAGE = os.environ.get("TDA_COMPUTE_LINEAGE", "0").strip().lower() in ("1", "true", "yes", "on")
    # PROP-009: an exported evidence bundle carries an artefact's signed bytes up to this size;
    # larger content travels as its hash only (the export can ask for it explicitly).
    EVIDENCE_CONTENT_LIMIT_BYTES = int(os.environ.get("TDA_EVIDENCE_CONTENT_LIMIT_BYTES", str(256 * 1024)))
    # PROP-009 §4.6 key custody: "local" (default — the key file in tda_keys/, byte-identical)
    # or "command" (an HSM / cloud KMS / Vault behind EVIDENCE_SIGNER_COMMAND, which reads the
    # message on stdin and prints a base64 signature; EVIDENCE_SIGNER_PUBLIC_KEY is its PEM).
    EVIDENCE_SIGNER = os.environ.get("TDA_EVIDENCE_SIGNER", "local").strip().lower() or "local"
    EVIDENCE_SIGNER_COMMAND = os.environ.get("TDA_EVIDENCE_SIGNER_COMMAND", "")
    EVIDENCE_SIGNER_PUBLIC_KEY = os.environ.get("TDA_EVIDENCE_SIGNER_PUBLIC_KEY", "")
    EVIDENCE_SIGNER_TIMEOUT_SECONDS = float(os.environ.get("TDA_EVIDENCE_SIGNER_TIMEOUT_SECONDS", "10"))
    # PROP-009 §4.6 external anchoring of the evidence ledger: "" (off, default) · "file" ·
    # "webhook" · "rfc3161", published to EVIDENCE_ANCHOR_TARGET every interval.
    EVIDENCE_ANCHOR = os.environ.get("TDA_EVIDENCE_ANCHOR", "").strip().lower()
    EVIDENCE_ANCHOR_TARGET = os.environ.get("TDA_EVIDENCE_ANCHOR_TARGET", "")
    EVIDENCE_ANCHOR_TOKEN = os.environ.get("TDA_EVIDENCE_ANCHOR_TOKEN", "")
    EVIDENCE_ANCHOR_INTERVAL_SECONDS = int(os.environ.get("TDA_EVIDENCE_ANCHOR_INTERVAL_SECONDS", "3600"))

    # PROP-006 phase 1 — the combine step: `TDA_Compute` op "sql" runs ONE read-only SELECT over
    # the turn's result sets (in-process DuckDB, no files, no network) so results from DIFFERENT
    # data sources can be joined. Effective only under COMPUTE_LINEAGE (the result sets exist only
    # there). OFF by default ⇒ the op, its argument and its instruction text are not served —
    # byte-identical (test/test_compute_sql_baseline.py). Env override TDA_COMPUTE_SQL=1. See
    # docs/roadmap/MULTI_SOURCE_AGENTS_BUILD_BRIEF.md.
    COMPUTE_SQL = os.environ.get("TDA_COMPUTE_SQL", "0").strip().lower() in ("1", "true", "yes", "on")
    # PROP-006 phase 2, B10: a multi-server agent's call naming data the called MCP server cannot
    # read runs on the agent's server that can (agent/source_routing.py), named on the Server row.
    # Only agents with several MCP servers are affected. Env override TDA_MCP_SOURCE_ROUTING=0.
    MCP_SOURCE_ROUTING = os.environ.get("TDA_MCP_SOURCE_ROUTING", "1").strip().lower() not in ("0", "false", "no", "off")
    # Build brief A8 / decision 7: a combine over an INCOMPLETE input (the registry cut it, or the
    # source returned exactly its own row limit) is REFUSED by default, naming how to narrow the
    # query at its source; "disclose" combines and appends a disclosure written by code. Operator
    # policy — never a model decision.
    COMPUTE_SQL_INCOMPLETE = ("disclose" if os.environ.get("TDA_COMPUTE_SQL_INCOMPLETE", "refuse")
                              .strip().lower() == "disclose" else "refuse")
    # Build brief A13 / decision 8: DEPLOYMENT-WIDE isolation rules — sets of sources
    # (`mcp:<server>`, `conn:<id>`) whose results must never be combined. Admin-set (persisted in
    # system_settings, restored at startup); an agent can add its own (`combineIsolation` on the
    # profile) but never loosen these. [] = no rule.
    COMBINE_ISOLATION: list = []

    # S4 — The Resilience Envelope (docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S4). Off by
    # default ⇒ the LLM retry loop, the LangChain lanes and every persisted turn are byte-identical
    # to before. On: errors are classified (retry only what a retry can change, honour retry-after),
    # a truncated answer is continued (bounded), an exhausted primary is served by the profile's
    # SIGNED fallback (`llmFallbackConfigurationId`) and recorded, a provider refusal becomes an
    # honest answer, a per-provider circuit breaker routes to the fallback while the provider is
    # down — every act a recorded, sealed `llm_recovery` step. Env override TDA_RESILIENCE_ENVELOPE=1.
    RESILIENCE_ENVELOPE = os.environ.get("TDA_RESILIENCE_ENVELOPE", "0").strip().lower() in ("1", "true", "yes", "on")
    # S4 (review): the per-ATTEMPT timeout applied to the SDK clients the platform builds
    # (Anthropic, Google) while the envelope is ON — so a hung provider fails INSIDE the
    # attempt loop as a classifiable `timeout` (retry / fallback / breaker) instead of being
    # cancelled from outside by the executor's whole-call LLM_CALL_TIMEOUT_SECONDS, which the
    # envelope never sees. Off ⇒ the clients are built exactly as before.
    LLM_ATTEMPT_TIMEOUT_SECONDS = int(os.environ.get("TDA_LLM_ATTEMPT_TIMEOUT_SECONDS", "45") or 45)

    # S5 — The Visible Budget (docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S5). Off by default
    # ⇒ byte-identical. On: a profile's `turnBudget` {steps, tokens, seconds} is armed per turn; the
    # model reads what is left at every tool boundary, an exhausted unit becomes a recorded GRACEFUL
    # finish (the next tool call is refused, recovery-shaped; the answer says what is left), the
    # Live Status header shows the budget bar, the next-step dock offers "Continue where it stopped",
    # and `budget_adherence` is scored. The hard stops stay the last line, one unit above.
    # Env override TDA_TURN_BUDGET=1.
    TURN_BUDGET = os.environ.get("TDA_TURN_BUDGET", "0").strip().lower() in ("1", "true", "yes", "on")

    # S6 — The Governed Tool Surface has NO deployment switch (Oct 2026): the per-agent
    # setting `toolSurface.mode` (all | governed) is the only control — `all` is byte-identical
    # to a surface-less turn. See agent/tool_surface.py.

    # S7 — The Outcome Loop (docs/roadmap/BEYOND_CLAUDE_CONVERSATION_AGENT.md §S7). Off by
    # default ⇒ byte-identical. On: a profile with `outcomeConfig.enabled` gets ONE bounded
    # revision (maxRevisions ≤ 2) when the grounding gate's deterministic findings are ones the
    # model can fix (an unsupported claim, an out-of-scope reference, an ungrounded number); the
    # gate re-grades the revision (0 tokens); a clearing revision is delivered clean, a
    # non-clearing one delivers the honest annotated first draft. Both drafts are sealed.
    # Env override TDA_OUTCOME_LOOP=1.
    OUTCOME_LOOP = os.environ.get("TDA_OUTCOME_LOOP", "0").strip().lower() in ("1", "true", "yes", "on")

    # S8 — Regenerate as an Experiment: a regenerate is a SCORED SIBLING turn under the same user
    # message (compared by the Scorecard's bands, one adopted — append-only, the other kept); an
    # edit-and-resend is a BRANCH (the record keeps every turn, the view follows the active
    # branch). Off ⇒ no sibling/branch keys are ever written and the endpoints refuse.
    # Env override TDA_TURN_LINEAGE=1.
    TURN_LINEAGE = os.environ.get("TDA_TURN_LINEAGE", "0").strip().lower() in ("1", "true", "yes", "on")

    # S9 — Grounded Multimodal: an attachment is a SOURCE, not a prompt. On: every upload is
    # READ by the platform where it can be read deterministically (table OCR on an image,
    # tables/text from a PDF, a CSV's rows, a text file) and what it read becomes a verified
    # source — grounding text the gate checks the answer against, rows the compute component
    # can operate on, a citation with its region. A model transcription (the optional vision
    # fallback, per-profile `attachmentConfig.transcribeImages`) is recorded UNVERIFIED and
    # never enters the grounding text. Off ⇒ uploads, document context, grounding and the
    # persisted turn are byte-identical. Engines are optional extras (rapidocr-onnxruntime,
    # pdfplumber). Env override TDA_ATTACHMENT_SOURCES=1.
    ATTACHMENT_SOURCES = os.environ.get("TDA_ATTACHMENT_SOURCES", "0").strip().lower() in ("1", "true", "yes", "on")

    # S10 — Steer & Reattach: the composer stays live during a turn (a steer is queued and read
    # by the model with its next step — the next tool result on the tool lanes, the next
    # tactical decision / final report on Optimize; one left waiting is delivered at the start
    # of the next turn), and the UI stream is cursor-stamped + logged so a lost connection or a
    # closed tab never loses the VIEW of a running turn (the turn itself never stopped). Off ⇒
    # no cursor, no log, no steer endpoints, the composer disabled while a turn runs — byte-
    # identical. Env override TDA_STEER_REATTACH=1.
    STEER_REATTACH = os.environ.get("TDA_STEER_REATTACH", "0").strip().lower() in ("1", "true", "yes", "on")

    # LLM call timeout — prevents indefinite hangs when a provider is overloaded or unresponsive.
    # Set to 0 to disable. Applies to every _call_llm_and_update_tokens() invocation.
    # It is a limit on SILENCE: a call that streams nothing is cut this many seconds after it
    # started; a streamed answer is cut this many seconds after the provider's last chunk.
    LLM_CALL_TIMEOUT_SECONDS = 120
    # Absolute bound on one streamed LLM call that never falls silent. 0 = no bound.
    LLM_STREAM_MAX_SECONDS = 600
    # Price of a provider-cached input token relative to a fresh one (Gemini implicit
    # caching bills cached prefix tokens at 25% of the input price). Applied only when
    # the provider reports cached tokens on a response.
    CACHED_INPUT_PRICE_FACTOR = 0.25

    SQL_OPTIMIZATION_PROMPTS = []
    SQL_OPTIMIZATION_TOOLS = ["base_readQuery"]

    # RAG Configuration
    RAG_ENABLED = True
    RAG_REFRESH_ON_STARTUP = True # If True, vector store is refreshed on startup. If False, it uses cache until manually refreshed.
    RAG_CASES_DIR = "rag/tda_rag_cases"
    RAG_PERSIST_DIR = ".chromadb_rag_cache"
    RAG_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    RAG_NUM_EXAMPLES = 3 # Total number of few-shot examples to retrieve across all active collections
    RAG_DEFAULT_COLLECTION_NAME = "default_collection" # ChromaDB collection name for default collection (ID 0)
    AUTOCOMPLETE_MIN_RELEVANCE = 0.40  # Minimum cosine similarity for autocomplete suggestions (0.0-1.0)

    # Next-Step Recommender (ephemeral, token-light follow-up suggestions)
    RECOMMENDER_ENABLED = True  # Global admin gate. When False, no profile can emit next-step suggestions.
    # Upper bound on the recommender's Tier B champion-case retrieval (awaited in the turn path).
    RECOMMENDER_TIER_B_TIMEOUT_SECONDS = 5.0

    # Grounding gate (Tier 1.1) — synchronous, deterministic enforcement of KG-declared
    # scope on the agent's answer (A2/T6/P2). Global master switch; OFF by default so the
    # baseline is untouched until an admin opts in. Even when ON, the per-profile mode
    # defaults to `observe` (measure + emit, never alter the answer), so enabling it
    # globally stays answer-non-regressive. See docs/roadmap/TIER_1.1_GROUNDING_GATE.md.
    GROUNDING_GATE_ENABLED = False  # Global admin gate. When False, the gate never runs.

    # Tier 1.3 P2 guardrails — global admin gate for the outbound PII/toxicity guardrail
    # (and, later, inbound prompt-injection). When False the guardrail never runs (answers
    # byte-identical). Even when ON, the per-profile mode defaults to `observe` (detect +
    # emit, never alter), so enabling it globally stays answer-non-regressive. Deterministic,
    # 0-token. See docs/roadmap/TIER_1.3_GUARDRAILS.md.
    GUARDRAILS_ENABLED = False  # Global admin gate. When False, guardrails never run.

    # The OBDA POLICY — one per plane, the "contracts only" posture of a deployment
    # (docs/Architecture/OBDA_ARCHITECTURE.md §0a). Availability is the component's (below);
    # the policy is what the deployment REQUIRES of every agent once a plane exists:
    #   off     — each agent's own authority decides (the baseline);
    #   observe — nothing refused; every call the policy WOULD refuse is recorded as a
    #             `policy_observed` trust event (see which agents would break before enforcing);
    #   enforce — reads only through signed data products (read), model changes only through
    #             proposals a person publishes (model), row changes only through signed
    #             actions and no command tools (act) — for every agent on every origin,
    #             interactive turns included. Applied ONCE at `agent_authority.derive_principal`.
    # Persisted in `system_settings.agent_eval.obda_policy`; absent ⇒ every plane off.
    OBDA_POLICY = {"read": "off", "model": "off", "act": "off"}

    # PROP-011 — needs input. The deployment's floor on the code raisers' posture: `enforce`
    # (default: each agent's `inputPolicy.raisers` decides) or `observe` (every raiser records
    # only, deployment-wide — the rollout dial). Expiry of an unanswered question, in hours,
    # interactive vs unattended (scheduler/flow/ask). `INPUT_REQUIRED_STATE_DEFAULT`: whether
    # REST clients that did not declare `X-Uderia-Accepts: input_required` see the new task
    # state (False ⇒ they see `complete` with `input_required: true` beside the question).
    INPUT_RAISERS_FLOOR = "enforce"
    INPUT_EXPIRY_INTERACTIVE_HOURS = 168
    INPUT_EXPIRY_UNATTENDED_HOURS = 72
    INPUT_REQUIRED_STATE_DEFAULT = False

    # The OBDA planes (OBDA-READ · OBDA-MODEL · OBDA-ACT) have NO master switch here: each is a
    # builtin component (obda_read · obda_model · action) and its availability is the component
    # chain — the admin pane's global gate, the per-user override, the agent's own toggle
    # (core/obda_planes). The former OBDA_ENABLED / ACT_ENABLED (TDA_ACT) switches were
    # migrated into component availability once at startup (auth/database._migrate_obda_masters).

    # Retention default for FULL withholdings under plain `enforce` (the review/enforce
    # consolidation): a grounding BLOCK or an outbound toxicity BLOCK parks its original
    # in the review queue as `retained` — recoverable from the Assurance Inbox, but never
    # counted as owed human work (that stays `review` mode's meaning). Routine PII
    # REDACTs never auto-park (the parked content would be the raw PII). Set False for
    # data-minimization regimes that must not store refused content at all.
    REVIEW_RETAIN_ENFORCED_BLOCKS = True

    # Preflight SQL schema grounding — before a raw-SQL tool executes, validate its FROM/JOIN
    # database qualifiers against the session's AUTHORITATIVE catalog (cached from a
    # base_databaseList(scope=all) result). A reference PROVABLY absent from a complete catalog
    # is a hallucinated database. Modes: 'off' | 'observe' (default — log only, execute anyway,
    # byte-identical baseline) | 'enforce' (short-circuit with a recovery-shaped error before
    # the DB round-trip). Regression-safe by construction: with no cached catalog it is a no-op,
    # and a valid database is always present in a complete catalog (no false positives).
    PREFLIGHT_SQL_MODE = "observe"
    # Preflight JOIN-key check (Sep 2026): a JOIN between two tables whose key the bound
    # ontology declares, on columns that are not that key, is the runaway-join class (a
    # near-cartesian product ran three hours on production). 'observe' logs; 'enforce'
    # refuses the call with a recovery-shaped error naming the declared key. Fail-open by
    # construction: no declared keys for the session, an unparseable statement or an
    # undeclared pair → no verdict. Env override TDA_PREFLIGHT_JOIN_MODE.
    PREFLIGHT_JOIN_MODE = os.environ.get("TDA_PREFLIGHT_JOIN_MODE", "observe")
    # Preflight text-date check (Sep 2026): a DATE the product deploys as characters,
    # compared as a string, ordered bare, or bucketed with SUBSTR, is the lexical-date
    # class (a "trend" of thirty arbitrary days ran on production). 'observe' logs;
    # 'enforce' refuses the call with a recovery-shaped error naming the column, its
    # sampled shape and the CAST … FORMAT rewrite — or, when the shape is not yet
    # sampled, the one probe that settles it. Fail-open by construction: no recorded
    # columns for the session, an unparseable statement → no verdict. Env override
    # TDA_PREFLIGHT_TEXT_DATE_MODE.
    PREFLIGHT_TEXT_DATE_MODE = os.environ.get("TDA_PREFLIGHT_TEXT_DATE_MODE", "observe")
    # Preflight value-existence check: a literal compared for equality against a column
    # whose DECLARED value domain does not contain it (a FIPS code on a UCR-coded column,
    # a city name on a Yes/No label) is refused under `enforce`; and an EMPTY result whose
    # filter rests on an UNVERIFIED literal (no domain, never seen in a row) is annotated
    # so a zero is not reported as an absence. Off / observe (log only, default) /
    # enforce. Env override TDA_PREFLIGHT_VALUE_MODE.
    PREFLIGHT_VALUE_MODE = os.environ.get("TDA_PREFLIGHT_VALUE_MODE", "observe")
    # Preflight COLUMN-existence check (agent/preflight/columns.py): a column the SQL
    # attributes to a table whose COMPLETE column list the KG enrichment recorded, and
    # which the table does not have, is refused under enforce naming the real columns
    # and the closest matches (the hallucinated-column class — `boarding_event.miles_flown`
    # cost a DB round trip + an LLM round + a SELECT * to discover the schema the graph
    # already held). Recorded only for column-complete graphs (industry / declared /
    # discovery — never ANDP); anything the parse cannot attribute → no verdict.
    # Env override TDA_PREFLIGHT_COLUMN_MODE.
    PREFLIGHT_COLUMN_MODE = os.environ.get("TDA_PREFLIGHT_COLUMN_MODE", "observe")
    # Preflight DECIMAL-division warning (agent/preflight/decimals.py): Teradata gives a
    # decimal division the scale of its operands, so `7/100.0` returns 0.1 and `1.0/3` 0.3 —
    # silently. A WARNING, never a block or a rewrite (the fix changes the result): under
    # `enforce` a note naming the expression is attached to the statement's result so the
    # model can rerun with a FLOAT operand; under `observe` (default) it is logged and
    # tallied only. Teradata statements only. Env override TDA_PREFLIGHT_DECIMAL_MODE.
    PREFLIGHT_DECIMAL_MODE = os.environ.get("TDA_PREFLIGHT_DECIMAL_MODE", "observe")

    # Online evaluation (WS3 W3.4) — automatic quality scoring of live turns + flow
    # runs, feeding the Agent Performance roster. Deterministic scoring is free and
    # non-blocking, so when enabled it scores EVERY turn/run. Never blocks/mutates.
    ONLINE_EVAL_ENABLED = True             # Score every executed turn / flow run (deterministic).
    # Only the LLM-judge layer is stochastic — it calls an LLM panel (token cost),
    # so it runs on anomalous turns + a fraction of the rest, independent of the
    # always-on deterministic scoring. OFF by default. (Manual "Score this turn"
    # and the offline CLI keep their own judges toggle.)
    ONLINE_EVAL_INCLUDE_JUDGES = False     # Add the judge panel to automatic scoring (costs tokens).
    ONLINE_EVAL_JUDGE_SAMPLE_RATE = 0.05   # Fraction of turns to ALSO judge (1.0 = judge every turn).
    ONLINE_EVAL_ALWAYS_ANOMALOUS = True    # Always judge turns that errored / self-corrected heavily.
    ONLINE_EVAL_SINK = "tda_eval/online_eval.jsonl"  # OTLP-shaped quality-annotated span sink.
    # PROP-002b — per-station Assurance reporting levels ('all' | 'warning' | 'danger'),
    # operator-assigned from the ONE severity construct (assurance_points.SEVERITY_CONSTRUCT).
    # Empty = every station at the default ('warning'). Danger ALWAYS surfaces (the floor).
    # Presentation, never creation: below-level items are recorded, just not badged/folded.
    ASSURANCE_STATION_REPORTING = {}
    # The online hook is dispatched as a background task (never blocks turn completion);
    # these bound it so a stalled provider can neither hang a turn nor leak a coroutine.
    ONLINE_EVAL_TIMEOUT_SECONDS = 25       # hard cap on the backgrounded online-eval per turn (judges)
    ONLINE_EVAL_JUDGE_CALL_TIMEOUT_SECONDS = 20  # per-judge LLM call timeout (a hung judge → abstain)
    # The evidence a RAG judge is shown. Whole chunks in retrieval order up to this many
    # characters; a judge that was handed `docs[:8]` / 5000 chars ruled two terms "entirely
    # absent" that sat in chunk 9 (prod 727292c3, Oct 2026). Sized for a modern context
    # window (~6K tokens); an unavoidable truncation is STATED to the judge.
    ONLINE_EVAL_JUDGE_EVIDENCE_CHARS = 24000
    ONLINE_EVAL_JUDGE_ANSWER_CHARS = 12000  # the answer under judgment — never silently cut
    # Admin-set global default thresholds for live/manual scoring. Threshold-bearing
    # scorers (cost, latency, self_correction, tool_success_rate, …) abstain without
    # a target; these make them band/PASS/FAIL on live turns. Empty = abstain. A
    # curated eval case's own assertions always override these. Admin-controlled at
    # runtime via /v1/admin/app-config (agent_eval.default_thresholds).
    #
    # 3-band (Healthy/Review/Danger) shape per metric — two optional cut points:
    #   {"max_cost_usd": {"healthy": 0.30, "danger": 0.50},   # Healthy ≤0.30, Danger >0.50
    #    "min_tool_success_rate": {"healthy": 0.95, "danger": 0.80}}  # Healthy ≥0.95, Danger <0.80
    # Review (amber) is the band between the two — it PASSES the gate but is flagged;
    # only Danger (red) fails. A bare scalar is treated as a legacy single Danger line.
    ONLINE_EVAL_DEFAULT_THRESHOLDS = {
        # Scope/efficiency validations — seeded so they BAND live traffic out of the
        # box (every other metric stays unset = measure-don't-band until an admin tunes
        # the sliders). Both scorers are non-gating: a red band flags the turn on the
        # Quality card but never fails a release gate — observe, don't constrain.
        "max_out_of_scope_dbs": {"danger": 1, "healthy": 0},   # 0 in-scope=green, 1=amber, >1=red
        "max_tool_calls":       {"danger": 14, "healthy": 7},  # <=7 green, 8-14 amber, >14 red
        # Citation provenance (F-001). Seeded for the same reason as the two above and
        # one more: it CANNOT affect a deployment that does not use progressive skills —
        # the scorer abstains when the agent has no library, so the band only ever
        # appears where the question exists. Unseeded it would measure nothing until
        # someone thought to tune a slider for a defect they had not met yet.
        "min_citation_fidelity": {"danger": 0.75, "healthy": 1.0},  # 1.0 green, <0.75 red
    }

    # Behavioral Trust Score (Trust & Assurance) — fuses Trust-Membrane integrity
    # (signed/changed/unsigned) with the live eval track record into one composite
    # trust state per agent (provisional/at_risk/watch/proven/trusted). Pure
    # read-side observability: computing it never gates or alters a turn. The
    # `enabled` flag governs only whether the UI surfaces (roster ring, badges)
    # show it — OFF by default so the baseline UI is untouched until an admin
    # opts in. Keys mirror eval/trust_behavior.py DEFAULTS; admin-controlled at
    # runtime via /v1/admin/app-config (trust_behavior block), DB-persisted.
    TRUST_BEHAVIOR = {
        "enabled": False,
        "window_turns": 50,
        "min_turns_proven": 10,
        "min_turns_trusted": 25,
        "healthy_floor": 0.80,
        "trusted_floor": 0.90,
        "at_risk_floor": 0.50,
        "half_life_days": 14.0,
        "stale_days": 30.0,
        # Circuit breaker: "off" (default — pure observability) | "notify" (alert
        # the owner when an agent enters at_risk) | "freeze_autonomy" (also suspend
        # the agent's UNATTENDED use via Agent Authority until a human reviews;
        # interactive use is never touched). Opt-in enforcement, fail-safe.
        "breaker_mode": "off",
    }

    # Universal vote signal (docs/roadmap/VOTE_SIGNAL_PLAN.md) — the user's per-turn
    # upvote/downvote, turned into a learning signal for every engine. All consumers
    # run behind ONE dispatcher (agent/feedback/) and are deterministic + 0 tokens,
    # EXCEPT `judge_on_downvote`, which spends tokens and is therefore OFF by default.
    # A vote is a PREFERENCE, never a measurement: no consumer may fold it into a pass
    # rate, a quality band or a trust composite. `enabled` False = pre-feature baseline.
    FEEDBACK_SIGNALS = {
        "enabled": True,              # master: the dispatcher runs at all
        "annotate_eval": True,        # record the vote as a human label for judge/threshold calibration
        "champion_upvote": True,      # an upvote promotes the turn's RAG case (the downvote half pre-dates this)
        "genie_propagation": True,    # a coordinator vote reaches its single expert
        "retrieval_feedback": True,   # record per-document feedback for Corpus Health (never reranks)
        "memory_deactivate": True,    # a downvote deactivates (never deletes) facts auto-learned from that turn
        "judge_on_downvote": False,   # OPT-IN: run the LLM judges on a downvoted turn (costs tokens)
    }

    # Knowledge Repository Configuration (Knowledge Repositories = Domain Knowledge RAG)
    KNOWLEDGE_RAG_ENABLED = True # Master switch for knowledge repository retrieval during planning
    # Semantic seeding for KG retrieval: embed each entity's label+description and union
    # the query's nearest entities into the lexical candidate pool, so a question phrased
    # in business vocabulary still reaches the right node ("customers" → an entity called
    # `Party`). ON by default — the search is one float32 matmul (0.08 ms / 6 MB on a
    # 4k-entity graph) and a cold graph degrades to lexical rather than blocking a turn.
    # Set TDA_KG_SEMANTIC_SEEDING=false to run lexical-only.
    KG_SEMANTIC_SEEDING = os.environ.get('TDA_KG_SEMANTIC_SEEDING', 'true').lower() == 'true'
    # Where the per-graph embedding matrices are persisted so a restart re-loads instead
    # of re-embedding. Empty string disables persistence (memory-only, rebuilt per process).
    KG_SEMANTIC_INDEX_DIR = os.environ.get('TDA_KG_SEMANTIC_INDEX_DIR', '.cache/kg_semantic')
    KNOWLEDGE_RAG_NUM_DOCS = 3 # Number of knowledge documents to retrieve per planning call
    KNOWLEDGE_MIN_RELEVANCE_SCORE = 0.30  # Lowered for testing - may need better embeddings # Minimum similarity score for knowledge document retrieval (0.0-1.0)
    KNOWLEDGE_MAX_TOKENS = 2000 # Maximum tokens for all knowledge context combined
    KNOWLEDGE_RERANKING_ENABLED = False # Global default for LLM reranking (can be overridden per collection in profiles)
    KNOWLEDGE_MAX_CHUNKS_PER_DOC = 0 # 0 = disabled (no per-document dedup). Limits chunks from same source document.
    KNOWLEDGE_FRESHNESS_WEIGHT = 0.0 # 0.0 = disabled (pure relevance). Blend: (1-w)*similarity + w*freshness
    KNOWLEDGE_FRESHNESS_DECAY_RATE = 0.005 # Exponential decay rate for freshness scoring. Higher = faster decay.
    
    # Session & Analytics Configuration
    SESSIONS_FILTER_BY_USER = os.environ.get('TDA_SESSIONS_FILTER_BY_USER', 'true').lower() == 'true' # If True, execution dashboard shows only current user's sessions. If False, shows all sessions. Note: User tier always filtered, Developer+ can override.


    # --- Initial State Configuration ---
    # Note: INITIALLY_DISABLED_PROMPTS and INITIALLY_DISABLED_TOOLS have been moved to tda_config.json
    # Each MCP server configuration now contains "initially_disabled_tools" and "initially_disabled_prompts" arrays
    # Use config_manager.get_initially_disabled_tools() and config_manager.get_initially_disabled_prompts() to access them

    # --- Tool & Argument Parsing Logic ---
    TOOL_SCOPE_HIERARCHY = [
        ('column', {'database_name', 'object_name', 'column_name'}),
        ('table', {'database_name', 'object_name'}),
        ('database', {'database_name'}),
    ]
    ARGUMENT_SYNONYM_MAP = {
        'database_name': {
            'database_name', 'db_name', 'DatabaseName'
        },
        'object_name':   {
            'table_name', 'tablename', 'TableName',
            'object_name', 'obj_name', 'ObjectName', 'objectname',
            'view_name', 'viewname', 'ViewName'
        },
        'column_name':   {
            'column_name', 'col_name', 'ColumnName'
        },
    }

APP_CONFIG = AppConfig()


def sessions_filtered_by_user() -> bool:
    """Whether a session read is limited to the caller's own sessions: the deployment's
    SESSIONS_FILTER_BY_USER switch — or, whatever the switch says, a request an agent makes
    through TDA_Platform (marked by the auth middleware as ``g.agent_operation``). An agent never
    reads other users' conversations because a deployment chose to let people do so."""
    if APP_CONFIG.SESSIONS_FILTER_BY_USER:
        return True
    try:
        from quart import g, has_request_context
        return bool(has_request_context() and getattr(g, "agent_operation", None))
    except Exception:  # noqa: BLE001 — no request context: the deployment's switch decides
        return False


def resolved_thresholds() -> dict:
    """The admin threshold map, flattened to the scalars every consumer reads.

    ``ONLINE_EVAL_DEFAULT_THRESHOLDS`` stores each metric as either a scalar (legacy,
    = the Danger/gate line alone) or ``{"danger": x, "healthy": y}`` (the 3-band model).
    Flattened here to::

        <key>            the Danger boundary — the gate/fail line
        <key>__healthy   the Healthy boundary — the green/amber split

    ONE flattener, beside the value it reads, because there are TWO consumers with very
    different jobs and they must resolve the same admin key to the same number: the
    SCORERS (which need both cut points, to band) and the GROUNDING GATE (which needs
    only the Danger line, to decide). The gate used to be handed this map RAW — so
    ``_as_float({"danger": 0.7, ...})`` raised, the failure was swallowed, and the gate
    silently kept its hardcoded default while the Scorecard used the admin's value. One
    setting, two consumers, two answers — with nothing on screen to show the split.

    Neither `eval` nor `agent` owns this map; `core.config` does, which is why the
    reader lives here rather than in either consumer.
    """
    raw = getattr(APP_CONFIG, "ONLINE_EVAL_DEFAULT_THRESHOLDS", None)
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, val in raw.items():
        if isinstance(val, dict):
            danger, healthy = val.get("danger"), val.get("healthy")
            if danger is not None:
                out[key] = danger
            if healthy is not None:
                out[key + "__healthy"] = healthy
        elif val is not None:
            out[key] = val          # legacy scalar = single Danger/gate line
    return out

APP_STATE = {
    # Live client instances and server configurations
    "llm": None, 
    "mcp_client": None, 
    "server_configs": {},

    # Raw tool/prompt definitions loaded from the MCP server
    "mcp_tools": {}, 
    "mcp_prompts": {}, 
    "mcp_charts": {},

    # Processed and categorized structures for UI display
    "structured_tools": {}, 
    "structured_prompts": {}, 
    "structured_resources": {}, 
    "structured_charts": {},

    # Cache for inferred tool operational scopes (e.g., 'table', 'column')
    "tool_scopes": {},

    # Formatted context strings injected into LLM prompts
    "tools_context": "--- No Tools Available ---", 
    "prompts_context": "--- No Prompts Available ---", 
    "charts_context": "--- No Charts Available ---",
    "constraints_context": "",

    # Runtime lists of currently disabled capabilities
    # These are populated from tda_config.json at startup via config_manager
    "disabled_prompts": [],
    "disabled_tools": [],

    # Validated license information
    "license_info": None,

    # Asynchronous task tracking for the REST API
    "background_tasks": {},
    
    # --- MODIFICATION START: Add RAG queue and instance placeholder ---
    # Asynchronous RAG processing queue and singleton instance
    "rag_processing_queue": asyncio.Queue(),
    "rag_retriever_instance": None,
    # RAG collections configuration: list of collection metadata
    # Each collection: {id, name, collection_name, mcp_server_id, enabled, created_at, description}
    "rag_collections": [],
    # --- MODIFICATION END ---

    # Concurrency lock for the configuration process
    "configuration_lock": asyncio.Lock(),

    # === CACHING LAYER (Performance Optimization) ===
    # Cache MCP tool/prompt/resource schemas by server_id (5 minute TTL)
    "mcp_tool_schema_cache": {},  # {server_id: {tools, prompts, resources, timestamp, tool_count}}

    # Connection pooling for MCP clients (keyed by server_id)
    "mcp_client_pool": {},  # {server_id: MultiServerMCPClient instance}

    # LLM instance pooling by (provider, model, credentials_hash, user_uuid).
    # Bounded LRU: grows with users — cap prevents unbounded memory. Evicted
    # clients are simply dropped (in-flight callers keep their own reference;
    # GC reclaims), never force-closed.
    "llm_instance_pool": BoundedLRUCache(max_size=_LLM_POOL_MAX, name="llm_instance_pool"),

    # TTS client caching (managed by tts_service.py)
    "tts_client_global": None,       # Cached global TTS client (for 'global' mode)
    "tts_clients_by_user": BoundedLRUCache(max_size=_TTS_CACHE_MAX, name="tts_clients_by_user"),  # Per-user (bounded LRU)

    # Fine-grained lock for pool mutations (Phase 1 scaling)
    "_pool_lock": asyncio.Lock(),
}

# Note: Recommended models are now stored in the database (recommended_models table)
# and bootstrapped from tda_config.json. See auth/database.py:_bootstrap_recommended_models()

# ==============================================================================
# USER CONFIGURATION HELPERS
# ==============================================================================
# Per-user runtime context removed - all configuration now stored in database


def get_user_provider(user_uuid: str) -> str:
    """
    Get current provider for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        Provider name (e.g., "Google", "Anthropic")

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_provider")
    return APP_STATE.get("current_provider_by_user", {}).get(user_uuid)

def set_user_provider(provider: str, user_uuid: str):
    """
    Set current provider for user.

    Args:
        provider: Provider name
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_provider")
    if "current_provider_by_user" not in APP_STATE:
        APP_STATE["current_provider_by_user"] = {}
    APP_STATE["current_provider_by_user"][user_uuid] = provider
    # Also update APP_CONFIG for backwards compatibility with status checks
    APP_CONFIG.CURRENT_PROVIDER = provider


def get_user_model(user_uuid: str) -> str:
    """
    Get current model for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        Model name (e.g., "gemini-2.5-flash", "claude-3-5-haiku")

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_model")
    return APP_STATE.get("current_model_by_user", {}).get(user_uuid)


def set_user_model(model: str, user_uuid: str):
    """
    Set current model for user.

    Args:
        model: Model name
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_model")
    if "current_model_by_user" not in APP_STATE:
        APP_STATE["current_model_by_user"] = {}
    APP_STATE["current_model_by_user"][user_uuid] = model
    # Also update APP_CONFIG for backwards compatibility with status checks
    APP_CONFIG.CURRENT_MODEL = model


def get_user_mcp_server_id(user_uuid: str) -> str:
    """
    Get current MCP server ID for user.

    Validates that the server ID actually exists in the MCP client.
    If invalid, automatically falls back to the first available server.

    Args:
        user_uuid: User UUID (required)

    Returns:
        MCP server ID

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_mcp_server_id")

    server_id = APP_STATE.get("current_server_id_by_user", {}).get(user_uuid)

    # VALIDATION — against the USER'S OWN configured servers, never the process-global
    # ``APP_STATE['mcp_client']``. That client holds whichever server the LAST user to
    # activate a profile connected, so checking a per-user id against it and "auto-correcting"
    # to the client's first server rerouted user A's tool calls to user B's server whenever the
    # two ran concurrently (prod 2026-09-20: an Optimize turn's base_databaseList landed on
    # another account's policing endpoint, and the correct per-turn overlay client then raised
    # "couldn't find server"). A per-user id may only ever be replaced by another server the
    # SAME user configured; an id absent from the user's config is genuinely stale and falls
    # back to that user's first configured server. Any failure keeps the stored id.
    if server_id:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            own = [srv.get("id") for srv in (get_config_manager().get_mcp_servers(user_uuid) or [])]
            if own and server_id not in own:
                import logging
                logger = logging.getLogger("quart.app")
                logger.error(
                    f"⚠️ STALE MCP SERVER ID DETECTED: user {user_uuid} references server '{server_id}' "
                    f"which is not in their configuration. Their servers: {own}"
                )
                fallback_server = own[0]
                logger.warning(f"🔄 Auto-correcting to the user's own server: {fallback_server}")
                APP_STATE["current_server_id_by_user"][user_uuid] = fallback_server
                return fallback_server
        except Exception as e:
            import logging
            logging.getLogger("quart.app").warning(f"Failed to validate MCP server ID: {e}")

    return server_id


def set_user_mcp_server_id(server_id: str, user_uuid: str):
    """
    Set current MCP server ID for user.

    Args:
        server_id: MCP server ID
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_mcp_server_id")
    if "current_server_id_by_user" not in APP_STATE:
        APP_STATE["current_server_id_by_user"] = {}
    APP_STATE["current_server_id_by_user"][user_uuid] = server_id
    # Also update APP_CONFIG for backwards compatibility with status checks
    APP_CONFIG.CURRENT_MCP_SERVER_ID = server_id


def get_user_aws_region(user_uuid: str) -> str:
    """
    Get AWS region for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        AWS region

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_aws_region")
    return APP_STATE.get("current_aws_region_by_user", {}).get(user_uuid)


def set_user_aws_region(region: str, user_uuid: str):
    """
    Set AWS region for user.

    Args:
        region: AWS region
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_aws_region")
    if "current_aws_region_by_user" not in APP_STATE:
        APP_STATE["current_aws_region_by_user"] = {}
    APP_STATE["current_aws_region_by_user"][user_uuid] = region
    # Also update APP_CONFIG for backwards compatibility
    APP_CONFIG.CURRENT_AWS_REGION = region


def get_user_azure_deployment_details(user_uuid: str) -> dict:
    """
    Get Azure deployment details for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        Azure deployment details dict

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_azure_deployment_details")
    return APP_STATE.get("current_azure_details_by_user", {}).get(user_uuid)


def set_user_azure_deployment_details(details: dict, user_uuid: str):
    """
    Set Azure deployment details for user.

    Args:
        details: Azure deployment details dict
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_azure_deployment_details")
    if "current_azure_details_by_user" not in APP_STATE:
        APP_STATE["current_azure_details_by_user"] = {}
    APP_STATE["current_azure_details_by_user"][user_uuid] = details
    # Also update APP_CONFIG for backwards compatibility
    APP_CONFIG.CURRENT_AZURE_DEPLOYMENT_DETAILS = details


def get_user_friendli_details(user_uuid: str) -> dict:
    """
    Get Friendli.ai details for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        Friendli.ai details dict

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_friendli_details")
    return APP_STATE.get("current_friendli_details_by_user", {}).get(user_uuid)


def set_user_friendli_details(details: dict, user_uuid: str):
    """
    Set Friendli.ai details for user.

    Args:
        details: Friendli.ai details dict
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_friendli_details")
    if "current_friendli_details_by_user" not in APP_STATE:
        APP_STATE["current_friendli_details_by_user"] = {}
    APP_STATE["current_friendli_details_by_user"][user_uuid] = details
    # Also update APP_CONFIG for backwards compatibility
    APP_CONFIG.CURRENT_FRIENDLI_DETAILS = details


def get_user_model_provider_in_profile(user_uuid: str) -> str:
    """
    Get model provider in profile (for AWS Bedrock) for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        Model provider string

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_model_provider_in_profile")
    return APP_STATE.get("current_model_provider_by_user", {}).get(user_uuid)


def set_user_model_provider_in_profile(provider: str, user_uuid: str):
    """
    Set model provider in profile (for AWS Bedrock) for user.

    Args:
        provider: Model provider string
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_model_provider_in_profile")
    if "current_model_provider_by_user" not in APP_STATE:
        APP_STATE["current_model_provider_by_user"] = {}
    APP_STATE["current_model_provider_by_user"][user_uuid] = provider
    # Also update APP_CONFIG for backwards compatibility
    APP_CONFIG.CURRENT_MODEL_PROVIDER_IN_PROFILE = provider


# Finished REST/dashboard task records are kept for polling (GET /v1/tasks/<id>), then
# dropped: APP_STATE["background_tasks"] was never pruned, so it grew until a restart. A task
# that stopped to ask (PROP-011, interrupted) is settled too — it waits in the question store,
# not here. The split is core/turn_states.py.
from trusted_data_agent.core import turn_states as _turn_states  # noqa: E402


def prune_background_tasks(now=None) -> int:
    """Drop FINISHED task records older than ``TDA_TASK_RETENTION_HOURS`` (default 24).

    A running task, or one without a readable ``last_updated``, is never dropped. Called
    where a task is registered, so the dict stays bounded by the retention window.
    Returns the number dropped; never raises.
    """
    from datetime import datetime, timedelta, timezone
    try:
        hours = float(os.environ.get("TDA_TASK_RETENTION_HOURS", "24") or 24)
    except ValueError:
        hours = 24.0
    tasks = APP_STATE.get("background_tasks") or {}
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=max(hours, 0.0))
    dropped = 0
    for task_id, entry in list(tasks.items()):
        try:
            if not isinstance(entry, dict) or not _turn_states.is_settled(entry.get("status")):
                continue
            ts = datetime.fromisoformat(str(entry.get("last_updated")))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                tasks.pop(task_id, None)
                dropped += 1
        except Exception:  # noqa: BLE001 — an unreadable record is kept
            continue
    return dropped


def get_user_llm_instance(user_uuid: str):
    """
    Get LLM instance for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        LLM client instance

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_llm_instance")
    return APP_STATE.get("llm_by_user", {}).get(user_uuid)


def set_user_llm_instance(llm_instance, user_uuid: str):
    """
    Set LLM instance for user.

    Args:
        llm_instance: LLM client instance
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_llm_instance")
    APP_STATE.setdefault("llm_by_user", {})[user_uuid] = llm_instance
    # Also update global APP_STATE for backwards compatibility
    APP_STATE["llm"] = llm_instance


def get_user_mcp_client(user_uuid: str):
    """
    Get MCP client for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        MCP client instance

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_mcp_client")
    return APP_STATE.get("mcp_clients", {}).get(user_uuid)


def set_user_mcp_client(mcp_client, user_uuid: str):
    """
    Set MCP client for user.

    Args:
        mcp_client: MCP client instance
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_mcp_client")
    APP_STATE.setdefault("mcp_clients", {})[user_uuid] = mcp_client
    # Also update global APP_STATE for backwards compatibility
    APP_STATE["mcp_client"] = mcp_client


def get_user_server_configs(user_uuid: str) -> dict:
    """
    Get MCP server configs for user.

    Args:
        user_uuid: User UUID (required)

    Returns:
        Server configs dict

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for get_user_server_configs")
    return APP_STATE.get("server_configs_by_user", {}).get(user_uuid, {})


def set_user_server_configs(server_configs: dict, user_uuid: str):
    """
    Set MCP server configs for user.

    Args:
        server_configs: Server configs dict
        user_uuid: User UUID (required)

    Raises:
        ValueError: If user_uuid is not provided
    """
    if not user_uuid:
        raise ValueError("user_uuid is required for set_user_server_configs")
    if "server_configs_by_user" not in APP_STATE:
        APP_STATE["server_configs_by_user"] = {}
    APP_STATE["server_configs_by_user"][user_uuid] = server_configs
    # Also update global APP_STATE for backwards compatibility
    APP_STATE["server_configs"] = server_configs


# cleanup_inactive_user_contexts removed - no longer needed with database persistence


def get_llm_pool_key(provider: str, model: str, credentials: dict, user_uuid: str) -> str:
    """
    Generate cache key for LLM instance pooling.

    Args:
        provider: LLM provider name (e.g., 'google', 'anthropic')
        model: Model identifier (e.g., 'gemini-2.5-flash')
        credentials: Credentials dictionary
        user_uuid: User UUID for isolation

    Returns:
        Cache key string in format: provider:model:cred_hash:user_uuid
    """
    import hashlib
    import json

    # Hash credentials to avoid storing sensitive data in keys
    cred_str = json.dumps({k: v for k, v in credentials.items() if v}, sort_keys=True)
    cred_hash = hashlib.sha256(cred_str.encode()).hexdigest()[:16]

    # Include user_uuid for per-user isolation
    return f"{provider}:{model}:{cred_hash}:{user_uuid}"


def clear_mcp_server_cache(server_id: str):
    """
    Clear all cached data for a specific MCP server.
    Call this when MCP server configuration changes.

    This clears:
    - Tool schema cache (tools, prompts, resources)
    - Connection pool (MCP client instances)

    Args:
        server_id: MCP server ID to clear caches for
    """
    import logging
    logger = logging.getLogger("quart.app")

    # Clear tool schema cache
    schema_cache = APP_STATE.get('mcp_tool_schema_cache', {})
    if server_id in schema_cache:
        del schema_cache[server_id]
        logger.info(f"Cleared tool schema cache for MCP server {server_id}")

    # Clear connection pool
    client_pool = APP_STATE.get('mcp_client_pool', {})
    if server_id in client_pool:
        del client_pool[server_id]
        logger.info(f"Cleared pooled MCP client for server {server_id}")