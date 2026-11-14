"""
Agent Pack REST API Endpoints

Provides pack-level orchestration endpoints. All actual CRUD is delegated to
AgentPackManager which calls existing internal APIs (ConfigManager,
CollectionDatabase, ChromaDB).

Endpoints:
- POST   /v1/agent-packs/import              Import an .agentpack file
- POST   /v1/agent-packs/export              Export a genie coordinator as .agentpack
- POST   /v1/agent-packs/create              Create .agentpack from selected profiles
- GET    /v1/agent-packs                     List installed packs
- GET    /v1/agent-packs/<installation_id>   Get pack details
- PUT    /v1/agent-packs/<installation_id>   Update pack metadata (name, description, version)
- DELETE /v1/agent-packs/<installation_id>   Uninstall a pack
- POST   /v1/agent-packs/notify-author        Send one-time introduction email to upstream repo author
- POST   /v1/agent-packs/notify-author-update Send one-time update email (once per source commit)

Marketplace endpoints:
- POST   /v1/agent-packs/<installation_id>/publish         Publish to marketplace (metadata only)
- GET    /v1/marketplace/agent-packs                       Browse published packs
- GET    /v1/marketplace/agent-packs/<pack_id>             Pack details
- POST   /v1/marketplace/agent-packs/<pack_id>/install     Subscribe (logical sharing grant)
- DELETE /v1/marketplace/agent-packs/<pack_id>/subscribe   Unsubscribe (remove sharing grant)
- POST   /v1/marketplace/agent-packs/<pack_id>/fork        Fork (physical copy into user's account)
- POST   /v1/marketplace/agent-packs/<pack_id>/rate        Rate a pack
- DELETE /v1/marketplace/agent-packs/<pack_id>             Unpublish a pack
"""

import base64
import json
import logging
import os
import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from quart import Blueprint, jsonify, request, send_file

from trusted_data_agent.api.operations import Arg, operation

from trusted_data_agent.auth.middleware import require_auth
from trusted_data_agent.core.agent_pack_manager import AgentPackManager, VectorStoreGovernanceError

agent_pack_bp = Blueprint("agent_packs", __name__)

#: Closed set of validation-error codes the import manager raises as "<code>: <message>".
#: Surfaced as a machine-readable ``error`` field on 400 responses — restAPI.md has always
#: documented one, but only the route-level pre-checks emitted it, so a client had to
#: string-match the human message. A closed set means an arbitrary message prefix can never
#: masquerade as a code.
_VALIDATION_ERROR_CODES = frozenset({
    "dropped_tags",
    "embedding_model_lock_violation",
    "ambiguous_target_installation",
    "posture_downgrade",
})

# Logo for outbound emails — served from the public uderia.com website.
# Upload static/images/Uderia_Logo.png to the uderia.com web root to make this work.
_EMAIL_LOGO_URL = "https://uderia.com/Uderia_Logo.png"
app_logger = logging.getLogger("quart.app")

DB_PATH = Path(__file__).resolve().parents[3] / "tda_auth.db"


def _manager() -> AgentPackManager:
    return AgentPackManager(db_path=str(DB_PATH))


def retire_learning_of_removed(removed_ids, user_uuid) -> None:
    """A bulk profile write is not the remove_profile chokepoint: the removed agents' own learning
    repositories go with them here too (OC-F5 — found by the marketplace probe, M7: unsubscribe
    left the subscribed agent's repository behind; unpublish runs the same cleanup for every
    subscriber)."""
    try:
        from trusted_data_agent.core.agent_learning import retire_for_removed_agent
        for _pid in removed_ids or ():
            retire_for_removed_agent(_pid, user_uuid)
    except Exception as e:  # noqa: BLE001 — never fail an unsubscribe over a notebook
        app_logger.warning(f"pack cleanup: learning repositories of removed agents kept: {e}")


def _cleanup_subscriber_profiles(
    user_uuid: str,
    source_installation_id: int,
    pack_resources: list[dict],
    save: bool = True,
) -> dict:
    """Remove subscribed profiles for one subscriber on unsubscribe/unpublish.

    Only profiles stamped with ``is_subscribed=True`` AND
    ``source_pack_installation_id == source_installation_id`` are removed.
    Profiles created before the subscribe (no markers) are always left intact.

    Subscribed profiles are always deleted — subscribers who want to customise
    a profile beyond the permitted LLM change should fork the pack instead.

    Args:
        save: When False, only compute what would be removed without touching the DB.
              The caller is responsible for calling save_profiles(result["remaining"])
              after archiving dependent sessions. Defaults to True for backward compat.

    Returns a dict with keys: profiles_removed, profiles_converted, removed_ids, remaining.
    """
    from trusted_data_agent.core.config_manager import get_config_manager

    config_manager = get_config_manager()
    profiles = config_manager.get_profiles(user_uuid)

    # Tags the pack owns — used for legacy-orphan detection below.
    pack_profile_tags = {
        r.get("resource_tag") for r in pack_resources
        if r.get("resource_type") == "profile" and r.get("resource_tag")
    }

    remaining: list[dict] = []
    profiles_removed = 0
    removed_ids: set[str] = set()

    for p in profiles:
        is_this_pack = (
            p.get("is_subscribed")
            and p.get("source_pack_installation_id") == source_installation_id
        )
        if not is_this_pack:
            # Legacy fallback: profiles created before the marker system (no is_subscribed
            # flag) that share the same tag as a pack profile are treated as orphan
            # subscribe artifacts and removed.
            if not p.get("is_subscribed") and p.get("tag") in pack_profile_tags:
                removed_ids.add(p["id"])
                profiles_removed += 1
                app_logger.info(
                    f"pack cleanup: removing legacy orphan profile @{p.get('tag')} "
                    f"(user={user_uuid}, no subscribe markers, tag matches pack)"
                )
                continue
            remaining.append(p)
            continue

        # Always delete subscribed profiles — fork is the path for customisation.
        removed_ids.add(p["id"])
        profiles_removed += 1

    # The knowledge-graph COPIES the subscription materialized go with the profiles that
    # hosted them (P1 of PACK_SUBSCRIBE_COMPLETION.md) — keyed on the removed profiles,
    # not on the listing, so the publisher-unpublish cascade (listing already gone) and
    # a plain unsubscribe clean up the same way. Skills are the publisher's files: kept.
    kgs_removed = 0
    if removed_ids:
        try:
            from trusted_data_agent.core import kg_subscription as _kgsub
            _c = sqlite_util.connect(str(DB_PATH))
            _c.row_factory = sqlite3.Row
            try:
                _rows = _c.execute(
                    "SELECT * FROM kg_subscriptions WHERE user_uuid = ? AND source_kind = 'agent_pack' "
                    f"AND target_profile_id IN ({','.join('?' * len(removed_ids))})",
                    (user_uuid, *removed_ids)).fetchall()
                for _r in _rows:
                    _kgsub.delete_copy(_c, _r)
                    kgs_removed += 1
                _c.commit()
            finally:
                _c.close()
        except Exception as _kg_err:
            app_logger.warning(f"pack cleanup: subscribed KG copies not removed: {_kg_err}")

    if save:
        config_manager.save_profiles(remaining, user_uuid)
        retire_learning_of_removed(removed_ids, user_uuid)

        # Clear default_profile_id if the removed profile was the default
        default_id = config_manager.get_default_profile_id(user_uuid)
        if default_id and default_id in removed_ids:
            config_manager.set_default_profile_id(None, user_uuid)
            app_logger.info(
                f"pack cleanup: cleared default_profile_id for user {user_uuid} "
                f"(subscribed profile {default_id} was removed)"
            )

    return {
        "profiles_removed": profiles_removed,
        "profiles_converted": 0,
        "removed_ids": removed_ids,
        "remaining": remaining,
        "kgs_removed": kgs_removed,
    }


def _key_planner_subscriptions(user_uuid: str, built_profiles: list) -> dict:
    """Stamp `collection_subscriptions.mcp_server_id` for every planner repository a
    subscribed profile reads, from the server that profile was bound to. Returns
    {collection_id: mcp_server_id} for what was keyed. A collection two profiles read
    on different servers keeps the first — one reference, one key (the planner's own
    rule: a repository is keyed to ONE server)."""
    from trusted_data_agent.auth.database import get_db_session
    from trusted_data_agent.auth.models import CollectionSubscription
    from trusted_data_agent.core.collection_db import get_collection_db
    wanted: dict = {}
    for prof in built_profiles or []:
        srv = prof.get("mcpServerId")
        if not srv:
            continue
        for cid in prof.get("ragCollections") or []:
            if cid in (None, "*"):
                continue
            try:
                wanted.setdefault(int(cid), srv)
            except (TypeError, ValueError):
                continue
    if not wanted:
        return {}
    cdb = get_collection_db()
    keyed: dict = {}
    with get_db_session() as session:
        for cid, srv in wanted.items():
            meta = cdb.get_collection_by_id(cid) or {}
            if meta.get("repository_type") != "planner":
                continue
            row = session.query(CollectionSubscription).filter_by(
                user_id=user_uuid, source_collection_id=cid).first()
            if row is None:
                continue
            row.mcp_server_id = srv
            keyed[cid] = srv
    return keyed


def _pack_profile_classes(cursor, installation_id: int) -> dict:
    """{tag: profile_type} for a pack's members, read from its manifest.

    Served so a card can colour each member chip by its IFOC class in EVERY seat —
    a consumer browsing someone else's pack has no local profile to resolve the tag
    against, so a client-side lookup would render the whole roster the unknown grey.
    A tag whose type is missing is simply absent: the chip then falls back to the
    local lookup and finally to grey, never to a guessed class.
    """
    cursor.execute(
        "SELECT manifest_json FROM agent_pack_installations WHERE id = ?",
        (installation_id,),
    )
    row = cursor.fetchone()
    if not row or not row["manifest_json"]:
        return {}
    try:
        profiles = json.loads(row["manifest_json"]).get("profiles", []) or []
        return {p["tag"]: p["profile_type"] for p in profiles
                if p.get("tag") and p.get("profile_type")}
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {}


def _has_tool_profiles(cursor, installation_id: int) -> bool:
    """Check if an agent pack installation contains tool_enabled profiles."""
    cursor.execute(
        "SELECT manifest_json FROM agent_pack_installations WHERE id = ?",
        (installation_id,),
    )
    row = cursor.fetchone()
    if not row or not row["manifest_json"]:
        return False
    try:
        manifest = json.loads(row["manifest_json"])
        profiles = manifest.get("profiles", [])
        return any(
            p.get("profile_type") == "tool_enabled" or p.get("requires_mcp")
            for p in profiles
        )
    except (json.JSONDecodeError, TypeError):
        return False



# ── Pack subscription SYNC (P2 of docs/roadmap/PACK_SUBSCRIBE_COMPLETION.md) ───────────
#
# A subscription re-materialized from the publisher's LIVE pack, in place: profiles updated
# under their existing ids (`_upsert_pack_profile`, the in-place import's merge), new tags
# added, dropped tags removed with their sessions archived, collection references
# reconciled and re-keyed, knowledge-graph copies rebuilt / kept / removed by export hash,
# the ledger reconciled, the publisher's trust reinstated. Lives here beside the subscribe
# and unsubscribe orchestration it shares helpers with (the KG marketplace keeps its
# fan-out in its routes module the same way) and is callable OUTSIDE a request — the
# publisher's update_in_place fans out to every grantee through the same function.
#
# The `_sync_*` seams are the DB reads and writes; the gate patches them and drives the
# orchestration between them.

class PackSyncError(Exception):
    """An expected refusal: carries the HTTP status and any structured detail."""
    def __init__(self, message: str, status: int = 400, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


#: the subscription facts a built profile carries that the in-place merge must not see
#: (they are not import-writable keys — the live copy keeps its own, re-stamped below)
_SUBSCRIPTION_META_KEYS = ("is_subscribed", "source_pack_installation_id", "subscribed_at", "pack_published_at")


def _sync_load_listing(pack_id: str) -> dict | None:
    conn = sqlite_util.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM marketplace_agent_packs WHERE id = ?", (pack_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _sync_has_grant(user_uuid: str, source_installation_id) -> bool:
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        return conn.execute(
            "SELECT 1 FROM marketplace_sharing_grants WHERE resource_type = 'agent_pack' "
            "AND resource_id = ? AND grantee_user_id = ? LIMIT 1",
            (str(source_installation_id), user_uuid)).fetchone() is not None
    finally:
        conn.close()


def _sync_grantees(source_installation_id) -> list:
    """Every subscriber of a listing (the fan-out's population)."""
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        return [r[0] for r in conn.execute(
            "SELECT grantee_user_id FROM marketplace_sharing_grants WHERE resource_type = 'agent_pack' "
            "AND resource_id = ? ORDER BY created_at", (str(source_installation_id),)).fetchall()]
    finally:
        conn.close()


def _sync_publisher_resources(source_installation_id) -> tuple:
    """(the publisher's ledger rows, the publisher's installation metadata or None)."""
    conn = sqlite_util.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        res = [dict(r) for r in conn.execute(
            "SELECT resource_type, resource_id, resource_tag, resource_role "
            "FROM agent_pack_resources WHERE pack_installation_id = ?", (source_installation_id,)).fetchall()]
        meta = conn.execute(
            "SELECT description, version, author, coordinator_tag, pack_type "
            "FROM agent_pack_installations WHERE id = ?", (source_installation_id,)).fetchone()
        return res, (dict(meta) if meta else None)
    finally:
        conn.close()


def _sync_installation_row(user_uuid: str, source_installation_id) -> dict | None:
    """The subscriber's own installation for this listing: {id, manifest, resources}."""
    conn = sqlite_util.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, manifest_json FROM agent_pack_installations WHERE owner_user_id = ? "
            "AND json_extract(manifest_json, '$._source_installation_id') = ? ORDER BY id DESC LIMIT 1",
            (user_uuid, source_installation_id)).fetchone()
        if not row:
            return None
        try:
            manifest = json.loads(row["manifest_json"] or "{}")
        except Exception:
            manifest = {}
        res = [dict(r) for r in conn.execute(
            "SELECT resource_type, resource_id, resource_tag, resource_role, is_owned "
            "FROM agent_pack_resources WHERE pack_installation_id = ?", (row["id"],)).fetchall()]
        return {"id": row["id"], "manifest": manifest, "resources": res}
    finally:
        conn.close()


def _sync_stamp_installation(installation_id, *, manifest: dict | None = None,
                             coordinator_tag=None, coordinator_profile_id=None,
                             synced_at: str | None = None, error: str | None = None) -> None:
    """Write the sync outcome on the subscriber's installation row: on success the
    manifest (the export's, with the subscription's own facts), the coordinator, the
    time, and a cleared error; on failure the error alone (the row keeps its last good
    state — a failed sync never rewrites what the subscriber has)."""
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        if error is not None:
            conn.execute("UPDATE agent_pack_installations SET sync_error = ? WHERE id = ?",
                         (str(error)[:500], installation_id))
        else:
            sets, vals = ["sync_error = NULL"], []
            if manifest is not None:
                sets += ["name = ?", "description = ?", "version = ?", "author = ?", "manifest_json = ?"]
                vals += [manifest.get("name"), manifest.get("description", ""),
                         manifest.get("version", "1.0.0"), manifest.get("author", ""), json.dumps(manifest)]
                sets += ["coordinator_tag = ?", "coordinator_profile_id = ?"]
                vals += [coordinator_tag, coordinator_profile_id]
            if synced_at:
                sets.append("synced_at = ?"); vals.append(synced_at)
            conn.execute(f"UPDATE agent_pack_installations SET {', '.join(sets)} WHERE id = ?",
                         (*vals, installation_id))
        conn.commit()
    finally:
        conn.close()


def _sync_collections(user_uuid: str, old_ids: list, new_ids: list) -> dict:
    """Bring the subscriber's collection references in line with the pack: a reference for
    every pack collection (kept where it exists), the references THIS pack recorded and
    the pack no longer ships removed. Returns {added: [...], removed: [...]}."""
    from trusted_data_agent.auth.database import get_db_session
    from trusted_data_agent.auth.models import CollectionSubscription
    added, removed = [], []
    with get_db_session() as session:
        for cid in new_ids:
            row = session.query(CollectionSubscription).filter_by(
                user_id=user_uuid, source_collection_id=int(cid)).first()
            if row is None:
                session.add(CollectionSubscription(user_id=user_uuid, source_collection_id=int(cid), enabled=True))
                added.append(int(cid))
        for cid in old_ids:
            if int(cid) in {int(n) for n in new_ids}:
                continue
            row = session.query(CollectionSubscription).filter_by(
                user_id=user_uuid, source_collection_id=int(cid)).first()
            if row is not None:
                session.delete(row)
                removed.append(int(cid))
    return {"added": added, "removed": removed}


def _sync_delete_kg_copies_for_profiles(user_uuid: str, profile_ids: list) -> int:
    """The agent_pack copies hosted by dropped profiles go with them (the unsubscribe rule)."""
    if not profile_ids:
        return 0
    from trusted_data_agent.core import kg_subscription as _kgsub
    n = 0
    conn = sqlite_util.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM kg_subscriptions WHERE user_uuid = ? AND source_kind = 'agent_pack' "
            f"AND target_profile_id IN ({','.join('?' * len(profile_ids))})",
            (user_uuid, *profile_ids)).fetchall()
        for r in rows:
            _kgsub.delete_copy(conn, r)
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def _reinstate_pack_seal(source_installation_id, target_installation_id) -> bool:
    """Re-apply the publisher's PACK seal onto a copy (fork, subscription, sync) — ONE step
    for all three. The honesty gate accepts it only when the copy's composition (its
    members' content, id-independent) equals what the publisher signed. Fails soft."""
    try:
        from trusted_data_agent.core.trust import portability as _trust_portability
        from trusted_data_agent.core.trust import pack as _trust_pack
        if not (source_installation_id and target_installation_id):
            return False
        claim = _trust_portability.capture("agent_pack", str(source_installation_id))
        if not claim:
            return False
        return bool(_trust_portability.reinstate(
            "agent_pack", str(target_installation_id), claim,
            _trust_pack.composition_hash_for(str(target_installation_id))))
    except Exception as e:
        app_logger.warning(f"pack seal not reinstated on installation {target_installation_id}: {e}")
        return False


