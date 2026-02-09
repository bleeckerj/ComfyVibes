#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
CFG="$ROOT_DIR/mcp_chat_config.json"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $ROOT_DIR/.venv. Create it and install deps first." >&2
  exit 1
fi

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

# Full doctor check so endpoint/network issues show up before launching TUI.
if ! "$VENV_PY" -m comfy_mcp.tui_client.script_runner --config "$CFG" doctor; then
  echo "MCP doctor failed. Fix endpoint/service issues above, then retry." >&2
  exit 1
fi

exec "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG"
