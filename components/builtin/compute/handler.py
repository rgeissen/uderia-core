"""
Compute with Lineage — the TDA_Compute component handler.

The agent picks WHAT to compute (an op and its parameters); the closed-set core
(`trusted_data_agent.agent.compute`) computes it exactly, in-process, and hands
back a receipt. The rows come from the turn's own result-set registry
(`trusted_data_agent.agent.result_sets`, fed at the tool-result seams) — the
handler never re-queries and the model never pastes rows.

Every receipt is recorded for the turn, so the end-of-turn seam can seal it into
the provenance chain (`compute_step`), stamp it on the persisted turn, trace the
answer's figures back to it, and the numeric-fidelity measurement can count a
receipted figure as grounded.

Deterministic, 0 tokens. Present only when the deployment's compute-lineage
switch is on (`is_profile_relevant`); absent otherwise — byte-identical baseline.
"""
from __future__ import annotations

import html as _html
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

from trusted_data_agent.components.base import (
    BaseComponentHandler,
    ComponentRenderPayload,
    RenderTarget,
)
from trusted_data_agent.agent import compute as _compute
from trusted_data_agent.agent import result_sets as _rs
from trusted_data_agent.agent import compute_sql as _combine

logger = logging.getLogger("quart.app")

_PARAMS = ("column", "by", "agg", "n", "order", "where", "on", "order_by")


