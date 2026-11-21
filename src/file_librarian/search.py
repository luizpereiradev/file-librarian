"""Full-text search over the index (RF-13, RF-35).

The user's words are never passed to FTS5 as query syntax: each word becomes
a quoted prefix term, so ``AND``, ``NEAR``, ``*`` or quotes in a query are just
text. All words must match. Accents are ignored (the index tokenizer removes
diacritics), so ``orcamento`` finds ``orçamento``. Results are ranked by BM25,
with names weighted above descriptions.

Filters (RF-14) narrow the results and work with or without words: by
category (case-insensitive, any of several), by extension (with or without the
dot, any of several) and by period. The period is the day flib last processed
the file (``processed_at``), in the local time zone (a file's mtime is that
of the download, not of when it was filed; the document's own date is the
``period`` filter below). Without words, matches are listed newest first.

Semantic search (RF-15, opt in with ``search.semantic.enabled``) finds files by
meaning too: "invoice" finds a file described as a "bill". The words ranking
(BM25) and the meaning ranking (cosine similarity of embeddings, see
``embeddings.py``) are fused by reciprocal rank, with the same filters on
both. A file found only by meaning must be at least
``search.semantic.min_similarity`` similar to the query; one that matches the
words always stays. Each hit says how it was found (``source``). When it
cannot run, it says why in one line and searches by words.

Structured fields (#88) narrow the results too: the issuer (matched by
``naming.issuer_key``, so case, accents, punctuation and legal forms such as
``Inc`` or ``Ltda`` do not matter), the kind of document, the amount (at least,
at most, in a currency), the due date (before, after) and the period the
document refers to (a year or a month: the answer's ``period``, else the month
of its document date). ``sum_amounts`` adds up the amounts of every match per
currency, never mixing two, optionally grouped by issuer, month, year, kind
or category; it matches the query by words only.

Duplicates (RF-21) are left out unless asked for (``include_duplicates``):
they are copies of a file the search already finds. They have no embedding,
so they are found by words only. A duplicate in the results says where its
original is (``duplicate_of``).
"""

from __future__ import annotations

import heapq
import json
import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from file_librarian.config import Config
from file_librarian.db.store import GROUPS, AmountTotal, FieldFilters, FileRow, Store
from file_librarian.embeddings import (
    Embedder,
    EmbeddingError,
    outdated_count,
    query_text,
    similarities,
)
from file_librarian.fields import (
    KINDS,
    clean_currency,
    clean_period,
    format_amount,
    format_decimal,
    parse_threshold,
)
from file_librarian.naming import issuer_key


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
    duplicate_of: str | None = None
    """For a duplicate, where its original is now."""
    source: str | None = None
    """``text`` (found by its words), ``semantic`` (only by meaning) or ``both``;
    ``None`` when filters alone listed it."""
    similarity: float | None = None
    """Cosine similarity of its embedding to the query, when a semantic search had one."""
    kind: str | None = None
    issuer: str | None = None
    amount: str | None = None
    """The document's amount as a decimal string (``"1234.56"``), in ``currency``."""
    currency: str | None = None
    document_date: str | None = None
    due_date: str | None = None
    period: str | None = None
    amount_minor: int | None = None
    """``amount`` in the currency's minor units (cents)."""

    def fields_line(self) -> str | None:
        """The structured fields in one short line, or ``None`` when there are none."""
        parts = [p for p in (self.kind, self.issuer) if p]
        if self.amount_minor is not None and self.currency is not None:
            parts.append(format_amount(self.amount_minor, self.currency))
        if self.due_date:
            parts.append(f"due {self.due_date}")
        if self.period:
            parts.append(f"for {self.period}")
        elif self.document_date:
            parts.append(f"dated {self.document_date}")
        return " · ".join(parts) or None


