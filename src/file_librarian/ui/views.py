"""The interface's read-only pages: Activity, Search and Due (#114).

Each page reads through the same modules as the command line and the MCP
server (``activity``, ``search``, ``due``) and is built on the server with
``markup``, so it reads without JavaScript and every value from the index is
escaped. Nothing here writes: the index is opened read-only, never created
or migrated. The forms that change something (Due's Mark done, Activity's
Undo links) go to ``actions`` through the server's route table (#115).

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
import re
import sqlite3
import stat
import subprocess
import unicodedata
from array import array
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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
from file_librarian.fields import KINDS
from file_librarian.i18n import Msg, english, formats, t
from file_librarian.i18n.core import core
from file_librarian.models import FileStatus, OperationStatus
from file_librarian.search import (
    SearchError,
    SearchFilters,
    SearchHit,
    Sums,
    fts_query,
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
WITHIN = (
    ("7d", t("due.within.7d")),
    ("2w", t("due.within.2w")),
    ("1m", t("due.within.1m")),
    ("3m", t("due.within.3m")),
)
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


def unreadable(exc: Unreadable) -> str:
    """What an ``Unreadable`` says: the message it was given (a ``Msg``, kept as one)."""
    return exc.args[0] if exc.args and isinstance(exc.args[0], str) else str(exc)


@contextmanager
def read_index(config: Config) -> Iterator[Store]:
    """The index, read-only, at the schema this flib reads; raises ``Unreadable``."""
    try:
        with Store.open_read_only(config.database) as store:
            activity_mod.check_schema(store)
            yield store
    except activity_mod.ActivityError as exc:
        raise Unreadable(core(english(exc))) from exc
    except sqlite3.Error as exc:
        raise Unreadable(t("common.unreadable")) from exc


# --- small pieces ----------------------------------------------------------------------


def shown(name: str) -> str:
    """A name for the page without format characters (Unicode ``Cf``): a right-to-left
    override could make ``invoice\u202efdp.command`` read as ``invoicecommand.pdf``."""
    return "".join(c for c in name if unicodedata.category(c) != "Cf")


def filename(name: str) -> Markup:
    """A file name on a page: as text (``shown``), isolated and left to right (``<bdi
    dir="ltr">``), so a right-to-left name keeps its order and its neighbours theirs."""
    return el("bdi", shown(name), class_="name", dir="ltr")


def short(path: Path, config: Config) -> str:
    """``path`` from the library or an input folder, as ``flib`` prints it (``shown``)."""
    for root in (config.output_root, *config.input_paths):
        if path.is_relative_to(root):
            return shown(str(path.relative_to(root)))
    return shown(str(path))


def folder(path: Path, config: Config) -> str:
    """The folder ``path`` is in, as the pages show it: ``Images/``, ``Downloads/``."""
    where = short(path.parent, config)
    return (where + "/") if where != "." else (shown(path.parent.name) + "/")


def local_time(instant: str | None) -> str:
    if not instant:
        return ""
    try:
        moment = datetime.fromisoformat(instant)
    except ValueError:
        return instant
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return formats.clock(moment.astimezone())


def money(value: float | None) -> str:
    """A cost as every page shows it: ``US$ 0.0123``, ``no cost``, ``no cost recorded``."""
    if value is None:
        return t("common.no_cost_recorded")
    return t("common.no_cost") if value == 0 else formats.usd(value)


def plural(count: int, word: str, many: str | None = None) -> str:
    """English only: for words not in the catalogue (``i18n``)."""
    return f"{count} {word}" if count == 1 else f"{count} {many or word + 's'}"


def sentence(text: str) -> str:
    """Text from another module as a sentence: a capital first letter, unless the
    first word is spelled with capitals of its own (``macOS``, ``iCloud``). A message
    of the catalogue is written as a sentence already: it is left as it is."""
    if isinstance(text, Msg) or not text:
        return text
    first = text.split(" ", 1)[0]
    if first[1:] != first[1:].lower():
        return text
    return text[:1].upper() + text[1:]


def message(text: str, *, error: bool = False) -> Markup:
    """A notice; `backticks` mark commands (``<code>``)."""
    body = prose(sentence(text))
    return el("p", body, class_="notice error" if error else "notice", role="status")


def chips(links: Sequence[tuple[str, str, bool]], label: str) -> Markup:
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


def file_actions(
    file_id: int,
    name: str,
    csrf: str,
    *,
    can_open: bool,
    back: Sequence[tuple[str, str]] = (),
) -> Markup:
    """Open and Show in Finder: forms that post the file's id, never its path. Open only
    for a document ``openable`` says macOS shows in an app rather than runs. ``back``:
    the page they are on (``back_to`` reads it), where a refusal is shown (#127)."""

    def action(path: str, label: Msg, named: Msg) -> Markup:
        return el(
            "form",
            csrf_field(csrf),
            el("input", type="hidden", name="id", value=file_id),
            *(el("input", type="hidden", name=k, value=v) for k, v in back),
            el("button", label, type="submit", class_="small", aria_label=named),
            method="post",
            action=path,
            class_="inline",
        )

    return el(
        "div",
        action("/files/open", t("common.open"), t("common.open_named", name=name))
        if can_open
        else None,
        action("/files/reveal", t("common.reveal"), t("common.reveal_named", name=name)),
        class_="acts",
    )


def totals_table(totals: list[AmountTotal] | list[due_mod.Total], caption: str) -> Markup:
    rows = [
        el(
            "tr",
            el("td", formats.amount(total.total_minor, total.currency), class_="num"),
            el("td", total.files, class_="num"),
        )
        for total in totals
    ]
    head = el(
        "tr",
        el("th", t("views.total"), scope="col", class_="num"),
        el("th", t("views.files"), **_NUM),
    )
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


KEPT_KEY = re.compile(r"[A-Za-z0-9_-]{16}")
"""A kept search's or outcome's id in an address (``secrets.token_urlsafe(12)``)."""
BACK_PAGES = ("search", "due", "review")
"""The pages whose Open and Show in Finder buttons say where they are (``back_to``)."""


def back_to(fields: dict[str, list[str]]) -> str | None:
    """The address of the page an Open or Show in Finder form came from, built here
    from fixed parts only (never from the form's text): ``/search`` with the kept
    search's id, ``/due`` with its window, ``/review``. ``None`` when the form names no
    page (a script); ``BadRequest`` for anything the pages never send."""
    page = _choice(fields, "back", BACK_PAGES)
    if page is None:
        if fields.keys() & {"r", *DUE_PARAMS}:
            raise BadRequest
        return None
    if page == "search":
        if fields.keys() & DUE_PARAMS:
            raise BadRequest
        key = one_choice(fields, "r")
        if key is None:
            return "/search"
        if not KEPT_KEY.fullmatch(key):
            raise BadRequest
        return f"/search?r={key}"
    if "r" in fields:
        raise BadRequest
    if page == "due":
        window = due_window(fields)
        return f"/due?{window}" if window else "/due"
    if fields.keys() & DUE_PARAMS:
        raise BadRequest
    return "/review"


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
    links = [
        (t("activity.days", count=d), f"/activity?days={d}", d == days) for d in activity_mod.DAYS
    ]
    title = t("nav.activity")
    parts: list[Markup] = [
        el("h1", title),
        el("p", t("activity.lead"), class_="lead"),
        chips(links, t("views.window")),
    ]
    try:
        found = activity_mod.collect(config, days)
    except activity_mod.ActivityError as exc:
        parts.append(message(core(english(exc)), error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout(title, "activity", join(parts), csrf)
    if not config.database.exists():
        parts.append(message(t("common.no_index")))
    elif not found.found:
        parts.append(message(t("activity.none", count=days)))
    for day in found.found:
        parts.append(_day(day, config))
    if found.capped:
        parts.append(message(t("activity.capped", count=activity_mod.MAX_RUNS)))
    return HTTPStatus.OK, layout(title, "activity", join(parts), csrf)


def _day(day: activity_mod.Day, config: Config) -> Markup:
    count = len(day.runs)
    cost = money(day.cost_usd) if day.unpriced < count else money(None)
    if day.unpriced and day.unpriced < count:
        unpriced = t("activity.runs_without_cost", count=day.unpriced)
        line = t("activity.day_runs_unpriced", count=count, cost=cost, unpriced=unpriced)
    else:
        line = t("activity.day_runs", count=count, cost=cost)
    return el(
        "section",
        el("h2", formats.long_day(day.day)),
        el("p", line, class_="note"),
        *(_run(item, config) for item in day.runs),
        class_="day",
    )


TRIGGERS: dict[str, Msg] = {
    "schedule": t("activity.trigger.schedule"),
    "manual": t("activity.trigger.manual"),
    "mcp": t("activity.trigger.mcp"),
    "reprocess": t("activity.trigger.reprocess"),
    "watch": t("activity.trigger.watch"),
}
"""``activity.TRIGGERS`` in the catalogue's words."""


def trigger_label(trigger: object) -> str:
    value = str(getattr(trigger, "value", trigger))
    return TRIGGERS.get(value, value)


def _run(item: activity_mod.RunActivity, config: Config) -> Markup:
    run = item.run
    counts = t(
        "activity.run_counts",
        organized=t("activity.organized", count=run.files_moved or 0),
        review=t("activity.to_review", count=run.files_review or 0),
        duplicates=t("activity.duplicates", count=run.files_duplicates or 0),
        failed=t("activity.failed", count=run.errors or 0),
        cost=money(run.cost_usd),
    )
    facts: list[Markup] = [el("p", counts, class_="note")]
    if run.finished_at is None:
        facts.append(el("p", t("activity.unfinished"), class_="note"))
    if item.sensitive:
        facts.append(el("p", t("activity.sensitive", count=item.sensitive), class_="note"))
    if item.settled:
        facts.append(el("p", t("activity.settled", count=item.settled)))
    if item.index_changes:
        facts.append(el("p", t("activity.index_changes", count=item.index_changes), class_="note"))
    unlock = _unlock(item.unlock)
    if unlock:
        facts.append(el("p", unlock, class_="note special"))
    moves = [_move(move, config) for move in item.moves]
    if item.more:
        moves.append(el("li", t("views.more", count=item.more), class_="note"))
    undo = None
    if item.undoable:
        undo = el(
            "p",
            el(
                "a",
                t("activity.undo_run"),
                href=f"/undo?run={run.id}",
                aria_label=t("activity.undo_run_named", run=run.id),
            ),
            class_="note",
        )
    return el(
        "article",
        el(
            "h3",
            local_time(run.started_at),
            " · ",
            trigger_label(run.trigger),
            " ",
            el("span", t("activity.run_number", run=run.id), class_="muted"),
        ),
        *facts,
        el("ul", *moves, class_="moves") if moves else None,
        undo,
        class_="run",
    )


_WAITED = {
    "waiting": "activity.waited.waiting",
    "unlocked": "activity.waited.unlocked",
    "timed out": "activity.waited.timed_out",
    "stopped": "activity.waited.stopped",
    "elsewhere": "activity.waited.elsewhere",
}


def _unlock(unlock: activity_mod.Unlock) -> Markup | None:
    """What a run's log says about the Mac being locked: whole sentences, one by one."""
    parts: list[Msg] = []
    if unlock.catch_up:
        parts.append(t("activity.catch_up"))
    if unlock.locked:
        waited = _WAITED.get(unlock.waited or "")
        if waited:
            parts.append(t("activity.locked_waited", count=unlock.locked, waited=t(waited)))
        else:
            parts.append(t("activity.locked", count=unlock.locked))
    if not parts:
        return None
    return join(*(join(" " if i else None, part) for i, part in enumerate(parts)))


OPERATION_STATES = {
    "planned": t("activity.op.planned"),
    "failed": t("activity.op.failed"),
    "skipped": t("activity.op.skipped"),
}
"""A journal entry's status (``OperationStatus``) as a badge says it."""


def _move(move: activity_mod.Move, config: Config) -> Markup:
    """``name → Folder/`` (or ``Folder/new name`` when renamed): the folder this move
    used is its category, review or duplicates folder then, whatever the file is now."""
    tags: list[Markup] = []
    if move.fix:
        tags.append(el("span", t("activity.fixed_by_hand"), class_="badge"))
    if move.status is OperationStatus.UNDONE:
        tags.append(el("span", t("activity.undone"), class_="badge warn"))
    elif move.status is not OperationStatus.DONE:
        state = OPERATION_STATES.get(move.status.value, move.status.value)
        tags.append(el("span", state, class_="badge warn"))
    undo = None
    if move.last and move.status is OperationStatus.DONE:
        name = shown(move.destination.name)
        undo = el(
            "a",
            t("activity.undo_move"),
            href=f"/undo?file={move.file_id}",
            aria_label=t("activity.undo_move_named", name=name),
        )
    return el(
        "li",
        filename(move.source.name),
        el("span", " → ", aria_hidden="true"),
        el("span", t("activity.to"), class_="sr-only"),
        el("bdi", _destination(move, config), class_="where", dir="ltr"),
        " ",
        *tags,
        " " if undo else None,
        undo,
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
            min_amount=_amount(self.min_amount, "--min-amount"),
            max_amount=_amount(self.max_amount, "--max-amount"),
            currency=self.currency,
            due_before=self.due_before,
            due_after=self.due_after,
            period=self.period,
        )


class AmountRefused(SearchError):
    """An amount typed in a filter that ``formats.parse_amount`` refuses; ``msg`` says why
    in the page's words."""

    def __init__(self, msg: Msg) -> None:
        super().__init__(str(msg))
        self.msg = msg


_AMOUNT_FIELDS = {
    "--min-amount": "search.label.min_amount",
    "--max-amount": "search.label.max_amount",
}


def _amount(text: str | None, flag: str) -> str | None:
    """A filter's amount read by the one rule every typed amount follows (``formats.
    parse_amount``: ``1.234,56`` refused, ``1.234`` refused in pt-BR as it could be read
    two ways), handed on as a plain decimal (``1234.5``)."""
    if text is None:
        return None
    try:
        value = formats.parse_amount(text, whole=12)
    except formats.AmountError as exc:
        field = t(_AMOUNT_FIELDS[flag])
        if exc.kind == "ambiguous":
            raise AmountRefused(t("search.amount_ambiguous", field=field, typed=text)) from exc
        raise AmountRefused(t("core.search.amount", field=field, value=repr(text))) from exc
    return str(value)


def _for_the_page(error: SearchError) -> str:
    """A filter error in the form's words rather than the command line's (``core``
    knows each of them; one it does not is shown as it is, capitalised)."""
    if isinstance(error, AmountRefused):
        return error.msg
    text = core(english(error))
    return text if isinstance(text, Msg) else text[:1].upper() + text[1:]


@dataclass(slots=True)
class Found:
    hits: list[tuple[SearchHit, int | None]] = field(default_factory=list)
    """Each hit with its index id (for Open and Show in Finder)."""
    sums: Sums | None = None
    notice: str | None = None
    weak: int = 0
    sensitive: int = 0
    """Sensitive files that matched; never shown."""
    total: int | None = None
    """Every match by words that may be shown (no sensitive file), however many are
    listed; ``None`` for a search by meaning, which keeps only the closest (#127)."""
    error: str | None = None
    categories: list[str] = field(default_factory=list)


NOTHING = t("search.nothing")


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
        found.notice = t("common.no_index")
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
            notice = None if outcome.notice is None else core(outcome.notice)
            hits, found.notice, found.weak = outcome.hits, notice, outcome.weak
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
        if not form.semantic or not config.search.semantic.enabled:
            # By words, every match is known: all fetched, or counted when there are more.
            everything = len(hits) < limit
            found.total = (
                len(hits) - found.sensitive
                if everything
                else count_matches(store, form.q, filters, form.duplicates)
            )
        if totals:
            found.sums = sum_amounts(store, form.q, filters=filters, by=form.by)
    return found


SEARCH_GONE = t("search.gone")


def search_page(
    config: Config,
    csrf: str,
    fields: dict[str, list[str]] | None,
    *,
    notice: str | None = None,
    key: str | None = None,
    outcome: Markup | None = None,
) -> Page:
    """The form, and the results when ``fields`` (a kept search, ``key`` its id) are
    given. ``outcome``: what an Open or Show in Finder from this page refused (#127)."""
    form = None if fields is None else SearchForm.parse(fields)
    try:
        found = find(config, form)
    except Unreadable as exc:
        main = join(el("h1", t("nav.search")), message(unreadable(exc), error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout(t("nav.search"), "search", main, csrf)
    results = form is not None and found.error is None
    parts: list[Markup | None] = [
        el("h1", t("nav.search")),
        el("p", t("search.lead"), class_="lead"),
        outcome,
        message(notice) if notice else None,
        _search_form(
            config,
            csrf,
            form or SearchForm(semantic=_semantic_default(config)),
            found,
            collapsed=results,
        ),
    ]
    title = t("nav.search")
    if found.error:
        parts.append(message(found.error, error=True))
    elif form is not None:
        back = (("back", "search"),) + ((("r", key),) if key is not None else ())
        parts.append(_results(found, form, config, csrf, back))
        title = _title(found, form)
    elif found.notice:
        parts.append(message(found.notice))
    return HTTPStatus.OK, layout(title, "search", join(parts), csrf)


def _title(found: Found, form: SearchForm) -> Msg:
    """The page's title says how many matched: all of them, or how many of how many
    are listed (``Search: 50 of 111 matches``), never just the page's limit."""
    count = len(found.hits)
    if found.total is not None and found.total > count:
        return t("search.title_of", shown=count, count=found.total)
    if found.total is None and count >= form.limit:
        return t("search.title_capped", count=count)
    return t("search.title_matches", count=count)


def count_matches(store: Store, text: str, filters: SearchFilters, include_duplicates: bool) -> int:
    """How many files the search by words matches, sensitive files left out (they are
    counted apart): what ``words_search`` would list without a limit."""
    if fts_query(text) is None and not filters:
        return 0
    return store.count_files(
        fts_query(text),
        categories=filters.categories,
        extensions=filters.extensions,
        since=filters.since,
        until=filters.until,
        include_duplicates=include_duplicates,
        fields=filters.fields,
    )


def _semantic_default(config: Config) -> bool:
    semantic = config.search.semantic
    return semantic.enabled and semantic.default


def _search_form(
    config: Config, csrf: str, form: SearchForm, found: Found, *, collapsed: bool
) -> Markup:
    """The search bar, and the filters: open on an empty form, folded once results
    are shown (the results come right under it)."""

    def text(name: str, label: Msg, value: str | None, **attrs: object) -> Markup:
        return el(
            "label",
            el("span", label),
            el("input", type=attrs.pop("type", "text"), name=name, value=value or "", **attrs),
            class_="field",
        )

    def boxes(name: str, legend: Msg, options: list[tuple[str, str]], chosen: list[str]) -> Markup:
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
                    label,
                    class_="chip",
                )
                for option, label in options
            ),
            class_="chips",
        )

    def select(name: str, label: Msg, options: list[tuple[str, str]], chosen: str) -> Markup:
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

    def check(name: str, label: Msg, on: bool) -> Markup:
        return el(
            "label",
            el("input", type="checkbox", name=name, value="1", checked=on),
            " ",
            label,
            class_="check",
        )

    more = el(
        "details",
        el("summary", t("search.more_filters")),
        el(
            "div",
            text("ext", t("search.label.ext"), form.ext, placeholder="pdf"),
            text("since", t("search.label.since"), form.since, type="date"),
            text("until", t("search.label.until"), form.until, type="date"),
            text("period", t("search.label.period_field"), form.period, placeholder="2026-09"),
            text("issuer", t("search.label.issuer"), form.issuer),
            text("min_amount", t("search.label.min_amount"), form.min_amount, inputmode="decimal"),
            text("max_amount", t("search.label.max_amount"), form.max_amount, inputmode="decimal"),
            text(
                "currency",
                t("search.label.currency"),
                form.currency,
                placeholder="USD",
                maxlength=3,
            ),
            text("due_after", t("search.label.due_after"), form.due_after, type="date"),
            text("due_before", t("search.label.due_before"), form.due_before, type="date"),
            select(
                "by",
                t("search.label.by"),
                [("", t("search.by_currency_only")), *((g, group_label(g)) for g in GROUPS)],
                form.by or "",
            ),
            select(
                "limit",
                t("search.label.limit"),
                [(str(n), str(n)) for n in LIMITS],
                str(form.limit),
            ),
            class_="grid",
        ),
        check("duplicates", t("search.label.duplicates"), form.duplicates),
        check("semantic", t("search.label.semantic"), form.semantic)
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
                el("span", t("search.words"), class_="sr-only"),
                el(
                    "input",
                    type="search",
                    name="q",
                    value=form.q,
                    placeholder=t("search.placeholder"),
                    autofocus=True,
                ),
                class_="query",
            ),
            el("button", t("search.button"), type="submit"),
            class_="searchbar",
        ),
        el(
            "details",
            el("summary", _filters_summary(form)),
            boxes(
                "category",
                t("search.label.category"),
                [(c, c) for c in found.categories],
                form.categories,
            ),
            boxes("kind", t("search.label.kind"), [(k, kind_label(k)) for k in KINDS], form.kinds),
            more,
            class_="filters",
            open=not collapsed,
        ),
        el(
            "p",
            el(
                "button",
                t("search.export"),
                type="submit",
                formaction="/search/export",
                class_="quiet",
            ),
            class_="actions",
        ),
        method="post",
        action="/search",
        class_="search",
    )


