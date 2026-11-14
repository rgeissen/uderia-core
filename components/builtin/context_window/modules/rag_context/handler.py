"""
RAG Context module.

Wraps the champion case retrieval from planner.py and rag_retriever.py.
Retrieves proven execution strategies from planner repositories via
semantic search, providing the LLM with successful patterns to follow.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from ..base import AssemblyContext, Contribution, ContextModule
from ..token_estimator import estimate_tokens, tokens_to_chars

logger = logging.getLogger("quart.app")


class RAGContextModule(ContextModule):
    """
    Contributes RAG champion cases to the context window.

    Retrieves proven execution strategies from planner repositories
    (ChromaDB collections) using semantic similarity search. These
    champion cases provide the LLM with templates for successful
    tool usage patterns, reducing planning errors and token usage.

    Condensation strategy: fewer examples (reduce k parameter).
    Purgeable: clears RAG collection data.
    """

    @property
    def module_id(self) -> str:
        return "rag_context"

    def applies_to(self, profile_type: str) -> bool:
        return profile_type == "tool_enabled"

    async def contribute(
        self,
        budget: int,
        ctx: AssemblyContext,
    ) -> Contribution:
        """
        Retrieve champion cases within the token budget.

        Queries planner repositories for semantically similar past successes
        and formats them for injection into the strategic planning prompt.
        """
        session_data = ctx.session_data
        profile_config = ctx.profile_config
        user_uuid = ctx.user_uuid

        # Get the current query from session data
        query = session_data.get("current_query", "")
        if not query:
            return Contribution(
                content="",
                tokens_used=0,
                metadata={"cases_retrieved": 0, "reason": "no_query"},
                condensable=False,
            )

        # The planner OWNS champion retrieval (planner.py: `retrieve_examples` with the
        # profile's `ragCollections` filter, the read-back of own learning, the OBDA-READ
        # surface + ontology-metric filters, and the adaptive few-shot header) and it
        # runs AFTER this assembly — the metric filter reads the KG enrichment that the
        # knowledge_graph module (priority 72) produces after this module (75). Until
        # Sep 2026 this module ALSO retrieved, from a `ragConfig.collections` key no
        # profile carries, so it reported `no_collections` on every turn — a module
        # describing a retrieval it did not perform, on a surface whose job is
        # transparency. It now reports the one true thing: where the champions come
        # from and what filter applies. The budget line stays 0 because the planner's
        # few-shot block is injected outside this module's allocation (documented in
        # CONTEXT_WINDOW_ARCHITECTURE.md).
        rag_collections = profile_config.get("ragCollections") or ["*"]
        if rag_collections and rag_collections != ["*"]:
            scope = f"{len(rag_collections)} configured planner repositories"
        else:
            scope = "all accessible planner repositories"
        return Contribution(
            content="",
            tokens_used=0,
            metadata={"cases_retrieved": 0, "reason": "planner_owned",
                      "note": "champion retrieval is performed by the planner (see the "
                              "rag_retrieval event); this module accounts for none of it",
                      "planner_scope": scope},
            condensable=False,
        )

    async def condense(
        self,
        content: str,
        target_tokens: int,
        ctx: AssemblyContext,
    ) -> Contribution:
        """Condense by reducing to fewer examples."""
        if not content:
            return Contribution(
                content="",
                tokens_used=0,
                metadata={"condensed": True, "cases_retrieved": 0},
            )

        # Simple approach: truncate to target
        char_budget = tokens_to_chars(target_tokens)
        if len(content) > char_budget:
            # Find last complete case separator before budget
            truncated = content[:char_budget]
            last_sep = truncated.rfind("\n---\n")
            if last_sep > 0:
                truncated = truncated[:last_sep]
            content = truncated

        tokens = estimate_tokens(content)
        return Contribution(
            content=content,
            tokens_used=tokens,
            metadata={"condensed": True, "strategy": "fewer_examples"},
        )

    async def purge(
        self,
        session_id: str,
        user_uuid: str,
    ) -> Dict[str, Any]:
        """Purge RAG retrieval cache."""
        return {
            "purged": True,
            "details": "RAG retrieval cache cleared (stateless — no persistent cache)",
        }

    def _format_cases(self, cases: list, char_budget: int) -> str:
        """Format champion cases for context injection."""
        lines = ["--- CHAMPION CASES ---"]
        lines.append(
            "The following proven execution strategies were retrieved from "
            "successful past queries. Use them as templates when applicable.\n"
        )

        total_chars = sum(len(line) for line in lines)
        included = 0

        for case in cases:
            case_text = self._format_single_case(case)
            if total_chars + len(case_text) > char_budget and included > 0:
                break
            lines.append(case_text)
            total_chars += len(case_text)
            included += 1

        return "\n".join(lines)

    def _format_single_case(self, case: dict) -> str:
        """Format a single champion case."""
        parts = []
        score = case.get("similarity_score", 0)
        collection = case.get("collection_name", "unknown")
        content = case.get("content", case.get("summary", ""))

        parts.append(f"Champion Case (score: {score:.2f}, collection: {collection}):")
        parts.append(content)
        parts.append("---")

        return "\n".join(parts)
