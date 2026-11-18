"""flib reprocess: filing the library again after the rules change (RF-37)."""

from __future__ import annotations

import errno
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import context, executor, fsops, pipeline
from file_librarian import reprocess as rp
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.executor import RunReport, recover
from file_librarian.finder_tags import read_tags
from file_librarian.fix import fix_file
from file_librarian.fsops import CrossVolumeError
from file_librarian.lock import instance_lock, reprocess_lock
from file_librarian.models import (
    Classification,
    FileContext,
    FileStatus,
    OperationStatus,
    Rules,
    RunMode,
    RunTrigger,
)
from file_librarian.providers.base import Usage
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.scanner import Candidate
from file_librarian.undo import undo_file, undo_run
from tests._helpers import (
    Crash,
    answer,
    context_for,
    fake_config,
    library,
    make_image,
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
    rename: bool = False,
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
            rename=rename,
            override_corrections=override_corrections,
        )
        planned = rp.plan_reprocess(selection.targets, provider, cfg, store)
        applied = rp.apply(planned, cfg, store, provider="fake", model="x")
    return planned, applied.report


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
    planned, _ = reprocess(cfg, provider)
    assert planned.moves() == []  # same category, and names are kept by default
    _, _, path = where(cfg, "scan.txt")
    assert path == lib / "Finance" / "scan.txt"
    # The description is refreshed even when nothing moves.
    assert row_for(cfg, "scan.txt").description == "fresh"

    cfg = config(downloads, naming={"pattern": "{subject}"})
    planned, _ = reprocess(cfg, provider)
    assert planned.moves() == []  # new naming rules rename nothing unless asked
    planned, _ = reprocess(cfg, provider, rename=True)
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

    def reading(candidate: Candidate, cf: Config, **kw: Any) -> FileContext:
        read.append(candidate.path)
        return real(candidate, cf, **kw)

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
    # Its category is kept and names are kept: nothing could change, so it is not sent.
    assert [c.path.name for c in provider.calls] == ["b.txt"]
    planned, _ = reprocess(cfg, provider, rename=True)  # a new name could come: it is sent
    assert where(cfg, "a.txt")[1] == "Finance"
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
    assert (lib / "Archive" / "a.txt").exists()  # names are kept by default
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


# --- review findings ------------------------------------------------------------------------


def docs_config(tmp_path: Path, downloads: Path, name: str) -> Path:
    return write_config(
        tmp_path,
        downloads,
        extra=f"categories: [{{name: Finance}}, {{name: Contracts}}, {{name: Other}}, "
        f"{{name: {name}}}]\n",
    )


