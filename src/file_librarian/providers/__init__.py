"""AI providers, one module per provider.

| name          | milestone | auth                              |
|---------------|-----------|-----------------------------------|
| anthropic     | M1        | ANTHROPIC_API_KEY                 |
| claude-code   | M2        | the logged-in ``claude`` CLI      |
| ollama        | M3        | none, local model                 |
| openai-compat | M3        | key + URL of a compatible API     |
"""

from __future__ import annotations

from collections.abc import Callable

from file_librarian.config import ProviderConfig
from file_librarian.providers.base import Provider, ProviderError
from file_librarian.providers.fake import FakeProvider

ProviderFactory = Callable[[ProviderConfig], Provider]

# Provider name -> factory. Real providers are added here as they land.
REGISTRY: dict[str, ProviderFactory] = {
    "fake": lambda _cfg: FakeProvider(echo=True),
}

PLANNED = {"anthropic": "M1", "claude-code": "M2", "ollama": "M3", "openai-compat": "M3"}


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
    "Provider",
    "ProviderError",
    "get_provider",
    "is_implemented",
    "known_providers",
]
