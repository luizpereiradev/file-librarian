"""A stand-in for /usr/bin/sips used by every test (see conftest.py).

It only "reads" synthetic images written by ``tests._helpers.make_image``: a
file starting with ``FAKEIMG <width> <height>``, and PNG files (the size from
their header, as the evaluation corpus writes them). Supports the two calls
``extractors/visual.py`` makes:

- ``-g pixelWidth -g pixelHeight FILE`` prints the size like the real tool;
- ``-s format jpeg -s formatOptions Q [-Z N] FILE --out OUT`` writes a small
  JPEG whose segments include EXIF (with a fake GPS tag) and a comment, so
  tests can check they are stripped, and whose quantization segment holds
  the resulting ``WIDTHxHEIGHT``.

Every call is appended as a JSON line to FAKE_SIPS_LOG. Any other input
fails with exit code 13, like the real tool on a file it cannot read. With
FAKE_SIPS_SILENT=1 a conversion exits 0 without writing anything, as the
real tool does for a file it skips.
"""

from __future__ import annotations

import json
import os
import sys


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _size(path: str) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as image:
            head = image.read(64)
    except OSError:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    parts = head.split(b"\n", 1)[0].split()
    if len(parts) != 3 or parts[0] != b"FAKEIMG":
        return None
    return int(parts[1]), int(parts[2])


def main(argv: list[str]) -> int:
    with open(os.environ["FAKE_SIPS_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(argv) + "\n")
    if argv[:1] == ["-g"]:
        size = _size(argv[-1])
        if size is None:
            print("Error: Cannot extract image from file.", file=sys.stderr)
            return 13
        print(f"{argv[-1]}\n  pixelWidth: {size[0]}\n  pixelHeight: {size[1]}")
        return 0
    out = argv[argv.index("--out") + 1]
    source = argv[argv.index("--out") - 1]
    size = _size(source)
    if size is None:
        return 13
    width, height = size
    if "-Z" in argv:
        limit = int(argv[argv.index("-Z") + 1])
        scale = limit / max(width, height)
        width, height = round(width * scale), round(height * scale)
    if os.environ.get("FAKE_SIPS_SILENT") == "1":
        return 0
    jpeg = (
        b"\xff\xd8"
        + _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x48\x00\x48\x00\x00")
        + _segment(0xE1, b"Exif\x00\x00GPS 48.8584,2.2945")
        + _segment(0xFE, b"camera comment")
        + _segment(0xDB, f"{width}x{height}".encode())
        + _segment(0xDA, b"\x01\x01\x00")
        + b"scan-data\xff\x00more"
        + b"\xff\xd9"
    )
    with open(out, "wb") as converted:
        converted.write(jpeg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
