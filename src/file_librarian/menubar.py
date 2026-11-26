"""flib in the menu bar (#116, ADR 0004): an AppleScriptObjC stay-open applet.

The item shows the cat in the pose for flib's state (``poses``), with a small
count, and a short menu whose main job is to open ``flib ui`` on the right
page. It is built like the notifier (``notifier``), with tools that ship with
macOS and nothing else:

1. ``script`` fills in the template (``assets/menubar.applescript``): the
   state folder, the interpreter and the configuration are fixed there, at
   build time, as AppleScript string literals (``literal`` escapes them;
   anything outside printable ASCII becomes ``character id N``), and the
   menu's actions come from ``ACTIONS``;
2. ``osacompile -s`` compiles it into ``file-librarian-menu.app`` (stay open),
   its ``Info.plist`` gets the bundle identifier and ``LSUIElement`` (no Dock
   icon), the pose icons (``assets/menu/*.svg``) go into ``Resources``;
3. a stamp records what it was built from; ``codesign --sign -`` signs it ad
   hoc and ``codesign --verify`` checks it; it is built in a temporary folder
   and moved into place whole, then registered with Launch Services.

It lives next to the notifier in flib's environment (``<env>/bin``) and is
started at login by the LaunchAgent ``MENU_LABEL`` (``RunAtLoad`` only: off
after Quit, or a crash, until the next login or ``flib menu``; nothing loops if
the app is gone), which ``install`` writes through the scheduler's functions.
``refresh`` rebuilds it when the stamp no longer matches (``scripts/install.sh``
and ``flib schedule --refresh`` call it) and restarts the agent; ``remove``
undoes everything.

**What the applet trusts.** Nothing it reads. ``state.json`` (written by
``menubar_state``, in ``folder()``) must be plain ASCII in the one flat form
flib writes (``FORM``), each key once, and is then checked key by key; only
text and counts come from it. A click runs one program, the interpreter fixed
at build time (``scheduler.dedicated_interpreter``, checked here to be a real
executable in flib's environment), with ``-I`` (no current folder or
``PYTHON*`` variable on its path), the words of one ``ACTIONS`` entry (picked
by its key inside the script; no handler takes words) plus ``--config <fixed
path>``, in ``/``, with a fixed minimal environment: no shell, no lookup in
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
NOTE_NAME = f"{BUNDLE_ID}.changed"
"""The distributed notification flib posts after writing the state (``menubar_state``)."""
APP_VERSION = "2"
"""Bump when the bundle's layout changes, so installed apps are rebuilt."""
STAMP = "flib-menu.json"
STATE_NAME = "state.json"
TARGET_NAME = "target.json"
SEEN_NAME = "seen.json"
PROBLEM_NAME = "problem.json"
IDLE_SECONDS = 30
"""The applet re-reads the state file this often (only when it changed)."""
MAX_SMALL_FILE = 8192
"""The most flib reads of any file in ``folder()``: they are all small."""
CHILD_ENV = ("HOME", "TMPDIR", "USER", "LOGNAME", "LANG")
"""What a click's process gets of the applet's own environment (set by launchd), with
``PATH`` fixed; never ``DYLD_*`` or ``PYTHON*``. ``TMPDIR`` keeps ``flib ui``'s handoff
file in the account's own temporary folder."""

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
STALE_SECONDS = 3 * 3600
"""A state that says a run is going on, written longer ago than this, is from a run
that was killed (flib writes the state again when a run ends): the applet shows
``STALE_LINE`` instead, and flib rewrites it whenever someone looks (``flib status``)."""
STALE_LINE = "The last run did not finish (`flib status`)"
DEFAULT_POSE = poses.Pose.TROUBLE
TEXT_PATTERN = r"[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]"
"""Characters that make a text refused (controls, format and bidi marks, line breaks)."""
_BACKSLASH = "\\"
_STRING = r'"(?:[\x20\x21\x23-\x5b\x5d-\x7e]|' + _BACKSLASH * 2 + r"u[0-9a-f]{4})*" + '"'
_VALUE = r"(?:" + _STRING + r"|0|[1-9][0-9]{0,3}|true|false)"
KEY_FORM = r'"[a-z]{1,16}":'
_PAIR = KEY_FORM + r"\x20?" + _VALUE
FORM = r"\{(?:" + _PAIR + r"(?:,\x20?" + _PAIR + r")*)?\}\n?"
"""The one shape of state.json either side accepts, as a whole: one flat object of
lower-case keys whose values are a string of printable ASCII (no ``"`` or ``\\``;
anything else as a lower-case ``\\uXXXX`` escape), a whole number 0..9999 without
leading zeros, ``true`` or ``false``. Nothing nested, no float, no ``null``, no other
escape, no trailing comma. Matched whole (``re.fullmatch``; ``\\A...\\z`` in the
applet); ``KEY_FORM`` counts the keys, so a key given twice is caught on both sides."""


