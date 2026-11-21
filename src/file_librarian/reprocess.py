"""File the library again after the rules change: ``flib reprocess`` (RF-37).

Categories, hints, naming rules or the model change; the files already
organized were filed under the old ones. Reprocessing asks the provider
again about files in the library (``organized`` or in review) and moves the
ones whose answer now differs, in four steps the command line runs in order:

1. ``select`` picks the files: index rows inside ``output_root``, narrowed by
   the search filters, each still at its recorded path with the same content
   (a file only in iCloud is not read: that would download it). Sensitive
   files are never selected: they were never sent and are not sent now.
2. ``estimate`` counts what the classification cache already answers and
   prices the rest, before anything is sent.
3. ``plan_reprocess`` classifies (the cost cap and the cache apply as in a
   run) and plans with the normal planner. Names are kept unless renaming
   was asked for. A file the provider is now unsure about stays in its
   category; one that cannot be classified stays where it is. A file that
   goes to (or stays in) review gets the planner's review reason. A file the
   user put in a sub-folder of its category stays there unless its category
   changes (then it goes to the new category's folder, its name kept). What
   the user corrected (``flib fix``, or a move by hand that ``flib
   reconcile`` recorded) is kept: a corrected category is not changed, a
   corrected name is not renamed, and a file with both is not even sent;
   ``override_corrections`` lifts that. A ``never_send`` pattern or a
   sensitive name matching the file's original name counts as well as one
   matching its current name.
4. ``apply`` moves through the executor (``executor.relocate``) as one run
   with trigger ``reprocess``, journaled, recovered after a crash and undone
   as a whole by ``flib undo``; Finder tags follow the category. The plan is
   made and confirmed before the instance lock is taken, so the executor
   checks every item again (index row and file) before it changes anything.

A category renamed or removed in the configuration while files still use it
(an *orphan* category, see ``orphan_categories``) is never guessed at: plain
reprocessing refuses until an explicit migration ran. ``plan_rename`` moves
its files to the new name's folder, and their corrections with them;
``--remove-category`` reclassifies its files among the remaining categories
(``plan_reprocess`` on ``select(..., removed=...)``) or sends them all to
review (``plan_to_review``). The index side of a rename (rows that do not
move, corrections, and for a rename that only changes case the Finder tags
flib set) is journaled with the run (``index_changes``), so ``flib undo``
reverts it too. The emptied folder is left in place; undo makes it again if
it was deleted.

Only files inside ``output_root`` are touched, and only to destinations the
planner (or the migration) builds inside it.

``--apply-layout`` (#86) files organized files into their category's
``layout`` without asking the provider: ``select_layout`` takes what the
layout needs from the index (the stored document date and issuer, else the
newest cached answer's), ``plan_layout`` places each file with the
planner's ``Placement`` (issuer folders matched the same way, names kept,
``-N`` on a collision) and ``apply`` moves them as one ``reprocess`` run
that ``flib undo`` reverts. Files whose layout needs an issuer the index
does not have stay, unless the provider is asked for it
(``--ask-issuer``); then only the issuer and document date of the answer
are kept, journaled with the run. Files the user placed in a sub-folder by
hand (``placed_by_hand``), review, duplicates and sensitive files are never
touched. A category without a layout gets the files flib had put in its
sub-folders back to its top level.

``--fill-fields`` (#88) asks the provider only about library files the index
has no structured fields for (``select_fields``: no ``kind``), after the cost
question, and keeps only those fields of each answer (``plan_fields``): kind,
issuer, amount and currency, document and due dates, period, each written
only where the index has none. Category, name, description and place stay;
nothing moves. The values are journaled with one ``reprocess`` run that
``flib undo`` reverts.

``--scrub-descriptions`` is different: no provider call and no move. It runs
``privacy.scrub`` over what the index already stores (descriptions, tags and
name-choice reasons written before #82), as one ``reprocess`` run whose index
changes ``flib undo`` reverts (``plan_scrub``, ``apply_scrub``). Full-text
search follows the rows; the embeddings of the files it changed are removed,
to be made again from the scrubbed text. Cached answers and the records kept
with journaled moves are scrubbed in place too.
"""

from __future__ import annotations

import json
import os
import re
import string
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from file_librarian.classifier import (
    CACHE_DAYS,
    answer_key,
    classify_all,
    rules_key,
    wants_attachment,
)
from file_librarian.config import Config, folder_key, is_within, layout_segments
from file_librarian.context import build_context_safely
from file_librarian.db.store import FIELD_COLUMNS, FileRow, Store
from file_librarian.executor import Relocation, RunReport, answer_fields, relocate
from file_librarian.finder_tags import tag_moved, untag_restored
from file_librarian.hashing import check_content, unit_manifest
from file_librarian.models import (
    Decision,
    FileContext,
    FileStatus,
    ReviewReason,
    Rules,
    RunMode,
    RunTrigger,
)
from file_librarian.pipeline import rules_for
from file_librarian.planner import Placement, UnsafeDestinationError, plan, same_folder
from file_librarian.privacy import scrub, scrub_name, scrub_tags
from file_librarian.protection import LOCKED_NOW
from file_librarian.providers.base import Provider, Usage
from file_librarian.providers.prompt import clean_issuer, system_prompt, user_message
from file_librarian.reconcile import in_cloud_only
from file_librarian.scanner import Candidate
from file_librarian.search import SearchFilters

_REPROCESSABLE = {FileStatus.ORGANIZED, FileStatus.REVIEW}
# What a reprocess may write where the index has nothing yet (``ReprocessPlan.learned``).
_LEARNABLE = frozenset({*FIELD_COLUMNS, "layout_date"})

