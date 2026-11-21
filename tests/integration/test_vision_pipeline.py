"""Vision end to end: a photo and a scanned PDF through simulate and run, offline.

The fake sips (tests/fake_sips.py) converts the synthetic photo; the
anthropic provider talks to a fake client, so the real request body is
checked without any network.
"""

from __future__ import annotations

import base64
import io
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader

from file_librarian import pipeline
from file_librarian.classifier import classify_all
from file_librarian.config import ProviderConfig
from file_librarian.extractors.visual import VisionError
from file_librarian.models import Attachment, CategoryRule, Classification, FileStatus, Rules
from file_librarian.providers.anthropic import AnthropicProvider
from file_librarian.providers.fake import FakeProvider
from file_librarian.providers.prompt import user_message
from tests._helpers import (
    FakeClient,
    context_for,
    fake_config,
    make_blank_pdf,
    make_image,
    make_old,
    make_scan,
    reply,
)

RULES = Rules(categories=(CategoryRule("Images"), CategoryRule("Other")), language="en")

pytestmark = pytest.mark.integration

PHOTO = Classification(
    category="Images",
    suggested_name="beach at sunset",
    description="A beach at sunset.",
    confidence=0.95,
)
SCAN = Classification(
    category="Finance",
    suggested_name="water bill",
    description="A scanned water bill.",
    confidence=0.95,
    document_date=date(2026, 10, 5),
)


def test_photo_and_scan_are_seen_named_and_filed(downloads: Path) -> None:
    make_old(make_image(downloads / "IMG_0001.HEIC", 4032, 3024))
    make_old(make_blank_pdf(downloads / "scan0001.pdf", 6))
    cfg = fake_config(downloads, vision={"enabled": True, "max_pdf_pages": 2})
    provider = FakeProvider(
        {"IMG_0001.HEIC": PHOTO, "scan0001.pdf": SCAN},
        attachments=frozenset({"image/jpeg", "application/pdf"}),
    )
    outcome = pipeline.run(cfg, provider, simulate=False)

    sent = {c.path.name: c.attachment for c in provider.calls}
    photo, scan = sent["IMG_0001.HEIC"], sent["scan0001.pdf"]
    assert photo is not None and b"1568x1176" in photo.data and b"Exif" not in photo.data
    assert scan is not None and scan.pages == 2
    assert len(PdfReader(io.BytesIO(scan.data)).pages) == 2

    out = cfg.output_root
    filed = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert filed[0] == "Finance/2026-10-05_water-bill.pdf"
    assert filed[1].startswith("Images/") and filed[1].endswith("_beach-at-sunset.HEIC")
    assert all(i.status is FileStatus.ORGANIZED for i in outcome.plan.items)


def test_with_vision_off_nothing_is_attached_and_names_are_kept(downloads: Path) -> None:
    make_old(make_image(downloads / "IMG_0001.HEIC", 4032, 3024))
    cfg = fake_config(downloads)
    provider = FakeProvider(
        {"IMG_0001.HEIC": PHOTO}, attachments=frozenset({"image/jpeg", "application/pdf"})
    )
    outcome = pipeline.run(cfg, provider, simulate=True)
    assert provider.calls[0].attachment is None
    [item] = outcome.plan.items
    assert item.destination is not None and item.destination.name == "IMG_0001.HEIC"


