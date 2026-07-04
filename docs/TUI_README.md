# MCP TUI Extraction Note

The TUI client and deterministic MCP runner have moved to `/Users/julian/Code/nfl-mcp-tui`.

Use that repo for operator UI usage:

```bash
cd /Users/julian/Code/nfl-mcp-tui
./run_mcp_chat.sh
./run_mcp_chat_mouse.sh
./run_mcp_tools.sh doctor
```

Or use the installed commands from the extracted package:

```bash
nfl-mcp-tui --config mcp_chat_config.json
nfl-mcp-run --config mcp_chat_config.json doctor
```

`nfl-comfymcp` remains the owner of the ComfyMCP server, workspace server, workflow store, and workflow MCP tools.
