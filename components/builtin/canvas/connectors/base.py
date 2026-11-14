"""
Base class and data structures for canvas execution connectors.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionResult:
    """Result from executing code via a connector."""
    result: str | None = None
    error: str | None = None
    row_count: int = 0
    execution_time_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    # Structured result for programmatic callers (the navigator's runner). `result` stays the
    # display text the Canvas shows; parsing that text back into rows broke on any value
    # containing a tab or newline and turned every value into a string.
    columns: list | None = None
    rows: list | None = None
    truncated: bool = False


@dataclass
class ConnectionTestResult:
    """Result from testing a database connection."""
    valid: bool = False
    message: str = ''
    server_info: str | None = None


class BaseCanvasConnector(ABC):
    """Abstract base class for canvas execution connectors."""

    id: str = ''
    name: str = ''
    supported_drivers: list[str] = []

    @abstractmethod
    async def execute(self, code: str, credentials: dict, *, max_rows: int = 0,
                      readonly: bool = False) -> ExecutionResult:
        """Execute code with the given credentials."""
        ...

    @abstractmethod
    async def test_connection(self, credentials: dict) -> ConnectionTestResult:
        """Test whether credentials can establish a connection."""
        ...
