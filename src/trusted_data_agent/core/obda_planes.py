"""The three OBDA planes as components — the shared facts their handlers need.

OBDA-READ (``obda_read``), OBDA-MODEL (``obda_model``) and OBDA-ACT (``action``) are builtin
components: each plane is AVAILABLE the way every component is — enabled globally in the
admin pane, granted or blocked per user, switched on per agent in the profile editor
(``components.enablement`` — the ONE chain). This module holds what the three handlers
share so none of them re-derives it:

* ``PLANES`` — plane name → component id (the ids are the stable keys a profile's
  ``componentConfig`` stores; the action component keeps its historic id).
* ``profile_has_graph(profile_id)`` — the dependency every OBDA plane stands on: a
  knowledge graph bound to the agent. A plane without a graph is structurally inert, so
  its handler's ``is_profile_relevant`` reads this (the ``skill_library`` idiom: eligible by
  the manifest, offered only where it can act).
* ``kg_handler()`` — the knowledge-graph component's handler, which holds the ontology
  engine the planes delegate to (a product is a node on the graph; a proposal lands on its
  authored layer). ONE implementation: the legacy ``TDA_KnowledgeGraph`` actions
  (``query_product`` … ``propose_entity``) stay routed to the same methods as aliases, so a
  stored champion plan keeps working byte for byte.

* ``plane_available(plane, user_uuid)`` / ``plane_enabled(plane, profile, user_uuid)`` —
  THE availability of a plane: the component chain (global admin gate · per-user override ·
  the agent's own toggle · relevance), never a master switch. Every face that used to ask
  ``APP_CONFIG.OBDA_ENABLED`` / ``ACT_ENABLED`` asks here, so a plane that is off for a
  deployment, a user or an agent is off on EVERY face at once (the REST catalog, the
  Designer, the tool, the enrichment, the scope parser, the scanner, the stations).

Pure stdlib + sqlite; never raises for a relevance decision (an unreadable binding reads
as *no graph* — inert, the additive baseline).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("quart.app")

#: plane → component id
PLANES: Dict[str, str] = {"read": "obda_read", "model": "obda_model", "act": "action"}

#: the agent-face tool of each plane (what the model calls)
PLANE_TOOLS: Dict[str, str] = {"read": "TDA_DataProduct", "model": "TDA_ModelProposal", "act": "TDA_Action"}

#: The knowledge-graph tool keeps the READ and MODEL actions as ALIASES (a stored champion
#: plan, a pack-authored plan). Every runtime seam that recognises a governed read — the
#: lane scanner, the governed_execution planner pass, the honesty primitives — reads these
#: sets, never a bare tool name, so the alias and the plane's own tool are one lane.
READ_TOOLS = frozenset({"TDA_DataProduct", "TDA_KnowledgeGraph"})
MODEL_TOOLS = frozenset({"TDA_ModelProposal", "TDA_KnowledgeGraph"})
READ_ACTIONS = frozenset({"list_products", "query_product"})
MODEL_ACTIONS = frozenset({"propose_entity", "propose_attribute"})


def is_read_call(tool_name, action) -> bool:
    """A data-product call through either face."""
    return str(tool_name or "") in READ_TOOLS and str(action or "") in READ_ACTIONS


def is_model_call(tool_name, action) -> bool:
    """A model proposal through either face."""
    return str(tool_name or "") in MODEL_TOOLS and str(action or "") in MODEL_ACTIONS

# A relevance decision runs on every tool-surface resolution; the binding is read once per
# profile per few seconds rather than once per component per turn.
_GRAPH_TTL_S = 5.0
_graph_cache: Dict[str, tuple] = {}


def _db_path() -> str:
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return str(getattr(APP_CONFIG, "AUTH_DB_PATH", "") or "tda_auth.db").replace("sqlite:///", "")
    except Exception:  # noqa: BLE001
        return "tda_auth.db"


def profile_has_graph(profile_id: Optional[str], user_uuid: Optional[str] = None) -> bool:
    """True when a knowledge graph is ACTIVE for this agent (``kg_profile_assignments``),
    or the agent owns a graph of its own (``kg_metadata``). The same resolution the
    knowledge-graph component uses for its tool reads (``_get_active_kg_owner``), without
    the user when the caller has none — a profile id is unique across users."""
    pid = str(profile_id or "").strip()
    if not pid:
        return False
    now = time.monotonic()
    hit = _graph_cache.get(pid)
    if hit and now - hit[0] < _GRAPH_TTL_S:
        return hit[1]
    found = False
    try:
        from trusted_data_agent.core import sqlite_util
        conn = sqlite_util.connect(_db_path())
        try:
            if user_uuid:
                row = conn.execute(
                    "SELECT 1 FROM kg_profile_assignments WHERE assigned_profile_id=? AND user_uuid=? "
                    "AND is_active=1 LIMIT 1", (pid, user_uuid)).fetchone()
            else:
                row = conn.execute(
                    "SELECT 1 FROM kg_profile_assignments WHERE assigned_profile_id=? AND is_active=1 LIMIT 1",
                    (pid,)).fetchone()
            if row is None:
                row = conn.execute("SELECT 1 FROM kg_metadata WHERE profile_id=? LIMIT 1", (pid,)).fetchone()
            found = row is not None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — unreadable binding reads as no graph
        logger.debug(f"[obda_planes] graph binding unreadable for {pid}: {exc}")
        found = False
    _graph_cache[pid] = (now, found)
    return found


def forget_graph_binding(profile_id: Optional[str] = None) -> None:
    """Drop the cached binding (a graph assigned or cleared mid-process)."""
    if profile_id:
        _graph_cache.pop(str(profile_id), None)
    else:
        _graph_cache.clear()


def kg_handler():
    """The knowledge-graph component's handler (the ontology engine the planes delegate to).
    Registered instance when the component manager holds one, else a fresh instance (the
    handler is stateless; the REST faces construct it the same way)."""
    try:
        from trusted_data_agent.components.manager import get_component_manager
        comp = get_component_manager().get_component("knowledge_graph")
        if comp is not None and getattr(comp, "handler", None) is not None:
            return comp.handler
    except Exception:  # noqa: BLE001
        pass
    from components.builtin.knowledge_graph.handler import KnowledgeGraphHandler
    return KnowledgeGraphHandler()


def restamp(payload: Any, *, component_id: str, tool_name: str) -> Any:
    """A payload the knowledge-graph engine produced, owned by the plane that asked for it:
    the row renders under the plane's component (its icon, its accent, its answer-surface
    word) and the model reads the plane's tool name in the metadata."""
    try:
        payload.component_id = component_id
        md = payload.metadata if isinstance(getattr(payload, "metadata", None), dict) else {}
        md = dict(md)
        md["tool_name"] = tool_name
        md.setdefault("via", "knowledge_graph")
        payload.metadata = md
    except Exception:  # noqa: BLE001
        pass
    return payload


