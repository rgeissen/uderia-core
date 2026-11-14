"""
KG constructors — pluggable strategies for building a Knowledge Graph.

A *constructor* is a one-shot, build-time act that produces a KG instance.  It
deliberately owns no run-time lifecycle: versioning, update-from-source, and
version switching belong to the instantiated KG (see ``core/kg_version_db.py``
and the ``check-update`` / ``apply-update`` endpoints).

Strategies are resolved by ``discovery_mode`` (a template input variable):

    "tool" / "prompt"   -> AgentDiscoveryStrategy   (live DB discovery, LLM-assisted)
    "industry_model"    -> DeclarativeModelStrategy (declarative model.json ingestion)
    "andp_semantic"     -> ANDPSemanticStrategy     (deployed AI-Native Data Product's
                                                     own semantic module)
    "declared_model"    -> DeclaredModelStrategy     (a deployed ISO/IEC 11179 registry)

The declarative path is deterministic (no LLM, no MCP): it fetches a
declarative logical model (``model.json``) and walks it directly into
entities/relationships via :func:`industry_model_walker.walk_industry_model`.

This package is introduced in milestones:
    M1 — industry_model_walker (pure) + industry_model_source (fetch)
    M2 — base / registry / agent_discovery / declarative_model
"""

try:  # the Industry Data Model constructor — development repository only (manifest group licence-review)
    from .industry_model_walker import WalkOptions, walk_industry_model  # noqa: F401
except ImportError:
    WalkOptions = walk_industry_model = None  # type: ignore[assignment]
from .base import (  # noqa: F401
    ConstructorContext,
    ConstructorResult,
    KGConstructorStrategy,
)
from . import registry  # noqa: F401

# Importing these modules triggers @register side-effects (registry population).
try:  # development repository only (licence-review): absent, the mode is simply not registered
    from . import declarative_model  # noqa: F401,E402
except ImportError:
    pass
from . import agent_discovery  # noqa: F401,E402
try:  # the ANDP constructor — enterprise package
    from . import andp_semantic  # noqa: F401,E402
    from .andp_semantic_walker import SemanticSnapshot, walk_andp_semantic  # noqa: F401,E402
except ImportError:
    SemanticSnapshot = walk_andp_semantic = None  # type: ignore[assignment]
from . import declared_model  # noqa: F401,E402

__all__ = [
    "WalkOptions",
    "walk_industry_model",
    "SemanticSnapshot",
    "walk_andp_semantic",
    "ConstructorContext",
    "ConstructorResult",
    "KGConstructorStrategy",
    "registry",
]
