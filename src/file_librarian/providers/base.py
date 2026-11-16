"""The interface every AI provider implements.

Adding a provider must not require changes outside ``providers/``: write a
module with a class satisfying ``Provider`` and register it in
``providers/__init__.py``. See docs/adding-a-provider.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from file_librarian.models import Classification, FileContext, Rules


class ProviderError(Exception):
    """The provider failed to produce a usable answer for one file."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class FatalProviderError(ProviderError):
    """The provider cannot work at all (credentials, unknown model): stop the run."""


@dataclass(slots=True)
class Usage:
    """Tokens billed for the last call, summed over fallback attempts."""

    input_tokens: int = 0
    output_tokens: int = 0
    models: list[str] = field(default_factory=list)


@runtime_checkable
class Provider(Protocol):
    name: str

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        """Return a schema-valid classification for one file.

        Implementations return data only. They never touch the file system and
        never decide paths. Raise ``ProviderError`` on failure; the classifier
        handles retries, rate limits and the ``failed`` status.
        """
        ...
