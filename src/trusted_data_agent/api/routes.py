# src/trusted_data_agent/api/routes.py
import json
import os
import logging
import asyncio
import sys
import copy
import hashlib
import httpx
import uuid # Import the uuid module
from pathlib import Path
from typing import Optional
from quart import Blueprint, request, jsonify, render_template, Response, abort, send_file
from langchain_mcp_adapters.prompts import load_mcp_prompt
from langchain_mcp_adapters.client import MultiServerMCPClient

from trusted_data_agent.auth.middleware import require_auth, optional_auth
from trusted_data_agent.api.operations import operation
from trusted_data_agent.core.config import APP_CONFIG, APP_STATE, get_user_mcp_server_id, get_user_mcp_client
from trusted_data_agent.core import session_manager
from trusted_data_agent.agent.prompts import PROVIDER_SYSTEM_PROMPTS
from trusted_data_agent.agent.executor import PlanExecutor
from trusted_data_agent.agent.rag_template_generator import RAGTemplateGenerator
from trusted_data_agent.llm import handler as llm_handler
from trusted_data_agent.agent import execution_service
from trusted_data_agent.core import configuration_service
from trusted_data_agent.core.utils import (
    synthesize_speech,
    unwrap_exception,
    _get_prompt_info,
    _regenerate_contexts,
    generate_task_id # Import generate_task_id
)


api_bp = Blueprint('api', __name__)
app_logger = logging.getLogger("quart.app")

def _get_user_uuid_from_request():
    """
    Extracts User UUID from request using JWT token.
    
    Authentication is always required.
    Returns None if not authenticated (caller should handle error).
    """
    try:
        from trusted_data_agent.auth.middleware import get_current_user
        user = get_current_user()
        if user and user.id:
            app_logger.debug(f"User UUID from auth token: {user.id}")
            return user.id
        else:
            app_logger.error(f"Authentication failed - get_current_user returned: {user}")
            return None
    except Exception as e:
        app_logger.error(f"Failed to get user from auth token: {e}", exc_info=True)
        return None


@api_bp.route("/")
async def index():
    """Serves the main HTML page."""
    # Ontology-suite gating flags (prepared; both True today). When the master switch
    # in core/enterprise.py flips to Enterprise-only, the Ontology nav + trust script
    # simply aren't emitted for non-enterprise deployments.
    return await render_template(
        "index.html",
        ontology_enabled=APP_STATE.get("ontology_enabled", True),
        trust_membrane_available=APP_STATE.get("trust_membrane_available", False),
        app_version=APP_STATE.get("app_version", "unknown"),
    )


@api_bp.route("/login")
async def login_page():
    """Serves the login page."""
    return await render_template("login.html")


@api_bp.route("/register")
async def register_page():
    """Serves the registration page."""
    return await render_template("register.html")

@api_bp.route("/verify-email")
async def verify_email_page():
    """Serves the email verification page."""
    return await render_template("verify_email.html")


@api_bp.route("/reset-password")
async def reset_password_page():
    """Serves the password reset page."""
    return await render_template("reset_password.html")


@api_bp.route("/api/status")
@optional_auth
async def get_application_status(current_user):
    """
    Returns the current configuration status of the application.
    This is used by the front end on startup to synchronize its state.
    Authentication is required - returns not configured if user not authenticated.
    """
    # Get user UUID - if not authenticated, return not configured
    user_uuid = current_user.id if current_user else None
    if not user_uuid:
        return jsonify({
            "isConfigured": False,
            "authenticationRequired": True
        })

    # Check if default profile exists (simplified check - activation enforces LLM+MCP configured)
    from trusted_data_agent.core.config_manager import get_config_manager
    config_manager = get_config_manager()
    default_profile_id = config_manager.get_default_profile_id(user_uuid)
    is_configured = default_profile_id is not None

    # Check RAG status
    rag_retriever = APP_STATE.get('rag_retriever_instance')

    # Check database for user's collections (independent of retriever initialization)
    # The database query doesn't need rag_retriever - it's a pure SQLite lookup
    rag_active = False
    if APP_CONFIG.RAG_ENABLED:
        try:
            from trusted_data_agent.core.collection_db import get_collection_db
            collection_db = get_collection_db()
            user_collections = collection_db.get_all_collections(user_id=user_uuid)
            # RAG is active if user has at least one collection (enabled or not)
            rag_active = len(user_collections) > 0
            app_logger.debug(f"RAG Status Check: retriever_exists={bool(rag_retriever)}, user_collections={len(user_collections)}, rag_active={rag_active}")
        except Exception as e:
            app_logger.warning(f"Failed to check RAG collections from database: {e}")
            # Fallback to checking in-memory collections if retriever is available
            if rag_retriever:
                rag_active = bool(rag_retriever.collections)
                app_logger.debug(f"RAG Status Check (fallback): collections_loaded={len(rag_retriever.collections)}, rag_active={rag_active}")
    else:
        app_logger.debug(f"RAG Status Check: rag_enabled=False, rag_active=False")
    
    if is_configured:
        status_payload = {
            "isConfigured": True,
            "provider": APP_CONFIG.ACTIVE_PROVIDER,
            "model": APP_CONFIG.ACTIVE_MODEL,
            "mcp_server": { "id": APP_CONFIG.CURRENT_MCP_SERVER_ID },
            "rag_active": rag_active,
            "knowledge": APP_STATE.get("knowledge_warmup"),   # core/first_start: loading | ready | failed
            "rag_enabled": APP_CONFIG.RAG_ENABLED
        }
        return jsonify(status_payload)
    else:
        status_payload = {
            "isConfigured": False,
            "rag_active": rag_active,
            "knowledge": APP_STATE.get("knowledge_warmup"),   # core/first_start: loading | ready | failed
            "rag_enabled": APP_CONFIG.RAG_ENABLED
        }
        return jsonify(status_payload)

@api_bp.route("/api/questions")
@optional_auth
async def get_rag_questions(current_user):
    """
    Returns semantically relevant questions from the RAG knowledge base.
    Supports profile-based filtering and semantic ranking.
    
    Query parameters:
    - query: Search text for semantic matching (optional, returns all if empty)
    - profile_id: Profile ID for collection filtering (optional)
    - limit: Maximum number of results (default: 10)
    """
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.agent.rag_access_context import RAGAccessContext
    
    query_text = request.args.get('query', '').strip()
    profile_id = request.args.get('profile_id', '').strip()
    limit = int(request.args.get('limit', 10))
    
    retriever = APP_STATE.get('rag_retriever_instance')
    if not retriever:
        return jsonify({"questions": []})
    
    # Get user context for access control
    user_uuid = current_user.id if current_user else None
    rag_context = RAGAccessContext(user_uuid, retriever) if user_uuid else None
    
    # Determine which collections to query based on profile and user access
    allowed_collection_ids = None
    profile = None
    if profile_id:
        config_manager = get_config_manager()
        profiles = config_manager.get_profiles(user_uuid)
        profile = next((p for p in profiles if p.get("id") == profile_id), None)
        
        if profile:
            autocomplete_collections = profile.get("autocompleteCollections", [])
            if not autocomplete_collections:
                # Autocomplete explicitly disabled for this profile
                return jsonify({"questions": []})
            if autocomplete_collections != ["*"]:
                allowed_collection_ids = set(autocomplete_collections)
    
    # Intersect with user-accessible collections if context available
    if rag_context:
        user_accessible = rag_context.accessible_collections
        if allowed_collection_ids is None:
            allowed_collection_ids = user_accessible
        else:
            allowed_collection_ids = allowed_collection_ids & user_accessible
    
    # If no query text, fall back to getting all unique questions (filtered by profile and user)
    if not query_text:
        questions = set()
        for coll_id, collection in retriever.collections.items():
            # Skip if profile filtering is active and this collection isn't allowed
            if allowed_collection_ids is not None and coll_id not in allowed_collection_ids:
                continue
                
            try:
                # Build where clause with user filtering if context available
                # Note: Template-generated cases should be accessible to all users, so we use OR logic
                where_clause = {"$and": [
                    {"strategy_type": {"$eq": "successful"}},
                    {"is_most_efficient": {"$eq": True}},
                    {"user_feedback_score": {"$gte": 0}}  # Exclude downvoted cases
                ]}
                if rag_context and user_uuid:
                    # Include both user's own cases AND template-generated cases
                    where_clause["$and"].append({
                        "$or": [
                            {"user_uuid": {"$eq": user_uuid}},
                            {"user_uuid": {"$eq": RAGTemplateGenerator.TEMPLATE_SESSION_ID}}
                        ]
                    })
                
                # Only get questions from successful and most efficient cases (not downvoted)
                results = collection.get(
                    where=where_clause,
                    include=["metadatas"]
                )
                for metadata in results.get("metadatas", []):
                    # Skip session primers (only if explicitly True; missing field = include)
                    if metadata.get("is_session_primer") is True:
                        continue

                    user_query = metadata.get("user_query", "")
                    # Filter out very short queries (< 3 chars) as they're not useful suggestions
                    if user_query and len(user_query.strip()) >= 3:
                        questions.add(user_query)
            except Exception as e:
                app_logger.error(f"Error getting documents from collection {collection.name}: {e}")
        
        return jsonify({"questions": sorted(list(questions))[:limit]})
    
    # Per-profile relevance threshold (fallback to global config)
    min_relevance = (
        profile.get("autocompleteMinRelevance", APP_CONFIG.AUTOCOMPLETE_MIN_RELEVANCE)
        if profile
        else APP_CONFIG.AUTOCOMPLETE_MIN_RELEVANCE
    )

    # Semantic search: query each allowed collection and aggregate results
    all_results = []
    for coll_id, collection in retriever.collections.items():
        # Skip if profile filtering is active and this collection isn't allowed
        if allowed_collection_ids is not None and coll_id not in allowed_collection_ids:
            continue
            
        try:
            # Build where clause with user filtering if context available
            # Note: Template-generated cases should be accessible to all users, so we use OR logic
            # Note: We don't filter by is_session_primer in the query because ChromaDB excludes missing fields
            where_clause = {"$and": [
                {"strategy_type": {"$eq": "successful"}},
                {"is_most_efficient": {"$eq": True}},
                {"user_feedback_score": {"$gte": 0}}  # Exclude downvoted cases
            ]}
            if rag_context and user_uuid:
                # Include both user's own cases AND template-generated cases
                where_clause["$and"].append({
                    "$or": [
                        {"user_uuid": {"$eq": user_uuid}},
                        {"user_uuid": {"$eq": RAGTemplateGenerator.TEMPLATE_SESSION_ID}}
                    ]
                })

            # Only query successful and most efficient cases (not downvoted)
            results = collection.query(
                query_texts=[query_text],
                n_results=limit,
                where=where_clause,
                include=["metadatas", "distances"]
            )

            # Extract questions with their similarity scores, filtering by relevance threshold
            # Post-filter to exclude session primers (handles missing field gracefully)
            max_distance = 1.0 - min_relevance
            if results and results.get("metadatas") and results["metadatas"][0]:
                for idx, metadata in enumerate(results["metadatas"][0]):
                    if "user_query" in metadata:
                        # Skip session primers (only if explicitly True; missing field = include)
                        if metadata.get("is_session_primer") is True:
                            continue

                        distance = results["distances"][0][idx] if results.get("distances") else 0
                        if max_distance < 1.0 and distance > max_distance:
                            continue
                        all_results.append({
                            "question": metadata["user_query"],
                            "distance": distance,
                            "collection_id": coll_id
                        })
        except Exception as e:
            app_logger.error(f"Error querying collection {collection.name}: {e}")
    
    # Sort by distance (lower is better for cosine/L2 distance) and deduplicate
    all_results.sort(key=lambda x: x["distance"])
    seen_questions = set()
    unique_questions = []
    
    for result in all_results:
        question = result["question"]
        if question not in seen_questions:
            seen_questions.add(question)
            unique_questions.append(question)
            if len(unique_questions) >= limit:
                break
    
    return jsonify({"questions": unique_questions})


# Registered under the /api/v1 path the welcome banner has fetched since the initial commit;
# the route was declared bare, so the banner 404ed on every load and never showed a warning.
@api_bp.route("/api/v1/consumption_warnings", methods=["GET"])
@operation("consumption.warnings", effect="read", scope="own",
           summary="The caller's usage warnings (limits close or reached)")
async def consumption_warnings():
    """
    Returns consumption usage warnings for the welcome screen.
    Checks hourly, daily, and monthly limits and returns the highest percentage.
    """
    from trusted_data_agent.auth.middleware import get_current_user
    
    current_user = get_current_user()
    if not current_user:
        return jsonify({"warning_level": None, "percentage": 0, "message": "Not authenticated"}), 401
    
    user_uuid = current_user.id

    try:
        from trusted_data_agent.auth.consumption_enforcer import ConsumptionEnforcer
        from trusted_data_agent.auth.models import User
        from trusted_data_agent.auth.database import get_db_session
        
        # Check if user is admin (no limits)
        with get_db_session() as session:
            user = session.query(User).filter_by(id=user_uuid).first()
            is_admin = user.is_admin if user else False
        
        if is_admin:
            return jsonify({"warning_level": None, "percentage": 0, "message": "Admin - No limits"})
        
        # Get consumption enforcer and check usage
        enforcer = ConsumptionEnforcer(user_uuid)
        usage = enforcer.get_current_usage()
        
        # Calculate highest percentage across all limits
        max_percentage = 0
        limit_name = ""
        
        # Check hourly prompts
        if usage.get('prompts_per_hour_limit') and usage['prompts_per_hour_limit'] > 0:
            hourly_pct = (usage['prompts_this_hour'] / usage['prompts_per_hour_limit']) * 100
            if hourly_pct > max_percentage:
                max_percentage = hourly_pct
                limit_name = "hourly prompts"
        
        # Check daily prompts
        if usage.get('prompts_per_day_limit') and usage['prompts_per_day_limit'] > 0:
            daily_pct = (usage['prompts_today'] / usage['prompts_per_day_limit']) * 100
            if daily_pct > max_percentage:
                max_percentage = daily_pct
                limit_name = "daily prompts"
        
        # Check monthly input tokens
        if usage.get('input_tokens_per_month_limit') and usage['input_tokens_per_month_limit'] > 0:
            input_pct = (usage['input_tokens_this_month'] / usage['input_tokens_per_month_limit']) * 100
            if input_pct > max_percentage:
                max_percentage = input_pct
                limit_name = "monthly input tokens"
        
        # Check monthly output tokens
        if usage.get('output_tokens_per_month_limit') and usage['output_tokens_per_month_limit'] > 0:
            output_pct = (usage['output_tokens_this_month'] / usage['output_tokens_per_month_limit']) * 100
            if output_pct > max_percentage:
                max_percentage = output_pct
                limit_name = "monthly output tokens"
        
        # Determine warning level
        warning_level = None
        message = ""
        
        if max_percentage >= 100:
            warning_level = "critical"
            message = f"You've reached your {limit_name} limit ({int(max_percentage)}%). New requests will be blocked."
        elif max_percentage >= 95:
            warning_level = "urgent"
            message = f"You've used {int(max_percentage)}% of your {limit_name} quota. You're very close to the limit."
        elif max_percentage >= 80:
            warning_level = "warning"
            message = f"You've used {int(max_percentage)}% of your {limit_name} quota. Consider managing your usage."
        
        return jsonify({
            "warning_level": warning_level,
            "percentage": round(max_percentage, 1),
            "message": message,
            "usage": usage
        })
        
    except Exception as e:
        app_logger.error(f"Error fetching consumption warnings: {e}", exc_info=True)
        return jsonify({"warning_level": None, "percentage": 0, "message": "Error checking consumption"}), 500

@api_bp.route("/simple_chat", methods=["POST"])
async def simple_chat():
    """
    Handles direct, tool-less chat with the configured LLM.
    This is used by the 'Chat' modal in the UI.
    """
    if not APP_STATE.get('llm'):
        return jsonify({"error": "LLM not configured."}), 400

    # Check consumption limits
    user_uuid = _get_user_uuid_from_request()
    if user_uuid:
        try:
            from trusted_data_agent.auth.consumption_enforcer import ConsumptionEnforcer
            from trusted_data_agent.auth.models import User
            from trusted_data_agent.auth.database import get_db_session
            
            # Check if user is admin
            with get_db_session() as session:
                user = session.query(User).filter_by(id=user_uuid).first()
                is_admin = user.is_admin if user else False
            
            if not is_admin:
                enforcer = ConsumptionEnforcer(user_uuid)
                can_proceed, error_message = enforcer.can_execute_prompt()
                
                if not can_proceed:
                    app_logger.warning(f"Consumption limit exceeded (simple_chat) for user {user_uuid}: {error_message}")
                    return jsonify({"error": error_message, "type": "rate_limit_exceeded"}), 429
        except Exception as e:
            app_logger.error(f"Error checking consumption limits: {e}", exc_info=True)
            # Fail open

    data = await request.get_json()
    message = data.get("message")
    history = data.get("history", [])

    if not message:
        return jsonify({"error": "No message provided."}), 400

    try:
        response_text, _, _, _, _ = await llm_handler.call_llm_api(
            llm_instance=APP_STATE.get('llm'),
            prompt=message,
            chat_history=history,
            system_prompt_override="You are a helpful assistant.",
            dependencies={'STATE': APP_STATE},
            reason="Simple, tool-less chat."
        )

        final_response = response_text.replace("FINAL_ANSWER:", "").strip()

        return jsonify({"response": final_response})

    except Exception as e:
        app_logger.error(f"Error in simple_chat: {e}", exc_info=True)
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

@api_bp.route("/app-config")
async def get_app_config():
    """Returns the startup configuration flags and license info."""
    from trusted_data_agent.core.config_manager import get_config_manager
    
    # Load window defaults from THIS USER's config.
    #
    # `load_config()` with no uuid returns the read-only bootstrap template, which is not
    # where window defaults live: the admin panel writes them into each user's
    # preferences. Reading the bootstrap here meant the admin could save a default
    # (Administration -> App Config), see it stored, and every browser would still be
    # served the template's value — a write and a read that never met. Unauthenticated
    # callers (the login page) still get the template, which is the right fallback.
    config_manager = get_config_manager()
    config = config_manager.load_config(_get_user_uuid_from_request())
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
    
    from trusted_data_agent.core.tts_service import get_tts_mode, has_global_tts_credentials, has_user_tts_credentials
    tts_mode = get_tts_mode()

    # Only enable voice buttons if credentials actually exist for the active mode
    voice_enabled = False
    if tts_mode == 'global':
        voice_enabled = has_global_tts_credentials()
    elif tts_mode == 'user':
        user_uuid = _get_user_uuid_from_request()
        if user_uuid:
            voice_enabled = has_user_tts_credentials(user_uuid)

    return jsonify({
        "charting_enabled": APP_CONFIG.CHARTING_ENABLED,
        "allow_synthesis_from_history": APP_CONFIG.ALLOW_SYNTHESIS_FROM_HISTORY,
        "default_charting_intensity": APP_CONFIG.DEFAULT_CHARTING_INTENSITY,
        "voice_conversation_enabled": voice_enabled,
        "tts_mode": tts_mode,
        "rag_enabled": APP_CONFIG.RAG_ENABLED,
        "license_info": APP_STATE.get("license_info"),
        "window_defaults": window_defaults
    })

