"""
Task Scheduler (Track B)

Autonomous scheduling engine integrated with APScheduler.
Scheduled tasks run through the identical execute_query() pipeline as
user-submitted queries — the only additions are persistence, delivery
channels, and concurrency governance.

Governance model:
  - Scheduler component must be admin-enabled (component_settings.disabled_components)
  - Profile must have scheduler component enabled (componentConfig.scheduler.enabled)
  - Per-task: overlap_policy (skip | queue | allow), max_tokens_per_run
"""

import asyncio
import json
import logging
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")

_DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"

# Holds the APScheduler instance (set during app startup)
_scheduler = None
# Holds the asyncio event loop captured at startup — used by sync APScheduler callbacks
_event_loop = None


def _ensure_columns():
    """Auto-migrate: add new columns to scheduled_tasks if missing.

    Called at import time AND deferred via _ensure_columns_deferred() once the app
    has bootstrapped the schema. The import-time call may fail silently when the
    table does not exist yet (fresh DB); the deferred call runs after schema init
    and always succeeds on existing tables.
    """
    try:
        with _get_conn() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()}
            if not cols:
                # Table doesn't exist yet — schema init hasn't run; skip silently.
                return
            if "session_id" not in cols:
                conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN session_id TEXT")
                conn.commit()
                logger.info("Task Scheduler: migrated scheduled_tasks — added session_id column.")
            if "job_type" not in cols:
                conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN job_type TEXT NOT NULL DEFAULT 'user'")
                conn.commit()
                logger.info("Task Scheduler: migrated scheduled_tasks — added job_type column.")
            # PROP-008 §6 — a scheduled ACTION (JSON {action, invocation, connection_id?});
            # NULL ⇒ the task is a prompt, exactly as before
            if "action_spec" not in cols:
                conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN action_spec TEXT")
                conn.commit()
                logger.info("Task Scheduler: migrated scheduled_tasks — added action_spec column.")
            run_cols = {row[1] for row in conn.execute("PRAGMA table_info(scheduled_task_runs)").fetchall()}
            if run_cols and "action_record" not in run_cols:
                conn.execute("ALTER TABLE scheduled_task_runs ADD COLUMN action_record TEXT")
                conn.commit()
                logger.info("Task Scheduler: migrated scheduled_task_runs — added action_record column.")
    except Exception as e:
        logger.warning(f"Task Scheduler schema migration warning: {e}")


# Tracks running task fires: {task_id: asyncio.Task}
_running_tasks: dict[str, asyncio.Task] = {}

# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite_util.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


_ensure_columns()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row) -> dict:
    return dict(row) if row else None


# ── Scheduler component gate ──────────────────────────────────────────────────

def is_scheduler_globally_enabled() -> bool:
    """Return True if the scheduler component is not in admin's disabled_components list."""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT setting_value FROM component_settings WHERE setting_key = 'disabled_components'"
            ).fetchone()
        if row:
            disabled = json.loads(row["setting_value"] or "[]")
            return "scheduler" not in disabled
    except Exception:
        pass
    return True  # default open


def is_profile_scheduler_enabled() -> bool:
    """Return True if user-created profile jobs are globally enabled."""
    try:
        from trusted_data_agent.components.settings import is_profile_scheduler_enabled as _flag
        return _flag()
    except Exception:
        return True


def is_platform_scheduler_enabled() -> bool:
    """Return True if platform maintenance jobs are globally enabled."""
    try:
        from trusted_data_agent.components.settings import is_platform_scheduler_enabled as _flag
        return _flag()
    except Exception:
        return True


