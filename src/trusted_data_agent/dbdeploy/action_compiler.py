"""
PROP-008 OBDA-ACT — the write compiler (increment B2).

Binding design: docs/proposals/PROP-008-act-business-operating-model.md §5.3.
``compile_action(manifest, invocation)`` turns ONE invocation of a signed action type
into everything a performer needs, and nothing it must decide:

* **one parameterised statement** on the target only — every value a bind parameter,
  never a literal, so no SQL fragment crosses the model boundary;
* an ``update`` / ``upsert`` is bound to the grain (``WHERE <key> = ?`` and nothing
  else); an ``insert`` carries every required attribute or is refused naming the
  missing ones;
* every value **coerced** through its declared type (and, for a character column that
  stores dates, the column's sampled shape), then checked against its declared value
  domain — a code the column never holds is refused with the domain named;
* the **conditions** as executable checks: the operation's derived defaults (§2a)
  followed by the author's additions — ``SELECT COUNT(*)`` predicates over the keyed
  rows, plus the affected-row count for the ``*_affected`` defaults; and, for an
  update, the **bound** check (the keyed rows may not exceed ``max_rows`` — A3
  measured the grain unique, but a database that does not enforce it can drift);
* the **before-image** read and the **ledger** insert (§5.5), when the platform ledger
  is on.

Refusals are recovery-shaped (:class:`ActionInvocationError`, a CLOSED code set): they
name the admissible attributes, the domain, the grain and the bound — self-correction
food, never a guess and never a silent narrowing.

Pure and deterministic: same manifest + same invocation → the same bytes, across calls
and processes. The A5 gate compiles the representative invocation through THIS module
(:func:`representative_statement`), so the statement the gate proved and the statement
a performer runs are the same function's output. The representative statement's bytes
are identical to B1's, so no report signed under B1 moves.

``dialect`` is ``teradata`` (the backend) or ``sqlite`` (the CI twin, so the compiled
statements execute against hand-computed truth — the product compiler's precedent).

Additive by construction: nothing on any existing execution path imports this module.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import difflib
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from trusted_data_agent.dbdeploy.action_model import (
    ActionAttribute, ActionManifest, DEFAULT_CONDITIONS, ledger_identifier, manifest_hash)

DIALECTS = frozenset({"teradata", "sqlite"})


class ActionInvocationError(ValueError):
    """An invocation the action cannot perform. ``code`` is from :data:`ERROR_CODES`;
    the message names what IS admissible."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


ERROR_CODES = frozenset({
    "bad_invocation_shape", "bad_dialect", "unknown_attribute", "key_missing",
    "unknown_key", "key_not_settable", "missing_required", "null_required",
    "bad_value", "outside_domain", "unknown_text_date_shape", "nothing_to_write",
})


def _fail(code: str, message: str) -> None:
    raise ActionInvocationError(code, message)


def _closest(name: str, candidates: Sequence[str]) -> str:
    hits = difflib.get_close_matches(str(name), [str(c) for c in candidates], n=2, cutoff=0.6)
    return f" — closest admissible: {', '.join(hits)}" if hits else ""


# ── identifiers (shape-checked by the model; quoted here) ───────────────────

def _q(ident: str) -> str:
    return f'"{ident}"'


def _qt(deployed: str) -> str:
    db, table = deployed.split(".", 1)
    return f"{_q(db)}.{_q(table)}"


# ── value coercion (§5.3) ───────────────────────────────────────────────────

#: Dictionary type codes → the coercion kind for a column the manifest does not type
#: (the grain of an update is not an attribute). Teradata codes and portable spellings.
_CODE_KIND = {"I": "integer", "I1": "integer", "I2": "integer", "I8": "integer",
              "D": "decimal", "N": "decimal", "F": "decimal",
              "CV": "string", "CF": "string", "CO": "string",
              "DA": "date", "TS": "timestamp", "SZ": "timestamp"}
_CHAR_CODES = frozenset({"CV", "CF", "CO"})


