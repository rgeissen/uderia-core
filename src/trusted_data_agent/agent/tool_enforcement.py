"""Shared tool-execution enforcement — agent-authority + preflight SQL grounding.

ONE implementation used by BOTH data-tool execution paths so the two invariants the docs
promise ("fail-closed at every tool chokepoint", "preflight is universal across every
SQL-running engine") actually hold on every engine:

  * the MCP adapter (``invoke_mcp_tool``) — Optimize / genie-slave / FASTPATH path
  * the LangChain MCP tool wrapper (``wrap_langchain_mcp_tools``) — Conversation / ReAct path

Framework ``TDA_*`` tools are exempt from authority (they are the agent's own reporting /
orchestration) and from preflight (they carry no raw SQL). Both checks fail **open** on any
internal error (never break a turn) except the authority decision itself, which the underlying
``agent_authority.enforce_tool`` deliberately fails **closed**.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from trusted_data_agent.agent import turn_budget as _turn_budget   # S5

app_logger = logging.getLogger("quart.app")


def _is_framework_tool(tool_name: Optional[str]) -> bool:
    return not tool_name or str(tool_name).startswith("TDA_")


def classify_sql_write(tool_name: str, arguments: Optional[Dict[str, Any]]) -> Optional[bool]:
    """``True`` if the call carries a ``sql`` argument that MUTATES, ``None`` otherwise
    (no SQL, or a read) — so the caller can hand a real ``is_write`` to the authority
    decision. This closes the ``deny_writes`` name-heuristic hole: a write smuggled through
    a read-named tool (``base_readQuery(sql="DELETE …")``) is classified by the statement,
    not the tool name. ``TDA_*`` exempt. Never raises."""
    if _is_framework_tool(tool_name):
        return None
    try:
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return None
        from trusted_data_agent.agent.preflight.catalog import sql_is_write
        return True if sql_is_write(sql) else None
    except Exception:
        return None


def phantom_source_block(tool_name: str,
                         arguments: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A statement that still reads FROM a plan step's output (``TDA_DateRange_result``,
    ``result_of_phase_2``) or a ``?`` bind is refused before it reaches a database — no
    database holds such an object. The executor rewrites the shapes it recognises into the
    turn's dates first (``phantom_date_source.rewrite``); this names every other one, with the
    recovery, so a model that invents a new form is corrected at 0 round trips. Retryable (the
    model CAN write the literals). ``TDA_*`` exempt. Never raises."""
    if _is_framework_tool(tool_name):
        return None
    try:
        from trusted_data_agent.agent import phantom_date_source as _pds
        for sk in ("sql", "query", "query_request"):
            v = (arguments or {}).get(sk)
            if isinstance(v, str):
                msg = _pds.refusal_message(v)
                if msg:
                    app_logger.warning(f"tool_enforcement: refusing '{tool_name}' — {msg}")
                    return {"status": "error", "error": msg, "data": msg,
                            "error_type": "phantom_source"}
    except Exception:  # noqa: BLE001 — a shape guard must never break a call
        return None
    return None


