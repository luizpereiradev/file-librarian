"""Where a provider's API key comes from (``provider.api_key_source``).

Two providers take a key, each with its own variable and Keychain account
(``KeySpec``), so one never picks up the other's:

- ``anthropic``: ``ANTHROPIC_API_KEY``, Keychain account ``anthropic-api-key``;
- ``openai-compat``: ``provider.api_key_env``, else ``FLIB_OPENAI_COMPAT_API_KEY``,
  Keychain account ``openai-compat-api-key``;
- ``embeddings`` (semantic search, ``search.semantic``): ``search.semantic.api_key_env``,
  else ``FLIB_EMBEDDINGS_API_KEY``, Keychain account ``embeddings-api-key``. It is
  never the classification provider's key, even when both point at one server.

``openai-compat`` does not read ``OPENAI_API_KEY`` unless ``api_key_env`` names
it: the key goes to whatever ``base_url`` says, and an OpenAI key set for other
tools must not reach another server by accident.

``auto`` (the default) tries, in order:

1. the environment variable;
2. the macOS Keychain (``flib key set`` stores it there);
3. the ``.env`` file named by ``provider.env_file``, if set (same variable name).

For ``anthropic``, when none has a key, the SDK's own resolution still applies
(for example an ``ant auth login`` profile). For ``openai-compat`` the key is
optional: a local server usually needs none. Keys are never logged or printed.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from file_librarian import keychain
from file_librarian.config import ProviderConfig, SemanticConfig

ENV_VAR = "ANTHROPIC_API_KEY"
OPENAI_COMPAT_ENV_VAR = "FLIB_OPENAI_COMPAT_API_KEY"
EMBEDDINGS_ENV_VAR = "FLIB_EMBEDDINGS_API_KEY"


@dataclass(frozen=True, slots=True)
class KeySpec:
    """Where one provider's key lives."""

    provider: str
    env_var: str
    account: str
    label: str
    """How the key is named to the user, e.g. "Anthropic API key"."""


def key_spec(config: ProviderConfig) -> KeySpec | None:
    """The key ``config``'s provider uses, or ``None`` when it takes no key."""
    return spec_for(config.name, config.api_key_env)


def spec_for(provider: str, api_key_env: str | None = None) -> KeySpec | None:
    if provider == "anthropic":
        return KeySpec(provider, ENV_VAR, keychain.ACCOUNT, "Anthropic API key")
    if provider == "openai-compat":
        return KeySpec(
            provider,
            api_key_env or OPENAI_COMPAT_ENV_VAR,
            keychain.OPENAI_COMPAT_ACCOUNT,
            "openai-compat API key",
        )
    if provider == "embeddings":
        return KeySpec(
            provider,
            api_key_env or EMBEDDINGS_ENV_VAR,
            keychain.EMBEDDINGS_ACCOUNT,
            "embeddings API key",
        )
    return None


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


def all_specs(config: ProviderConfig) -> list[KeySpec]:
    """Every provider's key, ``config``'s own first; for redacting them all."""
    specs = [
        key_spec(config), spec_for("anthropic"), spec_for("openai-compat"), spec_for("embeddings"),
    ]  # fmt: skip
    found: list[KeySpec] = []
    for spec in specs:
        if spec is not None and all(s.provider != spec.provider for s in found):
            found.append(spec)
    return found


def resolve(config: ProviderConfig, spec: KeySpec | None = None) -> ResolvedKey:
    """The key for ``spec`` (default: ``config``'s provider's), as ``api_key_source`` says."""
    spec = spec or key_spec(config)
    if spec is None:
        return ResolvedKey(None, "none")
    return resolve_spec(spec, config.api_key_source, config.env_file)


def resolve_embeddings(semantic: SemanticConfig) -> ResolvedKey:
    """Semantic search's key: its environment variable, then the Keychain. Optional."""
    spec = cast(KeySpec, spec_for("embeddings", semantic.api_key_env))
    return resolve_spec(spec, "auto", None)


def key_source(config: ProviderConfig, spec: KeySpec) -> str:
    """Where ``resolve`` would find ``spec``'s key ("environment", "keychain", "env_file"
    or "none"), without reading it from the Keychain: for pages that only say whether a
    key is set (#117)."""
    source = config.api_key_source
    if source in ("auto", "environment") and os.environ.get(spec.env_var, "").strip():
        return "environment"
    if source in ("auto", "keychain"):
        try:
            if keychain.exists(spec.account):
                return "keychain"
        except keychain.KeychainError:
            pass
    env_file = config.env_file if source in ("auto", "env_file") else None
    if env_file is not None and read_env_file(env_file, spec.env_var):
        return "env_file"
    return "none"


def resolve_spec(spec: KeySpec, source: str, env_file: Path | None) -> ResolvedKey:
    """``spec``'s key from the places ``source`` allows, in order."""
    if source in ("auto", "environment"):
        value = os.environ.get(spec.env_var, "").strip()
        if value:
            return ResolvedKey(value, "environment")
    if source in ("auto", "keychain"):
        try:
            value = keychain.read(spec.account) or ""
        except keychain.KeychainError:
            value = ""
        if value:
            return ResolvedKey(value, "keychain")
    if source in ("auto", "env_file") and env_file is not None:
        value = read_env_file(env_file, spec.env_var) or ""
        if value:
            return ResolvedKey(value, "env_file")
    return ResolvedKey(None, "none")
