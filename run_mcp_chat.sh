#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="${1:-start}"
VENV_PY="$ROOT_DIR/.venv/bin/python"
CFG="$ROOT_DIR/mcp_chat_config.json"
PHOTARIUM_ROOT="${PHOTARIUM_ROOT:-$(cd "$ROOT_DIR/../cloud-flare-image-handler" 2>/dev/null && pwd || true)}"
COMFY_MCP_PORT="${COMFY_MCP_HTTP_BIND_PORT:-8181}"
PHOTARIUM_MCP_PORT="${PHOTARIUM_HTTP_PORT:-8787}"
EDITORIAL_ROOT="${EDITORIAL_ROOT:-$(cd "$ROOT_DIR/../nfl-editorial" 2>/dev/null && pwd || true)}"
EDITORIAL_MCP_PORT="${EDITORIAL_HTTP_PORT:-8788}"
BACKOFFICE_ROOT="${BACKOFFICE_ROOT:-$(cd "$ROOT_DIR/../nfl-backoffice" 2>/dev/null && pwd || true)}"
BACKOFFICE_MCP_PORT="${BACKOFFICE_HTTP_PORT:-8766}"

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

show_server_status() {
  local name="$1"
  local port="$2"
  if is_listening "$port"; then
    echo "$name: listening on :$port"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN || true
  else
    echo "$name: not listening on :$port"
  fi
}

stop_port_listeners() {
  local port="$1"
  local name="$2"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    echo "$name already stopped on :$port"
    return 0
  fi
  echo "Stopping $name on :$port (pid(s): $pids)"
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    kill "$pid" 2>/dev/null || true
  done <<< "$pids"

  for _ in {1..20}; do
    if ! is_listening "$port"; then
      echo "$name stopped"
      return 0
    fi
    sleep 0.2
  done

  echo "Force stopping $name on :$port"
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    kill -9 "$pid" 2>/dev/null || true
  done <<< "$pids"

  if is_listening "$port"; then
    echo "Failed to stop $name on :$port" >&2
    return 1
  fi
}

start_comfy_mcp_http() {
  local force="${1:-0}"
  local helper="$ROOT_DIR/run_comfymcp_http_server.sh"

  if [[ ! -x "$helper" ]]; then
    echo "Comfy helper not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi

  if is_listening "$COMFY_MCP_PORT"; then
    if [[ "$force" == "1" ]]; then
      stop_port_listeners "$COMFY_MCP_PORT" "Comfy MCP HTTP"
    else
      echo "Comfy MCP HTTP already running on :$COMFY_MCP_PORT"
      return 0
    fi
  fi

  if is_listening "$COMFY_MCP_PORT"; then
    echo "Comfy MCP HTTP still occupies :$COMFY_MCP_PORT after stop attempt" >&2
    return 1
  fi

  echo "Starting Comfy MCP HTTP on :$COMFY_MCP_PORT..."
  nohup "$helper" >"$ROOT_DIR/.comfy_mcp_http.log" 2>&1 &
  wait_for_port "$COMFY_MCP_PORT" "Comfy MCP HTTP"
}

start_photarium_mcp_http() {
  local force="${1:-0}"

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

  if is_listening "$PHOTARIUM_MCP_PORT"; then
    if [[ "$force" == "1" ]]; then
      stop_port_listeners "$PHOTARIUM_MCP_PORT" "Photarium MCP HTTP"
    else
      echo "Photarium MCP HTTP already running on :$PHOTARIUM_MCP_PORT"
      return 0
    fi
  fi

  if is_listening "$PHOTARIUM_MCP_PORT"; then
    echo "Photarium MCP HTTP still occupies :$PHOTARIUM_MCP_PORT after stop attempt" >&2
    return 1
  fi

  echo "Starting Photarium MCP HTTP on :$PHOTARIUM_MCP_PORT..."
  nohup "$helper" >"$PHOTARIUM_ROOT/.photarium_mcp_http.log" 2>&1 &
  wait_for_port "$PHOTARIUM_MCP_PORT" "Photarium MCP HTTP"
}