# A rough price of one request: about four characters per token, and the
# answer with its thinking. An image costs about (pixels / 750) tokens at its
# largest size (``vision.max_image_px`` on both sides, the worst case), and a
# scanned PDF that many per page sent.
CHARS_PER_TOKEN = 4
ANSWER_TOKENS = 600
PIXELS_PER_TOKEN = 750


class ReprocessError(ValueError):
    """Reprocessing cannot start as asked; nothing was changed. The message says why."""


@dataclass(frozen=True, slots=True)
class Target:
    """A file selected for reprocessing, as it is now."""

    row: FileRow
    context: FileContext
    keep_category: bool = False
    """The user corrected its category: it stays."""
    keep_name: bool = False
    """The user corrected its name, or renaming was not asked for: it is not renamed."""
    nested: bool = False
    """The user put it in a sub-folder of its category folder: it stays unless the
    category changes."""


@dataclass(slots=True)
class Selection:
    targets: list[Target] = field(default_factory=list)
    left: list[tuple[Path, str]] = field(default_factory=list)
    """Files of the library left out, and why."""


@dataclass(frozen=True, slots=True)
class Estimate:
    files: int
    cached: int
    """Answered by the classification cache, at no cost."""
    to_ask: int
    cost_usd: float
    """A rough upper estimate for the ``to_ask`` files."""


@dataclass(frozen=True, slots=True)
class Change:
    """One file and what reprocessing does with it."""

    row: FileRow
    relocation: Relocation

    @property
    def moves(self) -> bool:
        destination = self.relocation.destination
        return destination is not None and destination != self.relocation.context.path


@dataclass(slots=True)
class ReprocessPlan:
    changes: list[Change] = field(default_factory=list)
    """Files that move, or stay with a fresh description."""
    kept: list[tuple[Path, str]] = field(default_factory=list)
    """Files that stay as they are, and why."""
    deferred: int = 0
    """Not sent because the cost cap was reached; the next reprocess goes on with them."""
    cost_usd: float = 0.0
    cost_known: bool = True
    """False when the provider's prices are unknown; the run then records no cost."""
    cached: int = 0
    rename: tuple[str, str] | None = None
    """For ``--rename-category``: the old and new names, applied to the index with the moves."""
    same_folder: bool = False
    """The rename only changes the case: the same folder, so only the index changes."""
    learned: dict[int, dict[str, str]] = field(default_factory=dict)
    """File id -> ``files`` column (``document_date``, ``issuer``, ``layout_date``) ->
    value, for values the index did not have yet: from the cache, the provider
    (``--ask-issuer``) or the layout date resolved once. Written with the run
    (journaled, only where the index still has none), undone with it."""
    in_place: int = 0
    """For ``--apply-layout``: files already where their layout puts them."""
    respelled: dict[int, Path] = field(default_factory=dict)
    """For ``--apply-layout``: files in place whose folder the disk now spells
    otherwise (renamed only in case by the user); the index takes that spelling."""

    def moves(self) -> list[Change]:
        return [c for c in self.changes if c.moves]


@dataclass(frozen=True, slots=True)
class Applied:
    report: RunReport
    index_changes: int = 0
    """Index changes journaled with the run: a category rename, and new descriptions
    and tags of files the run did not move."""

    @property
    def undoable(self) -> bool:
        """The run moved something or changed the index, so `flib undo` has work."""
        return self.report.moved > 0 or self.index_changes > 0


# --- orphan categories --------------------------------------------------------------


def orphan_categories(config: Config, store: Store) -> list[str]:
    """Categories organized files still have that the configuration no longer lists."""
    configured = {c.name for c in config.categories}
    return [name for name in store.organized_categories() if name not in configured]


def orphan_message(orphans: list[str]) -> str:
    """What to tell the user about categories files still use but the configuration lost."""
    names = ", ".join(repr(name) for name in orphans)
    first = orphans[0]
    return (
        f"organized files still use categories missing from the configuration: {names}. "
        f"If you renamed one, run `flib reprocess --rename-category {_arg(first)} NEW`; "
        f"if you removed it, run `flib reprocess --remove-category {_arg(first)}`"
    )


def _arg(name: str) -> str:
    return f'"{name}"' if not name.replace("-", "").replace("_", "").isalnum() else name


def _orphan(name: str, config: Config, store: Store) -> str:
    """``name`` as an orphan category of the index (any case); refuses anything else."""
    if config.category(name) is not None:
        raise ReprocessError(
            f"{name!r} is still a category in the configuration; "
            "rename or remove it there first, then run this again"
        )
    found = [o for o in orphan_categories(config, store) if o.casefold() == name.casefold()]
    if not found:
        raise ReprocessError(f"no organized file has the category {name!r}; nothing to migrate")
    return found[0]


# --- 1. selecting -------------------------------------------------------------------


