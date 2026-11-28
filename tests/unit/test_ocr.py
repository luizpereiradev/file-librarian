"""Local OCR (RF-26): text recognised in images and scanned PDFs, and where it goes.

The helper is the fake from tests/fake_ocr.py: it "reads" only the text that
``make_scan`` hides in a synthetic file. Nothing here runs macOS's text
recognition; tests/integration/test_ocr_real.py does, when asked to.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import doctor, pipeline
from file_librarian.classifier import answer_key, classify_all, ocr_key, rules_key, vision_key
from file_librarian.cli import app
from file_librarian.config import Config, ConfigError
from file_librarian.extractors import ocr
from file_librarian.models import (
    CategoryRule,
    Classification,
    FileContext,
    FileMetadata,
    PictureText,
    Rules,
)
from file_librarian.providers.fake import FakeProvider
from file_librarian.providers.prompt import user_message
from tests._helpers import (
    context_for,
    fake_config,
    make_blank_pdf,
    make_image,
    make_old,
    make_pdf,
    make_scan,
    write_config,
)

RECEIPT = "Corner Bakery receipt 2026-03-14 two croissants total 7.50 EUR paid by card"
BILL = "Power Company electricity bill October 2026 amount due 123.45 EUR due date 2026-11-10"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
RULES = Rules(categories=(CategoryRule("Finance"), CategoryRule("Other")), language="en")
ANSWER = Classification(
    category="Finance",
    suggested_name="power bill",
    description="An electricity bill.",
    confidence=0.95,
    document_date=date(2026, 10, 1),
)
runner = CliRunner()


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def recognitions(log: Path) -> list[list[str]]:
    return [c for c in calls(log) if c[3] == "recognize"]


# --- Configuration ----------------------------------------------------------------


def test_ocr_is_on_by_default_with_languages_from_language() -> None:
    o = fake_config().ocr
    assert (o.enabled, o.languages, o.max_pages, o.min_chars) == (True, ["en-US"], 3, 40)
    assert fake_config(language="pt-BR").ocr.languages == ["pt-BR", "en-US"]


def test_explicit_ocr_settings_win_and_languages_still_default() -> None:
    cfg = fake_config(language="pt-BR", ocr={"max_pages": 5})
    assert cfg.ocr.languages == ["pt-BR", "en-US"] and cfg.ocr.max_pages == 5
    assert fake_config(language="pt-BR", ocr={"languages": ["fr-FR"]}).ocr.languages == ["fr-FR"]


@pytest.mark.parametrize(
    "bad",
    [
        {"languages": []},
        {"languages": ["en US"]},
        {"languages": ["--help"]},
        {"languages": ["EN"]},
        {"max_pages": 0},
        {"max_pages": 21},
        {"min_chars": 0},
        {"colour": 1},
    ],
)
def test_ocr_settings_are_validated(bad: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        fake_config(ocr=bad)


# --- The helper -------------------------------------------------------------------


def test_recognize_passes_everything_as_arguments(
    tmp_path: Path, fake_ocr: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    monkeypatch.chdir(tmp_path)
    settings = fake_config(ocr={"languages": ["pt-BR", "en-US"], "max_pages": 2}).ocr
    assert ocr.recognize(Path("scan.png"), settings, 1000).text == RECEIPT
    [call] = calls(fake_ocr)
    assert call == [
        "-l", "JavaScript", str(ocr.HELPER), "recognize",
        str(scan), "2", str(ocr.MAX_PX), "pt-BR", "en-US",
    ]  # fmt: skip


def test_the_helper_ships_with_the_package() -> None:
    assert ocr.HELPER.is_file()
    source = ocr.HELPER.read_text(encoding="utf-8")
    assert "VNRecognizeTextRequest" in source and "PDFDocument" in source


def test_recognize_caps_and_cleans_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    settings = fake_config().ocr
    assert ocr.recognize(scan, settings, 10).text == RECEIPT[:10]
    monkeypatch.setenv("FAKE_OCR_MODE", "garbage")
    text = ocr.recognize(scan, settings, 1000).text
    assert RECEIPT in text
    assert not any(ch in text for ch in "\x00\x07\x1b")


def test_recognize_reports_the_layout(tmp_path: Path) -> None:
    found = ocr.recognize(
        make_scan(tmp_path / "flyer.png", "BIG SALE\nSaturday 10am", width=900, height=1200),
        fake_config().ocr,
        1000,
    )
    assert found.text == "BIG SALE\nSaturday 10am"
    assert (found.lines, found.width, found.height) == (2, 900, 1200)
    assert 0 < found.coverage < 0.05
    assert found.picture() == PictureText(900, 1200, 2, found.coverage)


def test_a_photo_has_no_text(tmp_path: Path) -> None:
    photo = make_image(tmp_path / "IMG_0001.jpg", 4000, 3000)
    found = ocr.recognize(photo, fake_config().ocr, 1000)
    assert (found.text, found.lines, found.coverage) == ("", 0, 0.0)


def test_unreadable_helper_output_raises_ocr_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    monkeypatch.setenv("FAKE_OCR_MODE", "broken")
    with pytest.raises(ocr.OcrError, match="unreadable"):
        ocr.recognize(scan, fake_config().ocr, 1000)
    monkeypatch.delenv("FAKE_OCR_MODE")
    monkeypatch.setattr(ocr, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(ocr.OcrError, match="too much"):
        ocr.recognize(scan, fake_config().ocr, 1000)


def line(text: str, w: float = 0.5, h: float = 0.1) -> dict[str, Any]:
    return {"text": text, "box": [0.1, 0.1, w, h]}


def test_parse_joins_pages_and_measures_the_first() -> None:
    output = json.dumps(
        {
            "v": 2,
            "pages": [
                {"width": 800, "height": 600, "lines": [line("one"), line("two", 0.8, 0.5)]},
                {"width": 800, "height": 600, "lines": [line("three", 1, 1)]},
            ],
        }
    )
    found = ocr.parse(output, 1000)
    assert found == ocr.Recognized("one\ntwo\n\nthree", 3, 0.45, 800, 600)
    assert ocr.parse(output, 5).text == "one\nt"


@pytest.mark.parametrize(
    "page",
    [
        {"width": "wide", "height": -1, "lines": [line("x", 3, 3)]},
        {"lines": [line("x", 3, 3), line("   ")]},
    ],
)
def test_parse_tolerates_odd_sizes_and_clamps_coverage(page: dict[str, Any]) -> None:
    found = ocr.parse(json.dumps({"v": 2, "pages": [page]}), 1000)
    assert (found.text, found.lines, found.coverage, found.width, found.height) == (
        "x",
        1,
        1.0,
        None,
        None,
    )


@pytest.mark.parametrize(
    "output",
    [
        "plain text from version 1",
        json.dumps({"v": 1, "pages": []}),
        json.dumps({"v": 2, "pages": [{"lines": [{"text": "x", "box": [0, 0, 1]}]}]}),
        json.dumps({"v": 2, "pages": [{"lines": [{"text": "x", "box": [0, 0, "1", 1]}]}]}),
        json.dumps({"v": 2, "pages": [{"lines": [{"text": "x", "box": [0, 0, True, 1]}]}]}),
        json.dumps({"v": 2, "pages": [{"nolines": []}]}),
        json.dumps([]),
    ],
)
def test_parse_rejects_anything_else(output: str) -> None:
    with pytest.raises(ocr.OcrError, match="unreadable"):
        ocr.parse(output, 1000)


def test_parse_with_no_pages_is_empty() -> None:
    assert ocr.parse(json.dumps({"v": 2, "pages": []}), 1000) == ocr.Recognized("")


DENSE = "x" * 40


@pytest.mark.parametrize(
    ("lines", "chars", "coverage", "document"),
    [
        (26, 988, 0.317, True),  # the corpus's scanned page
        (23, 210, 0.126, False),  # its chat screenshot
        (19, 145, 0.066, False),  # its photographed receipt
        (6, 88, 0.122, False),  # its flyer
        (9, 900, 0.5, False),  # too few lines: a poster
        (30, 299, 0.5, False),  # too few characters
        (30, 900, 0.19, False),  # too little of the picture
        (10, 300, 0.2, True),  # the thresholds themselves
    ],
)
def test_looks_like_document(lines: int, chars: int, coverage: float, document: bool) -> None:
    found = ocr.Recognized("a" * chars, lines, coverage)
    assert ocr.looks_like_document(found) is document


def test_replay_returns_recorded_results_without_the_helper(tmp_path: Path, fake_ocr: Path) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    photo = make_image(tmp_path / "photo.jpg", 10, 10)
    recorded = ocr.Recognized("recorded text", 1, 0.1, 10, 20)
    digest = hashlib.sha256(scan.read_bytes()).hexdigest()
    with ocr.replay({digest: recorded}):
        assert ocr.recognize(scan, fake_config().ocr, 1000) == recorded
        assert ocr.recognize(scan, fake_config().ocr, 8).text == "recorded"
        assert ocr.recognize(photo, fake_config().ocr, 1000) == ocr.Recognized("")
        with pytest.raises(ocr.OcrError, match="unreadable"):
            ocr.recognize(tmp_path / "gone.png", fake_config().ocr, 1000)
    assert calls(fake_ocr) == []
    assert ocr.recognize(scan, fake_config().ocr, 1000).text == RECEIPT


@pytest.mark.parametrize("mode", ["fail", "unavailable"])
def test_a_failing_helper_raises_ocr_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    monkeypatch.setenv("FAKE_OCR_MODE", mode)
    with pytest.raises(ocr.OcrError, match="text recognition failed"):
        ocr.recognize(scan, fake_config().ocr, 1000)


def test_a_helper_that_hangs_is_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    monkeypatch.setenv("FAKE_OCR_MODE", "hang")
    monkeypatch.setattr(ocr, "TIMEOUT", 0.1)  # the fake sleeps for a minute
    with pytest.raises(ocr.OcrError, match="longer than"):
        ocr.recognize(scan, fake_config().ocr, 1000)


def test_missing_osascript_raises_ocr_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    monkeypatch.setattr(ocr, "OSASCRIPT", str(tmp_path / "no-osascript"))
    with pytest.raises(ocr.OcrError, match="cannot run"):
        ocr.recognize(scan, fake_config().ocr, 1000)


def test_huge_or_unreadable_files_are_not_read(
    tmp_path: Path, fake_ocr: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan = make_scan(tmp_path / "scan.png", RECEIPT)
    monkeypatch.setattr(ocr, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(ocr.OcrError, match="too large"):
        ocr.recognize(scan, fake_config().ocr, 1000)
    with pytest.raises(ocr.OcrError, match="unreadable"):
        ocr.recognize(tmp_path / "gone.png", fake_config().ocr, 1000)
    assert calls(fake_ocr) == []


def test_availability_lists_the_languages_once(fake_ocr: Path) -> None:
    found = ocr.availability()
    assert found.available and "pt-BR" in found.languages
    assert ocr.availability() is found
    assert len(calls(fake_ocr)) == 1


def test_availability_reports_why_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "unavailable")
    found = ocr.availability()
    assert not found.available and found.reason and "unavailable" in found.reason

    ocr.availability.cache_clear()
    monkeypatch.setenv("FAKE_OCR_MODE", "hang")
    monkeypatch.setattr(ocr, "PROBE_TIMEOUT", 0.1)  # the fake sleeps for a minute
    assert "longer than" in (ocr.availability().reason or "")

    ocr.availability.cache_clear()
    monkeypatch.setattr(ocr, "HELPER", tmp_path / "missing.js")
    assert "missing" in (ocr.availability().reason or "")

    ocr.availability.cache_clear()
    monkeypatch.setattr(ocr, "OSASCRIPT", str(tmp_path / "no-osascript"))
    assert "not found" in (ocr.availability().reason or "")

    ocr.availability.cache_clear()
    monkeypatch.setattr(ocr, "OSASCRIPT", "/usr/bin/false")
    monkeypatch.setattr(ocr, "HELPER", Path(ocr.__file__))
    assert ocr.availability().reason == "macOS text recognition unavailable: exit code 1"


# --- Context: what is read, scanned and sent --------------------------------------


@pytest.mark.parametrize("name", ["scan.pdf", "receipt.jpg", "Screenshot 2026-03-14.png"])
def test_scanned_documents_are_read_as_text(tmp_path: Path, name: str) -> None:
    ctx = context_for(make_scan(tmp_path / name, BILL))
    assert ctx.text == BILL
    assert ctx.metadata.text_source == "ocr"
    assert ctx.visual is None
    # An image's text goes with how it sits in the picture; a PDF's does not.
    picture = ctx.metadata.picture
    if name.endswith(".pdf"):
        assert picture is None
    else:
        assert picture is not None and (picture.width, picture.height, picture.lines) == (
            1200,
            1600,
            1,
        )


# A page of long lines: what the fake helper reports looks like a scanned document.
PAGE = "\n".join(f"{BILL[:45]} line {i}" for i in range(12))


def test_with_vision_a_picture_with_words_sends_the_image_and_its_text(tmp_path: Path) -> None:
    cfg = fake_config(vision={"enabled": True})
    for name in ("flyer.jpg", "Screenshot 2026-03-14.png"):
        ctx = context_for(make_scan(tmp_path / name, RECEIPT), cfg)
        assert ctx.text == RECEIPT and ctx.visual == "image"
        assert ctx.metadata.text_source == "ocr" and ctx.metadata.picture is not None


def test_with_vision_a_document_sends_its_text_only(tmp_path: Path) -> None:
    cfg = fake_config(vision={"enabled": True})
    page = context_for(make_scan(tmp_path / "IMG_2041.jpg", PAGE), cfg)
    assert page.text == PAGE and page.visual is None
    assert page.metadata.picture is not None and page.metadata.picture.lines == 12
    # A scanned PDF with text is always a document, however little text it has.
    scan = context_for(make_scan(tmp_path / "scan.pdf", RECEIPT), cfg)
    assert scan.text == RECEIPT and scan.visual is None and scan.metadata.picture is None


def test_without_vision_a_picture_with_words_sends_its_text(tmp_path: Path) -> None:
    ctx = context_for(make_scan(tmp_path / "flyer.jpg", RECEIPT))
    assert ctx.text == RECEIPT and ctx.visual is None and ctx.metadata.picture is not None


def test_a_photo_falls_back_to_vision_or_metadata(tmp_path: Path, fake_ocr: Path) -> None:
    photo = make_image(tmp_path / "IMG_0001.jpg", 4000, 3000)
    with_vision = context_for(photo, fake_config(vision={"enabled": True}))
    assert with_vision.text is None and with_vision.visual == "image"
    without = context_for(photo)
    assert without.text is None and without.visual is None
    assert without.metadata == FileMetadata(content="no extractor for this format")
    assert len(recognitions(fake_ocr)) == 2


def test_too_little_text_is_not_a_document(tmp_path: Path) -> None:
    sign = make_scan(tmp_path / "IMG_0002.jpg", "EXIT")
    ctx = context_for(sign, fake_config(vision={"enabled": True}))
    assert ctx.text is None and ctx.visual == "image" and ctx.metadata.text_source is None
    scan = make_scan(tmp_path / "scan.pdf", "Page 1")
    ctx = context_for(scan, fake_config(vision={"enabled": True}))
    assert ctx.text is None and ctx.visual == "pdf"
    assert ctx.metadata.content == "no readable text"
    # The threshold is a setting.
    assert context_for(sign, fake_config(ocr={"min_chars": 4})).text == "EXIT"


@pytest.mark.parametrize("text", [f"aws key {AWS_KEY}", f"{BILL}\npassword = hunter2-hunter2"])
def test_a_secret_in_a_screenshot_makes_it_sensitive(tmp_path: Path, text: str) -> None:
    shot = make_scan(tmp_path / "Screenshot.png", text)
    ctx = context_for(shot, fake_config(vision={"enabled": True}))
    assert ctx.sensitive and ctx.text is None and ctx.visual is None
    assert ctx.metadata.sensitive is not None


def test_a_secret_in_too_little_text_still_counts(tmp_path: Path) -> None:
    shot = make_scan(tmp_path / "key.png", AWS_KEY)
    assert context_for(shot, fake_config(ocr={"min_chars": 100})).sensitive


def test_ocr_off_reads_nothing(tmp_path: Path, fake_ocr: Path) -> None:
    shot = make_scan(tmp_path / "Screenshot.png", f"aws key {AWS_KEY}")
    ctx = context_for(shot, fake_config(ocr={"enabled": False}, vision={"enabled": True}))
    assert not ctx.sensitive and ctx.text is None and ctx.visual == "image"
    assert calls(fake_ocr) == []


def test_never_send_and_sensitive_names_are_never_read(tmp_path: Path, fake_ocr: Path) -> None:
    cfg = fake_config(never_send=["*.png"])
    ctx = context_for(make_scan(tmp_path / "scan.png", BILL), cfg)
    assert ctx.text is None and ctx.metadata.content == "withheld (never_send)"
    ctx = context_for(make_scan(tmp_path / "id_rsa.png", BILL))
    assert ctx.sensitive
    assert calls(fake_ocr) == []


def test_a_pdf_with_a_text_layer_is_not_recognised(tmp_path: Path, fake_ocr: Path) -> None:
    ctx = context_for(make_pdf(tmp_path / "bill.pdf", BILL))
    assert ctx.text == BILL and ctx.metadata.text_source is None
    assert calls(fake_ocr) == []


def test_the_text_is_cut_to_max_chars_per_file(tmp_path: Path) -> None:
    cfg = fake_config(provider={"max_chars_per_file": 50})
    ctx = context_for(make_scan(tmp_path / "scan.pdf", BILL * 3), cfg)
    assert ctx.text == (BILL * 3)[:50]


def test_a_failure_falls_back_and_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_OCR_MODE", "fail")
    ctx = context_for(make_scan(tmp_path / "scan.pdf", BILL), fake_config(vision={"enabled": True}))
    assert ctx.text is None and ctx.visual == "pdf"
    assert ctx.metadata.ocr_error == "text recognition failed: text recognition failed"
    assert ctx.metadata.extraction_error is None


def test_settings_reach_the_helper(tmp_path: Path, fake_ocr: Path) -> None:
    make_blank_pdf(tmp_path / "blank.pdf", 5)
    cfg = fake_config(language="pt-BR", ocr={"max_pages": 4})
    context_for(tmp_path / "blank.pdf", cfg)
    [call] = recognitions(fake_ocr)
    assert call[5:] == ["4", str(ocr.MAX_PX), "pt-BR", "en-US"]


def test_the_prompt_says_the_text_came_from_ocr(tmp_path: Path) -> None:
    ctx = context_for(make_scan(tmp_path / "scan.pdf", BILL))
    message = user_message(ctx)
    assert BILL in message and "OCR" in message and "scanned pages" in message
    assert '"picture"' not in message
    assert "OCR" not in user_message(context_for(make_pdf(tmp_path / "bill.pdf", BILL)))


def test_the_prompt_says_an_images_text_was_found_in_a_picture(tmp_path: Path) -> None:
    flyer = make_scan(tmp_path / "flyer.png", "BIG SALE\nSaturday 10am to 4pm at the old town hall")
    message = user_message(context_for(flyer))
    meta = json.loads(message.split("File metadata:\n", 1)[1].split("\n\n", 1)[0])
    assert meta["text_source"] == "recognised inside this image file (OCR; may have typos)"
    assert meta["picture"] == {
        "size_px": "1200 x 1600",
        "text_lines": 2,
        "text_covers": "3% of the image",
    }
    assert "<file_excerpt>\nBIG SALE" in message


# --- Classification cache ---------------------------------------------------------


class Cache:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    def cached_answer(self, sha256: str, rules_key: str, *, max_age_days: int) -> str | None:
        return self.rows.get((sha256, rules_key))

    def cache_answer(self, sha256: str, rules_key: str, answer_json: str) -> None:
        self.rows[(sha256, rules_key)] = answer_json


def read_ctx(text_source: str | None = "ocr") -> FileContext:
    meta = FileMetadata(text_source="ocr") if text_source else FileMetadata()
    return FileContext(Path("/in/scan.pdf"), "scan", 10, ".pdf", text=BILL, metadata=meta)


def test_ocr_answers_have_their_own_cache_key() -> None:
    provider = FakeProvider(default=ANSWER)
    cfg = fake_config()
    text_key = rules_key(provider, RULES, cfg)
    key = ocr_key(text_key, cfg)
    assert len({text_key, key, vision_key(text_key, cfg)}) == 3
    assert answer_key(read_ctx(), text_key, cfg, vision=False) == key
    assert answer_key(read_ctx(None), text_key, cfg, vision=False) == text_key
    # An image sent with its recognised text depends on the OCR and the vision settings.
    assert answer_key(read_ctx(), text_key, cfg, vision=True) == vision_key(key, cfg)
    assert answer_key(read_ctx(None), text_key, cfg, vision=True) == vision_key(text_key, cfg)


@pytest.mark.parametrize(
    "change", [{"languages": ["pt-BR"]}, {"max_pages": 1}], ids=["languages", "pages"]
)
def test_ocr_settings_that_change_the_text_change_the_key(change: dict[str, Any]) -> None:
    provider = FakeProvider(default=ANSWER)
    base, other = fake_config(), fake_config(ocr=change)
    text_key = rules_key(provider, RULES, base)
    assert rules_key(provider, RULES, other) == text_key  # other files keep their answers
    assert ocr_key(text_key, other) != ocr_key(text_key, base)


def test_classify_caches_ocr_answers_under_the_ocr_key(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(default=ANSWER)
    cache = Cache()
    cfg = fake_config()

    def run(config: Config) -> Any:
        return classify_all(
            [read_ctx()], provider, RULES, config, sleep=lambda _s: None, cache=cache
        )

    run(cfg)
    assert list(cache.rows) == [("scan", ocr_key(rules_key(provider, RULES, cfg), cfg))]
    assert run(cfg).cached == 1
    run(fake_config(ocr={"languages": ["fr-FR"]}))
    assert len(provider.calls) == 2


# --- A whole run --------------------------------------------------------------------


def test_a_scan_and_a_receipt_are_classified_and_renamed_from_their_text(
    downloads: Path, cfg: Config
) -> None:
    make_old(make_scan(downloads / "scan0001.pdf", BILL))
    make_old(make_scan(downloads / "IMG_2041.jpg", RECEIPT))
    make_old(make_image(downloads / "IMG_2042.jpg", 4000, 3000))
    provider = FakeProvider(default=ANSWER)  # reads text only
    pipeline.run(cfg, provider, simulate=False)
    sent = {c.path.name: c for c in provider.calls}
    assert sent["scan0001.pdf"].text == BILL and sent["IMG_2041.jpg"].text == RECEIPT
    assert sent["IMG_2042.jpg"].text is None
    finance = sorted(p.name for p in (cfg.output_root / "Finance").iterdir())
    # Text from OCR is content like any other, so the documents are renamed; the photo is not.
    assert "IMG_2042.jpg" in finance
    assert "scan0001.pdf" not in finance and "IMG_2041.jpg" not in finance
    assert sum(name.endswith("power-bill.pdf") for name in finance) == 1


# --- Commands -------------------------------------------------------------------------


def test_check_config_reports_ocr(tmp_path: Path, downloads: Path) -> None:
    on = write_config(tmp_path, downloads, extra="language: pt-BR\n")
    result = runner.invoke(app, ["check-config", "-c", str(on)])
    # check-config speaks the configuration's language (#141)
    assert (
        "  ocr: ligado, neste Mac (pt-BR, en-US; as 3 primeiras páginas de um PDF escaneado)"
        in result.stdout
    )
    english = write_config(tmp_path, downloads, extra="ocr: {languages: [pt-BR, en-US]}\n")
    result = runner.invoke(app, ["check-config", "-c", str(english)])
    assert "  ocr: on, on this Mac (pt-BR, en-US; first 3 pages of a scanned PDF)" in result.stdout
    off = write_config(tmp_path, downloads, extra="ocr: {enabled: false}\n")
    assert "  ocr: off" in runner.invoke(app, ["check-config", "-c", str(off)]).stdout


def test_check_config_warns_when_ocr_cannot_run(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    odd = write_config(tmp_path, downloads, extra="ocr: {languages: [en-US, xx-XX]}\n")
    result = runner.invoke(app, ["check-config", "-c", str(odd)])
    assert result.exit_code == 0
    assert "macOS cannot recognise xx-XX" in result.stderr

    monkeypatch.setenv("FAKE_OCR_MODE", "unavailable")
    ocr.availability.cache_clear()
    result = runner.invoke(app, ["check-config", "-c", str(odd)])
    assert result.exit_code == 0
    assert "  ocr: unavailable" in result.stdout
    assert "OCR is on but cannot run here" in result.stderr


def test_inspect_shows_where_the_text_comes_from(tmp_path: Path, downloads: Path) -> None:
    config = write_config(tmp_path, downloads)
    scan = make_scan(downloads / "scan.pdf", BILL)
    photo = make_image(downloads / "IMG_0001.jpg", 4000, 3000)
    bill = make_pdf(downloads / "bill.pdf", BILL)
    result = runner.invoke(app, ["inspect", "-c", str(config), str(scan), str(photo), str(bill)])
    assert result.exit_code == 0, result.output
    assert f"text:   {len(BILL)} characters, recognised by OCR on this Mac\n" in result.stdout
    assert "text:   none sent (no extractor for this format)\n" in result.stdout
    assert f"text:   {len(BILL)} characters\n" in result.stdout
    data = json.loads(
        runner.invoke(app, ["inspect", "--json", "-c", str(config), str(scan)]).stdout
    )
    assert data[0]["text"] == {"chars": len(BILL), "source": "ocr", "note": None}
    assert BILL not in result.stdout  # the text itself is never shown


def test_inspect_explains_sensitive_and_failed_files(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads)
    shot = make_scan(downloads / "Screenshot.png", f"aws key {AWS_KEY}")
    result = runner.invoke(app, ["inspect", "-c", str(config), str(shot)])
    assert (
        "text:   none sent (sensitive (content: looks like AWS access key); nothing is sent)"
        in result.stdout
    )
    monkeypatch.setenv("FAKE_OCR_MODE", "fail")
    scan = make_scan(downloads / "scan.pdf", BILL)
    result = runner.invoke(app, ["inspect", "-c", str(config), str(scan)])
    assert "no readable text; OCR failed: text recognition failed" in result.stdout
    photo = make_image(downloads / "IMG_0001.jpg", 10, 10)
    result = runner.invoke(app, ["inspect", "-c", str(config), str(photo)])
    assert "none sent (no extractor for this format; OCR failed" in result.stdout


def test_inspect_without_a_configuration_reads_no_text(downloads: Path, fake_ocr: Path) -> None:
    scan = make_scan(downloads / "scan.pdf", BILL)
    result = runner.invoke(app, ["inspect", str(scan)])
    assert "text:   none sent (no configuration; not read)" in result.stdout
    assert calls(fake_ocr) == []


def test_doctor_reports_ocr(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads, extra="ocr: {languages: [en-US, xx-XX]}\n")
    report = doctor.build_report(doctor.Options(config_path=config))
    assert "ocr: on, available (en-US, xx-XX; not supported here: xx-XX)" in report
    monkeypatch.setenv("FAKE_OCR_MODE", "unavailable")
    ocr.availability.cache_clear()
    report = doctor.build_report(doctor.Options(config_path=config))
    assert "ocr: on, unavailable (macOS text recognition unavailable" in report
    off = write_config(tmp_path, downloads, extra="ocr: {enabled: false}\n")
    assert "ocr: off" in doctor.build_report(doctor.Options(config_path=off))
