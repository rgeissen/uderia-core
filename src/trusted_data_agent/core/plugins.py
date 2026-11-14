"""Plug-ins — how a separately installed package joins the framework at startup.

A package that wants to add to Uderia (the enterprise package, a third party's backends or
components) declares an entry point in the group ``uderia.plugins``::

    [project.entry-points."uderia.plugins"]
    enterprise = "uderia_enterprise:register"

``load_plugins()`` runs before the routes are registered. Each entry point is loaded and, if
it resolves to a callable, called with no arguments; what it registers (vector, graph, session
and file-store backends, deploy backends, catalog providers, connectors, KG constructors, key
managers, blueprints via ``register_blueprints(app)``) goes through the registries that already
exist. The core never names a plug-in; a failing plug-in is logged and skipped, never fatal.
"""
from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any, Callable, List, Optional

GROUP = "uderia.plugins"

_loaded: List[str] = []
_blueprint_hooks: List[Callable[[Any], None]] = []


def loaded() -> List[str]:
    """The names of the plug-ins loaded in this process."""
    return list(_loaded)


def register_blueprints_hook(fn: Callable[[Any], None]) -> None:
    """A plug-in that serves routes registers a hook; ``apply_blueprints(app)`` calls it."""
    _blueprint_hooks.append(fn)


def load_plugins(logger: Optional[logging.Logger] = None) -> List[str]:
    log = logger or logging.getLogger("quart.app")
    try:
        eps = list(entry_points(group=GROUP))
    except Exception as exc:  # noqa: BLE001 — a broken metadata index must not stop the server
        log.warning("plug-ins: entry points unreadable (%s)", exc)
        return []
    for ep in eps:
        try:
            target = ep.load()
            if callable(target):
                target()
            _loaded.append(ep.name)
            log.info("plug-in loaded: %s (%s)", ep.name, ep.value)
        except Exception as exc:  # noqa: BLE001
            log.error("plug-in %s failed to load: %s", ep.name, exc, exc_info=True)
    return list(_loaded)


def apply_blueprints(app: Any) -> None:
    for fn in _blueprint_hooks:
        try:
            fn(app)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("quart.app").error("plug-in blueprint hook failed: %s", exc, exc_info=True)
