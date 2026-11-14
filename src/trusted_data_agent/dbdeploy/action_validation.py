"""
PROP-008 OBDA-ACT — the action-type sign-time gate A1–A6 (increment B1).

Binding design: docs/proposals/PROP-008-act-business-operating-model.md §5.2. A signed
action type means "the producer approved this write contract AND the platform proved,
mechanically, that one invocation touches only the target table, at a grain the data
says is unique, through a role that holds exactly the grant it needs". The report's
hash goes INSIDE the signed content, so the signature binds the proof.

| Check | Proves | Evidence |
|---|---|---|
| A1 | the frozen `deployed` resolves to the table the graph resolves today | resolver |
| A2 | every writable attribute exists on the table with a compatible type; every value domain agrees with the graph's declared domain | dictionary + graph |
| A3 | the grain is a key the data says is unique | dictionary + **live** key probe |
| A4 | every author-declared pre/post predicate parses, is boolean, reads only the target's columns, and holds no subquery | pure (sqlglot) |
| A5 | the representative statement is one parameterised statement on the target only, byte-stable across compilations, and EXPLAINs without a product join or an all-rows scan of the target | pure + **live** EXPLAIN |
| A6 | the declared role holds exactly the rights the operation needs on exactly the target (and INSERT on the ledger), nothing wider | **live**, from the dictionary's rights view |

Pure module: no I/O, no store, no LLM. Everything external is INJECTED:

* ``resolution``     ``{entity: deployed}`` from the ontology resolver — ``None`` ⇒ A1
                     structural (accepted as declared, stated honestly);
* ``schema``         ``{deployed: {column: type}}`` from the dictionary (the target and,
                     when it exists, the ledger) — ``None`` ⇒ A2's column checks structural;
* ``unique_keys``    ``{deployed: [(col, …), …]}`` the dictionary's unique indexes / PK;
* ``graph_domains``  ``{attribute: [values]}`` the value domain the graph declares for the
                     target's attributes (A2 compares, never infers);
* ``entity_columns`` the target entity's declared columns in the graph — what a pre/post
                     condition may read when no dictionary is connected (A4);
* ``probe_fn``       ``probe_fn(sql, params=None) -> rows`` — a READ-ONLY executor; with
                     one the gate runs live (A3 key probe, A5 EXPLAIN, A6 rights) and the
                     report's ``mode`` says ``live``; without one, ``structural``;
* ``compile_fn``     the statement builder for A5. The default is
                     :func:`representative_statement` — the write compiler's output
                     (``action_compiler``) for the representative invocation, so the gate
                     and the runtime compile the same bytes.

Determinism: the report carries no timestamp, no randomness and no environment, so
``report_hash`` is byte-stable across processes — CI hashes two independent runs.

Additive by construction: nothing on any existing execution path imports this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from trusted_data_agent.dbdeploy.action_model import (
    ActionManifest, effective_conditions, ledger_identifier, manifest_hash, required_rights)

# ── report model ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CheckResult:
    id: str          # "A1" … "A6"
    status: str      # "pass" | "fail" | "unverified"
    mode: str        # "structural" | "schema" | "live" — the evidence behind it (S2)
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "status": self.status, "mode": self.mode, "detail": self.detail}


@dataclass(frozen=True)
class ActionReport:
    action: str
    version: int
    manifest_hash: str
    statement_hash: str           # sha256 of the representative statement (A5)
    conditions: Dict[str, List[Dict[str, str]]]   # the effective (default + authored) conditions
    checks: Tuple[CheckResult, ...]
    signable: bool
    blockers: Tuple[str, ...] = ()
    #: "live" when the live probes RAN against a connection, "structural" otherwise —
    #: attested content, so a live-verified signature is told from a declared one.
    mode: str = "structural"

    def to_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "version": self.version,
                "manifest_hash": self.manifest_hash, "statement_hash": self.statement_hash,
                "conditions": self.conditions,
                "checks": [c.to_dict() for c in self.checks],
                "signable": self.signable, "blockers": list(self.blockers), "mode": self.mode}


def report_hash(report: "ActionReport | Dict[str, Any]") -> str:
    d = report.to_dict() if isinstance(report, ActionReport) else report
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


# ── the representative statement (A5) ──

def _q(ident: str) -> str:
    return f'"{ident}"'


def _qt(deployed: str) -> str:
    db, table = deployed.split(".", 1)
    return f"{_q(db)}.{_q(table)}"


# B2: the statement the gate proves IS the write compiler's output for the
# representative invocation (every writable attribute, the grain bound) — so the gate
# and a performer compile the same bytes. Re-exported under B1's name.
from trusted_data_agent.dbdeploy.action_compiler import representative_statement  # noqa: E402,F401


def _sqlglot():
    try:
        import sqlglot  # noqa: F401
        from sqlglot import exp
        return sqlglot, exp
    except Exception:  # noqa: BLE001
        return None, None


def statement_shape(sql: str, target: str) -> List[str]:
    """What is wrong with a statement's SHAPE — empty when it is one parameterised
    statement that names only the target. Pure (sqlglot; a regex floor without it)."""
    problems: List[str] = []
    body = sql.strip().rstrip(";")
    if ";" in body:
        problems.append("more than one statement")
    if re.search(r"'[^']*'", body):
        problems.append("a string literal appears — every value must be a bind parameter")
    sqlglot, exp = _sqlglot()
    if sqlglot is None:
        return problems
    try:
        tree = sqlglot.parse_one(body, read="teradata")
    except Exception as exc:  # noqa: BLE001
        return problems + [f"does not parse: {str(exc)[:120]}"]
    lits = [l for l in tree.find_all(exp.Literal)]
    if lits:
        problems.append(f"{len(lits)} literal(s) appear — every value must be a bind parameter")
    db, table = target.split(".", 1)
    for tb in tree.find_all(exp.Table):
        name = (tb.name or "").lower()
        if not name:
            continue
        if name != table.lower() or (tb.db and tb.db.lower() != db.lower()):
            problems.append(f"names a table other than the target: {tb.sql(dialect='teradata')}")
    return problems


# ── predicates (A4) ─────────────────────────────────────────────────────────

def predicate_problems(expr: str, columns: Optional[Set[str]]) -> List[str]:
    """A pre/post predicate must parse as a condition, be BOOLEAN at its top node (a
    comparison, IN, BETWEEN, IS, LIKE, EXISTS-free combination of those), read only
    the target's ``columns`` and hold no subquery (it may not reach another table)."""
    p = (expr or "").strip()
    if not p:
        return ["empty predicate"]
    if re.search(r";|--|/\*", p):
        return ["must be one expression — no statements or comments"]
    sqlglot, exp = _sqlglot()
    if sqlglot is None:
        return [] if p.count("(") == p.count(")") else ["unbalanced parentheses"]
    try:
        where = sqlglot.parse_one(f"SELECT 1 FROM t WHERE {p}", read="teradata").args.get("where")
    except Exception as exc:  # noqa: BLE001
        return [f"does not parse as a condition: {str(exc)[:120]}"]
    node = where.this if where is not None else None
    if node is None:
        return ["does not parse as a condition"]
    out: List[str] = []
    if any(True for _ in node.find_all(exp.Select)) or any(True for _ in node.find_all(exp.Subquery)):
        out.append("holds a subquery — a condition reads the target row only")

    def _boolean(n) -> bool:
        if isinstance(n, exp.Paren):
            return _boolean(n.this)
        if isinstance(n, (exp.And, exp.Or)):
            return _boolean(n.left) and _boolean(n.right)
        if isinstance(n, exp.Not):
            return _boolean(n.this)
        return isinstance(n, (exp.Predicate, exp.Boolean, exp.In, exp.Between, exp.Is))
    if not _boolean(node):
        out.append(f"is not boolean at its top ({type(node).__name__}) — a condition must be true or false")
    if columns is None:
        return out          # no column universe known — the caller reports it unverified
    lowered = {c.lower() for c in columns}
    unknown = sorted({c.name for c in node.find_all(exp.Column) if c.name and c.name.lower() not in lowered})
    if unknown:
        out.append(f"reads column(s) the target does not declare: {', '.join(unknown)}")
    return out


