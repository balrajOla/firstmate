#!/usr/bin/env bash
# Opt-in installed-account startup check. Runs the command through a fresh login
# shell and checks selected-model authentication plus UI registration via RPC.
# Does not send model prompts or print credentials. Requires an installed balraj-work.
# WORKSTATION_SHELL and BALRAJ_WORK_COMMAND can select a shell or installation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/tests/lib.sh"
fm_live_gate opt-in OLYMPUS_AUTH_LIVE python3
export WORKSTATION_SHELL="${WORKSTATION_SHELL:-${SHELL:-/bin/bash}}"
python3 "$ROOT/tests/olympus-auth-live.py"
