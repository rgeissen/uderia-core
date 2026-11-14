"""Seam glue for the preflight guard: mode resolution, catalog recording, block decision.
Kept separate from the pure catalog logic so the MCP-adapter hook stays a one-liner."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from trusted_data_agent.agent.preflight import catalog as _catalog
from trusted_data_agent.agent.preflight import dialect as _dialect
from trusted_data_agent.core import posture_modes as _modes
from trusted_data_agent.agent import assurance_tally as _tally
from trusted_data_agent.core.tool_identity import base_name

app_logger = logging.getLogger("quart.app")
# The preflight guard has no ``annotate`` step — it observes or blocks. Single source:
# core.posture_modes (``PREFLIGHT_LADDER``).
_VALID_MODES = _modes.PREFLIGHT_LADDER


def resolve_mode() -> str:
    """Global preflight mode from APP_CONFIG.PREFLIGHT_SQL_MODE (default 'observe').

    A mode outside this ladder clamps to the strongest supported mode that does not exceed
    it (``annotate`` → ``observe``, ``review`` → ``enforce``)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return _modes.normalize(
            getattr(APP_CONFIG, "PREFLIGHT_SQL_MODE", "observe") or "observe",
            allowed=_VALID_MODES, context="APP_CONFIG.PREFLIGHT_SQL_MODE")
    except Exception:
        return "observe"


def block_result(bad_dbs: List[str]) -> Dict[str, Any]:
    """A recovery-shaped structured error. The message contains ``Database 'X' does not exist``
    verbatim so the Optimize ``DatabaseNotFoundStrategy`` (Error-3802 handler) engages, and a
    ReAct agent sees a fact-grounded, non-blind reason."""
    db = bad_dbs[0] if bad_dbs else "the specified database"
    joined = ", ".join(bad_dbs)
    return {
        "status": "error",
        "preflight": True,
        "error": f"Database '{db}' does not exist",
        "data": (f"[Preflight] Database '{db}' does not exist — referenced database(s) not in "
                 f"the catalog: {joined}. The query was NOT executed. Discover the correct "
                 f"database/table before retrying."),
    }


def maybe_record_catalog(tool_name: str, arguments: Dict[str, Any], result: Any, session_id: str) -> None:
    """After a ``base_databaseList(scope=all)`` success, cache the authoritative catalog. Only a
    complete (scope=all) list is recorded — a partial list would create false positives. Never
    raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        if base_name(tool_name) != "base_databaseList" or not session_id:
            return
        if str((arguments or {}).get("scope", "")).lower() != "all":
            return
        if isinstance(result, dict) and result.get("status") == "success":
            n = _catalog.record_catalog(session_id, result)
            if n:
                app_logger.debug(f"[preflight] cached authoritative catalog: {n} databases (session {session_id})")
    except Exception:
        pass  # catalog caching must never affect a turn


def resolve_join_mode() -> str:
    """Global join-key mode from APP_CONFIG.PREFLIGHT_JOIN_MODE (default 'observe'); same ladder
    as the schema check (off / observe / enforce)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return _modes.normalize(
            getattr(APP_CONFIG, "PREFLIGHT_JOIN_MODE", "observe") or "observe",
            allowed=_VALID_MODES, context="APP_CONFIG.PREFLIGHT_JOIN_MODE")
    except Exception:
        return "observe"


