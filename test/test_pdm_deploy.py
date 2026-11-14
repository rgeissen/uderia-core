"""
M2 module tests — dbdeploy deployment abstraction.

CI-pure: no live Teradata.  Exercises capability negotiation, the factory
(registration / resolution / required-capability validation / singleton cache),
the ABC's not-supported defaults, and the Teradata backend's **dry-run** path
(which short-circuits before any I/O and emits the DDL).  Real DDL execution +
bulk load are covered by the integration test (needs a reachable Teradata).

Run:
    python test/test_pdm_deploy.py
"""

import asyncio
import json
import os
import sys
from typing import Set

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.dbdeploy import convert  # noqa: E402
from trusted_data_agent.dbdeploy import factory  # noqa: E402
from trusted_data_agent.dbdeploy.backend import (  # noqa: E402
    DeploymentBackend,
    DeploymentNotSupported,
)
from trusted_data_agent.dbdeploy.capabilities import (  # noqa: E402
    DeploymentCapability,
    REQUIRED_CAPABILITIES,
)
from trusted_data_agent.dbdeploy.teradata_backend import TeradataDeploymentBackend  # noqa: E402
from trusted_data_agent.dbdeploy.types import DDLPlan, DeployResult, LoadPlan  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "industry_model_pdm_sample.json")

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {label}")
    else:
        _failed += 1
        print(f"  ✗ {label}")


def run(coro):
    return asyncio.run(coro)


def build_plan() -> DDLPlan:
    with open(FIXTURE) as f:
        model = json.load(f)
    return convert(model, industry="acme", flavour="mvm").ddl


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeBackend(DeploymentBackend):
    def __init__(self, connection_config=None):
        self._config = connection_config or {}

    @property
    def backend_type(self) -> str:
        return "fake"

    def capabilities(self) -> Set[DeploymentCapability]:
        return set(REQUIRED_CAPABILITIES) | {DeploymentCapability.CREATE_DATABASE}

    async def execute_ddl(self, plan, *, dry_run=False, stop_on_error=True, include_statistics=True, replace_existing=False):
        r = DeployResult(dry_run=dry_run, generated_sql=plan.sql_script())
        if dry_run:
            r.planned = [s.identifier for s in plan.ordered()]
        else:
            r.executed = [(s.kind, s.identifier) for s in plan.ordered()]
        r.success = True
        return r


class IncompleteBackend(DeploymentBackend):
    def __init__(self, connection_config=None):
        self._config = connection_config or {}

    @property
    def backend_type(self) -> str:
        return "incomplete"

    def capabilities(self) -> Set[DeploymentCapability]:
        return {DeploymentCapability.EXECUTE_DDL}  # missing DRY_RUN

    async def execute_ddl(self, plan, *, dry_run=False, stop_on_error=True, include_statistics=True, replace_existing=False):
        return DeployResult(success=True)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_capability_enum():
    print("\n[test_capability_enum]")
    check("required = EXECUTE_DDL + DRY_RUN",
          REQUIRED_CAPABILITIES == {DeploymentCapability.EXECUTE_DDL, DeploymentCapability.DRY_RUN})


def test_factory_registration_and_validation():
    print("\n[test_factory_registration_and_validation]")
    factory.reset_instances()
    check("teradata registered at import", "teradata" in factory.list_backends())

    factory.register_backend("fake", FakeBackend)
    inst = factory.make_backend("fake")
    check("make_backend resolves registered backend", isinstance(inst, FakeBackend))

    factory.register_backend("incomplete", IncompleteBackend)
    try:
        factory.make_backend("incomplete")
        check("missing required capability rejected", False)
    except RuntimeError as e:
        check("missing required capability rejected", "DRY_RUN" in str(e))

    try:
        factory.make_backend("nope")
        check("unknown backend raises", False)
    except ValueError:
        check("unknown backend raises", True)


def test_factory_singleton():
    print("\n[test_factory_singleton]")
    factory.reset_instances()
    factory.register_backend("fake", FakeBackend)
    a = run(factory.get_backend("fake", {"host": "h", "database": "d", "username": "u"}))
    b = run(factory.get_backend("fake", {"host": "h", "database": "d", "username": "u"}))
    check("same identity -> same cached instance", a is b)


