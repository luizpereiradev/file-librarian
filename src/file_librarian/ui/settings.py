"""The Settings page (#117): the essentials, changed from the interface.

What a person who does not open Terminal needs: when flib organizes (the
launchd schedule, as ``flib schedule``), who classifies (a fixed choice among
the providers this configuration can use without a new address), what is sent
or read (vision, text recognition, search by meaning), the input folders
(Downloads and the Desktop, from a fixed list), duplicates, categories (with
hints, layouts and names), the cost caps, the reminders and the language.
Everything else stays in the file; the page says where it is and how to
check it.

How a change is made:

- ``GET /settings`` only reads: the file, the index (read-only) and launchd's
  state. Each section is a form that carries a *pin*, a digest of the file's
  bytes as the page read them.
- The form posts through the server's route table (cookie, exact origin,
  CSRF) and is answered with a redirect back to the page (``?n=<id>``), so
  reload, Back and Forward never change anything again. What happened, or why
  nothing did, is shown inside the section that was sent, where the browser
  lands, with what was typed kept on a refusal.
- Under the instance lock (a run in progress: said, and nothing done) the
  file is read again; if its pin changed (edited by hand, another tab,
  ``flib``), nothing is written and the page shows it as it is now.
- The change is made to the text (``config_edit``: only the keys asked for,
  comments and order kept), and the new text is validated by the same parser
  the command line uses (``config.parse_config``) **before** anything is
  written; a value it refuses is reported with its field and why. Then a
  timestamped backup of the old file, and an atomic write (mode 0600).
  "Restore the previous settings" puts the latest backup back, after a
  confirmation page.
- A field the user did not change is left alone: a value is compared with
  what the page showed after the same clean-up a browser and this module give
  it (line breaks, spaces, Unicode form), and a hint a text field cannot hold
  is shown, not offered for editing.
- No value from the page is used as a path, an address or a command: folders
  come from a fixed list, checked again on the server (they exist, are
  folders and the user's own, are not hidden, a system folder, the home
  folder itself or the library); providers come from what the file already
  allows; categories are checked by the configuration's own rules. A command
  the page suggests is quoted for the shell and names this configuration
  file when it is not the default one.
- No secret is ever on a page: an API key is "set" or "not set" with where it
  is found (the Keychain is asked whether there is one, never for it), and
  web addresses show their host only.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import shlex
import stat
import subprocess
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from file_librarian import config_edit, credentials, i18n, menubar, notifier, scheduler
from file_librarian.config import (
    Category,
    Config,
    ConfigError,
    InputConfig,
    ProviderConfig,
    ScheduleConfig,
    default_config_path,
    is_loopback_url,
    is_within,
    parse_config,
    same_place,
)
from file_librarian.config_edit import Append, Delete, Edit, EditError, Set
from file_librarian.fields import KINDS
from file_librarian.i18n import Msg, formats, t
from file_librarian.locales import LOCALES
from file_librarian.lock import AlreadyRunningError, instance_lock
from file_librarian.onboarding import (
    DEFAULT_MODELS,
    DESKTOP_MIN_AGE_DAYS,
    DESKTOP_ONLY,
    LOCAL_MODEL,
    display_path,
)
from file_librarian.ui.actions import BUSY, Item, Malformed, Outcome, Refused, a_pin, one, stop
from file_librarian.ui.markup import Markup, csrf_field, el, join, layout, render
from file_librarian.ui.views import (
    BadRequest,
    Page,
    Unreadable,
    kind_label,
    query_params,
    read_index,
    sentence,
    shown,
)

log = logging.getLogger(__name__)

MAX_HINT = 300
"""Characters a category's hint may have (it goes to the model with every file)."""
MAX_NAME = 64
MAX_AMOUNT = 1000.0
"""The highest cost cap the page takes, in US$; more goes in the file."""
LONG_RULES = 160
"""An input folder's rules longer than this (in words) fold under a ``<details>``."""
LAYOUTS: tuple[tuple[str, str | None, str], ...] = (
    ("none", None, t("settings.layout.none")),
    ("year", "{year}", t("settings.layout.year")),
    ("year-month", "{year}/{month}", t("settings.layout.year_month")),
    ("issuer", "{issuer}", t("settings.layout.issuer")),
    ("issuer-year", "{issuer}/{year}", t("settings.layout.issuer_year")),
)
"""The sub-folder layouts the page offers (``config._layout`` checks any other)."""
SCHEDULE_MODES = ("off", "daily", "watch", "both")
SECTION_TITLES = tuple(
    (key, t(f"settings.section.{key}"))
    for key in (
        "schedule",
        "provider",
        "reading",
        "inputs",
        "categories",
        "costs",
        "reminders",
        "language",
        "file",
    )
)

STALE = t("settings.stale")
NO_PATH = t("settings.no_path")
GONE = t("settings.gone")
NOTHING = t("settings.nothing")
OTHER_DATABASE = t("settings.other_database")

LABELS = {
    key: t(f"settings.label.{key}")
    for key in (
        "language",
        "inputs",
        "output_root",
        "review_folder",
        "duplicates_folder",
        "duplicates",
        "provider.name",
        "provider.model",
        "vision.enabled",
        "ocr.enabled",
        "ocr.languages",
        "search.semantic.enabled",
        "limits.max_cost_usd_per_run",
        "limits.max_cost_usd_per_day",
        "summary.notify",
        "due.remind",
        "due.remind_days_before",
        "due.kinds",
        "schedule.mode",
        "schedule.time",
        "categories",
    )
}
FIELDS = {
    key: t(f"settings.field.{key}")
    for key in ("categories.name", "categories.hint", "categories.layout", "inputs.path")
}
"""Fields the parser names with a list index (``categories.5.name``)."""


# --- text: what the page wrote, and what the user did ----------------------------------

_TICK = "\x1f"
"""A backtick in the user's own text, kept apart from the page's: only the page's
`backticks` make a command (``<code>``). Never in a value: control characters are
refused in names and hints, and YAML has none."""


def lit(value: object) -> str:
    """The user's text (a name, a hint, a path) for a message: as text, never a command."""
    return shown(str(value)).replace("`", _TICK)


def prose(text: str) -> Markup:
    """Text where the page's `backticks` mark a command; the user's are text. A message
    is rendered: only its translation's backticks count, never a parameter's."""
    if isinstance(text, Msg):
        return render(text)
    parts = text.split("`")
    if len(parts) % 2 == 0:
        parts = [text.replace("`", _TICK)]
    return join(
        *(
            el("code", part.replace(_TICK, "`")) if i % 2 else part.replace(_TICK, "`")
            for i, part in enumerate(parts)
        )
    )


def message(text: str, *, error: bool = False, **attrs: object) -> Markup:
    return el(
        "p",
        prose(text) if isinstance(text, Msg) else prose(sentence(text)),
        class_="notice error" if error else "notice",
        role="status",
        **attrs,
    )


def command(config_path: Path | None, *args: str) -> Msg:
    """A ``flib`` command line the user can paste: every word quoted for the shell, and
    ``-c <file>`` when this is not the default configuration file. A message that renders
    as one ``<code>``, whatever the words hold (a name with a backtick stays text)."""
    words = ["flib", args[0]]
    if config_path is not None and config_path != default_config_path():
        words += ["-c", str(config_path)]
    words += args[1:]
    return t("settings.command", text=shown(" ".join(shlex.quote(w) for w in words)))


def listed(parts: list[Msg | str], msg_id: str = "settings.list.semicolon") -> Msg | str:
    """Parts one after another (``a; b; c``), as a message that renders in any language."""
    result = parts[-1]
    for part in reversed(parts[:-1]):
        result = t(msg_id, first=part, rest=result)
    return result


def _value(value: object) -> Msg | str:
    """A value for a message: a message as it is, anything else as text (``shown``)."""
    return value if isinstance(value, Msg) else shown(str(value))


def _exc_text(exc: BaseException) -> Msg | str:
    """An exception's words: a message when it carries one."""
    first = exc.args[0] if exc.args else None
    return first if isinstance(first, Msg) else str(exc)


def browser_text(value: str) -> str:
    """``value`` as it comes back from a one-line text field: a browser drops its line
    breaks, and ``field`` trims the ends and takes the composed Unicode form."""
    return unicodedata.normalize("NFC", value.replace("\r", "").replace("\n", "").strip())


def editable_hint(hint: str | None) -> bool:
    """A hint a text field can show and send back unchanged."""
    if hint is None:
        return True
    return len(hint) <= MAX_HINT and not any(unicodedata.category(c) in ("Cc", "Cf") for c in hint)


# --- the file --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The configuration file as read now."""

    path: Path
    data: bytes
    text: str
    raw: dict[str, Any] | None
    config: Config | None
    problem: str | None
    """Why the file cannot be changed from here, for the page (no secret)."""

    @property
    def pin(self) -> str:
        return config_edit.pin(self.data)


