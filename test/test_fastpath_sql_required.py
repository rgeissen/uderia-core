"""OC-F17 — a SQL runner is never FASTPATHed without a statement.

FOUND LIVE (Optimize campaign `20261029-073843`, `@OCFL-NORAG` R3 on MiMo): the plan's phase 4
was `base_readQuery` with `arguments: {}` (the SQL left to be written). On MCP endpoint 8888
(FastMCP 4) the tool's schema declares `sql` OPTIONAL (`anyOf [string, null], default null`), so
FASTPATH's required-arguments check passed on an empty dict; the tool ran without a statement,
the server answered "No SQL provided" with `status: success`, the phase completed, and the
report said the revenue query had not run — a wrong answer at the lane's cheapest price.

The rule (`phase_executor._takes_sql`): a tool whose schema has a `sql` argument goes to the
tactical step when the plan carries no statement, whatever the server declares `required`.
CI-pure.
"""
import inspect
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}{(' — ' + str(detail)) if detail else ''}")


from trusted_data_agent.agent import phase_executor as PE  # noqa: E402

# the 8888 shape, as the platform holds it (args: name → details; `sql` NOT required)
READ_Q = SimpleNamespace(args={"sql": {"type": "string", "required": False},
                               "row_limit": {"type": "integer", "required": False},
                               "persist": {"type": "boolean", "required": False}})
COL_D = SimpleNamespace(args={"database_name": {"required": False}, "table_name": {"required": False}})

print("\n── §1 which tools are statement runners")
check("base_readQuery on FastMCP 4 (sql optional) is one", PE._takes_sql(READ_Q))
check("a describe tool is not", not PE._takes_sql(COL_D))
check("a tool with no schema is not (no change for it)", not PE._takes_sql(None) and not PE._takes_sql(SimpleNamespace()))

print("\n── §2 the FASTPATH decision rejects a runner with no statement")
src = inspect.getsource(PE)
i = src.find("FASTPATH REJECTED: '{tool_name}' takes a SQL statement and the plan carries none.")
check("the rejection exists", i > 0)
blk = src[max(0, i - 700):i]
check("…it reads the tool's schema and the plan's own `sql`",
      "_takes_sql(tool_def if tool_name else None)" in blk and '(strategic_args or {}).get("sql")' in blk)
j_fast = src.find('"summary": f"FASTPATH initiated for \'{tool_name}\'."')
check("…and runs before FASTPATH is announced", 0 < i < j_fast, (i, j_fast))

print("\n── §3 the loop's own FASTPATH obeys the same rule (R5: six empty calls, one per column)")
k = src.find("FASTPATH REJECTED for loop: '{relevant_tools[0]}' takes a SQL statement and the plan carries none.")
k_loop = src.find("FASTPATH enabled for tool loop")
check("the loop rejection exists and runs before the loop FASTPATH is announced", 0 < k < k_loop, (k, k_loop))
check("…it reads the same rule", "_takes_sql(" in src[max(0, k - 600):k])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
