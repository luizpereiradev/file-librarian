from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from file_librarian.classifier import classify_all
from file_librarian.config import parse_config
from file_librarian.models import CategoryRule, Classification, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, ProviderError, Usage

RULES = Rules(categories=(CategoryRule("Other"),), language="en")
ANSWER = Classification(category="Other", suggested_name="x", description="d", confidence=0.9)


def ctx(name: str, *, sensitive: bool = False) -> FileContext:
    return FileContext(
        path=Path(name), sha256=name, size_bytes=1, extension="", sensitive=sensitive
    )


class ScriptedProvider:
    name = "scripted"
    local = False
    attachments: frozenset[str] = frozenset()

    def __init__(self, script: list[Any], usage: Usage | None = None) -> None:
        self.script = script
        self.usage = usage or Usage(1_000_000, 0, ["claude-haiku-5-5"])  # US$ 0.10 per call
        self.calls: list[str] = []
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        return usage.input_tokens * 0.10 / 1_000_000  # Haiku input price

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.calls.append(context.path.name)
        self.last_usage = self.usage
        item = self.script.pop(0) if self.script else ANSWER
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def config(**provider: Any) -> Any:
    limits = provider.pop("limits", {})
    return parse_config(
        {"provider": {"name": "fake", "model": "claude-haiku-5-5"} | provider, "limits": limits}
    )


def run(contexts: list[FileContext], prov: ScriptedProvider, cfg: Any) -> tuple[Any, Clock]:
    clock = Clock()
    return classify_all(contexts, prov, RULES, cfg, sleep=clock.sleep, clock=clock), clock


def test_rate_limit_spaces_calls() -> None:
    prov = ScriptedProvider([])
    result, clock = run([ctx("a"), ctx("b"), ctx("c")], prov, config(requests_per_minute=30))
    assert [d.classification for d in result.decisions] == [ANSWER] * 3
    assert clock.sleeps == [2.0, 2.0]


def test_retryable_errors_back_off_then_succeed() -> None:
    flaky = ProviderError("overloaded", retryable=True)
    prov = ScriptedProvider([flaky, flaky, ANSWER])
    result, clock = run([ctx("a")], prov, config(requests_per_minute=600, max_retries=3))
    assert prov.calls == ["a", "a", "a"]
    assert result.decisions[0].classification == ANSWER
    assert 1.0 in clock.sleeps and 2.0 in clock.sleeps


def test_retries_are_bounded_and_permanent_errors_are_not_retried() -> None:
    flaky = ProviderError("overloaded", retryable=True)
    prov = ScriptedProvider([flaky] * 5 + [ProviderError("bad request")])
    result, _ = run([ctx("a"), ctx("b")], prov, config(requests_per_minute=600, max_retries=2))
    # a: 1 call + 2 retries, all overloaded. b: overloaded twice, then a permanent error.
    assert prov.calls == ["a", "a", "a", "b", "b", "b"]
    assert all(d.classification is None for d in result.decisions)
    assert "overloaded" in (result.decisions[0].error or "")
    assert "bad request" in (result.decisions[1].error or "")


def test_fatal_error_stops_everything() -> None:
    prov = ScriptedProvider([FatalProviderError("bad key")])
    with pytest.raises(FatalProviderError):
        run([ctx("a"), ctx("b")], prov, config())
    assert prov.calls == ["a"]


def test_sensitive_contexts_are_never_sent() -> None:
    prov = ScriptedProvider([])
    result, _ = run([ctx("secret", sensitive=True), ctx("ok")], prov, config())
    assert prov.calls == ["ok"]
    assert result.decisions[0].classification is None and result.decisions[0].error is None


def test_cost_cap_defers_the_rest() -> None:
    prov = ScriptedProvider([])
    cfg = config(limits={"max_cost_usd_per_run": 0.25})
    result, _ = run([ctx(n) for n in "abcde"], prov, cfg)
    assert prov.calls == ["a", "b", "c"]  # 0.10 + 0.10 < 0.25, third reaches 0.30
    assert [c.path.name for c in result.deferred] == ["d", "e"]
    assert result.cost_usd == pytest.approx(0.30)


def test_no_cap() -> None:
    prov = ScriptedProvider([])
    result, _ = run([ctx(n) for n in "abcde"], prov, config(limits={"max_cost_usd_per_run": None}))
    assert len(prov.calls) == 5 and result.deferred == []
