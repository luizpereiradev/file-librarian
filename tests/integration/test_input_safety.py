"""What keeps per-input rules from taking a file they should not (#89 review).

Unicode-insensitive patterns, firmlink spellings of a folder, lock files and
password-like names, one unreadable input among several, the marker of files
filed by rule, and the index keeping the input a file came from.
"""

from __future__ import annotations

import os
import time
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian import reprocess as rp
from file_librarian import status as st
from file_librarian.classifier import rule_decisions
from file_librarian.cli import app
from file_librarian.config import Config, ConfigError, is_within, same_place
from file_librarian.db.store import Store, load_migrations
from file_librarian.globs import matches_any
from file_librarian.models import FileRecord, FileStatus, RunMode, RunTrigger
from file_librarian.onboarding import DESKTOP_ONLY
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.scanner import DAY, ScanError, SkipReason, item_age, scan
from file_librarian.sensitive import sensitive_name
from tests._helpers import answer, context_for, fake_config, put, row_for, write_config

runner = CliRunner()
DATA_VOLUME = Path("/System/Volumes/Data")


@pytest.fixture
def desktop(fake_home: Path) -> Path:
    folder = fake_home / "Desktop"
    folder.mkdir()
    return folder


def two_inboxes(downloads: Path, desktop: Path, **rules: Any) -> Config:
    entry: dict[str, Any] = {"path": str(desktop), **rules}
    return fake_config(inputs=[str(downloads), entry], output_root=str(downloads.parent / "Lib"))


def skipped(result: Any) -> dict[str, SkipReason]:
    return {path.name: reason for path, reason in result.skipped}


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


# --- patterns compare like APFS: case and Unicode form ------------------------------------


@pytest.mark.parametrize(
    ("name", "pattern"),
    [
        (nfd("Relatório final.pdf"), "Relatório*"),  # a decomposed name, a typed pattern
        ("Relatório final.pdf", nfd("Relatório*")),  # the other way round
        (nfd("RELATÓRIO.PDF"), "relatório*"),
    ],
)
def test_patterns_ignore_unicode_form(name: str, pattern: str) -> None:
    nfc = unicodedata.normalize
    assert nfc("NFC", name) != name or nfc("NFC", pattern) != pattern  # one is decomposed
    assert matches_any(name, [pattern])


def test_a_decomposed_name_is_still_ignored(downloads: Path, desktop: Path) -> None:
    cfg = fake_config(
        inputs=[str(downloads), {"path": str(desktop), "ignore": ["Orçamento*"]}],
        output_root=str(downloads.parent / "Lib"),
        ignore=["Relatório*"],
    )
    put(downloads, nfd("Relatório.pdf"), b"r")
    put(desktop, nfd("Orçamento 2026.xlsx"), b"o")
    put(desktop, nfd("Nota fiscal.pdf"), b"n")

    found = scan(cfg, lambda _sha: None)

    assert sorted(unicodedata.normalize("NFC", c.path.name) for c in found.candidates) == [
        "Nota fiscal.pdf"
    ]
    assert sorted(skipped(found).values()) == [SkipReason.IGNORED, SkipReason.IGNORED]


# --- the same folder under another spelling ------------------------------------------------

needs_firmlink = pytest.mark.skipif(
    not DATA_VOLUME.is_dir(), reason="the /System/Volumes/Data firmlink is macOS only"
)


def firmlinked(path: Path) -> Path:
    return DATA_VOLUME / Path(os.path.realpath(path)).relative_to("/")


@needs_firmlink
def test_a_firmlink_spelling_is_the_same_folder(downloads: Path) -> None:
    alias = firmlinked(downloads)
    assert alias.is_dir()
    assert same_place(alias, downloads)
    assert is_within(alias / "Lib" / "x.pdf", downloads)
    assert is_within(downloads / "x.pdf", alias)


@needs_firmlink
def test_firmlink_paths_cannot_bypass_the_overlap_checks(downloads: Path, fake_home: Path) -> None:
    (downloads / "Lib").mkdir()
    (downloads / "Sub").mkdir()
    with pytest.raises(ConfigError, match="listed twice"):
        fake_config(inputs=[str(downloads), str(firmlinked(downloads))])
    with pytest.raises(ConfigError, match="overlap"):
        fake_config(inputs=[str(downloads), str(firmlinked(downloads / "Sub"))])
    with pytest.raises(ConfigError, match="inside output_root"):
        fake_config(inputs=[str(firmlinked(downloads / "Lib"))], output_root=str(downloads / "Lib"))
    with pytest.raises(ConfigError, match="too broad"):
        fake_config(inputs=[str(firmlinked(fake_home))])


def test_the_home_folder_itself_is_rejected(fake_home: Path) -> None:
    with pytest.raises(ConfigError, match="too broad"):
        fake_config(inputs=[str(fake_home)])
    with pytest.raises(ConfigError, match="too broad"):
        fake_config(inputs=[str(fake_home / "Downloads" / "..")])


# --- lock files, password-like names, the Desktop defaults ---------------------------------


