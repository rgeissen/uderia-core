"""
KG Store backend factory.

Mirrors vectorstore/factory.py — maintains a singleton cache of backend
instances keyed by a config fingerprint.  All backends are synchronous so
no asyncio locking is required.

Usage:
    from trusted_data_agent.kgstore.factory import (
        get_backend,
        get_default_sqlite_backend,
        get_backend_for_profile,
        register_backend,
    )
"""

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Type

from .base import KGStoreBackend

logger = logging.getLogger("quart.app")

# ── Registry & instance cache ─────────────────────────────────────────────────

_REGISTRY: Dict[str, Type[KGStoreBackend]] = {}
_INSTANCES: Dict[str, KGStoreBackend] = {}


def register_backend(backend_type: str, cls: Type[KGStoreBackend]) -> None:
    """Register a backend class under a type name (e.g. 'sqlite', 'neo4j')."""
    _REGISTRY[backend_type] = cls
    logger.debug(f"KG backend registered: {backend_type} → {cls.__name__}")


def _fingerprint(backend_type: str, config: Dict[str, Any]) -> str:
    payload = {"type": backend_type, **config}
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def get_backend(backend_type: str, config: Optional[Dict[str, Any]] = None) -> KGStoreBackend:
    """
    Return (or create) a backend instance for the given type + config.
    Instances are cached by config fingerprint.
    """
    config = config or {}
    key = _fingerprint(backend_type, config)

    if key in _INSTANCES:
        return _INSTANCES[key]

    if backend_type not in _REGISTRY:
        # Lazy-load built-in backends
        _load_builtin_backend(backend_type)

    if backend_type not in _REGISTRY:
        raise ValueError(
            f"Unknown KG backend type: '{backend_type}'. "
            f"Available: {list(_REGISTRY.keys())}"
        )

    cls = _REGISTRY[backend_type]
    instance = cls(config=config)
    instance.initialize()
    instance.assert_required_capabilities()
    _INSTANCES[key] = instance
    logger.info(f"KG backend instance created: {backend_type} (key={key})")
    return instance


def get_default_sqlite_backend() -> KGStoreBackend:
    """Return the default SQLite backend with standard config."""
    return get_backend("sqlite", {})


def get_backend_for_profile(
    profile_id: str,
    user_uuid: str,
    config_id: Optional[str] = None,
) -> KGStoreBackend:
    """
    Resolve the KG backend for a profile.

    Resolution order:
    0. Explicit config_id override (e.g. from KG constructor request)
    1. Profile's kgStoreConfigId (from user_preferences)
    2. User's default KG backend configuration
    3. Global default: SQLite

    Returns a ready backend instance.
    """
    backend_type, cfg = resolve_backend_choice(profile_id, user_uuid, config_id)
    if backend_type is None:
        # Ultimate fallback: SQLite
        return get_default_sqlite_backend()
    return get_backend(backend_type, cfg)


def resolve_backend_choice(
    profile_id: str,
    user_uuid: str,
    config_id: Optional[str] = None,
) -> tuple:
    """WHICH backend a profile resolves to, WITHOUT instantiating it.

    The selection half of ``get_backend_for_profile`` — the same chain, extracted so
    that "which backend is this?" can be answered without CONNECTING to it. Building a
    remote backend is what dials the network, so a caller that only needs the type (a
    UI badge, an offline gate choosing a local target) had no way to ask without paying
    for a connection to a host that may not even resolve.

    Returns ``(backend_type, config)``, or ``(None, {})`` meaning "nothing named —
    the caller's default applies". One chain, so a reader can never disagree with the
    factory about which backend a profile actually uses.
    """
    try:
        from trusted_data_agent.core.config_manager import ConfigManager
        cm = ConfigManager()

        # Priority 0: explicit config_id override. An id that names nothing falls
        # THROUGH to the profile chain rather than failing — preserved verbatim.
        if config_id:
            configs = cm.get_kg_backend_configurations(user_uuid)
            cfg = next((c for c in configs if c["id"] == config_id), None)
            if cfg:
                return cfg["backend_type"], cfg.get("config", {})

        # Priority 1: the profile's own kgStoreConfigId
        profile = cm.get_profile(profile_id, user_uuid)
        kg_config_id = profile.get("kgStoreConfigId") if profile else None

        if kg_config_id:
            configs = cm.get_kg_backend_configurations(user_uuid)
            cfg = next((c for c in configs if c["id"] == kg_config_id), None)
            if cfg:
                return cfg["backend_type"], cfg.get("config", {})

        # Priority 2: the user's default KG backend configuration
        configs = cm.get_kg_backend_configurations(user_uuid)
        default_cfg = next((c for c in configs if c.get("is_default")), None)
        if default_cfg:
            return default_cfg["backend_type"], default_cfg.get("config", {})

    except Exception as exc:
        logger.warning(f"KG backend resolution failed for profile {profile_id}: {exc}")

    return None, {}


def _load_builtin_backend(backend_type: str) -> None:
    """Lazy-import a built-in backend to register it."""
    try:
        if backend_type == "sqlite":
            from . import sqlite_backend  # noqa: F401 — registers on import
        elif backend_type == "neo4j":
            from . import neo4j_backend  # noqa: F401 — registers on import
    except ImportError as exc:
        logger.warning(f"Could not import KG backend '{backend_type}': {exc}")


def reset_instance_cache() -> None:
    """Flush all cached backend instances (used in tests and maintenance)."""
    global _INSTANCES
    for instance in _INSTANCES.values():
        try:
            instance.shutdown()
        except Exception:
            pass
    _INSTANCES = {}
