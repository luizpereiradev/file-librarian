"""flib reconcile: the index follows files the user moved, renamed or deleted (RF-20)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.models import FileStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import ReconcileReport, reconcile
from tests._helpers import answer, library, make_old, mark_cloud_only, organize, row_for

pytestmark = pytest.mark.integration


def run(cfg: Config) -> ReconcileReport:
    with Store.open(cfg.database) as store:
        return reconcile(cfg, store)


def test_untouched_files_are_left_alone(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance")})
    report = run(cfg)
    assert report.checked == 1 and not (report.moved or report.missing)


def test_moved_to_another_category_is_learned(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.pdf": (b"bill", "Other")})
    (library(downloads) / "Finance").mkdir()
    shutil.move(
        library(downloads) / "Other" / "bill.pdf", library(downloads) / "Finance" / "bill.pdf"
    )
    report = run(cfg)
    row = row_for(cfg, "bill.pdf")
    assert row.current_path == library(downloads) / "Finance" / "bill.pdf"
    assert (row.status, row.category) == (FileStatus.ORGANIZED, "Finance")
    assert report.recategorized[0][1:] == ("Other", "Finance")
    with Store.open(cfg.database) as store:
        rows = store.conn.execute("SELECT field, old_value, new_value FROM corrections")
        assert [tuple(r) for r in rows] == [("category", "Other", "Finance")]


def test_renamed_in_place_records_the_name(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"scan.pdf": (b"scan", "Finance")})
    folder = library(downloads) / "Finance"
    (folder / "scan.pdf").rename(folder / "electricity-october.pdf")
    run(cfg)
    row = row_for(cfg, "scan.pdf")
    assert row.current_name == "electricity-october.pdf"
    with Store.open(cfg.database) as store:
        rows = store.conn.execute("SELECT field, new_value FROM corrections")
        assert [tuple(r) for r in rows] == [("name", "electricity-october.pdf")]


def test_moved_to_review_or_back_to_downloads(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance"), "b.pdf": (b"b", "Finance")})
    (library(downloads) / "_Review").mkdir()
    shutil.move(library(downloads) / "Finance" / "a.pdf", library(downloads) / "_Review" / "a.pdf")
    shutil.move(library(downloads) / "Finance" / "b.pdf", downloads / "b.pdf")
    run(cfg)
    assert row_for(cfg, "a.pdf").status is FileStatus.REVIEW
    assert row_for(cfg, "b.pdf").status is FileStatus.SKIPPED
    # Back in Downloads and known: the next run leaves it there.
    outcome = pipeline.run(cfg, FakeProvider(default=answer("Finance")), simulate=False)
    assert outcome.plan.items == [] and (downloads / "b.pdf").exists()


def test_deleted_becomes_missing_and_can_be_found_again(
    downloads: Path, cfg: Config, tmp_path: Path
) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance")})
    shutil.move(library(downloads) / "Finance" / "a.pdf", tmp_path / "a.pdf")
    first = run(cfg)
    assert row_for(cfg, "a.pdf").status is FileStatus.MISSING
    assert len(first.missing) == 1
    assert run(cfg).missing == []  # reported once
    (library(downloads) / "Contracts").mkdir()
    shutil.move(tmp_path / "a.pdf", library(downloads) / "Contracts" / "a.pdf")
    again = run(cfg)
    assert len(again.found_again) == 1
    assert row_for(cfg, "a.pdf").category == "Contracts"


def test_folder_units_are_found_by_manifest(downloads: Path, cfg: Config) -> None:
    (downloads / "project").mkdir()
    (downloads / "project" / "main.py").write_text("print(1)", encoding="utf-8")
    for p in (downloads / "project" / "main.py", downloads / "project"):
        make_old(p)
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    (library(downloads) / "Images").mkdir()
    shutil.move(library(downloads) / "Other" / "project", library(downloads) / "Images" / "project")
    run(cfg)
    row = row_for(cfg, "project")
    assert row.is_unit and row.category == "Images"


def test_cli_reconcile_reports(tmp_path: Path, downloads: Path, cfg: Config) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    organize(downloads, cfg, {"a.pdf": (b"a", "Other")})
    (library(downloads) / "Finance").mkdir()
    shutil.move(library(downloads) / "Other" / "a.pdf", library(downloads) / "Finance" / "a.pdf")
    result = CliRunner().invoke(app, ["reconcile", "-c", str(config_file)])
    assert result.exit_code == 0, result.stderr
    assert "learned Finance/a.pdf: Other -> Finance" in result.stdout
    assert "1 moved by hand" in result.stdout


# --- iCloud: nothing that exists only in the cloud is read (that would download it) ---


def test_cloud_only_at_its_place_is_left_as_is_not_missing(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance")})
    recorded = library(downloads) / "Finance" / "a.pdf"
    mark_cloud_only(monkeypatch, recorded)
    for _ in range(2):  # nothing is written, so it is reported again each time
        report = run(cfg)
        assert report.cloud_only == [recorded]
        assert report.missing == [] and report.moved == []
        row = row_for(cfg, "a.pdf")
        assert (row.status, row.current_path) == (FileStatus.ORGANIZED, recorded)


def test_file_inside_a_cloud_only_folder_is_not_looked_up(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance")})
    mark_cloud_only(monkeypatch, library(downloads) / "Finance")
    report = run(cfg)
    assert report.cloud_only == [library(downloads) / "Finance" / "a.pdf"]
    assert row_for(cfg, "a.pdf").status is FileStatus.ORGANIZED


def organize_unit(downloads: Path, cfg: Config) -> Path:
    (downloads / "project").mkdir()
    (downloads / "project" / "main.py").write_text("print(1)", encoding="utf-8")
    for p in (downloads / "project" / "main.py", downloads / "project"):
        make_old(p)
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    return library(downloads) / "Other" / "project"


def test_unit_with_a_cloud_only_file_inside_is_left_as_is(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = organize_unit(downloads, cfg)
    mark_cloud_only(monkeypatch, unit / "main.py")
    report = run(cfg)
    assert report.cloud_only == [unit]
    assert row_for(cfg, "project").status is FileStatus.ORGANIZED


def test_cloud_only_copy_elsewhere_is_no_match(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Other")})
    (library(downloads) / "Finance").mkdir()
    moved = library(downloads) / "Finance" / "a.pdf"
    shutil.move(library(downloads) / "Other" / "a.pdf", moved)
    with monkeypatch.context() as patch:
        mark_cloud_only(patch, moved)
        report = run(cfg)
    assert report.missing == [library(downloads) / "Other" / "a.pdf"]
    assert row_for(cfg, "a.pdf").status is FileStatus.MISSING
    # Once downloaded, the next reconcile finds it.
    assert run(cfg).found_again == [moved]
    assert row_for(cfg, "a.pdf").category == "Finance"


def test_cloud_only_unit_elsewhere_is_no_match(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = organize_unit(downloads, cfg)
    (library(downloads) / "Images").mkdir()
    moved = library(downloads) / "Images" / "project"
    shutil.move(unit, moved)
    mark_cloud_only(monkeypatch, moved / "main.py")
    report = run(cfg)
    assert report.missing == [unit]


def test_cloud_only_folders_are_not_searched(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = organize_unit(downloads, cfg)
    organize(downloads, cfg, {"a.pdf": (b"a", "Other")})
    archive = library(downloads) / "Archive"
    archive.mkdir()
    shutil.move(library(downloads) / "Other" / "a.pdf", archive / "a.pdf")
    shutil.move(unit, archive / "project")
    mark_cloud_only(monkeypatch, archive)
    report = run(cfg)  # listing or hashing anything in Archive fails the test
    assert sorted(p.name for p in report.missing) == ["a.pdf", "project"]


def test_cloud_only_library_is_not_searched(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance")})
    shutil.move(library(downloads) / "Finance" / "a.pdf", tmp_path / "a.pdf")
    run(cfg)  # now missing
    mark_cloud_only(monkeypatch, library(downloads))
    report = run(cfg)
    assert report.missing == [] and report.found_again == []
    assert row_for(cfg, "a.pdf").status is FileStatus.MISSING


def test_cli_reconcile_lists_cloud_only(
    tmp_path: Path, downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance")})
    mark_cloud_only(monkeypatch, library(downloads) / "Finance" / "a.pdf")
    result = CliRunner().invoke(app, ["reconcile", "-c", str(config_file)])
    assert result.exit_code == 0, result.stderr
    assert "icloud  Finance/a.pdf  (in iCloud only, not downloaded, not checked)" in result.stdout
    assert "0 missing, 1 in iCloud only." in result.stdout
