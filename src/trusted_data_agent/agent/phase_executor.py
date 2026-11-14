# trusted_data_agent/agent/phase_executor.py
import re
import difflib
import json
import logging
import copy
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Tuple, Dict, Any, List
from abc import ABC, abstractmethod

from trusted_data_agent.core import session_manager
from trusted_data_agent.mcp_adapter import adapter as mcp_adapter
from trusted_data_agent.core.config import APP_CONFIG, AppConfig
from trusted_data_agent.agent.prompts import (
    WORKFLOW_TACTICAL_PROMPT,
)
from trusted_data_agent.agent import orchestrators
from trusted_data_agent.agent.response_models import CanonicalResponse
from trusted_data_agent.core.utils import get_argument_by_canonical_name
from trusted_data_agent.agent.json_utils import robust_json_parse
from trusted_data_agent.core.tool_identity import parse as _tool_parse, qualify as _tool_qualify
from trusted_data_agent.agent import schema_facts as _sf


if TYPE_CHECKING:
    from trusted_data_agent.agent.executor import PlanExecutor, DefinitiveToolError


app_logger = logging.getLogger("quart.app")


def _needs_input_carrier_text() -> str:
    """PROP-011 — the self-correction prompt's `{needs_input_carrier}`: the ASK_USER: format
    while the model may ask (the needs_input component in use for the agent), else nothing."""
    try:
        from trusted_data_agent.agent import input_requests as _ir_mod
        return _ir_mod.carrier_section("correction")
    except Exception:  # noqa: BLE001
        return ""


DEFINITIVE_TOOL_ERRORS = {
    # QW8 (assessment §2): the keys are used only as `re.search` patterns. Bare substrings
    # ("3523", "Invalid query") false-matched any error/data text merely CONTAINING those
    # characters — a result row value, a longer code like 35234, a table named "…3523…" —
    # turning a recoverable error into a turn/loop-halting DefinitiveToolError. Word-boundary
    # anchors keep the intended matches while removing the incidental-substring false aborts.
    r"\bInvalid query\b": "The generated query was invalid and could not be run against the database.",
    r"\b3523\b": "The user does not have the necessary permissions for the requested object.",
    # The adapter's fail-fast for a SQL argument that resolved to a non-string
    # (an upstream reference that produced no SQL). Retrying cannot change what
    # the upstream phase produced, so a correction round is pure waste.
    r"\bthe upstream reference produced no SQL\b":
        "A referenced phase produced no SQL text, so this query cannot be built.",
}

RECOVERABLE_TOOL_ERRORS = {
    "table_not_found": r"Object '([\w\.]+)' does not exist",
    # Three shapes, ONE pattern (group 1 = the offending reference, possibly qualified):
    #   generic:        Column 'foo' does not exist
    #   Teradata 3810:  Column/Parameter 'fitness_db.S.Price' does not exist
    #   Teradata 5628:  Column product_name not found in FITNESS_DB.sales
    # Before this, every Teradata column error missed the pattern and fell to the paid
    # Generic LLM round (observed on 100% of column errors in the @OPGGT corpus).
    "column_not_found": r"Column(?:/Parameter)?\s+'?([\w\.]+)'?\s+(?:does not exist|not found)",
    # Teradata Error 3802. A weak planner frequently hallucinates a *logical* KG
    # entity name (e.g. ``airport.station``) as a physical ``database.table``
    # reference, so the qualifying database simply doesn't exist. Matched on the
    # human-readable message (present in every driver's error text).
    "database_not_found": r"Database '([^']+)' does not exist",
}

_SQL_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)(?:\s+(?:AS\s+)?([A-Za-z_][\w]*))?",
    re.IGNORECASE)
_SQL_RESERVED_AFTER_TABLE = {"where", "on", "group", "order", "join", "inner", "left", "right",
                             "full", "cross", "having", "limit", "qualify", "sample", "union"}


def _kg_table_columns(executor) -> Dict[str, List[str]]:
    """{'db.table': [col, ...]} from the turn's KG enrichment event (stamped by the
    knowledge-graph component); {} when no KG / no schema was in play."""
    return _sf.kg_table_columns(executor)


def _resolve_column_against_kg(executor, failed_action: Dict[str, Any], invalid_ref: str,
                               schema: Dict[str, List[str]] = None):
    """Deterministic column repair against the KG schema.

    Returns (fix, facts):
      fix   = {"sql", "bad", "good", "table"} when the SAME table the reference
              belongs to carries exactly one close match (case-insensitive equal,
              or difflib >= 0.8) — a mechanical rewrite, 0 tokens.
      facts = a schema-facts string for the LLM strategy otherwise (the real columns
              of the referenced table + where else the column name exists), or ""
              when no KG schema is available (baseline behaviour).
    """
    import difflib
    if schema is None:
        schema = _kg_table_columns(executor)
    sql = (failed_action.get("arguments") or {}).get("sql")
    if not schema or not isinstance(sql, str) or not sql.strip():
        return None, ""
    leaf = invalid_ref.rpartition(".")[2]
    qualifier = invalid_ref.rpartition(".")[0].rpartition(".")[2]  # alias or table leaf
    # alias → table map from FROM/JOIN clauses
    alias_to_table: Dict[str, str] = {}
    referenced: List[str] = []
    for m in _SQL_TABLE_REF_RE.finditer(sql):
        tbl, alias = m.group(1), m.group(2)
        if alias and alias.lower() in _SQL_RESERVED_AFTER_TABLE:
            alias = None
        referenced.append(tbl)
        if alias:
            alias_to_table[alias.lower()] = tbl
        alias_to_table.setdefault(tbl.rpartition(".")[2].lower(), tbl)
    lower_schema = {k.lower(): k for k in schema}

    def _resolve_table(name: str):
        if not name:
            return None
        n = name.lower()
        if n in lower_schema:
            return lower_schema[n]
        # unqualified 'Sales' → 'fitness_db.Sales'
        cands = [k for k in schema if k.rpartition(".")[2].lower() == n.rpartition(".")[2]]
        return cands[0] if len(cands) == 1 else None

    target_tables: List[str] = []
    if qualifier:
        t = _resolve_table(alias_to_table.get(qualifier.lower(), qualifier))
        if t:
            target_tables = [t]
    if not target_tables:
        target_tables = [t for t in (_resolve_table(r) for r in referenced) if t]
    target_tables = list(dict.fromkeys(target_tables))

    # 1) mechanical fix: unique close match in the referenced table(s)
    def _norm(x: str) -> str:
        return re.sub(r"[_\s]", "", (x or "").lower())

    matches = []
    for t in target_tables:
        cols = schema.get(t) or []
        by_norm = {}
        for c in cols:
            by_norm.setdefault(_norm(c), c)
        exact = [by_norm[_norm(leaf)]] if _norm(leaf) in by_norm else []
        close = exact or [by_norm[n] for n in difflib.get_close_matches(_norm(leaf), list(by_norm), n=2, cutoff=0.8)]
        for c in close:
            if c != leaf:
                matches.append((t, c))
    if len(matches) == 1 and len(target_tables) == 1:
        t, good = matches[0]
        # replace the reference the way it was written: alias-qualified (S.Col) or bare
        pat = re.compile(r"(?<!\w)" + re.escape(invalid_ref.rpartition(".")[2]) + r"(?!\w)")
        new_sql, n = pat.subn(good, sql)
        if n:
            return {"sql": new_sql, "bad": leaf, "good": good, "table": t}, ""

    # 2) facts for the LLM: the real columns + where the name actually lives
    lines = []
    for t in target_tables or list(schema)[:6]:
        lines.append(f"- {t}: {', '.join(schema.get(t) or [])}")
    elsewhere = [t for t, cols in schema.items()
                 if any(c.lower() == leaf.lower() for c in cols) and t not in target_tables]
    if elsewhere:
        lines.append(f"- The column '{leaf}' exists in: {', '.join(elsewhere)} (JOIN through a shared column to reach it).")
    # the join paths: columns shared by 2+ tables
    owners: Dict[str, List[str]] = {}
    for t, cols in schema.items():
        for c in cols:
            owners.setdefault(c.lower(), []).append(t)
    joinable = {c: ts for c, ts in owners.items() if len(ts) > 1}
    if joinable:
        lines.append("- JOINABLE COLUMNS (shared names — the only valid JOIN keys): "
                     + "; ".join(f"{c}: {', '.join(sorted(ts))}" for c, ts in sorted(joinable.items())))
    return None, "\n".join(lines)



async def _read_schema_facts(executor, failed_action: Dict[str, Any], invalid_ref: str):
    """Read the real columns of the failed statement's tables from the database and resolve
    ``invalid_ref`` against them exactly as against the knowledge graph. Returns
    ``(fix, facts, events)``: a mechanical fix when one close match exists, else the facts for
    the correction prompt ("" when nothing could be read). Never raises."""
    try:
        sql = (failed_action.get("arguments") or {}).get("sql")
        tables = _sf.sql_tables(sql)
        if not tables:
            return None, "", []
        default_db = (failed_action.get("arguments") or {}).get("database_name")
        cols, notes = await _sf.fetch_columns(executor, tables, default_db=default_db)
        if not cols:
            return None, "", []      # nothing read: add nothing (the behaviour before facts)
        events = [({"step": "System Self-Correction", "type": "workaround", "details": {
            "summary": (f"Read the real columns of {', '.join(cols)} from the database (0 LLM)."),
            "correction_type": "schema_facts_read",
            "details": {"columns": {k: v[:60] for k, v in cols.items()}, "not_read": notes}}}, None)]
        fix, _ = _resolve_column_against_kg(executor, failed_action, invalid_ref, schema=cols)
        return fix, _sf.facts_text(cols, notes), events
    except Exception as exc:  # noqa: BLE001
        app_logger.warning(f"schema facts: could not read the statement's tables: {exc}")
        return None, "", []


def _tool_words(name: str) -> frozenset:
    """A tool name's words, order- and plural-free: `base_listTables` and `base_tableList`
    both read {base, list, table}."""
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", str(name or ""))
    return frozenset(w.lower()[:-1] if len(w) > 3 and w.lower().endswith("s") else w.lower() for w in parts)


def _catalog_tool_for(name: str, known_tools) -> "str | None":
    """The ONE catalog tool that ``name`` means, or None (none, or ambiguous): the same name
    in another case, else the same words in another order or number."""
    same = [t for t in known_tools if t.lower() == str(name).lower()]
    if len(same) == 1:
        return same[0]
    words = _tool_words(name)
    if not words:
        return None
    hits = [t for t in known_tools if _tool_words(t) == words]
    return hits[0] if len(hits) == 1 else None


def _cs_only_phase_results(sql: str) -> bool:
    try:
        from trusted_data_agent.agent.cross_source import only_phase_results
        return only_phase_results(sql)
    except Exception:  # noqa: BLE001
        return False


def _resolve_unknown_tool(name: str, known_tools) -> "str | None":
    """A tool name the catalog does not have, resolved to the ONE catalog tool it means, or None.
    Beyond ``_catalog_tool_for`` (case, word order): a multi-server agent's tools read
    ``<alias>__<base>``, and a model writes the same shape for the FIRST server, whose tools carry
    no prefix (live: ``sales__base_readQuery`` on an agent whose first server is 'MS Fixture -
    sales'). A prefix that is no alias of the agent is dropped when the base name exists under
    exactly one catalog name — or, under several, when the prefix is a word of the first server's
    label (then the plain name). Never a guess between servers."""
    known = list(known_tools or [])
    if not isinstance(name, str) or name in known:
        return None
    hit = _catalog_tool_for(name, known)
    if hit:
        return hit
    try:
        from trusted_data_agent.core.tool_identity import SEP, parse
        from trusted_data_agent.agent import source_routing as _srt
    except Exception:  # noqa: BLE001
        return None
    if SEP not in name:
        return None
    prefix, _, base = name.rpartition(SEP)
    if not prefix or not base or parse(name).alias is not None:
        return None                                   # a real alias: the server simply lacks the tool
    cands = [t for t in known if parse(t).name == base]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        plain = [t for t in cands if parse(t).alias is None]
        first = next((s for s in _srt.turn_servers() if s.primary), None)
        label_words = set(re.findall(r"[a-z0-9]+", (_srt.label_of(first.server_id) if first else "").lower()))
        if len(plain) == 1 and prefix.lower() in label_words:
            return plain[0]
    return None


def _is_connector_tool(name: str) -> bool:
    """A platform connector tool is routed outside the MCP catalog — never refused here."""
    try:
        from trusted_data_agent.core.platform_connector_registry import get_connector_for_tool
        return bool(get_connector_for_tool(name))
    except Exception:  # noqa: BLE001 — an unreadable registry must not refuse a real tool
        return True


def _takes_sql(tool_def) -> bool:
    """True when a tool's schema has a `sql` argument — a statement runner, whatever the server
    declares about `required` (FastMCP 4 marks `base_readQuery`'s `sql` optional — OC-F17)."""
    args = getattr(tool_def, "args", None)
    return isinstance(args, dict) and "sql" in args

def _same_call_args(a, b) -> bool:
    """Two argument dicts name the same call: equal after normalising SQL text (case and
    whitespace — the database reads `select  x` and `SELECT x` alike). Unknown shapes are
    never 'the same' (the correction then runs as before)."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False

    def _norm(v):
        return " ".join(v.split()).lower().rstrip(";") if isinstance(v, str) else v
    return {k: _norm(v) for k, v in a.items() if v is not None} == \
           {k: _norm(v) for k, v in b.items() if v is not None}

class CorrectionStrategy(ABC):
    """Abstract base class for all self-correction strategies."""

    def __init__(self, executor: 'PlanExecutor'):
        self.executor = executor

    @abstractmethod
    def can_handle(self, error_data_str: str) -> bool:
        """Determines if this strategy can handle the given error."""
        pass

    @abstractmethod
    async def generate_correction(self, failed_action: Dict[str, Any], error_result: Dict[str, Any]) -> Tuple[Dict | None, List]:
        """Generates a corrected action or concludes the task."""
        pass

    async def _call_correction_llm(self, prompt: str, reason: str, system_prompt_override: str, failed_action: Dict[str, Any], extra_args_for_llm_task: Dict[str, Any] = None) -> Tuple[Dict | None, List]:
        """
        A helper method to standardize the LLM call for correction.
        Includes optional extra_args for TDA_LLMTask corrections.
        """
        events = []
        call_id = str(uuid.uuid4())
        events.append( ({"step": "Calling LLM for Self-Correction", "type": "system_message", "details": {"summary": reason, "call_id": call_id}}, None) )
        events.append( ({"target": "llm", "state": "busy"}, "status_indicator_update") )

        response_str, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
            prompt=prompt,
            reason=reason,
            system_prompt_override=system_prompt_override,
            raise_on_error=False,
            source=self.executor.source
            # user_uuid implicitly passed via self.executor._call_llm_and_update_tokens
        )
        events.append( ({"target": "llm", "state": "idle"}, "status_indicator_update") )

        # Log self-correction LLM call with tokens + cost for historical replay
        events.append( ({"step": "Calling LLM for Self-Correction", "type": "system_message", "details": {"summary": reason, "call_id": call_id, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0)}}, None) )

        # --- MODIFICATION START: Pass user_uuid to get_session ---
        updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
        # --- MODIFICATION END ---
        if updated_session:
            events.append( ({
                "statement_input": input_tokens, "statement_output": output_tokens,
                "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens,
                "total_input": updated_session.get("input_tokens", 0), "total_output": updated_session.get("output_tokens", 0),
                "call_id": call_id,
                "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0)
            }, "token_update") )

        # PROP-011 (phase 2): the self-correction carrier of the model's act — `ASK_USER: {…}`
        # when the error is an ambiguity only the person can settle. Raised into the ledger and
        # returned on the FINAL_ANSWER path, so the plan ends at this phase with the question
        # (no later phase is attempted; the chokepoints refuse anything else).
        if "ASK_USER:" in response_str:
            from trusted_data_agent.agent import input_requests as _ir_mod
            _ask = _ir_mod.parse_ask_prefix(response_str)
            _rec, _note = _ir_mod.raise_from_act(_ask or {}, source="self_correction")
            if _rec is not None:
                app_logger.info("Self-correction asked the user. Halting retries with the question.")
                return {"FINAL_ANSWER": _ir_mod.question_text(_rec)}, events
            app_logger.info(f"Self-correction tried to ask the user but the question was refused: {_note}")
            return {"FINAL_ANSWER": str((_ask or {}).get("question") or _note)}, events

        if "FINAL_ANSWER:" in response_str:
            app_logger.info("Self-correction resulted in a FINAL_ANSWER. Halting retries.")
            final_answer_text = response_str.split("FINAL_ANSWER:", 1)[1].strip()
            return {"FINAL_ANSWER": final_answer_text}, events

        corrected_data = robust_json_parse(response_str)
        if isinstance(corrected_data, dict):
            if corrected_data.get("tool_name") == "TDA_LLMTask" and extra_args_for_llm_task:
                if "arguments" not in corrected_data:
                    corrected_data["arguments"] = {}
                corrected_data["arguments"].update(extra_args_for_llm_task)
                events.append( ({"step": "System Self-Correction", "type": "workaround", "details": {"summary": f"LLM proposed TDA_LLMTask. Injecting required context.", "details": extra_args_for_llm_task}}, None) )

            # Validate the proposal against the catalog (mirrors plan-validation Pass 8):
            # a weak model answers `{"prompt_name": "base_readQuery"}` — a TOOL — and
            # the sub-prompt path then fails on a prompt that does not exist. Fix the
            # key deterministically; refuse a prompt that exists nowhere.
            _state = (getattr(self.executor, "dependencies", None) or {}).get("STATE", {}) or {}
            _known_tools = set((_state.get("mcp_tools") or {}).keys())
            _known_prompts = set((_state.get("mcp_prompts") or {}).keys())
            _pn = corrected_data.get("prompt_name")
            if isinstance(_pn, str) and _pn and _pn not in _known_prompts:
                if _pn in _known_tools or _pn.startswith("TDA_"):
                    corrected_data["tool_name"] = corrected_data.pop("prompt_name")
                    events.append(({"step": "System Self-Correction", "type": "workaround", "details": {
                        "summary": f"LLM labelled tool '{_pn}' as a prompt; re-keyed to a tool call (catalog validation).",
                        "correction_type": "correction_output_validation"}}, None))
                elif _known_prompts or _known_tools:
                    events.append(({"step": "System Self-Correction", "type": "error", "details": {
                        "summary": f"LLM proposed prompt '{_pn}', which is not in the catalog. Proposal rejected.",
                        "correction_type": "correction_output_validation"}}, None))
                    return None, events

            # …and the same for a proposed TOOL. Seen on the fusion bench (2026-09-24): a
            # correction proposed `base_listTables` — the catalog's tool is `base_tableList` —
            # and the unknown name went to the MCP server, which answered "Unknown tool" and
            # ended the phase, so a one-line count became "I cannot answer". A name whose WORDS
            # match exactly one catalog tool (order and plural ignored) is that tool: re-keyed
            # deterministically. Any other unknown name is refused before it reaches a server.
            _tn = corrected_data.get("tool_name")
            if (isinstance(_tn, str) and _tn and not _tn.startswith("TDA_") and _known_tools
                    and _tn not in _known_tools and not _is_connector_tool(_tn)):
                _match = _resolve_unknown_tool(_tn, _known_tools)
                if _match:
                    corrected_data["tool_name"] = _match
                    events.append(({"step": "System Self-Correction", "type": "workaround", "details": {
                        "summary": f"LLM proposed tool '{_tn}', which is not in the catalog; it names the same "
                                   f"words as '{_match}', so the call uses '{_match}' (catalog validation, no LLM round).",
                        "correction_type": "correction_output_validation"}}, None))
                else:
                    _near = difflib.get_close_matches(_tn, sorted(_known_tools), n=3, cutoff=0.5)
                    events.append(({"step": "System Self-Correction", "type": "error", "details": {
                        "summary": f"LLM proposed tool '{_tn}', which is not in the catalog. Proposal rejected"
                                   + (f" (closest catalog tools: {', '.join(_near)})." if _near else "."),
                        "correction_type": "correction_output_validation"}}, None))
                    return None, events

            # A correction that writes SQL over phase/statement results only (`FROM p2 … IN
            # (SELECT … FROM p1)`) is the combine written as a query: no database holds `p2`.
            # It is handed to the combine step, where those names resolve (0 LLM) — stage 3b.
            _sql = (corrected_data.get("arguments") or {}).get("sql") if isinstance(corrected_data.get("arguments"), dict) else None
            _tn2 = corrected_data.get("tool_name")
            if (isinstance(_sql, str) and isinstance(_tn2, str) and _tn2 != "TDA_Compute"
                    and "TDA_Compute" in _known_tools | {t for t in (self.executor.dependencies.get("STATE") or {}).get("mcp_tools", {})}
                    and _cs_only_phase_results(_sql)):
                corrected_data = {"tool_name": "TDA_Compute", "arguments": {"op": "sql", "sql": _sql}}
                events.append(({"step": "System Self-Correction", "type": "workaround", "details": {
                    "summary": (f"LLM proposed '{_tn2}' over phase results only ({_sql[:120]}…); no database holds "
                                f"those — run as the combine step (TDA_Compute op sql, 0 LLM)."),
                    "correction_type": "correction_output_validation"}}, None))

            if "prompt_name" in corrected_data and "arguments" in corrected_data:
                events.append( ({"step": "System Self-Correction", "type": "workaround", "details": {"summary": f"LLM proposed switching to prompt '{corrected_data['prompt_name']}'.", "details": corrected_data}}, None) )
                return corrected_data, events

            # The identical call that just failed will fail the same way (OC-F14, 2026-10-29: a
            # correction LLM given no database message re-proposed the same SQL, and it ran a
            # second and third time). Refused deterministically: no resend, no further round.
            _prop_tool = corrected_data.get("tool_name") if "tool_name" in corrected_data else failed_action.get("tool_name")
            _prop_args = corrected_data.get("arguments", corrected_data if "tool_name" not in corrected_data else None)
            if (_prop_tool == failed_action.get("tool_name") and "prompt_name" not in corrected_data
                    and _same_call_args(_prop_args, failed_action.get("arguments"))):
                events.append(({"step": "System Self-Correction", "type": "error", "details": {
                    "summary": f"LLM proposed the identical '{_prop_tool}' call that just failed; not resent "
                               "(the same call returns the same error).",
                    "correction_type": "correction_output_validation"}}, None))
                return None, events

            if "tool_name" in corrected_data and "arguments" in corrected_data:
                events.append( ({"step": "System Self-Correction", "type": "workaround", "details": {"summary": f"LLM proposed retrying with tool '{corrected_data['tool_name']}'.", "details": corrected_data}}, None) )
                return corrected_data, events

            new_args = corrected_data.get("arguments", corrected_data)
            if isinstance(new_args, dict):
                corrected_action = {**failed_action, "arguments": new_args}
                events.append( ({"step": "System Self-Correction", "type": "workaround", "details": {"summary": "LLM proposed new arguments.", "details": new_args}}, None) )
                return corrected_action, events

        else:
            events.append( ({"step": "System Self-Correction", "type": "error", "details": {"summary": "LLM failed to provide a valid JSON correction.", "details": response_str}}, None) )

        return None, events

class TableNotFoundStrategy(CorrectionStrategy):
    """Handles errors where a specified table does not exist."""
    def can_handle(self, error_data_str: str) -> bool:
        return bool(re.search(RECOVERABLE_TOOL_ERRORS["table_not_found"], error_data_str, re.IGNORECASE))

    async def generate_correction(self, failed_action: Dict[str, Any], error_result: Dict[str, Any]) -> Tuple[Dict | None, List]:
        # QW3 (C8): search BOTH fields (data + error_message) and guard .group(1), mirroring
        # DatabaseNotFoundStrategy. can_handle() matches against a combined string, but this
        # re-ran the regex on `data` only and called .group(1) unguarded — an error carrying
        # the pattern only in error_message (a shape this codebase itself constructs) raised
        # AttributeError inside the recovery path, turning a recoverable error turn-fatal.
        error_data_str = f"{error_result.get('data', '')} {error_result.get('error_message', '')}"
        table_error_match = re.search(RECOVERABLE_TOOL_ERRORS["table_not_found"], error_data_str, re.IGNORECASE)
        if not table_error_match:
            app_logger.info("TableNotFoundStrategy: pattern not present in data/error_message; delegating to GenericCorrectionStrategy.")
            return await GenericCorrectionStrategy(self.executor).generate_correction(failed_action, error_result)
        invalid_table = table_error_match.group(1)
        invalid_table_name_only = invalid_table.split('.')[-1]
        failed_args = failed_action.get("arguments", {})
        db_name = failed_args.get("database_name") or None

        # An UNQUALIFIED table this turn's own table lists place in exactly one database:
        # qualify it (0 LLM). The statement named no database, so the server could not find it.
        _sql = failed_args.get("sql")
        if "." not in invalid_table and isinstance(_sql, str):
            _hit = _sf.database_for_table(self.executor, invalid_table)
            _fixed = _sf.qualify_table(_sql, invalid_table, _hit[0], _hit[1]) if _hit else None
            if _fixed:
                corrected = copy.deepcopy(failed_action)
                corrected["arguments"] = dict(corrected.get("arguments") or {})
                corrected["arguments"]["sql"] = _fixed
                return corrected, [({"step": "System Self-Correction", "type": "workaround", "details": {
                    "summary": (f"'{invalid_table}' named no database; this turn's table list places it in "
                                f"{_hit[0]} — qualified as {_hit[0]}.{_hit[1]} (0 LLM)."),
                    "correction_type": "table_qualified_from_listing"}}, None)]

        app_logger.warning(f"Detected recoverable 'table_not_found' error for table: {invalid_table}")

        # Use profile-aware prompt resolution
        prompt_content = self.executor.prompt_resolver.get_tactical_self_correction_table_error_prompt()
        if not prompt_content:
            app_logger.error("Failed to resolve TACTICAL_SELF_CORRECTION_PROMPT_TABLE_ERROR from profile mapping")
            return None, []
        
        _cc_tools, _cc_prompts = self.executor._get_correction_catalog()  # §4: compact catalog
        prompt = prompt_content.format(
            user_question=self.executor.original_user_input,
            tool_name=failed_action.get("tool_name"),
            failed_arguments=json.dumps(failed_args),
            invalid_table_name=invalid_table_name_only,
            # never a placeholder a model could copy into a tool argument as a name
            database_name=db_name or "(none — the statement names no database)",
            tools_context=_cc_tools,
            prompts_context=_cc_prompts,
            needs_input_carrier=_needs_input_carrier_text(),   # PROP-011: the ASK_USER: format, only while the model may ask
        )
        read_events: List = []
        _db = invalid_table.rpartition(".")[0] or db_name
        if not _kg_table_columns(self.executor) and not _db:
            _seen = _sf.tables_seen(self.executor)
            if not _seen:
                # no table listing this turn: read the tables of the few user databases the
                # turn DID list (a statement that names no database, or an invented qualifier)
                _dbs = _sf.databases_seen(self.executor)
                if 0 < len(_dbs) <= _sf.MAX_LISTED_DATABASES:
                    for _d in _dbs:
                        _ts = await _sf.fetch_tables(self.executor, _d)
                        if _ts:
                            _seen[_d] = _ts
                    if _seen:
                        read_events.append(({"step": "System Self-Correction", "type": "workaround", "details": {
                            "summary": (f"Read the real tables of {', '.join(_seen)} from the database "
                                        f"(the statement named no usable database; 0 LLM)."),
                            "correction_type": "schema_facts_read", "details": {"tables": _seen}}}, None))
            if _seen:
                prompt += ("\n\n" + _sf.FACTS_HEADER + "\n"
                           + _sf.facts_text({}, tables_by_db=_seen) + "\n" + _sf.FACTS_DIRECTIVE)
        if not _kg_table_columns(self.executor) and _db:
            _tables = await _sf.fetch_tables(self.executor, _db)
            if _tables:
                prompt += ("\n\n" + _sf.FACTS_HEADER + "\n"
                           + _sf.facts_text({}, tables_by_db={_db: _tables}) + "\n" + _sf.FACTS_DIRECTIVE)
                read_events.append(({"step": "System Self-Correction", "type": "workaround", "details": {
                    "summary": f"Read the real tables of {_db} from the database ({len(_tables)} tables, 0 LLM).",
                    "correction_type": "schema_facts_read", "details": {"database": _db, "tables": _tables[:50]}}}, None))
        reason = f"Fact-based recovery for non-existent table '{invalid_table_name_only}'"
        system_prompt = "You are an expert troubleshooter. Follow the recovery directives precisely."

        corrected, events = await self._call_correction_llm(prompt, reason, system_prompt, failed_action)
        return corrected, read_events + events

class ColumnNotFoundStrategy(CorrectionStrategy):
    """Handles errors where a specified column does not exist."""
    def can_handle(self, error_data_str: str) -> bool:
        return bool(re.search(RECOVERABLE_TOOL_ERRORS["column_not_found"], error_data_str, re.IGNORECASE))

    async def generate_correction(self, failed_action: Dict[str, Any], error_result: Dict[str, Any]) -> Tuple[Dict | None, List]:
        # QW3 (C8): search BOTH fields + guard .group(1) — see TableNotFoundStrategy above.
        error_data_str = f"{error_result.get('data', '')} {error_result.get('error_message', '')}"
        column_error_match = re.search(RECOVERABLE_TOOL_ERRORS["column_not_found"], error_data_str, re.IGNORECASE)
        if not column_error_match:
            app_logger.info("ColumnNotFoundStrategy: pattern not present in data/error_message; delegating to GenericCorrectionStrategy.")
            return await GenericCorrectionStrategy(self.executor).generate_correction(failed_action, error_result)
        invalid_column = column_error_match.group(1)

        app_logger.warning(f"Detected recoverable 'column_not_found' error for column: {invalid_column}")

        # Deterministic, 0-token resolution against the KNOWLEDGE GRAPH schema first.
        # The KG enrichment already told the planner the real columns; a weak model
        # still mis-cases or misnames one (`product_name` for `ProductName`,
        # `S.Price` for `S.TotalAmount`). If the SAME table carries a unique close
        # match, the fix is mechanical — no LLM round, no "describe the table" tool
        # (which a 1-tool profile does not even have).
        kg_fix, kg_facts = _resolve_column_against_kg(self.executor, failed_action, invalid_column)
        read_events: List = []
        db_facts = ""
        if kg_fix is None and not kg_facts:
            # No knowledge graph: read the real columns of the statement's tables from the
            # database (the agent's own describe tool, 0 LLM) — the facts the correction needs,
            # instead of an LLM round that answers "describe the table" and ends the phase.
            kg_fix, db_facts, read_events = await _read_schema_facts(
                self.executor, failed_action, invalid_column)
        if kg_fix is not None:
            corrected = copy.deepcopy(failed_action)
            corrected["arguments"] = dict(corrected.get("arguments") or {})
            corrected["arguments"]["sql"] = kg_fix["sql"]
            _src = "the database's own columns" if read_events else "the knowledge graph"
            events = read_events + [({"step": "System Self-Correction", "type": "workaround", "details": {
                "summary": (f"Deterministic column fix from {_src}: "
                            f"'{kg_fix['bad']}' → '{kg_fix['good']}' in {kg_fix['table']} (0 LLM)."),
                "correction_type": "kg_column_resolution",
                "details": {"bad": kg_fix["bad"], "good": kg_fix["good"], "table": kg_fix["table"]},
            }}, None)]
            return corrected, events

        # Use profile-aware prompt resolution
        prompt_content = self.executor.prompt_resolver.get_tactical_self_correction_column_error_prompt()
        if not prompt_content:
            app_logger.error("Failed to resolve TACTICAL_SELF_CORRECTION_PROMPT_COLUMN_ERROR from profile mapping")
            return None, []
        
        _cc_tools, _cc_prompts = self.executor._get_correction_catalog()  # §4: compact catalog
        prompt = prompt_content.format(
            user_question=self.executor.original_user_input,
            tool_name=failed_action.get("tool_name"),
            failed_arguments=json.dumps(failed_action.get("arguments", {})),
            invalid_column_name=invalid_column,
            tools_context=_cc_tools,
            prompts_context=_cc_prompts,
            needs_input_carrier=_needs_input_carrier_text(),   # PROP-011: the ASK_USER: format, only while the model may ask
        )
        if kg_facts:
            # Hybrid: deterministic FACTS (the real schema from the ontology), LLM
            # judgement (how to rewrite the query with them). Replaces "call a tool
            # that lists the columns" — the facts are already in hand.
            prompt += "\n\n--- SCHEMA FACTS (from the knowledge graph — use ONLY these columns) ---\n" + kg_facts
        elif db_facts:
            prompt += "\n\n" + _sf.FACTS_HEADER + "\n" + db_facts + "\n" + _sf.FACTS_DIRECTIVE
        reason = f"Fact-based recovery for non-existent column '{invalid_column}'"
        system_prompt = "You are an expert troubleshooter. Follow the recovery directives precisely."

        corrected, events = await self._call_correction_llm(prompt, reason, system_prompt, failed_action)
        return corrected, read_events + events

class DatabaseNotFoundStrategy(CorrectionStrategy):
    """Deterministically recovers from Teradata Error 3802 ('Database X does not exist').

    A small/weak planner frequently hallucinates a *logical* KG entity name
    (e.g. ``airport.station``) as a physical, queryable ``database.table``
    reference — so the qualifying database doesn't exist. The prior behaviour let
    this fall through to the blind, scattershot ``GenericCorrectionStrategy`` LLM
    retry. Instead this strategy pivots — with ZERO LLM judgement — to schema
    discovery inside the KG-declared *deployed* scope, so the next attempt sees the
    REAL tables in the RIGHT database. It never guesses a table name.

    Scope reach: this strategy lives in the Optimize (tool_enabled) Planner/Executor
    self-correction path only. The other IFOC engines do not use CorrectionHandler,
    so they are structurally unaffected (no regression). Universal *prevention* of
    the same hallucination — for every SQL-running engine — is handled separately by
    the preflight schema-grounding gate at the MCP invocation seam.
    """

    _DISCOVERY_BY_DB = "base_tableList"    # list tables in a specific database
    _DISCOVERY_ALL = "base_databaseList"   # enumerate all databases

    def can_handle(self, error_data_str: str) -> bool:
        return bool(re.search(RECOVERABLE_TOOL_ERRORS["database_not_found"], error_data_str, re.IGNORECASE))

    def _kg_scope_databases(self) -> list:
        """The DEPLOYED, queryable database(s) the KG enrichment declared as the
        model's trusted scope, in stable order. Mirrors eval RunRecord.kg_scope_databases
        so the recovery scope and the grounding gate's scope can never diverge."""
        ev = getattr(self.executor, "kg_enrichment_event", None)
        out = []
        if isinstance(ev, dict):
            for e in ev.get("enrichments", []) or []:
                if isinstance(e, dict):
                    for db in (e.get("scope_databases") or []):
                        if db and str(db) not in out:
                            out.append(str(db))
        return out

    def _available_tools(self) -> dict:
        try:
            return self.executor.dependencies['STATE'].get('mcp_tools', {}) or {}
        except Exception:
            return {}

    def _on_failing_server(self, failed_action: Dict[str, Any], base: str, available: dict):
        """The catalog name of the discovery tool ``base`` on the SAME server as the call that
        failed (PROP-006 O7): asking another server where a database is would answer about the
        wrong warehouse. ``None`` when that server does not offer it."""
        alias = _tool_parse((failed_action or {}).get("tool_name")).alias
        try:
            name = _tool_qualify(alias, base)
        except ValueError:
            return None
        return name if name in available else None

    async def generate_correction(self, failed_action: Dict[str, Any], error_result: Dict[str, Any]) -> Tuple[Dict | None, List]:
        error_data_str = f"{error_result.get('data', '')} {error_result.get('error_message', '')}"
        match = re.search(RECOVERABLE_TOOL_ERRORS["database_not_found"], error_data_str, re.IGNORECASE)
        invalid_db = match.group(1) if match else "the specified database"

        app_logger.warning(f"Detected recoverable 'database_not_found' error for database: {invalid_db}")

        scope_dbs = self._kg_scope_databases()
        available = self._available_tools()

        # Priority 1 — deterministic pivot to table discovery inside the trusted scope.
        # We do NOT rewrite the SQL to a guessed table; we surface the REAL tables so
        # the next attempt (or a subsequent phase) can choose correctly.
        by_db = self._on_failing_server(failed_action, self._DISCOVERY_BY_DB, available)
        if scope_dbs and by_db:
            target_db = sorted(scope_dbs)[0]
            corrected = {"tool_name": by_db, "arguments": {"database_name": target_db}}
            summary = (f"Database '{invalid_db}' does not exist. Deterministic recovery: "
                       f"discovering tables in KG-scoped database '{target_db}'.")
            events = [({"step": "System Self-Correction", "type": "auto_correction",
                        "details": {"summary": summary, "details": corrected}}, None)]
            return corrected, events

        # Priority 2 — no trusted scope known: deterministically enumerate databases so
        # the next step can locate the correct one (still zero LLM judgement).
        list_all = self._on_failing_server(failed_action, self._DISCOVERY_ALL, available)
        if list_all:
            corrected = {"tool_name": list_all, "arguments": {"scope": "all"}}
            summary = (f"Database '{invalid_db}' does not exist. Deterministic recovery: "
                       f"enumerating available databases to locate the correct one.")
            events = [({"step": "System Self-Correction", "type": "auto_correction",
                        "details": {"summary": summary, "details": corrected}}, None)]
            return corrected, events

        # Priority 3 — never regress below the pre-existing behaviour: fall back to the
        # generic fact-based LLM correction when no discovery tool is available.
        app_logger.info("DatabaseNotFoundStrategy: no discovery tool available; delegating to GenericCorrectionStrategy.")
        return await GenericCorrectionStrategy(self.executor).generate_correction(failed_action, error_result)