# ── availability: the component chain, one reader per question ──────────────────────────

#: Tests force a plane's availability without touching the settings store — the ONLY
#: override, explicit and visible. ``None`` = not forced. Never set in production code.
_FORCED: Dict[str, Optional[bool]] = {"read": None, "model": None, "act": None}


def force(plane: str, value: Optional[bool]) -> None:
    """Test seam: force a plane available (True), unavailable (False) or unforced (None)."""
    if plane not in PLANES:
        raise KeyError(plane)
    _FORCED[plane] = value


def force_act(value: Optional[bool]) -> None:
    """Test seam: force the write half (OBDA-MODEL and OBDA-ACT together) — what the former
    single ``ACT_ENABLED`` switch covered."""
    force("model", value); force("act", value)


def plane_available(plane: str, user_uuid: Optional[str] = None) -> bool:
    """The plane exists for this deployment (and this user, when one is named): the
    component's global admin gate and the per-user override — ``settings.is_component_available``,
    the same rule every component answers to. Fails OPEN like that rule (a settings-store
    failure must not strip a capability)."""
    forced = _FORCED.get(plane)
    if forced is not None:
        return forced
    cid = PLANES[plane]
    try:
        # the three planes are enterprise components: a distribution that does not carry the
        # component has no plane (the public core) — nothing to gate, nothing to fail open on
        from trusted_data_agent.components.manager import get_component_manager
        if get_component_manager().get_component(cid) is None:
            return False
        from trusted_data_agent.components.settings import is_component_available
        return bool(is_component_available(cid, user_uuid))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[obda_planes] availability of {cid} unreadable, failing open: {exc}")
        return True