def _get_platform_job_ids() -> list[str]:
    """Return IDs of all platform jobs (job_type='platform')."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM scheduled_tasks WHERE job_type = 'platform'"
            ).fetchall()
        return [r["id"] for r in rows]
    except Exception:
        return []


# ── CRUD ──────────────────────────────────────────────────────────────────────

@_signals.announces("scheduler", "created", user="user_uuid", ids=("task_id",))
def create_task(
    user_uuid: str,
    profile_id: str,
    name: str,
    prompt: str,
    schedule: str,
    output_channel: Optional[str] = None,
    output_config: Optional[dict] = None,
    max_tokens_per_run: Optional[int] = None,
    overlap_policy: str = "skip",
    session_id: Optional[str] = None,
    action_spec: Optional[dict] = None,
) -> dict:
    spec_json = _validated_action_spec(action_spec, user_uuid, profile_id)
    if spec_json and not prompt:
        prompt = f"Perform the signed action {json.loads(spec_json)['action']!r}"
    task_id = f"sched-{uuid.uuid4().hex[:12]}"
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO scheduled_tasks
               (id, user_uuid, profile_id, name, prompt, schedule, enabled,
                output_channel, output_config, max_tokens_per_run, overlap_policy,
                session_id, created_at, updated_at, action_spec)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, user_uuid, profile_id, name, prompt, schedule,
                output_channel,
                json.dumps(output_config) if output_config else None,
                max_tokens_per_run,
                overlap_policy,
                session_id,
                now, now, spec_json,
            )
        )
        conn.commit()

    task = get_task(task_id, user_uuid)
    if task and _scheduler and _scheduler.running:
        _register_job(task)
    return task


def get_task(task_id: str, user_uuid: str) -> Optional[dict]:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ? AND user_uuid = ?",
            (task_id, user_uuid)
        ).fetchone()
    return _row(row)


def list_tasks(user_uuid: str, profile_id: Optional[str] = None) -> list[dict]:
    with _get_conn() as conn:
        if profile_id:
            # Include tasks with empty profile_id (created before profile scoping was enforced)
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE user_uuid = ? AND (profile_id = ? OR profile_id = '') ORDER BY created_at DESC",
                (user_uuid, profile_id)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE user_uuid = ? ORDER BY created_at DESC",
                (user_uuid,)
            ).fetchall()
    return [dict(r) for r in rows]


@_signals.announces("scheduler", "updated", user="user_uuid", ids=("task_id",))
def update_task(task_id: str, user_uuid: str, updates: dict) -> Optional[dict]:
    allowed = {
        "name", "prompt", "schedule", "enabled",
        "output_channel", "output_config", "max_tokens_per_run", "overlap_policy",
        "session_id", "action_spec",
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    if "action_spec" in fields:
        cur = get_task(task_id, user_uuid)
        if not cur:
            return None
        # the STORED agent — an update cannot change who the task runs as, so the grant is
        # always checked against the agent that will actually perform it
        fields["action_spec"] = _validated_action_spec(fields["action_spec"], user_uuid, cur.get("profile_id"))
    if not fields:
        return get_task(task_id, user_uuid)

    if "output_config" in fields and isinstance(fields["output_config"], dict):
        fields["output_config"] = json.dumps(fields["output_config"])

    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id, user_uuid]
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE scheduled_tasks SET {set_clause} WHERE id = ? AND user_uuid = ?",
            values
        )
        conn.commit()

    task = get_task(task_id, user_uuid)
    if task and _scheduler and _scheduler.running:
        _unregister_job(task_id)
        if task.get("enabled"):
            _register_job(task)
    return task


@_signals.announces("scheduler", "deleted", user="user_uuid", ids=("task_id",))
def delete_task(task_id: str, user_uuid: str) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM scheduled_tasks WHERE id = ? AND user_uuid = ?",
            (task_id, user_uuid)
        ).fetchone()
        if not row:
            return False
        # the schema declares ON DELETE CASCADE, but this connection does not switch
        # foreign keys on, so the cascade never ran and a deleted task left its run history
        # (incl. a scheduled action's records) behind — removed explicitly, in one transaction
        conn.execute("DELETE FROM scheduled_task_runs WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        conn.commit()
    _unregister_job(task_id)
    return True


# ── Run history ───────────────────────────────────────────────────────────────

def list_runs(task_id: str, user_uuid: str, limit: int = 20) -> list[dict]:
    """Return recent run records for a task (validates ownership)."""
    with _get_conn() as conn:
        owner = conn.execute(
            "SELECT id FROM scheduled_tasks WHERE id = ? AND user_uuid = ?",
            (task_id, user_uuid)
        ).fetchone()
        if not owner:
            return []
        rows = conn.execute(
            """SELECT * FROM scheduled_task_runs WHERE task_id = ?
               ORDER BY started_at DESC LIMIT ?""",
            (task_id, limit)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("action_record"):   # PROP-008 §6 — the act (with its signed receipt), parsed
            try:
                d["action_record"] = json.loads(d["action_record"])
            except ValueError:
                pass
        out.append(d)
    return out


def _record_run_start(task_id: str, bg_task_id: Optional[str] = None) -> str:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO scheduled_task_runs (id, task_id, bg_task_id, started_at, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (run_id, task_id, bg_task_id, _now())
        )
        conn.commit()
    return run_id


def _record_run_end(
    run_id: str,
    status: str,
    result_summary: Optional[str] = None,
    tokens_used: Optional[int] = None,
    cost_usd: Optional[float] = None,
    skip_reason: Optional[str] = None,
    action_record: Optional[dict] = None,
):
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            """UPDATE scheduled_task_runs
               SET completed_at = ?, status = ?, result_summary = ?,
                   tokens_used = ?, cost_usd = ?, skip_reason = ?, action_record = ?
               WHERE id = ?""",
            (now, status, result_summary, tokens_used, cost_usd, skip_reason,
             json.dumps(action_record, sort_keys=True, default=str) if action_record else None, run_id)
        )
        # Update parent task last_run fields
        run_row = conn.execute(
            "SELECT task_id FROM scheduled_task_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run_row:
            conn.execute(
                "UPDATE scheduled_tasks SET last_run_at = ?, last_run_status = ?, updated_at = ? WHERE id = ?",
                (now, status, now, run_row["task_id"])
            )
        conn.commit()


# ── APScheduler integration ───────────────────────────────────────────────────

def get_scheduler():
    """Return the global APScheduler instance (may be None before startup)."""
    return _scheduler


async def start_scheduler():
    """Start APScheduler and register all enabled tasks. Called at app startup."""
    global _scheduler, _event_loop
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:
        logger.warning("APScheduler not installed — Task Scheduler component disabled. Run: pip install apscheduler>=3.10")
        return

    _event_loop = asyncio.get_running_loop()
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.start()
    logger.info("Task Scheduler started (UTC).")

    user_count = await _load_user_jobs()
    platform_count = await _load_platform_jobs()
    _register_cdc_poller()
    logger.info(f"Task Scheduler: registered {user_count} user job(s) and {platform_count} platform job(s).")

    # Startup catch-up sweep: APScheduler's jobstore is in-memory, so a
    # daily reset or monthly rollover missed while the server was down is
    # invisible to it after a restart (jobs are re-registered fresh with a
    # future next_run_time). Sweep once now — each job is guarded by its
    # reset timestamp / period column, so this is a no-op when nothing is
    # overdue. Complements the lazy per-user healer in
    # ConsumptionManager.get_or_create_consumption (which only heals users
    # who are active) by covering idle users too.
    if is_platform_scheduler_enabled():
        try:
            result = await asyncio.to_thread(_run_consumption_maintenance, "__catchup__")
            ran = result.get("jobs_run") if isinstance(result, dict) else None
            if ran:
                logger.info(
                    "Task Scheduler: startup consumption catch-up ran %d overdue job(s): %s",
                    len(ran), ", ".join(j.get("job", "?") for j in ran)
                )
        except Exception as e:
            logger.error(f"Task Scheduler: startup consumption catch-up failed (non-fatal): {e}")


async def _load_user_jobs() -> int:
    """Register all enabled user-created (profile) jobs. Returns count registered."""
    if not is_profile_scheduler_enabled():
        logger.info("Task Scheduler: profile jobs are disabled — skipping user job registration.")
        return 0
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND (job_type = 'user' OR job_type IS NULL)"
        ).fetchall()
    tasks = [dict(r) for r in rows]
    for task in tasks:
        try:
            _register_job(task)
        except Exception as e:
            logger.error(f"Failed to register user task '{task['id']}': {e}")
    return len(tasks)


COST_SYNC_JOB_ID = "platform-cost-price-sync"
COST_SYNC_JOB_NAME = "Cost Price Sync"


def _ensure_cost_sync_job() -> None:
    """Seed the daily price refresh (idempotent). It runs the full cost sync — LiteLLM, the
    providers' live price APIs, and the coverage phase that prices every model in use or names
    it — so a price published after a model was configured still reaches the table (OC-F12)."""
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO scheduled_tasks (id, user_uuid, profile_id, name, prompt, schedule, enabled, job_type) "
                "VALUES (?, '__platform__', '__platform__', ?, '__platform_job__', '15 3 * * *', 1, 'platform')",
                (COST_SYNC_JOB_ID, COST_SYNC_JOB_NAME))
            conn.commit()
    except Exception as e:  # noqa: BLE001 — seeding must never block the scheduler
        logger.warning(f"Task Scheduler: cost sync job not seeded: {e}")


def _run_cost_price_sync() -> dict:
    """The cost sync as a platform job (synchronous — call via asyncio.to_thread)."""
    from trusted_data_agent.core.cost_manager import get_cost_manager
    r = get_cost_manager().sync_from_litellm(check_availability=False)
    cov = r.get("coverage") or {}
    return {"synced": r.get("synced"), "errors": len(r.get("errors") or []),
            "models_in_use": cov.get("total"), "unpriced": cov.get("unpriced"),
            "unpriced_models": cov.get("unpriced_models"), "filled": len(cov.get("filled") or [])}


DB_BACKUP_JOB_ID = "platform-db-backup"
DB_BACKUP_JOB_NAME = "Database Backup"


def _ensure_db_backup_job() -> None:
    """Seed the daily verified database backup (idempotent; 02:30 UTC). See core/db_health.py."""
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO scheduled_tasks (id, user_uuid, profile_id, name, prompt, schedule, enabled, job_type) "
                "VALUES (?, '__platform__', '__platform__', ?, '__platform_job__', '30 2 * * *', 1, 'platform')",
                (DB_BACKUP_JOB_ID, DB_BACKUP_JOB_NAME))
            conn.commit()
    except Exception as e:  # noqa: BLE001 — seeding must never block the scheduler
        logger.warning(f"Task Scheduler: database backup job not seeded: {e}")


def _run_db_backup() -> dict:
    """The daily verified backup as a platform job (synchronous — call via asyncio.to_thread)."""
    from trusted_data_agent.core import db_health
    r = db_health.run_backup()
    return {"path": r["path"], "mb": r["bytes"] // 1_000_000, "seconds": r["seconds"], "kept": r["kept"]}


async def _load_platform_jobs() -> int:
    """Register all enabled platform maintenance jobs. Returns count registered."""
    _ensure_cost_sync_job()
    _ensure_db_backup_job()
    _ensure_input_expiry_job()
    if not is_platform_scheduler_enabled():
        logger.info("Task Scheduler: platform jobs are disabled — skipping platform job registration.")
        return 0
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND job_type = 'platform'"
        ).fetchall()
    tasks = [dict(r) for r in rows]
    for task in tasks:
        try:
            _register_job(task)
        except Exception as e:
            logger.error(f"Failed to register platform task '{task['id']}': {e}")
    return len(tasks)


_CDC_POLL_INTERVAL_SECONDS = 300  # Check for due collections every 5 minutes


def _register_cdc_poller():
    """Register the periodic CDC sync poller with APScheduler."""
    if not _scheduler:
        return
    if not is_platform_scheduler_enabled():
        logger.info("Task Scheduler: platform jobs disabled — CDC poller not registered.")
        return
    _scheduler.add_job(
        _fire_cdc_sync,
        "interval",
        seconds=_CDC_POLL_INTERVAL_SECONDS,
        id="__cdc_poller__",
        coalesce=True,
        replace_existing=True,
    )
    logger.info("CDC sync poller registered (every 5 minutes).")


def _fire_cdc_sync():
    """Synchronous wrapper called by APScheduler — dispatches CDC sync check."""
    if not _event_loop or _event_loop.is_closed():
        logger.error("Task Scheduler: no event loop available for CDC sync")
        return
    asyncio.run_coroutine_threadsafe(_execute_cdc_sync_async(), _event_loop)


async def _execute_cdc_sync_async():
    """Query for knowledge collections due for CDC sync and trigger each one."""
    try:
        from trusted_data_agent.core.collection_db import get_collection_db
        db = get_collection_db()
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT c.id, c.name, c.next_sync_at, c.sync_interval, c.owner_user_id
            FROM collections c
            WHERE c.repository_type = 'knowledge'
              AND c.next_sync_at IS NOT NULL
              AND datetime(c.next_sync_at) <= datetime('now')
              AND EXISTS (
                  SELECT 1 FROM knowledge_documents kd
                  WHERE kd.collection_id = c.id AND kd.sync_enabled = 1
              )
        """)
        due = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as e:
        logger.error(f"CDC poller: failed to query due collections: {e}")
        return

    if not due:
        return

    logger.info(f"CDC poller: {len(due)} collection(s) due for sync.")
    from trusted_data_agent.core.knowledge_sync import sync_knowledge_collection
    for coll in due:
        coll_id = coll["id"]
        user_uuid = coll.get("owner_user_id") or "__system__"
        try:
            result = await sync_knowledge_collection(
                collection_id=coll_id,
                user_uuid=user_uuid,
                older_than_seconds=3600,
                advance_schedule=True,
            )
            ren = result.get("renewal") or {}
            logger.info(
                f"CDC sync: collection={coll_id} ('{coll['name']}') "
                f"checked={result['checked']} updated={result['updated']} "
                f"errors={result['errors']} "
                f"renewal(acknowledged={len(ren.get('acknowledged') or [])} "
                f"knowledge={(ren.get('knowledge') or {}).get('signed', '-')} "
                f"agents={[(a.get('tag'), a.get('signed')) for a in (ren.get('agents') or [])]})"
            )
        except Exception as e:
            logger.error(f"CDC sync: collection={coll_id} failed: {e}", exc_info=True)