# ── A2 type compatibility ───────────────────────────────────────────────────

#: Declared attribute type → the Teradata ColumnType codes (DBC.ColumnsV) it may write.
_TYPE_CODES = {
    "string": {"CV", "CF", "CO"},
    "integer": {"I", "I1", "I2", "I8", "D", "N"},
    "decimal": {"D", "F", "N", "I", "I1", "I2", "I8"},
    "date": {"DA"},
    "timestamp": {"TS", "SZ"},
    "boolean": {"I1", "CF", "CV"},
}


def contract_type_of(column_type: str) -> Optional[str]:
    """The contract type a column of this warehouse type is written as — the INVERSE of
    ``_TYPE_CODES``, so a contract derived from the dictionary always passes A2 (the 7 Oct
    rehearsal declared ``lead_id`` an integer over a text column and A2 refused it). A
    Teradata ColumnType code, or a portable spelling compared by family; ``None`` when the
    type says nothing a contract can use (the person decides, A2 still checks)."""
    ct = (column_type or "").strip().upper()
    if not ct:
        return None
    for declared in ("string", "integer", "decimal", "date", "timestamp"):
        if ct in _TYPE_CODES[declared] and not (declared == "integer" and ct in ("D", "N")):
            return declared
    for declared, fam in (("timestamp", ("TIMESTAMP", "DATETIME")), ("date", ("DATE",)),
                          ("string", ("CHAR", "TEXT", "STRING", "CLOB")),
                          ("decimal", ("DEC", "NUM", "FLOAT", "DOUBLE", "REAL")),
                          ("integer", ("INT",)), ("boolean", ("BOOL",))):
        if any(f in ct for f in fam):
            return declared
    return None


