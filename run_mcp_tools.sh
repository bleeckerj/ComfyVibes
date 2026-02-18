#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
CFG="$ROOT_DIR/mcp_chat_config.json"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $ROOT_DIR/.venv. Create it first." >&2
  exit 1
fi

if [[ $# -lt 1 ]]; then
  cat >&2 <<'EOF'
Usage:
  ./run_mcp_tools.sh doctor
  ./run_mcp_tools.sh list-tools
  ./run_mcp_tools.sh call --tool TOOL_NAME --args '{"key":"value"}'
  ./run_mcp_tools.sh run-steps --steps-file steps.json
EOF
  exit 2
fi

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export COMFY_MCP_WORKFLOW_LIBRARY_ROOT="${COMFY_MCP_WORKFLOW_LIBRARY_ROOT:-$ROOT_DIR/workflows}"
export COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS="${COMFY_MCP_INCLUDE_EXTRA_WORKFLOW_ROOTS:-0}"
exec "$VENV_PY" -m comfy_mcp.tui_client.script_runner --config "$CFG" "$@"