def test_undo_after_a_case_only_rename_reverts_the_index_not_the_earlier_run(
    downloads: Path, tmp_path: Path
) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "docs")})
    lib = library(downloads)
    assert read_tags(lib / "docs" / "a.txt") == ["docs"]
    path = docs_config(tmp_path, downloads, "Docs")
    args = ["reprocess", "-c", str(path), "--rename-category", "docs", "Docs", "--yes"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stderr
    assert "index change(s). `flib undo` reverts it." in result.stdout
    assert where(cfg, "a.txt")[1] == "Docs"
    assert read_tags(lib / "docs" / "a.txt") == ["Docs"]  # the tag follows the new case
    [tagged] = rows(cfg, "SELECT finder_tag FROM operations WHERE finder_tag IS NOT NULL")
    assert tagged[0] == "Docs"

    result = runner.invoke(app, ["undo", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert "2 index change(s) reverted" in result.stdout
    # The earlier run that filed the file is not undone; the index is back.
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "docs", lib / "docs" / "a.txt")
    assert read_tags(lib / "docs" / "a.txt") == ["docs"]
    result = runner.invoke(app, ["undo", "-c", str(path)])
    assert result.exit_code == 1 and "nothing left to undo" in result.stderr
    assert (lib / "docs" / "a.txt").exists()


def test_a_reprocess_that_changed_nothing_is_not_undone_in_place_of_an_earlier_run(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = cli_config(tmp_path, downloads)
    cfg = fake_config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    lib = library(downloads)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(executor, "_move", refuse)
    result = runner.invoke(app, ["reprocess", "-c", str(path), "--yes"])
    assert result.exit_code == 0, result.stderr
    assert "1 failed, 0 skipped. Nothing changed." in result.stdout
    assert "flib undo" not in result.stdout.splitlines()[-2]
    result = runner.invoke(app, ["undo", "-c", str(path)])
    assert result.exit_code == 1
    assert "the newest reprocess (run 2) changed nothing" in result.stderr
    assert "--run ID" in result.stderr
    assert (lib / "Finance" / "a.txt").exists()


def test_a_crash_after_the_rename_in_the_index_is_undone(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "Other")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, lib / "Other" / "a.txt", category="Docs")
    cfg = config(downloads, [*CATEGORIES, {"name": "Documents"}])

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise Crash

    monkeypatch.setattr(rp, "relocate", crash)
    with Store.open(cfg.database) as store:
        planned = rp.plan_rename(cfg, store, "Docs", "Documents")
        with pytest.raises(Crash):
            rp.apply(planned, cfg, store)
    assert [tuple(r) for r in rows(cfg, "SELECT new_value FROM corrections")] == [("Documents",)]
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert report.reverted == 1
    assert [tuple(r) for r in rows(cfg, "SELECT new_value FROM corrections")] == [("Docs",)]
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Docs", lib / "Docs" / "a.txt")


def test_a_correction_is_respected_again_after_a_rename_is_undone(downloads: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, cfg, {"b.txt": (b"y", "Other")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, lib / "Other" / "b.txt", category="Docs")
    renamed = config(downloads, [*CATEGORIES, {"name": "Documents"}])
    with Store.open(cfg.database) as store:
        rp.apply(rp.plan_rename(renamed, store, "Docs", "Documents"), renamed, store)
        assert store.corrected_fields() == {1: {"category"}}
        undo_run(store, config=renamed)
        # Docs is back in the configuration too: the correction is in effect again.
        assert store.corrected_fields() == {1: {"category"}}
    provider = FakeProvider(default=answer("Finance"))
    reprocess(cfg, provider)
    assert where(cfg, "b.txt") == (FileStatus.ORGANIZED, "Docs", lib / "Docs" / "b.txt")
    assert provider.calls == []


def test_files_in_a_sub_folder_stay_unless_their_category_changes(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    lib = library(downloads)
    for name in ("a.txt", "b.txt"):
        put(lib / "Finance" / "2024", name, (lib / "Finance" / name).read_bytes())
        (lib / "Finance" / name).unlink()
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)
    provider = FakeProvider(
        {"a.txt": answer("Finance", "new name", 0.95), "b.txt": answer("Contracts", "z", 0.95)}
    )
    planned, _ = reprocess(cfg, provider, rename=True)
    assert where(cfg, "a.txt")[2] == lib / "Finance" / "2024" / "a.txt"
    # A new category: to its folder, the name the user kept.
    assert where(cfg, "b.txt") == (FileStatus.ORGANIZED, "Contracts", lib / "Contracts" / "b.txt")
    assert len(planned.moves()) == 1


def test_never_send_and_sensitive_names_apply_to_the_original_name(downloads: Path) -> None:
    # Filed while secret detection was off, so the key file got a new name too.
    cfg = config(downloads, naming={"pattern": "{subject}"}, sensitive={"detect_secrets": False})
    organize(
        downloads,
        cfg,
        {
            "bank-x.txt": (b"bank stuff", answer("Finance", "statement", 0.95)),
            "id_rsa-old.txt": (b"plain words", answer("Other", "notes", 0.95)),
        },
    )
    lib = library(downloads)
    assert (lib / "Finance" / "statement.txt").exists() and (lib / "Other" / "notes.txt").exists()
    cfg = config(downloads, naming={"pattern": "{subject}"}, never_send=["*bank*"])
    provider = FakeProvider(default=answer("Contracts", "statement", 0.95))
    planned, _ = reprocess(cfg, provider)
    assert [(c.path.name, c.text) for c in provider.calls] == [("statement.txt", None)]
    assert any(p.name == "notes.txt" and "sensitive" in why for p, why in planned.kept)
    assert where(cfg, "id_rsa-old.txt")[1] == "Other"


def test_undo_makes_a_deleted_category_folder_again(downloads: Path, tmp_path: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "Docs")})
    lib = library(downloads)
    path = docs_config(tmp_path, downloads, "Documents")
    args = ["reprocess", "-c", str(path), "--rename-category", "Docs", "Documents", "--yes"]
    result = runner.invoke(app, args)
    assert "Keep it until you are sure" in result.stdout
    (lib / "Docs").rmdir()
    renamed = config(downloads, [*CATEGORIES, {"name": "Documents"}])
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=renamed)
    assert report.skipped == []
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Docs", lib / "Docs" / "a.txt")


