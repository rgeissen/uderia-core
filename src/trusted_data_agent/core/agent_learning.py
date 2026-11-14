"""Where an Optimize agent learns — its OWN planner repository, or the user's shared one.

OC-F5 (Optimize campaign, 2026-10-29; operator decision "option B"). Every successful Optimize
turn is stored as a champion case. Until now it landed in the user's ONE default planner
repository for the MCP server (or in the repository whose champion guided the turn), and every
agent of that user on that server read it back: one notebook, shared by all of them. An agent's
plans changed because of OTHER agents' turns, the shared repository changed under every signed
agent that bound it, and no agent could be measured cold.

The rule, held here and nowhere else:

* An agent HAS its own learning repository when a planner collection carries its id in
  ``learning_profile_id``. That row is the switch — no profile field, so no signature moves.
* New Optimize agents get one at creation (``provision_for_new_agent``, called from
  ``config_manager.add_profile[_locked]`` — the editor, REST, pack import and subscribe all
  create through there). Existing agents keep the shared notebook until opted in
  (``POST /v1/profiles/<id>/learning-repository``). Bootstrap agents of a new account are
  written straight into its preferences and keep the shared notebook too.
* An agent with its own repository LEARNS only into it (``learning_target``) and READS it
  beside what it binds (``read_repository``) — never the shared notebook, never the
  repository that guided the turn. A LOCKED own repository is frozen: read, never written.
* Lessons are SQL against one database, so the repository is per agent AND per MCP server:
  switching the agent's server starts a new one on its first lesson there.
* The own repository is NOT bound (``ragCollections``), so its contents are not hashed into the
  agent's signature — learning does not unsign an agent, the same treatment as Agent Memory.
  It is named on the agent instead (the editor), and every turn records which repository a
  champion came from (``rag_source_collection_id``). Its cases are learned, never
  ``template_generated``, so it is never replayed verbatim (``planner.exact_signed_champion``).
* It is never a "default": both default lookups skip it (``is_agent_repository``), so deleting
  your default can never turn one agent's repository into everyone's notebook.
* Removing the agent removes its repositories (``retire_for_removed_agent``).
"""
from __future__ import annotations

import logging
import threading
import uuid as _uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("quart.app")

COLUMN = "learning_profile_id"
PLANNING_TYPES = ("tool_enabled",)

_create_lock = threading.Lock()


def is_agent_repository(row: Optional[Dict]) -> bool:
    """A planner collection that belongs to one agent's learning (never a user default)."""
    return bool((row or {}).get(COLUMN))


def _db():
    from trusted_data_agent.core.collection_db import get_collection_db
    return get_collection_db()


def repositories_of(profile_id: str, user_uuid: Optional[str] = None,
                    rows: Optional[List[Dict]] = None) -> List[Dict]:
    """Every learning repository of an agent (all servers), oldest first."""
    if not profile_id:
        return []
    if rows is None:
        try:
            rows = _db().get_all_collections()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"agent_learning: collections unreadable ({e})")
            return []
    out = [r for r in rows or []
           if str(r.get(COLUMN) or "") == str(profile_id)
           and r.get("repository_type") == "planner"
           and (user_uuid is None or r.get("owner_user_id") == user_uuid)]
    return sorted(out, key=lambda r: r.get("id") or 0)


def learns_own(profile_id: str, user_uuid: Optional[str] = None,
               rows: Optional[List[Dict]] = None) -> bool:
    """True when the agent learns into its own repository (it has at least one)."""
    return bool(repositories_of(profile_id, user_uuid, rows))


def repository_for(profile_id: str, mcp_server_id: Optional[str], user_uuid: Optional[str] = None,
                   rows: Optional[List[Dict]] = None) -> Optional[Dict]:
    """The agent's repository for one server, or None."""
    for r in repositories_of(profile_id, user_uuid, rows):
        if (r.get("mcp_server_id") or "") == (mcp_server_id or ""):
            return r
    return None


def _label(profile: Optional[Dict], profile_id: str) -> str:
    tag = (profile or {}).get("tag")
    return f"@{tag}" if tag else (profile or {}).get("name") or profile_id


def ensure_repository(profile_id: str, mcp_server_id: str, user_uuid: str,
                      profile: Optional[Dict] = None) -> Optional[int]:
    """The agent's repository for this server, created when missing. Idempotent and safe under
    concurrent callers (the RAG worker and an editor save may race on a new agent)."""
    if not (profile_id and mcp_server_id and user_uuid):
        return None
    existing = repository_for(profile_id, mcp_server_id, user_uuid)
    if existing:
        return existing.get("id")
    with _create_lock:
        existing = repository_for(profile_id, mcp_server_id, user_uuid)
        if existing:
            return existing.get("id")
        try:
            label = _label(profile, profile_id)
            cid = _db().create_collection({
                "name": f"{label} — learned",
                "collection_name": f"tda_rag_coll_agent_{_uuid.uuid4().hex[:8]}",
                "mcp_server_id": mcp_server_id,
                "enabled": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "description": (f"What {label} has learned from its own successful turns. "
                                f"Only {label} writes here; bind it to another agent to share."),
                "owner_user_id": user_uuid,
                "visibility": "private",
                "is_marketplace_listed": False,
                "subscriber_count": 0,
                "marketplace_metadata": {},
                "repository_type": "planner",
            })
            _db().update_collection(cid, {COLUMN: str(profile_id)})
        except Exception as e:  # noqa: BLE001
            logger.error(f"agent_learning: could not create the learning repository of "
                         f"{profile_id} on {mcp_server_id}: {e}", exc_info=True)
            return None
    logger.info(f"agent_learning: created learning repository {cid} for {profile_id} "
                f"on server {mcp_server_id}")
    _refresh_runtime(cid)
    return cid


