# src/trusted_data_agent/core/session_manager.py
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
import uuid
import os
import json
import logging
from typing import Any, Optional
from datetime import datetime, timezone
from pathlib import Path # Use pathlib for better path handling
import shutil # For potential cleanup later if needed
import asyncio # For sending notifications asynchronously
import tempfile # For atomic file writes
import aiofiles # For async file I/O

from trusted_data_agent.agent.prompts import PROVIDER_SYSTEM_PROMPTS
# --- MODIFICATION START: Import APP_CONFIG ---
from trusted_data_agent.core.config import APP_STATE, APP_CONFIG
from trusted_data_agent.core.utils import generate_session_id, get_project_root # Import generate_session_id and get_project_root
from trusted_data_agent.agent.rag_template_generator import RAGTemplateGenerator
from trusted_data_agent.core.session_backends import get_session_backend
# --- MODIFICATION END ---


# --- Define SESSIONS_DIR relative to project root ---
# Use the new get_project_root() function to find the correct project root
_PROJECT_ROOT = get_project_root()
SESSIONS_DIR = _PROJECT_ROOT / "tda_sessions"


app_logger = logging.getLogger("quart.app") # Use quart logger for consistency

def _initialize_sessions_dir():
    """Creates the main sessions directory if it doesn't exist."""
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        app_logger.debug(f"Session directory ensured at: {SESSIONS_DIR}")
    except OSError as e:
        app_logger.error(f"Could not create session directory '{SESSIONS_DIR}'. Session persistence will fail. Error: {e}", exc_info=True)
        raise # Re-raise to prevent startup if dir fails

# Call initialization when the module loads
_initialize_sessions_dir()

# --- Removed global _SESSIONS dictionary ---

# --- Notification Deduplication Cache ---
# Track last notification state per session to avoid spam
# Key: session_id, Value: dict with provider, model, profile_tag
_last_notification_state = {}

# --- Per-Session Write Lock ---
# Serializes load-modify-save cycles on a per-session basis to prevent:
# 1. Storage corruption from concurrent writes (defense-in-depth)
# 2. Logical data loss where a stale load overwrites another writer's changes
# Delegated to the active LockProvider (in-process by default; Redis for
# multi-server, where the lock must coordinate across processes).
from trusted_data_agent.core.locks import get_lock_provider

def _get_session_lock(session_id: str):
    """Return the (async-context-manager) lock guarding a session's write cycle."""
    return get_lock_provider().get_lock(session_id)


# ---------------------------------------------------------------------------
# Session Metadata Index — delegated to the active session backend
# ---------------------------------------------------------------------------
# The summary index (fast session listing) is owned by the storage backend
# (FileSessionBackend: a SQLite cache over the JSON files; PostgresSessionBackend:
# the sessions table itself). These thin wrappers keep the historical private
# names used across this module while routing to the backend.
# ---------------------------------------------------------------------------


async def _init_session_index():
    """Initialize the active backend's session summary index."""
    await get_session_backend().init_index()


async def ensure_session_index_ready():
    """Startup hook: init the summary index and rebuild it if empty/stale."""
    await get_session_backend().ensure_index_ready()


def _compute_session_status(wf: list) -> str:
    """Derive session status from workflow history turns.

    Returns one of: 'empty', 'success', 'partial', 'failed'
    """
    valid_turns = [t for t in wf if t.get("isValid", True)]
    if not valid_turns:
        return "empty"
    last_status = valid_turns[-1].get("status", "success")
    if last_status == "error":
        return "failed"
    if last_status == "cancelled":
        return "partial"
    return "success"


async def _upsert_session_index(session_id: str, session_data: dict):
    """Insert/update a session's summary in the backend index. Fire-and-forget safe."""
    await get_session_backend().upsert_summary(session_id, session_data)


async def _delete_from_session_index(session_id: str):
    """Remove a session from the backend index."""
    await get_session_backend().delete_summary(session_id)


async def _query_session_index(user_uuid: str, include_archived: bool = False,
                                filter_by_user: bool = True) -> list[dict] | None:
    """Query session summaries from the backend index.

    Returns list of summary dicts, or None if the index is unavailable (caller
    should fall back to a scan)."""
    return await get_session_backend().query_summaries(
        user_uuid, include_archived=include_archived, filter_by_user=filter_by_user)


async def _rebuild_session_index():
    """Rebuild the backend's summary index from authoritative storage."""
    await get_session_backend().rebuild_summaries()


from contextlib import asynccontextmanager

@asynccontextmanager
async def _session_transaction(user_uuid: str, session_id: str):
    """
    Context manager that serializes load-modify-save cycles for a session.

    Usage:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if session_data is None:
                return  # Session not found
            session_data['field'] = new_value
            # Auto-saved on exit (only if session_data was found)

    The lock is held for the entire load-modify-save cycle, preventing concurrent
    writers from reading stale data and overwriting each other's changes.
    """
    lock = _get_session_lock(session_id)
    async with lock:
        session_data = await _load_session(user_uuid, session_id)
        yield session_data
        if session_data is not None:
            if not await _save_session(user_uuid, session_id, session_data):
                app_logger.error(f"Failed to save session {session_id} in transaction")


# --- File I/O Helper Functions ---

async def _load_session(user_uuid: str, session_id: str) -> dict | None:
    """Load a session body via the active storage backend."""
    return await get_session_backend().load(user_uuid, session_id)

async def _save_session(user_uuid: str, session_id: str, session_data: dict):
    """Persist a session via the active storage backend.

    Stamps ``last_updated``, writes the body through the backend, fires the
    (fire-and-forget) summary-index upsert, and emits the ``session_model_update``
    notification. The notification + stamping live here (not in the backend) so
    they run identically regardless of storage backend.
    """
    session_data['last_updated'] = datetime.now().isoformat()
    backend = get_session_backend()
    saved = await backend.save(user_uuid, session_id, session_data)
    if not saved:
        return False  # Indicate failure
    try:
        app_logger.debug(f"Successfully saved session '{session_id}' for user '{user_uuid}'.")

        # Update session summary index (fire-and-forget — index is a cache)
        asyncio.create_task(backend.upsert_summary(session_id, session_data))

        # --- MODIFICATION START: Send session_model_update notification (with deduplication) ---
        notification_queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
        if notification_queues:
            # Check if notification state has changed
            # Include profile_tags_used as a tuple for comparison (arrays aren't directly comparable)
            profile_tags_used = session_data.get("profile_tags_used", [])
            current_state = {
                "provider": session_data.get("provider"),
                "model": session_data.get("model"),
                "profile_tag": session_data.get("profile_tag"),
                "profile_tags_used": tuple(profile_tags_used) if profile_tags_used else (),
            }
            last_state = _last_notification_state.get(session_id)

            # Only send notification if state changed or this is the first notification
            if last_state != current_state:
                notification_payload = {
                    "session_id": session_id,
                    "models_used": session_data.get("models_used", []),
                    "profile_tags_used": session_data.get("profile_tags_used", []),
                    "last_updated": session_data.get("last_updated"),
                    "provider": current_state["provider"],
                    "model": current_state["model"],
                    "name": session_data.get("name", "Unnamed Session"),
                }
                app_logger.debug(f"_save_session sending notification for session {session_id}: provider={notification_payload['provider']}, model={notification_payload['model']}, profile_tags_used={notification_payload['profile_tags_used']} (state changed)")
                notification = {
                    "type": "session_model_update",
                    "payload": notification_payload
                }
                for queue in notification_queues:
                    asyncio.create_task(queue.put(notification))
                
                # Update cache
                _last_notification_state[session_id] = current_state
            else:
                app_logger.debug(f"_save_session skipping notification for session {session_id}: state unchanged")
        # --- MODIFICATION END ---

        return True # Indicate success
    except Exception as e:
        # Body already persisted by the backend; this guards index/notification side-effects.
        app_logger.error(f"Post-save side-effect error for session '{session_id}': {e}", exc_info=True)
        return True


async def save_session(user_uuid: str, session_id: str, session_data: dict) -> bool:
    """Public API to persist a session's data.

    Thin wrapper over the internal persistence primitive so callers outside
    this module never reach into the private `_save_session`. This is the single
    supported write entry point and the seam through which the storage backend
    is swapped (file vs. database).
    """
    return await _save_session(user_uuid, session_id, session_data)


# --- Public Session Management Functions ---

async def create_session(user_uuid: str, provider: str, llm_instance: any, charting_intensity: str, system_prompt_template: str | None = None, profile_tag: str | None = None, profile_id: str | None = None, is_temporary: bool = False, temporary_purpose: str | None = None, genie_parent_session_id: str | None = None, genie_slave_profile_id: str | None = None, genie_sequence_number: int | None = None) -> str:
    session_id = generate_session_id()
    app_logger.info(f"Attempting to create session '{session_id}' for user '{user_uuid}' (temporary: {is_temporary}).")

    # Note: chat_object cannot be directly serialized to JSON.
    # We will store history as a plain list.
    chat_history_for_file = []
    if provider == "Google":
        # Keep initial prompt for Google compatibility if needed later, but don't store the live object.
        initial_history_google = [
            {"role": "user", "parts": [{"text": "You are a helpful assistant."}]},
            {"role": "model", "parts": [{"text": "Understood."}]}
        ]
        # Store this initial history in the file version
        chat_history_for_file = [{'role': m['role'], 'content': m['parts'][0]['text']} for m in initial_history_google]

    session_data = {
        "id": session_id, # Store ID within the data itself
        "user_uuid": user_uuid, # Store user UUID for potential later use/verification
        "system_prompt_template": system_prompt_template,
        "charting_intensity": charting_intensity,
        "provider": provider, # --- Store the provider used for this session (for backwards compatibility)
        "model": APP_CONFIG.CURRENT_MODEL, # --- Store the model used for this session (for backwards compatibility)
        "profile_tag": profile_tag, # --- Store the profile tag used for this session
        "profile_id": profile_id, # --- Store the profile ID used for this session (for badge/color info)
        "profile_tags_used": [], # --- Populated on first query execution, not on session creation
        "models_used": [], # --- Keep for backwards compatibility
        "session_history": [], # UI history (messages added via add_message_to_histories)
        "chat_object": chat_history_for_file, # Store serializable history for LLM context
        "name": "New Chat",
        "created_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "input_tokens": 0,
        "output_tokens": 0,
        # --- MODIFICATION START: Ensure workflow_history list exists on creation ---
        "last_turn_data": {"workflow_history": []},
        # --- MODIFICATION END ---
        "full_context_sent": False,
        "license_info": APP_STATE.get("license_info"), # Store license info at creation time
        "is_temporary": is_temporary, # Mark if this is a temporary/utility session
        "temporary_purpose": temporary_purpose, # Optional description of the temporary session purpose
        # --- GENIE PROFILE METADATA ---
        "genie_metadata": {
            "is_genie_slave": genie_parent_session_id is not None,
            "parent_session_id": genie_parent_session_id,
            "slave_profile_id": genie_slave_profile_id,
            "slave_sequence_number": genie_sequence_number or 0
        }
    }

    if await _save_session(user_uuid, session_id, session_data):
        app_logger.info(f"Successfully created and saved session '{session_id}' for user '{user_uuid}'.")
        
        # --- CONSUMPTION TRACKING START ---
        # Increment session count in consumption database (only if truly new)
        try:
            from trusted_data_agent.auth.database import get_db_session
            from trusted_data_agent.auth.consumption_manager import ConsumptionManager
            
            with get_db_session() as db_session:
                manager = ConsumptionManager(db_session)
                manager.increment_session_count(user_uuid, session_id, is_new_session=True)
                app_logger.debug(f"Updated session count for user {user_uuid}, session {session_id}")
        except Exception as e:
            # Non-critical: File storage is source of truth, DB is performance cache
            app_logger.warning(f"Failed to update session count for user {user_uuid}: {e}")
        # --- CONSUMPTION TRACKING END ---
        
        return session_id
    else:
        app_logger.error(f"Failed to save newly created session '{session_id}' for user '{user_uuid}'.")
        raise IOError(f"Failed to save session file for session {session_id}")


async def read_session(user_uuid: str, session_id: str) -> dict | None:
    """Pure read of a session's full body — no enrichment, no migration, no re-save.

    Use this (not `get_session`) for read-only scans such as artifact relationship
    detection, where triggering `get_session`'s turn-number backfill re-save on
    every session would be wasteful and surprising. This is the public face of the
    storage backend's `load`.
    """
    return await _load_session(user_uuid, session_id)

async def get_session(user_uuid: str, session_id: str) -> dict | None:
    app_logger.debug(f"Getting session '{session_id}' for user '{user_uuid}'.")
    session_data = await _load_session(user_uuid, session_id)
    if session_data:
        history_modified = False
        # --- MODIFICATION START: Update backfill logic for turn numbers ---
        if "session_history" in session_data and session_data["session_history"]:
            if session_data["session_history"] and "turn_number" not in session_data["session_history"][0]:
                app_logger.info(f"Session {session_id} history is outdated. Adding turn numbers.")
                history_modified = True
                turn_counter = 0
                for msg in session_data["session_history"]:
                    if msg.get("role") == "user":
                        turn_counter += 1
                    msg["turn_number"] = turn_counter

        if "chat_object" in session_data and session_data["chat_object"]:
            if session_data["chat_object"] and "turn_number" not in session_data["chat_object"][0]:
                if not history_modified:
                    app_logger.info(f"Session {session_id} chat_object is outdated. Adding turn numbers.")
                history_modified = True
                turn_counter = 0
                # Skip the initial system message which doesn't have a turn
                for i, msg in enumerate(session_data["chat_object"]):
                    # The first two messages are the system prompt and should not be part of a turn
                    if i < 2:
                        msg["turn_number"] = 0
                        continue
                    if msg.get("role") == "user":
                        turn_counter += 1
                    msg["turn_number"] = turn_counter

        if history_modified:
            app_logger.info(f"Saving session {session_id} after migrating to include turn numbers.")
            await _save_session(user_uuid, session_id, session_data)

        # Enrich genie_metadata with nesting_level and slave_profile_tag from database
        genie_metadata = session_data.get("genie_metadata", {})
        if genie_metadata.get("is_genie_slave"):
            parent_link = await get_genie_parent_session(session_id, user_uuid)
            if parent_link:
                nesting_level = parent_link.get("nesting_level", 0)
                slave_profile_tag = parent_link.get("slave_profile_tag")
                genie_metadata["nesting_level"] = nesting_level
                genie_metadata["slave_profile_tag"] = slave_profile_tag
                session_data["genie_metadata"] = genie_metadata
                app_logger.debug(f"Enriched session {session_id} with nesting_level={nesting_level}, slave_profile_tag={slave_profile_tag}")

    return session_data

