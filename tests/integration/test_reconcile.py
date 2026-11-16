"""flib reconcile: the index follows files the user moved, renamed or deleted (RF-20)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config, parse_config
from file_librarian.db.store import FileRow, Store
from file_librarian.models import Classification, FileStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import ReconcileReport, reconcile
from tests._helpers import OLD

pytestmark = pytest.mark.integration


def answer(category: str) -> Classification:
    return Classification(category=category, suggested_name="x", description="d", confidence=0.75)


@pytest.fixture
def cfg(downloads: Path) -> Config:
    return parse_config({"inputs": [str(downloads)], "provider": {"name": "fake", "model": "x"}})


def organize(downloads: Path, cfg: Config, files: dict[str, tuple[bytes, str]]) -> None:
    for name, (content, _) in files.items():
        (downloads / name).write_bytes(content)
        os.utime(downloads / name, (OLD, OLD))
    provider = FakeProvider({name: answer(category) for name, (_, category) in files.items()})
    pipeline.run(cfg, provider, simulate=False)


def row_for(cfg: Config, original: str) -> FileRow:
    with Store.open(cfg.database) as store:
        return next(r for r in store.all_files() if r.original_name == original)


def lib(downloads: Path) -> Path:
    return downloads / "Organized"


def run(cfg: Config) -> ReconcileReport:
    with Store.open(cfg.database) as store:
        return reconcile(cfg, store)


def test_untouched_files_are_left_alone(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance")})
    report = run(cfg)
    assert report.checked == 1 and not (report.moved or report.missing)


def test_moved_to_another_category_is_learned(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.pdf": (b"bill", "Other")})
    (lib(downloads) / "Finance").mkdir()
    shutil.move(lib(downloads) / "Other" / "bill.pdf", lib(downloads) / "Finance" / "bill.pdf")
    report = run(cfg)
    row = row_for(cfg, "bill.pdf")
    assert row.current_path == lib(downloads) / "Finance" / "bill.pdf"
    assert (row.status, row.category) == (FileStatus.ORGANIZED, "Finance")
    assert report.recategorized[0][1:] == ("Other", "Finance")
    with Store.open(cfg.database) as store:
        rows = store.conn.execute("SELECT field, old_value, new_value FROM corrections")
        assert [tuple(r) for r in rows] == [("category", "Other", "Finance")]


def test_renamed_in_place_records_the_name(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"scan.pdf": (b"scan", "Finance")})
    folder = lib(downloads) / "Finance"
    (folder / "scan.pdf").rename(folder / "electricity-october.pdf")
    run(cfg)
    row = row_for(cfg, "scan.pdf")
    assert row.current_name == "electricity-october.pdf"
    with Store.open(cfg.database) as store:
        rows = store.conn.execute("SELECT field, new_value FROM corrections")
        assert [tuple(r) for r in rows] == [("name", "electricity-october.pdf")]


def test_moved_to_review_or_back_to_downloads(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.pdf": (b"a", "Finance"), "b.pdf": (b"b", "Finance")})
    (lib(downloads) / "_Review").mkdir()
    shutil.move(lib(downloads) / "Finance" / "a.pdf", lib(downloads) / "_Review" / "a.pdf")
    shutil.move(lib(downloads) / "Finance" / "b.pdf", downloads / "b.pdf")
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
    shutil.move(lib(downloads) / "Finance" / "a.pdf", tmp_path / "a.pdf")
    first = run(cfg)
    assert row_for(cfg, "a.pdf").status is FileStatus.MISSING
    assert len(first.missing) == 1
    assert run(cfg).missing == []  # reported once
    (lib(downloads) / "Contracts").mkdir()
    shutil.move(tmp_path / "a.pdf", lib(downloads) / "Contracts" / "a.pdf")
    again = run(cfg)
    assert len(again.found_again) == 1
    assert row_for(cfg, "a.pdf").category == "Contracts"


def test_folder_units_are_found_by_manifest(downloads: Path, cfg: Config) -> None:
    (downloads / "project").mkdir()
    (downloads / "project" / "main.py").write_text("print(1)", encoding="utf-8")
    for p in (downloads / "project" / "main.py", downloads / "project"):
        os.utime(p, (OLD, OLD))
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    (lib(downloads) / "Images").mkdir()
    shutil.move(lib(downloads) / "Other" / "project", lib(downloads) / "Images" / "project")
    run(cfg)
    row = row_for(cfg, "project")
    assert row.is_unit and row.category == "Images"


def test_cli_reconcile_reports(tmp_path: Path, downloads: Path, cfg: Config) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    organize(downloads, cfg, {"a.pdf": (b"a", "Other")})
    (lib(downloads) / "Finance").mkdir()
    shutil.move(lib(downloads) / "Other" / "a.pdf", lib(downloads) / "Finance" / "a.pdf")
    result = CliRunner().invoke(app, ["reconcile", "-c", str(config_file)])
    assert result.exit_code == 0, result.stderr
    assert "learned Finance/a.pdf: Other -> Finance" in result.stdout
    assert "1 moved by hand" in result.stdout
