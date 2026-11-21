"""``flib init``: everything in the guided first run except the questions (RF-38).

The prompts live in ``cli.py``; this module holds what can be tested without
them:

- ``detect_language`` and ``Answers``/``render_config``/``write_config``: the
  configuration ``init`` writes, short and commented, mode 600;
- ``desktop_folder`` and ``screenshot_folder``: the extra inputs ``init``
  offers (#89). The Desktop gets conservative rules (``DESKTOP_ONLY``,
  ``DESKTOP_MIN_AGE_DAYS``, folders left alone) because people keep work in
  progress there; a screenshots folder gets a fixed category, so no AI is
  asked about it. Where macOS saves screenshots is read with ``defaults``
  (``DEFAULTS``; tests swap in a fake);
- ``pick_sample``: the most recent items in the input folders that are worth
  a trial: not hidden, not in progress or ignored, not sensitive or
  ``never_send``, not only in iCloud, and not big;
- ``run_trial``: copies that sample into a temporary folder and runs a
  *simulation* over the copies with a throwaway database.

**The trial never touches a real file or the real database.** It reads the
originals only to copy them; the configuration it runs with points
``inputs``, ``output_root`` and ``database`` into the temporary folder, which
is deleted afterwards; and it only simulates, so even the copies stay put.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian import hashing, pipeline
from file_librarian.config import Config, InputConfig, is_within, parse_config, same_place
from file_librarian.globs import matches_any
from file_librarian.locales import DEFAULT_LANGUAGE, LOCALES, Language
from file_librarian.models import FileStatus
from file_librarian.providers.base import Provider
from file_librarian.scanner import DAY, IN_PROGRESS_PATTERNS, item_age
from file_librarian.sensitive import sensitive_name, withheld_by_config

DEFAULT_SAMPLE = 30
MAX_ITEM_BYTES = 20 * 1024 * 1024
"""Larger files and folders are left out of the trial: copying them takes too long."""
MAX_UNIT_ENTRIES = 500
DEFAULT_MODELS = {
    "anthropic": "claude-haiku-5-5",
    "claude-code": "haiku",
    "openai-compat": "gpt-5-mini",
    "ollama": "llama3.2",
}
OPENAI_COMPAT_URL = "https://api.openai.com/v1"
"""What ``init`` suggests for ``openai-compat``; any compatible server works."""
OLLAMA_PROBE_SECONDS = 1.0
DEFAULTS = "/usr/bin/defaults"
"""Reads where macOS saves screenshots; tests swap in a fake (``tests/fake_defaults.py``)."""
DESKTOP_MIN_AGE_DAYS = 7
DESKTOP_ONLY = (
    "*.pdf", "*.doc", "*.docx", "*.odt", "*.rtf", "*.pages",
    "*.xls", "*.xlsx", "*.ods", "*.numbers", "*.ppt", "*.pptx", "*.odp", "*.key",
    "*.png", "*.jpg", "*.jpeg", "*.heic", "*.gif", "*.webp", "*.tif", "*.tiff",
)  # fmt: skip
"""Documents and images: all ``flib init`` lets flib take from the Desktop. Plain text,
Markdown and CSV are left out: scratch notes and exports live there."""
SCREENSHOT_LAYOUT = "{year}/{month}"
SCREENSHOT_NAMES = (
    "Screenshot*", "Screen Shot*", "Screen Recording*",  # English
    "Captura de Tela*", "Gravação de Tela*",  # Portuguese
    "Captura de pantalla*", "Grabación de pantalla*",  # Spanish
    # French
    "Capture d\u2019écran*", "Capture d'écran*", "Enregistrement de l\u2019écran*",
    "Enregistrement de l'écran*", "Enregistrement d\u2019écran*", "Enregistrement d'écran*",
    "Bildschirmfoto*", "Bildschirmaufnahme*",  # German
    "Schermata*", "Registrazione schermo*",  # Italian
    "スクリーンショット*", "画面収録*",  # Japanese
)  # fmt: skip
"""How macOS names screenshots and screen recordings in the languages it is most used in."""
SCREENSHOT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".gif", ".mov", ".mp4")
SCREENSHOT_ONLY = tuple(
    f"{name}{ext}" for name in SCREENSHOT_NAMES for ext in SCREENSHOT_EXTENSIONS
)
"""The screenshots input's ``only``: nothing else in that folder is ever taken."""
SHARED_FOLDERS = (
    "", "Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music", "Library",
    "Library/Mobile Documents/com~apple~CloudDocs",
)  # fmt: skip
"""The home folder and the standard folders in it (relative to home; ``""`` is home)."""


