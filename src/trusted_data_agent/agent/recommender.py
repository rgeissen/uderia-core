# src/trusted_data_agent/agent/recommender.py
"""
Next-Step Recommender — ephemeral, token-light follow-up suggestions.

After a turn completes, this module proposes a small set of next steps the user
might want to take. It is built to *outperform* the market's "suggested
questions" widgets on three axes the competition handles poorly:

  1. Action-aware & tool-grounded  — chips like "Chart this" / "Pin to dashboard"
     are derived from the profile's *enabled component manifests*, so every
     suggestion maps to a capability that actually exists (no dead-ends).
  2. Token-light by default        — the two default tiers spend ZERO extra
     LLM tokens (deterministic + RAG retrieval). The LLM tier is strictly opt-in.
  3. Anti-fatigue & optional       — capped count, confidence-gated, suppressed
     when empty, and fully disableable per-profile / per-user.

Cost ladder (cheapest first):
  Tier A "action"   — deterministic chips from enabled components + result shape (0 tokens)
  Tier B "followup" — retrieved follow-up questions from RAG champion cases (0 generation tokens)
  Tier C "llm"      — opt-in, tiny, history-disabled LLM call (uses a small number of tokens)

The single integration seam is ``execution_service.run_agent_execution`` — see
``build_and_emit_suggestions``. The whole module is defensive: any failure is
swallowed and simply yields no suggestions, so a turn can never break because of it.

Architecture doc: docs/Architecture/NEXT_STEP_RECOMMENDER_ARCHITECTURE.md
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional

app_logger = logging.getLogger("quart.app")

_TAG_PREFIX_RE = re.compile(r"^\s*@[\w-]+\s+")   # \w excludes "-"; hyphenated tags are real
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _strip_tag(text: str) -> str:
    """Drop a leading @TAG profile-override prefix from a prompt."""
    return _TAG_PREFIX_RE.sub("", text or "", count=1).strip()


def _norm(text: str) -> str:
    """Normalize for near-duplicate comparison (case/punctuation/whitespace-insensitive)."""
    return _NON_ALNUM_RE.sub(" ", (text or "").lower()).strip()

# --- Default configuration (overridable per-profile via recommenderConfig) ---
DEFAULT_RECOMMENDER_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "tiers": ["action", "followup"],   # add "llm" to opt into Tier C
    "maxSuggestions": 4,
    "relevanceThreshold": 0.65,        # Tier B similarity floor (0..1)
    "llmEnrichment": False,            # Tier C master switch (also requires "llm" in tiers)
}

# Component-id -> action chip template. label/prompt/icon are stable.
# A chip only appears if (a) the component is enabled for the profile AND
# (b) the result-shape predicate for that chip is satisfied this turn.
# S5 — the one action chip that is not a component: continue a budget-stopped turn.
_CONTINUE_CHIP = {
    "id": "action:continue",
    "kind": "action",
    "label": "Continue where it stopped",
    "prompt": "Continue where you stopped: pick up the remaining work from your last answer and finish it.",
    "icon": "continue",
}

_ACTION_TEMPLATES = {
    "chart": {
        "label": "Chart this",
        "prompt": "Create a chart that best visualizes the results above.",
        "icon": "chart",
    },
    "dashboard": {
        "label": "Pin to dashboard",
        "prompt": "Add the results above to a dashboard as a new tile.",
        "icon": "dashboard",
    },
    "scheduler": {
        "label": "Schedule this",
        "prompt": "Schedule this query to run on a recurring basis.",
        "icon": "schedule",
    },
    "flow": {
        "label": "Build a flow",
        "prompt": "Build a flow from the steps used to produce this answer.",
        "icon": "flow",
    },
    "knowledge_graph": {
        "label": "Expand in graph",
        "prompt": "Expand the key entities from this answer in the knowledge graph.",
        "icon": "graph",
    },
    "canvas": {
        "label": "Open in canvas",
        "prompt": "Open the code from this answer in the canvas so I can edit it.",
        "icon": "canvas",
    },
}


def get_recommender_config(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge a profile's ``recommenderConfig`` over the module defaults."""
    cfg = dict(DEFAULT_RECOMMENDER_CONFIG)
    if profile and isinstance(profile.get("recommenderConfig"), dict):
        cfg.update(profile["recommenderConfig"])
    # Normalise
    cfg["tiers"] = list(cfg.get("tiers") or [])
    try:
        cfg["maxSuggestions"] = max(1, int(cfg.get("maxSuggestions", 4)))
    except (TypeError, ValueError):
        cfg["maxSuggestions"] = 4
    return cfg


