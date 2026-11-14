"""
REST API routes for Phase 4 admin and credential management features.

Includes:
- User management (admin only)
- Credential storage/retrieval
- Audit log access
- System administration
"""

import asyncio
import logging
from datetime import datetime, timezone

from quart import Blueprint, request, jsonify

from trusted_data_agent.api.operations import Arg, operation

from trusted_data_agent.auth.admin import require_admin, get_current_user_from_request, can_manage_user
from trusted_data_agent.auth.database import get_db_session
from trusted_data_agent.auth.models import User, AuditLog
from trusted_data_agent.auth import audit, encryption
from trusted_data_agent.auth.security import hash_password
from trusted_data_agent.core import configuration_service

admin_api_bp = Blueprint('admin_api', __name__)
logger = logging.getLogger("quart.app")


def _norm_reporting(raw):
    """PROP-002b — validate per-station reporting levels against the one construct.
    Defensive import: the assurance registry is the single source; absent, the
    defaults stand (every station at the default level)."""
    try:
        from trusted_data_agent.agent.assurance_points import normalize_station_reporting
        return normalize_station_reporting(raw)
    except Exception:
        return raw if isinstance(raw, dict) else {}


def _get_user_uuid_from_request():
    """
    Extract user ID from request (from auth token or header).
    
    IMPORTANT: Returns user.id (database primary key).
    The user.id is required for foreign key constraints in user_credentials table.
    After migration, the user_uuid column was removed.
    """
    user = get_current_user_from_request()
    if user:
        return user.id  # Return database ID, not user_uuid
    
    # No fallback - authentication is required
    return None


# ==============================================================================
# CREDENTIAL MANAGEMENT ENDPOINTS
# ==============================================================================