async def sync_pack_subscription(user_uuid: str, pack_id: str, *, llm_configuration_id=None,
                                 mcp_server_id=None, mcp_bindings: dict | None = None) -> dict:
    """Re-materialize one subscriber's copy of a listing from the publisher's live pack.

    Bindings: the LLM and the per-agent MCP servers are DERIVED from the subscriber's
    existing copies (what they chose at subscribe time); an explicit body value wins, and
    a derived server that no longer exists falls back to the pack-level / name match.
    Posture: the publisher's copy is authoritative for a subscription (it is read-only on
    the subscriber's side), so a weaker posture is never refused — it is REPORTED in
    ``posture_changes`` so the subscriber can see what moved.
    Raises ``PackSyncError`` for an expected refusal; anything else is stamped on the
    installation row as ``sync_error`` and re-raised.
    """
    from trusted_data_agent.core.config_manager import get_config_manager
    from trusted_data_agent.core import profile_config_contract as _pcc

    manager = _manager()
    config_manager = get_config_manager()
    listing = _sync_load_listing(pack_id)
    if not listing:
        raise PackSyncError("Marketplace pack not found", 404)
    source_installation_id = listing["source_installation_id"]
    publisher_user_id = listing["publisher_user_id"]
    pack_name = listing["name"]
    if publisher_user_id == user_uuid:
        raise PackSyncError("Cannot sync your own pack", 400)
    if not _sync_has_grant(user_uuid, source_installation_id):
        raise PackSyncError("Not subscribed to this pack", 404)
    pack_resources, install_meta = _sync_publisher_resources(source_installation_id)
    pub_profile_res = [r for r in pack_resources if r["resource_type"] == "profile"]
    if not pub_profile_res:
        raise PackSyncError("This pack has no profiles to sync", 409)
    missing = [r.get("resource_tag") or r["resource_id"] for r in pub_profile_res
               if not config_manager.get_profile(r["resource_id"], publisher_user_id)]
    if missing:
        raise PackSyncError(
            "This pack can no longer be synced: the publisher has removed "
            f"the following profile(s): {', '.join(f'@{t}' for t in missing)}.",
            409, missing_profiles=missing)

    inst = _sync_installation_row(user_uuid, source_installation_id)
    installation_id = inst["id"] if inst else None
    try:
        return await _sync_apply(
            manager, config_manager, _pcc, user_uuid=user_uuid, pack_id=pack_id, listing=listing,
            pack_resources=pack_resources, install_meta=install_meta, inst=inst,
            llm_configuration_id=llm_configuration_id, mcp_server_id=mcp_server_id,
            mcp_bindings=mcp_bindings)
    except PackSyncError:
        raise
    except Exception as e:
        if installation_id:
            try:
                _sync_stamp_installation(installation_id, error=str(e))
            except Exception:
                pass
        raise


async def _sync_apply(manager, config_manager, _pcc, *, user_uuid, pack_id, listing, pack_resources,
                      install_meta, inst, llm_configuration_id, mcp_server_id, mcp_bindings) -> dict:
    from collections import Counter
    from trusted_data_agent.core.agent_pack_db import AgentPackDB
    from trusted_data_agent.core.session_manager import (
        archive_sessions_by_profile, archive_sessions_by_collection)

    source_installation_id = listing["source_installation_id"]
    publisher_user_id = listing["publisher_user_id"]
    pack_name = listing["name"]
    content_stamp = listing.get("content_updated_at") or listing.get("updated_at")
    pub_profile_res = [r for r in pack_resources if r["resource_type"] == "profile"]
    pack_tags = {r.get("resource_tag") for r in pub_profile_res if r.get("resource_tag")}
    tag_to_role = {r.get("resource_tag"): r.get("resource_role", "profile") for r in pub_profile_res if r.get("resource_tag")}

    # ── The subscriber's existing copies: the live profiles are the truth, the ledger
    # names a copy that was lost out-of-band (re-created at its id, references kept).
    existing_profiles = [p for p in config_manager.get_profiles(user_uuid)
                         if p.get("is_subscribed") and p.get("source_pack_installation_id") == source_installation_id]
    existing_by_tag = {p.get("tag"): p for p in existing_profiles if p.get("tag")}
    # ids kept for the tags the pack STILL ships (a dropped tag's copy is removed below)
    existing_ids = {tag: p["id"] for tag, p in existing_by_tag.items() if tag in pack_tags}
    for r in (inst["resources"] if inst else []):
        if r["resource_type"] == "profile" and r.get("resource_tag") in pack_tags:
            existing_ids.setdefault(r["resource_tag"], r["resource_id"])

    # ── Bindings derived from the copies, overridden by the body.
    subscriber_llms = config_manager.get_llm_configurations(user_uuid) or []
    subscriber_llm_ids = {c.get("id") for c in subscriber_llms}
    if not llm_configuration_id:
        llm_configuration_id = next((p.get("llmConfigurationId") for p in existing_profiles
                                     if p.get("llmConfigurationId") in subscriber_llm_ids), None)
    if not llm_configuration_id:
        if not subscriber_llms:
            raise PackSyncError("No LLM configuration found in your account. Add one before syncing.", 400)
        llm_configuration_id = subscriber_llms[0]["id"]
    try:
        live_servers = {srv.get("id") for srv in (config_manager.get_mcp_servers(user_uuid) or [])}
    except Exception:
        live_servers = set()
    derived_bindings = {tag: p.get("mcpServerId") for tag, p in existing_by_tag.items()
                        if p.get("mcpServerId") and p.get("mcpServerId") in live_servers}
    bindings = {**derived_bindings, **(mcp_bindings or {})}
    if not mcp_server_id and derived_bindings:
        mcp_server_id = Counter(derived_bindings.values()).most_common(1)[0][0]

    try:
        built = manager.build_subscribed_profiles(
            pack_resources=pack_resources, publisher_user_id=publisher_user_id,
            subscriber_user_id=user_uuid, llm_config_id=llm_configuration_id,
            mcp_server_id=mcp_server_id, source_installation_id=source_installation_id,
            pack_published_at=content_stamp, mcp_bindings=bindings or None,
            existing_ids=existing_ids)
    except ValueError as ve:
        raise PackSyncError(str(ve), 400)

    # ── Profiles: existing updated IN PLACE (id preserved), new ones added.
    posture_changes: list = []
    tag_to_profile_id: dict = {}
    profiles_updated = profiles_added = 0
    for prof in built:
        tag = prof.get("tag")
        pid = existing_ids.get(tag)
        if pid:
            live = existing_by_tag.get(tag) or {}
            for d in _pcc.find_downgrades(live, prof):
                posture_changes.append(f"@{tag}: {d.describe()}")
            core = {k: v for k, v in prof.items() if k not in _SUBSCRIPTION_META_KEYS}
            manager._upsert_pack_profile(config_manager, {"tag": tag}, core, pid, user_uuid, tag_to_profile_id)
            config_manager.update_profile(pid, {
                "is_subscribed": True,
                "source_pack_installation_id": source_installation_id,
                "subscribed_at": live.get("subscribed_at") or prof.get("subscribed_at"),
                "pack_published_at": content_stamp,
            }, user_uuid)
            profiles_updated += 1
        else:
            manager._upsert_pack_profile(config_manager, {"tag": tag}, prof, None, user_uuid, tag_to_profile_id)
            profiles_added += 1

    # ── Dropped tags: sessions archived, their KG copies removed, the profile removed.
    dropped = [(tag, p) for tag, p in existing_by_tag.items() if tag not in pack_tags]
    for tag, p in dropped:
        try:
            await archive_sessions_by_profile(p["id"], user_uuid)
        except Exception as e:
            app_logger.warning(f"sync: sessions of dropped @{tag} not archived: {e}")
    dropped_ids = [p["id"] for _, p in dropped]
    kgs_removed_with_profiles = _sync_delete_kg_copies_for_profiles(user_uuid, dropped_ids)
    for pid in dropped_ids:
        config_manager.remove_profile(pid, user_uuid)
    if dropped_ids:
        default_id = config_manager.get_default_profile_id(user_uuid)
        if default_id in dropped_ids:
            config_manager.set_default_profile_id(None, user_uuid)

    # ── Collections: references reconciled, planners re-keyed to the subscriber's servers.
    new_coll_ids = [int(r["resource_id"]) for r in pack_resources if r["resource_type"] == "collection"]
    old_coll_ids = [int(r["resource_id"]) for r in (inst["resources"] if inst else [])
                    if r["resource_type"] == "collection"]
    coll = _sync_collections(user_uuid, old_coll_ids, new_coll_ids)
    for cid in coll["removed"]:
        try:
            await archive_sessions_by_collection(str(cid), user_uuid)
        except Exception as e:
            app_logger.warning(f"sync: sessions of removed collection {cid} not archived: {e}")
    keyed = {}
    try:
        keyed = _key_planner_subscriptions(user_uuid, built)
        if keyed:
            try:
                from trusted_data_agent.agent.rag_retriever import get_rag_retriever
                _r = get_rag_retriever()
                for _cid in keyed:
                    _r and _r.ensure_loaded(_cid)
            except Exception:
                pass
    except Exception as e:
        app_logger.warning(f"sync: planner keying failed: {e}")

    # ── Knowledge graphs + skills: rebuilt / kept / created / removed by export hash.
    members = await manager.materialize_subscription_members(
        subscriber_uuid=user_uuid, publisher_uuid=publisher_user_id,
        publisher_profile_ids=[r["resource_id"] for r in pub_profile_res],
        pack_name=pack_name, pack_listing_id=pack_id,
        tag_to_profile_id=dict(tag_to_profile_id), update=True)

    # ── Ledger: the export's manifest with the subscription's own facts, resources reconciled.
    now = datetime.now(timezone.utc).isoformat()
    manifest = {**(members.get("manifest") or {}),
                "name": pack_name,
                "description": install_meta["description"] if install_meta else "",
                "version": install_meta["version"] if install_meta else "1.0.0",
                "author": install_meta["author"] if install_meta else "",
                "_source_installation_id": source_installation_id}
    coord_tag = install_meta["coordinator_tag"] if install_meta else None
    coord_pid = tag_to_profile_id.get(coord_tag) if coord_tag else None
    pack_type = (install_meta["pack_type"] if install_meta else None) or "bundle"
    ordered_tags = [p.get("tag") for p in built]
    desired = [{"resource_type": "profile", "resource_id": tag_to_profile_id[t], "resource_tag": t,
                "resource_role": tag_to_role.get(t, "profile"), "is_owned": False}
               for t in ordered_tags if t in tag_to_profile_id]
    desired += [{"resource_type": "collection", "resource_id": str(r["resource_id"]),
                 "resource_tag": r.get("resource_tag") or str(r["resource_id"]),
                 "resource_role": "collection", "is_owned": False}
                for r in pack_resources if r["resource_type"] == "collection"]
    desired += [{"resource_type": "knowledge_graph", "resource_id": kid, "resource_tag": kname,
                 "resource_role": "knowledge_graph", "is_owned": False}
                for kid, kname in zip(members.get("kg_ids") or [], members.get("kg_names") or [])]
    desired += [{"resource_type": "skill", "resource_id": sid, "resource_tag": sid,
                 "resource_role": "skill", "is_owned": False} for sid in (members.get("skill_ids") or [])]
    installation_id = inst["id"] if inst else None
    if installation_id:
        AgentPackDB(str(DB_PATH)).reconcile_pack_resources(installation_id, desired)
        _sync_stamp_installation(installation_id, manifest=manifest, coordinator_tag=coord_tag,
                                 coordinator_profile_id=coord_pid, synced_at=now)
    else:
        # a subscription made before the ledger existed — recorded now, as subscribe would
        installation_id = manager._record_installation(
            manifest=manifest, coordinator_tag=coord_tag, coordinator_profile_id=coord_pid,
            pack_type=pack_type,
            profile_ids=[tag_to_profile_id[t] for t in ordered_tags if t in tag_to_profile_id],
            profile_tags=[t for t in ordered_tags if t in tag_to_profile_id],
            profile_roles=[tag_to_role.get(t, "profile") for t in ordered_tags if t in tag_to_profile_id],
            collection_ids=new_coll_ids,
            collection_refs=[r.get("resource_tag") or str(r["resource_id"]) for r in pack_resources if r["resource_type"] == "collection"],
            user_uuid=user_uuid, is_owned=False,
            kg_ids=members.get("kg_ids") or None, kg_names=members.get("kg_names") or None,
            skill_ids=members.get("skill_ids") or None)
        _sync_stamp_installation(installation_id, synced_at=now)
    trust_reinstated = manager._reinstate_member_trust(manifest, installation_id)
    if _reinstate_pack_seal(source_installation_id, installation_id):
        trust_reinstated += 1
    try:
        if members.get("zip_path"):
            Path(members["zip_path"]).unlink(missing_ok=True)
    except Exception:
        pass
    app_logger.info(
        f"sync: user {user_uuid} synced pack '{pack_name}' ({pack_id}): profiles {profiles_updated} updated / "
        f"{profiles_added} added / {len(dropped)} dropped, kgs {members.get('kgs_rebuilt', 0)} rebuilt / "
        f"{members.get('kgs_unchanged', 0)} unchanged / {members.get('kgs_created', 0)} created / "
        f"{members.get('kgs_removed', 0) + kgs_removed_with_profiles} removed, trust {trust_reinstated}")
    return {
        "status": "synced",
        "name": pack_name,
        "installation_id": installation_id,
        "synced_at": now,
        "profiles_updated": profiles_updated,
        "profiles_added": profiles_added,
        "profiles_dropped": len(dropped),
        "dropped_tags": [t for t, _ in dropped],
        "collections_added": len(coll["added"]),
        "collections_removed": len(coll["removed"]),
        "planner_keyed": len(keyed),
        "kgs_created": members.get("kgs_created", 0),
        "kgs_rebuilt": members.get("kgs_rebuilt", 0),
        "kgs_unchanged": members.get("kgs_unchanged", 0),
        "kgs_removed": members.get("kgs_removed", 0) + kgs_removed_with_profiles,
        "skills_recorded": len(members.get("skill_ids") or []),
        "trust_reinstated": trust_reinstated,
        "posture_changes": posture_changes,
        "warnings": members.get("warnings") or [],
    }


#: strong references — a task held only by the loop's weak set can be garbage-collected
_PACK_SYNC_TASKS: set = set()


async def fan_out_pack_sync(listing_id: str) -> dict:
    """After the publisher updates a listing in place: every grantee's copy re-materialized,
    sequentially, each failure stamped on that subscriber's installation row and logged —
    never raised, never blocking the publisher (the KG marketplace's fan-out, for packs)."""
    listing = _sync_load_listing(listing_id)
    if not listing or not listing.get("source_installation_id"):
        return {"synced": 0, "failed": 0}
    ok = failed = 0
    for grantee in _sync_grantees(listing["source_installation_id"]):
        try:
            await sync_pack_subscription(grantee, listing_id)
            ok += 1
        except Exception as e:
            failed += 1
            app_logger.warning(f"pack sync fan-out: subscriber {grantee} of {listing_id} not synced: {e}")
    app_logger.info(f"pack sync fan-out for {listing_id}: {ok} synced, {failed} failed")
    return {"synced": ok, "failed": failed}


def schedule_pack_subscriber_sync(listing_id: str):
    """Fan out in the background — the publisher's response returns first."""
    import asyncio
    if not listing_id:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(fan_out_pack_sync(listing_id))
    _PACK_SYNC_TASKS.add(task)
    task.add_done_callback(_PACK_SYNC_TASKS.discard)
    return task


def _update_available(content_updated_at, synced_at) -> bool:
    """A subscriber's copy is behind when the listing's CONTENT moved after their last sync
    (or their subscribe, for a copy never synced). ``updated_at`` is never read here — it
    moves on every subscribe and fork."""
    if not content_updated_at or not synced_at:
        return False
    try:
        from datetime import datetime as _dt
        a = _dt.fromisoformat(str(content_updated_at).replace("Z", "+00:00"))
        b = _dt.fromisoformat(str(synced_at).replace("Z", "+00:00"))
        if a.tzinfo is None:
            a = a.replace(tzinfo=timezone.utc)
        if b.tzinfo is None:
            b = b.replace(tzinfo=timezone.utc)
        return a > b
    except Exception:
        return str(content_updated_at) > str(synced_at)


def _parse_mcp_bindings(raw) -> dict | None:
    """Explicit per-agent MCP map ``{profile_tag: mcp_server_id}`` from a request.

    Accepts a dict (JSON body) or a JSON-encoded string (multipart form field).
    ``None``/empty → ``None``. Anything else → ValueError (a 400 for the caller).
    """
    if raw is None or raw == "" or raw == {}:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise ValueError("mcp_bindings must be a JSON object of {profile_tag: mcp_server_id}")
    if not isinstance(raw, dict):
        raise ValueError("mcp_bindings must be a JSON object of {profile_tag: mcp_server_id}")
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError("mcp_bindings keys must be profile tags")
        if v is None or v == "":
            continue  # an unbound row — falls back to the pack-level server
        if isinstance(v, dict):
            # A multi-server agent: {"primary": id, "<alias>": id, ...}
            row = {}
            for slot, sid in v.items():
                if not isinstance(slot, str) or not slot.strip():
                    raise ValueError("mcp_bindings server slots must be 'primary' or an alias")
                if sid is None or sid == "":
                    continue
                if not isinstance(sid, str):
                    raise ValueError("mcp_bindings values must be MCP server ids")
                row[slot.strip()] = sid
            if row:
                out[k.strip()] = row
            continue
        if not isinstance(v, str):
            raise ValueError("mcp_bindings values must be MCP server ids")
        out[k.strip()] = v
    return out or None


def _publisher_mcp_requirement_entries(pack_resources: list[dict], publisher_user_id: str) -> list[dict]:
    """Manifest-shaped entries for a listing's live publisher profiles — the same
    derivation of ``mcpServerName`` that subscribe/fork use (publisher server NAME by
    id), so the binding dialog shows exactly the names the import will try to match."""
    from trusted_data_agent.core.config_manager import get_config_manager
    cfg = get_config_manager()
    try:
        pub_servers = {s.get("id"): s.get("name") for s in cfg.get_mcp_servers(publisher_user_id)}
    except Exception:
        pub_servers = {}
    entries = []
    for res in pack_resources:
        if res.get("resource_type") != "profile":
            continue
        prof = cfg.get_profile(res["resource_id"], publisher_user_id)
        if not prof:
            continue
        entry = {
            "tag": prof.get("tag"),
            "name": prof.get("name"),
            "profile_type": prof.get("profile_type"),
            "useMcpTools": bool(prof.get("useMcpTools")),
            "requires_mcp": prof.get("profile_type") == "tool_enabled" or bool(prof.get("useMcpTools")),
        }
        name = prof.get("mcpServerName") or pub_servers.get(prof.get("mcpServerId"))
        if name:
            entry["mcpServerName"] = name
        from trusted_data_agent.core.agent_mcp_servers import (
            MANIFEST_SERVERS_KEY, manifest_servers)
        listed = manifest_servers(prof, pub_servers)
        if listed:
            entry[MANIFEST_SERVERS_KEY] = listed
        entries.append(entry)
    return entries


def _mcp_servers_for(user_uuid: str) -> list[dict]:
    from trusted_data_agent.core.config_manager import get_config_manager
    try:
        return [
            {"id": s.get("id"), "name": s.get("name")}
            for s in get_config_manager().get_mcp_servers(user_uuid)
            if s.get("id") and s.get("name")
        ]
    except Exception:
        return []


