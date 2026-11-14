"""
Trust Membrane REST seam (Tier 5.2, Phase 1).

The agent is the unit of trust. These endpoints resolve an agent's live badge
(computed compositionally over its ingredients), sign off ingredients + agents
(admin-gated, with separation of duties), attest a deployment (owner-gated), and
verify an attestation offline.

Endpoints (mounted under /api):
  GET  /v1/trust/agent/<profile_id>            → the agent's live badge + breakdown
  POST /v1/trust/ontology/<kg_id>/sign-off     → validate + sign an ontology ingredient
  POST /v1/trust/skill/<skill_id>/sign-off     → sign a skill ingredient (admin-attested)
  POST /v1/trust/agent/<profile_id>/sign-off   → sign the agent (bottom-up gate)
  POST /v1/trust/agent/<profile_id>/attest-deployment
                                               → attest THIS instance's resolved surface
  POST /v1/trust/verify                        → offline-verify an attestation

Governance — the two halves of the envelope have two AUTHORITIES, because they answer
different questions (see ``_authorize_deployment_attester`` for the full reasoning):

  * **Sign-off** (the portable half — "is this the artefact that was validated?") reuses
    the Tier 4.3 ontology-governance policy, because a third party can vouch for it:
      - signer tier must be in ``policy.approver_tiers`` (default: admin);
      - separation of duties — when ``policy.require_approval`` is set, a signer may
        not sign off their own artifact (default policy = off → self-signing allowed,
        so a solo/self-hosted instance is not blocked).
  * **Attest-deployment** (the deployment half — "is this what I intend HERE?") is gated
    on **ownership, not tier**: only the owner controls the resolved model, tool catalog
    and connector opt-ins being attested, and the profile is resolved under the caller's
    own uuid, so a non-owner gets 404. Neither the tier gate nor separation of duties
    applies (attesting is always self, so SoD would forbid it outright).

Design: docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md §4, §9.
"""

from __future__ import annotations

import contextlib
import asyncio
import logging
import threading
from typing import Optional

from quart import Blueprint, jsonify, request

from trusted_data_agent.core.trust import store as trust_store
from trusted_data_agent.api.operations import Arg, operation
from trusted_data_agent.core.trust import composition as trust_comp
from trusted_data_agent.core.trust import content as trust_content
from trusted_data_agent.core.trust import pack as trust_pack
from trusted_data_agent.core.trust.attestation import (
    content_hash, composition_hash, IngredientRef, agent_signable, resolve_agent_trust,
)
from trusted_data_agent.core.trust.validators import ontology_validator

logger = logging.getLogger("quart.app")

trust_bp = Blueprint("trust", __name__)


# ── auth + governance helpers ────────────────────────────────────────────────

def _current_user():
    """Return the authenticated User object, or None."""
    try:
        from trusted_data_agent.auth.middleware import get_current_user
        user = get_current_user()
        return user if (user and getattr(user, "id", None)) else None
    except Exception:
        return None