def read(path: Path) -> Snapshot:
    """The file at ``path`` and what the configuration's parser makes of it."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Snapshot(path, b"", "", None, None, t("settings.file.missing"))
    except OSError:
        return Snapshot(path, b"", "", None, None, t("settings.file.unreadable"))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Snapshot(path, data, "", None, None, t("settings.file.not_utf8"))
    try:
        raw = config_edit.load(text)
    except EditError as exc:  # YAML's own words, as the command line shows them
        return Snapshot(path, data, text, None, None, sentence_of(str(exc)))
    try:
        config = parse_config(raw)
    except ConfigError as exc:
        return Snapshot(path, data, text, raw, None, invalid(exc))
    return Snapshot(path, data, text, raw, config, None)


def sentence_of(text: str) -> str:
    return stop(text[:1].upper() + text[1:])


def invalid(exc: ConfigError) -> Msg | str:
    """The parser's refusal: each field and why, in one paragraph (as text). The field's
    name is the page's (translated); why is the parser's own words, as the command line
    prints them (pydantic's messages are English only)."""
    lines = str(exc).splitlines()[1:] or [str(exc)]
    parts: list[Msg | str] = []
    for line in lines:
        where, _, why = line.strip().partition(": ")
        why = shown(why.removeprefix("Value error, "))
        if where == "(root)":
            parts.append(why)
            continue
        label = LABELS.get(where) or FIELDS.get(re.sub(r"\.\d+(?=\.|$)", "", where))
        where = shown(where)
        if label:
            parts.append(
                t("settings.invalid.labelled", label=label, where=where, why=why)
                if why
                else t("settings.invalid.labelled_only", label=label, where=where)
            )
        else:
            parts.append(t("settings.invalid.where", where=where, why=why) if why else where)
    return listed(parts)


@contextmanager
def locked(config: Config) -> Iterator[None]:
    """The instance lock, as ``flib run`` takes it: a run in progress is said."""
    try:
        with instance_lock(config.database):
            yield
    except AlreadyRunningError as exc:
        raise Refused(BUSY) from exc


def _current(path: Path, pin: str) -> tuple[Snapshot, dict[str, Any], Config]:
    """The file again, under the lock: refused unless it is as the page showed it."""
    snap = read(path)
    if snap.pin != pin:
        raise Refused(STALE)
    if snap.config is None or snap.raw is None:
        raise Refused(t("settings.file.cannot_change", problem=_value(snap.problem)))
    return snap, snap.raw, snap.config


@dataclass(frozen=True, slots=True)
class Saved:
    config: Config
    changes: tuple[Item, ...]


def _write(path: Path, old: bytes, text: str) -> None:
    try:
        config_edit.write(path, old, text)
    except config_edit.Changed as exc:
        raise Refused(STALE) from exc
    except EditError as exc:
        raise Refused(t("settings.file.not_saved", error=shown(str(exc)))) from exc
    except OSError as exc:
        raise Refused(t("settings.file.write_failed")) from exc


def commit(snap: Snapshot, old: dict[str, Any], edits: list[Edit], running: Config) -> Saved:
    """Make ``edits`` to the file: in the text, validated, backed up, written."""
    try:
        text = config_edit.edit_text(snap.text, edits)
    except EditError as exc:
        raise Refused(t("settings.file.edit_failed", error=shown(str(exc)))) from exc
    raw = config_edit.load(text)
    try:
        config = parse_config(raw)
    except ConfigError as exc:
        raise Refused(t("settings.file.invalid", problem=invalid(exc))) from exc
    if config.database != running.database:
        raise Refused(OTHER_DATABASE)
    _write(snap.path, snap.data, text)
    return Saved(config, tuple(diff(old, raw, snap.config, config)))


# --- what changed ----------------------------------------------------------------------


_LISTS = {"categories": "category", "inputs": "input"}
"""Lists of entries told apart field by field, each named by its name or path."""


def _flat(data: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(data, dict) and prefix.rstrip(".") not in _LISTS:
        found: dict[str, Any] = {}
        for key, value in data.items():
            found |= _flat(value, f"{prefix}{key}.")
        return found
    return {prefix.rstrip("."): data}


_ABSENT = object()


def describe(key: str, value: Any) -> Msg | str:
    """A value as the page shows it (as text): web addresses by their host only."""
    if value is _ABSENT:
        return t("settings.value.default")
    if key.endswith("base_url") and isinstance(value, str):
        return host_of(value)
    if value is None:
        return t("settings.value.none")
    if isinstance(value, bool):
        return t("settings.value.on") if value else t("settings.value.off")
    if isinstance(value, list):
        return ", ".join(_ident(v) for v in value) or t("settings.value.none")
    if isinstance(value, dict):
        return "…"
    if isinstance(value, float):  # a cost cap: with the page's decimal separator
        return formats.amount_input(value)
    if key == "language" and value in i18n.NAMES:  # by its own name: English, Português
        return i18n.NAMES[value]
    return str(value)


def _ident(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("name") or entry.get("path") or "…")
    return str(entry)


def _fields_of(entry: Any) -> dict[str, Any]:
    return entry if isinstance(entry, dict) else {"path": entry}


def _entries(key: str, old: Any, new: Any) -> list[Item]:
    """What changed in a list of categories or input folders, one line per field: a
    renamed category, a hint, an entry added or removed (never the whole list)."""
    noun = _LISTS[key]

    def entry(name: str, field: str | None = None) -> Msg:
        if field is None:
            return t(f"settings.entry.{noun}", name=shown(name))
        return t(f"settings.entry.{noun}_field", name=shown(name), field=shown(field))

    if not isinstance(new, list):
        return [Item(LABELS[key], t("settings.change.language_default"))]
    if not isinstance(old, list):
        return [Item(LABELS[key], t("settings.change.written"))]
    items: list[Item] = []
    if len(old) == len(new):
        pairs = list(zip(old, new, strict=True))
    else:
        before = {_ident(e): e for e in old}
        after = {_ident(e): e for e in new}
        pairs = [(before[n], after[n]) for n in after if n in before]
        items += [Item(entry(n), t("settings.change.added")) for n in after if n not in before]
        items += [Item(entry(n), t("settings.change.removed")) for n in before if n not in after]
    for a, b in pairs:
        name = _ident(a)
        if _ident(b) != name:
            items.append(Item(entry(name), t("settings.change.renamed", name=shown(_ident(b)))))
        fa, fb = _fields_of(a), _fields_of(b)
        for field in dict.fromkeys([*fb, *fa]):
            if field in ("name", "path"):
                continue
            va, vb = fa.get(field, _ABSENT), fb.get(field, _ABSENT)
            if va != vb:
                text = t(
                    "settings.change.value",
                    old=_value(describe(field, va)),
                    new=_value(describe(field, vb)),
                )
                items.append(Item(entry(_ident(b), field), text))
    return items


def implicit(config: Config | None) -> dict[str, Any]:
    """The lists a file may leave to their defaults (the language's categories, the
    Downloads folder), as the file would hold them: so a list written out for the first
    time is compared with what it was, not with nothing."""
    if config is None:
        return {}
    return {
        "categories": [category_entry(c) for c in config.categories],
        "inputs": [display_path(p) for p in config.input_paths],
    }


def diff(
    old: dict[str, Any],
    new: dict[str, Any],
    old_config: Config | None = None,
    new_config: Config | None = None,
) -> list[Item]:
    """Each setting that differs, as ``label: old → new`` (all of it text)."""
    before = implicit(old_config) | _flat(old)
    after = implicit(new_config) | _flat(new)
    items = []
    for key in dict.fromkeys([*after, *before]):
        a, b = before.get(key, _ABSENT), after.get(key, _ABSENT)
        if a == b:
            continue
        if key in _LISTS:
            items += _entries(key, a, b)
        else:
            text = t(
                "settings.change.value", old=_value(describe(key, a)), new=_value(describe(key, b))
            )
            items.append(Item(LABELS.get(key) or shown(key), text))
    return items


def host_of(url: str) -> Msg | str:
    """Where a web address goes, and nothing else of it: ``api.example.com``."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return t("settings.address_in_file")
    return f"{host}{port}"


# --- what the page offers --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderChoice:
    name: str
    model: str
    label: str


def _provider_label(name: str, config: ProviderConfig) -> Msg:
    if name == "anthropic":
        return t("settings.provider.anthropic")
    if name == "claude-code":
        return t("settings.provider.claude_code")
    if name == "ollama":
        return t("settings.provider.ollama")
    if name == "openai-compat":
        return t("settings.provider.openai_compat", host=host_of(config.base_url or ""))
    return t("settings.provider.fake")


def provider_choices(config: Config) -> list[ProviderChoice]:
    """The providers and models this file can switch to without a new address: never an
    address typed on the page. Ollama only on this Mac. An OpenAI-compatible server only
    as the current choice: the page cannot know which models another server has."""
    p = config.provider
    pairs = [(p.name, p.model)]
    pairs += [
        ("anthropic", DEFAULT_MODELS["anthropic"]),
        ("claude-code", DEFAULT_MODELS["claude-code"]),
    ]
    if p.base_url is None or is_loopback_url(p.base_url):
        pairs += [("ollama", LOCAL_MODEL), ("ollama", DEFAULT_MODELS["ollama"])]
    return [
        ProviderChoice(
            name,
            model,
            t("settings.provider.choice", provider=_provider_label(name, p), model=model),
        )
        for name, model in dict.fromkeys(pairs)
    ]


def leaves(config: Config, name: str | None = None) -> Msg:
    """What leaves the Mac to classify a file, in one sentence."""
    p = config.provider
    name = name or p.name
    chars = p.max_chars_per_file
    if name == "anthropic":
        return t("settings.leaves.anthropic", chars=chars)
    if name == "claude-code":
        return t("settings.leaves.claude_code", chars=chars)
    if name in ("ollama", "openai-compat"):
        url = p.base_url or ("http://localhost:11434/v1" if name == "ollama" else "")
        if is_loopback_url(url):
            return t("settings.leaves.local")
        return t("settings.leaves.remote", chars=chars, host=host_of(url))
    return t("settings.leaves.none")


KEY_LABELS = {
    "anthropic": t("settings.key.anthropic"),
    "openai-compat": t("settings.key.openai_compat"),
    "embeddings": t("settings.key.embeddings"),
}


def key_state(config: ProviderConfig) -> Msg | None:
    """Whether the provider's API key is found, and where: never the key itself (the
    Keychain is only asked whether it holds one)."""
    spec = credentials.key_spec(config)
    if spec is None:
        return None
    source = credentials.key_source(config, spec)
    label = KEY_LABELS.get(spec.provider) or spec.label
    if source == "none":
        return t("settings.key.not_set", label=label)
    where = source if source in ("environment", "keychain") else "env_file"
    return t("settings.key.set", label=label, where=t(f"settings.key.where.{where}"))


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    path: Path
    label: Msg
    note: Msg
    entry: Any
    """What goes in ``inputs``: the path as ``flib init`` writes it, with its rules."""


def candidates() -> list[Candidate]:
    """The input folders the page can add: a fixed list, never a typed path."""
    home = Path.home()
    downloads, desktop = home / "Downloads", home / "Desktop"
    return [
        Candidate(
            "downloads",
            downloads,
            t("settings.folder.downloads"),
            t("settings.folder.downloads_note"),
            display_path(downloads),
        ),
        Candidate(
            "desktop",
            desktop,
            t("settings.folder.desktop"),
            t("settings.folder.desktop_note", days=DESKTOP_MIN_AGE_DAYS),
            {
                "path": display_path(desktop),
                "only": list(DESKTOP_ONLY),
                "min_age_days": DESKTOP_MIN_AGE_DAYS,
                "folders": "ignore",
            },
        ),
    ]


ALREADY = "settings.why.already"
"""Why a folder cannot be added that is no problem: it is an input folder already."""


def folder_problem(path: Path, config: Config) -> Msg | None:
    """Why ``path`` cannot be an input folder, or ``None``. Checked on the server, at the
    moment of the change: a folder of the user's own under the home folder, really there,
    not a link, not hidden, not ``~/Library``, not the home folder or ``/``, not the
    library or inside it, not already an input."""
    home = Path.home()
    text = str(path)
    if not path.is_absolute() or os.path.normpath(text) != text:
        return t("settings.why.plain")
    try:
        info = os.lstat(path)
    except OSError:
        return t("settings.why.missing")
    if stat.S_ISLNK(info.st_mode):
        return t("settings.why.link")
    if not stat.S_ISDIR(info.st_mode):
        return t("settings.why.not_folder")
    if same_place(path, Path(path.anchor)) or same_place(path, home):
        return t("settings.why.broad")
    if not path.is_relative_to(home) or not is_within(path, home):
        return t("settings.why.outside")
    if any(part.startswith(".") for part in path.relative_to(home).parts):
        return t("settings.why.hidden")
    if is_within(path, home / "Library"):
        return t("settings.why.system")
    if info.st_uid != os.getuid():
        return t("settings.why.not_yours")
    if is_within(path, config.output_root):
        return t("settings.why.library")
    if any(same_place(path, p) for p in config.input_paths):
        return t(ALREADY)
    return None


def other_schedule(path: Path) -> Path | None:
    """The configuration file the installed agents use, when it is not ``path``."""
    try:
        scheduled = scheduler.scheduled_config()
    except (OSError, ValueError):
        return None
    if scheduled is None or scheduled == path.resolve():
        return None
    return scheduled


# --- the page --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _View:
    """What one rendering needs: the file, the token, and the outcome of the last
    change (shown in the section it came from, with what was typed on a refusal)."""

    snap: Snapshot
    csrf: str
    outcome: Outcome | None

    def notice(self, key: str) -> Markup | None:
        if self.outcome is None or self.outcome.anchor != key:
            return None
        return outcome_notice(self.outcome, False, anchor=key)

    def typed(self, key: str, **match: str) -> dict[str, str]:
        """The values a refused form of section ``key`` had (``match``: which one)."""
        outcome = self.outcome
        if outcome is None or not outcome.error or outcome.anchor != key:
            return {}
        values = dict(outcome.form)
        if any(values.get(k) != v for k, v in match.items()):
            return {}
        return values


def settings_page(path: Path | None, csrf: str, outcome: Outcome | None, *, gone: bool) -> Page:
    parts: list[Markup | None] = [
        el("h1", t("settings.title")),
        el("p", prose(t("settings.lead")), class_="lead"),
    ]
    if gone or (outcome is not None and outcome.anchor is None):
        parts.append(outcome_notice(outcome, gone))
    if path is None:
        parts.append(message(NO_PATH))
        return HTTPStatus.OK, layout(t("settings.title"), "settings", join(parts), csrf)
    snap = read(path)
    view = _View(snap, csrf, outcome)
    if snap.config is None:
        if outcome is not None and outcome.anchor is not None:  # no section to show it in
            parts.append(outcome_notice(outcome, False))
        parts.append(message(t("settings.unusable", problem=_value(snap.problem)), error=True))
        parts.append(_file_section(view))
        return HTTPStatus.OK, layout(t("settings.title"), "settings", join(parts), csrf)
    config = snap.config
    parts += [
        el(
            "nav",
            *(el("a", title, href=f"#s-{key}", class_="chip") for key, title in SECTION_TITLES),
            class_="chips toc",
            aria_label=t("settings.toc"),
        ),
        _schedule_section(view, config),
        _provider_section(view, config),
        _reading_section(view, config),
        _inputs_section(view, config),
        _categories_section(view, config),
        _costs_section(view, config),
        _reminders_section(view, config),
        _language_section(view, config),
        _file_section(view),
    ]
    return HTTPStatus.OK, layout(t("settings.title"), "settings", join(parts), csrf)


def outcome_notice(
    outcome: Outcome | None, gone: bool, *, anchor: str | None = None
) -> Markup | None:
    if gone:
        return message(GONE)
    if outcome is None:
        return None
    items = [
        el("li", el("strong", _text_of(item.name)), ": ", prose(item.text))
        for item in outcome.items
    ]
    restore = None
    if outcome.restore:
        restore = el(
            "p",
            el("a", t("settings.restore_link"), href="/settings/restore"),
            class_="note",
        )
    return el(
        "div",
        message(outcome.text, error=outcome.error),
        el("ul", *items, class_="moves", aria_label=t("settings.what_changed")) if items else None,
        restore,
        class_="outcome",
        id=None if anchor is None else f"o-{anchor}",
        tabindex=None if anchor is None else "-1",
    )


def _text_of(label: str) -> str:
    if isinstance(label, Msg):
        return label
    return label.replace(_TICK, "`")


def _section(view: _View, key: str, *body: Markup | None) -> Markup:
    title = dict(SECTION_TITLES)[key]
    return el(
        "section",
        el("h2", title, id=f"h-{key}"),
        view.notice(key),
        *body,
        class_="settings",
        id=f"s-{key}",
        aria_labelledby=f"h-{key}",
    )


def _form(view: _View, section: str, *children: object, action: str = "/settings/save") -> Markup:
    return el(
        "form",
        csrf_field(view.csrf),
        el("input", type="hidden", name="section", value=section),
        el("input", type="hidden", name="pin", value=view.snap.pin),
        *children,
        method="post",
        action=action,
        class_="settings-form",
    )


def _check(name: str, label: str, checked: bool, help_text: str | None = None) -> Markup:
    return el(
        "label",
        el("input", type="checkbox", name=name, value="1", checked=checked),
        " ",
        el("span", label),
        el("span", prose(help_text), class_="help") if help_text else None,
        class_="check",
    )


def _field(label: str, control: Markup) -> Markup:
    return el("label", el("span", label), control, class_="field")


def _help(text: str) -> Markup:
    return el("p", prose(text), class_="note")


def _save(label: str | None = None) -> Markup:
    return el("div", el("button", label or t("settings.save"), type="submit"), class_="buttons")


def _schedule_section(view: _View, config: Config) -> Markup:
    notes: list[Markup | None] = []
    typed = view.typed("schedule")
    try:
        state = scheduler.status()
    except scheduler.ScheduleError:
        state = None
        notes.append(message(t("settings.schedule.unreadable"), error=True))
    current = "off"
    at = config.schedule.time
    takeover = None
    if state is not None and state.installed:
        current = state.mode or "daily"
        at = state.time or at
        if not state.loaded:
            notes.append(message(t("settings.schedule.not_loaded"), error=True))
        other = other_schedule(view.snap.path)
        if other is not None:
            notes.append(
                message(t("settings.schedule.other", other=shown(display_path(other))), error=True)
            )
            takeover = _check("takeover", t("settings.schedule.takeover"), False)
        stale = scheduler.stale_watch_message(state, config.input_paths)
        if stale and other is None:
            notes.append(message(t("settings.schedule.stale_watch")))
    current = typed.get("mode", current)
    at = typed.get("time", at)
    words = {mode: t(f"settings.schedule.mode.{mode}") for mode in SCHEDULE_MODES}
    radios = [
        el(
            "label",
            el("input", type="radio", name="mode", value=mode, checked=mode == current),
            " ",
            words[mode],
            class_="check",
        )
        for mode in SCHEDULE_MODES
    ]
    form = _form(
        view,
        "schedule",
        el("fieldset", el("legend", t("settings.schedule.legend")), *radios, class_="choices"),
        _field(
            t("settings.schedule.time"),
            el("input", type="time", name="time", value=at, required=True, step=60),
        ),
        takeover,
        _save(t("settings.schedule.save")),
        action="/settings/schedule",
    )
    return _section(
        view,
        "schedule",
        _help(t("settings.schedule.help")),
        *notes,
        form,
        _help(menu_bar_note()),
    )


def menu_bar_note() -> Msg:
    """Whether flib is in the menu bar (#116), and the command that changes it: a line,
    not a toggle (installing it builds an app and a LaunchAgent; that stays a command)."""
    if menubar.installed():
        return t("settings.menu.shown")
    return t("settings.menu.hidden")


def _provider_section(view: _View, config: Config) -> Markup:
    choices = provider_choices(config)
    options = [el("option", c.label, value=str(i), selected=i == 0) for i, c in enumerate(choices)]
    key = key_state(config.provider)
    return _section(
        view,
        "provider",
        _help(leaves(config)),
        _help(key) if key else None,
        _form(
            view,
            "provider",
            _field(t("settings.provider.field"), el("select", *options, name="choice")),
            el(
                "ul",
                *(
                    el("li", el("strong", c.label), ": ", leaves(config, c.name))
                    for c in choices[1:]
                ),
                class_="moves",
                aria_label=t("settings.provider.each"),
            ),
            _save(),
        ),
        _help(t("settings.provider.help")),
    )


def _reading_section(view: _View, config: Config) -> Markup:
    semantic = config.search.semantic
    where = (
        t("settings.reading.this_mac")
        if is_loopback_url(semantic.base_url)
        else host_of(semantic.base_url)
    )
    vision_help = t(
        "settings.reading.vision_help_claude"
        if config.provider.name == "claude-code"
        else "settings.reading.vision_help"
    )
    return _section(
        view,
        "reading",
        _form(
            view,
            "reading",
            _check("vision", t("settings.reading.vision"), config.vision.enabled, vision_help),
            _check(
                "ocr",
                t("settings.reading.ocr"),
                config.ocr.enabled,
                t("settings.reading.ocr_help"),
            ),
            _check(
                "semantic",
                t("settings.reading.semantic"),
                semantic.enabled,
                t("settings.reading.semantic_help", where=where),
            ),
            _save(),
        ),
    )


def rules_text(config: Config, entry: InputConfig) -> Msg | str:
    """An input folder's effective rules in words, in the page's language: what
    ``Config.input_summary`` says for ``flib check-config`` (the same words in English),
    with the patterns as text."""

    def globs(values: list[str]) -> str:
        return shown(", ".join(values))

    parts: list[Msg | str] = []
    if entry.only is not None:
        parts.append(t("settings.rules.only", patterns=globs(entry.only)))
    ignore = [*config.ignore, *entry.ignore]
    if ignore:
        if entry.ignore:
            own = len(entry.ignore)
            parts.append(t("settings.rules.ignore_own", patterns=globs(ignore), own=own))
        else:
            parts.append(t("settings.rules.ignore", patterns=globs(ignore)))
    if entry.min_age_days:
        parts.append(t("settings.rules.min_age", days=entry.min_age_days))
    parts.append(t(f"settings.rules.folders.{config.folders_for(entry)}"))
    parts.append(t(f"settings.rules.duplicates.{config.duplicates_for(entry)}"))
    if entry.category is not None:
        category = config.category(entry.category)
        layout_value = entry.layout or (category.layout if category else None)
        if layout_value:
            msg_id = (
                "settings.rules.fixed_layout_renamed"
                if entry.rename
                else "settings.rules.fixed_layout_kept"
            )
            parts.append(t(msg_id, category=shown(entry.category), layout=shown(layout_value)))
        else:
            msg_id = "settings.rules.fixed_renamed" if entry.rename else "settings.rules.fixed_kept"
            parts.append(t(msg_id, category=shown(entry.category)))
    else:
        parts.append(t("settings.rules.ai"))
    return listed(parts)


def _rules(config: Config, entry: InputConfig) -> Markup:
    """An input folder's rules in words; long ones (hundreds of patterns) fold away."""
    text = rules_text(config, entry)
    if len(str(text)) <= LONG_RULES:
        return el("div", text, class_="where")
    return el(
        "details",
        el("summary", t("settings.rules.summary")),
        el("div", text, class_="where"),
        class_="rules",
    )


def _inputs_section(view: _View, config: Config) -> Markup:
    rows = []
    for i, entry in enumerate(config.inputs):
        name = shown(display_path(entry.path))
        remove = None
        if len(config.inputs) > 1:
            remove = el(
                "a",
                t("settings.inputs.stop"),
                href=f"/settings/input-remove?index={i}",
                class_="small-link",
                aria_label=t("settings.inputs.stop_named", name=name),
            )
        rows.append(
            el(
                "li",
                el("bdi", name, class_="name", dir="ltr"),
                _rules(config, entry),
                remove,
                class_="hit",
            )
        )
    adds = []
    for candidate in candidates():
        why = folder_problem(candidate.path, config)
        if why is not None and why.msg_id == ALREADY:
            continue
        if why is not None:
            adds.append(
                el(
                    "li",
                    t("settings.inputs.cannot_add", folder=candidate.label, why=why),
                    class_="note",
                )
            )
            continue
        adds.append(
            el(
                "li",
                _form(
                    view,
                    "input-add",
                    el("input", type="hidden", name="folder", value=candidate.key),
                    el(
                        "button",
                        t("settings.inputs.add", folder=candidate.label),
                        type="submit",
                        class_="small",
                    ),
                    " ",
                    el("span", prose(candidate.note), class_="note"),
                ),
            )
        )
    duplicates = [
        el(
            "label",
            el(
                "input",
                type="radio",
                name="duplicates",
                value=value,
                checked=config.duplicates == value,
            ),
            " ",
            text,
            class_="check",
        )
        for value, text in (
            ("move", t("settings.inputs.dup_move", folder=shown(config.duplicates_folder))),
            ("leave", t("settings.inputs.dup_leave")),
        )
    ]
    return _section(
        view,
        "inputs",
        _help(t("settings.inputs.help")),
        el("ul", *rows, class_="hits", aria_label=LABELS["inputs"]),
        el("ul", *adds, class_="plain") if adds else None,
        el(
            "dl",
            el("dt", t("settings.inputs.library")),
            el("dd", el("bdi", shown(display_path(config.output_root)), dir="ltr")),
            el("dt", LABELS["review_folder"]),
            el("dd", el("bdi", shown(config.review_folder), dir="ltr")),
            class_="facts",
        ),
        _help(t("settings.inputs.help_file")),
        _form(
            view,
            "duplicates",
            el(
                "fieldset",
                el("legend", t("settings.inputs.dup_legend")),
                *duplicates,
                class_="choices",
            ),
            _save(),
        ),
    )


def _layout_options(current: str | None, chosen: str | None = None) -> list[Markup]:
    keys = {value: key for key, value, _ in LAYOUTS}
    picked = chosen if chosen is not None else keys.get(current, "keep")
    options = [el("option", text, value=key, selected=key == picked) for key, _, text in LAYOUTS]
    if current is not None and current not in keys:
        options.append(
            el(
                "option",
                t("settings.layout.as_file", layout=shown(current)),
                value="keep",
                selected=picked == "keep",
            )
        )
    return options


def _text_input(name: str, value: str, limit: int, *, required: bool = False) -> Markup:
    return el(
        "input",
        type="text",
        name=name,
        value=value,
        maxlength=limit,
        required=required,
        autocomplete="off",
        spellcheck="false",
    )


def _hint_field(hint: str | None, typed: dict[str, str]) -> Markup:
    """The hint's field, or, for a hint a text field cannot hold, the hint as text."""
    if not editable_hint(hint):
        return el(
            "div",
            el("span", t("settings.hint.label"), class_="label"),
            el("p", shown(hint or ""), class_="where hint"),
            _help(t("settings.hint.kept")),
            class_="field",
        )
    return join(
        el("input", type="hidden", name="hint_field", value="1"),
        _field(
            t("settings.hint.field"),
            _text_input("hint", typed.get("hint", hint or ""), MAX_HINT),
        ),
    )


def _categories_section(view: _View, config: Config) -> Markup:
    items = []
    for category in config.categories:
        name = shown(category.name)
        typed = view.typed("categories", section="category-edit", category=category.name)
        facts: list[Msg | str] = []
        if category.layout:
            facts.append(t("settings.category.subfolders", layout=shown(category.layout)))
        if not category.rename:
            facts.append(t("settings.category.names_kept"))
        edit = el(
            "details",
            el(
                "summary",
                t("settings.category.change"),
                el("span", " ", name, class_="sr-only"),
            ),
            _form(
                view,
                "category-edit",
                el("input", type="hidden", name="category", value=category.name),
                _field(
                    t("settings.category.name"),
                    _text_input("name", typed.get("name", category.name), MAX_NAME, required=True),
                ),
                _hint_field(category.hint, typed),
                _field(
                    t("settings.category.layout"),
                    el(
                        "select",
                        *_layout_options(category.layout, typed.get("layout")),
                        name="layout",
                    ),
                ),
                _check("rename", t("settings.category.rename"), category.rename),
                _save(),
            ),
            _form(
                view,
                "category-remove",
                el("input", type="hidden", name="category", value=category.name),
                el(
                    "button",
                    t("settings.category.remove"),
                    type="submit",
                    class_="small quiet",
                    aria_label=t("settings.category.remove_named", name=name),
                ),
            ),
            class_="other",
            open=bool(typed),
        )
        items.append(
            el(
                "li",
                el("bdi", name, class_="name", dir="ltr"),
                el(
                    "div",
                    shown(category.hint) if category.hint else t("settings.category.no_hint"),
                    class_="where",
                ),
                el("div", listed(facts, "settings.list.comma"), class_="note") if facts else None,
                edit,
                class_="hit",
            )
        )
    typed = view.typed("categories", section="category-add")
    add = el(
        "details",
        el("summary", t("settings.category.add")),
        _form(
            view,
            "category-add",
            _field(
                t("settings.category.new_name"),
                _text_input("name", typed.get("name", ""), MAX_NAME, required=True),
            ),
            _field(
                t("settings.category.new_hint"),
                _text_input("hint", typed.get("hint", ""), MAX_HINT),
            ),
            _field(
                t("settings.category.layout"),
                el("select", *_layout_options(None, typed.get("layout")), name="layout"),
            ),
            _check("rename", t("settings.category.rename"), True),
            _save(t("settings.category.add_button")),
        ),
        class_="other",
        open=bool(typed),
    )
    return _section(
        view,
        "categories",
        _help(t("settings.category.help")),
        el("ul", *items, class_="hits", aria_label=LABELS["categories"]),
        add,
    )


def amount_text(value: float | None) -> str:
    """A cost cap as the page shows it: every digit the file has, never an exponent, with
    the language's decimal separator (``formats.amount_input``)."""
    return formats.amount_input(value)


def as_shown(text: str, value: float | None) -> bool:
    """Whether ``text`` is the cap as a page showed it, in either language's form: such
    a value is left alone, whatever its digits (``0.00001`` is more than the form takes)."""
    return any(text == formats.amount_input(value, lang) for lang in i18n.LANGUAGES)


def no_prices(config: Config, *, capital: bool = False) -> Msg | None:
    """``status.cost_cap_lines``'s warning in the page's words: a remote provider with no
    prices, which no cost cap can stop. ``capital``: it starts a notice."""
    if not config.provider.cost_unknown:
        return None
    files = config.limits.files_cap_per_day
    limit = (
        t("settings.costs.unlimited")
        if files is None
        else t("settings.costs.files_cap", files=files)
    )
    name = config.provider.name
    return t("settings.costs.no_prices", provider=sentence(name) if capital else name, limit=limit)


def _costs_section(view: _View, config: Config) -> Markup:
    limits = config.limits
    typed = view.typed("costs")
    explicit = "max_cost_usd_per_day" in limits.model_fields_set
    day_mode = (
        "amount"
        if explicit and limits.max_cost_usd_per_day is not None
        else ("none" if explicit else "default")
    )
    day_mode = typed.get("per_day", day_mode)
    day_modes = [
        ("default", t("settings.costs.default")),
        ("none", t("settings.costs.none")),
        ("amount", t("settings.costs.amount")),
    ]
    found = no_prices(config, capital=True)
    warning = [message(found, error=True)] if found is not None else []
    day_value = amount_text(limits.max_cost_usd_per_day) if explicit else ""
    return _section(
        view,
        "costs",
        _help(t("settings.costs.help")),
        *warning,
        _form(
            view,
            "costs",
            _field(
                t("settings.costs.per_run"),
                el(
                    "input",
                    type="text",
                    name="per_run",
                    value=typed.get("per_run", amount_text(limits.max_cost_usd_per_run)),
                    inputmode="decimal",
                    autocomplete="off",
                    maxlength=24,
                ),
            ),
            el(
                "fieldset",
                el("legend", t("settings.costs.per_day")),
                *(
                    el(
                        "label",
                        el(
                            "input",
                            type="radio",
                            name="per_day",
                            value=value,
                            checked=value == day_mode,
                        ),
                        " ",
                        text,
                        class_="check",
                    )
                    for value, text in day_modes
                ),
                el(
                    "label",
                    el("span", t("settings.costs.day_amount"), class_="sr-only"),
                    el(
                        "input",
                        type="text",
                        name="day_amount",
                        value=typed.get("day_amount", day_value),
                        inputmode="decimal",
                        autocomplete="off",
                        maxlength=24,
                    ),
                    class_="field",
                ),
                class_="choices",
            ),
            _save(),
        ),
    )


def _reminders_section(view: _View, config: Config) -> Markup:
    typed = view.typed("reminders")
    kinds = [
        el(
            "label",
            el(
                "input", type="checkbox", name="kinds", value=kind, checked=kind in config.due.kinds
            ),
            " ",
            kind_label(kind),
            class_="chip",
        )
        for kind in KINDS
    ]
    return _section(
        view,
        "reminders",
        _form(
            view,
            "reminders",
            _check(
                "notify",
                t("settings.reminders.notify"),
                config.summary.notify,
                t("settings.reminders.notify_help"),
            ),
            _check("remind", t("settings.reminders.remind"), config.due.remind),
            _field(
                t("settings.reminders.days"),
                el(
                    "input",
                    type="number",
                    name="days",
                    value=typed.get("days", str(config.due.remind_days_before)),
                    min=0,
                    max=60,
                    step=1,
                    required=True,
                ),
            ),
            el(
                "fieldset",
                el("legend", t("settings.reminders.kinds")),
                *kinds,
                class_="chips",
            ),
            _save(),
        ),
    )


def _language_section(view: _View, config: Config) -> Markup:
    options = [
        el(
            "option",
            i18n.NAMES.get(code, locale.display_name),
            value=code,
            selected=code == config.language,
        )
        for code, locale in LOCALES.items()
    ]
    return _section(
        view,
        "language",
        _form(
            view,
            "language",
            _field(t("settings.language.field"), el("select", *options, name="language")),
            _help(t("settings.language.help")),
            _save(),
        ),
    )


def _file_section(view: _View) -> Markup:
    backup = config_edit.backups(view.snap.path)
    return _section(
        view,
        "file",
        el(
            "p",
            t(
                "settings.file.where",
                path=el("bdi", el("code", shown(display_path(view.snap.path))), dir="ltr"),
            ),
        ),
        el("p", prose(t("settings.file.check"))),
        el(
            "div",
            el(
                "form",
                csrf_field(view.csrf),
                el("button", t("settings.file.open"), type="submit", class_="small quiet"),
                method="post",
                action="/settings/open",
                class_="inline",
            ),
            el("a", t("settings.restore_link"), href="/settings/restore", class_="small-link")
            if backup
            else None,
            class_="tools",
        ),
    )


# --- stopping organizing a folder: a confirmation first --------------------------------


def input_remove_page(path: Path | None, csrf: str, query: str) -> Page:
    """``GET /settings/input-remove?index=<i>``: the folder, its rules, and the button."""
    params = query_params(query, frozenset({"index"}))
    values = params.get("index", [])
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        raise BadRequest
    index = int(values[0][:6])
    parts: list[Markup | None] = [el("h1", t("settings.remove.title"))]
    back = el("p", el("a", t("settings.back"), href="/settings#s-inputs"))
    snap = read(path) if path is not None else None
    config = snap.config if snap is not None else None
    if snap is None or config is None or index >= len(config.inputs) or len(config.inputs) < 2:
        parts += [message(t("settings.remove.cannot"), error=True), back]
        return HTTPStatus.OK, layout(t("settings.remove.short"), "settings", join(parts), csrf)
    entry = config.inputs[index]
    view = _View(snap, csrf, None)
    parts += [
        el(
            "p",
            t(
                "settings.remove.lead",
                folder=el("bdi", shown(display_path(entry.path)), class_="name", dir="ltr"),
            ),
            class_="lead",
        ),
        el("div", rules_text(config, entry), class_="where"),
        _form(
            view,
            "input-remove",
            el("input", type="hidden", name="index", value=index),
            el(
                "div",
                el("button", t("settings.remove.button"), type="submit"),
                " ",
                el("a", t("common.cancel"), href="/settings#s-inputs"),
                class_="confirm",
            ),
        ),
    ]
    return HTTPStatus.OK, layout(t("settings.remove.short"), "settings", join(parts), csrf)


# --- the changes -----------------------------------------------------------------------


Fields = dict[str, list[str]]


@dataclass(frozen=True, slots=True)
class Context:
    """What a section's change reads: the file as parsed, the form, the file's path."""

    raw: dict[str, Any]
    config: Config
    fields: Fields
    path: Path


def flag(fields: Fields, name: str) -> bool:
    values = fields.get(name, [])
    if len(values) > 1 or (values and values[0] != "1"):
        raise Malformed(name)
    return bool(values)


def field(fields: Fields, name: str, limit: int, label: Msg) -> str:
    """A text field, trimmed and composed (NFC); refused, naming it, when too long or
    holding invisible characters."""
    value = browser_text(one(fields, name, required=False) or "")
    if len(value) > limit:
        raise Refused(t("settings.field.too_long", label=label, limit=limit))
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in value):
        raise Refused(t("settings.field.invisible", label=label))
    return value


def amount(text: str, what: Msg) -> float:
    """A cost cap typed in the form, read by ``formats.parse_amount``'s rule: ``3,00`` and
    ``3.00`` are both three; ``1.234`` in pt-BR (``1,234`` in English) is refused."""
    bad = Refused(t("settings.costs.bad_amount", what=what, max=f"{MAX_AMOUNT:g}"))
    try:
        value = formats.parse_amount(text)
    except formats.AmountError as exc:
        if exc.kind == "ambiguous":
            raise Refused(t("settings.costs.ambiguous", what=what, typed=shown(text))) from exc
        raise bad from exc
    if not 0 < value <= Decimal(str(MAX_AMOUNT)):
        raise bad
    return float(value)


def _set(edits: list[Edit], path: tuple[str | int, ...], old: Any, new: Any) -> None:
    if old != new:
        edits.append(Set(path, new))


def _provider_edits(ctx: Context) -> list[Edit]:
    choices = provider_choices(ctx.config)
    value = one(ctx.fields, "choice") or ""
    if not value.isdigit() or not value.isascii() or len(value) > 3 or int(value) >= len(choices):
        raise Malformed("choice")
    chosen = choices[int(value)]
    edits: list[Edit] = []
    _set(edits, ("provider", "name"), ctx.config.provider.name, chosen.name)
    _set(edits, ("provider", "model"), ctx.config.provider.model, chosen.model)
    return edits


def _reading_edits(ctx: Context) -> list[Edit]:
    config, fields = ctx.config, ctx.fields
    edits: list[Edit] = []
    _set(edits, ("vision", "enabled"), config.vision.enabled, flag(fields, "vision"))
    _set(edits, ("ocr", "enabled"), config.ocr.enabled, flag(fields, "ocr"))
    _set(
        edits,
        ("search", "semantic", "enabled"),
        config.search.semantic.enabled,
        flag(fields, "semantic"),
    )
    return edits


def _duplicates_edits(ctx: Context) -> list[Edit]:
    value = one(ctx.fields, "duplicates")
    if value not in ("move", "leave"):
        raise Malformed("duplicates")
    edits: list[Edit] = []
    _set(edits, ("duplicates",), ctx.config.duplicates, value)
    return edits


def _costs_edits(ctx: Context) -> list[Edit]:
    config, fields = ctx.config, ctx.fields
    mode = one(fields, "per_day")
    if mode not in ("default", "none", "amount"):
        raise Malformed("per_day")
    edits: list[Edit] = []
    current = config.limits.max_cost_usd_per_run
    per_run_text = (one(fields, "per_run", required=False) or "").strip()
    if not as_shown(per_run_text, current):  # as shown: left alone, whatever its digits
        what = t("settings.costs.what_run")
        per_run = None if not per_run_text else amount(per_run_text, what)
        _set(edits, ("limits", "max_cost_usd_per_run"), current, per_run)
    limits = ctx.raw.get("limits")
    written = isinstance(limits, dict) and "max_cost_usd_per_day" in limits
    old = limits.get("max_cost_usd_per_day") if isinstance(limits, dict) else None
    if mode == "default":
        if written:
            edits.append(Delete(("limits", "max_cost_usd_per_day")))
    elif mode == "none":
        if not written or old is not None:
            edits.append(Set(("limits", "max_cost_usd_per_day"), None))
    else:
        text = (one(fields, "day_amount", required=False) or "").strip()
        if written and old is not None and as_shown(text, old):
            return edits
        day = amount(text, t("settings.costs.what_day"))
        if not written or old != day:
            edits.append(Set(("limits", "max_cost_usd_per_day"), day))
    return edits


def _reminders_edits(ctx: Context) -> list[Edit]:
    config, fields = ctx.config, ctx.fields
    days = (one(fields, "days", required=False) or "").strip()
    if not days.isdigit() or not days.isascii() or len(days) > 2 or int(days) > 60:
        raise Refused(t("settings.reminders.bad_days"))
    kinds = fields.get("kinds", [])
    if len(set(kinds)) != len(kinds) or not set(kinds) <= set(KINDS):
        raise Malformed("kinds")
    edits: list[Edit] = []
    _set(edits, ("summary", "notify"), config.summary.notify, flag(fields, "notify"))
    _set(edits, ("due", "remind"), config.due.remind, flag(fields, "remind"))
    _set(edits, ("due", "remind_days_before"), config.due.remind_days_before, int(days))
    old = list(config.due.kinds)
    if set(kinds) != set(old):  # the file's order stays; new ones go at the end
        new = [k for k in old if k in kinds] + [k for k in KINDS if k in kinds and k not in old]
        edits.append(Set(("due", "kinds"), new))
    return edits


def category_entry(category: Category) -> dict[str, Any]:
    """A category as the file holds it: only what differs from the defaults."""
    entry: dict[str, Any] = {"name": category.name}
    if category.hint is not None:
        entry["hint"] = category.hint
    if not category.rename:
        entry["rename"] = False
    if category.layout is not None:
        entry["layout"] = category.layout
    return entry


def _language_edits(ctx: Context) -> list[Edit]:
    """The language, with what it brought written in the file first: switching it never
    moves the library or renames a folder or a category."""
    raw, config = ctx.raw, ctx.config
    value = one(ctx.fields, "language")
    if value not in LOCALES:
        raise Malformed("language")
    if value == config.language:
        return []
    edits: list[Edit] = []
    implicit: dict[str, Any] = {
        "output_root": display_path(config.output_root),
        "review_folder": config.review_folder,
        "duplicates_folder": config.duplicates_folder,
        "categories": [category_entry(c) for c in config.categories],
    }
    for key, current in implicit.items():
        if key not in raw:
            edits.append(Set((key,), current))
    ocr = raw.get("ocr")
    if not (isinstance(ocr, dict) and "languages" in ocr):
        edits.append(Set(("ocr", "languages"), list(config.ocr.languages)))
    edits.append(Set(("language",), value))
    return edits


def _layout_choice(fields: Fields, current: str | None) -> str | None:
    key = one(fields, "layout")
    if key == "keep" and current is not None:
        return current
    for name, value, _ in LAYOUTS:
        if name == key:
            return value
    raise Malformed("layout")


def _category(config: Config, fields: Fields) -> tuple[int, Category]:
    name = one(fields, "category")
    for i, category in enumerate(config.categories):
        if category.name == name:
            return i, category
    raise Refused(STALE)


def _holds_files(config: Config, name: str) -> Msg | None:
    """Why category ``name`` holds files, or ``None`` when renaming it moves nothing. A
    folder that cannot be read is taken to hold some."""
    for entry in config.inputs:
        if entry.category == name:
            return t("settings.holds.input", path=shown(display_path(entry.path)))
    count = 0
    if config.database.exists():
        try:
            with read_index(config) as store:
                count = store.files_in_category(name)
        except Unreadable as exc:
            raise Refused(t("settings.holds.index_error", problem=_exc_text(exc))) from exc
    if count:
        return t("settings.holds.index", count=count)
    folder = config.output_root / name
    try:
        if folder.is_dir() and any(p.name != ".DS_Store" for p in folder.iterdir()):
            return t("settings.holds.not_empty")
    except OSError:
        return t("settings.holds.unreadable")
    return None


def _categories_edit(raw: dict[str, Any], config: Config, edits: list[Edit]) -> list[Edit]:
    """``edits`` to ``categories`` as asked when the file lists them; when they come from
    the language, the whole list is written with the change made."""
    if not edits or isinstance(raw.get("categories"), list):
        return edits
    entries = [category_entry(c) for c in config.categories]
    return [
        Set(("categories",), config_edit.apply_data({"categories": entries}, edits)["categories"])
    ]


def _category_add(ctx: Context) -> list[Edit]:
    name = field(ctx.fields, "name", MAX_NAME, t("settings.what.name"))
    if not name:
        raise Refused(t("settings.category.needs_name"))
    hint = field(ctx.fields, "hint", MAX_HINT, t("settings.what.hint"))
    entry: dict[str, Any] = {"name": name}
    if hint:
        entry["hint"] = hint
    if not flag(ctx.fields, "rename"):
        entry["rename"] = False
    layout_value = _layout_choice(ctx.fields, None)
    if layout_value is not None:
        entry["layout"] = layout_value
    return _categories_edit(ctx.raw, ctx.config, [Append(("categories",), entry)])


def _category_edit(ctx: Context) -> list[Edit]:
    fields = ctx.fields
    i, category = _category(ctx.config, fields)
    name = field(fields, "name", MAX_NAME, t("settings.what.name"))
    name = name or category.name  # names are NFC with no spaces at the ends: as shown
    hint: str | None = category.hint
    if flag(fields, "hint_field") and editable_hint(category.hint):
        typed = field(fields, "hint", MAX_HINT, t("settings.what.hint"))
        if typed != browser_text(category.hint or ""):
            hint = typed or None
    rename = flag(fields, "rename")
    layout_value = _layout_choice(fields, category.layout)
    if name != category.name and (why := _holds_files(ctx.config, category.name)):
        raise Refused(
            t(
                "settings.category.not_renamed",
                name=shown(category.name),
                why=why,
                command=command(ctx.path, "reprocess", "--rename-category", category.name, name),
            )
        )
    edits: list[Edit] = []
    base = ("categories", i)
    _set(edits, (*base, "name"), category.name, name)
    if hint != category.hint:
        edits.append(Set((*base, "hint"), hint) if hint else Delete((*base, "hint")))
    if rename != category.rename:
        edits.append(Set((*base, "rename"), rename))
    if layout_value != category.layout:
        edits.append(
            Set((*base, "layout"), layout_value) if layout_value else Delete((*base, "layout"))
        )
    return _categories_edit(ctx.raw, ctx.config, edits)


def _category_remove(ctx: Context) -> list[Edit]:
    i, category = _category(ctx.config, ctx.fields)
    if len(ctx.config.categories) == 1:
        raise Refused(t("settings.category.last"))
    if why := _holds_files(ctx.config, category.name):
        raise Refused(
            t(
                "settings.category.not_removed",
                name=shown(category.name),
                why=why,
                command=command(ctx.path, "reprocess", "--remove-category", category.name),
            )
        )
    return _categories_edit(ctx.raw, ctx.config, [Delete(("categories", i))])


def _input_add(ctx: Context) -> list[Edit]:
    key = one(ctx.fields, "folder")
    found = next((c for c in candidates() if c.key == key), None)
    if found is None:
        raise Malformed("folder")
    why = folder_problem(found.path, ctx.config)
    if why is not None:
        raise Refused(t("settings.inputs.not_added", folder=found.label, why=why))
    if isinstance(ctx.raw.get("inputs"), list):
        return [Append(("inputs",), found.entry)]
    return [Set(("inputs",), [display_path(p) for p in ctx.config.input_paths] + [found.entry])]


def _input_remove(ctx: Context) -> list[Edit]:
    value = one(ctx.fields, "index") or ""
    if not value.isdigit() or not value.isascii() or len(value) > 3:
        raise Malformed("index")
    if int(value) >= len(ctx.config.inputs):
        raise Malformed("index")
    if len(ctx.config.inputs) == 1 or not isinstance(ctx.raw.get("inputs"), list):
        raise Refused(t("settings.inputs.last"))
    return [Delete(("inputs", int(value)))]


Change = Callable[[Config, Path, "Fields"], tuple[Outcome, Config | None]]
"""A settings change: the running configuration, the file, the form."""
Builder = Callable[[Context], list[Edit]]
SECTIONS: dict[str, Builder] = {
    "provider": _provider_edits,
    "reading": _reading_edits,
    "duplicates": _duplicates_edits,
    "costs": _costs_edits,
    "reminders": _reminders_edits,
    "language": _language_edits,
    "category-add": _category_add,
    "category-edit": _category_edit,
    "category-remove": _category_remove,
    "input-add": _input_add,
    "input-remove": _input_remove,
}
ANCHORS = {
    "category-add": "categories",
    "category-edit": "categories",
    "category-remove": "categories",
    "input-add": "inputs",
    "input-remove": "inputs",
    "duplicates": "inputs",
}
_KEPT_FIELDS = frozenset(
    {"section", "category", "name", "hint", "layout", "per_run", "per_day", "day_amount",
     "days", "mode", "time"}
)  # fmt: skip
"""What a refused form gives back to the page: what was typed, never the token."""


def _typed(fields: Fields) -> tuple[tuple[str, str], ...]:
    return tuple((k, v[0]) for k, v in fields.items() if k in _KEPT_FIELDS and len(v) == 1)


@contextmanager
def _in_section(anchor: str, fields: Fields) -> Iterator[None]:
    """A refusal shows in its section, with what was typed."""
    try:
        yield
    except Refused as exc:
        exc.outcome = dataclasses.replace(exc.outcome, anchor=anchor, form=_typed(fields))
        raise


def save(running: Config, path: Path, fields: Fields) -> tuple[Outcome, Config | None]:
    """One section's form: the file changed as asked, or refused; ``running`` is the
    configuration the server uses (its database is the one locked)."""
    section = one(fields, "section") or ""
    builder = SECTIONS.get(section)
    if builder is None:
        raise Malformed("section")
    pin = a_pin(fields)
    anchor = ANCHORS.get(section, section)
    with _in_section(anchor, fields), locked(running):
        snap, raw, config = _current(path, pin)
        edits = builder(Context(raw, config, fields, path))
        if not edits:
            return Outcome(NOTHING, anchor=anchor), None
        saved = commit(snap, raw, edits, running)
    notes = list(saved.changes)
    if section.startswith("input-"):
        notes += _refresh_watch(path, saved.config)
    outcome = Outcome(t("settings.saved"), items=tuple(notes), anchor=anchor, restore=True)
    return outcome, saved.config


def after_change(old: Config, new: Config, path: Path) -> list[Item]:
    """What follows a saved change, run once the server already uses ``new``: a change of
    language rebuilds the menu bar item (#126). Never raises: the change is saved."""
    if new.language == old.language:
        return []
    return refresh_menu(path)


def refresh_menu(path: Path) -> list[Item]:
    """After a change of language, the menu bar item's fixed entries follow (#126): they
    are put in the app when it is built, so it is rebuilt (``flib menu --refresh``).
    Only when it is installed for this configuration file. Whatever goes wrong (a tool,
    a bundle it cannot read) is said, never raised: the settings are already saved."""
    try:
        target = menubar.read_target() if menubar.installed() else None
        if target is None or not same_place(target.config, path):
            return []
        menubar.refresh()
    except Exception as exc:  # the menu is a convenience: it never fails a saved change
        log.warning("menu bar not rebuilt after a change of language: %s", type(exc).__name__)
        return [Item(t("settings.menu.name"), t("settings.menu.failed"))]
    return [Item(t("settings.menu.name"), t("settings.menu.done"))]


def _refresh_watch(path: Path, config: Config) -> list[Item]:
    """After the inputs change, watch mode watches the new ones (``flib schedule
    --refresh``): launchd's folders are fixed when the agent is installed. Only for
    agents of this file: another file's schedule is never taken over silently."""
    try:
        state = scheduler.status()
        if not state.installed or not state.watch_paths:
            return []
        if other_schedule(path) is not None:
            return [Item(t("settings.watch.name"), t("settings.watch.other"))]
        scheduler.install_for(
            path, config, state.time or config.schedule.time, state.mode or "watch"
        )
    except (scheduler.ScheduleError, OSError):
        return [Item(t("settings.watch.name"), t("settings.watch.failed"))]
    return [Item(t("settings.watch.name"), t("settings.watch.done"))]


# --- the schedule ----------------------------------------------------------------------


def schedule(running: Config, path: Path, fields: Fields) -> tuple[Outcome, Config | None]:
    """``flib schedule`` (or ``--remove``) from the page: the mode and time written in
    the file, then the agents installed by the same function; launchd's refusal is said.
    Agents another configuration file installed are changed only when asked to."""
    mode = one(fields, "mode")
    if mode not in SCHEDULE_MODES:
        raise Malformed("mode")
    pin = a_pin(fields)
    takeover = flag(fields, "takeover")
    at = (one(fields, "time", required=False) or "").strip()
    with _in_section("schedule", fields), locked(running):
        snap, raw, config = _current(path, pin)
        other = other_schedule(path)
        if other is not None and not takeover:
            raise Refused(t("settings.other_schedule", other=shown(display_path(other))))
        if mode == "off":
            try:
                removed = scheduler.remove()
                notifier.remove()
            except OSError as exc:
                raise Refused(t("settings.schedule.remove_failed")) from exc
            text = t("settings.schedule.off") if removed else NOTHING
            return Outcome(text, anchor="schedule"), None
        when = config.schedule.time
        if mode != "watch":
            try:
                if not at.isascii():
                    raise ValueError(at)
                when = ScheduleConfig(time=at).time
            except ValueError as exc:
                raise Refused(t("settings.schedule.bad_time")) from exc
        edits: list[Edit] = []
        written = raw.get("schedule")
        if config.schedule.mode != mode or not (isinstance(written, dict) and "mode" in written):
            edits.append(Set(("schedule", "mode"), mode))
        _set(edits, ("schedule", "time"), config.schedule.time, when)
        saved = commit(snap, raw, edits, running) if edits else None
        config = saved.config if saved else config
        items = list(saved.changes) if saved else []
        try:
            scheduler.install_for(path, config, when, mode)
        except (scheduler.ScheduleError, OSError):
            refused = "settings.schedule.refused_saved" if saved else "settings.schedule.refused"
            text = t(refused, now=schedule_now())
            outcome = Outcome(
                text, error=True, items=tuple(items), anchor="schedule", restore=bool(saved)
            )
            return outcome, config if saved else None
    items += [Item(t("settings.note"), note) for note in schedule_notes(config)]
    outcome = Outcome(
        _scheduled(mode, when, config), items=tuple(items), anchor="schedule", restore=bool(saved)
    )
    return outcome, config if saved else None


def schedule_now() -> Msg:
    """What launchd has now: loaded, or only written (installed but not loaded)."""
    try:
        state = scheduler.status()
    except scheduler.ScheduleError:
        return t("settings.now.unreadable")
    if not state.installed:
        return t("settings.now.none")
    mode: Msg | str = (
        t(f"settings.mode.{state.mode}") if state.mode in SCHEDULE_MODES[1:] else str(state.mode)
    )
    what = t("settings.now.what_at", mode=mode, time=state.time) if state.time else mode
    if state.loaded:
        return t("settings.now.loaded", what=what)
    return t("settings.now.written", what=what)


def _scheduled(mode: str, when: str, config: Config) -> Msg:
    if mode == "daily":
        return t("settings.scheduled.daily", time=when)
    if mode == "both":
        return t("settings.scheduled.both", count=len(config.inputs), time=when)
    return t("settings.scheduled.watch", count=len(config.inputs))


def schedule_notes(config: Config) -> list[Msg]:
    """What ``flib schedule`` would warn about, without asking anything."""
    notes = []
    if not scheduler.is_installed_tool():
        notes.append(t("settings.notes.dev"))
    spec = credentials.key_spec(config.provider)
    if spec is not None:
        source = credentials.key_source(config.provider, spec)
        if source == "none" and spec.provider == "anthropic":
            notes.append(t("settings.notes.no_key"))
        elif source == "environment":
            notes.append(t("settings.notes.env_key"))
    if config.provider.name == "claude-code":
        from file_librarian.providers import claude_code

        if claude_code.find_claude(config.provider, search_path=scheduler.SAFE_PATH) is None:
            notes.append(t("settings.notes.claude"))
    problem = notifier.ensure()
    if problem is not None:
        notes.append(t("settings.notes.notifier"))
    if (warning := no_prices(config)) is not None:
        notes.append(warning)
    notes.append(t("settings.notes.doctor"))
    return notes


# --- restore ---------------------------------------------------------------------------


def restore_page(path: Path | None, csrf: str, query: str) -> Page:
    query_params(query, frozenset())  # it takes nothing
    parts: list[Markup | None] = [el("h1", t("settings.restore.title"))]
    back = el("p", el("a", t("settings.back"), href="/settings"))
    title = t("settings.restore.short")
    if path is None:
        parts += [message(NO_PATH), back]
        return HTTPStatus.OK, layout(title, "settings", join(parts), csrf)
    found = config_edit.backups(path)
    if not found:
        parts += [message(t("settings.restore.none")), back]
        return HTTPStatus.OK, layout(title, "settings", join(parts), csrf)
    latest = found[0]
    now, then = read(path), read(latest)
    if then.config is None:
        problem = _value(then.problem)
        parts += [message(t("settings.restore.unusable", problem=problem), error=True), back]
        return HTTPStatus.OK, layout(title, "settings", join(parts), csrf)
    changes = diff(now.raw or {}, then.raw or {}, now.config, then.config)
    parts += [
        el("p", t("settings.restore.lead", stamp=_stamp(latest, path)), class_="lead"),
        message(t("settings.restore.warning"), error=True),
        el(
            "ul",
            *(el("li", el("strong", _text_of(i.name)), ": ", prose(i.text)) for i in changes),
            class_="moves",
            aria_label=t("settings.restore.what"),
        )
        if changes
        else message(t("settings.restore.same")),
        el(
            "form",
            csrf_field(csrf),
            el("input", type="hidden", name="pin", value=now.pin),
            el("input", type="hidden", name="backup", value=config_edit.pin(then.data)),
            el("button", t("settings.restore.button"), type="submit"),
            " ",
            el("a", t("common.cancel"), href="/settings"),
            method="post",
            action="/settings/restore",
            class_="confirm",
        ),
    ]
    return HTTPStatus.OK, layout(title, "settings", join(parts), csrf)


def _stamp(backup: Path, path: Path) -> Msg | str:
    """When a backup was made, in local time (``formats.moment``)."""
    found = config_edit.stamp_of(path, backup)
    if found is None:  # pragma: no cover - backups() lists only stamped names
        return t("settings.restore.earlier")
    moment = datetime.strptime(found[0], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    return formats.moment(moment.astimezone())


def restore(running: Config, path: Path, fields: Fields) -> tuple[Outcome, Config | None]:
    pin = a_pin(fields)
    wanted = one(fields, "backup") or ""
    if len(wanted) != config_edit.PIN_LENGTH or not all(c in "0123456789abcdef" for c in wanted):
        raise Malformed("backup")
    with locked(running):
        snap = read(path)
        if snap.pin != pin:
            raise Refused(STALE)
        found = config_edit.backups(path)
        then = read(found[0]) if found else None
        if then is None or config_edit.pin(then.data) != wanted:
            raise Refused(t("settings.restore.changed"))
        if then.config is None:
            raise Refused(t("settings.restore.backup_unusable", problem=_value(then.problem)))
        if then.config.database != running.database:
            raise Refused(OTHER_DATABASE)
        _write(path, snap.data, then.text)
    items = diff(snap.raw or {}, then.raw or {}, snap.config, then.config)
    outcome = Outcome(t("settings.restore.done"), items=tuple(items), restore=True)
    return outcome, then.config


# --- open in an editor -----------------------------------------------------------------


def open_in_editor(path: Path | None, program: str, runner: Callable[[list[str]], int]) -> Outcome:
    """``open -t`` on the configuration file (the server's own path, never one from the
    page): an outcome for the page either way."""

    def refused(text: Msg) -> Outcome:
        return Outcome(text, error=True, anchor="file")

    if path is None:
        return refused(NO_PATH)
    try:
        info = path.stat()
    except OSError:
        return refused(t("settings.file.missing"))
    if not stat.S_ISREG(info.st_mode):
        return refused(t("settings.file.not_plain"))
    try:
        code = runner([program, "-t", "--", str(path)])
    except (OSError, subprocess.SubprocessError):
        code = -1
    if code != 0:
        return refused(t("settings.file.open_failed"))
    return Outcome(t("settings.file.opened"), anchor="file")