@api_bp.route("/api/prompts-version")
async def get_prompts_version():
    """
    Returns a SHA-256 hash of the master system prompts.
    """
    try:
        # Convert LazyPrompt objects to strings for JSON serialization
        prompts_dict = {k: str(v) for k, v in PROVIDER_SYSTEM_PROMPTS.items()}
        prompts_str = json.dumps(prompts_dict, sort_keys=True)
        prompt_hash = hashlib.sha256(prompts_str.encode('utf-8')).hexdigest()
        return jsonify({"version": prompt_hash})
    except Exception as e:
        app_logger.error(f"Failed to generate prompts version hash: {e}", exc_info=True)
        return jsonify({"error": "Could not generate prompts version"}), 500

@api_bp.route("/api_key/<provider>")
@require_auth
async def get_api_key(current_user, provider):
    """Retrieves API keys from environment variables for pre-population.

    The server's environment keys are deployment secrets: only an admin may read them
    (a signed-in user is not enough — every user would otherwise hold the platform's keys).
    """
    if not getattr(current_user, "is_admin", False):
        return jsonify({"status": "error", "message": "Admin privileges required."}), 403
    key = None
    provider_lower = provider.lower()

    if provider_lower == 'google':
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        return jsonify({"apiKey": key or ""})
    elif provider_lower == 'anthropic':
        key = os.environ.get("ANTHROPIC_API_KEY")
        return jsonify({"apiKey": key or ""})
    elif provider_lower == 'openai':
        key = os.environ.get("OPENAI_API_KEY")
        return jsonify({"apiKey": key or ""})
    elif provider_lower == 'azure':
        keys = {
            "azure_api_key": os.environ.get("AZURE_OPENAI_API_KEY"),
            "azure_endpoint": os.environ.get("AZURE_OPENAI_ENDPOINT"),
            "azure_deployment_name": os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME"),
            "azure_api_version": os.environ.get("AZURE_OPENAI_API_VERSION")
        }
        return jsonify(keys)
    elif provider_lower == 'friendli':
        keys = {
            "friendli_token": os.environ.get("FRIENDLI_TOKEN"),
            "friendli_endpoint_url": os.environ.get("FRIENDLI_ENDPOINT_URL")
        }
        return jsonify(keys)
    elif provider_lower == 'amazon':
        keys = {
            "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID"),
            "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY"),
            "aws_region": os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        }
        return jsonify(keys)
    elif provider_lower == 'ollama':
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        return jsonify({"host": host})

    return jsonify({"error": "Unknown provider"}), 404

@api_bp.route("/api/synthesize-speech", methods=["POST"])
async def text_to_speech():
    """
    Converts text to speech using Google Cloud TTS.
    Resolves TTS client based on tts_mode (global/user/disabled).
    """
    from trusted_data_agent.core.tts_service import resolve_tts_client, get_tts_mode

    tts_mode = get_tts_mode()
    if tts_mode == 'disabled':
        app_logger.warning("Voice conversation feature is disabled. Aborting text-to-speech request.")
        return jsonify({"error": "Voice conversation feature is disabled."}), 403

    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required."}), 401

    data = await request.get_json()
    text = data.get("text")
    if not text:
        app_logger.warning("No text provided in request body for speech synthesis.")
        return jsonify({"error": "No text provided for synthesis."}), 400

    tts_client = resolve_tts_client(user_uuid)
    if not tts_client:
        if tts_mode == 'user':
            return jsonify({"error": "No TTS credentials configured. Please add your Google TTS credentials in Settings."}), 403
        else:
            return jsonify({"error": "TTS client could not be initialized. Contact your administrator."}), 500

    audio_content = synthesize_speech(tts_client, text)

    if audio_content:
        app_logger.debug(f"Returning synthesized audio content ({len(audio_content)} bytes).")
        return Response(audio_content, mimetype="audio/mpeg")
    else:
        app_logger.error("synthesize_speech returned None. Sending error response.")
        return jsonify({"error": "Failed to synthesize speech."}), 500


# --- User TTS Credential Endpoints ---

@api_bp.route("/api/user/tts-credentials", methods=["GET"])
async def get_user_tts_status():
    """Check if current user has TTS credentials stored and what the current TTS mode is."""
    from trusted_data_agent.core.tts_service import get_tts_mode, has_user_tts_credentials

    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required."}), 401

    tts_mode = get_tts_mode()
    has_creds = has_user_tts_credentials(user_uuid) if tts_mode == 'user' else False

    return jsonify({
        "tts_mode": tts_mode,
        "has_credentials": has_creds
    }), 200


@api_bp.route("/api/user/tts-credentials", methods=["POST"])
async def save_user_tts_creds():
    """Save user's TTS credentials (encrypted)."""
    from trusted_data_agent.core.tts_service import save_user_tts_credentials, get_tts_mode

    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required."}), 401

    if get_tts_mode() != 'user':
        return jsonify({"error": "TTS is not in user-credentials mode. Contact your administrator."}), 403

    data = await request.get_json()
    credentials_json = data.get("credentials_json", "").strip()
    if not credentials_json:
        return jsonify({"error": "No credentials provided."}), 400

    if save_user_tts_credentials(user_uuid, credentials_json):
        return jsonify({"status": "success", "message": "TTS credentials saved successfully."}), 200
    else:
        return jsonify({"error": "Failed to save TTS credentials. Check JSON format."}), 400


@api_bp.route("/api/user/tts-credentials", methods=["DELETE"])
async def delete_user_tts_creds():
    """Delete user's stored TTS credentials."""
    from trusted_data_agent.core.tts_service import delete_user_tts_credentials

    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required."}), 401

    delete_user_tts_credentials(user_uuid)
    return jsonify({"status": "success", "message": "TTS credentials deleted."}), 200


@api_bp.route("/api/user/tts-credentials/test", methods=["POST"])
async def test_user_tts_creds():
    """Test user TTS credentials by attempting to create a client."""
    from trusted_data_agent.core.tts_service import test_tts_credentials

    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required."}), 401

    data = await request.get_json()
    credentials_json = data.get("credentials_json", "").strip()
    if not credentials_json:
        return jsonify({"error": "No credentials provided."}), 400

    result = test_tts_credentials(credentials_json)
    if result['success']:
        return jsonify({"status": "success", "message": "TTS credentials are valid."}), 200
    else:
        return jsonify({"error": result['error']}), 400


# ── The legacy resource routes: the agent asked for, through the ONE mechanism ────────────
# An agent's catalog is computed in ONE place — rest_routes.get_profile_resources: per agent,
# per user, with that agent's own on/off flags. These four routes used to read the
# PROCESS-GLOBAL APP_STATE catalog (whichever agent was last loaded, with THAT agent's
# flags) and answered WITHOUT a token: an anonymous GET /tools returned the whole catalog.
# They now require the caller's token and serve the agent asked for (?profile_id=) or the
# caller's default agent, in the shapes the panel has always read.

_SPECIAL_PROFILE_TYPES = ("rag_focused", "genie", "llm_only")
_SPECIAL_META_KEYS = ("profile_type", "profile_tag", "knowledge_collections", "slave_profiles")


async def _profile_resources_payload(current_user):
    """The per-agent payload for ``?profile_id=`` or the caller's default agent — ``(payload, None)``
    or ``(None, error_response)``."""
    from trusted_data_agent.api.rest_routes import get_profile_resources
    from trusted_data_agent.core.config_manager import get_config_manager
    profile_id = request.args.get("profile_id") or get_config_manager().get_default_profile_id(current_user.id)
    if not profile_id:
        return None, (jsonify({"error": "No profiles configured"}), 400)
    resp = await get_profile_resources(profile_id)
    body, status = resp if isinstance(resp, tuple) else (resp, 200)
    if status != 200:
        return None, (body, status)
    return await body.get_json(), None


def _legacy_shape(payload, kind):
    """``{category: [...]}``, or — for a Focus / Coordinate / Ideate-without-tools agent, which
    lists none — the metadata shape the panel reads (``{kind: {}, profile_type, profile_tag, …}``)."""
    categories = payload.get(kind) or {}
    if payload.get("profile_type") in _SPECIAL_PROFILE_TYPES and not categories:
        return {kind: {}, **{k: payload[k] for k in _SPECIAL_META_KEYS if k in payload}}
    return categories


@api_bp.route("/tools")
@require_auth
async def get_tools(current_user):
    """The categorized MCP tools of the agent asked for (``?profile_id=``) or the caller's default agent."""
    payload, err = await _profile_resources_payload(current_user)
    return err if err else jsonify(_legacy_shape(payload, "tools"))


@api_bp.route("/connectors")
@require_auth
async def get_connectors(current_user):
    """That agent's active Platform Connector tools, grouped by connector name."""
    payload, err = await _profile_resources_payload(current_user)
    return err if err else jsonify(payload.get("connectors") or {})


@api_bp.route("/prompts")
@require_auth
async def get_prompts(current_user):
    """That agent's categorized MCP prompts (metadata only)."""
    payload, err = await _profile_resources_payload(current_user)
    return err if err else jsonify(_legacy_shape(payload, "prompts"))


@api_bp.route("/resources")
@require_auth
async def get_resources(current_user):
    """MCP resources — a placeholder (the panel hides the tab); authenticated like its siblings."""
    return jsonify({})


async def _toggle_selection(current_user, kind: str):
    """Switch one MCP tool or prompt on or off FOR A PROFILE — the same selection the profile
    editor edits (``profile.tools`` / ``profile.prompts`` and each additional server's lists).

    Before this, the resource panel's eye icon changed only the process-wide
    ``APP_STATE['disabled_*']`` list: nothing reached the profile (the editor kept showing the
    item enabled), it came back on the next profile switch or restart, and — being process-wide
    and unauthenticated — it changed the agent for every user meanwhile.
    """
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.core import profile_selection as ps

    data = await request.get_json(silent=True) or {}
    name = data.get("name")
    is_disabled = data.get("disabled")
    if not name or is_disabled is None:
        return jsonify({"status": "error", "message": "Missing 'name' or 'disabled' field."}), 400

    user_uuid = current_user.id
    cm = get_config_manager()
    profile_id = data.get("profile_id") or cm.get_default_profile_id(user_uuid)
    profile = cm.get_profile(profile_id, user_uuid) if profile_id else None

    is_loaded = bool(profile_id) and APP_STATE.get("active_profile_id") == profile_id
    live = list((APP_STATE.get("mcp_tools" if kind == "tools" else "mcp_prompts") or {}).keys()) if is_loaded else []
    try:
        updates = ps.plan_toggle(profile, name, kind, not bool(is_disabled),
                                 ps.universe_resolver(profile or {}, kind, user_uuid, cm, live))
    except ps.ToggleRefused as e:
        return jsonify({"status": "error", "message": str(e)}), e.status

    if not cm.update_profile(profile_id, updates, user_uuid):
        return jsonify({"status": "error", "message": "The profile could not be saved."}), 500

    label = "Tool" if kind == "tools" else "Prompt"
    app_logger.info(f"{label} '{name}' {'disabled' if is_disabled else 'enabled'} for profile {profile_id}.")

    if is_loaded:
        APP_STATE["disabled_tools"] = cm.get_profile_disabled_tools(profile_id, user_uuid)
        APP_STATE["disabled_prompts"] = cm.get_profile_disabled_prompts(profile_id, user_uuid)
        _regenerate_contexts()

    return jsonify({"status": "success", "profile_id": profile_id,
                    "message": f"{label} '{name}' {'disabled' if is_disabled else 'enabled'} for this profile."})


@api_bp.route("/tool/toggle_status", methods=["POST"])
@require_auth
async def toggle_tool_status(current_user):
    """Enables or disables an MCP tool for a profile (persisted on the profile)."""
    return await _toggle_selection(current_user, "tools")


@api_bp.route("/prompt/toggle_status", methods=["POST"])
@require_auth
async def toggle_prompt_status(current_user):
    """Enables or disables an MCP prompt for a profile (persisted on the profile)."""
    return await _toggle_selection(current_user, "prompts")

@api_bp.route("/app-settings", methods=["GET"])
async def get_app_settings():
    """
    Returns application-level settings that are relevant to the frontend.
    No authentication required for public settings.
    """
    return jsonify({
        "github_api_enabled": APP_CONFIG.GITHUB_API_ENABLED
    })

@api_bp.route("/prompt/<prompt_name>", methods=["GET"])
@require_auth
async def get_prompt_content(current_user, prompt_name):
    """
    Retrieves the content of a specific MCP prompt. For dynamic prompts
    with arguments, it renders them with placeholder values for preview.
    """
    # Get user UUID for per-user MCP server ID
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"error": "Authentication required."}), 401

    # Try to get user's cached MCP client first
    mcp_client = get_user_mcp_client(user_uuid)
    server_id = get_user_mcp_server_id(user_uuid)

    # Auto-load profile if MCP client not cached (same pattern as /resources endpoint)
    if not mcp_client or not server_id:
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()

        # Use active_for_consumption profile (handles profile overwrite)
        active_profile_ids = config_manager.get_active_for_consumption_profile_ids(user_uuid)
        profile_id_to_load = active_profile_ids[0] if active_profile_ids else None

        if not profile_id_to_load:
            # Fallback to default profile
            profile_id_to_load = config_manager.get_default_profile_id(user_uuid)

        if not profile_id_to_load:
            return jsonify({"error": "No active profile configured."}), 400

        # Load profile into APP_STATE (without LLM validation for prompt viewing)
        result = await configuration_service.switch_profile_context(profile_id_to_load, user_uuid, validate_llm=False)
        if result["status"] != "success":
            return jsonify({"error": f"Failed to load profile: {result.get('message', 'Unknown error')}"}), 400

        # Re-fetch after loading
        mcp_client = get_user_mcp_client(user_uuid)
        server_id = get_user_mcp_server_id(user_uuid)

    if not mcp_client:
        return jsonify({"error": "MCP client not configured for this profile."}), 400

    if not server_id:
        return jsonify({"error": "MCP server ID not configured for this profile."}), 400

    try:
        prompt_info = _get_prompt_info(prompt_name)
        placeholder_args = {}

        if prompt_info and prompt_info.get("arguments"):
            app_logger.info(f"'{prompt_name}' is a dynamic prompt. Building placeholder arguments for preview.")
            for arg in prompt_info["arguments"]:
                arg_name = arg.get("name")
                if arg_name:
                    placeholder_args[arg_name] = f"<{arg_name}>"

        # PROP-006 phase 2 (O3): a prompt of the active profile's additional server is previewed
        # from that server under its own name
        from trusted_data_agent.mcp_adapter.adapter import resolve_mcp_target
        from trusted_data_agent.core.agent_mcp_servers import server_map_for
        from trusted_data_agent.core.config_manager import get_config_manager as _gcm
        _cmgr = _gcm()
        _active = _cmgr.get_active_for_consumption_profile_ids(user_uuid)
        _pid = (_active[0] if _active else None) or _cmgr.get_default_profile_id(user_uuid)
        _p_client, _p_sid, _p_name = resolve_mcp_target(
            prompt_name, mcp_client, server_id, user_uuid, servers=server_map_for(_pid, user_uuid))
        async with _p_client.session(_p_sid) as temp_session:
            if placeholder_args:
                prompt_obj = await load_mcp_prompt(
                    temp_session, name=_p_name, arguments=placeholder_args
                )
            else:
                prompt_obj = await temp_session.get_prompt(name=_p_name)

        if not prompt_obj:
            return jsonify({"error": f"Prompt '{prompt_name}' not found."}), 404

        prompt_text = "Prompt content is not available."
        if isinstance(prompt_obj, str):
            prompt_text = prompt_obj
        elif (isinstance(prompt_obj, list) and len(prompt_obj) > 0 and hasattr(prompt_obj[0], 'content')):
             if isinstance(prompt_obj[0].content, str):
                 prompt_text = prompt_obj[0].content
             elif hasattr(prompt_obj[0].content, 'text'):
                 prompt_text = prompt_obj[0].content.text
        elif (hasattr(prompt_obj, 'messages') and
            isinstance(prompt_obj.messages, list) and
            len(prompt_obj.messages) > 0 and
            hasattr(prompt_obj.messages[0], 'content') and
            hasattr(prompt_obj.messages[0].content, 'text')):
            prompt_text = prompt_obj.messages[0].content.text
        elif hasattr(prompt_obj, 'text') and isinstance(prompt_obj.text, str):
            prompt_text = prompt_obj.text

        return jsonify({"name": prompt_name, "content": prompt_text})

    except Exception as e:
        root_exception = unwrap_exception(e)
        error_str = str(root_exception)

        # Handle stale classification cache: prompt listed in cached classification
        # but not available on the MCP server (removed, renamed, or arguments changed)
        if "Unknown prompt" in error_str:
            app_logger.warning(f"Prompt '{prompt_name}' not found on MCP server (stale classification cache): {error_str}")
            return jsonify({
                "error": "prompt_not_on_server",
                "message": f"Prompt '{prompt_name}' is not available on the MCP server. "
                           f"It may have been removed or renamed since the last profile classification. "
                           f"Try reclassifying the profile to refresh."
            }), 404

        app_logger.error(f"Error fetching prompt content for '{prompt_name}': {root_exception}", exc_info=True)
        return jsonify({"error": "An unexpected error occurred while fetching the prompt."}), 500

@api_bp.route("/api/notifications/ticket", methods=["POST"])
@require_auth
async def notification_stream_ticket(current_user):
    """Issue a one-minute ticket that opens this user's notification stream.

    EventSource cannot send an Authorization header; the page asks here WITH its
    header and opens the stream with ``?ticket=``. The ticket names the caller, so a
    stream can only ever be opened for the authenticated user.
    """
    from trusted_data_agent.auth.security import create_stream_ticket
    return jsonify({"ticket": create_stream_ticket(current_user.id)})