async def enable_profile_scheduler() -> None:
    """Enable user-created profile jobs and register all enabled ones."""
    from trusted_data_agent.components.settings import save_component_settings
    save_component_settings({"profile_scheduler_enabled": True}, "system")
    await _load_user_jobs()
    logger.info("Profile scheduler enabled.")


async def disable_profile_scheduler() -> None:
    """Disable user-created profile jobs and unregister them from APScheduler."""
    from trusted_data_agent.components.settings import save_component_settings
    save_component_settings({"profile_scheduler_enabled": False}, "system")
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM scheduled_tasks WHERE job_type = 'user' OR job_type IS NULL"
        ).fetchall()
    for row in rows:
        _unregister_job(row["id"])
    logger.info("Profile scheduler disabled — all user jobs unregistered.")


async def enable_platform_scheduler() -> None:
    """Enable platform maintenance jobs and register them."""
    from trusted_data_agent.components.settings import save_component_settings
    save_component_settings({"platform_scheduler_enabled": True}, "system")
    await _load_platform_jobs()
    logger.info("Platform scheduler enabled.")


async def disable_platform_scheduler() -> None:
    """Disable platform maintenance jobs and unregister them."""
    from trusted_data_agent.components.settings import save_component_settings
    save_component_settings({"platform_scheduler_enabled": False}, "system")
    for task_id in _get_platform_job_ids():
        _unregister_job(task_id)
    logger.info("Platform scheduler disabled — all platform jobs unregistered.")


