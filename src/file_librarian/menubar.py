"""flib in the menu bar (#116, ADR 0004): an AppleScriptObjC stay-open applet.

The item shows the cat in the pose for flib's state (``poses``), with a small
count, and a short menu whose main job is to open ``flib ui`` on the right
page. It is built like the notifier (``notifier``), with tools that ship with
macOS and nothing else:

1. ``script`` fills in the template (``assets/menubar.applescript``): the
   state folder, the interpreter and the configuration are fixed there, at
   build time, as AppleScript string
   literals (``literal`` escapes them; anything outside printable ASCII
   becomes ``character id N``), and the menu's actions come from ``ACTIONS``;
2. ``osacompile -s`` compiles it into ``file-librarian-menu.app`` (stay open),
   its ``Info.plist`` gets the bundle identifier and ``LSUIElement`` (no Dock
   icon), the pose icons (``assets/menu/*.svg``) go into ``Resources``;
3. a stamp records what it was built from; ``codesign --sign -`` signs it ad
   hoc and ``codesign --verify`` checks it; it is built in a temporary folder
   and moved into place whole, then registered with Launch Services.

It lives next to the notifier in flib's environment (``<env>/bin``) and is
started at login by the LaunchAgent ``MENU_LABEL`` (``RunAtLoad``; back after
a crash, off after Quit until the next login), which ``install`` writes
through the scheduler's functions. ``refresh`` rebuilds it when the stamp no
longer matches (``scripts/install.sh`` and ``flib schedule --refresh`` call it)
and restarts the agent; ``remove`` undoes everything.

**What the applet trusts.** Nothing it reads. ``state.json`` (written by
``menubar_state``, in ``folder()``) is checked key by key before anything is
shown, and only text and counts come from it. A click runs one program, the
interpreter fixed at build time (``scheduler.dedicated_interpreter``, checked
here to be a real executable in flib's environment), with ``-I`` (no current
folder or ``PYTHON*`` variable on its path) and the arguments of one
``ACTIONS`` entry plus ``--config <fixed path>``: no shell, no lookup in
``PATH``, nothing from the state file. Its output goes to ``/dev/null``. The
applet writes one file, ``seen.json``: what it shows and its pid, which
``flib doctor`` reads (as untrusted, like everything the applet writes).

All external tools are the notifier's module constants (tests swap in fakes).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from file_librarian import notifier, poses, scheduler
from file_librarian.config import APP_NAME as SUPPORT_NAME

APP_NAME = "file-librarian-menu"
DISPLAY_NAME = "file-librarian"
BUNDLE_ID = "io.github.luizpereiradev.file-librarian.menu"
LABEL = scheduler.MENU_LABEL
APP_VERSION = "1"
"""Bump when the bundle's layout changes, so installed apps are rebuilt."""
STAMP = "flib-menu.json"
STATE_NAME = "state.json"
TARGET_NAME = "target.json"
SEEN_NAME = "seen.json"
PROBLEM_NAME = "problem.json"
IDLE_SECONDS = 30
"""The applet re-reads the state file this often (only when it changed)."""

# --- the contract of state.json: flib writes it, the applet checks it -------------------

STATE_VERSION = 1
MAX_STATE_BYTES = 4096
MAX_TEXT = 80
"""Longest ``line`` or ``next`` the applet shows, in UTF-16 code units (NSString length)."""
MAX_COUNT = 9999
STATE_TEXTS = ("line", "next")
STATE_COUNTS = ("count", "review", "due")
STATE_KEYS = frozenset({"version", "pose", "running", *STATE_TEXTS, *STATE_COUNTS})
DEFAULT_LINE = "No word from flib yet"
DEFAULT_POSE = poses.Pose.TROUBLE
TEXT_PATTERN = r"[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]"
"""Characters that make a text refused (controls, format and bidi marks, line breaks)."""


@dataclass(frozen=True, slots=True)
class Action:
    selector: str
    """The AppleScript handler (an Objective-C selector) the menu item calls."""
    title: str
    arguments: tuple[str, ...]
    """After ``<interpreter> -I -m file_librarian``; ``--config <path>`` follows."""


