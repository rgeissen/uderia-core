"""Engine-agnostic memory orchestration: recall (read), explicit store (tool write), and the
opt-in background extractor (LLM write with full cost accounting). All paths **fail safe** —
memory never breaks a turn. Every engine reaches memory through this one seam (charter #14).

See docs/roadmap/TIER_2.2_AGENT_MEMORY.md §2.4.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Tuple

from components.builtin.memory import adapter, recall as recall_mod
from components.builtin.memory.store import MemoryStore
from components.builtin.memory.types import MemoryConfig, MemoryItem, MemorySource, RecallResult

logger = logging.getLogger("quart.app")

_MEMORY_TASKS: set = set()  # strong refs for fire-and-forget extraction tasks


# --- read --------------------------------------------------------------------

def recall_for_turn(
    user_uuid: str,
    profile: Optional[dict],
    profile_type: str,
    query: str,
    budget_tokens: int,
    token_estimator: Optional[Callable[[str], int]] = None,
    manager=None,
) -> Tuple[RecallResult, str]:
    """Resolve config, rank the user's memory against the query, and return
    ``(RecallResult, block_text)``. Returns empty on dormant config or any error."""
    try:
        cfg = adapter.resolve_config(profile, profile_type, user_uuid, manager)
        if not cfg.recall_enabled:
            return RecallResult(scope=cfg.scope), ""
        profile_id = (profile or {}).get("id")
        store = MemoryStore()
        candidates = store.list_for_recall(user_uuid, profile_id, cfg.scope)
        result = recall_mod.rank(candidates, query, cfg, budget_tokens, token_estimator)
        if result.items:
            store.bump_recall(user_uuid, [it.id for it in result.items if it.id])
        return result, recall_mod.format_block(result)
    except Exception as e:
        logger.debug(f"[memory] recall_for_turn failed (fail-safe, empty): {e}")
        return RecallResult(), ""


# --- explicit write (TDA_Memory tool) ---------------------------------------

def store_explicit(
    user_uuid: str,
    profile: Optional[dict],
    profile_type: str,
    content: str,
    kind: str = "fact",
    mem_key: Optional[str] = None,
    scope_global: bool = False,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    manager=None,
) -> Optional[MemoryItem]:
    """Store a user-requested memory. Gated on the component being enabled. By default the
    item is scoped to THIS agent (profile); ``scope_global=True`` stores it user-wide so it
    recalls under every agent."""
    cfg = adapter.resolve_config(profile, profile_type, user_uuid, manager)
    if cfg.mode == "off":
        return None
    profile_id = None if scope_global else (profile or {}).get("id")
    item = MemoryItem(
        content=content.strip(), kind=kind, source=MemorySource.EXPLICIT,
        mem_key=mem_key, profile_id=profile_id, confidence=1.0,
        session_id=session_id, turn_id=str(turn_id) if turn_id is not None else None,
    )
    return MemoryStore().add(user_uuid, item)


def forget(user_uuid: str, query: str, profile_id: Optional[str] = None) -> int:
    """Delete the single best-matching memory item within the active recall set
    (user-global ∪ this profile). Token-overlap ranked (reusing the deterministic
    recall scorer) so an incidental substring no longer triggers deletion and
    another agent's memory is never touched. Returns the number deleted (0 or 1)."""
    try:
        q = (query or "").strip()
        if not q:
            return 0
        qtok = recall_mod._tokenize(q)
        if not qtok:
            return 0
        store = MemoryStore()
        candidates = store.list_for_recall(user_uuid, profile_id, scope="profile")
        matches = []
        for it in candidates:
            if not it.id:
                continue
            if qtok & recall_mod._tokenize(it.content):  # require genuine token overlap
                matches.append((recall_mod.score_item(it, qtok), it.updated_at or "", it.id, it))
        if not matches:
            return 0
        matches.sort(key=lambda m: (m[0], m[1], m[2]), reverse=True)
        best = matches[0][3]
        return 1 if store.delete(user_uuid, best.id) else 0
    except Exception as e:
        logger.debug(f"[memory] forget failed: {e}")
        return 0


# --- background extraction (opt-in, LLM, cost-accounted) ---------------------

def maybe_dispatch_extraction(*, user_uuid, session_id, profile, profile_type,
                              user_input, answer_text, turn_id, event_handler) -> None:
    """Fire-and-forget the learned-extraction hook iff the profile opted into background
    learning. Strong-ref'd, discarded on done; never blocks/raises into the turn."""
    try:
        cfg = adapter.resolve_config(profile, profile_type, user_uuid)
        if not cfg.extraction_enabled:
            return
        import asyncio
        task = asyncio.ensure_future(_extract_and_store(
            user_uuid=user_uuid, session_id=session_id, profile=profile,
            user_input=user_input, answer_text=answer_text, turn_id=turn_id,
            event_handler=event_handler, cfg=cfg))
        _MEMORY_TASKS.add(task)
        task.add_done_callback(_MEMORY_TASKS.discard)
    except Exception as e:
        logger.debug(f"[memory] extraction dispatch skipped: {e}")


