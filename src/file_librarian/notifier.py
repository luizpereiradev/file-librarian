"""flib's own notifier: a tiny app bundle, so notifications show as "file-librarian"
with flib's icon instead of Script Editor's.

macOS attributes a notification to the app that posts it. ``osascript`` posts
as Script Editor; an AppleScript applet posts as itself, with its own name and
icon. ``build`` makes one with tools that ship with every Mac, no Xcode
needed:

1. ``osacompile`` compiles ``SCRIPT`` into ``file-librarian.app``;
2. its ``Info.plist`` gets flib's name and bundle identifier and
   ``LSUIElement`` (no Dock icon while it runs); the stock icon in
   ``Assets.car`` is dropped;
3. ``sips`` and ``iconutil`` turn the package's ``assets/icon-1024.png`` into
   ``applet.icns``, every size including @2x;
4. a stamp (``STAMP``) records the script, icon and layout it was built from;
5. ``codesign --sign -`` re-signs it ad hoc (editing the bundle broke
   ``osacompile``'s signature) and ``codesign --verify`` checks the result.

It is built in a temporary folder next to the target and moved into place
only when every step worked, so a failed build never leaves half an app.

The app lives in flib's own environment next to ``flib-python``
(``<env>/bin/file-librarian.app``), never in /Applications. ``flib schedule``
(and ``--refresh``, which ``scripts/install.sh`` runs) builds it or rebuilds it
when the stamp no longer matches; ``flib schedule --remove`` deletes it.

``post`` launches it with ``open -g -n -W <app> --args <text> <title>``:
Launch Services starts it in the background (``-g``), as a new instance so the
arguments always arrive (``-n``), and ``open`` waits for it to quit (``-W``).
The script reads the arguments from ``NSProcessInfo`` and passes them to
``display notification``. Nothing is ever written into the script, and no shell
is involved. (Running ``Contents/MacOS/applet`` directly delivers the arguments
too, but the process then does not get its app identity from Launch Services,
which notifications are tied to.) The script swallows its own errors, as an applet that fails
would otherwise show a dialog. Clicking the notification only brings the
applet up again with no arguments, and it quits: no command ever runs from a
click.

macOS asks once whether "file-librarian" may notify; until the user allows it,
notifications go quietly to Notification Center. ``introduce`` posts a first,
fixed notification while the user is at the terminal (``flib schedule``), so
that question comes then rather than with the first scheduled summary. Apps in
temporary folders (``/tmp``, ``~/Library/Caches``) are not trusted for
notifications at all; flib's environment is in neither.

``status.notify`` uses the app when it is ready and falls back to ``osascript``
otherwise (``choice`` says which and why). Every external tool goes through a
module constant (tests swap in fakes).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import plistlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from file_librarian import i18n, scheduler
from file_librarian.i18n import t

OSACOMPILE = "/usr/bin/osacompile"
CODESIGN = "/usr/bin/codesign"
SIPS = "/usr/bin/sips"
ICONUTIL = "/usr/bin/iconutil"
OPEN = "/usr/bin/open"
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
APP_NAME = "file-librarian"
BUNDLE_ID = "io.github.luizpereiradev.file-librarian.notifier"
APP_VERSION = "1"
"""Bump when the bundle's layout changes, so installed apps are rebuilt."""
STAMP = "flib-notifier.json"
ICON_ASSET = "icon-1024.png"
TIMEOUT = 30

SCRIPT = """\
use AppleScript version "2.4"
use framework "Foundation"
use scripting additions

-- flib's notifier. Arguments: the text, then the title. They are read as data and
-- never run; any error is swallowed, so no dialog ever appears.
on run
\ttry
\t\tset args to (current application's NSProcessInfo's processInfo's arguments()) as list
\t\tif (count of args) < 3 then return
\t\tdisplay notification (item -2 of args) with title (item -1 of args)
\tend try
end run
"""

# (name in the .iconset, pixel size)
ICON_SIZES = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)
# Keys osacompile writes that a notifier has no use for: they only word permission
# prompts it never needs (camera, contacts, ...) or point at the stock icon.
_DROPPED_KEYS = ("CFBundleIconName",)


class NotifierError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Choice:
    """How the next notification will be posted."""

    kind: str
    """``app`` or ``osascript``."""
    reason: str


def app_path() -> Path:
    return scheduler.dedicated_interpreter().parent / f"{APP_NAME}.app"


def executable() -> Path:
    return app_path() / "Contents" / "MacOS" / "applet"


def icon_bytes() -> bytes:
    return resources.files("file_librarian").joinpath("assets", ICON_ASSET).read_bytes()


def expected_stamp() -> dict[str, str]:
    """What a current app was built from: a change to any of it means a rebuild."""
    return {
        "version": APP_VERSION,
        "bundle_id": BUNDLE_ID,
        "script": hashlib.sha256(SCRIPT.encode()).hexdigest(),
        "icon": hashlib.sha256(icon_bytes()).hexdigest(),
    }


