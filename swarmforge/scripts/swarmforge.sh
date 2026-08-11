#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "doctor" || "${1:-}" == "bootstrap" ]]; then
  COMMAND="$1"
  shift
  exec python3 "$SCRIPT_DIR/toolchain.py" "$COMMAND" "$@"
fi

exec bb "$SCRIPT_DIR/swarmforge.bb" "$@"