def session_cost_total(session_data: dict, *, provider: str | None = None, model: str | None = None) -> float:
    """THE session's cost: Σ `turn_cost` over EVERY turn in its record.

    One definition, read by every surface that reports "session cost" — the plan-reload
    endpoint, the session GET, the engines' per-turn snapshot — so they cannot disagree.
    It mirrors the session TOKEN counters, which accumulate and never subtract: a turn
    counts whether or not it is in the model's context (`isValid`) and whether or not it
    is the adopted answer of a regenerate — a non-adopted sibling spent real money, and a
    session cost that changed with which answer is adopted was the defect this closes
    (prod session 856afac4, 2026-09-21). A turn persisted before `turn_cost` existed is
    priced from its tokens at its own provider/model (else the session's, else the
    caller's); a turn that cannot be priced contributes nothing rather than raising.
    A flow-run turn contributes its `turn_cost` (0 — its nodes bill their own turns),
    exactly as its `turn_input_tokens` (0) enter the token counters.
    """
    total = 0.0
    wf = ((session_data or {}).get("last_turn_data") or {}).get("workflow_history") or []
    cost_manager = None
    for turn in wf:
        if not isinstance(turn, dict):
            continue
        if turn.get("turn_cost") is not None:
            try:
                total += float(turn["turn_cost"] or 0.0)
            except (TypeError, ValueError):
                pass
            continue
        # legacy turn — price it from its tokens, best effort
        try:
            if cost_manager is None:
                from trusted_data_agent.core.cost_manager import get_cost_manager
                cost_manager = get_cost_manager()
            total += float(cost_manager.calculate_cost(
                provider=turn.get("provider") or session_data.get("provider") or provider,
                model=turn.get("model") or session_data.get("model") or model,
                input_tokens=int(turn.get("turn_input_tokens") or turn.get("input_tokens") or 0),
                output_tokens=int(turn.get("turn_output_tokens") or turn.get("output_tokens") or 0),
            ) or 0.0)
        except Exception as e:  # noqa: BLE001
            app_logger.debug(f"[Session Cost] legacy turn {turn.get('turn', '?')} could not be priced: {e}")
    return total


async def get_workflow_history(user_uuid: str, session_id: str) -> list:
    """Return a session's `last_turn_data.workflow_history` as a list of turn dicts.

    Centralizes the extraction (including the legacy string-serialized fallback)
    so callers never read session files directly. Returns [] on any miss/error.
    """
    import ast as _ast
    try:
        session_data = await read_session(user_uuid, session_id)
    except Exception as e:
        app_logger.warning(f"get_workflow_history: failed to load session '{session_id}': {e}")
        return []
    if not session_data:
        return []
    wh = session_data.get("last_turn_data", {}).get("workflow_history", [])
    if isinstance(wh, str):
        try:
            wh = _ast.literal_eval(wh)
        except (ValueError, SyntaxError):
            return []
    return wh if isinstance(wh, list) else []

async def get_user_sessions(user_uuid: str, include_archived: bool = False) -> list:
    """Convenience wrapper over `get_all_sessions` returning just the summaries list.

    Single supported entry point for code that needs to enumerate a user's
    sessions (e.g. artifact relationship detection) instead of globbing the
    session directory directly.
    """
    result = await get_all_sessions(user_uuid, include_archived=include_archived)
    return result.get("sessions", []) if isinstance(result, dict) else []

async def get_user_session_bodies(user_uuid: str, include_archived: bool = True) -> list:
    """Load the full session bodies for a single user via the public read path.

    Scoped strictly to `user_uuid` even when global session filtering is off, and
    uses the pure `read_session` read (no enrichment / re-save). Backend-agnostic
    replacement for the historical per-user-dir glob used by relationship
    detection and the deprecated check-sessions endpoints.
    """
    bodies = []
    try:
        summaries = await get_user_sessions(user_uuid, include_archived=include_archived)
    except Exception as e:
        app_logger.warning(f"get_user_session_bodies: list failed for {user_uuid}: {e}")
        return bodies
    for summary in summaries:
        s_user = summary.get("user_uuid")
        if s_user and s_user != user_uuid:
            continue
        sid = summary.get("id")
        if not sid:
            continue
        body = await read_session(user_uuid, sid)
        if body:
            bodies.append(body)
    return bodies

async def get_all_session_bodies(include_archived: bool = True) -> list:
    """Load full session bodies across ALL users (admin / cross-user metrics).

    Backend-agnostic replacement for scanning every user's session directory.
    Pure reads; each body is loaded with its owning user's uuid. Falls back to a
    direct directory scan only if the session index is unavailable.
    """
    bodies = []
    indexed = await _query_session_index(
        user_uuid=None, include_archived=include_archived, filter_by_user=False
    )
    if indexed is not None:
        for summary in indexed:
            owner = summary.get("user_uuid")
            sid = summary.get("id")
            if not owner or not sid:
                continue
            body = await read_session(owner, sid)
            if body:
                bodies.append(body)
        return bodies
    # Index unavailable — delegate to the backend's full scan.
    try:
        return await get_session_backend().scan_all_bodies(include_archived=include_archived)
    except Exception as e:
        app_logger.warning(f"get_all_session_bodies: scan fallback failed: {e}")
    return bodies

async def find_session_owner(session_id: str) -> str | None:
    """Return the user_uuid that owns a session id (cross-user lookup via the index).

    Used by privileged read paths (e.g. VIEW_ALL_SESSIONS) to locate a session
    that does not belong to the requesting user without scanning the filesystem.
    """
    indexed = await _query_session_index(
        user_uuid=None, include_archived=True, filter_by_user=False
    )
    if indexed:
        for summary in indexed:
            if summary.get("id") == session_id:
                return summary.get("user_uuid")
    return None

