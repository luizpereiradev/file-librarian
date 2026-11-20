"""Semantic search with embeddings (RF-15, #36), against the in-process fake server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import embeddings, pipeline
from file_librarian.cli import app
from file_librarian.config import Config, load_config, parse_config
from file_librarian.db.store import Store
from file_librarian.embeddings import Embedder, refresh
from file_librarian.models import Classification, FileStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.search import make_filters, search, semantic_search
from tests._helpers import put, write_config
from tests.fake_openai import FakeOpenAI, Reply, error
from tests.integration.test_mcp_server import call, ok

pytestmark = pytest.mark.integration

SECRET = "token = " + "ghp_" + "a" * 36
CLOSED = "http://127.0.0.1:9/v1"  # nothing listens on the discard port


def answer(category: str, name: str, description: str, tags: list[str]) -> Classification:
    return Classification(
        category=category, suggested_name=name, description=description, tags=tags, confidence=0.95
    )


ANSWERS: dict[str, Any] = {
    "scan_0042.pdf": answer(
        "Finance", "Power", "Electricity invoice for October.", ["energy", "utilities"]
    ),
    "IMG_4821.png": answer("Images", "Beach", "Sunset over the beach.", ["holiday"]),
    "lease.docx": answer("Contracts", "Lease", "Apartment lease agreement.", ["housing"]),
}


def config_for(downloads: Path, url: str, **semantic: Any) -> Config:
    raw: dict[str, Any] = {
        "inputs": [str(downloads)],
        "provider": {"name": "fake", "model": "x"},
        "search": {"semantic": {"enabled": True, "base_url": url, "model": "embed-a"} | semantic},
    }
    return parse_config(raw)


def embedder(url: str, **semantic: Any) -> Embedder:
    cfg = parse_config(
        {
            "provider": {"name": "fake", "model": "x"},
            "search": {"semantic": {"base_url": url, "model": "embed-a"} | semantic},
        }
    )
    return Embedder(cfg.search.semantic)


def run(cfg: Config, *names: str, extra: dict[str, bytes] | None = None) -> pipeline.Outcome:
    downloads = cfg.inputs[0]
    for name in names:
        put(downloads, name, name.encode())
    for name, content in (extra or {}).items():
        put(downloads, name, content)
    return pipeline.run(cfg, FakeProvider(ANSWERS), simulate=False)


def stored(cfg: Config) -> dict[int, tuple[str, str]]:
    with Store.open(cfg.database) as store:
        return store.embedding_keys()


def originals(hits: list[Any]) -> list[str]:
    return [h.original_name for h in hits]


# --- opt in -----------------------------------------------------------------------------


def test_off_by_default_nothing_is_embedded(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url, enabled=False)
    default = parse_config({"provider": {"name": "fake", "model": "x"}}).search.semantic
    assert default.enabled is False and default.default is False
    outcome = run(cfg, *ANSWERS)
    assert outcome.embedding is None
    assert fake_openai.requests == []
    assert stored(cfg) == {}
    with Store.open(cfg.database) as store:
        result = semantic_search(store, "invoice", cfg)
    assert result.semantic is False and "off" in (result.notice or "")
    assert originals(result.hits) == ["scan_0042.pdf"]  # words still work
    assert fake_openai.requests == []


def test_a_run_embeds_index_text_never_content_nor_sensitive_files(
    downloads: Path, fake_openai: FakeOpenAI
) -> None:
    cfg = parse_config(
        {
            "inputs": [str(downloads)],
            "provider": {"name": "fake", "model": "x"},
            "never_send": ["private-*"],
            "search": {"semantic": {"enabled": True, "base_url": fake_openai.url}},
        }
    )
    outcome = run(
        cfg,
        *ANSWERS,
        extra={"keys.txt": SECRET.encode(), "private-notes.txt": b"plain words"},
    )
    assert outcome.embedding is not None and outcome.embedding.error is None
    sent = "\n".join(fake_openai.embedded_texts())
    assert "Electricity invoice for October." in sent and "category: Finance" in sent
    assert "keys.txt" not in sent and "ghp_" not in sent  # sensitive: never sent
    assert "private-notes" not in sent  # never_send: withheld here too
    with Store.open(cfg.database) as store:
        rows = {r.original_name: r for r in store.all_files()}
        keys = store.embedding_keys()
        # Files that are never embedded are not reported as waiting for an embedding.
        assert semantic_search(store, "invoice", cfg).notice is None
    assert rows["keys.txt"].status is FileStatus.SENSITIVE
    assert rows["keys.txt"].id not in keys and rows["private-notes.txt"].id not in keys
    assert {rows[n].id for n in ANSWERS} <= set(keys)
    assert all(model == "embeddinggemma" for model, _ in keys.values())
    request = next(r for r in fake_openai.requests if r.path.endswith("/embeddings"))
    assert request.path == "/v1/embeddings" and "Authorization" not in request.headers


def test_a_second_run_sends_nothing_new(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    before = len(fake_openai.requests)
    outcome = run(cfg)
    assert outcome.embedding is not None and outcome.embedding.embedded == 0
    assert len(fake_openai.requests) == before


# --- ranking ----------------------------------------------------------------------------


def test_hybrid_ranking_finds_a_synonym_words_miss(
    downloads: Path, fake_openai: FakeOpenAI
) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    with Store.open(cfg.database) as store:
        assert search(store, "bill") == []
        result = semantic_search(store, "bill", cfg)
    assert result.semantic is True and result.notice is None
    assert originals(result.hits)[0] == "scan_0042.pdf"
    query = fake_openai.requests[-1].body
    assert query == {"model": "embed-a", "input": ["bill"]}


def test_word_matches_still_rank_first(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    with Store.open(cfg.database) as store:
        result = semantic_search(store, "lease", cfg, limit=1)
    assert originals(result.hits) == ["lease.docx"]


def test_filters_apply_to_the_meaning_ranking(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    with Store.open(cfg.database) as store:
        images = make_filters(categories=["images"], known_categories=store.categories())
        result = semantic_search(store, "bill", cfg, filters=images)
        pdfs = make_filters(extensions=["pdf"])
        only_pdf = semantic_search(store, "beach", cfg, filters=pdfs)
    assert originals(result.hits) == ["IMG_4821.png"]
    assert originals(only_pdf.hits) == ["scan_0042.pdf"]


def test_filters_without_words_list_as_before(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    before = len(fake_openai.requests)
    with Store.open(cfg.database) as store:
        filters = make_filters(categories=["finance"], known_categories=store.categories())
        result = semantic_search(store, "", cfg, filters=filters)
    assert originals(result.hits) == ["scan_0042.pdf"] and result.notice is None
    assert len(fake_openai.requests) == before  # no query to embed


# --- staleness --------------------------------------------------------------------------


def test_a_changed_row_is_embedded_again(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    with Store.open(cfg.database) as store:
        old = store.embedding_keys()
        store.conn.execute(
            "UPDATE files SET description = 'Water bill.' WHERE original_name = 'lease.docx'"
        )
        before = len(fake_openai.embedded_texts())
        report = refresh(store, cfg)
        new = store.embedding_keys()
    assert report.embedded == 1
    sent = fake_openai.embedded_texts()[before:]
    assert len(sent) == 1 and "Water bill." in sent[0]
    changed = [i for i in new if new[i] != old[i]]
    assert len(changed) == 1


def test_a_model_change_embeds_everything_again(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    other = config_for(downloads, fake_openai.url, model="embed-b")
    with Store.open(cfg.database) as store:
        # Vectors of another model are not compared with this model's query.
        result = semantic_search(store, "bill", other)
        assert result.semantic is False and "reindex" in (result.notice or "")
        report = refresh(store, other)
        assert {m for m, _ in store.embedding_keys().values()} == {"embed-b"}
    assert report.embedded == len(ANSWERS)


def test_a_file_that_may_no_longer_be_sent_loses_its_vector(
    downloads: Path, fake_openai: FakeOpenAI
) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    stricter = cfg.model_copy(update={"never_send": ["lease*"]})
    with Store.open(cfg.database) as store:
        report = refresh(store, stricter)
        lease = next(r for r in store.all_files() if r.original_name == "lease.docx")
        assert lease.id not in store.embedding_keys()
    assert report.removed == 1 and report.embedded == 0


def test_full_refresh_embeds_all_again(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url, batch_size=2)
    run(cfg, *ANSWERS)
    with Store.open(cfg.database) as store:
        report = refresh(store, cfg, full=True)
    assert report.embedded == len(ANSWERS)


def test_new_files_without_a_vector_yet_are_noted(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    with Store.open(cfg.database) as store:
        store.delete_embeddings([next(iter(store.embedding_keys()))])
        result = semantic_search(store, "bill", cfg)
    assert result.semantic is True
    assert "1 file(s) have no embedding yet" in (result.notice or "")


# --- failures ---------------------------------------------------------------------------


def test_an_unreachable_server_never_fails_the_run(downloads: Path, loopback_network: None) -> None:
    cfg = config_for(downloads, CLOSED)
    outcome = run(cfg, *ANSWERS)
    assert outcome.report is not None and outcome.report.errors == 0
    assert outcome.embedding is not None and "cannot reach" in (outcome.embedding.error or "")
    assert outcome.embedding.pending == len(ANSWERS)
    with Store.open(cfg.database) as store:
        assert len(store.all_files()) == len(ANSWERS)
        result = semantic_search(store, "invoice", cfg)
    assert result.semantic is False and "reindex" in (result.notice or "")
    assert originals(result.hits) == ["scan_0042.pdf"]


def test_failed_embeddings_are_retried_next_run(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    fake_openai.embeddings_reply = error(500)
    first = run(cfg, *ANSWERS)
    assert first.embedding is not None and "HTTP 500" in (first.embedding.error or "")
    assert stored(cfg) == {}
    fake_openai.embeddings_reply = None
    second = run(cfg)
    assert second.embedding is not None and second.embedding.embedded == len(ANSWERS)


def test_an_unexpected_error_is_reported_not_raised(
    downloads: Path, fake_openai: FakeOpenAI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(pipeline, "refresh", broken)
    outcome = run(config_for(downloads, fake_openai.url), *ANSWERS)
    assert outcome.embedding is not None and "disk on fire" in (outcome.embedding.error or "")


def test_query_falls_back_to_words_when_the_server_is_gone(
    downloads: Path, fake_openai: FakeOpenAI
) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    closed = config_for(downloads, CLOSED)
    with Store.open(cfg.database) as store:
        result = semantic_search(store, "invoice", closed)
    assert result.semantic is False
    assert result.notice is not None and result.notice.startswith("semantic search unavailable")
    assert originals(result.hits) == ["scan_0042.pdf"]


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (error(401), "flib key set --provider embeddings"),
        (error(404, "model not found"), "ollama pull embed-a"),
        (Reply(302, {}, headers={"Location": "http://127.0.0.1:1/"}), "redirects are not"),
        (error(400, "bad input"), "HTTP 400: bad input"),
        (Reply(body={"data": []}), "one embedding per text"),
        (Reply(body={"data": [{"index": 0, "embedding": ["x"]}]}), "malformed"),
        (Reply(body={"data": [{"index": 0, "embedding": [0.0, 0.0]}]}), "malformed"),
        (Reply(body=b"not json"), "did not answer with JSON"),
    ],
)
def test_embedder_errors_say_what_to_do(
    fake_openai: FakeOpenAI, reply: Reply, message: str
) -> None:
    fake_openai.embeddings_reply = reply
    with pytest.raises(embeddings.EmbeddingError, match=message):
        embedder(fake_openai.url).embed(["text"])


def test_different_sizes_are_refused(fake_openai: FakeOpenAI) -> None:
    fake_openai.embeddings_reply = Reply(
        body={"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}]}
    )
    with pytest.raises(embeddings.EmbeddingError, match="different sizes"):
        embedder(fake_openai.url).embed(["a", "b"])


def test_answers_are_put_back_in_input_order(fake_openai: FakeOpenAI) -> None:
    fake_openai.embeddings_reply = Reply(
        body={"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [3.0, 0]}]}
    )
    first, second = embedder(fake_openai.url).embed(["a", "b"])
    assert list(first) == [1.0, 0.0] and list(second) == [0.0, 1.0]


# --- keys -------------------------------------------------------------------------------


def test_the_embeddings_key_is_its_own(
    downloads: Path, fake_openai: FakeOpenAI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLIB_OPENAI_COMPAT_API_KEY", "provider-key-0000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-provider-key-0000")
    cfg = config_for(downloads, fake_openai.url)
    Embedder(cfg.search.semantic).embed(["a"])
    assert "Authorization" not in fake_openai.requests[-1].headers
    monkeypatch.setenv("FLIB_EMBEDDINGS_API_KEY", "embed-key-1234")
    Embedder(cfg.search.semantic).embed(["a"])
    assert fake_openai.requests[-1].headers["Authorization"] == "Bearer embed-key-1234"
    custom = config_for(downloads, fake_openai.url, api_key_env="MY_EMBED_KEY")
    monkeypatch.setenv("MY_EMBED_KEY", "custom-key-5678")
    Embedder(custom.search.semantic).embed(["a"])
    assert fake_openai.requests[-1].headers["Authorization"] == "Bearer custom-key-5678"


# --- the command line -------------------------------------------------------------------


def cli_config(tmp_path: Path, downloads: Path, url: str, semantic: str = "") -> Path:
    extra = f"search:\n  semantic:\n    enabled: true\n    base_url: {url}\n{semantic}"
    return write_config(tmp_path, downloads, extra=extra)


def test_cli_semantic_search_and_json(
    downloads: Path, tmp_path: Path, fake_openai: FakeOpenAI
) -> None:
    config = cli_config(tmp_path, downloads, fake_openai.url)
    run(load_config(config), *ANSWERS)
    runner = CliRunner()
    words = runner.invoke(app, ["search", "bill", "-c", str(config)])
    assert words.exit_code == 0 and "No matches." in words.stdout
    found = runner.invoke(app, ["search", "bill", "--semantic", "-c", str(config)])
    assert found.exit_code == 0, found.stderr
    assert found.stdout.splitlines()[1].strip().startswith("[Finance] was scan_0042.pdf")
    as_json = runner.invoke(app, ["search", "bill", "--semantic", "--json", "-c", str(config)])
    assert json.loads(as_json.stdout)[0]["original_name"] == "scan_0042.pdf"


def test_cli_default_semantic_and_words_override(
    downloads: Path, tmp_path: Path, fake_openai: FakeOpenAI
) -> None:
    config = cli_config(tmp_path, downloads, fake_openai.url, "    default: true\n")
    run(load_config(config), *ANSWERS)
    runner = CliRunner()
    found = runner.invoke(app, ["search", "bill", "-c", str(config)])
    assert "scan_0042.pdf" in found.stdout
    words = runner.invoke(app, ["search", "bill", "--words", "-c", str(config)])
    assert "No matches." in words.stdout


def test_cli_notice_when_semantic_search_is_off(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads)
    run(load_config(config), *ANSWERS)
    result = CliRunner().invoke(app, ["search", "invoice", "--semantic", "-c", str(config)])
    assert result.exit_code == 0
    assert "note: semantic search is off" in result.stderr
    assert "scan_0042.pdf" in result.stdout


def test_cli_reindex_backfills(downloads: Path, tmp_path: Path, fake_openai: FakeOpenAI) -> None:
    config = cli_config(tmp_path, downloads, fake_openai.url)
    cfg = load_config(config)
    fake_openai.embeddings_reply = error(503)
    run(cfg, *ANSWERS)
    assert stored(cfg) == {}
    runner = CliRunner()
    failed = runner.invoke(app, ["search", "--reindex", "-c", str(config)])
    assert failed.exit_code == 1 and "3 file(s) still without an embedding" in failed.stderr
    fake_openai.embeddings_reply = None
    done = runner.invoke(app, ["search", "--reindex", "-c", str(config)])
    assert done.exit_code == 0, done.stderr
    assert "Embedded 3 file(s) with embeddinggemma (this Mac)." in done.stdout
    assert len(stored(cfg)) == len(ANSWERS)
    both = runner.invoke(app, ["search", "bill", "--reindex", "--semantic", "-c", str(config)])
    assert "Embedded 0 file(s)" in both.stdout and "scan_0042.pdf" in both.stdout


def test_cli_reindex_needs_semantic_search_on(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads)
    result = CliRunner().invoke(app, ["search", "--reindex", "-c", str(config)])
    assert result.exit_code == 1 and "search.semantic.enabled: true" in result.stderr


def test_cli_run_warns_when_embedding_fails(
    downloads: Path, tmp_path: Path, loopback_network: None
) -> None:
    config = cli_config(tmp_path, downloads, CLOSED)
    for name in ANSWERS:
        put(downloads, name, name.encode())
    result = CliRunner().invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.stderr
    assert "warning: semantic search: cannot reach" in result.stderr
    assert "retried on the next run" in result.stderr


# --- MCP ----------------------------------------------------------------------------------


def mcp_search(cfg: Config, **arguments: Any) -> dict[str, Any]:
    return ok(call(cfg, "search_files", **arguments))


def test_mcp_semantic_flag(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url)
    run(cfg, *ANSWERS)
    words = mcp_search(cfg, query="bill")
    assert words == {"files": [], "semantic": False, "notice": None}
    meaning = mcp_search(cfg, query="bill", semantic=True)
    assert meaning["semantic"] is True and meaning["notice"] is None
    assert meaning["files"][0]["original_name"] == "scan_0042.pdf"
    filtered = mcp_search(cfg, query="bill", semantic=True, categories=["Images"])
    assert [f["original_name"] for f in filtered["files"]] == ["IMG_4821.png"]


def test_mcp_follows_the_default_and_falls_back(downloads: Path, fake_openai: FakeOpenAI) -> None:
    cfg = config_for(downloads, fake_openai.url, default=True)
    run(cfg, *ANSWERS)
    assert mcp_search(cfg, query="bill")["semantic"] is True
    assert mcp_search(cfg, query="bill", semantic=False)["files"] == []
    gone = config_for(downloads, CLOSED, default=True)
    result = mcp_search(gone, query="invoice")
    assert result["semantic"] is False
    assert result["notice"].startswith("semantic search unavailable")
    assert [f["original_name"] for f in result["files"]] == ["scan_0042.pdf"]