async def stop_scheduler():
    """Stop APScheduler gracefully. Called at app shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Task Scheduler stopped.")
    _scheduler = None


def _register_job(task: dict):
    """Add or replace an APScheduler job for a task."""
    if not _scheduler:
        return
    schedule = task.get("schedule", "")
    task_id = task["id"]

    # Platform maintenance jobs (consumption resets) get a generous misfire
    # grace: they are idempotent + timestamp-guarded, so a late catch-up run
    # is always correct, whereas silently skipping one leaves counters
    # accumulating past their window. User jobs keep the tight 60s grace —
    # firing a user's query hours late is surprising, not helpful.
    misfire_grace = 3600 if task.get("job_type") == "platform" else 60

    # Remove existing job if present
    _unregister_job(task_id)

    try:
        if schedule.startswith("interval:"):
            # interval:300s  or  interval:10m  or  interval:1h
            interval_str = schedule[len("interval:"):]
            seconds = _parse_interval(interval_str)
            _scheduler.add_job(
                _fire_task,
                "interval",
                seconds=seconds,
                id=task_id,
                args=[task_id],
                misfire_grace_time=misfire_grace,
                coalesce=True,
                replace_existing=True,
            )
        else:
            # Cron expression: "0 9 * * 1-5"
            parts = schedule.split()
            if len(parts) != 5:
                raise ValueError(f"Invalid cron expression '{schedule}' — expected 5 fields")
            _scheduler.add_job(
                _fire_task,
                "cron",
                minute=parts[0], hour=parts[1],
                day=parts[2], month=parts[3], day_of_week=parts[4],
                id=task_id,
                args=[task_id],
                misfire_grace_time=misfire_grace,
                coalesce=True,
                replace_existing=True,
            )
        logger.info(f"Scheduled task '{task.get('name', task_id)}' registered ({schedule})")
    except Exception as e:
        logger.error(f"Failed to register job for task '{task_id}': {e}")


def _unregister_job(task_id: str):
    if _scheduler:
        try:
            _scheduler.remove_job(task_id)
        except Exception:
            pass


def _parse_interval(s: str) -> int:
    """Convert '300s', '10m', '2h', '1d' to seconds.

    Must accept every unit TDA_Scheduler advertises (instructions.json, scheduler-expert)
    and the handler's preview parser (``_parse_interval_seconds``) shows — a unit the
    preview accepts but the engine refuses is a task that lists next runs and never fires.
    """
    s = s.strip().lower()
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("m"):
        return int(s[:-1]) * 60
    if s.endswith("s"):
        return int(s[:-1])
    return int(s)  # bare number = seconds


# ── Task execution ────────────────────────────────────────────────────────────

def _fire_task(task_id: str):
    """Synchronous wrapper called by APScheduler — schedules the async execution."""
    if not _event_loop or _event_loop.is_closed():
        logger.error(f"Task Scheduler: no event loop available to fire task '{task_id}'")
        return
    # Branch: platform jobs use a lightweight maintenance path; user jobs use execute_query()
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT job_type FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        job_type = row["job_type"] if row else "user"
    except Exception:
        job_type = "user"

    if job_type == "platform":
        asyncio.run_coroutine_threadsafe(_execute_platform_job_async(task_id), _event_loop)
    else:
        asyncio.run_coroutine_threadsafe(_execute_task_async(task_id), _event_loop)


def _run_consumption_maintenance(job_name: str) -> dict:
    """Run one (or, for "__catchup__", all) consumption maintenance jobs.

    Synchronous — call via asyncio.to_thread. The DB session comes from the
    app's get_db_session() so SQLite access routes through core/sqlite_util's
    process-wide lock; the maintenance module's own create_engine() path is
    for standalone CLI use only and would bypass the lock in-process
    (see docs/Architecture/SQLITE_CONCURRENCY.md).

    Each job is internally guarded by its reset timestamp / period column,
    so running one that is not due is a no-op.
    """
    from pathlib import Path as _Path
    import sys as _sys
    _maint_dir = str(_Path(__file__).resolve().parents[3] / "maintenance")
    if _maint_dir not in _sys.path:
        _sys.path.insert(0, _maint_dir)
    from consumption_periodic_jobs import ConsumptionPeriodicJobs

    from trusted_data_agent.auth.database import get_db_session

    lowered = (job_name or "").lower()
    with get_db_session() as db_session:
        jobs = ConsumptionPeriodicJobs(session=db_session)
        if lowered == "__catchup__":
            return jobs.run_all_jobs()
        if "hourly" in lowered:
            return jobs.run_hourly_reset()
        if "daily" in lowered:
            return jobs.run_daily_reset()
        if "monthly" in lowered:
            return jobs.run_monthly_rollover()
        return {"status": "unknown", "job": job_name}


async def _execute_platform_job_async(task_id: str):
    """Execute a platform maintenance job by calling the appropriate maintenance function."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ? AND enabled = 1 AND job_type = 'platform'",
            (task_id,)
        ).fetchone()
    if not row:
        return
    task = dict(row)

    run_id = _record_run_start(task_id)
    try:
        name = task.get("name", "")
        # Run off the event loop — the maintenance jobs are synchronous DB work.
        if task_id == COST_SYNC_JOB_ID:
            result = await asyncio.to_thread(_run_cost_price_sync)
        elif task_id == DB_BACKUP_JOB_ID:
            result = await asyncio.to_thread(_run_db_backup)
        elif task_id == INPUT_EXPIRY_JOB_ID:
            result = await asyncio.to_thread(_run_input_expiry)
        else:
            result = await asyncio.to_thread(_run_consumption_maintenance, name)

        summary = f"Platform job '{name}' completed: {result}"[:500]
        _record_run_end(run_id, "success", result_summary=summary)
        logger.info(summary)
    except Exception as e:
        logger.error(f"Platform job '{task_id}' failed: {e}", exc_info=True)
        _record_run_end(run_id, "error", result_summary=str(e)[:500])


async def _emit_notification(user_uuid: str, notification: dict):
    """Push a notification to all active queues for a user (fire-and-forget)."""
    from trusted_data_agent.core.config import APP_STATE
    queues = APP_STATE.get("notification_queues", {}).get(user_uuid, set())
    for q in queues:
        asyncio.create_task(q.put(notification))


class ActionTaskError(ValueError):
    """A scheduled action its agent may not perform — refused before it is stored."""
    code = "action_not_granted"