@dataclass(frozen=True, slots=True)
class Action:
    key: str
    """The action's name; ``key + ":"`` is the handler (an Objective-C selector) the
    menu item calls."""
    title: str
    arguments: tuple[str, ...]
    """After ``<interpreter> -I -m file_librarian``; ``--config <path>`` follows."""

    @property
    def selector(self) -> str:
        return f"{self.key}:"


ACTIONS = (
    Action("openStatus", "Open flib", ("ui", "--open", "status")),
    Action("openReview", "Review", ("ui", "--open", "review")),
    Action("openDue", "Due", ("ui", "--open", "due")),
    Action("organize", "Organize now", ("menu", "--organize")),
    Action("openSettings", "Settings" + chr(0x2026), ("ui", "--open", "settings")),
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


def read_small(path: Path, limit: int = MAX_SMALL_FILE) -> bytes | None:
    """A file of flib's menu folder, if it is a regular file of at most ``limit`` bytes
    (a link is not followed); ``None`` otherwise. Never raises."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return None
            data = fh.read(limit + 1)
    except OSError:
        return None
    return data if len(data) <= limit else None


def read_target() -> Target | None:
    raw = read_small(folder() / TARGET_NAME)
    try:
        data = json.loads(raw or b"")
        return Target(Path(data["config"]), Path(data["database"]))
    except Exception:  # anything but what install wrote: no target
        return None


def private_folder(path: Path) -> Path:
    """``path`` as a folder of the user's own (0700), never through a link. Raises
    ``OSError`` for anything else, so nothing is written through it."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError(f"{path} is not a folder of this user's (a link?); not writing there")
    return path


def write_private(path: Path, data: bytes) -> None:
    """Atomically: a private temporary file in the same folder (0700), renamed over."""
    private_folder(path.parent)
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
_KEY = re.compile(r"[a-z][A-Za-z]*")


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


def _list(items: list[str] | tuple[str, ...]) -> str:
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


def _checked_actions() -> tuple[Action, ...]:
    for action in ACTIONS:
        if not _KEY.fullmatch(action.key) or not all(
            _WORD.fullmatch(word) for word in action.arguments
        ):
            raise MenuError(f"refusing an action with unexpected words: {action}")
    return ACTIONS


def _action_table() -> str:
    """The branches of ``actionArguments``: one fixed key, compared exactly, one fixed
    list of words."""
    lines = []
    for index, action in enumerate(_checked_actions()):
        keyword = "if" if index == 0 else "else if"
        lines.append(f"\t{keyword} (k's isEqualToString:{literal(action.key)}) as boolean then")
        lines.append(f"\t\tset chosen to {_list(action.arguments)}")
    lines.append("\tend if")
    return "\n".join(lines)


def _handlers() -> str:
    return "\n".join(
        f"on {action.selector}sender\n"
        f"\ttry\n"
        f"\t\tmy launchAction({literal(action.key)})\n"
        f"\tend try\n"
        f"end {action.selector}\n"
        for action in _checked_actions()
    )


def _title(key: str) -> str:
    return next(action.title for action in ACTIONS if action.key == key)


def script(program: Path, config: Path, state_dir: Path) -> str:
    """The applet's source for these fixed paths (see ``TEMPLATE_NAME``)."""
    values = {
        "STATE_DIR": literal(str(state_dir)),
        "PROGRAM": literal(str(program)),
        "CONFIG": literal(str(config)),
        "PREFIX": _list(PREFIX),
        "ACTION_TABLE": _action_table(),
        "IDLE": str(IDLE_SECONDS),
        "MAX_BYTES": str(MAX_STATE_BYTES),
        "MAX_TEXT": str(MAX_TEXT),
        "MAX_COUNT": str(MAX_COUNT),
        "VERSION": str(STATE_VERSION),
        "STALE": str(STALE_SECONDS),
        "STALE_LINE": literal(STALE_LINE),
        "POSES": _list([str(pose) for pose in poses.Pose]),
        "KEYS": _list(sorted(STATE_KEYS)),
        "FORM": literal(r"\A" + FORM + r"\z"),
        "KEY_FORM": literal(KEY_FORM),
        "TEXT_PATTERN": literal(TEXT_PATTERN),
        "DEFAULT_POSE": literal(str(DEFAULT_POSE)),
        "DEFAULT_LINE": literal(DEFAULT_LINE),
        "TITLE_STATUS": literal(_title("openStatus")),
        "TITLE_REVIEW": literal(_title("openReview")),
        "TITLE_DUE": literal(_title("openDue")),
        "TITLE_ORGANIZE": literal(_title("organize")),
        "TITLE_SETTINGS": literal(_title("openSettings")),
        "AUTOSAVE": literal(BUNDLE_ID),
        "NOTE": literal(NOTE_NAME),
        "SAFE_PATH": literal(scheduler.SAFE_PATH),
        "CHILD_ENV": _list(CHILD_ENV),
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
        data = json.loads(read_small(app / "Contents" / "Resources" / STAMP) or b"")
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def source_for(config: Path) -> str:
    return script(scheduler.dedicated_interpreter(), config, folder())


def quick_problem() -> str | None:
    """What is plainly wrong with the app, without regenerating its source (for
    ``flib status`` and the interface); ``problem`` checks it is current too."""
    app = app_path()
    if not app.exists():
        return "the menu bar app is not built"
    if not executable().is_file():
        return "the menu bar app is incomplete"
    return None


def problem(config: Path) -> str | None:
    """Why the app cannot be used as it is for ``config``; ``None`` when it is ready."""
    trouble = quick_problem()
    if trouble is None and _read_stamp(app_path()) != expected_stamp(source_for(config)):
        return "the menu bar app is out of date"
    return trouble


def missing_tools() -> list[str]:
    return [tool for tool in (notifier.OSACOMPILE, notifier.CODESIGN) if not Path(tool).exists()]


def _edit_info(app: Path) -> None:
    info = app / "Contents" / "Info.plist"
    data = plistlib.loads(info.read_bytes())
    for key in [
        "CFBundleIconName",
        "CFBundleDocumentTypes",
        "CFBundleURLTypes",
        *(k for k in data if k.endswith("UsageDescription")),
    ]:
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
            # No scripting dictionary, and no document or URL type: nothing invites
            # Apple events, files or links. (The handlers can still be reached by an
            # Apple event that macOS lets through; they only run ACTIONS.)
            "NSAppleScriptEnabled": False,
        }
    )
    info.write_bytes(plistlib.dumps(data))
    (app / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)