# ── Inspect (pre-import) ──────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/inspect", methods=["POST"])
@require_auth
async def inspect_agent_pack(current_user):
    """Read an .agentpack's manifest WITHOUT importing it.

    Returns the pack header plus ``mcp_requirements`` — one row per agent that
    needs an MCP server, with the manifest server name and the id it currently
    resolves to on this instance (or null) — and the caller's ``mcp_servers``,
    so the binding dialog can be rendered before the import is committed.
    """
    files = await request.files
    if "file" not in files:
        return jsonify({"status": "error", "message": "No file provided"}), 400
    uploaded = files["file"]
    if not uploaded or not uploaded.filename:
        return jsonify({"status": "error", "message": "Invalid file"}), 400
    tmp = tempfile.NamedTemporaryFile(suffix=".agentpack", delete=False)
    tmp_path = Path(tmp.name)
    try:
        await uploaded.save(str(tmp_path))
        manager = _manager()
        manifest, profiles = manager.read_manifest_profiles(tmp_path)
        rows = manager.pack_mcp_requirements(profiles, manager._mcp_name_map(current_user.id))
        return jsonify({
            "status": "success",
            "pack": {
                "name": manifest.get("name"),
                "version": manifest.get("version"),
                "format_version": manifest.get("format_version"),
                "profile_count": len(profiles),
            },
            "mcp_requirements": rows,
            "mcp_servers": _mcp_servers_for(current_user.id),
        }), 200
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        app_logger.error(f"Agent pack inspect failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


# ── Import ────────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/import", methods=["POST"])
@require_auth
async def import_agent_pack(current_user):
    """Import an .agentpack file.

    Accepts either:
      - multipart/form-data  →  'file' field + optional 'mcp_server_id' field
      - application/json     →  {"import_path": "...", "mcp_server_id": "..."}
    """
    user_uuid = current_user.id
    tmp_path = None

    try:
        content_type = request.content_type or ""
        mcp_server_id = None
        mcp_bindings = None
        llm_configuration_id = None
        conflict_strategy = None
        force_drop_tags = False
        dry_run = False
        allow_posture_downgrade = False

        def _coerce_bool(v) -> bool:
            return str(v).lower() in ("1", "true", "yes", "y", "on")

        if "multipart/form-data" in content_type:
            files = await request.files
            if "file" not in files:
                return jsonify({"status": "error", "message": "No file provided"}), 400
            uploaded = files["file"]
            if not uploaded or not uploaded.filename:
                return jsonify({"status": "error", "message": "Invalid file"}), 400

            form = await request.form
            mcp_server_id = form.get("mcp_server_id") or None
            mcp_bindings = _parse_mcp_bindings(form.get("mcp_bindings"))
            llm_configuration_id = form.get("llm_configuration_id") or None
            conflict_strategy = form.get("conflict_strategy") or None
            vector_store_config_id = form.get("vector_store_config_id") or None
            force_drop_tags = _coerce_bool(form.get("force_drop_tags", "false"))
            dry_run = _coerce_bool(form.get("dry_run", "false"))
            allow_posture_downgrade = _coerce_bool(
                form.get("allow_posture_downgrade", "false"))

            # Backward compat: some pipelines stuffed JSON into a "data" form
            # field instead of using flat fields. If the flat fields are empty
            # and "data" parses as JSON, pull the relevant keys from it.
            data_blob = form.get("data")
            if data_blob:
                try:
                    parsed = json.loads(data_blob)
                    if isinstance(parsed, dict):
                        conflict_strategy = conflict_strategy or parsed.get("conflict_strategy")
                        mcp_server_id = mcp_server_id or parsed.get("mcp_server_id")
                        mcp_bindings = mcp_bindings or _parse_mcp_bindings(parsed.get("mcp_bindings"))
                        llm_configuration_id = llm_configuration_id or parsed.get("llm_configuration_id")
                        vector_store_config_id = vector_store_config_id or parsed.get("vector_store_config_id")
                        if "force_drop_tags" in parsed:
                            force_drop_tags = bool(parsed["force_drop_tags"])
                        if "dry_run" in parsed:
                            dry_run = bool(parsed["dry_run"])
                        if "allow_posture_downgrade" in parsed:
                            allow_posture_downgrade = bool(parsed["allow_posture_downgrade"])
                except (ValueError, TypeError):
                    pass  # not JSON — ignore

            # Save to temp file
            tmp = tempfile.NamedTemporaryFile(suffix=".agentpack", delete=False)
            tmp_path = Path(tmp.name)
            await uploaded.save(str(tmp_path))
            zip_path = tmp_path

        elif "application/json" in content_type:
            data = await request.get_json()
            if not data or not data.get("import_path"):
                return jsonify({"status": "error", "message": "import_path is required"}), 400
            zip_path = Path(data["import_path"])
            if not zip_path.exists():
                return jsonify({"status": "error", "message": f"File not found: {zip_path}"}), 404
            mcp_server_id = data.get("mcp_server_id") or None
            mcp_bindings = _parse_mcp_bindings(data.get("mcp_bindings"))
            llm_configuration_id = data.get("llm_configuration_id") or None
            conflict_strategy = data.get("conflict_strategy") or None
            vector_store_config_id = data.get("vector_store_config_id") or None
            force_drop_tags = bool(data.get("force_drop_tags", False))
            dry_run = bool(data.get("dry_run", False))
            allow_posture_downgrade = bool(data.get("allow_posture_downgrade", False))

        else:
            return jsonify({
                "status": "error",
                "message": "Content-Type must be multipart/form-data or application/json",
            }), 400

        # Validate strategy + flag combinations early (so callers get a clear error).
        if conflict_strategy is not None and conflict_strategy not in (
            "replace", "expand", "update_in_place"
        ):
            return jsonify({
                "status": "error",
                "message": (
                    f"Invalid conflict_strategy '{conflict_strategy}'. "
                    "Must be one of: replace, expand, update_in_place."
                ),
            }), 400
        if dry_run and conflict_strategy != "update_in_place":
            return jsonify({
                "status": "error",
                "error": "dry_run_not_supported_for_strategy",
                "message": (
                    "dry_run=true is only supported with conflict_strategy=update_in_place."
                ),
            }), 400
        if force_drop_tags and conflict_strategy != "update_in_place":
            return jsonify({
                "status": "error",
                "message": (
                    "force_drop_tags=true is only meaningful with "
                    "conflict_strategy=update_in_place."
                ),
            }), 400
        if allow_posture_downgrade and conflict_strategy != "update_in_place":
            return jsonify({
                "status": "error",
                "error": "allow_posture_downgrade_not_supported_for_strategy",
                "message": (
                    "allow_posture_downgrade=true is only meaningful with "
                    "conflict_strategy=update_in_place."
                ),
            }), 400

        manager = _manager()
        result = await manager.import_pack(
            zip_path=zip_path,
            user_uuid=user_uuid,
            mcp_server_id=mcp_server_id,
            llm_configuration_id=llm_configuration_id,
            conflict_strategy=conflict_strategy,
            vector_store_config_id=vector_store_config_id,
            force_drop_tags=force_drop_tags,
            dry_run=dry_run,
            allow_posture_downgrade=allow_posture_downgrade,
            mcp_bindings=mcp_bindings,
        )
        # update_in_place returns its own status field; pass it through verbatim.
        if result.get("status") == "updated_in_place":
            # the listing's content moved — every subscriber's copy follows, in the background
            schedule_pack_subscriber_sync(result.get("marketplace_listing_id"))
        if result.get("status") in ("dry_run", "dry_run_failed", "updated_in_place"):
            return jsonify(result), 200
        return jsonify({"status": "success", **result}), 200

    except VectorStoreGovernanceError as e:
        app_logger.warning(f"Agent pack import blocked by VS governance: {e}")
        return jsonify({
            "status": "vs_governance_block",
            "message": str(e),
            "blocked_collections": e.blocked_collections,
            "allowed_backends": e.allowed_backends,
            "user_allowed_configs": e.user_allowed_configs,
            "needs_vs_configuration": e.needs_vs_configuration,
        }), 409
    except ValueError as e:
        err_msg = str(e)
        app_logger.warning(f"Agent pack import validation error: {err_msg}")
        if "Tag conflict" in err_msg:
            import re as _re
            conflicting = _re.findall(r"@([\w-]+)", err_msg)   # \w excludes "-": @ANDP-DOMAIN reported as "ANDP"
            return jsonify({
                "status": "tag_conflict",
                "conflicting_tags": conflicting,
                "message": err_msg,
            }), 409
        # Validation failures are raised as "<code>: <human message>". Surface the code as
        # a machine-readable `error` field — restAPI.md has always documented one (e.g.
        # error="dropped_tags") but only the route-level pre-checks ever emitted it, so a
        # client had to string-match the message. Codes come from a closed set so an
        # arbitrary message prefix can never masquerade as one.
        payload = {"status": "error", "message": err_msg}
        code = err_msg.split(":", 1)[0].strip()
        if code in _VALIDATION_ERROR_CODES:
            payload["error"] = code
        return jsonify(payload), 400
    except RuntimeError as e:
        app_logger.error(f"Agent pack import runtime error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        app_logger.error(f"Agent pack import failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Import failed: {e}"}), 500
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ── Export ────────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/export", methods=["POST"])
@require_auth
async def export_agent_pack(current_user):
    """Export a genie coordinator and its sub-profiles as an .agentpack file.

    Body (JSON): {"coordinator_profile_id": "profile-xxx"}
    Returns: .agentpack ZIP file download.
    """
    user_uuid = current_user.id

    try:
        data = await request.get_json()
        if not data or not data.get("coordinator_profile_id"):
            return jsonify({
                "status": "error",
                "message": "coordinator_profile_id is required",
            }), 400

        coordinator_profile_id = data["coordinator_profile_id"]
        manager = _manager()
        zip_path = await manager.export_pack(
            coordinator_profile_id=coordinator_profile_id,
            user_uuid=user_uuid,
        )

        return await send_file(
            str(zip_path),
            mimetype="application/zip",
            as_attachment=True,
            attachment_filename=zip_path.name,
        )

    except ValueError as e:
        app_logger.warning(f"Agent pack export validation error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except RuntimeError as e:
        app_logger.error(f"Agent pack export runtime error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        app_logger.error(f"Agent pack export failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Export failed: {e}"}), 500


# ── Create ───────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/create", methods=["POST"])
@require_auth
async def create_agent_pack(current_user):
    """Create an .agentpack file from selected profiles.

    Body (JSON): {"profile_ids": [...], "name": "...", "description": "..."}
    Returns: .agentpack ZIP file download.
    """
    user_uuid = current_user.id

    try:
        data = await request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "Request body required"}), 400

        profile_ids = data.get("profile_ids", [])
        if not profile_ids:
            return jsonify({"status": "error", "message": "profile_ids is required"}), 400

        manager = _manager()
        zip_path = await manager.export_pack(
            profile_ids=profile_ids,
            user_uuid=user_uuid,
            pack_name=data.get("name", "Agent Pack"),
            pack_description=data.get("description", ""),
        )

        return await send_file(
            str(zip_path),
            mimetype="application/zip",
            as_attachment=True,
            attachment_filename=zip_path.name,
        )

    except ValueError as e:
        app_logger.warning(f"Agent pack create validation error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except RuntimeError as e:
        app_logger.error(f"Agent pack create runtime error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        app_logger.error(f"Agent pack create failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Create failed: {e}"}), 500


# ── Create & Install ─────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/create-and-install", methods=["POST"])
@operation("agent_packs.create_and_install", effect="write", scope="own",
           summary="Bundle some of the caller's agents into a new draft pack — the agents stay where they are; removing the pack later leaves them",
           body={"profile_ids": Arg("array", "the agents to bundle, by @TAG or id", required=True),
                 "name": "the pack's name", "description": "what the pack is for"},
           notes="The pack starts as a draft: proposing and publishing it are the owner's.")
@require_auth
async def create_and_install_agent_pack(current_user):
    """Create an agent pack from selected profiles and install it locally.

    Unlike create+import, this references existing profiles with is_owned=False
    so uninstalling the pack does NOT delete the original resources.

    Body (JSON): {"profile_ids": [...], "name": "...", "description": "..."}
    Returns: JSON with installation details.
    """
    user_uuid = current_user.id

    try:
        data = await request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "Request body required"}), 400

        profile_ids = data.get("profile_ids", [])
        if not profile_ids:
            return jsonify({"status": "error", "message": "profile_ids is required"}), 400

        manager = _manager()
        result = await manager.create_and_install(
            profile_ids=profile_ids,
            user_uuid=user_uuid,
            pack_name=data.get("name", "Agent Pack"),
            pack_description=data.get("description", ""),
        )

        return jsonify(result), 200

    except ValueError as e:
        app_logger.warning(f"Agent pack create-and-install validation error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except RuntimeError as e:
        app_logger.error(f"Agent pack create-and-install runtime error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        app_logger.error(f"Agent pack create-and-install failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Create failed: {e}"}), 500


# ── List ──────────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs", methods=["GET"])
@operation("agent_packs.list", effect="read", scope="own",
           summary="The caller's agent packs — bundles of agents with their knowledge and skills: what each holds, its version, whether and how it is published",
           summary_fields=("installation_id", "name", "version", "profiles_count", "marketplace_visibility"))
@require_auth
async def list_agent_packs(current_user):
    """List installed agent packs for the current user.

    Extends each pack with marketplace_pack_id if published.
    """
    user_uuid = current_user.id

    try:
        manager = _manager()
        packs = await manager.list_packs(user_uuid)

        # Enrich with marketplace_pack_id and sharing_count
        try:
            conn = sqlite_util.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Check which packs are published to marketplace (include visibility + distribution_mode)
            cursor.execute(
                "SELECT id, source_installation_id, visibility, distribution_mode "
                "FROM marketplace_agent_packs "
                "WHERE publisher_user_id = ?",
                (user_uuid,)
            )
            published = {
                row["source_installation_id"]: {
                    "id": row["id"],
                    "visibility": row["visibility"],
                    "distribution_mode": row["distribution_mode"] or "both",
                }
                for row in cursor.fetchall()
            }

            # Structured fork lineage (best-practice: status is a FIELD, never a
            # name suffix). A forked install carries _source_marketplace_id in
            # its manifest_json; surfaces render "via fork" from this, so pack
            # display names stay clean.
            fork_source: dict = {}
            try:
                inst_ids = [p["installation_id"] for p in packs]
                if inst_ids:
                    ph = ",".join("?" * len(inst_ids))
                    cursor.execute(
                        f"SELECT id, manifest_json FROM agent_pack_installations "
                        f"WHERE id IN ({ph})", inst_ids)
                    for row in cursor.fetchall():
                        try:
                            man = json.loads(row["manifest_json"] or "{}")
                            src = man.get("_source_marketplace_id")
                            if src is not None:
                                fork_source[row["id"]] = src
                        except Exception:
                            continue
            except Exception:
                pass
            for pack in packs:
                src = fork_source.get(pack["installation_id"])
                pack["is_fork"] = src is not None
                pack["forked_from_marketplace_id"] = src

            # Get sharing counts by installation_id (grants use installation_id as resource_id)
            installation_ids = [str(p["installation_id"]) for p in packs]
            sharing_counts = {}
            if installation_ids:
                placeholders = ",".join("?" * len(installation_ids))
                cursor.execute(
                    f"SELECT resource_id, COUNT(*) as cnt FROM marketplace_sharing_grants "
                    f"WHERE resource_type = 'agent_pack' AND resource_id IN ({placeholders}) "
                    f"GROUP BY resource_id",
                    installation_ids,
                )
                sharing_counts = {row["resource_id"]: row["cnt"] for row in cursor.fetchall()}

            for pack in packs:
                if pack.get("is_owned", True):
                    # Only enrich owned packs with publish/sharing info
                    pack.pop("_source_installation_id", None)
                    mp = published.get(pack["installation_id"])
                    pack["marketplace_pack_id"] = mp["id"] if mp else None
                    pack["marketplace_visibility"] = mp["visibility"] if mp else None
                    pack["marketplace_distribution_mode"] = mp["distribution_mode"] if mp else None
                    pack["sharing_count"] = sharing_counts.get(str(pack["installation_id"]), 0)
                else:
                    # Subscribed packs — find the marketplace_pack_id for unsubscribe.
                    # Prefer _source_installation_id (subscriber-side installation pointing to
                    # publisher's original), fall back to installation_id (legacy sharing-grant rows).
                    src_id = pack.pop("_source_installation_id", None) or pack["installation_id"]
                    cursor.execute(
                        "SELECT id, distribution_mode, content_updated_at FROM marketplace_agent_packs "
                        "WHERE source_installation_id = ?",
                        (src_id,),
                    )
                    mp_row = cursor.fetchone()
                    pack["marketplace_pack_id"] = mp_row["id"] if mp_row else None
                    pack["marketplace_visibility"] = None
                    pack["marketplace_distribution_mode"] = (mp_row["distribution_mode"] or "both") if mp_row else None
                    pack["sharing_count"] = 0
                    # sync state of THIS copy vs the listing's content stamp
                    cursor.execute(
                        "SELECT synced_at, installed_at, sync_error FROM agent_pack_installations WHERE id = ?",
                        (pack["installation_id"],),
                    )
                    _sub = cursor.fetchone()
                    _synced = (_sub["synced_at"] or _sub["installed_at"]) if _sub else None
                    pack["synced_at"] = _synced
                    pack["sync_error"] = _sub["sync_error"] if _sub else None
                    pack["content_updated_at"] = mp_row["content_updated_at"] if mp_row else None
                    pack["update_available"] = _update_available(pack["content_updated_at"], _synced)

            conn.close()
        except Exception as e:
            # Table may not exist yet on first bootstrap
            app_logger.debug(f"Agent pack enrichment skipped: {e}")

        return jsonify({"status": "success", "packs": packs}), 200

    except Exception as e:
        app_logger.error(f"Failed to list agent packs: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to list packs: {e}"}), 500


# ── Details ───────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>", methods=["GET"])
@operation("agent_packs.get", effect="read", scope="own",
           summary="One of the caller's packs in full: its agents, repositories, graphs and skills", path={"installation_id": "the installed pack (agent_packs.list lists them)"})
@require_auth
async def get_agent_pack_details(current_user, installation_id: int):
    """Get full details of an installed agent pack."""
    user_uuid = current_user.id

    try:
        manager = _manager()
        details = await manager.get_pack_details(installation_id, user_uuid)
        return jsonify({"status": "success", **details}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 404
    except Exception as e:
        app_logger.error(f"Failed to get agent pack details: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to get details: {e}"}), 500


# ── Governance Lifecycle (Tier 3.2 / P7 — packs as governed data products) ────

def _pack_actor_tier(current_user):
    if current_user.is_admin or getattr(current_user, "profile_tier", None) == "admin":
        return "admin"
    return getattr(current_user, "profile_tier", None) or "user"


@agent_pack_bp.route("/v1/agent-packs/governance/policy", methods=["GET"])
@operation("agent_packs.governance_policy", effect="read", scope="instance",
           summary="This deployment's rules for publishing packs: whether a pack needs approval, who approves, whether an untrusted pack may be published")
@require_auth
async def get_pack_governance_policy_route(current_user):
    """The platform pack-governance policy (drives which lifecycle actions the UI offers)."""
    from trusted_data_agent.core import pack_governance
    pol = pack_governance.get_pack_governance_policy()
    return jsonify({"status": "success", "policy": {
        "require_approval": pol.require_approval,
        "approver_tiers": sorted(pol.approver_tiers),
        "block_untrusted_publish": pol.block_nonconformant_publish,
    }}), 200


@agent_pack_bp.route("/v1/agent-packs/governance/policy", methods=["PUT"])
@operation("agent_packs.governance_policy_set", effect="human_only", scope="instance", tier="admin",
           summary="Set the deployment's rules for publishing packs — an administrator's act")
@require_auth
async def set_pack_governance_policy_route(current_user):
    """Admin-only: set the pack-governance policy (persisted to system_settings)."""
    if not (current_user.is_admin or getattr(current_user, "profile_tier", None) == "admin"):
        return jsonify({"status": "error", "message": "Admin only"}), 403
    import json as _json
    import uuid as _uuid
    from datetime import datetime, timezone
    body = (await request.get_json(silent=True)) or {}
    updates = {}
    if "require_approval" in body:
        updates["pack_require_approval"] = "true" if body["require_approval"] else "false"
    if "block_untrusted_publish" in body:
        updates["pack_block_untrusted_publish"] = "true" if body["block_untrusted_publish"] else "false"
    if "approver_tiers" in body:
        updates["pack_approver_tiers"] = _json.dumps(list(body["approver_tiers"]))
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        for k, v in updates.items():
            conn.execute(
                "INSERT INTO system_settings (id, setting_key, setting_value, created_at, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(setting_key) DO UPDATE SET "
                "setting_value=excluded.setting_value, updated_at=excluded.updated_at",
                (str(_uuid.uuid4()), k, v, now, now))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "success", "updated": list(updates.keys())}), 200


@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>/lifecycle", methods=["GET"])
@operation("agent_packs.lifecycle", effect="read", scope="own",
           summary="Where a pack stands on its way to publication — draft, proposed, approved, published — with its audit trail and the policy that applies",
           path={"installation_id": "the installed pack (agent_packs.list lists them)"})
@require_auth
async def get_pack_lifecycle_route(current_user, installation_id: int):
    """The pack's lifecycle state + audit trail + the active policy — for its owner, and for a
    tier the governance policy names as an approver (they decide what is proposed). Anyone else
    reads not-found: until Nov 2026 any signed-in user could read any pack's lifecycle and audit
    trail by its id."""
    from trusted_data_agent.core import pack_governance
    state = pack_governance.get_pack_lifecycle(installation_id)
    if not state:
        return jsonify({"status": "error", "message": "Pack not found"}), 404
    pol = pack_governance.get_pack_governance_policy()
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT owner_user_id FROM agent_pack_installations WHERE id=?", (installation_id,)).fetchone()
    finally:
        conn.close()
    if not row or (row[0] != current_user.id and _pack_actor_tier(current_user) not in pol.approver_tiers):
        return jsonify({"status": "error", "message": "Pack not found"}), 404
    return jsonify({
        "status": "success",
        "lifecycle": state,
        "audit": pack_governance.get_pack_audit(installation_id),
        "policy": {"require_approval": pol.require_approval,
                   "block_untrusted_publish": pol.block_nonconformant_publish},
    }), 200


@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>/sla", methods=["POST"])
@operation("agent_packs.sla", effect="human_only", scope="own",
           summary="Set a pack's service level — a promise to its subscribers, the owner's to make",
           path={"installation_id": "the installed pack (agent_packs.list lists them)"})
@require_auth
async def set_pack_sla_route(current_user, installation_id: int):
    """Owner-only: set/clear the pack's SLA tier instantly (frictionless). Body: {sla_tier}."""
    from trusted_data_agent.core import pack_governance
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT owner_user_id FROM agent_pack_installations WHERE id=?", (installation_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"status": "error", "message": "Pack not found"}), 404
    if row[0] != current_user.id and not (current_user.is_admin or getattr(current_user, "profile_tier", None) == "admin"):
        return jsonify({"status": "error", "message": "Only the owner can set the SLA"}), 403
    body = (await request.get_json(silent=True)) or {}
    ok = pack_governance.set_sla(installation_id, body.get("sla_tier"))
    return jsonify({"status": "success" if ok else "error", "sla_tier": body.get("sla_tier")}), (200 if ok else 400)


@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>/lifecycle/<action>", methods=["POST"])
@operation("agent_packs.lifecycle_transition", effect="human_only", scope="own",
           summary="Move a pack along its lifecycle — propose, approve, reject, publish, deprecate — each a person's decision",
           path={"installation_id": "the installed pack (agent_packs.list lists them)", "action": "propose · approve · reject · publish · deprecate"})
@require_auth
async def transition_pack_lifecycle_route(current_user, installation_id: int, action: str):
    """Execute a lifecycle transition (propose/approve/reject/publish/deprecate).
    Body (optional): {sla_tier, override}. Denied transitions return 409 (audited)."""
    from trusted_data_agent.core import pack_governance
    from trusted_data_agent.core.governance_lifecycle import LifecycleAction
    if action not in {a.value for a in LifecycleAction}:
        return jsonify({"status": "error", "message": f"Unknown action '{action}'"}), 400
    conn = sqlite_util.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT owner_user_id FROM agent_pack_installations WHERE id=?", (installation_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"status": "error", "message": "Pack not found"}), 404
    is_owner = (row[0] == current_user.id)
    body = (await request.get_json(silent=True)) or {}
    result = await pack_governance.transition_pack(
        installation_id, action, user_uuid=current_user.id, actor_tier=_pack_actor_tier(current_user),
        is_owner=is_owner, sla_tier=body.get("sla_tier"), override_nonconformant=bool(body.get("override")))
    code = 200 if result.get("allowed") else 409
    return jsonify({"status": "success" if result.get("allowed") else "denied", **result}), code


# ── Update Pack Metadata ──────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>", methods=["PUT"])
@operation("agent_packs.update", effect="write", scope="own",
           summary="Rename a pack, change its description or its version",
           path={"installation_id": "the installed pack (agent_packs.list lists them)"},
           body={"name": "its name", "description": "what it is for", "version": "its version, e.g. 1.2.0"})
@require_auth
async def update_agent_pack(current_user, installation_id: int):
    """Update the editable metadata (name, description, version) of an installed pack."""
    user_uuid = current_user.id

    try:
        data = await request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "Request body required"}), 400

        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"status": "error", "message": "name is required"}), 400

        description = (data.get("description") or "").strip()
        version = (data.get("version") or "").strip()

        conn = sqlite_util.connect(str(DB_PATH))
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT id, manifest_json FROM agent_pack_installations WHERE id = ? AND owner_user_id = ?",
                (installation_id, user_uuid)
            )
            row = cursor.fetchone()
            if not row:
                return jsonify({"status": "error", "message": "Pack not found or access denied"}), 404

            manifest_json = row[1]
            try:
                manifest = json.loads(manifest_json) if manifest_json else {}
            except Exception:
                manifest = {}

            manifest["name"] = name
            if description:
                manifest["description"] = description
            if version:
                manifest["version"] = version

            cursor.execute(
                """UPDATE agent_pack_installations
                   SET name = ?, description = ?, version = ?, manifest_json = ?
                   WHERE id = ?""",
                (name, description or None, version or None, json.dumps(manifest), installation_id)
            )
            conn.commit()
        finally:
            conn.close()

        # Optionally update profiles
        profile_ids = data.get("profile_ids")
        profiles_result = None
        if profile_ids is not None:
            if not isinstance(profile_ids, list) or not profile_ids:
                return jsonify({"status": "error", "message": "profile_ids must be a non-empty list"}), 400
            manager = _manager()
            profiles_result = await manager.update_pack_profiles(installation_id, user_uuid, profile_ids)

        response = {
            "status": "ok",
            "pack": {"installation_id": installation_id, "name": name, "description": description, "version": version}
        }
        if profiles_result:
            response["pack"].update(profiles_result)

        return jsonify(response), 200

    except Exception as e:
        app_logger.error(f"Failed to update agent pack {installation_id}: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to update pack: {e}"}), 500


