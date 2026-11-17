#!/bin/sh
# Install the `flib` command for your user, with exactly the dependency
# versions in uv.lock (the same ones CI tested and audited).
#
#   ./scripts/install.sh                install or update (asks about the MCP server)
#   ./scripts/install.sh --with-mcp     also install the MCP server for Claude (`flib mcp`)
#   ./scripts/install.sh --without-mcp  leave it out
#
# The MCP server is optional: its SDK adds packages flib needs for nothing else.
# Without a flag, an update keeps it if it is installed; otherwise you are asked,
# and the answer is no when nobody is there to answer (stdin is not a terminal).
#
# A plain `uv tool install .` would resolve the newest versions instead,
# skipping the lockfile and the 7-day quarantine (see SECURITY.md).
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null 2>&1 || { echo "uv is required: brew install uv" >&2; exit 1; }

with_mcp=""
for arg in "$@"; do
  case "$arg" in
    --with-mcp) with_mcp=yes ;;
    --without-mcp) with_mcp=no ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 1 ;;
  esac
done

locked=$(mktemp)
trap 'rm -f "$locked"' EXIT
export_locked() {
  uv export --frozen --no-dev --no-emit-project --quiet "$@" -o "$locked"
}
# Packages that would be installed on this Mac (Windows-only ones never are).
count_packages() {
  grep -E '^[a-z0-9]' "$locked" | grep -cv "sys_platform == 'win32'" || true
}

if [ -z "$with_mcp" ]; then
  tool_python="$(uv tool dir)/file-librarian/bin/python"
  if [ -x "$tool_python" ] && "$tool_python" -c "import mcp" >/dev/null 2>&1; then
    with_mcp=yes  # already installed: an update never drops it silently
  elif [ -t 0 ]; then
    export_locked
    base=$(count_packages)
    export_locked --extra mcp
    extra=$(( $(count_packages) - base ))
    printf "Enable the MCP server for Claude (adds %s packages)? [y/N] " "$extra"
    read -r answer || answer=""
    case "$answer" in
      [yY]|[yY][eE][sS]) with_mcp=yes ;;
      *) with_mcp=no ;;
    esac
  else
    with_mcp=no
  fi
fi

if [ "$with_mcp" = yes ]; then
  export_locked --extra mcp
  # Overrides pin versions but drop a dependency's extras (the SDK needs
  # pyjwt[crypto]), so the locked list is also installed as requirements.
  uv tool install --reinstall --overrides "$locked" --with-requirements "$locked" ".[mcp]"
else
  export_locked
  uv tool install --reinstall --overrides "$locked" .
fi

# Reinstalling recreates flib's environment, including its dedicated
# interpreter: re-install an existing daily run so it keeps working.
flib_bin="$(uv tool dir --bin)/flib"
if [ -f "$HOME/Library/LaunchAgents/io.github.luizpereiradev.file-librarian.plist" ]; then
  "$flib_bin" schedule --refresh
fi
echo
echo "Installed: $(uv tool dir --bin)/flib"
if [ "$with_mcp" = yes ]; then
  echo "MCP server included: see \"Use it from Claude\" in the README."
fi
case ":$PATH:" in
  *":$(uv tool dir --bin):"*) ;;
  *) echo "Add it to your PATH with: uv tool update-shell" ;;
esac
