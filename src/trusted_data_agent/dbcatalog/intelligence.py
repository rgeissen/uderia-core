"""Deterministic catalog intelligence — pure functions over the NORMALISED catalog.

Everything a navigator user experiences as "it understands my schema" is computed
here from names, types, keys, foreign keys and statistics: object roles, key and
join inference, column flags (PII, text-stored dates, codes, measures), ranked
search, next actions and suggested questions. No provider knowledge, no I/O, no
model — the same rules hold for a Teradata dictionary, an information_schema and an
MCP tool result. Every verdict carries its reasons so the UI can SHOW WHY, and a
verdict the evidence does not settle is reported as unknown, never guessed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .capabilities import CatalogCapability as C
from .types import ColumnInfo, DatabaseInfo, ForeignKey, IndexInfo, ObjectDetail, ObjectInfo

# ── vocabulary ─────────────────────────────────────────────────────────────────

_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

PII_TOKENS = frozenset({
    "email", "phone", "mobile", "telephone", "fax", "ssn", "sin", "nin", "passport", "dob",
    "birthdate", "birthday", "birth", "address", "street", "zip", "zipcode", "postcode",
    "postal", "salary", "wage", "iban", "bic", "swift", "creditcard", "cardnumber", "pan",
    "licence", "license", "firstname", "lastname", "surname", "fullname", "middlename",
    "maiden", "gender", "sex", "race", "ethnicity", "religion", "nationality", "ip",
    "ipaddress", "mac", "imei", "taxid", "tin", "vat", "password", "passwd", "secret",
    "token", "apikey",
})
# two-token PII phrases ("first name", "date of birth", "credit card")
PII_PHRASES = (("first", "name"), ("last", "name"), ("full", "name"), ("date", "of", "birth"),
               ("credit", "card"), ("card", "number"), ("account", "number"), ("social", "security"),
               ("national", "id"), ("tax", "id"), ("home", "address"), ("phone", "number"),
               ("email", "address"), ("ip", "address"), ("person", "name"), ("customer", "name"),
               ("patient", "name"), ("employee", "name"))
# A bare `name` is ambiguous — a product, a city, a person. It is personal data when the
# table holds records about people (or authored by them: a review's `name` is its author).
# Judged by the table, never by the column alone: masking every `name` would mask every
# product name in a preview.
PERSON_TABLE_TOKENS = frozenset({
    "customer", "client", "user", "person", "people", "employee", "staff", "patient", "member",
    "contact", "reviewer", "review", "author", "student", "guest", "passenger", "driver",
    "owner", "applicant", "visitor", "subscriber", "participant", "respondent", "account_holder",
})
AMBIGUOUS_NAME_TOKENS = (["name"], ["display", "name"], ["user", "name"], ["username"], ["nickname"])
KEY_SUFFIXES = ("id", "key", "pk", "no", "num", "number", "code", "cd", "ident", "identifier", "uid", "guid", "sk", "nk")
MEASURE_TOKENS = frozenset({
    "amount", "amt", "qty", "quantity", "count", "cnt", "total", "sum", "price", "revenue",
    "cost", "rate", "pct", "percent", "percentage", "balance", "value", "val", "weight",
    "volume", "units", "sales", "income", "expense", "fee", "tax", "discount", "margin",
    "score", "duration", "minutes", "hours", "seconds", "length", "distance", "salary",
    "budget", "profit", "loss", "avg", "average", "min", "max", "net", "gross", "size",
})
CODE_TOKENS = frozenset({"code", "cd", "flag", "flg", "ind", "indicator", "status", "stat",
                         "type", "typ", "category", "cat", "class", "kind", "level", "lvl",
                         "tier", "segment", "seg", "reason", "mode"})
TEXT_TOKENS = frozenset({"name", "desc", "description", "title", "label", "comment", "comments",
                         "text", "note", "notes", "remark", "remarks", "subject", "summary"})
TIME_TOKENS = frozenset({"date", "dt", "time", "ts", "timestamp", "day", "month", "year", "period",
                         "created", "updated", "modified", "loaded", "effective", "expiry", "expires",
                         "start", "end", "begin"})
GENERIC_JOIN_NAMES = frozenset({"id", "name", "code", "type", "status", "description", "date",
                                "created", "updated", "value", "key", "flag", "comment", "notes"})

_NUMERIC = ("INT", "DECIMAL", "NUMBER", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "BIGINT", "SMALLINT",
            "BYTEINT", "TINYINT", "MONEY")
_DATE_TYPES = ("DATE", "TIMESTAMP", "DATETIME", "TIME")
_CHAR_TYPES = ("CHAR", "VARCHAR", "TEXT", "CLOB", "STRING", "NCHAR", "NVARCHAR", "CHARACTER")
_WIDTH_RE = re.compile(r"\((\d+)")

_ROLE_NAME_HINTS = {
    "fact": ("fact", "fct", "trans", "transaction", "sales", "orders", "order_line", "orderline",
             "invoice", "payment", "event", "events", "measure", "metric", "activity", "usage"),
    "dimension": ("dim", "customer", "customers", "product", "products", "location", "employee",
                  "store", "branch", "account", "vendor", "supplier", "party", "person", "geo",
                  "calendar", "region", "site", "asset"),
    "lookup": ("ref", "lkp", "lookup", "codes", "code", "types", "type", "status", "category",
               "categories", "domain", "enum", "mapping", "map", "xref", "crosswalk"),
    "log": ("log", "logs", "audit", "history", "hist", "archive", "arch", "trace", "snapshot"),
    "staging": ("stg", "staging", "stage", "tmp", "temp", "work", "wrk", "load", "raw", "landing", "extract"),
    "bridge": ("bridge", "brdg", "link", "assoc", "association", "junction", "xref", "crosswalk", "map"),
}
ROLE_LABELS = {
    "fact": "Fact", "dimension": "Dimension", "lookup": "Lookup", "bridge": "Bridge",
    "log": "Log / history", "staging": "Staging", "view": "View", "macro": "Macro",
    "procedure": "Procedure", "function": "Function", "unknown": "Table",
}


# ── tokens & types ─────────────────────────────────────────────────────────────

def ident_tokens(name: str) -> List[str]:
    """``TotalRevenue`` → ["total","revenue"]; ``cust_ID`` → ["cust","id"]; keeps order."""
    s = _CAMEL_RE.sub(" ", str(name or ""))
    return [t for t in _SPLIT_RE.split(s.lower()) if t]


def stem(t: str) -> str:
    """``customers`` → ``customer``, ``sales`` → ``sale``, ``status`` → ``status``: the
    one-letter plural tolerance a name comparison needs (``address`` and ``status`` keep
    their ``s``; ``ies`` → ``y``)."""
    t = str(t or "").lower()
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def is_numeric_type(t: str) -> bool:
    return any(str(t or "").upper().startswith(p) or p in str(t or "").upper() for p in _NUMERIC)


def is_date_type(t: str) -> bool:
    return any(str(t or "").upper().startswith(p) for p in _DATE_TYPES)


def is_char_type(t: str) -> bool:
    return any(str(t or "").upper().startswith(p) for p in _CHAR_TYPES)


def char_width(t: str) -> Optional[int]:
    m = _WIDTH_RE.search(str(t or ""))
    return int(m.group(1)) if m else None


def _leaf_tokens(col: str) -> List[str]:
    return ident_tokens(col)


def _is_keyish_name(name: str) -> bool:
    toks = _leaf_tokens(name)
    if not toks:
        return False
    if toks == ["id"] or toks[-1] in KEY_SUFFIXES:
        return True
    low = str(name).lower()
    return low.endswith(KEY_SUFFIXES[:3]) and len(low) > 2


def is_person_table(table: str) -> bool:
    """A table whose rows are about people (or authored by them)."""
    toks = _leaf_tokens(str(table or "").split(".")[-1])
    singular = [x[:-1] if x.endswith("s") and len(x) > 3 else x for x in toks]
    return any(x in PERSON_TABLE_TOKENS for x in toks + singular)


def _pii_reason(name: str, comment: str = "", table: str = "") -> Optional[str]:
    toks = _leaf_tokens(name)
    joined = "".join(toks)
    for t in toks:
        if t in PII_TOKENS:
            return f"name token '{t}'"
    for phrase in PII_PHRASES:
        if "".join(phrase) in joined:
            return f"name reads '{' '.join(phrase)}'"
    if table and toks in AMBIGUOUS_NAME_TOKENS and is_person_table(table):
        return f"a name in '{str(table).split('.')[-1]}', whose rows are about people"
    c = str(comment or "").lower()
    if c and any(w in c for w in ("personal data", "pii", "confidential", "sensitive", "gdpr")):
        return "comment says it is personal or sensitive data"
    return None


def statement_tables(sql: str, dialect: str = "") -> List[str]:
    """The tables a statement reads (CTE names excluded) — the context that decides
    whether an ambiguous `name` column is a person's name. sqlglot when present, else a
    FROM/JOIN regex (the preflight catalog's precedent); empty only when nothing parses."""
    text = str(sql or "")
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(text, read=dialect or None)
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        return [t.name for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes]
    except Exception:  # noqa: BLE001
        pass
    try:
        ctes = {m.group(1).lower() for m in re.finditer(r"(?:\bWITH\s+|,\s*)([A-Za-z_][\w$]*)\s+AS\s*\(", text, re.I)}
        out: List[str] = []
        for m in re.finditer(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*){0,2})", text, re.I):
            leaf = m.group(1).split(".")[-1]
            if leaf.lower() not in ctes and leaf not in out:
                out.append(leaf)
        return out
    except Exception:  # noqa: BLE001
        return []


