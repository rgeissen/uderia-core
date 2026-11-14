"""Tier 2.1 — REST client facade for the Uderia MCP server.

The MCP server is a thin facade over Uderia's own REST API: an MCP tool call becomes a
sequence of authenticated REST calls (create session → submit query → poll task), so ALL of
the platform's session, profile-override, governance, grounding-gate and guardrail logic is
reused unchanged — the MCP layer adds zero new execution path. The bearer token IS a Uderia
90-day REST access token, so per-user profile/component governance is enforced server-side.

Pure I/O wrapper, no MCP/fastmcp imports — so it is unit-testable with an injected httpx
transport (no live server). See docs/Architecture/MCP_SERVER_ARCHITECTURE.md.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("uderia.mcp_server")

# A poll stops on a SETTLED task: terminal, or interrupted — a turn that stopped to ask
# (PROP-011). The split is core/turn_states.py; no list of strings lives here.
from trusted_data_agent.core import turn_states as _turn_states


class UderiaError(RuntimeError):
    """A REST call failed or a task did not complete successfully."""


class UderiaRestClient:
    """Authenticated async client over the Uderia REST API. One instance per MCP server
    process; ``token`` is a Uderia access token (Bearer). Inject ``transport`` in tests."""

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0,
                 poll_interval: float = 2.0, poll_timeout: float = 180.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._base = base_url.rstrip("/")
        self._token = token
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _json(self, method: str, path: str, **kw) -> Any:
        try:
            r = await self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise UderiaError(f"{method} {path} failed: {e}") from e
        if r.status_code == 401:
            raise UderiaError("unauthorized — the Uderia access token is missing, invalid, or expired")
        if r.status_code >= 400:
            raise UderiaError(f"{method} {path} → HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise UderiaError(f"{method} {path} returned non-JSON: {e}") from e

    # --- profiles -----------------------------------------------------------
    async def list_profiles(self) -> List[Dict[str, Any]]:
        data = await self._json("GET", "/api/v1/profiles")
        profs = data.get("profiles", data) if isinstance(data, dict) else data
        out = []
        for p in (profs or []):
            out.append({"tag": p.get("tag"), "id": p.get("id"),
                        "type": p.get("profile_type"),
                        "description": p.get("description") or ""})
        return out

    async def resolve_profile_id(self, profile: str) -> Optional[str]:
        """Resolve a profile tag (case-insensitive, leading '@' tolerated) OR id to its id.
        Returns None if ``profile`` is falsy (→ caller uses the default profile)."""
        if not profile:
            return None
        needle = profile.lstrip("@").strip()
        profs = await self.list_profiles()
        for p in profs:
            if p["id"] == needle:
                return p["id"]
        for p in profs:
            if (p["tag"] or "").lower() == needle.lower():
                return p["id"]
        raise UderiaError(f"no profile matches '{profile}' "
                          f"(available: {', '.join(p['tag'] or '?' for p in profs)})")

    # --- ask a profile ------------------------------------------------------
    async def create_session(self, name: Optional[str] = None) -> str:
        body = {"name": name} if name else {}
        data = await self._json("POST", "/api/v1/sessions", json=body)
        sid = data.get("session_id")
        if not sid:
            raise UderiaError(f"session creation returned no session_id: {data}")
        return sid

    async def submit_query(self, session_id: str, prompt: str,
                           profile_id: Optional[str] = None) -> str:
        body: Dict[str, Any] = {"prompt": prompt}
        if profile_id:
            body["profile_id"] = profile_id
        data = await self._json("POST", f"/api/v1/sessions/{session_id}/query", json=body)
        tid = data.get("task_id")
        if not tid:
            raise UderiaError(f"query returned no task_id: {data}")
        return tid

    async def poll_task(self, task_id: str, *, sleep=asyncio.sleep) -> Dict[str, Any]:
        """Poll until the task reaches a terminal status, then return its ``result`` dict.
        ``sleep`` is injectable for tests. Raises on timeout / failure status."""
        waited = 0.0
        while True:
            data = await self._json("GET", f"/api/v1/tasks/{task_id}")
            status = (data.get("status") or "").lower()
            if _turn_states.is_settled(status):
                if _turn_states.is_interrupted(status):
                    # the agent stopped to ask: the result carries `input_request`; the MCP
                    # client reads the question as the answer text until phase 3 elicits it
                    return data.get("result") or {}
                if not _turn_states.is_success(status):
                    raise UderiaError(f"task {task_id} ended with status '{status}'")
                return data.get("result") or {}
            if waited >= self._poll_timeout:
                raise UderiaError(f"task {task_id} did not complete within {self._poll_timeout:.0f}s")
            await sleep(self._poll_interval)
            waited += self._poll_interval

    async def ask_profile(self, prompt: str, profile: Optional[str] = None) -> Dict[str, Any]:
        """Full ask flow: resolve profile → create session → submit → poll. Returns
        ``{answer, session_id, profile, guardrail_blocked}``."""
        profile_id = await self.resolve_profile_id(profile) if profile else None
        session_id = await self.create_session()
        task_id = await self.submit_query(session_id, prompt, profile_id)
        result = await self.poll_task(task_id)
        return {
            "answer": result.get("final_answer_text") or result.get("final_answer") or "(no answer)",
            "session_id": session_id,
            "profile": profile or "(default)",
            "guardrail_blocked": bool(result.get("guardrail_blocked")),
            # PROP-011: the agent stopped to ask — the question the MCP client relays to its user
            "input_request": result.get("input_request") if isinstance(result.get("input_request"), dict) else None,
        }

    async def answer_input_request(self, request_id: str, answer: Any) -> Dict[str, Any]:
        """PROP-011: the MCP client's user answered a question an agent reported — answer it
        (the owner's binding is fetched by the token's own read) and poll the continuation."""
        rec = await self._json("GET", f"/api/v1/input-requests/{request_id}")
        resume = rec.get("resume")
        items = rec.get("items") or []
        values = {items[0].get("header", "Q1"): answer} if len(items) == 1 else answer
        started = await self._json("POST", f"/api/v1/input-requests/{request_id}/answer",
                                   json={"decision": "accept" if answer not in (None, "") else "cancel",
                                         "answer": values, "resume": resume})
        tid = started.get("task_id")
        if not tid:
            raise UderiaError(f"the answer was not accepted: {started}")
        result = await self.poll_task(tid)
        return {
            "answer": result.get("final_answer_text") or result.get("final_answer") or "(no answer)",
            "session_id": rec.get("session_id"),
            "profile": rec.get("profile_tag") or "(default)",
            "guardrail_blocked": bool(result.get("guardrail_blocked")),
            "input_request": result.get("input_request") if isinstance(result.get("input_request"), dict) else None,
        }

    # --- identity (hard-gate input) ----------------------------------------
    async def get_my_tier(self) -> str:
        """Resolve the access token's user role ∈ {user, developer, admin} from /auth/me —
        the input to the MCP server's hard tier gate. ``is_admin`` → admin; else ``profile_tier``;
        anything unexpected → ``user`` (least privilege)."""
        data = await self._json("GET", "/api/v1/auth/me")
        user = data.get("user", data) if isinstance(data, dict) else {}
        if user.get("is_admin"):
            return "admin"
        tier = (user.get("profile_tier") or "user").lower()
        return tier if tier in ("user", "developer", "admin") else "user"

    # --- developer-tier reads ----------------------------------------------
    async def list_collections(self) -> List[Dict[str, Any]]:
        data = await self._json("GET", "/api/v1/rag/collections")
        cols = data.get("collections", data) if isinstance(data, dict) else data
        return [{"id": c.get("id"), "name": c.get("name"),
                 "type": c.get("collection_type") or c.get("repository_type") or c.get("type"),
                 "documents": c.get("document_count") or c.get("documents")}
                for c in (cols or [])]

    async def list_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        data = await self._json("GET", "/api/v1/sessions")
        sess = data.get("sessions", data) if isinstance(data, dict) else data
        out = [{"id": s.get("id") or s.get("session_id"), "name": s.get("name"),
                "updated": s.get("last_updated") or s.get("updated_at")}
               for s in (sess or [])]
        return out[: max(1, int(limit))]

    async def get_my_usage(self) -> Any:
        """The token user's own consumption summary (tokens / cost / limits)."""
        return await self._json("GET", "/api/v1/consumption/summary")

    # --- admin-tier reads ---------------------------------------------------
    async def system_usage(self) -> Any:
        """Instance-wide consumption summary (admin)."""
        return await self._json("GET", "/api/v1/consumption/system-summary")

    async def list_user_usage(self) -> Any:
        """Per-user consumption (admin)."""
        return await self._json("GET", "/api/v1/consumption/users")

    # --- OBDA-READ data products (Phase C2 — thin wrappers over the B3 REST) -----
    async def list_data_products(self, kg_id: str) -> List[Dict[str, Any]]:
        data = await self._json("GET", "/api/v1/data-products",
                                params={"kg_id": kg_id})
        return list(data.get("products") or []) if isinstance(data, dict) else []

    async def query_data_product(self, kg_id: str, product: str,
                                 request: Dict[str, Any]) -> Dict[str, Any]:
        """Compile AND execute a §3 request → rows + the governed provenance stamp.
        A REJECT (422) or the disabled switch (403) is returned as a structured
        error dict — self-correction food for the external agent, never a bare
        exception it cannot act on."""
        try:
            return await self._json(
                "POST", f"/api/v1/data-products/{product}/query",
                json={"kg_id": kg_id, "request": request or {}})
        except UderiaError as e:
            return {"status": "error", "message": str(e)}

    # --- signed actions (PROP-008 B4b — thin wrappers over /v1/actions/*) ------
    async def _json_or_refusal(self, method: str, path: str, **kw) -> Dict[str, Any]:
        """Like ``_json`` but a 4xx carrying a JSON body is RETURNED, not raised: a refusal
        (a value outside its domain, an ungranted action, a drifted confirmation) is
        recovery-shaped food for the external agent, never a bare exception it cannot act on."""
        try:
            r = await self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise UderiaError(f"{method} {path} failed: {e}") from e
        if r.status_code == 401:
            raise UderiaError("unauthorized — the Uderia access token is missing, invalid, or expired")
        try:
            body = r.json()
        except ValueError:
            if r.status_code >= 400:
                raise UderiaError(f"{method} {path} → HTTP {r.status_code}: {r.text[:200]}")
            raise UderiaError(f"{method} {path} returned non-JSON")
        if isinstance(body, dict):
            body.setdefault("http_status", r.status_code)
        return body

    @staticmethod
    def _target(kg_id: Optional[str], profile_id: Optional[str]) -> Dict[str, Any]:
        return {k: v for k, v in {"kg_id": kg_id, "profile_id": profile_id}.items() if v}

    async def list_actions(self, kg_id: Optional[str] = None,
                           profile_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._json_or_refusal("GET", "/api/v1/actions",
                                           params=self._target(kg_id, profile_id))

    async def preview_action(self, action: str, key: Dict[str, Any], values: Dict[str, Any], *,
                             kg_id: Optional[str] = None, profile_id: Optional[str] = None,
                             connection_id: Optional[str] = None) -> Dict[str, Any]:
        body = {**self._target(kg_id, profile_id), "face": "mcp", "key": key or {}, "values": values or {}}
        if connection_id:
            body["connection_id"] = connection_id
        return await self._json_or_refusal("POST", f"/api/v1/actions/{action}/preview", json=body)

    async def perform_action(self, action: str, key: Dict[str, Any], values: Dict[str, Any],
                             invocation_hash: str, *, kg_id: Optional[str] = None,
                             profile_id: Optional[str] = None,
                             connection_id: Optional[str] = None) -> Dict[str, Any]:
        body = {**self._target(kg_id, profile_id), "face": "mcp", "key": key or {}, "values": values or {},
                "invocation_hash": invocation_hash}
        if connection_id:
            body["connection_id"] = connection_id
        return await self._json_or_refusal("POST", f"/api/v1/actions/{action}/perform", json=body)

    # --- knowledge graphs (read) -------------------------------------------
    async def list_knowledge_graphs(self) -> List[Dict[str, Any]]:
        data = await self._json("GET", "/api/v1/knowledge-graph/list")
        graphs = data.get("graphs", data.get("knowledge_graphs", data)) if isinstance(data, dict) else data
        out = []
        for g in (graphs or []):
            out.append({"name": g.get("kg_name") or g.get("name"),
                        "id": g.get("kg_id") or g.get("id"),
                        "entities": g.get("entity_count") or g.get("total_entities"),
                        "description": g.get("kg_description") or g.get("description") or ""})
        return out