def _read_stamp(app: Path) -> dict[str, str] | None:
    try:
        data = json.loads((app / "Contents" / "Resources" / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def state() -> str | None:
    """Why the app cannot be used as it is; ``None`` when it is ready."""
    app = app_path()
    if not app.exists():
        return t("notify.app.not_built")
    if not executable().is_file():
        return t("notify.app.incomplete")
    if _read_stamp(app) != expected_stamp():
        return t("notify.app.out_of_date")
    return None


def choice() -> Choice:
    """Which notifier ``status.notify`` uses, and why."""
    problem = state()
    if problem is None:
        return Choice("app", t("notify.app.current"))
    return Choice("osascript", problem)


def missing_tools() -> list[str]:
    return [tool for tool in (OSACOMPILE, CODESIGN, SIPS, ICONUTIL) if not Path(tool).exists()]


def run_tool(*args: str) -> None:
    try:
        # Fixed system tools; the arguments are flib's own paths.
        result = subprocess.run(  # noqa: S603
            list(args), capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotifierError(f"cannot run {Path(args[0]).name}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[:200] or f"exit code {result.returncode}"
        raise NotifierError(f"{Path(args[0]).name} failed: {detail}")


def _edit_info(app: Path) -> None:
    info = app / "Contents" / "Info.plist"
    data = plistlib.loads(info.read_bytes())
    for key in [*_DROPPED_KEYS, *(k for k in data if k.endswith("UsageDescription"))]:
        data.pop(key, None)
    data.update(
        {
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleIconFile": "applet",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSUIElement": True,
        }
    )
    info.write_bytes(plistlib.dumps(data))
    (app / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)


def _make_icon(work: Path, target: Path) -> None:
    source = work / ICON_ASSET
    source.write_bytes(icon_bytes())
    iconset = work / f"{APP_NAME}.iconset"
    iconset.mkdir()
    for name, size in ICON_SIZES:
        run_tool(SIPS, "-s", "format", "png", "-z", str(size), str(size), str(source),
             "--out", str(iconset / name))  # fmt: skip
    run_tool(ICONUTIL, "-c", "icns", "-o", str(target), str(iconset))


def build() -> Path:
    """Build (or rebuild) the notifier app; returns its path. Raises ``NotifierError``."""
    missing = missing_tools()
    if missing:
        raise NotifierError(f"{', '.join(missing)} not found")
    target = app_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".notifier-", dir=target.parent) as tmp:
        work = Path(tmp)
        source = work / "notifier.applescript"
        source.write_text(SCRIPT, encoding="utf-8")
        app = work / target.name
        run_tool(OSACOMPILE, "-o", str(app), str(source))
        if not (app / "Contents" / "Info.plist").is_file():
            raise NotifierError("osacompile made no app bundle")
        _edit_info(app)
        _make_icon(work, app / "Contents" / "Resources" / "applet.icns")
        stamp = app / "Contents" / "Resources" / STAMP
        stamp.write_text(json.dumps(expected_stamp(), indent=2) + "\n", encoding="utf-8")
        run_tool(CODESIGN, "--force", "--deep", "--sign", "-", str(app))
        run_tool(CODESIGN, "--verify", "--deep", "--strict", str(app))
        if target.exists():
            shutil.rmtree(target)
        app.rename(target)
    lsregister("-f", target)
    return target


def lsregister(flag: str, app: Path) -> None:
    """Register (``-f``: so a rebuilt app's new icon shows) or unregister (``-u``) the
    app with Launch Services. Best effort: macOS also does it on the next launch."""
    if not Path(LSREGISTER).exists():
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603
            [LSREGISTER, flag, str(app)], capture_output=True, timeout=TIMEOUT, check=False
        )


def ensure() -> str | None:
    """Build the app unless it is ready; returns why it could not be, or ``None``."""
    if state() is None:
        return None
    try:
        build()
    except (NotifierError, OSError) as exc:
        return str(exc)
    return None


INTRO_TEXT = "notify.intro"
"""The id of the first notification's text, posted in the configuration's language."""
INTRO_MARKER = ".notifier-introduced"


def introduced(folder: Path) -> bool:
    """``introduce`` already posted, for this bundle identifier."""
    try:
        return (folder / INTRO_MARKER).read_text(encoding="utf-8").strip() == BUNDLE_ID
    except OSError:
        return False


def introduce(folder: Path, title: str = APP_NAME, *, language: str = "en") -> str | None:
    """Post ``INTRO_TEXT`` in ``language`` (the configuration's) and leave a marker in
    ``folder`` (flib's log folder, which outlives reinstalls) for ``introduced``;
    returns why it failed, or ``None``."""
    if state() is not None:
        return t("notify.app.not_ready")
    problem = post(t(INTRO_TEXT).text(i18n.resolve(language)), title)
    if problem is None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / INTRO_MARKER).write_text(BUNDLE_ID + "\n", encoding="utf-8")
    return problem


def remove() -> bool:
    """Delete the app and forget it in Launch Services. ``False`` if there was none."""
    target = app_path()
    if not target.exists():
        return False
    lsregister("-u", target)
    shutil.rmtree(target)
    return True


def post(text: str, title: str) -> str | None:
    """Post through the app; returns why it failed, or ``None``. Never raises."""
    try:
        # A fixed executable; the text and title go in as arguments, never as code.
        result = subprocess.run(  # noqa: S603
            [OPEN, "-g", "-n", "-W", str(app_path()), "--args", text, title],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc) or type(exc).__name__
    if result.returncode != 0:
        return result.stderr.strip()[:200] or f"the notifier app exited with {result.returncode}"
    return None
