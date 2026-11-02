#!/usr/bin/env bash
# Install Olympus into a user-owned prefix without sudo or changing shell startup files.
# Optional: --prefix PATH --source PATH --skip-pi --skip-build (existing release binaries).
set -euo pipefail
umask 077
prefix="${OLYMPUS_PREFIX:-$HOME/.local}"
source_dir="${OLYMPUS_SOURCE:-}"
skip_pi=0
skip_build=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --source) source_dir="$2"; shift 2 ;;
    --skip-pi) skip_pi=1; shift ;;
    --skip-build) skip_build=1; shift ;;
    *) echo "Unknown installer option: $1" >&2; exit 2 ;;
  esac
done
case "$prefix" in /*) ;; *) echo "Installation prefix must be absolute" >&2; exit 2 ;; esac
for tool in curl tar git; do
  command -v "$tool" >/dev/null || { echo "Install $tool, then run this command again." >&2; exit 1; }
done
mkdir -p "$prefix/bin" "$prefix/lib/olympus"
if [ -z "$source_dir" ]; then
  if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [ -f "$candidate/crates/olympusd/Cargo.toml" ]; then source_dir="$candidate"; fi
  fi
fi
if [ -z "$source_dir" ]; then
  source_dir="$prefix/lib/olympus/source-$(date +%Y%m%d%H%M%S)-$$"
  git clone --depth 1 --branch "${OLYMPUS_REF:-codex/olympus-control-room}" \
    https://github.com/balrajOla/firstmate.git "$source_dir"
fi
source_dir="$(cd "$source_dir" && pwd)"
if [ ! -f "$source_dir/crates/olympusd/Cargo.toml" ]; then
  echo "Source does not contain the Olympus workspace" >&2; exit 1
fi
# Bootstrap a private Node 24 runtime if no supported system Node is present.
if ! command -v node >/dev/null || ! command -v npm >/dev/null || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)'; then
  case "$(uname -s)" in Linux) os=linux ;; Darwin) os=darwin ;; *) echo "Use Linux, macOS, or WSL2" >&2; exit 1 ;; esac
  case "$(uname -m)" in x86_64) arch=x64 ;; arm64|aarch64) arch=arm64 ;; *) echo "Unsupported architecture" >&2; exit 1 ;; esac
  runtime_tmp="$(mktemp -d)"
  trap 'rm -rf -- "$runtime_tmp"' EXIT
  curl --fail --silent --show-error --location https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt -o "$runtime_tmp/SHASUMS256.txt"
  archive="$(awk -v suffix="-$os-$arch.tar.gz" '$2 ~ suffix "$" {print $2; exit}' "$runtime_tmp/SHASUMS256.txt")"
  [ -n "$archive" ] || { echo "Official Node release manifest has no compatible archive" >&2; exit 1; }
  curl --fail --silent --show-error --location "https://nodejs.org/dist/latest-v24.x/$archive" -o "$runtime_tmp/$archive"
  expected="$(awk -v name="$archive" '$2 == name {print $1}' "$runtime_tmp/SHASUMS256.txt")"
  if command -v sha256sum >/dev/null; then actual="$(sha256sum "$runtime_tmp/$archive" | awk '{print $1}')"; else actual="$(shasum -a 256 "$runtime_tmp/$archive" | awk '{print $1}')"; fi
  [ "$expected" = "$actual" ] || { echo "Node checksum mismatch" >&2; exit 1; }
  mkdir -p "$prefix/lib/olympus/node-runtime"
  tar --no-same-owner -xzf "$runtime_tmp/$archive" -C "$prefix/lib/olympus/node-runtime" --strip-components=1
  export PATH="$prefix/lib/olympus/node-runtime/bin:$PATH"
fi
if [ "$skip_build" = 0 ]; then
  if ! command -v cargo >/dev/null; then
    export CARGO_HOME="$prefix/lib/olympus/toolchain/cargo"
    export RUSTUP_HOME="$prefix/lib/olympus/toolchain/rustup"
    mkdir -p "$CARGO_HOME" "$RUSTUP_HOME"
    rust_installer="$prefix/lib/olympus/rustup-init.sh"
    curl --fail --silent --show-error --location https://sh.rustup.rs -o "$rust_installer"
    sh "$rust_installer" -y --profile minimal --no-modify-path
    export PATH="$CARGO_HOME/bin:$PATH"
  fi
  (cd "$source_dir" && cargo build --release --locked --workspace)
fi
for binary in olympus olympusd; do
  [ -x "$source_dir/target/release/$binary" ] || { echo "Release binary missing: $binary" >&2; exit 1; }
  cp "$source_dir/target/release/$binary" "$prefix/bin/$binary.new"
  chmod 755 "$prefix/bin/$binary.new"
  mv "$prefix/bin/$binary.new" "$prefix/bin/$binary"
done
for asset in index.ts client.mjs view.mjs wake.mjs policy.mjs terminal.mjs package.json; do
  cp "$source_dir/packages/olympus-pi/$asset" "$prefix/lib/olympus/$asset"
done
mkdir -p "$prefix/lib/olympus/docs"
cp "$source_dir/docs/olympus"*.md "$prefix/lib/olympus/docs/"
printf '%s\n' "$source_dir" > "$prefix/lib/olympus/source-path"
if [ "$skip_pi" = 0 ]; then
  mkdir -p "$prefix/lib/olympus/pi-runtime"
  cp "$source_dir/packages/olympus-pi/package.json" "$source_dir/packages/olympus-pi/package-lock.json" "$prefix/lib/olympus/pi-runtime/"
  npm ci --prefix "$prefix/lib/olympus/pi-runtime" --omit=dev --ignore-scripts --no-audit --no-fund
  # Pi loads the extension from its parent directory; expose the same exact dependency tree.
  if [ ! -e "$prefix/lib/olympus/node_modules" ]; then
    ln -s pi-runtime/node_modules "$prefix/lib/olympus/node_modules"
  fi
fi
# The wrapper exposes private Node/Pi only to Olympus and its children.
mv "$prefix/bin/olympus" "$prefix/lib/olympus/olympus-real"
cat > "$prefix/bin/olympus" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
prefix="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$prefix/lib/olympus/node-runtime/bin:$prefix/lib/olympus/pi-runtime/node_modules/.bin:$PATH"
exec "$prefix/lib/olympus/olympus-real" "$@"
WRAPPER
chmod 755 "$prefix/bin/olympus"
# The detached executable resolves its sibling daemon and terminal assets here.
cp "$prefix/bin/olympusd" "$prefix/lib/olympus/olympusd"
printf '\nInstalled Olympus in %s\n' "$prefix"
printf 'Start: %s/bin/olympus init && %s/bin/olympus start && %s/bin/olympus ui\n' "$prefix" "$prefix" "$prefix"
printf 'Pi chief: %s/bin/olympus chief\n' "$prefix"
# shellcheck disable=SC2016 # Print a copyable command with the user's future PATH expansion.
printf 'If needed, add to PATH: export PATH="%s/bin:$PATH"\n' "$prefix"
printf 'A fresh home starts with live dispatch disabled. Provider logins remain in your own harnesses.\n'