@api_bp.route("/api/notifications/subscribe", methods=["GET"])
async def subscribe_notifications():
    """
    SSE endpoint for clients to receive real-time notifications.
    Includes a heartbeat to keep the connection alive.

    The user is taken from the ``ticket`` (``POST /api/notifications/ticket``), never
    from a query parameter the caller chooses: a user id alone opens nothing.
    """
    from trusted_data_agent.auth.security import verify_stream_ticket
    user_uuid = verify_stream_ticket(request.args.get("ticket", ""))
    if not user_uuid:
        app_logger.info("Notification subscription refused: missing or invalid ticket.")
        return jsonify({"status": "error", "message": "A valid notification ticket is required."}), 401

    async def notification_generator():
        queue = asyncio.Queue()
        # Use a set for faster lookups
        queues_for_user = APP_STATE.setdefault("notification_queues", {}).setdefault(user_uuid, set())
        queues_for_user.add(queue)
        # Send active maintenance banner immediately to late-joining subscribers,
        # adjusting the countdown to reflect elapsed time since the banner was set.
        active_banner = APP_STATE.get("maintenance_banner")
        if active_banner:
            import time as _time
            original_countdown = active_banner.get("countdown_minutes", 0)
            started_at = active_banner.get("_started_at")
            if started_at and original_countdown > 0:
                elapsed_minutes = (_time.time() - started_at) / 60
                remaining = max(0, original_countdown - elapsed_minutes)
            else:
                remaining = original_countdown
            adjusted_banner = {
                "message": active_banner["message"],
                "countdown_minutes": remaining,
                "downtime_minutes": active_banner.get("downtime_minutes", 0),
            }
            await queue.put({"type": "maintenance_banner", "payload": adjusted_banner})
        try:
            while True:
                try:
                    # Wait for a notification with a 20-second timeout
                    notification = await asyncio.wait_for(queue.get(), timeout=20.0)
                    if isinstance(notification, dict) and notification.get("type") == "__stream_close__":
                        # The server is stopping (main.close_live_streams): end the stream so the
                        # graceful stop has nothing left to wait for; the page reconnects.
                        return
                    yield PlanExecutor._format_sse(notification, "notification")
                except asyncio.TimeoutError:
                    # If timeout, send a heartbeat comment to keep the connection alive
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            app_logger.debug(f"Notification subscription cancelled for user {user_uuid}.")
            raise   # a swallowed cancellation leaves asyncio's shutdown waiting on this task
        finally:
            queues_for_user.remove(queue)
            # Clean up user entry if no more queues are associated with them
            if not queues_for_user:
                notification_queues = APP_STATE.get("notification_queues", {})
                if user_uuid in notification_queues:
                    del notification_queues[user_uuid]

    return Response(notification_generator(), mimetype="text/event-stream")

@api_bp.route("/rag/collections", methods=["GET"])
async def list_rag_collections():
    """Lists available planner collections via the retriever's collection registry."""
    try:
        retriever = APP_STATE.get('rag_retriever_instance')
        if not retriever:
            return jsonify({"collections": []})

        collections = []
        for coll_id, coll_obj in retriever.collections.items():
            name = getattr(coll_obj, 'name', None) or str(coll_id)
            count = None
            metadata = {}
            try:
                count = coll_obj.count()
                metadata = getattr(coll_obj, 'metadata', None) or {}
            except Exception as inner_e:
                app_logger.warning(f"Failed to inspect collection '{name}': {inner_e}")
            collections.append({"name": name, "count": count, "metadata": metadata})
        return jsonify({"collections": collections})
    except Exception as e:
        app_logger.error(f"Error listing RAG collections: {e}", exc_info=True)
        return jsonify({"error": "Failed to list collections"}), 500

@api_bp.route("/rag/collections/<int:collection_id>/rows", methods=["GET"])
async def get_collection_rows(collection_id):
    """Returns a sample or search results of rows from a planner collection.

    Uses the retriever's pre-loaded ChromaDB collection objects instead of
    creating a raw ChromaDB client.  This keeps the endpoint consistent with
    the vector store abstraction layer.

    Query Parameters:
      limit (int): number of rows to return (default 25, max 100)
      q (str): optional search query; if provided runs a similarity query
      light (bool): if true, omits full_case_data from response for lighter payload
    """
    try:
        # Look up collection metadata by ID from database (not stale APP_STATE)
        from trusted_data_agent.core.collection_db import get_collection_db
        collection_db = get_collection_db()
        collection_meta = collection_db.get_collection_by_id(collection_id)

        if not collection_meta:
            return jsonify({"error": f"Collection with ID {collection_id} not found."}), 404

        collection_name = collection_meta["collection_name"]
        app_logger.info(f"RAG Inspection - Collection ID: {collection_id}, Name: {collection_meta.get('name')}, ChromaDB Name: {collection_name}")

        # Get collection via retriever (pre-loaded ChromaDB objects)
        retriever = APP_STATE.get('rag_retriever_instance')
        collection = None

        if retriever:
            # Primary path: use retriever's pre-loaded collection
            coll_obj = retriever.collections.get(collection_id)
            if coll_obj is not None:
                collection = coll_obj
                app_logger.info(f"Using retriever collection for ID {collection_id}")
            else:
                # Collection might not be loaded yet — try via ChromaDB client
                client = getattr(retriever, 'client', None)
                if client:
                    try:
                        collection = client.get_or_create_collection(
                            name=collection_name,
                            metadata={"hnsw:space": "cosine"},
                        )
                        app_logger.info(f"Loaded collection '{collection_name}' via retriever client")
                    except Exception as e:
                        app_logger.warning(f"Failed to load collection '{collection_name}': {e}")

        if collection is None:
            return jsonify({
                "error": "Collection not loaded. Please ensure the RAG system is initialized.",
                "rows": [],
                "total": 0,
                "collection_name": collection_name,
            }), 404

        count = collection.count()
        app_logger.info(f"Collection '{collection_name}' has {count} documents")

        limit = request.args.get('limit', default=25, type=int)
        limit = max(1, min(limit, 200))  # Allow up to 200 rows per request
        offset = request.args.get('offset', default=0, type=int)
        offset = max(0, offset)
        query_text = request.args.get('q', default=None, type=str)
        light = request.args.get('light', default='true').lower() == 'true'

        rows = []
        total = 0
        
        # --- MODIFICATION START: Get user context for access filtering ---
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401
        
        # Determine if we need to filter by user_uuid
        retriever = APP_STATE.get('rag_retriever_instance')
        from trusted_data_agent.agent.rag_access_context import RAGAccessContext
        
        where_filter = None
        if retriever:
            rag_context = RAGAccessContext(user_id=user_uuid, retriever=retriever)
            access_type = rag_context.get_access_type(collection_id)
            
            # For owned collections, filter to show only current user's cases
            if access_type == "owned":
                where_filter = {"user_uuid": {"$eq": user_uuid}}
        # --- MODIFICATION END ---
        
        if query_text and len(query_text) >= 3:
            # Similarity search path
            try:
                query_results = collection.query(
                    query_texts=[query_text], n_results=limit, include=["metadatas", "distances"], where=where_filter
                )
                if query_results and query_results.get("ids"):
                    total = len(query_results["ids"][0])
                    for i in range(total):
                        row_id = query_results["ids"][0][i]
                        meta = query_results["metadatas"][0][i]
                        distance = query_results["distances"][0][i]
                        similarity = 1 - distance
                        full_case_data = None
                        if not light:
                            try:
                                full_case_data = json.loads(meta.get("full_case_data", "{}"))
                            except json.JSONDecodeError:
                                full_case_data = None
                        rows.append({
                            "id": row_id,
                            "user_query": meta.get("user_query"),
                            "strategy_type": meta.get("strategy_type"),
                            "is_most_efficient": meta.get("is_most_efficient"),
                            "user_feedback_score": meta.get("user_feedback_score", 0),
                            "output_tokens": meta.get("output_tokens"),
                            "timestamp": meta.get("timestamp"),
                            "similarity_score": similarity,
                            "full_case_data": full_case_data,
                        })
            except Exception as qe:
                app_logger.warning(f"Query failed for collection '{collection_name}': {qe}")
        else:
            # Sampling path: attempt limited get; fallback to slice
            try:
                # ChromaDB doesn't always expose a limit param; retrieve all then slice
                all_results = collection.get(include=["metadatas"], where=where_filter)
                ids = all_results.get("ids", [])
                metas = all_results.get("metadatas", [])
                total = len(ids)
                sample_count = min(limit, total)
                for i in range(sample_count):
                    meta = metas[i]
                    full_case_data = None
                    if not light:
                        try:
                            full_case_data = json.loads(meta.get("full_case_data", "{}"))
                        except json.JSONDecodeError:
                            full_case_data = None
                    rows.append({
                        "id": ids[i],
                        "user_query": meta.get("user_query"),
                        "strategy_type": meta.get("strategy_type"),
                        "is_most_efficient": meta.get("is_most_efficient"),
                        "user_feedback_score": meta.get("user_feedback_score", 0),
                        "output_tokens": meta.get("output_tokens"),
                        "timestamp": meta.get("timestamp"),
                        "full_case_data": full_case_data,
                    })
            except Exception as ge:
                app_logger.error(f"Sampling failed for collection '{collection_name}': {ge}", exc_info=True)
        
        # Override feedback scores from cache for consistency (cache is source of truth after updates)
        retriever = APP_STATE.get('rag_retriever_instance')
        if retriever and hasattr(retriever, 'get_feedback_score'):
            for row in rows:
                case_id = row.get('id')
                if case_id:
                    cached_feedback = retriever.get_feedback_score(case_id)
                    if cached_feedback != row.get('user_feedback_score', 0):
                        app_logger.debug(f"Using cached feedback for {case_id}: {cached_feedback} (was {row.get('user_feedback_score', 0)})")
                        row['user_feedback_score'] = cached_feedback
        
        # Apply server-side sorting if requested
        sort_by = request.args.get('sort_by', default=None, type=str)
        sort_order = request.args.get('sort_order', default='asc', type=str)
        if sort_by and sort_by in ['id', 'user_query', 'strategy_type', 'user_feedback_score', 'output_tokens', 'timestamp', 'similarity_score']:
            reverse = sort_order.lower() == 'desc'
            try:
                rows.sort(key=lambda x: (x.get(sort_by) is None, x.get(sort_by)), reverse=reverse)
                app_logger.debug(f"Applied server-side sorting: {sort_by} {sort_order}")
            except Exception as e:
                app_logger.debug(f"Sorting failed: {e}")
        
        # Apply pagination: offset and limit
        paginated_rows = rows[offset:offset + limit]
        
        return jsonify({
            "rows": paginated_rows, 
            "total": total, 
            "query": query_text, 
            "collection_id": collection_id,
            "collection_name": collection_meta["name"]
        })
    except Exception as e:
        app_logger.error(f"Error getting collection rows for collection ID {collection_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to get collection rows"}), 500

# --- Case-file resolution -------------------------------------------------
# A case id is NOT unique across collections. An agent pack ships the same
# seeded cases to every account that installs it, so `case_<uuid>.json`
# legitimately exists under several `collection_<id>/` directories at once --
# plus any directory left behind by a collection that has since been removed.
# Scanning them and taking the first hit authorizes against whichever copy the
# filesystem happened to return, i.e. readdir order: it can serve another
# account's copy, or a copy stamped with a collection id nobody can access, and
# the answer differs between instances and between runs. The caller knows which
# repository it is inspecting, so it NAMES it and only that collection is read.

def _case_file_candidates(cases_dir: Path, case_id: str, collection_id=None) -> list:
    """Every on-disk copy of `case_id`, scoped to one collection when named.

    With `collection_id` given, only that collection's directory is searched.
    Without it, every collection directory is searched in SORTED order -- never
    readdir order -- so an unscoped read is at least reproducible.
    """
    if not cases_dir.exists():
        return []
    stem = case_id if case_id.startswith('case_') else f'case_{case_id}'
    paths = []
    if collection_id is not None:
        scoped = cases_dir / f"collection_{int(collection_id)}" / f"{stem}.json"
        if scoped.exists():
            paths.append(scoped)
    else:
        for collection_dir in sorted(cases_dir.glob("collection_*")):
            if collection_dir.is_dir():
                candidate = collection_dir / f"{stem}.json"
                if candidate.exists():
                    paths.append(candidate)
    # Legacy flat layout (predates collection directories). It carries no
    # collection attribution of its own, so it is only ever a last resort.
    flat = cases_dir / f"{stem}.json"
    if flat.exists():
        paths.append(flat)
    return paths


def _load_case_from_disk(cases_dir: Path, case_id: str, collection_id=None, rag_context=None):
    """Load the one copy of a case the caller may read.

    Returns `(case_data, resolved_collection_id, forbidden)`. `forbidden` is
    True when copies exist but none of them sits in a collection the caller can
    access -- a 403, which must stay distinguishable from a 404.
    """
    forbidden = False
    for path in _case_file_candidates(cases_dir, case_id, collection_id):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                case_data = json.load(f)
        except Exception as read_err:
            app_logger.warning(f"Unreadable case file '{path}': {read_err}")
            continue
        # When the caller named a collection, authorize against the collection it
        # ASKED for -- never against whatever id the file happens to record.
        stored = case_data.get('metadata', {}).get('collection_id', 0)
        resolved = int(collection_id) if collection_id is not None else stored
        if rag_context is not None:
            if not rag_context.validate_collection_access(resolved, write=False):
                forbidden = True
                continue
        return case_data, resolved, False
    return None, None, forbidden


# --- NEW ENDPOINT: Retrieve full case + associated turn summary ---
@api_bp.route("/rag/cases/<case_id>", methods=["GET"])
async def get_rag_case_details(case_id: str):
    """Returns full RAG case JSON and associated session turn summary.

    Query parameters:
      collection_id (int, optional): the repository the caller is inspecting.
        A case id is not unique across collections (see `_case_file_candidates`),
        so naming it is what makes this read deterministic and correctly scoped.

    Matching logic:
      1. Load the case from the named collection (disk, then that collection's
         own store) -- falling back to a scan only when no collection is named
      2. Extract session_id, turn_id, task_id from case metadata
      3. Search session logs, first by turn_id then by task_id
      4. Validate the user has read access to that collection
    """
    try:
        user_uuid = _get_user_uuid_from_request()
        if not user_uuid:
            return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401

        requested_collection_id = request.args.get('collection_id', type=int)

        project_root = Path(__file__).resolve().parents[3]
        cases_dir = project_root / 'rag' / 'tda_rag_cases'
        file_stem = case_id if case_id.startswith('case_') else f'case_{case_id}'

        retriever = APP_STATE.get('rag_retriever_instance')
        from trusted_data_agent.agent.rag_access_context import RAGAccessContext
        rag_context = RAGAccessContext(user_id=user_uuid, retriever=retriever) if retriever else None

        # --- Strategy 1: the collection's own case file on disk ---
        case_data, collection_id, forbidden = _load_case_from_disk(
            cases_dir, case_id, requested_collection_id, rag_context
        )
        loaded_from = 'file' if case_data is not None else None

        # --- Strategy 2: the collection's own store (imported collections keep
        # the case only in the vector store). `resolve_store` reads the
        # collection's OWN handle whatever server this process is serving -- the
        # retrieval map answers a different question and returns nothing for a
        # planner keyed elsewhere.
        if case_data is None and retriever:
            if requested_collection_id is not None:
                store = retriever.resolve_store(requested_collection_id, create=False)
                candidates = [(requested_collection_id, store)] if store is not None else []
            else:
                candidates = list(retriever.collections.items())
            for cid, collection in candidates:
                try:
                    result = collection.get(ids=[case_id], include=["metadatas"])
                    if result and result.get("ids") and len(result["ids"]) > 0:
                        meta = result["metadatas"][0]
                        full_case_str = meta.get("full_case_data", "{}")
                        loaded = json.loads(full_case_str) if full_case_str else {}
                        if rag_context and not rag_context.validate_collection_access(cid, write=False):
                            forbidden = True
                            continue
                        case_data = loaded
                        collection_id = cid
                        loaded_from = 'chromadb'
                        app_logger.info(f"Loaded case '{case_id}' from collection {cid} store")
                        break
                except Exception:
                    continue

        if case_data is None:
            if forbidden:
                app_logger.warning(
                    f"User {user_uuid} attempted to access case {case_id} "
                    f"(collection {requested_collection_id}) without read access"
                )
                return jsonify({"error": "You do not have access to this case."}), 403
            return jsonify({"error": f"Case '{file_stem}' not found."}), 404

        metadata = case_data.get('metadata', {})
        session_id = metadata.get('session_id')
        turn_id = metadata.get('turn_id')
        task_id = metadata.get('task_id')

        session_turn_summary = None
        join_method = None

        if session_id:
            # Locate the session (cross-user) and read its workflow history through
            # the backend-agnostic public API.
            from trusted_data_agent.core import session_manager
            try:
                owner = await session_manager.find_session_owner(session_id)
                if owner:
                    workflow_history = await session_manager.get_workflow_history(owner, session_id)
                    # 1. Try by turn_id
                    if turn_id is not None:
                        for entry in workflow_history:
                            if entry.get('turn') == turn_id:
                                session_turn_summary = entry
                                join_method = 'turn_id'
                                break
                    # 2. Fallback by task_id
                    if not session_turn_summary and task_id:
                        for entry in workflow_history:
                            if entry.get('task_id') == task_id:
                                session_turn_summary = entry
                                join_method = 'task_id'
                                break
            except Exception as se:
                app_logger.warning(f"Failed reading session '{session_id}': {se}")

        return jsonify({
            "case": case_data,
            "session_turn_summary": session_turn_summary,
            "join_method": join_method
        })
    except Exception as e:
        app_logger.error(f"Error retrieving RAG case details for '{case_id}': {e}", exc_info=True)
        return jsonify({"error": "Failed to retrieve case details"}), 500