def build(config: Path, *, into: Path | None = None) -> Path:
    """Build (or rebuild) the app for ``config`` at ``into`` (by default where it lives,
    and registered there); returns its path. Raises ``MenuError``."""
    missing = missing_tools()
    if missing:
        raise MenuError(f"{', '.join(missing)} not found (the menu bar app is built with it)")
    program = validate_program(scheduler.dedicated_interpreter())
    source = script(program, config, folder())
    target = into or app_path()
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
            _delete(target)
            app.rename(target)
    except (notifier.NotifierError, OSError) as exc:
        raise MenuError(str(exc)) from exc
    if into is None:
        notifier.lsregister("-f", target)
    return target


# --- the agent ----------------------------------------------------------------------------


def build_plist(config: Path) -> dict[str, object]:
    """Started at login (``RunAtLoad``); ``Interactive``, as it draws UI. No
    ``KeepAlive``, like flib's other agents: after Quit or a crash it stays off until
    the next login or ``flib menu``, and if the app is gone (flib uninstalled without
    ``flib menu --remove``) launchd fails once at login instead of retrying. Its
    output, and its children's, is discarded."""
    return {
        "Label": LABEL,
        "Program": str(executable()),
        "ProgramArguments": [str(executable())],
        "EnvironmentVariables": {"FLIB_CONFIG": str(config), "PATH": scheduler.SAFE_PATH},
        "RunAtLoad": True,
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
        "StandardOutPath": os.devnull,
        "StandardErrorPath": os.devnull,
    }


