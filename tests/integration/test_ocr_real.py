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

import hashlib
import os
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from file_librarian.config import OcrConfig
from file_librarian.evals import corpus
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
        text = ocr.recognize(path, settings, 10_000).text
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


@pytest.mark.parametrize(
    "item", [i for i in corpus.items() if "image-text" in i.gold.cases], ids=lambda i: i.path
)
def test_the_corpus_pictures_read_as_recorded(tmp_path: Path, item: corpus.Item) -> None:
    """What this Mac finds in the evaluation corpus's pictures is what ``ocr.json`` holds,
    near enough: the same size, about the same lines and letters, the same verdict."""
    target = tmp_path / item.path
    item.write(target)
    found = ocr.recognize(target, OcrConfig(languages=list(corpus.OCR_LANGUAGES)), 100_000)
    expected = corpus.recorded_ocr()[hashlib.sha256(target.read_bytes()).hexdigest()]
    assert (found.width, found.height) == (expected.width, expected.height)
    assert abs(found.lines - expected.lines) <= 3

    # Letters, not words: another macOS version may split or join stylised words.
    def letters(text: str) -> str:
        return "".join(ch for ch in text.lower() if ch.isalnum())

    assert SequenceMatcher(None, letters(expected.text), letters(found.text)).ratio() >= 0.7
    assert ocr.looks_like_document(found) is ocr.looks_like_document(expected)
