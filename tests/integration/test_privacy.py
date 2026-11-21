"""Personal identifiers stay out of what the index stores (#82).

The model's answers are scrubbed where they come through (the classifier),
so the cache, the index, full-text search and the MCP tools never see them;
``flib reprocess --scrub-descriptions`` cleans what older versions stored,
and ``flib undo`` puts it back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.classifier import rules_key
from file_librarian.cli import app
from file_librarian.config import Config, ConfigError
from file_librarian.db.store import Store
from file_librarian.doctor import Options, build_report
from file_librarian.models import Classification, Rules
from file_librarian.providers.fake import FakeProvider
from file_librarian.search import search
from tests._helpers import fake_config, put, row_for, write_config

pytestmark = pytest.mark.integration

runner = CliRunner()

PHONE, EMAIL, CPF, CARD = (
    "(00) 90000-0000",
    "user@example.com",
    "000.000.000-00",
    "4111111111111111",
)
RAW = Classification(
    category="Finance",
    suggested_name="Bakery flyer 00 90000-0000",
    description=f"Bakery flyer, contact {PHONE} (First Last), {EMAIL}, CPF {CPF}, card {CARD}.",
    tags=["bakery", EMAIL],
    confidence=0.95,
    name_choice="original",
    name_choice_reason=f"The current name already has {PHONE}.",
)
CLEAN = "Bakery flyer, contact [phone] (First Last), [email], CPF [id number], card [card number]."


def _run(cfg: Config) -> None:
    put(cfg.input_paths[0], "flyer.txt", b"A flyer.")
    pipeline.run(cfg, FakeProvider({"flyer.txt": RAW}), simulate=False)


def _found(cfg: Config, text: str) -> list[str]:
    with Store.open(cfg.database) as store:
        return [h.original_name for h in search(store, text)]


def _cached(cfg: Config) -> list[str]:
    with Store.open(cfg.database) as store:
        return [answer for _, _, answer in store.cached_answers()]


def test_a_run_stores_the_answer_without_identifiers(downloads: Path) -> None:
    cfg = fake_config(downloads)
    _run(cfg)
    row = row_for(cfg, "flyer.txt")
    assert row.description == CLEAN
    assert row.tags == ["bakery"]
    assert row.name_choice_reason == "The current name already has [phone]."
    # Names of people are the prompt's job; no pattern recognises them.
    assert "First Last" in row.description
    # The cache never keeps the raw answer either.
    (cached,) = _cached(cfg)
    for value in (PHONE, EMAIL, CPF, CARD, "90000"):
        assert value not in cached
    # Nor can full-text search find it.
    assert _found(cfg, "90000") == []
    assert _found(cfg, "bakery") == ["flyer.txt"]


def test_a_proposed_name_is_scrubbed_before_it_names_a_file(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "scan001.txt", b"A flyer.")
    proposed = RAW.model_copy(update={"name_choice": "proposed", "name_choice_reason": None})
    pipeline.run(cfg, FakeProvider({"scan001.txt": proposed}), simulate=False)
    name = row_for(cfg, "scan001.txt").current_name
    assert name.endswith("_bakery-flyer.txt"), name


def test_verbatim_keeps_todays_behaviour(downloads: Path) -> None:
    cfg = fake_config(downloads, privacy={"descriptions": "verbatim"})
    _run(cfg)
    row = row_for(cfg, "flyer.txt")
    assert (row.description, row.tags) == (RAW.description, RAW.tags)
    assert PHONE in _cached(cfg)[0]


def test_the_setting_is_part_of_the_cache_key(downloads: Path) -> None:
    rules = Rules(categories=(), language="en")
    provider = FakeProvider()
    redact = rules_key(provider, rules, fake_config(downloads))
    verbatim = rules_key(
        provider, rules, fake_config(downloads, privacy={"descriptions": "verbatim"})
    )
    assert redact != verbatim


def test_the_config_rejects_an_unknown_mode(downloads: Path) -> None:
    with pytest.raises(ConfigError, match="'redact' or 'verbatim'"):
        fake_config(downloads, privacy={"descriptions": "hide"})


def _seed_raw_index(downloads: Path, tmp_path: Path) -> tuple[Config, Path]:
    """An index written before #82: verbatim answers, a cached answer and an embedding."""
    old = fake_config(downloads, privacy={"descriptions": "verbatim"})
    _run(old)
    row = row_for(old, "flyer.txt")
    with Store.open(old.database) as store:
        store.save_embeddings([(row.id, "m", 2, "hash", b"\x00" * 8)])
    return fake_config(downloads), write_config(tmp_path, downloads)