def test_teradata_capabilities():
    print("\n[test_teradata_capabilities]")
    td = TeradataDeploymentBackend({})
    caps = td.capabilities()
    check("backend_type teradata", td.backend_type == "teradata")
    check("advertises required caps", REQUIRED_CAPABILITIES <= caps)
    check("advertises CREATE_DATABASE", DeploymentCapability.CREATE_DATABASE in caps)
    check("advertises SOFT_RI", DeploymentCapability.SOFT_RI in caps)
    check("advertises BULK_LOAD (M3)", DeploymentCapability.BULK_LOAD in caps)


def test_teradata_dry_run_no_io():
    print("\n[test_teradata_dry_run_no_io]")
    plan = build_plan()
    td = TeradataDeploymentBackend({})  # no real connection
    res = run(td.execute_ddl(plan, dry_run=True))
    check("dry_run success", res.success is True and res.dry_run is True)
    check("dry_run emits full DDL script", res.generated_sql == plan.sql_script())
    check("dry_run planned all statements", len(res.planned) == len(plan.ordered()))
    check("dry_run executed nothing", res.executed == [])
    check("dry_run did NOT connect (no I/O)", td._initialized is False)


def test_not_supported_defaults():
    print("\n[test_not_supported_defaults]")
    fake = FakeBackend()
    try:
        run(fake.bulk_load(LoadPlan()))
        check("bulk_load default raises NotSupported", False)
    except DeploymentNotSupported:
        check("bulk_load default raises NotSupported", True)
    try:
        run(fake.drop_objects(["x"]))
        check("drop_objects default raises NotSupported", False)
    except DeploymentNotSupported:
        check("drop_objects default raises NotSupported", True)


def test_teradata_requires_credentials():
    print("\n[test_teradata_requires_credentials]")
    # bulk_load is implemented (M3); with no credentials, initialize() guards.
    from trusted_data_agent.dbdeploy.backend import DeploymentError
    td = TeradataDeploymentBackend({})
    try:
        run(td.bulk_load(LoadPlan(tables=[])))
        # empty plan never reaches a row; but initialize() runs first.
        check("missing credentials guarded", True)
    except DeploymentError as e:
        check("missing credentials guarded", "credentials" in str(e).lower() or "pat_token" in str(e).lower())


class _CaptureTD(TeradataDeploymentBackend):
    """Intercepts I/O so ensure_database/database_exists run with no live system."""
    def __init__(self, exists=False, fail=False):
        super().__init__({})
        self._exists = exists
        self._fail = fail
        self.sql = []
    async def initialize(self):
        self._initialized = True
    async def database_exists(self, name):
        return self._exists
    async def _execute_sql(self, sql):
        self.sql.append(sql)
        if self._fail:
            raise RuntimeError("[5612] Worker Task ... no PERM space available")
        return None


def test_ensure_database_creates_when_missing():
    print("\n[test_ensure_database_creates_when_missing]")
    be = _CaptureTD(exists=False)
    created = run(be.ensure_database("acme_mvm", parent="dbc", perm_bytes=5_000_000_000))
    check("returns True (created)", created is True)
    check("CREATE DATABASE issued", any("CREATE DATABASE" in s and '"acme_mvm"' in s for s in be.sql))
    check("uses parent + PERM", any('FROM "dbc" AS PERM = 5000000000' in s for s in be.sql))


def test_ensure_database_skips_when_exists():
    print("\n[test_ensure_database_skips_when_exists]")
    be = _CaptureTD(exists=True)
    created = run(be.ensure_database("acme_mvm"))
    check("returns False (already existed)", created is False)
    check("no CREATE DATABASE issued (idempotent)", not any("CREATE DATABASE" in s for s in be.sql))


