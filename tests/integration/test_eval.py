"""``flib eval``: the whole corpus through the real pipeline, in isolation.

This is what keeps the harness from rotting in CI: it runs the full corpus
against offline providers only, never a paid one.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import cli, context
from file_librarian.config import Config, ProviderConfig, default_config_path, default_database_path
from file_librarian.evals import corpus, harness
from file_librarian.models import Classification, FileContext, Rules
from file_librarian.providers.base import Usage
from file_librarian.providers.fake import FakeProvider
from tests._helpers import put

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture(autouse=True)
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The temporary folders ``flib eval`` makes go here, so a test can see them go."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


class Oracle(FakeProvider):
    """Answers every corpus item with its first acceptable category (or review)."""

    def __init__(self, language: str, *, local: bool = True, price: float = 0.0) -> None:
        answers = {}
        for item in corpus.items():
            cats = item.gold.categories[language]  # type: ignore[index]
            answers[item.path] = Classification(
                category=cats[0] if cats else "Other",
                suggested_name="named by the oracle",
                description="d",
                confidence=0.4 if item.gold.review else 0.95,
                name_choice="original" if item.gold.keep_name else "proposed",
            )
        super().__init__(answers)
        self.local = local
        self.price = price

    def cost_usd(self, usage: Usage) -> float:
        return self.price

    def classify(self, context: FileContext, rules: Rules) -> Classification:
        self.last_usage = Usage(input_tokens=100, output_tokens=10)
        return super().classify(context, rules)


def use(monkeypatch: pytest.MonkeyPatch, provider: Any) -> list[ProviderConfig]:
    """Make ``flib eval`` use ``provider``; returns the configs it was asked for.

    The requests-per-minute spacing for remote providers is lifted, so a test
    with a provider that says it is not local does not wait.
    """
    asked: list[ProviderConfig] = []
    real_config = harness.eval_config

    def unthrottled(root: Path, settings: harness.Settings, files: int) -> Config:
        cfg = real_config(root, settings, files)
        quick = cfg.provider.model_copy(update={"requests_per_minute": 1_000_000})
        return cfg.model_copy(update={"provider": quick})

    monkeypatch.setattr(harness, "eval_config", unthrottled)

    def get(config: ProviderConfig) -> Any:
        asked.append(config)
        return provider

    monkeypatch.setattr(cli, "get_provider", get)
    return asked


def test_full_corpus_through_the_fake_provider(scratch: Path) -> None:
    result = runner.invoke(cli.app, ["eval", "--provider", "fake", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    m = data["metrics"]
    assert m["items"] == len(corpus.items())
    assert m["sensitive"] == {"expected": 10, "withheld": 10, "sent": 0}
    assert m["status"] == {"organized": len(corpus.items()) - 10, "sensitive": 10}
    # The fake provider files everything under the last category.
    assert m["category"]["per_category"]["Other"]["accuracy"] == 1.0
    assert data["meta"] | {"date": "-", "flib": "-"} == {
        "date": "-", "flib": "-", "provider": "fake", "model": "offline", "language": "en",
        "prompt": data["meta"]["prompt"], "corpus": corpus.CORPUS_VERSION,
        "seed": corpus.DEFAULT_SEED, "limit": None, "max_cost_usd": 1.0,
    }  # fmt: skip
    assert result.stderr == ""  # a local provider: no question asked
    assert list(scratch.iterdir()) == []  # the temporary folder is gone


def test_an_oracle_scores_full_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    oracle = Oracle("pt-BR")
    use(monkeypatch, oracle)
    result = runner.invoke(cli.app, ["eval", "--provider", "fake", "--language", "pt-BR"])
    assert result.exit_code == 0, result.output
    assert "Category accuracy: 100.0%" in result.stdout
    assert "9 expected there, 9 of them went" in result.stdout
    assert "Sensitive: 10/10 withheld, 0 sent." in result.stdout
    assert {c.path.name for c in oracle.calls}.isdisjoint(
        {i.path for i in corpus.items() if i.gold.sensitive}
    )
    assert "Name kept: precision 100.0%" in result.stdout
    assert "tokens" in result.stdout


def test_a_sensitive_item_that_reaches_the_provider_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Break secret detection: files with a secret inside are now sent.
    monkeypatch.setattr(context, "find_secrets", lambda text: [])
    out = tmp_path / "leak.json"
    result = runner.invoke(cli.app, ["eval", "--provider", "fake", "--out", str(out)])
    assert result.exit_code == 1
    assert "a sensitive item reached the provider" in result.stderr
    for name in ("notas-servidor.txt", "chave-acesso.txt", "db-settings.ini"):
        assert name in result.stderr
    # The result is still saved, to look into.
    assert json.loads(out.read_text(encoding="utf-8"))["metrics"]["sensitive"]["sent"] == 3


def test_the_users_config_library_and_index_are_never_touched(
    fake_home: Path, downloads: Path
) -> None:
    put(downloads, "invoice.pdf", b"%PDF-1.4 mine")
    config = default_config_path()
    config.parent.mkdir(parents=True)
    config.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: anthropic, model: x}}\n",
        encoding="utf-8",
    )
    before = {p: p.read_bytes() for p in fake_home.rglob("*") if p.is_file()}
    result = runner.invoke(cli.app, ["eval", "--provider", "fake", "--limit", "12"])
    assert result.exit_code == 0, result.output
    after = {p: p.read_bytes() for p in fake_home.rglob("*") if p.is_file()}
    assert after == before
    assert not default_database_path().exists()
    assert sorted(p.name for p in downloads.iterdir()) == ["invoice.pdf"]


def test_a_paid_provider_asks_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    paid = Oracle("en", local=False, price=0.001)
    use(monkeypatch, paid)
    result = runner.invoke(cli.app, ["eval", "--limit", "20"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "18 synthetic item(s) to send to fake" in result.stderr
    assert "estimated cost US$ 0.0180, stopping at US$ 1.00" in result.stderr
    assert "2 sensitive item(s) are never sent" in result.stderr
    assert "Send them?" in result.stderr
    assert "Nothing sent." in result.stderr
    assert paid.calls == []
    assert "Evaluated" not in result.stdout

    result = runner.invoke(cli.app, ["eval", "--limit", "20"], input="y\n")
    assert result.exit_code == 0, result.output
    assert len(paid.calls) == 18
    assert "cost US$ 0.0180" in result.stdout


def test_yes_skips_the_question_and_the_cap_still_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    paid = Oracle("en", local=False, price=0.25)
    use(monkeypatch, paid)
    result = runner.invoke(cli.app, ["eval", "--yes", "--max-cost", "0.5", "--json"])
    assert result.exit_code == 0, result.output
    assert "Send them?" not in result.stderr
    data = json.loads(result.stdout)
    # Two calls reach the cap; everything else waits, as in a real run.
    assert len(paid.calls) == 2
    assert data["metrics"]["status"]["deferred"] == len(corpus.items()) - 10 - 2
    assert data["metrics"]["cost_usd"] == 0.5


def test_the_real_anthropic_provider_is_asked_about_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the real provider (no network here), declining sends nothing at all."""
    result = runner.invoke(cli.app, ["eval", "--limit", "10"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "to send to anthropic (claude-haiku-5-5)" in result.stderr
    assert "Nothing sent." in result.stderr


def test_compare_a_new_run_with_a_saved_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "fake.json"
    result = runner.invoke(cli.app, ["eval", "--provider", "fake", "--out", str(base)])
    assert result.exit_code == 0, result.output
    assert f"Saved to {base}." in result.stdout
    use(monkeypatch, Oracle("en"))
    result = runner.invoke(cli.app, ["eval", "--provider", "fake", "--compare", str(base)])
    assert result.exit_code == 0, result.output
    assert "A: fake/offline en prompt v" in result.stdout
    assert "fixed  bill.pdf: Other -> Finance" in result.stdout


def test_compare_two_saved_runs_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert runner.invoke(cli.app, ["eval", "--provider", "fake", "--out", str(a)]).exit_code == 0
    use(monkeypatch, Oracle("en"))
    assert runner.invoke(cli.app, ["eval", "--provider", "fake", "--out", str(b)]).exit_code == 0
    asked = use(monkeypatch, None)
    result = runner.invoke(cli.app, ["eval", "--compare", str(a), "--compare", str(b)])
    assert result.exit_code == 0, result.output
    assert asked == []  # nothing ran
    assert "category accuracy" in result.stdout
    assert "Items whose category changed verdict" in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--language", "fr"], "--language must be one of en, pt-BR"),
        (["--provider", "ollama"], "--model is required for the ollama provider"),
        (["--provider", "nope", "--model", "x"], "unknown provider"),
        (["--provider", "openai-compat", "--model", "x"], "base_url is required"),
        (["--provider", "fake", "--out", "/nonexistent-flib-folder/x.json"], "cannot write"),
    ],
)
def test_bad_options_are_reported(args: list[str], message: str) -> None:
    result = runner.invoke(cli.app, ["eval", *args])
    assert result.exit_code == 1
    assert message in result.stderr


