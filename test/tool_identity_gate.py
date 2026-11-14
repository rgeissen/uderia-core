"""The build gate for PROP-006 decision 2: every rule that recognises an MCP tool by name reads
the BASE name through ``core.tool_identity.base_name``.

With several MCP servers, an additional server's tools are named ``<alias>__<name>``. A rule that
compares the raw name — ``tool_name == "base_readQuery"``, ``name in SQL_TOOLS``,
``name.startswith("base_")`` — would quietly skip the SQL checks for ``crm__base_readQuery``. This
scans the Python sources with the AST and reports every such comparison whose tool side is not a
``base_name(...)`` call (or a local variable assigned from one).

What counts as a tool literal: a string in the MCP tool naming style — a known server prefix, an
underscore, then a camelCase word (``base_readQuery``, ``qlty_columnSummary``) — or a bare prefix
used with ``startswith`` (``"base_"``). A collection counts when it holds such literals, whether
written inline or bound to a module-level name (resolved across the scanned files by name).

``violations()`` → list of ``(path, line, snippet)``; ``test_tool_identity.py`` asserts it is empty.
"""

import ast
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCAN_DIRS = ("src", "components")
TOOL_LITERAL = re.compile(r"^(base|qlty|dba|sec|tmpl|plot|evs|fs|rag|chat)_[a-z]+[A-Z][A-Za-z0-9]*$")
TOOL_PREFIX = re.compile(r"^(base|qlty|dba|sec|tmpl|plot|evs|fs)_$")
PARSER_FUNCS = {"base_name"}
# files whose literal comparisons are not tool recognition (the parser module itself; tests)
EXEMPT = {os.path.join("src", "trusted_data_agent", "core", "tool_identity.py")}


def _files():
    for d in SCAN_DIRS:
        for dp, _dn, fns in os.walk(os.path.join(ROOT, d)):
            if "__pycache__" in dp:
                continue
            for fn in fns:
                if fn.endswith(".py"):
                    full = os.path.join(dp, fn)
                    rel = os.path.relpath(full, ROOT)
                    if rel not in EXEMPT:
                        yield rel, full


def _is_tool_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and bool(TOOL_LITERAL.match(node.value))


def _collection_has_tools(node, named):
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return any(_is_tool_str(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return any(k is not None and _is_tool_str(k) for k in node.keys)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("frozenset", "set", "tuple", "list", "dict") and node.args:
        return _collection_has_tools(node.args[0], named)
    if isinstance(node, ast.Name):
        return node.id in named
    if isinstance(node, ast.Attribute):
        return node.attr in named
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "getattr" and len(node.args) >= 2 \
                and isinstance(node.args[1], ast.Constant) and node.args[1].value in named:
            return True
        fname = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
        if fname and ("()" + fname) in named:          # a function that returns a tool collection
            return True
    if isinstance(node, ast.BinOp):
        return _collection_has_tools(node.left, named) or _collection_has_tools(node.right, named)
    return False


class _Elem:
    """A binding to one ELEMENT of an iterable (a loop or comprehension target)."""
    def __init__(self, it):
        self.it = it


def _stmt_bindings(stmts, into_functions=False):
    """(name, value) bindings made by these statements: assignments, import aliases, loop and
    comprehension targets (as ``_Elem``), and — for a function — what it returns, under
    ``"()" + name``. Nested function bodies are skipped (they are their own scope)."""
    stack = list(stmts)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for r in ast.walk(node):
                if isinstance(r, ast.Return) and r.value is not None:
                    yield "()" + node.name, r.value
                    if isinstance(r.value, ast.Call) and isinstance(r.value.func, ast.Name) and r.value.args:
                        yield "()" + node.name, r.value.args[0]
            # the local that is returned: follow one level
            locs = dict((t.id, n.value) for n in ast.walk(node) if isinstance(n, ast.Assign)
                        for t in n.targets if isinstance(t, ast.Name))
            for r in ast.walk(node):
                if isinstance(r, ast.Return) and r.value is not None:
                    inner = r.value.args[0] if (isinstance(r.value, ast.Call) and r.value.args) else r.value
                    if isinstance(inner, ast.Name) and inner.id in locs:
                        yield "()" + node.name, locs[inner.id]
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.ClassDef):
            stack.extend(node.body)
            continue
        if isinstance(node, ast.ImportFrom):
            for al in node.names:
                if al.asname:
                    yield al.asname, ast.Name(id=al.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id, node.value
                elif isinstance(t, ast.Attribute):
                    yield t.attr, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            t = node.target
            if isinstance(t, ast.Name):
                yield t.id, node.value
            elif isinstance(t, ast.Attribute):
                yield t.attr, node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Name):
            yield node.target.id, _Elem(node.iter)
        if isinstance(node, ast.stmt):
            for sub in ast.walk(node):
                if isinstance(sub, ast.comprehension) and isinstance(sub.target, ast.Name):
                    yield sub.target.id, _Elem(sub.iter)
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                stack.append(child)


def _resolve(bindings, named, strings):
    """Grow (named, strings) from these bindings until nothing changes."""
    named, strings = set(named), set(strings)
    changed = True
    while changed:
        changed = False
        for name, value in bindings:
            if isinstance(value, _Elem):
                if name not in strings and _collection_has_tools(value.it, named):
                    strings.add(name)
                    changed = True
                continue
            if isinstance(value, ast.Name) and value.id in strings and name not in strings:
                strings.add(name)
                changed = True
            if name not in strings and _is_tool_str(value):
                strings.add(name)
                changed = True
            if name not in named and _collection_has_tools(value, named):
                named.add(name)
                changed = True
    return named, strings


def _tool_names(trees):
    """Module- and class-level names bound to a collection of tool literals or to one tool
    literal — resolved across the scanned files (they are imported by name)."""
    b = [x for tree in trees.values() for x in _stmt_bindings(tree.body)]
    return _resolve(b, set(), set())


def _is_parser_call(node):
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else None)
    return name in PARSER_FUNCS


