"""
Agent Authority — the Agent Principal's delegated, scoped authority (Tier 5 Step 4 / P3).

TERMINOLOGY (read this first). An agent's **identity** is its **Profile** — *who it is*: its
IFOC class, its LLM, its MCP "senses", its memory, its tools. This module is **not** identity.
It is the agent's **authority** — *what it is allowed to do, on whose behalf* — when it acts
autonomously. Authority is a strict, restrictable **subset** of the owning user's permissions.
Identity and authority are orthogonal axes (charter #8, *trust ≠ access*):

    Profile          → identity   (who the agent is)
    Trust Membrane   → assurance  (whether the agent is verified / attested)
    Agent Authority  → access     (what it may do autonomously, on whose behalf)  ← THIS MODULE
    Provenance chain → audit      (what it actually did, attributed to the agent)

Builds on the Trust Membrane (Tier 5.2): the **agent is a first-class governed principal**.
When an agent acts *autonomously* (a genie expert, a scheduled task, an MCP `ask_profile`
call, a flow node) it acts as an **Agent Principal derived from its owner** — never the raw
user token — and its authority is a **strict subset** of the owner's (charter #8, *trust ≠
access*). Every autonomous action is attributable to the agent in the provenance/audit chain.

**Scope of this module (honest):** this is the *attainable* form of P3 — a scoped principal
with capability restrictions + audit attribution + fail-closed enforcement. The *fuller*
vision (an agent confined to a graph sub-segment via ontology-mediated data access — "cannot
query outside its slice") is deferred: it depends on OBDA-READ, which does not yet exist. See
[TIER_5.2_TRUST_MEMBRANE.md §1.5](../../docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md).

This module is **pure & deterministic** — the model + the enforcement decision. It never
grants; a scope only ever *restricts* the owner's authority (subset invariant by construction).
Persistence lives on the profile's ``authorityConfig`` (legacy ``identityConfig`` still read);
the seams that build/stamp/enforce the principal live in the execution path.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger("quart.app")

# The canonical marker every surface (E1) denial reason carries. Declared ONCE:
# the E1 reason includes it, `denied_tool_result` keys the recovery-shaped tail on
# it, and the eval scorer + red-team probe match persisted traces by the same
# token — change it and every consumer of NEW records follows; old records keep
# the token they were written with.
SURFACE_DENIAL_MARKER = "surface-only"

# The write-surface twin (PROP-008 §5.4): an agent whose declared actions are its WHOLE
# write authority (`actions_exclusive`) is "action-only" — every raw write is refused,
# naming the actions it may perform instead. Declared once, like the surface marker.
ACTION_DENIAL_MARKER = "action-only"

# The turn "origins" (see execution_service) that mean the agent acted autonomously — i.e.
# a principal applies. An interactive user turn has none of these → no restriction (baseline).
AUTONOMOUS_ORIGINS = frozenset({"genie_parent", "genie_child", "flow", "scheduler", "mcp"})


def enforces_interactive(profile: Optional[dict]) -> bool:
    """Does this agent's authority apply to INTERACTIVE turns too? (opt-in)

    By default a principal is derived only for AUTONOMOUS_ORIGINS, on the reasoning
    that a present human is the control. That reasoning is thinner than it looks: what
    the human sees is prose, not SQL, and an operator who ticks "Surface-only
    (structural confinement)" reasonably reads it as confining — then watches the agent
    hand-write SQL over the very tables its product covers, because the switch quietly
    did not apply to the mode they were using.

    So the SCOPE is a declared choice rather than a hardcoded constant. Absent or false
    reproduces the previous behaviour exactly; true means "this agent's authority is its
    authority, whoever started the turn".

    Deliberately governs the WHOLE authorityConfig, not the data surface alone — a
    partially-applied principal would be a second, subtler notion of authority, and the
    confusion this exists to remove came from having two scopes in the first place.
    """
    try:
        # the deployment's OBDA policy (observe or enforce on any plane) applies to every
        # turn: a present human sees prose, not SQL — the very reason the opt-in exists
        from trusted_data_agent.core import obda_planes as _planes
        if _planes.policy_active():
            return True
        cfg = (profile or {}).get("authorityConfig")
        return bool(isinstance(cfg, dict) and cfg.get("enforce_interactive") is True)
    except Exception:  # noqa: BLE001
        return False

# Tools that mutate / write. `deny_writes` blocks these for an autonomous agent even when the
# owner could run them. Framework-internal tools (TDA_*) are never write-gated.
_WRITE_HINTS = ("write", "insert", "update", "delete", "drop", "create", "alter", "execute",
                "send", "post", "put", "modify", "remove", "grant", "revoke")
# Tools that run arbitrary commands (a shell, a script, a terminal): the platform cannot
# recognise what they write, so a "contracts only" write policy refuses them outright — a
# guarantee with a known hole is not one. Name-based like `_WRITE_HINTS`; `TDA_*` exempt.
_COMMAND_HINTS = ("shell", "bash", "terminal", "command", "exec", "run_script", "subprocess")


@dataclass(frozen=True)
class AgentScope:
    """The agent principal's authority — a *restriction* of the owner's. All defaults =
    unrestricted, so an agent with no authorityConfig behaves exactly as today (baseline)."""
    autonomous: bool = True                       # may this agent act autonomously at all?
    deny_writes: bool = False                     # block mutating/write tool actions
    denied_tools: FrozenSet[str] = frozenset()    # explicit tool deny-list
    allowed_connectors: Optional[FrozenSet[str]] = None   # None = all; else an allow-list
    max_cost_per_run: Optional[float] = None      # per-autonomous-run USD budget cap
    # ── the data surface (OBDA-READ Phase C, scope §7.1 E1–E4) ─────────────────────
    # None = no dataSurface declared (baseline). A product list WITHOUT exclusive is
    # additive capability (the raw lane stays available and monitored — E3); WITH
    # exclusive the agent's data authority IS the surface: raw SQL is denied
    # fail-closed and only signed-and-intact products are served. `advisory`
    # downgrades both to observations (the development mode E4 names).
    data_surface_products: Optional[FrozenSet[str]] = None
    data_surface_exclusive: bool = False
    data_surface_advisory: bool = False
    # ── OBDA-MODEL · OBDA-ACT (PROP-008 §7) ────────────────────────────────────────────────────
    # `model_authoring`: the agent may DRAFT on the ontology's authored layer
    # (OBDA-MODEL); publishing stays a person's act whatever this says (§13).
    # `actions_allowed`: the write surface — the action types the agent may PERFORM;
    # "the grant is the list" (the dataSurface precedent). None = no declared write is
    # admissible (baseline). `actions_exclusive`: the surface is the agent's whole write
    # authority. Both are dropped at parse time while the agent's OBDA-ACT / OBDA-MODEL plane is off, so every
    # consumer inherits the baseline from the one seam.
    model_authoring: bool = False
    actions_allowed: Optional[FrozenSet[str]] = None
    actions_exclusive: bool = False
    # ── the deployment's OBDA POLICY in force on this scope (core/obda_planes.policy) ─────
    # "contracts only", per plane: `enforce` is applied INTO the fields above by
    # `apply_policy` (an exclusive surface, an exclusive write surface) and is a restriction;
    # `observe` leaves the fields alone and the chokepoint records what enforce would refuse.
    # All `off` = the baseline: the stamp carries no policy key at all.
    policy_read: str = "off"
    policy_model: str = "off"
    policy_act: str = "off"

    def to_dict(self) -> dict:
        d = {
            "autonomous": self.autonomous,
            "deny_writes": self.deny_writes,
            "denied_tools": sorted(self.denied_tools),
            "allowed_connectors": (sorted(self.allowed_connectors)
                                   if self.allowed_connectors is not None else None),
            "max_cost_per_run": self.max_cost_per_run,
            "data_surface": (None if self.data_surface_products is None else {
                "products": sorted(self.data_surface_products),
                "exclusive": self.data_surface_exclusive,
                "advisory": self.data_surface_advisory,
            }),
        }
        # The OBDA-MODEL / OBDA-ACT grants are emitted ONLY when set: the sealed authority stamp of a turn
        # that carries neither must stay byte-identical to the pre-OBDA-MODEL/OBDA-ACT shape (PROP-008
        # increment 0 — the baseline is a hash, not a mood).
        if self.model_authoring:
            d["model_authoring"] = True
        if self.actions_allowed is not None:
            d["actions"] = {"types": sorted(self.actions_allowed),
                            "exclusive": self.actions_exclusive}
        # the policy rides the stamp only when a plane is under it — the baseline stamp of a
        # deployment with every policy off stays byte-identical
        pol = {k: v for k, v in (("read", self.policy_read), ("model", self.policy_model),
                                 ("act", self.policy_act)) if v != "off"}
        if pol:
            d["policy"] = pol
        return d

    @property
    def is_restricted(self) -> bool:
        # A product list alone is additive capability, not a restriction; only
        # EXCLUSIVE (surface-only) narrows what the agent can reach.
        # An action list alone is additive write capability (through the action face
        # only); EXCLUSIVE narrows what the agent may write — a restriction, as the
        # exclusive data surface is.
        return (not self.autonomous or self.deny_writes or bool(self.denied_tools)
                or self.allowed_connectors is not None or self.max_cost_per_run is not None
                or (self.data_surface_exclusive and not self.data_surface_advisory)
                or (self.actions_allowed is not None and self.actions_exclusive)
                # an enforced policy narrows what the agent may reach, whatever it configured
                or self.policy_read == "enforce" or self.policy_act == "enforce"
                or self.policy_model == "enforce")


@dataclass(frozen=True)
class AgentPrincipal:
    """A distinct identity for an agent acting autonomously, derived from its owner."""
    owner_uuid: str
    profile_id: str
    scope: AgentScope = field(default_factory=AgentScope)

    @property
    def principal_id(self) -> str:
        return f"agent:{self.profile_id}"

    def actor(self) -> dict:
        """The audit/provenance actor stamp — attributes an action to the agent, not the user."""
        return {"type": "agent", "principal_id": self.principal_id,
                "owner_uuid": self.owner_uuid, "profile_id": self.profile_id}


def user_actor(user_uuid: str) -> dict:
    """The actor stamp for a user-initiated (interactive) action."""
    return {"type": "user", "principal_id": f"user:{user_uuid}", "owner_uuid": user_uuid}


def parse_scope(authority_config: Optional[Dict[str, Any]], planes: Optional[Dict[str, bool]] = None) -> AgentScope:
    """Build an AgentScope from a profile's ``authorityConfig`` (absent/empty → baseline)."""
    c = authority_config or {}
    conns = c.get("allowed_connectors")
    cost = c.get("max_cost_per_run")
    # NB: don't use `cost not in (None, "", False)` — in Python `0 == False`, so a real $0 cap
    # would be silently dropped. Only None / "" / False mean "no cap"; 0 is a valid (deny-all) cap.
    if cost is None or cost == "" or cost is False:
        cost = None
    else:
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            cost = None
    ds = c.get("dataSurface")
    ds = ds if isinstance(ds, dict) else {}
    # OBDA-READ IS ADDITIVE (charter #15, operator-restated as a design imperative):
    # with the master switch OFF the platform must behave as if OBDA-READ never
    # shipped. The data surface is OBDA-READ machinery, so it is dropped HERE — the
    # ONE parse seam — and every consumer inherits: the tool chokepoints stop
    # denying, the enrichment stops cutting to contracts (_surface_for_turn),
    # the champion filter stops filtering, the catalog stops marking. Gating at
    # each consumer instead is how one of them gets missed (the enrichment and
    # the agent face WERE missed — found live, session d51cf073).
    # `planes` — which OBDA planes are ON for this agent (core/obda_planes.planes_for: the
    # component chain). None = unknown (a pure parse of a stored config): grants are kept.
    if planes is not None and not planes.get("read", True):
        ds = {}
    ds_products = ds.get("products")
    # OBDA-MODEL · OBDA-ACT ARE ADDITIVE (PROP-008 §7, the same rule as OBDA-READ above): with ACT_ENABLED off
    # the two grants are dropped HERE, the one parse seam, so the authoring seam, the
    # action chokepoint, the subscribed-graph write rule and every reading inherit the
    # baseline without a gate of their own.
    authoring = bool(c.get("modelAuthoring", False))
    acts = c.get("actions")
    acts = acts if isinstance(acts, dict) else {}
    if planes is not None and not planes.get("model", True):
        authoring = False
    if planes is not None and not planes.get("act", True):
        acts = {}
    act_types = acts.get("types")
    return AgentScope(
        autonomous=c.get("autonomous", True) is not False,
        deny_writes=bool(c.get("deny_writes", False)),
        denied_tools=frozenset(c.get("denied_tools") or []),
        allowed_connectors=(frozenset(conns) if isinstance(conns, (list, tuple, set)) else None),
        max_cost_per_run=cost,
        data_surface_products=(frozenset(str(p) for p in ds_products)
                               if isinstance(ds_products, (list, tuple, set)) else None),
        data_surface_exclusive=bool(ds.get("exclusive", False)),
        data_surface_advisory=bool(ds.get("advisory", False)),
        model_authoring=authoring,
        actions_allowed=(frozenset(str(a) for a in act_types)
                         if isinstance(act_types, (list, tuple, set)) else None),
        actions_exclusive=bool(acts.get("exclusive", False)),
    )


