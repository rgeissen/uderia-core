"""
DeclaredModelStrategy — deterministic KG construction from an **ISO/IEC 11179 metadata
registry** deployed on Teradata (discovery_mode="declared_model").

No LLM, no MCP, no guessing: connect to the model, read the standard registry it
publishes about itself (five tables — see the pack's core-model/10-catalog.sql), read
``DBC.ColumnsV`` for the type rule, walk it into the platform's two-layer KG topology,
import it.  Implements ``rebuild()`` so the KG re-syncs when the registry changes.

The interchange contract is a recognised standard (ISO/IEC 11179) rather than a bespoke
format; the KG this produces exports to RDFS/OWL/SKOS/DCAT through the platform's
existing ``semantic_interchange`` layer — so the model is standard at both ends.

Options (request body / stored descriptor):
    connection_id     : str   Uderia database connection to read through (required
                              unless a connection_factory is injected via ctx.deps)
    catalog_database  : str   where the 11179 registry lives (default Policing_Core_Semantic)
    model_name        : str   logical root display name (default "Policing Core")
    core_database     : str   Teradata database holding the deployed objects (default derived)
    include_columns   : bool  emit attribute/column nodes (default True)
    include_physical  : bool  emit the physical layer (default True)
    max_value_codes   : int   cap on decoded values per attribute (default 60)
    validate_with_source : bool  probe the source for row counts / key uniqueness /
                              sampled shapes and stamp them on the nodes (default False)
    kg_name / description : str  display metadata (optional)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from . import declared_model_source as source
from .base import (
    ConstructorContext,
    ConstructorResult,
    KGConstructorStrategy,
    maybe_await,
)
from .declared_model_walker import (
    DeclaredModelSnapshot,
    WalkOptions,
    WalkStats,
    walk_declared_model,
)
from .registry import register

logger = logging.getLogger("quart.app")

DISCOVERY_MODE = "declared_model"
DEFAULT_CATALOG_DATABASE = "Policing_Core_Semantic"
DEFAULT_MODEL_NAME = "Policing Core"

#: The descriptor keys the update dialog may override on a resync (arriving under
#: ``descriptor["options"]``; the stored descriptor itself is flat).
_RESYNC_OVERRIDABLE = ("include_columns", "include_physical", "validate_with_source")


def _walk_options_from(opts: Dict[str, Any]) -> WalkOptions:
    return WalkOptions(
        include_columns=bool(opts.get("include_columns", True)),
        include_physical=bool(opts.get("include_physical", True)),
        max_value_codes=int(opts.get("max_value_codes") or 60),
    )


def _descriptor_from(opts: Dict[str, Any]) -> Dict[str, Any]:
    """The re-syncable source descriptor stamped onto the KG."""
    return {
        "strategy": DISCOVERY_MODE,
        "discovery_mode": DISCOVERY_MODE,
        "connection_id": opts.get("connection_id"),
        "catalog_database": opts.get("catalog_database") or DEFAULT_CATALOG_DATABASE,
        "model_name": opts.get("model_name") or DEFAULT_MODEL_NAME,
        "core_database": opts.get("core_database"),
        "include_columns": bool(opts.get("include_columns", True)),
        "include_physical": bool(opts.get("include_physical", True)),
        "max_value_codes": int(opts.get("max_value_codes") or 60),
        "validate_with_source": bool(opts.get("validate_with_source", False)),
        "user_uuid": opts.get("user_uuid"),
    }


def _core_database(opts: Dict[str, Any], snapshot: DeclaredModelSnapshot) -> str:
    """The Teradata database the agent queries — the KG's anchor / trusted scope."""
    core = (opts.get("core_database") or "").strip()
    if core:
        return core
    for key in snapshot.physical_columns:
        return key[0]  # first deployed object's database, real-cased below
    cat = (snapshot.catalog_database or "").strip()
    return cat.rsplit("_", 1)[0] if cat else DEFAULT_MODEL_NAME


async def _stamp_validation(store, entities, validation_stats) -> None:
    """Stamp when the graph was validated against its source, from the graph's own
    content, so it can never claim a validation the nodes do not carry. Never raises."""
    try:
        from . import source_validation as _sv
        if not hasattr(store, "set_source_validation"):
            return
        if validation_stats and _sv.has_source_validation(entities):
            await maybe_await(store.set_source_validation({
                **_sv.summarize(entities),
                "probe_errors": int((validation_stats or {}).get("probe_errors", 0)),
                "seconds": round(sum((validation_stats.get("seconds") or {}).values()), 1),
            }))
        elif validation_stats is not None and not _sv.has_source_validation(entities):
            await maybe_await(store.set_source_validation(None))
    except Exception as exc:
        logger.warning("declared_model: could not stamp source validation: %s", exc)


