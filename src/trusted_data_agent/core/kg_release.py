"""Model B — released-only grounding (Tier 4.3 §1a, the deferred half).

Tier 4.3's lifecycle "publish" is a RELEASE act: it pins a conformance-stamped snapshot
of the ontology into `kg_versions` and stamps `published_version_id` on the graph. As
built (Model A) that changed nothing at runtime — every agent kept grounding on the
live draft, so a release was ceremony for anyone whose consumers are their own agents.

Model B gives release runtime teeth, OPT-IN per agent: an agent whose
`componentConfig.knowledge_graph.groundOn` is `"released"` reads the ontology's
RELEASED version — every KG read (enrichment, trusted scope, the TDA_KnowledgeGraph
tool's reads) — and never the draft. The draft can be edited, re-synced, re-validated
freely; the agent's grounding moves only when the owner publishes again.

How: the released snapshot is MATERIALISED into a shadow graph — the same `kg_entities`
/ `kg_relationships` tables under `<kg_id>::released` — so the entire retrieval path
(search, adaptive subgraph, capabilities, semantic index, usage telemetry, the
grounding gate's scope) runs UNCHANGED over a different kg_id. The shadow's
`kg_metadata` row carries `released_of` + `released_version_id`, is never active, and
is hidden from every enumerator (graph lists, pack export, the published catalog).

Invariants:
  * absent / any other value of `groundOn` ⇒ Model A, byte-identical (charter #15).
  * `"released"` with NO release yet ⇒ the live draft, and the event SAYS so
    (`fallback: "live"`) — an agent must never silently read nothing.
  * the shadow is rebuilt only when `published_version_id` moves (idempotent;
    lazily on first read, eagerly at publish).
  * writes through a released-mode agent are REFUSED at the tool (a release is
    immutable by definition; an agent that edited the draft while reading the
    release would never see its own edit).
  * trust hashes the EFFECTIVE ingredient: a released-mode agent's ontology hash is
    the shadow's, so editing the draft does not flip its badge (its behaviour did not
    change) and a new publish does (it did).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from trusted_data_agent.components import signals as _signals

logger = logging.getLogger("quart.app")

SHADOW_SUFFIX = "::released"
MODE_LIVE = "live"
MODE_RELEASED = "released"


def shadow_id(kg_id: str) -> str:
    return f"{kg_id}{SHADOW_SUFFIX}"


def is_shadow(kg_id: Optional[str]) -> bool:
    return bool(kg_id) and str(kg_id).endswith(SHADOW_SUFFIX)


def ground_on_mode(profile: Optional[Dict[str, Any]]) -> str:
    """`released` only on an explicit opt-in; every other shape is Model A."""
    try:
        cfg = ((profile or {}).get("componentConfig") or {}).get("knowledge_graph") or {}
        return MODE_RELEASED if str(cfg.get("groundOn") or "").lower() == MODE_RELEASED else MODE_LIVE
    except Exception:  # noqa: BLE001
        return MODE_LIVE


def _db_path(db_path: Optional[str] = None) -> str:
    if db_path:
        return db_path.replace("sqlite:///", "")
    try:
        from trusted_data_agent.core.config import APP_CONFIG
        return (getattr(APP_CONFIG, "AUTH_DB_PATH", "") or "tda_auth.db").replace("sqlite:///", "") or "tda_auth.db"
    except Exception:  # noqa: BLE001
        return "tda_auth.db"


def _connect(db_path: Optional[str]):
    from trusted_data_agent.core import sqlite_util
    from components.builtin.knowledge_graph.graph_store import GraphStore
    conn = sqlite_util.connect(_db_path(db_path))
    GraphStore._ensure_kg_metadata_table(conn)   # self-applying columns incl. released_of
    return conn


def release_info(kg_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The LIVE graph's release facts, or None when the graph is unknown."""
    if not kg_id or is_shadow(kg_id):
        return None
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT kg_id, profile_id, user_uuid, name, kg_store_config_id, "
            "lifecycle_status, semantic_version, published_version_id "
            "FROM kg_metadata WHERE kg_id=?", (kg_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    r = dict(zip(("kg_id", "profile_id", "user_uuid", "name", "kg_store_config_id",
                  "lifecycle_status", "semantic_version", "published_version_id"), row))
    return r


def shadow_state(kg_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The shadow's row facts, or None when no shadow has been materialised."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT kg_id, released_of, released_version_id, released_extensions_hash "
            "FROM kg_metadata WHERE kg_id=?",
            (shadow_id(kg_id),)).fetchone()
    finally:
        conn.close()
    return dict(zip(("kg_id", "released_of", "released_version_id", "released_extensions_hash"), row)) if row else None


# ── the published extension layer (PROP-008 A5) ─────────────────────────────────
# The released view = the PUBLISHED base snapshot + the PUBLISHED extensions as they stand
# NOW. A draft never reaches a released-mode agent: the base snapshot is read with its
# authored nodes removed (a publish pins everything the graph held, drafts included), and
# only authored nodes whose `status` is `published` are added back, with the edges sourced
# at them (the additive-only rule: an extension's edges start on its own nodes). The layer
# is fingerprinted so a publish — of the base OR of an extension — rebuilds the shadow.

def _is_authored(props: Dict[str, Any]) -> bool:
    return (props or {}).get("layer") == "authored"


def _is_published(props: Dict[str, Any]) -> bool:
    return _is_authored(props) and str((props or {}).get("status") or "draft") == "published"


def published_extension_layer(kg_id: str, db_path: Optional[str] = None
                              ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The live graph's published extension layer, in the import_bulk shape (names, never
    ids — the shadow mints its own): the published authored nodes with the edges sourced at
    them, AND their realisation — the schema, tables and columns their landing wrote, with
    the edges touching those (``contains`` from a base schema/table, ``implements`` to the
    concept). A released-mode agent reads a published Lead together with where it lives;
    before this, a Lead landed after the base was pinned reached the released view with no
    table, and an unpublished one's table reached it with the pinned base."""
    import json as _json
    from trusted_data_agent.kgstore import types as _T
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, name, entity_type, properties_json, source, source_detail FROM kg_entities "
            "WHERE kg_id=? AND (json_extract(properties_json, '$.layer') = 'authored' "
            f"OR json_extract(properties_json, '$.{_T.REALISATION_SOURCE}'))", (kg_id,)).fetchall()
        layer = []
        for r in rows:
            props = _json.loads(r[3]) if r[3] else {}
            layer.append({"id": r[0], "name": r[1], "entity_type": r[2], "properties": props,
                          "source": r[4] or "authored", "source_detail": r[5]})
        published = [e for e in layer if _is_published(e["properties"])]
        realised = _T.realisations_of(layer, {e["name"] for e in published})
        chosen = published + [e for e in realised if e not in published]
        ids = {e["id"] for e in chosen}
        real_ids = {e["id"] for e in realised}
        excluded = {e["id"] for e in layer} - ids     # drafts and their realisations stay home
        ents = [{k: v for k, v in e.items() if k != "id"} for e in chosen]
        rels: List[Dict[str, Any]] = []
        if ids:
            ph = ",".join("?" * len(ids))
            for r in conn.execute(
                    f"SELECT r.relationship_type, r.cardinality, r.metadata_json, r.source, "
                    f"se.name, se.entity_type, te.name, te.entity_type, r.source_entity_id, r.target_entity_id "
                    f"FROM kg_relationships r JOIN kg_entities se ON r.source_entity_id=se.id "
                    f"JOIN kg_entities te ON r.target_entity_id=te.id "
                    f"WHERE r.kg_id=? AND (r.source_entity_id IN ({ph}) OR r.target_entity_id IN ({ph}))",
                    (kg_id, *ids, *ids)).fetchall():
                s_id, t_id = r[8], r[9]
                # an authored node's edges are the ones sourced at it (additive-only); a
                # realisation's are every edge touching it; nothing reaching a draft
                if s_id in excluded or t_id in excluded:
                    continue
                if s_id not in ids and t_id not in real_ids:
                    continue
                md = None
                try:
                    md = _json.loads(r[2]) if r[2] else None
                except Exception:  # noqa: BLE001
                    md = None
                rels.append({"relationship_type": r[0], "cardinality": r[1], "metadata": md,
                             "source": r[3] or "authored", "source_name": r[4], "source_type": r[5],
                             "target_name": r[6], "target_type": r[7]})
    finally:
        conn.close()
    return ents, rels