def _assert_owner(kind: str, artifact_id: str, user_uuid: str):
    """TC-30 — per-kind OWNERSHIP gate for leaf sign-offs. Returns (ok, reason, http_status).

    ``_authorize_signer`` checks *tier*; the docstring's "targets the caller's own segment"
    was an assumption, not a check — an approver-tier user could validate-and-sign ANY
    user's KG/collection/flow/dashboard/canvas/pack, mutating the global attestation state
    that renders on the owner's badges. This resolves the artifact's owner per kind and
    requires the caller to BE the owner.

    Fail direction (the TC-33 lesson): a lookup error or a NULL owner row DENIES — an authz
    check that fails open is the defect class this closes. ``skill``/``extension`` are
    deliberately NOT gated here: they are platform-level artifacts with no per-user owner,
    so the tier gate alone is their (correct) authority model.
    """
    try:
        owner = None
        if kind in ("ontology", "data_product", "ontology_extension", "action_type"):
            # A data product is owned by its ONTOLOGY's owner (identity is
            # (kg_id, product name) — OBDA-READ Phase A3); the artifact id's kg part
            # resolves ownership through the same kg_metadata row. An extension
            # (PROP-008 A5, `kg::ext::<id>`) is owned by the owner of the GRAPH it sits
            # on — a subscriber's copy is the subscriber's row, so a subscriber signs
            # its own extension of a graph it does not publish.
            kg_key = (artifact_id.partition("::")[0]
                      if kind in ("data_product", "ontology_extension", "action_type") else artifact_id)
            from trusted_data_agent.core import sqlite_util
            with contextlib.closing(sqlite_util.connect(trust_store._db_path(None))) as conn:
                row = conn.execute("SELECT user_uuid FROM kg_metadata WHERE kg_id=?",
                                   (kg_key,)).fetchone()
            if not row:
                return False, f"Knowledge graph '{kg_key}' not found", 404
            owner = row[0]
        elif kind in ("knowledge", "planner"):
            from trusted_data_agent.core.collection_db import get_collection_db
            coll = get_collection_db().get_collection_by_id(int(artifact_id))
            if not coll:
                return False, f"Collection '{artifact_id}' not found", 404
            owner = coll.get("owner_user_id") or coll.get("user_id")
        elif kind in ("flow", "dashboard", "canvas"):
            from trusted_data_agent.core import sqlite_util
            table, idcol = {"flow": ("flows", "flow_id"),
                            "dashboard": ("dashboards", "id"),
                            "canvas": ("canvases", "canvas_id")}[kind]
            with contextlib.closing(sqlite_util.connect(trust_store._db_path(None))) as conn:
                row = conn.execute(f"SELECT user_id FROM {table} WHERE {idcol}=?",
                                   (artifact_id,)).fetchone()
            if not row:
                return False, f"{kind} '{artifact_id}' not found", 404
            owner = row[0]
        elif kind == "agent_pack":
            owner = trust_pack.owner(artifact_id)
            if owner is None:
                return False, f"Agent pack '{artifact_id}' not found", 404
        else:
            return True, "", 200   # platform-level kinds (skill/extension): tier gate alone
        if owner != user_uuid:
            return False, (f"you can only sign off your own {kind} — "
                           f"this one belongs to another user"), 403
        return True, "", 200
    except Exception as e:  # noqa: BLE001 — authz must fail CLOSED, never open
        logger.warning(f"trust: ownership check failed for {kind}/{artifact_id}: {e}")
        return False, "ownership could not be verified — sign-off refused", 403


@trust_bp.route("/v1/trust/agents", methods=["GET"])
@operation("trust.agents", effect="read", scope="own",
           summary="Every one of the caller's agents with its live trust state, in one call",
           summary_fields=("profile_id", "tag", "ifoc", "state", "composition_state"), count_by=("state", "ifoc"))
async def get_all_agent_trust():
    """Every one of the caller's agents with its live badge — one call for scoreboards/maps.

    Returns ``{agents: [{profile_id, tag, ifoc, state, composition_state, blocking, ingredients}]}``.
    Resolves compositionally per profile; degrades per-agent (a failed resolve → unsigned).
    """
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        profiles = get_config_manager().get_profiles(user.id) or []
        # Resolve OFF the event loop. The extracted body is synchronous CPU + SQLite
        # over every one of the caller's profiles (measured: ~0.5s for 30 profiles,
        # ~2.0s for 50). Run inline it pinned the single asyncio loop for that whole
        # window — a trivial 2 ms endpoint served concurrently took 1.8 s, and the
        # browser's module waterfall stalled behind it. SQLite is safe from a worker
        # because core/sqlite_util serialises every C-call process-wide; that lock
        # exists precisely so work like this can leave the loop.
        out = await asyncio.to_thread(_resolve_agent_trust_rows, profiles, user.id)
        return jsonify({"agents": out})
    except Exception as e:
        logger.error(f"trust: get_all_agent_trust failed: {e}", exc_info=True)
        return jsonify({"error": "Failed to resolve agent trust"}), 500




