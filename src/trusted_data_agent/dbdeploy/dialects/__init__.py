"""
Dialect emitters — pure functions from a :class:`PhysicalModel` to a target
backend's DDL + load plan + KG physical delta.

Each emitter is dialect-aware but I/O-free.  Resolved by name in
``converter.resolve_dialect``.
"""

from .base import DialectEmitter  # noqa: F401
from .teradata import TeradataDialect  # noqa: F401

__all__ = ["DialectEmitter", "TeradataDialect"]
