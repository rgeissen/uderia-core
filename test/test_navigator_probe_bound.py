"""An empty object ends the navigator's argument-name probe — it never walks the grid.

FOUND LIVE (trust campaign C12, 2026-10-28, Ideate + tools · B4): MCP endpoint 8888 upgraded to
FastMCP 4. Measured on both servers with the same call: an object with no visible columns
answers the DECLARED names (`database_name`, `table_name`) with `status: success, results: []`.
The navigator's probe (`_probe_two_args` / `_call_first_nonempty`) read "no rows" as "wrong
argument names" and tried the other 55 combinations of its grid — names the tool does not
declare. FastMCP 3 answered each with an error RESULT in ~0.05 s, so the waste was invisible;
FastMCP 4 RAISES a protocol error at ~0.9 s each: 515 refusals in half an hour, ~50 s per
empty object, a navigator call past a turn's 300 s cap.

The rule: when the preferred names are the ones the tool's schema DECLARES (the navigator's
roles) and the server accepts them, the result — empty or not — is the answer. The probe
still runs when those names are refused, and for callers whose preferred name is a guess
(`trust_preferred` off: the KG constructors' LLM-resolved names). CI-pure.
"""
import asyncio
import os
import sys

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


from trusted_data_agent.agent.kg_constructors import database_context as DC  # noqa: E402

DECLARED = {"database_name", "table_name"}


def server(tables):
    """A FastMCP-4-like tool: unknown argument names are REFUSED (raised), declared ones run."""
    calls = []

    async def invoke(tool, args):
        calls.append(dict(args))
        if set(args) - DECLARED:
            raise RuntimeError("Internal error: Unexpected keyword argument")
        cols = tables.get((args.get("database_name"), args.get("table_name")), [])
        return {"status": "success", "results": [{"ColumnName": c, "CType": "VARCHAR"} for c in cols]}
    return invoke, calls


def run(coro):
    return asyncio.run(coro)


print("\n── §1 an EMPTY object answered under the declared names (C12)")
inv, calls = server({})
rows, da, ta = run(DC._probe_two_args(inv, "base_columnDescription", "DBC", "SomeView",
                                       "database_name", "table_name", DC._DB_ARG_CANDIDATES,
                                       DC._TABLE_ARG_CANDIDATES, DC.COLUMN_NAME_FIELDS, trust_preferred=True))
check("one call, not the grid", len(calls) == 1, len(calls))
check("…returns the empty answer under the declared names", (rows, da, ta) == ([], "database_name", "table_name"))
check("no name the tool does not declare was sent", all(set(c) <= DECLARED for c in calls))

print("\n── §2 the same without the trust (a caller whose preferred name is a guess)")
inv, calls = server({})
run(DC._probe_two_args(inv, "base_columnDescription", "DBC", "SomeView", "database_name", "table_name",
                       DC._DB_ARG_CANDIDATES, DC._TABLE_ARG_CANDIDATES, DC.COLUMN_NAME_FIELDS))
check("…walks the grid as before (behaviour unchanged for that caller)",
      len(calls) == len(DC._DB_ARG_CANDIDATES) * len(DC._TABLE_ARG_CANDIDATES), len(calls))

print("\n── §3 rows under the declared names: found on the first call, as before")
inv, calls = server({("fitness_db", "Customers"): ["CustomerID", "LastName"]})
rows, da, ta = run(DC._probe_two_args(inv, "base_columnDescription", "fitness_db", "Customers",
                                       "database_name", "table_name", DC._DB_ARG_CANDIDATES,
                                       DC._TABLE_ARG_CANDIDATES, DC.COLUMN_NAME_FIELDS, trust_preferred=True))
check("two columns, one call", len(rows) == 2 and len(calls) == 1)

print("\n── §4 a WRONG preferred name is still corrected by the probe")
inv, calls = server({("fitness_db", "Customers"): ["CustomerID"]})
rows, da, ta = run(DC._probe_two_args(inv, "base_columnDescription", "fitness_db", "Customers",
                                       "schema", "obj_name", DC._DB_ARG_CANDIDATES,
                                       DC._TABLE_ARG_CANDIDATES, DC.COLUMN_NAME_FIELDS, trust_preferred=True))
check("a refused preferred pair falls through to the probe, which finds the declared names",
      (da, ta) == ("database_name", "table_name") and len(rows) == 1, (da, ta))

print("\n── §5 the one-argument probe (listing a database's tables)")
inv, calls = server({})
rows, used = run(DC._call_first_nonempty(inv, "base_tableList", "empty_db", "database_name",
                                          DC._DB_ARG_CANDIDATES, DC.TABLE_NAME_FIELDS, trust_preferred=True))
check("an empty database under the declared name: one call", len(calls) == 1 and (rows, used) == ([], "database_name"),
      len(calls))
inv, calls = server({})
run(DC._call_first_nonempty(inv, "base_tableList", "empty_db", "database_name",
                            DC._DB_ARG_CANDIDATES, DC.TABLE_NAME_FIELDS))
check("…without the trust, every candidate as before", len(calls) >= len(DC._DB_ARG_CANDIDATES))

print("\n── §6 an error RESULT is not acceptance")
async def _err(tool, args):
    return {"status": "error", "error": "Internal error"}
rows, used = run(DC._call_first_nonempty(_err, "t", "db", "database_name", ["database_name", "db"],
                                          DC.TABLE_NAME_FIELDS, trust_preferred=True))
check("an error result under the declared name keeps probing (and finds nothing)", (rows, used) == ([], None))

print("\n── §7 the navigator's MCP provider uses the trust for its schema-declared roles")
import inspect  # noqa: E402
from trusted_data_agent.dbcatalog import mcp_provider as MP  # noqa: E402
src = inspect.getsource(MP)
check("describe_object and list_objects pass trust_preferred=True", src.count("trust_preferred=True") == 2)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
