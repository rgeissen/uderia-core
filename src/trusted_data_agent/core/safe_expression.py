"""
Safe expression evaluator for Flow component nodes.

`task_function` nodes evaluate a Python expression and `decision` nodes evaluate
a boolean Python expression. These expressions are frequently authored by the LLM
(and therefore susceptible to hallucination / prompt injection) and can travel
between users via Platform Flows, forks, and the marketplace. Evaluating them with
the builtin ``eval()`` — even after swapping ``__builtins__`` for a small dict — is
NOT a sandbox: any string in scope can be walked back to ``object`` via dunder
attributes (``"".__class__.__mro__[1].__subclasses__()``) to reach ``os`` /
``subprocess`` and achieve arbitrary code execution, server-side, under the
victim's auth.

This module replaces that pattern with a strict AST allowlist:

  * Parse the expression in ``eval`` mode and walk every node.
  * Permit only a fixed set of literal, operator, comprehension and call node
    types — reject lambdas, assignments, ``import``, ``yield``/``await`` etc.
  * Reject any identifier or attribute beginning with ``_`` — this severs every
    known dunder-traversal escape (``__class__``, ``__globals__``,
    ``__subclasses__``, ``__builtins__`` …) while still allowing ordinary methods
    like ``.lower()``, ``.strip()``, ``.split()``.
  * Restrict bare-name function calls to a vetted ``SAFE_FUNCTIONS`` table.
  * Evaluate with ``__builtins__`` emptied so no builtin is reachable by name.

The result is an evaluator that supports the data-shaping and boolean-routing
expressions flows actually need, with no path to arbitrary code execution.

Residual, intentionally-accepted limitation: a CPU-bomb such as ``9 ** 9 ** 9``
is computation, not code execution. We mitigate the obvious cases (oversized
source, large ``**`` exponents) but do not claim wall-clock DoS protection — flow
runs already execute inside a cancellable asyncio task with a per-node timeout.
"""

from __future__ import annotations

import ast
from typing import Any, Callable, Dict, Optional

__all__ = ["UnsafeExpressionError", "safe_eval", "SAFE_FUNCTIONS"]


class UnsafeExpressionError(ValueError):
    """Raised when an expression contains a construct outside the allowlist."""


# ── Limits ─────────────────────────────────────────────────────────────────────
# Source length cap — guards against pathological parse inputs.
_MAX_SOURCE_LEN = 2000
# Reject `**` with a constant exponent above this — cheap CPU-bomb mitigation.
_MAX_POW_EXPONENT = 1000


# ── Vetted callables available to expressions ──────────────────────────────────
# Only names listed here may be called as bare functions (e.g. `len(output)`).
# Every entry is a pure, side-effect-free builtin. No I/O, no introspection.
SAFE_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


# ── AST node allowlist ─────────────────────────────────────────────────────────
# Container / literal / operator / comprehension / call nodes that are safe to
# evaluate. Anything not present is rejected by the validator. Operator *marker*
# nodes (ast.Add, ast.Eq, …) are included so the generic walk does not trip on
# them; the semantically meaningful nodes (Call, Name, Attribute) get dedicated
# checks in _Validator.
_ALLOWED_NODES = frozenset(
    t for t in (
        # Root
        getattr(ast, "Expression", None),
        # Literals & names
        getattr(ast, "Constant", None),
        getattr(ast, "Name", None),
        getattr(ast, "Load", None),
        getattr(ast, "Store", None),  # comprehension targets
        # Collections
        getattr(ast, "List", None),
        getattr(ast, "Tuple", None),
        getattr(ast, "Set", None),
        getattr(ast, "Dict", None),
        getattr(ast, "Starred", None),
        # Attribute access (method calls like .lower()/.split()); the
        # no-underscore rule in _Validator.visit_Attribute blocks dunders.
        getattr(ast, "Attribute", None),
        # Subscripting / slicing
        getattr(ast, "Subscript", None),
        getattr(ast, "Slice", None),
        getattr(ast, "Index", None),       # py < 3.9
        getattr(ast, "ExtSlice", None),    # py < 3.9
        # Boolean / comparison / arithmetic / unary
        getattr(ast, "BoolOp", None),
        getattr(ast, "And", None),
        getattr(ast, "Or", None),
        getattr(ast, "UnaryOp", None),
        getattr(ast, "Not", None),
        getattr(ast, "UAdd", None),
        getattr(ast, "USub", None),
        getattr(ast, "Invert", None),
        getattr(ast, "BinOp", None),
        getattr(ast, "Add", None),
        getattr(ast, "Sub", None),
        getattr(ast, "Mult", None),
        getattr(ast, "Div", None),
        getattr(ast, "FloorDiv", None),
        getattr(ast, "Mod", None),
        getattr(ast, "Pow", None),
        getattr(ast, "LShift", None),
        getattr(ast, "RShift", None),
        getattr(ast, "BitOr", None),
        getattr(ast, "BitXor", None),
        getattr(ast, "BitAnd", None),
        getattr(ast, "MatMult", None),
        getattr(ast, "Compare", None),
        getattr(ast, "Eq", None),
        getattr(ast, "NotEq", None),
        getattr(ast, "Lt", None),
        getattr(ast, "LtE", None),
        getattr(ast, "Gt", None),
        getattr(ast, "GtE", None),
        getattr(ast, "Is", None),
        getattr(ast, "IsNot", None),
        getattr(ast, "In", None),
        getattr(ast, "NotIn", None),
        # Conditional expression
        getattr(ast, "IfExp", None),
        # Comprehensions
        getattr(ast, "ListComp", None),
        getattr(ast, "SetComp", None),
        getattr(ast, "DictComp", None),
        getattr(ast, "GeneratorExp", None),
        getattr(ast, "comprehension", None),
        # Calls
        getattr(ast, "Call", None),
        getattr(ast, "keyword", None),
        # f-strings (inner expressions are recursively validated)
        getattr(ast, "JoinedStr", None),
        getattr(ast, "FormattedValue", None),
    )
    if t is not None
)


