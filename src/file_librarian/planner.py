"""Stage 4: turn classifications into a plan (RF-08, RF-09, RF-10, RF-31, RF-32).

This is where the model's answer stops being trusted. For each item:

- sensitive files stay where they are (status ``sensitive``);
- a provider failure leaves the file in place (status ``failed``);
- a category outside the configuration, or confidence below
  ``confidence_threshold``, goes to the review folder with its original name;
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
APFS compares them. ``flib simulate`` stops after this stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from file_librarian.config import Config, folder_key
from file_librarian.models import Classification, Decision, FileContext, FileStatus, NameChoice
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


@dataclass(slots=True)
class Plan:
    items: list[PlannedItem] = field(default_factory=list)

    def moves(self) -> list[PlannedItem]:
        return [i for i in self.items if i.destination is not None]


class UnsafeDestinationError(RuntimeError):
    """A computed destination escaped the configured folders. Always a bug."""


class _Names:
    """Names taken per folder: on disk plus already planned, APFS-style."""

    def __init__(self) -> None:
        self._taken: dict[Path, set[str]] = {}

    def claim(self, folder: Path, name: str) -> str:
        taken = self._taken.setdefault(folder, self._on_disk(folder))
        for candidate in candidate_names(name):
            if folder_key(candidate) not in taken:
                taken.add(folder_key(candidate))
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


def plan(decisions: list[Decision], config: Config, *, today: date | None = None) -> Plan:
    """Plan every decision. ``today`` names files that carry no other date."""
    result = Plan()
    names = _Names()
    today = today or date.today()
    for decision in decisions:
        try:
            item = _plan_one(decision, config, names, today)
        except (UnsafeDestinationError, OSError) as exc:
            # One odd item must never stop the run: it stays where it is.
            item = PlannedItem(
                decision.context, FileStatus.FAILED, None, f"cannot plan: {exc}"[:300]
            )
        result.items.append(item)
    return result


def _plan_one(decision: Decision, config: Config, names: _Names, today: date) -> PlannedItem:
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
        return _to_review(ctx, answer, config, names, reason)
    if answer.confidence < limits.confidence_threshold:
        reason = f"confidence {answer.confidence:.2f} < {limits.confidence_threshold:.2f}"
        return _to_review(ctx, answer, config, names, reason)

    original = ctx.path.name
    new_name = None
    choice: NameChoice | None = None
    if (
        not ctx.is_unit
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
    ctx: FileContext, answer: Classification, config: Config, names: _Names, reason: str
) -> PlannedItem:
    destination = _destination(config, config.review_folder, ctx.path.name, names, ctx)
    return PlannedItem(
        ctx,
        FileStatus.REVIEW,
        destination,
        reason,
        answer,
        renamed=destination.name != ctx.path.name,
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
    final = folder / names.claim(folder, safe)
    if final.parent != folder or not final.is_relative_to(config.output_root):
        raise UnsafeDestinationError(f"{final} escapes {folder}")
    return final
