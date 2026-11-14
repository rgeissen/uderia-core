"""
Deployment signature (Tier 5.2) — the half of an agent's trust that cannot travel.

An agent has two signable dimensions, because two different questions are being asked:

  * **portable** (``composition`` + ``posture`` + ``capability``) — *"is this agent the
    artefact that was validated?"* It travels with a marketplace pack and reinstates on
    install, so a publisher's signature survives an id remap.
  * **deployment** (this module) — *"is its resolved capability surface still what was
    attested **here**?"* It covers the things that legitimately differ per instance: which
    LLM actually answers, which MCP tools/prompts the profile can actually reach, and which
    platform connectors the owner has opted into.

Why they must be separate: the marketplace honesty gate (``portability.reinstate_kind``)
only re-applies a signature when the copy's live hash equals what was signed. A subscriber
uses *their* LLM, *their* MCP server and has opted into *no* connectors, so folding those
into the portable hash would make **every** installed pack permanently 🟠 and destroy the
marketplace trust story. Leaving them unsigned instead would let a model swap, a server
quietly gaining a write tool, or a connector opt-in widen an agent behind a green badge.
Two hashes answer both honestly: publisher provenance survives, and the deployer attests
their own surface.

**Who attests it.** The split carries an authority split with it: the portable half can be
vouched for by a third party (approver tier), the deployment half is a statement of intent
about a surface only its owner controls, so it is **ownership**-gated. See
``api/trust_routes._authorize_deployment_attester`` — routing it through the approver-tier
gate could only ever lock the owner out, never enable a reviewer.

**Enablement is content.** A disabled→enabled flip changes what the agent can do while
changing no artefact, so the opt-in state is hashed alongside the catalog itself.

Pure core (``build_deployment_signature``) is fixture-testable; the fetch helpers read the
real config/DB and mark what they could not resolve rather than dropping it — a silently
omitted surface is exactly the hole this closes.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Dict, List, Optional

from trusted_data_agent.core import sqlite_util
from .attestation import content_hash

logger = logging.getLogger("quart.app")

DEPLOYMENT_KIND = "agent_deployment"

#: Marker for a surface that exists but could not be read. It must never be confused with
#: "absent", which is a legitimate, stable state — see the module docstring.
UNRESOLVED = "__unresolved__"


# ── pure assembly (testable) ─────────────────────────────────────────────────

def bindings_state(db_path: Optional[str] = None):
    """PROP-003 — WHICH module assesses each property here, and what that module is.

    Deployment-bound by construction, so it belongs in this half of the envelope and never
    travels with a pack: a subscriber's bindings are their own decision. Including it means
    a rebind — or an edit to a bound third-party module — flips the deployment face to
    `changed` until re-attested. Silently swapping the machinery that decides what an agent
    may say is exactly the widening this envelope exists to catch.

    Carries each module's CONTENT HASH, not just its key: rebinding to a different module
    and editing the module in place are both changes to what runs, and a key-only record
    would see only the first.

    Returns UNRESOLVED (never `[]`) if the substrate cannot be read — the loud-absence
    convention `_connector_state` already uses, because "no bindings" and "we could not
    tell" must not hash alike.
    """
    try:
        from trusted_data_agent.agent import assurance as _A
        from trusted_data_agent.agent.assurance import registry as _reg
        _A.bootstrap(db_path=db_path)
        rows = []
        for r in _reg.bindings():
            if not r.get("bound"):
                continue
            key = str(r.get("assessor") or "")
            mod_hash = "builtin"
            if "." in key:
                from trusted_data_agent.core.trust import content as _tc
                mod_hash = _tc.live_hash(
                    "assurance_module", key.split(".", 1)[0]) or UNRESOLVED
            rows.append({"property": str(r.get("property") or ""),
                         "lane": str(r.get("lane") or ""),
                         "assessor": key, "module": mod_hash})
        return sorted(rows, key=lambda x: (x["property"], x["lane"]))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust.deployment: assurance bindings unavailable "
                       f"({type(e).__name__}: {e}) — marked UNRESOLVED, not dropped")
        return UNRESOLVED


def control_postures_state(db_path: Optional[str] = None):
    """PROP-003 — every control an operator has moved OFF its dormant default.

    Bindings decide what the machinery SEES; a posture decides whether it ACTS. Raising a
    third-party control from `observe` to `enforce` is the larger change of the two — it
    starts altering or withholding answers — and it was invisible to this envelope while a
    rebind was not. Same argument, same half.

    Only the deviations are carried, and the field is omitted entirely when there are none
    (see `build_deployment_signature`). That is not tidiness: it means every deployment
    that has never raised a control hashes exactly as it did before this existed, so adding
    the field flips no existing attestation to `changed`. The record appears on the day it
    first means something.
    """
    try:
        from trusted_data_agent.agent.assurance import controls as _ctl
        from trusted_data_agent.agent.assurance import store as _store
        rows = []
        for c in _ctl.controls():
            p = _store.control_posture(c.key, db_path=db_path)
            if p and p != _ctl.DEFAULT_POSTURE:
                rows.append({"control": c.key, "posture": str(p), "act": c.act})
        return sorted(rows, key=lambda x: x["control"])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trust.deployment: control postures unavailable "
                       f"({type(e).__name__}: {e}) — marked UNRESOLVED, not dropped")
        return UNRESOLVED


def build_deployment_signature(llm: Optional[Dict],
                               catalog: Optional[Dict],
                               connectors: Optional[List[Dict]],
                               bindings: Optional[Any] = None,
                               control_postures: Optional[Any] = None,
                               fallback_llm: Optional[Any] = None,
                               data_sources: Optional[Any] = None,
                               combine_isolation: Optional[Any] = None,
                               additional_mcp: Optional[Any] = None,
                               file_store: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the canonical, order-independent deployment signature.

    ``llm``        = ``{"provider", "model"}`` (never the config id, never credentials)
    ``catalog``    = ``{"tools": [...], "prompts": [...]}`` of reachable names+arg names
    ``connectors`` = ``[{"server_id", "opted_in", "user_tools": [...]}, ...]``
    Pure — no I/O.
    """
    out: Dict[str, Any] = {}

    if llm is UNRESOLVED or llm == UNRESOLVED:
        out["llm"] = UNRESOLVED
    elif llm:
        # Identity, not location: a pack installed elsewhere legitimately points at a
        # different stored configuration, but swapping a frontier model for a weak one is a
        # real change to what this deployment does.
        out["llm"] = {"provider": str(llm.get("provider") or ""),
                      "model": str(llm.get("model") or "")}
    else:
        out["llm"] = None

    if catalog is UNRESOLVED or catalog == UNRESOLVED:
        out["catalog"] = UNRESOLVED
    elif catalog:
        out["catalog"] = {
            "tools": sorted({str(t) for t in (catalog.get("tools") or [])}),
            "prompts": sorted({str(p) for p in (catalog.get("prompts") or [])}),
        }
    else:
        out["catalog"] = None

    if connectors is UNRESOLVED or connectors == UNRESOLVED:
        # TC-25: connector opt-in that could not be read is marked UNRESOLVED, exactly like
        # the LLM and catalog surfaces — never silently narrowed to "no connectors", which
        # would let a persistent read failure sign a signature omitting an enabled
        # shell/browser connector while the badge still read intact.
        out["connectors"] = UNRESOLVED
    else:
        # Only opted-in connectors carry capability; an opted-out row is equivalent to no row,
        # so it must hash identically (otherwise merely visiting the panel flips the badge).
        conns = []
        for c in (connectors or []):
            if not c.get("opted_in"):
                continue
            conns.append({"server_id": str(c.get("server_id") or ""),
                          "tools": sorted({str(t) for t in (c.get("user_tools") or [])})})
        out["connectors"] = sorted(conns, key=lambda c: c["server_id"])

    # PROP-003 · the assurance bindings. ABSENT rather than null when not supplied, so
    # every attestation written before this field existed hashes exactly as it did —
    # adding a key with a null value would flip every deployed agent to `changed` on
    # upgrade, which is the badge crying wolf on its first day.
    if bindings is not None:
        out["bindings"] = (UNRESOLVED if (bindings is UNRESOLVED or bindings == UNRESOLVED)
                           else list(bindings))

    # …and the postures. Omitted when EMPTY as well as when absent — an operator who has
    # never raised a control has nothing to record, and a deployment that records nothing
    # must hash as one that could not have. UNRESOLVED still lands, because "we could not
    # read the postures" is a fact and must not hash like "there are none".
    if control_postures is not None:
        if control_postures is UNRESOLVED or control_postures == UNRESOLVED:
            out["control_postures"] = UNRESOLVED
        elif list(control_postures):
            out["control_postures"] = list(control_postures)

    # S4 · the SIGNED fallback model. "Which fallback" is part of what this deployment does
    # — a turn served by it is DECLARED drift only because the fallback itself is attested
    # here. ABSENT when no fallback is bound (never null), so every attestation written
    # before this field existed hashes exactly as it did; UNRESOLVED when bound to a
    # configuration that cannot be read (a fact, not "none").
    if fallback_llm is not None:
        if fallback_llm is UNRESOLVED or fallback_llm == UNRESOLVED:
            out["fallback_llm"] = UNRESOLVED
        elif fallback_llm:
            out["fallback_llm"] = {"provider": str(fallback_llm.get("provider") or ""),
                                   "model": str(fallback_llm.get("model") or "")}

    # P1 · the database connections the agent may run its OWN SQL against (the Database
    # Navigator's `query` allow-list). What data this deployment can reach is deployment
    # surface; the ids are instance-local, which is exactly the deployment half's scope.
    # ABSENT when the agent cannot query, so every earlier attestation hashes as before;
    # UNRESOLVED when the connection store could not be read.
    if data_sources is not None:
        if data_sources is UNRESOLVED or data_sources == UNRESOLVED:
            out["data_sources"] = UNRESOLVED
        else:
            out["data_sources"] = sorted(
                ({"id": str(x.get("id") or ""), "driver": str(x.get("driver") or "")} for x in data_sources),
                key=lambda x: x["id"])
    # PROP-006 A13 — the agent's OWN combine isolation rules (instance-local source ids, so the
    # deployment half). Loosening one is a real change to what this agent may join. ABSENT when
    # the agent declares none, so every earlier attestation hashes as before.
    if combine_isolation:
        out["combine_isolation"] = sorted(
            (sorted(str(x) for x in (r.get("sources") or [])) for r in combine_isolation
             if isinstance(r, dict) and len(r.get("sources") or []) >= 2))
        if not out["combine_isolation"]:
            del out["combine_isolation"]
    # PROP-006 phase 2 (M5) — the catalogs of the agent's ADDITIONAL MCP servers, one entry per
    # server ({server_id, alias, catalog}), the primary's `catalog` above untouched. ABSENT for a
    # single-server agent, so every existing attestation hashes exactly as before; an entry whose
    # server was never classified here is UNRESOLVED (a fact, not "no tools").
    if additional_mcp:
        out["additional_mcp"] = sorted(
            ({"server_id": str(x.get("server_id") or ""), "alias": str(x.get("alias") or ""),
              "catalog": x.get("catalog")} for x in additional_mcp if isinstance(x, dict)),
            key=lambda x: (x["alias"], x["server_id"]))
    # PROP-010 §4.6 — WHERE this agent's files land (the store its fileStoreConfigId names).
    # Store ids are instance-local, so this is the deployment half; changing where an agent's
    # outputs are kept is visible drift. ABSENT when the agent names no store (it follows the
    # admin default), so every earlier attestation hashes exactly as before.
    if file_store:
        out["file_store"] = str(file_store)
    return out