def test_prompts_happen_outside_the_lock_and_the_plan_is_checked_again(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = cli_config(tmp_path, downloads)
    cfg = fake_config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    lib = library(downloads)
    asked: list[str] = []

    def confirm(question: str, default: bool = False) -> bool:
        asked.append(question)
        with instance_lock(cfg.database):  # free: a scheduled run could start now
            pass
        if question.startswith("Move"):
            with Store.open(cfg.database) as store:  # the user fixes a file meanwhile
                fix_file(cfg, store, lib / "Finance" / "a.txt", category="Contracts")
        return True

    monkeypatch.setattr("file_librarian.cli.typer.confirm", confirm)
    result = runner.invoke(app, ["reprocess", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert [q.split()[0] for q in asked] == ["Send", "Move"]
    assert "changed in the index since the plan" in result.stdout
    assert where(cfg, "a.txt")[1] == "Contracts"  # the fix stands
    assert where(cfg, "b.txt")[1] == "Archive"


def test_a_file_changed_after_the_plan_is_skipped(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
        targets = rp.select(cfg, store).targets
        planned = rp.plan_reprocess(targets, FakeProvider(default=answer("Contracts")), cfg, store)
        os.utime(lib / "Finance" / "a.txt", (1, 1))
        applied = rp.apply(planned, cfg, store)
    [item] = applied.report.items
    assert (item.status, item.note) == (FileStatus.SKIPPED, "changed since it was checked")
    assert not applied.undoable
    assert where(cfg, "a.txt")[1] == "Finance"


def test_undo_gives_back_the_recorded_status_not_one_guessed_from_the_folder(
    downloads: Path,
) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", answer("Finance", confidence=0.2))})
    lib = library(downloads)
    cfg = config(downloads, [*CATEGORIES, {"name": "Manuals"}])
    reprocess(cfg, FakeProvider(default=answer("Manuals")))
    # The review folder is renamed in the configuration before the undo.
    renamed = config(downloads, [*CATEGORIES, {"name": "Manuals"}], review_folder="_Check")
    with Store.open(cfg.database) as store:
        undo_run(store, config=renamed)
    assert where(cfg, "a.txt") == (FileStatus.REVIEW, None, lib / "_Review" / "a.txt")


def test_undo_file_of_a_reprocess_move(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    lib = library(downloads)
    reprocess(cfg, FakeProvider(default=answer("Contracts")))
    with Store.open(cfg.database) as store:
        undo_file(store, lib / "Contracts" / "a.txt", config=cfg)
    assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Finance", lib / "Finance" / "a.txt")
    assert where(cfg, "b.txt")[1] == "Contracts"


def test_swapping_tags_ignores_case(downloads: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "docs")})
    lib = library(downloads)
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    with Store.open(cfg.database) as store:
        # Only the index is renamed here, not the tag: the file keeps "docs".
        run_id = store.start_run(RunMode.RUN, RunTrigger.REPROCESS)
        store.rename_category(run_id, "docs", "Docs", organized=True)
    reprocess(cfg, FakeProvider(default=answer("Contracts")))
    assert read_tags(lib / "Contracts" / "a.txt") == ["Contracts"]


def test_the_estimate_prices_images_by_their_size(downloads: Path) -> None:
    cfg = config(downloads, vision={"enabled": True})
    ctx = context_for(make_image(downloads / "p.jpg", 4000, 3000), cfg)
    assert ctx.visual == "image"
    assert rp.attachment_tokens(ctx, cfg) == 1568 * 1568 // 750
    pdf = replace(ctx, visual="pdf")
    assert rp.attachment_tokens(pdf, cfg) == 1568 * 1568 // 750 * 3


def test_only_files_inside_category_folders_are_selected(downloads: Path, tmp_path: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    lib = library(downloads)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (lib / "Finance" / "link").symlink_to(outside)
    (lib / "Finance" / "a.txt").rename(outside / "a.txt")
    (lib / "Finance" / "b.txt").rename(lib / "b.txt")
    with Store.open(cfg.database) as store:
        a, b = row_for(cfg, "a.txt"), row_for(cfg, "b.txt")
        store.relocate_file(a.id, lib / "Finance" / "link" / "a.txt", a.status, a.category)
        store.relocate_file(b.id, lib / "b.txt", b.status, b.category)
        assert rp.select(cfg, store) == rp.Selection()


@pytest.mark.parametrize(("crash_at", "finished"), [("_discard", True), ("_copyfile", False)])
def test_a_cross_volume_reprocess_move_is_recovered(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, crash_at: str, finished: bool
) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    lib = library(downloads)

    def exdev(src: Path, dst: Path) -> None:
        raise CrossVolumeError(errno.EXDEV, "cross-device link", str(dst))

    real = getattr(fsops, crash_at)

    def crash(*args: Any, **kwargs: Any) -> Any:
        if not finished:
            real(*args, **kwargs)
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(executor, "move_no_replace", exdev)
        patch.setattr(fsops, crash_at, crash)
        with pytest.raises(Crash):
            reprocess(cfg, FakeProvider(default=answer("Contracts")))
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    if finished:
        assert where(cfg, "a.txt") == (
            FileStatus.ORGANIZED,
            "Contracts",
            lib / "Contracts" / "a.txt",
        )
        assert read_tags(lib / "Contracts" / "a.txt") == ["Contracts"]
        assert not (lib / "Finance" / "a.txt").exists()
    else:
        assert where(cfg, "a.txt") == (FileStatus.ORGANIZED, "Finance", lib / "Finance" / "a.txt")
        assert not (lib / "Contracts" / "a.txt").exists()


def test_rename_cannot_go_with_a_migration(downloads: Path, tmp_path: Path) -> None:
    path = cli_config(tmp_path, downloads)
    result = runner.invoke(
        app, ["reprocess", "-c", str(path), "--remove-category", "Misc", "--rename"]
    )
    assert result.exit_code == 1 and "keeps file names" in result.stderr


# --- second review ---------------------------------------------------------------------------


def renamed_setup(downloads: Path) -> tuple[Path, Config, Config]:
    """a.txt filed in Docs, b.txt corrected into Docs; then Docs is renamed Documents."""
    before = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, before, {"a.txt": (b"x", "Docs"), "b.txt": (b"y", "Other")})
    lib = library(downloads)
    with Store.open(before.database) as store:
        fix_file(before, store, lib / "Other" / "b.txt", category="Docs")
    return lib, before, config(downloads, [*CATEGORIES, {"name": "Documents"}])


def corrected(cfg: Config) -> dict[int, set[str]]:
    with Store.open(cfg.database) as store:
        return store.corrected_fields()


def test_a_rename_run_again_after_a_crash_is_undone_as_a_whole(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib, before, after = renamed_setup(downloads)

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise Crash

    with Store.open(after.database) as store:
        planned = rp.plan_rename(after, store, "Docs", "Documents")
        with monkeypatch.context() as patch:
            patch.setattr(rp, "relocate", crash)
            with pytest.raises(Crash):
                rp.apply(planned, after, store)
        applied = rp.apply(rp.plan_rename(after, store, "Docs", "Documents"), after, store)
        assert applied.index_changes == 1  # the first run's change, adopted
        report = undo_run(store, config=after)
    assert (len(report.restored), report.reverted) == (2, 1)
    assert corrected(before) == {2: {"category"}}
    for name in ("a.txt", "b.txt"):
        assert where(before, name) == (FileStatus.ORGANIZED, "Docs", lib / "Docs" / name)


def test_undoing_one_file_of_a_rename_brings_its_correction_back(downloads: Path) -> None:
    lib, before, after = renamed_setup(downloads)
    with Store.open(after.database) as store:
        rp.apply(rp.plan_rename(after, store, "Docs", "Documents"), after, store)
        report = undo_file(store, lib / "Documents" / "b.txt", config=after)
    assert report.reverted == 1
    assert where(before, "b.txt") == (FileStatus.ORGANIZED, "Docs", lib / "Docs" / "b.txt")
    assert corrected(before) == {2: {"category"}}
    assert where(before, "a.txt")[1] == "Documents"  # the rest of the run stands


def test_a_rename_cut_off_mid_moves_is_recovered_and_undone(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib, before, after = renamed_setup(downloads)
    real = executor.finish_relocation
    calls = 0

    def second_crashes(*args: Any, **kwargs: Any) -> list[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Crash
        return real(*args, **kwargs)

    with Store.open(after.database) as store:
        planned = rp.plan_rename(after, store, "Docs", "Documents")
        with monkeypatch.context() as patch:
            patch.setattr(executor, "finish_relocation", second_crashes)
            with pytest.raises(Crash):
                rp.apply(planned, after, store)
        assert recover(store, after) == 1
        undo_run(store, config=after)
    for name in ("a.txt", "b.txt"):
        assert where(before, name) == (FileStatus.ORGANIZED, "Docs", lib / "Docs" / name)
    assert corrected(before) == {2: {"category"}}


def test_a_stale_plan_never_overwrites_and_respects_a_fix_made_meanwhile(
    downloads: Path,
) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
        targets = rp.select(cfg, store).targets
        planned = rp.plan_reprocess(targets, FakeProvider(default=answer("Contracts")), cfg, store)
        fix_file(cfg, store, lib / "Finance" / "a.txt", category="Other")
        put(lib / "Contracts", "b.txt", b"someone else")
        applied = rp.apply(planned, cfg, store)
    notes = {i.source.name: i.note for i in applied.report.items}
    assert notes["a.txt"] == "changed in the index since the plan"
    assert (lib / "Contracts" / "b.txt").read_bytes() == b"someone else"
    assert where(cfg, "b.txt")[2] == lib / "Contracts" / "b-2.txt"
    assert where(cfg, "a.txt")[1] == "Other"


def test_a_category_changed_between_plan_and_apply_skips_the_file(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
        targets = rp.select(cfg, store).targets
        planned = rp.plan_reprocess(targets, FakeProvider(default=answer("Contracts")), cfg, store)
        row = row_for(cfg, "a.txt")
        store.relocate_file(row.id, row.current_path, FileStatus.ORGANIZED, "Other")
        applied = rp.apply(planned, cfg, store)
    [item] = applied.report.items
    assert (item.status, item.note) == (FileStatus.SKIPPED, "changed in the index since the plan")
    assert where(cfg, "a.txt")[1] == "Other"


def test_undo_reverts_only_values_still_as_the_run_left_them(downloads: Path) -> None:
    cfg = config(downloads, [*CATEGORIES, {"name": "Docs"}])
    organize(downloads, cfg, {"a.txt": (b"x", "Other"), "b.txt": (b"y", "Other")})
    lib = library(downloads)
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, lib / "Other" / "a.txt", category="Docs")
        fix_file(cfg, store, lib / "Other" / "b.txt", category="Docs")
        fix_file(cfg, store, lib / "Docs" / "b.txt", category="Finance")
    after = config(downloads, [*CATEGORIES, {"name": "Documents"}])
    with Store.open(after.database) as store:
        rp.apply(rp.plan_rename(after, store, "Docs", "Documents"), after, store)
        # The user changes one renamed correction by hand meanwhile.
        store.conn.execute("UPDATE corrections SET new_value = 'Mine' WHERE id = 1")
        before = [tuple(r) for r in store.conn.execute("SELECT * FROM corrections WHERE id != 1")]
        undo_run(store, config=after)
        values = [
            tuple(r) for r in store.conn.execute("SELECT id, old_value, new_value FROM corrections")
        ]
    assert values[0] == (1, "Other", "Mine")  # changed since: left alone
    assert values[1] == (2, "Other", "Docs")  # renamed by the run: back
    assert values[2] == (3, "Docs", "Finance")  # the run's old_value change: back
    assert len(before) == 2


def test_undo_restores_the_description_of_a_file_that_did_not_move(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance"), "b.txt": (b"y", "Finance")})
    old = row_for(cfg, "a.txt")
    provider = FakeProvider(
        {
            "a.txt": answer("Finance", description="better", tags=["new"], confidence=0.9),
            "b.txt": answer("Contracts"),
        }
    )
    reprocess(cfg, provider)
    assert row_for(cfg, "a.txt").description == "better"
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    back = row_for(cfg, "a.txt")
    assert (back.description, back.tags, back.processed_at) == (
        old.description,
        old.tags,
        old.processed_at,
    )
    [conf] = rows(cfg, f"SELECT confidence FROM files WHERE id = {old.id}")  # noqa: S608
    assert conf[0] == pytest.approx(0.75)


def test_an_unchanged_answer_is_not_journaled(downloads: Path) -> None:
    cfg = config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    with Store.open(cfg.database) as store:
        targets = rp.select(cfg, store).targets  # the cached answer: the same as before
        planned = rp.plan_reprocess(targets, FakeProvider({}), cfg, store)
        applied = rp.apply(planned, cfg, store, provider="fake", model="x")
    assert planned.cached == 1 and not applied.undoable


def test_reprocess_and_reconcile_finish_an_interrupted_move_first(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = cli_config(tmp_path, downloads)
    cfg = fake_config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    lib = library(downloads)

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise Crash

    for command in ("reconcile", "reprocess"):
        with monkeypatch.context() as patch:
            patch.setattr(executor, "finish_relocation", crash)
            with pytest.raises(Crash):
                reprocess(cfg, FakeProvider(default=answer("Contracts")))
        result = runner.invoke(app, [command, "-c", str(path)], input="n\n")
        assert result.exit_code == 0, result.stderr
        assert "Recovered 1 move(s)" in result.stdout
        assert "run `flib reconcile`" not in result.stdout
        assert corrected(cfg) == {}  # flib's own move is not taken for the user's
        with Store.open(cfg.database) as store:
            undo_run(store, config=cfg)
        assert (lib / "Finance" / "a.txt").exists()


def test_only_one_reprocess_at_a_time(downloads: Path, tmp_path: Path) -> None:
    path = cli_config(tmp_path, downloads)
    cfg = fake_config(downloads)
    organize(downloads, cfg, {"a.txt": (b"x", "Finance")})
    with reprocess_lock(cfg.database):
        result = runner.invoke(app, ["reprocess", "-c", str(path), "--yes"])
        assert result.exit_code == 1 and "another flib reprocess" in result.stderr
        with instance_lock(cfg.database):  # a run is not held up by it
            pass
