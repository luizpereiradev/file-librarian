"""Sensitive by name, precisely and not for ever (#89 review, second round).

Names are matched by whole words; a file sensitive only because of its name is
looked at again once its name no longer matches; the user can allow names; and
``flib status --sensitive`` says which files were left in place and why.
"""

from __future__ import annotations

import os
import random
import uuid
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import config as config_mod
from file_librarian import onboarding, pipeline
from file_librarian import reprocess as rp
from file_librarian import status as st
from file_librarian.cli import app
from file_librarian.config import canonical, is_within
from file_librarian.db.store import Store
from file_librarian.fix import fix_file
from file_librarian.models import FileRecord, FileStatus, RunMode, RunTrigger
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.sensitive import name_words, sensitive_reason
from tests._helpers import answer, fake_config, put, row_for, write_config

runner = CliRunner()


# --- whole words -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("ChromePasswords.csv", ["chrome", "passwords"]),
        ("1Password-8.10.dmg", ["1password", "8", "10"]),
        ("my_private-key.asc", ["my", "private", "key"]),
        ("3f2fa1b0-9c1e.pdf", ["3f2fa1b0", "9c1e"]),
        ("Passwörter 2026.txt", ["passwörter", "2026"]),
        ("README", ["readme"]),
    ],
)
def test_names_are_cut_into_words(name: str, words: list[str]) -> None:
    assert name_words(name) == words


@pytest.mark.parametrize(
    "name",
    [
        "3f2fa1b0-9c1e-4d2a-8f00-aa11bb22cc33.pdf",
        "invoice_a82fa9.pdf",
        "1Password-8.10.dmg",
        "1Password.pkg",
        "recovery keyboard manual.pdf",
        "recovery key holder design.pdf",
        "passwordless login guide.pdf",
        "keyboard backup.zip",
        "seed catalog.pdf",
        "passwords.dmg",  # an installer is never sensitive by its name alone
        "Senhor dos Anéis.epub",
    ],
)
def test_ordinary_names_are_not_caught(name: str) -> None:
    assert sensitive_reason(name) is None


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("Chrome Passwords.csv", "passwords"),
        ("ChromePasswords.csv", "passwords"),
        ("password.txt", "password"),
        ("passwd", "passwd"),
        ("2FA setup.png", "2fa"),
        ("minhas senhas.xlsx", "senhas"),
        ("contraseñas.txt", "contraseñas"),
        ("mots de passe.txt", "mots de passe"),
        ("Passwörter.txt", "passwörter"),
        ("github-recovery-codes.txt", "recovery codes"),
        ("backup_codes.pdf", "backup codes"),
        ("wallet seed phrase.jpg", "seed phrase"),
        ("my_private_key.asc", "private key"),
        ("Recovery Key.pdf", "recovery key"),
        ("recovery key 2.pdf", "recovery key"),
        ("recovery key notes.txt", "recovery key"),
        ("bitwarden_export_20261120.json", "bitwarden export"),
        ("logins.csv", "a credential file"),
        ("id_rsa", "a credential file"),
    ],
)
def test_names_of_passwords_and_recovery_material(name: str, reason: str) -> None:
    assert sensitive_reason(name) == reason


def test_random_hexadecimal_and_uuid_names_never_match() -> None:
    rng = random.Random(89)
    names = []
    for _ in range(3000):
        names.append(f"{uuid.UUID(int=rng.getrandbits(128))}.pdf")
        names.append(f"{uuid.UUID(int=rng.getrandbits(128)).hex}.png")
        size = rng.randint(3, 40)
        names.append(f"{rng.getrandbits(4 * size):0{size}x}{rng.choice(['.pdf', '', '.jpg'])}")
        names.append(f"invoice_{rng.getrandbits(24):06x}.pdf")
    assert [n for n in names if sensitive_reason(n) is not None] == []


def test_allow_names_lets_a_name_through_but_not_a_credential_file() -> None:
    assert sensitive_reason("password reset guide.pdf", ["password reset*"]) is None
    assert sensitive_reason("password list.txt", ["password reset*"]) == "password"


# --- the reason is recorded; a name-only verdict is looked at again ---------------------


def test_a_file_sensitive_by_name_alone_is_filed_once_renamed(downloads: Path) -> None:
    cfg = fake_config(downloads)
    path = put(downloads, "password reset guide.pdf", b"how to reset a password")
    provider = FakeProvider(default=answer("Other"))
    pipeline.run(cfg, provider, simulate=False)
    row = row_for(cfg, path.name)
    assert (row.status, row.sensitive_by, row.sensitive_reason) == (
        FileStatus.SENSITIVE,
        "name",
        "name looks like a credential file: password",
    )
    assert provider.calls == []

    path.rename(downloads / "reset guide.pdf")
    pipeline.run(cfg, provider, simulate=False)

    assert [c.path.name for c in provider.calls] == ["reset guide.pdf"]
    assert (cfg.output_root / "Other" / "reset guide.pdf").exists()
    row = row_for(cfg, "password reset guide.pdf")
    assert row.status is FileStatus.ORGANIZED


