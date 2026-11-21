"""A stand-in for /usr/bin/defaults used by every test (see conftest.py).

No test reads the real user defaults. It answers only
``defaults read com.apple.screencapture location``: with FAKE_SCREENSHOT_LOCATION
set, it prints that; unset, it fails like the real tool when the key does not
exist (screenshots then go to the Desktop). Every call is appended as a JSON
line (its argv) to FAKE_DEFAULTS_LOG.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    with Path(os.environ["FAKE_DEFAULTS_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps(argv, ensure_ascii=False) + "\n")
    location = os.environ.get("FAKE_SCREENSHOT_LOCATION")
    if argv != ["read", "com.apple.screencapture", "location"] or location is None:
        print(
            "The domain/default pair of (com.apple.screencapture, location) does not exist",
            file=sys.stderr,
        )
        return 1
    print(location)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
