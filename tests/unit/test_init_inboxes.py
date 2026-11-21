"""``flib init`` offers the Desktop and a screenshots folder (#89), with fakes only.

Where macOS saves screenshots comes from the fake ``defaults`` (conftest); the
Desktop is a folder in the fake home. Nothing real is read or moved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from file_librarian import onboarding
from file_librarian.cli import app
from file_librarian.config import load_config
from tests._helpers import fake_config, put

runner = CliRunner()


@pytest.fixture(autouse=True)
def _english_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


@pytest.fixture
def desktop(fake_home: Path) -> Path:
    folder = fake_home / "Desktop"
    folder.mkdir()
    return folder


@pytest.fixture
def shots(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = fake_home / "Pictures" / "Screenshots"
    folder.mkdir(parents=True)
    monkeypatch.setenv("FAKE_SCREENSHOT_LOCATION", "~/Pictures/Screenshots")
    return folder


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_screenshot_folder_reads_the_setting(shots: Path, fake_defaults: Path) -> None:
    assert onboarding.screenshot_folder() == shots
    assert calls(fake_defaults) == [["read", "com.apple.screencapture", "location"]]


def test_screenshot_folder_defaults_to_the_desktop(desktop: Path) -> None:
    assert onboarding.screenshot_folder() == desktop


@pytest.mark.parametrize("location", ["", "~/does-not-exist", "relative/path"])
def test_screenshot_folder_is_none_when_unusable(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    monkeypatch.setenv("FAKE_SCREENSHOT_LOCATION", location)
    assert onboarding.screenshot_folder() is None


def test_screenshot_folder_survives_a_missing_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("file_librarian.onboarding.DEFAULTS", "/nonexistent/defaults")
    assert onboarding.screenshot_folder() is None


def test_yes_keeps_the_easiest_setup_downloads_only(
    downloads: Path, desktop: Path, shots: Path, tmp_path: Path
) -> None:
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Also tidy the Desktop? [y/N]: n" in result.stdout
    assert "File screenshots too? [y/N]: n" in result.stdout
    cfg = load_config(path)
    assert cfg.input_paths == [downloads]
    assert "categories:" not in path.read_text(encoding="utf-8")


def test_accepting_both_adds_conservative_desktop_and_a_screenshots_category(
    downloads: Path, desktop: Path, shots: Path, tmp_path: Path
) -> None:
    path = tmp_path / "config.yaml"
    # language, folder, output, review: Enter; Desktop: y; screenshots: y;
    # provider: Enter (Claude Code); schedule: n.
    answers = "\n\n\n\ny\ny\n\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "work in progress" in result.stdout
    cfg = load_config(path)
    assert cfg.input_paths == [downloads, desktop, shots]
    _, on_desktop, screenshots = cfg.inputs
    assert on_desktop.min_age_days == 7
    assert on_desktop.only is not None
    assert "*.pdf" in on_desktop.only and "*.png" in on_desktop.only
    for kept_out in ("*.zip", "*.dmg", "*.txt", "*.md", "*.csv"):
        assert kept_out not in on_desktop.only
    assert cfg.folders_for(on_desktop) == "ignore"
    assert (screenshots.category, screenshots.layout) == ("Screenshots", "{year}/{month}")
    assert not screenshots.rename
    assert screenshots.only == list(onboarding.SCREENSHOT_ONLY)
    assert cfg.folders_for(screenshots) == "ignore"
    assert "0 item(s) there look like screenshots" in result.stdout
    assert "warning" not in result.stderr  # no fixed category without `only`
    # The language's default categories are written out, with Screenshots added.
    assert [c.name for c in cfg.categories] == [
        "Finance",
        "Contracts",
        "Installers",
        "Images",
        "Other",
        "Screenshots",
    ]
    installers = cfg.category("Installers")
    assert installers is not None and installers.rename is False
    text = path.read_text(encoding="utf-8")
    assert '  - path: "~/Desktop"' in text
    assert yaml.safe_load(text)["inputs"][0] == "~/Downloads"


def test_pt_br_names_the_screenshots_category_in_portuguese(
    downloads: Path, shots: Path, tmp_path: Path
) -> None:
    path = tmp_path / "config.yaml"
    # language: pt-BR; folder, output, review: Enter; screenshots: y; provider; schedule: n.
    answers = "pt-BR\n\n\n\ny\n\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)

    assert result.exit_code == 0, result.stdout + result.stderr
    cfg = load_config(path)
    assert cfg.inputs[1].category == "Capturas de tela"
    assert cfg.category("Capturas de tela") is not None
    assert cfg.category("Financeiro") is not None


def test_screenshots_saved_to_the_desktop_are_not_offered_as_an_input(
    downloads: Path, desktop: Path, tmp_path: Path
) -> None:
    path = tmp_path / "config.yaml"
    answers = "\n\n\n\nn\n\nn\n"  # Desktop: n
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "which holds other files too" in result.stdout
    assert "File screenshots too?" not in result.stdout
    assert load_config(path).input_paths == [downloads]


def test_an_inbox_that_would_overlap_is_explained_and_left_out(
    downloads: Path, fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (downloads / "Screens").mkdir()
    monkeypatch.setenv("FAKE_SCREENSHOT_LOCATION", str(downloads / "Screens"))
    path = tmp_path / "config.yaml"
    answers = "\n\n\n\ny\n\nn\n"  # screenshots: y
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Not adding the screenshots folder" in result.stderr
    assert "inside" in result.stderr
    assert load_config(path).input_paths == [downloads]


@pytest.mark.parametrize(
    "where",
    [
        "Pictures",
        "Documents",
        "Movies",
        "Downloads",
        "Library/Mobile Documents/com~apple~CloudDocs",
    ],
)
def test_a_screenshots_folder_shared_with_other_files_is_refused(
    downloads: Path, fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    (fake_home / where).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FAKE_SCREENSHOT_LOCATION", f"~/{where}")
    path = tmp_path / "config.yaml"
    answers = "\n\n\n\n\nn\n"  # nothing about screenshots is asked
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert "which holds other files too" in result.stdout
    assert "Other Location" in result.stdout
    assert "File screenshots too?" not in result.stdout
    assert load_config(path).input_paths == [downloads]


def test_the_home_folder_is_never_a_screenshots_input(
    downloads: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SCREENSHOT_LOCATION", "~")
    shots = onboarding.screenshot_folder()
    assert shots == fake_home
    assert onboarding.shared_folder(shots)
    assert not onboarding.shared_folder(fake_home / "Pictures" / "Screenshots")


def test_the_offer_says_how_many_items_match(downloads: Path, shots: Path, tmp_path: Path) -> None:
    put(shots, "Screenshot 2026-11-01 at 10.00.00.png", b"a")
    put(shots, "Captura de Tela 2026-11-02 às 09.00.00.png", b"b")
    put(shots, "Screen Recording 2026-11-03 at 08.00.00.mov", b"c")
    put(shots, "taxes.pdf", b"d")
    put(shots, "Screenshot notes.txt", b"e")
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert "(3 item(s) there look like screenshots" in result.stdout


def test_what_init_writes_files_only_screenshots_from_that_folder(
    downloads: Path, shots: Path
) -> None:
    """A screenshots folder that also holds other things: only screenshots move."""
    from file_librarian import pipeline
    from file_librarian.providers.fake import FakeProvider

    answers = onboarding.Answers(
        "en", downloads, downloads / "Organized", "_Review", "fake", screenshots=shots
    )
    cfg = onboarding.check_answers(answers)
    shot = put(shots, "Screenshot 2026-11-01 at 10.00.00.png", b"pixels")
    recording = put(shots, "Gravação de Tela 2026-11-02 às 09.00.00.mov", b"video")
    others = [
        put(shots, "taxes.pdf", b"a tax form"),
        put(shots, "Screenshot of a draft.txt", b"text"),
        put(shots, "Old shots/Screenshot 1.png", b"in a sub-folder"),
        put(shots, "Photos Library.photoslibrary/database.db", b"a package"),
    ]
    provider = FakeProvider()

    outcome = pipeline.run(cfg, provider, simulate=False)

    assert provider.calls == []
    assert not shot.exists() and not recording.exists()
    assert all(p.exists() for p in others)
    moved = sorted(i.destination.name for i in outcome.plan.items if i.destination is not None)
    assert moved == sorted([shot.name, recording.name])


def test_check_config_warns_about_a_fixed_category_without_only(
    downloads: Path, desktop: Path, tmp_path: Path
) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(
        f"provider: {{name: fake, model: x}}\n"
        f"inputs:\n  - {json.dumps(str(downloads))}\n"
        f"  - path: {json.dumps(str(desktop))}\n    category: Images\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["check-config", "-c", str(config)])
    assert result.exit_code == 0
    assert (
        f"warning: input {desktop} has a fixed category and no `only`: everything in this "
        "folder is filed as Images without being looked at."
    ) in result.stderr


def test_the_desktop_is_not_offered_when_it_is_the_first_input(
    desktop: Path, tmp_path: Path
) -> None:
    path = tmp_path / "config.yaml"
    # language: Enter; folder: ~/Desktop; output, review: Enter; provider: Enter; schedule: n.
    answers = "\n~/Desktop\n\n\n\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Also tidy the Desktop?" not in result.stdout
    assert load_config(path).input_paths == [desktop]


def test_no_desktop_folder_means_no_question(downloads: Path, tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert result.exit_code == 0, result.stderr
    assert "Desktop?" not in result.stdout


def test_the_trial_sample_follows_each_inputs_rules(downloads: Path, desktop: Path) -> None:
    shots_dir = downloads.parent / "Shots"
    shots_dir.mkdir()
    cfg = fake_config(
        inputs=[
            str(downloads),
            {"path": str(desktop), "only": ["*.pdf"], "ignore": ["draft*"]},
            {"path": str(shots_dir), "category": "Images"},
        ],
        output_root=str(downloads.parent / "Organized"),
    )
    put(downloads, "a.txt", b"a")
    put(desktop, "report.pdf", b"r")
    put(desktop, "draft.pdf", b"d")
    put(desktop, "notes.txt", b"n")
    put(shots_dir, "shot.png", b"p")

    names = sorted(p.name for p in onboarding.pick_sample(cfg, 10))
    assert names == ["a.txt", "report.pdf"]

    young = fake_config(
        inputs=[{"path": str(desktop), "min_age_days": 7}],
        output_root=str(downloads.parent / "Organized"),
    )
    assert onboarding.pick_sample(young, 10) == []


def test_the_access_check_reports_every_input(
    downloads: Path, desktop: Path, tmp_path: Path
) -> None:
    config = tmp_path / "c.yaml"
    config.write_text(
        f"provider: {{name: fake, model: x}}\n"
        f"inputs:\n  - {json.dumps(str(downloads))}\n"
        f"  - path: {json.dumps(str(desktop))}\n    min_age_days: 7\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.json"
    result = runner.invoke(app, ["check-access", "--out", str(out), "-c", str(config)])
    assert result.exit_code == 0
    assert json.loads(out.read_text()) == {str(downloads): "ok", str(desktop): "ok"}
