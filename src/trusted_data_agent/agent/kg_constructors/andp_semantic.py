"""
ANDPSemanticStrategy — deterministic KG construction from a **deployed AI-Native
Data Product**'s semantic module.

No LLM, no MCP, no guessing: connect to the product, read the catalog it publishes
about itself, walk it into the platform's two-layer KG topology, import it.
Implements ``rebuild()`` so the KG re-syncs when the product's catalog changes —
which makes a product's own evolution flow through to every agent bound to it.

Why this exists
---------------
Any product built to the AI-Native Data Product standard already publishes an
agent-discoverable description of itself.  This strategy makes that description
directly consumable by Uderia, so a conformant product becomes a grounded,
governed agent surface with **zero bespoke modelling**:

    * physical ``schema``/``table``/``column`` nodes carry the real, queryable
      identifiers → the grounding gate's trusted scope resolves against them
    * ``is_pii`` / ``data_classification`` ride onto the nodes → guardrails are
      driven by the product's *own* declaration rather than a hand-written rule
    * decoded value enumerations ride onto attributes → the planner knows what a
      coded value means instead of guessing

Options (request body / stored descriptor):
    connection_id       : str   Uderia database connection to read through (required
                                unless a cursor/connection factory is injected)
    product             : str   data product name, e.g. "NIBRS_Source" (required)
    semantic_database   : str   skip discovery and read this database directly
    include_columns     : bool  emit attribute/column nodes (default True)
    include_physical    : bool  emit the physical layer (default True)
    include_non_business_modules : bool  emit Semantic/Memory/Observability structure
    max_value_codes     : int   cap on decoded values per attribute (default 60)
    kg_name / description : str display metadata (optional)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from . import andp_semantic_source as source
from . import ontology_alignment as alignment
from .andp_semantic_walker import (
    LAYER_LOGICAL,
    LAYER_PHYSICAL,
    SemanticSnapshot,
    WalkOptions,
    WalkStats,
    scope_databases,
    walk_andp_semantic,
)
from .base import (
    ConstructorContext,
    ConstructorResult,
    KGConstructorStrategy,
    maybe_await,
)
from .registry import register

logger = logging.getLogger("quart.app")

DISCOVERY_MODE = "andp_semantic"


def _walk_options_from(opts: Dict[str, Any]) -> WalkOptions:
    layers = {LAYER_LOGICAL}
    if opts.get("include_physical", True):
        layers.add(LAYER_PHYSICAL)
    return WalkOptions(
        layers=frozenset(layers),
        include_columns=bool(opts.get("include_columns", True)),
        include_non_business_modules=bool(opts.get("include_non_business_modules", True)),
        max_value_codes=int(opts.get("max_value_codes") or 60),
        include_product_knowledge=bool(opts.get("include_product_knowledge", True)),
        max_sql_chars=int(opts.get("max_sql_chars") or 4000),
    )


def _merge_descriptor(existing: Optional[Dict[str, Any]],
                      new: Dict[str, Any]) -> Dict[str, Any]:
    """Fold a product's descriptor into the graph's existing one.

    A KG stores exactly ONE source descriptor, so appending a second product to a
    graph would otherwise overwrite the first — and resync would then silently
    rebuild only the last product, DROPPING the other one's nodes.  A multi-product
    graph therefore carries a ``products`` list, and ``rebuild`` walks every entry.
    Re-adding the same product replaces its entry rather than duplicating it.
    """
    def _as_list(d):
        if not isinstance(d, dict):
            return []
        if isinstance(d.get("products"), list):
            return [p for p in d["products"] if isinstance(p, dict)]
        return [d] if d.get("product") else []

    products = [p for p in _as_list(existing)
                if (p.get("product") or "").strip() != (new.get("product") or "").strip()]
    products.append(new)
    if len(products) == 1:
        return products[0]          # single-product graphs keep the flat shape
    return {"strategy": DISCOVERY_MODE, "discovery_mode": DISCOVERY_MODE,
            "products": products}


#: The descriptor keys the update dialog may override on a resync (the REST layer
#: merges them under ``descriptor["options"]`` — the industry-model shape — while an
#: ANDP descriptor is FLAT, so a resync reads them through ``entry_options``).
_OVERRIDABLE = ("include_columns", "validate_with_source")


def entry_options(descriptor: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """One product entry's build options for a resync: the entry's own (flat) keys, with
    the dialog's ``options`` overrides applied on top. Pure."""
    opts = dict(entry or {})
    over = (descriptor or {}).get("options")
    if isinstance(over, dict):
        for k in _OVERRIDABLE:
            if k in over:
                opts[k] = over[k]
    return opts


