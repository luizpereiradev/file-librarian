"""Regression tests for the adversarial safety review of the mover (M1).

Each test reproduces a scenario that used to misbehave.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from file_librarian import executor, pipeline
from file_librarian.config import ConfigError, parse_config
from file_librarian.db.store import Store
from file_librarian.fsops import move_no_replace
from file_librarian.models import Classification, RunTrigger
from file_librarian.providers.fake import FakeProvider
from file_librarian.scanner import SkipReason, scan
from file_librarian.undo import NothingToUndoError, undo_run
from tests._helpers import Crash, fake_config, make_old, put

pytestmark = pytest.mark.integration


def answer(
    category: str = "Finance", name: str = "doc", confidence: float = 0.95
) -> Classification:
    return Classification(
        category=category, suggested_name=name, description="d", confidence=confidence
    )


# 1. Path aliasing --------------------------------------------------------------------


def test_database_spelled_differently_inside_an_input_is_rejected(downloads: Path) -> None:
    lower = str(downloads.parent / "downloads" / "library.db")
    with pytest.raises(ConfigError, match="database must be outside"):
        fake_config(downloads, database=lower)


def test_input_through_a_symlink_is_compared_by_real_path(downloads: Path, tmp_path: Path) -> None:
    alias = tmp_path / "dl-alias"
    alias.symlink_to(downloads)
    with pytest.raises(ConfigError, match="database must be outside"):
        parse_config(
            {
                "inputs": [str(alias)],
                "database": str(downloads / "library.db"),
                "provider": {"name": "fake", "model": "x"},
            }
        )


def test_output_root_spelled_differently_is_still_managed(downloads: Path) -> None:
    config = fake_config(downloads, output_root=str(downloads.parent / "downloads" / "Organized"))
    put(downloads, "Organized/Other/x.pdf")
    make_old(downloads / "Organized")
    result = scan(config, lambda _s: None)
    assert result.candidates == []
    assert (downloads / "Organized", SkipReason.MANAGED) in result.skipped


def test_scanner_never_picks_up_flib_files_by_inode(downloads: Path) -> None:
    config = fake_config(downloads)
    db = put(downloads.parent / "elsewhere", "library.db")
    (downloads / "library.db").hardlink_to(db)  # same inode, different path
    config = fake_config(downloads, database=str(db))
    result = scan(config, lambda _s: None)
    assert result.candidates == []
    assert (downloads / "library.db", SkipReason.MANAGED) in result.skipped


# 2. One odd item must not abort the run -------------------------------------------------


@pytest.mark.parametrize("odd", [" ", "\u00a0\u00a0", " . "])
def test_names_that_sanitize_to_nothing_do_not_abort(downloads: Path, odd: str) -> None:
    put(downloads, odd, b"odd")
    put(downloads, "good.pdf", b"good")
    provider = FakeProvider({odd: answer("Taxes"), "good.pdf": answer(name="good")})
    outcome = pipeline.run(fake_config(downloads), provider, simulate=False)
    assert outcome.report is not None and outcome.report.moved == 2
    review = list((downloads / "Organized" / "_Review").iterdir())
    assert [p.name.startswith("unnamed-") for p in review] == [True]


def test_category_path_that_is_a_file_fails_one_item_only(downloads: Path) -> None:
    put(downloads, "Organized/Images", b"not a folder")
    put(downloads, "pic.png", b"png")
    put(downloads, "bill.pdf", b"pdf")
    provider = FakeProvider({"pic.png": answer("Images"), "bill.pdf": answer("Finance", "bill")})
    outcome = pipeline.run(fake_config(downloads), provider, simulate=False)
    assert outcome.report is not None
    statuses = {i.source.name: i.status.value for i in outcome.report.items}
    assert statuses == {"pic.png": "failed", "bill.pdf": "organized"}
    assert (downloads / "pic.png").exists()


# 3. Duplicates ---------------------------------------------------------------------


def test_same_content_twice_moves_one_and_leaves_the_other(downloads: Path) -> None:
    put(downloads, "x.pdf", b"same")
    put(downloads, "x (1).pdf", b"same")
    config = fake_config(downloads, duplicates="leave")
    outcome = pipeline.run(config, FakeProvider(default=answer()), simulate=False)
    assert outcome.report is not None and outcome.report.moved == 1
    [left] = [p for p in downloads.iterdir() if p.is_file()]
    assert (left, SkipReason.DUPLICATE) in outcome.scan.skipped


# 4. Recovery looks at the name actually used --------------------------------------------


def test_recover_after_crash_on_a_counter_name(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = fake_config(downloads)
    mine = put(downloads, "scan.pdf", b"mine")
    other = put(downloads, "Organized/Finance/scan.pdf", b"someone else's")

    def move_then_crash(src: Path, dst: Path) -> None:
        move_no_replace(src, dst)
        raise Crash

    with monkeypatch.context() as patch, pytest.raises(Crash):
        patch.setattr("file_librarian.executor.move_no_replace", move_then_crash)
        pipeline.run(config, FakeProvider(default=answer(confidence=0.75)), simulate=False)

    with Store.open(config.database) as store:
        assert executor.recover(store) == 1
        [op] = store.operations()
        assert (op.status.value, op.dst_path.name) == ("done", "scan-2.pdf")
        report = undo_run(store)
    assert [src for _, src in report.restored] == [mine]
    assert mine.read_bytes() == b"mine"
    assert other.read_bytes() == b"someone else's"


# 5. Folder units ---------------------------------------------------------------------------


def test_finder_metadata_does_not_block_undo_of_a_folder(downloads: Path) -> None:
    put(downloads, "Project/a.txt")
    make_old(downloads / "Project")
    config = fake_config(downloads)
    pipeline.run(config, FakeProvider(default=answer("Other")), simulate=False)
    moved = downloads / "Organized" / "Other" / "Project"
    (moved / ".DS_Store").write_bytes(b"finder")
    with Store.open(config.database) as store:
        report = undo_run(store)
    assert report.skipped == [] and (downloads / "Project" / "a.txt").exists()


def test_folder_changed_deep_inside_after_scan_is_not_moved(downloads: Path) -> None:
    deep = put(downloads, "Project/sub/a.txt", b"v1")
    for folder in ("Project/sub", "Project"):
        make_old(downloads / folder)
    config = fake_config(downloads)
    planned = pipeline.run(config, FakeProvider(default=answer("Other")), simulate=True)
    deep.write_bytes(b"v2 longer")
    make_old(downloads / "Project" / "sub")
    with Store.open(config.database) as store:
        report = executor.execute(
            planned.plan, config, store, trigger=RunTrigger.MANUAL, provider="f", model="m", seen=1
        )
    assert report.items[0].status.value == "skipped"
    assert (downloads / "Project").exists()


# 7-8. Scanner edge cases and identity ------------------------------------------------------


def test_empty_placeholders_hidden_flags_and_partial_siblings(downloads: Path) -> None:
    put(downloads, "empty.pdf", b"")
    put(downloads, "movie.mkv", b"placeholder")
    put(downloads, "movie.mkv.part", b"partial")
    flagged = put(downloads, "Icon-like", b"x")
    os.chflags(flagged, stat.UF_HIDDEN)
    result = scan(fake_config(downloads), lambda _s: None)
    reasons = {p.name: r for p, r in result.skipped}
    assert reasons["empty.pdf"] is SkipReason.EMPTY
    assert reasons["movie.mkv"] is SkipReason.IN_PROGRESS
    assert reasons["Icon-like"] is SkipReason.HIDDEN
    assert result.candidates == []


def test_replaced_file_with_same_size_and_mtime_is_not_moved(downloads: Path) -> None:
    path = put(downloads, "a.pdf", b"AAAA")
    config = fake_config(downloads)
    planned = pipeline.run(config, FakeProvider(default=answer()), simulate=True)
    path.unlink()
    put(downloads, "a.pdf", b"BBBB")  # new inode, same size and mtime
    with Store.open(config.database) as store:
        report = executor.execute(
            planned.plan, config, store, trigger=RunTrigger.MANUAL, provider="f", model="m", seen=1
        )
    assert report.items[0].status.value == "skipped"


def test_repeated_undo_does_not_unwind_older_runs(downloads: Path) -> None:
    config = fake_config(downloads)
    put(downloads, "first.pdf", b"1")
    pipeline.run(config, FakeProvider(default=answer(confidence=0.75)), simulate=False)
    put(downloads, "second.pdf", b"2")
    pipeline.run(config, FakeProvider(default=answer(confidence=0.75)), simulate=False)
    with Store.open(config.database) as store:
        undo_run(store)
        with pytest.raises(NothingToUndoError, match="--run"):
            undo_run(store)
    assert (downloads / "Organized" / "Finance" / "first.pdf").exists()


# Re-review --------------------------------------------------------------------------------


def test_recover_recognises_a_moved_file_edited_after_the_crash(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = fake_config(downloads)
    put(downloads, "notes.txt", b"original")

    def move_then_crash(src: Path, dst: Path) -> None:
        move_no_replace(src, dst)
        raise Crash

    with monkeypatch.context() as patch, pytest.raises(Crash):
        patch.setattr("file_librarian.executor.move_no_replace", move_then_crash)
        pipeline.run(config, FakeProvider(default=answer("Other", confidence=0.75)), simulate=False)
    moved = downloads / "Organized" / "Other" / "notes.txt"
    moved.write_bytes(b"original, then annotated")  # same inode, new content

    with Store.open(config.database) as store:
        assert executor.recover(store) == 1
        [op] = store.operations()
        row = store.file_by_id(op.file_id)
    assert op.status.value == "done"
    assert (row.status.value, row.current_path) == ("organized", moved)


@pytest.mark.parametrize("name", [" . .pdf", " ..txt", " .. .x"])
def test_names_never_become_hidden(downloads: Path, name: str) -> None:
    put(downloads, name, name.encode())
    pipeline.run(fake_config(downloads), FakeProvider(default=answer("Taxes")), simulate=False)
    [moved] = (downloads / "Organized" / "_Review").iterdir()
    assert not moved.name.startswith(".")


def test_plain_undo_skips_runs_whose_moves_all_failed(downloads: Path) -> None:
    config = fake_config(downloads)
    put(downloads, "first.pdf", b"1")
    pipeline.run(config, FakeProvider(default=answer(confidence=0.75)), simulate=False)
    put(downloads, "Organized/Images", b"not a folder")
    put(downloads, "pic.png", b"2")
    pipeline.run(config, FakeProvider(default=answer("Images")), simulate=False)
    with Store.open(config.database) as store:
        report = undo_run(store)
    assert [src.name for _, src in report.restored] == ["first.pdf"]
