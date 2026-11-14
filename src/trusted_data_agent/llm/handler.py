# src/trusted_data_agent/llm/handler.py
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
import asyncio
import base64
import json
import logging
import httpx
import re
import random
import time
import copy
import difflib
import pprint # Added for debugging
import unicodedata
from typing import Tuple, List

from trusted_data_agent.llm.lazy_sdk import sdk as _sdk, loaded_attrs as _loaded_attrs  # provider SDKs load on first use
genai = _sdk("google.genai")
genai_types = _sdk("google.genai.types")
_anthropic = _sdk("anthropic")
_openai = _sdk("openai")
from pydantic import ValidationError, BaseModel
boto3 = _sdk("boto3")

from trusted_data_agent.core.config import APP_CONFIG
from trusted_data_agent.core.config import get_user_provider, get_user_model, get_user_model_provider_in_profile
# --- MODIFICATION START: Import session manager functions ---
from trusted_data_agent.core.session_manager import get_session, update_token_count
# --- MODIFICATION END ---
from trusted_data_agent.agent.prompts import PROVIDER_SYSTEM_PROMPTS
from trusted_data_agent.llm import thinking_support
import contextlib
from trusted_data_agent.llm import streaming as _streaming
from trusted_data_agent.llm import cache_policy as _cache_policy
from trusted_data_agent.llm import resilience as _res
from trusted_data_agent.auth.database import get_db_session
from trusted_data_agent.auth.models import RecommendedModel

llm_logger = logging.getLogger("llm_conversation")

import contextvars as _contextvars
import time as _time

#: Per-call usage extras the (text, in, out, provider, model) return tuple cannot carry
#: without touching every caller: today {"cached_tokens": int}. A ContextVar is
#: task-local, so concurrent turns never read each other's usage.
LAST_CALL_USAGE: "_contextvars.ContextVar[dict]" = _contextvars.ContextVar("uderia_last_call_usage", default={})

_MCP_SYSTEM_NAME_CACHE = {"value": None, "ts": 0.0}
_MCP_SYSTEM_NAME_TTL = 300.0


def get_mcp_system_name() -> str:
    """The configured MCP system name, read from tda_auth.db at most every 5 minutes.

    This was a synchronous sqlite open+query on EVERY LLM call (and again in the
    planner) — on the hot path, under the process-wide SQLite lock, for a value that
    changes essentially never.
    """
    now = _time.monotonic()
    if _MCP_SYSTEM_NAME_CACHE["value"] is not None and now - _MCP_SYSTEM_NAME_CACHE["ts"] < _MCP_SYSTEM_NAME_TTL:
        return _MCP_SYSTEM_NAME_CACHE["value"]
    value = "Database System"
    try:
        from trusted_data_agent.core.utils import get_project_root
        from trusted_data_agent.core import sqlite_util as _su
        conn = _su.connect(get_project_root() / "tda_auth.db")
        try:
            row = conn.cursor().execute(
                "SELECT default_value FROM global_parameters WHERE parameter_name = 'mcp_system_name'"
            ).fetchone()
            if row and row[0]:
                value = row[0]
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — a display name must never break a call
        app_logger.debug(f"mcp_system_name lookup failed, using default: {exc}")
    _MCP_SYSTEM_NAME_CACHE.update(value=value, ts=now)
    return value
llm_history_logger = logging.getLogger("llm_conversation_history")
from trusted_data_agent.filestore.session_files import block_bytes as _block_bytes
app_logger = logging.getLogger("quart.app")

