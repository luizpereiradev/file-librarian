"""A stand-in for /bin/launchctl used by every test (see conftest.py).

Records loaded labels in a JSON file (FAKE_LAUNCHD). ``bootstrap`` of a plist
with ``RunAtLoad`` runs its program right away, with the plist's environment,
so the access check can be tested end to end. FAKE_LAUNCHD_FAIL=<command>
makes that command fail. Nothing reaches the real launchd.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    state = Path(os.environ["FAKE_LAUNCHD"])
    loaded: dict[str, str] = json.loads(state.read_text()) if state.exists() else {}
    command, args = argv[0], argv[1:]
    if os.environ.get("FAKE_LAUNCHD_FAIL") == command:
        print(f"{command}: simulated failure", file=sys.stderr)
        return 5
    if command == "bootstrap":
        plist = Path(args[1])
        data = plistlib.loads(plist.read_bytes())
        loaded[data["Label"]] = str(plist)
        state.write_text(json.dumps(loaded))
        if data.get("RunAtLoad"):
            env = {**os.environ, **data.get("EnvironmentVariables", {})}
            subprocess.run(data["ProgramArguments"], env=env, check=False)  # noqa: S603
        return 0
    if command == "bootout":
        label = args[0].rsplit("/", 1)[-1]
        if label not in loaded:
            return 3
        del loaded[label]
        state.write_text(json.dumps(loaded))
        return 0
    if command == "print":
        return 0 if args[0].rsplit("/", 1)[-1] in loaded else 113
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