class SurfaceRefusalStrategy(CorrectionStrategy):
    """Deterministically recovers a query_product call refused as OFF-SURFACE.

    The refusal names its own remedy verbatim — "'fitness_sales' is not on this
    agent's data surface — its surface is: support_load" — so paying an LLM
    correction round (~2,800 tokens, observed) to read that sentence and swap one
    argument is the shortcut the Component → LLM doctrine forbids: judgement for
    the model, mechanics for the code. Observed live: the generic LLM round did
    exactly this swap, every time, minus the tokens.

    ZERO-LLM path only when the refusal names exactly ONE surface product — the
    swap is then the only legal move. A multi-product surface is a real choice
    (which product answers this?) and falls through to the LLM strategy. If the
    swapped request's measures are not declared on the surface product, the next
    REJECT (unknown_member, with the closest declared name) is the designed food
    for that genuinely-semantic step.
    """

    _RE = re.compile(
        r"is not on this agent's data surface \u2014 its surface is: ([\w ,._-]+)")
    _RE_ASCII = re.compile(
        r"is not on this agent's data surface .{1,3} its surface is: ([\w ,._-]+)")

    def can_handle(self, error_data_str: str) -> bool:
        return bool(self._surface_products(error_data_str))

    def _surface_products(self, error_data_str: str) -> list:
        m = self._RE.search(error_data_str) or self._RE_ASCII.search(error_data_str)
        if not m:
            return []
        return [p.strip() for p in m.group(1).split(",") if p.strip()]

    async def generate_correction(self, failed_action, error_result):
        products = self._surface_products(str(error_result.get("data", "")))
        if len(products) != 1:
            return None, []           # a real choice — let the LLM strategy judge it
        target = products[0]
        corrected = copy.deepcopy(failed_action)
        args = corrected.setdefault("arguments", {})
        if args.get("product_name") == target:
            return None, []           # already at the only legal door — nothing to swap
        args["product_name"] = target
        events = [({
            "step": "System Self-Correction",
            "type": "workaround",
            "details": {
                "summary": (f"The requested product is not on this agent's data "
                            f"surface, whose ONLY product is '{target}'. Swapped "
                            f"deterministically — the refusal named the remedy, so "
                            f"no LLM round was spent reading it."),
                "correction_type": "surface_product_swap",
                "corrected_action": corrected,
            },
        }, None)]
        return corrected, events


class TeradataSyntaxStrategy(CorrectionStrategy):
    """Deterministically rewrites forms other dialects accept and Teradata refuses — ``!=``,
    ``SELECT *, expr``, ``DATE_TRUNC`` (day..year) — plus a trailing LIMIT in the same
    statement. ONE rewrite for every lane: ``sql_dialect.teradata_syntax_rewrite`` also serves
    the ReAct / Navigator retry. Found by executing Loom's Teradata patterns (2026-10-01);
    anything it cannot rewrite exactly falls through to the next strategy and the LLM."""

    _ERR_RE = re.compile(r"\b370[67]\b|Syntax error", re.IGNORECASE)

    def can_handle(self, error_data_str: str) -> bool:
        return bool(self._ERR_RE.search(error_data_str or ""))

    async def generate_correction(self, failed_action, error_result):
        from trusted_data_agent.agent.sql_dialect import teradata_syntax_rewrite
        args = (failed_action or {}).get("arguments") or {}
        rw = teradata_syntax_rewrite(args.get("sql"))
        if not rw:
            return None, []            # not one of these forms — the next strategy judges it
        corrected_sql, changes = rw
        corrected = copy.deepcopy(failed_action)
        corrected.setdefault("arguments", {})["sql"] = corrected_sql
        events = [({
            "step": "System Self-Correction",
            "type": "workaround",
            "details": {
                "summary": ("Rewrote forms other databases accept and Teradata refuses: "
                            + "; ".join(changes) + " (a mechanical dialect fix; no LLM round spent)."),
                "correction_type": "teradata_syntax_rewrite",
                "corrected_action": corrected,
            },
        }, None)]
        return corrected, events


class TeradataLimitStrategy(CorrectionStrategy):
    """Deterministically rewrites LIMIT n → SELECT TOP n on a Teradata syntax error.

    Teradata has no LIMIT clause; a model that writes one gets Error 3706
    ("expected something between …") and the observed correction (session
    dd40d071) was a full LLM round to perform a purely MECHANICAL translation —
    the same class the product compiler's dialect seam already owns (TOP form,
    never LIMIT). Syntax widens, meaning never: the rewrite moves the SAME n to
    the TOP position and strips the LIMIT clause; anything non-mechanical (no
    LIMIT in the SQL, no n, a LIMIT with OFFSET) falls through to the LLM.
    """

    _ERR_RE = re.compile(r"\b3706\b|Syntax error", re.IGNORECASE)
    _LIMIT_RE = re.compile(r"\s+LIMIT\s+(\d+)\s*;?\s*$", re.IGNORECASE)
    _SELECT_RE = re.compile(r"^(\s*SELECT\s+)(DISTINCT\s+)?", re.IGNORECASE)

    def can_handle(self, error_data_str: str) -> bool:
        # Broad on purpose: the driver's 3706 text does not always name LIMIT, and
        # generate_correction reads the SQL itself. A decline (no trailing LIMIT)
        # returns (None, []) and CorrectionHandler continues the chain — it no
        # longer aborts all correction on the first can_handle() match.
        return bool(self._ERR_RE.search(error_data_str or ""))

    async def generate_correction(self, failed_action, error_result):
        # ONE rewrite for both engines: `agent/sql_dialect.teradata_top_rewrite` also
        # serves the ReAct tool wrapper's retry, so the two paths cannot drift. It
        # covers a trailing LIMIT n AND a misplaced trailing TOP n (`… ORDER BY x TOP
        # 30`), and declines DISTINCT / an existing TOP / OFFSET / multi-statement.
        from trusted_data_agent.agent.sql_dialect import teradata_top_rewrite
        args = (failed_action or {}).get("arguments") or {}
        sql = args.get("sql")
        corrected_sql = teradata_top_rewrite(sql)
        if not corrected_sql:
            return None, []            # not the mechanical shape — the LLM judges it
        n = re.search(r"\bTOP\s+(\d+)", corrected_sql, re.IGNORECASE).group(1)
        corrected = copy.deepcopy(failed_action)
        corrected.setdefault("arguments", {})["sql"] = corrected_sql
        events = [({
            "step": "System Self-Correction",
            "type": "workaround",
            "details": {
                "summary": (f"Teradata takes its row limit as SELECT TOP {n} — rewrote "
                            f"the trailing clause deterministically (a mechanical "
                            f"dialect translation; no LLM round spent)."),
                "correction_type": "teradata_limit_to_top",
                "corrected_action": corrected,
            },
        }, None)]
        return corrected, events


class TeradataReservedAliasStrategy(CorrectionStrategy):
    """Deterministically quotes a reserved word used as a name (alias, table alias, column) on
    Error 3706/3707 naming it.

    A model names a column after a Teradata reserved word (`… END AS Month`, production
    and local replays 2026-09-05) and the driver answers "expected something like a name
    … between the 'AS' keyword and the 'Month' keyword" — the correction is purely
    mechanical: the identifier becomes delimited (`AS "Month"`, `ORDER BY "Month"`) and
    nothing else moves. ONE rewrite for both engines (`sql_dialect.teradata_reserved_alias_rewrite`
    also serves the ReAct wrapper's retry); anything non-mechanical declines to the LLM.
    """

    def can_handle(self, error_data_str: str) -> bool:
        from trusted_data_agent.agent.sql_dialect import reserved_alias_from_error
        return reserved_alias_from_error(error_data_str or "") is not None

    async def generate_correction(self, failed_action, error_result):
        from trusted_data_agent.agent.sql_dialect import (reserved_alias_from_error,
                                                          teradata_reserved_alias_rewrite)
        args = (failed_action or {}).get("arguments") or {}
        sql = args.get("sql")
        err_text = str((error_result or {}).get("data", "")) + " " + str((error_result or {}).get("error_message", ""))
        word = reserved_alias_from_error(err_text)
        corrected_sql = teradata_reserved_alias_rewrite(sql, err_text)
        if not word or not corrected_sql:
            return None, []            # not the mechanical shape — the LLM judges it
        corrected = copy.deepcopy(failed_action)
        corrected.setdefault("arguments", {})["sql"] = corrected_sql
        events = [({
            "step": "System Self-Correction",
            "type": "workaround",
            "details": {
                "summary": (f"`{word}` is a Teradata reserved word — quoted it as a delimited "
                            f"identifier (`\"{word}\"`) wherever the statement names it "
                            f"(a mechanical dialect fix; no LLM round spent)."),
                "correction_type": "teradata_reserved_alias_quoted",
                "corrected_action": corrected,
            },
        }, None)]
        return corrected, events


class TeradataDateLiteralStrategy(CorrectionStrategy):
    """Error 6760 on a bare ISO date string compared with a TIMESTAMP (`SaleDate >= '2026-08-25'`)
    → `DATE '2026-08-25'`, deterministically. Fusion bench 2026-09-24: the statement was right
    in substance; the LLM correction re-ran TDA_CurrentDate and the answer reported the number
    of dates instead of the sales count. ONE rewrite for every lane
    (`sql_dialect.teradata_iso_date_literal_rewrite`, also behind the ReAct/Navigator retry)."""

    def can_handle(self, error_data_str: str) -> bool:
        from trusted_data_agent.agent.sql_dialect import looks_like_invalid_timestamp
        return looks_like_invalid_timestamp(error_data_str or "")

    async def generate_correction(self, failed_action, error_result):
        from trusted_data_agent.agent.sql_dialect import teradata_iso_date_literal_rewrite
        sql = ((failed_action or {}).get("arguments") or {}).get("sql")
        corrected_sql = teradata_iso_date_literal_rewrite(sql)
        if not corrected_sql:
            return None, []            # no bare ISO date string — the LLM judges it
        corrected = copy.deepcopy(failed_action)
        corrected.setdefault("arguments", {})["sql"] = corrected_sql
        return corrected, [({
            "step": "System Self-Correction", "type": "workaround",
            "details": {"summary": "A date written as a string cannot be compared with a TIMESTAMP column — "
                                   "wrote it as a DATE literal (a mechanical dialect fix; no LLM round spent).",
                        "correction_type": "teradata_date_literal", "corrected_action": corrected}}, None)]


class TextDateStrategy(CorrectionStrategy):
    """Deterministically rewrites a statement the preflight text-date guard refused.

    The guard refuses, before the database sees it, a DATE deployed as characters that
    a statement compares as a string, orders bare, MIN/MAX's or buckets with SUBSTR.
    Once the column's stored shape has been learned from result rows, the fix for every
    use but the SUBSTR bucket is MECHANICAL — wrap the column in
    ``CAST(col AS DATE FORMAT '<shape>')``, turn the literal beside it into a DATE
    literal, swap a mismatched FORMAT — so it is applied here at 0 LLM (the doctrine of
    the surface-refusal strategy: a structured refusal that names its own remedy never
    costs an LLM round). Anything needing judgement (shape not yet sampled, a SUBSTR
    bucket whose period only the model knows, an ambiguous slash literal) declines
    ``(None, [])`` so the recovery-shaped refusal reaches the LLM exactly as before.
    ONE rewrite (`preflight.textdates.rewrite_sql`) also serves the ReAct tool wrapper.
    """

    def can_handle(self, error_data_str: str) -> bool:
        return "[Preflight]" in (error_data_str or "") and "DATE stored as" in (error_data_str or "")

    async def generate_correction(self, failed_action, error_result):
        from trusted_data_agent.agent.preflight import textdates as _td
        args = (failed_action or {}).get("arguments") or {}
        sql = args.get("sql")
        if not isinstance(sql, str):
            return None, []
        fix = _td.rewrite_sql(getattr(self.executor, "session_id", None), sql)
        if not fix:
            return None, []            # judgement involved — the LLM sees the refusal
        corrected = copy.deepcopy(failed_action)
        corrected.setdefault("arguments", {})["sql"] = fix["sql"]
        cols = ", ".join(fix.get("columns") or [])
        events = [({
            "step": "System Self-Correction",
            "type": "workaround",
            "details": {
                "summary": (f"{cols} is a DATE stored as text — rewrote every string "
                            f"comparison, ORDER BY, MIN/MAX and mismatched FORMAT on it to "
                            f"CAST(… AS DATE FORMAT) with the sampled shape, deterministically "
                            f"(no LLM round spent): " + "; ".join(fix.get("changes") or [])),
                "correction_type": "text_date_cast",
                "corrected_action": corrected,
            },
        }, None)]
        return corrected, events