async def get_all_sessions(user_uuid: str, limit: int = None, offset: int = 0, include_archived: bool = False,
                           filter_by_user: bool | None = None) -> dict:
    """
    Get all sessions for a user with optional pagination.

    Args:
        user_uuid: The user's UUID
        limit: Maximum number of sessions to return (None = all sessions)
        offset: Number of sessions to skip (for pagination)
        include_archived: Whether to include archived sessions (default: False)
        filter_by_user: Scope for THIS call (None = the deployment's
            ``APP_CONFIG.SESSIONS_FILTER_BY_USER``). A caller that may see every
            user's sessions passes ``False`` here — never by flipping the global, which
            every concurrent request (list AND single-session load) reads.

    Returns:
        dict with keys:
            - sessions: list of session summaries
            - total_count: total number of sessions (before pagination)
            - has_more: boolean indicating if more sessions exist
    """
    from trusted_data_agent.core.config import sessions_filtered_by_user

    if filter_by_user is None:
        filter_by_user = sessions_filtered_by_user()
    app_logger.debug(f"Getting all sessions for user '{user_uuid}'. Filter by user: {filter_by_user}, limit={limit}, offset={offset}")
    session_summaries = []

    # --- FAST PATH: Try session index first ---
    indexed = await _query_session_index(
        user_uuid,
        include_archived=include_archived,
        filter_by_user=filter_by_user
    )
    if indexed is not None:
        session_summaries = indexed
        app_logger.debug(f"Session index returned {len(session_summaries)} sessions (fast path)")
    else:
        # --- SLOW PATH: backend scan (index unavailable) ---
        # Returns summaries WITHOUT genie-metadata DB enrichment; that enrichment
        # is applied below, backend-agnostically.
        app_logger.debug("Session index unavailable, falling back to backend scan")
        session_summaries = await get_session_backend().scan_summaries(
            user_uuid,
            include_archived=include_archived,
            filter_by_user=filter_by_user,
        )

    # --- BATCH GENIE METADATA ENRICHMENT ---
    # Collect all slave session IDs that need metadata enrichment
    slave_session_ids = [
        s["id"] for s in session_summaries
        if s.get("genie_metadata", {}).get("is_genie_slave")
    ]

    # Batch query for all slave session metadata in a single database call
    if slave_session_ids:
        try:
            import sqlite3
            from trusted_data_agent.core.utils import get_project_root

            db_path = str(get_project_root() / "tda_auth.db")
            conn = sqlite_util.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Single query for all slave sessions
            placeholders = ','.join(['?'] * len(slave_session_ids))
            cursor.execute(f"""
                SELECT slave_session_id, nesting_level, slave_profile_tag
                FROM genie_session_links
                WHERE slave_session_id IN ({placeholders})
            """, slave_session_ids)

            rows = cursor.fetchall()

            # Build lookup map
            metadata_map = {row['slave_session_id']: dict(row) for row in rows}

            # Apply metadata to sessions, auto-repair orphaned slaves
            repaired_count = 0
            for session in session_summaries:
                session_id = session.get("id")
                if session_id in metadata_map:
                    genie_data = metadata_map[session_id]
                    session["genie_metadata"]["nesting_level"] = genie_data.get("nesting_level", 0)
                    session["genie_metadata"]["slave_profile_tag"] = genie_data.get("slave_profile_tag")
                elif session.get("genie_metadata", {}).get("is_genie_slave"):
                    # Auto-repair: insert missing link from session file data
                    genie_meta = session.get("genie_metadata", {})
                    parent_id = genie_meta.get("parent_session_id")
                    slave_profile_id = genie_meta.get("slave_profile_id", "")
                    slave_profile_tag = genie_meta.get("slave_profile_tag", "")
                    sequence_num = genie_meta.get("slave_sequence_number", 0)

                    if parent_id:
                        try:
                            cursor.execute("""
                                INSERT OR IGNORE INTO genie_session_links
                                (parent_session_id, slave_session_id, slave_profile_id,
                                 slave_profile_tag, user_uuid, execution_order, nesting_level)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (parent_id, session_id, slave_profile_id,
                                  slave_profile_tag, user_uuid, sequence_num, 0))
                            conn.commit()
                            session["genie_metadata"]["nesting_level"] = 0
                            repaired_count += 1
                            app_logger.info(f"Auto-repaired genie session link: {parent_id} -> {session_id}")
                        except Exception as repair_err:
                            app_logger.debug(f"Could not auto-repair genie link for {session_id}: {repair_err}")
                    else:
                        app_logger.debug(f"Orphaned genie slave session {session_id} has no parent_session_id")

            conn.close()

            if repaired_count > 0:
                app_logger.info(f"Auto-repaired {repaired_count} orphaned genie session links")
            app_logger.debug(f"Batch enriched {len(metadata_map)} genie slave sessions")
        except Exception as e:
            app_logger.error(f"Failed to batch enrich genie metadata: {e}", exc_info=True)
    # --- END BATCH GENIE METADATA ENRICHMENT ---


    # Filter out template generation sessions
    app_logger.debug(f"[Session Scan Complete] Found {len(session_summaries)} total sessions before filtering")
    for session in session_summaries:
        session_id = session.get("id", "unknown")[:12]
        is_slave = session.get("genie_metadata", {}).get("is_genie_slave", False)
        nesting_level = session.get("genie_metadata", {}).get("nesting_level", "N/A")
        app_logger.debug(f"[Session Scan] {session_id}... (slave={is_slave}, level={nesting_level})")

    session_summaries = [
        session for session in session_summaries
        if session.get("id") != RAGTemplateGenerator.TEMPLATE_SESSION_ID
    ]

    app_logger.debug(f"[After Template Filter] {len(session_summaries)} sessions remain")

    # Filter archived sessions unless explicitly requested
    if not include_archived:
        session_summaries = [
            session for session in session_summaries
            if not session.get("archived", False)
        ]
        app_logger.debug(f"[After Archived Filter] {len(session_summaries)} active sessions remain")
    else:
        app_logger.debug(f"[Include Archived] Keeping all {len(session_summaries)} sessions (including archived)")

    # Sort sessions with genie child sessions appearing directly after their parent
    # Build a lookup map for quick access
    session_by_id = {s.get("id"): s for s in session_summaries}

    # Separate parent sessions from child sessions
    parent_sessions = []
    slave_sessions_by_parent = {}  # parent_id -> list of slaves

    app_logger.debug(f"[Session Hierarchy] Processing {len(session_summaries)} total sessions")
    for session in session_summaries:
        genie_metadata = session.get("genie_metadata", {})
        parent_id = genie_metadata.get("parent_session_id")
        session_id = session.get("id")
        nesting_level = genie_metadata.get("nesting_level", 0)

        if genie_metadata.get("is_genie_slave") and parent_id:
            # This is a child session
            if parent_id not in slave_sessions_by_parent:
                slave_sessions_by_parent[parent_id] = []
            slave_sessions_by_parent[parent_id].append(session)
            app_logger.debug(f"[Session Hierarchy] Child: {session_id} (L{nesting_level}) -> parent: {parent_id}")
        else:
            # This is a parent/normal session
            parent_sessions.append(session)
            app_logger.debug(f"[Session Hierarchy] Parent/Normal: {session_id}")

    # Sort parent sessions by last_updated (most recent first)
    def sort_key(session):
        last_updated = session.get("last_updated") or session.get("created_at", "")
        if last_updated == "Unknown" or not last_updated:
            return datetime.min.isoformat()
        return last_updated

    parent_sessions.sort(key=sort_key, reverse=True)

    # Sort child sessions by sequence number (if available) or created_at
    for parent_id, slaves in slave_sessions_by_parent.items():
        slaves.sort(key=lambda s: s.get("genie_metadata", {}).get("slave_sequence_number", 0))

        # Mark last child for proper connector styling (└─ vs ├─)
        if slaves:
            slaves[-1]["genie_metadata"]["is_last_child"] = True
            app_logger.debug(f"[Session Hierarchy] Marked last child: {slaves[-1].get('id')} for parent {parent_id}")

    # Build final list with children inserted after their parents (recursively for nested Genies)
    def add_session_with_children(session_id, sessions_dict, added_ids, depth=0):
        """Recursively add a session and all its children (nested hierarchy support)"""
        indent = "  " * depth

        if session_id in added_ids:
            app_logger.debug(f"{indent}[Recursive Build] Skipping {session_id} (already added)")
            return  # Already added (prevent duplicates)

        if session_id not in sessions_dict:
            app_logger.debug(f"{indent}[Recursive Build] Session {session_id} not found in sessions_dict")
            return  # Session not found

        session = sessions_dict[session_id]
        final_sessions.append(session)
        added_ids.add(session_id)
        app_logger.debug(f"{indent}[Recursive Build] Added session {session_id} (depth={depth})")

        # Recursively add children
        if session_id in slave_sessions_by_parent:
            children = slave_sessions_by_parent[session_id]
            app_logger.debug(f"{indent}[Recursive Build] Session {session_id} has {len(children)} children")
            for child in children:
                add_session_with_children(child.get("id"), sessions_dict, added_ids, depth + 1)
        else:
            app_logger.debug(f"{indent}[Recursive Build] Session {session_id} has no children")

    final_sessions = []
    added_ids = set()

    # Add all parent sessions with their nested children
    app_logger.debug(f"[Recursive Build] Starting with {len(parent_sessions)} top-level sessions")
    for parent in parent_sessions:
        add_session_with_children(parent.get("id"), session_by_id, added_ids, depth=0)

    # Add any orphan children (parent not in list) at the end
    for parent_id, slaves in slave_sessions_by_parent.items():
        if parent_id not in session_by_id:
            final_sessions.extend(slaves)

    session_summaries = final_sessions

    # Calculate total count before pagination
    total_count = len(session_summaries)

    # Apply pagination if limit is specified
    if limit is not None:
        paginated_sessions = session_summaries[offset:offset + limit]
        has_more = offset + limit < total_count
    else:
        paginated_sessions = session_summaries
        has_more = False

    app_logger.debug(f"[FINAL RESULT] Returning {len(paginated_sessions)} of {total_count} sessions (offset={offset}, limit={limit}, has_more={has_more})")
    for i, session in enumerate(paginated_sessions[:10], 1):
        session_id = session.get("id", "unknown")[:12]
        is_slave = session.get("genie_metadata", {}).get("is_genie_slave", False)
        nesting_level = session.get("genie_metadata", {}).get("nesting_level", "N/A")
        name = session.get("name", "Unnamed")[:30]
        app_logger.debug(f"[FINAL] {i}. {session_id}... - {name} (slave={is_slave}, level={nesting_level})")

    return {
        "sessions": paginated_sessions,
        "total_count": total_count,
        "has_more": has_more
    }

async def delete_session(
    user_uuid: str,
    session_id: str,
    archived_reason: str = None,
    _cascade_slaves: bool = True,
) -> bool:
    """Archives a session by marking it as archived instead of deleting the file.

    When the archived session is a Genie parent (any number of coordinators
    participated in it), its child sessions are cascade-archived automatically so
    no orphan slave sessions are left behind. This includes children spawned by
    *every* coordinator that ran in the parent — not just the most recent one.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID to archive
        archived_reason: Optional reason for archiving (e.g., "User manually deleted session")
        _cascade_slaves: Internal flag; set False by ``cleanup_genie_slave_sessions``
            during its own recursive walk to avoid re-entering the cascade twice.
    """
    backend = get_session_backend()
    session_data = await backend.load(user_uuid, session_id)
    if session_data is None:
        app_logger.warning(f"Session '{session_id}' not found for archiving; treating as already gone (idempotent).")
        return True  # Idempotent: nothing to archive

    app_logger.info(f"Attempting to archive session: {session_id}")
    try:
        if True:
            # Mark as archived
            session_data["archived"] = True
            session_data["is_archived"] = True
            session_data["archived_at"] = datetime.now(timezone.utc).isoformat()
            if archived_reason:
                session_data["archived_reason"] = archived_reason

            # Persist the archived state via the backend
            saved = await backend.save(user_uuid, session_id, session_data)
            if not saved:
                app_logger.error(f"Failed to persist archived session '{session_id}'")
                return False

            app_logger.info(f"Successfully archived session: {session_id}")

            # Update session summary index with archived status
            asyncio.create_task(backend.upsert_summary(session_id, session_data))

            # Clean up uploads directory for this session
            _cleanup_session_uploads(user_uuid, session_id)

            # Clean up session vector store (in-memory: removes registry entry;
            # external: explicitly deletes per-module collections)
            try:
                from components.builtin.context_window.session_vector_store import (
                    destroy_session_store,
                )
                asyncio.create_task(destroy_session_store(session_id))
            except Exception:
                pass

            # Cascade: archive all child sessions registered against this parent.
            # Picks up slaves from *every* coordinator that participated, since
            # genie_session_links is keyed solely by parent_session_id.
            if _cascade_slaves:
                try:
                    cascade_reason = archived_reason or "Parent Genie session was archived"
                    await cleanup_genie_slave_sessions(
                        session_id, user_uuid, archived_reason=cascade_reason
                    )
                except Exception as _ce:
                    app_logger.error(
                        f"delete_session: cascade cleanup failed for {session_id}: {_ce}",
                        exc_info=True,
                    )

            return True # Indicate success
    except Exception as e:
        app_logger.error(f"Error archiving session '{session_id}': {e}", exc_info=True)
        return False # Indicate failure due to error


async def archive_sessions_by_profile(
    profile_id: str,
    user_uuid: str
) -> dict:
    """
    Archive all sessions for a user that reference the given profile_id.

    Uses the unified relationships REST endpoint to detect affected sessions,
    ensuring consistency with frontend warnings. This creates a single
    source of truth for session detection logic.

    Args:
        profile_id: The ID of the profile being deleted
        user_uuid: The user who owns the sessions

    Returns:
        dict: {
            "archived_count": int,
            "session_ids": [list of archived session IDs],
            "genie_children_archived": int
        }
    """
    archived_sessions = []
    genie_children = []

    try:
        # Call relationship analyzer directly (no HTTP call needed)
        from trusted_data_agent.core.relationship_analyzer import RelationshipAnalyzer

        app_logger.info(f"Analyzing relationships for profile {profile_id}")

        analyzer = RelationshipAnalyzer()
        result = await analyzer.analyze_artifact_relationships(
            artifact_type="profile",
            artifact_id=profile_id,
            user_uuid=user_uuid,
            include_archived=False,  # Only get active sessions
            limit=1000,  # High limit to get all sessions
            full=False
        )

        sessions_data = result.get("relationships", {}).get("sessions", {})
        affected_sessions = sessions_data.get("items", [])

        # Filter to only active sessions (not already archived)
        active_sessions = [s for s in affected_sessions if not s.get("archived")]

        app_logger.info(
            f"Unified endpoint found {len(active_sessions)} active sessions for profile {profile_id}"
        )

        # Archive each affected session
        for session_info in active_sessions:
            session_id = session_info.get("session_id")

            # Check if this is a Genie child session
            is_genie_child = session_info.get("is_genie_child", False)
            if is_genie_child:
                genie_children.append(session_id)

            try:
                # Use existing delete_session() archive function with reason
                success = await delete_session(
                    user_uuid,
                    session_id,
                    archived_reason=f"Profile '{profile_id}' was deleted"
                )

                if success:
                    archived_sessions.append(session_id)
                    app_logger.info(
                        f"Archived session {session_id} (profile {profile_id} deleted)"
                    )

            except Exception as e:
                app_logger.error(f"Failed to archive session {session_id}: {e}", exc_info=True)
                continue

        # Update genie_session_links table for archived children
        if genie_children:
            try:
                conn = get_auth_db_connection()
                cursor = conn.cursor()

                # Check if archived column exists (for backward compatibility)
                cursor.execute("PRAGMA table_info(genie_session_links)")
                columns = [row[1] for row in cursor.fetchall()]

                if "archived" in columns:
                    placeholders = ",".join("?" * len(genie_children))
                    cursor.execute(
                        f"UPDATE genie_session_links SET archived = 1 WHERE slave_session_id IN ({placeholders})",
                        genie_children
                    )
                    conn.commit()
                    app_logger.info(f"Updated genie_session_links archived flag for {len(genie_children)} children")

                conn.close()
            except Exception as e:
                app_logger.error(f"Error updating genie_session_links: {e}", exc_info=True)

        app_logger.info(
            f"Archive by profile complete: {len(archived_sessions)} sessions archived "
            f"({len(genie_children)} Genie children)"
        )

        return {
            "archived_count": len(archived_sessions),
            "session_ids": archived_sessions,
            "genie_children_archived": len(genie_children)
        }

    except Exception as e:
        app_logger.error(f"Error in archive_sessions_by_profile: {e}", exc_info=True)
        return {"archived_count": 0, "session_ids": [], "genie_children_archived": 0}


async def archive_sessions_by_collection(
    collection_id: str,
    user_uuid: str
) -> dict:
    """
    Archive all sessions for a user that use the given collection_id.

    Uses the unified relationships REST endpoint to detect affected sessions,
    ensuring consistency with frontend warnings. This creates a single
    source of truth for session detection logic.

    Args:
        collection_id: The ID of the collection being deleted
        user_uuid: The user who owns the sessions

    Returns:
        dict: {
            "archived_count": int,
            "session_ids": [list of archived session IDs]
        }
    """
    archived_sessions = []

    try:
        # Call unified relationships endpoint to get list of affected sessions
        from quart import current_app
        from trusted_data_agent.auth.middleware import create_internal_jwt
        import httpx

        # Create internal JWT for service-to-service call
        internal_token = create_internal_jwt(user_uuid)

        # Call relationship analyzer directly (no HTTP call needed)
        from trusted_data_agent.core.relationship_analyzer import RelationshipAnalyzer

        app_logger.info(f"[ARCHIVE] Analyzing relationships for collection {collection_id}")
        app_logger.info(f"[ARCHIVE] User: {user_uuid}, Collection: {collection_id}")

        analyzer = RelationshipAnalyzer()
        result = await analyzer.analyze_artifact_relationships(
            artifact_type="collection",
            artifact_id=collection_id,
            user_uuid=user_uuid,
            include_archived=False,  # Only get active sessions
            limit=1000,  # High limit to get all sessions
            full=False
        )

        sessions_data = result.get("relationships", {}).get("sessions", {})
        affected_sessions = sessions_data.get("items", [])

        # Filter to only active sessions (not already archived)
        active_sessions = [s for s in affected_sessions if not s.get("archived")]

        app_logger.info(f"[ARCHIVE] Relationship analyzer returned {len(affected_sessions)} total sessions")
        app_logger.info(f"[ARCHIVE] Filtered to {len(active_sessions)} active sessions to archive")
        for session in active_sessions:
            app_logger.info(f"[ARCHIVE]   - Session {session.get('session_id')} ({session.get('session_name', 'Unnamed')})")

        # Archive each affected session
        for session_info in active_sessions:
            session_id = session_info.get("session_id")

            try:
                # Use existing delete_session() archive function with reason
                app_logger.info(f"[ARCHIVE] Archiving session {session_id}...")
                success = await delete_session(
                    user_uuid,
                    session_id,
                    archived_reason=f"Collection '{collection_id}' was deleted"
                )
                app_logger.info(f"[ARCHIVE] delete_session() returned: {success}")
                if not success:
                    app_logger.error(f"[ARCHIVE] FAILED to archive session {session_id}")

                if success:
                    archived_sessions.append(session_id)
                    app_logger.info(
                        f"Archived session {session_id} (collection {collection_id} deleted)"
                    )

            except Exception as e:
                app_logger.error(f"Failed to archive session {session_id}: {e}", exc_info=True)
                continue

        app_logger.info(
            f"Archive by collection complete: {len(archived_sessions)} sessions archived"
        )

        return {
            "archived_count": len(archived_sessions),
            "session_ids": archived_sessions
        }

    except Exception as e:
        app_logger.error(f"Error in archive_sessions_by_collection: {e}", exc_info=True)
        return {"archived_count": 0, "session_ids": []}


async def archive_sessions_by_mcp_server(
    mcp_server_id: str,
    user_uuid: str
) -> dict:
    """
    Archive all sessions for a user that use the given MCP server.

    Uses the unified relationships REST endpoint to detect affected sessions,
    ensuring consistency with frontend warnings. This creates a single
    source of truth for session detection logic.

    Args:
        mcp_server_id: The ID of the MCP server being deleted
        user_uuid: The user who owns the sessions

    Returns:
        dict: {
            "archived_count": int,
            "session_ids": [list of archived session IDs]
        }
    """
    archived_sessions = []

    try:
        # Call relationship analyzer directly (no HTTP call needed)
        from trusted_data_agent.core.relationship_analyzer import RelationshipAnalyzer

        app_logger.info(f"Analyzing relationships for MCP server {mcp_server_id}")

        analyzer = RelationshipAnalyzer()
        result = await analyzer.analyze_artifact_relationships(
            artifact_type="mcp-server",
            artifact_id=mcp_server_id,
            user_uuid=user_uuid,
            include_archived=False,  # Only get active sessions
            limit=1000,  # High limit to get all sessions
            full=False
        )

        sessions_data = result.get("relationships", {}).get("sessions", {})
        affected_sessions = sessions_data.get("items", [])

        # Filter to only active sessions (not already archived)
        active_sessions = [s for s in affected_sessions if not s.get("archived")]

        app_logger.info(
            f"Unified endpoint found {len(active_sessions)} active sessions for MCP server {mcp_server_id}"
        )

        # Archive each affected session
        for session_info in active_sessions:
            session_id = session_info.get("session_id")

            try:
                # Use existing delete_session() archive function with reason
                success = await delete_session(
                    user_uuid,
                    session_id,
                    archived_reason=f"MCP server '{mcp_server_id}' was deleted"
                )

                if success:
                    archived_sessions.append(session_id)
                    app_logger.info(
                        f"Archived session {session_id} (MCP server {mcp_server_id} deleted)"
                    )

            except Exception as e:
                app_logger.error(f"Failed to archive session {session_id}: {e}", exc_info=True)
                continue

        app_logger.info(
            f"Archive by MCP server complete: {len(archived_sessions)} sessions archived"
        )

        return {
            "archived_count": len(archived_sessions),
            "session_ids": archived_sessions
        }

    except Exception as e:
        app_logger.error(f"Error in archive_sessions_by_mcp_server: {e}", exc_info=True)
        return {"archived_count": 0, "session_ids": []}


async def archive_sessions_by_llm_config(
    llm_config_id: str,
    user_uuid: str
) -> dict:
    """
    Archive all sessions for a user that use the given LLM configuration.

    Uses the unified relationships REST endpoint to detect affected sessions,
    ensuring consistency with frontend warnings. This creates a single
    source of truth for session detection logic.

    Args:
        llm_config_id: The ID of the LLM config being deleted
        user_uuid: The user who owns the sessions

    Returns:
        dict: {
            "archived_count": int,
            "session_ids": [list of archived session IDs]
        }
    """
    archived_sessions = []

    try:
        # Call relationship analyzer directly (no HTTP call needed)
        from trusted_data_agent.core.relationship_analyzer import RelationshipAnalyzer

        app_logger.info(f"Analyzing relationships for LLM config {llm_config_id}")

        analyzer = RelationshipAnalyzer()
        result = await analyzer.analyze_artifact_relationships(
            artifact_type="llm-config",
            artifact_id=llm_config_id,
            user_uuid=user_uuid,
            include_archived=False,  # Only get active sessions
            limit=1000,  # High limit to get all sessions
            full=False
        )

        sessions_data = result.get("relationships", {}).get("sessions", {})
        affected_sessions = sessions_data.get("items", [])

        # Filter to only active sessions (not already archived)
        active_sessions = [s for s in affected_sessions if not s.get("archived")]

        app_logger.info(
            f"Unified endpoint found {len(active_sessions)} active sessions for LLM config {llm_config_id}"
        )

        # Archive each affected session
        for session_info in active_sessions:
            session_id = session_info.get("session_id")

            try:
                # Use existing delete_session() archive function with reason
                success = await delete_session(
                    user_uuid,
                    session_id,
                    archived_reason=f"LLM config '{llm_config_id}' was deleted"
                )

                if success:
                    archived_sessions.append(session_id)
                    app_logger.info(
                        f"Archived session {session_id} (LLM config {llm_config_id} deleted)"
                    )

            except Exception as e:
                app_logger.error(f"Failed to archive session {session_id}: {e}", exc_info=True)
                continue

        app_logger.info(
            f"Archive by LLM config complete: {len(archived_sessions)} sessions archived"
        )

        return {
            "archived_count": len(archived_sessions),
            "session_ids": archived_sessions
        }

    except Exception as e:
        app_logger.error(f"Error in archive_sessions_by_llm_config: {e}", exc_info=True)
        return {"archived_count": 0, "session_ids": []}


def _cleanup_session_uploads(user_uuid: str, session_id: str):
    """A session was deleted/archived: its unkept attachments go now (object, wrapped key,
    row), with its manifest and uploads folder. Attachments the user kept stay in Files
    (PROP-010 §4.6)."""
    try:
        from trusted_data_agent.filestore import session_files
        session_files.purge_session(user_uuid, session_id)
        app_logger.info(f"Cleaned up uploads for session {session_id}")
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Failed to clean up uploads for session {session_id}: {e}")

# --- MODIFICATION START: Rename and refactor add_to_history ---
async def add_message_to_histories(user_uuid: str, session_id: str, role: str, content: str, html_content: str | None = None, source: str | None = None, profile_tag: str | None = None, is_session_primer: bool = False, attachments: list | None = None, extension_specs: list | None = None, skill_specs: list | None = None, tool_context: str | None = None, origin: str | None = None):
    """
    Adds a message to the appropriate histories, decoupling UI from LLM context.
    - `content` (plain text) is *always* added to the LLM's chat_object.
    - `html_content` (if provided) is added to the UI's session_history.
    - If `html_content` is not provided, `content` is used for the UI.
    - `profile_tag` (if provided) stores which profile was used for this message.
    - `is_session_primer` (if True) marks this message as part of session initialization.
    """
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            app_logger.warning(f"Could not add history: Session {session_id} not found for user {user_uuid}.")
            return

        # --- 1. Add to UI History (session_history) ---
        ui_content = html_content if html_content is not None else content

        session_history = session_data.setdefault('session_history', [])

        # S8 — the turn's lineage relation (a regenerate / an edit), armed at the execution seam;
        # None ⇒ every line below behaves exactly as before.
        _rel = None
        try:
            from trusted_data_agent.agent import turn_lineage as _tl
            _rel = _tl.current() if _tl.enabled() else None
            if _rel is not None and _rel.kind == "edit" and role == "user":
                # the edited message is the head of a new branch: register it BEFORE the message
                # lands, so the displaced turns are already out of the view it joins
                _tl.register_head(session_data, _rel.new_turn, _rel.of)
        except Exception as _le:  # noqa: BLE001 — lineage must never break a message write
            app_logger.warning(f"turn-lineage: relation skipped on message write: {_le}")
            _rel = None

        # Determine the next turn number. A user message opens the NEXT turn after the highest
        # one in the history (a sibling answer may sit mid-history — S8 — so the last message is
        # not always the highest); an assistant message belongs to the turn just opened.
        next_turn_number = 1
        if session_history:
            last_message = session_history[-1]
            last_turn_number = last_message.get('turn_number', 0)
            if role == 'user':
                next_turn_number = max((m.get('turn_number', 0) or 0) for m in session_history if isinstance(m, dict)) + 1
            else:
                next_turn_number = last_turn_number
        if _rel is not None:
            next_turn_number = _tl.message_turn_number(_rel, role, next_turn_number)

        message_to_append = {
            'role': role,
            'content': ui_content,
            'isValid': True,
            'turn_number': next_turn_number,
        }
        if _rel is not None:
            _tl.stamp_message(_rel, role, message_to_append)

        if source:
            message_to_append['source'] = source
        # Orchestration provenance (flow / genie) — the driver that produced this
        # turn, recorded authoritatively at execution time. Orthogonal to `source`
        # (transport), so a flow/genie turn keeps its Rest Call tag and adds its own.
        if origin:
            message_to_append['origin'] = origin
        if profile_tag:
            message_to_append['profile_tag'] = profile_tag
        if is_session_primer:
            message_to_append['is_session_primer'] = True
        if attachments and role == 'user':
            # the chip's facts (size, image or text) come from the platform's own upload record,
            # not the caller: a REST client sends the documented {file_id, filename} and nothing else
            try:
                from trusted_data_agent.filestore import session_files as _sf
                _man = _sf.readable_manifest(user_uuid, session_id) or {}
            except Exception:  # noqa: BLE001
                _man = {}

            def _stored(att: dict) -> dict:
                out = {k: v for k, v in att.items() if k != 'extracted_text'}
                entry = _man.get(att.get('file_id')) if isinstance(att, dict) else None
                if entry:
                    if not isinstance(out.get('file_size'), (int, float)) and entry.get('file_size') is not None:
                        out['file_size'] = entry['file_size']
                    out.setdefault('is_image', bool(entry.get('is_image')))
                    if not out.get('filename') and entry.get('filename'):
                        out['filename'] = entry['filename']
                return out

            message_to_append['attachments'] = [_stored(att) for att in attachments if isinstance(att, dict)]
        if extension_specs and role == 'user':
            message_to_append['extension_specs'] = extension_specs
        if skill_specs and role == 'user':
            message_to_append['skill_specs'] = skill_specs

        _ui_at = _tl.insert_index(session_history, _rel, role) if _rel is not None else None
        if _ui_at is None:
            session_history.append(message_to_append)
        else:
            session_history.insert(_ui_at, message_to_append)

        # --- 2. Add to LLM History (chat_object) ---
        provider_in_session = session_data.get("license_info", {}).get("provider")
        current_provider = APP_CONFIG.CURRENT_PROVIDER
        provider = provider_in_session or current_provider
        if not provider:
            app_logger.warning(f"Could not determine LLM provider for role conversion in session {session_id}. Defaulting role.")
            provider = "Unknown"

        llm_role = 'model' if role == 'assistant' and provider == 'Google' else role

        chat_object_history = session_data.setdefault('chat_object', [])

        llm_turn_number = 1
        if chat_object_history:
            last_llm_message = chat_object_history[-1]
            last_llm_turn = last_llm_message.get('turn_number', 0)
            if llm_role == 'user':
                llm_turn_number = max((m.get('turn_number', 0) or 0) for m in chat_object_history if isinstance(m, dict)) + 1
            else:
                llm_turn_number = last_llm_turn
        if _rel is not None:
            llm_turn_number = _tl.message_turn_number(_rel, role, llm_turn_number)

        llm_message = {
            'role': llm_role,
            'content': content,
            'isValid': True,
            'turn_number': llm_turn_number
        }
        if attachments and role == 'user':
            llm_message['has_attachments'] = True
        if tool_context and role == 'assistant':
            llm_message['tool_context'] = tool_context
        if _rel is not None:
            _tl.stamp_message(_rel, role, llm_message)
        _llm_at = _tl.insert_index(chat_object_history, _rel, role) if _rel is not None else None
        if _llm_at is None:
            chat_object_history.append(llm_message)
        else:
            chat_object_history.insert(_llm_at, llm_message)

async def update_session_name(user_uuid: str, session_id: str, new_name: str):
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            app_logger.warning(f"Could not update name: Session {session_id} not found for user {user_uuid}.")
            return
        session_data['name'] = new_name

    # Post-save operations (outside lock)
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.consumption_manager import ConsumptionManager

        with get_db_session() as db_session:
            manager = ConsumptionManager(db_session)
            manager.update_session_name(user_uuid, session_id, new_name)
    except Exception as e:
        app_logger.warning(f"Failed to update session name in consumption database: {e}")

    notification_queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
    if notification_queues:
        notification = {
            "type": "session_name_update",
            "payload": {"session_id": session_id, "newName": new_name}
        }
        for queue in notification_queues:
            asyncio.create_task(queue.put(notification))


async def set_input_default(user_uuid: str, session_id: str, key: str, value) -> None:
    """PROP-011 decision 10: an answer the person asked to hold for the rest of this
    conversation (`input_defaults`); the ledger reads it before a raiser asks."""
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            raise LookupError(f"session {session_id} not found")
        d = session_data.get("input_defaults")
        if not isinstance(d, dict):
            d = {}
        d[str(key)] = value
        session_data["input_defaults"] = d


async def set_platform_asked_by(user_uuid: str, session_id: str, asked_by: dict) -> None:
    """Record that another agent opened this conversation to ask a question (TDA_Platform's
    ``ask``): the asking conversation, agent, turn and the chain depth the depth limit reads.
    Raises when the session is missing — an unrecorded ask would leave the depth unbounded."""
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            raise LookupError(f"session {session_id} not found")
        session_data['platform_asked_by'] = dict(asked_by)


async def set_session_context_limit(user_uuid: str, session_id: str, context_limit: int | None):
    """Set or clear a session-level context limit override."""
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            return
        if context_limit is None:
            session_data.pop('session_context_limit_override', None)
        else:
            session_data['session_context_limit_override'] = int(context_limit)


async def update_token_count(user_uuid: str, session_id: str, input_tokens: int, output_tokens: int):
    """Updates the token counts for a given session."""
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            app_logger.warning(f"Could not update tokens: Session {session_id} not found for user {user_uuid}.")
            return
        session_data['input_tokens'] = session_data.get('input_tokens', 0) + input_tokens
        session_data['output_tokens'] = session_data.get('output_tokens', 0) + output_tokens

    # Post-save operations (outside lock for minimal lock hold time)
    try:
        from trusted_data_agent.auth.token_quota import record_token_usage
        record_token_usage(user_uuid, input_tokens, output_tokens)
        app_logger.debug(f"Recorded token usage for user {user_uuid}: input={input_tokens}, output={output_tokens}")
    except Exception as e:
        app_logger.error(f"Failed to record token usage for user {user_uuid}: {e}")

    # --- CONSUMPTION TRACKING ---
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.consumption_manager import ConsumptionManager

        with get_db_session() as db_session:
            manager = ConsumptionManager(db_session)
            manager.increment_request_counter(user_uuid)
            app_logger.debug(f"Incremented request counter for user {user_uuid}")
    except Exception as e:
        app_logger.warning(f"Failed to update consumption tracking for user {user_uuid}: {e}")


async def update_models_used(user_uuid: str, session_id: str, provider: str, model: str, profile_tag: str | None = None, planning_phase: str | None = None):
    """
    Adds the current model/profile to the list used in the session.

    Args:
        planning_phase: Optional planning phase indicator ("strategic" | "tactical" | "conversation")
                       for dual-model tracking
    """
    app_logger.debug(f"update_models_used called for session {session_id} with provider={provider}, model={model}, profile_tag={profile_tag}, planning_phase={planning_phase}")
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            app_logger.warning(f"Could not update models used: Session {session_id} not found for user {user_uuid}.")
            return
        # Keep models_used for backwards compatibility
        models_used = session_data.get('models_used', [])
        model_string = f"{provider}/{model}"
        if model_string not in models_used:
            models_used.append(model_string)
            session_data['models_used'] = models_used

        # Track strategic vs tactical usage (NEW: Dual-model traceability)
        if planning_phase and planning_phase in ["strategic", "tactical", "tactical_fastpath", "conversation"]:
            if "dual_model_usage" not in session_data:
                session_data["dual_model_usage"] = {
                    "strategic": [],
                    "tactical": [],
                    "tactical_fastpath": [],
                    "conversation": []
                }

            phase_models = session_data["dual_model_usage"][planning_phase]
            if model_string not in phase_models:
                phase_models.append(model_string)
                app_logger.debug(f"[Dual-Model] Tracked {model_string} as {planning_phase} model")

        # Add profile tag to profile_tags_used
        if profile_tag:
            profile_tags_used = session_data.get('profile_tags_used', [])
            if profile_tag not in profile_tags_used:
                profile_tags_used.append(profile_tag)
                session_data['profile_tags_used'] = profile_tags_used

        session_data['provider'] = provider
        session_data['model'] = model
        session_data['profile_tag'] = profile_tag


async def ensure_primary_profile_tag(user_uuid: str, session_id: str, profile_tag: str | None):
    """Record `profile_tag` as the session's PRIMARY profile — moved to the front of
    `profile_tags_used` (the sidebar's main badge / `profile_tags_used[0]`) and set as
    `profile_tag`. Used to surface a flow's MAIN orchestrating profile in the session
    card ALONGSIDE the per-node LLM profiles that `update_models_used` records during
    execution (those are kept, just after the primary). No-op without a profile tag."""
    if not profile_tag:
        return
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            return
        used = [t for t in session_data.get('profile_tags_used', []) if t != profile_tag]
        used.insert(0, profile_tag)
        session_data['profile_tags_used'] = used
        session_data['profile_tag'] = profile_tag


async def _ingest_turn_to_session_store(
    session_id: str,
    user_uuid: str,
    turn_data: dict,
    turn_number: int,
) -> None:
    """Background task: ingest the completed turn into the session vector store.

    Populates three module sub-collections so RAG condensation has warm data
    by the next turn:
      - workflow_history: one turn-summary chunk
      - plan_hydration:   one chunk per successful tool result
      - conversation_history: the latest user+assistant message pair
    """
    try:
        from components.builtin.context_window.session_vector_store import get_session_store
        from trusted_data_agent.vectorstore.types import VectorDocument

        session_store = get_session_store(session_id, user_uuid)
        execution_trace = turn_data.get("execution_trace", [])

        # ── workflow_history ──────────────────────────────────────────────────
        from trusted_data_agent.core.trace_shape import phase_action, phase_output, phase_rows
        if execution_trace:
            tool_lines = []
            for phase in execution_trace:
                action = phase_action(phase)          # ONE reader of the {action, result} shape
                output = phase_output(phase)
                tool_name = action.get("tool_name", "unknown")
                status = output.get("status", "unknown")
                results = phase_rows(phase)
                count = f" ({len(results)} rows)" if isinstance(results, list) else ""
                tool_lines.append(f"  - {tool_name}: {status}{count}")

            wf_content = f"Turn #{turn_number}:\n" + "\n".join(tool_lines)
            await session_store.ingest(
                "workflow_history",
                [VectorDocument(
                    id=f"{session_id}__wf__{turn_number}",
                    content=wf_content,
                    metadata={"turn_number": turn_number, "session_id": session_id},
                )],
            )

        # ── plan_hydration ────────────────────────────────────────────────────
        ph_chunks = []
        for phase_idx, phase in enumerate(execution_trace):
            action = phase_action(phase)
            output = phase_output(phase)
            if output.get("status") != "success":
                continue
            tool_name = action.get("tool_name", "unknown")
            if str(tool_name).startswith("TDA_"):
                continue
            results = phase_rows(phase) or []
            if isinstance(results, list) and len(results) > 5:
                result_text = (
                    f"{len(results)} rows. First 3: "
                    f"{json.dumps(results[:3], default=str)}"
                )
            else:
                result_text = json.dumps(results, default=str)
            ph_chunks.append(VectorDocument(
                id=f"{session_id}__plan__{turn_number}__phase{phase_idx}",
                content=f"{tool_name}:\n{result_text}",
                metadata={
                    "tool_name": tool_name,
                    "phase": phase_idx,
                    "turn_number": turn_number,
                },
            ))
        if ph_chunks:
            await session_store.ingest("plan_hydration", ph_chunks)

        # ── conversation_history ──────────────────────────────────────────────
        session_data = await get_session(user_uuid, session_id)
        if session_data:
            chat_object = session_data.get("chat_object", [])
            valid_msgs = [
                m for m in chat_object
                if m.get("role") and m.get("content") and m.get("isValid") is not False
            ]
            last_user = next(
                (m for m in reversed(valid_msgs) if m["role"] == "user"), None
            )
            last_asst = next(
                (m for m in reversed(valid_msgs) if m["role"] in ("assistant", "model")), None
            )
            if last_user and last_asst:
                conv_content = (
                    f"[user]: {last_user['content']}\n\n"
                    f"[assistant]: {last_asst['content']}"
                )
                await session_store.ingest(
                    "conversation_history",
                    [VectorDocument(
                        id=f"{session_id}__conv__{turn_number}",
                        content=conv_content,
                        metadata={"turn_number": turn_number, "session_id": session_id},
                    )],
                )

    except Exception as _e:
        app_logger.debug(
            f"Session store background ingestion skipped for {session_id}: {_e}"
        )


async def stamp_last_turn(user_uuid: str, session_id: str, turn: Any, fields: dict,
                          append_text: Optional[str] = None) -> bool:
    """Stamp ``fields`` onto a persisted turn record (by its ``turn`` number; the last record
    when ``turn`` is None) AFTER the engine saved it — the one place a cross-engine fact lands
    (PROP-011: a turn that stopped to ask reads ``input_required``, whichever engine ran it).
    ``append_text`` is added to the turn's ``final_summary_text`` and to the last assistant
    entry of ``chat_object`` when they do not carry it yet, so the question the person was
    asked is in the LLM-facing history too. Never raises; False when nothing was stamped."""
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                return False
            wf = ((session_data.get("last_turn_data") or {}).get("workflow_history") or [])
            rec = None
            if turn is not None:
                for t in reversed(wf):
                    try:
                        if int(t.get("turn")) == int(turn):
                            rec = t
                            break
                    except (TypeError, ValueError):
                        continue
            if rec is None and wf:
                rec = wf[-1]
            if rec is None:
                return False
            rec.update(fields or {})
            if append_text:
                fst = rec.get("final_summary_text")
                if isinstance(fst, str) and append_text.lower() not in fst.lower():
                    rec["final_summary_text"] = (fst.rstrip() + "\n\n" + append_text).strip()
                chat = session_data.get("chat_object")
                if isinstance(chat, list):
                    for m in reversed(chat):
                        if isinstance(m, dict) and m.get("role") == "assistant":
                            c = m.get("content")
                            if isinstance(c, str) and append_text.lower() not in c.lower():
                                m["content"] = (c.rstrip() + "\n\n" + append_text).strip()
                            break
            return True
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"stamp_last_turn skipped for session {session_id}: {e}")
        return False


async def update_last_turn_data(user_uuid: str, session_id: str, turn_data: dict):
    """Saves the most recent turn's action history and plans to the session file."""
    # Capture values needed for post-save consumption tracking
    _session_name = None
    _consumption_data = None

    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            app_logger.warning(f"Could not update last turn data: Session {session_id} not found for user {user_uuid}.")
            return
        # Ensure the structure exists
        if "last_turn_data" not in session_data:
            session_data["last_turn_data"] = {}
        if "workflow_history" not in session_data["last_turn_data"] or not isinstance(session_data["last_turn_data"]["workflow_history"], list):
            session_data["last_turn_data"]["workflow_history"] = []

        if isinstance(turn_data, dict):
            turn_data["isValid"] = True
            # Findings — the verified observations this turn's tools returned, extracted
            # DETERMINISTICALLY from the record (zero LLM tokens) for every engine, so the
            # `findings` context module can re-present them on the following turns. A lane
            # that stamped its own (ReAct, from its captured data results) is left alone.
            if "findings" not in turn_data:
                try:
                    from trusted_data_agent.agent import findings as _findings
                    turn_data["findings"] = _findings.from_execution_trace(
                        turn_data.get("execution_trace") or [])
                except Exception as _fe:  # noqa: BLE001 — an extractor must never break a save
                    app_logger.debug(f"findings: stamp skipped: {_fe}")

        # The turn's cost basis: `estimated` when its model has no price row and was billed at the
        # fallback rate — so a consumer can tell an estimate from a price (OC-F12). An engine that
        # already stamped wins. A turn without a cost is left unlabelled.
        if isinstance(turn_data, dict):
            try:
                _spent = (float(turn_data.get("turn_cost") or 0) > 0
                          or (turn_data.get("turn_input_tokens") or 0) or (turn_data.get("turn_output_tokens") or 0))
                if (turn_data.get("turn_cost") is not None and "cost_basis" not in turn_data and _spent
                        and turn_data.get("provider") != "flow"):
                    from trusted_data_agent.core.cost_manager import get_cost_manager as _gcm
                    turn_data["cost_basis"] = _gcm().cost_basis(turn_data.get("provider"), turn_data.get("model"))
            except Exception:  # noqa: BLE001 — a label must never break persistence
                pass

        # Agent Authority (A17) — stamp the scope IN FORCE onto the turn. THE chokepoint:
        # every engine's turn dict lands here, so one read covers all five without
        # threading a signature through each. The stamp is sealed at execution time by
        # `execution_service` (never resolved later — re-resolving would grade a historical
        # turn against today's authorityConfig, the same retroactive-falsification trap the
        # SIGNED stamp was sealed to avoid). Outside a turn the contextvar is unset → no
        # stamp → byte-identical baseline. `setdefault`: an engine that already stamped
        # wins, so this can never overwrite a more specific fact.
        if isinstance(turn_data, dict):
            try:
                from trusted_data_agent.core import agent_authority as _aid
                _auth = _aid.get_turn_authority()
                if _auth:
                    turn_data.setdefault("authority", _auth)
            except Exception as _ae:  # noqa: BLE001 — a stamp must never break a turn
                app_logger.debug(f"agent-authority: turn stamp skipped: {_ae}")
            # OBDA-ACT (PROP-008 §5.4) — the turn's action record: every invocation of a signed
            # action (performed, observed, parked, refused). Empty ⇒ nothing added ⇒
            # byte-identical baseline. The performed ones are what the provenance chain
            # seals, re-derivable from this stamp (`action_perform.receipt_content`).
            try:
                from trusted_data_agent.core import action_perform as _act_perf
                _acts = _act_perf.turn_actions()
                if _acts:
                    turn_data.setdefault("actions", [{k: v for k, v in r.items() if not k.startswith("_")}
                                                     for r in _acts])
            except Exception as _xe:  # noqa: BLE001
                app_logger.debug(f"act: turn stamp skipped: {_xe}")
            # PROP-009 — the turn's bill of materials (the signed parts it ran with), the
            # stamp the sealed `trust.bom` step is re-derived from. None (suite absent,
            # agent unresolvable) ⇒ nothing added.
            try:
                from trusted_data_agent.core.trust import evidence as _evidence
                _bom = _evidence.current_turn_bom()
                if _bom:
                    turn_data.setdefault("bom", {k: v for k, v in _bom.items()
                                                 if not str(k).startswith("_")})
                # The content-free evidence record + usage index (PROP-009 §4.3): written
                # here, at the one chokepoint, for any turn that carries a BOM — never by
                # scanning session files. Outlives the session; fails soft.
                if isinstance(turn_data.get("bom"), dict):
                    from trusted_data_agent.core.trust import evidence_index as _evx
                    _evx.record_turn(user_uuid, session_id, turn_data)
            except Exception as _be:  # noqa: BLE001
                app_logger.debug(f"evidence: turn stamp skipped: {_be}")
            # S2 — the turn's prompt-cache reading (same chokepoint, same reasoning: one
            # read covers every engine). None when caching was never armed and no
            # provider reported a cache ⇒ nothing added ⇒ byte-identical baseline.
            try:
                from trusted_data_agent.agent import prompt_cache as _pc
                _cache_stamp = _pc.stamp_for_turn()
                if _cache_stamp:
                    turn_data.setdefault("cache", _cache_stamp)
            except Exception as _ce:  # noqa: BLE001
                app_logger.debug(f"prompt-cache: turn stamp skipped: {_ce}")
            # S4 — the turn's recovery record (retries, continuations, the fallback that served,
            # a refusal). None when nothing was recovered ⇒ nothing added ⇒ byte-identical.
            try:
                from trusted_data_agent.agent import resilience_envelope as _renv
                _res_stamp = _renv.stamp_for_turn()
                if _res_stamp:
                    turn_data.setdefault("resilience", _res_stamp)
            except Exception as _rse:  # noqa: BLE001
                app_logger.debug(f"resilience: turn stamp skipped: {_rse}")
            # S7 — the turn's outcome loop (drafts, findings before/after, cleared). None when no
            # revision happened ⇒ nothing added ⇒ byte-identical.
            try:
                from trusted_data_agent.agent import outcome_loop as _oloop
                _outcome_stamp = _oloop.stamp_for_turn()
                if _outcome_stamp:
                    turn_data.setdefault("outcome", _outcome_stamp)
            except Exception as _ole:  # noqa: BLE001
                app_logger.debug(f"outcome-loop: turn stamp skipped: {_ole}")
            # S6 — the turn's tool surface (offered / held / denied / widened / asked_denied).
            # None when no governed surface was armed ⇒ nothing added ⇒ byte-identical.
            try:
                from trusted_data_agent.agent import tool_surface as _tsurf
                _surface_stamp = _tsurf.stamp_for_turn()
                if _surface_stamp:
                    turn_data.setdefault("tool_surface", _surface_stamp)
            except Exception as _tse:  # noqa: BLE001
                app_logger.debug(f"tool-surface: turn stamp skipped: {_tse}")
            # S5 — the turn's budget state (used/total per unit, exhausted, graceful, the finish
            # line). None when no budget was armed ⇒ nothing added ⇒ byte-identical.
            try:
                from trusted_data_agent.agent import turn_budget as _tbud
                _budget_stamp = _tbud.stamp_for_turn()
                if _budget_stamp:
                    turn_data.setdefault("budget", _budget_stamp)
            except Exception as _bse:  # noqa: BLE001
                app_logger.debug(f"turn-budget: turn stamp skipped: {_bse}")
            # S3 — the turn's compute receipts (+ a compact statement index), the record the
            # provenance verifier re-derives the sealed `compute_step` receipts from and the
            # lineage dialog reads the rows from. None when nothing was computed.
            try:
                from trusted_data_agent.agent import result_sets as _rsets
                _compute_stamp = _rsets.stamp_for_turn(session_id)
                if _compute_stamp:
                    turn_data.setdefault("compute", _compute_stamp)
            except Exception as _cse:  # noqa: BLE001
                app_logger.debug(f"compute-lineage: turn stamp skipped: {_cse}")
            # S9 — the turn's attachment sources (what the platform read from each upload, its
            # method, verification, tables + regions, and the verified evidence text the
            # post-hoc scorer reads). None when nothing was attached ⇒ byte-identical.
            try:
                from trusted_data_agent.llm import image_sources as _isrc
                _as_stamp = _isrc.stamp_for_turn()
                if _as_stamp:
                    turn_data.setdefault("attachment_sources", _as_stamp)
            except Exception as _ase:  # noqa: BLE001
                app_logger.debug(f"attachment-sources: turn stamp skipped: {_ase}")
            # PROP-010 — the attachments the turn read (ids + names, never their text): the
            # scorers read them as evidence through the guarded manifest. None ⇒ byte-identical.
            try:
                from trusted_data_agent.filestore import session_files as _sfiles
                _ev_stamp = _sfiles.stamp_for_turn()
                if _ev_stamp:
                    turn_data.setdefault("attachment_evidence", _ev_stamp)
            except Exception as _eve:  # noqa: BLE001
                app_logger.debug(f"attachment-evidence: turn stamp skipped: {_eve}")
            # S10 — the steers this turn considered (where) and the ones still waiting at its
            # end (carried into the next turn). None when nothing was steered ⇒ byte-identical.
            try:
                from trusted_data_agent.core import steer as _steer
                _steer_stamp = _steer.stamp_for_turn(session_id)
                if _steer_stamp:
                    turn_data.setdefault("steers", _steer_stamp)
            except Exception as _sse:  # noqa: BLE001
                app_logger.debug(f"steer: turn stamp skipped: {_sse}")

        # --- EFFICIENCY TRACKING ---
        workflow_history = session_data["last_turn_data"]["workflow_history"]
        if len(workflow_history) > 0 and isinstance(turn_data, dict):
            previous_turn = workflow_history[-1]
            previous_output = previous_turn.get('turn_output_tokens', 0)
            current_output = turn_data.get('turn_output_tokens', 0)
            previous_had_rag = previous_turn.get('rag_source_collection_id') is not None

            provider = turn_data.get('provider', 'Unknown')
            model = turn_data.get('model', 'unknown')
            output_tokens = turn_data.get('turn_output_tokens', 0)

            cost_per_token = 0.0
            if output_tokens > 0:
                from trusted_data_agent.core.cost_manager import get_cost_manager
                cost_manager = get_cost_manager()
                turn_cost = cost_manager.calculate_cost(provider, model, 0, output_tokens)
                cost_per_token = turn_cost / output_tokens if output_tokens > 0 else 0.0

            if previous_output > 0:
                from trusted_data_agent.core.efficiency_tracker import get_efficiency_tracker
                tracker = get_efficiency_tracker()
                tracker.record_improvement(
                    session_id=session_id,
                    turn_index=len(workflow_history),
                    previous_output_tokens=previous_output,
                    current_output_tokens=current_output,
                    had_rag=previous_had_rag,
                    cost_per_output_token=cost_per_token,
                    user_uuid=user_uuid
                )

                current_turn_has_rag = turn_data.get('rag_source_collection_id') is not None
                if current_turn_has_rag and current_output < previous_output:
                    tokens_saved = previous_output - current_output
                    turn_data['rag_efficiency_gain'] = tokens_saved
                    app_logger.debug(f"RAG efficiency gain for current turn: {tokens_saved} tokens saved")

        # Append the new turn data
        # S8 — the record's lineage keys (a regenerate's `sibling_of`, an edit's `branch_of`, the
        # branch it belongs to), stamped at THE chokepoint every engine reaches; a regenerate is
        # adopted on arrival (the reason to regenerate is to try again — Adopt lets the reader
        # go back), which moves only validity: every sibling stays in the record. Nothing here
        # ever removes or rewrites a turn. Unset relation + no branches ⇒ nothing stamped.
        _lineage_rel = None
        try:
            from trusted_data_agent.agent import turn_lineage as _tl
            if _tl.enabled() and isinstance(turn_data, dict):
                _lineage_rel = _tl.current()
                _tl.stamp_record(session_data, _lineage_rel, turn_data)
        except Exception as _le:  # noqa: BLE001
            app_logger.warning(f"turn-lineage: record stamp skipped: {_le}")
            _lineage_rel = None
        session_data["last_turn_data"]["workflow_history"].append(turn_data)
        if _lineage_rel is not None:
            try:
                _tl.after_record(session_data, _lineage_rel, turn_data)
            except Exception as _le:  # noqa: BLE001
                app_logger.warning(f"turn-lineage: adoption on arrival skipped: {_le}")

        # Capture data for post-save consumption tracking (avoid secondary load)
        _session_name = session_data.get('name') or 'Untitled Session'
        turn_number = len(session_data["last_turn_data"]["workflow_history"])
        _consumption_data = {
            "turn_number": turn_number,
            "input_tokens": turn_data.get('turn_input_tokens', 0),
            "output_tokens": turn_data.get('turn_output_tokens', 0),
            "provider": turn_data.get('provider', 'Unknown'),
            "model": turn_data.get('model', 'unknown'),
            "status": turn_data.get('status', 'success'),
            "rag_used": turn_data.get('rag_source_collection_id') is not None,
            "rag_tokens_saved": turn_data.get('rag_efficiency_gain', 0) if turn_data.get('rag_source_collection_id') is not None else 0,
            "user_query": turn_data.get('user_query', ''),
            # The turn's accurate cost = Σ per-call costs, each priced at the model that served
            # it (the engines' turn-cost accumulator). Re-pricing the summed tokens at ONE model
            # mispriced dual-model turns and @TAG-override turns (priced at the reverted default).
            "turn_cost": turn_data.get('turn_cost'),
        }

    # --- CONSUMPTION TRACKING (outside lock for minimal lock hold time) ---
    if _consumption_data:
        try:
            from trusted_data_agent.auth.database import get_db_session
            from trusted_data_agent.auth.consumption_manager import ConsumptionManager
            from trusted_data_agent.core.cost_manager import get_cost_manager

            with get_db_session() as db_session:
                manager = ConsumptionManager(db_session)

                cost_manager = get_cost_manager()
                _tc = _consumption_data.get("turn_cost")
                if isinstance(_tc, (int, float)) and not isinstance(_tc, bool) and _tc > 0:
                    cost_usd, _cost_src = float(_tc), "turn accumulator"
                else:
                    cost_usd = cost_manager.calculate_cost(
                        _consumption_data["provider"], _consumption_data["model"],
                        _consumption_data["input_tokens"], _consumption_data["output_tokens"]
                    )
                    _cost_src = "re-priced"
                cost_usd_cents = int(cost_usd * 1000000)
                app_logger.info(f"[Cost Tracking] {_consumption_data['provider']}/{_consumption_data['model']}: {_consumption_data['input_tokens']}in + {_consumption_data['output_tokens']}out = ${cost_usd:.6f} ({cost_usd_cents} micro-dollars, {_cost_src})")

                manager.record_turn(
                    user_id=user_uuid,
                    session_id=session_id,
                    turn_number=_consumption_data["turn_number"],
                    input_tokens=_consumption_data["input_tokens"],
                    output_tokens=_consumption_data["output_tokens"],
                    provider=_consumption_data["provider"],
                    model=_consumption_data["model"],
                    status=_consumption_data["status"],
                    rag_used=_consumption_data["rag_used"],
                    rag_tokens_saved=_consumption_data["rag_tokens_saved"],
                    cost_usd_cents=cost_usd_cents,
                    user_query=_consumption_data["user_query"],
                    session_name=_session_name
                )
                app_logger.debug(f"Recorded turn metrics for user {user_uuid}, session {session_id}, turn {_consumption_data['turn_number']}")
        except Exception as e:
            app_logger.warning(f"Failed to record turn metrics for user {user_uuid}: {e}")

    # Background fire-and-forget: populate session vector store for RAG condensation
    if APP_CONFIG.USE_CONTEXT_WINDOW_MANAGER and isinstance(turn_data, dict) and _consumption_data:
        try:
            asyncio.create_task(
                _ingest_turn_to_session_store(
                    session_id=session_id,
                    user_uuid=user_uuid,
                    turn_data=turn_data,
                    turn_number=_consumption_data["turn_number"],
                )
            )
        except Exception:
            pass  # Non-critical; background ingestion failure never blocks turn save


async def append_extension_results_to_turn(
    user_uuid: str,
    session_id: str,
    extension_results: dict,
    extension_events: list = None,
    extension_input_tokens: int = 0,
    extension_output_tokens: int = 0,
):
    """
    Append extension results and lifecycle events to the most recent turn
    in workflow_history. Called after extensions complete in execution_service.py,
    since extensions run AFTER the turn is already saved by update_last_turn_data().

    Args:
        user_uuid: User UUID for session lookup
        session_id: Session ID
        extension_results: Serialized extension results dict
        extension_events: List of extension lifecycle events [{type, payload}, ...]
        extension_input_tokens: Total input tokens consumed by extensions (for LLM-calling extensions)
        extension_output_tokens: Total output tokens consumed by extensions
    """
    async with _session_transaction(user_uuid, session_id) as session_data:
        if not session_data:
            app_logger.warning(f"Cannot append extension results: session {session_id} not found")
            return

        workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
        if not workflow_history:
            app_logger.warning(f"Cannot append extension results: no turns in session {session_id}")
            return  # No modifications — _session_transaction save is a harmless no-op

        # Patch the last turn entry
        last_turn = workflow_history[-1]
        last_turn["extension_results"] = extension_results
        if extension_events:
            last_turn["extension_events"] = extension_events

        # Update turn-level token totals if extensions consumed tokens
        if extension_input_tokens > 0 or extension_output_tokens > 0:
            last_turn["turn_input_tokens"] = last_turn.get("turn_input_tokens", 0) + extension_input_tokens
            last_turn["turn_output_tokens"] = last_turn.get("turn_output_tokens", 0) + extension_output_tokens
            try:
                from trusted_data_agent.core.cost_manager import get_cost_manager
                cost_manager = get_cost_manager()
                provider = last_turn.get("provider", "")
                model = last_turn.get("model", "")
                # §3.4: add the extension call's OWN cost as a priced INCREMENT rather than
                # re-pricing the whole turn at one model — a whole-turn re-price would discard the
                # dual-model-accurate turn_cost the engine persisted (Σ per-call). For a single-model
                # turn this yields the identical total (base + ext_cost at the same model).
                _ext_cost = cost_manager.calculate_cost(
                    provider, model, extension_input_tokens, extension_output_tokens
                )
                last_turn["turn_cost"] = float(last_turn.get("turn_cost", 0.0) or 0.0) + (_ext_cost or 0.0)
            except Exception:
                pass

            # Update consumption tracking with extension tokens
            try:
                from trusted_data_agent.auth.database import get_db_session
                from trusted_data_agent.auth.consumption_manager import ConsumptionManager
                from trusted_data_agent.core.cost_manager import get_cost_manager

                with get_db_session() as db_session:
                    manager = ConsumptionManager(db_session)
                    cost_manager = get_cost_manager()
                    provider = last_turn.get("provider", "Unknown")
                    model = last_turn.get("model", "unknown")
                    ext_cost = cost_manager.calculate_cost(
                        provider, model, extension_input_tokens, extension_output_tokens
                    )
                    ext_cost_cents = int(ext_cost * 1000000)

                    turn_number = len(workflow_history)
                    manager.update_turn_tokens(
                        user_id=user_uuid,
                        session_id=session_id,
                        turn_number=turn_number,
                        additional_input_tokens=extension_input_tokens,
                        additional_output_tokens=extension_output_tokens,
                        additional_cost_usd_cents=ext_cost_cents,
                    )
                    app_logger.info(
                        f"[Cost Tracking] Extension tokens added to consumption: "
                        f"{extension_input_tokens}in + {extension_output_tokens}out = ${ext_cost:.6f}"
                    )
            except Exception as e:
                app_logger.warning(f"Failed to update consumption with extension tokens: {e}")

        app_logger.debug(f"Appended extension results to last turn in session {session_id}")


# --- MODIFICATION START: Add function to purge only the agent's memory ---
async def purge_session_memory(user_uuid: str, session_id: str) -> bool:
    """
    Resets the agent's LLM context memory (`chat_object`) for a session,
    but leaves the UI history (`session_history`) and plan/trace history
    (`last_turn_data`) intact.
    """
    app_logger.info(f"Attempting to purge agent memory (chat_object) for session '{session_id}', user '{user_uuid}'.")
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                app_logger.warning(f"Could not purge memory: Session {session_id} not found for user {user_uuid}.")
                return False

            # Mark all existing history as invalid
            session_history = session_data.get('session_history', [])
            history_count = 0
            if isinstance(session_history, list):
                for msg in session_history:
                    if isinstance(msg, dict):
                        msg["isValid"] = False
                        history_count += 1

            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
            turn_count = 0
            if isinstance(workflow_history, list):
                for turn in workflow_history:
                    if isinstance(turn, dict):
                        turn["isValid"] = False
                        turn_count += 1

            app_logger.info(f"Marked {history_count} UI messages and {turn_count} turns as invalid for session '{session_id}'.")

            chat_history_for_file = []
            provider_in_session = session_data.get("license_info", {}).get("provider")
            current_provider = APP_CONFIG.CURRENT_PROVIDER
            provider = provider_in_session or current_provider or "Google"

            if provider == "Google":
                initial_history_google = [
                    {"role": "user", "parts": [{"text": "You are a helpful assistant."}]},
                    {"role": "model", "parts": [{"text": "Understood."}]}
                ]
                chat_history_for_file = [{'role': m['role'], 'content': m['parts'][0]['text']} for m in initial_history_google]

            session_data['chat_object'] = chat_history_for_file
            session_data['full_context_sent'] = False

        app_logger.info(f"Successfully reset chat_object for session '{session_id}'. Invalidated existing history.")
        return True

    except Exception as e:
        app_logger.error(f"An unexpected error occurred during memory purge for session '{session_id}': {e}", exc_info=True)
        return False

# --- MODIFICATION START: Add function to toggle turn validity ---
async def toggle_turn_validity(user_uuid: str, session_id: str, turn_id: int) -> bool:
    """
    Toggles the 'isValid' status of a specific turn and its corresponding
    UI messages in the session history and LLM chat_object.
    """
    app_logger.info(f"Toggling validity for turn {turn_id} in session '{session_id}' for user '{user_uuid}'.")
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                app_logger.warning(f"Could not toggle validity: Session {session_id} not found.")
                return False

            # 1. Toggle validity in workflow_history
            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
            turn_found = False
            new_status = None
            if isinstance(workflow_history, list):
                for turn in workflow_history:
                    if isinstance(turn, dict) and turn.get("turn") == turn_id:
                        current_status = turn.get("isValid", True)
                        new_status = not current_status
                        turn["isValid"] = new_status
                        turn_found = True
                        break

            if not turn_found:
                app_logger.warning(f"Turn {turn_id} not found in workflow_history for session {session_id}.")
                return False

            # 2. Toggle validity in session_history (for the UI). Messages carry their turn
            # number (backfilled on read for legacy sessions), and a sibling answer (S8) can sit
            # mid-history, so the match is by number — the assistant-count walk is only the
            # fallback for a history written before turn numbers existed.
            session_history = session_data.get('session_history', [])
            if isinstance(session_history, list):
                if any(isinstance(m, dict) and 'turn_number' in m for m in session_history):
                    for msg in session_history:
                        if isinstance(msg, dict) and msg.get('turn_number') == turn_id:
                            msg['isValid'] = new_status
                else:
                    assistant_message_count = 0
                    for i, msg in enumerate(session_history):
                        if isinstance(msg, dict) and msg.get('role') == 'assistant':
                            assistant_message_count += 1
                            if assistant_message_count == turn_id:
                                msg['isValid'] = new_status
                                if i > 0 and session_history[i-1].get('role') == 'user':
                                    session_history[i-1]['isValid'] = new_status
                                break

            # 3. Toggle validity in chat_object
            chat_object = session_data.get('chat_object', [])
            if isinstance(chat_object, list):
                for msg in chat_object:
                    if isinstance(msg, dict) and msg.get('turn_number') == turn_id:
                        msg['isValid'] = new_status

        return True

    except Exception as e:
        app_logger.error(f"An unexpected error occurred during validity toggle for turn {turn_id}: {e}", exc_info=True)
        return False

# --- MODIFICATION START: Add function to update turn system_events ---
async def update_turn_system_events(user_uuid: str, session_id: str, turn_number: int, system_events: list) -> bool:
    """
    Updates the system_events for a specific turn in the workflow_history.
    This is used when session name generation events need to be added after
    the turn has already been saved.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID
        turn_number: The turn number to update
        system_events: The list of system events to store

    Returns:
        True if successful, False otherwise
    """
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                app_logger.warning(f"Could not update system_events: Session {session_id} not found for user {user_uuid}.")
                return False

            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])

            turn_found = False
            for turn in workflow_history:
                if turn.get("turn") == turn_number:
                    turn["system_events"] = system_events
                    turn_found = True
                    break

            if not turn_found:
                app_logger.warning(f"Could not update system_events: Turn {turn_number} not found in session {session_id}.")
                return False

        app_logger.debug(f"Updated system_events for turn {turn_number} in session {session_id}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to update system_events for turn {turn_number}: {e}", exc_info=True)
        return False


