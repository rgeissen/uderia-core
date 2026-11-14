# src/trusted_data_agent/main.py
from dotenv import load_dotenv
load_dotenv()
# The licence's entitlements govern the RUNNING server (core/entitlements.py): armed here, when
# this module is run as the server and before any module of the core is imported, a module of a
# group the key withholds is not importable in this process — the core's optional imports fall
# through exactly as in the public core. A test or script that imports this module is not a
# deployment and is not governed.
from trusted_data_agent.core import entitlements as _entitlements
if __name__ == "__main__":
    _entitlements.install_import_guard()
import asyncio
import importlib
import os
import sys
import logging
import argparse

# --- MODIFICATION START: Import Response from Quart ---
# Required if you add test routes later, good practice to have it
from quart import Quart, Response, request
# --- MODIFICATION END ---
from quart_cors import cors
import hypercorn.asyncio
from hypercorn.config import Config

os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["TOKENIZERS_PARALLELISM"] = "false"  # Silence tokenizers warnings

# --- Logging Setup (from your original file) ---
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))
LOG_DIR = os.path.join(project_root, "logs")

# Created at import, never wiped here: every test suite imports this module, and the parallel
# battery used to race one process's rmtree against another's makedirs (FileExistsError /
# FileNotFoundError on the handlers below). The server start truncates its own log files instead
# (see __main__), which is what the wipe was for.
os.makedirs(LOG_DIR, exist_ok=True)

class SseConnectionFilter(logging.Filter):
    def filter(self, record):
        is_validation_error = "Failed to validate notification" in record.getMessage()
        is_sse_connection_method = "sse/connection" in record.getMessage()
        return not (is_validation_error and is_sse_connection_method)

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
handler.addFilter(SseConnectionFilter())

root_logger = logging.getLogger()
root_logger.handlers.clear()
root_logger.addHandler(handler)
# --- MODIFICATION START: Set Root Logger Level to INFO ---
root_logger.setLevel(logging.INFO)
# root_logger.setLevel(logging.DEBUG)
# --- MODIFICATION END ---

# Silence verbose third-party loggers
logging.getLogger('chromadb.telemetry.product.posthog').setLevel(logging.WARNING)
logging.getLogger('sentence_transformers').setLevel(logging.WARNING)
logging.getLogger('rag_template_manager').setLevel(logging.WARNING)
# --- TEMPORARILY ENABLE RAG RETRIEVER LOGGING FOR DEBUGGING ---
logging.getLogger('rag_retriever').setLevel(logging.INFO)
logging.getLogger('rag_access_context').setLevel(logging.INFO)

app_logger = logging.getLogger("quart.app")
# --- MODIFICATION START: Set Quart App Logger Level to INFO ---
app_logger.setLevel(logging.INFO) # Ensures quart.app messages (like ours) are shown
# app_logger.setLevel(logging.DEBUG)
# --- MODIFICATION END ---
app_logger.addHandler(handler)
app_logger.propagate = False # Prevent duplicate messages in the root logger


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.WARNING)
logging.getLogger("hypercorn.access").propagate = False
logging.getLogger("hypercorn.error").propagate = False

llm_log_handler = logging.FileHandler(os.path.join(LOG_DIR, "llm_conversation.log"))
llm_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
llm_logger = logging.getLogger("llm_conversation")
llm_logger.setLevel(logging.INFO)
llm_logger.addHandler(llm_log_handler)
llm_logger.propagate = False

llm_history_log_handler = logging.FileHandler(os.path.join(LOG_DIR, "llm_conversation_history.log"))
llm_history_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
llm_history_logger = logging.getLogger("llm_conversation_history")
llm_history_logger.setLevel(logging.INFO)
llm_history_logger.addHandler(llm_history_log_handler)
llm_history_logger.propagate = False
# --- End Logging Setup ---

try:
    from trusted_data_agent.agent import prompts
except RuntimeError as e:
    app_logger.critical(f"Application startup failed during initial import: {e}")
    sys.exit(1)

from trusted_data_agent.core.config import APP_CONFIG, APP_STATE