def deployment_hash(signature: Dict[str, Any]) -> str:
    """The canonical hash of a deployment signature."""
    return content_hash(signature)


def is_baseline(signature: Dict[str, Any]) -> bool:
    """True when nothing deployment-bound is configured — no LLM, no catalog, no
    connectors. Used to keep an unconfigured agent from carrying a meaningless
    deployment attestation requirement."""
    return (not signature.get("llm") and not signature.get("catalog")
            and not signature.get("connectors"))


# ── fetch helpers (real sources) ─────────────────────────────────────────────

def _llm_identity(profile: Dict, user_uuid: str, key: str = "llmConfigurationId") -> Optional[Any]:
    """Resolved provider+model for the profile's bound LLM configuration (`key` selects the
    primary or, for S4, the signed fallback `llmFallbackConfigurationId`)."""
    cfg_id = (profile or {}).get(key)
    if not cfg_id:
        return None
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        for cfg in get_config_manager().get_llm_configurations(user_uuid) or []:
            if cfg.get("id") == cfg_id:
                return {"provider": cfg.get("provider"),
                        "model": cfg.get("model") or cfg.get("model_name")}
        # Bound to a configuration that no longer exists: a real fault, surfaced.
        logger.warning(f"trust.deployment: profile bound to unknown LLM config {cfg_id}")
        return UNRESOLVED
    except Exception as e:  # noqa: BLE001 — trust resolution must never raise
        logger.debug(f"trust.deployment: llm identity failed: {e}")
        return UNRESOLVED


