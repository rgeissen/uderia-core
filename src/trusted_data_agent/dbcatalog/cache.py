"""Catalog snapshot cache — per (user, source), TTL-bounded, explicitly refreshable.

Browsing a 1,500-column schema must not re-read the dictionary on every click: the
database list, each database's object list and each object's detail are cached for
``TTL_SECONDS`` (30 min — dictionaries move slowly; a refresh button covers the
rest). Nothing here is shared across users: a cache key always starts with the
user's uuid, so one user's connection can never serve another user's tree."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

TTL_SECONDS = 1800
_MAX_ENTRIES = 5000


class CatalogCache:
    def __init__(self, ttl: int = TTL_SECONDS, clock=time.monotonic):
        self._ttl, self._clock = ttl, clock
        self._d: Dict[Tuple, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _key(self, user_uuid: str, source_id: str, *parts: str) -> Tuple:
        return (str(user_uuid), str(source_id)) + tuple(str(p).lower() for p in parts)

    def get(self, user_uuid: str, source_id: str, *parts: str) -> Optional[Any]:
        k = self._key(user_uuid, source_id, *parts)
        with self._lock:
            hit = self._d.get(k)
            if not hit:
                return None
            ts, val = hit
            if self._clock() - ts > self._ttl:
                self._d.pop(k, None)
                return None
            return val

    def put(self, user_uuid: str, source_id: str, *parts: str, value: Any) -> Any:
        k = self._key(user_uuid, source_id, *parts)
        with self._lock:
            if len(self._d) >= _MAX_ENTRIES:
                now = self._clock()
                for kk in [kk for kk, (ts, _v) in self._d.items() if now - ts > self._ttl]:
                    self._d.pop(kk, None)
                if len(self._d) >= _MAX_ENTRIES:
                    self._d.pop(next(iter(self._d)))
            self._d[k] = (self._clock(), value)
        return value

    def invalidate(self, user_uuid: str, source_id: Optional[str] = None) -> int:
        """Drop everything for a user (or one of their sources). Returns entries removed."""
        with self._lock:
            keys = [k for k in self._d if k[0] == str(user_uuid) and (source_id is None or k[1] == str(source_id))]
            for k in keys:
                self._d.pop(k, None)
        return len(keys)

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._d.clear()


CACHE = CatalogCache()
