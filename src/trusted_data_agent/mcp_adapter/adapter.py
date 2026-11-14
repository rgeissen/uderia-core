# trusted_data_agent/mcp/adapter.py
import asyncio
import contextvars
import copy
import json
import logging
import os
import re
import math as _math
import statistics as _stats
import uuid
from collections import Counter
from datetime import datetime, timedelta

# Configurable MCP tool call timeout (seconds). Prevents infinite hangs.
_MCP_TOOL_TIMEOUT = int(os.environ.get('TDA_MCP_TOOL_TIMEOUT', '120'))

# Per-session cache: stores the last HTML result from a real MCP data tool call.
# Keyed by session_id so concurrent sessions don't interfere.
# Used by TDA_Dashboard to auto-populate 'content' when the LLM omits it.

# Per-session cache: stores the user's original question for this turn.
# Populated by OPTIM (workflow_state) and IDEAT (conversation_engine) paths.
# Used by TDA_Dashboard to set an accurate source_prompt for tile re-run.
_user_input_cache: dict = {}


def _extract_first_html(result) -> str:
    """Extract or build an HTML table from an MCP tool result.

    Handles all common response envelope formats:
      {"status": "success", "results": [{"html": "<table>..."}]}  — pre-rendered HTML
      {"status": "success", "results": [{"Col": val, ...}]}      — raw column data
      {"data": [...]} / {"rows": [...]} / {"items": [...]}       — alternate keys
      [{"Col": val, ...}]                                         — bare result array
      Non-dict / None                                             — returns ""
    """
    # Normalise a top-level list into the standard dict envelope
    if isinstance(result, list):
        result = {"results": result}
    if not isinstance(result, dict):
        return ""
    # Skip confirmed error responses — don't cache bad data
    if result.get("status") == "error":
        return ""
    # Locate the results array under any common key
    rows = None
    for key in ("results", "data", "rows", "items"):
        candidate = result.get(key)
        if isinstance(candidate, list) and candidate:
            rows = candidate
            break
    if not rows:
        return ""
    # Priority 1: find a pre-rendered HTML table inside a result row
    for row in rows:
        if isinstance(row, dict):
            for key in ("html", "content", "response"):
                val = row.get(key)
                if val and isinstance(val, str) and ("<table" in val or "<tr" in val):
                    return val
    # Priority 2: build HTML from raw result rows
    first = next((r for r in rows if isinstance(r, dict)), None)
    if first:
        cols = [k for k in first.keys() if not k.startswith("_")]
        if cols:
            parts = ["<table class='assistant-table'><thead><tr>"]
            parts += [f"<th>{c}</th>" for c in cols]
            parts.append("</tr></thead><tbody>")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                parts.append("<tr>")
                for col in cols:
                    parts.append(f"<td>{row.get(col, '')}</td>")
                parts.append("</tr>")
            parts.append("</tbody></table>")
            return "".join(parts)
    return ""

from pydantic import ValidationError
from langchain_mcp_adapters.tools import load_mcp_tools
from trusted_data_agent.llm import handler as llm_handler
from trusted_data_agent.core.config import APP_CONFIG, AppConfig
from trusted_data_agent.core.config import get_user_mcp_server_id


def _overlay_map(STATE, key: str):
    """The per-turn overlay map that carries ``key``, at ANY depth of stacked overlays — or None.

    Overlays stack: the governed tool surface wraps the ``@TAG`` override's overlay in a second
    ``ChainMap({structured_tools, …}, override)``, so the override's slots sit one level down. Reading
    ``maps[0]`` alone missed them (trust campaign 3, Optimize · P2: the final report called the
    user's default Google client as the override's OpenRouter). The innermost base of every chain is
    the process-global ``APP_STATE`` and is never an overlay slot."""
    maps = getattr(STATE, "maps", None)
    if not maps:
        return None
    for m in maps[:-1]:
        if getattr(m, "maps", None) is not None:
            found = _overlay_map(m, key)
            if found is not None:
                return found
        elif isinstance(m, dict) and m.get(key) is not None:
            return m
    return _overlay_map(maps[-1], key)


def _source_data_keys(raw) -> list:
    """`source_data` as the list of workflow-state keys it names, whatever shape the model wrote:
    one string, a list, a NESTED list (`[["result_of_phase_1"]]`). A weak model produced the
    nested form and `key in workflow_state` raised "unhashable type: 'list'", failing a
    delegated prompt (trust campaign, Optimize · P2). Non-string entries are dropped."""
    out: list = []

    def _walk(v):
        if isinstance(v, str):
            if v.strip():
                out.append(v.strip())
        elif isinstance(v, (list, tuple)):
            for x in v:
                _walk(x)
    _walk(raw)
    return list(dict.fromkeys(out))


def turn_llm(STATE: dict, user_uuid: str | None):
    """The LLM identity of THIS turn: ``(instance, provider, model)``.

    ONE rule for every in-turn LLM task the adapter runs (filter · core · final report ·
    complex report). A per-turn ``@TAG`` overlay (a ChainMap whose first map carries ``llm``)
    is the turn's own client and wins outright — with the provider/model the override stamped
    beside it. Otherwise the PER-USER registry (``llm_by_user`` / ``current_provider_by_user``)
    is the authority, the same one the executor reads for its own calls; the process-global
    ``STATE['llm']`` / ``APP_CONFIG.CURRENT_PROVIDER`` are only the last resort, because they
    belong to whichever user last activated a profile (prod 2026-09-20: an OpenRouter turn's
    final report was dispatched as *Google* against its AsyncOpenAI client while another
    account ran a Gemini turn). ``None`` provider/model = let the handler fall back as before.
    """
    from trusted_data_agent.core.config import get_user_llm_instance, get_user_provider, get_user_model
    try:
        ov = _overlay_map(STATE, "llm")
        if ov is not None:
            return ov["llm"], ov.get("llm_provider"), ov.get("llm_model")
    except Exception:  # noqa: BLE001
        pass
    if user_uuid:
        try:
            inst = get_user_llm_instance(user_uuid)
            prov, model = get_user_provider(user_uuid), get_user_model(user_uuid)
            if inst is not None:
                return inst, prov, model
            return STATE.get("llm"), prov, model
        except Exception:  # noqa: BLE001
            pass
    return STATE.get("llm"), None, None
from trusted_data_agent.agent.response_models import CanonicalResponse, PromptReportResponse

app_logger = logging.getLogger("quart.app")



# ---------------------------------------------------------------------------
# Report-aware context distillation
# ---------------------------------------------------------------------------
# Unlike the executor's _distill_data_for_llm_context (metadata-only),
# this preserves sample rows so the LLM can generate meaningful analysis.

def _distill_workflow_for_report(workflow_state: dict) -> tuple[dict, dict | None]:
    """Create a report-friendly distillation of workflow_state.

    Keeps first APP_CONFIG.REPORT_DISTILLATION_MAX_ROWS rows as representative
    samples and adds metadata summary for the full dataset. Applies a total
    budget check and aggressively reduces if needed.

    Returns:
        Tuple of (distilled dict, distillation metadata dict or None if no meaningful reduction)
    """
    original_size = len(json.dumps(workflow_state))
    distilled = copy.deepcopy(workflow_state)

    for key in list(distilled.keys()):
        if key.startswith('_'):
            continue
        distilled[key] = _distill_value_for_report(distilled[key])

    # Safety check: if total size still exceeds budget, reduce further
    total_json = json.dumps(distilled)
    budget = APP_CONFIG.REPORT_DISTILLATION_TOTAL_BUDGET
    triggered_level2 = len(total_json) > budget
    if triggered_level2:
        app_logger.warning(
            f"Report distillation: total size {len(total_json):,} chars exceeds "
            f"budget {budget:,}. Applying aggressive reduction."
        )
        _aggressive_distill(distilled)
        reduced_json = json.dumps(distilled)
        app_logger.info(f"Report distillation: reduced to {len(reduced_json):,} chars")

    distilled_size = len(json.dumps(distilled))
    meta = None
    if distilled_size < original_size * 0.9:  # only report meaningful reductions (>10%)
        meta = {
            "subtype": "report_distillation",
            "summary": f"Report data distilled: {original_size:,} → {distilled_size:,} chars ({round((1 - distilled_size / original_size) * 100, 1)}% reduction, L{'2' if triggered_level2 else '1'}, analytical)",
            "original_chars": original_size,
            "distilled_chars": distilled_size,
            "reduction_pct": round((1 - distilled_size / original_size) * 100, 1),
            "level": 2 if triggered_level2 else 1,
            "budget_chars": budget,
            "analytical_distillation": True,
        }

    return distilled, meta


# ---------------------------------------------------------------------------
# Analytical Distillation — Column Statistics & Stratified Sampling
# ---------------------------------------------------------------------------
# Computes per-column statistics on the FULL dataset before truncation,
# and selects stratified samples covering the full data range instead of
# naive first-N rows. This gives the report LLM precise quantitative data
# (min/max/mean/percentiles/distributions) with minimal token overhead.

_TEMPORAL_NAME_HINTS = frozenset({
    "date", "time", "timestamp", "logdate", "thedate",
    "created", "modified", "updated", "datetime", "collecttimestamp",
})


def _compute_column_statistics(results_list: list[dict]) -> dict:
    """Compute per-column statistics for analytical distillation.

    Classifies each column as numeric, temporal, or categorical and
    returns appropriate statistics.  Uses only Python stdlib.
    """
    if not results_list:
        return {}

    columns = list(results_list[0].keys())
    col_stats = {}

    for col in columns:
        raw_values = [row.get(col) for row in results_list if row.get(col) is not None]
        if not raw_values:
            col_stats[col] = {"type": "empty", "count": 0}
            continue

        total_count = len(raw_values)

        # --- Type detection ---
        col_lower = col.lower().replace("_", "").replace("-", "").replace(" ", "")
        is_temporal_name = any(hint in col_lower for hint in _TEMPORAL_NAME_HINTS)

        # Numeric check: float() on sample of up to 50 values
        sample_for_detection = raw_values[:50]
        numeric_successes = 0
        for v in sample_for_detection:
            try:
                float(v)
                numeric_successes += 1
            except (ValueError, TypeError):
                pass
        is_numeric = len(sample_for_detection) > 0 and numeric_successes / len(sample_for_detection) > 0.8

        if is_numeric and not is_temporal_name:
            # --- Numeric column ---
            numeric_vals = []
            for v in raw_values:
                try:
                    numeric_vals.append(float(v))
                except (ValueError, TypeError):
                    pass

            if not numeric_vals:
                col_stats[col] = {"type": "categorical", "count": total_count,
                                  "distinct_count": len(set(str(v) for v in raw_values))}
                continue

            sorted_vals = sorted(numeric_vals)
            n = len(sorted_vals)

            entry = {
                "type": "numeric",
                "count": total_count,
                "min": round(sorted_vals[0], 4),
                "max": round(sorted_vals[-1], 4),
                "mean": round(_stats.mean(sorted_vals), 4),
                "sum": round(sum(sorted_vals), 4),
            }

            if n >= 2:
                entry["median"] = round(_stats.median(sorted_vals), 4)
                p25_idx = max(0, int(n * 0.25) - 1)
                p75_idx = min(n - 1, int(n * 0.75))
                entry["p25"] = round(sorted_vals[p25_idx], 4)
                entry["p75"] = round(sorted_vals[p75_idx], 4)
            if n >= 3:
                try:
                    finite_vals = [v for v in sorted_vals if _math.isfinite(v)]
                    if len(finite_vals) >= 3:
                        entry["stddev"] = round(_stats.stdev(finite_vals), 4)
                except (_stats.StatisticsError, AttributeError, ValueError):
                    pass

            col_stats[col] = entry

        elif is_temporal_name:
            # --- Temporal column ---
            str_values = [str(v) for v in raw_values]
            counter = Counter(str_values)
            sorted_unique = sorted(counter.keys())

            top_values = [
                {"value": val, "count": cnt, "pct": round(cnt / total_count * 100, 1)}
                for val, cnt in counter.most_common(10)
            ]
            col_stats[col] = {
                "type": "temporal",
                "count": total_count,
                "distinct_count": len(counter),
                "range": {"min": sorted_unique[0], "max": sorted_unique[-1]},
                "top_values": top_values,
            }

        else:
            # --- Categorical column ---
            str_values = [str(v) for v in raw_values]
            counter = Counter(str_values)

            top_values = [
                {"value": val, "count": cnt, "pct": round(cnt / total_count * 100, 1)}
                for val, cnt in counter.most_common(10)
            ]
            col_stats[col] = {
                "type": "categorical",
                "count": total_count,
                "distinct_count": len(counter),
                "top_values": top_values,
            }

    return col_stats