@api_bp.route("/sessions", methods=["GET"])
@require_auth
async def get_sessions(current_user):
    """Returns a list of all active chat sessions for the requesting user.

    Query Parameters:
        all_users (bool): If true and user has VIEW_ALL_SESSIONS feature, returns sessions from all users
        limit (int): Maximum number of sessions to return (default: 50, use 0 for all)
        offset (int): Number of sessions to skip for pagination (default: 0)
        include_archived (bool): If true, includes archived sessions in results (default: false)
    """
    user_uuid = current_user.id

    # Parse pagination parameters
    limit_param = request.args.get('limit', '50')
    offset = request.args.get('offset', 0, type=int)

    # Handle limit: 0 means no limit (return all sessions)
    if limit_param == '0' or limit_param == 'all':
        limit = None
    else:
        try:
            limit = int(limit_param)
            if limit < 0:
                limit = 50  # Default to 50 if negative
        except (ValueError, TypeError):
            limit = 50  # Default to 50 if invalid

    # Check if requesting all users' sessions
    all_users = request.args.get('all_users', 'false').lower() == 'true'

    # Check if including archived sessions
    include_archived = request.args.get('include_archived', 'false').lower() == 'true'

    if all_users:
        # Check if user has VIEW_ALL_SESSIONS feature
        from trusted_data_agent.auth.service import get_user_features
        from trusted_data_agent.auth.features import Feature

        user_features = get_user_features(user_uuid)
        if Feature.VIEW_ALL_SESSIONS not in user_features:
            return jsonify({"status": "error", "message": "Insufficient permissions to view all sessions"}), 403

        # The scope travels with THIS call. Flipping the process-wide
        # APP_CONFIG.SESSIONS_FILTER_BY_USER for the duration of an await (as this did)
        # unscoped every concurrent request — another user's list AND single-session loads.
        result = await session_manager.get_all_sessions(user_uuid=user_uuid, limit=limit, offset=offset,
                                                        include_archived=include_archived, filter_by_user=False)
    else:
        result = await session_manager.get_all_sessions(user_uuid=user_uuid, limit=limit, offset=offset, include_archived=include_archived)

    # Extract sessions from result (new format returns dict with sessions, total_count, has_more)
    sessions = result.get("sessions", [])
    total_count = result.get("total_count", len(sessions))
    has_more = result.get("has_more", False)

    # Ensure profile_tags_used is included for each session
    for session in sessions:
        if 'profile_tags_used' not in session:
            session['profile_tags_used'] = []

    return jsonify({
        "sessions": sessions,
        "total_count": total_count,
        "has_more": has_more
    })

@api_bp.route("/session/<session_id>", methods=["GET"])
@require_auth
async def get_session_history(current_user, session_id):
    """Retrieves the chat history and token counts for a specific session."""
    user_uuid = current_user.id

    session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
    if session_data:
        # --- MODIFICATION START: Extract feedback from workflow_history ---
        feedback_by_turn = {}
        workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
        for turn in workflow_history:
            turn_num = turn.get("turn")
            feedback = turn.get("feedback")
            if turn_num is not None and feedback is not None:
                feedback_by_turn[turn_num] = feedback
        # --- MODIFICATION END ---
        
        # Get profile_type from profile if available
        profile_id = session_data.get("profile_id")
        profile_type = None
        if profile_id:
            from trusted_data_agent.core.config_manager import get_config_manager
            config_manager = get_config_manager()
            profile = config_manager.get_profile(profile_id, user_uuid)
            if profile:
                profile_type = profile.get("profile_type", "tool_enabled")

        # Extract dual-model information if available
        dual_model_info = None
        if session_data.get("is_dual_model_active"):
            dual_model_info = {
                "strategicProvider": session_data.get("strategic_provider"),
                "strategicModel": session_data.get("strategic_model"),
                "tacticalProvider": session_data.get("tactical_provider"),
                "tacticalModel": session_data.get("tactical_model")
            }

        response_data = {
            "history": session_data.get("session_history", []),
            "input_tokens": session_data.get("input_tokens", 0),
            "output_tokens": session_data.get("output_tokens", 0),
            "models_used": session_data.get("models_used", []),
            "profile_tags_used": session_data.get("profile_tags_used", []),
            "provider": session_data.get("provider"),
            "model": session_data.get("model"),
            "feedback_by_turn": feedback_by_turn,  # Add feedback data
            "profile_tag": session_data.get("profile_tag"),  # Current profile tag
            "profile_id": profile_id,  # Profile ID for resource panel updates
            "profile_type": profile_type,  # Profile type for resource panel updates
            "workflow_history": workflow_history,  # For genie card rendering
            "session_cost_usd": session_manager.session_cost_total(session_data),  # Σ every turn's cost — the ONE definition (see session_manager)
            "dual_model_info": dual_model_info,  # Dual-model configuration
            "dual_model_usage": session_data.get("dual_model_usage"),  # Dual-model usage breakdown
            "session_context_limit_override": session_data.get("session_context_limit_override"),  # Session-level context limit
            # S1.1 — the deployment streams its answers (the Living Answer): a reloaded turn then
            # rebuilds the same work line / receipt / verdict it wore live. Off ⇒ byte-identical.
            "living_answer": bool(getattr(APP_CONFIG, "ANSWER_STREAMING", False)),
        }
        # PROP-011 — the question this conversation is waiting on, with the owner's binding, so a
        # reloaded card is answerable (the turn record carries the question without the binding)
        try:
            from trusted_data_agent.core import input_request_store as _ir_store
            _pend = _ir_store.pending_for_session(session_id, user_uuid)
            if _pend:
                response_data["pending_input_request"] = _ir_store.public_view(
                    dict(_pend, resume=_ir_store.mint_resume(_pend)), with_resume=True)
            # …and every question this conversation ever asked, by id, in its LIVE state: a turn
            # record carries the question as it was raised (pending), so a reloaded card reads its
            # outcome from the store, never from the snapshot (a settled question rendered pending
            # bound the composer to it and the next message was refused as "already accepted")
            _all = _ir_store.list_for_user(user_uuid, status="all", session_id=session_id, limit=200)
            if _all:
                response_data["input_requests"] = {r["id"]: _ir_store.public_view(r) for r in _all}
        except Exception as _ie:  # noqa: BLE001
            app_logger.debug(f"needs-input: pending question not read: {_ie}")
        # S8 — the session's lineage (sibling sets + branches). Only served when the switch is on
        # AND something regenerated or branched: an untouched session serves today's payload.
        try:
            from trusted_data_agent.agent import turn_lineage as _tl
            if _tl.enabled():
                response_data["lineage_enabled"] = True   # the acts (Regenerate · Edit) exist here
            _lin = _tl.lineage_payload(session_data)
            if _lin:
                response_data["lineage"] = _lin
        except Exception as _le:  # noqa: BLE001
            app_logger.debug(f"turn-lineage: payload skipped: {_le}")
        return jsonify(response_data)
    app_logger.warning(f"Session {session_id} not found for user {user_uuid}.")
    return jsonify({"error": "Session not found"}), 404

@api_bp.route("/api/session/<session_id>/rename", methods=["POST"])
@require_auth
async def rename_session(current_user, session_id: str):
    """Renames a specific session for the requesting user."""
    user_uuid = current_user.id
    data = await request.get_json()
    new_name = data.get("newName")

    if not new_name or not isinstance(new_name, str) or len(new_name.strip()) == 0:
        return jsonify({"status": "error", "message": "Invalid or empty 'newName' provided."}), 400

    session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
    if not session_data:
        app_logger.warning(f"Rename failed: Session {session_id} not found for user {user_uuid}.")
        return jsonify({"status": "error", "message": "Session not found or access denied."}), 404

    try:
        await session_manager.update_session_name(user_uuid, session_id, new_name.strip())
        app_logger.info(f"User {user_uuid} renamed session {session_id} to '{new_name.strip()}'.")
        return jsonify({"status": "success", "message": "Session renamed successfully."}), 200
    except Exception as e:
        app_logger.error(f"Error renaming session {session_id} for user {user_uuid}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to update session name on the server."}), 500

@api_bp.route("/api/session/<session_id>", methods=["DELETE"])
@require_auth
async def delete_session_endpoint(current_user, session_id: str):
    """Deletes a specific session for the requesting user."""
    user_uuid = current_user.id
    app_logger.info(f"DELETE request received for session {session_id} from user {user_uuid}.")

    try:
        # Snapshot slave list before delete_session() clears the links, so we can
        # report the cascade outcome to the client. delete_session() now performs
        # the cascade itself, covering slaves from every coordinator that ran in
        # this session.
        slave_sessions = await session_manager.get_genie_slave_sessions(session_id, user_uuid)
        deleted_children = [s.get('slave_session_id') for s in slave_sessions if s.get('slave_session_id')]

        success = await session_manager.delete_session(
            user_uuid,
            session_id,
            archived_reason="User manually deleted session"
        )
        if not success:
            app_logger.error(f"session_manager.delete_session reported failure for session {session_id} (user {user_uuid}).")
            return jsonify({"status": "error", "message": "Failed to archive session file on the server."}), 500

        if deleted_children:
            app_logger.info(f"Cascade archived {len(deleted_children)} child sessions for Genie parent {session_id}")

        app_logger.info(f"Successfully processed archive request for session {session_id} (user {user_uuid}).")
        return jsonify({"status": "success", "message": "Session archived successfully.", "deleted_children": deleted_children}), 200
    except Exception as e:
        app_logger.error(f"Unexpected error during DELETE /api/session/{session_id} for user {user_uuid}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "An unexpected server error occurred during archiving."}), 500

# --- MODIFICATION START: Add new endpoint for purging session memory ---
@api_bp.route("/api/session/<session_id>/purge_memory", methods=["POST"])
@require_auth
async def purge_memory(current_user, session_id: str):
    """Purges the agent's LLM context memory (`chat_object`) for a session.
    For Genie sessions, also purges all child session memory."""
    user_uuid = current_user.id
    app_logger.info(f"Purge memory request for session {session_id}, user {user_uuid}")

    success = await session_manager.purge_session_memory(user_uuid, session_id)

    if success:
        # Also purge memory for any Genie child sessions
        slave_sessions = await session_manager.get_genie_slave_sessions(session_id, user_uuid)
        if slave_sessions:
            app_logger.info(f"Purging memory for {len(slave_sessions)} Genie child sessions")
            for slave in slave_sessions:
                slave_id = slave.get('slave_session_id')
                if slave_id:
                    await session_manager.purge_session_memory(user_uuid, slave_id)
                    app_logger.debug(f"Purged memory for child session: {slave_id}")

        app_logger.info(f"Agent memory purged successfully for session {session_id}, user {user_uuid}.")
        return jsonify({"status": "success", "message": "Agent memory purged."}), 200
    else:
        # This could be 404 (session not found) or 500 (save error), 404 is safer
        app_logger.warning(f"Failed to purge memory for session {session_id}, user {user_uuid}. Session may not exist or save failed.")
        return jsonify({"status": "error", "message": "Failed to purge session memory. Session not found or save error."}), 404
# --- MODIFICATION END ---

# --- MODIFICATION START: Add endpoints for /plan and /details ---
@api_bp.route("/api/session/<session_id>/turn/<int:turn_id>/plan", methods=["GET"])
@require_auth
async def get_turn_plan(current_user, session_id: str, turn_id: int):
    """Retrieves the original plan for a specific turn in a session."""
    user_uuid = current_user.id
    session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
    if not session_data:
        return jsonify({"error": "Session not found"}), 404

    workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
    if not isinstance(workflow_history, list) or turn_id <= 0 or turn_id > len(workflow_history):
        return jsonify({"error": f"Turn {turn_id} not found in session history."}), 404

    # Turns are 1-based for the user, but list index is 0-based
    turn_data = workflow_history[turn_id - 1]
    original_plan = turn_data.get("original_plan")

    if original_plan:
        return jsonify({"plan": original_plan})
    else:
        return jsonify({"error": f"Original plan not found for turn {turn_id}."}), 404

@api_bp.route("/api/session/<session_id>/turn/<int:turn_id>/details", methods=["GET"])
@require_auth
async def get_turn_details(current_user, session_id: str, turn_id: int):
    """Retrieves the full details (plan and trace) for a specific turn."""
    user_uuid = current_user.id
    session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
    if not session_data:
        return jsonify({"error": "Session not found"}), 404

    workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])

    # Validate turn_id (workflow_history now exists for all profile types)
    if not isinstance(workflow_history, list) or turn_id <= 0 or turn_id > len(workflow_history):
        return jsonify({"error": f"Turn {turn_id} not found in session history."}), 404

    # --- MODIFICATION START: Prioritize turn-level data over session-level ---
    turn_data = workflow_history[turn_id - 1]
    # Create a copy to send, ensuring we don't modify the session data in memory
    turn_data_copy = copy.deepcopy(turn_data)

    # Fallback for provider: use turn-specific, otherwise session-specific
    if "provider" not in turn_data_copy:
        turn_data_copy["provider"] = session_data.get("provider")

    # Fallback for model: use turn-specific, otherwise session-specific
    if "model" not in turn_data_copy:
        turn_data_copy["model"] = session_data.get("model")

    # PROP-006 — a statement's data source is recorded by id; the reader sees its NAME
    # (resolved now, so a renamed server reads correctly on an old turn). Only a turn that
    # read MORE than one source (or combined) is touched — a single-source turn passes unchanged.
    if isinstance(turn_data_copy.get("compute"), dict):
        try:
            from trusted_data_agent.core.source_endpoints import enrich_for_reader
            enrich_for_reader(turn_data_copy["compute"], user_uuid)
        except Exception as _e:  # noqa: BLE001
            app_logger.debug(f"turn details: source names unresolved ({_e})")

    # Add session-level token totals for display
    turn_data_copy["session_input_tokens"] = session_data.get("input_tokens", 0)
    turn_data_copy["session_output_tokens"] = session_data.get("output_tokens", 0)
    # Session cost the same way — a property of the SESSION, derived live from every turn's
    # cost (siblings of a regenerate included), never the turn's frozen snapshot: the
    # snapshot is "cost at the time this turn ran" and made the KPI move with which answer
    # was adopted while the token counters beside it did not.
    turn_data_copy["session_cost_usd"] = session_manager.session_cost_total(session_data)

    # Surface genie-slave context so the eval overlay can explain why an expert
    # sub-turn is scored on demand (not auto-scored) and that its cost rolls up into
    # the coordinator turn. The slave turn itself doesn't carry this; the session does.
    _gm = session_data.get("genie_metadata") or {}
    if isinstance(_gm, dict) and _gm.get("is_genie_slave"):
        turn_data_copy["is_genie_slave"] = True
        turn_data_copy["genie_parent_session_id"] = _gm.get("parent_session_id")

    # NEW: Ensure turn_cost is present (calculate if missing for legacy sessions)
    if "turn_cost" not in turn_data_copy:
        try:
            from trusted_data_agent.core.cost_manager import CostManager
            cost_manager = CostManager()
            turn_cost = cost_manager.calculate_cost(
                provider=turn_data_copy.get("provider"),
                model=turn_data_copy.get("model"),
                input_tokens=turn_data_copy.get("turn_input_tokens", 0),
                output_tokens=turn_data_copy.get("turn_output_tokens", 0)
            )
            turn_data_copy["turn_cost"] = turn_cost
            app_logger.debug(f"Calculated legacy turn cost for turn {turn_id}: ${turn_cost:.6f}")
        except Exception as e:
            app_logger.warning(f"Failed to calculate legacy turn cost: {e}")
            turn_data_copy["turn_cost"] = 0.0

    # Add dual-model information if available
    if session_data.get("is_dual_model_active"):
        turn_data_copy["dual_model_info"] = {
            "strategicProvider": session_data.get("strategic_provider"),
            "strategicModel": session_data.get("strategic_model"),
            "tacticalProvider": session_data.get("tactical_provider"),
            "tacticalModel": session_data.get("tactical_model")
        }

    # Return the copy with ensured model/provider info
    # ── the assurance path, derived (migration R-2) ───────────────────────────
    # Every intervention that could touch this answer, read from the turn's own stamps
    # by the ONE registry. Computed on read rather than persisted: no schema change, and
    # it can never be stale relative to the registry that produced it.
    #
    # Deriving it here — instead of in the renderer — is the point. The strip it replaces
    # carried its own ladder over the same facts, which is how ten stages were enumerated
    # while six real gates went unrendered.
    # SIGNED's per-turn reading — the `integrity` stamp — is sealed onto the turn AT
    # ANSWER TIME (executor.run() via the assurance tally; the genie coordinator at its
    # own turn assembly) and rides in with the persisted turn like every other trust
    # stamp. It is deliberately NOT resolved here: a live resolve at read time
    # substituted today's badge for yesterday's fact, so editing an agent flipped
    # every historical turn's SIGNED station to the CURRENT state — retroactive
    # falsification of a claim worded "when this answer was produced". A turn without
    # the stamp (older than the seal, or the trust suite absent) reads as an honest
    # "not recorded", never as today's state.

    # SCORED's reading must come from where the score actually LIVES — the online-eval
    # span sink, the same source the Quality card reads. Reading `turn["eval"]` instead
    # made the dial print "not in yet" directly above a Quality card saying "passing —
    # 20 checks": two surfaces disagreeing about one fact, which is worse than either
    # being silent.
    try:
        from trusted_data_agent.api.rest_routes import _read_online_spans
        _match = None
        for _sp in _read_online_spans(2000, user_uuid=user_uuid) or []:
            _a = _sp.get("attributes") or {}
            if _a.get("agent.kind") == "flow":
                continue
            if _a.get("session.id") == session_id and str(_a.get("turn.id")) == str(turn_id):
                _match = _sp                      # chronological — last match wins
        if _match is not None:
            _v = [e.get("attributes") for e in (_match.get("events") or [])
                  if isinstance(e, dict) and e.get("name") == "verdict" and e.get("attributes")]
            # The Guide is scoped by a compact per-dimension record — the SAME shape the
            # Quality card already uses (`data-dim-context`). Carrying it here is what
            # makes the Guide open on THIS turn's dimensions instead of all 52.
            _dims = []
            for _x in _v:
                _k = _x.get("dimension") or _x.get("name")
                if not _k:
                    continue
                # Two errors lived in this one line. `passed` is TRI-state — True /
                # False / None — and `if passed else` collapsed None into the else
                # branch, so every ABSTAINED dimension was labelled danger: metrics
                # that say in their own description they never convict a turn were
                # rendering as the most severe reading on the surface. And "pass" is
                # not a band; the vocabulary is healthy / review / danger / na, so it
                # matched nothing and fell through to the abstention path anyway.
                _passed = _x.get("passed")
                _band = _x.get("band")
                if not _band:
                    _band = ("healthy" if _passed is True
                             else "danger" if _passed is False else "na")
                _d = {"k": _k, "b": _band}
                if _x.get("score") is not None:
                    _d["s"] = _x.get("score")
                if _x.get("threshold") is not None:
                    _d["t"] = _x.get("threshold")
                _dims.append(_d)
            turn_data_copy["eval"] = {
                "scored": True,
                "checks": len(_v),
                "dimensions": _dims,
                # Flagged means "outside the healthy band", which is what the Quality
                # card counts. Using `passed is False` counted only hard failures, so the
                # dial read "all passed" beside a card reading "3 flagged for review" —
                # one fact, two surfaces, two answers.
                "failed_dimensions": [x.get("dimension") or x.get("name") for x in _v
                                      if x.get("band") in ("review", "danger")
                                      or x.get("passed") is False],
            }
    except Exception as _ee:  # noqa: BLE001 — a missing score is `pending`, not a failure
        app_logger.debug(f"[assurance] score unresolved for turn {turn_id}: {_ee}")

    # TRUSTED's reading: the agent's trailing behavioural state. Agent-scoped by
    # construction — it is a window measurement, not a per-turn one — but it is a REAL
    # reading, and rendering the station blank claimed there was nothing to say.
    try:
        from trusted_data_agent.eval import trust_behavior as _tb
        _tag = turn_data_copy.get("profile_tag") or session_data.get("profile_tag")
        if _tag:
            from trusted_data_agent.api.rest_routes import _read_online_spans as _ros
            _states = _tb.assess_agents(_ros(2000, user_uuid=user_uuid) or []) or {}
            _cell = _states.get(_tag) or {}
            _comp = (_cell.get("composite") or {}) if isinstance(_cell, dict) else {}
            _beh = (_cell.get("behavior") or {}) if isinstance(_cell, dict) else {}
            if _comp or _beh:
                turn_data_copy["trailing_trust"] = {
                    "state": _comp.get("state") or _beh.get("state"),
                    "turns": _beh.get("turns"),
                }
    except Exception as _be:  # noqa: BLE001 — feature off or absent
        app_logger.debug(f"[assurance] trailing trust unresolved: {_be}")

    try:
        from trusted_data_agent.agent import assurance_points as _ap
        _cls = (turn_data_copy.get("profile_type")
                or session_data.get("profile_type") or "tool_enabled")
        _readings = _ap.read_turn(turn_data_copy, _cls)
        turn_data_copy["assurance"] = {
            "profile_class": _cls,
            # Pre-tracing turns say so, so the renderer never has to guess why a gate
            # is silent (invariant V4).
            "predates_tracing": _ap.turn_predates_tracing(turn_data_copy),
            "points": [r.as_dict() for r in _readings],
            "by_station": {k: [r.as_dict() for r in v]
                           for k, v in _ap.read_turn_by_station(turn_data_copy, _cls).items()},
            # A station's SECOND population — the measurements it owns, allocated by
            # the registry partition rather than delivered as cargo on whichever
            # mechanisms happened to render. Each record carries `st`, its contribution
            # in the state vocabulary, so the ring folds it straight through its rank
            # and converts no bands. Absent ⇒ no measurements yet (a live turn is scored
            # after the answer), which is the truth, not a gap.
            "measures": _ap.read_turn_measures(turn_data_copy, _cls),
            "stations": list(_ap.STATIONS),
        }
    except Exception as _ae:  # noqa: BLE001 — a reading must never break the trace view
        app_logger.debug(f"[assurance] could not derive readings for turn {turn_id}: {_ae}")

    # OBDA-ACT — the turn's acts folded with the confirmations a person gave afterwards, through
    # the ONE `action_perform.summarize` the BOUNDED reading uses, so the Live Status cards
    # synthesized on reload and the capsule's Actions row read the same acts. Absent ⇒ no key.
    if turn_data_copy.get("actions") or turn_data_copy.get("action_confirmations"):
        try:
            from trusted_data_agent.core.action_perform import summarize as _act_sum, act_words as _act_words
            _acts = _act_sum(turn_data_copy.get("actions") or [],
                             turn_data_copy.get("action_confirmations") or {})["acts"]
            turn_data_copy["action_acts"] = [
                {**{k: v for k, v in a.items() if k not in ("checks", "receipt", "before")},
                 "words": _act_words(a)} for a in _acts]
        except Exception as _xe:  # noqa: BLE001
            app_logger.debug(f"[act] could not fold the turn's acts: {_xe}")

    return jsonify(turn_data_copy)
    # --- MODIFICATION END ---