class FanoutJoinStrategy(CorrectionStrategy):
    """Deterministically rewrites a JOIN the preflight fan-out guard refused.

    The guard refuses, before the database sees it, a JOIN between two tables that both
    hold the ON column as a FOREIGN key to a third (fact-to-fact through a dimension key —
    every row of one paired with every row of the other per key value, a per-key total
    many times too large). The fix is MECHANICAL for the two-table shape: aggregate each
    holder at its own grain in a derived table, join the aggregates on the key, and push a
    `TOP n … ORDER BY` whose terms all belong to one side INTO that side so the TOP ranks
    that side's rows rather than the join's repeats (prod 2026-09-19: the model's own
    correction returned member 2 five times under TOP 5 and spent two more rounds). It is
    applied here at 0 LLM — the surface-refusal doctrine: a structured refusal that names
    its own remedy never costs an LLM round. Anything needing judgement — an expression
    mixing both tables, a bare COUNT(*), a third table, a subquery — declines ``(None, [])``
    so the recovery-shaped refusal reaches the LLM exactly as before. ONE rewrite
    (`preflight.joins.rewrite_fanout_sql`) also serves the ReAct tool wrapper.
    """

    def can_handle(self, error_data_str: str) -> bool:
        s = error_data_str or ""
        return "[Preflight]" in s and "multiplies rows" in s

    async def generate_correction(self, failed_action, error_result):
        from trusted_data_agent.agent.preflight import joins as _joins
        args = (failed_action or {}).get("arguments") or {}
        sql = args.get("sql")
        if not isinstance(sql, str):
            return None, []
        fix = _joins.rewrite_fanout_sql(getattr(self.executor, "session_id", None), sql)
        if not fix:
            return None, []            # judgement involved — the LLM sees the refusal
        corrected = copy.deepcopy(failed_action)
        corrected.setdefault("arguments", {})["sql"] = fix["sql"]
        a, b = fix["pair"]
        events = [({
            "step": "System Self-Correction",
            "type": "workaround",
            "details": {
                "summary": (f"JOIN between {a} and {b} on {fix['column']} multiplies rows (both hold it "
                            f"as a foreign key) — rewrote it deterministically (no LLM round spent) to "
                            f"aggregate-then-join: " + "; ".join(fix.get("changes") or [])
                            + f". Result grain: {fix['grain']}."),
                "correction_type": "fanout_aggregate_then_join",
                "corrected_action": corrected,
            },
        }, None)]
        return corrected, events


class GenericCorrectionStrategy(CorrectionStrategy):
    """The default fallback strategy for any other recoverable error."""
    def can_handle(self, error_data_str: str) -> bool:
        return True # It's the fallback, so it can always handle the error.

    async def generate_correction(self, failed_action: Dict[str, Any], error_result: Dict[str, Any]) -> Tuple[Dict | None, List]:
        tool_name = failed_action.get("tool_name")
        error_message = str(error_result.get('data', 'No error data.'))
        error_summary = str(error_result.get('error_message', error_message))

        is_json_parsing_error = "JSON" in error_summary or "Invalid control character" in error_message
        is_report_tool = tool_name in ["TDA_FinalReport", "TDA_ComplexPromptReport"]

        extra_args_for_llm_task = None

        if is_json_parsing_error and is_report_tool:
            app_logger.warning(f"Detected JSON parsing error for report tool '{tool_name}'. Attempting text sanitization recovery.")
            problematic_text = str(error_result.get('data', ''))

            if problematic_text:
                extra_args_for_llm_task = {
                    "task_description": (
                        "The previous attempt to generate a JSON report failed due to invalid characters or formatting. "
                        "Analyze the 'synthesized_answer' field below, which contains the raw text output. "
                        "Your task is to meticulously clean this text, removing any invalid control characters, "
                        "extraneous markdown, or conversational text. Ensure the output is a single, valid JSON object "
                        "strictly adhering to the required report schema. Preserve the original content as much as possible."
                    ),
                    "synthesized_answer": problematic_text,
                    "source_data": []
                }
                prompt = (
                    "A report generation tool failed because its output was invalid JSON (likely due to control characters or bad formatting).\n"
                    f"Error: {error_summary}\n"
                    "The best recovery is to use the `TDA_LLMTask` tool to clean the original text output and produce valid JSON.\n"
                    "Respond with a JSON object calling `TDA_LLMTask`. The necessary `task_description` and the problematic text (as `synthesized_answer`) will be automatically injected by the system."
                 )
                reason = f"Recovering from JSON error in {tool_name} via text sanitization."
                system_prompt = "You are an expert troubleshooter focused on JSON recovery. Call TDA_LLMTask as instructed."

                return await self._call_correction_llm(prompt, reason, system_prompt, failed_action, extra_args_for_llm_task=extra_args_for_llm_task)
            else:
                 app_logger.error(f"Cannot attempt JSON sanitization for {tool_name}: Original error data containing the text is missing.")

        tool_def = self.executor.dependencies['STATE'].get('mcp_tools', {}).get(tool_name)
        if not tool_def:
            tool_def_str = f"{{\"name\": \"{tool_name}\", \"description\": \"Client-side tool, definition not available.\"}}"
        else:
             try:
                 tool_def_str = json.dumps({
                     "name": getattr(tool_def, 'name', tool_name),
                     "description": getattr(tool_def, 'description', "No description"),
                     "args": getattr(tool_def, 'args', {})
                 }, default=str)
             except TypeError:
                 tool_def_str = f"{{\"name\": \"{tool_name}\", \"description\": \"Could not serialize tool definition.\"}}"

        # Use profile-aware prompt resolution
        prompt_content = self.executor.prompt_resolver.get_tactical_self_correction_prompt()
        if not prompt_content:
            app_logger.error("Failed to resolve TACTICAL_SELF_CORRECTION_PROMPT from profile mapping")
            return None, []
        
        # §4: compact catalog for switching (the failed tool keeps its FULL schema via tool_definition).
        _cc_tools, _cc_prompts = self.executor._get_correction_catalog()
        prompt = prompt_content.format(
            tool_definition=tool_def_str,
            failed_command=json.dumps(failed_action),
            error_message=json.dumps(error_message),
            user_question=self.executor.original_user_input,
            tools_context=_cc_tools,
            prompts_context=_cc_prompts,
            needs_input_carrier=_needs_input_carrier_text(),   # PROP-011: the ASK_USER: format, only while the model may ask
        )
        reason = f"Generic self-correction for failed tool call: {tool_name}"
        system_prompt = "You are an expert troubleshooter. Follow the recovery directives precisely."

        return await self._call_correction_llm(prompt, reason, system_prompt, failed_action, extra_args_for_llm_task=extra_args_for_llm_task)


class CorrectionHandler:
    """Manages and executes the appropriate correction strategy."""
    def __init__(self, executor: 'PlanExecutor', phase_state: dict = None):
        self.executor = executor
        # phase_state is the per-phase dict from executor._get_phase_state(phase_num).
        # When provided, correction_history is appended there instead of the shared
        # executor.correction_history so parallel phases don't cross-contaminate.
        self._phase_state = phase_state
        self.strategies = [
            SurfaceRefusalStrategy(executor),    # off-surface product — deterministic swap, 0 LLM
            TeradataSyntaxStrategy(executor),    # !=, SELECT *, expr, DATE_TRUNC (+LIMIT) — 0 LLM
            TeradataLimitStrategy(executor),     # LIMIT n → TOP n — deterministic dialect fix, 0 LLM
            TeradataReservedAliasStrategy(executor),  # reserved word as a name (3706/3707) → quoted, 0 LLM
            TeradataDateLiteralStrategy(executor),  # ISO date string vs TIMESTAMP (6760) → DATE '…', 0 LLM
            TextDateStrategy(executor),          # text-stored date refused by preflight → CAST … FORMAT, 0 LLM
            FanoutJoinStrategy(executor),        # fan-out join refused by preflight → aggregate-then-join, 0 LLM
            TableNotFoundStrategy(executor),
            ColumnNotFoundStrategy(executor),
            DatabaseNotFoundStrategy(executor),  # Error 3802 — deterministic scope-aware recovery
            GenericCorrectionStrategy(executor) # Generic is last
        ]

    async def attempt_correction(self, failed_action: Dict[str, Any], error_result: Dict[str, Any]) -> Tuple[Dict | None, List]:
        error_data_str = str(error_result.get('data', ''))
        error_summary = str(error_result.get('error_message', ''))

        full_error_context = f"{error_summary} {error_data_str}"

        for strategy in self.strategies:
            if strategy.can_handle(full_error_context):
                app_logger.info(f"Using correction strategy: {strategy.__class__.__name__}")
                corrected_action, events = await strategy.generate_correction(failed_action, error_result)
                if corrected_action is None and not events:
                    # The strategy DECLINED (a deterministic strategy whose narrow
                    # mechanical shape did not apply — e.g. TeradataLimitStrategy on a
                    # syntax error with no trailing LIMIT, SurfaceRefusalStrategy on a
                    # multi-product surface). Declining is "not handled", not "tried and
                    # failed": continue down the chain so the LLM fallback is still
                    # reached. Before this, the first can_handle() match returned
                    # unconditionally and a decline aborted ALL correction
                    # ("Unable to find a correction") — the Generic strategy was
                    # unreachable behind any broad-matching deterministic one.
                    app_logger.info(f"Correction strategy {strategy.__class__.__name__} declined; continuing the chain.")
                    continue
                # Record the failed attempt so the tactical LLM sees the full correction history (Reflexion).
                # Use per-phase state when available (parallel execution safety).
                ch = self._phase_state['correction_history'] if self._phase_state else self.executor.correction_history
                error_text = (error_summary or error_data_str)[:200].strip()
                ch.append({
                    "attempt": len(ch) + 1,
                    "tool_name": failed_action.get("tool_name", "unknown"),
                    "arguments": failed_action.get("arguments", {}),
                    "error": error_text,
                    "strategy": strategy.__class__.__name__,
                })
                return corrected_action, events

        app_logger.error("No correction strategy could handle the error.")
        return None, []


