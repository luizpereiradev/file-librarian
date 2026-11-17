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
from file_librarian.config import ProviderConfig
from file_librarian.models import Classification, FileStatus
from file_librarian.providers.anthropic import AnthropicProvider
from file_librarian.providers.fake import FakeProvider
from tests._helpers import FakeClient, fake_config, make_blank_pdf, make_image, make_old, reply

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
