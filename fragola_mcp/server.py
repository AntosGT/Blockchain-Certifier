from mcp.server.fastmcp import FastMCP

from fragola_mcp.prima.tools import register as register_prima

# A single MCP server for all Fragola tools. Each product adds its own, with its own prefix.
mcp = FastMCP("fragola")
register_prima(mcp)

def main() -> None:
    mcp.run()

if __name__ == "__main__":
    main()