def test_compare_option_errors(tmp_path: Path) -> None:
    saved = tmp_path / "a.json"
    saved.write_text("{}", encoding="utf-8")
    result = runner.invoke(cli.app, ["eval", "--compare", str(saved)])
    assert result.exit_code == 1
    assert "not a flib eval result" in result.stderr
    result = runner.invoke(cli.app, ["eval", "--json", "--compare", str(saved)])
    assert "--compare prints a table" in result.stderr
    result = runner.invoke(cli.app, ["eval", *(["--compare", str(saved)] * 3)])
    assert "--compare takes one saved result" in result.stderr


def test_recorder_passes_prices_and_failures_through() -> None:
    class Unpriced(FakeProvider):
        prices_known = False

    inner = Unpriced()
    recorder = harness.Recorder(inner)
    assert recorder.prices_known is False
    assert recorder.cost_usd(Usage(input_tokens=5)) == 0.0
    from file_librarian.providers.base import ProviderError

    with pytest.raises(ProviderError):
        recorder.classify(FileContext(Path("/x"), "0", 0, ""), Rules(categories=(), language="en"))
    assert recorder.calls == 1


def test_a_base_url_reaches_the_provider_settings(tmp_path: Path) -> None:
    settings = harness.Settings(provider="ollama", model="small", base_url="http://127.0.0.1:9/v1")
    cfg = harness.eval_config(tmp_path, settings, 3)
    assert cfg.provider.base_url == "http://127.0.0.1:9/v1"
    assert cfg.inputs == [tmp_path / "inbox"]
    assert cfg.database.is_relative_to(tmp_path)
    assert cfg.output_root.is_relative_to(tmp_path)
