#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $ROOT_DIR/.venv. Create it and install deps first." >&2
  exit 1
fi

export COMFYVIBE_ATTRACT=1
export COMFYVIBE_COLOR=1
export PYTHONPATH="$ROOT_DIR/src"

exec "$VENV_PY" -m comfy_mcp.mcp_server.cli