def _catalog_names(cr: Dict[str, Any]) -> Dict[str, List[str]]:
    """``name(arg,…)`` for every tool and prompt of one classification snapshot, the runtime-
    injected categories excluded — ONE rule for the primary's snapshot and every additional one."""
    from trusted_data_agent.core.config_manager import RUNTIME_INJECTED_CATEGORIES
    names: Dict[str, List[str]] = {"tools": [], "prompts": []}
    for field, bucket in (("tools", "tools"), ("prompts", "prompts")):
        section = cr.get(field)
        if isinstance(section, dict):
            entries = [e for cat, group in section.items()
                       if cat not in RUNTIME_INJECTED_CATEGORIES and isinstance(group, list)
                       for e in group]
        elif isinstance(section, list):
            entries = section
        else:
            entries = []
        for e in entries:
            if isinstance(e, dict) and e.get("name"):
                args = sorted(str(a.get("name")) for a in (e.get("arguments") or [])
                              if isinstance(a, dict) and a.get("name"))
                # Arg names ride along so a tool that silently gains a parameter — a real
                # capability change — is not invisible.
                # TC-68 (accepted): this format-string encoding is theoretically collision-
                # prone (`foo(x)` the tool name vs `foo` with arg `x`) — MCP tool names are
                # conventional identifiers, and changing the encoding flips every existing
                # deployment attestation to CHANGED, so it stays until a deliberate
                # hash-format version bump.
                names[bucket].append(f"{e['name']}({','.join(args)})")
    return names


