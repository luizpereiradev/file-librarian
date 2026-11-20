"""A stand-in for /usr/bin/osascript used by every test (see conftest.py).

No test ever posts a real notification. Every call is appended as a JSON line
(its argv) to FAKE_OSASCRIPT_LOG. With FAKE_OSASCRIPT_FAIL=1 it fails like the
real tool on a script error: a message on stderr and exit code 1.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    with Path(os.environ["FAKE_OSASCRIPT_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps(argv, ensure_ascii=False) + "\n")
    if os.environ.get("FAKE_OSASCRIPT_FAIL") == "1":
        print("execution error: Notifications are not allowed. (-1743)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
