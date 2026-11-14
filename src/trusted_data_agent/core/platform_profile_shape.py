"""
The agent's composition, shaped in code before TDA_Platform submits it.

An agent is built from ingredients the user already has — knowledge repositories, expert
agents, skills, components, a context window type. A model names them the way a person does
("@SALES", "Uderia Documentation", "chart"); the profile stores ids in fixed shapes. Turning
one into the other is deterministic, so it is done here and never left to the model
(component → LLM → skill layering: the code takes everything it can decide).

``shape(op_id, body, user_uuid, current)`` runs inside ``platform_operations.bind`` when the
call is resolved (submission AND approval), so the request parked for a person is exactly the
request that will run:

    knowledgeConfig.collections   each item an id, a name or {"id": …} → {"id": <int>, …}, one of
                                  the caller's KNOWLEDGE repositories; a non-empty list turns
                                  retrieval on unless ``enabled`` is sent
    ragCollections                planner repositories, by id or name → [<int>, …]
    genieConfig.slaveProfiles     expert agents, by @TAG or id → [<profile id>, …] (never the
                                  coordinator itself)
    skillsConfig                  a list of skill ids / entries, or {"skills": [...]} → entries
                                  {"id", "active", "enabled", "auto": false} — a skill the
                                  builder names is the user's choice, not auto-managed
    componentConfig               {<component id>: true | false | {...}} → {"enabled": …, …}
    contextWindowTypeId           by id or name → the id

On ``profiles.update`` each object is MERGED onto the agent's current one — send only what
changes: a component or a skill entry merges by its id, other top-level keys replace, and a
list (``collections``, ``slaveProfiles``) is replaced as sent. ``update_profile`` itself is a
shallow replace; without this, naming one component would have dropped every other one.

Every refusal names what is accepted, so a model corrects it in one step. Contract:
docs/Architecture/PLATFORM_OPERATIONS_ARCHITECTURE.md §18.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("quart.app")

SHAPED_OPS = ("profiles.create", "profiles.update")
SHAPED_FIELDS = ("knowledgeConfig", "ragCollections", "genieConfig", "skillsConfig",
                 "componentConfig", "contextWindowTypeId")

Err = Optional[str]


def _norm(s: Any) -> str:
    return str(s or "").strip().lstrip("@").strip().lower()


def _pick(kind: str, value: Any, items: List[Dict[str, Any]], *, id_key: str = "id",
          name_keys: Tuple[str, ...] = ("name",)) -> Tuple[Any, Err]:
    """One item of ``items`` by id or by name (case-insensitive) — or a sentence naming them."""
    v = value.get(id_key) if isinstance(value, dict) else value
    for it in items:
        if str(it.get(id_key)) == str(v).strip():
            return it.get(id_key), None
    hits = [it for it in items if any(_norm(it.get(k)) == _norm(v) and _norm(v) for k in name_keys)]
    if len(hits) == 1:
        return hits[0].get(id_key), None
    if len(hits) > 1:
        # PROP-011: two ingredients with the same name (two LLM configurations called "Gemini")
        # — silently taking the first built the agent on the wrong one; it is a question.
        from trusted_data_agent.agent import input_requests as _ir
        chosen = _ir.bound_answer(None, kind=kind, value=v)
        if chosen is not None and any(str(it.get(id_key)) == str(chosen) for it in hits):
            return chosen, None
        raise _ir.Ambiguous(kind, v, [{"id": it.get(id_key), "name": it.get(name_keys[0]),
                                      "detail": it.get("description")} for it in hits])
    named = ", ".join(f"{it.get(name_keys[0])} ({it.get(id_key)})" for it in items[:12]) or "none"
    return None, f"no {kind} '{v}' of yours — yours are: {named}"


# ── the ingredients the caller has (read on demand; each is the caller's own view) ──────

def _repositories(user_uuid: str, kind: str) -> List[Dict[str, Any]]:
    from trusted_data_agent.core.collection_db import get_collection_db
    return [c for c in (get_collection_db().get_all_collections(user_id=user_uuid) or [])
            if (c.get("repository_type") or "planner") == kind]


def _agents(user_uuid: str) -> List[Dict[str, Any]]:
    from trusted_data_agent.core.config_manager import get_config_manager
    return get_config_manager().get_profiles(user_uuid) or []


def _skills(user_uuid: str) -> List[Dict[str, Any]]:
    from trusted_data_agent.skills.manager import get_skill_manager
    from trusted_data_agent.skills.settings import is_skill_available
    return [s for s in get_skill_manager().list_skills() if is_skill_available(s["skill_id"])]


def _components(user_uuid: str) -> List[Dict[str, Any]]:
    from trusted_data_agent.components.manager import get_component_manager
    from trusted_data_agent.components.settings import is_component_available
    return [{"id": c.component_id, "name": c.display_name if hasattr(c, "display_name") else c.component_id}
            for c in get_component_manager().get_all_components()
            if is_component_available(c.component_id, user_uuid)]


def _cwts(user_uuid: str) -> List[Dict[str, Any]]:
    from trusted_data_agent.core.config_manager import get_config_manager
    return get_config_manager().get_context_window_types(user_uuid) or []


# ── one shaper per field: (sent, current, user, agent_id) → (shaped, error) ─────────────

def _knowledge(sent, current, user_uuid, agent_id):
    if not isinstance(sent, dict):
        return None, "'knowledgeConfig' must be an object, e.g. {\"collections\": [\"Uderia Documentation\"]}"
    out = {**(current or {}), **sent} if isinstance(current, dict) else dict(sent)
    if "collections" in sent:
        items = sent["collections"] if isinstance(sent["collections"], list) else [sent["collections"]]
        repos = _repositories(user_uuid, "knowledge")
        cols = []
        for it in items:
            cid, err = _pick("knowledge repository", it, repos)
            if err:
                return None, f"knowledgeConfig.collections: {err}"
            entry = {k: v for k, v in (it.items() if isinstance(it, dict) else []) if k != "id"}
            cols.append({"id": int(cid), **entry})
        out["collections"] = list({c["id"]: c for c in cols}.values())
        if "enabled" not in sent:
            out["enabled"] = bool(out["collections"])
    return out, None


def _planner(sent, current, user_uuid, agent_id):
    items = sent if isinstance(sent, list) else [sent]
    repos = _repositories(user_uuid, "planner")
    ids = []
    for it in items:
        cid, err = _pick("planner repository", it, repos)
        if err:
            return None, f"ragCollections: {err}"
        ids.append(int(cid))
    return list(dict.fromkeys(ids)), None


def _genie(sent, current, user_uuid, agent_id):
    if not isinstance(sent, dict):
        return None, "'genieConfig' must be an object, e.g. {\"slaveProfiles\": [\"@SALES\", \"@DOCS\"]}"
    out = {**(current or {}), **sent} if isinstance(current, dict) else dict(sent)
    if "slaveProfiles" in sent:
        items = sent["slaveProfiles"] if isinstance(sent["slaveProfiles"], list) else [sent["slaveProfiles"]]
        agents = [a for a in _agents(user_uuid) if a.get("id") != agent_id]
        ids = []
        for it in items:
            pid, err = _pick("agent", it, agents, name_keys=("tag", "name"))
            if err:
                return None, f"genieConfig.slaveProfiles: {err.replace('yours are', 'your agents are')}"
            ids.append(pid)
        out["slaveProfiles"] = list(dict.fromkeys(ids))
        settings = out.get("slaveProfileSettings")
        if isinstance(settings, dict):   # settings of an expert no longer consulted go with it
            out["slaveProfileSettings"] = {k: v for k, v in settings.items() if k in out["slaveProfiles"]}
    return out, None


def _skill_entry(it) -> Tuple[Optional[Dict[str, Any]], Err]:
    if isinstance(it, str):
        return {"id": it, "active": True}, None
    if isinstance(it, dict) and (it.get("id") or it.get("skill_id")):
        e = dict(it)
        e["id"] = e.pop("skill_id", None) or e["id"]
        return e, None
    return None, "each skill is an id or {\"id\": …, \"active\": true|false}"


def _skills_cfg(sent, current, user_uuid, agent_id):
    if isinstance(sent, list) and len(sent) == 1 and isinstance(sent[0], dict) and "skills" in sent[0]:
        sent = sent[0]   # the stored shape, wrapped in a list by the argument's array type
    items = sent.get("skills") if isinstance(sent, dict) else sent
    if not isinstance(items, list):
        return None, "'skillsConfig' is a list of skills, e.g. [\"chart-expert\"] or [{\"id\": \"chart-expert\", \"active\": false}]"
    known = {s["skill_id"]: s for s in _skills(user_uuid)}
    by_id = {e.get("id"): dict(e) for e in ((current or {}).get("skills") or []) if isinstance(e, dict)}
    order = list(by_id)
    for it in items:
        e, err = _skill_entry(it)
        if err:
            return None, f"skillsConfig: {err}"
        if e["id"] not in known:
            return None, (f"skillsConfig: no skill '{e['id']}' available to you — available: "
                          + ", ".join(sorted(known)[:30]))
        merged = {**by_id.get(e["id"], {"param": None}), **e}
        if "auto" not in e:          # named by the builder = the user's choice, not auto-managed
            merged["auto"] = False
        if merged.get("active") and "enabled" not in e:
            merged["enabled"] = True
        if e.get("enabled") is False and "active" not in e:
            merged["active"] = False
        merged.setdefault("enabled", bool(merged.get("active")))
        merged.setdefault("active", bool(merged.get("enabled")))
        by_id[e["id"]] = merged
        if e["id"] not in order:
            order.append(e["id"])
    out = {**(current or {})} if isinstance(current, dict) else {}
    if isinstance(sent, dict):
        out.update({k: v for k, v in sent.items() if k != "skills"})
    out["skills"] = [by_id[i] for i in order]
    return out, None


def _components_cfg(sent, current, user_uuid, agent_id):
    if not isinstance(sent, dict):
        return None, "'componentConfig' must be an object, e.g. {\"chart\": true, \"dashboard\": false}"
    known = {c["id"] for c in _components(user_uuid)}
    out = copy.deepcopy(current) if isinstance(current, dict) else {}
    for cid, val in sent.items():
        if cid not in known:
            return None, (f"componentConfig: no component '{cid}' available to you — available: "
                          + ", ".join(sorted(known)))
        if isinstance(val, bool):
            val = {"enabled": val}
        if not isinstance(val, dict):
            return None, f"componentConfig.{cid} must be true, false or an object like {{\"enabled\": true}}"
        prev = out.get(cid) if isinstance(out.get(cid), dict) else {}
        out[cid] = {**prev, **val}
    return out, None


def _cwt(sent, current, user_uuid, agent_id):
    cid, err = _pick("context window type", sent, _cwts(user_uuid))
    return (cid, None) if not err else (None, f"contextWindowTypeId: {err}")


_SHAPERS: Dict[str, Callable] = {
    "knowledgeConfig": _knowledge, "ragCollections": _planner, "genieConfig": _genie,
    "skillsConfig": _skills_cfg, "componentConfig": _components_cfg, "contextWindowTypeId": _cwt,
}


def shape(op_id: str, body: Dict[str, Any], user_uuid: Optional[str],
          current: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]], Err]:
    """``body`` with every composition field shaped (and, for an update, merged onto
    ``current`` — the agent as it is). Never raises: a failure is a sentence."""
    if op_id not in SHAPED_OPS or not any(f in body for f in SHAPED_FIELDS):
        return body, None
    if not user_uuid:
        return None, "no user for this call — the agent's ingredients cannot be resolved"
    if op_id == "profiles.update" and current is None:
        return None, "the agent to change was not found among yours"
    out = dict(body)
    merge = op_id == "profiles.update"
    agent_id = (current or {}).get("id")
    for f in SHAPED_FIELDS:
        if f not in body:
            continue
        try:
            v, err = _SHAPERS[f](body[f], (current or {}).get(f) if merge else None, user_uuid, agent_id)
        except Exception as e:  # noqa: BLE001 — an unreadable store is a refusal, never a crash
            logger.warning(f"[platform-shape] {op_id}.{f} could not be shaped: {e}")
            return None, f"{f} could not be checked against your ingredients ({type(e).__name__}) — nothing was submitted"
        if err:
            return None, err
        out[f] = v
    return out, None
