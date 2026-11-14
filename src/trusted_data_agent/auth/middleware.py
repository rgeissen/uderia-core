"""
Authentication middleware for protecting routes.

Provides decorators for requiring authentication and authorization.
"""

import logging
import time as _time
import hashlib as _hashlib
from functools import wraps
from typing import Optional, Callable

from quart import request, jsonify
from werkzeug.exceptions import Unauthorized, Forbidden

from trusted_data_agent.auth.security import verify_auth_token
from trusted_data_agent.auth.database import get_db_session
from trusted_data_agent.auth.models import User

logger = logging.getLogger("quart.app")

# ---------------------------------------------------------------------------
# In-memory JWT auth cache — eliminates 2 DB queries per request for cached tokens
# ---------------------------------------------------------------------------
_auth_cache: dict = {}  # token_hash -> (User, timestamp)
_AUTH_CACHE_TTL = 60  # seconds — short enough that revocations propagate quickly
_AUTH_CACHE_MAX_SIZE = 10000


def invalidate_auth_cache(token: str = None, token_hash: str = None):
    """
    Remove a specific token from the auth cache, or clear the entire cache.

    Called from security.py on token revocation to ensure revoked tokens
    are not served from cache.
    """
    if token:
        token_hash = _hashlib.sha256(token.encode()).hexdigest()
    if token_hash and token_hash in _auth_cache:
        del _auth_cache[token_hash]
        logger.debug(f"Invalidated auth cache entry for token hash {token_hash[:12]}...")
    elif token is None and token_hash is None:
        _auth_cache.clear()
        logger.debug("Auth cache cleared entirely")


def get_current_user() -> Optional[User]:
    """
    Extract and verify authentication token from request, return User object.
    
    Supports both JWT tokens (for web UI) and access tokens (for REST API).
    Checks Authorization header for Bearer token.
    
    Returns:
        User object if authenticated, None otherwise
    """
    # Get token from Authorization header
    auth_header = request.headers.get('Authorization', '')
    
    if not auth_header.startswith('Bearer '):
        logger.debug(f"No Bearer token in Authorization header for {request.path}: '{auth_header[:20] if auth_header else 'empty'}'")
        return None
    
    token = auth_header[7:]  # Remove 'Bearer ' prefix
    
    # Try access token first (format: tda_xxxxxxxx...)
    if token.startswith('tda_'):
        from trusted_data_agent.auth.security import verify_access_token
        user = verify_access_token(token)
        if user:
            logger.debug(f"Authenticated user {user.username} via access token")
            return user
        else:
            logger.warning(f"Invalid or revoked access token for {request.path}")
            return None
    
    # Otherwise, verify as JWT token
    # Check auth cache first (avoids 2 DB queries per request)
    _token_hash = _hashlib.sha256(token.encode()).hexdigest()
    _cached = _auth_cache.get(_token_hash)
    if _cached is not None:
        _cached_user, _cache_ts = _cached
        if _time.time() - _cache_ts < _AUTH_CACHE_TTL:
            return _cached_user
        else:
            del _auth_cache[_token_hash]

    payload = verify_auth_token(token)
    if not payload:
        return None

    user_id = payload.get('user_id')
    if not user_id:
        return None

    # Load user from database
    try:
        with get_db_session() as session:
            user = session.query(User).filter_by(id=user_id).first()

            if not user:
                logger.warning(f"Token valid but user {user_id} not found")
                # Revoke this token since the user no longer exists
                from trusted_data_agent.auth.security import revoke_token
                try:
                    revoke_token(token)
                    logger.info(f"Revoked orphaned token for non-existent user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to revoke orphaned token: {e}")
                return None

            if not user.is_active:
                logger.warning(f"Inactive user {user_id} attempted to authenticate")
                return None

            # Detach from session to use outside context manager
            session.expunge(user)
            # An agent's request (a token bound to one operation): marked, so no cross-user
            # feature is granted to it whatever the user's tier (auth/features.acting_for_agent).
            if payload.get('op_scope') is not None:
                user.agent_operation = (payload['op_scope'] or {}).get('op') or "operation"
                try:   # the request is an agent's: session reads stay on its user's own
                    from quart import g
                    g.agent_operation = user.agent_operation   # (core/config.sessions_filtered_by_user)
                except Exception:  # noqa: BLE001
                    pass

            # Cache the verified user (evict oldest if at capacity) — never a token bound to one
            # request: the cache is read before verification, so a cached one would skip its check.
            if payload.get('op_scope') is None:
                if len(_auth_cache) >= _AUTH_CACHE_MAX_SIZE:
                    _oldest_key = min(_auth_cache, key=lambda k: _auth_cache[k][1])
                    del _auth_cache[_oldest_key]
                _auth_cache[_token_hash] = (user, _time.time())

            return user

    except Exception as e:
        logger.error(f"Error loading user during authentication: {e}", exc_info=True)
        return None


SESSION_STATE_HEADER = "X-Session-State"


