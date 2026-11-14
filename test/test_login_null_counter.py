"""A NULL failed-login counter never breaks a login. CI-pure.

FOUND (2026-10-29, production): the admin row held failed_login_attempts = NULL (written
outside the ORM; the model declares NOT NULL DEFAULT 0), so every password login as admin
answered 500 on `None <= 1` in check_progressive_delay, and a failed attempt would have
crashed on `None += 1`. Every read now goes through security.failed_attempts().
"""
import os, pathlib, sys, types, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
logging.disable(logging.CRITICAL)
from trusted_data_agent.auth import security as S

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✓ {name}")
    else:
        FAIL += 1; print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))

u = types.SimpleNamespace(failed_login_attempts=None, last_failed_login_at=None, locked_until=None, username="admin", id="u1")
check("failed_attempts: NULL reads 0, a count reads itself, junk reads 0",
      S.failed_attempts(u) == 0 and S.failed_attempts(types.SimpleNamespace(failed_login_attempts=3)) == 3 and S.failed_attempts(types.SimpleNamespace(failed_login_attempts="x")) == 0
      and S.failed_attempts(types.SimpleNamespace()) == 0)
check("check_progressive_delay with a NULL counter: no wait, no crash", S.check_progressive_delay(u) == (False, 0))
st = S.get_login_status(u)
check("get_login_status with a NULL counter: 0 failed, every attempt remaining", st["failed_attempts"] == 0 and st["remaining_attempts"] == S.MAX_LOGIN_ATTEMPTS, st)
src = (pathlib.Path(__file__).resolve().parents[1] / "src/trusted_data_agent/auth/security.py").read_text()
check("no raw arithmetic or comparison on the column remains outside the accessor",
      "failed_login_attempts += 1" not in src and "user.failed_login_attempts <=" not in src and "- user.failed_login_attempts" not in src
      and "failed_attempts(db_user) + 1" in src)
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
