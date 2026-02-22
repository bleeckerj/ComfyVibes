#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
CFG="$ROOT_DIR/mcp_chat_config.json"
PHOTARIUM_ROOT="${PHOTARIUM_ROOT:-$(cd "$ROOT_DIR/../cloud-flare-image-handler" 2>/dev/null && pwd || true)}"
EDITORIAL_ROOT="${EDITORIAL_ROOT:-$(cd "$ROOT_DIR/../nfl-editorial" 2>/dev/null && pwd || true)}"
BACKOFFICE_ROOT="${BACKOFFICE_ROOT:-$(cd "$ROOT_DIR/../nfl-backoffice" 2>/dev/null && pwd || true)}"
COMFY_MCP_PORT="${COMFY_MCP_HTTP_BIND_PORT:-8181}"
PHOTARIUM_MCP_PORT="${PHOTARIUM_HTTP_PORT:-8787}"
EDITORIAL_MCP_PORT="${EDITORIAL_HTTP_PORT:-8788}"
BACKOFFICE_MCP_PORT="${BACKOFFICE_HTTP_PORT:-8766}"

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
    echo "Photarium helper missing or not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi
  echo "Starting Photarium MCP HTTP on :$PHOTARIUM_MCP_PORT..."
  nohup "$helper" >"$PHOTARIUM_ROOT/.photarium_mcp_http.log" 2>&1 &
  wait_for_port "$PHOTARIUM_MCP_PORT" "Photarium MCP HTTP"
}

start_editorial_mcp_http() {
  if is_listening "$EDITORIAL_MCP_PORT"; then
    echo "Editorial MCP HTTP already running on :$EDITORIAL_MCP_PORT"
    return 0
  fi
  if [[ -z "$EDITORIAL_ROOT" || ! -d "$EDITORIAL_ROOT" ]]; then
    echo "Editorial root not found. Set EDITORIAL_ROOT to your nfl-editorial path." >&2
    return 1
  fi
  local helper="$EDITORIAL_ROOT/run_editorial_mcp_server.sh"
  if [[ ! -x "$helper" ]]; then
    echo "Editorial helper missing or not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi
  echo "Starting Editorial MCP HTTP on :$EDITORIAL_MCP_PORT..."
  nohup env EDITORIAL_HTTP_ENABLED=true EDITORIAL_HTTP_PORT="$EDITORIAL_MCP_PORT" \
    "$helper" >"$EDITORIAL_ROOT/.editorial_mcp_http.log" 2>&1 &
  wait_for_port "$EDITORIAL_MCP_PORT" "Editorial MCP HTTP"
}

start_backoffice_mcp_http() {
  if is_listening "$BACKOFFICE_MCP_PORT"; then
    echo "Backoffice MCP HTTP already running on :$BACKOFFICE_MCP_PORT"
    return 0
  fi
  if [[ -z "$BACKOFFICE_ROOT" || ! -d "$BACKOFFICE_ROOT" ]]; then
    echo "Backoffice root not found. Set BACKOFFICE_ROOT to your nfl-backoffice path." >&2
    return 1
  fi
  local helper="$BACKOFFICE_ROOT/run_backoffice_mcp_server.sh"
  if [[ ! -x "$helper" ]]; then
    echo "Backoffice helper missing or not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi
  echo "Starting Backoffice MCP HTTP on :$BACKOFFICE_MCP_PORT..."
  nohup env BACKOFFICE_HTTP_PORT="$BACKOFFICE_MCP_PORT" \
    "$helper" >"$BACKOFFICE_ROOT/.backoffice_mcp_http.log" 2>&1 &
  wait_for_port "$BACKOFFICE_MCP_PORT" "Backoffice MCP HTTP"
}

# Bring up all configured MCP HTTP servers before TUI.
start_comfy_mcp_http
start_photarium_mcp_http
start_editorial_mcp_http
start_backoffice_mcp_http

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

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export COMFY_MCP_WORKFLOW_LIBRARY_ROOT="${COMFY_MCP_WORKFLOW_LIBRARY_ROOT:-$ROOT_DIR/workflows}"
export COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS="${COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS:-0}"
export TEXTUAL_ALLOW_SIGNALS="${TEXTUAL_ALLOW_SIGNALS:-1}"
exec "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG" --mouse
