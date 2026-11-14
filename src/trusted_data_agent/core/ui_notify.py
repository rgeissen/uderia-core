"""
ui_notify — the server tells the open UI that something it shows has changed (Oct 2026).

The standing rule: every UX component is LIVE between elements AND outside calls. A write that
reaches the server from anywhere — this page, another tab, a REST script, the MCP server — is
announced on the per-user notification channel at the ONE chokepoint it passes through, never
per caller. The page turns the notice into `uderia:profile-config-changed`, which the resource
panel's one subscriber reads (static/js/handlers/resourceSync.js).

One emission path: `trust_events.broadcast_notice` (loop-guarded, never raises).
"""
from __future__ import annotations

from typing import Optional

PROFILE_CONFIG_CHANGED = "profile_config_changed"


def announce_profile_config_change(user_uuid: Optional[str], profile_id: Optional[str] = None,
                                   aspect: str = "profile") -> None:
    """A profile's configuration (or something an agent's panel lists — its actions) changed.
    `profile_id` None = a change that concerns whatever agent the UI shows."""
    if not user_uuid:
        return
    try:
        from trusted_data_agent.core import trust_events
        trust_events.broadcast_notice(
            user_uuid, PROFILE_CONFIG_CHANGED, f"{aspect} changed",
            severity="info", notice_type=PROFILE_CONFIG_CHANGED,
            extra={"profile_id": profile_id, "aspect": aspect})
    except Exception:  # noqa: BLE001 — a live notice must never break the write
        pass


COMPONENT_CHANGED = "component_changed"
CLIENT_HEADER = "X-Uderia-Client"


def request_client() -> Optional[str]:
    """The id of the browser tab whose request is being served (its ``X-Uderia-Client`` header,
    stamped by auth.js on every same-origin fetch), or None off a request — a scheduled run, a
    REST client that sends none, a background task. Carried on every component notice as
    ``client`` so the page can mark the echo of its own write (uderiaBus.js ``own``). Note that a
    write an agent makes during a UI turn runs inside that page's stream request and so is that
    page's own — the renderer already shows it from the turn's own events."""
    try:
        from quart import has_request_context, request
        if has_request_context():
            v = request.headers.get(CLIENT_HEADER)
            return str(v)[:64] if v else None
    except Exception:  # noqa: BLE001
        pass
    return None


def announce_component_change(user_uuid: Optional[str], component_id: str, action: str,
                              ids=None, session_id: Optional[str] = None,
                              profile_id: Optional[str] = None, aspect: Optional[str] = None) -> None:
    """THE announcement that something a component keeps changed — a dashboard, a canvas, a flow,
    a graph's authored layer, a file, a task, a memory, an action, a data product. Raised ONCE,
    at the store's write seam (never per caller), so a write from this page, another tab, a REST
    script, a scheduled run or an agent turn reaches every open page the same way. The page turns
    it into ``uderia:component-changed`` (static/js/uderiaBus.js → ``UderiaBus.onComponent``).

    ``action`` is one of the component's declared ``signals.announces`` words (created · updated ·
    deleted · restored · refreshed · …); ``ids`` the instance ids it touched; ``aspect`` names the
    part for a component with several (a graph: 'authored' · 'products' · 'versions');
    ``client`` (read from the request, see ``request_client``) names the tab that wrote, so that
    tab's page can tell the echo of its own write from a change made elsewhere.
    Contract: components/signals.py. Never raises."""
    if not user_uuid or not component_id:
        return
    try:
        from trusted_data_agent.core import trust_events
        trust_events.broadcast_notice(
            user_uuid, COMPONENT_CHANGED, f"{component_id} {action}",
            session_id=session_id, severity="info", notice_type=COMPONENT_CHANGED,
            extra={"component_id": str(component_id), "action": str(action or "changed"),
                   "ids": [str(i) for i in (ids or [])], "profile_id": profile_id,
                   "aspect": aspect, "client": request_client()})
    except Exception:  # noqa: BLE001 — a live notice must never break the write
        pass


def announce_files_change(user_uuid: Optional[str], action: str, file_ids=None,
                          session_id: Optional[str] = None) -> None:
    """The user's kept files changed (kept, uploaded, renamed, deleted, restored, moved) —
    announced at the file service's one write path. The Files component's spelling of the one
    component announcement above."""
    announce_component_change(user_uuid, "files", action, file_ids, session_id=session_id)
