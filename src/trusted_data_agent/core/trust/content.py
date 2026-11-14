"""
Canonical per-kind content resolution for the Trust Membrane.

ONE place computes each artefact kind's *signable content* + its ``version_hash``, so the
three consumers never diverge:
  * the sign-off endpoints (which also need the content itself, to validate),
  * the live badge state (which needs only the hash — attested vs live),
  * marketplace **portability** (reinstate a copy iff its live hash matches the signed hash).

Leaf kinds only (skill / ontology / extension / knowledge / planner). The composed kinds
(agent, agent_pack) aggregate these via ``composition.py`` / the pack resolver.

Everything is defensive: any failure returns ``None`` rather than raising, so a hash that
can't be computed simply reads as "not resolvable" (badge falls back to signed/unsigned).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .attestation import content_hash
from . import composition as _comp

logger = logging.getLogger("quart.app")


def skill_content(skill_id: str) -> Tuple[Optional[str], Optional[str]]:
    """(content, hash) for a skill, or (None, None).

    THE one skill-hash resolver (PROP-005 §4) — composition ingredients and the
    sign-off route derive from here, never re-compute. Legacy skills keep the
    EXACT single-string formula (zero attestation flips — freeze gate B3);
    a progressive skill binds its library payload into the hash as sorted
    (relpath, sha256) pairs (the `extension_content` composite precedent), so
    an edited library file flips the badge exactly like an edited body.
    """
    try:
        from trusted_data_agent.skills.manager import get_skill_manager
        mgr = get_skill_manager()
        c = mgr.get_skill_full_content(skill_id)
        if c is None:
            return None, None
        pairs = skill_library_pairs(mgr, skill_id)
        if pairs is None:
            return c, content_hash(c)          # legacy formula, byte-identical
        payload = {"content": c, "library": pairs}
        # A library that claims its SQL was executed binds the claim (which statement
        # texts ran, on which system) into the hash; one that claims nothing keeps the
        # formula above byte for byte — no existing attestation moves.
        from trusted_data_agent.skills.execution_record import signature_part
        from trusted_data_agent.skills.package import progressive_index
        executed = signature_part(progressive_index((getattr(mgr, "manifests", {}) or {}).get(skill_id)))
        if executed is not None:
            payload["executed"] = executed
        return c, content_hash(payload)
    except Exception:
        return None, None


def skill_library_pairs(mgr, skill_id: str) -> Optional[List[List[str]]]:
    """Sorted [(relpath, sha256), …] for a progressive skill; None for legacy.

    Reads the INDEX-driven payload (stray files never enter the hash, exactly
    as they never ship). A missing indexed file simply drops out of the pairs —
    the hash then differs from what was signed, which reads 🟠 CHANGED: honest
    drift, never a silent pass.
    """
    try:
        from trusted_data_agent.skills.package import is_progressive, file_sha256
        manifest = (getattr(mgr, "manifests", {}) or {}).get(skill_id)
        if not is_progressive(manifest):
            return None
        files = mgr.get_skill_library_files(skill_id)
        return [[rel, file_sha256(data)] for rel, data in sorted(files.items())]
    except Exception:
        return None


def ontology_hash(kg_id: str) -> Optional[str]:
    """Content hash of a KG/ontology (entities + relationships)."""
    try:
        chash, n_ents = _comp.kg_graph_hash(kg_id, None)
        return chash if n_ents else None
    except Exception:
        return None


def extension_content(ext_id: str) -> Tuple[Optional[str], Optional[Dict], Optional[str]]:
    """(source, manifest, hash) for an extension package, or (None, None, None)."""
    try:
        from trusted_data_agent.extensions.manager import get_extension_manager
        mgr = get_extension_manager()
        manifest = mgr.get_manifest(ext_id)
        if not manifest:
            return None, None, None
        source = mgr.get_extension_source(ext_id) or ""
        return source, manifest, content_hash({"source": source, "manifest": manifest})
    except Exception:
        return None, None, None


async def knowledge_sample_texts(coll: dict, cap: int = 200):
    """Best-effort bounded fetch of document text for the injection scan. Fully defensive:
    any failure (backend lacks GET_ALL, init error) → ([], False) → the validator degrades
    to a 'content not scanned' partial rather than blocking. Shared by the sign-off route
    and the scheduled renewal (one fetch, one verdict path)."""
    try:
        from trusted_data_agent.vectorstore.factory import get_backend_for_collection
        from trusted_data_agent.vectorstore.capabilities import VectorStoreCapability
        backend = await get_backend_for_collection(coll, None)
        if not backend.supports(VectorStoreCapability.GET_ALL):
            return [], False
        res = await backend.get(coll.get("collection_name"), limit=cap)
        texts = [d.content for d in (res.documents or []) if getattr(d, "content", None)]
        total = coll.get("chunk_count") or len(texts)
        return texts, (len(texts) >= (total or 0))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"trust: knowledge sample fetch failed: {e}")
        return [], False


def knowledge_content(collection_id: str) -> Tuple[Optional[List[str]], Optional[str], Optional[str], Optional[Dict]]:
    """(doc_hashes, embedding_model, hash, collection) for a knowledge repo, or (None, …).

    RAG Trust Hardening Wave 2 (G1/Q3) — the **earned signature v2 shape**: when the
    ingest firewall has scanned any document, the signable content additionally binds the
    ``scan_report_hash`` (what was screened, what was found, what was acknowledged) and
    the quarantine set — so quarantining a doc or changing a scan honestly flips the
    badge 🟠 until re-signed, and a v2 signature attests *integrity + screened*.
    A never-scanned corpus keeps the legacy ``{docs, embedding_model}`` shape, so
    pre-existing attestations stay 🟢 until the owner initiates scanning (the flip is
    always owner-initiated, never retroactive)."""
    try:
        from trusted_data_agent.core.collection_db import get_collection_db
        db = get_collection_db()
        coll = db.get_collection_by_id(int(collection_id))
        if not coll:
            return None, None, None, None
        docs = db.get_all_documents_in_collection(int(collection_id))
        hashes = sorted((d.get("content_hash") or "") for d in docs)
        model = coll.get("embedding_model") or ""
        payload = {"docs": hashes, "embedding_model": model}
        try:
            manifest = db.get_scan_manifest(int(collection_id))
            if manifest.get("scanned") or manifest.get("quarantined"):
                payload["scan_report_hash"] = manifest.get("scan_report_hash")
                # Keyed on CONTENT, not on the local document id: a quarantine is a
                # decision about a document, and the same document in a copied corpus
                # carries a different local id. Falls back to the id only for a row with
                # no content hash, where nothing more portable exists.
                payload["quarantined"] = sorted(
                    (q.get("content_hash") or q.get("document_id") or "")
                    for q in manifest.get("quarantined") or [])
                coll = {**coll, "_scan_manifest": manifest}
        except Exception:
            pass
        return hashes, model, content_hash(payload), coll
    except Exception:
        return None, None, None, None


#: Fields stripped from a champion case before hashing under the BASELINE formula.
#:
#: Two groups, for two different reasons — and each is a way to lose the guarantee
#: rather than a tidiness preference.
#:
#: **Instance-local identity.** A case records where it was stored and who stored it
#: (``collection_id``, ``mcp_server_id``, ``user_uuid``) plus a per-seed ``case_id``, and
#: none of them survives an installation unchanged: the import re-keys ``mcp_server_id``
#: to the reading agent's endpoint (``_planner_mcp_for_ref``), the disk mirror writes the
#: consumer's ``collection_id``, and the id and owner are whatever each side minted.
#: Same reasoning as ``profile_config_contract``'s
#: ``local_ref_paths``: an instance-local id must never enter a portable hash, or a
#: byte-faithful copy of a pack's planner can never reinstate the publisher's
#: signature — it would hash its own collection id instead of the publisher's and the
#: honesty gate would refuse forever.
#:
#: **Operational state.** These three are the ONLY fields normal operation writes back
#: onto an ALREADY-SEEDED case (``update_case_feedback``, champion re-election,
#: ``record_champion_replay_outcome``). ``is_most_efficient`` is the sharpest: election
#: re-runs whenever a new case is seeded for the same query, so seeding ANY learned case
#: can flip a pack-shipped case's flag. Hashing them re-creates the exact failure the
#: baseline formula exists to prevent — a signed agent breaking its own seal through
#: correct operation — one level down, where a user's vote would do it.
_CASE_LOCAL_TOP = ("case_id",)
_CASE_LOCAL_META = (
    # `case_id` is here TWICE over — top-level and nested — because the mirror overlays
    # the flat ChromaDB metadata (which carries the row's own id) onto the case's
    # metadata. Ids are minted per instance and re-minted by an in-place update over a
    # stale copy, so publisher and consumer disagreed on exactly this field for 14 of
    # 14 cases while agreeing on everything the pack shipped (found live, 2026-09-07).
    "case_id",
    "collection_id", "mcp_server_id", "user_uuid",
    "user_feedback_score", "is_most_efficient", "replay_failure_count",
)


def _canonical_case(data: Any) -> Any:
    """The instance-invariant view of a champion case, used ONLY for hashing.

    Never applied to the cases returned to callers — ``planner_cases`` hands back the
    raw stored shape, because management surfaces (list/delete/fork) need the real ids.
    """
    if not isinstance(data, dict):
        return data
    out = {k: v for k, v in data.items() if k not in _CASE_LOCAL_TOP}
    md = out.get("metadata")
    if isinstance(md, dict):
        out["metadata"] = {k: v for k, v in md.items() if k not in _CASE_LOCAL_META}
    return out


def _is_pack_owned(data: Any) -> bool:
    """Whether a case was shipped by an agent pack (carries the ownership stamp).

    The stamp is written by ``agent_pack_manager._stamp_planner_ownership``; the update
    path already treats it as the boundary between what the PACK shipped and what the
    AGENT learned (``_diff_and_patch_planner_collection`` never deletes an unstamped
    case). The signature draws the same line.
    """
    if not isinstance(data, dict):
        return False
    md = data.get("metadata")
    return bool(isinstance(md, dict) and md.get("uderia_pack_ref"))


def case_content_id(data: Any) -> str:
    """The identity of what a champion case SAYS, independent of where it is stored.

    ONE definition, shared by the baseline hash here and by the pack update's change
    detection (``agent_pack_manager._planner_case_content_id``), so an update applies a
    change EXACTLY when the hash would move — which is what makes a diverged consumer
    always one update away from converging. Two definitions would let them disagree, and
    that disagreement is how a consumer ends up permanently unable to reinstate a
    signature it is entitled to: the publisher re-signs the new baseline while the
    consumer's copy is never rewritten.
    """
    return content_hash(_canonical_case(data))


def planner_locked(collection_id: Any) -> bool:
    """Is this planner repository LOCKED (its seal covers every case)? Read from the
    collection row; anything unreadable is NOT locked — the default formula, never
    the stricter one on a guess."""
    try:
        from trusted_data_agent.core.collection_db import get_collection_db
        row = get_collection_db().get_collection_by_id(int(collection_id)) or {}
        return bool(row.get("trust_locked")) if isinstance(row, dict) else False
    except Exception:
        return False


def planner_cases(collection_id: str) -> Tuple[Optional[List[Any]], Optional[str]]:
    """(cases, hash) for a planner repo (on-disk champion cases).

    (None, None) → the case directory does not exist. ``(cases, None)`` → the directory is
    populated but at least one case file could not be parsed, so the hash would be over an
    INCOMPLETE set: TC-21 returns a None hash instead so the caller emits the repo as
    ``unresolved`` (blocks signing) rather than silently signing a shrunk corpus.

    TWO HASH FORMULAS, selected by evidence — never by a flag:

    * **baseline** (any case carries ``uderia_pack_ref``): hash ONLY the pack-owned cases,
      over their instance-invariant view. A planner repo is a *learning* artifact — the
      seeding loop writes a new case after a successful turn — so hashing everything means
      a signed agent breaks its own seal through correct operation, and (with sign-off
      being tier-gated) a non-admin owner can never restore it. Hashing the publisher's
      baseline instead makes the signature say the one thing a consumer needs: *the
      shipped cases are intact*. What the agent learned locally is deliberately out of
      scope — it is not the publisher's claim.
    * **legacy** (no stamps present): the original formula, over every case, raw. Keeps
      every attestation signed before this change byte-identical — the same
      absent-new-signal-means-old-formula rule ``knowledge_content`` uses for scans.
    """
    try:
        import json as _json
        from trusted_data_agent.core.config import APP_CONFIG
        from trusted_data_agent.core.utils import get_project_root
        cdir = get_project_root() / APP_CONFIG.RAG_CASES_DIR / f"collection_{int(collection_id)}"
        if not cdir.is_dir():
            return None, None
        cases, legacy_hashes, baseline_hashes, had_error = [], [], [], False
        for fp in sorted(cdir.glob("case_*.json")):
            try:
                data = _json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                had_error = True   # a corrupt case must not silently shrink the signed set
                continue
            cases.append(data)
            legacy_hashes.append(content_hash(data))
            if _is_pack_owned(data):
                baseline_hashes.append(case_content_id(data))
        if had_error:
            # Conservative on purpose: a file that would not parse cannot be shown to be
            # OUTSIDE the baseline, so it still blocks. Same verdict as before this change.
            return cases, None
        if planner_locked(collection_id):
            # LOCKED (Model B, the owner's opt-in): the seal covers EVERY case over the
            # same canonical view — what the agent learned included. The seeding loop
            # never writes into a locked repository (learning lands in the user's own
            # default for the server), so operation cannot move this hash; only a pack
            # update or the owner's own edit can, and both are acts the owner re-signs.
            return cases, content_hash(sorted(case_content_id(c) for c in cases))
        if baseline_hashes:
            return cases, content_hash(sorted(baseline_hashes))
        return cases, content_hash(sorted(legacy_hashes))
    except Exception:
        return None, None


def planner_has_files(collection_id: str) -> bool:
    """Whether a planner repo's case directory exists and holds case files — an existence
    signal INDEPENDENT of parse success, so an unreadable populated repo is distinguished
    from an empty one (empty → omit; populated-but-unhashable → unresolved, blocks)."""
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        from trusted_data_agent.core.utils import get_project_root
        cdir = get_project_root() / APP_CONFIG.RAG_CASES_DIR / f"collection_{int(collection_id)}"
        return cdir.is_dir() and any(cdir.glob("case_*.json"))
    except Exception:
        return False


def _trust_db_path():
    from . import store as _store
    return _store._db_path(None)


def flow_definition(flow_id: str) -> Tuple[Optional[Dict], Optional[str]]:
    """(full definition, hash) for a flow instance, or (None, None). The hash is over the
    LOGIC only (node id/type/data + edges) — node positions/layout are cosmetic and excluded
    so a drag-to-reposition doesn't flip trust; the validator still gets the full definition.
    """
    try:
        import json as _json
        from trusted_data_agent.core import sqlite_util
        conn = sqlite_util.connect(_trust_db_path())
        try:
            row = conn.execute("SELECT definition FROM flows WHERE flow_id=?", (flow_id,)).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None, None
        d = _json.loads(row[0])
        nodes = [{"id": n.get("id"), "type": n.get("type"), "data": n.get("data")}
                 for n in (d.get("nodes") or []) if isinstance(n, dict)]
        edges = [{"source": e.get("source"), "target": e.get("target"),
                  "condition": e.get("condition") or (e.get("data") or {}).get("condition")}
                 for e in (d.get("edges") or []) if isinstance(e, dict)]
        norm = {"nodes": sorted(nodes, key=lambda x: str(x.get("id"))),
                "edges": sorted(edges, key=lambda e: (str(e.get("source")), str(e.get("target"))))}
        return d, content_hash(norm)
    except Exception:
        return None, None


def dashboard_tiles(dashboard_id: str) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """(tiles, hash) for a dashboard instance, or (None, None). Hashes the STRUCTURE +
    data sources (component/title/mcp source), NOT the volatile cached render data, so a
    tile refresh doesn't flip trust."""
    try:
        import json as _json
        from trusted_data_agent.core import sqlite_util
        conn = sqlite_util.connect(_trust_db_path())
        try:
            rows = conn.execute(
                "SELECT component_id, title, mcp_tool_name, mcp_args_json, source_prompt "
                "FROM dashboard_tiles WHERE dashboard_id=? ORDER BY id", (str(dashboard_id),)).fetchall()
        finally:
            conn.close()
        tiles = [{"component_id": r[0], "title": r[1], "mcp_tool_name": r[2],
                  "mcp_args": (_json.loads(r[3]) if r[3] else {}), "source_prompt": r[4] or ""}
                 for r in rows]
        if not tiles:
            return None, None
        return tiles, content_hash(tiles)
    except Exception:
        return None, None


