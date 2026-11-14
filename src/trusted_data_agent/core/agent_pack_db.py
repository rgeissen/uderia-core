"""
Query helper for the agent_pack_resources junction table.

Centralizes all many-to-many relationship queries between agent packs
and their resources (profiles + collections). Used by rest_routes.py
(API enrichment, deletion constraints), agent_pack_manager.py (uninstall),
and frontend enrichment.
"""

import sqlite3
from trusted_data_agent.core import sqlite_util  # WAL+busy_timeout connection factory (root-cause: async-loop SQLite deadlock)
import logging
from pathlib import Path

logger = logging.getLogger("quart.app")

# Default DB path (same convention as database.py)
_DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[3] / "tda_auth.db")


class AgentPackDB:
    """Query helper for agent_pack_resources junction table."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DEFAULT_DB_PATH

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite_util.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_packs_for_resource(self, resource_type: str, resource_id: str,
                               owner_user_id: str | None = None) -> list[dict]:
        """Return all packs that reference a resource.

        Returns: [{"id": 3, "name": "Virtual Account Team"}, ...]

        ``owner_user_id`` scopes the answer to ONE user's installations. A subscribed
        collection is a REFERENCE — the publisher's installation and every subscriber's
        installation name the same collection id — so the unscoped answer lists the same
        pack once per account that references it (a duplicated chip on every card). A
        card asks for the caller's own packs; the delete/uninstall policy sites keep the
        unscoped, cross-account answer (a resource another account's pack references is
        still referenced).
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()
            sql = """
                SELECT api.id, api.name
                FROM agent_pack_installations api
                JOIN agent_pack_resources apr ON api.id = apr.pack_installation_id
                WHERE apr.resource_type = ? AND apr.resource_id = ?
            """
            params: list = [resource_type, str(resource_id)]
            if owner_user_id is not None:
                sql += " AND api.owner_user_id = ?"
                params.append(owner_user_id)
            cursor.execute(sql, params)
            packs = [{"id": row["id"], "name": row["name"]} for row in cursor.fetchall()]
            if owner_user_id is None:
                return packs
            # Owner-scoped, the card's question: "which of MY packs manage this?".
            # One owner holding two installations of the same pack NAME that both
            # claim the same resource is always bookkeeping, never two packs — a
            # genuine second install produces DIFFERENT resources (`expand` renames
            # the tags, `replace` deletes the originals). Two identical chips name
            # no fact a reader can act on, so the name is reported once; the
            # anomaly is LOGGED rather than silently swallowed. Distinct pack names
            # are never merged — a resource shared by two real packs still shows two.
            seen: dict[str, dict] = {}
            for pack in packs:
                first = seen.get(pack["name"])
                if first is None:
                    seen[pack["name"]] = pack
                else:
                    logger.warning(
                        "agent pack '%s' is recorded twice for one owner against %s %s "
                        "(installations %s and %s) — reporting it once. "
                        "maintenance/dedupe_pack_registrations.py removes the superseded row.",
                        pack["name"], resource_type, resource_id, first["id"], pack["id"])
            return list(seen.values())
        finally:
            conn.close()

    def subscription_packs_for_collection(self, user_uuid: str, collection_id) -> list[dict]:
        """The caller's own pack SUBSCRIPTIONS that hold this collection as a reference —
        installations the caller owns whose ledger names the collection with
        ``is_owned = 0``. Such a reference was made by the pack subscribe and is removed by
        the pack unsubscribe; the collection-marketplace unsubscribe must refuse it.

        Returns: [{"installation_id": 92, "name": "Policing Data Products",
                   "listing_id": "<marketplace_agent_packs.id or None>"}]
        """
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT api.id AS installation_id, api.name,
                       json_extract(api.manifest_json, '$._source_installation_id') AS src
                FROM agent_pack_installations api
                JOIN agent_pack_resources apr ON api.id = apr.pack_installation_id
                WHERE api.owner_user_id = ? AND apr.resource_type = 'collection'
                  AND apr.resource_id = ? AND COALESCE(apr.is_owned, 0) = 0
            """, (user_uuid, str(collection_id))).fetchall()
            out = []
            for r in rows:
                listing = None
                if r["src"] is not None:
                    try:
                        lr = conn.execute(
                            "SELECT id FROM marketplace_agent_packs WHERE source_installation_id = ? "
                            "ORDER BY id DESC LIMIT 1", (r["src"],)).fetchone()
                        listing = lr["id"] if lr else None
                    except Exception:
                        listing = None
                out.append({"installation_id": r["installation_id"], "name": r["name"], "listing_id": listing})
            return out
        finally:
            conn.close()

    def is_pack_managed(self, resource_type: str, resource_id: str) -> bool:
        """Return True if the resource is referenced by any pack."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM agent_pack_resources
                WHERE resource_type = ? AND resource_id = ?
            """, (resource_type, str(resource_id)))
            return cursor.fetchone()[0] > 0
        finally:
            conn.close()

    def get_pack_names_for_resource(self, resource_type: str, resource_id: str) -> list[str]:
        """Return list of pack names for a resource (for UI badges)."""
        packs = self.get_packs_for_resource(resource_type, resource_id)
        return [p["name"] for p in packs]

    def is_safe_to_delete(
        self, resource_type: str, resource_id: str,
        excluding_pack_id: int | None = None
    ) -> bool:
        """Return True if the resource is not referenced by any (other) pack.

        Args:
            resource_type: 'profile' or 'collection'
            resource_id: Profile ID or collection ID as string
            excluding_pack_id: If provided, exclude this pack from the check
                              (used during uninstall to check if other packs reference it)
        """
        conn = self._connect()
        try:
            cursor = conn.cursor()
            if excluding_pack_id is not None:
                cursor.execute("""
                    SELECT COUNT(*) FROM agent_pack_resources
                    WHERE resource_type = ? AND resource_id = ?
                    AND pack_installation_id != ?
                """, (resource_type, str(resource_id), excluding_pack_id))
            else:
                cursor.execute("""
                    SELECT COUNT(*) FROM agent_pack_resources
                    WHERE resource_type = ? AND resource_id = ?
                """, (resource_type, str(resource_id)))
            return cursor.fetchone()[0] == 0
        finally:
            conn.close()

    def installation_is_owned(self, pack_installation_id: int) -> bool:
        """THE pack-level ownership rule — one definition, two consumers.

        Owned = the installation owns at least one resource (created/forked/
        imported), OR it carries the created-here origin marker
        (``_registration_origin == "created"`` — create-and-install registers a
        pack over REFERENCED resources, deliberately owning none so uninstall
        keeps the user's originals). ``list_packs`` inlines the same expression
        in SQL for the whole listing; the publish gate calls this. A third copy
        is how the "your own pack is a subscription of itself" defect happened
        twice (the badge, then the publish refusal).
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT CASE WHEN EXISTS("
                "  SELECT 1 FROM agent_pack_resources r "
                "  WHERE r.pack_installation_id = i.id AND r.is_owned = 1"
                ") OR json_extract(i.manifest_json, '$._registration_origin') = 'created' "
                "THEN 1 ELSE 0 END "
                "FROM agent_pack_installations i WHERE i.id = ?",
                (pack_installation_id,),
            ).fetchone()
            return bool(row and row[0])
        finally:
            conn.close()

    def get_resources_for_pack(self, pack_installation_id: int) -> list[dict]:
        """Return all resources for a pack."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT resource_type, resource_id, resource_tag, resource_role, is_owned
                FROM agent_pack_resources WHERE pack_installation_id = ?
            """, (pack_installation_id,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_installations_by_profile_tags(
        self, tags: list[str], owner_user_id: str
    ) -> dict[str, dict]:
        """Resolve profile tags to the pack installation that OWNS them, from the ledger.

        The ledger (`agent_pack_resources`) is the pack's system of record for what it
        owns; `user_preferences.profiles` is a mutable copy of the same fact. When the
        two diverge — a profile removed out-of-band while the pack row survives — a
        tag→id lookup against the copy silently reports "not installed", and every
        repair path keyed on it becomes unreachable. Resolving from the ledger lets a
        re-import reconcile reality TO the ledger instead of assuming they already agree.

        Scoped to `owner_user_id` so one user's ledger can never resolve another's tag.

        Returns: {tag: {"installation_id": int, "profile_id": str}}
        """
        if not tags:
            return {}
        conn = self._connect()
        try:
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(tags))
            cursor.execute(f"""
                SELECT apr.resource_tag, apr.pack_installation_id, apr.resource_id
                FROM agent_pack_resources apr
                JOIN agent_pack_installations api ON api.id = apr.pack_installation_id
                WHERE apr.resource_type = 'profile'
                  AND apr.resource_tag IN ({placeholders})
                  AND api.owner_user_id = ?
            """, (*tags, owner_user_id))
            return {
                row["resource_tag"]: {
                    "installation_id": int(row["pack_installation_id"]),
                    "profile_id": str(row["resource_id"]),
                }
                for row in cursor.fetchall()
            }
        finally:
            conn.close()

    def remove_pack_resources(self, pack_installation_id: int):
        """Remove all junction rows for a pack (during uninstall)."""
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM agent_pack_resources WHERE pack_installation_id = ?",
                (pack_installation_id,)
            )
            conn.commit()
            logger.info(f"Removed junction rows for pack {pack_installation_id}")
        finally:
            conn.close()

    def reconcile_pack_resources(
        self,
        pack_installation_id: int,
        desired: list[dict],
    ) -> dict:
        """Bring the junction table in line with a desired resource set.

        ``desired`` is a list of dicts with keys: ``resource_type``, ``resource_id``,
        ``resource_tag``, ``resource_role``, ``is_owned``. Existing rows that match
        a desired entry by (resource_type, resource_id) are updated in place
        (resource_tag/role/is_owned). Rows present only in ``desired`` are added.
        Rows present only in the database are removed.

        Returns: {"added": N, "updated": N, "removed": N}.

        Used by the agent pack update_in_place flow to keep agent_pack_resources
        coherent without recreating the installation row.
        """
        existing = self.get_resources_for_pack(pack_installation_id)
        existing_by_key = {
            (r["resource_type"], str(r["resource_id"])): r for r in existing
        }
        desired_by_key = {
            (d["resource_type"], str(d["resource_id"])): d for d in desired
        }

        added = 0
        updated = 0
        removed = 0
        conn = self._connect()
        try:
            cursor = conn.cursor()
            for key, d in desired_by_key.items():
                if key in existing_by_key:
                    cursor.execute(
                        """
                        UPDATE agent_pack_resources
                        SET resource_tag = ?, resource_role = ?, is_owned = ?
                        WHERE pack_installation_id = ?
                          AND resource_type = ? AND resource_id = ?
                        """,
                        (
                            d.get("resource_tag"),
                            d.get("resource_role"),
                            1 if d.get("is_owned", True) else 0,
                            pack_installation_id,
                            d["resource_type"],
                            str(d["resource_id"]),
                        ),
                    )
                    updated += 1
                else:
                    cursor.execute(
                        """
                        INSERT INTO agent_pack_resources
                        (pack_installation_id, resource_type, resource_id,
                         resource_tag, resource_role, is_owned)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pack_installation_id,
                            d["resource_type"],
                            str(d["resource_id"]),
                            d.get("resource_tag"),
                            d.get("resource_role"),
                            1 if d.get("is_owned", True) else 0,
                        ),
                    )
                    added += 1

            for key in existing_by_key.keys() - desired_by_key.keys():
                cursor.execute(
                    """
                    DELETE FROM agent_pack_resources
                    WHERE pack_installation_id = ?
                      AND resource_type = ? AND resource_id = ?
                    """,
                    (pack_installation_id, key[0], key[1]),
                )
                removed += 1

            conn.commit()
        finally:
            conn.close()

        logger.info(
            f"Reconciled pack {pack_installation_id} resources: "
            f"added={added} updated={updated} removed={removed}"
        )
        return {"added": added, "updated": updated, "removed": removed}
