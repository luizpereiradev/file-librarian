"""The evaluation corpus: reproducible, unique, consistent with its gold file."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from datetime import date
from pathlib import Path

import pytest

from file_librarian.evals import corpus
from file_librarian.evals.documents import make_blank_pdf, make_eml, make_png
from file_librarian.extractors import ocr
from file_librarian.locales import LOCALES
from file_librarian.sensitive import find_secrets
from tests._helpers import candidate, context_for, fake_config


def digests(root: Path) -> dict[str, str]:
    """Every file under ``root`` (relative path -> SHA-256)."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def build(root: Path, seed: int = corpus.DEFAULT_SEED) -> list[corpus.Item]:
    root.mkdir()
    chosen = corpus.items(seed)
    corpus.build(root, chosen)
    return chosen


def test_same_seed_writes_the_same_bytes(tmp_path: Path) -> None:
    build(tmp_path / "a")
    build(tmp_path / "b")
    first = digests(tmp_path / "a")
    assert first == digests(tmp_path / "b")
    assert len(first) > 150  # folder units add their own files


def test_another_seed_changes_amounts_not_names(tmp_path: Path) -> None:
    build(tmp_path / "a")
    build(tmp_path / "b", seed=1)
    a, b = digests(tmp_path / "a"), digests(tmp_path / "b")
    numbered = ("boleto_", "invoice_", "pedido-", "NFe")  # names with a document number
    assert {k for k in a if not k.startswith(numbered)} == {
        k for k in b if not k.startswith(numbered)
    }
    assert len(a) == len(b)
    assert a["bill.pdf"] != b["bill.pdf"]
    assert a["Notely-4.2.1.dmg"] == b["Notely-4.2.1.dmg"]


def test_every_item_is_written_once_with_unique_content(tmp_path: Path) -> None:
    chosen = build(tmp_path / "inbox")
    root = tmp_path / "inbox"
    assert sorted(p.name for p in root.iterdir()) == sorted(i.path for i in chosen)
    hashes = [candidate(root / i.path).sha256 for i in chosen]
    assert len(set(hashes)) == len(hashes), "two items with the same content are duplicates"
    assert {(root / i.path).stat().st_mtime for i in chosen} == {corpus.MTIME}


def test_corpus_covers_formats_languages_and_categories() -> None:
    items = corpus.items()
    assert 140 <= len(items) <= 170
    formats = {i.format for i in items}
    for fmt in ("txt", "md", "csv", "docx", "xlsx", "pptx", "pdf", "eml", "jpg", "png", "dmg"):
        assert fmt in formats
    assert {"folder", "app", "zip"} <= formats
    assert {i.gold.language for i in items} >= {"en", "pt-BR"}
    for lang in corpus.LANGUAGES:
        expected = {c.name for c in LOCALES[lang].categories}
        primary = {i.gold.categories[lang][0] for i in items if i.gold.categories[lang]}
        assert primary == expected, f"every {lang} category has an item"
    cases = {case for i in items for case in i.gold.cases}
    assert {"generic-name", "near-duplicate", "ambiguous", "review", "sensitive"} <= cases


def test_gold_labels_are_well_formed() -> None:
    items = corpus.items()
    assert len({i.path for i in items}) == len(items)
    for item in items:
        gold = item.gold
        assert "/" not in item.path and not item.path.startswith(".")
        for lang in corpus.LANGUAGES:
            allowed = {c.name for c in LOCALES[lang].categories}
            assert set(gold.categories[lang]) <= allowed, (item.path, lang)
            # Only credential files have no acceptable category at all.
            assert bool(gold.categories[lang]) != gold.sensitive, item.path
        assert set(gold.fields) <= set(corpus.FIELD_NAMES), item.path
        if "currency" in gold.fields:
            assert gold.fields["currency"] in {"USD", "BRL"}
        for key in ("document_date", "due_date"):
            if key in gold.fields:
                date.fromisoformat(gold.fields[key])
        if "amount" in gold.fields:
            assert float(gold.fields["amount"]) > 0
        if gold.sensitive:
            assert gold.keep_name is None and not gold.review


def test_near_duplicate_bills_share_an_issuer_across_months() -> None:
    bills = [i for i in corpus.items() if "near-duplicate" in i.gold.cases]
    by_issuer: dict[str, set[str]] = {}
    for item in bills:
        by_issuer.setdefault(item.gold.fields["issuer"], set()).add(item.gold.fields["period"])
    assert all(len(periods) == 3 for periods in by_issuer.values())
    assert len(by_issuer) == 2


def test_gold_file_is_what_the_code_generates() -> None:
    """``gold.json`` is generated: after changing an item, run
    ``uv run python -m file_librarian.evals.corpus``."""
    assert corpus.GOLD_PATH.read_text(encoding="utf-8") == corpus.gold_json()
    data = json.loads(corpus.gold_json())
    assert data["seed"] == corpus.DEFAULT_SEED
    assert data["corpus_version"] == corpus.CORPUS_VERSION
    assert [i["path"] for i in data["items"]] == [i.path for i in corpus.items()]
    first = data["items"][0]
    assert set(first) == {
        "path", "format", "categories", "keep_name", "review", "sensitive", "language",
        "fields", "cases",
    }  # fmt: skip