# --- MODIFICATION START: Add RAG Processing Worker ---
async def rag_processing_worker():
    """
    A single, persistent background worker that processes turns from the
    RAG queue one by one, ensuring no race conditions.
    """
    pass  # RAG worker started
    while True:
        try:
            # 1. Wait for a turn_summary to arrive in the queue
            turn_summary = await APP_STATE['rag_processing_queue'].get()

            # 2. Get the RAG retriever instance
            retriever = APP_STATE.get('rag_retriever_instance')
            
            # 3. Process the turn if RAG is enabled and the instance exists
            if retriever and turn_summary and APP_CONFIG.RAG_ENABLED:
                # Get the collection ID from the turn summary (where the base RAG example came from)
                collection_id = turn_summary.get('rag_source_collection_id')
                user_uuid = turn_summary.get('user_uuid')  # --- MODIFICATION: Extract user_uuid ---
                collection_info = f" to collection {collection_id}" if collection_id is not None else " (no RAG base, using default)"
                app_logger.info(f"RAG worker: Processing turn {turn_summary.get('turn')} from session {turn_summary.get('session_id')}{collection_info}.")
                
                # --- MODIFICATION START: Create context and pass to process_turn_for_rag ---
                from trusted_data_agent.agent.rag_access_context import RAGAccessContext
                rag_context = RAGAccessContext(user_id=user_uuid, retriever=retriever)
                
                # Process the turn and get the case_id, passing the context
                case_id = await retriever.process_turn_for_rag(
                    turn_summary, 
                    collection_id=collection_id,
                    rag_context=rag_context  # --- MODIFICATION: Pass context ---
                )
                # --- MODIFICATION END ---
                
                # If a case was created, store the case_id in the session
                if case_id:
                    from trusted_data_agent.core import session_manager
                    session_id = turn_summary.get('session_id')
                    turn_id = turn_summary.get('turn')
                    
                    if session_id and turn_id and user_uuid:
                        try:
                            await session_manager.add_case_id_to_turn(user_uuid, session_id, turn_id, case_id)
                        except Exception as e:
                            app_logger.error(f"Failed to store case_id {case_id} in session: {e}")
                
            # 4. Mark the queue item as processed
            APP_STATE['rag_processing_queue'].task_done()

        except Exception as e:
            # Log errors but don't crash the worker
            app_logger.error(f"Error in RAG processing worker: {e}", exc_info=True)
            # Ensure task_done() is called even on error to prevent queue blockage
            if 'turn_summary' in locals() and turn_summary:
                APP_STATE['rag_processing_queue'].task_done()
# --- MODIFICATION END ---


# User context cleanup worker removed - no longer needed with database persistence


def _register_optional_enterprise_suite(app):
    """Register the **Ontology suite** (Platform Ontology + Trust Membrane) — ONE
    enterprise feature, physically isolatable.

    Registered only when the enterprise gate is on (``core/enterprise.py``) AND each
    module is present (optional imports). If the suite is excluded from a build, the
    imports fail and the platform runs cleanly without it. Removal manifest:
    ``src/trusted_data_agent/core/trust/README.md``. Sets the two template flags
    (``ontology_enabled`` / ``trust_membrane_available``) to what actually registered.
    Flipping the feature Enterprise-only is a one-line edit in ``core/enterprise.py``.
    """
    _log = logging.getLogger("quart.app")
    ontology_ok = trust_ok = False
    try:
        from trusted_data_agent.core.enterprise import is_enterprise_ontology_enabled
        enabled = is_enterprise_ontology_enabled()
    except Exception as e:
        enabled = False
        _log.info(f"Ontology suite: enterprise gate unavailable — skipping ({e.__class__.__name__}).")

    if enabled:
        try:
            from trusted_data_agent.api.platform_ontology_routes import platform_ontology_bp
            app.register_blueprint(platform_ontology_bp, url_prefix="/api")
            ontology_ok = True
        except Exception as e:
            _log.info(f"Ontology suite: platform-ontology routes not present ({e.__class__.__name__}).")
        try:
            from trusted_data_agent.api.trust_routes import trust_bp
            app.register_blueprint(trust_bp, url_prefix="/api")
            try:  # the acts of signing (sign-off, attestation, revocation) — the enterprise
                  # package's trust-signing entitlement; the public core reads trust, it does not sign
                from trusted_data_agent.api.trust_signing_routes import trust_signing_bp
                app.register_blueprint(trust_signing_bp, url_prefix="/api")
            except ImportError:
                _log.info("Trust signing not installed (enterprise package) — the acts of signing answer 404; trust is read, not signed")
            trust_ok = True
        except Exception as e:
            _log.info(f"Ontology suite: Trust Membrane not present ({e.__class__.__name__}).")
        if trust_ok:
            # PROP-009 Evidence — reads the Trust Membrane's records, so only beside it.
            try:
                from trusted_data_agent.api.evidence_routes import evidence_bp
                app.register_blueprint(evidence_bp, url_prefix="/api")
            except Exception as e:
                _log.info(f"Ontology suite: Evidence routes not present ({e.__class__.__name__}).")
        _log.info(f"Ontology suite: enabled (platform-ontology={ontology_ok}, trust-membrane={trust_ok}).")
    else:
        _log.info("Ontology suite: disabled by the enterprise gate.")

    APP_STATE['ontology_enabled'] = ontology_ok
    APP_STATE['trust_membrane_available'] = trust_ok

    from trusted_data_agent.core.version import get_app_version
    APP_STATE['app_version'] = get_app_version()
    _log.info(f"Uderia version: {APP_STATE['app_version']}")


