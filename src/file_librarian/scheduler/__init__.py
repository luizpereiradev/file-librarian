"""Daily run as a launchd agent (RF-19).

``install`` writes ``~/Library/LaunchAgents/<LABEL>.plist`` and loads it. The
agent runs ``<python> -m file_librarian run --trigger schedule`` once a day at
``schedule.time`` with ``FLIB_CONFIG`` pinned to the configuration in use. If
the Mac is asleep at that time, launchd runs it once on wake; if it was off,
the next day's run picks up everything still unprocessed.

``check_access`` answers the question users cannot easily see: can a
*scheduled* process read the input folders? macOS privacy settings (TCC)
apply per binary and are silent for background agents. It loads a one-shot
agent that runs ``check-access`` in exactly the scheduled context and reports
back through a file.

All ``launchctl`` calls go through ``LAUNCHCTL`` (tests swap in a fake).
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

LABEL = "io.github.luizpereiradev.file-librarian"
CHECK_LABEL = f"{LABEL}.check"
LAUNCHCTL = "/bin/launchctl"
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class ScheduleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Status:
    installed: bool
    loaded: bool
    time: str | None
    program: list[str]


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str = LABEL) -> Path:
    return agents_dir() / f"{label}.plist"


def program() -> list[str]:
    """How the agent starts flib: this very interpreter, so it matches what was installed."""
    return [sys.executable, "-m", "file_librarian"]


def is_installed_tool() -> bool:
    """Running from a ``uv tool`` install, not a development checkout."""
    return (Path(sys.prefix) / "uv-receipt.toml").exists()


def build_plist(at: str, config_path: Path, logs: Path) -> dict[str, object]:
    hour, minute = (int(part) for part in at.split(":"))
    return {
        "Label": LABEL,
        "ProgramArguments": [*program(), "run", "--trigger", "schedule"],
        "EnvironmentVariables": {"FLIB_CONFIG": str(config_path), "PATH": SAFE_PATH},
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(logs / "schedule.out.log"),
        "StandardErrorPath": str(logs / "schedule.err.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        # Fixed binary; arguments are flib's own label and paths.
        return subprocess.run(  # noqa: S603
            [LAUNCHCTL, *args], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScheduleError(f"cannot run {LAUNCHCTL}: {exc}") from exc


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _load(path: Path, label: str) -> None:
    _launchctl("bootout", f"{_domain()}/{label}")  # not loaded yet is fine
    result = _launchctl("bootstrap", _domain(), str(path))
    if result.returncode != 0:
        raise ScheduleError(f"launchctl could not load {path.name}: {result.stderr.strip()}")


def _write_plist(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(plistlib.dumps(data))
    tmp.replace(path)


def install(at: str, config_path: Path, logs: Path) -> Path:
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = plist_path()
    _write_plist(path, build_plist(at, config_path, logs))
    _load(path, LABEL)
    return path


def remove() -> bool:
    """Unload and delete the agent. ``False`` if it was not installed."""
    path = plist_path()
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    if not path.exists():
        return False
    path.unlink()
    return True


def status() -> Status:
    path = plist_path()
    if not path.exists():
        return Status(False, False, None, [])
    data = plistlib.loads(path.read_bytes())
    interval = data.get("StartCalendarInterval", {})
    at = f"{interval.get('Hour', 0):02d}:{interval.get('Minute', 0):02d}"
    loaded = _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0
    return Status(True, loaded, at, list(data.get("ProgramArguments", [])))


def check_access(config_path: Path, *, timeout: float = 20.0) -> dict[str, str]:
    """Run ``check-access`` as a launchd agent and return {folder: "ok" | error}."""
    path = plist_path(CHECK_LABEL)
    result_file = path.with_suffix(".result.json")
    result_file.unlink(missing_ok=True)
    data = {
        "Label": CHECK_LABEL,
        "ProgramArguments": [*program(), "check-access", "--out", str(result_file)],
        "EnvironmentVariables": {"FLIB_CONFIG": str(config_path), "PATH": SAFE_PATH},
        "RunAtLoad": True,
        "ProcessType": "Background",
    }
    _write_plist(path, data)
    try:
        _load(path, CHECK_LABEL)
        deadline = time.monotonic() + timeout
        while not result_file.exists():
            if time.monotonic() > deadline:
                raise ScheduleError("the access check did not report back in time")
            time.sleep(0.2)
        report: dict[str, str] = json.loads(result_file.read_text(encoding="utf-8"))
        return report
    finally:
        _launchctl("bootout", f"{_domain()}/{CHECK_LABEL}")
        path.unlink(missing_ok=True)
        result_file.unlink(missing_ok=True)


def probe_inputs(inputs: list[Path]) -> dict[str, str]:
    """What the scheduled context sees: can each input folder be listed?"""
    report = {}
    for folder in inputs:
        try:
            with os.scandir(folder) as entries:
                next(entries, None)
            report[str(folder)] = "ok"
        except PermissionError:
            report[str(folder)] = "denied by macOS privacy settings"
        except OSError as exc:
            report[str(folder)] = f"error: {exc.strerror or exc}"
    return report
