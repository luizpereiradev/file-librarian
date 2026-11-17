"""Images and scanned PDFs, prepared for a vision model (RF-05, RF-26).

Only used when ``vision.enabled`` is on. Nothing here is registered as a
text extractor: the context stage marks a file as ``visual``, and the
classifier calls ``prepare`` right before a provider that reads attachments
is called, so prepared bytes live only for that one request.

- **Images** go through macOS's own ``/usr/bin/sips`` (no new dependency):
  converted to JPEG (HEIC, PNG, WebP, TIFF… all come out the same), shrunk
  so the longer side is at most ``vision.max_image_px`` (never enlarged:
  ``sips -Z`` would upscale a small image), then stripped of every metadata
  segment but the JFIF header, so EXIF (GPS position, camera serial) and XMP
  never leave the machine.
- **Scanned PDFs** are cut to their first ``vision.max_pdf_pages`` pages with
  pypdf and sent as a PDF document; the provider renders the pages, so
  nothing is rasterized here.

Anything that goes wrong raises ``VisionError``; the file is then classified
from its metadata, as it would be with vision off (RF-27).
"""

from __future__ import annotations

import io
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from file_librarian.config import VisionConfig
from file_librarian.models import VISUAL_MEDIA_TYPES, Attachment, FileContext, VisualKind

SIPS = "/usr/bin/sips"
SIPS_TIMEOUT = 60.0
JPEG_QUALITY = 80

IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
)
MAX_SOURCE_BYTES = 200 * 1024 * 1024
"""Larger images are not converted at all."""
MAX_IMAGE_BYTES = 5 * 1024 * 1024
"""The Anthropic API's limit for one image."""
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
"""A larger trimmed PDF is not sent: base64 adds a third, and requests cap at 32 MB."""

logging.getLogger("pypdf").setLevel(logging.ERROR)


class VisionError(Exception):
    """The image or PDF could not be prepared; classify the file from metadata."""


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def prepare(context: FileContext, settings: VisionConfig) -> Attachment:
    """The attachment for ``context.visual``. Raises ``VisionError``."""
    kind: VisualKind | None = context.visual
    if kind == "image":
        data = prepare_image(context.path, settings.max_image_px)
        return Attachment(VISUAL_MEDIA_TYPES["image"], data)
    if kind == "pdf":
        data, pages = prepare_pdf(context.path, settings.max_pdf_pages)
        return Attachment(VISUAL_MEDIA_TYPES["pdf"], data, pages=pages)
    raise VisionError("nothing visual to prepare")


def _sips(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        # Fixed binary; the only variable arguments are absolute paths and numbers.
        return subprocess.run(  # noqa: S603
            [SIPS, *args],
            capture_output=True,
            text=True,
            timeout=SIPS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise VisionError(f"cannot run {SIPS}: {exc}") from exc


def image_size(path: Path) -> tuple[int, int]:
    """Width and height in pixels, as ``sips`` reads them."""
    result = _sips("-g", "pixelWidth", "-g", "pixelHeight", str(path))
    width = re.search(r"pixelWidth:\s*(\d+)", result.stdout)
    height = re.search(r"pixelHeight:\s*(\d+)", result.stdout)
    if result.returncode != 0 or width is None or height is None:
        raise VisionError("not a readable image")
    return int(width.group(1)), int(height.group(1))


def prepare_image(path: Path, max_px: int) -> bytes:
    """A metadata-free JPEG of ``path``, its longer side at most ``max_px``."""
    path = path.absolute()  # an absolute path can never be read as a sips option
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise VisionError("image too large to convert")
    except OSError as exc:
        raise VisionError(f"unreadable: {exc.strerror or exc}") from exc
    width, height = image_size(path)
    resize = ["-Z", str(max_px)] if max(width, height) > max_px else []
    with tempfile.TemporaryDirectory(prefix="flib-vision-") as tmp:
        out = Path(tmp) / "image.jpg"
        result = _sips(
            "-s", "format", "jpeg", "-s", "formatOptions", str(JPEG_QUALITY),
            *resize, str(path), "--out", str(out),
        )  # fmt: skip
        # sips can exit 0 without writing anything (e.g. a file it skips).
        if result.returncode != 0 or not out.is_file():
            raise VisionError("sips could not convert the image")
        data = out.read_bytes()
    data = strip_jpeg_metadata(data)
    if len(data) > MAX_IMAGE_BYTES:
        raise VisionError("converted image is still too large to send")
    return data


def strip_jpeg_metadata(data: bytes) -> bytes:
    """Drop APP1-APP15 (EXIF, XMP, ICC, maker notes) and comment segments.

    Keeps the JFIF header (APP0) and every segment needed to decode the image;
    everything from the start of scan on is image data and is copied as is.
    """
    if data[:2] != b"\xff\xd8":
        raise VisionError("not a JPEG")
    out = bytearray(data[:2])
    i = 2
    while i + 1 < len(data):
        if data[i] != 0xFF:
            raise VisionError("malformed JPEG")
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte before a marker
            i += 1
            continue
        if marker in (0xDA, 0xD9):  # start of scan / end of image: the rest is image data
            out += data[i:]
            return bytes(out)
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # markers without a length
            out += data[i : i + 2]
            i += 2
            continue
        if i + 4 > len(data):
            break
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        end = i + 2 + length
        if length < 2 or end > len(data):
            raise VisionError("malformed JPEG")
        if not (0xE1 <= marker <= 0xEF or marker == 0xFE):
            out += data[i:end]
        i = end
    raise VisionError("JPEG without image data")


def prepare_pdf(path: Path, max_pages: int) -> tuple[bytes, int]:
    """The first ``max_pages`` pages of ``path`` as a new PDF, and how many there are."""
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise VisionError("password-protected PDF")
        writer = PdfWriter()
        for page in reader.pages[:max_pages]:
            writer.add_page(page)
        pages = len(writer.pages)
        buffer = io.BytesIO()
        writer.write(buffer)
    except VisionError:
        raise
    except Exception as exc:  # pypdf can raise almost anything on hostile input
        raise VisionError(f"cannot trim PDF ({type(exc).__name__})") from exc
    if pages == 0:
        raise VisionError("PDF without pages")
    data = buffer.getvalue()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise VisionError("first pages too large to send")
    return data, pages
