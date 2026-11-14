"""Session backend selection (singleton).

Selected by env:
  - ``TDA_SESSION_BACKEND`` = ``file`` (default) | ``postgres``
  - ``TDA_SESSIONS_DIR``    = override the file backend's root (tests / custom)

Single-user installs need set nothing — the file backend is the zero-config
default. ``reset_session_backend`` lets tests repoint storage.
"""
import os
import logging
from pathlib import Path

from trusted_data_agent.core.session_backends.base import SessionPersistenceBackend
from trusted_data_agent.core.session_backends.file_backend import (
    FileSessionBackend,
    DEFAULT_SESSIONS_DIR,
)

logger = logging.getLogger("quart.app")

_BACKEND: SessionPersistenceBackend | None = None


def _build_backend() -> SessionPersistenceBackend:
    backend_type = (os.environ.get("TDA_SESSION_BACKEND") or "file").strip().lower()
    sessions_dir = os.environ.get("TDA_SESSIONS_DIR")
    sd = Path(sessions_dir) if sessions_dir else DEFAULT_SESSIONS_DIR

    if backend_type in ("file", "filesystem", "local", ""):
        return FileSessionBackend(sd)
    if backend_type in ("postgres", "postgresql", "pg"):
        try:
            from trusted_data_agent.core.session_backends.postgres_backend import (
                PostgresSessionBackend,
            )
            return PostgresSessionBackend()
        except Exception as e:
            logger.error(
                f"Postgres session backend unavailable ({e}); falling back to file backend")
            return FileSessionBackend(sd)
    logger.warning(f"Unknown TDA_SESSION_BACKEND='{backend_type}', using file backend")
    return FileSessionBackend(sd)


def get_session_backend() -> SessionPersistenceBackend:
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _build_backend()
    return _BACKEND


def reset_session_backend(backend: SessionPersistenceBackend | None = None) -> None:
    """Replace the cached backend. With ``backend=None`` the next
    ``get_session_backend()`` rebuilds from the current environment."""
    global _BACKEND
    _BACKEND = backend


async def close_session_backend() -> None:
    """Release the active backend's resources (e.g. the Postgres pool) on shutdown."""
    global _BACKEND
    if _BACKEND is not None and hasattr(_BACKEND, "close"):
        try:
            await _BACKEND.close()
        except Exception:
            pass
