"""Pictures with words in them, drawn by code: the corpus's flyers, screenshots and scans.

The evaluation corpus needs images that hold text (#100) and the repository
holds no binary fixtures, so this module draws them: a canvas of RGB pixels,
filled rectangles, circles and gradients, and text in a 5x7 bitmap font of
its own (ASCII and the accented letters Portuguese needs), written out as a
PNG with ``zlib``. Nothing is installed, nothing depends on the platform's
fonts or renderer, and the same calls write the same bytes everywhere, so the
text macOS recognises in them can be recorded once and checked in
(``evals/ocr.json``).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

RGB = tuple[int, int, int]

_ROWS = 7
_DESCENT = 2
_ACCENT = 2
CELL_W, CELL_H = 6, _ACCENT + _ROWS + _DESCENT
"""A character's advance and its full height (accents above, descenders below), in font pixels."""

# Each glyph: rows from the top of a capital letter; a ninth and tenth row are
# descenders. "#" is ink.
_GLYPHS: dict[str, str] = {
    "A": ".###. #...# #...# ##### #...# #...# #...#",
    "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.",
    "D": "####. #...# #...# #...# #...# #...# ####.",
    "E": "##### #.... #.... ####. #.... #.... #####",
    "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####",
    "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.",
    "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#",
    "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.",
    "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.",
    "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.",
    "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "a": "..... ..... .###. ....# .#### #...# .####",
    "b": "#.... #.... #.##. ##..# #...# #...# ####.",
    "c": "..... ..... .###. #.... #.... #...# .###.",
    "d": "....# ....# .##.# #..## #...# #...# .####",
    "e": "..... ..... .###. #...# ##### #.... .###.",
    "f": "..##. .#..# .#... ###.. .#... .#... .#...",
    "g": "..... ..... .#### #...# #...# #...# .#### ....# .###.",
    "h": "#.... #.... #.##. ##..# #...# #...# #...#",
    "i": "..#.. ..... .##.. ..#.. ..#.. ..#.. .###.",
    "j": "...#. ..... ..##. ...#. ...#. ...#. ...#. #..#. .##..",
    "k": "#.... #.... #..#. #.#.. ##... #.#.. #..#.",
    "l": ".##.. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "m": "..... ..... ##.#. #.#.# #.#.# #.#.# #.#.#",
    "n": "..... ..... #.##. ##..# #...# #...# #...#",
    "o": "..... ..... .###. #...# #...# #...# .###.",
    "p": "..... ..... ####. #...# #...# #...# ####. #.... #....",
    "q": "..... ..... .#### #...# #...# #...# .#### ....# ....#",
    "r": "..... ..... #.##. ##..# #.... #.... #....",
    "s": "..... ..... .###. #.... .###. ....# ####.",
    "t": ".#... .#... ###.. .#... .#... .#..# ..##.",
    "u": "..... ..... #...# #...# #...# #..## .##.#",
    "v": "..... ..... #...# #...# #...# .#.#. ..#..",
    "w": "..... ..... #...# #...# #.#.# #.#.# .#.#.",
    "x": "..... ..... #...# .#.#. ..#.. .#.#. #...#",
    "y": "..... ..... #...# #...# #...# #...# .#### ....# .###.",
    "z": "..... ..... ##### ...#. ..#.. .#... #####",
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.",
    "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####",
    "3": "####. ....# ....# .###. ....# ....# ####.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.",
    "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.",
    "9": ".###. #...# #...# .#### ....# ...#. .##..",
    " ": "..... ..... ..... ..... ..... ..... .....",
    ".": "..... ..... ..... ..... ..... .##.. .##..",
    ",": "..... ..... ..... ..... ..... .##.. .##.. ..#.. .#...",
    ":": "..... .##.. .##.. ..... .##.. .##.. .....",
    ";": "..... .##.. .##.. ..... .##.. .##.. ..#.. .#...",
    "/": "....# ....# ...#. ..#.. .#... #.... #....",
    "-": "..... ..... ..... .###. ..... ..... .....",
    "+": "..... ..#.. ..#.. ##### ..#.. ..#.. .....",
    "$": "..#.. .#### #.#.. .###. ..#.# ####. ..#..",
    "!": "..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..",
    "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
    "'": "..#.. ..#.. .#... ..... ..... ..... .....",
    '"': ".#.#. .#.#. ..... ..... ..... ..... .....",
    "(": "...#. ..#.. .#... .#... .#... ..#.. ...#.",
    ")": ".#... ..#.. ...#. ...#. ...#. ..#.. .#...",
    "%": "##... ##..# ...#. ..#.. .#... #..## ...##",
    "&": ".##.. #..#. #.#.. .#... #.#.# #..#. .##.#",
    "@": ".###. #...# #.### #.#.# #.### #.... .####",
    "#": ".#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.",
    "*": "..... ..#.. #.#.# .###. #.#.# ..#.. .....",
    "=": "..... ..... ##### ..... ##### ..... .....",
    "_": "..... ..... ..... ..... ..... ..... #####",
    "|": "..#.. ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "<": "...#. ..#.. .#... #.... .#... ..#.. ...#.",
    ">": ".#... ..#.. ...#. ....# ...#. ..#.. .#...",
}
_DOTLESS_I = "..... ..... .##.. ..#.. ..#.. ..#.. .###."
_MARKS = {
    "acute": ("...#.", "..#.."),
    "grave": (".#...", "..#.."),
    "circumflex": ("..#..", ".#.#."),
    "tilde": (".##.#", "#..#."),
}
_ACCENTED = {
    "á": ("a", "acute"), "à": ("a", "grave"), "â": ("a", "circumflex"), "ã": ("a", "tilde"),
    "é": ("e", "acute"), "ê": ("e", "circumflex"), "í": ("i", "acute"), "ó": ("o", "acute"),
    "ô": ("o", "circumflex"), "õ": ("o", "tilde"), "ú": ("u", "acute"),
    "Á": ("A", "acute"), "À": ("A", "grave"), "Â": ("A", "circumflex"), "Ã": ("A", "tilde"),
    "É": ("E", "acute"), "Ê": ("E", "circumflex"), "Í": ("I", "acute"), "Ó": ("O", "acute"),
    "Ô": ("O", "circumflex"), "Õ": ("O", "tilde"), "Ú": ("U", "acute"),
}  # fmt: skip


