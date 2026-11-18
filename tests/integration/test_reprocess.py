"""flib reprocess: filing the library again after the rules change (RF-37)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import context, executor, pipeline
from file_librarian import reprocess as rp
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.executor import RunReport, recover
from file_librarian.finder_tags import read_tags
from file_librarian.fix import fix_file
from file_librarian.models import (
    Classification,
    FileContext,
    FileStatus,
    OperationStatus,
    Rules,
    RunTrigger,
)
from file_librarian.providers.base import Usage
from file_librarian.providers.fake import FakeProvider
from file_librarian.scanner import Candidate
from file_librarian.undo import undo_run
from tests._helpers import (
    Crash,
    answer,
    fake_config,
    library,
    make_old,
    mark_cloud_only,
    organize,
    put,
    row_for,
    rows,
    write_config,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

CATEGORIES = [{"name": "Finance"}, {"name": "Contracts"}, {"name": "Other"}]


def config(downloads: Path, categories: list[dict[str, Any]] | None = None, **kw: Any) -> Config:
    return fake_config(downloads, categories=categories or CATEGORIES, **kw)


def reprocess(
    cfg: Config,
    provider: FakeProvider,
    *,
    keep_names: bool = False,
    override_corrections: bool = False,
    removed: str | None = None,
    fresh: bool = True,
) -> tuple[rp.ReprocessPlan, RunReport]:
    """Reprocess with ``provider``. ``fresh``: forget cached answers first, so the
    provider's new answers count even where the rules did not change."""
    with Store.open(cfg.database) as store:
        if fresh:
            store.conn.execute("DELETE FROM classification_cache")
        selection = rp.select(
            cfg,
            store,
            removed=removed,
            keep_names=keep_names,
            override_corrections=override_corrections,
        )
        planned = rp.plan_reprocess(selection.targets, provider, cfg, store)
        report = rp.apply(planned, cfg, store, provider="fake", model="x")
    return planned, report


def where(cfg: Config, original: str) -> tuple[FileStatus, str | None, Path]:
    row = row_for(cfg, original)
    return row.status, row.category, row.current_path


def test_only_files_whose_answer_changed_move(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"tax form", "Finance"), "b.txt": (b"lease", "Contracts")})
    lib = library(downloads)
    # The rules change: a new category takes the tax documents.
    cfg = config(downloads, [*CATEGORIES, {"name": "Taxes", "hint": "tax forms"}])
    provider = FakeProvider({"a.txt": answer("Taxes"), "b.txt": answer("Contracts")})
    planned, report = reprocess(cfg, provider)

    assert [c.row.original_name for c in planned.moves()] == ["a.txt"]
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Taxes", lib / "Taxes" / "a.txt")
    # b.txt stays exactly where it was: its own name is not taken from it.
    assert where(cfg, "b.txt") == (FileStatus.ORGANIZED, "Contracts", lib / "Contracts" / "b.txt")
    assert sorted(p.name for p in (lib / "Contracts").iterdir()) == ["b.txt"]
    assert read_tags(lib / "Taxes" / "a.txt") == ["Taxes"]
    run = rows(cfg, "SELECT id, trigger, files_moved FROM runs ORDER BY id DESC LIMIT 1")[0]
    assert tuple(run) == (report.run_id, "reprocess", 1)
    ops = rows(cfg, "SELECT run_id, status FROM operations")
    assert [tuple(o) for o in ops if o[0] == report.run_id] == [(report.run_id, "done")]


def test_reprocess_renames_under_new_naming_rules_and_keeps_names_on_request(
    downloads: Path,
) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"scan.txt": (b"power bill", "Finance")})
    lib = library(downloads)
    sure = answer("Finance", "power bill", 0.95, description="fresh")
    provider = FakeProvider({"scan.txt": sure})
    planned, _ = reprocess(cfg, provider, keep_names=True)
    assert planned.moves() == []  # same category, and names are kept
    _, _, path = where(cfg, "scan.txt")
    assert path == lib / "Finance" / "scan.txt"
    # The description is refreshed even when nothing moves.
    assert row_for(cfg, "scan.txt").description == "fresh"

    cfg = config(downloads, naming={"pattern": "{subject}"})
    planned, _ = reprocess(cfg, provider)
    assert where(cfg, "scan.txt")[2] == lib / "Finance" / "power-bill.txt"