# ── Check Active Sessions Before Deletion ────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>/check-sessions", methods=["GET"])
@require_auth
async def check_pack_active_sessions(current_user, installation_id: int):
    """
    DEPRECATED: Check for active (non-archived) sessions using this pack's profiles or collections.

    **Deprecation Notice:**
    This endpoint is deprecated and will be removed in a future version.
    Use the unified relationships endpoint instead:

        GET /api/v1/artifacts/agent-pack/{installation_id}/relationships

    The unified endpoint provides:
    - Comprehensive relationship detection across all pack-managed resources
    - Deletion safety analysis (blockers, warnings, cascade effects)
    - Consistent response format across all artifact types
    - Better performance (single source of truth vs multiple file scans)

    Migration path:
    - Replace: checkData.active_session_count → checkData.relationships.sessions.active_count
    - Replace: checkData.active_sessions → checkData.relationships.sessions.items (filter by !is_archived)
    - Add: Check checkData.deletion_info.blockers for deletion blockers
    - Add: Check checkData.deletion_info.warnings for comprehensive warnings
    - Add: Access checkData.relationships.profiles for affected profiles
    - Add: Access checkData.relationships.agent_packs for pack metadata

    **Removal Target:** Q2 2026
    """
    import logging
    app_logger = logging.getLogger("quart.app")
    app_logger.warning(
        f"DEPRECATED endpoint called: GET /v1/agent-packs/{installation_id}/check-sessions. "
        "Use GET /api/v1/artifacts/agent-pack/{installation_id}/relationships instead."
    )
    user_uuid = current_user.id

    try:
        from trusted_data_agent.core.agent_pack_db import AgentPackDB
        from pathlib import Path
        import json

        pack_db = AgentPackDB(DB_PATH)

        # Get pack resources (profiles and collections)
        resources = pack_db.get_resources_for_pack(installation_id)
        profile_ids = [r["resource_id"] for r in resources if r["resource_type"] == "profile"]
        collection_ids = [r["resource_id"] for r in resources if r["resource_type"] == "collection"]

        # Check active sessions via the backend-agnostic public API
        from trusted_data_agent.core import session_manager

        active_sessions = []
        for session_data in await session_manager.get_user_session_bodies(user_uuid):
            try:
                # Skip archived sessions (treat null as not archived)
                if session_data.get("is_archived") is True:
                    continue

                # Check if session uses any of the pack's profiles (current or historical)
                session_profile_id = session_data.get("profile_id")
                profile_tags_used = session_data.get("profile_tags_used", [])

                # Check current profile
                if session_profile_id in profile_ids:
                    active_sessions.append({
                        "session_id": session_data.get("id"),
                        "session_name": session_data.get("name", "Unnamed Session"),
                        "reason": f"uses profile"
                    })
                    continue

                # Check historical profile usage (profile_tags_used stores profile_ids)
                if any(profile_id in profile_ids for profile_id in profile_tags_used):
                    active_sessions.append({
                        "session_id": session_data.get("id"),
                        "session_name": session_data.get("name", "Unnamed Session"),
                        "reason": f"used profile in history"
                    })
                    continue

                # Check if session uses any of the pack's collections
                # Collections are referenced in rag_collection_id or profile's collection_id
                rag_collection_id = session_data.get("rag_collection_id")
                if rag_collection_id:
                    # Convert to int for comparison (collection_ids are strings from resource table)
                    try:
                        if str(rag_collection_id) in [str(cid) for cid in collection_ids]:
                            active_sessions.append({
                                "session_id": session_data.get("id"),
                                "session_name": session_data.get("name", "Unnamed Session"),
                                "reason": f"uses collection"
                            })
                            continue
                    except (ValueError, TypeError):
                        pass

            except Exception as e:
                app_logger.warning(f"Error checking session {session_data.get('id')}: {e}")
                continue

        return jsonify({
            "status": "success",
            "active_session_count": len(active_sessions),
            "active_sessions": active_sessions[:5],  # Limit to 5 for display
            "profile_ids": profile_ids,
            "collection_ids": collection_ids
        }), 200

    except Exception as e:
        app_logger.error(f"Error checking active sessions: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Uninstall ─────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>", methods=["DELETE"])
@require_auth
async def uninstall_agent_pack(current_user, installation_id: int):
    """Uninstall an agent pack — removes all profiles and collections it created."""
    user_uuid = current_user.id

    try:
        manager = _manager()

        # If this installation was a marketplace FORK, capture the source pack id BEFORE
        # uninstall removes the installation row — so we can clear the per-user install
        # record afterwards and the marketplace card returns to "Fork" (symmetry with
        # unsubscribe). Forks created before this stamp existed simply won't have it.
        source_marketplace_id = None
        try:
            _c = sqlite_util.connect(str(DB_PATH))
            _c.row_factory = sqlite3.Row
            _row = _c.execute(
                "SELECT manifest_json FROM agent_pack_installations WHERE id = ?", (installation_id,)
            ).fetchone()
            _c.close()
            if _row and _row["manifest_json"]:
                source_marketplace_id = (json.loads(_row["manifest_json"]) or {}).get("_source_marketplace_id")
        except Exception as _src_err:
            app_logger.warning(f"Could not read source marketplace id for installation {installation_id}: {_src_err}")

        result = await manager.uninstall_pack(installation_id, user_uuid)

        # Auto-unpublish any marketplace listing tied to this installation
        try:
            conn = sqlite_util.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM marketplace_agent_packs WHERE source_installation_id = ? AND publisher_user_id = ?",
                (installation_id, user_uuid),
            )
            if cur.rowcount:
                app_logger.info(
                    f"Auto-unpublished marketplace listing for uninstalled pack {installation_id}"
                )
            conn.commit()
            conn.close()
        except Exception as mp_err:
            app_logger.warning(f"Failed to auto-unpublish marketplace listing: {mp_err}")

        # If this was a fork, clear the per-user install record (drives is_forked /
        # "Fork again") and decrement install_count — mirrors the unsubscribe cleanup.
        if source_marketplace_id:
            try:
                conn = sqlite_util.connect(str(DB_PATH))
                conn.execute(
                    "UPDATE marketplace_agent_packs SET install_count = "
                    "CASE WHEN install_count > 0 THEN install_count - 1 ELSE 0 END, "
                    "updated_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(), source_marketplace_id),
                )
                conn.execute(
                    "DELETE FROM marketplace_installs "
                    "WHERE artifact_type = 'agent_pack' AND marketplace_id = ? AND user_id = ?",
                    (source_marketplace_id, user_uuid),
                )
                conn.commit()
                conn.close()
                app_logger.info(
                    f"Cleared fork install record for marketplace pack {source_marketplace_id} (user {user_uuid})"
                )
            except Exception as _mi_err:
                app_logger.warning(f"Failed to clear fork install record: {_mi_err}")

        return jsonify({"status": "success", **result}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 404
    except RuntimeError as e:
        app_logger.error(f"Agent pack uninstall error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
    except Exception as e:
        app_logger.error(f"Agent pack uninstall failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Uninstall failed: {e}"}), 500


# ══════════════════════════════════════════════════════════════════════════════
# MARKETPLACE ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════


# ── Publish ──────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>/sync-subscribers", methods=["POST"])
@operation("agent_packs.sync_subscribers", effect="human_only", scope="own",
           summary="Push a pack's new version to everyone subscribed to it — it changes other people's agents, so the owner decides",
           path={"installation_id": "the installed pack (agent_packs.list lists them)"})
@require_auth
async def sync_agent_pack_subscribers(current_user, installation_id: int):
    """Publisher-triggered fan-out: re-sync every subscriber of this pack's listing NOW.

    The import route fans out when content changes — but a publisher signs AFTER the
    import (a signature can only cover content that exists), so the subscribers synced
    at import time could not reinstate it. This is the step that carries a fresh
    signature (or any publisher-side change that did not go through an import) to them.
    Same fan-out as the import's (fan_out_pack_sync), awaited so the caller can report.
    Owner-only; 404 when the pack is not yours, 409 when it has no marketplace listing."""
    try:
        conn = sqlite_util.connect(str(DB_PATH))
        try:
            owned = conn.execute(
                "SELECT 1 FROM agent_pack_installations WHERE id = ? AND owner_user_id = ?",
                (installation_id, current_user.id)).fetchone()
            listing = conn.execute(
                "SELECT id FROM marketplace_agent_packs WHERE source_installation_id = ? "
                "AND publisher_user_id = ?", (installation_id, current_user.id)).fetchone()
        finally:
            conn.close()
        if not owned:
            return jsonify({"status": "error", "message": "Agent pack not found or not owned by you"}), 404
        if not listing:
            return jsonify({"status": "error", "message": "This pack has no marketplace listing — nothing to sync"}), 409
        result = await fan_out_pack_sync(listing[0])
        return jsonify({"status": "success", "listing_id": listing[0], **result}), 200
    except Exception as e:
        app_logger.error(f"Pack subscriber sync failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Subscriber sync failed: {e}"}), 500


@agent_pack_bp.route("/v1/agent-packs/<int:installation_id>/publish", methods=["POST"])
@operation("agent_packs.publish", effect="human_only", scope="own",
           summary="Publish a pack to the marketplace — the owner puts their name to what others will run",
           path={"installation_id": "the installed pack (agent_packs.list lists them)"})
@require_auth
async def publish_agent_pack(current_user, installation_id: int):
    """Publish an installed agent pack to the marketplace (metadata only, no file export).

    Body (JSON): {"visibility": "public"} — optional, defaults to 'public'.
    """
    user_uuid = current_user.id

    try:
        data = (await request.get_json()) or {}
        visibility = data.get("visibility", "public")
        user_ids = data.get("user_ids", [])
        distribution_mode = data.get("distribution_mode", "both")

        if visibility not in ("public", "targeted"):
            return jsonify({"status": "error", "message": "Visibility must be 'public' or 'targeted'"}), 400
        if distribution_mode not in ("both", "subscribe_only", "fork_only"):
            return jsonify({"status": "error", "message": "distribution_mode must be 'both', 'subscribe_only', or 'fork_only'"}), 400
        if visibility == "targeted" and not user_ids:
            return jsonify({"status": "error", "message": "Targeted publish requires at least one user"}), 400

        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Verify ownership
        cursor.execute(
            "SELECT * FROM agent_pack_installations WHERE id = ? AND owner_user_id = ?",
            (installation_id, user_uuid),
        )
        pack_row = cursor.fetchone()
        if not pack_row:
            conn.close()
            return jsonify({"status": "error", "message": "Agent pack not found or not owned by you"}), 404

        # Prevent republishing subscribed packs (installed via marketplace
        # subscribe). Ownership is THE shared rule (AgentPackDB.installation_is_owned):
        # an owned resource OR the created-here origin marker — counting resources
        # alone refused packs registered over REFERENCED resources
        # (create-and-install), i.e. the operator's own pack.
        from trusted_data_agent.core.agent_pack_db import AgentPackDB
        if not AgentPackDB().installation_is_owned(installation_id):
            conn.close()
            return jsonify({"status": "error", "message": "Subscribed packs cannot be republished to the marketplace"}), 403

        # Check if already published
        cursor.execute(
            "SELECT id FROM marketplace_agent_packs WHERE source_installation_id = ? AND publisher_user_id = ?",
            (installation_id, user_uuid),
        )
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return jsonify({"status": "error", "message": "This pack is already published", "marketplace_pack_id": existing["id"]}), 409

        # Count resources
        cursor.execute(
            "SELECT resource_type, COUNT(*) as cnt FROM agent_pack_resources WHERE pack_installation_id = ? GROUP BY resource_type",
            (installation_id,),
        )
        counts = {row["resource_type"]: row["cnt"] for row in cursor.fetchall()}
        profile_count = counts.get("profile", 0)
        collection_count = counts.get("collection", 0)

        # Collect profile tags
        cursor.execute(
            "SELECT resource_tag FROM agent_pack_resources WHERE pack_installation_id = ? AND resource_type = 'profile' AND resource_tag IS NOT NULL",
            (installation_id,),
        )
        profile_tags = [row["resource_tag"] for row in cursor.fetchall()]

        # Detect tool_enabled profiles from installation manifest
        has_tool = _has_tool_profiles(cursor, installation_id)

        conn.close()

        # Build manifest summary for preview
        manifest_summary = {
            "name": pack_row["name"],
            "version": pack_row["version"],
            "author": pack_row["author"],
            "pack_type": pack_row["pack_type"] or "genie",
            "profile_count": profile_count,
            "collection_count": collection_count,
            "coordinator_tag": pack_row["coordinator_tag"],
            "profile_tags": profile_tags,
            "has_tool_profiles": has_tool,
        }

        # Insert lightweight marketplace record (no file export)
        marketplace_pack_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite_util.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO marketplace_agent_packs
               (id, name, description, version, author, pack_type, publisher_user_id,
                source_installation_id, profile_count, collection_count, coordinator_tag,
                profile_tags, manifest_summary, file_path, file_size_bytes, visibility,
                distribution_mode, download_count, install_count, published_at, updated_at,
                content_updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
            (
                marketplace_pack_id, pack_row["name"], pack_row["description"],
                pack_row["version"], pack_row["author"],
                pack_row["pack_type"] or "genie", user_uuid, installation_id,
                profile_count, collection_count, pack_row["coordinator_tag"],
                json.dumps(profile_tags), json.dumps(manifest_summary),
                "", 0, visibility, distribution_mode, now, now, now,
            ),
        )
        # Insert targeted users if targeted publish
        if visibility == "targeted" and user_ids:
            for uid in user_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO marketplace_targeted_users (id, resource_type, resource_id, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), "agent_pack", marketplace_pack_id, uid, now),
                )

        conn.commit()
        conn.close()

        app_logger.info(f"Agent pack '{pack_row['name']}' published to marketplace (id={marketplace_pack_id}, visibility={visibility})")
        return jsonify({
            "status": "success",
            "marketplace_pack_id": marketplace_pack_id,
            "message": "Pack published to marketplace",
        }), 200

    except Exception as e:
        app_logger.error(f"Agent pack publish failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Publish failed: {e}"}), 500