def _descriptor_from(opts: Dict[str, Any]) -> Dict[str, Any]:
    """The re-syncable source descriptor stamped onto the KG."""
    return {
        # BOTH keys, deliberately.  The update lifecycle resolves the constructor
        # from `strategy` (rest_routes `_rebuild_candidate_for_kg`), so a
        # descriptor carrying only `discovery_mode` made every ANDP graph report
        # "Unknown constructor strategy 'None'" — `supports_resync = True` was
        # advertised and unreachable.  `discovery_mode` stays for the descriptors
        # already stamped on deployed graphs.
        "strategy": DISCOVERY_MODE,
        "discovery_mode": DISCOVERY_MODE,
        "connection_id": opts.get("connection_id"),
        "product": opts.get("product"),
        "semantic_database": opts.get("semantic_database"),
        "include_columns": bool(opts.get("include_columns", True)),
        "include_physical": bool(opts.get("include_physical", True)),
        "include_non_business_modules": bool(opts.get("include_non_business_modules", True)),
        "max_value_codes": int(opts.get("max_value_codes") or 60),
        "include_product_knowledge": bool(opts.get("include_product_knowledge", True)),
        "max_sql_chars": int(opts.get("max_sql_chars") or 4000),
        # Stamped so a resync honours the build-time choice: a graph built with the
        # alignment off must not silently acquire it on the next update.
        "align_standards": bool(opts.get("align_standards", True)),
        # Validate the graph against the SOURCE (sampled date shapes + ranges, declared-key
        # uniqueness, blank ratios, row counts). Off by default: it costs one scan per
        # table at build time. Stamped so a resync re-samples what the build sampled.
        "validate_with_source": bool(opts.get("validate_with_source", False)),
        "user_uuid": opts.get("user_uuid"),
    }


async def _stamp_validation(store, entities, validation_stats) -> None:
    """Stamp WHEN the graph was validated against its source (+ the card's counts) on the
    graph's metadata — derived from the graph's content, so it can never claim a
    validation the nodes do not carry. Never raises."""
    try:
        from . import source_validation as _sv
        if not hasattr(store, "set_source_validation"):
            return
        if validation_stats and _sv.has_source_validation(entities):
            await maybe_await(store.set_source_validation(
                {**_sv.summarize(entities), "probe_errors": int((validation_stats or {}).get("probe_errors", 0)),
                 "seconds": round(sum((validation_stats.get("seconds") or {}).values()), 1)}))
        elif validation_stats is not None and not _sv.has_source_validation(entities):
            await maybe_await(store.set_source_validation(None))
    except Exception as exc:
        logger.warning("ANDP: could not stamp source validation: %s", exc)


