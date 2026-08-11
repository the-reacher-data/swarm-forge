#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  exec python3 "$SCRIPT_DIR/toolchain.py" --help
fi

if [[ "${1:-}" == "help" ]]; then
  shift
  exec python3 "$SCRIPT_DIR/toolchain.py" --help "$@"
fi

if [[ "${1:-}" == "doctor" || "${1:-}" == "bootstrap" || "${1:-}" == "integrate" || "${1:-}" == "gate" || "${1:-}" == "hardening" || "${1:-}" == "install-cli" ]]; then
  COMMAND="$1"
  shift
  exec python3 "$SCRIPT_DIR/toolchain.py" "$COMMAND" "$@"
fi

if [[ "${1:-}" == "run" ]]; then
  shift
fi

if [[ "${1:-}" == "close" ]]; then
  shift
  FRAMEWORK_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
  exec "$FRAMEWORK_ROOT/close-swarm" "$@"
fi

exec bb "$SCRIPT_DIR/swarmforge.bb" "$@"