class ComputeHandler(BaseComponentHandler):

    @property
    def component_id(self) -> str:
        return "compute"

    @property
    def tool_name(self) -> str:
        return "TDA_Compute"

    @property
    def is_deterministic(self) -> bool:
        return True

    # ── Relevance: the master switch (byte-identical baseline when off) ───────
    def is_profile_relevant(self, profile_config: Dict[str, Any]) -> bool:
        try:
            return _rs.enabled()
        except Exception:  # noqa: BLE001
            return False

    # ── Argument validation ───────────────────────────────────────────────────
    def validate_arguments(self, arguments: Dict[str, Any]) -> Tuple[bool, str]:
        op = str(arguments.get("op") or "").strip().lower()
        if op == "sql" and _combine_enabled():
            if not str(arguments.get("sql") or "").strip():
                return False, "op 'sql' needs `sql`: ONE SELECT over the tables s1, s2, … (this turn's statement numbers)"
            return True, ""
        if op not in _compute.OPS:
            ops = _compute.OPS + (("sql",) if _combine_enabled() else ())
            return False, f"op must be one of: {', '.join(ops)}"
        if op in ("join", "diff") and arguments.get("statement_b") in (None, ""):
            return False, f"op '{op}' needs statement_b (the second query's statement number)"
        if op == "pivot" and not arguments.get("by"):
            return False, "op 'pivot' needs `by` (the column to group on)"
        if op == "filter" and not arguments.get("where"):
            return False, "op 'filter' needs `where` as 'column op value'"
        return True, ""

    # ── Process ───────────────────────────────────────────────────────────────
    async def process(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ComponentRenderPayload:
        session_id = context.get("session_id")
        op = str(arguments.get("op") or "").strip().lower()
        try:
            ok, why = self.validate_arguments(arguments)
            if not ok:
                return self._err("bad_arguments", why)
            if op == "sql":
                return await self._combine(str(arguments.get("sql") or ""), context)
            st = _statement_ref(session_id, arguments.get("statement"))
            if st is None:
                have = _rs.statements(session_id)
                if not have:
                    return self._err("no_result_set",
                                     "No data query has returned rows in this turn yet — run the query first, then compute over its rows.")
                return self._err("no_such_statement",
                                 f"statement {arguments.get('statement')} does not exist this turn; "
                                 f"statements available: {', '.join(str(s['n']) for s in have)} (omit `statement` for the latest).")
            st_b = None
            if op in ("join", "diff"):
                st_b = _statement_ref(session_id, arguments.get("statement_b"))
                if st_b is None:
                    return self._err("no_such_statement", f"statement_b {arguments.get('statement_b')} does not exist this turn")
            params = {k: arguments.get(k) for k in _PARAMS if arguments.get(k) not in (None, "")}
            statements = [st["n"]] + ([st_b["n"]] if st_b else [])
            result = _compute.compute(op, st["rows"], rows_b=(st_b["rows"] if st_b else None),
                                      statements=statements, **params)
        except _compute.ComputeError as e:
            return self._err("compute_refused", str(e))
        except Exception as e:  # never leak a stack as success
            logger.error(f"TDA_Compute failed: {e}", exc_info=True)
            return self._err("compute_error", f"Compute error: {e}")

        receipt = result["receipt"]
        rows_out = result.get("result_rows")
        sample = rows_out if isinstance(rows_out, list) else st["rows"]
        _rs.add_receipt(session_id, receipt, sample_rows=sample)
        lineage = _compute.describe(receipt)
        value = result.get("value")
        title = _row_summary(lineage, value, rows_out)   # the evidence row's summary line
        spec: Dict[str, Any] = {
            "op": op, "value": value, "lineage": lineage, "receipt": receipt,
            "rows": (rows_out[:200] if isinstance(rows_out, list) else None),
            "result_row_count": (len(rows_out) if isinstance(rows_out, list) else None),
        }
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec=spec,
            html=_render_html(op, value, lineage, rows_out, receipt),
            title=title,
            metadata={"tool_name": self.tool_name, "op": op, "receipt": receipt, "lineage": lineage,
                      "value": value, "receipt_hash": receipt.get("receipt_hash")},
            tts_text=(f"{lineage}: {_fmt(value)}" if value is not None else lineage),
        )

    # ── The combine step (op "sql") ───────────────────────────────────────────
    async def _combine(self, sql: str, context: Dict[str, Any]) -> ComponentRenderPayload:
        """ONE read-only SELECT over the turn's result sets (``agent/compute_sql.py``), run through
        the one SQL checkpoint under its logical name, so it gets every protection a query gets:
        authority, loop guard, turn budget, preflight (DuckDB, its own namespace), the registry.
        The tool-result scan is skipped: every input row was scanned when its own query returned."""
        import asyncio
        session_id = context.get("session_id") or ""
        have = {s["n"]: s for s in _rs.statements(session_id)}
        if not have:
            return self._err("no_result_set",
                             "No data query has returned rows in this turn yet — query each source first, "
                             "then combine their results.")
        try:
            sql, phase_map = _combine.resolve_phase_tables(sql, lambda ph: _rs.for_phase(session_id, ph))
            wanted = _combine.referenced_statements(sql)
        except _combine.CombineError as e:
            return self._err(e.code, str(e))
        missing = [n for n in wanted if n not in have]
        if missing:
            return self._err("no_such_statement",
                             f"{', '.join(f's{n}' for n in missing)} did not run this turn; available: "
                             f"{', '.join(f's{n}' for n in sorted(have))}")
        from trusted_data_agent.core.source_endpoints import endpoint_for
        uid = context.get("user_uuid")
        refusal = _combine.isolation_refusal(sql, have, _isolation_rules(context), session_id,
                                             endpoint_of=lambda sid: endpoint_for(sid, uid))
        if refusal is not None:
            return self._err(refusal.code, str(refusal))
        incomplete = [why for n in wanted for ok, why in [_combine.completeness(have[n])] if not ok]
        disclosure = None
        if incomplete:
            if _incomplete_policy() == "refuse":
                return self._err("incomplete_input",
                                 "; ".join(incomplete) + " — a combined figure over part of a source would read "
                                 "as the whole. Narrow that query at its source (filter or aggregate there) so it "
                                 "returns every row it needs, then combine.")
            disclosure = ("Combined over INCOMPLETE input: " + "; ".join(incomplete) +
                          ". Any figure from this result covers only those rows.")

        repaired: Dict[str, Any] = {}

        async def _execute(args: Dict[str, Any]) -> Dict[str, Any]:
            q = args.get("sql") or sql
            try:
                res = await asyncio.to_thread(_combine.run, q, have)
            except _combine.CombineError as e:
                return {"status": "error", "error": str(e), "code": e.code}
            # a SELECT that does not bind as written: parallel results can arrive in any order, so
            # the right results may sit under the wrong numbers. ONE re-assignment that makes every
            # named column resolve is applied and named (0 LLM rounds); otherwise the error says
            # what each table actually is, so the next call is right.
            if res.get("status") == "error" and "Binder" in str(res.get("error") or ""):
                fix = _combine.repair_table_assignment(q, have)
                if fix:
                    # re-pointed at OTHER results: they pass the same checks as the originals did
                    try:
                        new_wanted = _combine.referenced_statements(fix[0])
                    except _combine.CombineError:
                        fix = None
                    else:
                        if set(new_wanted) != set(wanted) and (
                                _combine.isolation_refusal(fix[0], have, _isolation_rules(context), session_id,
                                                           endpoint_of=lambda sid: endpoint_for(sid, uid)) is not None
                                or any(not _combine.completeness(have[n])[0] for n in new_wanted)):
                            fix = None
                if fix:
                    try:
                        again = await asyncio.to_thread(_combine.run, fix[0], have)
                    except _combine.CombineError:
                        again = None
                    if again and again.get("status") != "error":
                        repaired["mapping"] = fix[1]
                        return again
                res["error"] = f"{res.get('error')} — {_combine.tables_note(wanted, have)}"
            return res

        from trusted_data_agent.agent.tool_enforcement import guarded_sql_call
        out = await guarded_sql_call(_combine.DERIVED_TOOL, {"sql": sql}, _execute,
                                     session_id=session_id, user_uuid=context.get("user_uuid"),
                                     dialect=_combine.DIALECT, aliases=frozenset({self.tool_name, self.component_id}),
                                     source_id=_combine.SOURCE_ID, scan_result=False)
        result = out.get("result") or {}
        notes = list(out.get("notes") or [])
        if out.get("outcome") != "success":
            reason = str(result.get("data") or result.get("error") or "the combine did not run")[:900]
            code = result.get("code") or ("refused" if out.get("outcome") == "refused" else "combine_failed")
            return self._err(code, reason + (" " + " ".join(notes) if notes else ""))

        rows_out = [r for r in (result.get("results") or []) if isinstance(r, dict)]
        executed = out.get("executed_sql") or result.get("executed_sql") or sql
        ran = result.get("executed_sql") or executed
        result_n = next((s["n"] for s in reversed(_rs.statements(session_id))
                         if s.get("tool") == _combine.DERIVED_TOOL), None) if rows_out else None
        ran_inputs = list(result.get("inputs") or wanted)       # after a re-point, the inputs that RAN
        receipt = {
            "op": "sql", "sql": ran, "statements": ran_inputs,
            "input_times": {str(n): have[n].get("ts") for n in ran_inputs if n in have},
            "normalizations": [{"left": r["left"], "right": r["right"], "applied": r["applied"]}
                               for r in (result.get("key_reports") or []) if r.get("applied")],
            "row_count": sum(len(have[n].get("rows") or []) for n in ran_inputs if n in have),
            "result_rows": len(rows_out), "columns": list(result.get("columns") or []),
            "truncated": bool(result.get("truncated")), "result_statement": result_n,
        }
        receipt["receipt_hash"] = _compute.receipt_hash(receipt)
        _rs.add_receipt(session_id, receipt, sample_rows=rows_out)
        findings = list(result.get("findings") or [])
        if repaired.get("mapping"):
            findings.insert(0, "the statement named results under the wrong numbers (statement numbers follow the "
                               "order results arrived) — read as " + ", ".join(
                                   f"{old} → {new}" for old, new in sorted(repaired["mapping"].items())))
        if result.get("truncated"):
            findings.append(f"the result holds the first {len(rows_out):,} rows only — aggregate further to see all of it")
        model_notes = ([disclosure] if disclosure else []) + findings + notes
        if phase_map:
            model_notes.insert(0, "plan phase results read as: " +
                               ", ".join(f"{k} = statement {v}" for k, v in sorted(phase_map.items())))
        lineage = _compute.describe(receipt)
        spec: Dict[str, Any] = {
            "op": "sql", "sql": ran, "lineage": lineage, "receipt": receipt,
            "rows": rows_out[:200], "result_row_count": len(rows_out),
            "statement": result_n, "inputs": receipt["statements"],
        }
        masked = _personal_columns(list(result.get("columns") or []), [have[n] for n in wanted])
        if masked:
            spec["masked_columns"] = masked
            model_notes.append(f"personal data is masked on the card ({', '.join(masked)}); the values above are "
                               f"unmasked for you — state only what the answer needs")
        if model_notes:
            spec["notes"] = model_notes
        shown = _masked_rows(rows_out, masked)
        body = _render_html("sql", None, lineage, shown, receipt)
        if model_notes:
            body = "".join(f'<div class="ans-meta">{_html.escape(n)}</div>' for n in model_notes) + body
        body += f'<details class="ans-meta"><summary>Statement</summary><pre>{_html.escape(ran)}</pre></details>'
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec=spec,
            html=body,
            title=_row_summary(lineage, None, rows_out),
            metadata={"tool_name": self.tool_name, "op": "sql", "receipt": receipt, "lineage": lineage,
                      "receipt_hash": receipt.get("receipt_hash"), "statement": result_n},
            tts_text=lineage,
        )

    def _err(self, code: str, message: str) -> ComponentRenderPayload:
        # spec.error is the adapter's failed-call contract (a refusal must not read as
        # success on the Optimize path); suppress_inline keeps the refusal headless — the
        # message still reaches the model via spec/metadata.
        return ComponentRenderPayload(
            component_id=self.component_id,
            render_target=RenderTarget.INLINE,
            spec={"error": message},
            html="",
            metadata={"tool_name": self.tool_name, "error_code": code, "error": message, "suppress_inline": True},
        )


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{v:,}" if float(v).is_integer() else f"{v:,.6g}"
    return str(v)


