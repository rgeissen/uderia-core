"""CLI entry for the Uderia MCP server.

    # STDIO (the model an MCP client uses to spawn the server as a subprocess):
    UDERIA_ACCESS_TOKEN=<token> python -m trusted_data_agent.mcp_server --profile user

    # Streamable-HTTP (serve at http://<host>:<port>/mcp):
    UDERIA_ACCESS_TOKEN=<token> python -m trusted_data_agent.mcp_server --transport http --port 5055

``--profile`` (or ``UDERIA_MCP_PROFILE``) ∈ {user, developer, admin, all} selects the consumer
tool surface. It is a HARD GATE: the surface is clamped to ``min(profile, the access token's actual
user tier)`` (resolved from /auth/me at startup, failing safe to ``user``), so a user-tier token can
never expose developer/admin tools. ``UDERIA_BASE_URL`` (default http://localhost:5050) targets the
instance. In STDIO mode all logging goes to stderr — stdout is the MCP wire protocol.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m trusted_data_agent.mcp_server",
        description="Uderia MCP server — expose Uderia profiles + reads as tier-segmented MCP tools.")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="MCP transport (default: stdio).")
    parser.add_argument("--profile", choices=["user", "developer", "admin", "all"],
                        default=os.environ.get("UDERIA_MCP_PROFILE", "all"),
                        help="Consumer tool surface; clamped to the token's actual tier (default: all).")
    parser.add_argument("--host", default="127.0.0.1", help="Host for --transport http.")
    parser.add_argument("--port", type=int, default=5055, help="Port for --transport http.")
    args = parser.parse_args()

    # stdout is reserved for the MCP protocol in stdio mode → log to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not os.environ.get("UDERIA_ACCESS_TOKEN"):
        print("WARNING: UDERIA_ACCESS_TOKEN is not set — tool calls will fail until it is "
              "(Anatomy → Advanced → Access Tokens in Uderia).", file=sys.stderr)
    base = os.environ.get("UDERIA_BASE_URL", "http://localhost:5050")

    # Imported here so --help works even if fastmcp/env is not ready.
    from trusted_data_agent.mcp_server import server

    # HARD GATE: resolve the token's actual tier and clamp the requested profile to it before
    # any tool is served. Fails safe to 'user' (least privilege) if the tier can't be resolved.
    token_rank = asyncio.run(server.resolve_token_tier_rank())
    registered = server.register_tools(args.profile, max_rank=token_rank)
    eff = [t for t, r in server.TIER_RANK.items() if r == token_rank]
    print(f"Uderia MCP server → {base} | profile={args.profile} "
          f"clamped to token tier '{(eff or ['user'])[0]}' | tools: {registered} "
          f"(transport={args.transport})", file=sys.stderr)

    if args.transport == "http":
        server.mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        server.mcp.run("stdio")


if __name__ == "__main__":
    main()
