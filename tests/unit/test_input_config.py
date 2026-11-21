"""Per-input rules in the configuration (#89): parsing, validation, overlap checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from file_librarian.cli import app
from file_librarian.config import ConfigError, InputConfig
from tests._helpers import REPO_ROOT, fake_config

runner = CliRunner()


def inbox(home: Path, name: str) -> Path:
    folder = home / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def with_inputs(home: Path, *inputs: Any, **extra: Any) -> Any:
    return fake_config(inputs=list(inputs), output_root=str(home / "Organized"), **extra)


def test_a_plain_list_keeps_working_with_no_rules(fake_home: Path) -> None:
    downloads = inbox(fake_home, "Downloads")
    desktop = inbox(fake_home, "Desktop")
    cfg = with_inputs(fake_home, str(downloads), "~/Desktop")

    assert cfg.input_paths == [downloads, desktop]
    for entry in cfg.inputs:
        assert not entry.has_rules
        assert (entry.only, entry.ignore, entry.min_age_days, entry.category) == (None, [], 0, None)
        assert cfg.folders_for(entry) == cfg.folders == "as_unit"
        assert cfg.duplicates_for(entry) == cfg.duplicates == "move"


def test_mappings_and_plain_paths_mix(fake_home: Path) -> None:
    cfg = with_inputs(
        fake_home,
        "~/Downloads",
        {
            "path": "~/Desktop",
            "only": ["*.pdf", "Screenshot*"],
            "ignore": ["draft*"],
            "min_age_days": 7,
            "folders": "ignore",
            "duplicates": "leave",
        },
        {"path": "~/Shots", "category": "Images", "layout": "{year}/{month}", "rename": True},
    )
    downloads, desktop, shots = cfg.inputs
    assert not downloads.has_rules
    assert desktop.path == fake_home / "Desktop"
    assert desktop.only == ["*.pdf", "Screenshot*"]
    assert desktop.min_age_days == 7
    assert cfg.folders_for(desktop) == "ignore"
    assert cfg.duplicates_for(desktop) == "leave"
    assert (shots.category, shots.layout, shots.rename) == ("Images", "{year}/{month}", True)
    assert cfg.input_of(fake_home / "Desktop" / "a.pdf") is desktop
    assert cfg.input_of(fake_home / "Desktop" / "sub" / "a.pdf") is None
    assert cfg.input_rules(fake_home / "Elsewhere") == InputConfig(path=fake_home / "Elsewhere")


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"path": "~/Desktop", "rename": True}, "rename applies only with a fixed category"),
        ({"path": "~/Desktop", "layout": "{year}"}, "layout applies only with a fixed category"),
        ({"path": "~/Desktop", "category": "Nope"}, "'Nope' is not a configured category"),
        ({"path": "~/Desktop", "only": []}, "at least 1 item"),
        ({"path": "~/Desktop", "only": ["Work/*.pdf"]}, "match a name, not a path"),
        ({"path": "~/Desktop", "ignore": ["  "]}, "must not be empty"),
        ({"path": "~/Desktop", "min_age_days": -1}, "greater than or equal to 0"),
        ({"path": "~/Desktop", "folders": "unit"}, "inputs.0.folders"),
        ({"path": "~/Desktop", "duplicates": "delete"}, "inputs.0.duplicates"),
        ({"path": "~/Desktop", "olny": ["*.pdf"]}, "Extra inputs are not permitted"),
        ({"path": "Desktop"}, "must be an absolute path"),
        ({"only": ["*.pdf"]}, "inputs.0.path"),
        ({"path": "~/Desktop", "layout": "{nope}", "category": "Images"}, "unknown placeholder"),
    ],
)
def test_invalid_rules_are_rejected_with_a_clear_message(
    fake_home: Path, entry: dict[str, Any], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        with_inputs(fake_home, entry)


@pytest.mark.parametrize(
    ("inputs", "message"),
    [
        (["~"], "too broad"),
        (["~/Downloads", "~/Downloads/Inbox"], r"Downloads/Inbox is inside .*Downloads"),
        (["~/Work/Desktop", "~/Work"], r"Work/Desktop is inside .*Work"),
        (["~/Downloads", "~/downloads"], "listed twice"),
        (["~/Organized/Inbox"], "must not be inside output_root"),
        ([{"path": "~/Downloads"}, {"path": "~/Downloads/Sub", "min_age_days": 3}], "overlap"),
    ],
)
def test_nested_or_overlapping_inputs_are_rejected(
    fake_home: Path, inputs: list[Any], message: str
) -> None:
    for name in (
        "Downloads",
        "Work/Desktop",
        "Organized/Inbox",
        "Downloads/Inbox",
        "Downloads/Sub",
    ):
        inbox(fake_home, name)
    with pytest.raises(ConfigError, match=message):
        with_inputs(fake_home, *inputs)


def test_an_input_reached_through_a_symlink_counts_as_the_same_folder(fake_home: Path) -> None:
    real = inbox(fake_home, "Real")
    (fake_home / "Alias").symlink_to(real)
    with pytest.raises(ConfigError, match="listed twice"):
        with_inputs(fake_home, "~/Real", "~/Alias")


def test_check_config_prints_each_input_with_its_effective_rules(
    fake_home: Path, tmp_path: Path
) -> None:
    for name in ("Downloads", "Desktop", "Shots"):
        inbox(fake_home, name)
    path = tmp_path / "config.yaml"
    path.write_text(
        """\