class OllamaClient:
    """A simple async client for interacting with the Ollama API."""
    def __init__(self, host: str):
        if not host.startswith("http://") and not host.startswith("https://"):
            self.host = f"http://{host}"
            app_logger.info(f"Ollama host missing protocol. Automatically prepending 'http://'. New host: {self.host}")
        else:
            self.host = host
        self.client = httpx.AsyncClient(base_url=self.host, timeout=120.0)

    async def list_models(self):
        try:
            response = await self.client.get("/api/tags")
            response.raise_for_status()
            return response.json().get("models", [])
        except httpx.RequestError as e:
            app_logger.error(f"Ollama API request error: {e}")
            raise RuntimeError("Could not connect to Ollama server.") from e

    async def chat(self, model: str, messages: list, system_prompt: str, on_delta=None):
        """`on_delta` (a `streaming.DeltaSink` or None): when given, the reply is streamed
        (NDJSON) and each text delta is handed to the sink; the returned dict has the SAME
        shape as the non-streaming reply, so the caller reads one shape."""
        try:
            payload = {
                "model": model,
                "messages": messages,
                "system": system_prompt,
                "stream": False
            }
            if on_delta is not None:
                return await _streaming.ollama_stream(self.client, on_delta, payload)
            response = await self.client.post("/api/chat", json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as e:
            app_logger.error(f"Ollama API request error: {e}")
            raise RuntimeError("Error during chat completion with Ollama.") from e

from json import JSONDecodeError

def parse_and_coerce_llm_response(response_text: str, target_model: BaseModel) -> Tuple[BaseModel, List[str]]:
    """
    A resilient parser for LLM responses that attempts to correct common
    schema deviations and reports on the corrections made. Includes robust
    JSON extraction and error handling.

    Args:
        response_text: The raw string output from the LLM.
        target_model: The Pydantic model class to validate against.

    Returns:
        A tuple containing the validated model instance and a list of
        strings describing any corrections that were applied.

    Raises:
        json.JSONDecodeError: If no valid JSON can be extracted or parsed
                              after sanitization.
        ValidationError: If the JSON is valid but cannot be coerced into the target model.
    """
    app_logger.debug(f"Attempting to parse and coerce response into {target_model.__name__}.")
    correction_descriptions = []

    sanitized_response_text = _sanitize_llm_output(response_text)

    # 1. Robustly extract JSON from the sanitized text
    json_match = re.search(r'```json\s*\n(.*?)\n\s*```|(\{.*\}|\[.*\])', sanitized_response_text, re.DOTALL)
    if not json_match:
        app_logger.error(f"No valid JSON structure found in sanitized LLM response: {sanitized_response_text[:500]}...")
        raise JSONDecodeError("No valid JSON object or list found in the LLM response.", sanitized_response_text, 0)

    json_str = next(g for g in json_match.groups() if g is not None)
    if not json_str:
        app_logger.error(f"Extracted JSON string is empty from sanitized response: {sanitized_response_text[:500]}...")
        raise JSONDecodeError("Extracted JSON string is empty.", sanitized_response_text, 0)

    try:
        data = json.loads(json_str)
    except JSONDecodeError as e:
        app_logger.warning(f"Initial JSON parsing failed: {e}. Attempting string literal repair...")
        try:
            repaired = _repair_json_string_literals(json_str)
            data = json.loads(repaired)
            app_logger.info("JSON string literal repair successful (fixed unescaped chars in string values).")
        except JSONDecodeError:
            app_logger.error(f"JSON parsing failed even after repair. Original error: {e}. String: {json_str[:500]}...")
            raise e  # Re-raise the original error

    # 1b. Normalize corrupted field names before validation
    if isinstance(data, dict):
        data, key_corrections = _normalize_keys(data, target_model)
        correction_descriptions.extend(key_corrections)

    # 2. First validation attempt
    try:
        validated_data = target_model.model_validate(data)
        app_logger.debug("Initial validation successful. No coercion needed.")
        return validated_data, correction_descriptions
    except ValidationError as e:
        app_logger.warning(f"Initial validation against {target_model.__name__} failed. Attempting proactive correction.")

        # 3. On failure, diagnose and apply corrections
        corrected_data = copy.deepcopy(data)
        errors = e.errors()

        for error in errors:
            field_name = error['loc'][0] if error['loc'] else None

            # Correction rule: List of strings to list of objects with a 'text' key
            if 'model_type' in error['type'] and isinstance(error['input'], list) and all(isinstance(i, str) for i in error['input']):
                if field_name and field_name in corrected_data:
                    correction_msg = f"Corrected field '{field_name}': LLM provided a list of strings instead of a list of objects; the system automatically wrapped each string in the required format."
                    app_logger.info(correction_msg)
                    correction_descriptions.append(correction_msg)
                    corrected_data[field_name] = [{"text": item} for item in error['input']]

            # Correction rule: Coerce numbers to strings for specific fields
            if 'string_type' in error['type'] and isinstance(error['input'], (int, float)):
                field_loc = error.get('loc', [])
                if len(field_loc) > 1 and field_loc[0] == 'key_metric' and field_loc[1] == 'value':
                     correction_msg = "Corrected field 'key_metric.value': LLM provided a number instead of a string; the system automatically converted it."
                     app_logger.info(correction_msg)
                     correction_descriptions.append(correction_msg)
                     if 'key_metric' in corrected_data and isinstance(corrected_data['key_metric'], dict):
                         corrected_data['key_metric']['value'] = str(error['input'])

        # 4. Final validation attempt after corrections
        try:
            validated_data = target_model.model_validate(corrected_data)
            app_logger.info(f"Proactive correction successful. Data is now valid for {target_model.__name__}.")
            return validated_data, correction_descriptions
        except ValidationError:
            app_logger.error(f"Correction failed for {target_model.__name__}. Re-raising original validation error. Corrected data was: {json.dumps(corrected_data)}")
            raise e # Re-raise the original validation error 'e'

_INVALID_JSON_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')

def _sanitize_llm_output(text: str) -> str:
    """
    Strips invalid characters from LLM output, focusing on problematic
    ASCII control characters that break JSON parsing, while preserving
    valid whitespace like tabs, newlines, and carriage returns. Also removes
    Byte Order Mark (BOM).
    """
    if not isinstance(text, str):
        return "" # Return empty string for non-string input

    # Remove BOM if present
    sanitized_text = text.replace('\ufeff', '')

    # Remove invalid control characters using the stricter regex
    sanitized_text = _INVALID_JSON_CONTROL_CHARS_RE.sub('', sanitized_text)

    # Basic stripping of leading/trailing whitespace
    return sanitized_text.strip()


def _normalize_keys(data: dict, target_model) -> tuple[dict, list[str]]:
    """
    Deterministically normalize JSON keys to match expected Pydantic field names.

    Handles LLM corruption patterns where field names contain non-ASCII characters,
    special Unicode symbols replacing underscores, or wrong suffixes on recognizable
    prefixes (e.g. 'direct在玩家中' or 'direct✑answer' instead of 'direct_answer').

    Returns:
        Tuple of (normalized dict, list of correction description strings).
    """
    expected_keys = set(target_model.model_fields.keys())
    corrections = []

    # Fast path: all keys already match
    if set(data.keys()) <= expected_keys:
        return data, corrections

    normalized = {}
    for original_key in list(data.keys()):
        if original_key in expected_keys:
            normalized[original_key] = data[original_key]
            continue

        # Step 1: Strip non-ASCII, replace non-alphanumeric with _, collapse, lowercase
        cleaned = unicodedata.normalize('NFKD', original_key)
        cleaned = ''.join(c if c.isascii() else '' for c in cleaned)
        cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', cleaned)
        cleaned = re.sub(r'_+', '_', cleaned).strip('_').lower()

        # Step 2: Exact match after cleaning
        if cleaned in expected_keys and cleaned not in normalized:
            correction_msg = (
                f"Corrected key '{original_key}' -> '{cleaned}': "
                f"non-ASCII/special characters removed to match schema field."
            )
            app_logger.info(correction_msg)
            corrections.append(correction_msg)
            normalized[cleaned] = data[original_key]
            continue

        # Step 3: Fuzzy match
        matches = difflib.get_close_matches(cleaned, list(expected_keys), n=1, cutoff=0.6)
        if matches and matches[0] not in normalized:
            matched_key = matches[0]
            correction_msg = (
                f"Corrected key '{original_key}' -> '{matched_key}': "
                f"fuzzy-matched after normalization (cleaned: '{cleaned}')."
            )
            app_logger.info(correction_msg)
            corrections.append(correction_msg)
            normalized[matched_key] = data[original_key]
            continue

        # No match — keep as-is
        app_logger.warning(
            f"Key normalization: '{original_key}' (cleaned: '{cleaned}') "
            f"could not be matched to any expected field in {target_model.__name__}. Keeping as-is."
        )
        normalized[original_key] = data[original_key]

    # Recursively normalize nested dicts for nested Pydantic models
    for key, value in list(normalized.items()):
        if key in expected_keys and isinstance(value, dict):
            field_info = target_model.model_fields.get(key)
            if field_info:
                annotation = field_info.annotation
                # Unwrap Optional[T] to get T
                args = getattr(annotation, '__args__', ())
                for arg in args:
                    if hasattr(arg, 'model_fields'):
                        annotation = arg
                        break
                if hasattr(annotation, 'model_fields'):
                    value_normalized, nested_corrections = _normalize_keys(value, annotation)
                    normalized[key] = value_normalized
                    corrections.extend(nested_corrections)

    return normalized, corrections


_VALID_JSON_ESCAPE_CHARS = set('"\\\\/bfnrtu')

def _repair_json_string_literals(json_str: str) -> str:
    """Escape literal newlines/tabs and invalid escape sequences inside JSON string values.

    LLMs sometimes emit actual newline characters inside JSON string
    values instead of the escaped \\n sequence, or produce invalid
    escape sequences like \\e, \\s, \\p in markdown content. This
    function walks the JSON text, tracks whether we're inside a string
    region (between unescaped quotes), and fixes these issues.
    """
    result = []
    in_string = False
    i = 0
    while i < len(json_str):
        ch = json_str[i]
        if ch == '"':
            num_preceding_backslashes = 0
            j = i - 1
            while j >= 0 and json_str[j] == '\\':
                num_preceding_backslashes += 1
                j -= 1
            if num_preceding_backslashes % 2 == 0:
                in_string = not in_string
            result.append(ch)
        elif in_string and ch == '\n':
            result.append('\\n')
        elif in_string and ch == '\r':
            result.append('\\r')
        elif in_string and ch == '\t':
            result.append('\\t')
        elif in_string and ch == '\\':
            # Check if next character forms a valid JSON escape
            if i + 1 < len(json_str) and json_str[i + 1] not in _VALID_JSON_ESCAPE_CHARS:
                # Invalid escape sequence (e.g., \e, \s, \p) — double the backslash
                result.append('\\\\')
            else:
                result.append(ch)
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _extract_final_answer_from_json(text: str) -> str:
    """
    Detects if the LLM hallucinated and wrapped a FINAL_ANSWER inside a JSON object.
    If so, it extracts the FINAL_ANSWER string and returns it.
    This makes the agent more robust to common LLM formatting errors.
    """
    sanitized_text = _sanitize_llm_output(text)
    try:
        json_match = re.search(r"```json\s*\n(.*?)\n\s*```|(\{.*?\})", sanitized_text, re.DOTALL)
        if not json_match:
            return sanitized_text # Return sanitized text if no JSON found

        json_str = json_match.group(1) or json_match.group(2)
        if not json_str:
            return sanitized_text # Return sanitized text if extracted JSON is empty

        data = json.loads(json_str.strip())

        def find_answer_in_values(d):
            if isinstance(d, dict):
                for value in d.values():
                    found = find_answer_in_values(value)
                    if found:
                        return found
            elif isinstance(d, list):
                for item in d:
                    found = find_answer_in_values(item)
                    if found:
                        return found
            elif isinstance(d, str) and "FINAL_ANSWER:" in d:
                return d
            return None

        final_answer_value = find_answer_in_values(data)

        if final_answer_value:
            app_logger.warning(f"LLM hallucination detected and corrected. Extracted FINAL_ANSWER from JSON.")
            return final_answer_value

    except (json.JSONDecodeError, AttributeError):
        return sanitized_text

    return sanitized_text

def _condense_and_clean_history(history: list, provider: str = None) -> list:
    """
    Sanitizes conversation history to save tokens by being abstract and provider-agnostic.
    0. Filters out turns where 'isValid' is False.
    1. Normalizes history from different provider formats by checking for object attributes.
    2. Aggressively removes ALL old capability definitions from the history.
    3. Replaces duplicate tool outputs with a placeholder.
    4. Denormalizes the history back to the original provider's format.

    ``provider`` is the effective provider for THIS call, threaded from the caller (which passes
    the per-call ``current_provider``). Defaults to the process-global ``APP_CONFIG.CURRENT_PROVIDER``
    for backward compatibility. Threading it here removes a pre-existing cross-user race: two
    concurrent turns on different providers previously both read the shared global, so one turn's
    history could be (de)normalized to the other turn's provider format.
    """
    provider = provider if provider is not None else APP_CONFIG.CURRENT_PROVIDER
    if not history:
        return []

    # --- MODIFICATION START: Filter out invalid messages ---
    # A message is valid if 'isValid' is missing (defaults to true) or is explicitly true.
    valid_history = [msg for msg in history if isinstance(msg, dict) and msg.get("isValid", True) is not False]
    app_logger.debug(f"History Condensation: Started with {len(history)} messages, {len(valid_history)} are valid.")
    # --- MODIFICATION END ---

    def _normalize_history(provider_history: list) -> list:
        """Converts provider-specific history to a generic internal format using type-aware checks."""
        normalized = []
        # --- MODIFICATION START: Handle Google history structure correctly ---
        if provider == "Google" and provider_history and hasattr(provider_history[0], 'role'):
            # Assume it's already in the expected structure with 'role' and 'parts'
            for msg in provider_history:
                role, content = "", ""
                role = msg.role
                if msg.parts and hasattr(msg.parts[0], 'text'):
                    content = msg.parts[0].text
                normalized.append({'role': role, 'content': content})
        # --- MODIFICATION END ---
        else: # Handle list of dicts format for other providers
            for msg in provider_history:
                role, content = "", ""
                if isinstance(msg, dict):
                    role = msg.get('role', '')
                    content = msg.get('content', '')
                normalized.append({'role': role, 'content': content})
        return normalized

    def _denormalize_history(generic_history: list, provider: str) -> list:
        """Converts the generic internal history back to a provider-specific format."""
        if provider == "Google":
            denormalized = []
            for msg in generic_history:
                role = 'model' if msg['role'] == 'assistant' else msg['role']
                # Ensure role is valid for Google API
                if role not in ['user', 'model']:
                    app_logger.warning(f"Invalid role '{role}' found during Google history denormalization. Skipping message.")
                    continue
                denormalized.append({
                    'role': role,
                    'parts': [{'text': msg['content']}]
                })
            return denormalized
        # For other providers, assume list of dicts is fine
        return generic_history

    normalized_history = _normalize_history(valid_history)

    cleaned_history = []
    seen_tool_outputs = set()
    capabilities_pattern = re.compile(r'# Capabilities\n--- Available Tools ---.*', re.DOTALL)

    system_prompt_wrapper_pattern = re.compile(r"SYSTEM PROMPT:.*USER PROMPT:\n", re.DOTALL)

    for msg in normalized_history:
        msg_copy = copy.deepcopy(msg)
        content = msg_copy.get('content', '')

        # --- MODIFICATION START: Handle Google's 'model' role ---
        msg_role = msg_copy.get('role')
        # --- MODIFICATION END ---

        if msg_role == 'user' and system_prompt_wrapper_pattern.match(content):
            app_logger.debug("History Condensation: Removing system prompt wrapper from user message.")
            content = system_prompt_wrapper_pattern.sub("", content)

        if capabilities_pattern.search(content):
            app_logger.debug("History Condensation: Removing obsolete capability definitions.")
            content = capabilities_pattern.sub("# Capabilities\n[... Omitted for Brevity ...]", content)

        # --- MODIFICATION START: Check for 'assistant' OR 'model' role ---
        if msg_role in ['assistant', 'model']:
        # --- MODIFICATION END ---
            try:
                # Attempt to parse as JSON to normalize and identify duplicates
                json_content = json.loads(content)
                normalized_content = json.dumps(json_content, sort_keys=True)

                if normalized_content in seen_tool_outputs:
                    app_logger.debug("History Condensation: Replacing duplicate tool output.")
                    content = json.dumps({"status": "success", "comment": "Duplicate output omitted for brevity."})
                else:
                    seen_tool_outputs.add(normalized_content)
            except (json.JSONDecodeError, TypeError):
                # If it's not JSON (e.g., a text response), keep it as is
                pass

        msg_copy['content'] = content
        cleaned_history.append(msg_copy)

    return _denormalize_history(cleaned_history, provider)


def _get_full_system_prompt(session_data: dict, dependencies: dict, system_prompt_override: str = None, active_prompt_name_for_filter: str = None, source: str = "text", active_profile_id: str = None, current_provider: str = None, user_uuid: str = None, strip_capabilities: bool = False) -> str:
    """
    Constructs the final system prompt based on the user's license tier and profile mapping.

    Args:
        session_data: Session data dictionary
        dependencies: Dependencies dictionary containing STATE
        system_prompt_override: Optional override prompt (takes precedence)
        active_prompt_name_for_filter: Prompt name to filter from context
        source: Source of the call ("text", "prompt_library", etc.)
        active_profile_id: Active profile ID for prompt resolution
        current_provider: Current LLM provider for prompt resolution
        user_uuid: User UUID for profile config resolution
    """
    if system_prompt_override:
        return system_prompt_override

    if not session_data or not dependencies or 'STATE' not in dependencies:
        return "You are a helpful assistant."

    base_prompt_text = ""
    license_info = session_data.get("license_info", {})
    user_tier = license_info.get("tier")
    privileged_tiers = ["Prompt Engineer", "Enterprise"]

    if user_tier in privileged_tiers and session_data.get("system_prompt_template"):
        app_logger.info(f"Using custom system prompt for privileged user (Tier: {user_tier}).")
        base_prompt_text = session_data["system_prompt_template"]
    else:
        app_logger.info(f"Using profile-mapped system prompt for user (Tier: {user_tier or 'Standard'}).")
        
        # Use profile-aware prompt resolution if profile and provider are available
        if active_profile_id and current_provider:
            try:
                from trusted_data_agent.agent.profile_prompt_resolver import ProfilePromptResolver
                resolver = ProfilePromptResolver(profile_id=active_profile_id, provider=current_provider)
                profile_prompt = resolver.get_master_system_prompt()
                
                if profile_prompt:
                    base_prompt_text = profile_prompt
                    app_logger.info(f"Resolved master system prompt via profile mapping: profile={active_profile_id}, provider={current_provider}")
                else:
                    # Fallback to default if resolution fails
                    app_logger.warning(f"Profile prompt resolution returned None, using default")
                    fallback_provider = current_provider or APP_CONFIG.CURRENT_PROVIDER
                    base_prompt_text = str(PROVIDER_SYSTEM_PROMPTS.get(fallback_provider, PROVIDER_SYSTEM_PROMPTS["Google"]))
            except Exception as e:
                app_logger.error(f"Error resolving profile prompt: {e}, falling back to default")
                fallback_provider = current_provider or APP_CONFIG.CURRENT_PROVIDER
                base_prompt_text = str(PROVIDER_SYSTEM_PROMPTS.get(fallback_provider, PROVIDER_SYSTEM_PROMPTS["Google"]))
        else:
            # Fallback to default if profile/provider not available
            fallback_provider = current_provider or APP_CONFIG.CURRENT_PROVIDER
            base_prompt_text = str(PROVIDER_SYSTEM_PROMPTS.get(fallback_provider, PROVIDER_SYSTEM_PROMPTS["Google"]))

    STATE = dependencies['STATE']

    # --- Component instructions pipeline ---
    # Assembles instructions from all active components for the current profile.
    from trusted_data_agent.components.manager import get_component_instructions_for_prompt
    component_instructions_section = get_component_instructions_for_prompt(
        active_profile_id, user_uuid, session_data
    )

    tools_context = ""
    use_condensed_context = False
    if APP_CONFIG.CONDENSE_SYSTEMPROMPT_HISTORY and session_data and session_data.get("full_context_sent"):
        use_condensed_context = True

    tool_to_exclude = 'TDA_FinalReport' if source == 'prompt_library' else 'TDA_ComplexPromptReport'
    structured_tools = STATE.get('structured_tools', {})
    mcp_tools = STATE.get('mcp_tools', {})

    if use_condensed_context:
        app_logger.info("Session context: Using condensed (names-only) capability list for subsequent turn.")
        condensed_tools_parts = ["--- Available Tools (Names Only) ---"]
        for category, tools in sorted(structured_tools.items()):
            enabled_tools = [f"`{t['name']}`" for t in tools if not t.get('disabled') and t['name'] != tool_to_exclude]
            if enabled_tools:
                condensed_tools_parts.append(f"- **{category}**: {', '.join(enabled_tools)}")
        tools_context = "\n".join(condensed_tools_parts) if len(condensed_tools_parts) > 1 else "--- No Tools Available ---"
    else:
        app_logger.info("Session context: Sending full, detailed capability list for the first turn.")
        tool_context_parts = ["--- Available Tools ---"]
        for category, tools in sorted(structured_tools.items()):
            enabled_tools_in_category = [t for t in tools if not t['disabled'] and t['name'] != tool_to_exclude]
            if enabled_tools_in_category:
                tool_context_parts.append(f"--- Category: {category} ---")
                for tool_info in enabled_tools_in_category:
                    tool_obj = mcp_tools.get(tool_info['name'])
                    if not tool_obj: continue

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

    prompts_context = STATE.get('prompts_context', '')
    if active_prompt_name_for_filter:
        app_logger.info(f"Recursion prevention: Filtering active prompt '{active_prompt_name_for_filter}' from planner context.")

        filtered_prompts_context_parts = []
        current_prompt_block = []
        is_in_target_prompt = False

        for line in prompts_context.split('\n'):
            is_new_prompt_start = line.strip().startswith('- `') and ' (prompt):' in line

            if is_new_prompt_start:
                if current_prompt_block and not is_in_target_prompt:
                    filtered_prompts_context_parts.extend(current_prompt_block)

                current_prompt_block = [line]
                is_in_target_prompt = f"`{active_prompt_name_for_filter}`" in line
            else:
                current_prompt_block.append(line)

        if current_prompt_block and not is_in_target_prompt:
            filtered_prompts_context_parts.extend(current_prompt_block)

        prompts_context = "\n".join(filtered_prompts_context_parts)

    if use_condensed_context:
        condensed_prompts_parts = ["--- Available Prompts (Names Only) ---"]
        structured_prompts = STATE.get('structured_prompts', {})
        for category, prompts in sorted(structured_prompts.items()):
            enabled_prompts = [f"`{p['name']}`" for p in prompts if not p.get('disabled') and p['name'] != active_prompt_name_for_filter]
            if enabled_prompts:
                condensed_prompts_parts.append(f"- **{category}**: {', '.join(enabled_prompts)}")
        prompts_context = "\n".join(condensed_prompts_parts) if len(condensed_prompts_parts) > 1 else "--- No Prompts Available ---"

    # Only set full_context_sent if we actually sent tool/prompt context
    # Don't set it for llm_only profiles with empty dependencies
    if not use_condensed_context and session_data:
        # Count actual tools inside categories (not just empty categories)
        tool_count = sum(len(tools) for tools in STATE.get('structured_tools', {}).values())
        prompt_count = sum(len(prompts) for prompts in STATE.get('structured_prompts', {}).values())

        if tool_count > 0 or prompt_count > 0:
            session_data["full_context_sent"] = True
            app_logger.info(f"Session context: Marked full_context_sent=True ({tool_count} tools, {prompt_count} prompts shown)")
        else:
            app_logger.info("Session context: Skipping full_context_sent flag (no tools/prompts to show - likely llm_only profile or profile with all capabilities disabled)")

    mcp_system_name = get_mcp_system_name()

    if strip_capabilities:
        # The STRATEGIC PLANNING call carries the full, budget-managed capability
        # catalog in its own prompt (Available Capabilities); repeating it here
        # duplicated ~2.5K tokens on every turn for nothing. The rest of the
        # master prompt (directives, best practices, component guidelines) is kept
        # unchanged so provider behaviour is otherwise identical.
        tools_context = "(The capability catalog for this task is listed in the task prompt below.)"
        prompts_context = ""
    
    final_system_prompt = base_prompt_text.replace(
        '{component_instructions_section}', component_instructions_section
    ).replace(
        '{tools_context}', tools_context
    ).replace(
        '{prompts_context}', prompts_context
    ).replace(
        '{mcp_system_name}', mcp_system_name
    )

    return final_system_prompt

def _normalize_bedrock_model_id(model_id: str) -> str:
    """
    Normalizes a standard Bedrock model ID by removing versioning suffixes,
    but leaves Inference Profile ARNs untouched.
    """
    if model_id.startswith("arn:aws:bedrock:"):
        return model_id
    # Safely split by the version delimiter ':' and take the base model ID
    return model_id.split(':')[0]


async def _materialize_placeholder_llm(placeholder: dict, user_uuid: str, provider: str, model: str):
    """Lazily create the real LLM client a placeholder stands in for.

    ``switch_profile_context(validate_llm=False)`` parks ``{"placeholder": True, provider, model}``
    in ``APP_STATE['llm']`` and defers real client creation to "the first message". This implements
    that deferred creation.

    - Uses the POOLED ``get_or_create_llm_client(validate=False)`` — no network round-trip — and does
      NOT write the client back to any global slot, so cross-user isolation is preserved (the pool,
      keyed by provider/model/credentials, provides reuse).
    - ``user.id == user_uuid`` (User.id is the UUID string), so credentials are loaded directly with
      ``retrieve_credentials_for_provider(user_uuid, provider)``.
    - Raises a clear, actionable ``RuntimeError`` on failure — most importantly when NO CREDENTIALS
      are configured for the provider — instead of letting a placeholder dict reach the provider call
      (the cryptic ``'dict' object has no attribute 'chat'``).

    Imports are function-local: ``configuration_service`` imports this module, so a module-level
    import here would be circular.
    """
    prov = provider or placeholder.get("provider")
    mdl = model or placeholder.get("model")
    if not prov or not mdl:
        raise RuntimeError(
            f"LLM configuration is incomplete (provider={prov!r}, model={mdl!r}); cannot create a client."
        )

    credentials = {}
    if user_uuid:
        try:
            from trusted_data_agent.core.configuration_service import retrieve_credentials_for_provider
            _res = await retrieve_credentials_for_provider(user_uuid, prov)
            credentials = (_res or {}).get("credentials") or {}
        except Exception as _cred_err:
            app_logger.warning(f"Placeholder LLM: could not load credentials for {prov}: {_cred_err}")

    try:
        from trusted_data_agent.llm.client_factory import get_or_create_llm_client
        client = await get_or_create_llm_client(prov, mdl, credentials)  # pooled, validate=False → no network call
        app_logger.info(f"Materialized deferred LLM client for {prov}/{mdl} (placeholder → real client).")
        return client
    except Exception as _mk_err:
        raise RuntimeError(
            f"LLM client could not be created for {prov}/{mdl}: {_mk_err}. "
            f"This usually means no credentials are configured for {prov} — open the profile's LLM "
            f"configuration and Test the connection before using it."
        ) from _mk_err


async def _google_generate_content(llm_instance, model: str, contents, gen_config_kwargs: dict, sink=None):
    """`generate_content`, degrading gracefully when a model refuses to have thinking disabled.

    A handful of Gemini models answer `400 INVALID_ARGUMENT` to a request carrying
    `thinking_budget=0` and the capability is not discoverable (see
    `llm/thinking_support.py`), so it is learned here from the refusal: retry ONCE
    without the thinking config, and only if that succeeds record the model — a
    success is what proves the thinking config was the cause rather than blaming it
    for an unrelated 400 and permanently losing the optimisation for that model.
    Disabling thinking is only ever an optimisation, so this degrades to "thinking
    enabled", never to a failed turn.
    """
    thinking_cfg = gen_config_kwargs.get("thinking_config")
    disabling = thinking_cfg is not None and getattr(thinking_cfg, "thinking_budget", None) == 0

    async def _generate(cfg_kwargs):
        cfg = genai_types.GenerateContentConfig(**cfg_kwargs)
        if sink is not None:
            # The Governed Stream (S1): same request, streamed; the returned object carries
            # the same fields the caller reads (text / candidates / usage_metadata).
            return await _streaming.google_stream(llm_instance, sink, model=model, contents=contents, config=cfg)
        return await llm_instance.aio.models.generate_content(model=model, contents=contents, config=cfg)

    try:
        return await _generate(gen_config_kwargs)
    except Exception as exc:
        if not (disabling and thinking_support.looks_like_thinking_disable_refusal(exc)):
            raise
        retry_kwargs = thinking_support.with_thinking_headroom(
            {k: v for k, v in gen_config_kwargs.items() if k != "thinking_config"})
        try:
            if sink is not None:
                sink.reset()   # nothing was delivered on a refused request, but be exact
            response = await _generate(retry_kwargs)
        except Exception:
            # The retry failed too, so the thinking config was NOT the cause. Surface
            # the original error and leave the model's capability unclaimed.
            raise exc
        thinking_support.record_disable_refusal(model)
        return response


def _record_terminal(provider: str, exc: BaseException) -> None:
    """A terminal error means the provider ANSWERED (badly) and is reachable — except an empty
    account: it answers every call with the same refusal, so it counts against the breaker
    (the header's health strip shows it; an open breaker routes the next call to the signed
    fallback at once). Trust campaign C18."""
    if _res.out_of_credits(exc):
        _provider_health.record_failure(provider, "credits")
    else:
        _provider_health.record_probe_answered(provider)


_NO_TTL_1H: set = set()   # models that refused ttl=1h — learned from the provider's own error
_AFFINITY_REFUSED: set = set()    # (provider, model) that refused the cache-affinity field — learned, never resent
_CACHE_POINT_REFUSED: set = set() # (provider, model) on Bedrock that refused the cache markers — learned


def _resolve_cache_for_call(active_profile_id, user_uuid, provider, model) -> dict:
    """The Attested Cache (S2): the profile's cache setting under the master switch, plus the
    provider's capability. Never raises — an unresolvable profile reads as caching off."""
    try:
        enabled = bool(getattr(APP_CONFIG, "PROMPT_CACHING", False))
        profile = None
        if enabled and active_profile_id and user_uuid:
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                profile = get_config_manager().get_profile(active_profile_id, user_uuid)
            except Exception:  # noqa: BLE001
                profile = None
        pol = _cache_policy.resolve(profile or {}, enabled)
        cap = _cache_policy.capability(provider, model)
        if cap == _cache_policy.BREAKPOINTS and model and (provider, model) in _NO_TTL_1H and pol["ttl"] == "1h":
            pol["ttl"] = "5m"
        return {"enabled": pol["enabled"], "ttl": pol["ttl"], "capability": cap}
    except Exception:  # noqa: BLE001
        return {"enabled": False, "ttl": _cache_policy.DEFAULT_TTL, "capability": _cache_policy.NONE}


def _record_cache_reading(provider, model, usage_norm: dict, prefix_hash, cache_pol: dict) -> None:
    """Publish the call's cache reading: the task-local side channel (per call) and the
    turn accumulator (per turn). Never raises."""
    try:
        extra = {"cached_tokens": int(usage_norm.get("cached") or 0),
                 "cache_write_tokens": int(usage_norm.get("written") or 0),
                 "cache_capability": cache_pol.get("capability"), "cache_enabled": bool(cache_pol.get("enabled")),
                 "cache_ttl": cache_pol.get("ttl"), "prefix_hash": prefix_hash}
        LAST_CALL_USAGE.set(extra)
        from trusted_data_agent.agent import prompt_cache as _pc
        _pc.record_call(provider=provider, model=model, input_tokens=int(usage_norm.get("input") or 0),
                        output_tokens=int(usage_norm.get("output") or 0), cached=extra["cached_tokens"],
                        written=extra["cache_write_tokens"], prefix_hash=prefix_hash)
    except Exception as _e:  # noqa: BLE001
        app_logger.debug(f"[cache] reading not recorded: {_e}")


    # --- MODIFICATION START: Add user_uuid parameter ---
# ── S4 The Resilience Envelope — the handler's seams into the pure policy ─────────────────
from trusted_data_agent.core import provider_health as _provider_health
from trusted_data_agent.llm import fault_injection as _fault_injection
from trusted_data_agent.agent import resilience_envelope as _resilience_envelope


def _resilience_enabled() -> bool:
    try:
        return bool(getattr(APP_CONFIG, "RESILIENCE_ENVELOPE", False))
    except Exception:  # noqa: BLE001
        return False


def _fallback_for_call(active_profile_id, user_uuid):
    """The SIGNED fallback for this call: the armed turn's (resolved once at the execution seam)
    or, for a call outside a turn, the profile's own `llmFallbackConfigurationId` — never
    anything else (an unsigned fallback is a silent widening; the policy refuses it)."""
    try:
        fb = _resilience_envelope.fallback_for_turn()
        if fb:
            return fb
        if active_profile_id and user_uuid:
            from trusted_data_agent.core.config_manager import get_config_manager
            return _resilience_envelope.resolve_fallback(get_config_manager().get_profile(active_profile_id, user_uuid), user_uuid)
    except Exception as e:  # noqa: BLE001
        app_logger.debug(f"[resilience] fallback lookup skipped: {e}")
    return None


async def _fallback_client(fb: dict, user_uuid):
    """A client for the fallback configuration — the executor's own credential resolution
    (the global credential store for the provider, merged with the configuration's own keys,
    which is where OpenRouter-style providers keep theirs)."""
    from trusted_data_agent.llm.client_factory import get_or_create_llm_client
    credentials = {}
    try:
        from trusted_data_agent.core.configuration_service import retrieve_credentials_for_provider
        credentials = (await retrieve_credentials_for_provider(user_uuid, fb["provider"])).get("credentials", {}) or {}
    except Exception:  # noqa: BLE001
        credentials = {}
    credentials = {**credentials, **(fb.get("credentials") or {})}
    return await get_or_create_llm_client(fb["provider"], fb["model"], credentials)


async def _call_fallback(fb: dict, prompt: str, **kw):
    """Serve the call from the signed fallback: the same `call_llm_api`, depth 1 (a fallback
    never falls back), so the returned provider/model — and therefore the price — are the
    model that actually served."""
    client = await _fallback_client(fb, kw.get("user_uuid"))
    app_logger.warning(f"[resilience] serving from the signed fallback {fb['provider']}/{fb['model']}")
    return await call_llm_api(client, prompt, current_provider=fb["provider"], current_model=fb["model"], _fallback_depth=1, **kw)


async def call_llm_api(llm_instance: any, prompt: str, user_uuid: str = None, session_id: str = None, chat_history=None, raise_on_error: bool = False, system_prompt_override: str = None, dependencies: dict = None, reason: str = "No reason provided.", disabled_history: bool = False, active_prompt_name_for_filter: str = None, source: str = "text", active_profile_id: str = None, current_provider: str = None, current_model: str = None, multimodal_content: list = None, planning_phase: str = None, thinking_budget: int = None, strip_capabilities: bool = False, on_delta=None, cache_prefix: str = None, _fallback_depth: int = 0) -> tuple[str, int, int, str, str]: # Added provider, model, planning_phase, and thinking_budget parameters
# --- MODIFICATION END ---
    if not llm_instance:
        raise RuntimeError("LLM is not initialized.")

    # The Governed Stream (S1): `on_delta` is a `streaming.DeltaSink` (or a bare callable).
    # When given, each provider branch streams its answer through the sink and returns the
    # SAME response shape it returns un-streamed; `_streamed` records whether the sink has
    # already seen the text so a provider with no streaming API can hand it over ONCE at
    # the end instead (honest degradation — every byte still passes through the sink).
    _sink = _streaming.as_sink(on_delta)
    _streamed = False

    # Use per-profile provider/model when passed, falling back to global config.
    # This prevents cross-profile contamination when the active profile uses a different
    # LLM provider than the global default (e.g., RAG profile using Friendli while default is Google).
    effective_provider = current_provider if current_provider else APP_CONFIG.CURRENT_PROVIDER
    effective_model = current_model if current_model else APP_CONFIG.CURRENT_MODEL

    # --- Deferred LLM client creation ("the client will be created on first message") ---
    # switch_profile_context(validate_llm=False) parks a placeholder dict
    # ({"placeholder": True, provider, model}) in APP_STATE['llm'] and defers real client creation
    # to the first message — but that lazy creation was never implemented, so the placeholder
    # reached the provider call and crashed with 'dict object has no attribute chat'. Implement the
    # deferred creation here, at the ONE universal LLM chokepoint, so it covers every path (normal +
    # @TAG override, every provider). Uses effective_provider/model so the created client matches the
    # branch that consumes it. On failure — most importantly NO CREDENTIALS configured — it surfaces
    # a clear, actionable error (logged, and raised when raise_on_error) instead of the cryptic crash.
    if isinstance(llm_instance, dict) and llm_instance.get("placeholder"):
        try:
            llm_instance = await _materialize_placeholder_llm(
                llm_instance, user_uuid, effective_provider, effective_model)
        except Exception as _mat_err:
            app_logger.error(f"Deferred LLM client creation failed: {_mat_err}")
            if raise_on_error:
                raise
            # Graceful contract (raise_on_error=False): return empty, exactly as a failed provider
            # call does. The clear reason is in the log; callers that treat empty as fatal (e.g. the
            # planner) surface an honest turn error.
            return "", 0, 0, effective_provider, effective_model

    response_text = ""
    input_tokens, output_tokens = 0, 0
    is_session_name_call = reason and "session name" in reason.lower()

    max_retries = APP_CONFIG.LLM_API_MAX_RETRIES
    base_delay = APP_CONFIG.LLM_API_BASE_DELAY
    # S4 The Resilience Envelope: under the master switch every failure is CLASSIFIED (retry only
    # what a retry can change, honour the provider's retry-after), a truncated answer is CONTINUED
    # (bounded), an exhausted primary is served by the profile's SIGNED fallback (recorded, priced
    # at the model that served), a refusal is an honest answer. Off ⇒ the loop below is the
    # pre-S4 loop byte for byte (the freeze pins it).
    _res_on = _resilience_enabled()

    def _attempt_limit():
        # The silence limit on ONE attempt, inside the envelope's own loop: a provider that stops
        # sending is cut here as a classifiable `timeout` the envelope retries or serves from the
        # signed fallback (recorded as an `llm_recovery` step) — instead of the executor cutting
        # the whole call from outside, where the envelope never sees it. Off ⇒ no limit here
        # (the executor's whole-call limit stays the only one, exactly as before).
        if not _res_on:
            return contextlib.nullcontext()
        return _streaming.activity_limit(getattr(APP_CONFIG, "LLM_CALL_TIMEOUT_SECONDS", 0) or 0,
                                         getattr(APP_CONFIG, "LLM_STREAM_MAX_SECONDS", 0) or 0)
    _model_label = _res.model_label(effective_provider, effective_model)
    _fallback_cfg = None
    if _res_on and _fallback_depth == 0:
        _fallback_cfg = _fallback_for_call(active_profile_id, user_uuid)
        if not _provider_health.allows(effective_provider):
            # the breaker is OPEN (or a probe is already in flight): route to the fallback NOW
            # instead of paying the timeout; without a fallback the call is still attempted —
            # once, no backoff (a breaker never refuses service for lack of an alternative).
            if _fallback_cfg:
                _b = _provider_health.get(effective_provider).snapshot()
                _resilience_envelope.record(_res.RecoveryStep(
                    kind=_res.STEP_FALLBACK, reason="breaker_open", from_model=_model_label,
                    to_model=_res.model_label(_fallback_cfg["provider"], _fallback_cfg["model"]),
                    detail=f"{effective_provider} breaker {_b.get('state')} ({_b.get('last_reason') or 'failures'})"))
                return await _call_fallback(_fallback_cfg, prompt, user_uuid=user_uuid, session_id=session_id, chat_history=chat_history,
                                            raise_on_error=raise_on_error, system_prompt_override=system_prompt_override, dependencies=dependencies,
                                            reason=reason, disabled_history=disabled_history, active_prompt_name_for_filter=active_prompt_name_for_filter,
                                            source=source, active_profile_id=active_profile_id, multimodal_content=multimodal_content,
                                            planning_phase=planning_phase, thinking_budget=thinking_budget, strip_capabilities=strip_capabilities, on_delta=on_delta,
                                            cache_prefix=cache_prefix)
            max_retries = 1
        elif _provider_health.get(effective_provider).snapshot().get("state") == _provider_health.HALF_OPEN:
            # HALF_OPEN: this call IS the probe — one attempt decides whether the breaker
            # closes or re-opens; three attempts would be three probes wearing one lease.
            max_retries = 1
    # --- MODIFICATION START: Pass user_uuid to get_session ---
    session_data = await get_session(user_uuid, session_id) if user_uuid and session_id else None
    # --- MODIFICATION END ---
    LAST_CALL_USAGE.set({})
    system_prompt = _get_full_system_prompt(session_data, dependencies, system_prompt_override, active_prompt_name_for_filter, source, active_profile_id, current_provider, user_uuid=user_uuid, strip_capabilities=strip_capabilities)
    _cache_pol = _resolve_cache_for_call(active_profile_id, user_uuid, effective_provider, effective_model)
    _prefix_hash = None
    # The cache plan (PROMPT_CACHE_ARCHITECTURE.md): the caller's declared stable prefix is
    # honoured only when it IS a prefix of this prompt and caching is on; the agent's
    # affinity key rides only providers that route by one. Off ⇒ every request byte-identical.
    _stable = cache_prefix if (_cache_pol["enabled"] and _cache_policy.stable_split(prompt, cache_prefix)) else None
    _affinity = (_cache_policy.affinity_fields(effective_provider, effective_model,
                                               _cache_policy.affinity_key(user_uuid, active_profile_id), _AFFINITY_REFUSED)
                 if _cache_pol["enabled"] else {})

    history_for_log_str = "No history available."
    history_source = [] # Initialize history source
    if session_data:
        if not disabled_history:
            # --- MODIFICATION START: Use session_data['chat_object'] for history if available ---
            # Prioritize explicitly passed chat_history if present
            history_source = chat_history if chat_history is not None else session_data.get('chat_object', [])
            # Ensure history_source is a list
            if not isinstance(history_source, list):
                 app_logger.warning(f"History source for {effective_provider} was not a list, resetting. Type: {type(history_source)}")
                 history_source = []
            # --- MODIFICATION END ---

        # --- MODIFICATION START: Handle Google history logging explicitly ---
        if effective_provider == "Google" and isinstance(history_source, list) and history_source and hasattr(history_source[0], 'role'):
             # Assume Google's genai history object list
             normalized_history_for_log = [
                 {'role': msg.role, 'content': msg.parts[0].text if msg.parts and hasattr(msg.parts[0], 'text') else '[Content missing]'} for msg in history_source
             ]
             history_json_obj = {"chat_history": normalized_history_for_log}
        elif isinstance(history_source, list):
             # Assume list of dicts for other providers
             history_json_obj = {"chat_history": history_source}
        else:
             history_json_obj = {"chat_history": []}
        # --- MODIFICATION END ---
        history_for_log_str = json.dumps(history_json_obj, indent=2)


    full_log_message = (
        # --- MODIFICATION START: Include user_uuid in log ---
        f"--- FULL CONTEXT (User: {user_uuid}, Session: {session_id or 'one-off'}) ---\n"
        # --- MODIFICATION END ---
        f"--- REASON FOR CALL ---\n{reason}\n\n"
        f"--- History (History Disabled for LLM Call: {disabled_history}) ---\n{history_for_log_str}\n\n"
        f"--- Current User Prompt (with System Prompt) ---\n"
        f"SYSTEM PROMPT:\n{system_prompt}\n\n"
        f"USER PROMPT:\n{prompt}\n"
    )
    llm_history_logger.info(full_log_message)


    _conts = 0                # S4: continuations asked so far (bounded)
    _parts: list = []         # S4: the partials a continuation extends
    _continuing = False       # S4: the next attempt-0 extends a partial — never reset the sink
    _finish = None            # S4: the provider's finish/stop reason of the LAST completion
    _res_exc = None           # S4: the classified error that ended the attempts
    while True:
      for attempt in range(max_retries):
        if attempt > 0 and _sink is not None:
            _sink.reset()   # a retry after an overload error must withdraw what attempt N delivered
            _streamed = False
        _finish = None
        try:
            async with _attempt_limit():
                # The test server's fire-drill switch (llm/fault_injection.py; OFF unless
                # TDA_FAULT_INJECT=1): inside the try, so the injected fault is retried,
                # breaker-counted and handed to the fallback exactly like a real one.
                _fault_injection.check(prompt, system_prompt, chat_history)
                if effective_provider == "Google":
                    # --- Native multimodal for Google (Gemini) ---
                    if multimodal_content:
                        try:
                            # Build parts list: text + file parts
                            parts = [prompt]
                            for block in multimodal_content:
                                file_bytes = _block_bytes(block)
                                parts.append(genai_types.Part.from_bytes(data=file_bytes, mime_type=block["mime_type"]))
                                app_logger.info(f"[Multimodal/Google] Added {block['type']} part: {block['filename']} ({block['mime_type']})")

                            google_mm_config = genai_types.GenerateContentConfig(max_output_tokens=APP_CONFIG.LLM_MAX_OUTPUT_TOKENS)
                            # Prepend system prompt for non-session multimodal calls
                            if system_prompt:
                                parts.insert(0, system_prompt)
                            response = await llm_instance.aio.models.generate_content(
                                model=effective_model, contents=parts, config=google_mm_config
                            )

                            try:
                                mm_text = response.text if response else None
                            except (ValueError, AttributeError):
                                mm_text = None
                            if not mm_text:
                                error_detail = "empty response" if not response else "response missing text content"
                                raise RuntimeError(f"Google multimodal LLM returned an invalid response ({error_detail})")

                            response_text = _sanitize_llm_output(mm_text)
                            if hasattr(response, 'usage_metadata'):
                                usage = response.usage_metadata
                                input_tokens = getattr(usage, 'prompt_token_count', 0)
                                output_tokens = getattr(usage, 'candidates_token_count', 0)
                            app_logger.info(f"[Multimodal/Google] Success: {input_tokens} in / {output_tokens} out tokens")
                            break  # Exit retry loop on success
                        except Exception as mm_err:
                            app_logger.warning(f"Native multimodal failed for Google, falling back to text: {mm_err}")
                            multimodal_content = None
                            # Fall through to existing text-only code path

                    # NOTE: `chat_object` is always a serializable list of dicts — a live
                    # ChatSession object is never stored in session state — so Google always
                    # takes the stateless generate_content path below. Conversation history is
                    # assembled upstream into `prompt` by the context-window layer; the system
                    # prompt is concatenated in (matching prior behavior).

                    # --- Set max_output_tokens to prevent truncation (consistent with other providers) ---
                    # Use low token limit for session name generation (3-5 word title)
                    gen_config_kwargs = {"max_output_tokens": 100 if is_session_name_call else APP_CONFIG.LLM_MAX_OUTPUT_TOKENS}
                    # ThinkingConfig is only supported on Gemini 2.5+ models (not 2.0)
                    model_name = effective_model or ''
                    _ver_match = re.search(r'gemini-(\d+(?:\.\d+)?)', model_name)
                    is_thinking_capable = bool(_ver_match and float(_ver_match.group(1)) >= 2.5 and hasattr(genai_types, 'ThinkingConfig'))
                    # Resolve the budget we WANT, then gate it once. A session-name call
                    # never carries the user's budget — a 3-5 word title needs no thinking.
                    desired_budget = None
                    if is_thinking_capable:
                        if is_session_name_call:
                            desired_budget = 0
                        elif thinking_budget is not None:
                            desired_budget = thinking_budget
                    # Some models refuse a DISABLED budget outright (400 INVALID_ARGUMENT) and
                    # the capability is not discoverable, so it is learned from the refusal
                    # below. Once learned, never send the doomed argument again.
                    if desired_budget == 0 and not thinking_support.supports_disabled_thinking(model_name):
                        app_logger.debug(f"[ThinkingBudget] Omitting the disabled thinking budget for {model_name} — it refuses one")
                        desired_budget = None
                        # The ceiling above was sized for an answer alone; the model will
                        # now also reason inside it.
                        gen_config_kwargs = thinking_support.with_thinking_headroom(gen_config_kwargs)
                    if desired_budget is not None:
                        gen_config_kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_budget=desired_budget)
                        app_logger.debug(f"[ThinkingBudget] Applying thinking_budget={desired_budget} for model {model_name}")

                    app_logger.debug("Google API Call: Using models.generate_content with full prompt.")
                    full_prompt_for_api = f"{system_prompt}\n\n{prompt}"
                    response = await _google_generate_content(
                        llm_instance, effective_model, full_prompt_for_api, gen_config_kwargs, sink=_sink
                    )
                    _streamed = _streaming.was_streamed(response)

                    # --- Debugging: Log raw response object ---
                    app_logger.debug(f"RAW LLM Response Object (Google): {pprint.pformat(response)}")
                    # --- End Debugging ---

                    if not response:
                        app_logger.error("Google LLM returned an empty response object.")
                        raise RuntimeError("Google LLM returned an invalid response (empty response). Check logs for safety ratings.")

                    # --- Extract finish_reason FIRST so we can report it on failure. ---
                    # Google's known finish_reason values: STOP (1), MAX_TOKENS (2), SAFETY (3),
                    # RECITATION (4), OTHER (5)... Newer Gemini models can emit values this SDK
                    # version does not recognize (e.g. 19 on gemini-3.1-*), which surface as an
                    # "Unrecognized FinishReason enum value" warning and an empty candidate.
                    finish_reason = None
                    finish_reason_name = "UNKNOWN"
                    candidate = None
                    if getattr(response, 'candidates', None):
                        candidate = response.candidates[0]
                        if hasattr(candidate, 'finish_reason'):
                            finish_reason = candidate.finish_reason
                            # Convert enum to string for logging (unrecognized values stringify to their int)
                            finish_reason_name = getattr(finish_reason, 'name', str(finish_reason))

                    app_logger.debug(f"Google API finish_reason: {finish_reason_name}")
                    _finish = finish_reason_name
                    if _res_on and _res.finish_kind(finish_reason_name) == _res.REFUSED:
                        raise _res.ProviderRefusal(_res.refusal_category(finish_reason_name), "Google", effective_model,
                                                   str(getattr(candidate, 'safety_ratings', '') or '')[:200] if candidate else "")

                    # Warn if the response was terminated for non-STOP reasons.
                    if finish_reason_name not in ["STOP", "UNKNOWN", "FinishReason.STOP"]:
                        if finish_reason_name in ["SAFETY", "FinishReason.SAFETY"]:
                            safety_ratings = getattr(candidate, 'safety_ratings', []) if candidate else []
                            app_logger.warning(f"Google API response blocked by SAFETY filter. Safety ratings: {safety_ratings}")
                        elif finish_reason_name in ["RECITATION", "FinishReason.RECITATION"]:
                            app_logger.warning(f"Google API response blocked due to RECITATION (potential copyrighted content).")
                        elif finish_reason_name in ["MAX_TOKENS", "FinishReason.MAX_TOKENS"]:
                            app_logger.warning(f"Google API response truncated due to MAX_TOKENS limit.")
                        else:
                            app_logger.warning(f"Google API response terminated with unexpected finish_reason: {finish_reason_name}")

                    # --- Safely pull the text. The `.text` quick-accessor raises ValueError
                    #     (NOT AttributeError, so `hasattr` does not catch it) when the candidate
                    #     has no valid Part. Guard it and fall back to reading parts directly, so
                    #     an empty/blocked completion degrades cleanly instead of crashing the
                    #     caller (e.g. session-name generation). ---
                    response_text = None
                    try:
                        response_text = response.text
                    except (ValueError, AttributeError) as text_err:
                        app_logger.debug(f"Google `.text` accessor unavailable: {text_err}")
                        parts = getattr(getattr(candidate, 'content', None), 'parts', None) if candidate else None
                        if parts:
                            response_text = "".join(getattr(p, 'text', '') or '' for p in parts)

                    if not response_text:
                        safety_ratings = getattr(candidate, 'safety_ratings', 'N/A') if candidate else 'N/A'
                        app_logger.error(
                            f"Google LLM returned no usable text (finish_reason={finish_reason_name}). "
                            f"Safety ratings: {safety_ratings}"
                        )
                        raise RuntimeError(
                            f"Google LLM returned an invalid response (no content; finish_reason={finish_reason_name})."
                        )

                    response_text = _sanitize_llm_output(response_text)


                    if hasattr(response, 'usage_metadata'):
                        usage = response.usage_metadata
                        input_tokens = getattr(usage, 'prompt_token_count', 0) # Use getattr for safety
                        output_tokens = getattr(usage, 'candidates_token_count', 0) # Use getattr for safety
                        # Gemini implicit caching: the cached prefix is billed at a fraction of
                        # the input price. Surface it so cost accounting and the Live Status
                        # step can see it (it was silently billed at full price before).
                        _cached = getattr(usage, 'cached_content_token_count', 0) or 0
                        if _cached:
                            app_logger.info(f"Google usage: {input_tokens} prompt tokens, {_cached} served from implicit cache")
                        # S2: the prefix identity is the system prompt + the history the call carried
                        _prefix_hash = _cache_policy.prefix_hash(system_prompt, [m for m in (history_source or []) if isinstance(m, dict)])
                        _record_cache_reading("Google", effective_model,
                                              {"input": input_tokens, "output": output_tokens, "cached": int(_cached), "written": 0},
                                              _prefix_hash, _cache_pol)

                    break # Exit retry loop on success

                elif effective_provider in ["Anthropic", "OpenAI", "Azure", "Ollama", "Friendli", "OpenRouter", "XAI", "Celeris", "MLX"]:
                    # --- MODIFICATION START: Use history_source consistently ---
                    current_history = []
                    if not disabled_history:
                        current_history = history_source # Use the already prepared history_source

                    if APP_CONFIG.CONDENSE_SYSTEMPROMPT_HISTORY:
                        # Apply condensation if needed (might be redundant if history_source was already condensed)
                        current_history = _condense_and_clean_history(current_history, provider=effective_provider)
                    # --- MODIFICATION END ---

                    # --- MODIFICATION START: Ensure roles are 'user' or 'assistant' for Anthropic/OpenAI/Azure/Friendli/Ollama ---
                    messages_for_api = []
                    for msg in current_history:
                        role = msg.get('role')
                        content = msg.get('content')
                        if role == 'model': role = 'assistant' # Normalize role
                        if role in ['user', 'assistant'] and content is not None:
                            messages_for_api.append({'role': role, 'content': content})
                        else:
                            app_logger.warning(f"Skipping history message with invalid role ('{role}') or missing content for {effective_provider}.")
                    # --- MODIFICATION END ---

                    if effective_provider == "Anthropic":
                        # --- Native multimodal for Anthropic ---
                        if multimodal_content:
                            try:
                                content_blocks = [{"type": "text", "text": prompt}]
                                for block in multimodal_content:
                                    b64 = base64.b64encode(_block_bytes(block)).decode("utf-8")
                                    if block["type"] == "image":
                                        content_blocks.append({
                                            "type": "image",
                                            "source": {"type": "base64", "media_type": block["mime_type"], "data": b64}
                                        })
                                    else:  # document (PDF)
                                        content_blocks.append({
                                            "type": "document",
                                            "source": {"type": "base64", "media_type": block["mime_type"], "data": b64}
                                        })
                                    app_logger.info(f"[Multimodal/Anthropic] Added {block['type']} block: {block['filename']} ({block['mime_type']})")
                                messages_for_api.append({"role": "user", "content": content_blocks})
                            except Exception as mm_err:
                                app_logger.warning(f"Native multimodal failed for Anthropic, falling back to text: {mm_err}")
                                multimodal_content = None
                                messages_for_api.append({'role': 'user', 'content': prompt})
                        else:
                            messages_for_api.append({'role': 'user', 'content': prompt})

                        # S2 The Attested Cache: with caching on, the stable prefix — the system prompt
                        # and the history BEFORE the volatile question — carries explicit breakpoints so
                        # the next call (the next turn) reads it at the cached rate. Off ⇒ the plain
                        # string / plain messages exactly as before.
                        _history_part = messages_for_api[:-1]
                        _use_bp = _cache_pol["enabled"] and _cache_pol["capability"] == _cache_policy.BREAKPOINTS
                        _stable_a = _stable if (_use_bp and not multimodal_content) else None
                        _prefix_hash = _cache_policy.prefix_hash(system_prompt, _history_part, _stable_a)

                        def _anthropic_question(ttl):
                            # The question's declared stable prefix is its own cached block.
                            return [{"role": "user", "content": _cache_policy.mark_stable_blocks(prompt, _stable_a, ttl)}] if _stable_a else messages_for_api[-1:]
                        _system_arg = (_cache_policy.system_blocks(system_prompt, _cache_pol["ttl"]) or system_prompt) if _use_bp else system_prompt
                        _messages_arg = (_cache_policy.mark_last_message(_history_part, _cache_pol["ttl"]) + _anthropic_question(_cache_pol["ttl"])) if _use_bp else messages_for_api
                        _anthropic_kw = dict(model=effective_model, system=_system_arg, messages=_messages_arg,
                                             max_tokens=100 if is_session_name_call else APP_CONFIG.LLM_MAX_OUTPUT_TOKENS, timeout=120.0)

                        async def _anthropic_call(kw):
                            if _sink is not None:
                                return await _streaming.anthropic_stream(llm_instance, _sink, **kw)
                            return await llm_instance.messages.create(**kw)
                        try:
                            response = await _anthropic_call(_anthropic_kw)
                        except Exception as _cc_err:
                            # A provider that refuses the 1h TTL is learned from its own error: retry
                            # once at the default TTL and remember (never a hardcoded model list).
                            if not (_use_bp and _cache_pol["ttl"] == "1h" and "ttl" in str(_cc_err).lower()):
                                raise
                            _NO_TTL_1H.add((effective_provider, effective_model)); _cache_pol["ttl"] = "5m"
                            app_logger.info(f"[cache] {effective_provider}/{effective_model} refused ttl=1h — using 5m from now on")
                            if _sink is not None:
                                _sink.reset()
                            _anthropic_kw["system"] = _cache_policy.system_blocks(system_prompt, "5m") or system_prompt
                            _anthropic_kw["messages"] = _cache_policy.mark_last_message(_history_part, "5m") + _anthropic_question("5m")
                            response = await _anthropic_call(_anthropic_kw)
                        _streamed = _sink is not None
                        _finish = getattr(response, "stop_reason", None)
                        if _res_on and _res.finish_kind(_finish) == _res.REFUSED:
                            raise _res.ProviderRefusal(_res.refusal_category(_finish), "Anthropic", effective_model)
                        # --- Debugging: Log raw response object ---
                        app_logger.debug(f"RAW LLM Response Object (Anthropic): {pprint.pformat(response.dict())}")
                        # --- End Debugging ---
                        raw_text = response.content[0].text if response.content else ""
                        response_text = _sanitize_llm_output(raw_text)
                        # Guard against usage being None: never discard a valid answer
                        # over missing token metadata — default the counts instead.
                        _usage = getattr(response, 'usage', None)
                        if _usage is not None:
                            # Anthropic's input_tokens EXCLUDES the cached prefix — normalise to the TOTAL
                            # prompt so accounting reads the same quantity on every provider.
                            _un = _cache_policy.read_usage("Anthropic", _usage)
                            input_tokens, output_tokens = _un["input"], _un["output"]
                            _record_cache_reading("Anthropic", effective_model, _un, _prefix_hash, _cache_pol)

                    elif effective_provider in ["OpenAI", "Azure", "Friendli", "OpenRouter", "XAI", "Celeris", "MLX"]:
                        # --- Native multimodal for OpenAI/Azure (images only) ---
                        if multimodal_content and effective_provider in ["OpenAI", "Azure"]:
                            try:
                                content_blocks = [{"type": "text", "text": prompt}]
                                for block in multimodal_content:
                                    if block["type"] == "image":
                                        b64 = base64.b64encode(_block_bytes(block)).decode("utf-8")
                                        content_blocks.append({
                                            "type": "image_url",
                                            "image_url": {"url": f"data:{block['mime_type']};base64,{b64}", "detail": "auto"}
                                        })
                                        app_logger.info(f"[Multimodal/OpenAI] Added image block: {block['filename']} ({block['mime_type']})")
                                    # Non-image files (PDFs) are ignored for OpenAI - they use text fallback
                                messages_for_api.append({"role": "user", "content": content_blocks})
                            except Exception as mm_err:
                                app_logger.warning(f"Native multimodal failed for OpenAI/Azure, falling back to text: {mm_err}")
                                multimodal_content = None
                                messages_for_api.append({'role': 'user', 'content': prompt})
                        else:
                            messages_for_api.append({'role': 'user', 'content': prompt})

                        # Prepend system prompt for these providers
                        messages_for_api.insert(0, {'role': 'system', 'content': system_prompt})
                        # S2 — the prefix is hashed over the PLAIN messages (system + history, never the
                        # volatile question), before any breakpoint marking; a broker fronting an
                        # Anthropic model (OpenRouter) takes the same explicit cache_control blocks
                        # the native Anthropic branch places — capability says breakpoints, so the
                        # branch marks them (a capability claimed and never placed is a silent miss).
                        _use_bp_o = _cache_pol["enabled"] and _cache_pol["capability"] == _cache_policy.BREAKPOINTS
                        _stable_o = _stable if (_use_bp_o and isinstance(messages_for_api[-1].get("content"), str)) else None
                        _prefix_hash = _cache_policy.prefix_hash(system_prompt, messages_for_api[1:-1], _stable_o)
                        if _use_bp_o:
                            messages_for_api = _cache_policy.mark_openai_messages(messages_for_api, _cache_pol["ttl"])
                            if _stable_o:
                                messages_for_api[-1] = {**messages_for_api[-1],
                                                        "content": _cache_policy.mark_stable_blocks(prompt, _stable_o, _cache_pol["ttl"])}
                        _max_out = 100 if is_session_name_call else APP_CONFIG.LLM_MAX_OUTPUT_TOKENS
                        # A hosted API answers within 120 s; a LOCAL server (MLX) generates at
                        # single-digit tokens/s, so its call runs as long as its client allows.
                        _call_timeout = 120.0
                        if effective_provider == "MLX":
                            _ct = getattr(llm_instance, "timeout", None)
                            if isinstance(_ct, (int, float)):
                                _call_timeout = float(_ct)
                            elif getattr(_ct, "read", None):
                                _call_timeout = float(_ct.read)
                        # Newer OpenAI models (gpt-5.x+) require max_completion_tokens; older ones use max_tokens.
                        _uses_completion_tokens = (effective_provider in ("OpenAI", "Azure")) and ("gpt-5" in effective_model or "5.4" in effective_model)
                        _token_kwargs = {"max_completion_tokens": _max_out} if _uses_completion_tokens else {"max_tokens": _max_out}
                        async def _openai_call(affinity):
                            _kw = dict(model=effective_model, messages=messages_for_api, timeout=_call_timeout,
                                       **_token_kwargs, **affinity.get("kwargs", {}))
                            if affinity.get("extra_body"):
                                _kw["extra_body"] = affinity["extra_body"]
                            if affinity.get("extra_headers"):
                                _kw["extra_headers"] = affinity["extra_headers"]
                            if _sink is not None:
                                return await _streaming.openai_compatible_stream(
                                    llm_instance, _sink, provider=effective_provider, **_kw)
                            return await llm_instance.chat.completions.create(**_kw)
                        try:
                            response = await _openai_call(_affinity)
                        except Exception as _af_err:
                            # A provider/gateway that refuses the affinity field is learned from its own
                            # error: retry once without it and never send it again (no model list).
                            if not (_affinity and _cache_policy.affinity_refused(effective_provider, _af_err)):
                                raise
                            _AFFINITY_REFUSED.add((effective_provider, effective_model))
                            app_logger.info(f"[cache] {effective_provider}/{effective_model} refused the cache-affinity field — not sending it from now on")
                            if _sink is not None:
                                _sink.reset()
                            response = await _openai_call({})
                        _streamed = _sink is not None
                        # --- Debugging: Log raw response object ---
                        app_logger.debug(f"RAW LLM Response Object (OpenAI/Azure/Friendli): {pprint.pformat(response.dict())}")
                        # --- End Debugging ---
                        raw_text = response.choices[0].message.content if response.choices else ""
                        _finish = getattr(response.choices[0], "finish_reason", None) if response.choices else None
                        if _res_on and _res.finish_kind(_finish) == _res.REFUSED:
                            raise _res.ProviderRefusal(_res.refusal_category(_finish), effective_provider, effective_model)
                        response_text = _sanitize_llm_output(raw_text)
                        # Guard against usage being None (OpenRouter returns usage=None
                        # on some routed/transient responses): the unguarded read used
                        # to crash AFTER raw_text was extracted, discarding a valid
                        # answer over missing token metadata. Default the counts instead.
                        _usage = getattr(response, 'usage', None)
                        if _usage is not None:
                            _un = _cache_policy.read_usage(effective_provider, _usage)
                            input_tokens, output_tokens = _un["input"], _un["output"]
                            _record_cache_reading(effective_provider, effective_model, _un, _prefix_hash, _cache_pol)

                    elif effective_provider == "Ollama":
                        messages_for_api.append({'role': 'user', 'content': prompt})
                        response = await llm_instance.chat(
                            model=effective_model, messages=messages_for_api, system_prompt=system_prompt,
                            on_delta=_sink
                        )
                        _streamed = _streaming.was_streamed(response)
                        # --- Debugging: Log raw response object ---
                        app_logger.debug(f"RAW LLM Response Object (Ollama): {pprint.pformat(response)}")
                        # --- End Debugging ---
                        raw_text = response.get("message", {}).get("content", "")
                        _finish = response.get("done_reason")
                        response_text = _sanitize_llm_output(raw_text)
                        input_tokens, output_tokens = response.get('prompt_eval_count', 0), response.get('eval_count', 0)
                        _record_cache_reading("Ollama", effective_model, {"input": input_tokens, "output": output_tokens, "cached": 0, "written": 0}, None, _cache_pol)

                    break # Exit retry loop on success

                elif effective_provider == "Amazon":
                    # --- MODIFICATION START: Use history_source consistently ---
                    current_history = []
                    if not disabled_history:
                        current_history = history_source # Use the already prepared history_source

                    if APP_CONFIG.CONDENSE_SYSTEMPROMPT_HISTORY:
                        # Apply condensation if needed
                        current_history = _condense_and_clean_history(current_history, provider=effective_provider)
                    # --- MODIFICATION END ---

                    model_id_to_invoke = effective_model
                    is_inference_profile = model_id_to_invoke.startswith("arn:aws:bedrock:")

                    bedrock_provider = ""
                    if is_inference_profile:
                        # Determine provider from ARN — prefer the USER-KEYED value (isolates concurrent
                        # Bedrock users), falling back to the process-global for backward compatibility,
                        # then "unknown". Previously read only the shared global → a concurrent turn's
                        # Bedrock ARN provider could bleed across users.
                        _user_bedrock_provider = None
                        if user_uuid:
                            try:
                                _user_bedrock_provider = get_user_model_provider_in_profile(user_uuid)
                            except Exception:
                                _user_bedrock_provider = None
                        bedrock_provider = _user_bedrock_provider or APP_CONFIG.CURRENT_MODEL_PROVIDER_IN_PROFILE or "unknown"
                        if bedrock_provider == "unknown":
                            app_logger.warning("Could not determine Bedrock provider from Inference Profile ARN.")
                    else:
                        bedrock_provider = model_id_to_invoke.split('.')[0]

                    app_logger.info(f"Determined Bedrock provider for payload construction: '{bedrock_provider}'")

                    body = ""
                    # --- MODIFICATION START: Ensure roles are 'user' or 'assistant' for Bedrock ---
                    bedrock_messages = []
                    for msg in current_history:
                        role = msg.get('role')
                        content = msg.get('content')
                        if role == 'model': role = 'assistant'
                        if role in ['user', 'assistant'] and content is not None:
                            bedrock_messages.append({'role': role, 'content': content})
                        else:
                            app_logger.warning(f"Skipping history message with invalid role ('{role}') or missing content for Bedrock.")
                    # --- MODIFICATION END ---

                    if bedrock_provider == "anthropic":
                        # --- Native multimodal for Bedrock Anthropic ---
                        if multimodal_content:
                            try:
                                content_blocks = [{"text": prompt}]
                                for block in multimodal_content:
                                    file_bytes = _block_bytes(block)
                                    if block["type"] == "image":
                                        fmt = block["mime_type"].split("/")[-1]
                                        if fmt == "jpeg":
                                            fmt = "jpeg"
                                        content_blocks.append({
                                            "image": {"format": fmt, "source": {"bytes": base64.b64encode(file_bytes).decode("utf-8")}}
                                        })
                                    else:  # document (PDF)
                                        fmt = block["mime_type"].split("/")[-1]
                                        doc_name = block["filename"].rsplit(".", 1)[0][:40]
                                        content_blocks.append({
                                            "document": {"format": fmt, "name": doc_name, "source": {"bytes": base64.b64encode(file_bytes).decode("utf-8")}}
                                        })
                                    app_logger.info(f"[Multimodal/Bedrock] Added {block['type']} block: {block['filename']} ({block['mime_type']})")
                                bedrock_messages.append({"role": "user", "content": content_blocks})
                            except Exception as mm_err:
                                app_logger.warning(f"Native multimodal failed for Bedrock Anthropic, falling back to text: {mm_err}")
                                multimodal_content = None
                                bedrock_messages.append({'role': 'user', 'content': prompt})
                        else:
                            # Add current prompt to messages list
                            bedrock_messages.append({'role': 'user', 'content': prompt})
                        body = json.dumps({
                            "anthropic_version": "bedrock-2023-05-31",
                            "max_tokens": 100 if is_session_name_call else 4096,
                            "system": system_prompt,
                            "messages": bedrock_messages # Use the cleaned messages
                        })
                    elif bedrock_provider == "amazon":
                        # Amazon Titan format (handle different variants)
                        if is_inference_profile or "titan-text-express" not in model_id_to_invoke:
                             # Newer format (potentially for profiles or other titan models)
                             titan_messages = []
                             for msg in bedrock_messages:
                                  titan_messages.append({"role": msg['role'], "content": [{"text": msg['content']}]})
                             titan_messages.append({"role": "user", "content": [{"text": prompt}]})
                             _max_tokens = 100 if is_session_name_call else APP_CONFIG.LLM_MAX_OUTPUT_TOKENS
                             body_dict = {"messages": titan_messages, "inferenceConfig": {"maxTokens": _max_tokens}}
                             if system_prompt:
                                 body_dict["system"] = [{"text": system_prompt}]
                             body = json.dumps(body_dict)
                        else:
                            # Legacy titan-text-express format
                            _max_tokens = 100 if is_session_name_call else APP_CONFIG.LLM_MAX_OUTPUT_TOKENS
                            text_prompt = f"{system_prompt}\n\n" + "".join([f"{msg['role']}: {msg['content']}\n\n" for msg in bedrock_messages]) + f"user: {prompt}\n\nassistant:"
                            body = json.dumps({"inputText": text_prompt, "textGenerationConfig": {"maxTokenCount": _max_tokens}})

                    elif bedrock_provider in ["cohere", "meta", "ai21", "mistral"]:
                        # Format for providers expecting a single text prompt with history
                        _max_tokens = 100 if is_session_name_call else APP_CONFIG.LLM_MAX_OUTPUT_TOKENS
                        _max_gen_len = 100 if is_session_name_call else min(APP_CONFIG.LLM_MAX_OUTPUT_TOKENS, 2048)
                        text_prompt = f"{system_prompt}\n\n" + "".join([f"{msg['role']}: {msg['content']}\n\n" for msg in bedrock_messages]) + f"user: {prompt}\n\nassistant:"

                        if bedrock_provider == "cohere": body_dict = {"prompt": text_prompt, "max_tokens": _max_tokens}
                        elif bedrock_provider == "meta": body_dict = {"prompt": text_prompt, "max_gen_len": _max_gen_len}
                        elif bedrock_provider == "mistral": body_dict = {"prompt": text_prompt, "max_tokens": _max_tokens}
                        else: body_dict = {"prompt": text_prompt, "maxTokens": _max_tokens} # AI21
                        body = json.dumps(body_dict)
                    else:
                        _max_tokens = 100 if is_session_name_call else APP_CONFIG.LLM_MAX_OUTPUT_TOKENS
                        app_logger.warning(f"Unknown Bedrock provider '{bedrock_provider}'. Defaulting to legacy 'inputText' format.")
                        text_prompt = f"{system_prompt}\n\n" + "".join([f"{msg['role']}: {msg['content']}\n\n" for msg in bedrock_messages]) + f"user: {prompt}\n\nassistant:"
                        body = json.dumps({ "inputText": text_prompt, "textGenerationConfig": {"maxTokenCount": _max_tokens} })

                    final_model_id_for_api = _normalize_bedrock_model_id(model_id_to_invoke)
                    app_logger.debug(f"Invoking Bedrock model: {final_model_id_for_api} with body: {body[:200]}...") # Log start of body

                    # The cache plan on Bedrock: Claude takes the Anthropic breakpoints, Nova takes
                    # cachePoint markers; any other family (and an ARN, which names none) is sent
                    # plain. A model that refuses the markers is learned and sent plain from then on.
                    _bedrock_hist = [m for m in bedrock_messages if isinstance(m, dict)]
                    _prefix_hash = _cache_policy.prefix_hash(system_prompt, _bedrock_hist, _stable)
                    _bedrock_marked = None
                    if (_cache_pol["enabled"] and _cache_pol["capability"] == _cache_policy.BREAKPOINTS
                            and (effective_provider, effective_model) not in _CACHE_POINT_REFUSED):
                        try:
                            _bedrock_marked = _cache_policy.mark_bedrock_body(json.loads(body), model_id_to_invoke, system_prompt,
                                                                             _stable, _cache_pol["ttl"])
                        except Exception:  # noqa: BLE001 — a marking problem never costs the call
                            _bedrock_marked = None

                    loop = asyncio.get_running_loop()

                    async def _bedrock_invoke(_body):
                        if _sink is not None and bedrock_provider == "anthropic":
                            # The one Bedrock family with a text-delta stream; the other families take
                            # their non-streaming call and hand the sink the whole text once (below).
                            return await _streaming.bedrock_anthropic_stream(
                                llm_instance, _sink, body=_body, modelId=final_model_id_for_api)
                        return await loop.run_in_executor(None, lambda: llm_instance.invoke_model(body=_body, modelId=final_model_id_for_api))
                    if _bedrock_marked is not None:
                        try:
                            response = await _bedrock_invoke(json.dumps(_bedrock_marked))
                        except Exception as _cp_err:
                            if not _cache_policy.cache_point_refused(_cp_err):
                                raise
                            _CACHE_POINT_REFUSED.add((effective_provider, effective_model))
                            app_logger.info(f"[cache] Bedrock {effective_model} refused the cache markers — sending it plain from now on")
                            if _sink is not None:
                                _sink.reset()
                            response = await _bedrock_invoke(body)
                    else:
                        response = await _bedrock_invoke(body)
                    _streamed = _sink is not None and bedrock_provider == "anthropic"
                    # --- Debugging: Log raw response object ---
                    response_body_raw = response.get('body').read()
                    app_logger.debug(f"RAW LLM Response Object (Bedrock Body): {response_body_raw}")
                    # --- End Debugging ---
                    response_body = json.loads(response_body_raw)

                    raw_text = "" # Initialize raw_text
                    # Extract text based on provider
                    if bedrock_provider == "anthropic":
                        raw_text = response_body.get('content')[0].get('text') if response_body.get('content') else ""
                    elif bedrock_provider == "amazon":
                        if is_inference_profile or "titan-text-express" not in model_id_to_invoke:
                            raw_text = response_body.get('output', {}).get('message', {}).get('content', [{}])[0].get('text', '')
                        else:
                            raw_text = response_body.get('results')[0].get('outputText') if response_body.get('results') else ""
                    elif bedrock_provider == "cohere":
                        raw_text = response_body.get('generations')[0].get('text') if response_body.get('generations') else ""
                    elif bedrock_provider == "meta":
                        raw_text = response_body.get('generation') or ""
                    elif bedrock_provider == "mistral":
                        raw_text = response_body.get('outputs')[0].get('text') if response_body.get('outputs') else ""
                    elif bedrock_provider == "ai21":
                        raw_text = response_body.get('completions')[0].get('data').get('text') if response_body.get('completions') else ""
                    else: # Default/Unknown
                        raw_text = response_body.get('results')[0].get('outputText') if response_body.get('results') else ""

                    response_text = _sanitize_llm_output(raw_text)

                    # Token counts - Extract based on provider response format
                    input_tokens = 0
                    output_tokens = 0
                    _bedrock_cache_read = False   # a family that reports its cache reading records it below
                
                    if bedrock_provider == 'anthropic':
                        # Anthropic Claude: usage.input_tokens EXCLUDES the cached prefix — read as the
                        # TOTAL, like the native branch, and publish the cache reading.
                        _un = _cache_policy.read_usage("Amazon", response_body.get('usage', {}) or {})
                        input_tokens, output_tokens = _un["input"], _un["output"]
                        _record_cache_reading("Amazon", effective_model, _un, _prefix_hash, _cache_pol)
                        _bedrock_cache_read = True
                    
                    elif bedrock_provider == 'amazon':
                        # Amazon Titan/Nova models have two formats:
                        if 'usage' in response_body:
                            # New format (Titan Premier, Nova): usage.inputTokens excludes a cached
                            # prefix (cacheRead/WriteInputTokenCount) — read as the TOTAL.
                            _un = _cache_policy.read_usage("Amazon", response_body.get('usage', {}) or {})
                            input_tokens, output_tokens = _un["input"], _un["output"]
                            _record_cache_reading("Amazon", effective_model, _un, _prefix_hash, _cache_pol)
                            _bedrock_cache_read = True
                        elif 'inputTextTokenCount' in response_body:
                            # Legacy format (Titan Express): inputTextTokenCount, results[0].tokenCount
                            input_tokens = response_body.get('inputTextTokenCount', 0)
                            if response_body.get('results'):
                                output_tokens = response_body['results'][0].get('tokenCount', 0)
                            
                    elif bedrock_provider == 'meta':
                        # Meta Llama: prompt_token_count, generation_token_count
                        input_tokens = response_body.get('prompt_token_count', 0)
                        output_tokens = response_body.get('generation_token_count', 0)
                    
                    # Note: Cohere, Mistral, and AI21 models don't return token counts
                    # For these providers, tokens will remain 0
                
                    if input_tokens > 0 or output_tokens > 0:
                        app_logger.debug(f"Bedrock token usage - Provider: {bedrock_provider}, Input: {input_tokens}, Output: {output_tokens}")
                    else:
                        app_logger.warning(f"No token usage data available for Bedrock provider: {bedrock_provider}")
                    if not _bedrock_cache_read:
                        _record_cache_reading("Amazon", effective_model, {"input": input_tokens, "output": output_tokens, "cached": 0, "written": 0}, None, _cache_pol)

                    break # Exit retry loop on success
                else:
                    raise NotImplementedError(f"Provider '{effective_provider}' is not yet supported.")

        except _res.ProviderRefusal as e:
            # S4: the provider declined on content grounds — terminal, never retried, never
            # handed to a fallback. Recorded; the caller delivers the honest answer.
            _provider_health.record_success(effective_provider)   # the provider is healthy; it declined
            _resilience_envelope.record(_res.RecoveryStep(kind=_res.STEP_REFUSAL, reason=e.category, from_model=_model_label,
                                                          to_model=_model_label, detail=e.detail))
            if raise_on_error: raise
            response_text = ""; break
        # Only the SDKs already loaded: reading the stand-ins here imported anthropic AND openai
        # on the first error of any provider (~1 s after a timeout cut, before the retry).
        except _loaded_attrs("anthropic:InternalServerError", "anthropic:RateLimitError",
                             "openai:APIError") as e:
            if _res_on:
                _res_exc = e
                _cls = _res.classify(e)
                if _cls.retryable:
                    _provider_health.record_failure(effective_provider, _cls.reason)
                if _res.route_to_fallback_now(_cls, bool(_fallback_cfg)):
                    break   # a timeout with a fallback bound: the fallback NOW, not after N more waits
                if _res.should_retry(_cls, attempt + 1, max_retries):
                    delay = _res.backoff(attempt + 1, base=base_delay, retry_after=_cls.retry_after, jitter=random.uniform(0, 1))
                    _resilience_envelope.record(_res.RecoveryStep(kind=_res.STEP_RETRY, reason=_cls.reason, from_model=_model_label,
                                                                  to_model=_model_label, attempt=attempt + 2, delay=delay, status=_cls.status,
                                                                  detail=str(e)[:200]))
                    app_logger.warning(f"[resilience] {_model_label} {_cls.reason}; retrying in {delay:.2f}s (attempt {attempt + 2}/{max_retries})")
                    await asyncio.sleep(delay)
                    continue
                if not _cls.retryable:
                    _record_terminal(effective_provider, e)   # reachable — unless the account is empty
                    if raise_on_error: raise e
                    app_logger.warning(f"[resilience] {_model_label} terminal ({_cls.reason}); raise_on_error=False → empty response"); response_text = ""; break
                break   # retryable but exhausted → the fallback decision below
            if attempt < max_retries - 1:
                delay = (base_delay * (2 ** attempt)) + random.uniform(0, 1)
                app_logger.warning(f"API overloaded or rate limited. Retrying in {delay:.2f}s...")
                await asyncio.sleep(delay)
                continue
            else:
                raise e
        except JSONDecodeError as e:
            app_logger.error(f"Failed to parse JSON response from LLM after sanitization. Error: {e}")
            llm_history_logger.error(f"--- ERROR in LLM JSON parsing ---\n{e}\n" + "-"*50 + "\n")
            if raise_on_error: raise
            else: response_text = ""; break
        except Exception as e:
            if _res_on:
                # S4: classify EVERY error — the SDK families above are not the only way a
                # provider fails (httpx timeouts, boto, google-genai errors); a retryable one
                # is retried on the same schedule, a terminal one fails fast.
                _res_exc = e
                _cls = _res.classify(e)
                if _cls.retryable:
                    _provider_health.record_failure(effective_provider, _cls.reason)
                    if _res.route_to_fallback_now(_cls, bool(_fallback_cfg)):
                        break   # a timeout with a fallback bound: the fallback NOW
                    if _res.should_retry(_cls, attempt + 1, max_retries):
                        delay = _res.backoff(attempt + 1, base=base_delay, retry_after=_cls.retry_after, jitter=random.uniform(0, 1))
                        _resilience_envelope.record(_res.RecoveryStep(kind=_res.STEP_RETRY, reason=_cls.reason, from_model=_model_label,
                                                                      to_model=_model_label, attempt=attempt + 2, delay=delay, status=_cls.status,
                                                                      detail=str(e)[:200]))
                        app_logger.warning(f"[resilience] {_model_label} {_cls.reason}; retrying in {delay:.2f}s (attempt {attempt + 2}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                    break   # exhausted → the fallback decision below
                else:
                    _record_terminal(effective_provider, e)   # a terminal answer: reachable — unless the account is empty
            app_logger.error(f"Error calling LLM API for provider {effective_provider}: {e}", exc_info=True)
            llm_history_logger.error(f"--- ERROR in LLM call ---\n{e}\n" + "-"*50 + "\n")
            if raise_on_error: raise e
            else: app_logger.warning(f"LLM call failed after {attempt+1} attempts but raise_on_error=False. Continuing with empty response."); response_text = ""; break
      # ── S4: after the attempts ───────────────────────────────────────────────────────
      if _res_on and _res_exc is not None and not response_text:
          # the primary could not serve (retryable, exhausted): the SIGNED fallback, or the error
          _cls = _res.classify(_res_exc)
          if _cls.retryable and _fallback_cfg and _fallback_depth == 0:
              _resilience_envelope.record(_res.RecoveryStep(kind=_res.STEP_FALLBACK, reason=_cls.reason, from_model=_model_label,
                                                            to_model=_res.model_label(_fallback_cfg["provider"], _fallback_cfg["model"]),
                                                            status=_cls.status, detail=str(_res_exc)[:200]))
              if _sink is not None:
                  _sink.reset()
              return await _call_fallback(_fallback_cfg, prompt, user_uuid=user_uuid, session_id=session_id, chat_history=chat_history,
                                          raise_on_error=raise_on_error, system_prompt_override=system_prompt_override, dependencies=dependencies,
                                          reason=reason, disabled_history=disabled_history, active_prompt_name_for_filter=active_prompt_name_for_filter,
                                          source=source, active_profile_id=active_profile_id, multimodal_content=multimodal_content,
                                          planning_phase=planning_phase, thinking_budget=thinking_budget, strip_capabilities=strip_capabilities, on_delta=on_delta,
                                            cache_prefix=cache_prefix)
          if _cls.retryable:
              if raise_on_error: raise _res_exc
              app_logger.warning(f"[resilience] {_model_label} exhausted ({_cls.reason}), no fallback; raise_on_error=False → empty response")
              response_text = ""
      if _res_on and response_text:
          _provider_health.record_success(effective_provider)
          if _res.should_continue(_finish, _conts, response_text):
              # the answer stopped at the output limit: ask for the rest, exactly from where it
              # stopped — the partial becomes the assistant's own prior turn; bounded.
              _parts.append(response_text)
              _conts += 1
              _resilience_envelope.record(_res.RecoveryStep(kind=_res.STEP_CONTINUATION, reason=str(getattr(_finish, 'name', None) or _finish),
                                                            from_model=_model_label, to_model=_model_label, attempt=_conts))
              app_logger.info(f"[resilience] {_model_label} truncated ({_finish}); continuation {_conts}/{_res.MAX_CONTINUATIONS}")
              history_source, prompt = _res.continuation(history_source if not disabled_history else [], prompt, response_text)
              disabled_history = False
              multimodal_content = None
              _res_exc = None
              response_text = ""
              _continuing = True
              continue
      break

    if _parts:
        response_text = _res.join_continuation(_parts + [response_text])

    if not response_text and attempt == max_retries - 1 and raise_on_error:
        raise RuntimeError(f"LLM call failed after {max_retries} retries.")

    response_text = _extract_final_answer_from_json(response_text)

    if _sink is not None and not _streamed and response_text:
        # Honest degradation: this provider/path did not stream, so the sink receives the
        # complete answer once, at the end. The consumer's contract holds either way.
        try:
            if hasattr(_sink, "note"):
                _sink.note("provider_no_stream")   # the assurance surface says so, honestly
            _sink.delta(response_text)
        except Exception as _sink_err:  # noqa: BLE001 — never break a turn on a sink
            app_logger.debug(f"[stream] final single delta failed (ignored): {_sink_err}")

    llm_logger.info(f"--- REASON FOR CALL ---\n{reason}\n--- RESPONSE ---\n{response_text}\n" + "-"*50 + "\n")

    # --- MODIFICATION START: Pass user_uuid to update_token_count ---
    if user_uuid and session_id:
        await update_token_count(user_uuid, session_id, input_tokens, output_tokens)
    # --- MODIFICATION END ---

    # Return the effective provider and model used for this call
    actual_provider = effective_provider
    actual_model = effective_model

    # Track model usage with planning_phase (dual-model support)
    if user_uuid and session_id:
        from trusted_data_agent.core.session_manager import update_models_used

        # Determine planning phase if not explicitly provided
        if not planning_phase:
            # Default to "conversation" for non-planning LLM calls
            # (TDA_FinalReport, TDA_LLMTask, self-correction, etc.)
            planning_phase = "conversation"

        await update_models_used(
            user_uuid=user_uuid,
            session_id=session_id,
            provider=actual_provider,
            model=actual_model,
            profile_tag=None,  # Not available at this level
            planning_phase=planning_phase
        )
        app_logger.debug(f"[Model Tracking] Tracked {actual_provider}/{actual_model} for {planning_phase} phase")

    return response_text, input_tokens, output_tokens, actual_provider, actual_model

def _get_recommended_patterns(provider: str) -> list[str]:
    """
    Gets recommended model patterns for a provider from the database.
    Returns a list of pattern strings (may include wildcards).
    """
    try:
        with get_db_session() as session:
            recommended = session.query(RecommendedModel).filter_by(
                provider=provider,
                is_active=True
            ).all()
            return [r.model_pattern for r in recommended]
    except Exception as e:
        app_logger.warning(f"Failed to query recommended models for {provider}: {e}")
        return []


def _is_model_recommended(model_name: str, recommended_list: list[str]) -> bool:
    """
    Checks if a model is in the recommended list, supporting wildcards.
    """
    for pattern in recommended_list:
        regex_pattern = re.escape(pattern).replace('\\*', '.*')
        if re.fullmatch(regex_pattern, model_name):
            return True
    return False


def _get_friendli_serverless_models() -> list[str]:
    """
    Gets active Friendli serverless models from the database.

    Falls back to a minimal hardcoded list if database is empty or unavailable
    (e.g., during fresh install before bootstrap runs).
    """
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import ProviderAvailableModel

        with get_db_session() as session:
            models = session.query(ProviderAvailableModel).filter_by(
                provider='Friendli',
                endpoint_type='serverless',
                is_active=True
            ).filter(
                ProviderAvailableModel.status != 'deprecated'
            ).all()

            if models:
                app_logger.info(f"Loaded {len(models)} Friendli serverless models from database")
                return [m.model_id for m in models]

            # Fallback: Database empty - return minimal list
            app_logger.warning("No Friendli models in database, using fallback list")
            return [
                "meta-llama-3.3-70b-instruct",
                "meta-llama-3.1-8b-instruct",
            ]

    except Exception as e:
        app_logger.warning(f"Failed to query Friendli models from database: {e}, using fallback list")
        # Fallback on error
        return [
            "meta-llama-3.3-70b-instruct",
            "meta-llama-3.1-8b-instruct",
        ]


def _extract_openrouter_pricing(models_data: list) -> list[dict]:
    """
    Map the OpenRouter /models payload to cost entries.

    OpenRouter reports per-token USD prices as strings under
    `pricing.prompt` (input) and `pricing.completion` (output); these are
    converted to per-million tokens keyed by the model slug used at runtime.
    """
    entries = []
    for m in models_data:
        model_id = m.get("id")
        pricing = m.get("pricing") or {}
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        if not model_id or prompt is None or completion is None:
            continue
        try:
            input_pm = float(prompt) * 1_000_000
            output_pm = float(completion) * 1_000_000
        except (TypeError, ValueError):
            continue
        entries.append({
            "model": model_id,
            "input_cost_per_million": input_pm,
            "output_cost_per_million": output_pm,
        })
    return entries


def _extract_friendli_pricing(models_data: list) -> list[dict]:
    """
    Map the Friendli /serverless/v1/models payload to cost entries.

    Friendli reports prices already per 1M tokens (verified against published
    rates) under `pricing.input`/`pricing.output` (with `prompt`/`completion`
    synonyms), so no per-token scaling is applied. Keyed by the model `id` used
    at runtime by the serverless branch of `list_models()`.
    """
    entries = []
    for m in models_data:
        model_id = m.get("id")
        pricing = m.get("pricing") or {}
        inp = pricing.get("input", pricing.get("prompt"))
        outp = pricing.get("output", pricing.get("completion"))
        if not model_id or inp is None or outp is None:
            continue
        try:
            input_pm = float(inp)
            output_pm = float(outp)
        except (TypeError, ValueError):
            continue
        entries.append({
            "model": model_id,
            "input_cost_per_million": input_pm,
            "output_cost_per_million": output_pm,
        })
    return entries


# Registry of providers that expose live pricing alongside their model list.
# Maps the runtime provider name -> an extractor that converts that provider's
# raw model-list payload into cost entries. To add a provider:
#   1. Write an extractor(raw_payload) -> [{model, input_cost_per_million,
#      output_cost_per_million}, ...] and register it here.
#   2. Add the provider's source tag to `LIVE_PRICING_PROVIDERS` in
#      `core/cost_manager.py` (so a LiteLLM sync won't downgrade it).
#   3. Call `_persist_provider_pricing(provider, raw_payload)` from that
#      provider's branch in `list_models()` (refreshes on dropdown open).
# The `url` (public pricing endpoint) lets the cost-sync flow refresh pricing
# directly via `fetch_live_pricing_sync()` — no credentials, no event loop —
# which is the reliable path (the Sync button), independent of the dropdown.
_PROVIDER_PRICING_SPECS = {
    "OpenRouter": {
        "url": "https://openrouter.ai/api/v1/models",
        "data_key": "data",
        "extractor": _extract_openrouter_pricing,
    },
    "Friendli": {
        "url": "https://api.friendli.ai/serverless/v1/models",
        "data_key": "data",
        "extractor": _extract_friendli_pricing,
    },
}


def _persist_provider_pricing(provider: str, models_data: list) -> None:
    """
    Extract and persist live pricing for a provider that exposes a cost API,
    from an already-fetched model-list payload (the `list_models()` path).
    Best-effort: never raises into model listing.
    """
    try:
        spec = _PROVIDER_PRICING_SPECS.get(provider)
        if not spec:
            return
        entries = spec["extractor"](models_data)
        _upsert_live_pricing_entries(provider, entries)
    except Exception as e:
        app_logger.warning(f"Failed to persist {provider} pricing: {e}")


def fetch_live_pricing_sync(provider: str) -> list[dict]:
    """
    Fetch a provider's public pricing endpoint synchronously (no credentials,
    no event loop) and return cost entries. Safe to call from sync code such as
    the cost-sync flow. Returns [] if the provider has no spec or on any error.
    """
    spec = _PROVIDER_PRICING_SPECS.get(provider)
    if not spec:
        return []
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(spec["url"])
            response.raise_for_status()
            payload = response.json().get(spec["data_key"], [])
        return spec["extractor"](payload)
    except Exception as e:
        app_logger.warning(f"Failed to fetch {provider} live pricing: {e}")
        return []


def _upsert_live_pricing_entries(provider: str, entries: list) -> dict:
    """Upsert extracted cost entries for a live-pricing provider. Returns stats."""
    if not entries:
        return {"new": 0, "updated": 0, "skipped": 0}
    from trusted_data_agent.core.cost_manager import (
        get_cost_manager,
        LIVE_PRICING_PROVIDERS,
    )
    source = LIVE_PRICING_PROVIDERS.get(provider)
    if not source:
        app_logger.warning(
            f"{provider} has a pricing spec but no source tag in "
            "LIVE_PRICING_PROVIDERS; skipping pricing persistence."
        )
        return {"new": 0, "updated": 0, "skipped": 0}
    stats = get_cost_manager().upsert_live_costs(provider, source, entries)
    if stats.get("new") or stats.get("updated"):
        app_logger.info(
            f"{provider} pricing sync: {stats['new']} new, "
            f"{stats['updated']} updated, {stats['skipped']} unchanged"
        )
    return stats


async def list_models(provider: str, credentials: dict) -> list[dict]:
    """
    Lists available models for a given provider and checks if they are recommended.
    """
    model_names = []
    # Get recommended patterns from database
    recommended_list = _get_recommended_patterns(provider)

    if provider == "Google":
        api_key = credentials.get("apiKey")
        if not api_key:
            raise ValueError("API key for Google is required.")

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                response.raise_for_status()
                models_data = response.json().get("models", [])

            model_names = [
                model['name'].split('/')[-1]
                for model in models_data
                if 'generateContent' in model.get('supportedGenerationMethods', [])
            ]
        except (httpx.RequestError, json.JSONDecodeError, KeyError) as e:
            app_logger.error(f"Failed to list Google models via direct API call: {e}", exc_info=True)
            try:
                app_logger.warning("Direct API failed. Falling back to SDK's client.models.list().")
                _google_client = genai.Client(api_key=api_key)
                sdk_models = [m for m in _google_client.models.list() if 'generateContent' in (m.supported_actions or [])]
                model_names = [model.name.split('/')[-1] for model in sdk_models]
            except Exception as sdk_e:
                app_logger.error(f"SDK fallback for listing Google models also failed: {sdk_e}", exc_info=True)
                raise RuntimeError("Could not retrieve model list from Google via API or SDK.") from sdk_e

    elif provider == "Anthropic":
        client = _anthropic.AsyncAnthropic(api_key=credentials.get("apiKey"))
        models_page = await client.models.list()
        model_names = [model.id for model in models_page.data]

    elif provider == "OpenAI":
        client = _openai.AsyncOpenAI(api_key=credentials.get("apiKey"))
        models_page = await client.models.list()
        model_names = [model.id for model in models_page.data if "gpt" in model.id]

    elif provider == "OpenRouter":
        openrouter_api_key = credentials.get("openrouter_api_key")
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {openrouter_api_key}"}
            )
            response.raise_for_status()
            data = response.json()
        model_names = [m.get("id") for m in data.get("data", []) if m.get("id")]
        # Best-effort: persist live OpenRouter pricing so cost tracking is
        # accurate. OpenRouter is authoritative for its own catalog (LiteLLM
        # lags it and mis-keys it under an "openrouter/" prefix), so these
        # prices take precedence over any LiteLLM-sourced row.
        _persist_provider_pricing("OpenRouter", data.get("data", []))

    elif provider == "Friendli":
        friendli_token = credentials.get("friendli_token")
        endpoint_url = credentials.get("friendli_endpoint_url")

        if endpoint_url:
            # Dedicated endpoint — /v1/models is not supported; model ID is
            # provided manually by the user in the configuration dialog
            model_names = []
        elif friendli_token:
            # Live query — Friendli support confirmed /serverless/v1/models is
            # the source of truth for serverless availability. Falls back to
            # the DB list if the call fails so the dialog still opens offline.
            try:
                async with httpx.AsyncClient(timeout=15.0) as http_client:
                    response = await http_client.get(
                        "https://api.friendli.ai/serverless/v1/models",
                        headers={"Authorization": f"Bearer {friendli_token}"},
                    )
                    response.raise_for_status()
                    data = response.json()
                model_names = [m["id"] for m in data.get("data", []) if m.get("id")]
                app_logger.info(f"Loaded {len(model_names)} Friendli serverless models from API")
                # Best-effort: persist live Friendli pricing (the serverless
                # /models payload includes per-1M token prices). Takes
                # precedence over LiteLLM. Only the API path carries pricing;
                # the DB fallback and dedicated-endpoint paths do not.
                _persist_provider_pricing("Friendli", data.get("data", []))
            except Exception as e:
                app_logger.warning(
                    f"Failed to fetch Friendli serverless models from API ({e}); "
                    "falling back to database list."
                )
                model_names = _get_friendli_serverless_models()
        else:
            # No token yet — fall back to DB list so the dropdown is populated
            # before the user enters credentials.
            app_logger.info("Friendli.ai Serverless mode: no token provided, using database list.")
            model_names = _get_friendli_serverless_models()

    elif provider == "Amazon":
        bedrock_client = boto3.client(
            service_name='bedrock',
            aws_access_key_id=credentials.get("aws_access_key_id"),
            aws_secret_access_key=credentials.get("aws_secret_access_key"),
            region_name=credentials.get("aws_region")
        )
        loop = asyncio.get_running_loop()
        if credentials.get("listing_method") == "inference_profiles":
            response = await loop.run_in_executor(None, lambda: bedrock_client.list_inference_profiles())
            model_names = [p['inferenceProfileArn'] for p in response['inferenceProfileSummaries']]
        else:
            response = await loop.run_in_executor(None, lambda: bedrock_client.list_foundation_models(byOutputModality='TEXT'))
            model_names = [m['modelId'] for m in response['modelSummaries']]

    elif provider == "Azure":
        deployment_name = credentials.get("azure_deployment_name")
        if deployment_name:
            model_names = [deployment_name]

    elif provider == "Ollama":
        client = OllamaClient(host=credentials.get("host"))
        models_data = await client.list_models()
        model_names = [m.get("name") for m in models_data]

    elif provider == "XAI":
        xai_api_key = credentials.get("xai_api_key")
        if not xai_api_key:
            app_logger.warning("XAI: no API key configured, skipping model list.")
            return []
        try:
            client = _openai.AsyncOpenAI(api_key=xai_api_key, base_url="https://api.x.ai/v1")
            response = await client.models.list()
            model_names = [m.id for m in response.data if m.id]
        except _openai.APIError as e:
            body = getattr(e, "body", None)
            if isinstance(body, dict):
                detail = body.get("error") or body.get("message") or e.message
            else:
                detail = e.message
            raise ValueError(f"xAI: {detail}") from e
        except Exception as e:
            app_logger.warning(f"Could not fetch XAI model list: {e}")
            model_names = []

    elif provider == "Celeris":
        from trusted_data_agent.llm.client_factory import celeris_base_url, CELERIS_DEFAULT_MODELS
        celeris_api_key = credentials.get("celeris_api_key")
        if not celeris_api_key:
            app_logger.warning("Celeris: no API key configured, skipping model list.")
            return []
        try:
            client = _openai.AsyncOpenAI(api_key=celeris_api_key, base_url=celeris_base_url(credentials))
            response = await client.models.list()
            model_names = [m.id for m in response.data if m.id]
        except _openai.APIError as e:
            # An auth failure is the operator's to fix — say so. A missing /models route is not:
            # the catalog is small and known, so fall back to it rather than an empty dropdown.
            if getattr(e, "status_code", None) in (401, 403):
                body = getattr(e, "body", None)
                detail = (body.get("error") or body.get("message")) if isinstance(body, dict) else None
                raise ValueError(f"Celeris: {detail or e.message}") from e
            app_logger.warning(f"Celeris /models unavailable ({e}); using the known catalog.")
            model_names = []
        except Exception as e:
            app_logger.warning(f"Could not fetch Celeris model list: {e}")
            model_names = []
        if not model_names:
            model_names = list(CELERIS_DEFAULT_MODELS)

    elif provider == "MLX":
        mlx_host = credentials.get("mlx_host", "http://localhost:8080").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5.0) as http_client:
                response = await http_client.get(f"{mlx_host}/v1/models")
                response.raise_for_status()
                data = response.json()
            model_names = [m.get("id") for m in data.get("data", []) if m.get("id")]
        except Exception as e:
            app_logger.warning(f"Could not reach MLX LM server at '{mlx_host}': {e}")
            model_names = []

    return [
        {
            "name": name,
            "recommended": _is_model_recommended(name, recommended_list)
        }
        for name in model_names
    ]

