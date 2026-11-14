"""
delta — the delta deployer's PURE core (PROP-008 increment A3, §4.3).

``plan_delta(authored, facts, dictionary, landing)`` turns the graph's AUTHORED layer
(what was drafted) and the target's DICTIONARY (what exists) into a ``DeltaPlan``: the
statements that make the warehouse match the model, and nothing else.  It is a
function of (before, after) and of nothing hidden:

  * an object the dictionary already holds is ``exists`` — no statement, so a
    re-run of a landed delta is a no-op and a HALF-landed delta resumes (the table
    exists, one column is missing → one ALTER);
  * an authored node whose realisation is gone (``removed``) is a drop, REFUSED
    unless ``force_drop`` — and only over objects the graph itself stamped as its own;
  * WHERE a new table lands is the ``Landing`` the caller resolved from deployment
    policy (§13): the model's own database when the deploy login owns it, else the
    platform-owned sibling ``UDERIA_EXT_<root>``.  Never an agent's choice, never
    read from the authored node.

No I/O here: the dictionary is handed in (``DeploymentBackend.describe_tables``),
the landing is handed in (``DeploymentBackend.database_owned``), and the plan is
handed back as a ``DDLPlan`` the existing ``execute_ddl`` runs one statement per
round trip (Teradata forbids several DDL per request — measured).  Emission reuses
the ONE design standard (``design_standard.map_type``, the PI rule, soft RI,
bounded COMMENT) so an authored table is built the way a walked one is.

The PI choice is a hook (``choose_pi``): today the declared key under the standard,
named ``declared_key`` on the receipt; A4 replaces it with the probed choice without
touching this planner.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .dialects.base import ColumnSpec
from .types import (
    DDLPlan,
    PHASE_COMMENT,
    PHASE_FOREIGN_KEY,
    PHASE_STATISTICS,
    PHASE_TABLE,
)

PHASE_COLUMN = PHASE_TABLE + 5        # ALTER … ADD lands after CREATE, before COMMENT
PHASE_DROP = PHASE_STATISTICS + 10    # drops last (and only with force)
PHASE_REFERENCE_SUPPORT = PHASE_FOREIGN_KEY + 2   # an index a reference needs, after the reference

EXT_PREFIX = "UDERIA_EXT_"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _q(ident: str) -> str:
    return '"' + str(ident).replace('"', '""') + '"'


def _qn(*parts: str) -> str:
    return ".".join(_q(p) for p in parts)


def _esc(text: str) -> str:
    return str(text).replace("'", "''")


def _leaf(name: str) -> str:
    return str(name or "").rpartition(".")[2]


def _db_of(identifier: str) -> str:
    return str(identifier or "").partition(".")[0]


def sql_hash(sql: str) -> str:
    return hashlib.sha256(" ".join(str(sql).split()).encode("utf-8")).hexdigest()[:16]


# ── the landing rule (§13) ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Landing:
    """Where a NEW authored table lands — deployment policy, decided once per deploy."""
    root_db: str
    owned: Optional[bool]        # None = the backend could not say (treated as not owned)
    database: str
    rule: str                    # 'owned_root' | 'platform_sibling'

    def to_dict(self) -> Dict[str, Any]:
        return {"root_db": self.root_db, "owned": self.owned, "database": self.database, "rule": self.rule}


def landing_for(root_db: str, owned: Optional[bool]) -> Landing:
    """The model's own database when the deploy login OWNS it; otherwise the
    platform-owned sibling ``UDERIA_EXT_<root>``.  ``owned=None`` (unknown) lands in
    the sibling — a table is never written into a database we could not prove is ours."""
    root = str(root_db or "").strip()
    if owned is True:
        return Landing(root, True, root, "owned_root")
    return Landing(root, owned, f"{EXT_PREFIX}{root}", "platform_sibling")


# ── the plan ─────────────────────────────────────────────────────────────────

@dataclass
class DeltaChange:
    kind: str            # table | column | comment | foreign_key | statistics | drop_table | drop_column
    identifier: str      # db.table or db.table.column
    node: str            # the authored node this realises (or the removed one, for a drop)
    sql: str
    phase: int
    state: str = "planned"   # planned | exists | refused
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "identifier": self.identifier, "node": self.node,
                "state": self.state, "reason": self.reason, "sql_hash": sql_hash(self.sql) if self.sql else ""}


@dataclass
class DeltaPlan:
    landing: Landing
    changes: List[DeltaChange] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    requires_database: bool = False     # the landing database does not exist yet
    pi_rule: str = "declared_key"
    backend: str = "teradata"           # the dialect that said the statements (C3)
    reference_noun: str = "soft-RI reference"
    standard: str = ""                  # the design standard's version that built this plan (A4)
    pi_decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # table → the probed PI decision, when one was made
    # the authored node → the realised identifier(s), for the stamping that follows a landing
    realises: Dict[str, str] = field(default_factory=dict)

    def pending(self) -> List[DeltaChange]:
        return sorted([c for c in self.changes if c.state == "planned"], key=lambda c: c.phase)

    def existing(self) -> List[DeltaChange]:
        return [c for c in self.changes if c.state == "exists"]

    def refused(self) -> List[DeltaChange]:
        return [c for c in self.changes if c.state == "refused"]

    @property
    def is_noop(self) -> bool:
        return not self.pending()

    def to_ddl_plan(self) -> DDLPlan:
        ddl = DDLPlan()
        for c in self.pending():
            ddl.add(c.kind, c.identifier, c.sql, c.phase)
        return ddl

    def sql_script(self) -> str:
        return "\n".join(c.sql for c in self.pending())

    def summary(self) -> str:
        """The deploy preview IN WORDS (§14 row 9): what lands where, before anything runs."""
        pend = self.pending()
        tables = [c for c in pend if c.kind == "table"]
        cols = [c for c in pend if c.kind == "column"]
        fks = [c for c in pend if c.kind == "foreign_key"]
        drops = [c for c in pend if c.kind.startswith("drop_")]
        parts: List[str] = []
        if tables:
            parts.append(f"{len(tables)} table{'s' if len(tables) != 1 else ''} in {self.landing.database}"
                         + (" (the model's own database)" if self.landing.rule == "owned_root"
                            else " (a platform-owned sibling — the deploy login does not own "
                                 f"{self.landing.root_db})"))
        if cols:
            parts.append(f"{len(cols)} column{'s' if len(cols) != 1 else ''} added to existing tables")
        if fks:
            parts.append(f"{len(fks)} {self.reference_noun}{'s' if len(fks) != 1 else ''}")
        if drops:
            parts.append(f"{len(drops)} drop{'s' if len(drops) != 1 else ''} (forced)")
        if self.requires_database:
            parts.append(f"the database {self.landing.database} will be created first")
        if not parts:
            ex = self.existing()
            if ex:
                return f"nothing to land — every realisation already exists ({len(ex)} checked)"
            return "nothing to land"
        return " · ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {"landing": self.landing.to_dict(), "summary": self.summary(), "is_noop": self.is_noop,
                "backend": self.backend,
                "requires_database": self.requires_database, "pi_rule": self.pi_rule,
                "standard": self.standard, "pi_decisions": dict(self.pi_decisions),
                "pending": [c.to_dict() for c in self.pending()],
                "existing": [c.to_dict() for c in self.existing()],
                "refused": [c.to_dict() for c in self.refused()],
                "warnings": list(self.warnings), "realises": dict(self.realises)}


# ── the dictionary (what the target holds), one shape ───────────────────────

def _dict_table(dictionary: Dict[str, Any], identifier: str) -> Optional[Dict[str, Any]]:
    tables = (dictionary or {}).get("tables") or {}
    want = str(identifier).lower()
    for k, v in tables.items():
        if str(k).lower() == want:
            return v or {}
    return None


def _dict_has_column(tbl: Optional[Dict[str, Any]], column: str) -> bool:
    if not tbl:
        return False
    return str(column).lower() in {str(c).lower() for c in (tbl.get("columns") or {})}


def _dict_has_fk(tbl: Optional[Dict[str, Any]], column: str, parent: str, parent_col: str) -> bool:
    if not tbl:
        return False
    for fk in tbl.get("foreign_keys") or []:
        try:
            c, p, pc = fk
        except (TypeError, ValueError):
            continue
        if (str(c).lower(), str(p).lower(), str(pc).lower()) == (str(column).lower(), str(parent).lower(), str(parent_col).lower()):
            return True
    return False


# ── the planner ──────────────────────────────────────────────────────────────

def resolve_delta_dialect(name: Optional[str] = None, standard: Any = None):
    """The ``DeltaDialect`` that says a delta on backend ``name`` (default: the original target)."""
    b = str(name or "teradata").strip().lower()
    if b == "teradata":
        from .dialects.teradata import TeradataDeltaDialect
        return TeradataDeltaDialect(standard)
    if b in ("postgresql", "postgres"):
        from .dialects.postgresql import PostgresDeltaDialect
        return PostgresDeltaDialect(standard)
    raise ValueError(f"no delta dialect for backend '{name}'")


def default_choose_pi(table: str, key_cols: List[str], columns: List[str]) -> Tuple[List[str], str]:
    """The declared key, as declared (the dialect's default when no hook is given)."""
    return (list(key_cols), "declared_key") if key_cols else ([], "no_key")


def _key_columns(props: Dict[str, Any]) -> List[str]:
    return [k.strip() for k in str(props.get("business_key") or "").replace(";", ",").replace("|", ",").split(",") if k.strip()]


def plan_delta(
    authored: Dict[str, Any],
    facts: Dict[str, Dict[str, Any]],
    dictionary: Dict[str, Any],
    landing: Landing,
    *,
    standard: Optional[Any] = None,
    force_drop: bool = False,
    comments: str = "table",
    removed: Optional[List[Dict[str, str]]] = None,
    choose_pi: Optional[Callable[[str, List[str], List[str]], Tuple[List[str], str]]] = None,
    dialect: Any = None,
) -> DeltaPlan:
    """Plan the statements that realise the authored layer on the target.

    ``authored``:
        ``{"entities": [{"name", "props", "attributes": [{"name", "props"}], "parent": name|None,
                         "deployed": "db.table"|None}],
           "attributes": [{"name", "props", "entity": base_entity_name}]}``
        — authored ENTITIES (new tables) and authored ATTRIBUTES on BASE entities (new columns).
    ``facts``: ``{entity_name: {"deployed": "db.table", "key": [cols]}}`` for every base
        entity the plan may reference (a parent of a draft, the owner of a new column).
    ``dictionary``: ``{"databases": {names}, "tables": {"db.table": {"columns": {col: type},
        "foreign_keys": [(col, "db.parent", parent_col)]}}}`` — what the target HOLDS.
    ``removed``: ``[{"kind": "table"|"column", "identifier": "…", "node": "…"}]`` — realisations
        whose authored node is gone; dropped only with ``force_drop``.
    ``dialect``: the ``DeltaDialect`` (or its backend name) that SAYS the statements — the
        planner decides what lands, never how a backend writes it (C3).  Default: the
        original target, under ``standard``.
    """
    dl = dialect if (dialect is not None and not isinstance(dialect, str)) else resolve_delta_dialect(dialect, standard)
    pick_key = choose_pi or dl.default_key_choice
    plan = DeltaPlan(landing=landing, standard=dl.standard_version, backend=dl.name,
                     reference_noun=dl.reference_noun)
    warnings = plan.warnings
    known_dbs = {str(d).lower() for d in ((dictionary or {}).get("databases") or set())}

    # ── new entities → tables (or resumed columns on a half-landed table) ──
    for ent in (authored or {}).get("entities") or []:
        name = str(ent.get("name") or "")
        leaf = _leaf(name)
        if not _IDENT_RE.match(leaf):
            warnings.append(f"{name}: '{leaf}' is not a plain identifier — not landed")
            continue
        props = ent.get("props") or {}
        attrs = [a for a in (ent.get("attributes") or []) if _IDENT_RE.match(_leaf(str(a.get("name") or "")))]
        for a in (ent.get("attributes") or []):
            if not _IDENT_RE.match(_leaf(str(a.get("name") or ""))):
                warnings.append(f"{name}: attribute '{a.get('name')}' is not a plain identifier — skipped")
        key_cols = _key_columns(props)
        col_names = [_leaf(str(a["name"])) for a in attrs]
        # a landed table keeps its identifier (a re-run must find THE SAME table)
        identifier = str(ent.get("deployed") or "") or f"{landing.database}.{leaf}"
        db = _db_of(identifier)
        existing = _dict_table(dictionary, identifier)
        plan.realises[name] = identifier
        pi_cols, pi_rule = pick_key(identifier, [k for k in key_cols if k.lower() in {c.lower() for c in col_names}], col_names)
        plan.pi_rule = pi_rule
        plan.pi_decisions[identifier] = {"columns": list(pi_cols), "rule": pi_rule}
        if existing is None:
            keys_l = {k.lower() for k in key_cols}
            specs = [ColumnSpec(_leaf(str(a["name"])),
                                dl.column_type(a.get("props") or {}, warnings, f"{name}.{_leaf(str(a['name']))}"),
                                _leaf(str(a["name"])).lower() in keys_l) for a in attrs]
            if not specs:
                warnings.append(f"{name}: no attributes — a table needs at least one column; not landed")
                continue
            sql = dl.create_table(db, leaf, specs, list(pi_cols), pi_rule, warnings, name)
            plan.changes.append(DeltaChange("table", identifier, name, sql, PHASE_TABLE))
            if db.lower() not in known_dbs:
                plan.requires_database = True
            if dl.comments_enabled() and comments in ("all", "table") and props.get("description"):
                csql = dl.comment_table(db, leaf, str(props["description"]))
                if csql:
                    plan.changes.append(DeltaChange("comment", identifier, name, csql, PHASE_COMMENT))
            for a in attrs:
                plan.realises[str(a["name"])] = f"{identifier}.{_leaf(str(a['name']))}"
        else:
            plan.changes.append(DeltaChange("table", identifier, name, "", PHASE_TABLE, state="exists",
                                            reason="the table already exists on the target"))
            for a in attrs:
                cname = _leaf(str(a["name"]))
                cid = f"{identifier}.{cname}"
                plan.realises[str(a["name"])] = cid
                if _dict_has_column(existing, cname):
                    plan.changes.append(DeltaChange("column", cid, str(a["name"]), "", PHASE_COLUMN, state="exists",
                                                    reason="the column already exists on the target"))
                else:
                    ctype = dl.column_type(a.get("props") or {}, warnings, f"{name}.{cname}")
                    plan.changes.append(DeltaChange("column", cid, str(a["name"]),
                                                    dl.add_column(db, leaf, cname, ctype), PHASE_COLUMN))
        # the reference to the parent, on the attribute that NAMES the parent's key
        parent = ent.get("parent")
        if parent:
            pf = (facts or {}).get(parent) or {}
            p_dep, p_key = str(pf.get("deployed") or ""), [str(k) for k in (pf.get("key") or [])]
            noun = dl.reference_noun
            # the notes about the reference belong to the table's CREATION (a later landing into
            # it — a new column — changes nothing about the reference; repeated, they read as
            # that change's findings)
            if not p_dep:
                if existing is None:
                    warnings.append(f"{name}: its parent {parent} is not deployed — no {noun} emitted")
            elif not p_key:
                if existing is None:
                    warnings.append(f"{name}: its parent {parent} declares no key — no {noun} emitted")
            elif (_dict_table(dictionary, p_dep) or {}).get("kind") == "view":
                # a reference cannot be declared on a view: the column lands, the model keeps
                # the relationship (the graph's belongs_to), and the warehouse is not asked to fail.
                # Said when the table is CREATED; a later landing into it (a new column) changes
                # nothing about the reference — repeated there, it reads as this change's finding
                if existing is None:
                    warnings.append(f"{name}: its parent {parent} is realised as a view ({p_dep}) — the reference "
                                    f"is recorded in the model; a {noun} cannot be declared on a view")
            else:
                matched = [(c, k) for k in p_key for c in col_names if c.lower() == k.lower()]
                if not matched and existing is None:
                    warnings.append(f"{name}: no attribute names the parent's key ({', '.join(p_key)}) — no {noun} emitted")
                for c, k in matched:
                    fid = f"{identifier}.{c}"
                    if _dict_has_fk(existing, c, p_dep, k):
                        plan.changes.append(DeltaChange("foreign_key", fid, name, "", PHASE_FOREIGN_KEY, state="exists",
                                                        reason="the reference already exists on the target"))
                        continue
                    pdb, ptab = p_dep.partition(".")[0], _leaf(p_dep)
                    plan.changes.append(DeltaChange("foreign_key", fid, name,
                                                    dl.foreign_key(db, leaf, c, pdb, ptab, k), PHASE_FOREIGN_KEY))
                    for ssql in dl.reference_support(db, leaf, c):
                        plan.changes.append(DeltaChange("reference_index", fid, name, ssql, PHASE_REFERENCE_SUPPORT))
        # statistics on the key (+ the reference columns), for a table landed by this plan
        if existing is None and dl.statistics_enabled():
            targets: List[List[str]] = [list(pi_cols)] if pi_cols else []
            for c in col_names:
                if any(ch.kind == "foreign_key" and ch.identifier.endswith("." + c) and ch.node == name
                       for ch in plan.changes) and [c] not in targets:
                    targets.append([c])
            for ssql in dl.statistics(db, leaf, targets):
                plan.changes.append(DeltaChange("statistics", identifier, name, ssql, PHASE_STATISTICS))

    # ── new attributes on BASE entities → ALTER TABLE … ADD ──
    for attr in (authored or {}).get("attributes") or []:
        name = str(attr.get("name") or "")
        cname = _leaf(name)
        base = str(attr.get("entity") or "")
        bf = (facts or {}).get(base) or {}
        dep = str(bf.get("deployed") or "")
        if not _IDENT_RE.match(cname):
            warnings.append(f"{name}: '{cname}' is not a plain identifier — not landed")
            continue
        if not dep:
            plan.changes.append(DeltaChange("column", f"?.{cname}", name, "", PHASE_COLUMN, state="refused",
                                            reason=f"the base entity {base} is not deployed — nothing to add a column to"))
            continue
        cid = f"{dep}.{cname}"
        plan.realises[name] = cid
        tbl = _dict_table(dictionary, dep)
        if tbl is None:
            plan.changes.append(DeltaChange("column", cid, name, "", PHASE_COLUMN, state="refused",
                                            reason=f"{dep} is not on the target's dictionary — the base table is missing"))
            continue
        if _dict_has_column(tbl, cname):
            plan.changes.append(DeltaChange("column", cid, name, "", PHASE_COLUMN, state="exists",
                                            reason="the column already exists on the target"))
            continue
        ctype = dl.column_type(attr.get("props") or {}, warnings, name)
        # NOT NULL only with a default — existing rows exist (§4.3); nothing declares a default
        plan.changes.append(DeltaChange("column", cid, name, dl.add_column(_db_of(dep), _leaf(dep), cname, ctype),
                                        PHASE_COLUMN))

    # ── drops: refused unless forced, and only over the graph's own realisations ──
    for rm in removed or []:
        kind, ident, node = str(rm.get("kind") or ""), str(rm.get("identifier") or ""), str(rm.get("node") or "")
        if kind not in ("table", "column") or not ident:
            continue
        parts = ident.split(".")
        if kind == "table" and len(parts) == 2:
            sql = dl.drop_table(*parts)
        elif kind == "column" and len(parts) == 3:
            sql = dl.drop_column(*parts)
        else:
            continue
        if force_drop:
            plan.changes.append(DeltaChange(f"drop_{kind}", ident, node, sql, PHASE_DROP))
        else:
            plan.changes.append(DeltaChange(f"drop_{kind}", ident, node, "", PHASE_DROP, state="refused",
                                            reason="its authored node is gone; a drop needs force_drop"))
    return plan


# ── the result of an execution (filled by DeploymentBackend.execute_delta) ───

@dataclass
class DeltaResult:
    dry_run: bool = False
    landed: List[Tuple[str, str]] = field(default_factory=list)     # (kind, identifier)
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)     # (identifier, error)
    executed_sql: List[str] = field(default_factory=list)
    success: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"dry_run": self.dry_run, "landed": list(self.landed), "skipped": list(self.skipped),
                "errors": list(self.errors), "success": self.success}


# ── the receipt (model_change) ───────────────────────────────────────────────

def receipt_content(receipt: Dict[str, Any]) -> str:
    """The CLOSED field tuple a model_change receipt is signed over — canonical, so the
    seal cannot be moved by a presentation field (the compute-receipt precedent)."""
    import json
    fields = {k: receipt.get(k) for k in ("receipt_id", "kind", "kg_id", "at", "by", "landing",
                                           "statements", "landed", "skipped", "refused", "errors", "pi_rule",
                                           "standard", "pi_decisions")}
    if receipt.get("backend") is not None:      # C3 — absent on a receipt sealed before it
        fields["backend"] = receipt.get("backend")
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
