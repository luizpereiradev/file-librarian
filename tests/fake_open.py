"""Stand-in for ``/usr/bin/open`` when ``flib ui`` opens its handoff file (see conftest.py).

Opens nothing: appends its arguments as one JSON line to FAKE_OPEN_LOG. With
FAKE_OPEN_FAIL set, it fails like the real tool: a message on stderr, exit 1.
"""

from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    with open(os.environ["FAKE_OPEN_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(argv) + "\n")
    if os.environ.get("FAKE_OPEN_FAIL"):
        print("LSOpenURLsWithRole() failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
