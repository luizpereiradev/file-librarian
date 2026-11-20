"""A stand-in for ``osascript`` running ``extractors/ocr.js``, used by every test.

See conftest.py: no test runs macOS's text recognition unless it opts in
(``tests/integration/test_ocr_real.py``). It "recognises" the text that
``tests._helpers.make_scan`` hides in a synthetic image or PDF (a
``FAKEOCR:<base64>`` marker); any other readable file has no text, like a
photo. Every call is appended as a JSON line (its argv) to FAKE_OCR_LOG.

FAKE_OCR_MODE changes what it does:

- ``fail``: exit 3 with a message on stderr, like a recognition error;
- ``hang``: sleep far longer than any test's timeout;
- ``garbage``: print invalid UTF-8 and control characters around the text;
- ``unavailable``: every call fails as on a Mac without Vision.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

LANGUAGES = ("en-US", "fr-FR", "de-DE", "pt-BR")
_MARKER = re.compile(rb"FAKEOCR:([A-Za-z0-9+/=]+)")


def main(argv: list[str]) -> int:
    with Path(os.environ["FAKE_OCR_LOG"]).open("a", encoding="utf-8") as log:
        log.write(json.dumps(argv, ensure_ascii=False) + "\n")
    mode = os.environ.get("FAKE_OCR_MODE", "")
    if argv[:2] != ["-l", "JavaScript"] or len(argv) < 4:
        print("usage error", file=sys.stderr)
        return 1
    command, args = argv[3], argv[4:]
    if mode == "unavailable":
        print("execution error: Error: ReferenceError: Can't find variable: VNRecognizeTextRequest")
        print("ocr.js: execution error (-2700)", file=sys.stderr)
        return 1
    if mode == "hang":
        time.sleep(60)
    if command == "probe":
        print("\n".join(LANGUAGES))
        return 0
    if mode == "fail":
        print("text recognition failed", file=sys.stderr)
        return 3
    try:
        data = Path(args[0]).read_bytes()
    except OSError:
        print("not a readable image", file=sys.stderr)
        return 2
    found = _MARKER.search(data)
    text = base64.b64decode(found.group(1)) if found else b""
    if mode == "garbage":
        text = b"\xff\xfe\x00\x07" + text + b"\x1b[31m\xc3"
    sys.stdout.buffer.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