ACTIONS = (
    Action("openStatus:", "Open flib", ("ui", "--open", "status")),
    Action("openReview:", "Review", ("ui", "--open", "review")),
    Action("openDue:", "Due", ("ui", "--open", "due")),
    Action("organize:", "Organize now", ("menu", "--organize")),
    Action("openSettings:", "Settings…", ("ui", "--open", "settings")),
)
"""Everything a click can run. Fixed words only (checked by ``script``)."""
PREFIX = ("-I", "-m", "file_librarian")


class MenuError(RuntimeError):
    pass


# --- where things are -------------------------------------------------------------------


def folder() -> Path:
    """flib's own support folder for the item: the state the applet reads and the
    configuration it was installed for. Fixed, so the applet can be built with it."""
    return Path.home() / "Library" / "Application Support" / SUPPORT_NAME / "menu"


def state_path() -> Path:
    return folder() / STATE_NAME


def app_path() -> Path:
    return scheduler.dedicated_interpreter().parent / f"{APP_NAME}.app"


def executable() -> Path:
    return app_path() / "Contents" / "MacOS" / "applet"


@dataclass(frozen=True, slots=True)
class Target:
    """What the item was installed for (``target.json``, written by ``install``)."""

    config: Path
    database: Path


def read_target() -> Target | None:
    try:
        data = json.loads((folder() / TARGET_NAME).read_text(encoding="utf-8"))
        return Target(Path(data["config"]), Path(data["database"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_private(path: Path, data: bytes) -> None:
    """Atomically: a private temporary file in the same folder (0700), renamed over."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)  # 0600
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


# --- the script -------------------------------------------------------------------------

_WORD = re.compile(r"(--)?[a-z][a-z-]*")
_SELECTOR = re.compile(r"[a-z][A-Za-z]*:")


def literal(text: str) -> str:
    """``text`` as an AppleScript expression that evaluates to exactly it.

    Printable ASCII goes in a string literal with ``\\`` and ``"`` escaped;
    every other character (a control, a line break, any non-ASCII letter)
    becomes ``(character id N)``, so nothing depends on how ``osacompile``
    decodes the source and nothing can end the literal early."""
    parts: list[str] = []
    run: list[str] = []
    for char in text:
        if " " <= char <= "~":
            run.append("\\" + char if char in '\\"' else char)
            continue
        if run:
            parts.append('"' + "".join(run) + '"')
            run = []
        parts.append(f"(character id {ord(char)})")
    if run or not parts:
        parts.append('"' + "".join(run) + '"')
    return " & ".join(parts)


def _list(items: list[str]) -> str:
    return "{" + ", ".join(literal(item) for item in items) + "}"


def validate_program(program: Path) -> Path:
    """The interpreter the applet will run: absolute, a regular executable file (not a
    link), and flib's own copy. Raises ``MenuError``."""
    if not program.is_absolute() or program != scheduler.dedicated_interpreter():
        raise MenuError(f"{program} is not flib's own interpreter")
    try:
        info = program.lstat()
    except OSError as exc:
        raise MenuError(f"flib's interpreter is missing ({exc.strerror or exc})") from exc
    if not stat.S_ISREG(info.st_mode) or not os.access(program, os.X_OK):
        raise MenuError(f"{program} is not an executable file")
    return program


def _handlers() -> str:
    out = []
    for action in ACTIONS:
        if not _SELECTOR.fullmatch(action.selector) or not all(
            _WORD.fullmatch(word) for word in action.arguments
        ):
            raise MenuError(f"refusing an action with unexpected words: {action}")
        out.append(
            f"on {action.selector}sender\n"
            f"\ttry\n"
            f"\t\tmy launchFlib({_list(list(action.arguments))})\n"
            f"\tend try\n"
            f"end {action.selector}\n"
        )
    return "\n".join(out)


def _title(selector: str) -> str:
    return next(action.title for action in ACTIONS if action.selector == selector)


def script(program: Path, config: Path, state_dir: Path) -> str:
    """The applet's source for these fixed paths (see ``TEMPLATE_NAME``)."""
    values = {
        "STATE_DIR": literal(str(state_dir)),
        "PROGRAM": literal(str(program)),
        "CONFIG": literal(str(config)),
        "PREFIX": _list(list(PREFIX)),
        "IDLE": str(IDLE_SECONDS),
        "MAX_BYTES": str(MAX_STATE_BYTES),
        "MAX_TEXT": str(MAX_TEXT),
        "MAX_COUNT": str(MAX_COUNT),
        "VERSION": str(STATE_VERSION),
        "POSES": _list([str(pose) for pose in poses.Pose]),
        "KEYS": _list(sorted(STATE_KEYS)),
        "TEXT_PATTERN": literal(TEXT_PATTERN),
        "DEFAULT_POSE": literal(str(DEFAULT_POSE)),
        "DEFAULT_LINE": literal(DEFAULT_LINE),
        "TITLE_STATUS": literal(_title("openStatus:")),
        "TITLE_REVIEW": literal(_title("openReview:")),
        "TITLE_DUE": literal(_title("openDue:")),
        "TITLE_ORGANIZE": literal(_title("organize:")),
        "TITLE_SETTINGS": literal(_title("openSettings:")),
        "AUTOSAVE": literal(BUNDLE_ID),
        "HANDLERS": _handlers(),
    }
    return re.sub(r"@([A-Z_]+)@", lambda m: values[m.group(1)], template())


TEMPLATE_NAME = "menubar.applescript"
"""The applet's source with ``@NAME@`` placeholders (in ``assets/``), filled in by
``script``."""


def template() -> str:
    return (
        resources.files("file_librarian")
        .joinpath("assets", TEMPLATE_NAME)
        .read_text(encoding="utf-8")
    )


# --- the bundle ---------------------------------------------------------------------------


def icons() -> dict[str, bytes]:
    return {f"{pose}.svg": poses.icon(pose).read_bytes() for pose in poses.Pose}


def expected_stamp(source: str) -> dict[str, str]:
    """What a current app was built from: a change to any of it means a rebuild."""
    digest = hashlib.sha256()
    for name, data in sorted(icons().items()):
        digest.update(name.encode() + b"\0" + data)
    return {
        "version": APP_VERSION,
        "bundle_id": BUNDLE_ID,
        "script": hashlib.sha256(source.encode()).hexdigest(),
        "icons": digest.hexdigest(),
    }


def _read_stamp(app: Path) -> dict[str, str] | None:
    try:
        data = json.loads((app / "Contents" / "Resources" / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def source_for(config: Path) -> str:
    return script(scheduler.dedicated_interpreter(), config, folder())


def problem(config: Path) -> str | None:
    """Why the app cannot be used as it is for ``config``; ``None`` when it is ready."""
    app = app_path()
    if not app.exists():
        return "the menu bar app is not built"
    if not executable().is_file():
        return "the menu bar app is incomplete"
    if _read_stamp(app) != expected_stamp(source_for(config)):
        return "the menu bar app is out of date"
    return None


def missing_tools() -> list[str]:
    return [tool for tool in (notifier.OSACOMPILE, notifier.CODESIGN) if not Path(tool).exists()]


def _edit_info(app: Path) -> None:
    info = app / "Contents" / "Info.plist"
    data = plistlib.loads(info.read_bytes())
    for key in ["CFBundleIconName", *(k for k in data if k.endswith("UsageDescription"))]:
        data.pop(key, None)
    data.update(
        {
            "CFBundleName": DISPLAY_NAME,
            "CFBundleDisplayName": DISPLAY_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleIconFile": "applet",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSUIElement": True,
        }
    )
    info.write_bytes(plistlib.dumps(data))
    (app / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)


def build(config: Path) -> Path:
    """Build (or rebuild) the app for ``config``; returns its path. Raises ``MenuError``."""
    missing = missing_tools()
    if missing:
        raise MenuError(f"{', '.join(missing)} not found (the menu bar app is built with it)")
    program = validate_program(scheduler.dedicated_interpreter())
    source = script(program, config, folder())
    target = app_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".menu-", dir=target.parent) as tmp:
            work = Path(tmp)
            (work / "menu.applescript").write_text(source, encoding="utf-8")
            app = work / target.name
            notifier.run_tool(notifier.OSACOMPILE, "-s", "-o", str(app),
                              str(work / "menu.applescript"))  # fmt: skip
            if not (app / "Contents" / "Info.plist").is_file():
                raise MenuError("osacompile made no app bundle")
            _edit_info(app)
            resources_dir = app / "Contents" / "Resources"
            for name, data in icons().items():
                (resources_dir / name).write_bytes(data)
            stamp = json.dumps(expected_stamp(source), indent=2) + "\n"
            (resources_dir / STAMP).write_text(stamp, encoding="utf-8")
            notifier.run_tool(notifier.CODESIGN, "--force", "--deep", "--sign", "-", str(app))
            notifier.run_tool(notifier.CODESIGN, "--verify", "--deep", "--strict", str(app))
            if target.exists():
                shutil.rmtree(target)
            app.rename(target)
    except notifier.NotifierError as exc:
        raise MenuError(str(exc)) from exc
    notifier.lsregister("-f", target)
    return target


# --- the agent ----------------------------------------------------------------------------


def build_plist(config: Path) -> dict[str, object]:
    """Started at login; back after a crash, off after Quit (a clean exit) until the next
    login; ``Interactive``, as it draws UI. Its output, and its children's, is discarded."""
    return {
        "Label": LABEL,
        "Program": str(executable()),
        "ProgramArguments": [str(executable())],
        "EnvironmentVariables": {"FLIB_CONFIG": str(config), "PATH": scheduler.SAFE_PATH},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
        "StandardOutPath": os.devnull,
        "StandardErrorPath": os.devnull,
    }


def installed() -> bool:
    return scheduler.plist_path(LABEL).exists()


def install(config: Path, database: Path) -> Path:
    """Build the app for ``config`` (resolved), write what it is for, write its first
    state and load its agent, which starts it. Raises ``MenuError``."""
    config = config.resolve()
    try:
        scheduler.prepare_interpreter()
    except (scheduler.ScheduleError, OSError) as exc:
        raise MenuError(str(exc)) from exc
    target = json.dumps({"config": str(config), "database": str(database)}, indent=2)
    write_private(folder() / TARGET_NAME, target.encode() + b"\n")
    if problem(config) is not None:
        build(config)
    from file_librarian import menubar_state

    menubar_state.changed()
    try:
        return scheduler.install_agent(LABEL, build_plist(config))
    except scheduler.ScheduleError as exc:
        raise MenuError(str(exc)) from exc


def start() -> None:
    """Show it again after Quit: start the loaded agent (a no-op while it runs)."""
    try:
        scheduler.kickstart(LABEL)
    except scheduler.ScheduleError as exc:
        raise MenuError(str(exc)) from exc


def refresh() -> str | None:
    """After an update: rebuild the app if its stamp no longer matches, and restart it.
    Returns what was done, or ``None`` when nothing was needed. Raises ``MenuError``."""
    target = read_target()
    if not installed() or target is None:
        return None
    try:
        scheduler.prepare_interpreter()
    except (scheduler.ScheduleError, OSError) as exc:
        raise MenuError(str(exc)) from exc
    if problem(target.config) is None and scheduler.read_agent(LABEL) == build_plist(target.config):
        return None
    build(target.config)
    try:
        # bootout + bootstrap: the new build starts (a plain `open` keeps the old one).
        scheduler.install_agent(LABEL, build_plist(target.config))
    except scheduler.ScheduleError as exc:
        raise MenuError(str(exc)) from exc
    return "rebuilt and restarted"


def remove() -> bool:
    """Stop it, unload and delete its agent, the app and its folder. ``False`` if there
    was nothing to remove."""
    found = scheduler.remove_agent(LABEL)
    app = app_path()
    if app.exists():
        notifier.lsregister("-u", app)
        shutil.rmtree(app)
        found = True
    if folder().exists():
        shutil.rmtree(folder())
        found = True
    return found


# --- what flib status and flib doctor say -----------------------------------------------


@dataclass(frozen=True, slots=True)
class Summary:
    installed: bool
    loaded: bool = False
    running: bool = False
    problem: str | None = None
    """Why the app is not as it should be (not built, out of date); ``None`` when fine."""
    config: Path | None = None
    seen: dict[str, Any] | None = None
    """What the applet last said it shows (``seen.json``, checked like the state)."""


def summary() -> Summary:
    """The item's state, for ``flib status`` and ``flib doctor``. Never raises."""
    if not installed():
        return Summary(False)
    target = read_target()
    try:
        loaded = scheduler.agent_loaded(LABEL)
        running = loaded and scheduler.agent_running(LABEL)
    except scheduler.ScheduleError:
        loaded = running = False
    trouble = "flib menu was not set up completely" if target is None else problem(target.config)
    return Summary(
        True,
        loaded,
        running,
        trouble,
        None if target is None else target.config,
        read_seen(),
    )


def read_seen() -> dict[str, Any] | None:
    """``seen.json`` as the applet wrote it, checked like ``state.json`` (plus its pid)."""
    try:
        raw = (folder() / SEEN_NAME).read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.pop("pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    return {**sanitize(json.dumps(data).encode()), "pid": pid}


def status_line(found: Summary) -> str | None:
    """``flib status``'s line about the item; ``None`` when it is not installed."""
    if not found.installed:
        return None
    if found.problem is not None:
        return f"Menu bar: {found.problem}; `flib menu` fixes it."
    if found.running:
        return "Menu bar: shown (`flib menu --remove` takes it away)."
    if found.loaded:
        return "Menu bar: installed, not running (Quit from its menu); `flib menu` shows it."
    return "Menu bar: installed but not loaded; `flib menu` shows it."


def defaults() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "pose": str(DEFAULT_POSE),
        "line": DEFAULT_LINE,
        "next": "",
        "count": 0,
        "review": 0,
        "due": 0,
        "running": False,
    }


_REFUSED = ("Cc", "Cf", "Zl", "Zp")
"""Unicode categories ``TEXT_PATTERN`` matches: controls, format and bidi marks, line
and paragraph separators."""


def _text(value: object) -> str | None:
    if not isinstance(value, str) or _utf16_length(value) > MAX_TEXT:
        return None
    if any(unicodedata.category(char) in _REFUSED for char in value):
        return None
    return value


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not 0 <= value <= MAX_COUNT or int(value) != value:
        return None
    return int(value)


def _no_constants(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def sanitize(raw: bytes | None) -> dict[str, Any]:
    """What the applet shows for a state file's bytes (``None``: no file): the reference
    for its ``readState``, which the opt-in test compares against the real applet."""
    clean = defaults()
    if raw is None or len(raw) > MAX_STATE_BYTES:
        return clean
    try:
        data = json.loads(raw, parse_constant=_no_constants)
    except ValueError:
        return clean
    if not isinstance(data, dict) or not set(data) <= STATE_KEYS:
        return clean
    if _count(data.get("version")) != STATE_VERSION:
        return clean
    pose = _text(data.get("pose"))
    if pose in {str(p) for p in poses.Pose}:
        clean["pose"] = pose
    line = _text(data.get("line"))
    if line:
        clean["line"] = line
    upcoming = _text(data.get("next"))
    if upcoming is not None:
        clean["next"] = upcoming
    for key in STATE_COUNTS:
        count = _count(data.get(key))
        if count is not None:
            clean[key] = count
    if isinstance(data.get("running"), bool):
        clean["running"] = data["running"]
    return clean