def _select_representative_sample(results_list: list[dict], max_rows: int) -> list[dict]:
    """Select a stratified sample covering the full range of data.

    Instead of taking the first N rows (biased toward the beginning),
    this selects boundary rows (first/last) plus evenly spaced interior
    rows to ensure coverage of the entire dataset.
    """
    n = len(results_list)
    if n <= max_rows:
        return list(results_list)

    boundary = min(5, max(1, max_rows // 4))
    head = results_list[:boundary]
    tail = results_list[-boundary:]

    interior_count = max_rows - (2 * boundary)
    if interior_count <= 0:
        return (head + tail)[:max_rows]

    middle_start = boundary
    middle_end = n - boundary
    middle_range = middle_end - middle_start

    step = max(1, middle_range // (interior_count + 1))
    interior = []
    for i in range(middle_start, middle_end, step):
        interior.append(results_list[i])
        if len(interior) >= interior_count:
            break

    return (head + interior + tail)[:max_rows]


def _distill_value_for_report(data):
    """Recursively distill a single value, preserving sample rows."""
    max_rows = APP_CONFIG.REPORT_DISTILLATION_MAX_ROWS
    max_chars = APP_CONFIG.REPORT_DISTILLATION_MAX_CHARS

    if isinstance(data, dict):
        if 'results' in data and isinstance(data['results'], list):
            results_list = data['results']
            is_large = (
                len(results_list) > max_rows
                or len(json.dumps(results_list)) > max_chars
            )
            if is_large and results_list and all(isinstance(r, dict) for r in results_list[:5]):
                # Analytical distillation: statistics on FULL data + stratified sample
                column_stats = _compute_column_statistics(results_list)
                sample = _select_representative_sample(results_list, max_rows)

                distilled = dict(data)
                distilled['results'] = sample
                distilled.setdefault('metadata', {})
                distilled['metadata']['total_row_count'] = len(results_list)
                distilled['metadata']['sample_rows_included'] = len(sample)
                distilled['metadata']['sampling_method'] = 'stratified'
                distilled['metadata']['columns'] = list(results_list[0].keys()) if results_list else []
                distilled['metadata']['column_statistics'] = column_stats
                distilled['metadata']['truncated'] = True
                distilled['metadata']['truncation_note'] = (
                    f"Analytical summary of {len(results_list)} rows with {len(sample)} "
                    f"representative samples (stratified). Use column_statistics for precise "
                    f"quantitative claims about the full dataset."
                )
                return distilled
        # a component result's pre-rendered `html` is the reader's card, never report input
        return {k: _distill_value_for_report(v) for k, v in data.items()
                if not (k in ("html", "title") and "spec" in data and "type" in data)}

    elif isinstance(data, list):
        if (
            len(data) > max_rows
            and data
            and all(isinstance(item, dict) for item in data[:5])
        ):
            column_stats = _compute_column_statistics(data)
            sample = _select_representative_sample(data, max_rows)
            return {
                '_distilled': True,
                'sample': sample,
                'total_count': len(data),
                'columns': list(data[0].keys()) if data else [],
                'column_statistics': column_stats,
                'sampling_method': 'stratified',
                'note': (
                    f"Analytical summary of {len(data)} items with {len(sample)} "
                    f"representative samples (stratified). Use column_statistics for "
                    f"precise quantitative claims about the full dataset."
                )
            }
        return [_distill_value_for_report(item) for item in data]

    return data


def _aggressive_distill(distilled: dict, max_rows: int = None):
    """Further reduce sample sizes when initial distillation exceeds budget."""
    if max_rows is None:
        max_rows = APP_CONFIG.REPORT_DISTILLATION_AGGRESSIVE_ROWS
    for key in list(distilled.keys()):
        if key.startswith('_'):
            continue
        distilled[key] = _reduce_samples(distilled[key], max_rows)


def _reduce_samples(data, max_rows: int):
    """Recursively reduce sample sizes in already-distilled data.

    Note: column_statistics are preserved as-is since they were computed
    on the full dataset during L1 distillation.
    """
    if isinstance(data, dict):
        if 'results' in data and isinstance(data['results'], list) and len(data['results']) > max_rows:
            data['results'] = _select_representative_sample(data['results'], max_rows)
            if 'metadata' in data and isinstance(data['metadata'], dict):
                data['metadata']['sample_rows_included'] = len(data['results'])
                data['metadata']['sampling_method'] = 'stratified'
                data['metadata']['truncation_note'] = (
                    f"Aggressively reduced to {len(data['results'])} stratified samples of "
                    f"{data['metadata'].get('total_row_count', '?')} rows. "
                    f"column_statistics reflect the full dataset."
                )
        if '_distilled' in data and 'sample' in data and isinstance(data['sample'], list):
            if len(data['sample']) > max_rows:
                data['sample'] = _select_representative_sample(data['sample'], max_rows)
        return {k: _reduce_samples(v, max_rows) for k, v in data.items()}
    elif isinstance(data, list):
        return [_reduce_samples(item, max_rows) for item in data]
    return data


# --- MODIFICATION START: System tools for the Planner/Executor architecture ---
# NOTE: Component tools (TDA_Charting, etc.) are NOT here — they are managed
# by ComponentManager and injected per-profile via get_tool_definitions().
CLIENT_SIDE_TOOLS = [
    {
        "name": "TDA_CurrentDate",
        "description": "Returns the current system date in YYYY-MM-DD format. Use this as the first step for any user query involving relative dates like 'today', 'yesterday', or 'this week'.",
        "args": {}
    },
    {
        "name": "TDA_DateRange",
        "description": "Calculates a list of dates based on a start date and a natural language phrase (e.g., 'past 3 days', 'last week'). This is a necessary second step for multi-day queries.",
        "args": {
            "start_date": {
                "type": "string",
                "description": "The anchor date for the calculation, usually today's date from `TDA_CurrentDate`. Must be in YYYY-MM-DD format.",
                "required": True
            },
            "date_phrase": {
                "type": "string",
                "description": "The natural language phrase describing the desired range (e.g., 'past 3 days', 'last 2 weeks').",
                "required": True
            }
        }
    },
    {
        "name": "TDA_LLMFilter",
        "description": "A specialized internal tool that filters a list of items based on a natural language goal and extracts a single, clean value. This is used as an intermediate step to pass a specific, machine-readable value from one tool to another.",
        "args": {
            "goal": {
                "type": "string",
                "description": "A clear, natural language description of the item to find and extract (e.g., 'Find the database related to fitness', 'Extract the name of the sales summary table').",
                "required": True
            },
            "data_to_filter": {
                "type": "list[dict]",
                "description": "The list of data objects to be filtered, passed from the result of a previous phase.",
                "required": True
            }
        }
    },
    {
        "name": "TDA_LLMTask",
        "description": "Performs internal, LLM-driven tasks that are not direct calls to the Teradata database. This tool is used for text synthesis, summarization, and formatting based on a specific 'task_description' provided by the LLM itself.",
        "args": {
            "task_description": {
                "type": "string",
                "description": "A natural language description of the internal task to be executed (e.g., 'describe the table in a business context', 'format final output'). The LLM infers this from the workflow plan.",
                "required": True
            },
            "source_data": {
                "type": "list[string]",
                "description": "A list of keys (e.g., 'result_of_phase_1') identifying which data from the workflow history is relevant for this task. This is critical for providing the correct context.",
                "required": True
            },
            "synthesized_answer": {
                "type": "string",
                "description": "The final, synthesized natural language answer, provided directly by the planner when it can confidently answer from history.",
                "required": False
            }
        }
    },
    {
        "name": "TDA_ContextReport",
        "description": "The History First Shortcut. This tool MUST be used for a single-phase plan if, and only if, the user's question can be answered entirely from the existing Workflow History. It bypasses all data gathering. Do NOT use this tool if you need to gather new data.",
        "args": {
            "answer_from_context": {
                "type": "string",
                "description": "The final, synthesized natural language answer, provided directly by the planner when it has confidently answered the user's question from the conversation history.",
                "required": True
            }
        }
    },
    {
        "name": "TDA_FinalReport",
        "description": "The Data Summarization Tool. This tool MUST be the final phase of a multi-step plan to synthesize the results from all previous data-gathering phases into a final report. Do NOT use this tool in a single-phase plan or as a first step.",
        "args": {}
    },
    {
        "name": "TDA_ComplexPromptReport",
        "description": "A special internal tool used to format and deliver the final, structured report for a pre-defined UI prompt. This tool MUST be called when all data gathering phases for the prompt are complete.",
        "args": {}
    }
]
# --- MODIFICATION END ---

# --- MODIFICATION START: Implement robust argument synonym resolution ---
def _normalize_tool_arguments(args: dict) -> dict:
    """
    Resolves argument synonyms to their canonical form using a 'last-wins'
    strategy. It iterates through the provided arguments, and if an argument
    is a known synonym, it maps it to the single, canonical argument name
    defined in the config, overwriting any previous value for that canonical name.
    This prevents validation errors and correctly handles cases where specific
    loop data should override generic plan data.
    """
    if not isinstance(args, dict):
        return {}

    resolved_args = {}
    # Create a reverse map for quick lookup: {synonym: canonical}
    reverse_synonym_map = {
        synonym: canonical
        for canonical, synonyms in AppConfig.ARGUMENT_SYNONYM_MAP.items()
        for synonym in synonyms
    }

    for arg_name, arg_value in args.items():
        # Find the canonical name for the current argument.
        # If the arg_name is not a synonym, it is treated as its own canonical name.
        canonical_name = reverse_synonym_map.get(arg_name, arg_name)

        # --- MODIFICATION START: Implement "last-wins" logic ---
        # Unconditionally set (or overwrite) the value for the canonical name.
        # This ensures the last synonym encountered takes precedence.
        resolved_args[canonical_name] = arg_value
        # --- MODIFICATION END ---

    return resolved_args
# --- MODIFICATION END ---


def _extract_and_clean_description(description: str | None) -> tuple[str, str]:
    if not isinstance(description, str):
        return "", "unknown"
    datatype = "unknown"
    match = re.search(r'\s*\((type:\s*(str|int|float|bool))\)', description, re.IGNORECASE)
    if match:
        datatype = match.group(2).lower()
        cleaned_description = description.replace(match.group(0), "").strip()
    else:
        cleaned_description = description
    return cleaned_description, datatype

def _extract_prompt_type_from_description(description: str | None) -> tuple[str, str]:
    if not isinstance(description, str):
        return "", "reporting"
    prompt_type = "reporting"
    match = re.search(r'\s*\((prompt_type:\s*(reporting|context))\)', description, re.IGNORECASE)
    if match:
        prompt_type = match.group(2).lower()
        cleaned_description = description.replace(match.group(0), "").strip()
    else:
        cleaned_description = description
    return cleaned_description, prompt_type

def _get_arg_descriptions_from_string(description: str) -> tuple[str, dict]:
    if not description:
        return "", {}
    args_section_match = re.search(r'\n\s*(Arguments|Args):\s*\n', description, re.IGNORECASE)
    if not args_section_match:
        return description, {}
    cleaned_description = description[:args_section_match.start()].strip()
    args_section_text = description[args_section_match.end():]
    pattern = re.compile(r'^\s*(?P<name>\w+)\s*[-:]\s*(?P<desc>.+)')
    descriptions = {}
    for line in args_section_text.split('\n'):
        match = pattern.match(line.strip())
        if match:
            data = match.groupdict()
            descriptions[data['name']] = data['desc'].strip()
    return cleaned_description, descriptions

def _get_type_from_schema(schema: dict) -> str:
    if not isinstance(schema, dict):
        return "any"
    if "type" in schema:
        return schema["type"]
    if "anyOf" in schema and isinstance(schema["anyOf"], list):
        for type_option in schema["anyOf"]:
            if isinstance(type_option, dict) and type_option.get("type") != "null":
                return type_option.get("type", "any")
    return "any"


class _SimpleTool:
    """Lightweight wrapper so MCP tool dicts behave like objects."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


async def _list_server_capabilities(temp_session):
    """ONE MCP server's tools, prompts and resources, read through an open session.

    PROP-006: the primary server and every additional server go through this one function —
    the per-server half of classification is the same code for both."""
    app_logger.info("--- Loading and classifying MCP tools and prompts... ---")

    list_tools_result = await temp_session.list_tools()
    raw_tools = list_tools_result.tools if hasattr(list_tools_result, 'tools') else []

    processed_tools = []
    for raw_tool in raw_tools:
        tool_name = raw_tool.name
        tool_desc = raw_tool.description or ""
        processed_args = []
        cleaned_description = tool_desc

        if hasattr(raw_tool, 'inputSchema') and raw_tool.inputSchema and 'properties' in raw_tool.inputSchema:
            cleaned_description, arg_desc_map = _get_arg_descriptions_from_string(tool_desc)
            schema = raw_tool.inputSchema
            required_args = schema.get('required', []) or []

            for arg_name, arg_schema in schema['properties'].items():
                processed_args.append({
                    "name": arg_name,
                    "type": _get_type_from_schema(arg_schema),
                    "required": arg_name in required_args,
                    "description": arg_desc_map.get(arg_name, arg_schema.get('title', 'No description.'))
                })

        processed_tools.append(_SimpleTool(
            name=tool_name,
            description=cleaned_description,
            args={arg['name']: arg for arg in processed_args}
        ))

    loaded_tools = processed_tools
    app_logger.info(f"Loaded {len(loaded_tools)} tools from MCP server")

    loaded_prompts = []
    try:
        list_prompts_result = await temp_session.list_prompts()
        if hasattr(list_prompts_result, 'prompts'):
            loaded_prompts = list_prompts_result.prompts
            app_logger.info(f"Loaded {len(loaded_prompts)} prompts from MCP server")
    except Exception as e:
        app_logger.warning(f"Could not load prompts from MCP server (this may be expected for older servers): {e}")

    loaded_resources = []
    try:
        list_resources_result = await temp_session.list_resources()
        if hasattr(list_resources_result, 'resources'):
            loaded_resources = list_resources_result.resources
            app_logger.info(f"Successfully loaded {len(loaded_resources)} resources from MCP server.")
    except Exception as e:
        app_logger.warning(f"Could not load resources from MCP server (this may be expected for older servers): {e}")
        loaded_resources = []
    return loaded_tools, loaded_prompts, loaded_resources


async def _classify_capabilities(llm_instance, classification_mode, loaded_tools, loaded_prompts,
                                 loaded_resources, exclude_names):
    """Categorise ONE server's capabilities: ``light`` = one generic category per type, ``full`` =
    one LLM call. Returns ``(classified_data, skip_classification)``."""
    all_capabilities = []
    all_capabilities.extend([f"- {tool.name} (tool): {tool.description}"
                              for tool in loaded_tools
                              if tool.name not in exclude_names])

    for p in loaded_prompts:
        prompt_str = f"- {p.name} (prompt): {p.description or 'No description available.'}"
        if hasattr(p, 'arguments') and p.arguments:
            prompt_str += "\n  - Arguments:"
            for arg in p.arguments:
                arg_dict = arg.model_dump()
                arg_name = arg_dict.get('name', 'unknown_arg')
                prompt_str += f"\n    - `{arg_name}`"
        all_capabilities.append(prompt_str)

    if loaded_resources:
        all_capabilities.extend([f"- {res.name} (resource): {res.description or 'No description.'}" for res in loaded_resources])

    capabilities_list_str = "\n".join(all_capabilities)

    if classification_mode == 'light':
        app_logger.info(f"Classification mode 'light': using generic single-category structure. Processing {len(loaded_tools)} tools, {len(loaded_prompts)} prompts, {len(loaded_resources)} resources")
        return {}, True
    if classification_mode == 'full':
        app_logger.info("Classification mode 'full': calling LLM to semantically categorize capabilities...")
        classification_prompt = (
            "You are a helpful assistant that analyzes a list of technical capabilities (tools and prompts) and classifies them. "
            "For each capability, you must determine a single user-friendly 'category' for a UI. "
            "Example categories might be 'Data Quality', 'Table Management', 'Performance', 'Utilities', 'Database Information', etc. Be concise and consistent.\n\n"
            "Your response MUST be a single, valid JSON object. The keys of this object must be the capability names, "
            "and the value for each key must be another JSON object containing only the 'category' you determined.\n\n"
            "Example format:\n"
            "{\n"
            '  "capability_name_1": {"category": "Some Category"},\n'
            '  "capability_name_2": {"category": "Another Category"}\n'
            "}\n\n"
            f"--- Capability List ---\n{capabilities_list_str}"
        )
        categorization_system_prompt = "You are an expert assistant that only responds with valid JSON."

        classified_capabilities_str, _, _, _, _ = await llm_handler.call_llm_api(
            llm_instance, classification_prompt, raise_on_error=True,
            system_prompt_override=categorization_system_prompt
        )

        match = re.search(r'\{.*\}', classified_capabilities_str, re.DOTALL)
        if match is None:
            raise ValueError(f"LLM failed to return a valid JSON for capability classification. Response: '{classified_capabilities_str}'")

        cleaned_str = match.group(0)
        return json.loads(cleaned_str), False
    app_logger.warning(f"Unknown classification mode '{classification_mode}', defaulting to 'light'")
    return {}, True


def _structure_capabilities(loaded_tools, loaded_prompts, loaded_resources, classified_data,
                            skip_classification, disabled_tools_list, disabled_prompts_list,
                            tool_scopes, exclude=()):
    """ONE server's categorised tools / prompts / resources, in the snapshot's shape. ``tool_scopes``
    is filled in place. Returns ``(structured_tools, structured_prompts, structured_resources)``."""
    structured_tools = {}
    for tool in loaded_tools:
        if tool.name in exclude:
            continue  # Always-active system tools; injected separately at runtime, not stored in classification_results

        if skip_classification:  # 'light' mode
            category = "All Tools"
        else:  # 'full' mode
            classification = classified_data.get(tool.name, {})
            category = classification.get("category", "Uncategorized")

        if category not in structured_tools:
            structured_tools[category] = []

        processed_args = []
        if hasattr(tool, 'args') and isinstance(tool.args, dict):
            for arg_name, arg_details in tool.args.items():
                if isinstance(arg_details, dict):
                    processed_args.append({
                        "name": arg_name,
                        "type": arg_details.get("type", "any"),
                        "description": arg_details.get("description", "No description available."),
                        "required": arg_details.get("required", False)
                    })

        required_args_raw = {arg['name'] for arg in processed_args if arg.get('required')}

        canonical_required_args = set()
        for arg_name in required_args_raw:
            found_canonical = False
            for canonical, synonyms in AppConfig.ARGUMENT_SYNONYM_MAP.items():
                if arg_name in synonyms:
                    canonical_required_args.add(canonical)
                    found_canonical = True
                    break
            if not found_canonical:
                canonical_required_args.add(arg_name)

        for scope, required_set in AppConfig.TOOL_SCOPE_HIERARCHY:
            if required_set.issubset(canonical_required_args):
                tool_scopes[tool.name] = scope
                break

        is_disabled = tool.name in disabled_tools_list
        structured_tools[category].append({
            "name": tool.name,
            "description": tool.description,
            "arguments": processed_args,
            "disabled": is_disabled
        })

    structured_prompts = {}
    if loaded_prompts:
        for prompt_obj in loaded_prompts:
            if skip_classification:  # 'light' mode
                category = "All Prompts"
            else:  # 'full' mode
                classification = classified_data.get(prompt_obj.name, {})
                category = classification.get("category", "Uncategorized")

            if category not in structured_prompts:
                structured_prompts[category] = []

            is_disabled = prompt_obj.name in disabled_prompts_list

            cleaned_prompt_desc, prompt_type = _extract_prompt_type_from_description(prompt_obj.description)

            processed_args = []
            if hasattr(prompt_obj, 'arguments') and prompt_obj.arguments:
                for arg in prompt_obj.arguments:
                    arg_dict = arg.model_dump()
                    cleaned_arg_desc, arg_type = _extract_and_clean_description(arg_dict.get("description"))
                    arg_dict['description'] = cleaned_arg_desc
                    arg_dict['type'] = arg_type
                    processed_args.append(arg_dict)

            structured_prompts[category].append({
                "name": prompt_obj.name,
                "description": cleaned_prompt_desc or "No description available.",
                "arguments": processed_args,
                "disabled": is_disabled,
                "prompt_type": prompt_type
            })

    structured_resources = {}
    if loaded_resources:
        for resource_obj in loaded_resources:
            if skip_classification:  # 'light' mode
                category = "All Resources"
            else:  # 'full' mode
                classification = classified_data.get(resource_obj.name, {})
                category = classification.get("category", "Uncategorized")

            if category not in structured_resources:
                structured_resources[category] = []

            structured_resources[category].append({
                "name": resource_obj.name,
                "description": resource_obj.description or "No description."
            })
    return structured_tools, structured_prompts, structured_resources


async def load_and_categorize_mcp_resources(STATE: dict, user_uuid: str = None, profile_id: str = None):
    """
    Load MCP tools, prompts, and resources, then categorize them based on the profile's classification mode.
    
    Args:
        STATE: Application state dictionary
        user_uuid: User identifier for config loading
        profile_id: Profile ID to get classification settings from
    """
    mcp_client = STATE.get('mcp_client')
    llm_instance = STATE.get('llm')
    if not mcp_client or not llm_instance:
        raise Exception("MCP or LLM client not initialized.")

    # Use server ID instead of name for session management
    server_id = get_user_mcp_server_id(user_uuid)
    # server_id may be None for capabilities-only profiles (platform tools, no data source)

    # Get classification mode from profile if provided
    classification_mode = 'light'  # default
    skip_classification_profile = False  # Track if this profile type should skip classification
    is_conversation_profile = False  # Track if this is a conversation profile (LangChain-based)
    if profile_id:
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()
        profile = config_manager.get_profile(profile_id, user_uuid)
        if profile:
            classification_mode = profile.get('classification_mode', 'light')
            profile_type = profile.get('profile_type', 'tool_enabled')
            use_mcp_tools = profile.get('useMcpTools', False)

            # Determine if this is a conversation profile (llm_only with MCP tools enabled)
            is_conversation_profile = (profile_type == 'llm_only' and use_mcp_tools)

            # Check if this profile type should skip LLM classification:
            # - llm_only with useMcpTools: Uses LangChain for tool selection, not the planner
            # - rag_focused: Uses knowledge retrieval, not MCP tool classification
            skip_classification_profile = (
                (profile_type == 'llm_only' and use_mcp_tools) or
                profile_type == 'rag_focused'
            )

            # CRITICAL: Force 'light' mode for profiles that don't need LLM classification
            if skip_classification_profile and classification_mode != 'light':
                app_logger.info(f"Forcing 'light' classification mode for {profile_type} profile {profile_id} (was: {classification_mode})")
                classification_mode = 'light'
            app_logger.info(f"Using classification mode '{classification_mode}' from profile {profile_id} (skip classification: {skip_classification_profile})")
        else:
            app_logger.warning(f"Profile {profile_id} not found, using default classification mode 'full'")
    else:
        app_logger.warning("No profile_id provided to load_and_categorize_mcp_resources, using default mode 'full'")

    # === CACHING LAYER: Check schema cache ===
    import time
    cache_key = server_id or '__no_primary_server__'
    schema_cache = STATE.setdefault('mcp_tool_schema_cache', {})
    cached_schemas = schema_cache.get(cache_key)

    cache_valid = False
    loaded_tools = []
    loaded_prompts = []
    loaded_resources = []

    if cached_schemas:
        age = time.time() - cached_schemas.get('timestamp', 0)
        if age < 300:  # 5 minute TTL
            cache_valid = True
            app_logger.info(f"✓ CACHE HIT: Tool schemas for server {server_id} (age: {age:.1f}s, {cached_schemas.get('tool_count', 0)} tools, saved ~7s)")
            loaded_tools = cached_schemas['tools']
            loaded_prompts = cached_schemas['prompts']
            loaded_resources = cached_schemas['resources']
        else:
            app_logger.info(f"Cache expired for server {server_id} (age: {age:.1f}s > 300s TTL)")

    SimpleTool = _SimpleTool

    # === Load from MCP if cache miss/expired ===
    if not cache_valid:
        if not server_id:
            # Capabilities-only profile: no primary data source server.
            # CLIENT_SIDE_TOOLS (TDA_*) are excluded here — injected at runtime by configuration_service.py.
            app_logger.info("No primary MCP server — capabilities-only profile, skipping primary server load")
            schema_cache[cache_key] = {
                'tools': [],
                'prompts': [],
                'resources': [],
                'timestamp': time.time(),
                'tool_count': 0,
            }
        else:
            async with mcp_client.session(server_id) as temp_session:
                loaded_tools, loaded_prompts, loaded_resources = await _list_server_capabilities(temp_session)

            # Add CLIENT_SIDE_TOOLS after the primary server tools (primary server path only)
            for tool_def in CLIENT_SIDE_TOOLS:
                loaded_tools.append(SimpleTool(**tool_def))

            # Cache the loaded schemas
            schema_cache[cache_key] = {
                'tools': loaded_tools,
                'prompts': loaded_prompts,
                'resources': loaded_resources,
                'timestamp': time.time(),
                'tool_count': len(loaded_tools)
            }
            app_logger.info(f"✓ Cached tool schemas for server {server_id} ({len(loaded_tools)} tools, {len(loaded_prompts)} prompts, {len(loaded_resources)} resources)")

    # === Process tools/prompts and save classification (runs for both fresh load AND cache hit) ===
    # This is outside the cache block because we need to populate STATE and save classification
    # results even when using cached schemas (schema cache != profile classification cache)
    STATE['mcp_tools'] = {tool.name: tool for tool in loaded_tools}
    if loaded_prompts:
        STATE['mcp_prompts'] = {prompt.name: prompt for prompt in loaded_prompts}

    # CLIENT_SIDE_TOOLS (TDA_*) are always-active platform tools, not MCP server tools.
    # They must not be sent to the LLM for classification or stored in classification_results.
    # They are injected into APP_STATE separately at runtime (configuration_service.py).
    client_side_names = {t['name'] for t in CLIENT_SIDE_TOOLS}

    classified_data, skip_classification = await _classify_capabilities(
        llm_instance, classification_mode, loaded_tools, loaded_prompts, loaded_resources, client_side_names)

    # `tool_scopes` exists once a server tool has been seen (as it always has)
    _scopes = STATE['tool_scopes'] if isinstance(STATE.get('tool_scopes'), dict) else {}
    _st, _sp, _sr = _structure_capabilities(
        loaded_tools, loaded_prompts, loaded_resources, classified_data, skip_classification,
        STATE.get("disabled_tools", []), STATE.get("disabled_prompts", []), _scopes,
        exclude=client_side_names)
    if any(t.name not in client_side_names for t in loaded_tools):
        STATE['tool_scopes'] = STATE.get('tool_scopes') if isinstance(STATE.get('tool_scopes'), dict) else _scopes
    STATE['structured_tools'] = _st
    # --- MODIFICATION START: Remove the generation of the static tools_context ---
    # STATE['tools_context'] will now be built dynamically in the handler.
    STATE['tools_context'] = "--- No Tools Available ---"
    # --- MODIFICATION END ---
    STATE['structured_prompts'] = _sp
    STATE['structured_resources'] = _sr

    prompt_context_parts = ["--- Available Prompts ---"]
    for category, prompts in sorted(STATE['structured_prompts'].items()):
        enabled_prompts_in_category = [p for p in prompts if not p['disabled']]
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

    if len(prompt_context_parts) > 1:
        STATE['prompts_context'] = "\n".join(prompt_context_parts)
    else:
        STATE['prompts_context'] = "--- No Prompts Available ---"

    tool_args = set()
    for tool in STATE['mcp_tools'].values():
        if hasattr(tool, 'args') and isinstance(tool.args, dict):
            tool_args.update(tool.args.keys())

    prompt_args = set()
    for prompt_list in STATE['structured_prompts'].values():
        for prompt_info in prompt_list:
            if 'arguments' in prompt_info and isinstance(prompt_info['arguments'], list):
                for arg_details in prompt_info['arguments']:
                    if 'name' in arg_details:
                        prompt_args.add(arg_details['name'])

    STATE['all_known_mcp_arguments'] = {
        "tool": list(tool_args),
        "prompt": list(prompt_args)
    }
    app_logger.info(f"Dynamically identified {len(tool_args)} tool and {len(prompt_args)} prompt arguments for context enrichment.")

    # === Merge platform MCP server tools into structured_tools ===
    # Platform tools are additive alongside the primary server tools (or are the only tools
    # for capabilities-only profiles). They bypass the profile.tools[] filter — see config_manager.py.
    if profile_id:
        try:
            from trusted_data_agent.core.platform_connector_registry import get_effective_tools as _get_platform_tools
            platform_tools = _get_platform_tools(profile_id)
            if platform_tools:
                # Remove stale platform categories before re-inserting
                for _pk in [k for k in STATE.get('structured_tools', {}) if k == "Platform Tools" or k.startswith("Platform: ")]:
                    STATE['structured_tools'].pop(_pk, None)
                for pt in platform_tools:
                    _pcat = pt.get("_category", "Platform Tools")
                    STATE['structured_tools'].setdefault(_pcat, []).append(pt)
                    # Also register in mcp_tools so tool execution can find it
                    STATE['mcp_tools'][pt['name']] = SimpleTool(
                        name=pt['name'],
                        description=pt.get('description', ''),
                        args={a['name']: a for a in pt.get('arguments', [])}
                    )
                app_logger.info(f"Merged {len(platform_tools)} platform MCP tools for profile {profile_id}")
        except Exception as _e:
            app_logger.warning(f"Could not merge platform MCP tools: {_e}")

    # Save classification results to profile if profile_id provided
    if profile_id:
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()

        # CRITICAL: Conversation profiles use LangChain which doesn't support MCP prompts
        # Exclude prompts from classification results for conversation profiles
        classification_results = {
            'tools': STATE.get('structured_tools', {}),
            'prompts': {} if is_conversation_profile else STATE.get('structured_prompts', {}),
            'resources': STATE.get('structured_resources', {}),
            'tool_scopes': STATE.get('tool_scopes', {}),
            'classified_with_mode': classification_mode
        }

        config_manager.save_profile_classification(
            profile_id=profile_id,
            classification_results=classification_results,
            user_uuid=user_uuid
        )
        tool_count = sum(len(tools) for tools in classification_results['tools'].values())
        prompt_count = sum(len(prompts) for prompts in classification_results['prompts'].values())
        resource_count = sum(len(resources) for resources in classification_results['resources'].values())
        app_logger.info(f"Saved classification results to profile {profile_id} (mode: {classification_mode}): {len(classification_results['tools'])} tool categories with {tool_count} tools, {len(classification_results['prompts'])} prompt categories with {prompt_count} prompts, {len(classification_results['resources'])} resource categories with {resource_count} resources")


def _classification_mode_for(profile: dict) -> tuple:
    """(mode, is_conversation_profile) — the same rule the primary server's classification applies."""
    mode = (profile or {}).get('classification_mode', 'light')
    ptype = (profile or {}).get('profile_type', 'tool_enabled')
    use_mcp = (profile or {}).get('useMcpTools', False)
    is_conv = (ptype == 'llm_only' and use_mcp)
    if ((ptype == 'llm_only' and use_mcp) or ptype == 'rag_focused') and mode != 'light':
        mode = 'light'
    if mode not in ('light', 'full'):
        mode = 'light'
    return mode, is_conv


def _merge_server_snapshot(STATE: dict, srv, snapshot: dict, include_prompts: bool = True) -> dict:
    """Put ONE additional server's snapshot into STATE under the agent's names for it: every tool
    and prompt as ``<alias>__<name>``, every category as ``<alias>: <category>`` (the platform's
    ``Platform: Web`` convention), each flagged by that server's own selection. Returns counts."""
    prefix = f"{srv.alias}: "
    for key in ('structured_tools', 'structured_prompts', 'structured_resources'):
        cur = STATE.get(key)
        if not isinstance(cur, dict):
            STATE[key] = {}
        for k in [k for k in STATE[key] if isinstance(k, str) and k.startswith(prefix)]:
            STATE[key].pop(k, None)
    STATE.setdefault('mcp_tools', {})
    STATE.setdefault('mcp_prompts', {})
    STATE.setdefault('tool_scopes', {})
    n_tools = n_prompts = 0
    for cat, items in (snapshot.get('tools') or {}).items():
        group = []
        for t in items or []:
            if not isinstance(t, dict) or not t.get('name'):
                continue
            base = t['name']
            q = srv.name_for(base)
            entry = dict(t, name=q, disabled=not srv.selects_tool(base))
            group.append(entry)
            STATE['mcp_tools'][q] = _SimpleTool(
                name=q, description=t.get('description', ''),
                args={a['name']: a for a in (t.get('arguments') or []) if isinstance(a, dict) and a.get('name')},
                mcp_server_id=srv.server_id, base_name=base)
            scope = (snapshot.get('tool_scopes') or {}).get(base)
            if scope:
                STATE['tool_scopes'][q] = scope
            n_tools += 1
        if group:
            STATE['structured_tools'][prefix + str(cat)] = group
    if include_prompts:
        for cat, items in (snapshot.get('prompts') or {}).items():
            group = []
            for pr in items or []:
                if not isinstance(pr, dict) or not pr.get('name'):
                    continue
                base = pr['name']
                q = srv.name_for(base)
                group.append(dict(pr, name=q, disabled=not srv.selects_prompt(base)))
                STATE['mcp_prompts'][q] = _SimpleTool(
                    name=q, description=pr.get('description', ''),
                    args={a['name']: a for a in (pr.get('arguments') or []) if isinstance(a, dict) and a.get('name')},
                    mcp_server_id=srv.server_id, base_name=base)
                n_prompts += 1
            if group:
                STATE['structured_prompts'][prefix + str(cat)] = group
    for cat, items in (snapshot.get('resources') or {}).items():
        if items:
            STATE['structured_resources'][prefix + str(cat)] = list(items)
    return {"tools": n_tools, "prompts": n_prompts}


async def load_additional_mcp_resources(STATE: dict, user_uuid: str, profile_id: str, *, client_for=None) -> dict:
    """PROP-006 phase 2 — an agent's ADDITIONAL MCP servers, classified per server and merged into
    STATE under their unique names. A single-server agent returns at once: no session, no call,
    nothing written.

    Per server (CL1–CL8): the server id is always explicit (never the per-user current server);
    a stored snapshot taken in the agent's mode is reused as is; otherwise the server is listed and
    classified through the SAME helpers as the primary (light = no LLM call, full = one per
    server) and its snapshot saved apart from the primary's. ``client_for(server_id)`` returns the
    pooled MCP client for a server (injected so tests can drive fake servers). Returns
    ``{server_id: {"alias", "source": "snapshot"|"classified"|"error", "tools", "prompts", "error"?}}``;
    one server failing never stops the others (a turn answers from the reachable sources, B9)."""
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.core.agent_mcp_servers import additional_of, ADDITIONAL_CLASSIFICATION_KEY
    cm = get_config_manager()
    profile = cm.get_profile(profile_id, user_uuid) if profile_id else None
    extra = additional_of(profile)
    if not extra:
        return {}
    mode, is_conv = _classification_mode_for(profile)
    stored = (profile or {}).get(ADDITIONAL_CLASSIFICATION_KEY)
    stored = stored if isinstance(stored, dict) else {}
    report = {}
    for srv in extra:
        try:
            snap = stored.get(srv.server_id)
            usable = (isinstance(snap, dict) and snap.get('last_classified')
                      and snap.get('classified_with_mode') == mode)
            source = "snapshot"
            if not usable:
                if client_for is None:
                    raise RuntimeError("no MCP client available for this server")
                client = client_for(srv.server_id)
                async with client.session(srv.server_id) as sess:
                    tools, prompts, resources = await _list_server_capabilities(sess)
                classified, skip = await _classify_capabilities(
                    STATE.get('llm'), mode, tools, prompts, resources, set())
                scopes = {}
                st, sp, sr = _structure_capabilities(tools, prompts, resources, classified, skip,
                                                     [], [], scopes)
                snap = {"tools": st, "prompts": {} if is_conv else sp, "resources": sr,
                        "tool_scopes": scopes, "classified_with_mode": mode}
                cm.save_additional_classification(profile_id, srv.server_id, snap, user_uuid)
                snap = dict(snap, last_classified=True)
                source = "classified"
            counts = _merge_server_snapshot(STATE, srv, snap, include_prompts=not is_conv)
            report[srv.server_id] = {"alias": srv.alias, "source": source, **counts}
        except Exception as e:  # noqa: BLE001 — one server down never stops the others
            app_logger.warning(f"PROP-006: additional MCP server {srv.server_id} ({srv.alias}) could not be loaded: {e}")
            report[srv.server_id] = {"alias": srv.alias, "source": "error", "tools": 0, "prompts": 0,
                                     "error": str(e)[:300]}
    return report


def resource_panel_servers(profile: dict, server_names: dict | None = None) -> dict | None:
    """PROP-006 phase 2 — what the resource panel shows for a multi-server agent BEYOND the first
    server's lists: every additional server's categories (``<alias>: <category>``, tools and
    prompts named ``<alias>__<name>`` and flagged by that server's own selection — built by the
    runtime's own ``_merge_server_snapshot``, so the panel and the turn can never disagree), and
    ``servers`` — one entry per server, the first server first, each with its classification
    state (never omitted: a server with no snapshot here is listed as not classified).

    None for a single-server agent — its panel response is unchanged."""
    from trusted_data_agent.core.agent_mcp_servers import additional_of, ADDITIONAL_CLASSIFICATION_KEY
    extra = additional_of(profile)
    if not extra:
        return None
    names = server_names or {}
    mode, is_conv = _classification_mode_for(profile)
    store = (profile or {}).get(ADDITIONAL_CLASSIFICATION_KEY) or {}
    from trusted_data_agent.core.agent_mcp_servers import primary_of
    primary_id = primary_of(profile) or ""
    primary_cls = (profile or {}).get("classification_results") or {}
    servers = [{"server_id": primary_id, "name": names.get(primary_id) or primary_id, "alias": None,
                "state": "classified" if primary_cls.get("last_classified") else "not_classified"}]
    tools: dict = {}
    prompts: dict = {}
    category_servers: dict = {}
    for srv in extra:
        snap = store.get(srv.server_id) if isinstance(store, dict) else None
        usable = (isinstance(snap, dict) and snap.get("last_classified")
                  and snap.get("classified_with_mode") == mode)
        entry = {"server_id": srv.server_id, "name": names.get(srv.server_id) or srv.server_id,
                 "alias": srv.alias, "state": "classified" if usable else "not_classified"}
        if not usable and isinstance(snap, dict) and snap.get("last_classified"):
            entry["reason"] = f"classified in {snap.get('classified_with_mode')} mode, the agent uses {mode}"
        servers.append(entry)
        if not usable:
            continue
        scratch: dict = {}
        counts = _merge_server_snapshot(scratch, srv, snap, include_prompts=not is_conv)
        entry.update({"tools": counts.get("tools", 0), "prompts": counts.get("prompts", 0)})
        for cat, items in (scratch.get("structured_tools") or {}).items():
            tools[cat] = items
            category_servers[cat] = srv.server_id
        for cat, items in (scratch.get("structured_prompts") or {}).items():
            prompts[cat] = items
            category_servers[cat] = srv.server_id
    return {"tools": tools, "prompts": prompts, "servers": servers,
            "category_servers": category_servers, "primary_server_id": primary_id}


def resolve_mcp_target(name: str, default_client, default_server_id, user_uuid: str,
                       servers: dict | None = None):
    """PROP-006 phase 2 — the ONE place a tool or prompt name becomes (client, server id, the name
    the server knows it by). A name of the agent's primary server — every name of a single-server
    agent — returns the defaults unchanged. A ``<alias>__<name>`` of an ADDITIONAL server returns
    that server's pooled client and the server's own name.

    ``servers`` = ``{alias: server_id}`` for callers outside a turn (a REST endpoint that knows the
    profile); inside a turn it defaults to the turn's map (armed at the execution seam). An alias
    that is known by pattern but cannot be routed raises — a call is never sent to another server."""
    from trusted_data_agent.core.tool_identity import parse as _p, turn_server_for as _tsf
    if servers is None:
        ident = _p(name)
        sid = _tsf(ident.alias)
    else:
        ident = _p(name, list(servers.keys()))
        sid = servers.get(ident.alias) if ident.alias else None
    if not ident.alias:
        return default_client, default_server_id, name
    if not sid:
        raise Exception(f"'{name}' belongs to a server ({ident.alias}) this turn cannot reach")
    from trusted_data_agent.core.configuration_service import pooled_mcp_client as _pooled
    return _pooled(sid, user_uuid), sid, ident.name


def _transform_chart_data(data: any) -> list[dict]:
    if isinstance(data, list) and all(isinstance(item, dict) and 'results' in item for item in data):
        app_logger.info("Detected nested tool output. Flattening data for charting.")
        flattened_data = []
        for item in data:
            results_list = item.get("results")
            if isinstance(results_list, list):
                flattened_data.extend(results_list)
        return flattened_data

    if isinstance(data, dict) and 'labels' in data and 'values' in data:
        app_logger.warning("Correcting hallucinated chart data format from labels/values to list of dicts.")
        labels = data.get('labels', [])
        values = data.get('values', [])
        if isinstance(labels, list) and isinstance(values, list) and len(labels) == len(values):
            return [{"label": l, "value": v} for l, v in zip(labels, values)]
    if isinstance(data, dict) and 'columns' in data and 'rows' in data:
        app_logger.warning("Correcting hallucinated chart data format from columns/rows to list of dicts.")
        if isinstance(data.get('rows'), list):
            return data['rows']

    if isinstance(data, list) and data and isinstance(data[0], dict):
        if "ColumnName" in data[0] and "DistinctValue" in data[0] and "DistinctValueCount" in data[0]:
            app_logger.info("Detected qlty_distinctCategories output pattern. Renaming 'ColumnName' to 'SourceColumnName'.")
            transformed_data = []
            for row in data:
                new_row = row.copy()
                if "ColumnName" in new_row:
                    new_row["SourceColumnName"] = new_row.pop("ColumnName")
                transformed_data.append(new_row)
            return transformed_data

    return data

def _build_g2plot_spec(args: dict, data: list[dict]) -> dict:
    chart_type = args.get("chart_type", "").lower()
    mapping = args.get("mapping", {})

    canonical_map = {
        'x_axis': 'xField',
        'y_axis': 'yField',
        'color': 'seriesField',
        'angle': 'angleField',
        'category': 'xField',
        'value': 'yField'
    }

    # Create a reverse lookup from alias (lowercase) to canonical G2Plot key
    reverse_canonical_map = {}
    for canonical, g2plot_key in canonical_map.items():
        reverse_canonical_map[canonical.lower()] = g2plot_key
        # Add common aliases - extend this if needed
        if canonical == 'x_axis':
            reverse_canonical_map['category'] = g2plot_key
        if canonical == 'y_axis':
            reverse_canonical_map['value'] = g2plot_key

    options = {"title": {"text": args.get("title", "Generated Chart")}}

    first_row_keys_lower = {k.lower(): k for k in data[0].keys()} if data and data[0] else {}

    processed_mapping = {}
    for llm_key, data_col_name in mapping.items():
        g2plot_key = reverse_canonical_map.get(llm_key.lower()) # Use reverse map
        if g2plot_key:
            actual_col_name = first_row_keys_lower.get(data_col_name.lower())
            if not actual_col_name:
                raise KeyError(f"The mapped column '{data_col_name}' (from '{llm_key}') was not found in the provided data.")
            processed_mapping[g2plot_key] = actual_col_name # Use g2plot_key here
        else:
            app_logger.warning(f"Unknown mapping key from LLM: '{llm_key}'. Skipping.")

    options.update(processed_mapping)

    # Specific adjustments for G2Plot types
    if chart_type == 'pie' and 'seriesField' in options:
        options['colorField'] = options.pop('seriesField') # Pie uses colorField for categories

    # Ensure numeric fields are actually numbers
    final_data = []
    if data:
        for row in data:
            new_row = row.copy()
            for g2plot_key, actual_col_name in options.items():
                if g2plot_key in ['yField', 'angleField', 'sizeField', 'value']: # Added 'value' just in case
                    cell_value = new_row.get(actual_col_name)
                    if cell_value is not None:
                        try:
                            # Attempt conversion, handling potential commas
                            numeric_value = float(str(cell_value).replace(',', ''))
                            new_row[actual_col_name] = numeric_value
                        except (ValueError, TypeError):
                            app_logger.warning(f"Non-numeric value '{cell_value}' encountered for numeric field '{actual_col_name}'. Conversion failed.")
            final_data.append(new_row)

    options["data"] = final_data

    # Map common names to official G2Plot types
    g2plot_type_map = {
        "bar": "Column", "column": "Column", "line": "Line", "area": "Area",
        "pie": "Pie", "scatter": "Scatter", "histogram": "Histogram",
        "heatmap": "Heatmap", "boxplot": "Box", "wordcloud": "WordCloud"
    }
    g2plot_type = g2plot_type_map.get(chart_type, chart_type.capitalize()) # Default to capitalized if not found

    return {"type": g2plot_type, "options": options}

# --- MODIFICATION START: Add user_uuid and update docstring ---
async def _invoke_llm_filter_task(STATE: dict, command: dict, user_uuid: str = None, session_id: str = None, call_id: str | None = None) -> tuple[dict, int, int]:
    """
    Executes a specialized LLM-based filtering task. It takes a list of data
    and a natural language goal, and is strictly prompted to return only a
    single, clean, machine-readable string value.
    This now robustly handles being passed a single dict from a loop.
    """
    # --- MODIFICATION END ---
    args = command.get("arguments", {})
    goal = args.get("goal")
    data_to_filter = args.get("data_to_filter")
    llm_instance, _turn_provider, _turn_model = turn_llm(STATE, user_uuid)

    final_call_id = call_id or str(uuid.uuid4())

    if not goal or not data_to_filter:
        return {"status": "error", "error_message": "TDA_LLMFilter requires 'goal' and 'data_to_filter' arguments."}, 0, 0

    # --- MODIFICATION START: Make context-sensitive to loop execution ---
    # If data_to_filter is a single dictionary (from a loop), wrap it in a list.
    if isinstance(data_to_filter, dict):
        app_logger.debug("TDA_LLMFilter received a single dict, wrapping in a list for processing.")
        data_to_filter = [data_to_filter]
    elif not isinstance(data_to_filter, list):
        app_logger.error(f"TDA_LLMFilter received invalid data_to_filter type: {type(data_to_filter)}")
        return {"status": "error", "error_message": f"TDA_LLMFilter 'data_to_filter' must be a list or a dict, but got {type(data_to_filter)}."}, 0, 0
    # --- MODIFICATION END ---

    filtering_prompt = (
        "You are an expert data extraction assistant. Your task is to find a single, specific item within a list of JSON objects that matches a given goal.\n\n"
        f"--- GOAL ---\n{goal}\n\n"
        f"--- DATA LIST ---\n{json.dumps(data_to_filter, indent=2)}\n\n"
        "--- INSTRUCTIONS ---\n"
        "1. Analyze the GOAL to understand what specific entity is being sought (e.g., a specific database name, a table name).\n"
        "2. Scan the DATA LIST to find the single JSON object that best matches this entity.\n"
        "3. From that single object, identify the key that holds the entity's primary identifier or name.\n"
        "4. Extract the value associated with that key.\n\n"
        "**CRITICAL OUTPUT FORMATTING:** Your response MUST be only the single, extracted value. It MUST NOT contain any punctuation, conversational text, explanations, markdown, or any other characters. Your output will be used directly as a parameter for another tool and must be perfectly clean.\n\n"
        "Example: If you extract the database name 'fitness_db', your response must be exactly `fitness_db` and nothing else."
    )

    reason = f"Client-Side Tool Call: TDA_LLMFilter\nGoal: {goal}"

    # --- MODIFICATION START: Pass user_uuid ---
    response_text, input_tokens, output_tokens, _, _ = await llm_handler.call_llm_api(
        llm_instance=llm_instance,
        current_provider=_turn_provider,
        current_model=_turn_model,
        prompt=filtering_prompt,
        reason=reason,
        system_prompt_override="You are a data extraction assistant that only responds with clean, single values.",
        raise_on_error=True,
        user_uuid=user_uuid,
        session_id=session_id
    )
    # --- MODIFICATION END ---

    cleaned_response_text = response_text.strip().strip('.,:;')

    result = {
        "status": "success",
        # --- MODIFICATION START: Add token counts to metadata ---
        "metadata": {
            "call_id": final_call_id,
            "tool_name": "TDA_LLMFilter",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        },
        # --- MODIFICATION END ---
        "results": [{"response": cleaned_response_text}]
    }

    return result, input_tokens, output_tokens

# --- MODIFICATION START: Add user_uuid and correct data fetching/prompt ---
async def _invoke_core_llm_task(STATE: dict, command: dict, workflow_state: dict, user_uuid: str = None, session_history: list = None, mode: str = "standard", session_id: str = None, call_id: str | None = None) -> tuple[dict, int, int]:
    args = command.get("arguments", {})
    user_question = args.get("user_question", "No user question provided.")
    llm_instance, _turn_provider, _turn_model = turn_llm(STATE, user_uuid)
    final_prompt = ""
    reason = ""

    final_call_id = call_id or str(uuid.uuid4())

    if mode == 'full_context':
        # ... (full_context mode logic remains unchanged) ...
        app_logger.info(f"Executing client-side LLM task in 'full_context' mode.")
        reason = f"Client-Side Tool Call: TDA_LLMTask (Full Context)\nSynthesizing answer for: '{user_question}'"

        history_str_parts = []
        if session_history:
            for entry in session_history:
                role = entry.get('role', 'unknown')
                content = entry.get('content', '')
                history_str_parts.append(f"--- Role: {role.capitalize()} ---\n{content}\n--- End Entry ---")
        history_str = "\n".join(history_str_parts)

        final_prompt = (
            "You are an expert data analyst and synthesizer. Your task is to answer the user's question by carefully analyzing the provided conversation history.\n"
            "The history contains the full dialogue, including the user's requests and the assistant's detailed, formatted HTML responses from previous turns.\n"
            "The answer to the current question is likely already present in this history.\n\n"
            "--- CURRENT USER QUESTION ---\n"
            f"{user_question}\n\n"
            "--- FULL CONVERSATION HISTORY ---\n"
            f"{history_str}\n\n"
            "--- INSTRUCTIONS ---\n"
            "1. Read the 'CURRENT USER QUESTION' to understand the user's goal.\n"
            "2. Thoroughly review the 'FULL CONVERSATION HISTORY' to find the previous turn where this question was successfully answered.\n"
            "3. Extract the relevant information from the assistant's previous HTML response.\n"
            "4. Synthesize a new, clean final answer that directly addresses the current user question.\n"
            "5. Your response MUST follow the same semantic content and markdown formatting as the original answer found in the history. Do NOT add conversational intros like \"I found this in the history...\". Simply provide the answer as if you were generating it for the first time."
        )

    else: # Standard mode
        task_description = args.get("task_description")
        source_data_keys = _source_data_keys(args.get("source_data", []))
        formatting_instructions = args.get("formatting_instructions")

        # Prioritize 'data' argument if present (from looping phase)
        focused_data_for_task = args.get("data")
        data_keys_found = [] # Track which keys were actually found

        if focused_data_for_task is None:
            # Fallback: Explicitly fetch from workflow_state using source_data_keys
            app_logger.info(f"TDA_LLMTask standard mode: Fetching data from workflow_state using keys: {source_data_keys}")
            focused_data_for_task = {}
            if isinstance(workflow_state, dict):
                for key in source_data_keys:
                    if key in workflow_state:
                        focused_data_for_task[key] = workflow_state[key]
                        data_keys_found.append(key)
                    else:
                        app_logger.warning(f"TDA_LLMTask: Source data key '{key}' specified in arguments was NOT found in the current workflow state.")
            else:
                 app_logger.error(f"TDA_LLMTask: workflow_state is not a dictionary. Cannot fetch source data. State type: {type(workflow_state)}")

            # Accurate Warning: Only warn if keys were specified but *none* were found.
            if source_data_keys and not data_keys_found:
                 app_logger.warning(f"TDA_LLMTask was called for '{task_description}' but NO source data was found for the specified keys: {source_data_keys}. The data payload will be empty.")
                 # Ensure focused_data_for_task is explicitly empty if nothing was found
                 focused_data_for_task = {}
            elif source_data_keys and len(data_keys_found) < len(source_data_keys):
                 app_logger.warning(f"TDA_LLMTask: Not all specified source data keys were found. Found: {data_keys_found}, Missing: {set(source_data_keys) - set(data_keys_found)}")


        else:
             app_logger.info(f"TDA_LLMTask is using the pre-filtered 'data' argument provided by the executor.")

        app_logger.info(f"Executing client-side LLM task in 'standard' mode: {task_description}")
        reason = f"Client-Side Tool Call: TDA_LLMTask\nTask: {task_description}"


        known_context = {}
        # Try to extract context from focused data first, then full state if needed
        # Use the definitively populated focused_data_for_task
        context_source = focused_data_for_task
        if isinstance(context_source, dict):
            for phase_results in context_source.values():
                # Handle both list of results and single result dicts robustly
                results_to_process = []
                if isinstance(phase_results, list):
                    results_to_process = phase_results
                elif isinstance(phase_results, dict): # Handle case where source might be a single dict
                    results_to_process = [phase_results]

                for result in results_to_process:
                    if isinstance(result, dict) and "metadata" in result:
                        metadata = result.get("metadata", {})
                        if "database" in metadata and "database_name" not in known_context:
                            known_context["database_name"] = metadata["database"]
                        if "table" in metadata and "table_name" not in known_context:
                            known_context["table_name"] = metadata["table"]
                        if "column_name" in metadata and "column_name" not in known_context:
                             known_context["column_name"] = metadata["column_name"]

        known_context_str = "\n".join([f"- {key}: {value}" for key, value in known_context.items()]) if known_context else "None"

        final_prompt = "You are a highly capable text processing and synthesis assistant.\n\n"

        if user_question and user_question != "Executing prompt...": # Don't inject default text
            final_prompt += (
                "--- PRIMARY GOAL ---\n"
                f"Your most important task is to directly answer the user's original question: '{user_question}'.\n"
                "You MUST begin your response with the direct answer. Do not repeat the user's question or use conversational intros like 'Here is...'. "
                "After providing the direct answer, you may then proceed with a more general summary or analysis of the data.\n\n"
            )

        final_prompt += (
            "--- TASK ---\n"
            f"{task_description}\n\n"
            "--- RELEVANT DATA (Selected from Previous Phases) ---\n"
            f"{json.dumps(focused_data_for_task, indent=2)}\n\n" # Use the definitively populated data
            "--- KNOWN CONTEXT ---\n"
            "The following key information has already been established in previous steps. You MUST use this information to populate header fields like 'Table Name' or 'Database Name'.\n"
            f"{known_context_str}\n\n"
            "--- SEMANTIC GUIDANCE ---\n"
            "When the 'TASK' asks for a 'description', 'analysis', or 'summary', you MUST synthesize new content that reflects the *semantic intent* of the request.\n"
            "For example:\n"
            "- If the 'TASK' asks for a 'business description of a table', you MUST explain its purpose from an organizational, functional, or analytical viewpoint, and the business significance of its columns. Do NOT simply reiterate technical DDL (Data Definition Language) information, even if it is present in the `RELEVANT DATA`.\n"
            "- If the 'TASK' asks for a 'summary of errors', you MUST provide a concise overview of the issues, not just a list of error codes.\n"
            "Always prioritize generating content that matches the *meaning* and *purpose* of the 'TASK', interpreting the raw data to produce the desired semantic output.\n\n"
            "--- CRITICAL RULES ---\n"
            "1. **Accurate Refusal:** If you cannot complete the `TASK` because the `RELEVANT DATA` is missing or is of the wrong type (e.g., you need descriptions but received DDLs), you MUST state this clearly. Explain *why* the provided data is insufficient (e.g., 'I need table descriptions, but only DDLs were provided'). Do NOT falsely claim the `RELEVANT DATA` section is empty if data is present.\n"
            "2. **Separate Data from Description:** If the 'TASK' requires you to output header fields (like `***Table Name:***` or `***Database Name:***`) AND a main description, you MUST treat these as separate steps. First, populate the header fields using the 'KNOWN CONTEXT'. Then, write the main description. Do NOT merge context data (like the database name) into a single header field.\n"
            "3. **Content and Formatting Precision:** You MUST adhere to any and all formatting instructions contained in the 'TASK' description with absolute precision. Do not deviate, simplify, or change the requested format in any way. You MUST generate content that genuinely fulfills the semantic goal of the 'TASK'.\n"
            "4. **Key Name Adherence:** If the 'TASK' description provides an example format, you MUST use the exact key names (e.g., `***Description:***`, `***Table Name:***`) shown in the example. Do not invent new key names or use synonyms like 'Table Description'.\n"
            "5. **Column Placeholder Replacement:** If the 'TASK' involves describing table columns and the formatting guidelines include a placeholder like `***ColumnX:***` or `***[Column Name]:***`, you MUST replace that placeholder with the actual name of the column you are describing (e.g., `***CUST_ID:***`, `***FIRSTNAME:***`). Do not use generic, numbered placeholders like 'Column1', 'Column2', etc.\n"
            "6. **Layout and Line Breaks:** Each key-value pair or list item specified in the formatting guidelines MUST be on its own separate line. Do not combine multiple items onto a single line.\n\n"
        )

        if formatting_instructions:
            final_prompt += f"--- ADDITIONAL FORMATTING INSTRUCTIONS ---\n{formatting_instructions}\n\n"

        final_prompt += "Your response should be the direct result of the task. Do not add any conversational text or extra formatting unless explicitly requested by the task description."
    # --- MODIFICATION END ---


    # --- MODIFICATION START: Pass user_uuid and planning_phase ---
    response_text, input_tokens, output_tokens, _, _ = await llm_handler.call_llm_api(
        llm_instance=llm_instance,
        current_provider=_turn_provider,
        current_model=_turn_model,
        prompt=final_prompt,
        reason=reason,
        system_prompt_override="You are a text processing and synthesis assistant.",
        raise_on_error=True,
        user_uuid=user_uuid,
        session_id=session_id,
        planning_phase="conversation"  # Track as conversation phase
    )
    # --- MODIFICATION END ---

    refusal_phrases = [
        "i'm unable to", "i cannot", "unable to generate", "no specific task",
        "as an ai model", "i can't provide"
    ]

    if any(phrase in response_text.lower() for phrase in refusal_phrases):
        app_logger.error(f"TDA_LLMTask failed due to detected LLM refusal. Response: '{response_text}'")
        result = {
            "status": "error",
            "error_message": "LLM refused to perform the synthesis task.",
            "data": response_text
        }
    else:
        result = {
            "status": "success",
            # --- MODIFICATION START: Add token counts to metadata ---
            "metadata": {
                "call_id": final_call_id,
                "tool_name": "TDA_LLMTask",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            },
            # --- MODIFICATION END ---
            "results": [{"response": response_text}]
        }

    return result, input_tokens, output_tokens

REPORT_FORMAT_NOTE = (
    "\n\nIMPORTANT: Your previous response contained no JSON object. Respond with ONLY the JSON "
    "object described above (with a non-empty \"direct_answer\" written from the data provided). "
    "No prose before or after it."
)


def report_needs_format_retry(response_text) -> bool:
    """True when a report response carries no JSON object or list at all — the shape a format
    retry can fix. A response with JSON that fails validation is NOT this case: the parser's
    coercion and the fallback extractor handle it (OC-F15)."""
    text = response_text or ""
    return not re.search(r"\{.*\}|\[.*\]", text, re.DOTALL)

def _extract_fallback_answer(response_text: str) -> str | None:
    """
    Last-resort extraction of an answer string from an LLM response whose
    JSON field names are corrupted beyond repair by key normalization.

    Strategy: parse the JSON and find the most likely 'direct_answer' value
    by checking key names and string value lengths.
    """
    try:
        json_match = re.search(r'```json\s*\n(.*?)\n\s*```|(\{.*\}|\[.*\])', response_text, re.DOTALL)
        if not json_match:
            return None
        json_str = next(g for g in json_match.groups() if g is not None)
        data = json.loads(json_str)
    except (json.JSONDecodeError, StopIteration):
        return None

    if not isinstance(data, dict):
        return None

    # Priority 1: Key containing "direct" or "answer" with a string value
    for key, value in data.items():
        if isinstance(value, str) and len(value.strip()) > 10:
            key_lower = key.lower()
            if 'direct' in key_lower or 'answer' in key_lower:
                return value.strip()

    # Priority 2: First string value longer than 20 chars
    for key, value in data.items():
        if isinstance(value, str) and len(value.strip()) > 20:
            return value.strip()

    # Priority 3: First non-empty string value
    for key, value in data.items():
        if isinstance(value, str) and len(value.strip()) > 0:
            return value.strip()

    return None


# --- MODIFICATION START: Add user_uuid ---
async def _resolve_fasttrack_report_cfg(user_uuid: str, session_id: str) -> tuple[bool, int]:
    """
    Resolve the report fast-track config for the session's active Optimize profile.
    Returns (enabled, max_rows). Fail-safe: returns (False, 0) on ANY uncertainty so the
    standard report path is used unless the profile has explicitly opted in.
    """
    try:
        if not user_uuid or not session_id:
            return False, 0
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.core.config_manager import get_config_manager
        sess = await session_manager.get_session(user_uuid, session_id)
        if not sess:
            return False, 0
        pid = sess.get("last_executed_profile_id") or sess.get("profile_id")
        if not pid:
            return False, 0
        prof = get_config_manager().get_profile(pid, user_uuid)
        if not prof or prof.get("profile_type") != "tool_enabled":
            return False, 0
        if not prof.get("fastTrackSimpleReports"):
            return False, 0
        try:
            max_rows = max(1, int(prof.get("fastTrackMaxRows")))
        except (TypeError, ValueError):
            max_rows = 1
        return True, max_rows
    except Exception:
        return False, 0


def _count_report_result_rows(workflow_state_for_data: dict):
    """
    Conservatively count result rows across the collected workflow state. Returns the total
    row count, or None if the structure can't be confidently interpreted (=> no fast-track).
    """
    try:
        total = 0
        found = False
        for k, v in (workflow_state_for_data or {}).items():
            if isinstance(k, str) and k.startswith('_'):
                continue
            cand = None
            if isinstance(v, dict):
                if isinstance(v.get("results"), list):
                    cand = v["results"]
                elif isinstance(v.get("data"), list):
                    cand = v["data"]
            elif isinstance(v, list):
                cand = v
            if cand is not None:
                total += len(cand)
                found = True
        return total if found else None
    except Exception:
        return None


async def _invoke_final_report_task(STATE: dict, command: dict, workflow_state: dict, user_uuid: str = None, session_id: str = None, call_id: str | None = None) -> tuple[dict, int, int]:
# --- MODIFICATION END ---
    llm_instance, _turn_provider, _turn_model = turn_llm(STATE, user_uuid)
    user_question = command.get("arguments", {}).get("user_question", "No user question provided.")
    final_call_id = call_id or str(uuid.uuid4())

    # --- OPTIMIZATION START: Pass-through mode when tactical planner pre-formatted the report ---
    # If the tactical planner already generated a well-formatted "report" argument,
    # skip the LLM call and use it directly. This saves ~3,000 tokens per query.
    pre_formatted_report = command.get("arguments", {}).get("report")
    _data_failure_note = command.get("arguments", {}).get("_data_failure_note")
    # A report the tactical planner pre-wrote cannot have known that every data query failed —
    # with that fact on the command, the report is written, not passed through.
    if _data_failure_note:
        pre_formatted_report = None
    if pre_formatted_report and isinstance(pre_formatted_report, str) and len(pre_formatted_report.strip()) > 0:
        app_logger.info(f"TDA_FinalReport: Using pre-formatted report from tactical planner (pass-through mode)")

        result = {
            "status": "success",
            "metadata": {
                "call_id": final_call_id,
                "tool_name": "TDA_FinalReport",
                "input_tokens": 0,
                "output_tokens": 0,
                "optimization": "pass_through"
            },
            "results": [{
                "direct_answer": pre_formatted_report.strip(),
                "key_metric": None,
                "key_observations": []
            }],
            "corrections": []
        }
        return result, 0, 0
    # --- OPTIMIZATION END ---

    # --- MODIFICATION START: Extract and format knowledge context separately for clarity ---
    # Use .get() instead of .pop() to avoid modifying the original workflow_state
    knowledge_context = workflow_state.get('_knowledge_context')
    knowledge_section = ""
    if knowledge_context:
        knowledge_section = (
            "--- KNOWLEDGE CONTEXT (Concepts, Syntax, Best Practices) ---\n"
            f"{knowledge_context}\n\n"
        )
        # Create a copy without the knowledge context for the DATA section (avoid duplication)
        workflow_state_for_data = {k: v for k, v in workflow_state.items() if k != '_knowledge_context'}
    else:
        workflow_state_for_data = workflow_state
    # --- MODIFICATION END ---

    distilled_data, distill_meta = _distill_workflow_for_report(workflow_state_for_data)
    distilled_json = json.dumps(distilled_data, indent=2)

    # --- FAST-TRACK (opt-in, Optimize): for a small result, trim the verbose CanonicalResponse
    # report instructions to a lightweight synthesis prompt. The LLM call, parse, and result
    # schema below stay IDENTICAL — only the prompt text changes. Additive + fail-safe: only
    # fires when the profile flag is on, the row count is within budget, AND the distilled data
    # is genuinely small (belt-and-suspenders vs a mis-count). Otherwise the standard report
    # prompt is used unchanged. ---
    # Honour the verdict stamped by the phase executor (single source of truth, so the Live
    # Status event and this synthesis stay consistent). The data-size guard remains here as a
    # final safety net against a mis-count upstream.
    _fast_track_report = bool(command.get("arguments", {}).get("_fast_track_report")) and len(distilled_json) < 6000
    if _fast_track_report:
        app_logger.info(
            f"TDA_FinalReport FAST-TRACK: lightweight report prompt "
            f"({command.get('arguments', {}).get('_fast_track_rows', '?')} row(s), "
            f"distilled {len(distilled_json):,} chars)."
        )

    if _fast_track_report:
        final_summary_prompt_text = (
            "Answer the user's question in one or two concise, factual sentences using ONLY the data below. "
            "Report the VALUES in the data: a single aggregate value (e.g. a COUNT/SUM) IS the answer — "
            "never report the number of rows as the count, and do not treat an id-named column holding an "
            "aggregate as an identifier.\n\n"
            f"--- USER'S ORIGINAL QUESTION ---\n{user_question}\n\n"
            f"{knowledge_section}"
            f"--- DATA ---\n{distilled_json}\n\n"
            "--- OUTPUT ---\n"
            "Respond with a single JSON object: {\"direct_answer\": \"<your one- or two-sentence answer>\"}. "
            "Do not include any other fields."
        )
    else:
        final_summary_prompt_text = (
        "You are an expert data analyst. Your task is to create a final report for the user by analyzing the provided data and their original question.\n\n"
        f"--- USER'S ORIGINAL QUESTION ---\n{user_question}\n\n"
        f"{knowledge_section}"
        f"--- DATA FOR ANALYSIS ---\n{distilled_json}\n\n"
        "--- INSTRUCTIONS ---\n"
        "Your response MUST be a single JSON object that strictly follows the schema for a `CanonicalResponse`.\n"
        "You are required to populate its fields based on your analysis of the data provided above.\n"
        + ("If KNOWLEDGE CONTEXT is provided above, integrate relevant concepts, syntax examples, or best practices into your response alongside the data analysis.\n\n" if knowledge_context else "\n")
        + "--- FIELD GUIDELINES ---\n"
        "1.  `direct_answer`: REQUIRED. A single, concise sentence that directly and factually answers the user's primary question.\n"
        "2.  `key_metric`: OPTIONAL. Use ONLY if the answer can be summarized by a single, primary value (e.g., a total count, a status). Requires `value` (string) and `label` (string). Omit the entire field if not applicable.\n"
        "3.  `key_observations`: OPTIONAL. A list of objects, each with a `text` field containing a single, narrative bullet point of supporting detail or context. Do NOT include raw data or code.\n\n"
        "--- STATISTICAL DATA USAGE ---\n"
        "When the data includes `column_statistics` in metadata, use these pre-computed statistics "
        "for precise quantitative claims (min, max, mean, median, percentiles, distributions). "
        "These statistics cover the FULL dataset, not just the sample rows."
    )

    reason = f"Client-Side Tool Call: TDA_FinalReport\nGoal: {user_question}"

    # --- FIX: Log prompt size to help diagnose large data issues ---
    raw_data_chars = len(json.dumps(workflow_state_for_data, indent=2))
    prompt_char_count = len(final_summary_prompt_text)
    app_logger.info(
        f"TDA_FinalReport: Prompt size={prompt_char_count:,} chars, "
        f"raw workflow_data={raw_data_chars:,} chars "
        f"(distilled to {prompt_char_count:,} in prompt)"
    )
    # --- END FIX ---

    # S1 The Governed Stream: the report's `direct_answer` streams as the model writes it
    # (one string field out of the JSON reply — the reader sees the sentence, never the
    # braces). The ambient stream is the turn's; a report started after an earlier one
    # already streamed (a delegated sub-prompt's) withdraws that text first — the LATEST
    # report is the candidate answer. None when streaming is off ⇒ non-streaming call.
    _report_sink = None
    try:
        from trusted_data_agent.agent import answer_stream as _as
        _turn_stream = _as.current()
        if _turn_stream is not None:
            if _turn_stream.delivered_text:
                _turn_stream.reset()
            _report_sink = _as.JsonFieldSink(_turn_stream, "direct_answer")
    except Exception:  # noqa: BLE001 — never break a report on a stream
        _report_sink = None

    if isinstance(_data_failure_note, str) and _data_failure_note:
        final_summary_prompt_text = f"{final_summary_prompt_text}\n\n{_data_failure_note}"

    # S5 — an exhausted turn budget: the report is written from what was collected and the
    # model is told to say what is left undone (a recorded graceful finish, never a kill).
    try:
        from trusted_data_agent.agent import turn_budget as _turn_budget
        _bnote = _turn_budget.report_note()
        if _bnote:
            final_summary_prompt_text = f"{final_summary_prompt_text}\n\n{_bnote}"
    except Exception:  # noqa: BLE001
        pass
    # S10 — a steer sent while the plan ran is read by the final report (the last step
    # boundary of an Optimize turn); nothing waits ⇒ nothing.
    try:
        from trusted_data_agent.core import steer as _steer
        _snote = _steer.consume_at_boundary(session_id, "TDA_FinalReport", user_uuid)
        if _snote:
            final_summary_prompt_text = f"{final_summary_prompt_text}\n\n{_snote}"
    except Exception:  # noqa: BLE001
        pass

    # --- MODIFICATION START: Pass user_uuid and planning_phase ---
    response_text, input_tokens, output_tokens, _, _ = await llm_handler.call_llm_api(
        llm_instance=llm_instance,
        current_provider=_turn_provider,
        current_model=_turn_model,
        prompt=final_summary_prompt_text,
        reason=reason,
        system_prompt_override="You are a JSON-only reporting assistant.",
        raise_on_error=True,
        user_uuid=user_uuid,
        session_id=session_id,
        planning_phase="conversation",  # Track as conversation phase
        on_delta=_report_sink,
    )
    # --- MODIFICATION END ---

    # OC-F15 (Optimize campaign, 2026-10-29): a weak model sometimes answers the report with no
    # JSON at all ("Could not extract a canonical answer." — 2 in 20 MiMo turns). The error went
    # to the generic correction, whose "text sanitization" LLM call invented arguments (the
    # question as `synthesized_answer`) and THEN re-ran the report. Repeat the report ONCE here
    # with the format restated — the same call the correction ended in, without the invented
    # step; its tokens are added to this tool's (one priced event, O6).
    if report_needs_format_retry(response_text):
        app_logger.warning("TDA_FinalReport: response carried no JSON — repeating the report once with the format restated.")
        _retry_sink = None
        if _report_sink is not None:   # a fresh field reader: the first one has read the non-JSON text
            try:
                _retry_sink = _as.JsonFieldSink(_turn_stream, "direct_answer")
            except Exception:  # noqa: BLE001 — no stream is never a failed report
                _retry_sink = None
        _r_text, _r_in, _r_out, _, _ = await llm_handler.call_llm_api(
            llm_instance=llm_instance,
            current_provider=_turn_provider,
            current_model=_turn_model,
            prompt=final_summary_prompt_text + REPORT_FORMAT_NOTE,
            reason=f"{reason} (format retry)",
            system_prompt_override="You are a JSON-only reporting assistant.",
            raise_on_error=True,
            user_uuid=user_uuid,
            session_id=session_id,
            planning_phase="conversation",
            on_delta=_retry_sink,
        )
        input_tokens += _r_in or 0
        output_tokens += _r_out or 0
        if not report_needs_format_retry(_r_text):
            response_text = _r_text

    try:
        report_data, correction_descriptions = llm_handler.parse_and_coerce_llm_response(response_text, CanonicalResponse)

        # --- SANITY CHECK: Cross-validate key_metric against direct_answer ---
        # Weak models often hallucinate garbage in key_metric.value (e.g. "10 growth."
        # instead of "100") while getting direct_answer right. Since key_metric is
        # optional, null it out if the value looks inconsistent with the answer.
        if report_data.key_metric and report_data.direct_answer:
            metric_val = report_data.key_metric.value.strip()
            answer = report_data.direct_answer
            # Extract all numbers from the direct_answer for cross-validation
            answer_numbers = set(re.findall(r'\d[\d,]*\.?\d*', answer))
            # Extract the leading number from the metric value (if any)
            metric_number_match = re.match(r'(\d[\d,]*\.?\d*)', metric_val)
            metric_number = metric_number_match.group(1) if metric_number_match else None
            # If metric has a number but it doesn't appear in the answer, OR
            # if metric contains unexpected alphabetic filler, null it out
            has_alpha_filler = bool(re.search(r'[a-zA-Z]{2,}', metric_val))
            number_mismatch = metric_number and answer_numbers and metric_number not in answer_numbers
            if has_alpha_filler and number_mismatch:
                correction_msg = (
                    f"Corrected key_metric: value '{metric_val}' is inconsistent with "
                    f"direct_answer (expected one of {answer_numbers}). Metric removed."
                )
                app_logger.info(correction_msg)
                correction_descriptions.append(correction_msg)
                report_data.key_metric = None
        # --- END SANITY CHECK ---

        result = {
            "status": "success",
            # --- MODIFICATION START: Add token counts to metadata ---
            "metadata": {
                "call_id": final_call_id,
                "tool_name": "TDA_FinalReport",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            },
            # --- MODIFICATION END ---
            "results": [report_data.model_dump()],
            "corrections": correction_descriptions
        }
        if distill_meta:
            result["_distillation_meta"] = distill_meta
        return result, input_tokens, output_tokens
    except (json.JSONDecodeError, ValidationError) as e:
        # --- FIX: More detailed error logging to diagnose parsing failures ---
        response_preview = response_text[:500] if len(response_text) > 500 else response_text
        response_end = response_text[-200:] if len(response_text) > 200 else ""
        app_logger.error(
            f"Failed to parse/validate TDA_FinalReport.\n"
            f"  Error: {e}\n"
            f"  Response length: {len(response_text)} chars\n"
            f"  Response start: {response_preview}\n"
            f"  Response end: {response_end}"
        )
        # --- END FIX ---

        # --- LAST-RESORT FALLBACK: Extract answer from corrupted JSON ---
        fallback_answer = _extract_fallback_answer(response_text)
        if fallback_answer:
            app_logger.warning(
                f"TDA_FinalReport: Using last-resort fallback extraction. "
                f"Extracted direct_answer ({len(fallback_answer)} chars) from corrupted response."
            )
            result = {
                "status": "success",
                "metadata": {
                    "call_id": final_call_id,
                    "tool_name": "TDA_FinalReport",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "optimization": "fallback_extraction"
                },
                "results": [{
                    "direct_answer": fallback_answer,
                    "key_metric": None,
                    "key_observations": []
                }],
                "corrections": [
                    "Last-resort fallback: LLM produced corrupted JSON field names. "
                    "The system extracted the answer text and constructed a minimal response."
                ]
            }
            if distill_meta:
                result["_distillation_meta"] = distill_meta
            return result, input_tokens, output_tokens
        # --- END LAST-RESORT FALLBACK ---

        return {"status": "error", "error_message": "Failed to generate valid report JSON.", "data": str(e)}, input_tokens, output_tokens

# --- MODIFICATION START: Add user_uuid ---
async def _invoke_complex_prompt_report_task(STATE: dict, command: dict, workflow_state: dict, user_uuid: str = None, session_id: str = None, call_id: str | None = None) -> tuple[dict, int, int]:
# --- MODIFICATION END ---
    llm_instance, _turn_provider, _turn_model = turn_llm(STATE, user_uuid)
    prompt_goal = command.get("arguments", {}).get("prompt_goal", "No prompt goal provided.")
    final_call_id = call_id or str(uuid.uuid4())

    distilled_data, distill_meta = _distill_workflow_for_report(workflow_state)
    distilled_json = json.dumps(distilled_data, indent=2)

    final_summary_prompt_text = (
        "You are an expert technical writer and data analyst. Your task is to synthesize all the collected data from a completed workflow into a formal, structured report that fulfills the original prompt's goal.\n\n"
        f"--- ORIGINAL PROMPT GOAL ---\n{prompt_goal}\n\n"
        f"--- ALL COLLECTED DATA ---\n{distilled_json}\n\n"
        "--- INSTRUCTIONS ---\n"
        "Your response MUST be a single JSON object that strictly follows the schema for a `PromptReportResponse`.\n\n"
        "--- FIELD GUIDELINES ---\n"
        "1.  `title`: REQUIRED. A clear, professional title for the report, derived from the ORIGINAL PROMPT GOAL.\n"
        "2.  `executive_summary`: REQUIRED. A concise, high-level summary paragraph explaining the key findings of the analysis.\n"
        "3.  `report_sections`: REQUIRED. A list of objects, where each object represents a logical section of the report. Each section object MUST have:\n"
        "    - `title`: The title for that specific section (e.g., 'Data Quality Analysis', 'Table DDL').\n"
        "    - `content`: The detailed findings for that section, formatted in markdown. You can use lists, bolding, code blocks, and MARKDOWN TABLES for clarity. When the prompt goal asks for tables, heatmaps, or rankings, you MUST include them as markdown tables inside the content string — do not defer rendering to the UI or use placeholders. Compute actual values from the data. IMPORTANT: Do NOT start the content with a markdown heading that repeats the section title — the title is already rendered separately by the UI.\n\n"
        "--- STATISTICAL DATA USAGE ---\n"
        "When the collected data includes `column_statistics` in metadata, use these pre-computed statistics "
        "for precise quantitative claims (min, max, mean, median, percentiles, value distributions). "
        "These statistics cover the FULL dataset, not just the sample rows."
    )

    raw_data_chars = len(json.dumps(workflow_state, indent=2))
    prompt_chars = len(final_summary_prompt_text)
    app_logger.info(
        f"TDA_ComplexPromptReport: raw workflow_state={raw_data_chars:,} chars, "
        f"distilled prompt={prompt_chars:,} chars"
    )

    reason = f"Client-Side Tool Call: TDA_ComplexPromptReport\nGoal: {prompt_goal}"

    # --- MODIFICATION START: Pass user_uuid ---
    response_text, input_tokens, output_tokens, _, _ = await llm_handler.call_llm_api(
        llm_instance=llm_instance,
        current_provider=_turn_provider,
        current_model=_turn_model,
        prompt=final_summary_prompt_text,
        reason=reason,
        system_prompt_override="You are a JSON-only reporting assistant.",
        raise_on_error=True,
        user_uuid=user_uuid,
        session_id=session_id
    )
    # --- MODIFICATION END ---

    try:
        report_data, correction_descriptions = llm_handler.parse_and_coerce_llm_response(response_text, PromptReportResponse)

        result = {
            "status": "success",
            # --- MODIFICATION START: Add token counts to metadata ---
            "metadata": {
                "call_id": final_call_id,
                "tool_name": "TDA_ComplexPromptReport",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens
            },
            # --- MODIFICATION END ---
            "results": [report_data.model_dump()],
            "corrections": correction_descriptions
        }
        if distill_meta:
            result["_distillation_meta"] = distill_meta
        return result, input_tokens, output_tokens
    except (json.JSONDecodeError, ValidationError) as e:
        app_logger.error(f"Failed to parse/validate TDA_ComplexPromptReport: {e}. Response: {response_text}")
        return {"status": "error", "error_message": "Failed to generate valid report JSON.", "data": str(e)}, input_tokens, output_tokens

# --- MODIFICATION START: Add user_uuid ---
async def _invoke_util_calculate_date_range(STATE: dict, command: dict, user_uuid: str = None, session_id: str = None) -> dict:
# --- MODIFICATION END ---
    args = command.get("arguments", {})
    start_date_str = args.get("start_date")
    date_phrase = args.get("date_phrase", "").lower().strip()

    app_logger.info(f"Executing client-side tool: TDA_DateRange with start: '{start_date_str}', phrase: '{date_phrase}'")

    if not start_date_str or not date_phrase:
        return {"status": "error", "error_message": "Missing start_date or date_phrase."}

    # Guard against unresolved placeholder dicts from planner
    if not isinstance(start_date_str, str):
        return {"status": "error", "error_message": f"start_date must be a string (got {type(start_date_str).__name__}). Likely an unresolved placeholder from the plan."}

    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    end_date = None

    try:
        # Simplified deterministic logic - add more cases as needed
        if "yesterday" in date_phrase:
            start_date = start_date - timedelta(days=1)
            end_date = start_date
        elif "today" in date_phrase:
            end_date = start_date
        elif "past weekend" in date_phrase or "last weekend" in date_phrase:
            days_since_sunday = (start_date.weekday() - 6) % 7 # Sunday is 6
            end_date = start_date - timedelta(days=days_since_sunday + 1) # Go back to previous Sunday
            start_date = end_date - timedelta(days=1) # Previous Saturday
        elif "last week" in date_phrase or "past week" in date_phrase:
            start_of_last_week = start_date - timedelta(days=start_date.weekday() + 7)
            end_of_last_week = start_of_last_week + timedelta(days=6)
            start_date, end_date = start_of_last_week, end_of_last_week
        elif "last month" in date_phrase or "past month" in date_phrase:
            first_day_of_current_month = start_date.replace(day=1)
            last_day_of_last_month = first_day_of_current_month - timedelta(days=1)
            first_day_of_last_month = last_day_of_last_month.replace(day=1)
            start_date, end_date = first_day_of_last_month, last_day_of_last_month
        elif "last year" in date_phrase or "past year" in date_phrase:
            first_day_of_last_year = start_date.replace(year=start_date.year - 1, month=1, day=1)
            last_day_of_last_year = start_date.replace(year=start_date.year - 1, month=12, day=31)
            start_date, end_date = first_day_of_last_year, last_day_of_last_year
        else:
            match = re.search(r'(\d+)\s+(day|week|month|year)s?', date_phrase)
            if match:
                quantity = int(match.group(1))
                unit = match.group(2)

                if "past" in date_phrase or "last" in date_phrase:
                    if unit == "day":
                        end_date = start_date - timedelta(days=1)
                        start_date = end_date - timedelta(days=quantity - 1)
                    elif unit == "week":
                        # End of previous week (Sunday)
                        end_date = start_date - timedelta(days=start_date.weekday() + 1)
                        # Start of N-1 weeks before that (Monday)
                        start_date = end_date - timedelta(weeks=quantity - 1, days=6)
                    elif unit == "month":
                        # Last day of previous month
                        end_date = start_date.replace(day=1) - timedelta(days=1)
                        # First day of N-1 months before that
                        start_date = end_date.replace(day=1)
                        for _ in range(quantity - 1):
                             start_date = (start_date - timedelta(days=1)).replace(day=1)
                    elif unit == "year":
                        # Last day of previous year
                        end_date = start_date.replace(month=1, day=1) - timedelta(days=1)
                        # First day of N-1 years before that
                        start_date = end_date.replace(year=end_date.year - (quantity - 1), month=1, day=1)

    except Exception as e:
        app_logger.warning(f"Deterministic date parsing failed with error: {e}. This may be expected for complex phrases.")
        end_date = None # Ensure fallback if deterministic logic fails

    # Fallback to LLM if deterministic logic didn't find a range
    if end_date is None:
        app_logger.info(f"Deterministic logic failed for '{date_phrase}'. Falling back to LLM-based date extraction.")

        llm_prompt = (
            f"Given the current date is {start_date_str}, analyze the phrase '{date_phrase}'. "
            "Determine the exact start and end dates for this phrase. "
            "Your response MUST be ONLY a single, valid JSON object with two keys: 'start_date' and 'end_date', both in 'YYYY-MM-DD' format."
        )

        # --- MODIFICATION START: Pass user_uuid ---
        response_text, _, _, _, _ = await llm_handler.call_llm_api(
            llm_instance=STATE.get('llm'),
            prompt=llm_prompt,
            reason=f"LLM fallback for complex date phrase: {date_phrase}",
            system_prompt_override="You are a helpful assistant that only responds with valid JSON.",
            raise_on_error=True,
            user_uuid=user_uuid,
            session_id=session_id
        )
        # --- MODIFICATION END ---

        try:
            # Added extraction logic for robustness
            json_match = re.search(r"```json\s*\n(.*?)\n\s*```|(\{.*\})", response_text, re.DOTALL)
            if not json_match: raise json.JSONDecodeError("No JSON found in LLM response", response_text, 0)
            json_str = json_match.group(1) or json_match.group(2)

            date_data = json.loads(json_str)
            start_date = datetime.strptime(date_data['start_date'], '%Y-%m-%d').date()
            end_date = datetime.strptime(date_data['end_date'], '%Y-%m-%d').date()
        except (json.JSONDecodeError, KeyError, ValueError, AttributeError) as e:
            error_msg = f"LLM fallback for date range failed to produce a valid result. Response: '{response_text}'. Error: {e}"
            app_logger.error(error_msg)
            return {"status": "error", "error_message": error_msg}

    # Generate the list of dates
    date_list = []
    current_date = start_date
    while current_date <= end_date:
        date_list.append({"date": current_date.strftime('%Y-%m-%d')})
        current_date += timedelta(days=1)

    return {
        "status": "success",
        "metadata": {"tool_name": "TDA_DateRange"},
        "start_date": date_list[0]["date"],
        "end_date": date_list[-1]["date"],
        "results": date_list
    }

# --- MODIFICATION START: Add user_uuid ---
# PROP-006 phase 2 — where the call in flight was routed (set by `_invoke_mcp_tool_impl`): a
# platform connector, or the MCP server id `resolve_mcp_target` chose. A mutable holder in a
# context variable, read by `invoke_mcp_tool` after the call to name the server on the result.
_MCP_ROUTE: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("mcp_call_route", default=None)


def _note_route(**kw) -> None:
    h = _MCP_ROUTE.get()
    if isinstance(h, dict):
        h.update(kw)


def _stamp_mcp_source(result, tool_name: str, route: dict) -> None:
    """Name the server a multi-server agent's MCP call went to on the call's own result
    (``mcp_source``), so the Live Status step shows it live and after a reload. Nothing for a
    single-server agent, a ``TDA_`` or connector tool, or a result that is not a dict."""
    try:
        from trusted_data_agent.core.tool_identity import turn_aliases as _ta, parse as _tp
        if not _ta() or not isinstance(result, dict) or not tool_name or str(tool_name).startswith("TDA_"):
            return
        if route.get("connector"):
            return
        sid = route.get("server_id")
        if not sid and not _tp(tool_name).alias:
            # refused before routing (a gate, authority): a plain name is the first server's
            # only when no platform connector owns it
            from trusted_data_agent.core.platform_connector_registry import get_connector_for_tool as _gc
            if _gc(tool_name):
                return
        from trusted_data_agent.core.agent_mcp_servers import call_source as _cs
        src = _cs(tool_name, sid)
        if src:
            result.setdefault("mcp_source", src)
    except Exception:  # noqa: BLE001 — naming the server must never break the call
        pass


def turn_server_id(STATE, user_uuid: str):
    """The MCP server a call made inside this turn goes to.

    Order: the per-turn overlay's ``mcp_server_id`` slot (an ``@TAG`` override that loaded its own
    client) → the turn's PRIMARY server armed at the execution seam (``tool_identity``, a context
    variable inherited by the tasks the turn spawns) → the per-user id, which is only honest for a
    caller outside any turn: every REST query rewrites it, so two concurrent turns of one user on
    different MCP entries raced on it and a call went to the other turn's server (OC-F1)."""
    _ov = _overlay_map(STATE, 'mcp_server_id')
    if _ov is not None:
        return _ov['mcp_server_id']
    try:
        from trusted_data_agent.core.tool_identity import turn_primary_server as _primary
        armed = _primary()
    except Exception:  # noqa: BLE001
        armed = None
    return armed or get_user_mcp_server_id(user_uuid)


async def invoke_mcp_tool(STATE: dict, command: dict, user_uuid: str = None, session_id: str = None, call_id: str | None = None, workflow_state: dict = None, profile_id: str = None) -> tuple[any, int, int]:
    """Invoke one tool. PROP-006 phase 2: a tool of one of the agent's ADDITIONAL MCP servers runs
    under that server's preflight source (its own database list and facts, O5/O6); every other
    call — every call of a single-server agent — goes straight through. A multi-server agent's
    result names the server that answered (``mcp_source``)."""
    from trusted_data_agent.core.tool_identity import parse as _tp, turn_server_for as _tsf, turn_aliases as _ta
    _tool = (command or {}).get("tool_name")
    _alias = _tp(_tool).alias
    _sid = _tsf(_alias) if _alias else None
    # a multi-server turn records where the call was routed; a single-server turn arms nothing
    _route: dict = {}
    _tok = _MCP_ROUTE.set(_route) if _ta() else None
    _routed = None
    try:
        if _tok is not None:
            out, _routed = await _invoke_routed(STATE, command, user_uuid, session_id, call_id,
                                                workflow_state, profile_id, _tool, _sid)
        elif not _sid:
            out = await _invoke_mcp_tool_impl(STATE, command, user_uuid, session_id, call_id, workflow_state, profile_id)
        else:
            from trusted_data_agent.agent.preflight.dialect import additional_mcp_scope
            with additional_mcp_scope(_sid):
                out = await _invoke_mcp_tool_impl(STATE, command, user_uuid, session_id, call_id, workflow_state, profile_id)
    finally:
        if _tok is not None:
            _MCP_ROUTE.reset(_tok)
    if _tok is not None and isinstance(out, tuple) and out:
        _stamp_mcp_source(out[0], _tool, _route)
        if _routed and isinstance(out[0], dict):
            _stamp_mcp_reroute(out[0], _routed)
    return out


def _stamp_mcp_reroute(result: dict, routed: dict) -> None:
    """A re-routed call names it twice: on the Server row (``mcp_source.reason = routed``, the
    tool the model called and the server it would have reached) and in a note the agent reads."""
    try:
        src = result.get("mcp_source")
        if isinstance(src, dict):
            from trusted_data_agent.agent.source_routing import routed_source as _rs
            result["mcp_source"] = _rs(routed, fallback=src) or src
        result["routing_note"] = routed["note"]
    except Exception:  # noqa: BLE001
        pass


async def _invoke_routed(STATE, command, user_uuid, session_id, call_id, workflow_state, profile_id,
                         tool_name, target_sid):
    """PROP-006 phase 2, B10 — a multi-server agent's call goes to the server that holds the data
    it names (``agent/source_routing.py``): before the call when the called server is known not to
    hold it and exactly one other does; after a "cannot read that" refusal, once. Returns
    ``(out, routed | None)``. With routing off, a single server, a write, a ``TDA_`` or connector
    tool, the call runs exactly as before."""
    from trusted_data_agent.agent import source_routing as _sr
    from trusted_data_agent.agent.preflight.dialect import additional_mcp_scope

    async def _run(cmd, sid):
        with additional_mcp_scope(sid):
            return await _invoke_mcp_tool_impl(STATE, cmd, user_uuid, session_id, call_id, workflow_state, profile_id)

    primary = next((s.server_id for s in _sr.turn_servers() if s.primary), None)
    target = target_sid or primary
    if not _sr.call_routable(tool_name, target):
        return await _run(command, target_sid), None

    # WHERE the call runs is the shared decision (source_routing.plan_before / plan_after, the
    # same functions the ReAct lane calls); this lane only runs it and records the outcome.
    args = dict(command.get("arguments") or {})
    avail = (None if not hasattr(STATE, "get") or STATE.get("_no_catalog")
             else set((STATE.get("mcp_tools") or {}).keys()))
    user = _sr.turn_user() or user_uuid
    lane = _sr.SOURCE_LANE_OPTIMIZE
    planned = command.get("planned_server") if isinstance(command, dict) else None

    d = await _sr.plan_before(tool_name, target, args, lane=lane, available=avail, planned_server=planned)
    if d.action == _sr.REFUSE:
        app_logger.info(f"[source routing] '{tool_name}' refused before running: {d.databases}")
        _res: dict = {"status": "error", "error": d.refusal, "data": d.refusal}
        if d.split:
            # stage 3b: machine-readable, so the phase executor splits the statement itself
            from trusted_data_agent.agent.cross_source import SPANS_CODE
            _res["code"] = SPANS_CODE
            _res["split"] = dict(d.split)
        return (_res, 0, 0), None
    if d.action == _sr.REROUTE:
        app_logger.info(f"[source routing] '{tool_name}' → '{d.to.tool_name}' (server {d.to.server_id} holds {d.databases})")
        out = await _run(dict(command, tool_name=d.to.tool_name, arguments=args), d.to.server_id)
        res = out[0] if isinstance(out, tuple) and out else None
        if isinstance(res, dict) and res.get("status") != "error" and d.databases:
            _sr.learn_outcome(user, d.to.server_id, d.databases, held=True)
        return out, d.info

    out = await _run(command, target_sid)
    res = out[0] if isinstance(out, tuple) and out else None
    if not (isinstance(res, dict) and res.get("status") == "error"):
        return out, None
    unreachable = bool((_MCP_ROUTE.get() or {}).get("unreachable"))
    if unreachable:
        # B9: named, recovery-shaped — the model must not retry a server that is down, nor read
        # a missing source as missing data (the transport detail stays in `data`). Recorded in the
        # turn's one record, so the next turn names the server at its start (stage 1).
        _sr.note_unreachable(target, str(res.get("data") or res.get("error") or "")[:240])
        res["error"] = _sr.unreachable_text(target, tool_name)
    err = f"{res.get('error') or ''}\n{res.get('data') or ''}"
    d = await _sr.plan_after(tool_name, target, args, err, lane=lane, available=avail, unreachable=unreachable)
    if d.action == _sr.REROUTE:
        app_logger.info(f"[source routing] '{tool_name}' refused by server {target} — retrying on '{d.to.tool_name}'")
        out2 = await _run(dict(command, tool_name=d.to.tool_name, arguments=args), d.to.server_id)
        res2 = out2[0] if isinstance(out2, tuple) and out2 else None
        if isinstance(res2, dict) and res2.get("status") != "error":
            if d.databases:
                _sr.learn_outcome(user, d.to.server_id, d.databases, held=True)
            return out2, d.info
        if isinstance(res2, dict) and d.databases:
            _sr.learn_outcome(user, d.to.server_id, d.databases, held=False)
    elif d.action == _sr.REFUSE:
        res["data"] = f"{res.get('data') or ''}\n{d.refusal}"
    return out, None


async def _invoke_mcp_tool_impl(STATE: dict, command: dict, user_uuid: str = None, session_id: str = None, call_id: str | None = None, workflow_state: dict = None, profile_id: str = None) -> tuple[any, int, int]:
# --- MODIFICATION END ---
    mcp_client = STATE.get('mcp_client')
    tool_name = command.get("tool_name")

    # PROP-011: nothing runs past a question for the user — every tool, framework and component
    # tools included (the turn's own reporting tools are exempt inside the ledger). The SQL gate
    # below repeats the check for data tools; this one covers the TDA_ tools it does not see.
    try:
        from trusted_data_agent.agent import input_requests as _ir_mod
        _ir_block = _ir_mod.refusal_for(tool_name)
    except Exception:  # noqa: BLE001
        _ir_block = None
    if _ir_block is not None:
        return _ir_block, 0, 0

    # The one SQL gate (agent/tool_enforcement.py — shared with the ReAct wrapper and the
    # Database Navigator's query action): Agent Authority (fail-closed, autonomous turns only;
    # a raw-SQL write is classified by the statement, not the tool name), non-string SQL
    # (definitive), the in-flight loop guard, and preflight SQL grounding. The Optimize lane
    # has no per-call budget gate (the phase loop skips what the budget cannot afford) and no
    # preflight inline rewrites (its correction chain applies them as System Self-Correction
    # steps); it does run the fixes every lane runs before anything reads the statement (an
    # unquoted date → a date literal), and a statement reading FROM a plan step's output is
    # refused before it reaches the database.
    # Framework TDA_ tools pass untouched.
    if tool_name and not str(tool_name).startswith("TDA_"):
        from trusted_data_agent.agent.tool_enforcement import LANE_OPTIMIZE, sql_gate_before
        _refusal, _fix, _kind = sql_gate_before(
            tool_name, command.get("arguments") or {}, session_id, LANE_OPTIMIZE)
        if _refusal is not None:
            return _refusal, 0, 0
        if _fix is not None:   # a mechanical rewrite every lane runs (e.g. an unquoted date)
            app_logger.info(f"SQL gate rewrote the statement before running it ({_kind}): {_fix.get('changes')}")
            command.setdefault("arguments", {})["sql"] = _fix["sql"]

    # --- MODIFICATION START: Add handler for the new TDA_ContextReport tool ---
    if tool_name == "TDA_ContextReport":
        app_logger.info("Executing client-side tool: TDA_ContextReport")
        answer = command.get("arguments", {}).get("answer_from_context", "The agent has completed its work.")
        result = {
            "status": "success",
            "metadata": {"tool_name": "TDA_ContextReport"},
            "results": [{"response": answer}]
        }
        return result, 0, 0
    # --- MODIFICATION END ---

    if tool_name == "TDA_LLMFilter":
        # --- MODIFICATION START: Pass user_uuid ---
        return await _invoke_llm_filter_task(STATE, command, user_uuid=user_uuid, session_id=session_id, call_id=call_id)
        # --- MODIFICATION END ---

    if tool_name == "TDA_FinalReport":
        command.setdefault("arguments", {})["user_question"] = workflow_state.get("original_user_input", "N/A")
        # --- MODIFICATION START: Pass user_uuid and workflow_state correctly ---
        return await _invoke_final_report_task(STATE, command, workflow_state, user_uuid=user_uuid, session_id=session_id, call_id=call_id)
        # --- MODIFICATION END ---

    if tool_name == "TDA_ComplexPromptReport":
        command.setdefault("arguments", {})["prompt_goal"] = workflow_state.get("workflow_goal_prompt", "N/A")
        # --- MODIFICATION START: Pass user_uuid and workflow_state correctly ---
        return await _invoke_complex_prompt_report_task(STATE, command, workflow_state, user_uuid=user_uuid, session_id=session_id, call_id=call_id)
        # --- MODIFICATION END ---

    if tool_name == "TDA_LLMTask":
        args = command.get("arguments", {})
        # Ensure 'mode' and 'session_history' are popped so they aren't passed to the core logic
        mode = args.pop("mode", "standard")
        session_history = args.pop("session_history", None)
        # Reconstruct the command with the remaining args for the core task function
        command_for_core = {"tool_name": "TDA_LLMTask", "arguments": args}
        # --- MODIFICATION START: Pass user_uuid and workflow_state correctly ---
        # Note: workflow_state contains the *entire* state, including results from previous phases.
        # _invoke_core_llm_task will handle extracting the relevant parts based on 'source_data' or using 'data' if present.
        return await _invoke_core_llm_task(STATE, command_for_core, workflow_state=workflow_state, user_uuid=user_uuid, session_history=session_history, mode=mode, session_id=session_id, call_id=call_id)
        # --- MODIFICATION END ---


    if tool_name == "TDA_CurrentDate":
        app_logger.info("Executing client-side tool: TDA_CurrentDate")
        current_date = datetime.now().strftime('%Y-%m-%d')
        result = {
            "status": "success",
            "metadata": {"tool_name": "TDA_CurrentDate"},
            "results": [{"current_date": current_date}]
        }
        return result, 0, 0

    if tool_name == "TDA_DateRange":
        # --- MODIFICATION START: Pass user_uuid ---
        result = await _invoke_util_calculate_date_range(STATE, command, user_uuid=user_uuid, session_id=session_id)
        # --- MODIFICATION END ---
        return result, 0, 0

    # --- Component handler routing ---
    # Route component tool calls through ComponentManager when available.
    # Falls back to legacy handler if component system unavailable.
    _comp_owned = False   # once True, a failure must NEVER fall through to MCP
    try:
        from trusted_data_agent.components.manager import get_component_manager
        comp_manager = get_component_manager()
        if comp_manager.is_component_tool(tool_name):
            _comp_owned = True
            handler = comp_manager.get_handler(tool_name)
            if handler:
                app_logger.info(f"Routing '{tool_name}' through component handler: {handler.component_id}")
                # Agent Authority (P3): component tools are TDA_* named, so the blanket TDA_
                # exemption above skips them — but a component IS a governable capability, so
                # enforce here by component id OR tool name (matching the LangChain path). Not
                # write-gated (components are capabilities, not raw data writes).
                try:
                    from trusted_data_agent.core import agent_authority as _agent_authority
                    _c_allowed, _c_reason = _agent_authority.enforce_tool(
                        handler.component_id, is_write=False,
                        aliases=frozenset({tool_name}))
                    if not _c_allowed:
                        app_logger.warning(
                            f"agent-authority: DENIED component '{handler.component_id}' — {_c_reason}")
                        return _agent_authority.denied_tool_result(handler.component_id, _c_reason), 0, 0
                except Exception:
                    pass  # enforce_tool fails closed; a bad import must not break tools

                # Component ENABLEMENT at the effect site. The advertise-time filter
                # (get_active_components) is the primary control, but a call can arrive
                # unadvertised — replayed champion plan, pack-authored plan, @TAG override,
                # leaked-call recovery, hallucination. Without this, "disabled" held only
                # for well-behaved turns. See
                # docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md.
                try:
                    from trusted_data_agent.components import enablement as _enablement
                    _prof_cfg = {}
                    if profile_id and user_uuid:
                        try:
                            from trusted_data_agent.core.config_manager import ConfigManager
                            _prof_cfg = ConfigManager().get_profile(profile_id, user_uuid) or {}
                        except Exception:
                            _prof_cfg = {}
                    _deny = _enablement.enforce_component_call(
                        comp_manager.get_component(handler.component_id), _prof_cfg, user_uuid)
                    if _deny:
                        app_logger.warning(
                            f"component-enablement: DENIED '{handler.component_id}' — not enabled for profile")
                        return {
                            "status": "error",
                            "error": _deny,
                            "metadata": {"tool_name": tool_name,
                                         "component_id": handler.component_id,
                                         "denied_reason": "component_not_enabled"},
                        }, 0, 0
                except Exception:
                    pass  # fail open — the advertise-time filter remains the control

                args = command.get("arguments", {})
                # Component signals (components/signals.py): a `source_ref` names an earlier
                # result of this session; the ledger resolves it and fills an empty `content` /
                # `data` from it (the dashboard's declared implicit source is the latest data
                # result — what its private session cache used to be). Resolved after the
                # context exists, below.
                # Cache the original user question so _handle_visualize can use it
                # as a clean source_prompt (the LLM's 'query' arg is often polluted
                # with result data or left empty).
                if session_id and workflow_state:
                    _ui = workflow_state.get("original_user_input", "")
                    if _ui:
                        _user_input_cache[session_id] = _ui

                # Build context for component handlers (session info + LLM callable)
                _profile_tag = ""
                if profile_id and user_uuid:
                    try:
                        from trusted_data_agent.core.config_manager import ConfigManager
                        _p = ConfigManager().get_profile(profile_id, user_uuid)
                        if _p:
                            _profile_tag = _p.get("tag", "")
                    except Exception:
                        pass
                comp_context: dict = {"session_id": session_id, "user_uuid": user_uuid, "profile_id": profile_id, "profile_tag": _profile_tag, "user_input": _user_input_cache.get(session_id, "")}
                # Turn number lets TDA_Dashboard stamp source_turn_id on created
                # tiles (binds MCP-refresh provenance to the originating turn).
                if isinstance(workflow_state, dict) and workflow_state.get("current_turn_number") is not None:
                    comp_context["current_turn_number"] = workflow_state.get("current_turn_number")
                _llm = STATE.get('llm')
                if _llm:
                    async def _comp_llm_callable(prompt, system_prompt="You are a data analyst.", max_tokens=200):
                        from trusted_data_agent.llm.handler import call_llm_api
                        text, in_tok, out_tok, _, _ = await call_llm_api(
                            _llm, prompt, user_uuid=user_uuid, session_id=session_id,
                            system_prompt_override=system_prompt,
                            reason="Component mapping resolution",
                            disabled_history=True)
                        return {"content": text, "input_tokens": in_tok, "output_tokens": out_tok}
                    comp_context["llm_callable"] = _comp_llm_callable
                from trusted_data_agent.components import signals as _signals
                _comp_def = comp_manager.get_component(handler.component_id)
                args = _signals.before_process(args, comp_context,
                                               getattr(_comp_def, "manifest", None),
                                               getattr(_comp_def, "tool_definition", None))
                payload = await handler.process(args, comp_context)
                _signals.after_process(payload, comp_context, tool_name, args)
                if payload.spec.get("error"):
                    # The MESSAGE is the self-correction food — a query_product
                    # REJECT names the closest declared member and the whole
                    # admissible set precisely so the next call can be right. A
                    # constant headline buries that: every surface reading `error`
                    # saw "Component Error" and the agent had to dig through `data`
                    # for the half that tells it how to recover. Carry it in both.
                    _comp_err_msg = str(payload.spec["error"])
                    result = {"status": "error", "error": _comp_err_msg,
                              "data": _comp_err_msg}
                else:
                    result = {"type": payload.component_id, "spec": payload.spec, "metadata": payload.metadata}
                    # The reader's pre-rendered card rides along (the ReAct lane carries it on
                    # the tool artifact); the Optimize final-answer seam hands it to
                    # generate_component_html, where html wins over spec.
                    if payload.html:
                        result["html"] = payload.html
                        if payload.title:
                            result["title"] = payload.title      # the evidence row's summary line
                _comp_in = payload.metadata.get("llm_input_tokens", 0)
                _comp_out = payload.metadata.get("llm_output_tokens", 0)
                return result, _comp_in, _comp_out
    except Exception as comp_err:
        if _comp_owned:
            # The tool is KNOWN to belong to a component — forwarding it to the
            # MCP server can only produce a nonsense "Unknown tool" error (it
            # did, live: a crashed query_product handler sent a perfectly valid
            # governed request to the Teradata MCP server). A component crash is
            # a structured tool error the correction machinery can read — the
            # governance chokepoint fails CLOSED, never open to another system.
            app_logger.error(
                f"Component handler for '{tool_name}' crashed (not forwarded to MCP): "
                f"{type(comp_err).__name__}: {comp_err}", exc_info=True)
            return {"status": "error",
                    "error": f"component tool '{tool_name}' failed internally: "
                             f"{type(comp_err).__name__}: {comp_err}. The call was NOT "
                             f"forwarded to any other system.",
                    "metadata": {"tool_name": tool_name, "component_error": True}}, 0, 0
        app_logger.warning(f"Component handler routing failed for '{tool_name}', using legacy: {comp_err}")

    if tool_name == "TDA_Charting":
        app_logger.info(f"Handling abstract chart generation for: {command} (legacy path)")

        try:
            args = command.get("arguments", {})
            data = args.get("data")
            # --- Robust Data Transformation ---
            data = _transform_chart_data(data)

            if not isinstance(data, list) or not data:
                # Handle empty list case specifically
                if isinstance(data, list) and not data:
                     error_detail = "The 'data' argument was an empty list. Cannot generate chart without data."
                else: # Handle non-list case
                     error_detail = "The 'data' argument must be a non-empty list of dictionaries."
                result = {"status": "error", "error": "Validation failed", "data": error_detail}
                return result, 0, 0

            # Ensure data is list of dicts after transformation
            if not all(isinstance(item, dict) for item in data):
                result = {"status": "error", "error": "Validation failed", "data": "Transformed 'data' is not a list of dictionaries."}
                return result, 0, 0

            chart_spec = _build_g2plot_spec(args, data)

            result = {"type": "chart", "spec": chart_spec, "metadata": {"tool_name": "TDA_Charting"}}
            return result, 0, 0
        except Exception as e:
            app_logger.error(f"Error building G2Plot spec: {e}", exc_info=True)
            result = {"status": "error", "error": "Chart Generation Failed", "data": str(e)}
            return result, 0, 0

    # --- Argument Extraction and Normalization ---
    args = {}
    if isinstance(command, dict):
        potential_arg_keys = [
            "arguments", "args", "tool_args", "parameters",
            "tool_input", "action_input", "tool_arguments"
        ]

        found_args = None
        # Check top-level keys first
        for key in potential_arg_keys:
            if key in command and isinstance(command[key], dict):
                found_args = command[key]
                break

        # If not found, check nested structures (e.g., {"action": {"arguments": ...}})
        if found_args is None:
            possible_wrapper_keys = ["action", "tool"]
            for wrapper_key in possible_wrapper_keys:
                if wrapper_key in command and isinstance(command[wrapper_key], dict):
                    nested_dict = command[wrapper_key]
                    for arg_key in potential_arg_keys:
                        if arg_key in nested_dict and isinstance(nested_dict[arg_key], dict):
                            found_args = nested_dict[arg_key]
                            break # Found args in nested structure
                    if found_args is not None:
                        break # Found args, no need to check other wrapper keys

        # Fallback: Assume the whole command *might* be the args if no standard keys found
        if found_args is None and command and all(k not in command for k in ["tool_name", "action", "tool"]):
             app_logger.warning(f"Could not find standard argument key for tool '{tool_name}'. Assuming entire command object contains arguments: {command}")
             found_args = command

    # Ensure found_args is a dict before normalization
    args = found_args if isinstance(found_args, dict) else {}

    # Preserve the caller-provided argument names *before* canonicalization, so they can
    # be restored if no tool schema is available to realign against (see below).
    original_args = dict(args)

    normalized_args = _normalize_tool_arguments(args)
    if normalized_args != args:
        app_logger.debug(f"Normalized tool arguments for '{tool_name}'")

    args = normalized_args

    # --- MODIFICATION START: Implement Intelligent Argument Alignment ---
    # _normalize_tool_arguments() canonicalises argument synonyms (e.g. table_name ->
    # object_name). Those canonical names are an internal convention and frequently do
    # NOT match the literal parameter names a tool expects, so they must be realigned
    # back to the tool's own argument names using its schema.
    #
    # Schema resolution is provider-agnostic and uses a two-step fallback. A tool invoked
    # by an MCP *prompt* (or otherwise) can be executed even when it sits outside the
    # active profile's filtered tool set, in which case it is absent from
    # STATE['mcp_tools']; recover its schema from the full, unfiltered schema cache so
    # realignment still works. Without this, the destructively-canonicalised names (which
    # match no real tool parameter) would reach the server and be rejected.
    tool_def = STATE.get('mcp_tools', {}).get(tool_name)
    if not (tool_def and getattr(tool_def, 'args', None)):
        try:
            for _cache_entry in (STATE.get('mcp_tool_schema_cache', {}) or {}).values():
                if not isinstance(_cache_entry, dict):
                    continue
                for _cached_tool in _cache_entry.get('tools', []) or []:
                    if getattr(_cached_tool, 'name', None) == tool_name and getattr(_cached_tool, 'args', None):
                        tool_def = _cached_tool
                        break
                if tool_def and getattr(tool_def, 'args', None):
                    break
        except Exception as _schema_lookup_err:
            app_logger.debug(f"Schema-cache fallback lookup failed for '{tool_name}': {_schema_lookup_err}")

    aligned_args = args.copy()

    if tool_def and hasattr(tool_def, 'args') and isinstance(tool_def.args, dict) and tool_def.args:
        tool_arg_names = set(tool_def.args.keys()) # Names the actual tool expects
        canonical_to_synonyms_map = AppConfig.ARGUMENT_SYNONYM_MAP

        # Iterate through the *canonical* names we have after normalization
        for canonical_name in list(aligned_args.keys()):
            # If the canonical name itself is NOT what the tool expects...
            if canonical_name not in tool_arg_names:
                # ...find all synonyms for this canonical name.
                synonyms = canonical_to_synonyms_map.get(canonical_name, set())

                # Check if any of those synonyms ARE what the tool expects.
                for synonym in synonyms:
                    if synonym in tool_arg_names:
                        # Found a match! The tool wants this specific synonym.
                        app_logger.debug(f"Aligning argument for '{tool_name}': '{canonical_name}' -> '{synonym}'")
                        # Rename the key in our arguments to match the tool's expectation.
                        aligned_args[synonym] = aligned_args.pop(canonical_name)
                        break # Found the correct synonym, move to the next canonical name
    else:
        # No schema available anywhere (the tool is absent from both the live tool set
        # and the full schema cache). Canonicalisation cannot be safely reversed without
        # a schema, and the canonical names match no real tool parameter, so the server
        # would reject them. The caller (planner/LLM, prompted with the real tool
        # signatures) supplied argument names that match the tool — send those verbatim.
        if normalized_args != original_args:
            app_logger.warning(
                f"[MCP] No schema found for '{tool_name}'; sending caller-provided "
                f"argument names verbatim instead of canonicalised forms."
            )
        aligned_args = dict(original_args)
    # --- MODIFICATION END ---


    # --- Route platform connector tools (browser, files, shell, web, google, etc.) ---
    # These bypass the primary MCP server session and go directly to the platform connector.
    try:
        from trusted_data_agent.core.platform_connector_registry import (
            get_connector_for_tool as _get_connector_for_tool,
            invoke_connector_tool as _invoke_connector_tool,
        )
        platform_server_id = _get_connector_for_tool(tool_name)
        if platform_server_id:
            _note_route(connector=platform_server_id)
            app_logger.info(f"[Platform Connector] Routing '{tool_name}' → '{platform_server_id}'")
            result = await _invoke_connector_tool(platform_server_id, tool_name, aligned_args, user_uuid=user_uuid)
            # Tool-result firewall (observe-only): connector output — web pages, browser,
            # files, Google/Slack content — is the canonical indirect-injection lane (a
            # fetched page saying "ignore previous instructions"), and it previously RETURNED
            # here before the scan seam that covers MCP data rows. Screen it through the same
            # shared firewall entry; never altered, labels-only inbox event, fails open.
            try:
                from trusted_data_agent.agent.tool_enforcement import scan_result as _scan_result
                _scan_result(tool_name, result, user_uuid=user_uuid, session_id=session_id)
            except Exception:
                pass
            return result, 0, 0
    except Exception as _pe:
        app_logger.warning(f"Platform tool routing failed for '{tool_name}': {_pe}")
    # --- End platform tool routing ---

    app_logger.debug(f"[MCP] Calling tool '{tool_name}' with args: {aligned_args}")
    try:
        # The turn's own server, resolved in ONE place (`turn_server_id`): the overlay's slot, then
        # the primary armed at the execution seam, then — outside any turn — the per-user id. Never
        # the process-global client's server (another user's), never the per-user value from
        # inside a turn (another concurrent turn of this user may have just switched it).
        server_id = turn_server_id(STATE, user_uuid)
        # PROP-006 phase 2: a tool of one of the agent's ADDITIONAL servers (`<alias>__<name>`)
        # goes to THAT server's pooled client under the server's own name; the alias → server map
        # is the turn's (a context variable), never process-global state. With no aliases armed —
        # every single-server agent — the defaults come back unchanged.
        _call_client, server_id, _call_name = resolve_mcp_target(tool_name, mcp_client, server_id, user_uuid)
        _note_route(server_id=server_id)
        if not server_id:
            raise Exception("MCP server ID not found in configuration.")

        async def _do_mcp_call():
            async with _call_client.session(server_id) as temp_session:
                return await temp_session.call_tool(_call_name, aligned_args)

        try:
            call_tool_result = await asyncio.wait_for(_do_mcp_call(), timeout=_MCP_TOOL_TIMEOUT)
        except asyncio.TimeoutError:
            raise
        except Exception as _first:
            # The CONNECTION failed, not the tool (seen on the fusion bench: the first call after
            # a restart, "unhandled errors in a TaskGroup", cost an LLM correction round while the
            # identical call succeeded a second later). A READ is resent once — never a write,
            # which may already have run (tool_enforcement.transient_transport_error /
            # transport_retry_allowed, the same rule the ReAct wrapper applies).
            from trusted_data_agent.agent.tool_enforcement import (
                transient_transport_error as _transient, transport_retry_allowed as _may_resend)
            if not (_transient(_first) and _may_resend(tool_name, aligned_args)):
                raise
            app_logger.warning(f"[MCP] '{tool_name}': transient transport failure — retrying once")
            call_tool_result = await asyncio.wait_for(_do_mcp_call(), timeout=_MCP_TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        app_logger.error(f"⏱️ [MCP TIMEOUT] Tool '{tool_name}' timed out after {_MCP_TOOL_TIMEOUT}s. Server ID: {server_id}")
        # Recovery-shaped (the loop-guard precedent): a request that hit the ceiling will
        # hit it again if resent verbatim — three identical 120 s waits were one observed
        # turn. Name the ceiling and the two moves that change the outcome.
        result = {
            "status": "error",
            "error": (
                f"Tool '{tool_name}' exceeded the platform's {_MCP_TOOL_TIMEOUT}-second request ceiling "
                f"and was abandoned. Do NOT resend the same statement — it will time out again. "
                f"Rewrite it so it can finish: join tables ONLY on their declared keys (the "
                f"enrichment's DECLARED FOREIGN KEYS / natural key), filter to the smallest "
                f"population first, and avoid DISTINCT over concatenated columns and joins on "
                f"columns that merely share a name across tables. If no cheaper form exists, "
                f"report that the question needs a narrower scope instead of retrying."
            ),
            "data": f"Timeout after {_MCP_TOOL_TIMEOUT}s",
            "timed_out": True,
        }
        return result, 0, 0
    except Exception as e:
        app_logger.error(f"❌ [MCP CALL ERROR] Tool '{tool_name}' failed: {e}", exc_info=True)
        result = {"status": "error", "error": f"An exception occurred while invoking tool '{tool_name}'.", "data": str(e)}
        _transport = False
        try:   # the server did not answer at all (B9) — the router reads this on a multi-server turn
            from trusted_data_agent.agent.tool_enforcement import transient_transport_error as _tte
            _transport = _tte(e)
            if _transport:
                _note_route(unreachable=True)
        except Exception:  # noqa: BLE001
            pass
        if not _transport:
            # The server RAISED the tool's failure (FastMCP 4 answers a failed call with a protocol
            # error, where FastMCP 3 returned an `isError` result): the database's message is a
            # leaf two TaskGroups down, and `str(e)` is only "unhandled errors in a TaskGroup".
            # Found live (Optimize campaign OC-F14, 2026-10-29): every correction strategy that
            # reads the database's text went blind on the upgraded endpoint, and the LLM
            # correction re-proposed the identical failing SQL. Read it like an `isError` result.
            from trusted_data_agent.agent.tool_enforcement import tool_error_text as _tool_error_text
            _tool_text = _tool_error_text(e)
            if _tool_text:
                _first_line = next((ln.strip() for ln in _tool_text.splitlines() if ln.strip()), _tool_text)
                result = {"status": "error", "error": _first_line[:500], "data": _tool_text}
        try:
            from trusted_data_agent.agent.sql_dialect import db_abort_hint as _db_abort_hint
            _hint = _db_abort_hint(result["data"])
            if _hint:   # the database's own time ceiling: a resend cannot succeed, say what can
                result["data"] = f"{result['data']}\n{_hint}"
        except Exception:  # noqa: BLE001 — a hint must never break the error path
            pass
        return result, 0, 0

    # The server said the call FAILED (MCP `isError`): the text is the tool's error, not a
    # result to parse. Before, a Teradata message — "[Version 20.0.0.63] … [SQLState 42000] …"
    # — was mistaken for a JSON array, failed to parse, and reached the model and the
    # correction chain labelled "Tool returned malformed JSON string". The full text stays in
    # `data` exactly as before (every correction strategy matches on it).
    if getattr(call_tool_result, "isError", False):
        _err_text = "\n".join(getattr(c, "text", "") for c in (getattr(call_tool_result, "content", None) or [])
                              if isinstance(getattr(c, "text", None), str)) or str(call_tool_result)
        _first_line = next((ln.strip() for ln in _err_text.splitlines() if ln.strip()), _err_text)
        app_logger.warning(f"Tool '{tool_name}' reported an error: {_first_line[:300]}")
        return {"status": "error", "error": _first_line[:500], "data": _err_text}, 0, 0

    # --- Robust Result Parsing ---
    # Try to parse the standard content structure
    if hasattr(call_tool_result, 'content') and isinstance(call_tool_result.content, list) and len(call_tool_result.content) > 0:
        text_content_obj = call_tool_result.content[0]
        if hasattr(text_content_obj, 'text') and isinstance(text_content_obj.text, str):
            raw_text = text_content_obj.text
            try:
                # Attempt to find JSON within the string
                json_match = re.search(r'\{.*\}|\[.*\]', raw_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    result = json.loads(json_str)
                    # Add metadata if missing and result looks like success
                    if isinstance(result, dict) and "metadata" not in result and "status" not in result:
                         result["metadata"] = {"tool_name": tool_name}
                         result["status"] = "success"
                    # --- MODIFICATION START: Add tool_name to existing metadata if missing ---
                    elif isinstance(result, dict) and "metadata" in result and isinstance(result["metadata"], dict) and "tool_name" not in result["metadata"]:
                         result["metadata"]["tool_name"] = tool_name
                    # --- MODIFICATION END ---
                    # The data result enters the session's component ledger under a stable
                    # ref (components/signals.py) — a later TDA_Dashboard / TDA_Canvas /
                    # TDA_Charting / TDA_Files call names it with `source_ref`, and the
                    # dashboard's declared implicit source is the latest of these.
                    if session_id:
                        try:
                            from trusted_data_agent.components import signals as _signals
                            _signals.record_data_result(
                                session_id, tool_name, result, _extract_first_html(result),
                                turn=(workflow_state or {}).get("current_turn_number")
                                if isinstance(workflow_state, dict) else None)
                        except Exception:  # noqa: BLE001 — the ledger never breaks a tool result
                            pass
                    # The one SQL gate's after-steps, on the tool's output: the tool-result
                    # firewall (observe-only), the catalog / date-shape / value learning, the
                    # turn's result-set registry (S3), the turn budget's step (S5 — the plan
                    # is fixed, so its note is not needed here), and the value-existence
                    # guard's note for an EMPTY result on an unverified literal, stamped on
                    # the result so a zero is not reported as an absence.
                    try:
                        from trusted_data_agent.agent.tool_enforcement import sql_gate_after
                        _vn, _bn = sql_gate_after(tool_name, command.get("arguments", {}), result,
                                                  session_id, user_uuid=user_uuid,
                                                  source=(f"mcp:{server_id}" if server_id else ""))
                        if _vn and isinstance(result, dict):
                            result["preflight_note"] = _vn["note"]
                            # Only the value check names unverified literals; a decimal note alone
                            # must not stamp an empty list the honesty gate would read as a finding.
                            if _vn.get("unverified"):
                                result["preflight_unverified"] = [
                                    f"{u['leaf']}.{u['column']}='{u['literal']}'" for u in _vn["unverified"]]
                    except Exception:
                        pass
                    return result, 0, 0
                else:
                    # If no JSON found, treat as potential error string
                    app_logger.warning(f"Tool '{tool_name}' returned a non-JSON string: '{raw_text}' - treating as error.")
                    result = {"status": "error", "error": "Tool returned non-JSON string", "data": raw_text}
                    return result, 0, 0
            except json.JSONDecodeError:
                app_logger.warning(f"Tool '{tool_name}' returned a malformed JSON string: '{raw_text}' - treating as error.")
                result = {"status": "error", "error": "Tool returned malformed JSON string", "data": raw_text}
                return result, 0, 0

    # Fallback for unexpected formats - log and return error
    app_logger.error(f"Unexpected tool result format for '{tool_name}': {call_tool_result}")
    result = {"status": "error", "error": "Unexpected tool result format", "data": str(call_tool_result)}
    return result, 0, 0