"""No damaged file may stop a run (RF-27).

Two layers, both fuzzed with many random corruptions of every format:

1. each extractor raises only ``ExtractionError`` (or returns text/None);
2. ``build_context_safely`` never raises, for files and for folder units
   with a damaged ``Info.plist`` - the backstop the pipeline relies on.

Office files are re-packed with DEFLATE compression, as real ones are, so
corruption can surface as zlib errors (the original bug).
"""

from __future__ import annotations

import plistlib
import random
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from file_librarian.config import parse_config
from file_librarian.context import build_context_safely
from file_librarian.extractors import ExtractionError, extractor_for
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.scanner import Candidate
from tests._helpers import make_docx, make_pdf, make_pptx, make_xlsx

CONFIG = parse_config({"provider": {"name": "fake", "model": "x"}})
ROUNDS = 150


def _deflated(build: Callable[[Path], Path]) -> Callable[[Path], Path]:
    def make(path: Path) -> Path:
        plain = build(path.with_name("plain-" + path.name))
        with zipfile.ZipFile(plain) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                dst.writestr(info.filename, src.read(info))
        plain.unlink()
        return path

    return make


def _zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(20):
            zf.writestr(f"dir/file-{i}.txt", "content " * 200)
    return path


def _tar(path: Path) -> Path:
    member = path.with_suffix(".member")
    member.write_text("content " * 2000, encoding="utf-8")
    with tarfile.open(path, "w:gz") as tf:
        tf.add(member, arcname="docs/member.txt")
    member.unlink()
    return path


BUILDERS: dict[str, Callable[[Path], Path]] = {
    "doc.docx": _deflated(lambda p: make_docx(p, [f"paragraph {i} " * 20 for i in range(200)])),
    "sheet.xlsx": _deflated(lambda p: make_xlsx(p, ["A"], [f"cell {i}" for i in range(500)])),
    "deck.pptx": _deflated(lambda p: make_pptx(p, [f"slide {i} " * 30 for i in range(30)])),
    "file.pdf": lambda p: make_pdf(p, "Fatura de energia " * 20),
    "photos.zip": _zip,
    "backup.tar.gz": _tar,
}


def corruptions(original: bytes, seed: str) -> list[bytes]:
    rng = random.Random(seed)  # deterministic per format
    out = []
    for _ in range(ROUNDS):
        data = bytearray(original)
        start = rng.randrange(0, max(1, len(data) - 16))
        for i in range(start, min(len(data), start + rng.randint(1, 16))):
            data[i] = rng.randrange(256)
        if rng.random() < 0.2:
            data = data[: rng.randrange(1, len(data))]  # truncated download
        out.append(bytes(data))
    return out


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_extractors_raise_only_extraction_error(tmp_path: Path, name: str) -> None:
    original = BUILDERS[name](tmp_path / name).read_bytes()
    target = tmp_path / f"damaged-{name}"
    extractor = extractor_for(target)
    assert extractor is not None
    for data in corruptions(original, name):
        target.write_bytes(data)
        try:
            result = extractor.extract(target, 4000)
        except ExtractionError:
            continue
        assert result is None or isinstance(result, str)


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_building_a_context_never_raises(tmp_path: Path, name: str) -> None:
    original = BUILDERS[name](tmp_path / name).read_bytes()
    target = tmp_path / f"damaged-{name}"
    for data in corruptions(original, name):
        target.write_bytes(data)
        candidate = Candidate(target, file_sha256(target), len(data), 0.0, is_unit=False)
        build_context_safely(candidate, CONFIG)


def test_app_with_a_damaged_info_plist_never_raises(tmp_path: Path) -> None:
    app = tmp_path / "Tool.app"
    (app / "Contents").mkdir(parents=True)
    plist = app / "Contents" / "Info.plist"
    original = plistlib.dumps({"CFBundleName": "Tool", "CFBundleIdentifier": "com.example"})
    for data in [original[:40], original[:81], *corruptions(original, "plist")]:
        plist.write_bytes(data)
        manifest = unit_manifest(app)
        candidate = Candidate(app, manifest.digest, manifest.size_bytes, 0.0, is_unit=True)
        context = build_context_safely(candidate, CONFIG)
        assert context.is_unit
