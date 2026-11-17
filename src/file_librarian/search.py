"""Full-text search over the index (RF-13, RF-35).

The user's words are never passed to FTS5 as query syntax: each word becomes
a quoted prefix term, so ``AND``, ``NEAR``, ``*`` or quotes in a query are just
text. All words must match. Accents are ignored (the index tokenizer removes
diacritics), so ``orcamento`` finds ``orçamento``. Results are ranked by BM25,
with names weighted above descriptions.

Filters (RF-14) narrow the results and work with or without words: by
category (case-insensitive, any of several), by extension (with or without the
dot, any of several) and by period. The period is the day flib last processed
the file (``processed_at``), in the local time zone: the index keeps no
document date, and a file's mtime is that of the download, not of when it was
filed. Without words, matches are listed newest first.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta

from file_librarian.db.store import Store


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    original_name: str
    category: str | None
    description: str | None
    tags: list[str]
    status: str
    processed_at: str
    exists: bool


class SearchError(ValueError):
    """A filter the user gave is not valid; the message says why."""


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Validated filters. Bounds are UTC ISO instants, ``until`` exclusive."""

    categories: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None

    def __bool__(self) -> bool:
        return bool(self.categories or self.extensions or self.since or self.until)


def split_values(values: Iterable[str] | None) -> list[str]:
    """Flatten repeated and comma-separated values, dropping blanks."""
    return [part.strip() for value in values or () for part in value.split(",") if part.strip()]


def make_filters(
    *,
    categories: Iterable[str] | None = None,
    extensions: Iterable[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    known_categories: Iterable[str] = (),
) -> SearchFilters:
    """Validate raw filter values; raises :class:`SearchError` on a bad one.

    Categories match ``known_categories`` case-insensitively and come back
    spelled as known. Dates are ``YYYY-MM-DD``, both ends inclusive.
    """
    known = {normalize_category(c): c for c in known_categories}
    chosen: list[str] = []
    for raw in split_values(categories):
        name = known.get(normalize_category(raw))
        if name is None:
            options = ", ".join(sorted(known.values(), key=str.casefold)) or "none yet"
            raise SearchError(f"unknown category {raw!r} (known: {options})")
        if name not in chosen:
            chosen.append(name)
    exts: list[str] = []
    for raw in split_values(extensions):
        ext = raw.lower().removeprefix(".")
        if not re.fullmatch(r"[^\W_]+", ext):
            raise SearchError(f"bad extension {raw!r}: give it like pdf or .pdf")
        if "." + ext not in exts:
            exts.append("." + ext)
    start = _day(since, "--since") if since is not None else None
    end = _day(until, "--until") if until is not None else None
    if start is not None and end is not None and start > end:
        raise SearchError(f"--since {since} is after --until {until}")
    return SearchFilters(
        categories=tuple(chosen),
        extensions=tuple(exts),
        since=None if start is None else _utc_start(start),
        until=None if end is None else _utc_start(end + timedelta(days=1)),
    )


def normalize_category(name: str) -> str:
    return unicodedata.normalize("NFC", name.strip()).casefold()


def _day(text: str, flag: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text.strip()):
        raise SearchError(f"{flag} {text!r} is not a date like 2026-01-31")
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise SearchError(f"{flag} {text!r} is not a valid date") from None


def _utc_start(day: date) -> str:
    """Local midnight starting ``day``, as UTC ISO text comparable with ``processed_at``."""
    local = datetime.combine(day, time()).astimezone()
    return local.astimezone(UTC).isoformat(timespec="seconds")


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query, or ``None`` if it has no words."""
    words = re.findall(r"\w+", unicodedata.normalize("NFC", text))
    if not words:
        return None
    return " ".join('"' + w.replace('"', '""') + '"*' for w in words[:20])


def search(
    store: Store, text: str, *, filters: SearchFilters | None = None, limit: int = 20
) -> list[SearchHit]:
    """Files matching all words of ``text`` and all ``filters``.

    No words and no filters find nothing. Filters without words list the
    matches newest first; with words, best first.
    """
    filters = filters or SearchFilters()
    query = fts_query(text)
    if query is None and not filters:
        return []
    return [
        SearchHit(
            path=str(row.current_path),
            original_name=row.original_name,
            category=row.category,
            description=row.description,
            tags=row.tags,
            status=row.status.value,
            processed_at=row.processed_at,
            exists=os.path.lexists(row.current_path),
        )
        for row in store.search_files(
            query,
            categories=filters.categories,
            extensions=filters.extensions,
            since=filters.since,
            until=filters.until,
            limit=limit,
        )
    ]


def to_json(hits: list[SearchHit]) -> str:
    return json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2)