def _type_ok(declared: str, column_type: str) -> bool:
    ct = (column_type or "").strip().upper()
    if not ct:
        return True        # a dictionary that does not say cannot contradict
    codes = _TYPE_CODES.get(declared)
    if codes is None:
        return True
    if ct in codes:
        return True
    # a portable spelling (an information_schema or SQLite catalog) — compare by family
    fam = {"string": ("CHAR", "TEXT", "VARCHAR", "STRING"), "integer": ("INT",),
           "decimal": ("DEC", "NUM", "FLOAT", "DOUBLE", "REAL", "INT"), "date": ("DATE",),
           "timestamp": ("TIMESTAMP", "DATETIME"), "boolean": ("BOOL", "INT", "BYTEINT", "CHAR")}
    return any(f in ct for f in fam.get(declared, ()))


# ── A6 rights ────────────────────────────────────────────────────────────────

#: Rights that only READ — every other right is a write-class or DDL right and counts as
#: "wider" when the operation does not need it.
READ_ONLY_RIGHTS = frozenset({"R"})


def rights_sql(role: str) -> Tuple[str, str]:
    """``(role_members_sql, rights_sql)`` — the role's nested roles, and the rights held
    by a set of roles. Identifiers are shape-checked by the model; values are bound."""
    return ("SELECT RoleName FROM DBC.RoleMembersV WHERE Grantee = ?",
            "SELECT RoleName, DatabaseName, TableName, AccessRight FROM DBC.AllRoleRightsV "
            "WHERE RoleName = ?")


def _words(exc: Exception) -> str:
    """A probe's failure in the warehouse's own sentence — never the driver's stack."""
    try:
        from trusted_data_agent.dbcatalog.sql_runner import friendly_db_error
        return friendly_db_error(exc)[:240]
    except Exception:  # noqa: BLE001
        return str(exc).splitlines()[0][:160]


def grant_statements(role: str, lacking: Dict[str, Sequence[str]]) -> List[str]:
    """The exact GRANTs that give ``role`` what it lacks — ``{object: [right code, …]}`` →
    ``GRANT INSERT, SELECT ON db.t TO role;``. A6 says them, so the person (or the DBA) runs
    exactly what the gate will accept: never wider, never guessed."""
    from trusted_data_agent.dbdeploy.action_model import RIGHT_WORDS
    out = []
    for ident, rights in lacking.items():
        words = [RIGHT_WORDS.get(r, r) for r in sorted(set(rights))]
        if words:
            out.append(f"GRANT {', '.join(words)} ON {ident} TO {role};")
    return out