def canvas_content(canvas_id: str) -> Tuple[Optional[str], Optional[str]]:
    """(content, hash) for a canvas instance, or (None, None)."""
    try:
        from trusted_data_agent.core import sqlite_util
        conn = sqlite_util.connect(_trust_db_path())
        try:
            row = conn.execute("SELECT content FROM canvases WHERE canvas_id=?", (canvas_id,)).fetchone()
        finally:
            conn.close()
        if not row or row[0] is None:
            return None, None
        return row[0], content_hash(row[0])
    except Exception:
        return None, None


def assurance_module_content(pack_id: str):
    """PROP-003 — an assurance-module pack: its manifest plus every source file.

    Both halves are hashed because both decide behaviour: the manifest carries the
    declarative rules (a regex IS the assessment for a T0 module), and the sources carry
    the Python. A hash over only one of them would let the other change under a signature.
    """
    try:
        from pathlib import Path
        roots = [Path(__file__).resolve().parents[3] / "assurance_modules" / "builtin",
                 Path.home() / ".tda" / "assurance_modules"]
        for base in roots:
            d = base / pack_id
            mf = d / "manifest.json"
            if not mf.exists():
                continue
            import json as _json
            manifest = _json.loads(mf.read_text())
            sources = {p.name: p.read_text() for p in sorted(d.glob("*.py"))}
            payload = {"manifest": manifest, "sources": sources}
            return payload, content_hash(payload)
        return None, None
    except Exception:
        return None, None