@api_bp.route("/api/session/<session_id>/turn/<int:turn_id>/query", methods=["GET"])
async def get_turn_query(session_id: str, turn_id: int):
    """Retrieves the original user query for a specific turn in a session."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401
    session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
    if not session_data:
        return jsonify({"error": "Session not found"}), 404

    workflow_history = session_data.get("last_turn_data", {}).get("workflow_history", [])
    if not isinstance(workflow_history, list) or turn_id <= 0 or turn_id > len(workflow_history):
        return jsonify({"error": f"Turn {turn_id} not found in session history."}), 404

    # Turns are 1-based for the user, but list index is 0-based
    turn_data = workflow_history[turn_id - 1]
    original_query = turn_data.get("user_query")

    if original_query:
        return jsonify({"query": original_query})
    else:
        # Should ideally always be present, but handle gracefully
        return jsonify({"error": f"Original query not found for turn {turn_id}."}), 404

@api_bp.route("/session", methods=["POST"])
async def new_session():
    """Creates a new chat session for the requesting user."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401

    # Get default profile to determine validation requirements
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.core import configuration_service
    config_manager = get_config_manager()
    default_profile_id = config_manager.get_default_profile_id(user_uuid)

    # Determine profile type to know what validation is needed
    default_profile = None
    profile_type = "tool_enabled"  # Default for backward compatibility

    if default_profile_id:
        profiles = config_manager.get_profiles(user_uuid)
        default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)
        if default_profile:
            profile_type = default_profile.get("profile_type", "tool_enabled")

    # Validate based on profile type
    # LLM is always required, MCP only required for Efficiency Focused profiles
    needs_mcp = (profile_type == "tool_enabled")

    if not APP_STATE.get('llm') or (needs_mcp and not APP_CONFIG.MCP_SERVER_CONNECTED):
        # Try to initialize and validate with default profile
        if default_profile_id:
            app_logger.info(f"Validating {'LLM+MCP' if needs_mcp else 'LLM'} for session creation using profile {default_profile_id} (type: {profile_type})")
            result = await configuration_service.switch_profile_context(default_profile_id, user_uuid, validate_llm=True)
            app_logger.info(f"Validation result: {result}")
            if result["status"] != "success":
                error_msg = f"Failed to validate LLM configuration: {result['message']}"
                app_logger.warning(f"Returning 400 error to client: {error_msg}")
                return jsonify({"error": error_msg}), 400
        else:
            return jsonify({"error": "Application not configured. Please set LLM details in Config."}), 400

    # Double-check after validation attempt
    # LLM always required, MCP only for Efficiency Focused profiles
    if not APP_STATE.get('llm'):
        return jsonify({"error": "Application not configured. Please set LLM details in Config."}), 400

    if needs_mcp and not APP_CONFIG.MCP_SERVER_CONNECTED:
        return jsonify({"error": "Efficiency Focused profile requires MCP server. Please configure MCP details in Config."}), 400

    try:
        loggers_to_purge = ["llm_conversation", "llm_conversation_history"]
        for logger_name in loggers_to_purge:
            logger = logging.getLogger(logger_name)
            for handler in logger.handlers[:]:
                if isinstance(handler, logging.FileHandler):
                    log_file_path = handler.baseFilename
                    handler.close()
                    logger.removeHandler(handler)
                    try:
                        with open(log_file_path, 'w'):
                            pass
                        app_logger.info(f"Successfully purged log file: {log_file_path}")
                        logger.addHandler(handler)
                    except Exception as clear_e:
                        app_logger.error(f"Failed to clear log file {log_file_path}: {clear_e}. Attempting to re-add handler anyway.")
                        logger.addHandler(handler)
    except Exception as e:
        app_logger.error(f"Failed to purge log files for new session: {e}", exc_info=True)


    data = await request.get_json()
    charting_intensity = APP_CONFIG.DEFAULT_CHARTING_INTENSITY if APP_CONFIG.CHARTING_ENABLED else "none"
    system_prompt_template = data.get("system_prompt")
    profile_override_id = data.get("profile_override_id")  # For session primer to use overridden profile

    # Get profile tag and LLM config from DEFAULT profile (not first active)
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.core.config import set_user_mcp_server_id, set_user_mcp_client
    config_manager = get_config_manager()
    default_profile_id = config_manager.get_default_profile_id(user_uuid)
    profile_tag = None
    profile_provider = APP_CONFIG.CURRENT_PROVIDER  # Fallback to global config

    # Always fetch profiles - needed for default profile and potential profile override
    profiles = config_manager.get_profiles(user_uuid)

    # Check for profile override FIRST - use override profile's tag if present
    override_profile = None
    if profile_override_id:
        override_profile = next((p for p in profiles if p.get("id") == profile_override_id), None)
        if override_profile:
            profile_tag = override_profile.get("tag")
            app_logger.info(f"Using overridden profile tag for session: @{profile_tag} (from profile override)")

    if default_profile_id:
        default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)
        if default_profile:
            # Only use default profile tag if no override is active
            if not override_profile:
                profile_tag = default_profile.get("tag")

            # CRITICAL: Initialize MCP server ID AND CLIENT from profile for deterministic behavior
            # This ensures new sessions always use the profile's configured MCP server,
            # not leftover state from profile overrides in previous sessions
            profile_mcp_server_id = default_profile.get('mcpServerId')
            if profile_mcp_server_id:
                # Update server ID
                set_user_mcp_server_id(profile_mcp_server_id, user_uuid)

                # CRITICAL: Also update the MCP client to point to the pooled client for this server
                # Without this, APP_STATE['mcp_client'] still points to the previous server's client
                client_pool = APP_STATE.get('mcp_client_pool', {})
                pooled_client = client_pool.get(profile_mcp_server_id)

                if pooled_client:
                    set_user_mcp_client(pooled_client, user_uuid)
                    app_logger.info(f"✅ Initialized MCP from default profile: server_id={profile_mcp_server_id}, client=POOLED")
                else:
                    app_logger.warning(f"⚠️ MCP server {profile_mcp_server_id} not in connection pool (will initialize on first use)")
            else:
                app_logger.warning(f"⚠️ Default profile has no mcpServerId configured")

            # Get provider from the profile's LLM configuration
            llm_config_id = default_profile.get('llmConfigurationId')
            if llm_config_id:
                llm_configs = config_manager.get_llm_configurations(user_uuid)
                llm_config = next((cfg for cfg in llm_configs if cfg['id'] == llm_config_id), None)
                if llm_config:
                    profile_provider = llm_config.get('provider', APP_CONFIG.CURRENT_PROVIDER)
                    app_logger.info(f"Creating session with default profile: {default_profile.get('name')} (@{profile_tag}), provider: {profile_provider}")
                else:
                    app_logger.warning(f"LLM config {llm_config_id} not found, using global provider")
            else:
                app_logger.info(f"Creating session with default profile: {default_profile.get('name')} (@{profile_tag}), no LLM config (using global)")
        else:
            app_logger.warning(f"Default profile ID {default_profile_id} not found in profiles list")

    try:
        # Determine profile_id: use override if present, otherwise default
        active_profile_id = profile_override_id if profile_override_id else default_profile_id

        session_id = await session_manager.create_session(
            user_uuid=user_uuid,
            provider=profile_provider,
            llm_instance=APP_STATE.get('llm'),
            charting_intensity=charting_intensity,
            system_prompt_template=system_prompt_template,
            profile_tag=profile_tag,
            profile_id=active_profile_id
        )
        app_logger.info(f"Created new session: {session_id} for user {user_uuid} with profile_tag {profile_tag}, profile_id {active_profile_id}.")

        # Check if the profile has a session primer
        # Use overridden profile if provided (profile override awareness), otherwise use default
        session_primer = None
        # override_profile was already looked up earlier (before session creation)
        primer_profile = override_profile if override_profile else default_profile

        if override_profile:
            app_logger.info(f"Using overridden profile for session primer: {override_profile.get('name')} (id: {profile_override_id})")

        if primer_profile:
            _sp = primer_profile.get('session_primer')
            # Respect the enabled flag — a dict with enabled:False is falsy for our purposes
            if isinstance(_sp, dict):
                session_primer = _sp if _sp.get('enabled', False) else None
            elif _sp:
                session_primer = _sp  # legacy string form — always active
            if session_primer:
                app_logger.info(f"Session {session_id} has session primer configured from profile '{primer_profile.get('name')}', will execute on frontend init")

        response_data = {
            "id": session_id,
            "name": "New Chat",
            "profile_tags_used": [],  # Populated on first query execution, not on session creation
            "profile_tag": profile_tag,  # Current profile tag for display
            "models_used": [],
            # Include profile metadata for resource panel updates
            "profile_id": profile_override_id if profile_override_id else default_profile_id,
            "profile_type": primer_profile.get("profile_type") if primer_profile else "tool_enabled"
        }

        # Include session_primer if configured - frontend will auto-execute it
        if session_primer:
            response_data["session_primer"] = session_primer
            # Also include the profile override ID so frontend can pass it to primer execution
            if profile_override_id:
                response_data["profile_override_id"] = profile_override_id

        return jsonify(response_data)
    except Exception as e:
        app_logger.error(f"Failed to create new session for user {user_uuid}: {e}", exc_info=True)
        return jsonify({"error": f"Failed to initialize a new chat session: {e}"}), 500

@api_bp.route("/models", methods=["POST"])
async def get_models():
    """Fetches the list of available models from the selected provider."""
    try:
        data = await request.get_json()
        provider = data.get("provider")
        credentials = { "listing_method": data.get("listing_method", "foundation_models") }
        if provider == 'Azure':
            credentials["azure_deployment_name"] = data.get("azure_deployment_name")
        elif provider == 'Amazon':
            credentials.update({
                "aws_access_key_id": data.get("aws_access_key_id"),
                "aws_secret_access_key": data.get("aws_secret_access_key"),
                "aws_region": data.get("aws_region")
            })
        elif provider == 'Friendli':
            credentials.update({
                "friendli_token": data.get("friendli_token"),
                "friendli_endpoint_url": data.get("friendli_endpoint_url")
            })
        elif provider == 'Ollama':
            # Support multiple host key formats for compatibility
            host_keys = ["ollama_host", "ollamaHost", "host"]
            ollama_host = next((data.get(key) for key in host_keys if data.get(key) is not None), None)
            credentials["host"] = ollama_host
        elif provider == 'XAI':
            credentials["xai_api_key"] = data.get("xai_api_key")
        elif provider == 'Celeris':
            credentials["celeris_api_key"] = data.get("celeris_api_key")
            credentials["celeris_base_url"] = data.get("celeris_base_url")
        elif provider == 'MLX':
            credentials["mlx_host"] = data.get("mlx_host", "http://localhost:8080")
        else:
            credentials["apiKey"] = data.get("apiKey")

        models = await llm_handler.list_models(provider, credentials)
        return jsonify({"status": "success", "models": models})
    except Exception as e:
        app_logger.error(f"Failed to list models for provider {provider}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 400

@api_bp.route("/system_prompt/<provider>/<path:model_name>", methods=["GET"])
async def get_default_system_prompt(provider, model_name):
    """Gets the system prompt for a given model using profile-based mapping."""
    try:
        from trusted_data_agent.agent.prompt_loader import get_prompt_loader
        from trusted_data_agent.agent.prompt_mapping import get_prompt_for_category
        from trusted_data_agent.config.configuration_manager import config_manager
        from trusted_data_agent.auth.middleware import get_current_user
        
        # Get user's UUID from session/auth
        current_user = await get_current_user()
        user_uuid = current_user.id if current_user else None
        
        # Get user's default profile to use their custom mappings
        profile_id = "__system_default__"  # Fallback
        if user_uuid:
            default_profile_id = config_manager.get_default_profile_id(user_uuid)
            if default_profile_id:
                profile_id = default_profile_id
                app_logger.info(f"Using profile {profile_id} for system prompt resolution")
        
        # Use prompt mapping system to resolve provider → prompt name
        prompt_name = get_prompt_for_category(
            profile_id=profile_id,
            category="master_system_prompts",
            subcategory=provider
        )
        
        if not prompt_name:
            # Fallback to default if mapping not found
            prompt_name = "MASTER_SYSTEM_PROMPT"
            app_logger.warning(f"No mapping found for provider {provider}, using default")
        
        app_logger.info(f"Resolved prompt for {provider}: {prompt_name} (profile: {profile_id})")
        
        # Load from database (gets active version of the mapped prompt)
        loader = get_prompt_loader()
        prompt_content = loader.get_prompt(prompt_name)
        
        return jsonify({"status": "success", "system_prompt": prompt_content})
    except Exception as e:
        app_logger.error(f"Failed to load system prompt for {provider}: {e}")
        # Fallback to old system if database fails
        base_prompt_template = str(PROVIDER_SYSTEM_PROMPTS.get(provider, PROVIDER_SYSTEM_PROMPTS["Google"]))
        return jsonify({"status": "success", "system_prompt": base_prompt_template})

@api_bp.route("/configure", methods=["POST"])
async def configure_services():
    """
    Configures and validates the core LLM and MCP services from the UI.
    This is now a thin, protected wrapper around the centralized configuration service.
    """
    data_from_ui = await request.get_json()
    if not data_from_ui:
        return jsonify({"status": "error", "message": "Request body must be a valid JSON."}), 400
    
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401

    # Check if credentials are nested (new format) or flat (old format)
    creds = data_from_ui.get("credentials", {})
    
    host_keys = ["ollama_host", "ollamaHost", "host"]
    ollama_host_value = next((creds.get(key) or data_from_ui.get(key) for key in host_keys if creds.get(key) or data_from_ui.get(key)), None)

    # Get MCP server config - support both nested and flat formats
    mcp_server_data = data_from_ui.get("mcp_server", {})
    
    service_config_data = {
        "provider": data_from_ui.get("provider"),
        "model": data_from_ui.get("model"),
        "user_uuid": user_uuid,
        "credentials": {
            "apiKey": creds.get("apiKey") or data_from_ui.get("apiKey"),
            "aws_access_key_id": creds.get("aws_access_key_id") or data_from_ui.get("aws_access_key_id"),
            "aws_secret_access_key": creds.get("aws_secret_access_key") or data_from_ui.get("aws_secret_access_key"),
            "aws_region": creds.get("aws_region") or data_from_ui.get("aws_region"),
            "ollama_host": ollama_host_value,
            "azure_api_key": creds.get("azure_api_key") or data_from_ui.get("azure_api_key"),
            "azure_endpoint": creds.get("azure_endpoint") or data_from_ui.get("azure_endpoint"),
            "azure_deployment_name": creds.get("azure_deployment_name") or data_from_ui.get("azure_deployment_name"),
            "azure_api_version": creds.get("azure_api_version") or data_from_ui.get("azure_api_version"),
            "listing_method": creds.get("listing_method") or data_from_ui.get("listing_method", "foundation_models"),
            "friendli_token": creds.get("friendli_token") or data_from_ui.get("friendli_token"),
            "friendli_endpoint_url": creds.get("friendli_endpoint_url") or data_from_ui.get("friendli_endpoint_url")
        },
        "mcp_server": {
            "name": mcp_server_data.get("name") or data_from_ui.get("server_name"),
            "id": mcp_server_data.get("id") or data_from_ui.get("server_id"),
            "host": mcp_server_data.get("host") or data_from_ui.get("host"),
            "port": mcp_server_data.get("port") or data_from_ui.get("port"),
            "path": mcp_server_data.get("path") or data_from_ui.get("path")
        }
    }
    service_config_data["credentials"] = {k: v for k, v in service_config_data["credentials"].items() if v is not None}

    result = await configuration_service.setup_and_categorize_services(service_config_data)

    if result.get("status") == "success":
        return jsonify(result), 200
    else:
        return jsonify(result), 500

@api_bp.route("/test-mcp-connection", methods=["POST"])
async def test_mcp_connection():
    """
    Tests MCP server connectivity without performing full configuration.
    This allows users to validate individual MCP server settings before activation.
    Supports both SSE/HTTP and stdio transports.
    """
    data = await request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Request body must be valid JSON."}), 400

    # Use server ID if provided, otherwise generate a temporary one for testing
    import uuid
    server_id = data.get("id") or f"temp-{uuid.uuid4()}"
    server_name = data.get("name", "Test Server")  # For logging only

    try:
        from trusted_data_agent.core.configuration_service import build_mcp_server_config

        # Build server config based on transport type
        app_logger.info(f"Testing MCP connection for '{server_name}' (ID: {server_id})")
        temp_server_configs = build_mcp_server_config(server_id, data)

        # Create temporary MCP client
        temp_mcp_client = MultiServerMCPClient(temp_server_configs)

        # Test connection by listing tools
        async with temp_mcp_client.session(server_id) as temp_session:
            tools_result = await temp_session.list_tools()
            tool_count = len(tools_result.tools) if hasattr(tools_result, 'tools') else 0

        app_logger.info(f"MCP connection test successful for '{server_name}' (ID: {server_id}). Found {tool_count} tools.")
        return jsonify({
            "status": "success",
            "message": f"Connection successful! Found {tool_count} tools.",
            "tool_count": tool_count
        }), 200

    except Exception as e:
        root_exception = unwrap_exception(e)
        error_message = ""

        transport_info = data.get('transport', {})
        transport_type = transport_info.get('type', 'sse')

        if isinstance(root_exception, (httpx.ConnectTimeout, httpx.ConnectError)):
            host = data.get("host")
            port = data.get("port")
            error_message = f"Connection failed: Cannot reach server at {host}:{port}. Please verify host and port."
        elif transport_type == 'stdio':
            error_message = f"Connection test failed: {str(root_exception)}. Check if command is installed and executable."
        else:
            error_message = f"Connection test failed: {str(root_exception)}"

        app_logger.error(f"MCP connection test failed for '{server_name}': {error_message}", exc_info=True)
        return jsonify({"status": "error", "message": error_message}), 500

#: Keepalive interval for the two per-request answer streams. A long tool call leaves
#: the stream silent — no event is produced until the warehouse returns — and an idle
#: connection is exactly what a reverse proxy in front of the server drops (the nginx
#: at tda.uderia.com did, and the browser reported a "connection timeout" while the
#: backend kept working). The notification stream has pinged every 20 s since it was
#: written; these two streams never did. An SSE comment line is ignored by every
#: consumer, so it is safe to emit at any point between events.
SSE_KEEPALIVE_SECONDS = 20.0


async def _pump_with_keepalive(queue: "asyncio.Queue", interval: float = SSE_KEEPALIVE_SECONDS):
    """Yield queued SSE items until the ``None`` sentinel, emitting a ``: ping`` comment
    whenever ``interval`` seconds pass with nothing to send. Pure over the queue, so it
    is testable without the app."""
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=interval)
        except asyncio.TimeoutError:
            yield ": ping\n\n"
            continue
        if item is None:
            break
        yield item