async def _extract_and_store(*, user_uuid, session_id, profile, user_input,
                             answer_text, turn_id, event_handler, cfg: MemoryConfig) -> None:
    """Run the LLM extraction, store new items, account for cost (charter #16), emit a
    ``memory_write`` step. Mirrors the recommender Tier-C accounting."""
    from components.builtin.memory import extractor

    try:
        text, in_tok, out_tok, provider, model = await _call_extraction_llm(
            profile, user_uuid, user_input, answer_text)
        if text is None:
            return
        candidates = extractor.parse_candidates(text)
        store = MemoryStore()
        profile_id = (profile or {}).get("id") if cfg.scope != "global" else None
        stored = []
        for cand in candidates:
            cand.profile_id = profile_id
            cand.session_id = session_id
            cand.turn_id = str(turn_id) if turn_id is not None else None
            stored.append(store.add(user_uuid, cand))

        cost_usd = 0.0
        if in_tok or out_tok:
            try:
                from trusted_data_agent.core.cost_manager import CostManager
                cost_usd = CostManager().calculate_cost(provider, model, in_tok, out_tok)
            except Exception:
                cost_usd = 0.0

        # booked through the ONE post-turn booking (core/post_turn_usage): this runs after the
        # turn's stream closed, so the step and counters go live on the notification channel,
        # and the turn gains this call's priced cost (never a re-price of the whole turn)
        from trusted_data_agent.core.post_turn_usage import book_post_turn_call
        await book_post_turn_call(
            user_uuid, session_id, turn_id, event_type="memory_write", step="Memory Updated",
            call_id=f"memory_extraction:{turn_id}", input_tokens=in_tok, output_tokens=out_tok,
            cost_usd=cost_usd, provider=provider or "", model=model or "",
            details={"stored": len(stored), "items": [it.content for it in stored],
                     "summary": (f"Learned {len(stored)} durable memory item(s) "
                                 f"({in_tok} in / {out_tok} out, ${cost_usd:.6f})")})
    except Exception as e:
        logger.debug(f"[memory] extraction failed (fail-safe): {e}")


async def _call_extraction_llm(profile, user_uuid, user_input, answer_text):
    """Resolve the profile's LLM and run the extraction prompt. Returns
    ``(text, in_tok, out_tok, provider, model)`` or ``(None, ...)`` if unavailable."""
    from components.builtin.memory import extractor
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.llm.client_factory import get_or_create_llm_client
        from trusted_data_agent.core.configuration_service import retrieve_credentials_for_provider
        from trusted_data_agent.llm.handler import call_llm_api

        cm = get_config_manager()
        llm_config_id = (profile or {}).get("llmConfigurationId")
        if not llm_config_id:
            return None, 0, 0, None, None
        llm_config = next((c for c in cm.get_llm_configurations(user_uuid)
                           if c.get("id") == llm_config_id), None)
        if not llm_config:
            return None, 0, 0, None, None
        provider, model = llm_config.get("provider"), llm_config.get("model")
        if not provider or not model:
            return None, 0, 0, None, None
        creds = (await retrieve_credentials_for_provider(user_uuid, provider)).get("credentials", {}) or {}
        if llm_config.get("credentials"):
            creds = {**creds, **llm_config["credentials"]}
        if not creds:
            return None, 0, 0, None, None
        llm = await get_or_create_llm_client(provider, model, creds)
        prompt = extractor.build_extraction_prompt(user_input, answer_text)
        result = await call_llm_api(llm, prompt, user_uuid=user_uuid,
                                    reason="Agent memory extraction", disabled_history=True,
                                    current_provider=provider, current_model=model)
        if isinstance(result, tuple):
            text = result[0]
            in_tok = result[1] if len(result) > 1 else 0
            out_tok = result[2] if len(result) > 2 else 0
            rp = result[3] if len(result) > 3 else None
            rm = result[4] if len(result) > 4 else None
        else:
            text, in_tok, out_tok, rp, rm = result, 0, 0, None, None
        return text, int(in_tok or 0), int(out_tok or 0), rp or provider, rm or model
    except Exception as e:
        logger.debug(f"[memory] extraction LLM call failed: {e}")
        return None, 0, 0, None, None
