"""
Consumption time-window healing — regression gate.

Root cause under test: the hourly/daily/monthly consumption counters
(`requests_this_hour`, `requests_today`, `*_tokens_today`, monthly
`total_*` + `current_period`) only reset when a reset path fires, and all
three paths were broken or incomplete:
  1. the lazy reset lived ONLY in check_rate_limits (never called when
     rate limiting is disabled — the default), so tokens_today accumulated
     forever on most installs;
  2. the platform daily maintenance job reset requests_today but SILENTLY
     OMITTED the token-today counters (an inline copy that predated the
     columns), so even a 24/7 production server accumulated;
  3. APScheduler's jobstore is in-memory, so a midnight firing missed
     while the server was down is invisible after restart (no catch-up).

The fix: ONE set of reset semantics (ConsumptionManager.apply_hourly_reset /
apply_daily_reset / _rollover) applied by a lazy healer on EVERY consumption
access (get_or_create_consumption → heal_time_windows), shared verbatim by
the maintenance jobs, plus a startup catch-up sweep in task_scheduler.

Also pins the all-time invariant the admin "Tokens Consumed" column relies
on: SUM(consumption_periods_archive) + current period totals == all-time,
across any number of rollovers (the consumption_turns ledger is pruned to
90 days, so it must NOT be the source of an all-time sum).

CI-pure: in-memory SQLite via the real SQLAlchemy models + the real
ConsumptionManager / ConsumptionPeriodicJobs. No server, no network.

Run:  PYTHONPATH=src python3 test/test_consumption_healing.py
"""
import sys
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "maintenance"))

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from trusted_data_agent.auth.models import (
    Base, User, UserConsumption, ConsumptionTurn, ConsumptionPeriodsArchive,
    ConsumptionProfile,
)
from trusted_data_agent.auth.consumption_manager import ConsumptionManager

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


def _fresh_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Only the tables under test — a full create_all trips a pre-existing
    # duplicate-index declaration on an unrelated table (oauth_accounts).
    Base.metadata.create_all(engine, tables=[
        User.__table__, ConsumptionProfile.__table__, UserConsumption.__table__,
        ConsumptionTurn.__table__, ConsumptionPeriodsArchive.__table__,
    ])
    return sessionmaker(bind=engine)()


def _mk_user(db, uid="u-1"):
    user = User(id=uid, username=f"user-{uid}", email=f"{uid}@x.test", password_hash="x")
    db.add(user)
    db.commit()
    return user


def _stale_consumption(db, uid="u-1", *, months_stale=True, day_stale=True, hour_stale=True):
    """A record shaped like the real-world failure: counters accumulated,
    boundaries in the past (mirrors the live DB observed with day_reset_at
    8 days stale and current_period 3 months stale)."""
    now = datetime.now(timezone.utc)
    c = UserConsumption(
        user_id=uid,
        current_period=(now - timedelta(days=95)).strftime("%Y-%m") if months_stale else now.strftime("%Y-%m"),
        period_started_at=now - timedelta(days=95),
        hour_reset_at=(now - timedelta(hours=5)) if hour_stale else (now + timedelta(hours=1)),
        day_reset_at=(now - timedelta(days=8)) if day_stale else (now + timedelta(days=1)),
        total_input_tokens=26_000_000,
        total_output_tokens=500_000,
        total_tokens=26_500_000,
        input_tokens_today=10_000_000,
        output_tokens_today=285_000,
        tokens_today=10_285_000,
        requests_this_hour=7,
        requests_today=120,
        successful_turns=700,
        total_turns=739,
        estimated_cost_usd=1_000_000,
    )
    db.add(c)
    db.commit()
    return c


# ---------------------------------------------------------------------------

def t_fresh_record_is_noop():
    """A record whose windows are all current must not be touched (healing
    is guarded, never a blind reset)."""
    db = _fresh_session()
    _mk_user(db)
    c = _stale_consumption(db, months_stale=False, day_stale=False, hour_stale=False)
    mgr = ConsumptionManager(db)
    changed = mgr.heal_time_windows(c)
    check("current windows: heal reports no change", changed is False)
    check("current windows: tokens_today untouched", c.tokens_today == 10_285_000)
    check("current windows: monthly totals untouched", c.total_tokens == 26_500_000)
    check("current windows: no archive row created",
          db.query(ConsumptionPeriodsArchive).count() == 0)


