#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
CFG="$ROOT_DIR/mcp_chat_config.json"
PHOTARIUM_ROOT="${PHOTARIUM_ROOT:-$(cd "$ROOT_DIR/../cloud-flare-image-handler" 2>/dev/null && pwd || true)}"
COMFY_MCP_PORT="${COMFY_MCP_HTTP_BIND_PORT:-8181}"
PHOTARIUM_MCP_PORT="${PHOTARIUM_HTTP_PORT:-8787}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $ROOT_DIR/.venv. Create it and install deps first." >&2
  exit 1
fi

is_listening() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

wait_for_port() {
  local port="$1"
  local name="$2"
  for _ in {1..25}; do
    if is_listening "$port"; then
      return 0
    fi
    sleep 0.2
  done
  echo "$name failed to start on port $port" >&2
  return 1
}

if ! is_listening "$COMFY_MCP_PORT"; then
  nohup "$ROOT_DIR/run_comfymcp_http_server.sh" >"$ROOT_DIR/.comfy_mcp_http.log" 2>&1 &
  wait_for_port "$COMFY_MCP_PORT" "Comfy MCP HTTP"
fi

if ! is_listening "$PHOTARIUM_MCP_PORT"; then
  if [[ -z "$PHOTARIUM_ROOT" || ! -x "$PHOTARIUM_ROOT/run_photarium_mcp_server.sh" ]]; then
    echo "Photarium helper missing or not executable. Set PHOTARIUM_ROOT and chmod +x run_photarium_mcp_server.sh" >&2
    exit 1
  fi
  nohup "$PHOTARIUM_ROOT/run_photarium_mcp_server.sh" >"$PHOTARIUM_ROOT/.photarium_mcp_http.log" 2>&1 &
  wait_for_port "$PHOTARIUM_MCP_PORT" "Photarium MCP HTTP"
fi

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export COMFY_MCP_WORKFLOW_LIBRARY_ROOT="${COMFY_MCP_WORKFLOW_LIBRARY_ROOT:-$ROOT_DIR/workflows}"
export COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS="${COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS:-0}"
exec "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG" --mouse
