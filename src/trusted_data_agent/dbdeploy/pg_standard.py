"""
pg_standard — the PostgreSQL design standard, encoded once (PROP-008 C3).

The second backend's own rules, NOT a translation of the Teradata ones.  Where the two
engines differ the rules differ: PostgreSQL places rows in a heap, so a key is a
PRIMARY KEY constraint rather than a distribution choice; a foreign key is always an
enforced constraint (there is no informational one) and PostgreSQL does not index the
referencing column, so the standard adds that index; statistics are one ANALYZE per
table; a comment is not length-bounded, so it carries the graph's full description;
unquoted names fold to lower case, so every identifier is quoted to keep the model's.

Each rule carries the statements that prove it on a target version; they are executed
by ``scripts/design_rules_verify.py --backend postgresql`` and recorded in
``design_standard_verification_postgresql.json`` — the same discipline as the Teradata
record, a separate file, read only for PostgreSQL targets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .design_standard import (
    MEASURED,
    OPERATOR,
    SCRATCH,
    SCRATCH_CHILD,
    SCRATCH_NOPI,
    VENDOR_DOC,
    DesignRule,
    RuleSet,
    major_version_family,
)


@dataclass(frozen=True)
class PostgresStandard:
    """Tunable knobs for the PostgreSQL delta dialect."""
    timestamp_type: str = "TIMESTAMP WITH TIME ZONE"
    timestamp_ntz_type: str = "TIMESTAMP WITHOUT TIME ZONE"
    boolean_type: str = "BOOLEAN"
    unbounded_string_type: str = "TEXT"
    primary_key_from_key: bool = True
    index_reference_columns: bool = True
    analyze_after_create: bool = True
    comments: bool = True
    version: str = "postgresql/1.0"


DEFAULT_PG_STANDARD = PostgresStandard()

_DECIMAL_RE = re.compile(r"^(?:DECIMAL|NUMERIC|NUMBER)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$", re.I)
_DECIMAL_P_RE = re.compile(r"^(?:DECIMAL|NUMERIC|NUMBER)\s*\(\s*(\d+)\s*\)$", re.I)
_CHARLIKE_RE = re.compile(r"^(VARCHAR|CHAR|STRING|TEXT|CHARACTER\s+VARYING|CHARACTER)\s*(?:\(\s*(\d+)\s*\))?$", re.I)
_COMPLEX_RE = re.compile(r"^(ARRAY|MAP|STRUCT|JSON)\b", re.I)


def map_type_pg(declared: str, standard: PostgresStandard = DEFAULT_PG_STANDARD) -> Tuple[str, Optional[str]]:
    """A declared (Spark/ANSI) type → a PostgreSQL column type.  Returns ``(type, warning)``."""
    t = (declared or "").strip()
    tu = t.upper()
    if tu in ("BOOLEAN", "BOOL"):
        return standard.boolean_type, None
    if tu in ("BIGINT", "LONG"):
        return "BIGINT", None
    if tu in ("INT", "INTEGER"):
        return "INTEGER", None
    if tu in ("SMALLINT", "TINYINT", "BYTEINT"):
        return "SMALLINT", None
    if tu in ("DOUBLE", "DOUBLE PRECISION", "FLOAT"):
        return "DOUBLE PRECISION", None
    if tu == "REAL":
        return "REAL", None
    m = _DECIMAL_RE.match(tu)
    if m:
        return f"NUMERIC({int(m.group(1))},{int(m.group(2))})", None
    m = _DECIMAL_P_RE.match(tu)
    if m:
        return f"NUMERIC({int(m.group(1))})", None
    if tu in ("DECIMAL", "NUMERIC", "NUMBER"):
        return "NUMERIC(18,2)", None
    if tu == "DATE":
        return "DATE", None
    if tu in ("TIMESTAMP", "TIMESTAMP_LTZ", "TIMESTAMPTZ"):
        return standard.timestamp_type, None
    if tu == "TIMESTAMP_NTZ":
        return standard.timestamp_ntz_type, None
    if _COMPLEX_RE.match(tu):
        return "JSONB", (None if tu.startswith("JSON") else f"type '{t}' has no relational equivalent; mapped to JSONB")
    if tu in ("BINARY", "BYTEA", "VARBINARY"):
        return "BYTEA", None
    m = _CHARLIKE_RE.match(tu)
    if m:
        kind, length = m.group(1).upper(), m.group(2)
        if kind in ("CHAR", "CHARACTER"):
            return f"CHAR({int(length) if length else 1})", None
        if length:
            return f"VARCHAR({int(length)})", None
        return standard.unbounded_string_type, None
    return standard.unbounded_string_type, f"unrecognised type '{t}'; mapped to {standard.unbounded_string_type}"


# ── the rules as records ─────────────────────────────────────────────────────
# placeholders: SCRATCH = "schema"."DR_SCRATCH" · SCRATCH_CHILD · SCRATCH_NOPI;
# {{scratch_db}} = the scratch schema, {{scratch_table}} = DR_SCRATCH

_CREATE = (f'CREATE TABLE {SCRATCH} ("k" INTEGER NOT NULL, "f" BOOLEAN, "s" VARCHAR(40), '
           f'"t" TIMESTAMP WITH TIME ZONE, PRIMARY KEY ("k"));')
_CREATE_CHILD = f'CREATE TABLE {SCRATCH_CHILD} ("cid" INTEGER NOT NULL, "k" INTEGER, PRIMARY KEY ("cid"));'
_CREATE_HEAP = f'CREATE TABLE {SCRATCH_NOPI} ("v" TEXT);'

PG_RULES: Tuple[DesignRule, ...] = (
    DesignRule("primary_key_from_key", "the declared key becomes the PRIMARY KEY: uniqueness and NOT NULL enforced "
               "by the constraint, a b-tree index for access by key", VENDOR_DOC,
               (_CREATE, "SELECT contype FROM pg_catalog.pg_constraint WHERE conrelid = "
                         "'\"{{scratch_db}}\".\"{{scratch_table}}\"'::regclass AND contype = 'p';"),
               readback="primary_key", applies="with_key", severity="high"),
    DesignRule("heap_without_key", "a table that declares no key gets no primary key rather than a guessed or "
               "surrogate one: a heap needs no key to store rows, and an invented one claims an identity the "
               "model never declared", VENDOR_DOC,
               (_CREATE_HEAP, f'INSERT INTO {SCRATCH_NOPI} ("v") VALUES (\'x\');'),
               readback="primary_key", applies="without_key", severity="high"),
    DesignRule("key_not_null", "key columns are NOT NULL — a key with a null cannot identify a row", VENDOR_DOC,
               ("SELECT attnotnull FROM pg_catalog.pg_attribute WHERE attrelid = "
                "'\"{{scratch_db}}\".\"{{scratch_table}}\"'::regclass AND attname = 'k';",),
               readback="key_not_null", applies="with_key", severity="high"),
    DesignRule("boolean_native", "a boolean is BOOLEAN — PostgreSQL has the type, so no integer stands in for it",
               VENDOR_DOC, (f'UPDATE {SCRATCH} SET "f" = TRUE WHERE "k" = 1;',), readback="", severity="low"),
    DesignRule("timestamp_with_zone", "a timestamp carries its zone — TIMESTAMP WITH TIME ZONE (the house convention)",
               OPERATOR, (f'UPDATE {SCRATCH} SET "t" = now() WHERE "k" = 1;',), readback="", severity="low"),
    DesignRule("encoding_utf8", "character data is stored in a UTF8 database: PostgreSQL sets the encoding per "
               "database, not per column, so the standard checks the database", VENDOR_DOC,
               ("SELECT pg_catalog.pg_encoding_to_char(encoding) FROM pg_catalog.pg_database "
                "WHERE datname = current_database();",),
               readback="encoding", severity="low"),
    DesignRule("foreign_key_enforced", "a foreign key is an enforced constraint — PostgreSQL has no informational "
               "one; on a new, empty table the check costs nothing and keeps the data honest", VENDOR_DOC,
               (_CREATE_CHILD, f'ALTER TABLE {SCRATCH_CHILD} ADD FOREIGN KEY ("k") REFERENCES {SCRATCH} ("k");',
                "SELECT convalidated FROM pg_catalog.pg_constraint WHERE conrelid = "
                "'\"{{scratch_db}}\".\"DR_CHILD\"'::regclass AND contype = 'f';"),
               readback="foreign_key", applies="with_parent", severity="medium"),
    DesignRule("reference_indexed", "every referencing column is indexed: PostgreSQL does not index the "
               "referencing side of a foreign key, so without one a join and a parent delete scan the child",
               VENDOR_DOC, (f'CREATE INDEX "ix_DR_CHILD_k" ON {SCRATCH_CHILD} ("k");',),
               readback="reference_index", applies="with_parent", severity="medium"),
    DesignRule("analyze_after_create", "a table the platform creates is ANALYZEd: the planner's row estimates come "
               "from the statistics, and a table never analyzed reads as such in the catalogue", VENDOR_DOC,
               (f"ANALYZE {SCRATCH};", "SELECT reltuples FROM pg_catalog.pg_class WHERE oid = "
                                       "'\"{{scratch_db}}\".\"{{scratch_table}}\"'::regclass;"),
               readback="analyzed", severity="low"),
    DesignRule("comment_full", "the table comment carries the graph's full description — PostgreSQL bounds no "
               "comment, so nothing is cut", MEASURED,
               (f"COMMENT ON TABLE {SCRATCH} IS '{'x' * 2000}';",), readback="comment", severity="low"),
    DesignRule("identifiers_quoted", "every identifier is quoted, so the model's case is kept — PostgreSQL folds "
               "an unquoted name to lower case", MEASURED,
               ('CREATE TABLE "{{scratch_db}}"."DR_MixedCase" ("Col" INTEGER);',
                "SELECT relname FROM pg_catalog.pg_class WHERE relname = 'DR_MixedCase';"),
               readback="identifier_case", severity="medium"),
)

PG_VERIFICATION_FILE = Path(__file__).with_name("design_standard_verification_postgresql.json")
PG_RULE_SET = RuleSet("postgresql", DEFAULT_PG_STANDARD.version, PG_RULES, PG_VERIFICATION_FILE, major_version_family,
                      ("primary_key_from_key", "heap_without_key", "foreign_key_enforced"))