def _filters_summary(form: SearchForm) -> str:
    used = len(form.categories) + len(form.kinds)
    used += sum(1 for flag in _more_filters(form) if flag)
    return t("search.filters") if not used else t("search.filters_used", count=used)


GROUP_LABELS = {
    "issuer": t("search.group.issuer"),
    "month": t("search.group.month"),
    "year": t("search.group.year"),
    "kind": t("search.group.kind"),
    "category": t("search.group.category"),
}
"""The ways to group totals (``store.GROUPS``) in words."""
GROUP_HEADINGS = {
    "issuer": t("search.heading.issuer"),
    "month": t("search.heading.month"),
    "year": t("search.heading.year"),
    "kind": t("search.heading.kind"),
    "category": t("search.heading.category"),
}
"""The same, as a column's heading."""


def group_label(group: str) -> str:
    return GROUP_LABELS.get(group, group)


def _more_filters(form: SearchForm) -> list[bool]:
    hidden: list[object] = [form.ext, form.since, form.until, form.period, form.issuer]
    hidden += [form.min_amount, form.max_amount, form.currency, form.due_after, form.due_before]
    return [v is not None for v in hidden] + [form.duplicates]


def _advanced(form: SearchForm) -> bool:
    """The more filters stay open when one of them is in use."""
    hidden = [form.ext, form.since, form.until, form.period, form.issuer, form.min_amount]
    hidden += [form.max_amount, form.currency, form.due_after, form.due_before, form.by]
    return any(v is not None for v in hidden) or form.duplicates or form.limit != DEFAULT_LIMIT


