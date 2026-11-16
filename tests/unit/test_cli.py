from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian.cli import EXIT_NOT_IMPLEMENTED, app
from file_librarian.extractors.origin import WHERE_FROMS
from tests._helpers import REPO_ROOT, write_xattr

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
        (["run"], "M1"),
        (["simulate"], "RF-16"),
        (["search", "lease contract"], "RF-13"),
        (["undo"], "RF-17"),
        (["schedule"], "RF-19"),
        (["reconcile"], "RF-20"),
        (["init"], "RF-38"),
        (["mcp"], "RF-22"),
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


def test_check_config_warns_about_planned_provider() -> None:
    result = runner.invoke(app, ["check-config", "-c", str(REPO_ROOT / "config.example.yaml")])
    assert result.exit_code == 0
    assert "planned for M1" in result.stderr


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
