"""Decimal division that Teradata truncates to a literal's scale — a WARNING, never a rewrite.

Teradata types ``1.0/3`` as DECIMAL(2,1) and returns 0.3; ``7/100.0`` returns 0.1 (the true value
is 0.07); ``7*1.0/3`` returns 2.3. A division takes the scale of its decimal operands, and a
literal written with one or two places — the usual way to "force a non-integer division" in
other dialects — fixes the result to that many places. Nothing errors; the figure is silently
coarse (measured on Teradata 20.00, 2026-10-01; the `teradata-patterns` findings recorded credits
summing to 304 instead of 322 from it).

The fix changes the RESULT, so it is never applied mechanically: the caller attaches a note to the
statement's result naming the expression and the two ways to get precision (a FLOAT operand, or a
literal with more places), and the model decides.

The rule, on the parse tree:

* a division whose operands carry a decimal literal of at most two places;
* no FLOAT anywhere in it (``1.0E0``, ``CAST(… AS FLOAT)``) and no explicit decimal cast with
  more places — those already carry the precision;
* not when the literal is a multiplier of 100 or more in the numerator (``x*100.0/total``, a
  percentage at 0.1-point resolution, is a deliberate idiom) — as a divisor (``x/100.0``) it is
  flagged, because there the scale cuts the fraction itself;
* not when the division is wrapped in ``ROUND``/``TRUNC``/a decimal ``CAST`` to no more places than
  the literal gives — the author chose that precision.

Columns are of unknown scale, so the note says "unless the other operand has more decimal
places". Teradata only; pure; never raises.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

NOTE_MARKER = "[Preflight:decimal]"

_FLOAT_TYPES = {"FLOAT", "DOUBLE", "DOUBLE PRECISION", "REAL"}
_INT_TYPES = {"INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT", "BYTEINT"}

# (kind, scale, literals) — kind: "float" | "known" | "unknown"; scale: max known places;
# literals: [(text, places, is_numerator_multiplier)] decimal literals in the arithmetic subtree.
_Facts = Tuple[str, int, list]


def _literal_places(text: str) -> Optional[int]:
    t = text.strip()
    if "e" in t.lower():
        return None                                   # an exponent makes it FLOAT
    if "." not in t:
        return 0
    return len(t.split(".", 1)[1])


def _cast_facts(node) -> Optional[_Facts]:
    to = node.args.get("to")
    if to is None:
        return None
    name = to.this.name.upper() if hasattr(to.this, "name") else str(to.this).upper()
    if name in _FLOAT_TYPES:
        return ("float", 0, [])
    if name in {"DECIMAL", "NUMERIC", "NUMBER"}:
        exprs = to.expressions or []
        if len(exprs) >= 2:
            try:
                return ("known", int(exprs[1].sql()), [])
            except Exception:  # noqa: BLE001
                return ("unknown", 0, [])
        return ("known", 0, [])
    if name in _INT_TYPES:
        return ("known", 0, [])
    return ("unknown", 0, [])


def _facts(node, exp) -> _Facts:
    if isinstance(node, exp.Literal) and not node.is_string:
        places = _literal_places(node.this)
        if places is None:
            return ("float", 0, [])
        return ("known", places, [(node.this, places)] if places > 0 else [])
    if isinstance(node, (exp.Paren, exp.Neg)):
        return _facts(node.this, exp)
    if isinstance(node, (exp.Cast, exp.TryCast)):
        return _cast_facts(node) or ("unknown", 0, [])
    if isinstance(node, (exp.Mul, exp.Div, exp.Add, exp.Sub)):
        a, b = _facts(node.left, exp), _facts(node.right, exp)
        if "float" in (a[0], b[0]):
            # The literals are kept: a FLOAT operand is what makes them harmless, and the
            # decision says so explicitly rather than by losing them here.
            return ("float", 0, a[2] + b[2])
        kind = "unknown" if "unknown" in (a[0], b[0]) else "known"
        return (kind, max(a[1], b[1]), a[2] + b[2])
    return ("unknown", 0, [])


def _numerator_multipliers(div, exp) -> set:
    """Literal texts that multiply the numerator (``x*100.0/t``, ``100.0*x/t``)."""
    out = set()
    node = div.left
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Mul):
        for side in (node.left, node.right):
            s = side
            while isinstance(s, exp.Paren):
                s = s.this
            if isinstance(s, exp.Literal) and not s.is_string:
                out.add(s.this)
    return out


def _chosen_precision(div, exp) -> Optional[int]:
    """Places the author fixed explicitly around the division (ROUND/TRUNC/decimal CAST)."""
    node = div.parent
    while isinstance(node, exp.Paren):
        node = node.parent
    if isinstance(node, (exp.Round,)) or (isinstance(node, exp.Anonymous) and node.name.upper() == "TRUNC"):
        dec = node.args.get("decimals") if isinstance(node, exp.Round) else (
            node.expressions[1] if len(node.expressions) > 1 else None)
        try:
            return int(dec.sql()) if dec is not None else 0
        except Exception:  # noqa: BLE001
            return None
    if isinstance(node, (exp.Cast, exp.TryCast)):
        f = _cast_facts(node)
        if f and f[0] == "known":
            return f[1]
    return None


def truncating_divisions(sql: str) -> List[Dict[str, Any]]:
    """``[{"expr", "places", "literal"}]`` — divisions Teradata truncates to a short literal scale."""
    if not isinstance(sql, str) or "/" not in sql:
        return []
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(sql, read="teradata")
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for div in tree.find_all(exp.Div):
        kind, scale, lits = _facts(div, exp)
        if kind == "float" or not lits:
            continue
        mult = _numerator_multipliers(div, exp)
        considered = [(t, p) for t, p in lits
                      if not (t in mult and _magnitude(t) >= 100)]
        if not considered:
            continue
        places = max(p for _, p in considered)
        if places > 2 or scale > 2:
            continue
        chosen = _chosen_precision(div, exp)
        if chosen is not None and chosen <= places:
            continue
        text = div.sql(dialect="teradata")
        if any(text in s for s in seen):
            continue
        seen.add(text)
        out.append({"expr": text, "places": places, "literal": considered[0][0]})
    return out


def _magnitude(text: str) -> float:
    try:
        return abs(float(text))
    except ValueError:
        return 0.0


def note(findings: List[Dict[str, Any]]) -> Optional[str]:
    """The recovery text attached to the statement's result."""
    if not findings:
        return None
    f = findings[0]
    more = f" (and {len(findings) - 1} more)" if len(findings) > 1 else ""
    return (f"{NOTE_MARKER} `{f['expr']}`{more} is computed as DECIMAL with {f['places']} decimal "
            f"place(s): Teradata gives a decimal division the scale of its operands, so `7/100.0` "
            f"returns 0.1 and `1.0/3` returns 0.3 — unless the other operand has more decimal places. "
            f"If the figure needs more precision, cast one operand to FLOAT (`CAST(x AS FLOAT)`) or "
            f"write the literal with more places (`1.0000`), and rerun before reporting it.")