# ── Unpublish ────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>", methods=["DELETE"])
@operation("marketplace.agent_pack_unpublish", effect="human_only", scope="own",
           summary="Withdraw a pack from the marketplace — its subscribers are affected, so the owner decides",
           path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def unpublish_agent_pack(current_user, pack_id: str):
    """Unpublish an agent pack from the marketplace (owner only)."""
    user_uuid = current_user.id

    try:
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, publisher_user_id, source_installation_id, name FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Marketplace pack not found"}), 404

        if row["publisher_user_id"] != user_uuid:
            conn.close()
            return jsonify({"status": "error", "message": "Only the publisher can unpublish"}), 403

        source_inst_id = str(row["source_installation_id"])
        source_installation_id = row["source_installation_id"]

        # Fetch subscriber user_ids before deleting grants
        cursor.execute(
            "SELECT grantee_user_id FROM marketplace_sharing_grants WHERE resource_type = 'agent_pack' AND resource_id = ?",
            (source_inst_id,),
        )
        subscriber_user_ids = [r["grantee_user_id"] for r in cursor.fetchall()]

        # Fetch pack resources so cleanup helper can identify subscribed profiles
        cursor.execute(
            "SELECT resource_type, resource_id, resource_tag FROM agent_pack_resources WHERE pack_installation_id = ?",
            (source_installation_id,),
        )
        pack_resources = [dict(r) for r in cursor.fetchall()]

        conn.close()

        # Collect collection IDs from pack resources for subscription cleanup
        collection_ids_in_pack = [
            int(r["resource_id"]) for r in pack_resources if r["resource_type"] == "collection"
        ]

        # Clean up subscribed profiles + collection subscriptions for each subscriber
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import CollectionSubscription

        total_removed = 0
        total_converted = 0
        total_subs_deleted = 0
        cleanup_failures: list[str] = []
        for sub_uuid in subscriber_user_ids:
            # Profiles (remove unmodified, convert modified)
            try:
                cleanup = _cleanup_subscriber_profiles(sub_uuid, source_installation_id, pack_resources)
                total_removed += cleanup["profiles_removed"]
                total_converted += cleanup["profiles_converted"]
            except Exception as ce:
                app_logger.error(
                    f"Profile cleanup failed for subscriber {sub_uuid} during unpublish: {ce}",
                    exc_info=True,
                )
                cleanup_failures.append(sub_uuid)

            # Collection subscriptions — revoke access to publisher's collections
            if collection_ids_in_pack:
                try:
                    with get_db_session() as session:
                        for coll_id in collection_ids_in_pack:
                            sub = session.query(CollectionSubscription).filter_by(
                                user_id=sub_uuid,
                                source_collection_id=coll_id,
                            ).first()
                            if sub:
                                session.delete(sub)
                                total_subs_deleted += 1
                except Exception as ce:
                    app_logger.warning(
                        f"CollectionSubscription cleanup failed for subscriber {sub_uuid} during unpublish: {ce}"
                    )

        # Now delete the marketplace record and associated rows
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Delete subscriber installation records (the is_owned=0 rows created during subscribe).
        # Without this the listing query (owner_user_id match) still returns the pack for each
        # subscriber even after the grant is revoked.
        for sub_uuid in subscriber_user_ids:
            cursor.execute(
                "SELECT id FROM agent_pack_installations "
                "WHERE owner_user_id = ? AND json_extract(manifest_json, '$._source_installation_id') = ?",
                (sub_uuid, source_installation_id),
            )
            local_row = cursor.fetchone()
            if local_row:
                cursor.execute(
                    "DELETE FROM agent_pack_resources WHERE pack_installation_id = ?",
                    (local_row["id"],),
                )
                cursor.execute(
                    "DELETE FROM agent_pack_installations WHERE id = ?",
                    (local_row["id"],),
                )

        # Delete sharing grants (grants use installation_id as resource_id)
        cursor.execute(
            "DELETE FROM marketplace_sharing_grants WHERE resource_type = 'agent_pack' AND resource_id = ?",
            (source_inst_id,),
        )
        # Delete targeted user entries
        cursor.execute(
            "DELETE FROM marketplace_targeted_users WHERE resource_type = 'agent_pack' AND resource_id = ?",
            (pack_id,),
        )
        # Delete ratings
        cursor.execute("DELETE FROM agent_pack_ratings WHERE pack_id = ?", (pack_id,))
        # Delete marketplace_installs records for all subscribers (data hygiene)
        cursor.execute(
            "DELETE FROM marketplace_installs WHERE artifact_type = 'agent_pack' AND marketplace_id = ?",
            (pack_id,),
        )
        # Delete marketplace record
        cursor.execute("DELETE FROM marketplace_agent_packs WHERE id = ?", (pack_id,))
        conn.commit()
        conn.close()

        app_logger.info(
            f"Agent pack '{row['name']}' unpublished from marketplace (id={pack_id}); "
            f"cleaned up for {len(subscriber_user_ids)} subscribers: "
            f"profiles_removed={total_removed}, profiles_converted={total_converted}, "
            f"collection_subscriptions_deleted={total_subs_deleted}"
        )
        response = {
            "status": "success",
            "message": "Pack unpublished from marketplace",
            "subscribers_affected": len(subscriber_user_ids),
            "profiles_removed": total_removed,
            "profiles_converted": total_converted,
            "collection_subscriptions_deleted": total_subs_deleted,
        }
        if cleanup_failures:
            response["cleanup_warnings"] = (
                f"Profile cleanup failed for {len(cleanup_failures)} subscriber(s). "
                "Their subscribed profiles may need manual removal. "
                f"Affected user IDs: {cleanup_failures}"
            )
        return jsonify(response), 200

    except Exception as e:
        app_logger.error(f"Agent pack unpublish failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Unpublish failed: {e}"}), 500


# ── Targeted Users ──────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/targeted-users", methods=["GET"])
@operation("marketplace.agent_pack_audience", effect="read", scope="own",
           summary="Who a pack the caller published is offered to (its publisher only)",
           path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def get_agent_pack_targeted_users(current_user, pack_id: str):
    """Get the list of targeted users for a published agent pack."""
    user_uuid = current_user.id

    try:
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Verify publisher ownership
        cursor.execute(
            "SELECT publisher_user_id FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Marketplace pack not found"}), 404
        if row["publisher_user_id"] != user_uuid:
            conn.close()
            return jsonify({"status": "error", "message": "Only the publisher can view targeted users"}), 403

        # Get targeted users with user info
        cursor.execute(
            """SELECT t.user_id, u.username, u.display_name
               FROM marketplace_targeted_users t
               LEFT JOIN users u ON t.user_id = u.id
               WHERE t.resource_type = 'agent_pack' AND t.resource_id = ?""",
            (pack_id,),
        )
        users = [
            {"user_id": r["user_id"], "username": r["username"], "display_name": r["display_name"] or r["username"]}
            for r in cursor.fetchall()
        ]
        conn.close()

        return jsonify({"status": "success", "users": users}), 200

    except Exception as e:
        app_logger.error(f"Failed to get targeted users: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed: {e}"}), 500


@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/targeted-users", methods=["PUT"])
@operation("marketplace.agent_pack_audience_set", effect="human_only", scope="own",
           summary="Choose who a published pack is offered to — whom you share with is yours to decide",
           path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def update_agent_pack_targeted_users(current_user, pack_id: str):
    """Update the targeted users list for a published agent pack."""
    user_uuid = current_user.id

    try:
        data = (await request.get_json()) or {}
        new_user_ids = data.get("user_ids", [])

        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Verify publisher ownership
        cursor.execute(
            "SELECT publisher_user_id FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Marketplace pack not found"}), 404
        if row["publisher_user_id"] != user_uuid:
            conn.close()
            return jsonify({"status": "error", "message": "Only the publisher can update targeted users"}), 403

        # Get current targeted user IDs
        cursor.execute(
            "SELECT user_id FROM marketplace_targeted_users WHERE resource_type = 'agent_pack' AND resource_id = ?",
            (pack_id,),
        )
        existing_ids = {r["user_id"] for r in cursor.fetchall()}
        new_ids = set(new_user_ids)

        # Delete removed users
        to_remove = existing_ids - new_ids
        if to_remove:
            placeholders = ",".join("?" * len(to_remove))
            cursor.execute(
                f"DELETE FROM marketplace_targeted_users WHERE resource_type = 'agent_pack' AND resource_id = ? AND user_id IN ({placeholders})",
                [pack_id] + list(to_remove),
            )

        # Insert new users
        now = datetime.now(timezone.utc).isoformat()
        to_add = new_ids - existing_ids
        for uid in to_add:
            cursor.execute(
                "INSERT OR IGNORE INTO marketplace_targeted_users (id, resource_type, resource_id, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "agent_pack", pack_id, uid, now),
            )

        conn.commit()

        # Return updated list
        cursor.execute(
            """SELECT t.user_id, u.username, u.display_name
               FROM marketplace_targeted_users t
               LEFT JOIN users u ON t.user_id = u.id
               WHERE t.resource_type = 'agent_pack' AND t.resource_id = ?""",
            (pack_id,),
        )
        users = [
            {"user_id": r["user_id"], "username": r["username"], "display_name": r["display_name"] or r["username"]}
            for r in cursor.fetchall()
        ]
        conn.close()

        app_logger.info(f"Updated targeted users for agent pack {pack_id}: {len(users)} users")
        return jsonify({"status": "success", "users": users}), 200

    except Exception as e:
        app_logger.error(f"Failed to update targeted users: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed: {e}"}), 500


# ── Browse ───────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs", methods=["GET"])
@operation("marketplace.agent_packs", effect="read", scope="shared",
           summary="The agent packs published in the marketplace that the caller may see — what each offers, by whom, how rated",
           query={"search": "words in a pack's name or description",
                  "sort_by": Arg("string", "order", enum=("recent", "rating", "installs", "downloads")),
                  "pack_type": "only one kind of pack (default all)",
                  "visibility": Arg("string", "public listings, those targeted at the caller, or both", enum=("public", "targeted", "all")),
                  "page": Arg("integer", "page number (default 1)"), "per_page": Arg("integer", "per page (default 12, max 50)")},
           summary_fields=("id", "name", "description", "publisher_username", "version"))
