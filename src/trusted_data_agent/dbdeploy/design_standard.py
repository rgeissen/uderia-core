"""
design_standard — the Teradata PDM design-standard ruleset, encoded once.

This is the single source of truth for the Teradata realization decisions
(datatype map, PI rule, table options, charset, RI, statistics, comments).  It
is grounded in:

  * House conventions — agent_packs/andp/base_content.md "Universal DDL Conventions"
    (BYTEINT booleans, TIMESTAMP(6) WITH TIME ZONE, COMMENT mandatory, etc.)
  * Web-grounded best practice — Teradata Vantage docs (PI selection criteria),
    dwhpro (UPI default, LATIN charset, MULTISET), Brad Todd (MULTISET/NO FALLBACK,
    COLLECT STATISTICS).

It can be overridden by ``Advocated_Data_Management_Standards.md`` by constructing
a ``TeradataStandard`` with different field values.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class TeradataStandard:
    """Tunable knobs for the Teradata dialect emitter."""

    # String handling
    varchar_default_len: int = 4000
    varchar_max_before_clob: int = 32000
    charset: str = "LATIN"

    # Temporal
    timestamp_type: str = "TIMESTAMP(6) WITH TIME ZONE"
    timestamp_ntz_type: str = "TIMESTAMP(6)"

    # Booleans (house convention)
    boolean_type: str = "BYTEINT"
    boolean_not_null_default: bool = True   # BYTEINT NOT NULL DEFAULT 0

    # Table options
    table_kind: str = "MULTISET"            # CREATE MULTISET TABLE
    fallback: str = "NO FALLBACK"

    # Primary index
    unique_pi_when_pk: bool = True          # UPI from declared PK; NoPI when no PK

    # Referential integrity
    soft_ri: bool = True                    # REFERENCES WITH NO CHECK OPTION

    # Post-deploy
    collect_statistics: bool = True
    comments: bool = True
    comment_max_chars: int = 255   # Teradata COMMENT limit (Error 5550 beyond this)

    # CREATE DATABASE
    default_perm_bytes: int = 10_000_000_000  # 10 GB; overridden by deploy config

    # The standard's VERSION (PROP-008 A4, §4.4): stamped on every DeployResult, every
    # DeltaPlan/receipt and every physical node a deploy writes, so a graph records what
    # built it and the trust layer can bind it.  Bump when a RULE changes, not when a knob
    # is tuned (a knob is a deployment choice; a rule is the standard).
    version: str = "teradata/1.0"


DEFAULT_TERADATA_STANDARD = TeradataStandard()
STANDARD_VERSION = DEFAULT_TERADATA_STANDARD.version


# ── type mapping ─────────────────────────────────────────────────────────────

_DECIMAL_RE = re.compile(r"^(?:DECIMAL|NUMERIC|NUMBER)\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$", re.I)
_DECIMAL_P_RE = re.compile(r"^(?:DECIMAL|NUMERIC|NUMBER)\s*\(\s*(\d+)\s*\)$", re.I)
_CHARLIKE_RE = re.compile(r"^(VARCHAR|CHAR|STRING|CHARACTER\s+VARYING|CHARACTER)\s*(?:\(\s*(\d+)\s*\))?$", re.I)
_COMPLEX_RE = re.compile(r"^(ARRAY|MAP|STRUCT)\b", re.I)


def map_type(
    dbx_type: str,
    *,
    is_flag: bool,
    standard: TeradataStandard,
) -> Tuple[str, bool, Optional[str]]:
    """Map a Databricks/Spark type string to a Teradata column type.

    Returns ``(teradata_type, is_boolean, warning)``.  ``is_boolean`` lets the
    emitter apply the NOT NULL DEFAULT 0 boolean convention.  ``warning`` is set
    for unsupported source types (ARRAY/MAP/STRUCT/BINARY) that are mapped to a
    fallback.
    """
    t = (dbx_type or "").strip()
    tu = t.upper()

    # Booleans
    if tu in ("BOOLEAN", "BOOL"):
        return standard.boolean_type, True, None

    # Integers
    if tu in ("BIGINT", "LONG"):
        return "BIGINT", False, None
    if tu in ("INT", "INTEGER"):
        return "INTEGER", False, None
    if tu == "SMALLINT":
        return "SMALLINT", False, None
    if tu in ("TINYINT", "BYTEINT"):
        return "BYTEINT", False, None

    # Floats
    if tu in ("DOUBLE", "DOUBLE PRECISION", "FLOAT", "REAL"):
        return "FLOAT", False, None

    # Decimals
    m = _DECIMAL_RE.match(tu)
    if m:
        return f"DECIMAL({int(m.group(1))},{int(m.group(2))})", False, None
    m = _DECIMAL_P_RE.match(tu)
    if m:
        return f"DECIMAL({int(m.group(1))})", False, None
    if tu in ("DECIMAL", "NUMERIC", "NUMBER"):
        return "DECIMAL(18,2)", False, None

    # Temporal
    if tu == "DATE":
        return "DATE", False, None
    if tu in ("TIMESTAMP", "TIMESTAMP_LTZ"):
        return standard.timestamp_type, False, None
    if tu == "TIMESTAMP_NTZ":
        return standard.timestamp_ntz_type, False, None

    # Complex / unsupported
    if _COMPLEX_RE.match(tu):
        return "JSON", False, f"type '{t}' has no native Teradata equivalent; mapped to JSON"
    if tu == "BINARY":
        return f"VARBYTE({standard.varchar_default_len})", False, f"type '{t}' mapped to VARBYTE"

    # Character-like
    m = _CHARLIKE_RE.match(tu)
    if m:
        kind = m.group(1).upper()
        length = m.group(2)
        if kind == "CHAR" or kind == "CHARACTER":
            n = int(length) if length else 1
            return f"CHAR({n}) CHARACTER SET {standard.charset}", False, None
        # STRING / VARCHAR / CHARACTER VARYING
        n = int(length) if length else standard.varchar_default_len
        if n > standard.varchar_max_before_clob:
            return "CLOB", False, None
        return f"VARCHAR({n}) CHARACTER SET {standard.charset}", False, None

    # Fallback
    return (
        f"VARCHAR({standard.varchar_default_len}) CHARACTER SET {standard.charset}",
        False,
        f"unrecognised type '{t}'; mapped to VARCHAR({standard.varchar_default_len})",
    )


# ── the rules as RECORDS (PROP-008 A4 §4.4 (1)) ──────────────────────────────
#
# ``TeradataStandard`` stays the API the emitters read; behind it each decision is a
# ``DesignRule`` with its rationale, its SOURCE (vendor_doc · measured · operator) and the
# statement that VERIFIES it executes on a target version.  A rule with no executed
# verification on the target version reads ``unverified`` in the deploy report — the
# teradata-patterns discipline (a claim about a database is checked on the database)
# applied to the DDL the platform itself emits.  The verification record lives beside
# this module (``design_standard_verification.json``, written by
# ``scripts/design_rules_verify.py --write``) in the shape teradata-patterns uses:
# system · on · sha256 per statement.
#
# ``readback`` names the readback check (``readback.py``) that decides, after a deploy,
# whether the warehouse HOLDS the rule.  ``applies`` says when the rule is in force so
# the readback never reports a rule the standard did not ask for (``nopi_without_key``
# applies only to a table without a key).

SCRATCH = "{{scratch}}"          # the verifier's scratch table (db.table) — substituted at run time
SCRATCH_CHILD = "{{scratch_child}}"
SCRATCH_NOPI = "{{scratch_nopi}}"

VENDOR_DOC, MEASURED, OPERATOR = "vendor_doc", "measured", "operator"
RULE_SOURCES = (VENDOR_DOC, MEASURED, OPERATOR)


@dataclass(frozen=True)
class DesignRule:
    id: str
    rationale: str
    source: str                                   # vendor_doc | measured | operator
    verification: Tuple[str, ...] = ()           # statements that prove the rule on a target version
    readback: str = ""                            # readback.py check key ("" = not readable back)
    applies: str = "always"                       # always | with_key | without_key | with_parent
    severity: str = "high"                        # what a VIOLATED readback row weighs: high | medium | low

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "rationale": self.rationale, "source": self.source,
                "readback": self.readback, "applies": self.applies, "severity": self.severity,
                "verification": [statement_sha(v) for v in self.verification]}


def statement_sha(sql: str) -> str:
    """One hash for one statement, whitespace-normalised — the teradata-patterns rule."""
    return hashlib.sha256(" ".join(str(sql).split()).encode("utf-8")).hexdigest()


_CREATE_SCRATCH = (f'CREATE MULTISET TABLE {SCRATCH}, NO FALLBACK ("k" INTEGER NOT NULL, "f" BYTEINT NOT NULL DEFAULT 0, '
                   f'"s" VARCHAR(40) CHARACTER SET LATIN, "t" TIMESTAMP(6) WITH TIME ZONE) UNIQUE PRIMARY INDEX ("k");')
_CREATE_CHILD = (f'CREATE MULTISET TABLE {SCRATCH_CHILD}, NO FALLBACK ("cid" INTEGER NOT NULL, "k" INTEGER) '
                 f'UNIQUE PRIMARY INDEX ("cid");')
_CREATE_NOPI = f'CREATE MULTISET TABLE {SCRATCH_NOPI}, NO FALLBACK ("v" VARCHAR(10) CHARACTER SET LATIN) NO PRIMARY INDEX;'

RULES: Tuple[DesignRule, ...] = (
    DesignRule("multiset", "MULTISET tables skip the duplicate-row check on insert; a SET table pays it on "
               "every row and the model's keys already carry uniqueness", VENDOR_DOC,
               (_CREATE_SCRATCH, f'INSERT INTO {SCRATCH} ("k","f","s") VALUES (1, 1, \'a\');'),
               readback="multiset", severity="high"),
    DesignRule("no_fallback", "NO FALLBACK: a demo or analytic model does not pay double storage for a copy "
               "the platform can regenerate", VENDOR_DOC,
               (f"SELECT ProtectionType FROM DBC.TablesV WHERE DatabaseName = '{{scratch_db}}' AND TableName = '{{scratch_table}}';",),
               readback="fallback", severity="medium"),
    DesignRule("upi_from_key", "the declared key becomes the UNIQUE PRIMARY INDEX: hash distribution on the "
               "identifier, uniqueness enforced by the index, single-AMP access by key", VENDOR_DOC,
               (f"SELECT IndexType, UniqueFlag, ColumnName FROM DBC.IndicesV WHERE DatabaseName = '{{scratch_db}}' "
                f"AND TableName = '{{scratch_table}}' AND IndexType = 'P';",),
               readback="primary_index", applies="with_key", severity="high"),
    DesignRule("nopi_without_key", "a table that declares no key gets NO PRIMARY INDEX rather than a guessed "
               "one: an arbitrary PI skews, a NoPI table distributes evenly", VENDOR_DOC,
               (_CREATE_NOPI, f'INSERT INTO {SCRATCH_NOPI} ("v") VALUES (\'x\');'),
               readback="primary_index", applies="without_key", severity="high"),
    DesignRule("key_not_null", "key columns are NOT NULL — a key with a null cannot identify a row",
               VENDOR_DOC, (f"SELECT Nullable FROM DBC.ColumnsV WHERE DatabaseName = '{{scratch_db}}' "
                            f"AND TableName = '{{scratch_table}}' AND ColumnName = 'k';",),
               readback="key_not_null", applies="with_key", severity="high"),
    DesignRule("boolean_byteint", "a boolean is BYTEINT NOT NULL DEFAULT 0 (Teradata has no BOOLEAN type; "
               "the house convention)", OPERATOR,
               (f'UPDATE {SCRATCH} SET "f" = 0 WHERE "k" = 1;',), readback="", severity="low"),
    DesignRule("charset_latin", "character columns are LATIN unless the data needs UNICODE: half the bytes, "
               "and the model's identifiers and codes are ASCII", VENDOR_DOC,
               (f"SELECT CharType FROM DBC.ColumnsV WHERE DatabaseName = '{{scratch_db}}' "
                f"AND TableName = '{{scratch_table}}' AND ColumnName = 's';",),
               readback="charset", severity="low"),
    DesignRule("timestamp_tz", "a timestamp carries its zone — TIMESTAMP(6) WITH TIME ZONE (the house convention)",
               OPERATOR, (f'UPDATE {SCRATCH} SET "t" = CURRENT_TIMESTAMP WHERE "k" = 1;',), readback="", severity="low"),
    DesignRule("soft_ri", "foreign keys are soft (WITH NO CHECK OPTION): the optimizer learns the join, "
               "the load is not slowed by a check the model's loader already made", VENDOR_DOC,
               (_CREATE_CHILD, f'ALTER TABLE {SCRATCH_CHILD} ADD FOREIGN KEY ("k") REFERENCES WITH NO CHECK OPTION {SCRATCH} ("k");'),
               readback="soft_ri", applies="with_parent", severity="medium"),
    DesignRule("collect_statistics", "statistics on the primary index and every foreign key, collected after the "
               "load — the optimizer's row estimates come from them", VENDOR_DOC,
               (f'COLLECT STATISTICS COLUMN ("k") ON {SCRATCH};',), readback="statistics", severity="low"),
    DesignRule("comment_bounded", "a COMMENT is a bounded projection of the graph's description (255 characters — "
               "Error 5550 beyond); the graph holds the full text", MEASURED,
               (f"COMMENT ON TABLE {SCRATCH} IS '{'x' * 255}';",), readback="comment", severity="low"),
    DesignRule("pi_even_distribution", "a primary index spreads rows evenly across AMPs: the fullest AMP within the "
               "threshold of the average (the verified pi_skew_detection pattern); a constant-column key "
               "puts every row on one AMP", MEASURED,
               (f'SELECT HASHAMP(HASHBUCKET(HASHROW("k"))) AS amp_no, COUNT(*) AS row_count FROM {SCRATCH} GROUP BY 1;',
                "SELECT HASHAMP() + 1 AS total_amps;"),
               readback="pi_skew", applies="with_key", severity="high"),
    DesignRule("pi_unique", "a UNIQUE primary index is unique in the data: the declared key identifies a row "
               "(source_validation's key probe)", MEASURED,
               (f'SELECT COUNT(*) AS groups_n FROM (SELECT "k" FROM {SCRATCH} GROUP BY "k") x;',),
               readback="pi_unique", applies="with_key", severity="high"),
)
RULES_BY_ID: Dict[str, DesignRule] = {r.id: r for r in RULES}

VERIFICATION_FILE = Path(__file__).with_name("design_standard_verification.json")


def load_verification(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    """The executed-verification record: ``{"standard", "system", "on", "rules": {id: {"statements": [sha…]}}}``.
    Missing or unreadable → ``{}`` (every rule reads unverified — never a silent pass)."""
    try:
        with open(path or VERIFICATION_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def rule_verification_status(rule: DesignRule, record: Dict[str, Any], system: Optional[str] = None,
                             family=None) -> Tuple[str, str]:
    """``verified`` when every one of the rule's statements was executed (sha-exact) on a
    system of the SAME version family; else ``unverified`` with the reason.  A rule with no
    verification statement is ``unverified · no statement``.  ``family`` is the rule set's
    notion of "the same version" (a patch release never re-verifies a rule)."""
    fam = family or _version_family
    if not rule.verification:
        return "unverified", "the rule declares no verification statement"
    rec = ((record or {}).get("rules") or {}).get(rule.id) or {}
    done = set(rec.get("statements") or [])
    want = [statement_sha(v) for v in rule.verification]
    if not done:
        return "unverified", "never executed on a target"
    missing = [w for w in want if w not in done]
    if missing:
        return "unverified", f"{len(missing)} of {len(want)} statement(s) changed since the recorded run"
    rec_sys = str((record or {}).get("system") or "")
    if system and rec_sys and fam(rec_sys) != fam(system):
        return "unverified", f"verified on {rec_sys}, this target is {system}"
    return "verified", f"executed on {rec_sys or 'the recorded system'} {((record or {}).get('on') or '').strip()}".strip()


def _version_family(system: str) -> str:
    """``Teradata 20.00.24.60`` → ``Teradata 20.00`` — a patch release does not re-verify a rule."""
    m = re.match(r"^\s*([A-Za-z]+)\s+(\d+)(?:\.(\d+))?", str(system or ""))
    if not m:
        return str(system or "").strip().lower()
    return f"{m.group(1).lower()} {m.group(2)}.{m.group(3) or '0'}"


def major_version_family(system: str) -> str:
    """``PostgreSQL 17.11`` → ``postgresql 17`` — for a backend whose second number is the patch."""
    m = re.match(r"^\s*([A-Za-z]+)\s+(\d+)", str(system or ""))
    return f"{m.group(1).lower()} {m.group(2)}" if m else str(system or "").strip().lower()


# ── rule sets: one per backend (PROP-008 C3) ─────────────────────────────────

@dataclass(frozen=True)
class RuleSet:
    """One backend's design standard as records: its version, its rules, and the file that
    records their executed verification on a target.  A backend never reads another's."""
    backend: str
    version: str
    rules: Tuple[DesignRule, ...]
    verification_file: Path
    family: Any = None                      # version-family function (default: major.minor)
    verify_first: Tuple[str, ...] = ()      # rules whose statements create the scratch tables the rest use

    @property
    def by_id(self) -> Dict[str, DesignRule]:
        return {r.id: r for r in self.rules}

    def load_verification(self) -> Dict[str, Any]:
        return load_verification(self.verification_file)


