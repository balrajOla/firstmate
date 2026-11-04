#!/usr/bin/env bash
# One terminal: Olympus's live Pi widget above the normal conversation editor.
# --check starts configured services and renders a model-free dashboard snapshot.
# OLYMPUS_BIN selects the CLI, OLYMPUS_HOME selects saved work, and
# OLYMPUS_PI_COMMAND selects an account-aware Pi executable (never shell text).
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
olympus="${OLYMPUS_BIN:-$script_dir/olympus}"
if [ ! -x "$olympus" ]; then
  olympus="$(command -v olympus)" || { echo 'Install Olympus first with bin/olympus-install.sh.' >&2; exit 1; }
fi
case "${1:-}" in
  --help|-h)
    printf '%s\n' 'Usage: balraj-work [--check] | [chief options] [-- Pi options]' \
      'Open your saved Olympus workspace inside Pi; keep typing below the live panel.' \
      'Views: /olympus-panel. Select task/project: /olympus-focus. Full detail: /olympus.' \
      'Colors and labels: cyan focus, green work/results, amber actions, red disconnected.' \
      '--check starts configured services and prints the dashboard without a model call.' \
      'OLYMPUS_HOME selects the workspace; OLYMPUS_PI_COMMAND selects the authenticated Pi launcher.' \
      'No credentials are copied. Automation settings are unchanged.'
    exit 0 ;;
esac
# Existing workstation services are optional. Never stop or forcibly restart them.
if command -v balraj-stack-start >/dev/null 2>&1; then
  balraj-stack-start
fi
# Keep macOS Unix socket paths short while preserving the caller's saved home.
if [ -n "${OLYMPUS_HOME:-}" ] && [ -d "$OLYMPUS_HOME" ]; then
  cd "$OLYMPUS_HOME"
fi
if [ "${1:-}" = --check ]; then
  [ "$#" -eq 1 ] || { echo '--check does not accept other arguments.' >&2; exit 2; }
  "$olympus" start
  exec "$olympus" ui
fi
if [ -z "${OLYMPUS_PI_COMMAND:-}" ] && command -v pi >/dev/null 2>&1; then
  OLYMPUS_PI_COMMAND="$(command -v pi)"
  export OLYMPUS_PI_COMMAND
fi
# The CLI starts the coordinator, reuses its chief identity, and loads the widget.
exec "$olympus" chief "$@"