def _validated_action_spec(spec: Optional[dict], user_uuid: str, profile_id: Optional[str]) -> Optional[str]:
    """``None`` stays ``None`` (a prompt task, byte-identical). Otherwise the spec must name an
    action and its agent must be granted it — the ONE rule every unattended face checks
    (``action_perform.unattended_grant_error``). Returns the canonical JSON to store."""
    if spec is None or spec == "":
        return None
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError as e:
            raise ActionTaskError(f"action_spec is not JSON ({e})")
    if not isinstance(spec, dict) or not str(spec.get("action") or "").strip():
        raise ActionTaskError("action_spec must name an action: {action, invocation: {key, values}}")
    inv = spec.get("invocation") if spec.get("invocation") is not None else {}
    if not isinstance(inv, dict):
        raise ActionTaskError("action_spec.invocation must be an object {key, values}")
    from trusted_data_agent.core import action_perform as AP
    from trusted_data_agent.core.config import APP_CONFIG
    profile = None
    if profile_id:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            profile = get_config_manager().get_profile(profile_id, user_uuid) or None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Task Scheduler: profile lookup for action task failed: {e}")
    why = AP.unattended_grant_error(spec["action"], profile)   # OBDA-ACT per the task's agent
    if why:
        raise ActionTaskError(f"The scheduled action {why}" if why.startswith("names")
                              else f"The scheduled action cannot run: {why}")
    clean = {"action": str(spec["action"]).strip(), "invocation": inv}
    if spec.get("connection_id"):
        clean["connection_id"] = str(spec["connection_id"])
    return json.dumps(clean, sort_keys=True)


def _action_run_status(rec: dict) -> str:
    """A run's status from its act: refused ⇒ error; performed, parked and observed are the
    action doing what its posture says ⇒ success (the record says which)."""
    return "error" if (rec or {}).get("outcome") == "refused" else "success"


