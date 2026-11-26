"""Daily run and watch mode as launchd agents (RF-19, #90).

``install`` writes ``~/Library/LaunchAgents/<LABEL>.plist`` and loads it. The
agent runs ``<python> -m file_librarian run --trigger schedule`` once a day at
``schedule.time`` with ``FLIB_CONFIG`` pinned to the configuration in use. If
the Mac is asleep at that time, launchd runs it once on wake; if it was off,
the next day's run picks up everything still unprocessed.

Watch mode (``schedule.mode: watch`` or ``both``) is a second agent,
``<WATCH_LABEL>.plist``: launchd starts ``run --trigger watch`` when an input
folder changes (``WatchPaths``, the folder's own entries only: launchd does
not watch sub-folders), at most once every
``schedule.watch_min_interval_minutes`` (``ThrottleInterval``). Two agents
rather than one with both keys, so a daily run waiting hours for an unlock
never holds back the watch agent (launchd runs one process per agent), and
each shows and goes away on its own. ``install`` puts in place the agents of
the mode and removes the other; ``remove`` removes both. What a watch run
does is in ``scheduler.watch``.

``check_access`` answers the question users cannot easily see: can a
*scheduled* process read the input folders? macOS privacy settings (TCC)
apply per binary and are silent for background agents. It loads a one-shot
agent that runs ``check-access`` in exactly the scheduled context and reports
back through a file.

**A dedicated interpreter.** macOS grants folder access per executable, and
the Python inside a uv environment is a link to an interpreter shared by every
project using that Python version. Granting Full Disk Access to it would open
your files to all of them. So the agent runs ``flib-python``: a real copy of
the interpreter placed inside flib's own environment (``<env>/bin``), where
Python recognises the environment by itself. Access granted to it covers flib
only. ``prepare_interpreter`` refreshes the copy when the interpreter changes;
reinstalling flib recreates the environment, so ``scripts/install.sh`` runs
``flib schedule --refresh`` afterwards.

The menu bar item (#116, ``menubar``) is one more agent, ``<MENU_LABEL>.plist``,
installed and removed on its own through ``install_agent`` and ``remove_agent``;
``install`` and ``remove`` never touch it. They do tell it the schedule changed
(``menubar_state.changed``), since its menu says when the next run is.

All ``launchctl`` calls go through ``LAUNCHCTL`` (tests swap in a fake).
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from file_librarian.config import Config

LABEL = "io.github.luizpereiradev.file-librarian"
CHECK_LABEL = f"{LABEL}.check"
WATCH_LABEL = f"{LABEL}.watch"
MENU_LABEL = f"{LABEL}.menu"
MODES = ("daily", "watch", "both")
LAUNCHCTL = "/bin/launchctl"
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
INTERPRETER_NAME = "flib-python"
ENV_ROOT: Path | None = None
"""The environment the dedicated interpreter goes into; ``None`` = this one (tests override)."""


class ScheduleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Status:
    installed: bool
    """Any of flib's agents is installed."""
    loaded: bool
    """Every installed agent is loaded."""
    time: str | None
    """The daily run's time; ``None`` without the daily agent."""
    program: list[str]
    interpreter_current: bool = True
    """False when the copy no longer matches the interpreter flib runs on."""
    mode: str | None = "daily"
    """``daily``, ``watch`` or ``both``, from the agents installed; ``None`` with none."""
    watch_paths: tuple[str, ...] = ()
    watch_interval_minutes: int | None = None
    watch_fallback_minutes: int | None = None
    """The watch agent's ``StartInterval`` in minutes; ``None`` without one."""


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str = LABEL) -> Path:
    return agents_dir() / f"{label}.plist"


def dedicated_interpreter() -> Path:
    return (ENV_ROOT or Path(sys.prefix)) / "bin" / INTERPRETER_NAME


def _source_interpreter() -> Path:
    return Path(os.path.realpath(sys.executable))


def interpreter_is_current() -> bool:
    target = dedicated_interpreter()
    try:
        return target.read_bytes() == _source_interpreter().read_bytes()
    except OSError:
        return False


