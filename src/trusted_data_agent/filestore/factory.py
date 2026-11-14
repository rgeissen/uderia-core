"""
File-store backend factory and the store resolution chain (PROP-010 §4.1).

Resolution, first match wins:
  0. an explicit store id — admin tooling and migration only, never a user's choice
  1. the producing agent's ``fileStoreConfigId``
  2. the admin's default store
  3. platform local — always succeeds

A store the user's tier may not use is skipped (the next step applies), so revoking a
store in the Policy tab takes effect on the next write without editing any agent.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, Optional, Tuple, Type

from . import db
from .base import FileStoreBackend, FileStoreError
from .catalog import BACKENDS
from .settings import PLATFORM_LOCAL_ID, is_store_allowed

logger = logging.getLogger("quart.app")

_REGISTRY: Dict[str, Type[FileStoreBackend]] = {}
_INSTANCES: Dict[str, FileStoreBackend] = {}

# the ONE backend declaration lives in catalog.py; this name stays for its readers
BACKEND_TYPES = {k: {"label": v["label"], "hint": v["hint"]} for k, v in BACKENDS.items()}


def register_backend(backend_type: str, cls: Type[FileStoreBackend]) -> None:
    _REGISTRY[backend_type] = cls


def _load_builtin(backend_type: str) -> None:
    import importlib
    b = BACKENDS.get(backend_type)
    if b:
        try:
            register_backend(backend_type, getattr(importlib.import_module(b["module"]), b["class"]))
        except ImportError as e:
            raise FileStoreError(f"the {b.get('label', backend_type)} file store is part of the enterprise package") from e


def build_backend(backend_type: str, config: Optional[Dict[str, Any]] = None, cache: bool = True) -> FileStoreBackend:
    config = dict(config or {})
    key = hashlib.sha256(json.dumps({"t": backend_type, **config}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    if cache and key in _INSTANCES:
        return _INSTANCES[key]
    if backend_type not in _REGISTRY:
        _load_builtin(backend_type)
    if backend_type not in _REGISTRY:
        raise ValueError(f"unknown file-store backend '{backend_type}'")
    inst = _REGISTRY[backend_type](config)
    inst.initialize()
    inst.assert_required_capabilities()
    if cache:
        _INSTANCES[key] = inst
    return inst


def backend_for_store(store_id: str) -> Tuple[Dict[str, Any], FileStoreBackend]:
    cfg = db.get_store_config(store_id, with_secrets=True)
    if cfg is None:
        raise LookupError(f"file store '{store_id}' does not exist")
    return cfg, build_backend(cfg["backend_type"], cfg.get("config"))


def reset_cache() -> None:
    _INSTANCES.clear()


def _tier_of(user_uuid: Optional[str]) -> str:
    if not user_uuid:
        return "user"
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import User
        with get_db_session() as s:
            u = s.query(User).filter_by(id=user_uuid).first()
            return (getattr(u, "profile_tier", None) or "user") if u else "user"
    except Exception:  # noqa: BLE001
        return "user"


def user_tier(user_uuid: Optional[str]) -> str:
    return _tier_of(user_uuid)


def resolve_store_id(profile_id: Optional[str], user_uuid: Optional[str], explicit: Optional[str] = None,
                     profile: Optional[Dict[str, Any]] = None) -> str:
    tier = _tier_of(user_uuid)

    def usable(sid: Optional[str]) -> bool:
        return bool(sid) and db.get_store_config(sid, with_secrets=False) is not None and is_store_allowed(sid, tier)

    if explicit and db.get_store_config(explicit, with_secrets=False) is not None:
        return explicit
    bound = None
    if profile is None and profile_id and user_uuid:
        try:
            from trusted_data_agent.core.config_manager import get_config_manager
            profile = get_config_manager().get_profile(profile_id, user_uuid)
        except Exception:  # noqa: BLE001
            profile = None
    if profile:
        bound = profile.get("fileStoreConfigId")
    if usable(bound):
        return bound
    if bound:
        logger.info(f"[files] agent {profile_id} is bound to store {bound}, which is unavailable for tier {tier}; using the default")
    default = db.default_store_id()
    if usable(default):
        return default
    return PLATFORM_LOCAL_ID