def _resolve_agent_trust_rows(profiles, user_id):
    """Pure, synchronous: profiles -> trust rows. Runs in a worker thread.

    The whole batch runs in ONE ``sqlite_util.reuse_connections()`` scope: resolving an
    agent opens ~10 connections (attestations, collections, KG, deployment), each costing
    more to open than the query it serves — 600+ opens for 64 agents was most of this
    endpoint's time.

    Extracted verbatim from get_all_agent_trust so it can leave the event loop. It
    touches no request-local state (every input is a plain value), and it keeps the
    per-agent degradation exactly as before: a failed resolve becomes `unsigned`
    rather than failing the whole batch.
    """
    from trusted_data_agent.core import sqlite_util
    resolved = resolve_profiles_shared(profiles, user_id)
    with sqlite_util.reuse_connections():
        return _resolve_agent_trust_rows_in_scope(profiles, user_id, resolved)


# ── one resolution for concurrent readers ──────────────────────────────────────
# The Trust page asks /v1/trust/agents and /v1/evals/agents/trust-behavior in the same
# parallel batch, and both resolve EVERY agent's trust — the same work twice, each slowing
# the other (measured on production: 0.4 s alone, 1.4 s side by side). A resolution already
# IN FLIGHT for the same caller and the same profile list is joined, never repeated.
# Nothing is cached: a request arriving after it finished resolves afresh (the
# requestCoalescer rule). The key is the profile list's identity — config_manager hands
# out its cached list, which a saved change replaces, and an in-flight list cannot be
# collected, so its id cannot be reused while it is a key.
_INFLIGHT: dict = {}
_INFLIGHT_LOCK = threading.Lock()


def resolve_profiles_shared(profiles, user_id):
    """``[(profile, AgentTrust | Exception)]`` for every profile with an id — joined with an
    identical resolution already running. Synchronous; for worker threads. Read-only."""
    key = (user_id, id(profiles), len(profiles))
    with _INFLIGHT_LOCK:
        entry = _INFLIGHT.get(key)
        owner = entry is None
        if owner:
            entry = {"done": threading.Event()}
            _INFLIGHT[key] = entry
    if not owner:
        entry["done"].wait()
        if "error" in entry:
            raise entry["error"]
        return entry["result"]
    try:
        from trusted_data_agent.core import sqlite_util
        out = []
        with sqlite_util.reuse_connections():
            for p in profiles:
                pid = p.get("id")
                if not pid:
                    continue
                try:
                    out.append((p, trust_comp.resolve_profile_trust(pid, user_id, profile=p)))
                except Exception as e:  # noqa: BLE001 — per-agent; each reader degrades its way
                    out.append((p, e))
        entry["result"] = out
        return out
    except BaseException as e:
        entry["error"] = e
        raise
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(key, None)
        entry["done"].set()


