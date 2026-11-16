"""M1 exit criterion (PRD roadmap), automated with an offline provider.

"A test folder with 50 varied files, including a sub-folder, a .app and a file
with credentials, is organized, searched and undone without loss."

Every byte is accounted for: before, after the run and after undo, the set of
(content hash, size) over all files is identical, nothing is left outside the
input or output folders, and undo restores the exact original tree.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import tarfile
import zipfile
from collections import Counter
from pathlib import Path

import pytest
from pypdf import PdfWriter

from file_librarian import pipeline
from file_librarian.config import Config, parse_config
from file_librarian.db.store import Store
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import Usage
from file_librarian.search import search
from file_librarian.undo import undo_run
from tests._helpers import OLD, make_docx, make_pdf, make_pptx, make_xlsx

pytestmark = pytest.mark.integration

CATEGORY_BY_EXTENSION = {
    ".pdf": "Finance", ".docx": "Contracts", ".xlsx": "Finance", ".pptx": "Other",
    ".png": "Images", ".jpg": "Images", ".dmg": "Installers", ".pkg": "Installers",
    ".app": "Installers", ".zip": "Other", ".gz": "Other", ".txt": "Other", ".md": "Other",
    ".csv": "Finance", ".doc": "Contracts", ".mp4": "Other", ".py": "Other", "": "Other",
}  # fmt: skip


class ByExtension:
    """Offline stand-in for the model: category from the extension, name from the stem."""

    name = "acceptance"
    local = True

    def __init__(self) -> None:
        self.last_usage = Usage()

    def cost_usd(self, usage: Usage) -> float:
        return 0.0

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        category = CATEGORY_BY_EXTENSION.get(context.extension, "Other")
        confidence = 0.6 if "unsure" in context.path.name else 0.92
        return Classification(
            category=category,
            suggested_name=f"{context.path.stem} document",
            description=f"Acceptance corpus item {context.path.name} about {category.lower()}.",
            tags=[category.lower(), "acceptance"],
            confidence=confidence,
        )


def build_corpus(root: Path) -> int:
    """Create 50 top-level items of many kinds; returns how many."""
    for i in range(6):
        make_pdf(root / f"documento({i}).pdf", f"Fatura de energia numero {i}")
    for i in range(4):
        make_docx(root / f"contrato-{i}.docx", [f"Contrato de aluguel {i}", "Cláusula 1"])
    for i in range(3):
        make_xlsx(root / f"planilha-{i}.xlsx", ["2026"], [f"Orçamento {i}", "Aluguel"])
    for i in range(2):
        make_pptx(root / f"slides-{i}.pptx", [f"Apresentação {i}"])
    for i in range(8):
        (root / f"IMG_48{i:02}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 64)
    for i in range(3):
        (root / f"Setup-{i}.dmg").write_bytes(f"dmg {i}".encode() * 100)
    (root / "Installer.pkg").write_bytes(b"xar!pkg")
    for i in range(4):
        (root / f"notes-{i}.txt").write_text(f"Meeting notes {i}", encoding="utf-8")
    (root / "README.md").write_text("# Project\nSome notes", encoding="utf-8")
    (root / "extrato.csv").write_text("data;valor\n01/10/2026;10,00\n", encoding="utf-8")
    (root / "antigo.doc").write_bytes(b"\xd0\xcf\x11\xe0legacy word")
    (root / "video.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" * 10)
    (root / "script.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "unsure-scan.pdf").write_bytes(make_pdf(root / "tmp.pdf", "?").read_bytes() + b"%x")
    (root / "tmp.pdf").unlink()
    (root / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")
    with zipfile.ZipFile(root / "fotos.zip", "w") as zf:
        zf.writestr("viagem/IMG_1.jpg", b"jpg")
    src = root / "_tmp_member.txt"
    src.write_text("backup", encoding="utf-8")
    with tarfile.open(root / "backup.tar.gz", "w:gz") as tf:
        tf.add(src, arcname="docs/backup.txt")
    src.unlink()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password="acceptance-only", algorithm="RC4-128")  # noqa: S106
    with (root / "protegido.pdf").open("wb") as fh:
        writer.write(fh)
    # Credentials: one by content, one by name. Both must stay where they are.
    (root / "aws-notes.txt").write_text("key = " + "AKIA" + "ABCDEFGHIJKLMNOP\n", encoding="utf-8")
    (root / "id_ed25519").write_text("not really a key", encoding="utf-8")
    # A sub-folder and an .app bundle move as units.
    (root / "thesis" / "chapters").mkdir(parents=True)
    (root / "thesis" / "chapters" / "01.md").write_text("intro", encoding="utf-8")
    (root / "thesis" / "refs.bib").write_text("@book{x}", encoding="utf-8")
    app = root / "Tool.app" / "Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "MacOS" / "tool").write_bytes(b"\xcf\xfa\xed\xfe binary")
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "Tool"}))
    # Fill up to 50 with more invoices.
    count = len(list(root.iterdir()))
    for i in range(50 - count):
        make_pdf(root / f"boleto-{i}.pdf", f"Boleto {i} vencimento outubro")
    for path in root.rglob("*"):
        os.utime(path, (OLD, OLD), follow_symlinks=False)
    os.utime(root, (OLD, OLD))
    return len(list(root.iterdir()))


def contents(root: Path) -> Counter[tuple[str, int]]:
    out: Counter[tuple[str, int]] = Counter()
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            out[(hashlib.sha256(data).hexdigest(), len(data))] += 1
    return out


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "Organized" not in p.relative_to(root).parts
    }


@pytest.fixture
def config(downloads: Path, tmp_path: Path) -> Config:
    return parse_config(
        {
            "inputs": [str(downloads)],
            "database": str(tmp_path / "data" / "library.db"),
            "provider": {"name": "fake", "model": "offline"},
        }
    )


def test_m1_exit_criterion(downloads: Path, config: Config, fake_home: Path) -> None:
    assert build_corpus(downloads) == 50
    before_tree = tree(downloads)
    before_bytes = contents(downloads)

    # Organize.
    outcome = pipeline.run(config, ByExtension(), simulate=False)
    report = outcome.report
    assert report is not None
    assert report.errors == 0
    assert len(report.items) == 50
    # Nothing lost or duplicated anywhere under the home folder.
    assert contents(downloads) == before_bytes
    stray = [p for p in fake_home.rglob("*") if p.is_file() and not p.is_relative_to(downloads)]
    assert not [p for p in stray if "Library" not in p.parts]
    # Credentials stayed exactly where they were; everything else moved.
    left = sorted(p.name for p in downloads.iterdir() if p.name != "Organized")
    assert left == ["aws-notes.txt", "id_ed25519"]
    organized = downloads / "Organized"
    assert (organized / "Installers" / "Tool.app" / "Contents" / "Info.plist").exists()
    assert (organized / "Other" / "thesis" / "chapters" / "01.md").exists()
    assert (organized / "_Review" / "unsure-scan.pdf").exists()
    assert any((organized / "Contracts").glob("*antigo*.doc"))  # legacy: metadata only

    # Idempotent.
    assert pipeline.run(config, ByExtension(), simulate=False).plan.items == []

    # Searchable.
    with Store.open(config.database) as store:
        assert [h.original_name for h in search(store, "Tool.app")] == ["Tool.app"]
        assert len(search(store, "acceptance finance", limit=100)) >= 10
        assert search(store, "AKIA") == []  # secrets are never described

        # Undone without loss.
        undo = undo_run(store)
    assert undo.skipped == []
    assert tree(downloads) == before_tree
    assert contents(downloads) == before_bytes
