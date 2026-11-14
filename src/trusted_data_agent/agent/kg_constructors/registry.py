"""
KG constructor strategy registry — resolve a strategy by ``discovery_mode``.

Mirrors the platform's EngineRegistry / connector strategy registry: a register
decorator populates a module-level map of singleton strategy instances; callers
resolve by mode.  Importing :mod:`trusted_data_agent.agent.kg_constructors`
triggers registration of the built-in strategies.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Type

from .base import KGConstructorStrategy

logger = logging.getLogger("quart.app")

_REGISTRY: Dict[str, KGConstructorStrategy] = {}


class UnknownConstructorMode(KeyError):
    """Raised when no strategy is registered for a discovery_mode."""


def register(cls: Type[KGConstructorStrategy]) -> Type[KGConstructorStrategy]:
    """Class decorator: instantiate ``cls`` and bind it to each of its
    ``discovery_modes``.  Strategies are stateless singletons."""
    instance = cls()
    modes = instance.discovery_modes
    if not modes:
        raise ValueError(f"{cls.__name__} declares no discovery_modes")
    for mode in modes:
        if mode in _REGISTRY:
            logger.warning("KG constructor mode '%s' re-registered by %s", mode, cls.__name__)
        _REGISTRY[mode] = instance
    return cls


def resolve(mode: str) -> KGConstructorStrategy:
    """Return the strategy for ``mode``.

    Raises:
        UnknownConstructorMode: if no strategy is registered for ``mode``.
    """
    key = (mode or "").strip().lower()
    if key not in _REGISTRY:
        raise UnknownConstructorMode(
            f"No KG constructor strategy for discovery_mode '{mode}'. "
            f"Known: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[key]


def has_mode(mode: str) -> bool:
    return (mode or "").strip().lower() in _REGISTRY


def list_modes() -> List[str]:
    return sorted(_REGISTRY)


def capabilities() -> Dict[str, Dict[str, object]]:
    """Map mode → {label, supports_resync} for UI/capability queries."""
    out: Dict[str, Dict[str, object]] = {}
    for mode, strat in _REGISTRY.items():
        out[mode] = {"label": strat.label, "supports_resync": strat.supports_resync}
    return out
