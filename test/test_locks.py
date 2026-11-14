"""Module tests for the R1.3 lock providers (src/trusted_data_agent/core/locks/).

Exercises the in-process provider's identity, mutual exclusion (no lost
updates), and bounded LRU eviction (never evicts a held lock). The Redis
provider is exercised only when TDA_TEST_REDIS_URL is set (skipped otherwise).

Run:
    PYTHONPATH=src python test/test_locks.py
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.locks import get_lock_provider, reset_lock_provider  # noqa: E402
from trusted_data_agent.core.locks.inprocess import InProcessLockProvider  # noqa: E402
from trusted_data_agent.core.locks.factory import _build_provider  # noqa: E402

_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def run(coro):
    return _loop.run_until_complete(coro)


_passed = _failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")


# ── default selection ─────────────────────────────────────────────────────────
def t_factory_default():
    os.environ.pop("TDA_LOCK_BACKEND", None)
    reset_lock_provider(None)
    p = get_lock_provider()
    check("default provider is in-process", isinstance(p, InProcessLockProvider))


# ── identity ──────────────────────────────────────────────────────────────────
def t_identity():
    p = InProcessLockProvider()
    check("same key -> same lock", p.get_lock("k") is p.get_lock("k"))
    check("different keys -> different locks", p.get_lock("a") is not p.get_lock("b"))


# ── mutual exclusion / no lost updates ────────────────────────────────────────
async def _bump(p, key, counter, times):
    for _ in range(times):
        async with p.get_lock(key):
            v = counter["v"]
            await asyncio.sleep(0)        # yield: invites interleaving if unlocked
            counter["v"] = v + 1


def t_mutual_exclusion():
    p = InProcessLockProvider()
    counter = {"v": 0}
    run(asyncio.gather(*[_bump(p, "shared", counter, 25) for _ in range(8)]))
    check("no lost updates under contention (== 200)", counter["v"] == 200)


def t_independent_keys_progress():
    # Distinct keys must not block each other: hold one key, another acquires freely.
    p = InProcessLockProvider()

    async def go():
        async with p.get_lock("held"):
            # A different key must be acquirable while 'held' is locked.
            acquired = False
            async with p.get_lock("other"):
                acquired = True
            return acquired

    check("independent keys don't block each other", run(go()) is True)


# ── bounded eviction ──────────────────────────────────────────────────────────
def t_eviction_bounded():
    p = InProcessLockProvider(max_locks=8)
    for i in range(50):
        p.get_lock(f"k{i}")          # not held
    check("unheld locks evicted to bound (size <= 8)", p.size() <= 8)


def t_eviction_preserves_held():
    p = InProcessLockProvider(max_locks=4)

    async def go():
        async with p.get_lock("HELD"):
            # Flood with new keys to force eviction pressure.
            for i in range(50):
                p.get_lock(f"flood{i}")
            # The held lock must NOT have been evicted+recreated: same object, still held.
            return p.get_lock("HELD").locked()

    check("held lock survives eviction pressure", run(go()) is True)


# ── redis (gated) ─────────────────────────────────────────────────────────────
def t_redis_optional():
    url = os.environ.get("TDA_TEST_REDIS_URL")
    if not url:
        print("  SKIP  redis provider (set TDA_TEST_REDIS_URL to run)")
        return
    from trusted_data_agent.core.locks.redis_backend import RedisLockProvider
    p = RedisLockProvider(url)
    counter = {"v": 0}

    async def go():
        await asyncio.gather(*[_bump(p, "rkey", counter, 25) for _ in range(8)])
        await p.close()

    run(go())
    check("redis: no lost updates under contention (== 200)", counter["v"] == 200)


def main():
    print("== lock provider tests ==")
    for t in (t_factory_default, t_identity, t_mutual_exclusion,
              t_independent_keys_progress, t_eviction_bounded,
              t_eviction_preserves_held, t_redis_optional):
        print(f"\n[{t.__name__}]")
        t()
    print(f"\n== {_passed} passed, {_failed} failed ==")
    return _failed


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
