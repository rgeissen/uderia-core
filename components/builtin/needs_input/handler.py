"""
Needs Input — the system component behind a turn that stops to ask (PROP-011).

The component is the SURFACE: the card in the conversation (renderer.js) and, from phase 2,
the model's own act (`TDA_AskUser`). The STATE is the platform's and lives outside the
component — `agent/input_requests.py` (the turn-scoped ledger, the raisers' posture, the
chokepoint guarantee, the gates' abstention) and `core/input_request_store.py` (the records,
the single-use resume binding). Disabling the component removes the card's interactivity
and the model's act; it never removes the guarantee that a turn which stopped to ask is
recorded as one, and a person can still answer in the composer (the composer rule).

Like the Context Window Manager, this handler is `SystemHandler`; unlike it, the component
carries ONE tool, `TDA_AskUser` — the model's own act on the ReAct lane (phase 2), offered only
where the agent's input policy allows (`offers_tool`), validated by the platform's one validator
and raised into the same ledger the code raisers use. `get_metrics` reports the questions a
person has waiting.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from trusted_data_agent.components.base import ComponentRenderPayload, RenderTarget, SystemHandler

logger = logging.getLogger("quart.app")


class NeedsInputHandler(SystemHandler):

    @property
    def component_id(self) -> str:
        return "needs_input"

    @property
    def tool_name(self) -> str:
        return "TDA_AskUser"

    def offers_tool(self, profile_config: Dict[str, Any]) -> bool:
        """The act is offered only to an agent whose input policy is not `never` — a public
        FAQ agent that must always answer is never shown the tool at all."""
        try:
            from trusted_data_agent.agent import input_requests as ir
            return ir.policy_for(profile_config or {}).get("interactive") != "never"
        except Exception:  # noqa: BLE001
            return True

    def validate_arguments(self, arguments: Dict[str, Any]):
        from trusted_data_agent.agent import input_requests as ir
        norm, err = ir.validate_ask(arguments or {})
        return (norm is not None), (err or "")

    async def process(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> ComponentRenderPayload:
        """The model asked. Validate, raise into the turn's ledger, and tell the model what to do
        now — the question is the result; the chokepoints refuse everything after it."""
        from trusted_data_agent.agent import input_requests as ir
        rec, note = ir.raise_from_act(arguments or {}, source="TDA_AskUser")
        spec: Dict[str, Any] = {"action": "ask", "message": note}
        meta: Dict[str, Any] = {"tool_name": "TDA_AskUser", "action": "ask", "suppress_inline": True}
        if rec is not None:
            from trusted_data_agent.core import input_request_store as store
            spec.update({"status": "input_required", "request_id": rec["id"], "question": store.public_view(rec)})
            meta["input_required"] = True
        else:
            spec["refused"] = True
        return ComponentRenderPayload(component_id=self.component_id, render_target=RenderTarget.INLINE,
                                      spec=spec, html="", title="Needs Input", metadata=meta)

    async def get_metrics(self, user_uuid: Optional[str] = None) -> Dict[str, Any]:
        """The questions waiting for a person — what the Inbox count and the status strip read."""
        try:
            from trusted_data_agent.core import input_request_store as store
            if not user_uuid:
                return {"pending": None}
            return {"pending": store.pending_count(user_uuid)}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"needs_input metrics skipped: {e}")
            return {"pending": None}
