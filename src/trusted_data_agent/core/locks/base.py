"""Abstract lock provider — the coordination seam for session_manager et al."""
from abc import ABC, abstractmethod


class LockProvider(ABC):
    @abstractmethod
    def get_lock(self, key: str):
        """Return an async context manager that serializes access to ``key``.

        Usage:
            async with provider.get_lock(key):
                ...  # critical section

        In-process backends return a per-key ``asyncio.Lock`` (mutual exclusion
        within the process); distributed backends return a lock that coordinates
        across processes/hosts.
        """
