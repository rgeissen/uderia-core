# trusted_data_agent/agent/executor.py
import re
import json
import logging
from trusted_data_agent.agent import answer_checks as _answer_checks
import copy
import uuid
import time
# --- MODIFICATION START: Import asyncio ---
import asyncio
# --- MODIFICATION END ---
from enum import Enum, auto
from typing import Tuple, List, Optional, Dict, Any
# --- MODIFICATION START: Import datetime and timezone ---
from datetime import datetime, timezone
# --- MODIFICATION END ---

from trusted_data_agent.agent.formatter import OutputFormatter
from trusted_data_agent.core import session_manager
from trusted_data_agent.llm import handler as llm_handler
from trusted_data_agent.llm import resilience as _resilience
from trusted_data_agent.llm import streaming as _streaming
from trusted_data_agent.agent import turn_budget as _turn_budget
from trusted_data_agent.agent import tool_surface as _tool_surface
from trusted_data_agent.agent import outcome_loop as _outcome_loop
from trusted_data_agent.agent.json_utils import robust_json_parse
# --- MODIFICATION START: Import APP_CONFIG and APP_STATE ---
from trusted_data_agent.core.config import (
    APP_CONFIG, APP_STATE,
    get_user_provider, get_user_model, set_user_provider, set_user_model,
    set_user_aws_region, set_user_azure_deployment_details,
    set_user_friendli_details, set_user_model_provider_in_profile
)
# --- MODIFICATION END ---
from trusted_data_agent.agent.response_models import CanonicalResponse, PromptReportResponse
from trusted_data_agent.mcp_adapter import adapter as mcp_adapter


# Refactored components
from trusted_data_agent.agent.planner import Planner
from trusted_data_agent.agent.phase_executor import PhaseExecutor
from trusted_data_agent.agent.session_name_generator import generate_session_name_with_events
from trusted_data_agent.agent.plan_compiler import (
    TERMINAL_TOOLS as _PC_TERMINAL_TOOLS,
    UTILITY_TOOLS as _PC_UTILITY_TOOLS,
    VISUALIZATION_TOOLS as _PC_VISUALIZATION_TOOLS,
    DISCOVERY_TOOLS as _PC_DISCOVERY_TOOLS,
)
from trusted_data_agent.core.tool_identity import base_name


app_logger = logging.getLogger("quart.app")

# QW7 (assessment §2): process-wide audit counter for trust-gate profile-resolution
# fail-open events. A non-zero count means one or more enforce-mode gates silently ran in
# observe/off because the profile couldn't be loaded — a security-relevant degradation that
# was previously invisible. Incremented + logged by PlanExecutor._resolve_gate_profile.
_TRUST_GATE_FAILOPEN = {"count": 0}

# §4 perf: process-global cache for _get_tool_constraints. The column-type classification
# (numeric/character/any) is a STATIC property of a tool (its name + description), but the executor
# is per-turn, so the paid LLM classification was re-paid every turn AND every sub-executor. Keyed
# by (tool_name, description) — exactly the classification's inputs (server-safe, model-agnostic
# since the constraint reflects the tool's semantics, not the model). Bounded to a backstop size.
_TOOL_CONSTRAINTS_CACHE: dict = {}
_TOOL_CONSTRAINTS_CACHE_MAX = 2048


def document_char_limit(tokens_allocated: Optional[int]) -> int:
    """THE character limit for a turn's documents: the context window's document allocation, but
    never less than today's fixed limit (``DOCUMENT_CONTEXT_MAX_CHARS``) — a type that gives
    documents a small share, or none, never sends LESS of what the user attached than before;
    a larger share sends more."""
    allocated = max(0, int(tokens_allocated or 0)) * 4   # 1 token ≈ 4 chars
    return max(APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS, allocated)


def load_document_context(user_uuid: str, session_id: str, attachments: list, max_total_chars: int | None = None) -> tuple[str | None, list[dict]]:
    """
    Load extracted text from uploaded documents and format as context block.

    This is a module-level function so it can be used by both PlanExecutor and
    the genie execution path.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID
        attachments: List of attachment dicts with file_id, filename keys
        max_total_chars: Optional budget-aware total character limit from context window module.
                        Falls back to APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS if not provided.

    Returns:
        Tuple of (formatted document context string or None, list of truncation event dicts)
    """
    if not attachments:
        return None, []

    from trusted_data_agent.filestore import session_files

    manifest = session_files.readable_manifest(user_uuid, session_id)
    if manifest is None:
        app_logger.warning(f"Upload manifest not found for session {session_id}")
        return None, []

    context_parts = []
    total_chars = 0
    truncation_events = []

    for attachment in attachments:
        file_id = attachment.get("file_id")
        if not file_id or file_id not in manifest:
            app_logger.warning(f"Attachment file_id {file_id} not found in manifest")
            continue

        entry = manifest[file_id]
        filename = entry.get("filename", attachment.get("filename", "Unknown"))
        extracted_text = entry.get("extracted_text", "")

        if not extracted_text:
            continue

        # Truncate individual documents if needed
        original_size = len(extracted_text)
        if original_size > APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS:
            extracted_text = extracted_text[:APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS] + \
                f"\n\n[Document truncated - showing first {APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS:,} characters of {original_size:,} total]"
            truncation_events.append({
                "subtype": "document_truncation",
                "summary": f"Document '{filename}' truncated: {original_size:,} → {APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS:,} chars",
                "filename": filename,
                "original_chars": original_size,
                "truncated_to_chars": APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS,
                "reason": "per_file_limit"
            })

        context_parts.append(f"--- Document: {filename} ---\n{extracted_text}\n--- End of {filename} ---")
        total_chars += len(extracted_text)

        effective_limit = max_total_chars if max_total_chars else APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS
        if total_chars > effective_limit:
            context_parts.append("[Additional documents omitted - context limit reached]")
            truncation_events.append({
                "subtype": "document_truncation",
                "summary": f"Document context limit reached: {len(context_parts)} of {len(attachments)} documents loaded ({total_chars:,} chars)",
                "documents_loaded": len(context_parts),
                "documents_total": len(attachments),
                "total_chars": total_chars,
                "limit_chars": effective_limit,
                "reason": "total_context_limit"
            })
            break

    if not context_parts:
        return None, []

    app_logger.info(f"Loaded document context: {len(context_parts)} documents, {total_chars:,} chars total")
    return "\n\n".join(context_parts), truncation_events


def load_multimodal_document_content(
    user_uuid: str, session_id: str, attachments: list,
    provider: str, model: str
) -> tuple[list[dict] | None, str | None]:
    """
    Split attachments into native multimodal blocks vs text fallback.

    For providers that support native document/image upload (e.g. Anthropic, Google),
    binary files are routed as multimodal blocks. Files that can't go native
    (wrong format, too large, unsupported provider) fall back to text extraction.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID
        attachments: List of attachment dicts with file_id, filename keys
        provider: LLM provider name (e.g. "Anthropic", "Google", "Friendli")
        model: Model name for capability filtering

    Returns:
        (multimodal_blocks, text_fallback) where either can be None.
        multimodal_blocks: [{"type": "document"|"image", "path": str, "mime_type": str, "filename": str}]
        text_fallback: formatted text string for files that can't go native
    """
    if not attachments:
        return None, None

    import os
    from trusted_data_agent.filestore import session_files
    from trusted_data_agent.llm.document_upload import DocumentUploadConfig, DocumentUploadCapability

    manifest = session_files.readable_manifest(user_uuid, session_id)
    if manifest is None:
        app_logger.warning(f"Upload manifest not found for session {session_id}")
        return None, None

    # Determine provider capabilities
    capability = DocumentUploadConfig.get_capability(provider, model)
    native_formats = set(DocumentUploadConfig.get_supported_formats(provider, model))
    max_file_size = DocumentUploadConfig.get_max_file_size(provider, model)
    supports_native = capability in [DocumentUploadCapability.NATIVE_FULL, DocumentUploadCapability.NATIVE_VISION_ONLY]

    app_logger.info(f"Multimodal routing: provider={provider}, model={model}, capability={capability.value}, native_formats={native_formats}")

    native_blocks = []
    text_parts = []
    total_text_chars = 0

    # MIME type mapping
    mime_types = {
        '.pdf': 'application/pdf', '.txt': 'text/plain',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.gif': 'image/gif', '.webp': 'image/webp'
    }
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

    for attachment in attachments:
        file_id = attachment.get("file_id")
        if not file_id or file_id not in manifest:
            app_logger.warning(f"Attachment file_id {file_id} not found in manifest")
            continue

        entry = manifest[file_id]
        filename = entry.get("filename", attachment.get("filename", "Unknown"))
        file_ext = os.path.splitext(filename)[1].lower()

        # Check if this file qualifies for native multimodal upload
        can_go_native = False
        binary = None
        if supports_native and file_ext in native_formats:
            binary = session_files.read_bytes(user_uuid, session_id, file_id, manifest)
        if binary is not None:
            file_size = len(binary)
            if max_file_size == 0 or file_size <= max_file_size:
                can_go_native = True
            else:
                app_logger.info(f"File {filename} ({file_size:,} bytes) exceeds native size limit ({max_file_size:,} bytes), falling back to text")

        if can_go_native:
            mime_type = mime_types.get(file_ext, 'application/octet-stream')
            block_type = "image" if file_ext in image_extensions else "document"
            native_blocks.append({
                "type": block_type,
                "data": binary,
                "mime_type": mime_type,
                "filename": filename
            })
            app_logger.info(f"Native multimodal block: {filename} ({block_type}, {mime_type})")

            # For document-type files (not images), ALSO extract text as fallback.
            # This ensures paths that can't send native document blocks (e.g. LangChain)
            # still have the text content available via document_context.
            if block_type == "document":
                extracted_text = entry.get("extracted_text", "")
                if extracted_text:
                    if len(extracted_text) > APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS:
                        extracted_text = extracted_text[:APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS] + \
                            f"\n\n[Document truncated - showing first {APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS:,} characters]"
                    text_parts.append(f"--- Document: {filename} ---\n{extracted_text}\n--- End of {filename} ---")
                    total_text_chars += len(extracted_text)
                    app_logger.info(f"Also extracted text for native document {filename} (fallback for non-multimodal paths)")
        else:
            # Fall back to text extraction
            extracted_text = entry.get("extracted_text", "")
            if not extracted_text:
                continue
            if len(extracted_text) > APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS:
                extracted_text = extracted_text[:APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS] + \
                    f"\n\n[Document truncated - showing first {APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS:,} characters]"
            text_parts.append(f"--- Document: {filename} ---\n{extracted_text}\n--- End of {filename} ---")
            total_text_chars += len(extracted_text)
            if total_text_chars > APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS:
                text_parts.append("[Additional documents omitted - context limit reached]")
                break

    multimodal_blocks = native_blocks if native_blocks else None
    text_fallback = "\n\n".join(text_parts) if text_parts else None

    if multimodal_blocks:
        app_logger.info(f"Multimodal result: {len(native_blocks)} native block(s) for {provider}")
    if text_fallback:
        app_logger.info(f"Text fallback: {len(text_parts)} document(s), {total_text_chars:,} chars")

    return multimodal_blocks, text_fallback


class DefinitiveToolError(Exception):
    """Custom exception for unrecoverable tool errors."""
    def __init__(self, message, friendly_message):
        super().__init__(message)
        self.friendly_message = friendly_message


class AgentState(Enum):
    PLANNING = auto()
    EXECUTING = auto()
    SUMMARIZING = auto()
    DONE = auto()
    ERROR = auto()


def unwrap_exception(e: BaseException) -> BaseException:
    """Recursively unwraps ExceptionGroups to find the root cause.

    Note: ExceptionGroup was added in Python 3.11. This function is compatible
    with Python 3.10+ by checking if ExceptionGroup exists.
    """
    # ExceptionGroup was added in Python 3.11 - use sys.version_info for compatibility
    import sys
    if sys.version_info >= (3, 11):
        if isinstance(e, ExceptionGroup) and e.exceptions:
            return unwrap_exception(e.exceptions[0])
    return e


def rebuild_tools_and_prompts_context(tool_to_exclude: str = None, state: dict = None) -> Tuple[str, str]:
    """
    Rebuild tools_context and prompts_context strings from a state mapping.

    This function should be called after profile override filtering to ensure
    the LLM sees the correct filtered tools/prompts in planning context.

    Args:
        tool_to_exclude: Tool name to exclude from context (e.g., 'TDA_FinalReport')
        state: Mapping to read structured_tools/mcp_tools/structured_prompts from.
            Defaults to the process-global APP_STATE (backward compatible). A per-executor
            @TAG override passes its own ChainMap overlay so the derived context reflects the
            override's catalog WITHOUT the previous global-APP_STATE mutation (cross-user bleed).

    Returns:
        Tuple[str, str]: (tools_context, prompts_context)
    """
    state = state if state is not None else APP_STATE
    structured_tools = state.get('structured_tools', {})
    mcp_tools = state.get('mcp_tools', {})
    structured_prompts = state.get('structured_prompts', {})

    # Build tools_context
    tool_context_parts = ["--- Available Tools ---"]
    for category, tools in sorted(structured_tools.items()):
        enabled_tools_in_category = [
            t for t in tools
            if not t.get('disabled') and t['name'] != tool_to_exclude
        ]
        if enabled_tools_in_category:
            tool_context_parts.append(f"--- Category: {category} ---")
            for tool_info in enabled_tools_in_category:
                tool_obj = mcp_tools.get(tool_info['name'])
                if not tool_obj:
                    continue

                tool_str = f"- `{tool_obj.name}` (tool): {tool_obj.description}"
                args_dict = tool_obj.args if isinstance(tool_obj.args, dict) else {}

                if args_dict:
                    tool_str += "\n  - Arguments:"
                    for arg_name, arg_details in args_dict.items():
                        arg_type = arg_details.get('type', 'any')
                        is_required = arg_details.get('required', False)
                        req_str = "required" if is_required else "optional"
                        arg_desc = arg_details.get('description', 'No description.')
                        tool_str += f"\n    - `{arg_name}` ({arg_type}, {req_str}): {arg_desc}"
                tool_context_parts.append(tool_str)

    tools_context = "\n".join(tool_context_parts) if len(tool_context_parts) > 1 else "--- No Tools Available ---"

    # Build prompts_context
    prompt_context_parts = ["--- Available Prompts ---"]
    for category, prompts in sorted(structured_prompts.items()):
        enabled_prompts_in_category = [p for p in prompts if not p.get('disabled')]
        if enabled_prompts_in_category:
            prompt_context_parts.append(f"--- Category: {category} ---")
            for prompt_info in enabled_prompts_in_category:
                prompt_description = prompt_info.get("description", "No description available.")
                prompt_str = f"- `{prompt_info['name']}` (prompt): {prompt_description}"

                processed_args = prompt_info.get('arguments', [])
                if processed_args:
                    prompt_str += "\n  - Arguments:"
                    for arg_details in processed_args:
                        arg_name = arg_details.get('name', 'unknown')
                        arg_type = arg_details.get('type', 'any')
                        is_required = arg_details.get('required', False)
                        req_str = "required" if is_required else "optional"
                        arg_desc = arg_details.get('description', 'No description.')
                        prompt_str += f"\n    - `{arg_name}` ({arg_type}, {req_str}): {arg_desc}"
                prompt_context_parts.append(prompt_str)

    prompts_context = "\n".join(prompt_context_parts) if len(prompt_context_parts) > 1 else "--- No Prompts Available ---"

    return tools_context, prompts_context


COMPACT_DESCRIPTION_CHARS = 240   # a correction catalog line: what the tool does, not its manual


def build_compact_tools_and_prompts_context(
    structured_tools: dict, mcp_tools: dict, structured_prompts: dict, tool_to_exclude: str = None
) -> Tuple[str, str]:
    """§4 perf: a COMPACT catalog for self-correction prompts.

    Self-correction templates (TABLE/COLUMN/generic) use the catalog to let the LLM *switch* to a
    better tool/prompt ("Consider Alternative Tools", "Switch to a New Capability"), so the catalog
    can't be removed — but it doesn't need each tool's full multi-line argument schema (type +
    required + description per arg), which dominates the size and is re-sent on every retry. This
    projection keeps one line per tool/prompt: name + description + argument NAMES (a `*` marks
    required) — enough to discover and correctly call an alternative — and drops the per-arg detail.
    The failed tool retains its full schema separately (the generic correction's `tool_definition`).

    Pure function (mirrors ``rebuild_tools_and_prompts_context``) so it is unit-testable.
    """
    def _arg_names(pairs):
        # pairs: iterable of (name, is_required)
        return ", ".join(f"{n}*" if req else n for n, req in pairs)

    def _brief(text):
        # The description's opening statement: its first paragraph, capped. Some MCP tools carry
        # multi-page descriptions (rules, examples, parameter manuals — one ran 6.5K tokens) and
        # made the "compact" catalog 18K; switching needs WHAT a tool does, the failed tool keeps
        # its full definition separately.
        t = (text or "").strip().split("\n\n", 1)[0].strip()
        t = " ".join(t.split())
        return t if len(t) <= COMPACT_DESCRIPTION_CHARS else t[:COMPACT_DESCRIPTION_CHARS - 1].rstrip() + "…"

    tool_parts = ["--- Available Tools ---"]
    for category, tools in sorted(structured_tools.items()):
        enabled = [t for t in tools if not t.get('disabled') and t['name'] != tool_to_exclude]
        if not enabled:
            continue
        tool_parts.append(f"--- Category: {category} ---")
        for tool_info in enabled:
            tool_obj = mcp_tools.get(tool_info['name'])
            if not tool_obj:
                continue
            args_dict = tool_obj.args if isinstance(tool_obj.args, dict) else {}
            names = _arg_names((an, ad.get('required', False)) for an, ad in args_dict.items())
            line = f"- `{tool_obj.name}` (tool): {_brief(tool_obj.description)}"
            if names:
                line += f" [args: {names}]"
            tool_parts.append(line)
    tools_context = "\n".join(tool_parts) if len(tool_parts) > 1 else "--- No Tools Available ---"

    prompt_parts = ["--- Available Prompts ---"]
    for category, prompts in sorted(structured_prompts.items()):
        enabled = [p for p in prompts if not p.get('disabled')]
        if not enabled:
            continue
        prompt_parts.append(f"--- Category: {category} ---")
        for p in enabled:
            names = _arg_names((a.get('name', 'unknown'), a.get('required', False)) for a in p.get('arguments', []))
            line = f"- `{p['name']}` (prompt): {_brief(p.get('description', 'No description available.'))}"
            if names:
                line += f" [args: {names}]"
            prompt_parts.append(line)
    prompts_context = "\n".join(prompt_parts) if len(prompt_parts) > 1 else "--- No Prompts Available ---"

    return tools_context, prompts_context


async def _ingest_documents_to_session_store(
    session_store: Any,
    session_id: str,
    user_uuid: str,
    session_data: dict,
) -> None:
    """Ingest uploaded documents into the session vector store for RAG condensation.

    Chunks extracted text from each attachment into ~200-token paragraph blocks.
    Idempotent: session_store.ingest() skips already-indexed chunk IDs.
    Called before handler.assemble() so the store is warm during Pass 4 condensation.
    """
    try:
        from trusted_data_agent.filestore import session_files
        from trusted_data_agent.vectorstore.types import VectorDocument

        attachments = session_data.get("attachments", [])
        if not attachments:
            return

        manifest = session_files.readable_manifest(user_uuid, session_id)
        if manifest is None:
            return

        CHUNK_CHARS = 800  # ~200 tokens per chunk

        for attachment in attachments:
            file_id = attachment.get("file_id", "")
            filename = attachment.get("filename", "unknown")
            file_info = manifest.get(file_id, {})
            extracted_text = file_info.get("extracted_text", "")
            if not extracted_text:
                continue

            # Split into paragraphs, group into ~200-token chunks
            paragraphs = [p.strip() for p in extracted_text.split("\n\n") if p.strip()]
            chunks: list = []
            current: list = []
            current_len = 0
            chunk_idx = 0

            for para in paragraphs:
                if current_len + len(para) > CHUNK_CHARS and current:
                    chunks.append((chunk_idx, "\n\n".join(current)))
                    chunk_idx += 1
                    current = []
                    current_len = 0
                current.append(para)
                current_len += len(para)

            if current:
                chunks.append((chunk_idx, "\n\n".join(current)))

            docs = [
                VectorDocument(
                    id=f"{session_id}__doc__{file_id}__chunk{idx}",
                    content=text,
                    metadata={"file_id": file_id, "filename": filename, "chunk_num": idx},
                )
                for idx, text in chunks
            ]

            if docs:
                await session_store.ingest("document_context", docs)
                app_logger.debug(
                    f"Ingested {len(docs)} document chunks for '{filename}' "
                    f"into session store {session_id}"
                )

    except Exception as _e:
        app_logger.debug(f"Document ingestion to session store skipped (non-critical): {_e}")


class AgentBudgetExceededError(RuntimeError):
    """An autonomous run hit its configured per-run cost ceiling.

    Named rather than a bare RuntimeError so the stop is legible in a trace and can be
    distinguished from a model or provider failure — "the agent ran out of budget" and
    "the provider fell over" call for opposite responses from an operator.
    """


