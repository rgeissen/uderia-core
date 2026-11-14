"""The campaigns' hard cost cap — read from the ledger, stops a run before the budget is gone.

FOUND (2026-10-29): two days of live campaigns cost ≈ $16 on the operator's OpenRouter account
(Nova 2 Lite alone $14.30 — 46 M input tokens), more than a whole top-up, and the account ran dry
mid-run (FINDINGS C18). Nothing in either runner knew what a run had cost. `campaign_budget.Budget`
reads `consumption_turns` (micro-dollars) for the campaign's user since the run started; both
runners check it before starting a case and refuse to accept a capped (partial) run as the
baseline. CI-pure: a throwaway ledger.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


from campaign_budget import Budget, _ledger_ts  # noqa: E402

db = os.path.join(tempfile.mkdtemp(), "ledger.db")
c = sqlite3.connect(db)
c.execute("CREATE TABLE users (id TEXT, username TEXT)")
c.execute("CREATE TABLE consumption_turns (user_id TEXT, cost_usd_cents INTEGER, status TEXT, created_at TEXT)")
c.executemany("INSERT INTO users VALUES (?, ?)", [("u-admin", "admin"), ("u-bob", "bob")])
start = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
before, during = _ledger_ts(start - timedelta(hours=1)), _ledger_ts(start + timedelta(minutes=5))
c.executemany("INSERT INTO consumption_turns VALUES (?, ?, ?, ?)", [
    ("u-admin", 5_000_000, "success", before),     # $5 BEFORE the run — not counted
    ("u-admin", 300_000, "success", during),       # $0.30 a turn during the run
    ("u-admin", 50_000, "judge", during),          # $0.05 a judge line — counted
    ("u-bob", 9_000_000, "success", during),       # another user — not counted
])
c.commit()
c.close()

print("\n── §1 what is counted")
b = Budget(1.00, "admin", db_path=db, started=start)
check("only this user's lines since the run started, judges included", abs(b.spent() - 0.35) < 1e-9, b.spent())
check("micro-dollars are read as USD", abs(Budget(0, "admin", db_path=db, started=start - timedelta(hours=2)).spent() - 5.35) < 1e-9)
check("under the cap → not exceeded", not b.exceeded() and not b.hit)

print("\n── §2 the cap")
c = sqlite3.connect(db)
c.execute("INSERT INTO consumption_turns VALUES ('u-admin', 700000, 'success', ?)", (during,))
c.commit()
c.close()
check("at the cap → exceeded", b.exceeded() and b.hit)
check("the report line says the cap ended the run", "CAP REACHED" in b.line() and "$1.00" in b.line())
c = sqlite3.connect(db)
c.execute("DELETE FROM consumption_turns WHERE cost_usd_cents = 700000")
c.commit()
c.close()
check("sticky: once stopped, a run never resumes", b.exceeded())
check("0 (or None) means no cap", not Budget(0, "admin", db_path=db, started=start).exceeded()
      and not Budget(None, "admin", db_path=db, started=start).exceeded())
check("an unreadable ledger reads 0 and never ends a run by itself",
      Budget(1.0, "admin", db_path="/nonexistent/ledger.db", started=start).spent() == 0.0)

print("\n── §3 both runners honour it")
for name in ("trust_campaign", "optimize_campaign"):
    src = open(os.path.join(HERE, name, "run_campaign.py")).read()
    check(f"{name}: a --max-usd cap with a default", '"--max-usd", type=float, default=DEFAULT_MAX_USD' in src)
    i_cap = src.find("BUDGET.exceeded()")
    i_run = src.find("rec = run_case(", i_cap)
    check(f"{name}: checked BEFORE a case starts", 0 < i_cap < i_run)
    check(f"{name}: a capped run is never the accepted baseline",
          'if args.baseline == "write" and BUDGET.hit:' in src and "REFUSED: the cost cap ended this run early" in src)
    check(f"{name}: the report carries the cost line", 'fh.write(f"\\n## Cost\\n\\n{BUDGET.line()}\\n")' in src)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