def _render_html(op: str, value: Any, lineage: str, rows_out: Optional[List[Dict[str, Any]]],
                 receipt: Optional[Dict[str, Any]] = None) -> str:
    """The BODY of the receipt's evidence row — the row itself (kind word, summary, provenance
    chip, disclosure) is drawn by the one component seam (`components/utils`) from this
    component's manifest, like every other component's. The body keeps everything on
    demand: the receipt line, the rows-used caveat, the hash, the way to the rows — or, for
    a row op, the result table, which IS the evidence."""
    rc = receipt or {}
    used, total = rc.get("row_count_used"), rc.get("row_count")
    rhash = str(rc.get("receipt_hash") or "")
    caveat = (f' <span class="ans-chip" title="rows whose value could not be read as a number are not part of the aggregate">'
              f'{used:,} of {total:,} rows used</span>'
              if isinstance(used, int) and isinstance(total, int) and used < total else "")
    receipt_chip = f' <span class="ans-chip">receipt {_html.escape(rhash[:8])}</span>' if rhash else ""
    lead = _html.escape(lineage)
    if rows_out is None:
        return (f'<div class="ans-meta">Computed by code, not by the model: <strong>{_html.escape(_fmt(value))}</strong> — {lead}{caveat}{receipt_chip}</div>'
                f'<button type="button" class="ans-act compute-open-btn" data-act="rows" data-receipt-hash="{_html.escape(rhash)}">View the rows it was computed over '
                f'<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg></button>')
    rows = [r for r in rows_out if isinstance(r, dict)][:20]
    cols = _compute.columns_of(rows)
    thead = "".join(f'<th>{_html.escape(str(c))}</th>' for c in cols)
    trs = "".join("<tr>" + "".join(f'<td>{_html.escape(_fmt(r.get(c)))}</td>' for c in cols) + "</tr>" for r in rows)
    more = (f'<div class="ans-meta">showing {len(rows)} of {len(rows_out):,} rows</div>' if len(rows_out) > len(rows) else "")
    return (f'<div class="ans-meta">Computed by code, not by the model: {lead}{caveat}{receipt_chip}</div>'
            f'<div class="ans-tw"><table class="ans-t"><thead><tr>{thead}</tr></thead><tbody>{trs}</tbody></table></div>{more}')


