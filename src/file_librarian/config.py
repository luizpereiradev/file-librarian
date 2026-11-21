"""Configuration: one YAML file, validated at startup.

An invalid configuration aborts the run before any file is touched. Every
section mirrors a block of ``config.example.yaml``; keep the two in sync.

This module is the first line of the project's main safety rule: files only
ever move into folders under ``output_root``. Folder names and the naming
pattern are therefore validated strictly here, so later stages can rely on
them; the planner still checks every destination again.
"""

from __future__ import annotations

import functools
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
from file_librarian.models import Kind

APP_NAME = "file-librarian"
CONFIG_ENV_VAR = "FLIB_CONFIG"

NAMING_PLACEHOLDERS = frozenset({"date", "subject"})
# Every provider name a configuration may use. Kept here, not imported from
# providers/, so config does not depend on provider code; a test keeps the two in step.
PROVIDER_NAMES = frozenset({"anthropic", "fake", "claude-code", "ollama", "openai-compat"})

OLLAMA_BASE_URL = "http://localhost:11434/v1"
"""Ollama's OpenAI-compatible endpoint on this Mac, the ``ollama`` provider's default."""
DEFAULT_EMBEDDING_MODEL = "embeddinggemma"
"""Semantic search's default model: small (about 600 MB), multilingual, 768 dimensions."""

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
    real = _without_data_volume(Path(os.path.realpath(path)))
    return Path(*(folder_key(part) for part in real.parts))


_DATA_VOLUME = ("/", "system", "volumes", "data")
FIRMLINKS_FILE = Path("/usr/share/firmlinks")
_FIRMLINK_FALLBACK = (
    "Applications", "Library", "Users", "Volumes", "cores", "opt", "private", "usr/local",
    "usr/libexec/cups", "usr/share/snmp", "AppleInternal", "System/Library/Caches",
    "System/Library/Assets", "System/Library/PreinstalledAssets", "System/Library/AssetsV2",
    "System/Library/PreinstalledAssetsV2",
    "System/Library/CoreServices/CoreTypes.bundle/Contents/Library",
    "System/Library/Speech",
)  # fmt: skip


@functools.cache
def firmlinked_roots() -> tuple[tuple[str, ...], ...]:
    """The folders of the system volume that are firmlinks into the data volume, as
    folded path parts (``/usr/share/firmlinks``, else a built-in list)."""
    try:
        lines = FIRMLINKS_FILE.read_text(encoding="utf-8").splitlines()
        roots = [line.split("\t", 1)[0].strip().strip("/") for line in lines if line.strip()]
    except OSError:
        roots = []
    roots = [r for r in roots if r] or list(_FIRMLINK_FALLBACK)
    return tuple(tuple(folder_key(p) for p in r.split("/")) for r in roots)


def _without_data_volume(path: Path) -> Path:
    """``/System/Volumes/Data/Users/x`` is ``/Users/x``: macOS joins the two with a
    firmlink, which ``realpath`` does not resolve. Stripped whenever what follows is
    one of the firmlinked folders, whether or not the path exists."""
    parts = path.parts
    if len(parts) <= 4 or tuple(folder_key(p) for p in parts[:4]) != _DATA_VOLUME:
        return path
    rest = tuple(folder_key(p) for p in parts[4:])
    if any(rest[: len(root)] == root for root in firmlinked_roots()):
        return Path("/", *parts[4:])
    return path


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_dev, st.st_ino


def same_place(a: Path, b: Path) -> bool:
    """``a`` and ``b`` are the same folder: by path the way macOS compares it, or, when
    both exist, by device and inode (any other spelling: a firmlink, a hard link)."""
    if canonical(a) == canonical(b):
        return True
    ident = _identity(a)
    return ident is not None and ident == _identity(b)


def is_within(path: Path, folder: Path) -> bool:
    """``path`` is ``folder`` or inside it, comparing the way macOS does.

    Paths are compared first; then, when ``folder`` exists, ``path`` and each
    folder above it by device and inode, so no spelling of ``folder`` escapes.
    """
    a, b = canonical(path), canonical(folder)
    if a == b or a.is_relative_to(b):
        return True
    target = _identity(folder)
    if target is None:
        return False
    real = Path(os.path.realpath(path))
    return any(_identity(p) == target for p in (real, *real.parents))


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


