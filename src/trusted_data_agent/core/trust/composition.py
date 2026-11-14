"""
Agent composition → live trust (Tier 5.2, Phase 1 wiring).

Bridges the pure trust core to a *real agent* (a profile): reads the profile's
actual ingredients — its bound ontology (active KG) and its active skills — hashes
each, looks up their attestations, and resolves the agent's badge. This is the
step that turns "here's the trust engine" into "here's *this agent's* real trust
status."

Ingredient set (the validated/composable kinds, §3.1):
  * ``ontology``  — the profile's active KG (via ``kg_profile_assignments``)
  * ``skill``     — each active entry in ``skillsConfig.skills``
  * ``knowledge`` — each bound knowledge repository actually used for retrieval
  * ``planner``   — each bound planner repository actually used for planning
Infrastructure references (LLM / MCP / backends) travel with the agent but are
not validated ingredients (§5); extensions + sub-profiles are later phases.

**Why repositories are ingredients.** The compositional promise is "a signed agent
contains only signed ingredients". A Focus agent *is* its corpus and an Optimize agent
is steered by its champion cases, so an agent whose repositories sat outside the
composition could resolve 🟢 while its entire substance was unscreened and unsigned —
and re-pointing it at another corpus would not disturb the badge. Repositories are
therefore first-class ingredients: the bottom-up gate refuses to sign the agent until
the repository it consumes is itself signed, and a corpus edit honestly flips 🟢 → 🟠.

Applicability mirrors the runtime, never a rule invented here: a knowledge repository
counts only while ``knowledgeConfig.enabled`` (that is what gates retrieval), and a
planner repository counts only for *planning* agent classes — the same convention
already encoded in ``planner.py``, ``agent_pack_manager.py`` and the platform-ontology
projection. Attesting an artefact the agent never consults would make the badge claim
more than it verifies.

The pure assembly (`build_agent_ingredients`) is testable with fixtures; the fetch
helpers read the real auth DB / config / skill manager and fail *soft* (a source
that can't be read is simply omitted, never crashes a resolution).

Design: docs/roadmap/TIER_5.2_TRUST_MEMBRANE.md §1, §3, §9.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

from trusted_data_agent.core import sqlite_util
from . import store as _store
from .attestation import (
    IngredientRef, AgentTrust, TrustState, content_hash, composition_hash,
    resolve_agent_trust, agent_signable, resolve_state, compose_agent_state,
    ingredient_state,
)
from .attestation import _CAPTURE as _att_capture
from . import kg_revision as _kg_rev

logger = logging.getLogger("quart.app")

AGENT_KIND = "agent"
ONTOLOGY_KIND = "ontology"
SKILL_KIND = "skill"
KNOWLEDGE_KIND = "knowledge"
PLANNER_KIND = "planner"

#: Agent classes whose planner (champion-case) repository actually steers execution.
#: Mirrors the convention already encoded in ``planner.py`` (RAG retrieval filtering),
#: ``agent_pack_manager`` (``uses_tools``) and ``platform_ontology.projection``.
PLANNING_PROFILE_TYPES = frozenset({"tool_enabled", "genie"})

#: (profile, collection) pairs already reported as dangling. Trust composition runs on
#: every badge resolution, so an unconditional warning per call buries every other
#: warning in the log under identical repeats. First occurrence warns; repeats debug.
#: Process-local and bounded by the number of real misconfigurations, so it needs no
#: eviction — a fixed cap guards against a pathological profile churn all the same.
_DANGLING_SEEN: set = set()
_DANGLING_SEEN_CAP = 512


# ── pure assembly (testable) ─────────────────────────────────────────────────

def build_agent_ingredients(
    ontology: Optional[Dict],
    skills: List[Dict],
    attest_lookup: Callable[[str, str], Optional[str]],
    repos: Optional[List[Dict]] = None,
) -> List[IngredientRef]:
    """Assemble an agent's IngredientRefs from already-fetched ingredient data.

    ``ontology`` = ``{"kg_id", "content_hash"}`` or None; ``skills`` = list of
    ``{"skill_id", "content_hash"}``; ``repos`` = list of
    ``{"kind", "collection_id", "content_hash", "unresolved"?}`` for the bound
    knowledge/planner repositories. ``attest_lookup(kind, id) -> attested_hash``
    (None = unsigned). Pure — no I/O.

    Each source may carry an optional ``name``, passed through as the ingredient's
    ``label`` so a surface that asks the operator to sign a blocker can name it. A
    skill's id is already readable, so it doubles as its own label.
    """
    refs: List[IngredientRef] = []
    if ontology and ontology.get("kg_id"):
        kid = ontology["kg_id"]
        refs.append(IngredientRef(ONTOLOGY_KIND, kid, ontology["content_hash"],
                                  attest_lookup(ONTOLOGY_KIND, kid),
                                  label=ontology.get("name")))
    for s in skills:
        sid = s.get("skill_id")
        if not sid:
            continue
        refs.append(IngredientRef(SKILL_KIND, sid, s["content_hash"],
                                  attest_lookup(SKILL_KIND, sid),
                                  label=s.get("name") or str(sid)))
    for r in (repos or []):
        cid, kind = r.get("collection_id"), r.get("kind")
        if not cid or kind not in (KNOWLEDGE_KIND, PLANNER_KIND):
            continue
        refs.append(IngredientRef(kind, cid, r.get("content_hash") or "",
                                  attest_lookup(kind, cid),
                                  bool(r.get("unresolved")),
                                  label=r.get("name"),
                                  finding=r.get("finding") or None))
    return refs


# ── fetch helpers (real sources; fail-soft) ──────────────────────────────────

def _active_kg_id(profile_id: str, user_uuid: str,
                  db_path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """The KG bound as active to a profile, as ``(kg_id, name)``.

    Assignment table first, legacy ``kg_metadata`` as the fallback. The name comes from
    the same open connection (a kg_id is a uuid — unusable in a sentence asking someone
    to sign it), and a missing name never fails the lookup.
    """
    try:
        conn = sqlite_util.connect(_store._db_path(db_path))
        try:
            row = conn.execute(
                "SELECT kg_id FROM kg_profile_assignments "
                "WHERE assigned_profile_id=? AND user_uuid=? AND is_active=1 LIMIT 1",
                (profile_id, user_uuid),
            ).fetchone()
            if not row:
                # The legacy fallback MUST be scoped to the user as well: the bootstrap
                # profile ids (``profile-default-chat`` etc.) are shared by every user,
                # so an unscoped lookup would hand one user's agent ANOTHER user's graph
                # as its ingredient (seen live: a demo agent blocked from signing by a
                # test user's subscribed copy it had never been bound to).
                row = conn.execute(
                    "SELECT kg_id FROM kg_metadata "
                    "WHERE profile_id=? AND user_uuid=? AND is_active=1 LIMIT 1",
                    (profile_id, user_uuid),
                ).fetchone()
            if not row:
                return (None, None)
            kg_id = row[0]
            try:
                nrow = conn.execute(
                    "SELECT name FROM kg_metadata WHERE kg_id=? LIMIT 1", (kg_id,)).fetchone()
                return (kg_id, nrow[0] if nrow else None)
            except Exception:
                return (kg_id, None)   # naming is a nicety; the ingredient still resolves
        finally:
            conn.close()
    except Exception as e:
        logger.debug(f"trust.composition: active KG lookup failed: {e}")
        return (None, None)


def _fetch_kg_rows(kg_id: str, db_path: Optional[str]) -> Tuple[List[tuple], List[tuple]]:
    """The raw rows an ontology's content hash is computed from — nothing else.

    ONE read, shared by `load_kg_graph` (which decodes them) and `kg_graph_hash`
    (which fingerprints them first), so the two can never read different columns.
    """
    conn = sqlite_util.connect(_store._db_path(db_path))
    try:
        ent_rows = conn.execute(
            "SELECT name, entity_type, properties_json FROM kg_entities WHERE kg_id=?",
            (kg_id,)).fetchall()
        rel_rows = conn.execute(
            "SELECT r.relationship_type, se.name, se.entity_type, te.name, te.entity_type "
            "FROM kg_relationships r "
            "JOIN kg_entities se ON r.source_entity_id=se.id "
            "JOIN kg_entities te ON r.target_entity_id=te.id WHERE r.kg_id=?",
            (kg_id,)).fetchall()
        return list(ent_rows), list(rel_rows)
    finally:
        conn.close()


def _graph_from_rows(ent_rows: List[tuple], rel_rows: List[tuple]) -> Tuple[List[Dict], List[Dict]]:
    ents = [{"name": r[0], "entity_type": r[1],
             "properties": (json.loads(r[2]) if r[2] else {})}
            for r in ent_rows]
    rels = [{"relationship_type": r[0], "source_name": r[1], "source_type": r[2],
             "target_name": r[3], "target_type": r[4]}
            for r in rel_rows]
    return ents, rels


def load_kg_graph(kg_id: str, db_path: Optional[str]) -> Tuple[List[Dict], List[Dict]]:
    """Load a KG's entities + relationships in the conformance-validator shape."""
    return _graph_from_rows(*_fetch_kg_rows(kg_id, db_path))


