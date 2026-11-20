"""Text recognised in images and scanned PDFs, on this Mac (RF-26).

With ``ocr.enabled`` (the default), an image or a PDF with no text layer is
read with macOS's own text recognition, the Vision framework's
``VNRecognizeTextRequest``. Nothing leaves the Mac for it, and nothing is
installed: the helper ``ocr.js`` (next to this module) runs under
``/usr/bin/osascript -l JavaScript``, whose Objective-C bridge reaches Vision,
ImageIO and PDFKit on every supported macOS.

Why JavaScript for Automation and not a Swift helper: measured on macOS 26,
it is as fast as a compiled Swift binary (about 0.25 s per image or page once
Vision is warm; the very first recognition of a new program takes about 30 s
while Vision prepares its model, for Swift too), needs no Xcode Command Line
Tools to build, and has nothing to compile or keep in step with the package.
``swift ocr.swift`` would need the toolchain on every run and compile each
time. Like ``sips`` for vision, it runs from the scheduled launchd agent with
no permission beyond reading the file.

Defences: the file's path is an argument, never part of the script's source;
an absolute path, so it is never taken for an option; files over
``MAX_SOURCE_BYTES`` are not read; every image or page is decoded or rendered
at most ``MAX_PX`` pixels on its longer side (which bounds the helper's
memory, about 200 MB); the helper is killed after ``TIMEOUT`` seconds; its
output is decoded as UTF-8 with errors replaced and capped at ``max_chars``.

Anything that goes wrong raises ``OcrError``; the file is then handled as it
was before OCR existed (vision when on, else metadata).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from file_librarian.config import OcrConfig

OSASCRIPT = "/usr/bin/osascript"
HELPER = Path(__file__).with_name("ocr.js")
TIMEOUT = 120.0
"""Seconds for one file. Generous: Vision's first use by a program takes about 30 s."""
PROBE_TIMEOUT = 30.0
MAX_PX = 2400
"""Longer side of each image or rendered page: about 200 dpi for an A4 page."""
MAX_SOURCE_BYTES = 200 * 1024 * 1024
"""Larger files are not read at all."""
MAX_OUTPUT_BYTES = 4 * 1024 * 1024

HELPER_VERSION = "1"
"""Part of the classification cache key: bump when ocr.js changes the text it returns."""


class OcrError(Exception):
    """The text could not be recognised; handle the file as if OCR were off."""


@dataclass(frozen=True, slots=True)
class Availability:
    """Whether this Mac can run OCR, and in which languages."""

    available: bool
    languages: tuple[str, ...] = ()
    reason: str | None = None


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        # A fixed binary and script; the variable arguments are an absolute path and numbers.
        return subprocess.run(  # noqa: S603
            [OSASCRIPT, "-l", "JavaScript", str(HELPER), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError(f"text recognition took longer than {timeout:.0f} s") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise OcrError(f"cannot run {OSASCRIPT}: {exc}") from exc


def _message(result: subprocess.CompletedProcess[bytes]) -> str:
    lines = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
    return (lines[-1] if lines else f"exit code {result.returncode}")[:200]


def recognize(path: Path, settings: OcrConfig, max_chars: int) -> str:
    """The text Vision finds in ``path`` (an image, or a PDF's first pages); may be empty.

    Raises ``OcrError``.
    """
    path = path.absolute()
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise OcrError("file too large for text recognition")
    except OSError as exc:
        raise OcrError(f"unreadable: {exc.strerror or exc}") from exc
    result = _run(
        ["recognize", str(path), str(settings.max_pages), str(MAX_PX), *settings.languages],
        TIMEOUT,
    )
    if result.returncode != 0:
        raise OcrError(f"text recognition failed: {_message(result)}")
    text = result.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
    # Control characters other than line breaks and tabs are never text on a page.
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    return cleaned.strip()[:max_chars]


@cache
def availability() -> Availability:
    """Ask the helper once per process whether Vision text recognition works here."""
    if not Path(OSASCRIPT).exists():
        return Availability(False, reason=f"{OSASCRIPT} not found")
    if not HELPER.is_file():
        return Availability(False, reason="the OCR helper is missing from the installation")
    try:
        result = _run(["probe"], PROBE_TIMEOUT)
    except OcrError as exc:
        return Availability(False, reason=str(exc))
    languages = tuple(
        line.strip() for line in result.stdout.decode("utf-8", errors="replace").splitlines()
    )
    languages = tuple(lang for lang in languages if lang)
    if result.returncode != 0 or not languages:
        return Availability(False, reason=f"macOS text recognition unavailable: {_message(result)}")
    return Availability(True, languages=languages)


def enough_text(text: str, settings: OcrConfig) -> bool:
    """``text`` reads like a document: at least ``min_chars`` letters and digits."""
    return sum(ch.isalnum() for ch in text) >= settings.min_chars