def action_approvers(authority_config: Optional[Dict[str, Any]]) -> List[str]:
    """The accounts that approve this agent's PARKED writes (four eyes, PROP-008 §5.4a):
    ``authorityConfig.actions.approvers`` — account usernames, as written. Empty = the
    agent's owner approves (the single-account baseline). Pure; OBDA-MODEL · OBDA-ACT off ⇒ [] (no write is
    ever parked then). Usernames, not ids, so the setting reads in the editor as written."""
    from trusted_data_agent.core import obda_planes as _planes
    if not _planes.plane_available("act"):
        return []
    acts = (authority_config or {}).get("actions")
    raw = acts.get("approvers") if isinstance(acts, dict) else None
    if isinstance(raw, str):
        raw = [x for x in raw.replace(";", ",").split(",")]
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for x in raw:
        n = str(x or "").strip().lstrip("@")
        if n and n.lower() not in (o.lower() for o in out):
            out.append(n)
    return out


def scope_from_stamp(stamp: Optional[Dict[str, Any]]) -> AgentScope:
    """The exact inverse of ``AgentScope.to_dict`` — reads a SEALED stamp back.

    Deliberately NOT ``parse_scope``: that reads a profile's ``authorityConfig``, whose
    keys differ (``dataSurface`` vs the serialized ``data_surface``), so round-tripping a
    stamp through it would silently drop the data surface — the one constraint that most
    narrows an agent. It also re-reads the OBDA-READ plane's availability at call time, which would grade a
    historical turn against today's master switch; a stamp already records what was in
    force, and re-resolving it is the retroactive-falsification trap sealing exists to
    avoid. Lives beside ``to_dict`` so the pair cannot drift.
    """
    c = stamp or {}
    conns = c.get("allowed_connectors")
    cost = c.get("max_cost_per_run")
    if cost is None or cost == "" or cost is False:
        cost = None
    else:
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            cost = None
    ds = c.get("data_surface")
    ds = ds if isinstance(ds, dict) else {}
    products = ds.get("products")
    acts = c.get("actions")
    acts = acts if isinstance(acts, dict) else {}
    act_types = acts.get("types")
    return AgentScope(
        autonomous=c.get("autonomous", True) is not False,
        deny_writes=bool(c.get("deny_writes", False)),
        denied_tools=frozenset(c.get("denied_tools") or []),
        allowed_connectors=(frozenset(conns) if isinstance(conns, (list, tuple, set)) else None),
        max_cost_per_run=cost,
        data_surface_products=(frozenset(str(p) for p in products)
                               if isinstance(products, (list, tuple, set)) else None),
        data_surface_exclusive=bool(ds.get("exclusive", False)),
        data_surface_advisory=bool(ds.get("advisory", False)),
        # Read back exactly as `to_dict` emits — absent means the grant was never in force.
        model_authoring=bool(c.get("model_authoring", False)),
        actions_allowed=(frozenset(str(a) for a in act_types)
                         if isinstance(act_types, (list, tuple, set)) else None),
        actions_exclusive=bool(acts.get("exclusive", False)),
    )