start_editorial_mcp_http() {
  local force="${1:-0}"

  if [[ -z "$EDITORIAL_ROOT" || ! -d "$EDITORIAL_ROOT" ]]; then
    echo "Editorial root not found. Set EDITORIAL_ROOT to your nfl-editorial path." >&2
    return 1
  fi

  local helper="$EDITORIAL_ROOT/run_editorial_mcp_server.sh"
  if [[ ! -x "$helper" ]]; then
    echo "Editorial helper not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi

  if is_listening "$EDITORIAL_MCP_PORT"; then
    if [[ "$force" == "1" ]]; then
      stop_port_listeners "$EDITORIAL_MCP_PORT" "Editorial MCP HTTP"
    else
      echo "Editorial MCP HTTP already running on :$EDITORIAL_MCP_PORT"
      return 0
    fi
  fi

  if is_listening "$EDITORIAL_MCP_PORT"; then
    echo "Editorial MCP HTTP still occupies :$EDITORIAL_MCP_PORT after stop attempt" >&2
    return 1
  fi

  echo "Starting Editorial MCP HTTP on :$EDITORIAL_MCP_PORT..."
  nohup env EDITORIAL_HTTP_ENABLED=true EDITORIAL_HTTP_PORT="$EDITORIAL_MCP_PORT" \
    "$helper" >"$EDITORIAL_ROOT/.editorial_mcp_http.log" 2>&1 &
  wait_for_port "$EDITORIAL_MCP_PORT" "Editorial MCP HTTP"
}

start_backoffice_mcp_http() {
  local force="${1:-0}"

  if [[ -z "$BACKOFFICE_ROOT" || ! -d "$BACKOFFICE_ROOT" ]]; then
    echo "Backoffice root not found. Set BACKOFFICE_ROOT to your nfl-backoffice path." >&2
    return 1
  fi

  local helper="$BACKOFFICE_ROOT/run_backoffice_mcp_server.sh"
  if [[ ! -x "$helper" ]]; then
    echo "Backoffice helper not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi

  if is_listening "$BACKOFFICE_MCP_PORT"; then
    if [[ "$force" == "1" ]]; then
      stop_port_listeners "$BACKOFFICE_MCP_PORT" "Backoffice MCP HTTP"
    else
      echo "Backoffice MCP HTTP already running on :$BACKOFFICE_MCP_PORT"
      return 0
    fi
  fi

  if is_listening "$BACKOFFICE_MCP_PORT"; then
    echo "Backoffice MCP HTTP still occupies :$BACKOFFICE_MCP_PORT after stop attempt" >&2
    return 1
  fi

  echo "Starting Backoffice MCP HTTP on :$BACKOFFICE_MCP_PORT..."
  nohup env BACKOFFICE_HTTP_PORT="$BACKOFFICE_MCP_PORT" \
    "$helper" >"$BACKOFFICE_ROOT/.backoffice_mcp_http.log" 2>&1 &
  wait_for_port "$BACKOFFICE_MCP_PORT" "Backoffice MCP HTTP"
}

start_all_servers() {
  local force="${1:-0}"
  start_comfy_mcp_http "$force"
  start_photarium_mcp_http "$force"
  start_editorial_mcp_http "$force"
  start_backoffice_mcp_http "$force"
}

stop_all_servers() {
  stop_port_listeners "$EDITORIAL_MCP_PORT" "Editorial MCP HTTP"
  stop_port_listeners "$BACKOFFICE_MCP_PORT" "Backoffice MCP HTTP"
  stop_port_listeners "$PHOTARIUM_MCP_PORT" "Photarium MCP HTTP"
  stop_port_listeners "$COMFY_MCP_PORT" "Comfy MCP HTTP"
}

status_all_servers() {
  show_server_status "Comfy MCP HTTP" "$COMFY_MCP_PORT"
  show_server_status "Photarium MCP HTTP" "$PHOTARIUM_MCP_PORT"
  show_server_status "Editorial MCP HTTP" "$EDITORIAL_MCP_PORT"
  show_server_status "Backoffice MCP HTTP" "$BACKOFFICE_MCP_PORT"
}

case "$ACTION" in
  restart)
    stop_all_servers
    start_all_servers 1
    ;;
  start|"")
    start_all_servers
    ;;
  *)
    echo "Usage: $0 [start|restart]" >&2
    exit 2
    ;;
esac

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $ROOT_DIR/.venv. Create it and install deps first." >&2
  exit 1
fi

# Sanity check MCP client availability only when launching the TUI.
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
export TEXTUAL_ALLOW_SIGNALS="${TEXTUAL_ALLOW_SIGNALS:-1}"

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
