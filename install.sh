#!/bin/sh
# Assistant Workbench installer for macOS and Linux. No sudo.
#
#   curl -fsSL https://raw.githubusercontent.com/balrajOla/firstmate/main/install.sh | sh
#   ./install.sh [--prefix DIR] [--home DIR] [--bin-dir DIR] [--source DIR] [awb setup options]
#
# It installs only open-source software: a Python 3.11+ through uv when the
# machine has none, this package in its own prefix, then `awb setup`, which
# adds Node.js, pnpm, rustup and Paperclip under the Workbench home when they
# are missing, checks the Claude Code and Codex CLIs you already signed in to,
# and starts the services. It never reads or copies their credentials.
# Rerun it to upgrade; every step is skipped when already done.
set -eu

REPO_URL="https://github.com/balrajOla/firstmate.git"
UV_VERSION="0.13.7"
PREFIX="${AWB_PREFIX:-$HOME/.local/awb}"
BIN_DIR="${AWB_BIN_DIR:-$HOME/.local/bin}"
STATE=""
SOURCE=""

say() { printf '==> %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --bin-dir) BIN_DIR="$2"; shift 2 ;;
    --home) STATE="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) break ;;  # everything else is for awb setup
  esac
done

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "Assistant Workbench supports macOS and Linux" ;;
esac
if ! command -v git >/dev/null 2>&1; then
  [ "$(uname -s)" = Darwin ] && die "git is missing: run 'xcode-select --install', then run this installer again"
  die "git is missing: install it with your package manager, then run this installer again"
fi

# 1. Python 3.11 or later.
PYTHON=""
for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  say "No Python 3.11 or later found; installing Python 3.12 with uv $UV_VERSION under $PREFIX/tools"
  mkdir -p "$PREFIX/tools/uv"
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | env UV_UNMANAGED_INSTALL="$PREFIX/tools/uv" sh
  UV_PYTHON_INSTALL_DIR="$PREFIX/tools/python" "$PREFIX/tools/uv/uv" python install 3.12
  PYTHON="$(UV_PYTHON_INSTALL_DIR="$PREFIX/tools/python" "$PREFIX/tools/uv/uv" python find 3.12)"
fi
say "Python: $PYTHON"

# 2. The source: this checkout, or a clone kept under the prefix.
if [ -z "$SOURCE" ]; then
  here="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
  if [ -n "$here" ] && [ -f "$here/pyproject.toml" ] && grep -q '^name = "assistant-workbench"' "$here/pyproject.toml"; then
    SOURCE="$here"
  else
    SOURCE="$PREFIX/src"
    if [ -d "$SOURCE/.git" ]; then
      say "Updating $SOURCE"
      git -C "$SOURCE" pull --ff-only --quiet
    else
      say "Downloading Assistant Workbench into $SOURCE"
      git clone --depth 1 --quiet "$REPO_URL" "$SOURCE"
    fi
  fi
fi

# 3. An owned release with its own launcher; the core needs only the standard library.
say "Installing into $PREFIX"
PYTHONPATH="$SOURCE/src" "$PYTHON" -m assistant_workbench install --source "$SOURCE" --prefix "$PREFIX" >/dev/null
AWB="$PREFIX/bin/awb"

# 4. Put awb on PATH without replacing a different awb.
mkdir -p "$BIN_DIR"
if [ -L "$BIN_DIR/awb" ] && [ "$(readlink "$BIN_DIR/awb")" = "$AWB" ]; then
  :
elif [ -e "$BIN_DIR/awb" ] || [ -L "$BIN_DIR/awb" ]; then
  say "Kept the existing $BIN_DIR/awb; this installation's launcher is $AWB"
else
  ln -s "$AWB" "$BIN_DIR/awb"
fi
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) say "Add $BIN_DIR to PATH, for example: echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zprofile" ;;
esac

# 5. Everything else.
if [ -n "$STATE" ]; then
  exec "$AWB" --home "$STATE" setup --executable "$AWB" "$@"
fi
exec "$AWB" setup --executable "$AWB" "$@"