@require_auth
async def browse_marketplace_agent_packs(current_user):
    """Browse published agent packs with pagination, search, and sorting.

    Query params: page, per_page, search, sort_by, pack_type
    """
    user_uuid = current_user.id

    try:
        page = int(request.args.get("page", 1))
        per_page = min(int(request.args.get("per_page", 12)), 50)
        search = request.args.get("search", "").strip()
        sort_by = request.args.get("sort_by", "recent")
        pack_type = request.args.get("pack_type", "all")
        # 'public' (default) → only public listings; 'targeted' → only listings
        # the user has been granted access to; 'all' → both (legacy union).
        visibility_filter = request.args.get("visibility", "public").strip().lower()
        if visibility_filter not in ("public", "targeted", "all"):
            visibility_filter = "public"

        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        if visibility_filter == "public":
            where_clauses = ["p.visibility = 'public'"]
            params = []
        elif visibility_filter == "targeted":
            # Show targeted listings the user has a grant for OR is the publisher of
            where_clauses = [
                "p.visibility = 'targeted' AND (p.id IN "
                "(SELECT resource_id FROM marketplace_targeted_users WHERE resource_type = 'agent_pack' AND user_id = ?) "
                "OR p.publisher_user_id = ?)"
            ]
            params = [user_uuid, user_uuid]
        else:  # 'all'
            # 'all' is the union of what 'public' and 'targeted' return — including
            # the caller's OWN targeted listings, or the name would not be true.
            where_clauses = [
                "(p.visibility = 'public' OR (p.visibility = 'targeted' AND (p.id IN "
                "(SELECT resource_id FROM marketplace_targeted_users WHERE resource_type = 'agent_pack' AND user_id = ?) "
                "OR p.publisher_user_id = ?)))"
            ]
            params = [user_uuid, user_uuid]

        if pack_type != "all":
            where_clauses.append("p.pack_type = ?")
            params.append(pack_type)

        if search:
            where_clauses.append("(p.name LIKE ? OR p.description LIKE ? OR p.coordinator_tag LIKE ? OR p.profile_tags LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like, like])

        where_sql = " AND ".join(where_clauses)

        # Sort
        sort_map = {
            "downloads": "p.download_count DESC",
            "installs": "p.install_count DESC",
            "rating": "avg_rating DESC",
            "recent": "p.published_at DESC",
        }
        order_sql = sort_map.get(sort_by, "p.published_at DESC")

        # Count total
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM marketplace_agent_packs p WHERE {where_sql}", params)
        total = cursor.fetchone()[0]
        total_pages = max(1, (total + per_page - 1) // per_page)

        # Fetch page with ratings JOIN
        offset = (page - 1) * per_page
        query = f"""
            SELECT p.*,
                   u.username AS publisher_username,
                   u.display_name AS publisher_display_name,
                   u.email AS publisher_email,
                   u.marketplace_visible,
                   COALESCE(r.avg_rating, 0) AS avg_rating,
                   COALESCE(r.rating_count, 0) AS rating_count,
                   COALESCE(g.kg_count, 0) AS kg_count
            FROM marketplace_agent_packs p
            LEFT JOIN users u ON p.publisher_user_id = u.id
            LEFT JOIN (
                SELECT pack_id, AVG(rating) AS avg_rating, COUNT(*) AS rating_count
                FROM agent_pack_ratings
                GROUP BY pack_id
            ) r ON r.pack_id = p.id
            LEFT JOIN (
                SELECT pack_installation_id, COUNT(*) AS kg_count
                FROM agent_pack_resources
                WHERE resource_type = 'knowledge_graph'
                GROUP BY pack_installation_id
            ) g ON g.pack_installation_id = p.source_installation_id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
        """
        cursor.execute(query, params + [per_page, offset])
        rows = cursor.fetchall()

        packs = []
        for row in rows:
            # Detect tool_enabled profiles from manifest or source installation
            manifest = json.loads(row["manifest_summary"]) if row["manifest_summary"] else {}
            has_tool = manifest.get("has_tool_profiles")
            if has_tool is None and row["source_installation_id"]:
                has_tool = _has_tool_profiles(cursor, row["source_installation_id"])

            pack = {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "version": row["version"],
                "author": row["author"],
                "pack_type": row["pack_type"],
                "publisher_username": row["publisher_display_name"] or row["publisher_username"],
                "publisher_email": row["publisher_email"] if row["marketplace_visible"] else None,
                "profile_count": row["profile_count"],
                "collection_count": row["collection_count"],
                # The ontologies the pack ships — read from the publisher's own
                # installation ledger, which claims them since the Sep-2026 fix.
                "kg_count": row["kg_count"],
                "coordinator_tag": row["coordinator_tag"],
                "profile_tags": json.loads(row["profile_tags"]) if row["profile_tags"] else [],
                "profile_classes": (_pack_profile_classes(cursor, row["source_installation_id"])
                                    if row["source_installation_id"] else {}),
                "download_count": row["download_count"],
                "install_count": row["install_count"],
                "average_rating": round(row["avg_rating"], 1),
                "rating_count": row["rating_count"],
                "visibility": row["visibility"],
                "distribution_mode": row["distribution_mode"] or "both",
                "published_at": row["published_at"],
                "content_updated_at": row["content_updated_at"] if "content_updated_at" in row.keys() else None,
                "is_publisher": row["publisher_user_id"] == user_uuid,
                "has_tool_profiles": bool(has_tool),
                "_source_installation_id": row["source_installation_id"],
            }
            packs.append(pack)

        # Subscription state derives strictly from marketplace_sharing_grants.
        # Fork state derives from marketplace_installs (after subtracting any
        # subscription grant). Forks must remain re-forkable, so they never
        # collapse into is_subscribed.
        for pack in packs:
            src_id = pack.pop("_source_installation_id", None)
            # The trust artefact for a pack is keyed by its source installation, so
            # the badge cannot resolve without it. Reads are not owner-scoped by
            # design: a consumer must be able to see that the publisher signed.
            pack["source_installation_id"] = src_id
            if src_id and not pack.get("is_publisher"):
                cursor.execute(
                    "SELECT COUNT(*) FROM marketplace_sharing_grants "
                    "WHERE resource_type = 'agent_pack' AND resource_id = ? AND grantee_user_id = ?",
                    (str(src_id), user_uuid),
                )
                pack["is_subscribed"] = cursor.fetchone()[0] > 0
                cursor.execute(
                    "SELECT COUNT(*) FROM marketplace_installs "
                    "WHERE artifact_type = 'agent_pack' AND marketplace_id = ? AND user_id = ?",
                    (pack["id"], user_uuid),
                )
                has_install = cursor.fetchone()[0] > 0
                pack["is_forked"] = has_install and not pack["is_subscribed"]
                if pack["is_subscribed"]:
                    # the subscriber's copy vs the listing's CONTENT stamp (never updated_at)
                    cursor.execute(
                        "SELECT synced_at, installed_at, sync_error FROM agent_pack_installations "
                        "WHERE owner_user_id = ? AND json_extract(manifest_json, '$._source_installation_id') = ? "
                        "ORDER BY id DESC LIMIT 1",
                        (user_uuid, src_id),
                    )
                    _sub = cursor.fetchone()
                    _synced = (_sub["synced_at"] or _sub["installed_at"]) if _sub else None
                    pack["synced_at"] = _synced
                    pack["sync_error"] = _sub["sync_error"] if _sub else None
                    pack["update_available"] = _update_available(pack.get("content_updated_at"), _synced)
            else:
                pack["is_subscribed"] = False
                pack["is_forked"] = False

        conn.close()

        return jsonify({
            "status": "success",
            "packs": packs,
            "total": total,
            "total_pages": total_pages,
            "page": page,
        }), 200

    except Exception as e:
        app_logger.error(f"Failed to browse marketplace agent packs: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Browse failed: {e}"}), 500


# ── Details ──────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>", methods=["GET"])
@operation("marketplace.agent_pack", effect="read", scope="shared",
           summary="One marketplace pack in full: its agents, what they need, its ratings and versions", path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def get_marketplace_agent_pack_details(current_user, pack_id: str):
    """Get full details of a marketplace agent pack including manifest summary."""
    user_uuid = current_user.id

    try:
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """SELECT p.*,
                      u.username AS publisher_username,
                      u.display_name AS publisher_display_name,
                      u.email AS publisher_email,
                      u.marketplace_visible
               FROM marketplace_agent_packs p
               LEFT JOIN users u ON p.publisher_user_id = u.id
               WHERE p.id = ?""",
            (pack_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Pack not found"}), 404

        # Enforce targeted visibility — non-publishers must have an explicit grant
        if row["visibility"] == "targeted" and row["publisher_user_id"] != user_uuid:
            cursor.execute(
                "SELECT 1 FROM marketplace_targeted_users "
                "WHERE resource_type = 'agent_pack' AND resource_id = ? AND user_id = ?",
                (pack_id, user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({"status": "error", "message": "Pack not found"}), 404

        # Get ratings
        cursor.execute(
            "SELECT AVG(rating) AS avg_rating, COUNT(*) AS rating_count FROM agent_pack_ratings WHERE pack_id = ?",
            (pack_id,),
        )
        rating_row = cursor.fetchone()

        # Detect tool_enabled profiles
        manifest = json.loads(row["manifest_summary"]) if row["manifest_summary"] else {}
        has_tool = manifest.get("has_tool_profiles")
        if has_tool is None and row["source_installation_id"]:
            has_tool = _has_tool_profiles(cursor, row["source_installation_id"])

        # Compute is_subscribed / is_forked for the current user, mirroring the
        # browse endpoint. Subscription = sharing grant on the source install.
        # Fork = install record without a subscription grant.
        is_publisher = row["publisher_user_id"] == user_uuid
        is_subscribed = False
        is_forked = False
        if not is_publisher:
            src_id = row["source_installation_id"]
            if src_id:
                cursor.execute(
                    "SELECT COUNT(*) FROM marketplace_sharing_grants "
                    "WHERE resource_type = 'agent_pack' AND resource_id = ? AND grantee_user_id = ?",
                    (str(src_id), user_uuid),
                )
                is_subscribed = cursor.fetchone()[0] > 0
            cursor.execute(
                "SELECT COUNT(*) FROM marketplace_installs "
                "WHERE artifact_type = 'agent_pack' AND marketplace_id = ? AND user_id = ?",
                (pack_id, user_uuid),
            )
            has_install = cursor.fetchone()[0] > 0
            is_forked = has_install and not is_subscribed

        conn.close()

        result = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "version": row["version"],
            "author": row["author"],
            "pack_type": row["pack_type"],
            "publisher_username": row["publisher_display_name"] or row["publisher_username"],
            "publisher_email": row["publisher_email"] if row["marketplace_visible"] else None,
            "profile_count": row["profile_count"],
            "collection_count": row["collection_count"],
            "coordinator_tag": row["coordinator_tag"],
            "profile_tags": json.loads(row["profile_tags"]) if row["profile_tags"] else [],
            "manifest_summary": json.loads(row["manifest_summary"]) if row["manifest_summary"] else None,
            "download_count": row["download_count"],
            "install_count": row["install_count"],
            "average_rating": round(rating_row["avg_rating"], 1) if rating_row["avg_rating"] else 0,
            "rating_count": rating_row["rating_count"] or 0,
            "visibility": row["visibility"],
            "distribution_mode": row["distribution_mode"] or "both",
            "published_at": row["published_at"],
            "is_publisher": is_publisher,
            "is_subscribed": is_subscribed,
            "is_forked": is_forked,
            "has_tool_profiles": bool(has_tool),
        }

        return jsonify({"status": "success", **result}), 200

    except Exception as e:
        app_logger.error(f"Failed to get marketplace pack details: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Failed to get details: {e}"}), 500


# ── Install from Marketplace ─────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/mcp-requirements", methods=["GET"])
@operation("marketplace.agent_pack_requirements", effect="read", scope="shared",
           summary="What each agent in a marketplace pack needs from the caller's data sources to run here", path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def marketplace_agent_pack_mcp_requirements(current_user, pack_id: str):
    """Per-agent MCP requirements of a marketplace listing, resolved for the caller.

    Serves both Subscribe and Fork: one row per publisher agent that needs an MCP
    server, with the server NAME the materialisation will try to match and the
    caller's server id it currently resolves to (or null), plus the caller's servers.
    """
    try:
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, source_installation_id, publisher_user_id "
            "FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Marketplace pack not found"}), 404
        cursor.execute(
            "SELECT resource_type, resource_id, resource_tag, resource_role "
            "FROM agent_pack_resources WHERE pack_installation_id = ?",
            (row["source_installation_id"],),
        )
        pack_resources = [dict(r) for r in cursor.fetchall()]
        conn.close()

        manager = _manager()
        entries = _publisher_mcp_requirement_entries(pack_resources, row["publisher_user_id"])
        rows = manager.pack_mcp_requirements(entries, manager._mcp_name_map(current_user.id))
        return jsonify({
            "status": "success",
            "pack": {"id": row["id"], "name": row["name"]},
            "mcp_requirements": rows,
            "mcp_servers": _mcp_servers_for(current_user.id),
        }), 200
    except Exception as e:
        app_logger.error(f"mcp-requirements {pack_id} failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/install", methods=["POST"])
@operation("marketplace.agent_pack_install", effect="human_only", scope="shared",
           summary="Install or subscribe to someone else's pack — bringing another publisher's agents into your estate is your trust decision",
           path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def subscribe_marketplace_agent_pack(current_user, pack_id: str):
    """Subscribe to a marketplace agent pack.

    Creates a sharing grant AND materialises the pack's resources for the
    subscriber: profiles are built with the subscriber's own LLM/MCP IDs
    (Fix 1B — no publisher IDs leaked), collection subscriptions are created,
    and the grant + collections commit atomically (Fix 1C).

    Body (JSON, optional):
        llm_configuration_id  — subscriber's LLM config to use for all profiles;
                                 falls back to their first configured LLM.
        mcp_server_id         — subscriber's MCP server for tool_enabled profiles.
        mcp_bindings          — optional explicit per-agent map {profile_tag: mcp_server_id}
                                 (the subscriber's own servers); wins over name auto-match.
    """
    from sqlalchemy.exc import IntegrityError
    from trusted_data_agent.auth.database import get_db_session
    from trusted_data_agent.auth.models import MarketplaceSharingGrant, CollectionSubscription
    from trusted_data_agent.core.config_manager import get_config_manager

    user_uuid = current_user.id

    try:
        data = (await request.get_json(silent=True)) or {}
        llm_configuration_id = data.get("llm_configuration_id")
        mcp_server_id = data.get("mcp_server_id") or None
        try:
            mcp_bindings = _parse_mcp_bindings(data.get("mcp_bindings"))
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400

        # ── Fetch marketplace pack ─────────────────────────────────────────────
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, name, source_installation_id, publisher_user_id, updated_at, distribution_mode "
            "FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Marketplace pack not found"}), 404

        publisher_user_id = row["publisher_user_id"]
        source_installation_id = row["source_installation_id"]
        pack_name = row["name"]
        pack_published_at = row["updated_at"]
        distribution_mode = row["distribution_mode"] or "both"

        if distribution_mode == "fork_only":
            conn.close()
            return jsonify({
                "status": "error",
                "message": "This pack is configured for fork only. Use the Fork option to get your own copy.",
            }), 403

        # ── Fetch pack resources ───────────────────────────────────────────────
        cursor.execute(
            "SELECT resource_type, resource_id, resource_tag, resource_role "
            "FROM agent_pack_resources WHERE pack_installation_id = ?",
            (source_installation_id,),
        )
        pack_resources = [dict(r) for r in cursor.fetchall()]

        # Fetch publisher's installation metadata for subscriber-side recording
        cursor.execute(
            "SELECT description, version, author, coordinator_tag, pack_type "
            "FROM agent_pack_installations WHERE id = ?",
            (source_installation_id,),
        )
        _install_meta = cursor.fetchone()
        conn.close()

        # ── Guard: cannot subscribe to own pack ────────────────────────────────
        if publisher_user_id == user_uuid:
            return jsonify({"status": "error", "message": "Cannot subscribe to your own pack"}), 400

        # ── Guard: pack must have at least one profile (Fix 1E) ────────────────
        profile_count = sum(1 for r in pack_resources if r["resource_type"] == "profile")
        if profile_count == 0:
            return jsonify({"status": "error", "message": "This pack has no profiles to subscribe to"}), 409

        # ── Fix 3D: Verify publisher's profiles still exist ───────────────────
        from trusted_data_agent.core.config_manager import get_config_manager as _gcm
        _pub_cfg = _gcm()
        missing_profiles = []
        for res in pack_resources:
            if res["resource_type"] == "profile":
                if not _pub_cfg.get_profile(res["resource_id"], publisher_user_id):
                    missing_profiles.append(res.get("resource_tag") or res["resource_id"])
        if missing_profiles:
            app_logger.warning(
                f"subscribe {pack_id}: publisher {publisher_user_id} is missing profiles "
                f"{missing_profiles} — aborting subscribe"
            )
            return jsonify({
                "status": "error",
                "message": (
                    "This pack can no longer be subscribed to: the publisher has removed "
                    f"the following profile(s): {', '.join(f'@{t}' for t in missing_profiles)}."
                ),
                "missing_profiles": missing_profiles,
            }), 409

        # ── Resolve subscriber LLM config (Fix 1B) ────────────────────────────
        config_manager = get_config_manager()
        if not llm_configuration_id:
            subscriber_llms = config_manager.get_llm_configurations(user_uuid)
            if not subscriber_llms:
                return jsonify({
                    "status": "error",
                    "message": "No LLM configuration found in your account. "
                               "Add one before subscribing to an agent pack.",
                }), 400
            llm_configuration_id = subscriber_llms[0]["id"]

        # ── Build subscriber-scoped profiles (Fix 1B — ID remapping) ──────────
        manager = _manager()
        try:
            built_profiles = manager.build_subscribed_profiles(
                pack_resources=pack_resources,
                publisher_user_id=publisher_user_id,
                subscriber_user_id=user_uuid,
                llm_config_id=llm_configuration_id,
                mcp_server_id=mcp_server_id,
                source_installation_id=source_installation_id,
                pack_published_at=pack_published_at,
                mcp_bindings=mcp_bindings,
            )
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400

        # ── Atomically create grant + collection subscriptions (Fix 1C) ────────
        with get_db_session() as session:
            grant = MarketplaceSharingGrant(
                resource_type="agent_pack",
                resource_id=str(source_installation_id),
                grantor_user_id=publisher_user_id,
                grantee_user_id=user_uuid,
            )
            session.add(grant)
            try:
                session.flush()  # triggers unique constraint before collections
            except IntegrityError:
                session.rollback()
                return jsonify({"status": "error", "message": "Already subscribed to this pack"}), 409

            # Collection subscriptions within the same transaction.
            # Track only newly-created IDs so rollback doesn't delete pre-existing subs.
            newly_subscribed_collection_ids: list[int] = []
            for res in pack_resources:
                if res["resource_type"] == "collection":
                    collection_id = int(res["resource_id"])
                    existing_sub = session.query(CollectionSubscription).filter_by(
                        user_id=user_uuid,
                        source_collection_id=collection_id,
                    ).first()
                    if not existing_sub:
                        session.add(CollectionSubscription(
                            user_id=user_uuid,
                            source_collection_id=collection_id,
                            enabled=True,
                        ))
                        newly_subscribed_collection_ids.append(collection_id)
            # session commits on exit from context manager

        # ── Pre-subscribe: purge legacy same-tag orphans before adding new profiles ──
        # Profiles created by the old subscribe code (before marker system) have no
        # is_subscribed flag.  Purge any that share a tag with this pack so the new
        # profiles don't create duplicates.
        pack_tags_to_subscribe = {
            r.get("resource_tag") for r in pack_resources
            if r.get("resource_type") == "profile" and r.get("resource_tag")
        }
        if pack_tags_to_subscribe:
            existing_profiles = config_manager.get_profiles(user_uuid)
            legacy_ids = [
                p["id"] for p in existing_profiles
                if not p.get("is_subscribed") and p.get("tag") in pack_tags_to_subscribe
            ]
            for lid in legacy_ids:
                config_manager.remove_profile(lid, user_uuid)
                app_logger.info(
                    f"subscribe pre-clean: removed legacy orphan profile {lid} "
                    f"(tag={next((p.get('tag') for p in existing_profiles if p['id'] == lid), '?')}, "
                    f"user={user_uuid})"
                )

        # ── Add profiles with compensating rollback on partial failure (Fix 2A) ──
        profiles_added = 0
        created_profile_ids: list[str] = []
        try:
            for profile in built_profiles:
                added = await config_manager.add_profile_locked(profile, user_uuid)
                if added:
                    profiles_added += 1
                    created_profile_ids.append(profile["id"])
        except Exception as profile_err:
            # Rollback: remove any profiles that were added in this subscribe
            app_logger.error(
                f"subscribe_marketplace_agent_pack: profile add failed, rolling back "
                f"{len(created_profile_ids)} profile(s) and grant. Error: {profile_err}",
                exc_info=True,
            )
            for pid in created_profile_ids:
                config_manager.remove_profile(pid, user_uuid)
            with get_db_session() as rb_session:
                rb_grant = rb_session.query(MarketplaceSharingGrant).filter_by(
                    resource_type="agent_pack",
                    resource_id=str(source_installation_id),
                    grantee_user_id=user_uuid,
                ).first()
                if rb_grant:
                    rb_session.delete(rb_grant)
                # Only remove collection subscriptions that were newly created by this
                # subscribe attempt — do not touch pre-existing subscriptions.
                if newly_subscribed_collection_ids:
                    rb_session.query(CollectionSubscription).filter_by(
                        user_id=user_uuid,
                    ).filter(
                        CollectionSubscription.source_collection_id.in_(
                            newly_subscribed_collection_ids
                        )
                    ).delete(synchronize_session=False)
            return jsonify({
                "status": "error",
                "message": f"Subscribe failed during profile creation and was rolled back: {profile_err}",
            }), 500

        # ── Record subscriber-side installation (enables pack grouping in IFOC panel) ──
        _members = {"manifest": {}, "kg_ids": [], "kg_names": [], "skill_ids": [],
                    "kgs_created": 0, "warnings": [], "zip_path": None}
        _installation_id = None
        _trust_reinstated = 0
        try:
            _tag_to_role = {
                r.get("resource_tag"): r.get("resource_role", "profile")
                for r in pack_resources
                if r["resource_type"] == "profile" and r.get("resource_tag")
            }
            # ── Key each subscribed PLANNER reference to the server its consuming agent
            # was bound to. The reference points at the PUBLISHER's collection, which is
            # keyed to the publisher's server and therefore never loaded for a session on
            # the subscriber's; the retriever reads this key beside the collection's own.
            try:
                _keyed = _key_planner_subscriptions(user_uuid, built_profiles)
                if _keyed:
                    app_logger.info(f"subscribe: keyed {len(_keyed)} planner subscription(s) "
                                    f"to the subscriber's servers: {_keyed}")
                    try:
                        from trusted_data_agent.agent.rag_retriever import get_rag_retriever
                        _r = get_rag_retriever()
                        for _cid in _keyed:
                            _r and _r.ensure_loaded(_cid)
                    except Exception:
                        pass
            except Exception as _key_err:
                app_logger.warning(f"subscribe: planner keying failed (references stay "
                                   f"unkeyed, the repositories will not load): {_key_err}")
            _created_id_set = set(created_profile_ids)
            # ── The members a fork gets and subscribe never did: knowledge-graph COPIES
            # (snapshot-with-sync) and the skills recorded — from the publisher's LIVE
            # installation, exported as a fork exports it (P1 of PACK_SUBSCRIBE_COMPLETION.md).
            _members = {"manifest": {}, "kg_ids": [], "kg_names": [], "skill_ids": [],
                        "kgs_created": 0, "warnings": [], "zip_path": None}
            try:
                _members = await manager.materialize_subscription_members(
                    subscriber_uuid=user_uuid, publisher_uuid=publisher_user_id,
                    publisher_profile_ids=[r["resource_id"] for r in pack_resources if r["resource_type"] == "profile"],
                    pack_name=pack_name, pack_listing_id=pack_id,
                    tag_to_profile_id={p.get("tag"): p["id"] for p in built_profiles if p["id"] in _created_id_set})
            except Exception as _mem_err:
                _members["warnings"].append(f"pack members not materialized: {_mem_err}")
                app_logger.warning(f"subscribe: member materialization failed: {_mem_err}", exc_info=True)
            _sub_profile_ids = [p["id"] for p in built_profiles if p["id"] in _created_id_set]
            _sub_profile_tags = [p.get("tag", "") for p in built_profiles if p["id"] in _created_id_set]
            _sub_profile_roles = [
                _tag_to_role.get(p.get("tag"), "profile")
                for p in built_profiles if p["id"] in _created_id_set
            ]
            _coll_ids = [int(r["resource_id"]) for r in pack_resources if r["resource_type"] == "collection"]
            _coll_refs = [r.get("resource_tag") or r["resource_id"] for r in pack_resources if r["resource_type"] == "collection"]
            _sub_manifest = {
                "name": pack_name,
                "description": _install_meta["description"] if _install_meta else "",
                "version": _install_meta["version"] if _install_meta else "1.0.0",
                "author": _install_meta["author"] if _install_meta else "",
                "_source_installation_id": source_installation_id,
            }
            _coord_tag = _install_meta["coordinator_tag"] if _install_meta else None
            _pack_type = _install_meta["pack_type"] if _install_meta else "bundle"
            _coord_pid = next(
                (p["id"] for p in built_profiles if p["id"] in _created_id_set and p.get("tag") == _coord_tag),
                None,
            )
            # the exported manifest (member_trust, skills, knowledge_graphs) is what the
            # ledger stores — the fork stores the same — with the subscription's own facts
            _sub_manifest = {**(_members.get("manifest") or {}), **_sub_manifest}
            _installation_id = manager._record_installation(
                manifest=_sub_manifest,
                coordinator_tag=_coord_tag,
                coordinator_profile_id=_coord_pid,
                pack_type=_pack_type,
                profile_ids=_sub_profile_ids,
                profile_tags=_sub_profile_tags,
                profile_roles=_sub_profile_roles,
                collection_ids=_coll_ids,
                collection_refs=_coll_refs,
                user_uuid=user_uuid,
                is_owned=False,
                kg_ids=_members.get("kg_ids") or None,
                kg_names=_members.get("kg_names") or None,
                skill_ids=_members.get("skill_ids") or None,
            )
            # Trust: every member's captured attestation re-applied where the copy hashes
            # identically — ONE step, the same the fork and the in-place update run.
            _trust_reinstated = manager._reinstate_member_trust(_sub_manifest, _installation_id)
            if _reinstate_pack_seal(source_installation_id, _installation_id):
                _trust_reinstated += 1
        except Exception as _rec_err:
            app_logger.warning(
                f"subscribe_marketplace_agent_pack: could not record local installation for "
                f"pack '{pack_name}' (user={user_uuid}): {_rec_err}"
            )

        # ── Increment install_count + record install for rating gate (Fix 4A) ───
        now_ts = datetime.now(timezone.utc).isoformat()
        conn = sqlite_util.connect(str(DB_PATH))
        conn.execute(
            "UPDATE marketplace_agent_packs SET install_count = install_count + 1, updated_at = ? WHERE id = ?",
            (now_ts, pack_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO marketplace_installs (id, artifact_type, marketplace_id, user_id, installed_at) "
            "VALUES (?, 'agent_pack', ?, ?, ?)",
            (str(uuid.uuid4()), pack_id, user_uuid, now_ts),
        )
        conn.commit()
        conn.close()

        collections_added = sum(1 for r in pack_resources if r["resource_type"] == "collection")
        app_logger.info(
            f"User {user_uuid} subscribed to marketplace agent pack '{pack_name}' "
            f"(pack_id={pack_id}, profiles={profiles_added}, collections={collections_added})"
        )
        try:
            if _members.get("zip_path"):
                Path(_members["zip_path"]).unlink(missing_ok=True)
        except Exception:
            pass
        return jsonify({
            "status": "success",
            "message": f"Subscribed to {pack_name}",
            "name": pack_name,
            "installation_id": locals().get("_installation_id"),
            "kgs_created": _members.get("kgs_created", 0),
            "skills_recorded": len(_members.get("skill_ids") or []),
            "trust_reinstated": locals().get("_trust_reinstated", 0),
            "warnings": (_members.get("warnings") or None),
            "profiles_added": profiles_added,
            "collections_added": collections_added,
        }), 200

    except Exception as e:
        app_logger.error(f"Marketplace agent pack subscribe failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Subscribe failed: {e}"}), 500


# ── Sync a subscription ──────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/sync", methods=["POST"])
@operation("marketplace.agent_pack_sync", effect="human_only", scope="shared",
           summary="Take a subscribed pack's new version — accepting changed agents into your estate is yours",
           path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def sync_marketplace_agent_pack(current_user, pack_id: str):
    """Re-materialize the caller's subscription from the publisher's live pack, in place.

    Body (JSON, optional): ``llm_configuration_id``, ``mcp_server_id``, ``mcp_bindings`` —
    each overrides the value derived from the subscriber's existing copies.
    """
    try:
        data = (await request.get_json(silent=True)) or {}
        try:
            mcp_bindings = _parse_mcp_bindings(data.get("mcp_bindings"))
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400
        result = await sync_pack_subscription(
            current_user.id, pack_id,
            llm_configuration_id=data.get("llm_configuration_id"),
            mcp_server_id=data.get("mcp_server_id") or None,
            mcp_bindings=mcp_bindings)
        return jsonify(result), 200
    except PackSyncError as pe:
        return jsonify({"status": "error", "message": str(pe), **pe.extra}), pe.status
    except Exception as e:
        app_logger.error(f"Marketplace agent pack sync failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Sync failed: {e}"}), 500


# ── Unsubscribe from Marketplace ────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/subscribe", methods=["DELETE"])
@operation("marketplace.agent_pack_unsubscribe", effect="human_only", scope="shared",
           summary="End a subscription to someone else's pack — its agents leave your estate", path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def unsubscribe_marketplace_agent_pack(current_user, pack_id: str):
    """Unsubscribe from a marketplace agent pack.

    Removes the sharing grant AND cleans up all resources that were
    materialised during subscribe: subscribed profiles are removed from the
    subscriber's config, and ``CollectionSubscription`` rows created by the
    pack subscription are deleted.
    """
    user_uuid = current_user.id

    try:
        # Find the marketplace pack and its resources
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT source_installation_id, name FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"status": "error", "message": "Marketplace pack not found"}), 404

        source_installation_id = row["source_installation_id"]
        pack_name = row["name"]

        # Fetch pack resources for cleanup (resource_tag needed for legacy orphan detection)
        cursor.execute(
            "SELECT resource_type, resource_id, resource_tag "
            "FROM agent_pack_resources WHERE pack_installation_id = ?",
            (source_installation_id,),
        )
        pack_resources = [dict(r) for r in cursor.fetchall()]
        conn.close()

        # Block unsubscribe when a pack profile is the subscriber's default profile.
        pack_profile_tags = {
            r.get("resource_tag") for r in pack_resources
            if r.get("resource_type") == "profile" and r.get("resource_tag")
        }
        if pack_profile_tags:
            from trusted_data_agent.core.config_manager import get_config_manager
            _cm = get_config_manager()
            default_profile_id = _cm.get_default_profile_id(user_uuid)
            if default_profile_id:
                default_profile = _cm.get_profile(default_profile_id, user_uuid)
                if default_profile and default_profile.get("tag") in pack_profile_tags:
                    default_tag = default_profile.get("tag", "unknown")
                    return jsonify({
                        "status": "error",
                        "message": (
                            f"Cannot unsubscribe: '@{default_tag}' is your default profile. "
                            "Please change the default profile first, then try again."
                        ),
                        "default_profile_blocked": True,
                        "default_profile_tag": default_tag,
                    }), 409

        # Delete the sharing grant and collection subscriptions
        from trusted_data_agent.auth.database import get_db_session
        from trusted_data_agent.auth.models import MarketplaceSharingGrant, CollectionSubscription

        with get_db_session() as session:
            grant = session.query(MarketplaceSharingGrant).filter_by(
                resource_type="agent_pack",
                resource_id=str(source_installation_id),
                grantee_user_id=user_uuid,
            ).first()
            if not grant:
                return jsonify({"status": "error", "message": "Not subscribed to this pack"}), 404
            session.delete(grant)

            # Delete collection subscriptions created by this pack
            for res in pack_resources:
                if res["resource_type"] == "collection":
                    collection_id = int(res["resource_id"])
                    sub = session.query(CollectionSubscription).filter_by(
                        user_id=user_uuid,
                        source_collection_id=collection_id,
                    ).first()
                    if sub:
                        session.delete(sub)

        # Compute which profiles will be removed WITHOUT deleting them yet.
        # Archive must run first so ProfileDetector can still look up profile tags via DB.
        # If we delete profiles before archiving, tag-based session matching is skipped and
        # sessions created under a previous subscription cycle (different profile ID, same tag)
        # are missed — they become orphaned in the session history panel.
        cleanup = _cleanup_subscriber_profiles(user_uuid, source_installation_id, pack_resources, save=False)
        profiles_removed = cleanup["profiles_removed"]
        profiles_converted = cleanup["profiles_converted"]

        # Archive sessions BEFORE deleting profiles so ProfileDetector can resolve tags.
        from trusted_data_agent.core.session_manager import (
            archive_sessions_by_profile,
            archive_sessions_by_collection,
        )
        for profile_id in cleanup["removed_ids"]:
            await archive_sessions_by_profile(profile_id, user_uuid)
        for res in pack_resources:
            if res["resource_type"] == "collection":
                await archive_sessions_by_collection(str(res["resource_id"]), user_uuid)

        # NOW delete profiles from the subscriber's config and clear default if needed.
        from trusted_data_agent.core.config_manager import get_config_manager as _gcm
        _cm = _gcm()
        _cm.save_profiles(cleanup["remaining"], user_uuid)
        retire_learning_of_removed(cleanup["removed_ids"], user_uuid)
        _default_id = _cm.get_default_profile_id(user_uuid)
        if _default_id and _default_id in cleanup["removed_ids"]:
            _cm.set_default_profile_id(None, user_uuid)
            app_logger.info(
                f"unsubscribe: cleared default_profile_id for user {user_uuid} "
                f"(subscribed profile {_default_id} was removed)"
            )

        # Remove subscriber's local installation record (created during subscribe for IFOC grouping)
        try:
            _conn = sqlite_util.connect(str(DB_PATH))
            _conn.row_factory = sqlite3.Row
            _cur = _conn.cursor()
            _cur.execute(
                "SELECT id FROM agent_pack_installations "
                "WHERE owner_user_id = ? AND json_extract(manifest_json, '$._source_installation_id') = ?",
                (user_uuid, source_installation_id),
            )
            _local = _cur.fetchone()
            if _local:
                _cur.execute("DELETE FROM agent_pack_resources WHERE pack_installation_id = ?", (_local["id"],))
                _cur.execute("DELETE FROM agent_pack_installations WHERE id = ?", (_local["id"],))
                _conn.commit()
            _conn.close()
        except Exception as _del_err:
            app_logger.warning(f"unsubscribe: could not remove local installation record: {_del_err}")

        # Decrement install_count and clean up marketplace_installs record
        conn = sqlite_util.connect(str(DB_PATH))
        conn.execute(
            "UPDATE marketplace_agent_packs "
            "SET install_count = CASE WHEN install_count > 0 THEN install_count - 1 ELSE 0 END, "
            "updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), pack_id),
        )
        conn.execute(
            "DELETE FROM marketplace_installs "
            "WHERE artifact_type = 'agent_pack' AND marketplace_id = ? AND user_id = ?",
            (pack_id, user_uuid),
        )
        conn.commit()
        conn.close()

        collections_removed = sum(1 for r in pack_resources if r["resource_type"] == "collection")
        app_logger.info(
            f"User {user_uuid} unsubscribed from agent pack '{pack_name}' "
            f"(pack_id={pack_id}, profiles_removed={profiles_removed}, "
            f"profiles_converted={profiles_converted}, collections_removed={collections_removed})"
        )
        return jsonify({
            "status": "success",
            "message": f"Unsubscribed from {pack_name}",
            "profiles_removed": profiles_removed,
            "profiles_converted": profiles_converted,
            "collections_removed": collections_removed,
        }), 200

    except Exception as e:
        app_logger.error(f"Agent pack unsubscribe failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Unsubscribe failed: {e}"}), 500