def nonstring_sql_block(tool_name: str,
                        arguments: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A SQL argument that is not a STRING is unrunnable by construction — refuse it
    before any round trip, definitively.

    Observed live (Optimize path): a phase referenced an upstream step that produced
    no SQL, the resolver fell back to the whole component payload, and the payload's
    repr shipped to the warehouse as the statement — a wasted round trip, an LLM
    correction round, and a final report that fabricated a number from the noise.
    The marker phrase is in DEFINITIVE_TOOL_ERRORS, so no correction rounds fire:
    retrying cannot change what the upstream step produced.

    ONE implementation for BOTH chokepoints (the module's charter): the MCP adapter
    and the ReAct wrapper — the first version lived inline in the adapter alone, and
    the cross-class audit found the ReAct path uncovered. ``TDA_*`` exempt. Never
    raises."""
    if _is_framework_tool(tool_name):
        return None
    try:
        args = arguments or {}
        for sk in ("sql", "query", "query_request"):
            if sk in args and args[sk] is not None and not isinstance(args[sk], str):
                msg = (f"argument '{sk}' is not SQL text (got "
                       f"{type(args[sk]).__name__}) — the upstream reference "
                       f"produced no SQL. This is not retryable: reference a phase "
                       f"whose result carries a 'sql' field (e.g. a query_product "
                       f"compile), or write the statement explicitly.")
                app_logger.warning(f"tool_enforcement: refusing '{tool_name}' — {msg}")
                return {"status": "error", "error": msg, "data": msg}
    except Exception:  # noqa: BLE001 — a shape guard must never break a call
        return None
    return None


def check_authority(tool_name: str, *, is_connector: bool = False,
                    is_write: Optional[bool] = None,
                    arguments: Optional[Dict[str, Any]] = None,
                    aliases: Optional[frozenset] = None) -> Tuple[bool, str]:
    """Fail-closed agent-authority decision against the ambient principal. ``TDA_*`` exempt.
    When ``is_write`` is not given and the call carries a mutating ``sql`` argument, the
    statement is classified so ``deny_writes`` sees the write regardless of the tool name."""
    if _is_framework_tool(tool_name):
        return True, "framework tool (exempt)"
    if is_write is None:
        is_write = classify_sql_write(tool_name, arguments)
    from trusted_data_agent.core import agent_authority as _aa
    return _aa.enforce_tool(tool_name, is_connector=is_connector, is_write=is_write,
                            arguments=arguments, aliases=aliases)


def denied_result(tool_name: str, reason: str) -> dict:
    from trusted_data_agent.core import agent_authority as _aa
    return _aa.denied_tool_result(tool_name, reason)


def preflight_sql(tool_name: str, arguments: Dict[str, Any], session_id: str) -> Optional[dict]:
    """Preflight a raw-SQL call. Returns a block result to short-circuit (enforce mode +
    nonexistent-database reference), or ``None`` to proceed. ``TDA_*`` exempt. Never raises."""
    if _is_framework_tool(tool_name):
        return None
    try:
        from trusted_data_agent.agent.preflight import guard as _guard
        return _guard.check_sql(tool_name, arguments or {}, session_id)
    except Exception:
        return None


def _coerce_to_dict(result: Any) -> Optional[dict]:
    """Best-effort extraction of a structured result dict from an MCP tool return, which may be a
    dict, a JSON string, or a LangChain ``content_and_artifact`` ``(content, artifact)`` tuple
    (artifact carries ``structured_content``)."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except Exception:
            return None
    if isinstance(result, tuple) and result:
        artifact = result[1] if len(result) > 1 else None
        structured = getattr(artifact, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        return _coerce_to_dict(result[0])
    if isinstance(result, list):
        # MCP content blocks ([{"type": "text", "text": "{…json…}"}]) — the shape the
        # ReAct lane's tools return when the server sends no structured content.
        for block in result:
            text = block.get("text") if isinstance(block, dict) else None
            if isinstance(text, str) and text.lstrip().startswith("{"):
                return _coerce_to_dict(text)
        # A bare JSON array of row objects (an MCP tool may answer `[{…}, {…}]`, which the
        # adapter's parser accepts): those ARE the rows. Read them as rows so the firewall scans
        # them and the registry numbers them — before, every lane skipped such a result.
        if result and all(isinstance(r, dict) and "type" not in r for r in result):
            return {"results": result}
        return None
    return None


def record_catalog(tool_name: str, arguments: Dict[str, Any], result: Any, session_id: str,
                   source: str = "") -> None:
    """After a ``base_databaseList(scope=all)`` success, cache the authoritative catalog.
    Accepts a dict result, a JSON string, or a content_and_artifact tuple. Never raises."""
    try:
        parsed = _coerce_to_dict(result)
        if parsed is None:
            return
        from trusted_data_agent.agent.preflight import guard as _guard
        _guard.maybe_record_catalog(tool_name, arguments or {}, parsed, session_id)
        _guard.maybe_learn_date_shapes(tool_name, arguments or {}, parsed, session_id)
        _guard.maybe_learn_values(tool_name, arguments or {}, parsed, session_id)
        # S3 — the ReAct twin of the adapter seam: register the statement's rows.
        from trusted_data_agent.agent import result_sets as _result_sets
        _result_sets.record(session_id, tool_name, arguments or {}, parsed, source=source)
        # B10 — what the call proved about its server (a multi-server turn only).
        from trusted_data_agent.agent import source_routing as _source_routing
        _source_routing.learn_from_result(tool_name, arguments or {}, parsed, source)
    except Exception:
        pass


def empty_result_note(tool_name: str, arguments: Dict[str, Any], result: Any, session_id: str) -> Optional[str]:
    """The value-existence guard's note for an EMPTY result whose filter rests on an
    unverified literal (None when there is nothing to say). ``TDA_*`` exempt. Never raises."""
    if _is_framework_tool(tool_name):
        return None
    try:
        parsed = _coerce_to_dict(result)
        if parsed is None:
            return None
        from trusted_data_agent.agent.preflight import guard as _guard
        vn = _guard.empty_result_note(tool_name, arguments or {}, parsed, session_id)
        return vn["note"] if vn else None
    except Exception:
        return None


def loop_guard_block(tool_name: str, arguments: Dict[str, Any]) -> Optional[dict]:
    """In-flight loop guard — block a byte-identical repeat BEFORE it runs (off unless a
    ``max_tool_repeats`` ceiling is configured; blocks the (N+1)th, never the retry). Returns
    a recovery-shaped block result or ``None``. ``TDA_*`` exempt. Never raises."""
    if _is_framework_tool(tool_name):
        return None
    try:
        from trusted_data_agent.agent import loop_guard as _loop_guard
        return _loop_guard.check_and_record(tool_name, arguments or {})
    except Exception:
        return None


def scan_result(tool_name: str, result: Any, *, user_uuid: Optional[str],
                session_id: Optional[str]) -> None:
    """Tool-result firewall (observe-only): a poisoned tool output — the indirect-injection
    lane the entry guard can't see — is detected + surfaced, never altered. ``TDA_*`` exempt.
    Coerces to the dict shape the firewall reads; never raises."""
    try:
        parsed = _coerce_to_dict(result)
        if parsed is None:
            return
        from trusted_data_agent.agent.guardrails import runtime as _guardrail_runtime
        _guardrail_runtime.scan_tool_result(
            tool_name, parsed, user_uuid=user_uuid, session_id=session_id)
    except Exception:
        pass



def tool_call_timeout() -> float:
    """The one per-call bound for a data tool, shared with the MCP adapter's
    ``TDA_MCP_TOOL_TIMEOUT`` so both engine lanes cut a runaway call at the same point."""
    try:
        from trusted_data_agent.mcp_adapter.adapter import _MCP_TOOL_TIMEOUT
        return float(_MCP_TOOL_TIMEOUT)
    except Exception:  # noqa: BLE001 — the adapter is optional in CI; the env var is the contract
        import os
        return float(os.environ.get("TDA_MCP_TOOL_TIMEOUT", "120"))


def tool_result_max_chars() -> int:
    """The model-facing size bound on a ReAct tool result. A result goes straight back
    into the model's context and is re-sent on EVERY later step of the loop, so a
    ``SELECT *`` on a 1,500-column table costs its size times the number of steps that
    follow (measured 2026-09-03: one turn read 2.88M input tokens over nine steps, under
    the time cap, with no cost bound at all). Env ``TDA_REACT_TOOL_RESULT_MAX_CHARS``."""
    import os
    try:
        return max(2000, int(os.environ.get("TDA_REACT_TOOL_RESULT_MAX_CHARS", "24000")))
    except ValueError:
        return 24000


def bound_result_content(tool_name: str, content: Any, limit: Optional[int] = None) -> Any:
    """Truncate an over-long model-facing tool result, keeping its head and appending a
    RECOVERY-SHAPED marker: the agent is told how much it saw, how much there was, and
    what to do (select fewer columns or rows, aggregate). Non-string content and content
    within the bound are returned untouched — byte-identical baseline."""
    if not isinstance(content, str):
        return content
    cap = limit or tool_result_max_chars()
    if len(content) <= cap:
        return content
    head = content[:cap]
    marker = (f"\n\n[TRUNCATED by the platform: showing {cap:,} of {len(content):,} characters of the "
              f"'{tool_name}' result. The full result is too large to reason over and would be re-sent "
              f"on every later step. Narrow the query — select only the columns you need, add a row "
              f"limit, or aggregate (COUNT/GROUP BY) — rather than reading rows.]")
    return head + marker


def _exception_leaves(exc: BaseException):
    """The innermost exceptions of ``exc`` — an ``ExceptionGroup`` (the MCP client's TaskGroup)
    is walked to its leaves; a chained ``__cause__`` is followed."""
    subs = getattr(exc, "exceptions", None)
    if subs:
        for sub in subs:
            yield from _exception_leaves(sub)
    elif exc.__cause__ is not None and exc.__cause__ is not exc:
        yield from _exception_leaves(exc.__cause__)
    else:
        yield exc


def tool_error_text(exc: BaseException) -> str:
    """The tool's own failure message inside a raised MCP call, or "" when there is none.

    A FastMCP 4 server raises a failed tool call as `McpError` (JSON-RPC error data), which the
    client delivers wrapped in TaskGroup ExceptionGroups. The leaves that are not the
    connection itself carry the server's text; "Internal error: " is the JSON-RPC envelope's
    prefix, not the tool's words. Both lanes read it: the Optimize adapter's error path
    and the ReAct wrapper's `failed_tool_result` (OC-F14)."""
    try:
        leaves = list(_exception_leaves(exc))
    except Exception:  # noqa: BLE001
        leaves = [exc]
    try:
        from mcp.shared.exceptions import McpError
    except Exception:  # noqa: BLE001 — no MCP SDK ⇒ no server-raised tool error to read
        return ""
    texts = []
    for leaf in leaves:
        # Only the SERVER's answer is the tool's text; any other exception (the client, the
        # socket) keeps the error path it always had.
        if not isinstance(leaf, McpError):
            continue
        msg = str(leaf).strip()
        if not msg or "unhandled errors in a TaskGroup" in msg:
            continue
        if msg.startswith("Internal error: "):
            msg = msg[len("Internal error: "):]
        texts.append(msg)
    return "\n".join(texts)


def transient_transport_error(exc: BaseException) -> bool:
    """True when every leaf of ``exc`` is the CONNECTION failing, not the tool: the MCP session
    could not be opened or dropped mid-request. Seen on the fusion bench (2026-09-24): the first
    call after a restart failed with "unhandled errors in a TaskGroup" and cost an LLM correction
    round although the identical call succeeded a second later. A timeout that may have left the
    server still working (read / write / pool) is NOT transient here — it has its own handling."""
    try:
        import httpx
        import anyio
        transport = (httpx.TransportError, anyio.ClosedResourceError, anyio.BrokenResourceError,
                     anyio.EndOfStream, ConnectionError)
        slow = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)
    except Exception:  # noqa: BLE001
        transport, slow = (ConnectionError,), ()
    try:
        # The leaf walk follows ``__cause__``, and httpx raises its ConnectError FROM httpcore's —
        # so a refused connection (the usual "server is down") ends on ``httpcore.ConnectError``,
        # which is not an httpx type. Without httpcore's own transport errors a down HTTP MCP
        # server was classified "not transient": no retry, no "could not be reached", no re-route
        # (found live, multi-source stage 1).
        import httpcore
        transport = transport + (httpcore.NetworkError, httpcore.RemoteProtocolError,
                                 httpcore.ConnectTimeout)
        slow = slow + (httpcore.ReadTimeout, httpcore.WriteTimeout, httpcore.PoolTimeout)
    except Exception:  # noqa: BLE001
        pass
    leaves = list(_exception_leaves(exc))
    return bool(leaves) and all(isinstance(l, transport) and not isinstance(l, slow) for l in leaves)


def transport_retry_allowed(tool_name: str, arguments: Optional[Dict[str, Any]]) -> bool:
    """A dropped connection may have dropped AFTER the server ran the call, so only a READ is
    ever resent: a statement classified as a write, or a tool named like one, never is."""
    if classify_sql_write(tool_name, arguments):
        return False
    try:
        from trusted_data_agent.core.agent_authority import _looks_like_write
        return not _looks_like_write(tool_name)
    except Exception:  # noqa: BLE001 — unknown ⇒ do not resend
        return False


def timed_out_result(tool_name: str, seconds: float, arguments: Optional[Dict[str, Any]] = None) -> dict:
    """Recovery-shaped error for a data tool that ran past its bound: names the tool and
    the bound, says the DATABASE was still working (not the model), and tells the agent
    what to do next — narrow the query, or state plainly what could not be computed."""
    sql = (arguments or {}).get("sql") if isinstance(arguments, dict) else None
    hint = ""
    if isinstance(sql, str) and sql.strip():
        hint = (" The statement was still running on the database. Narrow it — add a filter, "
                "join on the tables' full primary index (ORI, INCNUM on NIBRS segments), aggregate "
                "fewer rows — and retry once; if it cannot be narrowed, say the count could not be "
                "computed within the budget rather than estimating it.")
    return {
        "status": "error",
        "error": f"Tool '{tool_name}' exceeded its {int(seconds)}s budget.{hint}",
        "data": f"Timeout after {int(seconds)}s",
        "timed_out": True,
        "tool_name": tool_name,
    }

def failed_tool_result(tool_name: str, exc: BaseException, arguments: Optional[Dict[str, Any]] = None):
    """The model-facing shape of a tool that raised: the error text plus the one
    instruction that changes the outcome (fix the call, or answer honestly with what
    exists). Returned in the ``(content, artifact)`` shape so callers are unchanged."""
    # A FastMCP 4 server RAISES a failed call; its text is a leaf inside the client's TaskGroup
    # groups, and `str(exc)` of the outer group is only "unhandled errors in a TaskGroup" (OC-F14).
    msg = ("" if transient_transport_error(exc) else tool_error_text(exc)) or str(exc) or type(exc).__name__
    if len(msg) > 1500:
        msg = msg[:1500] + " …"
    app_logger.warning(f"ReAct tool '{tool_name}' failed: {msg[:300]}")
    from trusted_data_agent.agent.sql_dialect import db_abort_hint
    hint = db_abort_hint(msg)
    if hint:
        return (f"Error from {tool_name}: {msg}\n{hint}", None)
    text = (f"Error from {tool_name}: {msg}\n"
            f"NEXT STEP: correct the call (the error names what was wrong) and try again, "
            f"or answer with what you have — do not repeat the identical call.")
    return (text, None)


def _result_text(result: Any) -> str:
    """The human-readable text of a tool result in any of its shapes: a str, a dict, a
    list of content blocks, or the LangChain ``(content, artifact)`` tuple."""
    if isinstance(result, tuple) and result:
        return _result_text(result[0])
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        try:
            return json.dumps(result)
        except Exception:
            return str(result)
    if isinstance(result, list):
        return " ".join(_result_text(b.get("text", "") if isinstance(b, dict) else b) for b in result)
    return str(result or "")


TOP_RETRY_NOTE = ("[Uderia rewrote this query before it ran: Teradata takes its row limit as "
                  "`SELECT TOP n …` (no LIMIT; TOP never after ORDER BY). Write it that way "
                  "next time. Executed SQL: {sql}]")

SYNTAX_RETRY_NOTE = ("[Uderia rewrote this query before it ran — forms other databases accept and "
                     "Teradata refuses: {changes}. Write it that way next time. Executed SQL: {sql}]")

ALIAS_RETRY_NOTE = ("[Uderia rewrote this query before it ran: `{word}` is a Teradata reserved word, "
                    "so as a name (an alias, a table alias or a column) it must be a delimited "
                    "identifier — `\"{word}\"` — wherever the statement uses it. Quote it that way "
                    "next time, or pick a plain name. "
                    "Executed SQL: {sql}]")


DATE_LITERAL_RETRY_NOTE = ("[Uderia rewrote this query before it ran: a date written as a string ('2026-08-25') "
                           "cannot be compared with a TIMESTAMP column on Teradata; write it as a DATE literal "
                           "(DATE '2026-08-25') next time. Executed SQL: {sql}]")


TEXT_DATE_NOTE = ("[Uderia rewrote this query before it ran: {columns} is a DATE stored as text, "
                  "so every string comparison, ORDER BY, MIN/MAX and mismatched FORMAT on it was "
                  "wrapped in CAST(… AS DATE FORMAT '{shape}'). Write it that way next time. "
                  "Changes: {changes}. Executed SQL: {sql}]")


def text_date_rewrite(tool_name: str, arguments: Dict[str, Any], block: Optional[dict],
                      session_id: str) -> Optional[Dict[str, Any]]:
    """When the preflight text-date guard refused a statement whose fix is MECHANICAL
    (shape learned, no SUBSTR bucket, no ambiguous literal), return the rewrite so the
    chokepoint runs the corrected statement instead of handing the model the refusal —
    the ReAct twin of the Optimize ``TextDateStrategy``, on the ONE shared
    ``preflight.textdates.rewrite_sql``. None means "let the refusal through". Mode-gated by
    construction: a refusal (``block``) exists only under ``enforce``, so ``observe`` never
    reaches this rewrite and stays byte-identical. Never raises."""
    try:
        if not (isinstance(block, dict) and block.get("preflight_text_date")):
            return None
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return None
        from trusted_data_agent.agent.preflight import textdates as _td
        fix = _td.rewrite_sql(session_id, sql)
        if not fix:
            return None
        shape = next((v.get("shape") for v in (block.get("violations") or []) if v.get("shape")), "…")
        fix["note"] = TEXT_DATE_NOTE.format(columns=", ".join(fix.get("columns") or []), shape=shape,
                                            changes="; ".join(fix.get("changes") or []), sql=fix["sql"])
        app_logger.info(f"ReAct tool '{tool_name}': preflight refused a text-stored date use — "
                        f"running the mechanical CAST rewrite instead ({fix.get('columns')})")
        return fix
    except Exception as exc:                   # pragma: no cover - defensive
        app_logger.warning(f"ReAct text-date rewrite skipped for '{tool_name}': {exc}")
        return None


def fanout_rewrite(tool_name: str, arguments: Dict[str, Any], block: Optional[dict],
                   session_id: str) -> Optional[Dict[str, Any]]:
    """When the preflight fan-out guard refused a two-table JOIN whose fix is MECHANICAL,
    return the aggregate-then-join rewrite so the chokepoint runs the corrected statement
    instead of handing the model the refusal — the ReAct twin of the Optimize
    ``FanoutJoinStrategy``, on the ONE shared ``preflight.joins.rewrite_fanout_sql``.
    The rewritten statement is preflighted AGAIN in full (every check, not only the join
    guard's own re-check) and any refusal of it returns None — the original refusal then
    reaches the model, never a statement the guard would have refused. None also means
    "judgement involved" (a decline). Mode-gated by construction: a refusal (``block``)
    exists only under ``enforce``. Never raises."""
    try:
        if not (isinstance(block, dict) and block.get("preflight_fanout")):
            return None
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return None
        from trusted_data_agent.agent.preflight import joins as _joins
        fix = _joins.rewrite_fanout_sql(session_id, sql)
        if not fix:
            return None
        if preflight_sql(tool_name, dict(arguments, sql=fix["sql"]), session_id) is not None:
            app_logger.warning(f"ReAct tool '{tool_name}': the fan-out rewrite would itself be refused — "
                               f"handing the original refusal to the model")
            return None
        owner = next((v.get("owner") for v in (block.get("violations") or []) if v.get("owner")), None)
        fix["note"] = _joins.fanout_rewrite_note(fix, owner)
        app_logger.info(f"ReAct tool '{tool_name}': preflight refused a fan-out join {fix['pair']} on "
                        f"{fix['column']} — running the mechanical aggregate-then-join rewrite instead "
                        f"(grain: {fix['grain']}, TOP pushed: {fix['top_pushed']})")
        return fix
    except Exception as exc:                   # pragma: no cover - defensive
        app_logger.warning(f"ReAct fan-out rewrite skipped for '{tool_name}': {exc}")
        return None


# ── the rewrite record: a deterministic rewrite is a STEP of the turn, not a footnote ──────
#
# Every mechanical rewrite this wrapper performs (TOP placement, a reserved alias, a text-
# stored date, a fan-out join) used to reach the model as a note appended to the tool
# content — and NOTHING else: the persisted trace kept the model's ORIGINAL statement and a
# 5,000-char preview, so on a long result the historical Live Status card and the KG
# theatre narrated a statement that never ran (the Optimize lane emits a `System
# Self-Correction` event for the same rewrites; this lane emitted none). The record is
# captured here, at the ONE seam every rewrite passes through, into a per-turn list the
# agent sets at turn start (a MUTABLE list set in the parent — a child task's `.set()`
# would be invisible to it; mutation by reference is not) and drains after each tool call
# into a `conversation_tool_rewrite` event that persists beside `conversation_tool_completed`.
_REWRITES: contextvars.ContextVar[Optional[List[Dict[str, Any]]]] = contextvars.ContextVar(
    "tda_react_rewrites", default=None)


def begin_rewrite_capture() -> List[Dict[str, Any]]:
    """Arm the per-turn rewrite ledger (call once at the agent's turn start, in the task
    that will later drain it). Returns the list; a caller that never arms it sees nothing
    recorded — the wrapper stays byte-identical for any other consumer."""
    lst: List[Dict[str, Any]] = []
    _REWRITES.set(lst)
    return lst


def end_rewrite_capture() -> None:
    _REWRITES.set(None)


def record_rewrite(tool_name: str, kind: str, original_sql: Any, executed_sql: Any,
                   summary: str = "", changes: Optional[List[str]] = None, **extra: Any) -> Optional[Dict[str, Any]]:
    """Record a rewrite the wrapper performed for ``tool_name``. Never raises; None when
    no ledger is armed (nobody will read it)."""
    try:
        lst = _REWRITES.get()
        if lst is None:
            return None
        rec = {"tool_name": tool_name, "kind": kind,
               "original_sql": original_sql if isinstance(original_sql, str) else None,
               "executed_sql": executed_sql if isinstance(executed_sql, str) else None,
               "summary": summary or "", "changes": list(changes or [])}
        rec.update({k: v for k, v in extra.items() if v is not None})
        lst.append(rec)
        return rec
    except Exception:
        return None


def drain_rewrites(tool_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Take (and remove) the recorded rewrites — those of ``tool_name`` when given, else all.
    Order preserved. Never raises."""
    try:
        lst = _REWRITES.get()
        if not lst:
            return []
        if tool_name is None:
            out = list(lst); lst.clear(); return out
        out = [r for r in lst if r.get("tool_name") == tool_name]
        lst[:] = [r for r in lst if r.get("tool_name") != tool_name]
        return out
    except Exception:
        return []


REWRITE_KIND_LABEL = {
    "teradata_top": "LIMIT/TOP placement",
    "reserved_alias": "reserved word as alias",
    "text_date": "text-stored date",
    "fanout": "fan-out join → aggregate-then-join",
    "bare_date": "unquoted date → date literal",
    "teradata_syntax": "other-dialect form → Teradata",
}


def _annotate(result: Any, note: str) -> Any:
    """Append a note to the model-facing content half, whatever its shape."""
    if isinstance(result, tuple) and len(result) == 2:
        return (_annotate(result[0], note), result[1])
    if isinstance(result, str):
        return result + "\n" + note
    if isinstance(result, list):
        return list(result) + [{"type": "text", "text": note}]
    return result


def _lead(result: Any, note: str) -> Any:
    """PREPEND a note to the model-facing content half — a steer must be the first thing the
    model reads with the result, not a line after a JSON blob it may skim (observed: a note
    appended after a long result was ignored by a small model)."""
    if isinstance(result, tuple) and len(result) == 2:
        return (_lead(result[0], note), result[1])
    if isinstance(result, str):
        return note + "\n\n" + result
    if isinstance(result, list):
        return [{"type": "text", "text": note}] + list(result)
    return result


async def _retry_core(tool_name: str, arguments: Dict[str, Any], result: Any,
                      original, args, kwargs, fixed: str, note: str,
                      session_id: Optional[str] = None, kind: str = "dialect",
                      summary: str = "", keep_failed: bool = False) -> Tuple[Any, Optional[str]]:
    """Run ``original`` ONCE more with ``fixed`` in the sql slot → ``(result, note)``. A retry
    that still reads as a syntax error returns the ORIGINAL result (the truth) and no note; a
    success returns the retried result UNANNOTATED plus ``note``, and the argument dict is
    updated so the catalog and the firewall see the SQL that actually ran. The note is kept
    apart from the tool's output so everything that reads the output (the firewall, the
    catalog, the result-set registry) reads what the TOOL returned, never the platform's
    words about it. The one body behind every mechanical dialect retry.

    ``keep_failed`` (the reserved-word chain): a retry that still reads as a syntax error
    returns THAT refusal (and no note) instead of the original, and a tool that RAISES on the
    rewritten statement is handed back in its model-facing shape rather than propagating —
    the chain must read the NEW refusal to quote the next word. Off by default: the other
    retries keep their one-shot contract byte for byte.

    The REWRITTEN statement is preflighted exactly as the original was: the original may
    have failed open (a trailing ``TOP 10`` does not parse, so no join can be judged) and
    the rewrite is the statement that actually runs — seen live 2026-09-15 (@PXPROD): a
    cross-domain join the guard refuses in every other form ran through this retry.
    A refusal is a result, handed back in place of the retry."""
    from trusted_data_agent.agent.sql_dialect import looks_like_teradata_syntax_error
    new_kwargs = dict(kwargs)
    new_args = args
    if "sql" in new_kwargs:
        new_kwargs["sql"] = fixed
    elif len(args) == 1 and isinstance(args[0], dict):
        new_args = (dict(args[0], sql=fixed),)
    else:
        return result, None
    if session_id:
        block = preflight_sql(tool_name, dict(arguments or {}, sql=fixed), session_id)
        if block is not None:
            if isinstance(arguments, dict):
                arguments["sql"] = fixed
            return (json.dumps(block), None), None
    original_sql = (arguments or {}).get("sql") if isinstance(arguments, dict) else None
    if keep_failed:
        try:
            retried = await asyncio.wait_for(original(*new_args, **new_kwargs), timeout=tool_call_timeout())
        except asyncio.TimeoutError:
            retried = (json.dumps(timed_out_result(tool_name, tool_call_timeout(), dict(arguments or {}, sql=fixed))), None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:               # the tool RAISES on a refused statement (ToolException)
            retried = failed_tool_result(tool_name, exc, dict(arguments or {}, sql=fixed))
    else:
        retried = await asyncio.wait_for(original(*new_args, **new_kwargs), timeout=tool_call_timeout())
    if looks_like_teradata_syntax_error(_result_text(retried)):
        if keep_failed:
            return retried, None               # the chain reads the NEW refusal
        return result, None                    # the rewrite did not help — keep the truth
    if isinstance(arguments, dict):
        arguments["sql"] = fixed               # the catalog/firewall see what actually ran
    record_rewrite(tool_name, kind, original_sql, fixed, summary or REWRITE_KIND_LABEL.get(kind, kind))
    return retried, note


async def _retry_with_sql(tool_name: str, arguments: Dict[str, Any], result: Any,
                          original, args, kwargs, fixed: str, note: str,
                          session_id: Optional[str] = None, kind: str = "dialect",
                          summary: str = "") -> Any:
    """``_retry_core`` with its note appended to the result — the model-facing form."""
    res, n = await _retry_core(tool_name, arguments, result, original, args, kwargs, fixed, note,
                               session_id=session_id, kind=kind, summary=summary)
    return _annotate(res, n) if n else res


async def _top_retry_core(tool_name: str, arguments: Dict[str, Any], result: Any,
                          original, args, kwargs, session_id: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    try:
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return result, None
        from trusted_data_agent.agent.sql_dialect import (looks_like_teradata_syntax_error,
                                                          teradata_top_rewrite)
        if not looks_like_teradata_syntax_error(_result_text(result)):
            return result, None
        fixed = teradata_top_rewrite(sql)
        if not fixed:
            return result, None
        app_logger.info(f"ReAct tool '{tool_name}': Teradata 3706 on a LIMIT/TOP tail — "
                        f"retrying once with the mechanical SELECT TOP rewrite")
        return await _retry_core(tool_name, arguments, result, original, args, kwargs,
                                 fixed, TOP_RETRY_NOTE.format(sql=fixed), session_id=session_id,
                                 kind="teradata_top", summary="Teradata takes TOP n at SELECT, not a trailing LIMIT/TOP — moved it")
    except Exception as exc:                   # pragma: no cover - defensive
        app_logger.warning(f"ReAct TOP retry skipped for '{tool_name}': {exc}")
        return result, None


async def retry_teradata_top(tool_name: str, arguments: Dict[str, Any], result: Any,
                             original, args, kwargs, session_id: Optional[str] = None) -> Any:
    """On a Teradata syntax error over a SQL argument that ``teradata_top_rewrite`` can
    fix, run the rewritten statement ONCE and hand the model the successful result with
    a note naming the rewrite (so it learns the form rather than repeating it). Any
    doubt — no SQL, not that error, no mechanical rewrite, the retry failing too —
    returns the original result untouched. Never raises."""
    res, n = await _top_retry_core(tool_name, arguments, result, original, args, kwargs, session_id)
    return _annotate(res, n) if n else res


async def _syntax_retry_core(tool_name: str, arguments: Dict[str, Any], result: Any,
                             original, args, kwargs, session_id: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    """``!=``, ``SELECT *, expr``, ``DATE_TRUNC`` (+ a trailing LIMIT in the same statement) —
    ``sql_dialect.teradata_syntax_rewrite``, the same rewrite the Optimize lane's
    ``TeradataSyntaxStrategy`` applies. Only after Teradata refused the statement."""
    try:
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return result, None
        from trusted_data_agent.agent.sql_dialect import (looks_like_teradata_syntax_error,
                                                          teradata_syntax_rewrite)
        if not looks_like_teradata_syntax_error(_result_text(result)):
            return result, None
        rw = teradata_syntax_rewrite(sql)
        if not rw:
            return result, None
        fixed, changes = rw
        app_logger.info(f"ReAct tool '{tool_name}': Teradata syntax error — retrying once with "
                        f"the mechanical rewrite ({'; '.join(changes)})")
        return await _retry_core(tool_name, arguments, result, original, args, kwargs,
                                 fixed, SYNTAX_RETRY_NOTE.format(changes="; ".join(changes), sql=fixed),
                                 session_id=session_id, kind="teradata_syntax",
                                 summary="; ".join(changes))
    except Exception as exc:                   # pragma: no cover - defensive
        app_logger.warning(f"ReAct syntax retry skipped for '{tool_name}': {exc}")
        return result, None


#: How many DISTINCT reserved words one call may quote in turn. Teradata names ONE word per
#: refusal, so a statement with two (prod 09bc567f, Oct 2026: the table `account` AND the
#: alias `CM`) needs one round per word. Bounded: a statement that keeps naming new words is
#: handed back after this many, with the latest refusal.
ALIAS_CHAIN_MAX = 3

ALIAS_FAILED_NOTE = ("[Uderia quoted the Teradata reserved word(s) {words} as delimited identifiers and re-ran "
                     "the statement. The database then refused the REWRITTEN statement with the error below — "
                     "the earlier refusal naming `{first}` no longer applies. Fix what this error names and keep "
                     "the quoted identifiers. Rewritten SQL: {sql}]")


async def _alias_retry_core(tool_name: str, arguments: Dict[str, Any], result: Any,
                            original, args, kwargs, session_id: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    """Error 3706/3707 naming a reserved word used as a name → the statement re-runs with that
    identifier quoted; if Teradata then names ANOTHER reserved word, that one is quoted too
    (≤ ``ALIAS_CHAIN_MAX`` rounds). Outcomes:

    * the rewritten statement runs → its result, plus a note naming the word(s) quoted;
    * it is refused the same way (no new word) → the ORIGINAL result, unchanged (the
      rewrite did not help — the one-shot contract every dialect retry keeps);
    * it is refused for something ELSE — a further reserved word past the bound, or any
      other error — → the LATEST refusal, led by a note saying what was quoted. Prod
      09bc567f (Oct 2026): `retail_core.account … AS CM` — `account` was quoted, the
      retry failed on `CM`, that failure RAISED inside the retry and was logged as
      "retry skipped", and the model received the original `account` error seven times
      over; it never learned about `CM`. A model cannot fix what it was not told.
    Never raises."""
    try:
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return result, None
        from trusted_data_agent.agent.sql_dialect import (looks_like_teradata_syntax_error,
                                                          reserved_alias_from_error,
                                                          teradata_reserved_alias_rewrite)
        text = _result_text(result)
        word = reserved_alias_from_error(text)
        if not word:
            return result, None
        words: List[str] = []
        current_sql, current_text = sql, text
        retried: Any = None
        fixed: Optional[str] = None
        for _round in range(ALIAS_CHAIN_MAX):
            fixed = teradata_reserved_alias_rewrite(current_sql, current_text)
            if not fixed:
                fixed = current_sql if words else None
                break                                   # the word is not an identifier here
            words.append(word)
            app_logger.info(f"ReAct tool '{tool_name}': Teradata names the reserved word "
                            f"'{word}' — retrying with the identifier quoted"
                            + (f" (round {len(words)}, also quoted: {', '.join(words[:-1])})" if len(words) > 1 else ""))
            quoted = ", ".join(f"`{w}`" for w in words)
            # The one retry body, asked to hand back a FAILED retry too (the chain reads it).
            # Original sql must stay in `arguments` until a statement actually runs: the body
            # updates it only on success (or a preflight refusal, which is a result).
            retried, note = await _retry_core(
                tool_name, arguments, result, original, args, kwargs, fixed,
                ALIAS_RETRY_NOTE.format(word=words[0] if len(words) == 1 else " / ".join(words), sql=fixed),
                session_id=session_id, kind="reserved_alias", keep_failed=True,
                summary=(f"{quoted} {'is a' if len(words) == 1 else 'are'} Teradata reserved word{'s' if len(words) > 1 else ''} — "
                         "quoted as delimited identifiers wherever the statement uses them"))
            if note is not None:
                return retried, note                    # the rewritten statement ran
            rtext = _result_text(retried)
            if not looks_like_teradata_syntax_error(rtext):
                return retried, None                    # a preflight refusal is a result
            nxt = reserved_alias_from_error(rtext)
            if not nxt or nxt.lower() in {w.lower() for w in words}:
                break                                   # the same refusal, or not a reserved word
            word, current_sql, current_text = nxt, fixed, rtext
        if retried is None or not _names_new_refusal(text, _result_text(retried)):
            return result, None                         # refused the same way — the one-shot contract
        app_logger.info(f"ReAct tool '{tool_name}': the statement with {', '.join(words)} quoted was refused "
                        f"differently — handing the model the latest refusal")
        note = ALIAS_FAILED_NOTE.format(words=", ".join(f"`{w}`" for w in words), first=words[0], sql=fixed)
        return _lead(retried, note), None
    except Exception as exc:                   # pragma: no cover - defensive
        app_logger.warning(f"ReAct reserved-alias retry skipped for '{tool_name}': {exc}")
        return result, None


def _names_new_refusal(before: str, after: str) -> bool:
    """True when the retried refusal names a different problem than the original (a new
    reserved word, or a different error number / text), False when it is the same refusal."""
    import re as _re
    def _sig(t: str) -> str:
        m = _re.search(r"\[Error (\d+)\].*?(?:between|expected)([^\n]{0,160})", t or "", _re.IGNORECASE | _re.DOTALL)
        return (m.group(1) + "|" + m.group(2).strip().lower()) if m else (t or "").strip().lower()[:200]
    return _sig(before) != _sig(after)


async def _date_literal_retry_core(tool_name: str, arguments: Dict[str, Any], result: Any,
                                   original, args, kwargs, session_id: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    """Error 6760 on a bare ISO date string compared with a TIMESTAMP: the literal becomes a
    DATE literal and the statement runs once more (``sql_dialect.teradata_iso_date_literal_rewrite``,
    the same rewrite the Optimize ``TeradataDateLiteralStrategy`` applies). Never raises."""
    try:
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return result, None
        from trusted_data_agent.agent.sql_dialect import (looks_like_invalid_timestamp,
                                                          teradata_iso_date_literal_rewrite)
        if not looks_like_invalid_timestamp(_result_text(result)):
            return result, None
        fixed = teradata_iso_date_literal_rewrite(sql)
        if not fixed:
            return result, None
        app_logger.info(f"'{tool_name}': Teradata 6760 on an ISO date string — retrying once with DATE literals")
        return await _retry_core(tool_name, arguments, result, original, args, kwargs,
                                 fixed, DATE_LITERAL_RETRY_NOTE.format(sql=fixed), session_id=session_id,
                                 kind="date_literal",
                                 summary="an ISO date written as a string cannot compare with a TIMESTAMP — wrote it as DATE '…'")
    except Exception as exc:                   # pragma: no cover - defensive
        app_logger.warning(f"date-literal retry skipped for '{tool_name}': {exc}")
        return result, None


async def retry_teradata_reserved_alias(tool_name: str, arguments: Dict[str, Any], result: Any,
                                        original, args, kwargs, session_id: Optional[str] = None) -> Any:
    """On Error 3706/3707 naming a reserved word used as a name, run the statement ONCE
    with that identifier quoted (``teradata_reserved_alias_rewrite``) — the round the model
    spent on production 2026-09-05 (`… END AS Month`). Same contract as the TOP retry:
    any doubt returns the original result untouched; never raises."""
    res, n = await _alias_retry_core(tool_name, arguments, result, original, args, kwargs, session_id)
    return _annotate(res, n) if n else res


async def dialect_retries(tool_name: str, arguments: Dict[str, Any], result: Any,
                          original, args, kwargs, session_id: Optional[str] = None) -> Tuple[Any, List[str]]:
    """Every mechanical Teradata retry, in order (forms other dialects accept — ``!=``,
    ``SELECT *, expr``, ``DATE_TRUNC`` —, a trailing LIMIT/TOP, a reserved alias, an ISO
    date string compared with a TIMESTAMP) → ``(result, notes)``. The result is the TOOL's; the notes are the platform's."""
    notes: List[str] = []
    result, n = await _syntax_retry_core(tool_name, arguments, result, original, args, kwargs, session_id)
    if n:
        notes.append(n)
    result, n = await _top_retry_core(tool_name, arguments, result, original, args, kwargs, session_id)
    if n:
        notes.append(n)
    result, n = await _alias_retry_core(tool_name, arguments, result, original, args, kwargs, session_id)
    if n:
        notes.append(n)
    result, n = await _date_literal_retry_core(tool_name, arguments, result, original, args, kwargs, session_id)
    if n:
        notes.append(n)
    return result, notes


# ── ONE SQL gate, three lanes ──────────────────────────────────────────────────
#
# Every tool call that can carry an agent's SQL passes the SAME ordered protections,
# whichever lane runs it: the MCP adapter (Optimize / genie experts / FASTPATH), the
# LangChain MCP tool wrapper (Conversation / ReAct) and ``guarded_sql_call`` (a component
# that executes the agent's SQL — the Database Navigator's ``query`` action). Before this,
# each lane restated the sequence and they had drifted (test/test_sql_gate_lanes.py pins
# the baseline and names each difference the move removed): the adapter checked for
# non-string SQL before authority, so a denied call was never recorded as a denial; the
# other two ran the loop guard before it, so a second unrunnable call read "repeated
# identical call" instead of the reason it can never run.
#
# What legitimately differs between lanes is DECLARED here, never re-derived at a call site.

@dataclass(frozen=True)
class SqlLane:
    """A lane's policy. ``budget_gate`` — refuse a call once the turn budget is used up (the
    Optimize plan is fixed: its phase loop skips what the budget cannot afford instead).
    ``mechanical_fixes`` — apply the deterministic rewrites inline (text-stored dates, the
    fan-out) and retry a Teradata 3706/3707 once (the Optimize lane applies the same fixes in
    its correction chain, where each shows as a System Self-Correction step)."""
    name: str
    budget_gate: bool
    mechanical_fixes: bool


LANE_OPTIMIZE = SqlLane("optimize", budget_gate=False, mechanical_fixes=False)
LANE_REACT = SqlLane("react", budget_gate=True, mechanical_fixes=True)
LANE_NAVIGATOR = SqlLane("navigator", budget_gate=True, mechanical_fixes=True)


def sql_gate_before(tool_name: str, arguments: Dict[str, Any], session_id: str, lane: SqlLane, *,
                    dialect: str = "teradata",
                    aliases: Optional[frozenset] = None) -> Tuple[Optional[dict], Optional[dict], Optional[str]]:
    """Every protection that runs BEFORE a SQL-bearing tool call, in one order →
    ``(refusal, fix, kind)``. ``refusal`` set: nothing may run; hand it to the model. ``fix``
    set: run ``fix["sql"]`` instead (already recorded as a rewrite; ``fix["note"]`` is what
    the model reads). Both ``None``: run the call as asked. ``TDA_*`` tools pass untouched.

    Order: authority first (a denial is the security fact, recorded on the tool surface
    whatever else is wrong with the call) → non-string SQL (definitive: never counted as a
    repeat) → a phantom source (FROM a plan step's output) → the every-lane fix of an
    unquoted date (so everything after judges the statement that runs) → loop guard → turn
    budget (lanes with a gate) → preflight, whose refusal a lane with mechanical fixes answers
    with the rewrite when one exists (Teradata only)."""
    allowed, reason = check_authority(tool_name, arguments=arguments, aliases=aliases)
    if not allowed:
        app_logger.warning(f"agent-authority: DENIED '{tool_name}' ({lane.name}) — {reason}")
        try:
            from trusted_data_agent.agent import tool_surface as _tsurf
            _tsurf.note_denied_call(tool_name)   # S6: the named finding on the surface
        except Exception:  # noqa: BLE001
            pass
        return denied_result(tool_name, reason), None, None
    # PROP-011: nothing runs past a question for the user — refused, recovery-shaped, before
    # any statement is read (the final-report tools are exempt inside the ledger).
    try:
        from trusted_data_agent.agent import input_requests as _ir_mod
        _ir_block = _ir_mod.refusal_for(tool_name)
    except Exception:  # noqa: BLE001
        _ir_block = None
    if _ir_block is not None:
        return _ir_block, None, None
    gates = [nonstring_sql_block(tool_name, arguments), phantom_source_block(tool_name, arguments)]
    for gate in gates:
        if gate is not None:
            return gate, None, None
    # An unquoted calendar date is arithmetic in every dialect — fixed before anything reads
    # the statement, in every lane (the loop guard and preflight judge the statement that runs).
    bare = bare_date_fix(tool_name, arguments, dialect)
    if bare is not None:
        arguments = dict(arguments, sql=bare["sql"])
    gate = loop_guard_block(tool_name, arguments)
    if gate is not None:
        return gate, None, None
    if lane.budget_gate:
        gate = _turn_budget.tool_gate(tool_name)
        if gate is not None:
            return gate, None, None
    block = preflight_sql(tool_name, arguments, session_id)
    if block is None:
        return (None, bare, "bare_date") if bare is not None else (None, None, None)
    if lane.mechanical_fixes and dialect == _TERADATA:
        for kind, rewrite in (("text_date", text_date_rewrite), ("fanout", fanout_rewrite)):
            fix = rewrite(tool_name, arguments, block, session_id)
            if fix is not None:
                record_rewrite(tool_name, kind, arguments.get("sql"), fix["sql"],
                               summary=("; ".join(fix.get("changes") or []) or REWRITE_KIND_LABEL[kind]),
                               changes=fix.get("changes"), grain=fix.get("grain"), pair=fix.get("pair"),
                               columns=fix.get("columns"))
                if bare is not None:
                    fix = dict(fix, note=f"{bare['note']} {fix.get('note') or ''}".strip(),
                               changes=list(bare.get("changes") or []) + list(fix.get("changes") or []))
                return None, fix, kind
    return block, None, None


def bare_date_fix(tool_name: str, arguments: Optional[Dict[str, Any]],
                  dialect: str = "teradata") -> Optional[Dict[str, Any]]:
    """``{"sql", "note", "changes"}`` when the statement holds an unquoted ``YYYY-MM-DD``
    (``sql_dialect.bare_iso_date_rewrite``), recorded as a rewrite; else ``None``. ``TDA_*``
    exempt. Never raises."""
    if _is_framework_tool(tool_name):
        return None
    try:
        sql = (arguments or {}).get("sql")
        from trusted_data_agent.agent.sql_dialect import bare_iso_date_rewrite
        fixed = bare_iso_date_rewrite(sql, typed=(str(dialect or "").lower() != "sqlite"))
        if not fixed:
            return None
        note = ("[Rewrite] An unquoted date is arithmetic in SQL (2026-08-25 = 1993); it was "
                "written as a date literal before the statement ran. Write dates as DATE "
                "'YYYY-MM-DD'.")
        change = "unquoted YYYY-MM-DD → date literal"
        record_rewrite(tool_name, "bare_date", sql, fixed, summary=change, changes=[change])
        return {"sql": fixed, "note": note, "changes": [change]}
    except Exception:  # noqa: BLE001
        return None


def sql_gate_after(tool_name: str, arguments: Dict[str, Any], result: Any, session_id: str, *,
                   user_uuid: Optional[str] = None,
                   scan: bool = True,
                   source: str = "") -> Tuple[Optional[dict], Optional[str]]:
    """Everything that runs AFTER a SQL-bearing tool returned, on the TOOL's output (never on
    the platform's notes about it) → ``(value_note, budget_note)``.

    The tool-result firewall (``scan=False`` when there is no tool output — the call raised);
    the catalog, date-shape and value learning; the turn's result-set registry; the
    value-existence guard's note for an empty result on an unverified literal (structured:
    ``{"note", "unverified"}``); the turn budget's step. Never raises."""
    if scan:
        scan_result(tool_name, result, user_uuid=user_uuid, session_id=session_id)
    record_catalog(tool_name, arguments, result, session_id, source=source)
    vn = None
    if not _is_framework_tool(tool_name):
        try:
            parsed = _coerce_to_dict(result)
            if parsed is not None:
                from trusted_data_agent.agent.preflight import guard as _guard
                vn = _guard.empty_result_note(tool_name, arguments or {}, parsed, session_id)
                # A decimal division truncated to a literal's scale: a warning on a statement
                # that RAN, travelling on the same note channel (never a block, never a rewrite).
                dn = _guard.decimal_division_note(tool_name, arguments or {}, parsed)
                if dn:
                    vn = ({"note": f"{vn['note']}\n{dn}", "unverified": vn.get("unverified") or []}
                          if vn else {"note": dn, "unverified": []})
        except Exception:  # noqa: BLE001
            vn = None
    return vn, _turn_budget.after_tool(tool_name)


def steer_note(session_id: str, tool_name: str, user_uuid: Optional[str]) -> Optional[str]:
    """S10 — a steer sent while the agent worked, read at THIS step boundary. Never raises."""
    try:
        from trusted_data_agent.core import steer as _steer
        return _steer.consume_at_boundary(session_id, tool_name, user_uuid)
    except Exception:  # noqa: BLE001
        return None


# ── one guarded SQL call, for callers that are not an MCP tool ─────────────────
#
# A component that executes an agent's SQL (the Database Navigator's `query` action) is
# the same escape hatch as an MCP SQL tool by another name — the agent_authority module
# says so in as many words. Every protection the two MCP chokepoints apply is keyed on a
# non-`TDA_*` tool name or a `sql` argument, so a component call reached NONE of them.
# `guarded_sql_call` runs the SAME gate the ReAct wrapper runs around a caller-supplied
# `execute(arguments) -> dict`. The caller passes a LOGICAL tool name (never `TDA_*`) so
# nothing is exempted.

#: Teradata-only rewrites: they emit Teradata syntax (TOP, CAST … FORMAT), which another
#: dialect rejects.
_TERADATA = "teradata"


async def guarded_sql_call(tool_name: str, arguments: Dict[str, Any], execute, *,
                           session_id: str, user_uuid: Optional[str] = None,
                           dialect: str = _TERADATA,
                           aliases: Optional[frozenset] = None,
                           source_id: str = "",
                           scan_result: bool = True) -> Dict[str, Any]:
    """See ``_guarded_sql_call``. The whole call — authority's write classification, preflight,
    learning from the result, the empty-result note — runs in the call's dialect and against
    its own source's facts (``agent/preflight/dialect.py``): a statement is parsed as what it
    is, and a Teradata ontology never judges a PostgreSQL statement."""
    from trusted_data_agent.agent.preflight import dialect as _pd
    with _pd.using(dialect, source_id):
        return await _guarded_sql_call(tool_name, arguments, execute, session_id=session_id,
                                       user_uuid=user_uuid, dialect=_pd.normalize(dialect), aliases=aliases,
                                       scan_result=scan_result)


async def _guarded_sql_call(tool_name: str, arguments: Dict[str, Any], execute, *,
                            session_id: str, user_uuid: Optional[str] = None,
                            dialect: str = _TERADATA,
                            aliases: Optional[frozenset] = None,
                            scan_result: bool = True) -> Dict[str, Any]:
    """Run ``execute(arguments)`` behind the one SQL gate (``LANE_NAVIGATOR``).

    ``scan_result=False`` skips the tool-result firewall for a result made ONLY of rows that were
    already scanned when their own query returned (the combine step) — scanning them again
    raised every injection finding twice. Every other protection still runs.

    ``execute`` is an async callable taking the argument dict (with ``sql``) and returning a
    result dict — ``{"status": "success", "results": [row dicts], ...}`` or
    ``{"status": "error", "error": "..."}``; it may also raise.

    Returns ``{"outcome": "refused" | "error" | "success", "result": dict,
    "executed_sql": str, "notes": [str], "rewrites": [dict]}``. ``refused`` means nothing
    ran (the result is the refusal to hand the model); ``notes`` are what the model must
    read beside the result — a rewrite that changed the statement, an unverified literal,
    the remaining budget, a steer. Never raises except ``CancelledError``."""
    if _is_framework_tool(tool_name):   # a caller bug that would exempt everything below
        raise ValueError(f"guarded_sql_call needs a logical, non-TDA_ tool name (got {tool_name!r})")
    arguments = dict(arguments or {})
    notes: List[str] = []
    rewrites: List[Dict[str, Any]] = []

    refusal, fix, kind = sql_gate_before(tool_name, arguments, session_id, LANE_NAVIGATOR,
                                         dialect=dialect, aliases=aliases)
    if refusal is not None:
        return {"outcome": "refused", "result": refusal, "executed_sql": arguments.get("sql"),
                "notes": notes, "rewrites": rewrites}
    rewrite_note = None
    if fix is not None:
        rewrites.append({"kind": kind, "original_sql": arguments.get("sql"), "executed_sql": fix["sql"],
                         "changes": fix.get("changes") or []})
        arguments["sql"] = fix["sql"]
        rewrite_note = fix["note"]

    raised = [False]

    async def _run(**kw):
        try:
            return await execute(dict(kw))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a tool ERROR is a result, never a crash
            raised[0] = True
            return {"status": "error", "error": str(exc) or type(exc).__name__}

    try:
        result = await asyncio.wait_for(_run(**arguments), timeout=tool_call_timeout())
    except asyncio.TimeoutError:
        secs = tool_call_timeout()
        app_logger.error(f"⏱️ '{tool_name}' exceeded {secs}s (session {session_id})")
        return {"outcome": "error", "result": timed_out_result(tool_name, secs, arguments),
                "executed_sql": arguments.get("sql"), "notes": notes, "rewrites": rewrites}

    retry_notes: List[str] = []
    if dialect == _TERADATA:
        before_sql = arguments.get("sql")
        result, retry_notes = await dialect_retries(tool_name, arguments, result, _run, (),
                                                    dict(arguments), session_id)
        if isinstance(result, tuple):          # a retry refused by preflight hands back (json, None)
            try:
                result = json.loads(result[0])
            except Exception:  # noqa: BLE001
                result = {"status": "error", "error": str(result[0])}
        if arguments.get("sql") != before_sql:
            rewrites.append({"kind": "dialect", "original_sql": before_sql,
                             "executed_sql": arguments.get("sql"), "changes": []})

    if not isinstance(result, dict):
        result = {"status": "error", "error": str(result)}
    ok = str(result.get("status") or "").lower() == "success"
    from trusted_data_agent.agent.preflight import dialect as _pd
    vn, bnote = sql_gate_after(tool_name, arguments, result, session_id, user_uuid=user_uuid,
                               scan=(not raised[0]) and scan_result, source=_pd.source())
    notes.extend(retry_notes)
    for n in (rewrite_note, vn["note"] if vn else None, bnote):
        if n:
            notes.append(n)
    snote = steer_note(session_id, tool_name, user_uuid)
    if snote:
        notes.insert(0, snote)
    return {"outcome": "success" if ok else "error", "result": result,
            "executed_sql": arguments.get("sql"), "notes": notes, "rewrites": rewrites}


def wrap_langchain_mcp_tools(tools, session_id: str, user_uuid: Optional[str] = None):
    """Decorate each LangChain MCP tool's coroutine with the FULL chokepoint battery so the
    ReAct path reaches parity with ``invoke_mcp_tool`` — authority, preflight, in-flight loop
    guard, tool-result firewall (and, through authority, the budget cap). Mutates each tool's
    ``coroutine`` in place (preserving name / description / args_schema) and returns the list.

    Enforcement runs at execution time (not load time) so it reads the live ambient principal;
    an interactive user turn has no principal → authority + loop/budget are no-ops → baseline."""
    # B10 — every MCP tool's own coroutine and server, so a call naming data its server cannot
    # read can run on the agent's server that can (agent/source_routing.py). Filled below, read
    # at call time; only a multi-server turn ever reads it.
    peers: Dict[str, Tuple[str, Any]] = {}
    for tool in tools or []:
        try:
            tool_name = getattr(tool, "name", None)
            original = getattr(tool, "coroutine", None)
            if original is None or _is_framework_tool(tool_name):
                continue  # framework/sync tools: nothing to guard here
            _srv = (getattr(tool, "metadata", None) or {}).get("uderia_mcp_server_id")
            if _srv and tool_name:
                peers[tool_name] = (str(_srv), original)
            tool.coroutine = _make_guarded_coroutine(tool_name, original, session_id, user_uuid,
                                                     source=(f"mcp:{_srv}" if _srv else ""), peers=peers)
        except Exception as e:  # never let wrapping break tool loading
            app_logger.warning(f"tool_enforcement: could not wrap '{getattr(tool, 'name', '?')}': {e}")
    return tools


async def _react_route_before(tool_name, own_sid, arguments, peers):
    """B10, ReAct lane — ``None`` (run as called), a refusal text (several servers hold the
    data), or ``(name, coroutine, server_id, info)`` of the agent's server that holds it. The
    decision is the shared one (``source_routing.plan_before``, the Optimize lane's too)."""
    if not peers:
        return None
    from trusted_data_agent.agent import source_routing as _sr
    d = await _sr.plan_before(tool_name, own_sid, arguments, lane=_sr.SOURCE_LANE_REACT,
                              available=set(peers))
    if d.action == _sr.REFUSE:
        return json.dumps({"status": "error", "error": d.refusal})
    if d.action == _sr.REROUTE and d.to.tool_name in peers:
        app_logger.info(f"[source routing] '{tool_name}' → '{d.to.tool_name}' (server {d.to.server_id} holds {d.databases})")
        return (d.to.tool_name, peers[d.to.tool_name][1], d.to.server_id, d.info)
    return None


async def _react_route_after(tool_name, own_sid, arguments, error_text, peers, unreachable: bool = False):
    """After a failed call — ``None``, a refusal text to lead onto the failed result (several of
    the agent's servers could hold the data), or the re-route plan (shared ``plan_after``)."""
    if not peers:
        return None
    from trusted_data_agent.agent import source_routing as _sr
    d = await _sr.plan_after(tool_name, own_sid, arguments, error_text, lane=_sr.SOURCE_LANE_REACT,
                             available=set(peers), unreachable=unreachable)
    if d.action == _sr.REROUTE and d.to.tool_name in peers:
        app_logger.info(f"[source routing] '{tool_name}' refused by server {own_sid} — retrying on '{d.to.tool_name}'")
        return (d.to.tool_name, peers[d.to.tool_name][1], d.to.server_id, d.info)
    if d.action == _sr.REFUSE and d.refusal:
        return d.refusal
    return None


def _make_guarded_coroutine(tool_name: str, original, session_id: str,
                            user_uuid: Optional[str] = None, source: str = "",
                            peers: Optional[Dict[str, Tuple[str, Any]]] = None):
    # PROP-006 phase 2 (O5/O6): a tool of one of the agent's ADDITIONAL MCP servers runs its gate
    # under that server's preflight source; every other tool — every tool of a single-server
    # agent — is guarded exactly as before.
    _sid = source[4:] if isinstance(source, str) and source.startswith("mcp:") else None
    _peers = peers if peers is not None else {}

    def _arguments_of(args, kwargs) -> Dict[str, Any]:
        a: Dict[str, Any] = dict(kwargs)
        if not a and len(args) == 1 and isinstance(args[0], dict):
            a = dict(args[0])
        return a

    async def _scoped(*args, **kwargs):
        from trusted_data_agent.agent.preflight.dialect import additional_mcp_scope
        # B10 — decide where the call runs BEFORE the SQL gate, so the gate judges the
        # statement against the facts of the server that will actually run it.
        plan = await _react_route_before(tool_name, _sid, _arguments_of(args, kwargs), _peers)
        if isinstance(plan, str):                       # several servers hold the data: ask
            return plan, None
        if plan is not None:
            return await _run_rerouted(plan, args, kwargs)
        with additional_mcp_scope(_sid):
            return await _guarded(tool_name, original, source, True, *args, **kwargs)

    async def _run_rerouted(plan, args, kwargs, require_success: bool = False):
        """Run the call as the other server's tool (its own gate, scope and result recording).
        ``require_success``: a retry that fails there too returns None — the first error stands."""
        from trusted_data_agent.agent import source_routing as _sr
        from trusted_data_agent.agent.preflight.dialect import server_scope
        to_name, to_orig, to_sid, info = plan
        with server_scope(to_sid):
            out = await _guarded(to_name, to_orig, f"mcp:{to_sid}", False, *args, **kwargs)
        if require_success and _result_text(out).lstrip().startswith("Error from "):
            if info.get("databases"):
                _sr.learn_outcome(_sr.turn_user(), to_sid, info["databases"], held=False)
            return None
        _sr.record_reroute(tool_name, info)
        return _lead(out, info["note"])

    async def _guarded(tool_name, original, source, may_reroute, *args, **kwargs):
        # Reconstruct the argument dict the tool will run with (kwargs is the normal shape; a
        # single positional dict is tolerated defensively).
        arguments: Dict[str, Any] = _arguments_of(args, kwargs)

        # The one SQL gate — authority, non-string SQL, loop guard, turn budget, preflight
        # with its mechanical rewrites. A refusal matches the MCP tool's content_and_artifact
        # shape: (content, artifact).
        refusal, fix, _kind = sql_gate_before(tool_name, arguments, session_id, LANE_REACT)
        if refusal is not None:
            return json.dumps(refusal), None
        rewrite_note = None
        if fix is not None:
            arguments["sql"] = fix["sql"]
            if "sql" in kwargs:
                kwargs = dict(kwargs, sql=fix["sql"])
            elif len(args) == 1 and isinstance(args[0], dict):
                args = (dict(args[0], sql=fix["sql"]),)
            rewrite_note = fix["note"]

        # The per-turn tool ledger (agent/turn_tool_ledger.py) — AFTER the gate, so a denied
        # or blocked call is refused in the gate's own words, never "reused" or "disabled":
        #   * breaker: a tool that failed N times in a row this turn is off for the rest of it;
        #   * reuse: an identical call that already SUCCEEDED this turn hands back its result
        #     (zero tool time; a write between the two invalidates every memoised read).
        from trusted_data_agent.agent import turn_tool_ledger as _ledger
        _block = _ledger.breaker_block(tool_name)
        if _block is not None:
            return json.dumps(_block), None
        _again = _ledger.reuse(tool_name, arguments)
        if _again is not None:
            return _lead(_again, f"(Result reused — this identical '{tool_name}' call already "
                                 f"ran earlier this turn.)")

        # Per-call bound — parity with the MCP adapter's TDA_MCP_TOOL_TIMEOUT, which the
        # ReAct lane never had: a slow warehouse query ran until the engine's whole-turn
        # cap killed the turn and blamed the MODEL ("did not respond within 300 seconds").
        # A bounded call returns a recovery-shaped result naming what actually ran long,
        # so the agent can narrow the query or say honestly what it could not compute.
        _raised = False
        try:
            result = await asyncio.wait_for(original(*args, **kwargs), timeout=tool_call_timeout())
        except asyncio.TimeoutError:
            _ledger.record_failure(tool_name)
            secs = tool_call_timeout()
            app_logger.error(f"⏱️ ReAct tool '{tool_name}' exceeded {secs}s (session {session_id})")
            return json.dumps(timed_out_result(tool_name, secs, arguments)), None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A tool ERROR is a result, not a crash. `langchain_mcp_adapters` raises
            # ToolException on any MCP error result and LangGraph's default handler
            # re-raises it, so ONE failed SQL statement ended the whole turn with a
            # canned "I encountered an error" answer (observed locally, 2026-09-04) —
            # the model never saw the error it could have corrected, and no
            # deterministic retry could run. Hand the error back as tool content,
            # recovery-shaped, exactly as the Optimize path's adapter does.
            # When the CONNECTION failed rather than the tool, a read is resent ONCE first
            # (0 LLM rounds) — the same rule as the MCP adapter's.
            result = None
            _raised = True
            if transient_transport_error(exc) and transport_retry_allowed(tool_name, arguments):
                app_logger.warning(f"ReAct tool '{tool_name}': transient MCP transport failure — retrying once")
                try:
                    result = await asyncio.wait_for(original(*args, **kwargs), timeout=tool_call_timeout())
                except asyncio.CancelledError:
                    raise
                except Exception as exc2:  # noqa: BLE001 — the second failure is the one reported
                    exc = exc2
            if result is None:
                # B10 — "this server cannot read that" on a READ: once, on the agent's server
                # that can (a multi-server turn only; never a write, never twice).
                _down = transient_transport_error(exc)
                _after_refusal = None
                if may_reroute:
                    plan = await _react_route_after(tool_name, _sid, arguments,
                                                    ("" if _down else tool_error_text(exc)) or str(exc), _peers,
                                                    unreachable=_down)
                    if isinstance(plan, str):
                        _after_refusal = plan
                    elif plan is not None:
                        retried = await _run_rerouted(plan, args, kwargs, require_success=True)
                        if retried is not None:
                            return retried
                result = failed_tool_result(tool_name, exc, arguments)
                if _after_refusal:   # parity with the Optimize lane: name the servers that could
                    result = _lead(result, _after_refusal)
                if _down and _sid:
                    try:   # B9: a multi-server agent's server that did not answer is named as such
                        from trusted_data_agent.agent import source_routing as _sr
                        if _sr.active():
                            _sr.note_unreachable(_sid, str(exc)[:240])   # the turn's one record
                            result = _lead(result, _sr.unreachable_text(_sid, tool_name))
                    except Exception:  # noqa: BLE001
                        pass
        # Deterministic dialect retries — parity with the Optimize path's correction
        # strategies: a `… LIMIT n` or misplaced trailing `TOP n` (Error 3706) and a reserved
        # word used as an alias (3707) are fixed mechanically and run once more; anything
        # else falls through to the model exactly as before.
        result, retry_notes = await dialect_retries(tool_name, arguments, result, original, args, kwargs, session_id)
        # The firewall, the learning, the registry, the value-existence note and the budget
        # step — all on the TOOL's output. Then the platform's notes, in a fixed order; a
        # steer is read FIRST (a note appended after a long result was ignored by a small
        # model), then the model-facing size bound on the content half — the artifact is not
        # sent to the model and stays whole.
        vn, bnote = sql_gate_after(tool_name, arguments, result, session_id, user_uuid=user_uuid,
                                   source=source)
        for note in retry_notes + [rewrite_note, vn["note"] if vn else None, bnote]:
            if note:
                result = _annotate(result, note)
        snote = steer_note(session_id, tool_name, user_uuid)
        if snote:
            result = _lead(result, snote)
        if isinstance(result, tuple) and len(result) == 2:
            _final = (bound_result_content(tool_name, result[0]), result[1])
        else:
            _final = bound_result_content(tool_name, result)
        # The ledger's reading of the call, on the value the model will see: an EXECUTION
        # failure (it raised and no deterministic retry recovered it, or the server answered
        # with an error payload) counts toward the breaker; anything else is memoised.
        if _execution_failed(result, _raised):
            _ledger.record_failure(tool_name)
        else:
            _ledger.record_success(tool_name, arguments, _final)
        return _final

    return _scoped


def _execution_failed(result: Any, raised: bool) -> bool:
    """Whether a call's EXECUTION failed — it raised and no deterministic retry recovered it
    (the result still reads ``Error from …``), or the server answered with an error payload.
    A platform refusal (authority, preflight, the breaker itself) returns before this point,
    so it never counts. Never raises."""
    try:
        text = _result_text(result).lstrip()
        if raised and text.startswith("Error from "):
            return True
        d = _coerce_to_dict(result[0] if isinstance(result, tuple) and result else result)
        return bool(isinstance(d, dict) and d.get("status") == "error")
    except Exception:  # noqa: BLE001
        return False
