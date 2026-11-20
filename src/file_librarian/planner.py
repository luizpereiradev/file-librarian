"""Stage 4: turn classifications into a plan (RF-08, RF-09, RF-10, RF-31, RF-32).

This is where the model's answer stops being trusted. For each item:

- sensitive files stay where they are (status ``sensitive``);
- a provider failure leaves the file in place (status ``failed``);
- a category outside the configuration, or confidence below
  ``confidence_threshold``, goes to the review folder with its original name,
  and the item says which (``ReviewReason``) and what the model suggested;
- otherwise the file goes to its category folder, renamed only when the
  category allows it, confidence reaches ``rename_confidence_threshold`` and
  the suggestion yields a usable name. Files whose content the model could
  not read (video, installers, images with vision off) and folder units keep
  their name: a name invented from the old name alone is worse than the
  original. An image or scanned PDF the model actually saw (``vision``)
  counts as read content, so it can be renamed like a text document.
  When a rename is allowed, the model's ``name_choice`` picks the subject:
  ``original`` keeps the subject of the current name (a descriptive name the
  model judged better than its own proposal), ``proposed`` uses
  ``suggested_name``. Either way the naming rules (pattern, date, sanitising,
  length, extension) are applied here, never by the model.

Every destination is ``output_root / <configured folder> / <safe name>``, or,
for a category with a ``layout``, ``output_root / <category> / <sub-folders> /
<safe name>``; ``_destination`` re-checks that before returning it.

Layouts (#86): each segment of the category's ``layout`` becomes one folder.
``{year}`` and ``{month}`` (two digits) come from the document date the model
read, else the file's download date, else the processing date: the same date
the naming pattern uses, so a file's folder and its name agree. ``{issuer}``
is the organisation the model named; its folder is matched against the
sub-folders already there (and those planned earlier in the same plan) with
``naming.match_issuer``, so the same company always lands in the same folder,
spelled as it was the first time. A value that is unknown (no issuer) or
unusable (nothing left once made a folder name, or something that is not a
real folder already at that place) stops the layout there: the file goes
to the deepest folder that could be resolved, at worst the category's own.

Names are unique per final folder against what is already on disk and
earlier items in the same plan, compared the way APFS compares them; an
item's own current name is free for it, so a file
``flib reprocess`` re-plans into the folder it is already in keeps its name.
Duplicates (RF-21) come last, unclassified: each goes to
``output_root / duplicates_folder`` under its own name (``-N`` if taken), once
its original is filed. A copy of an item of this run follows that item: if
the original stays where it is (sensitive, failed, left for later), so does
the copy, and nothing is recorded about it.

``flib simulate`` stops after this stage.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import cast

from file_librarian.config import Category, Config, folder_key, layout_segments
from file_librarian.hashing import is_dataless
from file_librarian.models import (
    Classification,
    Decision,
    FileContext,
    FileStatus,
    NameChoice,
    ReviewReason,
)
from file_librarian.naming import (
    build_name,
    candidate_names,
    issuer_folder_name,
    match_issuer,
    original_subject,
    safe_component,
    split_name,
)
from file_librarian.scanner import Candidate, Duplicate


@dataclass(frozen=True, slots=True)
class PlannedItem:
    context: FileContext
    status: FileStatus
    destination: Path | None
    """``None`` means the item stays where it is."""
    reason: str
    classification: Classification | None = None
    renamed: bool = False
    name_choice: NameChoice | None = None
    """The subject the name was built from; ``None`` when no rename was considered."""
    review_reason: ReviewReason | None = None
    """Why it goes to review; ``None`` unless ``status`` is ``review``."""
    original: Path | None = None
    """For a duplicate, where its original is (or goes, in this run)."""

    @property
    def review_category(self) -> str | None:
        """The category the model suggested for an item going to review."""
        if self.status is not FileStatus.REVIEW or self.classification is None:
            return None
        return self.classification.category[:MAX_SUGGESTED]


@dataclass(slots=True)
class Plan:
    items: list[PlannedItem] = field(default_factory=list)

    def moves(self) -> list[PlannedItem]:
        return [i for i in self.items if i.destination is not None]


# How much of a suggested category outside the configuration is kept.
MAX_SUGGESTED = 100


class UnsafeDestinationError(RuntimeError):
    """A computed destination escaped the configured folders. Always a bug."""


class _Names:
    """Names taken per folder: on disk plus already planned, APFS-style."""

    def __init__(self) -> None:
        self._taken: dict[Path, set[str]] = {}

    def claim(self, folder: Path, name: str, own: Path) -> str:
        """A free name in ``folder``. ``own`` is where the item is now: its own
        name is free for it (``flib reprocess`` re-plans files already in place)."""
        taken = self._taken.setdefault(folder, self._on_disk(folder))
        mine = folder_key(own.name) if own.parent == folder else None
        for candidate in candidate_names(name):
            key = folder_key(candidate)
            if key not in taken or key == mine:
                taken.add(key)
                return candidate
        raise UnsafeDestinationError(f"no free name for {name} in {folder}")

    @staticmethod
    def _on_disk(folder: Path) -> set[str]:
        try:
            return {folder_key(p.name) for p in folder.iterdir()}
        except FileNotFoundError:
            return set()
        except NotADirectoryError as exc:
            raise UnsafeDestinationError(f"{folder} exists and is not a folder") from exc


class _Folders:
    """Sub-folders per parent folder: real folders on disk plus those already planned."""

    def __init__(self) -> None:
        self._known: dict[Path, list[str]] = {}

    def issuer(self, parent: Path, issuer: str) -> str | None:
        """The folder under ``parent`` for ``issuer``: one that already holds it, else a
        new one spelled like ``issuer``; ``None`` when no folder name can be made."""
        names = self._known.get(parent)
        if names is None:
            names = self._known[parent] = _real_folders(parent)
        found = match_issuer(issuer, names)
        if found is not None:
            return found
        name = issuer_folder_name(issuer)
        if name is not None:
            names.append(name)
        return name


def _real_folders(parent: Path) -> list[str]:
    """The visible real folders in ``parent`` (no symlinks, no files), by name."""
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return []
    return sorted(
        e.name for e in entries if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)
    )


def _usable_folder(path: Path) -> bool:
    """Nothing is at ``path`` yet, or a real folder is that is not only in iCloud
    (listing it to find a free name would download it)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not is_dataless(st)