async def append_system_events_to_turn(user_uuid: str, session_id: str, turn_number: int, new_events: list) -> bool:
    """
    Append (not replace) system events to a turn already saved in workflow_history.

    Used by post-turn LLM calls that run AFTER the turn is persisted (e.g. the
    Next-Step Recommender's Tier C call), so their Live-Status step events re-render
    on historical reload alongside session-name generation events.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID
        turn_number: The turn number to append to
        new_events: List of {type, payload} event dicts to append

    Returns:
        True if successful, False otherwise
    """
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                app_logger.warning(f"Could not append system_events: Session {session_id} not found for user {user_uuid}.")
                return False

            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
            for turn in workflow_history:
                if turn.get("turn") == turn_number:
                    existing = turn.get("system_events") or []
                    turn["system_events"] = existing + list(new_events)
                    app_logger.debug(f"Appended {len(new_events)} system_event(s) to turn {turn_number} in session {session_id}")
                    return True

            app_logger.warning(f"Could not append system_events: Turn {turn_number} not found in session {session_id}.")
            return False

    except Exception as e:
        app_logger.error(f"Failed to append system_events for turn {turn_number}: {e}", exc_info=True)
        return False


#: A confirmation in one of these states is FINAL for its invocation on its turn — a second
#: click, or a click on a reloaded row, must not perform it again. A ``refused`` one may be
#: retried (nothing was written).
ACTION_CONFIRMATION_FINAL = ("performing", "performed", "parked", "observed")