def scope_for_profile(profile: Optional[Dict[str, Any]], user_uuid: Optional[str] = None) -> AgentScope:
    """The agent scope a profile actually runs under, honouring the legacy key.

    The one place that resolves ``authorityConfig`` (falling back to the pre-rename
    ``identityConfig``) into a scope, so a reader can never disagree with the enforcer
    about what a profile's authority is.
    """
    profile = profile or {}
    cfg = profile.get("authorityConfig")
    if cfg is None:
        cfg = profile.get("identityConfig")  # backward-compat: profiles saved before the rename
    from trusted_data_agent.core import obda_planes as _planes
    return parse_scope(cfg, planes=_planes.planes_for(profile, user_uuid) if profile else None)


def describe_scope(scope: AgentScope) -> List[str]:
    """Human-readable constraints, in the order an operator should read them.

    Empty ⇔ ``not scope.is_restricted`` — i.e. the agent may act autonomously with the
    owner's full authority. Lives with the model rather than in a view so that every
    surface (ring, posture matrix, editor, audit) describes a scope identically, and so
    the description can never drift from ``is_restricted``.
    """
    out: List[str] = []
    if not scope.autonomous:
        out.append("no unattended use")
    if scope.deny_writes:
        out.append("read-only")
    if scope.denied_tools:
        n = len(scope.denied_tools)
        out.append(f"{n} tool{'s' if n != 1 else ''} denied")
    if scope.allowed_connectors is not None:
        n = len(scope.allowed_connectors)
        out.append("no connectors" if n == 0 else f"{n} connector{'s' if n != 1 else ''} allowed")
    if scope.max_cost_per_run is not None:
        out.append(f"${scope.max_cost_per_run:g}/run cap")
    if scope.data_surface_exclusive and not scope.data_surface_advisory:
        n = len(scope.data_surface_products or ())
        out.append(f"surface-only: {n} data product{'s' if n != 1 else ''}")
    # The OBDA-ACT write surface is a restriction only when exclusive (mirrors the data
    # surface) — keeps "empty ⇔ not is_restricted" true.
    if scope.actions_allowed is not None and scope.actions_exclusive:
        n = len(scope.actions_allowed)
        out.append(f"write surface only: {n} action type{'s' if n != 1 else ''}")
    # the deployment's OBDA policy, named as a constraint only when it ENFORCES (observe
    # restricts nothing — it records)
    _pol = [w for w, m in (("reads", scope.policy_read), ("model changes", scope.policy_model),
                           ("writes", scope.policy_act)) if m == "enforce"]
    if _pol:
        out.append(f"deployment policy: {', '.join(_pol)} contracts-only")
    return out