def _resolve_agent_trust_rows_in_scope(profiles, user_id, resolved):
    out = []
    for p, t in resolved:
        pid = p.get("id")
        try:
            if isinstance(t, Exception):
                raise t
            d = t.to_dict()
            row = {
                "profile_id": pid, "tag": p.get("tag"),
                "ifoc": p.get("profile_type"),
                "state": d["state"], "composition_state": d["composition_state"],
                "blocking": d["blocking"], "ingredients": d["ingredients"],
                # The TWO HALVES behind a composite state. Without them a `changed`
                # driven by DEPLOYMENT drift (model/tool-catalog/connectors moved on
                # this instance) is indistinguishable from a portable drift — and the
                # ring then offers "Re-sign" for a state only "Attest this deployment"
                # can move (Rule 8.5: the offered act must be the act that is owed).
                "portable_state": d.get("portable_state"),
                **({"deployment": d["deployment"]} if d.get("deployment") else {}),
            }
            # #22/#23 — the caveat recorded when this agent was signed, plus any
            # carried by a signed INGREDIENT. A `partial` signature ("every answer
            # gate is in observe mode", "content not fully scanned") rendered
            # identically to an earned one; the band qualifies the state, it never
            # becomes one (Rule 8.4).
            if d["state"] != "unsigned":
                cav = signed_caveat("agent", pid, owner_uuid=user_id)
                if cav:
                    row["caveat"] = cav
            ing_cav = []
            for ing in (d.get("ingredients") or []):
                if ing.get("state") == "unsigned":
                    continue
                c = signed_caveat(ing.get("kind"), ing.get("artifact_id"), owner_uuid=user_id)
                if c:
                    ing_cav.append({**c, "kind": ing.get("kind"),
                                    "artifact_id": ing.get("artifact_id"),
                                    "label": ing.get("label")})
            if ing_cav:
                row["ingredient_caveats"] = ing_cav
            out.append(row)
        except Exception as e:
            logger.debug(f"trust: batch resolve failed for {pid}: {e}")
            out.append({"profile_id": pid, "tag": p.get("tag"),
                        "ifoc": p.get("profile_type"), "state": "unsigned",
                        "composition_state": "unsigned", "blocking": [], "ingredients": []})
    return out

@trust_bp.route("/v1/trust/agent/<profile_id>", methods=["GET"])
@operation("trust.agent", effect="read", scope="own",
           summary="An agent's live trust state with the state of every ingredient it is built from (why it reads signed, changed or unsigned)",
           path={"profile_id": "the agent's (profile's) id"})
async def get_agent_trust(profile_id: str):
    """The agent's live trust badge + per-ingredient breakdown (⚪/🟢/🟠)."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        profile = get_config_manager().get_profile(profile_id, user.id)
        if not profile:
            return jsonify({"error": f"Profile '{profile_id}' not found"}), 404
        t = trust_comp.resolve_profile_trust(profile_id, user.id, profile=profile)
        return jsonify({
            "profile_id": profile_id,
            "tag": profile.get("tag"),
            "trust": t.to_dict(),
        })
    except Exception as e:
        logger.error(f"trust: get_agent_trust failed for {profile_id}: {e}", exc_info=True)
        return jsonify({"error": "Failed to resolve agent trust"}), 500


@trust_bp.route("/v1/trust/verify", methods=["POST"])
@operation("trust.verify", effect="read", scope="own",
           summary="Verify a stored signature cryptographically (whether it is intact and which key made it)",
           body={"kind": Arg("string", "the artefact kind (agent, skill, knowledge, ...)", required=True),
                 "artifact_id": Arg("string", "the artefact's id", required=True)})
async def verify_attestation():
    """Offline-verify a stored attestation's Ed25519 signature. Body: {kind, artifact_id}."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    try:
        body = await request.get_json() or {}
        kind, artifact_id = body.get("kind"), body.get("artifact_id")
        if not kind or not artifact_id:
            return jsonify({"error": "kind and artifact_id are required"}), 400
        return jsonify(trust_store.verify(kind, artifact_id, owner_uuid=user.id))
    except Exception as e:
        logger.error(f"trust: verify failed: {e}", exc_info=True)
        return jsonify({"error": "Failed to verify attestation"}), 500


# ── Per-artefact trust state (the "signed" badge shown next to any artefact name) ──

# Per-kind content resolution is canonical in core.trust.content (shared by sign-off,
# the badge state, and marketplace portability) — these are thin re-exports.
_extension_content = trust_content.extension_content


_knowledge_content = trust_content.knowledge_content


_knowledge_sample_texts = trust_content.knowledge_sample_texts


_planner_cases = trust_content.planner_cases


# The pack composition resolver is canonical in core.trust.pack (shared with marketplace
# portability); this is a thin re-export.
_pack_member_refs = trust_pack.member_refs


