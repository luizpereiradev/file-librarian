"""flib mcp: the MCP server, driven through a real MCP client session (RF-22).

The client talks to the server in-process over the SDK's memory streams with
the JSON-RPC framing and initialize handshake of a stdio connection; one test
also starts ``flib mcp`` as a subprocess and talks to it over stdio.
"""

from __future__ import annotations

import json
import os
import plistlib
import sqlite3
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import CallToolResult
from typer.testing import CliRunner

from file_librarian import mcp_server, pipeline, reprocess
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.lock import instance_lock
from file_librarian.mcp_server import WRITE_TOOLS, build_server
from file_librarian.models import Classification, FileStatus
from file_librarian.providers.fake import FakeProvider
from tests._helpers import (
    fake_config,
    library,
    make_old,
    mark_cloud_only,
    organize,
    write_config,
    write_xattr,
)

pytestmark = pytest.mark.integration

READ_TOOLS = {"search_files", "get_file", "list_recent", "list_review", "get_stats"}

SECRET = "token = " + "ghp_" + "a" * 36


def answer(category: str, confidence: float = 0.75) -> Classification:
    return Classification(
        category=category,
        suggested_name="x",
        description=f"A {category.lower()} document.",
        tags=["sample"],
        confidence=confidence,
    )


def session(cfg: Config, body: Callable[[Client], Awaitable[Any]]) -> Any:
    """Run ``body`` against a fresh server for ``cfg`` and return what it returns."""

    async def main() -> Any:
        async with Client(build_server(cfg), mode="legacy", raise_exceptions=True) as client:
            return await body(client)

    return anyio.run(main)


def call(cfg: Config, tool: str, **arguments: Any) -> CallToolResult:
    async def body(client: Client) -> CallToolResult:
        return await client.call_tool(tool, arguments)

    result: CallToolResult = session(cfg, body)
    return result