@register
class DeclaredModelStrategy(KGConstructorStrategy):
    """Build a KG from a deployed ISO/IEC 11179 registry."""

    discovery_modes: Tuple[str, ...] = (DISCOVERY_MODE,)
    supports_resync: bool = True
    label: str = "Declared model (ISO/IEC 11179 registry)"

    # ── connection acquisition (injectable for tests) ────────────────────────
    def _open(self, ctx_or_opts: Any, opts: Dict[str, Any]):
        deps = getattr(ctx_or_opts, "deps", None) or {}
        factory = deps.get("connection_factory")
        if factory is not None:
            return factory(opts)
        from .connection import open_uderia_connection  # shared Teradata opener
        return open_uderia_connection(opts)

    async def construct(self, ctx: ConstructorContext) -> ConstructorResult:
        opts = dict(ctx.options or {})
        opts.setdefault("catalog_database", DEFAULT_CATALOG_DATABASE)
        opts.setdefault("model_name", DEFAULT_MODEL_NAME)
        opts.setdefault("user_uuid", ctx.user_uuid)

        await ctx.emit_event({
            "type": "kg_construct_fetch",
            "catalog_database": opts.get("catalog_database"),
            "model_name": opts.get("model_name"),
        })

        snapshot = await self._fetch(ctx, opts)
        stats = WalkStats()
        entities, relationships = walk_declared_model(
            snapshot, _walk_options_from(opts), stats)

        validation_stats = await self._validate(ctx, opts, entities, relationships)
        if validation_stats:
            await ctx.emit_event({"type": "kg_construct_validated", "stats": validation_stats})

        all_stats = stats.as_dict()
        if validation_stats:
            all_stats["source_validation"] = validation_stats

        await ctx.emit_event({
            "type": "kg_construct_walked",
            "entities": len(entities),
            "relationships": len(relationships),
            "stats": all_stats,
        })

        import_res = await maybe_await(ctx.store.import_bulk(entities, relationships))
        entities_added = int((import_res or {}).get("entities_added", 0))
        rels_added = int((import_res or {}).get("relationships_added", 0))
        await _stamp_validation(ctx.store, entities, validation_stats)

        anchor = _core_database(opts, snapshot)
        kg_id = None
        if hasattr(ctx.store, "set_kg_metadata"):
            kg_name = (opts.get("kg_name") or "").strip() or opts["model_name"]
            kg_id = await maybe_await(ctx.store.set_kg_metadata(
                name=kg_name,
                database_name=anchor,
                description=(opts.get("description")
                             or f"Declared model '{opts['model_name']}' "
                                f"(ISO/IEC 11179 registry: {snapshot.catalog_database})"),
                kg_store_config_id=opts.get("kg_store_config_id"),
            ))

        if hasattr(ctx.store, "set_source_descriptor"):
            try:
                await maybe_await(ctx.store.set_source_descriptor(_descriptor_from(opts)))
            except Exception as exc:  # never fail a good build on descriptor stamping
                logger.warning("declared_model: could not stamp source descriptor: %s", exc)

        return ConstructorResult(
            kg_id=kg_id,
            entities_added=entities_added,
            relationships_added=rels_added,
            stats=all_stats,
            extra={
                "model_name": opts["model_name"],
                "catalog_database": snapshot.catalog_database,
                "scope_databases": [anchor],
                "descriptor": _descriptor_from(opts),
            },
        )

    async def rebuild(self, descriptor: Dict[str, Any]) -> Tuple[List[Dict], List[Dict]]:
        """Re-read the registry and re-walk (for the update lifecycle).

        The descriptor is FLAT; the update dialog's scope overrides arrive merged under
        ``descriptor["options"]`` (the REST layer's shape), so they are applied on top
        here — the same convention ``andp_semantic.entry_options`` follows.
        """
        opts = dict(descriptor if isinstance(descriptor, dict) else {})
        over = opts.get("options")
        if isinstance(over, dict):
            for k in _RESYNC_OVERRIDABLE:
                if k in over:
                    opts[k] = over[k]
        opts.setdefault("catalog_database", DEFAULT_CATALOG_DATABASE)
        opts.setdefault("model_name", DEFAULT_MODEL_NAME)
        snapshot = await self._fetch(None, opts)
        ents, rels = walk_declared_model(snapshot, _walk_options_from(opts), WalkStats())
        # A resync is a choice between plain and validated, never a silent repeat: the
        # override the update dialog sends (or the descriptor's own flag) must MEASURE the
        # source here exactly as a fresh build does, or the graph keeps stating nothing
        # about the data it lenses (no row counts, no date spans) while the run reports
        # "validated". Stats ride on the caller's descriptor for the apply handler, the
        # convention `andp_semantic.rebuild` follows.
        _vst = await self._validate(None, opts, ents, rels)
        if _vst and isinstance(descriptor, dict):
            descriptor["_validation_stats"] = _vst
        return ents, rels

    # ── internals ────────────────────────────────────────────────────────────
    async def _fetch(self, ctx: Optional[ConstructorContext],
                     opts: Dict[str, Any]) -> DeclaredModelSnapshot:
        """Open a connection, read the snapshot, always close — off the event loop."""
        def _read() -> DeclaredModelSnapshot:
            con = self._open(ctx, opts)
            try:
                cur = con.cursor()
                return source.fetch_snapshot(
                    cur,
                    catalog_database=(opts.get("catalog_database")
                                      or DEFAULT_CATALOG_DATABASE),
                    model_name=(opts.get("model_name") or DEFAULT_MODEL_NAME),
                    core_database=(opts.get("core_database") or None),
                    source_detail=(opts.get("source_detail") or ""),
                )
            finally:
                try:
                    con.close()
                except Exception:
                    pass

        return await asyncio.to_thread(_read)

    async def _validate(self, ctx: Optional[ConstructorContext], opts: Dict[str, Any],
                        entities: List[Dict], relationships: List[Dict]) -> Dict[str, Any]:
        """Probe the source (row counts / key uniqueness / sampled shapes) and stamp the
        nodes, when ``validate_with_source`` is set. Reuses the one pure source-validation
        core with this constructor's cursor as the runner. Never fails a build."""
        if not opts.get("validate_with_source"):
            return {}
        from . import source_validation as _sv

        def _run() -> Dict[str, Any]:
            con = self._open(ctx, opts)
            try:
                cur = con.cursor()
                return asyncio.run(_sv.validate_with_source(
                    entities, relationships,
                    lambda sql: source._rows(cur, sql), dialect="teradata"))
            finally:
                try:
                    con.close()
                except Exception:
                    pass

        try:
            return await asyncio.to_thread(_run)
        except Exception as exc:
            logger.warning("declared_model: source validation skipped (%s)", exc)
            return {"error": str(exc)[:200]}