async def claim_action_confirmation(user_uuid: str, session_id: str, turn_number, invocation_hash: str,
                                    claim: dict) -> dict:
    """PROP-008 B4c — claim a prepared act on its turn BEFORE it is performed.

    Atomic under the session transaction: if the turn already records a FINAL confirmation of
    this invocation the existing record is returned with ``claimed: False`` and nothing may be
    written; otherwise ``claim`` is stamped as ``performing`` and ``claimed: True`` is returned.
    Returns ``{"found": False}`` when the session or turn does not exist (the caller refuses —
    a confirmation it cannot record could be clicked again after a reload)."""
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                return {"found": False}
            for turn in session_data.get("last_turn_data", {}).get("workflow_history", []):
                if str(turn.get("turn")) != str(turn_number):
                    continue
                book = turn.setdefault("action_confirmations", {})
                prior = book.get(invocation_hash)
                if prior and prior.get("outcome") in ACTION_CONFIRMATION_FINAL:
                    return {"found": True, "claimed": False, "record": prior}
                # the turn's agent may have acted on this invocation itself — a review/observe
                # posture parks or records on the turn with no Confirm; replaying its hash
                # through a confirmation would park the same write twice (four-eyes live run)
                for acted in turn.get("actions") or []:
                    if (isinstance(acted, dict) and acted.get("invocation_hash") == invocation_hash
                            and acted.get("outcome") in ACTION_CONFIRMATION_FINAL):
                        return {"found": True, "claimed": False, "record": acted}
                book[invocation_hash] = {**claim, "outcome": "performing"}
                return {"found": True, "claimed": True}
            return {"found": False}
    except Exception as e:  # noqa: BLE001 — an unrecordable claim is refused by the caller
        app_logger.error(f"claim_action_confirmation failed for turn {turn_number}: {e}", exc_info=True)
        return {"found": False}