async def _execute_action_task(task: dict, run_id: str) -> dict:
    """Perform a scheduled action once; record the act (with its signed receipt) on the run.
    Never raises — a failure is a refused record on an ``error`` run."""
    from trusted_data_agent.core import action_perform as AP
    user_uuid, profile_id = task["user_uuid"], task.get("profile_id")
    try:
        spec = json.loads(task.get("action_spec") or "{}")
    except ValueError:
        spec = {}
    profile = None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        profile = get_config_manager().get_profile(profile_id, user_uuid) or None if profile_id else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Task Scheduler: profile lookup failed for action task: {e}")
    rec = await asyncio.to_thread(
        AP.perform_unattended, user_uuid, profile, spec.get("action") or "", spec.get("invocation") or {},
        face="scheduler", connection_id=spec.get("connection_id"), turn_id=run_id)
    status = _action_run_status(rec)
    summary = AP.describe(rec)
    _record_run_end(run_id, status, result_summary=summary[:500], tokens_used=0, cost_usd=0.0,
                    action_record=rec)
    logger.info(f"Task '{task.get('name', task.get('id'))}' acted: {summary}")
    try:
        await _emit_notification(user_uuid, {"type": "scheduled_action_run",
                                             "payload": {"task_id": task.get("id"), "run_id": run_id,
                                                         "status": status, "summary": summary,
                                                         "outcome": rec.get("outcome")}})
    except Exception:  # noqa: BLE001
        pass
    if status == "success" and task.get("output_channel"):
        try:
            await _deliver_result(task, summary, None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Task Scheduler: action result delivery failed: {e}")
    return rec


async def _execute_task_async(task_id: str, preflight_run_id: Optional[str] = None,
                              resumption: Optional[dict] = None):
    """Execute a scheduled task through the standard execution pipeline.

    Emits the same new_session_created / rest_task_update / rest_task_complete
    notification events as the REST query pipeline so the frontend's existing
    buffer/replay/live-rendering path works identically to genie slave sessions.

    PROP-011 phase 3: a run whose turn STOPPED TO ASK ends `input_required` — the question
    reaches the Inbox and the run's own delivery channel, and the run waits. `resumption`
    (from `input_resume.accept`, through `resume_task_run`) is the person's answer: the run
    continues in the SAME session with the answer bound, and delivers its answer as any run.
    """
    import copy as _copy

    # Load task from DB. A scheduled firing runs an ENABLED task only; a manual "Run now"
    # (it arrives with its preflight run) runs a paused task too — testing a task before
    # enabling it is the point of the button. A run row that was opened is always closed:
    # returning early used to leave a manual run of a paused task `running` forever.
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ?"
            + ("" if preflight_run_id else " AND enabled = 1"), (task_id,)
        ).fetchone()
    if not row:
        if preflight_run_id:
            _record_run_end(preflight_run_id, "skipped", skip_reason="the task no longer exists")
        return

    task = dict(row)
    user_uuid = task["user_uuid"]
    profile_id = task["profile_id"]
    overlap_policy = task.get("overlap_policy", "skip")

    # Overlap policy check
    if task_id in _running_tasks and not _running_tasks[task_id].done():
        if overlap_policy == "skip":
            run_id = preflight_run_id or _record_run_start(task_id)
            _record_run_end(run_id, "skipped", skip_reason="Previous run still active (overlap_policy=skip)")
            logger.info(f"Task '{task.get('name', task_id)}' skipped — previous run still active.")
            return
        elif overlap_policy == "queue":
            try:
                await asyncio.wait_for(_running_tasks[task_id], timeout=300)
            except asyncio.TimeoutError:
                pass

    run_id = preflight_run_id or _record_run_start(task_id)
    asyncio_task = asyncio.current_task()
    if asyncio_task:
        _running_tasks[task_id] = asyncio_task

    if task.get("action_spec"):
        # PROP-008 §6 — a scheduled ACTION runs deterministically: no session, no LLM, no
        # tokens. The same unattended seam as a flow node, so the grant, the posture and
        # the receipt are the action's own.
        try:
            await _execute_action_task(task, run_id)
        finally:
            _running_tasks.pop(task_id, None)
        return

    try:
        from trusted_data_agent.core import session_manager
        from trusted_data_agent.agent.execution_service import run_agent_execution
        from trusted_data_agent.core.config import APP_STATE, APP_CONFIG
        from trusted_data_agent.core.config_manager import get_config_manager

        # Unique ID used for rest_task_update / rest_task_complete payloads
        bg_task_id = f"sched-bg-{uuid.uuid4().hex[:12]}"

        # Determine session: use pinned session if set and still valid, else create new
        pinned_session_id = task.get("session_id")
        if resumption and resumption.get("session_id"):
            pinned_session_id = resumption["session_id"]   # the conversation that asked (PROP-011)
        session_id = None
        is_pinned_session = False
        if pinned_session_id:
            try:
                existing = await session_manager.get_session(user_uuid, pinned_session_id)
                if existing and not existing.get("archived"):
                    session_id = pinned_session_id
                    is_pinned_session = True
                    logger.info(
                        f"Scheduled task '{task.get('name', task_id)}' running in "
                        f"pinned session {pinned_session_id[:12]}…"
                    )
            except Exception:
                pass
            if not session_id:
                logger.warning(
                    f"Scheduled task '{task.get('name', task_id)}': pinned session "
                    f"'{pinned_session_id}' unavailable — falling back to new session."
                )

        if not session_id:
            session_id = await session_manager.create_session(
                user_uuid=user_uuid,
                provider=APP_CONFIG.CURRENT_PROVIDER,
                llm_instance=APP_STATE.get("llm"),
                charting_intensity="medium",
                profile_id=profile_id,
                is_temporary=True,
                temporary_purpose=f"Scheduled task: {task.get('name', task_id)}",
            )

        # --- Emit new_session_created so the session appears in the sidebar immediately ---
        # Only emit for freshly-created ephemeral sessions (not for reused pinned sessions).
        if not is_pinned_session:
            try:
                session_data = await session_manager.get_session(user_uuid, session_id)
                if session_data:
                    profile_type = None
                    try:
                        cm = get_config_manager()
                        profile_obj = cm.get_profile(profile_id, user_uuid) or {}
                        profile_type = profile_obj.get("profile_type")
                    except Exception:
                        pass
                    await _emit_notification(user_uuid, {
                        "type": "new_session_created",
                        "payload": {
                            "id": session_id,
                            "name": session_data.get("name", f"Scheduled: {task.get('name', task_id)}"),
                            "created_at": session_data.get("created_at"),
                            "profile_id": session_data.get("profile_id"),
                            "profile_tag": session_data.get("profile_tag"),
                            "profile_type": profile_type,
                            "genie_metadata": {},
                            "is_temporary": True,
                            "temporary_purpose": f"Scheduled task: {task.get('name', task_id)}",
                        }
                    })
            except Exception as e:
                logger.debug(f"Task scheduler: new_session_created emit failed: {e}")

        total_input_tokens = 0
        total_output_tokens = 0
        final_answer = None

        async def _event_handler(event_data, event_type):
            nonlocal total_input_tokens, total_output_tokens, final_answer
            if isinstance(event_data, dict):
                if event_type == "token_update":
                    total_input_tokens = event_data.get("total_input", total_input_tokens)
                    total_output_tokens = event_data.get("total_output", total_output_tokens)
                if event_data.get("type") == "conversation_agent_complete":
                    payload = event_data.get("payload", {})
                    final_answer = payload.get("final_answer_text") or payload.get("answer", "")

            # --- Mirror the REST pipeline: push every event to notification queues ---
            try:
                canonical_event = _copy.deepcopy(event_data) if isinstance(event_data, dict) else {"data": str(event_data)}
                if "type" not in canonical_event:
                    canonical_event["type"] = event_type or "notification"

                if event_type == "status_indicator_update":
                    notification = {"type": "status_indicator_update", "payload": canonical_event}
                elif event_type == "session_name_update":
                    notification = {"type": "session_name_update", "payload": canonical_event}
                else:
                    notification = {
                        "type": "rest_task_update",
                        "payload": {
                            "task_id": bg_task_id,
                            "session_id": session_id,
                            "event": canonical_event,
                        },
                    }
                await _emit_notification(user_uuid, notification)
            except Exception as e:
                logger.debug(f"Task scheduler: rest_task_update emit failed: {e}")

        _shown_prompt = resumption["display_message"] if resumption else task["prompt"]
        final_result_payload = await run_agent_execution(
            user_uuid=user_uuid,
            session_id=session_id,
            user_input=resumption["user_input"] if resumption else task["prompt"],
            event_handler=_event_handler,
            profile_override_id=profile_id,
            source="scheduler",
            # PROP-011: a question this run raises names the run, so the answer comes back here
            task_id=f"sched:{task_id}:{run_id}",
            display_message=resumption["display_message"] if resumption else None,
            input_answer=resumption["input_answer"] if resumption else None,
        )

        # Extract final_answer from result payload if event handler didn't capture it
        if not final_answer and isinstance(final_result_payload, dict):
            final_answer = final_result_payload.get("final_answer") or final_result_payload.get("final_answer_text")

        result_summary = (final_answer or "")[:500]
        tokens_used = total_input_tokens + total_output_tokens

        # PROP-011 phase 3 — the turn stopped to ask: the run WAITS. The question reaches the
        # Inbox (the store lists it) and the run's own channel with a link; the run record reads
        # `input_required` (not success — the question is not a result); on the answer the run
        # continues here (`resume_task_run`); on expiry the job ends it `unanswered` and says so.
        if isinstance(final_result_payload, dict) and final_result_payload.get("input_required"):
            _q = final_result_payload.get("input_request") or {}
            from trusted_data_agent.agent import input_requests as _ir_mod
            _qtext = _ir_mod.question_text(_q) if _q.get("items") else "a question for you"
            # the question may have expired or been superseded while the turn was finishing
            try:
                from trusted_data_agent.core import input_request_store as _ir_store
                _now_q = _ir_store.get(_q.get("id"), user_uuid) if _q.get("id") else None
            except Exception:  # noqa: BLE001
                _now_q = None
            if _now_q and _now_q.get("status") in ("expired", "superseded"):
                _record_run_end(run_id, "unanswered", result_summary=f"The question expired unanswered: {_qtext}"[:500], tokens_used=tokens_used)
                return
            _record_run_end(run_id, "input_required", result_summary=f"Waiting for your input: {_qtext}"[:500], tokens_used=tokens_used)
            try:
                session_data = await session_manager.get_session(user_uuid, session_id)
                await _emit_notification(user_uuid, {
                    "type": "rest_task_complete",
                    "payload": {"task_id": bg_task_id, "session_id": session_id,
                                "turn_id": final_result_payload.get("turn_id"), "user_input": _shown_prompt,
                                "final_answer": final_answer or result_summary,
                                "profile_tag": session_data.get("profile_tag") if session_data else None,
                                "extension_specs": None, "skill_specs": None, "source": "scheduler",
                                "input_request": _q}})
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Task scheduler: input_required notice failed: {e}")
            if task.get("output_channel") and task.get("output_channel") != "files":
                from trusted_data_agent.core.branding import email_base_url, get_brand
                _link = f"{email_base_url(get_brand())}/?session={session_id}"
                await _deliver_result(task, (f"The scheduled task stopped to ask you:\n\n{_qtext}\n\n"
                                             f"Answer in Uderia (the conversation, or Assurance → Inbox): {_link}\n"
                                             f"The run waits for your answer and continues with it; unanswered, it ends "
                                             f"after the expiry and says so."), session_id, [], subject_suffix=" — needs your input")
            logger.info(f"Scheduled task '{task.get('name', task_id)}' is waiting for the user's input ({_q.get('id')}).")
            return

        # Post-run token budget check (mid-run cancellation not yet supported)
        max_tokens = task.get("max_tokens_per_run")
        if max_tokens and tokens_used > max_tokens:
            logger.warning(
                f"Task '{task.get('name', task_id)}' exceeded token budget: "
                f"{tokens_used} used > {max_tokens} limit."
            )
            _record_run_end(run_id, "error", result_summary=f"Token budget exceeded ({tokens_used} > {max_tokens})", tokens_used=tokens_used)
            _running_tasks.pop(task_id, None)
            return

        # --- Emit rest_task_complete so the frontend renders the Q&A and cleans up state ---
        try:
            session_data = await session_manager.get_session(user_uuid, session_id)
            profile_tag = session_data.get("profile_tag") if session_data else None
            await _emit_notification(user_uuid, {
                "type": "rest_task_complete",
                "payload": {
                    "task_id": bg_task_id,
                    "session_id": session_id,
                    "turn_id": final_result_payload.get("turn_id") if isinstance(final_result_payload, dict) else None,
                    "user_input": _shown_prompt,
                    "final_answer": final_answer or result_summary,
                    "profile_tag": profile_tag,
                    "extension_specs": None,
                    "skill_specs": None,
                    "source": "scheduler",
                }
            })
        except Exception as e:
            logger.debug(f"Task scheduler: rest_task_complete emit failed: {e}")

        # Deliver result via output channel. PROP-010: the `files` channel — or
        # `output_config.keep_in_files` beside email/webhook — keeps the run's answer and
        # tables in Files as one series; the other channels then name them.
        output_channel = task.get("output_channel")
        kept_files = []
        if result_summary and _keeps_in_files(task):
            from trusted_data_agent.filestore.service import keep_scheduled_run
            kept_files = await keep_scheduled_run(task, session_id, run_id)
        if output_channel and output_channel != "files" and result_summary:
            await _deliver_result(task, result_summary, session_id, kept_files)

        _record_run_end(run_id, "success", result_summary=result_summary, tokens_used=tokens_used)
        logger.info(f"Scheduled task '{task.get('name', task_id)}' completed ({tokens_used} tokens).")

    except Exception as e:
        logger.error(f"Scheduled task '{task_id}' failed: {e}", exc_info=True)
        _record_run_end(run_id, "error", result_summary=str(e)[:500])
    finally:
        _running_tasks.pop(task_id, None)