def _is_parsed(node, parsed_locals):
    """A base name, or a collection OF base names (a comprehension over base_name(...), or a
    set/list/tuple/frozenset call over one)."""
    if _is_parser_call(node):
        return True
    if isinstance(node, ast.Name):
        return node.id in parsed_locals
    if isinstance(node, (ast.SetComp, ast.ListComp, ast.GeneratorExp)):
        return _is_parser_call(node.elt)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("set", "frozenset", "list", "tuple", "sorted") and node.args:
        return _is_parsed(node.args[0], parsed_locals)
    return False


def _parsed_locals(func):
    out = set()
    changed = True
    while changed:
        changed = False
        for n in ast.walk(func):
            if isinstance(n, ast.Assign) and _is_parsed(n.value, out):
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id not in out:
                        out.add(t.id)
                        changed = True
    return out


_SET_METHODS = {"intersection", "isdisjoint", "issubset", "issuperset", "difference",
                "symmetric_difference", "union"}


def violations(files=None):
    """``files``: ``[(label, path), ...]`` to scan instead of the source tree (the gate's self-test)."""
    trees = {}
    sources = {}
    for rel, full in (files if files is not None else _files()):
        try:
            src = open(full, encoding="utf-8").read()
            trees[rel] = ast.parse(src)
            sources[rel] = src.splitlines()
        except (SyntaxError, UnicodeDecodeError):
            continue
    g_named, g_strings = _tool_names(trees)

    found = []
    for rel, tree in trees.items():
        # per node: the names that are tool collections / tool strings in ITS scope (the module and
        # class level of every file, plus the bindings of its enclosing functions), and the locals
        # that hold base names — one recursive walk
        scope_of = {}

        def walk(node, scope):
            scope_of[id(node)] = scope
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    named, strings, pl = scope
                    n2, s2 = _resolve(list(_stmt_bindings(child.body)), named, strings)
                    walk(child, (n2, s2, pl | _parsed_locals(child)))
                else:
                    walk(child, scope)

        walk(tree, (g_named, g_strings, set()))

        def hit(node):
            found.append((rel, node.lineno, sources[rel][node.lineno - 1].strip()))

        for node in ast.walk(tree):
            named, strings, pl = scope_of[id(node)]

            def tool_str(n):
                return _is_tool_str(n) or (isinstance(n, ast.Name) and n.id in strings) \
                    or (isinstance(n, ast.Attribute) and n.attr in strings)

            def tool_coll(n):
                return _collection_has_tools(n, named)

            if isinstance(node, ast.Compare):
                operands = [node.left] + list(node.comparators)
                for i, op in enumerate(node.ops):
                    left, right = operands[i], operands[i + 1]
                    if isinstance(op, (ast.Eq, ast.NotEq)):
                        for a, b in ((left, right), (right, left)):
                            if tool_str(a) and not tool_str(b) and not _is_parsed(b, pl) \
                                    and not isinstance(b, ast.Constant):
                                hit(node)
                    elif isinstance(op, (ast.In, ast.NotIn)):
                        if tool_coll(right) and not _is_parsed(left, pl) and not isinstance(left, ast.Constant):
                            hit(node)                       # name in SQL_TOOLS
                        elif tool_str(left) and not tool_coll(right) and not _is_parsed(right, pl) \
                                and not isinstance(right, ast.Constant):
                            hit(node)                       # "base_readQuery" in tools
            elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitAnd, ast.Sub, ast.BitOr, ast.BitXor)):
                for a, b in ((node.left, node.right), (node.right, node.left)):
                    if tool_coll(a) and not tool_coll(b) and not _is_parsed(b, pl):
                        hit(node)
            elif isinstance(node, ast.Subscript) and tool_coll(node.value) \
                    and not isinstance(node.slice, ast.Constant) and not _is_parsed(node.slice, pl) \
                    and not isinstance(node.ctx, ast.Store):
                hit(node)                                   # TOOL_TABLE[name]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                recv = node.func.value
                if attr in ("get", "pop", "setdefault") and node.args and tool_coll(recv) \
                        and not isinstance(node.args[0], ast.Constant) and not _is_parsed(node.args[0], pl):
                    hit(node)                               # TOOL_TABLE.get(name)
                if attr in ("startswith", "endswith") and node.args:
                    a = node.args[0]
                    pref = [a] if isinstance(a, ast.Constant) else (list(a.elts) if isinstance(a, ast.Tuple) else [])
                    if any(isinstance(p, ast.Constant) and isinstance(p.value, str) and TOOL_PREFIX.match(p.value)
                           for p in pref) and not _is_parsed(recv, pl):
                        hit(node)
                elif attr in _SET_METHODS and node.args:
                    other = node.args[0]
                    if (tool_coll(recv) and not tool_coll(other) and not _is_parsed(other, pl)) or \
                            (tool_coll(other) and not tool_coll(recv) and not _is_parsed(recv, pl)):
                        hit(node)
    return sorted(set(found))


if __name__ == "__main__":
    v = violations()
    for rel, line, snip in v:
        print(f"{rel}:{line}: {snip[:140]}")
    print(f"{len(v)} violation(s)")