def session_invalid_marker(auth_header: str, path: str, status_code: int) -> bool:
    """Whether a response should carry ``X-Session-State: invalid``.

    The ONE rule behind the browser's one reaction (auth.js's fetch guard): a 401 from the
    API on a request that presented a web SESSION token (a Bearer JWT — not a ``tda_`` access
    token, which belongs to scripts) means the session itself was refused — expired, idle-
    revoked, revoked elsewhere, or the user gone. Every 401 emitter (the decorators here and
    the hand-rolled checks in the admin/marketplace routes) is covered by stamping at the
    app's after-request seam, so no route has to remember to say it.

    Without the marker a tab kept running on a dead token: every widget rendered its own
    empty state ("No scored runs yet.") while the server logged "attempt to use revoked
    token" on each click (prod, 2026-10-20).
    """
    if status_code != 401 or not (path or "").startswith("/api/"):
        return False
    hdr = auth_header or ""
    if not hdr.startswith("Bearer "):
        return False
    return not hdr[7:].startswith("tda_")


def require_auth(f: Callable) -> Callable:
    """
    Decorator to require authentication for a route.
    
    Usage:
        @app.route('/protected')
        @require_auth
        async def protected_route(current_user):
            return jsonify({'user': current_user.username})
    
    The decorated function will receive current_user as first argument.
    Returns 401 Unauthorized if not authenticated.
    """
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        current_user = get_current_user()
        
        if not current_user:
            logger.info(f"Unauthorized access attempt to {request.path}")
            return jsonify({
                'status': 'error',
                'message': 'Authentication required. Please login.'
            }), 401
        
        # Inject current_user as first argument
        return await f(current_user, *args, **kwargs)
    
    # The tier this guard enforces, read by the operation catalog (api/operations.py) to check a
    # route's descriptor never offers it below the tier the route itself demands.
    decorated_function.__auth_tier__ = getattr(f, "__auth_tier__", None) or "user"
    return decorated_function


def require_admin(f: Callable) -> Callable:
    """
    Decorator to require admin privileges for a route.
    
    Usage:
        @app.route('/admin/users')
        @require_admin
        async def admin_route(current_user):
            return jsonify({'users': get_all_users()})
    
    Returns 401 if not authenticated, 403 if not admin.
    """
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        current_user = get_current_user()
        
        if not current_user:
            logger.info(f"Unauthorized access attempt to {request.path}")
            return jsonify({
                'status': 'error',
                'message': 'Authentication required. Please login.'
            }), 401
        
        if not current_user.is_admin:
            logger.warning(f"Non-admin user {current_user.username} attempted to access {request.path}")
            return jsonify({
                'status': 'error',
                'message': 'Admin privileges required.'
            }), 403
        
        # Inject current_user as first argument
        return await f(current_user, *args, **kwargs)
    
    decorated_function.__auth_tier__ = "admin"
    return decorated_function


def optional_auth(f: Callable) -> Callable:
    """
    Decorator for routes that work with or without authentication.
    
    Usage:
        @app.route('/api/data')
        @optional_auth
        async def data_route(current_user):
            if current_user:
                return personalized_data(current_user)
            else:
                return public_data()
    
    Always passes current_user (may be None) as first argument.
    """
    @wraps(f)
    async def decorated_function(*args, **kwargs):
        current_user = get_current_user()
        
        # Inject current_user (may be None) as first argument
        return await f(current_user, *args, **kwargs)
    
    return decorated_function


def get_user_id_from_auth() -> Optional[str]:
    """
    Quick helper to get just the user_id from auth token.
    
    Returns:
        user_id string if authenticated, None otherwise
    """
    auth_header = request.headers.get('Authorization', '')
    
    if not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header[7:]
    payload = verify_auth_token(token)
    
    return payload.get('user_id') if payload else None


def get_request_context() -> dict:
    """
    Extract request context for audit logging.

    Returns:
        Dictionary with ip_address and user_agent
    """
    return {
        'ip_address': request.headers.get('X-Forwarded-For', request.remote_addr),
        'user_agent': request.headers.get('User-Agent', 'Unknown')
    }


def create_internal_jwt(user_uuid: str) -> str:
    """
    Create a JWT token for internal service-to-service calls.

    Used when archive functions need to call check-sessions endpoints.
    Token is short-lived (5 minutes) for security.

    Args:
        user_uuid: The user UUID to encode in the token

    Returns:
        JWT token string

    Raises:
        ValueError: If JWT secret not found
    """
    import jwt
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    # Load JWT secret
    jwt_secret_path = Path("tda_keys/jwt_secret.key")
    if not jwt_secret_path.exists():
        raise ValueError("JWT secret not found")

    with open(jwt_secret_path, "r") as f:
        secret = f.read().strip()

    # Create token payload
    payload = {
        "sub": user_uuid,
        "user_id": user_uuid,  # For compatibility with existing auth checks
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),  # Short-lived
        "internal": True  # Mark as internal service call
    }

    return jwt.encode(payload, secret, algorithm="HS256")
