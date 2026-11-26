"""The interface's read-only pages: Activity, Search and Due (#114).

Each page reads through the same modules as the command line and the MCP
server (``activity``, ``search``, ``due``) and is built on the server with
``markup``, so it reads without JavaScript and every value from the index is
escaped. Nothing here writes: the index is opened read-only, never created
or migrated, and no route moves, renames or deletes anything.

- **Activity** (``GET /activity?days=7|30|90``): runs by day, what each moved
  where, duplicates set aside, sensitive files left in place (a count only),
  a wait for the unlock, the cost.
- **Search** (``GET /search`` for the form, ``POST /search`` for results,
  ``POST /search/export`` for CSV): the words and filters of ``flib search``,
  in a form body, never in the address (it would stay in the browser's
  history); totals over every match as ``flib search --sum`` adds them up.
  Sensitive files never appear: they are counted.
- **Due** (``GET /due?within=...&overdue=1&all=1``): ``flib due``, with its
  window as links; only fixed choices go in the address, never an issuer.

Open and Show in Finder (``POST /files/open``, ``POST /files/reveal``) take a
file's index id, never a path: the path comes from the index and is checked
again (in the library or an input folder, there, not a symbolic link, not a
sensitive file) before macOS is asked to open it. Error pages say what went
wrong in general words; an ``OSError``, whose text holds paths, never
reaches the page.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import os
import sqlite3
import stat
import subprocess
import unicodedata
from array import array
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs

from file_librarian import activity as activity_mod
from file_librarian import due as due_mod
from file_librarian.config import Config, SemanticConfig, is_within
from file_librarian.db.store import GROUPS, AmountTotal, Store
from file_librarian.embeddings import Embedder, EmbeddingError
from file_librarian.extractors.origin import read_xattr
from file_librarian.fields import KINDS, format_amount
from file_librarian.models import FileStatus, OperationStatus
from file_librarian.search import (
    SearchError,
    SearchFilters,
    SearchHit,
    Sums,
    make_filters,
    semantic_search,
    split_values,
    sum_amounts,
)
from file_librarian.search import search as words_search
from file_librarian.ui.markup import Markup, csrf_field, el, join, layout, prose

Page = tuple[int, bytes]
"""A status and a whole HTML page."""

LIMITS = (20, 50, 100, 500)
_LIMIT_TEXT = frozenset(map(str, LIMITS))
DEFAULT_LIMIT = 50
WITHIN = (("7d", "7 days"), ("2w", "2 weeks"), ("1m", "1 month"), ("3m", "3 months"))
OPENABLE = frozenset({FileStatus.ORGANIZED, FileStatus.REVIEW, FileStatus.DUPLICATE})
OPEN_TIMEOUT = 10.0
EMBED_TIMEOUT = 5.0
"""Seconds a search by meaning waits for the embeddings server: a request thread waits
with it (``search.semantic.timeout_seconds`` stays the command line's)."""
OPENABLE_EXTENSIONS = frozenset(
    {
        *("pdf", "txt", "md", "markdown", "csv", "tsv", "json", "rtf"),
        *("png", "jpg", "jpeg", "gif", "heic", "heif", "webp", "tif", "tiff", "bmp"),
        *("docx", "xlsx", "pptx", "odt", "ods", "odp"),
        *("pages", "numbers", "key", "eml"),
        *("mp3", "m4a", "wav", "aac", "flac", "mp4", "mov", "m4v"),
    }
)
"""What Open hands to macOS: documents it shows in an app. Never a program, an
installer, a script, a shortcut to a place (``.webloc``, ``.fileloc``), a disk
image, a profile or a folder (an ``.app`` is one): those get Show in Finder only.
Nor the legacy Office formats (``.doc``, ``.xls``, ``.ppt``), which can carry
macros without saying so in their name."""
_FINDER_INFO = "com.apple.FinderInfo"
_OPEN_WITH = "com.apple.LaunchServices.OpenWith"
"""A per-file "Open With": macOS would open the file in the app it names."""
_IS_ALIAS = 0x8000
"""``kIsAlias`` in the Finder flags, bytes 8-9 of ``com.apple.FinderInfo``."""


class Unreadable(Exception):
    """The index cannot be read; the message is fit for the page (no path)."""


@contextmanager
def read_index(config: Config) -> Iterator[Store]:
    """The index, read-only, at the schema this flib reads; raises ``Unreadable``."""
    try:
        with Store.open_read_only(config.database) as store:
            activity_mod.check_schema(store)
            yield store
    except activity_mod.ActivityError as exc:
        raise Unreadable(str(exc)) from exc
    except sqlite3.Error as exc:
        raise Unreadable("cannot read the index") from exc


# --- small pieces ----------------------------------------------------------------------


def shown(name: str) -> str:
    """A name for the page without format characters (Unicode ``Cf``): a right-to-left
    override could make ``invoice\u202efdp.command`` read as ``invoicecommand.pdf``."""
    return "".join(c for c in name if unicodedata.category(c) != "Cf")


def short(path: Path, config: Config) -> str:
    """``path`` from the library or an input folder, as ``flib`` prints it (``shown``)."""
    for root in (config.output_root, *config.input_paths):
        if path.is_relative_to(root):
            return shown(str(path.relative_to(root)))
    return shown(str(path))


def local_time(instant: str | None) -> str:
    if not instant:
        return ""
    try:
        moment = datetime.fromisoformat(instant)
    except ValueError:
        return instant
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().strftime("%H:%M")


def money(value: float | None) -> str:
    """A cost as every page shows it: ``US$ 0.0123``, ``no cost``, ``no cost recorded``."""
    if value is None:
        return "no cost recorded"
    return "no cost" if value == 0 else f"US$ {value:.4f}"


def plural(count: int, word: str, many: str | None = None) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {many or word + 's'}"


def _state(count: int) -> str:
    return "states" if count == 1 else "state"


def sentence(text: str) -> str:
    """Text from another module as a sentence: a capital first letter, unless the
    first word is spelled with capitals of its own (``macOS``, ``iCloud``)."""
    first = text.split(" ", 1)[0]
    if first[1:] != first[1:].lower():
        return text
    return text[:1].upper() + text[1:]


def message(text: str, *, error: bool = False) -> Markup:
    """A notice; `backticks` mark commands (``<code>``)."""
    body = prose(sentence(text))
    return el("p", body, class_="notice error" if error else "notice", role="status")


def chips(links: list[tuple[str, str, bool]], label: str) -> Markup:
    """Links that pick a choice; the chosen one is marked for assistive technology."""
    return el(
        "nav",
        *(
            el("a", text, href=href, class_="chip", aria_current="true" if chosen else None)
            for text, href, chosen in links
        ),
        class_="chips",
        aria_label=label,
    )


def file_actions(file_id: int, name: str, csrf: str, *, can_open: bool) -> Markup:
    """Open and Show in Finder: forms that post the file's id, never its path. Open only
    for a document ``openable`` says macOS shows in an app rather than runs."""

    def action(path: str, label: str) -> Markup:
        return el(
            "form",
            csrf_field(csrf),
            el("input", type="hidden", name="id", value=file_id),
            el("button", label, type="submit", class_="small", aria_label=f"{label}: {name}"),
            method="post",
            action=path,
            class_="inline",
        )

    return el(
        "div",
        action("/files/open", "Open") if can_open else None,
        action("/files/reveal", "Show in Finder"),
        class_="acts",
    )


def totals_table(totals: list[AmountTotal] | list[due_mod.Total], caption: str) -> Markup:
    rows = [
        el(
            "tr",
            el("td", format_amount(t.total_minor, t.currency), class_="num"),
            el("td", t.files, class_="num"),
        )
        for t in totals
    ]
    head = el("tr", el("th", "Total", scope="col", class_="num"), el("th", "Files", **_NUM))
    return el(
        "table",
        el("caption", caption),
        el("thead", head),
        el("tbody", *rows),
        class_="totals",
    )


_NUM: dict[str, object] = {"scope": "col", "class_": "num"}


def query_params(query: str, known: frozenset[str]) -> dict[str, list[str]]:
    """The address's choices; any name but ``known`` is refused (400)."""
    try:
        params = parse_qs(query, keep_blank_values=True, max_num_fields=8)
    except ValueError as exc:
        raise BadRequest from exc
    if not set(params) <= known:
        raise BadRequest
    return params


class BadRequest(Exception):
    """A choice in the address or the form that the page does not offer (400)."""


def one_choice(params: dict[str, list[str]], name: str) -> str | None:
    """The one value of ``name`` in the address, or ``None``; two is a bad request."""
    values = params.get(name, [])
    if len(values) > 1:
        raise BadRequest
    return values[0] if values else None


def _choice(params: dict[str, list[str]], name: str, allowed: tuple[str, ...]) -> str | None:
    values = params.get(name, [])
    if not values:
        return None
    if len(values) != 1 or values[0] not in allowed:
        raise BadRequest
    return values[0]


# --- Activity --------------------------------------------------------------------------


def activity_page(config: Config, csrf: str, query: str) -> Page:
    params = query_params(query, frozenset({"days"}))
    days = int(_choice(params, "days", tuple(map(str, activity_mod.DAYS))) or 0) or (
        activity_mod.DEFAULT_DAYS
    )
    links = [(f"{d} days", f"/activity?days={d}", d == days) for d in activity_mod.DAYS]
    parts: list[Markup] = [
        el("h1", "Activity"),
        el("p", "What each run did, newest first.", class_="lead"),
        chips(links, "Window"),
    ]
    try:
        found = activity_mod.collect(config, days)
    except activity_mod.ActivityError as exc:
        parts.append(message(str(exc), error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout("Activity", "activity", join(parts), csrf)
    if not config.database.exists():
        parts.append(message("No run yet: the index is created by the first `flib run`."))
    elif not found.found:
        parts.append(message(f"No run in the last {plural(days, 'day')}."))
    for day in found.found:
        parts.append(_day(day, config))
    if found.capped:
        parts.append(
            message(f"Only the newest {activity_mod.MAX_RUNS} runs are shown; choose fewer days.")
        )
    return HTTPStatus.OK, layout("Activity", "activity", join(parts), csrf)


def _day(day: activity_mod.Day, config: Config) -> Markup:
    cost = money(day.cost_usd) if day.unpriced < len(day.runs) else money(None)
    if day.unpriced and day.unpriced < len(day.runs):
        cost += f" (and {plural(day.unpriced, 'run')} without a cost)"
    return el(
        "section",
        el("h2", f"{day.day:%A, %d %B %Y}"),
        el("p", f"{plural(len(day.runs), 'run')}, {cost}", class_="note"),
        *(_run(item, config) for item in day.runs),
        class_="day",
    )


def _run(item: activity_mod.RunActivity, config: Config) -> Markup:
    run = item.run
    trigger = activity_mod.TRIGGERS.get(run.trigger, run.trigger.value)
    title = f"{local_time(run.started_at)} · {trigger}"
    counts = [
        f"{run.files_moved or 0} organized",
        f"{run.files_review or 0} to review",
        f"{plural(run.files_duplicates or 0, 'duplicate')} set aside",
        f"{run.errors or 0} failed",
    ]
    facts: list[Markup] = [el("p", ", ".join(counts) + f"; {money(run.cost_usd)}.", class_="note")]
    if run.finished_at is None:
        facts.append(el("p", "It did not finish (still running, or stopped).", class_="note"))
    if item.sensitive:
        facts.append(
            el(
                "p",
                f"{plural(item.sensitive, 'sensitive file')} left in place, unread "
                "(not listed here).",
                class_="note",
            )
        )
    if item.settled:
        facts.append(el("p", f"{plural(item.settled, 'document')} marked done or open again."))
    if item.index_changes:
        facts.append(
            el("p", f"{plural(item.index_changes, 'change')} to the index.", class_="note")
        )
    unlock = _unlock(item.unlock)
    if unlock:
        facts.append(el("p", unlock, class_="note special"))
    moves = [_move(move, config) for move in item.moves]
    if item.more:
        moves.append(el("li", f"and {item.more} more", class_="note"))
    return el(
        "article",
        el("h3", title, " ", el("span", f"run {run.id}", class_="muted")),
        *facts,
        el("ul", *moves, class_="moves") if moves else None,
        class_="run",
    )


_WAITED = {
    "waiting": "it waited for the Mac to be unlocked",
    "unlocked": "it waited for the unlock, then ran once more",
    "timed out": "it waited for the unlock in vain; the next run gets them",
    "stopped": "its wait for the unlock was stopped; the next run gets them",
    "elsewhere": "an earlier run was already waiting for the unlock",
}


def _unlock(unlock: activity_mod.Unlock) -> str | None:
    parts = []
    if unlock.catch_up:
        parts.append("Run after the Mac was unlocked.")
    if unlock.locked:
        what = f"{plural(unlock.locked, 'file')} protected while the Mac is locked skipped"
        waited = _WAITED.get(unlock.waited or "")
        parts.append(f"{what}; {waited}." if waited else f"{what}.")
    return " ".join(parts) or None


def _move(move: activity_mod.Move, config: Config) -> Markup:
    """``name → Folder/`` (or ``Folder/new name`` when renamed): the folder this move
    used is its category, review or duplicates folder then, whatever the file is now."""
    tags: list[Markup] = []
    if move.fix:
        tags.append(el("span", "fixed by hand", class_="badge"))
    if move.status is OperationStatus.UNDONE:
        tags.append(el("span", "undone", class_="badge warn"))
    elif move.status is not OperationStatus.DONE:
        tags.append(el("span", move.status.value, class_="badge warn"))
    return el(
        "li",
        el("span", shown(move.source.name), class_="name"),
        el("span", " → ", aria_hidden="true"),
        el("span", "to ", class_="sr-only"),
        el("span", _destination(move, config), class_="where"),
        " ",
        *tags,
    )


def _destination(move: activity_mod.Move, config: Config) -> str:
    folder = short(move.destination.parent, config) + "/"
    if move.destination.name == move.source.name:
        return folder
    return folder + shown(move.destination.name)


# --- Search ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchForm:
    """The search form's fields, as typed; ``make_filters`` validates them."""

    q: str = ""
    categories: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    ext: str | None = None
    since: str | None = None
    until: str | None = None
    issuer: str | None = None
    min_amount: str | None = None
    max_amount: str | None = None
    currency: str | None = None
    due_before: str | None = None
    due_after: str | None = None
    period: str | None = None
    duplicates: bool = False
    semantic: bool = False
    by: str | None = None
    limit: int = DEFAULT_LIMIT

    @classmethod
    def parse(cls, fields: dict[str, list[str]]) -> SearchForm:
        def one(name: str) -> str | None:
            values = fields.get(name, [])
            value = values[0].strip() if values else ""
            return value or None

        limit = one("limit")
        by = one("by")
        if by is not None and by not in GROUPS:
            raise BadRequest  # the form offers fixed choices only
        if limit is not None and limit not in _LIMIT_TEXT:
            raise BadRequest
        return cls(
            q=one("q") or "",
            categories=split_values(fields.get("category")),
            kinds=split_values(fields.get("kind")),
            ext=one("ext"),
            since=one("since"),
            until=one("until"),
            issuer=one("issuer"),
            min_amount=one("min_amount"),
            max_amount=one("max_amount"),
            currency=one("currency"),
            due_before=one("due_before"),
            due_after=one("due_after"),
            period=one("period"),
            duplicates=one("duplicates") == "1",
            semantic=one("semantic") == "1",
            by=by,
            limit=int(limit) if limit is not None else DEFAULT_LIMIT,
        )

    @property
    def has_filters(self) -> bool:
        return bool(
            self.categories
            or self.kinds
            or any(
                v is not None
                for v in (
                    self.ext,
                    self.since,
                    self.until,
                    self.issuer,
                    self.min_amount,
                    self.max_amount,
                    self.currency,
                    self.due_before,
                    self.due_after,
                    self.period,
                )
            )
        )

    def filters(self, known: list[str]) -> SearchFilters:
        return make_filters(
            categories=self.categories,
            extensions=[self.ext] if self.ext else None,
            since=self.since,
            until=self.until,
            known_categories=known,
            issuer=self.issuer,
            kinds=self.kinds,
            min_amount=self.min_amount,
            max_amount=self.max_amount,
            currency=self.currency,
            due_before=self.due_before,
            due_after=self.due_after,
            period=self.period,
        )


_FLAGS = {
    "--since": "Processed from",
    "--until": "Processed until",
    "--min-amount": "At least",
    "--max-amount": "At most",
    "--due-before": "Due before",
    "--due-after": "Due after",
    "--currency": "Currency",
    "--period": "Period",
    "--issuer": "Issuer",
}


def _for_the_page(error: SearchError) -> str:
    """A filter error in the form's words rather than the command line's."""
    text = str(error)
    for flag, label in _FLAGS.items():
        text = text.replace(flag, label)
    return text[:1].upper() + text[1:]


@dataclass(slots=True)
class Found:
    hits: list[tuple[SearchHit, int | None]] = field(default_factory=list)
    """Each hit with its index id (for Open and Show in Finder)."""
    sums: Sums | None = None
    notice: str | None = None
    weak: int = 0
    sensitive: int = 0
    """Sensitive files that matched; never shown."""
    error: str | None = None
    categories: list[str] = field(default_factory=list)


NOTHING = "Give words to search for, a filter, or both."


class _QuickEmbedder(Embedder):
    """The embeddings client for a search from the interface: made only when the query
    is actually embedded, with at most ``EMBED_TIMEOUT`` per socket operation. A
    failure to make it is an ``EmbeddingError``, so the search falls back to words
    with its usual notice instead of failing the page."""

    def __init__(self, settings: SemanticConfig) -> None:  # the real client: in embed
        self._settings = settings.model_copy(
            update={"timeout_seconds": min(settings.timeout_seconds, EMBED_TIMEOUT)}
        )
        self._real: Embedder | None = None

    def embed(self, texts: Sequence[str]) -> list[array[float]]:
        if self._real is None:
            try:
                self._real = Embedder(self._settings)
            except Exception as exc:  # a client that cannot be made: search by words
                raise EmbeddingError(f"cannot set up ({type(exc).__name__})") from exc
        return self._real.embed(texts)


def find(config: Config, form: SearchForm | None, *, totals: bool = True) -> Found:
    """``flib search`` for the form; ``None`` is the empty form (categories only).
    Sensitive matches are left out, and never take the place of one that is shown:
    up to ``form.limit`` others are kept."""
    configured = [c.name for c in config.categories]
    found = Found(categories=configured)
    if not config.database.exists():
        found.notice = "No run yet: the index is created by the first `flib run`."
        if form is not None:
            try:
                form.filters(configured)
            except SearchError as exc:
                found.error = _for_the_page(exc)
        return found
    with read_index(config) as store:
        known = configured + [c for c in store.categories() if c not in configured]
        found.categories = known
        if form is None:
            return found
        if not form.q.strip() and not form.has_filters:
            found.error = NOTHING
            return found
        try:
            filters = form.filters(known)
        except SearchError as exc:
            found.error = _for_the_page(exc)
            return found
        # Sensitive matches are left out: fetch that many more, so they never take
        # the place of a match that can be shown.
        limit = form.limit + store.stats().by_status[FileStatus.SENSITIVE.value]
        if form.semantic and config.search.semantic.enabled:
            outcome = semantic_search(
                store,
                form.q,
                config,
                filters=filters,
                limit=limit,
                include_duplicates=form.duplicates,
                embedder=_QuickEmbedder(config.search.semantic),
            )
            hits, found.notice, found.weak = outcome.hits, outcome.notice, outcome.weak
        else:
            hits = words_search(
                store, form.q, filters=filters, limit=limit, include_duplicates=form.duplicates
            )
        for hit in hits:
            if hit.status == FileStatus.SENSITIVE.value:
                found.sensitive += 1
                continue
            if len(found.hits) < form.limit:
                row = store.file_by_current_path(Path(hit.path))
                found.hits.append((hit, None if row is None else row.id))
        if totals:
            found.sums = sum_amounts(store, form.q, filters=filters, by=form.by)
    return found


SEARCH_GONE = (
    "That search is no longer kept (only the latest ones are, until the session ends). "
    "Search again."
)


def search_page(
    config: Config, csrf: str, fields: dict[str, list[str]] | None, *, notice: str | None = None
) -> Page:
    """The form, and the results when ``fields`` (a kept search) are given."""
    form = None if fields is None else SearchForm.parse(fields)
    try:
        found = find(config, form)
    except Unreadable as exc:
        main = join(el("h1", "Search"), message(str(exc), error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout("Search", "search", main, csrf)
    results = form is not None and found.error is None
    parts: list[Markup | None] = [
        el("h1", "Search"),
        el(
            "p",
            "Words in a name, a description or a tag, and filters. Accents and case are ignored.",
            class_="lead",
        ),
        message(notice) if notice else None,
        _search_form(
            config,
            csrf,
            form or SearchForm(semantic=_semantic_default(config)),
            found,
            collapsed=results,
        ),
    ]
    title = "Search"
    if found.error:
        parts.append(message(found.error, error=True))
    elif form is not None:
        parts.append(_results(found, form, config, csrf))
        title = f"Search: {_matches(len(found.hits))}"
    elif found.notice:
        parts.append(message(found.notice))
    return HTTPStatus.OK, layout(title, "search", join(parts), csrf)


def _matches(count: int) -> str:
    return plural(count, "match", "matches")


def _semantic_default(config: Config) -> bool:
    semantic = config.search.semantic
    return semantic.enabled and semantic.default


def _search_form(
    config: Config, csrf: str, form: SearchForm, found: Found, *, collapsed: bool
) -> Markup:
    """The search bar, and the filters: open on an empty form, folded once results
    are shown (the results come right under it)."""

    def text(name: str, label: str, value: str | None, **attrs: object) -> Markup:
        return el(
            "label",
            el("span", label),
            el("input", type=attrs.pop("type", "text"), name=name, value=value or "", **attrs),
            class_="field",
        )

    def boxes(name: str, legend: str, options: list[str], chosen: list[str]) -> Markup:
        picked = {c.casefold() for c in chosen}
        return el(
            "fieldset",
            el("legend", legend),
            *(
                el(
                    "label",
                    el(
                        "input",
                        type="checkbox",
                        name=name,
                        value=option,
                        checked=option.casefold() in picked,
                    ),
                    " ",
                    option,
                    class_="chip",
                )
                for option in options
            ),
            class_="chips",
        )

    def select(name: str, label: str, options: list[tuple[str, str]], chosen: str) -> Markup:
        return el(
            "label",
            el("span", label),
            el(
                "select",
                *(
                    el("option", text, value=value, selected=value == chosen)
                    for value, text in options
                ),
                name=name,
            ),
            class_="field",
        )

    def check(name: str, label: str, on: bool) -> Markup:
        return el(
            "label",
            el("input", type="checkbox", name=name, value="1", checked=on),
            " ",
            label,
            class_="check",
        )

    more = el(
        "details",
        el("summary", "More filters"),
        el(
            "div",
            text("ext", "Extension", form.ext, placeholder="pdf"),
            text("since", "Processed from", form.since, type="date"),
            text("until", "Processed until", form.until, type="date"),
            text("period", "Period (year or month)", form.period, placeholder="2026-09"),
            text("issuer", "Issuer", form.issuer),
            text("min_amount", "At least", form.min_amount, inputmode="decimal"),
            text("max_amount", "At most", form.max_amount, inputmode="decimal"),
            text("currency", "Currency", form.currency, placeholder="USD", maxlength=3),
            text("due_after", "Due after", form.due_after, type="date"),
            text("due_before", "Due before", form.due_before, type="date"),
            select(
                "by",
                "Totals by",
                [("", "currency only"), *((g, g) for g in GROUPS)],
                form.by or "",
            ),
            select("limit", "Most results", [(str(n), str(n)) for n in LIMITS], str(form.limit)),
            class_="grid",
        ),
        check("duplicates", "Also duplicates set aside", form.duplicates),
        check("semantic", "Also by meaning (semantic search)", form.semantic)
        if config.search.semantic.enabled
        else None,
        open=_advanced(form),
    )
    return el(
        "form",
        csrf_field(csrf),
        el(
            "div",
            el(
                "label",
                el("span", "Words", class_="sr-only"),
                el(
                    "input",
                    type="search",
                    name="q",
                    value=form.q,
                    placeholder="What you remember about the file",
                    autofocus=True,
                ),
                class_="query",
            ),
            el("button", "Search", type="submit"),
            class_="searchbar",
        ),
        el(
            "details",
            el("summary", _filters_summary(form)),
            boxes("category", "Category", found.categories, form.categories),
            boxes("kind", "Kind of document", list(KINDS), form.kinds),
            more,
            class_="filters",
            open=not collapsed,
        ),
        el(
            "p",
            el("button", "Export CSV", type="submit", formaction="/search/export", class_="quiet"),
            class_="actions",
        ),
        method="post",
        action="/search",
        class_="search",
    )


def _filters_summary(form: SearchForm) -> str:
    used = len(form.categories) + len(form.kinds)
    used += sum(1 for flag in _more_filters(form) if flag)
    return "Filters" if not used else f"Filters ({used} in use)"


def _more_filters(form: SearchForm) -> list[bool]:
    hidden: list[object] = [form.ext, form.since, form.until, form.period, form.issuer]
    hidden += [form.min_amount, form.max_amount, form.currency, form.due_after, form.due_before]
    return [v is not None for v in hidden] + [form.duplicates]


def _advanced(form: SearchForm) -> bool:
    """The more filters stay open when one of them is in use."""
    hidden = [form.ext, form.since, form.until, form.period, form.issuer, form.min_amount]
    hidden += [form.max_amount, form.currency, form.due_after, form.due_before, form.by]
    return any(v is not None for v in hidden) or form.duplicates or form.limit != DEFAULT_LIMIT


def _results(found: Found, form: SearchForm, config: Config, csrf: str) -> Markup:
    """The count and the totals first, right under the form; then each match."""
    parts: list[Markup | None] = []
    if found.notice:
        parts.append(message(found.notice))
    if found.sensitive:
        parts.append(
            message(
                f"{plural(found.sensitive, 'sensitive file')} also matched; they are never "
                "shown here (`flib search` in Terminal lists them)."
            )
        )
    count = len(found.hits)
    if not found.hits:
        if found.weak:
            parts.append(message("No close matches; try other words, or search by words only."))
        elif config.database.exists():
            parts.append(message("No matches."))
    else:
        more = " (the most asked for)" if count >= form.limit else ""
        parts.append(el("h2", _matches(count) + more, class_="count"))
    if found.sums is not None and (found.hits or found.sums.totals):
        parts.append(_sums(found.sums, semantic=form.semantic))
    if found.hits:
        items = [_hit(hit, i, config, csrf) for hit, i in found.hits]
        parts.append(el("ol", *items, class_="hits", aria_label="Matches"))
    return el("section", *parts, class_="results", aria_label="Results")


def _input_of(path: Path, config: Config) -> str:
    """The input folder ``path`` is in, by name (``Downloads``)."""
    for root in config.input_paths:
        if path.is_relative_to(root):
            return shown(root.name)
    return shown(path.parent.name)


def _hit(hit: SearchHit, file_id: int | None, config: Config, csrf: str) -> Markup:
    """One match, as ``flib search`` lists it. A file that is not filed (put back by
    ``flib undo``, missing, failed) says where it is and offers no button: those
    are for files in the library."""
    path = Path(hit.path)
    status = hit.status
    filed = status in {s.value for s in OPENABLE}
    tags: list[Markup] = []
    if status == FileStatus.REVIEW.value:
        tags.append(el("span", "in review", class_="badge"))
    elif filed and hit.category:
        tags.append(el("span", hit.category, class_="badge"))
    elif status == FileStatus.SKIPPED.value:
        tags.append(el("span", f"not filed: back in {_input_of(path, config)}", class_="badge"))
    elif not filed:
        tags.append(el("span", status, class_="badge"))
    if not hit.exists:
        tags.append(el("span", "missing: moved by hand?", class_="badge warn"))
    where = short(path.parent, config)
    details: list[Markup | None] = [
        el("div", el("strong", shown(path.name), class_="name"), " ", *tags, class_="hit-head"),
        el("div", where + "/" if where != "." else _input_of(path, config) + "/", class_="where"),
    ]
    if hit.original_name != path.name:
        details.append(
            el("div", "was ", el("span", shown(hit.original_name), class_="name"), class_="note")
        )
    if hit.duplicate_of is not None:
        details.append(
            el("div", "duplicate of ", short(Path(hit.duplicate_of), config), class_="note")
        )
    if (line := hit.fields_line()) is not None:
        details.append(el("div", line, class_="fields"))
    if hit.description:
        details.append(el("p", hit.description, class_="desc"))
    if hit.tags:
        details.append(
            el("ul", *(el("li", tag) for tag in hit.tags), class_="tags", aria_label="Tags")
        )
    if file_id is not None and hit.exists and filed:
        can_open = openable(path)
        details.append(file_actions(file_id, shown(path.name), csrf, can_open=can_open))
    return el("li", *details, class_="hit")


def _sums(sums: Sums, *, semantic: bool) -> Markup:
    parts: list[Markup | None] = [el("h2", "Totals")]
    note = "Over every match by words (never duplicates), not only those listed."
    if semantic:
        note += " Totals match the words only, not by meaning."
    parts.append(el("p", note, class_="note"))
    if not sums.totals:
        parts.append(message("No amounts among the matches."))
    if sums.groups:
        ordered = sorted(
            sums.groups, key=lambda t: (t.group is None, (t.group or "").casefold(), t.currency)
        )
        rows = [
            el(
                "tr",
                el("td", t.group or "(none)"),
                el("td", format_amount(t.total_minor, t.currency), class_="num"),
                el("td", t.files, class_="num"),
            )
            for t in ordered
        ]
        parts.append(
            el(
                "table",
                el("caption", f"By {sums.by}"),
                el(
                    "thead",
                    el(
                        "tr",
                        el("th", str(sums.by).capitalize(), scope="col"),
                        el("th", "Total", **_NUM),
                        el("th", "Files", **_NUM),
                    ),
                ),
                el("tbody", *rows),
                class_="totals",
            )
        )
    if sums.totals:
        parts.append(totals_table(sums.totals, "Per currency"))
    if sums.without_amount:
        parts.append(
            el(
                "p",
                f"{plural(sums.without_amount, 'matching file')} {_state(sums.without_amount)} "
                "no amount; not counted.",
                class_="note",
            )
        )
    return el("section", *parts, class_="sums")


# --- CSV export ------------------------------------------------------------------------

CSV_COLUMNS = (
    "path",
    "original_name",
    "category",
    "status",
    "kind",
    "issuer",
    "amount",
    "currency",
    "document_date",
    "due_date",
    "period",
    "processed_at",
    "description",
    "tags",
)
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def cell(value: object, *, number: bool = False) -> str:
    """A CSV cell a spreadsheet never runs as a formula: text that starts like one gets
    a leading apostrophe. ``number``: a decimal amount stays as it is."""
    text = "" if value is None else str(value)
    if number:
        try:
            Decimal(text)
        except InvalidOperation:
            pass
        else:
            return text
    return "'" + text if text.startswith(_FORMULA) else text


EXPORT_LIMIT = 5000
"""Most matches in a CSV export, whatever the page's limit; a last row says when cut."""
CUT_ROW = "# cut: more than {limit} matches; only the first {limit} are here. Narrow the search."


def search_csv(config: Config, fields: dict[str, list[str]]) -> tuple[int, bytes, str]:
    """The matches as CSV (status, body, type), or an error page. Up to ``EXPORT_LIMIT``
    matches, not the page's limit; a last row says so when there were more."""
    form = dataclasses.replace(SearchForm.parse(fields), limit=EXPORT_LIMIT + 1)
    try:
        found = find(config, form, totals=False)
    except Unreadable as exc:
        return _plain_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
    if found.error:
        return _plain_error(HTTPStatus.BAD_REQUEST, found.error)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    cut = len(found.hits) > EXPORT_LIMIT  # sensitive matches are left out, not counted
    for hit, _ in found.hits[:EXPORT_LIMIT]:
        writer.writerow(
            [
                cell(hit.path),
                cell(hit.original_name),
                cell(hit.category),
                cell(hit.status),
                cell(hit.kind),
                cell(hit.issuer),
                cell(hit.amount, number=True),
                cell(hit.currency),
                cell(hit.document_date),
                cell(hit.due_date),
                cell(hit.period),
                cell(hit.processed_at),
                cell(hit.description),
                cell(", ".join(hit.tags)),
            ]
        )
    if cut:
        writer.writerow([CUT_ROW.format(limit=EXPORT_LIMIT)])
    return HTTPStatus.OK, out.getvalue().encode("utf-8"), "text/csv; charset=utf-8"


def _plain_error(code: int, text: str) -> tuple[int, bytes, str]:
    return code, (text + "\n").encode("utf-8"), "text/plain; charset=utf-8"


# --- Due -------------------------------------------------------------------------------


def due_page(config: Config, csrf: str, query: str) -> Page:
    params = query_params(query, frozenset({"within", "overdue", "all"}))
    within = _choice(params, "within", tuple(code for code, _ in WITHIN))
    overdue = _choice(params, "overdue", ("1",)) == "1"
    show_all = _choice(params, "all", ("1",)) == "1"
    if overdue and within is not None:
        raise BadRequest
    chosen = None if overdue else (within or "7d")
    extra = "&all=1" if show_all else ""
    links = [(text, f"/due?within={code}{extra}", code == chosen) for code, text in WITHIN]
    links.append(("Overdue", f"/due?overdue=1{extra}", overdue))
    window = "overdue=1" if overdue else f"within={chosen}"
    toggle = (
        el("a", "Hide those marked done", href=f"/due?{window}")
        if show_all
        else el("a", "Also those marked done, and other kinds", href=f"/due?{window}&all=1")
    )
    parts: list[Markup | None] = [
        el("h1", "Due"),
        el(
            "p",
            "Bills and other documents with a due date the AI read in them; nothing is guessed.",
            class_="lead",
        ),
        chips(links, "Window"),
        el("p", toggle, class_="note"),
    ]
    query_ = due_mod.make_query(within=within, overdue=overdue, include_all=show_all)
    try:
        found = due_mod.find(config, query_)
    except due_mod.DueError as exc:
        text = "Cannot read the index" if isinstance(exc.__cause__, sqlite3.Error) else str(exc)
        parts.append(message(text, error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout("Due", "due", join(parts), csrf)
    if found.start is None:
        span = f"Past due, however long ago (today is {found.today.isoformat()})."
    else:
        span = (
            f"Overdue since {found.start.isoformat()} ({due_mod.LOOKBACK_DAYS} days back) "
            f"and due up to {found.end.isoformat()}; today is {found.today.isoformat()}."
        )
    parts.append(el("p", span, class_="note"))
    if not found.items:
        parts.append(message("Nothing due in this window."))
    else:
        parts.append(_due_table(found, config, csrf))
        if found.totals:
            parts.append(totals_table(found.totals, "Total open, per currency"))
        if found.without_amount:
            parts.append(
                el(
                    "p",
                    f"{plural(found.without_amount, 'document')} {_state(found.without_amount)} "
                    "no amount; not counted.",
                    class_="note",
                )
            )
    if found.hidden:
        parts.append(
            el(
                "p",
                f"{plural(found.hidden, 'document')} marked done or of another kind not shown.",
                class_="note",
            )
        )
    return HTTPStatus.OK, layout("Due", "due", join(parts), csrf)


def _due_actions(item: due_mod.DueItem, csrf: str) -> Markup | None:
    if not item.exists:
        return None
    name = shown(item.path.name)
    return file_actions(item.file_id, name, csrf, can_open=openable(item.path))


def _due_table(found: due_mod.DueList, config: Config, csrf: str) -> Markup:
    rows = []
    for item in found.items:
        when = due_mod.relative(item.days)
        state = None
        if item.done:
            state = el(
                "span", "done" if item.done_by == "fix" else "done (Finder tag)", class_="badge"
            )
        elif item.overdue:
            state = el("span", "overdue", class_="badge warn")
        if not item.exists:
            state = join(state, " ", el("span", "missing: moved by hand?", class_="badge warn"))
        elif item.status == FileStatus.REVIEW.value:
            state = join(state, " ", el("span", "in review", class_="badge"))
        amount = (
            format_amount(item.amount_minor, item.currency)
            if item.amount_minor is not None and item.currency is not None
            else "-"
        )
        day = item.due_date.isoformat()
        rows.append(
            el(
                "tr",
                el("td", el("time", day, datetime=day), class_="date", data_label="Due"),
                el("td", when, " ", state, data_label="When"),
                el("td", amount, class_="num", data_label="Amount"),
                el("td", item.issuer or "-", data_label="Issuer"),
                el("td", item.kind or "-", data_label="Kind"),
                el(
                    "td",
                    el("span", shown(item.path.name), class_="name"),
                    el("div", short(item.path.parent, config) + "/", class_="where"),
                    class_="file",
                    data_label="File",
                ),
                el("td", _due_actions(item, csrf), class_="buttons"),
                class_="overdue" if item.overdue else None,
            )
        )
    head = [el("th", h, scope="col") for h in ("Due", "When")]
    head.append(el("th", "Amount", **_NUM))
    head += [el("th", h, scope="col") for h in ("Issuer", "Kind", "File")]
    head.append(el("th", el("span", "Actions", class_="sr-only"), scope="col"))
    return el(
        "table",
        el("thead", el("tr", *head)),
        el("tbody", *rows),
        class_="due",
    )


# --- Open and Show in Finder -----------------------------------------------------------

Opener = Callable[[list[str]], int]


def _run_open(argv: list[str]) -> int:
    # A fixed binary; the path is one argument, never parsed by a shell.
    result = subprocess.run(  # noqa: S603
        argv, capture_output=True, timeout=OPEN_TIMEOUT, check=False
    )
    return result.returncode


def real_location(path: Path, config: Config) -> Path | None:
    """Where ``path`` really is, when that is in the library or an input folder and the
    way there has no ``..`` and no symbolic link below that folder; else ``None``. (A
    link above the folder, such as ``/var`` to ``/private/var``, is the folder's own.)"""
    text = str(path)
    if not path.is_absolute() or os.path.normpath(text) != text:
        return None
    real = Path(os.path.realpath(text))
    for root in (config.output_root, *config.input_paths):
        if not path.is_relative_to(root):
            continue
        real_root = Path(os.path.realpath(root))
        if real == real_root / path.relative_to(root) and is_within(real, real_root):
            return real
    return None


def openable(path: Path) -> bool:
    """A document macOS shows in its usual app, never a program it would run: a
    regular file (not a folder, a bundle or a link) with no execute bit and an
    extension in ``OPENABLE_EXTENSIONS``, that chose no app of its own (no per-file
    "Open With", no Finder type or creator code) and is not an alias. An attribute
    that cannot be read counts against it. Never raises."""
    try:
        info = os.lstat(path)
        finder = read_xattr(path, _FINDER_INFO)
        open_with = read_xattr(path, _OPEN_WITH)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o111:
        return False
    if path.suffix.lower().removeprefix(".") not in OPENABLE_EXTENSIONS:
        return False
    if open_with is not None:
        return False
    if finder is None:
        return True
    if any(finder[:8]):  # a type or creator code picks the app
        return False
    return not (len(finder) >= 10 and (finder[8] << 8) & _IS_ALIAS)


def open_by_id(
    config: Config,
    fields: dict[str, list[str]],
    *,
    reveal: bool,
    program: str,
    runner: Opener = _run_open,
) -> tuple[int, str | None]:
    """Ask macOS to open (or show in Finder) the file with this index id.

    ``(204, None)`` when it was asked; otherwise a status and a message for the
    page, in general words."""
    values = fields.get("id", [])
    if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 18:
        return HTTPStatus.BAD_REQUEST, "No file was named."
    if not config.database.exists():
        return HTTPStatus.NOT_FOUND, "That file is not in the index."
    try:
        with read_index(config) as store:
            row = store.file_by_id(int(values[0]))
    except LookupError:
        return HTTPStatus.NOT_FOUND, "That file is not in the index."
    except Unreadable as exc:
        return HTTPStatus.SERVICE_UNAVAILABLE, str(exc)
    if row.status not in OPENABLE:
        return HTTPStatus.FORBIDDEN, "That file is not one the interface opens."
    real = real_location(row.current_path, config)
    if real is None:
        return HTTPStatus.FORBIDDEN, "That file is not in the library or an input folder."
    try:
        os.lstat(real)
    except OSError:
        return HTTPStatus.NOT_FOUND, "That file is no longer where the index says."
    if not reveal and not openable(real):
        return HTTPStatus.FORBIDDEN, (
            "Only documents are opened from here; this one could run a program or go "
            "somewhere. Use Show in Finder instead."
        )
    # The path is one argument after "--": never an option, never parsed by a shell.
    argv = [program, "-R", "--", str(real)] if reveal else [program, "--", str(real)]
    try:
        code = runner(argv)
    except (OSError, subprocess.SubprocessError):
        code = -1
    if code != 0:
        return HTTPStatus.BAD_GATEWAY, "macOS could not open it."
    return HTTPStatus.NO_CONTENT, None


def problem_page(code: int, text: str, csrf: str | None) -> Page:
    """A refusal as a page with the menu; ``csrf`` ``None`` for a browser without the
    session (no End session button)."""
    main = join(
        el("h1", HTTPStatus(code).phrase),
        message(text, error=True),
    )
    return code, layout(HTTPStatus(code).phrase, "", main, csrf)