def select(
    config: Config,
    store: Store,
    *,
    filters: SearchFilters | None = None,
    removed: str | None = None,
    rename: bool = False,
    override_corrections: bool = False,
) -> Selection:
    """The library files to reprocess, checked to be where and what the index says.

    ``removed``: only the organized files of that category, which the
    configuration no longer has (``--remove-category``). Otherwise every
    file of the library matching ``filters``; a category the configuration
    lost must be migrated first. Names are kept unless ``rename``.
    """
    if removed is not None:
        removed = _orphan(removed, config, store)
    else:
        orphans = orphan_categories(config, store)
        if orphans:
            raise ReprocessError(orphan_message(orphans))
    corrected = {} if override_corrections else store.corrected_fields()
    moves = store.done_moves()
    selection = Selection()
    for row in store.all_files():
        if not _wanted(row, config, filters, removed):
            continue
        if removed is None and filed_by_rule(row):
            selection.left.append((row.current_path, RULE_FILED))
            continue
        fields = corrected.get(row.id, set())
        keep_category = "category" in fields and removed is None
        nested = placed_by_hand(row, moves.get(row.id, []), config)
        keep_name = not rename or nested or "name" in fields
        if keep_category and keep_name:
            selection.left.append((row.current_path, "your correction is kept"))
            continue
        context, problem = _check(row, config)
        if context is None:
            selection.left.append((row.current_path, problem))
            continue
        selection.targets.append(Target(row, context, keep_category, keep_name, nested))
    return selection


RULE_FILED = "filed by its input's fixed category; no AI is asked about it"


def filed_by_rule(row: FileRow) -> bool:
    """``row`` was filed by its input's fixed category, with no AI (#89): the index
    marks it so (``files.filed_by_rule``). Reprocessing never asks the AI about it."""
    return row.filed_by_rule and row.status is FileStatus.ORGANIZED


def own_layout(row: FileRow, config: Config) -> str | None:
    """For a file filed by rule, the layout its input gives files (``None``: the
    category's). ``--apply-layout`` leaves such a file alone."""
    if not filed_by_rule(row) or row.source_input is None:
        return None
    return config.input_rules(row.source_input).layout


def _deep(path: Path, config: Config) -> bool:
    """Deeper than ``output_root/<folder>/``."""
    return path.is_relative_to(config.output_root) and (
        len(path.relative_to(config.output_root).parts) > 2
    )


def placed_by_hand(row: FileRow, moves: list[tuple[Path, Path]], config: Config) -> bool:
    """The user chose where the file is, not flib.

    ``moves``: the file's moves still in effect, newest first (``Store.done_moves``).
    A file not where flib's last move put it was moved by hand (``flib
    reconcile`` records that without a move of flib's), at any depth: out of
    a layout folder to the category's top, into a sub-folder of its own. A
    file deeper than ``output_root/<folder>/`` was put there by flib (a
    category layout) only if flib's last move brought it exactly there, from
    a top-level place or from another place flib had put it; a move that
    kept a sub-folder the user had made (a category rename keeps it) breaks
    that chain too. Paths are compared the way APFS compares names.
    """
    current = row.current_path
    if moves and not _same_path(moves[0][1], current):
        return True
    if not _deep(current, config):
        return False
    for src, dst in moves:
        if not _same_path(dst, current):
            return True
        if not _deep(src, config):
            return False
        current = src
    return True  # deep, and no move of flib's brought it there


def _same_path(a: Path, b: Path) -> bool:
    return a == b or [folder_key(p) for p in a.parts] == [folder_key(p) for p in b.parts]


def _wanted(
    row: FileRow, config: Config, filters: SearchFilters | None, removed: str | None
) -> bool:
    if row.status not in _REPROCESSABLE:
        return False
    root = config.output_root
    if not row.current_path.is_relative_to(root) or row.current_path.parent == root:
        return False
    if not is_within(row.current_path, root):
        return False  # a symlink on the way out of the library
    if removed is not None:
        return row.status is FileStatus.ORGANIZED and row.category == removed
    if filters is None:
        return True
    if filters.categories and row.category not in filters.categories:
        return False
    if filters.extensions and _extension(row) not in filters.extensions:
        return False
    if filters.since is not None and row.processed_at < filters.since:
        return False
    return filters.until is None or row.processed_at < filters.until


def _extension(row: FileRow) -> str:
    return row.current_path.suffix.lower()


def _check(row: FileRow, config: Config, *, read: bool = True) -> tuple[FileContext | None, str]:
    """The file's context, or why it cannot be reprocessed now.

    ``read``: extract its text for the provider; otherwise only what a move needs
    (content checked all the same)."""
    path = row.current_path
    if in_cloud_only(path):
        return None, "only in iCloud; download it to reprocess it"
    try:
        st = os.lstat(path)
    except OSError:
        return None, "not at its recorded place; run `flib reconcile`"
    if row.is_unit and _partly_in_cloud(path):
        return None, "something inside is only in iCloud; download it to reprocess it"
    same = check_content(path, row.sha256)
    if same is None:
        return None, LOCKED_NOW
    if not same:
        return None, "changed or moved since it was indexed; run `flib reconcile`"
    candidate = Candidate(
        path=path,
        sha256=row.sha256,
        size_bytes=row.size_bytes if row.size_bytes is not None else st.st_size,
        mtime=st.st_mtime,
        is_unit=row.is_unit,
        mtime_ns=st.st_mtime_ns,
        created=float(getattr(st, "st_birthtime", st.st_mtime)),
        inode=st.st_ino,
    )
    if not read:
        context = FileContext(
            path=path,
            sha256=row.sha256,
            size_bytes=candidate.size_bytes,
            extension=path.suffix.lower(),
            mtime_ns=st.st_mtime_ns,
            inode=st.st_ino,
            downloaded=date.fromtimestamp(candidate.created) if candidate.created else None,
            is_unit=row.is_unit,
        )
        return context, ""
    return build_context_safely(candidate, config, also_named=(row.original_name,)), ""


def _partly_in_cloud(path: Path) -> bool:
    try:
        return unit_manifest(path).dataless
    except OSError:
        return False


# --- 2. estimating ------------------------------------------------------------------


