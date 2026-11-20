"""Configuration: one YAML file, validated at startup.

An invalid configuration aborts the run before any file is touched. Every
section mirrors a block of ``config.example.yaml``; keep the two in sync.

This module is the first line of the project's main safety rule: files only
ever move into folders under ``output_root``. Folder names and the naming
pattern are therefore validated strictly here, so later stages can rely on
them; the planner still checks every destination again.
"""

from __future__ import annotations

import ipaddress
import os
import re
import string
import unicodedata
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    AfterValidator,
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

NAMING_PLACEHOLDERS = frozenset({"date", "subject"})
# Every provider name a configuration may use. Kept here, not imported from
# providers/, so config does not depend on provider code; a test keeps the two in step.
PROVIDER_NAMES = frozenset({"anthropic", "fake", "claude-code", "ollama", "openai-compat"})

OLLAMA_BASE_URL = "http://localhost:11434/v1"
"""Ollama's OpenAI-compatible endpoint on this Mac, the ``ollama`` provider's default."""

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveInt = Annotated[int, Field(gt=0)]


class ConfigError(Exception):
    """The configuration file is missing, unreadable or invalid."""


def folder_key(name: str) -> str:
    """How macOS compares names on a default (case-insensitive) APFS volume."""
    return unicodedata.normalize("NFC", name).casefold()


def _safe_folder_name(value: str) -> str:
    """A single path component that cannot escape or hide: no separators, no dot names."""
    value = unicodedata.normalize("NFC", value)
    if value != value.strip():
        raise ValueError("must not start or end with spaces")
    if any(ch in value for ch in "/:\\"):
        raise ValueError("must be a plain folder name (no '/', ':' or '\\')")
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValueError("must not contain control or invisible characters")
    if value.startswith("."):
        raise ValueError("must not start with '.'")
    return value


SafeFolderName = Annotated[
    str, Field(min_length=1, max_length=64), AfterValidator(_safe_folder_name)
]


def _normalize_path(value: Path) -> Path:
    path = Path(os.path.normpath(value.expanduser()))
    if not path.is_absolute():
        raise ValueError(f"must be an absolute path or start with '~': {value}")
    return path


AbsolutePath = Annotated[Path, AfterValidator(_normalize_path)]


def canonical(path: Path) -> Path:
    """The path as macOS sees it: symlinks resolved, case and Unicode form folded.

    Two spellings of the same folder (``~/downloads`` vs ``~/Downloads``, NFD vs
    NFC, through a symlink) give the same result. Use it only to compare paths.
    """
    real = Path(os.path.realpath(path))
    return Path(*(folder_key(part) for part in real.parts))


def is_within(path: Path, folder: Path) -> bool:
    """``path`` is ``folder`` or inside it, comparing the way macOS does."""
    a, b = canonical(path), canonical(folder)
    return a == b or a.is_relative_to(b)


class _Section(BaseModel):
    # Unknown keys are almost always typos; reject them instead of ignoring them.
    model_config = ConfigDict(extra="forbid", frozen=True)


def is_loopback_url(url: str) -> bool:
    """``url`` points at this Mac: ``localhost`` or a loopback address."""
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base_url(value: str) -> str:
    """An http(s) URL with a host and nothing that could hide a secret or change the path."""
    value = value.strip()
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as exc:
        raise ValueError(f"provider.base_url is not a valid URL: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("provider.base_url must be an http:// or https:// URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError(
            "provider.base_url must not contain a user name or password; "
            "the API key goes in the Keychain or an environment variable"
        )
    if parts.query or parts.fragment:
        raise ValueError("provider.base_url must not have a query (?) or fragment (#)")
    return value.rstrip("/")


