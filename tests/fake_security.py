"""A stand-in for /usr/bin/security used by every test (see conftest.py).

Supports the three calls file_librarian.keychain makes, storing items in a
JSON file next to this script's state path (FAKE_KEYCHAIN). Exit code 44
means "not found", as with the real tool.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    store = Path(os.environ["FAKE_KEYCHAIN"])
    items: dict[str, str] = json.loads(store.read_text()) if store.exists() else {}
    command, args = argv[0], argv[1:]
    opts = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i] in ("-s", "-a")}
    key = f"{opts.get('-s')}/{opts.get('-a')}"
    if command == "find-generic-password":
        if key not in items:
            return 44
        # Like the real tool: the secret only with -w; otherwise its attributes.
        print(
            items[key] if "-w" in args else f'keychain: "fake"\n    "acct"<blob>="{opts.get("-a")}"'
        )
        return 0
    if command == "add-generic-password":
        first, second = sys.stdin.readline().rstrip("\n"), sys.stdin.readline().rstrip("\n")
        if first != second:
            return 1
        items[key] = first
        store.write_text(json.dumps(items))
        return 0
    if command == "delete-generic-password":
        if key not in items:
            return 44
        del items[key]
        store.write_text(json.dumps(items))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
