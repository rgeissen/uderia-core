"""
Rigid module test for the Tier-3 component code-bug fixes (P2 follow-ups).

Run: PYTHONPATH=src python3 test/test_tier3_fixes.py

Covers:
  1. scheduler cron preview DOW convention — the manual fallback evaluator must
     interpret day-of-week as cron (0=Sunday..6=Saturday, 7=Sunday), NOT Python's
     weekday() (0=Monday). Regression: `* * * * 1-5` must be Mon-Fri (not Tue-Sat).
  3. memory mem_key skill mismatch — memory-expert.md must no longer advise setting
     a `mem_key`, an arg the TDA_Memory tool does not expose.

(Fix #2, memory `forget` scoping/precision, lives in test/test_memory.py:t_forget
 where the real sqlite store harness already exists.)
"""
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)  # repo root (for `components`)

from components.builtin.scheduler.handler import _next_runs_cron_manual  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


def _weekdays(expr, n=8):
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)  # Thu
    return [datetime.fromisoformat(s).weekday() for s in _next_runs_cron_manual(expr, base, n)]


def t_scheduler_dow():
    # py weekday(): Mon=0 .. Sun=6
    wd = _weekdays("0 9 * * 1-5")
    check("cron 1-5 -> Mon-Fri only (py weekday 0..4)", wd and all(0 <= d <= 4 for d in wd))
    check("cron 1-5 reaches Monday (was excluded by the bug)", 0 in wd)
    check("cron 1-5 excludes Saturday (was wrongly included by the bug)", 5 not in wd)
    check("cron 1-5 excludes Sunday", 6 not in wd)

    sun0 = _weekdays("0 9 * * 0", 3)
    check("cron 0 -> Sunday (py weekday 6)", sun0 and all(d == 6 for d in sun0))
    sun7 = _weekdays("0 9 * * 7", 3)
    check("cron 7 -> Sunday too (py weekday 6)", sun7 and all(d == 6 for d in sun7))
    sat = _weekdays("0 9 * * 6", 3)
    check("cron 6 -> Saturday (py weekday 5)", sat and all(d == 5 for d in sat))
    mon = _weekdays("0 9 * * 1", 3)
    check("cron 1 -> Monday (py weekday 0)", mon and all(d == 0 for d in mon))
    star = _weekdays("0 9 * * *", 4)
    check("cron * -> every day", len(star) == 4)


def t_memkey_skill():
    p = os.path.join(ROOT, "skills", "builtin", "memory-expert", "memory-expert.md")
    txt = open(p, encoding="utf-8").read()
    check("memory-expert no longer advises setting mem_key", "mem_key" not in txt)
    check("memory-expert still present / non-trivial", len(txt) > 1000)


def main():
    for t in (t_scheduler_dow, t_memkey_skill):
        print(f"\n{t.__name__}:")
        t()
    print("-" * 60)
    print(f"RESULT: {'PASS' if _failed == 0 else 'FAIL'}  ({_passed} passed, {_failed} failed)")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
