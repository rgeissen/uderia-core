"""Pluggable lock providers.

A ``LockProvider`` hands out an async context manager that guards a key:

    async with get_lock_provider().get_lock(session_id):
        ...  # critical section

The default ``InProcessLockProvider`` (per-key ``asyncio.Lock``) coordinates
within one process. ``RedisLockProvider`` is a drop-in for multi-server
deployments, selected via ``TDA_LOCK_BACKEND=redis``.
"""
from trusted_data_agent.core.locks.base import LockProvider
from trusted_data_agent.core.locks.factory import (
    get_lock_provider,
    reset_lock_provider,
)

__all__ = ["LockProvider", "get_lock_provider", "reset_lock_provider"]