def estimate(targets: list[Target], provider: Provider, config: Config, store: Store) -> Estimate:
    """How many answers the cache has, and roughly what asking for the rest costs."""
    rules = rules_for(config, store)
    text_key = rules_key(provider, rules, config)
    cached = to_ask = 0
    cost = 0.0
    for target in targets:
        ctx = target.context
        if ctx.sensitive:
            continue  # never sent
        vision = wants_attachment(ctx, provider, config)
        key = answer_key(ctx, text_key, config, vision=vision)
        if store.cached_answer(ctx.sha256, key, max_age_days=CACHE_DAYS) is not None:
            cached += 1
            continue
        to_ask += 1
        cost += provider.cost_usd(estimated_usage(ctx, rules, config, vision))
    return Estimate(len(targets), cached, to_ask, cost)


def estimated_usage(ctx: FileContext, rules: Rules, config: Config, vision: bool) -> Usage:
    """Roughly what one request for ``ctx`` bills, before it is sent."""
    chars = len(system_prompt(rules)) + len(user_message(ctx))
    tokens = chars // CHARS_PER_TOKEN + (attachment_tokens(ctx, config) if vision else 0)
    return Usage(input_tokens=tokens, output_tokens=ANSWER_TOKENS)


def attachment_tokens(ctx: FileContext, config: Config) -> int:
    """At most what an image, or a scanned PDF's first pages, adds to a request."""
    side = config.vision.max_image_px
    pages = config.vision.max_pdf_pages if ctx.visual == "pdf" else 1
    return side * side // PIXELS_PER_TOKEN * pages


# --- 3. planning --------------------------------------------------------------------


def plan_reprocess(
    targets: list[Target],
    provider: Provider,
    config: Config,
    store: Store,
    *,
    today: date | None = None,
) -> ReprocessPlan:
    """Classify the targets again and plan where each now belongs."""
    by_path = {t.context.path: t for t in targets}
    classified = classify_all(
        [t.context for t in targets], provider, rules_for(config, store), config, cache=store
    )
    decisions = [_respect_correction(d, by_path[d.context.path]) for d in classified.decisions]
    keep_names: Collection[Path] = {t.context.path for t in targets if t.keep_name}
    dates = {
        t.context.path: layout_date(
            t.row, config, nested=t.nested, first=_iso(t.row.document_date),
            born=t.context.downloaded,
        )
        for t in targets
    }  # fmt: skip
    layout_dates = {path: when for path, (when, _) in dates.items()}
    planned = plan(decisions, config, today=today, keep_names=keep_names, layout_dates=layout_dates)
    result = ReprocessPlan(
        deferred=len(classified.deferred),
        cost_usd=classified.cost_usd,
        cost_known=classified.cost_known,
        cached=classified.cached,
    )
    for item in planned.items:
        target = by_path[item.context.path]
        row = target.row
        path = item.context.path
        if item.status is FileStatus.SENSITIVE:
            result.kept.append((path, f"{item.reason}; left as it is"))
            continue
        if item.status is FileStatus.FAILED or item.classification is None:
            result.kept.append((path, f"{item.reason}; left as it is"))
            continue
        if (
            item.status is FileStatus.REVIEW
            and row.status is FileStatus.ORGANIZED
            and row.category is not None
            and config.category(row.category) is not None
        ):
            result.kept.append((path, f"stays in {row.category} ({item.reason})"))
            continue
        category = item.classification.category if item.status is FileStatus.ORGANIZED else None
        destination = item.destination
        if target.nested and (item.status, category) == (row.status, row.category):
            destination = None  # in the sub-folder the user chose
        if destination is not None and _same_place(destination, path):
            destination = None  # the same file, its folder spelled otherwise on disk
        relocation = Relocation(
            file_id=row.id,
            context=item.context,
            destination=destination,
            status=item.status,
            category=category,
            reason=_reason(item.reason, target),
            answer=item.classification,
            name_choice=item.name_choice,
            previous=(row.status, row.category),
            review_reason=item.review_reason,
            review_category=item.review_category,
        )
        result.changes.append(Change(row, relocation))
        when, resolved = dates[path]
        if resolved and item.status is FileStatus.ORGANIZED:
            result.learned.setdefault(row.id, {})["layout_date"] = when.isoformat()
    for ctx in classified.deferred:
        result.kept.append((ctx.path, "cost limit reached; run `flib reprocess` again"))
    return result


def _respect_correction(decision: Decision, target: Target) -> Decision:
    """A category the user chose stays, whatever the provider says now."""
    answer = decision.classification
    category = target.row.category
    if not target.keep_category or answer is None or category is None:
        return decision
    return Decision(decision.context, answer.model_copy(update={"category": category}))


def _reason(reason: str, target: Target) -> str:
    return f"{reason}, your category kept" if target.keep_category else reason


def plan_rename(config: Config, store: Store, old: str, new: str) -> ReprocessPlan:
    """``--rename-category OLD NEW``: the files of ``old`` go to ``new``'s folder, names kept.

    ``new`` must be a category of the configuration and ``old`` no longer one.
    """
    target = config.category(new) or next(
        (c for c in config.categories if c.name.casefold() == new.casefold()), None
    )
    if target is None:
        known = ", ".join(c.name for c in config.categories)
        raise ReprocessError(
            f"{new!r} is not a category in the configuration (configured: {known}); "
            "add it there first"
        )
    old = _orphan(old, config, store)
    result = ReprocessPlan(rename=(old, target.name))
    if folder_key(old) == folder_key(target.name):
        # Only the case differs: the same folder on macOS. Nothing moves; the
        # index takes the new spelling.
        result.same_folder = True
        return result
    old_folder = config.output_root / old
    for row in store.all_files():
        if not _wanted(row, config, None, old):
            continue
        context, problem = _check(row, config)
        if context is None:
            result.kept.append((row.current_path, problem))
            continue
        path = context.path
        inside = path.relative_to(old_folder) if path.is_relative_to(old_folder) else path.name
        relocation = Relocation(
            file_id=row.id,
            context=context,
            destination=config.output_root / target.name / inside,
            status=FileStatus.ORGANIZED,
            category=target.name,
            reason=f"category {old} renamed to {target.name}",
            previous=(row.status, row.category),
        )
        result.changes.append(Change(row, relocation))
    return result