# ── The ontology hash cache ─────────────────────────────────────────────────────
# Computing `kg_content_hash` means decoding every entity's properties, sorting on a
# re-encoded JSON key and hashing the whole graph: ~0.7-1.1 s for one industry-model
# graph, repeated for EVERY agent on EVERY badge request (/v1/trust/agents,
# trust-behavior, the KG cards). The result only changes when the graph does.
#
# The cache key is a fingerprint of the RAW ROWS — never a row count or a timestamp.
# Relationships carry no `updated_at`, and several writers update rows in place
# without touching one (the type migrations), so a count/time key could serve a stale
# hash and leave a changed ontology reading 🟢. The fingerprint covers strictly MORE
# than the hash reads (entity ids besides name/type/properties; each relationship's
# type and endpoint ids), so any change the hash can see changes the key, whoever
# wrote it. It is cheap because it skips the join, the JSON decode, the sort and the
# re-encode; the read itself is still paid — it IS the check.
#
# Two deliberate misses, never a wrong answer: rows returned in a different order (a
# reordered copy) fingerprint differently and simply recompute; and a relationship
# whose endpoint is NOT among this graph's own entity rows (a name the fingerprint
# cannot see) bypasses the cache entirely.
_KG_HASH_CACHE: "OrderedDict[str, str]" = OrderedDict()
_KG_HASH_CACHE_MAX = 256
_KG_HASH_LOCK = threading.Lock()


def _kg_fingerprint(kg_id: str, db_path: Optional[str]) -> Tuple[Optional[str], int]:
    """(fingerprint, entity count); fingerprint None ⇒ not cacheable, compute fresh.

    The rows are serialised with ``pickle``: it is lossless (the bytes decode back to
    exactly these rows), so two different graphs can never share a fingerprint — the
    property that makes the cache exact. pickle's back-references follow object
    identity, so the same rows could in principle serialise differently; that would
    only ever cost a cache miss, never a wrong hash. Measured 2.6× faster than repr.
    """
    conn = sqlite_util.connect(_store._db_path(db_path))
    try:
        ent_rows = conn.execute(
            "SELECT id, name, entity_type, properties_json FROM kg_entities WHERE kg_id=?",
            (kg_id,)).fetchall()
        rel_rows = conn.execute(
            "SELECT relationship_type, source_entity_id, target_entity_id "
            "FROM kg_relationships WHERE kg_id=?", (kg_id,)).fetchall()
    finally:
        conn.close()
    own = {r[0] for r in ent_rows}
    for r in rel_rows:
        if r[1] not in own or r[2] not in own:
            return None, len(ent_rows)
    h = hashlib.sha256(b"kgfp1\n")
    h.update(pickle.dumps((ent_rows, rel_rows), protocol=5))
    return h.hexdigest(), len(ent_rows)


def kg_graph_hash(kg_id: str, db_path: Optional[str]) -> Tuple[str, int]:
    """(canonical content hash, entity count) for a stored KG — cached, exact.

    Byte-identical to ``kg_content_hash(*load_kg_graph(kg_id, db_path))``. Three tiers,
    each exact:

    1. the graph's content REVISION (``kg_revision`` — trigger-maintained, bumped by every
       write to its rows): a hash recorded at the current revision is returned after ONE
       indexed row read, in memory or from ``kg_content_hash_cache`` (so a restart does not
       re-hash every industry graph on the first badge request);
    2. the raw-row fingerprint (below) — reads the graph, skips the decode/sort/encode;
    3. the full computation.

    A hash is recorded at a revision only when the graph is self-contained (every edge
    endpoint is one of its own entities — the same condition the fingerprint needs) and the
    revision did not move while it was computed. While a ``content_capture`` is open the
    caches are bypassed: the capture exists to see the exact bytes ``content_hash`` hashes.
    """
    if _att_capture.get() is not None:
        ent_rows, rel_rows = _fetch_kg_rows(kg_id, db_path)
        return kg_content_hash(*_graph_from_rows(ent_rows, rel_rows)), len(ent_rows)
    path = _store._db_path(db_path)
    rv = None
    conn = sqlite_util.connect(path)
    try:
        rv = _kg_rev.revision(conn, path, kg_id)
        if rv is not None:
            hit = _rev_cache_get(conn, path, kg_id, rv)
            if hit is not None:
                return hit
    finally:
        conn.close()
    value, n, self_contained = _kg_graph_hash_from_rows(kg_id, db_path)
    if rv is not None and self_contained:
        _rev_cache_put(path, kg_id, rv, value, n)
    return value, n