def masked_columns(columns: Iterable[str], tables: Iterable[str] = ()) -> List[str]:
    """The result columns a VIEW masks as personal data — by the column's own name, or by a
    bare `name` on a table whose rows are about people. ONE rule for every face that shows
    rows (the navigator's query card, its preview, the canvas result grid); never re-derived."""
    tbs = [t for t in (tables or ()) if t]
    return [c for c in (columns or []) if _pii_reason(c) or any(_pii_reason(c, "", tb) for tb in tbs)]


# ── column flags ───────────────────────────────────────────────────────────────

def column_flags(col: ColumnInfo, table_name: str = "") -> List[str]:
    """The flags a column earns from its own name, type, keys and statistics."""
    flags: List[str] = []
    name, t = col.name, col.data_type or ""
    toks = _leaf_tokens(name)
    if col.is_primary or col.is_unique:
        flags.append("key")
    elif _is_keyish_name(name):
        flags.append("identifier")
    if col.is_identity:
        flags.append("identity")
    if _pii_reason(name, col.comment, table_name):
        flags.append("pii")
    if is_date_type(t):
        flags.append("timestamp" if "TIME" in t.upper() else "date")
    elif is_char_type(t):
        try:
            from trusted_data_agent.components import dateforms as _df
            if _df.is_text_date_attr({"deployed_type": t, "name": name, "description": col.comment}):
                flags.append("text_date")
        except Exception:  # noqa: BLE001 — the date predicate is a bonus, never a dependency
            pass
    width = char_width(t)
    if is_char_type(t) and ((width is not None and width <= 3) or (toks and toks[-1] in CODE_TOKENS)):
        flags.append("code")
    if is_numeric_type(t) and "key" not in flags and "identifier" not in flags:
        if any(tok in MEASURE_TOKENS for tok in toks) or not any(tok in CODE_TOKENS for tok in toks):
            flags.append("measure")
    if is_char_type(t) and (width is None or width > 3) and any(tok in TEXT_TOKENS for tok in toks):
        flags.append("text")
    st = col.stats or {}
    if st.get("unique_values") is not None and st.get("row_count") and st["unique_values"] == st["row_count"] \
            and "key" not in flags:
        flags.append("unique_by_stats")
    return flags


