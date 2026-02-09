#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
CFG="$ROOT_DIR/mcp_chat_config.json"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $ROOT_DIR/.venv. Create it and install deps first." >&2
  exit 1
fi

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" -m comfy_mcp.tui_client.app --config "$CFG" --mouse