def _row_summary(lineage: str, value: Any, rows_out: Optional[List[Dict[str, Any]]]) -> str:
    """What the collapsed row says: the derivation and the figure (or the row count) on the line."""
    if rows_out is None:
        return f"{lineage} → {_fmt(value)}"
    return f"{lineage} → {len(rows_out):,} row{'' if len(rows_out) == 1 else 's'}"


def _combine_enabled() -> bool:
    """The combine op is served exactly where its instruction gate is open (one rule)."""
    try:
        from trusted_data_agent.components.manager import ComponentManager
        return bool(ComponentManager._instruction_gate_open("compute_sql"))
    except Exception:  # noqa: BLE001
        return False


def _incomplete_policy() -> str:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return "disclose" if getattr(APP_CONFIG, "COMPUTE_SQL_INCOMPLETE", "refuse") == "disclose" else "refuse"
    except Exception:  # noqa: BLE001
        return "refuse"


def _personal_columns(columns: List[str], inputs: List[Dict[str, Any]]) -> List[str]:
    """PROP-006 A12 — the combine card masks personal data exactly as the Database Navigator's
    card does (same classifier, same mask): a column whose own name reads as personal data, or a
    column carried from an input whose statement read a table about people. Card only: the model
    reads the values it needs; what reaches the user is the outbound guardrail's."""
    try:
        from trusted_data_agent.dbcatalog.intelligence import _pii_reason
    except Exception:  # noqa: BLE001
        return []
    tables: List[str] = []
    for st in inputs:
        try:
            import sqlglot
            from sqlglot import exp
            for t in sqlglot.parse_one(str(st.get("statement") or ""), error_level=None).find_all(exp.Table):
                if t.name:
                    tables.append(".".join(p for p in (t.db, t.name) if p))
        except Exception:  # noqa: BLE001
            continue
    out = []
    for c in columns:
        try:
            if _pii_reason(str(c)) or any(_pii_reason(str(c), "", tb) for tb in tables):
                out.append(c)
        except Exception:  # noqa: BLE001
            continue
    return out