def derive_principal(profile: Optional[Dict[str, Any]], owner_uuid: str,
                     profile_id: Optional[str] = None) -> AgentPrincipal:
    """Derive the agent principal for a profile from its ``authorityConfig``
    (falls back to the legacy ``identityConfig`` key for backward compatibility)."""
    profile = profile or {}
    pid = profile_id or profile.get("id") or "unknown"
    cfg = profile.get("authorityConfig")
    if cfg is None:
        cfg = profile.get("identityConfig")  # backward-compat: profiles saved before the rename
    from trusted_data_agent.core import obda_planes as _planes
    scope = parse_scope(cfg, planes=_planes.planes_for(profile, owner_uuid))
    # the deployment's OBDA policy, applied ONCE here, inherited by every consumer of the scope
    return AgentPrincipal(owner_uuid=owner_uuid, profile_id=str(pid),
                          scope=apply_policy(scope, _planes.policy_map()))


def _looks_like_write(tool_name: str) -> bool:
    t = (tool_name or "").lower()
    if t.startswith("tda_"):   # framework-internal (final report, logging, orchestration)
        return False
    return any(h in t for h in _WRITE_HINTS)


def _looks_like_command(tool_name: str) -> bool:
    t = (tool_name or "").lower()
    if t.startswith("tda_"):
        return False
    return any(h in t for h in _COMMAND_HINTS)


