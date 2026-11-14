"""Open a database connection from a Uderia database connection id.

Shared by the constructors that read a deployed model or product straight from the
warehouse (declared model, ANDP). Credentials are resolved through the platform's own
encrypted connection store — they are never accepted as literals in constructor options.
"""
from __future__ import annotations

from typing import Any, Dict

from .declared_model_source import SourceError


def open_uderia_connection(opts: Dict[str, Any]):
    conn_id = (opts.get("connection_id") or "").strip()
    user_uuid = (opts.get("user_uuid") or "").strip()
    if not conn_id:
        raise SourceError("connection_id is required to read a deployed product")
    if not user_uuid:
        raise SourceError("user_uuid is required to resolve the connection")

    from trusted_data_agent.core.database_connections import get_database_connection_store

    resolved = get_database_connection_store().resolve(user_uuid, conn_id)
    if not resolved:
        raise SourceError(f"database connection '{conn_id}' not found")

    try:
        import teradatasql
    except ImportError as exc:  # pragma: no cover
        raise SourceError("teradatasql is required to read a Teradata product") from exc

    kwargs: Dict[str, Any] = {
        "host": resolved.get("host"),
        "user": resolved.get("username"),
        "password": resolved.get("password"),
    }
    if resolved.get("logmech"):
        kwargs["logmech"] = resolved["logmech"]
    if resolved.get("port"):
        kwargs["dbs_port"] = str(resolved["port"])
    return teradatasql.connect(**kwargs)
