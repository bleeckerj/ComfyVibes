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

## Port conflict behavior

If `start` detects an existing listener on the configured port, it exits with details.

To auto-kill and restart:

```bash
KILL_IF_OCCUPIED=1 ./run_comfymcp_http_server.sh start
```

## Requirements

- Virtual environment at `nfl-comfymcp/.venv`
- Comfy MCP package installed in that environment
