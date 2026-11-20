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
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from file_librarian.classifier import (
    CACHE_DAYS,
    classify_all,
    rules_key,
    vision_key,
    wants_attachment,
)
from file_librarian.config import Config, folder_key, is_within
from file_librarian.context import build_context_safely
from file_librarian.db.store import FileRow, Store
from file_librarian.executor import Relocation, RunReport, relocate
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
from file_librarian.planner import plan
from file_librarian.privacy import scrub, scrub_name, scrub_tags
from file_librarian.protection import LOCKED_NOW
from file_librarian.providers.base import Provider, Usage
from file_librarian.providers.prompt import system_prompt, user_message
from file_librarian.reconcile import in_cloud_only
from file_librarian.scanner import Candidate
from file_librarian.search import SearchFilters

_REPROCESSABLE = {FileStatus.ORGANIZED, FileStatus.REVIEW}

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
    selection = Selection()
    for row in store.all_files():
        if not _wanted(row, config, filters, removed):
            continue
        fields = corrected.get(row.id, set())
        keep_category = "category" in fields and removed is None
        nested = _nested(row, config)
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


def _nested(row: FileRow, config: Config) -> bool:
    """Deeper than ``output_root/<folder>/``: the user organized it further by hand."""
    path = row.current_path
    return path.is_relative_to(config.output_root) and (
        len(path.relative_to(config.output_root).parts) > 2
    )


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


def _check(row: FileRow, config: Config) -> tuple[FileContext | None, str]:
    """The file's context, or why it cannot be reprocessed now."""
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
    seen_key = vision_key(text_key, config)
    cached = to_ask = 0
    cost = 0.0
    for target in targets:
        ctx = target.context
        if ctx.sensitive:
            continue  # never sent
        vision = wants_attachment(ctx, provider, config)
        key = seen_key if vision else text_key
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
    planned = plan(decisions, config, today=today, keep_names=keep_names)
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