@register
class ANDPSemanticStrategy(KGConstructorStrategy):
    """Build a KG from a deployed ANDP product's semantic module."""

    discovery_modes: Tuple[str, ...] = (DISCOVERY_MODE,)
    supports_resync: bool = True
    label: str = "AI-Native Data Product (semantic module)"

    # ── connection acquisition (injectable for tests) ────────────────────────
    def _open(self, ctx_or_opts: Any, opts: Dict[str, Any]):
        """Return an open DB-API connection.

        A ``connection_factory`` in ``ctx.deps`` takes precedence so tests (and
        alternative backends) never touch a real database.
        """
        deps = getattr(ctx_or_opts, "deps", None) or {}
        factory = deps.get("connection_factory")
        if factory is not None:
            return factory(opts)
        return _open_uderia_connection(opts)

    async def construct(self, ctx: ConstructorContext) -> ConstructorResult:
        opts = dict(ctx.options or {})
        product = (opts.get("product") or "").strip()
        if not product:
            raise source.SourceError("product is required (the data product's name)")
        opts.setdefault("user_uuid", ctx.user_uuid)

        await ctx.emit_event({
            "type": "kg_construct_fetch",
            "product": product,
            "semantic_database": opts.get("semantic_database"),
        })

        snapshot = await self._fetch(ctx, opts)

        # When adding to an existing graph, offer the other products' entities so a
        # declared cross-product bridge can resolve to a real node instead of dangling.
        known = await self._known_entities(ctx.store) if opts.get("append") else None

        walk_opts = _walk_options_from(opts)
        stats = WalkStats()
        entities, relationships = walk_andp_semantic(
            snapshot, walk_opts, stats, known_entities=known)

        validation_stats = await self._validate(ctx, opts, entities, relationships)
        if validation_stats:
            await ctx.emit_event({"type": "kg_construct_validated", "stats": validation_stats})

        concepts, concept_rels, align_stats = await self._align_standards(entities, opts)
        entities = list(entities) + concepts
        relationships = list(relationships) + concept_rels

        all_stats = stats.as_dict()
        all_stats.update(align_stats)
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

        # The product's declared business databases ARE the trusted scope the
        # grounding gate enforces; anchor the KG on the first one.
        scope = scope_databases(snapshot, business_only=True) or [product]

        kg_id = None
        if opts.get("append"):
            # the graph already exists — appending must not rename it or move its
            # anchor database out from under the products already in it
            kg_id = getattr(ctx.store, "kg_id", None)
        elif hasattr(ctx.store, "set_kg_metadata"):
            kg_name = (opts.get("kg_name") or "").strip() or product
            kg_id = await maybe_await(ctx.store.set_kg_metadata(
                name=kg_name,
                database_name=scope[0],
                description=(opts.get("description")
                             or f"AI-Native Data Product '{product}' "
                                f"({snapshot.semantic_database})"),
                kg_store_config_id=opts.get("kg_store_config_id"),
            ))

        if hasattr(ctx.store, "set_source_descriptor"):
            try:
                existing = None
                if opts.get("append") and hasattr(ctx.store, "get_source_descriptor"):
                    existing = await maybe_await(ctx.store.get_source_descriptor())
                await maybe_await(ctx.store.set_source_descriptor(
                    _merge_descriptor(existing, _descriptor_from(opts))))
            except Exception as exc:  # never fail a good build on descriptor stamping
                logger.warning("ANDP: could not stamp source descriptor: %s", exc)

        return ConstructorResult(
            kg_id=kg_id,
            entities_added=entities_added,
            relationships_added=rels_added,
            stats=all_stats,
            extra={
                "product": product,
                "semantic_database": snapshot.semantic_database,
                "scope_databases": scope,
                "descriptor": _descriptor_from(opts),
            },
        )

    async def _align_standards(self, entities: List[Dict], opts: Dict[str, Any],
                               ) -> Tuple[List[Dict], List[Dict], Dict[str, Any]]:
        """Resolve the standards these entities DECLARE and align them to it.

        Called from both `construct` and `rebuild`, deliberately: an alignment produced
        only on the build path would be silently deleted by the next resync, which
        clears the graph and re-imports whatever the rebuild returns.

        Fails safe and never blocks a build. A product declaring no `ontology_iri` does
        no network work at all and yields a byte-identical graph; a product declaring one
        whose standard is unreachable gets a warning and the graph it would have had
        before this pass existed.
        """
        if not opts.get("align_standards", True):
            return [], [], {}
        iris = alignment.declared_iris(entities)
        if not iris:
            return [], [], {}
        try:
            from trusted_data_agent.semantic_interchange import standards, ontology_classes
        except Exception as exc:
            logger.warning("ANDP: ontology alignment unavailable (%s)", exc)
            return [], [], {"standard_iris_unresolved": len(iris)}

        # `only` filters on ABSOLUTE IRIs; a product declares CURIEs. Passing the
        # declared form here matches no class at all and the pass yields nothing.
        abs_iris = [standards.expand_iri(i) or i for i in iris]
        urls = standards.sources_for(iris)
        graph, warnings = await standards.fetch_ontology(urls)
        classes = (ontology_classes.extract_classes(graph, only=abs_iris)
                   if graph is not None else {})
        concepts, rels, stats = alignment.align(
            entities, classes, source_detail=opts.get("product") or None,
            expand=standards.expand_iri)
        if warnings:
            stats = dict(stats)
            stats["standard_fetch_warnings"] = len(warnings)
            for w in warnings:
                logger.info("ANDP: ontology source skipped — %s", w)
        return concepts, rels, stats

    async def rebuild(self, descriptor: Dict[str, Any]) -> Tuple[List[Dict], List[Dict]]:
        """Re-read the product catalog(s) and re-walk (for the update lifecycle).

        A multi-product graph declares every product it holds, so all of them are
        re-walked — rebuilding only one would drop the others from the graph.
        """
        desc = descriptor if isinstance(descriptor, dict) else {}
        products = desc.get("products")
        if not isinstance(products, list) or not products:
            products = [desc]

        all_entities: List[Dict] = []
        all_rels: List[Dict] = []
        seen: set = set()
        for entry in products:
            opts = entry_options(desc, entry)
            if not (opts.get("product") or "").strip():
                raise source.SourceError("descriptor entry missing 'product'")
            snapshot = await self._fetch(None, opts)
            ents, rels = walk_andp_semantic(
                snapshot, _walk_options_from(opts), WalkStats(),
                known_entities=[(e["name"], (e.get("properties") or {}).get("label") or e["name"])
                                for e in all_entities if e["entity_type"] == "entity"])
            _vst = await self._validate(None, opts, ents, rels)
            if _vst:
                # handed to the apply-update handler (which pops it before persisting)
                # so the card can state the run's duration and probe errors
                desc["_validation_stats"] = _vst
            for e in ents:
                key = (e["name"], e["entity_type"])
                if key not in seen:
                    seen.add(key)
                    all_entities.append(e)
            all_rels.extend(rels)

        # Once, over the merged set: a class two products both declare is one node.
        # A multi-product descriptor keeps only `products`, so the flag is resolved from
        # the entries — otherwise a graph whose every product was built with the
        # alignment OFF would silently acquire it on the next resync.
        wants = [bool((e or {}).get("align_standards", True)) for e in products]
        concepts, concept_rels, _ = await self._align_standards(
            all_entities, {"product": desc.get("product"),
                           "align_standards": any(wants) if wants else True})
        for c in concepts:
            key = (c["name"], c["entity_type"])
            if key not in seen:
                seen.add(key)
                all_entities.append(c)
        all_rels.extend(concept_rels)
        return all_entities, all_rels

    async def _known_entities(self, store) -> List[Tuple[str, str]]:
        """(name, label) of logical entities already in the graph — for bridge resolution."""
        try:
            rows = await maybe_await(store.list_entities(entity_type="entity", limit=5000))
        except Exception as exc:
            logger.debug("ANDP: could not read existing entities: %s", exc)
            return []
        out = []
        for e in rows or []:
            name = (e or {}).get("name")
            if not name:
                continue
            label = ((e.get("properties") or {}).get("label")
                     if isinstance(e.get("properties"), dict) else None)
            out.append((name, label or str(name).rpartition(".")[2] or name))
        return out

    # ── internals ────────────────────────────────────────────────────────────
    async def _validate(self, ctx: Optional[ConstructorContext], opts: Dict[str, Any],
                        entities: List[Dict], relationships: List[Dict]) -> Dict[str, Any]:
        """Validate the walked graph against the SOURCE when asked (``validate_with_source``):
        ONE pure core (`kg_constructors.source_validation`) with this constructor's cursor as
        the runner, off the event loop, connection always closed. Never fails a build —
        every probe fails open inside the core; a connection failure here is logged."""
        if not opts.get("validate_with_source"):
            return {}
        import asyncio
        from . import source_validation as _sv

        def _run() -> Dict[str, Any]:
            con = self._open(ctx, opts)
            try:
                cur = con.cursor()
                return asyncio.run(_sv.validate_with_source(
                    entities, relationships, lambda sql: source._rows(cur, sql), dialect="teradata"))
            finally:
                try:
                    con.close()
                except Exception:
                    pass

        try:
            stats = await asyncio.to_thread(_run)
            logger.info("ANDP: source validation for %s — %s", opts.get("product"), stats)
            return stats
        except Exception as exc:
            logger.warning("ANDP: source validation skipped (%s)", exc)
            return {"error": str(exc)[:200]}

    async def _fetch(self, ctx: Optional[ConstructorContext],
                     opts: Dict[str, Any]) -> SemanticSnapshot:
        """Open a connection, read the snapshot, always close.

        Runs the blocking DB work off the event loop so a large catalog read
        (the reference product's is 1,508 rows) never stalls the server.
        """
        import asyncio

        def _read() -> SemanticSnapshot:
            con = self._open(ctx, opts)
            try:
                cur = con.cursor()
                return source.fetch_snapshot(
                    cur,
                    product=(opts.get("product") or "").strip(),
                    semantic_database=(opts.get("semantic_database") or None),
                    include_physical=bool(opts.get("include_physical", True)),
                    include_product_knowledge=bool(
                        opts.get("include_product_knowledge", True)),
                )
            finally:
                try:
                    con.close()
                except Exception:
                    pass

        return await asyncio.to_thread(_read)


