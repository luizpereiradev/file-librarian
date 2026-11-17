"""Vision (RF-05, RF-26): preparing images and scanned PDFs, and who gets them.

Images are converted by the fake sips from tests/fake_sips.py; nothing here
runs the real /usr/bin/sips or reaches the network.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter

from file_librarian.classifier import classify_all, rules_key, vision_key
from file_librarian.config import Config, ConfigError, VisionConfig, parse_config
from file_librarian.context import build_context
from file_librarian.extractors import visual
from file_librarian.extractors.visual import (
    VisionError,
    prepare,
    prepare_image,
    prepare_pdf,
    strip_jpeg_metadata,
)
from file_librarian.hashing import file_sha256
from file_librarian.models import (
    Attachment,
    CategoryRule,
    Classification,
    Decision,
    FileContext,
    FileStatus,
    Rules,
)
from file_librarian.planner import plan
from file_librarian.providers.anthropic import AnthropicProvider, user_content
from file_librarian.providers.base import Provider, Usage
from file_librarian.providers.fake import FakeProvider
from file_librarian.scanner import Candidate
from tests._helpers import make_blank_pdf, make_image, make_pdf

RULES = Rules(categories=(CategoryRule("Images"), CategoryRule("Other")), language="en")
ANSWER = Classification(
    category="Images",
    suggested_name="beach at sunset",
    description="A photo of a beach at sunset.",
    confidence=0.95,
)
EVERYTHING = frozenset({"image/jpeg", "application/pdf"})


def config(**overrides: Any) -> Config:
    raw: dict[str, Any] = {"provider": {"name": "fake", "model": "fixed"}}
    return parse_config(raw | overrides)


def vision_on(**settings: Any) -> Config:
    return config(vision={"enabled": True} | settings)


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def context_for(path: Path, cfg: Config) -> FileContext:
    candidate = Candidate(path, file_sha256(path), path.stat().st_size, 0.0, is_unit=False)
    return build_context(candidate, cfg)


# --- Configuration ----------------------------------------------------------------


def test_vision_is_off_by_default() -> None:
    v = config().vision
    assert (v.enabled, v.max_image_px, v.max_pdf_pages) == (False, 1568, 3)


@pytest.mark.parametrize(
    "bad", [{"max_image_px": 100}, {"max_image_px": 4000}, {"max_pdf_pages": 0}, {"colour": 1}]
)
def test_vision_limits_are_validated(bad: dict[str, Any]) -> None:
    with pytest.raises(ConfigError):
        config(vision=bad)


def test_old_scanned_pdf_max_pages_key_still_loads() -> None:
    assert config(limits={"scanned_pdf_max_pages": 5}).vision.max_pdf_pages == 5
    moved = config(limits={"scanned_pdf_max_pages": 5}, vision={"enabled": True})
    assert (moved.vision.enabled, moved.vision.max_pdf_pages) == (True, 5)
    explicit = config(limits={"scanned_pdf_max_pages": 5}, vision={"max_pdf_pages": 2})
    assert explicit.vision.max_pdf_pages == 2
    with pytest.raises(ConfigError):  # still validated where it lands
        config(limits={"scanned_pdf_max_pages": 0})
    with pytest.raises(ConfigError):  # a vision section that is not a mapping is reported
        config(limits={"scanned_pdf_max_pages": 5}, vision="yes")


# --- Context ----------------------------------------------------------------------


def test_images_and_scanned_pdfs_are_visual_only_with_vision_on(tmp_path: Path) -> None:
    photo = make_image(tmp_path / "IMG_0001.HEIC", 4032, 3024)
    scan = make_blank_pdf(tmp_path / "scan.pdf", 2)
    text_pdf = make_pdf(tmp_path / "bill.pdf", "Electricity bill")
    on, off = vision_on(), config()
    assert context_for(photo, on).visual == "image"
    assert context_for(scan, on).visual == "pdf"
    assert context_for(text_pdf, on).visual is None
    assert context_for(photo, off).visual is None
    assert context_for(scan, off).visual is None


def test_never_send_damaged_and_sensitive_files_are_not_visual(tmp_path: Path) -> None:
    cfg = vision_on()
    photo = make_image(tmp_path / "passport.jpg", 800, 600)
    withheld = context_for(photo, config(vision={"enabled": True}, never_send=["passport*"]))
    assert withheld.visual is None
    assert withheld.metadata == {"content": "withheld (never_send)"}
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really")
    assert context_for(broken, cfg).visual is None
    key = make_image(tmp_path / "server.pem", 10, 10)
    assert context_for(key, cfg).sensitive


# --- Preparing images ---------------------------------------------------------------


def test_large_image_is_downscaled_to_a_metadata_free_jpeg(tmp_path: Path, fake_sips: Path) -> None:
    data = prepare_image(make_image(tmp_path / "photo.heic", 4032, 3024), 1568)
    assert data.startswith(b"\xff\xd8\xff\xe0")  # JPEG, JFIF header kept
    assert b"1568x1176" in data
    assert b"Exif" not in data and b"GPS" not in data and b"camera comment" not in data
    assert data.endswith(b"scan-data\xff\x00more\xff\xd9")
    probe, convert = calls(fake_sips)
    assert probe[:4] == ["-g", "pixelWidth", "-g", "pixelHeight"]
    assert convert[:6] == ["-s", "format", "jpeg", "-s", "formatOptions", "80"]
    assert convert[6:8] == ["-Z", "1568"]
    assert Path(convert[8]).is_absolute()


def test_small_image_is_never_enlarged(tmp_path: Path, fake_sips: Path) -> None:
    data = prepare_image(make_image(tmp_path / "icon.png", 640, 480), 1568)
    assert b"640x480" in data
    assert "-Z" not in calls(fake_sips)[1]


def test_relative_paths_are_made_absolute(
    tmp_path: Path, fake_sips: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_image(tmp_path / "-Z.png", 10, 10)  # a name that looks like an option
    monkeypatch.chdir(tmp_path)
    prepare_image(Path("-Z.png"), 1568)
    assert all(
        Path(call[-1] if call[0] == "-g" else call[-3]).is_absolute() for call in calls(fake_sips)
    )


def test_unreadable_images_raise_vision_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_image = tmp_path / "fake.png"
    not_image.write_bytes(b"\x89PNG but not really")
    with pytest.raises(VisionError, match="not a readable image"):
        prepare_image(not_image, 1568)
    with pytest.raises(VisionError, match="unreadable"):
        prepare_image(tmp_path / "missing.png", 1568)
    big = make_image(tmp_path / "huge.png", 10, 10)
    monkeypatch.setattr(visual, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(VisionError, match="too large to convert"):
        prepare_image(big, 1568)


def test_sips_that_writes_nothing_or_fails_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image = make_image(tmp_path / "a.png", 10, 10)
    monkeypatch.setenv("FAKE_SIPS_SILENT", "1")
    with pytest.raises(VisionError, match="could not convert"):
        prepare_image(image, 1568)
    monkeypatch.setattr(visual, "SIPS", str(tmp_path / "no-such-sips"))
    with pytest.raises(VisionError, match="cannot run"):
        prepare_image(image, 1568)


def test_converted_image_over_the_api_limit_is_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(visual, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(VisionError, match="too large to send"):
        prepare_image(make_image(tmp_path / "a.png", 10, 10), 1568)


def seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def test_strip_jpeg_metadata_keeps_only_what_decodes_the_image() -> None:
    jpeg = (
        b"\xff\xd8"
        + seg(0xE0, b"JFIF\x00")
        + seg(0xE1, b"Exif\x00\x00GPS")
        + seg(0xE2, b"ICC_PROFILE")
        + seg(0xED, b"Photoshop IPTC")
        + seg(0xFE, b"comment")
        + b"\xff\xff"  # fill byte
        + seg(0xDB, b"tables")
        + b"\xff\xd0"  # restart marker, no length
        + seg(0xC0, b"frame")
        + seg(0xDA, b"scan")
        + b"pixels\xff\xd9"
    )
    out = strip_jpeg_metadata(jpeg)
    assert out == (
        b"\xff\xd8"
        + seg(0xE0, b"JFIF\x00")
        + seg(0xDB, b"tables")
        + b"\xff\xd0"
        + seg(0xC0, b"frame")
        + seg(0xDA, b"scan")
        + b"pixels\xff\xd9"
    )


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG",  # not a JPEG
        b"\xff\xd8\x00\x00",  # no marker where one belongs
        b"\xff\xd8" + b"\xff\xe1\x00\x40Exif",  # segment longer than the file
        b"\xff\xd8" + b"\xff\xe1\x00\x01",  # impossible length
        b"\xff\xd8" + seg(0xE0, b"JFIF"),  # never reaches the image data
        b"\xff\xd8\xff\xe0\x00",  # cut inside a length
    ],
)
def test_strip_jpeg_metadata_rejects_malformed_files(data: bytes) -> None:
    with pytest.raises(VisionError):
        strip_jpeg_metadata(data)


# --- Preparing PDFs -------------------------------------------------------------------


def test_scanned_pdf_is_cut_to_its_first_pages(tmp_path: Path) -> None:
    data, pages = prepare_pdf(make_blank_pdf(tmp_path / "scan.pdf", 7), 3)
    assert pages == 3
    assert len(PdfReader(io.BytesIO(data)).pages) == 3
    short, pages = prepare_pdf(make_blank_pdf(tmp_path / "one.pdf", 1), 3)
    assert pages == 1 and short.startswith(b"%PDF")


def test_pdfs_that_cannot_be_trimmed_raise_vision_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(VisionError, match="cannot trim"):
        prepare_pdf(broken, 3)

    locked = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("secret-password-for-test")
    with locked.open("wb") as fh:
        writer.write(fh)
    with pytest.raises(VisionError, match="password"):
        prepare_pdf(locked, 3)

    empty = tmp_path / "empty.pdf"
    with empty.open("wb") as fh:
        PdfWriter().write(fh)
    with pytest.raises(VisionError, match="without pages"):
        prepare_pdf(empty, 3)

    monkeypatch.setattr(visual, "MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(VisionError, match="too large"):
        prepare_pdf(make_blank_pdf(tmp_path / "scan.pdf", 1), 3)


def test_prepare_dispatches_on_the_visual_kind(tmp_path: Path) -> None:
    settings = VisionConfig(enabled=True, max_image_px=512, max_pdf_pages=2)
    photo = make_image(tmp_path / "a.webp", 1024, 256)
    image = prepare(FileContext(photo, "x", 1, ".webp", visual="image"), settings)
    assert image.media_type == "image/jpeg" and b"512x128" in image.data
    scan = make_blank_pdf(tmp_path / "scan.pdf", 5)
    doc = prepare(FileContext(scan, "x", 1, ".pdf", visual="pdf"), settings)
    assert (doc.media_type, doc.pages) == ("application/pdf", 2)
    with pytest.raises(VisionError):
        prepare(FileContext(scan, "x", 1, ".pdf"), settings)


# --- Classifier ---------------------------------------------------------------------


class Cache:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    def cached_answer(self, sha256: str, rules_key: str, *, max_age_days: int) -> str | None:
        return self.rows.get((sha256, rules_key))

    def cache_answer(self, sha256: str, rules_key: str, answer_json: str) -> None:
        self.rows[(sha256, rules_key)] = answer_json


def fake_prepare(ctx: FileContext, settings: VisionConfig) -> Attachment:
    return Attachment("image/jpeg", b"jpeg-bytes")


def failing_prepare(ctx: FileContext, settings: VisionConfig) -> Attachment:
    raise VisionError("sips could not convert the image")


def image_ctx(name: str = "IMG_0001.jpg") -> FileContext:
    return FileContext(Path("/in") / name, name, 10, ".jpg", visual="image")


def classify(ctxs: list[FileContext], provider: Provider, cfg: Config, **kw: Any) -> Any:
    kw.setdefault("prepare", fake_prepare)
    return classify_all(ctxs, provider, RULES, cfg, sleep=lambda _s: None, **kw)


def test_provider_that_reads_images_gets_the_attachment() -> None:
    provider = FakeProvider(default=ANSWER, attachments=EVERYTHING)
    result = classify([image_ctx()], provider, vision_on())
    [sent] = provider.calls
    assert sent.attachment == Attachment("image/jpeg", b"jpeg-bytes")
    [decision] = result.decisions
    assert decision.context.vision_used and decision.context.attachment is None


def test_text_only_provider_falls_back_to_metadata() -> None:
    provider = FakeProvider(default=ANSWER)  # attachments: none
    result = classify([image_ctx()], provider, vision_on(), prepare=failing_prepare)
    assert provider.calls[0].attachment is None
    assert not result.decisions[0].context.vision_used


def test_provider_reading_images_only_gets_no_pdf() -> None:
    provider = FakeProvider(default=ANSWER, attachments=frozenset({"image/jpeg"}))
    scan = FileContext(Path("/in/scan.pdf"), "s", 10, ".pdf", visual="pdf")
    classify([scan], provider, vision_on())
    assert provider.calls[0].attachment is None


def test_a_file_that_cannot_be_prepared_is_classified_from_metadata() -> None:
    provider = FakeProvider(default=ANSWER, attachments=EVERYTHING)
    cache = Cache()
    cfg = vision_on()
    result = classify([image_ctx()], provider, cfg, prepare=failing_prepare, cache=cache)
    assert provider.calls[0].attachment is None
    assert result.decisions[0].classification == ANSWER
    assert not result.decisions[0].context.vision_used
    # Cached as a text-only answer, so a later run with a working sips asks again.
    assert list(cache.rows) == [("IMG_0001.jpg", rules_key(provider, RULES, cfg))]


def test_vision_answers_are_cached_apart_from_text_answers() -> None:
    provider = FakeProvider(default=ANSWER, attachments=EVERYTHING)
    cache = Cache()
    on, off = vision_on(), config()
    classify([image_ctx()], provider, on, cache=cache)
    assert list(cache.rows) == [("IMG_0001.jpg", vision_key(rules_key(provider, RULES, on), on))]

    # Vision turned off: the context is no longer visual, the vision answer is not reused.
    classify(
        [FileContext(Path("/in/IMG_0001.jpg"), "IMG_0001.jpg", 10, ".jpg")],
        provider,
        off,
        cache=cache,
    )
    assert len(provider.calls) == 2 and len(cache.rows) == 2

    # On again: the cached vision answer comes back, still marked as seen.
    again = classify([image_ctx()], provider, on, cache=cache)
    assert again.cached == 1 and len(provider.calls) == 2
    assert again.decisions[0].context.vision_used

    # Other limits: another key, a fresh answer.
    classify([image_ctx()], provider, vision_on(max_image_px=800), cache=cache)
    assert len(provider.calls) == 3


class Billed(FakeProvider):
    """Bills like Haiku with a ~2,500-token image; stops at the per-run cap."""

    def __init__(self) -> None:
        super().__init__(default=ANSWER, attachments=EVERYTHING)

    def cost_usd(self, usage: Usage) -> float:
        return usage.input_tokens * 0.10 / 1_000_000

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.last_usage = Usage(3_000_000 if context.attachment else 1_000, 0, [])
        return super().classify(context, rules)


def test_billed_image_tokens_count_toward_the_cost_cap() -> None:
    provider = Billed()
    cfg = config(vision={"enabled": True}, limits={"max_cost_usd_per_run": 0.5})
    result = classify([image_ctx("a.jpg"), image_ctx("b.jpg"), image_ctx("c.jpg")], provider, cfg)
    assert len(result.decisions) == 2 and [c.path.name for c in result.deferred] == ["c.jpg"]
    assert result.cost_usd == pytest.approx(0.6)


# --- Planner --------------------------------------------------------------------------


def test_an_image_the_model_saw_is_renamed_one_it_did_not_keeps_its_name(
    tmp_path: Path,
) -> None:
    cfg = config(
        inputs=[str(tmp_path / "in")],
        output_root=str(tmp_path / "out"),
        categories=[{"name": "Images"}, {"name": "Other"}],
    )
    seen = FileContext(tmp_path / "in" / "IMG_0001.jpg", "a", 1, ".jpg", vision_used=True)
    unseen = FileContext(tmp_path / "in" / "IMG_0002.jpg", "b", 1, ".jpg")
    answer = ANSWER.model_copy(update={"document_date": None})
    planned = plan([Decision(seen, answer), Decision(unseen, answer)], cfg)
    first, second = planned.items
    assert first.status is FileStatus.ORGANIZED and first.renamed
    assert first.destination is not None and first.destination.name.endswith("_beach-at-sunset.jpg")
    assert second.destination is not None and second.destination.name == "IMG_0002.jpg"


# --- Anthropic request ----------------------------------------------------------------


def blocks(ctx: FileContext) -> list[dict[str, Any]]:
    content = user_content(ctx)
    assert isinstance(content, list)
    return content


def test_anthropic_declares_what_it_reads() -> None:
    assert AnthropicProvider.attachments == EVERYTHING


def test_image_goes_first_as_a_base64_image_block() -> None:
    ctx = FileContext(
        Path("/in/IMG_0001.jpg"),
        "x",
        10,
        ".jpg",
        metadata={"content": "no extractor for this format"},
        attachment=Attachment("image/jpeg", b"\xff\xd8jpeg"),
    )
    image, text = blocks(ctx)
    assert image == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(b"\xff\xd8jpeg").decode(),
        },
    }
    assert text["type"] == "text"
    assert "The attached image is this file" in text["text"]
    assert "untrusted data" in text["text"]
    assert "no extractor" not in text["text"]
    assert "No text content is available" not in text["text"]


@pytest.mark.parametrize(("pages", "words"), [(1, "first page"), (3, "first 3 pages")])
def test_scanned_pdf_goes_as_a_document_block(pages: int, words: str) -> None:
    ctx = FileContext(
        Path("/in/scan.pdf"),
        "x",
        10,
        ".pdf",
        attachment=Attachment("application/pdf", b"%PDF-1.7", pages=pages),
    )
    doc, text = blocks(ctx)
    assert doc["type"] == "document"
    assert doc["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(doc["source"]["data"]) == b"%PDF-1.7"
    assert f"the {words} of this PDF" in text["text"]


def test_without_attachment_the_message_stays_plain_text() -> None:
    assert isinstance(user_content(FileContext(Path("/in/a.txt"), "x", 1, ".txt", text="hi")), str)
