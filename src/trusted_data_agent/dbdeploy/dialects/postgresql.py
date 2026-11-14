"""
PostgresDeltaDialect — an authored-layer delta, said in PostgreSQL (PROP-008 C3).

The planner (``delta.plan_delta``) decides what lands; this module says it the way the
PostgreSQL standard (``pg_standard``) asks: the declared key is a PRIMARY KEY, a table
without one is a plain heap, a reference is an enforced FOREIGN KEY with an index on the
referencing column, statistics are one ANALYZE, a comment carries the full description,
every identifier is quoted.  It also reads its own statements back (``planned_from_sql``)
and decides each PostgreSQL rule against the catalogue (``readback_check``).

Names it invents (a constraint, an index) are derived from the table and column and
bounded to PostgreSQL's 63-byte identifier, so a re-run names the same object.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..pg_standard import DEFAULT_PG_STANDARD, PG_RULE_SET, PostgresStandard, map_type_pg
from .base import ColumnSpec, DeltaDialect, literal, qualified, quote

INFRA = "postgresql"
NAME_MAX = 63          # NAMEDATALEN - 1: a longer name is silently truncated by the server

_CREATE_RE = re.compile(r'^\s*CREATE\s+TABLE\s+"(?P<db>(?:[^"]|"")+)"\."(?P<tb>(?:[^"]|"")+)"\s*\(', re.I)
_COL_RE = re.compile(r'^\s*"(?P<name>(?:[^"]|"")+)"\s+(?P<type>.+?)\s*,?\s*$')
_PK_RE = re.compile(r'^\s*PRIMARY\s+KEY\s*\((?P<cols>[^)]*)\)\s*$', re.I)
_IDENTS_RE = re.compile(r'"((?:[^"]|"")+)"')
_FK_RE = re.compile(r'FOREIGN\s+KEY\s*\(\s*"([^"]+)"\s*\)\s*REFERENCES\s+"([^"]+)"\."([^"]+)"\s*\(\s*"([^"]+)"\s*\)', re.I)
_IX_RE = re.compile(r'CREATE\s+INDEX\s+"[^"]+"\s+ON\s+"[^"]+"\."[^"]+"\s*\((?P<cols>[^)]*)\)', re.I)


def bounded_name(prefix: str, table: str, column: str) -> str:
    """``fk_<table>_<column>`` within 63 bytes; a longer one keeps a stable hash suffix."""
    name = f"{prefix}_{table}_{column}"
    if len(name.encode("utf-8")) <= NAME_MAX:
        return name
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return name.encode("utf-8")[: NAME_MAX - 9].decode("utf-8", "ignore") + "_" + h


class PostgresDeltaDialect(DeltaDialect):
    name = INFRA
    reference_noun = "foreign key"
    key_places_rows = False

    def __init__(self, standard: Optional[PostgresStandard] = None):
        # a caller passing the OTHER backend's standard object gets this backend's own
        self.standard = standard if isinstance(standard, PostgresStandard) else DEFAULT_PG_STANDARD

    @property
    def standard_version(self) -> str:
        return self.standard.version

    def comments_enabled(self) -> bool:
        return bool(self.standard.comments)

    def statistics_enabled(self) -> bool:
        return bool(self.standard.analyze_after_create)

    def column_type(self, props, warnings, label):
        declared = str((props or {}).get("data_type") or "").strip()
        if not declared:
            warnings.append(f"{label}: no data type declared — {self.standard.unbounded_string_type}")
            return self.standard.unbounded_string_type
        if (props or {}).get("type_inferred"):
            warnings.append(f"{label}: {declared} inferred from its name ({props['type_inferred']}) — confirm before landing")
        t, warn = map_type_pg(declared, self.standard)
        if warn:
            warnings.append(f"{label}: {warn}")
        return t

    def create_table(self, db, table, columns, key_columns, key_rule, warnings, node):
        lines = [f"    {quote(c.name)} {c.type}" + (" NOT NULL" if c.not_null else "") for c in columns]
        if key_columns and self.standard.primary_key_from_key:
            lines.append(f"    PRIMARY KEY ( {', '.join(quote(c) for c in key_columns)} )")
        elif not key_columns:
            warnings.append(f"{node}: no declared key among its attributes — no primary key (a heap)")
        return f"CREATE TABLE {qualified(db, table)}\n(\n" + ",\n".join(lines) + "\n);"

    def comment_table(self, db, table, description):
        return f"COMMENT ON TABLE {qualified(db, table)} IS '{literal(str(description).strip())}';"

    def add_column(self, db, table, column, ctype):
        return f"ALTER TABLE {qualified(db, table)} ADD COLUMN {quote(column)} {ctype};"

    def foreign_key(self, db, table, column, parent_db, parent_table, parent_column):
        return (f"ALTER TABLE {qualified(db, table)} ADD CONSTRAINT {quote(bounded_name('fk', table, column))} "
                f"FOREIGN KEY ( {quote(column)} ) REFERENCES {qualified(parent_db, parent_table)} ( {quote(parent_column)} );")

    def reference_support(self, db, table, column):
        if not self.standard.index_reference_columns:
            return []
        return [f"CREATE INDEX {quote(bounded_name('ix', table, column))} ON {qualified(db, table)} ( {quote(column)} );"]

    def statistics(self, db, table, column_sets):
        return [f"ANALYZE {qualified(db, table)};"]

    def drop_table(self, db, table):
        return f"DROP TABLE {qualified(db, table)};"

    def drop_column(self, db, table, column):
        return f"ALTER TABLE {qualified(db, table)} DROP COLUMN {quote(column)};"

    def keyless_finding(self):
        return "heap_without_key", "no declared key — the table lands without a primary key"

    # ── reading its own statements back ──
    def planned_from_sql(self, create_sql, *, key_columns=(), fk_sql=(), statistics_sql=(), comment_sql=None,
                         support_sql=()):
        from ..readback import PlannedTable
        m = _CREATE_RE.match(create_sql or "")
        if not m:
            return None
        db, tb = m.group("db").replace('""', '"'), m.group("tb").replace('""', '"')
        p = PlannedTable(identifier=f"{db}.{tb}", multiset=False, fallback=False, key_columns=list(key_columns))
        body = (create_sql or "")[m.end():]
        end = body.rfind("\n);")
        for line in (body[:end] if end >= 0 else body).splitlines():
            pk = _PK_RE.match(line.rstrip(","))
            if pk:
                p.primary_key = [x.replace('""', '"') for x in _IDENTS_RE.findall(pk.group("cols"))]
                continue
            cm = _COL_RE.match(line)
            if cm:
                rest = cm.group("type").rstrip(",").strip()
                p.columns[cm.group("name").replace('""', '"')] = {
                    "type": re.sub(r"\s+NOT NULL$", "", rest, flags=re.I).strip(),
                    "not_null": rest.upper().endswith(" NOT NULL"), "charset": None}
        if not p.key_columns:
            p.key_columns = list(p.primary_key) or [n for n, c in p.columns.items() if c.get("not_null")]
        for s in fk_sql:
            fm = _FK_RE.search(s or "")
            if fm:
                p.foreign_keys.append((fm.group(1), f"{fm.group(2)}.{fm.group(3)}", fm.group(4)))
        for s in support_sql:
            im = _IX_RE.search(s or "")
            if im:
                p.reference_indexes.append(_IDENTS_RE.findall(im.group("cols")))
        p.analyzed = any(re.match(r"^\s*ANALYZE\b", s or "", re.I) for s in statistics_sql)
        if comment_sql:
            cm2 = re.search(r"IS\s+'(.*)'\s*;?\s*$", comment_sql, re.S)
            if cm2:
                p.comment = cm2.group(1).replace("''", "'")
        return p

    def rule_set(self):
        return PG_RULE_SET

    def readback_check(self, rule, pt, obs, skew, threshold_pct):
        from ..readback import HELD, UNVERIFIED, VIOLATED
        k = rule.readback

        def col(name: str) -> Dict[str, Any]:
            cols = obs.get("columns") or {}
            return cols.get(name) or next((v for n, v in cols.items() if n.lower() == name.lower()), {}) or {}

        if k == "primary_key":
            have = obs.get("primary_key")
            if have is None:
                return UNVERIFIED, "the primary key was not read"
            if not pt.primary_key:
                return (HELD, "no primary key (no key declared)") if not have else \
                    (VIOLATED, f"a primary key on {', '.join(have)} where the standard planned none")
            if [c.lower() for c in have] != [c.lower() for c in pt.primary_key]:
                return VIOLATED, (f"primary key on {', '.join(have)}, planned {', '.join(pt.primary_key)}"
                                  if have else f"no primary key where the standard planned {', '.join(pt.primary_key)}")
            return HELD, f"primary key on {', '.join(have)}"
        if k == "key_not_null":
            if not obs.get("columns"):
                return UNVERIFIED, "columns not read"
            bad = [c for c in pt.key_columns if col(c).get("nullable") is True]
            unknown = [c for c in pt.key_columns if col(c).get("nullable") is None]
            if bad:
                return VIOLATED, f"key column(s) nullable: {', '.join(bad)}"
            if unknown and len(unknown) == len(pt.key_columns):
                return UNVERIFIED, "nullability not read"
            return HELD, f"key NOT NULL: {', '.join(pt.key_columns)}"
        if k == "encoding":
            enc = obs.get("encoding")
            if enc is None:
                return UNVERIFIED, "the database encoding was not read"
            return (HELD, "UTF8") if str(enc).upper() == "UTF8" else (VIOLATED, f"the database encoding is {enc}, the standard says UTF8")
        if k == "foreign_key":
            fks = obs.get("foreign_keys")
            if fks is None:
                return UNVERIFIED, "foreign keys not read"
            have = {(str(a).lower(), str(b).lower(), str(c).lower()) for a, b, c in fks}
            missing = [f for f in pt.foreign_keys if (f[0].lower(), f[1].lower(), f[2].lower()) not in have]
            return (HELD, f"{len(pt.foreign_keys)} foreign key(s) present") if not missing else \
                (VIOLATED, "missing: " + ", ".join(f"{a} → {b}({c})" for a, b, c in missing))
        if k == "reference_index":
            ix = obs.get("indexes")
            if ix is None:
                return UNVERIFIED, "indexes not read"
            leading = {str(i[0]).lower() for i in ix if i}
            missing = [f[0] for f in pt.foreign_keys if f[0].lower() not in leading]
            return (HELD, f"every referencing column indexed ({len(pt.foreign_keys)})") if not missing else \
                (VIOLATED, "no index leads with: " + ", ".join(missing))
        if k == "analyzed":
            if not pt.analyzed:
                return HELD, "no statistics planned"
            v = obs.get("analyzed")
            if v is None:
                return UNVERIFIED, "statistics state not read"
            return (HELD, "analyzed") if v else (VIOLATED, "the table was never analyzed")
        if k == "comment":
            if pt.comment is None:
                return HELD, "no comment planned"
            c = obs.get("comment")
            if c is None:
                return UNVERIFIED, "comment not read"
            return (HELD, f"the full description ({len(str(c))} characters)") if str(c).strip() == str(pt.comment).strip() \
                else (VIOLATED, "the comment differs from the planned text")
        if k == "identifier_case":
            exact = obs.get("exact_columns")
            if exact is None or obs.get("name_exact") is None:
                return UNVERIFIED, "names not read"
            if not obs.get("name_exact"):
                return VIOLATED, "the table's name was folded"
            folded = [n for n in pt.columns if n not in set(exact)]
            return (HELD, "the model's names, case kept") if not folded else (VIOLATED, "folded: " + ", ".join(folded))
        return UNVERIFIED, f"no readback for {k}"