def test_gold_sensitive_matches_what_flib_withholds(tmp_path: Path) -> None:
    items = build(tmp_path / "inbox")
    cfg = fake_config(tmp_path / "inbox")
    for item in items:
        ctx = context_for(tmp_path / "inbox" / item.path, cfg)
        assert ctx.sensitive == item.gold.sensitive, item.path
        if not ctx.sensitive:
            assert not find_secrets(ctx.text or ""), item.path


def test_select_keeps_a_fixed_sample_with_sensitive_items() -> None:
    items = corpus.items()
    assert corpus.select(items, None) == items
    assert corpus.select(items, 1000) == items
    sample = corpus.select(items, 30)
    assert len(sample) == 30
    assert sample == corpus.select(items, 30)
    assert any(i.gold.sensitive for i in sample)
    assert sample == [i for i in items if i in sample]  # corpus order
    assert len(corpus.select(items, 1)) == 1


def test_build_refuses_a_path_outside_the_folder(tmp_path: Path) -> None:
    bad = corpus.Item("../escape.txt", "txt", corpus.Gold({"en": (), "pt-BR": ()}), print)
    with pytest.raises(ValueError, match="escapes"):
        corpus.build(tmp_path, [bad])
    assert not (tmp_path.parent / "escape.txt").exists()


def test_documents_are_reproducible(tmp_path: Path) -> None:
    for i in range(2):
        make_eml(tmp_path / f"{i}.eml", attachments={"a.bin": b"x"}, html="<p>hi</p>")
        make_png(tmp_path / f"{i}.png", 4, 3, shade=7)
        make_blank_pdf(tmp_path / f"{i}.pdf", 2)
    for ext in ("eml", "png", "pdf"):
        assert (tmp_path / f"0.{ext}").read_bytes() == (tmp_path / f"1.{ext}").read_bytes()
    assert b"flib-synthetic-boundary" in (tmp_path / "0.eml").read_bytes()
    assert (tmp_path / "0.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


IMAGE_TEXT = [i for i in corpus.items() if "image-text" in i.gold.cases]


def test_images_with_text_are_drawn_the_same_every_time(tmp_path: Path) -> None:
    assert len(IMAGE_TEXT) == 6
    for item in IMAGE_TEXT:
        first, second = tmp_path / "a.png", tmp_path / "b.png"
        item.write(first)
        item.write(second)
        data = first.read_bytes()
        assert data == second.read_bytes(), item.path
        assert data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR"
        # Decodable: the pixel rows inflate to exactly height x (1 + width x 3) bytes.
        width, height = struct.unpack(">II", data[16:24])
        start = data.index(b"IDAT") + 4
        length = struct.unpack(">I", data[start - 8 : start - 4])[0]
        assert len(zlib.decompress(data[start : start + length])) == height * (1 + width * 3)


def test_every_image_with_text_has_its_recorded_text(tmp_path: Path) -> None:
    """``ocr.json`` must follow the pictures: after changing one, record it again."""
    data = json.loads(corpus.OCR_PATH.read_text(encoding="utf-8"))
    assert data["helper"] == ocr.HELPER_VERSION
    assert sorted(data["items"]) == sorted(i.path for i in IMAGE_TEXT)
    recorded = corpus.recorded_ocr()
    for item in IMAGE_TEXT:
        target = tmp_path / item.path
        item.write(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        assert data["items"][item.path]["sha256"] == digest, f"record {item.path} again"
        assert recorded[digest].text


def test_the_recorded_layouts_tell_documents_from_pictures() -> None:
    """The rule in ``ocr.looks_like_document``, against what macOS found in each picture."""
    data = json.loads(corpus.OCR_PATH.read_text(encoding="utf-8"))["items"]
    verdicts = {
        path: ocr.looks_like_document(
            ocr.Recognized(e["text"], e["lines"], e["coverage"], e["width"], e["height"])
        )
        for path, e in data.items()
    }
    assert {path for path, document in verdicts.items() if document} == {"scan_0007.png"}
    pictures = {i.path for i in IMAGE_TEXT if "picture" in i.gold.cases}
    assert len(pictures) == 4 and not any(verdicts[p] for p in pictures)


def test_the_font_draws_every_letter_it_knows(tmp_path: Path) -> None:
    from file_librarian.evals import raster

    known = "".join(raster._GLYPHS) + "áàâãéêíóôõúÁÀÂÃÉÊÍÓÔÕÚçÇ"
    for char in known:
        rows = raster._glyph(char)
        assert len(rows) == raster.CELL_H and all(len(r) == 5 for r in rows), char
        assert char == " " or any("#" in r for r in rows), char
    assert raster._glyph("€") == raster._glyph("?")
    canvas = raster.Canvas(40, 20, (0, 0, 0))
    canvas.rect(-5, -5, 3, 3, (255, 0, 0))  # clipped away entirely
    assert set(canvas.pixels) == {0}
    assert canvas.text(0, 0, "Hi", 1, (255, 255, 255)) == raster.text_width("Hi", 1) == 11


def test_pdf_text_keeps_lines_and_accents(tmp_path: Path) -> None:
    from file_librarian.evals.documents import make_pdf
    from file_librarian.extractors import extractor_for

    path = make_pdf(tmp_path / "conta.pdf", "Conta de energia\nVencimento: 20/10/2026 (ação)")
    extractor = extractor_for(path)
    assert extractor is not None
    text = extractor.extract(path, 1000) or ""
    assert "Conta de energia" in text
    assert "ação" in text
    assert text.index("Conta") < text.index("Vencimento")