def _masked_rows(rows: List[Dict[str, Any]], masked: List[str]) -> List[Dict[str, Any]]:
    if not masked:
        return rows
    try:
        from trusted_data_agent.dbcatalog.service import mask_value
    except Exception:  # noqa: BLE001 — cannot mask ⇒ show nothing of those columns
        return [{k: ("•••" if k in masked else v) for k, v in r.items()} for r in rows]
    return [{k: (mask_value(v) if k in masked else v) for k, v in r.items()} for r in rows]


def _isolation_rules(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Deployment rules first (the admin's — an agent cannot loosen them), then the agent's own
    (`combineIsolation` on its profile — it can only ADD). Both are policy, never a model decision."""
    rules: List[Dict[str, Any]] = []
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        rules += _combine.normalize_rules(getattr(APP_CONFIG, "COMBINE_ISOLATION", []), "deployment administrator")
    except Exception:  # noqa: BLE001
        pass
    try:
        pid, uid = context.get("profile_id"), context.get("user_uuid")
        if pid:
            from trusted_data_agent.core.config_manager import get_config_manager
            prof = get_config_manager().get_profile(pid, uid) or {}
            rules += _combine.normalize_rules(prof.get("combineIsolation"), "agent's owner")
    except Exception:  # noqa: BLE001
        pass
    return rules


def _statement_ref(session_id: Optional[str], ref: Any):
    """A statement named by number (today's form) or — with the combine switch on — by the plan
    phase that produced it (``p2``): an Optimize plan is written before anything runs, and parallel
    phases finish in any order, so its numbers cannot be known in advance (O1)."""
    if isinstance(ref, str) and _combine_enabled():
        m = re.match(r"^\s*p(?:hase)?[ _]?(\d+)\s*$", ref, re.IGNORECASE)
        if m:
            return _rs.for_phase(session_id, int(m.group(1)))
    return _rs.get(session_id, ref)
