#!/usr/bin/env bash
# Executable workstation/installer fixtures; no real service or provider calls.
set -euo pipefail
# shellcheck source=tests/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
unset BASH_ENV ENV ZDOTDIR OLYMPUS_BIN OLYMPUS_PI_COMMAND
area="$(mktemp -d)"
trap 'rm -rf -- "$area"' EXIT
mkdir -p "$area/tools" "$area/home"
for tool in node npm git curl tar; do
  ln -s "$(command -v "$tool")" "$area/tools/$tool"
done
export PATH="$area/tools:/usr/bin:/bin:/usr/sbin:/sbin"
export WORK_LOG="$area/work.log"
export OLYMPUS_HOME="$area/home"
cat > "$area/tools/balraj-stack-start" <<'SERVICE'
#!/bin/sh
printf '%s\n' services >> "$WORK_LOG"
SERVICE
cat > "$area/tools/pi" <<'PI'
#!/bin/sh
exit 99
PI
cat > "$area/tools/olympus" <<'CLI'
#!/bin/sh
printf 'cli' >> "$WORK_LOG"
printf ' <%s>' "$@" >> "$WORK_LOG"
printf ' launcher=%s cwd=%s skip-version=%s\n' "${OLYMPUS_PI_COMMAND:-bundled}" "$PWD" "${PI_SKIP_VERSION_CHECK:-unset}" >> "$WORK_LOG"
CLI
chmod +x "$area/tools/"{balraj-stack-start,pi,olympus}

test_one_command_uses_saved_home_and_account_launcher() {
  : > "$WORK_LOG"
  bash "$ROOT/bin/balraj-work.sh" -- --provider example --model exact
  grep -q '^services$' "$WORK_LOG" || fail 'configured services were not started'
  grep -q 'cli <chief> <--> <--provider> <example> <--model> <exact>' "$WORK_LOG" || fail 'chief options were not preserved'
  grep -q "launcher=$area/tools/pi cwd=$OLYMPUS_HOME" "$WORK_LOG" || fail 'account launcher or saved home was lost'
  [ "$(wc -l < "$WORK_LOG" | tr -d ' ')" = 2 ] || fail 'startup performed unexpected extra work'
  grep -q 'skip-version=1' "$WORK_LOG" || fail 'installer-managed runtime showed a self-update check'
  : > "$WORK_LOG"
  PI_SKIP_VERSION_CHECK=0 bash "$ROOT/bin/balraj-work.sh" -- --no-session
  grep -q 'skip-version=0' "$WORK_LOG" || fail 'explicit version-check override was ignored'
  pass 'one command starts services and one account-aware Pi chief in the saved home'
}

test_check_and_help_do_not_launch_pi() {
  : > "$WORK_LOG"
  bash "$ROOT/bin/balraj-work.sh" --check
  grep -q 'cli <start>' "$WORK_LOG" || fail 'check did not start coordinator'
  grep -q 'cli <ui>' "$WORK_LOG" || fail 'check did not render the working view'
  ! grep -q 'cli <chief>' "$WORK_LOG" || fail 'check launched a model interface'
  : > "$WORK_LOG"
  bash "$ROOT/bin/balraj-work.sh" --help > "$area/help"
  [ ! -s "$WORK_LOG" ] || fail 'help started services'
  grep -q '/olympus-focus' "$area/help" || fail 'help did not explain how to select work'
  pass 'check renders working view without Pi and help has no startup side effects'
}

test_installed_workstation_is_portable() {
  local source="$area/source" prefix="$area/install with spaces"
  mkdir -p "$source/target/release" "$source/crates/olympusd" "$source/packages/olympus-pi" "$source/docs" "$source/bin"
  : > "$source/crates/olympusd/Cargo.toml"
  cp "$area/tools/olympus" "$source/target/release/olympus"
  cp "$area/tools/olympus" "$source/target/release/olympusd"
  cp "$ROOT/packages/olympus-pi/"{index.ts,panel.ts,parallel.ts,intake.mjs,context-safety.ts,panel-view.mjs,task-view.mjs,task-numbers.mjs,task-control.mjs,gateway-launch.py,client.mjs,view.mjs,wake.mjs,policy.mjs,terminal.mjs,package.json} "$source/packages/olympus-pi/"
  cp "$ROOT/bin/balraj-work.sh" "$source/bin/"
  cp "$ROOT/docs/olympus.md" "$source/docs/"
  bash "$ROOT/bin/olympus-install.sh" --source "$source" --prefix "$prefix" --skip-build --skip-pi > "$area/install.log"
  [ -f "$prefix/lib/olympus/panel.ts" ] && [ -f "$prefix/lib/olympus/panel-view.mjs" ] || fail 'installer omitted panel dependencies'
  : > "$WORK_LOG"
  "$prefix/bin/balraj-work" -- --no-session
  grep -q 'cli <chief> <--> <--no-session>' "$WORK_LOG" || fail 'installed workstation command did not run'
  pass 'fresh user-prefix installation includes working command and widget dependencies'
}
fm_test_run test_one_command_uses_saved_home_and_account_launcher
fm_test_run test_check_and_help_do_not_launch_pi
fm_test_run test_installed_workstation_is_portable
