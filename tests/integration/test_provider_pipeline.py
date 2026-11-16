"""How provider failures and the cost cap affect a whole run."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from file_librarian import pipeline
from file_librarian.config import parse_config
from file_librarian.db.store import Store
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import FatalProviderError, Usage

pytestmark = pytest.mark.integration
OLD = time.time() - 3600


class PricedProvider:
    """Answers everything, billing US$ 0.10 per call (Haiku prices, 1M input tokens)."""

    name = "priced"
    local = True

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.fail_with = fail_with
        self.last_usage = Usage()
        self.calls = 0

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.calls += 1
        self.last_usage = Usage(1_000_000, 0, ["claude-haiku-5-5"])
        if self.fail_with:
            raise self.fail_with
        return Classification(category="Other", suggested_name="x", description="d", confidence=0.9)


def setup(downloads: Path, n: int, **limits: object) -> object:
    for i in range(n):
        path = downloads / f"f{i}.txt"
        path.write_text(f"file {i}", encoding="utf-8")
        os.utime(path, (OLD - i, OLD - i))
    return parse_config(
        {"inputs": [str(downloads)], "provider": {"name": "fake", "model": "claude-haiku-5-5"},
         "limits": limits}
    )  # fmt: skip


def test_fatal_provider_error_moves_nothing_and_marks_nothing(downloads: Path) -> None:
    cfg = setup(downloads, 3)
    with pytest.raises(FatalProviderError):
        pipeline.run(cfg, PricedProvider(FatalProviderError("bad key")), simulate=False)  # type: ignore[arg-type]
    assert sorted(p.name for p in downloads.iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]
    with Store.open(cfg.database) as store:  # type: ignore[attr-defined]
        assert store.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


def test_cost_cap_defers_without_failing(downloads: Path) -> None:
    cfg = setup(downloads, 5, max_cost_usd_per_run=0.25)
    provider = PricedProvider()
    outcome = pipeline.run(cfg, provider, simulate=False)  # type: ignore[arg-type]
    assert provider.calls == 3
    assert outcome.deferred == 2
    assert outcome.cost_usd == pytest.approx(0.30)
    left = sorted(p.name for p in downloads.iterdir() if p.is_file())
    assert left == ["f0.txt", "f1.txt"]  # newest two wait for the next run
    with Store.open(cfg.database) as store:  # type: ignore[attr-defined]
        statuses = [r[0] for r in store.conn.execute("SELECT status FROM files")]
    assert statuses == ["organized"] * 3
    # Next run picks up the rest.
    pipeline.run(cfg, PricedProvider(), simulate=False)  # type: ignore[arg-type]
    assert not [p for p in downloads.iterdir() if p.is_file()]
