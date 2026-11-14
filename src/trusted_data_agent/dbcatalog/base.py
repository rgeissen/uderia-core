"""The catalog contract. A provider answers each question it CAN answer and declares
the rest in ``capabilities()``; the navigator never asks for what a provider lacks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Set

from .capabilities import CatalogCapability
from .types import DatabaseInfo, ObjectDetail, ObjectInfo, PreviewResult


class CatalogError(RuntimeError):
    """A provider could not answer. The message is user-facing and names the remedy
    where one exists (recovery-shaped, never a bare traceback)."""


class CatalogProvider(ABC):
    provider_kind: str = "direct"

    @property
    @abstractmethod
    def source_id(self) -> str: ...

    @property
    @abstractmethod
    def label(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> Set[CatalogCapability]: ...

    def has(self, cap: CatalogCapability) -> bool:
        return cap in self.capabilities()

    @abstractmethod
    async def list_databases(self) -> List[DatabaseInfo]: ...

    @abstractmethod
    async def list_objects(self, database: str) -> List[ObjectInfo]: ...

    @abstractmethod
    async def describe_object(self, database: str, name: str) -> ObjectDetail: ...

    async def preview(self, database: str, name: str, limit: int = 50) -> PreviewResult:
        raise CatalogError("This source cannot preview rows.")

    async def row_count(self, database: str, name: str) -> Optional[int]:
        return None

    async def run_select(self, sql: str, max_rows: int):
        """Run ONE read-only statement written by an agent → ``(columns, rows)``, at most
        ``max_rows`` rows. Only a direct connection can: an MCP source has its own SQL
        tool, with the MCP path's protections."""
        raise CatalogError(f"{self.label} cannot run a query here — it is reached through MCP tools; "
                           f"use that server's own SQL tool instead")

    async def existing_functions(self, names) -> Optional[set]:
        """Which of ``names`` (upper-case function / table-operator names) exist on this source
        → a set, or None when the source cannot tell. Default: cannot tell."""
        return None

    async def search_objects(self, term: str, limit: int = 200) -> List[ObjectInfo]:
        """Dictionary-wide object-name search (SEARCH capability); default: none."""
        return []

    async def search_columns(self, term: str, limit: int = 300) -> List[dict]:
        """Dictionary-wide column-name search → ``[{database, object, column, data_type}]``."""
        return []

    async def test(self) -> bool:
        """Cheap liveness check — a provider that cannot even list databases is
        reported as unreachable rather than as an empty estate."""
        try:
            await self.list_databases()
            return True
        except Exception:  # noqa: BLE001
            return False
