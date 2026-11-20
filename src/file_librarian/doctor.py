"""``flib doctor``: one redacted report to attach to a bug report (RF-39).

The report gathers what a maintainer asks first: versions, how flib was
installed, the configuration, where the API key comes from, the schedule, the
database and the recent run logs. It is meant to be posted publicly, so
everything personal is replaced before it is written:

- **The API key never appears**, not even its last characters; only the word
  for where it comes from (``environment``, ``keychain``, ``env_file``,
  ``none``).
- **Paths** start with a placeholder for the folder they are in: ``<home>``,
  ``<input-1>``, ``<output>``, ``<data>`` (flib's own folder). Every other folder
  name becomes ``<dir-N>``, except category names and the review folder (they
  come from the configuration) and a few standard macOS folders.
- **File names** become ``<file-N>`` plus the extension (``<file-3>.pdf``). The
  same name always gets the same number within one report, so a reader can
  still follow one file through the logs.
- **Secrets** (anything :mod:`file_librarian.sensitive` detects), **e-mail
  addresses**, **URLs** and **quoted text** are replaced too, as is the account
  name.

Run logs are parsed line by line in the formats ``flib run`` writes them, so a
name with spaces is replaced whole; any other text goes through the generic
rules above. The report never fails as a whole: a section that cannot be
collected shows its (redacted) error instead.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from file_librarian import __version__, credentials, protection, scheduler
from file_librarian import status as status_mod
from file_librarian.config import (
    Config,
    default_config_path,
    default_database_path,
    inactive_options_set,
    is_loopback_url,
    load_config,
)
from file_librarian.db.store import load_migrations
from file_librarian.models import FileStatus
from file_librarian.runlog import logs_dir

# The detection patterns of sensitive.py, reused to replace values instead of naming kinds.
from file_librarian.sensitive import EMAIL_PATTERN, SECRET_PATTERNS

Format = Literal["text", "markdown"]

KEEP_REPORTS = 10
"""Doctor reports kept in the logs folder; older ones are removed."""
SCHEDULE_LOG_LINES = 40
_REPORT_PREFIX = "doctor-"

# Folder names that say nothing about the person: standard macOS and tool folders.
_SAFE_DIRS = frozenset(
    {
        "Users", "Volumes", "Library", "Application Support", "LaunchAgents", "Logs",
        "file-librarian", "logs", "usr", "local", "bin", "opt", "homebrew", "private",
        "var", "tmp", "Applications", ".config", ".local", "share", "Downloads",
        "Desktop", "Documents", "Mobile Documents", "uv", "tools", ".venv",
    }
)  # fmt: skip
# Files flib itself writes; their names are safe and useful.
_OWN_FILES = re.compile(
    r"run-\d+\.log|library\.db(?:-wal|-shm)?|schedule\.(?:out|err)\.log"
    r"|doctor-[\d-]+\.(?:txt|md)|config\.yaml|flib-python|.*\.plist"
    r"|flib(?:-reprocess|-waiter)?\.lock"
)
_ASSIGNMENT = "password or secret assignment"
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}")

_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]+")
_QUOTED = re.compile(r"'([^'\n]*)'|\"([^\"\n]*)\"")
# An unquoted path may hold spaces: up to the first extension when there is one (taking
# too much only hides more), else up to the first space.
_PATH = re.compile(
    r"(?<![\w<>~/.])(?:~/|/)(?:"
    r"[^\n'\"<>]*?\.[A-Za-z0-9]{1,8}(?=[\s'\",;:)<>]|$)"
    r"|(?:Application Support|Mobile Documents|[^\s'\"(),;<>])*)"
)
_FILE_TOKEN = re.compile(
    r"(?<![\w<>./-])([^\s/\\'\"()<>\[\]{},;:=|]+)(\.[A-Za-z0-9]{1,8})(?![\w.])"
)
_PYTHON_CODE = (
    (re.compile(r".*/site-packages/"), "<site-packages>/"),
    (re.compile(r".*/src/(?=file_librarian/)"), "<src>/"),
    (re.compile(r".*/lib/python3\.\d+/"), "<python>/"),
)


class Redactor:
    """Replaces names, paths and secrets with stable placeholders."""

    def __init__(
        self,
        *,
        home: Path,
        roots: Iterable[tuple[Path, str]] = (),
        keep: Iterable[str] = (),
    ) -> None:
        self.home = home
        pairs = [(str(home), "<home>"), *((str(path), name) for path, name in roots)]
        # Most specific first: <output> inside <input-1> inside <home>.
        self.roots = sorted(
            ((_nfc(path).rstrip("/") or "/", name) for path, name in pairs),
            key=lambda pair: -len(pair[0]),
        )
        self.keep = {_nfc(name) for name in keep}
        user = home.name
        self._user = re.compile(rf"\b{re.escape(user)}\b") if len(user) >= 3 else None
        self._files: dict[str, str] = {}
        self._dirs: dict[str, str] = {}
        self.secrets: list[str] = []
        """Exact values that must never appear (the API key); replaced as a last resort."""

    # --- names and paths -----------------------------------------------------

    def file_name(self, name: str) -> str:
        name = _nfc(name)
        if name in self.keep or name in _SAFE_DIRS or _OWN_FILES.fullmatch(name):
            return name
        stem, dot, ext = name.rpartition(".")
        suffix = f".{ext}" if dot and stem and _EXTENSION.fullmatch(f".{ext}") else ""
        key = name.casefold()
        if key not in self._files:
            self._files[key] = f"<file-{len(self._files) + 1}>"
        return self._files[key] + suffix

    def dir_name(self, name: str) -> str:
        name = _nfc(name)
        if name in self.keep or name in _SAFE_DIRS or name in ("", ".", ".."):
            return name
        key = name.casefold()
        if key not in self._dirs:
            self._dirs[key] = f"<dir-{len(self._dirs) + 1}>"
        return self._dirs[key]

    def path(self, text: str) -> str:
        """A path, absolute or relative to a known folder, with every component replaced."""
        text = _nfc(text)
        if text.startswith("~/"):
            text = str(self.home) + text[1:]
        for pattern, placeholder in _PYTHON_CODE:
            match = pattern.match(text)
            if match:  # flib's and Python's own code: the rest of the path is safe
                return placeholder + text[match.end() :]
        for root, placeholder in self.roots:
            if text == root:
                return placeholder
            if text.startswith(root.rstrip("/") + "/"):
                return f"{placeholder}/{self._relative(text[len(root.rstrip('/')) + 1 :])}"
        if text.startswith("/"):
            return "/" + self._relative(text[1:])
        return self._relative(text)

    def _relative(self, text: str) -> str:
        *folders, last = text.split("/")
        parts = [self.dir_name(part) for part in folders]
        parts.append(self.file_name(last) if last else "")
        return "/".join(parts)

    # --- free text -----------------------------------------------------------

    def text(self, text: str) -> str:
        """Free text (an error message, a note): every rule of the module docstring."""
        text = self.secrets_and_contacts(_nfc(text))
        text = _QUOTED.sub(self._quoted, text)
        text = _PATH.sub(lambda m: self.path(m.group(0)), text)
        text = _FILE_TOKEN.sub(self._file_token, text)
        if self._user is not None:
            text = self._user.sub("<user>", text)
        return text

    def secrets_and_contacts(self, text: str, *, assignments: bool = True) -> str:
        """Secrets, URLs and e-mail addresses. ``assignments=False`` keeps ``key: value``
        lines, for the final pass over the configuration (``api_key_source: keychain``)."""
        for value in self.secrets:
            if value:
                text = text.replace(value, "<secret>")
        for kind, pattern in SECRET_PATTERNS:
            if assignments or kind != _ASSIGNMENT:
                text = pattern.sub(f"<secret: {kind}>", text)
        if assignments:
            text = _URL.sub("<url>", text)
        return EMAIL_PATTERN.sub("<email>", text)

    def _quoted(self, match: re.Match[str]) -> str:
        quote = match.group(0)[0]
        inner = match.group(1) if match.group(1) is not None else match.group(2)
        if inner in self.keep:
            return match.group(0)
        if inner.startswith(("/", "~/")):
            return quote + self.path(inner) + quote
        return f"{quote}<text>{quote}"

    def _file_token(self, match: re.Match[str]) -> str:
        if re.fullmatch(r"[\d.]+", match.group(1)):  # a number such as 0.75, not a name
            return match.group(0)
        return self.file_name(match.group(1) + match.group(2))

    # --- run logs ------------------------------------------------------------

    def log_line(self, line: str) -> str:
        """One line in a format ``flib run`` writes; anything else goes through :meth:`text`."""
        for pattern, rebuild in self._log_formats:
            match = pattern.fullmatch(line)
            if match:
                return rebuild(self, match)
        return self.text(line)

    _log_formats: tuple[tuple[re.Pattern[str], Callable[[Redactor, re.Match[str]], str]], ...] = (
        (re.compile(r"# flib run \d+ finished \S+"), lambda _r, m: m.group(0)),
        (
            re.compile(r"  dup     (.+)  ->  (.+)  \(same content as (.+)\)"),
            lambda r, m: (
                f"  dup     {r.path(m.group(1))}  ->  {r.path(m.group(2))}  "
                f"(same content as {r.path(m.group(3))})"
            ),
        ),
        (
            re.compile(r"  moved   (.+)  ->  (.+)"),
            lambda r, m: f"  moved   {r.path(m.group(1))}  ->  {r.path(m.group(2))}",
        ),
        (
            re.compile(r"  (warning |skip    )(.+)  \((.*)\)"),
            lambda r, m: f"  {m.group(1)}{r.path(m.group(2))}  ({r.text(m.group(3))})",
        ),
        (
            re.compile(rf"  ({'|'.join(s.value for s in FileStatus)}) *(.+)  \((.*)\)"),
            lambda r, m: f"  {m.group(1):<8}{r.path(m.group(2))}  ({r.text(m.group(3))})",
        ),
    )


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def redactor_for(config: Config | None) -> Redactor:
    home = Path.home()
    roots: list[tuple[Path, str]] = [(default_database_path().parent, "<data>")]
    keep: list[str] = []
    if config is not None:
        roots.append((config.database.parent, "<data>"))
        roots.extend((path, f"<input-{i}>") for i, path in enumerate(config.inputs, start=1))
        roots.append((config.output_root, "<output>"))
        keep.extend(c.name for c in config.categories)
        keep.append(config.review_folder)
        keep.append(config.duplicates_folder)
    # Also the resolved spelling (/private/var for /var, a symlinked Downloads).
    roots.extend((Path(os.path.realpath(path)), name) for path, name in list(roots))
    roots.append((Path(os.path.realpath(home)), "<home>"))
    return Redactor(home=home, roots=roots, keep=keep)


# --- the report --------------------------------------------------------------------


@dataclass(slots=True)
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    """``key: value`` lines."""
    blocks: list[tuple[str, list[str]]] = field(default_factory=list)
    """Verbatim blocks (a log, the configuration), each with a caption."""


@dataclass(frozen=True, slots=True)
class Options:
    config_path: Path | None = None
    run_logs: int = 5
    runs: int = 10


def collect(options: Options) -> tuple[list[Section], Redactor]:
    """Every section of the report, already redacted. Never raises."""
    path = options.config_path or default_config_path()
    config: Config | None = None
    config_error: Exception | None = None
    try:
        config = load_config(path)
    except Exception as exc:  # reported in the configuration section
        config_error = exc
    try:
        redactor = redactor_for(config)
    except Exception:  # pragma: no cover - only Path.home() could fail here
        redactor = Redactor(home=Path("/nonexistent"))
    builders: list[tuple[str, Callable[[Section], None]]] = [
        ("flib", _versions),
        ("Configuration", lambda s: _configuration(s, redactor, path, config, config_error)),
        ("API key", lambda s: _api_key(s, redactor, config)),
        ("Schedule", lambda s: _schedule(s, redactor, path)),
        ("Database", lambda s: _database(s, redactor, config, options.runs)),
        ("Status", lambda s: _status(s, redactor, config)),
        ("Logs", lambda s: _logs(s, redactor, config, options.run_logs)),
    ]
    sections = []
    for title, build in builders:
        section = Section(title)
        try:
            build(section)
        except Exception as exc:  # one broken section must not hide the others
            section.lines.append(f"error: {type(exc).__name__}: {redactor.text(str(exc))}")
        sections.append(section)
    return sections, redactor


def render(sections: list[Section], redactor: Redactor, fmt: Format) -> str:
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    intro = [
        f"flib doctor report, generated {stamp}",
        "File names, paths, secrets and e-mail addresses are replaced with placeholders.",
        "Please read it before attaching it to an issue.",
    ]
    out: list[str] = []
    if fmt == "markdown":
        out += ["# flib doctor report", "", *(f"{line}  " for line in intro[1:])]
        for section in sections:
            out += ["", f"## {section.title}", ""]
            # In code spans, or GitHub would take <home> and the like for HTML tags.
            out += ["- `" + line.replace("`", "'") + "`" for line in section.lines]
            for caption, block in section.blocks:
                fence = "````" if any("```" in line for line in block) else "```"
                out += ["", f"{caption}:", "", f"{fence}text", *block, fence]
        out.append(f"\n_Generated {stamp}._")
    else:
        out += intro
        for section in sections:
            out += ["", f"== {section.title} =="]
            out += section.lines
            for caption, block in section.blocks:
                out += ["", f"{caption}:", *(f"    {line}" for line in block)]
    # Last line of defence: whatever slipped through above still loses its secrets.
    return redactor.secrets_and_contacts("\n".join(out) + "\n", assignments=False)


def build_report(options: Options, fmt: Format = "text") -> str:
    sections, redactor = collect(options)
    return render(sections, redactor, fmt)


def default_report_path(config_path: Path | None, fmt: Format) -> Path:
    """``doctor-<time>.txt`` in flib's logs folder (docs/troubleshooting.md says why there)."""
    try:
        database = load_config(config_path).database
    except Exception:
        database = default_database_path()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "md" if fmt == "markdown" else "txt"
    return logs_dir(database) / f"{_REPORT_PREFIX}{stamp}.{suffix}"


