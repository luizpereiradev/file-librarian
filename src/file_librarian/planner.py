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

Every destination is ``output_root / <configured folder> / <safe name>``;
``_destination`` re-checks that before returning it. Names are unique against
what is already on disk and earlier items in the same plan, compared the way
APFS compares them; an item's own current name is free for it, so a file
``flib reprocess`` re-plans into the folder it is already in keeps its name.
``flib simulate`` stops after this stage.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from file_librarian.config import Config, folder_key
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
    original_subject,
    safe_component,
    split_name,
)


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


def plan(
    decisions: list[Decision],
    config: Config,
    *,
    today: date | None = None,
    keep_names: Collection[Path] = (),
) -> Plan:
    """Plan every decision. ``today`` names files that carry no other date.

    Items at a path in ``keep_names`` are never renamed (``flib reprocess``:
    a name the user chose).
    """
    result = Plan()
    names = _Names()
    today = today or date.today()
    for decision in decisions:
        try:
            item = _plan_one(decision, config, names, today, decision.context.path in keep_names)
        except (UnsafeDestinationError, OSError) as exc:
            # One odd item must never stop the run: it stays where it is.
            item = PlannedItem(
                decision.context, FileStatus.FAILED, None, f"cannot plan: {exc}"[:300]
            )
        result.items.append(item)
    return result


def _plan_one(
    decision: Decision, config: Config, names: _Names, today: date, keep_name: bool
) -> PlannedItem:
    ctx = decision.context
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
    destination = _destination(config, category.name, chosen, names, ctx)
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
    config: Config, folder_name: str, file_name: str, names: _Names, ctx: FileContext
) -> Path:
    allowed = {c.name for c in config.categories} | {config.review_folder}
    if folder_name not in allowed:
        raise UnsafeDestinationError(f"folder {folder_name!r} is not configured")
    safe = safe_component(file_name)
    if not safe.strip(" .\u00a0") or safe.startswith("."):
        # A name that sanitizes to nothing (" ", "..."): give it a neutral one.
        safe = f"unnamed-{ctx.sha256[:8]}{split_name(file_name)[1]}"
    folder = config.output_root / folder_name
    final = folder / names.claim(folder, safe, ctx.path)
    if final.parent != folder or not final.is_relative_to(config.output_root):
        raise UnsafeDestinationError(f"{final} escapes {folder}")
    return final
