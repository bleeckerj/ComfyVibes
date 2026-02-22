#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHOTARIUM_ROOT="${PHOTARIUM_ROOT:-$(cd "$ROOT_DIR/../cloud-flare-image-handler" 2>/dev/null && pwd || true)}"
EDITORIAL_ROOT="${EDITORIAL_ROOT:-$(cd "$ROOT_DIR/../nfl-editorial" 2>/dev/null && pwd || true)}"
BACKOFFICE_ROOT="${BACKOFFICE_ROOT:-$(cd "$ROOT_DIR/../nfl-backoffice" 2>/dev/null && pwd || true)}"
BACKOFFICE_HTTP_PORT="${BACKOFFICE_HTTP_PORT:-8766}"
COMFY_HELPER="$ROOT_DIR/run_comfymcp_http_server.sh"
PHOTARIUM_HELPER="${PHOTARIUM_ROOT:+$PHOTARIUM_ROOT/run_photarium_mcp_server.sh}"
EDITORIAL_HELPER="${EDITORIAL_ROOT:+$EDITORIAL_ROOT/run_editorial_mcp_server.sh}"
BACKOFFICE_HELPER="${BACKOFFICE_ROOT:+$BACKOFFICE_ROOT/run_backoffice_mcp_server.sh}"
ACTION="${1:-status}"

print_health() {
  local url="$1"
  if command -v jq >/dev/null 2>&1; then
    curl -fsS "$url" | jq '.' || true
  else
    curl -fsS "$url" || true
    echo
  fi
}

if [[ ! -x "$COMFY_HELPER" ]]; then
  echo "Missing Comfy helper: $COMFY_HELPER" >&2
  exit 1
fi

if [[ -z "$PHOTARIUM_ROOT" || ! -x "$PHOTARIUM_HELPER" ]]; then
  echo "Missing Photarium helper. Set PHOTARIUM_ROOT and ensure run_photarium_mcp_server.sh is executable." >&2
  exit 1
fi
if [[ -z "$EDITORIAL_ROOT" || ! -x "$EDITORIAL_HELPER" ]]; then
  echo "Missing Editorial helper. Set EDITORIAL_ROOT and ensure run_editorial_mcp_server.sh is executable." >&2
  exit 1
fi
if [[ -z "$BACKOFFICE_ROOT" || ! -x "$BACKOFFICE_HELPER" ]]; then
  echo "Missing Backoffice helper. Set BACKOFFICE_ROOT and ensure run_backoffice_mcp_server.sh is executable." >&2
  exit 1
fi

case "$ACTION" in
  status)
    "$COMFY_HELPER" status
    "$PHOTARIUM_HELPER" status
    "$EDITORIAL_HELPER" status
    "$BACKOFFICE_HELPER" status
    ;;
  start)
    "$COMFY_HELPER" start || true
    "$PHOTARIUM_HELPER" start || true
    "$EDITORIAL_HELPER" start || true
    "$BACKOFFICE_HELPER" start || true
    ;;
  stop)
    "$COMFY_HELPER" stop
    "$PHOTARIUM_HELPER" stop
    "$EDITORIAL_HELPER" stop
    "$BACKOFFICE_HELPER" stop
    ;;
  restart)
    "$COMFY_HELPER" restart &
    COMFY_PID=$!
    "$PHOTARIUM_HELPER" restart &
    PHOTARIUM_PID=$!
    "$EDITORIAL_HELPER" restart &
    EDITORIAL_PID=$!
    "$BACKOFFICE_HELPER" restart &
    BACKOFFICE_PID=$!
    wait "$COMFY_PID" "$PHOTARIUM_PID" "$EDITORIAL_PID" "$BACKOFFICE_PID"
    ;;
  health)
    print_health http://127.0.0.1:8181/health
    print_health http://127.0.0.1:8787/health
    print_health http://127.0.0.1:8788/health
    print_health http://127.0.0.1:${BACKOFFICE_HTTP_PORT}/health
    ;;
  *)
    echo "Usage: $0 [status|start|stop|restart|health]" >&2
    exit 2
    ;;
esac