def _results(
    found: Found,
    form: SearchForm,
    config: Config,
    csrf: str,
    back: Sequence[tuple[str, str]] = (),
) -> Markup:
    """The count and the totals first, right under the form; then each match."""
    parts: list[Markup | None] = []
    if found.notice:
        parts.append(message(found.notice))
    if found.sensitive:
        parts.append(message(t("search.sensitive", count=found.sensitive)))
    count = len(found.hits)
    if not found.hits:
        if found.weak:
            parts.append(message(t("search.no_close")))
        elif config.database.exists():
            parts.append(message(t("search.no_matches")))
    elif found.total is not None and found.total > count:
        line = t("search.matches_of", shown=count, count=found.total)
        parts.append(el("h2", line, class_="count"))
    else:
        capped = found.total is None and count >= form.limit  # by meaning: the closest
        key = "search.matches_capped" if capped else "search.matches"
        parts.append(el("h2", t(key, count=count), class_="count"))
    if found.sums is not None and (found.hits or found.sums.totals):
        words = fts_query(form.q) is not None
        parts.append(_sums(found.sums, semantic=form.semantic, words=words))
    if found.hits:
        items = [_hit(hit, i, config, csrf, back) for hit, i in found.hits]
        parts.append(el("ol", *items, class_="hits", aria_label=t("search.matches_label")))
    return el("section", *parts, class_="results", aria_label=t("search.results_label"))


