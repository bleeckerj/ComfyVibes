# `run_comfymcp_http_server.sh` README

Helper script to run the Comfy MCP HTTP server with lifecycle commands and port ownership diagnostics.

## Location

- `nfl-comfymcp/run_comfymcp_http_server.sh`

## Commands

From `nfl-comfymcp/`:

```bash
./run_comfymcp_http_server.sh start
./run_comfymcp_http_server.sh stop
./run_comfymcp_http_server.sh restart
./run_comfymcp_http_server.sh status
```

## Port configuration

Default bind settings:

- Host: `127.0.0.1`
- Port: `8001`

Override via environment variables:

- `COMFY_MCP_HTTP_BIND_HOST`
- `COMFY_MCP_HTTP_BIND_PORT`

Examples:

```bash
COMFY_MCP_HTTP_BIND_PORT=8101 ./run_comfymcp_http_server.sh start
COMFY_MCP_HTTP_BIND_HOST=0.0.0.0 COMFY_MCP_HTTP_BIND_PORT=8101 ./run_comfymcp_http_server.sh restart
```

## Comfy Org auth for workflow runs

The HTTP server reads `.env` through the normal ComfyMCP configuration loader. To let workflow runs use Comfy Org API nodes, configure local secret files:

```dotenv
COMFY_MCP_COMFY_ORG_AUTH_TOKEN_FILE=~/.config/comfy-mcp/comfy-org-auth-token
COMFY_MCP_COMFY_ORG_API_KEY_FILE=~/.config/comfy-mcp/comfy-org-api-key
```

Direct env values are also supported:

```dotenv
COMFY_MCP_COMFY_ORG_AUTH_TOKEN=your-comfy-org-auth-token
COMFY_MCP_COMFY_ORG_API_KEY=your-comfy-org-api-key
```

These settings are applied server-side when `workflows_run`, `workflows_run_from_source`, or `workflows_run_aspect_ratio_adjustment` submits a prompt. ComfyMCP sends them as ComfyUI `extra_data` keys named `auth_token_comfy_org` and `api_key_comfy_org`, which ComfyUI exposes only through matching hidden node inputs.

## Port conflict behavior

If `start` detects an existing listener on the configured port, it exits with details.

To auto-kill and restart:

```bash
KILL_IF_OCCUPIED=1 ./run_comfymcp_http_server.sh start
```

## Requirements

- Virtual environment at `nfl-comfymcp/.venv`
- Comfy MCP package installed in that environment
