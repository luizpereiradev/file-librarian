"""Why a file is in review, what each run cost, and when a move was undone (#68)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian import reprocess as rp
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.fix import fix_file
from file_librarian.models import (
    Classification,
    FileContext,
    FileStatus,
    ReviewReason,
    Rules,
    explain_review,
)
from file_librarian.providers.base import Usage
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.runlog import logs_dir
from file_librarian.undo import undo_file, undo_run
from tests._helpers import answer, fake_config, library, organize, put, row_for, write_config

pytestmark = pytest.mark.integration

runner = CliRunner()

LOW = ReviewReason.LOW_CONFIDENCE
UNKNOWN = ReviewReason.UNKNOWN_CATEGORY


class BilledProvider:
    """Answers ``Other`` for everything; each call bills 1M input tokens.

    With ``price`` it costs that much per call; without, its prices are unknown.
    """

    name = "billed"
    local = True
    attachments: frozenset[str] = frozenset()

    def __init__(self, price: float | None) -> None:
        self.price = price
        self.prices_known = price is not None
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        return usage.input_tokens * (self.price or 0.0) / 1_000_000

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.last_usage = Usage(1_000_000, 0, ["m"])
        return answer("Other")


def review_of(cfg: Config, original: str) -> tuple[FileStatus, ReviewReason | None, str | None]:
    row = row_for(cfg, original)
    return row.status, row.review_reason, row.review_category


def reprocess(cfg: Config, provider: FakeProvider) -> rp.Applied:
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
        selection = rp.select(cfg, store)
        planned = rp.plan_reprocess(selection.targets, provider, cfg, store)
        return rp.apply(planned, cfg, store, provider="fake", model="x")


def last_cost(cfg: Config) -> float | None:
    with Store.open(cfg.database) as store:
        cost: float | None = store.conn.execute(
            "SELECT cost_usd FROM runs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
    return cost


# --- review reasons ----------------------------------------------------------------------


def test_each_planner_reason_is_stored_with_the_suggested_category(
    downloads: Path, cfg: Config
) -> None:
    organize(
        downloads,
        cfg,
        {
            "unsure.txt": (b"who knows", answer("Finance", confidence=0.2)),
            "odd.txt": (b"odd", answer("Recipes", confidence=0.9)),
            "bill.txt": (b"electricity bill", answer("Finance")),
        },
    )
    assert review_of(cfg, "unsure.txt") == (FileStatus.REVIEW, LOW, "Finance")
    assert review_of(cfg, "odd.txt") == (FileStatus.REVIEW, UNKNOWN, "Recipes")
    assert review_of(cfg, "bill.txt") == (FileStatus.ORGANIZED, None, None)


def test_run_and_simulate_say_why_an_item_goes_to_review(downloads: Path, tmp_path: Path) -> None:
    put(downloads, "notes.txt", b"some notes")
    config = write_config(tmp_path, downloads)  # the echo provider: confidence 0.9
    stricter = write_config(tmp_path, downloads, extra="limits: {confidence_threshold: 0.95}\n")
    simulated = runner.invoke(app, ["simulate", "-c", str(stricter)])
    assert simulated.exit_code == 0, simulated.stderr
    assert "_Review/notes.txt  (confidence 0.90 < 0.95)" in simulated.stdout
    result = runner.invoke(app, ["run", "-c", str(stricter)])
    assert result.exit_code == 0, result.stderr
    assert "_Review/notes.txt  (review: confidence 0.90 < 0.95)" in result.stdout
    cfg = fake_config(downloads)
    assert review_of(cfg, "notes.txt") == (FileStatus.REVIEW, LOW, "Other")

    inspected = runner.invoke(
        app, ["inspect", "-c", str(config), str(library(downloads) / "_Review" / "notes.txt")]
    )
    assert inspected.exit_code == 0, inspected.stderr
    assert "  index:  review\n" in inspected.stdout
    assert (
        "  review: the AI was not sure enough (confidence 0.90); it suggested Other\n"
        in inspected.stdout
    )
    as_json = runner.invoke(
        app,
        ["inspect", "--json", "-c", str(config), str(library(downloads) / "_Review" / "notes.txt")],
    )
    [item] = json.loads(as_json.stdout)
    index = item["index"]
    assert (index["review_reason"], index["review_category"]) == ("low_confidence", "Other")
    assert index["review"].startswith("the AI was not sure enough")


def test_a_file_fixed_out_of_review_loses_its_reason_and_undo_gives_it_back(
    downloads: Path, cfg: Config
) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Finance", confidence=0.2))})
    organize(downloads, cfg, {"b.txt": (b"b", answer("Recipes", confidence=0.9))})
    review = library(downloads) / "_Review"
    with Store.open(cfg.database) as store:
        fixed = fix_file(cfg, store, review / "a.txt", category="Finance")
        fix_file(cfg, store, review / "b.txt", category="Other")
    assert review_of(cfg, "a.txt") == (FileStatus.ORGANIZED, None, None)
    assert review_of(cfg, "b.txt") == (FileStatus.ORGANIZED, None, None)
    assert last_cost(cfg) == 0.0  # a fix sends nothing
    with Store.open(cfg.database) as store:
        undo_run(store, fixed.run_id, config=cfg)
        undo_file(store, library(downloads) / "Other" / "b.txt", config=cfg)
    assert review_of(cfg, "a.txt") == (FileStatus.REVIEW, LOW, "Finance")
    assert review_of(cfg, "b.txt") == (FileStatus.REVIEW, UNKNOWN, "Recipes")


def test_a_rename_in_review_keeps_the_reason(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Finance", confidence=0.2))})
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, library(downloads) / "_Review" / "a.txt", name="renamed")
    assert review_of(cfg, "a.txt") == (FileStatus.REVIEW, LOW, "Finance")


def test_reprocess_keeps_the_reason_consistent_and_undo_restores_it(
    downloads: Path, cfg: Config
) -> None:
    organize(
        downloads,
        cfg,
        {
            "leaves.txt": (b"x", answer("Finance", confidence=0.2)),
            "stays.txt": (b"y", answer("Recipes", confidence=0.9)),
        },
    )
    # leaves.txt finds a category; stays.txt stays in review for another reason.
    reprocess(
        cfg,
        FakeProvider(
            {"leaves.txt": answer("Finance"), "stays.txt": answer("Contracts", confidence=0.3)}
        ),
    )
    assert review_of(cfg, "leaves.txt") == (FileStatus.ORGANIZED, None, None)
    assert review_of(cfg, "stays.txt") == (FileStatus.REVIEW, LOW, "Contracts")
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert review_of(cfg, "leaves.txt") == (FileStatus.REVIEW, LOW, "Finance")
    assert review_of(cfg, "stays.txt") == (FileStatus.REVIEW, UNKNOWN, "Recipes")


def test_a_removed_category_sends_files_to_review_with_that_reason(downloads: Path) -> None:
    categories = [{"name": "Finance"}, {"name": "Misc"}]
    cfg = fake_config(downloads, categories=categories)
    organize(downloads, cfg, {"a.txt": (b"x", "Misc")})
    cfg = fake_config(downloads, categories=categories[:1])
    with Store.open(cfg.database) as store:
        selection = rp.select(cfg, store, removed="Misc")
        rp.apply(rp.plan_to_review(selection.targets, cfg), cfg, store)
    assert review_of(cfg, "a.txt") == (FileStatus.REVIEW, ReviewReason.CATEGORY_REMOVED, None)
    assert last_cost(cfg) == 0.0  # nothing was sent
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert review_of(cfg, "a.txt") == (FileStatus.ORGANIZED, None, None)


def test_reconcile_marks_a_file_moved_to_review_by_hand(downloads: Path, cfg: Config) -> None:
    organize(
        downloads,
        cfg,
        {"a.txt": (b"a", "Finance"), "b.txt": (b"b", answer("Finance", confidence=0.2))},
    )
    lib = library(downloads)
    shutil.move(lib / "Finance" / "a.txt", lib / "_Review" / "a.txt")
    (lib / "_Review" / "deeper").mkdir()
    shutil.move(lib / "_Review" / "b.txt", lib / "_Review" / "deeper" / "b.txt")
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)
    assert review_of(cfg, "a.txt") == (FileStatus.REVIEW, ReviewReason.MOVED_BY_HAND, None)
    assert review_of(cfg, "b.txt") == (FileStatus.REVIEW, LOW, "Finance")  # still in review
    # Taken out of review by hand, it loses the reason.
    shutil.move(lib / "_Review" / "deeper" / "b.txt", lib / "Finance" / "b.txt")
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)
    assert review_of(cfg, "b.txt") == (FileStatus.ORGANIZED, None, None)


def test_undoing_a_run_clears_the_reason(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Finance", confidence=0.2))})
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert review_of(cfg, "a.txt") == (FileStatus.SKIPPED, None, None)


def test_every_reason_reads_in_words() -> None:
    assert "0.20" in explain_review(LOW, "Finance", 0.2)
    assert explain_review(LOW, None, None) == "the AI was not sure enough"
    assert explain_review(UNKNOWN, "Recipes", 0.9).endswith(": Recipes")
    assert "removed" in explain_review(ReviewReason.CATEGORY_REMOVED, None, None)
    assert "outside flib" in explain_review(ReviewReason.MOVED_BY_HAND, None, None)
    assert explain_review(None, None, None).startswith("unknown")


# --- run cost ------------------------------------------------------------------------------


def test_a_run_records_its_cost(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.txt", b"a")
    put(downloads, "b.txt", b"b")
    outcome = pipeline.run(cfg, BilledProvider(0.25), simulate=False)
    assert outcome.cost_known and outcome.cost_usd == pytest.approx(0.5)
    assert last_cost(cfg) == pytest.approx(0.5)
    # Nothing new: a run that sent nothing cost nothing.
    pipeline.run(cfg, BilledProvider(0.25), simulate=False)
    assert last_cost(cfg) == 0.0


def test_unknown_prices_record_no_cost(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.txt", b"a")
    outcome = pipeline.run(cfg, BilledProvider(None), simulate=False)
    assert not outcome.cost_known
    assert last_cost(cfg) is None


def test_the_cli_and_the_log_say_the_cost_is_unknown(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "a.txt", b"a")
    config = write_config(tmp_path, downloads)
    monkeypatch.setattr("file_librarian.cli.get_provider", lambda _: BilledProvider(None))
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.stderr
    assert "Provider cost: unknown" in result.stdout
    [log] = logs_dir(fake_config(downloads).database).glob("run-*.log")
    assert "Provider cost: unknown" in log.read_text(encoding="utf-8")

    put(downloads, "b.txt", b"b")
    monkeypatch.setattr("file_librarian.cli.get_provider", lambda _: BilledProvider(0.5))
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert "Estimated provider cost: US$ 0.5000" in result.stdout
    newest = max(logs_dir(fake_config(downloads).database).glob("run-*.log"))
    assert "Estimated provider cost: US$ 0.5000" in newest.read_text(encoding="utf-8")


def test_a_reprocess_records_its_cost(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", "Finance")})
    with Store.open(cfg.database) as store:
        selection = rp.select(cfg, store)
        planned = rp.plan_reprocess(selection.targets, BilledProvider(0.125), cfg, store)
        rp.apply(planned, cfg, store, provider="billed", model="m")
    assert last_cost(cfg) == pytest.approx(0.125)
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
        selection = rp.select(cfg, store)
        planned = rp.plan_reprocess(selection.targets, BilledProvider(None), cfg, store)
        rp.apply(planned, cfg, store, provider="billed", model="m")
    assert last_cost(cfg) is None


# --- undo time -----------------------------------------------------------------------------


def test_undo_records_when(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", "Finance"), "b.txt": (b"b", "Finance")})
    organize(downloads, cfg, {"c.txt": (b"c", "Other")})
    with Store.open(cfg.database) as store:
        undo_file(store, library(downloads) / "Finance" / "a.txt", config=cfg)
        undo_run(store, config=cfg)  # the run of c.txt
        undone = {store.file_by_id(o.file_id).original_name: o for o in store.operations()}
    assert undone["a.txt"].undone_at is not None
    assert undone["c.txt"].undone_at is not None
    assert undone["b.txt"].undone_at is None  # never undone
    assert undone["a.txt"].undone_at <= undone["c.txt"].undone_at
