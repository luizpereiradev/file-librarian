from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import credentials, keychain
from file_librarian.cli import app
from file_librarian.config import ProviderConfig

KEY_ENV = "sk-ant-" + "env-" + "x" * 20
KEY_CHAIN = "sk-ant-" + "chain-" + "y" * 20
KEY_FILE = "sk-ant-" + "file-" + "z" * 20


def provider(**kw: object) -> ProviderConfig:
    return ProviderConfig.model_validate({"name": "anthropic", "model": "m"} | kw)


def env_file(tmp_path: Path, value: str = KEY_FILE) -> Path:
    path = tmp_path / ".env"
    path.write_text(f"# comment\nOTHER=1\nexport ANTHROPIC_API_KEY='{value}'\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_keychain_roundtrip_and_missing() -> None:
    assert keychain.read() is None
    keychain.write(KEY_CHAIN)
    assert keychain.read() == KEY_CHAIN
    assert keychain.delete() is True
    assert keychain.delete() is False
    with pytest.raises(ValueError):
        keychain.write("two\nlines")


def test_auto_prefers_environment_then_keychain_then_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = provider(env_file=str(env_file(tmp_path)))
    assert credentials.resolve(cfg) == credentials.ResolvedKey(KEY_FILE, "env_file")
    keychain.write(KEY_CHAIN)
    assert credentials.resolve(cfg).source == "keychain"
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY_ENV)
    assert credentials.resolve(cfg) == credentials.ResolvedKey(KEY_ENV, "environment")


def test_explicit_sources_look_only_there(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY_ENV)
    keychain.write(KEY_CHAIN)
    path = env_file(tmp_path)
    assert credentials.resolve(provider(api_key_source="keychain")).value == KEY_CHAIN
    assert (
        credentials.resolve(provider(api_key_source="env_file", env_file=str(path))).value
        == KEY_FILE
    )
    assert credentials.resolve(provider(api_key_source="env_file")).source == "none"


def test_nothing_found() -> None:
    assert credentials.resolve(provider()) == credentials.ResolvedKey(None, "none")


def test_env_file_privacy_check(tmp_path: Path) -> None:
    path = env_file(tmp_path)
    assert credentials.env_file_is_private(path)
    path.chmod(0o644)
    assert not credentials.env_file_is_private(path)


def test_hint_never_reveals_the_key() -> None:
    found = credentials.ResolvedKey(KEY_ENV, "environment")
    assert found.hint == "…" + KEY_ENV[-4:]
    assert KEY_ENV[:-4] not in found.hint


def test_cli_key_set_status_delete(tmp_path: Path) -> None:
    runner = CliRunner()
    cfg = tmp_path / "c.yaml"
    cfg.write_text("provider: {name: anthropic, model: m}\n", encoding="utf-8")
    missing = runner.invoke(app, ["key", "status", "-c", str(cfg)])
    assert missing.exit_code == 1 and "flib key set" in missing.stdout
    saved = runner.invoke(app, ["key", "set"], input=KEY_CHAIN + "\n")
    assert saved.exit_code == 0 and KEY_CHAIN not in saved.stdout
    status = runner.invoke(app, ["key", "status", "-c", str(cfg)])
    assert "keychain" in status.stdout and KEY_CHAIN not in status.stdout
    check = runner.invoke(app, ["check-config", "-c", str(cfg)])
    assert "api key: keychain" in check.stdout
    assert runner.invoke(app, ["key", "delete"]).stdout.startswith("Removed")


def test_check_config_warns_about_a_readable_env_file(tmp_path: Path) -> None:
    path = env_file(tmp_path)
    path.chmod(0o644)
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"provider: {{name: anthropic, model: m, env_file: {path}}}\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["check-config", "-c", str(cfg)])
    assert "chmod 600" in result.stderr


def test_tests_never_touch_the_real_keychain() -> None:
    assert keychain.SECURITY != "/usr/bin/security"


@pytest.fixture
def broken_keychain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "security"
    script.write_text("#!/bin/sh\nexit 5\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setattr("file_librarian.keychain.SECURITY", str(script))


@pytest.mark.usefixtures("broken_keychain")
def test_keychain_failures_raise_keychain_error() -> None:
    with pytest.raises(keychain.KeychainError, match="read failed"):
        keychain.read()
    with pytest.raises(keychain.KeychainError, match="write failed"):
        keychain.write(KEY_CHAIN)
    with pytest.raises(keychain.KeychainError, match="delete failed"):
        keychain.delete()


def test_missing_security_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("file_librarian.keychain.SECURITY", "/nonexistent/security")
    with pytest.raises(keychain.KeychainError, match="cannot run"):
        keychain.read()
    # Resolution treats an unusable Keychain as "no key there", not as a crash.
    assert credentials.resolve(provider()).source == "none"


@pytest.mark.usefixtures("broken_keychain")
def test_cli_reports_keychain_failures() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["key", "set"], input=KEY_CHAIN + "\n").exit_code == 1
    assert runner.invoke(app, ["key", "delete"]).exit_code == 1


def test_key_set_warns_about_an_unusual_key() -> None:
    result = CliRunner().invoke(app, ["key", "set"], input="not-an-anthropic-key-123\n")
    assert result.exit_code == 0 and "usually start with sk-ant-" in result.stderr