def probe_ollama() -> list[str] | None:
    """The models a local Ollama has pulled, or ``None`` when it does not answer.

    One ``GET /v1/models`` to this Mac, with no file content; used by ``init`` to
    offer Ollama only when it is there.
    """
    from file_librarian.providers import ProviderError, ollama

    try:
        return ollama.installed_models(timeout=OLLAMA_PROBE_SECONDS)
    except ProviderError:
        return None


def detect_language(environ: Mapping[str, str] | None = None) -> Language:
    """``pt-BR`` when the shell's locale is Portuguese, else the default (``en``)."""
    env = os.environ if environ is None else environ
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(name, "")
        if value:
            return "pt-BR" if value.lower().startswith("pt") else DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE


def display_path(path: Path) -> str:
    """``path`` with the home folder written as ``~``."""
    home = Path.home()
    if path != home and path.is_relative_to(home):
        return f"~/{path.relative_to(home)}"
    return str(path)


def default_output_root(input_folder: Path, language: Language) -> Path:
    return input_folder / LOCALES[language].output_folder


# --- The configuration file ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Answers:
    language: Language
    input_folder: Path
    output_root: Path
    review_folder: str
    provider: str
    vision: bool = False
    command: Path | None = None
    """The ``claude`` executable, when the daily run would not find it on its own."""
    model_name: str | None = None
    """The model, when it was asked for (``openai-compat``, ``ollama``)."""
    base_url: str | None = None
    """``openai-compat``: the server's API root."""
    desktop: Path | None = None
    """The Desktop, added with conservative rules (``DESKTOP_ONLY``...)."""
    screenshots: Path | None = None
    """A screenshots folder, added with a fixed category and a year/month layout."""

    @property
    def model(self) -> str:
        return self.model_name or DEFAULT_MODELS.get(self.provider, "offline")


def _q(value: str) -> str:
    """A YAML double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(value, ensure_ascii=False)


def _inputs_block(answers: Answers) -> str:
    lines = [f"  - {_q(display_path(answers.input_folder))}"]
    if answers.desktop is not None:
        lines += [
            "  # People keep work in progress on the Desktop: only documents and images",
            f"  # that have not changed for {DESKTOP_MIN_AGE_DAYS} days are taken, and folders "
            "are left alone.",
            f"  - path: {_q(display_path(answers.desktop))}",
            *_flow("    only: ", DESKTOP_ONLY),
            f"    min_age_days: {DESKTOP_MIN_AGE_DAYS}",
            "    folders: ignore",
        ]
    if answers.screenshots is not None:
        category = LOCALES[answers.language].screenshots_category
        lines += [
            "  # Screenshots: filed by year and month with no AI call (and no cost). Only",
            "  # what macOS names as a screenshot or screen recording is taken; folders",
            "  # and everything else there are left alone.",
            f"  - path: {_q(display_path(answers.screenshots))}",
            f"    category: {_q(category)}",
            f"    layout: {_q(SCREENSHOT_LAYOUT)}",
            *_flow("    only: ", SCREENSHOT_ONLY),
            "    folders: ignore",
        ]
    return "\n".join(lines)


def _flow(prefix: str, items: tuple[str, ...], width: int = 88) -> list[str]:
    """``prefix`` and a YAML flow list of ``items``, wrapped at ``width`` columns."""
    lines: list[str] = []
    start = f"{prefix}["
    pad = " " * len(start)
    line = start
    for i, item in enumerate(items):
        text = _q(item) + ("]" if i == len(items) - 1 else ",")
        fresh = line in (start, pad)
        joined = line + text if fresh else f"{line} {text}"
        if len(joined) > width and not fresh:
            lines.append(line)
            line = pad + text
        else:
            line = joined
    lines.append(line)
    return lines


def _categories_block(answers: Answers) -> str:
    """Only when a screenshots folder needs its category: the language's default
    categories, written out, with it added."""
    if answers.screenshots is None:
        return ""
    locale = LOCALES[answers.language]
    lines = [
        "",
        "# The language's default categories, plus one for screenshots. A hint tells",
        "# the AI what belongs in a category; rename: false keeps file names as they are.",
        "categories:",
    ]
    for category in locale.categories:
        lines.append(f"  - name: {_q(category.name)}")
        if category.hint:
            lines.append(f"    hint: {_q(category.hint)}")
        if not category.rename:
            lines.append("    rename: false")
    lines += [
        f"  - name: {_q(locale.screenshots_category)}",
        "    rename: false",
    ]
    return "\n".join(lines) + "\n"


def render_config(answers: Answers) -> str:
    """The configuration ``init`` writes: the answers, with a comment on each."""
    command = (
        f"\n  command: {_q(display_path(answers.command))}  # found by `flib init`"
        if answers.command is not None
        else ""
    )
    if answers.base_url is not None:
        command += f"\n  base_url: {_q(answers.base_url)}"
    if answers.provider == "openai-compat":
        command += (
            "\n  # US$ per million tokens; while both are 0 the cost cap below cannot stop a run"
            "\n  price_input_per_mtok: 0"
            "\n  price_output_per_mtok: 0"
        )
    return f"""\