def additional_catalogs(profile: Dict) -> Optional[List[Dict[str, Any]]]:
    """PROP-006 phase 2 (M5): the catalog of each ADDITIONAL MCP server, from its own snapshot
    (names are the server's own; the alias rides beside them). ``None`` for a single-server agent
    or a class that never calls MCP — the signature field is then absent."""
    from trusted_data_agent.core.profile_capabilities import consumes_mcp_catalog
    from trusted_data_agent.core.agent_mcp_servers import additional_of, ADDITIONAL_CLASSIFICATION_KEY
    if not consumes_mcp_catalog(profile):
        return None
    extra = additional_of(profile)
    if not extra:
        return None
    snaps = (profile or {}).get(ADDITIONAL_CLASSIFICATION_KEY)
    snaps = snaps if isinstance(snaps, dict) else {}
    out = []
    for srv in extra:
        cr = snaps.get(srv.server_id)
        if not (isinstance(cr, dict) and cr.get("last_classified")):
            cat: Any = UNRESOLVED
        else:
            cat = _catalog_names(cr)
        out.append({"server_id": srv.server_id, "alias": srv.alias, "catalog": cat})
    return out


def catalog_from_classification(profile: Dict) -> Optional[Any]:
    """The MCP tool/prompt surface this profile can actually reach.

    Read from the persisted classification snapshot rather than a live MCP connection, so
    badge resolution works offline and does not depend on the server being up.

    **Honest limit:** a server that changes upstream is caught when the profile next
    re-classifies, not instantaneously. The timestamp is deliberately excluded from the
    signature, so re-classifying an unchanged server does not flip the badge, while a
    server that gained a tool does.
    """
    # Reachability is a property of the CLASS, asked in ONE place. A Focus or
    # Coordinate agent never calls MCP, so a stored `mcpServerId` — routinely left
    # behind by a pack install or a class change — is an inert binding, not a
    # surface. Hashing it would make the attestation claim coverage of a catalog the
    # agent cannot reach, and demanding it be classified refused an attestation that
    # could never be granted (555 profiles on production, 2026-09-09).
    from trusted_data_agent.core.profile_capabilities import consumes_mcp_catalog
    if not consumes_mcp_catalog(profile):
        return None
    from trusted_data_agent.core.agent_mcp_servers import primary_of
    if not primary_of(profile):
        return None
    cr = (profile or {}).get("classification_results")
    if not isinstance(cr, dict):
        # An MCP server is bound but nothing has been discovered: honestly unknown, not
        # "no tools" — the latter would understate the surface.
        return UNRESOLVED
    # The runtime-injected categories are PLATFORM tools (the TDA_* framework tools and
    # the action components), never part of the MCP surface this profile reaches. The
    # persistence chokepoint strips them, but a record written before that strip — or
    # mutated by a reader that enriched its live reference (the resource-panel endpoint,
    # found live 2026-09-22: it REPLACED "Component Tools" with only the tools it found
    # missing, so a record went from the 8 pre-S3 components to `[TDA_Compute]`) — still
    # carries them. Hashing them made a platform release ("TDA_Compute appeared, 8 tools
    # removed") read as this agent's deployment drift on 10 of 57 production attestations.
    # The exclusion is by the ONE declared category set, never by a name heuristic: an
    # MCP tool that happens to be named `TDA_something` stays in the surface.
    names = _catalog_names(cr)
    if not names["tools"] and not names["prompts"] and not cr.get("last_classified"):
        # A snapshot that EXISTS but was never taken: `_build_profile` writes the block
        # empty with `last_classified: None` for every imported profile, and until this
        # branch an attestation over it hashed "no tools" as a fact and read `trusted` for
        # a catalog the instance had never looked at (found live, 2026-09-07). A server
        # that genuinely exposes nothing has a timestamp; a snapshot with neither is not
        # a catalog, it is the absence of one.
        return UNRESOLVED
    return names