class PhaseExecutor:
    """
    Handles the tactical execution of a single phase of a plan. It is instantiated
    by the PlanExecutor (Orchestrator) and maintains a reference to it for state
    and helper method access.
    """
    def __init__(self, executor: 'PlanExecutor'):
        self.executor = executor

    def _build_correction_history_section(self, correction_history: list = None) -> str:
        """Renders the accumulated correction history for the current phase into a prompt string.

        Pass correction_history explicitly when in parallel execution so each phase
        only sees its own failures, not those from sibling parallel phases.
        Falls back to executor.correction_history for backward compatibility.
        """
        history = correction_history if correction_history is not None else self.executor.correction_history
        if not history:
            return "None"
        lines = ["The following tool calls have ALREADY been attempted in this phase and FAILED:"]
        for entry in history:
            args_str = json.dumps(entry.get("arguments", {}))
            if len(args_str) > 150:
                args_str = args_str[:150] + "..."
            lines.append(f"  • Attempt {entry['attempt']}: {entry['tool_name']}({args_str})")
            lines.append(f"    Error: {entry['error']}")
        lines.append("You MUST NOT repeat any of the above failed calls. Analyze the errors and choose a fundamentally different approach.")
        return "\n".join(lines)

    @staticmethod
    def _failed_upstream_refs(phase: dict, workflow_state: dict) -> list:
        """[(ref_key, error_text)] for every phase this one references whose stored
        result is an ERROR.

        The dependency-failure gap, observed twice live: a phase referencing a
        REJECTed compile ran anyway — its reference resolved to nothing (or to the
        error payload), the argument was omitted or garbage, the tool errored, and
        an LLM self-correction round filled the hole with hand-written SQL. A phase
        whose upstream failed cannot succeed; running it buys nothing but noise and
        correction tokens.

        Conservative on purpose: only a reference whose target EXISTS in the
        workflow state as a dict with status=='error' (or an 'error' field beside no
        results) counts. A missing target is NOT a failure — the phase may be mid-
        flight in a parallel group, and the resolver's existing behaviour handles it.
        """
        refs: set = set()

        def walk(v):
            if isinstance(v, str):
                for m in re.finditer(r"\b(result_of_phase_\d+|phase_\d+)\b", v):
                    refs.add(m.group(1))
            elif isinstance(v, dict):
                src = v.get("source")
                if isinstance(src, str) and re.match(r"(result_of_phase_|phase_)\d+$", src):
                    refs.add(src)
                for x in v.values():
                    walk(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    walk(x)

        walk((phase or {}).get("arguments"))
        walk((phase or {}).get("loop_over"))
        out = []
        for ref in sorted(refs):
            key = ref if ref.startswith("result_of_") else f"result_of_{ref}"
            res = (workflow_state or {}).get(key)
            _spec_err = (res.get("spec") or {}).get("error") if isinstance(
                res, dict) and isinstance(res.get("spec"), dict) else None
            _meta_err = (res.get("metadata") or {}).get("error") if isinstance(
                res, dict) and isinstance(res.get("metadata"), dict) else None
            if isinstance(res, dict) and (
                    res.get("status") == "error"
                    or (res.get("error") and not res.get("results"))
                    # component payloads carry their failure in spec/metadata —
                    # the dd40d071 shape: compile_metric stored
                    # {"spec": {}, "metadata": {"error": "no metric named …"}}
                    # and this helper read it as SUCCESS, so the dependent phase
                    # executed a reference to nothing
                    or (_spec_err and not res.get("results"))
                    or (_meta_err and _meta_err is not True and not res.get("results"))):
                err = str(res.get("error") or _spec_err or _meta_err
                          or res.get("data") or "upstream error")
                out.append((key, err))
        return out

    @staticmethod
    def _is_resolvable_phase_ref(value) -> bool:
        """A canonical phase-result reference the executor resolves DETERMINISTICALLY.

        `{"source": "result_of_phase_N", "key": "sql"}` carries zero unknowns —
        `_resolve_arguments` turns it into the referenced value with no judgement.
        Treating it as an unresolved placeholder sent the phase to the tactical LLM,
        which sometimes re-emitted the arguments and FLATTENED the dict to the bare
        string "result_of_phase_1" — losing `key`, so the resolver unwrapped the
        WHOLE component payload into a SQL argument (observed live: the governed
        statement replaced by the payload's repr, the tool erroring, and the final
        answer fabricating "1,000 customers" from the TOP 1000 clause in the noise).
        A phase with nothing left to decide must never visit an LLM at all.

        Orchestrator placeholders ({"source": "date_range", ...}, loop sources)
        stay rejected — those DO need their orchestrators.
        """
        if not isinstance(value, dict):
            return False
        src = value.get("source")
        key = value.get("key")
        return (isinstance(src, str)
                and re.match(r"(result_of_phase_\d+|phase_\d+|injected_previous_turn_data)$", src) is not None
                and isinstance(key, str) and bool(key))

    async def execute_phase(self, phase: dict):
        """
        The main public entry point to execute a single phase. It determines the
        phase type and delegates to the appropriate specialized execution method.
        """
        # PROP-006 O1 — results registered while this phase runs are marked with its number,
        # so a later phase can name them (`p2`) though parallel phases finish in any order.
        from trusted_data_agent.agent import result_sets as _rs_phase
        _ph = phase.get("phase") if isinstance(phase.get("phase"), int) else None
        _ph_token = _rs_phase.set_phase(_ph)
        try:
            if phase.get("type") == "loop":
                async for event in self._execute_looping_phase(phase):
                    yield event
            else:
                async for event in self._execute_standard_phase(phase):
                    yield event
        finally:
            _rs_phase.reset_phase(_ph_token)

    def _extract_loop_items(self, source_phase_key: str) -> list:
        """
        Intelligently extracts the list of items to iterate over from a previous phase's results.
        It now correctly handles and flattens results from a previous looping phase.
        """
        # C6a defense-in-depth: a non-string key (e.g. a hallucinated list loop_over that
        # isn't all-strings, so the orchestrator dispatch above didn't catch it) is unhashable
        # — `list not in dict` would raise TypeError: unhashable type. Never crash the turn on
        # a malformed loop source; treat it as "no items" (the empty-loop path handles it).
        if not isinstance(source_phase_key, str):
            app_logger.warning(f"Loop source is not a string reference ({type(source_phase_key).__name__}): {source_phase_key!r}. Returning empty list.")
            return []

        if source_phase_key not in self.executor.workflow_state:
            app_logger.warning(f"Loop source '{source_phase_key}' not found in workflow state.")
            return []

        source_data = self.executor.workflow_state[source_phase_key]

        if isinstance(source_data, list) and all(isinstance(item, list) for item in source_data):
            app_logger.info(f"Detected nested list structure from previous multi-tool loop '{source_phase_key}'. Attempting to flatten.")
            flattened_data = []
            for sub_list in source_data:
                if isinstance(sub_list, list):
                    for tool_result in sub_list:
                        if isinstance(tool_result, dict) and 'results' in tool_result and isinstance(tool_result['results'], list):
                             flattened_data.extend(tool_result['results'])
            if flattened_data:
                 app_logger.info(f"Successfully flattened {len(flattened_data)} items.")
                 return flattened_data
            else:
                 app_logger.warning("Flattening attempt yielded no results.")


        if isinstance(source_data, list) and all(isinstance(item, dict) and 'results' in item for item in source_data):
            flattened_results = []
            for tool_result in source_data:
                if isinstance(tool_result.get('results'), list):
                    flattened_results.extend(tool_result['results'])

            if flattened_results:
                app_logger.debug(f"Extracted and flattened {len(flattened_results)} items from previous loop phase '{source_phase_key}'.")
                return flattened_results

        def find_results_list(data):
            if isinstance(data, list):
                if all(isinstance(item, dict) and 'results' in item for item in data):
                     return data
                for item in data:
                    found = find_results_list(item)
                    if found is not None: return found
            elif isinstance(data, dict):
                if 'results' in data and isinstance(data['results'], list):
                    return data['results']
                for value in data.values():
                    found = find_results_list(value)
                    if found is not None: return found
            return None

        items = find_results_list(source_data)

        if isinstance(items, list) and all(isinstance(item, dict) and 'results' in item for item in items):
             app_logger.info("Fallback found list of tool results. Flattening.")
             flattened_items = []
             for item in items:
                 if isinstance(item.get('results'), list):
                     flattened_items.extend(item['results'])
             items = flattened_items


        if items is None:
            app_logger.warning(f"Could not find a 'results' list in '{source_phase_key}' using fallback. Returning empty list.")
            return []

        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
             app_logger.warning(f"Extracted loop items from '{source_phase_key}' are not in the expected format (list of dicts). Content: {items}")
             if isinstance(items, list) and len(items) == 1 and isinstance(items[0], list) and all(isinstance(i, dict) for i in items[0]):
                 app_logger.info("Recovered loop items from nested list.")
                 return items[0]
             return []

        return items


    async def _execute_looping_phase(self, phase: dict):
        """
        Orchestrates the execution of a looping phase. It uses a "fast path" for simple,
        repetitive tool calls to improve performance, and a standard, LLM-driven path
        for complex or synthesis-based loops.
        """
        phase_goal = phase.get("goal", "No goal defined.")
        phase_num = phase.get("phase", self.executor.current_phase_index + 1)
        loop_over_key = phase.get("loop_over")
        relevant_tools = phase.get("relevant_tools", [])

        event_data = {
            "step": f"Starting Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
            "type": "phase_start",
            "details": {
                "phase_num": phase_num,
                "total_phases": len(self.executor.meta_plan),
                "goal": phase_goal,
                "phase_details": phase,
                "execution_depth": self.executor.execution_depth
            }
        }
        self.executor._log_system_event(event_data)
        yield self.executor._format_sse_with_depth(event_data)

        # C6a fix: the planner hallucinated a loop over a *literal list of strings* (e.g.
        # ["past 5 days"] / ["tableA", "tableB"]) instead of a `result_of_phase_N` reference.
        # The hallucinated-loop safeguard exists for exactly this, but it lived only on the
        # standard-phase path (_execute_action_with_orchestrators) — a loop-TYPED phase reached
        # _extract_loop_items first, where `list not in dict` raised TypeError: unhashable type.
        # Dispatch here so the safeguard is reachable for loop-typed phases too. Result storage
        # mirrors the standard-path dispatch so downstream phases can reference this phase.
        # Non-empty guard: an empty list ([]) satisfies all()-over-empty vacuously; it must fall
        # through to the clean empty-loop skip below (via the non-string guard in
        # _extract_loop_items), NOT dispatch to the orchestrator with zero items.
        if isinstance(loop_over_key, list) and loop_over_key and all(isinstance(i, str) for i in loop_over_key):
            app_logger.warning("Loop-typed phase with a hallucinated string-list loop_over. Invoking hallucinated-loop orchestrator.")
            async for event in orchestrators.execute_hallucinated_loop(self.executor, phase):
                yield event
            action_for_history = {
                "tool_name": "TDA_SystemLog",
                "arguments": {"message": f"Hallucinated Loop: {phase.get('goal')}"},
                "metadata": {"execution_depth": self.executor.execution_depth, "timestamp": datetime.now(timezone.utc).isoformat()}
            }
            self.executor.turn_action_history.append({"action": action_for_history, "result": self.executor.last_tool_output})
            phase_result_key = f"result_of_phase_{phase_num}"
            if not isinstance(self.executor.workflow_state.get(phase_result_key), list):
                # a recovery that switched to a delegated prompt stores its result as a dict
                # here; `dict in dict` then raised `unhashable type` and killed the turn
                # (PROP-011 live check, 2026-11-02) — the slot is a LIST of outputs
                _prev = self.executor.workflow_state.get(phase_result_key)
                self.executor.workflow_state[phase_result_key] = [] if _prev is None else [_prev]
            if self.executor.last_tool_output not in self.executor.workflow_state[phase_result_key]:
                self.executor.workflow_state[phase_result_key].append(self.executor.last_tool_output)
            self.executor._add_to_structured_data(self.executor.last_tool_output)
            event_data = {
                "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                "type": "phase_end",
                "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": "completed", "execution_depth": self.executor.execution_depth}
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)
            return

        self.executor.current_loop_items = self._extract_loop_items(loop_over_key)

        if not self.executor.current_loop_items:
            event_data = {"step": "Skipping Empty Loop", "type": "system_message", "details": f"No items found from '{loop_over_key}' to loop over."}
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)
            event_data = {
                "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                "type": "phase_end",
                "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": "skipped", "execution_depth": self.executor.execution_depth}
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)
            return

        is_fast_path_candidate = (
            len(relevant_tools) == 1 and
            relevant_tools[0] not in ["TDA_LLMTask", "TDA_Charting", "TDA_Dashboard", "TDA_FinalReport", "TDA_ComplexPromptReport"]
        )
        # OC-F17, the loop's own FASTPATH: a SQL runner looped with no statement ran once per
        # item and answered "No SQL provided" as success each time (R5: six empty calls, then the
        # repeated-call guard). The per-item tactical step writes the statement instead.
        if is_fast_path_candidate and _takes_sql(
                (self.executor.dependencies.get('STATE') or {}).get('mcp_tools', {}).get(relevant_tools[0])) \
                and not str((phase.get("arguments") or {}).get("sql") or "").strip():
            is_fast_path_candidate = False
            app_logger.info(f"FASTPATH REJECTED for loop: '{relevant_tools[0]}' takes a SQL statement and the plan carries none.")

        if is_fast_path_candidate:
            tool_name = relevant_tools[0]

            raw_phase_args = phase.get("arguments", {})
            args_to_prune = [
                arg_name for arg_name, arg_value in raw_phase_args.items()
                if arg_value == loop_over_key
            ]
            if args_to_prune:
                modified_args = raw_phase_args.copy()
                for arg_name in args_to_prune:
                    app_logger.info(f"System Correction: Pruning redundant loop argument '{arg_name}' from phase '{phase_goal}'.")
                    del modified_args[arg_name]

                phase['arguments'] = modified_args

                event_data = {
                    "step": "System Correction",
                    "type": "workaround",
                    "details": {
                        "summary": "The agent's plan contained a redundant argument in a loop. The system has automatically removed it to prevent an error.",
                        "correction_type": "redundant_argument_pruning",
                        "pruned_arguments": args_to_prune
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)

            tool_scope = self.executor.dependencies['STATE'].get('tool_scopes', {}).get(tool_name)

            if tool_scope == 'column':
                event_data = {"step": "Plan Optimization", "type": "plan_optimization", "details": f"FASTPATH Data Expansion: Preparing column-level iteration for '{tool_name}'."}
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)

                yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")
                tool_constraints, constraint_events = await self.executor._get_tool_constraints(tool_name)
                for event in constraint_events:
                    yield event
                yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

                required_type = tool_constraints.get("dataType")

                expanded_loop_items = []
                tables_to_process = self.executor.current_loop_items
                db_name = phase.get("arguments", {}).get("database_name")

                if not db_name:
                    raise RuntimeError(f"Cannot perform column-level FASTPATH for tool '{tool_name}' because 'database_name' is missing from the phase arguments.")

                yield self.executor._format_sse_with_depth({"target": "db", "state": "busy"}, "status_indicator_update")
                for table_item in tables_to_process:
                    table_name = get_argument_by_canonical_name(table_item, 'object_name')
                    if not table_name: continue

                    args_for_col_tool = {'database_name': db_name}
                    for synonym in AppConfig.ARGUMENT_SYNONYM_MAP.get('object_name', {'object_name', 'table_name'}):
                        args_for_col_tool[synonym] = table_name


                    cols_command = {"tool_name": "base_columnDescription", "arguments": args_for_col_tool}
                    # --- MODIFICATION START: Pass user_uuid ---
                    cols_result, _, _ = await mcp_adapter.invoke_mcp_tool(
                        self.executor.dependencies['STATE'],
                        cols_command,
                        user_uuid=self.executor.user_uuid,
                        session_id=self.executor.session_id,
                        profile_id=self.executor.active_profile_id
                    )
                    # --- MODIFICATION END ---

                    if cols_result and isinstance(cols_result, dict) and cols_result.get('status') == 'success' and cols_result.get('results'):
                        columns_metadata = cols_result.get('results', [])
                        for col_info in columns_metadata:
                            col_name = col_info.get("ColumnName")
                            if not col_name: continue

                            col_type = next((v for k, v in col_info.items() if "type" in k.lower()), "").upper()
                            if required_type and col_type != "UNKNOWN":
                                is_numeric = any(t in col_type for t in ["INT", "NUMERIC", "DECIMAL", "FLOAT", "BYTEINT", "SMALLINT", "BIGINT"])
                                is_char = any(t in col_type for t in ["CHAR", "VARCHAR", "TEXT", "DATE", "TIMESTAMP"])
                                if (required_type == "numeric" and not is_numeric) or (required_type == "character" and not is_char):
                                    skip_details = f"Tool '{tool_name}' requires a {required_type} column, but '{col_name}' is '{col_type}'. Skipping."
                                    event_data = {"step": "Skipping Incompatible Column", "type": "plan_optimization", "details": skip_details}
                                    self.executor._log_system_event(event_data)
                                    yield self.executor._format_sse_with_depth(event_data)
                                    continue
                            expanded_loop_items.append({**table_item, "column_name": col_name})
                    else:
                        error_msg = (cols_result or {}).get('error', '') if isinstance(cols_result, dict) else ''
                        if 'timed out' in error_msg.lower():
                            # MCP server is unresponsive — every remaining table will also time out.
                            # Abort immediately instead of waiting N × 120 s.
                            abort_event = {
                                "step": "Column Expansion Aborted",
                                "type": "plan_optimization",
                                "details": f"MCP server timed out getting columns for '{table_name}'. Aborting column expansion for '{tool_name}' to avoid further delays."
                            }
                            self.executor._log_system_event(abort_event)
                            yield self.executor._format_sse_with_depth(abort_event)
                            break
                        app_logger.warning(f"Data expansion: Failed to get columns for table '{table_name}'. Tool `base_columnDescription` may have failed. Result: {cols_result}")

                yield self.executor._format_sse_with_depth({"target": "db", "state": "idle"}, "status_indicator_update")
                self.executor.current_loop_items = expanded_loop_items

                if not self.executor.current_loop_items:
                    event_data = {"step": "Skipping Empty Loop", "type": "system_message", "details": f"No compatible columns found for '{tool_name}'."}
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)
                    event_data = {"step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}", "type": "phase_end", "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": "skipped", "execution_depth": self.executor.execution_depth}}
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)
                    return

            event_data = {
                "step": "Plan Optimization",
                "type": "plan_optimization",
                "details": f"FASTPATH enabled for tool loop: '{tool_name}'"
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)

            static_phase_args = phase.get("arguments", {})

            tool_def = self.executor.dependencies['STATE'].get('mcp_tools', {}).get(tool_name)
            allowed_arg_names = set()
            if tool_def and hasattr(tool_def, 'args') and isinstance(tool_def.args, dict):
                for arg_name in tool_def.args.keys():
                    allowed_arg_names.add(arg_name)
                    canonical_name = None
                    for c, s in AppConfig.ARGUMENT_SYNONYM_MAP.items():
                        if arg_name in s:
                            canonical_name = c
                            break
                    if canonical_name:
                        allowed_arg_names.update(AppConfig.ARGUMENT_SYNONYM_MAP.get(canonical_name, set()))

            all_loop_results = []
            # QW8-residual: DefinitiveToolError is TYPE_CHECKING-only at module scope; import at
            # runtime so the fast-path loop can catch it per-item (see below).
            from trusted_data_agent.agent.executor import DefinitiveToolError
            yield self.executor._format_sse_with_depth({"target": "db", "state": "busy"}, "status_indicator_update")
            for i, item in enumerate(self.executor.current_loop_items):
                event_data = {"step": f"Processing Loop Item {i+1}/{len(self.executor.current_loop_items)}", "type": "system_message", "details": item}
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)

                item_data = item if isinstance(item, dict) else {}
                resolved_item_args = self.executor._resolve_arguments(static_phase_args, loop_item=item_data)

                pruned_item_data = {}
                if allowed_arg_names:
                    for key, value in item_data.items():
                         if key in allowed_arg_names:
                             pruned_item_data[key] = value

                merged_args = {**resolved_item_args, **pruned_item_data}

                # === Column Iteration Orchestrator Check (FASTPATH Loop) ===
                # Check if this is a column-scoped tool missing column_name argument
                tool_scope = self.executor.dependencies['STATE'].get('tool_scopes', {}).get(tool_name)
                has_column_arg = get_argument_by_canonical_name(merged_args, 'column_name') is not None

                if tool_scope == 'column' and not has_column_arg:
                    app_logger.info(f"FASTPATH Loop: Tool '{tool_name}' is column-scoped but missing column_name. Invoking column iteration orchestrator.")

                    event_data = {
                        "step": "Scope-Aware Dispatcher Action",
                        "type": "plan_optimization",
                        "details": f"FASTPATH loop invoking column iteration for '{tool_name}' because 'column_name' was missing."
                    }
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)

                    command_for_orchestrator = {"tool_name": tool_name, "arguments": merged_args}
                    try:
                        async for event in orchestrators.execute_column_iteration(self.executor, command_for_orchestrator):
                            yield event
                        enriched_tool_output = copy.deepcopy(self.executor.last_tool_output)
                        if (isinstance(enriched_tool_output, dict) and
                            enriched_tool_output.get("status") == "success" and
                            isinstance(item, dict)):
                            enriched_tool_output.setdefault("metadata", {}).update(item_data)
                        all_loop_results.append(enriched_tool_output)  # QW2 (C7): accumulator is all_loop_results — the old name was undefined, so a NameError discarded the orchestrator's result and double-executed the item without column_name
                        continue  # Skip normal tool execution for this item
                    except Exception as orch_e:
                        app_logger.error(f"FASTPATH Loop: Column iteration orchestrator failed for '{tool_name}': {orch_e}", exc_info=True)
                        # Fall through to normal tool execution for recovery
                # === End Column Iteration Check ===

                command = {"tool_name": tool_name, "arguments": merged_args}
                try:
                    async for event in self._execute_tool(command, phase, is_fast_path=True):
                        yield event
                except DefinitiveToolError as _dte:
                    # QW8-residual: a DEFINITIVE error means "do not retry" — but inside a LOOP it
                    # must skip only THIS item, never abort the remaining items (the slow path
                    # already does this via its per-item except). Record the failure and continue.
                    error_result = {
                        "status": "error",
                        "metadata": {"loop_item": item},
                        "error_message": {
                            "summary": "A definitive error occurred while processing this item; skipping it.",
                            "details": str(_dte),
                        },
                    }
                    all_loop_results.append(error_result)
                    self.executor._add_to_structured_data(error_result)
                    event_data = {"step": "Loop Item Failed (definitive)", "details": error_result, "type": "error"}
                    self.executor._log_system_event(event_data, "tool_result")
                    yield self.executor._format_sse_with_depth(event_data, "tool_result")
                    continue

                enriched_tool_output = copy.deepcopy(self.executor.last_tool_output)
                if (isinstance(enriched_tool_output, dict) and
                    enriched_tool_output.get("status") == "success" and
                    isinstance(item, dict)):

                    if 'results' in enriched_tool_output and isinstance(enriched_tool_output['results'], list):
                        for result_row in enriched_tool_output['results']:
                            if isinstance(result_row, dict):
                                for key, value in item.items():
                                    if key not in result_row:
                                        result_row[key] = value

                # --- MODIFICATION START: Log fast-path actions to history ---
                # Ensure fast-path tool calls are logged just like slow-path
                action_for_history = copy.deepcopy(command)
                action_for_history.setdefault("metadata", {})["phase_number"] = phase_num
                action_for_history.setdefault("metadata", {})["execution_depth"] = self.executor.execution_depth
                action_for_history.setdefault("metadata", {})["timestamp"] = datetime.now(timezone.utc).isoformat()
                self.executor.turn_action_history.append({"action": action_for_history, "result": enriched_tool_output})
                # --- MODIFICATION END ---
                all_loop_results.append(enriched_tool_output)

            yield self.executor._format_sse_with_depth({"target": "db", "state": "idle"}, "status_indicator_update")

            phase_result_key = f"result_of_phase_{phase_num}"
            self.executor.workflow_state[phase_result_key] = all_loop_results
            self.executor._add_to_structured_data(all_loop_results)
            self.executor.last_tool_output = all_loop_results

        else: # Slow Path (Multi-tool or complex single tool like TDA_LLMTask)
            self.executor.is_in_loop = True
            self.executor.processed_loop_items = []
            all_loop_item_results_aggregate = []

            for i, item in enumerate(self.executor.current_loop_items):
                event_data = {"step": f"Processing Loop Item {i+1}/{len(self.executor.current_loop_items)}", "type": "system_message", "details": item}
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)

                try:
                    async for event in self._execute_standard_phase(phase, is_loop_iteration=True, loop_item=item):
                        yield event
                    if isinstance(self.executor.last_tool_output, list):
                         all_loop_item_results_aggregate.extend(copy.deepcopy(self.executor.last_tool_output))
                    elif isinstance(self.executor.last_tool_output, dict):
                         all_loop_item_results_aggregate.append(copy.deepcopy(self.executor.last_tool_output))

                except Exception as e:
                    error_message = f"Error processing item {item}: {e}"
                    app_logger.error(error_message, exc_info=True)
                    error_result = {
                        "status": "error",
                        "metadata": {"loop_item": item},
                        "error_message": {
                            "summary": f"An error occurred while processing the item.",
                            "details": str(e)
                        }
                    }
                    all_loop_item_results_aggregate.append(error_result)
                    self.executor._add_to_structured_data(error_result)
                    event_data = {"step": "Loop Item Failed", "details": error_result, "type": "error"}
                    self.executor._log_system_event(event_data, "tool_result")
                    yield self.executor._format_sse_with_depth(event_data, "tool_result")

                self.executor.processed_loop_items.append(item)

            phase_result_key = f"result_of_phase_{phase_num}"
            self.executor.workflow_state[phase_result_key] = all_loop_item_results_aggregate
            self.executor.last_tool_output = all_loop_item_results_aggregate

            self.executor.is_in_loop = False
            self.executor.current_loop_items = []
            self.executor.processed_loop_items = []


        event_data = {
            "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
            "type": "phase_end",
            "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": "completed", "execution_depth": self.executor.execution_depth}
        }
        self.executor._log_system_event(event_data)
        yield self.executor._format_sse_with_depth(event_data)

    def _is_numeric(self, value: any) -> bool:
        """Checks if a value can be reliably converted to a number."""
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            try:
                float(value.replace(',', ''))
                return True
            except (ValueError, TypeError):
                return False
        return False

    # §5 cleanup: removed the dead, deprecated `_generate_charting_mapping` method
    # (~75 lines, zero real callers — charting mapping resolves at execution time).

    def _resolve_charting_data(self, action: dict) -> bool:
        """
        Deterministic pre-flight fix for TDA_Charting: resolves placeholder references
        in the 'data' argument to actual data from workflow_state.

        Less capable LLMs (e.g., Llama 3.3 70B) output the literal string
        "result_of_phase_2" instead of embedding the actual data array. This method
        detects such placeholders and resolves them from workflow_state, avoiding
        expensive self-correction cycles.

        Returns True if data was resolved/fixed, False if no fix was needed.
        """
        if action.get("tool_name") != "TDA_Charting":
            return False

        args = action.get("arguments", {})
        data = args.get("data")

        # Case 1: data is already a valid list of dicts — no fix needed
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return False

        # Case 2: data is a string placeholder like "result_of_phase_2"
        resolved_data = None
        if isinstance(data, str):
            match = re.match(r'^result_of_phase_(\d+)$', data)
            if match:
                resolved_data = self.executor.workflow_state.get(data)
                app_logger.info(f"Charting Data Resolver: Resolved string placeholder '{data}' from workflow state.")

        # Case 3: data is a dict wrapper (hallucinated function call, nested result, etc.)
        elif isinstance(data, dict):
            if 'results' in data:
                resolved_data = [data]
            elif 'args' in data:
                # Hallucinated function call like {"function_name": "...", "args": ["result_of_phase_2"]}
                ref_args = data.get('args', [])
                if isinstance(ref_args, list):
                    for ref in ref_args:
                        if isinstance(ref, str) and re.match(r'^result_of_phase_\d+$', ref):
                            resolved_data = self.executor.workflow_state.get(ref)
                            app_logger.info(f"Charting Data Resolver: Resolved hallucinated wrapper referencing '{ref}'.")
                            break

        # Case 4: data is None or missing — find the most recent phase result
        elif data is None:
            current_phase = self.executor.current_phase_index + 1
            for prev_phase in range(current_phase - 1, 0, -1):
                key = f"result_of_phase_{prev_phase}"
                if key in self.executor.workflow_state:
                    resolved_data = self.executor.workflow_state[key]
                    app_logger.info(f"Charting Data Resolver: Data was None, resolved from '{key}'.")
                    break

        # Case 5: Placeholder not found in current workflow_state — search previous turn data
        # This handles cross-turn charting where Turn 2 references Turn 1's data.
        # Collect ALL successful results from the most recent turn (not just the first one),
        # because looped tool calls produce multiple entries (e.g., 3x dba_resusageSummary).
        if resolved_data is None and self.executor.previous_turn_data:
            workflow_history = self.executor.previous_turn_data.get("workflow_history", [])
            if isinstance(workflow_history, list):
                for turn in reversed(workflow_history):
                    if not isinstance(turn, dict):
                        continue
                    execution_trace = turn.get("execution_trace", [])
                    turn_results = []
                    for entry in execution_trace:  # Forward order (chronological)
                        if not isinstance(entry, dict):
                            continue
                        result = entry.get("result", {})
                        entry_action = entry.get("action", {})
                        # Skip system logs and reporting tools
                        if isinstance(entry_action, dict):
                            tool = entry_action.get("tool_name", "")
                            if tool in ("TDA_SystemLog", "TDA_FinalReport", "TDA_ComplexPromptReport",
                                        "TDA_CurrentDate", "TDA_DateRange"):
                                continue
                        if (isinstance(result, dict) and
                            result.get("status") == "success" and
                            isinstance(result.get("results"), list) and
                            result["results"]):
                            turn_results.append(result)
                    if turn_results:
                        resolved_data = turn_results
                        total_rows = sum(len(r.get("results", [])) for r in turn_results)
                        app_logger.info(
                            f"Charting Data Resolver: Resolved from previous turn "
                            f"({len(turn_results)} result entries, {total_rows} total rows)."
                        )
                        break

        if resolved_data is None:
            return False

        # Flatten: workflow_state stores list of tool outputs, each with a "results" key
        flat_data = []
        if isinstance(resolved_data, list):
            for item in resolved_data:
                if isinstance(item, dict) and 'results' in item:
                    results = item['results']
                    if isinstance(results, list):
                        flat_data.extend(results)
                elif isinstance(item, dict):
                    flat_data.append(item)
        elif isinstance(resolved_data, dict) and 'results' in resolved_data:
            flat_data = resolved_data['results']

        if flat_data:
            args['data'] = flat_data
            app_logger.info(f"Charting Data Resolver: Injected {len(flat_data)} data rows into TDA_Charting action.")
            return True

        return False

    def _resolve_dashboard_content(self, action: dict) -> bool:
        """
        Pre-flight fix for TDA_Dashboard: resolves placeholder content references.

        LLMs sometimes emit "result_of_previous_query", "result_of_phase_2", or other
        placeholder strings in the 'content' argument instead of actual data. This mirrors
        the _resolve_charting_data pattern and injects real rows from workflow_state or
        previous_turn_data so the dashboard handler always receives convertible data.

        Returns True if content was resolved/fixed, False otherwise.
        """
        if action.get("tool_name") != "TDA_Dashboard":
            return False

        args = action.get("arguments", {})
        content = args.get("content")

        # Already valid HTML or JSON — no fix needed
        if isinstance(content, str):
            stripped = content.strip()
            if stripped.startswith("<") or stripped.startswith("[") or stripped.startswith("{"):
                return False
        # Already structured data — handler will convert it
        if isinstance(content, (list, dict)):
            return False

        # Detect placeholder: known patterns or any non-HTML short string
        _PLACEHOLDER_RE = re.compile(
            r'^(result_of_phase_\d+|result_of_previous_query|previous_result|'
            r'previous_query_result|phase_\d+_result|collected_data|'
            r'results_from_phase_\d+)$',
            re.IGNORECASE,
        )
        is_placeholder = (
            content is None
            or content == ""
            or (isinstance(content, str) and bool(_PLACEHOLDER_RE.match(content.strip())))
        )

        # For action=visualize, ANY plain-text string is treated as an implicit placeholder.
        # The LLM often writes natural language like "The data is from the previous analysis..."
        # instead of a canonical token. In visualize mode content must always be data rows —
        # non-structured strings should fall through to the cross-turn lookup.
        if (not is_placeholder
                and isinstance(content, str)
                and action.get("arguments", {}).get("action") == "visualize"):
            stripped = content.strip()
            if not stripped.startswith(("<", "[", "{")):
                is_placeholder = True
                app_logger.info(
                    "Dashboard Content Resolver: treating natural-language content as implicit "
                    "placeholder for 'visualize' action — will resolve from workflow state or "
                    "previous turn."
                )

        if not is_placeholder:
            return False

        app_logger.info(f"Dashboard Content Resolver: placeholder '{content}' detected — resolving from workflow state.")

        resolved_data = None

        def _has_chart_data(data) -> bool:
            """True if data contains at least one numeric value — i.e. actual tabular data,
            not a plain-text context report (TDA_ContextReport returns {"response": "..."})"""
            if not isinstance(data, list):
                return False
            for item in data:
                results = item.get("results", []) if isinstance(item, dict) else []
                for row in results:
                    if not isinstance(row, dict):
                        continue
                    keys = set(row.keys())
                    # Text-only marker: single 'response' key → not chart data
                    if keys == {"response"}:
                        continue
                    # Accept if any value is numeric OR there are multiple columns
                    if len(keys) > 1 or any(isinstance(v, (int, float)) for v in row.values()):
                        return True
            return False

        # 1. Phase-specific placeholder (same turn)
        if isinstance(content, str):
            match = re.match(r'^result_of_phase_(\d+)$', content.strip(), re.IGNORECASE)
            if match:
                candidate = self.executor.workflow_state.get(content.strip())
                if _has_chart_data(candidate):
                    resolved_data = candidate
                elif candidate is not None:
                    app_logger.info(
                        "Dashboard Content Resolver: skipping workflow_state result "
                        "(no numeric columns — likely TDA_ContextReport text output)."
                    )

        # 2. Most-recent phase result from the same turn
        if resolved_data is None:
            current_phase = self.executor.current_phase_index + 1
            for prev_phase in range(current_phase - 1, 0, -1):
                key = f"result_of_phase_{prev_phase}"
                if key in self.executor.workflow_state:
                    candidate = self.executor.workflow_state[key]
                    if _has_chart_data(candidate):
                        resolved_data = candidate
                        app_logger.info(f"Dashboard Content Resolver: resolved from '{key}'.")
                    else:
                        app_logger.info(
                            f"Dashboard Content Resolver: skipping '{key}' "
                            "(no numeric columns — likely TDA_ContextReport text output)."
                        )
                    break

        # 3. Previous turn's execution trace (cross-turn chart-type change).
        # TDA_ContextReport and other text-report tools are excluded from this scan.
        _SECTION3_SKIP = {
            "TDA_SystemLog", "TDA_FinalReport", "TDA_ComplexPromptReport",
            "TDA_CurrentDate", "TDA_DateRange", "TDA_Dashboard",
            "TDA_Charting", "TDA_ContextReport", "TDA_LLMTask",
        }
        if resolved_data is None and self.executor.previous_turn_data:
            workflow_history = self.executor.previous_turn_data.get("workflow_history", [])
            if isinstance(workflow_history, list):
                for turn in reversed(workflow_history):
                    if not isinstance(turn, dict):
                        continue
                    execution_trace = turn.get("execution_trace", [])
                    turn_results = []
                    for entry in execution_trace:
                        if not isinstance(entry, dict):
                            continue
                        result = entry.get("result", {})
                        entry_action = entry.get("action", {})
                        if isinstance(entry_action, dict):
                            tool = entry_action.get("tool_name", "")
                            if tool in _SECTION3_SKIP:
                                continue
                        if (isinstance(result, dict) and
                                result.get("status") == "success" and
                                isinstance(result.get("results"), list) and
                                result["results"]):
                            turn_results.append(result)
                    if turn_results:
                        resolved_data = turn_results
                        total_rows = sum(len(r.get("results", [])) for r in turn_results)
                        app_logger.info(
                            f"Dashboard Content Resolver: resolved {total_rows} rows from previous turn."
                        )
                        break

        if resolved_data is None:
            app_logger.warning("Dashboard Content Resolver: could not resolve placeholder from any source.")
            return False

        # Normalise to a JSON string — handler's _content_to_html will convert it
        import json as _json
        args["content"] = _json.dumps(resolved_data)
        action_preview = str(args.get("tile_title") or args.get("query") or "")[:60]
        app_logger.info(f"Dashboard Content Resolver: injected resolved data for '{action_preview}'.")
        return True

    async def _execute_standard_phase(self, phase: dict, is_loop_iteration: bool = False, loop_item: dict = None):
        """Executes a single, non-looping phase or a single iteration of a complex loop."""
        phase_goal = phase.get("goal", "No goal defined.")
        phase_num = phase.get("phase", self.executor.current_phase_index + 1)

        # Per-phase isolated state — prevents parallel phases from contaminating
        # each other's correction_history (Reflexion) and last_action_str (loop detect).
        _ps = self.executor._get_phase_state(phase_num)
        _ps['correction_history'] = []   # fresh history for this phase execution
        _ps['last_action_str'] = None    # reset loop-detection sentinel
        relevant_tools = phase.get("relevant_tools", [])

        # Dependency-failure skip: a phase whose referenced upstream FAILED cannot
        # succeed — do not resolve, do not invoke, do not spend correction rounds.
        # The skip is recorded as this phase's own honest error result so a later
        # phase referencing THIS one cascades through the same rule, and the final
        # report sees "skipped" rather than a fabricated value.
        _failed_up = self._failed_upstream_refs(phase, self.executor.workflow_state)
        if _failed_up:
            _up_key, _up_err = _failed_up[0]
            _skip_msg = (f"skipped — this phase depends on {_up_key}, which failed: "
                         f"{_up_err[:300]}")
            app_logger.warning(f"Phase {phase_num}: {_skip_msg}")
            self.executor.workflow_state[f"result_of_phase_{phase_num}"] = {
                "status": "error", "error": _skip_msg, "skipped_upstream_failure": True}
            for _ev in (
                {"step": f"Starting Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                 "type": "phase_start",
                 "details": {"phase_num": phase_num,
                             "total_phases": len(self.executor.meta_plan),
                             "execution_depth": self.executor.execution_depth}},
                {"step": "System Correction", "type": "workaround",
                 "details": {"summary": (f"Phase {phase_num} was skipped: it depends on "
                                         f"{_up_key}, which failed. Running it could only "
                                         f"error or fabricate — the failure is reported "
                                         f"instead."),
                             "correction_type": "upstream_failure_skip",
                             "upstream": _up_key}},
                {"step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                 "type": "phase_end",
                 "details": {"phase_num": phase_num,
                             "total_phases": len(self.executor.meta_plan),
                             "status": "skipped",
                             "execution_depth": self.executor.execution_depth}},
            ):
                self.executor._log_system_event(_ev)
                yield self.executor._format_sse_with_depth(_ev)
            return

        strategic_args = self.executor._resolve_arguments(phase.get("arguments", {}), loop_item=loop_item)
        if phase.get("_inserted_by") == "schema_before_sql":
            strategic_args, _settled = _sf.settle_describe_args(self.executor, strategic_args)
            if _settled:
                _sev = {"step": "System Correction", "type": "workaround",
                        "details": {"pass": "schema_before_sql", "summary": _settled + " (0 LLM)."}}
                self.executor._log_system_event(_sev)
                yield self.executor._format_sse_with_depth(_sev)
        executable_prompt = phase.get("executable_prompt")

        if not is_loop_iteration:
            event_data = {
                "step": f"Starting Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                "type": "phase_start",
                "details": {
                    "phase_num": phase_num,
                    "total_phases": len(self.executor.meta_plan),
                    "goal": phase_goal,
                    "phase_details": phase,
                    "execution_depth": self.executor.execution_depth
                }
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)

        if len(relevant_tools) > 1:
            event_data = {
                "step": "Scope-Aware Dispatcher Active",
                "type": "workaround",
                "details": f"Multi-tool phase detected. Agent will dispatch {len(relevant_tools)} tools based on scope."
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)

            all_phase_results = []
            for tool_name in relevant_tools:
                app_logger.info(f"Dispatcher: Processing tool '{tool_name}' in multi-tool phase.")

                current_args = {}
                if is_loop_iteration and loop_item:
                     base_resolved_args = self.executor._resolve_arguments(phase.get("arguments", {}), loop_item=None)
                     item_args = loop_item.copy()
                     current_args = {**base_resolved_args, **item_args}
                else:
                    current_args = strategic_args


                tool_scope = self.executor.dependencies['STATE'].get('tool_scopes', {}).get(tool_name)
                has_column_arg = get_argument_by_canonical_name(current_args, 'column_name') is not None

                if tool_scope == 'column' and not has_column_arg:
                    app_logger.info(f"Dispatcher: Tool '{tool_name}' is column-scoped but missing column_name. Calling column iteration orchestrator.")
                    event_data = {
                        "step": "Scope-Aware Dispatcher Action",
                        "type": "plan_optimization",
                        "details": f"Dispatcher is invoking column iteration for '{tool_name}' because 'column_name' was missing."
                    }
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)

                    action_for_orchestrator = {"tool_name": tool_name, "arguments": current_args}
                    try:
                        async for event in orchestrators.execute_column_iteration(self.executor, action_for_orchestrator):
                            yield event
                        all_phase_results.append(copy.deepcopy(self.executor.last_tool_output))
                    except Exception as orch_e:
                         app_logger.error(f"Dispatcher: Column iteration orchestrator failed for '{tool_name}': {orch_e}", exc_info=True)
                         error_result = {"status": "error", "error_message": f"Column iteration failed for {tool_name}: {str(orch_e)}"}
                         all_phase_results.append(error_result)
                         self.executor.last_tool_output = error_result
                    continue

                app_logger.info(f"Dispatcher: Tool '{tool_name}' (scope: {tool_scope}) has sufficient args or is not column-scoped. Executing normally.")
                action_to_execute = {"tool_name": tool_name, "arguments": current_args}
                async for event in self._execute_action_with_orchestrators(action_to_execute, phase):
                    yield event
                all_phase_results.append(copy.deepcopy(self.executor.last_tool_output))

            phase_result_key = f"result_of_phase_{phase_num}"
            if is_loop_iteration:
                 self.executor.last_tool_output = all_phase_results
                 self.executor._add_to_structured_data(all_phase_results)
                 action_for_history = {
                     "tool_name": "TDA_SystemLog",
                     "arguments": {"message": f"Multi-Tool Phase Item: {loop_item}"},
                     "metadata": {"execution_depth": self.executor.execution_depth, "timestamp": datetime.now(timezone.utc).isoformat()}
                 }
                 self.executor.turn_action_history.append({
                     "action": action_for_history,
                     "result": all_phase_results
                 })
            else:
                 self.executor.workflow_state[phase_result_key] = all_phase_results
                 self.executor.last_tool_output = all_phase_results
                 self.executor._add_to_structured_data(all_phase_results)
                 action_for_history = {
                     "tool_name": "TDA_SystemLog",
                     "arguments": {"message": f"Multi-Tool Phase: {phase_goal}"},
                     "metadata": {"execution_depth": self.executor.execution_depth, "timestamp": datetime.now(timezone.utc).isoformat()}
                 }
                 self.executor.turn_action_history.append({
                     "action": action_for_history,
                     "result": all_phase_results
                 })


            if not is_loop_iteration:
                event_data = {
                    "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                    "type": "phase_end",
                    "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": "completed", "execution_depth": self.executor.execution_depth}
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
            return


        tool_name = relevant_tools[0] if len(relevant_tools) == 1 else None

        # --- SYSTEM TOOL BYPASS: TDA_ContextReport with answer_from_context ---
        # Check if this is TDA_ContextReport with a pre-populated answer (from plan optimization)
        if tool_name == "TDA_ContextReport" and strategic_args.get("answer_from_context"):
            app_logger.info("Bypassing tactical LLM: TDA_ContextReport has pre-synthesized answer from plan optimization.")
            
            # Create the action directly without tactical LLM call
            action_to_execute = {
                "tool_name": "TDA_ContextReport",
                "arguments": strategic_args
            }
            
            # Execute the bypass logic directly (same as in _execute_action_with_orchestrators)
            self.executor.is_synthesis_from_history = True
            self.executor.last_tool_output = {
                "status": "success",
                "metadata": {"tool_name": "TDA_ContextReport"},
                "results": [{"response": strategic_args.get("answer_from_context")}]
            }
            
            # Log the action
            phase_num = phase.get("phase", self.executor.current_phase_index + 1)
            action_for_history = copy.deepcopy(action_to_execute)
            action_for_history.setdefault("metadata", {})["phase_number"] = phase_num
            action_for_history.setdefault("metadata", {})["execution_depth"] = self.executor.execution_depth
            action_for_history.setdefault("metadata", {})["timestamp"] = datetime.now(timezone.utc).isoformat()
            
            yield self.executor._format_sse_with_depth({
                "step": "Tool Execution Result",
                "details": self.executor.last_tool_output,
                "tool_name": "TDA_ContextReport"
            }, "tool_result")
            
            self.executor.turn_action_history.append({
                "action": action_for_history,
                "result": self.executor.last_tool_output
            })
            
            # Store result in workflow state
            phase_result_key = f"result_of_phase_{phase_num}"
            self.executor.workflow_state.setdefault(phase_result_key, []).append(self.executor.last_tool_output)
            self.executor._add_to_structured_data(self.executor.last_tool_output)
            
            # Phase complete
            if not is_loop_iteration:
                event_data = {
                    "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                    "type": "phase_end",
                    "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": "completed", "execution_depth": self.executor.execution_depth}
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
            return
        # --- END SYSTEM TOOL BYPASS ---

        # --- COMPONENT FAST-PATH: Deterministic component handlers (zero LLM calls) ---
        # Components with is_deterministic=True can bypass the tactical LLM entirely.
        # The chart component (TDA_Charting) is the primary user: it requires 4 args
        # (chart_type, data, title, mapping) — all deterministic. The tactical LLM
        # hallucinates mapping keys (x/y vs x_axis/y_axis) and column names, then
        # self-correction fails because it tries to re-emit all data rows in JSON.
        _component_bypass_handled = False
        try:
            from trusted_data_agent.components.manager import get_component_manager
            comp_manager = get_component_manager()
            if tool_name and comp_manager.is_component_tool(tool_name):
                comp_handler = comp_manager.get_handler(tool_name)
                if comp_handler and comp_handler.is_deterministic:
                    app_logger.info(f"Component Fast-Path: Handling {tool_name} deterministically via component '{comp_handler.component_id}'.")

                    # --- Chart-specific deterministic resolution ---
                    if tool_name == "TDA_Charting":
                        chart_type = strategic_args.get("chart_type", "bar")
                        title = phase_goal

                        # Step 1: Resolve data from workflow_state or previous_turn_data
                        temp_action = {"tool_name": "TDA_Charting", "arguments": dict(strategic_args)}
                        self._resolve_charting_data(temp_action)
                        resolved_data = temp_action["arguments"].get("data")

                        if not isinstance(resolved_data, list) or not resolved_data:
                            app_logger.warning("Component Fast-Path (chart): Could not resolve data. Falling through to tactical LLM.")
                        else:
                            # Step 2: Delegate mapping entirely to handler's 5-stage pipeline.
                            # The handler's _classify_columns() + _assign_roles() uses semantic
                            # name patterns AND cardinality filtering — strictly superior to the
                            # naive value-type classification in _generate_charting_mapping().
                            # Passing an empty mapping avoids data-transformation timing mismatches
                            # (fast-path sees pre-transform column names, handler renames them).
                            mapping = {}

                            # Step 3: Build complete action and execute directly
                            # (handler resolves mapping internally when missing)
                            # Start with strategic_args to preserve optional params
                            # (sort_by, sort_direction, row_limit, etc.)
                            charting_action = {
                                "tool_name": "TDA_Charting",
                                "arguments": {
                                    **strategic_args,
                                    "chart_type": chart_type,
                                    "data": resolved_data,
                                    "title": title,
                                    "mapping": mapping
                                }
                            }

                            event_data = {
                                "step": "Plan Optimization",
                                "type": "plan_optimization",
                                "details": {
                                    "summary": (
                                        f"Component Fast-Path ({comp_handler.component_id}): bypassing tactical LLM. "
                                        f"Resolved {len(resolved_data)} data rows, mapping: {mapping or '(handler-resolved)'}."
                                    ),
                                    "correction_type": "deterministic_component"
                                }
                            }
                            self.executor._log_system_event(event_data)
                            yield self.executor._format_sse_with_depth(event_data)

                            async for event in self._execute_action_with_orchestrators(charting_action, phase):
                                yield event

                            if not is_loop_iteration:
                                phase_num = phase.get("phase", self.executor.current_phase_index + 1)
                                event_data = {
                                    "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                                    "type": "phase_end",
                                    "details": {
                                        "phase_num": phase_num,
                                        "total_phases": len(self.executor.meta_plan),
                                        "status": "completed",
                                        "execution_depth": self.executor.execution_depth
                                    }
                                }
                                self.executor._log_system_event(event_data)
                                yield self.executor._format_sse_with_depth(event_data)
                            _component_bypass_handled = True

                    # --- Generic deterministic component fast-path ---
                    # For non-chart deterministic components (canvas, etc.), build
                    # an action from strategic args and execute via the shared path.
                    elif not _component_bypass_handled:
                        app_logger.info(f"Component Fast-Path ({comp_handler.component_id}): generic deterministic execution.")

                        generic_action = {
                            "tool_name": tool_name,
                            "arguments": dict(strategic_args),
                        }

                        event_data = {
                            "step": "Plan Optimization",
                            "type": "plan_optimization",
                            "details": {
                                "summary": (
                                    f"Component Fast-Path ({comp_handler.component_id}): "
                                    f"bypassing tactical LLM — deterministic pass-through."
                                ),
                                "correction_type": "deterministic_component"
                            }
                        }
                        self.executor._log_system_event(event_data)
                        yield self.executor._format_sse_with_depth(event_data)

                        async for event in self._execute_action_with_orchestrators(generic_action, phase):
                            yield event

                        if not is_loop_iteration:
                            phase_num = phase.get("phase", self.executor.current_phase_index + 1)
                            event_data = {
                                "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                                "type": "phase_end",
                                "details": {
                                    "phase_num": phase_num,
                                    "total_phases": len(self.executor.meta_plan),
                                    "status": "completed",
                                    "execution_depth": self.executor.execution_depth
                                }
                            }
                            self.executor._log_system_event(event_data)
                            yield self.executor._format_sse_with_depth(event_data)
                        _component_bypass_handled = True
        except Exception as comp_err:
            app_logger.warning(f"Component fast-path check failed for '{tool_name}', falling through: {comp_err}")

        if _component_bypass_handled:
            return
        # --- END COMPONENT FAST-PATH ---

        is_fast_path_candidate = False
        if tool_name:
            all_tools = self.executor.dependencies['STATE'].get('mcp_tools', {})
            tool_def = all_tools.get(tool_name)
            if tool_def:
                required_args_set = {name for name, details in (tool_def.args.items() if hasattr(tool_def, 'args') and isinstance(tool_def.args, dict) else {}) if details.get('required')}
                present_args_canonical = set(mcp_adapter._normalize_tool_arguments(strategic_args).keys())
                required_args_canonical = set()
                for req_arg in required_args_set:
                     found_canonical = False
                     for canonical, synonyms in AppConfig.ARGUMENT_SYNONYM_MAP.items():
                         if req_arg in synonyms:
                             required_args_canonical.add(canonical)
                             found_canonical = True
                             break
                     if not found_canonical:
                         required_args_canonical.add(req_arg)

                if required_args_canonical.issubset(present_args_canonical):
                     all_required_present_and_valid = True
                     for req_can_arg in required_args_canonical:
                          value = get_argument_by_canonical_name(strategic_args, req_can_arg)

                          # Reject None, empty string, or missing values
                          if value in [None, ""]:
                              all_required_present_and_valid = False
                              break

                          # Reject dict placeholders (Pass 0 temporal wiring, loop sources)
                          # Examples: {"source": "date_range", "duration": 2}, {"key": "phase_1_result"}
                          # EXCEPT the canonical phase-result reference, which the executor
                          # resolves deterministically — rejecting it sent a zero-unknowns
                          # phase to the tactical LLM, which could flatten the dict and
                          # lose `key` (see _is_resolvable_phase_ref).
                          if (isinstance(value, dict) and ("source" in value or "key" in value)
                                  and not self._is_resolvable_phase_ref(value)):
                              all_required_present_and_valid = False
                              app_logger.debug(
                                  f"FASTPATH REJECTED: Argument '{req_can_arg}' contains dict placeholder: {value}"
                              )
                              break

                          # Reject list placeholders (hallucinated loop items)
                          # Example: ["item1", "item2"] from loop without TDA_LLMTask wrapper
                          if isinstance(value, list) and len(value) > 0 and all(isinstance(x, str) for x in value):
                              all_required_present_and_valid = False
                              app_logger.debug(
                                  f"FASTPATH REJECTED: Argument '{req_can_arg}' contains list placeholder: {value}"
                              )
                              break

                          # Reject temporal phrases that need date resolution
                          # Examples: "past 5 days", "last week", "yesterday"
                          if isinstance(value, str):
                              temporal_phrase_patterns = [
                                  r'past\s+\d+\s+(hours?|days?|weeks?|months?)',
                                  r'last\s+\d+\s+(hours?|days?|weeks?|months?)',
                                  r'(yesterday|today)',
                                  r'in\s+the\s+(last|past)',
                                  r'for\s+the\s+(past|last)',
                                  r'\d+\s+(hours?|days?|weeks?|months?)\s+ago'
                              ]
                              if any(re.search(pattern, value.lower()) for pattern in temporal_phrase_patterns):
                                  all_required_present_and_valid = False
                                  app_logger.debug(
                                      f"FASTPATH REJECTED: Argument '{req_can_arg}' contains temporal phrase: '{value}'"
                                  )
                                  break

                              # §2 fix: reject a value that STILL contains an UNRESOLVED phase
                              # reference — a bare 'result_of_phase_N', a braced '{result_of_phase_N}',
                              # or 'injected_previous_turn_data'. _resolve_arguments leaves these
                              # untouched when the source phase hasn't produced its result yet;
                              # fast-pathing would send the literal placeholder text to the tool
                              # (e.g. as SQL). Fall to the tactical/refinement path so it resolves.
                              if re.search(r'result_of_phase_\d+', value) or value.strip() == "injected_previous_turn_data":
                                  all_required_present_and_valid = False
                                  app_logger.debug(
                                      f"FASTPATH REJECTED: Argument '{req_can_arg}' contains an unresolved phase reference: '{value}'"
                                  )
                                  break
                     if all_required_present_and_valid:
                         is_fast_path_candidate = True


        # A statement the schema_before_sql pass withdrew is written by the tactical step —
        # never run without SQL (a server that does not mark `sql` required answers
        # "No SQL provided" as a success).
        if is_fast_path_candidate and phase.get("_sql_draft") and not str((strategic_args or {}).get("sql") or "").strip():
            is_fast_path_candidate = False
        # …and the same for ANY phase on a SQL runner that carries no statement. OC-F17 (Optimize
        # campaign, 2026-10-29): FastMCP 4's schema for `base_readQuery` declares `sql` optional
        # (`default: null`), so the required-arguments check above passed on `{}`; FASTPATH ran it,
        # the server answered "No SQL provided" as a SUCCESS, the phase completed and the report
        # said the query never ran. A statement is the one argument such a tool cannot run without.
        if is_fast_path_candidate and _takes_sql(tool_def if tool_name else None) \
                and not str((strategic_args or {}).get("sql") or "").strip():
            is_fast_path_candidate = False
            app_logger.info(f"FASTPATH REJECTED: '{tool_name}' takes a SQL statement and the plan carries none.")

        if is_fast_path_candidate:
            event_data = {
                "step": "Plan Optimization",
                "type": "plan_optimization",
                "details": {
                    "summary": f"FASTPATH initiated for '{tool_name}'.",
                    "phase_num": phase.get('phase', phase_num)
                }
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)

            # Track configured tactical model even when FASTPATH skips LLM call
            if self.executor.is_dual_model_active:
                from trusted_data_agent.core.session_manager import update_models_used
                await update_models_used(
                    user_uuid=self.executor.user_uuid,
                    session_id=self.executor.session_id,
                    provider=self.executor.tactical_provider,
                    model=self.executor.tactical_model,
                    profile_tag=None,
                    planning_phase="tactical_fastpath"  # Distinguish FASTPATH from actual LLM calls
                )
                app_logger.debug(f"[Dual-Model FASTPATH] Tracked configured tactical model: {self.executor.tactical_provider}/{self.executor.tactical_model}")

            fast_path_action = {"tool_name": tool_name, "arguments": strategic_args}

            if tool_name == "TDA_LLMTask" and is_loop_iteration and loop_item:
                modified_args = fast_path_action["arguments"].copy()
                task_desc = modified_args.get("task_description", "")
                loop_item_str = json.dumps(loop_item)

                modified_args["task_description"] = (
                    f"{task_desc}\n\n"
                    f"CRITICAL CONTEXT: You MUST focus your response on the following item provided from the loop: {loop_item_str}"
                )
                app_logger.info(f"Injected loop context into TDA_LLMTask description for item: {loop_item_str}")

                source_data_keys = modified_args.get("source_data", [])
                focused_data_payload = {}
                if source_data_keys:
                    app_logger.info(f"Filtering source data for loop item based on keys: {source_data_keys}")
                    loop_item_id_key = None
                    loop_item_id_value = None
                    possible_id_keys = ['TableName', 'ProductID', 'CustomerID', 'SaleID', 'TicketID', 'ColumnName'] # Extend as needed
                    for key in possible_id_keys:
                        if key in loop_item:
                            loop_item_id_key = key
                            loop_item_id_value = loop_item[key]
                            break

                    if loop_item_id_key:
                        app_logger.debug(f"Matching loop item using key '{loop_item_id_key}' with value '{loop_item_id_value}'")
                        for source_key in source_data_keys:
                            if source_key in self.executor.workflow_state:
                                full_data_list = self.executor.workflow_state[source_key]
                                if isinstance(full_data_list, list):
                                    matching_items = []
                                    for data_item in full_data_list:
                                        potential_match_locations = []
                                        if isinstance(data_item, dict):
                                            potential_match_locations.append(data_item)
                                            if 'results' in data_item and isinstance(data_item['results'], list):
                                                potential_match_locations.extend(data_item['results'])
                                            if 'metadata' in data_item and isinstance(data_item['metadata'], dict):
                                                potential_match_locations.append(data_item['metadata'])

                                        found_match_in_item = False
                                        for location in potential_match_locations:
                                            if isinstance(location, dict) and location.get(loop_item_id_key) == loop_item_id_value:
                                                matching_items.append(data_item)
                                                found_match_in_item = True
                                                break

                                        if not found_match_in_item:
                                            if any(loop_item == loc for loc in potential_match_locations if isinstance(loc, dict)):
                                                 matching_items.append(data_item)
                                                 app_logger.debug(f"Found match for loop item via deep comparison in source '{source_key}'.")


                                    if matching_items:
                                        focused_data_payload.setdefault(source_key, []).extend(matching_items)
                                        app_logger.debug(f"Found {len(matching_items)} matching item(s) in source '{source_key}' for current loop item.")
                                    else:
                                         app_logger.warning(f"Could not find matching data in source '{source_key}' for loop item key '{loop_item_id_key}'='{loop_item_id_value}'. Source data might be missing or key mismatch.")
                                else:
                                     app_logger.warning(f"Source data '{source_key}' in workflow state is not a list.")
                            else:
                                app_logger.warning(f"Source data key '{source_key}' not found in workflow state.")
                    else:
                        app_logger.warning(f"Could not find a reliable unique identifier (e.g., TableName) in loop item {loop_item} to filter source data.")
                        focused_data_payload = {k: self.executor.workflow_state[k] for k in source_data_keys if k in self.executor.workflow_state}

                else:
                    app_logger.warning("No source data keys specified for TDA_LLMTask in loop.")
                    focused_data_payload = {}


                distill_events = []
                modified_args["data"] = self.executor._distill_data_for_llm_context(focused_data_payload, _events=distill_events)
                for evt in distill_events:
                    event_data = {"step": "Context Optimization", "type": "context_optimization", "details": evt}
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)
                self.executor._distillation_events.extend(distill_events)
                fast_path_action["arguments"] = modified_args
                app_logger.info(f"Prepared focused data payload for TDA_LLMTask loop item: {json.dumps(modified_args['data'])}")


            async for event in self._execute_action_with_orchestrators(fast_path_action, phase):
                yield event

            call_id_for_completion = None
            if isinstance(self.executor.last_tool_output, dict):
                call_id_for_completion = self.executor.last_tool_output.get("metadata", {}).get("call_id")

            completion_event_payload = {"target": "context", "state": "processing_complete"}
            if call_id_for_completion:
                completion_event_payload["call_id"] = call_id_for_completion

            yield self.executor._format_sse_with_depth(completion_event_payload, "context_state_update")

            if not is_loop_iteration:
                # §2 fix: reflect the actual FASTPATH outcome in the phase_end status instead of a
                # hardcoded "completed". A FASTPATH phase whose tool ERRORED (after its own
                # self-correction in _execute_tool) was stamped "completed", hiding the failure from
                # trace analysis, honest reporting, and RAG seed classification.
                _fp_status = "completed"
                if isinstance(self.executor.last_tool_output, dict) and self.executor.last_tool_output.get("status") == "error":
                    _fp_status = "error"
                event_data = {
                    "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                    "type": "phase_end",
                    "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": _fp_status, "execution_depth": self.executor.execution_depth}
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
            return

        # --- Tactical LLM Path (Slow Path) ---
        phase_attempts = 0
        max_phase_attempts = APP_CONFIG.MAX_PHASE_RETRY_ATTEMPTS
        tactical_call_id = None
        while True:
            phase_attempts += 1
            if phase_attempts > max_phase_attempts:
                # C2 (removed, user decision): the autonomous LLM "recovery replan" was dead code.
                # It generated a new plan and set meta_plan/current_phase_index=0, but _run_plan
                # iterates a phase_groups snapshot built ONCE from the OLD plan — so the recovery
                # plan NEVER executed (the LLM call was paid and "Recovery Plan Generated" emitted,
                # yet the ORIGINAL plan's remaining phases ran). Orphaned by the Apr-2026 parallel
                # phase-groups refactor. Honest behavior: record the exhausted-phase failure as an
                # error result (so downstream phases + the final report reflect the missing data)
                # and end the phase — the turn continues and synthesises from what was gathered.
                app_logger.error(f"Phase '{phase_goal}' failed after {max_phase_attempts} attempts.")
                _fail_result = {
                    "status": "error",
                    "metadata": {"phase_num": phase_num},
                    "error_message": {
                        "summary": f"Phase failed after {max_phase_attempts} attempts.",
                        "details": f"The agent could not complete the phase goal: '{phase_goal}'.",
                    },
                }
                self.executor.last_tool_output = _fail_result
                _phase_result_key = f"result_of_phase_{phase_num}"
                self.executor.workflow_state[_phase_result_key] = _fail_result
                self.executor._add_to_structured_data(_fail_result)
                event_data = {
                    "step": f"Phase Failed After {max_phase_attempts} Attempts",
                    "type": "error",
                    "details": _fail_result,
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
                return

            enriched_args, enrich_events, _ = self._enrich_arguments_from_history(relevant_tools, strategic_args) # Pass strategic_args here

            for event in enrich_events:
                self.executor.events_to_yield.append(event)

            tactical_call_id = str(uuid.uuid4())
            # Match strategic planning pattern (planner.py:2793-2829):
            # Only emit status_indicator before LLM call; emit system_message
            # AFTER call completes so it includes token counts.
            yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")

            next_action, input_tokens, output_tokens = await self._get_next_tactical_action(
                phase_goal, relevant_tools, enriched_args, strategic_args, executable_prompt,
                phase_num=phase_num
            )

            # Emit any context distillation events from tactical planning
            for evt in getattr(self, '_pending_distill_events', []):
                event_data = {"step": "Context Optimization", "type": "context_optimization", "details": evt}
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
            self.executor._distillation_events.extend(getattr(self, '_pending_distill_events', []))
            self._pending_distill_events = []

            # Emit tactical per-call context window snapshot (mirrors strategic snapshot in planner.py)
            _tac_snap = getattr(self, '_pending_tactical_snapshot', None)
            if _tac_snap:
                yield self.executor._format_sse_with_depth({
                    "step": "Context Assembly (Tactical)",
                    "type": "context_window_snapshot",
                    "payload": _tac_snap,
                })
                self._pending_tactical_snapshot = None

            # --- Emit tactical LLM call with token data (live SSE + execution trace) ---
            tactical_log_event = {
                "step": "Calling LLM for Tactical Action",
                "type": "system_message",
                "details": {
                    "summary": f"Deciding next action for phase goal: '{phase_goal}'",
                    "call_id": tactical_call_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0),
                    "planning_phase": "tactical"
                }
            }
            self.executor._log_system_event(tactical_log_event)
            yield self.executor._format_sse_with_depth(tactical_log_event)

            yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

            current_action_str = json.dumps(next_action, sort_keys=True)
            if current_action_str == _ps['last_action_str']:
                app_logger.warning(f"LOOP DETECTED: Repeating action: {current_action_str}")
                error_msg = "Exact repeat of previous action detected. You MUST choose a different tool or different arguments."
                _ps['correction_history'].append({
                    "attempt": len(_ps['correction_history']) + 1,
                    "tool_name": next_action.get("tool_name", "unknown") if isinstance(next_action, dict) else "unknown",
                    "arguments": next_action.get("arguments", {}) if isinstance(next_action, dict) else {},
                    "error": error_msg,
                    "strategy": "LoopDetection",
                })
                self.executor.last_failed_action_info = error_msg
                yield self.executor._format_sse_with_depth({"step": "System Error", "details": "Repetitive action detected.", "type": "error"}, "tool_result")
                # §2 fix: do NOT reset the sentinel to None here. Resetting it disarmed the breaker
                # on the very next attempt — if the LLM repeated the same action a third time,
                # `current == None` was False, so it was NOT caught and executed anyway (the breaker
                # only fired every OTHER attempt). Keeping the repeated action means a persistent
                # repeat keeps being caught (the LoopDetection correction is already in the history).
                continue
            _ps['last_action_str'] = current_action_str

            if self.executor.events_to_yield:
                for event in self.executor.events_to_yield: yield event
                self.executor.events_to_yield = []

            # --- MODIFICATION START: Pass user_uuid to get_session ---
            updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
            # --- MODIFICATION END ---
            if updated_session:
                yield self.executor._format_sse_with_depth({ "statement_input": input_tokens, "statement_output": output_tokens, "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens, "total_input": updated_session.get("input_tokens", 0), "total_output": updated_session.get("output_tokens", 0), "call_id": tactical_call_id, "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0), "planning_phase": "tactical" }, "token_update")

            if isinstance(next_action, str) and next_action == "SYSTEM_ACTION_COMPLETE":
                app_logger.info("Tactical LLM decided phase is complete.")
                break # Exit the while loop for this phase/iteration

            if not isinstance(next_action, dict):
                raise RuntimeError(f"Tactical LLM failed to provide a valid action. Received: {next_action}")

            async for event in self._execute_action_with_orchestrators(next_action, phase):
                yield event

            is_standard_success = (isinstance(self.executor.last_tool_output, dict) and self.executor.last_tool_output.get("status") == "success")
            is_chart_success = (isinstance(self.executor.last_tool_output, dict) and self.executor.last_tool_output.get("type") == "chart")

            if self.executor.last_tool_output and (is_standard_success or is_chart_success):
                if next_action.get("tool_name") == "TDA_Charting":
                    is_valid_chart = True
                    spec = self.executor.last_tool_output.get("spec", {})
                    options = spec.get("options", {})
                    mapping_keys = ['xField', 'yField', 'seriesField', 'angleField', 'colorField']
                    if not any(key in options for key in mapping_keys):
                        is_valid_chart = False
                        chart_error_msg = "Chart failed: 'mapping' argument was incorrect or missing. You MUST provide a valid mapping with the correct keys (e.g., 'angle', 'color')."
                        _ps['correction_history'].append({
                            "attempt": len(_ps['correction_history']) + 1,
                            "tool_name": "TDA_Charting",
                            "arguments": next_action.get("arguments", {}),
                            "error": chart_error_msg,
                            "strategy": "ChartValidation",
                        })
                        self.executor.last_failed_action_info = chart_error_msg

                    if is_valid_chart:
                        mapping = next_action.get("arguments", {}).get("mapping", {})
                        data = next_action.get("arguments", {}).get("data", [])
                        if data and mapping and isinstance(data, list) and data[0]: # Check if data is list and not empty
                            first_row = data[0]
                            numeric_roles = ['angle', 'y_axis', 'value']
                            for role, column_name in mapping.items():
                                if role.lower() in numeric_roles:
                                    if column_name in first_row and not self._is_numeric(first_row[column_name]):
                                        is_valid_chart = False
                                        chart_error_msg = f"Chart failed: non-numeric column '{column_name}' mapped to '{role}' role which requires a number. You MUST map a numeric column to this role."
                                        _ps['correction_history'].append({
                                            "attempt": len(_ps['correction_history']) + 1,
                                            "tool_name": "TDA_Charting",
                                            "arguments": next_action.get("arguments", {}),
                                            "error": chart_error_msg,
                                            "strategy": "ChartValidation",
                                        })
                                        self.executor.last_failed_action_info = chart_error_msg
                                        break

                    if not is_valid_chart:
                        app_logger.warning(f"Silent chart failure detected. Reason: {self.executor.last_failed_action_info}")
                        continue

                # A phase whose statement the schema_before_sql pass withdrew is answered by a
                # QUERY: a discovery action there read the schema (facts), it did not answer the
                # phase. Record its facts for the next tactical attempt and keep going.
                if (phase.get("_sql_draft") and is_standard_success
                        and _sf.is_discovery_tool(next_action.get("tool_name"))):
                    _sf.record_discovery(self.executor.session_id, next_action, self.executor.last_tool_output)
                    _facts = _sf.discovery_facts_text(next_action, self.executor.last_tool_output)
                    _ps['correction_history'].append({
                        "attempt": len(_ps['correction_history']) + 1,
                        "tool_name": next_action.get("tool_name"),
                        "arguments": next_action.get("arguments", {}),
                        "error": ("This call read the schema; it does not answer this phase. Now write "
                                  "the query for the phase's own tool using only these real names:\n"
                                  + (_facts or "(the call returned no columns — that table does not exist; "
                                              "use a table from the listing)")),
                        "strategy": "SchemaBeforeSql",
                    })
                    _sev = {"step": "System Correction", "type": "workaround", "details": {
                        "pass": "schema_before_sql",
                        "summary": (f"'{next_action.get('tool_name')}' read the schema for phase {phase_num}; "
                                    f"the phase's query is written next with the real names.")}}
                    self.executor._log_system_event(_sev)
                    yield self.executor._format_sse_with_depth(_sev)
                    continue

                _ps['last_action_str'] = None
                break # Successful action, exit the while loop
            else:
                app_logger.warning(f"Action failed. Attempt {phase_attempts}/{max_phase_attempts} for phase.")
                # Loop continues for retry

        yield self.executor._format_sse_with_depth(
            {"target": "context", "state": "processing_complete", "call_id": tactical_call_id},
            "context_state_update"
        )
        if not is_loop_iteration:
            event_data = {
                "step": f"Ending Plan Phase {phase_num}/{len(self.executor.meta_plan)}",
                "type": "phase_end",
                "details": {"phase_num": phase_num, "total_phases": len(self.executor.meta_plan), "status": "completed", "execution_depth": self.executor.execution_depth}
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)


    async def _execute_action_with_orchestrators(self, action: dict, phase: dict):
        """
        A wrapper that runs pre-flight checks (orchestrators) before executing a tool.
        These orchestrators act as a safety net for common planning mistakes.
        """
        tool_name = action.get("tool_name")
        prompt_name = action.get("prompt_name")

        # --- EPC: tactical_decision step ---
        try:
            if hasattr(self.executor, 'provenance') and self.executor.provenance:
                import json as _json
                _epc_phase_num = phase.get("phase", "?")
                _epc_args = action.get("arguments", {})
                self.executor.provenance.add_step(
                    "tactical_decision",
                    f"{_epc_phase_num}:{tool_name or prompt_name}:{_json.dumps(_epc_args, sort_keys=True, default=str)}",
                    f"Phase {_epc_phase_num}: {tool_name or prompt_name}")
        except Exception:
            pass
        # --- EPC END ---

        if not tool_name and not prompt_name:
            raise ValueError("Action from tactical LLM is missing a 'tool_name' or 'prompt_name'.")

        if prompt_name:
            yield self.executor._format_sse_with_depth({
                "step": "Prompt Execution Granted",
                "details": f"Executing prompt '{prompt_name}' as a sub-task.",
                "type": "workaround"
            })
            async for event in self.executor._run_sub_prompt(prompt_name, action.get("arguments", {})):
                yield event
            return

        # === Charting Data Resolver ===
        # Resolve placeholder references in TDA_Charting data argument.
        # Less capable LLMs output "result_of_phase_2" (string) instead of actual data.
        if action.get("tool_name") == "TDA_Charting":
            if self._resolve_charting_data(action):
                data_len = len(action.get("arguments", {}).get("data", []))
                event_data = {
                    "step": "System Correction",
                    "type": "workaround",
                    "details": {
                        "summary": (
                            f"The agent referenced phase results by placeholder instead of "
                            f"embedding actual data. Resolved to {data_len} rows from workflow state."
                        ),
                        "correction_type": "charting_data_resolution"
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
        # === End Charting Data Resolver ===

        # === Dashboard Content Resolver ===
        # Resolve placeholder content in TDA_Dashboard (visualize/add_tile actions).
        # Same root cause as TDA_Charting: LLMs emit "result_of_previous_query" instead of data.
        if action.get("tool_name") == "TDA_Dashboard":
            if self._resolve_dashboard_content(action):
                event_data = {
                    "step": "System Correction",
                    "type": "workaround",
                    "details": {
                        "summary": "Dashboard visualization referenced data by placeholder — resolved from workflow state.",
                        "correction_type": "dashboard_content_resolution"
                    }
                }
                self.executor._log_system_event(event_data)
                yield self.executor._format_sse_with_depth(event_data)
        # === End Dashboard Content Resolver ===

        # === Column Iteration Orchestrator Check ===
        # Check if this is a column-scoped tool missing column_name argument
        # This handles single-tool phases that bypass the multi-tool dispatcher
        arguments = action.get("arguments", {})
        tool_scope = self.executor.dependencies['STATE'].get('tool_scopes', {}).get(tool_name)
        has_column_arg = get_argument_by_canonical_name(arguments, 'column_name') is not None

        if tool_scope == 'column' and not has_column_arg:
            app_logger.info(f"Tool '{tool_name}' is column-scoped but missing column_name. Invoking column iteration orchestrator.")

            event_data = {
                "step": "Scope-Aware Dispatcher Action",
                "type": "plan_optimization",
                "details": f"Dispatcher is invoking column iteration for '{tool_name}' because 'column_name' was missing."
            }
            self.executor._log_system_event(event_data)
            yield self.executor._format_sse_with_depth(event_data)

            try:
                async for event in orchestrators.execute_column_iteration(self.executor, action):
                    yield event
                # --- MODIFICATION START: Manually log orchestrator action to history ---
                action_for_history = copy.deepcopy(action)
                action_for_history.setdefault("metadata", {})["execution_depth"] = self.executor.execution_depth
                action_for_history.setdefault("metadata", {})["timestamp"] = datetime.now(timezone.utc).isoformat()
                self.executor.turn_action_history.append({"action": action_for_history, "result": self.executor.last_tool_output})
                # --- MODIFICATION END ---
                return  # Column orchestrator handled execution
            except Exception as orch_e:
                app_logger.error(f"Column iteration orchestrator failed for '{tool_name}': {orch_e}", exc_info=True)
                # Fall through to normal tool execution for recovery
        # === End Column Iteration Check ===

        is_range_candidate, date_param_name, tool_supports_range = self._is_date_query_candidate(action)
        is_date_orchestrator_target = False
        known_temporal_phrase = None  # Set when phrase is already known from args (skips LLM classification)
        if is_range_candidate and tool_name not in ["TDA_DateRange", "TDA_CurrentDate"]:
            if not tool_supports_range:
                # Single-date tool — always route to orchestrator (existing behavior)
                is_date_orchestrator_target = True
                # Extract temporal phrase from the date argument if available
                action_args = action.get("arguments", {})
                date_val = action_args.get(date_param_name, "")
                if isinstance(date_val, str) and date_val:
                    temporal_patterns_single = [
                        r'past\s+\d+\s+(hours?|days?|weeks?|months?)',
                        r'last\s+\d+\s+(hours?|days?|weeks?|months?)',
                        r'(yesterday|today)',
                        r'in\s+the\s+(last|past)',
                        r'for\s+the\s+(past|last)',
                        r'\d+\s+(hours?|days?|weeks?|months?)\s+ago',
                    ]
                    if any(re.search(p, date_val.lower()) for p in temporal_patterns_single):
                        known_temporal_phrase = date_val
                        app_logger.info(
                            f"ORCHESTRATOR: Single-date tool '{tool_name}' has temporal phrase "
                            f"'{date_val}' in '{date_param_name}'. Extracted for deterministic routing."
                        )
            else:
                # Range tool (start_date + end_date) — route to orchestrator in two cases:
                args = action.get("arguments", {})
                start_val = args.get("start_date", "")
                end_val = args.get("end_date", "")

                if not start_val and not end_val:
                    # Case 1: Both date args missing — placeholder resolution likely failed.
                    # The planner wired date refs (e.g., result_of_phase_2.start_date) but
                    # TDA_DateRange returns {"date": ...}, not {"start_date": ...}.
                    app_logger.info(
                        f"ORCHESTRATOR: Range tool '{tool_name}' has MISSING start_date/end_date "
                        f"(placeholder resolution failed). Routing to orchestrator for date resolution."
                    )
                    is_date_orchestrator_target = True
                elif not isinstance(start_val, str) or (end_val and not isinstance(end_val, str)):
                    # Case 3: Date args resolved to non-string (list/dict from TDA_DateRange result).
                    # The planner piped a TDA_DateRange result directly as start_date, producing
                    # a list of date dicts instead of a YYYY-MM-DD string. Clear the invalid
                    # values and route to orchestrator for proper date resolution.
                    action["arguments"].pop("start_date", None)
                    action["arguments"].pop("end_date", None)
                    app_logger.info(
                        f"ORCHESTRATOR: Range tool '{tool_name}' has non-string date args "
                        f"(start_date type={type(start_val).__name__}). "
                        f"Clearing and routing to orchestrator for date resolution."
                    )
                    is_date_orchestrator_target = True
                else:
                    # Case 2: Date args present but contain unresolved temporal phrase
                    date_arg_value = args.get(date_param_name, "")
                    if isinstance(date_arg_value, str) and date_arg_value:
                        temporal_patterns = [
                            r'past\s+\d+\s+(hours?|days?|weeks?|months?)',
                            r'last\s+\d+\s+(hours?|days?|weeks?|months?)',
                            r'(yesterday|today)',
                            r'in\s+the\s+(last|past)',
                            r'for\s+the\s+(past|last)',
                            r'\d+\s+(hours?|days?|weeks?|months?)\s+ago',
                        ]
                        if any(re.search(p, date_arg_value.lower()) for p in temporal_patterns):
                            app_logger.info(
                                f"ORCHESTRATOR: Range tool '{tool_name}' has temporal phrase "
                                f"'{date_arg_value}' in '{date_param_name}'. Routing to orchestrator."
                            )
                            is_date_orchestrator_target = True
                            known_temporal_phrase = date_arg_value

        if is_date_orchestrator_target:
            # Phase goal fallback: if phrase not found in action args, extract from phase goal.
            # This covers Case 1 (empty args), Case 3 (non-string args), and single-date tools
            # where Pass 0 didn't inject a temporal phrase into the argument value.
            if not known_temporal_phrase:
                phase_goal = phase.get('goal', '')
                goal_match = re.search(
                    r'((?:past|last|next)\s+\d+\s+(?:hours?|days?|weeks?|months?|years?)'
                    r'|yesterday|today'
                    r'|this\s+(?:week|month|year)'
                    r'|last\s+(?:week|month|year))',
                    phase_goal, re.IGNORECASE
                )
                if goal_match:
                    known_temporal_phrase = goal_match.group(1)
                    app_logger.info(
                        f"ORCHESTRATOR: Extracted temporal phrase from phase goal: "
                        f"'{known_temporal_phrase}' (goal: '{phase_goal}')"
                    )

            # Fallback 2: Extract from original user input.
            # Covers plans where TDA_DateRange phase absorbed the temporal phrase
            # and left the target tool phase with a generic goal.
            if not known_temporal_phrase:
                user_input_lower = self.executor.original_user_input.lower()
                input_match = re.search(
                    r'((?:past|last|next)\s+\d+\s+(?:hours?|days?|weeks?|months?|years?)'
                    r'|yesterday|today'
                    r'|this\s+(?:week|month|year)'
                    r'|last\s+(?:week|month|year)'
                    r'|current\s+(?:week|month|year))',
                    user_input_lower
                )
                if input_match:
                    known_temporal_phrase = input_match.group(1)
                    app_logger.info(
                        f"ORCHESTRATOR: Extracted temporal phrase from user input: "
                        f"'{known_temporal_phrase}'"
                    )

            if known_temporal_phrase:
                # DETERMINISTIC: type from tool schema, phrase from args or goal.
                # Always 'range' — the orchestrator handles single dates too
                # ("yesterday" → start=end, "today" → start=end).
                self.executor.temp_data_holder = {'type': 'range', 'phrase': known_temporal_phrase}
                app_logger.info(
                    f"ORCHESTRATOR: Deterministic date routing — "
                    f"phrase='{known_temporal_phrase}'"
                )
            else:
                # LAST RESORT: no phrase in args, goal, or user input — use LLM classification.
                # This should only fire for queries with no recognizable temporal phrase at all.
                app_logger.warning(
                    f"ORCHESTRATOR: No temporal phrase found in args, phase goal, or user input "
                    f"for '{tool_name}'. Falling back to LLM classification."
                )
                yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")
                async for event in self._classify_date_query_type(): yield event
                yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")

            # For range tools (start_date + end_date), accept both 'range' and 'single' classifications.
            # "today" is often classified as 'single', but the orchestrator can still resolve it
            # to start_date=end_date=same_date. For single-date tools, only 'range' is needed.
            classified_type = self.executor.temp_data_holder.get('type') if self.executor.temp_data_holder else None
            should_orchestrate = (
                classified_type == 'range' or
                (classified_type == 'single' and tool_supports_range)
            )
            if should_orchestrate:
                async for event in orchestrators.execute_date_range_orchestrator(
                    self.executor, action, date_param_name, self.executor.temp_data_holder.get('phrase'), phase,
                    tool_supports_range=tool_supports_range
                ):
                    yield event
                # --- MODIFICATION START: Manually log orchestrator action to history ---
                action_for_history = copy.deepcopy(action)
                action_for_history.setdefault("metadata", {})["execution_depth"] = self.executor.execution_depth
                action_for_history.setdefault("metadata", {})["timestamp"] = datetime.now(timezone.utc).isoformat()
                self.executor.turn_action_history.append({"action": action_for_history, "result": self.executor.last_tool_output})
                phase_num = phase.get("phase", self.executor.current_phase_index + 1)
                phase_result_key = f"result_of_phase_{phase_num}"
                if phase_result_key not in self.executor.workflow_state:
                    self.executor.workflow_state[phase_result_key] = []
                if self.executor.last_tool_output not in self.executor.workflow_state[phase_result_key]:
                    self.executor.workflow_state[phase_result_key].append(self.executor.last_tool_output)
                self.executor._add_to_structured_data(self.executor.last_tool_output)
                # --- MODIFICATION END ---
                return

        if phase.get("type") == "loop" and isinstance(phase.get("loop_over"), list) and all(isinstance(i, str) for i in phase["loop_over"]):
             app_logger.warning("Detected hallucinated loop over strings. Invoking orchestrator.")
             async for event in orchestrators.execute_hallucinated_loop(self.executor, phase):
                 yield event
             # --- MODIFICATION START: Manually log orchestrator action to history ---
             action_for_history = {
                 "tool_name": "TDA_SystemLog",
                 "arguments": {"message": f"Hallucinated Loop: {phase.get('goal')}"},
                 "metadata": {"execution_depth": self.executor.execution_depth, "timestamp": datetime.now(timezone.utc).isoformat()}
             }
             self.executor.turn_action_history.append({"action": action_for_history, "result": self.executor.last_tool_output})
             phase_num = phase.get("phase", self.executor.current_phase_index + 1)
             phase_result_key = f"result_of_phase_{phase_num}"
             if phase_result_key not in self.executor.workflow_state:
                 self.executor.workflow_state[phase_result_key] = []
             if self.executor.last_tool_output not in self.executor.workflow_state[phase_result_key]:
                 self.executor.workflow_state[phase_result_key].append(self.executor.last_tool_output)
             self.executor._add_to_structured_data(self.executor.last_tool_output)
             # --- MODIFICATION END ---
             return


        async for event in self._execute_tool(action, phase):
            yield event


    async def _proactively_refine_arguments(self, action: dict, phase: dict):
        """
        Performs an intelligent, LLM-driven pre-flight check on tool arguments. It
        detects and corrects mismatches between the planner's provided arguments
        and the tool's actual schema, preventing validation errors.
        Now preserves the 'data' key for TDA_LLMTask in loops and skips refinement
        if only optional arguments are missing.
        """
        tool_name = action.get("tool_name")
        if not tool_name:
            return

        tool_def = self.executor.dependencies['STATE'].get('mcp_tools', {}).get(tool_name)
        if not tool_def or not hasattr(tool_def, 'args') or not isinstance(tool_def.args, dict):
            return

        provided_args = action.get("arguments", {})

        is_llm_task_in_loop = tool_name == "TDA_LLMTask" and self.executor.is_in_loop
        focused_data_payload = None
        if is_llm_task_in_loop and "data" in provided_args:
            focused_data_payload = provided_args.pop("data")
            app_logger.debug("Temporarily removing focused 'data' payload for argument refinement.")


        normalized_provided_args = mcp_adapter._normalize_tool_arguments(provided_args)
        tool_canonical_arg_names_required = set()
        tool_canonical_arg_names_all = set()

        for schema_arg_name, schema_details in tool_def.args.items():
            is_required = schema_details.get('required', False)
            found_canonical = False
            for canonical, synonyms in AppConfig.ARGUMENT_SYNONYM_MAP.items():
                if schema_arg_name in synonyms:
                    tool_canonical_arg_names_all.add(canonical)
                    if is_required:
                        tool_canonical_arg_names_required.add(canonical)
                    found_canonical = True
                    break
            if not found_canonical: # Argument is its own canonical name
                tool_canonical_arg_names_all.add(schema_arg_name)
                if is_required:
                    tool_canonical_arg_names_required.add(schema_arg_name)

        provided_canonical_arg_names = set(normalized_provided_args.keys())

        # --- MODIFICATION START: Check if only optional arguments are missing ---
        missing_canonical_args = tool_canonical_arg_names_all - provided_canonical_arg_names
        extraneous_canonical_args = provided_canonical_arg_names - tool_canonical_arg_names_all
        missing_required_args = tool_canonical_arg_names_required - provided_canonical_arg_names

        # Check for None values in required arguments (treat as missing)
        none_valued_required_args = set()
        for req_arg in tool_canonical_arg_names_required:
            if req_arg in normalized_provided_args and normalized_provided_args[req_arg] is None:
                none_valued_required_args.add(req_arg)
                missing_required_args.add(req_arg)  # Treat None as missing

        if none_valued_required_args:
            app_logger.warning(f"Required arguments have None values: {none_valued_required_args}. Will call refinement LLM.")

        # Check if planner marked this phase as needing refinement (after stripping extraneous args)
        force_refinement = phase.get('_needs_refinement', False)
        if '_needs_refinement' in phase:
            del phase['_needs_refinement']  # Clean up internal flag

        # A relative date range is anchored on TODAY. When the plan left TDA_DateRange's anchor
        # out and the turn already knows today's date (TDA_CurrentDate ran), the anchor is not a
        # judgement — fill it before any refinement LLM is asked. Fusion bench 2026-09-24: the
        # refinement LLM invented "2024-05-22" one phase after TDA_CurrentDate returned
        # 2026-09-24, and the turn counted the wrong month.
        if tool_name == "TDA_DateRange" and "start_date" in missing_required_args:
            _today = None
            try:
                _today = (self.executor._turn_date_context() or {}).get("today")
            except Exception:  # noqa: BLE001 — no context ⇒ the refinement path decides, as before
                _today = None
            if _today:
                action.setdefault("arguments", {})["start_date"] = _today
                missing_required_args.discard("start_date")
                missing_canonical_args.discard("start_date")
                if not missing_required_args and not extraneous_canonical_args:
                    force_refinement = False       # the only reason for refinement is resolved
                yield self.executor._format_sse_with_depth({
                    "step": "System Correction", "type": "workaround",
                    "details": {"summary": f"The date range had no anchor; anchored it on today's date ({_today}) "
                                           f"from this turn's TDA_CurrentDate (a mechanical fix, no LLM round).",
                                "correction_type": "date_range_anchor"}})

        # Skip refinement if:
        # 1. No arguments are missing OR the only missing arguments are NOT required, AND
        # 2. No extraneous arguments were provided (LLM didn't hallucinate extra args), AND
        # 3. Planner didn't flag this phase as needing refinement
        if not missing_required_args and not extraneous_canonical_args and not force_refinement:
            if missing_canonical_args:
                app_logger.debug(f"Argument check for tool '{tool_name}': Only optional arguments missing ({missing_canonical_args}). Skipping refinement.")
            else:
                app_logger.debug(f"Argument check for tool '{tool_name}': Arguments perfectly match schema. Skipping refinement.")

            if is_llm_task_in_loop and focused_data_payload is not None:
                action['arguments']['data'] = focused_data_payload
                app_logger.debug("Restored focused 'data' payload after skipping refinement.")
            return # Skip the rest of the function
        # --- MODIFICATION END ---


        app_logger.warning(
            f"Argument mismatch for tool '{tool_name}'. "
            f"Provided (canonically): {provided_canonical_arg_names}, "
            f"Tool requires (canonically): {tool_canonical_arg_names_required}, "
            f"Tool allows (canonically): {tool_canonical_arg_names_all}. "
            f"Initiating LLM-based refinement."
        )


        yield self.executor._format_sse_with_depth({
            "step": "System Correction",
            "type": "workaround",
            "details": {
                "summary": f"Detected an argument mismatch for tool '{tool_name}'. Agent is proactively correcting the arguments.",
                "correction_type": "argument_refinement"
            }
        })

        tool_schema_str = json.dumps({name: details for name, details in tool_def.args.items()}, indent=2)

        refinement_prompt = (
            "You are an expert argument mapper. Your task is to correct a potential tool call failure by re-mapping the provided arguments to the tool's official schema.\n\n"
            f"--- GOAL ---\n{phase.get('goal', self.executor.original_user_input)}\n\n"
            f"--- PROVIDED ARGUMENTS (May be incorrect or incomplete) ---\n{json.dumps(provided_args, indent=2)}\n\n" # Use original (potentially without 'data')
            f"--- CORRECT TOOL SCHEMA ---\n{tool_schema_str}\n\n"
            "--- INSTRUCTIONS ---\n"
            "1. Analyze the `GOAL` and the `PROVIDED ARGUMENTS` to understand the user's intent and what data is available.\n"
            "2. Examine the `CORRECT TOOL SCHEMA` to understand the exact argument names and structure the tool expects.\n"
            "3. Create a new, valid set of arguments by mapping the values from the `PROVIDED ARGUMENTS` to the correct names in the `CORRECT TOOL SCHEMA`.\n"
            "4. Use argument synonyms (e.g., 'table_name' vs 'object_name') intelligently if needed to bridge the gap.\n"
            "5. Discard any arguments from the provided call that do not correspond to anything in the correct schema.\n"
            "6. Ensure all *required* arguments from the schema are present, inferring values from the GOAL or PROVIDED ARGUMENTS if possible.\n"
            "7. Your response MUST be a single JSON object containing only the corrected arguments.\n\n"
            "Example:\n"
            "`{{\"sql\": \"SELECT * FROM ...\"}}`"
        )


        reason = f"Proactively refining arguments for '{tool_name}' to prevent tool failure."
        call_id = str(uuid.uuid4())
        yield self.executor._format_sse_with_depth({"step": "Calling LLM for Argument Refinement", "type": "system_message", "details": {"summary": reason, "call_id": call_id}})
        yield self.executor._format_sse_with_depth({"target": "llm", "state": "busy"}, "status_indicator_update")

        response_text, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
            prompt=refinement_prompt, reason=reason,
            system_prompt_override="You are a JSON-only responding assistant.",
            raise_on_error=True,
            source=self.executor.source
            # user_uuid implicitly passed
        )

        # --- Log refinement LLM call to execution trace for reload consistency ---
        refinement_log_event = {
            "step": "Calling LLM for Argument Refinement",
            "type": "system_message",
            "details": {
                "summary": reason,
                "call_id": call_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0)
            }
        }
        self.executor._log_system_event(refinement_log_event)

        yield self.executor._format_sse_with_depth({"target": "llm", "state": "idle"}, "status_indicator_update")
        # --- MODIFICATION START: Pass user_uuid to get_session ---
        updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
        # --- MODIFICATION END ---
        if updated_session:
            yield self.executor._format_sse_with_depth({ "statement_input": input_tokens, "statement_output": output_tokens, "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens, "total_input": updated_session.get("input_tokens", 0), "total_output": updated_session.get("output_tokens", 0), "call_id": call_id, "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0) }, "token_update")

        corrected_args = robust_json_parse(response_text)
        if isinstance(corrected_args, dict):
            if is_llm_task_in_loop and focused_data_payload is not None:
                corrected_args['data'] = focused_data_payload
                app_logger.info("Restored focused 'data' payload after argument refinement.")
            action['arguments'] = corrected_args
            app_logger.debug(f"Argument refinement successful. New args for '{tool_name}': {corrected_args}")
            yield self.executor._format_sse_with_depth({
                "step": "System Correction",
                "type": "workaround",
                "details": {
                    "summary": f"Arguments for '{tool_name}' proactively corrected.",
                    "correction_type": "argument_refinement_applied",
                    "new_arguments": corrected_args
                }
            })
        else:
            app_logger.warning(f"Argument refinement failed: could not parse JSON response. Original arguments will be used. Response: {response_text[:200]}")
            if is_llm_task_in_loop and focused_data_payload is not None:
                action['arguments']['data'] = focused_data_payload
                app_logger.debug("Restored focused 'data' payload after failed refinement.")


    async def _cross_source_runtime_split(self, action: dict, refusal: dict):
        """Stage 3b at run time: a statement refused as spanning two servers (the refusal carries
        ``split``: database → server) is fetched per server and combined by the compute component —
        the same split the planner makes, for a statement the planner did not see (a champion's,
        a tactical rewrite's). Returns None when the shape is declined (nothing ran), else
        ``(compute_action or None, combine_result, [(fetch_action, fetch_result), …], split)``.
        Never raises."""
        from trusted_data_agent.agent import cross_source as _cs
        from trusted_data_agent.agent import source_routing as _srt
        from trusted_data_agent.agent import result_sets as _rs
        from trusted_data_agent.agent import schema_facts as _sf
        from trusted_data_agent.core.tool_identity import parse as _parse_tool
        try:
            split_map = {str(k).lower(): str(v) for k, v in (refusal.get("split") or {}).items()}
            sql = (action.get("arguments") or {}).get("sql")
            if not isinstance(sql, str) or not split_map:
                return None
            session_id = self.executor.session_id
            columns = dict(_srt.turn_catalog_columns())
            for db, t in _sf.sql_tables(sql):
                known = _sf.known_columns(session_id, t)
                if known and db:
                    columns.setdefault(f"{db}.{t}", known)
            split = _cs.split_statement(sql, lambda d: split_map.get(str(d).lower()), columns)
            if split is None:
                return None
            servers = {str(s.server_id): s for s in _srt.turn_servers()}
            base = _parse_tool(action.get("tool_name")).name
            STATE = self.executor.dependencies["STATE"]
            records, numbers = [], []
            for f in split.fetches:
                srv = servers.get(f.server_id)
                if srv is None:
                    return None
                cmd = {"tool_name": srv.name_for(base), "arguments": {"sql": f.sql}}
                before = len(_rs.statements(session_id))
                res, _i, _o = await mcp_adapter.invoke_mcp_tool(
                    STATE, cmd, user_uuid=self.executor.user_uuid, session_id=session_id,
                    profile_id=self.executor.active_profile_id)
                records.append((cmd, res))
                sts = _rs.statements(session_id)
                if not isinstance(res, dict) or res.get("status") == "error" or len(sts) <= before:
                    return None, res, records, split
                numbers.append(f"s{sts[-1]['n']}")
            ccmd = {"tool_name": "TDA_Compute", "arguments": {"op": "sql", "sql": split.combine_sql(numbers)}}
            cres, _i, _o = await mcp_adapter.invoke_mcp_tool(
                STATE, ccmd, user_uuid=self.executor.user_uuid, session_id=session_id,
                profile_id=self.executor.active_profile_id)
            ok = (isinstance(cres, dict) and cres.get("status") != "error"
                  and not (cres.get("spec") or {}).get("error")
                  and not (cres.get("metadata") or {}).get("error_code"))
            return (ccmd if ok else None), cres, records, split
        except Exception as exc:  # noqa: BLE001
            app_logger.warning(f"[cross_source] runtime split skipped: {exc}")
            return None

    async def _execute_tool(self, action: dict, phase: dict, is_fast_path: bool = False):
        """Executes a single tool call with a built-in retry and recovery mechanism."""

        # §2 fix: a cooperative cancellation checkpoint at every tool call — Stop was previously
        # only checked BETWEEN phase groups, so a long phase (many tool retries / self-correction
        # LLM calls) couldn't be interrupted mid-execution. This raises CancelledError, which the
        # sequential path propagates and the parallel path re-raises (C9) — so the turn cancels
        # promptly instead of only after the current group finishes.
        self.executor._check_cancellation()

        is_multi_tool_phase = len(phase.get("relevant_tools", [])) > 1

        if not is_fast_path and not is_multi_tool_phase:
             async for event in self._proactively_refine_arguments(action, phase):
                 yield event
        elif is_multi_tool_phase:
             app_logger.debug(f"Skipping proactive argument refinement for tool '{action.get('tool_name')}' because it's part of a multi-tool phase.")


        tool_name = action.get("tool_name")
        arguments = action.get("arguments", {})
        
        # --- MODIFICATION START: Add phase number and execution depth to action for history ---
        phase_num = phase.get("phase", self.executor.current_phase_index + 1)
        action_for_history = copy.deepcopy(action)
        action_for_history.setdefault("metadata", {})["phase_number"] = phase_num
        action_for_history.setdefault("metadata", {})["execution_depth"] = self.executor.execution_depth
        action_for_history.setdefault("metadata", {})["timestamp"] = datetime.now(timezone.utc).isoformat()
        # --- MODIFICATION END ---


        if tool_name == "TDA_ContextReport" or (tool_name == "TDA_LLMTask" and "synthesized_answer" in arguments):
            if tool_name == "TDA_ContextReport":
                answer_key = "answer_from_context"
                log_message = f"Bypassing execution. Using context-based answer from planner via {tool_name}."
            else: # Legacy support
                answer_key = "synthesized_answer"
                log_message = "Bypassing TDA_LLMTask execution. Using synthesized answer from planner."

            app_logger.info(log_message)
            self.executor.is_synthesis_from_history = True
            
            # --- DEFENSIVE FIX: Handle missing answer_from_context ---
            answer_content = arguments.get(answer_key)
            if not answer_content:
                # LLM didn't provide the answer in arguments - generate a fallback message
                app_logger.warning(f"{tool_name} called without '{answer_key}' argument. Using fallback message.")
                answer_content = (
                    "I found relevant information in the knowledge base that may help answer your question. "
                    "However, the system needs to be configured to properly format the response. "
                    "Please try rephrasing your question or contact support if this issue persists."
                )
            
            self.executor.last_tool_output = {
                "status": "success",
                "metadata": {"tool_name": tool_name},
                "results": [{"response": answer_content}]
            }
            yield self.executor._format_sse_with_depth({"step": "Tool Execution Result", "details": self.executor.last_tool_output, "tool_name": tool_name}, "tool_result")
            # --- MODIFICATION START: Use action_for_history ---
            self.executor.turn_action_history.append({"action": action_for_history, "result": self.executor.last_tool_output})
            # --- MODIFICATION START: Add timestamp if missing (defensive) ---
            if isinstance(action_for_history, dict):
                action_for_history.setdefault("metadata", {})["timestamp"] = action_for_history.get("metadata", {}).get("timestamp") or datetime.now(timezone.utc).isoformat()
            # --- MODIFICATION END ---
            # --- MODIFICATION END ---
            phase_result_key = f"result_of_phase_{phase_num}"
            self.executor.workflow_state.setdefault(phase_result_key, []).append(self.executor.last_tool_output)
            self.executor._add_to_structured_data(self.executor.last_tool_output)
            return

        max_retries = 3

        if tool_name == "TDA_LLMTask" and self.executor.is_synthesis_from_history:
            app_logger.info("Preparing TDA_LLMTask for 'full_context' execution.")
            # --- MODIFICATION START: Pass user_uuid to get_session ---
            session_data = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
            # --- MODIFICATION END ---
            session_history = session_data.get("session_history", []) if session_data else []

            action.setdefault("arguments", {})["mode"] = "full_context"
            action.setdefault("arguments", {})["session_history"] = session_history
            action["arguments"]["user_question"] = self.executor.original_user_input

        # Set when a correction REPLACED a failed SQL call with a discovery call (describe a
        # table, list tables): that call's result is FACTS for the statement, never the phase's
        # answer. Once it succeeds, the original statement is corrected again with them.
        _sql_origin = None
        _standin_bonus = False     # a stand-in costs the attempt the retry needs: granted back once

        attempt = -1
        while True:
            attempt += 1
            if attempt >= max_retries:
                break
            if 'notification' in action:
                yield self.executor._format_sse_with_depth({"step": "System Notification", "details": action['notification'], "type": "workaround"})
                del action['notification']

            status_target = "db"
            call_id_for_tool = None

            if tool_name in ["TDA_LLMTask", "TDA_LLMFilter", "TDA_CurrentDate", "TDA_DateRange", "TDA_FinalReport", "TDA_ComplexPromptReport"]:
                status_target = "llm"
                call_id_for_tool = str(uuid.uuid4())

                # --- Report fast-track decision (single source of truth) ---
                # Decided HERE (not in the adapter) so the Live Status event reflects it; the
                # adapter honours the stamped verdict. Additive + fail-safe: only fires when the
                # Optimize profile opted in and the result is provably small.
                _ft_report = False
                if tool_name == "TDA_FinalReport":
                    try:
                        from trusted_data_agent.mcp_adapter.adapter import (
                            _resolve_fasttrack_report_cfg, _count_report_result_rows,
                        )
                        _ft_en, _ft_max = await _resolve_fasttrack_report_cfg(
                            self.executor.user_uuid, self.executor.session_id)
                        if _ft_en:
                            _ws_data = {k: v for k, v in self.executor.workflow_state.items()
                                        if not (isinstance(k, str) and k.startswith('_'))}
                            _rows = _count_report_result_rows(_ws_data)
                            _ft_report = _rows is not None and _rows <= _ft_max
                            if _ft_report:
                                action.setdefault("arguments", {})["_fast_track_report"] = True
                                action["arguments"]["_fast_track_rows"] = _rows
                    except Exception as _ft_e:
                        app_logger.debug(f"Report fast-track decision skipped: {_ft_e}")
                        _ft_report = False
                    # When every data query failed, the report is told so before it writes —
                    # the same rule the honesty gate applies afterwards (one definition).
                    try:
                        from trusted_data_agent.agent.honesty.primitives import data_failure_report_note
                        from trusted_data_agent.agent.obda import calls_from_trace
                        _dfn = data_failure_report_note(calls_from_trace(self.executor.turn_action_history))
                        if _dfn:
                            action.setdefault("arguments", {})["_data_failure_note"] = _dfn
                            _ft_report = False
                            action["arguments"].pop("_fast_track_report", None)
                    except Exception as _df_e:  # noqa: BLE001 — never block a report
                        app_logger.debug(f"Data-failure note skipped: {_df_e}")

                reason_map = {
                    "TDA_LLMTask": action.get("arguments", {}).get("task_description", "Executing LLM-based task."),
                    "TDA_LLMFilter": action.get("arguments", {}).get("goal", "Filtering data with LLM."),
                    "TDA_FinalReport": (
                        "Fast-Track synthesis — simple result, skipping the full report."
                        if _ft_report else "Synthesizing final user-facing report."
                    ),
                    "TDA_ComplexPromptReport": "Synthesizing final prompt-based report."
                }
                reason = reason_map.get(tool_name, f"Executing client-side tool: {tool_name}")
                report_step = "Fast-Track Final Report" if _ft_report else f"Calling LLM for {tool_name}"

                yield self.executor._format_sse_with_depth({
                    "step": report_step,
                    "type": "system_message",
                    "details": {"summary": reason, "call_id": call_id_for_tool, "fast_track": _ft_report}
                })

            yield self.executor._format_sse_with_depth({"target": status_target, "state": "busy"}, "status_indicator_update")

            # Emit tool execution intent (visible in Live Status during execution)
            yield self.executor._format_sse_with_depth({
                "step": "Tool Execution Intent",
                "type": "tool_intent",
                "details": action
            })

            full_context_for_tool = {
                "original_user_input": self.executor.original_user_input,
                "workflow_goal_prompt": self.executor.workflow_goal_prompt,
                **self.executor.workflow_state
            }

            # --- EPC: tool_call step ---
            try:
                if hasattr(self.executor, 'provenance') and self.executor.provenance:
                    import json as _json
                    _epc_args = action.get("arguments", {})
                    self.executor.provenance.add_step(
                        "tool_call",
                        f"{tool_name}:{_json.dumps(_epc_args, sort_keys=True, default=str)}",
                        f"Call: {tool_name}")
            except Exception:
                pass
            # --- EPC END ---

            # --- MODIFICATION START: Pass user_uuid and remove incorrect comment ---
            tool_result, input_tokens, output_tokens = await mcp_adapter.invoke_mcp_tool(
                self.executor.dependencies['STATE'],
                action,
                user_uuid=self.executor.user_uuid,
                session_id=self.executor.session_id,
                call_id=call_id_for_tool,
                workflow_state=full_context_for_tool,
                profile_id=self.executor.active_profile_id
            )
            # --- MODIFICATION END ---

            # Stage 3b — a statement over two servers: fetched per server and combined by code
            # (0 LLM rounds). If it cannot be split, NO one-server substitute is taken as the
            # phase's answer (the error branch ends the phase with the refusal instead).
            _span_unsplit = False
            if isinstance(tool_result, dict) and tool_result.get("code") == "spans_servers":
                from trusted_data_agent.agent import source_routing as _srt_cs
                _sp = await self._cross_source_runtime_split(action, tool_result)
                if _sp is None:
                    _span_unsplit = True
                else:
                    _ccmd, _cres, _recs, _split = _sp
                    for _c, _r in _recs:
                        _ah = copy.deepcopy(_c)
                        _ah.setdefault("metadata", {})["phase_number"] = phase_num
                        self.executor.turn_action_history.append({"action": _ah, "result": _r})
                    if _ccmd is not None:
                        from trusted_data_agent.agent import cross_source as _cs_ev
                        _ev = {"step": "System Self-Correction", "type": "workaround", "details": {
                            "summary": (f"The statement read databases on different MCP servers "
                                        f"({_cs_ev.describe(_split, _srt_cs.label_of)}); fetched each server's rows "
                                        f"with its own tool ({len(_recs)} fetch(es)) and combined them with the "
                                        f"statement's own logic by code — 0 LLM rounds."),
                            "correction_type": "cross_source_split",
                            "details": {"fetches": [f.sql for f in _split.fetches],
                                        "combine": _ccmd["arguments"]["sql"]}}}
                        self.executor._log_system_event(_ev)
                        yield self.executor._format_sse_with_depth(_ev)
                        action, tool_name, tool_result = _ccmd, "TDA_Compute", _cres
                        action_for_history = copy.deepcopy(action)
                        action_for_history.setdefault("metadata", {})["phase_number"] = phase_num
                    else:
                        _err = (_cres or {}).get("error") or ((_cres or {}).get("spec") or {}).get("error") \
                            or ((_cres or {}).get("metadata") or {}).get("error_code") or "the combine did not run"
                        tool_result["data"] = f"{tool_result.get('data') or ''}\nThe per-server fetches ran but the combine did not: {str(_err)[:400]}"
                        _span_unsplit = True

            # Check for report distillation metadata piggybacked by adapter
            if isinstance(tool_result, dict):
                distill_meta = tool_result.pop("_distillation_meta", None)
                if distill_meta:
                    event_data = {"step": "Context Optimization", "type": "context_optimization", "details": distill_meta}
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)

            # Check for component-piggybacked Live Status events (e.g. chart LLM mapping resolution)
            if isinstance(tool_result, dict):
                comp_events = tool_result.get("metadata", {}).pop("_component_llm_events", None)
                if comp_events:
                    for evt in comp_events:
                        self.executor._log_system_event(evt)
                        yield self.executor._format_sse_with_depth(evt)

            # --- Calculate cost for client-side LLM evaluation of MCP server feedback ---
            _tool_llm_cost = 0
            if input_tokens > 0 or output_tokens > 0:
                try:
                    from trusted_data_agent.core.cost_manager import get_cost_manager
                    _tool_llm_cost = get_cost_manager().calculate_cost(
                        provider=self.executor.current_provider or "Unknown",
                        model=self.executor.current_model or "Unknown",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens
                    )
                except Exception:
                    pass

            # --- Log client-side LLM call to execution trace for reload consistency ---
            if call_id_for_tool and (input_tokens > 0 or output_tokens > 0):
                tool_llm_log_event = {
                    "step": f"Calling LLM for {tool_name}",
                    "type": "system_message",
                    "details": {
                        "summary": reason,
                        "call_id": call_id_for_tool,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_usd": _tool_llm_cost
                    }
                }
                self.executor._log_system_event(tool_llm_log_event)

            # --- Inject cost into tool_result metadata for "Tool Execution Result" card ---
            if _tool_llm_cost > 0 and isinstance(tool_result, dict) and "metadata" in tool_result:
                tool_result["metadata"]["cost_usd"] = _tool_llm_cost

            # Emit tool execution result (visible in Live Status during execution)
            _result_event_type = "tool_error" if (isinstance(tool_result, dict) and tool_result.get("status") == "error") else "tool_result"
            yield self.executor._format_sse_with_depth({
                "step": "Tool Execution Result",
                "type": _result_event_type,
                "details": tool_result
            })

            # --- EPC: tool_result step ---
            try:
                if hasattr(self.executor, 'provenance') and self.executor.provenance:
                    _epc_result_str = str(tool_result)[:4096] if tool_result else ""
                    self.executor.provenance.add_step(
                        "tool_result",
                        f"{tool_name}:{_epc_result_str}",
                        f"Result: {tool_name} ({'error' if _result_event_type == 'tool_error' else 'ok'})")
            except Exception:
                pass
            # --- EPC END ---

            # Emit correction event if deterministic recovery was applied (key normalization or fallback)
            if isinstance(tool_result, dict) and tool_result.get("corrections"):
                optimization_type = tool_result.get("metadata", {}).get("optimization", "key_normalization")
                correction_event = {
                    "step": "System Auto-Correction",
                    "type": "auto_correction",
                    "details": {
                        "summary": f"Deterministic recovery applied ({optimization_type})",
                        "corrections": tool_result["corrections"],
                        "tool_name": tool_name
                    }
                }
                self.executor._log_system_event(correction_event)
                yield self.executor._format_sse_with_depth(correction_event)

            yield self.executor._format_sse_with_depth({"target": status_target, "state": "idle"}, "status_indicator_update")

            if input_tokens > 0 or output_tokens > 0:
                # Accumulate turn tokens for client-side tool LLM calls
                self.executor.turn_input_tokens += input_tokens
                self.executor.turn_output_tokens += output_tokens
                # …and the COST. This seam accumulated tokens but never fed the
                # turn-cost accumulator, so every tool-borne LLM call — the
                # TDA_FinalReport synthesis on every Optimize turn — was missing
                # from the persisted turn_cost while its token_update carried the
                # cost live: live sum and stored turn_cost disagreed on every
                # reload (found by an operator comparing the two screens).
                self.executor.turn_cost_usd += (_tool_llm_cost or 0.0)

                # --- MODIFICATION START: Pass user_uuid to get_session ---
                updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
                # --- MODIFICATION END ---
                if updated_session:
                    final_call_id = tool_result.get("metadata", {}).get("call_id") if isinstance(tool_result, dict) else None
                    yield self.executor._format_sse_with_depth({
                        "statement_input": input_tokens,
                        "statement_output": output_tokens,
                        "turn_input": self.executor.turn_input_tokens,
                        "turn_output": self.executor.turn_output_tokens,
                        "total_input": updated_session.get("input_tokens", 0),
                        "total_output": updated_session.get("output_tokens", 0),
                        "call_id": final_call_id,
                        "cost_usd": _tool_llm_cost
                    }, "token_update")

            self.executor.last_tool_output = tool_result
            
            # --- MODIFICATION START: Log action and result *inside* the loop ---
            # Log every attempt, whether success or failure, unless it's a fast-path call
            # (Fast-path loops log their own history)
            if not is_fast_path:
                self.executor.turn_action_history.append({"action": action_for_history, "result": self.executor.last_tool_output})
                if isinstance(action_for_history, dict):
                    action_for_history.setdefault("metadata", {})["timestamp"] = action_for_history.get("metadata", {}).get("timestamp") or datetime.now(timezone.utc).isoformat()
            # --- MODIFICATION END ---


            if isinstance(tool_result, dict) and tool_result.get("status") == "error":
                yield self.executor._format_sse_with_depth({"details": tool_result, "tool_name": tool_name}, "tool_error")

                error_data_str = str(tool_result.get('data', ''))
                error_summary = str(tool_result.get('error_message', ''))

                for error_pattern, friendly_message in DEFINITIVE_TOOL_ERRORS.items():
                    if re.search(error_pattern, error_data_str, re.IGNORECASE) or re.search(error_pattern, error_summary, re.IGNORECASE):
                        from trusted_data_agent.agent.executor import DefinitiveToolError
                        raise DefinitiveToolError(error_summary or error_data_str, friendly_message)


                if _span_unsplit:
                    _ev = {"step": "System Self-Correction", "type": "workaround", "details": {
                        "summary": ("The statement reads databases on different MCP servers and could not be "
                                    "split by code; no one-server substitute is taken as its answer — the phase "
                                    "ends with this refusal (0 LLM rounds)."),
                        "correction_type": "cross_source_unsplit"}}
                    self.executor._log_system_event(_ev)
                    yield self.executor._format_sse_with_depth(_ev)
                    break
                if attempt < max_retries - 1:
                    correction_details = {
                        "summary": f"Tool failed. Attempting self-correction ({attempt + 1}/{max_retries - 1}).",
                        "details": tool_result
                    }
                    event_data = {"step": "System Self-Correction", "type": "workaround", "details": correction_details}
                    self.executor._log_system_event(event_data)
                    yield self.executor._format_sse_with_depth(event_data)

                    corrected_action, correction_events = await self._attempt_tool_self_correction(
                        action, tool_result, phase_num=phase_num)

                    # --- EPC: self_correction step ---
                    try:
                        if hasattr(self.executor, 'provenance') and self.executor.provenance:
                            _epc_err_str = str(tool_result)[:500] if tool_result else ""
                            _epc_corr_str = str(corrected_action)[:500] if corrected_action else "none"
                            self.executor.provenance.add_step(
                                "self_correction",
                                f"{attempt}:{_epc_err_str}:{_epc_corr_str}",
                                f"Self-correction attempt {attempt + 1} for {tool_name}")
                    except Exception:
                        pass
                    # --- EPC END ---

                    for event_data, event_name in correction_events:
                        self.executor._log_system_event(event_data, event_name=event_name)
                        yield self.executor._format_sse_with_depth(event_data, event=event_name)

                    if corrected_action:
                        if "FINAL_ANSWER" in corrected_action:
                            final_answer_from_correction = corrected_action["FINAL_ANSWER"]
                            self.executor.last_tool_output = {"status": "success", "results": [{"response": f"FINAL_ANSWER: {final_answer_from_correction}"}]}
                            break # Correction led to final answer, break retry loop

                        if "prompt_name" in corrected_action:
                            async for event in self.executor._run_sub_prompt(
                                corrected_action['prompt_name'],
                                corrected_action.get("arguments", {}),
                                is_delegated_task=True
                            ):
                                yield event

                            if self.executor.state == self.executor.AgentState.ERROR:
                                app_logger.error(f"Recovery prompt '{corrected_action['prompt_name']}' failed. Continuing retry loop.")
                                self.executor.last_tool_output = {"status": "error", "data": "The recovery prompt failed to execute."}
                                continue # Prompt failed, continue retry loop for original tool
                            else:
                                app_logger.info(f"Successfully recovered from tool failure by executing prompt '{corrected_action['prompt_name']}'.")
                                break # Prompt succeeded, break retry loop

                        if _sf.is_discovery_standin(action, corrected_action):
                            _sql_origin = (copy.deepcopy(action), tool_result)
                            if not _standin_bonus:
                                _standin_bonus = True
                                max_retries += 1
                        action = corrected_action
                        # --- MODIFICATION START: Update action_for_history for the next attempt ---
                        action_for_history = copy.deepcopy(action)
                        action_for_history.setdefault("metadata", {})["phase_number"] = phase_num
                        # --- MODIFICATION END ---
                        continue
                    else:
                        correction_failed_details = {
                            "summary": "Unable to find a correction. Aborting retries for this action.",
                            "details": tool_result
                        }
                        yield self.executor._format_sse_with_depth({"step": "System Self-Correction Failed", "type": "error", "details": correction_failed_details})
                        break
                else:
                    persistent_failure_details = {
                        "summary": f"Tool '{tool_name}' failed after {max_retries} attempts.",
                        "details": tool_result
                    }
                    yield self.executor._format_sse_with_depth({"step": "Persistent Failure", "type": "error", "details": persistent_failure_details})
            else:
                if _sql_origin is not None and attempt < max_retries - 1:
                    _orig_action, _orig_error = _sql_origin
                    _sql_origin = None
                    _sf.record_discovery(self.executor.session_id, action, tool_result)
                    _aug = dict(_orig_error) if isinstance(_orig_error, dict) else {"status": "error", "data": str(_orig_error)}
                    _facts = _sf.discovery_facts_text(action, tool_result)
                    if _facts:
                        _aug["data"] = f"{_aug.get('data', '')}\n\n{_sf.FACTS_HEADER}\n{_facts}\n{_sf.FACTS_DIRECTIVE}"
                    _ev = {"step": "System Self-Correction", "type": "workaround", "details": {
                        "summary": (f"'{action.get('tool_name')}' answered the schema question; retrying the "
                                    f"original statement with the real names instead of ending the phase."),
                        "correction_type": "schema_facts_retry"}}
                    self.executor._log_system_event(_ev)
                    yield self.executor._format_sse_with_depth(_ev)
                    _retry, _retry_events = await self._attempt_tool_self_correction(
                        _orig_action, _aug, phase_num=phase_num)
                    for event_data, event_name in _retry_events:
                        self.executor._log_system_event(event_data, event_name=event_name)
                        yield self.executor._format_sse_with_depth(event_data, event=event_name)
                    if (isinstance(_retry, dict) and _retry.get("tool_name") and "arguments" in _retry
                            and not _sf.is_discovery_tool(_retry.get("tool_name"))):
                        action = _retry
                        action_for_history = copy.deepcopy(action)
                        action_for_history.setdefault("metadata", {})["phase_number"] = phase_num
                        continue
                    if isinstance(_retry, dict) and "FINAL_ANSWER" in _retry:
                        self.executor.last_tool_output = {"status": "success", "results": [
                            {"response": f"FINAL_ANSWER: {_retry['FINAL_ANSWER']}"}]}
                break
        
        # --- MODIFICATION START: Move logging out of the loop ---
        # The logging is now done *inside* the loop for all non-fast-path attempts.
        # This section is no longer needed here.
        # --- MODIFICATION END ---
        
        # --- MODIFICATION START: This logic is now outside the loop ---
        # Add to workflow state and structured data *after* the loop finishes
        # (whether it succeeded or ended in error)
        if not is_fast_path:
            phase_result_key = f"result_of_phase_{phase_num}"
            if not isinstance(self.executor.workflow_state.get(phase_result_key), list):
                # a recovery that switched to a delegated prompt stores its result as a dict
                # here; `dict in dict` then raised `unhashable type` and killed the turn
                # (PROP-011 live check, 2026-11-02) — the slot is a LIST of outputs
                _prev = self.executor.workflow_state.get(phase_result_key)
                self.executor.workflow_state[phase_result_key] = [] if _prev is None else [_prev]
            if self.executor.last_tool_output not in self.executor.workflow_state[phase_result_key]:
                self.executor.workflow_state[phase_result_key].append(self.executor.last_tool_output)
            self.executor._add_to_structured_data(self.executor.last_tool_output)
        # --- MODIFICATION END ---


    def _enrich_arguments_from_history(self, relevant_tools: list[str], current_args: dict = None) -> tuple[dict, list, bool]:
        """
        Scans the current turn's action history to find missing arguments for a tool call.
        It now only uses arguments from tool calls that were definitively successful.
        """
        events_to_yield = []
        initial_args = current_args.copy() if current_args else {}
        enriched_args = initial_args.copy()

        all_tools = self.executor.dependencies['STATE'].get('mcp_tools', {})
        required_args_for_phase = set()
        for tool_name in relevant_tools:
            tool = all_tools.get(tool_name)
            if not tool: continue
            args_dict = tool.args if isinstance(tool.args, dict) else {}
            for arg_name, arg_details in args_dict.items():
                if arg_details.get('required', False):
                    required_args_for_phase.add(arg_name)

        args_to_find = {
            arg for arg in required_args_for_phase
            if get_argument_by_canonical_name(enriched_args, arg) is None
        }

        if not args_to_find:
            return enriched_args, [], False

        for entry in reversed(self.executor.turn_action_history):
            if not args_to_find: break

            result = entry.get("result", {})
            is_successful_data_action = (
                isinstance(result, dict) and
                result.get('status') == 'success' and
                'results' in result
            )
            is_successful_chart_action = (
                isinstance(result, dict) and
                result.get('type') == 'chart' and
                'spec' in result
            )

            if not (is_successful_data_action or is_successful_chart_action):
                continue

            action_args = entry.get("action", {}).get("arguments", {})
            for arg_name in list(args_to_find):
                value_from_action = get_argument_by_canonical_name(action_args, arg_name)
                if value_from_action is not None:
                    enriched_args[arg_name] = value_from_action
                    args_to_find.remove(arg_name)


            if isinstance(result, dict):
                result_metadata = result.get("metadata", {})
                if result_metadata:
                    metadata_to_arg_map = {
                        "database": "database_name",
                        "table": "table_name",
                        "column": "column_name"
                    }
                    for meta_key, arg_name in metadata_to_arg_map.items():
                        if arg_name in args_to_find and meta_key in result_metadata:
                             if get_argument_by_canonical_name(enriched_args, arg_name) is None:
                                enriched_args[arg_name] = result_metadata[meta_key]
                                args_to_find.remove(arg_name)


        was_enriched = enriched_args != initial_args
        if was_enriched:
            for arg_name, value in enriched_args.items():
                if arg_name not in initial_args or initial_args.get(arg_name) is None:
                    app_logger.info(f"Proactively inferred '{arg_name}' from turn history: '{value}'")
                    events_to_yield.append(self.executor._format_sse_with_depth({
                        "step": "System Correction",
                        "details": f"System inferred '{arg_name}: {value}' from the current turn's actions.",
                        "type": "workaround",
                        "correction_type": "inferred_argument"
                    }))

        return enriched_args, events_to_yield, was_enriched


    async def _get_next_tactical_action(self, current_phase_goal: str, relevant_tools: list[str], enriched_args: dict, strategic_args: dict, executable_prompt: str = None, phase_num: int = None) -> tuple[dict | str, int, int]:
        """Makes a tactical LLM call to decide the single next best action for the current phase."""

        permitted_tools_with_details = ""
        all_tools = self.executor.dependencies['STATE'].get('mcp_tools', {})

        for tool_name in relevant_tools:
            tool = all_tools.get(tool_name)
            if not tool: continue

            tool_str = f"\n- Tool: `{tool.name}`\n  - Description: {tool.description}"
            args_dict = tool.args if isinstance(tool.args, dict) else {}

            if args_dict:
                tool_str += "\n  - Arguments:"
                for arg_name, arg_details in args_dict.items():
                    is_required = arg_details.get('required', False)
                    arg_type = arg_details.get('type', 'any')
                    req_str = "required" if is_required else "optional"
                    arg_desc = arg_details.get('description', 'No description.')
                    tool_str += f"\n    - `{arg_name}` ({arg_type}, {req_str}): {arg_desc}"
            permitted_tools_with_details += tool_str + "\n"

        permitted_prompts_with_details = "None"
        if executable_prompt:
            all_prompts = self.executor.dependencies['STATE'].get('structured_prompts', {})
            prompt_info = None
            for category, prompts in all_prompts.items():
                for p in prompts:
                    if p['name'] == executable_prompt:
                        prompt_info = p
                        break
                if prompt_info: break

            if prompt_info:
                prompt_str = f"\n- Prompt: `{prompt_info['name']}`\n  - Description: {prompt_info.get('description', 'No description.')}"
                if prompt_info.get('arguments'):
                    prompt_str += "\n  - Arguments:"
                    for arg in prompt_info['arguments']:
                        req_str = "required" if arg.get('required') else "optional"
                        prompt_str += f"\n    - `{arg['name']}` ({arg.get('type', 'any')}, {req_str}): {arg.get('description', 'No description.')}"
                permitted_prompts_with_details = prompt_str + "\n"


        context_enrichment_section = ""
        if enriched_args:
            context_items = [f"- `{name}`: `{value}`" for name, value in enriched_args.items()]
            context_enrichment_section = (
                "\n--- CONTEXT FROM HISTORY ---\n"
                "The following critical information has been inferred from the conversation history. You MUST use it to fill in missing arguments.\n"
                + "\n".join(context_items) + "\n"
            )

        loop_context_section = ""
        if self.executor.is_in_loop:
            next_item = next((item for item in self.executor.current_loop_items if item not in self.executor.processed_loop_items), None)
            if next_item:
                loop_context_section = (
                    f"\n--- LOOP CONTEXT ---\n"
                    f"- You are currently in a loop to process multiple items.\n"
                    f"- All Items in Loop: {json.dumps(self.executor.current_loop_items)}\n"
                    f"- Items Already Processed: {json.dumps(self.executor.processed_loop_items)}\n"
                    f"- Your task is to process this single item next: {json.dumps(next_item)}\n"
                )

        strategic_arguments_section = "None provided."
        if strategic_args:
            strategic_arguments_section = json.dumps(strategic_args, indent=2)

        # Conditional tactical rules: components that need to steer the per-phase
        # tactical LLM declare a manifest `tactical_prompt_rule` (e.g. the chart
        # component's mapping rule). It is injected into WORKFLOW_TACTICAL_PROMPT's
        # {chart_mapping_rule_section} ONLY when the component is enabled for the
        # active profile — so a disabled chart leaves no charting guidance behind.
        chart_mapping_rule_section = ""
        try:
            from trusted_data_agent.components.manager import get_component_manager
            _comp_mgr = get_component_manager()
            if _comp_mgr:
                chart_mapping_rule_section = _comp_mgr.get_tactical_prompt_rules(
                    self.executor._get_profile_config(),
                    getattr(self.executor, "user_uuid", None),
                )
        except Exception as _tr_err:
            app_logger.warning(f"Tactical prompt rule assembly failed: {_tr_err}")

        distill_events = []
        # §4 perf: _distill_data_for_llm_context (and the delegated ExecutionContextDistiller) are
        # purely NON-MUTATING — they read the input and return a fresh distilled tree — so the former
        # per-phase copy.deepcopy() of the full workflow_state + turn history was pure waste.
        distilled_workflow_state = self.executor._distill_data_for_llm_context(self.executor.workflow_state, _events=distill_events)
        distilled_turn_history = self.executor._distill_data_for_llm_context(self.executor.turn_action_history, _events=distill_events)
        # Store distillation events for caller to emit via SSE
        self._pending_distill_events = distill_events

        # Resolve per-phase correction history (parallel execution safety).
        _phase_ch = (self.executor._get_phase_state(phase_num)['correction_history']
                     if phase_num is not None else self.executor.correction_history)

        # === ContextBuilder integration (Phase 3a) ===
        _builder = getattr(self.executor, 'context_builder', None)
        if _builder and _builder.has_assembled_context:
            try:
                prompt_ctx = await _builder.build("tactical", {
                    "workflow_goal": self.executor.workflow_goal_prompt,
                    "current_phase_goal": current_phase_goal,
                    "strategic_arguments_section": strategic_arguments_section,
                    "correction_history_section": self._build_correction_history_section(_phase_ch),
                    "loop_context_section": loop_context_section,
                    "context_enrichment_section": context_enrichment_section,
                    "permitted_prompts_with_details": permitted_prompts_with_details,
                    "chart_mapping_rule_section": chart_mapping_rule_section,
                    "phase_tools": relevant_tools,
                })
                _tv = prompt_ctx.template_vars
                # Override distilled data with manually distilled values
                # (distillation events must be captured for SSE observability)
                _tv["all_collected_data"] = json.dumps(distilled_workflow_state, indent=2)
                _tv["turn_action_history"] = json.dumps(distilled_turn_history, indent=2)
                # Log per-call snapshot and store for parent generator to emit as SSE
                if prompt_ctx.snapshot:
                    app_logger.info(
                        f"Tactical planning using ContextBuilder "
                        f"(tokens: {prompt_ctx.tokens_used:,}, source: {prompt_ctx.source})"
                    )
                    # scope marks a per-call subset (not the turn's full assembly),
                    # so the resource-panel badge never reads it as the turn's utilization.
                    self._pending_tactical_snapshot = {**prompt_ctx.snapshot.to_sse_event(), "scope": "tactical"}
            except Exception as _cb_err:
                app_logger.warning(f"ContextBuilder tactical build failed, using legacy: {_cb_err}")
                _tv = None
        else:
            _tv = None

        if _tv is None:
            _tv = {
                "workflow_goal": self.executor.workflow_goal_prompt,
                "current_phase_goal": current_phase_goal,
                "strategic_arguments_section": strategic_arguments_section,
                "permitted_tools_with_details": permitted_tools_with_details,
                "permitted_prompts_with_details": permitted_prompts_with_details,
                "correction_history_section": self._build_correction_history_section(_phase_ch),
                "turn_action_history": json.dumps(distilled_turn_history, indent=2),
                "all_collected_data": json.dumps(distilled_workflow_state, indent=2),
                "loop_context_section": loop_context_section,
                "context_enrichment_section": context_enrichment_section,
                "chart_mapping_rule_section": chart_mapping_rule_section,
            }

        # Defense-in-depth: WORKFLOW_TACTICAL_PROMPT.format() KeyErrors on any
        # unsupplied placeholder. Guarantee the conditional section is always
        # present regardless of which build path produced _tv.
        _tv.setdefault("chart_mapping_rule_section", chart_mapping_rule_section)

        tactical_system_prompt = WORKFLOW_TACTICAL_PROMPT.format(**_tv)

        # Use tactical model for per-phase execution (dual-model feature)
        tactical_provider = self.executor.tactical_provider
        tactical_model = self.executor.tactical_model

        # S10 — a steer sent while the plan ran is read with this tactical decision (the
        # Optimize lane's step boundary); nothing waits ⇒ the fixed prompt as before.
        _tactical_prompt = "Determine the next action based on the instructions and state provided in the system prompt."
        try:
            from trusted_data_agent.core import steer as _steer
            _snote = _steer.consume_at_boundary(self.executor.session_id, f"phase {phase_num}" if phase_num is not None else "tactical decision", self.executor.user_uuid)
            if _snote:
                _tactical_prompt = f"{_tactical_prompt}\n\n{_snote}"
        except Exception:  # noqa: BLE001
            pass
        response_text, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
            prompt=_tactical_prompt,
            reason=f"Deciding next tactical action for phase: {current_phase_goal}",
            system_prompt_override=tactical_system_prompt,
            disabled_history=True,
            source=self.executor.source,
            current_provider=tactical_provider,  # NEW: Tactical model override
            current_model=tactical_model,        # NEW: Tactical model override
            planning_phase="tactical"            # NEW: Identify as tactical planning call
            # user_uuid implicitly passed
        )

        # Log dual-model usage
        if self.executor.is_dual_model_active:
            phase_num = getattr(self.executor, 'current_phase_index', '?')
            app_logger.info(f"[Tactical Execution] Phase {phase_num}: {tactical_provider}/{tactical_model}")

        if "FINAL_ANSWER:" in response_text.upper() or "SYSTEM_ACTION_COMPLETE" in response_text.upper():
            return "SYSTEM_ACTION_COMPLETE", input_tokens, output_tokens

        raw_action = robust_json_parse(response_text)

        if raw_action is None:
            # Inject the parse failure into the system prompt and retry once.
            app_logger.warning("Tactical action JSON parse failed. Retrying with error context.")
            retry_system_prompt = tactical_system_prompt + (
                "\n\nIMPORTANT: Your previous response could not be parsed as valid JSON.\n"
                f"Partial response received:\n---\n{response_text[:400]}\n---\n"
                "You MUST respond with ONLY a single valid JSON object for the tool or prompt call."
            )
            response_text, _, _ = await self.executor._call_llm_and_update_tokens(
                prompt="Determine the next action based on the instructions and state provided in the system prompt.",
                reason=f"Deciding tactical action (parse-error retry)",
                system_prompt_override=retry_system_prompt,
                disabled_history=True,
                source=self.executor.source,
                current_provider=tactical_provider,
                current_model=tactical_model,
                planning_phase="tactical"
            )
            raw_action = robust_json_parse(response_text)

        if raw_action is None:
            raise RuntimeError(
                f"Failed to get a valid JSON action from the tactical LLM after 2 attempts. "
                f"Last response: {response_text[:200]}"
            )

        if isinstance(raw_action, list) and raw_action and isinstance(raw_action[0], dict):
            if len(raw_action) > 1:
                app_logger.warning(
                    f"Tactical LLM returned {len(raw_action)} actions in a list; "
                    "using the first only (contract is one action per phase)."
                )
            else:
                app_logger.info("Tactical LLM wrapped single action in a list; unwrapping.")
            raw_action = raw_action[0]

        if not isinstance(raw_action, dict):
            raise RuntimeError(
                f"Tactical LLM returned a non-object JSON value. "
                f"Expected a dict, got {type(raw_action).__name__}. Response: {response_text[:200]}"
            )

        action_details = raw_action
        tool_name_synonyms = ["tool_name", "name", "tool", "action_name"]
        prompt_name_synonyms = ["prompt_name", "prompt"]
        arg_synonyms = ["arguments", "args", "tool_input", "action_input", "parameters"]

        possible_wrapper_keys = ["action", "tool_call", "tool", "prompt_call", "prompt"]
        for key in possible_wrapper_keys:
            if key in action_details and isinstance(action_details[key], dict):
                action_details = action_details[key]
                break

        found_tool_name = None
        for key in tool_name_synonyms:
            if key in action_details:
                found_tool_name = action_details.pop(key)
                break

        found_prompt_name = None
        for key in prompt_name_synonyms:
            if key in action_details:
                found_prompt_name = action_details.pop(key)
                break

        found_args = None
        for key in arg_synonyms:
            if key in action_details and isinstance(action_details[key], dict):
                found_args = action_details[key]
                break

        if found_args is None:
            if isinstance(action_details, dict) and not any(k in action_details for k in tool_name_synonyms + prompt_name_synonyms):
                found_args = action_details

        normalized_action = {
            "tool_name": found_tool_name,
            "prompt_name": found_prompt_name,
            "arguments": found_args if isinstance(found_args, dict) else {}
        }

        if not normalized_action.get("tool_name") and not normalized_action.get("prompt_name"):
            if len(relevant_tools) == 1:
                normalized_action["tool_name"] = relevant_tools[0]
                self.executor.events_to_yield.append(self.executor._format_sse_with_depth({
                    "step": "System Correction",
                    "type": "workaround",
                    "correction_type": "inferred_tool_name",
                    "details": f"LLM omitted tool_name. System inferred '{relevant_tools[0]}'."
                }))
            elif executable_prompt:
                normalized_action["prompt_name"] = executable_prompt
                self.executor.events_to_yield.append(self.executor._format_sse_with_depth({
                    "step": "System Correction",
                    "type": "workaround",
                    "correction_type": "inferred_prompt_name",
                    "details": f"LLM omitted prompt_name. System inferred '{executable_prompt}'."
                }))

        # A tool name the catalog does not have, that means exactly one it has (a case, a word
        # order, a server prefix the first server's tools do not carry): re-keyed here, 0 LLM —
        # sent as written it can only come back "Unknown tool" and cost a correction round.
        _tn = normalized_action.get("tool_name")
        if isinstance(_tn, str) and _tn and not _tn.startswith("TDA_"):
            _known = list((self.executor.dependencies.get("STATE") or {}).get("mcp_tools") or {})
            if _tn not in _known and not _is_connector_tool(_tn):
                _rk = _resolve_unknown_tool(_tn, _known)
                if _rk:
                    normalized_action["tool_name"] = _rk
                    self.executor.events_to_yield.append(self.executor._format_sse_with_depth({
                        "step": "System Correction", "type": "workaround",
                        "correction_type": "tool_name_rekeyed",
                        "details": f"The tactical step named '{_tn}', which no server of this agent offers; "
                                   f"re-keyed to '{_rk}' (0 LLM)."}))

        if not normalized_action.get("tool_name") and not normalized_action.get("prompt_name"):
            raise RuntimeError(
                f"Could not determine tool_name or prompt_name from tactical LLM response. "
                f"Response: {response_text[:200]}"
            )

        return normalized_action, input_tokens, output_tokens

    def _is_date_query_candidate(self, command: dict) -> tuple[bool, str, bool]:
        """Checks if a command is a candidate for the date-range orchestrator."""
        tool_name = command.get("tool_name")
        tool_spec = self.executor.dependencies['STATE'].get('mcp_tools', {}).get(tool_name)
        if not tool_spec or not hasattr(tool_spec, 'args') or not isinstance(tool_spec.args, dict):
            return False, None, False

        tool_arg_names = set(tool_spec.args.keys())
        tool_supports_range = 'start_date' in tool_arg_names and 'end_date' in tool_arg_names

        # First check action arguments for date params
        args = command.get("arguments", {})
        date_param_name = next((param for param in args if 'date' in param.lower()), None)

        # If action args have no date params but tool spec does, still mark as candidate.
        # This handles the case where placeholder resolution failed and date args were omitted.
        if not date_param_name and tool_supports_range:
            date_param_name = 'start_date'  # Use canonical name for range tools

        return bool(date_param_name), date_param_name, tool_supports_range

    async def _classify_date_query_type(self):
        """Uses LLM to classify a date query as 'single' or 'range'."""
        classification_prompt = (
            f"You are a query classifier. Analyze the following query: '{self.executor.original_user_input}'. "
            "Determine if it refers to a 'single' date or a 'range' of dates. "
            "Extract the specific phrase that describes the date or range. "
            "Your response MUST be ONLY a JSON object with two keys: 'type' and 'phrase'."
        )
        reason="Classifying date query."
        call_id = str(uuid.uuid4())
        yield self.executor._format_sse_with_depth({"step": "Calling LLM", "details": {"summary": reason, "call_id": call_id}})
        response_str, input_tokens, output_tokens = await self.executor._call_llm_and_update_tokens(
            prompt=classification_prompt, reason=reason,
            system_prompt_override="You are a JSON-only responding assistant.", raise_on_error=True,
            source=self.executor.source
            # user_uuid implicitly passed
        )
        # Log date classification LLM call with tokens + cost for history
        date_class_log_event = {
            "step": "Calling LLM",
            "type": "system_message",
            "details": {
                "summary": reason,
                "call_id": call_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0)
            }
        }
        self.executor._log_system_event(date_class_log_event)

        # --- MODIFICATION START: Pass user_uuid to get_session ---
        updated_session = await session_manager.get_session(self.executor.user_uuid, self.executor.session_id)
        # --- MODIFICATION END ---
        if updated_session:
            yield self.executor._format_sse_with_depth({ "statement_input": input_tokens, "statement_output": output_tokens, "turn_input": self.executor.turn_input_tokens, "turn_output": self.executor.turn_output_tokens, "total_input": updated_session.get("input_tokens", 0), "total_output": updated_session.get("output_tokens", 0), "call_id": call_id, "cost_usd": self.executor._last_call_metadata.get("cost_usd", 0) }, "token_update")
        parsed = robust_json_parse(response_str)
        self.executor.temp_data_holder = parsed if isinstance(parsed, dict) else {'type': 'single', 'phrase': self.executor.original_user_input}


    # C2 (removed, user decision): the dead `_recover_from_phase_failure` LLM replan was
    # deleted here. It was orphaned by the Apr-2026 parallel phase-groups refactor — it set
    # meta_plan/current_phase_index but _run_plan iterates a phase_groups snapshot, so the
    # recovery plan never executed. Phase-exhaustion is now handled honestly inline in
    # _execute_standard_phase (records an error result + surfaces it; the turn continues).

    async def _attempt_tool_self_correction(self, failed_action: dict, error_result: dict, phase_num: int = None) -> tuple[dict | None, list]:
        """
        Delegates the correction task to the CorrectionHandler, which uses the
        Strategy Pattern to find and execute the appropriate recovery logic.

        phase_num is forwarded so CorrectionHandler appends to the per-phase
        correction_history rather than the shared executor attribute.
        """
        phase_state = self.executor._get_phase_state(phase_num) if phase_num is not None else None
        correction_handler = CorrectionHandler(self.executor, phase_state=phase_state)
        return await correction_handler.attempt_correction(failed_action, error_result)
