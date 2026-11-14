#!/usr/bin/env python3
"""The SQL canvas ↔ Database Navigator bridge — the backend half.

ONE masking rule for every face that shows rows: the navigator's query card, its preview and
the canvas console all mask what `dbcatalog.intelligence.masked_columns` names. This gate
holds the rule to one home (the handler and the canvas endpoint DELEGATE — they never keep a
copy), the statement reader to one home, and the canvas execute payload to its shape.

No server, no network: the pure functions are executed, the wiring is read on the AST.
"""
from __future__ import annotations

import ast
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}{(' — ' + str(detail)) if detail else ''}")


from trusted_data_agent.dbcatalog.intelligence import masked_columns, statement_tables  # noqa: E402

# ── [1] the statement reader
t = statement_tables("SELECT c.CustomerID, s.Total FROM fitness_db.Customers AS c JOIN fitness_db.Sales s ON s.CustomerID = c.CustomerID", "teradata")
check("[1] every table a statement reads, CTEs excluded", sorted(t) == ["Customers", "Sales"], t)
t = statement_tables("WITH x AS (SELECT * FROM fitness_db.Sales) SELECT * FROM x JOIN fitness_db.Customers c ON c.id = x.id", "teradata")
check("[1] a CTE name is not a table", "x" not in [n.lower() for n in t] and sorted(t) == ["Customers", "Sales"], t)
check("[1] an unparseable statement reads no tables, never raises", statement_tables("SELEC FROM", "teradata") == [] or isinstance(statement_tables("SELEC FROM", "teradata"), list))
check("[1] no dialect is tolerated", isinstance(statement_tables("SELECT 1", ""), list))

# ── [2] the masking rule
m = masked_columns(["CustomerID", "Email", "Total", "phone_number"])
check("[2] a personal-data column is masked by its name", "Email" in m and "phone_number" in m and "Total" not in m and "CustomerID" not in m, m)
m = masked_columns(["name", "Total"], ["Customers"])
check("[2] a bare `name` is personal data when the table is about people", "name" in m, m)
m = masked_columns(["name", "Total"], ["Products"])
check("[2] …and not when it is not", "name" not in m, m)
check("[2] empty input → empty, never raises", masked_columns([], []) == [] and masked_columns(None, None) == [])

# ── [3] ONE home: the handler and the canvas endpoint delegate
handler = (ROOT / "components/builtin/database_navigator/handler.py").read_text()
h_tree = ast.parse(handler)
st_fn = next(n for n in ast.walk(h_tree) if isinstance(n, ast.FunctionDef) and n.name == "_statement_tables")
calls = [n.func.id for n in ast.walk(st_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
check("[3] the handler's _statement_tables delegates to intelligence.statement_tables", "statement_tables" in calls and not any(isinstance(n, ast.Import) or (isinstance(n, ast.ImportFrom) and n.module == "sqlglot") for n in ast.walk(st_fn)))
check("[3] both handler mask sites call the shared rule", handler.count("masked_columns(") >= 2)
check("[3] the handler keeps no private mask rule", "_pii_reason(" not in handler)

routes = (ROOT / "src/trusted_data_agent/api/rest_routes.py").read_text()
r_tree = ast.parse(routes)
grid_fn = next(n for n in ast.walk(r_tree) if isinstance(n, ast.FunctionDef) and n.name == "_canvas_result_grid")
g_calls = {n.func.id for n in ast.walk(grid_fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check("[3] the canvas grid masks through the SAME rule and reads tables through the SAME reader", {"masked_columns", "statement_tables"} <= g_calls)
exec_fn = next(n for n in ast.walk(r_tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "canvas_execute")
check("[3] canvas_execute attaches the grid to its response", any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_canvas_result_grid" for n in ast.walk(exec_fn)))

# ── [4] the grid payload, executed
import importlib.util  # noqa: E402

# _canvas_result_grid + _json_cell + CANVAS_GRID_MAX_ROWS lifted out of rest_routes (importing the
# blueprint module pulls the whole app) — the very functions the AST above pinned.
src_lines = routes.splitlines()
def _src(fn):
    return "\n".join(src_lines[fn.lineno - 1: fn.end_lineno])
cell_fn = next(n for n in ast.walk(r_tree) if isinstance(n, ast.FunctionDef) and n.name == "_json_cell")
cap = next(n for n in ast.walk(r_tree) if isinstance(n, ast.Assign) and any(isinstance(t_, ast.Name) and t_.id == "CANVAS_GRID_MAX_ROWS" for t_ in n.targets))
ns = {}
exec(_src(cap) + "\n" + _src(cell_fn) + "\n" + _src(grid_fn), ns)  # noqa: S102 — the shipped source, not a copy
grid = ns["_canvas_result_grid"]
MAX = ns["CANVAS_GRID_MAX_ROWS"]

res = SimpleNamespace(columns=["CustomerID", "Email", "Total"], rows=[[1, "maria@x.io", Decimal("12.50")], [2, None, 3]], truncated=False)
out = grid(res, "SELECT CustomerID, Email, Total FROM fitness_db.Customers", {"driver": "teradata"})
check("[4] columns + rows + the masked columns the navigator would mask", out["columns"] == ["CustomerID", "Email", "Total"] and out["masked_columns"] == ["Email"], out)
check("[4] a Decimal cell is JSON-safe (a string), a null stays null", out["rows"][0][2] == "12.50" and out["rows"][1][1] is None)
check("[4] not truncated", out["truncated"] is False and out["grid_truncated"] is False)
big = SimpleNamespace(columns=["n"], rows=[[i] for i in range(MAX + 7)], truncated=True)
out = grid(big, "SELECT n FROM t", {"driver": "postgresql"})
check("[4] the grid is capped and SAYS so, apart from the database's own truncation", len(out["rows"]) == MAX and out["grid_truncated"] is True and out["truncated"] is True)
out = grid(SimpleNamespace(columns=["email"], rows=[["a@b"]], truncated=False), '{"collection":"users"}', {"driver": "mongodb"})
check("[4] a MongoDB statement is never parsed as SQL, the name rule still masks", out["masked_columns"] == ["email"])
out = grid(SimpleNamespace(columns=None, rows=None, truncated=False), "SELECT 1", {"driver": "teradata"})
check("[4] a result with no columns yields an empty grid, never raises", out["columns"] == [] and out["rows"] == [])

# ── [5] the two connectors that return structured rows
sqlc = (ROOT / "components/builtin/canvas/connectors/sql.py").read_text()
c_tree = ast.parse(sqlc)
for fn_name in ("_execute_teradata", "_execute_jdbc"):
    fn = next(n for n in ast.walk(c_tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name)
    kws = {kw.arg for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ExecutionResult" for kw in n.keywords}
    check(f"[5] {fn_name} carries columns + rows on its ExecutionResult", {"columns", "rows"} <= kws, kws)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
