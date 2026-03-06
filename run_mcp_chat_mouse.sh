#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$ROOT_DIR/run_mcp_chat.sh" start --mouse "$@"