TERADATA_RULE_SET = RuleSet("teradata", STANDARD_VERSION, RULES, VERIFICATION_FILE, _version_family,
                            ("multiset", "nopi_without_key", "soft_ri"))


def rule_set_for(backend: str) -> RuleSet:
    """The rule set of ``backend`` (``teradata`` · ``postgresql``).  Unknown → ``ValueError``:
    a deploy is never reported against a standard that is not its own."""
    b = str(backend or "teradata").strip().lower()
    if b == "teradata":
        return TERADATA_RULE_SET
    if b in ("postgresql", "postgres"):
        from .pg_standard import PG_RULE_SET
        return PG_RULE_SET
    raise ValueError(f"no design rule set for backend '{backend}'")


def rules_report(system: Optional[str] = None, record: Optional[Dict[str, Any]] = None,
                 standard: Optional[Any] = None, rule_set: Optional[RuleSet] = None) -> Dict[str, Any]:
    """Every rule with its source and its verification status for ``system`` — the
    rules section of a deploy report.  Counts are honest: verified · unverified."""
    rs = rule_set or TERADATA_RULE_SET
    rec = rs.load_verification() if record is None else record
    version = getattr(standard, "version", None) or rs.version
    rows = []
    for r in rs.rules:
        st, why = rule_verification_status(r, rec, system, rs.family)
        rows.append({**r.to_dict(), "status": st, "note": why})
    return {"backend": rs.backend, "standard": version, "system": system, "verified_on": (rec or {}).get("on"),
            "verified_system": (rec or {}).get("system"),
            "rules": rows, "verified": sum(1 for x in rows if x["status"] == "verified"),
            "unverified": sum(1 for x in rows if x["status"] != "verified")}