def t_daily_reset_heals_token_counters():
    """The core defect: a stale day boundary must reset the TOKEN counters,
    not just requests_today."""
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db, months_stale=False)
    mgr = ConsumptionManager(db)
    c = mgr.get_or_create_consumption("u-1")  # heals on access
    check("daily heal: input_tokens_today reset", c.input_tokens_today == 0)
    check("daily heal: output_tokens_today reset", c.output_tokens_today == 0)
    check("daily heal: tokens_today reset", c.tokens_today == 0)
    check("daily heal: requests_today reset", c.requests_today == 0)
    check("daily heal: peak_requests_per_day tracked before reset",
          c.peak_requests_per_day == 120)
    now = datetime.now(timezone.utc)
    expected_day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    dr = c.day_reset_at if c.day_reset_at.tzinfo else c.day_reset_at.replace(tzinfo=timezone.utc)
    check("daily heal: day_reset_at advanced to next midnight", dr == expected_day)
    check("daily heal: monthly totals NOT touched by a daily reset",
          c.total_tokens == 26_500_000)


def t_hourly_reset_heals():
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db, months_stale=False, day_stale=False)
    mgr = ConsumptionManager(db)
    c = mgr.get_or_create_consumption("u-1")
    check("hourly heal: requests_this_hour reset", c.requests_this_hour == 0)
    check("hourly heal: peak_requests_per_hour tracked", c.peak_requests_per_hour == 7)
    check("hourly heal: daily token counters untouched (day window current)",
          c.tokens_today == 10_285_000)


def t_monthly_rollover_archives_then_resets():
    """A stale current_period must archive the finished period's totals and
    reset the monthly counters — the invariant behind the all-time sum."""
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db)  # months + day + hour all stale
    mgr = ConsumptionManager(db)
    c = mgr.get_or_create_consumption("u-1")
    now = datetime.now(timezone.utc)

    check("rollover: current_period healed", c.current_period == now.strftime("%Y-%m"))
    check("rollover: monthly totals reset", c.total_tokens == 0 and c.total_input_tokens == 0)
    rows = db.query(ConsumptionPeriodsArchive).all()
    check("rollover: exactly one archive row", len(rows) == 1, f"got {len(rows)}")
    if rows:
        check("rollover: archive carries the finished period's totals",
              rows[0].total_tokens == 26_500_000 and rows[0].total_input_tokens == 26_000_000)
        check("rollover: archive keyed to the OLD period",
              rows[0].period == (now - timedelta(days=95)).strftime("%Y-%m"))
    check("rollover: daily counters also reset (day window was stale)",
          c.tokens_today == 0)

    # Idempotence: healing again must not create a second archive row.
    changed = mgr.heal_time_windows(c)
    check("rollover: second heal is a no-op", changed is False)
    check("rollover: no duplicate archive row",
          db.query(ConsumptionPeriodsArchive).count() == 1)


def t_alltime_invariant_across_rollover():
    """SUM(archive) + current totals == all-time, before and after new
    activity lands in the fresh period. This is what the admin 'Tokens
    Consumed' column reads — it must survive consumption_turns pruning,
    so it must never depend on the turn ledger."""
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db)
    mgr = ConsumptionManager(db)
    mgr.get_or_create_consumption("u-1")  # triggers rollover

    def alltime():
        arch = db.query(func.coalesce(func.sum(ConsumptionPeriodsArchive.total_tokens), 0)).scalar()
        cur = db.query(UserConsumption).filter_by(user_id="u-1").one().total_tokens
        return int(arch) + int(cur)

    check("invariant: archive+current equals pre-rollover all-time",
          alltime() == 26_500_000, f"got {alltime()}")

    # New activity in the fresh period
    mgr.record_turn(
        user_id="u-1", session_id="s-1", turn_number=1,
        input_tokens=1000, output_tokens=200,
        provider="Google", model="gemini", status="success",
    )
    check("invariant: holds after new turns land in the fresh period",
          alltime() == 26_501_200, f"got {alltime()}")

    # Prune the turn ledger entirely — the all-time sum must not move.
    db.query(ConsumptionTurn).delete()
    db.commit()
    check("invariant: unaffected by consumption_turns pruning",
          alltime() == 26_501_200)


def t_record_turn_lands_in_fresh_window():
    """A turn recorded after a stale boundary must count ONLY itself in the
    daily window (heal first, then add) — not pile onto the stale total."""
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db, months_stale=False)
    mgr = ConsumptionManager(db)
    mgr.record_turn(
        user_id="u-1", session_id="s-1", turn_number=1,
        input_tokens=500, output_tokens=100,
        provider="Google", model="gemini", status="success",
    )
    c = db.query(UserConsumption).filter_by(user_id="u-1").one()
    check("record_turn: tokens_today is exactly the new turn", c.tokens_today == 600,
          f"got {c.tokens_today}")
    check("record_turn: monthly total accumulated on top of the period",
          c.total_tokens == 26_500_600)


