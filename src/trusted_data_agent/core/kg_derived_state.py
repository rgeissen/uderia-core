"""Per-KG derived state dies with the KG's metadata row — ONE purge over N tables.

A knowledge graph's `kg_metadata` row owns rows in tables keyed on nothing but its
`kg_id`, none of which is reachable once that row is gone:

  * ``kg_versions``      — snapshot history            (`core.kg_version_db`)
  * ``kg_entity_usage``  — retrieval telemetry         (`kgstore.usage`)
  * ``kg_content_hash_cache`` — the ontology hash at a content revision (`trust.kg_revision`)
  * ``kg_content_memo``  — other graph facts at a content revision (`trust.kg_revision`)

KG deletion has NO single chokepoint — six ``DELETE FROM kg_metadata`` statements in
five files, each with its own transaction — so the purge cannot live at one site. What
CAN live in one place is the LIST of what must be purged. Before this module each site
hand-called `delete_versions_for_kg`, and the second table was simply never added to
any of them: `kg_entity_usage` (Phase E, Aug 2026) predates the versions purge
(2026-09-08) and was still unpurged a week later — 8,879 rows on production, part of
them for graphs that no longer existed. A per-site call list is a hand-maintained
allow-list, the defect class this codebase keeps paying for; a registry is one row.

Contract:
  * `purge_for_kg(kg_id)` runs EVERY purger and never raises — a purger that fails is
    logged by name (WARNING) and does not stop the others (losing telemetry must never
    block a removal the user asked for, and a versions failure must not leave usage
    behind).
  * Every ``DELETE FROM kg_metadata`` site calls `purge_for_kg` and nothing else for
    this purpose (`test/test_kg_derived_state_cleanup.py` holds both halves: the
    registry covers every derived table, and every deletion site calls the registry).
  * Adding a derived table = one `_Purger` row here + its primitive. No site changes.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, NamedTuple

logger = logging.getLogger("quart.app")


class _Purger(NamedTuple):
    table: str
    fn: Callable[[str], int]


def _versions(kg_id: str) -> int:
    from trusted_data_agent.core import kg_version_db
    return kg_version_db.delete_versions_for_kg(kg_id)


def _usage(kg_id: str) -> int:
    from trusted_data_agent.kgstore import usage
    return usage.delete_for_kg(kg_id)


def _content_hash(kg_id: str) -> int:
    from trusted_data_agent.core.trust import kg_revision
    return kg_revision.purge_hash_cache(kg_id)


def _content_memo(kg_id: str) -> int:
    from trusted_data_agent.core.trust import kg_revision
    return kg_revision.purge_memo(kg_id)


#: THE list. A table keyed only on kg_id that must not outlive the kg_metadata row.
PURGERS: tuple = (
    _Purger("kg_versions", _versions),
    _Purger("kg_entity_usage", _usage),
    _Purger("kg_content_hash_cache", _content_hash),
    _Purger("kg_content_memo", _content_memo),
)


def derived_tables() -> List[str]:
    """The tables the purge covers — what the gate compares against the schema."""
    return [p.table for p in PURGERS]


def purge_for_kg(kg_id: str) -> Dict[str, object]:
    """Purge every derived table for ``kg_id``. Never raises.

    Returns ``{"removed": {table: rows}, "errors": {table: message}}`` so a caller can
    log what went, and a gate can assert that one failing purger did not stop the rest.
    """
    removed: Dict[str, int] = {}
    errors: Dict[str, str] = {}
    if not kg_id:
        return {"removed": removed, "errors": errors}
    for p in PURGERS:
        try:
            removed[p.table] = int(p.fn(kg_id) or 0)
        except Exception as exc:  # noqa: BLE001 — fail-soft, per table, by name
            errors[p.table] = f"{type(exc).__name__}: {exc}"
            logger.warning(f"KG derived state: could not purge {p.table} for {kg_id}: {exc}")
    return {"removed": removed, "errors": errors}
