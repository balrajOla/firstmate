#!/usr/bin/env bash
# Token-free guard: pinned real Pi SDK compilation and RPC extension loading.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/tests/lib.sh"
fm_live_gate default-on OLYMPUS_PI_LIVE node npm cargo python3
sdk="$(mktemp -d "${TMPDIR:-/tmp}/olympus-pi-sdk.XXXXXX")"
trap 'rm -rf -- "$sdk"' EXIT
cp "$ROOT/packages/olympus-pi/"{package.json,package-lock.json,index.ts,client.mjs,view.mjs,wake.mjs,policy.mjs} "$sdk/"
npm ci --prefix "$sdk" --ignore-scripts --no-audit --no-fund
(cd "$sdk" && npm run typecheck)
(cd "$ROOT" && cargo build --workspace --locked)
export OLYMPUS_PI_CLI="$sdk/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
export OLYMPUS_PI_EXTENSION="$sdk/index.ts"
python3 "$ROOT/tests/olympus-e2e.py" ProcessContract.test_real_pi_loads_extension_and_registers_commands_without_model_call