def unresolved_surfaces(signature: Dict[str, Any]) -> List[str]:
    """The surfaces of a signature that could not be resolved — pure. An additional MCP server
    whose catalog is unresolved is named ``additional_mcp:<alias>``."""
    out = [k for k, v in (signature or {}).items() if v == UNRESOLVED]
    for x in (signature or {}).get("additional_mcp") or []:
        if isinstance(x, dict) and x.get("catalog") == UNRESOLVED:
            out.append(f"additional_mcp:{x.get('alias')}")
    return out


_UNRESOLVED_GUIDANCE = {
    "catalog": ("the tool catalog is not classified on this instance — run the profile's "
                "connection test (classification) first, then attest"),
    "llm": "the bound LLM configuration could not be resolved",
    "connectors": "the connector opt-in store could not be read",
    "bindings": "the assurance bindings could not be read",
    "control_postures": "the control postures could not be read",
}


def attest_refusal(signature: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Why a deployment signature must NOT be attested, or ``None`` when it may be.

    An attestation is the deployer saying *this is what I intend here*; over an
    UNRESOLVED surface it says that about something nobody has looked at, and the badge
    then reads 🟢 for a fact the instance does not hold. Refuse, and name the surface
    and the act that resolves it — a refusal that names its own remedy costs no round.
    """
    unresolved = unresolved_surfaces(signature)
    if not unresolved:
        return None

    def _say(k):
        if k.startswith("additional_mcp:"):
            return (f"the tool catalog of the additional MCP server '{k.split(':', 1)[1]}' is not "
                    f"classified on this instance — activate the agent once, then attest")
        return _UNRESOLVED_GUIDANCE.get(k, f"{k} could not be resolved")
    return {
        "reason": "unresolved_surface",
        "unresolved": unresolved,
        "message": "cannot attest: " + "; ".join(_say(k) for k in unresolved),
    }


def _connector_state(profile_id: str, db_path: Optional[str] = None):
    """Per-profile platform-connector opt-in — the highest-capability enablement on the
    platform (browser, shell, files, Google/M365/Slack), and stored outside the profile.

    Returns a list of connector rows, or ``UNRESOLVED`` (TC-25) when the store could not be
    read — so a persistent failure is hashed as "unknown", never as "no connectors"."""
    try:
        # Reuse the store's resolver rather than re-deriving the path: `AUTH_DB_PATH`
        # carries a `sqlite:///` prefix that must be stripped, and a second hand-rolled
        # copy of that rule silently resolved to an unopenable path — which, behind the
        # fail-soft below, made connector opt-in vanish from the signature entirely.
        from . import store as _store_mod
        with contextlib.closing(sqlite_util.connect(_store_mod._db_path(db_path))) as conn:
            rows = conn.execute(
                "SELECT server_id, opted_in, user_tools FROM profile_connector_settings "
                "WHERE profile_id = ?", (profile_id,)).fetchall()
        out = []
        for server_id, opted_in, user_tools in rows:
            tools = []
            if user_tools:
                try:
                    import json as _json
                    parsed = _json.loads(user_tools)
                    tools = parsed if isinstance(parsed, list) else list(parsed or [])
                except Exception:
                    tools = [str(user_tools)]
            out.append({"server_id": server_id, "opted_in": bool(opted_in),
                        "user_tools": tools})
        return out
    except Exception as e:  # noqa: BLE001
        # Deliberately loud: connector opt-in is a security-relevant input, and silently
        # returning "no connectors" would make an enabled shell/browser connector vanish
        # from the signature — the badge would then read intact while the agent's reach had
        # widened. Surface it rather than degrade quietly.
        logger.warning(f"trust.deployment: connector state unavailable for "
                       f"profile {profile_id!r} ({type(e).__name__}: {e}) — "
                       f"connector opt-in marked UNRESOLVED (not silently dropped)")
        return UNRESOLVED


def deployment_signature(profile: Dict, user_uuid: str,
                         db_path: Optional[str] = None) -> Dict[str, Any]:
    """The live deployment signature for a real profile-agent."""
    profile = profile or {}
    pid = profile.get("id")
    if not pid:
        # TC-67: a profile dict with no id would silently query profile_id="" → zero
        # connector rows → a narrowed signature with no log. Every real caller supplies an
        # id, so this is a caller bug — make it visible instead of quietly signing less.
        logger.warning("trust.deployment: profile has no id — connector opt-in cannot be "
                       "resolved and is marked UNRESOLVED")
    return build_deployment_signature(
        _llm_identity(profile, user_uuid),
        catalog_from_classification(profile),
        _connector_state(pid, db_path=db_path) if pid else UNRESOLVED,
        bindings_state(db_path=db_path),
        control_postures_state(db_path=db_path),
        _llm_identity(profile, user_uuid, key="llmFallbackConfigurationId"),
        navigator_query_sources(profile, user_uuid),
        profile.get("combineIsolation"),
        additional_catalogs(profile),
        profile.get("fileStoreConfigId") or None,
    )


def navigator_query_sources(profile: Dict, user_uuid: str) -> Optional[Any]:
    """The connections this agent may query through the Database Navigator, as
    ``[{id, driver}]`` — ``None`` when it may not query (so nothing is recorded), ``UNRESOLVED``
    when the store cannot be read. Resolved exactly as the handler resolves it: the explicit
    allow-list, else the agent's resolved default source."""
    try:
        nav = ((profile or {}).get("componentConfig") or {}).get("database_navigator") or {}
        q = nav.get("query") if isinstance(nav, dict) else None
        if not (isinstance(nav, dict) and nav.get("enabled") and isinstance(q, dict) and q.get("enabled") is True):
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        from trusted_data_agent.dbcatalog.resolution import resolve_sources
        res = resolve_sources(user_uuid, profile)
        by_id = {src["id"]: src for src in res.get("sources", [])}
        wanted = [str(x) for x in (q.get("sources") or []) if str(x).strip()] or \
                 ([res["default"]] if res.get("default") else [])
        return [{"id": i, "driver": (by_id.get(i) or {}).get("driver") or "unavailable"} for i in wanted]
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"trust.deployment: navigator query sources unreadable — marked UNRESOLVED ({exc})")
        return UNRESOLVED


