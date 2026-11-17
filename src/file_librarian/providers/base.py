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
    local: bool
    """True when no remote API is behind it: no requests-per-minute spacing."""
    attachments: frozenset[str]
    """Media types ``classify`` can read from ``context.attachment``: ``"image/jpeg"``
    for images, ``"application/pdf"`` for scanned PDFs. Empty when the provider
    reads text only; images and scanned PDFs then fall back to metadata."""
    last_usage: Usage
    """Set after every ``classify`` call, failed ones included; the classifier resets it."""

    def cost_usd(self, usage: Usage) -> float:
        """What ``usage`` cost in US dollars, for ``limits.max_cost_usd_per_run``."""
        ...

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        """Return a schema-valid classification for one file.

        ``context.attachment``, when set, is an image or PDF of a media type
        listed in ``attachments``: send it along as the file's content.

        Implementations return data only. They never touch the file system and
        never decide paths. Raise ``ProviderError`` on failure; the classifier
        handles retries, rate limits and the ``failed`` status.
        """
        ...
