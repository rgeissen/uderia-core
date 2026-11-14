"""
Database Connection connector — a User-type connector whose per-user "auth" is a
set of relational database connections (credential forms), not OAuth.

It is a **credential/connection provider**, not a tool-execution connector: it
exposes no LLM tools.  Canvas SQL execution, industry-model PDM deployment, and
future SQL consumers resolve credentials from the centralized store
(``core/database_connections.py``); this module backs the connector framework's
status/disconnect contract so the connector renders + governs like any other.

Contract parity with the OAuth connectors (``connectors/registry.py``):
    SERVER_ID, is_configured, get_connection_status, disconnect, get_tokens,
    initiate_oauth, handle_callback, inject_env_tokens.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

SERVER_ID = "uderia-database"


def _store():
    from trusted_data_agent.core.database_connections import get_database_connection_store
    return get_database_connection_store()


def is_configured() -> bool:
    """No admin app-credentials are required — users supply their own DB creds."""
    return True


def get_connection_status(user_uuid: str) -> Dict[str, Any]:
    """Report how many connections the user has (drives the connector card)."""
    try:
        conns = _store().list(user_uuid, include_legacy=True)
    except Exception:
        conns = []
    return {
        "configured": True,
        "connected": len(conns) > 0,
        "count": len(conns),
        "connections": [
            {"id": c.get("id"), "name": c.get("name"), "driver": c.get("driver")}
            for c in conns
        ],
    }


def disconnect(user_uuid: str) -> None:
    """Remove the user's central connections (legacy Canvas connections untouched)."""
    store = _store()
    for c in store.list(user_uuid, include_legacy=False):
        try:
            store.delete(user_uuid, c["id"])
        except Exception:
            pass


# ── OAuth contract stubs (credential connectors don't use OAuth) ───────────────

def initiate_oauth(user_uuid: str, redirect_uri: str) -> Optional[str]:
    return None


def handle_callback(code: str, state: str, redirect_uri: str) -> Optional[dict]:
    return None


async def get_tokens(user_uuid: str) -> Optional[dict]:
    return None


def inject_env_tokens(env: dict, user_uuid: str) -> dict:
    return env