def layout_parts(
    category: Category, folder: Path, when: date, issuer: str | None, folders: _Folders
) -> tuple[str, ...]:
    """The sub-folders ``category``'s layout gives a file, below its folder ``folder``.

    The layout stops at the first value that is unknown or unusable: the file
    goes to the deepest folder resolved (see the module docstring).
    """
    if category.layout is None:
        return ()
    parts: list[str] = []
    for segment in layout_segments(category.layout):
        name: str | None
        if segment == "{issuer}":
            name = folders.issuer(folder, issuer) if issuer else None
        else:
            name = segment.format(year=f"{when.year:04d}", month=f"{when.month:02d}")
        if name is None or not _usable_folder(folder / name):
            break
        parts.append(name)
        folder = folder / name
    return tuple(parts)


class Placement:
    """The names and issuer folders one plan has taken so far, on disk and planned.

    ``flib reprocess --apply-layout`` places files with it the way a run does.
    """

    def __init__(self) -> None:
        self.names = _Names()
        self.folders = _Folders()

    def in_category(
        self,
        config: Config,
        category: Category,
        file_name: str,
        ctx: FileContext,
        *,
        when: date,
        issuer: str | None,
    ) -> Path:
        """Where ``ctx`` goes in ``category``, its layout applied, under ``file_name``
        (or the next free ``-N``)."""
        folder = config.output_root / category.name
        sub = layout_parts(category, folder, when, issuer, self.folders)
        return _destination(config, category.name, file_name, self.names, ctx, sub)