def describe_drift(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """Human-readable drift between two deployment signatures.

    Re-signing after a catalog change is only a reasonable ask if the operator can see
    *what appeared*; an unexplained amber badge trains people to click through it.
    """
    out: List[str] = []
    o, n = old or {}, new or {}

    ol, nl = o.get("llm"), n.get("llm")
    def _fmt(v):
        if isinstance(v, dict):
            return f"{v.get('provider')}/{v.get('model')}"
        return "unresolved" if v == UNRESOLVED else "none"
    if ol != nl:
        out.append(f"model changed: {_fmt(ol)} → {_fmt(nl)}")
    od, nd = o.get("data_sources"), n.get("data_sources")
    if od != nd:
        _ids = lambda v: sorted(x.get("id") for x in v) if isinstance(v, list) else ([] if v is None else [str(v)])
        added, removed = set(_ids(nd)) - set(_ids(od)), set(_ids(od)) - set(_ids(nd))
        if od is None and nd is not None:
            out.append(f"database querying enabled: {', '.join(_ids(nd)) or '(none)'}")
        elif nd is None and od is not None:
            out.append("database querying disabled")
        else:
            if added:
                out.append(f"queryable databases added: {', '.join(sorted(added))}")
            if removed:
                out.append(f"queryable databases removed: {', '.join(sorted(removed))}")
            if not added and not removed:
                out.append("queryable databases changed")
    oi, ni = o.get("combine_isolation") or [], n.get("combine_isolation") or []
    if oi != ni:
        _r = lambda v: {" + ".join(x) for x in v}
        for gone in sorted(_r(oi) - _r(ni)):
            out.append(f"combine isolation rule removed: {gone}")
        for new in sorted(_r(ni) - _r(oi)):
            out.append(f"combine isolation rule added: {new}")
    of, nf = o.get("fallback_llm"), n.get("fallback_llm")
    if of != nf:
        out.append(f"fallback model changed: {_fmt(of)} → {_fmt(nf)}")
    ofs, nfs = o.get("file_store"), n.get("file_store")
    if ofs != nfs:
        out.append(f"file store changed: {ofs or 'the admin default'} → {nfs or 'the admin default'}")

    # PROP-006 phase 2 (M5): the additional MCP servers, named per server
    oa = {x.get("server_id"): x for x in (o.get("additional_mcp") or []) if isinstance(x, dict)}
    na = {x.get("server_id"): x for x in (n.get("additional_mcp") or []) if isinstance(x, dict)}
    for sid in sorted(set(na) - set(oa)):
        out.append(f"MCP server added: {na[sid].get('alias')} ({sid})")
    for sid in sorted(set(oa) - set(na)):
        out.append(f"MCP server removed: {oa[sid].get('alias')} ({sid})")
    for sid in sorted(set(oa) & set(na)):
        ox, nx = oa[sid], na[sid]
        if ox.get("alias") != nx.get("alias"):
            out.append(f"MCP server {sid} renamed: {ox.get('alias')} → {nx.get('alias')}")
        oc, nc = ox.get("catalog"), nx.get("catalog")
        if oc == nc:
            continue
        if oc == UNRESOLVED or nc == UNRESOLVED:
            out.append(f"MCP server {nx.get('alias')}: tool catalog became "
                       + ("unreadable (not classified on this instance)" if nc == UNRESOLVED else "readable"))
            continue
        for field in ("tools", "prompts"):
            before, after = set((oc or {}).get(field) or []), set((nc or {}).get(field) or [])
            gained, lost = sorted(after - before), sorted(before - after)
            if gained:
                out.append(f"MCP server {nx.get('alias')}: {field} appeared: {', '.join(gained[:8])}"
                           + (f" (+{len(gained) - 8} more)" if len(gained) > 8 else ""))
            if lost:
                out.append(f"MCP server {nx.get('alias')}: {field} removed: {', '.join(lost[:8])}"
                           + (f" (+{len(lost) - 8} more)" if len(lost) > 8 else ""))

    ocat, ncat = o.get("catalog"), n.get("catalog")
    if ocat == UNRESOLVED or ncat == UNRESOLVED:
        # TC-26: a dict→UNRESOLVED transition is "catalog became unreadable", NOT "every tool
        # removed" — the latter mis-describes an unavailable snapshot as a capability loss.
        if ocat != ncat:
            out.append("tool catalog became unreadable (not classified on this instance, "
                       "or the snapshot could not be read) — classify, then re-attest"
                       if ncat == UNRESOLVED
                       else "tool catalog became readable")
    else:
        oc = ocat if isinstance(ocat, dict) else {}
        nc = ncat if isinstance(ncat, dict) else {}
        for field in ("tools", "prompts"):
            before, after = set(oc.get(field) or []), set(nc.get(field) or [])
            gained, lost = sorted(after - before), sorted(before - after)
            if gained:
                out.append(f"{field} appeared: {', '.join(gained[:8])}"
                           + (f" (+{len(gained) - 8} more)" if len(gained) > 8 else ""))
            if lost:
                out.append(f"{field} removed: {', '.join(lost[:8])}"
                           + (f" (+{len(lost) - 8} more)" if len(lost) > 8 else ""))

    oconns, nconns = o.get("connectors"), n.get("connectors")
    if oconns == UNRESOLVED or nconns == UNRESOLVED:
        if oconns != nconns:
            out.append("connector opt-in became unreadable" if nconns == UNRESOLVED
                       else "connector opt-in became readable")
    else:
        # TC-26: diff per server AND within a shared server — a connector whose user_tools
        # list widened is a real capability change that a server_id-set comparison misses,
        # leaving an unexplained amber the describer exists to prevent.
        o_by = {c["server_id"]: set(c.get("tools") or []) for c in (oconns or [])}
        n_by = {c["server_id"]: set(c.get("tools") or []) for c in (nconns or [])}
        for sid in sorted(set(n_by) - set(o_by)):
            out.append(f"connector enabled: {sid}")
        for sid in sorted(set(o_by) - set(n_by)):
            out.append(f"connector disabled: {sid}")
        for sid in sorted(set(o_by) & set(n_by)):
            gained = sorted(n_by[sid] - o_by[sid])
            if gained:
                out.append(f"connector {sid} tools appeared: {', '.join(gained[:8])}"
                           + (f" (+{len(gained) - 8} more)" if len(gained) > 8 else ""))

    # PROP-003 · the assessment machinery itself. Named per row, because "the bindings
    # changed" tells an operator only that something moved — the thing they need is WHICH
    # property is now assessed by WHAT, which is the difference between re-attesting and
    # investigating.
    obind, nbind = o.get("bindings"), n.get("bindings")
    if obind is None and nbind is None:
        pass                      # neither signature carries the field — nothing to say
    elif obind == UNRESOLVED or nbind == UNRESOLVED:
        if obind != nbind:
            out.append("assurance bindings became unreadable" if nbind == UNRESOLVED
                       else "assurance bindings became readable")
    else:
        def _key(r):
            return (r.get("property"), r.get("lane"))
        o_by = {_key(r): r for r in (obind or [])}
        n_by = {_key(r): r for r in (nbind or [])}
        for k in sorted(set(n_by) - set(o_by)):
            out.append(f"assurance binding added: {k[0]} ({k[1].lower()}) is now "
                       f"assessed by {n_by[k].get('assessor')}")
        for k in sorted(set(o_by) - set(n_by)):
            out.append(f"assurance binding removed: {k[0]} ({k[1].lower()}) is no longer "
                       f"assessed")
        for k in sorted(set(o_by) & set(n_by)):
            ob, nb = o_by[k], n_by[k]
            if ob.get("assessor") != nb.get("assessor"):
                out.append(f"assurance binding changed: {k[0]} ({k[1].lower()}) is now "
                           f"assessed by {nb.get('assessor')} "
                           f"(was {ob.get('assessor')})")
            elif ob.get("module") != nb.get("module"):
                # Same module key, different content: the pack was edited in place. A
                # key-only comparison would report this as no change at all.
                out.append(f"assurance module edited: {nb.get('assessor')} "
                           f"(assessing {k[0]}) is not the version that was attested")
    return out