def _kind_of_code(code: Optional[str]) -> Optional[str]:
    c = (code or "").strip().upper()
    if c in _CODE_KIND:
        return _CODE_KIND[c]
    for frag, kind in (("CHAR", "string"), ("TEXT", "string"), ("INT", "integer"),
                       ("DEC", "decimal"), ("NUM", "decimal"), ("TIMESTAMP", "timestamp"),
                       ("DATE", "date")):
        if frag in c:
            return kind
    return None


def _is_char_code(code: Optional[str]) -> bool:
    c = (code or "").strip().upper()
    return c in _CHAR_CODES or "CHAR" in c or "TEXT" in c


_INT_RE = re.compile(r"^[+-]?\d+$")


def _coerce(name: str, kind: str, v: Any) -> Any:
    """Coerce ONE value to its declared kind, or refuse naming what the kind accepts.
    Never guesses across kinds: a string that is not a number is not a zero."""
    if kind == "string":
        if isinstance(v, (dict, list, tuple, set)):
            _fail("bad_value", f"{name}: a string is expected, got {type(v).__name__}")
        return v if isinstance(v, str) else str(v)
    if kind == "integer":
        if isinstance(v, bool):
            _fail("bad_value", f"{name}: an integer is expected, got a boolean")
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v.is_integer():
            return int(v)
        if isinstance(v, str) and _INT_RE.match(v.strip()):
            return int(v.strip())
        _fail("bad_value", f"{name}: an integer is expected, got {v!r}")
    if kind == "decimal":
        if isinstance(v, bool):
            _fail("bad_value", f"{name}: a number is expected, got a boolean")
        try:
            d = decimal.Decimal(str(v).strip()) if isinstance(v, (int, float, str, decimal.Decimal)) else None
        except decimal.InvalidOperation:
            d = None
        if d is None or not d.is_finite():
            _fail("bad_value", f"{name}: a number is expected, got {v!r}")
        return d
    if kind == "boolean":
        if isinstance(v, bool):
            return 1 if v else 0
        if v in (0, 1):
            return int(v)
        s = str(v).strip().lower()
        if s in ("true", "yes", "1", "y"):
            return 1
        if s in ("false", "no", "0", "n"):
            return 0
        _fail("bad_value", f"{name}: a boolean is expected (true/false), got {v!r}")
    if kind == "date":
        if isinstance(v, _dt.datetime):
            return v.date()
        if isinstance(v, _dt.date):
            return v
        s = str(v).strip()
        if len(s) == 10:
            try:
                return _dt.date.fromisoformat(s)
            except ValueError:
                pass
        _fail("bad_value", f"{name}: a date is expected as YYYY-MM-DD, got {v!r}")
    if kind == "timestamp":
        if isinstance(v, _dt.datetime):
            return v
        if isinstance(v, _dt.date):
            return _dt.datetime(v.year, v.month, v.day)
        try:
            return _dt.datetime.fromisoformat(str(v).strip())
        except ValueError:
            _fail("bad_value", f"{name}: a timestamp is expected (ISO, e.g. 2026-10-07 14:30:00), got {v!r}")
    _fail("bad_value", f"{name}: unknown kind {kind!r}")


def _render_text_date(name: str, d: _dt.date, fmt: Optional[str]) -> str:
    """A date bound into a CHARACTER column is written in the column's own shape — the
    shape the data holds (sampled, never the one the docs claim)."""
    from trusted_data_agent.components.dateforms import format_to_strftime
    strf = format_to_strftime(fmt) if fmt else None
    if not strf:
        _fail("unknown_text_date_shape",
              f"{name} stores dates as text and its shape is not known — validate the source "
              f"(sampled shapes) first, so the value is written the way the column holds it")
    out = d.strftime(strf)
    return out.upper() if "%b" in strf else out


def _same(a: Any, b: Any) -> bool:
    """Domain membership after coercion — compared on the value's text, so a declared
    ``[0, 1]`` admits ``"1"`` for an integer column and ``"gold"`` matches ``"gold"``."""
    def norm(x):
        if isinstance(x, bool):
            return "1" if x else "0"
        if isinstance(x, decimal.Decimal):
            return format(x.normalize(), "f")
        if isinstance(x, float) and x.is_integer():
            return str(int(x))
        return str(x).strip()
    return norm(a) == norm(b)


