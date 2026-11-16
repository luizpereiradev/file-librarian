"""One log file per run, next to the database (RF-18).

Logs stay on this Mac and contain file names and paths, so they are written
with owner-only permissions. Only the newest ``KEEP`` logs are kept; older
log files written by flib are the one thing it removes, and only inside its
own logs folder.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

KEEP = 100
_PREFIX = "run-"


def logs_dir(database: Path) -> Path:
    return database.parent / "logs"


def write_log(database: Path, run_id: int, lines: list[str]) -> Path:
    folder = logs_dir(database)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f"{_PREFIX}{run_id:06d}.log"
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(f"# flib run {run_id} finished {stamp}\n")
        fh.writelines(line + "\n" for line in lines)
    _prune(folder)
    return path


def _prune(folder: Path) -> None:
    logs = sorted(p for p in folder.iterdir() if p.name.startswith(_PREFIX) and p.suffix == ".log")
    for old in logs[:-KEEP]:
        if old.is_file() and not old.is_symlink():
            old.unlink()
