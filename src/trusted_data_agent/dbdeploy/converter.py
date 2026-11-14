"""
converter — pure entry point: parsed ``model.json`` → :class:`ConversionResult`.

Resolves a dialect emitter, extracts the dialect-neutral
:class:`PhysicalModel`, and emits DDL + load plan + KG physical delta.  No I/O.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .design_standard import TeradataStandard
from .dialects.base import DialectEmitter
from .dialects.teradata import TeradataDialect
from .physical_model import extract_physical_model
from .types import ConversionResult


class UnknownDialect(KeyError):
    """Raised when no emitter is registered for a dialect name."""


def resolve_dialect(name: str, *, standard: Optional[TeradataStandard] = None) -> DialectEmitter:
    key = (name or "").strip().lower()
    if key == "teradata":
        return TeradataDialect(standard=standard)
    raise UnknownDialect(f"No dbdeploy dialect emitter for '{name}'. Known: ['teradata']")


def convert(
    model_doc: Dict[str, Any],
    *,
    industry: str,
    flavour: str,
    dialect: str = "teradata",
    divisions: Any = None,
    logical_db_name: Optional[str] = None,
    database_parent: str = "dbc",
    database_perm_bytes: Optional[int] = None,
    root_database: Optional[str] = None,
    include_metric_views: bool = False,
    standard: Optional[TeradataStandard] = None,
    flat_namespace: bool = True,
    comments: str = "none",
    pi_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    column_refinements: Optional[Dict[str, Dict[str, Any]]] = None,
) -> ConversionResult:
    """Convert a parsed ``model.json`` to a deployable artifact set.

    Args:
        model_doc: parsed ``model.json``.
        industry, flavour: provenance + default catalog/db naming.
        dialect: target backend dialect (``teradata`` for now).
        divisions: optional division filter (matches the walker's semantics).
        logical_db_name: KG logical ``database`` root name to ``implements`` against
            (defaults to ``<industry>_<flavour>``, matching the walker).
        database_parent / database_perm_bytes: Teradata ``CREATE DATABASE`` params.
        standard: override the Teradata design-standard ruleset.

    Returns:
        ConversionResult with DDL plan, load plan, KG physical delta, warnings.
    """
    emitter = resolve_dialect(dialect, standard=standard)
    model = extract_physical_model(
        model_doc, industry=industry, flavour=flavour, divisions=divisions,
    )
    from trusted_data_agent.agent.kg_constructors.model_naming import model_db_name
    db_name = (logical_db_name or model_db_name(industry, flavour)).strip()

    # Canonical model: a SINGLE flat physical database — division/domain are
    # logical-only KG entities, never physical containers (see ONTOLOGY_ARCHITECTURE
    # ARCHITECTURE Part II).  The resolved root is the one database every table
    # lands in; the emitter uses the same value as its catalog.  It is OUR name
    # (the caller's, else the model's Uderia name) — Databricks' deployment_catalog
    # names their Unity Catalog, not our Teradata database.
    resolved_root = (root_database or db_name).strip()
    if flat_namespace:
        from .physical_model import flatten_to_single_database
        flatten_to_single_database(model, resolved_root)

    result = emitter.convert(
        model,
        logical_db_name=db_name,
        database_parent=database_parent,
        database_perm_bytes=database_perm_bytes,
        root_database=resolved_root,
        include_metric_views=include_metric_views,
        comments=(comments if comments in ("all", "table", "none") else "none"),
        pi_overrides=pi_overrides,
        column_refinements=column_refinements,
    )
    result.physical_model = model  # expose for downstream data generation
    return result
