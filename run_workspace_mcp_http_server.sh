#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
ACTION="${1:-start}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $ROOT_DIR/.venv. Create it and install deps first." >&2
  exit 1
fi

cd "$ROOT_DIR"

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export WORKSPACE_MCP_HTTP_BIND_PORT="${WORKSPACE_MCP_HTTP_BIND_PORT:-8777}"
export WORKSPACE_MCP_ROOTS="${WORKSPACE_MCP_ROOTS:-/Users/julian/Code}"

port_pids() {
  lsof -tiTCP:"$WORKSPACE_MCP_HTTP_BIND_PORT" -sTCP:LISTEN 2>/dev/null || true
}

show_port_owners() {
  local pids
  pids="$(port_pids)"
  if [[ -z "$pids" ]]; then
    echo "No listener on 127.0.0.1:${WORKSPACE_MCP_HTTP_BIND_PORT}"
    return 1
  fi
  echo "Listener(s) on 127.0.0.1:${WORKSPACE_MCP_HTTP_BIND_PORT}:"
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    ps -p "$pid" -ww -o pid,ppid,args
  done <<< "$pids"
  return 0
}

stop_listeners() {
  local pids
  pids="$(port_pids)"
  if [[ -z "$pids" ]]; then
    echo "Nothing to stop on :$WORKSPACE_MCP_HTTP_BIND_PORT"
    return 0
  fi
  echo "Stopping listener(s) on :$WORKSPACE_MCP_HTTP_BIND_PORT -> $pids"
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    kill "$pid" 2>/dev/null || true
  done <<< "$pids"
  sleep 0.3
  local still
  still="$(port_pids)"
  if [[ -n "$still" ]]; then
    echo "Force-killing remaining: $still"
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      kill -9 "$pid" 2>/dev/null || true
    done <<< "$still"
  fi
}

case "$ACTION" in
  status)
    show_port_owners || true
    ;;
  stop)
    stop_listeners
    ;;
  restart)
    stop_listeners
    exec "$VENV_PY" -m comfy_mcp.workspace_server.http_server
    ;;
  start)
    if show_port_owners >/dev/null 2>&1; then
      if [[ "${KILL_IF_OCCUPIED:-0}" == "1" ]]; then
        echo "Port $WORKSPACE_MCP_HTTP_BIND_PORT is occupied; KILL_IF_OCCUPIED=1 so restarting..."
        stop_listeners
      else
        echo "Port $WORKSPACE_MCP_HTTP_BIND_PORT is already in use."
        show_port_owners || true
        echo "Use: $0 stop   (or KILL_IF_OCCUPIED=1 $0 start)"
        exit 1
      fi
    fi
    exec "$VENV_PY" -m comfy_mcp.workspace_server.http_server
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status]" >&2
    exit 2
    ;;
esac