def _open_uderia_connection(opts: Dict[str, Any]):
    """Open a Teradata connection from a Uderia database connection id.

    Credentials are resolved through the platform's own encrypted connection
    store — they are never accepted as literals in constructor options.
    """
    conn_id = (opts.get("connection_id") or "").strip()
    user_uuid = (opts.get("user_uuid") or "").strip()
    if not conn_id:
        raise source.SourceError("connection_id is required to read a deployed product")
    if not user_uuid:
        raise source.SourceError("user_uuid is required to resolve the connection")

    from trusted_data_agent.core.database_connections import get_database_connection_store

    resolved = get_database_connection_store().resolve(user_uuid, conn_id)
    if not resolved:
        raise source.SourceError(f"database connection '{conn_id}' not found")

    try:
        import teradatasql
    except ImportError as exc:  # pragma: no cover
        raise source.SourceError("teradatasql is required to read a Teradata product") from exc

    kwargs: Dict[str, Any] = {
        "host": resolved.get("host"),
        "user": resolved.get("username"),
        "password": resolved.get("password"),
    }
    if resolved.get("logmech"):
        kwargs["logmech"] = resolved["logmech"]
    if resolved.get("port"):
        kwargs["dbs_port"] = str(resolved["port"])
    return teradatasql.connect(**kwargs)


