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

from pydantic import BaseModel, ConfigDict, Field

from file_librarian.locales import Language


class FileStatus(StrEnum):
    ORGANIZED = "organized"
    REVIEW = "review"
    SKIPPED = "skipped"
    MISSING = "missing"
    FAILED = "failed"
    SENSITIVE = "sensitive"


class OperationStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    UNDONE = "undone"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunMode(StrEnum):
    RUN = "run"
    SIMULATE = "simulate"


class RunTrigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    MCP = "mcp"


@dataclass(frozen=True, slots=True)
class FileState:
    """What the index knows about a content hash."""

    status: FileStatus
    attempts: int


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
    """What made the file sensitive (a secret kind or a credential file name)."""
    bundle: BundleInfo | None = None
    """For an ``.app`` unit, what its ``Info.plist`` says."""


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


@dataclass(frozen=True, slots=True)
class Decision:
    """The classifier's outcome for one item (stage 3 output, planner input)."""

    context: FileContext
    classification: Classification | None = None
    error: str | None = None


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


@dataclass(frozen=True, slots=True)
class Operation:
    """One planned move/rename. Recorded as ``planned`` before it is executed."""

    sha256: str
    src: Path
    dst: Path
    reason: str = ""