POLICY_DENIAL_MARKER = "contracts-only"


def apply_policy(scope: AgentScope, policy: Optional[Dict[str, str]]) -> AgentScope:
    """THE one application of the deployment's OBDA policy onto an agent's scope (pure).

    ``enforce`` on a plane is written INTO the scope's own fields, so every consumer of the
    scope — the three chokepoints, the tool surface, the enrichment cut, the champion filter,
    the stations — inherits it without a gate of its own:
      * read  → an EXCLUSIVE data surface over the agent's granted products (none granted ⇒ an
                empty surface: raw SQL refused, nothing readable but through a product);
      * act   → an EXCLUSIVE write surface over the agent's granted actions (none ⇒ every raw
                write refused), and command tools refused (`_looks_like_command`);
      * model → recorded on the scope; the knowledge-graph component refuses its direct edits
                for an agent under it (the graph changes through proposals a person publishes).
    ``observe`` and ``off`` change no field; the mode is recorded so the chokepoint can tell
    what enforce WOULD have refused (observe) — the pre-enforcement rehearsal.
    """
    pol = policy or {}
    read = str(pol.get("read", "off")); model = str(pol.get("model", "off")); act = str(pol.get("act", "off"))
    if read == "off" and model == "off" and act == "off":
        return scope
    from dataclasses import replace as _replace
    out = _replace(scope, policy_read=read, policy_model=model, policy_act=act)
    if read == "enforce":
        out = _replace(out, data_surface_products=scope.data_surface_products or frozenset(),
                       data_surface_exclusive=True, data_surface_advisory=False)
    if act == "enforce":
        out = _replace(out, actions_allowed=scope.actions_allowed or frozenset(),
                       actions_exclusive=True)
    return out


def _as_if_enforced(scope: AgentScope) -> Optional[AgentScope]:
    """The scope as `enforce` would have it, for every plane now under `observe` — None when
    no plane is observed."""
    obs = {k: "enforce" for k, v in (("read", scope.policy_read), ("model", scope.policy_model),
                                      ("act", scope.policy_act)) if v == "observe"}
    if not obs:
        return None
    pol = {"read": scope.policy_read, "model": scope.policy_model, "act": scope.policy_act}
    pol.update(obs)
    base = AgentScope(**{**scope.__dict__, "policy_read": "off", "policy_model": "off", "policy_act": "off"})
    return apply_policy(base, pol)


# ── the governed-SQL registry (turn-scoped, contextvars — E1's one exception) ─
#
# Under the compile-never-execute doctrine (the probe_bridge precedent) a
# surface-only agent still has to RUN the statement query_product compiled — through
# its own governed data tool, where this very chokepoint sits. So the ONLY raw SQL a
# surface-only agent may execute is a statement the surface itself produced this
# turn: query_product registers its compiled output here, and E1 permits exactly
# those statements (whitespace/semicolon-normalized — the SAME matching rule the
# OBDA-READ lane scanner uses, so enforcement and observability can never disagree about
# what "executing the contract" means). Reset with the principal at each turn
# boundary; interactive turns carry no principal, so the registry is inert there.
#: A MUTABLE set created at the turn boundary and MUTATED, never re-``.set()``:
#: contextvars propagate parent→child only, so a ``.set()`` inside a child task (a
#: component handler, a parallel phase) would be invisible to the SIBLING task that
#: later executes the statement. Sharing one set object by reference makes every
#: task in the turn see every registration — the gate caught exactly this.
_GOVERNED_SQL: "contextvars.ContextVar[Optional[set]]" = \
    contextvars.ContextVar("agent_governed_sql", default=None)


def _norm_sql(sql: Any) -> str:
    return " ".join(str(sql or "").split()).rstrip(";")


def register_governed_sql(sql: str) -> None:
    """Called by the product surface when it compiles a statement — the statement
    becomes executable for THIS turn's surface-only principal. Fail-open by shape
    (an empty statement registers nothing). MUTATES the turn's shared set; the
    ``.set()`` below is only the lazy-create fallback for principal-less contexts
    (where enforcement is off anyway)."""
    n = _norm_sql(sql)
    if not n:
        return
    cur = _GOVERNED_SQL.get()
    if cur is None:
        cur = set()
        _GOVERNED_SQL.set(cur)
    cur.add(n)