@api_bp.route("/ask_stream", methods=["POST"])
async def ask_stream():
    """Handles the main chat conversation stream for ad-hoc user queries."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        async def error_gen():
            yield PlanExecutor._format_sse({"error": "Authentication required. Please login."}, "error")
        return Response(error_gen(), mimetype="text/event-stream")

    # Check consumption limits before allowing execution
    try:
        from trusted_data_agent.auth.consumption_enforcer import ConsumptionEnforcer
        from trusted_data_agent.auth.models import User
        from trusted_data_agent.auth.database import get_db_session
        
        # Check if user is admin - admins bypass consumption checks
        with get_db_session() as session:
            user = session.query(User).filter_by(id=user_uuid).first()
            is_admin = user.is_admin if user else False
        
        if not is_admin:
            enforcer = ConsumptionEnforcer(user_uuid)
            can_proceed, error_message = enforcer.can_execute_prompt()
            
            if not can_proceed:
                app_logger.warning(f"Consumption limit exceeded for user {user_uuid}: {error_message}")
                async def limit_error_gen():
                    yield PlanExecutor._format_sse({
                        "error": error_message,
                        "type": "rate_limit_exceeded"
                    }, "error")
                return Response(limit_error_gen(), mimetype="text/event-stream")
    except Exception as e:
        app_logger.error(f"Error checking consumption limits for user {user_uuid}: {e}", exc_info=True)
        # Fail open - allow execution if consumption check fails
        pass

    data = await request.get_json()
    user_input = data.get("message")
    session_id = data.get("session_id")
    disabled_history = data.get("disabled_history", False)
    source = data.get("source", "text")
    # --- MODIFICATION START: Receive optional plan and replay flag ---
    plan_to_execute = data.get("plan_to_execute") # Plan object or null
    is_replay = data.get("is_replay", False) # Boolean flag
    display_message = data.get("display_message") # Optional message for history
    # --- MODIFICATION END ---
    # --- MODIFICATION START: Receive optional profile override ---
    profile_override_id = data.get("profile_override_id") # Profile ID for temporary override
    # --- MODIFICATION END ---
    # --- MODIFICATION START: Receive session primer flag ---
    is_session_primer = data.get("is_session_primer", False) # True if this is a session primer execution
    # --- MODIFICATION END ---
    # PROP-011 — the person answers a pending question from the chat: the card (or the composer
    # rule) put the binding in `input_answer`; the act is validated and consumed ONCE here, and
    # the turn runs with the structured block as its input and the person's line on screen.
    input_answer = None
    _ia = data.get("input_answer")
    if isinstance(_ia, dict) and _ia.get("input_request_id"):
        from trusted_data_agent.core import input_resume as _resume
        _res, _why, _code = _resume.accept(user_uuid, str(_ia.get("input_request_id")),
                                           decision=_ia.get("decision") or "accept", values=_ia.get("values"),
                                           resume=_ia.get("resume"), via="chat")
        if _res is None:
            async def _ir_error_gen():
                yield PlanExecutor._format_sse({"error": f"The answer could not be used: {_why}",
                                                "type": "input_request_refused"}, "error")
            return Response(_ir_error_gen(), mimetype="text/event-stream")
        _res = await _resume.with_original_query(_res)
        await _resume.remember(_res, _ia.get("remember"))   # decision 10: session · agent · None
        user_input = _res["user_input"]
        display_message = display_message or _res["display_message"]
        input_answer = _res["input_answer"]
        if not session_id:
            session_id = _res.get("session_id")
    # --- Document upload attachments ---
    attachments = data.get("attachments")  # Optional list of [{file_id, filename, ...}]
    # --- Post-processing extensions ---
    extension_specs = data.get("extensions")  # Optional list of [{"name": "json", "param": null}]
    # --- Pre-processing skills ---
    skill_specs = data.get("skills")  # Optional list of [{"name": "sql-expert", "param": "strict"}]
    # --- Canvas bidirectional context ---
    canvas_context = data.get("canvas_context")  # Optional {title, language, content, modified}
    # S8 — the UI's regenerate / edit-and-resend: a relation the execution seam validates and arms
    turn_relation = None
    if isinstance(data.get("regenerate_of"), int):
        turn_relation = {"kind": "regenerate", "of": data["regenerate_of"]}
    elif isinstance(data.get("edit_of"), int):
        turn_relation = {"kind": "edit", "of": data["edit_of"], "prompt": user_input}

    session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
    # S8 review: a regenerate with no explicit profile is a regenerate under the turn's OWN agent
    # (the REST regenerate resolves it this way; the UI's "Same agent" ran under the default)
    if session_data and turn_relation and turn_relation.get("kind") == "regenerate" and not profile_override_id:
        try:
            from trusted_data_agent.agent import turn_lineage as _tl
            from trusted_data_agent.core.config_manager import get_config_manager as _gcm
            _root_rec = _tl.find_turn(session_data, turn_relation["of"]) or {}
            _root_tag = _root_rec.get("profile_tag")
            if _root_tag:
                _rp = next((p for p in (_gcm().get_profiles(user_uuid) or []) if p.get("tag") == _root_tag), None)
                if _rp and _rp.get("id"):
                    profile_override_id = _rp.get("id")
        except Exception as _e:  # noqa: BLE001
            app_logger.debug(f"[lineage] regenerate profile resolution skipped: {_e}")
    if not session_data:
        app_logger.error(f"ask_stream denied: Session {session_id} not found for user {user_uuid}.")
        async def error_gen():
            yield PlanExecutor._format_sse({"error": "Session not found or invalid."}, "error")
        return Response(error_gen(), mimetype="text/event-stream")

    # Get profile tag from active profile (NOT from override)
    # Note: If profile_override_id is set, the executor will handle updating the session
    # with the override profile only if it succeeds. We should not pre-emptively add
    # the override tag here, as it may fail during executor initialization.
    from trusted_data_agent.core.config_manager import get_config_manager
    config_manager = get_config_manager()

    # Determine active profile type to conditionally validate MCP requirement
    # Check profile override first, then fall back to default profile
    active_profile = None
    if profile_override_id:
        profiles = config_manager.get_profiles(user_uuid)
        active_profile = next((p for p in profiles if p.get("id") == profile_override_id), None)

    if not active_profile:
        # Get default profile
        default_profile_id = config_manager.get_default_profile_id(user_uuid)
        if default_profile_id:
            profiles = config_manager.get_profiles(user_uuid)
            active_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)

    # CRITICAL FIX: Switch profile context to reload tools when profile changes
    # This ensures TDA_* tools and MCP tools are correctly loaded for the active profile
    # Fixes hallucinated tool names when switching from override back to default
    # IMPORTANT: Use profile_id directly, not active_profile object which might be None due to cache issues
    from trusted_data_agent.core import configuration_service

    # Determine which profile ID to use
    profile_id_to_load = None
    if profile_override_id:
        profile_id_to_load = profile_override_id
        app_logger.debug(f"Using profile override: {profile_override_id}")
    else:
        # Get default profile - don't rely on active_profile object which might be None
        default_profile_id = config_manager.get_default_profile_id(user_uuid)
        if default_profile_id:
            profile_id_to_load = default_profile_id
            app_logger.debug(f"Using default profile: {default_profile_id}")

    # Always switch profile context if we have a profile_id
    if profile_id_to_load:
        profile_context = await configuration_service.switch_profile_context(
            profile_id_to_load, user_uuid, validate_llm=False
        )
        if "error" not in profile_context:
            app_logger.info(f"✅ Switched profile context for query execution (profile: {profile_id_to_load})")
        else:
            app_logger.warning(f"Failed to switch profile context: {profile_context.get('error')}")
    else:
        app_logger.warning("No profile ID available to switch context!")

    # Get profile type (default to tool_enabled for backward compatibility)
    active_profile_type = active_profile.get("profile_type", "tool_enabled") if active_profile else "tool_enabled"

    # Only validate MCP tools for tool-enabled profiles
    # LLM-only profiles bypass planner/tools and execute directly
    if active_profile_type == "tool_enabled":
        # For profile overrides, check if the profile itself has MCP configured
        # For default profiles, check if MCP tools are loaded in APP_STATE
        if profile_override_id:
            # Profile override: check if profile has mcpServerId configured
            # The executor will load the MCP tools for this profile later
            mcp_server_id = active_profile.get("mcpServerId") if active_profile else None
            if not mcp_server_id:
                app_logger.error(f"Profile override {profile_override_id} is tool_enabled but has no MCP server configured")
                async def error_gen():
                    yield PlanExecutor._format_sse({
                        "error": "The selected profile requires MCP tools but has no MCP server configured. Please configure an MCP server in the profile settings."
                    }, "error")
                return Response(error_gen(), mimetype="text/event-stream")
        else:
            # Default profile: check if MCP tools are loaded in APP_STATE
            if not APP_STATE.get('mcp_tools'):
                async def error_gen():
                    yield PlanExecutor._format_sse({
                        "error": "The agent is not fully configured. Please ensure the LLM and MCP server details are set correctly in the 'Config' tab before starting a chat."
                    }, "error")
                return Response(error_gen(), mimetype="text/event-stream")

    # Validate knowledge collections for RAG focused profiles
    if active_profile_type == "rag_focused":
        knowledge_config = active_profile.get("knowledgeConfig", {}) if active_profile else {}
        knowledge_collections = knowledge_config.get("collections", [])

        if not knowledge_collections or len(knowledge_collections) == 0:
            app_logger.warning(f"RAG focused profile has no knowledge collections configured for user {user_uuid}")
            async def error_gen():
                yield PlanExecutor._format_sse({
                    "error": "RAG focused profiles require at least 1 knowledge collection. Please configure knowledge collections in the profile settings.",
                    "error_type": "missing_knowledge_collections"
                }, "error")
            return Response(error_gen(), mimetype="text/event-stream")

    # Always use default profile here - executor will update if override succeeds
    default_profile_id = config_manager.get_default_profile_id(user_uuid)
    if default_profile_id:
        profiles = config_manager.get_profiles(user_uuid)
        default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)
        if default_profile:
            profile_tag = default_profile.get("tag")
        else:
            # Profile not found - log error and preserve existing session value
            app_logger.error(f"Default profile {default_profile_id} not found in profiles list for user {user_uuid}")
            # Don't set profile_tag to None - fetch from existing session to avoid corruption
            existing_session_data = await session_manager.get_session(user_uuid, session_id)
            profile_tag = existing_session_data.get('profile_tag') if existing_session_data else None
    else:
        profile_tag = None

    # Only update session if no profile override is active AND we have a valid profile_tag
    # If profile override is active, let executor handle the update after validation
    # If profile_tag is None (profile lookup failed), don't corrupt existing session data
    if not profile_override_id and profile_tag is not None:
        await session_manager.update_models_used(user_uuid=user_uuid, session_id=session_id, provider=APP_CONFIG.CURRENT_PROVIDER, model=APP_CONFIG.CURRENT_MODEL, profile_tag=profile_tag)

    # --- MODIFICATION START: Initialize user's LLM instance for chat route ---
    # Similar to REST routes, ensure user's LLM instance is created and loaded into APP_STATE
    # This fixes "LLM is not initialized" errors when using chat UI
    from trusted_data_agent.core.config import (
        get_user_llm_instance, get_user_mcp_client,
        set_user_llm_instance, set_user_mcp_client,
        set_user_provider, set_user_model
    )
    from trusted_data_agent.llm.client_factory import get_or_create_llm_client
    from trusted_data_agent.core.configuration_service import retrieve_credentials_for_provider

    user_llm = get_user_llm_instance(user_uuid)
    user_mcp = get_user_mcp_client(user_uuid)

    # If no cached LLM instance, create one from user's default profile
    if not user_llm:
        try:
            # Get user's default profile LLM configuration
            default_profile_id = config_manager.get_default_profile_id(user_uuid)
            if default_profile_id:
                profiles = config_manager.get_profiles(user_uuid)
                default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)

                if default_profile:
                    llm_config_id = default_profile.get('llmConfigurationId')
                    if llm_config_id:
                        llm_configs = config_manager.get_llm_configurations(user_uuid)
                        llm_config = next((cfg for cfg in llm_configs if cfg['id'] == llm_config_id), None)

                        if llm_config:
                            provider = llm_config.get('provider')
                            model = llm_config.get('model')

                            # Load encrypted credentials from database
                            credentials_result = await retrieve_credentials_for_provider(user_uuid, provider)
                            if credentials_result.get('credentials'):
                                credentials = credentials_result['credentials']

                                # Create LLM instance (pooled)
                                user_llm = await get_or_create_llm_client(provider, model, credentials)
                                if user_llm:
                                    set_user_llm_instance(user_llm, user_uuid)
                                    APP_STATE['llm'] = user_llm

                                    # CRITICAL: Set provider and model in APP_CONFIG
                                    # This ensures executor knows which provider/model to use
                                    set_user_provider(provider, user_uuid)
                                    set_user_model(model, user_uuid)

                                    app_logger.info(f"✅ Created and cached LLM instance for {user_uuid}: {provider}/{model}")
                                else:
                                    app_logger.error(f"Failed to create LLM instance for {user_uuid}")
                            else:
                                app_logger.error(f"No credentials found for provider {provider}")
        except Exception as e:
            app_logger.error(f"Error creating LLM instance for user {user_uuid}: {e}", exc_info=True)
    else:
        APP_STATE['llm'] = user_llm

        # Also load provider/model from user's default profile
        try:
            default_profile_id = config_manager.get_default_profile_id(user_uuid)
            if default_profile_id:
                profiles = config_manager.get_profiles(user_uuid)
                default_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)

                if default_profile:
                    llm_config_id = default_profile.get('llmConfigurationId')
                    if llm_config_id:
                        llm_configs = config_manager.get_llm_configurations(user_uuid)
                        llm_config = next((cfg for cfg in llm_configs if cfg['id'] == llm_config_id), None)

                        if llm_config:
                            provider = llm_config.get('provider')
                            model = llm_config.get('model')

                            # Set provider and model in APP_CONFIG
                            set_user_provider(provider, user_uuid)
                            set_user_model(model, user_uuid)

                            app_logger.info(f"✅ Loaded cached LLM instance for {user_uuid}: {provider}/{model}")
                        else:
                            app_logger.info(f"Loaded cached LLM instance for {user_uuid} (no config found)")
                    else:
                        app_logger.info(f"Loaded cached LLM instance for {user_uuid} (no llmConfigurationId)")
                else:
                    app_logger.info(f"Loaded cached LLM instance for {user_uuid} (no default profile)")
            else:
                app_logger.info(f"Loaded cached LLM instance for {user_uuid} (no default profile ID)")
        except Exception as e:
            app_logger.error(f"Error loading provider/model for cached LLM: {e}", exc_info=True)
            app_logger.info(f"Loaded cached LLM instance for {user_uuid} (failed to load provider/model)")

    if user_mcp:
        APP_STATE['mcp_client'] = user_mcp
        app_logger.info(f"Loaded user MCP client for {user_uuid}")

    # Final check - if still no LLM, return error
    if not APP_STATE.get('llm'):
        app_logger.error(f"No LLM instance available for user {user_uuid} - check profile configuration")
        async def error_gen():
            yield PlanExecutor._format_sse({
                "error": "LLM is not configured. Please configure your default profile's LLM settings in Anatomy → Agent Profiles."
            }, "error")
        return Response(error_gen(), mimetype="text/event-stream")
    # --- MODIFICATION END ---

    # --- MODIFICATION START: Generate task_id for interactive sessions ---
    task_id = generate_task_id()
    # --- MODIFICATION END ---

    active_tasks_key = f"{user_uuid}_{session_id}"
    active_tasks = APP_STATE.get("active_tasks", {})
    if active_tasks_key in active_tasks:
        existing_task = active_tasks.pop(active_tasks_key)
        if not existing_task.done():
            app_logger.warning(f"Cancelling previous active task for user {user_uuid}, session {session_id}.")
            existing_task.cancel()
            try:
                await asyncio.wait_for(existing_task, timeout=0.1)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                app_logger.error(f"Error during cancellation cleanup: {e}")

    async def stream_generator():
        queue = asyncio.Queue()

        # Store queue in APP_STATE for cancellation endpoint access
        active_tasks_key = f"{user_uuid}_{session_id}"
        active_queues = APP_STATE.setdefault("active_queues", {})
        active_queues[active_tasks_key] = queue

        # S10 Reattach — every frame of this turn is kept in a cursor-numbered log and stamped
        # with its cursor (`id:`, the SSE standard's resume field), so a client that loses the
        # connection resumes from the cursor it last saw and a closed tab finds the running
        # turn again. Off ⇒ no log, no cursor, the frame exactly as before.
        from trusted_data_agent.core import turn_stream as _tstream
        _tstream.open(task_id, session_id, user_uuid)

        async def event_handler(event_data, event_type):
            sse_event = PlanExecutor._format_sse(event_data, event_type)
            await queue.put(_tstream.stamp(sse_event, _tstream.append(task_id, sse_event)))

        # --- MODIFICATION START: Send initial task_start event ---
        # Send an initial event to the client with the task_id (+ whether it can be reattached)
        _start = {"task_id": task_id}
        if _tstream.enabled():
            _start["reattach"] = True
        # S1.1 — the Living Answer arms on the stream's own word that this turn streams
        # (never on a client guess); off ⇒ the frame is byte-identical to before.
        if getattr(APP_CONFIG, "ANSWER_STREAMING", False):
            _start["stream"] = True
        _start_frame = PlanExecutor._format_sse(_start, "task_start")
        await queue.put(_tstream.stamp(_start_frame, _tstream.append(task_id, _start_frame)))
        # --- MODIFICATION END ---

        async def run_and_signal_completion():
            task = None
            try:
                # --- MODIFICATION START: Pass plan and replay flag to execution service ---
                task = asyncio.create_task(
                    execution_service.run_agent_execution(
                        user_uuid=user_uuid,
                        session_id=session_id,
                        user_input=user_input,
                        event_handler=event_handler,
                        disabled_history=disabled_history,
                        source=source,
                        plan_to_execute=plan_to_execute, # Pass the plan
                        is_replay=is_replay, # Pass the flag
                        display_message=display_message, # Pass the display message
                        task_id=task_id, # Pass the generated task_id
                        profile_override_id=profile_override_id, # Pass the profile override
                        is_session_primer=is_session_primer, # Pass the session primer flag
                        attachments=attachments,  # Pass document upload attachments
                        extension_specs=extension_specs,  # Pass post-processing extensions
                        skill_specs=skill_specs,  # Pass pre-processing skills
                        canvas_context=canvas_context,  # Pass canvas bidirectional context
                        stream_answer=True,  # S1: this stream renders answer_delta events live
                        turn_relation=turn_relation,  # S8: a sibling or a branch head; None = ordinary
                        input_answer=input_answer,  # PROP-011: the answer the ledger binds on a re-entry
                    )
                )
                # --- MODIFICATION END ---
                APP_STATE.setdefault("active_tasks", {})[active_tasks_key] = task
                await task
            except asyncio.CancelledError:
                app_logger.info(f"Task for user {user_uuid}, session {session_id} was cancelled.")
                await event_handler({"message": "Execution stopped by user."}, "cancelled")
            except Exception as e:
                 app_logger.error(f"Error during task execution for user {user_uuid}, session {session_id}: {e}", exc_info=True)
                 await event_handler({"error": str(e)}, "error")
            finally:
                if active_tasks_key in APP_STATE.get("active_tasks", {}):
                    del APP_STATE["active_tasks"][active_tasks_key]
                # Clean up queue from APP_STATE
                active_queues = APP_STATE.get("active_queues", {})
                if active_tasks_key in active_queues:
                    del active_queues[active_tasks_key]
                _tstream.mark_done(task_id)   # S10: the log is complete; a late reattach replays it whole
                await queue.put(None)

        asyncio.create_task(run_and_signal_completion())

        async for item in _pump_with_keepalive(queue):
            yield item

    return Response(stream_generator(), mimetype="text/event-stream")

@api_bp.route("/invoke_prompt_stream", methods=["POST"])
async def invoke_prompt_stream():
    """
    Handles the direct invocation of a prompt from the UI.
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        async def error_gen():
            yield PlanExecutor._format_sse({"error": "Authentication required. Please login."}, "error")
        return Response(error_gen(), mimetype="text/event-stream")

    # Check consumption limits before proceeding
    try:
        from trusted_data_agent.auth.consumption_enforcer import ConsumptionEnforcer
        from trusted_data_agent.auth.models import User
        from trusted_data_agent.auth.database import get_db_session
        
        # Check if user is admin (admins bypass consumption limits)
        with get_db_session() as session:
            user = session.query(User).filter_by(id=user_uuid).first()
            is_admin = user.is_admin if user else False
        
        if not is_admin:
            enforcer = ConsumptionEnforcer(user_uuid)
            can_proceed, error_message = enforcer.can_execute_prompt()
            
            if not can_proceed:
                app_logger.warning(f"Consumption limit exceeded (invoke_prompt) for user {user_uuid}: {error_message}")
                async def limit_error_gen():
                    yield PlanExecutor._format_sse({
                        "error": error_message,
                        "type": "rate_limit_exceeded"
                    }, "error")
                return Response(limit_error_gen(), mimetype="text/event-stream")
    except Exception as e:
        app_logger.error(f"Error checking consumption limits: {e}", exc_info=True)
        # Fail open: allow execution if enforcement check fails

    if not APP_STATE.get('mcp_tools'):
        async def error_gen():
            yield PlanExecutor._format_sse({
                "error": "The agent is not fully configured. Please ensure the LLM and MCP server details are set correctly in the 'Config' tab before invoking a prompt."
            }, "error")
        return Response(error_gen(), mimetype="text/event-stream")

    data = await request.get_json()
    session_id = data.get("session_id")
    prompt_name = data.get("prompt_name")
    arguments = data.get("arguments", {})
    disabled_history = data.get("disabled_history", False)
    source = data.get("source", "prompt_library")
    profile_override_id = data.get("profile_override_id")  # Profile ID for temporary override
    # Post-processing extensions [{"name": "json", "param": null}]
    extension_specs = data.get("extensions")
    # Pre-processing skills [{"name": "sql-expert", "param": "strict"}]
    skill_specs = data.get("skills")
    # Canvas bidirectional context {title, language, content, modified}
    canvas_context = data.get("canvas_context")
    # File attachments [{file_id, filename, ...}]
    attachments = data.get("attachments")

    session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
    if not session_data:
        app_logger.error(f"invoke_prompt_stream denied: Session {session_id} not found for user {user_uuid}.")
        async def error_gen():
            yield PlanExecutor._format_sse({"error": "Session not found or invalid."}, "error")
        return Response(error_gen(), mimetype="text/event-stream")

    # Get active profile to check profile type
    # Check profile override first, then last executed profile, then session's profile_id, then default
    from trusted_data_agent.core.config_manager import get_config_manager
    config_manager = get_config_manager()

    active_profile = None
    resolution_source = None

    # Log profile sources for debugging
    app_logger.debug(
        f"[PROMPT INVOCATION] Profile resolution for session {session_id}: "
        f"override={profile_override_id}, last_executed={session_data.get('last_executed_profile_id')}, "
        f"session_profile={session_data.get('profile_id')}"
    )

    # Priority 1: Profile override from request (explicit client-side override)
    if profile_override_id:
        profiles = config_manager.get_profiles(user_uuid)
        active_profile = next((p for p in profiles if p.get("id") == profile_override_id), None)
        if active_profile:
            resolution_source = "Priority 1: profile_override_id from request"

    # Priority 2: Last executed profile (for prompt invocations after override)
    # This captures the profile used in the most recent query, including overrides
    if not active_profile and session_data.get("last_executed_profile_id"):
        profiles = config_manager.get_profiles(user_uuid)
        active_profile = next((p for p in profiles if p.get("id") == session_data.get("last_executed_profile_id")), None)
        if active_profile:
            resolution_source = "Priority 2: last_executed_profile_id from session"

    # Priority 3: Session's stored profile_id (original session base profile)
    if not active_profile and session_data.get("profile_id"):
        profiles = config_manager.get_profiles(user_uuid)
        active_profile = next((p for p in profiles if p.get("id") == session_data.get("profile_id")), None)
        if active_profile:
            resolution_source = "Priority 3: profile_id from session"

    # Priority 4: Default profile
    if not active_profile:
        default_profile_id = config_manager.get_default_profile_id(user_uuid)
        if default_profile_id:
            profiles = config_manager.get_profiles(user_uuid)
            active_profile = next((p for p in profiles if p.get("id") == default_profile_id), None)
            if active_profile:
                resolution_source = "Priority 4: default profile"

    # Get profile type (default to tool_enabled for backward compatibility)
    active_profile_type = active_profile.get("profile_type", "tool_enabled") if active_profile else "tool_enabled"

    app_logger.debug(f"[PROMPT INVOCATION] Resolved: @{active_profile.get('tag') if active_profile else 'NONE'} (type={active_profile_type}) via {resolution_source}")

    # MCP prompt invocation is only for tool-enabled profiles
    if active_profile_type != "tool_enabled":
        app_logger.warning(
            f"❌ [PROMPT INVOCATION] REJECTED - profile type '{active_profile_type}' is not tool_enabled. "
            f"Profile: @{active_profile.get('tag') if active_profile else 'NONE'}, "
            f"resolved via: {resolution_source}"
        )
        async def error_gen():
            yield PlanExecutor._format_sse({
                "error": "MCP prompts are only available for tool-enabled profiles. Please switch to a tool-enabled profile or use the chat interface.",
                "error_type": "invalid_profile_type"
            }, "error")
        return Response(error_gen(), mimetype="text/event-stream")

    # --- MODIFICATION START: Generate task_id for prompt invocations ---
    task_id = generate_task_id()
    # --- MODIFICATION END ---

    active_tasks_key = f"{user_uuid}_{session_id}"
    active_tasks = APP_STATE.get("active_tasks", {})
    if active_tasks_key in active_tasks:
        existing_task = active_tasks.pop(active_tasks_key)
        if not existing_task.done():
            app_logger.warning(f"Cancelling previous active task for user {user_uuid}, session {session_id} during prompt invocation.")
            existing_task.cancel()
            try:
                await asyncio.wait_for(existing_task, timeout=0.1)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:
                app_logger.error(f"Error during cancellation cleanup: {e}")

    async def stream_generator():
        queue = asyncio.Queue()

        # Store queue in APP_STATE for cancellation endpoint access
        active_tasks_key = f"{user_uuid}_{session_id}"
        active_queues = APP_STATE.setdefault("active_queues", {})
        active_queues[active_tasks_key] = queue

        async def event_handler(event_data, event_type):
            sse_event = PlanExecutor._format_sse(event_data, event_type)
            await queue.put(sse_event)

        async def run_and_signal_completion():
            task = None
            try:
                # Prompt invocation doesn't support replay currently
                # --- MODIFICATION START: Use f-string for user_input ---
                task = asyncio.create_task(
                    execution_service.run_agent_execution(
                        user_uuid=user_uuid,
                        session_id=session_id,
                        user_input=f"Executing prompt: {prompt_name}",
                        event_handler=event_handler,
                        active_prompt_name=prompt_name,
                # --- MODIFICATION END ---
                        prompt_arguments=arguments,
                        disabled_history=disabled_history,
                        source=source,
                        task_id=task_id, # Pass the generated task_id
                        # Only pass profile override when resolved profile differs from default.
                        # When they match, APP_STATE already has the correct MCP data from login.
                        # When they differ (e.g., session uses tool_enabled but default is llm_only),
                        # the override block in executor.py loads the correct MCP tools/prompts.
                        profile_override_id=active_profile.get('id') if active_profile and active_profile.get('id') != config_manager.get_default_profile_id(user_uuid) else profile_override_id,
                        attachments=attachments,
                        extension_specs=extension_specs,
                        skill_specs=skill_specs,
                        canvas_context=canvas_context,
                        stream_answer=True,  # S1: the prompt stream renders answer_delta events live
                    )
                )
                APP_STATE.setdefault("active_tasks", {})[active_tasks_key] = task
                await task
            except asyncio.CancelledError:
                app_logger.info(f"Prompt task for user {user_uuid}, session {session_id} was cancelled.")
                await event_handler({"message": "Execution stopped by user."}, "cancelled")
            except Exception as e:
                 app_logger.error(f"Error during prompt task execution for user {user_uuid}, session {session_id}: {e}", exc_info=True)
                 await event_handler({"error": str(e)}, "error")
            finally:
                if active_tasks_key in APP_STATE.get("active_tasks", {}):
                    del APP_STATE["active_tasks"][active_tasks_key]
                # Clean up queue from APP_STATE
                active_queues = APP_STATE.get("active_queues", {})
                if active_tasks_key in active_queues:
                    del active_queues[active_tasks_key]
                await queue.put(None)

        asyncio.create_task(run_and_signal_completion())

        async for item in _pump_with_keepalive(queue):
            yield item

    return Response(stream_generator(), mimetype="text/event-stream")