def test_allow_names_releases_a_file_left_in_place_earlier(downloads: Path) -> None:
    put(downloads, "password reset guide.pdf", b"how to reset a password")
    pipeline.run(fake_config(downloads), FakeProvider(default=answer("Other")), simulate=False)
    allowing = fake_config(downloads, sensitive={"allow_names": ["password reset*"]})

    pipeline.run(allowing, FakeProvider(default=answer("Other")), simulate=False)

    assert (allowing.output_root / "Other" / "password reset guide.pdf").exists()


def test_a_secret_in_the_content_keeps_a_file_sensitive_when_renamed(downloads: Path) -> None:
    cfg = fake_config(downloads)
    path = put(downloads, "notes.txt", b"AKIAABCDEFGHIJKLMNOP is the key")
    provider = FakeProvider(default=answer("Other"))
    pipeline.run(cfg, provider, simulate=False)
    assert row_for(cfg, "notes.txt").sensitive_by == "content"

    path.rename(downloads / "harmless.txt")
    outcome = pipeline.run(cfg, provider, simulate=False)

    assert (downloads / "harmless.txt").exists()
    assert provider.calls == []
    assert ("harmless.txt", "already processed") in [
        (p.name, r.value) for p, r in outcome.scan.skipped
    ]


def test_a_renamed_file_whose_content_holds_a_secret_becomes_content_sensitive(
    downloads: Path,
) -> None:
    cfg = fake_config(downloads)
    path = put(downloads, "passwords.txt", b"token ghp_" + b"a" * 36)
    pipeline.run(cfg, FakeProvider(), simulate=False)
    path.rename(downloads / "list.txt")
    pipeline.run(cfg, FakeProvider(), simulate=False)
    row = row_for(cfg, "passwords.txt")
    assert (row.status, row.sensitive_by) == (FileStatus.SENSITIVE, "content")
    assert row.sensitive_reason == "content: looks like GitHub token"


# --- what the user sees -------------------------------------------------------------------


