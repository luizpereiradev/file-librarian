"""A deterministic, offline provider for tests and local development.

It never touches the network. Answers come from a fixed table keyed by file
name; anything not in the table gets ``default``.
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
    ) -> None:
        self._answers = dict(answers or {})
        self._default = default
        self.calls: list[FileContext] = []

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.calls.append(context)
        answer = self._answers.get(context.path.name, self._default)
        if answer is None:
            raise ProviderError(f"no fake answer for {context.path.name}")
        return answer
