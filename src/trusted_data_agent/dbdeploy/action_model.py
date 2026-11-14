"""
PROP-008 OBDA-ACT — the action-type manifest model (increment B1).

An **action type** is a signed, machine-validated WRITE contract over one entity of the
ontology: which table it touches, at which grain, which columns it may set (an
allowlist with value domains), which conditions must hold before and after, how many
rows one invocation may touch, and which database role carries the grant. Binding
design: docs/proposals/PROP-008-act-business-operating-model.md §5.1 (the model),
§2a (the derived default conditions), §5.5 + §13 (the ledger, on by default).

This module is **pure** — no I/O, no LLM, no store access — and mirrors
``product_model.py`` in discipline so the two cannot drift:

* everything decidable from the manifest ALONE is enforced here and raises
  :class:`ActionManifestError` with a machine-readable ``code`` from a CLOSED set,
  with a difflib near-miss hint where one exists (self-correction food, never a guess);
* everything that needs the outside world — the resolver, the deployed dictionary,
  the data, the role's rights, EXPLAIN — is ``action_validation.py`` (A1–A6).

Canonicalisation matches the Trust Membrane's (``sort_keys=True, separators=(",",
":"), default=str``), so ``manifest_hash`` is byte-stable and is what the
``action_type`` attestation binds.

The derived default conditions (§2a) are NOT typed by the author: :func:`effective_conditions`
returns the operation's defaults FOLLOWED BY the author's additions, and the defaults
cannot be removed — a manifest that declares a condition with a reserved default
identifier is refused (``reserved_condition``). The canonical manifest stores only what
the author wrote; the defaults are a pure function of the operation, so the signed
content and the enforced conditions can never disagree.

Additive by construction: nothing on any existing execution path imports this module.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# ── The closed vocabularies ─────────────────────────────────────────────────

#: Action identity: unique per ontology, machine-safe (the product rule, verbatim).
ACTION_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")

#: v1 operations. ``delete`` is deliberately absent (§9): its undo is the ledger's
#: before-image, which is why the ledger is on by default — v2 opens it.
OPERATIONS = frozenset({"insert", "update", "upsert"})

#: The posture ladder, reused verbatim from the answer gates (core/posture_modes.py).
POSTURES = frozenset({"observe", "annotate", "review", "enforce"})

#: Where the audit trail lives. ``platform`` (the default) mirrors every performed
#: action into a platform-owned append-only table; ``target_audit_columns`` declares
#: that the target already records who/when itself (the ANDP products do).
LEDGER_MODES = frozenset({"platform", "target_audit_columns"})

#: The declared attribute types a value is coerced through (§5.3). Advisory at the
#: model; A2 checks each against the deployed column.
ATTRIBUTE_TYPES = frozenset({"string", "integer", "decimal", "date", "timestamp", "boolean"})

#: A database identifier — the shape every column, key and role name must have. The
#: manifest is signable content AND the compiler's input: a name that is not an
#: identifier is refused here, so no SQL fragment can enter through a column name.
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
DEPLOYED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\.[A-Za-z_][A-Za-z0-9_]{0,127}$")

#: The ledger table's name prefix (§5.5 — a platform-owned structure, `UDERIA_` like the
#: delta deployer's sibling database).
LEDGER_PREFIX = "UDERIA_ACT_"

#: The default conditions per operation (§2a), as identifiers + plain words. The
#: compiler (B2) renders them; the identifiers are reserved so an author can neither
#: redeclare nor shadow them. Order is significant and fixed.
DEFAULT_CONDITIONS: Dict[str, Dict[str, Tuple[Tuple[str, str], ...]]] = {
    "update": {
        "pre": (("row_exists", "the keyed row exists before the write"),),
        "post": (("one_row_affected", "exactly one row was affected"),),
    },
    "insert": {
        "pre": (("row_absent", "no row with this key exists before the write"),),
        "post": (("one_row_present", "exactly one row with this key exists after"),),
    },
    "upsert": {
        "pre": (),
        "post": (("one_row_affected", "exactly one row was affected"),),
    },
}
RESERVED_CONDITION_IDS = frozenset(
    cid for op in DEFAULT_CONDITIONS.values() for side in op.values() for cid, _w in side)

#: A bound larger than this is refused at the model: an "action" that may touch more
#: rows per invocation is a batch job, and belongs to a different contract.
MAX_ROWS_CEILING = 1000


class ActionManifestError(ValueError):
    """A manifest violated a rule decidable from the manifest alone. ``code`` is from
    :data:`ERROR_CODES`; ``message`` names the offending member and the closest
    declared name where one exists."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