def cors_allowed_origins():
    """The browser origins allowed to call the API cross-origin.

    ``CORS_ALLOWED_ORIGINS`` = a comma-separated list of exact origins
    (``https://app.example.com,https://portal.example.com``). Unset or ``*`` keeps the
    permissive default (any origin) — the API authenticates with a bearer token, not a
    cookie, so a foreign page holds no ambient credentials; a deployment that embeds no
    cross-origin client should still set it.
    """
    raw = (os.getenv("CORS_ALLOWED_ORIGINS") or "").strip()
    if not raw or raw == "*":
        return "*"
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    return origins or "*"


def _entitled_template_loader(inner):
    """A Jinja loader that serves every template as `inner` does, less the enterprise blocks the
    licence does not entitle (core/entitlements.py). The public export removes every block and
    includes the package's partials instead; here the blocks are in the file, so the render removes
    the withheld ones. A fully entitled deployment renders byte-identical pages."""
    from jinja2 import BaseLoader

    class _Loader(BaseLoader):
        def get_source(self, environment, template):
            source, filename, uptodate = inner.get_source(environment, template)
            return _entitlements.strip_unentitled_blocks(source), filename, uptodate

        def list_templates(self):
            return inner.list_templates()

    return _Loader()


def create_app():
    template_folder = os.path.join(project_root, 'templates')
    static_folder = os.path.join(project_root, 'static')

    app = Quart(__name__, template_folder=template_folder, static_folder=static_folder)
    app = cors(app, allow_origin=cors_allowed_origins())

    # Plug-in assets (core/overlay.py): brand partials join the template search path, and a
    # static file the core lacks is served from the first asset root that carries it.
    from trusted_data_agent.core import overlay as _overlay
    _extra_tpl = [str(r / "templates") for r in _overlay.asset_roots() if (r / "templates").is_dir()]
    if _extra_tpl:
        from jinja2 import ChoiceLoader, FileSystemLoader
        app.jinja_loader = ChoiceLoader([app.jinja_loader, FileSystemLoader(_extra_tpl)])
    # A template block of a group the licence withholds (`{# enterprise-begin:x #}`, present in the
    # development repository's templates) is not rendered — core/entitlements.strip_unentitled_blocks.
    app.jinja_loader = _entitled_template_loader(app.jinja_loader)
    if _overlay.asset_roots():
        from quart import send_from_directory as _send_from_directory
        _core_static = app.view_functions.get("static")

        async def _static_with_overlay(filename):
            if not os.path.exists(os.path.join(static_folder, filename)):
                for r in _overlay.asset_roots():
                    if (r / "static" / filename).is_file():
                        return await _send_from_directory(str(r / "static"), filename)
            return await _core_static(filename)

        if _core_static is not None:
            app.view_functions["static"] = _static_with_overlay

    # --- Set Secret Key for Session Management ---
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key:
        app_logger.critical("FATAL: SECRET_KEY environment variable is not set. This is required for session management.")
        raise ValueError("SECRET_KEY is not set. Please set it in your .env file.")
    app.secret_key = secret_key
    # --- End Secret Key ---

    # --- MODIFICATION START: Increase Quart timeouts ---
    # This prevents Quart from closing long SSE streams prematurely (default is 60s)
    # A PDM deploy (industry-model KG constructor with deploy_pdm) runs its whole
    # DDL -> load -> statistics -> KG-import inside ONE request and takes 35-40 min
    # for a ~230-table model on Teradata (DDL is serial, ~1 s per CREATE TABLE).
    # At 30 min Quart cancelled the handler mid-COLLECT STATISTICS -- silently, since
    # CancelledError is not an Exception -- and the physical layer never landed.
    # 4 h covers an ECM-sized deploy; the real fix is a task-shaped deploy (submit +
    # poll), recorded as a follow-up.
    app.config['RESPONSE_TIMEOUT'] = 4 * 3600  # (was 1800; use None for unlimited)
    app.config['REQUEST_TIMEOUT'] = None
    app.config['BODY_TIMEOUT'] = None     # CRITICAL: Disables timeout for reading request body (file uploads)
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB (agent packs can be large)
    # --- MODIFICATION END ---

    # --- White-label branding: every template render receives `brand` -------
    # ONE registration serves all templates (index + the four auth pages and any
    # future template) — no per-route kwargs. Resolution is per-request by Host
    # header (see core/branding.py); with no brands configured this resolves to
    # the code-baked Uderia default and every surface renders byte-identically.
    from trusted_data_agent.core.branding import brand_for_request

    @app.context_processor
    def _inject_brand():
        b = brand_for_request()
        import json as _json
        try:   # the components this deployment carries (the page imports only their renderers)
            from trusted_data_agent.components.manager import get_component_manager
            installed = sorted(get_component_manager().components)
        except Exception:  # noqa: BLE001
            installed = None
        try:   # the licence in force, for the Info box (core/entitlements.summary)
            lic = _entitlements.summary()
        except Exception:  # noqa: BLE001
            lic = None
        return {"brand": b, "brand_public_json": b.public_json(),
                "signin_methods_json": _json.dumps(APP_STATE.get("signin_methods") or {"oauth": True, "sso": True}),
                "installed_components_json": _json.dumps(installed) if installed is not None else "null",
                "license_summary": lic, "license_summary_json": _json.dumps(lic), "entitlement_groups": list(_entitlements.GROUPS),
                "enterprise_component_count": len(_entitlements.ENTERPRISE_COMPONENTS)}

    @app.context_processor
    def _inject_session_policy():
        # Idle limit the browser enforces on real user input (static/js/idleLock.js).
        try:
            from trusted_data_agent.auth.security import session_idle_minutes
            return {"session_idle_minutes": session_idle_minutes()}
        except Exception:
            return {"session_idle_minutes": 0}
    # --- End branding -------------------------------------------------------

    # Plug-ins (the enterprise package, third parties): entry points in the group
    # "uderia.plugins" load here, before the routes, so what they register is in place.
    from trusted_data_agent.core.plugins import load_plugins, apply_blueprints
    load_plugins(app_logger)
    # The licence this server runs on, in the log (its first read happened at import, before logging)
    _lic = _entitlements.summary()
    if _lic["tier"] == "Community":
        app_logger.info("Licence: none (Community) — the public core")
    else:
        app_logger.info(f"Licence: tier {_lic['tier']} for {_lic['holder']} until {str(_lic['expires_at'])[:10]} — entitles "
                        + ("the whole enterprise package" if _lic["all_groups"] else (", ".join(_lic["entitlements"]) or "no group"))
                        + (f" (components: {', '.join(_lic['components'])})" if _lic["components"] and len(_lic["components"]) < len(_entitlements.ENTERPRISE_COMPONENTS) else ""))

    from trusted_data_agent.api.routes import api_bp
    from trusted_data_agent.api.rest_routes import rest_api_bp
    from trusted_data_agent.api.auth_routes import auth_bp
    from trusted_data_agent.api.admin_routes import admin_api_bp
    from trusted_data_agent.api.knowledge_routes import knowledge_api_bp
    from trusted_data_agent.api.contact_routes import contact_bp
    from trusted_data_agent.api.agent_pack_routes import agent_pack_bp
    from trusted_data_agent.api.skills_routes import skills_api_bp
    from trusted_data_agent.api.provenance_routes import provenance_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(rest_api_bp, url_prefix="/api")
    # The sign-in methods this deployment offers — the login and register pages read them
    # (window.UDERIA_SIGNIN) and never ask an absent endpoint (core/entitlements: identity).
    APP_STATE["signin_methods"] = {"oauth": False, "sso": False}
    app.register_blueprint(auth_bp)  # Auth routes are already prefixed with /api/v1/auth
    try:  # Google and GitHub sign-in — the enterprise package's identity entitlement; the core keeps local accounts
        from trusted_data_agent.api.oauth_routes import oauth_bp
        app.register_blueprint(oauth_bp)
        APP_STATE["signin_methods"]["oauth"] = True
    except ImportError:
        app_logger.info("Google/GitHub sign-in not installed — the login page offers local accounts only")
    try:  # identity federation (OIDC, SAML) — the enterprise package; absent in the public core
        from trusted_data_agent.api.sso_routes import sso_bp
        app.register_blueprint(sso_bp)
        APP_STATE["signin_methods"]["sso"] = True
    except ImportError:
        app_logger.info("SSO federation not installed — the login page offers no SSO providers")
    app.register_blueprint(admin_api_bp, url_prefix="/api")  # Phase 4 admin & credential management
    try:  # the system-prompt editor — enterprise package; the public core ships plain prompts
        from trusted_data_agent.api.system_prompts_routes import system_prompts_bp
        app.register_blueprint(system_prompts_bp)
    except ImportError:
        app_logger.info("System-prompt editor not installed")
    app.register_blueprint(knowledge_api_bp, url_prefix="/api")  # Knowledge repository endpoints
    app.register_blueprint(contact_bp)  # Contact form endpoint for promotional website
    app.register_blueprint(agent_pack_bp, url_prefix="/api")  # Agent pack management
    app.register_blueprint(skills_api_bp, url_prefix="/api")  # Skills management
    app.register_blueprint(provenance_bp)  # Execution Provenance Chain endpoints
    # Ontology suite (Platform Ontology + Trust Membrane) — ONE isolated, optional,
    # physically-removable enterprise feature. Registered only when present + gated on.
    _register_optional_enterprise_suite(app)

    from trusted_data_agent.api.kg_marketplace_routes import kg_marketplace_bp
    app.register_blueprint(kg_marketplace_bp, url_prefix="/api")  # KG marketplace

    from trusted_data_agent.api.component_instance_marketplace_routes import component_instance_marketplace_bp
    app.register_blueprint(component_instance_marketplace_bp, url_prefix="/api")  # Component-instance marketplace
    from trusted_data_agent.api.assurance_module_marketplace_routes import assurance_module_marketplace_bp
    app.register_blueprint(assurance_module_marketplace_bp, url_prefix="/api")  # PROP-003 assurance-module marketplace

    from trusted_data_agent.api.connector_routes import connector_bp
    app.register_blueprint(connector_bp)  # Google OAuth connector (Track C)

    # The Flow, Dashboard, Canvas and OBDA-ACT routes belong to enterprise components: present in
    # the full stack (the package's overlay), absent in the public core — the components are absent too.
    for _mod, _name, _what in (
        ("trusted_data_agent.api.flow_routes", "flow_bp", "Flow component"),
        ("trusted_data_agent.api.dashboard_routes", "dashboard_bp", "Dashboard Canvas component"),
        ("trusted_data_agent.api.canvas_routes", "canvas_bp", "Canvas persistence"),
        ("trusted_data_agent.api.action_routes", "action_bp", "PROP-008 OBDA-ACT — signed actions (REST face)"),
    ):
        try:
            _bp = getattr(importlib.import_module(_mod), _name)
        except ImportError:
            app_logger.info(f"{_what} not installed (not in this distribution) — its routes are not served")
            continue
        app.register_blueprint(_bp, url_prefix="/api")
    from trusted_data_agent.api.database_navigator_routes import dbnav_bp
    app.register_blueprint(dbnav_bp, url_prefix="/api")  # Database Navigator component
    from trusted_data_agent.api.files_routes import files_bp
    app.register_blueprint(files_bp, url_prefix="/api")  # PROP-010 Files and file stores
    apply_blueprints(app)  # routes a plug-in serves

    @app.route('/favicon.ico')
    async def favicon():
        """Serve favicon.ico from root path for browser compatibility"""
        return await app.send_static_file('favicon.ico')

    @app.before_request
    async def limit_unauthenticated_api_calls():
        """API calls per minute per IP (Administration → Config → Security → Rate Limiting),
        for /api/ requests without a valid credential; signed-in use is governed per user."""
        from trusted_data_agent.auth.rate_limiter import enforce_ip_api_limit
        return await enforce_ip_api_limit()

    @app.after_request
    async def add_security_headers(response):
        # Allow connections to unpkg for G2Plot if needed, adjust connect-src
        csp_policy = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net https://esm.sh", # Allow inline scripts for auth pages + marked.js + CodeMirror 6 (canvas) + Pyodide WASM
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net", # Allow inline styles for G2Plot tooltips etc. + Prism.js
            "font-src 'self' https://fonts.gstatic.com",
            "connect-src 'self' *.googleapis.com https://*.withgoogle.com https://unpkg.com https://api.github.com https://cdn.jsdelivr.net https://esm.sh", # Added unpkg, GitHub API, jsdelivr (Prism.js sourcemaps), esm.sh (CodeMirror 6)
            "worker-src 'self' blob:",
            "img-src 'self' data:",
            "media-src 'self' blob:" # Allow media from blobs for TTS audio
        ]
        response.headers['Content-Security-Policy'] = "; ".join(csp_policy)
        return response

    @app.after_request
    async def mark_session_state(response):
        """A 401 that refused a web session token says so in ONE header, so the browser's one
        guard (auth.js) can end the dead session instead of every widget painting an empty
        state. The rule is `auth.middleware.session_invalid_marker` (tested in isolation)."""
        try:
            from trusted_data_agent.auth.middleware import SESSION_STATE_HEADER, session_invalid_marker
            if session_invalid_marker(request.headers.get('Authorization', ''), request.path, response.status_code):
                response.headers[SESSION_STATE_HEADER] = 'invalid'
        except Exception:  # noqa: BLE001 — a marker must never break a response
            pass
        return response

    @app.after_request
    async def compress_response(response):
        """gzip text responses. See core/compression.py for the safety rules —
        in particular: SSE (IterableBody) is never touched."""
        from trusted_data_agent.core.compression import compress_response as _compress
        return await _compress(request, response)

    # --- MODIFICATION START: Add startup task hook ---
    @app.before_serving
    async def startup():
        """
        Runs once before the server starts serving requests.
        Used to start background tasks like our RAG worker and initialize RAG independently.
        """
        # The loop that owns the per-user notification queues: a live notice raised in a worker
        # thread (asyncio.to_thread) is handed to it by trust_events.broadcast_notice.
        APP_STATE["serving_loop"] = asyncio.get_running_loop()

        # Initialize authentication database (always required)
        try:
            from trusted_data_agent.auth.database import init_database
            init_database()
        except Exception as e:
            app_logger.error(f"Failed to initialize authentication database: {e}", exc_info=True)
            raise  # Fatal error - cannot run without auth database

        # A built-in connector's identity (type, auth mode, name, version) is owned by its
        # manifest; heal any row written before that rule (fails soft, logs what moved).
        try:
            from trusted_data_agent.core.platform_connector_registry import reconcile_builtin_connectors
            reconcile_builtin_connectors()
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Built-in connector reconciliation failed: {e}")

        # PROP-003 · the assurance substrate + the third-party module plane.
        #
        # Bootstrap FIRST so the shipped bindings and any stored overrides are in force
        # before a turn can run; then discover packs (a no-op unless an admin turned the
        # master on) and register a scorer for every bound third-party property.
        #
        # Wrapped end to end: a pack that will not load must never stop the server, and
        # the substrate's own defaults are already applied by the time discovery runs.
        try:
            from trusted_data_agent.agent import assurance as _assurance
            _assurance.bootstrap()
            from trusted_data_agent.assurance_modules import get_assurance_module_manager
            _mgr = get_assurance_module_manager()
            _packs = _mgr.discover()
            if _packs:
                from trusted_data_agent.eval.scorers.plugin_properties import (
                    sync_plugin_scorers)
                _dims = sync_plugin_scorers(_mgr.tunables())
                app_logger.info(
                    f"Assurance modules: {len(_packs)} pack(s) loaded, "
                    f"{len(_dims)} bound property scorer(s)")
        except Exception as e:
            app_logger.warning(f"Assurance module plane unavailable: {e}")

        # Check voice feature credentials (after init_database syncs TTS mode)
        if APP_CONFIG.VOICE_CONVERSATION_ENABLED:
            if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
                app_logger.warning("Voice feature enabled but GOOGLE_APPLICATION_CREDENTIALS not set — credentials must be provided via config UI")

        # Load configuration from tda_config.json and apply to APP_CONFIG
        from trusted_data_agent.core.config_manager import get_config_manager
        config_manager = get_config_manager()
        loaded_config = config_manager.load_config()
        
        # Load GLOBAL MCP Classification setting from global settings file (overrides environment variable if present)
        # This is a GLOBAL application setting that affects ALL users
        try:
            from pathlib import Path
            import json
            global_settings_file = Path("tda_global_settings.json")
            if global_settings_file.exists():
                with open(global_settings_file, 'r') as f:
                    global_settings = json.load(f)
                if "enable_mcp_classification" in global_settings:
                    APP_CONFIG.ENABLE_MCP_CLASSIFICATION = global_settings["enable_mcp_classification"]
        except Exception as e:
            app_logger.warning(f"Failed to load global settings file: {e}. Using environment default: {APP_CONFIG.ENABLE_MCP_CLASSIFICATION}")
        
        # Initialize RAG early if enabled - independent of LLM/MCP configuration
        if APP_CONFIG.RAG_ENABLED:
            try:
                from pathlib import Path
                from trusted_data_agent.agent.rag_retriever import RAGRetriever
                from trusted_data_agent.agent.rag_template_manager import get_template_manager
                from trusted_data_agent.core.config_manager import get_config_manager
                from trusted_data_agent.core.utils import get_project_root

                app_logger.info("Initializing knowledge retrieval system...")

                # Calculate paths using get_project_root to handle both installed and editable installs
                project_root = get_project_root()
                rag_cases_dir = project_root / APP_CONFIG.RAG_CASES_DIR
                persist_dir = project_root / APP_CONFIG.RAG_PERSIST_DIR

                # Load collections from persistent config
                config_manager = get_config_manager()
                collections_list = config_manager.get_rag_collections()
                APP_STATE["rag_collections"] = collections_list

                # Initialize RAG template manager
                template_manager = get_template_manager()
                templates = template_manager.list_templates()
                APP_STATE['rag_template_manager'] = template_manager

                # The embedding model loads in the BACKGROUND (core/first_start): the server
                # serves at once, the knowledge lane reports Connecting until the model is in
                # memory (23 s from the cache; the first start downloads it once), and the
                # bundled packs are seeded after it. Before 12 Nov 2026 this blocked the start
                # for the whole load (44 s on a busy host).
                from trusted_data_agent.core import first_start as _first_start
                _first_start.schedule(app_logger, rag_cases_dir=rag_cases_dir,
                                      embedding_model_name=APP_CONFIG.RAG_EMBEDDING_MODEL, persist_directory=persist_dir)
                # the tool surface's semantic ranker shares this model: core/first_start warms it
                # AFTER the retriever's model is in memory (a parallel warm raced the load, 12 Nov 2026)

                # Integrity scan: warn about knowledge documents registered but never indexed
                try:
                    from trusted_data_agent.core.collection_db import get_collection_db
                    broken = get_collection_db().scan_broken_knowledge_documents()
                    if broken:
                        for rec in broken:
                            app_logger.warning(
                                f"[INTEGRITY] Knowledge document '{rec['filename']}' "
                                f"(doc_id={rec['id']}, collection_id={rec['collection_id']}, "
                                f"collection='{rec['collection_name']}') has file_size={rec['file_size']} "
                                f"and content_hash='{rec['content_hash']}' — may not be indexed. "
                                "Consider re-uploading via the Knowledge Repository UI."
                            )
                        app_logger.warning(
                            f"[INTEGRITY] {len(broken)} broken knowledge document record(s) found. "
                            "These collections may return empty results causing LLM hallucinations."
                        )
                except Exception as scan_err:
                    app_logger.debug(f"Knowledge integrity scan skipped: {scan_err}")

            except Exception as e:
                app_logger.error(f"Failed to initialize RAG at startup: {e}", exc_info=True)
                app_logger.warning("RAG retriever will be lazy-initialized on first use.")
                APP_STATE["rag_collections"] = []
        else:
            APP_STATE["rag_collections"] = []
        
        # Initialize Extensions manager
        try:
            from trusted_data_agent.extensions.manager import get_extension_manager
            ext_manager = get_extension_manager()
            ext_count = len(ext_manager.extensions)
            APP_STATE['extension_manager'] = ext_manager
            app_logger.info(f"Extension system ready: {ext_count} extension(s) loaded")
        except Exception as e:
            app_logger.error(f"Failed to initialize extensions: {e}", exc_info=True)
            app_logger.warning("Extensions will be lazy-initialized on first use.")

        # Initialize the session summary index (backend-owned; rebuilds if empty/stale)
        from trusted_data_agent.core.session_manager import ensure_session_index_ready
        await ensure_session_index_ready()

        # Start RAG processing workers (configurable via TDA_RAG_WORKERS env var)
        _rag_worker_count = int(os.environ.get('TDA_RAG_WORKERS', '2'))
        for _i in range(_rag_worker_count):
            asyncio.create_task(rag_processing_worker())
        app_logger.info(f"Started {_rag_worker_count} RAG processing worker(s)")

        # Eagerly initialize skill manager so startup log shows which directory is used
        from trusted_data_agent.skills.manager import get_skill_manager
        _sm = get_skill_manager()
        app_logger.info(f"Skills loaded from: {_sm.state_dir} ({len(_sm.manifests)} skill(s))")

        # PROP-009 §4.6: publish signed ledger checkpoints outside this host — only when
        # anchoring is configured and the Evidence routes (Ontology Suite) are present.
        if APP_STATE.get('trust_membrane_available'):
            try:
                from trusted_data_agent.core.trust import anchoring as _anchoring
                if _anchoring.enabled():
                    asyncio.create_task(_anchoring.anchor_loop())
                    app_logger.info(f"Evidence ledger anchoring on: {_anchoring.config()['sink']}")
            except Exception as e:  # noqa: BLE001 — the suite may be absent
                app_logger.info(f"Evidence ledger anchoring not started ({e.__class__.__name__}).")

        # Start Task Scheduler (Track B — autonomous scheduling component)
        try:
            from trusted_data_agent.core.task_scheduler import start_scheduler
            await start_scheduler()
        except Exception as e:
            app_logger.warning(f"Task Scheduler failed to start (non-fatal): {e}")

        # Settle flow runs orphaned by a prior crash/restart (stuck queued/running
        # with a dead in-memory executor) so run history stays honest.
        try:
            from trusted_data_agent.core.flow_db import reconcile_orphaned_runs
            _orphaned = reconcile_orphaned_runs()
            if _orphaned:
                app_logger.info(f"Reconciled {_orphaned} orphaned flow run(s) → cancelled")
        except ImportError:
            pass   # the Flow component is not in this deployment (public core, or not entitled)
        except Exception as e:
            app_logger.warning(f"Flow run reconciliation failed (non-fatal): {e}")

        # PROP-010: move plaintext uploads into the file store once, purge what Recently
        # deleted no longer keeps, then keep purging on a timer.
        try:
            from trusted_data_agent import filestore as _filestore
            await asyncio.to_thread(_filestore.startup)
            asyncio.create_task(_filestore.maintenance_loop())
        except Exception as e:
            app_logger.warning(f"File store startup failed (non-fatal): {e}")

        # Print ready message now that all initialization is complete
        host = APP_STATE.get('server_host', '127.0.0.1')
        port = APP_STATE.get('server_port', 5050)
        print(f"\n{'='*60}")
        print(f"  Web client initialized and ready!")
        print(f"  Navigate to http://{host}:{port}")
        print(f"{'='*60}\n")
    # --- MODIFICATION END ---

    @app.after_serving
    async def shutdown():
        """Graceful shutdown of background services."""
        try:
            from trusted_data_agent.core.task_scheduler import stop_scheduler
            await stop_scheduler()
        except Exception:
            pass
        try:
            from trusted_data_agent.core.session_backends import close_session_backend
            await close_session_backend()
        except Exception:
            pass

    # The served app, for platform operations: an agent's TDA_Platform call runs the real route
    # in-process (core/platform_operations.call), so the REST layer stays the one policy.
    APP_STATE["quart_app"] = app
    return app