def t_naive_datetimes_tolerated():
    """SQLite hands back naive datetimes; healing must normalize instead of
    raising (the old check_rate_limits carried this guard — it must survive
    the refactor)."""
    db = _fresh_session()
    _mk_user(db)
    c = _stale_consumption(db, months_stale=False)
    # Force naive datetimes as the DB would return them
    c.day_reset_at = (datetime.now() - timedelta(days=2)).replace(tzinfo=None)
    c.hour_reset_at = (datetime.now() - timedelta(hours=2)).replace(tzinfo=None)
    db.commit()
    mgr = ConsumptionManager(db)
    try:
        healed = mgr.get_or_create_consumption("u-1")
        check("naive datetimes: healed without raising", True)
        check("naive datetimes: daily tokens reset", healed.tokens_today == 0)
    except Exception as e:
        check("naive datetimes: healed without raising", False, e)


def t_check_rate_limits_still_enforces():
    """The refactor removed check_rate_limits' inline resets — the limit
    checks themselves must still work, against HEALED counters."""
    db = _fresh_session()
    # The limit comes from the user's live profile (auth/consumption_limits.py), not the
    # cached column on the consumption row — test_consumption_limits.py pins that.
    db.add(ConsumptionProfile(name="Tight", prompts_per_hour=1000, prompts_per_day=100,
                              config_changes_per_hour=10, is_default=True))
    db.commit()
    _mk_user(db)
    c = _stale_consumption(db, months_stale=False, day_stale=False, hour_stale=False)
    # requests_today=120 in a CURRENT window, over the profile's 100/day
    mgr = ConsumptionManager(db)
    allowed, reason = mgr.check_rate_limits("u-1")
    check("rate limits: current-window overage still blocks", allowed is False)

    # Same overage but the day window is STALE → healed to 0 → allowed.
    c.day_reset_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()
    allowed, reason = mgr.check_rate_limits("u-1")
    check("rate limits: stale window heals then allows", allowed is True, reason)


def t_periodic_daily_job_resets_tokens():
    """The maintenance daily job must reset the token counters too — via the
    SAME apply_daily_reset helper (the inline copy it replaces omitted them)."""
    from consumption_periodic_jobs import ConsumptionPeriodicJobs
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db, months_stale=False)
    jobs = ConsumptionPeriodicJobs(session=db)
    result = jobs.run_daily_reset()
    c = db.query(UserConsumption).filter_by(user_id="u-1").one()
    check("daily job: updated the stale record", result["updated_count"] == 1)
    check("daily job: tokens_today reset (the omitted counters)",
          c.tokens_today == 0 and c.input_tokens_today == 0 and c.output_tokens_today == 0)
    check("daily job: requests_today reset", c.requests_today == 0)

    # Guarded: running again is a no-op.
    result2 = jobs.run_daily_reset()
    check("daily job: second run touches nothing", result2["updated_count"] == 0)


def t_periodic_monthly_job_rolls_over():
    from consumption_periodic_jobs import ConsumptionPeriodicJobs
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db)
    jobs = ConsumptionPeriodicJobs(session=db)
    result = jobs.run_monthly_rollover()
    c = db.query(UserConsumption).filter_by(user_id="u-1").one()
    check("monthly job: archived one user", result["archived_count"] == 1)
    check("monthly job: period healed",
          c.current_period == datetime.now(timezone.utc).strftime("%Y-%m"))
    check("monthly job: archive row exists",
          db.query(ConsumptionPeriodsArchive).count() == 1)


def t_reset_semantics_defined_once():
    """Structural: the maintenance jobs must call the shared apply_* helpers
    and carry NO inline copy of the reset assignments (the defect class:
    a hand-maintained duplicate that silently missed the token counters)."""
    src = (_ROOT / "maintenance" / "consumption_periodic_jobs.py").read_text()
    # Strip comments — prose about the code is not the code.
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    check("one source: daily job delegates to apply_daily_reset",
          "apply_daily_reset(" in code)
    check("one source: hourly job delegates to apply_hourly_reset",
          "apply_hourly_reset(" in code)
    check("one source: no inline requests_today reset survives",
          "requests_today = 0" not in code)
    check("one source: no inline tokens_today reset appears",
          "tokens_today = 0" not in code)

    mgr_src = (_ROOT / "src/trusted_data_agent/auth/consumption_manager.py").read_text()
    mgr_code = "\n".join(l.split("#")[0] for l in mgr_src.splitlines())
    check("one source: check_rate_limits carries no inline reset",
          mgr_code.count("requests_today = 0") == 1  # only inside apply_daily_reset
          and mgr_code.count("tokens_today = 0") == 3)  # input/output/total, once each


