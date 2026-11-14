"""Lock provider selection (singleton).

Selected by env:
  - ``TDA_LOCK_BACKEND`` = ``inprocess`` (default) | ``redis``
  - ``TDA_REDIS_URL``    = redis connection url (when backend=redis)

Single-server installs need set nothing — in-process locking is the default and
adds no dependency. ``reset_lock_provider`` lets tests swap the provider.
"""
import os
import logging

from trusted_data_agent.core.locks.base import LockProvider
from trusted_data_agent.core.locks.inprocess import InProcessLockProvider

logger = logging.getLogger("quart.app")

_PROVIDER: LockProvider | None = None


def _build_provider() -> LockProvider:
    backend = (os.environ.get("TDA_LOCK_BACKEND") or "inprocess").strip().lower()
    if backend in ("inprocess", "memory", "local", ""):
        return InProcessLockProvider()
    if backend in ("redis",):
        try:
            from trusted_data_agent.core.locks.redis_backend import RedisLockProvider
            url = os.environ.get("TDA_REDIS_URL", "redis://localhost:6379/0")
            return RedisLockProvider(url)
        except Exception as e:
            logger.error(f"Redis lock provider unavailable ({e}); falling back to in-process")
            return InProcessLockProvider()
    logger.warning(f"Unknown TDA_LOCK_BACKEND='{backend}', using in-process")
    return InProcessLockProvider()


def get_lock_provider() -> LockProvider:
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = _build_provider()
    return _PROVIDER


def reset_lock_provider(provider: LockProvider | None = None) -> None:
    """Replace the cached provider. With ``provider=None`` the next
    ``get_lock_provider()`` rebuilds from the current environment."""
    global _PROVIDER
    _PROVIDER = provider
