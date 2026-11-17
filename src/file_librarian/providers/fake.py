"""A deterministic, offline provider for tests and local development.

It never touches the network. Answers come from a fixed table keyed by file
name; anything not in the table gets ``default``. With ``echo=True`` (what
``provider.name: fake`` in a configuration uses) every file goes to the last
configured category under its own name, which lets you try ``flib simulate``
and ``flib run`` end to end without an API key. It reads no attachments unless
``attachments`` says so; ``calls`` keeps every context, attachment included.
"""

from __future__ import annotations

from collections.abc import Mapping

from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import ProviderError, Usage


class FakeProvider:
    name = "fake"
    local = True  # no API behind it: no rate limit

    def __init__(
        self,
        answers: Mapping[str, Classification] | None = None,
        default: Classification | None = None,
        *,
        echo: bool = False,
        attachments: frozenset[str] = frozenset(),
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self._echo = echo
        self.attachments = attachments
        """Media types it pretends to read; empty (text only) unless a test sets them."""
        self.calls: list[FileContext] = []
        self.rules: list[Rules] = []
        """The rules each call received, examples included, for tests to inspect."""
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        return 0.0

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.calls.append(context)
        self.rules.append(rules)
        answer = self._answers.get(context.path.name, self._default)
        if answer is None and self._echo:
            return Classification(
                category=rules.categories[-1].name,
                suggested_name=context.path.stem or context.path.name,
                description=f"Offline test classification of {context.path.name}.",
                confidence=0.9,
            )
        if answer is None:
            raise ProviderError(f"no fake answer for {context.path.name}")
        return answer