def _keeps_in_files(task: dict) -> bool:
    if task.get("output_channel") == "files":
        return True
    try:
        cfg = json.loads(task.get("output_config") or "{}") if isinstance(task.get("output_config"), str) else (task.get("output_config") or {})
    except Exception:  # noqa: BLE001
        cfg = {}
    return bool(cfg.get("keep_in_files"))


# The canonical delivery keys, and the spellings writers have used for them. TDA_Scheduler's
# manifest once told the LLM {to} / {url, token} and the Jobs form saved a webhook as {url},
# while delivery read only the canonical names — such a task ran and delivered nothing.
# Normalising HERE, at the one delivery seam, also heals tasks already stored that way.
_DELIVERY_KEY_ALIASES = {
    "to_address": ("to_address", "to", "email"),
    "webhook_url": ("webhook_url", "url"),
    "bearer_token": ("bearer_token", "token"),
}


def _delivery_config(cfg: Optional[dict]) -> dict:
    """``output_config`` with every delivery key under its canonical name (first spelling wins)."""
    out = dict(cfg or {})
    for canonical, spellings in _DELIVERY_KEY_ALIASES.items():
        if out.get(canonical):
            continue
        for alias in spellings:
            if out.get(alias):
                out[canonical] = out[alias]
                break
    return out


async def resume_task_run(task_id: str, resumption: dict, previous_run_id: Optional[str] = None) -> Optional[str]:
    """PROP-011 phase 3 — the person answered a question a scheduled run raised: continue the
    run (a new run row, linked to the one that waited) in the same session with the answer
    bound; it delivers its answer through the task's channel like any run. Returns the run id."""
    try:
        with _get_conn() as conn:
            row = conn.execute("SELECT id FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        run_id = _record_run_start(task_id)
        if previous_run_id:
            try:
                with _get_conn() as conn:
                    conn.execute("UPDATE scheduled_task_runs SET result_summary = COALESCE(result_summary, '') || ? WHERE id = ?",
                                 (f" — answered; continued as {run_id}", previous_run_id))
                    conn.commit()
            except Exception:  # noqa: BLE001
                pass
        asyncio.create_task(_execute_task_async(task_id, preflight_run_id=run_id, resumption=resumption))
        return run_id
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Task scheduler: resume of '{task_id}' failed: {e}")
        return None


INPUT_EXPIRY_JOB_ID = "platform-input-expiry"
INPUT_EXPIRY_JOB_NAME = "Needs-input expiry"


def _ensure_input_expiry_job() -> None:
    """Seed the hourly expiry of unanswered questions (PROP-011; idempotent; :20 past the hour)."""
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO scheduled_tasks (id, user_uuid, profile_id, name, prompt, schedule, enabled, job_type) "
                "VALUES (?, '__platform__', '__platform__', ?, '__platform_job__', '20 * * * *', 1, 'platform')",
                (INPUT_EXPIRY_JOB_ID, INPUT_EXPIRY_JOB_NAME))
            conn.commit()
    except Exception as e:  # noqa: BLE001 — seeding must never block the scheduler
        logger.warning(f"Task Scheduler: needs-input expiry job not seeded: {e}")


def _run_input_expiry() -> dict:
    """Flip every pending question past its expiry; for a question a scheduled run raised, end
    that run `unanswered` and say so through the task's own channel (synchronous — call via
    asyncio.to_thread; the delivery is scheduled back onto the loop)."""
    from trusted_data_agent.core import input_request_store as _store
    expired = _store.expire_due(return_rows=True)
    runs_closed = 0
    for rec in expired or []:
        tid = str(rec.get("task_id") or "")
        if not tid.startswith("sched:"):
            continue
        parts = tid.split(":")
        if len(parts) < 3:
            continue
        sched_id, run_id = parts[1], parts[2]
        try:
            # only a run that is WAITING is closed — a run whose turn is still in progress (the
            # question is raised at the turn's start) records its own outcome when it ends
            with _get_conn() as conn:
                cur = conn.execute("UPDATE scheduled_task_runs SET status = 'unanswered', completed_at = ?, "
                                   "result_summary = ? WHERE id = ? AND status = 'input_required'",
                                   (_now(), "The question expired unanswered; the run ended without a result.", run_id))
                closed = cur.rowcount == 1
                if closed:
                    conn.execute("UPDATE scheduled_tasks SET last_run_at = ?, last_run_status = 'unanswered', updated_at = ? WHERE id = ?",
                                 (_now(), _now(), sched_id))
                conn.commit()
            if not closed:
                continue
            runs_closed += 1
            with _get_conn() as conn:
                row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (sched_id,)).fetchone()
            task = dict(row) if row else None
            if task and task.get("output_channel") and task.get("output_channel") != "files" and _event_loop:
                from trusted_data_agent.agent import input_requests as _ir_mod
                body = (f"The scheduled task's question was not answered before it expired, so the run ended "
                        f"without a result.\n\nThe question was: {_ir_mod.question_text(rec)}")
                asyncio.run_coroutine_threadsafe(
                    _deliver_result(task, body, rec.get("session_id") or "", [], subject_suffix=" — expired unanswered"),
                    _event_loop)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Task Scheduler: closing run {run_id} after expiry failed: {e}")
    return {"expired": len(expired or []), "runs_closed": runs_closed}


