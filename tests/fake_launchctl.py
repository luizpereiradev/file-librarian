"""A stand-in for /bin/launchctl used by every test (see conftest.py).

Records loaded labels in a JSON file (FAKE_LAUNCHD). ``bootstrap`` of a plist
with ``RunAtLoad`` runs its program right away, with the plist's environment,
so the access check can be tested end to end. ``print`` of a loaded label says
``state = running`` unless the label is in FAKE_LAUNCHD_STOPPED (comma
separated). ``kickstart`` of a loaded label appends ``[label, restart]`` as a
JSON line to ``kicks.jsonl`` next to FAKE_LAUNCHD. FAKE_LAUNCHD_FAIL=<command>
makes that command fail. Nothing reaches the real launchd.
"""

from __future__ import annotations

import json
import os
import sys


def _save(state: str, loaded: dict[str, str]) -> None:
    with open(state, "w", encoding="utf-8") as fh:
        json.dump(loaded, fh)


def main(argv: list[str]) -> int:
    state = os.environ["FAKE_LAUNCHD"]
    loaded: dict[str, str] = {}
    if os.path.exists(state):
        with open(state, encoding="utf-8") as fh:
            loaded = json.load(fh)
    command, args = argv[0], argv[1:]
    if os.environ.get("FAKE_LAUNCHD_FAIL") == command:
        print(f"{command}: simulated failure", file=sys.stderr)
        return 5
    if command == "bootstrap":
        import plistlib  # only here: every call pays for what is imported at the top
        import subprocess

        with open(args[1], "rb") as fh:
            data = plistlib.load(fh)
        loaded[data["Label"]] = args[1]
        _save(state, loaded)
        if data.get("RunAtLoad"):
            env = {**os.environ, **data.get("EnvironmentVariables", {})}
            subprocess.run(data["ProgramArguments"], env=env, check=False)  # noqa: S603
        return 0
    if command == "bootout":
        label = args[0].rsplit("/", 1)[-1]
        if label not in loaded:
            return 3
        del loaded[label]
        _save(state, loaded)
        return 0
    if command == "print":
        label = args[0].rsplit("/", 1)[-1]
        if label not in loaded:
            return 113
        stopped = os.environ.get("FAKE_LAUNCHD_STOPPED", "").split(",")
        print(f"{args[0]} = {{\n\tstate = {'not running' if label in stopped else 'running'}\n}}")
        return 0
    if command == "kickstart":
        label = args[-1].rsplit("/", 1)[-1]
        if label not in loaded:
            print("Could not find service", file=sys.stderr)
            return 113
        with open(
            os.path.join(os.path.dirname(state), "kicks.jsonl"), "a", encoding="utf-8"
        ) as kicks:
            kicks.write(json.dumps([label, "-k" in args]) + "\n")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