class SearchError(ValueError):
    """A filter the user gave is not valid; the message says why."""


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Validated filters. Bounds are UTC ISO instants, ``until`` exclusive."""

    categories: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    since: str | None = None
    until: str | None = None
    fields: FieldFilters = field(default_factory=FieldFilters)
    """On the structured fields (#88): issuer, kind, amount, due date, period."""

    def __bool__(self) -> bool:
        return bool(self.categories or self.extensions or self.since or self.until or self.fields)


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
    issuer: str | None = None,
    kinds: Iterable[str] | None = None,
    min_amount: str | None = None,
    max_amount: str | None = None,
    currency: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    period: str | None = None,
) -> SearchFilters:
    """Validate raw filter values; raises :class:`SearchError` on a bad one.

    Categories match ``known_categories`` case-insensitively and come back
    spelled as known. Dates are ``YYYY-MM-DD``, both ends inclusive; the due
    date bounds are exclusive. Amounts are inclusive, in major units.
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
        fields=_field_filters(
            issuer=issuer,
            kinds=kinds,
            min_amount=min_amount,
            max_amount=max_amount,
            currency=currency,
            due_before=due_before,
            due_after=due_after,
            period=period,
        ),
    )


def _field_filters(
    *,
    issuer: str | None,
    kinds: Iterable[str] | None,
    min_amount: str | None,
    max_amount: str | None,
    currency: str | None,
    due_before: str | None,
    due_after: str | None,
    period: str | None,
) -> FieldFilters:
    key = None
    if issuer is not None:
        key = issuer_key(issuer)
        if not key:
            raise SearchError(f"--issuer {issuer!r} names no organisation")
    chosen: list[str] = []
    for raw in split_values(kinds):
        kind = raw.casefold()
        if kind not in KINDS:
            raise SearchError(f"unknown kind {raw!r} (kinds: {', '.join(KINDS)})")
        if kind not in chosen:
            chosen.append(kind)
    low = _amount(min_amount, "--min-amount")
    high = _amount(max_amount, "--max-amount")
    if low is not None and high is not None and low > high:
        raise SearchError(f"--min-amount {min_amount} is more than --max-amount {max_amount}")
    code = None
    if currency is not None:
        code = clean_currency(currency)
        if code is None:
            raise SearchError(f"--currency {currency!r} is not a currency code like USD or BRL")
    before = _day(due_before, "--due-before").isoformat() if due_before is not None else None
    after = _day(due_after, "--due-after").isoformat() if due_after is not None else None
    wanted = None
    if period is not None:
        text = period.strip()
        wanted = clean_period(text) if re.fullmatch(r"\d{4}(-\d{2})?", text) else None
        if wanted is None:
            raise SearchError(f"--period {period!r} is not a year or month like 2026 or 2026-09")
    return FieldFilters(
        issuer_key=key,
        kinds=tuple(chosen),
        min_amount=low,
        max_amount=high,
        currency=code,
        due_before=before,
        due_after=after,
        period=wanted,
    )


def _amount(text: str | None, flag: str) -> Decimal | None:
    if text is None:
        return None
    value = parse_threshold(text)
    if value is None:
        raise SearchError(f"{flag} {text!r} is not an amount like 100 or 99.90")
    return value


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
    store: Store,
    text: str,
    *,
    filters: SearchFilters | None = None,
    limit: int = 20,
    include_duplicates: bool = False,
) -> list[SearchHit]:
    """Files matching all words of ``text`` and all ``filters``.

    No words and no filters find nothing. Filters without words list the
    matches newest first; with words, best first.
    """
    filters = filters or SearchFilters()
    query = fts_query(text)
    if query is None and not filters:
        return []
    rows = _word_rows(store, query, filters, limit, include_duplicates)
    source = None if query is None else "text"
    return [_hit(store, row, source) for row in rows]


def _word_rows(
    store: Store,
    query: str | None,
    filters: SearchFilters,
    limit: int,
    include_duplicates: bool = False,
) -> list[FileRow]:
    return store.search_files(
        query,
        categories=filters.categories,
        extensions=filters.extensions,
        since=filters.since,
        until=filters.until,
        include_duplicates=include_duplicates,
        fields=filters.fields,
        limit=limit,
    )


def original_path(store: Store, file_id: int | None) -> str | None:
    """Where the original of a duplicate is now (``None`` for a file that is not one)."""
    return None if file_id is None else str(store.file_by_id(file_id).current_path)


def _hit(
    store: Store, row: FileRow, source: str | None = None, similarity: float | None = None
) -> SearchHit:
    return SearchHit(
        path=str(row.current_path),
        original_name=row.original_name,
        category=row.category,
        description=row.description,
        tags=row.tags,
        status=row.status.value,
        processed_at=row.processed_at,
        exists=os.path.lexists(row.current_path),
        duplicate_of=original_path(store, row.duplicate_of),
        source=source,
        similarity=None if similarity is None else round(similarity, 4),
        kind=row.kind,
        issuer=row.issuer,
        amount=None
        if row.amount_minor is None or row.currency is None
        else format_decimal(row.amount_minor, row.currency),
        currency=row.currency,
        document_date=row.document_date,
        due_date=row.due_date,
        period=row.period,
        amount_minor=row.amount_minor if row.currency is not None else None,
    )


# --- semantic search (RF-15) -----------------------------------------------------------

RRF_K = 60
"""Reciprocal rank fusion constant: a result at rank r scores 1 / (RRF_K + r) per list."""


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    hits: list[SearchHit]
    semantic: bool
    """True when the ranking used embeddings; False when it fell back to words only."""
    notice: str | None = None
    """One line saying why it fell back, or that some files have no vector yet."""
    weak: int = 0
    """Files found only by meaning and left out as below ``search.semantic.min_similarity``."""


def semantic_search(
    store: Store,
    text: str,
    config: Config,
    *,
    filters: SearchFilters | None = None,
    limit: int = 20,
    embedder: Embedder | None = None,
    include_duplicates: bool = False,
) -> SearchOutcome:
    """Words and meaning together: BM25 and cosine rankings fused by reciprocal rank.

    The meaning ranking keeps only files at least ``search.semantic.min_similarity``
    similar to the query, so a weak match never fills the list; a file that
    matches the words is kept whatever its similarity.

    The filters apply to both. Falls back to :func:`search` with a notice when
    semantic search is off, the query has no words, the embeddings server
    cannot be used, or no file has a vector for ``search.semantic.model`` yet.
    """
    filters = filters or SearchFilters()
    settings = config.search.semantic

    def words_only(notice: str | None) -> SearchOutcome:
        hits = search(
            store, text, filters=filters, limit=limit, include_duplicates=include_duplicates
        )
        return SearchOutcome(hits, False, notice)

    if not settings.enabled:
        return words_only(
            "semantic search is off (search.semantic.enabled: false); searched by words only"
        )
    query = fts_query(text)
    if query is None:
        return words_only(None)
    if store.embedding_count(settings.model) == 0:
        return words_only(
            f"no file has an embedding from {settings.model} yet; run `flib search --reindex`. "
            "Searched by words only"
        )
    embedder = embedder or Embedder(settings)
    try:
        [vector] = embedder.embed([query_text(text, settings)])
    except EmbeddingError as exc:
        return words_only(f"semantic search unavailable ({exc}); searched by words only")
    pool = max(limit * 2, 50)
    by_words = [row.id for row in _word_rows(store, query, filters, pool, include_duplicates)]
    similarity = similarities(
        vector,
        store.embedding_vectors(
            settings.model,
            len(vector),
            categories=filters.categories,
            extensions=filters.extensions,
            since=filters.since,
            until=filters.until,
            fields=filters.fields,
        ),
    )
    nearby = heapq.nlargest(pool, similarity, key=similarity.__getitem__)
    by_meaning = [i for i in nearby if similarity[i] >= settings.min_similarity]
    in_words, close = set(by_words), set(by_meaning)
    weak = sum(1 for i in nearby if i not in close and i not in in_words)
    scores: dict[int, float] = {}
    for ranking in (by_words, by_meaning):
        for rank, file_id in enumerate(ranking, start=1):
            scores[file_id] = scores.get(file_id, 0.0) + 1.0 / (RRF_K + rank)
    best = sorted(scores, key=lambda file_id: -scores[file_id])[:limit]
    rows = store.files_by_ids(best)
    notice = None
    outdated = outdated_count(store, config)
    if outdated:
        notice = (
            f"{outdated} file(s) have no up-to-date embedding yet and may be found by words "
            "only; the next run or `flib search --reindex` embeds them"
        )
    hits = [
        _hit(store, rows[i], _source(i in in_words, i in close), similarity.get(i))
        for i in best
        if i in rows
    ]
    return SearchOutcome(hits, True, notice, weak)


def _source(by_words: bool, by_meaning: bool) -> str:
    if by_words and by_meaning:
        return "both"
    return "text" if by_words else "semantic"


def to_json(hits: list[SearchHit]) -> str:
    return json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2)


# --- totals (#88) ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Sums:
    """What ``sum_amounts`` found: totals per group and currency, and overall."""

    by: str | None
    groups: list[AmountTotal]
    """Per group and currency, largest first; empty without ``by``."""
    totals: list[AmountTotal]
    """Per currency over every match, largest first."""
    without_amount: int
    """Matching files that state no amount, left out of the totals."""

    def to_dict(self) -> dict[str, object]:
        return {
            "by": self.by,
            "groups": [total_dict(t) for t in self.groups],
            "totals": [total_dict(t) for t in self.totals],
            "files_without_amount": self.without_amount,
        }


def total_dict(total: AmountTotal) -> dict[str, object]:
    """One total as JSON shows it: the amount as a decimal string, never a float."""
    return {
        "group": total.group,
        "currency": total.currency,
        "total": format_decimal(total.total_minor, total.currency),
        "files": total.files,
    }


def sum_amounts(
    store: Store, text: str, *, filters: SearchFilters | None = None, by: str | None = None
) -> Sums:
    """Add up the amounts of every file matching ``text`` (by words) and ``filters``.

    No words and no filters add up every filed document (never duplicates or
    sensitive files). Words with nothing searchable in them (``"..."``) and no
    filters match nothing. ``by`` is one of ``GROUPS``.
    """
    if by is not None and by not in GROUPS:
        raise SearchError(f"cannot group by {by!r} (one of: {', '.join(GROUPS)})")
    filters = filters or SearchFilters()
    query = fts_query(text)
    if query is None and text.strip() and not filters:
        return Sums(by, [], [], 0)

    def totals(group: str | None) -> tuple[list[AmountTotal], int]:
        return store.sum_amounts(
            query,
            by=group,
            categories=filters.categories,
            extensions=filters.extensions,
            since=filters.since,
            until=filters.until,
            fields=filters.fields,
        )

    overall, missing = totals(None)
    return Sums(by, totals(by)[0] if by is not None else [], overall, missing)
