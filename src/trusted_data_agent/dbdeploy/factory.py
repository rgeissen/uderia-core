"""
Deployment backend factory — singleton cache + capability validation.

Mirrors vectorstore/factory.py: backends register at import time; instances are
cached by a connection-identity fingerprint (teradataml maintains a *global*
context, so the same identity must reuse one instance).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Dict, Optional, Type

from .backend import DeploymentBackend
from .capabilities import REQUIRED_CAPABILITIES

logger = logging.getLogger("dbdeploy.factory")

_REGISTRY: Dict[str, Type[DeploymentBackend]] = {}
_INSTANCES: Dict[str, DeploymentBackend] = {}
_INIT_LOCKS: Dict[str, asyncio.Lock] = {}


def register_backend(backend_type: str, cls: Type[DeploymentBackend]) -> None:
    _REGISTRY[backend_type] = cls
    logger.info("Registered deployment backend: %s", backend_type)


def list_backends() -> list[str]:
    return sorted(_REGISTRY)


def _fingerprint(backend_type: str, config: Dict[str, Any]) -> str:
    # Connection identity only (teradataml has a single global context).
    identity = {
        "t": backend_type,
        "host": config.get("host", ""),
        "port": str(config.get("port", "") or ""),
        "database": config.get("database", ""),
        "username": config.get("username", ""),
    }
    return hashlib.md5(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]


def make_backend(backend_type: str, config: Optional[Dict[str, Any]] = None) -> DeploymentBackend:
    """Construct (without caching/initializing) a backend + validate capabilities.

    Synchronous — safe to call from tests.  ``get_backend`` wraps this with the
    singleton cache + init lock.
    """
    if backend_type not in _REGISTRY:
        raise ValueError(
            f"Unknown deployment backend: '{backend_type}'. Available: {list_backends()}"
        )
    instance = _REGISTRY[backend_type](connection_config=config or {})  # type: ignore[call-arg]
    missing = REQUIRED_CAPABILITIES - instance.capabilities()
    if missing:
        names = ", ".join(c.name for c in missing)
        raise RuntimeError(f"Backend '{backend_type}' missing required capabilities: {names}")
    return instance


async def get_backend(backend_type: str, config: Optional[Dict[str, Any]] = None) -> DeploymentBackend:
    config = config or {}
    key = _fingerprint(backend_type, config)
    if key in _INSTANCES:
        return _INSTANCES[key]
    _INIT_LOCKS.setdefault(key, asyncio.Lock())
    async with _INIT_LOCKS[key]:
        if key in _INSTANCES:
            return _INSTANCES[key]
        instance = make_backend(backend_type, config)
        _INSTANCES[key] = instance
        return instance


def reset_instances() -> None:
    """Clear cached instances. Tests only."""
    _INSTANCES.clear()
    _INIT_LOCKS.clear()
