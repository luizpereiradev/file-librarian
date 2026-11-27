"""``openai-compat`` and ``ollama`` from the command line: configuration, keys, init, runs.

Every server here is the in-process fake on 127.0.0.1 (``fake_openai``); no test
reaches a real API or a real Ollama.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import config as config_module
from file_librarian import keychain, onboarding
from file_librarian.cli import app
from file_librarian.config import ConfigError, ProviderConfig, load_config, parse_config
from file_librarian.doctor import Options, build_report
from file_librarian.providers import claude_code
from tests._helpers import put, write_config
from tests.fake_openai import FakeOpenAI, completion, error

runner = CliRunner()
MENU_NO = "n\n"
"""The answer to init's last question, the menu bar item (#116)."""
REAL_PROBE = onboarding.probe_ollama  # before the autouse fixture stubs it
KEY = "sk-" + "compat-" + "w" * 24
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


def compat_provider(url: str, extra: str = "") -> str:
    return f"{{name: openai-compat, model: test-model, base_url: {json.dumps(url)}{extra}}}"


# --- configuration ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "match"),
    [
        ({"name": "openai-compat", "model": "m"}, "base_url is required"),
        ({"name": "openai-compat", "model": "m", "base_url": "ftp://x/v1"}, "http:// or https://"),
        ({"name": "openai-compat", "model": "m", "base_url": "https:///v1"}, "with a host"),
        ({"name": "openai-compat", "model": "m", "base_url": "https://u:p@x/v1"}, "password"),
        ({"name": "openai-compat", "model": "m", "base_url": "https://x/v1?k=1"}, "query"),
        ({"name": "openai-compat", "model": "m", "base_url": "https://x:99999/v1"}, "valid URL"),
        ({"name": "ollama", "model": "m", "api_key_env": "BAD-NAME"}, "environment variable"),
        ({"name": "ollama", "model": "m", "price_input_per_mtok": -1}, "greater than or equal"),
    ],
)
def test_invalid_provider_settings(provider: dict[str, object], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_config({"provider": provider})


def test_base_url_is_normalized() -> None:
    cfg = ProviderConfig(name="openai-compat", model="m", base_url=" https://x.example/v1/ ")
    assert cfg.base_url == "https://x.example/v1"


# --- check-config -------------------------------------------------------------------------


def test_check_config_for_a_remote_server_without_prices(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads, compat_provider("http://api.example.com/v1"))
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert "base_url: http://api.example.com/v1 (a remote server)" in result.stdout
    assert "api key: none" in result.stdout and "FLIB_OPENAI_COMPAT_API_KEY" in result.stdout
    assert "travel unencrypted" in result.stderr
    assert "max_cost_usd_per_run cannot stop a run" in result.stderr


def test_check_config_with_prices_a_key_and_vision(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLIB_OPENAI_COMPAT_API_KEY", KEY)
    provider = compat_provider(
        "https://api.example.com/v1", ", price_input_per_mtok: 1, price_output_per_mtok: 4"
    )
    path = write_config(tmp_path, downloads, provider, "vision: {enabled: true}\n")
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert f"api key: environment (…{KEY[-4:]})" in result.stdout
    assert KEY not in result.stdout + result.stderr
    assert "vision: off for this model" in result.stdout
    assert "cannot stop" not in result.stderr and "unencrypted" not in result.stderr


def test_check_config_for_a_local_server(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads, compat_provider("http://127.0.0.1:1234/v1"))
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert "(this Mac; no rate limit, never_send still applies)" in result.stdout
    assert result.stderr == ""


def test_check_config_finds_ollama_and_its_model(
    tmp_path: Path, downloads: Path, fake_openai: FakeOpenAI
) -> None:
    fake_openai.models = ["llama3.2:latest"]
    provider = f"{{name: ollama, model: llama3.2, base_url: {fake_openai.url}}}"
    path = write_config(tmp_path, downloads, provider)
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert f"ollama: Ollama at {fake_openai.url} has 'llama3.2'" in result.stdout
    assert "api key" not in result.stdout
    assert result.stderr == ""
    [seen] = fake_openai.requests
    assert (seen.method, seen.path, seen.body) == ("GET", "/v1/models", None)  # no file content


def test_check_config_reports_a_missing_ollama_model(
    tmp_path: Path, downloads: Path, fake_openai: FakeOpenAI
) -> None:
    path = write_config(
        tmp_path, downloads, f"{{name: ollama, model: mistral, base_url: {fake_openai.url}}}"
    )
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert "ollama pull mistral" in result.stderr


def test_check_config_reports_an_unreachable_ollama(
    tmp_path: Path, downloads: Path, fake_openai: FakeOpenAI
) -> None:
    fake_openai.models_reply = error(500)
    path = write_config(
        tmp_path, downloads, f"{{name: ollama, model: m, base_url: {fake_openai.url}}}"
    )
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert result.exit_code == 0
    assert "ollama serve" in result.stderr


def test_check_config_warns_about_a_remote_ollama(tmp_path: Path, downloads: Path) -> None:
    import file_librarian.providers.ollama as ollama

    path = write_config(
        tmp_path, downloads, "{name: ollama, model: m, base_url: 'https://gpu.example.com/v1'}"
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ollama, "check", lambda _cfg: (True, "ok"))
        result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert "file content leaves it" in result.stderr
    assert "cannot stop" in result.stderr  # remote and unpriced


def test_check_config_flags_options_the_provider_ignores(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads, "{name: fake, model: x, vision: true}")
    result = runner.invoke(app, ["check-config", "-c", str(path)])
    assert "provider.vision applies to openai-compat and ollama only" in result.stderr


# --- keys -------------------------------------------------------------------------------------


def test_key_commands_use_the_provider_account(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads, compat_provider("https://api.example.com/v1"))
    saved = runner.invoke(app, ["key", "set", "-c", str(path)], input=KEY + "\n")
    assert saved.exit_code == 0, saved.stderr
    assert "openai-compat-api-key" in saved.stdout and KEY not in saved.stdout
    assert "sk-ant-" not in saved.stderr  # no Anthropic key warning
    assert keychain.read(keychain.OPENAI_COMPAT_ACCOUNT) == KEY
    assert keychain.read() is None  # the Anthropic key is untouched
    status = runner.invoke(app, ["key", "status", "-c", str(path)])
    assert "Using the key from: keychain" in status.stdout and KEY not in status.stdout
    deleted = runner.invoke(app, ["key", "delete", "--provider", "openai-compat"])
    assert deleted.stdout.startswith("Removed")
    assert keychain.read(keychain.OPENAI_COMPAT_ACCOUNT) is None
    missing = runner.invoke(app, ["key", "status", "-c", str(path)])
    assert missing.exit_code == 1
    assert "flib key set --provider openai-compat" in missing.stdout


def test_key_set_with_an_explicit_provider() -> None:
    result = runner.invoke(app, ["key", "set", "--provider", "openai-compat"], input=KEY + "\n")
    assert result.exit_code == 0
    assert keychain.read(keychain.OPENAI_COMPAT_ACCOUNT) == KEY


def test_keyless_providers(tmp_path: Path, downloads: Path) -> None:
    refused = runner.invoke(app, ["key", "set", "--provider", "ollama"])
    assert refused.exit_code == 1 and "takes no key" in refused.stderr
    path = write_config(tmp_path, downloads, "{name: ollama, model: m}")
    status = runner.invoke(app, ["key", "status", "-c", str(path)])
    assert status.exit_code == 0 and "takes no API key" in status.stdout


def test_schedule_saves_an_environment_key_to_its_account(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLIB_OPENAI_COMPAT_API_KEY", KEY)
    path = write_config(tmp_path, downloads, compat_provider("https://api.example.com/v1"))
    result = runner.invoke(app, ["schedule", "-c", str(path), "--no-check", "--save-key"])
    assert result.exit_code == 0, result.stderr
    assert keychain.read(keychain.OPENAI_COMPAT_ACCOUNT) == KEY
    assert keychain.read() is None


def test_schedule_without_a_key_is_fine_for_openai_compat(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads, compat_provider("http://127.0.0.1:1234/v1"))
    result = runner.invoke(app, ["schedule", "-c", str(path), "--no-check"])
    assert result.exit_code == 0 and "no API key" not in result.stderr


# --- runs -------------------------------------------------------------------------------------


def test_simulate_and_run_through_the_server(
    tmp_path: Path, downloads: Path, fake_openai: FakeOpenAI
) -> None:
    put(downloads, "scan001.txt", b"electricity invoice october")
    fake_openai.script(completion(ANSWER, tokens=(2_000, 100)))
    provider = compat_provider(
        fake_openai.url, ", price_input_per_mtok: 1.5, price_output_per_mtok: 10"
    )
    path = write_config(tmp_path, downloads, provider)
    sim = runner.invoke(app, ["simulate", "-c", str(path)])
    assert sim.exit_code == 0, sim.stderr
    assert "Estimated provider cost: US$ 0.0040" in sim.stdout  # (2000*1.5 + 100*10) / 1e6
    run = runner.invoke(app, ["run", "-c", str(path)])
    assert run.exit_code == 0, run.stderr
    assert len(fake_openai.requests) == 1  # the run reused the simulation's answer
    assert any(p.name.endswith("electricity-invoice.txt") for p in downloads.rglob("*.txt"))


def test_a_rejected_key_stops_the_run(
    tmp_path: Path, downloads: Path, fake_openai: FakeOpenAI
) -> None:
    put(downloads, "a.txt", b"some text")
    fake_openai.script(error(401, "invalid api key"))
    path = write_config(tmp_path, downloads, compat_provider(fake_openai.url))
    result = runner.invoke(app, ["run", "-c", str(path)])
    assert result.exit_code == 1
    assert "rejected the credentials" in result.stderr
    assert (downloads / "a.txt").exists()


def test_never_send_holds_for_a_local_provider(
    tmp_path: Path, downloads: Path, fake_openai: FakeOpenAI
) -> None:
    put(downloads, "secret-plans.txt", b"top secret text")
    put(downloads, "id_rsa", b"not really a key")
    fake_openai.script(completion(ANSWER))
    provider = f"{{name: ollama, model: m, base_url: {fake_openai.url}}}"
    path = write_config(tmp_path, downloads, provider, "never_send: ['secret-*']\n")
    result = runner.invoke(app, ["simulate", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    sent = json.dumps([r.body for r in fake_openai.requests])
    assert "top secret text" not in sent and "not really a key" not in sent


def test_doctor_hides_the_server_address(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads, compat_provider("https://llm.internal.example/v1"))
    keychain.write(KEY, keychain.OPENAI_COMPAT_ACCOUNT)
    report = build_report(Options(config_path=path))
    assert "llm.internal.example" not in report
    assert "base_url: <https, remote>" in report
    assert "source: keychain" in report and KEY not in report


# --- init -------------------------------------------------------------------------------------


@pytest.fixture
def no_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_code, "find_claude", lambda *_a, **_k: None)


@pytest.mark.usefixtures("no_claude")
def test_init_offers_a_running_ollama_and_tries_it(
    downloads: Path, tmp_path: Path, fake_openai: FakeOpenAI, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_openai.models = ["qwen3:8b", "llama3.2:latest"]
    monkeypatch.setattr(onboarding, "probe_ollama", lambda: list(fake_openai.models))
    monkeypatch.setattr(config_module, "OLLAMA_BASE_URL", fake_openai.url)
    fake_openai.script(completion(ANSWER))
    put(downloads, "invoice.txt", b"electricity invoice october")
    path = tmp_path / "c.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "ollama         a local model through Ollama (2 pulled)" in result.stdout
    cfg = load_config(path)
    assert (cfg.provider.name, cfg.provider.model) == ("ollama", "llama3.2:latest")
    assert "Trial on 1 copies" in result.stdout, result.stderr
    posts = [r for r in fake_openai.requests if r.method == "POST"]
    assert len(posts) == 1 and posts[0].body["model"] == "llama3.2:latest"


@pytest.mark.usefixtures("no_claude")
def test_init_without_ollama_does_not_offer_it(tmp_path: Path, downloads: Path) -> None:
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml"), "--sample", "0"])
    assert "a local model through Ollama" not in result.stdout
    assert "openai-compat  any OpenAI-compatible API" in result.stdout
    assert load_config(tmp_path / "c.yaml").provider.name == "anthropic"


@pytest.mark.usefixtures("no_claude")
def test_init_with_ollama_missing_the_model_skips_the_trial(
    downloads: Path, tmp_path: Path, fake_openai: FakeOpenAI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "probe_ollama", lambda: ["qwen3:8b"])
    monkeypatch.setattr(config_module, "OLLAMA_BASE_URL", fake_openai.url)
    fake_openai.models = []
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml")])
    assert "ollama pull qwen3:8b" in result.stderr
    assert "Skipping the trial" in result.stdout


@pytest.mark.usefixtures("no_claude")
def test_init_with_an_openai_compatible_server(tmp_path: Path, downloads: Path) -> None:
    path = tmp_path / "c.yaml"
    answers = "\n\n\n\nopenai-compat\nftp://nope\nhttp://127.0.0.1:1234/v1/\nlocal-model\nn\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers + MENU_NO)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "http:// or https://" in result.stderr  # asked again
    cfg = load_config(path)
    assert cfg.provider.name == "openai-compat"
    assert (cfg.provider.base_url, cfg.provider.model) == (
        "http://127.0.0.1:1234/v1",
        "local-model",
    )
    text = path.read_text(encoding="utf-8")
    assert "price_input_per_mtok: 0" in text and "cost cap below cannot stop a run" in text
    assert "No API key found for this server" in result.stdout


@pytest.mark.usefixtures("no_claude")
def test_init_stores_an_openai_compat_key(tmp_path: Path, downloads: Path) -> None:
    path = tmp_path / "c.yaml"
    answers = f"\n\n\n\nopenai-compat\nhttps://api.example.com/v1\nm\ny\n{KEY}\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers + MENU_NO)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert keychain.read(keychain.OPENAI_COMPAT_ACCOUNT) == KEY
    assert KEY not in result.stdout


def test_init_with_an_existing_openai_compat_key(tmp_path: Path, downloads: Path) -> None:
    keychain.write(KEY, keychain.OPENAI_COMPAT_ACCOUNT)
    path = write_config(tmp_path, downloads, compat_provider("https://api.example.com/v1"))
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert "API key: found in the keychain" in result.stdout


def test_init_reports_a_keychain_failure_for_openai_compat(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise keychain.KeychainError("Keychain write failed (code 5)")

    monkeypatch.setattr(keychain, "write", broken)
    path = write_config(tmp_path, downloads, compat_provider("https://api.example.com/v1"))
    answers = f"n\ny\n{KEY}\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers + MENU_NO)
    assert "Keychain write failed" in result.stderr


def test_probe_ollama_returns_none_when_nothing_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    from file_librarian.providers import ProviderError, ollama

    def down(**_k: object) -> list[str]:
        raise ProviderError("down")

    monkeypatch.setattr(ollama, "installed_models", down)
    assert REAL_PROBE() is None
    monkeypatch.setattr(ollama, "installed_models", lambda **_k: ["m:latest"])
    assert REAL_PROBE() == ["m:latest"]
