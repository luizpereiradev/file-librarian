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

The helper reports each line it found with where it is in the picture, and
the picture's size; ``recognize`` returns the text with that summary
(``Recognized``). ``looks_like_document`` tells a scanned or photographed
document (many lines, most of the picture text) from a picture that happens
to contain words (a flyer, a meme, a chat screenshot); see ``context.py`` for
what each is sent as (#100).

Anything that goes wrong raises ``OcrError``; the file is then handled as it
was before OCR existed (vision when on, else metadata).

``replay`` makes ``recognize`` return recorded results instead of running the
helper: ``flib eval`` uses it so that its corpus scores the same everywhere,
with or without macOS's text recognition.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from file_librarian.config import OcrConfig
from file_librarian.models import PictureText

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

HELPER_VERSION = "2"
"""Part of the classification cache key: bump when ocr.js changes what it returns.
2: JSON with each line's box and the picture's size, instead of plain text."""

DOCUMENT_MIN_LINES = 10
DOCUMENT_MIN_CHARS = 300
DOCUMENT_MIN_COVERAGE = 0.20
"""An image is a document when it has at least this many lines, letters and digits, and
share of its area under text. Measured on the evaluation corpus's pictures (#100,
``docs/quality.md``): a scanned page has 26 lines, 988 characters and 32% of its area
under text; a chat screenshot 23 lines but 210 characters and 13%; a photographed
receipt 19 lines, 145 characters and 7%; a flyer, a meme or a caption at most 6 lines."""


class OcrError(Exception):
    """The text could not be recognised; handle the file as if OCR were off."""


@dataclass(frozen=True, slots=True)
class Recognized:
    """What the helper found: the text, and how it sits in the (first) picture."""

    text: str
    lines: int = 0
    coverage: float = 0.0
    """The share of the first picture's area inside the boxes of its lines, 0 to 1."""
    width: int | None = None
    height: int | None = None
    """The first picture's size in pixels: an image's as stored, a page's as rendered."""

    def picture(self) -> PictureText:
        return PictureText(self.width, self.height, self.lines, self.coverage)


_replayed: ContextVar[Mapping[str, Recognized] | None] = ContextVar("replayed", default=None)


@contextmanager
def replay(recorded: Mapping[str, Recognized]) -> Iterator[None]:
    """Within the block, ``recognize`` runs nothing: it returns what ``recorded`` holds
    for the file's SHA-256, and no text for any other file."""
    token = _replayed.set(recorded)
    try:
        yield
    finally:
        _replayed.reset(token)


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


def recognize(path: Path, settings: OcrConfig, max_chars: int) -> Recognized:
    """What Vision finds in ``path`` (an image, or a PDF's first pages); the text may be empty.

    Raises ``OcrError``.
    """
    path = path.absolute()
    recorded = _replayed.get()
    if recorded is not None:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise OcrError(f"unreadable: {exc.strerror or exc}") from exc
        found = recorded.get(digest, Recognized(""))
        return Recognized(
            found.text[:max_chars], found.lines, found.coverage, found.width, found.height
        )
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
    if len(result.stdout) > MAX_OUTPUT_BYTES:
        raise OcrError("text recognition returned too much")
    return parse(result.stdout.decode("utf-8", errors="replace"), max_chars)


def _clean(text: str) -> str:
    # Control characters other than line breaks and tabs are never text on a page.
    return "".join(ch for ch in text if ch.isprintable() or ch in "\n\t").strip()


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError("not a number")
    return float(value)


def _size(value: Any) -> int | None:
    try:
        size = _number(value)
    except ValueError:
        return None
    return int(size) if 0 < size < 1_000_000 else None


def parse(output: str, max_chars: int) -> Recognized:
    """Read the helper's JSON (``ocr.js``, version 2). Raises ``OcrError``."""
    try:
        data = json.loads(output)
        if not isinstance(data, dict) or data.get("v") != 2 or not isinstance(data["pages"], list):
            raise ValueError("unexpected format")
        texts: list[str] = []
        lines = 0
        first: tuple[float, int | None, int | None] | None = None
        for page in data["pages"]:
            covered = 0.0
            found: list[str] = []
            for line in page["lines"]:
                text = _clean(str(line["text"]))
                box = [_number(v) for v in line["box"]]
                if len(box) != 4:
                    raise ValueError("a box has four numbers")
                covered += min(max(box[2], 0.0), 1.0) * min(max(box[3], 0.0), 1.0)
                if text:
                    found.append(text)
            lines += len(found)
            if found:
                texts.append("\n".join(found))
            if first is None:
                first = (min(covered, 1.0), _size(page.get("width")), _size(page.get("height")))
    except (ValueError, KeyError, TypeError) as exc:
        raise OcrError(f"text recognition returned something unreadable ({exc})") from exc
    coverage, width, height = first or (0.0, None, None)
    return Recognized("\n\n".join(texts)[:max_chars], lines, round(coverage, 4), width, height)


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
    """``text`` is worth sending: at least ``min_chars`` letters and digits."""
    return letters(text) >= settings.min_chars


def letters(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def looks_like_document(found: Recognized) -> bool:
    """Many lines of text covering much of the picture: a scanned or photographed page,
    not a picture that happens to contain words (``DOCUMENT_MIN_*``)."""
    return (
        found.lines >= DOCUMENT_MIN_LINES
        and letters(found.text) >= DOCUMENT_MIN_CHARS
        and found.coverage >= DOCUMENT_MIN_COVERAGE
    )
