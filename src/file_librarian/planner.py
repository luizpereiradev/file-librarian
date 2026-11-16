"""Stage 4: turn classifications into a plan (RF-08, RF-09, RF-10, RF-31, RF-32).

This is where the model's answer stops being trusted. For each item:

- sensitive files stay where they are (status ``sensitive``);
- a provider failure leaves the file in place (status ``failed``);
- a category outside the configuration, or confidence below
  ``confidence_threshold``, goes to the review folder with its original name;
- otherwise the file goes to its category folder, renamed only when the
  category allows it, confidence reaches ``rename_confidence_threshold`` and
  the suggestion yields a usable name. Folder units keep their name.

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
from file_librarian.models import Classification, FileContext, FileStatus
from file_librarian.naming import build_name, safe_component, with_counter

MAX_COUNTER = 999


@dataclass(frozen=True, slots=True)
class Decision:
    """The classifier's outcome for one item."""

    context: FileContext
    classification: Classification | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedItem:
    context: FileContext
    status: FileStatus
    destination: Path | None
    """``None`` means the item stays where it is."""
    reason: str
    classification: Classification | None = None
    renamed: bool = False


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
        candidate = name
        n = 2
        while folder_key(candidate) in taken:
            if n > MAX_COUNTER:
                raise UnsafeDestinationError(f"no free name for {name} in {folder}")
            candidate = with_counter(name, n)
            n += 1
        taken.add(folder_key(candidate))
        return candidate

    @staticmethod
    def _on_disk(folder: Path) -> set[str]:
        try:
            return {folder_key(p.name) for p in folder.iterdir()}
        except FileNotFoundError:
            return set()


def plan(decisions: list[Decision], config: Config) -> Plan:
    result = Plan()
    names = _Names()
    for decision in decisions:
        result.items.append(_plan_one(decision, config, names))
    return result


def _plan_one(decision: Decision, config: Config, names: _Names) -> PlannedItem:
    ctx = decision.context
    if ctx.sensitive:
        reason = ctx.metadata.get("sensitive", "sensitive content")
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
    if (
        not ctx.is_unit
        and category.rename
        and answer.confidence >= config.naming.rename_confidence_threshold
    ):
        when = answer.document_date or ctx.downloaded or date.today()
        new_name = build_name(original, answer.suggested_name, when, config.naming)
    chosen = new_name or original
    destination = _destination(config, category.name, chosen, names)
    return PlannedItem(
        ctx,
        FileStatus.ORGANIZED,
        destination,
        f"category {category.name}",
        answer,
        renamed=destination.name != original,
    )


def _to_review(
    ctx: FileContext, answer: Classification, config: Config, names: _Names, reason: str
) -> PlannedItem:
    destination = _destination(config, config.review_folder, ctx.path.name, names)
    return PlannedItem(
        ctx,
        FileStatus.REVIEW,
        destination,
        reason,
        answer,
        renamed=destination.name != ctx.path.name,
    )


def _destination(config: Config, folder_name: str, file_name: str, names: _Names) -> Path:
    allowed = {c.name for c in config.categories} | {config.review_folder}
    if folder_name not in allowed:
        raise UnsafeDestinationError(f"folder {folder_name!r} is not configured")
    safe = safe_component(file_name)
    if not safe or safe in {".", ".."}:
        raise UnsafeDestinationError(f"unusable file name {file_name!r}")
    folder = config.output_root / folder_name
    final = folder / names.claim(folder, safe)
    if final.parent != folder or not final.is_relative_to(config.output_root):
        raise UnsafeDestinationError(f"{final} escapes {folder}")
    return final