def overall_status(per_product: List[Dict[str, Any]]) -> str:
    """The status of a build of several products.

    "partial" is its own status deliberately. A build where one of three products
    failed is not a success: the graph that results looks exactly like a complete
    one, and an agent bound to it fails only later, on a question it can no longer
    answer. Rounding it up to "success" is the single change that would make that
    invisible, so the rule lives here under a name a test can hold, rather than as
    a conditional inside a response literal.
    """
    rows = [r for r in (per_product or []) if isinstance(r, dict)]
    if not rows:
        return "error"
    if all(r.get("status") == "success" for r in rows):
        return "success"
    if any(r.get("status") == "success" for r in rows):
        return "partial"
    return "error"


def aggregate_stats(per_product: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One stats dict for a build of several products — numeric fields summed.

    The response previously served the LAST product's stats as the build's, so a
    CASE+NIBRS card read as NIBRS-only: CASE's entities, glossary and design
    decisions vanished from the display, and NIBRS's divergence count wore the
    whole build's name. Summing is correct for every WalkStats field because each
    is a count over one product's own catalog — the products share nothing.

    Failed products contribute nothing (their stats describe a walk that was
    discarded); booleans are excluded because ``isinstance(True, int)`` holds.
    """
    out: Dict[str, Any] = {}
    for r in per_product or []:
        if not isinstance(r, dict) or r.get("status") != "success":
            continue
        for k, v in (r.get("stats") or {}).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            out[k] = out.get(k, 0) + v
    return out


def union_scope(per_product: List[Dict[str, Any]]) -> List[str]:
    """Ordered union of every successful product's scope databases.

    The trusted-scope LINE on the result card must state what the grounding gate
    will actually enforce — and the gate derives scope from the WHOLE graph, so a
    two-product build's scope is both domains. Serving only the last product's
    scope understated the guarantee. Order follows build order (first occurrence
    wins), so the anchor product reads first.
    """
    seen: List[str] = []
    for r in per_product or []:
        if not isinstance(r, dict) or r.get("status") != "success":
            continue
        for db in r.get("scope_databases") or []:
            if isinstance(db, str) and db and db not in seen:
                seen.append(db)
    return seen


async def reresolve_bridges(store) -> Dict[str, int]:
    """Re-check dangling bridges once every selected product is in the graph.

    A bridge resolves against whatever existed AT THE MOMENT its product was
    walked, so a sequence is one-directional: building NIBRS then CASE lets CASE's
    bridges see NIBRS, but a bridge NIBRS declared against CASE can never resolve —
    its target did not exist yet. That is an artefact of build ORDER, not a fact
    about the products, and an operator selecting both in one go has no way to know
    which order was luckier.

    So after the last product lands, every still-dangling bridge is offered the
    complete entity set. Resolution reuses the walker's own
    :func:`_resolve_external` — same label match, same "unique hit or nothing"
    rule — so this pass can never resolve something the walker would have refused.

    Only ever UPGRADES a bridge (dangling -> resolved). A resolved bridge is never
    revisited, and a target that stays ambiguous stays dangling: an honest dangling
    bridge is the finding, and inventing an edge to make it disappear would destroy
    exactly the signal the bridge exists to carry.
    """
    from .andp_semantic_walker import _resolve_external

    out = {"checked": 0, "resolved": 0}
    try:
        entities = await store.list_entities(limit=999999)  # default 200 returned only attributes — no bridge was ever examined or []
    except Exception as exc:  # noqa: BLE001 — never fail a build over a second pass
        logger.debug("[andp_semantic] bridge re-resolution skipped: %s", exc)
        return out

    by_name = {str(e.get("name")): e for e in entities}
    known = [(str(e.get("name")), str((e.get("properties") or {}).get("label") or ""))
             for e in entities if e.get("entity_type") == "entity"]

    for ent in entities:
        if ent.get("entity_type") != "product_bridge":
            continue
        props = dict(ent.get("properties") or {})
        if props.get("target_resolved"):
            continue
        out["checked"] += 1
        target = props.get("target_reference")
        hit = _resolve_external(known, target)
        if not hit or hit not in by_name:
            continue
        props["target_resolved"] = True
        props["resolution"] = f"target resolved to {hit} (after the full selection was built)"
        try:
            await store.update_entity(ent["id"], properties=props)
            await store.add_relationship(ent["id"], by_name[hit]["id"], "relates_to",
                                         source="andp_semantic")
            out["resolved"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("[andp_semantic] could not link bridge %s: %s",
                         ent.get("name"), exc)
    return out
