"""
!classify extension — LLM-powered answer categorization.

Uses the session's LLM to semantically classify the answer into one of a set
of categories. Produces a primary category + confidence, with a `branch_key`
for routing on a flow decision node or an external automation engine.

Tier: LLM (LLMExtension) — calls the session's configured LLM.

Usage:
    !classify
        → classify into the default ops vocabulary:
          alert, performance, data_quality, capacity, security,
          data_report, informational

    !classify:billing,technical,sales,other
        → classify into YOUR OWN categories (comma-separated, 2+). Makes the
          extension reusable for any flow, not just operational answers. The
          `branch_key` is the chosen category — branch on it directly.
"""

import json
from typing import List, Optional

from trusted_data_agent.extensions.base import LLMExtension
from trusted_data_agent.extensions.models import (
    ExtensionContext,
    ExtensionResult,
    OutputTarget,
)
from trusted_data_agent.extensions.helpers import extract_json_from_text

VALID_CATEGORIES = [
    "alert", "performance", "data_quality", "capacity",
    "security", "data_report", "informational",
]

# Default prompt keeps the rich, descriptive ops vocabulary.
SYSTEM_PROMPT = """You are a content classification engine. Classify the provided text into exactly one primary category.

Valid categories:
- "alert": Critical issues, failures, outages, errors
- "performance": CPU, memory, throughput, latency, response time metrics
- "data_quality": Missing data, nulls, duplicates, validation issues
- "capacity": Storage, growth, forecasting, quotas
- "security": Access control, permissions, audit, compliance
- "data_report": Query results, tables, records, summaries, counts
- "informational": General information that doesn't fit other categories

You MUST return valid JSON with exactly these fields:
- "primary_category": One of the valid categories above
- "confidence": A number between 0.0 and 1.0
- "all_categories": An object mapping each relevant category to a relevance score (0-10)
- "branch_key": Same as primary_category (for workflow/decision-node routing)
- "reasoning": A brief (1 sentence) explanation of why this category was chosen

Respond with JSON only, no markdown fences or extra text."""


def _parse_categories(param: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated category list from the param. None if no param."""
    if not param:
        return None
    cats = []
    seen = set()
    for raw in param.split(","):
        c = raw.strip().lower().replace(" ", "_")
        if c and c not in seen:
            seen.add(c)
            cats.append(c)
    return cats or None


def _build_system_prompt(categories: List[str]) -> str:
    """Build a classification prompt for an arbitrary category list."""
    cat_lines = "\n".join(f'- "{c}"' for c in categories)
    return (
        "You are a content classification engine. Classify the provided text "
        "into exactly one primary category.\n\n"
        f"Valid categories:\n{cat_lines}\n\n"
        "You MUST return valid JSON with exactly these fields:\n"
        '- "primary_category": One of the valid categories above\n'
        '- "confidence": A number between 0.0 and 1.0\n'
        '- "all_categories": An object mapping each relevant category to a relevance score (0-10)\n'
        '- "branch_key": Same as primary_category (for workflow/decision-node routing)\n'
        '- "reasoning": A brief (1 sentence) explanation of why this category was chosen\n\n'
        "Respond with JSON only, no markdown fences or extra text."
    )


class ClassifyExtension(LLMExtension):

    name = "classify"
    description = "LLM classification into the default ops vocabulary or your own categories (:a,b,c)"

    @property
    def output_target(self) -> OutputTarget:
        return OutputTarget.SILENT

    def validate_param(self, param: Optional[str] = None):
        if param is None:
            return True, None
        cats = _parse_categories(param)
        if not cats or len(cats) < 2:
            return False, (
                "classify needs at least 2 comma-separated categories, "
                "e.g. 'billing,technical,sales'."
            )
        return True, None

    async def execute(
        self,
        context: ExtensionContext,
        param: Optional[str] = None,
    ) -> ExtensionResult:

        # Custom categories via param, else the default ops vocabulary.
        custom = _parse_categories(param)
        categories = custom or VALID_CATEGORIES
        system_prompt = _build_system_prompt(custom) if custom else SYSTEM_PROMPT
        fallback = categories[0] if custom else "informational"

        prompt = (
            f"Classify this LLM response:\n\n"
            f"**Original query:** {context.clean_query}\n\n"
            f"**LLM answer:**\n{context.answer_text[:3000]}"
        )

        try:
            raw_response = await self.call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.0,
                json_mode=True,
            )

            parsed = extract_json_from_text(raw_response)
            if parsed is None:
                parsed = json.loads(raw_response)

            primary = parsed.get("primary_category", fallback)
            # Validate against the active category set
            if primary not in categories:
                primary = fallback

            output = {
                "primary_category": primary,
                "confidence": parsed.get("confidence", 0.8),
                "all_categories": parsed.get("all_categories", {primary: 10}),
                "branch_key": primary,
                "query": context.clean_query,
            }

        except Exception as e:
            output = {
                "primary_category": fallback,
                "confidence": 0.0,
                "all_categories": {},
                "branch_key": fallback,
                "query": context.clean_query,
                "error": f"LLM classification failed: {str(e)}",
            }

        return ExtensionResult(
            extension_name="classify",
            content=output,
            content_type="application/json",
            metadata={"categories_matched": len(output.get("all_categories", {}))},
        )