def _layer_fingerprint(ents: List[Dict[str, Any]], rels: List[Dict[str, Any]]) -> str:
    """A content fingerprint of the published extension layer (order-independent)."""
    import hashlib as _hl
    import json as _json
    e = sorted(_json.dumps({"n": x["name"], "t": x["entity_type"], "p": x.get("properties") or {}},
                           sort_keys=True, default=str) for x in ents)
    r = sorted(_json.dumps({k: v for k, v in x.items() if k != "metadata"}, sort_keys=True, default=str)
               for x in rels)
    return _hl.sha256(_json.dumps({"e": e, "r": r}).encode("utf-8")).hexdigest()


def _without_authored(payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The base of a pinned snapshot: its extension layer (``is_extension_layer`` — authored
    nodes AND their realisations) and every edge touching it removed. The published
    extension layer is added back as it stands NOW (``published_extension_layer``)."""
    from trusted_data_agent.kgstore.types import is_extension_layer as _ext_layer
    ents = payload.get("entities") or []
    rels = payload.get("relationships") or []
    auth = {(str(e.get("name")), str(e.get("entity_type"))) for e in ents if _ext_layer(e.get("properties") or {})}
    base_e = [e for e in ents if (str(e.get("name")), str(e.get("entity_type"))) not in auth]
    base_r = [r for r in rels
              if (str(r.get("source_name")), str(r.get("source_type"))) not in auth
              and (str(r.get("target_name")), str(r.get("target_type"))) not in auth]
    return base_e, base_r


def ensure_released_shadow(kg_id: str, user_uuid: Optional[str] = None,
                           db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Make sure `<kg_id>::released` holds exactly the published version. Idempotent.

    Returns the release description ``{shadow_kg_id, version_id, label, lifecycle}`` or
    None when the graph has no release (nothing to materialise). Never raises — a
    materialisation failure logs once and returns None, which the caller reads as
    "no release", i.e. the honest live fallback.
    """
    try:
        info = release_info(kg_id, db_path)
        if not info or not info.get("published_version_id"):
            return None
        version_id = info["published_version_id"]
        owner_uuid = info.get("user_uuid") or user_uuid
        desc = {"shadow_kg_id": shadow_id(kg_id), "version_id": version_id,
                "label": info.get("semantic_version"),
                "lifecycle": info.get("lifecycle_status") or "published"}
        ext_e, ext_r = published_extension_layer(kg_id, db_path)
        ext_hash = _layer_fingerprint(ext_e, ext_r)
        cur = shadow_state(kg_id, db_path)
        if cur and cur.get("released_version_id") == version_id \
                and cur.get("released_extensions_hash") == ext_hash:
            return desc
        from trusted_data_agent.core import kg_version_db
        payload = kg_version_db.get_version_graph(version_id, kg_id, owner_uuid)
        if not payload:
            from trusted_data_agent.core.log_once import warn_once
            warn_once(logger, f"kg-release-missing:{kg_id}:{version_id}",
                      f"KG release: published version {version_id} of {kg_id} has no stored "
                      f"snapshot — released-mode agents fall back to the live draft")
            return None
        _materialise(info, version_id, payload, db_path, ext_e, ext_r, ext_hash)
        logger.info(f"KG release: materialised {desc['shadow_kg_id']} from {version_id} "
                    f"({len(payload.get('entities') or [])} entities, "
                    f"{len(ext_e)} published extension node(s))")
        return desc
    except Exception as exc:  # noqa: BLE001 — never break a turn or a publish
        from trusted_data_agent.core.log_once import warn_once
        warn_once(logger, f"kg-release-materialise:{kg_id}",
                  f"KG release: could not materialise the released view of {kg_id} — "
                  f"released-mode agents fall back to the live draft ({type(exc).__name__}: {exc})")
        return None


def _materialise(info: Dict[str, Any], version_id: str, payload: Dict[str, Any],
                 db_path: Optional[str], ext_e: Optional[List[Dict[str, Any]]] = None,
                 ext_r: Optional[List[Dict[str, Any]]] = None, ext_hash: Optional[str] = None) -> None:
    from components.builtin.knowledge_graph.graph_store import GraphStore
    kg_id, sid = info["kg_id"], shadow_id(info["kg_id"])
    now = datetime.now(timezone.utc).isoformat()
    if ext_e is None:
        ext_e, ext_r = published_extension_layer(kg_id, db_path)
        ext_hash = _layer_fingerprint(ext_e, ext_r)
    conn = _connect(db_path)
    try:
        # The shadow row: same owner, never active, same backend as the live graph, and
        # marked as a release so every enumerator hides it.
        conn.execute(
            """INSERT INTO kg_metadata (kg_id, profile_id, user_uuid, name, description,
                   kg_store_config_id, is_active, created_at, updated_at,
                   released_of, released_version_id, semantic_version, released_extensions_hash)
               VALUES (?, ?, ?, ?, ?, ?, 0, COALESCE((SELECT created_at FROM kg_metadata WHERE kg_id=?), ?), ?, ?, ?, ?, ?)
               ON CONFLICT(kg_id) DO UPDATE SET
                   released_version_id=excluded.released_version_id,
                   released_extensions_hash=excluded.released_extensions_hash,
                   semantic_version=excluded.semantic_version,
                   kg_store_config_id=excluded.kg_store_config_id,
                   name=excluded.name, updated_at=excluded.updated_at, is_active=0""",
            (sid, info["profile_id"], info["user_uuid"],
             f"{info.get('name') or 'Knowledge Graph'} — released {info.get('semantic_version') or version_id}",
             f"Released view of {kg_id} (version {version_id}). Read-only; rebuilt on publish.",
             info.get("kg_store_config_id"), sid, now, now, kg_id, version_id, info.get("semantic_version"),
             ext_hash))
        conn.commit()
    finally:
        conn.close()
    store = GraphStore(info["profile_id"], info["user_uuid"], kg_id=sid, db_path=db_path,
                       kg_store_config_id=info.get("kg_store_config_id"))
    store.clear_graph()
    # the released view: the pinned base (drafts removed) + the published extensions as of now
    base_e, base_r = _without_authored(payload)
    store.import_bulk(base_e + list(ext_e or []), base_r + list(ext_r or []))
    try:
        store.warm_semantic_index()
    except Exception:  # noqa: BLE001
        pass


@_signals.announces("knowledge_graph", "updated", user="user_uuid", ids=("kg_id",), aspect="release")
def drop_released_shadow(kg_id: str, user_uuid: Optional[str] = None,
                         db_path: Optional[str] = None) -> bool:
    """Remove a graph's released view (KG deletion cascade). Fail-soft."""
    try:
        sid = shadow_id(kg_id)
        cur = shadow_state(kg_id, db_path)
        if not cur:
            return False
        info = release_info(kg_id, db_path) or {}
        from components.builtin.knowledge_graph.graph_store import GraphStore
        try:
            GraphStore(info.get("profile_id") or "__default__", info.get("user_uuid") or user_uuid or "system",
                       kg_id=sid, db_path=db_path,
                       kg_store_config_id=info.get("kg_store_config_id")).clear_graph()
        except Exception:  # noqa: BLE001
            pass
        conn = _connect(db_path)
        try:
            conn.execute("DELETE FROM kg_metadata WHERE kg_id=?", (sid,))
            conn.commit()
        finally:
            conn.close()
        # Derived state keyed on the SHADOW id. Versions are keyed on the live kg_id
        # (none here), but the whole retrieval path runs over the shadow's own id, so
        # its usage telemetry (`kg_entity_usage`) is real and must go with it. ONE
        # registry (core.kg_derived_state) decides what "derived state" is.
        try:
            from trusted_data_agent.core import kg_derived_state as _kgds
            _kgds.purge_for_kg(sid)
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"KG release: could not drop the released view of {kg_id}: {exc}")
        return False