app = create_app()

async def main(args): # MODIFIED: Accept args
    # Key policy: warns on insecure key settings; in hardened mode
    # (TDA_SECURITY_PROFILE=hardened or TDA_ENV=production) refuses to start.
    from trusted_data_agent.core.key_policy import enforce_at_startup
    enforce_at_startup()
    host = args.host
    port = args.port
    # Store host/port in APP_STATE so startup() can print the ready message after RAG initialization
    APP_STATE['server_host'] = host
    APP_STATE['server_port'] = port
    app_logger.info(f"Starting server on http://{host}:{port} — please wait for initialization...")
    config = Config()
    config.bind = [f"{host}:{port}"] # MODIFIED: Use dynamic host and port
    config.accesslog = None
    config.errorlog = None
    # Longer Hypercorn timeouts to avoid premature disconnects on long-running SSE streams
    # and to support large file uploads (agent packs up to 500MB)
    config.worker_timeout = 4 * 3600  # matches RESPONSE_TIMEOUT
    config.read_timeout = 4 * 3600    # for slow uploads over network
    # Startup (lifespan) budget. Hypercorn's default is 60 s; on a busy host (e.g. right after a
    # large image unpack) loading the embedding model alone took 44 s and the app never came up.
    # Override with TDA_STARTUP_TIMEOUT_SECONDS.
    try:
        config.startup_timeout = float(os.environ.get("TDA_STARTUP_TIMEOUT_SECONDS", "300"))
    except ValueError:
        config.startup_timeout = 300.0
    await hypercorn.asyncio.serve(app, config, shutdown_trigger=_stop_requested)