def annotate_columns(columns: Sequence[ColumnInfo], table_name: str = "") -> None:
    for c in columns:
        c.flags = column_flags(c, table_name)


# ── keys ───────────────────────────────────────────────────────────────────────

def infer_keys(columns: Sequence[ColumnInfo], indexes: Sequence[IndexInfo],
               table_name: str = "") -> Dict[str, Any]:
    declared = [i for i in indexes if i.kind in ("primary", "primary_key") or i.unique]
    out: Dict[str, Any] = {
        "declared": [{"name": i.name, "kind": i.kind, "columns": list(i.columns), "unique": i.unique}
                     for i in declared],
        "inferred": [],
    }
    if declared:
        return out
    ttoks = {stem(t) for t in ident_tokens(table_name)}
    for c in columns:
        toks = _leaf_tokens(c.name)
        if not toks:
            continue
        st = c.stats or {}
        unique_by_stats = st.get("unique_values") is not None and st.get("row_count") \
            and st["unique_values"] == st["row_count"]
        if toks == ["id"] or (toks[-1] in ("id", "key", "pk") and ({stem(t) for t in toks[:-1]} & ttoks or len(toks) == 1)):
            out["inferred"].append({"columns": [c.name], "confidence": 0.9 if unique_by_stats else 0.7,
                                    "reason": "named like the table's own key" + (", unique per statistics" if unique_by_stats else "")})
        elif unique_by_stats and _is_keyish_name(c.name):
            out["inferred"].append({"columns": [c.name], "confidence": 0.8,
                                    "reason": "key-shaped name and unique per statistics"})
    out["inferred"].sort(key=lambda k: -k["confidence"])
    return out


