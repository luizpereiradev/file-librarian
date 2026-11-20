"""macOS's own text recognition, for real (RF-26). Opt-in.

Every other test uses the fake helper (tests/fake_ocr.py). These run
``extractors/ocr.js`` under the real ``/usr/bin/osascript`` on synthetic
documents: a one-page PDF with a line of text, rendered to a PNG by the real
``/usr/bin/sips`` (a "screenshot"), and that PNG wrapped back into a PDF with
no text layer (a "scan"). Run them with
``FLIB_TEST_OCR=1 uv run pytest -m ocr``. The first recognition by a program
can take half a minute while Vision prepares its model.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from file_librarian.extractors import ocr
from tests._helpers import context_for, fake_config, make_pdf

pytestmark = [
    pytest.mark.integration,
    pytest.mark.ocr,
    pytest.mark.skipif(
        os.environ.get("FLIB_TEST_OCR") != "1" or not Path("/usr/bin/osascript").exists(),
        reason="real text recognition is opt-in: FLIB_TEST_OCR=1 on macOS",
    ),
]

LINE = "Electricity bill October 2026 total due 123.45 EUR"
SECRET = "password = correct-horse-battery-staple"  # noqa: S105 - a synthetic secret


@pytest.fixture(autouse=True)
def real_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "OSASCRIPT", "/usr/bin/osascript")
    ocr.availability.cache_clear()


def sips(*args: str) -> None:
    subprocess.run(["/usr/bin/sips", *args], check=True, capture_output=True, timeout=60)  # noqa: S603


def documents(folder: Path, text: str) -> tuple[Path, Path]:
    """A PNG of ``text`` and a PDF holding that PNG, with no text layer."""
    source = make_pdf(folder / "source.pdf", text)
    png = folder / "Screenshot.png"
    sips("-s", "format", "png", "-Z", "1600", str(source), "--out", str(png))
    scan = folder / "scan.pdf"
    sips("-s", "format", "pdf", str(png), "--out", str(scan))
    return png, scan


def words(text: str) -> set[str]:
    return set(text.lower().split())


def test_this_mac_can_recognise_text() -> None:
    found = ocr.availability()
    assert found.available, found.reason
    assert {"en-US", "pt-BR"} <= set(found.languages)


def test_text_is_recognised_in_an_image_and_a_scanned_pdf(tmp_path: Path) -> None:
    png, scan = documents(tmp_path, LINE)
    settings = fake_config(language="pt-BR").ocr
    for path in (png, scan):
        text = ocr.recognize(path, settings, 10_000)
        assert words(LINE) - words(text) <= {"123.45"}, text  # a figure may come out spaced
        assert "electricity" in text.lower() and "october" in text.lower()


def test_a_scan_is_classified_from_its_text_and_a_secret_is_caught(tmp_path: Path) -> None:
    _, scan = documents(tmp_path, LINE)
    ctx = context_for(scan)
    assert ctx.metadata.text_source == "ocr" and ctx.text is not None
    assert "electricity" in ctx.text.lower()

    secret = tmp_path / "secret"
    secret.mkdir()
    shot, _ = documents(secret, SECRET)
    assert context_for(shot).sensitive


def test_an_unreadable_file_fails_cleanly(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    with pytest.raises(ocr.OcrError, match="not a readable image"):
        ocr.recognize(broken, fake_config().ocr, 1000)
