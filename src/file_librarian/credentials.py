"""Where the Anthropic API key comes from (``provider.api_key_source``).

``auto`` (the default) tries, in order:

1. the ``ANTHROPIC_API_KEY`` environment variable;
2. the macOS Keychain (``flib key set`` stores it there);
3. the ``.env`` file named by ``provider.env_file``, if set.

When none has a key, the Anthropic SDK's own resolution still applies (for
example an ``ant auth login`` profile). Keys are never logged or printed.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from file_librarian import keychain
from file_librarian.config import ProviderConfig

ENV_VAR = "ANTHROPIC_API_KEY"


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    value: str | None
    source: str
    """Where it was found: "environment", "keychain", "env_file", or "none"."""

    @property
    def hint(self) -> str:
        """A safe way to show which key it is: its last four characters."""
        return f"…{self.value[-4:]}" if self.value and len(self.value) > 8 else "(set)"


def read_env_file(path: Path, name: str = ENV_VAR) -> str | None:
    """``name`` from a ``KEY=value`` file; comments and blank lines are ignored."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if key == name:
            value = value.strip().strip("'\"")
            return value or None
    return None


def env_file_is_private(path: Path) -> bool:
    """Only the owner can read it (mode 600 or stricter)."""
    try:
        return not stat.S_IMODE(path.stat().st_mode) & 0o077
    except OSError:
        return True


def resolve(config: ProviderConfig) -> ResolvedKey:
    source = config.api_key_source
    if source in ("auto", "environment"):
        value = os.environ.get(ENV_VAR, "").strip()
        if value:
            return ResolvedKey(value, "environment")
    if source in ("auto", "keychain"):
        try:
            value = keychain.read() or ""
        except keychain.KeychainError:
            value = ""
        if value:
            return ResolvedKey(value, "keychain")
    if source in ("auto", "env_file") and config.env_file is not None:
        value = read_env_file(config.env_file) or ""
        if value:
            return ResolvedKey(value, "env_file")
    return ResolvedKey(None, "none")