# ── roles ──────────────────────────────────────────────────────────────────────

def infer_role(info: ObjectInfo, columns: Sequence[ColumnInfo], fks_out: Sequence[ForeignKey],
               fks_in: Sequence[ForeignKey]) -> Dict[str, Any]:
    """What KIND of table is this — fact, dimension, bridge, lookup, log, staging?
    Evidence-ranked; a table nothing settles is ``unknown`` with the evidence listed."""
    if info.kind in ("view", "macro", "procedure", "function"):
        return {"role": info.kind, "confidence": 1.0, "reasons": [f"a {info.kind}"]}
    reasons: List[str] = []
    toks = set(ident_tokens(info.name))
    flags = {c.name: set(c.flags) for c in columns}
    n_cols = len(columns)
    keyish = [c for c in columns if flags[c.name] & {"key", "identifier"}]
    measures = [c for c in columns if "measure" in flags[c.name]]
    texts = [c for c in columns if "text" in flags[c.name]]
    codes = [c for c in columns if "code" in flags[c.name]]
    times = [c for c in columns if flags[c.name] & {"date", "timestamp", "text_date"}]
    declared_out = [f for f in fks_out if f.declared]
    n_out, n_in = len(fks_out), len(fks_in)
    rc = info.row_count

    def hinted(role: str) -> bool:
        return bool(toks & set(_ROLE_NAME_HINTS[role]))

    scores: Dict[str, float] = {k: 0.0 for k in ("fact", "dimension", "lookup", "bridge", "log", "staging")}
    if hinted("staging"):
        scores["staging"] += 3; reasons.append("name hints at staging")
    if hinted("log"):
        scores["log"] += 2; reasons.append("name hints at a log or history")
    if hinted("bridge"):
        scores["bridge"] += 2; reasons.append("name hints at a bridge")
    if hinted("fact"):
        scores["fact"] += 1.5; reasons.append("name hints at a fact")
    if hinted("dimension"):
        scores["dimension"] += 1.5; reasons.append("name hints at a dimension")
    if hinted("lookup"):
        scores["lookup"] += 1.5; reasons.append("name hints at a lookup")

    if n_out >= 2 or len(keyish) >= 2:
        if n_cols <= len(keyish) + 2 and n_cols <= 6 and not measures:
            scores["bridge"] += 3; reasons.append(f"{max(n_out, len(keyish))} keys and almost nothing else")
        if measures:
            scores["fact"] += 3; reasons.append(f"{max(n_out, len(keyish))} keys with {len(measures)} measure(s)")
        elif n_cols > 6:
            scores["fact"] += 1
    if declared_out and measures:
        scores["fact"] += 1.5; reasons.append("declared foreign keys plus measures")
    if n_in >= 1 and n_out <= 1:
        scores["dimension"] += 2; reasons.append(f"referenced by {n_in} table(s)")
    if texts and n_out <= 1 and not measures:
        scores["dimension"] += 1.5; reasons.append("descriptive text columns, few keys")
    if n_cols <= 5 and codes and (rc is None or rc < 5000) and not measures:
        scores["lookup"] += 2.5; reasons.append("a few columns around a code" + (f", {rc:,} rows" if rc is not None else ""))
    if times and not fks_in and (hinted("log") or any(t in toks for t in ("event", "events", "audit"))):
        scores["log"] += 2
    if rc is not None and rc >= 1_000_000 and (measures or len(keyish) >= 2):
        scores["fact"] += 1.5; reasons.append(f"{rc:,} rows")
    if rc is not None and rc < 200 and n_cols <= 6:
        scores["lookup"] += 1

    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] < 2:
        return {"role": "unknown", "confidence": 0.0,
                "reasons": reasons or ["not enough structural evidence"]}
    total = sum(scores.values()) or 1.0
    return {"role": best[0], "confidence": round(min(0.95, best[1] / (total * 0.6 + 1)), 2), "reasons": reasons}


