#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHOTARIUM_ROOT="${PHOTARIUM_ROOT:-$(cd "$ROOT_DIR/../cloud-flare-image-handler" 2>/dev/null && pwd || true)}"
COMFY_HELPER="$ROOT_DIR/run_comfymcp_http_server.sh"
PHOTARIUM_HELPER="${PHOTARIUM_ROOT:+$PHOTARIUM_ROOT/run_photarium_mcp_server.sh}"
ACTION="${1:-status}"

if [[ ! -x "$COMFY_HELPER" ]]; then
  echo "Missing Comfy helper: $COMFY_HELPER" >&2
  exit 1
fi

if [[ -z "$PHOTARIUM_ROOT" || ! -x "$PHOTARIUM_HELPER" ]]; then
  echo "Missing Photarium helper. Set PHOTARIUM_ROOT and ensure run_photarium_mcp_server.sh is executable." >&2
  exit 1
fi

case "$ACTION" in
  status)
    "$COMFY_HELPER" status
    "$PHOTARIUM_HELPER" status
    ;;
  start)
    "$COMFY_HELPER" start || true
    "$PHOTARIUM_HELPER" start || true
    ;;
  stop)
    "$COMFY_HELPER" stop
    "$PHOTARIUM_HELPER" stop
    ;;
  restart)
    "$COMFY_HELPER" restart &
    COMFY_PID=$!
    "$PHOTARIUM_HELPER" restart &
    PHOTARIUM_PID=$!
    wait "$COMFY_PID" "$PHOTARIUM_PID"
    ;;
  *)
    echo "Usage: $0 [status|start|stop|restart]" >&2
    exit 2
    ;;
esac
