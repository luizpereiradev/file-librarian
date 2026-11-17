"""The category as a Finder tag through the real pipeline and undo (RF-36)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.finder_tags import USER_TAGS, read_tags
from file_librarian.providers.fake import FakeProvider
from file_librarian.undo import undo_file, undo_run
from tests._helpers import answer, fake_config, make_old, put, rows, set_tags, write_xattr

pytestmark = pytest.mark.integration


def test_run_tags_organized_items_and_undo_removes_only_that_tag(
    downloads: Path, cfg: Config
) -> None:
    set_tags(put(downloads, "a.pdf", b"A"), ["Work\n4"])
    set_tags(put(downloads, "b.pdf", b"B"), ["finance"])  # the user's own, any case
    put(downloads, "proj/x.txt")
    make_old(downloads / "proj")
    put(downloads, "unsure.pdf", b"U")
    provider = FakeProvider(
        {
            "a.pdf": answer("Finance", "a", 0.75),
            "b.pdf": answer("Finance", "b", 0.75),
            "proj": answer("Other", "project", 0.95),
            "unsure.pdf": answer("Finance", "u", confidence=0.3),
        }
    )
    outcome = pipeline.run(cfg, provider, simulate=False)
    assert outcome.report is not None
    assert all(i.warning is None for i in outcome.report.items)

    out = downloads / "Organized"
    assert read_tags(out / "Finance" / "a.pdf") == ["Work\n4", "Finance"]
    assert read_tags(out / "Finance" / "b.pdf") == ["finance"]
    assert read_tags(out / "Other" / "proj") == ["Other"]  # a folder unit: the folder itself
    assert read_tags(out / "Other" / "proj" / "x.txt") == []
    assert read_tags(out / "_Review" / "unsure.pdf") == []  # no category, no tag
    recorded = {
        Path(r["dst_path"]).name: r["finder_tag"]
        for r in rows(cfg, "SELECT dst_path, finder_tag FROM operations")
    }
    assert recorded == {"a.pdf": "Finance", "b.pdf": None, "proj": "Other", "unsure.pdf": None}

    with Store.open(cfg.database) as store:
        report = undo_run(store)
    assert len(report.restored) == 4 and report.warnings == []
    assert read_tags(downloads / "a.pdf") == ["Work\n4"]
    assert read_tags(downloads / "b.pdf") == ["finance"]  # never added, never removed
    assert read_tags(downloads / "proj") == []


def test_turning_the_option_off_stops_new_tags(downloads: Path) -> None:
    cfg = fake_config(downloads, finder_tags={"enabled": False})
    put(downloads, "a.pdf")
    pipeline.run(cfg, FakeProvider(default=answer("Finance", "a", 0.75)), simulate=False)
    assert read_tags(downloads / "Organized" / "Finance" / "a.pdf") == []
    [op] = rows(cfg, "SELECT finder_tag FROM operations")
    assert op["finder_tag"] is None


def test_simulate_writes_no_tags(downloads: Path, cfg: Config) -> None:
    path = put(downloads, "a.pdf")
    pipeline.run(cfg, FakeProvider(default=answer("Finance", "a", 0.75)), simulate=True)
    assert read_tags(path) == []


def test_tag_failure_never_fails_the_move(downloads: Path, cfg: Config) -> None:
    path = put(downloads, "a.pdf")
    write_xattr(path, USER_TAGS, b"\x00not a plist")
    outcome = pipeline.run(cfg, FakeProvider(default=answer("Finance", "a", 0.75)), simulate=False)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.destination == downloads / "Organized" / "Finance" / "a.pdf"
    assert item.warning is not None and "Finder tag" in item.warning
    [op] = rows(cfg, "SELECT status, finder_tag FROM operations")
    assert (op["status"], op["finder_tag"]) == ("done", None)


def test_undo_file_removes_the_tag_even_if_recolored(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf")
    pipeline.run(cfg, FakeProvider(default=answer("Finance", "a", 0.75)), simulate=False)
    moved = downloads / "Organized" / "Finance" / "a.pdf"
    set_tags(moved, ["Finance\n2", "Later"])  # the user colored it and added another
    with Store.open(cfg.database) as store:
        report = undo_file(store, moved)
    assert report.warnings == []
    assert read_tags(downloads / "a.pdf") == ["Later"]


def test_cli_prints_tag_warnings_in_run_and_undo(downloads: Path, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: offline}}\n", encoding="utf-8"
    )
    broken = put(downloads, "broken.txt", b"one")
    write_xattr(broken, USER_TAGS, b"\x00not a plist")
    put(downloads, "fine.txt", b"two")
    runner = CliRunner()

    run = runner.invoke(app, ["run", "-c", str(config)])
    assert run.exit_code == 0, run.stderr
    assert "2 organized" in run.stdout
    assert run.stdout.count("warning") == 1 and "not set" in run.stdout

    # The fake provider may rename it: find it by content.
    [fine] = [p for p in (downloads / "Organized").rglob("*.txt") if p.read_bytes() == b"two"]
    write_xattr(fine, USER_TAGS, b"\x00broken by someone else")
    undo = runner.invoke(app, ["undo", "-c", str(config)])
    assert undo.exit_code == 0, undo.stderr
    assert "2 restored" in undo.stdout
    assert "warning" in undo.stdout and "not removed" in undo.stdout