# ── joins ──────────────────────────────────────────────────────────────────────

def _type_family(t: str) -> str:
    if is_numeric_type(t):
        return "n"
    if is_char_type(t):
        return "c"
    if is_date_type(t):
        return "d"
    return "o"


def join_candidates(detail: ObjectDetail, peers: Sequence[Tuple[ObjectInfo, Sequence[ColumnInfo]]],
                    limit: int = 12) -> List[ForeignKey]:
    """Joins the DICTIONARY did not declare but the NAMES strongly suggest — shared,
    key-shaped column names with compatible types, the side that owns the column as a
    key as the parent. Never offered where a declared key already covers the pair."""
    declared_pairs = {(f.parent_database.lower(), f.parent_table.lower()) for f in detail.foreign_keys_out} | \
                     {(f.child_database.lower(), f.child_table.lower()) for f in detail.foreign_keys_in}
    mine = {c.name.lower(): c for c in detail.columns}
    me = detail.info
    out: List[ForeignKey] = []
    for peer, pcols in peers:
        if peer.name.lower() == me.name.lower() and peer.database.lower() == me.database.lower():
            continue
        if (peer.database.lower(), peer.name.lower()) in declared_pairs:
            continue
        for pc in pcols:
            c = mine.get(pc.name.lower())
            if c is None:
                continue
            leaf = c.name.lower()
            keyish = _is_keyish_name(leaf) or c.is_primary or pc.is_primary
            if not keyish or leaf in GENERIC_JOIN_NAMES:
                continue
            if c.data_type and pc.data_type and _type_family(c.data_type) != _type_family(pc.data_type):
                continue
            # direction: whoever holds it as a primary key is the parent; else the table
            # whose own name is inside the column name ("customer_id" → customers)
            ptoks = {stem(t) for t in ident_tokens(peer.name)}; mtoks = {stem(t) for t in ident_tokens(me.name)}; ctoks = {stem(t) for t in ident_tokens(leaf)}
            if pc.is_primary and not c.is_primary:
                parent, child, conf, why = peer, me, 0.8, f"'{c.name}' is {peer.name}'s primary key"
            elif c.is_primary and not pc.is_primary:
                parent, child, conf, why = me, peer, 0.8, f"'{c.name}' is {me.name}'s primary key"
            elif ctoks & ptoks:
                parent, child, conf, why = peer, me, 0.6, f"'{c.name}' names {peer.name}"
            elif ctoks & mtoks:
                parent, child, conf, why = me, peer, 0.6, f"'{c.name}' names {me.name}"
            else:
                parent, child, conf, why = peer, me, 0.45, f"both carry key-shaped column '{c.name}'"
            out.append(ForeignKey(child_database=child.database, child_table=child.name, child_columns=[c.name if child is me else pc.name],
                                  parent_database=parent.database, parent_table=parent.name, parent_columns=[pc.name if parent is peer else c.name],
                                  name="", declared=False, confidence=conf, reason=why))
    # one verdict per child column: a weaker "both carry the column" candidate must not
    # sit beside the parent whose PRIMARY KEY that column is
    out.sort(key=lambda f: -f.confidence)
    best_for: Dict[Tuple[str, str], float] = {}
    kept: List[ForeignKey] = []
    for f in out:
        k = (f.child_table.lower(), f.child_columns[0].lower())
        top = best_for.get(k)
        if top is not None and f.confidence < 0.6 and top >= 0.6:
            continue
        best_for.setdefault(k, f.confidence)
        kept.append(f)
    return kept[:limit]