async def _cancel_genie_slaves_recursive(user_uuid: str, parent_session_id: str, _depth: int = 0):
    """Cancel all slave sessions for a genie coordinator, depth-first recursively."""
    if _depth > 5:
        return
    slaves = await session_manager.get_genie_slave_sessions(parent_session_id, user_uuid)
    if not slaves:
        return

    active_tasks = APP_STATE.get("active_tasks", {})
    background_tasks = APP_STATE.get("background_tasks", {})
    cancellation_flags = APP_STATE.setdefault("cancellation_flags", {})

    for slave in slaves:
        slave_session_id = slave["slave_session_id"]

        # Recurse first so deepest slaves are cancelled before their parents
        await _cancel_genie_slaves_recursive(user_uuid, slave_session_id, _depth + 1)

        # Cancel interactive SSE task (key = "{user_uuid}_{session_id}")
        sse_key = f"{user_uuid}_{slave_session_id}"
        cancellation_flags[sse_key] = True
        sse_task = active_tasks.get(sse_key)
        if sse_task and not sse_task.done():
            sse_task.cancel()
            app_logger.info(f"[cancel_stream] Cancelled SSE slave task for session {slave_session_id}")

        # Cancel REST background task (key = task_id UUID) — scan background_tasks by session_id
        for task_id, meta in list(background_tasks.items()):
            if meta.get("session_id") == slave_session_id and meta.get("user_uuid") == user_uuid:
                rest_task = active_tasks.get(task_id)
                if rest_task and not rest_task.done():
                    rest_task.cancel()
                    meta["status"] = "cancelling"
                    app_logger.info(f"[cancel_stream] Cancelled REST slave task {task_id} for session {slave_session_id}")
                break