async def record_action_confirmation(user_uuid: str, session_id: str, turn_number, invocation_hash: str,
                                     record: dict) -> bool:
    """Replace a claim with the act's outcome (performed · parked · observed · refused)."""
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                return False
            for turn in session_data.get("last_turn_data", {}).get("workflow_history", []):
                if str(turn.get("turn")) == str(turn_number):
                    turn.setdefault("action_confirmations", {})[invocation_hash] = record
                    return True
            return False
    except Exception as e:  # noqa: BLE001
        app_logger.error(f"record_action_confirmation failed for turn {turn_number}: {e}", exc_info=True)
        return False


async def get_action_confirmations(user_uuid: str, session_id: str) -> dict:
    """``{"<turn>": {invocation_hash: record}}`` for one session — what a reloaded answer reads
    to show each act as it ended rather than offering its Confirm again."""
    try:
        session_data = await get_session(user_uuid, session_id)
    except Exception:  # noqa: BLE001
        session_data = None
    out: dict = {}
    for turn in ((session_data or {}).get("last_turn_data", {}) or {}).get("workflow_history", []) or []:
        book = turn.get("action_confirmations")
        if book:
            out[str(turn.get("turn"))] = book
    return out


async def set_turn_rebind_context(user_uuid: str, session_id: str, turn_number: int, context: dict) -> bool:
    """
    Stamp a compact Next-Step Recommender *rebind context* onto a saved turn.

    The context is ``{"query": <asked, tag-stripped>, "flags": <result-shape descriptor>}``
    (see ``recommender.derive_render_flags``). It lets the recommender rebuild the
    free (A+B) suggestion set for a DIFFERENT profile — when the user changes the
    composer's ``@TAG`` override — without re-materializing the turn's
    ``collected_data`` or rendered HTML. Read back by the
    ``/v1/sessions/<id>/next-step-rebind`` endpoint.

    Returns True if the turn was found and stamped, False otherwise.
    """
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                return False
            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
            for turn in workflow_history:
                if turn.get("turn") == turn_number:
                    turn["next_step_rebind"] = context
                    return True
            return False
    except Exception as e:
        app_logger.debug(f"Failed to set rebind context for turn {turn_number}: {e}")
        return False


