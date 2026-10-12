from mcp.server.fastmcp import FastMCP

from fragola_mcp.prima.tools import register as register_prima

# Un solo servidor MCP para todas las herramientas de Fragola. Cada producto suma las suyas con su propio prefijo.
mcp = FastMCP("fragola")
register_prima(mcp)

def main() -> None:
    mcp.run()

if __name__ == "__main__":
    main()