def _input_of(path: Path, config: Config) -> str:
    """The input folder ``path`` is in, by name (``Downloads``)."""
    for root in config.input_paths:
        if path.is_relative_to(root):
            return shown(root.name)
    return shown(path.parent.name)


def _hit(
    hit: SearchHit,
    file_id: int | None,
    config: Config,
    csrf: str,
    back: Sequence[tuple[str, str]] = (),
) -> Markup:
    """One match, as ``flib search`` lists it. A file that is not filed (put back by
    ``flib undo``, missing, failed) says where it is and offers no button: those
    are for files in the library."""
    path = Path(hit.path)
    status = hit.status
    filed = status in {s.value for s in OPENABLE}
    tags: list[Markup] = []
    if status == FileStatus.REVIEW.value:
        tags.append(el("span", t("common.in_review"), class_="badge"))
    elif filed and hit.category:
        tags.append(el("span", hit.category, class_="badge"))
    elif status == FileStatus.SKIPPED.value:
        put_back = t("search.not_filed", folder=_input_of(path, config))
        tags.append(el("span", put_back, class_="badge"))
    elif not filed:
        tags.append(el("span", FILE_STATES.get(status, status), class_="badge"))
    if not hit.exists:
        tags.append(el("span", t("common.missing"), class_="badge warn"))
    where = short(path.parent, config)
    details: list[Markup | None] = [
        el("div", el("strong", filename(path.name)), " ", *tags, class_="hit-head"),
        el("div", where + "/" if where != "." else _input_of(path, config) + "/", class_="where"),
    ]
    if hit.original_name != path.name:
        details.append(el("div", t("views.was", name=filename(hit.original_name)), class_="note"))
    if hit.duplicate_of is not None:
        original = el("bdi", short(Path(hit.duplicate_of), config), dir="ltr")
        details.append(el("div", t("search.duplicate_of", path=original), class_="note"))
    if (line := fields_line(hit)) is not None:
        details.append(el("div", line, class_="fields"))
    if hit.description:
        details.append(el("p", hit.description, class_="desc"))
    if hit.tags:
        details.append(
            el(
                "ul",
                *(el("li", tag) for tag in hit.tags),
                class_="tags",
                aria_label=t("search.tags_label"),
            )
        )
    if file_id is not None and hit.exists and filed:
        can_open = openable(path)
        details.append(file_actions(file_id, shown(path.name), csrf, can_open=can_open, back=back))
    return el("li", *details, class_="hit")


