"""
Dialect seams — what every target backend's emitter provides.

Two abstractions, both pure (no I/O, deterministic):

  * :class:`DialectEmitter` turns a :class:`physical_model.PhysicalModel` into a
    :class:`types.ConversionResult` (DDL plan + load plan + KG physical delta) — the
    walked-model path.
  * :class:`DeltaDialect` renders the statements of an AUTHORED-layer delta
    (``delta.plan_delta``) and reads them back (``readback``) — the OBDA-MODEL path
    (PROP-008 A3/A4/C3).  The planner decides WHAT lands (a table, a column, a
    reference, a drop, statistics); the dialect decides how the target SAYS it.

This module names no vendor and carries no vendor's vocabulary (gate-asserted): a
backend's table options, index model, type names, statistics and constraint words live
in its own dialect module, its rules in its own rule set.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..physical_model import PhysicalModel
from ..types import ConversionResult


class DialectEmitter(ABC):
    #: Backend dialect name.
    name: str = ""

    @abstractmethod
    def convert(
        self,
        model: PhysicalModel,
        *,
        logical_db_name: str,
        database_parent: str = "",
        database_perm_bytes: int | None = None,
        root_database: str | None = None,
        include_metric_views: bool = False,
        comments: str = "none",
        pi_overrides: dict | None = None,
        column_refinements: dict | None = None,
    ) -> ConversionResult:
        """Emit DDL + load plan + KG physical delta for ``model``.

        ``pi_overrides`` (``{"db.table": {"columns": [...], "unique": bool}}``) and
        ``column_refinements`` (``{"db.table.column": {"not_null": bool|None, "charset": str|None}}``)
        carry a MEASURED decision (``probes.py``) into the emission; absent, the declared
        standard applies unchanged.  Both are dialect-neutral facts; what they emit is the
        dialect's business.  ``comments`` is ``none`` · ``table`` · ``all``.

        Args:
            model: the dialect-neutral physical model.
            logical_db_name: name of the KG's logical ``database`` root node, so
                the physical layer's ``implements`` edges point at the right
                logical nodes (must match the walker's ``db_name``).
            database_parent: the parent a created database is carved from, where the
                backend has one.
            database_perm_bytes: space per created database (None = standard default).
        """
        raise NotImplementedError


# ── the authored-layer delta (PROP-008 C3) ───────────────────────────────────

def quote(ident: str) -> str:
    """A delimited identifier (the standard SQL form) — the model's case is kept."""
    return '"' + str(ident).replace('"', '""') + '"'


def qualified(*parts: str) -> str:
    return ".".join(quote(p) for p in parts)


def literal(text: str) -> str:
    return str(text).replace("'", "''")


@dataclass(frozen=True)
class ColumnSpec:
    """One column of a new table, as the planner decided it."""
    name: str
    type: str
    not_null: bool = False


class DeltaDialect(ABC):
    """How one backend SAYS an authored-layer delta, and how its own statements read back.

    The planner (``delta.plan_delta``) calls these with decisions already made — the key,
    the parent reference, which columns are new — and never writes a statement itself."""

    #: the backend's name; it is also the ``infrastructure`` a landed physical node carries
    name: str = ""
    #: how a reference is said in the deploy preview (in words, plural form built by the caller)
    reference_noun: str = "reference"
    #: does the backend place rows by a key the delta chooses (so a key choice is a
    #: distribution decision the probes make)?
    key_places_rows: bool = False

    @property
    @abstractmethod
    def standard_version(self) -> str:
        """The design standard's version this dialect emits under (stamped on plan + receipt)."""

    @abstractmethod
    def comments_enabled(self) -> bool: ...

    @abstractmethod
    def statistics_enabled(self) -> bool: ...

    @abstractmethod
    def column_type(self, props: Dict[str, Any], warnings: List[str], label: str) -> str:
        """The column type for an authored attribute's declared ``data_type``."""

    @abstractmethod
    def create_table(self, db: str, table: str, columns: Sequence[ColumnSpec], key_columns: Sequence[str],
                     key_rule: str, warnings: List[str], node: str) -> str:
        """``CREATE TABLE`` for a new authored entity.  ``key_columns`` is the key the
        planner chose (empty = none), ``key_rule`` why."""

    @abstractmethod
    def comment_table(self, db: str, table: str, description: str) -> Optional[str]:
        """The table comment, or ``None`` when the standard emits none."""

    @abstractmethod
    def add_column(self, db: str, table: str, column: str, ctype: str) -> str: ...

    @abstractmethod
    def foreign_key(self, db: str, table: str, column: str, parent_db: str, parent_table: str,
                    parent_column: str) -> str: ...

    def reference_support(self, db: str, table: str, column: str) -> List[str]:
        """Statements that make a reference cheap to use (none by default)."""
        return []

    @abstractmethod
    def statistics(self, db: str, table: str, column_sets: Sequence[Sequence[str]]) -> List[str]:
        """Statistics for a table the delta created: on the key and every reference column."""

    @abstractmethod
    def drop_table(self, db: str, table: str) -> str: ...

    @abstractmethod
    def drop_column(self, db: str, table: str, column: str) -> str: ...

    def default_key_choice(self, table: str, key_cols: List[str], columns: List[str]) -> Tuple[List[str], str]:
        """The declared key, as declared — a backend whose key places rows replaces this with
        the probed choice."""
        return (list(key_cols), "declared_key") if key_cols else ([], "no_key")

    @abstractmethod
    def keyless_finding(self) -> Tuple[str, str]:
        """``(rule_id, message)`` the design report names for a planned table with no key."""

    # ── reading its own statements back (readback.py) ──
    @abstractmethod
    def planned_from_sql(self, create_sql: str, *, key_columns: Sequence[str] = (), fk_sql: Iterable[str] = (),
                         statistics_sql: Iterable[str] = (), comment_sql: Optional[str] = None,
                         support_sql: Iterable[str] = ()):
        """The platform's own CREATE (and its sibling statements) as a ``PlannedTable``."""

    @abstractmethod
    def rule_set(self):
        """This backend's design rules (``design_standard.RuleSet``)."""

    @abstractmethod
    def readback_check(self, rule, planned, observed: Dict[str, Any], skew: Optional[Dict[str, Any]],
                       threshold_pct: float) -> Tuple[str, str]:
        """``(held | violated | unverified, detail)`` for one rule on one table."""