def test_the_run_says_why_with_a_space_after_the_status(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads)
    put(downloads, "3f2fa1b0-9c1e-4d2a-8f00-aa11bb22cc33.pdf", b"an invoice")
    put(downloads, "1Password-8.10.dmg", b"installer")
    put(downloads, "Chrome Passwords.csv", b"name,url,password")

    result = runner.invoke(app, ["run", "-c", str(path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    assert (
        "  sensitive Chrome Passwords.csv  (sensitive: name looks like a credential file: "
        "passwords)"
    ) in result.stdout
    assert "sensitive3f2" not in result.stdout and "sensitive1Password" not in result.stdout
    assert "1 sensitive left in place" in result.stdout


def test_status_sensitive_lists_each_file_and_why(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads)
    put(downloads, "Chrome Passwords.csv", b"name,url,password")
    put(downloads, "notes.txt", b"AKIAABCDEFGHIJKLMNOP")
    runner.invoke(app, ["run", "-c", str(path)])

    plain = runner.invoke(app, ["status", "-c", str(path)])
    listed = runner.invoke(app, ["status", "--sensitive", "-c", str(path)])

    assert "Chrome Passwords" not in plain.stdout  # only counted by default
    assert listed.exit_code == 0, listed.stderr
    assert (
        f"  {downloads / 'Chrome Passwords.csv'}  (name looks like a credential file: passwords)"
    ) in listed.stdout
    assert f"  {downloads / 'notes.txt'}  (content: looks like AWS access key)" in listed.stdout
    assert "Left in place as sensitive: 2 files" in listed.stdout


def test_status_sensitive_without_an_index_or_files(downloads: Path) -> None:
    cfg = fake_config(downloads)
    assert st.render_sensitive(st.sensitive_files(cfg)) == [
        "No file is left in place as sensitive."
    ]


def test_doctor_never_lists_sensitive_files(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads)
    put(downloads, "Chrome Passwords.csv", b"name,url,password")
    runner.invoke(app, ["run", "-c", str(path)])
    result = runner.invoke(app, ["doctor", "-c", str(path)])
    assert "Chrome Passwords" not in result.stdout + result.stderr


# --- screenshots in other languages --------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Captura de pantalla 2026-11-01 a las 10.00.00.png",
        "Capture d\u2019écran 2026-11-01 à 10.00.00.png",
        "Bildschirmfoto 2026-11-01 um 10.00.00.png",
        "Schermata 2026-11-01 alle 10.00.00.png",
        "スクリーンショット 2026-11-01 10.00.00.png",
        "Grabación de pantalla 2026-11-01.mov",
        "画面収録 2026-11-01.mov",
    ],
)
def test_screenshot_names_in_other_languages_match(fake_home: Path, name: str) -> None:
    folder = fake_home / "Shots"
    put(folder, name, b"x")
    put(folder, "taxes.pdf", b"y")
    assert onboarding.count_matching(folder, onboarding.SCREENSHOT_ONLY) == 1


def test_a_folder_with_no_screenshot_says_so_and_defaults_to_no(
    downloads: Path, fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(fake_home / "Shots", "taxes.pdf", b"y")
    monkeypatch.setenv("FAKE_SCREENSHOT_LOCATION", "~/Shots")
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert "(0 item(s) there look like screenshots" in result.stdout
    assert "the answer you probably want is no" in result.stdout
    assert "File screenshots too? [y/N]: n" in result.stdout


# --- the firmlink: stripping and identity, each on its own ----------------------------------


def test_the_firmlink_prefix_is_stripped_even_for_a_path_that_does_not_exist() -> None:
    missing = Path("/Users/no-such-user-89/Downloads/x.pdf")
    assert canonical(Path("/System/Volumes/Data") / missing.relative_to("/")) == canonical(missing)
    other = Path("/System/Volumes/Data/not-a-firmlink-89/x")
    assert canonical(other) == canonical(other)  # left as it is
    assert str(canonical(other)).startswith("/system/volumes/data")


def test_the_firmlinked_roots_come_from_the_system_file_or_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    listing = tmp_path / "firmlinks"
    listing.write_text("/Users\tUsers\n/usr/local\tusr/local\n", encoding="utf-8")
    with monkeypatch.context() as m:
        m.setattr(config_mod, "FIRMLINKS_FILE", listing)
        config_mod.firmlinked_roots.cache_clear()
        assert config_mod.firmlinked_roots() == (("users",), ("usr", "local"))
        m.setattr(config_mod, "FIRMLINKS_FILE", tmp_path / "missing")
        config_mod.firmlinked_roots.cache_clear()
        assert ("private",) in config_mod.firmlinked_roots()
    config_mod.firmlinked_roots.cache_clear()


@pytest.mark.skipif(not Path("/System/Volumes/Data").is_dir(), reason="macOS only")
def test_device_and_inode_catch_a_firmlink_without_the_stripping(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias = Path("/System/Volumes/Data") / Path(os.path.realpath(downloads)).relative_to("/")
    with monkeypatch.context() as m:
        m.setattr(config_mod, "_without_data_volume", lambda path: path)
        assert canonical(alias) != canonical(downloads)  # the path alone no longer tells
        assert is_within(alias, downloads)
        assert config_mod.same_place(alias, downloads)


# --- the rule marker and the input are kept or cleared as they should ----------------------


def ruled(downloads: Path, desktop: Path) -> Any:
    return fake_config(
        inputs=[str(downloads), {"path": str(desktop), "category": "Images"}],
        output_root=str(downloads.parent / "Lib"),
    )


def test_a_fix_clears_the_rule_marker(downloads: Path, fake_home: Path) -> None:
    desktop = fake_home / "Desktop"
    desktop.mkdir()
    cfg = ruled(downloads, desktop)
    put(desktop, "shot.png", b"pixels")
    pipeline.run(cfg, FakeProvider(), simulate=False)
    assert row_for(cfg, "shot.png").filed_by_rule

    with Store.open(cfg.database) as store:
        fix_file(cfg, store, cfg.output_root / "Images" / "shot.png", category="Other")
        selection = rp.select(cfg, store, override_corrections=True)

    assert not row_for(cfg, "shot.png").filed_by_rule
    assert [t.row.original_name for t in selection.targets] == ["shot.png"]


def test_a_correction_reconcile_records_clears_the_rule_marker(
    downloads: Path, fake_home: Path
) -> None:
    desktop = fake_home / "Desktop"
    desktop.mkdir()
    cfg = ruled(downloads, desktop)
    put(desktop, "shot.png", b"pixels")
    pipeline.run(cfg, FakeProvider(), simulate=False)
    (cfg.output_root / "Other").mkdir()
    (cfg.output_root / "Images" / "shot.png").rename(cfg.output_root / "Other" / "shot.png")

    with Store.open(cfg.database) as store:
        reconcile(cfg, store)

    row = row_for(cfg, "shot.png")
    assert (row.category, row.filed_by_rule, row.source_input) == ("Other", False, desktop)


def test_upserts_keep_source_input_and_follow_the_rule_marker(cfg: Any, downloads: Path) -> None:
    other = downloads.parent / "Desktop"

    def record(source: Path | None, by_rule: bool, duplicate_of: int | None = None) -> FileRecord:
        return FileRecord(
            "abc", downloads / "a.png", downloads / "a.png", FileStatus.ORGANIZED,
            source_input=source, by_rule=by_rule, duplicate_of=duplicate_of,
        )  # fmt: skip

    with Store.open(cfg.database) as store:
        run = store.start_run(RunMode.RUN, RunTrigger.MANUAL)
        original = store.upsert_file(record(downloads, True), run_id=run)
        store.upsert_file(record(other, False), run_id=run)
        [row] = store.all_files()
        assert (row.source_input, row.filed_by_rule) == (downloads, False)
        store.upsert_file(record(None, True), run_id=run)
        assert store.file_by_id(original).filed_by_rule

        duplicate = store.upsert_file(record(downloads, False, original), run_id=run)
        store.upsert_file(record(other, False, original), run_id=run)  # the same copy again
        assert store.file_by_id(duplicate).source_input == downloads