#: Sentinel: this artefact KIND fundamentally has no live-hash resolver (so a signed row
#: falls back to signed/unsigned only). Distinct from a resolver that RAN and could not
#: produce a hash — that must fail closed (TC-4), not report trusted.
_NO_LIVE_HASH = object()


def _artifact_live_hash(kind: str, artifact_id: str, user_uuid: str = None):
    """Current content hash for an artefact kind.

    Returns a hash string on success, ``_NO_LIVE_HASH`` when the kind has no resolver at all,
    or ``None`` when a resolver ran but could not compute a hash (unreadable content / raised).
    Callers must treat ``None`` as fail-closed, never as trusted.
    """
    try:
        if kind == "agent_pack":
            refs, _ = _pack_member_refs(artifact_id)
            return composition_hash(refs) if refs else None
        if kind == "agent":
            # Route through the ONE canonical agent hash so the badge agrees with
            # /v1/trust/agent/<id>. TC-1: tag space, so a coordinator agent resolves the
            # same hash it was signed under (its slaveProfiles are hashed via id→tag).
            from trusted_data_agent.core.trust import composition as _comp
            profile = _comp._load_profile(artifact_id, user_uuid)
            refs = _comp.agent_ingredient_refs(artifact_id, user_uuid, profile=profile)
            id_to_tag = _comp.profile_id_to_tag(user_uuid) if user_uuid else None
            return _comp.agent_version_hash(refs, profile, id_to_tag=id_to_tag)
        if kind in trust_content._LEAF:
            # leaf kinds (skill/ontology/extension/knowledge/planner/flow/dashboard/canvas)
            return trust_content.live_hash(kind, artifact_id)
        return _NO_LIVE_HASH   # a registered validator kind with no live-hash resolver
    except Exception as e:
        logger.debug(f"trust: live hash for {kind}/{artifact_id} failed: {e}")
    return None


def _resolve_artifact_state(kind: str, artifact_id: str, user_uuid: str = None) -> str:
    """Single-artefact badge state: 'unsigned' | 'trusted' | 'changed'."""
    from trusted_data_agent.core.trust.attestation import resolve_state, TrustState
    attested = trust_store.attested_hash(kind, artifact_id, owner_uuid=user_uuid)
    if not attested:
        return TrustState.UNSIGNED.value
    live = _artifact_live_hash(kind, artifact_id, user_uuid=user_uuid)
    if live is _NO_LIVE_HASH:
        # This kind genuinely has no live hash → signed/unsigned only, no tamper-evidence.
        return TrustState.TRUSTED.value
    if live is None:
        # TC-4: a resolver RAN and could not confirm the content (unreadable/raised). The
        # composition path emits such an ingredient as `unresolved` and BLOCKS; the badge must
        # not answer the opposite for the same fault. Fail closed to CHANGED — recoverable on
        # the next render, and a transient false-🟠 is honest where a false-🟢 over tampered
        # content is not.
        return TrustState.CHANGED.value
    return resolve_state(attested, live).value


def _known_kinds() -> frozenset:
    """Artefact kinds this API will answer for: every registered validator plus the
    composed kinds, which validate through their members rather than a validator."""
    from trusted_data_agent.core.trust.attestation import registered_kinds
    return registered_kinds() | {"agent_pack"}