def prepare_interpreter() -> Path:
    """Create or refresh flib's own copy of the interpreter; returns its path."""
    if ENV_ROOT is None and sys.prefix == sys.base_prefix:
        raise ScheduleError(
            "flib is not running from its own environment; install it with ./scripts/install.sh"
        )
    target = dedicated_interpreter()
    if not interpreter_is_current():
        tmp = target.with_name(f".{INTERPRETER_NAME}.tmp")
        shutil.copy2(_source_interpreter(), tmp)
        tmp.chmod(0o755)
        tmp.replace(target)
    return target


def program() -> list[str]:
    """How the agent starts flib: the dedicated interpreter (see the module docstring)."""
    return [str(dedicated_interpreter()), "-m", "file_librarian"]


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


def build_watch_plist(
    inputs: list[Path],
    config_path: Path,
    logs: Path,
    min_interval_minutes: int,
    fallback_minutes: int = 0,
) -> dict[str, object]:
    """``fallback_minutes``: also start every that many minutes (``StartInterval``), for
    what ``WatchPaths`` cannot see (a change made while flib was running); 0 = never."""
    data: dict[str, object] = {
        "Label": WATCH_LABEL,
        "ProgramArguments": [*program(), "run", "--trigger", "watch"],
        "EnvironmentVariables": {"FLIB_CONFIG": str(config_path), "PATH": SAFE_PATH},
        "WatchPaths": [str(folder) for folder in inputs],
        "ThrottleInterval": min_interval_minutes * 60,
        "StandardOutPath": str(logs / "watch.out.log"),
        "StandardErrorPath": str(logs / "watch.err.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
    }
    if fallback_minutes:
        data["StartInterval"] = fallback_minutes * 60
    return data


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


def install(
    at: str,
    config_path: Path,
    logs: Path,
    *,
    mode: str = "daily",
    inputs: list[Path] | None = None,
    watch_interval_minutes: int = 5,
    watch_fallback_minutes: int = 0,
) -> list[Path]:
    """Install the agents of ``mode`` and remove the other one; returns their plists."""
    if mode not in MODES:
        raise ScheduleError(f"unknown schedule mode {mode!r}; use daily, watch or both")
    if mode != "daily" and not inputs:
        raise ScheduleError("watch mode needs at least one input folder")
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    prepare_interpreter()
    installed = []
    if mode in ("daily", "both"):
        path = plist_path()
        _write_plist(path, build_plist(at, config_path, logs))
        _load(path, LABEL)
        installed.append(path)
    else:
        _remove(LABEL)
    if mode in ("watch", "both"):
        path = plist_path(WATCH_LABEL)
        data = build_watch_plist(
            inputs or [], config_path, logs, watch_interval_minutes, watch_fallback_minutes
        )
        _write_plist(path, data)
        _load(path, WATCH_LABEL)
        installed.append(path)
    else:
        _remove(WATCH_LABEL)
    _schedule_changed()
    return installed


def install_for(config_path: Path, config: Config, at: str, mode: str) -> list[Path]:
    """``install`` for the configuration at ``config_path``, as ``flib schedule`` and the
    interface's Settings page (#117) both do it; raises ``ScheduleError``."""
    from file_librarian.runlog import logs_dir

    return install(
        at,
        config_path.resolve(),
        logs_dir(config.database),
        mode=mode,
        inputs=config.input_paths,
        watch_interval_minutes=config.schedule.watch_min_interval_minutes,
        watch_fallback_minutes=config.schedule.watch_fallback_minutes,
    )


def remove() -> bool:
    """Unload and delete every agent. ``False`` if none was installed."""
    daily = _remove(LABEL)
    watching = _remove(WATCH_LABEL)
    _schedule_changed()
    return daily or watching


def _schedule_changed() -> None:
    """The menu bar item shows the next run: tell it (never fails, see ``menubar_state``)."""
    from file_librarian import menubar_state

    menubar_state.changed(probe=True)  # whether the agents are loaded, really


# --- one agent, for the menu bar item (#116) ----------------------------------------------


def install_agent(label: str, data: dict[str, object]) -> Path:
    """Write the agent ``label`` and (re)load it; returns its plist. Raises ``ScheduleError``."""
    path = plist_path(label)
    _write_plist(path, data)
    _load(path, label)
    return path


def remove_agent(label: str) -> bool:
    """Unload and delete the agent ``label``. ``False`` if it was not installed."""
    return _remove(label)


def read_agent(label: str) -> dict[str, Any] | None:
    """The agent's plist as installed; ``None`` without one (or one that cannot be read)."""
    try:
        return _read(label)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


_RUNNING = re.compile(r"^\s*state = running\s*$", re.MULTILINE)


def agent_state(label: str) -> tuple[bool, bool]:
    """Whether launchd has the agent loaded, and whether its process runs now: one
    ``launchctl print``."""
    result = _launchctl("print", f"{_domain()}/{label}")
    loaded = result.returncode == 0
    return loaded, loaded and _RUNNING.search(result.stdout) is not None


def kickstart(label: str, *, restart: bool = False) -> None:
    """Start the loaded agent ``label`` now (``restart``: kill a running one first, so it
    starts again from a rebuilt bundle). Raises ``ScheduleError``."""
    args = ["kickstart", *(["-k"] if restart else []), f"{_domain()}/{label}"]
    result = _launchctl(*args)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise ScheduleError(f"launchctl could not start {label}: {detail}")


def _remove(label: str) -> bool:
    path = plist_path(label)
    _launchctl("bootout", f"{_domain()}/{label}")  # not loaded is fine
    if not path.exists():
        return False
    path.unlink()
    return True


def _read(label: str) -> dict[str, Any] | None:
    path = plist_path(label)
    if not path.exists():
        return None
    data: dict[str, Any] = plistlib.loads(path.read_bytes())
    return data


def _is_loaded(label: str) -> bool:
    return _launchctl("print", f"{_domain()}/{label}").returncode == 0


def daily_agent_loaded() -> bool:
    """The daily agent is installed and launchd has it loaded."""
    return plist_path().exists() and _is_loaded(LABEL)


def status(*, probe: bool = True) -> Status:
    """The installed agents. ``probe=False`` reads their plists only: no ``launchctl``
    (``loaded`` is then assumed) and no comparison of the interpreter copy, for the
    menu bar item's state, which is computed on every write to the library."""
    daily, watching = _read(LABEL), _read(WATCH_LABEL)
    if daily is None and watching is None:
        return Status(False, False, None, [], mode=None)
    agents = [(LABEL, daily), (WATCH_LABEL, watching)]
    loaded = not probe or all(_is_loaded(label) for label, data in agents if data is not None)
    at = None
    if daily is not None:
        interval = daily.get("StartCalendarInterval", {})
        at = f"{interval.get('Hour', 0):02d}:{interval.get('Minute', 0):02d}"
    paths: tuple[str, ...] = ()
    interval_minutes = fallback = None
    if watching is not None:
        paths = tuple(str(p) for p in watching.get("WatchPaths", []))
        interval_minutes = int(watching.get("ThrottleInterval", 60)) // 60
        if watching.get("StartInterval"):
            fallback = int(watching["StartInterval"]) // 60
    mode = "both" if daily and watching else ("daily" if daily else "watch")
    main = daily if daily is not None else watching or {}
    return Status(
        True,
        loaded,
        at,
        list(main.get("ProgramArguments", [])),
        interpreter_is_current() if probe else True,
        mode,
        paths,
        interval_minutes,
        fallback,
    )


def stale_watch_message(state: Status, inputs: list[Path]) -> str | None:
    """launchd's ``WatchPaths`` are fixed when the agent is installed: after the inputs
    change, it watches the old ones until ``flib schedule --refresh``."""
    if not state.watch_paths or set(state.watch_paths) == {str(p) for p in inputs}:
        return None
    return (
        "the watch agent watches other folders than the configuration's inputs; "
        "run `flib schedule --refresh`"
    )


def scheduled_config() -> Path | None:
    """The configuration path the installed agents use, if any."""
    data = _read(LABEL) or _read(WATCH_LABEL)
    if data is None:
        return None
    value = data.get("EnvironmentVariables", {}).get("FLIB_CONFIG")
    return Path(value) if value else None


def check_access(config_path: Path, *, timeout: float = 20.0) -> dict[str, str]:
    """Run ``check-access`` as a launchd agent and return {folder: "ok" | error}."""
    prepare_interpreter()
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