def _refresh_runtime(cid: Optional[int] = None) -> None:
    try:
        from trusted_data_agent.core.config import APP_STATE
        from trusted_data_agent.core.config_manager import get_config_manager
        APP_STATE["rag_collections"] = get_config_manager().get_rag_collections()
    except Exception:  # noqa: BLE001
        pass
    if cid is not None:
        try:
            from trusted_data_agent.agent.rag_retriever import get_rag_retriever
            r = get_rag_retriever()
            if r is not None:
                r.ensure_loaded(int(cid))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"agent_learning: repository {cid} created but not loaded yet: {e}")


def provision_for_new_agent(profile: Optional[Dict], user_uuid: Optional[str]) -> Optional[int]:
    """A NEW Optimize agent learns into its own repository from its first turn."""
    p = profile or {}
    if p.get("profile_type") not in PLANNING_TYPES:
        return None
    from trusted_data_agent.core.agent_mcp_servers import primary_of
    server = primary_of(p)
    if not (p.get("id") and server and user_uuid):
        return None
    return ensure_repository(p["id"], server, user_uuid, profile=p)


def learning_target(profile_id: Optional[str], mcp_server_id: Optional[str],
                    user_uuid: Optional[str]) -> Dict:
    """Where a successful turn of this agent is stored.

    ``{"mode": "shared"}`` — the agent has no own repository: the legacy path decides.
    ``{"mode": "own", "collection_id": n}`` — store there (created on the first lesson on a
    server the agent has not learned on before).
    ``{"mode": "frozen", "collection_id": n}`` — its repository here is LOCKED: store nothing.
    """
    if not (profile_id and user_uuid) or not learns_own(profile_id, user_uuid):
        return {"mode": "shared"}
    row = repository_for(profile_id, mcp_server_id, user_uuid)
    if row is not None and row.get("trust_locked"):
        return {"mode": "frozen", "collection_id": row.get("id")}
    cid = row.get("id") if row else ensure_repository(profile_id, mcp_server_id, user_uuid)
    if cid is None:
        return {"mode": "frozen", "collection_id": None}   # cannot create ⇒ never the shared one
    return {"mode": "own", "collection_id": cid}


def read_repository(profile_id: Optional[str], mcp_server_id: Optional[str],
                    user_uuid: Optional[str]) -> Optional[int]:
    """The agent's own repository it reads for this server (locked or not), or None.
    Never creates — retrieval must not write."""
    if not (profile_id and user_uuid):
        return None
    row = repository_for(profile_id, mcp_server_id, user_uuid)
    return row.get("id") if row else None


def _remove_case_mirror(cid) -> None:
    """The planner's on-disk case files die with it, as in `RAGRetriever.remove_collection`."""
    try:
        import shutil
        from trusted_data_agent.core.config import APP_CONFIG
        from trusted_data_agent.core.utils import get_project_root
        mirror = get_project_root() / APP_CONFIG.RAG_CASES_DIR / f"collection_{int(cid)}"
        if mirror.is_dir():
            shutil.rmtree(mirror)
    except Exception as e:  # noqa: BLE001 — a file we cannot remove never blocks the removal
        logger.warning(f"agent_learning: on-disk cases of repository {cid} kept: {e}")


def retire_for_removed_agent(profile_id: str, user_uuid: Optional[str]) -> List[int]:
    """Removing an agent removes what it learned (its repositories on every server)."""
    removed: List[int] = []
    for row in repositories_of(profile_id, user_uuid):
        cid = row.get("id")
        try:
            try:
                from trusted_data_agent.agent.rag_retriever import get_rag_retriever
                r = get_rag_retriever()
                if r is not None:
                    try:
                        r.client.delete_collection(name=row.get("collection_name"))
                    except Exception:  # noqa: BLE001 — never created in the vector store yet
                        pass
                    r.collections.pop(cid, None)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"agent_learning: vector store cleanup for {cid} skipped: {e}")
            if _db().delete_collection(int(cid)):
                removed.append(int(cid))
                _remove_case_mirror(cid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"agent_learning: could not remove repository {cid} of {profile_id}: {e}")
    if removed:
        logger.info(f"agent_learning: removed learning repositories {removed} of agent {profile_id}")
        _refresh_runtime()
    return removed
