"""CI-pure gate — the credential routes must not read an ORM attribute after the
session that loaded it has closed.

`get_db_session()` commits on exit, which expires every loaded attribute; the next
read raises `DetachedInstanceError`, and both credential routes read `user.id` exactly
there. Production (14 Oct 2026): `POST /v1/credentials/Anthropic/test` answered 500 for
a key that worked. The routes already hold the id (`_get_user_uuid_from_request`); the
lookup only proves the user exists.

Run:  PYTHONPATH=src python test/test_credential_routes.py
"""
import asyncio
import os
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from quart import Quart  # noqa: E402
from sqlalchemy.orm.exc import DetachedInstanceError  # noqa: E402

_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {label}")
    else:
        _failed += 1; print(f"  FAIL  {label}")


class _ExpiringUser:
    """Behaves like a User whose attributes expired when its session closed."""
    def __init__(self):
        self.detached = False

    @property
    def id(self):
        if self.detached:
            raise DetachedInstanceError("Instance <User> is not bound to a Session")
        return "u1"


class _Session:
    def __init__(self, user):
        self._user = user

    def query(self, *_a):
        return self

    def filter_by(self, **_kw):
        return self

    def first(self):
        return self._user


def _app():
    from trusted_data_agent.api.admin_routes import admin_api_bp
    app = Quart(__name__)
    app.register_blueprint(admin_api_bp, url_prefix="/api")   # as main.py mounts it
    return app


def main():
    print("== credential routes: no ORM read after the session closed ==")
    user = _ExpiringUser()

    @contextmanager
    def fake_session():
        try:
            yield _Session(user)
        finally:
            user.detached = True          # commit-on-exit expires the instance

    creds = AsyncMock(return_value={"status": "success", "credentials": {"apiKey": "k"}})
    app = _app()
    with patch("trusted_data_agent.api.admin_routes._get_user_uuid_from_request", return_value="u1"), \
         patch("trusted_data_agent.auth.database.get_db_session", fake_session), \
         patch("trusted_data_agent.core.configuration_service.retrieve_credentials_for_provider", creds):
        client = app.test_client()

        async def _get():
            r = await client.get("/api/v1/credentials/Ollama")
            return r.status_code, await r.get_json()

        async def _test():
            r = await client.post("/api/v1/credentials/Ollama/test")
            return r.status_code, await r.get_json()

        user.detached = False
        code, body = asyncio.run(_get())
        print("   GET ->", code, body)
        check("GET /v1/credentials/<provider> answers 200 after the session closed", code == 200)
        check("…and reports the stored credentials", (body or {}).get("has_credentials") is True)
        user.detached = False
        code, body = asyncio.run(_test())
        print("   POST test ->", code, body)
        check("POST /v1/credentials/<provider>/test reaches the provider branch (501 for Ollama, never 500)",
              code == 501)
        check("the credential lookup was made for the request's user, by the id already in hand",
              all(c.args[0] == "u1" for c in creds.call_args_list) and len(creds.call_args_list) == 2)
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
