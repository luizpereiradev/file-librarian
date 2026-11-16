#!/bin/sh
# Install the `flib` command for your user, with exactly the dependency
# versions in uv.lock (the same ones CI tested and audited).
#
#   ./scripts/install.sh            install or update
#
# A plain `uv tool install .` would resolve the newest versions instead,
# skipping the lockfile and the 7-day quarantine (see SECURITY.md).
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null 2>&1 || { echo "uv is required: brew install uv" >&2; exit 1; }
locked=$(mktemp)
trap 'rm -f "$locked"' EXIT
uv export --frozen --no-dev --no-emit-project --quiet -o "$locked"
uv tool install --reinstall --overrides "$locked" .
echo
echo "Installed: $(uv tool dir --bin)/flib"
case ":$PATH:" in
  *":$(uv tool dir --bin):"*) ;;
  *) echo "Add it to your PATH with: uv tool update-shell" ;;
esac
