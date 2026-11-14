"""A minimal MCP server over streamable HTTP, for the WS2 connector tests.

Run standalone (the test launches it as a subprocess):
    python test/mcp_http_test_server.py <port>

Serves at http://127.0.0.1:<port>/mcp with two tools: echo, add.
"""
import sys
import json

from mcp.server.fastmcp import FastMCP

_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

mcp = FastMCP("uderia-test-http", host="127.0.0.1", port=_PORT)


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back as a JSON object."""
    return json.dumps({"echoed": message, "ok": True})


@mcp.tool()
def add(a: int, b: int) -> str:
    """Add two integers, returning JSON."""
    return json.dumps({"sum": a + b})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