def plan_to_review(targets: list[Target], config: Config) -> ReprocessPlan:
    """``--remove-category OLD --to-review``: every file of ``OLD`` to review, names kept."""
    result = ReprocessPlan()
    folder = config.output_root / config.review_folder
    for target in targets:
        relocation = Relocation(
            file_id=target.row.id,
            context=target.context,
            destination=folder / target.context.path.name,
            status=FileStatus.REVIEW,
            category=None,
            reason="its category was removed",
            previous=(target.row.status, target.row.category),
            review_reason=ReviewReason.CATEGORY_REMOVED,
        )
        result.changes.append(Change(target.row, relocation))
    return result


def layout_date(
    row: FileRow,
    config: Config,
    *,
    nested: bool = False,
    first: date | None = None,
    born: date | None = None,
) -> tuple[date, bool]:
    """The date ``row``'s layout ``{year}``/``{month}`` come from, and whether it is
    resolved now (not yet in the index, so to be written there).

    The index's ``layout_date`` wins, always: file metadata (a birth date a
    copy to another volume resets, a time zone) never moves a filed file.
    Without one it is resolved once: from the layout folder the file is in,
    if flib put it there (``nested`` False), else ``first`` (a document date),
    the download (birth) date ``born``, or the day it was processed.
    """
    stored = _iso(row.layout_date)
    if stored is not None:
        return stored, False
    chain = first or born or _processed_on(row)
    parsed = None if nested else _date_in_path(row.current_path, config, row.category)
    if parsed is None:
        return chain, True
    year, month = parsed
    year = year or chain.year
    if month is None:
        month = chain.month if chain.year == year else 1
    day = chain.day if (chain.year, chain.month) == (year, month) else 1
    return date(year, month, day), True


def _date_in_path(
    path: Path, config: Config, category: str | None
) -> tuple[int | None, int | None] | None:
    """(year, month) read from the layout folders ``path`` is in; ``None`` when its
    folders do not show its category layout's ``{year}`` or ``{month}``."""
    found = config.category(category or "")
    folder = config.output_root / (category or "")
    if found is None or found.layout is None or not path.is_relative_to(folder):
        return None
    inside = path.relative_to(folder).parts[:-1]
    year = month = None
    for segment, part in zip(layout_segments(found.layout), inside, strict=False):
        if "{year}" not in segment and "{month}" not in segment:
            continue
        pattern = "".join(
            re.escape(text)
            + {"year": r"(?P<year>\d{4})", "month": r"(?P<month>0[1-9]|1[0-2])"}.get(name or "", "")
            for text, name, _, _ in string.Formatter().parse(segment)
        )
        match = re.fullmatch(pattern, unicodedata.normalize("NFC", part))
        if match is None:
            return None
        groups = match.groupdict()
        year = int(groups["year"]) if groups.get("year") else year
        month = int(groups["month"]) if groups.get("month") else month
    if year is None and month is None:
        return None
    return year, month


def _processed_on(row: FileRow) -> date:
    """The day the index last filed ``row`` (``processed_at`` is an ISO time in UTC)."""
    try:
        return date.fromisoformat(row.processed_at[:10])
    except ValueError:
        return date.today()


# --- category layouts (#86) -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Placed:
    """A library file and what its category's layout files it by."""

    row: FileRow
    when: date
    issuer: str | None
    learned: dict[str, str] = field(default_factory=dict)
    """``files`` column -> value taken from outside the index (a cached answer, the
    provider, the layout date resolved once): written to the index with the run,
    so the next one finds them there."""


@dataclass(slots=True)
class LayoutSelection:
    """``--apply-layout``'s files, before any is checked or moved."""

    ready: list[_Placed] = field(default_factory=list)
    missing: list[Target] = field(default_factory=list)
    """Their layout needs an issuer the index does not have; only asking the provider
    (``--ask-issuer``) can place them. Contexts are read only when asking."""
    left: list[tuple[Path, str]] = field(default_factory=list)