def maybe_record_join_keys(session_id: str, kg_enrichment_event: Any) -> None:
    """Record the declared join keys the KG enrichment carries for this session (the
    ``join_keys`` metadata `_declared_join_pairs` emits) and, beside them, the model's
    domain partition + class bridges (``subject_areas`` / ``class_bridges``). Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        from trusted_data_agent.agent.preflight import joins as _joins
        n = _joins.record_from_event(session_id, kg_enrichment_event)
        if n:
            app_logger.debug(f"[preflight] declared join keys known: {n} pair(s) (session {session_id})")
    except Exception:
        pass


def check_join_keys(sql: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Preflight the JOINs of a raw-SQL call against the session's declared keys. Returns a
    block result (mode ``enforce`` + a provable off-key join) or None. Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        mode = resolve_join_mode()
        if mode == "off":
            return None
        from trusted_data_agent.agent.preflight import joins as _joins
        viol = _joins.join_violations(session_id, sql)
        if viol:
            v = viol[0]
            app_logger.warning(
                f"[preflight:join] JOIN {v['tables'][0]}⇄{v['tables'][1]} not on declared key "
                f"{v['key']} (used {v['used']}, missing {v['missing']}) — mode={mode}")
            if mode == "enforce":
                return _joins.block_result(viol)
            return None
        # Two tables joined to EACH OTHER on a key both hold as a FOREIGN key to a third
        # table — fact-to-fact through a dimension key (prod 2026-09-19: a member's miles
        # redeemed reported 149× too large).
        fviol = _joins.fanout_violations(session_id, sql)
        if fviol:
            v = fviol[0]
            app_logger.warning(
                f"[preflight:join] fan-out JOIN {v['tables'][0]}⇄{v['tables'][1]} on {v['column']} "
                f"(both foreign keys to {v.get('owner') or '?'}) — mode={mode}")
            if mode == "enforce":
                return _joins.fanout_block_result(fviol)
            return None
        # A join ACROSS the model's subject areas with no declared key: the instance link
        # a class-level correspondence forbids (the core-model bridge class, 2026-09-15).
        xviol = _joins.cross_domain_violations(session_id, sql)
        if not xviol:
            return None
        v = xviol[0]
        app_logger.warning(
            f"[preflight:join] cross-domain JOIN {v['tables'][0]} ({v['areas'][0]}) ⇄ "
            f"{v['tables'][1]} ({v['areas'][1]}) with no declared key"
            f"{' — bridge ' + repr(v['bridge']) if v.get('bridge') else ''} — mode={mode}")
        if mode == "enforce":
            return _joins.cross_domain_block_result(xviol)
        return None
    except Exception:
        return None


def resolve_text_date_mode() -> str:
    """Global text-date mode from APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE (default 'observe');
    same ladder as the schema check (off / observe / enforce)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return _modes.normalize(
            getattr(APP_CONFIG, "PREFLIGHT_TEXT_DATE_MODE", "observe") or "observe",
            allowed=_VALID_MODES, context="APP_CONFIG.PREFLIGHT_TEXT_DATE_MODE")
    except Exception:
        return "observe"


def maybe_record_text_dates(session_id: str, kg_enrichment_event: Any) -> None:
    """Record the text-stored date columns the KG enrichment names for this session (the
    ``text_date_columns`` metadata `_text_date_columns` emits). Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        from trusted_data_agent.agent.preflight import textdates as _td
        n = _td.record_from_event(session_id, kg_enrichment_event)
        if n:
            app_logger.debug(f"[preflight] text-stored date columns known: {n} (session {session_id})")
    except Exception:
        pass


def maybe_learn_date_shapes(tool_name: str, arguments: Dict[str, Any], result: Any, session_id: str) -> None:
    """After ANY tool result carrying rows: learn the stored shape of a recorded text-date
    column from its values (the fact the ontology cannot carry). Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        if not session_id or not isinstance(result, dict):
            return
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return
        from trusted_data_agent.agent.preflight import textdates as _td
        for leaf, col, fmt in _td.learn_from_result(session_id, sql, result):
            app_logger.info(f"[preflight:textdate] learned shape of {leaf}.{col}: FORMAT '{fmt}' (session {session_id})")
    except Exception:
        pass


