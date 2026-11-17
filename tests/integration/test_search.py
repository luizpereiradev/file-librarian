"""Search over what a run indexed, and the per-run log."""

from __future__ import annotations

import json
import stat
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.models import Classification
from file_librarian.providers.fake import FakeProvider
from file_librarian.runlog import KEEP, logs_dir, write_log
from file_librarian.search import SearchError, fts_query, make_filters, search
from tests._helpers import fake_config, put, write_config

pytestmark = pytest.mark.integration


def answer(category: str, name: str, description: str, tags: list[str]) -> Classification:
    return Classification(
        category=category, suggested_name=name, description=description, tags=tags, confidence=0.95
    )


@pytest.fixture
def indexed(downloads: Path, tmp_path: Path) -> tuple[Config, Path]:
    config_file = write_config(tmp_path, downloads, "{name: fake, model: offline}")
    cfg = fake_config(downloads)
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
        put(downloads, name, name.encode())
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


# --- Filters (RF-14) ---------------------------------------------------------------------

KNOWN = ["Finance", "Images", "Contracts"]


@pytest.fixture
def sao_paulo(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Local time UTC-3, so a local day and a UTC day differ."""
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def set_processed(cfg: Config, when: dict[str, str]) -> None:
    with Store.open(cfg.database) as store, store.transaction():
        for original, processed_at in when.items():
            store.conn.execute(
                "UPDATE files SET processed_at = ? WHERE original_name = ?",
                (processed_at, original),
            )


def test_filters_alone_list_newest_first(indexed: tuple[Config, Path]) -> None:
    cfg, _ = indexed
    set_processed(
        cfg,
        {
            "documento(3).pdf": "2026-03-01T10:00:00+00:00",
            "IMG_4821.png": "2026-05-01T10:00:00+00:00",
            "contrato.docx": "2026-04-01T10:00:00+00:00",
        },
    )
    with Store.open(cfg.database) as store:
        every = make_filters(extensions=["pdf", ".PNG", "docx"])
        assert names(search(store, "", filters=every)) == [
            "IMG_4821.png",
            "contrato.docx",
            "documento(3).pdf",
        ]
        assert names(search(store, "", filters=every, limit=1)) == ["IMG_4821.png"]


def test_filters_combine_with_words(indexed: tuple[Config, Path]) -> None:
    cfg, _ = indexed
    with Store.open(cfg.database) as store:
        finance = make_filters(categories=["fInAnCe"], known_categories=KNOWN)
        assert finance.categories == ("Finance",)
        assert names(search(store, "energia", filters=finance)) == ["documento(3).pdf"]
        assert search(store, "praia", filters=finance) == []
        two = make_filters(categories=["images,contracts"], known_categories=KNOWN)
        assert sorted(names(search(store, "", filters=two))) == ["IMG_4821.png", "contrato.docx"]
        both = make_filters(categories=["Finance"], extensions=["png"], known_categories=KNOWN)
        assert search(store, "", filters=both) == []  # every filter must match
        assert names(search(store, "praia", filters=make_filters(extensions=[".png"]))) == [
            "IMG_4821.png"
        ]


def test_period_is_the_local_processing_day(indexed: tuple[Config, Path], sao_paulo: None) -> None:
    cfg, _ = indexed
    set_processed(
        cfg,
        {
            # 2026-03-01 01:00 UTC is still 2026-02-28 in Sao Paulo.
            "documento(3).pdf": "2026-03-01T01:00:00+00:00",
            "IMG_4821.png": "2026-03-01T12:00:00+00:00",
            "contrato.docx": "2026-03-02T02:59:59+00:00",  # 2026-03-01 23:59:59 local
        },
    )
    with Store.open(cfg.database) as store:

        def period(since: str | None, until: str | None) -> list[str]:
            return sorted(names(search(store, "", filters=make_filters(since=since, until=until))))

        assert period("2026-03-01", "2026-03-01") == ["IMG_4821.png", "contrato.docx"]
        assert period(None, "2026-02-28") == ["documento(3).pdf"]
        assert period("2026-03-02", None) == []
        assert period("2026-02-01", None) == ["IMG_4821.png", "contrato.docx", "documento(3).pdf"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"categories": ["Travel"]}, "unknown category 'Travel' (known: Contracts, Finance"),
        ({"extensions": ["p df"]}, "bad extension 'p df'"),
        ({"extensions": ["tar.gz"]}, "bad extension"),
        ({"extensions": ["."]}, "bad extension"),
        ({"since": "01/02/2026"}, "--since '01/02/2026' is not a date like"),
        ({"until": "2026-02-30"}, "--until '2026-02-30' is not a valid date"),
        ({"since": "2026-03-02", "until": "2026-03-01"}, "is after --until"),
    ],
)
def test_bad_filters_are_explained(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(SearchError) as caught:
        make_filters(known_categories=KNOWN, **kwargs)
    assert message in str(caught.value)


def test_filter_values_are_deduplicated_and_blank_ones_ignored() -> None:
    filters = make_filters(
        categories=["finance", "Finance, ,FINANCE"],
        extensions=["pdf", ".PDF"],
        known_categories=KNOWN,
    )
    assert filters.categories == ("Finance",)
    assert filters.extensions == (".pdf",)
    assert not make_filters(categories=[" , "])
    with pytest.raises(SearchError, match="known: none yet"):
        make_filters(categories=["x"])


def test_cli_search_filters(indexed: tuple[Config, Path]) -> None:
    _, config_file = indexed
    runner = CliRunner()

    def flib(*args: str) -> Any:
        return runner.invoke(app, ["search", *args, "-c", str(config_file)])

    data = json.loads(flib("--category", "finance", "--ext", ".pdf", "--json").stdout)
    assert [d["original_name"] for d in data] == ["documento(3).pdf"]
    listed = flib("--category", "Images", "--category", "contracts")
    assert listed.exit_code == 0, listed.stderr
    assert "was IMG_4821.png" in listed.stdout and "was contrato.docx" in listed.stdout
    assert flib("praia", "--ext", "pdf").stdout.strip() == "No matches."
    assert flib("contrato", "--since", "2000-01-01", "--until", "2999-12-31").exit_code == 0
    bad = flib("--category", "Nope")
    assert bad.exit_code == 1 and "unknown category 'Nope'" in bad.stderr
    nothing = flib()
    assert nothing.exit_code == 1 and "give words to search for" in nothing.stderr
    assert flib("...").stdout.strip() == "No matches."  # words without letters, as before


def test_cli_filters_are_checked_before_any_run(tmp_path: Path, downloads: Path) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8"
    )
    runner = CliRunner()
    bad = runner.invoke(app, ["search", "--since", "soon", "-c", str(config_file)])
    assert bad.exit_code == 1 and "not a date" in bad.stderr
    fine = runner.invoke(app, ["search", "--ext", "pdf", "--json", "-c", str(config_file)])
    assert fine.exit_code == 0 and json.loads(fine.stdout) == []


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
