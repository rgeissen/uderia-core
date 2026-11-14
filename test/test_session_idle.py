"""
Session idle expiry gate (CI-pure; uses a throwaway SQLite database).

  - TDA_SESSION_IDLE_MINUTES sets the limit; hardened mode defaults it to 30;
    default mode leaves it off (historical behaviour).
  - A session token unused for longer than the limit is refused and revoked;
    use within the limit refreshes last_used_at.
  - In hardened mode a session token with no server record is refused;
    internal service tokens are exempt.
  - The browser lock reads the same limit (template injection + idleLock.js).

Run: PYTHONPATH=src python test/test_session_idle.py
"""
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import jwt as pyjwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trusted_data_agent.auth import security
from trusted_data_agent.auth.models import AuthToken, User

PASS = FAIL = 0
SAVED = {k: os.environ.get(k) for k in ("TDA_SESSION_IDLE_MINUTES", "TDA_SECURITY_PROFILE", "TDA_ENV")}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name} {detail}")


def env(**kw):
    for k in SAVED:
        os.environ.pop(k, None)
    os.environ.update(kw)


db = os.path.join(tempfile.mkdtemp(), "auth.db")
engine = create_engine(f"sqlite:///{db}")
# Only the tables this path touches (the full metadata has a duplicate index name).
User.__table__.create(engine)
AuthToken.__table__.create(engine)
Session = sessionmaker(bind=engine)


@contextmanager
def temp_session():
    s = Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


security.get_db_session = temp_session


def set_last_used(token, minutes_ago):
    import hashlib
    h = hashlib.sha256(token.encode()).hexdigest()
    with temp_session() as s:
        t = s.query(AuthToken).filter_by(token_hash=h).first()
        t.last_used_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)


def revoked(token):
    import hashlib
    h = hashlib.sha256(token.encode()).hexdigest()
    with temp_session() as s:
        return s.query(AuthToken).filter_by(token_hash=h).first().revoked


# 1. Limit resolution
env()
check("default mode: off", security.session_idle_minutes() == 0)
env(TDA_SECURITY_PROFILE="hardened")
check("hardened mode: 30", security.session_idle_minutes() == 30)
env(TDA_SECURITY_PROFILE="hardened", TDA_SESSION_IDLE_MINUTES="15")
check("explicit value wins", security.session_idle_minutes() == 15)
env(TDA_SESSION_IDLE_MINUTES="nonsense")
check("invalid value ignored", security.session_idle_minutes() == 0)

# 2. Idle expiry
env(TDA_SESSION_IDLE_MINUTES="30")
tok, _ = security.generate_auth_token("u1", "alice")
check("fresh token valid", security.verify_auth_token(tok) is not None)
set_last_used(tok, 10)
check("used 10 min ago: valid", security.verify_auth_token(tok) is not None)
set_last_used(tok, 31)
check("idle 31 min: refused", security.verify_auth_token(tok) is None)
check("idle token revoked", revoked(tok))
check("stays refused", security.verify_auth_token(tok) is None)

tok2, _ = security.generate_auth_token("u1", "alice")
set_last_used(tok2, 25)
security.verify_auth_token(tok2)


def last_used(token):
    import hashlib
    h = hashlib.sha256(token.encode()).hexdigest()
    with temp_session() as s:
        v = s.query(AuthToken).filter_by(token_hash=h).first().last_used_at
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v


check("use refreshes the clock", datetime.now(timezone.utc) - last_used(tok2) < timedelta(seconds=30))

env()
tok3, _ = security.generate_auth_token("u1", "alice")
set_last_used(tok3, 600)
check("default mode never idle-expires", security.verify_auth_token(tok3) is not None)

# 3. Unrecorded tokens
now = datetime.now(timezone.utc)
ghost = pyjwt.encode({"user_id": "u1", "username": "alice", "exp": now + timedelta(hours=1),
                      "iat": now, "jti": "ghost"}, security.JWT_SECRET_KEY, algorithm=security.JWT_ALGORITHM)
env()
check("default mode: unrecorded token accepted (historical)", security.verify_auth_token(ghost) is not None)
env(TDA_SECURITY_PROFILE="hardened")
check("hardened: unrecorded token refused", security.verify_auth_token(ghost) is None)
internal = pyjwt.encode({"user_id": "u1", "username": "internal", "exp": now + timedelta(minutes=5),
                         "iat": now, "jti": "int", "internal": True},
                        security.JWT_SECRET_KEY, algorithm=security.JWT_ALGORITHM)
check("hardened: internal service token still accepted", security.verify_auth_token(internal) is not None)