#: The sentinel that ends a live notification stream (api/routes.py subscribe).
STREAM_CLOSE = {"type": "__stream_close__"}


def close_live_streams() -> int:
    """End every open notification stream (one per open browser tab). A stream never finishes
    on its own, and hypercorn's graceful stop waits for open responses — an open tab used to
    hold the stop until it was forced (Nov 2026). Each stream's generator returns on the
    sentinel and cleans up after itself. → how many streams were told."""
    told = 0
    for queues in list((APP_STATE.get("notification_queues") or {}).values()):
        for q in list(queues):
            try:
                q.put_nowait(STREAM_CLOSE)
                told += 1
            except Exception:  # noqa: BLE001 — a queue that cannot take it is closing anyway
                pass
    return told


async def _stop_requested() -> None:
    """hypercorn's shutdown trigger: wait for SIGINT/SIGTERM, then end the live streams FIRST,
    so the graceful stop that follows has no response left to wait for."""
    import signal as _signal
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(_signal, name), stop.set)
        except (NotImplementedError, RuntimeError, AttributeError):  # e.g. Windows
            _signal.signal(getattr(_signal, name), lambda *_: loop.call_soon_threadsafe(stop.set))
    await stop.wait()
    APP_STATE["shutting_down"] = True
    n = close_live_streams()
    app_logger.info(f"Stop requested — ended {n} live notification stream(s); stopping the server.")