FILE_STATES = {
    "skipped": t("search.state.skipped"),
    "missing": t("search.state.missing"),
    "failed": t("search.state.failed"),
    "sensitive": t("search.state.sensitive"),
}
"""A file's status (``FileStatus``) as a badge says it."""


def _day_text(value: str) -> str:
    """A ``YYYY-MM-DD`` from the index as the page shows dates; anything else as it is."""
    try:
        return formats.day(date.fromisoformat(value))
    except ValueError:
        return value


def kind_label(code: str) -> str:
    """A kind of document (``models.Kind``) by its label in the page's language; the
    code stays the value of a form's field, the address and the CSV."""
    return t(KIND_IDS[code]) if code in KIND_IDS else code


KIND_IDS = {
    "bill": "kind.bill",
    "receipt": "kind.receipt",
    "invoice": "kind.invoice",
    "statement": "kind.statement",
    "contract": "kind.contract",
    "payslip": "kind.payslip",
    "tax": "kind.tax",
    "ticket": "kind.ticket",
    "policy": "kind.policy",
    "report": "kind.report",
    "form": "kind.form",
    "letter": "kind.letter",
    "manual": "kind.manual",
    "other": "kind.other",
}


def _group_value(by: str, group: str | None) -> str:
    if group is None:
        return t("search.no_group")
    return kind_label(group) if by == "kind" else group