def is_registered_governed_sql(sql: Any) -> bool:
    n = _norm_sql(sql)
    return bool(n) and n in (_GOVERNED_SQL.get() or ())


def _carries_raw_sql(arguments: Optional[Dict[str, Any]]) -> bool:
    """E1's classifier — THE one the OBDA-READ lane scanner uses (a non-empty ``sql``
    argument), imported lazily so the definition never forks."""
    try:
        from trusted_data_agent.agent import obda as _obda
        return _obda._is_raw_sql(arguments)
    except Exception:  # noqa: BLE001 — classifier failure must not fail OPEN a rule…
        args = arguments or {}                  # …so fall back to the same truth
        sql = args.get("sql")
        coll = args.get("collection")
        return ((isinstance(sql, str) and sql.strip() != "")
                or (isinstance(coll, str) and coll.strip() != "" and args.get("pipeline") is not None))


def _is_derived_sql(tool_name: str, arguments: Optional[Dict[str, Any]]) -> bool:
    """The ONE exception to E1 that is not a governed statement: the combine step. Its SQL runs
    in a sandboxed engine over rows this turn's queries already returned (each of which passed
    this very rule) — it reaches no data source, so it cannot be a surface escape. ONLY the
    surface rule is waived: ``denied_tools`` and every other check still apply. Fails CLOSED."""
    try:
        from trusted_data_agent.agent.obda import is_derived_call
        return is_derived_call(tool_name, arguments)
    except Exception:  # noqa: BLE001
        return False