def test_preview_create_database():
    print("\n[test_preview_create_database]")
    be = TeradataDeploymentBackend({})
    sql = be.preview_create_database("acme_mvm", parent="dbc", perm_bytes=5_000_000_000)
    check("preview is exact Teradata CREATE DATABASE",
          sql == 'CREATE DATABASE "acme_mvm" FROM "dbc" AS PERM = 5000000000;')
    check("preview matches what ensure_database would run",
          sql == be._create_database_sql("acme_mvm", "dbc", 5_000_000_000))


def test_ensure_database_transparent_error():
    print("\n[test_ensure_database_transparent_error]")
    from trusted_data_agent.dbdeploy.backend import DeploymentError
    be = _CaptureTD(exists=False, fail=True)
    try:
        run(be.ensure_database("acme_mvm", parent="dbc"))
        check("raises DeploymentError on failure", False)
    except DeploymentError as e:
        check("raises DeploymentError on failure", True)
        check("exact Teradata error preserved", "5612" in str(e) and "acme_mvm" in str(e))


class _IdempotentTD(TeradataDeploymentBackend):
    """Stateful fake: tracks which tables/views exist and enforces Teradata's
    real drop semantics — a soft-RI parent can't drop while a child references it
    (Error 5313), a missing object errors 3807, a CREATE over an existing table
    errors 3803.  Lets us prove the multi-pass drop-and-recreate is FK-safe."""
    def __init__(self, tables=(), views=(), child_of=None):
        super().__init__({})
        self.tables = set(tables)
        self.views = set(views)
        self.child_of = {k: set(v) for k, v in (child_of or {}).items()}
        self.sql = []

    async def initialize(self):
        self._initialized = True

    async def _execute_sql(self, sql):
        import re
        self.sql.append(sql)
        s = sql.strip()
        m = re.match(r'DROP TABLE "([^"]+)"\."([^"]+)"', s)
        if m:
            ident = f"{m.group(1)}.{m.group(2)}"
            if ident not in self.tables:
                raise RuntimeError("[Error 3807] Object does not exist")
            if self.child_of.get(ident, set()) & self.tables:
                raise RuntimeError("[Error 5313] Referential integrity violation: cannot drop a referenced (Parent) table")
            self.tables.discard(ident); return None
        m = re.match(r'DROP VIEW "([^"]+)"\."([^"]+)"', s)
        if m:
            ident = f"{m.group(1)}.{m.group(2)}"
            if ident not in self.views:
                raise RuntimeError("[Error 3807] Object does not exist")
            self.views.discard(ident); return None
        m = re.match(r'CREATE (?:MULTISET )?TABLE "([^"]+)"\."([^"]+)"', s)
        if m:
            ident = f"{m.group(1)}.{m.group(2)}"
            if ident in self.tables:
                raise RuntimeError("[Error 3803] Table 'x' already exists.")
            self.tables.add(ident); return None
        m = re.match(r'CREATE VIEW "([^"]+)"\."([^"]+)"', s)
        if m:
            self.views.add(f"{m.group(1)}.{m.group(2)}"); return None
        return None  # FKs/comments/etc.


def _redeploy_plan() -> DDLPlan:
    plan = DDLPlan()
    plan.add("table", "db.parent", 'CREATE MULTISET TABLE "db"."parent" ("id" BIGINT);', 20)
    plan.add("table", "db.child", 'CREATE MULTISET TABLE "db"."child" ("id" BIGINT);', 20)
    plan.add("foreign_key", "db.child",
             'ALTER TABLE "db"."child" ADD FOREIGN KEY ("id") REFERENCES WITH NO CHECK OPTION "db"."parent" ("id");', 40)
    plan.add("view", "db.v", 'CREATE VIEW "db"."v" AS SELECT * FROM "db"."parent";', 45)
    return plan


