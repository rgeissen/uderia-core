"""Needs-input fire drill — a switch for the TEST server only (PROP-011).

Why: a question for the person is raised by code where the platform can tell, and by the
model's own act where it cannot — and a weak model does not take that act (live, 0 of 4
engines did). So the machinery AROUND a question — the chokepoint refusal, the plan ending,
the question on the result and the record, the card, the re-entry, the relay up a chain, a
scheduled run waiting, a flow pausing, the auth card, the elicitation hold — could not be
provoked on demand, on every engine, without betting on a model. The same gap the provider
fire drill (`llm/fault_injection.py`) closed for failures.

What: with ``TDA_NEEDS_INPUT_INJECT=1`` (OFF by default — production never sets it), a turn
whose input carries ``[needs-input:<kind>]`` raises that question in code the moment the
ledger is armed, exactly as a resolver would (`raised_by: code`, `source: fire_drill`):

  choose   · a choice between two of "your" things with one name
  provide  · a typed value nobody recorded
  confirm  · yes/no on a named step
  auth     · an unconnected user connector (the Google card; its answer is the OAuth popup)
  elicit   · a data source's form elicitation, through the real callback (held, then cancelled)

A re-entry turn carrying the marker does NOT raise again: the ledger holds the answer, and a
step that asked once is answered once — the drill reports that it found the answer instead.
Every other turn is untouched, so the agents' activation tests still pass.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")

MARKER_RE = re.compile(r"\[needs-input:(choose|provide|confirm|auth|elicit)\]", re.I)


def enabled() -> bool:
    return (os.environ.get("TDA_NEEDS_INPUT_INJECT") or "").strip().lower() in ("1", "true", "yes", "on")


def kind_of(user_input: Any) -> Optional[str]:
    if not enabled() or not isinstance(user_input, str):
        return None
    m = MARKER_RE.search(user_input)
    return m.group(1).lower() if m else None


async def fire(user_input: Any) -> Optional[Dict[str, Any]]:
    """Raise the marked question into the armed ledger. Returns what happened (for the log and
    the probe): `{"kind", "request_id"|None, "note"}`, or None when nothing was marked."""
    kind = kind_of(user_input)
    if not kind:
        return None
    from trusted_data_agent.agent import input_requests as ir
    if not ir.in_turn():
        return {"kind": kind, "request_id": None, "note": "no ledger armed"}
    led = ir.ledger() or {}
    if led.get("answer"):
        return {"kind": kind, "request_id": None, "note": "re-entry turn: the answer is on the ledger, nothing raised"}
    try:
        if kind == "choose":
            # exactly what a resolver does: a remembered answer first, then the question
            chosen = ir.bound_answer(None, kind="knowledge graph", value="Sales")
            if chosen is not None:
                return {"kind": kind, "request_id": None, "note": f"remembered answer used: {chosen}"}
            rec = ir.ambiguity("knowledge graph", "Sales", [{"id": "kg-sales-1", "name": "Sales", "detail": "48 entities"},
                                                            {"id": "kg-sales-2", "name": "Sales", "detail": "12 entities"}],
                               source="fire_drill.graphs.get.kg_id")
            note = "ambiguity raised" if rec else "ambiguity observed (raisers in observe) or refused"
        elif kind == "provide":
            rec, note = ir.raise_request("provide", [{"header": "FY start", "question": "When does your fiscal year start?",
                                                      "schema": {"type": "string", "format": "date"}}],
                                         "the figures depend on it", raised_by="code", source="fire_drill.provide")
        elif kind == "confirm":
            rec, note = ir.raise_request("confirm", [{"header": "Drop", "question": "Drop the staging table STG_ORDERS before reloading?",
                                                      "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}]}],
                                         "it cannot be undone", raised_by="code", source="fire_drill.confirm")
        elif kind == "auth":
            rec, note = ir.raise_auth_request(server_id="uderia-google", platform="google", display_name="Google")
        else:  # elicit — through the real callback, with the real hold
            from trusted_data_agent.core import mcp_elicitation as me

            class _P:  # the shape `langchain_mcp_adapters` hands the callback
                message = "Which warehouse should the report read?"
                requestedSchema = {"type": "object", "properties": {"warehouse": {"type": "string", "enum": ["north", "south"]}}}
                url = None

            class _C:
                server_name, tool_name = "fire_drill", "warehouse_report"

            res = await me.on_elicitation(None, _P(), _C())
            rec = ir.pending()
            note = f"elicitation answered '{getattr(res, 'action', '?')}' by the hold"
        out = {"kind": kind, "request_id": rec["id"] if rec else None, "note": note if isinstance(note, str) else str(note)}
        logger.info(f"[needs-input drill] {out}")
        return out
    except Exception as e:  # noqa: BLE001 — a drill never breaks a turn
        logger.warning(f"[needs-input drill] {kind} failed: {e}")
        return {"kind": kind, "request_id": None, "note": f"failed: {e}"}
