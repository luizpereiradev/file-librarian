"""The model says which name describes a file better; the planner applies it (#59)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.models import Classification, NameChoice
from file_librarian.providers.fake import FakeProvider
from tests._helpers import make_old, make_pdf, put, rows, write_config

pytestmark = pytest.mark.integration
runner = CliRunner()
WHEN = date(2026, 11, 16)


def answer(
    name: str, choice: NameChoice, reason: str | None = None, confidence: float = 0.95
) -> Classification:
    return Classification(
        category="Finance",
        suggested_name=name,
        description=f"A {name}.",
        confidence=confidence,
        document_date=WHEN,
        name_choice=choice,
        name_choice_reason=reason,
    )


def put_pdf(folder: Path, name: str, text: str) -> Path:
    return make_old(make_pdf(folder / name, text))


def finance(downloads: Path) -> list[str]:
    return sorted(p.name for p in (downloads / "Organized" / "Finance").iterdir())


def test_descriptive_name_is_kept_and_generic_one_replaced(downloads: Path, cfg: Config) -> None:
    put_pdf(downloads, "Study guide - 11 tweaks for coding agents.pdf", "Eleven tweaks")
    put_pdf(downloads, "Study guide - AI agents in software engineering.pdf", "AI agents")
    put_pdf(downloads, "scan001.pdf", "Electricity bill")
    provider = FakeProvider(
        {
            "Study guide - 11 tweaks for coding agents.pdf": answer(
                "study guide coding agents", "original", "The original names the 11 tweaks."
            ),
            "Study guide - AI agents in software engineering.pdf": answer(
                "study guide ai agents", "original", "More specific than the proposal."
            ),
            "scan001.pdf": answer("electricity bill", "proposed", "scan001 says nothing."),
        }
    )
    outcome = pipeline.run(cfg, provider, simulate=False)

    # The original subject still gets flib's naming rules: date prefix, slug, extension.
    assert finance(downloads) == [
        "2026-11-16_electricity-bill.pdf",
        "2026-11-16_study-guide-11-tweaks-for-coding-agents.pdf",
        "2026-11-16_study-guide-ai-agents-in-software-engineering.pdf",
    ]
    assert outcome.report is not None
    reasons = {i.source.name: i.note for i in outcome.report.items}
    assert reasons["scan001.pdf"] == "category Finance"
    assert reasons["Study guide - 11 tweaks for coding agents.pdf"].endswith("original name kept")
    stored = {
        r["original_name"]: (r["name_choice"], r["name_choice_reason"])
        for r in rows(cfg, "SELECT * FROM files")
    }
    assert stored == {
        "Study guide - 11 tweaks for coding agents.pdf": (
            "original",
            "The original names the 11 tweaks.",
        ),
        "Study guide - AI agents in software engineering.pdf": (
            "original",
            "More specific than the proposal.",
        ),
        "scan001.pdf": ("proposed", "scan001 says nothing."),
    }


def test_kept_name_loses_its_old_date_and_unsafe_characters(downloads: Path, cfg: Config) -> None:
    put_pdf(downloads, "2025-01-02_Quarterly: budget review.pdf", "Budget")
    provider = FakeProvider({"2025-01-02_Quarterly: budget review.pdf": answer("b", "original")})
    pipeline.run(cfg, provider, simulate=False)
    assert finance(downloads) == ["2026-11-16_quarterly-budget-review.pdf"]


def test_original_with_nothing_usable_falls_back_to_the_proposal(
    downloads: Path, cfg: Config
) -> None:
    put_pdf(downloads, "().pdf", "Electricity bill")
    provider = FakeProvider({"().pdf": answer("electricity bill", "original")})
    pipeline.run(cfg, provider, simulate=False)
    assert finance(downloads) == ["2026-11-16_electricity-bill.pdf"]
    [row] = rows(cfg, "SELECT name_choice FROM files")
    assert row["name_choice"] == "proposed"


def test_no_choice_is_stored_when_no_rename_was_considered(downloads: Path, cfg: Config) -> None:
    put(downloads, "IMG_4801.png", b"\x89PNG not really")  # no readable content
    put_pdf(downloads, "unsure.pdf", "Something")
    provider = FakeProvider(
        {
            "IMG_4801.png": answer("photo", "proposed", "generic"),
            "unsure.pdf": answer("thing", "proposed", "generic", confidence=0.3),  # review
        }
    )
    pipeline.run(cfg, provider, simulate=False)
    stored = {
        r["original_name"]: (r["name_choice"], r["name_choice_reason"])
        for r in rows(cfg, "SELECT * FROM files")
    }
    assert stored == {"IMG_4801.png": (None, None), "unsure.pdf": (None, None)}


def test_inspect_shows_the_name_choice(downloads: Path, cfg: Config, tmp_path: Path) -> None:
    put_pdf(downloads, "Recipe - lemon cake.pdf", "Lemon cake")
    provider = FakeProvider(
        {"Recipe - lemon cake.pdf": answer("cake recipe", "original", "Names the cake.")}
    )
    pipeline.run(cfg, provider, simulate=False)
    target = downloads / "Organized" / "Finance" / "2026-11-16_recipe-lemon-cake.pdf"
    config = write_config(tmp_path, downloads)

    result = runner.invoke(app, ["inspect", "-c", str(config), str(target)])
    assert result.exit_code == 0, result.stderr
    assert "  index:  organized, Finance\n" in result.stdout
    assert "  name:   original name kept (Names the cake.)\n" in result.stdout

    result = runner.invoke(app, ["inspect", "--json", "-c", str(config), str(target)])
    [item] = json.loads(result.stdout)
    assert item["index"] == {
        "status": "organized",
        "category": "Finance",
        "current_path": str(target),
        "review_reason": None,
        "review_category": None,
        "review": None,
        "name_choice": "original",
        "name_choice_reason": "Names the cake.",
        "duplicate_of": None,
    }


def test_inspect_of_a_file_flib_has_not_seen(downloads: Path, cfg: Config) -> None:
    put_pdf(downloads, "other.pdf", "Unrelated")
    put_pdf(downloads, "seen.pdf", "Seen")
    pipeline.run(cfg, FakeProvider({"seen.pdf": answer("x", "proposed")}), simulate=False)
    # No --config: the default one (under the fake home) does not exist, so no index.
    result = runner.invoke(app, ["inspect", "--json", str(downloads / "other.pdf")])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)[0]["index"] is None


def test_inspect_reports_an_index_entry_without_a_choice(
    downloads: Path, cfg: Config, tmp_path: Path
) -> None:
    put(downloads, "video.mp4", b"\x00\x00\x00\x18ftypmp42")
    config = write_config(tmp_path, downloads)
    pipeline.run(cfg, FakeProvider({"video.mp4": answer("x", "proposed")}), simulate=False)
    target = downloads / "Organized" / "Finance" / "video.mp4"
    result = runner.invoke(app, ["inspect", "-c", str(config), str(target)])
    assert result.exit_code == 0, result.stderr
    assert "  index:  organized, Finance\n" in result.stdout
    assert "name:" not in result.stdout


def test_inspect_with_a_bad_config_fails(downloads: Path, tmp_path: Path) -> None:
    put(downloads, "a.txt", b"a")
    bad = tmp_path / "bad.yaml"
    bad.write_text("inputs: []\n", encoding="utf-8")
    result = runner.invoke(app, ["inspect", "-c", str(bad), str(downloads / "a.txt")])
    assert result.exit_code == 1