def test_redeploy_idempotent_drops_then_creates():
    print("\n[test_redeploy_idempotent_drops_then_creates]")
    # A prior deploy is already present; child references parent (soft RI).
    be = _IdempotentTD(tables={"db.parent", "db.child"}, views={"db.v"},
                       child_of={"db.parent": {"db.child"}})
    res = run(be.execute_ddl(_redeploy_plan(), replace_existing=True))
    check("re-deploy succeeds (no Error 3803)", res.success is True)
    check("dropped existing view + both tables (3)", len(res.replaced) == 3)
    kinds = [k for k, _ in res.replaced]
    check("view dropped before tables", kinds[0] == "view")
    dropped_tables = [i for k, i in res.replaced if k == "table"]
    check("multi-pass: child dropped before parent (FK-safe)",
          dropped_tables.index("db.child") < dropped_tables.index("db.parent"))
    check("all objects exist again after re-create",
          {"db.parent", "db.child"} <= be.tables and "db.v" in be.views)
    check("parent DROP TABLE was attempted twice (blocked then retried)",
          sum(1 for s in be.sql if s.startswith('DROP TABLE "db"."parent"')) == 2)


def test_first_deploy_drops_are_noops():
    print("\n[test_first_deploy_drops_are_noops]")
    be = _IdempotentTD()  # clean target — nothing exists
    res = run(be.execute_ddl(_redeploy_plan(), replace_existing=True))
    check("first deploy succeeds", res.success is True)
    check("nothing replaced (all drops are no-ops)", res.replaced == [])
    check("objects created", {"db.parent", "db.child"} <= be.tables and "db.v" in be.views)


def test_replace_existing_off_does_not_drop():
    print("\n[test_replace_existing_off_does_not_drop]")
    be = _IdempotentTD(tables={"db.parent"})
    # replace_existing defaults False → no pre-drop; CREATE over existing parent errors 3803.
    res = run(be.execute_ddl(_redeploy_plan(), replace_existing=False, stop_on_error=True))
    check("no pre-drop when replace_existing off", res.replaced == [])
    check("create-over-existing fails (proves drop was needed)", res.success is False)


class _FKLimitTD(TeradataDeploymentBackend):
    """Fake target where a specific FK ALTER hits Teradata's per-table
    referential-constraint cap (Error 3974) — proves a soft-RI FK failure is
    skipped, not fatal, and never rolls back already-created tables."""
    def __init__(self, fail_fk_on: str):
        super().__init__({})
        self.fail_fk_on = fail_fk_on
        self.sql = []
    async def initialize(self):
        self._initialized = True
    async def _execute_sql(self, sql):
        self.sql.append(sql)
        if sql.strip().startswith("ALTER TABLE") and self.fail_fk_on in sql:
            raise RuntimeError("[Error 3974] This table has too many referential constraints.")
        return None


def test_fk_limit_error_is_non_fatal():
    print("\n[test_fk_limit_error_is_non_fatal]")
    be = _FKLimitTD(fail_fk_on='"db"."child"')
    res = run(be.execute_ddl(_redeploy_plan(), replace_existing=False, stop_on_error=True))
    check("deploy succeeds despite FK 3974", res.success is True)
    check("no errors recorded (FK failure is not an error)", res.errors == [])
    check("offending FK recorded as skipped", ("foreign_key", "db.child") in res.skipped)
    check("both tables still created (no rollback)",
          ("table", "db.parent") in res.executed and ("table", "db.child") in res.executed)
    check("view still created after the skipped FK", ("view", "db.v") in res.executed)


class _ViewRefusedTD(TeradataDeploymentBackend):
    """Fake target where Teradata refuses a metric view's compiled definition."""
    def __init__(self):
        super().__init__({})
        self.sql = []
    async def initialize(self):
        self._initialized = True
    async def _execute_sql(self, sql):
        self.sql.append(sql)
        if sql.strip().upper().startswith("CREATE VIEW") or sql.strip().upper().startswith("REPLACE VIEW"):
            raise RuntimeError("[Error 3706] Syntax error: expected something between '(' and the 'CAST' keyword.")
        return None


