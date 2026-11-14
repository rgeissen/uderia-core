"""Knowledge Graph context module.

Promotes the knowledge-graph enrichment — previously appended raw onto the
``knowledge_context`` channel by each engine — into a first-class, budget-managed
context module (sibling of ``knowledge_context``). The node count of the extracted
subgraph now scales to the module's token allocation instead of a fixed 500-node
cap, and the contribution gets its own bar in the context_window_snapshot and a
real condensation strategy.

All-engine (IFOC) by design: applies to ``tool_enabled`` / ``llm_only`` /
``rag_focused`` — the same set as the sibling ``knowledge_context`` module. Genie
coordinators are deliberately excluded (the coordinator injects no KG text of its
own; KG enrichment reaches genie through its slaves, which are real I/F/O engines).

The actual subgraph extraction, deployed-identifier resolution, and trusted-scope
stamping live in the knowledge-graph COMPONENT
(``components.builtin.knowledge_graph.handler``) — this module is the thin,
budget-aware ontology delivery seam over that single implementation, reached
via the same ``get_component_context_enrichment`` entry the executor already used.
The returned ``kg_enrichment_event`` (carrying ``scope_databases`` /
``scope_entities``) is preserved verbatim in the contribution metadata so the
grounding gate (Tier 1.1) keeps working unchanged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..base import AssemblyContext, Contribution, ContextModule
from ..token_estimator import estimate_tokens

logger = logging.getLogger("quart.app")


class KnowledgeGraphContextModule(ContextModule):
    """Injects the query-relevant knowledge-graph subgraph, budget-managed."""

    # Rough average tokens per emitted graph node (table/column/entity line).
    # Conservative on purpose: better to slightly under-request and leave budget
    # on the table than to overshoot and force a condense pass every turn.
    EST_TOKENS_PER_NODE: int = 30

    # Floor / ceiling for the node cap regardless of budget. The ceiling matches
    # the component's historical fixed cap so a generous budget never asks for
    # more than the extractor was ever asked for before.
    MIN_NODES: int = 20
    MAX_NODES: int = 500

    @property
    def module_id(self) -> str:
        return "knowledge_graph_context"

    def applies_to(self, profile_type: str) -> bool:
        # Same set as the sibling knowledge_context module. genie excluded —
        # KG reaches the coordinator through its (real-engine) slaves.
        return profile_type in ("tool_enabled", "llm_only", "rag_focused")

    def _budget_to_max_nodes(self, budget: int) -> int:
        """Map a token budget to a subgraph node cap."""
        if budget <= 0:
            return self.MAX_NODES
        return max(self.MIN_NODES, min(self.MAX_NODES, budget // self.EST_TOKENS_PER_NODE))

    @staticmethod
    def _trim_to_budget(content: str, target_tokens: int) -> str:
        """Keep leading lines until the budget is hit.

        The enrichment text is ordered most-valuable-first (header → TABLE
        SCHEMAS backbone → entity-group listings), so dropping trailing lines
        sheds the least-critical content while preserving the schema/JOIN
        backbone the engine needs to validate SQL.
        """
        if target_tokens <= 0:
            return ""
        kept: List[str] = []
        used = 0
        for line in content.split("\n"):
            t = estimate_tokens(line + "\n")
            if used + t > target_tokens and kept:
                break
            kept.append(line)
            used += t
        return "\n".join(kept)

    async def contribute(self, budget: int, ctx: AssemblyContext) -> Contribution:
        try:
            query = (ctx.session_data or {}).get("current_query", "") or ""
            if not query or not ctx.profile_id:
                return Contribution(
                    content="", tokens_used=0,
                    metadata={"entities": 0, "reason": "no_query_or_profile"},
                    condensable=True,
                )

            max_nodes = self._budget_to_max_nodes(budget)

            from trusted_data_agent.components.manager import get_component_context_enrichment
            # RENDER, THEN MEASURE. The 30-tokens/node estimate is 5× low on rich
            # graphs (measured 136–174 on an ANDP graph), so a single request could
            # overshoot the budget by 8× and the tail-trim then dropped the most
            # valuable lines. Now: request, measure, and if over budget re-request
            # with a smaller node cap and a tighter per-table column cap (bounded
            # rounds) — condensation is the last resort, not the first.
            # session_id lets the enricher carry the previous turn's entities forward;
            # passed only to a seam that accepts it (legacy / third-party enrichers keep
            # their shape — the same guard the column cap uses below)
            import inspect as _inspect
            try:
                _accepts_sid = "session_id" in _inspect.signature(get_component_context_enrichment).parameters
            except (ValueError, TypeError):
                _accepts_sid = False
            _sid_kw = {"session_id": ctx.session_id} if (_accepts_sid and ctx.session_id) else {}
            text, details = await get_component_context_enrichment(
                query=query, profile_id=ctx.profile_id, user_uuid=ctx.user_uuid,
                max_nodes=max_nodes, **_sid_kw,
            )
            column_cap = None
            _round = 0
            # pass the column cap only to a seam that accepts it (legacy / third-party
            # enrichers keep their 4-arg shape; a TypeError here must never empty the turn)
            try:
                _accepts_cap = "column_cap" in _inspect.signature(get_component_context_enrichment).parameters
            except (ValueError, TypeError):
                _accepts_cap = False
            for _round in range(1, self.MAX_FIT_ROUNDS + 1):
                if not text or budget <= 0 or estimate_tokens(text) <= budget:
                    _round -= 1
                    break
                ratio = budget / max(1, estimate_tokens(text))
                max_nodes = max(self.MIN_NODES, int(max_nodes * min(0.75, ratio)))
                column_cap = max(3, int((column_cap or self.DEFAULT_COLUMN_CAP) * min(0.75, ratio)))
                _kw = {"column_cap": column_cap} if _accepts_cap else {}
                text, details = await get_component_context_enrichment(
                    query=query, profile_id=ctx.profile_id, user_uuid=ctx.user_uuid,
                    max_nodes=max_nodes, **_kw, **_sid_kw,
                )

            if not text:
                # No query-relevant subgraph. The always-on trusted scope is
                # stamped separately by the executor seam (_apply_trusted_scope),
                # so the grounding gate is still armed on out-of-domain queries.
                return Contribution(
                    content="", tokens_used=0,
                    metadata={"entities": 0, "reason": "no_match", "max_nodes": max_nodes},
                    condensable=True,
                )

            details = details or []
            # Reconstruct the exact event shape the executor + grounding gate
            # consume (mirrors executor._get_kg_enrichment legacy stamping).
            event: Dict[str, Any] = {
                "enrichments": details,
                "total_entities": sum(d.get("entity_count", 0) for d in details),
                "total_relationships": sum(d.get("relationship_count", 0) for d in details),
                "session_id": ctx.session_id,
            }

            tokens = estimate_tokens(text)
            # Floor: if the fit rounds still overshoot, condense SECTION-AWARE
            # (relationships → attribute prose → other groups; never the schema
            # backbone) so the contribution never blows its allocation.
            if budget > 0 and tokens > budget:
                text = self._condense_sections(text, budget)
                tokens = estimate_tokens(text)

            return Contribution(
                content=text,
                tokens_used=tokens,
                metadata={
                    "entities": event["total_entities"],
                    "relationships": event["total_relationships"],
                    "max_nodes": max_nodes,
                    "column_cap": column_cap,
                    "fit_rounds": _round,
                    # Consumed by the executor seam to re-stamp kg_enrichment_event
                    # for the grounding gate, Live Status replay, and persistence.
                    "kg_enrichment_event": event,
                },
                condensable=True,
            )
        except Exception as e:  # noqa: BLE001 — fail-safe: never break assembly
            logger.warning(f"[knowledge_graph_context] contribute failed (fail-safe, empty): {type(e).__name__}: {e}")
            return Contribution(
                content="", tokens_used=0,
                metadata={"entities": 0, "reason": "error"},
                condensable=True,
            )

    async def condense(self, content: str, target_tokens: int,
                       ctx: AssemblyContext) -> Contribution:
        """Trim trailing (least-critical) graph lines to fit the smaller budget."""
        if not content:
            return Contribution(content="", tokens_used=0, metadata={"condensed": True})
        out = self._condense_sections(content, target_tokens)
        return Contribution(
            content=out,
            tokens_used=estimate_tokens(out),
            metadata={"condensed": True, "strategy": "section_aware_trim"},
            condensable=True,
        )

    # Sections in the order they are SHED (lowest value first). Anything not
    # listed — the header, TABLE SCHEMAS, JOINABLE COLUMNS, CODED COLUMNS, METRIC
    # ENTITIES, SIGNED DATA PRODUCTS — is the backbone and is trimmed last,
    # tail-first, only when nothing else is left to shed.
    SHED_ORDER = ("KNOWN RELATIONSHIPS", "ATTRIBUTE ENTITIES", "COLUMN ENTITIES",
                  "TABLE ENTITIES", "SCHEMA ENTITIES", "DATABASE ENTITIES", "DOMAIN ENTITIES",
                  "ENTITY ENTITIES", "TAXONOMY ENTITIES", "BUSINESS_CONCEPT ENTITIES",
                  "QUERY_RECIPE ENTITIES", "DESIGN_DECISION ENTITIES", "NAMING_STANDARD ENTITIES",
                  "QUALITY_METRIC ENTITIES", "PRODUCT_BRIDGE ENTITIES")
    MAX_FIT_ROUNDS: int = 3
    DEFAULT_COLUMN_CAP: int = 12

    @classmethod
    def _split_sections(cls, content: str):
        """[(header_line_or_None, [lines])] in document order."""
        sections, cur_head, cur = [], None, []
        for line in content.split("\n"):
            s = line.strip()
            is_head = (s.endswith(":") and s.replace("_", " ").replace(" ENTITIES", "").replace(" COLUMNS", "").replace(" SCHEMAS", "").replace(" RELATIONSHIPS", "").split("(")[0].strip().isupper()) \
                or s.startswith("TABLE SCHEMAS") or s.startswith("JOINABLE COLUMNS") or s.startswith("KNOWN RELATIONSHIPS") \
                or s.startswith("CODED") or s.startswith("METRIC ENTITIES") or s.startswith("SIGNED DATA PRODUCTS")
            if is_head and s:
                sections.append((cur_head, cur)); cur_head, cur = s, [line]
            else:
                cur.append(line)
        sections.append((cur_head, cur))
        return sections

    @classmethod
    def _condense_sections(cls, content: str, target_tokens: int) -> str:
        """Shed whole sections lowest-value-first (tail lines within a section),
        keep the schema backbone; fall back to the tail trim only on the backbone."""
        if target_tokens <= 0:
            return ""
        if estimate_tokens(content) <= target_tokens:
            return content
        sections = cls._split_sections(content)

        def total():
            return sum(estimate_tokens("\n".join(l) + "\n") for _, l in sections)
        for key in cls.SHED_ORDER:
            for idx, (head, lines) in enumerate(sections):
                if head and head.startswith(key):
                    while len(lines) > 1 and total() > target_tokens:
                        lines.pop()          # tail line of this section
                    if total() <= target_tokens:
                        break
            if total() <= target_tokens:
                break
        out = "\n".join("\n".join(l) for _, l in sections if l)
        if estimate_tokens(out) > target_tokens:
            out = cls._trim_to_budget(out, target_tokens)
        return out