#: Every code this module can raise — closed, so callers branch on it and the gate
#: asserts each one fires.
ERROR_CODES = frozenset({
    "bad_manifest_shape", "bad_action_name", "bad_version", "missing_backend",
    "bad_description", "missing_target", "missing_target_entity", "missing_deployed",
    "bad_deployed", "missing_key", "bad_key", "bad_operation", "no_attributes",
    "bad_attribute", "duplicate_attribute", "bad_attribute_type", "bad_value_domain",
    "default_outside_domain", "key_is_writable", "key_not_supplied",
    "bad_conditions", "bad_condition", "duplicate_condition", "reserved_condition",
    "missing_bound", "bad_bound", "missing_authority", "bad_role", "bad_posture",
    "bad_ledger", "bad_ledger_columns", "ledger_column_writable",
})


def _fail(code: str, message: str) -> None:
    raise ActionManifestError(code, message)


def _closest(name: str, candidates) -> str:
    hits = difflib.get_close_matches(str(name), [str(c) for c in candidates], n=2, cutoff=0.6)
    return f" — closest declared: {', '.join(hits)}" if hits else ""


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


# ── The model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionAttribute:
    """One WRITABLE column — the list is an allowlist: a column not listed cannot be
    set by this action, whatever the invocation carries."""
    name: str
    type: str
    required: bool = False
    value_domain: Optional[Tuple[Any, ...]] = None
    default: Any = None
    has_default: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "type": self.type, "required": self.required}
        if self.value_domain is not None:
            d["value_domain"] = list(self.value_domain)
        if self.has_default:
            d["default"] = self.default
        return d


@dataclass(frozen=True)
class ActionCondition:
    """An author-declared predicate over the target row(s). ``id`` names it in a refusal
    and a receipt; ``expr`` is a boolean predicate over the target's declared columns
    (A4 parses it and checks it references only declared columns)."""
    id: str
    expr: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "expr": self.expr}


@dataclass(frozen=True)
class ActionManifest:
    action: str
    version: int
    backend: str
    entity: str                       # the target's logical entity in the ontology
    deployed: str                     # db.table — resolver output, FROZEN (A1)
    key: Tuple[str, ...]              # the grain: which rows one invocation may touch
    operation: str
    attributes: Tuple[ActionAttribute, ...]
    max_rows: int
    required_role: str
    posture: str
    pre: Tuple[ActionCondition, ...] = ()
    post: Tuple[ActionCondition, ...] = ()
    ledger_mode: str = "platform"
    ledger_columns: Tuple[str, ...] = ()
    description: str = ""

    # -- projections ---------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "action": self.action, "version": self.version,
            "description": self.description, "backend": self.backend,
            "target": {"entity": self.entity, "deployed": self.deployed, "key": list(self.key)},
            "operation": self.operation,
            "attributes": [a.to_dict() for a in self.attributes],
            "conditions": {"pre": [c.to_dict() for c in self.pre],
                           "post": [c.to_dict() for c in self.post]},
            "bound": {"max_rows": self.max_rows},
            "authority": {"required_role": self.required_role, "posture": self.posture},
            "ledger": {"mode": self.ledger_mode},
        }
        if self.ledger_mode == "target_audit_columns":
            d["ledger"]["columns"] = list(self.ledger_columns)
        return d

    def attribute(self, name: str) -> Optional[ActionAttribute]:
        return next((a for a in self.attributes if a.name == name), None)

    def attribute_names(self) -> Tuple[str, ...]:
        return tuple(a.name for a in self.attributes)

    def target_db(self) -> str:
        return self.deployed.split(".", 1)[0]

    def target_table(self) -> str:
        return self.deployed.split(".", 1)[1]


# ── Canonicalisation (the signable content) ─────────────────────────────────

def canonical_json(manifest: "ActionManifest | Dict[str, Any]") -> str:
    d = manifest.to_dict() if isinstance(manifest, ActionManifest) else manifest
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)


def manifest_hash(manifest: "ActionManifest | Dict[str, Any]") -> str:
    """sha256 over the canonical form — what the attestation binds. Hash the PARSED
    manifest (callers pass ``parse_manifest(raw)``) so an author's key order, a missing
    optional field written as its default, or whitespace never moves it."""
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


# ── Derived facts (pure functions of the manifest) ──────────────────────────