def plan(
    decisions: list[Decision],
    config: Config,
    *,
    today: date | None = None,
    keep_names: Collection[Path] = (),
    duplicates: Collection[Duplicate] = (),
    filed_on: Mapping[Path, date] | None = None,
) -> Plan:
    """Plan every decision, then every duplicate. ``today`` names files that carry
    no other date.

    Items at a path in ``keep_names`` are never renamed (``flib reprocess``:
    a name the user chose). ``filed_on`` gives files already in the library
    the day they were filed, used instead of ``today`` by a layout's
    ``{year}`` and ``{month}``, so reprocessing never moves a file to this
    year's folder just because it carries no date.
    """
    result = Plan()
    placement = Placement()
    names = placement.names
    today = today or date.today()
    filed_on = filed_on or {}
    for decision in decisions:
        path = decision.context.path
        try:
            item = _plan_one(
                decision, config, placement, today, path in keep_names, filed_on.get(path, today)
            )
        except (UnsafeDestinationError, OSError) as exc:
            # One odd item must never stop the run: it stays where it is.
            item = PlannedItem(
                decision.context, FileStatus.FAILED, None, f"cannot plan: {exc}"[:300]
            )
        result.items.append(item)
    going = {item.context.path: item for item in result.items}
    for duplicate in duplicates:
        ctx = duplicate_context(duplicate.candidate)
        try:
            item = _plan_duplicate(ctx, duplicate, going, config, names)
        except (UnsafeDestinationError, OSError) as exc:
            # It stays, and nothing is recorded: its content's row is the original's.
            reason = f"cannot plan: {exc}"[:300]
            item = PlannedItem(ctx, FileStatus.SKIPPED, None, reason, original=duplicate.original)
        result.items.append(item)
    return result


def duplicate_context(candidate: Candidate) -> FileContext:
    """What a duplicate needs to be moved: nothing is read from it besides the scan."""
    return FileContext(
        path=candidate.path,
        sha256=candidate.sha256,
        size_bytes=candidate.size_bytes,
        extension=candidate.path.suffix.lower(),
        mtime_ns=candidate.mtime_ns,
        inode=candidate.inode,
        is_unit=candidate.is_unit,
    )


def _plan_duplicate(
    ctx: FileContext,
    duplicate: Duplicate,
    going: dict[Path, PlannedItem],
    config: Config,
    names: _Names,
) -> PlannedItem:
    """A duplicate goes to the duplicates folder once its original is filed (RF-21).

    A copy of an item of this run follows it: if the original stays where it
    is (sensitive, failed, left for a later run), so does the copy.
    """
    original = duplicate.original
    if not duplicate.in_library:
        item = going.get(duplicate.original)
        if item is None or item.status not in _FILED:
            why = "is left for a later run" if item is None else f"stays ({item.status.value})"
            reason = f"same content as another item of this run, which {why}"
            return PlannedItem(ctx, FileStatus.SKIPPED, None, reason, original=duplicate.original)
        # Organized and review items always have a destination.
        original = cast(Path, item.destination)
    destination = _destination(config, config.duplicates_folder, ctx.path.name, names, ctx)
    return PlannedItem(
        ctx,
        FileStatus.DUPLICATE,
        destination,
        "duplicate",
        renamed=destination.name != ctx.path.name,
        original=original,
    )


_FILED = frozenset({FileStatus.ORGANIZED, FileStatus.REVIEW})