def t_scheduler_wiring():
    """Structural: the in-process scheduler path must pass a managed session
    (sqlite_util lock discipline) and sweep at startup."""
    src = (_ROOT / "src/trusted_data_agent/core/task_scheduler.py").read_text()
    check("scheduler: platform jobs run on a managed get_db_session",
          "ConsumptionPeriodicJobs(session=" in src)
    check("scheduler: no private db_path engine for in-process jobs",
          "ConsumptionPeriodicJobs(db_path" not in src)
    check("scheduler: startup catch-up sweep present",
          '"__catchup__"' in src and "run_all_jobs()" in src)
    check("scheduler: platform jobs get the wide misfire grace",
          'misfire_grace = 3600 if task.get("job_type") == "platform"' in src)
    check("scheduler: grace is actually wired into add_job",
          src.count("misfire_grace_time=misfire_grace") == 2)


def t_admin_endpoint_lifetime_source():
    """Structural: the admin consumption endpoint's lifetime sums must come
    from archive+current, never from the prunable consumption_turns ledger."""
    src = (_ROOT / "src/trusted_data_agent/api/rest_routes.py").read_text()
    start = src.index("def get_all_users_consumption")
    end = src.index("def ", start + 10)
    body = src[start:end]
    check("admin endpoint: sums ConsumptionPeriodsArchive",
          "ConsumptionPeriodsArchive.total_tokens" in body)
    check("admin endpoint: adds the current period on top",
          "consumption.total_tokens" in body)
    check("admin endpoint: does NOT sum the prunable turn ledger",
          "func.sum(ConsumptionTurn" not in body)
    check("admin endpoint: exposes lifetime_total_tokens",
          "lifetime_total_tokens" in body)
    check("admin endpoint: default sort orders by the DISPLAYED lifetime sum",
          "query.order_by(lifetime_total.desc())" in body)
    check("admin endpoint: lifetime ordering happens in SQL before pagination",
          body.index("order_by(lifetime_total") < body.index("query.limit(limit)"))


def t_failed_turn_statuses_count():
    """The engines write ``failed`` / ``error`` / ``cancelled``; the counter used to test
    for ``"failure"`` (a word nothing emits) so ``failed_turns`` stayed 0 forever. A
    failed or errored turn is a failed turn; a cancelled one is neither — it still
    lowers the success rate because it stays in the denominator (the tooltip says so)."""
    from trusted_data_agent.auth.consumption_manager import FAILED_TURN_STATUSES
    check("statuses: the engines' words are the failed set",
          FAILED_TURN_STATUSES == {"failure", "failed", "error"}, repr(FAILED_TURN_STATUSES))
    db = _fresh_session()
    _mk_user(db)
    _stale_consumption(db, months_stale=False)
    mgr = ConsumptionManager(db)
    c0 = db.query(UserConsumption).filter_by(user_id="u-1").one()
    before = (c0.successful_turns, c0.failed_turns, c0.total_turns)
    for n, st in enumerate(("success", "failed", "error", "cancelled"), start=1):
        mgr.record_turn(user_id="u-1", session_id="s-1", turn_number=n,
                        input_tokens=10, output_tokens=1,
                        provider="Google", model="gemini", status=st)
    c = db.query(UserConsumption).filter_by(user_id="u-1").one()
    delta = (c.successful_turns - before[0], c.failed_turns - before[1], c.total_turns - before[2])
    check("statuses: failed + error are counted as failed turns", delta[1] == 2, f"got {delta}")
    check("statuses: one success", delta[0] == 1, f"got {delta}")
    check("statuses: every turn stays in the denominator (cancelled included)",
          delta[2] == 4, f"got {delta}")


TESTS = [
    t_fresh_record_is_noop,
    t_failed_turn_statuses_count,
    t_daily_reset_heals_token_counters,
    t_hourly_reset_heals,
    t_monthly_rollover_archives_then_resets,
    t_alltime_invariant_across_rollover,
    t_record_turn_lands_in_fresh_window,
    t_naive_datetimes_tolerated,
    t_check_rate_limits_still_enforces,
    t_periodic_daily_job_resets_tokens,
    t_periodic_monthly_job_rolls_over,
    t_reset_semantics_defined_once,
    t_scheduler_wiring,
    t_admin_endpoint_lifetime_source,
]

if __name__ == "__main__":
    for t in TESTS:
        print(f"\n[{t.__name__}]")
        try:
            t()
        except Exception:
            FAIL += 1
            print(f"  ✗ {t.__name__} RAISED:")
            traceback.print_exc()
    print(f"\n{'='*60}\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
