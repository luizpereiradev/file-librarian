"""Glob matching the way the Mac's default file system compares names."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from fnmatch import fnmatchcase


def _fold(text: str) -> str:
    """NFC, then case-folded: how APFS compares names (``é`` typed or decomposed)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    """``name`` matches one of ``patterns``, ignoring case and Unicode form like APFS does."""
    folded = _fold(name)
    return any(fnmatchcase(folded, _fold(pattern)) for pattern in patterns)
