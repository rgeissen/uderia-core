"""Redis lock provider — cross-process/host coordination (drop-in for multi-server).

Implements a standard single-instance Redis lock: ``SET key token NX PX ttl`` to
acquire (spin with a short poll until it succeeds), and a Lua compare-and-delete
to release only our own token. Selected via ``TDA_LOCK_BACKEND=redis``; the
``redis`` package is an optional dependency (import guarded).
"""
import asyncio
import uuid
import logging

from trusted_data_agent.core.locks.base import LockProvider

logger = logging.getLogger("quart.app")

try:
    import redis.asyncio as aioredis  # type: ignore
    _HAS_REDIS = True
except Exception:  # pragma: no cover - exercised only when redis is absent
    _HAS_REDIS = False

# Release only if the value still equals our token (avoid releasing someone else's
# lock after a TTL-expiry hand-off).
_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class _RedisLock:
    def __init__(self, client, key: str, ttl_ms: int, poll_s: float):
        self._client = client
        self._key = key
        self._ttl_ms = ttl_ms
        self._poll_s = poll_s
        self._token: str | None = None

    async def __aenter__(self):
        token = uuid.uuid4().hex
        while True:
            ok = await self._client.set(self._key, token, nx=True, px=self._ttl_ms)
            if ok:
                self._token = token
                return self
            await asyncio.sleep(self._poll_s)

    async def __aexit__(self, exc_type, exc, tb):
        if self._token is not None:
            try:
                await self._client.eval(_RELEASE_LUA, 1, self._key, self._token)
            except Exception as e:  # pragma: no cover
                logger.warning(f"RedisLock release failed for {self._key}: {e}")
            finally:
                self._token = None
        return False


class RedisLockProvider(LockProvider):
    def __init__(self, url: str, ttl_ms: int = 30000, poll_s: float = 0.05,
                 prefix: str = "tda:lock:"):
        if not _HAS_REDIS:
            raise RuntimeError("redis package not installed (pip install redis)")
        self._client = aioredis.from_url(url, decode_responses=True)
        self._ttl_ms = ttl_ms
        self._poll_s = poll_s
        self._prefix = prefix

    def get_lock(self, key: str) -> _RedisLock:
        return _RedisLock(self._client, self._prefix + key, self._ttl_ms, self._poll_s)

    async def close(self):
        try:
            await self._client.aclose()
        except Exception:
            pass
