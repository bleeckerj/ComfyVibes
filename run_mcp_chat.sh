#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ACTION="start"
if [[ "$#" -gt 0 ]]; then
  case "$1" in
    start|restart|stop|status)
      ACTION="$1"
      shift || true
      ;;
    -*)
      ACTION="start"
      ;;
    *)
      ACTION="$1"
      shift || true
      ;;
  esac
fi
TUI_ARGS=()
for arg in "$@"; do
  TUI_ARGS+=("$arg")
done
VENV_PY="$ROOT_DIR/.venv/bin/python"
CFG="$ROOT_DIR/mcp_chat_config.json"
PHOTARIUM_ROOT="${PHOTARIUM_ROOT:-$(cd "$ROOT_DIR/../cloud-flare-image-handler" 2>/dev/null && pwd || true)}"
COMFY_MCP_PORT="${COMFY_MCP_HTTP_BIND_PORT:-8181}"
PHOTARIUM_MCP_PORT="${PHOTARIUM_HTTP_PORT:-8787}"
EDITORIAL_ROOT="${EDITORIAL_ROOT:-$(cd "$ROOT_DIR/../nfl-editorial" 2>/dev/null && pwd || true)}"
EDITORIAL_MCP_PORT="${EDITORIAL_HTTP_PORT:-8788}"
BACKOFFICE_ROOT="${BACKOFFICE_ROOT:-$(cd "$ROOT_DIR/../nfl-backoffice" 2>/dev/null && pwd || true)}"
BACKOFFICE_MCP_PORT="${BACKOFFICE_HTTP_PORT:-8766}"
DIGESTER_ROOT="${DIGESTER_ROOT:-$(cd "$ROOT_DIR/../Digester" 2>/dev/null && pwd || true)}"
DIGESTER_MCP_PORT="${DIGESTER_HTTP_PORT:-8767}"
MCP_CHAT_TUI_PAUSE_SECONDS="${MCP_CHAT_TUI_PAUSE_SECONDS:-0}"

if [[ -z "${COMFY_MCP_COMFY_BASE_URL:-}" && -f "$ROOT_DIR/.env" ]]; then
  comfy_base_from_env_file="$(grep -E '^COMFY_MCP_COMFY_BASE_URL=' "$ROOT_DIR/.env" | tail -n1 | sed 's/^COMFY_MCP_COMFY_BASE_URL=//')"
  if [[ -n "$comfy_base_from_env_file" ]]; then
    export COMFY_MCP_COMFY_BASE_URL="$comfy_base_from_env_file"
  fi
fi

is_listening() {
  local port="$1"
  lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

get_running_comfy_target_base() {
  local payload
  payload="$(curl -sS -X POST "http://127.0.0.1:${COMFY_MCP_PORT}/tools/comfy_server_info" \
    -H 'Content-Type: application/json' \
    -d '{}' 2>/dev/null || true)"
  if [[ -z "$payload" ]]; then
    return 0
  fi
  "$VENV_PY" - <<'PY' "$payload" 2>/dev/null || true
import json
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}
result = data.get("result") if isinstance(data, dict) else None
if isinstance(result, dict):
    data = result
base = ""
if isinstance(data, dict):
    value = data.get("configured_base_url")
    if isinstance(value, str):
        base = value.rstrip("/")
if base:
    print(base)
PY
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
      local expected_base="${COMFY_MCP_COMFY_BASE_URL:-}"
      local running_base
      running_base="$(get_running_comfy_target_base)"
      if [[ -n "$expected_base" && -n "$running_base" && "${running_base%/}" != "${expected_base%/}" ]]; then
        echo "Comfy MCP HTTP on :$COMFY_MCP_PORT is targeting $running_base (expected $expected_base). Restarting..."
        stop_port_listeners "$COMFY_MCP_PORT" "Comfy MCP HTTP"
      else
        if [[ -n "$running_base" ]]; then
          echo "Comfy MCP HTTP already running on :$COMFY_MCP_PORT (target: $running_base)"
        else
          echo "Comfy MCP HTTP already running on :$COMFY_MCP_PORT"
        fi
        return 0
      fi
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

