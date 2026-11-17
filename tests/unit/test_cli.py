from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian.cli import EXIT_NOT_IMPLEMENTED, app
from file_librarian.extractors.origin import WHERE_FROMS
from tests._helpers import REPO_ROOT, mark_cloud_only, write_xattr

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.startswith("flib ")


def test_help_lists_every_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "run",
        "simulate",
        "search",
        "undo",
        "schedule",
        "reconcile",
        "fix",
        "init",
        "mcp",
        "reprocess",
        "doctor",
        "check-config",
        "inspect",
    ):
        assert command in result.stdout


@pytest.mark.parametrize(
    ("args", "requirement"),
    [
        (["init"], "RF-38"),
        (["reprocess"], "RF-37"),
        (["doctor"], "RF-39"),
    ],
)
def test_planned_commands_exit_non_zero(args: list[str], requirement: str) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == EXIT_NOT_IMPLEMENTED
    assert requirement in result.stderr


def test_check_config_ok() -> None:
    result = runner.invoke(app, ["check-config", "-c", str(REPO_ROOT / "config.example.yaml")])
    assert result.exit_code == 0, result.stderr
    assert "Finance" in result.stdout


def test_check_config_warns_about_planned_provider(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("provider: {name: claude-code, model: x}\n", encoding="utf-8")
    result = runner.invoke(app, ["check-config", "-c", str(cfg)])
    assert result.exit_code == 0
    assert "planned for M2" in result.stderr


def test_inspect_directory_reports_unit_size(downloads: Path) -> None:
    folder = downloads / "project"
    folder.mkdir()
    (folder / "a.txt").write_text("abc", encoding="utf-8")
    result = runner.invoke(app, ["inspect", "--json", str(folder)])
    assert result.exit_code == 0, result.stderr
    [item] = json.loads(result.stdout)
    assert item["kind"] == "folder unit"
    assert item["size_bytes"] == 3
    assert len(item["sha256"]) == 64


def test_check_config_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("inputs: []\n", encoding="utf-8")
    result = runner.invoke(app, ["check-config", "-c", str(bad)])
    assert result.exit_code == 1
    assert "invalid configuration" in result.stderr


def test_inspect_shows_origin(downloads: Path) -> None:
    path = downloads / "IMG_4821.png"
    path.write_bytes(b"\x89PNG synthetic")
    url = "https://example.com/photo.png"
    write_xattr(path, WHERE_FROMS, plistlib.dumps([url], fmt=plistlib.FMT_BINARY))

    result = runner.invoke(app, ["inspect", "--json", str(path)])
    assert result.exit_code == 0, result.stderr
    [item] = json.loads(result.stdout)
    assert item["source_urls"] == [url]
    assert len(item["sha256"]) == 64
    assert item["icloud_only"] is False


def test_inspect_cloud_only_file_is_not_read(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = downloads / "slides.key"
    path.write_bytes(b"only in the cloud")
    url = "https://example.com/slides.key"
    write_xattr(path, WHERE_FROMS, plistlib.dumps([url], fmt=plistlib.FMT_BINARY))
    mark_cloud_only(monkeypatch, path)

    result = runner.invoke(app, ["inspect", "--json", str(path)])
    assert result.exit_code == 0, result.stderr
    [item] = json.loads(result.stdout)
    assert item["icloud_only"] is True
    assert item["sha256"] is None
    assert item["size_bytes"] == len(b"only in the cloud")  # from metadata
    assert item["source_urls"] == [url]  # an extended attribute, never downloads

    text = runner.invoke(app, ["inspect", str(path)])
    assert text.exit_code == 0, text.stderr
    assert "sha256: (in iCloud only, not downloaded; download it to inspect it)" in text.stdout
    assert "size:   17 bytes" in text.stdout


def test_inspect_cloud_only_folders(downloads: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    album = downloads / "Album"
    album.mkdir()
    (album / "photo.jpg").write_bytes(b"jpg")
    project = downloads / "project"
    project.mkdir()
    (project / "a.txt").write_text("a", encoding="utf-8")
    (project / "b.bin").write_bytes(b"only in the cloud")
    mark_cloud_only(monkeypatch, album, project / "b.bin")

    result = runner.invoke(app, ["inspect", "--json", str(album), str(project)])
    assert result.exit_code == 0, result.stderr
    items = json.loads(result.stdout)
    assert [(i["kind"], i["icloud_only"], i["sha256"]) for i in items] == [
        ("folder unit", True, None),
        ("folder unit", True, None),
    ]
    text = runner.invoke(app, ["inspect", str(album)])
    assert "size:" not in text.stdout  # a partial size would mislead


def test_simulate_run_and_undo_end_to_end(downloads: Path, tmp_path: Path) -> None:
    import os
    import time

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: offline}}\n", encoding="utf-8"
    )
    path = downloads / "notes.txt"
    path.write_text("meeting notes", encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))

    sim = runner.invoke(app, ["simulate", "-c", str(cfg)])
    assert sim.exit_code == 0, sim.stderr
    assert "would move" in sim.stdout and path.exists()

    run = runner.invoke(app, ["run", "-c", str(cfg)])
    assert run.exit_code == 0, run.stderr
    assert "1 organized" in run.stdout
    assert not path.exists()

    undo = runner.invoke(app, ["undo", "-c", str(cfg)])
    assert undo.exit_code == 0, undo.stderr
    assert "1 restored" in undo.stdout
    assert path.read_text(encoding="utf-8") == "meeting notes"

    again = runner.invoke(app, ["undo", "-c", str(cfg)])
    assert again.exit_code == 1
    assert "nothing left to undo" in again.stderr


def test_run_with_unimplemented_provider_fails_cleanly(downloads: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: claude-code, model: x}}\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["run", "-c", str(cfg)])
    assert result.exit_code == 1
    assert "planned for M2" in result.stderr


def test_simulate_lists_cloud_only_files(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    from file_librarian import hashing

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: offline}}\n", encoding="utf-8"
    )
    path = downloads / "slides.key"
    path.write_bytes(b"only in the cloud")
    old = time.time() - 3600
    os.utime(path, (old, old))
    inode = os.lstat(path).st_ino
    monkeypatch.setattr(hashing, "is_dataless", lambda st: st.st_ino == inode)

    sim = runner.invoke(app, ["simulate", "-c", str(cfg)])
    assert sim.exit_code == 0, sim.stderr
    assert "slides.key  (in iCloud only, not downloaded)" in sim.stdout
