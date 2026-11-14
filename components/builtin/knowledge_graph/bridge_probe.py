"""
bridge_probe — turn a DECLARED cross-product link into testable evidence.

A ``product_bridge`` node is a *claim*: some product author asserted that one
product's column corresponds to another's.  Its basis may be a real shared key or —
as on the reference deployment — a self-declared "Synthetic Crosswalk Key" pointing
at a table that was never deployed.  A claim is not a join, and an agent must not
present one as if it were.

This module does the deterministic half: resolve both sides of a bridge to their
**deployed** identifiers and compile a probe that measures whether their values
actually overlap.  What the numbers *mean* stays judgement (see the
``policing-data-product-consumer`` skill) — but the agent should never have to write this query, and
should never be the thing that decides whether the columns even exist.

WHY IT COMPILES RATHER THAN EXECUTES
------------------------------------
The KG component has no warehouse connection and deliberately does not acquire one:
executing SQL from inside a component would bypass the MCP tool catalog, which is
where **Agent Authority** enforces ``deny_writes`` / denied tools / connector
allow-lists.  A read-only probe is harmless, but the precedent is not — a component
that opens its own connection is a governance hole the next feature walks through.
So this mirrors ``compile_metric``: the component does the hard resolution and
emits ready-to-run SQL; the agent executes it through its normal, governed path.

Pure: no I/O, no LLM.  ``build_probe_sql`` is the unit-test target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: Rows of overlapping values returned as illustrative evidence.
SAMPLE_LIMIT = 10


@dataclass
class BridgeProbe:
    """The resolved, compilable form of a declared bridge."""

    bridge: str
    linkage_mechanism: str = "undeclared"
    #: the claim as the product stated it, e.g. "InvestigativeAction → incident_Current"
    links: Optional[str] = None
    source_identifier: Optional[str] = None      # db.table.column
    target_identifier: Optional[str] = None
    overlap_sql: Optional[str] = None
    sample_sql: Optional[str] = None
    #: why a probe could not be compiled — an honest blocker, never a silent empty
    blocked: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def probeable(self) -> bool:
        return bool(self.overlap_sql)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "bridge": self.bridge,
            "linkage_mechanism": self.linkage_mechanism,
            "links": self.links,
            "source_identifier": self.source_identifier,
            "target_identifier": self.target_identifier,
            "overlap_sql": self.overlap_sql,
            "sample_sql": self.sample_sql,
            "blocked": self.blocked,
            "notes": self.notes,
            "probeable": self.probeable,
        }


def _q(identifier: str) -> str:
    """Quote a Teradata identifier part-wise (``db.table`` → ``"db"."table"``)."""
    return ".".join(f'"{p.strip()}"' for p in str(identifier).split(".") if p.strip())


def _split(identifier: str) -> Optional[Tuple[str, str]]:
    """Split ``db.table.column`` into (``db.table``, ``column``)."""
    parts = [p for p in str(identifier or "").split(".") if p.strip()]
    if len(parts) < 3:
        return None
    return ".".join(parts[:-1]), parts[-1]


def build_probe_sql(source_identifier: str, target_identifier: str,
                    sample_limit: int = SAMPLE_LIMIT) -> Tuple[Optional[str], Optional[str]]:
    """Compile (overlap_sql, sample_sql) for two fully-qualified ``db.table.column``.

    The overlap query reports three counts — distinct values each side and how many
    they share — because a bare intersection count is unreadable without its
    denominators: 40 shared values means something very different against 45 than
    against 4,000,000.
    """
    src, tgt = _split(source_identifier), _split(target_identifier)
    if not src or not tgt:
        return None, None
    (s_tbl, s_col), (t_tbl, t_col) = src, tgt

    overlap = (
        "SELECT\n"
        f"  (SELECT COUNT(DISTINCT {_q(s_col)}) FROM {_q(s_tbl)}) AS source_distinct,\n"
        f"  (SELECT COUNT(DISTINCT {_q(t_col)}) FROM {_q(t_tbl)}) AS target_distinct,\n"
        "  (SELECT COUNT(*) FROM\n"
        f"     (SELECT DISTINCT {_q(s_col)} AS v FROM {_q(s_tbl)}) s\n"
        f"     INNER JOIN (SELECT DISTINCT {_q(t_col)} AS v FROM {_q(t_tbl)}) t\n"
        "       ON s.v = t.v) AS overlapping_values"
    )
    sample = (
        f"SELECT TOP {int(sample_limit)} s.v AS shared_value FROM\n"
        f"  (SELECT DISTINCT {_q(s_col)} AS v FROM {_q(s_tbl)}) s\n"
        f"  INNER JOIN (SELECT DISTINCT {_q(t_col)} AS v FROM {_q(t_tbl)}) t\n"
        "    ON s.v = t.v"
    )
    return overlap, sample


def _deployed_column(entity_props: Dict[str, Any], column: str) -> Optional[str]:
    """``db.table.column`` for a column of an entity, from its deployed table."""
    table = (entity_props or {}).get("deployed_table")
    col = (column or "").strip()
    if not table or not col:
        return None
    return f"{table}.{col}"


def resolve_bridge_probe(bridge_node: Dict[str, Any],
                         entity_by_name: Dict[str, Dict[str, Any]],
                         entity_by_label: Optional[Dict[str, Dict[str, Any]]] = None,
                         sample_limit: int = SAMPLE_LIMIT) -> BridgeProbe:
    """Resolve a ``product_bridge`` node into a compilable probe.

    Args:
        bridge_node: the bridge entity (``{"name", "properties": {...}}``).
        entity_by_name: logical entities keyed by canonical name.
        entity_by_label: the same entities keyed by their human label, so a bridge
            naming a bare table (``provenance``) can resolve across products.

    A bridge whose target does not exist yields ``blocked`` rather than SQL — there
    is nothing to measure, and saying so is the useful answer.
    """
    props = (bridge_node or {}).get("properties") or {}
    probe = BridgeProbe(
        bridge=(bridge_node or {}).get("name") or props.get("label") or "bridge",
        linkage_mechanism=props.get("linkage_mechanism") or "undeclared",
        links=props.get("links"),
    )
    by_label = entity_by_label or {}

    if probe.linkage_mechanism == "type_correspondence":
        # a class-level correspondence has no per-record key, so there is no value
        # overlap to probe — it is READ from its basis, never measured or joined.
        probe.blocked = (
            "this is a CLASS-level correspondence (kinds correspond to kinds), not a "
            "per-record link — there is nothing to probe at the row level; read and "
            "state the correspondence from its basis"
        )
        return probe

    def _find(ref: Any) -> Optional[Dict[str, Any]]:
        name = str(ref or "").strip()
        if not name:
            return None
        if name in entity_by_name:
            return entity_by_name[name]
        bare = name.rpartition(".")[2] or name
        return by_label.get(bare.lower())

    src_ent = _find(props.get("source_reference"))
    tgt_ent = _find(props.get("target_reference"))
    src_col = props.get("source_property") or ""
    tgt_col = props.get("target_column") or ""
    # a facet-style reference ("EmailAddressFacet.emailAddressValue") names its own
    # column in the last segment
    src_col = str(src_col).rpartition(".")[2] or str(src_col)

    if not tgt_ent:
        probe.blocked = (
            f"the declared target '{props.get('target_reference')}' does not exist in "
            "this graph, so there is nothing to measure — the link is a claim only"
        )
        return probe
    if not src_ent:
        probe.blocked = (
            f"the declared source '{props.get('source_reference')}' does not exist in "
            "this graph"
        )
        return probe
    if not src_col or not tgt_col:
        probe.blocked = ("the bridge names no column on one or both sides, so no value "
                         "comparison is defined")
        return probe

    s_ident = _deployed_column(src_ent.get("properties") or {}, src_col)
    t_ident = _deployed_column(tgt_ent.get("properties") or {}, tgt_col)
    if not s_ident or not t_ident:
        probe.blocked = ("one side has no deployed table in this graph, so its column "
                         "cannot be addressed")
        return probe

    probe.source_identifier, probe.target_identifier = s_ident, t_ident
    probe.overlap_sql, probe.sample_sql = build_probe_sql(s_ident, t_ident, sample_limit)
    if probe.linkage_mechanism.lower() != "undeclared":
        probe.notes.append(
            f"the product declares this link's basis as '{probe.linkage_mechanism}' — "
            "evidence of value overlap does not upgrade a declared basis into a verified one"
        )
    return probe


def index_entities(entities: List[Dict[str, Any]]
                   ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Build (by canonical name, by lower-cased label) indexes of logical entities.

    A label shared by two products is left OUT of the label index rather than
    resolved arbitrarily — attaching a probe to the wrong product's table is worse
    than declining to probe.
    """
    by_name: Dict[str, Dict[str, Any]] = {}
    label_hits: Dict[str, List[Dict[str, Any]]] = {}
    for e in entities or []:
        if (e or {}).get("entity_type") != "entity":
            continue
        name = e.get("name")
        if not name:
            continue
        by_name[name] = e
        label = ((e.get("properties") or {}).get("label")
                 or str(name).rpartition(".")[2] or name)
        label_hits.setdefault(str(label).lower(), []).append(e)
    by_label = {k: v[0] for k, v in label_hits.items() if len(v) == 1}
    return by_name, by_label
