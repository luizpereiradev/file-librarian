"""A deterministic, offline provider for tests and local development.

It never touches the network. Answers come from a fixed table keyed by file
name; anything not in the table gets ``default``. With ``echo=True`` (what
``provider.name: fake`` in a configuration uses) every file goes to the last
configured category under its own name, which lets you try ``flib simulate``
and ``flib run`` end to end without an API key.
"""

from __future__ import annotations

from collections.abc import Mapping

from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import ProviderError


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        answers: Mapping[str, Classification] | None = None,
        default: Classification | None = None,
        *,
        echo: bool = False,
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self._echo = echo
        self.calls: list[FileContext] = []

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.calls.append(context)
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
