# `run_mcp_processes.sh` README

Wrapper script that manages both MCP HTTP servers together:

- Comfy MCP (`run_comfymcp_http_server.sh`)
- Photarium MCP (`run_photarium_mcp_server.sh`)

## Location

- `nfl-comfymcp/run_mcp_processes.sh`

## Commands

From `nfl-comfymcp/`:

```bash
./run_mcp_processes.sh status
./run_mcp_processes.sh start
./run_mcp_processes.sh stop
./run_mcp_processes.sh restart
```

## Configure where Photarium lives

If your `cloud-flare-image-handler` path is non-default, set `PHOTARIUM_ROOT`:

```bash
PHOTARIUM_ROOT=/absolute/path/to/cloud-flare-image-handler ./run_mcp_processes.sh status
```

## Configure ports

This wrapper forwards environment variables to both underlying scripts.

Comfy MCP:
- `COMFY_MCP_HTTP_BIND_HOST` (default `127.0.0.1`)
- `COMFY_MCP_HTTP_BIND_PORT` (default `8001`)

Photarium MCP:
- `PHOTARIUM_HTTP_HOST` (default `127.0.0.1`)
- `PHOTARIUM_HTTP_PORT` (default `8787`)
- `PHOTARIUM_BASE_URL` (default `http://127.0.0.1:3000`)

Example:

```bash
COMFY_MCP_HTTP_BIND_PORT=8101 \
PHOTARIUM_HTTP_PORT=8790 \
PHOTARIUM_ROOT=/Users/julian/Code/cloud-flare-image-handler \
./run_mcp_processes.sh restart
```

## Notes

- `status` prints PID + command info for current listeners.
- `stop` attempts graceful kill, then force-kills if needed.