def effective_conditions(m: ActionManifest) -> Dict[str, List[Dict[str, str]]]:
    """The conditions the action ENFORCES: the operation's defaults (§2a) first, then
    the author's additions. The defaults are a function of the operation — never typed,
    never removable — so a validated manifest and a performed write agree by
    construction."""
    defaults = DEFAULT_CONDITIONS[m.operation]
    out: Dict[str, List[Dict[str, str]]] = {}
    for side, authored in (("pre", m.pre), ("post", m.post)):
        out[side] = ([{"id": cid, "kind": "default", "words": words} for cid, words in defaults[side]]
                     + [{"id": c.id, "kind": "authored", "expr": c.expr} for c in authored])
    return out


def ledger_identifier(m: ActionManifest) -> Optional[str]:
    """``<target db>.UDERIA_ACT_<table>`` under the platform ledger, ``None`` when the
    action records into the target's own audit columns. Truncated to Teradata's
    128-character object-name limit."""
    if m.ledger_mode != "platform":
        return None
    name = (LEDGER_PREFIX + m.target_table())[:128]
    return f"{m.target_db()}.{name}"


def ledger_ddl(m: ActionManifest) -> Optional[str]:
    """The platform ledger's CREATE TABLE (Teradata) — append-only audit of every
    performed invocation: who, when, which invocation, which action version, and the
    BEFORE-image of the columns the action may touch (JSON, so the ledger's shape does
    not follow the target's). NoPI: the ledger is written once per invocation and read
    rarely. Pure; the deployer runs it."""
    ident = ledger_identifier(m)
    if ident is None:
        return None
    db, table = ident.split(".", 1)
    return (f'CREATE MULTISET TABLE "{db}"."{table}", NO FALLBACK ('
            f'"performed_at" TIMESTAMP(6) WITH TIME ZONE NOT NULL, '
            f'"principal" VARCHAR(200) CHARACTER SET UNICODE NOT NULL, '
            f'"action_name" VARCHAR(64) CHARACTER SET LATIN NOT NULL, '
            f'"action_version" INTEGER NOT NULL, '
            f'"invocation_hash" CHAR(64) CHARACTER SET LATIN NOT NULL, '
            f'"statement_hash" CHAR(64) CHARACTER SET LATIN NOT NULL, '
            f'"posture" VARCHAR(16) CHARACTER SET LATIN NOT NULL, '
            f'"rows_affected" INTEGER, '
            f'"key_json" VARCHAR(4000) CHARACTER SET UNICODE, '
            f'"before_json" VARCHAR(32000) CHARACTER SET UNICODE'
            f') NO PRIMARY INDEX;')


#: The rights an operation needs ON ITS TARGET (Teradata AccessRight codes) — the ONE table
#: A6 judges and the Designer's form names (I insert · U update · R select).
TARGET_RIGHTS: Dict[str, Tuple[str, ...]] = {"insert": ("I", "R"), "update": ("U", "R"), "upsert": ("I", "U", "R")}
RIGHT_WORDS: Dict[str, str] = {"I": "INSERT", "U": "UPDATE", "R": "SELECT"}


def required_rights(m: ActionManifest) -> Dict[str, Tuple[str, ...]]:
    """The Teradata rights the role needs — EXACTLY these, and nothing wider (A6):
    on the target, the operation's write right(s) plus SELECT (the pre/post checks and
    the before-image read the row); on the ledger, INSERT only (append-only)."""
    target = TARGET_RIGHTS[m.operation]
    out: Dict[str, Tuple[str, ...]] = {m.deployed: tuple(sorted(target))}
    led = ledger_identifier(m)
    if led:
        out[led] = ("I",)
    return out


# ── Parsing + rule enforcement ──────────────────────────────────────────────

def _parse_conditions(raw_side: Any, side: str, seen: set) -> Tuple[ActionCondition, ...]:
    if raw_side is None:
        return ()
    if not isinstance(raw_side, list):
        _fail("bad_conditions", f"conditions.{side} must be a list of {{id, expr}}")
    out: List[ActionCondition] = []
    for rc in raw_side:
        if not isinstance(rc, dict):
            _fail("bad_condition", f"each {side} condition must be an object {{id, expr}}")
        cid, expr = rc.get("id"), rc.get("expr")
        if not isinstance(cid, str) or not IDENT_RE.match(cid):
            _fail("bad_condition", f"{side} condition id {cid!r} must be an identifier")
        if cid in RESERVED_CONDITION_IDS:
            _fail("reserved_condition",
                  f"{side} condition {cid!r} is a derived default (§2a) — defaults are written "
                  f"in by the platform and cannot be redeclared or removed; name yours differently")
        if cid in seen:
            _fail("duplicate_condition", f"condition id {cid!r} is declared twice")
        if not isinstance(expr, str) or not expr.strip():
            _fail("bad_condition", f"{side} condition {cid!r}: 'expr' must be a non-empty predicate")
        if ";" in expr:
            _fail("bad_condition", f"{side} condition {cid!r}: a predicate is one expression — no ';'")
        seen.add(cid)
        out.append(ActionCondition(id=cid, expr=expr.strip()))
    return tuple(out)


