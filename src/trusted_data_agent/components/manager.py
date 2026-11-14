"""
Component Manager: Discovers, loads, and manages Generative UI Components.

Mirrors the ExtensionManager pattern:
- Singleton via get_component_manager()
- Registry-based discovery from component_registry.json
- Auto-discovery from ~/.tda/components/
- Hot-reload via reload()

Discovery order (later overrides earlier):
  1. Built-in (registry):  components/builtin/
  2. Agent Pack:           components installed via agent packs
  3. User (auto-discover): ~/.tda/components/
"""

from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory
import importlib.util
import inspect
import json
import logging
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from trusted_data_agent.components.base import BaseComponentHandler, StructuralHandler, SystemHandler

#: Component types whose manifest may carry a `tool_definition` the model is offered. A
#: system component offers a tool only when its manifest declares one (PROP-011: `needs_input`
#: carries `TDA_AskUser`, the model's own act); no other system component does, so every
#: existing one is untouched.
_TOOL_BEARING_TYPES = ("action", "system")
from trusted_data_agent.components.models import ComponentDefinition

logger = logging.getLogger("quart.app")

# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_manager_instance: Optional["ComponentManager"] = None


def get_component_manager(
    components_dir: Optional[Path] = None,
    user_dir: Optional[Path] = None,
) -> "ComponentManager":
    """
    Get or create the singleton ComponentManager.

    On first call, discovers and loads all components.
    Subsequent calls return the cached instance.
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = ComponentManager(components_dir, user_dir)
    return _manager_instance


def reset_component_manager() -> None:
    """Reset the singleton (for testing or hot-reload)."""
    global _manager_instance
    _manager_instance = None


def get_component_instructions_for_prompt(
    profile_id: Optional[str],
    user_uuid: Optional[str],
    session_data: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Resolve component instructions for injection into any profile's system prompt.

    Encapsulates the full resolve-profile → get-instructions → format pattern
    so that all profile types (tool_enabled, llm_only, genie, rag_focused) use
    the same code path for component instruction injection.

    Returns formatted instruction text ready for {component_instructions_section}
    replacement, or empty string if no active components.
    """
    try:
        manager = get_component_manager()
        profile_config: Dict[str, Any] = {}
        if profile_id and user_uuid:
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                cm = get_config_manager()
                profile_config = cm.get_profile(profile_id, user_uuid) or {}
            except Exception:
                pass

        text = manager.get_instructions_text(profile_config, session_data or {}, user_uuid)
        if text:
            return f"- **Component Guidelines:**\n{text}"
    except Exception as e:
        logger.warning(f"Component instructions assembly failed: {e}")
    return ""


async def get_component_context_enrichment(
    query: str,
    profile_id: Optional[str],
    user_uuid: Optional[str],
    max_nodes: int = 500,
    column_cap: Optional[int] = None,
    session_id: Optional[str] = None,
) -> tuple:
    """
    Gather context enrichment text from all qualifying components.

    Components that set ``supports_context_enrichment: true`` in their manifest
    and whose handler overrides ``get_context_enrichment()`` contribute text
    that is appended to the planner's knowledge context.

    Args:
        query: The user's original input query.
        profile_id: Active profile ID (for component filtering).
        user_uuid: Current user UUID.
        max_nodes: Budget hint for graph-shaped enrichers (e.g. the knowledge
            graph). Passed through only to handlers whose ``get_context_enrichment``
            accepts it; default 500 preserves legacy behaviour.
        session_id: The conversation the query continues. Passed through only to
            handlers that accept it, so an enricher can carry the PREVIOUS turn's
            entities forward ("the names of those customers" names nothing a graph
            holds; the turn before it did). Absent ⇒ query-only, as before.

    Returns:
        Tuple of (combined_text, enrichment_details) where enrichment_details
        is a list of metadata dicts from contributing components, or an empty
        list if none contributed.
    """
    try:
        manager = get_component_manager()
        profile_config: Dict[str, Any] = {}
        if profile_id and user_uuid:
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                cm = get_config_manager()
                profile_config = cm.get_profile(profile_id, user_uuid) or {}
            except Exception as profile_err:
                logger.warning(f"Component context enrichment: profile lookup failed for {profile_id}: {profile_err}")

        text, details = await manager.get_context_enrichment(
            query, profile_id or "", user_uuid or "", profile_config, max_nodes=max_nodes,
            column_cap=column_cap, session_id=session_id)
        logger.debug(
            f"Component context enrichment: text_len={len(text) if text else 0}, "
            f"details_count={len(details) if details else 0}"
        )
        return text, details
    except Exception as e:
        logger.warning(f"Component context enrichment assembly failed: {e}", exc_info=True)
    return "", []


async def get_component_trusted_scope(profile_id: Optional[str], user_uuid: Optional[str]) -> tuple:
    """The active KG's always-on deployed **trusted scope** (databases, concepts) for the
    grounding gate — independent of any per-query enrichment. Never raises."""
    try:
        manager = get_component_manager()
        profile_config: Dict[str, Any] = {}
        if profile_id and user_uuid:
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                profile_config = get_config_manager().get_profile(profile_id, user_uuid) or {}
            except Exception as profile_err:
                logger.warning(f"Trusted scope: profile lookup failed for {profile_id}: {profile_err}")
        return await manager.get_trusted_scope(profile_id or "", user_uuid or "", profile_config)
    except Exception as e:
        logger.warning(f"Component trusted scope failed: {e}")
        return [], []


def get_component_langchain_tools(
    profile_id: Optional[str],
    user_uuid: Optional[str],
    session_id: Optional[str] = None,
    current_turn_number: Optional[int] = None,
    current_query: Optional[str] = None,
    llm_instance: Any = None,
) -> List[Any]:
    """
    Resolve profile → get LangChain component tools.

    ``llm_instance`` is the TURN's handler-lane client (the executor's
    ``profile_llm_instance`` or its ``STATE['llm']``) for components that call an LLM
    (chart mapping Stage 4). Without it the per-user registry is used
    (``mcp_adapter.turn_llm``), never the process-global ``APP_STATE['llm']`` — that
    belongs to whichever user last activated a profile.

    Convenience function parallel to get_component_instructions_for_prompt().
    Profile classes call this one function — all resolution logic is internal.

    Returns:
        List of LangChain StructuredTool objects for active action components,
        or empty list if none.
    """
    try:
        manager = get_component_manager()
        profile_config: Dict[str, Any] = {}
        if profile_id and user_uuid:
            try:
                from trusted_data_agent.core.config_manager import get_config_manager
                cm = get_config_manager()
                profile_config = cm.get_profile(profile_id, user_uuid) or {}
            except Exception:
                pass

        comp_context = {"session_id": session_id, "user_uuid": user_uuid, "profile_id": profile_id}
        # Turn number lets TDA_Dashboard stamp source_turn_id on created tiles
        # (binds MCP-refresh provenance to the originating turn).
        if current_turn_number is not None:
            comp_context["current_turn_number"] = current_turn_number
        # The user's current request — lets components apply deterministic intent
        # detection the LLM may have missed (e.g. TDA_Memory global-scope phrasing).
        if current_query:
            comp_context["user_query"] = current_query

        # Provide llm_callable for components needing LLM assistance
        # (e.g., chart mapping resolution Stage 4). Mirrors adapter.py:1682-1690.
        if user_uuid and session_id:
            async def _comp_llm_callable(
                prompt,
                system_prompt="You are a data analyst.",
                max_tokens=200,
                _uid=user_uuid,
                _sid=session_id,
                _turn_llm=llm_instance,
            ):
                from trusted_data_agent.core.config import APP_STATE
                from trusted_data_agent.llm.handler import call_llm_api
                _llm = _turn_llm
                if _llm is None:
                    from trusted_data_agent.mcp_adapter.adapter import turn_llm
                    _llm = turn_llm(APP_STATE, _uid)[0]
                if _llm:
                    text, in_tok, out_tok, _, _ = await call_llm_api(
                        _llm, prompt, user_uuid=_uid, session_id=_sid,
                        system_prompt_override=system_prompt,
                        reason="Component mapping resolution",
                        disabled_history=True,
                    )
                    return {"content": text, "input_tokens": in_tok, "output_tokens": out_tok}
                return {"content": "", "input_tokens": 0, "output_tokens": 0}
            comp_context["llm_callable"] = _comp_llm_callable

        return manager.get_langchain_tools(profile_config, comp_context)
    except Exception as e:
        logger.warning(f"Component LangChain tools assembly failed: {e}")
    return []


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
# LangChain args_schema builder
# ---------------------------------------------------------------------------


