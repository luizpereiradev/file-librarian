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

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config, parse_config
from file_librarian.db.store import Store
from file_librarian.lock import instance_lock
from file_librarian.mcp_server import WRITE_TOOLS, build_server
from file_librarian.models import Classification, FileStatus
from file_librarian.providers.fake import FakeProvider
from tests._helpers import OLD, write_xattr

pytestmark = pytest.mark.integration

SECRET = "token = " + "ghp_" + "a" * 36


def answer(category: str, confidence: float = 0.75) -> Classification:
    return Classification(
        category=category,
        suggested_name="x",
        description=f"A {category.lower()} document.",
        tags=["sample"],
        confidence=confidence,
    )


def make_cfg(downloads: Path, **extra: Any) -> Config:
    return parse_config(
        {"inputs": [str(downloads)], "provider": {"name": "fake", "model": "x"}} | extra
    )


@pytest.fixture
def cfg(downloads: Path) -> Config:
    return make_cfg(downloads)


def organize(downloads: Path, cfg: Config, files: dict[str, tuple[bytes, Classification]]) -> None:
    for name, (content, _) in files.items():
        (downloads / name).write_bytes(content)
        os.utime(downloads / name, (OLD, OLD))
    provider = FakeProvider({name: a for name, (_, a) in files.items()})
    pipeline.run(cfg, provider, simulate=False)


def lib(downloads: Path) -> Path:
    return downloads / "Organized"


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
    assert set(tools) == {"search_files", "get_file", *WRITE_TOOLS}
    for tool in tools.values():
        assert len(tool.description) > 40
        assert tool.input_schema["type"] == "object"
        assert tool.output_schema["type"] == "object"
        for spec in tool.input_schema["properties"].values():
            assert spec["description"]
    assert tools["search_files"].input_schema["required"] == ["query"]
    assert tools["search_files"].annotations.read_only_hint is True
    assert tools["undo"].annotations.read_only_hint is False
    # The configured categories are named, so the assistant does not have to guess.
    category = tools["recategorize_file"].input_schema["properties"]["category"]
    assert "Finance" in category["description"]


def test_read_only_hides_the_write_tools(downloads: Path) -> None:
    cfg = make_cfg(downloads, mcp={"read_only": True})
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    assert set(tool_names(cfg)) == {"search_files", "get_file"}
    target = lib(downloads) / "Other" / "bill.txt"
    result = call(cfg, "recategorize_file", path=str(target), category="Finance")
    assert "Unknown tool" in error_text(result)
    assert "Unknown tool" in error_text(call(cfg, "undo"))
    assert target.exists()


def test_bad_arguments_are_rejected_by_the_schema(cfg: Config) -> None:
    assert "limit" in error_text(call(cfg, "search_files", query="x", limit=0))
    assert "query" in error_text(call(cfg, "search_files"))


# --- Reading ---------------------------------------------------------------------------


def test_search_before_any_run_finds_nothing(cfg: Config) -> None:
    assert ok(call(cfg, "search_files", query="bill")) == {"files": []}
    assert not cfg.database.exists()  # a read never creates the library


def test_search_and_get_file(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Finance"))})
    target = lib(downloads) / "Finance" / "bill.txt"
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
    }
    detail = ok(call(cfg, "get_file", path=str(target)))
    assert detail["original_path"] == str(downloads / "bill.txt")
    assert detail["kind"] == "file"
    assert detail["size_bytes"] == len(b"electricity bill")
    assert len(detail["sha256"]) == 64
    assert detail["source_urls"] == ["https://example.com/bill.txt"]
    # The content itself is never part of a result.
    assert "electricity" not in json.dumps(detail)


