"""THE one rule for "is component X enabled for profile P".

Root cause this closes
----------------------
The rule had FOUR independent re-derivations that disagreed with each other
(``docs/assessments/COMPONENT_ENABLEMENT_ASSESSMENT_2026-08.md``):

  A  ``agent/recommender._component_enabled_for_profile``  (chips, companion skills)
  B  ``GET /v1/profiles/<id>/components`` -> ``profile_enabled``  (every panel)
  C  ``ComponentManager.get_active_components``  (the LLM tool surface — what RUNS)
  D  the editor / component manager / KG panel  (every toggle a human looks at)

C was blind to admin governance and defaulted an absent entry to True; D read a
*second* manifest key. Consequences proven live: an admin's global disable left the
tool bound, and ``TDA_Memory`` was bound on every shipped profile while every UI
reported memory off.

The chain (first decisive step wins)
------------------------------------
  1. global admin gate + per-user admin override  (``settings.is_component_available``)
  2. explicit ``profile.componentConfig[cid].enabled``
  3. manifest default for THIS profile type (``profile_defaults.enabled_for``)

``component_profile_defaults`` (the per-user "which components start on for a NEW profile"
setting) is deliberately NOT a resolution step. It is a CREATION default: it decides how
the editor pre-fills the toggles for a profile that does not exist yet, and once the
profile is saved every toggle is explicit. Folding it into the resolution of an EXISTING
profile would (a) make a stored profile's behaviour depend on a setting changed long
afterwards, and (b) put a per-user config read — which bootstraps, i.e. WRITES — on the
per-turn path. ``decide()`` still accepts ``user_defaults`` so the creation-default
endpoint can use the same function.

``enabled_for`` is the SINGLE manifest default. The former second key
``profile_defaults.default_enabled`` is retired — two keys for one fact is precisely
what let the editor and the runtime disagree.

Shape
-----
``decide()`` is PURE (every lookup already done by the caller) so the truth table is
unit-testable and the JS mirror can be asserted against it. ``is_enabled()`` /
``filter_enabled()`` are the impure wrappers that perform each lookup ONCE — never
once per component, which on the per-turn path would be a sqlite read per component
under the process-wide DB lock.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("quart.app")

DEFAULT_PROFILE_TYPE = "tool_enabled"


def profile_type_of(profile_config: Optional[Dict[str, Any]]) -> str:
    """The profile type the manifest default is evaluated against."""
    return (profile_config or {}).get("profile_type") or DEFAULT_PROFILE_TYPE


def manifest_default(comp: Any, profile_type: str) -> bool:
    """Step 3 — the manifest's per-profile-type default. The ONLY manifest key.

    ``enabled_for`` means "ELIGIBLE for these profile types", not "always offered": a
    handler's ``is_profile_relevant`` hook may narrow it further (``skill_library`` is
    eligible everywhere but offered only to a profile resolving an active progressive
    skill). Before the collapse, an absent componentConfig entry defaulted to True, so a
    relevance-gated component could leave ``enabled_for`` empty and still be offered —
    listing the types it supports is what keeps that behaviour once absent means "ask
    the manifest".
    """
    defaults = getattr(comp, "profile_defaults", None) or {}
    return profile_type in (defaults.get("enabled_for") or [])


def decide(
    comp: Any,
    profile_config: Optional[Dict[str, Any]],
    *,
    available: bool,
    user_defaults: Optional[Dict[str, Any]] = None,
    profile_type: Optional[str] = None,
    require_action: bool = False,
) -> bool:
    """PURE. Resolve the chain with every lookup supplied by the caller.

    Args:
        comp: a ``ComponentDefinition`` (needs ``component_id``, ``component_type``,
            ``profile_defaults``). ``None`` -> False (an absent component is not enabled).
        available: the result of step 1, already looked up.
        user_defaults: ``component_profile_defaults`` for this user, or None to skip step 3.
        require_action: when True a non-action component resolves False. Used by callers
            that gate *action* affordances only (chips, companion skills); the REST view
            and the runtime surface leave it False so system components resolve honestly.
    """
    if comp is None:
        return False
    if require_action and getattr(comp, "component_type", None) != "action":
        return False
    if not available:
        return False

    cid = getattr(comp, "component_id", None) or ""
    prof_cfg = ((profile_config or {}).get("componentConfig") or {}).get(cid)
    if isinstance(prof_cfg, dict) and "enabled" in prof_cfg:
        return bool(prof_cfg["enabled"])

    if user_defaults and cid in user_defaults:
        return bool(user_defaults[cid])

    return manifest_default(comp, profile_type or profile_type_of(profile_config))


# --- impure wrappers: each lookup performed exactly once ---------------------

def _availability(user_uuid: Optional[str]):
    """Return ``cid -> bool``. Fails OPEN (matches ``is_component_available``'s own
    contract): a settings-store failure must not silently strip every capability."""
    try:
        from trusted_data_agent.components.settings import is_component_available
        return lambda cid: is_component_available(cid, user_uuid)
    except Exception as e:  # pragma: no cover — import-time failure only
        logger.warning(f"[enablement] availability lookup unavailable, failing open: {e}")
        return lambda cid: True


def user_component_defaults(user_uuid: Optional[str]) -> Dict[str, Any]:
    """The per-user CREATION default (``component_profile_defaults``).

    Used only by the endpoints that answer "how should a NEW profile's toggles be
    pre-filled" — never on the per-turn path (``load_config`` bootstraps a row for an
    unknown user, so this is not a pure read).
    """
    if not user_uuid:
        return {}
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        cfg = get_config_manager().load_config(user_uuid) or {}
        defaults = cfg.get("component_profile_defaults")
        return defaults if isinstance(defaults, dict) else {}
    except Exception as e:
        logger.debug(f"[enablement] per-user component defaults unavailable: {e}")
        return {}


def is_auto(comp: Any, profile_config: Optional[Dict[str, Any]]) -> bool:
    """Whether the PLATFORM holds this component's switch for the agent — the resource
    vocabulary's "Auto" mark, one rule for components as for companion skills: an agent with no
    stored ``componentConfig[cid].enabled`` follows the manifest default (``enabled_for``) and
    the platform may move it in a release; a stored entry is the owner's (it stays stored — a
    save never strips it, so a no-op save keeps a signed agent's hash) and follows nothing.
    Auto is a mark, never a state: it says who holds the switch, not whether it is on."""
    cid = getattr(comp, "component_id", None) or ""
    prof_cfg = ((profile_config or {}).get("componentConfig") or {}).get(cid)
    return not (isinstance(prof_cfg, dict) and "enabled" in prof_cfg)


def acts_every_turn(comp: Any) -> bool:
    """The manifest's ``acts_every_turn`` — the component is in effect on every turn without
    being called (the vocabulary's "active"). A declaration of the component's nature, never a
    switch; an enabled component that declares it is active, an enabled one that does not acts
    only when called."""
    manifest = getattr(comp, "manifest", None) or {}
    return bool(manifest.get("acts_every_turn", False))


def relevant(comp: Any, profile_config: Optional[Dict[str, Any]]) -> bool:
    """Step 4 — the handler's relevance gate (PROP-005 ``is_profile_relevant``): a component
    that is ELIGIBLE by the manifest but INERT for this profile (no progressive skill for
    ``skill_library``; the compute-lineage switch off for ``compute``) is not enabled for it.
    Consulted by EVERY impure resolution — the runtime surface, the REST profile view, the
    next-step chips and the companion-skill auto-assign — so a component that would never
    be offered cannot pull its companion skill into the prompt (found by S3: with the
    switch off `compute-expert` would have auto-injected everywhere). A component without
    a handler, or a RAISING hook, reads relevant (fail toward the component's normal state);
    a component wanting inert-on-error returns False itself."""
    try:
        handler = getattr(comp, "handler", None)
        if handler is None or not hasattr(handler, "is_profile_relevant"):
            return True
        return bool(handler.is_profile_relevant(profile_config or {}))
    except Exception:  # noqa: BLE001
        return True


def offers_tool(comp: Any, profile_config: Optional[Dict[str, Any]]) -> bool:
    """Step 5, for ACTION affordances only — the handler's tool gate (``offers_tool``): is the
    component's LLM tool offered to this profile? The runtime tool surface reads it, and so
    does every ``require_action`` resolution (the companion-skill auto-assign, the next-step
    chips), so a companion skill follows the tool it teaches. A component without a handler,
    or a RAISING hook, reads True."""
    try:
        handler = getattr(comp, "handler", None)
        if handler is None or not hasattr(handler, "offers_tool"):
            return True
        return bool(handler.offers_tool(profile_config or {}))
    except Exception:  # noqa: BLE001
        return True


def is_enabled(
    comp: Any,
    profile_config: Optional[Dict[str, Any]],
    user_uuid: Optional[str] = None,
    *,
    profile_type: Optional[str] = None,
    require_action: bool = False,
) -> bool:
    """Single-component resolution. Prefer :func:`filter_enabled` for batches."""
    cid = getattr(comp, "component_id", None) or ""
    return decide(
        comp, profile_config,
        available=_availability(user_uuid)(cid),
        profile_type=profile_type,
        require_action=require_action,
    ) and relevant(comp, profile_config) and (not require_action or offers_tool(comp, profile_config))


def filter_enabled(
    comps: Iterable[Any],
    profile_config: Optional[Dict[str, Any]],
    user_uuid: Optional[str] = None,
    *,
    profile_type: Optional[str] = None,
    require_action: bool = False,
) -> List[Any]:
    """Batch resolution — settings + user defaults are read ONCE for the whole set."""
    avail = _availability(user_uuid)
    ptype = profile_type or profile_type_of(profile_config)
    out = []
    for comp in comps:
        cid = getattr(comp, "component_id", None) or ""
        if decide(comp, profile_config, available=avail(cid),
                  profile_type=ptype, require_action=require_action) and relevant(comp, profile_config) \
                and (not require_action or offers_tool(comp, profile_config)):
            out.append(comp)
    return out


# --- effect-site enforcement ------------------------------------------------

def enforce_component_call(
    comp: Any,
    profile_config: Optional[Dict[str, Any]],
    user_uuid: Optional[str] = None,
) -> Optional[str]:
    """Gate an actual component INVOCATION. Returns None to allow, else a reason.

    Why this exists on top of the advertise-time filter: a component tool call can
    arrive without ever having been advertised — a replayed RAG champion plan, an
    agent-pack-authored plan, a ``@TAG`` override resolving a different catalog,
    leaked-tool-call recovery, or a plain hallucination. Advertise-time gating alone
    means "disabled" holds only for well-behaved turns.

    Fails OPEN on an internal error: a resolver fault must not break every component
    call. The advertise-time filter remains the primary control; this is the backstop.
    """
    try:
        if is_enabled(comp, profile_config, user_uuid):
            return None
        cid = getattr(comp, "component_id", None) or "component"
        return (f"The '{cid}' component is not enabled for this agent profile. "
                f"Do not retry this call; answer without it, or ask the user to enable "
                f"'{cid}' for this profile in Anatomy -> Agent Profiles -> Edit -> Capabilities.")
    except Exception as e:  # pragma: no cover
        logger.warning(f"[enablement] effect-site check failed, allowing: {e}")
        return None