def test_anthropic_request_carries_the_image(downloads: Path) -> None:
    make_old(make_image(downloads / "Screenshot 2026-11-02.png", 2880, 1800))
    cfg = fake_config(downloads, vision={"enabled": True, "max_image_px": 1280})
    client = FakeClient(
        reply(
            {
                "category": "Images",
                "suggested_name": "flight receipt",
                "description": "A screenshot of a flight receipt.",
                "tags": [],
                "confidence": 0.9,
                "document_date": None,
            },
            usage=(2_200, 150),
        )
    )
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-haiku-5-5"), client=client
    )
    outcome = pipeline.run(cfg, provider, simulate=True)
    [request] = client.requests
    image, text = request["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert b"1280x800" in base64.standard_b64decode(image["source"]["data"])
    assert "Screenshot 2026-11-02.png" in text["text"]
    assert outcome.cost_usd == pytest.approx((2_200 * 0.10 + 150 * 0.50) / 1_000_000)


# --- Images with text (#100) ------------------------------------------------------

FLYER_TEXT = "SWAP MARKET\nbooks, records and plants\nSaturday 15 November, 9am to 1pm"
PAGE_TEXT = "\n".join(f"Water company annual statement, paragraph line {i:02d}" for i in range(14))
FLYER = Classification(
    category="Images",
    suggested_name="swap market flyer",
    description="A flyer for a swap market.",
    confidence=0.9,
)
BOTH = frozenset({"image/jpeg", "application/pdf"})


def test_a_picture_with_words_is_sent_as_the_image_and_its_text(downloads: Path) -> None:
    make_old(make_scan(downloads / "IMG_7310.jpg", FLYER_TEXT, width=900, height=1200))
    make_old(make_scan(downloads / "IMG_7311.jpg", PAGE_TEXT, width=1240, height=1754))
    make_old(make_scan(downloads / "scan0002.pdf", FLYER_TEXT))
    cfg = fake_config(downloads, vision={"enabled": True})
    provider = FakeProvider(default=FLYER, attachments=BOTH)
    pipeline.run(cfg, provider, simulate=True)
    sent = {c.path.name: c for c in provider.calls}

    flyer = sent["IMG_7310.jpg"]
    assert flyer.attachment is not None and flyer.text == FLYER_TEXT
    message = user_message(flyer)
    assert "The attached image is this file" in message and "<file_excerpt>\nSWAP" in message
    assert '"size_px": "900 x 1200"' in message
    # A page of text is a document: its text alone, like a scanned PDF's.
    for name in ("IMG_7311.jpg", "scan0002.pdf"):
        assert sent[name].attachment is None and sent[name].text is not None


def test_anthropic_request_carries_the_image_then_its_text(downloads: Path) -> None:
    make_old(make_scan(downloads / "IMG_7310.jpg", FLYER_TEXT, width=900, height=1200))
    cfg = fake_config(downloads, vision={"enabled": True})
    client = FakeClient(
        reply(
            {
                "category": "Images",
                "suggested_name": "swap market flyer",
                "description": "A flyer.",
                "tags": [],
                "confidence": 0.9,
                "document_date": None,
            }
        )
    )
    provider = AnthropicProvider(
        ProviderConfig(name="anthropic", model="claude-haiku-5-5"), client=client
    )
    pipeline.run(cfg, provider, simulate=True)
    [request] = client.requests
    image, text = request["messages"][0]["content"]
    assert image["type"] == "image"
    assert "recognised inside this image file" in text["text"]
    assert "books, records and plants" in text["text"]


@pytest.mark.parametrize(
    ("vision", "attachments"),
    [(False, BOTH), (True, frozenset())],
    ids=["vision-off", "provider-reads-no-images"],
)
def test_without_the_image_its_text_says_it_was_found_in_a_picture(
    downloads: Path, vision: bool, attachments: frozenset[str]
) -> None:
    make_old(make_scan(downloads / "IMG_7310.jpg", FLYER_TEXT, width=900, height=1200))
    cfg = fake_config(downloads, vision={"enabled": vision})
    provider = FakeProvider(default=FLYER, attachments=attachments)
    outcome = pipeline.run(cfg, provider, simulate=True)
    [ctx] = provider.calls
    assert ctx.attachment is None and ctx.text == FLYER_TEXT
    message = user_message(ctx)
    assert '"text_source": "recognised inside this image file (OCR; may have typos)"' in message
    assert '"text_lines": 3' in message and '"size_px": "900 x 1200"' in message
    [item] = outcome.plan.items
    assert item.status is FileStatus.ORGANIZED


def test_a_picture_that_cannot_be_prepared_falls_back_to_its_text(downloads: Path) -> None:
    flyer = make_scan(downloads / "IMG_7310.jpg", FLYER_TEXT)
    cfg = fake_config(downloads, vision={"enabled": True})
    provider = FakeProvider(default=FLYER, attachments=BOTH)

    def broken(*_args: object) -> Attachment:
        raise VisionError("sips could not convert the image")

    ctx = context_for(flyer, cfg)
    assert ctx.visual == "image"
    [decision] = classify_all(
        [ctx], provider, RULES, cfg, sleep=lambda _s: None, prepare=broken
    ).decisions
    [sent] = provider.calls
    assert sent.attachment is None and sent.text == FLYER_TEXT
    assert decision.classification == FLYER and not decision.context.vision_used
