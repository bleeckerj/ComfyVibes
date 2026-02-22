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

1. Ensure Comfy MCP HTTP is reachable (starts it if needed)
2. Ensure Photarium MCP HTTP is reachable (starts it if needed)
3. Ensure Editorial MCP HTTP is reachable (starts it if needed)
4. Ensure Backoffice MCP HTTP is reachable (starts it if needed)
5. Launch the TUI client

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

## Multiline composer

- The bottom prompt is multiline.
- Press `Enter` to insert a new line.
- Press `Ctrl+Enter` to send the message.

## Default ports

- Comfy MCP HTTP: `8001`
- Photarium MCP HTTP: `8787`
- Editorial MCP HTTP: `8788`
- Backoffice MCP HTTP: `8766`

## Configure ports for launcher preflight

Launchers now read these environment variables for port checks/startup:

- `COMFY_MCP_HTTP_BIND_PORT` (default `8001`)
- `PHOTARIUM_HTTP_PORT` (default `8787`)
- `EDITORIAL_HTTP_PORT` (default `8788`)
- `BACKOFFICE_HTTP_PORT` (default `8766`)

Example:

```bash
COMFY_MCP_HTTP_BIND_PORT=8101 \
PHOTARIUM_HTTP_PORT=8790 \
EDITORIAL_HTTP_PORT=8791 \
BACKOFFICE_HTTP_PORT=8792 \
./run_mcp_chat.sh
```

Optional related vars:

- `COMFY_MCP_HTTP_BIND_HOST` (used by Comfy MCP helper)
- `PHOTARIUM_HTTP_HOST` (used by Photarium MCP helper)
- `PHOTARIUM_ROOT` (path to `cloud-flare-image-handler`)
- `EDITORIAL_ROOT` (path to `nfl-editorial`)
- `BACKOFFICE_ROOT` (path to `nfl-backoffice`)

## Configure ports in TUI config

The TUI tool client reads MCP endpoints from `mcp_chat_config.json`.
You must keep these URLs aligned with your chosen ports.

Edit `servers[].http_url`:

```json
{
  "servers": [
    {
      "name": "comfy",
      "transport": "http",
      "http_url": "http://127.0.0.1:8101"
    },
    {
      "name": "photarium",
      "transport": "http",
      "http_url": "http://127.0.0.1:8790"
    },
    {
      "name": "editorial",
      "transport": "http",
      "http_url": "http://127.0.0.1:8791"
    },
    {
      "name": "backoffice",
      "transport": "http",
      "http_url": "http://127.0.0.1:8792"
    }
  ]
}
```

## Typical custom-port workflow

```bash
# 1) Start MCP servers on custom ports
COMFY_MCP_HTTP_BIND_PORT=8101 \
PHOTARIUM_HTTP_PORT=8790 \
EDITORIAL_HTTP_PORT=8791 \
BACKOFFICE_HTTP_PORT=8792 \
PHOTARIUM_ROOT=/Users/julian/Code/cloud-flare-image-handler \
EDITORIAL_ROOT=/Users/julian/Code/nfl-editorial \
BACKOFFICE_ROOT=/Users/julian/Code/nfl-backoffice \
./run_mcp_processes.sh restart

# 2) Update mcp_chat_config.json http_url values to :8101, :8790, :8791, :8792

# 3) Start TUI with same port env vars
COMFY_MCP_HTTP_BIND_PORT=8101 \
PHOTARIUM_HTTP_PORT=8790 \
EDITORIAL_HTTP_PORT=8791 \
BACKOFFICE_HTTP_PORT=8792 \
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

Reset both servers:

```bash
./run_mcp_processes.sh stop
./run_mcp_processes.sh start
```