# 4. Browser lock wiring
root = os.path.join(os.path.dirname(__file__), "..")
idx = open(os.path.join(root, "templates", "index.html")).read()
check("template injects the limit", "UDERIA_SESSION_IDLE_MINUTES = {{ session_idle_minutes" in idx)
check("template loads idleLock.js", "/static/js/idleLock.js" in idx)
main_src = open(os.path.join(root, "src", "trusted_data_agent", "main.py")).read()
check("context processor supplies the limit", "session_idle_minutes()" in main_src)
js = open(os.path.join(root, "static", "js", "idleLock.js")).read()
check("lock signs out through the auth client", "authClient.logout()" in js)
check("lock is off at 0", "minutes <= 0) return" in js)

# ── a dead session ends the tab, once, from one place (prod 2026-10-20) ─────────────
# The server revoked the demo's token for 30 min idle while the tab stayed open; every
# widget then painted its own empty state on each 401 ("No scored runs yet.") and the
# server logged "attempt to use revoked token" per click. Now: ONE marker on the server,
# ONE guard in the browser, the login page says why, and the browser keeps the server's
# idle clock in step while the user is active.
from trusted_data_agent.auth import middleware as mw
mk = mw.session_invalid_marker
check("marker: a 401 on an API path with a session Bearer token", mk("Bearer eyJhbGciOi.x.y", "/api/v1/evals/agents/A/runs", 401) is True)
check("marker: never on a tda_ access token (a script's credential, not a session)", mk("Bearer tda_abcdef", "/api/v1/x", 401) is False)
check("marker: never without a Bearer header", mk("", "/api/v1/x", 401) is False and mk("Basic abc", "/api/v1/x", 401) is False)
check("marker: never on a non-401", mk("Bearer eyJ.x.y", "/api/v1/x", 403) is False and mk("Bearer eyJ.x.y", "/api/v1/x", 200) is False)
check("marker: never off the API (a page 401 is the login page's business)", mk("Bearer eyJ.x.y", "/login", 401) is False)
check("marker: header name is the one the guard reads", mw.SESSION_STATE_HEADER == "X-Session-State")
main_src = open(os.path.join(root, "src", "trusted_data_agent", "main.py"), encoding="utf-8").read()
check("the app stamps the marker at ONE after-request seam", "session_invalid_marker(request.headers.get('Authorization', ''), request.path, response.status_code)" in main_src
      and "response.headers[SESSION_STATE_HEADER] = 'invalid'" in main_src)
auth_js = open(os.path.join(root, "static", "js", "auth.js"), encoding="utf-8").read()
check("guard: installed once, wrapping fetch", "window.__uderiaSessionGuard" in auth_js and "window.fetch = async function" in auth_js)
check("guard: reacts to the MARKER, never to a bare 401", "res.headers.get('X-Session-State') === 'invalid'" in auth_js)
check("guard: only while a session token is stored", "&& localStorage.getItem('tda_auth_token')" in auth_js)
check("guard: ends the session once and says why", "'/login?reason=session'" in auth_js and "if (ended) return;" in auth_js)
check("guard: never redirects from the login/register pages", "window.location.pathname !== '/login' && window.location.pathname !== '/register'" in auth_js)
login_html = open(os.path.join(root, "templates", "login.html"), encoding="utf-8").read()
check("login page explains a server-ended session", "urlParams.get('reason') === 'session'" in login_html and "ended on the server" in login_html)
check("idle lock: heartbeat keeps the server's clock in step while active", "function heartbeat()" in js and "/api/v1/auth/me" in js and "idle < HEARTBEAT_MS" in js)
check("idle lock: heartbeat is throttled (never per event)", "Date.now() - lastBeat >= HEARTBEAT_MS" in js)
roster = open(os.path.join(root, "static", "js", "handlers", "agentPerformanceHandler.js"), encoding="utf-8").read()
_i = roster.index("async function loadRunsLedger("); _catch = roster[roster.index("} catch (e) {", _i):roster.index("} catch (e) {", _i) + 600]
check("roster: a failed runs load reads as a failure, never as 'No scored runs yet.'", '">No scored runs yet.' not in _catch and "Runs could not be loaded" in _catch)
check("background requests do not count (no request or response ever marks activity)",
      "XMLHttpRequest" not in js and ".then(markActive" not in js and "markActive()" not in js[js.index("function heartbeat()"):js.index("writeShared(last);\n    setInterval")]
      and all(ev in js for ev in ("keydown", "pointerdown", "wheel", "touchstart", "scroll", "pointermove")))
login = open(os.path.join(root, "templates", "login.html")).read()
check("login explains an idle sign-out", "reason') === 'idle'" in login)

for k, v in SAVED.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
