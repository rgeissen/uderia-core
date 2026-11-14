"""
Consumption tracking manager for real-time token usage and rate limiting.

This module provides high-performance consumption tracking with dual-write pattern:
- Primary: File-based session storage (source of truth)
- Secondary: Database consumption cache (performance optimization)

Design principles:
- O(1) lookups for rate limiting and quota checks
- Atomic updates to prevent race conditions
- Graceful degradation if DB is unavailable
- Periodic reconciliation with file-based source of truth
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from .models import (
    UserConsumption, ConsumptionTurn, ConsumptionPeriodsArchive,
    User, ConsumptionProfile
)

logger = logging.getLogger(__name__)

# The turn statuses that count as a FAILED turn. The engines write ``failed`` (ReAct),
# ``error`` / ``cancelled`` (Optimize); the counter used to test for ``"failure"``, a
# word nothing emits, so ``failed_turns`` stayed 0 forever while the success rate still
# fell (non-success turns remained in the denominator). A cancelled turn is not an
# agent failure: it is neither success nor failed, and the rate's tooltip says so.
FAILED_TURN_STATUSES = frozenset({"failure", "failed", "error"})


class ConsumptionManager:
    """Manages user consumption tracking and enforcement."""
    
    def __init__(self, db_session: Session):
        """
        Initialize consumption manager.
        
        Args:
            db_session: SQLAlchemy database session
        """
        self.db = db_session
    
    # ------------------------------------------------------------------
    # TIME-WINDOW HEALING
    #
    # The hourly/daily/monthly counters are only meaningful relative to
    # their reset boundaries. The boundaries are enforced by three
    # cooperating paths that MUST agree:
    #   1. this lazy healer — runs on EVERY consumption access via
    #      get_or_create_consumption(), so any user activity self-heals
    #      the record even if no scheduled job ever fires;
    #   2. the platform maintenance jobs
    #      (maintenance/consumption_periodic_jobs.py), which sweep ALL
    #      users (including idle ones) and call the same apply_* helpers;
    #   3. the startup catch-up sweep in core/task_scheduler.py, which
    #      covers resets missed while the server was down (APScheduler's
    #      jobstore is in-memory, so a missed midnight firing is
    #      invisible to it after a restart).
    # The reset semantics are defined ONCE in the apply_* helpers below —
    # never inline a reset elsewhere (an earlier inline copy in the daily
    # maintenance job silently omitted the token-today counters).
    # ------------------------------------------------------------------

    @staticmethod
    def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
        """Normalize a possibly-naive DB datetime to timezone-aware UTC."""
        if dt is not None and dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    @staticmethod
    def apply_hourly_reset(consumption: UserConsumption, now: datetime) -> None:
        """Reset the hourly window. Pure state change — the caller commits."""
        if consumption.requests_this_hour > consumption.peak_requests_per_hour:
            consumption.peak_requests_per_hour = consumption.requests_this_hour
        consumption.requests_this_hour = 0
        consumption.hour_reset_at = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    @staticmethod
    def apply_daily_reset(consumption: UserConsumption, now: datetime) -> None:
        """Reset the daily window — requests AND the daily token counters.
        Pure state change — the caller commits."""
        if consumption.requests_today > consumption.peak_requests_per_day:
            consumption.peak_requests_per_day = consumption.requests_today
        consumption.requests_today = 0
        consumption.input_tokens_today = 0
        consumption.output_tokens_today = 0
        consumption.tokens_today = 0
        consumption.day_reset_at = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    def heal_time_windows(self, consumption: UserConsumption, now: Optional[datetime] = None) -> bool:
        """
        Bring all time-windowed counters up to date (monthly rollover,
        daily reset, hourly reset). Mutates the record without committing.

        Returns:
            True if anything changed (the caller should commit).
        """
        now = now or datetime.now(timezone.utc)
        changed = False

        # Normalize for comparison only — SQLite returns naive datetimes on
        # every load, so writing tzinfo back would mark EVERY access as
        # changed (a write per read, forever). The stored value is UTC by
        # convention; the apply_* helpers write fresh aware boundaries.
        hour_reset = self._as_utc(consumption.hour_reset_at)
        day_reset = self._as_utc(consumption.day_reset_at)

        # Monthly rollover first — it archives the finished period before
        # the daily/hourly windows are touched.
        if consumption.current_period != now.strftime("%Y-%m"):
            self._rollover(consumption, now)
            changed = True

        if hour_reset is None or now >= hour_reset:
            self.apply_hourly_reset(consumption, now)
            changed = True

        if day_reset is None or now >= day_reset:
            self.apply_daily_reset(consumption, now)
            changed = True

        if changed:
            consumption.last_updated_at = now
        return changed

    def get_or_create_consumption(self, user_id: str) -> UserConsumption:
        """
        Get or create consumption record for user.

        Existing records are self-healed on every access: overdue hourly/
        daily resets and monthly rollovers are applied here, so every
        consumer path (rate check, quota check, turn recording, summary
        reads) sees current windows even if no scheduled job ever fired.

        Args:
            user_id: User ID

        Returns:
            UserConsumption record (committed to DB)
        """
        consumption = self.db.query(UserConsumption).filter_by(user_id=user_id).first()

        if consumption:
            if self.heal_time_windows(consumption):
                self.db.commit()
            return consumption

        if not consumption:
            # Get user and their consumption profile
            user = self.db.query(User).filter_by(id=user_id).first()
            if not user:
                raise ValueError(f"User {user_id} not found")
            
            from .consumption_limits import effective_limits
            limits = effective_limits(self.db, user)

            # Create new consumption record
            now = datetime.now(timezone.utc)
            current_period = now.strftime("%Y-%m")
            
            consumption = UserConsumption(
                user_id=user_id,
                current_period=current_period,
                period_started_at=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
                hour_reset_at=now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1),
                day_reset_at=now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
                input_tokens_limit=limits.input_tokens_per_month,
                output_tokens_limit=limits.output_tokens_per_month,
                prompts_per_hour_limit=limits.prompts_per_hour if limits.prompts_per_hour is not None else 100,
                prompts_per_day_limit=limits.prompts_per_day if limits.prompts_per_day is not None else 1000
            )
            
            self.db.add(consumption)
            self.db.commit()
            self.db.refresh(consumption)
            
            logger.info(f"Created consumption record for user {user_id}, period {current_period}")
        
        return consumption
    
    def current_limits(self, user_id: str, consumption: Optional[UserConsumption] = None):
        """The limits that apply to the user NOW (auth/consumption_limits.py — the resolver
        the UI path's ConsumptionEnforcer uses too), with the cached columns on the
        consumption row brought up to date when they differ.

        The cached ``*_limit`` columns were copied from the profile when the row was
        created and never refreshed, so a reassigned or edited profile, the Global
        Override and the admin exemption never reached the per-turn check. The check
        now reads the live limits; the cache is kept only for the admin views that
        display it, and is written only when a value actually changed.
        """
        from .consumption_limits import effective_limits_for
        limits = effective_limits_for(self.db, user_id)
        if consumption is not None and not limits.exempt:
            changed = False
            for column, value, nullable in (
                ("input_tokens_limit", limits.input_tokens_per_month, True),
                ("output_tokens_limit", limits.output_tokens_per_month, True),
                ("prompts_per_hour_limit", limits.prompts_per_hour, False),
                ("prompts_per_day_limit", limits.prompts_per_day, False),
            ):
                if value is None and not nullable:
                    continue                      # NOT NULL column: "no limit" is not storable
                if getattr(consumption, column) != value:
                    setattr(consumption, column, value)
                    changed = True
            if changed:
                self.db.commit()
        return limits

    def check_rate_limits(self, user_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if user has exceeded rate limits, against the limits that apply now.

        Args:
            user_id: User ID

        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
        """
        from .consumption_limits import INACTIVE_MESSAGE
        # get_or_create_consumption() heals overdue hourly/daily/monthly
        # windows (heal_time_windows), so the counters checked below are
        # always current — no inline reset logic here (the reset semantics
        # live ONCE in the apply_* helpers).
        consumption = self.get_or_create_consumption(user_id)
        limits = self.current_limits(user_id, consumption)
        if limits.exempt:
            return True, None
        if not limits.is_active:
            return False, INACTIVE_MESSAGE

        if limits.prompts_per_hour is not None and consumption.requests_this_hour >= limits.prompts_per_hour:
            return False, f"Hourly limit exceeded ({limits.prompts_per_hour} requests/hour)"

        if limits.prompts_per_day is not None and consumption.requests_today >= limits.prompts_per_day:
            return False, f"Daily limit exceeded ({limits.prompts_per_day} requests/day)"

        return True, None

    def check_token_quota(self, user_id: str) -> Tuple[bool, Optional[str]]:
        """
        Check if user has exceeded the monthly token quota that applies now.

        Args:
            user_id: User ID

        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
        """
        from .consumption_limits import INACTIVE_MESSAGE
        consumption = self.get_or_create_consumption(user_id)
        limits = self.current_limits(user_id, consumption)
        if limits.exempt:
            return True, None
        if not limits.is_active:
            return False, INACTIVE_MESSAGE

        if limits.input_tokens_per_month is not None:
            if consumption.total_input_tokens >= limits.input_tokens_per_month:
                return False, f"Input token quota exceeded ({limits.input_tokens_per_month} tokens/month)"

        if limits.output_tokens_per_month is not None:
            if consumption.total_output_tokens >= limits.output_tokens_per_month:
                return False, f"Output token quota exceeded ({limits.output_tokens_per_month} tokens/month)"

        return True, None
    
    def increment_request_counter(self, user_id: str) -> None:
        """
        Increment request counters (hourly, daily, velocity).
        Call this at the START of each request.
        
        Args:
            user_id: User ID
        """
        consumption = self.get_or_create_consumption(user_id)
        now = datetime.now(timezone.utc)
        
        # Increment counters
        consumption.requests_this_hour += 1
        consumption.requests_today += 1
        
        # Update peak tracking
        if consumption.requests_this_hour > consumption.peak_requests_per_hour:
            consumption.peak_requests_per_hour = consumption.requests_this_hour
        
        if consumption.requests_today > consumption.peak_requests_per_day:
            consumption.peak_requests_per_day = consumption.requests_today
        
        consumption.last_updated_at = now
        self.db.commit()
    
    def record_turn(
        self,
        user_id: str,
        session_id: str,
        turn_number: int,
        input_tokens: int,
        output_tokens: int,
        provider: str,
        model: str,
        status: str,
        rag_used: bool = False,
        rag_tokens_saved: int = 0,
        cost_usd_cents: int = 0,
        user_query: str = None,
        session_name: str = None
    ) -> None:
        """
        Record a completed turn with full metrics.
        Call this at the END of each turn.
        
        Args:
            user_id: User ID
            session_id: Session ID
            turn_number: Turn number (1-indexed)
            input_tokens: Input token count
            output_tokens: Output token count
            provider: LLM provider name
            model: Model name
            status: Turn status (success, failure, partial)
            rag_used: Whether RAG was used
            rag_tokens_saved: Tokens saved by RAG efficiency
            cost_usd_cents: Cost in micro-dollars (USD × 1,000,000)
        """
        consumption = self.get_or_create_consumption(user_id)
        now = datetime.now(timezone.utc)
        
        # Update token counts (monthly cumulative)
        total_tokens = input_tokens + output_tokens
        consumption.total_input_tokens += input_tokens
        consumption.total_output_tokens += output_tokens
        consumption.total_tokens += total_tokens

        # Update daily token counts (reset at midnight UTC via day_reset_at)
        consumption.input_tokens_today += input_tokens
        consumption.output_tokens_today += output_tokens
        consumption.tokens_today += total_tokens

        # Update quality metrics
        consumption.total_turns += 1
        if status == "success":
            consumption.successful_turns += 1
        elif status in FAILED_TURN_STATUSES:
            consumption.failed_turns += 1
        
        # Update RAG metrics
        if rag_used:
            consumption.rag_guided_turns += 1
            consumption.rag_output_tokens_saved += rag_tokens_saved
        
        # Update cost
        consumption.estimated_cost_usd += cost_usd_cents
        if rag_used and rag_tokens_saved > 0:
            # Estimate cost savings (approximate)
            avg_cost_per_output_token = cost_usd_cents / output_tokens if output_tokens > 0 else 0
            consumption.rag_cost_saved_usd += int(rag_tokens_saved * avg_cost_per_output_token)
        
        # Update model usage
        models_used = json.loads(consumption.models_used) if consumption.models_used else {}
        models_used[model] = models_used.get(model, 0) + 1
        consumption.models_used = json.dumps(models_used)
        
        providers_used = json.loads(consumption.providers_used) if consumption.providers_used else {}
        providers_used[provider] = providers_used.get(provider, 0) + 1
        consumption.providers_used = json.dumps(providers_used)
        
        # Update velocity (24h window)
        consumption.turns_last_24h += 1
        
        # Update timestamps
        if consumption.first_usage_at is None:
            consumption.first_usage_at = now
        consumption.last_usage_at = now
        consumption.last_updated_at = now
        
        # Create turn record for audit trail
        turn = ConsumptionTurn(
            user_id=user_id,
            session_id=session_id,
            turn_number=turn_number,
            user_query=user_query,
            session_name=session_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            provider=provider,
            model=model,
            cost_usd_cents=cost_usd_cents,
            status=status,
            rag_used=rag_used,
            rag_tokens_saved=rag_tokens_saved,
            created_at=now
        )
        
        self.db.add(turn)
        self.db.commit()
        
        logger.debug(f"Recorded turn for user {user_id}, session {session_id}, turn {turn_number}: "
                    f"{total_tokens} tokens, status={status}, rag={rag_used}")
    
    def update_turn_tokens(
        self,
        user_id: str,
        session_id: str,
        turn_number: int,
        additional_input_tokens: int,
        additional_output_tokens: int,
        additional_cost_usd_cents: int = 0
    ) -> None:
        """
        Update an existing turn record with additional tokens (e.g., from extensions).
        Also updates the UserConsumption aggregate counters so daily/monthly totals stay accurate.

        Args:
            user_id: User ID
            session_id: Session ID
            turn_number: Turn number to update
            additional_input_tokens: Extra input tokens to add
            additional_output_tokens: Extra output tokens to add
            additional_cost_usd_cents: Extra cost in micro-dollars to add
        """
        additional_total = additional_input_tokens + additional_output_tokens

        # Update the turn audit record
        turn = self.db.query(ConsumptionTurn).filter_by(
            user_id=user_id, session_id=session_id, turn_number=turn_number
        ).first()
        if turn:
            turn.input_tokens += additional_input_tokens
            turn.output_tokens += additional_output_tokens
            turn.total_tokens += additional_total
            turn.cost_usd_cents += additional_cost_usd_cents

        self._add_to_aggregates(user_id, additional_input_tokens, additional_output_tokens,
                                additional_cost_usd_cents)
        self.db.commit()
        logger.debug(
            f"Updated turn tokens for user {user_id}, session {session_id}, turn {turn_number}: "
            f"+{additional_total} tokens, +${additional_cost_usd_cents / 1_000_000:.6f}"
        )

    def _add_to_aggregates(self, user_id: str, in_tok: int, out_tok: int, cost_micro: int) -> None:
        """Add tokens and cost to the UserConsumption aggregates (monthly + daily). No commit."""
        consumption = self.get_or_create_consumption(user_id)
        consumption.total_input_tokens += in_tok
        consumption.total_output_tokens += out_tok
        consumption.total_tokens += in_tok + out_tok
        consumption.input_tokens_today += in_tok
        consumption.output_tokens_today += out_tok
        consumption.tokens_today += in_tok + out_tok
        consumption.estimated_cost_usd += cost_micro
        consumption.last_updated_at = datetime.now(timezone.utc)

    def record_usage_line(self, user_id: str, session_id: str, turn_number: int, provider: str,
                          model: str, input_tokens: int, output_tokens: int, cost_usd_cents: int,
                          kind: str = "judge", user_query: Optional[str] = None) -> None:
        """Book one model's usage ON a turn as its OWN line (``status=kind``), under the model that
        was called — so per-provider spend is true. Judge calls were added into the turn's row and
        so billed to the TURN's model (a Google judge read as Nova spend). Updates the token and
        cost aggregates exactly once; does NOT count a prompt (it is not a turn)."""
        if not (input_tokens or output_tokens or cost_usd_cents):
            return
        self.db.add(ConsumptionTurn(
            user_id=user_id, session_id=session_id, turn_number=turn_number,
            user_query=user_query, input_tokens=int(input_tokens), output_tokens=int(output_tokens),
            total_tokens=int(input_tokens) + int(output_tokens), provider=provider or "Unknown",
            model=model or "unknown", cost_usd_cents=int(cost_usd_cents), status=kind,
            rag_used=False, rag_tokens_saved=0))
        self._add_to_aggregates(user_id, int(input_tokens), int(output_tokens), int(cost_usd_cents))
        self.db.commit()

    def update_session_name(self, user_id: str, session_id: str, session_name: str) -> None:
        """
        Update session_name for all turns in a session.
        Call this when a session name is generated/changed.
        
        Args:
            user_id: User ID
            session_id: Session ID
            session_name: New session name
        """
        # Update all turns for this session
        turns = self.db.query(ConsumptionTurn).filter(
            ConsumptionTurn.user_id == user_id,
            ConsumptionTurn.session_id == session_id
        ).all()
        
        for turn in turns:
            turn.session_name = session_name
        
        if turns:
            self.db.commit()
            logger.debug(f"Updated session_name to '{session_name}' for {len(turns)} turns in session {session_id}")
    
    def increment_session_count(self, user_id: str, session_id: str, is_new_session: bool = True) -> None:
        """
        Increment session counters only if this session hasn't been seen before.
        
        Args:
            user_id: User ID
            session_id: Session ID to check
            is_new_session: True for new session, False for existing (legacy parameter, now verified)
        """
        consumption = self.get_or_create_consumption(user_id)
        now = datetime.now(timezone.utc)
        
        if is_new_session:
            # Check if this session already exists in consumption_turns table
            # Only increment if we've never seen this session_id before
            existing_session = self.db.query(ConsumptionTurn).filter(
                ConsumptionTurn.user_id == user_id,
                ConsumptionTurn.session_id == session_id
            ).first()
            
            if not existing_session:
                # Truly a new session - increment counters
                consumption.total_sessions += 1
                consumption.active_sessions += 1
                consumption.sessions_last_24h += 1
                logger.info(f"New session detected for user {user_id}: {session_id}")
            else:
                logger.debug(f"Session {session_id} already exists for user {user_id}, not incrementing")
        
        consumption.last_updated_at = now
        self.db.commit()
    
    def decrement_active_sessions(self, user_id: str) -> None:
        """
        Decrement active session count (when session closes).
        
        Args:
            user_id: User ID
        """
        consumption = self.get_or_create_consumption(user_id)
        
        if consumption.active_sessions > 0:
            consumption.active_sessions -= 1
            consumption.last_updated_at = datetime.now(timezone.utc)
            self.db.commit()
    
    def increment_champion_cases(self, user_id: str, count: int = 1) -> None:
        """
        Increment champion case counter.
        
        Args:
            user_id: User ID
            count: Number of cases created
        """
        consumption = self.get_or_create_consumption(user_id)
        consumption.champion_cases_created += count
        consumption.last_updated_at = datetime.now(timezone.utc)
        self.db.commit()
    
    def update_collection_subscriptions(self, user_id: str, collection_ids: List[int]) -> None:
        """
        Update user's collection subscriptions.
        
        Args:
            user_id: User ID
            collection_ids: List of collection IDs
        """
        consumption = self.get_or_create_consumption(user_id)
        consumption.collections_subscribed = json.dumps(collection_ids)
        consumption.last_updated_at = datetime.now(timezone.utc)
        self.db.commit()
    
    def rollover_period(self, user_id: str) -> None:
        """
        Archive current period and start new period.
        Idempotent — a no-op when the record is already in the current
        period. Note that get_or_create_consumption() self-heals overdue
        rollovers, so calling this explicitly is normally redundant; it is
        kept as the public API for the monthly maintenance job.

        Args:
            user_id: User ID
        """
        # get_or_create_consumption() heals — including the rollover itself —
        # and commits, so by the time it returns the period is current.
        self.get_or_create_consumption(user_id)

    def _rollover(self, consumption: UserConsumption, now: datetime) -> None:
        """
        Archive the record's finished period and reset the monthly
        counters. Pure state change on the given record (plus an archive
        row added to the session) — the caller commits. Callers must have
        already checked that the period is stale.
        """
        current_period = now.strftime("%Y-%m")

        # Archive previous period
        archive = ConsumptionPeriodsArchive(
            user_id=consumption.user_id,
            period=consumption.current_period,
            total_input_tokens=consumption.total_input_tokens,
            total_output_tokens=consumption.total_output_tokens,
            total_tokens=consumption.total_tokens,
            successful_turns=consumption.successful_turns,
            failed_turns=consumption.failed_turns,
            total_turns=consumption.total_turns,
            rag_guided_turns=consumption.rag_guided_turns,
            rag_output_tokens_saved=consumption.rag_output_tokens_saved,
            champion_cases_created=consumption.champion_cases_created,
            estimated_cost_usd=consumption.estimated_cost_usd,
            rag_cost_saved_usd=consumption.rag_cost_saved_usd,
            total_sessions=consumption.total_sessions,
            period_started_at=consumption.period_started_at,
            period_ended_at=now,
            archived_at=now
        )
        
        self.db.add(archive)
        
        # Reset consumption for new period
        consumption.current_period = current_period
        consumption.period_started_at = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        consumption.total_input_tokens = 0
        consumption.total_output_tokens = 0
        consumption.total_tokens = 0
        consumption.successful_turns = 0
        consumption.failed_turns = 0
        consumption.total_turns = 0
        consumption.rag_guided_turns = 0
        consumption.rag_output_tokens_saved = 0
        consumption.champion_cases_created = 0
        consumption.estimated_cost_usd = 0
        consumption.rag_cost_saved_usd = 0
        consumption.total_sessions = 0
        consumption.models_used = None
        consumption.providers_used = None
        consumption.sessions_last_24h = 0
        consumption.turns_last_24h = 0
        consumption.peak_requests_per_hour = 0
        consumption.peak_requests_per_day = 0

        # No commit here — _rollover is a pure state change; the caller
        # (heal_time_windows via get_or_create_consumption) commits.
        logger.info(f"Rolled over consumption for user {consumption.user_id} from {archive.period} to {current_period}")
    
    def get_consumption_summary(self, user_id: str) -> Dict:
        """
        Get comprehensive consumption summary for user.
        
        Args:
            user_id: User ID
            
        Returns:
            Dictionary with all consumption metrics
        """
        consumption = self.get_or_create_consumption(user_id)
        return consumption.to_dict()
    
    def get_users_near_limit(self, threshold_percent: float = 80.0) -> List[Dict]:
        """
        Get users who are near their consumption limits.
        
        Args:
            threshold_percent: Percentage threshold (default 80%)
            
        Returns:
            List of user consumption summaries
        """
        results = []
        
        # Query all users with token limits
        consumptions = self.db.query(UserConsumption).filter(
            (UserConsumption.input_tokens_limit.isnot(None)) |
            (UserConsumption.output_tokens_limit.isnot(None))
        ).all()
        
        for consumption in consumptions:
            near_limit = False
            
            # Check input token threshold
            if consumption.input_tokens_limit:
                usage_percent = (consumption.total_input_tokens / consumption.input_tokens_limit) * 100
                if usage_percent >= threshold_percent:
                    near_limit = True
            
            # Check output token threshold
            if consumption.output_tokens_limit:
                usage_percent = (consumption.total_output_tokens / consumption.output_tokens_limit) * 100
                if usage_percent >= threshold_percent:
                    near_limit = True
            
            if near_limit:
                results.append(consumption.to_dict())
        
        return results
    
    def cleanup_old_turns(self, days_to_keep: int = 90) -> int:
        """
        Clean up old turn records (for storage management).
        
        Args:
            days_to_keep: Number of days to keep (default 90)
            
        Returns:
            Number of records deleted
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
        
        deleted = self.db.query(ConsumptionTurn).filter(
            ConsumptionTurn.created_at < cutoff
        ).delete()
        
        self.db.commit()
        
        logger.info(f"Cleaned up {deleted} turn records older than {days_to_keep} days")
        return deleted
    
    def reconcile_session_count(self, user_id: str) -> Dict[str, int]:
        """
        Reconcile session count with actual distinct sessions in consumption_turns.
        This fixes any discrepancies caused by duplicate increments.
        
        Args:
            user_id: User ID to reconcile
            
        Returns:
            Dictionary with old_count, new_count, and difference
        """
        from sqlalchemy import func
        
        consumption = self.get_or_create_consumption(user_id)
        old_count = consumption.total_sessions
        
        # Count actual distinct sessions in consumption_turns
        actual_count = self.db.query(func.count(func.distinct(ConsumptionTurn.session_id))).filter(
            ConsumptionTurn.user_id == user_id
        ).scalar() or 0
        
        if old_count != actual_count:
            logger.info(f"Reconciling session count for user {user_id}: {old_count} -> {actual_count}")
            consumption.total_sessions = actual_count
            self.db.commit()
        
        return {
            'old_count': old_count,
            'new_count': actual_count,
            'difference': actual_count - old_count
        }
