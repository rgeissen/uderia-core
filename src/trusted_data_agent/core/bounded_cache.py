"""BoundedLRUCache — a dict-compatible, size-bounded LRU cache.

A drop-in replacement for a plain ``dict`` used as an in-memory cache/pool, so
caches that otherwise grow without limit (e.g. per-user LLM clients, per-
collection handles) stay bounded under load. Eviction removes the
least-recently-used entry; an optional ``on_evict(key, value)`` hook lets a
caller release the evicted resource.

Design note: the hook is intentionally NOT used for live network clients that a
caller may still be using (an in-flight request keeps its own local reference,
so dropping the cache entry never breaks it — Python reclaims the object once no
references remain). Only pass ``on_evict`` for resources that are safe to close
immediately on eviction.
"""
from collections import OrderedDict
from typing import Any, Callable, Optional

DEFAULT_MAX = 512


class BoundedLRUCache:
    def __init__(self, max_size: int = DEFAULT_MAX,
                 on_evict: Optional[Callable[[Any, Any], None]] = None,
                 name: str = ""):
        self._d: "OrderedDict[Any, Any]" = OrderedDict()
        self._max = max(1, int(max_size))
        self._on_evict = on_evict
        self.name = name
        self.evictions = 0

    # ── dict-compatible surface ──────────────────────────────────────────────
    def __contains__(self, key) -> bool:
        return key in self._d

    def __len__(self) -> int:
        return len(self._d)

    def __iter__(self):
        return iter(self._d)

    def __getitem__(self, key):
        value = self._d[key]
        self._d.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self._d:
            self._d.move_to_end(key)
        self._d[key] = value
        self._enforce_bound()

    def __delitem__(self, key):
        del self._d[key]

    def get(self, key, default=None):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return default

    def setdefault(self, key, default=None):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        self._d[key] = default
        self._enforce_bound()
        return default

    def pop(self, key, *default):
        return self._d.pop(key, *default)

    def clear(self):
        self._d.clear()

    def keys(self):
        return self._d.keys()

    def values(self):
        return self._d.values()

    def items(self):
        return self._d.items()

    # ── eviction ─────────────────────────────────────────────────────────────
    def _enforce_bound(self):
        while len(self._d) > self._max:
            key, value = self._d.popitem(last=False)  # least-recently-used
            self.evictions += 1
            if self._on_evict is not None:
                try:
                    self._on_evict(key, value)
                except Exception:
                    pass

    @property
    def max_size(self) -> int:
        return self._max