def select_layout(
    config: Config,
    store: Store,
    *,
    categories: Collection[str] = (),
    ask: bool = False,
) -> LayoutSelection:
    """The organized files ``--apply-layout`` places, with what each is filed by.

    Only ``organized`` files in their category's folder: never review,
    duplicates or sensitive files, and never a file the user put in a
    sub-folder by hand (``placed_by_hand``). ``{year}`` and ``{month}`` come
    from the stored document date, else the newest cached answer's, else the
    file's download (birth) date, else the day it was filed; ``{issuer}``
    from the stored issuer, else the cached answer's. A value taken from the
    cache is written to the index with the run (``learned``), so the file
    stays put once the cache has expired. A file whose layout
    needs an issuer that is in neither is ``missing``; with ``ask`` its text
    is read so the provider can be asked. Nothing is sent here.
    """
    orphans = orphan_categories(config, store)
    if orphans:
        raise ReprocessError(orphan_message(orphans))
    wanted = set(categories)
    unknown = sorted(wanted - {c.name for c in config.categories})
    if unknown:
        known = ", ".join(c.name for c in config.categories)
        raise ReprocessError(f"unknown category {unknown[0]!r} (configured: {known})")
    moves = store.done_moves()
    selection = LayoutSelection()
    for row in store.all_files():
        category = config.category(row.category or "")
        if row.status is not FileStatus.ORGANIZED or category is None:
            continue
        if wanted and category.name not in wanted:
            continue
        path = row.current_path
        folder = config.output_root / category.name
        if not path.is_relative_to(folder) or not is_within(path, folder):
            selection.left.append((path, f"not in the {category.name} folder"))
            continue
        if placed_by_hand(row, moves.get(row.id, []), config):
            reason = "you moved or renamed it; it stays (`flib fix` hands it back)"
            selection.left.append((path, reason))
            continue
        if own_layout(row, config) is not None:
            selection.left.append((path, "filed under its input's own layout; it stays"))
            continue
        try:
            st = os.lstat(path)
        except OSError:
            selection.left.append((path, "not at its recorded place; run `flib reconcile`"))
            continue
        cached_date, cached_issuer = _cached_values(store, row.sha256)
        stored_date = _iso(row.document_date)
        born = getattr(st, "st_birthtime", None)
        when, resolved = layout_date(
            row,
            config,
            first=stored_date or cached_date,
            born=date.fromtimestamp(born) if born else None,
        )
        issuer = row.issuer or cached_issuer
        learned: dict[str, str] = {}
        if cached_date and not stored_date:
            learned["document_date"] = cached_date.isoformat()
        if cached_issuer and not row.issuer:
            learned["issuer"] = cached_issuer
        if resolved:
            learned["layout_date"] = when.isoformat()
        needs_issuer = category.layout is not None and "{issuer}" in layout_segments(
            category.layout
        )
        if needs_issuer and issuer is None:
            if not ask:
                selection.left.append(
                    (path, "issuer unknown; `--ask-issuer` asks the provider (it may cost)")
                )
                continue
            context, problem = _check(row, config)
            if context is None:
                selection.left.append((path, problem))
                continue
            selection.missing.append(Target(row, context))
            continue
        selection.ready.append(_Placed(row, when, issuer, learned))
    return selection


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _cached_values(store: Store, sha256: str) -> tuple[date | None, str | None]:
    """The document date and issuer of the newest answer cached for this content."""
    text = store.latest_cached_answer(sha256)
    if text is None:
        return None, None
    try:
        data = json.loads(text)
    except ValueError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    when = data.get("document_date")
    return _iso(when if isinstance(when, str) else None), clean_issuer(data.get("issuer"))


def plan_layout(
    selection: LayoutSelection,
    config: Config,
    store: Store,
    *,
    provider: Provider | None = None,
) -> ReprocessPlan:
    """Where each selected file goes under its category's layout; nothing moves here.

    With ``provider``, the ``missing`` files are classified (cache and cost cap
    as in a run) for their issuer and document date only: their category
    and name stay. Without it, or when the answer names no issuer, they stay.
    Names are kept; one taken in the new folder gets the next free ``-N``.
    The content of every file that moves is checked first.
    """
    result = ReprocessPlan()
    placed = list(selection.ready)
    if selection.missing and provider is not None:
        classified = classify_all(
            [t.context for t in selection.missing],
            provider,
            rules_for(config, store),
            config,
            cache=store,
        )
        result.cost_usd = classified.cost_usd
        result.cost_known = classified.cost_known
        result.cached = classified.cached
        result.deferred = len(classified.deferred)
        by_path = {t.context.path: t for t in selection.missing}
        for decision in classified.decisions:
            row = by_path[decision.context.path].row
            answer = decision.classification
            if answer is None or answer.issuer is None:
                why = decision.error or "the provider named no issuer"
                if decision.context.sensitive:
                    why = "sensitive: never sent"
                result.kept.append((row.current_path, f"{why}; it stays"[:300]))
                continue
            stored = _iso(row.document_date)
            when, resolved = layout_date(
                row,
                config,
                first=stored or answer.document_date,
                born=decision.context.downloaded,
            )
            values = {"issuer": answer.issuer}
            if not stored and answer.document_date:
                values["document_date"] = answer.document_date.isoformat()
            if resolved:
                values["layout_date"] = when.isoformat()
            placed.append(_Placed(row, when, answer.issuer, values))
        for ctx in classified.deferred:
            result.kept.append((ctx.path, "cost limit reached; run it again"))
    else:
        for target in selection.missing:
            result.kept.append((target.context.path, "issuer unknown; it stays"))
    placement = Placement(store.flib_folders())
    for item in sorted(placed, key=lambda p: p.row.id):
        _place_in_layout(item, config, placement, result)
    return result


def _place_in_layout(
    item: _Placed, config: Config, placement: Placement, result: ReprocessPlan
) -> None:
    row = item.row
    category = config.category(row.category or "")
    if category is None:  # selected as organized in a configured category
        return
    path = row.current_path
    # Where it goes is worked out first: most files are already in place and are
    # then neither read nor hashed.
    light = FileContext(path=path, sha256=row.sha256, size_bytes=0, extension="")
    try:
        destination, note = placement.in_category(
            config, category, path.name, light, when=item.when, issuer=item.issuer
        )
    except (UnsafeDestinationError, OSError) as exc:
        result.kept.append((path, f"cannot plan: {exc}"[:300]))
        return
    if _same_place(destination, path):
        result.in_place += 1
        _learn(result, row, item.learned)
        if destination != path:  # the same place, spelled otherwise: the disk's wins
            spelled = _spelled_on_disk(path, config.output_root)
            if spelled is not None and spelled != path:
                result.respelled[row.id] = spelled
        return
    context, problem = _check(row, config, read=False)
    if context is None:
        result.kept.append((path, problem))
        return
    where = destination.parent.relative_to(config.output_root)
    reason = f"layout of {category.name}: {where}" + (f"; {note}" if note else "")
    relocation = Relocation(
        file_id=row.id,
        context=context,
        destination=destination,
        status=FileStatus.ORGANIZED,
        category=category.name,
        reason=reason,
        previous=(row.status, row.category),
    )
    result.changes.append(Change(row, relocation))
    _learn(result, row, item.learned)