def test_scrub_descriptions_cleans_the_index_and_undo_restores_it(
    downloads: Path, tmp_path: Path
) -> None:
    cfg, config_file = _seed_raw_index(downloads, tmp_path)
    assert _found(cfg, "90000") == ["flyer.txt"]

    result = runner.invoke(app, ["reprocess", "--scrub-descriptions", "-y", "-c", str(config_file)])
    assert result.exit_code == 0, result.output
    assert (
        "scrub   " in result.output and "(description, tags, name choice reason)" in result.output
    )
    assert "1 cached answer(s) scrubbed." in result.output
    assert "1 file(s), 3 value(s) changed. `flib undo` reverts it." in result.output
    assert "1 embedding(s) removed" in result.output

    row = row_for(cfg, "flyer.txt")
    assert (row.description, row.tags) == (CLEAN, ["bakery"])
    assert _found(cfg, "90000") == []
    with Store.open(cfg.database) as store:
        assert store.embedding_keys() == {}
    cached = json.loads(_cached(cfg)[0])
    assert (cached["description"], cached["tags"]) == (CLEAN, ["bakery"])
    assert cached["suggested_name"] == "Bakery flyer"

    # Running it again finds nothing more.
    again = runner.invoke(app, ["reprocess", "--scrub-descriptions", "-y", "-c", str(config_file)])
    assert "Nothing to scrub" in again.output

    undone = runner.invoke(app, ["undo", "-c", str(config_file)])
    assert undone.exit_code == 0, undone.output
    assert "3 index change(s) reverted" in undone.output
    row = row_for(cfg, "flyer.txt")
    assert (row.description, row.tags) == (RAW.description, RAW.tags)
    assert _found(cfg, "90000") == ["flyer.txt"]


def test_scrub_descriptions_simulates_asks_and_refuses_other_options(
    downloads: Path, tmp_path: Path
) -> None:
    cfg, config_file = _seed_raw_index(downloads, tmp_path)
    args = ["reprocess", "--scrub-descriptions", "-c", str(config_file)]

    simulated = runner.invoke(app, [*args, "--simulate"])
    assert "Simulation: 1 file(s) to scrub; nothing changed." in simulated.output
    declined = runner.invoke(app, args, input="n\n")
    assert "Scrub 1 file(s) in the index?" in declined.output
    assert "Nothing changed." in declined.output
    assert row_for(cfg, "flyer.txt").description == RAW.description

    refused = runner.invoke(app, [*args, "--category", "Finance"])
    assert refused.exit_code != 0
    assert "takes the whole index" in refused.stderr


def test_scrub_descriptions_also_cleans_journaled_records(downloads: Path, tmp_path: Path) -> None:
    cfg, config_file = _seed_raw_index(downloads, tmp_path)
    record = json.dumps({"description": RAW.description, "tags": RAW.tags, "other": 1})
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE operations SET pending_record = ?", (record,))
        store.conn.execute("INSERT INTO classification_cache VALUES ('s', 'k', 'not json', '')")
        store.conn.commit()
    result = runner.invoke(app, ["reprocess", "--scrub-descriptions", "-y", "-c", str(config_file)])
    assert result.exit_code == 0, result.output
    with Store.open(cfg.database) as store:
        ((kept,),) = store.conn.execute("SELECT pending_record FROM operations").fetchall()
    assert json.loads(kept) == {"description": CLEAN, "tags": ["bakery"], "other": 1}


def test_the_doctor_report_never_includes_descriptions(downloads: Path, tmp_path: Path) -> None:
    _, config_file = _seed_raw_index(downloads, tmp_path)
    report = build_report(Options(config_path=config_file, run_logs=10), "text")
    assert "schema version:" in report
    for value in ("Bakery", "bakery", "90000", "First Last", EMAIL):
        assert value not in report