if __name__ == "__main__":
    # a fresh log per server start (the import-time wipe this replaces)
    for _name in ("llm_conversation.log", "llm_conversation_history.log"):
        try:
            open(os.path.join(LOG_DIR, _name), "w", encoding="utf-8").close()
        except OSError:
            pass
    parser = argparse.ArgumentParser(description="Run the Uderia Platform web client.")
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host address to bind the server to. Use '0.0.0.0' for Docker."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5050,
        help="Port to bind the server to."
    )
    # The GitHub star-count call is OFF by default (Oct 2026): the repository is not public,
    # so the call only ever answered 404 on every page load. `--gitcall` opts in;
    # `--nogitcall` is kept for existing launch lines and always wins.
    parser.add_argument("--gitcall", action="store_true", help="Enable the GitHub API call that fetches the repository star count (off by default).")
    parser.add_argument("--nogitcall", action="store_true", help="Disable the GitHub API call (the default; kept for existing launch lines).")
    parser.add_argument("--offline", action="store_true", help="Use cached HuggingFace models only (skip remote version checks).")
    args = parser.parse_args()

    if args.gitcall and not args.nogitcall:
        APP_CONFIG.GITHUB_API_ENABLED = True
        app_logger.info("GitHub API enabled (--gitcall)")
    else:
        APP_CONFIG.GITHUB_API_ENABLED = False
        app_logger.info("GitHub API disabled (default; pass --gitcall to enable)")

    if args.offline:
        os.environ['HF_HUB_OFFLINE'] = '1'
        app_logger.info("Offline mode: using cached HuggingFace models only")

    try:
        asyncio.run(main(args)) # MODIFIED: Pass args to main
    except KeyboardInterrupt:
        print("\nServer shut down.")