_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ProviderConfig(_Section):
    name: str = "anthropic"
    model: str = Field(min_length=1)
    max_chars_per_file: PositiveInt = 4000
    max_examples: Annotated[int, Field(ge=0, le=50)] = 10
    """Recent corrections sent with each request as examples (RF-29); ``0`` sends none."""
    requests_per_minute: PositiveInt = 30
    max_retries: Annotated[int, Field(ge=0)] = 3
    timeout_seconds: Annotated[float, Field(gt=0)] = 60.0
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    """How hard the model thinks. Classification is a simple task; ``low`` is plenty."""
    api_key_source: Literal["auto", "environment", "keychain", "env_file"] = "auto"
    """Where the API key comes from; ``auto`` tries environment, Keychain, then env_file."""
    env_file: AbsolutePath | None = None
    """A ``.env`` file with ``ANTHROPIC_API_KEY=...``, used by ``auto`` and ``env_file``."""
    command: AbsolutePath | None = None
    """The ``claude`` executable for the ``claude-code`` provider; found on PATH and in
    the usual install folders when unset."""
    base_url: Annotated[str, AfterValidator(_base_url)] | None = None
    """``openai-compat`` (required) and ``ollama``: the API root, the part before
    ``/chat/completions``. ``ollama`` defaults to ``OLLAMA_BASE_URL``."""
    api_key_env: str | None = None
    """``openai-compat``: the environment variable holding the key; unset, it is
    ``FLIB_OPENAI_COMPAT_API_KEY``."""
    price_input_per_mtok: Annotated[float, Field(ge=0)] = 0.0
    """``openai-compat`` and ``ollama``: US$ per million input tokens; ``0`` means
    unknown (or free), and then ``limits.max_cost_usd_per_run`` cannot stop a run."""
    price_output_per_mtok: Annotated[float, Field(ge=0)] = 0.0
    """US$ per million output tokens, like ``price_input_per_mtok``."""
    vision: bool = False
    """``openai-compat`` and ``ollama``: the model reads images, so with ``vision.enabled``
    they are sent to it. Scanned PDFs are never sent to these providers."""

    @model_validator(mode="before")
    @classmethod
    def _ollama_default_url(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("name") == "ollama" and not data.get("base_url"):
            data = dict(data) | {"base_url": OLLAMA_BASE_URL}
        return data

    @model_validator(mode="after")
    def _openai_compat_needs_a_url(self) -> ProviderConfig:
        if self.name == "openai-compat" and self.base_url is None:
            raise ValueError(
                "provider.base_url is required for openai-compat "
                "(for example https://api.openai.com/v1)"
            )
        return self

    @field_validator("api_key_env")
    @classmethod
    def _env_name(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.fullmatch(value):
            raise ValueError("provider.api_key_env must be an environment variable name")
        return value

    @field_validator("name")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        known = PROVIDER_NAMES
        if value not in known:
            raise ValueError(f"unknown provider {value!r} (known: {', '.join(sorted(known))})")
        return value


class LimitsConfig(_Section):
    max_files_per_run: PositiveInt = 200
    stable_seconds: Annotated[int, Field(ge=0)] = 60
    max_attempts: PositiveInt = 3
    confidence_threshold: Fraction = 0.7
    max_cost_usd_per_run: Annotated[float, Field(gt=0)] | None = 1.0
    """Stop sending files once the estimated provider cost reaches this; ``None`` = no cap."""


class NamingConfig(_Section):
    pattern: str = "{date}_{subject}"
    date_order: Literal["DMY", "MDY", "YMD"] = "DMY"
    max_length: Annotated[int, Field(ge=16, le=255)] = 80
    rename_confidence_threshold: Fraction = 0.8

    @field_validator("pattern")
    @classmethod
    def _pattern_is_a_file_name(cls, value: str) -> str:
        try:
            parts = list(string.Formatter().parse(value))
        except ValueError as exc:
            raise ValueError(f"naming.pattern is not a valid pattern: {exc}") from exc
        fields = {name for _, name, _, _ in parts if name is not None}
        unknown = fields - NAMING_PLACEHOLDERS
        if unknown:
            allowed = ", ".join(f"{{{p}}}" for p in sorted(NAMING_PLACEHOLDERS))
            raise ValueError(f"naming.pattern: unknown placeholder(s) {unknown}; use {allowed}")
        if "subject" not in fields:
            raise ValueError("naming.pattern must contain {subject}")
        if any(spec or conv for _, name, spec, conv in parts if name is not None):
            raise ValueError("naming.pattern placeholders take no format spec or conversion")
        literal = "".join(text for text, _, _, _ in parts)
        if any(ch in literal for ch in "/:\\") or ".." in literal:
            raise ValueError("naming.pattern must produce a file name, not a path")
        if any(unicodedata.category(ch).startswith("C") for ch in literal):
            raise ValueError("naming.pattern must not contain control characters")
        return value


class Category(_Section):
    name: SafeFolderName
    hint: str | None = None
    rename: bool = True


class FinderTagsConfig(_Section):
    enabled: bool = True


class VisionConfig(_Section):
    """Images and scanned PDFs sent to the model (RF-05, RF-26). Off by default: opt in.

    With it off, such files are classified from their name and metadata only.
    """

    enabled: bool = False
    max_image_px: Annotated[int, Field(ge=256, le=2576)] = 1568
    """Images are downscaled so the longer side is at most this many pixels."""
    max_pdf_pages: Annotated[int, Field(ge=1, le=20)] = 3
    """Only this many first pages of a PDF with no text layer are sent."""


class SensitiveConfig(_Section):
    detect_secrets: bool = True


class McpConfig(_Section):
    read_only: bool = False


class ScheduleConfig(_Section):
    time: str = "12:30"
    """Local time of the daily run, HH:MM (24-hour). Applied by ``flib schedule``."""

    @field_validator("time")
    @classmethod
    def _hh_mm(cls, value: str) -> str:
        hour, _, minute = value.partition(":")
        if not (hour.isdigit() and minute.isdigit() and len(minute) == 2):
            raise ValueError("schedule.time must look like 12:30")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError("schedule.time must be a valid 24-hour time")
        return f"{int(hour):02d}:{minute}"


class SummaryConfig(_Section):
    """The periodic summary after a scheduled run (RF-40): what waits for the user.

    It comes at most once every ``every_days``, and only when a file has waited
    in review or failed for ``min_age_days`` or more. It always goes to the run
    log; with ``notify`` it is also a macOS notification (counts only, no names).
    """

    notify: bool = True
    every_days: PositiveInt = 7
    min_age_days: Annotated[int, Field(ge=0)] = 2


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
    inputs: list[AbsolutePath] = Field(min_length=1)
    output_root: AbsolutePath
    review_folder: SafeFolderName
    database: AbsolutePath = Field(default_factory=default_database_path)
    provider: ProviderConfig
    limits: LimitsConfig = LimitsConfig()
    naming: NamingConfig = NamingConfig()
    categories: list[Category] = Field(min_length=1)
    """Filled from ``language`` when omitted, like ``output_root`` and ``review_folder``."""
    folders: Literal["as_unit", "ignore"] = "as_unit"
    finder_tags: FinderTagsConfig = FinderTagsConfig()
    ignore: list[str] = Field(default_factory=list)
    never_send: list[str] = Field(default_factory=list)
    vision: VisionConfig = VisionConfig()
    sensitive: SensitiveConfig = SensitiveConfig()
    mcp: McpConfig = McpConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    summary: SummaryConfig = SummaryConfig()

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
        return _moved_keys(data)

    @model_validator(mode="after")
    def _check_folder_names(self) -> Config:
        seen: dict[str, str] = {}
        for name in [*(c.name for c in self.categories), self.review_folder]:
            key = folder_key(name)
            if key in seen:
                what = "review_folder" if name == self.review_folder else "category"
                raise ValueError(
                    f"{what} {name!r} is the same folder as {seen[key]!r} on macOS "
                    "(names are compared ignoring case and Unicode form)"
                )
            seen[key] = name
        return self

    @model_validator(mode="after")
    def _check_paths(self) -> Config:
        home = Path.home()
        for folder in self.inputs:
            if canonical(folder) in {canonical(Path(folder.anchor)), canonical(home)}:
                raise ValueError(f"input {folder} is too broad; pick specific folders")
            if is_within(folder, self.output_root):
                raise ValueError(f"input {folder} must not be inside output_root")
        for i, a in enumerate(self.inputs):
            for b in self.inputs[i + 1 :]:
                if is_within(a, b) or is_within(b, a):
                    raise ValueError(f"inputs must not overlap: {a} and {b}")
        if is_within(self.database, self.output_root) or any(
            is_within(self.database, folder) for folder in self.inputs
        ):
            raise ValueError("database must be outside the input folders and output_root")
        return self

    def category(self, name: str) -> Category | None:
        return next((c for c in self.categories if c.name == name), None)


# Options the configuration accepts but no code reads yet, as dotted paths
# (``"limits.some_option"``) mapped to the milestone that activates them.
# ``flib check-config`` warns when one is set to anything but its default. The PR
# that activates an option removes it from here. Planned providers are tracked in
# ``providers.PLANNED`` instead.
INACTIVE_OPTIONS: dict[str, str] = {}


def _option_value_and_default(config: Config, dotted: str) -> tuple[Any, Any]:
    *sections, field = dotted.split(".")
    section: BaseModel = config
    for name in sections:
        section = getattr(section, name)
    info = type(section).model_fields[field]
    return getattr(section, field), info.get_default(call_default_factory=True)


def inactive_options_set(config: Config) -> list[tuple[str, str]]:
    """Each inactive option set to a non-default value, with the milestone that activates it."""
    found = []
    for dotted, milestone in sorted(INACTIVE_OPTIONS.items()):
        value, default = _option_value_and_default(config, dotted)
        if value != default:
            found.append((dotted, milestone))
    return found


def _moved_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Accept keys that moved, so an older configuration keeps working.

    ``limits.scanned_pdf_max_pages`` (a placeholder before vision existed) is
    now ``vision.max_pdf_pages``; an explicit ``vision.max_pdf_pages`` wins.
    """
    limits = data.get("limits")
    if isinstance(limits, dict) and "scanned_pdf_max_pages" in limits:
        limits = dict(limits)
        pages = limits.pop("scanned_pdf_max_pages")
        data["limits"] = limits
        vision = data.get("vision")
        if vision is None:
            data["vision"] = {"max_pdf_pages": pages}
        elif isinstance(vision, dict):
            data["vision"] = {"max_pdf_pages": pages} | vision
    return data


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
            f"no configuration at {path}; run `flib init`, or copy config.example.yaml there"
        ) from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(raw)