start_digester_mcp_http() {
  local force="${1:-0}"

  if [[ -z "$DIGESTER_ROOT" || ! -d "$DIGESTER_ROOT" ]]; then
    echo "Digester root not found. Set DIGESTER_ROOT to your Digester path." >&2
    return 1
  fi

  local helper="$DIGESTER_ROOT/run_digester_mcp_http_server.sh"
  if [[ ! -x "$helper" ]]; then
    echo "Digester helper not executable: $helper" >&2
    echo "Run: chmod +x $helper" >&2
    return 1
  fi

  if is_listening "$DIGESTER_MCP_PORT"; then
    if [[ "$force" == "1" ]]; then
      stop_port_listeners "$DIGESTER_MCP_PORT" "Digester MCP HTTP"
    else
      echo "Digester MCP HTTP already running on :$DIGESTER_MCP_PORT"
      return 0
    fi
  fi

  if is_listening "$DIGESTER_MCP_PORT"; then
    echo "Digester MCP HTTP still occupies :$DIGESTER_MCP_PORT after stop attempt" >&2
    return 1
  fi

  echo "Starting Digester MCP HTTP on :$DIGESTER_MCP_PORT..."
  nohup env DIGESTER_HTTP_PORT="$DIGESTER_MCP_PORT" \
    "$helper" >"$DIGESTER_ROOT/.digester_mcp_http.log" 2>&1 &
  wait_for_port "$DIGESTER_MCP_PORT" "Digester MCP HTTP"
}

start_all_servers() {
  local force="${1:-0}"
  start_comfy_mcp_http "$force"
  start_photarium_mcp_http "$force"
  start_editorial_mcp_http "$force"
  start_backoffice_mcp_http "$force"
  start_digester_mcp_http "$force"
}

stop_all_servers() {
  stop_port_listeners "$DIGESTER_MCP_PORT" "Digester MCP HTTP"
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
  show_server_status "Digester MCP HTTP" "$DIGESTER_MCP_PORT"
}

maybe_pause_before_tui() {
  local seconds="$1"
  if [[ "$seconds" == "0" || "$seconds" == "0.0" ]]; then
    return 0
  fi
  echo "Waiting ${seconds}s before launching TUI..."
  sleep "$seconds"
}

case "$ACTION" in
  restart)
    stop_all_servers
    start_all_servers 1
    ;;
  start|"")
    start_all_servers
    ;;
  stop)
    stop_all_servers
    exit 0
    ;;
  status)
    status_all_servers
    exit 0
    ;;
  *)
    echo "Usage: $0 [start|restart|stop|status]" >&2
    echo "Set MCP_CHAT_TUI_PAUSE_SECONDS to delay TUI launch after server startup." >&2
    exit 2
    ;;
esac

maybe_pause_before_tui "$MCP_CHAT_TUI_PAUSE_SECONDS"

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
if [[ "${TERM_PROGRAM:-}" == "WezTerm" && -z "${EDGAR_TUI_DISABLE_KITTY_KEYBOARD:-}" ]]; then
  export EDGAR_TUI_DISABLE_KITTY_KEYBOARD=1
fi

# Doctor behavior: warn (default), strict, or off
MCP_CHAT_DOCTOR_MODE="${MCP_CHAT_DOCTOR_MODE:-warn}"
if [[ "$MCP_CHAT_DOCTOR_MODE" != "off" ]]; then
  if ! "$VENV_PY" - <<'PY' "$CFG"; then
import asyncio
import sys
from comfy_mcp.tui_client.script_runner import _doctor

config_path = sys.argv[1] if len(sys.argv) > 1 else "mcp_chat_config.json"
raise SystemExit(asyncio.run(_doctor(config_path)))
PY
    if [[ "$MCP_CHAT_DOCTOR_MODE" == "strict" ]]; then
      echo "MCP doctor failed (strict mode)." >&2
      exit 1
    fi
    echo "MCP doctor reported issues; continuing to launch TUI (warn mode)." >&2
  fi
fi

echo "Launching MCP chat TUI..."
MCP_CHAT_STOP_SERVERS_ON_EXIT="${MCP_CHAT_STOP_SERVERS_ON_EXIT:-0}"
if [[ "$MCP_CHAT_STOP_SERVERS_ON_EXIT" == "1" || "$MCP_CHAT_STOP_SERVERS_ON_EXIT" == "true" || "$MCP_CHAT_STOP_SERVERS_ON_EXIT" == "yes" || "$MCP_CHAT_STOP_SERVERS_ON_EXIT" == "on" ]]; then
  # When enabled, don't exec: we need to regain control and stop background servers.
  if [[ ${#TUI_ARGS[@]} -gt 0 ]]; then
    "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG" "${TUI_ARGS[@]}"
    code=$?
  else
    "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG"
    code=$?
  fi
  echo "Stopping MCP servers (MCP_CHAT_STOP_SERVERS_ON_EXIT=1)..."
  stop_all_servers || true
  exit "$code"
fi

if [[ ${#TUI_ARGS[@]} -gt 0 ]]; then
  exec "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG" "${TUI_ARGS[@]}"
fi
exec "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG"
