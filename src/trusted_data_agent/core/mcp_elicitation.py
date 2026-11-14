"""
MCP elicitation — a data source asks (PROP-011 phase 3, the MCP CLIENT side).

When one of the agent's MCP servers sends `elicitation/create` during a tool call, the request
becomes a question for the person through the same ledger every other raiser uses
(`raised_by: data_source`, `source: mcp:<server>:<tool>`): the form's flat schema becomes the
items. The protocol holds the tool call open, so the callback waits a short, bounded time for
the answer (the card is on the way through the turn's stream); unanswered within the bound it
answers `cancel` — the turn then ends `input_required` with the question still pending, the
person answers it, and on the re-entry turn the same tool call finds the answer on the ledger
and accepts at once (the retry pattern MCP's draft adopts). A URL-mode elicitation (OAuth and
the like) is relayed as the URL to open; its answer is `cancel` here — the person completes it
outside the turn and asks again.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")


def hold_seconds() -> float:
    """How long a held-open elicitation waits for the answer: the env (a test server's
    fire drill shortens it) over `APP_CONFIG.INPUT_ELICIT_HOLD_SECONDS` (90)."""
    import os
    try:
        if os.environ.get("INPUT_ELICIT_HOLD_SECONDS"):
            return float(os.environ["INPUT_ELICIT_HOLD_SECONDS"])
        from trusted_data_agent.core.config import APP_CONFIG
        return float(getattr(APP_CONFIG, "INPUT_ELICIT_HOLD_SECONDS", 90) or 90)
    except Exception:  # noqa: BLE001
        return 90.0


def _result(action: str, content: Optional[Dict[str, Any]] = None):
    from mcp.types import ElicitResult
    return ElicitResult(action=action, content=content) if action == "accept" else ElicitResult(action=action)


async def on_elicitation(mcp_context, params, context) -> Any:
    """`langchain_mcp_adapters` callback: `(mcp_context, ElicitRequestParams, CallbackContext)`."""
    from trusted_data_agent.agent import input_requests as ir
    server = getattr(context, "server_name", None) or "mcp"
    tool = getattr(context, "tool_name", None) or "?"
    source = f"mcp:{server}:{tool}"
    message = str(getattr(params, "message", "") or "")
    url = getattr(params, "url", None)
    schema = getattr(params, "requestedSchema", None)
    if schema is not None and hasattr(schema, "model_dump"):
        schema = schema.model_dump()
    if not ir.in_turn():
        logger.info(f"[needs-input] elicitation from {source} outside a turn — cancelled")
        return _result("cancel")
    # the re-entry: the answer is already on the ledger for this very step
    bound = ir.bound_answer(source)
    if bound is not None:
        content = bound if isinstance(bound, dict) else {"value": bound}
        return _result("accept", _by_field(content, schema))
    if url:
        items = [{"header": "Open", "question": f"{message or 'The data source needs you to complete a step'} — open: {url}",
                  "schema": {"type": "string", "format": "uri"}}]
        ir.raise_request("provide", items, f"{server} asks", raised_by="data_source", source=source)
        return _result("cancel")
    items = ir.elicit_items_from_schema(message, schema)
    kind = "provide"
    rec, why = ir.raise_request(kind, items, f"{server} asks", raised_by="data_source", source=source)
    if rec is None:
        logger.info(f"[needs-input] elicitation from {source} could not be raised: {why}")
        return _result("cancel")
    # hold the call open, bounded, for the answer (the card reaches the person through the turn)
    from trusted_data_agent.core import input_request_store as store
    deadline = asyncio.get_event_loop().time() + hold_seconds()
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(2.0)
        cur = store.get(rec["id"], rec["user_uuid"])
        if cur is None:
            break
        if cur["status"] == "accepted":
            led = ir.ledger()
            if led is not None and led.get("pending") and led["pending"].get("id") == rec["id"]:
                led["pending"] = None   # answered in flight: the turn goes on
            return _result("accept", _by_field(cur.get("answer") or {}, schema, items))
        if cur["status"] in ("declined", "cancelled", "expired", "superseded"):
            led = ir.ledger()
            if led is not None and led.get("pending") and led["pending"].get("id") == rec["id"]:
                led["pending"] = None
            return _result("decline" if cur["status"] == "declined" else "cancel")
    logger.info(f"[needs-input] elicitation from {source} not answered within the hold — the turn ends asking")
    return _result("cancel")


def _by_field(values: Dict[str, Any], schema: Any, items=None) -> Dict[str, Any]:
    """The answer keyed the way the server's schema names its fields (items carry `field`)."""
    if items:
        out = {}
        for it in items:
            h, f = it.get("header"), it.get("field") or it.get("header")
            if h in values:
                out[f] = values[h]
        if out:
            return out
    props = (schema or {}).get("properties") if isinstance(schema, dict) else None
    if isinstance(props, dict) and len(props) == 1 and len(values) == 1:
        return {next(iter(props)): next(iter(values.values()))}
    return dict(values)


def langchain_callbacks():
    """The `Callbacks` object every MCP client of a data source is built with."""
    try:
        from langchain_mcp_adapters.callbacks import Callbacks
        return Callbacks(on_elicitation=on_elicitation)
    except Exception as e:  # noqa: BLE001 — an adapter without the hook keeps today's behaviour
        logger.debug(f"mcp elicitation callbacks unavailable: {e}")
        return None
