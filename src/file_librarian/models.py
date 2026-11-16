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
from typing import Annotated

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
class FileContext:
    """Everything the classifier may know about one file (or one folder/.app unit)."""

    path: Path
    sha256: str
    size_bytes: int
    extension: str
    source_urls: tuple[str, ...] = ()
    text: str | None = None
    image_paths: tuple[Path, ...] = ()
    is_unit: bool = False
    sensitive: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


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
class CategoryRule:
    name: str
    hint: str | None = None


@dataclass(frozen=True, slots=True)
class Correction:
    """A past user correction, reused as a few-shot example (RF-29)."""

    original_name: str
    field: str
    old_value: str | None
    new_value: str


@dataclass(frozen=True, slots=True)
class Rules:
    """What the classifier is allowed to answer, built from the configuration."""

    categories: tuple[CategoryRule, ...]
    language: Language
    examples: tuple[Correction, ...] = ()

    @property
    def category_names(self) -> frozenset[str]:
        return frozenset(c.name for c in self.categories)


@dataclass(frozen=True, slots=True)
class Operation:
    """One planned move/rename. Recorded as ``planned`` before it is executed."""

    sha256: str
    src: Path
    dst: Path
    reason: str = ""
