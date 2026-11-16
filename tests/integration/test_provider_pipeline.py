"""How provider failures and the cost cap affect a whole run."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from file_librarian import pipeline
from file_librarian.config import Config, parse_config
from file_librarian.db.store import Store
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, Usage
from tests._helpers import OLD

pytestmark = pytest.mark.integration


class PricedProvider:
    """Answers everything, billing US$ 0.10 per call (Haiku prices, 1M input tokens)."""

    name = "priced"
    local = True

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.last_usage = Usage()
        self.calls = 0

    def cost_usd(self, usage: Usage) -> float:
        return usage.input_tokens * 0.10 / 1_000_000  # Haiku input price

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.calls += 1
        self.last_usage = Usage(1_000_000, 0, ["claude-haiku-5-5"])
        if self.fail_with:
            raise self.fail_with
        return Classification(category="Other", suggested_name="x", description="d", confidence=0.9)


def setup(downloads: Path, n: int, **limits: object) -> Config:
    for i in range(n):
        path = downloads / f"f{i}.txt"
        path.write_text(f"file {i}", encoding="utf-8")
        os.utime(path, (OLD - i, OLD - i))
    return parse_config(
        {
            "inputs": [str(downloads)],
            "provider": {"name": "fake", "model": "claude-haiku-5-5"},
            "limits": limits,
        }
    )


def test_fatal_provider_error_moves_nothing_and_marks_nothing(downloads: Path) -> None:
    cfg = setup(downloads, 3)
    with pytest.raises(FatalProviderError):
        pipeline.run(cfg, PricedProvider(FatalProviderError("bad key")), simulate=False)
    assert sorted(p.name for p in downloads.iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]
    with Store.open(cfg.database) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


def test_cost_cap_defers_without_failing(downloads: Path) -> None:
    cfg = setup(downloads, 5, max_cost_usd_per_run=0.25)
    provider = PricedProvider()
    outcome = pipeline.run(cfg, provider, simulate=False)
    assert provider.calls == 3
    assert outcome.deferred == 2
    assert outcome.cost_usd == pytest.approx(0.30)
    left = sorted(p.name for p in downloads.iterdir() if p.is_file())
    assert left == ["f0.txt", "f1.txt"]  # newest two wait for the next run
    with Store.open(cfg.database) as store:
        statuses = [r[0] for r in store.conn.execute("SELECT status FROM files")]
    assert statuses == ["organized"] * 3
    # Next run picks up the rest.
    pipeline.run(cfg, PricedProvider(), simulate=False)
    assert not [p for p in downloads.iterdir() if p.is_file()]


def test_run_after_simulate_reuses_the_classifications(downloads: Path) -> None:
    cfg = setup(downloads, 3)
    provider = PricedProvider()
    first = pipeline.run(cfg, provider, simulate=True)
    assert provider.calls == 3 and first.cost_usd > 0
    second = pipeline.run(cfg, provider, simulate=False)
    assert provider.calls == 3  # nothing sent again
    assert second.cached == 3 and second.cost_usd == 0
    assert second.report is not None and second.report.moved == 3


def test_changing_the_rules_invalidates_the_cache(downloads: Path) -> None:
    cfg = setup(downloads, 2)
    provider = PricedProvider()
    pipeline.run(cfg, provider, simulate=True)
    changed = cfg.model_copy(
        update={"categories": [c.model_copy(update={"hint": "new hint"}) for c in cfg.categories]}
    )
    pipeline.run(changed, provider, simulate=True)
    assert provider.calls == 4


def test_old_or_broken_cache_entries_are_not_used(downloads: Path) -> None:
    cfg = setup(downloads, 2)
    provider = PricedProvider()
    pipeline.run(cfg, provider, simulate=True)
    with Store.open(cfg.database) as store:
        rows = store.conn.execute(
            "SELECT sha256 FROM classification_cache ORDER BY sha256"
        ).fetchall()
        store.conn.execute(
            "UPDATE classification_cache SET created_at = '2000-01-01T00:00:00+00:00' "
            "WHERE sha256 = ?",
            (rows[0][0],),
        )
        store.conn.execute(
            "UPDATE classification_cache SET answer = '{not json' WHERE sha256 = ?", (rows[1][0],)
        )
    outcome = pipeline.run(cfg, provider, simulate=True)
    assert provider.calls == 4 and outcome.cached == 0