def test_office_lock_files_are_never_taken(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "~$port.docx", b"owner")
    put(downloads, "report.docx", b"the document")
    found = scan(cfg, lambda _sha: None)
    assert [c.path.name for c in found.candidates] == ["report.docx"]
    assert skipped(found) == {"~$port.docx": SkipReason.IN_PROGRESS}


@pytest.mark.parametrize(
    "name",
    [
        "Chrome Passwords.csv",
        "passwords.txt",
        "PASSWORD.docx",
        "senhas do banco.xlsx",
        "github-recovery-codes.txt",
        "Recovery Key.pdf",
        "backup codes.txt",
        "google_backup_codes.pdf",
        "2FA setup.png",
        "seed phrase.txt",
        "wallet-seed-phrase.jpg",
        "private key.txt",
        "my_private_key.asc",
        "chave privada.txt",
        "código de recuperação.txt",
    ],
)
def test_names_of_passwords_and_recovery_material_are_sensitive(name: str) -> None:
    assert sensitive_name(name)
    assert sensitive_name(nfd(name))


@pytest.mark.parametrize(
    "name", ["passport.pdf", "Talk.key", "recovery plan.docx", "seed catalog.pdf", "keynote.pdf"]
)
def test_ordinary_names_are_not_sensitive(name: str) -> None:
    assert not sensitive_name(name)


def test_a_password_named_file_stays_in_place_and_is_never_sent(downloads: Path) -> None:
    cfg = fake_config(downloads)
    path = put(downloads, "Chrome Passwords.csv", b"name,url,username,password\n")
    assert context_for(path, cfg).sensitive
    provider = FakeProvider(default=answer("Other"))
    pipeline.run(cfg, provider, simulate=False)
    assert path.exists()
    assert provider.calls == []
    assert row_for(cfg, path.name).status is FileStatus.SENSITIVE


def test_the_desktop_defaults_take_documents_and_images_only(
    downloads: Path, desktop: Path
) -> None:
    cfg = two_inboxes(downloads, desktop, only=list(DESKTOP_ONLY), min_age_days=7, folders="ignore")
    names = [
        "Report.docx", "~$port.docx", "notes.md", "todo.txt", "export.csv", "Budget.numbers",
        "Thesis.PDF", "Screenshot 2026-11-01 at 10.00.00.png", "Talk.key",
    ]  # fmt: skip
    for name in names:
        put(desktop, name, b"content of " + name.encode())
    put(desktop / "Pkg.pages", "Index.zip", b"z")

    found = scan(cfg, lambda _sha: None, now=time.time() + 8 * DAY)

    assert sorted(c.path.name for c in found.candidates) == [
        "Budget.numbers",
        "Report.docx",
        "Screenshot 2026-11-01 at 10.00.00.png",
        "Talk.key",
        "Thesis.PDF",
    ]
    reasons = skipped(found)
    assert reasons["~$port.docx"] is SkipReason.IN_PROGRESS
    for name in ("notes.md", "todo.txt", "export.csv"):
        assert reasons[name] is SkipReason.NOT_ONLY
    assert reasons["Pkg.pages"] is SkipReason.FOLDER_IGNORED


# --- one unreadable input does not stop the others -----------------------------------------


def test_an_unreadable_input_is_skipped_and_the_others_scanned(
    downloads: Path, desktop: Path
) -> None:
    cfg = two_inboxes(downloads, desktop)
    put(downloads, "a.txt", b"a")
    put(desktop, "b.txt", b"b")
    desktop.chmod(0o000)
    try:
        outcome = pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    finally:
        desktop.chmod(0o755)
    assert not (downloads / "a.txt").exists()
    assert (desktop / "b.txt").exists()
    assert (desktop, SkipReason.INPUT_UNREADABLE) in outcome.scan.skipped
    assert outcome.scan.details[desktop].denied


