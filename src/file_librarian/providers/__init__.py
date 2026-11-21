"""AI providers, one module per provider.

| name          | milestone | auth                                              |
|---------------|-----------|---------------------------------------------------|
| anthropic     | M1        | ANTHROPIC_API_KEY                                 |
| claude-code   | M2        | the logged-in ``claude`` CLI                      |
| openai-compat | M3        | ``provider.base_url`` + optional key              |
| ollama        | M3        | none, local model (Ollama's own API, #92)         |
"""

from __future__ import annotations

from collections.abc import Callable

from file_librarian.config import ProviderConfig
from file_librarian.providers.base import FatalProviderError, Provider, ProviderError
from file_librarian.providers.fake import FakeProvider

ProviderFactory = Callable[[ProviderConfig], Provider]


# Provider name -> factory. Real providers are added here as they land.
def _anthropic(config: ProviderConfig) -> Provider:
    from file_librarian.providers.anthropic import AnthropicProvider  # imports the SDK lazily

    return AnthropicProvider(config)


def _claude_code(config: ProviderConfig) -> Provider:
    from file_librarian.providers.claude_code import ClaudeCodeProvider

    return ClaudeCodeProvider(config)


def _openai_compat(config: ProviderConfig) -> Provider:
    from file_librarian.providers.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(config)


def _ollama(config: ProviderConfig) -> Provider:
    from file_librarian.providers.ollama import OllamaProvider

    return OllamaProvider(config)


REGISTRY: dict[str, ProviderFactory] = {
    "fake": lambda _cfg: FakeProvider(echo=True),
    "anthropic": _anthropic,
    "claude-code": _claude_code,
    "openai-compat": _openai_compat,
    "ollama": _ollama,
}

# Provider names the configuration accepts before they are implemented, with their milestone.
PLANNED: dict[str, str] = {}


def known_providers() -> frozenset[str]:
    """Every name the configuration accepts: implemented and planned providers."""
    return frozenset({*REGISTRY, *PLANNED})


def is_implemented(name: str) -> bool:
    return name in REGISTRY


def get_provider(config: ProviderConfig) -> Provider:
    factory = REGISTRY.get(config.name)
    if factory is not None:
        return factory(config)
    if config.name in PLANNED:
        raise ProviderError(
            f"provider {config.name!r} is planned for {PLANNED[config.name]} "
            "and not implemented yet"
        )
    known = ", ".join(sorted({*REGISTRY, *PLANNED}))
    raise ProviderError(f"unknown provider {config.name!r} (known: {known})")


__all__ = [
    "PLANNED",
    "REGISTRY",
    "FatalProviderError",
    "Provider",
    "ProviderError",
    "get_provider",
    "is_implemented",
    "known_providers",
]