def _check_url(value: str, option: str) -> str:
    """An http(s) URL with a host and nothing that could hide a secret or change the path."""
    value = value.strip()
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError as exc:
        raise ValueError(f"{option} is not a valid URL: {exc}") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{option} must be an http:// or https:// URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError(
            f"{option} must not contain a user name or password; "
            "the API key goes in the Keychain or an environment variable"
        )
    if parts.query or parts.fragment:
        raise ValueError(f"{option} must not have a query (?) or fragment (#)")
    return value.rstrip("/")


def _base_url(value: str) -> str:
    return _check_url(value, "provider.base_url")


def _semantic_base_url(value: str) -> str:
    return _check_url(value, "search.semantic.base_url")


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


DAY_CAP_RUNS = 3
"""Without ``limits.max_cost_usd_per_day``: how many per-run caps automatic runs get a day."""


class LimitsConfig(_Section):
    max_files_per_run: PositiveInt = 200
    stable_seconds: Annotated[int, Field(ge=0)] = 60
    max_attempts: PositiveInt = 3
    confidence_threshold: Fraction = 0.7
    max_cost_usd_per_run: Annotated[float, Field(gt=0)] | None = 1.0
    """Stop sending files once the estimated provider cost reaches this; ``None`` = no cap."""
    max_cost_usd_per_day: Annotated[float, Field(gt=0)] | None = None
    """Automatic runs (daily and watch) stop sending files once their cost today, local
    time, reaches this. Left out: ``DAY_CAP_RUNS`` times ``max_cost_usd_per_run``; set to
    ``null``: no daily cap. See ``cost_cap_per_day``."""

    @property
    def cost_cap_per_day(self) -> float | None:
        """The daily cap for automatic runs, or ``None`` for none."""
        if "max_cost_usd_per_day" in self.model_fields_set:
            return self.max_cost_usd_per_day
        per_run = self.max_cost_usd_per_run
        return None if per_run is None else per_run * DAY_CAP_RUNS


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


LAYOUT_PLACEHOLDERS = frozenset({"year", "month", "issuer"})
MAX_LAYOUT_DEPTH = 3
"""Sub-folders a category's layout may add under its folder."""
# What each placeholder stands for when a layout is checked: the longest value
# ``year`` and ``month`` take, and a plain issuer folder name.
_LAYOUT_SAMPLES = {"year": "2026", "month": "12", "issuer": "Acme"}


def layout_segments(layout: str) -> tuple[str, ...]:
    """A validated layout's sub-folder templates, outermost first."""
    return tuple(layout.split("/"))


def _layout(value: str) -> str:
    """A category's sub-folder layout: ``{year}``, ``{year}/{month}``, ``{issuer}``...

    Each ``/``-separated segment becomes one folder name: at least one
    placeholder in the whole layout, only known placeholders, ``{issuer}``
    alone in its segment (its folder is matched with the ones already there),
    and every segment a safe folder name once filled in.
    """
    value = unicodedata.normalize("NFC", value)
    segments = value.split("/")
    if len(segments) > MAX_LAYOUT_DEPTH:
        raise ValueError(f"layout may have at most {MAX_LAYOUT_DEPTH} levels of sub-folders")
    used: set[str] = set()
    for segment in segments:
        if not segment:
            raise ValueError("layout must not start or end with '/' nor contain '//'")
        try:
            parts = list(string.Formatter().parse(segment))
        except ValueError as exc:
            raise ValueError(f"layout is not a valid pattern: {exc}") from exc
        fields = {name for _, name, _, _ in parts if name is not None}
        unknown = fields - LAYOUT_PLACEHOLDERS
        if unknown or "" in fields:
            allowed = ", ".join(f"{{{p}}}" for p in sorted(LAYOUT_PLACEHOLDERS))
            raise ValueError(f"layout: unknown placeholder(s) in {segment!r}; use {allowed}")
        if any(spec or conv for _, name, spec, conv in parts if name is not None):
            raise ValueError("layout placeholders take no format spec or conversion")
        if "issuer" in fields and segment != "{issuer}":
            raise ValueError("layout: {issuer} must be a whole sub-folder, alone between '/'")
        literal = "".join(text for text, _, _, _ in parts)
        if any(ch in literal for ch in ":\\"):
            raise ValueError("layout: sub-folders are separated by '/' only (no ':' or '\\')")
        filled = segment.format(**_LAYOUT_SAMPLES)
        if filled in {".", ".."}:
            raise ValueError(f"layout: {segment!r} is not a folder name")
        try:
            _safe_folder_name(filled)
        except ValueError as exc:
            raise ValueError(f"layout: sub-folder {segment!r} {exc}") from exc
        if len(filled) > 64:
            raise ValueError(f"layout: sub-folder {segment!r} is longer than 64 characters")
        used |= fields
    if not used:
        raise ValueError("layout needs at least one placeholder: {year}, {month} or {issuer}")
    return value