def fields_line(hit: SearchHit) -> Markup | None:
    """``SearchHit.fields_line`` in the page's words and formats: kind, issuer, amount,
    due date, period or document date."""
    parts: list[str] = [p for p in (hit.kind and kind_label(hit.kind), hit.issuer) if p]
    if hit.amount_minor is not None and hit.currency is not None:
        parts.append(formats.amount(hit.amount_minor, hit.currency))
    if hit.due_date:
        parts.append(t("search.field.due", date=_day_text(hit.due_date)))
    if hit.period:
        parts.append(t("search.field.period", period=hit.period))
    elif hit.document_date:
        parts.append(t("search.field.dated", date=_day_text(hit.document_date)))
    if not parts:
        return None
    return join(*(join(" · " if i else None, part) for i, part in enumerate(parts)))


def _sums(sums: Sums, *, semantic: bool, words: bool = True) -> Markup:
    """``words``: the search had words (the totals add up every match by them); without,
    every match of the filters."""
    parts: list[Markup | None] = [el("h2", t("search.totals"))]
    if not words:
        note = t("search.totals_note_filters")
    else:
        note = t("search.totals_note_semantic") if semantic else t("search.totals_note")
    parts.append(el("p", note, class_="note"))
    if not sums.totals:
        parts.append(message(t("search.no_amounts")))
    if sums.groups:
        ordered = sorted(
            sums.groups, key=lambda t: (t.group is None, (t.group or "").casefold(), t.currency)
        )
        rows = [
            el(
                "tr",
                el("td", _group_value(str(sums.by), total.group)),
                el("td", formats.amount(total.total_minor, total.currency), class_="num"),
                el("td", total.files, class_="num"),
            )
            for total in ordered
        ]
        by = str(sums.by)
        parts.append(
            el(
                "table",
                el("caption", t("search.by_caption", group=group_label(by))),
                el(
                    "thead",
                    el(
                        "tr",
                        el("th", GROUP_HEADINGS.get(by, by.capitalize()), scope="col"),
                        el("th", t("views.total"), **_NUM),
                        el("th", t("views.files"), **_NUM),
                    ),
                ),
                el("tbody", *rows),
                class_="totals",
            )
        )
    if sums.totals:
        parts.append(totals_table(sums.totals, t("search.per_currency")))
    if sums.without_amount:
        parts.append(el("p", t("search.without_amount", count=sums.without_amount), class_="note"))
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
CUT_ROW = "search.csv_cut"
"""The message of the CSV's last row when it was cut, in the language of the request."""


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
        writer.writerow([t(CUT_ROW, count=EXPORT_LIMIT).text()])
    return HTTPStatus.OK, out.getvalue().encode("utf-8"), "text/csv; charset=utf-8"