async def _deliver_result(task: dict, result: str, session_id: str, kept_files: Optional[list] = None, *,
                          subject_suffix: str = ""):
    """Deliver task result via the configured output channel."""
    channel = task.get("output_channel")
    cfg_raw = task.get("output_config")
    cfg = _delivery_config(json.loads(cfg_raw) if cfg_raw else {})
    if kept_files:
        # the email names the kept files instead of attaching them (download stays the one exit)
        result = (result.rstrip() + "\n\nKept in Files: "
                  + ", ".join(f.get("filename") for f in kept_files) + " (open Uderia → Workspace → Files)")
    task_name = task.get("name", task["id"])
    from trusted_data_agent.core.branding import get_brand
    subject = f"[{get_brand().short_name}] Scheduled task: {task_name}{subject_suffix}"

    try:
        if channel == "email":
            to_addr = cfg.get("to_address")
            if not to_addr:
                logger.warning(f"Task '{task_name}': email channel configured but no to_address.")
                return
            from trusted_data_agent.auth.email_service import EmailService
            await EmailService.send_email(to_addr, subject, result)

        elif channel == "webhook":
            import httpx
            url = cfg.get("webhook_url")
            if not url:
                return
            headers = {"Content-Type": "application/json"}
            bearer = cfg.get("bearer_token")
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"
            payload = {
                "task_id": task["id"],
                "task_name": task_name,
                "session_id": session_id,
                "result": result,
                "files": [{"id": f.get("id"), "filename": f.get("filename"), "media_type": f.get("media_type"),
                           "size_bytes": f.get("size_bytes"), "download_url": f"/api/v1/files/{f.get('id')}/download"}
                          for f in (kept_files or [])],
                "completed_at": _now(),
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                await client.post(url, json=payload, headers=headers)

        elif channel == "google_mail":
            user_uuid = task.get("user_uuid")
            if not user_uuid:
                logger.warning(f"Task '{task_name}': google_mail channel — no user_uuid on task, cannot send.")
                return
            from trusted_data_agent.connectors import registry as _connectors
            _google = _connectors.get("google")
            if _google is None:
                logger.warning(f"Task '{task_name}': google_mail channel needs the Google connector (enterprise package).")
                return
            tokens = await _google.get_tokens(user_uuid)
            if not tokens or not tokens.get("access_token"):
                logger.warning(f"Task '{task_name}': google_mail — no Google account connected for user {user_uuid}.")
                return
            from trusted_data_agent.core.platform_connector_registry import get_server_credentials
            creds = get_server_credentials("uderia-google")
            to_addr = cfg.get("to_address") or tokens.get("email") or ""
            if not to_addr:
                logger.warning(f"Task '{task_name}': google_mail — no recipient address configured.")
                return
            import base64
            from email.mime.text import MIMEText
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build as _gapi_build
            google_creds = Credentials(
                token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds.get("GOOGLE_CLIENT_ID", ""),
                client_secret=creds.get("GOOGLE_CLIENT_SECRET", ""),
            )
            msg = MIMEText(result)
            msg["to"] = to_addr
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            service = _gapi_build("gmail", "v1", credentials=google_creds, cache_discovery=False)
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info(f"Task '{task_name}': google_mail delivered to {to_addr}.")

    except Exception as e:
        logger.warning(f"Failed to deliver result for task '{task_name}' via {channel}: {e}")


# ── Platform job admin helpers ────────────────────────────────────────────────

def list_platform_jobs() -> list[dict]:
    """Return all platform jobs with last/next run info (admin view)."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE job_type = 'platform' ORDER BY name"
        ).fetchall()
    result = []
    for row in rows:
        task = dict(row)
        task["_next_run"] = _get_next_run_time(task["id"])
        result.append(task)
    return result


def update_platform_job(task_id: str, updates: dict) -> Optional[dict]:
    """Enable/disable a single platform job. Only 'enabled' key is allowed."""
    allowed = {"enabled"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return None
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE scheduled_tasks SET {set_clause} WHERE id = ? AND job_type = 'platform'",
            values,
        )
        conn.commit()
    # Sync APScheduler registration
    if _scheduler and _scheduler.running:
        if fields.get("enabled") == 0:
            _unregister_job(task_id)
        elif fields.get("enabled") == 1 and is_platform_scheduler_enabled():
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
                ).fetchone()
            if row:
                _register_job(dict(row))
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
        ).fetchone()
    return dict(row) if row else None


def list_platform_job_runs(task_id: str, limit: int = 20) -> list[dict]:
    """Return recent runs for a platform job (no ownership check — admin only)."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_task_runs WHERE task_id = ? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


async def run_platform_job_now(task_id: str) -> None:
    """Admin-triggered immediate execution of a platform maintenance job."""
    import asyncio
    asyncio.create_task(_execute_platform_job_async(task_id))


def _get_next_run_time(task_id: str) -> Optional[str]:
    """Return the next scheduled run time for a job as ISO string, or None."""
    if not _scheduler:
        return None
    try:
        job = _scheduler.get_job(task_id)
        if job and job.next_run_time:
            return job.next_run_time.isoformat()
    except Exception:
        pass
    return None


# ── Manual trigger ────────────────────────────────────────────────────────────

async def run_task_now(task_id: str, user_uuid: str) -> str:
    """Manually trigger a task immediately. Returns run_id."""
    task = get_task(task_id, user_uuid)
    if not task:
        raise ValueError(f"Task '{task_id}' not found.")
    run_id = _record_run_start(task_id)
    asyncio.create_task(_execute_task_async(task_id, preflight_run_id=run_id))
    return run_id
