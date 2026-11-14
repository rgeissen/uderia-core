"""The normalisation advisor — attribute, value domain or entity? (PROP-008 §4.2, increment A2).

Pure: no I/O, no store, no model. Every decision is made by CODE from evidence, in the
verdict shape ``dbcatalog.intelligence.infer_role`` already uses — every reason listed,
``unknown`` below the evidence floor, never a guess. The model's part is the residue
(§2a): it may NAME a thing the rules found, it may never overrule a ``must``.

Inputs
    candidate   the thing being proposed: ``{"name", "description", "data_type",
                "values": [..] | None, "positions": [..] | None, "family": str | None,
                "referenced_by": [entity names], "has_lifecycle": bool | None,
                "declared": "attribute" | "entity" | "value_domain" | None}``
    entity      the target entity: ``{"name", "key": [columns], "deployed": "db.table"}``
    probes      what the data said, as returned by ``apply_probes`` (or ``{}`` when no rows
                could be measured): ``{"max_per_key", "determines", "rows", "errors"}``

Output
    Verdict(decision, confidence, reasons, provisional, findings, must, rule)

The six rules, in the order they are checked (§4.2):
    functional_dependency   > 1 value per entity key          ⇒ entity (MUST)
    multiplicity            repeating group / list / array    ⇒ entity with a sequence key
    hidden_identity         the candidate determines ≥ 2 cols ⇒ entity whose key it is
    shared_reference        referenced from ≥ 2 entities      ⇒ entity with FKs
    independent_lifecycle   own timestamps / history          ⇒ entity
    closed_enumeration      code + label, closed value set    ⇒ value_domain (never entity)
    (none fires)                                              ⇒ attribute

Precedence: a DECLARED decision wins over an inferred one; a contradiction between the
declaration and the data is a FINDING, surfaced, never silently overridden. Where no rows
were measured only the declared rules and the structural rules apply and the verdict says
``provisional`` with the reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

ATTRIBUTE = "attribute"
VALUE_DOMAIN = "value_domain"
ENTITY = "entity"
UNKNOWN = "unknown"
DECISIONS = (ATTRIBUTE, VALUE_DOMAIN, ENTITY, UNKNOWN)

RULES = ("functional_dependency", "multiplicity", "hidden_identity", "shared_reference",
         "independent_lifecycle", "closed_enumeration")

# a repeating group's suffix: "address_1", "phone-2", "offence 3"
_SUFFIX_RE = re.compile(r"^(?P<stem>.+?)[\s_\-]+(?P<n>\d{1,2})$")
_LIST_TYPE_RE = re.compile(r"\b(array|list|json|multiset|set)\b", re.I)
_LIFECYCLE_RE = re.compile(r"(valid_from|valid_to|effective_(from|to|date)|start_(ts|date|time)|end_(ts|date|time)"
                           r"|_history$|_hist$|created_(at|ts|dts)|updated_(at|ts|dts)|version)", re.I)
_CODE_RE = re.compile(r"(_code|_cd|_type|_status|_flag|_ind|_category|_class)$", re.I)
_MAX_ENUM_VALUES = 64          # a value set larger than this is data, not a vocabulary
_MAX_ENUM_LEN = 40             # a "code" longer than this is prose


@dataclass
class Verdict:
    decision: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    provisional: bool = False
    findings: List[str] = field(default_factory=list)
    must: bool = False
    rule: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"decision": self.decision, "confidence": round(self.confidence, 2), "reasons": list(self.reasons),
                "provisional": self.provisional, "findings": list(self.findings), "must": self.must,
                "rule": self.rule}


# ── probes: the SQL the caller runs (dialect-neutral; sqlite and Teradata both accept it) ──
def fd_probe(table: str, keys: Sequence[str], candidate: str) -> str:
    """How many distinct values of ``candidate`` does ONE entity key take, at most?
    ``> 1`` anywhere means the candidate is not functionally dependent on the key —
    it is one-to-many, an entity of its own. The ``key_probe`` shape one column over."""
    k = ", ".join(keys)
    return (f"SELECT MAX(n) AS max_n, COUNT(*) AS groups_n FROM "
            f"(SELECT {k}, COUNT(DISTINCT {candidate}) AS n FROM {table} "
            f"WHERE {candidate} IS NOT NULL GROUP BY {k}) x")


def transitive_probe(table: str, candidate: str, column: str) -> str:
    """Does ``candidate`` DETERMINE ``column``? (one value of column per candidate value)"""
    return (f"SELECT MAX(n) AS max_n FROM "
            f"(SELECT {candidate}, COUNT(DISTINCT {column}) AS n FROM {table} "
            f"WHERE {candidate} IS NOT NULL AND {column} IS NOT NULL GROUP BY {candidate}) x")


def row_probe(table: str) -> str:
    return f"SELECT COUNT(*) AS rows_n FROM {table}"


def distinct_probe(table: str, candidate: str) -> str:
    return f"SELECT COUNT(DISTINCT {candidate}) AS distinct_n FROM {table} WHERE {candidate} IS NOT NULL"


def values_probe(table: str, candidate: str) -> str:
    """The value set, grouped — the caller bounds the rows (``_MAX_ENUM_VALUES + 1`` is
    enough: one row over the ceiling proves the set is open)."""
    return (f"SELECT {candidate} AS v, COUNT(*) AS n FROM {table} WHERE {candidate} IS NOT NULL "
            f"GROUP BY {candidate}")


def probe_plan(candidate: Dict[str, Any], entity: Dict[str, Any],
               other_columns: Sequence[str] = (), max_columns: int = 24) -> Dict[str, str]:
    """Every statement the advisor would like to see answered, keyed by what it measures.
    The caller runs what it can; an unanswered probe is simply absent from ``probes``.
    Built only when the candidate already EXISTS as a column of the deployed table
    (``candidate["column"]``); a brand-new attribute has no rows to measure."""
    table = str(entity.get("deployed") or "").strip()
    keys = [k for k in (entity.get("key") or []) if k]
    col = str(candidate.get("column") or "").strip()
    if not (table and keys and col):
        return {}
    plan = {"rows": row_probe(table), "fd": fd_probe(table, keys, col),
            "distinct": distinct_probe(table, col), "values": values_probe(table, col)}
    for other in list(other_columns)[:max_columns]:
        if other and other != col and other not in keys:
            plan[f"det:{other}"] = transitive_probe(table, col, other)
    return plan


def apply_probes(plan: Dict[str, str], results: Dict[str, Optional[List[Dict[str, Any]]]]) -> Dict[str, Any]:
    """Fold probe results (``key → rows`` as list-of-dicts, ``None`` = the probe failed)
    into the evidence shape ``advise`` reads. Never raises."""
    out: Dict[str, Any] = {"rows": None, "max_per_key": None, "groups": None, "distinct": None,
                           "values": None, "determines": [], "errors": []}

    def first(rows, name):
        if not rows or not isinstance(rows[0], dict):
            return None
        for k, v in rows[0].items():
            if str(k).strip().lower() == name:
                try:
                    return int(v) if v is not None else None
                except Exception:  # noqa: BLE001
                    return None
        return None

    for key in plan:
        rows = results.get(key)
        if rows is None:
            out["errors"].append(key)
            continue
        if key == "rows":
            out["rows"] = first(rows, "rows_n")
        elif key == "fd":
            out["max_per_key"] = first(rows, "max_n")
            out["groups"] = first(rows, "groups_n")
        elif key == "distinct":
            out["distinct"] = first(rows, "distinct_n")
        elif key == "values":
            vals = []
            for r in rows:
                if isinstance(r, dict):
                    for k, v in r.items():
                        if str(k).strip().lower() == "v":
                            vals.append(v)
                            break
            out["values"] = vals
        elif key.startswith("det:"):
            n = first(rows, "max_n")
            if n is not None and n == 1:
                out["determines"].append(key[4:])
    return out


# ── the rules ──────────────────────────────────────────────────────────────────
def _multiplicity(c: Dict[str, Any]) -> Optional[str]:
    pos = c.get("positions")
    if pos and isinstance(pos, (list, tuple)) and len(pos) >= 1 and c.get("family"):
        return f"repeating group: '{c.get('family')}' position {list(pos)} — one row per position"
    name = str(c.get("name") or "")
    m = _SUFFIX_RE.match(name)
    if m and c.get("siblings") and any(_SUFFIX_RE.match(str(s)) and _SUFFIX_RE.match(str(s)).group("stem") == m.group("stem")
                                        for s in c.get("siblings") or () if str(s) != name):
        return f"repeating group: '{m.group('stem')}' appears with several position suffixes"
    dt = str(c.get("data_type") or "")
    if _LIST_TYPE_RE.search(dt):
        return f"list-valued type {dt}: several values per row"
    if c.get("delimited"):
        return "delimited list in one value: several values per row"
    return None


def _closed_enumeration(c: Dict[str, Any]) -> Optional[str]:
    values = c.get("values")
    if not values or not isinstance(values, (list, tuple, dict)):
        return None
    items = list(values.items()) if isinstance(values, dict) else [(v, None) for v in values]
    if not (1 < len(items) <= _MAX_ENUM_VALUES):
        return None
    if any(len(str(k)) > _MAX_ENUM_LEN for k, _ in items):
        return None
    labelled = isinstance(values, dict) and all(v for _, v in items)
    name_says_code = bool(_CODE_RE.search(str(c.get("name") or "")))
    # A value set the DATA showed is only a vocabulary when the name says code (any
    # low-cardinality column has few values — a city is not a code); a value set the
    # proposer DECLARED is a closed set by declaration.
    measured = bool(c.get("values_measured"))
    if (measured and name_says_code) or (not measured):
        return (f"closed set of {len(items)} value(s)" + (" with labels" if labelled else "")
                + (" declared" if not measured else " in the data")
                + " — a vocabulary on the attribute, never a table of its own")
    return None


def advise(candidate: Dict[str, Any], entity: Dict[str, Any],
           probes: Optional[Dict[str, Any]] = None) -> Verdict:
    """Attribute, value domain or entity — from evidence, with the rule named."""
    c = dict(candidate or {})
    p = dict(probes or {})
    ent_name = str((entity or {}).get("name") or "the entity")
    reasons: List[str] = []
    findings: List[str] = []
    measured = p.get("rows") is not None and (p.get("rows") or 0) > 0 and "fd" not in (p.get("errors") or [])
    inferred: Optional[str] = None
    rule: Optional[str] = None
    must = False
    confidence = 0.0

    # 1 functional dependency — the MUST
    mpk = p.get("max_per_key")
    if measured and mpk is not None:
        if mpk > 1:
            inferred, rule, must, confidence = ENTITY, "functional_dependency", True, 0.95
            reasons.append(f"up to {mpk} distinct values per key of {ent_name}: one-to-many, "
                           f"not a property of one {ent_name}")
        else:
            reasons.append(f"at most one value per key of {ent_name} (functional dependency holds)")

    # 2 multiplicity
    if inferred is None:
        why = _multiplicity(c)
        if why:
            inferred, rule, confidence = ENTITY, "multiplicity", 0.85
            reasons.append(why + " — an entity with a sequence key")

    # the value set the data showed, when the candidate declared none
    if not c.get("values") and p.get("values") and p.get("distinct") is not None \
            and p["distinct"] <= _MAX_ENUM_VALUES and len(p["values"]) <= _MAX_ENUM_VALUES:
        c = dict(c, values=list(p["values"]), values_measured=True)
    enum_why = _closed_enumeration(c)

    # 3 hidden identity — with an evidence floor: a column that GROUPS several entity
    # keys (fewer distinct values than keys, at least three of them) and determines ≥ 2
    # others. A column one-to-one with the key determines everything the key does — it
    # is a candidate key of THIS entity, not a hidden one. A closed vocabulary (a code
    # with its label) is NEVER an entity — the two-column decode rule.
    if inferred is None and measured and not enum_why:
        det = [d for d in (p.get("determines") or []) if d]
        dn, gn = p.get("distinct"), p.get("groups") or (p.get("rows") or 0)
        floor = dn is not None and 3 <= dn < gn
        if len(det) >= 2 and floor:
            inferred, rule, confidence = ENTITY, "hidden_identity", 0.8
            reasons.append(f"'{c.get('name')}' determines {len(det)} other column(s) "
                           f"({', '.join(det[:4])}{'…' if len(det) > 4 else ''}): a key of its own")

    # 4 shared reference
    if inferred is None:
        refs = [r for r in (c.get("referenced_by") or []) if r and r != ent_name]
        if len(refs) >= 2:
            inferred, rule, confidence = ENTITY, "shared_reference", 0.75
            reasons.append(f"referenced from {len(refs)} entities ({', '.join(map(str, refs[:4]))}): "
                           f"one entity with a foreign key from each")

    # 5 independent lifecycle
    if inferred is None:
        lifecycle = c.get("has_lifecycle")
        if lifecycle is None:
            lifecycle = bool(_LIFECYCLE_RE.search(str(c.get("name") or "")))
        if lifecycle:
            inferred, rule, confidence = ENTITY, "independent_lifecycle", 0.7
            reasons.append("carries its own timestamps / history: a lifecycle of its own")

    # 6 closed enumeration — never an entity
    if inferred is None:
        why = _closed_enumeration(c)
        if why:
            inferred, rule, confidence = VALUE_DOMAIN, "closed_enumeration", 0.85
            reasons.append(why)

    if inferred is None:
        inferred, rule = ATTRIBUTE, None
        confidence = 0.8 if measured else 0.5
        reasons.append(f"no rule fires: an attribute of {ent_name}")

    # evidence floor: probes were planned and ALL failed → unknown
    if p.get("errors") and not measured and p.get("rows") is None and inferred == ATTRIBUTE and not c.get("declared"):
        return Verdict(UNKNOWN, 0.0, reasons + [f"the data could not be measured ({', '.join(p['errors'][:3])})"],
                       provisional=True, findings=findings, must=False, rule=None)

    provisional = not measured and rule in (None, "closed_enumeration", "shared_reference", "independent_lifecycle", "multiplicity")
    if not measured:
        reasons.append("no rows to measure — only the declared and structural rules applied")

    # precedence: a declaration wins; a contradiction with the data is a finding
    declared = c.get("declared")
    if declared in (ATTRIBUTE, VALUE_DOMAIN, ENTITY):
        if declared != inferred:
            if must:
                findings.append(f"declared '{declared}' but the data shows one-to-many "
                                f"(up to {mpk} values per key) — the declaration is contradicted, "
                                f"the data wins")
                # a MUST is a must: the declaration does not reinstate an attribute the data refutes
            elif rule is None:
                # nothing fired — the default reading ("an attribute") is not evidence against a
                # declaration: a brand-new thing has nothing in the data describing it yet
                # the default reading's own sentence ("an attribute of X") is not a reason once the
                # declaration stands — a declared ENTITY must not be explained as an attribute
                reasons[:] = [r for r in reasons if not r.startswith("no rule fires")]
                reasons.append(f"declared '{declared}' — nothing measured or structural contradicts it"
                               + ("" if measured else "; nothing in the data describes it yet"))
                inferred, rule, confidence = declared, "declared", 0.85 if measured else 0.7
            else:
                findings.append(f"declared '{declared}'; the rules inferred '{inferred}'"
                                + (f" ({rule})" if rule else "") + " — the declaration wins")
                inferred, rule, confidence = declared, "declared", 0.9
                provisional = False if measured else provisional
        else:
            reasons.append(f"declared '{declared}' agrees")
            confidence = max(confidence, 0.9)

    return Verdict(inferred, confidence, reasons, provisional=provisional, findings=findings, must=must, rule=rule)


def overrule(verdict: Verdict, chosen: Optional[str]) -> Verdict:
    """The model's residue: it may choose a decision the rules left open; it may NEVER
    overrule a ``must``. Returns the verdict that stands (with the refusal recorded)."""
    if not chosen or chosen == verdict.decision:
        return verdict
    if chosen not in DECISIONS:
        return verdict
    if verdict.must:
        return Verdict(verdict.decision, verdict.confidence, verdict.reasons,
                       provisional=verdict.provisional,
                       findings=verdict.findings + [f"'{chosen}' refused: {verdict.rule} is a must"],
                       must=True, rule=verdict.rule)
    if verdict.decision == UNKNOWN or verdict.rule is None:
        return Verdict(chosen, min(verdict.confidence, 0.6), verdict.reasons + [f"'{chosen}' chosen where the rules left it open"],
                       provisional=verdict.provisional, findings=verdict.findings, must=False, rule="chosen")
    return Verdict(verdict.decision, verdict.confidence, verdict.reasons, provisional=verdict.provisional,
                   findings=verdict.findings + [f"'{chosen}' not taken: {verdict.rule} decided '{verdict.decision}'"],
                   must=False, rule=verdict.rule)


def design_decision_props(verdict: Verdict, candidate: Dict[str, Any], entity: Dict[str, Any],
                          decided_by: str, decision_id: str, decided_at: str) -> Dict[str, Any]:
    """The ``design_decision`` node recorded for every verdict (the ANDP shape, so the
    inspector and the enrichment already render it)."""
    name = str(candidate.get("name") or "")
    ent = str((entity or {}).get("name") or "")
    return {
        "layer": "authored",
        "label": f"{verdict.decision}: {name}" + (f" on {ent}" if ent else ""),
        "decision_id": decision_id,
        "description": f"'{name}' is {('an ' if verdict.decision in (ATTRIBUTE, ENTITY) else 'a ')}{verdict.decision.replace('_', ' ')}"
                       + (f" of {ent}" if ent and verdict.decision != ENTITY else ""),
        "rule": verdict.rule,
        "evidence": list(verdict.reasons),
        "findings": list(verdict.findings),
        "rationale": "; ".join(verdict.reasons[:3]),
        "status": "provisional" if verdict.provisional else "decided",
        "confidence": round(verdict.confidence, 2),
        "must": verdict.must,
        "decided_by": decided_by,
        "decided_date": decided_at,
        "category": "normalisation",
        "source_module": "normalisation_advisor",
    }


# ── a column type from an attribute's name (Act II rehearsal) ─────────────────────────
#
# A proposal that names attributes without types used to land every column — the key
# included — as VARCHAR(4000). Most attribute names say what they hold, so the type is
# decided here, deterministically, from the name's LAST word, and stamped on the draft
# with its reason (`type_inferred`), which the deploy preview repeats so a person can
# correct it before anything lands. A name no rule recognises stays undeclared.

_TYPE_RULES = (
    # (last-word set, type, reason)
    ({"id", "ids", "uuid", "guid", "number", "no", "num", "ref", "reference"}, "VARCHAR(255)", "an identifier"),
    ({"status", "state", "stage", "priority", "type", "category", "kind", "level", "code", "class"},
     "VARCHAR(32)", "a short coded value"),
    ({"at", "timestamp", "time", "datetime"}, "TIMESTAMP", "a point in time"),
    ({"date", "on", "day"}, "DATE", "a date"),
    ({"reason", "summary", "description", "notes", "note", "comment", "comments", "text", "narrative",
      "details", "detail", "remarks", "finding", "findings"}, "VARCHAR(1000)", "free text"),
    ({"name", "investigator", "officer", "analyst", "owner", "assignee", "assigned", "by", "person",
      "author", "reviewer", "detective", "agent", "user"}, "VARCHAR(128)", "a person or a name"),
    ({"count", "quantity", "qty"}, "INTEGER", "a count"),
    ({"amount", "total", "score", "value", "rate", "ratio", "price", "cost"}, "DECIMAL(18,2)", "an amount"),
)
_BOOL_PREFIX = ("is", "has", "can", "was")


def _name_words(name: str) -> List[str]:
    n = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name or ""))
    n = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", n)
    return [w.lower() for w in re.split(r"[\s_\-.]+", n) if w]


def infer_attribute_type(name: str) -> Optional[Tuple[str, str]]:
    """Pure: ``(type, reason)`` from an attribute name, or None when no rule applies.
    ``LeadID`` → VARCHAR(255) an identifier · ``Status`` → VARCHAR(32) · ``ClosureReason`` →
    VARCHAR(1000) · ``AssignedInvestigator`` → VARCHAR(128) · ``opened_at`` → TIMESTAMP ·
    ``is_confirmed`` → BOOLEAN."""
    words = _name_words(name)
    if not words:
        return None
    if words[0] in _BOOL_PREFIX and len(words) > 1:
        return "BOOLEAN", "a yes/no flag"
    last = words[-1]
    for vocab, t, why in _TYPE_RULES:
        if last in vocab:
            return t, why
    return None
