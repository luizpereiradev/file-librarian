"""``flib init``: everything in the guided first run except the questions (RF-38).

The prompts live in ``cli.py``; this module holds what can be tested without
them:

- ``detect_language`` and ``Answers``/``render_config``/``write_config``: the
  configuration ``init`` writes, short and commented, mode 600;
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
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian import hashing, pipeline
from file_librarian.config import Config, is_within, parse_config
from file_librarian.globs import matches_any
from file_librarian.locales import DEFAULT_LANGUAGE, LOCALES, Language
from file_librarian.models import FileStatus
from file_librarian.providers.base import Provider
from file_librarian.scanner import IN_PROGRESS_PATTERNS
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

    @property
    def model(self) -> str:
        return self.model_name or DEFAULT_MODELS.get(self.provider, "offline")


def _q(value: str) -> str:
    """A YAML double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(value, ensure_ascii=False)


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
  - {_q(display_path(answers.input_folder))}

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


def _sample_time(entry: os.DirEntry[str], config: Config) -> float | None:
    """The item's modification time if it belongs in a trial, else ``None``."""
    name = entry.name
    if name.startswith(".") or entry.is_symlink():
        return None
    if matches_any(name, (*IN_PROGRESS_PATTERNS, *config.ignore)):
        return None
    if sensitive_name(name) or withheld_by_config(name, config.never_send):
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
    if stat.S_ISREG(st.st_mode):
        return st.st_mtime if 0 < st.st_size <= MAX_ITEM_BYTES else None
    if stat.S_ISDIR(st.st_mode) and config.folders == "as_unit" and _small_unit(path):
        return st.st_mtime
    return None


def pick_sample(config: Config, size: int) -> list[Path]:
    """Up to ``size`` of the most recent items worth a trial, newest first."""
    found: list[tuple[float, Path]] = []
    for folder in config.inputs:
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    when = _sample_time(entry, config)
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
