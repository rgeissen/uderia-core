"""
readback — the dictionary read back, rule by rule (PROP-008 A4, §4.4 (3)).

After every deploy and every delta the backend reads the warehouse's own dictionary
(``DeploymentBackend.read_design``) and this module decides, per table and per rule of
the design standard, whether the warehouse HOLDS what the standard asked for:

    held · violated · unverified

in the shape the KG conformance gate reports (status + severity-banded issues), so the
Govern dialog shows one grammar for "does the graph conform" and "does the warehouse
conform".  A hand-made object inside a model database is a ``violated`` row (the model's
database holds something the model did not build), not a silent presence.  A rule the
dictionary cannot show (the backend returned nothing for it) reads ``unverified`` — an
honest third state, never a pass.

What was PLANNED is parsed from the platform's own emitted CREATE statement
(``planned_from_sql``) — one builder for both deploy paths, so the readback can never
disagree with the DDL it checks.  Pure: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .design_standard import DEFAULT_TERADATA_STANDARD, RULES, RULES_BY_ID, TeradataStandard, DesignRule
from .probes import SKEW_THRESHOLD_PCT

HELD, VIOLATED, UNVERIFIED = "held", "violated", "unverified"
CONFORMANT, PARTIAL, NONCONFORMANT = "conformant", "partial", "nonconformant"

_CREATE_RE = re.compile(r'^\s*CREATE\s+(?P<kind>MULTISET|SET)?\s*TABLE\s+"(?P<db>[^"]+)"\."(?P<tb>[^"]+)"\s*,\s*(?P<fb>NO FALLBACK|FALLBACK)',
                        re.I | re.S)
_PI_RE = re.compile(r'(?P<uniq>UNIQUE\s+)?PRIMARY\s+INDEX\s*\(\s*(?P<cols>[^)]*)\)', re.I)
_NOPI_RE = re.compile(r'NO\s+PRIMARY\s+INDEX', re.I)
_COL_RE = re.compile(r'^\s*"(?P<name>[^"]+)"\s+(?P<type>.+?)\s*$', re.M)
_IDENT_LIST_RE = re.compile(r'"([^"]+)"')


@dataclass
class PlannedTable:
    identifier: str
    multiset: bool = True
    fallback: bool = False
    pi_columns: List[str] = field(default_factory=list)
    pi_unique: bool = False
    nopi: bool = False
    key_columns: List[str] = field(default_factory=list)
    columns: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # name → {type, not_null, charset}
    foreign_keys: List[Tuple[str, str, str]] = field(default_factory=list)  # (col, parent_ident, parent_col)
    statistics: List[List[str]] = field(default_factory=list)
    comment: Optional[str] = None
    # a backend whose key is a constraint, not a placement (C3): the PRIMARY KEY columns,
    # the indexes that support each reference, whether the statistics were planned
    primary_key: List[str] = field(default_factory=list)
    reference_indexes: List[List[str]] = field(default_factory=list)
    analyzed: bool = False

    @property
    def keyless(self) -> bool:
        """No key was DECLARED or planned (a declared key demoted to NoPI is still a key)."""
        return not self.pi_columns and not self.primary_key and not self.key_columns

    def to_dict(self) -> Dict[str, Any]:
        return {"identifier": self.identifier, "multiset": self.multiset, "fallback": self.fallback,
                "pi_columns": list(self.pi_columns), "pi_unique": self.pi_unique, "nopi": self.nopi,
                "key_columns": list(self.key_columns), "columns": dict(self.columns),
                "foreign_keys": [list(f) for f in self.foreign_keys], "statistics": [list(s) for s in self.statistics],
                "comment": self.comment, "primary_key": list(self.primary_key),
                "reference_indexes": [list(x) for x in self.reference_indexes], "analyzed": self.analyzed}


def planned_from_sql(create_sql: str, *, key_columns: Sequence[str] = (), fk_sql: Iterable[str] = (),
                     statistics_sql: Iterable[str] = (), comment_sql: Optional[str] = None) -> Optional[PlannedTable]:
    """Read the platform's OWN emitted CREATE (and its sibling statements) back into the
    structure the readback compares against the dictionary."""
    m = _CREATE_RE.match(create_sql or "")
    if not m:
        return None
    p = PlannedTable(identifier=f"{m.group('db')}.{m.group('tb')}", multiset=(m.group("kind") or "").upper() == "MULTISET",
                     fallback=(m.group("fb") or "").upper() == "FALLBACK", key_columns=list(key_columns))
    body = create_sql[m.end():]
    open_i = body.find("(")
    # our own format: the column block closes on a line of its own ("\n)"), the PI clause follows
    end_i = body.find("\n)")
    if end_i < 0:
        end_i = body.rfind(")")
    cols_text = body[open_i + 1: end_i] if open_i >= 0 else ""
    close_i = end_i
    for cm in _COL_RE.finditer(cols_text):
        name, rest = cm.group("name"), cm.group("type").rstrip(",").strip()
        p.columns[name] = {"type": rest.split(" NOT NULL")[0].split(" CHARACTER SET")[0].strip(),
                           "not_null": " NOT NULL" in rest.upper(),
                           "charset": (re.search(r'CHARACTER SET (\w+)', rest, re.I) or [None, None])[1]}
    if not p.key_columns:
        # the planner makes exactly the declared key NOT NULL — without a caller-supplied key the
        # CREATE itself names it (an empty key would make key_not_null hold vacuously)
        p.key_columns = [n for n, c in p.columns.items() if c.get("not_null")]
    tail = body[close_i + 1:] if close_i >= 0 else body
    if _NOPI_RE.search(tail):
        p.nopi = True
    else:
        pm = _PI_RE.search(tail)
        if pm:
            p.pi_columns = _IDENT_LIST_RE.findall(pm.group("cols"))
            p.pi_unique = bool(pm.group("uniq"))
    for s in fk_sql:
        fm = re.search(r'FOREIGN KEY\s*\(\s*"([^"]+)"\s*\)\s*REFERENCES\s+(?:WITH NO CHECK OPTION\s+)?"([^"]+)"\."([^"]+)"\s*\(\s*"([^"]+)"\s*\)', s, re.I)
        if fm:
            p.foreign_keys.append((fm.group(1), f"{fm.group(2)}.{fm.group(3)}", fm.group(4)))
    for s in statistics_sql:
        sm = re.search(r'COLUMN\s*\(\s*([^)]*)\)', s, re.I)
        if sm:
            p.statistics.append(_IDENT_LIST_RE.findall(sm.group(1)))
    if comment_sql:
        cm2 = re.search(r"IS\s+'(.*)'\s*;?\s*$", comment_sql, re.S)
        if cm2:
            p.comment = cm2.group(1).replace("''", "'")
    return p


def planned_from_ddl(plan, key_columns_by_table: Optional[Dict[str, Sequence[str]]] = None,
                     dialect: Any = None) -> Dict[str, PlannedTable]:
    """Every table of a ``DDLPlan`` (or a ``DeltaPlan``'s pending changes) as a PlannedTable,
    read by the dialect that SAID the statements (a ``DeltaPlan`` names its backend)."""
    dl = _dialect(dialect if dialect is not None else getattr(plan, "backend", None))
    stmts = list(plan.ordered()) if hasattr(plan, "ordered") else [c for c in plan.pending()]
    by_ident: Dict[str, Dict[str, List[str]]] = {}
    creates: Dict[str, str] = {}
    for s in stmts:
        kind, ident, sql = s.kind, s.identifier, s.sql
        if kind == "table" and sql:
            creates[ident] = sql
        elif kind in ("foreign_key", "statistics", "comment", "reference_index") and sql:
            tbl = ident.rpartition(".")[0] if kind in ("foreign_key", "reference_index") else ident
            if kind == "comment" and ident.count(".") == 2:
                continue   # a column comment is not the table's
            by_ident.setdefault(tbl, {}).setdefault(kind, []).append(sql)
    out: Dict[str, PlannedTable] = {}
    for ident, sql in creates.items():
        extra = by_ident.get(ident, {})
        pt = dl.planned_from_sql(sql, key_columns=(key_columns_by_table or {}).get(ident, ()),
                                 fk_sql=extra.get("foreign_key", []), statistics_sql=extra.get("statistics", []),
                                 comment_sql=(extra.get("comment") or [None])[0],
                                 support_sql=extra.get("reference_index", []))
        if pt:
            out[ident] = pt
    return out


def _dialect(d: Any):
    """A ``DeltaDialect`` from an instance, a backend name, or nothing (the original target)."""
    if d is not None and not isinstance(d, str):
        return d
    from .delta import resolve_delta_dialect
    return resolve_delta_dialect(d)


# ── the report ───────────────────────────────────────────────────────────────

@dataclass
class ReadbackRow:
    table: str
    rule: str
    status: str            # held | violated | unverified
    detail: str = ""
    severity: str = "low"

    def to_dict(self) -> Dict[str, Any]:
        return {"table": self.table, "rule": self.rule, "status": self.status, "detail": self.detail, "severity": self.severity}


@dataclass
class ReadbackReport:
    standard: str
    system: Optional[str]
    rows: List[ReadbackRow] = field(default_factory=list)
    foreign_objects: List[str] = field(default_factory=list)
    tables_read: int = 0
    tables_unread: List[str] = field(default_factory=list)
    backend: str = "teradata"
    rules_by_id: Dict[str, DesignRule] = field(default_factory=lambda: dict(RULES_BY_ID))

    @property
    def held(self) -> int: return sum(1 for r in self.rows if r.status == HELD)
    @property
    def violated(self) -> int: return sum(1 for r in self.rows if r.status == VIOLATED)
    @property
    def unverified(self) -> int: return sum(1 for r in self.rows if r.status == UNVERIFIED)

    @property
    def status(self) -> str:
        """The conformance gate's grammar: a HIGH violation is nonconformant, any other
        violation partial, else conformant.  ``unverified`` never lowers the status — it is
        counted and listed, because an unread dictionary is not a broken warehouse."""
        v = [r for r in self.rows if r.status == VIOLATED]
        if any(r.severity == "high" for r in v):
            return NONCONFORMANT
        if v:
            return PARTIAL
        # nothing held and nothing violated = nothing was READ: that is not a clean warehouse,
        # it is an unread one (Rule 3.7 — no vacuous clean)
        if not any(r.status == HELD for r in self.rows):
            return UNVERIFIED
        return CONFORMANT

    def issues(self) -> List[Dict[str, Any]]:
        """Violations grouped by rule — code · severity · message · count · refs (the gate's issue shape)."""
        groups: Dict[str, List[ReadbackRow]] = {}
        for r in self.rows:
            if r.status == VIOLATED:
                groups.setdefault(r.rule, []).append(r)
        out = []
        for code, rs in groups.items():
            rule = self.rules_by_id.get(code)
            out.append({"code": code, "severity": rs[0].severity, "count": len(rs),
                        "message": (rule.rationale if rule else code) if code != "foreign_object"
                        else "the model's database holds objects the model did not build",
                        "refs": [f"{r.table}: {r.detail}" for r in rs][:10]})
        out.sort(key=lambda i: ({"high": 0, "medium": 1, "low": 2}.get(i["severity"], 3), i["code"]))
        return out

    def summary(self) -> str:
        """In words: what held, what did not, what could not be read."""
        parts = [f"{self.held} rule check{'s' if self.held != 1 else ''} held"]
        if self.violated:
            parts.append(f"{self.violated} violated")
        if self.unverified:
            parts.append(f"{self.unverified} unverified (the dictionary did not show them)")
        if self.foreign_objects:
            parts.append(f"{len(self.foreign_objects)} object{'s' if len(self.foreign_objects) != 1 else ''} in the model's database the model did not build")
        if self.tables_unread:
            parts.append(f"{len(self.tables_unread)} table{'s' if len(self.tables_unread) != 1 else ''} not read back")
        return " · ".join(parts) + f" — {self.status}"

    def to_dict(self) -> Dict[str, Any]:
        return {"backend": self.backend, "standard": self.standard, "system": self.system, "status": self.status,
                "summary": self.summary(),
                "held": self.held, "violated": self.violated, "unverified": self.unverified,
                "tables_read": self.tables_read, "tables_unread": list(self.tables_unread),
                "foreign_objects": list(self.foreign_objects), "issues": self.issues(),
                "rows": [r.to_dict() for r in self.rows]}


def _applies(rule: DesignRule, planned: PlannedTable) -> bool:
    if rule.applies == "with_key":
        return not planned.keyless
    if rule.applies == "without_key":
        return planned.keyless
    if rule.applies == "with_parent":
        return bool(planned.foreign_keys)
    return True


def _lower(xs: Iterable[str]) -> List[str]:
    return [str(x).strip().lower() for x in xs]


def readback_report(planned: Dict[str, PlannedTable], observed: Dict[str, Dict[str, Any]], *,
                    standard: Optional[TeradataStandard] = None, skew: Optional[Dict[str, Dict[str, Any]]] = None,
                    foreign_objects: Sequence[str] = (), system: Optional[str] = None,
                    threshold_pct: float = SKEW_THRESHOLD_PCT, dialect: Any = None) -> ReadbackReport:
    """Decide, per planned table and per applicable rule, whether the OBSERVED dictionary
    holds the rule.

    ``observed[ident]`` (what ``DeploymentBackend.read_design`` returns):
        ``{"multiset": bool|None, "fallback": bool|None, "pi_columns": [...]|None, "pi_unique": bool|None,
           "nopi": bool|None, "columns": {name: {"type", "nullable": bool|None, "charset": str|None}},
           "foreign_keys": [(col, parent_ident, parent_col)], "statistics": [[cols]]|None, "comment": str|None}``
        — a ``None`` means the backend could not read that fact (→ unverified).
    ``skew[ident]``: ``{"skew_pct": float, "amps": int, "max_rows": int, "avg_rows": float}`` measured on
        the PI (the pattern's statement) — absent → ``pi_even_distribution`` unverified.
    """
    dl = _dialect(dialect)
    if standard is not None and dl.name == "teradata":
        from .dialects.teradata import TeradataDeltaDialect
        dl = TeradataDeltaDialect(standard)
    rs = dl.rule_set()
    rep = ReadbackReport(standard=getattr(standard, "version", None) or dl.standard_version, system=system,
                         foreign_objects=list(foreign_objects), backend=dl.name, rules_by_id=dict(rs.by_id))
    for ident, pt in planned.items():
        obs = (observed or {}).get(ident)
        if obs is None:
            obs = next((v for k, v in (observed or {}).items() if str(k).lower() == ident.lower()), None)
        if obs is None:
            rep.tables_unread.append(ident)
            for rule in rs.rules:
                if rule.readback and _applies(rule, pt):
                    rep.rows.append(ReadbackRow(ident, rule.id, UNVERIFIED, "the table was not read back", rule.severity))
            continue
        rep.tables_read += 1
        for rule in rs.rules:
            if not rule.readback or not _applies(rule, pt):
                continue
            st, why = dl.readback_check(rule, pt, obs, (skew or {}).get(ident), threshold_pct)
            rep.rows.append(ReadbackRow(ident, rule.id, st, why, rule.severity))
    for fo in foreign_objects:
        rep.rows.append(ReadbackRow(fo, "foreign_object", VIOLATED, "not built by the model", "high"))
    return rep


def _check(rule: DesignRule, pt: PlannedTable, obs: Dict[str, Any], skew: Optional[Dict[str, Any]],
           std: TeradataStandard, threshold_pct: float) -> Tuple[str, str]:
    k = rule.readback
    if k == "multiset":
        v = obs.get("multiset")
        if v is None: return UNVERIFIED, "the dictionary did not say SET or MULTISET"
        want = (std.table_kind.upper() == "MULTISET")
        return (HELD, "MULTISET" if want else "SET") if v == want else (VIOLATED, f"the table is {'MULTISET' if v else 'SET'}, the standard says {std.table_kind}")
    if k == "fallback":
        v = obs.get("fallback")
        if v is None: return UNVERIFIED, "protection type not read"
        want = std.fallback.upper() == "FALLBACK"
        return (HELD, std.fallback) if v == want else (VIOLATED, f"the table has {'FALLBACK' if v else 'NO FALLBACK'}, the standard says {std.fallback}")
    if k == "primary_index":
        cols, uniq, nopi = obs.get("pi_columns"), obs.get("pi_unique"), obs.get("nopi")
        if cols is None and nopi is None: return UNVERIFIED, "the primary index was not read"
        if pt.nopi:
            return (HELD, "NO PRIMARY INDEX") if (nopi or not cols) else (VIOLATED, f"a primary index on {', '.join(cols or [])} where the standard says NO PRIMARY INDEX (no key declared)")
        if nopi or not cols:
            return VIOLATED, f"NO PRIMARY INDEX where the standard planned {', '.join(pt.pi_columns)}"
        if _lower(cols) != _lower(pt.pi_columns):
            return VIOLATED, f"primary index on {', '.join(cols)}, planned {', '.join(pt.pi_columns)}"
        if uniq is not None and bool(uniq) != bool(pt.pi_unique):
            return VIOLATED, f"the primary index is {'unique' if uniq else 'not unique'}, planned {'unique' if pt.pi_unique else 'non-unique'}"
        return HELD, f"{'unique ' if pt.pi_unique else ''}primary index on {', '.join(cols)}"
    if k == "key_not_null":
        cols = obs.get("columns") or {}
        if not cols: return UNVERIFIED, "columns not read"
        bad = [c for c in pt.key_columns if (cols.get(c) or next((v for n, v in cols.items() if n.lower() == c.lower()), {}) or {}).get("nullable") is True]
        unknown = [c for c in pt.key_columns if (cols.get(c) or next((v for n, v in cols.items() if n.lower() == c.lower()), {}) or {}).get("nullable") is None]
        if bad: return VIOLATED, f"key column(s) nullable: {', '.join(bad)}"
        if unknown and len(unknown) == len(pt.key_columns): return UNVERIFIED, "nullability not read"
        return HELD, f"key NOT NULL: {', '.join(pt.key_columns)}"
    if k == "charset":
        cols = obs.get("columns") or {}
        chars = {n: v for n, v in pt.columns.items() if v.get("charset")}
        if not chars: return HELD, "no character column"
        if not cols: return UNVERIFIED, "columns not read"
        bad, unknown = [], []
        for n, v in chars.items():
            o = cols.get(n) or next((x for m, x in cols.items() if m.lower() == n.lower()), {}) or {}
            if o.get("charset") is None: unknown.append(n)
            elif str(o["charset"]).upper() != str(v["charset"]).upper(): bad.append(f"{n} is {o['charset']}, planned {v['charset']}")
        if bad: return VIOLATED, "; ".join(bad)
        if unknown and len(unknown) == len(chars): return UNVERIFIED, "character sets not read"
        return HELD, f"{len(chars)} character column(s) as planned"
    if k == "soft_ri":
        fks = obs.get("foreign_keys")
        if fks is None: return UNVERIFIED, "referential constraints not read"
        have = {(str(a).lower(), str(b).lower(), str(c).lower()) for a, b, c in fks}
        missing = [f for f in pt.foreign_keys if (f[0].lower(), f[1].lower(), f[2].lower()) not in have]
        return (HELD, f"{len(pt.foreign_keys)} soft reference(s) present") if not missing else (VIOLATED, "missing: " + ", ".join(f"{a} → {b}({c})" for a, b, c in missing))
    if k == "statistics":
        st = obs.get("statistics")
        if not pt.statistics: return HELD, "no statistics planned"
        if st is None: return UNVERIFIED, "statistics not read"
        have = {tuple(_lower(s)) for s in st}
        missing = [s for s in pt.statistics if tuple(_lower(s)) not in have]
        return (HELD, f"{len(pt.statistics)} statistic(s) collected") if not missing else (VIOLATED, "not collected on: " + ", ".join("(" + ", ".join(s) + ")" for s in missing))
    if k == "comment":
        if pt.comment is None: return HELD, "no comment planned"
        c = obs.get("comment")
        if c is None: return UNVERIFIED, "comment not read"
        if len(str(c)) > std.comment_max_chars: return VIOLATED, f"comment is {len(str(c))} characters, the bound is {std.comment_max_chars}"
        return (HELD, "comment present, within bound") if str(c).strip() == str(pt.comment).strip() else (VIOLATED, "the comment differs from the planned text")
    if k == "pi_skew":
        if pt.nopi or not pt.pi_columns: return HELD, "NO PRIMARY INDEX distributes evenly by construction"
        if not skew or skew.get("skew_pct") is None: return UNVERIFIED, "distribution not measured"
        pct = float(skew["skew_pct"])
        rows = skew.get("total_rows")
        if rows is not None and int(rows) < 1: return UNVERIFIED, "no rows to measure"
        if pct > threshold_pct:
            return VIOLATED, f"the fullest AMP holds {pct:.0f}% more than the average on {', '.join(pt.pi_columns)} (threshold {threshold_pct:.0f}%)"
        return HELD, f"even: fullest AMP {pct:.1f}% above the average"
    if k == "pi_unique":
        if not pt.pi_unique: return HELD, "a non-unique primary index claims no uniqueness"
        v = obs.get("pi_unique_in_data")
        if v is None: return UNVERIFIED, "uniqueness not measured"
        return (HELD, "unique in the data") if v else (VIOLATED, "the unique primary index is not unique in the data")
    return UNVERIFIED, f"no readback for {k}"