def installed() -> bool:
    """Its agent is installed: the one ``stat`` every hook starts with."""
    return scheduler.plist_path(LABEL).exists()


def install(config: Path, database: Path) -> Path:
    """Build the app for ``config`` (resolved), write its first state, load its agent
    (which starts it) and only then record what it is for (``target.json``), so a
    failure never leaves flib writing state for an item that is not there or that acts
    on another configuration. A new build is made aside and swapped in whole; if
    launchd then refuses it, the item that was there (app, agent, record) is put back
    as it was. Raises ``MenuError``, saying which."""
    from file_librarian import menubar_state

    config = config.resolve()
    try:
        scheduler.prepare_interpreter()
    except (scheduler.ScheduleError, OSError) as exc:
        raise MenuError(str(exc)) from exc
    before = scheduler.read_agent(LABEL) if installed() else None
    app = app_path()
    kept = app.with_name(f".{APP_NAME}.previous.app")
    swapped = False
    if problem(config) is not None:
        staged = build(config, into=app.with_name(f".{APP_NAME}.next.app"))
        try:
            _delete(kept)
            if app.exists() or app.is_symlink():
                app.rename(kept)
            staged.rename(app)
        except OSError as exc:
            _restore_app(app, kept)
            raise MenuError(f"cannot put the new app in place: {exc}") from exc
        swapped = True
    try:
        menubar_state.write_state(database, config)
        plist = scheduler.install_agent(LABEL, build_plist(config))
    except (scheduler.ScheduleError, OSError, ValueError) as exc:
        raise MenuError(_put_back(exc, before, kept if swapped else None)) from exc
    if swapped:
        with contextlib.suppress(OSError):
            _delete(kept)
        notifier.lsregister("-f", app)
    target = json.dumps({"config": str(config), "database": str(database)}, indent=2)
    write_private(folder() / TARGET_NAME, target.encode() + b"\n")
    return plist


def _restore_app(app: Path, kept: Path) -> None:
    if kept.exists():
        with contextlib.suppress(OSError):
            _delete(app)
            kept.rename(app)


def _put_back(exc: Exception, before: dict[str, Any] | None, kept: Path | None) -> str:
    """After a failed install: the agent of the new one out, the item that was there
    back (its app, then its agent); what happened, in words."""
    from file_librarian import menubar_state

    with contextlib.suppress(scheduler.ScheduleError, OSError):
        scheduler.remove_agent(LABEL)
    if kept is not None:
        _restore_app(app_path(), kept)
    if before is None:
        return f"{exc}; the menu bar item is not installed"
    try:
        scheduler.install_agent(LABEL, before)
    except (scheduler.ScheduleError, OSError) as again:
        return f"{exc}; the menu bar item that was there could not be loaded again ({again})"
    menubar_state.changed()  # its own state again, from the record that never changed
    old = read_target()
    what = f" for {old.config}" if old is not None else ""
    return f"{exc}; the menu bar item{what} is back as it was"


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


