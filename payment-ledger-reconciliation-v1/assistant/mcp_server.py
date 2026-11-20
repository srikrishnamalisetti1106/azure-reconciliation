"""
MCP server that exposes the read-only tools to any MCP client
(the LangGraph agent, Claude Desktop, or the MCP Inspector).

Run on its own to check it starts:   python assistant/mcp_server.py
(The agent starts it automatically; you don't need to keep it running.)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from assistant.rag import search_playbooks  # noqa: E402

# Same four tools, two backends:
#   duckdb      local read-only copy (development, CI, docker compose)
#   databricks  Gold/reporting tables through a Databricks SQL warehouse (production)
if os.getenv("RECON_BACKEND", "duckdb") == "databricks":
    from assistant.tools_databricks import ALL_TOOLS  # noqa: E402
else:
    from assistant.tools import ALL_TOOLS  # noqa: E402

mcp = FastMCP("reconciliation")

for fn in [*ALL_TOOLS, search_playbooks]:
    mcp.tool()(fn)

if __name__ == "__main__":
    mcp.run()  # talks over stdin/stdout ("stdio" transport)