# ── Fork from Marketplace ───────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/fork", methods=["POST"])
@operation("marketplace.agent_pack_fork", effect="human_only", scope="shared",
           summary="Take your own copy of someone else's pack — bringing their agents into your estate is your trust decision",
           path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def fork_marketplace_agent_pack(current_user, pack_id: str):
    """Fork a marketplace agent pack — creates a physical copy under the current user's account.

    Body (JSON): {"mcp_server_id": "...", "llm_configuration_id": "...", "conflict_strategy": "expand"}
    """
    user_uuid = current_user.id
    tmp_path = None

    try:
        data = (await request.get_json()) or {}
        mcp_server_id = data.get("mcp_server_id") or None
        llm_configuration_id = data.get("llm_configuration_id") or None
        conflict_strategy = data.get("conflict_strategy", "expand")
        try:
            mcp_bindings = _parse_mcp_bindings(data.get("mcp_bindings"))
        except ValueError as ve:
            return jsonify({"status": "error", "message": str(ve)}), 400
        vector_store_config_id = data.get("vector_store_config_id") or None
        # The two validation opt-ins `import_pack` honours for update_in_place. Without
        # them a consumer whose older fork carried a STRICTER posture than the pack now
        # ships could never take the update through the fork route at all (the guard
        # refused, and there was no way to say "accepted") — the file-import path was
        # the only door (found live, 2026-09-07).
        allow_posture_downgrade = bool(data.get("allow_posture_downgrade", False))
        force_drop_tags = bool(data.get("force_drop_tags", False))
        dry_run = bool(data.get("dry_run", False))

        # Find the marketplace pack
        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT source_installation_id, publisher_user_id, name, distribution_mode "
            "FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        mp_row = cursor.fetchone()
        if not mp_row:
            conn.close()
            return jsonify({"status": "error", "message": "Marketplace pack not found"}), 404

        if mp_row["publisher_user_id"] == user_uuid:
            conn.close()
            return jsonify({"status": "error", "message": "Cannot fork your own pack"}), 400

        dist_mode = mp_row["distribution_mode"] or "both"
        if dist_mode == "subscribe_only":
            conn.close()
            return jsonify({
                "status": "error",
                "message": "This pack is configured for subscribe only. Use the Subscribe option instead.",
            }), 403

        source_installation_id = mp_row["source_installation_id"]
        pack_name = mp_row["name"]
        publisher_user_id = mp_row["publisher_user_id"]

        # Governance (Tier 3.2): a deprecated data product can no longer be consumed.
        try:
            from trusted_data_agent.core import pack_governance
            if pack_governance.get_pack_lifecycle(source_installation_id).get("lifecycle_status") == "deprecated":
                return jsonify({"status": "error",
                                "message": "This pack has been deprecated by its owner and can no longer be forked."}), 403
        except Exception:
            pass

        # Get profile IDs from source installation resources
        cursor.execute(
            "SELECT resource_id FROM agent_pack_resources "
            "WHERE pack_installation_id = ? AND resource_type = 'profile'",
            (source_installation_id,),
        )
        profile_ids = [r["resource_id"] for r in cursor.fetchall()]
        conn.close()

        if not profile_ids:
            return jsonify({"status": "error", "message": "Source pack has no profiles to fork"}), 400

        manager = _manager()

        # Step 1: Export from publisher's context.
        # The copy keeps the ORIGINAL name — fork-ness is structured data
        # (_source_marketplace_id below), never a status smuggled into the
        # display name. The old "<name> (Fork)" suffix leaked into every
        # surface that prints pack names verbatim (observed live: a skill's
        # provenance badge read "(Fork)" for a skill nobody forked).
        zip_path = await manager.export_pack(
            profile_ids=profile_ids,
            user_uuid=publisher_user_id,
            pack_name=pack_name,
            pack_description=f"Forked from {pack_name}",
        )
        tmp_path = zip_path

        # Step 2: Import into current user's account
        result = await manager.import_pack(
            zip_path=zip_path,
            user_uuid=user_uuid,
            mcp_server_id=mcp_server_id,
            llm_configuration_id=llm_configuration_id,
            conflict_strategy=conflict_strategy,
            vector_store_config_id=vector_store_config_id,
            mcp_bindings=mcp_bindings,
            allow_posture_downgrade=allow_posture_downgrade,
            force_drop_tags=force_drop_tags,
            dry_run=dry_run,
        )
        if dry_run:
            return jsonify(result), 200
        if result.get("status") == "updated_in_place":
            # a fork that is itself published: its subscribers follow the in-place update
            schedule_pack_subscriber_sync(result.get("marketplace_listing_id"))

        # Trust Membrane portability: if the publisher signed their pack, reinstate the
        # attestation on the forked installation — the honesty gate accepts it only because
        # the composition (members' content) is id-independent, so the forked copy matches.
        _reinstate_pack_seal(source_installation_id, result.get("installation_id"))

        # Stamp the source marketplace pack onto the forked installation so that
        # uninstalling this fork later can clear the per-user install record and the
        # marketplace card returns to "Fork" (manifest_json is the only per-install store).
        try:
            inst_id = result.get("installation_id")
            if inst_id:
                _c = sqlite_util.connect(str(DB_PATH))
                _c.row_factory = sqlite3.Row
                _row = _c.execute(
                    "SELECT manifest_json FROM agent_pack_installations WHERE id = ?", (inst_id,)
                ).fetchone()
                _man = json.loads(_row["manifest_json"]) if _row and _row["manifest_json"] else {}
                _man["_source_marketplace_id"] = pack_id
                _c.execute(
                    "UPDATE agent_pack_installations SET manifest_json = ? WHERE id = ?",
                    (json.dumps(_man), inst_id),
                )
                _c.commit()
                _c.close()
        except Exception as _stamp_err:
            app_logger.warning(f"Could not stamp source marketplace id on fork: {_stamp_err}")

        # Increment download_count; record install for rating gate
        now_ts = datetime.now(timezone.utc).isoformat()
        conn = sqlite_util.connect(str(DB_PATH))
        conn.execute(
            "UPDATE marketplace_agent_packs SET download_count = download_count + 1, "
            "updated_at = ? WHERE id = ?",
            (now_ts, pack_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO marketplace_installs (id, artifact_type, marketplace_id, user_id, installed_at) "
            "VALUES (?, 'agent_pack', ?, ?, ?)",
            (str(uuid.uuid4()), pack_id, user_uuid, now_ts),
        )
        conn.commit()
        conn.close()

        app_logger.info(
            f"User {user_uuid} forked agent pack '{pack_name}' "
            f"(pack_id={pack_id}) -> installation_id={result.get('installation_id')}"
        )
        return jsonify({
            "status": "success",
            "message": f"Successfully forked '{pack_name}'",
            **result,
        }), 201

    except VectorStoreGovernanceError as e:
        app_logger.warning(f"Agent pack fork blocked by VS governance: {e}")
        return jsonify({
            "status": "vs_governance_block",
            "message": str(e),
            "blocked_collections": e.blocked_collections,
            "allowed_backends": e.allowed_backends,
            "user_allowed_configs": e.user_allowed_configs,
            "needs_vs_configuration": e.needs_vs_configuration,
        }), 409
    except ValueError as e:
        app_logger.warning(f"Agent pack fork validation error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        app_logger.error(f"Agent pack fork failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Fork failed: {e}"}), 500
    finally:
        if tmp_path and Path(tmp_path).exists():
            Path(tmp_path).unlink(missing_ok=True)


# ── Rate ─────────────────────────────────────────────────────────────────────

@agent_pack_bp.route("/v1/marketplace/agent-packs/<pack_id>/rate", methods=["POST"])
@operation("marketplace.agent_pack_rate", effect="human_only", scope="shared",
           summary="Rate a marketplace pack — a rating is a person's opinion, never an agent's", path={"pack_id": "the marketplace listing (marketplace.agent_packs lists them)"})
@require_auth
async def rate_marketplace_agent_pack(current_user, pack_id: str):
    """Rate a marketplace agent pack.

    Body (JSON): {"rating": 5, "comment": "Excellent pack!"}
    """
    user_uuid = current_user.id

    try:
        data = await request.get_json()
        if not data or "rating" not in data:
            return jsonify({"status": "error", "message": "rating is required"}), 400

        rating = int(data["rating"])
        if rating < 1 or rating > 5:
            return jsonify({"status": "error", "message": "rating must be between 1 and 5"}), 400

        comment = data.get("comment", "").strip() or None

        conn = sqlite_util.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Verify pack exists
        cursor.execute(
            "SELECT id, publisher_user_id, source_installation_id FROM marketplace_agent_packs WHERE id = ?",
            (pack_id,),
        )
        pack_row = cursor.fetchone()
        if not pack_row:
            conn.close()
            return jsonify({"status": "error", "message": "Pack not found"}), 404

        # Cannot rate own pack
        if pack_row["publisher_user_id"] == user_uuid:
            conn.close()
            return jsonify({"status": "error", "message": "Cannot rate your own pack"}), 400

        # Fix 4A: Must have installed/subscribed before rating
        cursor.execute(
            "SELECT 1 FROM marketplace_installs "
            "WHERE artifact_type = 'agent_pack' AND marketplace_id = ? AND user_id = ?",
            (pack_id, user_uuid),
        )
        if not cursor.fetchone():
            # Legacy fallback: check sharing grant (pre-Fix 4A installs)
            cursor.execute(
                "SELECT 1 FROM marketplace_sharing_grants "
                "WHERE resource_type = 'agent_pack' AND resource_id = ? AND grantee_user_id = ?",
                (str(pack_row["source_installation_id"]), user_uuid),
            )
            if not cursor.fetchone():
                conn.close()
                return jsonify({
                    "status": "error",
                    "message": "You must subscribe to or install this pack before rating it.",
                }), 403

        # Check existing rating
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "SELECT id FROM agent_pack_ratings WHERE pack_id = ? AND user_id = ?",
            (pack_id, user_uuid),
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                "UPDATE agent_pack_ratings SET rating = ?, comment = ?, updated_at = ? WHERE id = ?",
                (rating, comment, now, existing["id"]),
            )
            rating_id = existing["id"]
            message = "Rating updated successfully"
            status_code = 200
        else:
            rating_id = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO agent_pack_ratings (id, pack_id, user_id, rating, comment, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rating_id, pack_id, user_uuid, rating, comment, now, now),
            )
            message = "Rating submitted successfully"
            status_code = 201

        conn.commit()
        conn.close()

        return jsonify({"status": "success", "rating_id": rating_id, "message": message}), status_code

    except Exception as e:
        app_logger.error(f"Agent pack rating failed: {e}", exc_info=True)
        return jsonify({"status": "error", "message": f"Rating failed: {e}"}), 500


# ── Author notification ────────────────────────────────────────────────────────

def _build_author_notification_html(
    author_name: str,
    repo_name: str,
    repo_url: str,
    pack_name: str,
    pack_tags: list,
    license_name: str,
) -> str:
    """Return a professional HTML email notifying a repo author their work is now a Uderia agent pack."""
    logo_cell = (
        f'<td style="vertical-align:middle;padding-right:12px">'
        f'<img src="{_EMAIL_LOGO_URL}" alt="Uderia" width="40" height="40" '
        f'style="display:block;border:0;outline:none;text-decoration:none" /></td>'
    )
    tags_html = "".join(
        f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:3px 10px;'
        f'background:#f15f2218;color:#F15F22;border:1px solid #f15f2240;'
        f'border-radius:4px;font-size:12px;font-family:monospace">{t}</span>'
        for t in (pack_tags or [])
    )
    author_first = author_name.split()[0] if author_name else "there"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Your repository is now a Uderia Agent Pack</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">

  <!-- Outer wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f1f5f9;padding:40px 0">
    <tr><td align="center">

      <!-- Card -->
      <table width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;width:100%;border-radius:12px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,0.10)">

        <!-- Header -->
        <tr>
          <td style="background:#0f172a;padding:32px 40px 0 40px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="vertical-align:middle">
                  <table cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      {logo_cell}
                      <td style="vertical-align:middle">
                        <span style="font-size:22px;font-weight:700;letter-spacing:-0.5px;color:#f1f5f9">
                          Uderia
                        </span>
                      </td>
                    </tr>
                  </table>
                </td>
                <td align="right" style="vertical-align:middle">
                  <span style="font-size:11px;color:#64748b;letter-spacing:0.5px;text-transform:uppercase">
                    Agent Pack Notification
                  </span>
                </td>
              </tr>
            </table>
            <!-- Orange accent bar -->
            <div style="margin-top:28px;height:3px;background:linear-gradient(90deg,#F15F22,#f15f2200)"></div>
          </td>
        </tr>

        <!-- Hero -->
        <tr>
          <td style="background:#0f172a;padding:36px 40px 40px 40px">
            <p style="margin:0 0 12px 0;font-size:12px;font-weight:600;letter-spacing:1.5px;
                      text-transform:uppercase;color:#F15F22">
              Your work is live on Uderia
            </p>
            <h1 style="margin:0 0 16px 0;font-size:28px;font-weight:700;line-height:1.25;
                       color:#f1f5f9;letter-spacing:-0.5px">
              Your repository has been<br>featured as an Agent Pack
            </h1>
            <p style="margin:0;font-size:15px;line-height:1.7;color:#94a3b8">
              Hi {author_first}, we adapted <strong style="color:#f1f5f9">{repo_name}</strong>
              into a ready-to-deploy agent pack on the Uderia platform.
              We wanted you to know — and to say thank you.
            </p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="background:#ffffff;padding:40px 40px 0 40px">

            <!-- Pack details box -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                          margin-bottom:32px">
              <tr>
                <td style="padding:24px 28px">
                  <p style="margin:0 0 16px 0;font-size:11px;font-weight:700;letter-spacing:1px;
                             text-transform:uppercase;color:#94a3b8">Agent Pack Details</p>
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td style="padding:6px 0;font-size:13px;color:#64748b;width:90px;
                                 vertical-align:top;font-weight:500">Pack</td>
                      <td style="padding:6px 0;font-size:13px;color:#1e293b;
                                 font-weight:600">{pack_name}</td>
                    </tr>
                    <tr>
                      <td style="padding:6px 0;font-size:13px;color:#64748b;
                                 vertical-align:top;font-weight:500">Source</td>
                      <td style="padding:6px 0;font-size:13px">
                        <a href="{repo_url}" style="color:#F15F22;text-decoration:none">{repo_url}</a>
                      </td>
                    </tr>
                    <tr>
                      <td style="padding:6px 0;font-size:13px;color:#64748b;
                                 vertical-align:top;font-weight:500">License</td>
                      <td style="padding:6px 0;font-size:13px;color:#1e293b">{license_name}</td>
                    </tr>
                    {'<tr><td style="padding:10px 0 0 0" colspan="2">' + tags_html + '</td></tr>' if tags_html else ''}
                  </table>
                </td>
              </tr>
            </table>

            <!-- What is Uderia -->
            <p style="margin:0 0 12px 0;font-size:11px;font-weight:700;letter-spacing:1px;
                       text-transform:uppercase;color:#94a3b8">What is Uderia?</p>
            <p style="margin:0 0 16px 0;font-size:14px;line-height:1.75;color:#475569">
              Uderia is an enterprise AI orchestration platform that lets teams deploy
              multi-agent workflows against their data — connecting language models,
              data sources, and tools in a governed, auditable way.
            </p>
            <p style="margin:0 0 32px 0;font-size:14px;line-height:1.75;color:#475569">
              Agent packs are portable bundles — profiles, skills, and routing logic —
              that anyone can deploy with a single import. Yours is now one of them.
            </p>

            <!-- Divider -->
            <div style="border-top:1px solid #e2e8f0;margin-bottom:32px"></div>

            <!-- Attribution note -->
            <p style="margin:0 0 32px 0;font-size:13px;line-height:1.7;color:#64748b;
                       background:#f8fafc;border-left:3px solid #F15F22;padding:14px 18px;
                       border-radius:0 6px 6px 0">
              Your original work is credited in the pack with a direct link to
              <a href="{repo_url}" style="color:#F15F22;text-decoration:none">{repo_url}</a>.
              If you have any questions or concerns about how your repository was adapted,
              please reply to this email and we will get back to you promptly.
            </p>

            <!-- CTA -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="margin-bottom:40px">
              <tr>
                <td align="center">
                  <a href="https://uderia.com"
                     style="display:inline-block;padding:14px 36px;background:#F15F22;
                            color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;
                            border-radius:6px;letter-spacing:0.25px">
                    Explore Uderia
                  </a>
                </td>
              </tr>
            </table>

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#0f172a;padding:28px 40px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td>
                  <p style="margin:0 0 4px 0;font-size:12px;font-weight:600;color:#f1f5f9">
                    Uderia Platform
                  </p>
                  <p style="margin:0;font-size:11px;color:#475569;line-height:1.6">
                    Enterprise AI Orchestration &nbsp;·&nbsp;
                    Zero-Trust Privacy &nbsp;·&nbsp;
                    Multi-Provider LLM
                  </p>
                </td>
                <td align="right" valign="middle">
                  <span style="font-size:18px;font-weight:700;color:#f1f5f9;
                               letter-spacing:-0.5px">
                    Uderia
                  </span>
                </td>
              </tr>
              <tr>
                <td colspan="2" style="padding-top:20px;border-top:1px solid #1e293b">
                  <p style="margin:0;font-size:10px;color:#334155;line-height:1.6">
                    You are receiving this email because your public repository was adapted
                    as a Uderia agent pack. This is a one-time notification.
                    To request removal, reply to this email.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

      </table>
      <!-- / Card -->

    </td></tr>
  </table>

