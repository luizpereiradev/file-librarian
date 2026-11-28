"""A stand-in for ``osascript`` running ``extractors/ocr.js``, used by every test.

See conftest.py: no test runs macOS's text recognition unless it opts in
(``tests/integration/test_ocr_real.py``). It "recognises" the text that
``tests._helpers.make_scan`` hides in a synthetic image or PDF (a
``FAKEOCR:<base64>`` marker); any other readable file has no text, like a
photo. Every call is appended as a JSON line (its argv) to FAKE_OCR_LOG.

It answers like ``ocr.js`` version 2: JSON with one box per line of text.
Each box is as wide as its line (a 50-character line spans 90% of the
picture) and 3.5% of its height, so a page of long lines looks like a
document and a line or two like a picture with words. The picture's size is
the one ``make_image`` wrote (1000 x 1000 for anything else).

FAKE_OCR_MODE changes what it does:

- ``fail``: exit 3 with a message on stderr, like a recognition error;
- ``hang``: sleep far longer than any test's timeout;
- ``garbage``: put control characters around the text;
- ``broken``: print JSON that is not what the helper writes;
- ``unavailable``: every call fails as on a Mac without Vision.
"""

from __future__ import annotations

import binascii
import json
import os
import re
import sys
import time

LANGUAGES = ("en-US", "fr-FR", "de-DE", "pt-BR")
_MARKER = re.compile(rb"FAKEOCR:([A-Za-z0-9+/=]+)")
_SIZE = re.compile(rb"^FAKEIMG (\d+) (\d+)\n")
LINE_HEIGHT = 0.035


def answer(text: str, data: bytes) -> str:
    """The helper's JSON for ``text`` found in ``data``."""
    size = _SIZE.match(data)
    width, height = (int(size.group(1)), int(size.group(2))) if size else (1000, 1000)
    rows = [line for line in text.splitlines() if line.strip()]
    lines = [
        {"text": line, "box": [0.05, 0.9 - i * 0.04, min(0.9, len(line) / 50 * 0.9), LINE_HEIGHT]}
        for i, line in enumerate(rows)
    ]
    page = {"width": width, "height": height, "lines": lines}
    return json.dumps({"v": 2, "pages": [page]}, ensure_ascii=False)


def main(argv: list[str]) -> int:
    with open(os.environ["FAKE_OCR_LOG"], "a", encoding="utf-8") as log:
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
        with open(args[0], "rb") as image:
            data = image.read()
    except OSError:
        print("not a readable image", file=sys.stderr)
        return 2
    found = _MARKER.search(data)
    text = binascii.a2b_base64(found.group(1)).decode("utf-8") if found else ""
    if mode == "broken":
        sys.stdout.write('{"v": 2, "pages": "not a list"}')
        return 0
    if mode == "garbage":
        text = "\x00\x07" + text + "\x1b[31m"  # control characters inside the JSON strings
    sys.stdout.buffer.write(answer(text, data).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