# file-librarian configuration, written by `flib init`.
# Everything not set here has a default; config.example.yaml in the repository
# lists every setting. Check a change with: flib check-config

# Language of generated names and descriptions, and of the default folder and
# category names: en | pt-BR.
language: {answers.language}

# Folders to keep organized. Only their top level is read.
inputs:
{_inputs_block(answers)}

# Category folders are created here. Files flib is unsure about go to
# review_folder, inside it, for you to decide.
output_root: {_q(display_path(answers.output_root))}
review_folder: {_q(answers.review_folder)}

provider:
  # anthropic: the Anthropic API, key in the macOS Keychain (`flib key set`)
  # claude-code: your Claude subscription through the Claude Code CLI, no key
  # ollama: a local model through Ollama, nothing leaves this Mac
  # openai-compat: any OpenAI-compatible API at base_url
  #   (key: `flib key set --provider openai-compat`)
  name: {answers.provider}
  model: {answers.model}{command}

limits:
  max_cost_usd_per_run: 1.00  # estimated; files left over wait for the next run

vision:
  # true sends images (shrunk, without EXIF or GPS metadata) and the first pages
  # of scanned PDFs to the model; false classifies them from name and metadata.
  # Has no effect with claude-code; ollama and openai-compat also need
  # provider.vision: true, and read images only.
  enabled: {"true" if answers.vision else "false"}
{_categories_block(answers)}
schedule:
  time: "12:30"  # the daily run, 24-hour local time; applied by `flib schedule`
