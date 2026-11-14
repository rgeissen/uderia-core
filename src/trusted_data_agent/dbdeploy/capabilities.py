"""
Deployment backend capability enum.

Mirrors vectorstore/capabilities.py and kgstore/capabilities.py — each capability
maps to an optional feature of a deployment backend.  REQUIRED_CAPABILITIES is
the minimal set every backend must support to be usable.
"""

from enum import Enum, auto
from typing import Set


class DeploymentCapability(Enum):
    # ── Required ─────────────────────────────────────────────────────
    EXECUTE_DDL = auto()        # run a DDL plan
    DRY_RUN = auto()            # produce the DDL without executing it

    # ── Optional ─────────────────────────────────────────────────────
    CREATE_DATABASE = auto()    # provision a database/schema namespace
    BULK_LOAD = auto()          # bulk-load tabular data (CSV → table)
    DROP_OBJECTS = auto()       # drop created objects (cleanup-on-failure)
    SOFT_RI = auto()            # soft referential integrity (no-check FKs)
    COLLECT_STATISTICS = auto() # optimizer statistics collection
    VIEWS = auto()              # CREATE VIEW (metric views, etc.)
    TRANSACTIONAL_DDL = auto()  # DDL participates in a transaction (rare; TD = no)


REQUIRED_CAPABILITIES: Set[DeploymentCapability] = {
    DeploymentCapability.EXECUTE_DDL,
    DeploymentCapability.DRY_RUN,
}