def test_get_file_of_a_file_moved_away(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Finance"))})
    target = lib(downloads) / "Finance" / "bill.txt"
    target.rename(downloads / "elsewhere.txt")
    detail = ok(call(cfg, "get_file", path=str(target)))
    assert (detail["exists"], detail["source_urls"]) == (False, [])


def test_get_file_needs_an_indexed_absolute_path(downloads: Path, cfg: Config) -> None:
    assert "not in flib's index" in error_text(call(cfg, "get_file", path=str(downloads / "x")))
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Finance"))})
    assert "not in flib's index" in error_text(call(cfg, "get_file", path=str(downloads / "x")))
    assert "absolute" in error_text(call(cfg, "get_file", path="Finance/bill.txt"))


def test_sensitive_files_never_show_their_text(downloads: Path, cfg: Config) -> None:
    secret = downloads / "notes.txt"
    secret.write_text(SECRET, encoding="utf-8")
    os.utime(secret, (OLD, OLD))
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    write_xattr(secret, "com.apple.metadata:kMDItemWhereFroms", _where_froms())
    # Even a row that somehow carries text keeps it out of every result.
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("UPDATE files SET description = ?, tags = ?", (SECRET, json.dumps([SECRET])))
    [hit] = ok(call(cfg, "search_files", query="notes"))["files"]
    assert (hit["status"], hit["description"], hit["tags"]) == ("sensitive", None, [])
    detail = ok(call(cfg, "get_file", path=str(secret)))
    assert (detail["description"], detail["tags"], detail["source_urls"]) == (None, [], [])
    for result in (
        call(cfg, "search_files", query="notes"),
        call(cfg, "get_file", path=str(secret)),
    ):
        assert "ghp_" not in result.model_dump_json()


# --- Writing ---------------------------------------------------------------------------


def test_recategorize_through_mcp_then_undo_the_run(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"bill.txt": (b"electricity bill", answer("Other"))})
    source = lib(downloads) / "Other" / "bill.txt"
    fixed = ok(call(cfg, "recategorize_file", path=str(source), category="finance", name="power"))
    target = lib(downloads) / "Finance" / "power.txt"
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
    moved_a = lib(downloads) / "Finance" / "a.txt"
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
    source = lib(downloads) / "Other" / "bill.txt"
    result = call(cfg, "recategorize_file", path=str(source), category="Nope")
    assert "unknown category" in error_text(result)
    assert "absolute" in error_text(call(cfg, "recategorize_file", path="bill.txt", name="x"))
    assert "not both" in error_text(call(cfg, "undo", run_id=1, path=str(source)))
    assert source.exists()


def test_write_tools_wait_for_nobody_and_hold_no_lock_when_idle(
    downloads: Path, cfg: Config
) -> None:
    organize(downloads, cfg, {"bill.txt": (b"bill", answer("Other"))})
    source = lib(downloads) / "Other" / "bill.txt"

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
    assert (lib(downloads) / "Other" / "x.txt").exists()


# --- The command -------------------------------------------------------------------------


def test_flib_mcp_serves_the_loaded_config(
    downloads: Path, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[Config] = []
    monkeypatch.setattr("file_librarian.mcp_server.serve", served.append)
    path = _config_file(tmp_path, downloads)
    result = CliRunner().invoke(app, ["mcp", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    [loaded] = served
    assert loaded.inputs == [downloads]


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
        args=["-m", "file_librarian", "mcp", "-c", str(_config_file(tmp_path, downloads))],
        env={**os.environ},
    )

    async def main() -> dict[str, Any]:
        async with Client(params, mode="legacy") as client:
            return ok(await client.call_tool("search_files", {"query": "bill"}))

    [hit] = anyio.run(main)["files"]
    assert hit["path"] == str(lib(downloads) / "Finance" / "bill.txt")


def _config_file(folder: Path, downloads: Path) -> Path:
    path = folder / "config.yaml"
    path.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n",
        encoding="utf-8",
    )
    return path


def _where_froms() -> bytes:
    return plistlib.dumps(["https://example.com/bill.txt"], fmt=plistlib.FMT_BINARY)
