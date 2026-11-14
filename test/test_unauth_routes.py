#!/usr/bin/env python3
"""Routes that answered without a token are closed — the documentation-audit gate.

Found by the Oct-2026 documentation audit (the REST inventory diffed every route against
its auth decorators):

1. ``GET /api_key/<provider>`` (routes.py) returned the SERVER's environment LLM keys
   (Google, Anthropic, OpenAI, Azure, Bedrock, Friendli) to any caller. Now: signed in AND
   admin; the provider form treats the 403 as "nothing to pre-fill".
2. ``GET /api/notifications/subscribe`` trusted a ``user_uuid`` query parameter: anyone who
   knew a user's id could read that user's notification stream. Now: the user comes from a
   one-minute signed ticket (``POST /api/notifications/ticket``, issued only WITH the
   Authorization header — EventSource cannot send one). The ticket carries no ``user_id``
   claim, so it is never accepted as a bearer token, and a session token is never accepted
   as a ticket.
3. ``POST /v1/admin/session-stores/cleanup-orphans`` had no admin check and judged orphans
   against the CALLER's sessions — without a token every external session store read as an
   orphan. Now: admin only, judged over every user's sessions, nothing deleted when the
   session index cannot be read.
4. ``POST /v1/rag/collections/<id>/refresh`` had no auth. Now: the collection's owner or an
   admin; the repository card sends its token.
5. ``GET /v1/rag/collections`` (Nov 2026) answered an anonymous caller with the admin view —
   every user's repositories. Now: 401 without a user.

Structural (decorators, bodies, client calls) plus behavioural for the ticket (no server).
Deploy verification: ``curl -s -o /dev/null -w '%{http_code}' …/api_key/google`` → 401.

Run:  python test/test_unauth_routes.py
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  FAIL: {label}")


def _view(src, route_regex):
    """The decorators + body of the view a route regex introduces (up to the next route)."""
    m = re.search(route_regex, src)
    if not m:
        return None
    start = m.start()
    nxt = re.search(r"\n@\w+\.route\(", src[m.end():])
    return src[start:m.end() + nxt.start()] if nxt else src[start:]


routes = open(os.path.join(ROOT, "src/trusted_data_agent/api/routes.py"), encoding="utf-8").read()
rest = open(os.path.join(ROOT, "src/trusted_data_agent/api/rest_routes.py"), encoding="utf-8").read()

# ── 1. /api_key/<provider> ────────────────────────────────────────────────────────────
v = _view(routes, r'@api_bp\.route\("/api_key/<provider>"\)')
check("/api_key/<provider> is declared", v is not None)
if v:
    check("/api_key/<provider> carries @require_auth and receives current_user",
          "@require_auth\nasync def get_api_key(current_user, provider):" in v)
    check("/api_key/<provider> refuses a non-admin with 403 BEFORE reading any env key",
          v.index("is_admin") < v.index("os.environ") and "403" in v[:v.index("os.environ")])
api_js = open(os.path.join(ROOT, "static/js/api.js"), encoding="utf-8").read()
check("the provider form treats the admin-only 403 as nothing to pre-fill",
      "if (res.status === 403) return {};" in api_js)

# ── 2. notification stream ────────────────────────────────────────────────────────────
v = _view(routes, r'@api_bp\.route\("/api/notifications/subscribe", methods=\["GET"\]\)')
check("/api/notifications/subscribe is declared", v is not None)
if v:
    check("the stream's user comes from the ticket", 'verify_stream_ticket(request.args.get("ticket", ""))' in v)
    check("the stream no longer reads a user_uuid query parameter", 'request.args.get("user_uuid")' not in v)
    check("a missing/invalid ticket is a 401", "401" in v.split("async def notification_generator")[0])
v = _view(routes, r'@api_bp\.route\("/api/notifications/ticket", methods=\["POST"\]\)')
check("the ticket is issued only to an authenticated caller, for that caller",
      v is not None and "@require_auth" in v and "create_stream_ticket(current_user.id)" in v)
nf = open(os.path.join(ROOT, "static/js/notifications.js"), encoding="utf-8").read()
check("the page asks for a ticket WITH its Authorization header",
      re.search(r"fetch\('/api/notifications/ticket',\s*\{\s*method: 'POST',\s*headers: token \? \{ 'Authorization'", nf) is not None)
check("the page opens the stream with the ticket, never with the user id",
      "/api/notifications/subscribe?ticket=${encodeURIComponent(ticket)}" in nf
      and "subscribe?user_uuid=" not in nf)
check("a failed ticket request backs off and retries (the channel heals by itself)",
      re.search(r"\.catch\(err => \{[\s\S]{0,400}_scheduleResubscribe\(_sseRetryDelay\)", nf) is not None)

# behavioural: the ticket and the session token are not interchangeable
import jwt  # noqa: E402
from trusted_data_agent.auth import security  # noqa: E402

ticket = security.create_stream_ticket("user-abc")
claims = jwt.decode(ticket, security.JWT_SECRET_KEY, algorithms=[security.JWT_ALGORITHM])
check("a ticket carries no user_id claim (get_current_user can never accept it as a bearer)",
      "user_id" not in claims and claims.get("stream_sub") == "user-abc")
check("a ticket expires within STREAM_TICKET_SECONDS",
      claims["exp"] - claims["iat"] <= security.STREAM_TICKET_SECONDS)
check("verify_auth_token refuses a ticket (no user_id to load)",
      (security.verify_auth_token(ticket) or {}).get("user_id") is None)


class _FakeUser:
    is_active = True


class _FakeQuery:
    def __init__(self, user): self._u = user
    def filter_by(self, **kw): return self
    def first(self): return self._u


class _FakeSession:
    def __init__(self, user): self._u = user
    def query(self, model): return _FakeQuery(self._u)


class _FakeCtx:
    def __init__(self, user): self._u = user
    def __enter__(self): return _FakeSession(self._u)
    def __exit__(self, *a): return False


_real_session = security.get_db_session
try:
    security.get_db_session = lambda: _FakeCtx(_FakeUser())
    check("a fresh ticket names its user", security.verify_stream_ticket(ticket) == "user-abc")
    session_like = jwt.encode({"user_id": "user-abc", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                              security.JWT_SECRET_KEY, algorithm=security.JWT_ALGORITHM)
    check("a session token is not a ticket", security.verify_stream_ticket(session_like) is None)
    expired = jwt.encode({"stream_sub": "user-abc", "purpose": security.STREAM_TICKET_PURPOSE,
                          "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
                         security.JWT_SECRET_KEY, algorithm=security.JWT_ALGORITHM)
    check("an expired ticket is refused", security.verify_stream_ticket(expired) is None)
    forged = jwt.encode({"stream_sub": "user-abc", "purpose": security.STREAM_TICKET_PURPOSE,
                         "exp": datetime.now(timezone.utc) + timedelta(seconds=60)},
                        "not-the-secret", algorithm=security.JWT_ALGORITHM)
    check("a ticket signed with another key is refused", security.verify_stream_ticket(forged) is None)
    check("an empty ticket is refused", security.verify_stream_ticket("") is None)
    inactive = _FakeUser(); inactive.is_active = False
    security.get_db_session = lambda: _FakeCtx(inactive)
    check("a ticket for a deactivated user is refused", security.verify_stream_ticket(ticket) is None)
    security.get_db_session = lambda: _FakeCtx(None)
    check("a ticket for a deleted user is refused", security.verify_stream_ticket(ticket) is None)
finally:
    security.get_db_session = _real_session

# ── 3. cleanup-orphans ────────────────────────────────────────────────────────────────
v = _view(rest, r'@rest_api_bp\.route\("/v1/admin/session-stores/cleanup-orphans", methods=\["POST"\]\)')
check("cleanup-orphans is declared", v is not None)
if v:
    check("cleanup-orphans is admin only", "@require_admin\nasync def cleanup_orphan_session_stores():" in v)
    check("orphans are judged over every user's sessions, not the caller's",
          "user_uuid=None, include_archived=False, filter_by_user=False" in v
          and "get_all_sessions(user_uuid" not in v and "_get_user_uuid_from_request" not in v)
    check("an unreadable session index deletes nothing (503 before any destroy)",
          "if indexed is None:" in v and v.index("if indexed is None:") < v.index("store.destroy()") and "503" in v)

# ── 3b. REST configure ────────────────────────────────────────────────────────────────
# Found in the follow-up: the REST twin of the UI's /configure ran the full service setup
# for an anonymous caller (writing the process-global LLM/MCP state) and broadcast
# "reconfigured — refresh" to EVERY connected user. Now: 401 without a user; the notice
# goes to the caller's own pages only.
v = _view(rest, r'@rest_api_bp\.route\("/v1/configure", methods=\["POST"\]\)')
check("/v1/configure is declared", v is not None)
if v:
    check("/v1/configure refuses an unauthenticated caller before any setup runs",
          "if not user_uuid:" in v and "401" in v
          and v.index("if not user_uuid:") < v.index("setup_and_categorize_services"))
    check("/v1/configure notifies only the caller's own pages",
          'get("notification_queues", {}).get(user_uuid' in v
          and ".values() for q in user_queues" not in v)

# ── 4. RAG collection refresh ─────────────────────────────────────────────────────────
v = _view(rest, r'@rest_api_bp\.route\("/v1/rag/collections/<int:collection_id>/refresh", methods=\["POST"\]\)')
check("collection refresh is declared", v is not None)
if v:
    check("collection refresh carries @require_auth",
          "@require_auth\nasync def refresh_rag_collection(current_user, collection_id: int):" in v)
    check("collection refresh requires the owner or an admin before anything else runs",
          "is_user_collection_owner(collection_id, current_user.id)" in v
          and v.index("is_user_collection_owner") < v.index("refresh_vector_store") and "403" in v)
rcm = open(os.path.join(ROOT, "static/js/handlers/ragCollectionManagement.js"), encoding="utf-8").read()
check("the repository card sends its token with the refresh",
      re.search(r"fetch\(`/api/v1/rag/collections/\$\{collectionId\}/refresh`, \{\s*method: 'POST',\s*headers: token \? \{ 'Authorization'", rcm) is not None)

# ── 5. RAG collection list ────────────────────────────────────────────────────────────
# Found Nov 2026 describing the route for TDA_Platform: without a token the user id was None,
# and get_all_collections(None) is the ADMIN view — every user's repositories (names, owners,
# source roots) answered to anyone. Now: 401 before the collection database is read.
v = _view(rest, r'@rest_api_bp\.route\("/v1/rag/collections", methods=\["GET"\]\)')
check("collection list is declared", v is not None)
if v:
    check("collection list refuses an unauthenticated caller before reading any collection",
          "if not user_uuid:" in v and "401" in v
          and v.index("if not user_uuid:") < v.index("get_all_collections("))
    check("collection list never asks for the admin view",
          "get_all_collections(user_id=user_uuid)" in v and "get_all_collections()" not in v)

total = _passed + _failed
print(f"{'FAILED' if _failed else 'ALL PASS'} — {total} assertions ({_passed} passed, {_failed} failed)")
sys.exit(1 if _failed else 0)
