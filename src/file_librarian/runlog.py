"""One log file per run, next to the database (RF-18).

Logs stay on this Mac and contain file names and paths, so they are written
with owner-only permissions. Only the newest ``KEEP`` logs are kept; older
log files written by flib are the one thing it removes, and only inside its
own logs folder. Watch-mode runs (#90) write ``watch-run-*.log`` and keep
their own ``KEEP_WATCH`` newest, so many small watch runs never push the
daily runs' logs out. ``trim`` caps the size of the launchd output logs.

A log is English whatever the configuration's language (#141): each line is written
with ``i18n.english``, so a run printed in pt-BR logs the same lines in English, as
maintainers and ``status.count_skips`` read them.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from file_librarian.i18n import english

KEEP = 100
KEEP_WATCH = 100
_PREFIX = "run-"
_WATCH_PREFIX = "watch-run-"
OUTPUT_LOG_MAX_BYTES = 1_000_000
"""``trim`` keeps a launchd output log (``watch.out.log``...) under about this size."""


def logs_dir(database: Path) -> Path:
    return database.parent / "logs"


def log_path(database: Path, run_id: int) -> Path:
    """The log of run ``run_id``, a watch run's or another's (the watch one if it exists)."""
    folder = logs_dir(database)
    watched = folder / f"{_WATCH_PREFIX}{run_id:06d}.log"
    return watched if watched.exists() else folder / f"{_PREFIX}{run_id:06d}.log"


def write_log(database: Path, run_id: int, lines: list[str], *, watch: bool = False) -> Path:
    folder = logs_dir(database)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    prefix = _WATCH_PREFIX if watch else _PREFIX
    path = folder / f"{prefix}{run_id:06d}.log"
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"# flib run {run_id} finished {stamp}\n")
        fh.writelines(english(line) + "\n" for line in lines)  # a log is English (#141)
    _prune(folder, prefix, KEEP_WATCH if watch else KEEP)
    return path


def append_log(database: Path, run_id: int, lines: list[str]) -> None:
    """Add lines to the log of a run that already finished (what happened after it)."""
    path = log_path(database, run_id)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.writelines(english(line) + "\n" for line in lines)  # a log is English (#141)


def trim(path: Path, max_bytes: int = OUTPUT_LOG_MAX_BYTES) -> bool:
    """Keep the newest half of ``path`` when it grew past ``max_bytes``; ``True`` if trimmed.

    Rewritten in place, so a process appending to it (launchd's output file is
    opened in append mode) goes on writing at its new end.
    """
    try:
        if path.is_symlink() or path.stat().st_size <= max_bytes:
            return False
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except OSError:
        return False
    with os.fdopen(fd, "r+b") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - max_bytes // 2))
        tail = fh.read()
        cut = tail.find(b"\n")
        tail = tail[cut + 1 :] if cut >= 0 else tail
        fh.seek(0)
        fh.write(tail)
        fh.truncate()
    return True


def _prune(folder: Path, prefix: str, keep: int) -> None:
    logs = sorted(p for p in folder.iterdir() if p.name.startswith(prefix) and p.suffix == ".log")
    for old in logs[:-keep]:
        if old.is_file() and not old.is_symlink():
            old.unlink()
