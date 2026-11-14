"""Offline flow-agent executor — run a flow through the live pipeline and score it.

A flow-agent case (``profile_class == "flow"``, carrying ``flow_id``) is executed
the same way a user runs a flow: ``POST /v1/flows/<id>/execute`` (which binds a
session at run time — a flow isn't session-bound until execution) → poll the run
→ read the run record (with node_runs). The result feeds FlowRunRecord + the flow
scorers, so flows inherit the same pass^k / gate / diff machinery as profiles.

``CompositeEvalExecutor`` routes each case to the right executor by agent type, so
a dataset may mix profile-agent and flow-agent cases freely.
"""
from __future__ import annotations

import asyncio
import logging

from trusted_data_agent.eval.rest_executor import RestEvalExecutor

logger = logging.getLogger("quart.app")


class FlowEvalExecutor(RestEvalExecutor):
    """Drives a flow run via REST and returns its run record (FlowRunRecord shape)."""

    async def __call__(self, case) -> dict:
        flow_id = getattr(case, "flow_id", None)
        if not flow_id:
            return {"status": "error", "error_message": "flow case has no flow_id"}
        client = await self._client()
        try:
            token = await self._ensure_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            body = {}
            input_params = (case.assertions or {}).get("input_params")
            if input_params:
                body["input_params"] = input_params
            er = await client.post(f"/api/v1/flows/{flow_id}/execute", headers=headers, json=body)
            er.raise_for_status()
            run_id = er.json().get("run_id")
            if not run_id:
                return {"status": "error", "error_message": "flow execute returned no run_id"}
            await self._poll_flow(client, headers, run_id)
            rr = await client.get(f"/api/v1/flows/runs/{run_id}", headers=headers)
            return rr.json() or {"status": "error", "error_message": "no flow run record"}
        except Exception as e:
            logger.warning(f"flow eval executor failed for {flow_id}: {e}")
            return {"status": "error", "error_message": str(e)}
        finally:
            await client.aclose()

    async def _poll_flow(self, client, headers, run_id: str) -> None:
        waited = 0.0
        while waited < self.poll_timeout:
            r = await client.get(f"/api/v1/flows/runs/{run_id}", headers=headers)
            status = (r.json() or {}).get("status", "")
            if status in ("complete", "error", "cancelled"):
                return
            await asyncio.sleep(2.0)
            waited += 2.0


class CompositeEvalExecutor:
    """Routes each case to the flow executor (``profile_class == "flow"``) or the
    standard turn executor — so a dataset can mix both agent types."""

    def __init__(self, base_url: str = "http://localhost:5050", **kw):
        self._turn = RestEvalExecutor(base_url=base_url, **kw)
        self._flow = FlowEvalExecutor(base_url=base_url, **kw)

    async def __call__(self, case) -> dict:
        if getattr(case, "profile_class", None) == "flow":
            return await self._flow(case)
        return await self._turn(case)
