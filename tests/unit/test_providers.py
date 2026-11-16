from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from file_librarian.config import ProviderConfig
from file_librarian.models import CategoryRule, Classification, FileContext, Rules
from file_librarian.providers import Provider, ProviderError, get_provider
from file_librarian.providers.fake import FakeProvider

RULES = Rules(categories=(CategoryRule("Finance"), CategoryRule("Other")), language="en")
ANSWER = Classification(
    category="Finance",
    suggested_name="electricity-invoice-october",
    description="Electricity invoice for October 2026.",
    tags=["invoice", "electricity"],
    confidence=0.93,
)


def context(name: str) -> FileContext:
    return FileContext(path=Path("/in") / name, sha256="0" * 64, size_bytes=10, extension=".pdf")


def test_fake_provider_satisfies_interface() -> None:
    provider = FakeProvider({"a.pdf": ANSWER})
    assert isinstance(provider, Provider)
    assert provider.classify(context("a.pdf"), RULES) == ANSWER
    with pytest.raises(ProviderError):
        provider.classify(context("b.pdf"), RULES)


def test_registry() -> None:
    assert get_provider(ProviderConfig(name="fake", model="x")).name == "fake"
    assert get_provider(ProviderConfig(name="anthropic", model="x")).name == "anthropic"
    with pytest.raises(ProviderError, match="planned for M2"):
        get_provider(ProviderConfig(name="claude-code", model="x"))
    with pytest.raises(ValidationError, match="unknown provider"):
        ProviderConfig(name="nope", model="x")


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence": 1.2},
        {"category": ""},
        {"tags": ["x"] * 11},
        {"path": "/etc/passwd"},  # extra keys are rejected, the model only returns data
    ],
)
def test_classification_schema_rejects_bad_answers(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Classification.model_validate(ANSWER.model_dump() | bad)