def ok(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    content: dict[str, Any] | None = result.structured_content
    assert content is not None
    return content


def error_text(result: CallToolResult) -> str:
    assert result.is_error
    return " ".join(getattr(part, "text", "") for part in result.content)


def tool_names(cfg: Config) -> dict[str, Any]:
    async def body(client: Client) -> dict[str, Any]:
        return {tool.name: tool for tool in (await client.list_tools()).tools}

    names: dict[str, Any] = session(cfg, body)
    return names


def runs(cfg: Config) -> list[tuple[int, str]]:
    with Store.open(cfg.database) as store:
        return [(r["id"], r["trigger"]) for r in store.conn.execute("SELECT * FROM runs")]


# --- Tools and their schemas -------------------------------------------------------------


def test_every_tool_has_a_description_and_schemas(cfg: Config) -> None:
    tools = tool_names(cfg)
    assert set(tools) == {*READ_TOOLS, *WRITE_TOOLS}
    for tool in tools.values():
        assert len(tool.description) > 40
        assert tool.input_schema["type"] == "object"
        assert tool.output_schema["type"] == "object"
        for spec in tool.input_schema["properties"].values():
            assert spec["description"]
    for name in READ_TOOLS:
        assert tools[name].annotations.read_only_hint is True
        if name != "get_stats":  # counts only, no names or descriptions
            assert "untrusted" in tools[name].description
    assert "Finance" in tools["list_recent"].input_schema["properties"]["category"]["description"]
    assert {"history", "history_truncated"} <= set(tools["get_file"].output_schema["properties"])
    unpriced = tools["get_stats"].output_schema["properties"]["runs_with_unknown_cost_this_month"]
    assert "without a recorded cost" in unpriced["description"]
    assert "required" not in tools["search_files"].input_schema  # a filter alone is enough
    filters = tools["search_files"].input_schema["properties"]
    assert {"categories", "extensions", "since", "until"} <= set(filters)
    assert "Finance" in filters["categories"]["description"]
    assert tools["search_files"].annotations.read_only_hint is True
    assert tools["undo"].annotations.read_only_hint is False
    # The configured categories are named, so the assistant does not have to guess.
    category = tools["recategorize_file"].input_schema["properties"]["category"]
    assert "Finance" in category["description"]


@pytest.mark.parametrize(
    ("semantic", "leaves"),
    [
        ({}, False),  # semantic search off
        ({"enabled": True}, False),  # Ollama on this Mac, the default
        ({"enabled": True, "base_url": "http://127.0.0.1:8080/v1"}, False),
        ({"enabled": False, "base_url": "https://embeddings.example.com/v1"}, False),
        ({"enabled": True, "base_url": "https://embeddings.example.com/v1"}, True),
    ],
)
def test_search_is_open_world_only_with_a_remote_embeddings_server(
    downloads: Path, semantic: dict[str, Any], leaves: bool
) -> None:
    cfg = fake_config(downloads, search={"semantic": semantic})
    tools = tool_names(cfg)
    search = tools["search_files"]
    assert search.annotations.open_world_hint is leaves
    assert search.annotations.read_only_hint is True
    assert ("outside this Mac" in search.description) is leaves
    assert "untrusted" in search.description
    for name, tool in tools.items():  # nothing else ever leaves the Mac
        if name != "search_files":
            assert tool.annotations.open_world_hint is False


def test_read_only_hides_the_write_tools(downloads: Path) -> None:
    cfg = fake_config(downloads, mcp={"read_only": True})
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    assert set(tool_names(cfg)) == READ_TOOLS
    target = library(downloads) / "Other" / "bill.txt"
    result = call(cfg, "recategorize_file", path=str(target), category="Finance")
    assert "Unknown tool" in error_text(result)
    assert "Unknown tool" in error_text(call(cfg, "undo"))
    assert target.exists()


def test_bad_arguments_are_rejected_by_the_schema(cfg: Config) -> None:
    assert "limit" in error_text(call(cfg, "search_files", query="x", limit=0))
    assert "give a query" in error_text(call(cfg, "search_files"))
    assert "not a date" in error_text(call(cfg, "search_files", since="yesterday"))
    assert "unknown category" in error_text(call(cfg, "search_files", categories=["Nope"]))
    assert "unknown category" in error_text(call(cfg, "list_recent", category="Nope"))
    assert "limit" in error_text(call(cfg, "list_recent", limit=0))
    assert "limit" in error_text(call(cfg, "list_review", limit=1000))


# --- Reading ---------------------------------------------------------------------------


def test_search_before_any_run_finds_nothing(cfg: Config) -> None:
    assert ok(call(cfg, "search_files", query="bill"))["files"] == []
    assert ok(call(cfg, "list_recent", category="finance")) == {"files": []}
    review = ok(call(cfg, "list_review"))
    assert (review["total"], review["files"]) == (0, [])
    stats = ok(call(cfg, "get_stats"))
    assert (stats["total_files"], stats["last_run"], stats["by_category"]) == (0, None, {})
    assert stats["by_status"]["organized"] == 0
    assert not cfg.database.exists()  # a read never creates the library


def test_list_recent_newest_first_and_by_category(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Finance"))})
    organize(
        downloads,
        cfg,
        {
            "notes.md": (b"meeting notes", answer("Other")),
            "unsure.txt": (b"who knows", answer("Finance", confidence=0.2)),
        },
    )
    with sqlite3.connect(cfg.database) as conn:  # the second run is later, whatever the clock
        conn.execute("UPDATE files SET processed_at = '2026-01-01T00:00:00+00:00' WHERE id = 1")
    recent = ok(call(cfg, "list_recent"))["files"]
    # Only organized files: the one in review is listed by list_review.
    assert [f["original_name"] for f in recent] == ["notes.md", "bill.txt"]
    assert recent[1]["path"] == str(library(downloads) / "Finance" / "bill.txt")
    assert recent[1]["description"] == "A finance document."
    finance = ok(call(cfg, "list_recent", category="finance", limit=5))["files"]
    assert [f["original_name"] for f in finance] == ["bill.txt"]
    assert [f["original_name"] for f in ok(call(cfg, "list_recent", limit=1))["files"]] == [
        "notes.md"
    ]


def test_list_review_shows_confidence(downloads: Path, cfg: Config) -> None:
    organize(
        downloads,
        cfg,
        {
            "unsure.txt": (b"who knows", answer("Finance", confidence=0.2)),
            "odd.txt": (b"odd", answer("Recipes", confidence=0.9)),  # not a configured category
            "bill.txt": (b"electricity bill", answer("Finance")),
        },
    )
    review = ok(call(cfg, "list_review"))
    assert review["review_folder"] == cfg.review_folder
    assert review["confidence_threshold"] == cfg.limits.confidence_threshold
    assert review["total"] == 2
    by_name = {f["original_name"]: f for f in review["files"]}
    assert set(by_name) == {"unsure.txt", "odd.txt"}
    assert by_name["unsure.txt"]["confidence"] == 0.2
    assert by_name["odd.txt"]["confidence"] == 0.9
    unsure, odd = by_name["unsure.txt"], by_name["odd.txt"]
    assert (unsure["review_reason"], unsure["suggested_category"]) == ("low_confidence", "Finance")
    assert unsure["review_explanation"].startswith("the AI was not sure enough (confidence 0.20)")
    assert (odd["review_reason"], odd["suggested_category"]) == ("unknown_category", "Recipes")
    detail = ok(call(cfg, "get_file", path=odd["path"]))
    assert (detail["review_reason"], detail["suggested_category"]) == (
        "unknown_category",
        "Recipes",
    )
    assert detail["review_explanation"].endswith("not configured: Recipes")
    bill = ok(call(cfg, "get_file", path=str(library(downloads) / "Finance" / "bill.txt")))
    assert (bill["review_reason"], bill["suggested_category"], bill["review_explanation"]) == (
        None,
        None,
        None,
    )
    assert by_name["unsure.txt"]["status"] == "review"
    assert by_name["unsure.txt"]["exists"] is True
    assert cfg.review_folder in by_name["unsure.txt"]["path"]
    limited = ok(call(cfg, "list_review", limit=1))
    assert (limited["total"], len(limited["files"])) == (2, 1)


def test_get_stats(downloads: Path, cfg: Config) -> None:
    secret = downloads / "notes.txt"
    secret.write_text(SECRET, encoding="utf-8")
    make_old(secret)
    organize(
        downloads,
        cfg,
        {
            "bill.txt": (b"electricity bill", answer("Finance")),
            "tax.txt": (b"tax", answer("Finance")),
            "memo.md": (b"memo", answer("Other")),
            "unsure.txt": (b"who knows", answer("Finance", confidence=0.2)),
        },
    )
    stats = ok(call(cfg, "get_stats"))
    assert stats["total_files"] == 5
    assert stats["by_status"] == {
        "organized": 3,
        "review": 1,
        "skipped": 0,
        "missing": 0,
        "failed": 0,
        "sensitive": 1,
        "duplicate": 0,
    }
    assert stats["by_category"] == {"Finance": 2, "Other": 1}
    assert stats["filed_size_bytes"] == len(b"electricity bill" + b"tax" + b"memo" + b"who knows")
    last = stats["last_run"]
    assert (last["trigger"], last["files_moved"], last["files_review"], last["errors"]) == (
        "manual",
        3,
        1,
        0,
    )
    assert last["finished_at"] is not None
    assert last["cost_usd"] == 0.0  # the fake provider costs nothing
    assert (stats["cost_this_month_usd"], stats["runs_with_unknown_cost_this_month"]) == (0.0, 0)
    with Store.open(cfg.database) as store:  # what earlier runs this month cost
        store.conn.execute("UPDATE runs SET cost_usd = 0.75 WHERE id = ?", (last["run_id"],))
        store.conn.execute(
            """INSERT INTO runs (mode, trigger, started_at, finished_at, cost_usd)
               VALUES ('run', 'schedule', '2020-01-01T00:00:00+00:00', '2020-01-01', 9.0)"""
        )
        store.conn.execute(
            """INSERT INTO runs (mode, trigger, started_at, finished_at, cost_usd)
               VALUES ('run', 'schedule', ?, ?, 0.25)""",
            (last["started_at"], last["finished_at"]),
        )
    priced = ok(call(cfg, "get_stats"))
    assert priced["cost_this_month_usd"] == pytest.approx(1.0)  # 2020 is not this month
    assert priced["last_run"]["cost_usd"] == 0.25
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE runs SET cost_usd = NULL WHERE trigger = 'schedule'")
    unknown = ok(call(cfg, "get_stats"))
    assert unknown["last_run"]["cost_usd"] is None
    assert unknown["cost_this_month_usd"] == pytest.approx(0.75)
    assert unknown["runs_with_unknown_cost_this_month"] == 1
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM runs WHERE trigger = 'schedule'")
    # A fix is its own run but not "the last run".
    memo = library(downloads) / "Other" / "memo.md"
    fixed = ok(call(cfg, "recategorize_file", path=str(memo), category="Finance"))
    after = ok(call(cfg, "get_stats"))
    assert after["last_run"]["run_id"] == last["run_id"] != fixed["run_id"]
    assert after["by_category"] == {"Finance": 3}
    assert "ghp_" not in json.dumps(after)


def test_get_file_history_newest_first(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    first = library(downloads) / "Other" / "bill.txt"
    [filed] = ok(call(cfg, "get_file", path=str(first)))["history"]
    assert filed["from_path"] == str(downloads / "bill.txt")
    assert filed["to_path"] == str(first)
    assert (filed["by"], filed["trigger"], filed["change"], filed["status"]) == (
        "run",
        "manual",
        "moved",
        "done",
    )
    assert filed["category"] is None

    renamed = ok(call(cfg, "recategorize_file", path=str(first), name="power"))
    moved = ok(call(cfg, "recategorize_file", path=renamed["destination"], category="Finance"))
    ok(call(cfg, "undo", run_id=moved["run_id"]))  # back to Other/power.txt
    detail = ok(call(cfg, "get_file", path=renamed["destination"]))
    history = detail["history"]
    assert [(h["by"], h["trigger"], h["change"], h["status"]) for h in history] == [
        ("fix", "mcp", "moved", "undone"),
        ("fix", "mcp", "renamed", "done"),
        ("run", "manual", "moved", "done"),
    ]
    assert history[0]["undone_at"] >= history[0]["at"]
    assert [h["undone_at"] for h in history[1:]] == [None, None]
    assert history[0]["category"] == "Finance"
    assert history[0]["run_id"] == moved["run_id"]
    assert detail["history_truncated"] is False

    monkeypatch.setattr(mcp_server, "MAX_HISTORY", 2)
    capped = ok(call(cfg, "get_file", path=renamed["destination"]))
    assert len(capped["history"]) == 2 and capped["history_truncated"] is True


def test_search_filters(downloads: Path, cfg: Config) -> None:
    organize(
        downloads,
        cfg,
        {
            "bill.txt": (b"electricity bill", answer("Finance")),
            "notes.md": (b"meeting notes", answer("Other")),
        },
    )
    by_category = ok(call(cfg, "search_files", categories=["finance"]))["files"]
    assert [h["original_name"] for h in by_category] == ["bill.txt"]
    by_ext = ok(call(cfg, "search_files", query="document", extensions=["md"]))["files"]
    assert [h["original_name"] for h in by_ext] == ["notes.md"]
    assert ok(call(cfg, "search_files", extensions=["txt"], until="2000-01-01"))["files"] == []


def test_search_and_get_file(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Finance"))})
    target = library(downloads) / "Finance" / "bill.txt"
    write_xattr(target, "com.apple.metadata:kMDItemWhereFroms", _where_froms())
    [hit] = ok(call(cfg, "search_files", query="finance"))["files"]
    assert hit == {
        "path": str(target),
        "exists": True,
        "original_name": "bill.txt",
        "status": "organized",
        "category": "Finance",
        "description": "A finance document.",
        "tags": ["sample"],
        "processed_at": hit["processed_at"],
        "duplicate_of": None,
        "source": "text",
        "similarity": None,
    }
    detail = ok(call(cfg, "get_file", path=str(target)))
    assert "source" not in detail and "similarity" not in detail
    assert detail["original_path"] == str(downloads / "bill.txt")
    assert detail["kind"] == "file"
    assert detail["icloud_only"] is False
    assert detail["size_bytes"] == len(b"electricity bill")
    assert len(detail["sha256"]) == 64
    assert detail["source_urls"] == ["https://example.com/bill.txt"]
    # Below the rename threshold no rename was considered, so there is no name choice.
    assert detail["name_choice"] is None and detail["name_choice_reason"] is None
    # The content itself is never part of a result.
    assert "electricity" not in json.dumps(detail)


def test_get_file_shows_the_name_choice(downloads: Path, cfg: Config) -> None:
    kept = Classification(
        category="Finance",
        suggested_name="bill",
        description="d",
        confidence=0.95,
        name_choice="original",
        name_choice_reason="The original is specific.",
    )
    organize(downloads, cfg, {"Power bill October.txt": (b"electricity bill", kept)})
    [target] = (library(downloads) / "Finance").iterdir()
    assert target.name.endswith("_power-bill-october.txt")
    detail = ok(call(cfg, "get_file", path=str(target)))
    assert detail["name_choice"] == "original"
    assert detail["name_choice_reason"] == "The original is specific."


def test_get_file_of_a_file_moved_away(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Finance"))})
    target = library(downloads) / "Finance" / "bill.txt"
    target.rename(downloads / "elsewhere.txt")
    detail = ok(call(cfg, "get_file", path=str(target)))
    assert (detail["exists"], detail["source_urls"]) == (False, [])


def test_get_file_never_reads_a_file_only_in_icloud(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Finance"))})
    target = library(downloads) / "Finance" / "bill.txt"
    with Store.open(cfg.database) as store:
        row = store.file_by_current_path(target)
    assert row is not None
    mark_cloud_only(monkeypatch, target)  # any open or hash of it fails the test
    detail = ok(call(cfg, "get_file", path=str(target)))
    assert (detail["icloud_only"], detail["exists"]) == (True, True)
    assert detail["sha256"] == row.sha256  # from the index, not recomputed


def test_get_file_needs_an_indexed_absolute_path(downloads: Path, cfg: Config) -> None:
    assert "not in flib's index" in error_text(call(cfg, "get_file", path=str(downloads / "x")))
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Finance"))})
    assert "not in flib's index" in error_text(call(cfg, "get_file", path=str(downloads / "x")))
    assert "absolute" in error_text(call(cfg, "get_file", path="Finance/bill.txt"))


def test_sensitive_files_never_show_their_text(downloads: Path, cfg: Config) -> None:
    secret = downloads / "notes.txt"
    secret.write_text(SECRET, encoding="utf-8")
    make_old(secret)
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    write_xattr(secret, "com.apple.metadata:kMDItemWhereFroms", _where_froms())
    # Even a row that somehow carries text keeps it out of every result.
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("UPDATE files SET description = ?, tags = ?", (SECRET, json.dumps([SECRET])))
    [hit] = ok(call(cfg, "search_files", query="notes"))["files"]
    assert (hit["status"], hit["description"], hit["tags"]) == ("sensitive", None, [])
    detail = ok(call(cfg, "get_file", path=str(secret)))
    assert (detail["description"], detail["tags"], detail["source_urls"]) == (None, [], [])
    assert ok(call(cfg, "get_file", path=str(secret)))["history"] == []  # it never moved
    for result in (
        call(cfg, "search_files", query="notes"),
        call(cfg, "get_file", path=str(secret)),
        call(cfg, "list_recent"),
        call(cfg, "list_review"),
        call(cfg, "get_stats"),
    ):
        assert "ghp_" not in result.model_dump_json()


# --- Writing ---------------------------------------------------------------------------


def test_files_in_a_category_layout_are_found_and_renamed_in_place(downloads: Path) -> None:
    categories = [{"name": "Finance", "layout": "{issuer}"}, {"name": "Other"}]
    cfg = fake_config(downloads, categories=categories)
    reply = answer("Finance").model_copy(update={"issuer": "Power Co"})
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", reply)})
    source = library(downloads) / "Finance" / "Power Co" / "bill.txt"
    [hit] = ok(call(cfg, "search_files", query="", categories=["Finance"]))["files"]
    assert (hit["path"], hit["category"]) == (str(source), "Finance")
    fixed = ok(call(cfg, "recategorize_file", path=str(source), name="power"))
    assert fixed["destination"] == str(source.with_name("power.txt"))


def test_recategorize_through_mcp_then_undo_the_run(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    source = library(downloads) / "Other" / "bill.txt"
    fixed = ok(call(cfg, "recategorize_file", path=str(source), category="finance", name="power"))
    target = library(downloads) / "Finance" / "power.txt"
    assert fixed["destination"] == str(target) and target.exists() and not source.exists()
    assert (fixed["category"], fixed["name"]) == (["Other", "Finance"], ["bill.txt", "power.txt"])
    assert fixed["recovered"] == 0
    # Journaled as its own run, started by MCP, and the index follows.
    assert runs(cfg)[-1] == (fixed["run_id"], "mcp")
    [hit] = ok(call(cfg, "search_files", query="power"))["files"]
    assert (hit["path"], hit["category"]) == (str(target), "Finance")

    undone = ok(call(cfg, "undo", run_id=fixed["run_id"]))
    assert undone["restored"] == [{"from_path": str(target), "to_path": str(source)}]
    assert source.exists() and not target.exists()
    with Store.open(cfg.database) as store:
        row = store.file_by_current_path(source)
    assert row is not None and (row.status, row.category) == (FileStatus.ORGANIZED, "Other")


def test_undo_one_file_and_the_last_run(downloads: Path, cfg: Config) -> None:
    organize(
        downloads,
        cfg,
        {"a.txt": (b"a", answer("Finance")), "b.txt": (b"b", answer("Finance"))},
    )
    moved_a = library(downloads) / "Finance" / "a.txt"
    undone = ok(call(cfg, "undo", path=str(moved_a)))
    assert undone["restored"] == [{"from_path": str(moved_a), "to_path": str(downloads / "a.txt")}]
    # Neither argument: the most recent run, like `flib undo`.
    undone = ok(call(cfg, "undo"))
    assert [r["to_path"] for r in undone["restored"]] == [str(downloads / "b.txt")]
    assert "nothing left to undo" in error_text(call(cfg, "undo"))


def test_undo_reports_what_it_skipped(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", answer("Finance"))})
    (downloads / "a.txt").write_bytes(b"someone else")  # the old place is taken
    undone = ok(call(cfg, "undo"))
    assert undone["restored"] == []
    [skipped] = undone["skipped"]
    assert "taken" in skipped["reason"]


def test_write_errors_change_nothing(downloads: Path, cfg: Config) -> None:
    assert "no run has moved" in error_text(call(cfg, "undo"))
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Other"))})
    source = library(downloads) / "Other" / "bill.txt"
    result = call(cfg, "recategorize_file", path=str(source), category="Nope")
    assert "unknown category" in error_text(result)
    assert "absolute" in error_text(call(cfg, "recategorize_file", path="bill.txt", name="x"))
    assert "not both" in error_text(call(cfg, "undo", run_id=1, path=str(source)))
    assert source.exists()


def test_write_tools_wait_for_nobody_and_hold_no_lock_when_idle(
    downloads: Path, cfg: Config
) -> None:
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Other"))})
    source = library(downloads) / "Other" / "bill.txt"

    async def body(client: Client) -> None:
        # A run holds the lock: the write fails at once, reads still work.
        with instance_lock(cfg.database):
            busy = await client.call_tool("recategorize_file", {"path": str(source), "name": "x"})
            assert "another flib process" in error_text(busy)
            assert ok(await client.call_tool("search_files", {"query": "bill"}))["files"]
        done = await client.call_tool("recategorize_file", {"path": str(source), "name": "x"})
        ok(done)
        # Between calls the server holds nothing: a scheduled run can start.
        with instance_lock(cfg.database):
            pass

    session(cfg, body)
    assert (library(downloads) / "Other" / "x.txt").exists()


# --- The command -------------------------------------------------------------------------


def test_flib_mcp_serves_the_loaded_config(
    downloads: Path, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[Config] = []
    monkeypatch.setattr("file_librarian.mcp_server.serve", served.append)
    path = write_config(tmp_path, downloads)
    result = CliRunner().invoke(app, ["mcp", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    [loaded] = served
    assert loaded.inputs == [downloads]


def test_flib_mcp_without_the_optional_sdk(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "file_librarian.mcp_server", None)  # import fails
    result = CliRunner().invoke(app, ["mcp", "-c", str(write_config(tmp_path, downloads))])
    assert result.exit_code == 1
    assert "./scripts/install.sh --with-mcp" in result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.stderr


def test_flib_mcp_refuses_a_bad_config(tmp_path: Path) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("provider: {name: nope}\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["mcp", "-c", str(bad)])
    assert result.exit_code == 1
    assert "error:" in result.stderr


def test_flib_mcp_over_stdio(downloads: Path, cfg: Config, tmp_path: Path) -> None:
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Finance"))})
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "file_librarian", "mcp", "-c", str(write_config(tmp_path, downloads))],
        env={**os.environ},
    )

    async def main() -> dict[str, Any]:
        async with Client(params, mode="legacy") as client:
            return ok(await client.call_tool("search_files", {"query": "bill"}))

    [hit] = anyio.run(main)["files"]
    assert hit["path"] == str(library(downloads) / "Finance" / "bill.txt")


def _where_froms() -> bytes:
    return plistlib.dumps(["https://example.com/bill.txt"], fmt=plistlib.FMT_BINARY)


def test_a_reprocess_shows_in_history_and_is_the_last_run(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")  # as if the rules changed
        targets = reprocess.select(cfg, store).targets
        planned = reprocess.plan_reprocess(
            targets, FakeProvider(default=answer("Finance")), cfg, store
        )
        done = reprocess.apply(planned, cfg, store, provider="fake", model="x")
    moved = library(downloads) / "Finance" / "bill.txt"
    newest = ok(call(cfg, "get_file", path=str(moved)))["history"][0]
    assert (newest["by"], newest["trigger"], newest["category"]) == (
        "reprocess",
        "reprocess",
        "Finance",
    )
    last = ok(call(cfg, "get_stats"))["last_run"]
    assert (last["run_id"], last["trigger"], last["files_moved"]) == (
        done.report.run_id,
        "reprocess",
        1,
    )


def test_duplicates_show_their_original(downloads: Path, cfg: Config) -> None:
    organize(
        downloads,
        cfg,
        {
            "bill.txt": (b"electricity bill", answer("Finance")),
            "bill (1).txt": (b"electricity bill", answer("Finance")),
        },
    )
    original = str(library(downloads) / "Finance" / "bill.txt")
    copy = str(library(downloads) / "_Duplicates" / "bill (1).txt")
    hits = ok(call(cfg, "search_files", query="bill"))["files"]
    assert [h["path"] for h in hits] == [original]
    both = ok(call(cfg, "search_files", query="bill", include_duplicates=True))["files"]
    assert {h["path"]: h["duplicate_of"] for h in both} == {original: None, copy: original}
    detail = ok(call(cfg, "get_file", path=copy))
    assert (detail["status"], detail["duplicate_of"]) == ("duplicate", original)
    assert ok(call(cfg, "get_stats"))["by_status"]["duplicate"] == 1