def _learn(result: ReprocessPlan, row: FileRow, values: dict[str, str]) -> None:
    if values:
        result.learned.setdefault(row.id, {}).update(values)


def _same_place(destination: Path, path: Path) -> bool:
    """``destination`` is where ``path`` already is: the same name in the same folder,
    however the folder is spelled (a folder renamed only in case is the same)."""
    return destination.name == path.name and same_folder(destination.parent, path.parent)


def _spelled_on_disk(path: Path, root: Path) -> Path | None:
    """``path`` as the disk spells it below ``root`` (case, Unicode form); ``None``
    when a part cannot be found or listed."""
    if not path.is_relative_to(root):
        return None
    current = root
    for part in path.relative_to(root).parts:
        try:
            names = [entry.name for entry in current.iterdir()]
        except OSError:
            return None
        wanted = folder_key(part)
        found = next((n for n in names if folder_key(n) == wanted), None)
        if found is None:
            return None
        current = current / unicodedata.normalize("NFC", found)
    return current


# --- structured fields (#88) ----------------------------------------------------------


def select_fields(
    config: Config, store: Store, *, filters: SearchFilters | None = None
) -> Selection:
    """The library files (organized or in review) the index has no structured fields for
    (no ``kind``: filed before #88, or the answer gave none), checked like ``select``.

    Sensitive files and duplicates are never selected. Nothing is sent here.
    """
    selection = Selection()
    for row in store.all_files():
        if not _wanted(row, config, filters, None) or row.kind is not None:
            continue
        context, problem = _check(row, config)
        if context is None:
            selection.left.append((row.current_path, problem))
            continue
        selection.targets.append(Target(row, context, keep_category=True, keep_name=True))
    return selection


def plan_fields(
    targets: list[Target], provider: Provider, config: Config, store: Store
) -> ReprocessPlan:
    """Ask the provider about the targets (cache and cost cap as in a run) and keep only
    the structured fields of each answer, for the columns the index has none in.

    Categories, names, descriptions and places stay as they are: nothing moves.
    The values go in ``learned``, written and journaled by ``apply``.
    """
    classified = classify_all(
        [t.context for t in targets], provider, rules_for(config, store), config, cache=store
    )
    result = ReprocessPlan(
        deferred=len(classified.deferred),
        cost_usd=classified.cost_usd,
        cost_known=classified.cost_known,
        cached=classified.cached,
    )
    by_path = {t.context.path: t.row for t in targets}
    for decision in classified.decisions:
        row = by_path[decision.context.path]
        answer = decision.classification
        if answer is None:
            why = "sensitive: never sent" if decision.context.sensitive else decision.error
            result.kept.append((row.current_path, f"{why or 'not classified'}; nothing filled"))
            continue
        given = answer_fields(answer)
        found: dict[str, object] = {
            "kind": given["kind"],
            "issuer": answer.issuer,
            "amount_minor": given["amount_minor"],
            "currency": given["currency"],
            "document_date": answer.document_date.isoformat() if answer.document_date else None,
            "due_date": given["due_date"],
            "period": given["period"],
        }
        if row.amount_minor is not None or row.currency is not None:
            found["amount_minor"] = found["currency"] = None  # an amount goes with its currency
        values = {
            column: str(value)
            for column, value in found.items()
            if value is not None and getattr(row, column) is None
        }
        if values:
            result.learned[row.id] = values
        else:
            result.kept.append((row.current_path, "the document states none of the fields"))
    for ctx in classified.deferred:
        result.kept.append((ctx.path, "cost limit reached; run it again"))
    return result


# --- 4. applying --------------------------------------------------------------------