def test_unsure_answer_keeps_the_file_in_its_category(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    provider = FakeProvider({"a.txt": answer("Contracts", confidence=0.3)})
    planned, _ = reprocess(cfg, provider)
    assert planned.moves() == [] and "stays in Finance" in planned.kept[0][1]
    assert where(cfg, "a.txt")[1] == "Finance"


def test_review_files_find_a_new_category(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", answer("Finance", confidence=0.2))})
    assert where(cfg, "a.txt")[0] is FileStatus.REVIEW
    cfg = config(downloads, [*CATEGORIES, {"name": "Manuals"}])
    reprocess(cfg, FakeProvider({"a.txt": answer("Manuals")}))
    assert where(cfg, "a.txt") == (
        FileStatus.ORGANIZED,
        "Manuals",
        library(downloads) / "Manuals" / "a.txt",
    )


def test_failed_classification_and_sensitive_files_are_left_alone(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(downloads)
    organize(
        downloads,
        cfg,
        {"a.txt": (b"x", "Finance"), "id_rsa": (b"-----BEGIN OPENSSH PRIVATE KEY-----", "Other")},
    )
    assert where(cfg, "id_rsa")[0] is FileStatus.SENSITIVE
    read: list[Path] = []
    real = context.build_context_safely

    def reading(candidate: Candidate, cf: Config) -> FileContext:
        read.append(candidate.path)
        return real(candidate, cf)

    monkeypatch.setattr(rp, "build_context_safely", reading)
    provider = FakeProvider({})  # no answer: the classification fails
    planned, _ = reprocess(cfg, provider)
    assert [c.path.name for c in provider.calls] == ["a.txt"]
    assert all(p.name != "id_rsa" for p in read)  # a sensitive file is never read again
    assert "no fake answer" in planned.kept[0][1]
    assert where(cfg, "a.txt") == (
        FileStatus.ORGANIZED,
        "Finance",
        library(downloads) / "Finance" / "a.txt",
    )


def test_files_outside_the_library_and_changed_files_are_not_touched(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)  # both back in Downloads, status skipped
    organize(downloads, cfg, {"c.txt": (b"z", "Finance")})
    (library(downloads) / "Finance" / "c.txt").write_bytes(b"edited")
    provider = FakeProvider(default=answer("Other"))
    with Store.open(cfg.database) as store:
        selection = rp.select(cfg, store)
    assert selection.targets == []
    assert [(p.name, why) for p, why in selection.left] == [
        ("c.txt", "changed or moved since it was indexed; run `flib reconcile`")
    ]
    reprocess(cfg, provider)
    assert provider.calls == []
    assert (downloads / "a.txt").exists() and (downloads / "b.txt").exists()


# --- corrections ----------------------------------------------------------------------------


def test_a_corrected_category_is_kept(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"bill", "Other"), "b.txt": (b"lease", "Other")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, lib / "Other" / "a.txt", category="Finance")
    provider = FakeProvider(default=answer("Contracts"))
    planned, _ = reprocess(cfg, provider)
    assert where(cfg, "a.txt")[1] == "Finance"  # the user's choice stands
    assert where(cfg, "b.txt")[1] == "Contracts"
    assert any("your category kept" in c.relocation.reason for c in planned.changes)

    # Only an explicit flag overrides it.
    reprocess(cfg, provider, override_corrections=True)
    assert where(cfg, "a.txt")[1] == "Contracts"


def test_a_corrected_name_is_kept_and_both_corrected_is_not_sent(downloads: Path) -> None:
    cfg = config(downloads, naming={"pattern": "{subject}"})
    organize(downloads, cfg, {"a.txt": (b"bill", "Other"), "b.txt": (b"lease", "Other")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, lib / "Other" / "a.txt", name="my bill")
        fix_file(cfg, store, lib / "Other" / "b.txt", category="Contracts", name="my lease")
    provider = FakeProvider(default=answer("Finance", "something else", 0.95))
    reprocess(cfg, provider)
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Finance", lib / "Finance" / "my bill.txt")
    assert [c.path.name for c in provider.calls] == ["my bill.txt"]
    assert where(cfg, "b.txt")[2] == lib / "Contracts" / "my lease.txt"


# --- undo and crashes -----------------------------------------------------------------------


def test_undo_reverts_the_whole_reprocess(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    lib = library(downloads)
    reprocess(cfg, FakeProvider(default=answer("Contracts")))
    assert read_tags(lib / "Contracts" / "a.txt") == ["Contracts"]
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert len(report.restored) == 2
    for name in ("a.txt", "b.txt"):
        assert where(cfg, name) == (FileStatus.ORGANIZED, "Finance", lib / "Finance" / name)
        assert read_tags(lib / "Finance" / name) == ["Finance"]


def test_crash_after_the_move_is_finished_by_recovery(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    lib = library(downloads)

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(executor, "finish_relocation", crash)
        with pytest.raises(Crash):
            reprocess(cfg, FakeProvider(default=answer("Contracts")))
    assert (lib / "Contracts" / "a.txt").exists()
    assert where(cfg, "a.txt")[1] == "Finance"  # the index has not caught up yet
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Contracts", lib / "Contracts" / "a.txt")
    assert read_tags(lib / "Contracts" / "a.txt") == ["Contracts"]
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Finance", lib / "Finance" / "a.txt")


def test_crash_before_the_move_leaves_the_file_and_its_row(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    lib = library(downloads)

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(executor, "_move", crash)
        with pytest.raises(Crash):
            reprocess(cfg, FakeProvider(default=answer("Contracts")))
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
        statuses = [op.status for op in store.operations()]
    assert statuses[-1] is OperationStatus.FAILED
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Finance", lib / "Finance" / "a.txt")


def test_a_failed_move_changes_nothing(downloads: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(executor, "_move", refuse)
    _, report = reprocess(cfg, FakeProvider(default=answer("Contracts")))
    assert report.errors == 1
    assert where(cfg, "a.txt")[:2] == (FileStatus.ORGANIZED, "Finance")


# --- cost ----------------------------------------------------------------------------------


class Priced(FakeProvider):
    """A fake provider that bills US$ 0.40 per call."""

    def cost_usd(self, usage: Usage) -> float:
        return 0.4 if usage.input_tokens else 0.0

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.last_usage = Usage(input_tokens=1000, output_tokens=100)
        return super().classify(context, rules)


def test_the_cost_cap_stops_it_and_the_next_one_goes_on(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {f"{n}.txt": (n.encode(), "Finance") for n in "abcd"})
    rows(cfg, "DELETE FROM classification_cache")  # as if the rules had changed
    provider = Priced(default=answer("Contracts"))
    with Store.open(cfg.database) as store:
        targets = rp.select(cfg, store).targets
        before = rp.estimate(targets, provider, cfg, store)
    assert (before.files, before.cached, before.to_ask) == (4, 0, 4)
    assert before.cost_usd == pytest.approx(1.6)

    planned, _ = reprocess(cfg, provider, fresh=False)  # the cap is US$ 1.00: three calls reach it
    assert len(provider.calls) == 3 and planned.deferred == 1
    assert "cost limit reached" in planned.kept[-1][1]
    assert where(cfg, "d.txt")[1] == "Finance"

    provider = Priced(default=answer("Contracts"))
    with Store.open(cfg.database) as store:
        after = rp.estimate(rp.select(cfg, store).targets, provider, cfg, store)
    assert (after.cached, after.to_ask) == (3, 1)  # the cache answers the done ones
    reprocess(cfg, provider, fresh=False)
    assert len(provider.calls) == 1
    assert where(cfg, "d.txt")[1] == "Contracts"


# --- category migrations --------------------------------------------------------------------


def test_orphan_category_is_reported_and_blocks_plain_reprocess(
    downloads: Path, tmp_path: Path
) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "Docs")})
    path = write_config(
        tmp_path, downloads, extra="categories: [{name: Finance}, {name: Documents}]\n"
    )
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert result.exit_code == 0
    assert "'Docs'" in result.stderr and "--rename-category Docs NEW" in result.stderr
    assert "--remove-category Docs" in result.stderr
    result = runner.invoke(app, ["search", "-c", str(path), "--ext", "txt"])
    assert "--rename-category" in result.stderr  # every command says it
    result = runner.invoke(app, ["reprocess", "-c", str(path), "--yes"])
    assert result.exit_code == 1 and "--rename-category" in result.stderr


def test_rename_category_moves_files_index_tags_and_corrections(
    downloads: Path, tmp_path: Path
) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "Docs"), "b.txt": (b"y", "Other")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, lib / "Other" / "b.txt", category="Docs")
    put(lib / "Documents", "a.txt", b"already there")  # a name taken in the new folder
    path = write_config(
        tmp_path,
        downloads,
        extra="categories: [{name: Finance}, {name: Other}, {name: Documents}]\n",
    )
    result = runner.invoke(
        app, ["reprocess", "-c", str(path), "--rename-category", "Docs", "Documents"], input="n\n"
    )
    assert result.exit_code == 0, result.stderr
    assert "Move 2 file(s)?" in result.stdout and "Nothing changed." in result.stdout
    assert (lib / "Docs" / "a.txt").exists()

    result = runner.invoke(
        app, ["reprocess", "-c", str(path), "--rename-category", "docs", "documents", "--yes"]
    )
    assert result.exit_code == 0, result.stderr
    assert "empty now" in result.stdout
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Documents", lib / "Documents" / "a-2.txt")
    assert where(cfg, "b.txt")[1:] == ("Documents", lib / "Documents" / "b.txt")
    assert read_tags(lib / "Documents" / "b.txt") == ["Documents"]
    corrections = rows(cfg, "SELECT old_value, new_value FROM corrections")
    assert [tuple(c) for c in corrections] == [("Other", "Documents")]
    with Store.open(cfg.database) as store:
        assert len(store.current_corrections()) == 1  # still in effect
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert "missing from the configuration" not in result.stderr

    # Undone, the files are back in Docs and still organized there: the warning returns.
    result = runner.invoke(app, ["undo", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Docs", lib / "Docs" / "a.txt")
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert "--rename-category Docs NEW" in result.stderr


def test_rename_category_refusals(downloads: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "Docs")})
    with Store.open(cfg.database) as store:
        with pytest.raises(rp.ReprocessError, match="still a category"):
            rp.plan_rename(cfg, store, "Docs", "Finance")
        cfg = config(downloads)
        with pytest.raises(rp.ReprocessError, match="not a category in the configuration"):
            rp.plan_rename(cfg, store, "Docs", "Documents")
        with pytest.raises(rp.ReprocessError, match="nothing to migrate"):
            rp.plan_rename(cfg, store, "Nope", "Finance")


def test_rename_category_changing_only_the_case_renames_the_index(downloads: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "docs")})
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    with Store.open(cfg.database) as store:
        planned = rp.plan_rename(cfg, store, "docs", "Docs")
        assert planned.same_folder and planned.changes == []
        rp.apply(planned, cfg, store)
        assert rp.orphan_categories(cfg, store) == []
    assert where(cfg, "a.txt")[1] == "Docs"


