"""A hard cost cap for the live campaigns (trust and Optimize) — read from the platform's own ledger.

Why: the campaigns spend real money on the operator's provider account. On 2026-10-28/29 two days
of campaigns cost ≈ $16 (Nova 2 Lite alone $14.30: 46 M input tokens at ~43 K per call), more than
a whole top-up, because nothing in the runners knew what a run had cost. A full trust campaign is
≈ $2, a targeted run cents — the cap makes "stop before the budget is gone" structural.

What it counts: every line the platform booked in `consumption_turns` for the campaign's user
since the run started — the turns, the coordinator's expert sessions (their own session ids), and
the judges' own lines (`status: judge`). Conservative on purpose: anything else on that account
during the run counts too, so the run stops EARLIER, never later. `cost_usd_cents` holds
micro-dollars (models.py: "USD × 1,000,000") despite its name.

How a runner uses it: `budget = Budget(max_usd, user)`; before starting a case,
`budget.exceeded()` → skip the rest (the case is recorded as skipped, the report says the cap
ended the run); `budget.spent()` for the report line. Read-only: it never writes the ledger.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB = os.path.join(ROOT, "tda_auth.db")
DEFAULT_MAX_USD = 1.00


def _ledger_ts(dt: datetime) -> str:
    """The ledger's own timestamp form (UTC, space-separated) so a string comparison is exact."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Budget:
    def __init__(self, max_usd: float, username: str, db_path: str = DB, started: datetime = None):
        self.max_usd = float(max_usd) if max_usd is not None else None
        self.username = username
        self.db_path = db_path
        self.started = _ledger_ts(started or datetime.now(timezone.utc))
        self.hit = False
        self._user_id = None

    def _uid(self, conn):
        if self._user_id is None:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (self.username,)).fetchone()
            self._user_id = row[0] if row else ""
        return self._user_id

    def spent(self) -> float:
        """USD booked on the campaign's account since the run started. 0.0 if unreadable."""
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd_cents), 0) FROM consumption_turns "
                    "WHERE user_id = ? AND created_at >= ?", (self._uid(conn), self.started)).fetchone()
                return (row[0] or 0) / 1_000_000.0
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — an unreadable ledger never ends a run by itself
            return 0.0

    def exceeded(self) -> bool:
        """True once the cap is reached (sticky: a run never resumes after it stopped)."""
        if self.max_usd is None or self.max_usd <= 0:
            return False
        if not self.hit and self.spent() >= self.max_usd:
            self.hit = True
        return self.hit

    def line(self) -> str:
        cap = f"${self.max_usd:.2f}" if self.max_usd else "no cap"
        return (f"spend ${self.spent():.4f} of {cap} (campaign account, since "
                f"{self.started} UTC){' — CAP REACHED, remaining cases skipped' if self.hit else ''}")