# Manifest arg types whose values are objects with *dynamic* keys (the keys
# are data-dependent, e.g. arbitrary column names or chart roles). These cannot
# be expressed as a closed JSON-Schema object with enumerated `properties`.
# Representing them as an open `object` + `additionalProperties: true` is legal
# JSON Schema, but strict provider validators (Groq, Cerebras, OpenAI strict
# mode) ignore `additionalProperties` and reject every key the model produces.
# To stay universally valid across ALL providers we advertise these arguments
# as JSON-encoded *strings* in the tool schema and decode them back to native
# objects before the handler runs (see ``_decode_json_string_args`` /
# ``get_component_langchain_tools._run_component``).
_OPEN_OBJECT_ARG_TYPES = {"dict", "list[dict]", "object"}


def _coerce_open_object_to_json_str(value: Any) -> Any:
    """Pydantic ``BeforeValidator`` for JSON-string-encoded object args.

    The schema advertises these args as ``type: string``.  A compliant provider
    sends a JSON string (passed through untouched).  A lenient provider that
    ignores the schema and sends a native ``dict``/``list`` is coerced to its
    JSON-string form here so Pydantic validation never fails — the value is
    later decoded back to a native object by ``_decode_json_string_args``.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        import json as _json
        try:
            return _json.dumps(value)
        except (TypeError, ValueError):
            return value
    return value


def _build_args_schema(tool_def: dict) -> type:
    """
    Build a Pydantic BaseModel from a component manifest ``tool_definition``.

    Without a proper ``args_schema``, LangChain's ``StructuredTool`` wraps all
    arguments under a single ``kwargs`` key, which breaks handler argument
    parsing.  This function converts the manifest's ``args`` dict into a
    dynamically-created Pydantic model that LangChain uses as the input schema.

    Dynamic-key object arguments (``dict`` / ``list[dict]``) are advertised as
    JSON-encoded strings so the generated schema contains no open-ended object
    nodes — making it acceptable to every LLM provider's tool-call validator
    (see ``_OPEN_OBJECT_ARG_TYPES``).  The set of such field names is attached
    to the model as ``__json_string_fields__`` so the invocation wrapper can
    decode them back into native objects before the handler runs.
    """
    from pydantic import BaseModel, Field, BeforeValidator
    from typing import Any as _Any, Optional as _Opt, Annotated as _Annotated

    args_spec = tool_def.get("args", {})
    # Scalar/array-of-scalar types map directly; dynamic-key object types are
    # handled separately (advertised as JSON strings).  Both the JSON-Schema
    # spelling ("integer"/"number"/"boolean") and the Python spelling
    # ("int"/"float"/"bool") are accepted so manifests can use either.
    from typing import List as _List
    _scalar_type_map = {
        "string": str,
        "str": str,
        # "list[str]" carries a typed `items` node — Gemini's function-declaration
        # converter REJECTS an items-less array ("items: missing field", found
        # live on Ideate: session 228cae64). Bare "list"/"array" is kept as the
        # legacy any-items mapping for third-party manifests, but emits an
        # items-less array some providers refuse — declare list[str] or
        # list[dict] instead.
        "list[str]": _List[str],
        "list": list,
        "array": list,
        "int": int,
        "integer": int,
        "float": float,
        "number": float,
        "bool": bool,
        "boolean": bool,
    }
    # Reliable `type: string` schema node that survives strict-mode transformers,
    # paired with a before-validator that tolerates a native object/list input.
    _JsonStr = _Annotated[str, BeforeValidator(_coerce_open_object_to_json_str)]

    json_string_fields: set = set()
    annotations: dict = {}
    field_defaults: dict = {}
    for arg_name, arg_def in args_spec.items():
        arg_type = arg_def.get("type", "string")
        description = arg_def.get("description", "")
        required = arg_def.get("required", False)

        if arg_type in _OPEN_OBJECT_ARG_TYPES:
            # Advertise as a JSON string (universal provider compatibility).
            # The runtime type is a coercing `str` so the emitted schema is a
            # plain `type: string` node (no open object for strict validators
            # to reject) while a native object/list input is still tolerated.
            json_string_fields.add(arg_name)
            shape = (
                "a JSON array of objects (e.g. '[{\"col\": value}, ...]')"
                if arg_type == "list[dict]"
                else "a JSON object (e.g. '{\"key\": \"value\"}')"
            )
            full_desc = f"{description} Provide as a JSON-encoded string representing {shape}.".strip()
            if required:
                annotations[arg_name] = _JsonStr
                field_defaults[arg_name] = Field(..., description=full_desc)
            else:
                annotations[arg_name] = _Opt[_JsonStr]
                field_defaults[arg_name] = Field(default=None, description=full_desc)
        else:
            py_type = _scalar_type_map.get(arg_type, _Any)
            if required:
                annotations[arg_name] = py_type
                field_defaults[arg_name] = Field(description=description)
            else:
                annotations[arg_name] = _Opt[py_type]
                field_defaults[arg_name] = Field(default=None, description=description)

    # Dynamically create a BaseModel subclass with the computed fields
    model_name = tool_def.get("name", "ComponentTool") + "Input"
    namespace = {"__annotations__": annotations, **field_defaults}
    model = type(model_name, (BaseModel,), namespace)
    # Record which fields are JSON-string-encoded so the invocation wrapper can
    # decode them back to native objects before calling the handler.
    model.__json_string_fields__ = json_string_fields
    return model


def _decode_json_string_args(kwargs: dict, json_string_fields: set) -> dict:
    """Decode JSON-string-encoded object args back into native dict/list.

    Component tool schemas advertise dynamic-key object arguments as JSON
    strings (see ``_build_args_schema``).  Handlers expect native objects, so
    this restores them transparently.  Values that are already native objects
    (a lenient provider may send them) pass through unchanged; un-parseable
    strings are left as-is for the handler's own tolerance to deal with.
    """
    if not json_string_fields:
        return kwargs
    import json as _json
    for _f in json_string_fields:
        _v = kwargs.get(_f)
        if isinstance(_v, str):
            _raw = _v.strip()
            if not _raw:
                continue
            try:
                kwargs[_f] = _json.loads(_raw)
            except (ValueError, TypeError):
                # Leave the raw string in place; the handler (e.g. chart's
                # _transform_chart_data) has its own format tolerance.
                pass
    return kwargs


# ---------------------------------------------------------------------------

class ComponentManager:
    """
    Manages Generative UI Components loaded from manifest + handler files.

    Each component lives in its own directory with:
      - manifest.json  — metadata, tool definition, render targets
      - handler.py     — Python handler (subclass of BaseComponentHandler)
      - renderer.js    — Frontend renderer (served via API)
      - instructions   — LLM prompt text (optional)
    """

    def __init__(
        self,
        components_dir: Optional[Path] = None,
        user_dir: Optional[Path] = None,
    ):
        # Resolve project root → components/ directory
        if components_dir is None:
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            components_dir = project_root / "components"

        self.components_dir = components_dir
        self.builtin_dir = components_dir / "builtin"
        self.registry_file = components_dir / "component_registry.json"
        self.schemas_dir = components_dir / "schemas"

        # Manifest schema validator (root-cause guard against manifest drift).
        # Built once; None if jsonschema or the schema file is unavailable, in
        # which case validation degrades to a no-op (loading still works).
        self._manifest_validator = self._build_manifest_validator()

        # User override directory
        self.user_dir = user_dir or (Path.home() / ".tda" / "components")

        # Loaded state
        self.components: Dict[str, ComponentDefinition] = {}
        self.registry: Dict[str, Any] = {}

        # Tool name → component_id lookup for fast handler routing
        self._tool_to_component: Dict[str, str] = {}

        # Ensure directories exist
        self.builtin_dir.mkdir(parents=True, exist_ok=True)
        self.user_dir.mkdir(parents=True, exist_ok=True)

        # Load
        self._load_registry()
        self._discover_and_load()

    # ------------------------------------------------------------------
    # Manifest schema validation
    # ------------------------------------------------------------------

    def _build_manifest_validator(self):
        """Build a cached JSON Schema validator for component manifests.

        Returns a Draft202012Validator, or None if validation can't be set up
        (missing schema file or jsonschema package) — in which case manifest
        validation degrades to a no-op so component loading never breaks.
        """
        schema_file = self.schemas_dir / "component_manifest_v1.schema.json"
        try:
            import jsonschema  # declared in requirements.txt
            schema = json.loads(schema_file.read_text(encoding="utf-8"))
            return jsonschema.Draft202012Validator(schema)
        except FileNotFoundError:
            logger.info("Component manifest schema not found — manifest validation disabled")
        except ImportError:
            logger.info("jsonschema not installed — component manifest validation disabled")
        except Exception as e:
            logger.warning(f"Could not build component manifest validator: {e}")
        return None

    def _validate_manifest(self, manifest: Dict[str, Any], comp_dir: Path) -> None:
        """Validate a manifest against the schema, logging each violation.

        Warn-level and non-fatal: a non-conformant manifest still loads (so a
        single bad component can't take down startup), but every drift is now
        surfaced in the logs instead of failing silently.
        """
        if not self._manifest_validator:
            return
        errors = sorted(
            self._manifest_validator.iter_errors(manifest),
            key=lambda e: list(e.path),
        )
        for e in errors:
            path = "/".join(str(p) for p in e.path) or "(root)"
            logger.warning(
                f"Component manifest schema violation in '{comp_dir.name}': "
                f"[{path}] {e.message}"
            )

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def _load_registry(self) -> None:
        """Load the component registry from disk."""
        if not self.registry_file.exists():
            logger.info(
                f"Component registry not found at {self.registry_file}. "
                "Using empty registry — will auto-discover only."
            )
            self.registry = {
                "registry_version": "1.0.0",
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "components": [],
            }
            return

        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                self.registry = json.load(f)
            count = len(self.registry.get("components", []))
            logger.info(f"Loaded component registry with {count} component(s)")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse component registry: {e}")
            self.registry = {"components": []}

    # ------------------------------------------------------------------
    # Discovery and loading
    # ------------------------------------------------------------------

    def _builtin_dirs(self) -> List[Path]:
        """The directories builtin components are read from: the repository's `components/builtin`
        and the same directory of every asset root a plug-in registered (core/overlay.py)."""
        dirs = [self.builtin_dir] if self.builtin_dir.exists() else []
        try:
            from trusted_data_agent.core import overlay
            dirs += [d for d in overlay.asset_dirs("components/builtin") if d not in dirs]
        except Exception:  # noqa: BLE001 — the overlay is optional
            pass
        return dirs

    @staticmethod
    def _entitled(comp_id: str) -> bool:
        """An enterprise component the licence does not entitle is not installed — it behaves as
        in the public core (core/entitlements.component_entitled; a public component always is)."""
        try:
            from trusted_data_agent.core import entitlements
            ok = entitlements.component_entitled(comp_id)
        except Exception:  # noqa: BLE001 — never let the gate stop discovery
            return True
        if not ok:
            logger.info(f"Component '{comp_id}': not licensed in this deployment (entitlement enterprise-components)")
        return ok

    def _discover_and_load(self) -> None:
        """Discover and load components from all sources."""
        self.components.clear()
        self._tool_to_component.clear()

        # 1. Load built-in components from registry
        for entry in self.registry.get("components", []):
            comp_id = entry.get("component_id")
            plugin_dir = entry.get("plugin_directory", comp_id)
            comp_dir = next((d / plugin_dir for d in self._builtin_dirs() if (d / plugin_dir).exists()),
                            self.builtin_dir / plugin_dir)

            if not comp_dir.exists():
                # a registry entry whose component this distribution does not carry (the
                # enterprise components, in the public core) — not a defect
                logger.info(f"Component '{comp_id}': not installed in this distribution ({comp_dir})")
                continue
            if not self._entitled(comp_id):
                continue

            self._load_component(comp_dir, source="builtin")

        # 2. Auto-discover from builtin (not in registry) — the repository's directory and the
        #    `components/builtin` of every asset root a plug-in registered (core/overlay.py): the
        #    enterprise package carries nine builtin components the public core does not.
        for builtin_dir in self._builtin_dirs():
            for subdir in sorted(builtin_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                manifest_file = subdir / "manifest.json"
                if manifest_file.exists():
                    # Check if already loaded via registry
                    try:
                        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                        comp_id = manifest.get("component_id")
                        if comp_id and comp_id not in self.components and self._entitled(comp_id):
                            self._load_component(subdir, source="builtin")
                    except (json.JSONDecodeError, OSError):
                        pass

        # 3. Auto-discover user components
        if self.user_dir.exists():
            for subdir in sorted(self.user_dir.iterdir()):
                if not subdir.is_dir():
                    continue
                manifest_file = subdir / "manifest.json"
                if manifest_file.exists():
                    self._load_component(subdir, source="user")

        logger.info(
            f"ComponentManager: loaded {len(self.components)} component(s): "
            f"{', '.join(self.components.keys())}"
        )

        # Sync discovered components to database for admin visibility
        self._sync_to_database()

    def _sync_to_database(self) -> None:
        """Sync discovered components to installed_components table for admin visibility."""
        try:
            import sqlite3
            from trusted_data_agent.core.config import APP_CONFIG
            db_path = APP_CONFIG.AUTH_DB_PATH
            if not db_path:
                return

            # AUTH_DB_PATH may be a SQLAlchemy URL (e.g. "sqlite:////abs/path/tda_auth.db").
            # sqlite_util.connect() requires a plain file path, so strip the protocol prefix.
            if isinstance(db_path, str) and db_path.startswith("sqlite:///"):
                db_path = db_path[len("sqlite:///"):]

            conn = sqlite_util.connect(db_path)
            cursor = conn.cursor()

            for comp_id, comp_def in self.components.items():
                cursor.execute("""
                    INSERT OR REPLACE INTO installed_components
                    (component_id, display_name, version, source, agent_pack_id, is_active, manifest_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
                """, (
                    comp_id,
                    comp_def.display_name,
                    comp_def.version,
                    comp_def.source,
                    comp_def.agent_pack_id,
                    json.dumps(comp_def.manifest) if comp_def.manifest else None,
                ))

            # Deactivate components no longer on disk
            discovered_ids = list(self.components.keys())
            if discovered_ids:
                placeholders = ','.join('?' * len(discovered_ids))
                cursor.execute(
                    f"UPDATE installed_components SET is_active = 0 WHERE component_id NOT IN ({placeholders})",
                    discovered_ids,
                )

            conn.commit()
            conn.close()
            logger.debug(f"Synced {len(self.components)} component(s) to installed_components table")
        except Exception as e:
            logger.warning(f"Failed to sync components to database: {e}")

    def _load_component(self, comp_dir: Path, source: str = "builtin") -> None:
        """Load a single component from its directory."""
        manifest_file = comp_dir / "manifest.json"
        if not manifest_file.exists():
            logger.warning(f"No manifest.json in {comp_dir}, skipping")
            return

        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load manifest from {comp_dir}: {e}")
            return

        # Validate against the manifest schema (warn-level, non-fatal) so manifest
        # drift surfaces in logs instead of silently diverging from the contract.
        self._validate_manifest(manifest, comp_dir)

        comp_id = manifest.get("component_id")
        if not comp_id:
            logger.warning(f"Manifest in {comp_dir} missing 'component_id', skipping")
            return

        # Build ComponentDefinition
        comp_def = ComponentDefinition(
            component_id=comp_id,
            display_name=manifest.get("display_name", comp_id),
            description=manifest.get("description", ""),
            version=manifest.get("version", "1.0.0"),
            component_type=manifest.get("component_type", "action"),
            category=manifest.get("category", "General"),
            source=source,
            agent_pack_id=manifest.get("agent_pack_id"),
            directory=comp_dir,
            manifest_path=manifest_file,
            manifest=manifest,
            tool_definition=manifest.get("tool_definition"),
            instructions_config=manifest.get("instructions", {}),
            render_targets=manifest.get("render_targets", {"default": "inline", "supports": ["inline"]}),
            frontend_config=manifest.get("frontend", {}),
            profile_defaults=manifest.get("profile_defaults", {}),
        )

        # Load handler
        backend_config = manifest.get("backend", {})
        handler_file = backend_config.get("handler_file")
        if handler_file:
            handler_path = comp_dir / handler_file
            comp_def.handler_path = handler_path
            if handler_path.exists():
                handler = self._load_handler(
                    handler_path,
                    backend_config.get("handler_class"),
                    comp_id,
                )
                comp_def.handler = handler
            else:
                logger.warning(
                    f"Component '{comp_id}': handler file not found at {handler_path}"
                )

        # Set renderer path (relative for API serving)
        renderer_file = comp_def.frontend_config.get("renderer_file")
        if renderer_file:
            comp_def.renderer_path = comp_dir / renderer_file

        # Register
        self.components[comp_id] = comp_def

        # Build tool → component lookup
        tool_name = comp_def.get_tool_name()
        if tool_name:
            self._tool_to_component[tool_name] = comp_id

        logger.info(
            f"  Loaded component: {comp_id} "
            f"(type={comp_def.component_type}, "
            f"tool={tool_name or 'none'}, "
            f"source={source})"
        )

    def _load_handler(
        self,
        handler_path: Path,
        handler_class_name: Optional[str],
        comp_id: str,
    ) -> Optional[Any]:
        """Import and instantiate a component handler from a Python file."""
        module_name = f"component_handler_{comp_id}"

        try:
            handler_dir = handler_path.parent
            init_file = handler_dir / "__init__.py"

            # If the component directory has __init__.py, register it as a
            # package so that relative imports (from .base import X) work.
            if init_file.exists():
                package_name = f"component_pkg_{comp_id}"
                handler_module_name = f"{package_name}.handler"

                if package_name not in sys.modules:
                    pkg = types.ModuleType(package_name)
                    pkg.__path__ = [str(handler_dir)]
                    pkg.__file__ = str(init_file)
                    pkg.__package__ = package_name
                    sys.modules[package_name] = pkg

                spec = importlib.util.spec_from_file_location(
                    handler_module_name, handler_path
                )
                if spec is None or spec.loader is None:
                    logger.error(f"Component '{comp_id}': cannot create module spec for {handler_path}")
                    return None

                module = importlib.util.module_from_spec(spec)
                module.__package__ = package_name
                sys.modules[handler_module_name] = module
                # Also register under the flat name for backward compatibility
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            else:
                spec = importlib.util.spec_from_file_location(module_name, handler_path)
                if spec is None or spec.loader is None:
                    logger.error(f"Component '{comp_id}': cannot create module spec for {handler_path}")
                    return None

                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

            # Find the handler class
            if handler_class_name:
                cls = getattr(module, handler_class_name, None)
                if cls is None:
                    logger.error(
                        f"Component '{comp_id}': class '{handler_class_name}' "
                        f"not found in {handler_path}"
                    )
                    return None
            else:
                # Auto-detect: first BaseComponentHandler or SystemHandler subclass
                cls = None
                for _, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, BaseComponentHandler)
                        and obj is not BaseComponentHandler
                        and obj is not StructuralHandler
                    ):
                        cls = obj
                        break
                    if (
                        issubclass(obj, SystemHandler)
                        and obj is not SystemHandler
                    ):
                        cls = obj
                        break

                if cls is None:
                    logger.error(
                        f"Component '{comp_id}': no BaseComponentHandler or SystemHandler "
                        f"subclass found in {handler_path}"
                    )
                    return None

            return cls()

        except Exception as e:
            logger.error(f"Component '{comp_id}': failed to load handler: {e}")
            return None

    # ------------------------------------------------------------------
    # Public API: Component access
    # ------------------------------------------------------------------

    def get_component(self, component_id: str) -> Optional[ComponentDefinition]:
        """Get a component by ID."""
        return self.components.get(component_id)

    def get_handler(self, tool_name: str) -> Optional[BaseComponentHandler]:
        """Get the handler for a given tool name (e.g., 'TDA_Charting')."""
        comp_id = self._tool_to_component.get(tool_name)
        if comp_id:
            comp = self.components.get(comp_id)
            if comp and comp.handler:
                return comp.handler
        return None

    def is_component_tool(self, tool_name: str) -> bool:
        """Check if a tool name belongs to a component."""
        return tool_name in self._tool_to_component

    def get_component_by_tool(self, tool_name: str) -> Optional[ComponentDefinition]:
        """Resolve the ComponentDefinition that owns a tool name, if any.

        Covers components from every source (builtin, user, agent-pack/marketplace),
        so callers can classify a tool without enumerating component names.
        """
        comp_id = self._tool_to_component.get(tool_name)
        return self.components.get(comp_id) if comp_id else None

    def get_all_components(self) -> List[ComponentDefinition]:
        """Get all loaded components."""
        return list(self.components.values())

    def get_builtin_components(self) -> List[ComponentDefinition]:
        """Get only built-in components."""
        return [c for c in self.components.values() if c.source == "builtin"]

    def get_system_components(self) -> List[ComponentDefinition]:
        """Get all system components (component_type == 'system')."""
        return [c for c in self.components.values() if c.component_type == "system"]

    def get_system_component(self, component_id: str) -> Optional[ComponentDefinition]:
        """
        Get a system component by ID.

        System components (like context_window) manage platform infrastructure
        and have different lifecycle than action/structural components — they
        don't expose tools or inject LLM instructions.

        Args:
            component_id: The system component's unique identifier.

        Returns:
            ComponentDefinition if found and is a system component, else None.
        """
        comp = self.components.get(component_id)
        if comp and comp.component_type == "system":
            return comp
        return None

    # ------------------------------------------------------------------
    # Public API: Profile-filtered queries
    # ------------------------------------------------------------------

    def get_active_components(
        self,
        profile_config: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> List[ComponentDefinition]:
        """
        Get components that are active for a given profile configuration.

        Enablement is resolved by ``components.enablement`` — THE one rule, shared with
        the REST profile view, the next-step recommender and the memory adapter. This
        method used to carry its own weaker copy (absent entry -> True, and no reading
        of the admin global gate at all), which is why an admin's global disable left
        the tool bound to the model. See
        ``docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md``.

        Args:
            profile_config: Profile dict with optional 'componentConfig' key. An absent
                entry falls through to the manifest's ``enabled_for`` default for this
                profile type — NOT to True.
            user_uuid: enables the per-user admin override and per-user default steps.
                Omit only where the caller genuinely has no user context; the global
                admin gate applies either way.

        Returns:
            List of active ComponentDefinition objects.
        """
        from trusted_data_agent.components import enablement

        enabled = enablement.filter_enabled(
            self.components.values(), profile_config, user_uuid
        )
        # NOTE on `conflicts_with`: chart and dashboard declare each other exclusive, yet
        # BOTH are in each other's `enabled_for` for all four profile types — so the
        # DEFAULT state violates the declaration. A warning here therefore fires on every
        # ordinary turn, which is the "false alarm trains operators to ignore alarms"
        # failure. `conflicts_with` is enforced only by the profile editor (it auto-unchecks
        # the other side); making it a real rule needs a product decision on which side wins
        # and a manifest fix, so it is recorded as open rather than half-enforced here.
        # See docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md.
        return [c for c in enabled if self._component_relevant(c, profile_config)]

    @staticmethod
    def _component_relevant(comp_def, profile_config: Dict[str, Any]) -> bool:
        """Consult the handler's relevance gate (PROP-005). Default True for
        every component that doesn't override it — existing behavior untouched
        (freeze gate B5). A RAISING hook reads as True (fail toward the
        component's normal state); a component wanting inert-on-error returns
        False itself."""
        try:
            handler = comp_def.handler
            if handler is None:
                return True
            return bool(handler.is_profile_relevant(profile_config or {}))
        except Exception:
            return True

    def get_tool_definitions(
        self,
        profile_config: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get tool definitions for all active action components.

        Returns tool definitions formatted for injection into the LLM's
        tools context, filtered by profile component configuration.
        """
        active = self.get_active_components(profile_config, user_uuid)
        tools = []
        for comp in active:
            if comp.tool_definition and comp.component_type in _TOOL_BEARING_TYPES \
                    and self._component_offers_tool(comp, profile_config):
                tools.append(self.tool_definition_for(comp, profile_config))
        return tools

    @staticmethod
    def _component_offers_tool(comp_def, profile_config: Optional[Dict[str, Any]]) -> bool:
        """The handler's tool gate (``offers_tool``) — the tool advertised only where the
        agent opted in. Shares ``enablement.offers_tool`` so the surface and the companion
        auto-assign read one rule."""
        from trusted_data_agent.components import enablement
        return enablement.offers_tool(comp_def, profile_config)

    def tool_definition_for(self, comp_def, profile_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """THE tool definition a model sees for ``comp_def`` under ``profile_config``:
        [[gate:x]] blocks resolved in the description and in every argument's
        description. Every place that hands a component's tool text to a model —
        the planner catalog, the ReAct LangChain tools, the Optimize tools context
        — must read it through here; reading ``comp_def.tool_definition`` directly
        leaks gate markup and advertises capabilities whose gate is closed (found
        2026-09-24: the ReAct path and the Optimize tools context both did)."""
        from trusted_data_agent.components import signals as _signals
        gated = self._gated_tool_definition(comp_def.tool_definition, profile_config)
        # Component signals: a component that declares `signals.consumes_refs` takes
        # `source_ref` — the name of an earlier result it reads itself (components/signals.py).
        return _signals.with_source_ref_arg(gated, getattr(comp_def, "manifest", None))

    @classmethod
    def _gated_tool_definition(cls, tool_def: Dict[str, Any],
                               profile_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """A tool definition with [[gate:x]] blocks resolved in its description
        strings — the tool CATALOG is an instruction surface too (the planner
        reads descriptions), so it honours the same gates. Shallow-copied only
        where a string actually changes; otherwise returned as-is."""
        try:
            blob = json.dumps(tool_def) if isinstance(tool_def, dict) else ""
            if "[[gate:" not in blob:
                return tool_def
            out = dict(tool_def)
            desc = out.get("description")
            if isinstance(desc, str) and "[[gate:" in desc:
                out["description"] = cls.apply_instruction_gates(desc, profile_config)
            # arg-level prose is planner-visible too (the args ride the tool
            # catalog): session a52f10d2 planned query_product with the master
            # off partly because product_name/measures/… still described it
            args = out.get("args")
            if isinstance(args, dict):
                new_args = {}
                for k, v in args.items():
                    if (isinstance(v, dict) and isinstance(v.get("description"), str)
                            and "[[gate:" in v["description"]):
                        v = dict(v)
                        v["description"] = cls.apply_instruction_gates(v["description"], profile_config)
                        # An argument that exists ONLY behind a gate (its whole description is
                        # one gated block) is dropped when the gate is closed — a capability's
                        # argument must not reach the schema of an agent that lacks it.
                        if not v["description"].strip():
                            continue
                    new_args[k] = v
                out["args"] = new_args
            return out
        except Exception:  # noqa: BLE001
            return tool_def

    def get_action_tool_status(
        self,
        profile_config: Dict[str, Any],
    ) -> List[tuple]:
        """
        Get every action-component tool definition paired with its per-profile
        ``disabled`` flag.

        Unlike :meth:`get_tool_definitions` (which returns only *active* tools),
        this returns ALL action component tools — disabled ones included — so the
        global tool universe (``structured_tools`` → ``mcp_tools``) stays complete
        for validation. The ``disabled`` flag mirrors :meth:`get_active_components`
        enable logic, and every ``tools_context`` builder already filters on it, so
        components deactivated in the profile no longer leak into the system or
        planner prompt.

        Returns:
            List of ``(ComponentDefinition, disabled: bool)`` tuples for action
            components, in registration order.
        """
        component_config = profile_config.get("componentConfig")
        out: List[tuple] = []
        for comp_id, comp_def in self.components.items():
            if not comp_def.tool_definition or comp_def.component_type not in _TOOL_BEARING_TYPES:
                continue
            if component_config is None:
                # Backward compat: no componentConfig → all components active.
                disabled = False
            else:
                disabled = not component_config.get(comp_id, {}).get("enabled", True)
            # Relevance gate (PROP-005): an irrelevant tool reads DISABLED —
            # the validation universe stays complete, the planner never offers it.
            if not disabled and not self._component_relevant(comp_def, profile_config):
                disabled = True
            # Tool gate (PROP-010): a tool the agent has not opted into reads DISABLED too.
            if not disabled and not self._component_offers_tool(comp_def, profile_config):
                disabled = True
            out.append((comp_def, disabled))
        return out

    # ── gated instruction blocks ─────────────────────────────────────────────
    # OBDA-READ IS ADDITIVE (charter #15): instruction text that advertises a
    # master-gated capability must vanish with its master, or the model is
    # instructed to prefer a door that refuses (found live, session dd40d071:
    # with OBDA-READ off the KG instructions still said "prefer query_product …
    # execute the governed statement exactly as given", and the plan's step
    # goals quoted it back verbatim). Convention: [[gate:<name>]]…[[/gate]] in
    # instructions.json / manifest description text; the block's CONTENT is
    # kept (unwrapped) when the gate is open, dropped when closed. An unknown
    # gate name keeps the content and WARNS — visible text is detectable,
    # silently vanished text is not.
    _GATE_BLOCK_RE = re.compile(r"\[\[gate:([a-z_]+)\]\](.*?)\[\[/gate\]\]", re.DOTALL)

    @staticmethod
    def _instruction_gate_open(name: str, profile_config: Optional[Dict[str, Any]] = None) -> bool:
        # PER-AGENT gates: open only for an agent that opted in. No profile → closed (a
        # capability must not be advertised to an agent nobody could name).
        if name == "navigator_query":
            try:
                q = (((profile_config or {}).get("componentConfig") or {})
                     .get("database_navigator") or {}).get("query") or {}
                return isinstance(q, dict) and q.get("enabled") is True
            except Exception:  # noqa: BLE001
                return False
        try:
            from trusted_data_agent.core.config import APP_CONFIG
            if name in ("obda", "act"):
                # the OBDA planes: open for an agent whose plane's COMPONENT is enabled (the one
                # component chain — global · user · the agent's toggle); no profile → closed
                from trusted_data_agent.core import obda_planes as _planes
                if not profile_config:
                    return False
                return _planes.plane_enabled("read" if name == "obda" else "act", profile_config)
            if name == "files":
                # PROP-010: Files-backed capabilities (a flow's File nodes) are advertised only
                # to an agent that has Files — the global gate and the agent's own toggle
                if not profile_config:
                    return False
                cc = ((profile_config.get("componentConfig") or {}).get("files") or {})
                if isinstance(cc, dict) and cc.get("enabled") is False:
                    return False
                from trusted_data_agent.components.settings import is_component_available
                return bool(is_component_available("files"))
            if name == "compute_sql":
                # the combine step: its own switch, meaningful only where the result sets exist
                return bool(getattr(APP_CONFIG, "COMPUTE_LINEAGE", False)) and \
                    bool(getattr(APP_CONFIG, "COMPUTE_SQL", False))
        except Exception:  # noqa: BLE001
            return True
        logger.warning(f"unknown instruction gate '{name}' — content kept (fail-visible)")
        return True

    @classmethod
    def apply_instruction_gates(cls, text: str, profile_config: Optional[Dict[str, Any]] = None) -> str:
        """Resolve [[gate:x]] blocks in instruction text. Never raises; text
        without gate markers is returned byte-identical. ``profile_config`` lets a
        gate depend on the agent (a per-profile opt-in); deployment gates ignore it."""
        if not text or "[[gate:" not in text:
            return text
        try:
            def _sub(m):
                return m.group(2) if cls._instruction_gate_open(m.group(1), profile_config) else ""
            return cls._GATE_BLOCK_RE.sub(_sub, text)
        except Exception:  # noqa: BLE001
            return text

    def get_instructions_text(
        self,
        profile_config: Dict[str, Any],
        session_data: Optional[Dict[str, Any]] = None,
        user_uuid: Optional[str] = None,
    ) -> str:
        """
        Assemble active components' LLM instructions into a single text block.

        This is injected into the system prompt via {component_instructions_section}.

        Args:
            profile_config: Profile dict with optional componentConfig.
            session_data: Session data dict (for intensity resolution).

        Returns:
            Combined instruction text, or empty string if none.
        """
        session_data = session_data or {}
        component_config = profile_config.get("componentConfig", {})
        active = self.get_active_components(profile_config, user_uuid)

        parts = []
        for comp in active:
            if not comp.instructions_config:
                continue
            # an action component's instructions teach its TOOL: withheld where the tool is
            # (PROP-010 — TDA_Files is offered only to an agent that opted in)
            if comp.component_type in _TOOL_BEARING_TYPES and comp.tool_definition \
                    and not self._component_offers_tool(comp, profile_config):
                continue

            # Resolve intensity
            comp_settings = component_config.get(comp.component_id, {})
            intensity = comp_settings.get(
                "intensity",
                session_data.get("charting_intensity", "medium"),
            )

            instructions = self.apply_instruction_gates(comp.get_instructions(intensity), profile_config)
            if instructions:
                parts.append(instructions)

        return "\n\n".join(parts) if parts else ""

    def get_tactical_prompt_rules(
        self,
        profile_config: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> str:
        """
        Collect tactical-LLM prompt rules from the profile's ACTIVE components.

        Unlike :meth:`get_instructions_text` (strategic/system-prompt guidance),
        these are condensed rules injected into ``WORKFLOW_TACTICAL_PROMPT`` via
        the ``{chart_mapping_rule_section}`` placeholder. The tactical call does
        not receive component instructions, so a component that needs to steer
        the per-phase tactical LLM declares a manifest ``tactical_prompt_rule``
        string. It is injected ONLY when the component is enabled for the
        profile (mirrors the enable filter in :meth:`get_active_components`), so
        a disabled component leaves no trace in the tactical prompt.

        Component-agnostic: any component may declare ``tactical_prompt_rule``.

        Returns:
            Combined rule text, or empty string if no active component declares one.
        """
        parts: List[str] = []
        for comp in self.get_active_components(profile_config, user_uuid):
            rule = (comp.manifest or {}).get("tactical_prompt_rule")
            if rule and isinstance(rule, str):
                parts.append(self.apply_instruction_gates(rule, profile_config).strip())
        return "\n".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # Public API: Context enrichment (guardrail)
    # ------------------------------------------------------------------

    async def get_trusted_scope(
        self,
        profile_id: str,
        user_uuid: str,
        profile_config: Dict[str, Any],
    ) -> tuple:
        """The active KG component's always-on deployed **trusted scope** (databases,
        concept names) — query-independent, for the grounding gate. Returns the first
        contributing handler's scope, or ([], []) if none."""
        for comp in self.get_active_components(profile_config, user_uuid):
            handler = comp.handler
            if handler is not None and hasattr(handler, "get_trusted_scope"):
                try:
                    dbs, ents = await handler.get_trusted_scope(profile_id, user_uuid)
                    if dbs:
                        return dbs, ents
                except Exception:  # noqa: BLE001
                    continue
        return [], []

    async def get_context_enrichment(
        self,
        query: str,
        profile_id: str,
        user_uuid: str,
        profile_config: Dict[str, Any],
        max_nodes: int = 500,
        column_cap: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> tuple:
        """
        Collect context enrichment text from qualifying components.

        Iterates active components, checks the ``supports_context_enrichment``
        manifest flag, and calls each qualifying handler's
        ``get_context_enrichment()`` method.

        Returns:
            Tuple of (combined_text, enrichment_details) where enrichment_details
            is a list of metadata dicts from contributing components.
        """
        active = self.get_active_components(profile_config, user_uuid)
        parts: List[str] = []
        details: List[Dict[str, Any]] = []

        enrichment_capable_count = 0
        for comp in active:
            # Check manifest flag
            backend_cfg = (comp.manifest or {}).get("backend", {})
            if not backend_cfg.get("supports_context_enrichment", False):
                continue

            handler = comp.handler
            if handler is None:
                continue

            enrichment_capable_count += 1
            try:
                # Pass the budget hint only to handlers that accept it, so legacy
                # / third-party enrichers with the 3-arg signature never break.
                _enrich_kwargs = {}
                try:
                    import inspect as _inspect
                    _params = _inspect.signature(handler.get_context_enrichment).parameters
                    if "max_nodes" in _params:
                        _enrich_kwargs["max_nodes"] = max_nodes
                    if column_cap is not None and "column_cap" in _params:
                        _enrich_kwargs["column_cap"] = column_cap
                    if session_id and "session_id" in _params:
                        _enrich_kwargs["session_id"] = session_id
                except (ValueError, TypeError):
                    pass
                result = await handler.get_context_enrichment(
                    query, profile_id, user_uuid, **_enrich_kwargs)
                # Handlers return (text, metadata) tuple or plain string (backward compat)
                if isinstance(result, tuple):
                    text, metadata = result
                else:
                    text, metadata = result, None

                if text:
                    parts.append(text)
                    if metadata:
                        metadata["component_id"] = comp.component_id
                        details.append(metadata)
                    logger.info(
                        f"Component '{comp.component_id}' contributed context enrichment "
                        f"({len(text)} chars)"
                    )
                else:
                    logger.debug(f"Component '{comp.component_id}' returned empty enrichment")
            except Exception as e:
                logger.warning(
                    f"Component '{comp.component_id}' context enrichment failed: {e}",
                    exc_info=True,
                )

        logger.debug(
            f"Context enrichment: {enrichment_capable_count} capable component(s), "
            f"{len(parts)} contributed text"
        )

        return "\n\n".join(parts) if parts else "", details

    # ------------------------------------------------------------------
    # Public API: LangChain tool integration
    # ------------------------------------------------------------------

    def has_active_tool_components(
        self,
        profile_config: Dict[str, Any],
        user_uuid: Optional[str] = None,
    ) -> bool:
        """
        Check if profile has any active action components requiring tool calling.

        Used by profile classes to decide whether to auto-upgrade to an agent
        with tool-calling capability (e.g., llm_only → ConversationAgentExecutor).
        """
        active = self.get_active_components(profile_config, user_uuid)
        return any(
            c.tool_definition and c.component_type in _TOOL_BEARING_TYPES
            and self._component_offers_tool(c, profile_config)
            for c in active
        )

    def get_langchain_tools(
        self,
        profile_config: Dict[str, Any],
        comp_context: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        """
        Create LangChain Tool objects for all active action components.

        Each tool wraps the component handler's process() method so that
        LangChain agents (ConversationAgentExecutor) can call component tools
        the same way they call MCP tools.

        Returns:
            List of LangChain StructuredTool objects. Empty if no active
            action components or if LangChain is not available.
        """
        active = self.get_active_components(
            profile_config, (comp_context or {}).get("user_uuid")
        )
        action_components = [
            c for c in active
            if c.tool_definition and c.component_type in _TOOL_BEARING_TYPES and c.handler
            and self._component_offers_tool(c, profile_config)
        ]
        if not action_components:
            return []

        try:
            from langchain_core.tools import StructuredTool
        except ImportError:
            logger.warning("langchain_core not available — cannot create component LangChain tools")
            return []

        _context = comp_context or {}

        tools = []
        for comp in action_components:
            tool_def = self.tool_definition_for(comp, profile_config)
            handler = comp.handler

            # Build a Pydantic args_schema from the manifest tool_definition
            # so LangChain delivers arguments as proper keyword args (not
            # double-wrapped under a "kwargs" key).
            args_schema = _build_args_schema(tool_def)
            _json_string_fields = getattr(args_schema, "__json_string_fields__", set())

            # Build the async wrapper that calls the handler's process() method
            async def _run_component(
                _handler=handler,
                _comp_id=comp.component_id,
                _tool_name=tool_def["name"],
                _ctx=_context,
                _json_fields=_json_string_fields,
                _manifest=comp.manifest,
                _tool_def=tool_def,
                **kwargs,
            ) -> str:
                """Execute a component handler and return a JSON result."""
                import json as _json
                # Agent Authority (Tier 5 Step 4 / P3): fail-closed scope enforcement for
                # component tools against the ambient principal (by component id; components
                # are capabilities, not data-writes, so they aren't write-gated).
                try:
                    from trusted_data_agent.core import agent_authority as _aid
                    # Denyable by EITHER the component id ("scheduler") OR the TDA_ tool name
                    # ("TDA_Scheduler") so both execution paths enforce the same denied_tools entry.
                    _allowed, _why = _aid.enforce_tool(
                        _comp_id, is_write=False, aliases=frozenset({_tool_name}),
                        arguments=kwargs)
                    if not _allowed:
                        logger.warning(f"agent-authority: DENIED component '{_comp_id}' — {_why}")
                        # A refusal is a RESULT on this tool, so it takes the tool's declared
                        # (content, artifact) shape — a bare string here raised in LangChain's
                        # tool node and ended the whole turn with the error as the answer
                        # (trust campaign 2026-10-27; test_component_denial_shape.py).
                        return _json.dumps(_aid.denied_tool_result(_comp_id, _why)), None
                except Exception:
                    pass  # enforce_tool already fails closed; a bad import must not break tools

                # Component ENABLEMENT at the effect site — the ReAct twin of the guard in
                # mcp_adapter.adapter. Advertise-time filtering is the primary control; this
                # backstops a call that arrives unadvertised (leaked-call recovery, a pack- or
                # champion-authored plan, a hallucination). Fails OPEN on internal error.
                try:
                    from trusted_data_agent.components import enablement as _enablement
                    _uid = _ctx.get("user_uuid")
                    _pid = _ctx.get("profile_id")
                    _pcfg = {}
                    if _pid and _uid:
                        try:
                            from trusted_data_agent.core.config_manager import get_config_manager
                            _pcfg = get_config_manager().get_profile(_pid, _uid) or {}
                        except Exception:
                            _pcfg = {}
                    _deny = _enablement.enforce_component_call(
                        get_component_manager().get_component(_comp_id), _pcfg, _uid)
                    if _deny:
                        logger.warning(
                            f"component-enablement: DENIED '{_comp_id}' — not enabled for profile")
                        return _json.dumps({"status": "error", "error": _deny,
                                            "denied_reason": "component_not_enabled"}), None
                except Exception:
                    pass  # fail open — advertise-time filtering remains the control

                # PROP-011: nothing runs past a question for the user — a component call after
                # the question was raised is refused with the same recovery-shaped result the
                # SQL gate gives (the ReAct twin of the check in mcp_adapter.adapter).
                try:
                    from trusted_data_agent.agent import input_requests as _ir_mod
                    _ir_block = _ir_mod.refusal_for(_tool_name)
                    if _ir_block is not None:
                        return _json.dumps(_ir_block), None
                except Exception:
                    pass

                # Decode JSON-string-encoded object args (data/mapping/...) back
                # into native dict/list before the handler sees them. These are
                # advertised as strings in the schema for universal provider
                # compatibility (see _build_args_schema).
                kwargs = _decode_json_string_args(kwargs, _json_fields)
                # Strip None values — Pydantic fills None for optional fields not
                # provided by the LLM, but handlers expect absent keys (matching
                # the adapter path where optional args are simply omitted).
                kwargs = {k: v for k, v in kwargs.items() if v is not None}
                # Component signals (components/signals.py): `source_ref` → the ledger entry
                # in context['source'] and an empty `content` / `data` filled from it; the
                # dashboard's declared implicit source is the latest data result (what its
                # private session cache used to be). One rule for both tool lanes.
                from trusted_data_agent.components import signals as _signals
                kwargs = _signals.before_process(kwargs, _ctx, _manifest, _tool_def)
                # Late-bind user_input for TDA_Dashboard: read from cache at
                # invocation time (cache is populated just before agent.execute()).
                # This gives _handle_visualize the clean original user question
                # so source_prompt on tiles is never empty or polluted with results.
                if _comp_id == "dashboard" and not _ctx.get("user_input"):
                    _sid = _ctx.get("session_id")
                    if _sid:
                        try:
                            from trusted_data_agent.mcp_adapter.adapter import _user_input_cache
                            _ui = _user_input_cache.get(_sid, "")
                            if _ui:
                                _ctx = dict(_ctx)  # copy — never mutate the shared factory context
                                _ctx["user_input"] = _ui
                        except Exception:
                            pass
                try:
                    payload = await _handler.process(kwargs, _ctx)
                    _signals.after_process(payload, _ctx, _tool_name, kwargs)
                    # The result the MODEL reads is the structured half — spec + metadata,
                    # exactly what the Optimize lane's adapter hands the planner
                    # (adapter.py returns {type, spec, metadata}; it never sent html).
                    # The HTML is PRESENTATION for the reader and rides the ToolMessage
                    # ARTIFACT, which LangChain never sends to the model. Before this the
                    # markup went into the tool result verbatim on every LangChain lane
                    # (Ideate+tools, Focus with component tools, the Genie coordinator):
                    # a KG lookup whose plain-text facts cost ~2K tokens carried a card
                    # that cost 2.4K more — 27K once the card became a card. One
                    # chokepoint, every component; the reader's copy is re-attached by
                    # components.utils.extract_component_payload from the artifact.
                    content = _json.dumps({
                        "status": "success",
                        "component_id": _comp_id,
                        "title": payload.title,
                        "metadata": payload.metadata,
                        "render_target": payload.render_target.value,
                        "spec": payload.spec,
                        "window_id": payload.window_id,
                        "window_action": payload.window_action,
                        "interactive": payload.interactive,
                    })
                    return content, {"html": payload.html}
                except Exception as e:
                    return _json.dumps({
                        "status": "error",
                        "component_id": _comp_id,
                        "error": str(e),
                    }), None

            tool = StructuredTool.from_function(
                coroutine=_run_component,
                name=tool_def["name"],
                description=tool_def.get("description", ""),
                args_schema=args_schema,
                # (content, artifact): the content is the model's, the artifact the reader's
                response_format="content_and_artifact",
            )
            tools.append(tool)

        logger.info(
            f"Created {len(tools)} LangChain component tool(s): "
            f"{', '.join(t.name for t in tools)}"
        )
        return tools

    # ------------------------------------------------------------------
    # Public API: Frontend manifest
    # ------------------------------------------------------------------

    def get_frontend_manifest(self) -> List[Dict[str, Any]]:
        """
        Get minimal manifest data for the frontend ComponentRendererRegistry.

        Returns list of dicts with: component_id, renderer_file, cdn_dependencies, etc.
        """
        return [comp.to_frontend_manifest() for comp in self.components.values()]

    # ------------------------------------------------------------------
    # Hot-reload
    # ------------------------------------------------------------------

    def reload(self) -> int:
        """
        Hot-reload all components from disk.

        Returns:
            Number of components loaded after reload.
        """
        logger.info("ComponentManager: hot-reloading components...")

        # Clear cached instruction text
        for comp in self.components.values():
            comp._instructions_cache.clear()

        # Re-discover
        self._load_registry()
        self._discover_and_load()

        return len(self.components)
