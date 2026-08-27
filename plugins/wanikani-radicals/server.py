"""Demo MCP plugin: WaniKani-style radical meanings.

Illustrates the plugin contract (spec §5.1):
- standalone stdio MCP server, discovered via its manifest.json
- provides_ground_truth: false — it may NEVER shadow a dictionary/parser
  tool name; the registry rejects any such attempt at mount time
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("wanikani-radicals")

MEANINGS = {
    "人": "person",
    "日": "sun / day",
    "月": "moon / month",
    "水": "water",
    "火": "fire",
    "木": "tree / wood",
    "金": "gold / metal",
    "土": "dirt / ground",
    "口": "mouth",
    "田": "rice field",
}


@mcp.tool()
def wk_radical_meaning(radical: str) -> str:
    """WaniKani-style mnemonic meaning for a radical (demo data)."""
    return MEANINGS.get(radical, f"(no WaniKani meaning on file for {radical!r})")


if __name__ == "__main__":
    mcp.run()
