"""``locality``: "fully local: yes/no" and what leaves the Mac (#92)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import locality
from file_librarian.cli import app
from file_librarian.doctor import Options, build_report
from file_librarian.providers import ollama
from tests._helpers import fake_config, write_config

LOCAL_SEARCH = {"semantic": {"enabled": True, "model": "embeddinggemma"}}


def lines(**overrides: Any) -> list[str]:
    return locality.summary_lines(fake_config(**overrides))


def test_local_ollama_with_local_search_is_fully_local() -> None:
    config = fake_config(provider={"name": "ollama", "model": "m"}, search=LOCAL_SEARCH)
    assert locality.what_leaves(config) == []
    assert locality.summary_lines(config)[0].startswith("fully local: yes")
    assert lines()[0].startswith("fully local: yes")  # the offline fake provider


@pytest.mark.parametrize(
    ("provider", "vision", "where", "images"),
    [
        ({"name": "anthropic", "model": "m"}, True, "the Anthropic API", "scanned pages"),
        ({"name": "anthropic", "model": "m"}, False, "the Anthropic API", None),
        ({"name": "claude-code", "model": "haiku"}, True, "Anthropic (Claude Code)", None),
        (
            {"name": "ollama", "model": "m", "base_url": "http://gpu.lan:11434/v1", "vision": True},
            True,
            "gpu.lan",
            "images",
        ),
        (
            {"name": "openai-compat", "model": "m", "base_url": "https://api.example.com/v1"},
            True,
            "api.example.com",
            None,
        ),
    ],
)
def test_remote_providers_are_named(
    provider: dict[str, Any], vision: bool, where: str, images: str | None
) -> None:
    config = fake_config(provider=provider, vision={"enabled": vision})
    [leaves] = locality.what_leaves(config)
    assert leaves.where == where and "text excerpts" in leaves.what
    assert (images in leaves.what) if images else ("images" not in leaves.what)


def test_remote_embeddings_leave_too() -> None:
    search = {"semantic": {"enabled": True, "base_url": "https://emb.example.com/v1"}}
    config = fake_config(provider={"name": "ollama", "model": "m"}, search=search)
    assert locality.summary_lines(config) == [
        "fully local: no",
        "  leaves this Mac: the index's names, tags and descriptions, and search queries, "
        "to emb.example.com",
    ]
    assert locality.summary_lines(config, hosts=False)[1].endswith("to a remote server")


def test_anthropic_is_named_even_without_hosts() -> None:
    config = fake_config(provider={"name": "anthropic", "model": "m"})
    assert locality.summary_lines(config, hosts=False)[1].endswith("to the Anthropic API")


def test_check_config_and_doctor_say_it(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ollama, "check", lambda _cfg: (True, "ok"))
    local = write_config(tmp_path, downloads, "{name: ollama, model: m}")
    result = CliRunner().invoke(app, ["check-config", "-c", str(local)])
    assert result.exit_code == 0, result.output
    assert "  fully local: yes" in result.stdout
    remote = write_config(tmp_path, downloads, "{name: anthropic, model: m}")
    result = CliRunner().invoke(app, ["check-config", "-c", str(remote)])
    assert "  fully local: no" in result.stdout
    assert "leaves this Mac: file names, metadata and text excerpts to classify" in result.stdout
    report = build_report(Options(config_path=remote, run_logs=0))
    assert "fully local: no" in report and "to the Anthropic API" in report
