"""Semantic search settings, keys, vectors and what check-config says (RF-15, #36)."""

from __future__ import annotations

import math
from array import array
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import credentials, embeddings, keychain
from file_librarian.cli import app
from file_librarian.config import (
    DEFAULT_EMBEDDING_MODEL,
    OLLAMA_BASE_URL,
    ConfigError,
    load_config,
    parse_config,
)
from file_librarian.db.store import FileRow
from file_librarian.doctor import Redactor, redacted_config
from file_librarian.embeddings import dot, nearest, pack, row_text, unpack
from file_librarian.models import FileStatus
from tests._helpers import fake_config, write_config

FAKE = {"name": "fake", "model": "x"}


def semantic(**values: object) -> dict[str, object]:
    return {"provider": FAKE, "search": {"semantic": values}}


def test_defaults_are_off_local_and_documented() -> None:
    settings = parse_config({"provider": FAKE}).search.semantic
    assert settings.enabled is False and settings.default is False
    assert settings.base_url == OLLAMA_BASE_URL
    assert settings.model == DEFAULT_EMBEDDING_MODEL == "embeddinggemma"
    assert settings.api_key_env is None
    example = Path(__file__).parents[2] / "config.example.yaml"
    assert load_config(example).search.semantic == settings


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"base_url": "ftp://x"}, "search.semantic.base_url must be an http"),
        ({"base_url": "https://u:p@x/v1"}, "search.semantic.base_url must not contain"),
        ({"base_url": "http://x:99999"}, "search.semantic.base_url is not a valid URL"),
        ({"api_key_env": "1BAD"}, "environment variable name"),
        ({"model": ""}, "search.semantic.model"),
        ({"batch_size": 0}, "batch_size"),
        ({"surprise": True}, "surprise"),
    ],
)
def test_bad_settings_are_refused(values: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(semantic(**values))


def test_a_trailing_slash_is_dropped() -> None:
    cfg = parse_config(semantic(base_url="https://embed.example/v1/"))
    assert cfg.search.semantic.base_url == "https://embed.example/v1"


# --- vectors ------------------------------------------------------------------------------


def test_vectors_round_trip_as_float32() -> None:
    vector = array("f", [0.6, 0.8])
    blob = pack(vector)
    assert len(blob) == 8
    assert list(unpack(blob)) == list(vector)


def test_dot_works_without_sumprod(monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = array("f", [0.6, 0.8]), array("f", [1.0, 0.0])
    with monkeypatch.context() as patch:
        patch.setattr(embeddings, "_sumprod", None)
        assert math.isclose(dot(a, b), 0.6, rel_tol=1e-6)
    with monkeypatch.context() as patch:
        patch.setattr(embeddings, "_sumprod", lambda x, y: math.fsum(map(float.__mul__, x, y)))
        assert math.isclose(dot(a, b), 0.6, rel_tol=1e-6)


def test_nearest_is_best_first() -> None:
    query = array("f", [1.0, 0.0])
    vectors = [
        (1, pack(array("f", [0.0, 1.0]))),
        (2, pack(array("f", [1.0, 0.0]))),
        (3, pack(array("f", [0.6, 0.8]))),
    ]
    assert [i for i, _ in nearest(query, vectors, limit=2)] == [2, 3]


def test_row_text_is_index_metadata_only() -> None:
    row = FileRow(
        id=1,
        sha256="0" * 64,
        original_name="scan.pdf",
        original_path=Path("/d/scan.pdf"),
        current_name="Power bill.pdf",
        current_path=Path("/o/Finance/Power bill.pdf"),
        status=FileStatus.ORGANIZED,
        category="Finance",
        description="d" * 5000,
        tags=["energy", "home"],
        attempts=1,
        processed_at="2026-01-01T00:00:00+00:00",
        size_bytes=1,
        is_unit=False,
    )
    text = row_text(row)
    assert text.startswith(
        "name: Power bill.pdf\noriginal name: scan.pdf\ncategory: Finance\ntags: energy, home\n"
    )
    assert len(text) == embeddings.MAX_TEXT_CHARS


# --- keys ---------------------------------------------------------------------------------


def test_key_set_for_embeddings_uses_its_own_account(tmp_path: Path, downloads: Path) -> None:
    config = write_config(tmp_path, downloads)
    runner = CliRunner()
    saved = runner.invoke(
        app, ["key", "set", "--provider", "embeddings", "-c", str(config)], input="emb-key-123\n"
    )
    assert saved.exit_code == 0, saved.stderr
    assert "embeddings-api-key" in saved.stdout and "emb-key-123" not in saved.stdout
    assert keychain.read(keychain.EMBEDDINGS_ACCOUNT) == "emb-key-123"
    assert keychain.read(keychain.OPENAI_COMPAT_ACCOUNT) is None
    found = credentials.resolve_embeddings(parse_config({"provider": FAKE}).search.semantic)
    assert (found.value, found.source) == ("emb-key-123", "keychain")
    wrong = runner.invoke(app, ["key", "set", "--provider", "fake", "-c", str(config)])
    assert "anthropic, openai-compat or embeddings" in wrong.stderr


def test_the_embeddings_key_is_redacted_in_doctor_reports() -> None:
    names = {spec.provider for spec in credentials.all_specs(fake_config().provider)}
    assert "embeddings" in names


# --- check-config and doctor ------------------------------------------------------------


def test_check_config_says_when_it_is_off(tmp_path: Path, downloads: Path) -> None:
    result = CliRunner().invoke(app, ["check-config", "-c", str(write_config(tmp_path, downloads))])
    assert result.exit_code == 0
    assert "semantic search: off" in result.stdout


def test_check_config_local_embeddings(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLIB_EMBEDDINGS_API_KEY", "local-key-abcdef")
    config = write_config(tmp_path, downloads, extra="search: {semantic: {enabled: true}}\n")
    result = CliRunner().invoke(app, ["check-config", "-c", str(config)])
    assert result.exit_code == 0
    assert f"semantic search: embeddinggemma at {OLLAMA_BASE_URL} (this Mac)" in result.stdout
    assert "embeddings api key: environment" in result.stdout
    assert "leave it" not in result.stderr


@pytest.mark.parametrize("scheme", ["https", "http"])
def test_check_config_warns_when_index_text_leaves_the_mac(
    tmp_path: Path, downloads: Path, scheme: str
) -> None:
    extra = f"search: {{semantic: {{enabled: true, base_url: '{scheme}://embed.example/v1'}}}}\n"
    config = write_config(tmp_path, downloads, extra=extra)
    result = CliRunner().invoke(app, ["check-config", "-c", str(config)])
    assert result.exit_code == 0
    assert "(a remote server)" in result.stdout
    assert "search.semantic.base_url is not this Mac" in result.stderr
    assert "not priced" in result.stderr
    assert ("unencrypted" in result.stderr) is (scheme == "http")


def test_doctor_hides_the_embeddings_host() -> None:
    cfg = parse_config(semantic(enabled=True, base_url="https://private.example/v1"))
    text = "\n".join(redacted_config(cfg, Redactor(home=Path.home())))
    assert "private.example" not in text and "<https, remote>" in text