class Category(_Section):
    name: SafeFolderName
    hint: str | None = None
    rename: bool = True
    layout: Annotated[str, AfterValidator(_layout)] | None = None
    """Sub-folders inside the category folder, such as ``"{year}"`` or
    ``"{issuer}/{year}"``; ``None`` files everything at its top level."""


MAX_MIN_AGE_DAYS = 3650


def _glob(value: str) -> str:
    """A name pattern for ``only``/``ignore``: matched against a name, so never a path."""
    if not value.strip():
        raise ValueError("a pattern must not be empty")
    if "/" in value:
        raise ValueError(f"{value!r}: patterns match a name, not a path (no '/')")
    return value


Glob = Annotated[str, AfterValidator(_glob)]


class InputConfig(_Section):
    """One input folder and the rules for what flib may take from it (#89).

    A plain path in ``inputs`` is this with every rule at its default: flib
    takes everything at the folder's top level, as before.
    """

    path: AbsolutePath
    only: Annotated[list[Glob], Field(min_length=1)] | None = None
    """Name patterns; when set, everything that matches none of them is left alone."""
    ignore: list[Glob] = Field(default_factory=list)
    """Name patterns left alone in this folder, on top of the global ``ignore``."""
    min_age_days: Annotated[int, Field(ge=0, le=MAX_MIN_AGE_DAYS)] = 0
    """Only items that have not changed for this many days (see ``item_age``)."""
    category: SafeFolderName | None = None
    """A fixed category: everything taken from here is filed there with no AI call."""
    rename: bool = False
    """With ``category``: apply ``naming.pattern`` to the original name (no AI)."""
    layout: Annotated[str, AfterValidator(_layout)] | None = None
    """With ``category``: the sub-folders for files from here, instead of the category's."""
    folders: Literal["as_unit", "ignore"] | None = None
    """Overrides the global ``folders`` for this folder."""
    duplicates: Literal["move", "leave"] | None = None
    """Overrides the global ``duplicates`` for this folder."""

    @model_validator(mode="after")
    def _needs_a_category(self) -> InputConfig:
        if self.category is None:
            for option, used in (("rename", self.rename), ("layout", self.layout is not None)):
                if used:
                    raise ValueError(
                        f"input {self.path}: {option} applies only with a fixed category"
                    )
        return self

    @property
    def has_rules(self) -> bool:
        """Anything set besides the path."""
        return bool(self.model_fields_set - {"path"})


def _input_entry(value: Any) -> Any:
    """A plain path in ``inputs`` stands for an input with no rules."""
    if isinstance(value, str | Path):
        return {"path": value}
    return value


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


_OCR_LANGUAGE = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*")


def _ocr_language(value: str) -> str:
    if not _OCR_LANGUAGE.fullmatch(value):
        raise ValueError(f"{value!r} is not a language code like en-US or pt-BR")
    return value