def test_remove_category_reclassifies_its_files(downloads: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Misc"}])
    organize(
        downloads,
        cfg,
        {"a.txt": (b"x", "Misc"), "b.txt": (b"y", "Misc"), "c.txt": (b"z", "Other")},
    )
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, lib / "Misc" / "b.txt", category="Misc", name="mine")
    cfg = config(downloads)
    provider = FakeProvider(
        {"a.txt": answer("Finance"), "mine.txt": answer("Contracts", confidence=0.2)}
    )
    reprocess(cfg, provider, removed="misc")
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Finance", lib / "Finance" / "a.txt")
    # Unsure, and its category is gone: to review. Its corrected name stays.
    assert where(cfg, "b.txt") == (FileStatus.REVIEW, None, lib / "_Review" / "mine.txt")
    assert [c.path.name for c in provider.calls] == ["a.txt", "mine.txt"]
    with Store.open(cfg.database) as store:
        assert rp.orphan_categories(cfg, store) == []


def test_remove_category_to_review_asks_no_one(downloads: Path, tmp_path: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Misc"}])
    organize(downloads, cfg, {"a.txt": (b"x", "Misc")})
    lib = library(downloads)
    path = write_config(
        tmp_path,
        downloads,
        provider="{name: anthropic, model: x}",  # no key: it must not be needed
        extra="categories: [{name: Finance}, {name: Other}]\n",
    )
    result = runner.invoke(
        app, ["reprocess", "-c", str(path), "--remove-category", "Misc", "--to-review", "--yes"]
    )
    assert result.exit_code == 0, result.stderr
    assert where(cfg, "a.txt") == (FileStatus.REVIEW, None, lib / "_Review" / "a.txt")
    assert not read_tags(lib / "_Review" / "a.txt")
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Misc", lib / "Misc" / "a.txt")


def test_remove_category_refuses_a_configured_one(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    with Store.open(cfg.database) as store, pytest.raises(rp.ReprocessError, match="still"):
        rp.select(cfg, store, removed="Finance")


# --- the command line -----------------------------------------------------------------------


def cli_config(tmp_path: Path, downloads: Path) -> Path:
    """The configured fake provider files everything under the last category, Archive."""
    return write_config(
        tmp_path,
        downloads,
        extra="categories: [{name: Finance}, {name: Contracts}, {name: Other}, {name: Archive}]\n",
    )


def test_cli_asks_before_sending_and_before_moving(downloads: Path, tmp_path: Path) -> None:
    path = cli_config(tmp_path, downloads)
    cfg = fake_config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    lib = library(downloads)
    args = ["reprocess", "-c", str(path)]
    result = runner.invoke(app, args, input="n\n")
    assert result.exit_code == 0, result.stderr
    assert "1 to send to fake" in result.stdout and "Nothing sent" in result.stdout

    result = runner.invoke(app, [*args, "--simulate"], input="y\n")
    assert result.exit_code == 0, result.stderr
    assert "Finance/a.txt  ->  Archive/" in result.stdout and "Simulation" in result.stdout
    assert (lib / "Finance" / "a.txt").exists()

    result = runner.invoke(app, args, input="n\n")  # cached now: only the move is asked
    assert "1 answered by the cache, 0 to send" in result.stdout
    assert "Nothing changed." in result.stdout
    assert (lib / "Finance" / "a.txt").exists()

    result = runner.invoke(app, args, input="y\n")
    assert result.exit_code == 0, result.stderr
    assert "Reprocess run" in result.stdout
    assert [p.name.endswith("_a.txt") for p in (lib / "Archive").iterdir()] == [True]
    result = runner.invoke(app, [*args, "--yes"])
    assert "Nothing to move" in result.stdout
    result = runner.invoke(app, ["undo", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert (lib / "Finance" / "a.txt").exists()
    assert rows(cfg, "SELECT trigger FROM runs ORDER BY id DESC LIMIT 1")[0][0] == "reprocess"
    assert RunTrigger.REPROCESS.value == "reprocess"


def test_cli_filters_and_bad_combinations(downloads: Path, tmp_path: Path) -> None:
    path = cli_config(tmp_path, downloads)
    cfg = fake_config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.pdf": (b"y", "Contracts")})
    args = ["reprocess", "-c", str(path), "--yes"]
    result = runner.invoke(app, [*args, "--category", "contracts", "--ext", "pdf"])
    assert result.exit_code == 0, result.stderr
    assert "1 file(s) to reclassify" in result.stdout
    assert where(cfg, "a.txt")[1] == "Finance" and where(cfg, "b.pdf")[1] == "Archive"
    result = runner.invoke(app, [*args, "--since", "2999-01-01"])
    assert "Nothing to reprocess." in result.stdout
    result = runner.invoke(app, [*args, "--category", "nope"])
    assert result.exit_code == 1 and "unknown category" in result.stderr
    for bad, message in [
        (["--rename-category", "a", "b", "--remove-category", "c"], "not both"),
        (["--remove-category", "c", "--category", "Finance"], "leave out the filters"),
        (["--to-review"], "goes with --remove-category"),
    ]:
        result = runner.invoke(app, [*args, *bad])
        assert result.exit_code == 1 and message in result.stderr


def test_files_only_in_icloud_are_not_read(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(downloads)
    make_old(put(downloads / "photos", "one.txt", b"1").parent)
    put(downloads, "a.txt", b"x")
    put(downloads, "b.txt", b"y")
    pipeline.run(cfg, FakeProvider({"photos": answer("Other")}, answer()), simulate=False)
    lib = library(downloads)
    unit = lib / "Other" / "photos"
    assert where(cfg, "photos")[2] == unit
    mark_cloud_only(monkeypatch, lib / "Finance" / "a.txt", unit / "one.txt")
    with Store.open(cfg.database) as store:
        selection = rp.select(cfg, store)
    assert [t.row.original_name for t in selection.targets] == ["b.txt"]
    assert sorted((p.name, why.split(";")[0]) for p, why in selection.left) == [
        ("a.txt", "only in iCloud"),
        ("photos", "something inside is only in iCloud"),
    ]