</body>
</html>"""


@agent_pack_bp.route("/v1/agent-packs/notify-author", methods=["POST"])
@operation("agent_packs.notify_author", effect="human_only", scope="own",
           summary="Email the author of a pack's source — a message from you to someone outside Uderia")
@require_auth
async def notify_pack_author(current_user):
    """
    Send a one-time courtesy notification to the author of a source repository
    whose work has been adapted as a Uderia agent pack.

    Body (JSON):
        author_email  str   required  Recipient email address
        author_name   str   optional  Author display name (default: "there")
        repo_url      str   required  Source repository URL
        repo_name     str   optional  Short repo name shown in email (default: derived from URL)
        pack_name     str   optional  Agent pack name (default: "Uderia Agent Pack")
        pack_tags     list  optional  Tag list shown in email
        license_name  str   optional  License identifier (default: "Open Source")

    Returns:
        200  {"status": "sent",    "to": "<email>"}
        400  {"status": "error",   "message": "<reason>"}
        500  {"status": "error",   "message": "<reason>"}
    """
    try:
        data = await request.get_json() or {}

        author_email = (data.get("author_email") or "").strip()
        if not author_email:
            return jsonify({"status": "error", "message": "author_email is required"}), 400

        repo_url   = (data.get("repo_url") or "").strip()
        if not repo_url:
            return jsonify({"status": "error", "message": "repo_url is required"}), 400

        author_name  = (data.get("author_name") or "").strip()
        repo_name    = (data.get("repo_name") or repo_url.rstrip("/").split("/")[-1]).strip()
        pack_name    = (data.get("pack_name") or "Uderia Agent Pack").strip()
        pack_tags    = data.get("pack_tags") or []
        license_name = (data.get("license_name") or "Open Source").strip()

        html_body = _build_author_notification_html(
            author_name  = author_name,
            repo_name    = repo_name,
            repo_url     = repo_url,
            pack_name    = pack_name,
            pack_tags    = pack_tags,
            license_name = license_name,
        )

        from trusted_data_agent.auth.email_service import EmailService
        subject = f"Your repository is now a Uderia Agent Pack — {repo_name}"
        sent = await EmailService.send_email(author_email, subject, html_body,
                                             cc_emails=["info@uderia.com"])

        if sent:
            app_logger.info(f"Author notification sent to {author_email} for repo {repo_url}")
            return jsonify({"status": "sent", "to": author_email}), 200
        else:
            app_logger.warning(f"Author notification failed (email service returned False) for {author_email}")
            return jsonify({"status": "error", "message": "Email service failed to send — check server email configuration"}), 500

    except Exception as e:
        app_logger.error(f"Author notification error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Author update notification HTML ───────────────────────────────────────────

def _build_author_update_notification_html(
    author_name: str,
    repo_name: str,
    repo_url: str,
    pack_name: str,
    pack_tags: list,
    license_name: str,
    previous_commit: str,
    current_commit: str,
    changes: list,
) -> str:
    """Return a professional HTML email notifying a repo author that their adapted pack has been updated."""
    logo_cell = (
        f'<td style="vertical-align:middle;padding-right:12px">'
        f'<img src="{_EMAIL_LOGO_URL}" alt="Uderia" width="40" height="40" '
        f'style="display:block;border:0;outline:none;text-decoration:none" /></td>'
    )
    tags_html = "".join(
        f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:3px 10px;'
        f'background:#f15f2218;color:#F15F22;border:1px solid #f15f2240;'
        f'border-radius:4px;font-size:12px;font-family:monospace">{t}</span>'
        for t in (pack_tags or [])
    )
    author_first = author_name.split()[0] if author_name else "there"

    commit_range = (
        f'<code style="font-family:monospace;font-size:12px;color:#94a3b8">'
        f'{previous_commit} → {current_commit}</code>'
        if previous_commit else
        f'<code style="font-family:monospace;font-size:12px;color:#94a3b8">{current_commit}</code>'
    )

    changes_html = "".join(
        f'<tr><td style="padding:4px 0;font-size:13px;color:#475569;vertical-align:top">'
        f'<span style="color:#F15F22;margin-right:8px">·</span>{change}</td></tr>'
        for change in (changes or [f"Updated to commit {current_commit}"])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Uderia Agent Pack updated — {repo_name}</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">

  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f1f5f9;padding:40px 0">
    <tr><td align="center">

      <table width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;width:100%;border-radius:12px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,0.10)">

        <!-- Header -->
        <tr>
          <td style="background:#0f172a;padding:32px 40px 0 40px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="vertical-align:middle">
                  <table cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      {logo_cell}
                      <td style="vertical-align:middle">
                        <span style="font-size:22px;font-weight:700;letter-spacing:-0.5px;color:#f1f5f9">
                          Uderia
                        </span>
                      </td>
                    </tr>
                  </table>
                </td>
                <td align="right" style="vertical-align:middle">
                  <span style="font-size:11px;color:#64748b;letter-spacing:0.5px;text-transform:uppercase">
                    Agent Pack Update
                  </span>
                </td>
              </tr>
            </table>
            <div style="margin-top:28px;height:3px;background:linear-gradient(90deg,#F15F22,#f15f2200)"></div>
          </td>
        </tr>

        <!-- Hero -->
        <tr>
          <td style="background:#0f172a;padding:36px 40px 40px 40px">
            <p style="margin:0 0 12px 0;font-size:12px;font-weight:600;letter-spacing:1.5px;
                      text-transform:uppercase;color:#F15F22">
              Your agent pack has been updated
            </p>
            <h1 style="margin:0 0 16px 0;font-size:28px;font-weight:700;line-height:1.25;
                       color:#f1f5f9;letter-spacing:-0.5px">
              {pack_name}<br>
              <span style="font-size:20px;color:#64748b;font-weight:400">has a new version</span>
            </h1>
            <p style="margin:0;font-size:15px;line-height:1.7;color:#94a3b8">
              Hi {author_first}, we've synced the Uderia agent pack for
              <strong style="color:#f1f5f9">{repo_name}</strong>
              with the latest changes from your repository.
            </p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="background:#ffffff;padding:40px 40px 0 40px">

            <!-- Pack + commit details box -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;
                          margin-bottom:32px">
              <tr>
                <td style="padding:24px 28px">
                  <p style="margin:0 0 16px 0;font-size:11px;font-weight:700;letter-spacing:1px;
                             text-transform:uppercase;color:#94a3b8">Update Details</p>
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td style="padding:6px 0;font-size:13px;color:#64748b;width:90px;
                                 vertical-align:top;font-weight:500">Pack</td>
                      <td style="padding:6px 0;font-size:13px;color:#1e293b;font-weight:600">{pack_name}</td>
                    </tr>
                    <tr>
                      <td style="padding:6px 0;font-size:13px;color:#64748b;
                                 vertical-align:top;font-weight:500">Source</td>
                      <td style="padding:6px 0;font-size:13px">
                        <a href="{repo_url}" style="color:#F15F22;text-decoration:none">{repo_url}</a>
                      </td>
                    </tr>
                    <tr>
                      <td style="padding:6px 0;font-size:13px;color:#64748b;
                                 vertical-align:top;font-weight:500">Commit</td>
                      <td style="padding:6px 0">{commit_range}</td>
                    </tr>
                    {'<tr><td style="padding:10px 0 0 0" colspan="2">' + tags_html + '</td></tr>' if tags_html else ''}
                  </table>
                </td>
              </tr>
            </table>

            <!-- Changes -->
            <p style="margin:0 0 12px 0;font-size:11px;font-weight:700;letter-spacing:1px;
                       text-transform:uppercase;color:#94a3b8">What changed</p>
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="margin-bottom:32px">
              {changes_html}
            </table>

            <div style="border-top:1px solid #e2e8f0;margin-bottom:32px"></div>

            <!-- Attribution note -->
            <p style="margin:0 0 32px 0;font-size:13px;line-height:1.7;color:#64748b;
                       background:#f8fafc;border-left:3px solid #F15F22;padding:14px 18px;
                       border-radius:0 6px 6px 0">
              Your original work remains credited with a direct link to
              <a href="{repo_url}" style="color:#F15F22;text-decoration:none">{repo_url}</a>.
              If you have any questions or concerns, please reply to this email.
            </p>

            <!-- CTA -->
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="margin-bottom:40px">
              <tr>
                <td align="center">
                  <a href="https://uderia.com"
                     style="display:inline-block;padding:14px 36px;background:#F15F22;
                            color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;
                            border-radius:6px;letter-spacing:0.25px">
                    Explore Uderia
                  </a>
                </td>
              </tr>
            </table>

          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#0f172a;padding:28px 40px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td>
                  <p style="margin:0 0 4px 0;font-size:12px;font-weight:600;color:#f1f5f9">
                    Uderia Platform
                  </p>
                  <p style="margin:0;font-size:11px;color:#475569;line-height:1.6">
                    Enterprise AI Orchestration &nbsp;·&nbsp;
                    Zero-Trust Privacy &nbsp;·&nbsp;
                    Multi-Provider LLM
                  </p>
                </td>
                <td align="right" valign="middle">
                  <span style="font-size:18px;font-weight:700;color:#1e293b;letter-spacing:-0.5px">
                    U<span style="color:#F15F22">·</span>
                  </span>
                </td>
              </tr>
              <tr>
                <td colspan="2" style="padding-top:20px;border-top:1px solid #1e293b">
                  <p style="margin:0;font-size:10px;color:#334155;line-height:1.6">
                    You are receiving this email because your public repository was adapted
                    as a Uderia agent pack. Update notifications are sent once per release.
                    To unsubscribe, reply to this email.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>

      </table>

    </td></tr>
  </table>

</body>
</html>"""


@agent_pack_bp.route("/v1/agent-packs/notify-author-update", methods=["POST"])
@operation("agent_packs.notify_author_update", effect="human_only", scope="own",
           summary="Email the author of a pack's source about an update — a message from you to someone outside Uderia")
@require_auth
async def notify_pack_author_update(current_user):
    """
    Send a one-time update notification to the author of a source repository
    whose adapted Uderia agent pack has been updated to a new commit.

    Body (JSON):
        author_email     str   required  Recipient email address
        author_name      str   optional  Author display name (default: "there")
        repo_url         str   required  Source repository URL
        repo_name        str   optional  Short repo name (default: derived from URL)
        pack_name        str   optional  Agent pack name (default: "Uderia Agent Pack")
        pack_tags        list  optional  Tag list shown in email
        license_name     str   optional  License identifier (default: "Open Source")
        previous_commit  str   optional  Previous source commit hash
        current_commit   str   optional  New source commit hash
        changes          list  optional  Commit messages / change descriptions

    Returns:
        200  {"status": "sent",  "to": "<email>"}
        400  {"status": "error", "message": "<reason>"}
        500  {"status": "error", "message": "<reason>"}
    """
    try:
        data = await request.get_json() or {}

        author_email = (data.get("author_email") or "").strip()
        if not author_email:
            return jsonify({"status": "error", "message": "author_email is required"}), 400

        repo_url = (data.get("repo_url") or "").strip()
        if not repo_url:
            return jsonify({"status": "error", "message": "repo_url is required"}), 400

        author_name      = (data.get("author_name") or "").strip()
        repo_name        = (data.get("repo_name") or repo_url.rstrip("/").split("/")[-1]).strip()
        pack_name        = (data.get("pack_name") or "Uderia Agent Pack").strip()
        pack_tags        = data.get("pack_tags") or []
        license_name     = (data.get("license_name") or "Open Source").strip()
        previous_commit  = (data.get("previous_commit") or "").strip()
        current_commit   = (data.get("current_commit") or "").strip()
        changes          = data.get("changes") or []

        html_body = _build_author_update_notification_html(
            author_name     = author_name,
            repo_name       = repo_name,
            repo_url        = repo_url,
            pack_name       = pack_name,
            pack_tags       = pack_tags,
            license_name    = license_name,
            previous_commit = previous_commit,
            current_commit  = current_commit,
            changes         = changes,
        )

        from trusted_data_agent.auth.email_service import EmailService
        subject = f"Uderia Agent Pack updated — {repo_name}"
        sent = await EmailService.send_email(author_email, subject, html_body,
                                             cc_emails=["info@uderia.com"])

        if sent:
            app_logger.info(f"Author update notification sent to {author_email} for repo {repo_url} commit {current_commit}")
            return jsonify({"status": "sent", "to": author_email}), 200
        else:
            app_logger.warning(f"Author update notification failed (email service returned False) for {author_email}")
            return jsonify({"status": "error", "message": "Email service failed to send — check server email configuration"}), 500

    except Exception as e:
        app_logger.error(f"Author update notification error: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500
