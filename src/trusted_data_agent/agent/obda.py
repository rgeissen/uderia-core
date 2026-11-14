"""
OBDA-READ — Ontology-Based Data **Operations** (the observable face of OBDA-READ).

Pure, deterministic classification of a turn's data-touching operations into two lanes:

* **governed** — a concept consumed through the deterministic data-product path (today: a
  ``compile_metric`` operation — the metric compiler resolves the deployed target and emits
  ratio-safe SQL with **no judgement in the loop**).
* **free-formed** — the agent wrote raw SQL itself (a data-source tool call carrying a ``sql``
  argument). Advisory, judgement-in-the-loop, not a governed data product.

This one module is the **single source of truth** for the OBDA-READ signal, consumed by two ends:

* **observability** — the executor scans the turn trace and emits an ``obda`` Live Status card
  so an operator can *see* which figures came from the deterministic lane;
* **evaluation** — the ``governed_data_ratio`` scorer reads the same trace and bands the ratio,
  creating an incentive gradient toward the governed lane.

Deterministic, zero-LLM. See docs/roadmap/OBDA_VISION.md §5.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

# The knowledge-graph component tool through which governed metric operations flow.
_KG_TOOL = "TDA_KnowledgeGraph"
# The data-product face: the OBDA-READ component's own tool, and the knowledge-graph tool's
# alias of the same actions (one lane, two doors — core/obda_planes).
_READ_TOOLS = frozenset({"TDA_DataProduct", _KG_TOOL})
# Governed (deterministic) data-product operations. Phase 0 shipped compile;
# Phase B4 adds the data-product surface's query compile.
_GOVERNED_ACTIONS = {"compile_metric", "query_product"}


def _is_document_query(arguments: Optional[Dict[str, Any]]) -> bool:
    """An agent-written document-store query (MongoDB: a collection and a pipeline) — the
    same free-formed data operation as raw SQL, in another language."""
    args = arguments or {}
    coll = args.get("collection")
    return isinstance(coll, str) and coll.strip() != "" and args.get("pipeline") is not None


def _is_raw_sql(arguments: Optional[Dict[str, Any]]) -> bool:
    """A free-formed data operation: the agent supplied the query itself — raw SQL, or a
    document-store pipeline."""
    sql = (arguments or {}).get("sql")
    return (isinstance(sql, str) and sql.strip() != "") or _is_document_query(arguments)


def is_derived_call(tool_name: Any, arguments: Optional[Dict[str, Any]] = None) -> bool:
    """A call whose SQL reads only rows this turn's queries already returned — the combine step
    (``TDA_Compute`` op "sql", run at the checkpoint as ``compute_sql``). It reaches no data
    source, so it is neither the governed nor the free-formed lane: counting it free-formed would
    lower ``governed_data_ratio`` and raise a surface breach for a transformation of rows that
    already passed the checkpoint. Declared in ``agent.compute_sql``; read here."""
    try:
        from trusted_data_agent.agent.compute_sql import DERIVED_TOOL
    except Exception:  # noqa: BLE001
        DERIVED_TOOL = "compute_sql"
    t = str(tool_name or "")
    if t == DERIVED_TOOL:
        return True
    return t == "TDA_Compute" and str((arguments or {}).get("op") or "").strip().lower() == "sql"


def is_data_retrieval(call: Dict[str, Any]) -> bool:
    """THE predicate for "this call retrieved data with an agent-written query" — used by the
    lane scanner and the honesty gate alike, so the two can never classify a call differently."""
    if not isinstance(call, dict):
        return False
    args = call.get("arguments")
    return _is_raw_sql(args) and not is_derived_call(call.get("tool_name"), args)


def _governed_op(call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """If this tool call is a governed data-product operation, describe it; else None."""
    tool = call.get("tool_name") or ""
    if tool not in _READ_TOOLS:
        return None
    args = call.get("arguments") or {}
    action = args.get("action")
    if action not in _GOVERNED_ACTIONS:
        return None
    if action == "compile_metric" and tool != _KG_TOOL:
        return None
    # Best-effort detail from the result payload (realization / deployed target); the card
    # degrades gracefully when it is absent.
    meta: Dict[str, Any] = {}
    result = call.get("result")
    if isinstance(result, dict):
        meta = result.get("metadata") or result.get("spec") or {}
        if not isinstance(meta, dict):
            meta = {}
    op = {
        "op": action,
        "metric": args.get("metric_name") or meta.get("metric_name") or meta.get("metric"),
        "realization": meta.get("realization"),
        "deployed": (meta.get("deployed_view") or meta.get("deployed_column")
                     or meta.get("source_table")),
    }
    # O2 — a query_product compile carries the CONSUMPTION RECEIPT fields the EPC
    # step + the answer capsule render: product identity, request hash, the trust
    # state at consumption time, and the compiled statement's hash. A REJECTed
    # compile (error in metadata, no sql) carries none — nothing was consumed.
    if action == "query_product" and meta.get("sql") and not meta.get("error"):
        sql_norm = " ".join(str(meta.get("sql")).split())
        op.update({
            "product": meta.get("product"),
            "version": meta.get("version"),
            "request_hash": meta.get("request_hash"),
            "trust_state": meta.get("trust_state") or meta.get("trust"),
            "sql_hash": hashlib.sha256(sql_norm.encode("utf-8")).hexdigest(),
        })
    return op


def profile_obda(profile) -> tuple:
    """THE per-agent OBDA-READ mode — ONE rule, read by every surface (operator model,
    confirmed 2026-08-17): the enrichment render, the subgraph filter, the
    query_product/list_products grant, the governed_execution planner pass, the
    lane scan, and the champion filter. Gating each consumer separately is how
    three leaks shipped (session a52f10d2).

    Returns ``(mode, products)``:
      * ``("off", frozenset())`` — Option A. The DEFAULT for a profile that
        never chose (dataSurface absent/null), and always the answer when the
        deployment master is off. No products visible to the planner, both
        agent actions refuse, no OBDA-READ planner behaviour, no lane scan.
      * ``("exclusive", products)`` — Option B (Surface Full): only the listed
        products; raw SQL denied by Agent Authority as before. The ``advisory``
        dev sub-toggle stays on the stored dataSurface (enforcement-side only).
      * ``("mixed", products)`` — Option C: the listed products AND the regular
        KG paths — the LLM decides. THE LIST IS THE GRANT here too:
        query_product refuses products not on it (operator decision #3).

    Pure and never raises; a malformed block reads as Option A (fail toward
    invisible — the additive baseline)."""
    try:
        from trusted_data_agent.core import obda_planes as _planes
        if not _planes.plane_enabled("read", profile if isinstance(profile, dict) else None):
            return "off", frozenset()
    except Exception:  # noqa: BLE001
        return "off", frozenset()
    try:
        cfg = (profile or {}).get("authorityConfig")
        if not isinstance(cfg, dict):
            cfg = (profile or {}).get("identityConfig")   # legacy alias
        ds = (cfg or {}).get("dataSurface")
        if not isinstance(ds, dict):
            return "off", frozenset()
        prods = ds.get("products")
        if not isinstance(prods, (list, tuple, set)) or not prods:
            return "off", frozenset()
        products = frozenset(str(p) for p in prods)
        return ("exclusive" if ds.get("exclusive") else "mixed"), products
    except Exception:  # noqa: BLE001
        return "off", frozenset()


def scan_obda(tool_calls: List[Dict[str, Any]],
              action_records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Classify a turn's tool calls into the governed vs free-formed data lanes.

    ``tool_calls`` is the canonical ``[{tool_name, arguments, result}, ...]`` shape (as produced
    by ``eval.types.RunRecord.tool_calls`` or built from ``turn_action_history``). Pure — returns
    a summary; never raises on malformed input.
    """
    governed: List[Dict[str, Any]] = []
    freeform = 0
    denied = 0
    rejected = 0
    # Pass 1 — collect the SQL text every governed compile PRODUCED this turn.
    # Executing a compiled governed statement through the agent's own data tool
    # IS the governed lane (the component compiles, never executes — the
    # probe_bridge doctrine — so the paired execution call must not be
    # mis-counted as free-forming).
    governed_sql: set = set()
    for c in tool_calls or []:
        if not isinstance(c, dict):
            continue
        if _governed_op(c):
            result = c.get("result")
            # a REJECTed compile produced no statement — anything riding its
            # result must not allowlist a later raw execution as "governed"
            if isinstance(result, dict) and (result.get("status") == "error"
                                             or result.get("error")):
                continue
            meta = (result.get("metadata") if isinstance(result, dict) else None) or {}
            for src in (meta, result if isinstance(result, dict) else {}):
                sql = src.get("sql") if isinstance(src, dict) else None
                if isinstance(sql, str) and sql.strip():
                    governed_sql.add(" ".join(sql.split()))
    _op_by_sql: Dict[str, Dict[str, Any]] = {}   # norm(sql) → the compile op (O2 receipt)
    for c in tool_calls or []:
        if not isinstance(c, dict):
            continue
        g = _governed_op(c)
        if g:
            # A REJECTed request is NOT a data operation that ran — it is the
            # contract refusing one. Counting it governed read "3 governed data
            # operations" over a turn where one of the three moved no data at
            # all (operator finding, session 2fcbb51f: an M4 allowlist REJECT
            # counted beside the real compile+execute pair). Same rule as the
            # surface's denied_count: reported, never folded into a lane.
            _res_g = c.get("result")
            _is_reject = bool(isinstance(_res_g, dict)
                              and (_res_g.get("status") == "error" or _res_g.get("error")))
            if _is_reject:
                rejected += 1
                continue
            governed.append(g)
            if g.get("product"):
                _res = c.get("result")
                _meta = (_res.get("metadata") if isinstance(_res, dict) else None) or {}
                _sql = _meta.get("sql")
                if isinstance(_sql, str) and _sql.strip():
                    _op_by_sql[" ".join(_sql.split()).rstrip(";")] = g
        elif is_data_retrieval(c):
            # A call the authority layer DENIED never executed — it is neither lane.
            # Counting it free-formed would read a surface-only agent's confinement
            # working (3 attempts, 3 denials, 0 rows) as "3 free-formed operations"
            # and let governed_data_ratio punish the agent for being confined.
            _res = c.get("result")
            _meta = (_res.get("metadata") if isinstance(_res, dict) else None) or {}
            if isinstance(_meta, dict) and _meta.get("agent_denied"):
                denied += 1
                continue
            raw = " ".join(str((c.get("arguments") or {}).get("sql", "")).split())
            if raw and (raw in governed_sql or raw.rstrip(";") in
                        {s.rstrip(";") for s in governed_sql}):
                governed.append({"op": "execute_governed",
                                 "tool": c.get("tool_name")})
                # O2 — the paired execution supplies the receipt's row_count: the
                # compile op has product identity, the execution has the rows.
                _cop = _op_by_sql.get(raw.rstrip(";"))
                if _cop is not None and "row_count" not in _cop:
                    _res = c.get("result")
                    _rows = _res.get("results") if isinstance(_res, dict) else None
                    if isinstance(_rows, list):
                        _cop["row_count"] = len(_rows)
                    elif isinstance(_res, dict) and isinstance(_res.get("result_rows"), int):
                        # the conversation lane's event carries the count, not the rows
                        _cop["row_count"] = int(_res["result_rows"])
            else:
                freeform += 1
    total = len(governed) + freeform
    # OBDA-ACT (PROP-008 §5.4) — the third lane: signed actions this turn PERFORMED (wrote) and
    # REFUSED (the compiler, a check, or the transaction said no). Read from the turn's
    # action record, never from tool calls: an action is performed by the action face in
    # one transaction, not through a data tool. Observed / parked invocations wrote
    # nothing and are neither count.
    acts = [r for r in (action_records or []) if isinstance(r, dict)]
    acted = sum(1 for r in acts if r.get("outcome") == "performed")
    refused = sum(1 for r in acts if r.get("outcome") == "refused")
    out_acts = {"acted_count": acted, "refused_count": refused} if acts else {}
    return {
        **out_acts,
        "governed_ops": governed,
        "governed_count": len(governed),
        "freeform_count": freeform,
        # Attempts the authority layer denied before execution — reported, never
        # folded into either lane (nothing ran).
        "denied_count": denied,
        # Requests the CONTRACT refused (unknown member, grain conflict, …) —
        # the governance working, not data moving. Reported beside the lanes.
        "rejected_count": rejected,
        "total_data_ops": total,
        # None when the turn had no data operations at all → consumers abstain (no card, no score).
        "governed_ratio": (round(len(governed) / total, 4) if total else None),
        "has_data_ops": total > 0,
    }


# ── O2 — the consumption receipt (scope §9 O2) ───────────────────────────────
# The fields a receipt seals. A closed tuple, not "whatever the op carries":
# the sealed content must be byte-reproducible from the turn's `obda` stamp at
# verification time, so volatile / presentation fields must never leak into it.
RECEIPT_FIELDS = ("product", "version", "request_hash", "sql_hash",
                  "row_count", "trust_state")


def receipt_content(op: Dict[str, Any]) -> str:
    """The canonical, signable content of ONE governed consumption — used by BOTH
    the EPC sealing site (executor) and offline verification (provenance
    ``verify_content``), so what is sealed and what is checked can never drift.
    Deterministic: sorted keys, compact separators, absent fields as None."""
    return json.dumps({k: op.get(k) for k in RECEIPT_FIELDS},
                      sort_keys=True, separators=(",", ":"), default=str)


def consumption_receipts(event: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The governed consumptions worth a receipt, from a built obda event (or a
    reloaded ``turn_data['obda']`` stamp — same shape). Only ops with product
    identity qualify; a compile that was REJECTed carries none."""
    ops = (event or {}).get("operations") or (event or {}).get("governed_ops") or []
    return [op for op in ops if isinstance(op, dict) and op.get("product")]


def calls_from_trace(execution_trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapt an Optimize ``turn_action_history`` (``[{action, result}, ...]``) into the canonical
    ``{tool_name, arguments, result}`` shape ``scan_obda`` expects. Mirrors the reader in
    ``eval.types.RunRecord`` so the emission side and the eval side classify identically."""
    out: List[Dict[str, Any]] = []
    for entry in execution_trace or []:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action") or {}
        name = action.get("tool_name")
        if not name:
            continue
        # Orchestrated calls (date-range / column-iteration) wrap the real per-item tool calls in
        # TDA_SystemOrchestration.wrapped_tool_calls — expand them so their raw-SQL data ops are
        # visible to OBDA-READ (free-form lane) and the honesty gate instead of hidden behind the
        # framework wrapper.
        if name == "TDA_SystemOrchestration" and isinstance(action.get("wrapped_tool_calls"), list):
            for w in action["wrapped_tool_calls"]:
                if not isinstance(w, dict):
                    continue
                wname = w.get("tool") or w.get("tool_name")
                if not wname:
                    continue
                wargs = w.get("arguments")
                if not isinstance(wargs, dict):
                    wargs = {k: v for k, v in w.items()
                             if k not in ("tool", "tool_name", "arguments", "result")}
                out.append({"tool_name": wname, "arguments": wargs, "result": w.get("result")})
            continue
        out.append({
            "tool_name": name,
            "arguments": action.get("arguments") or {},
            "result": entry.get("result"),
        })
    return out


def calls_from_conversation_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adapt the ReAct ``conversation_with_tools`` engine's ``conversation_agent_events`` into the
    canonical ``{tool_name, arguments, result}`` shape. Pairs each ``conversation_tool_invoked``
    with the following ``conversation_tool_completed`` by order — mirrors ``eval.types.RunRecord``
    so the emission side and the eval side classify identically."""
    if not isinstance(events, list):
        return []
    invoked = [e.get("payload") or {} for e in events
               if isinstance(e, dict) and e.get("type") == "conversation_tool_invoked"]
    completed = [e.get("payload") or {} for e in events
                 if isinstance(e, dict) and e.get("type") == "conversation_tool_completed"]
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(invoked):
        name = p.get("tool_name")
        if not name:
            continue
        out.append({
            "tool_name": name,
            "arguments": p.get("arguments") or {},
            "result": (completed[i] if i < len(completed) else None),
        })
    return out


def build_event(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the OBDA-READ Live Status **details** dict from a scan summary — analogous to the grounding
    verdict (``_grounding_event``): the engine wraps this in ``{step, type:'obda', details:...}``
    for the live emit and stamps it as ``turn_data['obda']`` for reload. Returns None when the turn
    did no data operations (nothing to show). Carries the lane breakdown so the card can show
    *both* lanes — governed and free-formed — never only the good path."""
    denied = (summary or {}).get("denied_count", 0)
    acted = (summary or {}).get("acted_count", 0)
    refused_acts = (summary or {}).get("refused_count", 0)
    if not summary or (not summary.get("has_data_ops") and not denied and not acted and not refused_acts):
        return None
    governed = summary.get("governed_count", 0)
    freeform = summary.get("freeform_count", 0)
    ratio = summary.get("governed_ratio")
    pct = int(round(ratio * 100)) if isinstance(ratio, (int, float)) else 0
    return {
        "rejected_count": summary.get("rejected_count", 0),
        "governed_count": governed,
        "freeform_count": freeform,
        "denied_count": denied,
        "governed_ratio": ratio,
        "governed_pct": pct,
        "operations": summary.get("governed_ops", []),
        # A single, honest headline: which lane dominated this turn. A turn whose
        # only data activity was DENIED attempts executed nothing — "confined",
        # neither lane, never "free-form" (that word means raw SQL actually ran).
        **({"acted_count": acted, "refused_count": refused_acts} if (acted or refused_acts) else {}),
        # `acted`: the turn's only data activity was performing signed actions (writes
        # through the action face) — a third lane, never folded into governed/free-form.
        "lane": ("acted" if governed == 0 and freeform == 0 and acted > 0
                 else "confined" if governed == 0 and freeform == 0 and denied > 0
                 else "governed" if freeform == 0 and governed > 0
                 else "free-form" if governed == 0
                 else "mixed"),
    }