"""


def check_answers(answers: Answers) -> Config:
    """Validate the configuration the answers would produce; raises ``ConfigError``."""
    import yaml

    return parse_config(yaml.safe_load(render_config(answers)))


def write_config(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    tmp.replace(path)


# --- More inboxes: the Desktop and screenshots (#89) ---------------------------------


def desktop_folder() -> Path | None:
    """The Desktop, if there is one."""
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else None


def screenshot_folder(timeout: float = 5.0) -> Path | None:
    """Where macOS saves screenshots (``com.apple.screencapture location``).

    Unset, that is the Desktop. ``None`` when ``defaults`` cannot be run or the
    folder it names does not exist. Nothing is written.
    """
    try:
        # A fixed binary and arguments; nothing from the user goes in.
        result = subprocess.run(  # noqa: S603
            [DEFAULTS, "read", "com.apple.screencapture", "location"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        folder = Path.home() / "Desktop"  # the key is unset: the default place
    else:
        raw = result.stdout.strip()
        if not raw:
            return None
        folder = Path(os.path.normpath(Path(raw).expanduser()))
    return folder if folder.is_absolute() and folder.is_dir() else None


def shared_folder(folder: Path) -> bool:
    """``folder`` is the home folder or one of the standard folders in it (Desktop,
    Documents, Pictures, iCloud Drive...): it holds more than screenshots."""
    home = Path.home()
    return any(same_place(folder, home / name) for name in SHARED_FOLDERS)


def count_matching(folder: Path, patterns: tuple[str, ...]) -> int:
    """How many visible items at the top of ``folder`` match ``patterns`` (nothing is read)."""
    try:
        with os.scandir(folder) as entries:
            return sum(
                1
                for entry in entries
                if not entry.name.startswith(".") and matches_any(entry.name, patterns)
            )
    except OSError:
        return 0


# --- The sample ----------------------------------------------------------------------


def _small_unit(root: Path) -> bool:
    """A folder small enough to copy, with nothing inside that is only in iCloud."""
    total = entries = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            for name in [*dirnames, *filenames]:
                st = os.lstat(Path(dirpath, name))
                if hashing.is_dataless(st):
                    return False  # before os.walk lists it, which would download it
                entries += 1
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
                if entries > MAX_UNIT_ENTRIES or total > MAX_ITEM_BYTES:
                    return False
    except OSError:
        return False
    return True


def _sample_time(
    entry: os.DirEntry[str], config: Config, rules: InputConfig, now: float
) -> float | None:
    """The item's modification time if it belongs in a trial, else ``None``.

    The input's own rules hold as in a real run: its ``ignore`` and ``only``
    patterns, its ``folders`` and its ``min_age_days``.
    """
    name = entry.name
    if name.startswith(".") or entry.is_symlink():
        return None
    if matches_any(name, (*IN_PROGRESS_PATTERNS, *config.ignore, *rules.ignore)):
        return None
    if rules.only is not None and not matches_any(name, rules.only):
        return None
    if sensitive_name(name, config.sensitive.allow_names) or withheld_by_config(
        name, config.never_send
    ):
        return None
    path = Path(entry.path)
    if is_within(path, config.output_root):
        return None
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return None
    if st.st_flags & stat.UF_HIDDEN or hashing.is_dataless(st):
        return None
    if item_age(st, now) < rules.min_age_days * DAY:
        return None
    if stat.S_ISREG(st.st_mode):
        return st.st_mtime if 0 < st.st_size <= MAX_ITEM_BYTES else None
    if stat.S_ISDIR(st.st_mode) and config.folders_for(rules) == "as_unit" and _small_unit(path):
        return st.st_mtime
    return None


def pick_sample(config: Config, size: int, *, now: float | None = None) -> list[Path]:
    """Up to ``size`` of the most recent items worth a trial, newest first.

    Inputs with a fixed category are left out: no AI is asked about their files.
    """
    now = time.time() if now is None else now
    found: list[tuple[float, Path]] = []
    for rules in config.inputs:
        if rules.category is not None:
            continue
        try:
            with os.scandir(rules.path) as entries:
                for entry in entries:
                    when = _sample_time(entry, config, rules, now)
                    if when is not None:
                        found.append((when, Path(entry.path)))
        except OSError:
            continue  # a missing or blocked folder: the real run reports it
    found.sort(key=lambda item: (-item[0], str(item[1])))
    return [path for _, path in found[:size]]


def copy_sample(paths: list[Path], into: Path) -> list[Path]:
    """Copy ``paths`` into ``into`` with their dates and extended attributes."""
    copies = []
    for path in paths:
        target = into / path.name
        if target.exists():
            continue  # the same name in two input folders: one is enough
        try:
            if path.is_dir():
                shutil.copytree(path, target, symlinks=True)
            else:
                shutil.copy2(path, target, follow_symlinks=False)
        except OSError:
            shutil.rmtree(target, ignore_errors=True)
            continue
        copies.append(target)
    return copies


# --- The trial -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrialItem:
    name: str
    status: FileStatus
    destination: Path | None
    """Relative to ``output_root``; ``None`` when the item would stay."""
    reason: str


@dataclass(slots=True)
class Trial:
    sampled: int
    items: list[TrialItem] = field(default_factory=list)
    cost_usd: float = 0.0
    deferred: int = 0

    def count(self, status: FileStatus) -> int:
        return sum(1 for item in self.items if item.status is status)

    def by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            if item.status is FileStatus.ORGANIZED and item.destination is not None:
                folder = item.destination.parts[0]
                counts[folder] = counts.get(folder, 0) + 1
        return counts


def trial_config(config: Config, root: Path, inbox: Path, files: int) -> Config:
    """``config`` with every path inside ``root``, so nothing real can be touched."""
    data = config.model_dump(mode="json")
    data["inputs"] = [str(inbox)]
    data["output_root"] = str(root / "library")
    data["database"] = str(root / "index" / "library.db")
    data["limits"] = data["limits"] | {"stable_seconds": 0, "max_files_per_run": max(files, 1)}
    return parse_config(data)


def run_trial(config: Config, provider: Provider, sample: list[Path]) -> Trial:
    """Simulate a run over copies of ``sample`` in a temporary folder, then delete it."""
    with tempfile.TemporaryDirectory(prefix="flib-init-") as tmp:
        root = Path(tmp)
        inbox = root / "sample"
        inbox.mkdir()
        copies = copy_sample(sample, inbox)
        cfg = trial_config(config, root, inbox, len(copies))
        outcome = pipeline.run(cfg, provider, simulate=True)
        items = [
            TrialItem(
                item.context.path.name,
                item.status,
                item.destination.relative_to(cfg.output_root) if item.destination else None,
                item.reason,
            )
            for item in outcome.plan.items
        ]
        return Trial(len(copies), items, outcome.cost_usd, outcome.deferred)