@api_bp.route("/api/session/<session_id>/cancel_stream", methods=["POST"])
async def cancel_stream(session_id: str):
    """Cancels the active execution task for a given session."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401
    active_tasks_key = f"{user_uuid}_{session_id}"
    active_tasks = APP_STATE.get("active_tasks", {})
    task = active_tasks.get(active_tasks_key)

    if task and not task.done():
        app_logger.info(f"Received request to cancel task for user {user_uuid}, session {session_id}.")

        # Set cancellation flag for executor to check
        cancellation_flags = APP_STATE.setdefault("cancellation_flags", {})
        cancellation_flags[active_tasks_key] = True

        # Cancel the asyncio task
        task.cancel()

        # Send immediate SSE event to frontend to ensure UI updates
        # This happens even if task is stuck and doesn't acknowledge cancellation
        active_queues = APP_STATE.get("active_queues", {})
        queue = active_queues.get(active_tasks_key)
        if queue:
            try:
                from trusted_data_agent.agent.executor import PlanExecutor
                sse_event = PlanExecutor._format_sse(
                    {"message": "Cancellation requested by user", "session_id": session_id},
                    "cancelled"
                )
                await queue.put(sse_event)
                app_logger.info(f"Sent immediate cancellation SSE event for session {session_id}")
            except Exception as e:
                app_logger.error(f"Failed to send SSE cancellation event: {e}")

        # Delayed cleanup to allow event propagation and task cancellation
        async def delayed_cleanup():
            await asyncio.sleep(2)
            if active_tasks_key in active_tasks:
                del active_tasks[active_tasks_key]
            cancellation_flags.pop(active_tasks_key, None)
            app_logger.info(f"Cleaned up cancelled task for session {session_id}")

        asyncio.create_task(delayed_cleanup())

        # Propagate cancellation to genie slave sessions (fire-and-forget, handles nesting)
        asyncio.create_task(_cancel_genie_slaves_recursive(user_uuid, session_id))

        return jsonify({"status": "success", "message": "Cancellation request sent."}), 200
    elif task and task.done():
        app_logger.info(f"Cancellation request for user {user_uuid}, session {session_id} ignored: task already completed.")
        if active_tasks_key in active_tasks:
             del active_tasks[active_tasks_key]
        return jsonify({"status": "info", "message": "Task already completed."}), 200
    else:
        app_logger.warning(f"Cancellation request for user {user_uuid}, session {session_id} failed: No active task found.")
        return jsonify({"status": "error", "message": "No active task found for this session."}), 404

# --- MODIFICATION START: Add endpoint to toggle turn validity ---
@api_bp.route("/api/session/<session_id>/turn/<int:turn_id>/toggle_validity", methods=["POST"])
async def toggle_turn_validity_route(session_id: str, turn_id: int):
    """Toggles the validity of a specific turn."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401
    app_logger.info(f"Toggle validity request for session {session_id}, turn {turn_id}, user {user_uuid}")

    success = await session_manager.toggle_turn_validity(user_uuid, session_id, turn_id)

    if success:
        return jsonify({"status": "success", "message": f"Turn {turn_id} validity toggled."}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to toggle turn validity."}), 500
# --- MODIFICATION END ---

# --- MODIFICATION START: Add endpoint to update turn feedback ---
@api_bp.route("/api/session/<session_id>/turn/<int:turn_id>/feedback", methods=["POST"])
async def update_turn_feedback_route(session_id: str, turn_id: int):
    """Updates the feedback (upvote/downvote) for a specific turn."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401
    data = await request.get_json()
    vote = data.get("vote")  # Expected: 'up', 'down', or None
    
    app_logger.info(f"Feedback update request for session {session_id}, turn {turn_id}, user {user_uuid}: {vote}")

    success = await session_manager.update_turn_feedback(user_uuid, session_id, turn_id, vote)

    if success:
        return jsonify({"status": "success", "message": f"Turn {turn_id} feedback updated."}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to update turn feedback."}), 500
# --- MODIFICATION END ---

# --- MODIFICATION START: Add endpoint for direct RAG case feedback (works without session) ---
@api_bp.route("/api/rag/cases/<case_id>/feedback", methods=["POST"])
async def update_rag_case_feedback_route(case_id: str):
    """
    Updates the feedback (upvote/downvote) directly for a RAG case.
    This endpoint works independently of sessions and is used when updating feedback
    from the RAG collection view (e.g., when the session may no longer exist).
    
    Args:
        case_id: The case ID (with or without 'case_' prefix)
        vote: 'up', 'down', or None to clear
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required. Please login."}), 401
    
    data = await request.get_json()
    vote = data.get("vote")  # Expected: 'up', 'down', or None
    
    if vote not in ['up', 'down', None]:
        return jsonify({"status": "error", "message": "Invalid vote. Must be 'up', 'down', or None."}), 400
    
    app_logger.info(f"Direct RAG case feedback update request for case {case_id}, user {user_uuid}: {vote}")
    
    try:
        # Get the RAG retriever instance
        retriever = APP_STATE.get('rag_retriever_instance')
        if not retriever:
            return jsonify({"status": "error", "message": "RAG system not initialized."}), 500
        
        # Resolve the case within the collection the caller named. Feedback is a
        # WRITE against one repository's copy; picking whichever copy readdir
        # returned first could authorize against -- and record a vote about -- a
        # different account's copy of the same case id.
        from trusted_data_agent.agent.rag_access_context import RAGAccessContext

        project_root = Path(__file__).resolve().parents[3]
        cases_dir = project_root / 'rag' / 'tda_rag_cases'
        file_stem = case_id if case_id.startswith('case_') else f'case_{case_id}'
        requested_collection_id = data.get('collection_id')
        if requested_collection_id is not None:
            try:
                requested_collection_id = int(requested_collection_id)
            except (TypeError, ValueError):
                requested_collection_id = None

        rag_context = RAGAccessContext(user_id=user_uuid, retriever=retriever)
        case_data, collection_id, forbidden = _load_case_from_disk(
            cases_dir, case_id, requested_collection_id, rag_context
        )

        if case_data is None:
            if forbidden:
                app_logger.warning(
                    f"User {user_uuid} attempted to update feedback for case {case_id} "
                    f"(collection {requested_collection_id}) without access"
                )
                return jsonify({"status": "error", "message": "You do not have access to this case."}), 403
            return jsonify({"status": "error", "message": f"Case '{file_stem}' not found."}), 404

        # Convert vote to feedback score
        feedback_score = 1 if vote == 'up' else -1 if vote == 'down' else 0
        
        # Update the case feedback directly (handles both JSON and ChromaDB updates)
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context
            success = await retriever.update_case_feedback(case_id, feedback_score)
        except RuntimeError:
            # No running loop, create new one
            success = await asyncio.new_event_loop().run_until_complete(
                retriever.update_case_feedback(case_id, feedback_score)
            )
        
        if success:
            action = "upvoted" if vote == 'up' else "downvoted" if vote == 'down' else "cleared"
            app_logger.info(f"Successfully {action} RAG case {case_id} by user {user_uuid}")
            
            # Check if the original session still exists (for warning purposes).
            # The case was already resolved above -- re-finding it by scanning
            # every collection directory would read a different copy than the one
            # this vote was authorized against.
            session_warning = None
            case_metadata = case_data.get('metadata', {}) if case_data else None
            try:
                session_id = (case_metadata or {}).get('session_id')
                if session_id:
                    from trusted_data_agent.core import session_manager
                    session_found = (await session_manager.find_session_owner(session_id)) is not None

                    if not session_found:
                        session_warning = f"Note: Original session (ID: {session_id[:8]}...) no longer exists, but RAG case has been updated successfully."
                        app_logger.info(f"Session {session_id} not found for case {case_id}, but feedback updated anyway")
            except Exception as e:
                app_logger.debug(f"Could not check session existence: {e}")
            
            response_data = {
                "status": "success", 
                "message": f"Case {case_id} feedback {action}.",
                "case_id": case_id,
                "feedback_score": feedback_score
            }
            if session_warning:
                response_data["warning"] = session_warning
            
            return jsonify(response_data), 200
        else:
            app_logger.warning(f"Failed to update feedback for case {case_id}: update_case_feedback returned False")
            return jsonify({"status": "error", "message": f"Case {case_id} not found or failed to update."}), 404
    
    except Exception as e:
        app_logger.error(f"Error updating RAG case feedback for case {case_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to update case feedback: {str(e)}"}), 500
# --- MODIFICATION END ---


# =============================================================================
# Document Upload in Conversations
# =============================================================================

ALLOWED_UPLOAD_EXTENSIONS = {'.pdf', '.txt', '.docx', '.md', '.jpg', '.jpeg', '.png', '.gif', '.webp',
                             # tables are always readable: as a bounded text table by default, and with
                             # attachment sources on (S9) CSV/TSV are read as a verified table source
                             '.csv', '.tsv', '.xlsx'}
# S9: with attachment sources on, a CSV/TSV is a first-class table source (read deterministically).
SOURCE_ONLY_EXTENSIONS = {'.csv', '.tsv'}


def allowed_upload_extensions() -> set:
    try:
        from trusted_data_agent.llm import image_sources as _isrc
        if _isrc.enabled():
            return ALLOWED_UPLOAD_EXTENSIONS | SOURCE_ONLY_EXTENSIONS
    except Exception:  # noqa: BLE001
        pass
    return set(ALLOWED_UPLOAD_EXTENSIONS)
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024  # 50MB
MAX_FILES_PER_UPLOAD = 5


def _ingest_upload_into(user_uuid: str, session_id: str, manifest: dict, filename: str, file_content: bytes,
                        profile_id: Optional[str] = None) -> dict:
    """Store ONE validated upload, read it once, record it in ``manifest`` and return the
    response entry. The caller validates (extension, size, emptiness) and saves the manifest.

    PROP-010: the bytes go to the file store (the agent's store, encrypted) as a
    conversation-lifetime file; the reading happens from memory, before encryption; the
    manifest entry references the file instead of naming a plaintext path."""
    from trusted_data_agent.llm.document_upload import DocumentUploadHandler
    from trusted_data_agent.filestore import session_files

    file_ext = os.path.splitext(filename)[1].lower()
    file_size = len(file_content)

    # Generate file ID and store (encrypted, in the agent's file store)
    file_id = str(uuid.uuid4())
    doc_handler_for_mime = DocumentUploadHandler()
    content_type = doc_handler_for_mime._get_mime_type(file_ext)
    session_files.store_upload(user_uuid, session_id, file_id, filename, file_content, content_type,
                               profile_id=profile_id)

    is_image = file_ext in IMAGE_EXTENSIONS

    # Extract text content
    if is_image:
        extracted_text = f"[Image file: {filename} - visual content not available in text mode]"
    else:
        result = DocumentUploadHandler().extract_text_from_bytes(file_content, f"{file_id}{file_ext}")
        extracted_text = result.get('content', f'[Failed to extract text from {filename}]')

    # Update manifest
    manifest[file_id] = {
        "file_ref": file_id,
        "filename": filename,
        "content_type": content_type,
        "file_size": file_size,
        "extracted_text": extracted_text,
        "is_image": is_image
    }

    # S9 — the platform READS the upload once, here (deterministic: OCR / PDF tables /
    # CSV / text); the full source rides the manifest so the turn rebuilds it without
    # re-extracting. A verified read REPLACES the image placeholder / the plain text
    # as what a text-only provider sees. Off ⇒ this block never runs.
    source_summary = None
    try:
        from trusted_data_agent.llm import image_sources as _isrc
        if _isrc.enabled():
            _src = _isrc.extract(file_id, filename, None, content_type, data=file_content)
            manifest[file_id]["source"] = _isrc.to_record(_src)
            if _src.verified and _src.usable:
                manifest[file_id]["extracted_text"] = extracted_text = _isrc.document_text_for(_src)
            source_summary = _src.to_dict()
    except Exception as _se:  # noqa: BLE001
        app_logger.debug(f"[attachment-sources] upload-time read skipped for {filename}: {_se}")

    # Build response entry
    preview_text = extracted_text[:200] + "..." if len(extracted_text) > 200 else extracted_text
    return {
        "file_id": file_id,
        "original_filename": filename,
        "content_type": content_type,
        "file_size": file_size,
        "extracted_text_preview": preview_text,
        "extracted_text_length": len(extracted_text),
        "is_image": is_image,
        **({"source": source_summary} if source_summary else {})
    }


def _ingest_upload(user_uuid: str, session_id: str, filename: str, file_content: bytes,
                   profile_id: Optional[str] = None) -> dict:
    """One upload, end to end, for a validated file: the seam the attachment-storage
    freeze gate drives (test/test_attachment_storage_freeze.py)."""
    from trusted_data_agent.filestore import session_files
    manifest = session_files.load_manifest(user_uuid, session_id) or {}
    entry = _ingest_upload_into(user_uuid, session_id, manifest, filename, file_content, profile_id)
    session_files.save_manifest(user_uuid, session_id, manifest)
    return entry


def _upload_bytes(user_uuid: str, session_id: str, file_id: str):
    """The stored bytes of one upload, or None — owner/session scoped."""
    from trusted_data_agent.filestore import session_files
    return session_files.read_bytes(user_uuid, session_id, file_id)


def _delete_upload(user_uuid: str, session_id: str, file_id: str) -> bool:
    """Remove one pending upload and its manifest entry. False when it is not there."""
    from trusted_data_agent.filestore import session_files
    return session_files.delete_upload(user_uuid, session_id, file_id)


@api_bp.route("/api/v1/chat/upload", methods=["POST"])
async def chat_upload_file():
    """
    Upload files for use in a chat conversation.

    Accepts multipart/form-data with:
    - files: One or more files to upload
    - session_id: The session to associate files with

    Returns file references with extracted text previews.
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    try:
        files = await request.files
        form = await request.form
        session_id = form.get('session_id')

        if not session_id:
            return jsonify({"status": "error", "message": "session_id is required"}), 400

        file_list = files.getlist('files')
        if not file_list:
            return jsonify({"status": "error", "message": "No files provided"}), 400

        if len(file_list) > MAX_FILES_PER_UPLOAD:
            return jsonify({"status": "error", "message": f"Maximum {MAX_FILES_PER_UPLOAD} files per upload"}), 400

        # Validate session exists
        session_data = await session_manager.get_session(user_uuid, session_id)
        if not session_data:
            return jsonify({"status": "error", "message": "Session not found"}), 404

        from trusted_data_agent.filestore import session_files
        manifest = session_files.load_manifest(user_uuid, session_id) or {}
        upload_profile_id = (form.get('profile_id') or '').strip() or None
        uploaded_files = []

        for file_storage in file_list:
            filename = file_storage.filename
            if not filename:
                continue

            file_ext = os.path.splitext(filename)[1].lower()
            _allowed = allowed_upload_extensions()
            if file_ext not in _allowed:
                return jsonify({
                    "status": "error",
                    "message": f"Unsupported file format: {file_ext}. Allowed: {', '.join(sorted(_allowed))}"
                }), 400

            file_content = file_storage.read()
            file_size = len(file_content)

            if file_size > MAX_UPLOAD_SIZE_BYTES:
                return jsonify({
                    "status": "error",
                    "message": f"File '{filename}' exceeds maximum size of {MAX_UPLOAD_SIZE_BYTES // (1024*1024)}MB"
                }), 400

            if file_size == 0:
                return jsonify({"status": "error", "message": f"File '{filename}' is empty"}), 400

            uploaded_files.append(_ingest_upload_into(user_uuid, session_id, manifest, filename, file_content,
                                                      upload_profile_id))

        session_files.save_manifest(user_uuid, session_id, manifest)

        app_logger.info(f"Chat upload: {len(uploaded_files)} files uploaded for session {session_id} by user {user_uuid}")

        return jsonify({
            "status": "success",
            "files": uploaded_files
        }), 200

    except Exception as e:
        app_logger.error(f"Error in chat file upload: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Upload failed: {str(e)}"}), 500


def _attachment_sources_capability() -> dict:
    """S9: whether the platform reads uploads as sources here, and with which engines — the
    composer says "read by the platform" only when it is true. Absent when the switch is off."""
    try:
        from trusted_data_agent.llm import image_sources as _isrc
        if not _isrc.enabled():
            return {}
        _eng, _name = _isrc.ocr_engine()
        try:
            import pdfplumber  # type: ignore  # noqa: F401
            _pdf = "pdfplumber"
        except Exception:  # noqa: BLE001
            _pdf = None
        return {"attachment_sources": {"enabled": True, "ocr_engine": _name, "pdf_engine": _pdf}}
    except Exception:  # noqa: BLE001
        return {}


@api_bp.route("/api/v1/chat/upload/<file_id>/content", methods=["GET"])
@require_auth
async def chat_upload_content(current_user, file_id: str):
    """S9: the stored upload's bytes, session-scoped to its owner — what the capsule's Sources
    row and the lineage dialog draw the cited region over. Only with attachment sources on."""
    try:
        from trusted_data_agent.llm import image_sources as _isrc
        if not _isrc.enabled():
            return jsonify({"status": "error", "message": "attachment sources are off"}), 404
        session_id = request.args.get("session_id")
        if not session_id:
            return jsonify({"status": "error", "message": "session_id is required"}), 400
        user_uuid = current_user.id
        session_data = await session_manager.get_session(user_uuid=user_uuid, session_id=session_id)
        if not session_data:
            return jsonify({"status": "error", "message": "Session not found"}), 404
        from trusted_data_agent.filestore import session_files
        entry = (session_files.load_manifest(user_uuid, session_id) or {}).get(file_id)
        data = _upload_bytes(user_uuid, session_id, file_id) if entry else None
        if data is None:
            return jsonify({"status": "error", "message": "File not found"}), 404
        import io as _io
        return await send_file(_io.BytesIO(data), mimetype=entry.get("content_type") or "application/octet-stream",
                               as_attachment=False, attachment_filename=entry.get("filename"))
    except Exception as e:
        app_logger.error(f"Error serving upload content: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@api_bp.route("/api/v1/chat/upload/<file_id>", methods=["DELETE"])
async def chat_delete_upload(file_id: str):
    """Delete a pending file upload before sending the message."""
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    session_id = request.args.get('session_id')
    if not session_id:
        return jsonify({"status": "error", "message": "session_id query parameter is required"}), 400

    try:
        if not _delete_upload(user_uuid, session_id, file_id):
            return jsonify({"status": "error", "message": "File not found"}), 404

        app_logger.info(f"Chat upload deleted: file {file_id} for session {session_id}")
        return jsonify({"status": "success", "message": "File deleted"}), 200

    except Exception as e:
        app_logger.error(f"Error deleting chat upload: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Delete failed: {str(e)}"}), 500


@api_bp.route("/api/v1/chat/upload-capabilities", methods=["GET"])
async def chat_upload_capabilities():
    """
    Get document upload capabilities for the current user's active profile.
    Used by the frontend to show/hide the upload button and validate files.
    """
    user_uuid = _get_user_uuid_from_request()
    if not user_uuid:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()

        session_id = request.args.get('session_id')

        # Determine provider from active profile
        provider = APP_CONFIG.CURRENT_PROVIDER or "Unknown"
        model = APP_CONFIG.CURRENT_MODEL or ""

        # Try to get provider from session's profile
        if session_id:
            session_data = await session_manager.get_session(user_uuid, session_id)
            if session_data and session_data.get("provider"):
                provider = session_data["provider"]

        return jsonify({
            "status": "success",
            "capabilities": {
                "enabled": True,
                "provider": provider,
                "model": model,
                "supported_formats": sorted(list(allowed_upload_extensions())),
                "max_file_size_mb": MAX_UPLOAD_SIZE_BYTES // (1024 * 1024),
                "max_files_per_message": MAX_FILES_PER_UPLOAD,
                "text_extraction_formats": sorted([ext for ext in allowed_upload_extensions() if ext not in IMAGE_EXTENSIONS]),
                "image_formats": sorted(list(IMAGE_EXTENSIONS)),
                **_attachment_sources_capability()
            }
        }), 200

    except Exception as e:
        app_logger.error(f"Error getting upload capabilities: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500