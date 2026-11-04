#!/usr/bin/env bash
# Opt-in real WezTerm GUI guard. Creates and closes only a new owned test window,
# uses an isolated Olympus simulator home, and submits no model prompts.
# Requires built Olympus binaries, installed pinned Pi dependencies, and a running
# WezTerm GUI. WEZTERM_BIN, OLYMPUS_TEST_BIN and OLYMPUS_PI_CLI may select installs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/tests/lib.sh"
fm_live_gate opt-in OLYMPUS_WEZTERM_LIVE node python3
if [ -z "${WEZTERM_BIN:-}" ]; then
  if command -v wezterm >/dev/null 2>&1; then
    WEZTERM_BIN="$(command -v wezterm)"
  elif [ -x /Applications/WezTerm.app/Contents/MacOS/wezterm ]; then
    WEZTERM_BIN=/Applications/WezTerm.app/Contents/MacOS/wezterm
  else
    echo 'WezTerm CLI missing; set WEZTERM_BIN.' >&2
    exit 1
  fi
fi
export WEZTERM_BIN
export OLYMPUS_PI_CLI="${OLYMPUS_PI_CLI:-$ROOT/packages/olympus-pi/node_modules/@earendil-works/pi-coding-agent/dist/cli.js}"
export OLYMPUS_PI_EXTENSION="${OLYMPUS_PI_EXTENSION:-$ROOT/packages/olympus-pi/index.ts}"
[ -f "$OLYMPUS_PI_CLI" ] || { echo 'Install the pinned packages/olympus-pi dependencies before this guard.' >&2; exit 1; }
"$WEZTERM_BIN" --version
"$WEZTERM_BIN" cli list --format json >/dev/null
python3 "$ROOT/tests/olympus-e2e.py" ProcessContract.test_real_wezterm_colors_unicode_resize_and_draft