def judge_rights(m: ActionManifest, held: Sequence[Tuple[str, str, str]],
                 ledger_exists: bool) -> Tuple[bool, str]:
    """Pure A6 decision. ``held`` = ``[(database, table, right)]`` over the role and every
    role nested in it. Database-level grants arrive with table ``All``."""
    need = required_rights(m)
    led = ledger_identifier(m)
    have: Dict[str, Set[str]] = {}
    wider: List[str] = []
    for db, table, right in held:
        db, table, right = str(db).strip(), str(table).strip(), str(right).strip().upper()
        ident = f"{db}.{table}"
        key = next((k for k in need if k.lower() == ident.lower()), None)
        if table.lower() == "all":
            if right not in READ_ONLY_RIGHTS:
                wider.append(f"{right} on the whole database {db}")
            continue
        if key is None:
            if right not in READ_ONLY_RIGHTS:
                wider.append(f"{right} on {ident}")
            continue
        have.setdefault(key, set()).add(right)
        if right not in need[key]:
            wider.append(f"{right} on {ident} (the operation needs only " + "".join(need[key]) + ")")
    missing = []
    lacking: Dict[str, List[str]] = {}
    for ident, rights in need.items():
        if ident == led and not ledger_exists:
            continue
        lack = sorted(set(rights) - have.get(ident, set()))
        if lack:
            from trusted_data_agent.dbdeploy.action_model import RIGHT_WORDS
            missing.append(", ".join(RIGHT_WORDS.get(x, x) for x in lack) + f" on {ident}")
            lacking[ident] = lack
    problems = []
    if led and not ledger_exists:
        problems.append(f"the action ledger {led} is not provisioned — provision it, then re-validate")
    if missing:
        problems.append(f"role {m.required_role} lacks {'; '.join(missing)} — grant exactly: "
                        + " ".join(grant_statements(m.required_role, lacking)))
    if wider:
        problems.append(f"role {m.required_role} holds more than the operation needs: {'; '.join(sorted(set(wider)))}")
    if problems:
        return False, " · ".join(problems)
    from trusted_data_agent.dbdeploy.action_model import RIGHT_WORDS
    held_words = "; ".join(", ".join(RIGHT_WORDS.get(x, x) for x in sorted(r)) + " on " + i for i, r in need.items())
    return True, (f"role {m.required_role} holds exactly {held_words}"
                  + ("" if led else " (the target's own audit columns record who/when)"))


# ── A5 plan reading ─────────────────────────────────────────────────────────

_STEP_SPLIT_RE = re.compile(r"(?m)^\s*\d+(?:\.\d+)?\)\s+")


def write_plan_problems(text: str, target: str) -> List[str]:
    """What an EXPLAIN of ONE write says that the gate refuses. The shared plan parser
    (``explain_plan/plan_parser``) models retrieve and join steps; a write step (UPDATE,
    INSERT, MERGE) reads to it as ``other`` and carries no access path — measured, Oct
    2026 — so the write is read here from the plan's own step text: a step that joins
    with a product join, or that reaches the TARGET by way of an all-rows scan (the
    grain does not reach a row through an index). Pure."""
    out: List[str] = []
    flat = re.sub(r"\s+", " ", text or "")
    steps = [st for st in _STEP_SPLIT_RE.split(text or "") if st.strip()] or [text or ""]
    tgt = target.lower()
    tbl = target.split(".", 1)[1].lower()
    if re.search(r"product join", flat, re.I):
        out.append("the plan does a product join")
    for st in steps:
        one = re.sub(r"\s+", " ", st).lower()
        names_target = tgt in one or re.search(rf"\b{re.escape(tbl)}\b", one) is not None
        if names_target and "all-rows scan" in one:
            out.append(f"the plan reads {target} with an all-rows scan — the grain does not reach "
                       f"a row through an index")
            break
    return out


# ── the gate ────────────────────────────────────────────────────────────────

def _rows_values(rows: Any) -> List[Tuple]:
    out = []
    for r in rows or []:
        out.append(tuple(r.values()) if isinstance(r, dict) else tuple(r))
    return out