def check_text_dates(sql: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Preflight a raw-SQL call's use of text-stored date columns. Returns a block result
    (mode ``enforce`` + a proven or unverified lexical use) or None. Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        mode = resolve_text_date_mode()
        if mode == "off":
            return None
        from trusted_data_agent.agent.preflight import textdates as _td
        proven, unverified = _td.text_date_violations(session_id, sql)
        if not proven and not unverified:
            return None
        lead = (proven or unverified)[0]
        app_logger.warning(
            f"[preflight:textdate] {lead['column']} on {lead['table']} ({lead.get('type')}) used as "
            f"{lead['kind']} in `{lead['snippet']}` — {'PROVEN' if proven else 'unverified'} "
            f"(shape {lead.get('shape') or 'unknown'}), mode={mode}")
        if mode == "enforce":
            _tally.record("preflight", mode=mode, blocked_count=1,
                          text_date_columns=[f"{lead['table']}.{lead['column']}"])
            return _td.block_result(proven, unverified)
        _tally.record("preflight", mode=mode, flagged_count=1,
                      text_date_columns=[f"{lead['table']}.{lead['column']}"])
        return None
    except Exception:
        return None


def resolve_value_mode() -> str:
    """Global value-existence mode from APP_CONFIG.PREFLIGHT_VALUE_MODE (default 'observe');
    same ladder as its siblings (off / observe / enforce)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return _modes.normalize(
            getattr(APP_CONFIG, "PREFLIGHT_VALUE_MODE", "observe") or "observe",
            allowed=_VALID_MODES, context="APP_CONFIG.PREFLIGHT_VALUE_MODE")
    except Exception:
        return "observe"


def maybe_record_value_domains(session_id: str, kg_enrichment_event: Any) -> None:
    """Record the declared value domains the KG enrichment names for this session (the
    ``value_domains`` metadata `_value_domains` emits — a coded column's codes, a label
    column's labels). Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        from trusted_data_agent.agent.preflight import values as _vals
        n = _vals.record_from_event(session_id, kg_enrichment_event)
        if n:
            app_logger.debug(f"[preflight] declared value domains known: {n} column(s) (session {session_id})")
    except Exception:
        pass


def maybe_learn_values(tool_name: str, arguments: Dict[str, Any], result: Any, session_id: str) -> None:
    """After ANY tool result carrying rows: every value the rows carry is an OBSERVED value
    of its column for this session — the data vouching for itself. Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        if not session_id or not isinstance(result, dict):
            return
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str):
            return
        from trusted_data_agent.agent.preflight import values as _vals
        _vals.learn_from_result(session_id, sql, result)
    except Exception:
        pass


def empty_result_note(tool_name: str, arguments: Dict[str, Any], result: Any, session_id: str) -> Optional[Dict[str, Any]]:
    """After a raw-SQL call that returned NOTHING: when its filter rests on a literal
    nothing established occurs in that column (no declared domain, never seen in a row),
    return ``{"note": <recovery text>, "unverified": [...]}`` — the caller stamps it on
    the result under ``enforce``; under ``observe`` it is logged only. None otherwise.
    Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        mode = resolve_value_mode()
        if mode == "off" or not session_id:
            return None
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return None
        from trusted_data_agent.agent.preflight import values as _vals
        if not _vals.is_empty_result(result):
            return None
        unverified = _vals.unverified_literals(session_id, sql)
        if not unverified:
            return None
        lead = unverified[0]
        app_logger.warning(
            f"[preflight:value] empty result on unverified literal {lead['leaf']}.{lead['column']} = "
            f"'{lead['literal']}' — mode={mode}")
        if mode == "enforce":
            _tally.record("preflight", mode=mode, flagged_count=1,
                          unverified_values=[f"{u['leaf']}.{u['column']}='{u['literal']}'" for u in unverified])
            return {"note": _vals.empty_result_note(unverified), "unverified": unverified}
        _tally.record("preflight", mode=mode, flagged_count=1,
                      unverified_values=[f"{u['leaf']}.{u['column']}='{u['literal']}'" for u in unverified])
        return None
    except Exception:
        return None


def resolve_decimal_mode() -> str:
    """Global decimal-division mode from APP_CONFIG.PREFLIGHT_DECIMAL_MODE (default 'observe');
    same ladder as its siblings — here ``enforce`` attaches a note, it never blocks."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return _modes.normalize(
            getattr(APP_CONFIG, "PREFLIGHT_DECIMAL_MODE", "observe") or "observe",
            allowed=_VALID_MODES, context="APP_CONFIG.PREFLIGHT_DECIMAL_MODE")
    except Exception:
        return "observe"


def decimal_division_note(tool_name: str, arguments: Dict[str, Any], result: Any) -> Optional[str]:
    """After a Teradata statement RAN: a division its decimal literal truncates to one or two
    places (``agent/preflight/decimals.py``). Returns the note under ``enforce``; logs and tallies
    under ``observe``; None otherwise. A statement that failed carries no figure to warn about.
    Never raises."""
    try:
        mode = resolve_decimal_mode()
        if mode == "off" or not _dialect.is_teradata():
            return None
        sql = (arguments or {}).get("sql")
        if not isinstance(sql, str) or "/" not in sql:
            return None
        if isinstance(result, dict) and (result.get("status") == "error" or result.get("success") is False):
            return None
        from trusted_data_agent.agent.preflight import decimals as _dec
        found = _dec.truncating_divisions(sql)
        if not found:
            return None
        app_logger.warning(f"[preflight:decimal] {tool_name}: `{found[0]['expr']}` truncates to "
                           f"{found[0]['places']} place(s) — mode={mode}")
        _tally.record("preflight", mode=mode, flagged_count=1,
                      decimal_divisions=[f["expr"] for f in found])
        return _dec.note(found) if mode == "enforce" else None
    except Exception:
        return None


def check_values(sql: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Preflight a raw-SQL call's equality literals against the session's declared value
    domains. Returns a block result (mode ``enforce`` + a PROVEN absent literal) or None.
    Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        mode = resolve_value_mode()
        if mode == "off":
            return None
        from trusted_data_agent.agent.preflight import values as _vals
        findings = _vals.value_violations(session_id, sql)
        if not findings:
            return None
        lead = findings[0]
        app_logger.warning(
            f"[preflight:value] {lead['column']} on {lead['table']} filtered to '{lead['literal']}', "
            f"which its declared {lead['kind']} domain does not contain — PROVEN, mode={mode}")
        keys = [f"{f['table']}.{f['column']}='{f['literal']}'" for f in findings]
        if mode == "enforce":
            _tally.record("preflight", mode=mode, blocked_count=1, absent_values=keys)
            return _vals.block_result(findings)
        _tally.record("preflight", mode=mode, flagged_count=1, absent_values=keys)
        return None
    except Exception:
        return None


def resolve_column_mode() -> str:
    """Global column-existence mode from APP_CONFIG.PREFLIGHT_COLUMN_MODE (default 'observe');
    same ladder as the schema check (off / observe / enforce)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return _modes.normalize(
            getattr(APP_CONFIG, "PREFLIGHT_COLUMN_MODE", "observe") or "observe",
            allowed=_VALID_MODES, context="APP_CONFIG.PREFLIGHT_COLUMN_MODE")
    except Exception:
        return "observe"


def maybe_record_columns(session_id: str, kg_enrichment_event: Any) -> None:
    """Record the complete column lists the KG enrichment carries for this session (the
    ``table_columns_full`` metadata, emitted only for a column-complete graph). Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        from trusted_data_agent.agent.preflight import columns as _columns
        n = _columns.record_from_event(session_id, kg_enrichment_event)
        if n:
            app_logger.debug(f"[preflight] complete column lists known: {n} table(s) (session {session_id})")
    except Exception:
        pass


def check_columns(sql: str, session_id: str) -> Optional[Dict[str, Any]]:
    """Preflight the column references of a raw-SQL call against the session's complete
    column lists. Returns a block result (mode ``enforce`` + a provably-nonexistent column)
    or None. Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        mode = resolve_column_mode()
        if mode == "off":
            return None
        from trusted_data_agent.agent.preflight import columns as _columns
        viol = _columns.column_violations(session_id, sql)
        if not viol:
            return None
        v = viol[0]
        app_logger.warning(
            f"[preflight:column] '{v['table']}.{v['column']}' does not exist ({v['real_count']} real columns"
            f"{'; closest ' + ', '.join(v['suggestions'][:3]) if v.get('suggestions') else ''}) — mode={mode}")
        if mode == "enforce":
            _tally.record("preflight", mode=mode, checked_count=1, blocked_count=1,
                          missing_columns=[f"{x['table']}.{x['column']}" for x in viol])
            return _columns.block_result(viol)
        _tally.record("preflight", mode=mode, checked_count=1, flagged_count=1,
                      missing_columns=[f"{x['table']}.{x['column']}" for x in viol])
        return None
    except Exception:
        return None


def check_sql(tool_name: str, arguments: Dict[str, Any], session_id: str) -> Optional[Dict[str, Any]]:
    """Preflight a raw-SQL tool call. Returns a block result to SHORT-CIRCUIT execution (mode
    ``enforce`` + a provably-nonexistent database reference); returns None to proceed normally
    (observe / abstain / off / no catalog / fail-open). Never raises."""
    session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
    try:
        mode = resolve_mode()
        if mode == "off":
            return None
        sql = (arguments or {}).get("sql")
        if not (isinstance(sql, str) and sql.strip() and session_id):
            return None
        # The guard reached a real SQL call — record that BEFORE knowing the outcome,
        # so "ran and every database checked out" is distinguishable from "never ran".
        _tally.mark_ran("preflight")
        _jb = check_join_keys(sql, session_id)
        if _jb is not None:
            return _jb
        _tb = check_text_dates(sql, session_id)
        if _tb is not None:
            return _tb
        _vb = check_values(sql, session_id)
        if _vb is not None:
            return _vb
        _cb = check_columns(sql, session_id)
        if _cb is not None:
            return _cb
        bad = _catalog.nonexistent_db_refs(session_id, sql)
        if not bad:
            _tally.record("preflight", mode=mode, checked_count=1)
            return None
        if mode == "enforce":
            app_logger.warning(
                f"[preflight] ENFORCE: '{tool_name}' references unknown database(s) {bad} — blocked pre-execution")
            _tally.record("preflight", mode=mode, checked_count=1,
                          blocked_count=1, unknown_dbs=list(bad))
            return block_result(bad)
        app_logger.info(
            f"[preflight] OBSERVE: '{tool_name}' references unknown database(s) {bad} — executing anyway")
        _tally.record("preflight", mode=mode, checked_count=1,
                      flagged_count=1, unknown_dbs=list(bad))
        return None
    except Exception:
        return None  # fail open — never break a turn


def record_source_dictionary(session_id: str, *, databases: Any = None,
                             table: Optional[str] = None, columns: Any = None) -> Dict[str, int]:
    """Arm the checks from a direct source's OWN dictionary — the facts an ontology would
    otherwise supply. Called by the Database Navigator inside ``dialect.using(dialect, source)``:
    ``databases`` is the source's complete schema list (the database-name check), ``table`` +
    ``columns`` one table's complete column list from ``describe`` (the column check).

    Non-Teradata sources only: a Teradata call shares the ontology's facts (dialect.py), and a
    dictionary list must never overwrite what the ontology recorded. Never raises."""
    out = {"databases": 0, "tables": 0}
    try:
        if _dialect.is_teradata():
            return out
        session_id = _dialect.ns(session_id)   # the facts of THIS call's source (preflight/dialect.py)
        if not session_id:
            return out
        if databases:
            out["databases"] = _catalog.record_names(session_id, databases)
        if table and columns:
            from trusted_data_agent.agent.preflight import columns as _columns
            out["tables"] = _columns.record_columns(session_id, {str(table): list(columns)}, origin="dictionary")
    except Exception:  # noqa: BLE001
        pass
    return out