async def get_turn_rebind_context(user_uuid: str, session_id: str, turn_number: int | None = None) -> dict | None:
    """
    Return the stashed Next-Step rebind context for a turn (default: the latest
    turn) of a session, or None if absent. Used by the rebind endpoint to rebuild
    suggestions for a newly-selected profile against an already-executed turn.
    """
    try:
        session_data = await get_session(user_uuid, session_id)
        if not session_data:
            return None
        workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
        if not isinstance(workflow_history, list) or not workflow_history:
            return None
        if turn_number is None:
            for turn in reversed(workflow_history):
                ctx = turn.get("next_step_rebind")
                if ctx:
                    return {"turn": turn.get("turn"), **ctx}
            return None
        for turn in workflow_history:
            if turn.get("turn") == turn_number:
                ctx = turn.get("next_step_rebind")
                return {"turn": turn.get("turn"), **ctx} if ctx else None
        return None
    except Exception as e:
        app_logger.debug(f"Failed to get rebind context for session {session_id}: {e}")
        return None

# --- MODIFICATION START: Add function to update turn token counts ---
async def update_turn_token_counts(user_uuid: str, session_id: str, turn_number: int, input_tokens: int, output_tokens: int, added_cost_usd: float = None, additive: bool = False) -> bool:
    """
    Updates the token counts for a specific turn in the workflow_history.
    This is used when session name generation tokens need to be added after
    the turn has already been saved.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID
        turn_number: The turn number to update
        input_tokens: The total input tokens for the turn (including session name)
        output_tokens: The total output tokens for the turn (including session name)
        added_cost_usd: When provided, the ALREADY-PRICED incremental cost to ADD to the
            existing turn cost (e.g. a judge run priced at the judge model's own rates). This
            avoids re-pricing the whole turn — including the added tokens — at the session
            model's rates, which made the persisted turn_cost drift from the sum of the
            per-event costs. When None, turn_cost is recomputed from the totals as before
            (correct for same-model additions like session-name generation).

    Returns:
        True if successful, False otherwise
    """
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                app_logger.warning(f"Could not update token counts: Session {session_id} not found for user {user_uuid}.")
                return False

            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])

            turn_found = False
            for turn in workflow_history:
                if turn.get("turn") == turn_number:
                    if additive:
                        # Read-modify-write INSIDE the per-session lock so two concurrent
                        # background billers (online-eval + memory extraction fire at nearly the
                        # same instant on the same turn) can't both read stale totals and clobber
                        # each other — here input_tokens/output_tokens are DELTAS, added atomically.
                        input_tokens = (turn.get("turn_input_tokens", 0) or 0) + input_tokens
                        output_tokens = (turn.get("turn_output_tokens", 0) or 0) + output_tokens
                    turn["input_tokens"] = input_tokens
                    turn["output_tokens"] = output_tokens
                    turn["turn_input_tokens"] = input_tokens
                    turn["turn_output_tokens"] = output_tokens
                    if "session_input_tokens" in turn:
                        turn["session_input_tokens"] = session_data.get("input_tokens", input_tokens)
                    if "session_output_tokens" in turn:
                        turn["session_output_tokens"] = session_data.get("output_tokens", output_tokens)

                    try:
                        if added_cost_usd is not None:
                            # Add the already-priced increment (e.g. a judge run at its own
                            # model's rates) instead of re-pricing the whole turn at the session
                            # model — keeps turn_cost == sum of the per-event costs.
                            prior_cost = float(turn.get("turn_cost", 0.0) or 0.0)
                            turn["turn_cost"] = prior_cost + float(added_cost_usd or 0.0)
                        else:
                            from trusted_data_agent.core.cost_manager import CostManager
                            cost_manager = CostManager()
                            provider = session_data.get("provider", "")
                            model = session_data.get("model", "")
                            turn_cost = cost_manager.calculate_cost(provider, model, input_tokens, output_tokens)
                            turn["turn_cost"] = turn_cost
                    except Exception as e:
                        app_logger.warning(f"Failed to calculate turn cost: {e}")

                    try:
                        session_cost = 0.0
                        for i, t in enumerate(workflow_history):
                            if i + 1 <= turn_number:
                                if "turn_cost" in t:
                                    session_cost += float(t["turn_cost"])
                        turn["session_cost_usd"] = session_cost
                    except Exception as e:
                        app_logger.warning(f"Failed to recalculate session_cost_usd: {e}")

                    turn_found = True
                    app_logger.debug(f"Updated turn {turn_number} token counts: {input_tokens} in / {output_tokens} out")
                    break

            if not turn_found:
                app_logger.warning(f"Could not update token counts: Turn {turn_number} not found in session {session_id}.")
                return False

        return True

    except Exception as e:
        app_logger.error(f"Failed to update token counts for turn {turn_number}: {e}", exc_info=True)
        return False
