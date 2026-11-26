"""Stand-ins for the tools that build and launch flib's notifier app (see conftest.py).

One script for all of them, chosen by the name it is run under (each is a shim
named after the tool): ``osacompile``, ``codesign``, ``sips``, ``iconutil``,
``open`` and ``lsregister``. No test builds, signs or launches a real app.

- ``osacompile -o APP SOURCE`` writes the bundle layout the real tool makes
  (``Info.plist`` with the keys flib edits, ``MacOS/applet``,
  ``Resources/applet.icns`` and ``Assets.car``) and keeps the source as
  ``Resources/Scripts/main.scpt``, so tests can read the script;
- ``sips ... -z N N SOURCE --out OUT`` writes ``OUT`` as ``FAKE N``;
- ``iconutil -c icns -o OUT ICONSET`` writes ``OUT`` listing the iconset's files;
- ``codesign``, ``open`` and ``lsregister`` do nothing.

Every call is appended as a JSON line (``[tool, *argv]``) to FAKE_NOTIFIER_LOG.
With FAKE_NOTIFIER_FAIL set to a tool's name, that tool fails like the real one:
a message on stderr and exit code 1.
"""

from __future__ import annotations

import json
import os
import plistlib
import sys
from pathlib import Path


def _osacompile(argv: list[str]) -> None:
    app = Path(argv[argv.index("-o") + 1])
    source = Path(argv[-1])
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources" / "Scripts").mkdir(parents=True)
    info = {
        "CFBundleExecutable": "applet",
        "CFBundleIconFile": "applet",
        "CFBundleIconName": "applet",
        "CFBundleDocumentTypes": [{"CFBundleTypeName": "x"}],
        "CFBundleName": app.stem,
        "CFBundlePackageType": "APPL",
        "NSCameraUsageDescription": "This script needs access to your camera to run.",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    applet = contents / "MacOS" / "applet"
    applet.write_text("#!/bin/sh\n", encoding="utf-8")
    applet.chmod(0o755)
    (contents / "Resources" / "applet.icns").write_bytes(b"stock icon")
    (contents / "Resources" / "Assets.car").write_bytes(b"stock assets")
    (contents / "Resources" / "Scripts" / "main.scpt").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )


def _sips(argv: list[str]) -> None:
    size = argv[argv.index("-z") + 1]
    Path(argv[argv.index("--out") + 1]).write_text(f"FAKE {size}", encoding="utf-8")


def _iconutil(argv: list[str]) -> None:
    iconset = Path(argv[-1])
    names = sorted(path.name for path in iconset.iterdir())
    Path(argv[argv.index("-o") + 1]).write_text("\n".join(names), encoding="utf-8")


def main(tool: str, argv: list[str]) -> int:
    with Path(os.environ["FAKE_NOTIFIER_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps([tool, *argv], ensure_ascii=False) + "\n")
    if os.environ.get("FAKE_NOTIFIER_FAIL") == tool:
        print(f"{tool}: something went wrong", file=sys.stderr)
        return 1
    if tool == "osacompile":
        _osacompile(argv)
    elif tool == "sips":
        _sips(argv)
    elif tool == "iconutil":
        _iconutil(argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
