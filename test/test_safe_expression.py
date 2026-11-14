#!/usr/bin/env python3
"""
Unit + adversarial tests for the Flow safe expression evaluator.

Covers safe_expression.safe_eval — the AST-allowlisted replacement for the
former eval()-with-swapped-__builtins__ pattern used by task_function and
decision nodes.

Two halves:
  1. Functional   — every construct flows legitimately need still works.
  2. Adversarial  — every known sandbox-escape / injection vector is rejected
                    with UnsafeExpressionError (NOT silently evaluated).

Usage:
    python test/test_safe_expression.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from trusted_data_agent.core.safe_expression import (  # noqa: E402
    UnsafeExpressionError,
    safe_eval,
)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}")


def expect_value(name, expr, names, expected):
    try:
        got = safe_eval(expr, names)
        check(f"{name}  ({expr!r} -> {got!r})", got == expected)
    except Exception as exc:  # noqa: BLE001
        check(f"{name}  ({expr!r} raised {type(exc).__name__}: {exc})", False)


def expect_blocked(name, expr, names=None):
    """The expression must be rejected as unsafe BEFORE any evaluation."""
    try:
        safe_eval(expr, names or {})
        check(f"BLOCK {name}  ({expr!r} was NOT blocked!)", False)
    except UnsafeExpressionError:
        check(f"BLOCK {name}", True)
    except Exception as exc:  # noqa: BLE001
        # Any other exception means the expression started evaluating — a leak.
        check(f"BLOCK {name}  (wrong error {type(exc).__name__}: {exc})", False)


def expect_runtime_error(name, expr, names, exc_type):
    """A *validated* expression may still raise a real runtime error — it must
    propagate unchanged, NOT be masked as UnsafeExpressionError."""
    try:
        safe_eval(expr, names)
        check(f"RUNTIME {name}  (no error raised)", False)
    except UnsafeExpressionError as exc:
        check(f"RUNTIME {name}  (wrongly blocked: {exc})", False)
    except exc_type:
        check(f"RUNTIME {name}", True)
    except Exception as exc:  # noqa: BLE001
        check(f"RUNTIME {name}  (got {type(exc).__name__})", False)


# ── Functional ─────────────────────────────────────────────────────────────────

def test_functional():
    print("\n[functional — constructs flows legitimately need]")
    expect_value("arithmetic", "2 + 3 * 4", {}, 14)
    expect_value("float cast + var", "float(output) * multiplier",
                 {"output": "2.5", "multiplier": 4}, 10.0)
    expect_value("int cast", "int(output) + 1", {"output": "41"}, 42)
    expect_value("comparison", "int(count) < 100", {"count": "37"}, True)
    expect_value("bool ops", "a and not b", {"a": True, "b": False}, True)
    expect_value("conditional expr", "'hi' if flag else 'bye'", {"flag": True}, "hi")
    expect_value("membership", "'yes' in output.lower()", {"output": "YES sir"}, True)
    expect_value("str method chain", "output.strip().upper()", {"output": "  ok "}, "OK")
    expect_value("split + index", "output.split(',')[1]", {"output": "a,b,c"}, "b")
    expect_value("list literal + sum", "sum([1, 2, 3])", {}, 6)
    expect_value("list comprehension", "[x * 2 for x in nums]", {"nums": [1, 2, 3]}, [2, 4, 6])
    expect_value("dict comprehension", "{k: 1 for k in keys}", {"keys": ["a"]}, {"a": 1})
    expect_value("dict literal + subscript", "{'a': 1, 'b': 2}['b']", {}, 2)
    expect_value("slice", "output[1:3]", {"output": [0, 1, 2, 3]}, [1, 2])
    expect_value("f-string", "f'{n} items'", {"n": 5}, "5 items")
    expect_value("len + sorted", "len(sorted(items))", {"items": [3, 1, 2]}, 3)
    expect_value("any/all", "all(x > 0 for x in nums)", {"nums": [1, 2, 3]}, True)
    expect_value("pow small", "2 ** 10", {}, 1024)
    expect_value("user var shadows nothing special", "score >= 0.5", {"score": 0.8}, True)


# ── Adversarial — sandbox escapes & injection ───────────────────────────────────

def test_adversarial():
    print("\n[adversarial — every vector must be BLOCKED]")
    # The canonical dunder-traversal RCE escapes.
    expect_blocked("subclasses via tuple", "().__class__.__bases__[0].__subclasses__()")
    expect_blocked("subclasses via str", '"".__class__.__mro__[1].__subclasses__()')
    expect_blocked("globals access", "(lambda: 0).__globals__", {})
    expect_blocked("__import__ builtin", "__import__('os').system('id')")
    expect_blocked("__builtins__ name", "__builtins__")
    expect_blocked("dunder attribute on var", "output.__class__", {"output": "x"})
    expect_blocked("dunder reduce", "().__reduce__()")
    expect_blocked("dunder getattribute", "output.__getattribute__('x')", {"output": "x"})

    # Leading-underscore names / attributes (severs the dunder family wholesale).
    expect_blocked("leading underscore name", "_secret", {"_secret": 1})
    expect_blocked("single underscore attr", "output._private", {"output": "x"})

    # Non-whitelisted builtin calls.
    expect_blocked("open()", "open('/etc/passwd')")
    expect_blocked("eval()", "eval('1+1')")
    expect_blocked("exec()", "exec('x=1')")
    expect_blocked("getattr()", "getattr(output, 'x')", {"output": "y"})
    expect_blocked("__import__ as call", "__import__('os')")

    # Language features outside the allowlist.
    expect_blocked("lambda", "lambda x: x")
    expect_blocked("walrus assignment", "(x := 5)")
    expect_blocked("statement (not an expression)", "x = 5")
    expect_blocked("attribute assignment via comp leak",
                   "[y for y in ().__class__.__bases__]")

    # DoS guards.
    expect_blocked("oversized source", "1+" * 1500 + "1")
    expect_blocked("huge pow exponent", "9 ** 999999")

    # Builtins are unreachable: calling one (reachable in plain eval) is blocked
    # at validation; merely *naming* an undefined builtin is a safe runtime
    # NameError (covered in the runtime-errors section) — not an escape.
    expect_blocked("globals() call", "globals()")
    expect_blocked("vars() call", "vars()")
    expect_blocked("locals() call", "locals()")


# ── Runtime errors must propagate (not be masked as unsafe) ─────────────────────

def test_runtime_errors_propagate():
    print("\n[runtime errors propagate unchanged]")
    expect_runtime_error("division by zero", "1 / 0", {}, ZeroDivisionError)
    expect_runtime_error("bad int cast", "int(output)", {"output": "abc"}, ValueError)
    expect_runtime_error("missing name", "undefined_var + 1", {}, NameError)
    expect_runtime_error("key error", "d['missing']", {"d": {}}, KeyError)


def main():
    print("=" * 70)
    print("SAFE EXPRESSION EVALUATOR — UNIT + ADVERSARIAL TESTS")
    print("=" * 70)
    test_functional()
    test_adversarial()
    test_runtime_errors_propagate()
    print("\n" + "=" * 70)
    print(f"RESULTS: {_passed} passed, {_failed} failed")
    print("=" * 70)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
