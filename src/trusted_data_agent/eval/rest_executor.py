"""REST-driven executor — run an eval case through the live pipeline.

Drives the same REST query API a real user hits (create session → submit query →
poll → read the resulting turn), so "execute + score" exercises the real engines
(no special eval path). Used by the CLI ``eval run`` against a running server.

The profile is selected by ``profile_id`` in the body and NOTHING ELSE. An
earlier version also prefixed the prompt with ``@TAG`` "to force it two ways";
that is the exact anti-pattern CLAUDE.md documents — the REST path does not
strip a leading tag (only the UI does), so the tag is stored verbatim in
``user_query`` and rides into the RAG retrieval embedding, returning a
DIFFERENT chunk set. In an eval runner, whose entire job is A/B comparison,
that silently invalidates the comparison it exists to make.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("quart.app")


class RestEvalExecutor:
    def __init__(self, base_url: str = "http://localhost:5050",
                 token: Optional[str] = None, username: Optional[str] = None,
                 password: Optional[str] = None, poll_timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._username = username
        self._password = password
        self.poll_timeout = poll_timeout
        self._profile_cache: dict = {}

    async def _client(self):
        import httpx
        return httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def _ensure_token(self, client) -> str:
        if self._token:
            return self._token
        r = await client.post("/api/v1/auth/login",
                              json={"username": self._username or "admin",
                                    "password": self._password or "admin"})
        r.raise_for_status()
        self._token = r.json().get("token")
        if not self._token:
            raise RuntimeError("eval REST executor: login returned no token")
        return self._token

    async def _resolve_profile_id(self, client, headers, case) -> Optional[str]:
        if case.profile_id:
            return case.profile_id
        tag = case.profile_tag
        if not tag:
            return None
        if tag in self._profile_cache:
            return self._profile_cache[tag]
        try:
            r = await client.get("/api/v1/profiles", headers=headers)
            for p in (r.json().get("profiles") or r.json() or []):
                if isinstance(p, dict) and p.get("tag") == tag:
                    self._profile_cache[tag] = p.get("id")
                    return p.get("id")
        except Exception as e:
            logger.warning(f"eval executor: could not resolve profile tag {tag}: {e}")
        return None

    async def __call__(self, case) -> dict:
        client = await self._client()
        try:
            token = await self._ensure_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            pid = await self._resolve_profile_id(client, headers, case)

            sr = await client.post("/api/v1/sessions", headers=headers, json={})
            sr.raise_for_status()
            sid = sr.json()["session_id"]

            # The BARE question. Never an @TAG (see the module docstring).
            body = {"prompt": case.query}
            if pid:
                body["profile_id"] = pid
            elif case.profile_tag:
                # No id resolved and the tag is all we have: a silently WRONG profile
                # would score the wrong agent, which is worse than a failed case.
                raise RuntimeError(
                    f"eval executor: could not resolve profile tag @{case.profile_tag} "
                    "to an id — refusing to run the case against the default profile")
            qr = await client.post(f"/api/v1/sessions/{sid}/query", headers=headers, json=body)
            qr.raise_for_status()
            task_id = qr.json().get("task_id")

            await self._poll(client, headers, task_id)
            return await self._last_turn(client, headers, sid)
        finally:
            await client.aclose()

    async def _poll(self, client, headers, task_id: str) -> None:
        if not task_id:
            return
        deadline = self.poll_timeout
        waited = 0.0
        while waited < deadline:
            r = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
            status = (r.json() or {}).get("status", "")
            from trusted_data_agent.core import turn_states as _ts
            if _ts.is_settled(status):
                return
            await asyncio.sleep(2.0)
            waited += 2.0

    async def _last_turn(self, client, headers, sid: str) -> dict:
        """Read the persisted turn — the scorers' input.

        `GET /api/v1/sessions/<id>` does NOT exist (only POST for create, and a
        GET *list*). It returned a 404 HTML page, so `.json()` raised
        `Expecting value: line 1 column 1` for EVERY case — which the runner
        recorded as `executor failed`, i.e. every offline run scored 0% no
        matter how the agent actually performed. The real read is the legacy
        `GET /session/<id>` (no `/api` prefix), whose `workflow_history` is
        top-level. `last_turn_data` is kept as a fallback shape only; the
        v1 sessions LIST is deliberately not used — it stubs the field to `[]`.
        """
        r = await client.get(f"/session/{sid}", headers=headers)
        if r.status_code != 200:
            return {"status": "error", "isValid": False,
                    "error_message": f"session read failed: HTTP {r.status_code}"}
        try:
            data = r.json() or {}
        except ValueError as e:   # non-JSON body — say so, never a bare decode error
            return {"status": "error", "isValid": False,
                    "error_message": f"session read returned non-JSON: {e}"}
        wf = data.get("workflow_history")
        if not isinstance(wf, list) or not wf:
            wf = (data.get("last_turn_data") or {}).get("workflow_history") or []
        if isinstance(wf, list) and wf:
            return wf[-1]
        return {"status": "error", "isValid": False, "error_message": "no turn produced"}