def _canon(v: Any) -> Any:
    """JSON-safe, deterministic form of a bound value (for the invocation hash and the
    receipt) — never used as SQL."""
    if isinstance(v, decimal.Decimal):
        return format(v.normalize(), "f")
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def _bind(v: Any, dialect: str) -> Any:
    """The value handed to the driver. SQLite cannot bind Decimal/date — it receives
    their text, which its column affinity converts."""
    if dialect == "sqlite" and isinstance(v, (decimal.Decimal, _dt.date, _dt.datetime)):
        return _canon(v)
    return v


# ── the compiled write ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Check:
    """One executable condition. ``kind`` is ``count`` (``sql`` returns one integer)
    or ``rows_affected`` (compared with the write's affected-row count). Holds when
    ``observed <op> expect``."""
    id: str
    source: str            # "default" | "authored" | "bound"
    kind: str
    op: str                # "==" | ">=" | "<="
    expect: int
    words: str
    sql: Optional[str] = None
    params: Tuple[Any, ...] = ()

    def holds(self, observed: int) -> bool:
        return {"==": observed == self.expect, ">=": observed >= self.expect,
                "<=": observed <= self.expect}[self.op]

    def to_dict(self) -> Dict[str, Any]:
        d = {"id": self.id, "source": self.source, "kind": self.kind, "op": self.op,
             "expect": self.expect, "words": self.words}
        if self.sql is not None:
            d["sql"] = self.sql
        return d


@dataclass(frozen=True)
class CompiledWrite:
    action: str
    version: int
    operation: str
    target: str
    dialect: str
    sql: str
    params: Tuple[Any, ...]
    param_names: Tuple[str, ...]
    key: Dict[str, Any]
    values: Dict[str, Any]
    manifest_hash: str
    statement_hash: str
    invocation_hash: str
    expected_rows_max: int
    pre: Tuple[Check, ...]
    post: Tuple[Check, ...]
    before_sql: str
    before_params: Tuple[Any, ...]
    before_columns: Tuple[str, ...]
    ledger_sql: Optional[str] = None
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def ledger_params(self, *, principal: str, posture: str, rows_affected: Optional[int],
                      before: Optional[Sequence[Dict[str, Any]]]) -> Tuple[Any, ...]:
        """The ledger row's bind values. ``performed_at`` is the database's own clock
        (CURRENT_TIMESTAMP in the statement); everything else is bound."""
        return (principal, self.action, self.version, self.invocation_hash, self.statement_hash,
                posture, rows_affected,
                json.dumps({k: _canon(v) for k, v in self.key.items()}, sort_keys=True),
                json.dumps([{k: _canon(v) for k, v in r.items()} for r in (before or [])],
                           sort_keys=True, default=str))

    def receipt(self) -> Dict[str, Any]:
        """The facts a sealed receipt carries — values in their canonical text form."""
        return {"action": self.action, "version": self.version, "operation": self.operation,
                "target": self.target, "manifest_hash": self.manifest_hash,
                "statement_hash": self.statement_hash, "invocation_hash": self.invocation_hash,
                "key": {k: _canon(v) for k, v in self.key.items()},
                "values": {k: _canon(v) for k, v in self.values.items()},
                "expected_rows_max": self.expected_rows_max}

    def to_dict(self) -> Dict[str, Any]:
        return {**self.receipt(), "dialect": self.dialect, "sql": self.sql,
                "param_names": list(self.param_names),
                "pre": [c.to_dict() for c in self.pre], "post": [c.to_dict() for c in self.post],
                "before_sql": self.before_sql, "ledger_sql": self.ledger_sql,
                "notes": list(self.notes)}


