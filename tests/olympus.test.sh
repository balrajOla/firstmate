#!/usr/bin/env bash
# Portable Olympus unit, SQLite integration, transport and terminal contracts.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for tool in cargo node python3; do
  command -v "$tool" >/dev/null || { echo "not ok - Olympus requires $tool" >&2; exit 1; }
done
cargo test --workspace --locked
cargo build --workspace --locked
node --test tests/olympus-ui.test.mjs
python3 tests/olympus-e2e.py