class OcrConfig(_Section):
    """Text recognised locally in images and scanned PDFs (RF-26). On by default.

    It runs on this Mac (macOS's Vision framework); nothing is sent anywhere for
    it. The text then follows the same path as any document's text: the secret
    scan, ``never_send``, ``provider.max_chars_per_file``.
    """

    enabled: bool = True
    languages: Annotated[
        list[Annotated[str, AfterValidator(_ocr_language)]], Field(min_length=1, max_length=10)
    ] = Field(default_factory=lambda: ["en-US"])
    """Vision's language codes, most likely first. Filled from ``language`` when omitted."""
    max_pages: Annotated[int, Field(ge=1, le=20)] = 3
    """Only this many first pages of a PDF with no text layer are read."""
    min_chars: Annotated[int, Field(ge=1, le=10_000)] = 40
    """Fewer letters and digits than this and the file is not a document (a photo):
    it goes to vision when that is on, else it is classified from metadata."""


class SensitiveConfig(_Section):
    detect_secrets: bool = True
    allow_names: list[Glob] = Field(default_factory=list)
    """Name patterns never sensitive by their name alone (a "password reset guide.pdf");
    a secret found in the content still makes a file sensitive."""


class PrivacyConfig(_Section):
    """What happens to personal identifiers in the model's answers (#82)."""

    descriptions: Literal["redact", "verbatim"] = "redact"
    """``redact`` replaces phone numbers, e-mail addresses, document, card and account
    numbers in descriptions, tags and proposed names with markers (``privacy.scrub``)
    before they are stored; ``verbatim`` stores what the model wrote. The prompt asks
    the model to leave them out either way."""


class McpConfig(_Section):
    read_only: bool = False


