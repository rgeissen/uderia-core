"""In-process lock provider — per-key ``asyncio.Lock`` (default backend).

Replaces the old module-level ``_session_locks`` dict: de-races the
check-then-create with ``setdefault`` and bounds growth with an LRU that only
evicts locks that are not currently held (a held lock can never be evicted, so
mutual exclusion is preserved).
"""
import asyncio
import logging
from collections import OrderedDict

from trusted_data_agent.core.locks.base import LockProvider

logger = logging.getLogger("quart.app")

DEFAULT_MAX_LOCKS = 4096


class InProcessLockProvider(LockProvider):
    def __init__(self, max_locks: int = DEFAULT_MAX_LOCKS):
        self._locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()
        self._max = max_locks

    def get_lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            # setdefault is atomic w.r.t. the single-threaded event loop (no await
            # between the get above and here), so concurrent callers share one lock.
            lock = self._locks.setdefault(key, asyncio.Lock())
        else:
            self._locks.move_to_end(key)  # mark most-recently-used
        self._evict_if_needed()
        return lock

    def _evict_if_needed(self) -> None:
        # Evict least-recently-used locks that are NOT currently held. A held lock
        # is skipped (evicting it would let a second caller create a fresh lock for
        # the same key and break mutual exclusion).
        while len(self._locks) > self._max:
            evicted = False
            for k in list(self._locks.keys()):  # oldest first
                if not self._locks[k].locked():
                    del self._locks[k]
                    evicted = True
                    break
            if not evicted:
                break  # everything currently held; stop trying

    # Introspection (tests / diagnostics)
    def size(self) -> int:
        return len(self._locks)