def _result_shape(collected_data: Any) -> Dict[str, Any]:
    """Inspect collected tool data and report whether it's tabular + row count."""
    shape = {"tabular": False, "rows": 0}
    if not isinstance(collected_data, dict):
        return shape
    for value in collected_data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            shape["tabular"] = True
            shape["rows"] += len(value)
    return shape


def derive_render_flags(final_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compact, JSON-serializable descriptor of a turn's *result shape* — everything
    Tier A needs to decide which action chips apply, with none of the bulk.

    Stashed per-turn (``session_manager.set_turn_rebind_context``) so the
    suggestion set can be rebuilt for a *different* profile when the user changes
    the composer's ``@TAG`` override — without re-materializing ``collected_data``
    or the rendered HTML. ``_build_action_chips`` consumes this descriptor for both
    the live path (derived from the real payload) and the rebind path (read back
    from the turn).
    """
    fp = final_payload or {}
    final_html = fp.get("final_answer") or ""
    final_text = fp.get("final_answer_text") or ""
    shape = _result_shape(fp.get("collected_data"))
    return {
        "tabular": bool(shape["tabular"]),
        "rows": int(shape["rows"]),
        "has_chart": 'data-component-id="chart"' in final_html,
        "has_canvas": 'data-component-id="canvas"' in final_html,
        "has_code": ("```" in final_text or "<pre" in final_html),
        "has_kg": 'data-component-id="knowledge_graph"' in final_html,
        # the answer already offers "Pin to dashboard" directly (a data row's table or a
        # pinnable component row) — the dock does not offer the same act a second time
        "has_direct_pin": ("data-pin-table" in final_html or "data-pin-component" in final_html),
        # S5 — the turn finished early because its budget ran out (graceful or killed): the
        # dock offers "Continue where it stopped" — a chip that needs no component.
        "stopped_early": bool((fp.get("budget") or {}).get("exhausted")),
    }


def _component_enabled_for_profile(
    manager,
    comp_id: str,
    profile: Dict[str, Any],
    profile_type: str,
    user_uuid: Optional[str],
) -> bool:
    """Is this ACTION component enabled for this profile?

    Thin wrapper over ``components.enablement`` — THE one rule, shared with the runtime
    tool surface, the REST profile view and the memory adapter. This function used to
    carry its own copy of the chain; the copy is gone so it can no longer drift.

    ``require_action=True``: a chip / companion skill is only ever offered for an action
    component. (The REST profile view deliberately does NOT pass this — a system
    component must report its own honest state there.)
    """
    try:
        from trusted_data_agent.components import enablement
        return enablement.is_enabled(
            manager.get_component(comp_id), profile, user_uuid,
            profile_type=profile_type, require_action=True,
        )
    except Exception as e:
        app_logger.debug(f"[recommender] enablement lookup failed for {comp_id}: {e}")
        return False


def _build_action_chips(
    profile: Dict[str, Any],
    flags: Dict[str, Any],
    user_uuid: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Tier A — deterministic, tool-grounded action chips (0 tokens).

    Only emits a chip when the relevant component is *enabled for the profile*
    per the same governance the editor enforces (so the action is guaranteed
    fulfillable) and the turn's result shape makes the action meaningful.

    ``flags`` is the compact result-shape descriptor from ``derive_render_flags``
    — so this is identical whether called live (flags derived from the fresh
    payload) or on rebind (flags read back from the stashed turn context).
    """
    flags = flags or {}
    chips: List[Dict[str, Any]] = []

    # S5 — a budget-stopped turn is CONTINUABLE: the first chip picks up where it stopped.
    # Component-independent, so it is offered before the component filter can empty the set.
    if flags.get("stopped_early"):
        chips.append(dict(_CONTINUE_CHIP))

    try:
        from trusted_data_agent.components.manager import get_component_manager
        manager = get_component_manager()
    except Exception as e:
        app_logger.debug(f"[recommender] component inventory unavailable: {e}")
        return chips

    profile_type = profile.get("profile_type", "tool_enabled")
    enabled_ids = {
        comp_id for comp_id in _ACTION_TEMPLATES
        if _component_enabled_for_profile(manager, comp_id, profile, profile_type, user_uuid)
    }
    if not enabled_ids:
        return chips

    has_chart_already = bool(flags.get("has_chart"))
    has_canvas_already = bool(flags.get("has_canvas"))
    has_code = bool(flags.get("has_code"))
    has_kg_already = bool(flags.get("has_kg"))
    # One act, one place: when the answer itself carries "Pin to dashboard" (a table or a
    # pinnable component row), asking the agent to pin would be the same act at the cost of
    # a turn. The chip stays for answers with nothing pinnable to click.
    has_direct_pin = bool(flags.get("has_direct_pin"))
    is_tabular = bool(flags.get("tabular"))

    def add(comp_id: str):
        if comp_id in enabled_ids:
            tpl = _ACTION_TEMPLATES[comp_id]
            chips.append({
                "id": f"action:{comp_id}",
                "kind": "action",
                "label": tpl["label"],
                "prompt": tpl["prompt"],
                "icon": tpl["icon"],
            })

    # Tabular result → visualize / persist / operationalize
    if is_tabular:
        if not has_chart_already:
            add("chart")
        if not has_direct_pin:
            add("dashboard")
        add("scheduler")
        add("flow")
    elif has_chart_already and not has_direct_pin:
        # A chart was produced but no raw table captured — still let them persist it
        add("dashboard")

    # Code in the answer → canvas
    if has_code and not has_canvas_already:
        add("canvas")

    # Knowledge-graph already drawn → offer expansion
    if has_kg_already:
        add("knowledge_graph")

    return chips


async def _build_followup_chips(
    profile: Dict[str, Any],
    user_uuid: str,
    user_input: str,
    cfg: Dict[str, Any],
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Tier B — retrieved follow-up questions from RAG champion cases (0 generation tokens).

    Reuses the same champion-case store that powers input autocomplete, but seeds
    the retrieval with the *just-completed* query so the suggestions are
    "what people usually ask next" rather than "what people ask first".
    """
    user_input = _strip_tag(user_input)
    if not user_input:
        return []
    try:
        from trusted_data_agent.agent.rag_retriever import get_rag_retriever
        retriever = get_rag_retriever()
        if retriever is None:
            return []

        # Honour the profile's autocomplete collection scope (["*"] = all, [] = off).
        autocomplete_collections = profile.get("autocompleteCollections", ["*"])
        if autocomplete_collections == []:
            return []
        allowed = None if autocomplete_collections == ["*"] else set(autocomplete_collections)

        # The access context is NOT optional: `retrieve_examples(rag_context=None)`
        # means "no access scope" and scans EVERY loaded planner collection — every
        # user's private champion cases become suggestion candidates (a privacy
        # leak), and on an instance with hundreds of per-user default repositories
        # the scan took ~27 s on the event loop per turn (Aug 2026, prod: this
        # import pointed at the wrong module, the ImportError was swallowed below,
        # and every turn ran unscoped). No context ⇒ no follow-ups. Fail closed.
        try:
            from trusted_data_agent.agent.rag_access_context import RAGAccessContext
            rag_context = RAGAccessContext(user_uuid, retriever)
        except Exception as e:
            app_logger.warning(
                f"[recommender] follow-up suggestions skipped: access context unavailable ({e})")
            return []

        min_score = float(cfg.get("relevanceThreshold", 0.65))
        # Bounded: this seam is awaited in the turn's critical path (the suggestions
        # ride the same event stream as the answer), so a slow store must never hold
        # the turn — the dial and the next request wait on it.
        from trusted_data_agent.core.config import APP_CONFIG
        timeout = float(getattr(APP_CONFIG, "RECOMMENDER_TIER_B_TIMEOUT_SECONDS", 5.0) or 5.0)
        cases = await asyncio.wait_for(
            retriever.retrieve_examples(
                query=user_input,
                k=limit + 3,                      # over-fetch a little for dedupe
                min_score=min_score,
                allowed_collection_ids=allowed,
                rag_context=rag_context,
                repository_type="planner",
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        app_logger.warning("[recommender] follow-up retrieval exceeded its time budget — skipped")
        return []
    except Exception as e:
        app_logger.debug(f"[recommender] RAG follow-up retrieval failed: {e}")
        return []

    asked = _norm(user_input)
    seen = set()
    chips: List[Dict[str, Any]] = []
    for case in cases or []:
        # Champion cases may carry a leading @TAG override — strip for both display and dedupe.
        q = _strip_tag((case.get("user_query") or "").strip())
        key = _norm(q)
        if not q or key == asked or key in seen:
            continue
        seen.add(key)
        chips.append({
            "id": f"followup:{case.get('case_id', len(chips))}",
            "kind": "followup",
            "label": q,
            "prompt": q,
            "icon": "followup",
        })
        if len(chips) >= limit:
            break
    return chips


async def _build_llm_chips(
    profile: Dict[str, Any],
    user_uuid: str,
    user_input: str,
    final_payload: Dict[str, Any],
    limit: int,
) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Tier C — opt-in, tiny, history-disabled LLM call producing follow-up questions.

    Kept deliberately cheap: no chat history, a compact prompt, and a request for
    at most ``limit`` short questions as a JSON array. Fully optional and wrapped
    so any failure degrades to "no LLM chips".

    Returns ``(chips, usage)`` where ``usage`` is
    ``{"input_tokens", "output_tokens", "provider", "model"}`` (so the caller can
    route the call through the cost engine, token counters, and session history,
    exactly like the Session Name Generation LLM call), or ``None`` if no call ran.
    """
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        from trusted_data_agent.llm.client_factory import get_or_create_llm_client
        from trusted_data_agent.core.configuration_service import retrieve_credentials_for_provider
        from trusted_data_agent.llm.handler import call_llm_api
        from trusted_data_agent.agent.json_utils import robust_json_parse

        config_manager = get_config_manager()
        llm_config_id = profile.get("llmConfigurationId")
        if not llm_config_id:
            return [], None
        llm_configs = config_manager.get_llm_configurations(user_uuid)
        llm_config = next((c for c in llm_configs if c.get("id") == llm_config_id), None)
        if not llm_config:
            return [], None
        provider = llm_config.get("provider")
        model = llm_config.get("model")
        if not provider or not model:
            return [], None

        creds_result = await retrieve_credentials_for_provider(user_uuid, provider)
        credentials = creds_result.get("credentials", {}) or {}
        if llm_config.get("credentials"):
            credentials = {**credentials, **llm_config["credentials"]}
        if not credentials:
            return [], None

        llm_instance = await get_or_create_llm_client(provider, model, credentials)

        answer_snippet = (final_payload.get("final_answer_text") or "")[:800]
        prompt = (
            "You suggest what the user might ask NEXT in this data/analytics conversation.\n"
            f"User's last question: {user_input}\n"
            f"Assistant's answer (excerpt): {answer_snippet}\n\n"
            f"Return ONLY a JSON array of at most {limit} short, specific follow-up "
            "questions (each under 12 words). No prose, no numbering. Example: "
            '["Break this down by region", "Show the trend over the last year"]'
        )

        result = await call_llm_api(
            llm_instance,
            prompt,
            user_uuid=user_uuid,
            reason="Next-step recommender (Tier C)",
            disabled_history=True,
            current_provider=provider,
            current_model=model,
        )
        if isinstance(result, tuple):
            text = result[0]
            in_tok = result[1] if len(result) > 1 else 0
            out_tok = result[2] if len(result) > 2 else 0
            ret_provider = result[3] if len(result) > 3 else None
            ret_model = result[4] if len(result) > 4 else None
        else:
            text, in_tok, out_tok, ret_provider, ret_model = result, 0, 0, None, None

        usage = {
            "input_tokens": int(in_tok or 0),
            "output_tokens": int(out_tok or 0),
            "provider": ret_provider or provider,
            "model": ret_model or model,
        }

        parsed = robust_json_parse(text)
        if not isinstance(parsed, list):
            return [], usage

        asked = _norm(_strip_tag(user_input))
        chips: List[Dict[str, Any]] = []
        for i, item in enumerate(parsed):
            q = (item if isinstance(item, str) else item.get("question", "")).strip() if item else ""
            if not q or _norm(q) == asked:
                continue
            chips.append({
                "id": f"llm:{i}",
                "kind": "followup",
                "label": q,
                "prompt": q,
                "icon": "followup",
            })
            if len(chips) >= limit:
                break
        return chips, usage
    except Exception as e:
        app_logger.debug(f"[recommender] Tier C LLM enrichment failed: {e}")
        return [], None


def _global_gate_open() -> bool:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return bool(getattr(APP_CONFIG, "RECOMMENDER_ENABLED", True))
    except Exception:
        return True


def _merge_unique(
    base: List[Dict[str, Any]],
    extra: List[Dict[str, Any]],
    max_total: int,
) -> List[Dict[str, Any]]:
    """Append ``extra`` chips not already present (by normalized label), capped."""
    existing = {_norm(s["label"]) for s in base}
    out = list(base)
    for chip in extra:
        if _norm(chip["label"]) not in existing:
            out.append(chip)
            existing.add(_norm(chip["label"]))
            if len(out) >= max_total:
                break
    return out[:max_total]


async def _build_base_suggestions(
    profile: Dict[str, Any],
    user_uuid: str,
    user_input: str,
    flags: Dict[str, Any],
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Tiers A + B only (the two free tiers). Action chips lead; follow-ups fill."""
    tiers = set(cfg.get("tiers") or [])
    max_total = cfg["maxSuggestions"]
    suggestions: List[Dict[str, Any]] = []

    # Tier A — action chips (free, grounded, highest value)
    if "action" in tiers:
        suggestions.extend(_build_action_chips(profile, flags, user_uuid))
    suggestions = suggestions[:max_total]

    # Tier B — RAG follow-up questions (free) fill remaining slots
    remaining = max_total - len(suggestions)
    if remaining > 0 and "followup" in tiers:
        followups = await _build_followup_chips(
            profile, user_uuid, user_input, cfg, remaining
        )
        suggestions.extend(followups)

    return suggestions[:max_total]


async def build_rebind_suggestions(
    profile: Optional[Dict[str, Any]],
    user_uuid: str,
    user_input: str,
    flags: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Rebuild the *free* (A + B) suggestion set for a DIFFERENT profile against a
    turn that already ran, using the stashed result-shape ``flags`` + asked query.

    This is what powers "change the @TAG override → the chips re-filter to that
    profile's class" with **zero tokens** and **no new turn**: the result is fixed
    (last turn's ``flags``); only the profile's enabled-component filter (Tier A)
    and collection scope (Tier B) change. Tier C is intentionally excluded — a
    profile toggle must never fire an LLM call.
    """
    if not _global_gate_open() or not profile:
        return []
    cfg = get_recommender_config(profile)
    if not cfg.get("enabled"):
        return []
    return await _build_base_suggestions(profile, user_uuid, user_input, flags or {}, cfg)


async def build_suggestions(
    profile: Optional[Dict[str, Any]],
    user_uuid: str,
    user_input: str,
    final_payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Assemble the next-step suggestion list per the profile's cost ladder (data only).

    Returns a capped, de-duplicated list of chip dicts. Tier C runs here WITHOUT
    cost/observability accounting — used by callers that only need the data (tests).
    The production seam ``build_and_emit_suggestions`` runs Tier C with full
    accounting (Live Status + cost engine + session history).
    """
    if not _global_gate_open():
        return []
    cfg = get_recommender_config(profile)
    if not cfg.get("enabled") or not profile:
        return []

    flags = derive_render_flags(final_payload)
    suggestions = await _build_base_suggestions(profile, user_uuid, user_input, flags, cfg)

    tiers = set(cfg.get("tiers") or [])
    remaining = cfg["maxSuggestions"] - len(suggestions)
    if remaining > 0 and "llm" in tiers and cfg.get("llmEnrichment"):
        llm_chips, _usage = await _build_llm_chips(
            profile, user_uuid, user_input, final_payload, remaining
        )
        suggestions = _merge_unique(suggestions, llm_chips, cfg["maxSuggestions"])

    return suggestions


# Stable identity for the Tier C LLM call across the idempotent live cost ledger.
_TIER_C_CALL_ID = "next_step_recommender"


async def _record_tier_c_usage(
    user_uuid: str,
    session_id: str,
    turn_id: Any,
    in_tok: int,
    out_tok: int,
    cost_usd: float,
    provider: str,
    model: str,
    event_handler,
    system_events: List[Dict[str, Any]],
) -> None:
    """
    Route the Tier C LLM call through the cost engine, token counters, and session
    history — exactly like Session Name Generation:

      • session cumulative totals   → update_token_count
      • live counters (idempotent)  → token_update event (single cost channel, by call_id)
      • turn totals + turn_cost      → update_turn_token_counts (authoritative for reload)
      • consumption / billing ledger → ConsumptionManager.update_turn_tokens
      • Live Status on reload        → append the step events to the turn's system_events
    """
    from trusted_data_agent.core import session_manager

    # 1. Session cumulative token totals.
    await session_manager.update_token_count(user_uuid, session_id, in_tok, out_tok)
    refreshed = await session_manager.get_session(user_uuid, session_id)
    total_in = (refreshed or {}).get("input_tokens", 0)
    total_out = (refreshed or {}).get("output_tokens", 0)

    # 2. Turn-level totals = existing turn tokens + Tier C (existing already includes
    #    the main turn + session-name + extensions, since those ran before us).
    new_turn_in, new_turn_out = in_tok, out_tok
    if turn_id is not None and refreshed:
        wh = refreshed.get("last_turn_data", {}).get("workflow_history", [])
        turn = next((t for t in wh if t.get("turn") == turn_id), None)
        if turn:
            new_turn_in = (turn.get("turn_input_tokens", 0) or 0) + in_tok
            new_turn_out = (turn.get("turn_output_tokens", 0) or 0) + out_tok
        # §3.4: add Tier C as a priced INCREMENT (additive tokens + added_cost_usd) instead of
        # re-pricing the whole turn at the session model — a whole-turn re-price would discard the
        # dual-model-accurate turn_cost the engine persisted (Σ per-call). cost_usd is Tier C's own
        # already-priced cost; the next_step_generation_complete event carries the same figure so
        # _getTurnCost's event-sum stays reconciled.
        await session_manager.update_turn_token_counts(
            user_uuid, session_id, turn_id, in_tok, out_tok,
            added_cost_usd=cost_usd, additive=True
        )

    # 3. Live counters. Cost flows ONLY through this token_update (idempotent per
    #    call_id) so the per-call ledger can't double-count it against the step event.
    try:
        await event_handler({
            "statement_input": in_tok,
            "statement_output": out_tok,
            "turn_input": new_turn_in,
            "turn_output": new_turn_out,
            "total_input": total_in,
            "total_output": total_out,
            "call_id": _TIER_C_CALL_ID,
            "cost_usd": cost_usd,
            "turn_id": turn_id,
        }, "token_update")
    except Exception as e:
        app_logger.debug(f"[recommender] token_update emit skipped: {e}")

    # 4. Consumption / billing ledger.
    try:
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.consumption_manager import ConsumptionManager
        with get_db_session() as db_session:
            ConsumptionManager(db_session).update_turn_tokens(
                user_id=user_uuid,
                session_id=session_id,
                turn_number=turn_id or 0,
                additional_input_tokens=in_tok,
                additional_output_tokens=out_tok,
                additional_cost_usd_cents=int((cost_usd or 0) * 1000000),
            )
    except Exception as e:
        app_logger.debug(f"[recommender] consumption update skipped: {e}")

    # 5. Persist the step events into the turn so they re-render in Live Status on reload.
    try:
        if turn_id is not None and system_events:
            await session_manager.append_system_events_to_turn(
                user_uuid, session_id, turn_id, system_events
            )
    except Exception as e:
        app_logger.debug(f"[recommender] system-event persistence skipped: {e}")


async def _run_tier_c_with_accounting(
    base: List[Dict[str, Any]],
    profile: Dict[str, Any],
    user_uuid: str,
    session_id: str,
    user_input: str,
    final_payload: Dict[str, Any],
    turn_id: Any,
    cfg: Dict[str, Any],
    remaining: int,
    event_handler,
) -> List[Dict[str, Any]]:
    """
    Run Tier C with full observability + accounting, mirroring Session Name Generation:
    emits a single ``next_step_generation_complete`` step (Live Status), computes cost,
    and records tokens/cost/consumption + persists the step.
    """
    # 1. The LLM call (returns chips + token usage). One event is sufficient, so we
    #    emit only the completion step (with tokens/cost) — no separate "start" card.
    llm_chips, usage = await _build_llm_chips(
        profile, user_uuid, user_input, final_payload, remaining
    )
    merged = _merge_unique(base, llm_chips, cfg["maxSuggestions"])

    in_tok = (usage or {}).get("input_tokens", 0) or 0
    out_tok = (usage or {}).get("output_tokens", 0) or 0
    provider = (usage or {}).get("provider") or final_payload.get("provider") or "Unknown"
    model = (usage or {}).get("model") or final_payload.get("model") or "Unknown"

    # 1b. Cost engine.
    cost_usd = 0.0
    if in_tok or out_tok:
        try:
            from trusted_data_agent.core.cost_manager import get_cost_manager
            cost_usd = get_cost_manager().calculate_cost(provider, model, in_tok, out_tok)
        except Exception as e:
            app_logger.debug(f"[recommender] cost calc failed: {e}")
            cost_usd = 0.0

    # 2. The single completion step (Live Status + reload cost source — carries cost_usd).
    complete_evt = {
        "step": "Smart Next Steps Generated",
        "type": "next_step_generation_complete",
        "details": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cost_usd": cost_usd,
            "suggestion_count": len(llm_chips),
            "summary": (
                f"Generated {len(llm_chips)} follow-up suggestion(s) "
                f"({in_tok} in / {out_tok} out, ${cost_usd:.6f})"
            ),
            "session_id": session_id,
        },
    }
    try:
        await event_handler(complete_evt, "next_step_generation_complete")
    except Exception:
        pass

    # 3. Cost engine + token counters + session history (only if the call cost tokens).
    if in_tok or out_tok:
        try:
            await _record_tier_c_usage(
                user_uuid, session_id, turn_id, in_tok, out_tok, cost_usd,
                provider, model, event_handler,
                system_events=[
                    {"type": "next_step_generation_complete", "payload": complete_evt},
                ],
            )
        except Exception as e:
            app_logger.warning(f"[recommender] Tier C accounting failed: {e}")

    return merged


async def build_and_emit_suggestions(
    user_uuid: str,
    session_id: str,
    user_input: str,
    profile: Optional[Dict[str, Any]],
    final_payload: Dict[str, Any],
    turn_id: Any,
    event_handler,
) -> None:
    """
    Single integration seam. Build suggestions and push them to the frontend via
    the same ``event_handler`` that carries every other turn event. Tier C (opt-in)
    is run with full Live-Status + cost-engine + session-history accounting, exactly
    like the Session Name Generation LLM call. Emits a ``notification`` event of type
    ``next_step_suggestions``. Never raises.
    """
    try:
        if not _global_gate_open() or not profile:
            return
        cfg = get_recommender_config(profile)
        if not cfg.get("enabled"):
            return

        # Compact result-shape descriptor — used both for THIS profile's chips and,
        # stashed on the turn, for rebuilding chips when the user switches the
        # composer's @TAG to a different profile (see build_rebind_suggestions).
        flags = derive_render_flags(final_payload)
        if turn_id is not None:
            try:
                from trusted_data_agent.core import session_manager
                await session_manager.set_turn_rebind_context(
                    user_uuid, session_id, turn_id,
                    {"query": _strip_tag(user_input), "flags": flags},
                )
            except Exception as e:
                app_logger.debug(f"[recommender] rebind-context stash skipped: {e}")

        # Tiers A + B (free).
        suggestions = await _build_base_suggestions(
            profile, user_uuid, user_input, flags, cfg
        )

        # Tier C (opt-in) — only if slots remain, with full accounting/observability.
        tiers = set(cfg.get("tiers") or [])
        remaining = cfg["maxSuggestions"] - len(suggestions)
        if remaining > 0 and "llm" in tiers and cfg.get("llmEnrichment"):
            suggestions = await _run_tier_c_with_accounting(
                suggestions, profile, user_uuid, session_id, user_input,
                final_payload, turn_id, cfg, remaining, event_handler,
            )

        # Emit even when empty IF the turn is rebindable: the frontend needs the
        # profile_id + rebindable marker so a later @TAG change can re-filter the
        # chips for this same result (a profile that produced no chips here may
        # produce chips under a different class). An empty, non-rebindable result
        # stays silent (preserves the anti-fatigue "suppress when nothing").
        rebindable = turn_id is not None
        if not suggestions and not rebindable:
            return
        await event_handler({
            "type": "next_step_suggestions",
            "payload": {
                "session_id": session_id,
                "turn_id": turn_id,
                "profile_id": profile.get("id"),
                "rebindable": rebindable,
                "items": suggestions,
            },
        }, "notification")
        app_logger.debug(
            f"[recommender] emitted {len(suggestions)} suggestion(s) for session {session_id} "
            f"(profile={profile.get('id')}, rebindable={rebindable})"
        )
    except Exception as e:
        app_logger.warning(f"[recommender] suppressed (no suggestions emitted): {e}")
