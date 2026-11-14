"""
KG constructor strategy contract.

A strategy turns a *source* into KG entities/relationships and writes them into a
GraphStore.  It is resolved by ``discovery_mode`` (a template input variable) via
the registry.  Strategies are stateless singletons.

Separation of concerns (mirrors the platform's EngineRegistry / connector
ConnectorInvocationStrategy patterns):

    * The strategy owns *how to acquire and shape* a graph (fetch, parse, walk).
    * The endpoint owns governance, session creation, and response shaping.
    * The instantiated KG owns lifecycle (versioning, update-from-source) — the
      strategy only contributes a re-syncable ``rebuild()`` primitive +
      ``supports_resync`` capability flag.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


class SourceError(RuntimeError):
    """A source artifact could not be fetched or parsed. Raised by the constructor sources; the
    knowledge-graph routes answer it with 502, so it lives here, where every distribution has it."""


async def maybe_await(value: Any) -> Any:
    """Await ``value`` if it is awaitable, else return it.

    GraphStore mixes sync (``import_bulk``, ``set_kg_metadata``) and async methods
    across backends; this lets a strategy call either uniformly.
    """
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass
class ConstructorContext:
    """Everything a strategy needs to build a graph.

    Dependencies (e.g. the fetch function) are injected via ``deps`` so strategies
    are unit-testable with fakes — no network or DB required in tests.
    """

    store: Any                                  # GraphStore-like: import_bulk / set_kg_metadata / ...
    options: Dict[str, Any] = field(default_factory=dict)
    user_uuid: Optional[str] = None
    profile_id: Optional[str] = None
    emit: Optional[Callable[[Dict[str, Any]], Any]] = None   # progress callback
    deps: Dict[str, Any] = field(default_factory=dict)        # injectable dependencies

    async def emit_event(self, event: Dict[str, Any]) -> None:
        if self.emit is None:
            return
        await maybe_await(self.emit(event))


@dataclass
class ConstructorResult:
    """What a strategy returns after building."""

    kg_id: Optional[str] = None
    entities_added: int = 0
    relationships_added: int = 0
    stats: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kg_id": self.kg_id,
            "entities_added": self.entities_added,
            "relationships_added": self.relationships_added,
            "stats": self.stats,
            **self.extra,
        }


class KGConstructorStrategy(ABC):
    """Base class for all KG construction strategies."""

    #: One or more ``discovery_mode`` values this strategy handles.
    discovery_modes: Tuple[str, ...] = ()
    #: Whether the instantiated KG can be re-synced from its source descriptor.
    supports_resync: bool = False
    #: Human-facing label for the strategy (UI/capabilities).
    label: str = ""

    @abstractmethod
    async def construct(self, ctx: ConstructorContext) -> ConstructorResult:
        """Build the graph into ``ctx.store`` and return a result."""
        raise NotImplementedError

    async def rebuild(self, descriptor: Dict[str, Any]) -> Tuple[List[Dict], List[Dict]]:
        """Re-acquire + re-shape the source into (entities, relationships).

        Used by the KG-instance update lifecycle to diff against the live graph.
        Only meaningful when ``supports_resync`` is True.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support resync (supports_resync=False)"
        )