def statement_hash(sql: str) -> str:
    """sha256 over the statement text — the same digest the A5 report records."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _statement(m: ActionManifest, cols: Sequence[str], dialect: str) -> Tuple[str, Tuple[str, ...]]:
    """``(sql, param_names)`` for the given written columns, in declared order."""
    t = _qt(m.deployed)
    if m.operation == "insert":
        sql = (f"INSERT INTO {t} ({', '.join(_q(c) for c in cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)})")
        return sql, tuple(cols)
    if m.operation == "update":
        sets = ", ".join(f"{_q(c)} = ?" for c in cols)
        where = " AND ".join(f"{_q(k)} = ?" for k in m.key)
        return f"UPDATE {t} SET {sets} WHERE {where}", tuple(cols) + tuple(m.key)
    non_key = [c for c in cols if c not in m.key]
    if dialect == "sqlite":
        upd = ", ".join(f"{_q(c)} = excluded.{_q(c)}" for c in non_key)
        sql = (f"INSERT INTO {t} ({', '.join(_q(c) for c in cols)}) "
               f"VALUES ({', '.join('?' for _ in cols)}) "
               f"ON CONFLICT ({', '.join(_q(k) for k in m.key)}) "
               + (f"DO UPDATE SET {upd}" if upd else "DO NOTHING"))
        return sql, tuple(cols)
    src = ", ".join(f"? AS {_q(c)}" for c in cols)
    on = " AND ".join(f"tgt.{_q(k)} = src.{_q(k)}" for k in m.key)
    upd = ", ".join(f"{_q(c)} = src.{_q(c)}" for c in non_key)
    ins_cols = ", ".join(_q(c) for c in cols)
    ins_vals = ", ".join(f"src.{_q(c)}" for c in cols)
    sql = (f"MERGE INTO {t} AS tgt USING (SELECT {src}) AS src ON {on} "
           + (f"WHEN MATCHED THEN UPDATE SET {upd} " if upd else "")
           + f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})")
    return sql, tuple(cols)


def representative_statement(m: ActionManifest) -> Tuple[str, Tuple[str, ...]]:
    """The statement the A5 gate proves: every writable attribute written, the grain
    bound. Teradata dialect. Byte-identical to B1's builder (asserted)."""
    return _statement(m, [a.name for a in m.attributes], "teradata")


def _key_where(m: ActionManifest) -> str:
    return " AND ".join(f"{_q(k)} = ?" for k in m.key)