class PlanExecutor:
    AgentState = AgentState

    def _get_prompt_info(self, prompt_name: str) -> dict | None:
        """Helper to find prompt details from the structured prompts in the global state."""
        if not prompt_name:
            return None
        structured_prompts = self.dependencies['STATE'].get('structured_prompts', {})
        for category_prompts in structured_prompts.values():
            for prompt in category_prompts:
                if prompt.get("name") == prompt_name:
                    return prompt
        return None

    # --- MODIFICATION START: Add plan_to_execute and is_replay ---
    def __init__(self, session_id: str, user_uuid: str, original_user_input: str, dependencies: dict, active_prompt_name: str = None, prompt_arguments: dict = None, execution_depth: int = 0, disabled_history: bool = False, previous_turn_data: dict = None, force_history_disable: bool = False, source: str = "text", is_delegated_task: bool = False, force_final_summary: bool = False, plan_to_execute: list = None, is_replay: bool = False, task_id: str = None, profile_override_id: str = None, event_handler=None, is_session_primer: bool = False, attachments: list = None, skill_result=None, canvas_context: dict = None, force_profile_type: str = None, _visited_prompts: frozenset = None, answer_stream=None):
        self.session_id = session_id
        self.user_uuid = user_uuid
        self.event_handler = event_handler
        # The Governed Stream (S1): the per-turn delta sink created at the execution
        # seam (None ⇒ streaming off ⇒ every call takes the non-streaming API). Only the
        # TOP-LEVEL executor's engine answer-producing call feeds it — a delegated
        # sub-executor's prose is never the user's answer.
        self.answer_stream = answer_stream if execution_depth == 0 else None
        self._partial_turn_saved = False
        self.is_session_primer = is_session_primer  # Track if this is a session primer execution
        # --- MODIFICATION END ---
        self.original_user_input = original_user_input
        self.dependencies = dependencies
        self.state = self.AgentState.PLANNING

        # C11 fix: knowledge reranking was dead — `self.llm_handler` was never assigned, so
        # both the executor's own rerank guard (executor.py:_rerank_knowledge_with_llm) AND the
        # three engine callers (Ideate/Focus/Conversation: `if coll_results and executor.llm_handler`)
        # raised AttributeError the instant a `reranking`-enabled collection returned results —
        # aborting the turn. The executor always has LLM access via `_call_llm_and_update_tokens`,
        # so reranking IS available; expose the (always-truthy) module handler so the guards read
        # as "reranking available" and the working implementation actually runs.
        self.llm_handler = llm_handler

        # --- MODIFICATION START: Store profile override and setup temporary context ---
        self.profile_override_id = profile_override_id
        self.original_llm = None  # Will store original LLM if overridden
        self.original_mcp_tools = None  # Will store original tools if overridden
        self.original_mcp_prompts = None  # Will store original prompts if overridden
        self.original_provider = None  # Will store original provider if overridden
        self.original_model = None  # Will store original model if overridden
        # The provider/model that actually SERVED this turn, snapshotted when an @TAG override
        # is reverted (restore_override_app_state resets current_* to the default BEFORE the
        # turn is persisted). Read via served_provider_model(); None ⇒ current_* is the truth.
        self.turn_served_provider = None
        self.turn_served_model = None
        self.original_structured_tools = None  # Will store original structured_tools if overridden
        self.original_structured_prompts = None  # Will store original structured_prompts if overridden
        self.original_provider_details = {}  # Will store provider-specific config (Friendli, Azure, AWS)
        self.original_server_id = None  # Will store original current_server_id_by_user entry if overridden
        # C10: True once an @TAG override has CAPTURED originals and begun (or intends to) mutate
        # process-global APP_STATE. The restore is keyed on THIS flag, not profile_override_id
        # (which the failure path clears), so a partial mutation can never leak the override's
        # tool catalog to later turns/sessions. Flipped back to False by the restore (idempotent).
        self.profile_override_applied = False
        self.effective_mcp_server_id = None  # Track the ACTUAL MCP server ID used during this turn (for RAG storage)
        self.profile_llm_instance = None  # Profile-specific LLM client (created when profile uses different provider than default)
        self.thinking_budget = None  # Gemini 2.x thinking budget from LLM config (None = not set, 0 = disabled, -1 = dynamic)
        self.force_profile_type = force_profile_type  # Override _detect_profile_type() (e.g. "tool_enabled" for KG constructor)

        # Snapshot model and provider for this turn from active profile (default or override)
        # Don't use global config as it may not match the profile being used
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()
            
            # Determine which profile will be used (override or default)
            self.active_profile_id = profile_override_id if profile_override_id else config_manager.get_default_profile_id(user_uuid)
            
            if self.active_profile_id:
                profiles = config_manager.get_profiles(user_uuid)
                active_profile = next((p for p in profiles if p.get("id") == self.active_profile_id), None)
                
                if active_profile:
                    # Get LLM configuration from the active profile
                    llm_config_id = active_profile.get('llmConfigurationId')
                    if llm_config_id:
                        llm_configs = config_manager.get_llm_configurations(user_uuid)
                        llm_config = next((cfg for cfg in llm_configs if cfg['id'] == llm_config_id), None)

                        if llm_config:
                            self.current_provider = llm_config.get('provider', get_user_provider(user_uuid))
                            self.current_model = llm_config.get('model', get_user_model(user_uuid))
                            self.thinking_budget = llm_config.get('thinking_budget')
                            app_logger.debug(f"Initialized consumption tracking with profile model: {self.current_provider}/{self.current_model}")
                        else:
                            # Fallback to global config if LLM config not found
                            self.current_model = get_user_model(user_uuid)
                            self.current_provider = get_user_provider(user_uuid)
                    else:
                        # Fallback to global config if no LLM config in profile
                        self.current_model = get_user_model(user_uuid)
                        self.current_provider = get_user_provider(user_uuid)

                    # Initialize dual-model configuration (tool_enabled only)
                    dual_model_config = active_profile.get('dualModelConfig')
                    if dual_model_config and active_profile.get('profile_type') == 'tool_enabled':
                        strategic_id = dual_model_config.get('strategicModelId')
                        tactical_id = dual_model_config.get('tacticalModelId')

                        llm_configs = config_manager.get_llm_configurations(user_uuid)

                        # Resolve strategic model (fallback to base)
                        if strategic_id:
                            strategic_config = next((cfg for cfg in llm_configs if cfg['id'] == strategic_id), None)
                            if strategic_config:
                                self.strategic_provider = strategic_config.get('provider')
                                self.strategic_model = strategic_config.get('model')
                                app_logger.info(f"[Dual-Model] Strategic: {self.strategic_provider}/{self.strategic_model}")
                            else:
                                app_logger.warning(f"Strategic model '{strategic_id}' not found, using base")
                                self.strategic_provider = self.current_provider
                                self.strategic_model = self.current_model
                        else:
                            self.strategic_provider = self.current_provider
                            self.strategic_model = self.current_model

                        # Resolve tactical model (fallback to base)
                        if tactical_id:
                            tactical_config = next((cfg for cfg in llm_configs if cfg['id'] == tactical_id), None)
                            if tactical_config:
                                self.tactical_provider = tactical_config.get('provider')
                                self.tactical_model = tactical_config.get('model')
                                app_logger.info(f"[Dual-Model] Tactical: {self.tactical_provider}/{self.tactical_model}")
                            else:
                                app_logger.warning(f"Tactical model '{tactical_id}' not found, using base")
                                self.tactical_provider = self.current_provider
                                self.tactical_model = self.current_model
                        else:
                            self.tactical_provider = self.current_provider
                            self.tactical_model = self.current_model

                        # Track if dual-model is active
                        self.is_dual_model_active = (
                            self.strategic_provider != self.tactical_provider or
                            self.strategic_model != self.tactical_model
                        )

                        # Initialize client instances (created in async run() method)
                        self.strategic_llm_instance = None
                        self.tactical_llm_instance = None
                    else:
                        # No dual-model config: Use base for both
                        self.strategic_provider = self.current_provider
                        self.strategic_model = self.current_model
                        self.tactical_provider = self.current_provider
                        self.tactical_model = self.current_model
                        self.is_dual_model_active = False
                        self.strategic_llm_instance = None
                        self.tactical_llm_instance = None
                else:
                    # Fallback to global config if profile not found
                    self.current_model = get_user_model(user_uuid)
                    self.current_provider = get_user_provider(user_uuid)
                    # No dual-model for fallback
                    self.strategic_provider = self.current_provider
                    self.strategic_model = self.current_model
                    self.tactical_provider = self.current_provider
                    self.tactical_model = self.current_model
                    self.is_dual_model_active = False
                    self.strategic_config = None
                    self.tactical_config = None
                    self.strategic_llm_instance = None
                    self.tactical_llm_instance = None
            else:
                # Fallback to global config if no active profile
                self.active_profile_id = "__system_default__"
                self.current_model = get_user_model(user_uuid)
                self.current_provider = get_user_provider(user_uuid)
                # No dual-model for fallback
                self.strategic_provider = self.current_provider
                self.strategic_model = self.current_model
                self.tactical_provider = self.current_provider
                self.tactical_model = self.current_model
                self.is_dual_model_active = False
                self.strategic_config = None
                self.tactical_config = None
                self.strategic_llm_instance = None
                self.tactical_llm_instance = None
        except Exception as e:
            # Fallback to global config on error
            app_logger.warning(f"Failed to get model/provider from profile, using global config: {e}")
            self.active_profile_id = "__system_default__"
            self.current_model = get_user_model(user_uuid)
            self.current_provider = get_user_provider(user_uuid)
            # No dual-model for fallback
            self.strategic_provider = self.current_provider
            self.strategic_model = self.current_model
            self.tactical_provider = self.current_provider
            self.tactical_model = self.current_model
            self.is_dual_model_active = False
            self.strategic_config = None
            self.tactical_config = None
            self.strategic_llm_instance = None
            self.tactical_llm_instance = None
            self.tactical_provider = self.current_provider
            self.tactical_model = self.current_model
            self.is_dual_model_active = False
        
        # Initialize profile-aware prompt resolver
        from trusted_data_agent.agent.profile_prompt_resolver import ProfilePromptResolver
        self.prompt_resolver = ProfilePromptResolver(
            profile_id=self.active_profile_id,
            provider=self.current_provider
        )
        app_logger.info(f"Initialized ProfilePromptResolver with profile_id='{self.active_profile_id}', provider='{self.current_provider}'")
        # --- MODIFICATION END ---

        self.structured_collected_data = {}
        self.workflow_state = {}
        self.turn_action_history = []
        self.meta_plan = None
        self.original_plan_for_history = None # Added to store original plan
        self.raw_llm_plan = None  # LLM's raw output before any preprocessing/rewrite passes
        self.current_phase_index = 0
        self.last_tool_output = None

        self.active_prompt_name = active_prompt_name
        self.prompt_arguments = prompt_arguments or {}
        self.workflow_goal_prompt = ""

        prompt_info = self._get_prompt_info(active_prompt_name)
        self.prompt_type = prompt_info.get("prompt_type", "reporting") if prompt_info else "reporting"

        self.is_in_loop = False
        self.current_loop_items = []
        self.processed_loop_items = []

        self.tool_constraints_cache = {}
        self.temp_data_holder = None
        self.last_failed_action_info = "None"  # kept for logging compat; superseded by correction_history
        self.correction_history: list = []   # accumulated failed attempts for current phase (Reflexion pattern)
        self.events_to_yield = []
        self.last_action_str = None
        # Per-phase isolated state for parallel execution correctness.
        # Parallel phases share the same PlanExecutor instance but need independent
        # correction_history and last_action_str so corrections from one phase don't
        # leak into another's tactical LLM prompt.  Keyed by phase_num (int).
        self._phase_parallel_state: dict[int, dict] = {}

        self.llm_debug_history = []
        self.max_steps = 40

        self.execution_depth = execution_depth
        self.MAX_EXECUTION_DEPTH = APP_CONFIG.MAX_EXECUTION_DEPTH
        # Tracks the prompt call chain for this execution tree to detect cycles (e.g. A→B→A).
        # Inherited by sub-executors and extended with each new prompt name.
        self._visited_prompts: frozenset = _visited_prompts if _visited_prompts is not None else frozenset()

        self.disabled_history = disabled_history or force_history_disable
        self.previous_turn_data = previous_turn_data or {}
        self.is_synthesis_from_history = False
        self.is_conversational_plan = False
        self.source = source
        self.is_delegated_task = is_delegated_task
        self.force_final_summary = force_final_summary

        self.is_complex_prompt_workflow = False
        self.final_canonical_response = None
        self.is_single_prompt_plan = False
        self.final_summary_text = ""

        # --- MODIFICATION START: Store replay flags ---
        self.plan_to_execute = plan_to_execute # Store the plan if provided for replay
        self.is_replay = is_replay # Flag indicating if this is a replay
        # --- MODIFICATION END ---
        # --- MODIFICATION START: Add instance variable for turn number ---
        self.current_turn_number = 0 # Will be calculated once in run()
        # --- MODIFICATION END ---
        # --- MODIFICATION START: Store task_id ---
        self.task_id = task_id
        # --- MODIFICATION END ---
        # --- Document upload attachments ---
        self.attachments = attachments or []
        self.document_context = None  # Will be populated from extracted text if attachments present
        self.multimodal_content = None  # Will be populated with native multimodal blocks if provider supports it
        # --- Pre-processing skill injections ---
        self.skill_result = skill_result  # SkillResult from skills module (or None)
        # --- Canvas bidirectional context ---
        self.canvas_context = canvas_context  # {title, language, content, modified} or None

        self.turn_input_tokens = 0
        self.turn_output_tokens = 0
        # turn_cost dual-model fix: the running Σ of every in-turn per-call cost_usd, each priced at
        # its OWN model. Persisted as turn_cost so the total matches the sum of the per-event costs
        # the Live Status panel shows (the _getTurnCost invariant) — instead of re-pricing all turn
        # tokens at a single model, which mis-priced dual-model turns. See
        # docs/assessments/TURN_COST_DUAL_MODEL_PLAN.md.
        self.turn_cost_usd = 0.0
        self.turn_cached_tokens = 0        # S2: prompt tokens served from the provider's cache this turn
        self.turn_cache_write_tokens = 0   # S2: prompt tokens written to it (explicit breakpoints)

        # --- MODIFICATION START: Store the global RAG retriever instance ---
        if APP_CONFIG.RAG_ENABLED:
            from trusted_data_agent.agent.rag_retriever import get_rag_retriever
            self.rag_retriever = get_rag_retriever()
            if self.rag_retriever is None:
                app_logger.warning("RAG is enabled but retriever could not be initialized. Knowledge retrieval will be unavailable for this turn.")
        else:
            self.rag_retriever = None
        # --- MODIFICATION END ---
        
        # --- MODIFICATION START: Track which collection RAG examples came from ---
        self.rag_source_collection_id = None  # Will be set when RAG examples are retrieved
        self.rag_source_case_id = None  # Will be set when RAG examples are retrieved (for feedback tracking)
        self.rag_case_count = None  # How many champions matched (the receipt's fact; None = none retrieved)
        # --- MODIFICATION END ---
        
        # --- PHASE 2: Track knowledge repository access ---
        self.knowledge_accessed = []  # List of {collection_id, collection_name, document_count} during planning
        self.knowledge_retrieval_event = None  # Store the knowledge retrieval event for replay
        # --- PHASE 2 END ---
        self.context_window_snapshot_event = None  # Store context window snapshot for historical replay
        self.strategic_context_snapshot_event = None  # Per-call strategic snapshot from planner for reload
        self._context_distiller = None  # Lazily initialised from context window type config
        self._distillation_events = []  # Accumulates distillation events across all phases for snapshot
        self.context_builder = None  # ContextBuilder — lazily initialised from context window assembly

        # Knowledge Graph enrichment event for Live Status replay
        self.kg_enrichment_event = None
        # Budget-managed KG enrichment cached from the Context Window assembly
        # (the knowledge_graph_context module). _cw_kg_assembled gates whether
        # _get_kg_enrichment() trusts the cache vs. falls back to a direct fetch.
        self._cw_kg_text = None
        self._cw_kg_assembled = False
        # RAG Trust Hardening Wave 1 (E2): knowledge_context module cache — the served
        # chunks + retrieval outcome, threaded to the corpus-support trigger and the
        # sufficiency-honesty gate for module-fed engines. None ⇒ module didn't run.
        self._cw_knowledge_chunks = None
        self._cw_knowledge_retrieval = None
        # Findings (the `findings` module): the verified results of earlier turns, as the
        # text the model is shown — delivered after the KG enrichment on every engine and
        # handed to the grounding gate as vouched evidence. None ⇒ module didn't run / nothing.
        self._cw_findings_text = None
        # Agent Memory (the `user_memory` module): the recalled "[Remembered about this user]"
        # block. Until Nov 2026 the module assembled and measured it but NO engine read the
        # contribution, so recall never reached a model. Delivered beside the findings.
        self._cw_memory_text = None

    def _get_findings_context(self) -> str:
        """The findings block the context window assembled for this turn ("" when none)."""
        return getattr(self, "_cw_findings_text", None) or ""  # an executor built without __init__ (tests) has none

    def _get_memory_context(self) -> str:
        """The Agent Memory block the context window recalled for this turn ("" when none)."""
        return getattr(self, "_cw_memory_text", None) or ""

    def _session_context_blocks(self) -> str:
        """Memory first (who the user is), then the findings (what the session's tools
        returned) — the ONE order every engine appends after its KG enrichment."""
        return "\n\n".join(b for b in (self._get_memory_context(), self._get_findings_context()) if b)

    async def _get_kg_enrichment(self):
        """
        Fetch KG context enrichment from active components.

        Prefers the budget-managed output assembled by the Context Window
        orchestrator (the ``knowledge_graph_context`` module), which already
        scaled the subgraph to the module's token allocation and stamped
        ``self.kg_enrichment_event``. Falls back to a direct, unbounded fetch
        when assembly did not run (Context Window Manager disabled, no CWT
        bound, or a profile type the module does not apply to) — preserving
        the exact legacy behaviour and keeping the always-on trusted scope
        armed for the grounding gate.

        Returns:
            Enrichment text to inject into LLM context, or empty string.
        """
        # Budget-managed path: the orchestrator already ran the KG module for
        # this turn and cached its output. Trust it even when empty (the module
        # ran but found no query-relevant subgraph) to avoid a redundant fetch.
        if getattr(self, "_cw_kg_assembled", False):
            # The module stamps kg_enrichment_event for a real match; the
            # always-on trusted scope must still be applied (idempotent) so the
            # gate is armed on out-of-domain queries with no per-query match.
            await self._apply_trusted_scope()
            return getattr(self, "_cw_kg_text", "") or ""
        try:
            app_logger.debug(
                f"KG enrichment: starting for profile_id={self.active_profile_id}, "
                f"user_uuid={self.user_uuid}, query='{self.original_user_input[:80]}'"
            )
            from trusted_data_agent.components.manager import get_component_context_enrichment
            kg_text, kg_details = await get_component_context_enrichment(
                query=self.original_user_input,
                profile_id=self.active_profile_id,
                user_uuid=self.user_uuid,
                session_id=self.session_id,
            )
            if kg_text and kg_details:
                self.kg_enrichment_event = {
                    "enrichments": kg_details,
                    "total_entities": sum(d.get("entity_count", 0) for d in kg_details),
                    "total_relationships": sum(d.get("relationship_count", 0) for d in kg_details),
                    "session_id": self.session_id,
                }
                app_logger.info(
                    f"KG enrichment: {self.kg_enrichment_event['total_entities']} entities, "
                    f"{self.kg_enrichment_event['total_relationships']} relationships"
                )
                await self._apply_trusted_scope()
                return kg_text
            else:
                app_logger.debug(
                    f"KG enrichment: returned empty (kg_text={bool(kg_text)}, kg_details={bool(kg_details)})"
                )
                # Even with no query-relevant match, the gate's trusted scope is always-on:
                # stamp the profile's bound-KG deployed scope so out-of-scope drift is caught
                # on out-of-domain queries too (the hole that previously left the gate blind).
                await self._apply_trusted_scope()
        except Exception as e:
            app_logger.warning(f"KG enrichment failed: {e}", exc_info=True)
        return ""

    async def _apply_trusted_scope(self):
        """Pre-stamp ``self.kg_enrichment_event`` with the profile's bound-KG ALWAYS-ON deployed
        trusted scope so the LIVE KG-enrichment display event carries it. Delegates to the one
        shared implementation (``grounding.adapter.ensure_trusted_scope``) that ``run_grounding``
        also uses, so the displayed scope and the gate's scope can never diverge. Never raises."""
        from trusted_data_agent.agent.grounding import adapter as _grounding_adapter
        self.kg_enrichment_event = await _grounding_adapter.ensure_trusted_scope(
            self.kg_enrichment_event, {"id": self.active_profile_id},
            self.user_uuid, self.session_id)
        # The declared join keys the enrichment carries become the preflight guard's
        # reference for this session (the runaway-join class). Never raises.
        try:
            from trusted_data_agent.agent.preflight import guard as _preflight_guard
            _preflight_guard.maybe_record_join_keys(self.session_id, self.kg_enrichment_event)
            # …and the text-stored date columns (the lexical-date class).
            _preflight_guard.maybe_record_text_dates(self.session_id, self.kg_enrichment_event)
            # …and the declared value domains (the hallucinated-literal class).
            _preflight_guard.maybe_record_value_domains(self.session_id, self.kg_enrichment_event)
            # …and the complete column lists (the hallucinated-column class).
            _preflight_guard.maybe_record_columns(self.session_id, self.kg_enrichment_event)
        except Exception:
            pass

    def _check_cancellation(self):
        """
        Check if cancellation was requested for this execution.
        Raises asyncio.CancelledError if cancellation flag is set.
        This provides a manual cancellation checkpoint independent of asyncio task.cancel().
        """
        from trusted_data_agent.core.config import APP_STATE
        active_tasks_key = f"{self.user_uuid}_{self.session_id}"
        cancellation_flags = APP_STATE.get("cancellation_flags", {})

        if cancellation_flags.get(active_tasks_key):
            app_logger.info(f"[CANCELLATION] Flag detected for session {self.session_id} - raising CancelledError")
            raise asyncio.CancelledError()

    def _log_system_event(self, event_data: dict, event_name: str = None):
        """Logs a system-level event to the turn action history for replay and debugging."""
        # Avoid logging token updates or status indicators
        if event_name in ["token_update", "status_indicator_update"] or "state" in event_data:
            return

        # Avoid logging the final answer event as it's not a step in the process
        if event_name == "final_answer":
            return

        action_for_history = {
            "tool_name": "TDA_SystemLog",
            "arguments": {
                "message": event_data.get("step"),
                "details": event_data.get("details")
            },
            "metadata": {
                "execution_depth": self.execution_depth,
                "type": event_data.get("type"),
                # --- MODIFICATION START: Add timestamp for per-step timing ---
                "timestamp": datetime.now(timezone.utc).isoformat()
                # --- MODIFICATION END ---
            }
        }
        result = {"status": "info"}
        if event_data.get("type") in ["error", "cancelled"]:
            result["status"] = event_data.get("type")

        self.turn_action_history.append({"action": action_for_history, "result": result})


    @staticmethod
    def _format_sse(data: dict, event: str = None) -> str:
        msg = f"data: {json.dumps(data)}\n"
        if event is not None:
            msg += f"event: {event}\n"
        return f"{msg}\n"

    def served_provider_model(self):
        """(provider, model) that served this turn — the override's model when an @TAG override
        ran and was reverted before persistence, else the executor's current model."""
        return (getattr(self, "turn_served_provider", None) or self.current_provider,
                getattr(self, "turn_served_model", None) or self.current_model)

    def _format_sse_with_depth(self, data: dict, event: str = None) -> str:
        """Wraps _format_sse and auto-injects execution_depth into metadata."""
        if self.execution_depth > 0:
            data.setdefault("metadata", {})["execution_depth"] = self.execution_depth
        return self._format_sse(data, event)

    @staticmethod
    def _sub_totals_base(sub) -> tuple:
        """The running totals a delegated sub-executor STARTS from (inherited from the parent)."""
        return (int(getattr(sub, "turn_input_tokens", 0) or 0), int(getattr(sub, "turn_output_tokens", 0) or 0),
                float(getattr(sub, "turn_cost_usd", 0.0) or 0.0))

    def _merge_sub_totals(self, sub, base: tuple) -> None:
        """Add a sub-executor's OWN usage (its totals minus where it started) to this turn's.
        A delta, so a call made concurrently on this executor while the sub ran is kept."""
        bi, bo, bc = base
        self.turn_input_tokens = (self.turn_input_tokens or 0) + max(0, int(sub.turn_input_tokens or 0) - bi)
        self.turn_output_tokens = (self.turn_output_tokens or 0) + max(0, int(sub.turn_output_tokens or 0) - bo)
        self.turn_cost_usd = (getattr(self, "turn_cost_usd", 0.0) or 0.0) + max(0.0, float(sub.turn_cost_usd or 0.0) - bc)

    def _accumulate_call_cost(self, cost_usd: float) -> None:
        """The one sink for a priced LLM call made OUTSIDE `_call_llm_and_update_tokens` (the
        LangChain lanes): it lands in the same per-turn accumulator, so `_resolve_turn_cost_usd`
        is authoritative for every engine."""
        self.turn_cost_usd = (getattr(self, "turn_cost_usd", 0.0) or 0.0) + float(cost_usd or 0.0)

    def _resolve_turn_cost_usd(self, prefer_accumulator: bool = True) -> float:
        """turn_cost dual-model fix (§3.3): the authoritative per-turn cost = Σ of the accurate
        per-call costs accumulated during the turn (each priced at its OWN model). This is the
        exact quantity ``_getTurnCost`` sums, so persisting it keeps backend ``turn_cost`` ==
        the Live Status event-sum for both single- AND dual-model turns.

        Falls back to a single-model re-price of the summed tokens ONLY when the accumulator is
        empty but tokens were spent (a legacy/edge path that bypassed the accumulator) — so we can
        never regress to ``$0``. Returns 0.0 when nothing was spent.

        ``prefer_accumulator=False`` forces the single-model re-price and IGNORES the accumulator.
        Used by the Conversation (ReAct) engine: its main LangChain call does NOT flow through
        ``_call_llm_and_update_tokens`` (so it never accumulates), but a helper that DOES — knowledge
        reranking — can partially populate the accumulator. A PARTIAL accumulator (reranking only,
        missing the ReAct bulk) must not be treated as authoritative; ReAct is single-model and
        ``turn_input_tokens`` already includes ReAct + reranking, so the re-price is the correct total."""
        accumulated = getattr(self, "turn_cost_usd", 0.0) or 0.0
        if prefer_accumulator and accumulated > 0:
            return accumulated
        if self.turn_input_tokens or self.turn_output_tokens:
            try:
                from trusted_data_agent.core.cost_manager import get_cost_manager
                return get_cost_manager().calculate_cost(
                    self.current_provider, self.current_model,
                    self.turn_input_tokens, self.turn_output_tokens) or 0.0
            except Exception:
                return 0.0
        return 0.0

    def _resolve_gate_profile(self, gate_name: str):
        """QW7 (assessment §2): resolve the active profile for a trust-gate seam.

        All five gate seams (grounding, outbound/inbound guardrail, honesty, honesty-RAG)
        previously did ``except Exception: profile = None`` silently — so a transient DB error
        while loading the profile downgrades an *enforce*-mode gate to observe with zero
        evidence. The gate must still fail OPEN (return None → observe/off, never break a turn),
        but the fail-open is now logged and counted so it is auditable rather than invisible.
        """
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            return get_config_manager().get_profile(self.active_profile_id, self.user_uuid)
        except Exception as _gate_err:
            _TRUST_GATE_FAILOPEN["count"] += 1
            app_logger.warning(
                f"[trust-gate:{gate_name}] profile resolution failed for profile "
                f"'{self.active_profile_id}' — gate degrades to observe/off "
                f"(fail-open #{_TRUST_GATE_FAILOPEN['count']}): {_gate_err}"
            )
            return None

    async def _run_grounding_gate_full(self, answer_text: str, *, trace_extra: dict = None):
        """Tier 1.1 — the ONE entrypoint the Optimize/Ideate/Focus/Conversation engines call
        (charter #14). Delegates to the engine-agnostic ``grounding.runtime.run_grounding``
        (deterministic gate + judge-backed enforce), passing this executor's turn context. The
        Coordinate/genie engine — which has no ``PlanExecutor`` — calls ``run_grounding``
        directly with the same contract, so all five engines share one implementation.

        Returns ``(answer_text, event_payload | None)`` — event ``None`` & answer unchanged on
        abstain/off/error (fails open). Async so the per-engine seam can await the judge before
        the answer is streamed (you cannot withhold after delivery).

        See docs/roadmap/TIER_1.1_GROUNDING_GATE.md.
        """
        from trusted_data_agent.agent.grounding import runtime as _grounding_runtime
        # PROP-011: a turn that stopped to ask makes no claim — the gate abstains (an answer
        # that is a question must not be graded, revised or withheld).
        from trusted_data_agent.agent import input_requests as _ir_mod
        if _ir_mod.pending() is not None:
            return answer_text, {"ran": False, "acted": False, "action": "na", "gate": "grounding",
                                 "reason": "the turn stopped to ask the user"}
        profile = self._resolve_gate_profile("grounding")
        # RAG Trust Hardening Wave 1 (E2): when the engine didn't thread its own retrieved
        # chunks (Focus does; module-fed engines don't), merge the knowledge_context
        # module's cached chunks so the corpus-support family covers every engine.
        trace_extra = dict(trace_extra or {})
        if "knowledge_retrieval_event" not in trace_extra and getattr(
                self, "_cw_knowledge_chunks", None):
            trace_extra["knowledge_retrieval_event"] = {"chunks": self._cw_knowledge_chunks}
        # Stash the turn's retrieved chunks (whichever path supplied them) so the outbound
        # exfil guard's X1 whitelist sees the same sources the answer legitimately had.
        try:
            _kre = trace_extra.get("knowledge_retrieval_event") or {}
            if _kre.get("chunks"):
                self._last_retrieved_chunks = _kre["chunks"]
        except Exception:
            pass
        _pclass = self._detect_profile_type() or "tool_enabled"
        _attached_texts = self.attached_material()[0]
        # The findings block the model was SHOWN this turn: a figure the platform itself put
        # in front of the model (a count an earlier turn returned) is vouched for, never an
        # "ungrounded number". The gate stamps its numbers on the persisted event, so the
        # post-hoc scorer reads the same vouched set.
        if "findings_context" not in trace_extra and self._get_findings_context():
            trace_extra["findings_context"] = self._get_findings_context()

        async def _grade(text: str, regrade: bool = False):
            return await _grounding_runtime.run_grounding(
                text,
                kg_enrichment_event=self.kg_enrichment_event,
                profile=profile,
                profile_class=_pclass,
                trace_extra=trace_extra,
                user_uuid=self.user_uuid,
                session_id=self.session_id,
                turn_id=self.current_turn_number,
                query=self.original_user_input or "",
                event_handler=self.event_handler,
                skip_judge=regrade, park=not regrade,   # S7 review: a re-grade is deterministic, never parked
                attached_texts=_attached_texts,
            )

        gated_text, event = await _grade(answer_text)
        # S7 — the Outcome Loop: a revisable deterministic finding earns ONE bounded revision
        # BEFORE delivery, re-graded by the same gate; a clearing revision is delivered clean,
        # a non-clearing one delivers the honest annotated first draft. Off ⇒ the gate's result
        # returned exactly as above (the S7 freeze).
        return await _outcome_loop.run_loop(
            first_text=gated_text, first_event=event, profile=profile, raw_first=answer_text,
            question=self.original_user_input or "", grade=_grade, revise=self._outcome_revise,
            user_uuid=self.user_uuid, session_id=self.session_id, on_event=self._outcome_event)

    async def _outcome_revise(self, prompt: str):
        """S7 — ONE LLM call on the draft + the targeted note (no tools, history disabled: the
        draft carries everything the revision needs). Billed and evented like every call."""
        _cost_before = float(getattr(self, "turn_cost_usd", 0.0) or 0.0)
        text, tin, tout = await self._call_llm_and_update_tokens(
            prompt, reason="Outcome Revision", disabled_history=True, raise_on_error=False,
            system_prompt_override=("You revise your own draft answer to remove or correct exactly what the grounding "
                                    "check named. Keep every supported statement; add no new claims; return only the "
                                    "revised answer."))
        cost = max(0.0, float(getattr(self, "turn_cost_usd", 0.0) or 0.0) - _cost_before)
        return text, tin, tout, cost

    async def _outcome_event(self, ev: dict):
        """S7 — the revision as a Live Status step: recorded in the trace (reload) and pushed
        live over the per-user notification channel (the judge-step / llm_recovery precedent —
        the loop runs inside an awaited seam, not the engine's generator)."""
        try:
            self._log_system_event(ev)
            from trusted_data_agent.core.config import APP_STATE as _AS
            payload = dict(ev); payload["session_id"] = self.session_id
            for q in list(_AS.get("notification_queues", {}).get(self.user_uuid, set())):
                try:
                    q.put_nowait({"type": "outcome_revision", "payload": payload})
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    def _run_guardrail_outbound(self, answer_text: str):
        """Tier 1.3 — the ONE entrypoint the Optimize/Ideate/Focus/Conversation engines call
        for the outbound PII/toxicity guardrail (charter #14). Mirrors
        ``_run_grounding_gate_full`` but is **synchronous** (deterministic, 0-token — no judge,
        no LLM). Delegates to the engine-agnostic ``guardrails.runtime.scan_outbound``; the
        Coordinate/genie engine calls ``scan_outbound`` directly with the same contract.

        Returns ``(answer_text, event)`` — an event on EVERY path, because the trace has to
        answer "did this protection run?". The stamp carries ``ran`` (off vs ran) and ``acted``
        (found nothing vs altered/blocked/parked); a clean scan is ``ran=True, acted=False`` and
        leaves the answer byte-for-byte unchanged. Callers STAMP unconditionally and RENDER only
        on ``acted``, so a clean turn is byte-identical in the trace and the UI (#15/#17)."""
        from trusted_data_agent.agent.guardrails import runtime as _guardrail_runtime
        profile = self._resolve_gate_profile("guardrail_outbound")
        # Wave 3 (X1) whitelist: URLs already present in the turn's sources (retrieved
        # chunks + the user's question) are not novel — they never trigger the exfil
        # guard. Both conditions (novel AND exfil-shaped) are required.
        _sources = [self.original_user_input or ""]
        try:
            _chunks = (getattr(self, "_last_retrieved_chunks", None)
                       or getattr(self, "_cw_knowledge_chunks", None) or [])
            for c in _chunks:
                if isinstance(c, dict) and c.get("content"):
                    _sources.append(str(c["content"]))
        except Exception:
            pass
        return _guardrail_runtime.scan_outbound(
            answer_text, profile=profile,
            profile_class=self._detect_profile_type() or "tool_enabled",
            user_uuid=self.user_uuid, session_id=self.session_id,
            turn_id=self.current_turn_number, source_texts=tuple(_sources))

    def _record_kg_used(self, tool_calls: list) -> None:
        """``used`` KG telemetry (kgstore.usage) — which of the entities the enrichment
        SHOWED the model this turn's data tool calls then REFERENCED. One seam, called
        beside ``_run_obda_scan`` by every data-running engine with the same canonical
        ``[{tool_name, arguments, result}]`` list. Fire-and-forget; never breaks a turn;
        a turn with no enrichment or no tool calls records nothing."""
        try:
            ev = getattr(self, "kg_enrichment_event", None) or {}
            if not tool_calls or not isinstance(ev, dict):
                return
            from trusted_data_agent.kgstore import usage as _usage
            for e in ev.get("enrichments") or []:
                if not isinstance(e, dict):
                    continue
                kid = e.get("kg_id")
                names = e.get("entity_names") or []
                if kid and names:
                    _usage.record_used_async(kid, names, tool_calls)
                    # …and the MISSES: tables the calls read that the enrichment did not
                    # show, bumped on the graph's own table entity so the prior pulls it
                    # forward next time (`enrichment_recall` reads the same rule)
                    self._record_kg_missed(kid, names, tool_calls,
                                           _usage.deployed_names([e]))
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass

    def _record_kg_missed(self, kg_id: str, shown_names: list, tool_calls: list,
                          deployed: list = None) -> None:
        """Resolve the graph's table-entity names (bounded, off the hot path) and record
        the `missed` counter for every table the calls read that the enrichment had not
        shown. Never raises."""
        try:
            from trusted_data_agent.kgstore import usage as _usage
            if not _usage.unshown_tables(shown_names, tool_calls, deployed):
                return                                   # nothing to record: no graph read
            import threading
            from components.builtin.knowledge_graph.graph_store import GraphStore

            def _run():
                try:
                    import asyncio as _aio
                    store = GraphStore(self.active_profile_id, self.user_uuid, kg_id=kg_id)
                    rows = _aio.run(store.list_entities(entity_type="entity", limit=5000)) or []
                    _usage.record_missed_async(kg_id, shown_names, tool_calls,
                                               [str(r.get("name")) for r in rows if r.get("name")],
                                               deployed,
                                               {str(r.get("name")): str((r.get("properties") or {}).get("deployed_table"))
                                                for r in rows if r.get("name") and (r.get("properties") or {}).get("deployed_table")})
                except Exception:  # noqa: BLE001
                    pass
            threading.Thread(target=_run, name="kg-usage-missed-resolve", daemon=True).start()
        except Exception:  # noqa: BLE001
            pass

    _SYNTHESIS_TOOL_NAMES = ("TDA_FinalReport", "TDA_ContextReport", "TDA_LLMTask")

    def _budget_skip(self, group: list):
        """S5 — the group's DATA phases are trimmed to what the turn budget can still afford:
        with a unit exhausted every data phase is dropped; with N steps left only the first N
        data phases of a parallel group run (the group is dispatched under `asyncio.gather`,
        so counting after the run would let the whole group through). A synthesis phase — the
        report — is always kept so the answer is delivered with what was collected. The skip
        is recorded (the trace) and returned as a live event. Returns (kept, event) or None
        when nothing changes."""
        try:
            tb = _turn_budget.current()
            if tb is None:
                return None
            ex = tb.exhausted()
            left = None
            if ex is None and tb.budget.steps is not None:
                left = max(0, int(tb.budget.steps) - int(tb.steps))
            kept, skipped = [], []
            data_seen = 0
            for ph in group:
                tools = ph.get("relevant_tools") or []
                if tools and all(t in self._SYNTHESIS_TOOL_NAMES for t in tools):
                    kept.append(ph)
                    continue
                if ex is not None or (left is not None and data_seen >= left):
                    skipped.append(ph)
                else:
                    kept.append(ph)
                    data_seen += 1
            if not skipped:
                return None
            unit = ex or "steps"
            ev = {"step": "Budget Exhausted — Phase Skipped", "type": "turn_budget",
                  "details": {"exhausted": unit, "skipped_phases": [p.get("phase") for p in skipped],
                              "goals": [str(p.get("goal", ""))[:120] for p in skipped],
                              "summary": f"the turn's {unit} budget is used up — {len(skipped)} phase(s) skipped; "
                                         f"the answer is written from what was collected and says what is left"}}
            self._log_system_event(ev)
            return kept, ev
        except Exception as e:  # noqa: BLE001 — a budget must never break a turn
            app_logger.debug(f"[budget] phase skip check failed: {e}")
            return None

    def _apply_turn_budget_finish(self, final_text: str, final_html: str):
        """S5 — the quiet line under the answer: within budget, or exhausted and finished
        gracefully. No armed budget ⇒ untouched."""
        try:
            st = _turn_budget.stamp_for_turn()
            if not st:
                return final_text, final_html
            line = st.get("finish") or ""
            if not line:
                return final_text, final_html
            text = (final_text or "").rstrip() + f"\n\n_{line}_"
            from trusted_data_agent.agent import answer_surface as _ans
            # an exhausted budget is a finding (warn); a finish within budget is a plain disclosure
            html = _ans.place_note(final_html, _ans.note("budget", line, tone="warn" if st.get("exhausted") else "muted"))
            return text, html
        except Exception:  # noqa: BLE001
            return final_text, final_html

    def _apply_model_gap(self, final_text: str, final_html: str):
        """PROP-008 — the ONE method every engine calls to deliver a model gap: when the
        turn's knowledge-graph enrichment found that the question's subject is not in the
        model, the platform's note (and, when this agent may author, the act that proposes
        it) goes under the answer — decided in code, never left to the model to offer
        (``agent/model_gap.py``). No gap ⇒ untouched."""
        try:
            from trusted_data_agent.agent import model_gap as _mg
            text, html, gap = _mg.apply(final_text, final_html, getattr(self, "kg_enrichment_event", None))
            self._model_gap_event = gap
            return text, html
        except Exception:  # noqa: BLE001 — delivery must never break a turn
            return final_text, final_html

    async def _load_attachment_sources(self):
        """S9 — rebuild this turn's sources from the upload manifest (extracted ONCE at upload),
        register them for the turn, register every verified table as an S3 result-set
        statement, and — for an image nothing could read, when the profile's
        ``attachmentConfig.transcribeImages`` is on and the provider sees images — ask the
        model for a transcription, recorded UNVERIFIED. Returns ``(document_text_to_append,
        live_event | None)``: the text is the image sources' rows (documents' text already
        rides the document context); native-image providers otherwise never see the rows the
        platform vouches for. ``(None, None)`` when off or nothing was attached."""
        from trusted_data_agent.llm import image_sources as _isrc
        if not _isrc.enabled() or not self.attachments:
            return None, None
        extra_parts = []
        for src, entry in _isrc.manifest_entries(self.user_uuid, self.session_id, self.attachments):
            if not src.usable and src.kind == "image" and self._attachment_transcription_enabled():
                src = await self._transcribe_image_source(src, entry)
            _isrc.add(src)
            if src.usable and src.kind == "image":
                # an image routed NATIVELY carries no text yet — append the platform's reading;
                # a text-only provider already has it (the upload replaced the placeholder)
                blk = _isrc.document_text_for(src)
                if blk and blk not in (self.document_context or ""):
                    extra_parts.append(blk)
        _isrc.register_tables(self.session_id)
        stamp = _isrc.stamp_for_turn()
        if not stamp:
            return None, None
        event = {"sources": [{k: v for k, v in s_.items() if k not in ("text",)} for s_ in stamp["sources"]],
                 "verified": stamp["verified"], "unverified": stamp["unverified"], "none": stamp["none"]}
        return ("\n\n".join(p for p in extra_parts if p) or None), event

    def _attachment_transcription_enabled(self) -> bool:
        try:
            prof = self._get_profile_config() or {}
            cfg = prof.get("attachmentConfig") or {}
            return bool(cfg.get("transcribeImages") is True)
        except Exception:  # noqa: BLE001
            return False

    async def _transcribe_image_source(self, src, entry: dict):
        """The vision fallback: ONE bounded call with the image block; the reply (pipe rows or
        prose) becomes an UNVERIFIED source. Any failure keeps the unread source as it was."""
        from trusted_data_agent.llm import image_sources as _isrc
        from trusted_data_agent.llm.document_upload import DocumentUploadConfig, DocumentUploadCapability
        try:
            cap = DocumentUploadConfig.get_capability(self.current_provider, self.current_model)
            if cap not in (DocumentUploadCapability.NATIVE_FULL, DocumentUploadCapability.NATIVE_VISION_ONLY):
                return src
            from trusted_data_agent.filestore import session_files
            data = session_files.read_bytes(self.user_uuid, self.session_id, src.file_id)
            if data is None:
                return src
            block = {"type": "image", "data": data, "mime_type": entry.get("content_type") or "image/png", "filename": src.filename}
            prompt = (f"Transcribe the content of the attached image '{src.filename}' exactly as it appears. "
                      "If it holds a table, return it as pipe-delimited rows with the header first "
                      "(cells separated by ' | ', one row per line, no commentary). Otherwise return the text verbatim. "
                      "Do not add, estimate or summarise anything.")
            # its own minimal system prompt — under the agent's master prompt (a capability
            # catalog) the model answers a transcription request with a tool-call JSON
            text, _in, _out = await self._call_llm_and_update_tokens(
                prompt, "Transcribing an attached image (S9 vision fallback)", disabled_history=True,
                system_prompt_override=("You transcribe images exactly as they appear. Reply with ONLY the transcription: "
                                        "pipe-delimited rows for a table (header first), plain text otherwise. "
                                        "No JSON, no tool calls, no commentary, no estimates."),
                strip_capabilities=True, multimodal_content=[block], raise_on_error=False)
            return _isrc.transcription_source(src.file_id, src.filename, text or "", width=src.width, height=src.height)
        except Exception as e:  # noqa: BLE001
            app_logger.debug(f"[attachment-sources] transcription skipped for {src.filename}: {e}")
            return src

    def _turn_vouched_numbers(self) -> set:
        """The numbers this turn's inputs account for — `RunRecord.vouched_numbers`, the ONE
        definition the grounding gate and `numeric_fidelity` read — built from what the
        executor already holds (the question, the session's earlier user turns, the KG
        enrichment). Empty on any failure: a figure check then judges as it did before."""
        try:
            from trusted_data_agent.eval.types import RunRecord
            from trusted_data_agent.agent.grounding.adapter import session_context_text
            _prev = getattr(self, "previous_turn_data", None) or {}
            return RunRecord({
                "user_query": getattr(self, "original_user_input", "") or "",
                "context_text": session_context_text(_prev.get("workflow_history") if isinstance(_prev, dict) else None),
                "kg_enrichment_event": getattr(self, "kg_enrichment_event", None),
            }).vouched_numbers()
        except Exception:  # noqa: BLE001
            return set()

    def _component_enabled_for_turn(self, component_id: str):
        """Is this component enabled for the agent answering this turn? The one enablement
        rule (`ComponentManager.get_active_components`), for platform-written text that names
        a component's act — a note or a context line must not advise a tool the agent does
        not have. ``None`` when it cannot be resolved (the caller keeps its default)."""
        try:
            from trusted_data_agent.components.manager import get_component_manager
            _pcfg = self._get_profile_config() or {}
            _active = get_component_manager().get_active_components(_pcfg, getattr(self, "user_uuid", None))
            return component_id in {getattr(c, "component_id", c) for c in _active}
        except Exception:  # noqa: BLE001
            return None

    def _run_compute_lineage(self, final_text: str, final_html: str):
        """S3 — Compute with Lineage, the end-of-turn seam (charter #14: every data-running
        engine calls it after the honesty gate, before the save + final_answer yield).

        ONE assessment of the delivered answer against the turn's compute receipts and result
        sets (`compute_lineage.assess`): every figure is receipted, quoted or unreceipted. From
        it: the receipts are sealed into the provenance chain as `compute_step` (the same
        canonical content the verifier re-derives), the rendered answer gains its inline
        lineage marks, and an unreceipted figure is disclosed under an ACTING honesty mode
        (annotate/enforce/review — append-only, the answer is never rewritten) or only
        flagged under observe. Returns ``(text, html, event | None)``; stamped
        ``turn_data['compute_lineage']`` for reload (#17). Off ⇒ ``(text, html, None)`` —
        byte-identical. Never breaks a turn."""
        try:
            from trusted_data_agent.agent import result_sets as _rs
            if not _rs.enabled():
                return final_text, final_html, None
            from trusted_data_agent.agent import compute as _cmp
            from trusted_data_agent.agent import compute_lineage as _lin
            receipts = _rs.receipts(self.session_id)
            statements = _rs.statements(self.session_id)
            if not receipts and not statements:
                return final_text, final_html, None
            # the graph's declared value domains: a code the answer names ('120') traces to
            # the ontology even when the statement filtered on the label
            _vd = None
            try:
                _enr = ((getattr(self, "kg_enrichment_event", None) or {}).get("enrichments") or [None])[0]
                _vd = _enr.get("value_domains") if isinstance(_enr, dict) else None
            except Exception:  # noqa: BLE001
                _vd = None
            event = _lin.assess(final_text or "", receipts, statements, query=self.original_user_input or "",
                                value_domains=_vd, vouched_numbers=PlanExecutor._turn_vouched_numbers(self))
            # seal every receipt ONCE, in the order it was produced
            prov = getattr(self, "provenance", None)
            sealed = 0
            if prov is not None:
                for r in receipts:
                    try:
                        prov.add_step("compute_step", _cmp.receipt_content(r), _cmp.describe(r))
                        sealed += 1
                    except Exception:  # noqa: BLE001
                        pass
            event["sealed"] = sealed
            # PROP-006 — on a turn that read more than one source or combined, name them: which
            # statement came from where, when, and what each combine read. Absent otherwise, so
            # a single-source turn's event is exactly what it was.
            try:
                from trusted_data_agent.core.source_endpoints import turn_sources
                _src = turn_sources(statements, getattr(self, "user_uuid", None))
                if _src:
                    event["sources"] = _src
            except Exception:  # noqa: BLE001
                pass
            from trusted_data_agent.agent.honesty import adapter as _hadapt
            mode = _hadapt.resolve_mode(self._resolve_gate_profile("compute_lineage"))
            event["mode"] = mode
            event["acted"] = False
            event["disclosure_appended"] = False
            # the note may only advise an act this agent can perform
            _has_compute = PlanExecutor._component_enabled_for_turn(self, "compute")
            if _has_compute is not None:
                event["compute_available"] = _has_compute
            text, html = final_text, final_html
            if event.get("decision") == "note" and mode in ("annotate", "enforce"):
                note = _lin.disclosure(event)
                text = (final_text or "").rstrip() + "\n\n" + note
                event["acted"] = True
                event["disclosure_appended"] = True
                event["note"] = note
            html = _lin.mark_html(html, event, turn_id=self.current_turn_number)
            if event.get("disclosure_appended"):
                from trusted_data_agent.agent import answer_surface as _ans
                html = _ans.place_note(html, _lin.note_html(event))
            # a quoted figure's rows must survive into the persisted record (the lineage dialog
            # opens on them after a reload) — mark its statements before the turn persists
            try:
                from trusted_data_agent.agent import result_sets as _rs
                _rs.mark_quoted(self.session_id, [q.get("statement") for q in (event.get("quoted") or [])]
                                + [g.get("statement") for g in (event.get("derived") or [])])
            except Exception:  # noqa: BLE001
                pass
            self._compute_lineage_event = event
            return text, html, event
        except Exception as e:  # noqa: BLE001 — observability must never break a turn
            app_logger.debug(f"[compute-lineage] skipped: {e}")
            return final_text, final_html, None

    def _run_obda_scan(self, tool_calls: list):
        """OBDA-READ (Ontology-Based Data Access) — the ONE method every engine calls to make the
        governed-data lane observable (charter #14, all engines; OBDA_VISION.md §5). Pure,
        deterministic, 0-token: classifies the turn's data operations into **governed**
        (compile_metric) vs **free-formed** (raw SQL) and returns a Live Status event, or ``None``
        when the turn did no data operations (abstain — no card). ``tool_calls`` is the canonical
        ``[{tool_name, arguments, result}]`` shape (each engine builds it from its own trace via
        ``obda.calls_from_trace`` / ``obda.calls_from_conversation_events``). The seam yields the
        event live and stamps ``turn_data['obda']`` for reload (#17). Never breaks a turn."""
        # The OBDA-READ component off for this agent (the component chain), or Option A →
        # no event, no `turn_data['obda']` stamp, so `_read_obda` reads `na` (structurally
        # absent) and GROUNDED never sees the advisory warn — profile_obda answers both.
        try:
            # Option A (per-agent): the ring must read as pre-OBDA-READ for this
            # agent — no lane stamp, no card, governed_data reads na.
            from trusted_data_agent.agent.obda import profile_obda as _pobda
            if _pobda(self._resolve_gate_profile("obda"))[0] == "off":
                return None
        except Exception:  # noqa: BLE001
            pass
        from trusted_data_agent.agent import obda as _obda
        try:
            try:
                from trusted_data_agent.core import action_perform as _act_perf
                _acts = _act_perf.turn_actions()
            except Exception:  # noqa: BLE001
                _acts = []
            ev = _obda.build_event(_obda.scan_obda(tool_calls or [], action_records=_acts))
        except Exception:
            return None  # observability must never break a turn
        # O2 (scope §9) — seal each governed consumption into the turn's Ed25519
        # provenance chain as an `obda.consumption` step. The sealed content is
        # `obda.receipt_content(op)` — the SAME function offline verification
        # re-derives from the stamped event, so receipt and check cannot drift.
        # Fail-open: a sealing failure loses a receipt, never the turn.
        try:
            chain = getattr(self, "provenance", None)
            if ev and chain is not None and not getattr(chain, "_sealed", True):
                for op in _obda.consumption_receipts(ev):
                    rows = op.get("row_count")
                    chain.add_step(
                        "obda.consumption", _obda.receipt_content(op),
                        f"data product {op.get('product')} v{op.get('version')} · "
                        f"{'?' if rows is None else rows} row(s) · "
                        f"{op.get('trust_state') or 'unknown'}")
        except Exception:
            pass
        # E5 (scope §7.2) — the empirical monitor for structural confinement.
        # A SURFACE-ONLY agent executing free-formed raw SQL is not an advisory
        # statistic: structural confinement made that impossible, so a nonzero
        # freeform count here means the confinement LEAKED — a hole report,
        # recorded as a danger incident (Assurance Inbox) + broadcast live.
        try:
            self._record_surface_breach(ev)
        except Exception:
            pass
        return ev

    def _record_surface_breach(self, ev):
        """E5 — record a confinement breach: the ambient principal is surface-only
        (exclusive, not advisory) yet this turn EXECUTED free-formed raw SQL.
        Deterministic, fail-open, fires only on the breach (never a routine event)."""
        if not ev or not int(ev.get("freeform_count") or 0):
            return
        from trusted_data_agent.core import agent_authority as _aa
        p = _aa.get_current_principal()
        if p is None:
            return
        sc = p.scope
        if not (sc.data_surface_exclusive and not sc.data_surface_advisory):
            return
        from trusted_data_agent.core import trust_events as _te
        n = int(ev.get("freeform_count") or 0)
        summary = (f"SURFACE BREACH: a surface-only agent executed {n} free-formed "
                   f"raw-SQL operation(s) this turn — structural confinement (E1) "
                   f"should have made this impossible; treat as a hole report, not "
                   f"agent misbehaviour")
        _te.record_event(
            self.user_uuid, "surface_breach", summary, severity="danger",
            session_id=self.session_id,
            turn_id=getattr(self, "current_turn_number", None),
            payload={"freeform_count": n,
                     "products": sorted(sc.data_surface_products or ())})
        try:
            _te.broadcast_notice(self.user_uuid, "surface_breach", summary,
                                 severity="danger")
        except Exception:
            pass

    async def _run_honesty_gate(self, answer_text: str, tool_calls: list, support_text: str = None):
        """Honesty post-condition (charter #14, all data-running engines). Deterministic,
        0-token: when the answer asserts data/entity NON-EXISTENCE but every raw-SQL data
        query this turn FAILED (none succeeded), the false negative is flagged
        (observe/annotate) or rewritten to an honest retrieval-failure (enforce). Abstains on
        legitimate empty results (a query that succeeded with zero rows) and on turns with no
        raw-SQL data queries (Ideate/Focus) → byte-identical baseline (#15). Returns
        ``(text, event | None)`` — event ``None`` & answer unchanged on abstain/off/error
        (fails open). ``tool_calls`` is the canonical ``[{tool_name, arguments, result}]`` shape
        (same as OBDA-READ). Stamped ``turn_data['honesty']`` for reload (#17)."""
        from trusted_data_agent.agent.honesty import runtime as _honesty_runtime
        # PROP-011: under `enforce` this gate REPLACES the answer — never the question a turn
        # stopped to ask. Abstains, byte-identical.
        from trusted_data_agent.agent import input_requests as _ir_mod
        if _ir_mod.pending() is not None:
            return answer_text, None
        profile = self._resolve_gate_profile("honesty")
        # Async seam: the sync gate + the OPTIONAL grain_faithfulness judge
        # confirmation (honestyConfig.grainJudge). `query` powers the REJECT-free
        # grain trigger — a plan-time silent substitution leaves no runtime REJECT
        # for the deterministic path to see (session 7b9b5a25).
        return await _honesty_runtime.run_honesty_async(
            answer_text, tool_calls, profile=profile,
            profile_class=self._detect_profile_type() or "tool_enabled",
            user_uuid=self.user_uuid, session_id=self.session_id,
            turn_id=self.current_turn_number,
            query=self.original_user_input or "",
            event_handler=self.event_handler,
            bridges=self._enrichment_class_bridges(),
            support_text=support_text)

    def _enrichment_class_bridges(self) -> list:
        """The declared class-level correspondences the KG enrichment stamped this turn
        (``enrichments[*].class_bridges`` — the SAME list the preflight join guard
        records), read for the honesty gate's overreach check. Empty when no graph is
        bound or the graph declares none; never raises."""
        try:
            ev = getattr(self, "kg_enrichment_event", None) or {}
            out = []
            for enr in (ev.get("enrichments") or []) if isinstance(ev, dict) else []:
                if isinstance(enr, dict) and isinstance(enr.get("class_bridges"), list):
                    out.extend(b for b in enr["class_bridges"] if isinstance(b, dict))
            return out
        except Exception:
            return []

    def _run_honesty_gate_rag(self, answer_text: str, retrieval_info: dict = None):
        """RAG Trust Hardening Wave 1 §1.3 — retrieval-sufficiency honesty (the knowledge
        twin of ``_run_honesty_gate``). When knowledge retrieval RAN this turn and found
        (near-)nothing yet the answer asserts specifics, flag/annotate/replace per the
        profile's ``honestyConfig.mode``. ``retrieval_info`` = the engine's own
        ``{searched, docs_retrieved/qualifying, top_score}`` (Focus passes its results);
        None ⇒ the knowledge_context module's cached outcome is used (module-fed engines).
        Abstains (byte-identical) when retrieval never ran → chat turns are never flagged.
        Returns ``(text, event | None)``; stamp ``turn_data['honesty']`` for reload (#17)."""
        from trusted_data_agent.agent.honesty import runtime as _honesty_runtime
        info = retrieval_info or getattr(self, "_cw_knowledge_retrieval", None)
        if not info:
            return answer_text, None
        from trusted_data_agent.agent import input_requests as _ir_mod
        if _ir_mod.pending() is not None:   # PROP-011: a question is not an unsupported claim
            return answer_text, None
        profile = self._resolve_gate_profile("honesty_rag")
        # the turn's attachments are material the answer may legitimately draw on: a claim
        # they carry is not "unsupported by the knowledge base", it was never meant to be
        support_texts, unreadable = self.attached_material()
        return _honesty_runtime.run_honesty_rag(
            answer_text,
            qualifying_chunks=int(info.get("qualifying",
                                           info.get("docs_retrieved") or 0) or 0),
            top_score=float(info.get("top_score") or 0.0),
            scores=tuple(info.get("scores") or ()),
            searched=bool(info.get("searched")),
            support_texts=support_texts,
            unreadable_material=unreadable,
            profile=profile,
            profile_class=self._detect_profile_type() or "rag_focused",
            user_uuid=self.user_uuid, session_id=self.session_id,
            turn_id=self.current_turn_number)

    def _run_citation_gate(self, answer_text: str, tool_calls: list = None,
                           events: list = None):
        """F-001 — CITED-BUT-UNREAD (progressive-disclosure skills, charter #14).

        The third honesty branch, and the only one that runs on EVERY engine that can
        reach a library: an agent asserting *"I have fetched `master-design`"* without
        calling the tool has claimed provenance it does not have, and nothing else in
        the platform contradicts it.

        Engine-agnostic by taking BOTH evidence shapes — the canonical
        ``{tool_name, arguments, result}`` calls (Optimize, ReAct) and the collected
        event list carrying the handler's own ``skill_section_loaded`` (every engine).
        Each seam passes what it holds; the union is the read set.

        Abstains byte-identically (returns the answer unchanged, no event) when the
        profile carries no progressive library — which is every profile that has not
        opted into one, so the baseline is untouched (#15). Stamped
        ``turn_data['honesty_citation']`` for reload (#17) — its own key, because the
        SQL/RAG branches already own ``turn_data['honesty']`` and a turn runs both.
        """
        from trusted_data_agent.agent.honesty import runtime as _honesty_runtime
        self._library_use = None
        try:
            from components.builtin.skill_library.handler import (
                known_anchors_for_profile, closed_vocabulary_for_profile,
                library_indexes_for_profile)
        except Exception:                       # component absent → nothing to check
            return answer_text, None
        profile = self._resolve_gate_profile("honesty_citation")
        # Read → ran (SKILL_ARCHITECTURE §4a): did the agent open the section before it ran
        # that section's SQL, and did the SQL run? A MEASUREMENT, never an act — stamped
        # `turn_data['library_use']` for the scorer and the per-section aggregate. Same seam
        # as the citation check because it needs the same two things: the profile's library
        # and the turn's calls in order. Absent without a library (#15). Never raises.
        try:
            from trusted_data_agent.skills import library_use as _library_use
            _indexes = library_indexes_for_profile(profile or {})
            if _indexes:
                self._library_use = _library_use.record(tool_calls or [], _indexes)
        except Exception as _e:  # noqa: BLE001
            app_logger.debug(f"library_use record skipped: {_e}")
        anchors = known_anchors_for_profile(profile or {})
        vocab, valid_ids, vocab_label = closed_vocabulary_for_profile(profile or {})
        if not anchors and not vocab:
            return answer_text, None
        return _honesty_runtime.run_honesty_citation(
            answer_text, tool_calls or [],
            known_anchors=anchors, events=events or [],
            vocabulary=vocab, valid_identifiers=valid_ids,
            vocabulary_label=vocab_label,
            profile=profile,
            profile_class=self._detect_profile_type() or "llm_only",
            user_uuid=self.user_uuid, session_id=self.session_id,
            turn_id=self.current_turn_number)

    def _run_guardrail_inbound(self, text: str):
        """Tier 1.3 increment 2b — inbound prompt-injection guardrail (charter #14, all engines).
        Runs at the shared dispatch entry in ``run()`` BEFORE any engine, so one call covers
        every profile class. Synchronous, deterministic, 0-token. Returns ``(text, event|None)``
        — in enforce a detected injection BLOCKs (``text`` is the refusal); observe/annotate keep
        the text. Fails open."""
        from trusted_data_agent.agent.guardrails import runtime as _guardrail_runtime
        profile = self._resolve_gate_profile("guardrail_inbound")
        return _guardrail_runtime.scan_inbound(
            text, profile=profile,
            profile_class=self._detect_profile_type() or "tool_enabled",
            user_uuid=self.user_uuid, session_id=self.session_id,
            turn_id=self.current_turn_number)

    #: gate key (as the mechanism records it) -> the dial's event type. A gate absent here
    #: is simply not drawn live; the authoritative payload still carries it at the end.
    _PROGRESS_STATION = {
        "preflight_sql": "grounding_gate", "grounding_gate": "grounding_gate",
        "honesty_gate": "honesty", "corpus_quarantine": "grounding_gate",
        "tool_authority": "guardrail", "tool_enforcement": "guardrail",
        "outbound_guard": "guardrail", "inbound_guard": "guardrail",
        "retrieval_firewall": "guardrail", "governed_data": "obda",
    }

    def _drain_assurance_progress(self):
        """SSE for every mechanism that has reported since the last drain.

        The mechanisms already record that they ran — that is what the tally is for — and
        until now nobody read it before the turn ended, which is why a guard running
        mid-turn was invisible. This reports what the mechanism ITSELF recorded; it never
        infers from a tool call returning cleanly, because that inference is false the
        moment a mechanism is switched off or swallows an exception.

        Draws no card and is not persisted: progress, not trace.
        """
        out = []
        try:
            from trusted_data_agent.agent import assurance_tally as _tally
            for row in _tally.drain_new():
                mech = self._PROGRESS_STATION.get(row.get("gate"))
                if not mech:
                    continue
                out.append(self._format_sse_with_depth({
                    "step": row["gate"], "type": "assurance_progress",
                    "details": {"mechanism": mech, "ran": row["ran"],
                                "acted": row["acted"],
                                "action": "acted" if row["acted"] else "pass"},
                }))
        except Exception:  # noqa: BLE001 — progress must never break a turn
            return []
        return out

    async def _with_progress(self, agen):
        """Wrap an engine's event stream, interleaving assurance progress.

        ONE wrapper at the dispatch seam rather than a drain beside every yield: the
        engines differ, and a per-site drain is a rule five files have to remember.
        Progress therefore arrives at the NEXT event the engine emits — milliseconds on a
        tool-heavy turn, and honestly not instantaneous.
        """
        async for event in agen:
            for ev in self._drain_assurance_progress():
                yield ev
            yield event
        for ev in self._drain_assurance_progress():
            yield ev

    async def _emit_inbound_guardrail_block(self, event: dict):
        """Self-contained terminal for an inbound-guardrail BLOCK — runs at the dispatch entry,
        bypassing every engine (the injected turn is never sent to the LLM). Emits the guardrail
        event + the refusal as the final answer, persists the assistant turn and a minimal
        ``workflow_history`` turn (verdict in ``system_events`` for reload + ``guardrail_inbound``
        stamp for audit, #17). Zero tokens/cost. Uses the shared ``adapter.build_block_turn`` so
        the refusal turn is byte-identical to the genie path. Fails open (a save error must not
        hang the turn — the refusal is still yielded)."""
        from trusted_data_agent.agent.guardrails import adapter as _ga
        final_html, gr_event, final_answer, turn_summary = _ga.build_block_turn(
            user_input=self.original_user_input, event=event,
            profile_tag=self._get_current_profile_tag(),
            profile_type=self._detect_profile_type() or "tool_enabled",
            turn_number=self.current_turn_number, session_id=self.session_id,
            duration_ms=self._turn_duration_ms())
        self._log_system_event(gr_event)
        yield self._format_sse_with_depth(gr_event)
        try:
            await session_manager.add_message_to_histories(
                self.user_uuid, self.session_id, "assistant",
                content=turn_summary["final_summary"], html_content=final_html,
                is_session_primer=self.is_session_primer)
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"[Guardrail/inbound] history save failed: {e}")
        final_answer.update({"source": self.source, "is_session_primer": self.is_session_primer})
        yield self._format_sse_with_depth(final_answer, "final_answer")
        try:
            turn_summary.update({"provider": self.current_provider, "model": self.current_model,
                                 "task_id": getattr(self, "task_id", None)})
            await session_manager.update_last_turn_data(
                self.user_uuid, self.session_id, turn_summary)
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"[Guardrail/inbound] turn save failed: {e}")
        self.state = self.AgentState.DONE

    def _profile_llm_baseline(self) -> tuple:
        """(provider, model) of the client this executor falls back to when it builds none.

        That client (``STATE['llm']`` / the per-user instance) was built from the user's DEFAULT
        profile, so its LLM configuration is the baseline. The per-user provider is NOT: a
        ``profile_id`` override writes it to the override's own provider while leaving the
        client alone, so comparing against it read "same LLM" and no matching client was built
        — the outcome revision then called the default Google client as OpenRouter (trust
        campaign 3). The per-user values are the fallback only when the default can't be read."""
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            cm = get_config_manager()
            pid = cm.get_default_profile_id(self.user_uuid)
            prof = cm.get_profile(pid, self.user_uuid) if pid else None
            cfg_id = (prof or {}).get("llmConfigurationId")
            if cfg_id:
                cfg = next((c for c in cm.get_llm_configurations(self.user_uuid) if c.get("id") == cfg_id), None)
                if cfg and cfg.get("provider"):
                    return cfg.get("provider"), cfg.get("model")
        except Exception:  # noqa: BLE001 — the baseline must never break a turn
            pass
        return get_user_provider(self.user_uuid), get_user_model(self.user_uuid)

    async def _call_llm_and_update_tokens(self, prompt: str, reason: str, system_prompt_override: str = None, raise_on_error: bool = False, disabled_history: bool = False, active_prompt_name_for_filter: str = None, source: str = "text", multimodal_content: list = None, planning_phase: str = None, current_provider: str = None, current_model: str = None, strip_capabilities: bool = False, on_delta=None, cache_prefix: str = None) -> tuple[str, int, int]:
        """
        A centralized wrapper for calling the LLM that handles token updates.

        ``cache_prefix``: the leading part of ``prompt`` that is identical on every call of
        this kind for this agent (the cache plan's stable prefix — PROMPT_CACHE_ARCHITECTURE.md).

        Args:
            planning_phase: Optional string indicating which phase of planning this call belongs to.
                           Valid values: "strategic" | "tactical" | "conversation"
            current_provider: Optional override for LLM provider (for dual-model support)
            current_model: Optional override for LLM model (for dual-model support)
        """
        # ── the in-flight budget cap (symmetry map Part D) ──────────────────────
        # `max_cost_per_run` was enforced POST-HOC only: the overrun was flagged once the
        # money was spent. Asked here, before the call is paid for, using the SAME
        # predicate as the post-hoc record — a cap that could be read two ways is a cap
        # nobody can reason about.
        #
        # Autonomous turns only (that is what the scope governs) and only when a cap is
        # configured, so an interactive turn and an uncapped agent are byte-identical.
        # Raises rather than returning empty: a silent stop mid-turn is indistinguishable
        # from a model that had nothing to say, and the engines' error path produces an
        # honest turn that names the cause.
        try:
            from trusted_data_agent.core import agent_authority as _aid
            _ok, _why = _aid.check_budget_before_call(
                float(getattr(self, "turn_cost_usd", 0.0) or 0.0))
        except Exception:  # noqa: BLE001 — a guard must never break a turn
            _ok, _why = True, ""
        if not _ok:
            app_logger.warning(f"agent-authority: LLM call refused — {_why}")
            raise AgentBudgetExceededError(
                f"This autonomous run stopped before completing: {_why}. "
                f"The work done so far is preserved; raise the per-run budget on the "
                f"agent's authority settings to let it finish.")

        final_disabled_history = disabled_history or self.disabled_history

        # Use explicitly passed provider/model if provided (dual-model feature)
        # Otherwise fall back to instance defaults
        effective_provider = current_provider if current_provider else self.current_provider
        effective_model = current_model if current_model else self.current_model

        # **NEW: Select LLM instance based on planning_phase for dual-model**
        # When dual-model is active, use strategic/tactical instances based on planning phase.
        # Otherwise, use profile-specific or global instance.
        if self.is_dual_model_active and planning_phase:
            if planning_phase == "strategic" and self.strategic_llm_instance:
                llm_instance = self.strategic_llm_instance
                app_logger.debug(f"[Dual-Model] Using strategic LLM instance for {effective_provider}/{effective_model}")
            elif planning_phase == "tactical" and self.tactical_llm_instance:
                llm_instance = self.tactical_llm_instance
                app_logger.debug(f"[Dual-Model] Using tactical LLM instance for {effective_provider}/{effective_model}")
            else:
                # Fallback if instances weren't created
                llm_instance = self.profile_llm_instance if self.profile_llm_instance else self.dependencies['STATE']['llm']
        else:
            # Single-model mode: Use profile-specific LLM instance when available (e.g., RAG profile with Friendli
            # while default profile uses Google). Falls back to global instance.
            llm_instance = self.profile_llm_instance if self.profile_llm_instance else self.dependencies['STATE']['llm']
        _llm_coro = llm_handler.call_llm_api(
            llm_instance, prompt,
            user_uuid=self.user_uuid, session_id=self.session_id,
            dependencies=self.dependencies, reason=reason,
            system_prompt_override=system_prompt_override, raise_on_error=raise_on_error,
            disabled_history=final_disabled_history,
            active_prompt_name_for_filter=active_prompt_name_for_filter,
            source=source,
            active_profile_id=self.active_profile_id,
            current_provider=effective_provider,
            current_model=effective_model,
            multimodal_content=multimodal_content,
            thinking_budget=self.thinking_budget,
            strip_capabilities=strip_capabilities,
            on_delta=on_delta,
            cache_prefix=cache_prefix,
        )
        # The limit is on the provider's SILENCE, not on the length of the call: a streamed
        # answer that is still arriving is not cut at `_timeout`; a call that streams nothing
        # is cut `_timeout` seconds after it started, exactly as before.
        _timeout = APP_CONFIG.LLM_CALL_TIMEOUT_SECONDS
        _ceiling = getattr(APP_CONFIG, "LLM_STREAM_MAX_SECONDS", 0)
        _outer, _outer_ceiling = _timeout, _ceiling
        if llm_handler._resilience_enabled() and _timeout:
            # The envelope limits each ATTEMPT itself (handler `_attempt_limit`) and retries or
            # falls back on a cut. This limit is then only the backstop for a defect: wide
            # enough for the envelope's worst case — two silent attempts, the longest backoff,
            # a silent fallback — so it can never cut a call the envelope is still handling.
            _outer = _timeout * (_resilience.TIMEOUT_MAX_ATTEMPTS + 1) + _resilience.MAX_BACKOFF_SECONDS
            _outer_ceiling = (_ceiling * (_resilience.TIMEOUT_MAX_ATTEMPTS + 1)) if _ceiling else 0
        try:
            response_text, statement_input_tokens, statement_output_tokens, actual_provider, actual_model = \
                await _streaming.await_with_activity_timeout(_llm_coro, silence=_outer, ceiling=_outer_ceiling)
        except asyncio.TimeoutError as _timed_out:
            _why = getattr(_timed_out, "reason", "no_response")
            app_logger.error(
                f"LLM call timed out ({_why}) after {getattr(_timed_out, 'elapsed', _timeout):.0f}s "
                f"[reason='{reason}', provider={effective_provider}, model={effective_model}]"
            )
            if _why == "stalled":
                raise RuntimeError(
                    f"The AI provider stopped sending its answer for {_timeout} seconds. "
                    "It may be overloaded — please try again in a moment."
                )
            if _why == "ceiling":
                raise RuntimeError(
                    f"The answer was still being written after {_ceiling} seconds and was stopped. "
                    "Ask for a shorter answer, or split the request."
                )
            raise RuntimeError(
                f"The AI provider did not respond within {_timeout} seconds. "
                "It may be overloaded — please try again in a moment."
            )
        except _resilience.ProviderRefusal as _refusal:
            # S4 — a provider refusal is an HONEST TURN, never a canned "technical error": the
            # delivered answer names the refusal category; it is gated, sealed and priced like
            # any other answer (0 tokens — nothing was generated). The step was recorded by the
            # handler; `_refused_call` lets the engine mark the turn.
            response_text = _resilience.refusal_answer(_refusal.category, _refusal.provider, _refusal.model)
            statement_input_tokens, statement_output_tokens = 0, 0
            actual_provider, actual_model = _refusal.provider, _refusal.model
            self._refused_call = {"category": _refusal.category, "provider": _refusal.provider, "model": _refusal.model}
            app_logger.warning(f"[resilience] {_refusal.provider}/{_refusal.model} refused ({_refusal.category}); honest answer delivered")
        self.llm_debug_history.append({"reason": reason, "response": response_text})
        app_logger.debug(f"LLM RESPONSE (DEBUG): Reason='{reason}', Response='{response_text}'")

        self.turn_input_tokens += statement_input_tokens
        self.turn_output_tokens += statement_output_tokens
        _turn_budget.record_tokens(statement_input_tokens + statement_output_tokens)   # S5: the tokens unit

        # Store planning phase and model info for potential event emission
        # (actual event emission happens in callers that have access to event handlers)
        # Calculate per-call cost for centralized access by all callers
        from trusted_data_agent.core.cost_manager import get_cost_manager
        cost_manager = get_cost_manager()
        call_cost_usd = cost_manager.calculate_cost(
            provider=actual_provider or "Unknown",
            model=actual_model or "Unknown",
            input_tokens=statement_input_tokens,
            output_tokens=statement_output_tokens
        )
        # Provider-side prompt caching (S2 — one rule, `calculate_cost_with_cache`): the
        # cached prefix is billed at the provider's cached-read rate, an explicit cache
        # write at its surcharge. Read from the handler's task-local side channel so the
        # figure is exact per call; no reading ⇒ the plain cost above, untouched.
        _cached_tokens = 0
        _cache_write_tokens = 0
        _cache_ttl = "5m"
        try:
            _lcu = llm_handler.LAST_CALL_USAGE.get({}) or {}
            _cached_tokens = int(_lcu.get("cached_tokens") or 0)
            _cache_write_tokens = int(_lcu.get("cache_write_tokens") or 0)
            _cache_ttl = str(_lcu.get("cache_ttl") or "5m")
        except Exception:  # noqa: BLE001
            _cached_tokens = _cache_write_tokens = 0
        if (_cached_tokens or _cache_write_tokens) and call_cost_usd:
            call_cost_usd = cost_manager.calculate_cost_with_cache(
                actual_provider or "Unknown", actual_model or "Unknown",
                statement_input_tokens, statement_output_tokens,
                cached_tokens=_cached_tokens, cache_write_tokens=_cache_write_tokens, ttl=_cache_ttl)
        self.turn_cached_tokens = getattr(self, "turn_cached_tokens", 0) + _cached_tokens
        self.turn_cache_write_tokens = getattr(self, "turn_cache_write_tokens", 0) + _cache_write_tokens
        # turn_cost dual-model fix (§3.2-A): accumulate the ACCURATE per-call cost (priced at this
        # call's real model) so turn_cost = Σ per-call costs — not a single-model re-price of the
        # summed tokens. Covers every Optimize/Ideate/Focus main + utility + planner LLM call.
        self.turn_cost_usd += (call_cost_usd or 0.0)

        self._last_call_metadata = {
            "planning_phase": planning_phase,
            "provider": actual_provider,
            "model": actual_model,
            # S4: the call was served by the SIGNED fallback, not the configured model
            "served_by_fallback": bool(actual_model and effective_model and (actual_provider, actual_model) != (effective_provider, effective_model)),
            "input_tokens": statement_input_tokens,
            "output_tokens": statement_output_tokens,
            "cached_tokens": _cached_tokens,
            "cache_write_tokens": _cache_write_tokens,
            "cost_usd": call_cost_usd
        }

        # **NEW: Track model usage with planning_phase for dual-model visibility**
        # This ensures session data accurately reflects which models were used for
        # strategic vs tactical planning
        await session_manager.update_models_used(
            user_uuid=self.user_uuid,
            session_id=self.session_id,
            provider=actual_provider,
            model=actual_model,
            profile_tag=self._get_current_profile_tag(),
            planning_phase=planning_phase  # ← Enables dual-model tracking
        )

        return response_text, statement_input_tokens, statement_output_tokens

    def _get_current_profile_tag(self) -> str | None:
        """
        Get the current profile tag from active profiles or profile override.
        
        Returns:
            Profile tag string or None if no active profile
        """
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()
            
            # If profile override is active, use that profile's tag
            if self.profile_override_id:
                profiles = config_manager.get_profiles(self.user_uuid)
                override_profile = next((p for p in profiles if p.get("id") == self.profile_override_id), None)
                if override_profile:
                    profile_tag = override_profile.get("tag")
                    app_logger.debug(f"_get_current_profile_tag: Using profile override tag: {profile_tag}")
                    return profile_tag
                else:
                    app_logger.warning(f"_get_current_profile_tag: Profile override ID {self.profile_override_id} not found")
            
            # Otherwise use the default profile
            default_profile_id = config_manager.get_default_profile_id(self.user_uuid)
            if default_profile_id:
                profiles = config_manager.get_profiles(self.user_uuid)
                default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)
                if default_profile:
                    profile_tag = default_profile.get("tag")
                    app_logger.debug(f"_get_current_profile_tag: Using default profile tag: {profile_tag}")
                    return profile_tag
            
            app_logger.debug(f"_get_current_profile_tag: No profile tag found (override_id={self.profile_override_id}, default_id={default_profile_id if 'default_profile_id' in locals() else 'not set'})")
        except Exception as e:
            app_logger.warning(f"Failed to get current profile tag: {e}", exc_info=True)
        return None

    def _get_active_profile_tag(self) -> str:
        """
        Get the tag of the currently active (default) profile, ignoring any profile override.
        Used for fallback messaging when profile override fails.
        
        Returns:
            str: The tag of the default profile, or "DEFAULT" if none found
        """
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()
            
            default_profile_id = config_manager.get_default_profile_id(self.user_uuid)
            if default_profile_id:
                profiles = config_manager.get_profiles(self.user_uuid)
                default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)
                if default_profile:
                    return default_profile.get("tag", "DEFAULT")
            
        except Exception as e:
            app_logger.warning(f"Failed to get default profile tag: {e}", exc_info=True)
        
        return "DEFAULT"

    def _calculate_session_cost_at_turn(self, session_data: dict) -> float:
        """
        Cumulative session cost of every turn ALREADY in the record (the current turn is
        not yet persisted when this runs) — delegates to the ONE definition,
        `session_manager.session_cost_total`, so the per-turn snapshot the engines stamp
        and the live figure the reload/session endpoints serve are the same arithmetic.
        """
        from trusted_data_agent.core import session_manager as _sm
        return _sm.session_cost_total(session_data, provider=self.current_provider, model=self.current_model)

    def _get_correction_catalog(self) -> Tuple[str, str]:
        """§4 perf: per-turn cached COMPACT (names + description + arg-names) tools+prompts catalog
        for self-correction prompts, in place of resending the FULL catalogs on every correction
        attempt. Preserves tool/prompt-switching discoverability (which all three correction
        templates require) at a fraction of the tokens. The executor is per-turn, so this cache is
        correctly scoped to the turn (STATE is stable during the execution/correction phase)."""
        state = self.dependencies.get('STATE', {}) if isinstance(self.dependencies, dict) else {}
        structured = state.get('structured_tools', {}) or {}
        # The governed surface decides which tools this turn may see — here too (the ONE rule
        # the planner's catalog reads, tool_definitions._apply_surface): a correction listed all
        # 54 tools of a governed agent whose planner was offered 17. Keyed by the shown set, so a
        # tool found mid-turn (TDA_FindTools) appears in the next correction.
        shown_key = None
        try:
            from trusted_data_agent.agent import tool_surface as _ts
            _cur = _ts.current()
            if _cur is not None:
                shown_key = frozenset(_cur.surface.offered) | frozenset(_cur.surface.widened)
                from components.builtin.context_window.module_registry import get_module_registry
                structured = get_module_registry().get_handler('tool_definitions')._apply_surface(structured)
        except Exception as e:  # noqa: BLE001 — a surface must never break a correction
            app_logger.debug(f"[surface] correction catalog unfiltered: {e}")
        cached = getattr(self, "_correction_catalog", None)
        if cached is not None and getattr(self, "_correction_catalog_key", None) == shown_key:
            return cached
        result = build_compact_tools_and_prompts_context(
            structured, state.get('mcp_tools', {}), state.get('structured_prompts', {})
        )
        self._correction_catalog = result
        self._correction_catalog_key = shown_key
        return result

    async def _get_tool_constraints(self, tool_name: str) -> Tuple[dict, list]:
        """
        Uses an LLM to determine if a tool requires numeric or character columns.
        Returns the constraints and a list of events to be yielded by the caller.
        """
        if tool_name in self.tool_constraints_cache:
            return self.tool_constraints_cache[tool_name], []

        events = []
        tool_definition = self.dependencies['STATE'].get('mcp_tools', {}).get(tool_name)
        constraints = {}

        # §4 perf: L2 process-global cache keyed by (tool_name, description) — the classification's
        # actual inputs. A hit skips the paid LLM call entirely (no cost, no events) on every turn
        # after the first, incl. sub-executors. Only a COMPLETE (non-empty) classification is cached.
        _gk = (tool_name, getattr(tool_definition, "description", "") if tool_definition else "")
        if _gk in _TOOL_CONSTRAINTS_CACHE:
            cached = _TOOL_CONSTRAINTS_CACHE[_gk]
            self.tool_constraints_cache[tool_name] = cached
            return cached, []

        if tool_definition:
            prompt_modifier = ""
            if any(k in tool_name.lower() for k in ["univariate", "standarddeviation", "negativevalues"]):
                prompt_modifier = "This tool is for quantitative analysis and requires a 'numeric' data type for `column_name`."
            elif any(k in tool_name.lower() for k in ["distinctcategories"]):
                prompt_modifier = "This tool is for categorical analysis and requires a 'character' data type for `column_name`."

            prompt = (
                f"Analyze the tool to determine if its `column_name` argument is for 'numeric', 'character', or 'any' type.\n"
                f"Tool: `{tool_definition.name}`\nDescription: \"{tool_definition.description}\"\nHint: {prompt_modifier}\n"
                "Respond with a single JSON object: {\"dataType\": \"numeric\" | \"character\" | \"any\"}"
            )

            reason="Determining tool constraints for column iteration."
            call_id = str(uuid.uuid4())
            events.append(self._format_sse_with_depth({"step": "Calling LLM", "type": "system_message", "details": {"summary": reason, "call_id": call_id}}))

            response_text, input_tokens, output_tokens = await self._call_llm_and_update_tokens(
                prompt=prompt, reason=reason,
                system_prompt_override="You are a JSON-only responding assistant.",
                raise_on_error=True,
                source=self.source
            )

            # Log post-LLM system_message with tokens + cost for historical replay
            constraint_log_event = {
                "step": "Calling LLM",
                "type": "system_message",
                "details": {
                    "summary": reason,
                    "call_id": call_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": self._last_call_metadata.get("cost_usd", 0)
                }
            }
            self._log_system_event(constraint_log_event)

            updated_session = await session_manager.get_session(self.user_uuid, self.session_id)
            if updated_session:
                events.append(self._format_sse_with_depth({
                    "statement_input": input_tokens,
                    "statement_output": output_tokens,
                    "turn_input": self.turn_input_tokens,
                    "turn_output": self.turn_output_tokens,
                    "total_input": updated_session.get("input_tokens", 0),
                    "total_output": updated_session.get("output_tokens", 0),
                    "call_id": call_id,
                    "cost_usd": self._last_call_metadata.get("cost_usd", 0)
                }, "token_update"))

            # §5: robust_json_parse per the repo-wide parse-hardening mandate.
            constraints = robust_json_parse(response_text)
            if not isinstance(constraints, dict):
                constraints = {}

        self.tool_constraints_cache[tool_name] = constraints
        # §4: populate the process-global L2 cache only with a real (non-empty) classification, so a
        # metering/parse failure isn't frozen in. Bounded backstop against unbounded growth.
        if constraints and len(_TOOL_CONSTRAINTS_CACHE) < _TOOL_CONSTRAINTS_CACHE_MAX:
            _TOOL_CONSTRAINTS_CACHE[_gk] = constraints
        return constraints, events

    def _add_to_structured_data(self, tool_result: dict, context_key_override: str = None):
        """Adds tool results to the structured data dictionary."""
        context_key = context_key_override or f"Plan Results: {self.active_prompt_name or 'Ad-hoc'}"
        if context_key not in self.structured_collected_data:
            self.structured_collected_data[context_key] = []

        if isinstance(tool_result, list):
             self.structured_collected_data[context_key].extend(tool_result)
        else:
             self.structured_collected_data[context_key].append(tool_result)
        app_logger.debug(f"Added tool result to structured data under key: '{context_key}'.")

    def _distill_data_for_llm_context(self, data: any, _events: list = None) -> any:
        """
        Recursively distills large data structures into metadata summaries
        to protect the LLM context window.

        Delegates to the unified ExecutionContextDistiller from the
        context_window component when available (configurable thresholds
        from the active context-window-type).  Falls back to APP_CONFIG
        thresholds when the distiller has not been initialised.

        Args:
            data: The data structure to distill
            _events: Optional list to accumulate distillation event dicts
                     (caller reads after call)
        """
        if self._context_distiller is not None:
            return self._context_distiller.distill(data, events=_events)

        # Fallback: inline distillation with global APP_CONFIG thresholds
        # (only reached if _run_context_window_assembly hasn't run yet)
        if isinstance(data, dict):
            if 'results' in data and isinstance(data['results'], list):
                results_list = data['results']
                is_large = (len(results_list) > APP_CONFIG.CONTEXT_DISTILLATION_MAX_ROWS or
                            len(json.dumps(results_list)) > APP_CONFIG.CONTEXT_DISTILLATION_MAX_CHARS)

                if is_large and all(isinstance(item, dict) for item in results_list):
                    columns = list(results_list[0].keys()) if results_list else []
                    if _events is not None:
                        _events.append({
                            "subtype": "context_distillation",
                            "summary": f"Large result distilled: {len(results_list):,} rows → metadata summary",
                            "row_count": len(results_list),
                            "char_count": len(json.dumps(results_list)),
                            "columns": columns
                        })
                    distilled_result = {
                        "status": data.get("status", "success"),
                        "metadata": {
                            "row_count": len(results_list),
                            "columns": columns,
                            **data.get("metadata", {})
                        },
                        "comment": "Full data is too large for context. This is a summary."
                    }
                    return distilled_result

            # A component result's pre-rendered `html` is the READER's card (it rides the
            # trace so the final answer can show it); the model already holds the same
            # facts in `spec`, so the markup never enters its context.
            return {key: self._distill_data_for_llm_context(value, _events=_events) for key, value in data.items()
                    if not (key in ("html", "title") and "spec" in data and "type" in data)}

        elif isinstance(data, list):
            return [self._distill_data_for_llm_context(item, _events=_events) for item in data]

        return data

    def _snapshot_with_distillation_events(self):
        """Return context window snapshot enriched with intra-turn distillation events."""
        snapshot = getattr(self, 'context_window_snapshot_event', None)
        distill_evts = getattr(self, '_distillation_events', [])
        if snapshot and distill_evts:
            snapshot = dict(snapshot)  # shallow copy to avoid mutating original
            snapshot["distillation_events"] = distill_evts
        return snapshot

    def _find_value_by_key(self, data_structure: any, target_key: str) -> any:
        """Recursively searches a nested data structure for the first value of a given key."""
        if isinstance(data_structure, dict):
            # Check for a direct match, but be case-insensitive for robustness
            for key, value in data_structure.items():
                if key.lower() == target_key.lower():
                    return value

            # If no direct match, recurse into values
            for value in data_structure.values():
                found = self._find_value_by_key(value, target_key)
                if found is not None:
                    return found

        elif isinstance(data_structure, list):
            for item in data_structure:
                found = self._find_value_by_key(item, target_key)
                if found is not None:
                    return found
        return None

    def _unwrap_single_value_from_result(self, data_structure: any) -> any:
        """
        Deterministically unwraps a standard tool result structure to extract a
        single primary value, if one exists.
        """
        is_single_value_structure = (
            isinstance(data_structure, list) and len(data_structure) == 1 and
            isinstance(data_structure[0], dict) and "results" in data_structure[0] and
            isinstance(data_structure[0]["results"], list) and len(data_structure[0]["results"]) == 1 and
            isinstance(data_structure[0]["results"][0], dict) and len(data_structure[0]["results"][0]) == 1
        )

        if is_single_value_structure:
            # Extract the single value from the nested structure
            return next(iter(data_structure[0]["results"][0].values()))

        # If the structure doesn't match, return the original data structure
        return data_structure

    _DATE_BIND_START = ("start_date", "from_date", "begin_date", "date_from", "start", "min_date")
    _DATE_BIND_END = ("end_date", "to_date", "until_date", "date_to", "end", "max_date")
    _DATE_BIND_NOW = ("date", "current_date", "today", "as_of", "as_of_date", "run_date")

    def _turn_date_context(self) -> dict:
        """{'start','end','today'} from this turn's TDA_DateRange / TDA_CurrentDate results."""
        out = {}
        for val in (self.workflow_state or {}).values():
            items = val if isinstance(val, list) else [val]
            for r in items:
                if not isinstance(r, dict):
                    continue
                tool = ((r.get("metadata") or {}).get("tool_name") or "")
                if tool == "TDA_DateRange" or ("start_date" in r and "end_date" in r):
                    if r.get("start_date") and r.get("end_date"):
                        out["start"], out["end"] = str(r["start_date"]), str(r["end_date"])
                elif tool == "TDA_CurrentDate":
                    rows = r.get("results") or []
                    if rows and isinstance(rows[0], dict) and rows[0].get("current_date"):
                        out["today"] = str(rows[0]["current_date"])
        return out

    def _substitute_sql_date_binds(self, sql: str) -> str:
        ctx = self._turn_date_context()
        if not ctx:
            return sql
        def _sub(m):
            name = m.group(1).lower()
            if name in self._DATE_BIND_START and ctx.get("start"):
                return f"'{ctx['start']}'"
            if name in self._DATE_BIND_END and ctx.get("end"):
                return f"'{ctx['end']}'"
            if name in self._DATE_BIND_NOW and (ctx.get("today") or ctx.get("end")):
                return f"'{ctx.get('today') or ctx.get('end')}'"
            return m.group(0)
        new_sql = re.sub(r"(?<![:\w]):([A-Za-z_]+)", _sub, sql)
        if new_sql != sql:
            app_logger.info("Resolved SQL date bind placeholders from this turn's date context.")
            self.events_to_yield.append(self._format_sse_with_depth({
                "step": "System Correction", "type": "workaround",
                "details": {"summary": "SQL used named date bind placeholders the query tool cannot take; "
                                       "substituted the turn's resolved dates deterministically.",
                            "correction_type": "sql_date_bind_wiring", "dates": ctx}}))
        return new_sql

    def _rewrite_phantom_date_source(self, sql: str) -> str:
        """Bounds read from a phantom date source → this turn's TDA_DateRange dates. Never
        raises; untouched when no range ran this turn or nothing is recognised."""
        try:
            from trusted_data_agent.agent import phantom_date_source as _pds
            ctx = self._turn_date_context()
            if not (ctx.get("start") and ctx.get("end")):
                return sql
            new_sql, changes = _pds.rewrite(sql, ctx["start"], ctx["end"])
            new_sql, bound_changes = _pds.rewrite_bound_refs(new_sql, ctx["start"], ctx["end"])
            changes = changes + bound_changes
            if changes:
                app_logger.info(f"Rewrote a date range read from a phantom source: {changes}")
                self.events_to_yield.append(self._format_sse_with_depth({
                    "step": "System Correction", "type": "workaround",
                    "details": {"summary": "The query read its date range from a table that does not exist "
                                           "(the date-range step's output); used the turn's dates instead "
                                           "(a mechanical fix, no LLM round).",
                                "correction_type": "phantom_date_source", "changes": changes,
                                "original_sql": sql, "executed_sql": new_sql, "dates": ctx}}))
            return new_sql
        except Exception as e:  # noqa: BLE001 — a rewrite must never break argument resolution
            app_logger.warning(f"phantom date-source rewrite skipped: {e}")
            return sql

    def _scalar_of_phase(self, phase_key: str):
        """A phase's result when it unwraps to ONE scalar (a name, a date, a number), else None
        — the same unwrap the embedded-reference rule uses."""
        data = self.workflow_state.get(phase_key)
        if data is None:
            return None
        try:
            unwrapped = self._unwrap_single_value_from_result([data] if isinstance(data, dict) else data)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(unwrapped, (str, int, float)) and not isinstance(unwrapped, bool):
            return str(unwrapped).strip()
        return None

    def _resolve_arguments(self, arguments: dict, loop_item: dict = None) -> dict:
        """
        Scans tool arguments for placeholders and resolves them based on the
        current context (workflow state and the optional loop_item).
        """
        if not isinstance(arguments, dict):
            return arguments

        resolved_args = {}

        placeholder_pattern = re.compile(r'(\s*\{[\s\n]*"source":\s*"[^"]+"(?:,[\s\n]*"key":\s*"[^"]+")?[\s\n]*\}\s*)')

        def _resolve_embedded_placeholder(match):
            """Callback function for re.sub to resolve a matched placeholder string."""
            placeholder_str = match.group(1).strip()
            try:
                placeholder_data = json.loads(placeholder_str)
                source_key = placeholder_data.get("source")
                target_key = placeholder_data.get("key")

                data_from_source = None
                if source_key == "loop_item" and loop_item:
                    data_from_source = loop_item
                elif source_key and source_key.startswith("result_of_phase_"):
                    data_from_source = self.workflow_state.get(source_key)
                elif source_key and source_key.startswith("phase_"):
                    data_from_source = self.workflow_state.get(f"result_of_{source_key}")
                elif source_key:
                    data_from_source = self.workflow_state.get(source_key)

                if data_from_source is None:
                    app_logger.warning(f"Could not resolve embedded placeholder: source '{source_key}' not found.")
                    return match.group(1)

                if target_key:
                    found_value = self._find_value_by_key(data_from_source, target_key)
                else:
                    found_value = self._unwrap_single_value_from_result(data_from_source)

                if found_value is not None:
                    app_logger.info(f"Resolved embedded placeholder '{placeholder_str}' to value '{found_value}'.")
                    return str(found_value)
                else:
                    app_logger.warning(f"Could not resolve embedded placeholder: key '{target_key}' not found in source '{source_key}'.")
                    return match.group(1)

            except (json.JSONDecodeError, AttributeError):
                return match.group(1)

        for key, value in arguments.items():
            # §2 fix (shape 1): a WHOLE-STRING JSON placeholder ('{"source":…,"key":…}') was
            # excluded by the embedded handler's `not fullmatch` guard below and then never
            # resolved — the literal JSON string reached the tool. Parse it into the canonical
            # dict so the placeholder machinery further down resolves it.
            if isinstance(value, str) and '"source":' in value and placeholder_pattern.fullmatch(value.strip()):
                try:
                    value = json.loads(value.strip())
                except (json.JSONDecodeError, ValueError):
                    pass  # not valid JSON after all — fall through and treat as a plain string

            if isinstance(value, str) and '"source":' in value and not placeholder_pattern.fullmatch(value.strip()):
                resolved_value = placeholder_pattern.sub(_resolve_embedded_placeholder, value)
                resolved_args[key] = resolved_value
                continue

            source_phase_key = None
            target_data_key = None
            is_placeholder = False
            original_placeholder = copy.deepcopy(value)

            if isinstance(value, dict) and value.get("source") == "loop_item" and loop_item:
                loop_key = value.get("key")
                # --- MODIFICATION START ---
                # If a key is specified, get that value. If no key is specified, pass the entire loop_item.
                resolved_args[key] = loop_item.get(loop_key) if loop_key else loop_item
                # --- MODIFICATION END ---
                continue

            if isinstance(value, str):
                # Handle bare {KeyName} templates embedded in SQL strings or other text
                # Planner normalizes standalone templates to dicts, but embedded ones remain as strings
                if loop_item and re.search(r'\{[A-Za-z][A-Za-z0-9_]*\}', value):
                    def replace_bare_template(match):
                        key = match.group(1)
                        # Only resolve if it looks like a template variable and exists in loop_item
                        if (key[0].isupper() or key in ['TableName', 'ColumnName', 'DatabaseName', 'SchemaName']) and key in loop_item:
                            replacement = loop_item.get(key)
                            if replacement is not None:
                                app_logger.info(f"Resolved bare template: {{{key}}} -> '{replacement}'")
                                return str(replacement)
                        return match.group(0)  # Return original if not a template or key not found

                    value = re.sub(r'\{([A-Za-z][A-Za-z0-9_]*)\}', replace_bare_template, value)
                    resolved_args[key] = value
                    continue

                # --- NEW: Handle string interpolation for result_of_phase_N references ---
                # Pattern: {result_of_phase_1[KeyName]} or {result_of_phase_1} within strings
                if "{result_of_phase_" in value or "{phase_" in value:
                    # `{result_of_phase_N[key]}`, or the same key written with a dot —
                    # `{result_of_phase_2.start_date}` (fusion bench 2026-09-24: left unresolved,
                    # the braces reached Teradata as a string and the turn answered "unable")
                    phase_pattern = re.compile(r'\{(result_of_phase_\d+|phase_\d+)(?:\[([^\]]+)\]|\.([A-Za-z_]\w*))?\}')

                    def replace_phase_ref(match):
                        """Replace {result_of_phase_N[key]} / {result_of_phase_N.key} with the value
                        from workflow state."""
                        source_key = match.group(1)
                        target_key = match.group(2) or match.group(3)  # optional key: [key] or .key

                        if source_key.startswith("phase_"):
                            source_key = f"result_of_{source_key}"

                        data_from_phase = self.workflow_state.get(source_key)

                        if data_from_phase is None:
                            app_logger.warning(f"Could not resolve phase interpolation: '{source_key}' not in workflow state.")
                            return match.group(0)

                        if target_key:
                            # Extract specific key from the phase result
                            target_key_clean = target_key.strip('\'"')
                            found_value = self._find_value_by_key(data_from_phase, target_key_clean)
                            if found_value is not None:
                                app_logger.info(f"Resolved phase interpolation: {{{source_key}[{target_key_clean}]}} -> '{found_value}'")
                                return str(found_value)
                            else:
                                app_logger.warning(f"Could not find key '{target_key_clean}' in '{source_key}'.")
                                return match.group(0)
                        else:
                            # No specific key - unwrap single value
                            unwrapped = self._unwrap_single_value_from_result(data_from_phase)
                            if unwrapped is not None:
                                app_logger.info(f"Resolved phase interpolation: {{{source_key}}} -> '{unwrapped}'")
                                return str(unwrapped)
                            else:
                                app_logger.warning(f"Could not unwrap value from '{source_key}'.")
                                return match.group(0)

                    resolved_value = phase_pattern.sub(replace_phase_ref, value)
                    resolved_args[key] = resolved_value
                    continue
                # --- END NEW ---

                # Named DATE binds the tool cannot take. `base_readQuery` exposes only
                # `sql`, yet a weak planner writes `WHERE d >= :start_date` after a
                # TDA_DateRange phase — the driver then fails "a value is required for
                # bind parameter". The values ARE in this turn's state; wire them.
                if key == "sql" and re.search(r"(?<![:\w]):[A-Za-z_]+", value):
                    value = self._substitute_sql_date_binds(value)

                # The date range read from a table that does not exist — a phase's OUTPUT
                # treated as a database object (`FROM TDA_DateRange_result`, `TABLE(CAST(? AS
                # JSON))`, `IN (SELECT date FROM result_of_phase_2)`). The dates are in this
                # turn's state; the bounds become literals (agent/phantom_date_source.py).
                if key == "sql" and "SELECT" in value.upper():
                    value = self._rewrite_phantom_date_source(value)

                # A bare phase reference EMBEDDED in a longer string (the planner wrote
                # `... WHERE SaleDate <= 'result_of_phase_1'` instead of a brace/dict
                # placeholder) was sent to the tool verbatim. When the referenced phase
                # unwraps to a SCALAR (a date, a name, a number) the substitution is
                # unambiguous and deterministic; anything else is left untouched.
                # …and a phase reference WITH a key (`'result_of_phase_2.start_date'`, fusion
                # bench 2026-09-24) resolves to that key's value in the phase's result when it is
                # a scalar; anything else is left as written.
                # A reference dotted to a reference (`result_of_phase_2.result_of_phase_4`) is a
                # qualified name built from two steps' outputs (a database found by one step, a
                # table by the next) — not a key. Both sides resolve when both are scalars; the
                # keyed rule below would otherwise read `result_of_phase_4` as a key, find none,
                # and leave the database half as literal text (multi-source bench, 2026-09-29).
                if key == "sql" and re.search(r"\bresult_of_phase_\d+\.result_of_phase_\d+\b", value):
                    def _sub_composed(m):
                        left = self._scalar_of_phase(f"result_of_phase_{m.group(1)}")
                        right = self._scalar_of_phase(f"result_of_phase_{m.group(2)}")
                        if left is not None and right is not None:
                            app_logger.info(f"Resolved composed phase reference '{m.group(0)}' -> '{left}.{right}'")
                            return f"{left}.{right}"
                        return m.group(0)
                    value = re.sub(r"\bresult_of_phase_(\d+)\.result_of_phase_(\d+)\b", _sub_composed, value)
                    resolved_args[key] = value

                if key == "sql" and re.search(r"\bresult_of_phase_\d+\.[A-Za-z_]\w*", value):
                    def _sub_keyed(m):
                        data = self.workflow_state.get(f"result_of_phase_{m.group(1)}")
                        found = self._find_value_by_key(data, m.group(2)) if data is not None else None
                        if isinstance(found, (str, int, float)) and not isinstance(found, bool):
                            app_logger.info(f"Resolved embedded phase reference '{m.group(0)}' -> '{found}'")
                            return str(found)
                        return m.group(0)
                    value = re.sub(r"\bresult_of_phase_(\d+)\.([A-Za-z_]\w*)", _sub_keyed, value)
                    resolved_args[key] = value

                if (re.search(r"\bresult_of_phase_\d+\b(?!\.[A-Za-z_])", value)
                        and not re.match(r"^\s*(result_of_phase_\d+|phase_\d+|injected_previous_turn_data)\s*$", value)):
                    def _sub_embedded_scalar(m):
                        data = self.workflow_state.get(m.group(0))
                        if data is None:
                            return m.group(0)
                        try:
                            # phase results are stored list-wrapped; a bare dict result
                            # (`{"status":…, "results":[{k: v}]}`) is the same shape one level up
                            unwrapped = self._unwrap_single_value_from_result(
                                [data] if isinstance(data, dict) else data)
                        except Exception:  # noqa: BLE001
                            return m.group(0)
                        if isinstance(unwrapped, (str, int, float)) and not isinstance(unwrapped, bool):
                            app_logger.info(f"Resolved embedded phase reference '{m.group(0)}' -> '{unwrapped}'")
                            return str(unwrapped)
                        return m.group(0)
                    # a keyed reference (`result_of_phase_2.start_date`) is the step above's —
                    # never the prefix of one
                    substituted = re.sub(r"\bresult_of_phase_\d+\b(?!\.[A-Za-z_])", _sub_embedded_scalar, value)
                    if substituted != value:
                        resolved_args[key] = substituted
                        continue

                match = re.match(r"(result_of_phase_\d+|phase_\d+|injected_previous_turn_data)", value)
                if match:
                    source_phase_key = match.group(1)
                    is_placeholder = True

            elif isinstance(value, dict):
                if "source" in value and "key" in value:
                    source_phase_key = value["source"]
                    target_data_key = value["key"]
                    is_placeholder = True

                elif "source" in value and "key" not in value:
                    source_phase_key = value["source"]
                    target_data_key = None
                    is_placeholder = True
                    self.events_to_yield.append(self._format_sse_with_depth({
                        "step": "System Correction", "type": "workaround",
                        "details": {
                            "summary": "The agent's plan used an incomplete placeholder. The system will automatically extract the primary value from the source.",
                            "correction_type": "placeholder_unwrapping",
                            "from": original_placeholder,
                            "to": f"Unwrapped value from '{source_phase_key}'"
                        }
                    }))

                else:
                    for k, v in value.items():
                        if re.match(r"result_of_phase_\d+", k):
                            source_phase_key = k
                            target_data_key = v
                            is_placeholder = True

                            canonical_value = {"source": source_phase_key, "key": target_data_key}
                            self.events_to_yield.append(self._format_sse_with_depth({
                                "step": "System Correction", "type": "workaround",
                                "details": {
                                    "summary": "The agent's plan contained a non-standard placeholder. The system has automatically normalized it to ensure correct data flow.",
                                    "correction_type": "placeholder_normalization",
                                    "from": original_placeholder,
                                    "to": canonical_value
                                }
                            }))
                            value = canonical_value
                            break

            if is_placeholder:
                if source_phase_key and source_phase_key.startswith("phase_"):
                    source_phase_key = f"result_of_{source_phase_key}"

                # Normalize tool_<ToolName> references to result_of_phase_N
                # Some LLMs generate "source": "tool_TDA_CurrentDate" instead of "result_of_phase_5"
                if source_phase_key and source_phase_key.startswith("tool_") and source_phase_key not in self.workflow_state:
                    tool_ref_name = source_phase_key[5:]  # Strip "tool_" prefix
                    if self.meta_plan:
                        for phase in self.meta_plan:
                            phase_tools = phase.get("relevant_tools", [])
                            if tool_ref_name in phase_tools:
                                resolved_key = f"result_of_phase_{phase.get('phase', '')}"
                                if resolved_key in self.workflow_state:
                                    app_logger.info(f"Normalized tool reference '{source_phase_key}' → '{resolved_key}'")
                                    source_phase_key = resolved_key
                                    break

                if source_phase_key in self.workflow_state:
                    data_from_phase = self.workflow_state[source_phase_key]

                    if target_data_key:
                        found_value = self._find_value_by_key(data_from_phase, target_data_key)
                        if found_value is not None:
                            resolved_args[key] = found_value
                        else:
                            app_logger.warning(f"Could not resolve placeholder: key '{target_data_key}' not found in '{source_phase_key}'. Omitting argument.")
                            # Don't set None - just omit the argument entirely
                    else:
                        # A key-less reference still names the key IMPLICITLY: the
                        # argument's own name. Observed live: the model authored
                        # `sql: "result_of_phase_1"` against a query_product result —
                        # meaning "the sql phase 1 produced" — and the blind unwrap
                        # returned the WHOLE component payload, which was then sent to
                        # the warehouse as the SQL text; the tool errored, self-
                        # correction fell back to raw SQL (denied, surface-only), and
                        # the final answer fabricated "1,000 customers" out of the
                        # payload's TOP 1000 clause. Try the argument's name as the
                        # lookup key FIRST; unwrap stays as the fallback so a scalar
                        # reference (date="result_of_phase_1" → a lone value) resolves
                        # exactly as before.
                        by_arg_name = self._find_value_by_key(data_from_phase, key)
                        if by_arg_name is not None:
                            resolved_args[key] = by_arg_name
                            app_logger.info(
                                f"Resolved placeholder for '{key}' from '{source_phase_key}' "
                                f"by the argument's own name.")
                        else:
                            unwrapped_value = self._unwrap_single_value_from_result(data_from_phase)
                            resolved_args[key] = unwrapped_value
                            app_logger.info(f"Resolved placeholder for '{key}' by unwrapping the result of '{source_phase_key}'.")

                else:
                    app_logger.warning(f"Could not resolve placeholder: source '{source_phase_key}' not in workflow state.")
                    resolved_args[key] = value

            elif isinstance(value, dict):
                resolved_args[key] = self._resolve_arguments(value, loop_item)

            elif isinstance(value, list):
                # §2 fix (shape 2): resolve placeholder STRINGS inside a list arg, not just dicts.
                # A list like ["result_of_phase_1", "{result_of_phase_2[x]}"] previously passed its
                # string items through unresolved → raw placeholder text sent to the tool. Wrap each
                # string in a temp dict and recurse so bare / braced / whole-string JSON placeholders
                # all resolve; an unresolvable string keeps its original value (unchanged behaviour).
                resolved_list = []
                for item in value:
                    if isinstance(item, dict):
                        resolved_list.append(self._resolve_arguments(item, loop_item))
                    elif isinstance(item, str):
                        resolved_list.append(self._resolve_arguments({"_v": item}, loop_item).get("_v", item))
                    else:
                        resolved_list.append(item)
                resolved_args[key] = resolved_list

            else:
                resolved_args[key] = value

        # Filter out None values (defense in depth)
        filtered_args = {k: v for k, v in resolved_args.items() if v is not None}

        if len(filtered_args) < len(resolved_args):
            removed_keys = set(resolved_args.keys()) - set(filtered_args.keys())
            app_logger.debug(f"Filtered out None values for keys: {removed_keys}")

        return filtered_args

    async def _generate_and_emit_session_name(self):
        """
        Generate session name using unified generator and emit SSE events.
        Collects events for system_events array (plan reload).

        Yields:
            - SSE formatted events (strings) for live streaming
            - Final tuple: (session_name, input_tokens, output_tokens, collected_events)
        """
        collected_events = []
        session_name = "New Chat"
        name_input_tokens = 0
        name_output_tokens = 0

        async for event_data, event_type, in_tokens, out_tokens in generate_session_name_with_events(
            user_query=self.original_user_input,
            session_id=self.session_id,
            llm_interface="executor",
            llm_dependencies=self.dependencies,
            user_uuid=self.user_uuid,
            active_profile_id=self.active_profile_id,
            current_provider=self.current_provider,
            current_model=self.current_model,
            profile_llm_instance=self.profile_llm_instance,
            emit_events=True
        ):
            if event_data is None:
                # Final yield: (None, session_name, input_tokens, output_tokens)
                session_name = event_type  # event_type contains name in final yield
                name_input_tokens = in_tokens
                name_output_tokens = out_tokens
            else:
                # SSE event: yield to frontend
                yield self._format_sse_with_depth(event_data, event_type)

                # Collect for system_events (plan reload)
                # Store complete event_data including 'step', 'details', and 'type'
                collected_events.append({
                    "type": event_type,
                    "payload": event_data  # Full event data, not just details
                })

        # Final yield: return the collected data as a tuple
        yield (session_name, name_input_tokens, name_output_tokens, collected_events)


    async def _run_context_window_assembly(self, profile_type: str, profile_config: dict):
        """
        Run the Context Window Manager orchestrator to assemble context
        and emit a snapshot event for observability.

        Only called when APP_CONFIG.USE_CONTEXT_WINDOW_MANAGER is True.
        In Phase 4 (feature-flagged), this runs alongside existing code paths
        for observability only — it does NOT replace the actual context assembly.
        """
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            from components.builtin.context_window.handler import ContextWindowHandler
            from components.builtin.context_window.base import AssemblyContext

            config_manager = get_config_manager()

            # Resolve context window type for the active profile
            cwt_id = profile_config.get("contextWindowTypeId")
            if cwt_id:
                cwt = config_manager.get_context_window_type(cwt_id, self.user_uuid)
            else:
                cwt = config_manager.get_default_context_window_type(self.user_uuid)

            if not cwt:
                app_logger.debug("No context window type configured — skipping assembly")
                return None

            # Initialise the execution-context distiller from context window
            # type config so that intra-turn distillation uses the same
            # thresholds configured alongside the module budget allocations.
            try:
                from components.builtin.context_window.distiller import ExecutionContextDistiller
                self._context_distiller = ExecutionContextDistiller.from_context_window_type(cwt)
            except Exception as e:
                app_logger.debug(f"Could not create context distiller from cwt: {e}")

            # Resolve model context limit from litellm model metadata
            model_context_limit = 128_000  # Safe default
            try:
                import litellm
                model_key = f"{self.current_provider}/{self.current_model}" if self.current_provider else self.current_model
                model_info = litellm.model_cost.get(model_key) or litellm.model_cost.get(self.current_model or "")
                if model_info:
                    model_context_limit = model_info.get("max_input_tokens") or model_info.get("max_tokens") or 128_000
                    app_logger.debug(f"Resolved model context limit: {model_context_limit:,} tokens for {model_key}")
            except Exception as e:
                app_logger.debug(f"Could not resolve model context limit from litellm: {e}")

            # Apply profile-level context limit override
            context_limit_override = profile_config.get("contextLimitOverride")
            if context_limit_override and isinstance(context_limit_override, int):
                if context_limit_override < model_context_limit:
                    app_logger.info(
                        f"Profile context limit override: {context_limit_override:,} tokens "
                        f"(model default: {model_context_limit:,})"
                    )
                    model_context_limit = context_limit_override

            # Build assembly context
            session_data = await session_manager.get_session(self.user_uuid, self.session_id)

            # Enrich dependencies: modules expect top-level keys like
            # 'current_provider' and 'structured_tools' that live on self
            # or nested under dependencies['STATE'].
            enriched_deps = dict(self.dependencies)
            enriched_deps['current_provider'] = getattr(self, 'current_provider', '') or ''
            enriched_deps['current_model'] = getattr(self, 'current_model', '') or ''
            app_state = enriched_deps.get('STATE')
            # a per-executor overlay (an @TAG override's ChainMap, the S6 surface) is a Mapping,
            # not a dict — reading it through `.get` is what lets the module see the overlay
            if app_state is not None and hasattr(app_state, 'get'):
                enriched_deps['structured_tools'] = app_state.get('structured_tools', {})
                enriched_deps['mcp_tools'] = app_state.get('mcp_tools', {})

            # Enrich session_data: modules like RAGContextModule and
            # KnowledgeContextModule expect 'current_query' which is on
            # self.original_user_input, not in session_manager data.
            enriched_session = dict(session_data) if session_data else {}
            enriched_session['current_query'] = self.original_user_input or ''
            # The turn's attachments: the document_context module measures (and delivers) them,
            # and the "no_documents_attached" rule reads them — neither saw any before.
            enriched_session['attachments'] = list(self.attachments or [])

            # Apply session-level context limit override (takes precedence over profile)
            session_context_limit = enriched_session.get('session_context_limit_override')
            if session_context_limit and isinstance(session_context_limit, int):
                if session_context_limit < model_context_limit:
                    app_logger.info(
                        f"Session context limit override: {session_context_limit:,} tokens "
                        f"(effective default: {model_context_limit:,})"
                    )
                    model_context_limit = session_context_limit

            ctx = AssemblyContext(
                profile_type=profile_type,
                profile_id=self.active_profile_id or "",
                session_id=self.session_id,
                user_uuid=self.user_uuid,
                session_data=enriched_session,
                turn_number=getattr(self, "current_turn_number", 1),
                is_first_turn=getattr(self, "current_turn_number", 1) == 1,
                model_context_limit=model_context_limit,
                dependencies=enriched_deps,
                profile_config=profile_config,
            )

            # Create per-session vector store for RAG condensation (if configured)
            session_store = None
            try:
                from components.builtin.context_window.session_vector_store import get_session_store
                rag_backend_id = cwt.get("rag_offload_backend_id")
                backend_config = None
                if rag_backend_id:
                    vs_configs = config_manager.get_vector_store_configurations(self.user_uuid)
                    vs_cfg = next(
                        (c for c in vs_configs if c.get("id") == rag_backend_id), None
                    )
                    if vs_cfg:
                        backend_config = {
                            "backend_type": vs_cfg.get("backend_type", "chromadb"),
                            "backend_config": vs_cfg.get("backend_config", {}),
                        }
                session_store = get_session_store(self.session_id, self.user_uuid, backend_config)
            except Exception as _svs_err:
                app_logger.debug(f"Session store init skipped (non-critical): {_svs_err}")

            # Ingest documents if document_context has rag_offload configured
            if session_store:
                cwt_modules = cwt.get("modules", {})
                doc_module_cfg = cwt_modules.get("document_context", {})
                if doc_module_cfg.get("condensation_strategy") == "rag_offload" and self.attachments:
                    # Awaited (bounded): the module searches the index during THIS assembly — a
                    # fire-and-forget task raced it and the first turn of a document found nothing.
                    try:
                        await asyncio.wait_for(
                            _ingest_documents_to_session_store(
                                session_store=session_store,
                                session_id=self.session_id,
                                user_uuid=self.user_uuid,
                                session_data=enriched_session,
                            ),
                            timeout=30,
                        )
                    except asyncio.TimeoutError:
                        app_logger.warning("Document indexing for search took over 30 s — this turn uses the excerpt only")

            # Run orchestrator
            handler = ContextWindowHandler()
            assembled = await handler.assemble(cwt, ctx, session_store=session_store)

            # Log snapshot
            if assembled.snapshot:
                app_logger.info(
                    f"Context Window Snapshot: "
                    f"{assembled.snapshot.total_used:,}/{assembled.snapshot.available_budget:,} tokens "
                    f"({assembled.snapshot.utilization_pct:.1f}% utilization)"
                )

            # Initialise the ContextBuilder with the assembled output so that
            # planner.py and phase_executor.py can consume module content
            # through a single, budget-aware entry point.
            try:
                from components.builtin.context_window.context_builder import ContextBuilder
                if self.context_builder is None:
                    self.context_builder = ContextBuilder(self)
                self.context_builder.set_assembled_context(assembled, ctx, cwt)
                app_logger.debug("ContextBuilder initialised with assembled context")
            except Exception as cb_err:
                app_logger.debug(f"Could not initialise ContextBuilder: {cb_err}")

            return assembled

        except Exception as e:
            app_logger.warning(f"Context window assembly failed (non-critical): {e}")
            return None

    def _detect_profile_type(self) -> str:
        """Detect whether current profile is llm_only, rag_focused, or tool_enabled."""
        if self.force_profile_type:
            return self.force_profile_type
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()

            if self.profile_override_id:
                # Check override profile
                profiles = config_manager.get_profiles(self.user_uuid)
                override = next((p for p in profiles if p.get("id") == self.profile_override_id), None)
                if override:
                    return override.get("profile_type", "tool_enabled")
            else:
                # Check default profile
                default_profile_id = config_manager.get_default_profile_id(self.user_uuid)
                if default_profile_id:
                    default = config_manager.get_profile(default_profile_id, self.user_uuid)
                    if default:
                        return default.get("profile_type", "tool_enabled")
        except Exception as e:
            app_logger.error(f"Error detecting profile type: {e}")

        return "tool_enabled"  # Default

    def _is_rag_focused_profile(self) -> bool:
        """Check if current profile is RAG focused type."""
        profile_type = self._detect_profile_type()
        return profile_type == "rag_focused"

    async def _execute_conversation_with_tools(self):
        """Delegated to ConversationEngine. Kept for any internal callers."""
        from trusted_data_agent.agent.engines.conversation_engine import ConversationEngine
        async for event in self._with_progress(ConversationEngine().run(self)):
            yield event

    # ── S6 The Governed Tool Surface ───────────────────────────────────────────────────
    def _surface_enrichment_names(self) -> list:
        """The entities / tables the KG enrichment named this turn — the surface's vocabulary
        beyond the question's own words."""
        ev = getattr(self, 'kg_enrichment_event', None) or {}
        names = []
        for k in ('entity_names', 'scope_entities'):
            v = ev.get(k)
            if isinstance(v, (list, tuple)):
                names.extend(str(x) for x in v)
        tc = ev.get('table_columns')
        if isinstance(tc, dict):
            names.extend(str(k) for k in tc.keys())
        return names

    def _surface_scope(self):
        """The authority scope the chokepoints will enforce this turn (the ambient principal),
        so the surface's DENIED set is exactly what a call would be denied."""
        try:
            from trusted_data_agent.core import agent_authority as _aa
            p = _aa.get_current_principal()
            return p.scope if p is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _arm_tool_surface_optimize(self):
        """Optimize lane: decide the surface over `STATE['structured_tools']` and install a
        per-executor STATE overlay (the @TAG-override ChainMap precedent) whose
        `structured_tools` carries only the OFFERED tools and whose `tools_context` is rebuilt
        from it — so the planner's catalog, the tactical prompt and the `tool_definitions`
        context module all read the surface. `mcp_tools` is NOT filtered: a held tool that the
        model finds (or a replayed plan names) still executes. Returns the overlay or None."""
        try:
            from collections import ChainMap
            profile = self._get_profile_config() or {}
            if not _tool_surface.armed_governed(profile):
                return None
            state = self.dependencies.get('STATE') or {}
            structured = state.get('structured_tools', {}) or {}
            catalog = _tool_surface.catalog_from_structured(structured, state.get('tool_scopes', {}))
            if not catalog:
                return None
            tok = _tool_surface.arm(self.original_user_input or "", catalog, profile=profile,
                                    scope=self._surface_scope(), enrichment_names=self._surface_enrichment_names(),
                                    user_uuid=self.user_uuid, session_id=self.session_id)
            if tok is None:
                return None
            self._surface_base_state = state
            self._apply_surface_overlay()
            # S6 review: a TDA_FindTools that widens the surface rebuilds the overlay at once —
            # the found tool must reach the NEXT tactical prompt, not only the persisted stamp
            _ts_now = _tool_surface.current()
            if _ts_now is not None:
                _ts_now.on_widen = self._apply_surface_overlay
            return self.dependencies.get('STATE')
        except Exception as e:  # noqa: BLE001 — a surface must never break a turn
            app_logger.debug(f"[surface] optimize arming skipped: {e}")
            return None

    def _reselect_tool_surface(self) -> bool:
        """After the KG enrichment named the tables in play: re-run the selection with the
        enrichment's vocabulary (offered can only GROW — a found tool is kept), rebuild the
        overlay, and refresh the assembled `tool_definitions` content so the ContextBuilder hands
        the planner the current surface. Returns True when the offered set changed."""
        try:
            ts = _tool_surface.current()
            if ts is None:
                return False
            names = self._surface_enrichment_names()
            if not names:
                return False
            sem = _tool_surface.semantic_scores(self.original_user_input or "", ts.catalog, names)
            fresh = _tool_surface.select(self.original_user_input or "", ts.catalog, enrichment_names=names,
                                         scope=self._surface_scope(), mode="governed", semantic=sem)
            before = set(ts.surface.offered)
            grown = [n for n in fresh.offered if n not in before and n not in ts.surface.widened]
            if not grown:
                return False
            for n in grown:
                if n in ts.surface.held:
                    ts.surface.held.remove(n)
                ts.surface.offered.append(n)
                ts.surface.reasons[n] = fresh.reasons.get(n, "addresses the tables the ontology named")
            self._apply_surface_overlay()
            self._refresh_assembled_tool_definitions()
            _tool_surface._broadcast()
            return True
        except Exception as e:  # noqa: BLE001
            app_logger.debug(f"[surface] re-select skipped: {e}")
            return False

    def _refresh_assembled_tool_definitions(self):
        """Re-render the cached `tool_definitions` contribution against the current overlay
        (the assembly ran before the enrichment; the planner reads the cache)."""
        try:
            cb = getattr(self, 'context_builder', None)
            asm = getattr(cb, '_assembled', None) if cb is not None else None
            contrib = (getattr(asm, 'contributions', None) or {}).get('tool_definitions') if asm is not None else None
            if contrib is None:
                return
            from components.builtin.context_window.module_registry import get_module_registry
            from components.builtin.context_window.token_estimator import estimate_tokens
            mod = get_module_registry().get_handler('tool_definitions')
            state = self.dependencies.get('STATE') or {}
            tools = mod._apply_surface(state.get('structured_tools', {}) or {})
            full = (contrib.metadata or {}).get('mode') == 'full'
            content = mod._format_full(tools) if full else mod._format_condensed(tools)
            contrib.content = content
            contrib.tokens_used = estimate_tokens(content)
        except Exception as e:  # noqa: BLE001
            app_logger.debug(f"[surface] assembled tool_definitions refresh skipped: {e}")

    def _apply_surface_overlay(self):
        """(Re)build the STATE overlay from the ambient surface — called at arming and after a
        `TDA_FindTools` widened it."""
        from collections import ChainMap
        ts = _tool_surface.current()
        base = getattr(self, '_surface_base_state', None)
        if ts is None or base is None:
            return
        shown = set(ts.surface.offered) | set(ts.surface.widened)
        filtered = {cat: [t for t in tools if isinstance(t, dict) and (t.get('disabled') or t.get('name') in shown)]
                    for cat, tools in (base.get('structured_tools', {}) or {}).items()}
        filtered = {c: t for c, t in filtered.items() if any(not x.get('disabled') for x in t)}
        slots = {'structured_tools': filtered}
        overlay = ChainMap(slots, base)
        tc, pc = rebuild_tools_and_prompts_context(state=overlay)
        slots['tools_context'] = tc
        slots['prompts_context'] = pc
        self.dependencies['STATE'] = overlay

    def surface_event(self) -> Optional[dict]:
        """The Live Status `tool_surface` step (collapsed): offered · held · denied · widened."""
        st = _tool_surface.stamp_for_turn()
        if not st:
            return None
        c = st.get('counts') or {}
        return {"step": "Tool Surface", "type": "tool_surface",
                "details": {**st, "summary": f"offered {c.get('offered', 0)} of {c.get('total', 0)} tools"
                                             + (f"; {c.get('denied')} denied by authority" if c.get('denied') else "")
                                             + (f"; {c.get('widened')} found by the model" if c.get('widened') else "")}}

    def _format_canvas_context(self) -> Optional[str]:
        """Format open canvas state as context string for LLM injection.

        Returns:
            Formatted context string or None if no canvas is open.
        """
        if not self.canvas_context:
            return None
        title = self.canvas_context.get("title", "Canvas")
        language = self.canvas_context.get("language", "text")
        content = self.canvas_context.get("content", "")
        modified = self.canvas_context.get("modified", False)

        status = "modified by user" if modified else "as generated"
        return (
            f"# Open Canvas: {title}\n"
            f"Language: {language} | Status: {status}\n"
            f"The user has this canvas open in the side panel. "
            f"When they refer to \"the canvas\", \"the code\", \"the page\", or \"it\", they mean this content.\n"
            + ("If asked to modify it, use TDA_Canvas with the same title to update it.\n"
               if self._component_enabled_for_turn("canvas") is not False else "") +
            f"```{language}\n{content}\n```"
        )

    def documents_for_turn(self) -> tuple[Optional[str], list]:
        """The turn's document text and its truncation events, for an engine that builds its own
        message: the context window's document text when assembly produced one (budgeted,
        condensed, with searched passages), else the loader under the one limit rule."""
        text = getattr(self, "_cw_document_text", None)
        if text:
            return text, list(getattr(self, "_cw_document_events", None) or [])
        if not self.attachments:
            return None, []
        return load_document_context(self.user_uuid, self.session_id, self.attachments,
                                     max_total_chars=getattr(self, "_cw_document_max_chars", None))

    def attached_material(self) -> tuple[tuple, bool]:
        """What this turn's attachments give the model to answer from: ``(texts, unreadable)``.

        ``texts`` — the document text the turn sends (``documents_for_turn``) plus the
        platform's readings of attached images (S9), exactly what the model was shown.
        ``unreadable`` — a still-readable attachment with no text reached the model natively
        (an image): the answer may draw on it, and no deterministic check can read it.
        ``((), False)`` when nothing usable was attached (a file deleted from Files never counts)."""
        attachments = getattr(self, "attachments", None)
        if not attachments:
            return (), False
        try:
            from trusted_data_agent.filestore import session_files
            manifest = session_files.readable_manifest(self.user_uuid, self.session_id) or {}
        except Exception:  # noqa: BLE001
            return (), False
        live = [manifest[a["file_id"]] for a in attachments
                if isinstance(a, dict) and a.get("file_id") in manifest]

        texts = []
        if any(session_files.entry_has_text(e) for e in live):
            try:
                doc_text, _ = self.documents_for_turn()
            except Exception:  # noqa: BLE001
                doc_text = None
            if doc_text:
                texts.append(doc_text)
        extra = getattr(self, "_attachment_sources_text", None)
        if extra and not any(extra in t for t in texts):
            texts.append(extra)
        # an image the model saw natively whose text the platform never read (S9 off/unverified)
        unreadable = bool(getattr(self, "multimodal_content", None)) and any(
            e.get("is_image") and not session_files.entry_has_text(e) for e in live)
        return tuple(texts), unreadable

    async def _build_user_message_for_conversation(self, knowledge_context: Optional[str] = None, document_context: Optional[str] = None) -> str:
        """Build user message for LLM-only direct execution.

        System prompt is handled separately via system_prompt_override parameter.
        This method builds only the user-facing content: document context + knowledge context + conversation history + current query.

        IMPORTANT: This retrieves conversation history from session_id, which is
        SHARED across all profile switches. This means LLM-only profiles can
        reference and build upon results from previous tool-enabled turns.

        Example flow:
          Turn 1 (@SQL): "Query products table" → Returns SQL results
          Turn 2 (@CHAT): "Summarize those results" → Sees SQL results in history

        Args:
            knowledge_context: Optional knowledge context to inject before conversation history
            document_context: Optional uploaded document context to inject

        Returns:
            User message string with documents + knowledge + history + current query
        """
        # Get session history (includes ALL previous turns regardless of profile)
        # Use budget-aware module output if available (Phase 5a: context window integration)
        cw_history = getattr(self, '_cw_conversation_history', None)
        if cw_history:
            history_text = cw_history
            app_logger.debug("Using budget-aware conversation history from context window module")
        else:
            try:
                session_data = await session_manager.get_session(self.user_uuid, self.session_id)
                session_history = session_data.get('chat_object', []) if session_data else []

                # Fallback: Format last 10 messages for context, filtering out invalid messages
                history_text = "\n".join([
                    f"{'User' if msg.get('role') == 'user' else 'Assistant'}: {msg.get('content', '')}"
                    for msg in session_history[-10:]
                    if msg.get("isValid") is not False  # Skip messages marked as invalid
                ])
            except Exception as e:
                app_logger.error(f"Failed to load session history: {e}")
                history_text = "(No conversation history available)"

        # Build user message (WITHOUT system prompt)
        user_message_parts = []

        # Inject open canvas context if present (bidirectional context)
        canvas_ctx = self._format_canvas_context()
        if canvas_ctx:
            user_message_parts.append(canvas_ctx)

        # Inject uploaded document context if present
        if document_context:
            user_message_parts.append(f"# Uploaded Documents\n{document_context}")

        # Inject knowledge context if retrieved
        if knowledge_context:
            user_message_parts.append(knowledge_context)

        # Add conversation history and current query
        user_message_parts.append(f"""Previous conversation:
{history_text}

User: {self.original_user_input}""")

        return "\n".join(user_message_parts)

    async def _build_user_message_for_rag_synthesis(self, knowledge_context: str, document_context: Optional[str] = None) -> str:
        """Build user message for RAG focused synthesis with knowledge + history.

        Args:
            knowledge_context: Formatted knowledge context from retrieval
            document_context: Optional uploaded document context to inject

        Returns:
            User message string with documents + knowledge + optional history + current query
        """
        parts = []

        # Inject open canvas context if present (bidirectional context)
        canvas_ctx = self._format_canvas_context()
        if canvas_ctx:
            parts.append(canvas_ctx)

        # Inject uploaded document context if present
        if document_context:
            parts.append(f"# Uploaded Documents\n{document_context}")

        # Knowledge context (most important)
        if knowledge_context:
            parts.append(knowledge_context)

        # Conversation history (for follow-ups) - only if history not disabled
        # Use budget-aware module output if available (Phase 5a: context window integration)
        if not self.disabled_history:
            cw_history = getattr(self, '_cw_conversation_history', None)
            if cw_history:
                parts.append(f"\n--- CONVERSATION HISTORY ---\n{cw_history}\n")
                app_logger.debug("Using budget-aware conversation history from context window module")
            else:
                try:
                    session_data = await session_manager.get_session(self.user_uuid, self.session_id)
                    session_history = session_data.get('chat_object', []) if session_data else []

                    if session_history:
                        # Fallback: Format last 10 messages for context, filtering out invalid messages
                        history_text = "\n".join([
                            f"{'User' if msg.get('role') == 'user' else 'Assistant'}: {msg.get('content', '')}"
                            for msg in session_history[-10:]
                            if msg.get("isValid") is not False  # Skip messages marked as invalid
                        ])
                        if history_text:
                            parts.append(f"\n--- CONVERSATION HISTORY ---\n{history_text}\n")
                except Exception as e:
                    app_logger.error(f"Error retrieving conversation history for RAG synthesis: {e}", exc_info=True)
                    # Continue without history (graceful degradation)

        # Current query
        parts.append(f"\n--- CURRENT QUERY ---\n{self.original_user_input}\n")

        return "\n".join(parts)

    def _get_profile_config(self) -> Dict[str, Any]:
        """Get current profile configuration (with override support)."""
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()

        if self.profile_override_id:
            profiles = config_manager.get_profiles(self.user_uuid)
            override = next((p for p in profiles if p['id'] == self.profile_override_id), None)
            if override:
                return override

        default_profile_id = config_manager.get_default_profile_id(self.user_uuid)
        if default_profile_id:
            default = config_manager.get_profile(default_profile_id, self.user_uuid)
            if default:
                return default

        return {}


    def _format_knowledge_for_prompt(
        self,
        results: List[Dict[str, Any]],
        max_tokens: int = 2000
    ) -> str:
        """Format knowledge documents for prompt injection with token limiting."""
        if not results:
            return ""

        formatted_docs = []
        total_chars = 0
        char_limit = max_tokens * 4  # 1 token ≈ 4 chars

        for doc in results:
            content = doc.get("content", "")
            metadata = doc.get("metadata", {})
            # Get collection_name from document level (matching RAG retriever behavior)
            collection_name = doc.get("collection_name", "Unknown")

            # Try title first (user-friendly name), then filename
            source = metadata.get("title") or metadata.get("filename")

            # If no title or filename, fall back to collection name
            if not source:
                source = collection_name if collection_name != "Unknown" else "Unknown Source"

            doc_text = f"""
Source: {source} (Collection: {collection_name})
Content: {content}
---"""

            if total_chars + len(doc_text) > char_limit:
                app_logger.info(f"Knowledge context truncated at {len(formatted_docs)} documents")
                break

            formatted_docs.append(doc_text)
            total_chars += len(doc_text)

        return "\n".join(formatted_docs)


    async def _rerank_knowledge_with_llm(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        max_docs: int,
        _events: list = None,
    ) -> List[Dict[str, Any]]:
        """Rerank knowledge documents using LLM for improved relevance.

        §4 cost-transparency (cross-class): this billed LLM call flows through
        ``_call_llm_and_update_tokens`` (so it accumulates into the turn), but historically emitted
        NO cost event → its cost was invisible to ``_getTurnCost``'s event-sum, drifting the turn
        total for EVERY engine that reranks (Ideate/Focus/Conversation here, Optimize via the planner
        copy). When ``_events`` is provided, the reranking call's cost is appended as a cost-bearing
        step so the caller can route it into its persisted+summed event list (mirrors the item-2
        parse-retry fix)."""
        if not documents or not self.llm_handler:
            return documents

        docs_text = "\n\n".join([
            f"Document {i+1}:\n{doc.get('content', '')[:500]}"
            for i, doc in enumerate(documents)
        ])

        rerank_prompt = f"""You are helping rank documents by relevance to a query.

Query: {query}

Documents:
{docs_text}

Rank these documents by relevance to the query. Return ONLY a JSON array of document numbers in order of relevance (most relevant first).
Example: [3, 1, 5, 2, 4]

Response:"""

        try:
            response_text, _rr_in, _rr_out = await self._call_llm_and_update_tokens(
                prompt=rerank_prompt,
                reason="Knowledge Reranking",
                source="knowledge_retrieval"
            )
            # §4: surface this billed call's cost so the caller can persist it into a summed event
            # list — otherwise turn_cost (which includes it) drifts from the event-sum on reload.
            # Shape MUST be {type, payload:{details:{cost_usd}}} — that is exactly what the frontend
            # _getTurnCost `extractCost` reads (event.payload.details.cost_usd), matching the
            # convention of every entry in these lists (knowledge_events / llm_execution_events).
            if _events is not None:
                _events.append({
                    "type": "knowledge_reranking_llm",
                    "payload": {
                        "details": {
                            "summary": "Knowledge reranking",
                            "input_tokens": _rr_in,
                            "output_tokens": _rr_out,
                            "cost_usd": self._last_call_metadata.get("cost_usd", 0),
                        }
                    },
                })

            import re
            ranking = robust_json_parse(response_text)  # §5: repo-wide parse mandate
            if not isinstance(ranking, list):
                match = re.search(r'\[[\d,\s]+\]', response_text)
                ranking = robust_json_parse(match.group(0)) if match else None
            if isinstance(ranking, list):
                reranked = []
                for doc_num in ranking[:max_docs]:
                    if isinstance(doc_num, int) and 0 < doc_num <= len(documents):
                        reranked.append(documents[doc_num - 1])
                return reranked if reranked else documents[:max_docs]

        except Exception as e:
            app_logger.warning(f"Reranking failed, using original order: {e}")

        return documents[:max_docs]


    def _turn_duration_ms(self) -> int:
        """Wall-clock duration of the current turn in milliseconds.

        Measured from the canonical `turn_start_time` set at the top of run(), so
        every profile class reports latency on the same baseline. Returns 0 if the
        timer was never set (defensive — never raises, never blocks a turn).
        """
        start = getattr(self, "turn_start_time", None)
        if not start:
            return 0
        return max(0, int((time.time() - start) * 1000))

    def _emit_lifecycle_event(self, event_type: str, event_data: dict):
        """
        Emit lifecycle event with standardized structure.

        This helper creates lifecycle events (execution_start, execution_complete,
        execution_error, execution_cancelled) with consistent payload structure across
        all profile types.

        Args:
            event_type: Type of lifecycle event ('execution_start', 'execution_complete', etc.)
            event_data: Event-specific data to include in payload

        Returns:
            Formatted SSE event dict
        """
        # Build base payload with common fields
        base_payload = {
            "profile_type": event_data.get("profile_type", "tool_enabled"),
            "session_id": self.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn_id": self.current_turn_number,
            "profile_tag": event_data.get("profile_tag", self.active_profile_id),
            "provider": self.served_provider_model()[0],
            "model": self.served_provider_model()[1],
        }

        # Merge with event-specific data
        full_payload = {**base_payload, **event_data}

        # Return formatted SSE event
        return self._format_sse_with_depth({"type": event_type, "payload": full_payload}, event="notification")


    def _classify_error(self, exception: Exception) -> str:
        """
        Classify exception into user-friendly error type.

        This helper categorizes exceptions for better error reporting and tracking.

        Args:
            exception: The exception to classify

        Returns:
            String error type ('rate_limit', 'quota_exceeded', 'llm_error', 'tool_error', 'system_error')
        """
        error_str = str(exception).lower()

        if "rate limit" in error_str or "429" in error_str:
            return "rate_limit"
        elif "quota" in error_str or "insufficient" in error_str:
            return "quota_exceeded"
        elif "llm" in error_str or "model" in error_str or "anthropic" in error_str or "openai" in error_str:
            return "llm_error"
        elif "tool" in error_str or "mcp" in error_str:
            return "tool_error"
        elif "cancelled" in error_str or "canceled" in error_str:
            return "cancelled"
        else:
            return "system_error"


    async def run(self):
        """The main, unified execution loop for the agent."""
        final_answer_override = None

        # Canonical per-turn wall-clock start. Set once at the shared entry point so
        # EVERY engine (Ideate/Focus/Optimize/Coordinate/Conversation) measures turn
        # latency from the same baseline. _turn_duration_ms() reads this; it is the
        # single source of truth for the top-level `duration_ms` stamped on each turn.
        self.turn_start_time = time.time()

        # Open this turn's assurance tally at the SAME shared entry point, so every
        # engine gets it and gates whose seam has no turn context (preflight in the
        # MCP adapter, retrieval hygiene inside retrieve_examples) can record without
        # any signature threading. contextvars propagate into awaited coroutines and
        # child tasks, so concurrent turns cannot contaminate each other.
        try:
            from trusted_data_agent.agent import assurance_tally as _assurance_tally
            _assurance_tally.begin_turn()
        except Exception:  # noqa: BLE001 — never break a turn
            pass

        # SIGNED's per-turn fact: the agent's integrity AT ANSWER TIME, sealed into the
        # turn via the tally (every engine already persists the tally snapshot). This is
        # a claim about the past, so it is captured HERE, when the answer is produced —
        # resolving it at read time substituted today's badge for yesterday's fact,
        # flipping every historical turn's SIGNED station whenever the agent changed.
        # Defensive: the trust suite is removable, and an absent stamp reads as an
        # honest "not recorded", never as a failure.
        # PROP-009: the SAME resolution yields the turn's bill of materials — every signed
        # part the turn runs with — held for the chain seal and the turn stamp. Set (even to
        # None) at every turn start so an earlier turn's BOM can never leak into this one.
        try:
            from trusted_data_agent.core.trust import evidence as _evidence
            _evidence.set_turn_bom(None)
        except Exception:  # noqa: BLE001 — trust suite absent
            _evidence = None
        try:
            from trusted_data_agent.agent import assurance_tally as _assurance_tally
            from trusted_data_agent.core.trust.composition import turn_evidence
            _integ, _bom = turn_evidence(self.active_profile_id, self.user_uuid)
            if _integ:
                _assurance_tally.record("integrity", **_integ)
            if _bom and _evidence is not None:
                _evidence.set_turn_bom(_bom)
        except Exception:  # noqa: BLE001 — trust suite absent or unresolvable
            pass

        # --- CONSUMPTION ENFORCEMENT START ---
        # Check rate limits and token quotas BEFORE execution
        try:
            from trusted_data_agent.auth.database import get_db_session
            from trusted_data_agent.auth.consumption_manager import ConsumptionManager
            
            with get_db_session() as db_session:
                manager = ConsumptionManager(db_session)
                
                # Check rate limits
                rate_allowed, rate_reason = manager.check_rate_limits(self.user_uuid)
                if not rate_allowed:
                    error_msg = f"Rate limit exceeded: {rate_reason}"
                    app_logger.warning(f"Blocking execution for user {self.user_uuid}: {error_msg}")
                    raise ValueError(error_msg)
                
                # Check token quotas
                quota_allowed, quota_reason = manager.check_token_quota(self.user_uuid)
                if not quota_allowed:
                    error_msg = f"Token quota exceeded: {quota_reason}"
                    app_logger.warning(f"Blocking execution for user {self.user_uuid}: {error_msg}")
                    raise ValueError(error_msg)
                
                app_logger.debug(f"Consumption checks passed for user {self.user_uuid}")
        except ValueError:
            # Re-raise quota/rate limit errors
            raise
        except Exception as e:
            # Consumption enforcement failed unexpectedly (e.g. DB unavailable).
            # Execution is allowed to proceed (fail-open) to prevent locking all users out
            # during transient infrastructure issues, but we emit a visible warning event
            # and increment a process-level counter so operators can detect the bypass.
            APP_STATE.setdefault("consumption_enforcement_bypasses", 0)
            APP_STATE["consumption_enforcement_bypasses"] += 1
            bypass_count = APP_STATE["consumption_enforcement_bypasses"]
            app_logger.warning(
                f"[SECURITY] Consumption enforcement bypassed for user {self.user_uuid} "
                f"due to unexpected error (bypass #{bypass_count}): {e}"
            )
            # operator-visible: CRITICAL log + a throttled banner to connected admins
            from trusted_data_agent.core import quota_health as _quota_health
            _quota_health.report_bypass(self.user_uuid, f"{type(e).__name__}: {e}")
            bypass_event = {
                "step": "Security Warning",
                "type": "workaround",
                "details": (
                    f"Consumption limit check could not be completed ({type(e).__name__}). "
                    "Execution is proceeding, but quota limits may not be enforced. "
                    "Contact your administrator if this persists."
                ),
            }
            self._log_system_event(bypass_event)
            yield self._format_sse_with_depth(bypass_event)
        # --- CONSUMPTION ENFORCEMENT END ---
        
        # --- LLM-ONLY PROFILE: DIRECT EXECUTION PATH START ---
        # Detect if using LLM-only profile and bypass planner if so
        profile_type = self._detect_profile_type()
        # Check if llm_only profile has MCP tools enabled (routes to LangChain agent path instead)
        profile_config = self._get_profile_config()
        use_mcp_tools = profile_config.get("useMcpTools", False)
        # Check if profile has active component tools (e.g., TDA_Charting) — platform feature
        # Component tools require tool-calling capability, so profiles with active components
        # auto-upgrade to the conversation agent path (LangChain) even without MCP tools.
        from trusted_data_agent.components.manager import get_component_manager as _get_comp_mgr
        _comp_mgr = _get_comp_mgr()
        has_component_tools = _comp_mgr.has_active_tool_components(profile_config, self.user_uuid)
        # Only pure llm_only (without MCP tools AND without component tools) goes through direct execution
        is_llm_only = (profile_type == "llm_only" and not use_mcp_tools and not has_component_tools)

        # --- Document upload: Load document context with multimodal routing ---
        if self.attachments:
            self.multimodal_content, text_fallback = load_multimodal_document_content(
                self.user_uuid, self.session_id, self.attachments,
                self.current_provider, self.current_model
            )
            # Always set text context (for planning, text-only files, and as fallback)
            self.document_context = text_fallback
            if not self.document_context and not self.multimodal_content:
                # Pure fallback: use text extraction for everything
                self.document_context, doc_trunc_events = load_document_context(self.user_uuid, self.session_id, self.attachments, max_total_chars=getattr(self, '_cw_document_max_chars', None))
                for evt in doc_trunc_events:
                    event_data = {"step": "Context Optimization", "type": "context_optimization", "details": evt}
                    self._log_system_event(event_data)
                    yield self._format_sse_with_depth(event_data)

            if self.multimodal_content:
                app_logger.info(f"Native multimodal: {len(self.multimodal_content)} block(s) for {self.current_provider}")
            if self.document_context:
                app_logger.info(f"Document context loaded: {len(self.document_context):,} chars from {len(self.attachments)} attachment(s)")

            # --- S9 Grounded Multimodal: every attachment the platform READ at upload becomes
            # a source of THIS turn (verified rows/text → grounding evidence + S3 result sets);
            # an image nothing could read may be transcribed by the model (unverified) when the
            # profile opts in. Off ⇒ nothing here runs. Never breaks a turn.
            try:
                _src_extra, _src_event = await self._load_attachment_sources()
                if _src_extra:
                    self._attachment_sources_text = _src_extra
                    self.document_context = ((self.document_context or "").rstrip() + "\n\n" + _src_extra).strip()
                if _src_event:
                    event_data = {"step": "Attachments Read as Sources", "type": "attachment_sources", "details": _src_event}
                    self._log_system_event(event_data)
                    yield self._format_sse_with_depth(event_data)
            except Exception as _ase:  # noqa: BLE001
                app_logger.debug(f"[attachment-sources] seam skipped: {_ase}")

        # --- MODIFICATION START: Calculate turn number from workflow_history ---
        # All profile types (llm_only, rag_focused, tool_enabled) use workflow_history for consistency
        self.current_turn_number = 1
        session_data = await session_manager.get_session(self.user_uuid, self.session_id)

        if session_data and isinstance(session_data.get("last_turn_data", {}).get("workflow_history"), list):
            self.current_turn_number = len(session_data["last_turn_data"]["workflow_history"]) + 1
        # PROP-011: the needs-input ledger stamps the turn a question was raised in
        try:
            from trusted_data_agent.agent import input_requests as _ir_mod
            _ir_mod.set_turn(self.current_turn_number)
        except Exception:  # noqa: BLE001
            pass

        # PROP-010: every turn that reads an attachment is recorded on the file ("Used in").
        if self.attachments:
            try:
                from trusted_data_agent.filestore import service as _files_service
                for _att in self.attachments:
                    _fid = (_att or {}).get("file_id") if isinstance(_att, dict) else None
                    if _fid:
                        _files_service.record_use(_fid, self.user_uuid, self.session_id, self.current_turn_number)
            except Exception as _fue:  # noqa: BLE001 — provenance must never break a turn
                app_logger.debug(f"[files] attachment use not recorded: {_fue}")

        # Publish the turn number into workflow_state so component handlers invoked
        # mid-turn (e.g. TDA_Dashboard) can stamp source_turn_id on created tiles.
        # This is the single authoritative source for the value during execution.
        if isinstance(self.workflow_state, dict):
            self.workflow_state["current_turn_number"] = self.current_turn_number
        if self.answer_stream is not None:
            self.answer_stream.turn_id = self.current_turn_number

        is_rag_focused = (profile_type == "rag_focused")
        profile_label = "rag_focused" if is_rag_focused else ("llm_only" if is_llm_only else "tool_enabled")
        app_logger.info(f"PlanExecutor initialized for {profile_label} turn: {self.current_turn_number}")
        # --- MODIFICATION END ---

        # --- EXECUTION PROVENANCE CHAIN (EPC) INIT ---
        # QW6 (assessment §2): initialise the chain AFTER current_turn_number is computed above.
        # Previously this ran before the turn-number block, so the provenance record (and every
        # signed attestation derived from it) was sealed with turn_number=0 for all non-genie
        # engines. Nothing between the old and new position references self.provenance.
        from trusted_data_agent.core.provenance import ProvenanceChain, get_previous_turn_tip_hash
        prev_tip = await get_previous_turn_tip_hash(self.user_uuid, self.session_id)
        self.provenance = ProvenanceChain(
            session_id=self.session_id,
            turn_number=self.current_turn_number,
            user_uuid=self.user_uuid,
            profile_type=profile_type,
            previous_turn_tip_hash=prev_tip,
            event_queue=asyncio.Queue(maxsize=100),
        )
        # --- EPC INIT END ---

        # --- Tier 1.3 increment 2b — INBOUND prompt-injection guardrail (charter #14) ---
        # One scan at the shared dispatch entry covers every engine. Runs before any LLM call /
        # context assembly, so a blocked request costs nothing. observe/annotate emit the verdict
        # for observability and let the turn proceed (the engine stamps `guardrail_inbound`);
        # enforce + a detected injection short-circuits to a self-contained refusal terminal —
        # the injected text is never sent to the model. Default observe + master off → no-op.
        self._inbound_guardrail_event = None
        self._honesty_event = None  # Honesty post-condition verdict (set at the end-of-turn seam)
        self._compute_lineage_event = None  # S3 number lineage (set at the end-of-turn seam)
        self._citation_event = None  # F-001 cited-but-unread verdict (end-of-turn seam)
        try:
            _ib_text, _inbound_event = self._run_guardrail_inbound(self.original_user_input)
            # stamp ALWAYS (the audit fact); render only when it acted.
            self._inbound_guardrail_event = _inbound_event
            if (_inbound_event or {}).get("acted"):
                if (_inbound_event.get("action") == "block"):
                    async for _ev in self._emit_inbound_guardrail_block(_inbound_event):
                        yield _ev
                    return
                # observe / annotate: surface the detection live; the turn continues.
                _ib_live = {"step": "Guardrail (input)", "type": "guardrail",
                            "details": _inbound_event}
                self._log_system_event(_ib_live)
                yield self._format_sse_with_depth(_ib_live)
            elif _inbound_event:
                # It RAN and found nothing — the earliest thing knowable about a turn, and
                # until now it was silent: the stamp was recorded for audit and no surface
                # moved. The dial sat fully pending because every event wired to it fires
                # in the end-of-turn seam, so the whole ring settled at once.
                #
                # PROGRESS ONLY, and NOT persisted: it draws no card (the trace should
                # describe what the turn did, not carry a line per clean pass) and it does
                # not colour the station — GUARDED still holds the outbound guard, which
                # has not run. The dial keeps it pending and shows what has come in.
                yield self._format_sse_with_depth({
                    "step": "Inbound guard", "type": "assurance_progress",
                    "details": {"mechanism": "guardrail", "direction": "inbound",
                                "ran": True, "acted": False, "action": "pass"},
                })
        except Exception as _ib_err:  # noqa: BLE001 — fail open, never block a turn on a gate bug
            app_logger.debug(f"[Guardrail/inbound] scan skipped: {_ib_err}")

        # --- S6 The Governed Tool Surface (Optimize): armed BEFORE assembly so the
        # tool_definitions module renders the surface; the planner RE-SELECTS once the KG
        # enrichment has named the tables in play (a re-select can only widen).
        if profile_type == 'tool_enabled':
            try:
                if self._arm_tool_surface_optimize() is not None:
                    _sev = self.surface_event()
                    if _sev:
                        self._log_system_event(_sev)
                        yield self._format_sse_with_depth(_sev)
            except Exception as _surf_err:  # noqa: BLE001
                app_logger.debug(f"[surface] pre-assembly arming skipped: {_surf_err}")

        # --- CONTEXT WINDOW MANAGER (Feature-Flagged) ---
        if APP_CONFIG.USE_CONTEXT_WINDOW_MANAGER:
            assembled_context = await self._run_context_window_assembly(profile_type, profile_config)
            if assembled_context and assembled_context.snapshot:
                # Emit snapshot as SSE event for observability
                snapshot_event = {
                    "step": "Context Window Assembly",
                    "type": "context_window_snapshot",
                    "details": assembled_context.snapshot.to_summary_text(),
                    "payload": assembled_context.snapshot.to_sse_event(),
                }
                self._log_system_event(snapshot_event)
                self.context_window_snapshot_event = assembled_context.snapshot.to_sse_event()
                yield self._format_sse_with_depth(snapshot_event)
                # Live-dial progressive engagement: if knowledge was retrieved, GROUNDED's
                # machinery begins to have something to say — surface it as PROGRESS the
                # moment it happens (never a verdict; the station stays pending until the
                # grounding gate reports). Draws no card; moves only the instrument. The
                # consumer watches grounding retrieve instead of a ring that settles at the
                # end. Fully additive; wrapped so a live cue can never break the turn.
                try:
                    _chunks = 0
                    for _cm in (assembled_context.snapshot.contributions or []):
                        if _cm.module_id == "knowledge_context" and _cm.tokens_used > 0:
                            _chunks = _cm.tokens_used
                            break
                    if _chunks > 0:
                        yield self._format_sse_with_depth({
                            "step": "Retrieval", "type": "assurance_progress",
                            "details": {"mechanism": "retrieval", "ran": True,
                                        "acted": False, "action": "pass"},
                        })
                except Exception:  # noqa: BLE001 — a live cue must never break a turn
                    pass
            # Extract budget-aware contributions for downstream use
            if assembled_context:
                # Conversation history: text content + window size
                cw_hist = assembled_context.contributions.get("conversation_history")
                if cw_hist:
                    if cw_hist.content:
                        self._cw_conversation_history = cw_hist.content
                    if cw_hist.metadata.get("turn_count", 0) > 0:
                        self._cw_history_window = cw_hist.metadata["turn_count"]
                        app_logger.info(
                            f"Context window: conversation_history budget-aware window={self._cw_history_window} "
                            f"({cw_hist.tokens_used:,} tokens, "
                            f"mode={cw_hist.metadata.get('mode', 'unknown')})"
                        )

                # Knowledge + Document context: budget-aware limits from allocation
                if assembled_context.snapshot:
                    for cm in assembled_context.snapshot.contributions:
                        if cm.module_id == "knowledge_context" and cm.tokens_allocated > 0:
                            self._cw_knowledge_max_tokens = cm.tokens_allocated
                            app_logger.info(
                                f"Context window: knowledge_context budget={cm.tokens_allocated:,} tokens "
                                f"(used={cm.tokens_used:,})"
                            )
                        elif cm.module_id == "document_context":
                            self._cw_document_max_chars = document_char_limit(cm.tokens_allocated)
                            app_logger.info(
                                f"Context window: document_context budget={cm.tokens_allocated:,} tokens "
                                f"(limit {self._cw_document_max_chars:,} chars, used={cm.tokens_used:,})"
                            )

                # Documents: the module's text IS what the turn sends (it was measured against the
                # budget, condensed if needed, and may carry passages searched from beyond the
                # excerpt). Native multimodal blocks stay as loaded; S9's readings are re-appended.
                cw_doc = assembled_context.contributions.get("document_context")
                if cw_doc and cw_doc.content:
                    self._cw_document_text = cw_doc.content
                    self._cw_document_events = list(cw_doc.metadata.get("events") or [])
                    if not self.multimodal_content:
                        extra = getattr(self, "_attachment_sources_text", None)
                        self.document_context = (cw_doc.content.rstrip() + ("\n\n" + extra if extra and extra not in cw_doc.content else "")).strip()

                # Component instructions: pre-computed content (Phase 5e consolidation)
                cw_comp = assembled_context.contributions.get("component_instructions")
                if cw_comp and cw_comp.content:
                    self._cw_component_instructions = cw_comp.content
                    app_logger.info(
                        f"Context window: component_instructions pre-computed "
                        f"({cw_comp.tokens_used:,} tokens, {len(cw_comp.content):,} chars)"
                    )

                # Knowledge graph: budget-managed enrichment text + the
                # kg_enrichment_event the grounding gate / replay consume. The
                # module ran for this profile type (tool_enabled/llm_only/
                # rag_focused) only — for those, _get_kg_enrichment() returns
                # this cached value instead of re-fetching. Setting the flag
                # (even on an empty contribution) prevents a redundant direct
                # fetch when the module ran but found no query-relevant subgraph.
                cw_kg = assembled_context.contributions.get("knowledge_graph_context")
                if cw_kg is not None:
                    self._cw_kg_text = cw_kg.content or ""
                    self._cw_kg_assembled = True
                    kg_event = (cw_kg.metadata or {}).get("kg_enrichment_event")
                    if kg_event:
                        self.kg_enrichment_event = kg_event
                        app_logger.info(
                            f"Context window: knowledge_graph budget-managed "
                            f"({cw_kg.tokens_used:,} tokens, "
                            f"max_nodes={cw_kg.metadata.get('max_nodes')}, "
                            f"entities={cw_kg.metadata.get('entities')})"
                        )

                # RAG Trust Hardening Wave 1 (E2): cache the knowledge_context module's
                # served chunks + retrieval outcome so the grounding gate's corpus-support
                # family and the sufficiency-honesty gate reach every module-fed engine
                # (Ideate/Optimize/Conversation) — the _get_kg_enrichment cache pattern.
                cw_knowledge = assembled_context.contributions.get("knowledge_context")
                if cw_knowledge is not None:
                    md = cw_knowledge.metadata or {}
                    self._cw_knowledge_chunks = list(md.get("chunks") or [])
                    _reason = md.get("reason")
                    self._cw_knowledge_retrieval = {
                        "searched": _reason not in ("no_collections", "no_query",
                                                    "knowledge_disabled",
                                                    "no_retriever_instance"),
                        "docs_retrieved": int(md.get("docs_retrieved") or 0),
                        "top_score": float(md.get("top_score") or 0.0),
                        "scores": list(md.get("scores") or []),
                        "reason": _reason,
                    }

                # Findings: the verified results of earlier turns, budgeted and pinned by the
                # module — the engines append the SAME text after the KG enrichment, and the
                # grounding gate reads it as evidence the turn's inputs vouch for.
                cw_findings = assembled_context.contributions.get("findings")
                if cw_findings is not None and cw_findings.content:
                    self._cw_findings_text = cw_findings.content
                    app_logger.info(
                        f"Context window: findings delivered "
                        f"({cw_findings.tokens_used:,} tokens, turns={cw_findings.metadata.get('turns')})"
                    )

                # Agent Memory: the user_memory module's recalled block — delivered with the
                # findings on every engine (it had no reader before Nov 2026).
                cw_memory = assembled_context.contributions.get("user_memory")
                if cw_memory is not None and cw_memory.content:
                    self._cw_memory_text = cw_memory.content
                    app_logger.info(
                        f"Context window: agent memory delivered "
                        f"({cw_memory.tokens_used:,} tokens, recalled={cw_memory.metadata.get('recalled')})"
                    )
        # --- CONTEXT WINDOW MANAGER END ---

        # --- PROFILE LLM INSTANCE: Create profile-specific LLM client when provider differs ---
        # The global APP_STATE['llm'] is set at login from the user's default profile.
        # When the active profile (via @TAG override) uses a different LLM provider,
        # we need a matching client instance. This is stored on self.profile_llm_instance
        # (local to this executor, no global state modification) and used by _call_llm_and_update_tokens.
        # The tool_enabled path has its own override mechanism at line ~3400, so this
        # primarily serves llm_only, rag_focused, and conversation_with_tools profiles.
        current_user_provider, current_user_model = self._profile_llm_baseline()
        if self.current_provider and (self.current_provider != current_user_provider or self.current_model != current_user_model):
            app_logger.info(f"🔄 Profile uses different LLM than default: {self.current_provider}/{self.current_model} vs {current_user_provider}/{current_user_model}")
            try:
                from trusted_data_agent.llm.client_factory import get_or_create_llm_client
                from trusted_data_agent.core.configuration_service import retrieve_credentials_for_provider

                credentials_result = await retrieve_credentials_for_provider(self.user_uuid, self.current_provider)
                credentials = credentials_result.get("credentials", {}) or {}

                # Always merge LLM config credentials — providers like OpenRouter store their
                # API key only in the LLM config, not in the global credential store.
                try:
                    from trusted_data_agent.core.config_manager import get_config_manager
                    config_manager = get_config_manager()
                    if self.active_profile_id:
                        profiles = config_manager.get_profiles(self.user_uuid)
                        active_profile = next((p for p in profiles if p.get("id") == self.active_profile_id), None)
                        if active_profile:
                            llm_config_id = active_profile.get('llmConfigurationId')
                            if llm_config_id:
                                llm_configs = config_manager.get_llm_configurations(self.user_uuid)
                                llm_config = next((cfg for cfg in llm_configs if cfg['id'] == llm_config_id), None)
                                if llm_config and llm_config.get('credentials'):
                                    credentials = {**credentials, **llm_config['credentials']}
                except Exception as e:
                    app_logger.debug(f"Could not merge profile LLM config credentials: {e}")

                if credentials:
                    self.profile_llm_instance = await get_or_create_llm_client(self.current_provider, self.current_model, credentials)
                    app_logger.info(f"✅ Created profile-specific LLM instance: {self.current_provider}/{self.current_model}")
                else:
                    app_logger.warning(f"No credentials found for profile provider {self.current_provider}, falling back to global LLM instance")
            except Exception as e:
                app_logger.warning(f"Failed to create profile-specific LLM instance for {self.current_provider}/{self.current_model}: {e}")
                app_logger.warning(f"Falling back to global LLM instance — provider branching in call_llm_api will still use correct provider path")
        # --- PROFILE LLM INSTANCE END ---

        # --- DUAL-MODEL LLM INSTANCES: Create strategic and tactical clients ---
        # When dual-model is active, create separate client instances for each model
        # because they may use different providers (e.g., Google for strategic, Friendli for tactical)
        if self.is_dual_model_active:
            try:
                from trusted_data_agent.llm.client_factory import get_or_create_llm_client
                from trusted_data_agent.auth.encryption import decrypt_credentials

                # Create strategic LLM instance
                # Credentials are stored per-provider in user_credentials table, not in LLM configs
                strategic_creds = decrypt_credentials(self.user_uuid, self.strategic_provider)
                if not strategic_creds:
                    raise ValueError(f"No credentials found for strategic model provider: {self.strategic_provider}")

                self.strategic_llm_instance = await get_or_create_llm_client(
                    self.strategic_provider,
                    self.strategic_model,
                    strategic_creds
                )
                app_logger.info(f"✅ Created strategic LLM instance: {self.strategic_provider}/{self.strategic_model}")

                # Create tactical LLM instance
                # Credentials are stored per-provider in user_credentials table, not in LLM configs
                tactical_creds = decrypt_credentials(self.user_uuid, self.tactical_provider)
                if not tactical_creds:
                    raise ValueError(f"No credentials found for tactical model provider: {self.tactical_provider}")

                self.tactical_llm_instance = await get_or_create_llm_client(
                    self.tactical_provider,
                    self.tactical_model,
                    tactical_creds
                )
                app_logger.info(f"✅ Created tactical LLM instance: {self.tactical_provider}/{self.tactical_model}")

            except Exception as e:
                app_logger.error(f"Failed to create dual-model LLM instances: {e}")
                # Fall back to single-model mode - reset provider/model to base
                app_logger.warning(f"Dual-model disabled, falling back to single model: {self.current_provider}/{self.current_model}")
                self.is_dual_model_active = False
                self.strategic_llm_instance = None
                self.tactical_llm_instance = None
                self.strategic_provider = self.current_provider
                self.strategic_model = self.current_model
                self.tactical_provider = self.current_provider
                self.tactical_model = self.current_model
                app_logger.warning(f"Dual-model disabled, falling back to single model: {self.current_provider}/{self.current_model}")
        # --- DUAL-MODEL LLM INSTANCES END ---

        # --- Save dual-model configuration to session for UI display ---
        if self.is_dual_model_active and session_data:
            try:
                session_data["is_dual_model_active"] = True
                session_data["strategic_provider"] = self.strategic_provider
                session_data["strategic_model"] = self.strategic_model
                session_data["tactical_provider"] = self.tactical_provider
                session_data["tactical_model"] = self.tactical_model
                await session_manager.save_session(self.user_uuid, self.session_id, session_data)
                app_logger.debug(f"[Dual-Model] Saved configuration to session: strategic={self.strategic_provider}/{self.strategic_model}, tactical={self.tactical_provider}/{self.tactical_model}")
            except Exception as e:
                app_logger.warning(f"Failed to save dual-model configuration to session: {e}")
        elif session_data:
            # Clear dual-model metadata when switching to single-model (e.g., profile override expires)
            try:
                session_data["is_dual_model_active"] = False
                # Remove dual-model specific fields to prevent stale UI indicators
                session_data.pop("strategic_provider", None)
                session_data.pop("strategic_model", None)
                session_data.pop("tactical_provider", None)
                session_data.pop("tactical_model", None)
                await session_manager.save_session(self.user_uuid, self.session_id, session_data)
                app_logger.debug("[Dual-Model] Cleared dual-model metadata (switched to single-model)")
            except Exception as e:
                app_logger.warning(f"Failed to clear dual-model metadata: {e}")
        # --- Dual-model configuration saved ---

        # --- Session primer as a BRIEFING (agent/primer.py) ---
        # A briefing primer offers NO tools on its turn: the tool-bearing engines
        # (ReAct, Optimize) would otherwise read the briefing as a task. Routed to the
        # direct engine, which answers from the profile's own LLM and context alone; a
        # warm-up primer (the default, `allow_tools` absent or true) is untouched.
        from trusted_data_agent.agent.primer import is_briefing_turn, BRIEFING_NOTE
        _briefing = is_briefing_turn(profile_config, self.is_session_primer)
        if _briefing and profile_type in ("llm_only", "tool_enabled") and not is_llm_only:
            _bn = {"step": "Session Primer", "type": "system_message",
                   "details": {"summary": BRIEFING_NOTE}}
            self._log_system_event(_bn)
            yield self._format_sse_with_depth(_bn)
            is_llm_only = True

        if is_llm_only:
            # DIRECT EXECUTION PATH - Delegated to IdeateEngine
            from trusted_data_agent.agent.engines.ideate_engine import IdeateEngine
            async for event in self._with_progress(IdeateEngine().run(self)):
                yield event
            return
        # --- LLM-ONLY PROFILE: DIRECT EXECUTION PATH END (delegated to IdeateEngine) ---

        # --- CONVERSATION WITH TOOLS: LANGCHAIN AGENT PATH ---
        # Routes here when llm_only profile has MCP tools OR active component tools.
        # has_component_tools requires a runtime check (component manager), so applies_to()
        # on ConversationEngine only covers useMcpTools=True; the component-tools case is
        # detected here and also delegated to ConversationEngine.
        is_conversation_with_tools = (profile_type == "llm_only" and (use_mcp_tools or has_component_tools))
        if is_conversation_with_tools:
            from trusted_data_agent.agent.engines.conversation_engine import ConversationEngine
            async for event in self._with_progress(ConversationEngine().run(self)):
                yield event
            return
        # --- CONVERSATION WITH TOOLS END ---

        # --- RAG FOCUSED EXECUTION PATH ---
        is_rag_focused = self._is_rag_focused_profile()
        if is_rag_focused:
            # DIRECT EXECUTION PATH - Delegated to FocusEngine
            from trusted_data_agent.agent.engines.focus_engine import FocusEngine
            async for event in self._with_progress(FocusEngine().run(self)):
                yield event
            return


        # DIRECT EXECUTION PATH - Delegated to OptimizeEngine
        from trusted_data_agent.agent.engines.optimize_engine import OptimizeEngine
        async for event in self._with_progress(OptimizeEngine().run(self)):
            yield event
    # --- END of run method ---


    async def _save_partial_turn_data(self, status: str, error_message: str = None, error_details: str = None):
        """
        Saves partial turn data for cancelled/error turns.

        This ensures that even failed turns have retrievable data for the UI,
        including accumulated tokens and any execution trace up to the failure point.

        Args:
            status: One of "cancelled", "error"
            error_message: Human-readable error message (optional)
            error_details: Technical error details (optional)
        """
        # Only save for top-level executor
        if self.execution_depth != 0:
            app_logger.debug(f"Skipping partial turn save for nested executor (depth={self.execution_depth})")
            return
        # Idempotent: an engine's own cancel/error path and the execution seam's stream
        # cleanup may both reach here for one turn — the first save wins.
        if self._partial_turn_saved:
            return
        self._partial_turn_saved = True

        # The Governed Stream (S1): the answer text that ARRIVED before the stop is the
        # turn's answer — kept, marked partial, never replaced by a bare "stopped" line.
        _partial_text = ""
        _stream_summary = None
        if self.answer_stream is not None:
            _partial_text = self.answer_stream.delivered_text or ""
            _stream_summary = self.answer_stream.summary()

        try:
            profile_tag = self._get_current_profile_tag()

            # Get session data for session token totals (needed for plan reload display)
            session_data = await session_manager.get_session(self.user_uuid, self.session_id)
            session_input_tokens = session_data.get("input_tokens", 0) if session_data else 0
            session_output_tokens = session_data.get("output_tokens", 0) if session_data else 0

            # §3.3: turn cost for partial/error turn = Σ per-call costs (dual-model aware).
            turn_cost = self._resolve_turn_cost_usd()
            app_logger.debug(f"[tool_enabled-partial] Turn {self.current_turn_number} cost (Σ per-call): ${turn_cost:.6f}")

            # Calculate session cost (cumulative up to and including this turn)
            session_cost_usd = 0.0
            try:
                previous_session_cost = self._calculate_session_cost_at_turn(session_data)
                session_cost_usd = previous_session_cost + turn_cost
                app_logger.debug(f"[tool_enabled-partial] Session cost at turn {self.current_turn_number}: ${session_cost_usd:.6f}")
            except Exception as e:
                app_logger.warning(f"Failed to calculate session cost for partial turn: {e}")

            turn_summary = {
                "turn": self.current_turn_number,
                "user_query": self.original_user_input,
                "is_session_primer": self.is_session_primer,  # Flag for RAG case filtering
                "raw_llm_plan": self.raw_llm_plan,  # LLM's raw output (None if error before planning)
                "original_plan": self.original_plan_for_history,  # May be None if error before planning
                "execution_trace": self.turn_action_history,  # Partial trace up to failure
                "final_summary": None,  # No final summary for failed turns
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": self.served_provider_model()[0],
                "model": self.served_provider_model()[1],
                "profile_tag": profile_tag,
                "task_id": self.task_id,
                "turn_input_tokens": self.turn_input_tokens,  # Accumulated tokens up to failure
                "turn_output_tokens": self.turn_output_tokens,
                "turn_cost": turn_cost,  # NEW - Cost at time of error/cancellation
                "session_cost_usd": session_cost_usd,  # NEW - Cumulative cost snapshot
                # Session totals at the time of this turn (for plan reload)
                "session_input_tokens": session_input_tokens,
                "session_output_tokens": session_output_tokens,
                "session_id": self.session_id,
                "mcp_server_id": self.effective_mcp_server_id or APP_CONFIG.CURRENT_MCP_SERVER_ID,
                "user_uuid": self.user_uuid,
                "rag_source_collection_id": getattr(self, 'rag_source_collection_id', None),
                "case_id": getattr(self, 'rag_source_case_id', None),
                "rag_case_count": getattr(self, 'rag_case_count', None),
                "knowledge_accessed": getattr(self, 'knowledge_accessed', []),
                "knowledge_retrieval_event": getattr(self, 'knowledge_retrieval_event', None),
                "context_window_snapshot_event": self._snapshot_with_distillation_events(),
                "strategic_context_snapshot_event": getattr(self, 'strategic_context_snapshot_event', None),
                "tool_enabled_events": getattr(self, 'tool_enabled_events', []),  # Partial lifecycle events for tool_enabled profiles
                # Status fields for partial data
                "status": status,  # "cancelled" or "error"
                "error_message": error_message,
                "error_details": error_details,
                "is_partial": True,  # Flag to indicate incomplete execution
                # S1: the partial answer (if any) IS this turn's answer text
                "final_summary_text": _partial_text or None,
                "partial_answer": bool(_partial_text),
                "answer_stream": _stream_summary,
                "skills_applied": self.skill_result.to_applied_list() if self.skill_result and self.skill_result.has_content else []
            }

            # The turn's assurance stamps — the integrity at answer time, the inbound screen, the
            # budget — persist on a failed turn too (every engine's success path carries them;
            # without them a failed turn read "predates tracing" and no integrity stamp — trust
            # campaign F1/F2 on Optimize). The turn's own keys win.
            try:
                from trusted_data_agent.agent import assurance_tally as _tally
                for _k, _v in (_tally.snapshot() or {}).items():
                    turn_summary.setdefault(_k, _v)
            except Exception:  # noqa: BLE001 — never break the failure path
                pass

            # --- EPC: Seal provenance chain on error/cancellation ---
            try:
                if hasattr(self, 'provenance') and not self.provenance._sealed:
                    self.provenance.add_error_step(
                        status, error_message or "Unknown error")
                    turn_summary.update(self.provenance.finalize())
            except Exception as _epc_err:
                app_logger.debug(f"[EPC] error finalize: {_epc_err}")
            # --- EPC END ---

            await session_manager.update_last_turn_data(self.user_uuid, self.session_id, turn_summary)
            app_logger.info(f"Saved partial turn data (status={status}) for turn {self.current_turn_number}")

            # C14 (negative-learning substitute): a champion that GUIDED this now-failed turn
            # accrues a replay failure; after N consecutive REAL failures it is auto-demoted
            # (excluded from retrieval). Transient failures — user cancellation, provider timeout
            # — are ignored so a blip can't retire a good champion. No-op if the turn wasn't
            # champion-guided. Fails soft (never breaks the failure-save path).
            try:
                _case_id = getattr(self, 'rag_source_case_id', None)
                if getattr(self, 'rag_retriever', None) and _case_id:
                    _err_text = f"{error_message or ''} {error_details or ''}".lower()
                    _transient = (
                        status == "cancelled"
                        or "did not respond within" in _err_text
                        or "timed out" in _err_text
                        or "timeout" in _err_text
                    )
                    await self.rag_retriever.record_champion_replay_outcome(
                        _case_id, succeeded=False, is_transient=_transient)
            except Exception as _demote_err:
                app_logger.debug(f"[C14] champion replay-outcome (failure) skipped: {_demote_err}")

            # Save the cancelled/error message to conversation history so it appears on session reload
            status_tag = "CANCELLED" if status == "cancelled" else "ERROR"
            if _partial_text:
                # S1: the answer that arrived is kept, rendered like any other, marked partial.
                from trusted_data_agent.agent import answer_stream as _as
                assistant_message = _as.partial_html(_partial_text)
                plain_content = f"{_partial_text}\n\n[{_as.PARTIAL_NOTE}]"
            else:
                assistant_message = f'<span class="{status}-tag">{status_tag}</span> {error_message or "Execution did not complete."}'
                plain_content = error_message or "Execution did not complete."
            await session_manager.add_message_to_histories(
                self.user_uuid,
                self.session_id,
                role='assistant',
                content=plain_content,  # Plain text for LLM
                html_content=assistant_message,  # HTML with tag for UI
                profile_tag=profile_tag
            )
            app_logger.info(f"Saved {status} message to conversation history for turn {self.current_turn_number}")

            # NOTE: Do NOT add to RAG processing queue for failed turns
            # Failed turns should not be used as champion cases

        except Exception as e:
            app_logger.error(f"Failed to save partial turn data: {e}", exc_info=True)
            # Don't re-raise - this is best-effort cleanup

    async def _handle_single_prompt_plan(self, planner: Planner):
        """Orchestrates the logic for expanding a single-prompt plan."""
        single_phase = self.meta_plan[0]
        prompt_name = single_phase.get('executable_prompt')
        prompt_args = single_phase.get('arguments', {})

        # TDA_ContextReport is a client-side tool, not an MCP prompt.
        # If LLM put it in executable_prompt, convert to relevant_tools and exit.
        # This allows the normal tool execution path to handle it (e.g., for knowledge queries).
        if prompt_name == 'TDA_ContextReport':
            app_logger.info(f"PLAN CORRECTION: '{prompt_name}' is a tool, not a prompt. Converting to tool execution.")
            single_phase['relevant_tools'] = [prompt_name]
            if 'executable_prompt' in single_phase:
                del single_phase['executable_prompt']
            self.is_single_prompt_plan = False
            return  # Exit - plan will be executed as a tool call

        event_data = {
            "step": "System Correction", "type": "workaround",
            "details": f"Single Prompt('{prompt_name}') identified. Expanding plan in-process to improve efficiency."
        }
        self._log_system_event(event_data)
        yield self._format_sse_with_depth(event_data)

        prompt_info = self._get_prompt_info(prompt_name)
        if prompt_info:
            required_args = {arg['name'] for arg in prompt_info.get('arguments', []) if arg.get('required')}
            missing_args = required_args - set(prompt_args.keys())

            if missing_args:
                event_data = {
                    "step": "System Correction", "type": "workaround",
                    "details": f"Prompt '{prompt_name}' is missing required arguments: {missing_args}. Attempting to extract from user query."
                }
                self._log_system_event(event_data)
                yield self._format_sse_with_depth(event_data)

                enrichment_prompt = (
                    f"You are an expert argument extractor. From the user's query, extract the values for the following missing arguments: {list(missing_args)}. "
                    f"User Query: \"{self.original_user_input}\"\n"
                    "Respond with only a single, valid JSON object mapping the argument names to their extracted values."
                )
                reason = f"Extracting missing arguments for prompt '{prompt_name}'"

                call_id = str(uuid.uuid4())
                event_data = {
                    "step": "Calling LLM for Argument Enrichment",
                    "type": "system_message",
                    "details": {"summary": reason, "call_id": call_id}
                }
                self._log_system_event(event_data)
                yield self._format_sse_with_depth(event_data)
                yield self._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")

                response_text, input_tokens, output_tokens = await self._call_llm_and_update_tokens(
                    prompt=enrichment_prompt, reason=reason,
                    system_prompt_override="You are a JSON-only responding assistant.",
                    raise_on_error=True,
                    source=self.source
                )

                # Log post-LLM system_message with tokens + cost for historical replay
                enrichment_log_event = {
                    "step": "Calling LLM for Argument Enrichment",
                    "type": "system_message",
                    "details": {
                        "summary": reason,
                        "call_id": call_id,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": self._last_call_metadata.get("cost_usd", 0)
                    }
                }
                self._log_system_event(enrichment_log_event)

                updated_session = await session_manager.get_session(self.user_uuid, self.session_id)
                if updated_session:
                    yield self._format_sse_with_depth({
                        "statement_input": input_tokens, "statement_output": output_tokens,
                        "turn_input": self.turn_input_tokens,
                        "turn_output": self.turn_output_tokens,
                        "total_input": updated_session.get("input_tokens", 0),
                        "total_output": updated_session.get("output_tokens", 0),
                        "call_id": call_id,
                        "cost_usd": self._last_call_metadata.get("cost_usd", 0)
                    }, "token_update")

                yield self._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

                # §5: robust_json_parse per the repo-wide parse-hardening mandate.
                extracted_args = robust_json_parse(response_text)
                if isinstance(extracted_args, dict):
                    prompt_args.update(extracted_args)
                    app_logger.info(f"Successfully enriched arguments: {extracted_args}")
                else:
                    app_logger.error("Failed to parse extracted arguments from LLM response. The prompt may fail.")

        self.active_prompt_name = prompt_name
        self.prompt_arguments = self._resolve_arguments(prompt_args)
        self.prompt_type = prompt_info.get("prompt_type", "reporting") if prompt_info else "reporting"

        # Regenerate the plan based on the expanded prompt
        async for event in planner.generate_and_refine_plan():
            yield event

    # --- Phase parallelization helpers ---

    def _get_phase_state(self, phase_num: int) -> dict:
        """Return (or lazily create) isolated per-phase state for parallel execution.

        Parallel phases run in the same PlanExecutor and share its attributes, but
        each phase must have its own correction_history (Reflexion) and last_action_str
        (loop-detection) so that failures and loop checks from one phase don't corrupt
        another.  Sequential phases also use this — the state is just never contended.

        Keys: 'correction_history' (list), 'last_action_str' (str | None)
        """
        if phase_num not in self._phase_parallel_state:
            self._phase_parallel_state[phase_num] = {
                'correction_history': [],
                'last_action_str': None,
            }
        return self._phase_parallel_state[phase_num]

    @staticmethod
    def _phase_referenced_deps(phase: dict) -> set:
        """Return the set of phase numbers that this phase directly depends on.

        Scans every field where the planner is allowed to embed phase references:
          - arguments   — recursive scan (covers source_data, data, data_to_filter, etc.)
          - goal        — natural-language description sometimes contains 'result_of_phase_N'
          - loop_over   — source key for loop iteration phases
          - conditions  — any conditional logic the LLM might add (defensive)
          - filter      — any filter field the LLM might add (defensive)

        Pattern matches:
          result_of_phase_N  — canonical inter-phase reference
          phase_N            — abbreviated form sometimes emitted by weaker models

        A false positive (unrelated string matching "phase_3") is safe — it just
        forces sequential execution, which is conservative.  A false negative
        (missed dependency) would allow two dependent phases to run concurrently,
        potentially reading stale workflow_state.  The scan is designed to be
        conservative (over-detect) rather than liberal.
        """
        _PAT = re.compile(r'result_of_phase_(\d+)|phase_(\d+)')

        def _scan(obj):
            nums = set()
            if isinstance(obj, str):
                for m in _PAT.finditer(obj):
                    nums.add(int(m.group(1) or m.group(2)))
            elif isinstance(obj, dict):
                for v in obj.values():
                    nums |= _scan(v)
            elif isinstance(obj, list):
                for item in obj:
                    nums |= _scan(item)
            return nums

        refs = set()
        refs |= _scan(phase.get('loop_over', ''))
        refs |= _scan(phase.get('arguments', {}))
        refs |= _scan(phase.get('goal', ''))
        refs |= _scan(phase.get('conditions', ''))   # defensive: non-standard LLM field
        refs |= _scan(phase.get('filter', ''))       # defensive: non-standard LLM field
        return refs

    @classmethod
    def _phase_implicit_deps(cls, phase: dict, all_phases: list) -> set:
        """Return phase numbers this phase implicitly depends on, based on tool semantics.

        Belt-and-suspenders complement to _phase_referenced_deps.  Where
        _phase_referenced_deps scans string values for 'result_of_phase_N',
        this method encodes the *semantic* knowledge that visualization tools always
        consume the output of preceding data-gathering phases — regardless of whether
        a result_of_phase_N placeholder is present in the arguments.

        This prevents race conditions when a rewrite pass strips the placeholder or
        the LLM never emits it (e.g. LLM omits `content` on TDA_Dashboard visualize,
        or `data` is stripped from TDA_Charting by the charting-cleanup pass).

        A false positive (over-detecting a dependency) is safe — it only forces
        sequential execution.  A false negative (missed dependency) would let the
        visualization phase race ahead of its data phase, resulting in an empty
        workflow_state and a silent 'No data to visualize' failure.
        """
        tools = set(phase.get('relevant_tools') or [])
        if not (tools & _PC_VISUALIZATION_TOOLS):
            return set()

        # TDA_Dashboard: only action=visualize consumes data from prior phases.
        # Other actions (create, add_tile, refresh) are orchestration calls that
        # do not read phase data and may run in parallel safely.
        if 'TDA_Dashboard' in tools and 'TDA_Charting' not in tools:
            action = (phase.get('arguments') or {}).get('action', '')
            if action != 'visualize':
                return set()

        phase_num = phase.get('phase', 0)
        implicit: set = set()
        for other in all_phases:
            other_num = other.get('phase', 0)
            if other_num >= phase_num:
                continue
            other_tools = set(other.get('relevant_tools') or [])
            # A phase is data-gathering if at least one of its tools is NOT in
            # UTILITY_TOOLS (i.e. it actually fetches something from the outside).
            if other_tools and not (other_tools <= _PC_UTILITY_TOOLS):
                implicit.add(other_num)
        return implicit

    @classmethod
    def _phase_discovery_deps(cls, phase: dict, all_phases: list) -> set:
        """Return phase numbers this phase implicitly depends on because it is a data-EXECUTION
        phase (raw SQL) that follows a schema-DISCOVERY phase.

        Third complement to _phase_referenced_deps (explicit result_of_phase_N strings) and
        _phase_implicit_deps (visualization→data). It encodes the semantic knowledge that a SQL
        query consumes the database/table/column a preceding discovery phase identified —
        regardless of whether the plan carries a result_of_phase_N placeholder.

        This closes the exact turn-6 failure: a weak planner emitted phase 1 = 'query the KG to
        identify the table' and phase 2 = 'SELECT * FROM airport.station' with a hardcoded
        (hallucinated) name and NO reference, so the scheduler saw them as independent and ran
        them concurrently — the query could never consume the discovery.

        Over-detection is safe (per the module's stated philosophy — a false positive only
        forces sequential execution); a false negative races the query ahead of its discovery.
        """
        tools = {base_name(t) for t in (phase.get('relevant_tools') or [])}
        args = phase.get('arguments') or {}
        is_execution = ('base_readQuery' in tools) or (
            isinstance(args, dict) and bool(str(args.get('sql') or '').strip()))
        # A describe the schema_before_sql pass inserted settles its table on the listing an
        # earlier discovery phase returns — so it waits for that discovery like a query does.
        if phase.get('_inserted_by') == 'schema_before_sql':
            is_execution = True
        if not is_execution:
            return set()

        phase_num = phase.get('phase', 0)
        deps: set = set()
        for other in all_phases:
            other_num = other.get('phase', 0)
            if other_num >= phase_num:
                continue
            other_tools = {base_name(t) for t in (other.get('relevant_tools') or [])}
            if other_tools & _PC_DISCOVERY_TOOLS:
                deps.add(other_num)
            # Temporal CONTEXT is consumed the same way: a raw-SQL phase after
            # TDA_CurrentDate / TDA_DateRange reads their dates (bind wiring, embedded
            # references) even when it carries no result_of_phase_N. Racing it ahead
            # of them leaves the dates unresolved — observed live (session 96998015:
            # phases [2, 3] concurrent, `:start_date` never wired). Instant tools, so
            # serialising costs nothing.
            if other_tools & cls._TEMPORAL_CONTEXT_TOOLS:
                deps.add(other_num)
        return deps

    _TEMPORAL_CONTEXT_TOOLS = frozenset({"TDA_CurrentDate", "TDA_DateRange"})

    @classmethod
    def _phase_compute_deps(cls, phase: dict, all_phases: list) -> set:
        """Fourth complement (S3): a `TDA_Compute` phase reads the turn's RESULT-SET REGISTRY —
        the rows every earlier data phase returned — so it depends on every earlier data
        phase even though it carries no `result_of_phase_N` (it addresses rows by statement
        number, not by placeholder). Without this the scheduler ran the compute phase
        concurrently with the query it was meant to compute over (found live: "3 phases → 2
        groups", 0 receipts, the report did the arithmetic itself)."""
        if "TDA_Compute" not in set(phase.get('relevant_tools') or []):
            return set()
        phase_num = phase.get('phase', 0)
        deps: set = set()
        for other in all_phases:
            other_num = other.get('phase', 0)
            if other_num >= phase_num:
                continue
            other_tools = set(other.get('relevant_tools') or [])
            if other_tools and not all(str(t).startswith("TDA_") for t in other_tools):
                deps.add(other_num)
            # …and on every earlier COMPUTE phase: a combine's result is the statement the next
            # compute reads ("the latest"). Found live (multi-source 3b): a `sum` scheduled beside
            # the combine it was meant to total read the fetch that arrived before it.
            elif "TDA_Compute" in other_tools:
                deps.add(other_num)
        return deps

    # Canonical terminal-tools set — sourced from plan_compiler (single source of truth).
    # Terminal phases run alone (never in a parallel group) even without explicit
    # result_of_phase_N references — they synthesise all prior data implicitly.
    # Non-terminal tools (TDA_Charting, TDA_Dashboard, TDA_LLMFilter, etc.) are kept
    # sequential by _phase_referenced_deps + _phase_implicit_deps combined.
    _TERMINAL_TOOLS = _PC_TERMINAL_TOOLS

    @classmethod
    def _is_terminal_phase(cls, phase: dict) -> bool:
        """Return True if this phase must run alone — not in a parallel group.

        Three independent terminal conditions:
          1. Loop phases  — internally serial; parallel dispatch would break
             loop-item iteration and shared loop-state tracking.
          2. Executable-prompt phases  — delegated to a sub-executor; they must
             run in the sequential path (handled in _run_plan, not _stream_parallel_phases).
             This check is belt-and-suspenders: _build_phase_groups already isolates them.
          3. Terminal tools  — synthesis/reporting tools that depend on ALL prior
             data-gathering phases implicitly (even without explicit result_of_phase_N
             references in the plan).
        """
        if phase.get('type') == 'loop':
            return True
        if 'executable_prompt' in phase:
            return True
        tools = set(phase.get('relevant_tools') or [])
        return bool(tools & cls._TERMINAL_TOOLS)

    @classmethod
    def _build_phase_groups(cls, meta_plan: list) -> list:
        """
        Return a list of groups where each group is a list of phases.
        Groups with >1 phase can be run in parallel — their phases have
        no cross-dependencies and none is terminal.
        """
        groups = []
        current_group: list = []
        current_nums: set = set()

        for phase in meta_plan:
            phase_num = phase.get('phase', 0)

            if cls._is_terminal_phase(phase):
                if current_group:
                    groups.append(current_group)
                    current_group = []
                    current_nums = set()
                groups.append([phase])
                continue

            # Combine string-reference scan with semantic (tool-type) implicit deps.
            # Either source alone can be fooled; together they are robust:
            #   _phase_referenced_deps catches explicit result_of_phase_N strings.
            #   _phase_implicit_deps catches visualization tools even when the
            #   placeholder string is absent or was stripped by a rewrite pass.
            deps = (cls._phase_referenced_deps(phase)
                    | cls._phase_implicit_deps(phase, meta_plan)
                    | cls._phase_discovery_deps(phase, meta_plan)
                    | cls._phase_compute_deps(phase, meta_plan))
            if deps & current_nums:
                # This phase depends on something already in the current group —
                # flush and start a fresh group.
                groups.append(current_group)
                current_group = [phase]
                current_nums = {phase_num}
            else:
                current_group.append(phase)
                current_nums.add(phase_num)

        if current_group:
            groups.append(current_group)

        return groups

    async def _stream_parallel_phases(self, phase_executor, phases: list):
        """
        Execute a list of independent phases concurrently, collect all events per
        phase, then yield them phase-by-phase.

        The collect-then-yield approach ensures the ui.js phase container stack
        sees a clean sequential lifecycle (phase_start … phase_end) for each phase,
        even though the underlying execution was parallel. Interleaving events from
        multiple phases would otherwise confuse the stack and lose "Phase N Completed"
        footers.

        turn_action_history entries produced during the parallel group are also
        post-sorted by phase_num so that historical replay renders in the correct order.
        """
        phase_nums = [p.get('phase', '?') for p in phases]
        n = len(phases)

        # Emit a system note so the Live Status panel shows parallel execution
        parallel_note = {
            "step": f"Parallel Execution: phases {phase_nums}",
            "type": "system_message",
            "details": {
                "summary": f"Executing {n} independent phases concurrently: {phase_nums}",
                "parallel_phases": phase_nums,
            },
        }
        self._log_system_event(parallel_note)
        yield self._format_sse_with_depth(parallel_note)

        # Mark where parallel phase entries begin in turn_action_history
        history_start = len(self.turn_action_history)

        async def _collect(phase):
            """Run one phase generator and collect all SSE events into a list.

            Returns ``{'events': [...], 'error': exc | None}``. C9: a phase exception is
            captured (with a visible error event emitted for the UI) AND surfaced as
            ``error`` so the caller can HALT the turn — matching the sequential path, where a
            phase exception (notably a turn-halting ``DefinitiveToolError``) propagates up and
            fails the turn. The previous code swallowed every exception into an event and let
            the group + downstream groups continue, so a blocked/failed parallel phase produced
            a ``status:"success"`` turn eligible for RAG champion seeding. ``CancelledError``
            (BaseException) is NOT caught here — it propagates to ``gather`` and is handled below.
            """
            events = []
            pnum = phase.get('phase', '?')
            captured_error = None
            try:
                is_delegated = ('executable_prompt' in phase
                                and self.execution_depth < self.MAX_EXECUTION_DEPTH)
                if is_delegated:
                    gen = self._run_sub_prompt(
                        phase.get('executable_prompt'), phase.get('arguments', {}))
                else:
                    gen = phase_executor.execute_phase(phase)
                async for event in gen:
                    events.append(event)
            except Exception as exc:
                app_logger.error(f"Parallel phase error (phase {pnum}): {exc}", exc_info=True)
                captured_error = exc  # C9: surface it so the turn halts (sequential parity)
                # Emit a visible error event so the Live Status panel shows the failure.
                err_event = {
                    "step": f"Phase {pnum} Failed",
                    "type": "error",
                    "details": {
                        "phase_num": pnum,
                        "error": str(exc),
                        "summary": f"Phase {pnum} raised an unexpected exception during parallel execution.",
                    },
                }
                self._log_system_event(err_event)
                events.append(self._format_sse_with_depth(err_event))
            return {'events': events, 'error': captured_error}

        # Run all phases concurrently; each returns {'events', 'error'}.
        per_phase_results = await asyncio.gather(
            *[_collect(p) for p in phases], return_exceptions=True)

        # Yield events phase-by-phase so the UI sees clean sequential lifecycles, and remember
        # the FIRST terminal failure so the turn halts after the group's events are surfaced.
        # C9: check BaseException (not just Exception) so a CancelledError from gather — which
        # `isinstance(x, Exception)` misses — is caught and re-raised as real cancellation
        # instead of being silently dropped and letting the turn persist as success.
        terminal_exc = None
        for res in per_phase_results:
            if isinstance(res, BaseException):
                # A phase raised outside _collect's Exception catch (e.g. CancelledError).
                if terminal_exc is None:
                    terminal_exc = res
                continue
            for event in res['events']:
                yield event
            if res.get('error') is not None and terminal_exc is None:
                terminal_exc = res['error']

        # Post-sort turn_action_history entries by phase_num for correct historical replay
        def _entry_phase_num(entry):
            action = entry.get('action', {})
            if not isinstance(action, dict):
                return float('inf')
            # Tool call entries carry metadata.phase_number
            pn = action.get('metadata', {}).get('phase_number')
            if pn is not None:
                return int(pn)
            # TDA_SystemLog entries carry details.phase_num
            if action.get('tool_name') == 'TDA_SystemLog':
                details = action.get('arguments', {}).get('details', {})
                if isinstance(details, dict):
                    pn = details.get('phase_num')
                    if pn is not None:
                        return int(pn)
            return float('inf')

        parallel_slice = self.turn_action_history[history_start:]
        parallel_slice.sort(key=_entry_phase_num)  # stable sort preserves intra-phase order
        self.turn_action_history[history_start:] = parallel_slice

        # Release per-phase state for this group (prevents unbounded memory growth
        # across long multi-turn conversations with many parallel groups).
        for p in phases:
            self._phase_parallel_state.pop(p.get('phase', -1), None)

        # C9: after surfacing the group's events + cleaning up, halt the turn if any phase
        # failed. This matches the sequential path (a phase exception propagates and fails the
        # turn) and stops _run_plan from dispatching dependent downstream groups with a missing
        # result — so a blocked/failed parallel turn persists as failed, not "success".
        if terminal_exc is not None:
            raise terminal_exc

    # --- End phase parallelization helpers ---

    async def _run_plan(self):
        """Executes the generated meta-plan, delegating to the PhaseExecutor."""
        if not self.meta_plan:
            raise RuntimeError("Cannot execute plan: meta_plan is not generated.")

        phase_executor = PhaseExecutor(self) # Pass self (PlanExecutor instance)

        # Skip final summary check (remains unchanged)
        if not APP_CONFIG.SUB_PROMPT_FORCE_SUMMARY and self.execution_depth > 0 and len(self.meta_plan) > 1:
            last_phase = self.meta_plan[-1]
            last_phase_tools = last_phase.get('relevant_tools', [])
            is_final_report_phase = any(tool in ["TDA_FinalReport", "TDA_ComplexPromptReport"] for tool in last_phase_tools)

            if is_final_report_phase:
                app_logger.info(f"Sub-process (depth {self.execution_depth}) is skipping its final summary phase.")
                event_data = {
                    "step": "Plan Optimization", "type": "plan_optimization",
                    "details": "Sub-process is skipping its final summary task to prevent redundant work. The main process will generate the final report."
                }
                self._log_system_event(event_data)
                yield self._format_sse_with_depth(event_data)
                self.meta_plan = self.meta_plan[:-1]


        # Build phase groups — consecutive independent phases share a group and
        # are run concurrently; terminal/loop/synthesis phases run alone.
        phase_groups = self._build_phase_groups(self.meta_plan)
        has_parallel = any(len(g) > 1 for g in phase_groups)
        if has_parallel:
            n_parallel = sum(len(g) for g in phase_groups if len(g) > 1)
            app_logger.info(
                f"Phase parallelization: {len(self.meta_plan)} phases → {len(phase_groups)} groups "
                f"({n_parallel} phases eligible for parallel execution)"
            )

        for group in phase_groups:
            # Check for cancellation before each group
            self._check_cancellation()

            # PROP-011 — the plan ENDS at a question. Once a step raised one, no later phase is
            # attempted (the chokepoint refusal is the backstop, not the mechanism: a refused
            # tool result here is what fires the self-correction strategies, phase after phase);
            # the summarization still runs so the final report carries the question.
            try:
                from trusted_data_agent.agent import input_requests as _ir_mod
                _ir_pending = _ir_mod.pending()
            except Exception:  # noqa: BLE001
                _ir_pending = None
            if _ir_pending is not None:
                remaining = len(self.meta_plan) - self.current_phase_index
                if remaining > 0:
                    _ev = {"step": "Plan Ended at a Question", "type": "workaround",
                           "details": (f"A question for the user is pending ({_ir_pending['id']}); "
                                       f"{remaining} remaining phase(s) are not attempted until it is answered.")}
                    self._log_system_event(_ev)
                    yield self._format_sse_with_depth(_ev)
                break

            # S5 The Visible Budget — once a unit is exhausted, the data phases the budget cannot
            # afford are SKIPPED (recorded, never silently) and the synthesis phase still runs
            # with what was collected and the finish instruction: a graceful finish, not a kill.
            _bskip = self._budget_skip(group)
            if _bskip is not None:
                _orig_len = len(group)
                group, _bev = _bskip
                if _bev is not None:
                    yield self._format_sse_with_depth(_bev)
                if not group:
                    self.current_phase_index += _orig_len
                    continue

            if len(group) > 1:
                # Parallel group — stream events from all phases concurrently
                async for event in self._stream_parallel_phases(phase_executor, group):
                    yield event
                self.current_phase_index += len(group)
                continue

            # Single-phase group — existing sequential path (unchanged behaviour)
            current_phase = group[0]
            is_delegated_prompt_phase = 'executable_prompt' in current_phase and self.execution_depth < self.MAX_EXECUTION_DEPTH

            # --- MODIFICATION START: Add replay status prefix ---
            replay_prefix = ""
            if self.is_replay:
                replay_type_text = "Optimized" if "optimized" in str(self.is_replay).lower() else "Original"
                # Find original turn ID (similar logic as in run method)
                original_turn_id = "..."
                session_data = await session_manager.get_session(self.user_uuid, self.session_id)
                if session_data and isinstance(session_data.get("last_turn_data", {}).get("workflow_history"), list):
                    for idx, turn in enumerate(session_data["last_turn_data"]["workflow_history"]):
                        if turn.get("original_plan") == self.plan_to_execute: # Compare against the plan being replayed
                            original_turn_id = str(idx + 1)
                            break
                replay_prefix = f"🔄 Replay ({replay_type_text} from Turn {original_turn_id}): "
            # --- MODIFICATION END ---

            if is_delegated_prompt_phase:
                prompt_name = current_phase.get('executable_prompt')
                prompt_args = current_phase.get('arguments', {})

                # Safeguard: Skip if prompt_name is None or empty (shouldn't happen after planner cleanup, but defensive)
                if not prompt_name or prompt_name in ['None', 'null', '']:
                    app_logger.warning(f"Skipping delegated prompt phase with invalid prompt_name: '{prompt_name}'. Phase: {current_phase}")
                    error_event = {
                        "step": "Plan Optimization",
                        "details": f"Skipping invalid prompt execution step. The plan phase contained an unusable prompt reference.",
                        "type": "workaround"
                    }
                    self._log_system_event(error_event)
                    yield self._format_sse_with_depth(error_event)
                    self.current_phase_index += 1
                    continue

                # A prompt the agent has switched off is never delegated to — a replayed champion
                # plan or a model-authored phase can name one; skip it and say so.
                from trusted_data_agent.core.profile_selection import disabled_prompt_refusal
                _refusal = disabled_prompt_refusal(getattr(self, "active_profile_id", None), self.user_uuid, prompt_name)
                if _refusal:
                    app_logger.info(f"Skipping delegated prompt phase: {_refusal}")
                    skip_event = {"step": "Plan Optimization", "details": f"Skipped a plan step: {_refusal}",
                                  "type": "workaround"}
                    self._log_system_event(skip_event)
                    yield self._format_sse_with_depth(skip_event)
                    self.current_phase_index += 1
                    continue

                # Send phase_start event for delegated prompt phases
                phase_num = current_phase.get("phase", self.current_phase_index + 1)
                phase_goal = current_phase.get("goal", "No goal defined.")
                event_data = {
                    "step": f"Starting Plan Phase {phase_num}/{len(self.meta_plan)}",
                    "type": "phase_start",
                    "details": {
                        "phase_num": phase_num,
                        "total_phases": len(self.meta_plan),
                        "goal": phase_goal,
                        "phase_details": current_phase,
                        "execution_depth": self.execution_depth
                    }
                }
                self._log_system_event(event_data)
                yield self._format_sse_with_depth(event_data)

                async for event in self._run_sub_prompt(prompt_name, prompt_args):
                    yield event

                # Send phase_end event after sub-prompt completes
                event_data = {
                    "step": f"Ending Plan Phase {phase_num}/{len(self.meta_plan)}",
                    "type": "phase_end",
                    "details": {"phase_num": phase_num, "total_phases": len(self.meta_plan), "status": "completed", "execution_depth": self.execution_depth}
                }
                self._log_system_event(event_data)
                yield self._format_sse_with_depth(event_data)
            else:
                # --- MODIFICATION START: Pass replay prefix conceptually ---
                # PhaseExecutor needs modification to accept and use this prefix
                # For now, just logging it here. Actual prefixing requires PhaseExecutor changes.
                if replay_prefix:
                    app_logger.debug(f"Passing replay prefix to PhaseExecutor: '{replay_prefix}'")
                async for event in phase_executor.execute_phase(current_phase): # Assuming execute_phase will handle the prefix internally
                    yield event
                # --- MODIFICATION END ---

            self.current_phase_index += 1

        app_logger.debug("Meta-plan has been fully executed. Transitioning to summarization.")
        self.state = self.AgentState.SUMMARIZING

    async def _run_sub_prompt(self, prompt_name: str, prompt_args: dict, is_delegated_task: bool = False):
        """
        Creates and runs a sub-executor for a delegated prompt, adopting its
        final state upon completion to ensure a continuous and complete workflow.
        """
        # Safety check: Don't execute if prompt_name is invalid (final defensive layer)
        if not prompt_name or prompt_name in ['None', 'null', '']:
            error_event = {
                "step": "Plan Optimization",
                "details": f"Skipping execution of invalid prompt reference. The system prevented an error.",
                "type": "workaround"
            }
            self._log_system_event(error_event)
            yield self._format_sse_with_depth(error_event)
            app_logger.error(f"Attempted to run sub-prompt with invalid name: '{prompt_name}'")
            return

        # Cycle detection: abort if this prompt is already in the current call chain
        if prompt_name in self._visited_prompts:
            cycle_path = " → ".join(sorted(self._visited_prompts)) + f" → {prompt_name}"
            app_logger.error(f"Prompt cycle detected and blocked: {cycle_path}")
            cycle_event = {
                "step": "System Correction",
                "details": f"Circular prompt execution blocked: '{prompt_name}' is already in the current call chain ({cycle_path}). Skipping to prevent infinite recursion.",
                "type": "workaround"
            }
            self._log_system_event(cycle_event)
            yield self._format_sse_with_depth(cycle_event)
            return
        
        event_data = {
            "step": "Prompt Execution Granted",
            "details": f"Executing prompt '{prompt_name}' as part of the plan.",
            "type": "workaround"
        }
        self._log_system_event(event_data)
        yield self._format_sse_with_depth(event_data)

        force_disable_sub_history = is_delegated_task
        if force_disable_sub_history:
            app_logger.info(f"Token Optimization: Disabling history for delegated recovery task '{prompt_name}'.")

        sub_executor = PlanExecutor(
            session_id=self.session_id,
            user_uuid=self.user_uuid,
            original_user_input=f"Executing prompt: {prompt_name}",
            dependencies=self.dependencies,
            active_prompt_name=prompt_name,
            prompt_arguments=prompt_args,
            execution_depth=self.execution_depth + 1,
            disabled_history=self.disabled_history or force_disable_sub_history,
            previous_turn_data=self.previous_turn_data,
            source="prompt_library",
            is_delegated_task=is_delegated_task,
            force_final_summary=APP_CONFIG.SUB_PROMPT_FORCE_SUMMARY,
            event_handler=self.event_handler,
            profile_override_id=self.profile_override_id,
            _visited_prompts=self._visited_prompts | {prompt_name},
        )

        sub_executor.workflow_state = self.workflow_state
        sub_executor.structured_collected_data = self.structured_collected_data

        # Inherit parent's turn token counts so nested execution accumulates correctly (its
        # budget and live token_update read the turn's running total)…
        sub_executor.turn_input_tokens = self.turn_input_tokens
        sub_executor.turn_output_tokens = self.turn_output_tokens
        sub_executor.turn_cost_usd = self.turn_cost_usd  # nested per-call costs accumulate on top
        # …and remember where it started, so only its OWN usage is merged back (below)
        _sub_base = self._sub_totals_base(sub_executor)



        async for event in sub_executor.run():
            yield event

        self.structured_collected_data = sub_executor.structured_collected_data
        self.workflow_state = sub_executor.workflow_state

        # --- MODIFICATION START: Append sub-trace, don't overwrite ---
        if sub_executor.turn_action_history:
            self.turn_action_history.extend(sub_executor.turn_action_history)
        # --- MODIFICATION END ---

        # Merge sub-executor's lifecycle events for reload persistence
        if hasattr(sub_executor, 'tool_enabled_events') and hasattr(self, 'tool_enabled_events'):
            self.tool_enabled_events.extend(sub_executor.tool_enabled_events)

        self.last_tool_output = sub_executor.last_tool_output

        # MERGE the sub-executor's own usage into the turn — never assign its totals back: a
        # sibling phase running in parallel accumulates on THIS executor while the sub runs, and
        # an assignment overwrote that call (Optimize campaign run 4, invariant O6: the turn's
        # recorded price missed one priced call on two turns; test_sub_prompt_totals.py).
        self._merge_sub_totals(sub_executor, _sub_base)
        


        if sub_executor.state == self.AgentState.ERROR:
            app_logger.error(f"Sub-executor for prompt '{prompt_name}' failed.")
            # A tool output may be a LIST (a row-set result): `.get` on it turned a recoverable
            # sub-prompt failure into a fatal error for the whole turn (trust campaign, P2).
            if not isinstance(self.last_tool_output, dict) or self.last_tool_output.get("status") != "error":
                self.last_tool_output = {"status": "error", "error_message": f"Sub-prompt '{prompt_name}' failed."}
        else:
             if self.last_tool_output is None:
                self.last_tool_output = {"status": "success"}

    async def _run_delegated_prompt(self):
        """
        Executes a single, delegated prompt by immediately expanding it into a
        concrete plan. This is used for sub-executors created during
        self-correction to avoid redundant planning and recursion.
        """
        if not self.active_prompt_name:
            app_logger.error("Delegated task started without an active_prompt_name.")
            self.state = self.AgentState.ERROR
            return

        # --- MODIFICATION START: Pass RAG retriever instance to Planner ---
        planner = Planner(self, rag_retriever_instance=self.rag_retriever)
        # --- MODIFICATION END ---
        app_logger.info(f"Delegated task: Directly expanding prompt '{self.active_prompt_name}' into a concrete plan.")

        async for event in planner.generate_and_refine_plan():
            yield event
        # --- MODIFICATION START: Store plan for history even in delegated ---
        # Ensure the plan generated for the delegated task is also stored
        self.original_plan_for_history = copy.deepcopy(self.meta_plan)
        app_logger.info("Stored delegated prompt plan for history.")
        # --- MODIFICATION END ---


        self.state = self.AgentState.EXECUTING
        async for event in self._run_plan():
            yield event

    async def _handle_summarization(self, final_answer_override: str | None):
        """Orchestrates the final summarization and answer formatting."""
        final_content = None

        # Summarization logic remains largely the same
        if self.is_synthesis_from_history:
            app_logger.info("Bypassing summarization. Using direct synthesized answer from planner.")
            synthesized_answer = "Could not extract synthesized answer."
            if self.last_tool_output and isinstance(self.last_tool_output.get("results"), list) and self.last_tool_output["results"]:
                synthesized_answer = self.last_tool_output["results"][0].get("response", synthesized_answer)
            final_content = CanonicalResponse(direct_answer=synthesized_answer)
        elif self.execution_depth > 0 and not self.force_final_summary:
            app_logger.info(f"Sub-planner (depth {self.execution_depth}) completed. Bypassing final summary.")
            self.state = self.AgentState.DONE

            # Emit execution_complete lifecycle event for sub-executor
            try:
                duration_ms = int((time.time() - self.tool_enabled_start_time) * 1000) if hasattr(self, 'tool_enabled_start_time') else 0
                # §3.3: turn cost = Σ per-call costs (dual-model aware) for the completion card.
                _turn_cost = self._resolve_turn_cost_usd()
                complete_payload = {
                    "profile_type": "tool_enabled",
                    "profile_tag": self._get_current_profile_tag(),
                    "phases_executed": len([a for a in self.turn_action_history if isinstance(a.get("action"), dict) and a["action"].get("tool_name") != "TDA_SystemLog"]),
                    "total_input_tokens": self.turn_input_tokens,
                    "total_output_tokens": self.turn_output_tokens,
                    "duration_ms": duration_ms,
                    "cost_usd": _turn_cost,
                    "success": True
                }

                # Store in tool_enabled_events BEFORE yield (yield suspends execution;
                # storing first guarantees persistence even if generator is not fully consumed)
                if hasattr(self, 'tool_enabled_events'):
                    self.tool_enabled_events.append({
                        "type": "execution_complete",
                        "payload": complete_payload,
                        "metadata": {"execution_depth": self.execution_depth}
                    })

                yield self._emit_lifecycle_event("execution_complete", complete_payload)
                app_logger.info(f"✅ Emitted execution_complete for sub-executor (depth={self.execution_depth})")
            except Exception as e:
                app_logger.warning(f"Failed to emit sub-executor execution_complete: {e}")

            return
        elif final_answer_override:
            final_content = CanonicalResponse(direct_answer=final_answer_override)
        elif self.is_conversational_plan:
            response_text = self.temp_data_holder or "I'm sorry, I don't have a response for that."
            final_content = CanonicalResponse(direct_answer=response_text)
        elif self.last_tool_output and self.last_tool_output.get("status") == "success":
            results = self.last_tool_output.get("results", [{}])
            if not results:
                final_content = CanonicalResponse(direct_answer="The agent has completed its work, but the final step produced no data.")
            else:
                last_result = results[0]
                tool_name = self.last_tool_output.get("metadata", {}).get("tool_name")

                if self.active_prompt_name and tool_name == "TDA_ComplexPromptReport":
                    final_content = PromptReportResponse.model_validate(last_result)
                elif tool_name == "TDA_FinalReport":
                    final_content = CanonicalResponse.model_validate(last_result)
                else:
                    final_content = CanonicalResponse(direct_answer="The agent has completed its work, but a final report was not generated.")
        else:
            final_content = CanonicalResponse(direct_answer="The agent has completed its work, but an issue occurred in the final step.")

        if final_content:
            async for event in self._format_and_yield_final_answer(final_content):
                yield event
            self.state = self.AgentState.DONE

    def _build_tool_context_summary(self) -> str | None:
        """
        Build a compact summary of MCP tool calls from this turn for cross-turn LLM context.

        Stored in chat_object so conversation_history carries tool awareness across turns
        without re-running queries. Only data-returning tools are included — TDA_ internal
        tools are skipped as they carry no cross-turn value.
        """
        _TDA_PREFIX = "TDA_"
        lines = []

        for entry in self.turn_action_history:
            action = entry.get("action", {})
            result = entry.get("result", {})
            if not isinstance(action, dict):
                continue
            tool_name = action.get("tool_name", "")

            # Skip internal orchestration tools
            if tool_name.startswith(_TDA_PREFIX):
                # Date range orchestrator: summarise using its consolidated result
                args = action.get("arguments", {})
                if args.get("orchestration_type") == "date_range_complete":
                    target = args.get("target_tool", tool_name)
                    n_iter = args.get("num_iterations", "?")
                    lines.append(self._format_tool_summary_line(
                        f"{target} (×{n_iter} dates)", action, result
                    ))
                continue

            if result.get("status") == "error":
                continue

            lines.append(self._format_tool_summary_line(tool_name, action, result))

        if not lines:
            return None
        return "[Tool Execution Summary]\n" + "\n".join(lines)

    def _format_tool_summary_line(self, tool_name: str, action: dict, result: dict) -> str:
        """Format a single tool call as a compact summary line."""
        args = action.get("arguments", {})
        meta = result.get("metadata", {})
        results_list = result.get("results", [])

        # Primary argument: prefer sql/query fields, then first string value
        primary_arg = ""
        for key in ("sql", "query", "statement"):
            if key in args and isinstance(args[key], str):
                val = args[key].replace("\n", " ").strip()
                primary_arg = val[:120] + "..." if len(val) > 120 else val
                break
        if not primary_arg:
            for val in args.values():
                if isinstance(val, str) and val.strip():
                    primary_arg = val[:80] + "..." if len(val) > 80 else val
                    break

        # Row count and columns — prefer distiller metadata, fall back to inline
        row_count = meta.get("row_count")
        columns = meta.get("columns")
        if row_count is None and results_list:
            row_count = len(results_list)
        if columns is None and results_list and isinstance(results_list[0], dict):
            columns = list(results_list[0].keys())
        # Normalize: distiller may store columns as [{name, type}, ...] — extract names only
        if columns and isinstance(columns[0], dict):
            columns = [c.get("name", str(c)) for c in columns]

        # One sample row (first row, trimmed)
        sample = ""
        if results_list and isinstance(results_list[0], dict):
            row = results_list[0]
            trimmed = {k: (str(v)[:60] if len(str(v)) > 60 else v)
                       for k, v in list(row.items())[:5]}
            sample = f", sample: {trimmed}"

        row_info = f"→ {row_count} row{'s' if row_count != 1 else ''}" if row_count is not None else ""
        col_info = f", cols: {columns}" if columns else ""
        arg_info = f": {primary_arg}" if primary_arg else ""

        return f"• {tool_name}{arg_info} {row_info}{col_info}{sample}".strip()

    async def _format_and_yield_final_answer(self, final_content: CanonicalResponse | PromptReportResponse):
        """
        Formats a raw summary string OR a CanonicalResponse object and yields
        the final SSE event to the UI. Also saves the final HTML to session history.
        Includes the turn number in the final event payload.
        """
        formatter_kwargs = {
            "collected_data": self.structured_collected_data,
            "original_user_input": self.original_user_input,
            "active_prompt_name": self.active_prompt_name
        }
        if isinstance(final_content, PromptReportResponse):
            formatter_kwargs["prompt_report_response"] = final_content
        else:
            formatter_kwargs["canonical_response"] = final_content

        formatter = OutputFormatter(**formatter_kwargs)
        final_html, tts_payload = formatter.render()

        # --- MODIFICATION START: Extract component payloads from execution trace ---
        # In tool_enabled profiles, component tool results (canvas, chart, etc.) are
        # stored in turn_action_history with format {"type": "<component_id>", "spec": {...}}.
        # We must extract these and generate the data-component-id divs so the frontend
        # renderer can pick them up — matching what llm_only and rag_focused paths do.
        try:
            component_payloads = []
            for entry in self.turn_action_history:
                result = entry.get("result") if isinstance(entry, dict) else None
                if isinstance(result, dict) and result.get("spec") and result.get("type"):
                    if result.get("type") == "chart":
                        continue  # Formatter handles charts via structured_collected_data with paired table details
                    component_payloads.append({
                        "component_id": result["type"],
                        "spec": result["spec"],
                        "html": result.get("html") or "",
                        "title": result.get("title") or "",
                        "render_target": result.get("render_target"),
                        "metadata": result.get("metadata") or {},
                    })
            if component_payloads:
                from trusted_data_agent.components.utils import generate_component_html
                final_html += generate_component_html(component_payloads)
        except Exception as comp_extract_err:
            app_logger.warning(f"Failed to extract component payloads from execution trace: {comp_extract_err}")
        # --- MODIFICATION END ---

        # --- MODIFICATION START: Decouple UI and LLM history ---
        # First, determine the clean text summary for the LLM
        clean_summary_for_llm = "The agent has completed its work."
        if hasattr(final_content, 'direct_answer'):
            clean_summary_for_llm = final_content.direct_answer
        elif hasattr(final_content, 'executive_summary'):
            clean_summary_for_llm = final_content.executive_summary
        
        # Store this clean summary in self.final_summary_text *before* saving
        self.final_summary_text = clean_summary_for_llm

        # Tier 1.1 grounding gate (charter #14: all engines). Runs before the histories
        # save + final_answer yield so any annotation/withholding flows into the stored
        # chat_object, the displayed HTML, and the LLM-facing text in one place. Default
        # observe → byte-identical (charter #15). Stamped for reload (#17).
        _gated_text, self._grounding_event = await self._run_grounding_gate_full(
            self.final_summary_text,
            trace_extra={"execution_trace": self.turn_action_history})
        if (self._grounding_event or {}).get("decision"):
            self.final_summary_text = _gated_text
            from trusted_data_agent.agent.grounding import adapter as _grounding_adapter
            if (self._grounding_event or {}).get("outcome_delivered") == "revision":
                # S7 review: the outcome loop delivered a REVISION — the report's answer field is
                # the delivered text now, so the HTML is rebuilt from it (the draft's HTML would
                # have shown the reader the very statement the revision removed)
                try:
                    if hasattr(final_content, "direct_answer"):
                        final_content.direct_answer = _gated_text
                    elif hasattr(final_content, "executive_summary"):
                        final_content.executive_summary = _gated_text
                    final_html, tts_payload = OutputFormatter(**formatter_kwargs).render()
                except Exception as _rr:  # noqa: BLE001
                    app_logger.debug(f"[outcome] report re-render skipped: {_rr}")
            final_html = _grounding_adapter.apply_to_html(final_html, self._grounding_event)

        # Tier 1.3 outbound guardrail (charter #14: all engines). Runs after grounding, before
        # the save + final_answer yield, so a PII redaction / toxicity withhold flows into the
        # stored chat_object, the displayed HTML, and the LLM-facing text in one place.
        # Deterministic, 0-token. Default observe → byte-identical (#15). Stamped for reload (#17).
        _g_text, self._guardrail_event = self._run_guardrail_outbound(self.final_summary_text)
        if (self._guardrail_event or {}).get("acted"):
            self.final_summary_text = _g_text
            from trusted_data_agent.agent.guardrails import adapter as _guardrail_adapter
            final_html = _guardrail_adapter.apply_event_to_html(
                final_html, self.final_summary_text, self._guardrail_event)

        # OBDA-READ (OBDA_VISION.md §5) — make the governed-data lane observable: scan this turn's
        # tool calls into governed (compile_metric) vs free-formed (raw SQL). Deterministic,
        # 0-token; abstains (None) when the turn did no data ops. Stamped for reload (#17) by
        # the engine into turn_summary['obda']; yielded live below beside grounding/guardrail.
        from trusted_data_agent.agent import obda as _obda_mod
        _kg_calls = _obda_mod.calls_from_trace(self.turn_action_history)
        self._obda_event = self._run_obda_scan(_kg_calls)
        self._record_kg_used(_kg_calls)   # `used` KG telemetry — not gated on OBDA-READ

        # Honesty post-condition (charter #14: all data-running engines). Runs after
        # grounding/guardrail so any rewrite is the FINAL text. When the answer asserts
        # non-existence but every raw-SQL data query this turn failed (none succeeded), a
        # confident false negative is flagged (observe) or rewritten to an honest
        # retrieval-failure (enforce). Default observe → byte-identical (#15). Stamped for
        # reload (#17). Deterministic, 0-token; fails open.
        _h_text, self._honesty_event = await self._run_honesty_gate(
            self.final_summary_text, _obda_mod.calls_from_trace(self.turn_action_history))
        # Adopt the gate's text when it ACTED ON the text: an enforce rewrite
        # (answer_contained) OR an appended grain-substitution disclosure
        # (disclosure_appended) — gating on answer_contained alone silently
        # dropped the appended disclosure the append act exists to deliver.
        if (self._honesty_event or {}).get("acted") and (
                self._honesty_event.get("answer_contained")
                or self._honesty_event.get("disclosure_appended")):
            self.final_summary_text = _h_text
            from trusted_data_agent.agent.honesty import adapter as _honesty_adapter
            final_html = _honesty_adapter.apply_to_html(final_html, self._honesty_event, _h_text)

        # F-001 — cited-but-unread. Runs AFTER the honesty gate so it sees the final
        # text: if honesty rewrote the answer, the citations in the delivered text are
        # what a reader will act on. Abstains byte-identically without a progressive
        # library, which is every profile that has not opted into one (#15).
        _c_text, self._citation_event = self._run_citation_gate(
            self.final_summary_text, _obda_mod.calls_from_trace(self.turn_action_history))
        if (self._citation_event or {}).get("disclosure_appended"):
            self.final_summary_text = _c_text
            from trusted_data_agent.agent.honesty import adapter as _honesty_adapter2
            final_html = _honesty_adapter2.apply_to_html(
                final_html, self._citation_event, _c_text)

        # S3 — number lineage: receipts sealed, figures marked, an unreceipted figure
        # disclosed under an acting honesty mode. Off ⇒ byte-identical. Stamped for reload.
        self.final_summary_text, final_html, self._compute_lineage_event = self._run_compute_lineage(
            self.final_summary_text, final_html)
        # S5 — the budget's finish line (no armed budget ⇒ untouched)
        self.final_summary_text, final_html = self._apply_turn_budget_finish(self.final_summary_text, final_html)
        # PROP-008 — a question whose subject is not in the model: the platform's note + act
        self.final_summary_text, final_html = self._apply_model_gap(self.final_summary_text, final_html)

        # Now, save both versions to their respective histories
        tool_context = self._build_tool_context_summary() if self._detect_profile_type() == "tool_enabled" else None
        await session_manager.add_message_to_histories(
            self.user_uuid,
            self.session_id,
            'assistant',
            content=self.final_summary_text, # Clean text for LLM's chat_object
            html_content=final_html,         # Rich HTML for UI's session_history
            tool_context=tool_context,
            is_session_primer=self.is_session_primer
        )
        # --- MODIFICATION END ---

        # NOTE: Removed redundant "LLM has generated the final answer" event (Issue #14)
        # The final_answer event below serves as the visual confirmation in the UI

        # Grounding gate observability event (live) before the answer; stamped into
        # turn_summary by the engine for reload (#17).
        if (getattr(self, "_grounding_event", None) or {}).get("decision"):
            yield self._format_sse_with_depth({
                "step": "Grounding Gate",
                "type": "grounding_gate",
                "details": self._grounding_event
            })
        if (getattr(self, "_guardrail_event", None) or {}).get("acted"):
            yield self._format_sse_with_depth({
                "step": "Guardrail",
                "type": "guardrail",
                "details": self._guardrail_event
            })
        if getattr(self, "_obda_event", None):
            yield self._format_sse_with_depth({
                "step": "Governed Data Operations",
                "type": "obda",
                "details": self._obda_event
            })
        if (getattr(self, "_honesty_event", None) or {}).get("acted"):
            yield self._format_sse_with_depth({
                "step": "Honesty Check",
                "type": "honesty",
                "details": self._honesty_event
            })
        if getattr(self, "_compute_lineage_event", None):
            from trusted_data_agent.agent.compute_lineage import step_title as _lin_title
            yield self._format_sse_with_depth({
                "step": _lin_title(self._compute_lineage_event),
                "type": "compute_lineage",
                "details": self._compute_lineage_event
            })
        if (getattr(self, "_citation_event", None) or {}).get("acted"):
            yield self._format_sse_with_depth({
                "step": "Citation Check",
                "type": "honesty",
                "details": self._citation_event
            })

        # --- MODIFICATION START: Include both HTML and clean text in response ---
        yield self._format_sse_with_depth({
            "final_answer": final_html,
            "final_answer_text": self.final_summary_text,  # Clean text for LLM consumption
            "tts_payload": tts_payload,
            "source": self.source,
            "turn_id": self.current_turn_number, # Use the authoritative instance variable
            "session_id": self.session_id,  # Include session_id for filtering when switching sessions
            # Raw execution data for API consumers
            "execution_trace": self.turn_action_history,
            "collected_data": self.structured_collected_data,
            "turn_input_tokens": self.turn_input_tokens,
            "turn_output_tokens": self.turn_output_tokens,
            "is_session_primer": self.is_session_primer,
            # S1.1 — the turn's duration on the SAME basis the turn persists (one clock, two moments)
            "duration_ms": (int((time.time() - self.tool_enabled_start_time) * 1000) if hasattr(self, 'tool_enabled_start_time') else self._turn_duration_ms()),
            # S1.1 — the checks that ran, as the reload will read them (one verdict, two moments)
            "checks": _answer_checks.checks_summary(
                getattr(self, "_grounding_event", None), getattr(self, "_guardrail_event", None),
                getattr(self, "_honesty_event", None), getattr(self, "_compute_lineage_event", None)),
        }, "final_answer")
        # --- MODIFICATION END ---

        # --- PHASE 2: Emit execution_complete lifecycle event for tool_enabled ---
        try:
            profile_type = self._detect_profile_type()
            if profile_type == "tool_enabled":
                # Calculate duration from tracked start time
                duration_ms = int((time.time() - self.tool_enabled_start_time) * 1000) if hasattr(self, 'tool_enabled_start_time') else 0

                # §3.3: turn cost = Σ per-call costs (dual-model aware) for the completion card.
                _turn_cost = self._resolve_turn_cost_usd()

                complete_event_payload = {
                    "profile_type": "tool_enabled",
                    "profile_tag": self._get_current_profile_tag(),
                    "phases_executed": len([a for a in self.turn_action_history if isinstance(a.get("action"), dict) and a["action"].get("tool_name") != "TDA_SystemLog"]),
                    "total_input_tokens": self.turn_input_tokens,
                    "total_output_tokens": self.turn_output_tokens,
                    "duration_ms": duration_ms,
                    "cost_usd": _turn_cost,
                    "success": True
                }

                complete_event = self._emit_lifecycle_event("execution_complete", complete_event_payload)
                yield complete_event

                # For depth=0: execution_complete is pre-collected into tool_enabled_events
                # before turn_summary save (in the finally block). SSE emission here is for live UI only.
                # For depth>0 (sub-executors via is_synthesis_from_history path): the finally block's
                # pre-build is guarded by execution_depth==0, so we store here instead.
                if self.execution_depth > 0 and hasattr(self, 'tool_enabled_events'):
                    self.tool_enabled_events.append({
                        "type": "execution_complete",
                        "payload": complete_event_payload,
                        "metadata": {"execution_depth": self.execution_depth}
                    })

                app_logger.info(f"✅ Emitted execution_complete event for tool_enabled profile (depth={self.execution_depth})")
        except Exception as e:
            # Silent failure - don't break execution
            app_logger.warning(f"Failed to emit execution_complete event: {e}")
        # --- PHASE 2 END ---

        # --- Session Name Generation (AFTER final answer) ---
        # Generate session name for first turn AFTER final answer (consolidation requirement)
        # Only for top-level executor (depth==0) to avoid duplicate generation from sub-executors
        # Store collected events in instance variable for workflow history
        self.session_name_events = []
        self.session_name_tokens = (0, 0)

        # Generate session name on first NON-PRIMER turn only
        # Note: Primers are regular turns, so first real query might be turn 2+
        # We check session name not set AND is_session_primer to handle both cases
        if (self.execution_depth == 0 and
            not self.is_delegated_task and
            not self.is_session_primer):  # Skip session primers
            try:
                session_data = await session_manager.get_session(self.user_uuid, self.session_id)
                current_name = session_data.get("name", "") if session_data else ""

                # Generate name only if not yet set (works for turn 1 or turn 2+ after primer)
                if session_data and (not current_name or current_name == "New Chat"):
                    app_logger.info(f"[SessionName] ✅ GENERATING name for session {self.session_id} (AFTER final answer)")

                    # Generate and emit events using unified generator
                    async for event_dict, event_type, in_tok, out_tok in generate_session_name_with_events(
                        user_query=self.original_user_input,
                        session_id=self.session_id,
                        llm_interface="executor",
                        llm_dependencies=self.dependencies,
                        user_uuid=self.user_uuid,
                        active_profile_id=self.active_profile_id,
                        current_provider=self.current_provider,
                        current_model=self.current_model,
                        profile_llm_instance=self.profile_llm_instance,
                        emit_events=True
                    ):
                        if event_dict is None:
                            # Final yield: update session name
                            new_name = event_type
                            self.session_name_tokens = (in_tok, out_tok)

                            # Add session name tokens to turn totals and session totals
                            if in_tok > 0 or out_tok > 0:
                                self.turn_input_tokens += in_tok
                                self.turn_output_tokens += out_tok
                                await session_manager.update_token_count(
                                    self.user_uuid, self.session_id, in_tok, out_tok
                                )
                                # Emit token_update event so UI reflects updated session totals
                                updated_session = await session_manager.get_session(self.user_uuid, self.session_id)
                                if updated_session:
                                    # LOCAL import like every other site in this file — this
                                    # block used the bare name and NameError'd on EVERY
                                    # Optimize name generation since the July fold landed,
                                    # swallowed by the outer [SessionName] catch: no fold, no
                                    # token_update, no turn-count update, silently.
                                    from trusted_data_agent.core.cost_manager import get_cost_manager
                                    _name_cost = get_cost_manager().calculate_cost(
                                        provider=self.current_provider or "Unknown",
                                        model=self.current_model or "Unknown",
                                        input_tokens=in_tok,
                                        output_tokens=out_tok
                                    )
                                    # §3.2-D: fold the session-name call's cost into the turn-cost
                                    # accumulator so the engine's authoritative turn_cost (Σ per-call)
                                    # includes it — the session_name_generation_complete event carries
                                    # this same cost_usd, so _getTurnCost's event-sum stays reconciled.
                                    # GUARD (fold only when the main pipeline already accumulated): on a
                                    # direct-call path the accumulator is empty and _resolve_turn_cost_usd
                                    # falls back to re-pricing turn_input_tokens (which ALREADY includes the
                                    # session-name tokens) — folding there would return a name-only cost.
                                    if getattr(self, "turn_cost_usd", 0.0) > 0:
                                        self.turn_cost_usd += (_name_cost or 0.0)
                                    yield self._format_sse_with_depth({
                                        "statement_input": in_tok,
                                        "statement_output": out_tok,
                                        "turn_input": self.turn_input_tokens,
                                        "turn_output": self.turn_output_tokens,
                                        "total_input": updated_session.get("input_tokens", 0),
                                        "total_output": updated_session.get("output_tokens", 0),
                                        "call_id": "session_name_generation",
                                        "cost_usd": _name_cost
                                    }, "token_update")
                                # Update turn token counts in workflow_history for reload.
                                # Pass the name call's ALREADY-PRICED cost as the increment:
                                # the None path re-prices the WHOLE turn at the session
                                # model's rates, which un-does the dual-model per-call sum
                                # the accumulator exists to keep (the July imperative).
                                await session_manager.update_turn_token_counts(
                                    self.user_uuid, self.session_id, self.current_turn_number,
                                    self.turn_input_tokens, self.turn_output_tokens,
                                    added_cost_usd=(_name_cost or 0.0)
                                )

                            if new_name and new_name != "New Chat":
                                try:
                                    await session_manager.update_session_name(
                                        self.user_uuid, self.session_id, new_name
                                    )
                                    yield self._format_sse_with_depth({
                                        "session_id": self.session_id,
                                        "newName": new_name
                                    }, "session_name_update")
                                    app_logger.info(f"[SessionName] ✅ Updated session name to '{new_name}'")
                                except Exception as name_e:
                                    app_logger.error(f"[SessionName] ❌ Failed to update: {name_e}", exc_info=True)
                        else:
                            # Emit SSE event and collect for workflow history
                            yield self._format_sse_with_depth(event_dict, event_type)
                            self.session_name_events.append({
                                "type": event_type,
                                "payload": event_dict  # Full event data including step, type, details
                            })
            except Exception as e:
                app_logger.error(f"[SessionName] ❌ Error during generation: {e}", exc_info=True)
        elif self.is_session_primer:
            app_logger.debug(f"[SessionName] ⏭️  Skipping name generation for session primer: {self.original_user_input[:50]}...")