def test_a_missing_input_is_skipped_through_the_cli(
    downloads: Path, fake_home: Path, tmp_path: Path
) -> None:
    gone = fake_home / "Gone"
    path = write_config(tmp_path, downloads)
    text = path.read_text(encoding="utf-8").replace(
        f'inputs: ["{downloads}"]', f'inputs: ["{downloads}", "{gone}"]'
    )
    path.write_text(text, encoding="utf-8")
    put(downloads, "a.txt", b"a")

    result = runner.invoke(app, ["run", "-c", str(path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert f"  skip    {gone}  (input folder could not be read" in result.stdout
    assert "warning: 1 input folder(s) could not be read and were skipped" in result.stdout
    cfg = fake_config(inputs=[str(downloads), str(gone)])
    state = st.collect(cfg)
    assert state.skipped is not None and state.skipped["input_unreadable"] == 1
    assert "1 input folder(s) that could not be read" in "\n".join(st.render(state, cfg))


def test_the_run_fails_only_when_no_input_can_be_read(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop)
    downloads.chmod(0o000)
    desktop.rmdir()
    try:
        with pytest.raises(ScanError, match=r"macOS denied access.*does not exist"):
            scan(cfg, lambda _sha: None)
    finally:
        downloads.chmod(0o755)


# --- files filed by rule are marked; the input a file came from is kept --------------------


def test_only_files_marked_as_filed_by_rule_are_left_out_of_reprocess(
    downloads: Path, desktop: Path
) -> None:
    cfg = two_inboxes(downloads, desktop)  # the Desktop has no fixed category yet
    put(desktop, "photo.jpg", b"filed by the AI")
    pipeline.run(cfg, FakeProvider(default=answer("Images")), simulate=False)
    ruled = two_inboxes(downloads, desktop, category="Images")
    put(desktop, "shot.png", b"filed by rule")
    pipeline.run(ruled, FakeProvider(), simulate=False)

    assert not row_for(ruled, "photo.jpg").filed_by_rule
    assert row_for(ruled, "shot.png").filed_by_rule
    with Store.open(ruled.database) as store:
        selection = rp.select(ruled, store)
    # Same input and category, but the AI filed it: it is reprocessed like any file.
    assert [t.row.original_name for t in selection.targets] == ["photo.jpg"]
    assert [(p.name, why) for p, why in selection.left] == [("shot.png", rp.RULE_FILED)]


def test_rule_decisions_leave_sensitive_files_in_place(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, category="Images")
    key = put(desktop, "id_rsa", b"key")
    shot = put(desktop, "shot.png", b"pixels")
    other = put(downloads, "a.txt", b"a")
    contexts = [replace(context_for(p, cfg), input=p.parent) for p in (key, shot, other)]

    decided, rest = rule_decisions(contexts, cfg)

    assert [c.path for c in rest] == [other]
    by_name = {d.context.path.name: d for d in decided}
    assert by_name["id_rsa"].classification is None and not by_name["id_rsa"].by_rule
    assert by_name["shot.png"].by_rule
    assert by_name["shot.png"].classification is not None
    assert by_name["shot.png"].classification.category == "Images"


def test_upsert_keeps_the_input_a_row_already_has(cfg: Config, downloads: Path) -> None:
    def record(source: Path | None) -> FileRecord:
        return FileRecord(
            "abc", downloads / "a.txt", downloads / "a.txt", FileStatus.FAILED,
            source_input=source,
        )  # fmt: skip

    with Store.open(cfg.database) as store:
        run = store.start_run(RunMode.RUN, RunTrigger.MANUAL)
        store.upsert_file(record(downloads), run_id=run)
        store.upsert_file(record(downloads.parent / "Desktop"), run_id=run)
        store.upsert_file(record(None), run_id=run)
        [row] = store.all_files()
    assert row.source_input == downloads


def test_reconcile_keeps_the_input_of_a_file_moved_inside_the_library(
    downloads: Path, desktop: Path
) -> None:
    cfg = two_inboxes(downloads, desktop)
    put(desktop, "b.txt", b"from the desktop")
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    (cfg.output_root / "Finance").mkdir()
    (cfg.output_root / "Other" / "b.txt").rename(cfg.output_root / "Finance" / "b.txt")

    with Store.open(cfg.database) as store:
        reconcile(cfg, store)

    row = row_for(cfg, "b.txt")
    assert (row.category, row.source_input) == ("Finance", desktop)


# --- ages ------------------------------------------------------------------------------------


def test_a_time_in_the_future_counts_as_brand_new(downloads: Path, desktop: Path) -> None:
    future = time.time() + 30 * DAY
    item = put(desktop, "from-the-future.pdf", b"x", mtime=future)
    assert item_age(os.lstat(item), time.time()) == 0.0
    cfg = two_inboxes(downloads, desktop, min_age_days=3)
    found = scan(cfg, lambda _sha: None, now=time.time() + DAY)
    assert skipped(found)["from-the-future.pdf"] is SkipReason.TOO_RECENT
    assert found.days_left[item] == 3


# --- upgrading an index ----------------------------------------------------------------------


@pytest.mark.parametrize("version", range(1, 16))
def test_an_index_from_any_version_upgrades_with_its_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    path = tmp_path / "library.db"
    before = [m for m in load_migrations() if m.version <= version]
    with monkeypatch.context() as m:
        m.setattr("file_librarian.db.store.load_migrations", lambda: before)
        old = Store.open(path)
    with old:
        old.conn.execute(
            """INSERT INTO files (sha256, original_name, original_path, current_name,
                   current_path, extension, size_bytes, tags, status, description, category,
                   processed_at)
               VALUES ('abc', 'Relatório.pdf', '/U/Downloads/Relatório.pdf', 'r.pdf',
                       '/L/Finance/r.pdf', '.pdf', 3, '[]', 'organized', 'conta de luz',
                       'Finance', '2026-01-01T00:00:00+00:00')"""
        )
    with Store.open(path) as store:
        assert store.schema_version == len(load_migrations())
        [row] = store.all_files()
        assert (row.source_input, row.filed_by_rule, row.description) == (
            None,
            False,
            "conta de luz",
        )
        assert store.count_by_input() == {"/U/Downloads": {"organized": 1}}
        assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
