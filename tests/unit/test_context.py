from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any

import pytest

from file_librarian.config import Config, parse_config
from file_librarian.context import build_context
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.models import BundleInfo, FileContext, FileMetadata
from file_librarian.scanner import Candidate
from file_librarian.sensitive import find_secrets, sensitive_name
from tests._helpers import make_docx, make_pdf

# Synthetic, clearly fake secrets assembled at runtime so scanners do not flag the repo.
FAKE = {
    "private key": "-----BEGIN "
    + "OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----",
    "AWS access key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "GitHub token": "ghp_" + "a" * 36,
    "Anthropic API key": "sk-ant-" + "api03-" + "x" * 30,
    "Stripe key": "sk_live_" + "b" * 24,
    "connection string with password": "postgres://admin:" + "hunter2pass@db.example.com/app",
    "password or secret assignment": "DB_PASSWORD=" + "correct-horse-battery",
}


def config(**overrides: Any) -> Config:
    raw: dict[str, Any] = {"provider": {"name": "fake", "model": "fixed"}}
    return parse_config(raw | overrides)


def candidate(path: Path) -> Candidate:
    if path.is_dir():
        m = unit_manifest(path)
        return Candidate(path, m.digest, m.size_bytes, 0.0, is_unit=True)
    return Candidate(path, file_sha256(path), path.stat().st_size, 0.0, is_unit=False)


def context_for(path: Path, cfg: Config | None = None) -> FileContext:
    return build_context(candidate(path), cfg or config())


@pytest.mark.parametrize(("kind", "value"), FAKE.items())
def test_each_secret_kind_is_detected(kind: str, value: str) -> None:
    assert kind in find_secrets(f"some text\n{value}\nmore text")


def test_ordinary_text_has_no_secrets() -> None:
    text = "Fatura de energia. Total R$ 231,40. Vencimento 10/11/2026. Senha do wifi no verso."
    assert find_secrets(text) == []


@pytest.mark.parametrize(
    "name", ["id_rsa", "id_ed25519.pub", "server.pem", "vault.kdbx", "credentials.json"]
)
def test_sensitive_names(name: str) -> None:
    assert sensitive_name(name)


@pytest.mark.parametrize("name", ["presentation.key", "keynote.KEY", "invoice.pdf", "env.txt"])
def test_not_sensitive_names(name: str) -> None:
    assert not sensitive_name(name)


def test_document_text_is_capped_for_the_provider(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "contract.docx", ["Lease agreement " * 500])
    ctx = context_for(
        path, config(provider={"name": "fake", "model": "x", "max_chars_per_file": 100})
    )
    assert ctx.text is not None and len(ctx.text) == 100
    assert ctx.sensitive is False
    assert ctx.extension == ".docx"


def test_secret_in_document_marks_sensitive_and_sends_nothing(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "notes.docx", ["deploy notes", FAKE["AWS access key"]])
    ctx = context_for(path)
    assert ctx.sensitive is True
    assert ctx.text is None
    assert ctx.metadata == FileMetadata(sensitive="AWS access key")
    assert FAKE["AWS access key"] not in repr(ctx)


def test_secret_beyond_the_sent_excerpt_is_still_caught(tmp_path: Path) -> None:
    path = tmp_path / "config.txt"
    path.write_text("padding\n" * 5000 + FAKE["GitHub token"], encoding="utf-8")
    assert context_for(path).sensitive is True


def test_key_without_extractor_is_sniffed_but_never_sent(tmp_path: Path) -> None:
    keyfile = tmp_path / "deploy.key"
    keyfile.write_text(FAKE["private key"], encoding="utf-8")
    assert context_for(keyfile).sensitive is True

    other = tmp_path / "notes.weird"
    other.write_text("harmless words", encoding="utf-8")
    ctx = context_for(other)
    assert ctx.sensitive is False
    assert ctx.text is None  # sniffed for secrets only
    assert ctx.metadata.content == "no extractor for this format"


def test_sensitive_name_needs_no_reading(tmp_path: Path) -> None:
    path = tmp_path / "id_rsa"
    path.write_text("whatever", encoding="utf-8")
    assert context_for(path).metadata == FileMetadata(sensitive="credential file name")


def test_detection_can_be_turned_off(tmp_path: Path) -> None:
    path = tmp_path / "id_rsa"
    path.write_text("plain", encoding="utf-8")
    ctx = context_for(path, config(sensitive={"detect_secrets": False}))
    assert ctx.sensitive is False


def test_never_send_withholds_content(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "medical.pdf", "Exam results")
    ctx = context_for(path, config(never_send=["medical*"]))
    assert ctx.sensitive is False
    assert ctx.text is None
    assert ctx.metadata == FileMetadata(content="withheld (never_send)")


def test_extraction_failure_falls_back_to_metadata(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4 garbage")
    ctx = context_for(path)
    assert ctx.text is None
    assert ctx.metadata.extraction_error is not None
    assert "cannot read PDF" in ctx.metadata.extraction_error
    assert ctx.metadata.content is None


def test_folder_unit_sends_a_listing(tmp_path: Path) -> None:
    unit = tmp_path / "thesis"
    (unit / "chapters").mkdir(parents=True)
    (unit / "chapters" / "01-intro.md").write_text("x", encoding="utf-8")
    (unit / "refs.bib").write_text("y", encoding="utf-8")
    ctx = context_for(unit)
    assert ctx.is_unit
    assert ctx.text == "Folder contents (2 files):\nrefs.bib\nchapters/01-intro.md"


def test_app_bundle_reports_its_name(tmp_path: Path) -> None:
    app = tmp_path / "Tool.app"
    (app / "Contents").mkdir(parents=True)
    info = {
        "CFBundleName": "Tool",
        "CFBundleIdentifier": "com.example.tool",
        "CFBundleShortVersionString": "2.1",
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    ctx = context_for(app)
    assert ctx.text is not None
    assert ctx.text.startswith("CFBundleName: Tool\nCFBundleIdentifier: com.example.tool\n")
    assert ctx.metadata == FileMetadata(bundle=BundleInfo("Tool", "com.example.tool", "2.1"))


def test_folder_with_credentials_inside_is_sensitive(tmp_path: Path) -> None:
    unit = tmp_path / "backup"
    (unit / ".ssh").mkdir(parents=True)
    (unit / ".ssh" / "id_ed25519").write_text("k", encoding="utf-8")
    ctx = context_for(unit)
    assert ctx.sensitive is True
    assert ctx.text is None
