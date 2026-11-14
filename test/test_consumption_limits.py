"""Consumption limits: one resolver, enforced on every path.

Defects under test (recorded in the Nov 2026 documentation sweep, fixed here):

  1. The per-turn check (ConsumptionManager.check_rate_limits / check_token_quota, which
     every turn runs, REST included) compared against limits COPIED onto the consumption
     row when it was created and never refreshed. A reassigned or edited profile, the
     Global Override and the admin exemption never reached it; only the UI routes'
     ConsumptionEnforcer read the live profile. Both now ask auth/consumption_limits.py.
  2. "Configuration changes per hour" was stored and shown but enforced nowhere, and the
     enforcer's recorder wrote an AuditLog row without its NOT NULL status, so even a
     caller would have counted nothing. The write routes now carry limit_config_changes.
  3. "API calls per minute per IP" was stored and shown but enforced nowhere. A
     before_request hook now applies it to /api/ calls without a valid credential.

CI-pure: in-memory SQLite through the real models, a throwaway Quart app for the request
paths. No server, no network.

Run:  PYTHONPATH=src python3 test/test_consumption_limits.py
"""
import asyncio
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import trusted_data_agent.auth.database as auth_db
from trusted_data_agent.auth.models import (
    AuditLog, Base, ConsumptionProfile, ConsumptionTurn, ConsumptionPeriodsArchive,
    SystemSettings, User, UserConsumption,
)

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


