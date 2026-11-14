"""
KG Store package.

Public API — import from here rather than sub-modules.
"""

from .base import KGStoreBackend
from .capabilities import KGStoreCapability, REQUIRED_CAPABILITIES
from .factory import (
    get_backend,
    get_default_sqlite_backend,
    get_backend_for_profile,
    register_backend,
    reset_instance_cache,
)
from .settings import (
    get_kgstore_settings,
    save_kgstore_settings,
    is_kg_enabled_for_tier,
    is_backend_allowed,
    get_allowed_backends,
    ALL_KG_BACKENDS,
)
from .types import KGBackendConfig, KGEntity, KGRelationship

# Ensure backends are registered on package import
from . import sqlite_backend  # noqa: F401
try:  # enterprise package
    from . import neo4j_backend   # noqa: F401
except ImportError:
    pass

__all__ = [
    "KGStoreBackend",
    "KGStoreCapability",
    "REQUIRED_CAPABILITIES",
    "get_backend",
    "get_default_sqlite_backend",
    "get_backend_for_profile",
    "register_backend",
    "reset_instance_cache",
    "get_kgstore_settings",
    "save_kgstore_settings",
    "is_kg_enabled_for_tier",
    "is_backend_allowed",
    "get_allowed_backends",
    "ALL_KG_BACKENDS",
    "KGBackendConfig",
    "KGEntity",
    "KGRelationship",
]