@admin_api_bp.route("/v1/credentials", methods=["GET"])
async def list_credentials():
    """
    List all providers with stored credentials for current user.
    
    Returns:
    {
        "status": "success",
        "providers": ["Amazon", "Google"]
    }
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    
    result = await configuration_service.list_user_providers(user_uuid)
    
    if result["status"] == "success":
        return jsonify(result), 200
    else:
        return jsonify(result), 500


@admin_api_bp.route("/v1/credentials/<provider>", methods=["GET", "POST", "DELETE"])
async def manage_provider_credentials(provider: str):
    """
    Manage credentials for a specific provider.
    
    GET: Check if credentials exist (doesn't return actual values)
    POST: Store new credentials
    DELETE: Delete stored credentials
    
    POST body:
    {
        "credentials": {
            "apiKey": "...",  // For Google, Anthropic, etc.
            "aws_access_key_id": "...",  // For Amazon
            "aws_secret_access_key": "...",
            "aws_region": "..."
        }
    }
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    
    # Look up user by id
    from trusted_data_agent.auth.models import User
    from trusted_data_agent.auth.database import get_db_session
    
    # The lookup only proves the user exists. `get_db_session` commits on exit, which
    # expires every loaded attribute — a `user.id` read AFTER the block raised
    # DetachedInstanceError (prod, 14 Oct 2026: a working key answered 500). The id is
    # `user_uuid`, already in hand.
    with get_db_session() as session:
        if session.query(User).filter_by(id=user_uuid).first() is None:
            return jsonify({"status": "error", "message": "User not found"}), 404
    
    if request.method == "GET":
        # Check if credentials exist
        result = await configuration_service.retrieve_credentials_for_provider(user_uuid, provider)
        
        has_credentials = result.get("credentials") is not None
        return jsonify({
            "status": "success",
            "provider": provider,
            "has_credentials": has_credentials,
            "credential_keys": list(result["credentials"].keys()) if has_credentials else []
        }), 200
    
    elif request.method == "POST":
        # Store credentials
        data = await request.get_json()
        
        if not data or "credentials" not in data:
            return jsonify({
                "status": "error",
                "message": "Request body must contain 'credentials' field"
            }), 400
        
        result = await configuration_service.store_credentials_for_provider(
            user_uuid,
            provider,
            data["credentials"]
        )
        
        if result["status"] == "success":
            return jsonify(result), 200
        else:
            return jsonify(result), 400
    
    elif request.method == "DELETE":
        # Delete credentials
        result = await configuration_service.delete_credentials_for_provider(user_uuid, provider)
        
        if result["status"] == "success":
            return jsonify(result), 200
        else:
            return jsonify(result), 404


@admin_api_bp.route("/v1/credentials/<provider>/test", methods=["POST"])
async def test_provider_credentials(provider: str):
    """
    Test stored credentials by attempting a connection.
    
    Returns:
    {
        "status": "success"/"error",
        "message": "Credentials are valid" or error details
    }
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    
    # Look up user by id
    from trusted_data_agent.auth.models import User
    from trusted_data_agent.auth.database import get_db_session
    
    with get_db_session() as session:   # existence only — see manage_provider_credentials
        if session.query(User).filter_by(id=user_uuid).first() is None:
            return jsonify({"status": "error", "message": "User not found"}), 404
    
    # Retrieve stored credentials
    cred_result = await configuration_service.retrieve_credentials_for_provider(user_uuid, provider)
    
    if not cred_result.get("credentials"):
        return jsonify({
            "status": "error",
            "message": f"No stored credentials found for {provider}"
        }), 404
    
    # Test the credentials by attempting a lightweight operation
    # This reuses validation logic from setup_and_categorize_services
    try:
        credentials = cred_result["credentials"]
        
        if provider == "Google":
            from google import genai
            client = genai.Client(api_key=credentials.get("apiKey"))
            # Try listing models
            list(client.models.list())
            
        elif provider == "Anthropic":
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=credentials.get("apiKey"))
            await client.models.list()
            
        elif provider == "Amazon":
            import boto3
            client = boto3.client(
                'bedrock-runtime',
                aws_access_key_id=credentials.get("aws_access_key_id"),
                aws_secret_access_key=credentials.get("aws_secret_access_key"),
                region_name=credentials.get("aws_region")
            )
            # List foundation models as test
            client.list_foundation_models()
            
        else:
            return jsonify({
                "status": "error",
                "message": f"Credential testing not yet implemented for {provider}"
            }), 501
        
        return jsonify({
            "status": "success",
            "message": f"{provider} credentials are valid"
        }), 200
        
    except Exception as e:
        logger.error(f"Credential test failed for {provider}: {e}")
        return jsonify({
            "status": "error",
            "message": f"Credential test failed: {str(e)}"
        }), 400


# ==============================================================================
# AUDIT LOG ENDPOINTS
# ==============================================================================

@admin_api_bp.route("/v1/auth/me/audit-logs", methods=["GET"])
async def get_my_audit_logs():
    """
    Get current user's audit logs.
    
    Query params:
    - limit: Number of records (default 100)
    - offset: Skip records (default 0)
    - action: Filter by action type
    
    Returns:
    {
        "status": "success",
        "logs": [...],
        "total": 150
    }
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401
    
    limit = int(request.args.get('limit', 100))
    offset = int(request.args.get('offset', 0))
    action_filter = request.args.get('action')
    
    logs = audit.get_user_audit_logs(user_uuid, limit=limit, offset=offset, action_filter=action_filter)
    
    return jsonify({
        "status": "success",
        "logs": logs,
        "total": len(logs)
    }), 200


# ==============================================================================
# ADMIN USER MANAGEMENT ENDPOINTS
# ==============================================================================

@admin_api_bp.route("/v1/admin/db-health", methods=["GET"])
@operation("admin.db_health", effect="read", scope="instance", tier="admin",
           summary="The database's health: unreadable reads since the start, the last backup and how many are kept")
@require_admin
async def db_health_report():
    """Database health: unreadable reads since start (state, first/last seen, count, last
    error), the last daily backup and the verified backups on disk (core/db_health.py)."""
    import asyncio
    from trusted_data_agent.core import db_health
    return jsonify({"status": "success", **(await asyncio.to_thread(db_health.status))})


@admin_api_bp.route("/v1/admin/db-health/backup", methods=["POST"])
@require_admin
async def db_health_backup_now():
    """Take a verified backup now (the daily job's routine)."""
    import asyncio
    from trusted_data_agent.core import db_health
    try:
        return jsonify({"status": "success", **(await asyncio.to_thread(db_health.run_backup))})
    except Exception as e:  # noqa: BLE001 — the failure is recorded and reported by run_backup
        return jsonify({"status": "error", "message": str(e)[:300]}), 500


@admin_api_bp.route("/v1/admin/schema/ledger", methods=["GET"])
@require_admin
async def schema_ledger_report():
    """The schema ledger: the last starts (version, fingerprint, backup, schema change), the
    upgrade backups on disk, and the drift — tables and columns the schema files declare that
    the database lacks. Read-only."""
    import json
    from trusted_data_agent.auth.database import DATABASE_URL
    from trusted_data_agent.core import schema_ledger as L

    def _build():
        rows = L.records(DATABASE_URL, limit=int(request.args.get("limit", 20)))
        for r in rows:
            try:
                r["drift"] = json.loads(r.pop("drift_json") or "{}")
            except Exception:  # noqa: BLE001
                r["drift"] = {}
        backups = [{"path": str(p), "bytes": p.stat().st_size} for p in L.backups(DATABASE_URL)]
        return {"status": "success", "backup_mode": L.backup_mode(), "records": rows,
                "backups": backups, "drift": L.drift(DATABASE_URL)}

    return jsonify(await asyncio.to_thread(_build)), 200


@admin_api_bp.route("/v1/admin/users", methods=["GET", "POST"])
@require_admin
async def manage_users():
    """
    Manage users (admin only).
    
    GET - List all users:
    Query params:
    - limit: Number of records (default 50)
    - offset: Skip records (default 0)
    - search: Search by username or email
    - active_only: Filter active users (default false)
    
    POST - Create new user:
    Body:
    {
        "username": "newuser",
        "email": "user@example.com",
        "password": "password123",
        "display_name": "New User",
        "profile_tier": "user"
    }
    
    Returns:
    {
        "status": "success",
        "users": [...] or "user": {...},
        "total": 25
    }
    """
    if request.method == "POST":
        # Create new user
        admin_user = get_current_user_from_request()
        data = await request.get_json()
        
        required_fields = ['username', 'email', 'password']
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "status": "error",
                    "message": f"Missing required field: {field}"
                }), 400
        
        try:
            import uuid
            
            logger.info(f"Creating user with data: username={data.get('username')}, email={data.get('email')}")
            
            with get_db_session() as session:
                # Check if username already exists (among active users only)
                existing = session.query(User).filter_by(username=data['username'], is_active=True).first()
                if existing:
                    logger.warning(f"Username already exists: {data['username']}")
                    return jsonify({
                        "status": "error",
                        "message": "Username already exists"
                    }), 400
                
                # Check if email already exists (among active users only)
                existing_email = session.query(User).filter_by(email=data['email'], is_active=True).first()
                if existing_email:
                    return jsonify({
                        "status": "error",
                        "message": "Email already exists"
                    }), 400
                
                # Admin-vouched pre-verification: when set, the user skips the email-verification
                # step and can log in immediately. Defaults to False (the user must verify their
                # email) so existing behaviour is unchanged. Accepts `pre_verified` or the explicit
                # `email_verified` flag.
                pre_verified = bool(data.get('pre_verified') or data.get('email_verified', False))

                # Create new user
                new_user = User(
                    id=str(uuid.uuid4()),
                    username=data['username'],
                    email=data['email'],
                    password_hash=hash_password(data['password']),
                    display_name=data.get('display_name', data['username']),
                    full_name=data.get('full_name'),
                    profile_tier=data.get('profile_tier', 'user'),
                    is_admin=(data.get('profile_tier', 'user') == 'admin'),
                    is_active=True,
                    email_verified=pre_verified,
                    failed_login_attempts=0,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )

                session.add(new_user)
                session.commit()
                
                # Log admin action
                audit.log_admin_action(
                    admin_user.id,
                    "user_create",
                    new_user.id,
                    f"Created user {new_user.username}"
                )
                
                response_data = {
                    "status": "success",
                    "message": "User created successfully",
                    "user": {
                        "id": new_user.id,
                        "username": new_user.username,
                        "email": new_user.email,
                        "display_name": new_user.display_name,
                        "profile_tier": new_user.profile_tier,
                        "email_verified": new_user.email_verified
                    }
                }
                logger.info(f"User creation successful, returning: {response_data}")
                return jsonify(response_data), 201
                
        except Exception as e:
            logger.error(f"Failed to create user: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
    
    # GET - List users
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    search = request.args.get('search')
    status = request.args.get('status', 'all').lower()
    
    try:
        with get_db_session() as session:
            query = session.query(User)
            
            if status == 'active':
                query = query.filter_by(is_active=True)
            elif status == 'inactive':
                query = query.filter_by(is_active=False)
            
            if search:
                query = query.filter(
                    (User.username.ilike(f'%{search}%')) |
                    (User.email.ilike(f'%{search}%'))
                )
            
            total = query.count()
            users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()
            
            # Get current user for comparison
            current_user = get_current_user_from_request()
            
            user_list = []
            for user in users:
                # Get feature count for this user's tier
                try:
                    from trusted_data_agent.auth.features import get_user_features
                    user_features = get_user_features(user)
                    feature_count = len(user_features)
                except Exception as e:
                    logger.warning(f"Failed to get user features for {user.username}: {e}")
                    feature_count = 0
                
                # Get consumption profile information
                consumption_profile_id = None
                consumption_profile_name = None
                if hasattr(user, 'consumption_profile_id') and user.consumption_profile_id:
                    consumption_profile_id = user.consumption_profile_id
                    if hasattr(user, 'consumption_profile') and user.consumption_profile:
                        consumption_profile_name = user.consumption_profile.name
                
                user_list.append({
                    'id': user.id,
                    'user_uuid': user.id,
                    'username': user.username,
                    'email': user.email,
                    'display_name': user.display_name,
                    'is_active': user.is_active,
                    'is_admin': user.is_admin,
                    'profile_tier': user.profile_tier,
                    'consumption_profile_id': consumption_profile_id,
                    'consumption_profile_name': consumption_profile_name,
                    'feature_count': feature_count,
                    'is_current_user': current_user and user.id == current_user.id,
                    'created_at': user.created_at.isoformat(),
                    'last_login_at': user.last_login_at.isoformat() if user.last_login_at else None,
                    'failed_login_attempts': user.failed_login_attempts
                })
            
            from sqlalchemy import func
            tier_counts = {'user': 0, 'developer': 0, 'admin': 0}
            for tier, count in session.query(User.profile_tier, func.count(User.id)).group_by(User.profile_tier).all():
                tier_counts[tier] = count

            return jsonify({
                "status": "success",
                "users": user_list,
                "total": total,
                "limit": limit,
                "offset": offset,
                "tier_counts": tier_counts,
            }), 200
            
    except Exception as e:
        logger.error(f"Failed to list users: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route("/v1/admin/users/<user_id>", methods=["GET", "PATCH", "DELETE"])
@require_admin
async def manage_user(user_id: str):
    """
    Manage a specific user (admin only).
    
    GET: Get user details with audit history
    PATCH: Update user (activate/deactivate, change role)
    DELETE: Soft delete user
    
    PATCH body:
    {
        "is_active": true/false,
        "is_admin": true/false,
        "display_name": "New Name"
    }
    """
    admin_user = get_current_user_from_request()
    
    if request.method == "GET":
        try:
            with get_db_session() as session:
                user = session.query(User).filter_by(id=user_id).first()
                
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
                
                # Get recent audit logs
                audit_logs = audit.get_user_audit_logs(user.id, limit=20)
                
                # Get stored credential providers
                providers = encryption.list_user_providers(user.id)
                
                return jsonify({
                    "status": "success",
                    "user": {
                        'id': user.id,
                        'user_uuid': user.id,
                        'username': user.username,
                        'email': user.email,
                        'display_name': user.display_name,
                        'full_name': user.full_name,
                        'is_active': user.is_active,
                        'is_admin': user.is_admin,
                        'profile_tier': user.profile_tier,
                        'created_at': user.created_at.isoformat(),
                        'updated_at': user.updated_at.isoformat(),
                        'last_login_at': user.last_login_at.isoformat() if user.last_login_at else None,
                        'failed_login_attempts': user.failed_login_attempts,
                        'locked_until': user.locked_until.isoformat() if user.locked_until else None,
                        'stored_providers': providers
                    },
                    "recent_audit_logs": audit_logs
                }), 200
                
        except Exception as e:
            logger.error(f"Failed to get user: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
    
    elif request.method == "PATCH":
        # Prevent self-modification
        if not can_manage_user(admin_user, user_id):
            return jsonify({
                "status": "error",
                "message": "Cannot modify your own admin status"
            }), 403
        
        data = await request.get_json()
        
        try:
            with get_db_session() as session:
                user = session.query(User).filter_by(id=user_id).first()
                
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
                
                changes = []
                
                if 'is_active' in data:
                    user.is_active = bool(data['is_active'])
                    changes.append(f"is_active={user.is_active}")
                
                if 'is_admin' in data:
                    user.is_admin = bool(data['is_admin'])
                    # Sync profile_tier with is_admin for backward compatibility
                    if user.is_admin:
                        user.profile_tier = 'admin'
                    changes.append(f"is_admin={user.is_admin}")
                
                if 'profile_tier' in data:
                    from trusted_data_agent.auth.admin import PROFILE_TIER_USER, PROFILE_TIER_DEVELOPER, PROFILE_TIER_ADMIN
                    new_tier = data['profile_tier'].lower()
                    valid_tiers = [PROFILE_TIER_USER, PROFILE_TIER_DEVELOPER, PROFILE_TIER_ADMIN]
                    if new_tier in valid_tiers:
                        user.profile_tier = new_tier
                        # Sync is_admin flag
                        user.is_admin = (new_tier == PROFILE_TIER_ADMIN)
                        changes.append(f"profile_tier={new_tier}")
                
                if 'display_name' in data:
                    user.display_name = data['display_name']
                    changes.append(f"display_name={user.display_name}")
                
                if 'email' in data:
                    # Check if email is already taken by another active user
                    existing = session.query(User).filter_by(email=data['email'], is_active=True).first()
                    if existing and existing.id != user_id:
                        return jsonify({
                            "status": "error",
                            "message": "Email already in use"
                        }), 400
                    user.email = data['email']
                    changes.append(f"email={user.email}")
                
                if 'username' in data:
                    # Check if username is already taken by another active user
                    existing = session.query(User).filter_by(username=data['username'], is_active=True).first()
                    if existing and existing.id != user_id:
                        return jsonify({
                            "status": "error",
                            "message": "Username already in use"
                        }), 400
                    user.username = data['username']
                    changes.append(f"username={user.username}")
                
                if 'password' in data and data['password']:
                    # Admin can reset user password
                    user.password_hash = hash_password(data['password'])
                    changes.append("password=***")
                
                user.updated_at = datetime.now(timezone.utc)
                session.commit()
                
                # Log admin action
                audit.log_admin_action(
                    admin_user.id,
                    "user_update",
                    user.id,
                    f"Updated user: {', '.join(changes)}"
                )
                
                return jsonify({
                    "status": "success",
                    "message": f"User updated: {', '.join(changes)}"
                }), 200
                
        except Exception as e:
            logger.error(f"Failed to update user: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500
    
    elif request.method == "DELETE":
        # Prevent self-deletion
        if not can_manage_user(admin_user, user_id):
            return jsonify({
                "status": "error",
                "message": "Cannot delete your own account"
            }), 403
        
        try:
            with get_db_session() as session:
                user = session.query(User).filter_by(id=user_id).first()
                
                if not user:
                    return jsonify({"status": "error", "message": "User not found"}), 404
                
                # Soft delete by deactivating
                user.is_active = False
                user.updated_at = datetime.now(timezone.utc)
                session.commit()
                
                # Log admin action
                audit.log_admin_action(
                    admin_user.id,
                    "user_delete",
                    user.id,
                    f"Deactivated user {user.username}"
                )
                
                return jsonify({
                    "status": "success",
                    "message": f"User {user.username} deactivated"
                }), 200
                
        except Exception as e:
            logger.error(f"Failed to delete user: {e}", exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route("/v1/admin/users/<user_id>/unlock", methods=["POST"])
@require_admin
async def unlock_user(user_id: str):
    """
    Unlock a locked user account (admin only).
    
    Returns:
    {
        "status": "success",
        "message": "User unlocked"
    }
    """
    admin_user = get_current_user_from_request()
    
    try:
        with get_db_session() as session:
            user = session.query(User).filter_by(id=user_id).first()
            
            if not user:
                return jsonify({"status": "error", "message": "User not found"}), 404
            
            user.failed_login_attempts = 0
            user.locked_until = None
            user.updated_at = datetime.now(timezone.utc)
            session.commit()
            
            # Log admin action
            audit.log_admin_action(
                admin_user.id,
                "user_unlock",
                user.id,
                f"Unlocked user {user.username}"
            )
            
            return jsonify({
                "status": "success",
                "message": f"User {user.username} unlocked"
            }), 200
            
    except Exception as e:
        logger.error(f"Failed to unlock user: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route("/v1/admin/users/<user_id>/tier", methods=["PATCH"])
@require_admin
async def change_user_tier(user_id: str):
    """
    Change user's profile tier (admin only).
    
    Profile tiers:
    - user: Basic access (default)
    - developer: Advanced features (RAG, templates, testing)
    - admin: Full system access
    
    POST body:
    {
        "profile_tier": "developer"
    }
    
    Returns:
    {
        "status": "success",
        "message": "User promoted to developer tier",
        "user": {
            "id": "...",
            "username": "...",
            "profile_tier": "developer"
        }
    }
    """
    from trusted_data_agent.auth.admin import PROFILE_TIER_USER, PROFILE_TIER_DEVELOPER, PROFILE_TIER_ADMIN
    
    admin_user = get_current_user_from_request()
    
    # Prevent self-modification
    if not can_manage_user(admin_user, user_id):
        return jsonify({
            "status": "error",
            "message": "Cannot modify your own profile tier"
        }), 403
    
    data = await request.get_json()
    
    if not data or 'profile_tier' not in data:
        return jsonify({
            "status": "error",
            "message": "Request body must contain 'profile_tier' field"
        }), 400
    
    new_tier = data['profile_tier'].lower()
    valid_tiers = [PROFILE_TIER_USER, PROFILE_TIER_DEVELOPER, PROFILE_TIER_ADMIN]
    
    if new_tier not in valid_tiers:
        return jsonify({
            "status": "error",
            "message": f"Invalid profile tier. Must be one of: {', '.join(valid_tiers)}"
        }), 400
    
    try:
        with get_db_session() as session:
            user = session.query(User).filter_by(id=user_id).first()
            
            if not user:
                return jsonify({"status": "error", "message": "User not found"}), 404
            
            old_tier = user.profile_tier
            user.profile_tier = new_tier
            
            # Sync is_admin flag for backward compatibility
            user.is_admin = (new_tier == PROFILE_TIER_ADMIN)
            
            user.updated_at = datetime.now(timezone.utc)
            session.commit()
            
            # Log admin action
            audit.log_admin_action(
                admin_user.id,
                "tier_change",
                user.id,
                f"Changed profile tier: {old_tier} -> {new_tier}"
            )
            
            action = "promoted" if valid_tiers.index(new_tier) > valid_tiers.index(old_tier) else "changed"
            
            return jsonify({
                "status": "success",
                "message": f"User {action} to {new_tier} tier",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "profile_tier": user.profile_tier,
                    "is_admin": user.is_admin
                }
            }), 200
            
    except Exception as e:
        logger.error(f"Failed to change user tier: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route("/v1/admin/stats", methods=["GET"])
@operation("admin.stats", effect="read", scope="instance", tier="admin",
           summary="The deployment's users in numbers: how many, active, locked, by tier, recent sign-ins and registrations — counts only")
@require_admin
async def get_admin_stats():
    """
    Get system statistics (admin only).
    
    Returns:
    {
        "status": "success",
        "stats": {
            "total_users": 25,
            "active_users": 20,
            "admin_users": 2,
            "locked_users": 1,
            "recent_logins_24h": 15,
            "recent_registrations_7d": 5
        }
    }
    """
    try:
        from datetime import timedelta
        
        with get_db_session() as session:
            now = datetime.now(timezone.utc)
            day_ago = now - timedelta(days=1)
            week_ago = now - timedelta(days=7)
            
            total_users = session.query(User).count()
            active_users = session.query(User).filter_by(is_active=True).count()
            admin_users = session.query(User).filter_by(is_admin=True).count()
            locked_users = session.query(User).filter(User.locked_until > now).count()
            
            recent_logins = session.query(User).filter(User.last_login_at >= day_ago).count()
            recent_registrations = session.query(User).filter(User.created_at >= week_ago).count()
            
            # Recent audit events
            recent_audits = session.query(AuditLog).filter(AuditLog.timestamp >= day_ago).count()
            
            # Profile tier distribution
            from sqlalchemy import func
            tier_counts = session.query(
                User.profile_tier, 
                func.count(User.id)
            ).group_by(User.profile_tier).all()
            
            tier_distribution = {tier: count for tier, count in tier_counts}
            
            return jsonify({
                "status": "success",
                "stats": {
                    "total_users": total_users,
                    "active_users": active_users,
                    "admin_users": admin_users,
                    "locked_users": locked_users,
                    "recent_logins_24h": recent_logins,
                    "recent_registrations_7d": recent_registrations,
                    "recent_audit_events_24h": recent_audits,
                    "tier_distribution": tier_distribution
                }
            }), 200
            
    except Exception as e:
        logger.error(f"Failed to get stats: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ==============================================================================
# FEATURE MANAGEMENT ENDPOINTS
# ==============================================================================

@admin_api_bp.route("/v1/admin/features", methods=["GET"])
@require_admin
async def get_all_features():
    """
    Get all features with their tier mappings and metadata.
    
    Returns:
    {
        "status": "success",
        "features": [
            {
                "name": "execute_prompts",
                "display_name": "Execute Prompts",
                "required_tier": "user",
                "category": "Core Execution",
                "description": "Execute AI prompts"
            },
            ...
        ],
        "feature_count_by_tier": {
            "user": 19,
            "developer": 25,
            "admin": 22
        }
    }
    """
    try:
        from trusted_data_agent.auth.features import (
            Feature, FEATURE_TIER_MAP, get_feature_info
        )
        from trusted_data_agent.auth.admin import PROFILE_TIER_USER, PROFILE_TIER_DEVELOPER, PROFILE_TIER_ADMIN
        
        feature_info = get_feature_info()
        
        # Count features by tier (features exclusive to that tier)
        tier_counts = {
            PROFILE_TIER_USER: 0,
            PROFILE_TIER_DEVELOPER: 0,
            PROFILE_TIER_ADMIN: 0
        }
        
        for feature_name, tier in FEATURE_TIER_MAP.items():
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        
        return jsonify({
            "status": "success",
            "features": feature_info,
            "feature_count_by_tier": tier_counts
        }), 200
        
    except Exception as e:
        logger.error(f"Failed to get features: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route("/v1/admin/features/<feature_name>/tier", methods=["PATCH"])
@require_admin
async def update_feature_tier(feature_name: str):
    """
    Update the required tier for a specific feature.
    
    Request body:
    {
        "required_tier": "developer"  // "user", "developer", or "admin"
    }
    
    Returns:
    {
        "status": "success",
        "feature": "create_rag_collections",
        "old_tier": "developer",
        "new_tier": "admin"
    }
    """
    try:
        from trusted_data_agent.auth.features import Feature, FEATURE_TIER_MAP
        from trusted_data_agent.auth.admin import (
            PROFILE_TIER_USER, PROFILE_TIER_DEVELOPER, PROFILE_TIER_ADMIN, TIER_HIERARCHY
        )
        
        data = await request.get_json()
        new_tier = data.get("required_tier")
        
        if not new_tier or new_tier not in TIER_HIERARCHY:
            return jsonify({
                "status": "error",
                "message": f"Invalid tier. Must be one of: {', '.join(TIER_HIERARCHY)}"
            }), 400
        
        # Find the feature
        feature_enum = None
        for f in Feature:
            if f.value == feature_name:
                feature_enum = f
                break
        
        if not feature_enum:
            return jsonify({
                "status": "error",
                "message": f"Feature '{feature_name}' not found"
            }), 404
        
        old_tier = FEATURE_TIER_MAP.get(feature_enum)
        
        # Update the feature tier mapping (in-memory)
        FEATURE_TIER_MAP[feature_enum] = new_tier
        
        # Log the change
        current_user = get_current_user_from_request()
        audit.log_audit_event(
            user_id=current_user.id if current_user else None,
            action='feature_tier_changed',
            details=f"Changed feature '{feature_name}' tier from '{old_tier}' to '{new_tier}'",
            success=True,
            resource=f'/api/v1/admin/features/{feature_name}/tier',
            metadata={
                "feature": feature_name,
                "old_tier": old_tier,
                "new_tier": new_tier
            }
        )
        
        return jsonify({
            "status": "success",
            "feature": feature_name,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "message": "Feature tier updated successfully"
        }), 200
        
    except Exception as e:
        logger.error(f"Failed to update feature tier: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route("/v1/admin/features/reset", methods=["POST"])
@require_admin
async def reset_feature_tiers():
    """
    Reset all feature tiers to their default values.
    
    Returns:
    {
        "status": "success",
        "message": "Feature tiers reset to defaults",
        "reset_count": 66
    }
    """
    try:
        from trusted_data_agent.auth.features import Feature, FEATURE_TIER_MAP, get_default_feature_tier_map
        
        # Get default mappings
        default_map = get_default_feature_tier_map()
        
        # Reset all features
        reset_count = 0
        for feature, default_tier in default_map.items():
            if FEATURE_TIER_MAP.get(feature) != default_tier:
                FEATURE_TIER_MAP[feature] = default_tier
                reset_count += 1
        
        # Log the reset
        current_user = get_current_user_from_request()
        audit.log_audit_event(
            user_id=current_user.id if current_user else None,
            action='features_reset',
            details=f"Reset all feature tiers to defaults ({reset_count} changes)",
            success=True,
            resource='/api/v1/admin/features/reset',
            metadata={"reset_count": reset_count}
        )
        
        return jsonify({
            "status": "success",
            "message": "Feature tiers reset to defaults",
            "reset_count": reset_count
        }), 200
        
    except Exception as e:
        logger.error(f"Failed to reset feature tiers: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ==============================================================================
# PANE VISIBILITY MANAGEMENT
# ==============================================================================

@admin_api_bp.route('/v1/admin/panes', methods=['GET'])
@require_admin
async def get_panes():
    """
    Get all pane visibility configurations.
    
    Returns list of panes with tier visibility settings.
    
    Example Response:
    {
        "status": "success",
        "panes": [
            {
                "id": "uuid",
                "pane_id": "conversation",
                "pane_name": "Reasoning",
                "visible_to_user": true,
                "visible_to_developer": true,
                "visible_to_admin": true,
                "description": "Conversations with your agents",
                "group": null,
                "display_order": 1
            },
            ...
        ]
    }
    """
    try:
        from trusted_data_agent.core.pane_catalog import sync_panes

        with get_db_session() as session:
            # Names, groups and order come from the catalogue (the sidebar); the
            # stored rows contribute only the admin's visibility choices.
            panes_data = sync_panes(session)
        
        return jsonify({
            "status": "success",
            "panes": panes_data
        }), 200
        
    except Exception as e:
        logger.error(f"Failed to get panes: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route('/v1/admin/panes/<pane_id>/visibility', methods=['PATCH'])
@require_admin
async def update_pane_visibility(pane_id: str):
    """
    Update visibility settings for a specific pane.
    
    Request Body:
    {
        "visible_to_user": true,
        "visible_to_developer": true,
        "visible_to_admin": true
    }
    
    Returns updated pane configuration.
    """
    try:
        from trusted_data_agent.auth.models import PaneVisibility
        from trusted_data_agent.core.pane_catalog import pane_dict

        data = await request.get_json()
        
        with get_db_session() as session:
            # Get pane
            pane = session.query(PaneVisibility).filter_by(pane_id=pane_id).first()
            if not pane:
                return jsonify({
                    "status": "error",
                    "message": f"Pane '{pane_id}' not found"
                }), 404
            
            # Update visibility flags
            if 'visible_to_user' in data:
                pane.visible_to_user = bool(data['visible_to_user'])
            if 'visible_to_developer' in data:
                pane.visible_to_developer = bool(data['visible_to_developer'])
            if 'visible_to_admin' in data:
                pane.visible_to_admin = bool(data['visible_to_admin'])
            
            # Admin pane must always be visible to admins
            if pane_id == 'admin':
                pane.visible_to_admin = True
            
            pane_data = pane_dict(pane)
        
        # Log the change
        current_user = get_current_user_from_request()
        audit.log_audit_event(
            user_id=current_user.id if current_user else None,
            action='pane_visibility_updated',
            details=f"Updated visibility for pane '{pane_id}'",
            success=True,
            resource=f'/api/v1/admin/panes/{pane_id}/visibility',
            metadata=pane_data
        )
        
        return jsonify({
            "status": "success",
            "pane": pane_data
        }), 200
        
    except Exception as e:
        logger.error(f"Failed to update pane visibility: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route('/v1/admin/panes/reset', methods=['POST'])
@require_admin
async def reset_panes():
    """
    Reset all pane visibility settings to defaults.
    
    Default Configuration:
    - admin: admin only
    - developer: developer + admin
    - user: all tiers
    
    Returns:
    {
        "status": "success",
        "message": "Pane visibility reset to defaults",
        "panes": [...]
    }
    """
    try:
        from trusted_data_agent.core.pane_catalog import reset_panes as _reset_to_catalog

        with get_db_session() as session:
            panes_data = _reset_to_catalog(session)
        
        # Log the reset
        current_user = get_current_user_from_request()
        audit.log_audit_event(
            user_id=current_user.id if current_user else None,
            action='panes_reset',
            details="Reset all pane visibility to defaults",
            success=True,
            resource='/api/v1/admin/panes/reset'
        )
        
        return jsonify({
            "status": "success",
            "message": "Pane visibility reset to defaults",
            "panes": panes_data
        }), 200
        
    except Exception as e:
        logger.error(f"Failed to reset panes: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route("/v1/admin/mcp-classification", methods=["POST"])
@require_admin
async def run_mcp_classification():
    """
    Manually trigger MCP resource classification for the default profile.
    This will use the LLM to categorize all MCP tools, prompts, and resources.
    Auto-activates services if configured but not loaded.
    
    Returns:
        200: Classification completed successfully with statistics
        500: Classification failed
    """
    try:
        from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
        from trusted_data_agent.mcp_adapter import adapter
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.core import configuration_service
        
        current_user = get_current_user_from_request()
        user_uuid = current_user.id if current_user else None
        logger.info(f"Admin {current_user.username if current_user else 'unknown'} triggered manual MCP classification")
        
        # Check if services are already loaded
        mcp_client = APP_STATE.get('mcp_client')
        llm_instance = APP_STATE.get('llm')
        
        # Log current state
        logger.info(f"MCP Classification check - mcp_client exists: {mcp_client is not None}, llm_instance exists: {llm_instance is not None}")
        logger.info(f"MCP_SERVER_CONNECTED flag: {APP_CONFIG.MCP_SERVER_CONNECTED}, SERVICES_CONFIGURED: {APP_CONFIG.SERVICES_CONFIGURED}")
        logger.info(f"CURRENT_PROVIDER: {APP_CONFIG.CURRENT_PROVIDER}, CURRENT_MODEL: {APP_CONFIG.CURRENT_MODEL}")
        
        # Auto-activate services if not loaded
        if not llm_instance or not mcp_client:
            logger.info("Services not loaded - attempting auto-activation from default profile")
            
            # Get config manager and default profile
            config_manager = get_config_manager()
            default_profile_id = config_manager.get_default_profile_id(user_uuid)
            
            if not default_profile_id:
                logger.warning("No default profile set")
                return jsonify({
                    'status': 'error',
                    'message': 'No default profile - set one in Anatomy → Agent Profiles'
                }), 400
            
            profiles = config_manager.get_profiles(user_uuid)
            default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)
            
            if not default_profile:
                logger.warning(f"Default profile {default_profile_id} not found")
                return jsonify({
                    'status': 'error',
                    'message': 'Default profile not found - reconfigure in Anatomy → Agent Profiles'
                }), 400
            
            # Get LLM configuration
            llm_config_id = default_profile.get('llmConfigurationId')
            llm_configs = config_manager.get_llm_configurations(user_uuid)
            llm_config = next((c for c in llm_configs if c.get('id') == llm_config_id), None)
            
            if not llm_config:
                logger.warning(f"LLM configuration {llm_config_id} not found for default profile")
                return jsonify({
                    'status': 'error',
                    'message': 'LLM configuration missing - check default profile in Anatomy → Agent Profiles'
                }), 400
            
            # Get credentials from encrypted storage (auth mode)
            provider = llm_config.get('provider')
            credentials = encryption.decrypt_credentials(current_user.id, provider)
            
            if not credentials:
                logger.warning(f"No credentials found in encrypted storage for provider {provider}")
                return jsonify({
                    'status': 'error',
                    'message': f'LLM credentials missing for {provider} - edit configuration and re-enter credentials'
                }), 400
            
            # Get MCP server
            mcp_server_id = default_profile.get('mcpServerId')
            mcp_servers = config_manager.get_mcp_servers(user_uuid)
            mcp_server = next((s for s in mcp_servers if s.get('id') == mcp_server_id), None)
            
            if not mcp_server:
                logger.warning(f"MCP server {mcp_server_id} not found for default profile")
                return jsonify({
                    'status': 'error',
                    'message': 'MCP server missing - check default profile in Anatomy → Agent Profiles'
                }), 400
            
            # Build configuration data
            logger.info(f"Auto-activating services: Provider={provider}, Model={llm_config.get('model')}, MCP={mcp_server.get('name')}")
            
            service_config_data = {
                "provider": provider,
                "model": llm_config.get('model'),
                "credentials": credentials,
                "user_uuid": user_uuid,
                "mcp_server": {
                    "id": mcp_server.get('id'),
                    "name": mcp_server.get('name'),
                    "host": mcp_server.get('host'),
                    "port": mcp_server.get('port'),
                    "path": mcp_server.get('path', '/mcp')
                },
            }
            
            # Activate services
            try:
                result = await configuration_service.setup_and_categorize_services(service_config_data)
                if result.get("status") != "success":
                    logger.error(f"Service activation failed: {result.get('message')}")
                    return jsonify({
                        'status': 'error',
                        'message': f"Failed to activate services: {result.get('message')}"
                    }), 500
                
                logger.info("Services auto-activated successfully")
                
                # Refresh references after activation
                mcp_client = APP_STATE.get('mcp_client')
                llm_instance = APP_STATE.get('llm')
                
            except Exception as activation_error:
                logger.error(f"Service activation error: {activation_error}", exc_info=True)
                return jsonify({
                    'status': 'error',
                    'message': f"Service activation failed: {str(activation_error)}"
                }), 500
        
        # Final validation
        if not llm_instance or not mcp_client:
            logger.error("Services still not available after auto-activation attempt")
            return jsonify({
                'status': 'error',
                'message': 'Failed to initialize services - check logs'
            }), 500
        
        # Temporarily enable classification
        original_classification_setting = APP_CONFIG.ENABLE_MCP_CLASSIFICATION
        APP_CONFIG.ENABLE_MCP_CLASSIFICATION = True
        
        try:
            # Reload MCP capabilities with classification enabled
            logger.info("Reloading MCP capabilities with classification enabled")
            # Get default profile for classification
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()
            profile_id = config_manager.get_default_profile_id(user_uuid)
            await adapter.load_and_categorize_mcp_resources(APP_STATE, user_uuid, profile_id)
            
            # Get statistics from structured data
            tools_count = sum(len(tools) for tools in APP_STATE.get('structured_tools', {}).values())
            prompts_count = sum(len(prompts) for prompts in APP_STATE.get('structured_prompts', {}).values())
            resources_count = sum(len(resources) for resources in APP_STATE.get('structured_resources', {}).values())
            
            categories_count = len(set(
                list(APP_STATE.get('structured_tools', {}).keys()) +
                list(APP_STATE.get('structured_prompts', {}).keys()) +
                list(APP_STATE.get('structured_resources', {}).keys())
            ))
            
            logger.info(f"MCP classification completed: {categories_count} categories, {tools_count} tools, {prompts_count} prompts, {resources_count} resources")
            
            return jsonify({
                'status': 'success',
                'message': 'MCP resource classification completed successfully',
                'categories_count': categories_count,
                'tools_count': tools_count,
                'prompts_count': prompts_count,
                'resources_count': resources_count
            }), 200
            
        finally:
            # Restore original setting
            APP_CONFIG.ENABLE_MCP_CLASSIFICATION = original_classification_setting
        
    except Exception as e:
        logger.error(f"Error running MCP classification: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': f'Failed to run classification: {str(e)}'
        }), 500


@admin_api_bp.route("/v1/admin/expert-settings", methods=["GET"])
@require_admin
async def get_expert_settings():
    """
    Get current expert settings values.
    
    Returns:
        200: Expert settings object
        500: Error retrieving settings
    """
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        
        settings = {
            'llm_behavior': {
                'max_retries': APP_CONFIG.LLM_API_MAX_RETRIES,
                'base_delay': APP_CONFIG.LLM_API_BASE_DELAY,
                'max_output_tokens': APP_CONFIG.LLM_MAX_OUTPUT_TOKENS
            },
            'agent_config': {
                'max_execution_steps': APP_CONFIG.MAX_EXECUTION_STEPS if hasattr(APP_CONFIG, 'MAX_EXECUTION_STEPS') else 25,
                'tool_call_timeout': APP_CONFIG.TOOL_CALL_TIMEOUT if hasattr(APP_CONFIG, 'TOOL_CALL_TIMEOUT') else 60,
                'max_execution_depth': APP_CONFIG.MAX_EXECUTION_DEPTH,
                'max_phase_retry_attempts': APP_CONFIG.MAX_PHASE_RETRY_ATTEMPTS
            },
            'performance': {
                'context_max_rows': APP_CONFIG.CONTEXT_DISTILLATION_MAX_ROWS,
                'context_max_chars': APP_CONFIG.CONTEXT_DISTILLATION_MAX_CHARS,
                'description_threshold': APP_CONFIG.DETAILED_DESCRIPTION_THRESHOLD,
                'report_distillation_max_rows': APP_CONFIG.REPORT_DISTILLATION_MAX_ROWS,
                'report_distillation_max_chars': APP_CONFIG.REPORT_DISTILLATION_MAX_CHARS,
                'report_distillation_total_budget': APP_CONFIG.REPORT_DISTILLATION_TOTAL_BUDGET,
                'report_distillation_aggressive_rows': APP_CONFIG.REPORT_DISTILLATION_AGGRESSIVE_ROWS,
                'document_context_max_chars': APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS,
                'document_per_file_max_chars': APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS
            },
            'agent_behavior': {
                'allow_synthesis': APP_CONFIG.ALLOW_SYNTHESIS_FROM_HISTORY,
                'force_sub_summary': APP_CONFIG.SUB_PROMPT_FORCE_SUMMARY,
                'condense_prompts': APP_CONFIG.CONDENSE_SYSTEMPROMPT_HISTORY
            },
            'query_optimization': {
                'enable_sql_consolidation': APP_CONFIG.ENABLE_SQL_CONSOLIDATION_REWRITE
            },
            'security': {
                'session_timeout': APP_CONFIG.SESSION_TIMEOUT_HOURS if hasattr(APP_CONFIG, 'SESSION_TIMEOUT_HOURS') else 24,
                'token_expiry': APP_CONFIG.TOKEN_EXPIRY_HOURS if hasattr(APP_CONFIG, 'TOKEN_EXPIRY_HOURS') else 168
            },
            'langchain_behavior': {
                'max_iterations': APP_CONFIG.LANGCHAIN_MAX_ITERATIONS
            }
        }
        
        return jsonify({
            'status': 'success',
            'settings': settings
        }), 200
        
    except Exception as e:
        logger.error(f"Error retrieving expert settings: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route("/v1/admin/expert-settings", methods=["POST"])
@require_admin
async def save_expert_settings():
    """
    Save expert settings values.
    
    Returns:
        200: Settings saved successfully
        400: Invalid settings
        500: Error saving settings
    """
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        
        data = await request.get_json()
        
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No settings provided'
            }), 400
        
        # Update LLM behavior settings
        if 'llm_behavior' in data:
            llm = data['llm_behavior']
            if 'max_retries' in llm:
                APP_CONFIG.LLM_API_MAX_RETRIES = int(llm['max_retries'])
            if 'base_delay' in llm:
                APP_CONFIG.LLM_API_BASE_DELAY = float(llm['base_delay'])
            if 'max_output_tokens' in llm:
                APP_CONFIG.LLM_MAX_OUTPUT_TOKENS = int(llm['max_output_tokens'])
        
        # Update Langchain behavior settings
        if 'langchain_behavior' in data:
            lc = data['langchain_behavior']
            if 'max_iterations' in lc:
                APP_CONFIG.LANGCHAIN_MAX_ITERATIONS = max(1, min(100, int(lc['max_iterations'])))

        # Update agent configuration
        if 'agent_config' in data:
            agent = data['agent_config']
            if 'max_execution_steps' in agent:
                APP_CONFIG.MAX_EXECUTION_STEPS = int(agent['max_execution_steps'])
            if 'tool_call_timeout' in agent:
                APP_CONFIG.TOOL_CALL_TIMEOUT = int(agent['tool_call_timeout'])
            if 'max_execution_depth' in agent:
                APP_CONFIG.MAX_EXECUTION_DEPTH = int(agent['max_execution_depth'])
            if 'max_phase_retry_attempts' in agent:
                APP_CONFIG.MAX_PHASE_RETRY_ATTEMPTS = int(agent['max_phase_retry_attempts'])
        
        # Update performance settings
        if 'performance' in data:
            perf = data['performance']
            if 'context_max_rows' in perf:
                APP_CONFIG.CONTEXT_DISTILLATION_MAX_ROWS = int(perf['context_max_rows'])
            if 'context_max_chars' in perf:
                APP_CONFIG.CONTEXT_DISTILLATION_MAX_CHARS = int(perf['context_max_chars'])
            if 'description_threshold' in perf:
                APP_CONFIG.DETAILED_DESCRIPTION_THRESHOLD = int(perf['description_threshold'])
            if 'report_distillation_max_rows' in perf:
                APP_CONFIG.REPORT_DISTILLATION_MAX_ROWS = int(perf['report_distillation_max_rows'])
            if 'report_distillation_max_chars' in perf:
                APP_CONFIG.REPORT_DISTILLATION_MAX_CHARS = int(perf['report_distillation_max_chars'])
            if 'report_distillation_total_budget' in perf:
                APP_CONFIG.REPORT_DISTILLATION_TOTAL_BUDGET = int(perf['report_distillation_total_budget'])
            if 'report_distillation_aggressive_rows' in perf:
                APP_CONFIG.REPORT_DISTILLATION_AGGRESSIVE_ROWS = int(perf['report_distillation_aggressive_rows'])
            if 'document_context_max_chars' in perf:
                APP_CONFIG.DOCUMENT_CONTEXT_MAX_CHARS = int(perf['document_context_max_chars'])
            if 'document_per_file_max_chars' in perf:
                APP_CONFIG.DOCUMENT_PER_FILE_MAX_CHARS = int(perf['document_per_file_max_chars'])
        
        # Update agent behavior settings
        if 'agent_behavior' in data:
            behavior = data['agent_behavior']
            if 'allow_synthesis' in behavior:
                APP_CONFIG.ALLOW_SYNTHESIS_FROM_HISTORY = bool(behavior['allow_synthesis'])
            if 'force_sub_summary' in behavior:
                APP_CONFIG.SUB_PROMPT_FORCE_SUMMARY = bool(behavior['force_sub_summary'])
            if 'condense_prompts' in behavior:
                APP_CONFIG.CONDENSE_SYSTEMPROMPT_HISTORY = bool(behavior['condense_prompts'])
        
        # Update query optimization settings
        if 'query_optimization' in data:
            query_opt = data['query_optimization']
            if 'enable_sql_consolidation' in query_opt:
                APP_CONFIG.ENABLE_SQL_CONSOLIDATION_REWRITE = bool(query_opt['enable_sql_consolidation'])
        
        # Update security settings
        if 'security' in data:
            security = data['security']
            if 'session_timeout' in security:
                APP_CONFIG.SESSION_TIMEOUT_HOURS = int(security['session_timeout'])
            if 'token_expiry' in security:
                APP_CONFIG.TOKEN_EXPIRY_HOURS = int(security['token_expiry'])
        
        logger.info("Expert settings updated successfully")
        
        return jsonify({
            'status': 'success',
            'message': 'Expert settings saved successfully'
        }), 200
        
    except Exception as e:
        logger.error(f"Error saving expert settings: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route("/v1/admin/window-defaults", methods=["GET"])
@require_admin
async def get_window_defaults():
    """
    Get default panel expansion states.
    
    Returns:
        200: Window defaults retrieved successfully
        500: Error retrieving defaults
    """
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        
        config_manager = get_config_manager()
        config = config_manager.load_config()
        window_defaults = config.get('window_defaults', {
            # Session History Panel
            'session_history_visible': True,
            'session_history_default_mode': 'collapsed',
            'session_history_user_can_toggle': True,
            # Resources Panel
            'resources_visible': True,
            'resources_default_mode': 'collapsed',
            'resources_user_can_toggle': True,
            # Status Window
            'status_visible': True,
            'status_default_mode': 'collapsed',
            'status_user_can_toggle': True,
            # Other settings
            'always_show_welcome_screen': False,
            'default_theme': 'modern'
        })
        
        return jsonify({
            'status': 'success',
            'window_defaults': window_defaults
        }), 200
        
    except Exception as e:
        logger.error(f"Error retrieving window defaults: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route("/v1/admin/window-defaults", methods=["PUT"])
@require_admin
async def update_window_defaults():
    """
    Update default panel expansion states.
    
    Expects JSON body:
    {
        "session_history_expanded": bool,
        "resources_expanded": bool,
        "status_expanded": bool
    }
    
    Returns:
        200: Window defaults saved successfully
        400: Invalid data
        500: Error saving defaults
    """
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        
        data = await request.get_json()
        
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            }), 400
        
        config_manager = get_config_manager()
        config = config_manager.load_config()
        
        # Update window defaults
        window_defaults = {
            # Session History Panel
            'session_history_visible': bool(data.get('session_history_visible', True)),
            'session_history_default_mode': str(data.get('session_history_default_mode', 'collapsed')),
            'session_history_user_can_toggle': bool(data.get('session_history_user_can_toggle', True)),
            # Resources Panel
            'resources_visible': bool(data.get('resources_visible', True)),
            'resources_default_mode': str(data.get('resources_default_mode', 'collapsed')),
            'resources_user_can_toggle': bool(data.get('resources_user_can_toggle', True)),
            # Status Window
            'status_visible': bool(data.get('status_visible', True)),
            'status_default_mode': str(data.get('status_default_mode', 'collapsed')),
            'status_user_can_toggle': bool(data.get('status_user_can_toggle', True)),
            # Other settings
            'always_show_welcome_screen': bool(data.get('always_show_welcome_screen', False)),
            'default_theme': str(data.get('default_theme', 'modern'))
        }
        
        config['window_defaults'] = window_defaults
        config_manager.save_config(config)
        
        logger.info(f"Window defaults updated: {window_defaults}")
        
        return jsonify({
            'status': 'success',
            'message': 'Window defaults saved successfully',
            'window_defaults': window_defaults
        }), 200
        
    except Exception as e:
        logger.error(f"Error saving window defaults: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route("/v1/admin/clear-cache", methods=["POST"])
@require_admin
async def clear_cache():
    """
    Clear application cache.
    
    Returns:
        200: Cache cleared successfully
        500: Error clearing cache
    """
    try:
        from trusted_data_agent.core.config import APP_STATE
        
        # Clear any cached data in APP_STATE
        cache_keys = ['cached_prompts', 'cached_tools', 'cached_resources']
        for key in cache_keys:
            if key in APP_STATE:
                del APP_STATE[key]
        
        logger.info("Application cache cleared")
        
        return jsonify({
            'status': 'success',
            'message': 'Cache cleared successfully'
        }), 200
        
    except Exception as e:
        logger.error(f"Error clearing cache: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route("/v1/admin/reset-state", methods=["POST"])
@require_admin
async def reset_state():
    """
    Reset application state (requires reconnection).
    
    Returns:
        200: State reset successfully
        500: Error resetting state
    """
    try:
        from trusted_data_agent.core.config import APP_CONFIG, APP_STATE
        
        # Reset configuration flags
        APP_CONFIG.SERVICES_CONFIGURED = False
        APP_CONFIG.MCP_SERVER_CONNECTED = False
        APP_CONFIG.CURRENT_PROVIDER = None
        APP_CONFIG.CURRENT_MODEL = None
        
        # Clear APP_STATE
        keys_to_clear = ['llm', 'mcp_client', 'mcp_tools', 'mcp_prompts', 'mcp_resources',
                         'structured_tools', 'structured_prompts', 'structured_resources']
        for key in keys_to_clear:
            if key in APP_STATE:
                del APP_STATE[key]
        
        logger.warning("Application state reset - reconnection required")
        
        return jsonify({
            'status': 'success',
            'message': 'Application state reset successfully. Please reconnect services.'
        }), 200
        
    except Exception as e:
        logger.error(f"Error resetting state: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route('/v1/admin/app-config', methods=['GET'])
@require_admin
async def get_app_config():
    """Get current application configuration settings (feature availability)"""
    from trusted_data_agent.core.config import APP_CONFIG
    
    try:
        from trusted_data_agent.core.tts_service import get_tts_mode

        return jsonify({
            'status': 'success',
            'config': {
                'rag_enabled': APP_CONFIG.RAG_ENABLED,
                'voice_conversation_enabled': APP_CONFIG.VOICE_CONVERSATION_ENABLED,
                'tts_mode': get_tts_mode(),
                'charting_enabled': APP_CONFIG.CHARTING_ENABLED,
                'rag_config': {
                    'refresh_on_startup': APP_CONFIG.RAG_REFRESH_ON_STARTUP,
                    'num_examples': APP_CONFIG.RAG_NUM_EXAMPLES,
                    'embedding_model': APP_CONFIG.RAG_EMBEDDING_MODEL,
                    'autocomplete_min_relevance': APP_CONFIG.AUTOCOMPLETE_MIN_RELEVANCE
                },
                # PROP-006 A13 — deployment-wide combine isolation rules (sources whose
                # results must never be combined); an agent can add rules, never loosen these.
                'combine_isolation': [{'endpoints': sorted(r['sources']), 'reason': r['reason']}
                                      for r in _combine_rules_normalized()],
                # Agent evaluation — automatic deterministic scoring of every turn;
                # the LLM-judge layer is admin-controlled (costs tokens).
                'agent_eval': {
                    'online_enabled': APP_CONFIG.ONLINE_EVAL_ENABLED,
                    'include_judges': getattr(APP_CONFIG, 'ONLINE_EVAL_INCLUDE_JUDGES', False),
                    'grounding_enabled': getattr(APP_CONFIG, 'GROUNDING_GATE_ENABLED', False),
                    'guardrails_enabled': getattr(APP_CONFIG, 'GUARDRAILS_ENABLED', False),
                    'judge_sample_rate': getattr(APP_CONFIG, 'ONLINE_EVAL_JUDGE_SAMPLE_RATE', 0.05),
                    # the OBDA policy — "contracts only" per plane (off · observe · enforce)
                    'obda_policy': dict(getattr(APP_CONFIG, 'OBDA_POLICY', {}) or {}),
                    'default_thresholds': getattr(APP_CONFIG, 'ONLINE_EVAL_DEFAULT_THRESHOLDS', {}) or {},
                    # PROP-002b — per-station reporting levels from the ONE severity
                    # construct (normalized: every station present, danger floor implicit).
                    'station_reporting': _norm_reporting(
                        getattr(APP_CONFIG, 'ASSURANCE_STATION_REPORTING', None)),
                },
                # Knowledge-graph retrieval — how a question reaches entities in a
                # bound ontology. Lexical matching is always on; this governs the
                # second, semantic channel.
                'kg_config': {
                    'semantic_seeding': bool(getattr(APP_CONFIG, 'KG_SEMANTIC_SEEDING', True)),
                },
                # Behavioral Trust Score — integrity × live track record fusion.
                # Read-side only; `enabled` governs the UI surfaces (roster ring).
                'trust_behavior': dict(getattr(APP_CONFIG, 'TRUST_BEHAVIOR', {}) or {})
            }
        }), 200
    except Exception as e:
        logger.error(f"Error getting app config: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route('/v1/admin/app-config', methods=['POST'])
@require_admin
async def save_app_config():
    """Save application configuration settings (feature availability)"""
    from trusted_data_agent.core.config import APP_CONFIG

    try:
        data = await request.get_json()

        # Update APP_CONFIG with new feature availability settings
        if 'rag_enabled' in data:
            APP_CONFIG.RAG_ENABLED = bool(data['rag_enabled'])
            logger.info(f"RAG enabled: {APP_CONFIG.RAG_ENABLED}")

        # voice_conversation_enabled is now derived from tts_mode (managed via /v1/admin/tts-config)
        # Keep backward compat: if explicitly sent, ignore silently

        if 'charting_enabled' in data:
            APP_CONFIG.CHARTING_ENABLED = bool(data['charting_enabled'])
            logger.info(f"Charting enabled: {APP_CONFIG.CHARTING_ENABLED}")

        # Update RAG configuration
        if 'rag_config' in data:
            rag_config = data['rag_config']
            if 'refresh_on_startup' in rag_config:
                APP_CONFIG.RAG_REFRESH_ON_STARTUP = bool(rag_config['refresh_on_startup'])
                logger.info(f"RAG refresh on startup: {APP_CONFIG.RAG_REFRESH_ON_STARTUP}")
            if 'num_examples' in rag_config:
                APP_CONFIG.RAG_NUM_EXAMPLES = int(rag_config['num_examples'])
                logger.info(f"RAG num examples: {APP_CONFIG.RAG_NUM_EXAMPLES}")
            if 'embedding_model' in rag_config:
                APP_CONFIG.RAG_EMBEDDING_MODEL = str(rag_config['embedding_model'])
                logger.info(f"RAG embedding model: {APP_CONFIG.RAG_EMBEDDING_MODEL}")
            if 'autocomplete_min_relevance' in rag_config:
                val = float(rag_config['autocomplete_min_relevance'])
                APP_CONFIG.AUTOCOMPLETE_MIN_RELEVANCE = max(0.0, min(1.0, val))
                logger.info(f"Autocomplete min relevance: {APP_CONFIG.AUTOCOMPLETE_MIN_RELEVANCE}")

        # Knowledge-graph retrieval. Applied at runtime (the seeder reads APP_CONFIG on
        # every turn) and persisted so the choice survives a restart, like agent_eval.
        # Switching it OFF leaves any built index on disk: it is a cache of the graph,
        # not a record of the decision, and deleting it would make a re-enable pay the
        # full embedding cost again for no gain.
        if 'kg_config' in data and isinstance(data['kg_config'], dict):
            kc = data['kg_config']
            if 'semantic_seeding' in kc:
                APP_CONFIG.KG_SEMANTIC_SEEDING = bool(kc['semantic_seeding'])
                logger.info(f"KG semantic seeding: {APP_CONFIG.KG_SEMANTIC_SEEDING}")
            try:
                from trusted_data_agent.auth.database import set_system_setting
                import json as _json
                set_system_setting('kg_config', _json.dumps({
                    'semantic_seeding': bool(getattr(APP_CONFIG, 'KG_SEMANTIC_SEEDING', True)),
                }), 'Knowledge-graph retrieval settings (semantic entity matching)')
            except Exception as _e:
                logger.warning(f"Could not persist kg_config settings: {_e}")

        # Agent evaluation — admin-controlled at runtime (deterministic scoring is
        # always-on when enabled; the judge layer is the token-costing opt-in).
        if 'agent_eval' in data:
            ae = data['agent_eval']
            if 'online_enabled' in ae:
                APP_CONFIG.ONLINE_EVAL_ENABLED = bool(ae['online_enabled'])
                logger.info(f"Online eval enabled: {APP_CONFIG.ONLINE_EVAL_ENABLED}")
            if 'include_judges' in ae:
                APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES = bool(ae['include_judges'])
                logger.info(f"Online eval include judges: {APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES}")
            if 'grounding_enabled' in ae:
                APP_CONFIG.GROUNDING_GATE_ENABLED = bool(ae['grounding_enabled'])
                logger.info(f"Grounding gate enabled: {APP_CONFIG.GROUNDING_GATE_ENABLED}")
            if 'guardrails_enabled' in ae:
                APP_CONFIG.GUARDRAILS_ENABLED = bool(ae['guardrails_enabled'])
                logger.info(f"Guardrails enabled: {APP_CONFIG.GUARDRAILS_ENABLED}")
            # `obda_enabled` / `act_enabled` are no longer settings: the OBDA planes are
            # components, governed under Administration → Components (an older page may still
            # send the keys — they are dropped, never persisted)
            ae.pop('obda_enabled', None); ae.pop('act_enabled', None)
            if 'obda_policy' in ae:
                # the OBDA policy — normalised by the ONE rule (core/obda_planes.set_policy):
                # an unknown mode reads off, never enforce; live without a restart
                from trusted_data_agent.core import obda_planes as _planes
                _planes.set_policy(ae['obda_policy'])
                logger.info(f"OBDA policy: {APP_CONFIG.OBDA_POLICY}")
            if 'judge_sample_rate' in ae:
                APP_CONFIG.ONLINE_EVAL_JUDGE_SAMPLE_RATE = max(0.0, min(1.0, float(ae['judge_sample_rate'])))
                logger.info(f"Online eval judge sample rate: {APP_CONFIG.ONLINE_EVAL_JUDGE_SAMPLE_RATE}")
            if 'station_reporting' in ae:
                # PROP-002b — assignments are validated against the ONE construct
                # (unknown stations/levels fall back; danger floor is implicit and
                # not assignable). Presentation, never creation.
                APP_CONFIG.ASSURANCE_STATION_REPORTING = _norm_reporting(ae['station_reporting'])
                logger.info(f"Assurance station reporting: {APP_CONFIG.ASSURANCE_STATION_REPORTING}")
            if 'default_thresholds' in ae and isinstance(ae['default_thresholds'], dict):
                # MUST stay in sync with the slider metric list (thresholdSliders.js
                # METRICS). A key absent here is silently dropped on save — so any new
                # banded metric must be added to one of these sets or it can never be
                # persisted from the admin UI.
                _int_keys = {'max_steps', 'max_tokens', 'max_self_corrections', 'max_tool_repeats',
                             'max_latency_ms', 'max_tasks', 'max_tool_calls', 'max_out_of_scope_dbs',
                             'max_knowledge_age_days',
                             # boolean expectation flags ride as int 0/1 in the danger slot
                             # (assertion flattening makes them truthy for the scorer)
                             'require_citations'}
                _float_keys = {'max_cost_usd', 'min_tool_success_rate', 'min_numeric_fidelity',
                               'min_grounded', 'min_retrieval_relevance', 'min_parallel_efficiency',
                               'min_governed_data_ratio', 'min_context_utilization',
                               'min_citation_fidelity'}

                def _coerce(key, raw):
                    if raw in (None, ''):
                        return None
                    try:
                        return int(raw) if key in _int_keys else max(0.0, float(raw))
                    except (TypeError, ValueError):
                        return None

                cleaned = {}
                for k, v in ae['default_thresholds'].items():
                    if k not in _int_keys and k not in _float_keys:
                        continue
                    # 3-band shape {healthy, danger}; legacy scalar = Danger gate only.
                    if isinstance(v, dict):
                        danger = _coerce(k, v.get('danger'))
                        healthy = _coerce(k, v.get('healthy'))
                        band = {}
                        if danger is not None:
                            band['danger'] = danger
                        if healthy is not None:
                            band['healthy'] = healthy
                        if band:
                            cleaned[k] = band
                    else:
                        c = _coerce(k, v)
                        if c is not None:
                            cleaned[k] = {'danger': c}
                APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS = cleaned
                logger.info(f"Online eval default thresholds: {cleaned}")

            # Persist the whole agent_eval block so it survives a restart (loaded by
            # _sync_agent_eval_to_config at startup). Built from APP_CONFIG so it
            # reflects exactly the in-memory state just applied above.
            try:
                from trusted_data_agent.auth.database import set_system_setting
                import json as _json
                set_system_setting('agent_eval', _json.dumps({
                    'online_enabled': APP_CONFIG.ONLINE_EVAL_ENABLED,
                    'include_judges': APP_CONFIG.ONLINE_EVAL_INCLUDE_JUDGES,
                    'grounding_enabled': getattr(APP_CONFIG, 'GROUNDING_GATE_ENABLED', False),
                    'guardrails_enabled': getattr(APP_CONFIG, 'GUARDRAILS_ENABLED', False),
                    'judge_sample_rate': APP_CONFIG.ONLINE_EVAL_JUDGE_SAMPLE_RATE,
                    'obda_policy': dict(getattr(APP_CONFIG, 'OBDA_POLICY', {}) or {}),
                    'default_thresholds': APP_CONFIG.ONLINE_EVAL_DEFAULT_THRESHOLDS,
                    'station_reporting': _norm_reporting(
                        getattr(APP_CONFIG, 'ASSURANCE_STATION_REPORTING', None)),
                }), 'Agent evaluation settings (online scoring, judges, grounding gate, guardrails, banded thresholds, station reporting levels)')
            except Exception as _e:
                logger.warning(f"Could not persist agent_eval settings: {_e}")

        # PROP-006 A13 — deployment-wide combine isolation rules. Validated through the ONE
        # normalizer the combine step itself reads (a malformed rule restricts nothing and is
        # dropped); persisted so the rules survive a restart (_sync_combine_isolation_to_config).
        if 'combine_isolation' in data and isinstance(data['combine_isolation'], list):
            rules = [{'endpoints': sorted(r['sources']), 'reason': r['reason']}
                     for r in _combine_rules_normalized(data['combine_isolation'])]
            APP_CONFIG.COMBINE_ISOLATION = rules
            logger.info(f"Combine isolation rules: {len(rules)} rule(s)")
            try:
                from trusted_data_agent.auth.database import set_system_setting
                import json as _json
                set_system_setting('combine_isolation', _json.dumps(rules),
                                   'Sources whose results must never be combined (PROP-006 A13)')
            except Exception as _e:
                logger.warning(f"Could not persist combine isolation rules: {_e}")

        # Behavioral Trust Score — admin-tunable fusion parameters. Read-side
        # observability only (never gates a turn); `enabled` governs whether the
        # UI surfaces (roster ring, badges) render it. Persisted like agent_eval
        # so a tuned block survives restart (_sync_trust_behavior_to_config).
        if 'trust_behavior' in data and isinstance(data['trust_behavior'], dict):
            tb_in = data['trust_behavior']
            tb = dict(getattr(APP_CONFIG, 'TRUST_BEHAVIOR', {}) or {})
            _tb_bool = {'enabled'}
            _tb_int = {'window_turns', 'min_turns_proven', 'min_turns_trusted'}
            _tb_float = {'healthy_floor', 'trusted_floor', 'at_risk_floor',
                         'half_life_days', 'stale_days'}
            _tb_enum = {'breaker_mode': {'off', 'notify', 'freeze_autonomy'}}
            for k, v in tb_in.items():
                try:
                    if k in _tb_bool:
                        tb[k] = bool(v)
                    elif k in _tb_int:
                        tb[k] = max(0, int(v))
                    elif k in _tb_float:
                        tb[k] = max(0.0, float(v))
                    elif k in _tb_enum and str(v).lower() in _tb_enum[k]:
                        tb[k] = str(v).lower()
                except (TypeError, ValueError):
                    continue
            APP_CONFIG.TRUST_BEHAVIOR = tb
            logger.info(f"Trust behavior settings: {tb}")
            try:
                from trusted_data_agent.auth.database import set_system_setting
                import json as _json
                set_system_setting('trust_behavior', _json.dumps(tb),
                                   'Behavioral Trust Score settings (integrity x track-record fusion)')
            except Exception as _e:
                logger.warning(f"Could not persist trust_behavior settings: {_e}")

        from trusted_data_agent.core.tts_service import get_tts_mode

        return jsonify({
            'status': 'success',
            'message': 'Application configuration updated successfully',
            'config': {
                'rag_enabled': APP_CONFIG.RAG_ENABLED,
                'voice_conversation_enabled': APP_CONFIG.VOICE_CONVERSATION_ENABLED,
                'tts_mode': get_tts_mode(),
                'charting_enabled': APP_CONFIG.CHARTING_ENABLED,
                'rag_config': {
                    'refresh_on_startup': APP_CONFIG.RAG_REFRESH_ON_STARTUP,
                    'num_examples': APP_CONFIG.RAG_NUM_EXAMPLES,
                    'embedding_model': APP_CONFIG.RAG_EMBEDDING_MODEL,
                    'autocomplete_min_relevance': APP_CONFIG.AUTOCOMPLETE_MIN_RELEVANCE
                },
                'agent_eval': {
                    'online_enabled': APP_CONFIG.ONLINE_EVAL_ENABLED,
                    'include_judges': getattr(APP_CONFIG, 'ONLINE_EVAL_INCLUDE_JUDGES', False),
                    'judge_sample_rate': getattr(APP_CONFIG, 'ONLINE_EVAL_JUDGE_SAMPLE_RATE', 0.05),
                    'obda_policy': dict(getattr(APP_CONFIG, 'OBDA_POLICY', {}) or {}),
                    'default_thresholds': getattr(APP_CONFIG, 'ONLINE_EVAL_DEFAULT_THRESHOLDS', {}) or {}
                }
            }
        }), 200

    except Exception as e:
        logger.error(f"Error saving app config: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route('/v1/admin/system-restart/notify', methods=['POST'])
@require_admin
async def notify_system_restart():
    """Broadcast a maintenance banner to all connected users."""
    from trusted_data_agent.core.config import APP_STATE
    try:
        import time as _time
        data = await request.get_json()
        countdown_minutes = max(0, int(data.get("countdown_minutes", 0)))
        payload = {
            "message": data.get("message", "System restart scheduled for maintenance. Please save your work."),
            "countdown_minutes": countdown_minutes,
            "downtime_minutes": max(0, int(data.get("downtime_minutes", 0))),
            "_started_at": _time.time() if countdown_minutes > 0 else None,
        }
        APP_STATE["maintenance_banner"] = payload
        # Strip internal field before broadcasting to already-connected clients
        broadcast_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        notification = {"type": "maintenance_banner", "payload": broadcast_payload}
        all_queues = [q for uq in APP_STATE.get("notification_queues", {}).values() for q in uq]
        for q in all_queues:
            asyncio.create_task(q.put(notification))
        logger.info(f"Maintenance banner broadcast to {len(all_queues)} connected client(s).")
        return jsonify({"status": "success", "notified": len(all_queues)}), 200
    except Exception as e:
        logger.error(f"Error broadcasting maintenance banner: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route('/v1/admin/system-restart/cancel', methods=['POST'])
@require_admin
async def cancel_system_restart_notification():
    """Cancel the active maintenance banner for all connected users."""
    from trusted_data_agent.core.config import APP_STATE
    try:
        APP_STATE.pop("maintenance_banner", None)
        notification = {"type": "maintenance_banner_cancel"}
        all_queues = [q for uq in APP_STATE.get("notification_queues", {}).values() for q in uq]
        for q in all_queues:
            asyncio.create_task(q.put(notification))
        logger.info(f"Maintenance banner cancelled for {len(all_queues)} connected client(s).")
        return jsonify({"status": "success", "notified": len(all_queues)}), 200
    except Exception as e:
        logger.error(f"Error cancelling maintenance banner: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@admin_api_bp.route('/v1/admin/tts-config', methods=['GET'])
@require_admin
async def get_tts_config():
    """Get TTS configuration: mode, whether global credentials are set, and project hint."""
    from trusted_data_agent.core.tts_service import (
        get_tts_mode, has_global_tts_credentials, get_global_credentials_project_id
    )

    try:
        mode = get_tts_mode()
        has_creds = has_global_tts_credentials()
        project_id = get_global_credentials_project_id() if has_creds else None

        return jsonify({
            'status': 'success',
            'tts_mode': mode,
            'has_global_credentials': has_creds,
            'project_id': project_id
        }), 200

    except Exception as e:
        logger.error(f"Error getting TTS config: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/tts-config', methods=['POST'])
@require_admin
async def save_tts_config():
    """Save TTS configuration: mode and optionally global credentials."""
    from trusted_data_agent.core.tts_service import (
        set_tts_mode, save_global_tts_credentials, delete_global_tts_credentials,
        get_tts_mode, has_global_tts_credentials, get_global_credentials_project_id
    )

    try:
        data = await request.get_json()

        # Update TTS mode
        new_mode = data.get('tts_mode')
        if new_mode:
            if not set_tts_mode(new_mode):
                return jsonify({'status': 'error', 'message': f'Invalid TTS mode: {new_mode}'}), 400
            logger.info(f"Admin updated TTS mode to: {new_mode}")

        # Handle global credentials
        global_creds = data.get('global_credentials_json', '').strip()
        if global_creds:
            if not save_global_tts_credentials(global_creds):
                return jsonify({'status': 'error', 'message': 'Failed to save global TTS credentials. Check JSON format.'}), 400
            logger.info("Admin saved global TTS credentials")

        # Handle credential deletion
        if data.get('delete_global_credentials'):
            delete_global_tts_credentials()
            logger.info("Admin deleted global TTS credentials")

        mode = get_tts_mode()
        has_creds = has_global_tts_credentials()
        project_id = get_global_credentials_project_id() if has_creds else None

        return jsonify({
            'status': 'success',
            'message': 'TTS configuration updated successfully',
            'tts_mode': mode,
            'has_global_credentials': has_creds,
            'project_id': project_id
        }), 200

    except Exception as e:
        logger.error(f"Error saving TTS config: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/tts-config/test', methods=['POST'])
@require_admin
async def test_admin_tts_credentials():
    """Test TTS credentials without saving them."""
    from trusted_data_agent.core.tts_service import test_tts_credentials

    try:
        data = await request.get_json()
        credentials_json = data.get('credentials_json', '').strip()

        if not credentials_json:
            return jsonify({'status': 'error', 'message': 'No credentials provided'}), 400

        result = test_tts_credentials(credentials_json)

        if result['success']:
            return jsonify({'status': 'success', 'message': 'TTS credentials are valid'}), 200
        else:
            return jsonify({'status': 'error', 'message': result['error']}), 400

    except Exception as e:
        logger.error(f"Error testing TTS credentials: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/knowledge-config', methods=['GET'])
@require_admin
async def get_knowledge_config():
    """Get current knowledge repository configuration settings"""
    from trusted_data_agent.core.config import APP_CONFIG
    
    try:
        return jsonify({
            'status': 'success',
            'config': {
                'enabled': APP_CONFIG.KNOWLEDGE_RAG_ENABLED,
                'num_docs': APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS,
                'min_relevance_score': APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE,
                'max_tokens': APP_CONFIG.KNOWLEDGE_MAX_TOKENS,
                'reranking_enabled': APP_CONFIG.KNOWLEDGE_RERANKING_ENABLED,
                'chunk_size': APP_CONFIG.KNOWLEDGE_CHUNK_SIZE,
                'chunk_overlap': APP_CONFIG.KNOWLEDGE_CHUNK_OVERLAP
            }
        }), 200
    except Exception as e:
        logger.error(f"Error getting knowledge config: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route('/v1/admin/knowledge-config', methods=['POST'])
@require_admin
async def save_knowledge_config():
    """Save knowledge repository configuration settings"""
    from trusted_data_agent.core.config import APP_CONFIG
    
    try:
        data = await request.get_json()
        
        # Update APP_CONFIG with new knowledge repository settings
        if 'enabled' in data:
            APP_CONFIG.KNOWLEDGE_RAG_ENABLED = bool(data['enabled'])
            logger.info(f"Knowledge RAG enabled: {APP_CONFIG.KNOWLEDGE_RAG_ENABLED}")
        
        if 'num_docs' in data:
            APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS = int(data['num_docs'])
            logger.info(f"Knowledge num docs: {APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS}")
        
        if 'min_relevance_score' in data:
            APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE = float(data['min_relevance_score'])
            logger.info(f"Knowledge min relevance score: {APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE}")
        
        if 'max_tokens' in data:
            APP_CONFIG.KNOWLEDGE_MAX_TOKENS = int(data['max_tokens'])
            logger.info(f"Knowledge max tokens: {APP_CONFIG.KNOWLEDGE_MAX_TOKENS}")
        
        if 'reranking_enabled' in data:
            APP_CONFIG.KNOWLEDGE_RERANKING_ENABLED = bool(data['reranking_enabled'])
            logger.info(f"Knowledge reranking enabled: {APP_CONFIG.KNOWLEDGE_RERANKING_ENABLED}")

        if 'chunk_size' in data:
            APP_CONFIG.KNOWLEDGE_CHUNK_SIZE = int(data['chunk_size'])
            logger.info(f"Knowledge chunk size: {APP_CONFIG.KNOWLEDGE_CHUNK_SIZE}")

        if 'chunk_overlap' in data:
            APP_CONFIG.KNOWLEDGE_CHUNK_OVERLAP = int(data['chunk_overlap'])
            logger.info(f"Knowledge chunk overlap: {APP_CONFIG.KNOWLEDGE_CHUNK_OVERLAP}")

        return jsonify({
            'status': 'success',
            'message': 'Knowledge repository configuration updated successfully',
            'config': {
                'enabled': APP_CONFIG.KNOWLEDGE_RAG_ENABLED,
                'num_docs': APP_CONFIG.KNOWLEDGE_RAG_NUM_DOCS,
                'min_relevance_score': APP_CONFIG.KNOWLEDGE_MIN_RELEVANCE_SCORE,
                'max_tokens': APP_CONFIG.KNOWLEDGE_MAX_TOKENS,
                'reranking_enabled': APP_CONFIG.KNOWLEDGE_RERANKING_ENABLED,
                'chunk_size': APP_CONFIG.KNOWLEDGE_CHUNK_SIZE,
                'chunk_overlap': APP_CONFIG.KNOWLEDGE_CHUNK_OVERLAP
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Error saving knowledge config: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ============================================================================
# Genie Global Settings Endpoints
# ============================================================================

@admin_api_bp.route('/v1/admin/genie-settings', methods=['GET'])
@require_admin
async def get_genie_global_settings():
    """
    Get global Genie coordination settings.

    Returns all settings with their values and lock status.
    """
    from trusted_data_agent.core.config_manager import get_config_manager

    try:
        config_manager = get_config_manager()
        settings = config_manager.get_genie_global_settings()

        return jsonify({
            'status': 'success',
            'settings': settings
        }), 200

    except Exception as e:
        logger.error(f"Error getting genie settings: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route('/v1/admin/genie-settings', methods=['PUT'])
@require_admin
async def save_genie_global_settings():
    """
    Save global Genie coordination settings.

    Expected payload:
    {
        "temperature": {"value": 0.7, "is_locked": false},
        "queryTimeout": {"value": 300, "is_locked": true},
        "maxIterations": {"value": 10, "is_locked": false},
        "maxNestingDepth": {"value": 3, "is_locked": false}
    }
    """
    from trusted_data_agent.core.config_manager import get_config_manager
    from quart import g

    try:
        data = await request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No settings provided'
            }), 400

        # Validate settings
        valid_keys = {'temperature', 'queryTimeout', 'maxIterations', 'maxNestingDepth'}
        for key in data.keys():
            if key not in valid_keys:
                return jsonify({
                    'status': 'error',
                    'message': f'Invalid setting key: {key}'
                }), 400

        # Validate value ranges
        if 'temperature' in data:
            temp = data['temperature'].get('value')
            if temp is not None and (temp < 0.0 or temp > 1.0):
                return jsonify({
                    'status': 'error',
                    'message': 'Temperature must be between 0.0 and 1.0'
                }), 400

        if 'queryTimeout' in data:
            timeout = data['queryTimeout'].get('value')
            if timeout is not None and (timeout < 60 or timeout > 900):
                return jsonify({
                    'status': 'error',
                    'message': 'Query timeout must be between 60 and 900 seconds'
                }), 400

        if 'maxIterations' in data:
            max_iter = data['maxIterations'].get('value')
            if max_iter is not None and (max_iter < 1 or max_iter > 25):
                return jsonify({
                    'status': 'error',
                    'message': 'Max iterations must be between 1 and 25'
                }), 400

        if 'maxNestingDepth' in data:
            max_depth = data['maxNestingDepth'].get('value')
            if max_depth is not None and (max_depth < 1 or max_depth > 10):
                return jsonify({
                    'status': 'error',
                    'message': 'Max nesting depth must be between 1 and 10'
                }), 400

        config_manager = get_config_manager()
        user_uuid = getattr(g, 'user_uuid', None)

        success = config_manager.save_genie_global_settings(data, user_uuid)

        if success:
            # Return updated settings
            updated_settings = config_manager.get_genie_global_settings()
            return jsonify({
                'status': 'success',
                'message': 'Genie settings saved successfully',
                'settings': updated_settings
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to save genie settings'
            }), 500

    except Exception as e:
        logger.error(f"Error saving genie settings: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ============================================================================
# Knowledge Global Settings Endpoints
# ============================================================================

@admin_api_bp.route('/v1/admin/knowledge-global-settings', methods=['GET'])
@require_admin
async def get_knowledge_global_settings():
    """
    Get global Knowledge repository settings.

    Returns all settings with their values and lock status.
    """
    from trusted_data_agent.core.config_manager import get_config_manager

    try:
        config_manager = get_config_manager()
        settings = config_manager.get_knowledge_global_settings()

        return jsonify({
            'status': 'success',
            'settings': settings
        }), 200

    except Exception as e:
        logger.error(f"Error getting knowledge global settings: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@admin_api_bp.route('/v1/admin/knowledge-global-settings', methods=['PUT'])
@require_admin
async def save_knowledge_global_settings():
    """
    Save global Knowledge repository settings.

    Expected payload:
    {
        "minRelevanceScore": {"value": 0.30, "is_locked": false},
        "maxDocs": {"value": 3, "is_locked": true},
        "maxTokens": {"value": 2000, "is_locked": false},
        "rerankingEnabled": {"value": false, "is_locked": false}
    }
    """
    from trusted_data_agent.core.config_manager import get_config_manager
    from quart import g

    try:
        data = await request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No settings provided'
            }), 400

        # Validate settings
        valid_keys = {'minRelevanceScore', 'maxDocs', 'maxTokens', 'rerankingEnabled',
                      'maxChunksPerDocument', 'freshnessWeight', 'freshnessDecayRate',
                      'synthesisPromptOverride'}
        for key in data.keys():
            if key not in valid_keys:
                return jsonify({
                    'status': 'error',
                    'message': f'Invalid setting key: {key}'
                }), 400

        # Validate value ranges
        if 'minRelevanceScore' in data:
            score = data['minRelevanceScore'].get('value')
            if score is not None and (score < 0.0 or score > 1.0):
                return jsonify({
                    'status': 'error',
                    'message': 'Min relevance score must be between 0.0 and 1.0'
                }), 400

        if 'maxDocs' in data:
            max_docs = data['maxDocs'].get('value')
            if max_docs is not None and (max_docs < 1 or max_docs > 20):
                return jsonify({
                    'status': 'error',
                    'message': 'Max documents must be between 1 and 20'
                }), 400

        if 'maxTokens' in data:
            max_tokens = data['maxTokens'].get('value')
            if max_tokens is not None and (max_tokens < 500 or max_tokens > 10000):
                return jsonify({
                    'status': 'error',
                    'message': 'Max tokens must be between 500 and 10000'
                }), 400

        if 'maxChunksPerDocument' in data:
            max_chunks = data['maxChunksPerDocument'].get('value')
            if max_chunks is not None and (max_chunks < 0 or max_chunks > 50):
                return jsonify({
                    'status': 'error',
                    'message': 'Max chunks per document must be between 0 and 50'
                }), 400

        if 'freshnessWeight' in data:
            fw = data['freshnessWeight'].get('value')
            if fw is not None and (fw < 0.0 or fw > 1.0):
                return jsonify({
                    'status': 'error',
                    'message': 'Freshness weight must be between 0.0 and 1.0'
                }), 400

        if 'freshnessDecayRate' in data:
            fdr = data['freshnessDecayRate'].get('value')
            if fdr is not None and (fdr < 0.001 or fdr > 1.0):
                return jsonify({
                    'status': 'error',
                    'message': 'Freshness decay rate must be between 0.001 and 1.0'
                }), 400

        config_manager = get_config_manager()
        user_uuid = getattr(g, 'user_uuid', None)

        success = config_manager.save_knowledge_global_settings(data, user_uuid)

        if success:
            # Return updated settings
            updated_settings = config_manager.get_knowledge_global_settings()
            return jsonify({
                'status': 'success',
                'message': 'Knowledge settings saved successfully',
                'settings': updated_settings
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to save knowledge settings'
            }), 500

    except Exception as e:
        logger.error(f"Error saving knowledge global settings: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# ==============================================================================
# RAG OFFLOAD SETTINGS ENDPOINTS
# ==============================================================================

@admin_api_bp.route('/v1/admin/rag-offload-settings', methods=['GET'])
@require_admin
async def get_rag_offload_settings():
    """Get the platform-level RAG offload condensation policy."""
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()
        settings = config_manager.get_rag_offload_settings()
        return jsonify({'status': 'success', 'settings': settings}), 200
    except Exception as e:
        logger.error(f"Error loading RAG offload settings: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/rag-offload-settings', methods=['PUT'])
@require_admin
async def save_rag_offload_settings():
    """Save the platform-level RAG offload condensation policy."""
    try:
        data = await request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()
        user_uuid = getattr(g, 'user_uuid', None)

        backend_policy = data.get('backend_policy', 'allow_internal')
        default_max_store_mb = int(data.get('default_max_store_mb', 10))

        success = config_manager.save_rag_offload_settings(
            backend_policy=backend_policy,
            default_max_store_mb=default_max_store_mb,
            user_uuid=user_uuid,
        )

        if success:
            updated = config_manager.get_rag_offload_settings()
            return jsonify({
                'status': 'success',
                'message': 'RAG offload settings saved successfully',
                'settings': updated,
            }), 200
        else:
            return jsonify({'status': 'error', 'message': 'Failed to save RAG offload settings'}), 500

    except Exception as e:
        logger.error(f"Error saving RAG offload settings: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ==============================================================================
# PROMPT CACHE MANAGEMENT ENDPOINT
# ==============================================================================

@admin_api_bp.route("/v1/admin/prompts/clear-cache", methods=["POST"])
@require_admin
async def clear_prompt_cache():
    """
    Clear the PromptLoader cache (admin only).

    This endpoint allows external scripts (like update_prompt.py) to invalidate
    the in-memory prompt cache after database updates.

    Returns:
    {
        "success": true,
        "message": "Prompt cache cleared successfully"
    }
    """
    try:
        from trusted_data_agent.agent.prompt_loader import PromptLoader

        loader = PromptLoader()
        loader.clear_cache()

        logger.info("PromptLoader cache cleared by admin request")

        return jsonify({
            'success': True,
            'message': 'Prompt cache cleared successfully'
        }), 200
    except Exception as e:
        logger.error(f"Failed to clear prompt cache: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==============================================================================
# EXTENSION GOVERNANCE SETTINGS
# ==============================================================================

@admin_api_bp.route('/v1/admin/extension-settings', methods=['GET'])
@require_admin
async def get_extension_settings_endpoint():
    """
    Get current extension governance settings plus the list of built-in extensions.

    Returns:
    {
        "status": "success",
        "settings": { ... },
        "builtin_extensions": [ { extension_id, display_name, category, extension_tier }, ... ]
    }
    """
    try:
        from trusted_data_agent.extensions.settings import get_extension_settings
        from trusted_data_agent.extensions.manager import get_extension_manager

        settings = get_extension_settings()
        manager = get_extension_manager()

        # Provide the list of built-in extensions so the admin UI can render checkboxes
        builtin = [
            {
                "extension_id": e["extension_id"],
                "display_name": e["display_name"],
                "category": e.get("category", "General"),
                "extension_tier": e.get("extension_tier", "standard"),
            }
            for e in manager.list_extensions()
            if e.get("is_builtin")
        ]

        return jsonify({
            'status': 'success',
            'settings': settings,
            'builtin_extensions': builtin,
        }), 200

    except Exception as e:
        logger.error(f"Error getting extension settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/extension-settings', methods=['POST'])
@require_admin
async def save_extension_settings_endpoint():
    """
    Save extension governance settings (admin only).

    Body (all fields optional — only supplied keys are updated):
    {
        "extensions_mode": "all" | "selective",
        "disabled_extensions": ["classify", "summary"],
        "user_extensions_enabled": true | false,
        "user_extensions_marketplace_enabled": true | false
    }
    """
    try:
        from trusted_data_agent.extensions.settings import save_extension_settings, get_extension_settings

        data = await request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # Validate extensions_mode
        if 'extensions_mode' in data and data['extensions_mode'] not in ('all', 'selective'):
            return jsonify({'status': 'error', 'message': "extensions_mode must be 'all' or 'selective'"}), 400

        # Validate disabled_extensions is a list
        if 'disabled_extensions' in data and not isinstance(data['disabled_extensions'], list):
            return jsonify({'status': 'error', 'message': 'disabled_extensions must be a list'}), 400

        admin_user = get_current_user_from_request()
        admin_uuid = admin_user.id if admin_user else 'unknown'

        save_extension_settings(data, admin_uuid)

        return jsonify({
            'status': 'success',
            'message': 'Extension settings updated successfully',
            'settings': get_extension_settings(),
        }), 200

    except Exception as e:
        logger.error(f"Error saving extension settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Skill Governance Settings (mirrors extension settings above)
# ---------------------------------------------------------------------------

@admin_api_bp.route('/v1/admin/skill-settings', methods=['GET'])
@require_admin
async def get_skill_settings_endpoint():
    """
    Get current skill governance settings plus the list of built-in skills.

    Returns:
    {
        "status": "success",
        "settings": { ... },
        "builtin_skills": [ { skill_id, name, description, tags }, ... ]
    }
    """
    try:
        from trusted_data_agent.skills.settings import get_skill_settings
        from trusted_data_agent.skills.manager import get_skill_manager

        settings = get_skill_settings()
        manager = get_skill_manager()

        # Provide the list of built-in skills so the admin UI can render checkboxes
        builtin = [
            {
                "skill_id": s["skill_id"],
                "name": s.get("name", s["skill_id"]),
                "description": s.get("description", ""),
                "tags": s.get("tags", []),
            }
            for s in manager.list_skills()
            if s.get("is_builtin")
        ]

        return jsonify({
            'status': 'success',
            'settings': settings,
            'builtin_skills': builtin,
        }), 200

    except Exception as e:
        logger.error(f"Error getting skill settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/skill-settings', methods=['POST'])
@require_admin
async def save_skill_settings_endpoint():
    """
    Save skill governance settings (admin only).

    Body (all fields optional — only supplied keys are updated):
    {
        "skills_mode": "all" | "selective",
        "disabled_skills": ["classify-intent"],
        "user_skills_enabled": true | false,
        "auto_skills_enabled": true | false
    }
    """
    try:
        from trusted_data_agent.skills.settings import save_skill_settings, get_skill_settings

        data = await request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        # Validate skills_mode
        if 'skills_mode' in data and data['skills_mode'] not in ('all', 'selective'):
            return jsonify({'status': 'error', 'message': "skills_mode must be 'all' or 'selective'"}), 400

        # Validate disabled_skills is a list
        if 'disabled_skills' in data and not isinstance(data['disabled_skills'], list):
            return jsonify({'status': 'error', 'message': 'disabled_skills must be a list'}), 400

        # Validate marketplace setting
        if 'user_skills_marketplace_enabled' in data and not isinstance(data['user_skills_marketplace_enabled'], bool):
            return jsonify({'status': 'error', 'message': 'user_skills_marketplace_enabled must be boolean'}), 400

        admin_user = get_current_user_from_request()
        admin_uuid = admin_user.id if admin_user else 'unknown'

        save_skill_settings(data, admin_uuid)

        return jsonify({
            'status': 'success',
            'message': 'Skill settings updated successfully',
            'settings': get_skill_settings(),
        }), 200

    except Exception as e:
        logger.error(f"Error saving skill settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Component Governance Settings (mirrors extension/skill settings above)
# ---------------------------------------------------------------------------

@admin_api_bp.route('/v1/admin/component-settings', methods=['GET'])
@require_admin
async def get_component_settings_endpoint():
    """
    Get current component governance settings plus the list of built-in components.

    Returns:
    {
        "status": "success",
        "settings": { ... },
        "builtin_components": [ { component_id, display_name, category, component_type }, ... ]
    }
    """
    try:
        from trusted_data_agent.components.settings import get_component_settings
        from trusted_data_agent.components.manager import get_component_manager

        settings = get_component_settings()
        manager = get_component_manager()

        builtin = [
            {
                "component_id": c.component_id,
                "display_name": c.display_name,
                "category": c.category,
                "component_type": c.component_type,
            }
            for c in manager.get_builtin_components()
        ]

        return jsonify({
            'status': 'success',
            'settings': settings,
            'builtin_components': builtin,
        }), 200

    except Exception as e:
        logger.error(f"Error getting component settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/component-settings', methods=['POST'])
@require_admin
async def save_component_settings_endpoint():
    """
    Save component governance settings (admin only).

    Body (all fields optional — only supplied keys are updated):
    {
        "components_mode": "all" | "selective",
        "disabled_components": ["audio_tts", "video"],
        "user_components_enabled": true | false,
        "user_components_marketplace_enabled": true | false
    }
    """
    try:
        from trusted_data_agent.components.settings import save_component_settings, get_component_settings

        data = await request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        if 'components_mode' in data and data['components_mode'] not in ('all', 'selective'):
            return jsonify({'status': 'error', 'message': "components_mode must be 'all' or 'selective'"}), 400

        if 'disabled_components' in data and not isinstance(data['disabled_components'], list):
            return jsonify({'status': 'error', 'message': 'disabled_components must be a list'}), 400

        admin_user = get_current_user_from_request()
        admin_uuid = admin_user.id if admin_user else 'unknown'

        save_component_settings(data, admin_uuid)

        return jsonify({
            'status': 'success',
            'message': 'Component settings updated successfully',
            'settings': get_component_settings(),
        }), 200

    except Exception as e:
        logger.error(f"Error saving component settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Per-User Component Access Overrides
# ---------------------------------------------------------------------------

@admin_api_bp.route('/v1/admin/component-settings/users/<component_id>', methods=['GET'])
@require_admin
async def get_component_user_overrides_endpoint(component_id: str):
    """
    Return all users with their effective access to a component.
    Each entry shows: user_uuid, username, email, tier, access_type (global|override),
    is_enabled, note, updated_at, updated_by.
    """
    try:
        from trusted_data_agent.components.settings import get_all_users_with_component_access
        users = get_all_users_with_component_access(component_id)
        return jsonify({'status': 'success', 'component_id': component_id, 'users': users}), 200
    except Exception as e:
        logger.error(f"Error getting component user overrides for {component_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/component-settings/users/<user_uuid>/<component_id>', methods=['PUT'])
@require_admin
async def set_component_user_override_endpoint(user_uuid: str, component_id: str):
    """
    Create or update a per-user component override.
    Body: { "is_enabled": bool, "note": str (optional) }
    """
    try:
        from trusted_data_agent.components.settings import set_user_component_override
        data = await request.get_json() or {}
        is_enabled = bool(data.get('is_enabled', True))
        note = str(data.get('note', ''))
        admin_user = get_current_user_from_request()
        admin_uuid = str(admin_user.id) if admin_user else 'system'
        set_user_component_override(user_uuid, component_id, is_enabled, admin_uuid, note)
        return jsonify({'status': 'success', 'user_uuid': user_uuid, 'component_id': component_id,
                        'is_enabled': is_enabled}), 200
    except Exception as e:
        logger.error(f"Error setting component user override: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/component-settings/users/<user_uuid>/<component_id>', methods=['DELETE'])
@require_admin
async def delete_component_user_override_endpoint(user_uuid: str, component_id: str):
    """Remove a per-user override (user reverts to global default)."""
    try:
        from trusted_data_agent.components.settings import delete_user_component_override
        delete_user_component_override(user_uuid, component_id)
        return jsonify({'status': 'success', 'user_uuid': user_uuid, 'component_id': component_id}), 200
    except Exception as e:
        logger.error(f"Error deleting component user override: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Scheduler Admin: Profile Jobs + Platform Jobs
# ---------------------------------------------------------------------------

@admin_api_bp.route('/v1/admin/scheduler/profile-jobs/enable', methods=['POST'])
@require_admin
async def enable_profile_scheduler_endpoint():
    """Globally enable user-created profile jobs."""
    try:
        from trusted_data_agent.core.task_scheduler import enable_profile_scheduler
        await enable_profile_scheduler()
        return jsonify({'status': 'success', 'profile_scheduler_enabled': True}), 200
    except Exception as e:
        logger.error(f"Error enabling profile scheduler: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/scheduler/profile-jobs/disable', methods=['POST'])
@require_admin
async def disable_profile_scheduler_endpoint():
    """Globally disable user-created profile jobs."""
    try:
        from trusted_data_agent.core.task_scheduler import disable_profile_scheduler
        await disable_profile_scheduler()
        return jsonify({'status': 'success', 'profile_scheduler_enabled': False}), 200
    except Exception as e:
        logger.error(f"Error disabling profile scheduler: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/scheduler/platform-jobs/enable', methods=['POST'])
@require_admin
async def enable_platform_scheduler_endpoint():
    """Globally enable platform maintenance jobs."""
    try:
        from trusted_data_agent.core.task_scheduler import enable_platform_scheduler
        await enable_platform_scheduler()
        return jsonify({'status': 'success', 'platform_scheduler_enabled': True}), 200
    except Exception as e:
        logger.error(f"Error enabling platform scheduler: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/scheduler/platform-jobs/disable', methods=['POST'])
@require_admin
async def disable_platform_scheduler_endpoint():
    """Globally disable platform maintenance jobs."""
    try:
        from trusted_data_agent.core.task_scheduler import disable_platform_scheduler
        await disable_platform_scheduler()
        return jsonify({'status': 'success', 'platform_scheduler_enabled': False}), 200
    except Exception as e:
        logger.error(f"Error disabling platform scheduler: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/scheduler/platform-jobs', methods=['GET'])
@operation("admin.platform_jobs", effect="read", scope="instance", tier="admin",
           summary="The platform's own maintenance jobs (usage resets, price sync, backups): their schedules and how their last runs went",
           summary_fields=("id", "name", "schedule", "enabled", "last_run_at", "last_run_status"))
@require_admin
async def list_platform_jobs_endpoint():
    """Return all platform maintenance jobs with last/next run info."""
    try:
        from trusted_data_agent.core.task_scheduler import list_platform_jobs, get_scheduler
        from trusted_data_agent.components.settings import is_platform_scheduler_enabled
        jobs = list_platform_jobs()
        scheduler = get_scheduler()
        return jsonify({
            'status': 'success',
            'jobs': jobs,
            'platform_scheduler_enabled': is_platform_scheduler_enabled(),
            'apscheduler_running': bool(scheduler and scheduler.running),
        }), 200
    except Exception as e:
        logger.error(f"Error listing platform jobs: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/scheduler/platform-jobs/<task_id>', methods=['PUT'])
@require_admin
async def update_platform_job_endpoint(task_id: str):
    """Enable or disable a single platform maintenance job. Body: { "enabled": 0|1 }"""
    try:
        from trusted_data_agent.core.task_scheduler import update_platform_job
        data = await request.get_json() or {}
        task = update_platform_job(task_id, data)
        if not task:
            return jsonify({'status': 'error', 'message': 'Platform job not found'}), 404
        return jsonify({'status': 'success', 'job': task}), 200
    except Exception as e:
        logger.error(f"Error updating platform job {task_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/scheduler/platform-jobs/<task_id>/runs', methods=['GET'])
@require_admin
async def list_platform_job_runs_endpoint(task_id: str):
    """Return run history for a platform maintenance job."""
    try:
        from trusted_data_agent.core.task_scheduler import list_platform_job_runs
        runs = list_platform_job_runs(task_id)
        return jsonify({'status': 'success', 'task_id': task_id, 'runs': runs}), 200
    except Exception as e:
        logger.error(f"Error listing platform job runs for {task_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/scheduler/platform-jobs/<task_id>/run-now', methods=['POST'])
@require_admin
async def run_platform_job_now_endpoint(task_id: str):
    """Trigger immediate execution of a platform maintenance job."""
    try:
        from trusted_data_agent.core.task_scheduler import run_platform_job_now
        await run_platform_job_now(task_id)
        return jsonify({'status': 'success', 'message': f'Platform job {task_id} triggered'}), 200
    except Exception as e:
        logger.error(f"Error triggering platform job {task_id}: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Vector Store Governance Settings (per-tier backend availability)
# ---------------------------------------------------------------------------

@admin_api_bp.route('/v1/admin/vectorstore-settings', methods=['GET'])
@require_admin
async def get_vectorstore_settings_endpoint():
    """
    Get current vector store governance settings plus metadata.

    Returns:
    {
        "status": "success",
        "settings": {
            "allowed_backends_user": ["chromadb", "teradata", "qdrant"],
            "allowed_backends_developer": ["chromadb", "teradata", "qdrant"]
        },
        "available_backends": [
            {"backend_id": "chromadb", "display_name": "ChromaDB (Local)", "color": "#22c55e"},
            ...
        ]
    }
    """
    try:
        from trusted_data_agent.vectorstore.settings import get_vectorstore_settings

        settings = get_vectorstore_settings()

        available = [
            {"backend_id": "chromadb", "display_name": "ChromaDB (Local)", "color": "#22c55e"},
            {"backend_id": "teradata", "display_name": "Teradata (Enterprise)", "color": "#F15F22"},
            {"backend_id": "qdrant", "display_name": "Qdrant Cloud", "color": "#a78bfa"},
        ]

        return jsonify({
            'status': 'success',
            'settings': settings,
            'available_backends': available,
        }), 200

    except Exception as e:
        logger.error(f"Error getting vectorstore settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/vectorstore-settings', methods=['POST'])
@require_admin
async def save_vectorstore_settings_endpoint():
    """
    Save vector store governance settings (admin only).

    Body (all fields optional — only supplied keys are updated):
    {
        "allowed_backends_user": ["chromadb"],
        "allowed_backends_developer": ["chromadb", "teradata"]
    }

    Enforces cumulative constraint: anything not in developer list
    is automatically removed from user list.
    """
    try:
        from trusted_data_agent.vectorstore.settings import (
            save_vectorstore_settings, get_vectorstore_settings, ALL_BACKENDS
        )

        data = await request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        valid_set = set(ALL_BACKENDS)

        # Validate allowed_backends_developer
        if 'allowed_backends_developer' in data:
            if not isinstance(data['allowed_backends_developer'], list):
                return jsonify({'status': 'error', 'message': 'allowed_backends_developer must be a list'}), 400
            invalid = set(data['allowed_backends_developer']) - valid_set
            if invalid:
                return jsonify({'status': 'error', 'message': f'Invalid backends: {", ".join(invalid)}'}), 400

        # Validate allowed_backends_user
        if 'allowed_backends_user' in data:
            if not isinstance(data['allowed_backends_user'], list):
                return jsonify({'status': 'error', 'message': 'allowed_backends_user must be a list'}), 400
            invalid = set(data['allowed_backends_user']) - valid_set
            if invalid:
                return jsonify({'status': 'error', 'message': f'Invalid backends: {", ".join(invalid)}'}), 400

        # Enforce cumulative constraint: user list must be subset of developer list
        dev_list = data.get('allowed_backends_developer')
        user_list = data.get('allowed_backends_user')

        if dev_list is not None and user_list is not None:
            # Both provided — enforce subset
            data['allowed_backends_user'] = [b for b in user_list if b in dev_list]
        elif dev_list is not None and user_list is None:
            # Developer list changed — trim existing user list
            current = get_vectorstore_settings()
            current_user = current.get('allowed_backends_user', list(ALL_BACKENDS))
            data['allowed_backends_user'] = [b for b in current_user if b in dev_list]

        admin_user = get_current_user_from_request()
        admin_uuid = admin_user.id if admin_user else 'unknown'

        save_vectorstore_settings(data, admin_uuid)

        return jsonify({
            'status': 'success',
            'message': 'Vector store settings updated successfully',
            'settings': get_vectorstore_settings(),
        }), 200

    except Exception as e:
        logger.error(f"Error saving vectorstore settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Knowledge Graph Marketplace Governance Settings
# ---------------------------------------------------------------------------

@admin_api_bp.route('/v1/admin/kg-marketplace-settings', methods=['GET'])
@require_admin
async def get_kg_marketplace_settings_endpoint():
    """
    Get current knowledge graph marketplace governance settings.

    Returns:
    {
        "status": "success",
        "settings": { "kg_marketplace_enabled": true }
    }
    """
    try:
        from trusted_data_agent.kg.settings import get_kg_marketplace_settings

        settings = get_kg_marketplace_settings()

        return jsonify({
            'status': 'success',
            'settings': settings,
        }), 200

    except Exception as e:
        logger.error(f"Error getting KG marketplace settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route('/v1/admin/kg-marketplace-settings', methods=['POST'])
@require_admin
async def save_kg_marketplace_settings_endpoint():
    """
    Save knowledge graph marketplace governance settings (admin only).

    Body (all fields optional):
    {
        "kg_marketplace_enabled": true | false
    }
    """
    try:
        from trusted_data_agent.kg.settings import save_kg_marketplace_settings, get_kg_marketplace_settings

        data = await request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No data provided'}), 400

        if 'kg_marketplace_enabled' in data and not isinstance(data['kg_marketplace_enabled'], bool):
            return jsonify({'status': 'error', 'message': 'kg_marketplace_enabled must be boolean'}), 400

        admin_user = get_current_user_from_request()
        admin_uuid = admin_user.id if admin_user else 'unknown'

        save_kg_marketplace_settings(data, admin_uuid)

        return jsonify({
            'status': 'success',
            'message': 'KG marketplace settings updated successfully',
            'settings': get_kg_marketplace_settings(),
        }), 200

    except Exception as e:
        logger.error(f"Error saving KG marketplace settings: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500


@admin_api_bp.route("/v1/admin/source-endpoints", methods=["GET"])
@require_admin
async def list_source_endpoints():
    """Every data-source endpoint users' MCP servers and database connections reach — what an
    administrator picks from when writing a deployment-wide combine isolation rule (PROP-006 A13).
    Derived by code from the configurations; names as users gave them, never a credential."""
    import asyncio as _asyncio
    from trusted_data_agent.core.source_endpoints import list_endpoints
    return jsonify({"endpoints": await _asyncio.to_thread(list_endpoints)}), 200


def _combine_rules_normalized(rules=None):
    """The combine step's own normalizer over the given (or current) deployment rules."""
    from trusted_data_agent.core.config import APP_CONFIG
    from trusted_data_agent.agent.compute_sql import normalize_rules
    src = getattr(APP_CONFIG, 'COMBINE_ISOLATION', []) if rules is None else rules
    return normalize_rules(src, "deployment administrator")