def signed_caveat(kind: str, artifact_id: str, owner_uuid: Optional[str] = None) -> Optional[dict]:
    """The CAVEAT recorded when this artefact was signed — `{band, status, note}`.

    #22/#23. Every validator returns a 3-band verdict and the band is already stored in
    `trust_attestations.verdict_json`, but no surface read it back: a `partial` signature
    ("137 documents · content not fully scanned for injection", "every answer gate is in
    observe mode") rendered pixel-identical to an earned one.

    Deliberately NOT a new state (Rule 8.4: where a second channel exists, use it rather
    than a fifth state). `trusted` still means signed-and-intact; the band QUALIFIES it,
    exactly as `status`/`note` already qualify a sign-off dialog. `clean` returns None —
    a caveat that says "no caveat" is noise.
    """
    try:
        rec = trust_store.load_attestation(kind, artifact_id, owner_uuid=owner_uuid)
        verdict = (rec or {}).get("verdict") or {}
        band = verdict.get("band")
        if not band or band == "clean":
            return None
        return {"band": band,
                "status": verdict.get("status"),
                "note": verdict.get("note")}
    except Exception as e:  # noqa: BLE001 — a badge must render even if its caveat cannot
        # LOG it: a silent except here once hid a call to a store method that does not
        # exist, so the caveat never appeared and nothing said why — the same shape of
        # failure this fix exists to remove.
        logger.debug(f"trust: caveat unavailable for {kind}/{artifact_id}: {e}")
        return None


@trust_bp.route("/v1/trust/artifact/<kind>/<path:artifact_id>", methods=["GET"])
@operation("trust.artifact", effect="read", scope="own",
           summary="One artefact's signed state (signed, changed or unsigned)",
           path={"kind": "the artefact kind (agent, skill, knowledge, planner, ontology, data_product, action, flow, dashboard, canvas, ...)", "artifact_id": "the artefact's id"})
async def get_artifact_trust(kind: str, artifact_id: str):
    """The signed-state of a single artefact (for the badge next to its name)."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    known = _known_kinds()
    if kind not in known:
        # Never answer for a kind we don't know: 'unsigned' would be indistinguishable
        # from a real never-signed artefact, so a mis-wired badge would look honest.
        return jsonify({"error": "unknown_kind",
                        "message": f"'{kind}' is not a signable artefact kind",
                        "known_kinds": sorted(known)}), 400
    try:
        # TC-77: the one route in this file that had no wrapper — a store DB error
        # propagated as a framework 500 while every sibling degrades gracefully.
        state = _resolve_artifact_state(kind, artifact_id, user_uuid=user.id)
        out = {"kind": kind, "artifact_id": artifact_id, "state": state}
        # A caveat only means something on a signature that exists.
        if state != "unsigned":
            caveat = signed_caveat(kind, artifact_id, owner_uuid=user.id)
            if caveat:
                out["caveat"] = caveat
        return jsonify(out)
    except Exception as e:
        logger.error(f"trust: get_artifact_trust failed for {kind}/{artifact_id}: {e}",
                     exc_info=True)
        return jsonify({"error": "Failed to resolve artefact trust"}), 500


@trust_bp.route("/v1/trust/artifacts", methods=["POST"])
@operation("trust.artifacts", effect="read", scope="own",
           summary="The signed state of several artefacts in one call",
           body={"items": Arg("array", "the artefacts: [{kind, id}], at most 500", required=True)})
async def get_artifacts_trust():
    """Batch artefact states for a list. Body: {items: [{kind, id}]} → {states: {"kind:id": state}}."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    try:
        body = await request.get_json() or {}
        known = _known_kinds()
        out, unknown = {}, []
        for it in (body.get("items") or [])[:500]:
            k, i = it.get("kind"), it.get("id")
            if not (k and i):
                continue
            if k not in known:
                # Reported, not silently answered for — the batch still serves the rest.
                if k not in unknown:
                    unknown.append(k)
                continue
            out[f"{k}:{i}"] = _resolve_artifact_state(k, i, user_uuid=user.id)
        resp = {"states": out}
        if unknown:
            resp["unknown_kinds"] = sorted(unknown)
            logger.warning(f"trust: batch artefact request named unknown kind(s): {unknown}")
        return jsonify(resp)
    except Exception as e:
        logger.error(f"trust: get_artifacts_trust failed: {e}", exc_info=True)
        return jsonify({"error": "Failed to resolve artefact trust"}), 500


# ── PROP-009 §4.1 — attestation history, the review list, signing keys ──────────

def _is_admin(user) -> bool:
    try:
        from trusted_data_agent.auth.admin import get_user_tier
        return get_user_tier(user) == "admin"
    except Exception:  # noqa: BLE001 — unknown tier is not admin
        return False