# ── tier 1: hash at a content revision ──────────────────────────────────────────
_KG_REV_CACHE: Dict[Tuple[str, str], Tuple[Tuple[int, int], str, int]] = {}
_KG_REV_CACHE_MAX = 1024
_KG_HASH_CODE_VERSION: Optional[str] = None


def _kg_hash_code_version() -> str:
    """A persisted hash is only as good as the code that computed it. This digest covers
    the source of every module that decides the ontology hash (row read, decode, the
    stacked-layer exclusions, canonical encoding), so ANY edit to them invalidates every
    persisted row — a deploy can recompute, never serve a hash an older rule produced."""
    global _KG_HASH_CODE_VERSION
    if _KG_HASH_CODE_VERSION is None:
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        h = hashlib.sha256(b"kghashcode1\n")
        for rel in ("composition.py", "attestation.py",
                    os.path.join("..", "..", "kgstore", "types.py")):
            try:
                with open(os.path.join(here, rel), "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"missing:" + rel.encode())
        _KG_HASH_CODE_VERSION = h.hexdigest()[:32]
    return _KG_HASH_CODE_VERSION


def _rev_cache_get(conn, path: str, kg_id: str, rv) -> Optional[Tuple[str, int]]:
    mem = _KG_REV_CACHE.get((path, kg_id))
    if mem is not None and mem[0] == rv:
        return mem[1], mem[2]
    try:
        row = conn.execute(
            "SELECT hash, n FROM kg_content_hash_cache "
            "WHERE kg_id=? AND epoch=? AND rev=? AND code_ver=?",
            (kg_id, rv[0], rv[1], _kg_hash_code_version())).fetchone()
    except Exception:  # noqa: BLE001 — the table is created on first write
        row = None
    if row is None:
        return None
    with _KG_HASH_LOCK:
        _KG_REV_CACHE[(path, kg_id)] = (rv, row[0], int(row[1]))
    return row[0], int(row[1])


def _rev_cache_put(path: str, kg_id: str, rv, value: str, n: int) -> None:
    conn = sqlite_util.connect(path)
    try:
        if _kg_rev.revision(conn, path, kg_id) != rv:
            return   # the graph moved while it was hashed — record nothing
        with _KG_HASH_LOCK:
            if len(_KG_REV_CACHE) >= _KG_REV_CACHE_MAX:
                _KG_REV_CACHE.clear()
            _KG_REV_CACHE[(path, kg_id)] = (rv, value, n)
        try:
            conn.execute(
                "INSERT INTO kg_content_hash_cache(kg_id, epoch, rev, code_ver, hash, n) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(kg_id) DO UPDATE SET epoch=excluded.epoch, "
                "rev=excluded.rev, code_ver=excluded.code_ver, hash=excluded.hash, n=excluded.n",
                (kg_id, rv[0], rv[1], _kg_hash_code_version(), value, n))
            conn.commit()
        except Exception as e:  # noqa: BLE001 — a read-only db still serves from memory
            logger.debug(f"trust.composition: kg hash not persisted for {kg_id}: {e}")
    finally:
        conn.close()


def _kg_graph_hash_from_rows(kg_id: str, db_path: Optional[str]) -> Tuple[str, int, bool]:
    """Tiers 2 + 3: (hash, entity count, self-contained)."""
    key, _n = _kg_fingerprint(kg_id, db_path)
    if key is not None:
        with _KG_HASH_LOCK:
            hit = _KG_HASH_CACHE.get(key)
            if hit is not None:
                _KG_HASH_CACHE.move_to_end(key)
                return hit, _n, True
    ent_rows, rel_rows = _fetch_kg_rows(kg_id, db_path)
    value = kg_content_hash(*_graph_from_rows(ent_rows, rel_rows))
    if key is not None:
        with _KG_HASH_LOCK:
            _KG_HASH_CACHE[key] = value
            _KG_HASH_CACHE.move_to_end(key)
            while len(_KG_HASH_CACHE) > _KG_HASH_CACHE_MAX:
                _KG_HASH_CACHE.popitem(last=False)
    return value, len(ent_rows), key is not None


def kg_content_hash(entities: List[Dict], relationships: List[Dict]) -> str:
    """The canonical content hash of an ontology ingredient (entities + relationships).

    **Order-independent** — entities/relationships are sorted by a stable content key
    before hashing, so the hash is a true *content* identity: two copies of the same KG
    (e.g. after a marketplace deep-copy that reorders rows) yield the same hash. This is
    what lets the portability honesty gate recognise an identical KG across an id remap.

    **Data products are EXCLUDED (OBDA-READ Phase A4b).** The ontology's signable content is
    the KNOWLEDGE MODEL; a `data_product` node is a consumption contract STACKED ON it,
    protected by its own `data_product` attestation (which binds the manifest + its
    validation proof). Counting it here double-counted the same content and created a
    rubber-stamp loop: authoring or editing any product flipped its own ontology to
    CHANGED, and the bottom-up rule then demanded an ontology re-sign that attested
    nothing new — the alarm-fatigue anti-pattern. A graph carrying no products hashes
    byte-identically to before (the filter no-ops), so no existing attestation moves.

    **The AUTHORED layer is EXCLUDED too (PROP-008 A5 / §3a, §4.6).** An extension
    (`properties.layer == "authored"`) is tier 1 stacked on the base: signed on its own
    as `ontology_extension` with the base's attested hash INSIDE its signature. Counting
    it here would let a subscriber's draft move a seal the subscriber does not own —
    and break every reinstatement of the publisher's signature. Every edge touching an
    authored node goes with it (an extension's edges are sourced at its nodes — the
    additive-only rule — so they are the extension's content, never the base's). A graph
    with no authored node hashes byte-identically to before.
    """
    # OBDA-ACT (PROP-008 B1): an `action_type` is a write contract STACKED ON the
    # model — like a data product it carries its own signature (manifest + the A1–A6
    # proof). The ledger table an action provisions (a `ddl_artifact` with
    # `purpose: action_ledger`) is the ACTION's structure, not the model's. Neither moves
    # the ontology's seal; a graph with no action type hashes byte-identically.
    from trusted_data_agent.kgstore.types import is_extension_layer as _ext_layer, is_stacked_contract
    authored = {(str(e.get("name", "")), str(e.get("entity_type", "")))
                for e in (entities or [])
                if _ext_layer(e.get("properties")) or is_stacked_contract(e)}
    ents = sorted((e for e in (entities or [])
                   if e.get("entity_type") != "data_product"
                   and (str(e.get("name", "")), str(e.get("entity_type", ""))) not in authored),
                  key=lambda e: (
        str(e.get("entity_type", "")), str(e.get("name", "")),
        json.dumps(e.get("properties", {}), sort_keys=True, default=str)))
    rels = sorted((r for r in (relationships or [])
                   if r.get("relationship_type") not in ("member", "acts_on")
                   and r.get("source_type") != "data_product"
                   and r.get("target_type") != "data_product"
                   and (str(r.get("source_name", "")), str(r.get("source_type", ""))) not in authored
                   and (str(r.get("target_name", "")), str(r.get("target_type", ""))) not in authored),
                  key=lambda r: (
        str(r.get("relationship_type", "")), str(r.get("source_name", "")),
        str(r.get("source_type", "")), str(r.get("target_name", "")),
        str(r.get("target_type", ""))))
    return content_hash({"entities": ents, "relationships": rels})


# Physical artifacts stacked on the model: the ledger an action provisions (B1) and the
# performance artifacts a workload proposal lands (C1 — statistics, join indexes). Each is
# protected by its own receipt; none is the knowledge model, so none moves its seal.
from trusted_data_agent.kgstore.types import STACKED_ARTIFACT_PURPOSES as _STACKED_ARTIFACT_PURPOSES  # noqa: E402


def _ontology_ingredient(profile_id: str, user_uuid: str, db_path: Optional[str],
                         profile: Optional[Dict] = None) -> Optional[Dict]:
    kg_id, kg_name = _active_kg_id(profile_id, user_uuid, db_path)
    if not kg_id:
        return None
    try:
        # Model B: a released-mode agent's ontology IS the released view — the seal must
        # describe what the agent reads. The ingredient keeps the live kg_id (that is what
        # the ontology's own attestation is keyed on); only the CONTENT hashed changes.
        read_id = kg_id
        try:
            from trusted_data_agent.core import kg_release as _rel
            read_id = _rel.effective_kg_id(kg_id, profile, user_uuid, db_path) or kg_id
        except Exception:  # noqa: BLE001
            read_id = kg_id
        chash, _n = kg_graph_hash(read_id, db_path)
        return {"kg_id": kg_id, "content_hash": chash, "name": kg_name,
                "released_view": read_id if read_id != kg_id else None}
    except Exception as e:
        logger.debug(f"trust.composition: ontology hash failed for {kg_id}: {e}")
        return None


def _skill_ingredients(profile: Dict) -> List[Dict]:
    """Active skills from ``skillsConfig.skills`` (active=True), hashed by content.

    The hash comes from THE one resolver (`trust.content.skill_content`) — never
    re-computed here, so a progressive skill's library payload is part of the
    agent's ingredient hash exactly as it is part of the skill's own badge
    (PROP-005 §4; the seal must not lie). Lazy import: content.py imports this
    module at module level.
    """
    out: List[Dict] = []
    try:
        entries = (((profile or {}).get("skillsConfig") or {}).get("skills")) or []
        active = [e.get("id") for e in entries if isinstance(e, dict) and e.get("active") and e.get("id")]
        if not active:
            return out
        from trusted_data_agent.core.trust.content import skill_content
        for sid in active:
            _c, vhash = skill_content(sid)
            if vhash is None:
                continue  # fail-soft: a skill we can't read is simply omitted
            out.append({"skill_id": sid, "content_hash": vhash})
    except Exception as e:
        logger.debug(f"trust.composition: skill ingredient assembly failed: {e}")
    return out


def bound_repository_ids(profile: Dict) -> List[int]:
    """Every collection id this profile references, from either binding field.

    ``knowledgeConfig.collections`` and ``ragCollections`` overlap (a Focus profile
    lists its knowledge repo in both), so the union is de-duplicated and the *kind* is
    decided by each collection's own ``repository_type`` — never by which field
    happened to name it.
    """
    out: List[int] = []
    kc = ((profile or {}).get("knowledgeConfig") or {}).get("collections") or []
    for entry in kc:
        cid = entry.get("id") if isinstance(entry, dict) else entry
        if cid is not None:
            out.append(cid)
    for cid in ((profile or {}).get("ragCollections") or []):
        if cid is not None:
            out.append(cid)
    seen, ids = set(), []
    for cid in out:
        try:
            cid_i = int(cid)
        except (TypeError, ValueError):
            continue
        if cid_i not in seen:
            seen.add(cid_i)
            ids.append(cid_i)
    return ids


def _repo_ingredients(profile: Dict, user_uuid: Optional[str] = None) -> List[Dict]:
    """The bound knowledge/planner repositories this agent actually consumes.

    ``user_uuid`` is the agent's OWNER. A bound collection the owner cannot read
    (``CollectionDatabase.can_user_read`` — the retrieval gate's own rule) is runtime-inert:
    the agent never retrieves from it, so it is not an ingredient. It is a configuration
    fault of the same class as a dangling binding and is handled the same way — logged once,
    skipped. Counting it produced a sign-gate block the owner could never clear (the
    bootstrap template shipped the admin's planner id into every account; TC-37). With
    ``user_uuid=None`` (legacy callers, tests) no access check is applied.

    Emptiness and unreadability are deliberately *not* the same outcome. A repository
    with no content has nothing to attest, so omitting it is honest and keeps the
    default (empty) planner repo from making every agent unsignable. A repository that
    demonstrably holds content whose hash we could not compute is a failure, and is
    emitted ``unresolved`` so it blocks rather than silently vanishing — otherwise a
    transient read error would quietly reopen exactly the gap this closes.
    """
    out: List[Dict] = []
    try:
        from trusted_data_agent.core.collection_db import get_collection_db
        from . import content as _content
        db = get_collection_db()
        kc = (profile or {}).get("knowledgeConfig") or {}
        ptype = (profile or {}).get("profile_type")
        is_planning = ptype in PLANNING_PROFILE_TYPES
        # TC-3: knowledge applicability MUST mirror the runtime retrieval gate, in BOTH
        # directions. rag_focused retrieves unconditionally (focus_engine ignores `enabled`)
        # → always count; every other class gates on knowledgeConfig.enabled with DEFAULT
        # FALSE (the ideate/conversation engines and the knowledge_context module all read
        # get("enabled", False)). The old `is not False` default-True over-counted a repo a
        # non-Focus agent never consults (badge over-claim + spurious sign-gate block) AND —
        # worse — dropped a Focus agent's corpus whenever `enabled` was explicitly False,
        # letting it sign 🟢 without its substance (the F1 hole, reopened).
        knowledge_enabled = True if ptype == "rag_focused" else bool(kc.get("enabled", False))

        for cid in bound_repository_ids(profile):
            coll = db.get_collection_by_id(cid)
            if not coll:
                # A binding to a collection that no longer exists is a configuration
                # fault, not an ingredient — it must not silently degrade this agent's
                # badge (the agent is not less trustworthy; it is misconfigured).
                #
                # Logged ONCE per (profile, collection) per process. Trust composition
                # runs on every badge resolution, so an unconditional warning emitted
                # ~40 identical lines per session — which does not make the fault more
                # visible, it makes every OTHER warning less visible. The first line
                # carries the full fact; repeats drop to debug.
                _key = (str((profile or {}).get("id") or (profile or {}).get("tag") or "?"), str(cid))
                _msg = (f"trust.composition: profile {(profile or {}).get('tag') or '?'} is bound to "
                        f"collection {cid}, which does not exist — skipped as an ingredient. "
                        f"Its retrieval is silently inert; re-point or remove the binding.")
                if _key in _DANGLING_SEEN:
                    logger.debug(_msg)
                else:
                    if len(_DANGLING_SEEN) < _DANGLING_SEEN_CAP:
                        _DANGLING_SEEN.add(_key)
                    logger.warning(_msg)
                continue
            if user_uuid is not None and not db.can_user_read(coll, user_uuid):
                # TC-37: runtime-inert binding (owner has no read access) — not an ingredient.
                _key = ("unreadable", str((profile or {}).get("id") or (profile or {}).get("tag") or "?"), str(cid))
                _msg = (f"trust.composition: profile {(profile or {}).get('tag') or '?'} is bound to "
                        f"collection {cid} ('{coll.get('name')}'), which its owner cannot read — "
                        f"skipped as an ingredient. Its retrieval is silently inert; re-point the binding.")
                if _key in _DANGLING_SEEN:
                    logger.debug(_msg)
                else:
                    if len(_DANGLING_SEEN) < _DANGLING_SEEN_CAP:
                        _DANGLING_SEEN.add(_key)
                    logger.warning(_msg)
                continue
            rtype = (coll.get("repository_type") or "").strip().lower()
            # Decide APPLICABILITY before any failure-prone content read, so a hashing failure
            # can only ever block a repo the agent actually consumes (TC-25 over-block guard).
            if rtype == KNOWLEDGE_KIND:
                if not knowledge_enabled:
                    continue
            elif rtype == PLANNER_KIND:
                if not is_planning:
                    continue
            else:
                continue

            # TC-20/TC-25: this repo IS consumed by the agent, so a failure to resolve it must
            # BLOCK (emit `unresolved`), never silently vanish — and never abort the loop and
            # drop every repo after it. The content helpers already fail-soft to a None hash;
            # this nested guard catches the rarer case where the helper itself raises.
            try:
                finding = None
                if rtype == KNOWLEDGE_KIND:
                    docs, _model, chash, _c = _content.knowledge_content(str(cid))
                    # `document_count` is an independent existence signal, so "no documents"
                    # is never confused with "the content helper failed".
                    has_content = bool(docs) or int(coll.get("document_count") or 0) > 0
                else:  # PLANNER_KIND
                    cases, chash = _content.planner_cases(str(cid))
                    # Planner Health: name what holds this repository's signature back
                    # (the cases, their labels) on the ingredient itself — a pure regex
                    # scan over cases already in hand, so the Trust Circle can say
                    # "remove THIS case" instead of "the planner is not signable".
                    try:
                        from .validators import planner_finding_summary
                        finding = planner_finding_summary(cases) if cases else None
                    except Exception:  # noqa: BLE001 — a finding is evidence, never a gate here
                        finding = None
                    # TC-21: empty-vs-unreadable asymmetry. `planner_cases` returns a hash only
                    # when the case dir was fully readable; a populated-but-corrupt/​unreadable
                    # repo yields (cases, None). `planner_has_files` is the independent existence
                    # signal so an unreadable populated repo → has_content True + chash None →
                    # emitted `unresolved` (blocks), never mistaken for an empty repo (omitted).
                    has_content = bool(cases) or _content.planner_has_files(str(cid))
            except Exception as e:  # noqa: BLE001 — a consumed repo must fail closed, not vanish
                logger.warning(
                    f"trust.composition: {rtype} repository {cid} could not be resolved "
                    f"({e}) — emitted as unresolved so it blocks signing")
                out.append({"kind": rtype, "collection_id": str(cid), "content_hash": "",
                            "unresolved": True, "name": coll.get("name")})
                continue

            if not has_content:
                continue
            out.append({"kind": rtype, "collection_id": str(cid),
                        "content_hash": chash or "", "unresolved": not chash,
                        # Already in hand from the collection row — no extra query.
                        "name": coll.get("name"),
                        **({"finding": finding} if finding else {})})
            if not chash:
                logger.warning(
                    f"trust.composition: {rtype} repository {cid} holds content but could not "
                    f"be hashed — emitted as unresolved so it blocks signing")
    except Exception as e:  # noqa: BLE001 — trust resolution must never raise
        # TC-20: escalate from DEBUG. A raise here silently narrows the composition (drops an
        # entire ingredient class) while the badge still reads intact — the exact fail-soft the
        # connector-state fix removed. Visible at WARNING so a narrowed seal is never silent.
        logger.warning(f"trust.composition: repository ingredient assembly failed: {e}")
    return out


def agent_ingredient_refs(profile_id: str, user_uuid: str,
                          profile: Optional[Dict] = None,
                          db_path: Optional[str] = None) -> List[IngredientRef]:
    """Assemble the real IngredientRefs for a profile-agent.

    Ontology + active skills + the bound knowledge/planner repositories.
    """
    if profile is None:
        from trusted_data_agent.core.config_manager import get_config_manager
        profile = get_config_manager().get_profile(profile_id, user_uuid) or {}
    ontology = _ontology_ingredient(profile_id, user_uuid, db_path, profile=profile)
    skills = _skill_ingredients(profile)
    repos = _repo_ingredients(profile, user_uuid)
    refs = build_agent_ingredients(
        ontology, skills,
        lambda kind, aid: _store.attested_hash(kind, aid, db_path=db_path, owner_uuid=user_uuid),
        repos,
    )
    refs.extend(_extension_ingredients(ontology, db_path))
    return refs


def _extension_ingredients(ontology: Optional[Dict], db_path: Optional[str]) -> List[IngredientRef]:
    """One ingredient per PUBLISHED extension on the agent's ontology (PROP-008 A5 / §3a).

    An agent grounded on base + extension(s) is green only when the base is intact, each
    extension is intact, and each extension's recorded base hash equals the base's
    current attested hash — the bottom-up rule with one more ingredient kind. The label
    NAMES which half moved (`base moved` / `extension edited`) so the drift line on every
    surface says what to do, not just that something is amber.

    Switch-gated: with the OBDA-MODEL · OBDA-ACT master off there is no new signature field — the
    baseline is byte-identical (§7). A published extension can only exist with the master
    on, so the gate changes nothing for a graph nobody extended.
    """
    try:
        from trusted_data_agent.core import obda_planes as _planes
        if not _planes.plane_available("model"):
            return []
    except Exception:  # noqa: BLE001
        return []
    if not ontology or not ontology.get("kg_id"):
        return []
    try:
        from . import extensions as _ext
        kg_id = ontology["kg_id"]
        # The graph's revision-cached extension summary — this ran a whole-graph load and
        # decode per agent per badge request (3.5 s of /v1/trust/agents on production).
        summary = _ext.graph_summary(kg_id, db_path)
        if summary is None:
            return []
        out: List[IngredientRef] = []
        for ext_id, info in sorted(summary.items()):
            if not info.get("published"):
                continue   # a draft is not an ingredient (it never reaches the agent's grounding)
            st = _ext.extension_state(kg_id, ext_id, db_path)
            label = _ext.extension_label(ext_id)
            if st["drift"]:
                label += " · " + " · ".join(st["drift"])
            out.append(IngredientRef(_ext.EXTENSION_KIND, st["artifact_id"], st["live_hash"] or "",
                                     st["attested_hash"], unresolved=st["live_hash"] is None,
                                     label=label))
        return out
    except Exception as e:  # noqa: BLE001 — never break trust resolution
        logger.warning(f"trust.composition: extension ingredient assembly failed: {e}")
        return []


# ── the canonical agent version hash ─────────────────────────────────────────

def _load_profile(profile_id: str, user_uuid: str,
                  profile: Optional[Dict] = None) -> Dict:
    if profile is not None:
        return profile
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        return get_config_manager().get_profile(profile_id, user_uuid) or {}
    except Exception as e:  # noqa: BLE001 — trust resolution must never raise
        logger.debug(f"trust.composition: profile load failed for {profile_id}: {e}")
        return {}


def profile_id_to_tag(user_uuid: str) -> Callable[[str], Optional[str]]:
    """A resolver mapping profile id → tag for this user, for portable hashing.

    Coordinator routing stores raw profile ids that a marketplace install remaps, so the
    capability signature hashes them in tag space; without this a genie pack could never
    reinstate 🟢 on another instance.
    """
    mapping: Dict[str, str] = {}
    try:
        from trusted_data_agent.core.config_manager import get_config_manager
        for p in get_config_manager().get_profiles(user_uuid) or []:
            if p.get("id") and p.get("tag"):
                mapping[str(p["id"])] = str(p["tag"])
    except Exception as e:  # noqa: BLE001 — never break trust resolution
        logger.debug(f"trust.composition: profile tag map unavailable: {e}")
    return lambda pid: mapping.get(str(pid))


def agent_version_hash(refs: List[IngredientRef], profile: Optional[Dict] = None,
                       id_to_tag: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """THE agent's signable version hash — ingredients, safety posture **and capability**.

    This is the single definition. It previously existed as four independent
    recomputations (sign, live resolve, pack member, REST badge); adding a dimension to one
    of them would silently flip every agent to 🟠, so they all route through here.

    Why posture belongs in the hash: the badge promises "this agent is what it was validated
    to be". Two agents with identical ontology and skills but opposite enforcement — one
    withholding ungrounded answers, one merely observing — used to hash identically and both
    resolve 🟢 against the same attestation. The seal then attested to something other than
    the thing that protects the user.

    **Byte-identical baseline (charter #15):** an agent whose posture is entirely at the
    documented runtime defaults hashes exactly as before, so existing attestations stay 🟢
    and nothing re-signs. The posture dimension engages only once there is real posture to
    protect. ``authorityConfig`` is deliberately excluded — it is deployment-bound access,
    not intrinsic validation (*trust ≠ access*); see profile_config_contract.
    """
    comp = composition_hash(refs)
    try:
        from trusted_data_agent.core import profile_config_contract as _pcc
        posture = _pcc.posture_signature(profile or {})
        capability = _pcc.capability_signature(profile or {}, id_to_tag=id_to_tag)
        if (posture == _pcc.posture_signature({})
                and capability == _pcc.capability_signature({})):
            return comp          # nothing configured → historical hash, unchanged
        return content_hash({"composition": comp, "posture": posture,
                             "capability": capability})
    except Exception as e:  # noqa: BLE001 — never break trust resolution
        logger.debug(f"trust.composition: posture/capability hashing skipped: {e}")
        return comp


# ── the entry points ─────────────────────────────────────────────────────────

def resolve_deployment(profile_id: str, user_uuid: str, profile: Optional[Dict] = None,
                       db_path: Optional[str] = None) -> Dict:
    """This deployment's half of the badge: state, drift, and whether it is even required.

    Returns ``{"state", "required", "drift": [...], "signature", "hash"}``. Never raises —
    an unreadable deployment surface degrades to *unattested*, never to *trusted*.
    """
    from . import deployment as _dep
    try:
        prof = _load_profile(profile_id, user_uuid, profile)
        sig = _dep.deployment_signature(prof, user_uuid, db_path=db_path)
        live = _dep.deployment_hash(sig)
        attested = _store.attested_hash(_dep.DEPLOYMENT_KIND, profile_id, db_path=db_path,
                                        owner_uuid=user_uuid)
        state = resolve_state(attested, live)
        drift: List[str] = []
        if state is TrustState.CHANGED:
            rec = _store.load_attestation(_dep.DEPLOYMENT_KIND, profile_id, db_path=db_path,
                                          owner_uuid=user_uuid) or {}
            prior = (rec.get("verdict") or {}).get("signature")
            if isinstance(prior, dict):
                # Name what appeared — an unexplained amber trains people to click through.
                drift = _dep.describe_drift(prior, sig)
        return {"state": state.value, "required": not _dep.is_baseline(sig),
                "drift": drift, "signature": sig, "hash": live}
    except Exception as e:  # noqa: BLE001 — trust resolution must never raise
        logger.warning(f"trust.composition: deployment resolution failed: {e}")
        # TC-63: if a deployment attestation EXISTS but the live surface could not be
        # resolved, the honest state is CHANGED ("was attested, cannot show it is still
        # intact" — the unresolved principle), not UNSIGNED: reporting "not attested here"
        # would hide possible drift behind the wrong owed act (attest again). Never green.
        state = TrustState.UNSIGNED.value
        try:
            from . import deployment as _dep2
            if _store.attested_hash(_dep2.DEPLOYMENT_KIND, profile_id, db_path=db_path,
                                    owner_uuid=user_uuid):
                state = TrustState.CHANGED.value
        except Exception:
            pass
        return {"state": state, "required": True,
                "drift": [], "signature": {}, "hash": None}


def resolve_profile_trust(profile_id: str, user_uuid: str,
                          profile: Optional[Dict] = None,
                          db_path: Optional[str] = None) -> AgentTrust:
    """Resolve a real agent's live badge over BOTH halves — portable and deployment.

    ``AgentTrust.state`` is the composite; ``portable_state`` and ``deployment`` carry the
    breakdown so a surface can say *which* half is outstanding rather than just "not green".
    """
    return _resolve_profile_trust_detail(profile_id, user_uuid, profile=profile,
                                         db_path=db_path)["trust"]


def _resolve_profile_trust_detail(profile_id: str, user_uuid: str,
                                  profile: Optional[Dict] = None,
                                  db_path: Optional[str] = None) -> Dict:
    """The one resolution behind the badge AND a turn's bill of materials (PROP-009):
    ``{trust, profile, refs, agent_attested, agent_live, deployment}``. The BOM keeps the
    hashes this computes; before PROP-009 they were discarded after reading the state."""
    prof = _load_profile(profile_id, user_uuid, profile)
    refs = agent_ingredient_refs(profile_id, user_uuid, profile=prof, db_path=db_path)
    agent_hash = _store.attested_hash(AGENT_KIND, profile_id, db_path=db_path, owner_uuid=user_uuid)
    agent_live = agent_version_hash(refs, prof, id_to_tag=profile_id_to_tag(user_uuid))
    trust = resolve_agent_trust(agent_hash, refs, live_version_hash=agent_live)
    dep = resolve_deployment(profile_id, user_uuid, profile=prof, db_path=db_path)
    trust.portable_state = trust.state
    trust.deployment = {k: dep[k] for k in ("state", "required", "drift")}
    trust.state = compose_agent_state(
        trust.state, TrustState(dep["state"]), deployment_required=dep["required"])
    return {"trust": trust, "profile": prof, "refs": refs, "agent_attested": agent_hash,
            "agent_live": agent_live, "deployment": dep}


def turn_integrity_stamp(profile_id: str, user_uuid: str,
                         profile: Optional[Dict] = None,
                         db_path: Optional[str] = None) -> Optional[Dict]:
    """The integrity fact a turn seals AT ANSWER TIME — never resolved retroactively.

    SIGNED's per-turn reading is a claim about the PAST ("signed and intact when this
    answer was produced"), so it must be captured when the answer is produced and
    persisted with the turn. Resolving it live at read time substituted today's badge
    for yesterday's fact: editing an agent flipped every historical turn's SIGNED
    station to the CURRENT state — retroactive falsification, the exact thing an
    at-answer-time claim must never do.

    Returns the minimal stamp the registry reader consumes (``{"state": ...}``), or
    None when trust cannot be resolved — an absent stamp reads as honest
    "not recorded", never as a guess. One implementation for every stamping seam
    (the executor's shared entry point and the genie coordinator), so the two can
    never diverge on what the stamp means.

    The profile must actually LOAD before anything is sealed: resolving a signed
    agent over an unloadable profile (a transient read failure, the
    ``__system_default__`` pseudo-profile) would compute a version hash over empty
    ingredients and seal a false "changed" onto a turn forever. No resolvable agent
    ⇒ no stamp — "not recorded" is recoverable; a sealed falsehood is not.
    """
    return turn_evidence(profile_id, user_uuid, profile=profile, db_path=db_path)[0]


def _deployment_summary(sig: Dict) -> Dict:
    """A readable summary of the deployment surface for the evidence view — the model and
    the size of the reachable catalog; never credentials, never config ids."""
    out: Dict[str, Any] = {}
    llm = sig.get("llm") if isinstance(sig, dict) else None
    if isinstance(llm, dict):
        out["provider"] = llm.get("provider")
        out["model"] = llm.get("model")
    cat = sig.get("catalog") if isinstance(sig, dict) else None
    if isinstance(cat, dict):
        out["tools"] = len(cat.get("tools") or [])
        out["prompts"] = len(cat.get("prompts") or [])
    con = sig.get("connectors") if isinstance(sig, dict) else None
    if isinstance(con, list):
        out["connectors"] = sum(1 for c in con if isinstance(c, dict) and c.get("opted_in"))
    return out


def turn_evidence(profile_id: str, user_uuid: str, profile: Optional[Dict] = None,
                  db_path: Optional[str] = None):
    """``(integrity_stamp, bill_of_materials)`` for a turn — ONE resolution, two products.

    The stamp is SIGNED's per-turn fact (unchanged: ``{"state": ...}``). The bill of
    materials (PROP-009 §4.2) records every signed part the turn ran with: the agent's
    composition, its deployment surface, and each ingredient — content hash, the attested
    hash at this moment, the attestation-history row that matched, and the state. Both are
    ``None`` when the agent cannot be resolved (never a guess). Never raises.
    """
    try:
        prof = _load_profile(str(profile_id), user_uuid, profile)
        if not prof:
            return None, None
        d = _resolve_profile_trust_detail(str(profile_id), user_uuid, profile=prof,
                                          db_path=db_path)
    except Exception:  # noqa: BLE001 — a stamp must never break the turn it describes
        return None, None
    t = d["trust"]
    state = getattr(getattr(t, "state", None), "value", None)
    stamp = {"state": str(state)} if state else None
    try:
        from datetime import datetime, timezone

        def _hid(kind, aid, owner=None):
            try:
                return _store.current_history_id(kind, aid, db_path=db_path, owner_uuid=owner)
            except Exception:  # noqa: BLE001
                return None

        portable = getattr(getattr(t, "portable_state", None), "value", None) or state
        dep = d["deployment"] or {}
        parts = []
        for r in sorted(d["refs"], key=lambda r: (r.kind, str(r.artifact_id))):
            entry = {"kind": r.kind, "artifact_id": str(r.artifact_id),
                     "label": r.label or str(r.artifact_id),
                     "live_hash": r.live_hash or None,
                     "attested_hash": r.attested_hash,
                     "history_id": _hid(r.kind, r.artifact_id, user_uuid)
                     if r.attested_hash else None,
                     "state": ingredient_state(r).value}
            if r.unresolved:
                entry["unresolved"] = True
            parts.append(entry)
        bom = {
            "version": 1,
            "profile_id": str(profile_id),
            "profile_tag": prof.get("tag"),
            "owner_uuid": str(user_uuid) if user_uuid else None,
            "resolved": "turn_start",
            "badge": state,                       # the composite badge at turn start
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "agent": {"kind": AGENT_KIND, "artifact_id": str(profile_id),
                      "label": prof.get("tag") or str(profile_id),
                      "live_hash": d["agent_live"], "attested_hash": d["agent_attested"],
                      "history_id": _hid(AGENT_KIND, str(profile_id), user_uuid)
                      if d["agent_attested"] else None,
                      # the agent's OWN signature against its live composition — its
                      # ingredients report on their own rows, never twice
                      "state": getattr(getattr(t, "composition_state", None), "value", None),
                      # the portable badge (signature AND every ingredient green)
                      "badge": portable},
            "deployment": {"kind": "agent_deployment", "artifact_id": str(profile_id),
                           "label": "deployment",
                           "live_hash": dep.get("hash"),
                           "attested_hash": None, "history_id": None,
                           "state": dep.get("state"),
                           "required": bool(dep.get("required")),
                           "summary": _deployment_summary(dep.get("signature") or {})},
            "parts": parts,
        }
        try:
            from . import deployment as _dep
            _att = _store.attested_hash(_dep.DEPLOYMENT_KIND, str(profile_id), db_path=db_path,
                                        owner_uuid=user_uuid)
            bom["deployment"]["attested_hash"] = _att
            bom["deployment"]["history_id"] = _hid(_dep.DEPLOYMENT_KIND, str(profile_id),
                                                   user_uuid) if _att else None
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001 — the stamp stands even if the BOM cannot be built
        logger.warning(f"trust.composition: bill of materials not built: {e}")
        bom = None
    return stamp, bom


def attest_deployment(profile_id: str, user_uuid: str, signer_uuid: str,
                      profile: Optional[Dict] = None,
                      db_path: Optional[str] = None) -> Dict:
    """Attest **this instance's** resolved capability surface for an agent.

    Deliberately independent of the portable signature and of the bottom-up ingredient gate:
    the deployer is answering a different question ("this model, this tool catalog, these
    connectors are what I intend here"), and a subscriber must be able to answer it for a
    pack whose artefact was signed by someone else. The record stores the signature itself
    so a later drift can be *described*, not merely detected.
    """
    from . import deployment as _dep
    prof = _load_profile(profile_id, user_uuid, profile)
    sig = _dep.deployment_signature(prof, user_uuid, db_path=db_path)
    refusal = _dep.attest_refusal(sig)
    if refusal:
        # Never sign over an UNRESOLVED surface: the record would read 🟢 for a catalog
        # (or connector store) this instance has not resolved. Name the surface and the
        # act that resolves it instead.
        return {"signed": False, "envelope": None, "signature": sig, **refusal}
    env = _store.sign(_dep.DEPLOYMENT_KIND, profile_id, _dep.deployment_hash(sig),
                      signer_uuid=signer_uuid,
                      verdict={"status": "attested", "band": "clean", "signable": True,
                               "note": "deployment surface attested on this instance",
                               "signature": sig},
                      db_path=db_path, owner_uuid=user_uuid)
    return {"signed": bool(env.get("signed")), "envelope": env, "signature": sig}


def sign_agent(profile_id: str, user_uuid: str, signer_uuid: str,
               profile: Optional[Dict] = None,
               db_path: Optional[str] = None,
               event: str = "sign",
               provenance: Optional[Dict] = None) -> Dict:
    """Sign an agent — the bottom-up gate: refuse unless every ingredient is already 🟢.

    Returns ``{"signed": bool, "blocking": [...], "envelope": {...}}``. Separation
    of duties (signer ≠ author) is enforced at the REST endpoint, not here.

    ``event`` / ``provenance``: a scheduled renewal (``core/trust/renewal.py``) signs with
    ``event="renew"`` and records WHY under ``verdict["renewal"]`` — the run, the
    repository that changed, the files — so the attestation history can say "renewed by
    the scheduled sync", never "signed by the owner".
    """
    prof = _load_profile(profile_id, user_uuid, profile)
    refs = agent_ingredient_refs(profile_id, user_uuid, profile=prof, db_path=db_path)
    ok, blocking = agent_signable(refs)
    if not ok:
        # TC-27: name the blockers so the sign-refusal payload reads "Uderia Documentation",
        # not "knowledge repository #2" — the same label seam ``AgentTrust.to_dict`` uses.
        _labels = {(r.kind, r.artifact_id): r.label for r in refs if r.label}
        return {"signed": False,
                "blocking": [{"kind": k, "artifact_id": a,
                              **({"label": _labels[(k, a)]} if (k, a) in _labels else {})}
                             for (k, a) in blocking],
                "envelope": None}
    # TC-1: sign in TAG space, exactly as resolve_profile_trust does. A genie coordinator's
    # genieConfig.slaveProfiles are hashed via the id→tag resolver; signing without it recorded
    # an id-space hash the badge (which always resolves in tag space) could never reproduce, so
    # every coordinator read 🟠 CHANGED the instant it was signed and no coordinator pack member
    # could ever reinstate. One canonical call, as the module docstring already claims.
    comp = agent_version_hash(refs, prof, id_to_tag=profile_id_to_tag(user_uuid))
    # The agent kind has a validator now (posture band), so record its verdict alongside the
    # signature — the column was always there, the agent path just never populated it.
    verdict = None
    try:
        from trusted_data_agent.core.trust.attestation import get_validator
        fn = get_validator(AGENT_KIND)
        if fn:
            v = fn(prof)
            verdict = {"status": v.status, "band": getattr(v.band, "value", v.band),
                       "signable": v.signable, "note": v.note}
    except Exception as e:  # noqa: BLE001 — a verdict is metadata, never a gate here
        logger.debug(f"trust.composition: agent validator skipped: {e}")
    if provenance:
        verdict = {**(verdict or {}), "renewal": dict(provenance)}
    env = _store.sign(AGENT_KIND, profile_id, comp, signer_uuid=signer_uuid,
                      verdict=verdict, db_path=db_path, owner_uuid=user_uuid, event=event)
    return {"signed": bool(env.get("signed")), "blocking": [], "envelope": env,
            "validation": verdict}