def _delete(path: Path) -> bool:
    """Delete a folder, or a link in its place (never what the link points to)."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
        return True
    if path.exists():
        shutil.rmtree(path)
        return True
    return False


def remove() -> bool:
    """Stop it, unload and delete its agent, the app and its folder, each step even
    when another failed. ``False`` if there was nothing to remove; raises ``MenuError``
    naming what could not be removed."""
    found = False
    left: list[str] = []
    try:
        found = scheduler.remove_agent(LABEL)
    except (scheduler.ScheduleError, OSError) as exc:
        left.append(f"its agent ({exc})")
    app = app_path()
    try:
        if app.exists() and not app.is_symlink():
            notifier.lsregister("-u", app)
        found = _delete(app) or found
    except OSError as exc:
        left.append(f"{app} ({exc.strerror or exc})")
    try:
        found = _delete(folder()) or found
    except OSError as exc:
        left.append(f"{folder()} ({exc.strerror or exc})")
    if left:
        raise MenuError("could not remove " + "; ".join(left))
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


def summary(*, full: bool = False) -> Summary:
    """The item's state, for ``flib status``, the interface and ``flib doctor``: one
    ``launchctl print``; ``full`` also checks the app is current (regenerating its
    source), for ``flib doctor`` and ``flib menu --status``. Never raises."""
    try:
        if not installed():
            return Summary(False)
        target = read_target()
        try:
            loaded, running = scheduler.agent_state(LABEL)
        except scheduler.ScheduleError:
            loaded = running = False
        if target is None:
            trouble: str | None = "flib menu was not set up completely"
        else:
            trouble = problem(target.config) if full else quick_problem()
        return Summary(
            True,
            loaded,
            running,
            trouble,
            None if target is None else target.config,
            read_seen(),
        )
    except Exception as exc:
        return Summary(True, problem=f"its state cannot be read ({type(exc).__name__})")


def read_seen() -> dict[str, Any] | None:
    """``seen.json`` as the applet wrote it (any order, any JSON spacing), checked value
    by value like ``state.json``, plus its pid; ``None`` for anything else. Never
    raises."""
    raw = read_small(folder() / SEEN_NAME)
    if raw is None:
        return None
    try:
        data = json.loads(raw, parse_constant=_no_constants)
        if not isinstance(data, dict):
            return None
        pid = data.pop("pid", None)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        return {**_checked(data), "pid": pid}
    except Exception:  # a nesting too deep, a number too big: not what the applet writes
        return None


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


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("a key given twice")
    return dict(pairs)


def _has_surrogate(value: object) -> bool:
    return isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value)


def _checked(data: dict[str, Any]) -> dict[str, Any]:
    """The defaults, with each value of ``data`` that passes its own check."""
    clean = defaults()
    if not set(data) <= STATE_KEYS or _count(data.get("version")) != STATE_VERSION:
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


def sanitize(raw: bytes | None, age: float = 0.0) -> dict[str, Any]:
    """What the applet shows for a state file's bytes (``None``: no file), written
    ``age`` seconds ago: the reference for its ``readState``, which the opt-in test
    compares against the real applet. Anything that is not plain ``FORM`` (and each
    key once) gives the defaults; a stale "running" is shown as ``STALE_LINE``."""
    clean = _sanitize(raw)
    if clean["running"] and age > STALE_SECONDS:
        clean.update(running=False, line=STALE_LINE)
        if clean["pose"] == str(poses.Pose.BUSY):
            clean["pose"] = str(poses.Pose.TROUBLE)
    return clean


def _sanitize(raw: bytes | None) -> dict[str, Any]:
    if raw is None or len(raw) > MAX_STATE_BYTES:
        return defaults()
    try:
        text = raw.decode("ascii")
        if re.fullmatch(FORM, text) is None:
            return defaults()
        data = json.loads(text, object_pairs_hook=_unique)
    except ValueError:
        return defaults()
    if not isinstance(data, dict) or any(_has_surrogate(v) for v in data.values()):
        return defaults()  # NSJSONSerialization refuses a lone surrogate outright
    return _checked(data)