class _Validator(ast.NodeVisitor):
    """Walks the AST and raises UnsafeExpressionError on any disallowed construct."""

    def generic_visit(self, node: ast.AST) -> None:
        if type(node) not in _ALLOWED_NODES:
            raise UnsafeExpressionError(
                f"disallowed expression element: {type(node).__name__}"
            )
        super().generic_visit(node)

    # -- Names: no leading underscore (blocks dunder access via bare names) ------
    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("_"):
            raise UnsafeExpressionError(f"access to '{node.id}' is not allowed")
        self.generic_visit(node)

    # -- Attributes: no leading underscore (blocks __class__, __globals__, …) ----
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise UnsafeExpressionError(
                f"access to attribute '{node.attr}' is not allowed"
            )
        self.generic_visit(node)

    # -- Calls: bare-name calls must target a vetted function -------------------
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id not in SAFE_FUNCTIONS:
                raise UnsafeExpressionError(f"call to '{func.id}' is not allowed")
        elif isinstance(func, ast.Attribute):
            # Method call — visit_Attribute enforces the no-underscore rule.
            pass
        else:
            raise UnsafeExpressionError("unsupported call target")
        self.generic_visit(node)

    # -- Pow: cap constant exponents (cheap CPU-bomb mitigation) ----------------
    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Pow):
            exp = node.right
            if isinstance(exp, ast.Constant) and isinstance(exp.value, int):
                if abs(exp.value) > _MAX_POW_EXPONENT:
                    raise UnsafeExpressionError("exponent too large")
        self.generic_visit(node)


def safe_eval(
    expression: str,
    names: Optional[Dict[str, Any]] = None,
    extra_functions: Optional[Dict[str, Callable[..., Any]]] = None,
) -> Any:
    """Evaluate ``expression`` against ``names`` with a strict AST allowlist.

    Args:
        expression: A single Python expression (no statements).
        names: Variables available to the expression (flow params, upstream
            outputs, global variables). User-supplied data only — never callables.
        extra_functions: Optional additional vetted callables to expose by name,
            merged over ``SAFE_FUNCTIONS``.

    Returns:
        The value the expression evaluates to.

    Raises:
        UnsafeExpressionError: The expression uses a disallowed construct, is too
            long, or fails to parse.
        Exception: Any runtime error raised by the (validated) expression itself
            — propagated to the caller unchanged.
    """
    if not isinstance(expression, str):
        raise UnsafeExpressionError("expression must be a string")
    if len(expression) > _MAX_SOURCE_LEN:
        raise UnsafeExpressionError("expression too long")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"invalid expression syntax: {exc.msg}") from exc

    _Validator().visit(tree)

    namespace: Dict[str, Any] = dict(SAFE_FUNCTIONS)
    if extra_functions:
        namespace.update(extra_functions)
    if names:
        # User data shadows function names, mirroring normal Python scoping.
        namespace.update(names)

    compiled = compile(tree, filename="<flow-expression>", mode="eval")
    # Empty __builtins__ so no builtin is reachable by name; only `namespace`
    # (vetted functions + user data) is in scope.
    return eval(compiled, {"__builtins__": {}}, namespace)  # noqa: S307 — AST-allowlisted
