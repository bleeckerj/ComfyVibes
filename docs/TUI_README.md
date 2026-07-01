# TUI README (`run_mcp_chat.sh` / `run_mcp_chat_mouse.sh`)

This document covers launching the Comfy MCP TUI and configuring MCP server ports.

## Launchers

From `nfl-comfymcp/`:

```bash
./run_mcp_chat.sh
./run_mcp_chat_mouse.sh
```

- `run_mcp_chat.sh`: copy-friendly mode
- `run_mcp_chat_mouse.sh`: mouse-capture mode (`--mouse`)

Both launchers:

1. Read `mcp_chat_config.json` and normalize each server transport
2. Ensure only HTTP-configured MCPs are reachable (starts them if needed)
3. Leave stdio-configured MCPs for EDGAR to spawn on connect
4. Launch the TUI client

The checked-in default transport mix is:

- `stdio`: Comfy, Photarium, Editorial, Backoffice, Digester
- `http`: Workspace

If you switch a server to `transport: "http"`, the launcher will prestart its daemon. If you switch a server to `transport: "stdio"`, EDGAR will spawn it directly when it connects.

`run_mcp_chat.sh` actions:

- `./run_mcp_chat.sh` or `./run_mcp_chat.sh start`: ensure servers + launch TUI
- `./run_mcp_chat.sh restart`: restart servers, then launch TUI
- `./run_mcp_chat.sh start-only`: ensure servers only (no TUI)
- `./run_mcp_chat.sh stop` / `status`: server control only
- `./run_mcp_chat.sh servers-stop|servers-restart|servers-status`: explicit server-only aliases

## In-chat commands

- `/help` shows available local commands.
- `/status` shows current session status (model, readiness, tool count, server endpoints, context size).
- `/reset` clears current TUI chat/tool logs and resets LLM conversation context to only the system prompt.

## Workflow-from-image usage in TUI

There is no dedicated slash command for `workflows_run_from_source`. Use plain chat requests and let EDGAR route to the tool.

Examples:

- `Run the workflow embedded in Photarium image 75e92a7e-2838-45a7-6f2c-32a5fde6c300.`
- `Run the workflow from /tmp/ComfyUI_01065.png and keep the prompt but set denoise to 0.65.`
- `Run the workflow from https://example.com/comfy-output.png.`
- `Rerun the workflow that made image b287f5ef-2901-4e27-f6b4-b483fc4a7e00.`

If the tool replies that it needs more inputs, respond with the missing bindings directly:

- `Use /tmp/input_a.png for image and /tmp/input_b.png for image_2.`

Related local slash commands:

- `/importwf <image_id>` imports an embedded Photarium workflow into the curated workflow catalog.
- `/imageedit <image_id> <request>` runs the dedicated image-edit flow.
- `/vary <image_id>` runs the variation flow.
- `/aspect <image_id> targets=...` runs the aspect-ratio flow.

See also:

- [Workflow Run From Source / Lineage Cache](workflows_run_from_source.README.md)

## Multiline composer

- The bottom prompt is multiline.
- Press `Enter` to insert a new line.
- Press `Ctrl+Enter` to send the message.

## Default HTTP ports

- Workspace MCP HTTP: `8777`

Only HTTP-configured servers use these preflight port checks.

## Configure ports for launcher preflight

Launchers now read these environment variables for port checks/startup of HTTP-configured servers:

- `WORKSPACE_HTTP_PORT` (default `8777`)

Example:

```bash
WORKSPACE_HTTP_PORT=8791 \
./run_mcp_chat.sh
```

Optional related vars:

- `COMFY_MCP_HTTP_BIND_HOST` (used by Comfy MCP helper)
- `COMFY_MCP_COMFY_ORG_AUTH_TOKEN_FILE` / `COMFY_MCP_COMFY_ORG_API_KEY_FILE` (server-side Comfy Org auth for workflow API nodes)
- `PHOTARIUM_HTTP_HOST` (used by Photarium MCP helper)
- `PHOTARIUM_ROOT` (path to `cloud-flare-image-handler`)
- `EDITORIAL_ROOT` (path to `nfl-editorial`)
- `BACKOFFICE_ROOT` (path to `nfl-backoffice`)

## Configure transports in TUI config

The TUI client reads both HTTP endpoints and stdio commands from `mcp_chat_config.json`.

You can mix HTTP and stdio MCP servers in the same config. Example stdio entry:

```json
{
  "name": "editorial",
  "transport": "stdio",
  "command": "/Users/julian/Code/nfl-editorial/run_editorial_mcp_server.sh",
  "cwd": "/Users/julian/Code/nfl-editorial",
  "env": {
    "EDITORIAL_HTTP_ENABLED": "false"
  }
}
```

Example HTTP entry:

```json
{
  "name": "workspace",
  "transport": "http",
  "http_url": "http://127.0.0.1:8791"
}
```

Example Photarium stdio entry:

```json
{
  "name": "photarium",
  "transport": "stdio",
  "command": "/Users/julian/Code/cloud-flare-image-handler/run_photarium_mcp_server.sh",
  "cwd": "/Users/julian/Code/cloud-flare-image-handler",
  "env": {
    "PHOTARIUM_HTTP_ENABLED": "false",
    "PHOTARIUM_BASE_URL": "http://127.0.0.1:3000"
  }
}
```

## Typical custom-port workflow

```bash
# 1) Update HTTP-configured server URLs in mcp_chat_config.json
#    and keep stdio-configured servers on command/cwd/env entries.

# 2) Start TUI with matching HTTP port env vars
WORKSPACE_HTTP_PORT=8791 \
./run_mcp_chat.sh
```

## Doctor mode (`run_mcp_chat.sh`)

- `MCP_CHAT_DOCTOR_MODE=warn` (default): warn and continue
- `MCP_CHAT_DOCTOR_MODE=strict`: fail on doctor issues
- `MCP_CHAT_DOCTOR_MODE=off`: skip doctor

Example:

```bash
MCP_CHAT_DOCTOR_MODE=strict ./run_mcp_chat.sh
```

## Troubleshooting

Check listeners:

```bash
./run_mcp_processes.sh status
```

The launcher status output will report stdio-configured servers as EDGAR-managed instead of expecting an HTTP listener.

Stop background servers:

```bash
./run_mcp_chat.sh stop
```

Stop servers automatically when you exit the TUI:

```bash
MCP_CHAT_STOP_SERVERS_ON_EXIT=1 ./run_mcp_chat.sh
```

Reset both servers:

```bash
./run_mcp_processes.sh stop
./run_mcp_processes.sh start
```
