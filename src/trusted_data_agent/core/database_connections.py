"""
Centralized, user-centric Database Connection store.

A single source of truth for named relational database connections that every
participant draws from — Canvas execution, PDM deployment, and any future SQL
consumer.  Non-secret metadata is persisted via ``config_manager``
(``database_connections`` in user preferences); secrets (password / PAT / PEM)
are encrypted separately under the ``dbconn_{id}`` provider, mirroring the
vector-store configuration pattern.

Design notes (best practice, not a shortcut):
  * Secrets never live in the metadata record — only in the encrypted credential
    provider.  ``list``/``get`` return masked creds; ``resolve`` returns the full
    creds and is the only path that exposes secrets (for actual connection use).
  * The store *unifies* legacy Canvas connections (``canvas_conn_{id}``): they are
    surfaced read-only in ``list`` and resolvable, and ``migrate_legacy`` promotes
    them into the central namespace non-destructively.
  * ``config_manager`` and the credential layer are injected, so the service is
    unit-testable with in-memory fakes (no DB, no Fernet).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("quart.app")

PROVIDER_PREFIX = "dbconn_"
LEGACY_PREFIX = "canvas_conn_"
MASK = "••••••••"

#: THE driver declaration. Every surface that names a driver derives from it — the
#: store's validation, the Canvas SQL connector, the Database Navigator's resolution and
#: catalog provider, the connection form, the profile editor's source pickers (the JS
#: mirrors are pinned equal to it by test/test_duckdb_driver.py).
#:   label   — how the UI names it
#:   catalog — how the Database Navigator reads its dictionary: "teradata" (DBC views),
#:             "information_schema", "sqlite" (sqlite_master + PRAGMA), or None (not a
#:             direct catalog source — reachable through the Canvas only)
#:   file    — the connection IS a local file path (``database``); no host, no login
#:   comments — the catalog reader returns table and column comments (a capability the
#:             Navigator advertises only when a reader actually fetches them)
#:   pip     — the package the executor needs
#:   language — the query language the connection speaks: "sql", or "mongodb" (a JSON
#:             statement naming a collection and an aggregation pipeline — core.mongo_query)
DRIVERS: Dict[str, Dict[str, Any]] = {
    "teradata":   {"label": "Teradata",       "catalog": "teradata",           "file": False, "comments": True,  "pip": "teradatasql", "language": "sql"},
    "postgresql": {"label": "PostgreSQL",     "catalog": "information_schema", "file": False, "comments": True,  "pip": "asyncpg",     "language": "sql"},
    "mysql":      {"label": "MySQL",          "catalog": "information_schema", "file": False, "comments": False, "pip": "aiomysql",    "language": "sql"},
    "sqlite":     {"label": "SQLite",         "catalog": "sqlite",             "file": True,  "comments": False, "pip": "aiosqlite",   "language": "sql"},
    "duckdb":     {"label": "DuckDB",         "catalog": "information_schema", "file": True,  "comments": True,  "pip": "duckdb",      "language": "sql"},
    "mongodb":    {"label": "MongoDB",        "catalog": "mongodb",            "file": False, "comments": False, "pip": "pymongo",     "language": "mongodb"},
    "jdbc":       {"label": "JDBC (Generic)", "catalog": None,                 "file": False, "comments": False, "pip": "JayDeBeApi",  "language": "sql"},
}

#: Every driver the store accepts.
SUPPORTED_DRIVERS = tuple(DRIVERS)
#: Drivers the Database Navigator reads directly (a dictionary reader exists).
DIRECT_DRIVERS = tuple(d for d, spec in DRIVERS.items() if spec["catalog"])
#: Drivers whose connection is a local file path.
FILE_DRIVERS = tuple(d for d, spec in DRIVERS.items() if spec["file"])
#: Drivers whose statements are not SQL (each names its language in DRIVERS).
DOCUMENT_DRIVERS = tuple(d for d, spec in DRIVERS.items() if spec["language"] != "sql")


def driver_language(driver: str) -> str:
    """The query language a driver speaks ("sql" for an unknown one — the historical default)."""
    return (DRIVERS.get(str(driver or "").strip().lower()) or {}).get("language", "sql")

#: Fields treated as secrets — stored encrypted, never in metadata, masked on read.
SECRET_FIELDS = ("password", "pat_token", "pem_content")

#: Non-secret metadata fields persisted on the record.
META_FIELDS = ("host", "port", "database", "username", "driver", "name",
               "jdbc_url", "jdbc_driver_class", "jdbc_driver_path", "pem_file", "ssl",
               # MongoDB: the database that holds the login, and the owner's explicit consent
               # to agent queries on a login that can write (core.mongo_query)
               "auth_source", "allow_writable_login")


class DatabaseConnectionError(ValueError):
    pass


class _DefaultCrypto:
    """Adapter over the platform credential layer (lazy-imported)."""

    def encrypt(self, user_uuid: str, provider: str, creds: Dict[str, Any]) -> None:
        from trusted_data_agent.auth.encryption import encrypt_credentials
        encrypt_credentials(user_uuid, provider, creds)

    def decrypt(self, user_uuid: str, provider: str) -> Dict[str, Any]:
        from trusted_data_agent.auth.encryption import decrypt_credentials
        return decrypt_credentials(user_uuid, provider) or {}

    def delete(self, user_uuid: str, provider: str) -> None:
        from trusted_data_agent.auth.encryption import delete_credentials
        try:
            delete_credentials(user_uuid, provider)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"delete_credentials({provider}) failed: {e}")

    def list_providers(self, user_uuid: str) -> List[str]:
        from trusted_data_agent.auth.encryption import list_user_providers
        return list_user_providers(user_uuid) or []


class DatabaseConnectionStore:
    def __init__(self, config_manager=None, crypto=None):
        self._cm = config_manager
        self._crypto = crypto or _DefaultCrypto()

    # -- internals -----------------------------------------------------------

    @property
    def cm(self):
        if self._cm is None:
            from trusted_data_agent.core.config_manager import get_config_manager
            self._cm = get_config_manager()
        return self._cm

    @staticmethod
    def _provider(conn_id: str) -> str:
        return conn_id if conn_id.startswith((PROVIDER_PREFIX, LEGACY_PREFIX)) else f"{PROVIDER_PREFIX}{conn_id}"

    @staticmethod
    def _mask(creds: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(creds)
        for f in SECRET_FIELDS:
            if out.get(f):
                out[f] = MASK
        return out

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _split(self, payload: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Split a flat payload into (metadata, secrets)."""
        meta = {k: v for k, v in payload.items() if k in META_FIELDS}
        # a masked value is what a form was SHOWN, never a new secret — saving it back
        # unchanged must keep the stored secret, not overwrite it with the mask
        secrets = {k: v for k, v in payload.items() if k in SECRET_FIELDS and v not in (None, "") and v != MASK}
        return meta, secrets

    # -- validation ----------------------------------------------------------

    def _validate(self, driver: str) -> None:
        if (driver or "").strip().lower() not in SUPPORTED_DRIVERS:
            raise DatabaseConnectionError(
                f"Unsupported driver '{driver}'. Supported: {list(SUPPORTED_DRIVERS)}"
            )

    # -- read ----------------------------------------------------------------

    def list(self, user_uuid: str, *, include_legacy: bool = True) -> List[Dict[str, Any]]:
        """Return all connections (metadata + masked secrets), newest first style."""
        out: List[Dict[str, Any]] = []
        migrated_from = set()
        for rec in self.cm.get_database_connections(user_uuid):
            cid = rec.get("id")
            if not cid:
                continue
            if rec.get("migrated_from"):
                migrated_from.add(rec["migrated_from"])
            secrets = self._crypto.decrypt(user_uuid, self._provider(cid))
            merged = {**rec, **self._mask(secrets),
                      "has_password": bool(secrets.get("password") or secrets.get("pat_token")),
                      "source": "central"}
            out.append(merged)

        if include_legacy:
            for provider in self._crypto.list_providers(user_uuid):
                if not provider.startswith(LEGACY_PREFIX) or provider in migrated_from:
                    continue
                creds = self._crypto.decrypt(user_uuid, provider)
                if not creds:
                    continue
                out.append({
                    "id": provider,  # full legacy provider as id
                    "name": creds.get("name", provider[len(LEGACY_PREFIX):]),
                    "driver": creds.get("driver", "unknown"),
                    "host": creds.get("host"),
                    "database": creds.get("database"),
                    "username": creds.get("username") or creds.get("user"),
                    **self._mask({k: creds.get(k) for k in SECRET_FIELDS if creds.get(k)}),
                    "has_password": bool(creds.get("password") or creds.get("pat_token")),
                    "source": "canvas_legacy",
                    "migratable": True,
                })
        return out

    def get(self, user_uuid: str, conn_id: str) -> Optional[Dict[str, Any]]:
        for rec in self.list(user_uuid):
            if rec["id"] == conn_id:
                return rec
        return None

    def resolve(self, user_uuid: str, conn_id: str) -> Optional[Dict[str, Any]]:
        """Return full creds (metadata + secrets) for actual use, or None.

        Resolves any of the id forms every participant may hold:
          * a central id (``dbconn_…``)
          * a fully-qualified legacy provider (``canvas_conn_…``)
          * a bare Canvas connection id (mapped to ``canvas_conn_{id}``)
        """
        if conn_id.startswith(LEGACY_PREFIX):
            creds = self._crypto.decrypt(user_uuid, conn_id)
            return dict(creds) if creds else None
        rec = next((c for c in self.cm.get_database_connections(user_uuid) if c.get("id") == conn_id), None)
        if rec:
            secrets = self._crypto.decrypt(user_uuid, self._provider(conn_id))
            return {**rec, **secrets}
        # Fallback: a bare Canvas connection id.
        legacy = self._crypto.decrypt(user_uuid, f"{LEGACY_PREFIX}{conn_id}")
        return dict(legacy) if legacy else None

    # -- write ---------------------------------------------------------------

    def create(self, user_uuid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        driver = (payload.get("driver") or "").strip().lower()
        self._validate(driver)
        if not (payload.get("name") or "").strip():
            raise DatabaseConnectionError("name is required")
        conn_id = f"{PROVIDER_PREFIX}{uuid.uuid4().hex[:12]}"
        meta, secrets = self._split(payload)
        meta.update({"id": conn_id, "driver": driver, "created_at": self._now(),
                     "updated_at": self._now()})
        if secrets:
            self._crypto.encrypt(user_uuid, conn_id, secrets)
        self.cm.add_database_connection(meta, user_uuid)
        return {**meta, **self._mask(secrets), "has_password": bool(secrets.get("password") or secrets.get("pat_token")),
                "source": "central"}

    def update(self, user_uuid: str, conn_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        meta, secrets = self._split(payload)
        meta["updated_at"] = self._now()
        if not self.cm.update_database_connection(conn_id, meta, user_uuid):
            return None
        if secrets:
            existing = self._crypto.decrypt(user_uuid, self._provider(conn_id))
            existing.update(secrets)
            self._crypto.encrypt(user_uuid, self._provider(conn_id), existing)
        return self.get(user_uuid, conn_id)

    def legacy_provider(self, user_uuid: str, conn_id: str) -> Optional[str]:
        """The legacy ``canvas_conn_*`` provider an id names, if one exists — a
        fully-qualified legacy id, or a bare Canvas id that is not a central record."""
        cid = str(conn_id or "")
        if cid.startswith(PROVIDER_PREFIX):
            return None
        provider = cid if cid.startswith(LEGACY_PREFIX) else f"{LEGACY_PREFIX}{cid}"
        if any(c.get("id") == cid for c in self.cm.get_database_connections(user_uuid)):
            return None
        return provider if self._crypto.decrypt(user_uuid, provider) else None

    def save(self, user_uuid: str, conn_id: Optional[str], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update by any id form every participant may hold.

        * no id → a new central record
        * a central id → update in place
        * a legacy Canvas id → PROMOTED: a central record carrying the legacy creds
          merged with the change (``migrated_from`` hides the legacy one), so editing
          an old connection moves it into the one store rather than writing the old one
        """
        if not conn_id:
            return self.create(user_uuid, payload)
        legacy = self.legacy_provider(user_uuid, conn_id)
        if legacy:
            base = self._crypto.decrypt(user_uuid, legacy)
            if base.get("user") and not base.get("username"):
                base["username"] = base["user"]
            merged = {**{k: v for k, v in base.items() if v not in (None, "")},
                      **{k: v for k, v in payload.items() if v not in (None, "") and v != MASK}}
            rec = self.create(user_uuid, merged)
            self.cm.update_database_connection(rec["id"], {"migrated_from": legacy}, user_uuid)
            return {**rec, "migrated_from": legacy}
        rec = self.update(user_uuid, conn_id, payload)
        if rec is None:
            raise DatabaseConnectionError(f"connection '{conn_id}' not found")
        return rec

    def delete(self, user_uuid: str, conn_id: str) -> bool:
        """Delete by any id form. A central record that was migrated from a legacy one
        takes its legacy provider with it — otherwise the old connection would
        resurface in every list the moment its replacement is gone."""
        legacy = self.legacy_provider(user_uuid, conn_id)
        if legacy:
            self._crypto.delete(user_uuid, legacy)
            return True
        rec = next((c for c in self.cm.get_database_connections(user_uuid) if c.get("id") == conn_id), None)
        ok = self.cm.remove_database_connection(conn_id, user_uuid)
        if ok:
            self._crypto.delete(user_uuid, self._provider(conn_id))
            if rec and rec.get("migrated_from"):
                self._crypto.delete(user_uuid, rec["migrated_from"])
        return ok

    def migrate_legacy(self, user_uuid: str) -> int:
        """Promote legacy ``canvas_conn_*`` connections into the central store.

        Non-destructive: the legacy provider is left in place; a central record is
        created with ``migrated_from`` set so it is not surfaced/duplicated again.
        """
        existing = {c.get("migrated_from") for c in self.cm.get_database_connections(user_uuid)}
        count = 0
        for provider in self._crypto.list_providers(user_uuid):
            if not provider.startswith(LEGACY_PREFIX) or provider in existing:
                continue
            creds = self._crypto.decrypt(user_uuid, provider)
            if not creds:
                continue
            payload = {
                "name": creds.get("name", provider[len(LEGACY_PREFIX):]),
                "driver": (creds.get("driver") or "teradata"),
                "host": creds.get("host"), "database": creds.get("database"),
                "username": creds.get("username") or creds.get("user"),
            }
            for f in SECRET_FIELDS:
                if creds.get(f):
                    payload[f] = creds[f]
            try:
                rec = self.create(user_uuid, payload)
                self.cm.update_database_connection(rec["id"], {"migrated_from": provider}, user_uuid)
                count += 1
            except DatabaseConnectionError as e:
                logger.warning(f"legacy migrate skipped {provider}: {e}")
        return count


_store: Optional[DatabaseConnectionStore] = None


def get_database_connection_store() -> DatabaseConnectionStore:
    global _store
    if _store is None:
        _store = DatabaseConnectionStore()
    return _store