def write_report(path: Path, report: str) -> Path:
    """Write owner-only, then keep only the newest ``KEEP_REPORTS`` reports next to it."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(report)
    if path.name.startswith(_REPORT_PREFIX):
        old = sorted(p for p in path.parent.glob(f"{_REPORT_PREFIX}*") if p.is_file())
        for stale in old[:-KEEP_REPORTS]:
            stale.unlink()
    return path


# --- sections ----------------------------------------------------------------------


def _versions(section: Section) -> None:
    section.lines += [
        f"version: {__version__}",
        f"python: {platform.python_version()} ({platform.python_implementation()})",
        f"macos: {platform.mac_ver()[0] or 'unknown'} ({platform.machine()})",
        f"install: {install_kind()}",
        f"mcp extra: {_mcp_extra()}",
    ]


def install_kind() -> str:
    if scheduler.is_installed_tool():
        return "uv tool"
    package = Path(__file__).resolve().parent
    if package.parent.name == "src" and (package.parent.parent / "pyproject.toml").exists():
        return "development checkout"
    if sys.prefix != sys.base_prefix:
        return "virtual environment"
    return "system Python"


def _mcp_extra() -> str:
    if importlib.util.find_spec("mcp") is None:
        return "not installed"
    try:
        return f"installed (mcp {importlib.metadata.version('mcp')})"
    except importlib.metadata.PackageNotFoundError:
        return "installed"


def _configuration(
    section: Section, r: Redactor, path: Path, config: Config | None, error: Exception | None
) -> None:
    section.lines.append(f"file: {r.path(str(path))}")
    section.lines.append(f"FLIB_CONFIG set: {'yes' if os.environ.get('FLIB_CONFIG') else 'no'}")
    if config is None:
        section.lines.append(f"valid: no ({r.text(str(error))})")
        return
    section.lines.append("valid: yes")
    for option, milestone in inactive_options_set(config):
        section.lines.append(f"inactive option set: {option} (planned for {milestone})")
    for i, folder in enumerate(config.inputs, start=1):
        section.lines.append(f"input-{i}: {_folder_state(folder)}")
    output = _folder_state(config.output_root)
    section.lines.append(f"output: {output}")
    section.blocks.append(("Configuration (redacted)", redacted_config(config, r)))


def _folder_state(folder: Path) -> str:
    if not folder.exists():
        return "missing"
    if not folder.is_dir():
        return "not a folder"
    return "exists" if os.access(folder, os.R_OK | os.X_OK) else "exists, not readable"


def redacted_config(config: Config, r: Redactor) -> list[str]:
    data: dict[str, Any] = config.model_dump(mode="json")
    data["inputs"] = [f"<input-{i}>" for i in range(1, len(config.inputs) + 1)]
    data["output_root"] = "<output>"
    data["database"] = r.path(str(config.database))
    provider = data["provider"]
    for key in ("env_file", "command"):
        if provider.get(key) is not None:
            provider[key] = f"<{key.replace('_', '-')}>"
    if config.provider.base_url is not None:
        # The host can name a private server; keep only what helps a bug report.
        url = config.provider.base_url
        where = "this Mac" if is_loopback_url(url) else "remote"
        provider["base_url"] = f"<{url.partition(':')[0]}, {where}>"
    semantic = data["search"]["semantic"]
    url = config.search.semantic.base_url
    where = "this Mac" if is_loopback_url(url) else "remote"
    semantic["base_url"] = f"<{url.partition(':')[0]}, {where}>"
    for category in data["categories"]:
        if category.get("hint"):
            category["hint"] = f"<hint, {len(category['hint'])} characters>"
    for key in ("ignore", "never_send"):
        data[key] = [_pattern(p, i) for i, p in enumerate(data[key], start=1)]
    data["naming"]["pattern"] = r.text(data["naming"]["pattern"])
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return dumped.rstrip("\n").splitlines()


def _pattern(pattern: str, index: int) -> str:
    """Generic patterns (``*.dmg``, ``.DS_Store``) stay; anything with a name is replaced."""
    if re.fullmatch(r"\*?\.[A-Za-z0-9_-]{1,12}\*?|\.[A-Za-z0-9_.-]{1,20}|\*", pattern):
        return pattern
    return f"<pattern-{index}>"


def _api_key(section: Section, r: Redactor, config: Config | None) -> None:
    if config is None:
        section.lines.append("source: unknown (the configuration did not load)")
        return
    specs = credentials.all_specs(config.provider)
    for spec in specs:  # every key flib could find, so none can leak through a log line
        value = credentials.resolve(config.provider, spec).value
        if value:
            r.secrets.append(value)
    # A provider without a key reports the Anthropic one, as before keys were per provider.
    found = credentials.resolve(config.provider, specs[0])
    section.lines.append(f"provider: {config.provider.name}")
    section.lines.append(f"api_key_source setting: {config.provider.api_key_source}")
    section.lines.append(f"source: {found.source}")
    env_file = config.provider.env_file
    if env_file is not None:
        private = credentials.env_file_is_private(env_file)
        state = (
            "missing" if not env_file.exists() else ("private" if private else "READABLE BY OTHERS")
        )
        section.lines.append(f"env_file: {state}")


def _schedule(section: Section, r: Redactor, config_path: Path) -> None:
    state = scheduler.status()
    if not state.installed:
        section.lines.append("installed: no")
        return
    section.lines += [
        "installed: yes",
        f"loaded: {'yes' if state.loaded else 'no'}",
        f"time: {state.time}",
        f"interpreter current: {'yes' if state.interpreter_current else 'no'}",
        f"program: {' '.join(r.path(part) if '/' in part else part for part in state.program)}",
    ]
    scheduled = scheduler.scheduled_config()
    if scheduled is not None:
        same = os.path.realpath(scheduled) == os.path.realpath(config_path)
        section.lines.append(f"uses this configuration: {'yes' if same else 'no'}")


def _database(section: Section, r: Redactor, config: Config | None, runs: int) -> None:
    path = config.database if config is not None else default_database_path()
    section.lines.append(f"file: {r.path(str(path))}")
    if not path.exists():
        section.lines.append("exists: no (created by the first run)")
        return
    latest = len(load_migrations())
    # Read-only: the doctor never creates, migrates or locks the database.
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        section.lines.append(f"schema version: {version} (this flib knows {latest})")
        for table, column in (("files", "status"), ("operations", "status")):
            counts = conn.execute(
                f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column} ORDER BY {column}"  # noqa: S608
            ).fetchall()
            summary = ", ".join(f"{status} {count}" for status, count in counts) or "none"
            section.lines.append(f"{table} by {column}: {summary}")
        for table in ("runs", "corrections", "classification_cache"):
            try:
                (count,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
                section.lines.append(f"{table}: {count}")
            except sqlite3.OperationalError as exc:
                section.lines.append(f"{table}: {r.text(str(exc))}")
        # A database older than this flib (never migrated here) has no cost column yet.
        columns = {str(c[1]) for c in conn.execute("PRAGMA table_info(runs)")}
        cost = "cost_usd" if "cost_usd" in columns else "NULL"
        query = (
            "SELECT id, mode, trigger, started_at, finished_at, files_seen, files_moved,"  # noqa: S608 - a fixed column name
            f" files_review, errors, {cost} FROM runs ORDER BY id DESC LIMIT ?"
        )
        rows = conn.execute(query, (runs,)).fetchall()
    finally:
        conn.close()
    if rows:
        header = (
            "id  mode      trigger   started                    finished  seen moved review errors"
            "  cost US$"
        )
        block = [header]
        for row in rows:
            run_id, mode, trigger, started, finished, *counts, usd = row
            done = "yes" if finished else "no"
            numbers = " ".join(f"{'-' if c is None else c:>5}" for c in counts)
            price = "-" if not finished else "unknown" if usd is None else f"{usd:.4f}"
            block.append(
                f"{run_id:<3} {mode:<9} {trigger:<9} {started:<26} {done:<8} {numbers} {price:>9}"
            )
        section.blocks.append((f"Last {len(rows)} run(s)", block))


def _status(section: Section, r: Redactor, config: Config | None) -> None:
    """What ``flib status`` shows (it names no file), read-only like the rest."""
    if config is None:
        section.lines.append("unknown (the configuration did not load)")
        return
    status = status_mod.collect(config)
    lines = status_mod.render(status, config, private=True)
    section.lines += [r.text(line) for line in lines]
    _data_protection(section, status)


def _data_protection(section: Section, status: status_mod.Status) -> None:
    """Whether the Mac is locked now, and how many files the last run could not read
    because of it (macOS Data Protection; counts only)."""
    locked = protection.screen_locked()
    state = "unknown" if locked is None else "yes" if locked else "no"
    section.lines.append(f"screen locked now: {state}")
    if status.skipped is not None:
        count = status.skipped.get("locked", 0)
        section.lines.append(f"skipped in the last run as protected while locked: {count}")


def _logs(section: Section, r: Redactor, config: Config | None, count: int) -> None:
    database = config.database if config is not None else default_database_path()
    folder = logs_dir(database)
    section.lines.append(f"folder: {r.path(str(folder))}")
    if not folder.is_dir():
        section.lines.append("exists: no")
        return
    logs = sorted(p for p in folder.glob("run-*.log") if p.is_file() and not p.is_symlink())
    section.lines.append(f"run logs: {len(logs)} (showing the last {min(count, len(logs))})")
    for log in logs[-count:] if count > 0 else []:
        section.blocks.append((log.name, _read_lines(log, r, limit=None)))
    for name in ("schedule.out.log", "schedule.err.log"):
        path = folder / name
        if path.is_file() and not path.is_symlink():
            lines = _read_lines(path, r, limit=SCHEDULE_LOG_LINES)
            section.blocks.append((f"{name} (last {SCHEDULE_LOG_LINES} lines)", lines))


def _read_lines(path: Path, r: Redactor, *, limit: int | None) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"error: {r.text(str(exc))}"]
    if limit is not None:
        lines = lines[-limit:]
    return [r.log_line(line) for line in lines] or ["(empty)"]