def _plain_error(code: int, text: str) -> tuple[int, bytes, str]:
    return code, (text + "\n").encode("utf-8"), "text/plain; charset=utf-8"


# --- Due -------------------------------------------------------------------------------


DUE_PARAMS = frozenset({"within", "overdue", "all"})


def due_window(fields: dict[str, list[str]]) -> str:
    """The Due window a form came from (``within``, ``overdue``, ``all``), checked as
    the page's address is, for the address to go back to; ``BadRequest`` otherwise."""
    within = _choice(fields, "within", tuple(code for code, _ in WITHIN))
    overdue = _choice(fields, "overdue", ("1",))
    show_all = _choice(fields, "all", ("1",))
    if overdue and within is not None:
        raise BadRequest
    parts = [("overdue", "1")] if overdue else [("within", within)] if within else []
    parts += [("all", "1")] if show_all else []
    return "&".join(f"{k}={v}" for k, v in parts)


def due_page(config: Config, csrf: str, query: str, *, notice: Markup | None = None) -> Page:
    """``notice``: what Mark done or Open again just did (``server`` keeps it)."""
    params = query_params(query, DUE_PARAMS)
    within = _choice(params, "within", tuple(code for code, _ in WITHIN))
    overdue = _choice(params, "overdue", ("1",)) == "1"
    show_all = _choice(params, "all", ("1",)) == "1"
    if overdue and within is not None:
        raise BadRequest
    chosen = None if overdue else (within or "7d")
    extra = "&all=1" if show_all else ""
    links = [(text, f"/due?within={code}{extra}", code == chosen) for code, text in WITHIN]
    links.append((t("due.overdue_chip"), f"/due?overdue=1{extra}", overdue))
    window = "overdue=1" if overdue else f"within={chosen}"
    keep = [("overdue", "1")] if overdue else [("within", chosen or "7d")]
    keep += [("all", "1")] if show_all else []
    toggle = (
        el("a", t("due.hide_done"), href=f"/due?{window}")
        if show_all
        else el("a", t("due.show_all"), href=f"/due?{window}&all=1")
    )
    title = t("nav.due")
    parts: list[Markup | None] = [
        el("h1", title),
        el("p", t("due.lead"), class_="lead"),
        notice,
        chips(links, t("views.window")),
        el("p", toggle, class_="note"),
    ]
    query_ = due_mod.make_query(within=within, overdue=overdue, include_all=show_all)
    try:
        found = due_mod.find(config, query_)
    except due_mod.DueError as exc:
        unreadable_now = isinstance(exc.__cause__, sqlite3.Error)
        text = t("common.unreadable") if unreadable_now else core(english(exc))
        parts.append(message(text, error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout(title, "due", join(parts), csrf)
    today = formats.day(found.today)
    if found.start is None:
        span = t("due.span_overdue", today=today)
    else:
        span = t(
            "due.span",
            start=formats.day(found.start),
            count=due_mod.LOOKBACK_DAYS,
            end=formats.day(found.end),
            today=today,
        )
    parts.append(el("p", span, class_="note"))
    if not found.items:
        parts.append(message(t("due.nothing")))
    else:
        parts.append(_due_table(found, config, csrf, _pins(config, found), keep))
        if found.totals:
            parts.append(totals_table(found.totals, t("due.totals")))
        if found.without_amount:
            parts.append(
                el("p", t("due.without_amount", count=found.without_amount), class_="note")
            )
    if found.hidden:
        parts.append(el("p", t("due.hidden", count=found.hidden), class_="note"))
    return HTTPStatus.OK, layout(title, "due", join(parts), csrf)


def _pins(config: Config, found: due_mod.DueList) -> dict[int, str]:
    """Each listed document's state, for Mark done and Open again (``actions.file_pin``)."""
    from file_librarian.ui.actions import file_pin

    try:
        with read_index(config) as store:
            rows = store.files_by_ids([item.file_id for item in found.items])
    except Unreadable:
        return {}
    return {file_id: file_pin(row) for file_id, row in rows.items()}


def _due_actions(
    item: due_mod.DueItem, csrf: str, pin: str | None, keep: list[tuple[str, str]]
) -> Markup | None:
    """Open, Show in Finder, and Mark done (or Open again, for one marked done here):
    ``flib fix --done`` / ``--not-done``, journaled; Undo reverts it."""
    if not item.exists:
        return None
    name = shown(item.path.name)
    settle = None
    if pin is not None and item.done_by != "tag":
        label = t("due.reopen") if item.done else t("due.mark_done")
        named = (
            t("due.reopen_named", name=name) if item.done else t("due.mark_done_named", name=name)
        )
        settle = el(
            "form",
            csrf_field(csrf),
            el("input", type="hidden", name="id", value=item.file_id),
            el("input", type="hidden", name="pin", value=pin),
            el("input", type="hidden", name="done", value="0" if item.done else "1"),
            *(el("input", type="hidden", name=k, value=v) for k, v in keep),  # the window
            el("button", label, type="submit", class_="small quiet", aria_label=named),
            method="post",
            action="/due/settle",
            class_="inline",
        )
    back = (("back", "due"), *keep)  # a refusal is shown on this page, in this window
    tools = file_actions(item.file_id, name, csrf, can_open=openable(item.path), back=back)
    return join(tools, settle)


def _due_table(
    found: due_mod.DueList,
    config: Config,
    csrf: str,
    pins: dict[int, str],
    keep: list[tuple[str, str]],
) -> Markup:
    rows = []
    columns = {
        "due": t("due.col.due"),
        "when": t("due.col.when"),
        "amount": t("due.col.amount"),
        "issuer": t("due.col.issuer"),
        "kind": t("due.col.kind"),
        "file": t("due.col.file"),
    }
    for item in found.items:
        when = relative(item.days)
        state = None
        if item.done:
            done = t("due.done") if item.done_by == "fix" else t("due.done_tag")
            state = el("span", done, class_="badge")
        elif item.overdue:
            state = el("span", t("due.overdue"), class_="badge warn")
        if not item.exists:
            state = join(state, " ", el("span", t("common.missing"), class_="badge warn"))
        elif item.status == FileStatus.REVIEW.value:
            state = join(state, " ", el("span", t("common.in_review"), class_="badge"))
        amount = (
            formats.amount(item.amount_minor, item.currency)
            if item.amount_minor is not None and item.currency is not None
            else "-"
        )
        day = item.due_date.isoformat()
        rows.append(
            el(
                "tr",
                el(
                    "td",
                    el("time", formats.day(item.due_date), datetime=day),
                    class_="date",
                    data_label=columns["due"],
                ),
                el("td", when, " ", state, data_label=columns["when"]),
                el("td", amount, class_="num", data_label=columns["amount"]),
                el("td", item.issuer or "-", data_label=columns["issuer"]),
                el("td", kind_label(item.kind) if item.kind else "-", data_label=columns["kind"]),
                el(
                    "td",
                    filename(item.path.name),
                    el("div", short(item.path.parent, config) + "/", class_="where"),
                    class_="file",
                    data_label=columns["file"],
                ),
                el("td", _due_actions(item, csrf, pins.get(item.file_id), keep), class_="buttons"),
                class_="overdue" if item.overdue else None,
            )
        )
    head = [el("th", columns[h], scope="col") for h in ("due", "when")]
    head.append(el("th", columns["amount"], **_NUM))
    head += [el("th", columns[h], scope="col") for h in ("issuer", "kind", "file")]
    head.append(el("th", el("span", t("due.col.actions"), class_="sr-only"), scope="col"))
    return el(
        "table",
        el("thead", el("tr", *head)),
        el("tbody", *rows),
        class_="due",
    )


def relative(days: int) -> Msg:
    """``due.relative`` in the page's words: today, tomorrow, yesterday, in 3 days, 2 days
    ago."""
    if days == 0:
        return t("due.today")
    if days == 1:
        return t("due.tomorrow")
    if days == -1:
        return t("due.yesterday")
    return t("due.in_days", count=days) if days > 0 else t("due.days_ago", count=-days)


# --- Open and Show in Finder -----------------------------------------------------------

Opener = Callable[[list[str]], int]


def run_open(argv: list[str]) -> int:
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
    runner: Opener = run_open,
) -> tuple[int, str | None]:
    """Ask macOS to open (or show in Finder) the file with this index id.

    ``(204, None)`` when it was asked; otherwise a status and a message for the
    page, in general words."""
    values = fields.get("id", [])
    if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 18:
        return HTTPStatus.BAD_REQUEST, t("open.no_file")
    if not config.database.exists():
        return HTTPStatus.NOT_FOUND, t("open.not_indexed")
    try:
        with read_index(config) as store:
            row = store.file_by_id(int(values[0]))
    except LookupError:
        return HTTPStatus.NOT_FOUND, t("open.not_indexed")
    except Unreadable as exc:
        return HTTPStatus.SERVICE_UNAVAILABLE, unreadable(exc)
    if row.status not in OPENABLE:
        return HTTPStatus.FORBIDDEN, t("open.not_openable")
    real = real_location(row.current_path, config)
    if real is None:
        return HTTPStatus.FORBIDDEN, t("open.outside")
    try:
        os.lstat(real)
    except OSError:
        return HTTPStatus.NOT_FOUND, t("open.gone")
    if not reveal and not openable(real):
        return HTTPStatus.FORBIDDEN, t("open.not_a_document")
    # The path is one argument after "--": never an option, never parsed by a shell.
    argv = [program, "-R", "--", str(real)] if reveal else [program, "--", str(real)]
    try:
        code = runner(argv)
    except (OSError, subprocess.SubprocessError):
        code = -1
    if code != 0:
        return HTTPStatus.BAD_GATEWAY, t("open.failed")
    return HTTPStatus.NO_CONTENT, None


def problem_page(code: int, text: str, csrf: str | None) -> Page:
    """A refusal as a page with the menu; ``csrf`` ``None`` for a browser without the
    session (no End session button)."""
    title = problem_title(code)
    main = join(el("h1", title), message(text, error=True))
    return code, layout(title, "", main, csrf)


PROBLEM_TITLES = {
    400: t("problem.title.400"),
    403: t("problem.title.403"),
    404: t("problem.title.404"),
    502: t("problem.title.502"),
    503: t("problem.title.503"),
}
"""The statuses a page refuses with, by title: in English, the status's own phrase."""


def problem_title(code: int) -> Msg:
    return PROBLEM_TITLES.get(code) or t("problem.title.other", code=code)
