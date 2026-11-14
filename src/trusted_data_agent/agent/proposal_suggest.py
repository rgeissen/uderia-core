"""
proposal_suggest — what a concept the model lacks probably RECORDS, suggested in plain words
when a person opens the model-gap proposal form (PROP-008 OBDA-MODEL).

Where the model is consulted, and where it is not:

  code    the concept's identifier and the reference to what it belongs to — added by
          ``business_terms.complete`` inside ``propose_entity``; never asked of a model, never
          shown as something to type
  code    which entities it may belong to — the model's own, read from the graph; a model
          answer naming anything else is dropped
  model   the domain judgement only: what a "lead" records ("Summary", "Status", "Assigned
          investigator", …) and which of the given entities it most likely belongs to — one
          small call on the answering agent's own model, made only when a person opens the form
          (intent already shown), its tokens and cost booked to the turn the note belongs to

The suggestion is a starting point the person edits; the proposal is still theirs, and the
advisor still decides entity or attribute. Never raises: no model, no credentials or an
unreadable answer is an empty suggestion, and the form works without one.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

app_logger = logging.getLogger("quart.app")

#: Bounds — the prompt and the answer stay small whatever the model's size.
MAX_ENTITIES = 120
MAX_RECORDS = 8
MAX_WORDS_LEN = 60


def build_prompt(name: str, definition: str, question: str, entities: List[Dict[str, str]]) -> str:
    """The one prompt: the concept, the person's words around it, the model's entities."""
    ent_lines = "\n".join(
        f"- {e['leaf']}" + (f": {e['description'][:140]}" if e.get("description") else "")
        for e in entities[:MAX_ENTITIES])
    return (
        f"A person wants to add the concept \"{name}\" to a business data model.\n"
        + (f"They asked: {question.strip()[:400]}\n" if question and question.strip() else "")
        + (f"They define it as: {definition.strip()[:400]}\n" if definition and definition.strip() else "")
        + f"\nThe model's existing entities:\n{ent_lines}\n\n"
        f"Suggest what one \"{name}\" records, as short business terms a non-technical person uses "
        f"(for example \"Summary\", \"Assigned investigator\") — at most {MAX_RECORDS}. Do NOT include an "
        f"identifier for the {name} itself, and do NOT include a reference to the entity it belongs to: "
        f"the platform adds both. Also name the ONE existing entity above it most likely belongs to "
        f"(one {name} refers to one of it), or null if none fits.\n"
        "Return ONLY JSON: {\"records\": [\"...\"], \"belongs_to\": \"<entity name from the list>\" | null}"
    )


def validate(parsed: Any, name: str, entities: List[Dict[str, str]]) -> Dict[str, Any]:
    """Code keeps only what may stand: plain terms, bounded; an entity from the list or none."""
    out: Dict[str, Any] = {"records": [], "belongs_to": None}
    if not isinstance(parsed, dict):
        return out
    by_leaf = {e["leaf"].lower(): e["name"] for e in entities}
    by_name = {e["name"].lower(): e["name"] for e in entities}
    bt = str(parsed.get("belongs_to") or "").strip()
    if bt:
        out["belongs_to"] = by_name.get(bt.lower()) or by_leaf.get(bt.rsplit(".", 1)[-1].lower())
    parent_leaf = (out["belongs_to"] or "").rsplit(".", 1)[-1].lower()
    from trusted_data_agent.agent.kg_constructors.business_terms import _words
    own = "".join(_words(name))
    seen = set()
    for r in parsed.get("records") or []:
        t = " ".join(str(r if isinstance(r, str) else (r or {}).get("name", "")).split())[:MAX_WORDS_LEN]
        w = "".join(_words(t))
        if not w or w in seen:
            continue
        if w in (own + "id", own + "identifier", own) or (parent_leaf and w.startswith("".join(_words(parent_leaf)))):
            continue                       # the platform adds the identifier and the reference
        seen.add(w)
        out["records"].append(t)
        if len(out["records"]) >= MAX_RECORDS:
            break
    return out


async def _llm_for(profile: Dict[str, Any], user_uuid: str):
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.llm.client_factory import get_or_create_llm_client
    from trusted_data_agent.core.configuration_service import retrieve_credentials_for_provider
    cfg_id = (profile or {}).get("llmConfigurationId")
    if not cfg_id:
        return None
    cfg = next((c for c in get_config_manager().get_llm_configurations(user_uuid) if c.get("id") == cfg_id), None)
    if not cfg or not cfg.get("provider") or not cfg.get("model"):
        return None
    creds = ((await retrieve_credentials_for_provider(user_uuid, cfg["provider"])) or {}).get("credentials") or {}
    if cfg.get("credentials"):
        creds = {**creds, **cfg["credentials"]}
    if not creds:
        return None
    return await get_or_create_llm_client(cfg["provider"], cfg["model"], creds), cfg["provider"], cfg["model"]


async def _book(user_uuid: str, session_id: Optional[str], turn_id: Optional[int], in_tok: int, out_tok: int,
                cost_usd: float, model: str, provider: str = "") -> Dict[str, Any]:
    """The call's tokens and cost on the turn the note belongs to — through the ONE booking for a
    post-turn call (``core/post_turn_usage``): session and turn totals, the priced increment, the
    consumption ledger, the persisted step, and the live update on the page."""
    import uuid
    from trusted_data_agent.core.post_turn_usage import book_post_turn_call
    return await book_post_turn_call(
        user_uuid, session_id, turn_id, event_type="proposal_suggestion_complete", step="Proposal Suggested",
        call_id=f"proposal_suggestion:{uuid.uuid4().hex[:12]}", input_tokens=in_tok, output_tokens=out_tok,
        cost_usd=cost_usd, provider=provider, model=model,
        details={"summary": (f"Suggested what the new concept records "
                             f"({in_tok} in / {out_tok} out, ${(cost_usd or 0):.6f})")})


async def suggest(*, user_uuid: str, store, profile: Dict[str, Any], name: str, definition: str = "",
                  question: str = "", session_id: Optional[str] = None, turn_id: Optional[int] = None) -> Dict[str, Any]:
    """``{records, belongs_to, model, input_tokens, output_tokens, cost_usd}`` — empty records when
    no suggestion could be made."""
    res: Dict[str, Any] = {"records": [], "belongs_to": None}
    try:
        raw = [e for e in (await _maybe(store.list_entities(entity_type="entity", limit=400)) or [])
               if e.get("name")]
        entities = sorted(({"name": str(e["name"]), "leaf": str(e["name"]).rsplit(".", 1)[-1],
                            "description": str((e.get("properties") or {}).get("description") or "")}
                           for e in raw), key=lambda e: e["name"])[:MAX_ENTITIES]
        got = await _llm_for(profile, user_uuid)
        if not got:
            return res
        llm, provider, model = got
        from trusted_data_agent.llm.handler import call_llm_api
        from trusted_data_agent.agent.json_utils import robust_json_parse
        r = await call_llm_api(llm, build_prompt(name, definition, question, entities), user_uuid=user_uuid,
                               reason="OBDA-MODEL — proposal suggestion", disabled_history=True,
                               current_provider=provider, current_model=model)
        text, in_tok, out_tok = (r[0], int(r[1] or 0), int(r[2] or 0)) if isinstance(r, tuple) else (r, 0, 0)
        res.update(validate(robust_json_parse(text), name, entities))
        cost = 0.0
        try:
            from trusted_data_agent.core.cost_manager import get_cost_manager
            cost = float(get_cost_manager().calculate_cost(provider, model, in_tok, out_tok) or 0.0)
        except Exception:  # noqa: BLE001
            cost = 0.0
        res.update({"model": model, "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost})
        res["event"] = await _book(user_uuid, session_id, turn_id, in_tok, out_tok, cost, model, provider)
    except Exception as e:  # noqa: BLE001 — a suggestion that fails is no suggestion
        app_logger.debug(f"[proposal-suggest] skipped: {e}")
    return res


async def _maybe(v):
    import inspect
    return await v if inspect.isawaitable(v) else v
