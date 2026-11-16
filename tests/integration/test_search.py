"""Search over what a run indexed, and the per-run log."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config, parse_config
from file_librarian.db.store import Store
from file_librarian.models import Classification
from file_librarian.providers.fake import FakeProvider
from file_librarian.runlog import KEEP, logs_dir, write_log
from file_librarian.search import fts_query, search

pytestmark = pytest.mark.integration
OLD = time.time() - 3600


def answer(category: str, name: str, description: str, tags: list[str]) -> Classification:
    return Classification(
        category=category, suggested_name=name, description=description, tags=tags, confidence=0.95
    )


@pytest.fixture
def indexed(downloads: Path, tmp_path: Path) -> tuple[Config, Path]:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: offline}}\n", encoding="utf-8"
    )
    cfg = parse_config({"inputs": [str(downloads)], "provider": {"name": "fake", "model": "x"}})
    files: dict[str, Any] = {
        "documento(3).pdf": answer(
            "Finance",
            "Conta de luz",
            "Fatura de energia elétrica de outubro.",
            ["energia", "boleto"],
        ),
        "IMG_4821.png": answer("Images", "Praia", "Foto de uma praia ao pôr do sol.", ["férias"]),
        "contrato.docx": answer(
            "Contracts", "Contrato aluguel", "Contrato de aluguel do apartamento.", ["aluguel"]
        ),
    }
    for name in files:
        path = downloads / name
        path.write_bytes(name.encode())
        os.utime(path, (OLD, OLD))
    pipeline.run(cfg, FakeProvider(files), simulate=False)
    return cfg, config_file


def names(hits: list[Any]) -> list[str]:
    return [h.original_name for h in hits]


def test_search_by_description_tags_and_name(indexed: tuple[Config, Path]) -> None:
    cfg, _ = indexed
    with Store.open(cfg.database) as store:
        assert names(search(store, "energia")) == ["documento(3).pdf"]
        assert names(search(store, "ferias")) == ["IMG_4821.png"]  # accents ignored
        assert names(search(store, "IMG_4821")) == ["IMG_4821.png"]
        assert names(search(store, "contrato aluguel")) == ["contrato.docx"]  # all words
        assert names(search(store, "alug")) == ["contrato.docx"]  # prefix
        assert search(store, "contrato praia") == []


@pytest.mark.parametrize(
    "query", ['"', "AND OR NOT", "NEAR(a b)", "*", "a:b", "-x", "'; DROP TABLE"]
)
def test_query_syntax_is_inert(indexed: tuple[Config, Path], query: str) -> None:
    cfg, _ = indexed
    with Store.open(cfg.database) as store:
        search(store, query)  # never raises


def test_fts_query_quotes_every_word() -> None:
    assert fts_query('conta "luz" OR x') == '"conta"* "luz"* "OR"* "x"*'
    assert fts_query("  ...  ") is None


def test_hits_report_missing_files(indexed: tuple[Config, Path], downloads: Path) -> None:
    cfg, _ = indexed
    [moved] = (downloads / "Organized" / "Images").iterdir()
    moved.rename(downloads / "elsewhere.png")
    with Store.open(cfg.database) as store:
        [hit] = search(store, "praia")
    assert hit.exists is False


def test_cli_search_text_and_json(indexed: tuple[Config, Path]) -> None:
    _, config_file = indexed
    runner = CliRunner()
    text = runner.invoke(app, ["search", "energia", "-c", str(config_file)])
    assert text.exit_code == 0, text.stderr
    assert "was documento(3).pdf" in text.stdout
    data = json.loads(
        runner.invoke(app, ["search", "energia", "--json", "-c", str(config_file)]).stdout
    )
    assert [d["original_name"] for d in data] == ["documento(3).pdf"]
    assert data[0]["tags"] == ["energia", "boleto"]
    none = runner.invoke(app, ["search", "nothing-like-this", "-c", str(config_file)])
    assert none.stdout.strip() == "No matches."


def test_cli_search_before_any_run(tmp_path: Path, downloads: Path) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["search", "x", "--json", "-c", str(config_file)])
    assert result.exit_code == 0 and json.loads(result.stdout) == []


def test_run_log_is_private_and_pruned(tmp_path: Path) -> None:
    database = tmp_path / "data" / "library.db"
    path = write_log(database, 1, ["  moved   a.pdf  ->  Finance/a.pdf"])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "moved   a.pdf" in path.read_text(encoding="utf-8")
    for run_id in range(2, KEEP + 6):
        write_log(database, run_id, ["x"])
    logs = sorted(p.name for p in logs_dir(database).iterdir())
    assert len(logs) == KEEP
    assert logs[0] == "run-000006.log"
