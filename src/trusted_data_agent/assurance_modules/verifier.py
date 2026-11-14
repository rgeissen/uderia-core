"""
T1 — the verified-pure Python subset.

`core/safe_expression.py` sandboxes a single flow EXPRESSION by walking its AST against an
allowlist. This is the same idea raised to module scope, for the one place on the platform
where third-party code runs beside the answer: an assessor sees the answer, the retrieved
chunks and the tool arguments, and a gate can act on what it returns.

WHY STATIC VERIFICATION RATHER THAN A RUNTIME SANDBOX. The in-flight lanes are latency-
bound — this platform has already frozen a server once by putting a model call on the hot
path — so process-per-assessment is the wrong cost. A statically verified subset achieves
the isolation before anything runs, and the code that will not fit in it is confined to
the post-hoc lane instead (tier T2), where it cannot touch an answer.

WHAT THIS IS NOT. It is not a defence against a determined attacker with commit access to
the pack, and it never claims to be: it is the machine-checkable half of a decision a
signer still makes. The honest limit is stated on the sign-off surface.

The rules mirror `safe_expression`'s reasoning, in particular the one that matters most:
**any name or attribute beginning with `_` is refused**, which severs the dunder-traversal
escape (`"".__class__.__mro__[1].__subclasses__()` → `os`) that makes a naive "strip
builtins" sandbox worthless.
"""

from __future__ import annotations

import ast
from typing import List, Set

#: Modules a pure assessor may import. Deliberately tiny and closed: everything here is
#: computation over values already in hand. Adding `os`, `pathlib`, `socket`, `subprocess`,
#: `importlib` or `requests` would each, on their own, end the guarantee.
ALLOWED_IMPORTS: Set[str] = {
    "re", "math", "json", "collections", "itertools", "typing", "dataclasses",
    "statistics", "string", "unicodedata", "difflib", "functools", "operator",
    "enum", "abc",
    "trusted_data_agent.agent.assurance.contract",
    "trusted_data_agent.agent.assurance.capabilities",
}

#: Names that are never callable, whatever they are bound to. `getattr`/`setattr` are here
#: because they reconstruct attribute access from a string, which would walk straight past
#: the underscore rule.
BANNED_CALLS: Set[str] = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "input", "breakpoint", "memoryview",
}

#: The node types a pure assessor needs. Anything absent is refused rather than ignored —
#: an allowlist that silently permits an unknown construct is not an allowlist.
ALLOWED_NODES = frozenset({
    ast.Module, ast.ClassDef, ast.FunctionDef, ast.Return, ast.Assign, ast.AugAssign,
    ast.AnnAssign, ast.Expr, ast.If, ast.For, ast.While, ast.Break, ast.Continue,
    ast.Pass, ast.With, ast.withitem, ast.Try, ast.ExceptHandler, ast.Raise,
    ast.Import, ast.ImportFrom, ast.alias,
    ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call, ast.IfExp,
    ast.Attribute, ast.Subscript, ast.Name, ast.Load, ast.Store, ast.Del,
    ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set, ast.Slice, ast.Starred,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp, ast.comprehension,
    ast.arguments, ast.arg, ast.keyword, ast.FormattedValue, ast.JoinedStr,
    ast.And, ast.Or, ast.Not, ast.Invert, ast.UAdd, ast.USub,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.LShift, ast.RShift, ast.BitOr, ast.BitXor, ast.BitAnd, ast.MatMult,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn, ast.Lambda,
})

MAX_SOURCE_CHARS = 20_000


def _import_allowed(name: str) -> bool:
    """A dotted module is allowed if it, or a parent package, is on the list."""
    if not name:
        return False
    parts = name.split(".")
    return any(".".join(parts[:i]) in ALLOWED_IMPORTS for i in range(1, len(parts) + 1))


def verify_source(source: str) -> List[str]:
    """Every reason this source is not verifiably pure. Empty list = it is.

    Reasons carry line numbers because they are shown verbatim on the sign-off surface —
    a refusal an author cannot locate is a refusal they will work around rather than fix.
    """
    problems: List[str] = []

    if len(source) > MAX_SOURCE_CHARS:
        problems.append(
            f"source is {len(source)} chars; the verified tier caps at {MAX_SOURCE_CHARS} "
            f"— larger logic belongs in the post-hoc tier, where it cannot reach an answer")
        return problems

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"line {exc.lineno}: cannot be parsed ({exc.msg})"]

    for node in ast.walk(tree):
        if type(node) not in ALLOWED_NODES:
            problems.append(
                f"line {getattr(node, 'lineno', 0)}: {type(node).__name__} is not "
                f"permitted in the verified tier")
            continue

        if isinstance(node, ast.Import):
            for a in node.names:
                if not _import_allowed(a.name):
                    problems.append(
                        f"line {node.lineno}: imports {a.name!r}, which a pure assessor "
                        f"may not use")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                problems.append(
                    f"line {node.lineno}: relative imports are not permitted — a pack "
                    f"must be readable on its own")
            elif not _import_allowed(mod):
                problems.append(
                    f"line {node.lineno}: imports from {mod!r}, which a pure assessor "
                    f"may not use")

        elif isinstance(node, ast.Attribute):
            # The escape hatch this rule closes: attribute traversal through dunders
            # reaches the interpreter's object graph and, from there, anything at all.
            if node.attr.startswith("_"):
                problems.append(
                    f"line {node.lineno}: attribute {node.attr!r} — names beginning with "
                    f"'_' are not reachable in the verified tier")

        elif isinstance(node, ast.Name):
            # A leading single `_` is idiomatic and harmless (`_helper`, `_i`); the
            # dangerous form is the dunder, which reaches the interpreter's object graph.
            if node.id.startswith("__"):
                problems.append(
                    f"line {node.lineno}: name {node.id!r} is not reachable in the "
                    f"verified tier")
            if isinstance(getattr(node, "ctx", None), ast.Load) and node.id in BANNED_CALLS:
                problems.append(
                    f"line {node.lineno}: {node.id!r} is not available to a pure assessor")

        # `Global`/`Nonlocal` need no branch of their own: they are absent from
        # ALLOWED_NODES, so the type check above already refuses them. A second rule for
        # the same construct is a second place to forget.

    # Deduplicate while preserving order: one construct can trip two rules, and a list
    # that repeats itself reads as more findings than there are.
    seen = set()
    out = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