def resolve_grounding(kg_id: Optional[str], profile: Optional[Dict[str, Any]],
                      user_uuid: Optional[str] = None,
                      db_path: Optional[str] = None) -> Dict[str, Any]:
    """Which graph an agent GROUNDS on, and why — THE decision, stated once.

    ``{"mode": "live"}`` for Model A. For a released-mode agent:
    ``{"mode": "released", "kg_id": <shadow>, "version_id", "label", "lifecycle"}``, or
    ``{"mode": "released", "fallback": "live", "reason": ...}`` when the graph has no
    release (or it could not be materialised) — the caller then reads the live graph
    and the event says so.
    """
    if ground_on_mode(profile) != MODE_RELEASED:
        return {"mode": MODE_LIVE}
    if not kg_id:
        return {"mode": MODE_RELEASED, "fallback": MODE_LIVE, "reason": "no knowledge graph bound"}
    desc = ensure_released_shadow(kg_id, user_uuid, db_path)
    if not desc:
        return {"mode": MODE_RELEASED, "fallback": MODE_LIVE,
                "reason": "the ontology has no release yet — publish it to ground this agent on a release"}
    return {"mode": MODE_RELEASED, "kg_id": desc["shadow_kg_id"], "version_id": desc["version_id"],
            "label": desc.get("label"), "lifecycle": desc.get("lifecycle")}


def effective_kg_id(kg_id: Optional[str], profile: Optional[Dict[str, Any]],
                    user_uuid: Optional[str] = None, db_path: Optional[str] = None) -> Optional[str]:
    """The kg_id an agent actually reads — the shadow under released mode, else the input."""
    g = resolve_grounding(kg_id, profile, user_uuid, db_path)
    return g.get("kg_id") or kg_id