def is_tool_allowed(scope: AgentScope, tool_name: str, *, is_connector: bool = False,
                    is_write: Optional[bool] = None,
                    aliases: Optional[FrozenSet[str]] = None,
                    arguments: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """The enforcement decision for one tool/action under an agent scope. Pure. Returns
    ``(allowed, reason)``. Fail-closed callers deny on ``allowed is False``.

    ``aliases`` lets a tool be denied under more than one name — a component tool is denyable by
    EITHER its ``TDA_*`` tool name OR its component id, so ``denied_tools`` matches whichever the
    admin wrote and both execution paths (MCP adapter, LangChain) enforce identically.

    ``arguments`` feeds the data-surface rule (E1): for a SURFACE-ONLY agent, any call
    carrying raw SQL is denied — its data authority IS the set of signed products on its
    surface, and the denial names the correction (``query_product``) so the LLM
    self-corrects in one step. Deliberately NO ``TDA_`` exemption here: a component that
    executes SQL (canvas) is the same escape hatch by another name."""
    if not scope.autonomous:
        return False, "this agent is not permitted to act autonomously"
    names = {tool_name} | set(aliases or ())
    denied_hit = names & scope.denied_tools
    if denied_hit:
        return False, f"'{sorted(denied_hit)[0]}' is denied to this agent"
    if is_connector and scope.allowed_connectors is not None and tool_name not in scope.allowed_connectors:
        return False, f"connector '{tool_name}' is not in this agent's allow-list"
    if (scope.data_surface_exclusive and not scope.data_surface_advisory
            and _carries_raw_sql(arguments) and not _is_derived_sql(tool_name, arguments)
            and not is_registered_governed_sql((arguments or {}).get("sql"))):
        products = ", ".join(sorted(scope.data_surface_products or ())) or "(none declared)"
        return False, (f"this agent is {SURFACE_DENIAL_MARKER} — raw SQL is denied; use "
                       f"TDA_DataProduct action 'query_product' over its signed "
                       f"data products ({products}) and execute the returned "
                       f"statement exactly as given")
    write = _looks_like_write(tool_name) if is_write is None else bool(is_write)
    # The deployment's write policy ("contracts only", enforce): a tool that runs arbitrary
    # commands could write what no classifier sees — refused, naming the policy.
    if scope.policy_act == "enforce" and _looks_like_command(tool_name):
        return False, (f"this agent is {ACTION_DENIAL_MARKER} under the deployment's {POLICY_DENIAL_MARKER} "
                       f"write policy — '{tool_name}' runs commands whose writes cannot be recognised")
    # The write surface (PROP-008 §5.4): with `actions_exclusive` the declared action types
    # are the agent's WHOLE write authority. A raw write never reaches a table here — an
    # action is performed by the action face, in one transaction with its checks, never
    # through a data tool — so every raw write is refused, naming what IS admissible.
    if write and scope.actions_allowed is not None and scope.actions_exclusive:
        acts = ", ".join(sorted(scope.actions_allowed)) or "(none declared)"
        return False, (f"this agent is {ACTION_DENIAL_MARKER} — a raw write is refused; it "
                       f"may change data only by performing one of its signed action types "
                       f"({acts}) through the action face")
    if scope.deny_writes and write:
        return False, f"this agent may not perform write actions ('{tool_name}')"
    return True, "allowed"


def check_budget_before_call(cost_so_far: float) -> Tuple[bool, str]:
    """The IN-FLIGHT half of the budget cap — asked before an LLM call is paid for.

    `max_cost_per_run` was declared, stored, surfaced in the editor, and enforced only
    POST-HOC: the overrun was flagged once the money was spent, which is an audit record
    wearing a control's name (symmetry map Part D).

    Deliberately the SAME predicate as the post-hoc check rather than a second threshold
    comparison — a cap that could be read two ways is a cap nobody can reason about.

    Applies to AUTONOMOUS turns only, because that is what the scope governs: on an
    interactive turn the user's own permissions apply, and cutting a user off mid-answer
    is not what a per-run budget was for. No ambient principal → no cap → today's
    behaviour.
    """
    principal = get_current_principal()
    if principal is None:
        return True, "no agent principal — interactive turn"
    return is_run_within_budget(principal.scope, cost_so_far)


def is_run_within_budget(scope: AgentScope, cost_so_far: float) -> Tuple[bool, str]:
    """Per-run budget check. Shared by the in-flight guard and the post-hoc record."""
    if scope.max_cost_per_run is None:
        return True, "no cap"
    if cost_so_far > scope.max_cost_per_run:
        return False, f"autonomous run exceeded its ${scope.max_cost_per_run:.4f} budget"
    return True, "within budget"


# ── per-turn security context (contextvars — async-task-local, non-racy) ──────
# The principal is set once at the autonomous-turn boundary and read at every tool
# execution chokepoint. contextvars propagate into awaited coroutines + child tasks
# (asyncio copies the context), so concurrent sessions stay isolated and no signature
# plumbing is needed. An interactive user turn leaves this unset → baseline (no gating).

_CURRENT_PRINCIPAL: "contextvars.ContextVar[Optional[AgentPrincipal]]" = \
    contextvars.ContextVar("agent_principal", default=None)


def set_current_principal(principal: Optional[AgentPrincipal]):
    """Set the ambient agent principal for the current async context. Returns a reset token."""
    # A FRESH MUTABLE set per turn (shared by reference into every child task) — the
    # governed-SQL registry shares the principal's lifecycle.
    _GOVERNED_SQL.set(set())
    return _CURRENT_PRINCIPAL.set(principal)


def get_current_principal() -> Optional[AgentPrincipal]:
    return _CURRENT_PRINCIPAL.get()


def reset_current_principal(token) -> None:
    try:
        _CURRENT_PRINCIPAL.reset(token)
    except Exception:
        pass


def enforce_tool(tool_name: str, *, is_connector: bool = False,
                 is_write: Optional[bool] = None,
                 aliases: Optional[FrozenSet[str]] = None,
                 arguments: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """**Fail-closed** enforcement at a tool execution chokepoint, against the ambient
    principal. No principal (interactive user turn) → allowed (baseline). Any error → **deny**.
    ``aliases`` (see ``is_tool_allowed``) lets a component tool be denied by tool name or id;
    ``arguments`` feeds the data-surface rule (E1 — raw SQL under a surface-only scope).
    """
    try:
        p = get_current_principal()
        if p is None:
            return True, "no principal"
        ok, why = is_tool_allowed(p.scope, tool_name, is_connector=is_connector,
                                  is_write=is_write, aliases=aliases, arguments=arguments)
        if ok:
            # OBDA policy under OBSERVE: nothing refused — what enforce WOULD refuse is recorded
            # (the pre-enforcement rehearsal: which agents would break, before anything breaks)
            as_if = _as_if_enforced(p.scope)
            if as_if is not None:
                ok2, why2 = is_tool_allowed(as_if, tool_name, is_connector=is_connector,
                                            is_write=is_write, aliases=aliases, arguments=arguments)
                if not ok2:
                    record_policy_observation(p, tool_name, why2,
                                              is_write=bool(is_write) or _looks_like_write(tool_name)
                                              or _looks_like_command(tool_name))
        return ok, why
    except Exception as e:  # fail closed (charter #8)
        logger.warning(f"agent_authority: enforcement error for '{tool_name}', denying: {e}")
        return False, "enforcement error — denied"


def record_policy_observation(principal: AgentPrincipal, tool_name: str, would_refuse: str,
                              *, is_write: bool = False) -> None:
    """A `policy_observed` trust event — the OBDA policy under `observe` saw a call that
    `enforce` would refuse. The plane is the WRITE plane for any write or command tool (a
    write is a write, whichever rule refused it first), the READ plane otherwise. Fail-open."""
    try:
        from trusted_data_agent.core import trust_events as _te
        plane = "act" if is_write else ("read" if SURFACE_DENIAL_MARKER in would_refuse else "act")
        summary = (f"POLICY OBSERVED: {principal.principal_id} called '{tool_name}' — the deployment's "
                   f"contracts-only {plane} policy would refuse it under enforce ({would_refuse})")
        _te.record_event(principal.owner_uuid, "policy_observed", summary, severity="warning",
                         payload={"tool": tool_name, "plane": plane, "profile_id": principal.profile_id,
                                  "would_refuse": would_refuse})
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"policy observation not recorded: {exc}")


def denied_tool_result(tool_name: str, reason: str) -> dict:
    """A consistent, non-retryable denial result every chokepoint returns. The content reads
    as a definitive tool outcome so the LLM incorporates it (no self-correction loop); the
    ``agent_denied`` metadata flag makes it auditable.

    A CATEGORICAL denial (denied tool, deny_writes) ends with "report this limitation" —
    there is nothing the agent can do. The SURFACE denial is different: it HAS a correction
    path, and the live C4 acceptance proved the old blanket "Do not retry; report this
    limitation" made the model obey the stop clause over the fix named two sentences
    earlier — three identical retries, then a give-up, with query_product never called.
    A denial with a correction path must be RECOVERY-SHAPED (the loop-guard precedent):
    the next step is spelled out as an instruction, not an aside inside the refusal. The
    WRITE surface (``action-only``) has one too: a signed action, named conditionally since
    the action component may be off for the agent."""
    if SURFACE_DENIAL_MARKER in (reason or ""):
        tail = (f"NEXT STEP (do this now, do not resubmit hand-written SQL): call "
                f"TDA_DataProduct with action='query_product' naming one of this "
                f"agent's signed data products, then execute the RETURNED statement "
                f"verbatim with this same tool — that statement is permitted.")
    elif ACTION_DENIAL_MARKER in (reason or ""):
        # the write surface has a correction path too — a signed action, prepared for the
        # person's confirmation. The action component may be off for this agent, so the tool
        # is named conditionally: never an instruction to call a tool that is not offered.
        tail = (f"NEXT STEP (do not resubmit a raw write): change the data through one of the "
                f"signed actions named above — with the TDA_Action tool (action='perform', "
                f"action_type, key, values) if it is offered to you; it prepares the change "
                f"for the user to confirm. If TDA_Action is not offered, tell the user which "
                f"signed action would make this change.")
    else:
        tail = "Do not retry; report this limitation."
    return {
        "status": "error",
        "error": f"agent_policy_denied: {reason}",
        "metadata": {"tool_name": tool_name, "agent_denied": True, "policy_reason": reason,
                     "terminal": True},
        "results": [{"agent_policy": f"DENIED — this action is outside this agent's permitted "
                                     f"scope ({reason}). {tail}"}],
    }


# ── the turn's authority stamp ────────────────────────────────────────────────
# The scope IN FORCE on a turn, sealed at execution time (symmetry map A17). It was
# already built for the returned payload, but the payload is not the turn: it never
# reached `workflow_history`, so the BOUNDED reading re-derived autonomy from a
# `turn["origin"]` key that no engine writes — and reported "acting as the user" on
# EVERY autonomous turn (0 of 1540 production turns carried the stamp).
#
# ONE builder, so the payload copy and the persisted copy cannot drift, and ONE
# contextvar set at the same seam as the principal — read by the single persistence
# chokepoint, so all five engines inherit it without signature plumbing.
#
# `applied` is the load-bearing field: a measurement must be able to tell "the
# boundary held" from "the boundary was never in force", and an absent stamp cannot
# say which. Built BEFORE the engine runs — nothing in it depends on the outcome.

def build_authority_stamp(principal: Optional[AgentPrincipal],
                          origin: Optional[str],
                          configured: Optional["AgentScope"] = None) -> Dict[str, Any]:
    """The turn's authority stamp. Pure — the one shape both copies are built from.

    ``configured`` is the agent's own scope on a turn where it did NOT apply (an interactive
    turn without ``enforce_interactive``). A restricted one is sealed as ``not_in_force`` so the
    reading can name limits the operator configured but this turn ran without; an unrestricted
    one adds nothing (byte-identical baseline)."""
    if principal is None:
        stamp: Dict[str, Any] = {"applied": False, "origin": origin}
        if isinstance(configured, AgentScope) and configured.is_restricted:
            stamp["not_in_force"] = configured.to_dict()
        return stamp
    return {"applied": True, "principal_id": principal.principal_id,
            "origin": origin, **principal.scope.to_dict()}


_TURN_AUTHORITY: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = \
    contextvars.ContextVar("agent_turn_authority", default=None)


def set_turn_authority(stamp: Optional[Dict[str, Any]]):
    """Set the ambient authority stamp for the current async context. Returns a token."""
    return _TURN_AUTHORITY.set(stamp)


def get_turn_authority() -> Optional[Dict[str, Any]]:
    """The stamp for the running turn, or None outside a turn (→ no stamp, baseline)."""
    return _TURN_AUTHORITY.get()


def reset_turn_authority(token) -> None:
    try:
        _TURN_AUTHORITY.reset(token)
    except Exception:  # noqa: BLE001 — a reset must never break a turn
        pass