_LEAF = {
    "skill": lambda i: skill_content(i)[1],
    "assurance_module": lambda i: assurance_module_content(i)[1],
    "ontology": ontology_hash,
    "extension": lambda i: extension_content(i)[2],
    "knowledge": lambda i: knowledge_content(i)[2],
    "planner": lambda i: planner_cases(i)[1],
    "flow": lambda i: flow_definition(i)[1],
    "dashboard": lambda i: dashboard_tiles(i)[1],
    "canvas": lambda i: canvas_content(i)[1],
    # OBDA-READ data product (Phase A3): manifest + stored validation report, same
    # formula as signing — an edited manifest reads 🟠 CHANGED, never intact.
    "data_product": lambda i: __import__(
        "trusted_data_agent.core.trust.products",
        fromlist=["live_hash"]).live_hash(i),
    # PROP-008 A5: an ontology extension — its published layer + the base's attested
    # hash (the lineage), same formula as signing; a base re-sign reads CHANGED here.
    "action_type": lambda i: __import__(
        "trusted_data_agent.core.trust.actions", fromlist=["live_hash"]).live_hash(i),
    "ontology_extension": lambda i: __import__(
        "trusted_data_agent.core.trust.extensions",
        fromlist=["live_hash"]).live_hash(i),
}


def live_hash(kind: str, artifact_id: str) -> Optional[str]:
    """The canonical current content hash for a leaf artefact kind, or None."""
    fn = _LEAF.get(kind)
    try:
        return fn(artifact_id) if fn else None
    except Exception:
        return None