# ── search ─────────────────────────────────────────────────────────────────────

def _score_name(name: str, terms: Sequence[str]) -> Tuple[int, str]:
    low = str(name or "").lower()
    toks = set(ident_tokens(low))
    score, why = 0, ""
    for t in terms:
        if low == t:
            score += 100; why = "exact"
        elif stem(low) == stem(t):
            score += 95; why = "exact"          # the plural of the asked-for name IS the name
        elif low.startswith(t):
            score += 40; why = why or "prefix"
        elif t in toks:
            score += 25; why = why or "word"
        elif t in low:
            score += 10; why = why or "contains"
    return score, why


def search_terms(q: str) -> List[str]:
    return [t for t in ident_tokens(q) if len(t) >= 2]


def rank_search(q: str, databases: Sequence[DatabaseInfo], objects: Sequence[ObjectInfo],
                columns: Optional[Dict[str, Sequence[ColumnInfo]]] = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Identifier-token ranking, the KG handler's rules applied to a catalog: exact
    beats prefix beats word beats substring; a comment mention is weak evidence; a
    qualified ``db.table`` query matches both parts."""
    terms = search_terms(q)
    if not terms:
        return []
    hits: List[Dict[str, Any]] = []
    for d in databases:
        s, why = _score_name(d.name, terms)
        if s:
            hits.append({"kind": "database", "database": d.name, "score": s + 5, "why": why})
    for o in objects:
        s, why = _score_name(o.name, terms)
        cs = 5 if o.comment and any(t in o.comment.lower() for t in terms) else 0
        if s or cs:
            hits.append({"kind": o.kind if o.kind != "table" else "object", "database": o.database,
                         "object": o.name, "score": s + cs + 10, "why": why or "comment"})
    for qual, cols in (columns or {}).items():
        db, _, obj = qual.partition(".")
        for c in cols:
            s, why = _score_name(c.name, terms)
            cs = 3 if c.comment and any(t in c.comment.lower() for t in terms) else 0
            if s or cs:
                hits.append({"kind": "column", "database": db, "object": obj, "column": c.name,
                             "data_type": c.data_type, "score": s + cs, "why": why or "comment",
                             "flags": list(c.flags)})
    hits.sort(key=lambda h: (-h["score"], h.get("database", ""), h.get("object", ""), h.get("column", "")))
    return hits[:limit]


# ── actions, questions, sample SQL ──────────────────────────────────────────────

def _quote(ident: str, dialect: str) -> str:
    if dialect == "mysql":
        return f"`{ident}`"
    return '"' + str(ident).replace('"', '""') + '"'


def sample_sql(detail: ObjectDetail, dialect: str = "teradata", limit: int = 20) -> str:
    if dialect == "mongodb":
        # a document store's statement is JSON (collection + pipeline), never SQL
        from trusted_data_agent.core.mongo_query import sample_statement
        return sample_statement(detail.info.name, [c.name for c in detail.columns], limit)
    cols = [c.name for c in detail.columns][:8] or ["*"]
    proj = ", ".join(_quote(c, dialect) if c != "*" else c for c in cols)
    db, name = detail.info.database, detail.info.name
    tbl = _quote(name, dialect) if dialect == "sqlite" else f"{_quote(db, dialect)}.{_quote(name, dialect)}"
    if dialect == "teradata":
        return f"SELECT TOP {limit} {proj}\nFROM {tbl}"
    return f"SELECT {proj}\nFROM {tbl}\nLIMIT {limit}"


def suggested_questions(detail: ObjectDetail, role: str) -> List[str]:
    name = detail.info.name
    flags = {c.name: set(c.flags) for c in detail.columns}
    measures = [c.name for c in detail.columns if "measure" in flags[c.name]][:2]
    dims = [c.name for c in detail.columns if flags[c.name] & {"code", "text"} and "pii" not in flags[c.name]][:2]
    dates = [c.name for c in detail.columns if flags[c.name] & {"date", "timestamp", "text_date"}][:1]
    parents = [f.parent_table for f in detail.foreign_keys_out][:2]
    qs: List[str] = [f"How many rows does {name} have?"]
    if role == "fact" and measures:
        m = measures[0]
        if dates:
            qs.append(f"What is the total {m} in {name} by month of {dates[0]}?")
        if dims:
            qs.append(f"Which {dims[0]} has the highest total {m} in {name}?")
        if parents:
            qs.append(f"Break {m} in {name} down by {parents[0]}.")
    elif role in ("dimension", "lookup"):
        if dims:
            qs.append(f"List the distinct values of {dims[0]} in {name}.")
        if detail.foreign_keys_in:
            child = detail.foreign_keys_in[0].child_table
            qs.append(f"How many {child} rows belong to each {name}?")
    elif role == "bridge" and parents:
        qs.append(f"Which {parents[0]} rows are linked to more than one {parents[1] if len(parents) > 1 else 'partner'} through {name}?")
    elif role in ("log", "staging") and dates:
        qs.append(f"How many rows landed in {name} per day of {dates[0]} over the last month?")
    if measures and len(qs) < 4:
        qs.append(f"What are the minimum, maximum and average {measures[0]} in {name}?")
    return qs[:4]


def suggest_actions(detail: ObjectDetail, caps: Iterable[str], dialect: str = "teradata",
                    ontology_present: bool = False) -> List[Dict[str, Any]]:
    caps = {str(getattr(c, "value", c)) for c in caps}
    info = detail.info
    acts: List[Dict[str, Any]] = []
    if C.PREVIEW.value in caps and info.kind in ("table", "view"):
        acts.append({"id": "preview", "label": "Preview rows", "kind": "data"})
    if C.ROW_COUNT.value in caps and info.kind in ("table", "view"):
        acts.append({"id": "count", "label": "Count rows", "kind": "data"})
    acts.append({"id": "copy", "label": "Copy qualified name", "kind": "clipboard", "payload": info.qualified})
    acts.append({"id": "sql", "label": "Sample query", "kind": "clipboard", "payload": sample_sql(detail, dialect)})
    if detail.view_text:
        acts.append({"id": "view_text", "label": "Show definition", "kind": "text"})
    flags = {c.name: set(c.flags) for c in detail.columns}
    if any("measure" in f for f in flags.values()) and any(f & {"code", "text", "date", "timestamp"} for f in flags.values()):
        acts.append({"id": "chart", "label": "Chart it", "kind": "agent",
                     "payload": f"Chart the main measure of {info.qualified} by its most natural dimension."})
    if ontology_present:
        acts.append({"id": "graph", "label": "Open in graph", "kind": "graph", "payload": info.qualified})
    if detail.foreign_keys_out or detail.foreign_keys_in:
        acts.append({"id": "joins", "label": "Show join paths", "kind": "nav"})
    return acts


# ── database-level summary & helpers ────────────────────────────────────────────

def summarize_objects(objects: Sequence[ObjectInfo]) -> Dict[str, Any]:
    out = {"objects": len(objects), "tables": 0, "views": 0, "other": 0, "rows_known": 0,
           "size_bytes": 0, "with_comments": 0}
    for o in objects:
        if o.kind == "table":
            out["tables"] += 1
        elif o.kind == "view":
            out["views"] += 1
        else:
            out["other"] += 1
        if o.row_count is not None:
            out["rows_known"] += o.row_count
        if o.size_bytes:
            out["size_bytes"] += o.size_bytes
        if o.comment:
            out["with_comments"] += 1
    return out


def humanize_bytes(n: Optional[int]) -> str:
    if n is None:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def stats_age_days(ts: str, now_iso: Optional[str] = None) -> Optional[int]:
    """Days since a dictionary timestamp; None when it cannot be read."""
    import datetime as _dt
    if not ts:
        return None
    try:
        t = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")[:26])
    except ValueError:
        try:
            t = _dt.datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    now = _dt.datetime.fromisoformat(now_iso) if now_iso else _dt.datetime.now(t.tzinfo)
    if t.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return max(0, (now - t).days)


def scope_status(database: str, scope_dbs: Optional[Iterable[str]]) -> str:
    """``in_scope`` / ``out_of_scope`` against the agent's trusted scope, or ``unscoped``
    when the agent has no bound ontology (nothing to be outside of)."""
    dbs = {str(d).lower() for d in (scope_dbs or [])}
    if not dbs:
        return "unscoped"
    return "in_scope" if str(database).lower() in dbs else "out_of_scope"


def enrich_detail(detail: ObjectDetail, peers: Sequence[Tuple[ObjectInfo, Sequence[ColumnInfo]]] = (),
                  caps: Iterable[str] = (), dialect: str = "teradata", scope_dbs: Optional[Iterable[str]] = None,
                  ontology_present: bool = False) -> ObjectDetail:
    """Stamp every deterministic reading onto an ObjectDetail (mutates + returns it)."""
    annotate_columns(detail.columns, detail.info.name)
    role = infer_role(detail.info, detail.columns, detail.foreign_keys_out, detail.foreign_keys_in)
    keys = infer_keys(detail.columns, detail.indexes, detail.info.name)
    joins = join_candidates(detail, peers)
    flags_count: Dict[str, int] = {}
    for c in detail.columns:
        for f in c.flags:
            flags_count[f] = flags_count.get(f, 0) + 1
    detail.intelligence = {
        "role": role["role"], "role_label": ROLE_LABELS.get(role["role"], "Table"),
        "role_confidence": role["confidence"], "role_reasons": role["reasons"],
        "keys": keys,
        "inferred_joins": [j.to_dict() for j in joins],
        "flag_counts": flags_count,
        "scope": scope_status(detail.info.database, scope_dbs),
        "actions": suggest_actions(detail, caps, dialect, ontology_present),
        "questions": suggested_questions(detail, role["role"]),
        "sample_sql": sample_sql(detail, dialect),
        "stats_age_days": stats_age_days(detail.stats_collected_at) if detail.stats_collected_at else None,
        "size_human": humanize_bytes(detail.info.size_bytes),
    }
    return detail