def compile_action(m: ActionManifest, invocation: Dict[str, Any], *,
                   column_types: Optional[Dict[str, str]] = None,
                   text_date_shapes: Optional[Dict[str, str]] = None,
                   dialect: str = "teradata") -> CompiledWrite:
    """Compile one invocation. ``invocation`` is ``{"key": {col: value}, "values":
    {attr: value}}``; for an insert/upsert the key columns are attributes and may be
    given in either place. ``column_types`` (the dictionary's ``{column: type code}``)
    types the grain of an update and tells a character column from a date one;
    ``text_date_shapes`` is ``{column: FORMAT}`` for character columns that hold dates.
    Raises :class:`ActionInvocationError`."""
    if dialect not in DIALECTS:
        _fail("bad_dialect", f"dialect {dialect!r} must be one of {sorted(DIALECTS)}")
    if not isinstance(invocation, dict) or set(invocation) - {"key", "values"}:
        _fail("bad_invocation_shape", "an invocation is {\"key\": {column: value}, \"values\": "
                                      "{attribute: value}} — nothing else")
    raw_key = invocation.get("key") or {}
    raw_vals = invocation.get("values") or {}
    if not isinstance(raw_key, dict) or not isinstance(raw_vals, dict):
        _fail("bad_invocation_shape", "'key' and 'values' must be objects")
    ctypes = {k: v for k, v in (column_types or {}).items()}
    shapes = dict(text_date_shapes or {})
    names = [a.name for a in m.attributes]
    by = {a.name: a for a in m.attributes}
    notes: List[str] = []

    def coerce_attr(a: ActionAttribute, v: Any) -> Any:
        if v is None:
            if a.required:
                _fail("null_required", f"{a.name} is required and cannot be set to null")
            return None
        if a.type == "date" and _is_char_code(ctypes.get(a.name)):
            return _render_text_date(a.name, _coerce(a.name, "date", v), shapes.get(a.name))
        out = _coerce(a.name, a.type, v)
        if a.value_domain is not None and isinstance(out, str) and not any(_same(out, d) for d in a.value_domain):
            # a value that differs from exactly ONE declared value only in case or surrounding
            # space is that value ('Closed' → 'closed'), said in the notes — never refused for
            # the spelling (Act II rehearsal: one wasted tool round per act). Two candidates
            # differing only in case stay ambiguous and are refused below.
            folded = [d for d in a.value_domain if str(d).strip().lower() == out.strip().lower()]
            if len(folded) == 1:
                notes.append(f"{a.name} {v!r} written as the declared {folded[0]!r}")
                return folded[0]
        if a.value_domain is not None and not any(_same(out, d) for d in a.value_domain):
            _fail("outside_domain", f"{a.name} = {v!r} is not a value the column holds — admissible: "
                                    f"{', '.join(str(d) for d in a.value_domain)}"
                                    + _closest(str(v), [str(d) for d in a.value_domain]))
        return out

    # -- unknown names first (the allowlist is the contract) ------------------
    for n in raw_vals:
        if n not in by:
            if m.operation == "update" and n in m.key:
                _fail("key_not_settable", f"{n} is the grain — an update addresses the row by it and "
                                          f"cannot change it; pass it under 'key'")
            _fail("unknown_attribute", f"{n} is not writable by {m.action} — writable: "
                                       f"{', '.join(names)}" + _closest(n, names))
    for n in raw_key:
        if n not in m.key:
            _fail("unknown_key", f"{n} is not the grain of {m.action} — the grain is "
                                 f"({', '.join(m.key)})" + _closest(n, list(m.key)))

    key: Dict[str, Any] = {}
    values: Dict[str, Any] = {}
    if m.operation == "update":
        missing_k = [k for k in m.key if k not in raw_key or raw_key[k] is None]
        if missing_k:
            _fail("key_missing", f"the row is addressed by its grain ({', '.join(m.key)}); missing: "
                                 f"{', '.join(missing_k)}")
        for k in m.key:
            kind = _kind_of_code(ctypes.get(k))
            v = raw_key[k]
            if kind:
                key[k] = _coerce(k, kind, v)
            else:
                if isinstance(v, (dict, list, tuple, set, bool)):
                    _fail("bad_value", f"{k}: a key value must be a scalar, got {type(v).__name__}")
                key[k] = v
        missing = [a.name for a in m.attributes if a.required and a.name not in raw_vals]
        if missing:
            _fail("missing_required", f"{m.action} requires {', '.join(missing)} — writable: {', '.join(names)}")
        for a in m.attributes:
            if a.name in raw_vals:
                values[a.name] = coerce_attr(a, raw_vals[a.name])
        if not values:
            _fail("nothing_to_write", f"no value to write — writable: {', '.join(names)}")
    else:
        merged: Dict[str, Any] = {}
        for k, v in raw_key.items():
            if k in raw_vals and not _same(raw_vals[k], v):
                _fail("bad_invocation_shape", f"{k} is given twice with different values")
            merged[k] = v
        merged.update(raw_vals)
        unknown = [n for n in merged if n not in by]
        if unknown:
            _fail("unknown_attribute", f"{unknown[0]} is not writable by {m.action} — writable: "
                                       f"{', '.join(names)}" + _closest(unknown[0], names))
        for a in m.attributes:
            if a.name in merged:
                values[a.name] = coerce_attr(a, merged[a.name])
            elif a.has_default:
                values[a.name] = coerce_attr(a, a.default) if a.default is not None else None
                notes.append(f"{a.name} took its declared default")
        missing = [a.name for a in m.attributes if a.required and a.name not in values]
        if missing:
            _fail("missing_required", f"an {m.operation} carries every required attribute; missing: "
                                      f"{', '.join(missing)}")
        missing_k = [k for k in m.key if values.get(k) is None]
        if missing_k:
            _fail("key_missing", f"an {m.operation} supplies its grain ({', '.join(m.key)}); missing: "
                                 f"{', '.join(missing_k)}")
        key = {k: values[k] for k in m.key}

    cols = [n for n in names if n in values]
    sql, pnames = _statement(m, cols, dialect)
    # param order follows the statement: an update binds its SET columns then the grain;
    # an insert/upsert binds its columns (the grain is among them)
    if m.operation == "update":
        params = tuple(_bind(values[c], dialect) for c in cols) + tuple(_bind(key[k], dialect) for k in m.key)
    else:
        params = tuple(_bind(values[c], dialect) for c in cols)
    kparams = tuple(_bind(key[k], dialect) for k in m.key)

    # -- the checks ----------------------------------------------------------
    t = _qt(m.deployed)
    where = _key_where(m)
    count_keyed = f"SELECT COUNT(*) FROM {t} WHERE {where}"
    defaults = DEFAULT_CONDITIONS[m.operation]
    shapes_by_id = {
        "row_exists":       ("count", ">=", 1),
        "row_absent":       ("count", "==", 0),
        "one_row_present":  ("count", "==", 1),
        "one_row_affected": ("rows_affected", "==", 1),
    }

    def default_checks(side: str) -> List[Check]:
        out = []
        for cid, words in defaults[side]:
            kind, op, n = shapes_by_id[cid]
            out.append(Check(cid, "default", kind, op, n, words,
                             sql=count_keyed if kind == "count" else None,
                             params=kparams if kind == "count" else ()))
        return out

    def authored(conds) -> List[Check]:
        # a keyed row that does NOT satisfy the predicate is a violation; NULL counts as
        # not satisfied (CASE, because Teradata has no boolean type)
        return [Check(c.id, "authored", "count", "==", 0,
                      f"every keyed row satisfies: {c.expr}",
                      sql=(f"SELECT COUNT(*) FROM {t} WHERE {where} "
                           f"AND (CASE WHEN ({c.expr}) THEN 1 ELSE 0 END) = 0"),
                      params=kparams) for c in conds]

    pre = default_checks("pre")
    if m.operation == "update":
        pre.append(Check("within_bound", "bound", "count", "<=", m.max_rows,
                         f"the keyed rows do not exceed the bound ({m.max_rows})",
                         sql=count_keyed, params=kparams))
    pre += authored(m.pre)
    post = default_checks("post")
    post.append(Check("rows_within_bound", "bound", "rows_affected", "<=", m.max_rows,
                      f"no more than {m.max_rows} row(s) affected"))
    post += authored(m.post)

    # -- the before-image and the ledger -------------------------------------
    before_cols = tuple(list(m.key) + [c for c in names if c not in m.key])
    before_sql = f"SELECT {', '.join(_q(c) for c in before_cols)} FROM {t} WHERE {where}"
    led = ledger_identifier(m)
    ledger_sql = None
    if led:
        now = "CURRENT_TIMESTAMP(6)" if dialect == "teradata" else "CURRENT_TIMESTAMP"
        ledger_sql = (f"INSERT INTO {_qt(led)} (\"performed_at\", \"principal\", \"action_name\", "
                      f"\"action_version\", \"invocation_hash\", \"statement_hash\", \"posture\", "
                      f"\"rows_affected\", \"key_json\", \"before_json\") "
                      f"VALUES ({now}, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
    else:
        notes.append("the target records its own audit columns (ledger.mode target_audit_columns)")

    mh = manifest_hash(m)
    inv_canon = json.dumps({"action": m.action, "version": m.version, "manifest_hash": mh,
                            "key": {k: _canon(v) for k, v in key.items()},
                            "values": {k: _canon(v) for k, v in values.items()}},
                           sort_keys=True, separators=(",", ":"), default=str)
    return CompiledWrite(
        action=m.action, version=m.version, operation=m.operation, target=m.deployed,
        dialect=dialect, sql=sql, params=params, param_names=pnames, key=key, values=values,
        manifest_hash=mh, statement_hash=statement_hash(sql),
        invocation_hash=hashlib.sha256(inv_canon.encode("utf-8")).hexdigest(),
        expected_rows_max=m.max_rows, pre=tuple(pre), post=tuple(post),
        before_sql=before_sql, before_params=kparams, before_columns=before_cols,
        ledger_sql=ledger_sql, notes=tuple(notes),
    )