def _fresh_db():
    """Point every get_db_session() user at a fresh in-memory database; return a session."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        User.__table__, ConsumptionProfile.__table__, UserConsumption.__table__,
        ConsumptionTurn.__table__, ConsumptionPeriodsArchive.__table__,
        SystemSettings.__table__, AuditLog.__table__,
    ])
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    auth_db.SessionLocal = maker
    return maker()


def _profile(db, name, *, hour=50, day=500, configs=5, tin=None, tout=None, default=False, active=True):
    p = ConsumptionProfile(name=name, prompts_per_hour=hour, prompts_per_day=day,
                           config_changes_per_hour=configs, input_tokens_per_month=tin,
                           output_tokens_per_month=tout, is_default=default, is_active=active)
    db.add(p)
    db.commit()
    return p


def _user(db, uid="u-1", *, profile=None, admin=False):
    u = User(id=uid, username=f"user-{uid}", email=f"{uid}@x.test", password_hash="x",
             is_admin=admin, consumption_profile_id=profile.id if profile else None)
    db.add(u)
    db.commit()
    return u


def _setting(db, key, value):
    db.add(SystemSettings(setting_key=key, setting_value=value))
    db.commit()


# ── 1. the resolver ──────────────────────────────────────────────────────────────────
def t_resolver():
    from trusted_data_agent.auth.consumption_limits import effective_limits_for
    db = _fresh_db()
    free = _profile(db, "Free", hour=5, day=50, configs=3, tin=1000, tout=500)
    _profile(db, "Unlimited", hour=1000, day=10000, configs=100, default=True)
    _user(db, "assigned", profile=free)
    _user(db, "plain")
    _user(db, "boss", profile=free, admin=True)

    lim = effective_limits_for(db, "assigned")
    check("resolver: an assigned profile applies", (lim.source, lim.prompts_per_hour, lim.input_tokens_per_month)
          == ("profile", 5, 1000), lim)
    lim = effective_limits_for(db, "plain")
    check("resolver: no assignment falls back to the default profile", lim.profile_name == "Unlimited", lim)
    lim = effective_limits_for(db, "boss")
    check("resolver: an admin is exempt", lim.exempt and lim.is_unlimited(), lim)

    _setting(db, "rate_limit_global_override", "true")
    _setting(db, "rate_limit_user_prompts_per_hour", "7")
    lim = effective_limits_for(db, "assigned")
    check("resolver: the Global Override beats the profile, with no token limits",
          (lim.source, lim.prompts_per_hour, lim.prompts_per_day, lim.config_changes_per_hour,
           lim.input_tokens_per_month) == ("global_override", 7, 1000, 10, None), lim)
    check("resolver: the Global Override never binds an admin", effective_limits_for(db, "boss").exempt)

    db2 = _fresh_db()
    _user(db2, "lonely")
    lim = effective_limits_for(db2, "lonely")
    check("resolver: no profile at all is unlimited", lim.source == "unlimited" and lim.is_unlimited(), lim)


# ── 2. the per-turn check reads the live limits ─────────────────────────────────────
def t_per_turn_follows_reassignment():
    from trusted_data_agent.auth.consumption_manager import ConsumptionManager
    db = _fresh_db()
    free = _profile(db, "Free", hour=5, day=50)
    pro = _profile(db, "Pro", hour=200, day=2000)
    user = _user(db, profile=free)
    mgr = ConsumptionManager(db)
    c = mgr.get_or_create_consumption(user.id)
    check("created: the row caches the profile's limit", c.prompts_per_hour_limit == 5)
    c.requests_this_hour = 10
    db.commit()
    ok, why = mgr.check_rate_limits(user.id)
    check("on Free, 10 requests this hour are over the limit", not ok and "5 requests/hour" in (why or ""), why)

    user.consumption_profile_id = pro.id          # the admin reassigns the user
    db.commit()
    ok, why = mgr.check_rate_limits(user.id)
    check("after reassignment to Pro the same turn is allowed (was refused before the fix)", ok, why)
    check("the cached column follows the live limit", db.query(UserConsumption).filter_by(user_id=user.id).first().prompts_per_hour_limit == 200)

    pro.prompts_per_hour = 8                      # the admin edits the profile
    db.commit()
    ok, why = mgr.check_rate_limits(user.id)
    check("an edited profile applies on the next turn", not ok and "8 requests/hour" in (why or ""), why)


def t_per_turn_override_admin_inactive():
    from trusted_data_agent.auth.consumption_manager import ConsumptionManager
    db = _fresh_db()
    free = _profile(db, "Free", hour=5, day=50, tin=1000)
    user = _user(db, profile=free)
    admin = _user(db, "boss", profile=free, admin=True)
    mgr = ConsumptionManager(db)
    for uid in (user.id, admin.id):
        c = mgr.get_or_create_consumption(uid)
        c.requests_this_hour = 30
        c.total_input_tokens = 5000
    db.commit()

    ok, _ = mgr.check_rate_limits(admin.id)
    ok2, _ = mgr.check_token_quota(admin.id)
    check("an admin is never blocked on a turn (rate)", ok)
    check("an admin is never blocked on a turn (tokens)", ok2)

    _setting(db, "rate_limit_global_override", "true")
    _setting(db, "rate_limit_user_prompts_per_hour", "100")
    ok, why = mgr.check_rate_limits(user.id)
    check("the Global Override reaches the per-turn check", ok, why)
    ok, why = mgr.check_token_quota(user.id)
    check("the Global Override carries no token quota", ok, why)

    db.query(SystemSettings).filter_by(setting_key="rate_limit_global_override").first().setting_value = "false"
    db.commit()
    ok, why = mgr.check_token_quota(user.id)
    check("back on the profile, the token quota applies", not ok and "1000 tokens/month" in (why or ""), why)

    free.input_tokens_per_month = None            # the admin lifts the quota
    db.commit()
    ok, why = mgr.check_token_quota(user.id)
    check("a lifted quota applies on the next turn", ok, why)
    check("the cached token limit follows (NULL = unlimited)",
          db.query(UserConsumption).filter_by(user_id=user.id).first().input_tokens_limit is None)

    free.is_active = False
    free.prompts_per_hour = 1000
    db.commit()
    ok, why = mgr.check_rate_limits(user.id)
    check("an inactive profile blocks the turn", not ok and "inactive" in (why or ""), why)


def t_both_paths_agree():
    from trusted_data_agent.auth.consumption_enforcer import ConsumptionEnforcer
    from trusted_data_agent.auth.consumption_manager import ConsumptionManager
    db = _fresh_db()
    free = _profile(db, "Free", hour=5, day=50, configs=3, tin=1000, tout=500)
    _user(db, profile=free)
    _user(db, "boss", admin=True)
    enforcer = ConsumptionEnforcer("u-1")
    live = ConsumptionManager(db).current_limits("u-1")
    check("the UI path and the per-turn path resolve the same limits", enforcer.limits == live,
          (enforcer.limits, live))
    boss = ConsumptionEnforcer("boss")
    check("the UI path exempts an admin through the same resolver", boss.exempt and boss.can_execute_prompt()[0])


# ── 3. configuration changes per hour ───────────────────────────────────────────────
def t_config_change_counting():
    from trusted_data_agent.auth.consumption_enforcer import ConsumptionEnforcer
    db = _fresh_db()
    free = _profile(db, "Free", configs=2)
    _user(db, profile=free)
    enforcer = ConsumptionEnforcer("u-1")
    check("no change yet: allowed", enforcer.can_change_configuration()[0])
    enforcer.record_configuration_change("config_change", "PUT profile", resource="/api/v1/profiles/p1")
    rows = db.query(AuditLog).filter_by(user_id="u-1").all()
    check("a recorded change lands in the audit log (the old insert omitted its status)",
          len(rows) == 1 and rows[0].status == "success" and rows[0].resource == "/api/v1/profiles/p1", rows)
    enforcer.record_configuration_change("config_change", "PUT profile")
    ok, why = enforcer.can_change_configuration()
    check("the third change within the hour is refused", not ok and "2 changes/hour" in (why or ""), why)


def _app():
    """A throwaway Quart app with one decorated write route and the API-limit hook."""
    from quart import Quart, jsonify
    from trusted_data_agent.auth.config_change_limit import limit_config_changes
    from trusted_data_agent.auth.rate_limiter import enforce_ip_api_limit
    app = Quart(__name__)
    app.before_request(enforce_ip_api_limit)

    @app.route("/api/v1/profiles/<pid>", methods=["PUT"])
    @limit_config_changes("profile")
    async def update(pid):
        return jsonify({"status": "success", "pid": pid}), 200

    @app.route("/api/v1/profiles/<pid>", methods=["DELETE"])
    @limit_config_changes("profile")
    async def delete(pid):
        return jsonify({"status": "error"}), 404

    @app.route("/api/v1/public")
    async def public():
        return jsonify({"ok": True})

    @app.route("/api/notifications/subscribe")
    async def stream():
        return jsonify({"ok": True})

    @app.route("/page")
    async def page():
        return "ok"
    return app


def t_config_change_decorator():
    import trusted_data_agent.auth.middleware as mw
    import trusted_data_agent.auth.rate_limiter as rl
    db = _fresh_db()
    free = _profile(db, "Free", configs=2)
    user = _user(db, profile=free)
    admin = _user(db, "boss", profile=free, admin=True)
    rl._is_rate_limit_enabled = lambda: False          # this test is about config changes only
    current = {"user": user}
    mw.get_current_user = lambda: current["user"]

    async def run():
        client = _app().test_client()
        codes = [(await client.put("/api/v1/profiles/p1")).status_code for _ in range(3)]
        check("two changes pass, the third is refused with 429", codes == [200, 200, 429], codes)
        r = await client.put("/api/v1/profiles/p1")
        body = await r.get_json()
        check("the refusal says rate_limit_exceeded", body.get("type") == "rate_limit_exceeded", body)
        check("only successful changes were recorded",
              db.query(AuditLog).filter_by(user_id=user.id, action="config_change").count() == 2)
        current["user"] = admin
        codes = [(await client.put("/api/v1/profiles/p1")).status_code for _ in range(4)]
        check("an admin is never limited", codes == [200] * 4, codes)
        check("an admin's changes are not counted",
              db.query(AuditLog).filter_by(user_id=admin.id).count() == 0)

        db2 = _fresh_db()
        _user(db2, "fresh", profile=_profile(db2, "Free", configs=2))
        current["user"] = db2.query(User).filter_by(id="fresh").first()
        await client.delete("/api/v1/profiles/p1")
        check("a failed change (404) is not counted",
              db2.query(AuditLog).filter_by(user_id="fresh").count() == 0)
    asyncio.run(run())


# ── 4. API calls per minute per IP ──────────────────────────────────────────────────
def t_ip_api_limit():
    import trusted_data_agent.auth.middleware as mw
    import trusted_data_agent.auth.rate_limiter as rl
    from trusted_data_agent.auth.rate_limiter import ip_api_limit_applies
    _fresh_db()
    check("policy: /api/ is counted", ip_api_limit_applies("/api/v1/sessions"))
    check("policy: the notification stream is not", not ip_api_limit_applies("/api/notifications/subscribe"))
    check("policy: a component renderer (an ES-module import, no credential) is an asset, not an API call",
          not ip_api_limit_applies("/api/v1/components/flow/renderer") and ip_api_limit_applies("/api/v1/components/flow/other")
          and ip_api_limit_applies("/api/v1/components"))
    check("policy: pages are not", not ip_api_limit_applies("/page"))
    check("policy: a CORS preflight is not", not ip_api_limit_applies("/api/v1/x", "OPTIONS"))

    rl._rate_limits.clear()
    rl._is_rate_limit_enabled = lambda: True
    rl._get_rate_limit_config = lambda: {"enabled": True, "ip_api_per_minute": 3}
    signed_in = {"user": None}
    mw.get_current_user = lambda: signed_in["user"]

    async def run():
        client = _app().test_client()
        h = {"X-Forwarded-For": "203.0.113.9"}
        codes = [(await client.get("/api/v1/public", headers=h)).status_code for _ in range(4)]
        check("an anonymous caller gets 3 calls a minute, then 429", codes == [200, 200, 200, 429], codes)
        r = await client.get("/api/v1/public", headers=h)
        check("the refusal carries Retry-After", r.headers.get("Retry-After", "").isdigit(), dict(r.headers))
        bad = {**h, "Authorization": "Bearer not-a-token"}
        check("a garbage Bearer token does not buy a way past the limit",
              (await client.get("/api/v1/public", headers=bad)).status_code == 429)
        check("a garbage token= parameter does not either",
              (await client.get("/api/v1/public?token=junk", headers=h)).status_code == 429)
        other = {"X-Forwarded-For": "198.51.100.4"}
        check("another address has its own allowance",
              (await client.get("/api/v1/public", headers=other)).status_code == 200)
        check("the notification stream is never counted",
              (await client.get("/api/notifications/subscribe", headers=h)).status_code == 200)
        check("pages are never counted", (await client.get("/page", headers=h)).status_code == 200)
        signed_in["user"] = object()
        ok = {**h, "Authorization": "Bearer a-valid-session"}
        codes = [(await client.get("/api/v1/public", headers=ok)).status_code for _ in range(5)]
        check("signed-in traffic from the same address is never counted", codes == [200] * 5, codes)
        rl._is_rate_limit_enabled = lambda: False
        signed_in["user"] = None
        check("the master switch off lifts the limit",
              (await client.get("/api/v1/public", headers=h)).status_code == 200)
    asyncio.run(run())


def t_hook_is_installed():
    main = (_ROOT / "src/trusted_data_agent/main.py").read_text(encoding="utf-8")
    check("main.py installs the API-limit hook", "@app.before_request" in main and "enforce_ip_api_limit" in main)
    routes = (_ROOT / "src/trusted_data_agent/api/rest_routes.py").read_text(encoding="utf-8")
    for fn in ("create_mcp_server", "update_mcp_server", "delete_mcp_server", "import_mcp_server_from_registry",
               "create_llm_configuration", "update_llm_configuration", "delete_llm_configuration",
               "create_profile", "update_profile", "delete_profile"):
        check(f"{fn} carries limit_config_changes",
              f'@limit_config_changes(' in routes.split(f"\nasync def {fn}(")[0].rsplit("@rest_api_bp.route", 1)[1])


TESTS = [t_resolver, t_per_turn_follows_reassignment, t_per_turn_override_admin_inactive, t_both_paths_agree,
         t_config_change_counting, t_config_change_decorator, t_ip_api_limit, t_hook_is_installed]

if __name__ == "__main__":
    import trusted_data_agent.auth.middleware as _mw
    import trusted_data_agent.auth.rate_limiter as _rl
    saved = (_mw.get_current_user, _rl._is_rate_limit_enabled, _rl._get_rate_limit_config)
    for t in TESTS:
        print(f"\n{t.__name__}")
        try:
            t()
        except Exception:
            FAIL += 1
            traceback.print_exc()
        finally:
            _mw.get_current_user, _rl._is_rate_limit_enabled, _rl._get_rate_limit_config = saved
    print(f"\ntest_consumption_limits: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
