"""``flib init --local`` and the "local" answer in ``flib init`` (#92).

Ollama is the in-process fake on 127.0.0.1 (``fake_openai``) or absent; no test
reaches a real Ollama, installs anything or downloads a model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import config as config_module
from file_librarian import onboarding
from file_librarian.cli import app
from file_librarian.config import load_config
from file_librarian.providers import ProviderError, claude_code
from file_librarian.providers import ollama as ollama_module
from tests._helpers import put
from tests.fake_openai import FakeOpenAI, Reply, completion

runner = CliRunner()
ANSWER = {
    "category": "Finance",
    "suggested_name": "Electricity invoice",
    "name_choice_reason": "The current name says nothing.",
    "name_choice": "proposed",
    "description": "Electricity invoice for October.",
    "tags": ["invoice"],
    "confidence": 0.95,
    "document_date": "2026-10-05",
}
MODEL = onboarding.LOCAL_MODEL
EMBEDDER = onboarding.LOCAL_EMBEDDING_MODEL


@pytest.fixture(autouse=True)
def _setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_code, "find_claude", lambda *_a, **_k: None)
    monkeypatch.setattr(ollama_module, "ollama_command", lambda: None)
    for name in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


@pytest.fixture
def ollama(fake_openai: FakeOpenAI, monkeypatch: pytest.MonkeyPatch) -> FakeOpenAI:
    """A running Ollama: the fake server, found by ``flib init``."""
    monkeypatch.setattr(config_module, "OLLAMA_BASE_URL", fake_openai.url)
    monkeypatch.setattr(onboarding, "probe_ollama", lambda: list(fake_openai.models))
    return fake_openai


def pulls(server: FakeOpenAI) -> list[str]:
    return [r.body["model"] for r in server.requests if r.path == "/api/pull"]


def progress(*events: object) -> Reply:
    return Reply(body=b"".join(json.dumps(e).encode() + b"\n" for e in events))


def test_ollama_missing_explains_how_to_install_it(tmp_path: Path, downloads: Path) -> None:
    path = tmp_path / "c.yaml"
    result = runner.invoke(app, ["init", "--local", "--yes", "-c", str(path)])
    assert result.exit_code == 1
    assert "Ollama is not installed" in result.stderr
    assert "brew install ollama" in result.stderr and "ollama.com/download" in result.stderr
    assert "does not install it" in result.stderr
    assert not path.exists()


def test_ollama_installed_but_not_running(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ollama_module, "ollama_command", lambda: "/opt/homebrew/bin/ollama")
    result = runner.invoke(app, ["init", "--local", "--yes", "-c", str(tmp_path / "c.yaml")])
    assert result.exit_code == 1
    assert "installed (/opt/homebrew/bin/ollama) but not running" in result.stderr
    assert "ollama serve" in result.stderr


def test_running_without_models_never_downloads_with_yes(
    tmp_path: Path, downloads: Path, ollama: FakeOpenAI
) -> None:
    ollama.models = []
    path = tmp_path / "c.yaml"
    result = runner.invoke(app, ["init", "--local", "--yes", "-c", str(path)])
    assert result.exit_code == 0, result.output
    assert f"Not downloaded yet: {MODEL} (" in result.stdout
    assert f"{EMBEDDER} (0.6 GB)" in result.stdout
    assert f"ollama pull {MODEL}; ollama pull {EMBEDDER}" in result.stdout
    assert pulls(ollama) == []
    cfg = load_config(path)
    assert (cfg.provider.name, cfg.provider.model) == ("ollama", MODEL)
    assert cfg.search.semantic.enabled and cfg.search.semantic.model == EMBEDDER
    assert cfg.vision.enabled is onboarding.LOCAL_VISION
    assert "Skipping the trial" in result.stdout
    assert f"ollama pull {MODEL}" in result.stderr


def test_running_without_models_downloads_them_when_asked(
    tmp_path: Path, downloads: Path, ollama: FakeOpenAI
) -> None:
    ollama.models = [f"{EMBEDDER}:latest"]
    ollama.script(
        progress(
            {"status": "pulling manifest"},
            {"status": "pulling abc", "total": 1000, "completed": 0},
            {"status": "pulling abc", "total": 1000, "completed": 420},
            {"status": "pulling abc", "total": 1000, "completed": 1000},
            {"status": "verifying sha256 digest"},
            {"status": "success"},
        )
    )
    # language, folder, output, review, then: download? yes; trial skipped; no schedule
    answers = "\n\n\n\ny\n" + "n\n" * 6
    result = runner.invoke(app, ["init", "--local", "-c", str(tmp_path / "c.yaml")], input=answers)
    assert result.exit_code == 0, result.output
    assert f"Download them now ({onboarding.LOCAL_MODELS[MODEL]:.1f} GB)?" in result.stdout
    assert pulls(ollama) == [MODEL]
    for line in ("pulling manifest", "downloading 40%", "downloading 100%", "done"):
        assert f"  {MODEL}: {line}" in result.stdout


def test_a_failed_download_is_a_warning(
    tmp_path: Path, downloads: Path, ollama: FakeOpenAI
) -> None:
    ollama.models = []
    ollama.script(progress({"error": "pull model manifest: file does not exist"}))
    answers = "\n\n\n\ny\n" + "n\n" * 6
    result = runner.invoke(app, ["init", "--local", "-c", str(tmp_path / "c.yaml")], input=answers)
    assert result.exit_code == 0, result.output
    assert "Ollama could not pull" in result.stderr and "file does not exist" in result.stderr


def test_ready_runs_the_trial_and_states_the_quality(
    tmp_path: Path, downloads: Path, ollama: FakeOpenAI
) -> None:
    ollama.models = [MODEL, f"{EMBEDDER}:latest"]
    ollama.script(completion(ANSWER))
    put(downloads, "invoice.txt", b"electricity invoice october")
    path = tmp_path / "c.yaml"
    result = runner.invoke(app, ["init", "--local", "--yes", "-c", str(path)])
    assert result.exit_code == 0, result.output
    assert "Both models are already downloaded." in result.stdout
    assert "Trial on 1 copies" in result.stdout
    assert onboarding.LOCAL_QUALITY in result.stdout
    [chat] = [r for r in ollama.requests if r.path == "/api/chat"]
    assert chat.body["model"] == MODEL
    check = runner.invoke(app, ["check-config", "-c", str(path)])
    assert "fully local: yes" in check.stdout


def test_local_is_an_answer_in_the_interactive_flow(
    tmp_path: Path, downloads: Path, ollama: FakeOpenAI
) -> None:
    ollama.models = [MODEL, f"{EMBEDDER}:latest"]
    path = tmp_path / "c.yaml"
    answers = "\n\n\n\nlocal\n" + "n\n" * 6
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.output
    assert "  local          nothing leaves this Mac" in result.stdout
    cfg = load_config(path)
    assert (cfg.provider.name, cfg.provider.model) == ("ollama", MODEL)
    assert cfg.search.semantic.enabled


def test_local_without_ollama_asks_again(tmp_path: Path, downloads: Path) -> None:
    path = tmp_path / "c.yaml"
    answers = "\n\n\n\nlocal\nanthropic\nn\n" + "n\n" * 6
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.output
    assert "Ollama is not installed" in result.stderr
    assert "Choose another provider for now." in result.stdout
    assert load_config(path).provider.name == "anthropic"


def test_pull_model_reports_http_and_connection_errors(
    ollama: FakeOpenAI, monkeypatch: pytest.MonkeyPatch
) -> None:
    ollama.script(Reply(500, {"error": "boom"}))
    with pytest.raises(ProviderError, match="HTTP 500"):
        onboarding.pull_model("m", lambda _line: None)
    ollama.script(progress({"status": "pulling manifest"}, "not an object", {"status": ""}))
    with pytest.raises(ProviderError, match="stopped before it finished"):
        onboarding.pull_model("m", lambda _line: None)
    monkeypatch.setattr(config_module, "OLLAMA_BASE_URL", "http://127.0.0.1:9/v1")
    with pytest.raises(ProviderError, match="cannot reach Ollama"):
        onboarding.pull_model("m", lambda _line: None)


def test_local_state_lists_what_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding, "probe_ollama", lambda: [f"{EMBEDDER}:latest"])
    state = onboarding.local_state()
    assert state.command is None and state.missing == [MODEL]
    assert onboarding.LocalState(None, None).missing == [MODEL, EMBEDDER]