provider: {name: fake, model: x}
ignore: ["*.tmp"]
inputs:
  - ~/Downloads
  - path: ~/Desktop
    only: ["*.pdf"]
    ignore: ["draft*"]
    min_age_days: 7
    folders: ignore
  - path: ~/Shots
    category: Images
    layout: "{year}/{month}"
""",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["check-config", "-c", str(path)])

    assert result.exit_code == 0, result.stderr
    out = result.stdout
    assert (
        f"    {fake_home / 'Downloads'}: ignore *.tmp; folders: as_unit; duplicates: move; "
        "classified by the AI"
    ) in out
    assert (
        f"    {fake_home / 'Desktop'}: only *.pdf; ignore *.tmp, draft* (1 of them its own); "
        "min_age_days 7; folders: ignore; duplicates: move; classified by the AI"
    ) in out
    assert (
        f"    {fake_home / 'Shots'}: ignore *.tmp; folders: as_unit; duplicates: move; "
        "fixed category Images (no AI), layout {year}/{month}, name kept"
    ) in out


def test_the_summary_can_hide_the_patterns(fake_home: Path) -> None:
    cfg = with_inputs(
        fake_home,
        {"path": "~/Desktop", "only": ["Tax*", "*.pdf"], "category": "Finance", "rename": True},
    )
    summary = cfg.input_summary(cfg.inputs[0], patterns=False)
    assert "Tax" not in summary
    assert summary.startswith("only 2 pattern(s);")
    assert "fixed category Finance (no AI), renamed by naming.pattern" in summary


def test_the_commented_examples_in_config_example_are_valid(fake_home: Path) -> None:
    lines = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8").splitlines()
    start = lines.index("inputs:") + 1
    end = lines.index("", start)
    block = [line.replace("  # ", "  ", 1) for line in lines[start:end]]
    raw = yaml.safe_load("\n".join(["inputs:", *block]))
    assert len(raw["inputs"]) == 3
    cfg = fake_config(inputs=raw["inputs"], categories=[{"name": "Screenshots"}, {"name": "Other"}])
    _, desktop, shots = cfg.inputs
    assert (desktop.min_age_days, cfg.folders_for(desktop)) == (7, "ignore")
    assert (shots.category, shots.layout) == ("Screenshots", "{year}/{month}")
