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

from file_librarian import config_edit, credentials, menubar, notifier, scheduler
from file_librarian import status as status_mod
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
from file_librarian.ui.markup import Markup, csrf_field, el, join, layout
from file_librarian.ui.views import (
    BadRequest,
    Page,
    Unreadable,
    plural,
    query_params,
    read_index,
    sentence,
    shown,
)

MAX_HINT = 300
"""Characters a category's hint may have (it goes to the model with every file)."""
MAX_NAME = 64
MAX_AMOUNT = 1000.0
"""The highest cost cap the page takes, in US$; more goes in the file."""
_AMOUNT = re.compile(r"[0-9]{1,4}(\.[0-9]{1,4})?")
LONG_RULES = 160
"""An input folder's rules longer than this (in words) fold under a ``<details>``."""
LAYOUTS: tuple[tuple[str, str | None, str], ...] = (
    ("none", None, "All in the category's folder"),
    ("year", "{year}", "By year"),
    ("year-month", "{year}/{month}", "By year, then month"),
    ("issuer", "{issuer}", "By issuer"),
    ("issuer-year", "{issuer}/{year}", "By issuer, then year"),
)
"""The sub-folder layouts the page offers (``config._layout`` checks any other)."""
SCHEDULE_MODES = ("off", "daily", "watch", "both")
SECTION_TITLES = (
    ("schedule", "When to organize"),
    ("provider", "Who classifies"),
    ("reading", "What flib reads and sends"),
    ("inputs", "Folders"),
    ("categories", "Categories"),
    ("costs", "Cost caps"),
    ("reminders", "Notifications and what is due"),
    ("language", "Language"),
    ("file", "Everything else"),
)

STALE = (
    "The configuration file changed since this page was shown: edited by hand, in another "
    "tab or by flib. Nothing was saved; the page now shows it as it is."
)
NO_PATH = (
    "This flib ui does not know its configuration file, so its settings can only be read "
    "here. Run `flib ui` from Terminal to change them."
)
GONE = (
    "What happened there is no longer kept (only the latest are, until the session ends). "
    "The page shows the settings as they are now."
)
NOTHING = "Nothing changed: the settings were already like that."
OTHER_DATABASE = (
    "The file now names another database than this flib ui uses. Nothing was saved; end "
    "the session and run `flib ui` again."
)
OTHER_SCHEDULE = (
    "The schedule uses another configuration file, {other}. Nothing was changed in launchd: "
    "tick “Use this file for the schedule” to move it here."
)

LABELS = {
    "language": "Language",
    "inputs": "Input folders",
    "output_root": "Library folder",
    "review_folder": "Review folder",
    "duplicates_folder": "Duplicates folder",
    "duplicates": "Duplicates",
    "provider.name": "Who classifies",
    "provider.model": "Model",
    "vision.enabled": "Images to the model (vision)",
    "ocr.enabled": "Text in images (OCR)",
    "ocr.languages": "Text recognition languages",
    "search.semantic.enabled": "Search by meaning",
    "limits.max_cost_usd_per_run": "Cost cap per run",
    "limits.max_cost_usd_per_day": "Daily cost cap",
    "summary.notify": "Summary notification",
    "due.remind": "Due reminders",
    "due.remind_days_before": "Reminder, days before",
    "due.kinds": "Kinds on the Due list",
    "schedule.mode": "When to organize",
    "schedule.time": "Daily run at",
    "categories": "Categories",
}
FIELDS = {
    "categories.name": "Category name",
    "categories.hint": "Category hint",
    "categories.layout": "Sub-folders",
    "inputs.path": "Input folder",
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
    """Text where the page's `backticks` mark a command; the user's are text."""
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
        prose(sentence(text)),
        class_="notice error" if error else "notice",
        role="status",
        **attrs,
    )