def parse_manifest(raw: Dict[str, Any]) -> ActionManifest:
    """Parse a raw manifest dict, enforcing every rule decidable from it alone. Raises
    :class:`ActionManifestError`; returns a frozen :class:`ActionManifest`. The input is
    never mutated."""
    if not isinstance(raw, dict):
        _fail("bad_manifest_shape", f"manifest must be an object, got {type(raw).__name__}")

    action = raw.get("action")
    if not isinstance(action, str) or not ACTION_NAME_RE.match(action):
        _fail("bad_action_name", f"action name {action!r} must match [a-z0-9_]{{1,64}}")
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        _fail("bad_version", f"version must be an integer >= 1, got {version!r}")
    backend = raw.get("backend")
    if not isinstance(backend, str) or not backend.strip():
        _fail("missing_backend", "backend is required — one deployed backend per action")
    description = raw.get("description") or ""
    if not isinstance(description, str):
        _fail("bad_description", "description must be a string")

    # -- target ------------------------------------------------------------
    target = raw.get("target")
    if not isinstance(target, dict):
        _fail("missing_target", "'target' {entity, deployed, key} is required — an action writes ONE entity")
    entity = target.get("entity")
    if not isinstance(entity, str) or not entity.strip():
        _fail("missing_target_entity", "target.entity (the logical entity in the ontology) is required")
    deployed = target.get("deployed")
    if not isinstance(deployed, str) or not deployed.strip():
        _fail("missing_deployed", "target.deployed is required — the resolver output, frozen at authoring (A1)")
    if not DEPLOYED_RE.match(deployed):
        _fail("bad_deployed", f"target.deployed {deployed!r} must be <database>.<table>, identifiers only")
    key = target.get("key")
    if not isinstance(key, list) or not key:
        _fail("missing_key", "target.key (the grain — which rows one invocation may touch) is REQUIRED")
    if not all(isinstance(k, str) and IDENT_RE.match(k) for k in key) or len(set(key)) != len(key):
        _fail("bad_key", f"target.key {key!r}: distinct identifiers only")

    operation = raw.get("operation")
    if operation not in OPERATIONS:
        _fail("bad_operation", f"operation {operation!r} must be one of {sorted(OPERATIONS)} "
                               f"(delete is v2 — its undo is the ledger's before-image)"
                               + _closest(str(operation), OPERATIONS))

    # -- attributes --------------------------------------------------------
    raw_attrs = raw.get("attributes")
    if not isinstance(raw_attrs, list) or not raw_attrs:
        _fail("no_attributes", "an action declares at least one writable attribute (the allowlist)")
    attrs: List[ActionAttribute] = []
    seen_a: set = set()
    for ra in raw_attrs:
        if not isinstance(ra, dict):
            _fail("bad_attribute", "each attribute must be an object")
        name = ra.get("name")
        if not isinstance(name, str) or not IDENT_RE.match(name):
            _fail("bad_attribute", f"attribute name {name!r} must be an identifier")
        if name in seen_a:
            _fail("duplicate_attribute", f"attribute {name!r} is listed twice")
        seen_a.add(name)
        a_type = ra.get("type")
        if a_type not in ATTRIBUTE_TYPES:
            _fail("bad_attribute_type", f"attribute {name!r}: type {a_type!r} must be one of "
                                        f"{sorted(ATTRIBUTE_TYPES)}" + _closest(str(a_type), ATTRIBUTE_TYPES))
        required = ra.get("required", False)
        if not isinstance(required, bool):
            _fail("bad_attribute", f"attribute {name!r}: 'required' must be true or false")
        dom = ra.get("value_domain")
        if dom is not None:
            if not isinstance(dom, list) or not dom or not all(_scalar(v) and v is not None for v in dom):
                _fail("bad_value_domain", f"attribute {name!r}: value_domain must be a non-empty list of scalars")
            if len({json.dumps(v, sort_keys=True) for v in dom}) != len(dom):
                _fail("bad_value_domain", f"attribute {name!r}: value_domain repeats a value")
        has_default = "default" in ra
        default = ra.get("default")
        if has_default and not _scalar(default):
            _fail("bad_attribute", f"attribute {name!r}: default must be a scalar")
        if has_default and dom is not None and default is not None and default not in dom:
            _fail("default_outside_domain", f"attribute {name!r}: default {default!r} is not in its value_domain")
        attrs.append(ActionAttribute(name=name, type=a_type, required=required,
                                     value_domain=tuple(dom) if dom is not None else None,
                                     default=default, has_default=has_default))
    names = [a.name for a in attrs]

    # -- the grain against the allowlist ----------------------------------
    if operation == "update":
        overlap = [k for k in key if k in names]
        if overlap:
            _fail("key_is_writable",
                  f"an update may not set its own grain ({', '.join(overlap)}) — changing the key "
                  f"re-targets the row; that is a different action")
    else:
        by = {a.name: a for a in attrs}
        missing = [k for k in key if k not in by or not (by[k].required or by[k].has_default)]
        if missing:
            _fail("key_not_supplied",
                  f"an {operation} must supply its key: declare {', '.join(missing)} as a required "
                  f"attribute" + _closest(missing[0], names))

    # -- conditions --------------------------------------------------------
    cond = raw.get("conditions") or {}
    if not isinstance(cond, dict) or (set(cond) - {"pre", "post"}):
        _fail("bad_conditions", "conditions must be an object with optional 'pre' and 'post' lists")
    seen_c: set = set()
    pre = _parse_conditions(cond.get("pre"), "pre", seen_c)
    post = _parse_conditions(cond.get("post"), "post", seen_c)

    # -- bound -------------------------------------------------------------
    bound = raw.get("bound")
    if not isinstance(bound, dict) or "max_rows" not in bound:
        _fail("missing_bound", "bound.max_rows is REQUIRED and never inferred — how many rows one "
                               "invocation may touch")
    max_rows = bound.get("max_rows")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not (1 <= max_rows <= MAX_ROWS_CEILING):
        _fail("bad_bound", f"bound.max_rows must be an integer 1..{MAX_ROWS_CEILING}, got {max_rows!r}")

    # -- authority ---------------------------------------------------------
    auth = raw.get("authority")
    if not isinstance(auth, dict):
        _fail("missing_authority", "authority {required_role, posture} is required")
    role = auth.get("required_role")
    if not isinstance(role, str) or not IDENT_RE.match(role):
        _fail("bad_role", f"authority.required_role {role!r} must be a database role identifier — "
                          f"the role that holds exactly the grant (A6)")
    posture = auth.get("posture", "enforce")
    if posture not in POSTURES:
        _fail("bad_posture", f"authority.posture {posture!r} must be one of {sorted(POSTURES)}"
                             + _closest(str(posture), POSTURES))

    # -- ledger (on by default — §13) --------------------------------------
    led = raw.get("ledger")
    if led is None:
        led = {"mode": "platform"}
    if not isinstance(led, dict) or led.get("mode", "platform") not in LEDGER_MODES:
        _fail("bad_ledger", f"ledger.mode must be one of {sorted(LEDGER_MODES)}")
    ledger_mode = led.get("mode", "platform")
    ledger_columns: Tuple[str, ...] = ()
    if ledger_mode == "target_audit_columns":
        cols = led.get("columns")
        if not isinstance(cols, list) or not cols or not all(isinstance(c, str) and IDENT_RE.match(c) for c in cols):
            _fail("bad_ledger_columns", "ledger.mode target_audit_columns names the target's own audit "
                                        "columns: a non-empty list of identifiers")
        clash = [c for c in cols if c in names]
        if clash:
            _fail("ledger_column_writable", f"audit column(s) {', '.join(clash)} are also writable "
                                            f"attributes — the audit trail is the platform's, never the caller's")
        ledger_columns = tuple(cols)
    elif set(led) - {"mode"}:
        _fail("bad_ledger", "the platform ledger takes no further settings")

    return ActionManifest(
        action=action, version=version, backend=backend.strip(), entity=entity.strip(),
        deployed=deployed, key=tuple(key), operation=operation, attributes=tuple(attrs),
        max_rows=max_rows, required_role=role, posture=posture, pre=pre, post=post,
        ledger_mode=ledger_mode, ledger_columns=ledger_columns, description=description,
    )