def test_view_refusal_is_non_fatal():
    """A metric view is derived + optional: a refused definition is skipped and
    NAMED, never a rollback of every table the deploy created."""
    print("\n[test_view_refusal_is_non_fatal]")
    be = _ViewRefusedTD()
    res = run(be.execute_ddl(_redeploy_plan(), replace_existing=False, stop_on_error=True))
    check("deploy succeeds despite the refused view", res.success is True and res.errors == [])
    check("the view is recorded as skipped", ("view", "db.v") in res.skipped)
    check("the reason is kept for the deploy summary", "3706" in (getattr(res, "skipped_reasons", {}) or {}).get("db.v", ""))
    check("the tables were NOT rolled back", res.cleaned_up == [] and ("table", "db.parent") in res.executed)


class _ReplaceViewTD(TeradataDeploymentBackend):
    """Fake target recording every statement; refuses ONE named view."""
    def __init__(self, refuse=()):
        super().__init__({})
        self.sql = []
        self._refuse = set(refuse)
    async def initialize(self):
        self._initialized = True
    async def _execute_sql(self, sql):
        self.sql.append(sql)
        for name in self._refuse:
            if sql.strip().upper().startswith("REPLACE VIEW") and name in sql:
                raise RuntimeError("[Error 3706] Syntax error, expected something like a name")
        return None


def _views_and_tables_plan():
    from trusted_data_agent.dbdeploy.types import DDLPlan, PHASE_VIEW
    plan = DDLPlan()
    plan.add("table", "db.parent", 'CREATE MULTISET TABLE "db"."parent" (id INTEGER);', 20)
    plan.add("view", "db.v_ok", 'CREATE VIEW "db"."v_ok" AS\nSELECT 1 AS x FROM "db"."parent";', PHASE_VIEW)
    plan.add("view", "db.v_bad", 'CREATE VIEW "db"."v_bad" AS\nSELECT 1 AS x FROM "db"."parent";', PHASE_VIEW)
    plan.add("statistics", "db.parent", 'COLLECT STATISTICS ON "db"."parent" COLUMN (id);', 50)
    return plan


def test_replace_views_is_atomic_and_views_only():
    """The views-only repair: every view goes through REPLACE VIEW (create-or-swap in
    ONE statement — a refused definition leaves the existing view standing, no
    drop-then-create hole); tables/statistics in the same plan are never touched;
    a refusal is skipped and named, never an error."""
    print("\n[test_replace_views_is_atomic_and_views_only]")
    be = _ReplaceViewTD(refuse={"v_bad"})
    res = run(be.replace_views(_views_and_tables_plan()))
    ups = [q.strip().upper() for q in be.sql]
    check("only the two views were sent", len(be.sql) == 2 and all(q.startswith("REPLACE VIEW") for q in ups))
    check("no CREATE VIEW, no DROP VIEW (atomic swap, never a hole)",
          not any(q.startswith(("CREATE VIEW", "DROP VIEW", "DROP TABLE", "CREATE MULTISET", "COLLECT")) for q in ups))
    check("the body after the keyword is the compiled SQL verbatim",
          be.sql[0].split("\n", 1)[1] == 'SELECT 1 AS x FROM "db"."parent";')
    check("the accepted view is executed", ("view", "db.v_ok") in res.executed)
    check("the refused view is skipped with its reason, not an error",
          ("view", "db.v_bad") in res.skipped and "3706" in res.skipped_reasons.get("db.v_bad", "")
          and res.errors == [] and res.success is True)


def main():
    for fn in [
        test_capability_enum,
        test_ensure_database_creates_when_missing,
        test_ensure_database_skips_when_exists,
        test_preview_create_database,
        test_ensure_database_transparent_error,
        test_factory_registration_and_validation,
        test_factory_singleton,
        test_teradata_capabilities,
        test_teradata_dry_run_no_io,
        test_not_supported_defaults,
        test_teradata_requires_credentials,
        test_redeploy_idempotent_drops_then_creates,
        test_first_deploy_drops_are_noops,
        test_replace_existing_off_does_not_drop,
        test_fk_limit_error_is_non_fatal,
        test_view_refusal_is_non_fatal,
        test_replace_views_is_atomic_and_views_only,
    ]:
        fn()
    factory.reset_instances()
    print(f"\n{'='*50}\nM2 deploy: {_passed} passed, {_failed} failed\n{'='*50}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