class SemanticConfig(_Section):
    """Semantic search with embeddings (RF-15). Off by default: opt in.

    Embeddings come from ``POST {base_url}/embeddings`` of any OpenAI-compatible
    server, configured apart from the classification provider. Only what the
    index already stores is embedded (names, description, tags, category), never
    file content, and never a sensitive file.
    """

    enabled: bool = False
    """With it off nothing is embedded and ``flib search --semantic`` searches by words."""
    default: bool = False
    """Search semantically without ``--semantic`` (the MCP tool too, unless told otherwise)."""
    base_url: Annotated[str, AfterValidator(_semantic_base_url)] = OLLAMA_BASE_URL
    """The API root, the part before ``/embeddings``; Ollama on this Mac by default."""
    model: str = Field(default=DEFAULT_EMBEDDING_MODEL, min_length=1)
    """Changing it re-embeds the library on the next run or ``flib search --reindex``."""
    api_key_env: str | None = None
    """The environment variable holding the key; unset, it is ``FLIB_EMBEDDINGS_API_KEY``.
    The Keychain account ``embeddings-api-key`` (``flib key set --provider embeddings``)
    is tried next. Never the classification provider's key."""
    timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    batch_size: Annotated[int, Field(ge=1, le=256)] = 32
    """Texts sent per request."""
    min_similarity: Annotated[float, Field(ge=0, le=1)] = 0.25
    """Files found only by meaning need at least this cosine similarity to the query;
    files that match the words are always kept. 0 keeps every one. Measured for
    ``embeddinggemma`` with its prefixes; another model needs its own value."""
    query_prefix: Annotated[str | None, Field(max_length=200)] = None
    """Put before each query; unset, the one ``model`` documents (``embeddinggemma``),
    else none. ``""`` turns it off."""
    document_prefix: Annotated[str | None, Field(max_length=200)] = None
    """Put before each file's text; like ``query_prefix``. Changing it re-embeds the library."""

    @field_validator("api_key_env")
    @classmethod
    def _env_name(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.fullmatch(value):
            raise ValueError("search.semantic.api_key_env must be an environment variable name")
        return value


class SearchConfig(_Section):
    semantic: SemanticConfig = SemanticConfig()


class ScheduleConfig(_Section):
    time: str = "12:30"
    """Local time of the daily run, HH:MM (24-hour). Applied by ``flib schedule``."""
    wait_for_unlock_hours: Annotated[int, Field(ge=0, le=23)] = 8
    """After a scheduled run that skipped files readable only while the Mac is
    unlocked, how long it waits for an unlock to run once more; 0 = never waits."""
    mode: Literal["daily", "watch", "both"] = "daily"
    """``daily``: once a day at ``time``. ``watch``: whenever something arrives in an
    input folder (launchd ``WatchPaths``). ``both``: watch, with the daily run as a
    safety net. Applied by ``flib schedule``."""
    watch_min_interval_minutes: Annotated[int, Field(ge=1, le=1440)] = 5
    """In watch mode, launchd starts flib at most once every this many minutes."""
    watch_settle_minutes: Annotated[int, Field(ge=0, le=120)] = 10
    """In watch mode, how long a run waits for downloads in progress (and files still
    changing) to settle before it runs; what is still pending then waits for the next
    trigger. 0 = never waits."""

    @property
    def watches(self) -> bool:
        return self.mode in ("watch", "both")

    @property
    def daily(self) -> bool:
        return self.mode in ("daily", "both")

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


def _due_kinds() -> list[Kind]:
    return ["bill", "invoice", "statement", "tax"]


class DueConfig(_Section):
    """What is due (#91): ``flib due``, its line in ``flib status`` and its reminder.

    Only documents of ``kinds`` with a due date the AI read are listed; a due
    date on another kind shows only with ``flib due --all``. With ``remind``, a
    scheduled run's summary counts each open document once, when it is due
    within ``remind_days_before`` days or overdue (counts only, no names).
    ``done_tag``: a Finder tag that marks a document paid or done, like
    ``flib fix --done`` (none by default).
    """

    kinds: list[Kind] = Field(default_factory=_due_kinds)
    remind: bool = True
    remind_days_before: Annotated[int, Field(ge=0, le=60)] = 3
    done_tag: str | None = None

    @field_validator("done_tag")
    @classmethod
    def _tag_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or "\n" in value:
            raise ValueError("due.done_tag must be a Finder tag name")
        return value


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
    inputs: list[InputConfig] = Field(min_length=1)
    """Each a plain path or a mapping with ``path`` and per-folder rules (#89)."""
    output_root: AbsolutePath
    review_folder: SafeFolderName
    duplicates: Literal["move", "leave"] = "move"
    """What happens to a file with the same content as one already filed (RF-21):
    ``move`` puts it in ``duplicates_folder``; ``leave`` leaves it where it is."""
    duplicates_folder: SafeFolderName
    """Under ``output_root``, like ``review_folder``; never scanned, never a category."""
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
    ocr: OcrConfig = OcrConfig()
    """``ocr.languages`` is filled from ``language`` when omitted."""
    sensitive: SensitiveConfig = SensitiveConfig()
    privacy: PrivacyConfig = PrivacyConfig()
    mcp: McpConfig = McpConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    summary: SummaryConfig = SummaryConfig()
    due: DueConfig = DueConfig()
    search: SearchConfig = SearchConfig()

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
        data.setdefault("duplicates_folder", locale.duplicates_folder)
        data.setdefault("categories", [c.as_dict() for c in locale.categories])
        ocr = data.get("ocr")
        if ocr is None:
            data["ocr"] = {"languages": list(locale.ocr_languages)}
        elif isinstance(ocr, dict) and "languages" not in ocr:
            data["ocr"] = {"languages": list(locale.ocr_languages)} | ocr
        return _moved_keys(data)

    @model_validator(mode="after")
    def _check_folder_names(self) -> Config:
        seen: dict[str, str] = {}
        special = {self.review_folder: "review_folder", self.duplicates_folder: "duplicates_folder"}
        for name in [
            *(c.name for c in self.categories),
            self.review_folder,
            self.duplicates_folder,
        ]:
            key = folder_key(name)
            if key in seen:
                what = special.get(name, "category")
                raise ValueError(
                    f"{what} {name!r} is the same folder as {seen[key]!r} on macOS "
                    "(names are compared ignoring case and Unicode form)"
                )
            seen[key] = name
        return self

    @field_validator("inputs", mode="before")
    @classmethod
    def _plain_paths(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [_input_entry(item) for item in value]
        return value

    @model_validator(mode="after")
    def _check_input_categories(self) -> Config:
        for entry in self.inputs:
            if entry.category is not None and self.category(entry.category) is None:
                known = ", ".join(c.name for c in self.categories)
                raise ValueError(
                    f"input {entry.path}: category {entry.category!r} is not a configured "
                    f"category (configured: {known})"
                )
        return self

    @model_validator(mode="after")
    def _check_paths(self) -> Config:
        home = Path.home()
        for folder in self.input_paths:
            if same_place(folder, Path(folder.anchor)) or same_place(folder, home):
                raise ValueError(f"input {folder} is too broad; pick specific folders")
            if is_within(folder, self.output_root):
                raise ValueError(
                    f"input {folder} must not be inside output_root ({self.output_root}): "
                    "flib would pick up the files it has just filed"
                )
        paths = self.input_paths
        for i, a in enumerate(paths):
            for b in paths[i + 1 :]:
                if same_place(a, b):
                    raise ValueError(f"input {a} is listed twice (also as {b})")
                if is_within(a, b) or is_within(b, a):
                    inner, outer = (a, b) if is_within(a, b) else (b, a)
                    raise ValueError(
                        f"inputs must not overlap: {inner} is inside {outer}; list only one of them"
                    )
        if is_within(self.database, self.output_root) or any(
            is_within(self.database, folder) for folder in paths
        ):
            raise ValueError("database must be outside the input folders and output_root")
        return self

    @property
    def input_paths(self) -> list[Path]:
        """The input folders, in the order configured."""
        return [entry.path for entry in self.inputs]

    def input_rules(self, folder: Path) -> InputConfig:
        """The rules of the input ``folder`` (no rules for a folder that is not one)."""
        for entry in self.inputs:
            if entry.path == folder:
                return entry
        found = (e for e in self.inputs if same_place(e.path, folder))
        return next(found, InputConfig(path=folder))

    def input_of(self, path: Path) -> InputConfig | None:
        """The input whose top level holds ``path``, if any (not deeper: flib reads only
        the top level of an input, and ``output_root`` may sit inside one)."""
        for entry in self.inputs:
            if path.parent == entry.path:
                return entry
        return next((e for e in self.inputs if same_place(e.path, path.parent)), None)

    def input_summary(self, entry: InputConfig, *, patterns: bool = True) -> str:
        """``entry``'s effective rules in words, for ``flib check-config`` and ``doctor``.

        ``patterns=False`` gives counts instead of the patterns (they may hold names).
        """

        def globs(values: list[str]) -> str:
            if not patterns:
                return f"{len(values)} pattern(s)"
            return ", ".join(values)

        parts = []
        if entry.only is not None:
            parts.append(f"only {globs(entry.only)}")
        ignore = [*self.ignore, *entry.ignore]
        if ignore:
            extra = f" ({len(entry.ignore)} of them its own)" if entry.ignore else ""
            parts.append(f"ignore {globs(ignore)}{extra}")
        if entry.min_age_days:
            parts.append(f"min_age_days {entry.min_age_days}")
        parts.append(f"folders: {self.folders_for(entry)}")
        parts.append(f"duplicates: {self.duplicates_for(entry)}")
        if entry.category is not None:
            category = self.category(entry.category)
            layout = entry.layout or (category.layout if category else None)
            rule = f"fixed category {entry.category} (no AI)"
            rule += f", layout {layout}" if layout else ""
            rule += ", renamed by naming.pattern" if entry.rename else ", name kept"
            parts.append(rule)
        else:
            parts.append("classified by the AI")
        return "; ".join(parts)

    def folders_for(self, entry: InputConfig) -> Literal["as_unit", "ignore"]:
        return entry.folders or self.folders

    def duplicates_for(self, entry: InputConfig | None) -> Literal["move", "leave"]:
        if entry is None or entry.duplicates is None:
            return self.duplicates
        return entry.duplicates

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