def _plan_one(
    decision: Decision,
    config: Config,
    placement: Placement,
    today: date,
    keep_name: bool,
    filed: date,
) -> PlannedItem:
    ctx = decision.context
    names = placement.names
    if ctx.sensitive:
        reason = ctx.metadata.sensitive or "sensitive content"
        return PlannedItem(ctx, FileStatus.SENSITIVE, None, f"sensitive: {reason}")
    answer = decision.classification
    if answer is None:
        return PlannedItem(ctx, FileStatus.FAILED, None, decision.error or "not classified")

    limits = config.limits
    category = config.category(answer.category)
    if category is None:
        reason = f"model chose unknown category {answer.category[:40]!r}"
        return _to_review(ctx, answer, config, names, reason, ReviewReason.UNKNOWN_CATEGORY)
    if answer.confidence < limits.confidence_threshold:
        reason = f"confidence {answer.confidence:.2f} < {limits.confidence_threshold:.2f}"
        return _to_review(ctx, answer, config, names, reason, ReviewReason.LOW_CONFIDENCE)

    original = ctx.path.name
    new_name = None
    choice: NameChoice | None = None
    if (
        not keep_name
        and not ctx.is_unit
        # a new name needs evidence from the content (text, or pixels the model saw)
        and (ctx.text or ctx.vision_used)
        and category.rename
        and answer.confidence >= config.naming.rename_confidence_threshold
    ):
        when = answer.document_date or ctx.downloaded or today
        choice = answer.name_choice
        if choice == "original":
            new_name = build_name(original, original_subject(original), when, config.naming)
            if new_name is None:  # nothing usable left of the original: take the proposal
                choice = "proposed"
        if choice == "proposed":
            new_name = build_name(original, answer.suggested_name, when, config.naming)
    chosen = new_name or original
    destination = placement.in_category(
        config,
        category,
        chosen,
        ctx,
        when=answer.document_date or ctx.downloaded or filed,
        issuer=answer.issuer,
    )
    reason = f"category {category.name}"
    if choice == "original":
        reason += ", original name kept"
    return PlannedItem(
        ctx,
        FileStatus.ORGANIZED,
        destination,
        reason,
        answer,
        renamed=destination.name != original,
        name_choice=choice,
    )


def _to_review(
    ctx: FileContext,
    answer: Classification,
    config: Config,
    names: _Names,
    reason: str,
    code: ReviewReason,
) -> PlannedItem:
    destination = _destination(config, config.review_folder, ctx.path.name, names, ctx)
    return PlannedItem(
        ctx,
        FileStatus.REVIEW,
        destination,
        reason,
        answer,
        renamed=destination.name != ctx.path.name,
        review_reason=code,
    )


def _destination(
    config: Config,
    folder_name: str,
    file_name: str,
    names: _Names,
    ctx: FileContext,
    sub: tuple[str, ...] = (),
) -> Path:
    """``output_root / folder_name / *sub / <free safe name>``, checked.

    ``sub``: a category layout's sub-folders, each a single plain folder name.
    """
    allowed = {c.name for c in config.categories} | {config.review_folder, config.duplicates_folder}
    if folder_name not in allowed:
        raise UnsafeDestinationError(f"folder {folder_name!r} is not configured")
    if sub and config.category(folder_name) is None:
        raise UnsafeDestinationError(f"folder {folder_name!r} has no layout")
    safe = safe_component(file_name)
    if not safe.strip(" .\u00a0") or safe.startswith("."):
        # A name that sanitizes to nothing (" ", "..."): give it a neutral one.
        safe = f"unnamed-{ctx.sha256[:8]}{split_name(file_name)[1]}"
    top = config.output_root / folder_name
    folder = top
    for part in sub:
        if not part or part in {".", ".."} or safe_component(part) != part:
            raise UnsafeDestinationError(f"sub-folder {part!r} is not a plain folder name")
        folder = folder / part
    final = folder / names.claim(folder, safe, ctx.path)
    if (
        final.parent != folder
        or len(final.relative_to(top).parts) != len(sub) + 1
        or not final.is_relative_to(config.output_root)
    ):
        raise UnsafeDestinationError(f"{final} escapes {folder}")
    return final