def plane_enabled(plane: str, profile: Optional[Dict[str, Any]], user_uuid: Optional[str] = None) -> bool:
    """The plane is on for THIS agent: the whole component chain (global · user · the
    profile's toggle · the manifest default · relevance — a graph bound). ``profile`` None
    means no agent is in play (a person, a REST caller): the deployment/user answer."""
    forced = _FORCED.get(plane)
    if forced is not None:
        return forced
    if not isinstance(profile, dict):
        return plane_available(plane, user_uuid)
    try:
        from trusted_data_agent.components.manager import get_component_manager
        from trusted_data_agent.components import enablement
        comp = get_component_manager().get_component(PLANES[plane])
        if comp is None:
            return False
        return bool(enablement.is_enabled(comp, profile, user_uuid))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[obda_planes] enablement of {PLANES[plane]} unreadable for this agent: {exc}")
        return False


def planes_for(profile: Optional[Dict[str, Any]], user_uuid: Optional[str] = None) -> Dict[str, bool]:
    """All three answers at once — what the scope parser drops a grant on."""
    return {k: plane_enabled(k, profile, user_uuid) for k in PLANES}


#: The refusal every face uses when a plane is off — one wording per plane, naming where it
#: is switched on (the admin pane for the deployment or the user, the agent's Capabilities).
_OFF = {
    "read": ("obda_read_off", "OBDA-READ is not enabled here — enable the OBDA-READ component "
                              "(Administration → Components, or this agent's Capabilities) to use data products."),
    "model": ("obda_model_off", "OBDA-MODEL is not enabled here — enable the OBDA-MODEL component "
                                "(Administration → Components, or this agent's Capabilities) to propose or publish concepts."),
    "act": ("obda_act_off", "OBDA-ACT is not enabled here — enable the OBDA-ACT component "
                            "(Administration → Components, or this agent's Capabilities) to define or perform actions."),
}


def off_refusal(plane: str) -> tuple:
    """``(error_code, message)`` for a plane that is off."""
    return _OFF[plane]


# ── the policy: "contracts only", one per plane ──────────────────────────────────────────
#: The policy ladder — the preflight shape (off · observe · enforce): a policy either records
#: what it would refuse or refuses; it never annotates an answer.
POLICY_LADDER = ("off", "observe", "enforce")

_FORCED_POLICY: Dict[str, Optional[str]] = {"read": None, "model": None, "act": None}


def force_policy(plane: str, mode: Optional[str]) -> None:
    """Test seam: force a plane's policy (``None`` = unforced)."""
    if plane not in PLANES:
        raise KeyError(plane)
    _FORCED_POLICY[plane] = mode


def normalize_policy(mapping: Any) -> Dict[str, str]:
    """A full, valid policy map from any input — the ONE normalizer the admin route, the
    startup loader and the reader share. Unknown modes read ``off`` (a config-integrity
    failure never escalates to enforce); unknown planes are dropped."""
    from trusted_data_agent.core.posture_modes import normalize as _norm
    src = mapping if isinstance(mapping, dict) else {}
    return {k: _norm(src.get(k, "off"), allowed=POLICY_LADDER, default="off", context=f"obda_policy.{k}")
            for k in PLANES}


def policy(plane: str) -> str:
    """The deployment's policy for a plane: ``off`` · ``observe`` · ``enforce``."""
    forced = _FORCED_POLICY.get(plane)
    if forced is not None:
        return forced
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return normalize_policy(getattr(APP_CONFIG, "OBDA_POLICY", None)).get(plane, "off")
    except Exception:  # noqa: BLE001 — an unreadable policy never enforces
        return "off"


def policy_map() -> Dict[str, str]:
    return {k: policy(k) for k in PLANES}


def policy_active() -> bool:
    """Any plane under observe or enforce — the policy then applies to EVERY turn, interactive
    ones included (a present human sees prose, not SQL)."""
    return any(m != "off" for m in policy_map().values())


def set_policy(mapping: Any) -> Dict[str, str]:
    """Apply a policy map to the live config (the admin route and the startup loader)."""
    from trusted_data_agent.core.config import APP_CONFIG
    APP_CONFIG.OBDA_POLICY = normalize_policy(mapping)
    return dict(APP_CONFIG.OBDA_POLICY)