@trust_bp.route("/v1/trust/history/<kind>/<path:artifact_id>", methods=["GET"])
@operation("trust.history", effect="read", scope="own",
           summary="Every recorded trust event of one artefact, newest first (signed, reinstated, revoked, retired)",
           path={"kind": "the artefact kind (agent, skill, knowledge, planner, ontology, data_product, action, flow, dashboard, canvas, ...)", "artifact_id": "the artefact's id"})
async def get_attestation_history(kind: str, artifact_id: str):
    """Every recorded event for one artifact — sign, reinstate, revoke, retire — newest
    first. For an owner-scoped kind (an agent) the caller's own history only."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if kind not in _known_kinds() and kind != "agent_deployment":
        return jsonify({"error": "unknown_kind", "known_kinds": sorted(_known_kinds())}), 400
    rows = trust_store.history(kind, artifact_id, owner_uuid=user.id)
    for r in rows:
        r.pop("verdict_json", None)
        r["key_status"] = trust_store.key_status(r.get("signer_fingerprint"))
    return jsonify({"kind": kind, "artifact_id": artifact_id, "history": rows})


@trust_bp.route("/v1/trust/unassigned", methods=["GET"])
@operation("trust.unassigned", effect="read", scope="cross_user", tier="admin",
           summary="Agent signatures the owner migration could not attribute to an account, each with the reason")
async def get_unassigned_attestations():
    """Admin: agent attestations the owner-scoping migration could not attribute, each
    with the reason in words. Listed for review — never assigned by guess."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if not _is_admin(user):
        return jsonify({"error": "admin only"}), 403
    rows = trust_store.unassigned_agent_attestations()
    for r in rows:
        r.pop("verdict_json", None)
        r.pop("signer_public_key", None)
    return jsonify({"unassigned": rows})


@trust_bp.route("/v1/trust/unassigned/assign", methods=["POST"])
@operation("trust.unassigned_assign", effect="human_only", scope="cross_user", tier="admin", governs=True,
           summary="Give an unattributed agent signature to the account that owns the agent — an administrator's act",
           body={"kind": Arg("string", "the artefact kind", required=True), "artifact_id": Arg("string", "the artefact's id", required=True),
                 "owner_uuid": Arg("string", "the account that owns the agent", required=True)})
async def assign_unassigned_attestation():
    """Admin: give an unassigned agent attestation to the account that owns that agent.
    Body ``{kind, artifact_id, owner_uuid}``. Refused unless that account holds the agent."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    if not _is_admin(user):
        return jsonify({"error": "admin only"}), 403
    body = await request.get_json() or {}
    kind, aid, owner = body.get("kind"), body.get("artifact_id"), body.get("owner_uuid")
    if not (kind and aid and owner):
        return jsonify({"error": "kind, artifact_id and owner_uuid are required"}), 400
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        if not get_config_manager().get_profile(aid, owner):
            return jsonify({"error": "not_held",
                            "message": "that account does not hold an agent with this id"}), 409
    except Exception:  # noqa: BLE001
        return jsonify({"error": "could not read that account's agents"}), 500
    if not trust_store.assign_owner(kind, aid, owner, actor_uuid=user.id):
        return jsonify({"error": "not_assignable",
                        "message": "the attestation is not unassigned, or that account "
                                   "already has one for this agent"}), 409
    return jsonify({"assigned": True, "kind": kind, "artifact_id": aid, "owner_uuid": owner})


@trust_bp.route("/v1/trust/keys", methods=["GET"])
@operation("trust.keys", effect="read", scope="instance",
           summary="Every public key that has signed on this deployment: active, retired or carried from another signer")
async def get_signing_keys():
    """Every public key that has signed on this deployment: active, retired (an earlier key
    of this deployment) or carried (another signer's, seen on a reinstated attestation)."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify({"keys": trust_store.list_keys()})
