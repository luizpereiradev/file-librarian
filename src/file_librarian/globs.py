"""Glob matching the way the Mac's default file system compares names."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase


def matches_any(name: str, patterns: Iterable[str]) -> bool:
    """``name`` matches one of ``patterns``, ignoring case like APFS does."""
    folded = name.casefold()
    return any(fnmatchcase(folded, pattern.casefold()) for pattern in patterns)
