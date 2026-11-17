"""Turn a model's suggested name into a safe file name (RF-10, RF-32).

Everything the model says is untrusted. A suggested name becomes a single
path component: Unicode NFC, no separators or control characters, no leading
dot, lower case words joined by ``-``, within ``naming.max_length`` characters
and 255 bytes. The original extension is always kept.

When the model judges the original name better than its proposal
(``name_choice: original``), ``original_subject`` makes that name the subject,
so it gets the same pattern, date and sanitising as any other.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from datetime import date
from pathlib import PurePath

from file_librarian.config import NamingConfig

COMPOUND_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")
_MAX_NAME_BYTES = 255
# A date flib (or someone) already put in front of a name: "2026-11-16_", "2026-11-16 - ".
_LEADING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}[\s_.-]*")


def split_name(name: str) -> tuple[str, str]:
    """Stem and extension, treating ``.tar.gz`` and friends as one extension."""
    lower = name.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(suffix) :]
    suffix = PurePath(name).suffix
    if suffix and len(suffix) <= 16 and name != suffix:
        return name[: -len(suffix)], suffix
    return name, ""


def original_subject(name: str) -> str:
    """The subject an original name carries: its stem without a leading ISO date.

    The pattern adds the date again, so ``2026-11-16_report.pdf`` must not
    become ``2026-11-16_2026-11-16-report.pdf``.
    """
    stem, _ = split_name(name)
    return _LEADING_DATE.sub("", stem.strip())


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = "".join(ch if unicodedata.category(ch)[0] in "LN" else " " for ch in text)
    return re.sub(r"\s+", "-", text.strip())


def _fit(stem: str, extension: str, max_chars: int) -> str:
    stem = stem[: max(1, max_chars - len(extension))].rstrip("-_ .")
    while len((stem + extension).encode("utf-8")) > _MAX_NAME_BYTES:
        stem = stem[:-1]
    return stem + extension


def build_name(original: str, subject: str, when: date, naming: NamingConfig) -> str | None:
    """The new file name, or ``None`` when the suggestion yields nothing usable."""
    slug = slugify(subject)
    if not slug:
        return None
    _, extension = split_name(original)
    stem = naming.pattern.format(date=when.isoformat(), subject=slug)
    stem = safe_component(stem)
    if not stem:
        return None
    return _fit(stem, extension, naming.max_length)


def safe_component(name: str) -> str:
    """Make any string a harmless single path component (may return ``""``)."""
    name = unicodedata.normalize("NFC", name)
    name = "".join(
        "-" if ch in "/:\\" or unicodedata.category(ch).startswith("C") else ch for ch in name
    )
    # Repeat until stable: " . .pdf" must not end up as the hidden ".pdf".
    previous = None
    while name != previous:
        previous = name
        name = name.strip().lstrip(".")
    return name


MAX_COUNTER = 999


def candidate_names(name: str) -> Iterator[str]:
    """``name``, then ``name-2`` ... ``name-999``: the order collisions are resolved in."""
    yield name
    for n in range(2, MAX_COUNTER + 1):
        yield with_counter(name, n)


def with_counter(name: str, n: int) -> str:
    """``report.pdf`` -> ``report-2.pdf``, still within 255 bytes."""
    stem, extension = split_name(name)
    suffix = f"-{n}{extension}"
    while stem and len((stem + suffix).encode("utf-8")) > _MAX_NAME_BYTES:
        stem = stem[:-1]
    return f"{stem}{suffix}"
