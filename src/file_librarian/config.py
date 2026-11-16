"""Configuration: one YAML file, validated at startup.

An invalid configuration aborts the run before any file is touched. Every
section mirrors a block of ``config.example.yaml``; keep the two in sync.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from file_librarian.locales import DEFAULT_LANGUAGE, LOCALES, Language

APP_NAME = "file-librarian"
CONFIG_ENV_VAR = "FLIB_CONFIG"

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveInt = Annotated[int, Field(gt=0)]


class ConfigError(Exception):
    """The configuration file is missing, unreadable or invalid."""


class _Section(BaseModel):
    # Unknown keys are almost always typos; reject them instead of ignoring them.
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderConfig(_Section):
    name: str = "anthropic"
    model: str = Field(min_length=1)
    max_chars_per_file: PositiveInt = 4000
    requests_per_minute: PositiveInt = 30
    max_retries: Annotated[int, Field(ge=0)] = 3
    timeout_seconds: Annotated[float, Field(gt=0)] = 60.0


class LimitsConfig(_Section):
    max_files_per_run: PositiveInt = 200
    stable_seconds: Annotated[int, Field(ge=0)] = 60
    max_attempts: PositiveInt = 3
    confidence_threshold: Fraction = 0.7
    scanned_pdf_max_pages: PositiveInt = 3


class NamingConfig(_Section):
    pattern: str = "{date}_{subject}"
    date_order: Literal["DMY", "MDY", "YMD"] = "DMY"
    max_length: Annotated[int, Field(ge=16, le=255)] = 80
    rename_confidence_threshold: Fraction = 0.8

    @field_validator("pattern")
    @classmethod
    def _pattern_has_subject(cls, value: str) -> str:
        if "{subject}" not in value:
            raise ValueError("naming.pattern must contain {subject}")
        return value


class Category(_Section):
    name: str = Field(min_length=1, max_length=64)
    hint: str | None = None
    rename: bool = True

    @field_validator("name")
    @classmethod
    def _name_is_a_safe_folder(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("category name must not start or end with spaces")
        if "/" in value or ":" in value or value.startswith("."):
            raise ValueError("category name must be a plain folder name (no '/', ':', leading '.')")
        return value


class FinderTagsConfig(_Section):
    enabled: bool = True


class SensitiveConfig(_Section):
    detect_secrets: bool = True


class McpConfig(_Section):
    read_only: bool = False


def default_database_path() -> Path:
    """The database lives outside the organized folders so it is never moved by mistake."""
    return Path.home() / "Library" / "Application Support" / APP_NAME / "library.db"


def default_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / APP_NAME / "config.yaml"


class Config(_Section):
    language: Language = DEFAULT_LANGUAGE
    inputs: list[Path] = Field(min_length=1)
    output_root: Path
    review_folder: str = Field(min_length=1)
    database: Path = Field(default_factory=default_database_path)
    provider: ProviderConfig
    limits: LimitsConfig = LimitsConfig()
    naming: NamingConfig = NamingConfig()
    categories: list[Category] = Field(min_length=1)
    """Filled from ``language`` when omitted, like ``output_root`` and ``review_folder``."""
    folders: Literal["as_unit", "ignore"] = "as_unit"
    finder_tags: FinderTagsConfig = FinderTagsConfig()
    ignore: list[str] = Field(default_factory=list)
    never_send: list[str] = Field(default_factory=list)
    sensitive: SensitiveConfig = SensitiveConfig()
    mcp: McpConfig = McpConfig()

    @model_validator(mode="before")
    @classmethod
    def _apply_language_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        locale = LOCALES.get(data.get("language", DEFAULT_LANGUAGE))
        if locale is None:
            return data  # the language field itself reports the error
        data = dict(data)
        data.setdefault("inputs", ["~/Downloads"])
        data.setdefault("output_root", f"~/Downloads/{locale.output_folder}")
        data.setdefault("review_folder", locale.review_folder)
        data.setdefault("categories", [c.as_dict() for c in locale.categories])
        return data

    @field_validator("inputs", mode="after")
    @classmethod
    def _expand_inputs(cls, value: list[Path]) -> list[Path]:
        return [p.expanduser() for p in value]

    @field_validator("output_root", "database", mode="after")
    @classmethod
    def _expand_path(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def _check_consistency(self) -> Config:
        names = [c.name for c in self.categories]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate category names: {', '.join(duplicates)}")
        if self.review_folder in names:
            raise ValueError(f"review_folder {self.review_folder!r} must not also be a category")
        for folder in self.inputs:
            if folder == self.output_root:
                raise ValueError(f"output_root must not be an input folder itself: {folder}")
        return self

    def category(self, name: str) -> Category | None:
        return next((c for c in self.categories if c.name == name), None)


def parse_config(raw: object) -> Config:
    """Validate an already-parsed YAML document."""
    if not isinstance(raw, dict):
        raise ConfigError("the configuration must be a YAML mapping")
    try:
        return Config.model_validate(raw)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            where = ".".join(str(part) for part in err["loc"]) or "(root)"
            lines.append(f"  {where}: {err['msg']}")
        raise ConfigError("invalid configuration:\n" + "\n".join(lines)) from exc


def load_config(path: Path | None = None) -> Config:
    """Read and validate the configuration file."""
    path = path or default_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"no configuration at {path}; copy config.example.yaml there to start"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(raw)