def apply(
    planned: ReprocessPlan,
    config: Config,
    store: Store,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> Applied:
    """Carry out the plan as one ``reprocess`` run; ``flib undo`` reverts it.

    The caller holds the instance lock. The index side of a category rename
    is written and journaled in one transaction with the run's start, before
    any file moves, so undo finds it whatever happens next. What an earlier
    rename of the same category, cut off by a crash, left in the index is
    adopted by this run, so one undo reverts all of it.
    """
    renamed: list[int] = []
    with store.transaction():
        run_id = store.start_run(RunMode.RUN, RunTrigger.REPROCESS)
        if planned.rename is not None:
            old, new = planned.rename
            renamed = [r.id for r in store.all_files() if r.category == old]
            store.adopt_index_changes(run_id, old, new)
            store.rename_category(run_id, old, new, organized=planned.same_folder)
        for file_id, values in planned.learned.items():
            row = store.file_by_id(file_id)
            for column, value in values.items():
                if column in _LEARNABLE and getattr(row, column) is None:
                    store.change_index(run_id, f"files.{column}", file_id, None, value)
        for file_id, spelled in planned.respelled.items():
            row = store.file_by_id(file_id)
            if _same_path(row.current_path, spelled):
                store.set_file_location(file_id, spelled, row.status)
    if planned.rename is not None and planned.same_folder:
        _rename_tags(config, store, run_id, renamed, *planned.rename)
    report = relocate(
        [c.relocation for c in planned.changes],
        config,
        store,
        run_id=run_id,
        provider=provider,
        model=model,
        deferred=planned.deferred,
        cost_usd=planned.cost_usd if planned.cost_known else None,
    )
    return Applied(report, store.count_index_changes(run_id))


def _rename_tags(
    config: Config, store: Store, run_id: int, file_ids: list[int], old: str, new: str
) -> int:
    """A category renamed only in case: the Finder tag flib set follows, in the journal too.

    Files only in iCloud are left as they are (writing a tag could download them).
    """
    if not config.finder_tags.enabled:
        return 0
    changed = 0
    for file_id in file_ids:
        row = store.file_by_id(file_id)
        op = store.last_tag_operation(file_id)
        if row.status is not FileStatus.ORGANIZED or op is None or op.finder_tag is None:
            continue
        if op.finder_tag.casefold() != old.casefold() or op.finder_tag == new:
            continue
        path = row.current_path
        if not os.path.lexists(path) or in_cloud_only(path):
            continue
        if untag_restored(path, op.finder_tag) is not None:
            continue
        tag_moved(path, new)
        with store.transaction():
            store.change_index(run_id, "operations.finder_tag", op.id, op.finder_tag, new)
        changed += 1
    return changed


def empty_folder(config: Config, category: str) -> Path | None:
    """The folder of a migrated category, if nothing but hidden files is left in it."""
    folder = config.output_root / category
    try:
        entries = [e for e in folder.iterdir() if not e.name.startswith(".")]
    except OSError:
        return None
    return None if entries else folder


# --- scrubbing stored descriptions (#82) ---------------------------------------------


@dataclass(frozen=True, slots=True)
class Scrubbed:
    """A file whose stored answer holds something ``privacy.scrub`` replaces."""

    file_id: int
    path: Path
    values: dict[str, str | None]
    """``files`` column -> its new value, as the index stores it (tags as JSON)."""


@dataclass(frozen=True, slots=True)
class ScrubReport:
    run_id: int | None
    """The run that journaled the changes; ``None`` when no file changed."""
    files: int = 0
    values: int = 0
    embeddings: int = 0
    """Vectors removed, made again from the scrubbed text by the next refresh."""
    cached: int = 0
    """Cached answers scrubbed in place."""


def plan_scrub(store: Store) -> list[Scrubbed]:
    """The files whose description, tags or name-choice reason would change."""
    planned = []
    for row in store.all_files():
        values = _scrubbed_values(row)
        if values:
            planned.append(Scrubbed(row.id, row.current_path, values))
    return planned


def _scrubbed_values(row: FileRow) -> dict[str, str | None]:
    values: dict[str, str | None] = {}
    if row.description is not None and (new := scrub(row.description)) != row.description:
        values["description"] = new
    if row.tags and (tags := scrub_tags(row.tags)) != row.tags:
        values["tags"] = json.dumps(tags, ensure_ascii=False)
    reason = row.name_choice_reason
    if reason is not None and (new := scrub(reason)) != reason:
        values["name_choice_reason"] = new
    return values


def apply_scrub(store: Store) -> ScrubReport:
    """Scrub the index as one ``reprocess`` run that ``flib undo`` reverts.

    The caller holds the instance lock; the rows are read again here, so what
    changed since the plan is scrubbed as it is now.
    """
    with store.transaction():
        cached = _scrub_cache(store)
        _scrub_pending_records(store)
    rows = [r for r in store.all_files() if _scrubbed_values(r)]
    if not rows:
        return ScrubReport(None, cached=cached)
    values = 0
    with store.transaction():
        run_id = store.start_run(RunMode.RUN, RunTrigger.REPROCESS)
        for row in rows:
            # As the index stores them (tags as the JSON list upsert_file writes).
            stored = {
                "description": row.description,
                "tags": json.dumps(row.tags, ensure_ascii=False),
                "name_choice_reason": row.name_choice_reason,
            }
            for column, new in _scrubbed_values(row).items():
                store.change_index(run_id, f"files.{column}", row.id, stored[column], new)
                values += 1
        removed = store.delete_embeddings([r.id for r in rows])
        store.finish_run(run_id, seen=len(rows), moved=0, review=0, errors=0, cost_usd=0.0)
    return ScrubReport(run_id, len(rows), values, removed, cached)


def _scrub_cache(store: Store) -> int:
    changed = 0
    for sha256, key, answer in store.cached_answers():
        new = _scrub_json(answer, name="suggested_name")
        if new is not None:
            store.replace_cached_answer(sha256, key, new)
            changed += 1
    return changed


def _scrub_pending_records(store: Store) -> None:
    for op_id, record in store.pending_records():
        new = _scrub_json(record)
        if new is not None:
            store.replace_pending_record(op_id, new)


def _scrub_json(text: str, *, name: str | None = None) -> str | None:
    """A stored answer or record (JSON) with its free text scrubbed; ``None`` if unchanged
    or unreadable (an unreadable cached answer is never used anyway)."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    new = dict(data)
    for key in ("description", "name_choice_reason"):
        if isinstance(data.get(key), str):
            new[key] = scrub(data[key])
    if isinstance(data.get("tags"), list):
        new["tags"] = scrub_tags(str(t) for t in data["tags"])
    if name is not None and isinstance(data.get(name), str):
        new[name] = scrub_name(data[name]) or "untitled"
    return None if new == data else json.dumps(new, ensure_ascii=False)