def command(config_path: Path | None, *args: str) -> str:
    """A ``flib`` command line the user can paste: every word quoted for the shell, and
    ``-c <file>`` when this is not the default configuration file. Marked as a command."""
    words = ["flib", args[0]]
    if config_path is not None and config_path != default_config_path():
        words += ["-c", str(config_path)]
    words += args[1:]
    return "`" + lit(" ".join(shlex.quote(w) for w in words)) + "`"


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
        return Snapshot(path, b"", "", None, None, "There is no configuration file there.")
    except OSError:
        return Snapshot(path, b"", "", None, None, "The configuration file cannot be read.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Snapshot(path, data, "", None, None, "The configuration file is not UTF-8 text.")
    try:
        raw = config_edit.load(text)
    except EditError as exc:
        return Snapshot(path, data, text, None, None, sentence_of(str(exc)))
    try:
        config = parse_config(raw)
    except ConfigError as exc:
        return Snapshot(path, data, text, raw, None, invalid(exc))
    return Snapshot(path, data, text, raw, config, None)


def sentence_of(text: str) -> str:
    return stop(text[:1].upper() + text[1:])


def invalid(exc: ConfigError) -> str:
    """The parser's refusal: each field and why, in one paragraph (as text)."""
    lines = str(exc).splitlines()[1:] or [str(exc)]
    parts = []
    for line in lines:
        where, _, why = line.strip().partition(": ")
        why = why.removeprefix("Value error, ")
        if where == "(root)":
            parts.append(why)
            continue
        label = LABELS.get(where) or FIELDS.get(re.sub(r"\.\d+(?=\.|$)", "", where))
        where = f"{label} ({where})" if label else where
        parts.append(f"{where}: {why}" if why else where)
    return lit("; ".join(parts))


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
        raise Refused(f"The configuration file cannot be changed here: {snap.problem}")
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
        raise Refused(f"Not saved: {exc}.") from exc
    except OSError as exc:
        raise Refused("The configuration file could not be written; nothing changed.") from exc


def commit(snap: Snapshot, old: dict[str, Any], edits: list[Edit], running: Config) -> Saved:
    """Make ``edits`` to the file: in the text, validated, backed up, written."""
    try:
        text = config_edit.edit_text(snap.text, edits)
    except EditError as exc:
        raise Refused(
            f"Not saved: {exc}. Change it in the configuration file instead; nothing was written."
        ) from exc
    raw = config_edit.load(text)
    try:
        config = parse_config(raw)
    except ConfigError as exc:
        raise Refused(f"Not saved, nothing was written: {invalid(exc)}.") from exc
    if config.database != running.database:
        raise Refused(OTHER_DATABASE)
    _write(snap.path, snap.data, text)
    return Saved(config, tuple(diff(old, raw, snap.config, config)))


# --- what changed ----------------------------------------------------------------------


_LISTS = {"categories": "Category", "inputs": "Input folder"}
"""Lists of entries told apart field by field, each named by its name or path."""


def _flat(data: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(data, dict) and prefix.rstrip(".") not in _LISTS:
        found: dict[str, Any] = {}
        for key, value in data.items():
            found |= _flat(value, f"{prefix}{key}.")
        return found
    return {prefix.rstrip("."): data}


_ABSENT = object()


def describe(key: str, value: Any) -> str:
    """A value as the page shows it (as text): web addresses by their host only."""
    if value is _ABSENT:
        return "the default"
    if key.endswith("base_url") and isinstance(value, str):
        return host_of(value)
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, list):
        return ", ".join(_ident(v) for v in value) or "none"
    if isinstance(value, dict):
        return "…"
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
    if not isinstance(new, list):
        return [Item(LABELS[key], "back to what the language brings")]
    if not isinstance(old, list):
        return [Item(LABELS[key], "written in the file, as they were")]
    items: list[Item] = []
    if len(old) == len(new):
        pairs = list(zip(old, new, strict=True))
    else:
        before = {_ident(e): e for e in old}
        after = {_ident(e): e for e in new}
        pairs = [(before[n], after[n]) for n in after if n in before]
        items += [Item(f"{noun} {lit(n)}", "added") for n in after if n not in before]
        items += [Item(f"{noun} {lit(n)}", "removed") for n in before if n not in after]
    for a, b in pairs:
        name = _ident(a)
        if _ident(b) != name:
            items.append(Item(f"{noun} {lit(name)}", f"renamed to {lit(_ident(b))}"))
        fa, fb = _fields_of(a), _fields_of(b)
        for field in dict.fromkeys([*fb, *fa]):
            if field in ("name", "path"):
                continue
            va, vb = fa.get(field, _ABSENT), fb.get(field, _ABSENT)
            if va != vb:
                text = f"{lit(describe(field, va))} → {lit(describe(field, vb))}"
                items.append(Item(f"{noun} {lit(_ident(b))} {field}", text))
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
            text = f"{lit(describe(key, a))} → {lit(describe(key, b))}"
            items.append(Item(LABELS.get(key, lit(key)), text))
    return items


def host_of(url: str) -> str:
    """Where a web address goes, and nothing else of it: ``api.example.com``."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return "an address in the file"
    return f"{host}{port}"


# --- what the page offers --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderChoice:
    name: str
    model: str
    label: str


def _provider_label(name: str, config: ProviderConfig) -> str:
    if name == "anthropic":
        return "Anthropic API"
    if name == "claude-code":
        return "Your Claude subscription (Claude Code)"
    if name == "ollama":
        return "Ollama, on this Mac"
    if name == "openai-compat":
        return f"OpenAI-compatible API at {host_of(config.base_url or '')}"
    return "The test provider (nothing is sent)"


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
        ProviderChoice(name, model, f"{_provider_label(name, p)}: {model}")
        for name, model in dict.fromkeys(pairs)
    ]


def leaves(config: Config, name: str | None = None) -> str:
    """What leaves the Mac to classify a file, in one sentence."""
    p = config.provider
    name = name or p.name
    chars = p.max_chars_per_file
    what = f"Each file's name, details and up to {chars} characters of its text"
    if name == "anthropic":
        return f"{what} go to Anthropic's API."
    if name == "claude-code":
        return f"{what} go to Anthropic through your Claude subscription."
    if name in ("ollama", "openai-compat"):
        url = p.base_url or ("http://localhost:11434/v1" if name == "ollama" else "")
        if is_loopback_url(url):
            return "Nothing leaves this Mac: the model runs here."
        return f"{what} go to {host_of(url)}."
    return "Nothing leaves this Mac."


def key_state(config: ProviderConfig) -> str | None:
    """Whether the provider's API key is found, and where: never the key itself (the
    Keychain is only asked whether it holds one)."""
    spec = credentials.key_spec(config)
    if spec is None:
        return None
    source = credentials.key_source(config, spec)
    where = {"environment": "in the environment", "keychain": "in the Keychain"}
    if source == "none":
        return f"{spec.label}: not set. `flib key set` in Terminal saves it in the Keychain."
    return f"{spec.label}: set ({where.get(source, 'in the file named by provider.env_file')})."


@dataclass(frozen=True, slots=True)
class Candidate:
    key: str
    path: Path
    label: str
    note: str
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
            "Downloads",
            "Everything at its top level.",
            display_path(downloads),
        ),
        Candidate(
            "desktop",
            desktop,
            "Desktop",
            f"Only documents and images unchanged for {DESKTOP_MIN_AGE_DAYS} days; folders are "
            "left alone, as `flib init` sets it up.",
            {
                "path": display_path(desktop),
                "only": list(DESKTOP_ONLY),
                "min_age_days": DESKTOP_MIN_AGE_DAYS,
                "folders": "ignore",
            },
        ),
    ]


def folder_problem(path: Path, config: Config) -> str | None:
    """Why ``path`` cannot be an input folder, or ``None``. Checked on the server, at the
    moment of the change: a folder of the user's own under the home folder, really there,
    not a link, not hidden, not ``~/Library``, not the home folder or ``/``, not the
    library or inside it, not already an input."""
    home = Path.home()
    text = str(path)
    if not path.is_absolute() or os.path.normpath(text) != text:
        return "it is not a plain folder path"
    try:
        info = os.lstat(path)
    except OSError:
        return "there is no such folder"
    if stat.S_ISLNK(info.st_mode):
        return "it is a link to somewhere else"
    if not stat.S_ISDIR(info.st_mode):
        return "it is not a folder"
    if same_place(path, Path(path.anchor)) or same_place(path, home):
        return "it is too broad"
    if not path.is_relative_to(home) or not is_within(path, home):
        return "it is outside your home folder"
    if any(part.startswith(".") for part in path.relative_to(home).parts):
        return "it is hidden"
    if is_within(path, home / "Library"):
        return "it is a system folder"
    if info.st_uid != os.getuid():
        return "it is not your folder"
    if is_within(path, config.output_root):
        return "it is the library, or inside it"
    if any(same_place(path, p) for p in config.input_paths):
        return "it is an input folder already"
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
        el("h1", "Settings"),
        el(
            "p",
            prose(
                "The essentials. Each change is checked as `flib check-config` checks it "
                "before it is saved, keeps a backup, and can be undone with Restore the "
                "previous settings. Everything else is in the configuration file."
            ),
            class_="lead",
        ),
    ]
    if gone or (outcome is not None and outcome.anchor is None):
        parts.append(outcome_notice(outcome, gone))
    if path is None:
        parts.append(message(NO_PATH))
        return HTTPStatus.OK, layout("Settings", "settings", join(parts), csrf)
    snap = read(path)
    view = _View(snap, csrf, outcome)
    if snap.config is None:
        if outcome is not None and outcome.anchor is not None:  # no section to show it in
            parts.append(outcome_notice(outcome, False))
        parts.append(message(f"The configuration file cannot be used: {snap.problem}", error=True))
        parts.append(_file_section(view))
        return HTTPStatus.OK, layout("Settings", "settings", join(parts), csrf)
    config = snap.config
    parts += [
        el(
            "nav",
            *(el("a", title, href=f"#s-{key}", class_="chip") for key, title in SECTION_TITLES),
            class_="chips toc",
            aria_label="Sections",
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
    return HTTPStatus.OK, layout("Settings", "settings", join(parts), csrf)


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
            el("a", "Restore the previous settings…", href="/settings/restore"),
            class_="note",
        )
    return el(
        "div",
        message(outcome.text, error=outcome.error),
        el("ul", *items, class_="moves", aria_label="What changed") if items else None,
        restore,
        class_="outcome",
        id=None if anchor is None else f"o-{anchor}",
        tabindex=None if anchor is None else "-1",
    )


def _text_of(label: str) -> str:
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


def _save(label: str = "Save") -> Markup:
    return el("div", el("button", label, type="submit"), class_="buttons")


def _schedule_section(view: _View, config: Config) -> Markup:
    notes: list[Markup | None] = []
    typed = view.typed("schedule")
    try:
        state = scheduler.status()
    except scheduler.ScheduleError:
        state = None
        notes.append(message("launchd's state cannot be read now.", error=True))
    current = "off"
    at = config.schedule.time
    takeover = None
    if state is not None and state.installed:
        current = state.mode or "daily"
        at = state.time or at
        if not state.loaded:
            notes.append(
                message("The schedule is written but not loaded: save it again.", error=True)
            )
        other = other_schedule(view.snap.path)
        if other is not None:
            notes.append(
                message(
                    f"The installed schedule uses another configuration file, "
                    f"{lit(display_path(other))}. Saving here changes nothing in launchd "
                    "unless you tick the box below: then it uses this file instead.",
                    error=True,
                )
            )
            takeover = _check("takeover", "Use this file for the schedule", False)
        stale = scheduler.stale_watch_message(state, config.input_paths)
        if stale and other is None:
            notes.append(
                message("Watch mode watches other folders than the inputs: save it again.")
            )
    current = typed.get("mode", current)
    at = typed.get("time", at)
    words = {
        "off": "Off: only when you run it",
        "daily": "Every day at the time below",
        "watch": "As files arrive",
        "both": "As files arrive, and every day at the time below",
    }
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
        el("fieldset", el("legend", "Run flib"), *radios, class_="choices"),
        _field(
            "Daily run at (24-hour)",
            el("input", type="time", name="time", value=at, required=True, step=60),
        ),
        takeover,
        _save("Save the schedule"),
        action="/settings/schedule",
    )
    return _section(
        view,
        "schedule",
        _help(
            "A launchd agent runs flib, as `flib schedule` sets it up. If the Mac is asleep "
            "at the time, it runs on wake. As files arrive, the reminders and the periodic "
            "summary come only after a daily run."
        ),
        *notes,
        form,
        _help(menu_bar_note()),
    )


def menu_bar_note() -> str:
    """Whether flib is in the menu bar (#116), and the command that changes it: a line,
    not a toggle (installing it builds an app and a LaunchAgent; that stays a command)."""
    if menubar.installed():
        return "Menu bar: the cat is there. `flib menu --remove` takes it away."
    return "Menu bar: not shown. `flib menu` puts the cat there, with what needs you."


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
            _field("Provider and model", el("select", *options, name="choice")),
            el(
                "ul",
                *(
                    el("li", el("strong", c.label), ": ", leaves(config, c.name))
                    for c in choices[1:]
                ),
                class_="moves",
                aria_label="What each choice sends",
            ),
            _save(),
        ),
        _help(
            "Another server or model, a key or the provider's other options are set in the "
            "file (`flib key set` for a key). `flib reprocess` classifies filed files again."
        ),
    )


def _reading_section(view: _View, config: Config) -> Markup:
    semantic = config.search.semantic
    where = "this Mac" if is_loopback_url(semantic.base_url) else host_of(semantic.base_url)
    vision_help = (
        "Images (made smaller, without their location) and the first pages of scanned PDFs "
        "go to the model with the file. Off, they are classified from their name and text."
    )
    if config.provider.name == "claude-code":
        vision_help += " It has no effect with your Claude subscription."
    return _section(
        view,
        "reading",
        _form(
            view,
            "reading",
            _check(
                "vision", "Send images to the model (vision)", config.vision.enabled, vision_help
            ),
            _check(
                "ocr",
                "Read text in images and scanned PDFs (OCR)",
                config.ocr.enabled,
                "On this Mac, with macOS's own text recognition; nothing is sent for it.",
            ),
            _check(
                "semantic",
                "Search by meaning",
                semantic.enabled,
                f"Names, tags, descriptions and categories (never file content) go to {where} "
                "to be embedded. `flib search --reindex` embeds what is already filed.",
            ),
            _save(),
        ),
    )


def _rules(config: Config, entry: InputConfig) -> Markup:
    """An input folder's rules in words; long ones (hundreds of patterns) fold away."""
    text = shown(config.input_summary(entry))
    if len(text) <= LONG_RULES:
        return el("div", text, class_="where")
    return el(
        "details",
        el("summary", "Its rules"),
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
                "Stop organizing it…",
                href=f"/settings/input-remove?index={i}",
                class_="small-link",
                aria_label=f"Stop organizing {name}…",
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
        if why == "it is an input folder already":
            continue
        if why is not None:
            adds.append(el("li", f"{candidate.label}: cannot be added, {why}.", class_="note"))
            continue
        adds.append(
            el(
                "li",
                _form(
                    view,
                    "input-add",
                    el("input", type="hidden", name="folder", value=candidate.key),
                    el("button", f"Organize {candidate.label} too", type="submit", class_="small"),
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
            ("move", f"Set them aside in {config.duplicates_folder}"),
            ("leave", "Leave them where they are"),
        )
    ]
    return _section(
        view,
        "inputs",
        _help("flib reads the top level of each input folder only."),
        el("ul", *rows, class_="hits", aria_label="Input folders"),
        el("ul", *adds, class_="plain") if adds else None,
        el(
            "dl",
            el("dt", "Library"),
            el("dd", el("bdi", shown(display_path(config.output_root)), dir="ltr")),
            el("dt", "Review folder"),
            el("dd", el("bdi", shown(config.review_folder), dir="ltr")),
            class_="facts",
        ),
        _help(
            "Another folder, the rules of a folder or the library's place are changed in the "
            "file; files already filed stay where they are."
        ),
        _form(
            view,
            "duplicates",
            el(
                "fieldset",
                el("legend", "Files with the same content as one already filed"),
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
                f"As in the file: {shown(current)}",
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
            el("span", "Hint for the AI", class_="label"),
            el("p", shown(hint or ""), class_="where hint"),
            _help(
                "It has line breaks or is long, so it is kept as it is here; change it in the "
                "configuration file."
            ),
            class_="field",
        )
    return join(
        el("input", type="hidden", name="hint_field", value="1"),
        _field(
            "Hint for the AI (what belongs here)",
            _text_input("hint", typed.get("hint", hint or ""), MAX_HINT),
        ),
    )


def _categories_section(view: _View, config: Config) -> Markup:
    items = []
    for category in config.categories:
        name = shown(category.name)
        typed = view.typed("categories", section="category-edit", category=category.name)
        facts = [
            category.layout and f"sub-folders {shown(category.layout)}",
            None if category.rename else "names kept",
        ]
        edit = el(
            "details",
            el("summary", "Change", el("span", f" {name}", class_="sr-only")),
            _form(
                view,
                "category-edit",
                el("input", type="hidden", name="category", value=category.name),
                _field(
                    "Name",
                    _text_input("name", typed.get("name", category.name), MAX_NAME, required=True),
                ),
                _hint_field(category.hint, typed),
                _field(
                    "Sub-folders",
                    el(
                        "select",
                        *_layout_options(category.layout, typed.get("layout")),
                        name="layout",
                    ),
                ),
                _check("rename", "Rename files with the naming pattern", category.rename),
                _save(),
            ),
            _form(
                view,
                "category-remove",
                el("input", type="hidden", name="category", value=category.name),
                el(
                    "button",
                    "Remove this category",
                    type="submit",
                    class_="small quiet",
                    aria_label=f"Remove the category {name}",
                ),
            ),
            class_="other",
            open=bool(typed),
        )
        items.append(
            el(
                "li",
                el("bdi", name, class_="name", dir="ltr"),
                el("div", shown(category.hint or "no hint"), class_="where"),
                el("div", ", ".join(f for f in facts if f), class_="note") if any(facts) else None,
                edit,
                class_="hit",
            )
        )
    typed = view.typed("categories", section="category-add")
    add = el(
        "details",
        el("summary", "Add a category"),
        _form(
            view,
            "category-add",
            _field(
                "Name (a folder name)",
                _text_input("name", typed.get("name", ""), MAX_NAME, required=True),
            ),
            _field(
                "Hint for the AI (optional)", _text_input("hint", typed.get("hint", ""), MAX_HINT)
            ),
            _field(
                "Sub-folders",
                el("select", *_layout_options(None, typed.get("layout")), name="layout"),
            ),
            _check("rename", "Rename files with the naming pattern", True),
            _save("Add"),
        ),
        class_="other",
        open=bool(typed),
    )
    return _section(
        view,
        "categories",
        _help(
            "Each one is a folder in the library. A hint tells the AI what belongs there. A "
            "category that holds files is renamed or removed with `flib reprocess`, which "
            "moves them; the page says how."
        ),
        el("ul", *items, class_="hits", aria_label="Categories"),
        add,
    )


def amount_text(value: float | None) -> str:
    """A cost cap as the page shows it: every digit the file has, never an exponent."""
    if value is None:
        return ""
    return format(Decimal(repr(value)), "f")


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
        ("default", "Three times the cap per run"),
        ("none", "No daily cap"),
        ("amount", "This amount:"),
    ]
    warning = [
        message(line.removeprefix("warning: "), error=True)
        for line in status_mod.cost_cap_lines(config)
        if line.startswith("warning")
    ]
    day_value = amount_text(limits.max_cost_usd_per_day) if explicit else ""
    return _section(
        view,
        "costs",
        _help(
            "What the provider's calls may cost, estimated by flib. Files left over wait for "
            "the next run. The daily cap applies to the scheduled runs."
        ),
        *warning,
        _form(
            view,
            "costs",
            _field(
                "Per run, US$ (empty: no cap)",
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
                el("legend", "Per day"),
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
                    el("span", "Daily cap, US$", class_="sr-only"),
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
            kind,
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
                "Notify me with the periodic summary",
                config.summary.notify,
                "A macOS notification with counts only, never file names, when files have waited.",
            ),
            _check("remind", "Count documents coming due in the summary", config.due.remind),
            _field(
                "Days before the due date",
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
                el("legend", "Kinds of document on the Due list"),
                *kinds,
                class_="chips",
            ),
            _save(),
        ),
    )


def _language_section(view: _View, config: Config) -> Markup:
    options = [
        el("option", locale.display_name, value=code, selected=code == config.language)
        for code, locale in LOCALES.items()
    ]
    return _section(
        view,
        "language",
        _form(
            view,
            "language",
            _field("Names and descriptions in", el("select", *options, name="language")),
            _help(
                "Folder and category names stay as they are: the page writes them in the "
                "file first if they came from the language."
            ),
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
            "The configuration file: ",
            el("bdi", el("code", shown(display_path(view.snap.path))), dir="ltr"),
            prose(". `config.example.yaml` in the repository lists every setting."),
        ),
        el(
            "p",
            prose(
                "`flib check-config` checks it, and `flib doctor` checks flib as a whole "
                "(folder access, the schedule, the key)."
            ),
        ),
        el(
            "div",
            el(
                "form",
                csrf_field(view.csrf),
                el("button", "Open it in a text editor", type="submit", class_="small quiet"),
                method="post",
                action="/settings/open",
                class_="inline",
            ),
            el("a", "Restore the previous settings…", href="/settings/restore", class_="small-link")
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
    parts: list[Markup | None] = [el("h1", "Stop organizing a folder")]
    back = el("p", el("a", "Back to Settings", href="/settings#s-inputs"))
    snap = read(path) if path is not None else None
    config = snap.config if snap is not None else None
    if snap is None or config is None or index >= len(config.inputs) or len(config.inputs) < 2:
        parts += [message("That folder cannot be removed from here now.", error=True), back]
        return HTTPStatus.OK, layout("Stop organizing", "settings", join(parts), csrf)
    entry = config.inputs[index]
    view = _View(snap, csrf, None)
    parts += [
        el(
            "p",
            "flib stops taking files from ",
            el("bdi", shown(display_path(entry.path)), class_="name", dir="ltr"),
            ". Nothing in it moves; what flib already filed stays in the library. Its "
            "entry, its rules and the comment above it leave the configuration file "
            "(a backup is kept).",
            class_="lead",
        ),
        el("div", shown(config.input_summary(entry)), class_="where"),
        _form(
            view,
            "input-remove",
            el("input", type="hidden", name="index", value=index),
            el(
                "div",
                el("button", "Stop organizing it", type="submit"),
                " ",
                el("a", "Cancel", href="/settings#s-inputs"),
                class_="confirm",
            ),
        ),
    ]
    return HTTPStatus.OK, layout("Stop organizing", "settings", join(parts), csrf)


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


def field(fields: Fields, name: str, limit: int, label: str) -> str:
    """A text field, trimmed and composed (NFC); refused, naming it, when too long or
    holding invisible characters."""
    value = browser_text(one(fields, name, required=False) or "")
    if len(value) > limit:
        raise Refused(f"Not saved: {label} is longer than {limit} characters.")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in value):
        raise Refused(f"Not saved: {label} has invisible characters, which are not taken.")
    return value


def amount(text: str, what: str) -> float:
    if not _AMOUNT.fullmatch(text) or not 0 < float(text) <= MAX_AMOUNT:
        raise Refused(
            f"Not saved: {what} must be an amount in US$ above 0 and at most {MAX_AMOUNT:g}, "
            "like 1.50."
        )
    return float(text)


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
    if per_run_text != amount_text(current):  # as shown: left alone, whatever its digits
        per_run = None if not per_run_text else amount(per_run_text, "the cap per run")
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
        if written and old is not None and text == amount_text(old):
            return edits
        day = amount(text, "the daily cap")
        if not written or old != day:
            edits.append(Set(("limits", "max_cost_usd_per_day"), day))
    return edits


def _reminders_edits(ctx: Context) -> list[Edit]:
    config, fields = ctx.config, ctx.fields
    days = (one(fields, "days", required=False) or "").strip()
    if not days.isdigit() or not days.isascii() or len(days) > 2 or int(days) > 60:
        raise Refused("Not saved: Days before the due date needs a number from 0 to 60.")
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


def _holds_files(config: Config, name: str) -> str | None:
    """Why category ``name`` holds files, or ``None`` when renaming it moves nothing. A
    folder that cannot be read is taken to hold some."""
    for entry in config.inputs:
        if entry.category == name:
            return f"the input folder {lit(display_path(entry.path))} files everything in it"
    count = 0
    if config.database.exists():
        try:
            with read_index(config) as store:
                count = store.files_in_category(name)
        except Unreadable as exc:
            raise Refused(f"Not saved: {exc}; nothing changed.") from exc
    if count:
        return f"the index has {plural(count, 'file')} in it"
    folder = config.output_root / name
    try:
        if folder.is_dir() and any(p.name != ".DS_Store" for p in folder.iterdir()):
            return "its folder is not empty"
    except OSError:
        return "its folder cannot be read"
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
    name = field(ctx.fields, "name", MAX_NAME, "the name")
    if not name:
        raise Refused("Not saved: a category needs a name.")
    hint = field(ctx.fields, "hint", MAX_HINT, "the hint")
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
    name = field(fields, "name", MAX_NAME, "the name")
    name = name or category.name  # names are NFC with no spaces at the ends: as shown
    hint: str | None = category.hint
    if flag(fields, "hint_field") and editable_hint(category.hint):
        typed = field(fields, "hint", MAX_HINT, "the hint")
        if typed != browser_text(category.hint or ""):
            hint = typed or None
    rename = flag(fields, "rename")
    layout_value = _layout_choice(fields, category.layout)
    if name != category.name and (why := _holds_files(ctx.config, category.name)):
        raise Refused(
            f"{lit(category.name)} was not renamed: {why}. Renaming it means moving them: "
            "change the name in the configuration file, then run "
            + command(ctx.path, "reprocess", "--rename-category", category.name, name)
            + " in Terminal."
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
        raise Refused("Not saved: flib needs at least one category.")
    if why := _holds_files(ctx.config, category.name):
        raise Refused(
            f"{lit(category.name)} was not removed: {why}. Remove it in the configuration "
            "file, then run "
            + command(ctx.path, "reprocess", "--remove-category", category.name)
            + " in Terminal: it classifies those files again (`--to-review` sends them to "
            "review instead)."
        )
    return _categories_edit(ctx.raw, ctx.config, [Delete(("categories", i))])


def _input_add(ctx: Context) -> list[Edit]:
    key = one(ctx.fields, "folder")
    found = next((c for c in candidates() if c.key == key), None)
    if found is None:
        raise Malformed("folder")
    why = folder_problem(found.path, ctx.config)
    if why is not None:
        raise Refused(f"{found.label} was not added: {why}.")
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
        raise Refused("Not saved: flib needs at least one input folder.")
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
    outcome = Outcome("Saved.", items=tuple(notes), anchor=anchor, restore=True)
    return outcome, saved.config


def _refresh_watch(path: Path, config: Config) -> list[Item]:
    """After the inputs change, watch mode watches the new ones (``flib schedule
    --refresh``): launchd's folders are fixed when the agent is installed. Only for
    agents of this file: another file's schedule is never taken over silently."""
    try:
        state = scheduler.status()
        if not state.installed or not state.watch_paths:
            return []
        if other_schedule(path) is not None:
            return [
                Item(
                    "Watch mode",
                    "not changed: the schedule uses another configuration file.",
                )
            ]
        scheduler.install_for(
            path, config, state.time or config.schedule.time, state.mode or "watch"
        )
    except (scheduler.ScheduleError, OSError):
        return [
            Item(
                "Watch mode",
                "still watches the old folders: launchd did not take the change. "
                "`flib schedule --refresh` in Terminal tries again and says why.",
            )
        ]
    return [Item("Watch mode", "now watches these folders.")]


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
            raise Refused(OTHER_SCHEDULE.format(other=lit(display_path(other))))
        if mode == "off":
            try:
                removed = scheduler.remove()
                notifier.remove()
            except OSError as exc:
                raise Refused(
                    "The schedule could not be removed; `flib schedule --remove` in Terminal "
                    "says why."
                ) from exc
            text = "The schedule is off: flib runs only when you run it." if removed else NOTHING
            return Outcome(text, anchor="schedule"), None
        when = config.schedule.time
        if mode != "watch":
            try:
                if not at.isascii():
                    raise ValueError(at)
                when = ScheduleConfig(time=at).time
            except ValueError as exc:
                raise Refused("Not saved: Daily run at needs a 24-hour time like 12:30.") from exc
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
            text = "macOS's launchd did not take the schedule"
            text += " (the settings were saved)" if saved else ""
            text += f". {schedule_now()} `flib schedule` in Terminal says why."
            outcome = Outcome(
                text, error=True, items=tuple(items), anchor="schedule", restore=bool(saved)
            )
            return outcome, config if saved else None
    items += [Item("Note", note) for note in schedule_notes(config)]
    outcome = Outcome(
        _scheduled(mode, when, config), items=tuple(items), anchor="schedule", restore=bool(saved)
    )
    return outcome, config if saved else None


def schedule_now() -> str:
    """What launchd has now: loaded, or only written (installed but not loaded)."""
    try:
        state = scheduler.status()
    except scheduler.ScheduleError:
        return "launchd's state cannot be read now."
    if not state.installed:
        return "Nothing is scheduled now."
    what = f"{state.mode}{' at ' + state.time if state.time else ''}"
    if state.loaded:
        return f"Loaded and running now: {what}."
    return f"Written but not loaded, so not running: {what}."


def _scheduled(mode: str, when: str, config: Config) -> str:
    folders = plural(len(config.inputs), "input folder")
    if mode == "daily":
        return f"Daily run scheduled at {when}. If the Mac is asleep then, it runs on wake."
    text = (
        f"Watch mode on: flib runs when something arrives in the {folders}, once downloads finish."
    )
    if mode == "both":
        text += f" And every day at {when}."
    return text


def schedule_notes(config: Config) -> list[str]:
    """What ``flib schedule`` would warn about, without asking anything."""
    notes = []
    if not scheduler.is_installed_tool():
        notes.append(
            "flib runs from a development folder: the schedule depends on it. Install it with "
            "`./scripts/install.sh` and save the schedule again."
        )
    spec = credentials.key_spec(config.provider)
    if spec is not None:
        source = credentials.key_source(config.provider, spec)
        if source == "none" and spec.provider == "anthropic":
            notes.append("No API key is set: the scheduled run fails until `flib key set`.")
        elif source == "environment":
            notes.append(
                "The API key is only in this Terminal's environment, which the scheduled run "
                "cannot see: `flib key set` saves it in the Keychain."
            )
    if config.provider.name == "claude-code":
        from file_librarian.providers import claude_code

        if claude_code.find_claude(config.provider, search_path=scheduler.SAFE_PATH) is None:
            notes.append(
                "The scheduled run will not find Claude Code: `flib schedule` in Terminal says "
                "what to add to the file."
            )
    problem = notifier.ensure()
    if problem is not None:
        notes.append(
            "Notifications will show as Script Editor; `flib schedule --refresh` tries again."
        )
    notes += [
        line.removeprefix("warning: ")
        for line in status_mod.cost_cap_lines(config)
        if line.startswith("warning")
    ]
    notes.append("`flib doctor` checks that the scheduled run can read the folders.")
    return notes


# --- restore ---------------------------------------------------------------------------


def restore_page(path: Path | None, csrf: str, query: str) -> Page:
    query_params(query, frozenset())  # it takes nothing
    parts: list[Markup | None] = [el("h1", "Restore the previous settings")]
    back = el("p", el("a", "Back to Settings", href="/settings"))
    if path is None:
        parts += [message(NO_PATH), back]
        return HTTPStatus.OK, layout("Restore", "settings", join(parts), csrf)
    found = config_edit.backups(path)
    if not found:
        parts += [
            message("There is no backup to restore: nothing was changed from here yet."),
            back,
        ]
        return HTTPStatus.OK, layout("Restore", "settings", join(parts), csrf)
    latest = found[0]
    now, then = read(path), read(latest)
    if then.config is None:
        parts += [message(f"The latest backup cannot be used: {then.problem}", error=True), back]
        return HTTPStatus.OK, layout("Restore", "settings", join(parts), csrf)
    changes = diff(now.raw or {}, then.raw or {}, now.config, then.config)
    parts += [
        el(
            "p",
            "This puts back the configuration file as it was before the last change made "
            "here (a backup from ",
            _stamp(latest, path),
            "). The current file is backed up first, so this can be undone the same way. The "
            "schedule in launchd is not changed: save it again on the Settings page if needed.",
            class_="lead",
        ),
        message(
            "The whole file is put back: comments and changes made by hand since that "
            "backup are lost (the current file stays in a backup).",
            error=True,
        ),
        el(
            "ul",
            *(el("li", el("strong", _text_of(i.name)), ": ", prose(i.text)) for i in changes),
            class_="moves",
            aria_label="What changes",
        )
        if changes
        else message("The backup has the same settings; only comments or layout differ."),
        el(
            "form",
            csrf_field(csrf),
            el("input", type="hidden", name="pin", value=now.pin),
            el("input", type="hidden", name="backup", value=config_edit.pin(then.data)),
            el("button", "Restore these settings", type="submit"),
            " ",
            el("a", "Cancel", href="/settings"),
            method="post",
            action="/settings/restore",
            class_="confirm",
        ),
    ]
    return HTTPStatus.OK, layout("Restore", "settings", join(parts), csrf)


def _stamp(backup: Path, path: Path) -> str:
    """When a backup was made, in local time."""
    found = config_edit.stamp_of(path, backup)
    if found is None:  # pragma: no cover - backups() lists only stamped names
        return "earlier"
    moment = datetime.strptime(found[0], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


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
            raise Refused("The backup changed since the page was shown; nothing was restored.")
        if then.config is None:
            raise Refused(f"The backup cannot be used: {then.problem}")
        if then.config.database != running.database:
            raise Refused(OTHER_DATABASE)
        _write(path, snap.data, then.text)
    items = diff(snap.raw or {}, then.raw or {}, snap.config, then.config)
    return Outcome("The previous settings are back.", items=tuple(items), restore=True), then.config


# --- open in an editor -----------------------------------------------------------------


def open_in_editor(path: Path | None, program: str, runner: Callable[[list[str]], int]) -> Outcome:
    """``open -t`` on the configuration file (the server's own path, never one from the
    page): an outcome for the page either way."""

    def refused(text: str) -> Outcome:
        return Outcome(text, error=True, anchor="file")

    if path is None:
        return refused(NO_PATH)
    try:
        info = path.stat()
    except OSError:
        return refused("There is no configuration file there.")
    if not stat.S_ISREG(info.st_mode):
        return refused("The configuration file is not a plain file.")
    try:
        code = runner([program, "-t", "--", str(path)])
    except (OSError, subprocess.SubprocessError):
        code = -1
    if code != 0:
        return refused("macOS could not open it.")
    return Outcome(
        "Opened in your text editor. Reload this page after you save it there; "
        "`flib check-config` checks it.",
        anchor="file",
    )