def validate_action(
    m: ActionManifest,
    *,
    resolution: Optional[Dict[str, Any]] = None,
    schema: Optional[Dict[str, Dict[str, str]]] = None,
    unique_keys: Optional[Dict[str, List[Sequence[str]]]] = None,
    graph_domains: Optional[Dict[str, List[Any]]] = None,
    entity_columns: Optional[Sequence[str]] = None,
    probe_fn: Optional[Callable[..., Any]] = None,
    compile_fn: Callable[[ActionManifest], Tuple[str, Tuple[str, ...]]] = representative_statement,
    sample_params: Optional[Callable[[ActionManifest, Tuple[str, ...]], list]] = None,
) -> ActionReport:
    """Run A1–A6. Deterministic: same inputs → byte-identical report."""
    checks: List[CheckResult] = []
    blockers: List[str] = []
    live = probe_fn is not None

    def _check(cid: str, ok: bool, mode: str, detail: str):
        checks.append(CheckResult(cid, "pass" if ok else "fail", mode, detail))
        if not ok:
            blockers.append(f"{cid}: {detail}")

    target_cols = (schema or {}).get(m.deployed) if schema is not None else None

    # -- A1 · resolution agreement --------------------------------------------
    if resolution is None:
        checks.append(CheckResult("A1", "pass", "structural",
                                  "resolver not connected — the frozen target accepted as declared "
                                  "(structural mode, stated honestly)"))
    else:
        now = resolution.get(m.entity)
        if now is None:
            _check("A1", False, "schema", f"{m.entity!r} resolves to no deployed table today")
        elif isinstance(now, (list, tuple)):
            _check("A1", False, "schema", f"{m.entity!r} resolves to {len(now)} tables — must be exactly one")
        elif str(now).lower() != m.deployed.lower():
            _check("A1", False, "schema", f"frozen {m.deployed!r} but the graph now resolves {m.entity!r} to "
                                          f"{now!r} — re-validate + re-sign (the ratchet)")
        else:
            _check("A1", True, "schema", f"{m.entity!r} resolves to exactly {m.deployed}")

    # -- A2 · writable attributes exist, types agree, domains agree ----------
    bad: List[str] = []
    if target_cols is None:
        mode2 = "structural"
        if schema is not None:
            bad.append(f"the target {m.deployed} does not exist in the deployed schema")
            mode2 = "schema"
    else:
        mode2 = "schema"
        lc = {c.lower(): t for c, t in target_cols.items()}
        for a in m.attributes:
            ct = lc.get(a.name.lower())
            if ct is None:
                bad.append(f"{a.name!r} is not a column of {m.deployed}")
            elif not _type_ok(a.type, ct):
                bad.append(f"{a.name!r} is declared {a.type} but {m.deployed} stores it as {ct.strip()}")
        for k in m.key:
            if k.lower() not in lc:
                bad.append(f"key column {k!r} is not a column of {m.deployed}")
        for c in m.ledger_columns:
            if c.lower() not in lc:
                bad.append(f"audit column {c!r} is not a column of {m.deployed}")
    for a in m.attributes:
        gd = (graph_domains or {}).get(a.name)
        if gd is None:
            continue
        if a.value_domain is None:
            bad.append(f"{a.name!r}: the graph declares a value domain; the action must carry it")
            continue
        extra = [v for v in a.value_domain if str(v) not in {str(x) for x in gd}]
        if extra:
            bad.append(f"{a.name!r}: value(s) {extra!r} are outside the graph's declared domain")
    if graph_domains is not None and mode2 == "structural":
        mode2 = "schema"
    _check("A2", not bad, mode2,
           ("every writable attribute exists with a compatible type and agrees with the graph's domain"
            if mode2 != "structural" else
            "deployed schema not connected — the allowlist accepted as declared (structural mode, stated honestly)")
           if not bad else "; ".join(bad))

    # -- A3 · the grain is unique ---------------------------------------------
    key_l = tuple(k.lower() for k in m.key)
    declared_unique = None
    if unique_keys is not None:
        declared_unique = any(tuple(c.lower() for c in u) == key_l
                              or set(c.lower() for c in u) <= set(key_l)
                              for u in (unique_keys.get(m.deployed) or []))
    if live:
        db, table = m.deployed.split(".", 1)
        col_list = ", ".join(_q(k) for k in m.key)
        sql = (f"SELECT COUNT(*) AS dup_groups FROM (SELECT {col_list} FROM {_qt(m.deployed)} "
               f"GROUP BY {col_list} HAVING COUNT(*) > 1) AS probe_x")
        try:
            vals = _rows_values(probe_fn(sql))
            dup = int(vals[0][0]) if vals and vals[0] else None
        except Exception as exc:  # noqa: BLE001
            dup, err = None, _words(exc)
        else:
            err = None
        if dup is None:
            _check("A3", False, "live", f"the key probe could not run: {err or 'unreadable result'}")
        elif dup > 0:
            _check("A3", False, "live", f"the data CONTRADICTS the grain: ({', '.join(m.key)}) has {dup} "
                                        f"duplicate group(s) on {m.deployed} — one invocation could touch "
                                        f"more than one row")
        else:
            note = ("" if declared_unique else
                    " — no unique index declares it; measured unique today, not enforced by the database")
            _check("A3", True, "live", f"({', '.join(m.key)}) identifies one row of {m.deployed}{note}")
    elif declared_unique is not None:
        _check("A3", bool(declared_unique), "schema",
               f"({', '.join(m.key)}) is declared unique on {m.deployed}" if declared_unique else
               f"no unique index on {m.deployed} covers ({', '.join(m.key)}) — the grain is unproven")
    else:
        checks.append(CheckResult("A3", "unverified", "structural",
                                  "no connection — the grain is accepted as declared; uniqueness is "
                                  "proven only by a live key probe (validate with a connection)"))

    # -- A4 · predicates -------------------------------------------------------
    # the column universe a condition may read: the deployed table's columns, else the
    # entity's declared columns in the graph, else none known (→ column reads unverified)
    if target_cols is not None:
        cols, mode4 = set(target_cols), "schema"
    elif entity_columns is not None:
        cols, mode4 = set(entity_columns) | set(m.attribute_names()) | set(m.key), "schema"
    else:
        cols, mode4 = None, "structural"
    bad4 = []
    for side, conds in (("pre", m.pre), ("post", m.post)):
        for c in conds:
            for p in predicate_problems(c.expr, cols):
                bad4.append(f"{side} {c.id}: {p}")
    n_auth = len(m.pre) + len(m.post)
    if bad4:
        _check("A4", False, mode4, "; ".join(bad4))
    elif n_auth and cols is None:
        checks.append(CheckResult("A4", "unverified", "structural",
                                  f"{n_auth} authored condition(s) parse as boolean predicates; the "
                                  f"columns they read are unverified — neither the graph nor the "
                                  f"dictionary was available"))
    else:
        _check("A4", True, mode4,
               f"{n_auth} authored condition(s) are boolean predicates over the target's columns; the "
               f"operation's defaults are written in" if n_auth else
               "no authored conditions; the operation's derived defaults apply")

    # -- A5 · the statement ----------------------------------------------------
    sql1, params = compile_fn(m)
    sql2, params2 = compile_fn(m)
    statement_hash = hashlib.sha256(sql1.encode("utf-8")).hexdigest()
    bad5 = []
    if (sql1, params) != (sql2, params2):
        bad5.append("not byte-stable across compilations")
    bad5 += statement_shape(sql1, m.deployed)
    if sql1.count("?") != len(params):
        bad5.append(f"{sql1.count('?')} bind marker(s) for {len(params)} parameter(s)")
    mode5 = "structural"
    if not bad5 and live:
        mode5 = "live"
        try:
            vals = sample_params(m, params) if sample_params else None
            rows = probe_fn(f"EXPLAIN {sql1}", vals) if vals is not None else probe_fn(f"EXPLAIN {sql1}")
            text = "\n".join(str(v[0]) for v in _rows_values(rows) if v)
        except Exception as exc:  # noqa: BLE001
            text, bad5 = None, [f"EXPLAIN could not run: {_words(exc)}"]
        if text:
            bad5 += write_plan_problems(text, m.deployed)
    _check("A5", not bad5, mode5,
           (f"one parameterised {m.operation} on {m.deployed} only, byte-stable"
            + (" · EXPLAIN shows no product join and no all-rows scan of the target" if mode5 == "live" else ""))
           if not bad5 else "; ".join(bad5))

    # -- A6 · the role's rights ------------------------------------------------
    if live:
        members_sql, rsql = rights_sql(m.required_role)
        try:
            roles = [m.required_role]
            frontier = [m.required_role]
            while frontier:
                nxt = []
                for r in frontier:
                    for row in _rows_values(probe_fn(members_sql, [r])):
                        name = str(row[0]).strip()
                        if name and name not in roles:
                            roles.append(name)
                            nxt.append(name)
                frontier = nxt
            held = []
            for r in roles:
                for row in _rows_values(probe_fn(rsql, [r])):
                    held.append((row[1], row[2], row[3]))
        except Exception as exc:  # noqa: BLE001
            _check("A6", False, "live", f"the role's rights could not be read: {_words(exc)}")
        else:
            if not held:
                need = required_rights(m)
                led = ledger_identifier(m)
                led_exists = bool(led) and schema is not None and led in schema
                lacking = {i: r for i, r in need.items() if i != led or led_exists}
                _check("A6", False, "live",
                       f"role {m.required_role} holds no rights yet (create it first if it does not exist) — "
                       f"grant exactly: " + " ".join(grant_statements(m.required_role, lacking)))
            else:
                led = ledger_identifier(m)
                led_exists = bool(led) and schema is not None and led in schema
                ok6, detail6 = judge_rights(m, held, led_exists)
                _check("A6", ok6, "live", detail6)
    else:
        checks.append(CheckResult("A6", "unverified", "structural",
                                  "no connection — what the role holds is proven only from the "
                                  "dictionary's rights view (validate with a connection)"))

    return ActionReport(
        action=m.action, version=m.version, manifest_hash=manifest_hash(m),
        statement_hash=statement_hash, conditions=effective_conditions(m),
        checks=tuple(checks), signable=not blockers, blockers=tuple(blockers),
        mode="live" if live else "structural",
    )
