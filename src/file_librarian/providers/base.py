"""The interface every AI provider implements.

Adding a provider must not require changes outside ``providers/``: write a
module with a class satisfying ``Provider`` and register it in
``providers/__init__.py``. See docs/adding-a-provider.md.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from file_librarian.models import Classification, FileContext, Rules


class ProviderError(Exception):
    """The provider failed to produce a usable answer for one file."""


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
