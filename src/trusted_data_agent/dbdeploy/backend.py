"""
DeploymentBackend — capability-negotiated ABC for executing a DDL plan and
bulk-loading data against a live relational backend.

Dialect-agnostic: a backend executes whatever DDL/load-plan the converter hands
it.  Mirrors the vectorstore/kgstore backend ABCs.

Optional operations (``bulk_load``, ``drop_objects``) have default
implementations that raise :class:`DeploymentNotSupported`; a backend advertises
what it actually supports via :meth:`capabilities`, and the factory validates
:data:`REQUIRED_CAPABILITIES`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set, Tuple

from .capabilities import DeploymentCapability
from .types import DDLPlan, DeployResult, LoadPlan, LoadResult


class DeploymentError(RuntimeError):
    """Base error for deployment backends."""


class DeploymentNotSupported(DeploymentError):
    """Raised when an operation isn't supported by a backend."""


class DeploymentBackend(ABC):
    @property
    @abstractmethod
    def backend_type(self) -> str:
        ...

    @property
    def dialect(self) -> str:
        """The dialect that SAYS this backend's statements (``delta.resolve_delta_dialect``) —
        also the ``infrastructure`` a landed physical node carries.  Default: the backend type."""
        return self.backend_type

    @abstractmethod
    def capabilities(self) -> Set[DeploymentCapability]:
        ...

    @abstractmethod
    async def execute_ddl(
        self,
        plan: DDLPlan,
        *,
        dry_run: bool = False,
        stop_on_error: bool = True,
        include_statistics: bool = True,
        replace_existing: bool = False,
    ) -> DeployResult:
        """Execute (or, with ``dry_run``, merely plan) a DDL plan.

        ``dry_run`` MUST short-circuit before any I/O and return the generated
        SQL — this is the safe-preview path and is required of every backend.

        ``include_statistics=False`` defers the plan's ``COLLECT STATISTICS``
        statements so the caller can run them *after* the data load (collecting
        stats on empty tables is useless to the optimizer).  Run them later via
        :meth:`collect_statistics`.

        ``replace_existing=True`` makes the deploy **idempotent**: before
        creating, drop the plan's OWN views + tables (drop-and-recreate) so a
        re-deploy over existing objects succeeds instead of erroring. The drop is
        scoped strictly to the plan's identifiers — it never touches objects the
        deploy does not manage.
        """
        ...

    async def replace_views(self, plan: DDLPlan) -> DeployResult:
        """(Re)create ONLY the plan's views — the views-only repair path.

        A metric view is derived (compiled from the source model's expression), so
        a compiler fix should land WITHOUT rebuilding tables or reloading data.
        The contract: every ``view`` statement in ``plan`` is created or replaced;
        a view the target refuses is SKIPPED and named in ``skipped_reasons``
        (never an error — one refused expression must not stop the rest); nothing
        but views is touched.  Backends with an atomic create-or-replace form
        (Teradata ``REPLACE VIEW``) override this so a refused definition leaves
        the existing view standing; the generic form is drop-then-create.
        """
        views = DDLPlan()
        for st in plan.by_kind("view"):
            views.add(st.kind, st.identifier, st.sql, st.phase)
        return await self.execute_ddl(views, include_statistics=False,
                                      stop_on_error=False, replace_existing=True)

    async def collect_statistics(
        self,
        plan: DDLPlan,
        *,
        only_identifiers: Optional[Set[str]] = None,
    ) -> DeployResult:
        """Run the plan's ``COLLECT STATISTICS`` statements (post-load phase).

        ``only_identifiers`` restricts collection to the given ``db.table``
        identifiers (e.g. only tables that loaded successfully).  Default
        implementation is a no-op for backends that don't collect statistics.
        """
        return DeployResult(success=True)

    def preview_create_database(self, name: str, *, parent: Optional[str] = None,
                                perm_bytes: Optional[int] = None) -> str:
        """Return the exact DDL ``ensure_database`` would run — for dry-run preview.

        Pure (no I/O).  Requires the ``CREATE_DATABASE`` capability.
        """
        raise DeploymentNotSupported(f"{self.backend_type} cannot preview database creation")

    async def database_exists(self, name: str) -> bool:
        """Return True if the database/namespace ``name`` already exists.

        Requires the ``CREATE_DATABASE`` capability (the same metadata access).
        """
        raise DeploymentNotSupported(f"{self.backend_type} cannot check database existence")

    async def ensure_database(
        self,
        name: str,
        *,
        parent: Optional[str] = None,
        perm_bytes: Optional[int] = None,
    ) -> bool:
        """Idempotently provision database ``name`` — create it iff absent.

        Returns True if it was created, False if it already existed.  Requires the
        ``CREATE_DATABASE`` capability.  This is the single, abstraction-level home
        for "create the database if it doesn't exist" — callers never write raw
        ``CREATE DATABASE`` DDL.
        """
        raise DeploymentNotSupported(f"{self.backend_type} does not support ensure_database")

    async def list_database_objects(self, name: str) -> List[Tuple[str, str]]:
        """Every object in database ``name`` as ``(kind, object_name)`` — kind is
        ``table`` | ``view`` | other.  Requires the ``CREATE_DATABASE`` capability
        (the same metadata access).  What :meth:`drop_database` refuses over."""
        raise DeploymentNotSupported(f"{self.backend_type} cannot list database objects")

    async def drop_database(self, name: str, *, owned: Optional[Set[str]] = None) -> DeployResult:
        """Drop database ``name`` with everything in it — but only if everything in it
        is OURS.

        ``owned`` is the set of object names a deploy plan created there (tables +
        views).  An object the plan does not own — anything a user created by hand
        in a model database — makes the drop refuse, naming it, so a rename or a
        re-deploy can never take a stranger's object down with the old database.
        ``owned=None`` skips the check (the caller vouches).  Requires the
        ``CREATE_DATABASE`` capability.
        """
        raise DeploymentNotSupported(f"{self.backend_type} does not support drop_database")

    async def clear_tables(self, identifiers: List[str]) -> DeployResult:
        """Empty the given ``db.table`` objects (rows only — the DDL stays).

        The data-only path: regenerate the demo rows of a deployed model without
        touching its tables, keys or the graph's physical layer.  Default: not
        supported (a backend opts in).
        """
        raise NotImplementedError(f"{self.backend_type} cannot clear tables")

    async def bulk_load(self, load_plan: LoadPlan) -> LoadResult:
        raise DeploymentNotSupported(f"{self.backend_type} does not support bulk_load")

    async def drop_objects(self, identifiers: List[str]) -> DeployResult:
        raise DeploymentNotSupported(f"{self.backend_type} does not support drop_objects")

    async def test_connection(self) -> Dict[str, object]:
        return {"success": False, "message": "test_connection not implemented"}

    # ── the delta deployer (PROP-008 A3) ───────────────────────────────────────

    async def database_owned(self, name: str) -> Optional[bool]:
        """Does the deploy LOGIN own database ``name`` — its owner in the dictionary, or a
        login granted CREATE TABLE on it?  The landing rule (§13) reads this once per
        deploy: owned ⇒ the model's own database, else the platform sibling.  ``None`` =
        the backend cannot say (treated as not owned — never write where we cannot prove
        ownership).  Requires the ``CREATE_DATABASE`` capability (the same dictionary)."""
        raise DeploymentNotSupported(f"{self.backend_type} cannot read database ownership")

    async def describe_tables(self, database: str, tables: Optional[List[str]] = None) -> Dict[str, Dict[str, object]]:
        """The dictionary's view of ``database``: ``{"db.table": {"columns": {col: type},
        "foreign_keys": [(col, "db.parent", parent_col)]}}`` for the named tables (all when
        ``None``).  What ``plan_delta`` reads as *before* — a landed object is one the
        dictionary holds, so a re-run plans nothing for it."""
        raise DeploymentNotSupported(f"{self.backend_type} cannot describe tables")

    # ── the readback (PROP-008 A4 §4.4 (3)) — optional; the defaults are HONEST ─────
    async def amp_count(self) -> Optional[int]:
        """How many units the backend distributes rows over (Teradata: AMPs).  ``None`` =
        unknown — the probes then bound skew only for a constant key."""
        return None

    async def read_design(self, identifiers: List[str]) -> Dict[str, Dict[str, object]]:
        """Read the dictionary's design facts for ``identifiers`` (``db.table``) in the shape
        ``readback.readback_report`` consumes.  A fact the backend cannot read is ``None``
        (→ ``unverified``), never guessed.  The default reads nothing: every rule unverified."""
        return {}

    async def measure_pi_skew(self, identifier: str, pi_columns: List[str]) -> Optional[Dict[str, object]]:
        """The measured distribution of ``identifier``'s rows over the units when hashed on
        ``pi_columns`` — ``{skew_pct, amps, total_rows, max_rows, avg_rows}`` — or ``None``."""
        return None

    async def execute_delta(self, plan, *, dry_run: bool = False):
        """Run a ``DeltaPlan``'s PENDING statements, one per round trip, never stopping on
        a failed statement (the next run reads the dictionary and resumes what is missing)
        and never cleaning up (a half-landed delta is resumable by construction).  Returns
        a ``DeltaResult``.  ``dry_run`` performs no I/O."""
        from .delta import DeltaResult
        ddl = plan.to_ddl_plan()
        res = await self.execute_ddl(ddl, dry_run=dry_run, stop_on_error=False, include_statistics=True)
        out = DeltaResult(dry_run=dry_run)
        if dry_run:
            out.executed_sql = []
            out.success = True
            return out
        out.landed = list(res.executed)
        out.skipped = list(res.skipped)
        out.errors = list(res.errors)
        out.executed_sql = [st.sql for st in ddl.ordered()
                            if (st.kind, st.identifier) in set(res.executed)]
        out.success = not res.errors
        return out
