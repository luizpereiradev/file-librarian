"""Typed values passed between pipeline stages.

Scanner -> Extractor -> (FileContext) -> Classifier -> (Classification)
-> Planner -> (Operation) -> Executor -> Indexer.

The model only ever produces a ``Classification``. It never sees a path it can
act on; the planner decides what happens on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from file_librarian.locales import Language

Kind = Literal[
    "bill",
    "receipt",
    "invoice",
    "statement",
    "contract",
    "payslip",
    "tax",
    "ticket",
    "policy",
    "report",
    "form",
    "letter",
    "manual",
    "other",
]
"""What sort of document a file is (#88); ``fields.KINDS`` lists them in this order."""


class FileStatus(StrEnum):
    ORGANIZED = "organized"
    REVIEW = "review"
    SKIPPED = "skipped"
    MISSING = "missing"
    FAILED = "failed"
    SENSITIVE = "sensitive"
    DUPLICATE = "duplicate"
    """Same content as a file already filed; moved to ``duplicates_folder`` (RF-21)."""


class OperationStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    UNDONE = "undone"
    FAILED = "failed"
    SKIPPED = "skipped"


class CopyPhase(StrEnum):
    """How far a move to another volume got (RF-33), journaled with the operation."""

    COPYING = "copying"
    """A copy is being written to the staging folder; the source is untouched."""
    COPIED = "copied"
    """The copy is verified and being renamed into place."""
    PLACED = "placed"
    """The copy is in place; the source is being removed."""


class RunMode(StrEnum):
    RUN = "run"
    SIMULATE = "simulate"


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    MCP = "mcp"
    REPROCESS = "reprocess"
    """``flib reprocess``: files already in the library, filed again under new rules."""


class ReviewReason(StrEnum):
    """Why a file is in the review folder (``files.review_reason``)."""

    LOW_CONFIDENCE = "low_confidence"
    """The model's confidence was below ``limits.confidence_threshold``."""
    UNKNOWN_CATEGORY = "unknown_category"
    """The model chose a category the configuration does not have."""
    CATEGORY_REMOVED = "category_removed"
    """Its category was removed (``flib reprocess --remove-category --to-review``)."""
    MOVED_BY_HAND = "moved_by_hand"
    """``flib reconcile`` found it in the review folder, moved there outside flib."""


def explain_review(
    reason: ReviewReason | None, suggested: str | None, confidence: float | None
) -> str:
    """A review reason in words, for ``flib inspect`` and the MCP tools."""
    if reason is ReviewReason.LOW_CONFIDENCE:
        text = "the AI was not sure enough"
        if confidence is not None:
            text += f" (confidence {confidence:.2f})"
        return text + (f"; it suggested {suggested}" if suggested else "")
    if reason is ReviewReason.UNKNOWN_CATEGORY:
        return f"the AI chose a category that is not configured: {suggested or '?'}"
    if reason is ReviewReason.CATEGORY_REMOVED:
        return "its category was removed from the configuration"
    if reason is ReviewReason.MOVED_BY_HAND:
        return "it was moved to the review folder outside flib"
    return "unknown (filed before flib recorded review reasons)"


@dataclass(frozen=True, slots=True)
class FileState:
    """What the index knows about a content hash."""

    status: FileStatus
    attempts: int
    path: Path | None = None
    """Where the index says the file with this content is now."""
    kept: frozenset[str] = frozenset()
    """Paths (NFC) of other copies of this content that ``flib undo`` put back: a
    duplicate moved back by hand is left where the user wanted it."""
    sensitive_by: str | None = None
    """For a sensitive content: ``name`` (looked at again on every scan) or ``content``."""


NameChoice = Literal["original", "proposed"]
"""The model's verdict on a file's name: keep the original subject, or use its proposal."""

VisualKind = Literal["image", "pdf"]
"""What a file can show a vision model: a picture, or the pages of a scanned PDF."""

# The media type each visual kind is sent as, once prepared.
VISUAL_MEDIA_TYPES: dict[VisualKind, str] = {"image": "image/jpeg", "pdf": "application/pdf"}


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file's content prepared for a vision model: a downscaled image or trimmed PDF."""

    media_type: str
    data: bytes = field(repr=False)
    pages: int | None = None
    """For a PDF, how many of its first pages are included."""


@dataclass(frozen=True, slots=True)
class BundleInfo:
    """What an ``.app`` bundle's ``Info.plist`` says about it (strings only, each capped)."""

    name: str | None = None
    """``CFBundleName``; the only one sent in the prompt's metadata block."""
    identifier: str | None = None
    """``CFBundleIdentifier``."""
    version: str | None = None
    """``CFBundleShortVersionString``."""

    def plist_items(self) -> tuple[tuple[str, str], ...]:
        """The keys that were present, under their ``Info.plist`` names, in a fixed order."""
        items = (
            ("CFBundleName", self.name),
            ("CFBundleIdentifier", self.identifier),
            ("CFBundleShortVersionString", self.version),
        )
        return tuple((key, value) for key, value in items if value is not None)


@dataclass(frozen=True, slots=True)
class PictureText:
    """How text recognised in an image file sits in it (#100): the model is told, so a
    flyer with a few words is judged as a picture and not as a broken document."""

    width: int | None
    height: int | None
    """The image's size in pixels, when the helper could tell."""
    lines: int
    """Lines of text found."""
    coverage: float
    """The share of the image's area inside those lines' boxes, 0 to 1."""


@dataclass(frozen=True, slots=True)
class FileMetadata:
    """What the extractor stage learned about a file besides its text.

    Each field is ``None`` when it does not apply. Only ``content`` and
    ``bundle.name`` reach the provider (see ``providers/prompt.py``); the rest
    explains the outcome locally.
    """

    content: str | None = None
    """Why no text is sent: ``"no readable text"``, ``"withheld (never_send)"``..."""
    extraction_error: str | None = None
    """Why reading the file failed (RF-27); it is then classified from metadata."""
    sensitive: str | None = None
    """What made the file sensitive, in words (``name looks like a credential file:
    password``, ``content: looks like private key``)."""
    sensitive_by: Literal["name", "content"] | None = None
    """Whether its name alone (looked at again on every scan) or its content made it
    sensitive."""
    bundle: BundleInfo | None = None
    """For an ``.app`` unit, what its ``Info.plist`` says."""
    text_source: Literal["ocr"] | None = None
    """``"ocr"`` when the text was recognised in the image or scanned pages (RF-26)."""
    ocr_error: str | None = None
    """Why text recognition failed; the file was then handled as with OCR off."""
    picture: PictureText | None = None
    """For an image whose text came from OCR: its size and how much of it is text."""


@dataclass(frozen=True, slots=True)
class FileContext:
    """Everything the classifier may know about one file (or one folder/.app unit)."""

    path: Path
    sha256: str
    size_bytes: int
    extension: str
    mtime_ns: int = 0
    inode: int = 0
    downloaded: date | None = None
    source_urls: tuple[str, ...] = ()
    text: str | None = None
    is_unit: bool = False
    sensitive: bool = False
    metadata: FileMetadata = FileMetadata()
    visual: VisualKind | None = None
    """Set when ``vision.enabled`` and the file is an image or a PDF with no text layer."""
    attachment: Attachment | None = None
    """The prepared image or pages, set by the classifier only for the provider call."""
    vision_used: bool = False
    """The answer came from a model that saw the image or pages (so renaming is allowed)."""
    input: Path | None = None
    """The input folder it was found in; ``None`` for a file already in the library."""


class Classification(BaseModel):
    """The provider's answer. Validated by schema before the planner sees it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1)
    suggested_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=600)
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=10
    )
    confidence: float = Field(ge=0.0, le=1.0)
    document_date: date | None = None
    name_choice: NameChoice = "proposed"
    """Which describes the file better: its original name, or ``suggested_name``."""
    name_choice_reason: str | None = Field(default=None, max_length=300)
    issuer: str | None = Field(default=None, min_length=1, max_length=100)
    """The organisation the document comes from (a company, bank, public body or
    brand); the prompt asks for no private person's name, which nothing can check.
    ``None`` when there is none or it is unclear.
    Category layouts with ``{issuer}`` file into a folder named after it."""
    amount: str | None = Field(default=None, pattern=r"^\d{1,15}(\.\d{1,4})?$")
    """The document's total or amount due as a decimal string (``"1234.56"``), in
    ``currency``; ``None`` when it states none (#88). ``fields.clean_amount`` reads it."""
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    """The ISO 4217 code of the document's amounts."""
    due_date: date | None = None
    """When the amount is due, for a bill or an invoice."""
    period: str | None = Field(default=None, pattern=r"^\d{4}(-(0[1-9]|1[0-2]))?$")
    """What the document refers to: a month (``YYYY-MM``) or a year (``YYYY``)."""
    kind: Kind | None = None
    """What sort of document it is, from a closed list (``fields.KINDS``)."""

    @model_validator(mode="after")
    def _amount_has_currency(self) -> Classification:
        if self.amount is not None and self.currency is None:
            raise ValueError("an amount needs its currency")
        return self


@dataclass(frozen=True, slots=True)
class Decision:
    """The classifier's outcome for one item (stage 3 output, planner input)."""

    context: FileContext
    classification: Classification | None = None
    error: str | None = None
    by_rule: bool = False
    """The classification is its input's fixed category, made here without any AI (#89)."""


@dataclass(frozen=True, slots=True)
class CategoryRule:
    name: str
    hint: str | None = None


@dataclass(frozen=True, slots=True)
class Correction:
    """A past user correction, reused as a few-shot example (RF-29).

    Only what the provider already saw or produced: the kind and extension of
    the file, the description it wrote, and the value the user changed. Never
    the file's content.
    """

    field: Literal["category", "name"]
    old_value: str | None
    """``None`` for a category: the file had none (it was in review or left in place)."""
    new_value: str
    extension: str = ""
    is_unit: bool = False
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Rules:
    """What the classifier is allowed to answer, built from the configuration."""

    categories: tuple[CategoryRule, ...]
    language: Language
    examples: tuple[Correction, ...] = ()
    """Recent user corrections, newest first, sent as few-shot examples (RF-29)."""
    date_order: Literal["DMY", "MDY", "YMD"] = "DMY"
    """How to read ambiguous numeric dates in documents (``naming.date_order``)."""

    @property
    def category_names(self) -> frozenset[str]:
        return frozenset(c.name for c in self.categories)


@dataclass(frozen=True, slots=True)
class FileRecord:
    """One row of the ``files`` table as the executor writes it."""

    sha256: str
    original_path: Path
    current_path: Path
    status: FileStatus
    extension: str = ""
    size_bytes: int = 0
    is_unit: bool = False
    source_url: str | None = None
    category: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    confidence: float | None = None
    attempts: int = 0
    last_error: str | None = None
    provider: str | None = None
    model: str | None = None
    name_choice: NameChoice | None = None
    """The name choice the planner applied; ``None`` when no rename was considered."""
    name_choice_reason: str | None = None
    review_reason: ReviewReason | None = None
    """Why it goes to review; ``None`` unless ``status`` is ``review``."""
    review_category: str | None = None
    """The category the model suggested for a file in review, if any."""
    duplicate_of: int | None = None
    """For a duplicate (RF-21), the id of the row of the file it is a copy of."""
    document_date: str | None = None
    """The date the document refers to (YYYY-MM-DD), as the model read it."""
    issuer: str | None = None
    """The organisation the document comes from, as the model named it."""
    layout_date: str | None = None
    """The date a category layout's ``{year}``/``{month}`` came from when it was filed."""
    amount_minor: int | None = None
    """The document's amount in ``currency``'s minor units (cents), as the model read it (#88)."""
    currency: str | None = None
    due_date: str | None = None
    """When the amount is due (YYYY-MM-DD)."""
    period: str | None = None
    """What the document refers to: ``YYYY-MM`` or ``YYYY``."""
    kind: str | None = None
    """One of ``fields.KINDS``."""
    source_input: Path | None = None
    """The input folder the file was taken from (#89); an existing row keeps its own."""
    by_rule: bool = False
    """Filed by its input's fixed category, with no AI answer (#89)."""
    sensitive_by: Literal["name", "content"] | None = None
    """For a sensitive file: whether its name or its content made it sensitive."""
    sensitive_reason: str | None = None
    """For a sensitive file: why, in words."""


@dataclass(frozen=True, slots=True)
class Operation:
    """One planned move/rename. Recorded as ``planned`` before it is executed."""

    sha256: str
    src: Path
    dst: Path
    reason: str = ""