# --- MODIFICATION END ---

# --- MODIFICATION START: Add function to update turn feedback ---
async def update_turn_feedback(user_uuid: str, session_id: str, turn_id: int, vote: str | None,
                               *, _depth: int = 0) -> bool:
    """
    Updates the feedback (upvote/downvote) for a specific turn in the workflow_history
    and dispatches the vote to the universal feedback consumers.

    This is the SINGLE wire point of the vote signal (docs/roadmap/VOTE_SIGNAL_PLAN.md).
    Every consequence — RAG champion scoring, eval annotation, genie propagation,
    retrieval feedback, memory, judge diagnosis — lives behind
    ``agent.feedback.dispatch_vote``; nothing vote-related may be added here.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID
        turn_id: The turn number to update
        vote: 'up', 'down', or None to clear the vote
        _depth: internal — genie propagation depth (0 = user-originated). Never
            exposed through the HTTP route; only the genie consumer sets it.

    Returns:
        True if successful, False otherwise
    """
    import copy

    prev_vote = None
    turn_snapshot = None
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                app_logger.warning(f"Could not update feedback: Session {session_id} not found for user {user_uuid}.")
                return False

            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])

            turn_found = False
            for turn in workflow_history:
                if turn.get("turn") == turn_id:
                    prev_vote = turn.get("feedback")
                    if vote is None:
                        turn.pop("feedback", None)
                    else:
                        turn["feedback"] = vote
                    turn_found = True
                    # Snapshot AFTER the update so consumers see the turn as persisted.
                    # Deep copy: consumers run outside the lock and must not alias
                    # (nor mutate) live session state.
                    turn_snapshot = copy.deepcopy(turn)
                    app_logger.info(f"Updated feedback for turn {turn_id} in session {session_id}: {vote}")
                    break

            if not turn_found:
                app_logger.warning(f"Turn {turn_id} not found in workflow_history for session {session_id}")
                return False

        # Post-save (outside the lock): fan the vote out to every consumer. Scheduled
        # fire-and-forget so the caller's response never waits on a consumer, and
        # fully defensive so a consumer can never fail the vote itself.
        try:
            from trusted_data_agent.agent.feedback import VoteEvent, schedule_vote_dispatch
            snapshot = turn_snapshot if isinstance(turn_snapshot, dict) else {}
            schedule_vote_dispatch(VoteEvent(
                user_uuid=user_uuid, session_id=session_id, turn_id=turn_id,
                vote=vote, prev_vote=prev_vote, turn=snapshot,
                profile_tag=snapshot.get("profile_tag"),
                profile_type=snapshot.get("profile_type"),
                depth=_depth, propagated=_depth > 0,
            ))
        except Exception as e:  # noqa: BLE001 — the vote itself already succeeded
            app_logger.error(f"Failed to dispatch vote signal for turn {turn_id}: {e}", exc_info=True)

        return True

    except Exception as e:
        app_logger.error(f"Error updating feedback for turn {turn_id}: {e}", exc_info=True)
        return False

async def add_case_id_to_turn(user_uuid: str, session_id: str, turn_id: int, case_id: str) -> bool:
    """
    Adds a RAG case_id to a specific turn in the workflow_history.

    Args:
        user_uuid: The user's UUID
        session_id: The session ID
        turn_id: The turn number to update
        case_id: The RAG case ID to associate with this turn

    Returns:
        True if successful, False otherwise
    """
    try:
        async with _session_transaction(user_uuid, session_id) as session_data:
            if not session_data:
                app_logger.warning(f"Could not add case_id: Session {session_id} not found for user {user_uuid}.")
                return False

            workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])

            turn_found = False
            for turn in workflow_history:
                if turn.get("turn") == turn_id:
                    turn["case_id"] = case_id
                    turn_found = True
                    app_logger.debug(f"Added case_id {case_id} to turn {turn_id} in session {session_id}")
                    break

            if not turn_found:
                app_logger.warning(f"Turn {turn_id} not found in workflow_history for session {session_id}")
                return False

        return True

    except Exception as e:
        app_logger.error(f"Error adding case_id to turn {turn_id}: {e}", exc_info=True)
        return False


# --- GENIE SESSION MANAGEMENT FUNCTIONS ---

async def record_genie_session_link(parent_session_id: str, slave_session_id: str, slave_profile_id: str, slave_profile_tag: str, user_uuid: str, execution_order: int = 0, nesting_level: int = 0) -> bool:
    """
    Record a link between a Genie parent session and a slave session.

    Args:
        parent_session_id: The Genie coordinator session ID
        slave_session_id: The spawned slave session ID
        slave_profile_id: The profile ID used for the slave session
        slave_profile_tag: The profile tag (e.g., @CHAT, @RAG)
        user_uuid: The user who owns both sessions
        execution_order: The order in which the slave was invoked
        nesting_level: Depth in nested Genie hierarchy (0 = direct slave, 1 = slave of slave, etc.)

    Returns:
        True if successful, False otherwise
    """
    try:
        from trusted_data_agent.auth.database import get_db_session
        import sqlite3
        from trusted_data_agent.core.utils import get_project_root

        db_path = str(get_project_root() / "tda_auth.db")
        conn = sqlite_util.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO genie_session_links
            (parent_session_id, slave_session_id, slave_profile_id, slave_profile_tag, user_uuid, execution_order, nesting_level)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (parent_session_id, slave_session_id, slave_profile_id, slave_profile_tag, user_uuid, execution_order, nesting_level))

        conn.commit()
        conn.close()

        app_logger.info(f"Recorded genie session link: {parent_session_id} -> child {slave_session_id} (@{slave_profile_tag})")
        return True

    except Exception as e:
        app_logger.error(f"Failed to record genie session link: {e}", exc_info=True)
        return False


async def get_genie_slave_sessions(parent_session_id: str, user_uuid: str) -> list:
    """
    Get all child sessions for a Genie parent session.

    Note: Function name preserved for API compatibility.

    Args:
        parent_session_id: The Genie coordinator session ID
        user_uuid: The user who owns the sessions

    Returns:
        List of child session info dicts
    """
    try:
        import sqlite3
        from trusted_data_agent.core.utils import get_project_root

        db_path = str(get_project_root() / "tda_auth.db")
        conn = sqlite_util.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT slave_session_id, slave_profile_id, slave_profile_tag, created_at, status, execution_order
            FROM genie_session_links
            WHERE parent_session_id = ? AND user_uuid = ?
            ORDER BY execution_order
        """, (parent_session_id, user_uuid))

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except Exception as e:
        app_logger.error(f"Failed to get genie child sessions: {e}", exc_info=True)
        return []


async def get_genie_parent_session(slave_session_id: str, user_uuid: str) -> dict | None:
    """
    Get the parent Genie session for a child session.

    Note: Function name preserved for API compatibility.

    Args:
        slave_session_id: The child session ID (parameter name preserved for API compatibility)
        user_uuid: The user who owns the sessions

    Returns:
        Parent session link info or None if not a child session
    """
    try:
        import sqlite3
        from trusted_data_agent.core.utils import get_project_root

        db_path = str(get_project_root() / "tda_auth.db")
        conn = sqlite_util.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT parent_session_id, slave_profile_id, slave_profile_tag, created_at, status, execution_order, nesting_level
            FROM genie_session_links
            WHERE slave_session_id = ? AND user_uuid = ?
        """, (slave_session_id, user_uuid))

        row = cursor.fetchone()
        conn.close()

        return dict(row) if row else None

    except Exception as e:
        app_logger.error(f"Failed to get genie parent session: {e}", exc_info=True)
        return None


async def cleanup_genie_slave_sessions(
    parent_session_id: str,
    user_uuid: str,
    _depth: int = 0,
    archived_reason: str = None
) -> list:
    """
    Clean up (archive) all child sessions when a Genie parent session is deleted.
    Recursively handles nested Genie sessions (children that are themselves parents).

    Args:
        parent_session_id: The Genie coordinator session ID
        user_uuid: The user who owns the sessions
        _depth: Internal recursion depth guard (max 5)
        archived_reason: Optional reason for archiving (passed to delete_session)

    Returns:
        List of all deleted (archived) child session IDs, including nested descendants
    """
    all_deleted = []

    if _depth > 5:
        app_logger.warning(f"Max nesting depth reached for cleanup of parent {parent_session_id}")
        return all_deleted

    try:
        import sqlite3
        from trusted_data_agent.core.utils import get_project_root

        # Get all direct child sessions
        slaves = await get_genie_slave_sessions(parent_session_id, user_uuid)

        # Delete each child session, recursing into nested Genie children first.
        # We disable the cascade-inside-delete_session here and drive the recursion
        # ourselves so the depth guard (_depth > 5) stays authoritative.
        for slave in slaves:
            slave_session_id = slave.get('slave_session_id')
            if slave_session_id:
                # Recurse: if this child is also a Genie parent, clean up its children first
                nested_deleted = await cleanup_genie_slave_sessions(
                    slave_session_id, user_uuid, _depth=_depth + 1, archived_reason=archived_reason
                )
                all_deleted.extend(nested_deleted)

                await delete_session(
                    user_uuid, slave_session_id,
                    archived_reason=archived_reason,
                    _cascade_slaves=False,
                )
                all_deleted.append(slave_session_id)
                app_logger.info(f"Deleted genie child session: {slave_session_id}")

        # Remove links from database
        db_path = str(get_project_root() / "tda_auth.db")
        conn = sqlite_util.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM genie_session_links
            WHERE parent_session_id = ? AND user_uuid = ?
        """, (parent_session_id, user_uuid))

        conn.commit()
        conn.close()

        # Also clear the in-memory cache in GenieCoordinator
        try:
            from trusted_data_agent.agent.genie_coordinator import SlaveSessionTool
            # Remove all cache entries for this parent session
            keys_to_remove = [
                key for key in SlaveSessionTool._session_cache.keys()
                if key.startswith(f"{parent_session_id}:")
            ]
            for key in keys_to_remove:
                del SlaveSessionTool._session_cache[key]
            if keys_to_remove:
                app_logger.debug(f"Cleared {len(keys_to_remove)} in-memory cache entries for parent {parent_session_id}")
        except ImportError:
            pass  # GenieCoordinator not available, skip cache cleanup
        except Exception as cache_err:
            app_logger.warning(f"Failed to clear genie session cache: {cache_err}")

        app_logger.info(f"Cleaned up {len(slaves)} genie child sessions for parent {parent_session_id}")
        return all_deleted

    except Exception as e:
        app_logger.error(f"Failed to cleanup genie child sessions: {e}", exc_info=True)
        return all_deleted


async def update_genie_slave_status(slave_session_id: str, user_uuid: str, status: str) -> bool:
    """
    Update the status of a genie child session link.

    Args:
        slave_session_id: The child session ID (parameter name preserved for API compatibility)
        user_uuid: The user who owns the sessions
        status: New status ('active', 'completed', 'failed')

    Returns:
        True if successful, False otherwise
    """
    try:
        import sqlite3
        from trusted_data_agent.core.utils import get_project_root

        db_path = str(get_project_root() / "tda_auth.db")
        conn = sqlite_util.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE genie_session_links
            SET status = ?
            WHERE slave_session_id = ? AND user_uuid = ?
        """, (status, slave_session_id, user_uuid))

        conn.commit()
        conn.close()

        app_logger.debug(f"Updated genie child session {slave_session_id} status to {status}")
        return True

    except Exception as e:
        app_logger.error(f"Failed to update genie child status: {e}", exc_info=True)
        return False
