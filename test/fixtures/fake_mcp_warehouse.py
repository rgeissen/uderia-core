"""A tiny stdio MCP server standing in for one data warehouse (PROP-006 phase-2 tests).

Started twice by ``test_multi_mcp_runtime.py`` with different ``--name`` / ``--db`` so the two
servers expose OVERLAPPING tool names (``base_readQuery``, ``base_tableList``,
``base_databaseList``) plus one tool of their own, and every answer names the server that
produced it — a call that reaches the wrong server is visible in the result.

    python test/fixtures/fake_mcp_warehouse.py --name A --db sales
"""
import argparse
import json

from mcp.server.fastmcp import FastMCP

ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
ap.add_argument("--db", required=True)
ap.add_argument("--extra", default="")
a = ap.parse_args()

mcp = FastMCP(f"warehouse-{a.name}", log_level="ERROR")


@mcp.tool()
def base_readQuery(sql: str) -> str:
    """Runs a SQL query on this warehouse."""
    return json.dumps({"status": "success", "server": a.name,
                       "results": [{"server": a.name, "db": a.db, "sql": sql}]})


@mcp.tool()
def base_tableList(database_name: str) -> str:
    """Lists the tables of a database."""
    return json.dumps({"status": "success", "server": a.name,
                       "results": [{"server": a.name, "TableName": f"{database_name}_orders"}]})


@mcp.tool()
def base_databaseList(scope: str = "all") -> str:
    """Lists the databases on this warehouse."""
    return json.dumps({"status": "success", "server": a.name, "results": [{"DatabaseName": a.db}]})


if a.extra:
    def _extra(question: str) -> str:
        return json.dumps({"status": "success", "server": a.name, "results": [{"answer": question}]})
    _extra.__name__ = a.extra
    _extra.__doc__ = f"A tool only warehouse {a.name} has."
    mcp.tool(name=a.extra)(_extra)


@mcp.prompt()
def base_query(database_name: str) -> str:
    """Answer a question about a database."""
    return f"Answer about {database_name} on {a.name}"


if __name__ == "__main__":
    mcp.run("stdio")
