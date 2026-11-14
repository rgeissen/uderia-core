"""Declare a business CONCEPT on a knowledge graph — deterministic, product-neutral.

A use case names concepts a data product never declared ("digital channel"); without
a declaration every agent re-derives the rule from the columns and any one of several
defensible readings can come back. Declaring the rule ONCE gives the plain-language
question one answer for every agent, and gives the platform something to compile.

One declaration produces, on the graph the agents read:
  * a `business_concept` node — the term, its prose definition, the predicate;
  * a `metric` node the platform's metric compiler consumes deterministically
    (`SUM(CASE WHEN <predicate> THEN 1 ELSE 0 END)` over the entity's deployed table),
    so `compile_metric` answers the question without an agent writing SQL;
  * a `query_recipe` node — the proven statement, the cookbook's shape;
  * edges tying all three to the entity and to the columns the predicate names.

Nothing here executes SQL or writes to the product's database. The Memory-module
statements a product owner could apply are RETURNED as text (the product's own
glossary/cookbook tables), never run — the rule's content is the owner's.

Fails closed: the predicate must parse as a boolean expression, and every column it
names must be a declared attribute of the entity — a concept is grounded in declared
columns or it is refused, naming what is missing.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\b")
_SQL_WORDS = {"and", "or", "not", "in", "is", "null", "like", "between", "case", "when", "then", "else",
              "end", "true", "false", "coalesce", "trim", "upper", "lower", "cast", "as", "integer",
              "trycast", "exists", "select", "from", "where", "sum", "count", "distinct"}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def predicate_columns(predicate: str) -> List[str]:
    """Identifiers a predicate names, minus SQL words, string literals and qualifiers."""
    text = re.sub(r"'[^']*'", "''", predicate or "")
    out: List[str] = []
    for m in _IDENT_RE.finditer(text):
        tok = m.group(1)
        # `i.V20081` — the qualifier before a dot is an alias, not a column
        if m.end() < len(text) and text[m.end():m.end() + 1] == ".":
            continue
        if tok.lower() in _SQL_WORDS or tok.isdigit():
            continue
        if tok not in out:
            out.append(tok)
    return out


def predicate_is_boolean(predicate: str) -> Tuple[bool, str]:
    """Does the predicate parse as a WHERE condition? sqlglot when present, a balanced
    parenthesis + no-statement check otherwise. Never executes anything."""
    p = (predicate or "").strip()
    if not p:
        return False, "the predicate is empty"
    if re.search(r";|--|/\*", p):
        return False, "the predicate must be one boolean expression (no statements or comments)"
    if p.count("(") != p.count(")"):
        return False, "unbalanced parentheses"
    try:
        import sqlglot  # optional at runtime
        sqlglot.parse_one(f"SELECT 1 FROM t WHERE {p}")
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — the parser's reason IS the user-facing message
        return False, f"the predicate does not parse as a condition: {str(exc)[:160]}"
    return True, ""


def build_declaration(*, concept: str, definition: str, predicate: str, entity: Dict[str, Any],
                      entity_attributes: List[str], dimensions: Optional[List[Dict[str, str]]] = None,
                      declared_by: str = "operator") -> Dict[str, Any]:
    """The nodes, edges and optional Memory-module statements for one concept.

    ``entity`` is the graph's entity node (name + properties); ``entity_attributes`` the
    leaf names of its declared attributes. Pure: returns {"entities", "relationships",
    "memory_module_sql", "errors"}; nothing is written here."""
    errors: List[str] = []
    name = (concept or "").strip()
    if not name:
        errors.append("a concept name is required")
    ok, why = predicate_is_boolean(predicate)
    if not ok:
        errors.append(why)
    cols = predicate_columns(predicate) if ok else []
    known = {a.lower(): a for a in entity_attributes}
    missing = [c for c in cols if c.lower() not in known]
    if cols and missing:
        errors.append("the predicate names columns the entity does not declare: " + ", ".join(missing))
    if not cols and ok:
        errors.append("the predicate names no column of the entity")
    if errors:
        return {"entities": [], "relationships": [], "memory_module_sql": "", "errors": errors}

    ename = str(entity.get("name") or "")
    eprops = entity.get("properties") or {}
    product = str(eprops.get("product") or ename.split(".", 1)[0] if "." in ename else "")
    table = str(eprops.get("deployed_table") or eprops.get("consumable_object") or ename)
    s = slug(name)
    term_name = f"{product}.term:{name}" if product else f"term:{name}"
    metric_name = f"{product}.{s}_count" if product else f"{s}_count"
    recipe_name = f"{product}.recipe:Count {name}" if product else f"recipe:Count {name}"
    sql = f"SELECT COUNT(*) AS {s}_count FROM {table} WHERE {predicate.strip()}"
    dims = [d for d in (dimensions or []) if isinstance(d, dict) and d.get("name") and d.get("expr")]

    entities = [
        {"name": term_name, "entity_type": "business_concept", "properties": {
            "label": name, "definition": definition.strip(), "predicate": predicate.strip(),
            "columns": [known[c.lower()] for c in cols], "entity": ename,
            "declared_by": declared_by, "layer": "logical"}},
        {"name": metric_name, "entity_type": "metric", "properties": {
            "label": f"{s}_count", "description": f"Number of {ename.rpartition('.')[2]} rows that are '{name}': {definition.strip()}",
            "metric_source": table, "formula": f"SUM(CASE WHEN {predicate.strip()} THEN 1 ELSE 0 END)",
            "dimensions": dims,
            "measures": [{"name": f"{s}_count", "expr": f"SUM(CASE WHEN {predicate.strip()} THEN 1 ELSE 0 END)", "additive": True}],
            "concept": name, "declared_by": declared_by, "layer": "logical"}},
        {"name": recipe_name, "entity_type": "query_recipe", "properties": {
            "label": f"Count {name}", "description": f"How many {ename.rpartition('.')[2]} rows are '{name}'",
            "sql": sql, "concept": name, "declared_by": declared_by, "layer": "logical"}},
    ]
    rels = [
        {"source_name": term_name, "source_type": "business_concept", "target_name": ename, "target_type": "entity", "relationship_type": "relates_to"},
        {"source_name": metric_name, "source_type": "metric", "target_name": ename, "target_type": "entity", "relationship_type": "measures"},
        {"source_name": recipe_name, "source_type": "query_recipe", "target_name": term_name, "target_type": "business_concept", "relationship_type": "relates_to"},
        {"source_name": metric_name, "source_type": "metric", "target_name": term_name, "target_type": "business_concept", "relationship_type": "relates_to"},
    ]
    for c in cols:
        rels.append({"source_name": term_name, "source_type": "business_concept",
                     "target_name": f"{ename}.{known[c.lower()]}", "target_type": "attribute", "relationship_type": "relates_to"})

    q = lambda v: "'" + str(v).replace("'", "''") + "'"
    mem = ""
    if product:
        mem = "\n".join([
            f"-- Memory module of {product}: apply as the product owner; the platform never runs this.",
            f"INSERT INTO {product}_Memory.Business_Glossary (term, definition, is_active) VALUES ({q(name)}, {q(definition.strip() + ' Predicate: ' + predicate.strip())}, 1);",
            f"INSERT INTO {product}_Memory.Query_Cookbook (recipe_name, description, sql_text, is_active) VALUES ({q('Count ' + name)}, {q('How many rows are ' + name)}, {q(sql)}, 1);",
        ])
    return {"entities": entities, "relationships": rels, "memory_module_sql": mem, "errors": []}
