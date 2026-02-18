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

start_comfy_mcp_http() {
  if is_listening "$COMFY_MCP_PORT"; then
    echo "Comfy MCP HTTP already running on :$COMFY_MCP_PORT"
    return 0
  fi
  echo "Starting Comfy MCP HTTP on :$COMFY_MCP_PORT..."
  nohup "$ROOT_DIR/run_comfymcp_http_server.sh" >"$ROOT_DIR/.comfy_mcp_http.log" 2>&1 &
  wait_for_port "$COMFY_MCP_PORT" "Comfy MCP HTTP"
}

start_photarium_mcp_http() {
  if is_listening "$PHOTARIUM_MCP_PORT"; then
    echo "Photarium MCP HTTP already running on :$PHOTARIUM_MCP_PORT"
    return 0
  fi
  if [[ -z "$PHOTARIUM_ROOT" || ! -d "$PHOTARIUM_ROOT" ]]; then
    echo "Photarium root not found. Set PHOTARIUM_ROOT to your cloud-flare-image-handler path." >&2
    return 1
  fi
  local helper="$PHOTARIUM_ROOT/run_photarium_mcp_server.sh"
  if [[ ! -x "$helper" ]]; then
    echo "Photarium helper not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi
  echo "Starting Photarium MCP HTTP on :$PHOTARIUM_MCP_PORT..."
  nohup "$helper" >"$PHOTARIUM_ROOT/.photarium_mcp_http.log" 2>&1 &
  wait_for_port "$PHOTARIUM_MCP_PORT" "Photarium MCP HTTP"
}

# Sanity check MCP client availability
if ! "$VENV_PY" - <<'PY' >/dev/null 2>&1; then
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
PY
  echo "MCP client SDK not installed in venv. Run: $ROOT_DIR/.venv/bin/pip install -e ." >&2
  exit 1
fi

# Always run local source to avoid stale editable-install issues.
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export COMFY_MCP_WORKFLOW_LIBRARY_ROOT="${COMFY_MCP_WORKFLOW_LIBRARY_ROOT:-$ROOT_DIR/workflows}"
export COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS="${COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS:-0}"

# Bring up both MCP HTTP servers before doctor + TUI.
start_comfy_mcp_http
start_photarium_mcp_http

# Doctor behavior: warn (default), strict, or off
MCP_CHAT_DOCTOR_MODE="${MCP_CHAT_DOCTOR_MODE:-warn}"
if [[ "$MCP_CHAT_DOCTOR_MODE" != "off" ]]; then
  if ! "$VENV_PY" -m comfy_mcp.tui_client.script_runner --config "$CFG" doctor; then
    if [[ "$MCP_CHAT_DOCTOR_MODE" == "strict" ]]; then
      echo "MCP doctor failed (strict mode)." >&2
      exit 1
    fi
    echo "MCP doctor reported issues; continuing to launch TUI (warn mode)." >&2
  fi
fi

exec "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG"
