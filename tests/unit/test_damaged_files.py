"""No damaged file may stop a run (RF-27): every format, many random corruptions."""

from __future__ import annotations

import random
import tarfile
import zipfile
from pathlib import Path

import pytest

from file_librarian.config import parse_config
from file_librarian.context import build_context
from file_librarian.hashing import file_sha256
from file_librarian.scanner import Candidate
from tests._helpers import make_docx, make_pdf, make_pptx, make_xlsx

CONFIG = parse_config({"provider": {"name": "fake", "model": "x"}})
ROUNDS = 150


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


BUILDERS = {
    "doc.docx": lambda p: make_docx(p, [f"paragraph {i} " * 20 for i in range(200)]),
    "sheet.xlsx": lambda p: make_xlsx(p, ["A", "B"], [f"cell {i}" for i in range(500)]),
    "deck.pptx": lambda p: make_pptx(p, [f"slide {i} " * 30 for i in range(30)]),
    "file.pdf": lambda p: make_pdf(p, "Fatura de energia " * 20),
    "photos.zip": _zip,
    "backup.tar.gz": _tar,
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_corrupted_files_never_raise(tmp_path: Path, name: str) -> None:
    original = BUILDERS[name](tmp_path / name).read_bytes()
    rng = random.Random(name)  # deterministic per format
    target = tmp_path / f"damaged-{name}"
    for _ in range(ROUNDS):
        data = bytearray(original)
        start = rng.randrange(0, max(1, len(data) - 16))
        for i in range(start, min(len(data), start + rng.randint(1, 16))):
            data[i] = rng.randrange(256)
        if rng.random() < 0.2:
            data = data[: rng.randrange(1, len(data))]  # truncated download
        target.write_bytes(bytes(data))
        candidate = Candidate(target, file_sha256(target), len(data), 0.0, is_unit=False)
        context = build_context(candidate, CONFIG)  # must never raise
        assert context.text is None or isinstance(context.text, str)