def _bitmap(rows: list[str]) -> list[str]:
    """``rows`` padded to the full cell: accent rows above, descender rows below."""
    return ["....."] * _ACCENT + rows + ["....."] * (_ROWS + _DESCENT - len(rows))


def _glyph(char: str) -> list[str]:
    """The cell of ``char``, ``CELL_H`` rows of 5 columns; an unknown character is a ``?``."""
    if char in _ACCENTED:
        base, mark = _ACCENTED[char]
        rows = _bitmap((_DOTLESS_I if base == "i" else _GLYPHS[base]).split())
        top = next(i for i, row in enumerate(rows) if "#" in row)
        rows[top - 2 : top] = _MARKS[mark]
        return rows
    if char in ("ç", "Ç"):
        rows = _bitmap(_GLYPHS["c" if char == "ç" else "C"].split())
        rows[_ACCENT + _ROWS : _ACCENT + _ROWS + 2] = ["..#..", ".##.."]
        return rows
    return _bitmap(_GLYPHS.get(char, _GLYPHS["?"]).split())


class Canvas:
    """An RGB picture, ``width`` x ``height`` pixels, drawn in place."""

    def __init__(self, width: int, height: int, background: RGB = (255, 255, 255)) -> None:
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(background) * (width * height))

    def rect(self, x: int, y: int, w: int, h: int, color: RGB) -> None:
        """Fill the rectangle at ``(x, y)`` (clipped to the canvas)."""
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + w), min(self.height, y + h)
        if x0 >= x1 or y0 >= y1:
            return
        run = bytes(color) * (x1 - x0)
        for row in range(y0, y1):
            start = (row * self.width + x0) * 3
            self.pixels[start : start + len(run)] = run

    def rounded(self, x: int, y: int, w: int, h: int, radius: int, color: RGB) -> None:
        """A rectangle with rounded corners, like a chat bubble or a button."""
        r = min(radius, w // 2, h // 2)
        self.rect(x + r, y, w - 2 * r, h, color)
        self.rect(x, y + r, r, h - 2 * r, color)
        self.rect(x + w - r, y + r, r, h - 2 * r, color)
        for cx, cy in ((x + r, y + r), (x + w - r - 1, y + r), (x + r, y + h - r - 1)):
            self.circle(cx, cy, r, color)
        self.circle(x + w - r - 1, y + h - r - 1, r, color)

    def circle(self, cx: int, cy: int, radius: int, color: RGB) -> None:
        for dy in range(-radius, radius + 1):
            half = int((radius * radius - dy * dy) ** 0.5)
            self.rect(cx - half, cy + dy, 2 * half + 1, 1, color)

    def vgradient(self, y: int, h: int, top: RGB, bottom: RGB) -> None:
        """A vertical gradient across the full width, from ``top`` to ``bottom``."""
        for i in range(h):
            t = i / max(1, h - 1)
            color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
            self.rect(0, y + i, self.width, 1, color)  # type: ignore[arg-type]

    def mountain(self, peak_x: int, peak_y: int, base_y: int, half_width: int, color: RGB) -> None:
        """A triangle standing on ``base_y`` with its tip at ``(peak_x, peak_y)``."""
        height = max(1, base_y - peak_y)
        for row in range(peak_y, base_y):
            half = (row - peak_y) * half_width // height
            self.rect(peak_x - half, row, 2 * half + 1, 1, color)

    def speckle(self, count: int, color: RGB, seed: int) -> None:
        """``count`` single pixels scattered over the canvas, like dust on a scan."""
        state = seed or 1
        for _ in range(count):
            state = (state * 1103515245 + 12345) % 2**31
            x = state % self.width
            state = (state * 1103515245 + 12345) % 2**31
            y = state % self.height
            self.rect(x, y, 1, 1, color)

    def text(self, x: int, y: int, line: str, scale: int, color: RGB) -> int:
        """Draw ``line`` with its cell's top-left at ``(x, y)``; returns its width in pixels."""
        for i, char in enumerate(line):
            for row, bits in enumerate(_glyph(char)):
                for col, bit in enumerate(bits):
                    if bit == "#":
                        self.rect(
                            x + (i * CELL_W + col) * scale, y + row * scale, scale, scale, color
                        )
        return text_width(line, scale)

    def centered(self, y: int, line: str, scale: int, color: RGB) -> None:
        self.text((self.width - text_width(line, scale)) // 2, y, line, scale, color)

    def png(self) -> bytes:
        """The picture as an 8-bit RGB PNG; the same pixels always give the same bytes."""

        def chunk(kind: bytes, data: bytes) -> bytes:
            crc = struct.pack(">I", zlib.crc32(kind + data))
            return struct.pack(">I", len(data)) + kind + data + crc

        stride = self.width * 3
        raw = b"".join(
            b"\0" + bytes(self.pixels[r * stride : (r + 1) * stride]) for r in range(self.height)
        )
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )

    def save(self, path: Path) -> Path:
        path.write_bytes(self.png())
        return path


def text_width(line: str, scale: int) -> int:
    """How wide ``line`` is drawn at ``scale``, without the trailing gap of its last letter."""
    return max(0, len(line) * CELL_W - 1) * scale
