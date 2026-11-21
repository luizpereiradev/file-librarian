"""Structured fields (#88): stored with the answer, searched, summed, filled in later.

Organisations are fictitious; amounts and dates are made up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from typer.testing import CliRunner

from file_librarian import cli, pipeline
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.mcp_server import build_server
from file_librarian.models import Classification
from file_librarian.providers.fake import FakeProvider
from tests._helpers import answer, library, organize, put, row_for, rows, write_config

pytestmark = pytest.mark.integration

runner = CliRunner()


def doc(
    issuer: str | None,
    amount: str | None = None,
    currency: str | None = None,
    *,
    kind: str = "bill",
    period: str | None = None,
    due: str | None = None,
    dated: str | None = None,
    category: str = "Finance",
) -> Classification:
    return answer(
        category,
        description=f"A {kind} from {issuer or 'someone'}.",
        kind=kind,
        issuer=issuer,
        amount=amount,
        currency=currency,
        period=period,
        due_date=due,
        document_date=dated,
    )


FILES: dict[str, tuple[bytes, Classification | str]] = {
    "power-sep.txt": (
        b"power, september",
        doc("Power Co", "103.91", "USD", period="2026-08", due="2026-09-20", dated="2026-09-03"),
    ),
    "power-oct.txt": (
        b"power, october",
        doc("POWER CO, Inc.", "217.47", "USD", period="2026-09", due="2026-10-20"),
    ),
    "power-old.txt": (b"power, last year", doc("Power Co", "50.00", "USD", period="2025-12")),
    "agua.txt": (
        b"water, september",
        doc("Água Clara", "176.30", "BRL", period="2026-09", due="2026-10-20"),
    ),
    # No period: the month of its document date counts.
    "recibo.txt": (
        b"water receipt",
        doc("agua clara ltda", "80.06", "BRL", kind="receipt", dated="2026-10-14"),
    ),
    "contract.txt": (b"contract", doc("Acme", kind="contract", category="Contracts")),
}


@pytest.fixture
def filed(downloads: Path, cfg: Config, tmp_path: Path) -> Path:
    """The files above, organized; returns a config file for ``flib -c``."""
    organize(downloads, cfg, FILES)
    return write_config(tmp_path, downloads)


def found(path: Path, *args: str) -> list[str]:
    result = runner.invoke(cli.app, ["search", "-c", str(path), "--json", *args])
    assert result.exit_code == 0, result.output
    return sorted(hit["original_name"] for hit in json.loads(result.stdout))


def summed(path: Path, *args: str) -> dict[str, Any]:
    result = runner.invoke(cli.app, ["search", "-c", str(path), "--sum", "--json", *args])
    assert result.exit_code == 0, result.output
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def test_fields_are_indexed_from_the_answer(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, FILES)
    row = row_for(cfg, "power-sep.txt")
    assert (row.kind, row.issuer, row.amount_minor, row.currency) == (
        "bill",
        "Power Co",
        10391,
        "USD",
    )
    assert (row.due_date, row.period, row.document_date) == (
        "2026-09-20",
        "2026-08",
        "2026-09-03",
    )
    contract = row_for(cfg, "contract.txt")
    assert (contract.kind, contract.amount_minor, contract.currency) == ("contract", None, None)


def test_search_by_each_field(filed: Path) -> None:
    power = ["power-oct.txt", "power-old.txt", "power-sep.txt"]
    assert found(filed, "--issuer", "power co") == power
    assert found(filed, "--issuer", "Power Co Ltd.") == power  # legal forms do not matter
    assert found(filed, "--issuer", "Agua Clara") == ["agua.txt", "recibo.txt"]  # nor accents
    assert found(filed, "--issuer", "Power Co", "--period", "2026") == [
        "power-oct.txt",
        "power-sep.txt",
    ]
    assert found(filed, "--issuer", "Power") == []  # another organisation: never a prefix
    assert found(filed, "--period", "2026-10") == ["recibo.txt"]  # from its document date
    assert found(filed, "--period", "2025") == ["power-old.txt"]
    assert found(filed, "--kind", "receipt") == ["recibo.txt"]
    assert found(filed, "--kind", "receipt,CONTRACT") == ["contract.txt", "recibo.txt"]
    assert found(filed, "--min-amount", "100", "--currency", "usd") == [
        "power-oct.txt",
        "power-sep.txt",
    ]
    # Bounds are inclusive and compare exactly.
    assert found(filed, "--max-amount", "103.91", "--currency", "USD") == [
        "power-old.txt",
        "power-sep.txt",
    ]
    assert found(filed, "--min-amount", "1.000,00") == []
    assert found(filed, "--due-before", "2026-10-01") == ["power-sep.txt"]
    assert found(filed, "--due-after", "2026-09-20") == ["agua.txt", "power-oct.txt"]
    # With words and other filters.
    assert found(filed, "receipt", "--currency", "BRL") == ["recibo.txt"]
    assert found(filed, "--category", "contracts", "--issuer", "acme") == ["contract.txt"]


def test_results_show_the_fields(filed: Path) -> None:
    result = runner.invoke(cli.app, ["search", "-c", str(filed), "--json", "--period", "2026-08"])
    [hit] = json.loads(result.stdout)
    assert {k: hit[k] for k in ("kind", "issuer", "amount", "currency", "due_date", "period")} == {
        "kind": "bill",
        "issuer": "Power Co",
        "amount": "103.91",
        "currency": "USD",
        "due_date": "2026-09-20",
        "period": "2026-08",
    }
    result = runner.invoke(cli.app, ["search", "-c", str(filed), "--period", "2026-08"])
    assert "  bill · Power Co · USD 103.91 · due 2026-09-20 · for 2026-08\n" in result.stdout
    result = runner.invoke(cli.app, ["search", "-c", str(filed), "--kind", "receipt"])
    assert "receipt · agua clara ltda · BRL 80.06 · dated 2026-10-14" in result.stdout


def test_what_did_i_pay_to_an_issuer_in_a_year(filed: Path) -> None:
    data = summed(filed, "--issuer", "Power Co", "--period", "2026")
    assert data == {
        "by": None,
        "groups": [],
        "totals": [{"group": None, "currency": "USD", "total": "321.38", "files": 2}],
        "files_without_amount": 0,
    }
    result = runner.invoke(
        cli.app, ["search", "-c", str(filed), "--issuer", "power co", "--period", "2026", "--sum"]
    )
    assert result.exit_code == 0, result.output
    assert result.stdout == "Total: USD 321.38 (2 file(s))\n"


def test_sums_never_mix_currencies(filed: Path) -> None:
    data = summed(filed, "--period", "2026", "--by", "issuer")
    assert data["groups"] == [
        {"group": "POWER CO, Inc.", "currency": "USD", "total": "321.38", "files": 2},
        {"group": "agua clara ltda", "currency": "BRL", "total": "256.36", "files": 2},
    ]
    assert data["totals"] == [
        {"group": None, "currency": "USD", "total": "321.38", "files": 2},
        {"group": None, "currency": "BRL", "total": "256.36", "files": 2},
    ]
    by_month = summed(filed, "--issuer", "agua clara", "--by", "month")["groups"]
    assert [(g["group"], g["total"]) for g in by_month] == [
        ("2026-09", "176.30"),
        ("2026-10", "80.06"),
    ]
    by_year = summed(filed, "--currency", "USD", "--by", "year")["groups"]
    assert [(g["group"], g["total"], g["files"]) for g in by_year] == [
        ("2026", "321.38", 2),
        ("2025", "50.00", 1),
    ]
    by_kind = summed(filed, "--currency", "BRL", "--by", "kind")["groups"]
    assert [(g["group"], g["total"]) for g in by_kind] == [("bill", "176.30"), ("receipt", "80.06")]
    by_category = summed(filed, "--kind", "bill,contract", "--by", "category")
    assert [(g["group"], g["currency"]) for g in by_category["groups"]] == [
        ("Finance", "USD"),
        ("Finance", "BRL"),
    ]
    assert by_category["files_without_amount"] == 1  # the contract states none


def test_sum_output_and_words(filed: Path) -> None:
    args = ["search", "-c", str(filed), "--sum"]
    result = runner.invoke(cli.app, [*args, "--period", "2026", "--by", "issuer"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Totals by issuer:",
        "  POWER CO, Inc.           USD 321.38  (2 file(s))",
        "  agua clara ltda          BRL 256.36  (2 file(s))",
        "Total: USD 321.38 (2 file(s)); BRL 256.36 (2 file(s))",
    ]
    result = runner.invoke(cli.app, [*args, "--category", "Contracts"])
    assert "No amounts among the matches." in result.stdout
    assert "1 matching file(s) state no amount; not counted." in result.stdout
    assert summed(filed, "receipt")["totals"][0]["total"] == "80.06"  # words alone
    result = runner.invoke(cli.app, [*args, "receipt", "--semantic"])
    assert "--sum matches the words only" in result.stderr


def test_duplicates_are_never_counted(downloads: Path, cfg: Config, filed: Path) -> None:
    put(downloads, "power-sep copy.txt", b"power, september")
    pipeline.run(cfg, FakeProvider({}), simulate=False)
    assert rows(cfg, "SELECT COUNT(*) FROM files WHERE status = 'duplicate'")[0][0] == 1
    assert summed(filed, "--issuer", "power co", "--period", "2026")["totals"][0]["files"] == 2


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--by", "issuer", "--period", "2026"], "--by goes with --sum"),
        (["--sum", "--duplicates", "--period", "2026"], "never counts duplicates"),
        (["--sum", "--by", "week", "--period", "2026"], "--by must be one of"),
        (["--period", "2026-13"], "is not a year or month"),
        (["--period", "Sept"], "is not a year or month"),
        (["--kind", "spaceship"], "unknown kind"),
        (["--min-amount", "10", "--max-amount", "5"], "is more than"),
        (["--min-amount", "lots"], "is not an amount"),
        (["--currency", "XX"], "is not a currency code"),
        (["--issuer", "!!!"], "names no organisation"),
        (["--due-before", "tomorrow"], "is not a date"),
    ],
)
def test_bad_filters_are_refused(filed: Path, args: list[str], message: str) -> None:
    result = runner.invoke(cli.app, ["search", "-c", str(filed), *args])
    assert result.exit_code != 0
    assert message in result.stderr


def test_filters_are_checked_before_the_first_run(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads)
    result = runner.invoke(cli.app, ["search", "-c", str(path), "--period", "2026", "--sum"])
    assert (result.exit_code, result.stdout) == (0, "No amounts among the matches.\n")
    result = runner.invoke(cli.app, ["search", "-c", str(path), "--sum", "--kind", "nope"])
    assert "unknown kind" in result.stderr


def test_inspect_shows_the_fields(filed: Path, downloads: Path) -> None:
    target = library(downloads) / "Finance" / "power-sep.txt"
    result = runner.invoke(cli.app, ["inspect", "-c", str(filed), str(target)])
    assert result.exit_code == 0, result.output
    for line in ("kind:   bill", "issuer: Power Co", "amount: 103.91 USD", "due date:2026-09-20"):
        assert line in result.stdout
    result = runner.invoke(cli.app, ["inspect", "--json", "-c", str(filed), str(target)])
    fields = json.loads(result.stdout)[0]["index"]["fields"]
    assert fields["amount"] == "103.91" and fields["period"] == "2026-08"


# --- MCP ----------------------------------------------------------------------------------


def call(cfg: Config, tool: str, **arguments: Any) -> dict[str, Any]:
    async def main() -> dict[str, Any]:
        async with Client(build_server(cfg), mode="legacy", raise_exceptions=True) as client:
            result = await client.call_tool(tool, arguments)
            assert not result.is_error, result.content
            data: dict[str, Any] = result.structured_content or {}
            return data

    out: dict[str, Any] = anyio.run(main)
    return out


def test_mcp_filters_fields_and_totals(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, FILES)
    hits = call(cfg, "search_files", issuer="power co", period="2026")["files"]
    assert sorted(h["original_name"] for h in hits) == ["power-oct.txt", "power-sep.txt"]
    [hit] = call(cfg, "search_files", kinds=["receipt"], currency="brl")["files"]
    assert hit["fields"] == {
        "kind": "receipt",
        "issuer": "agua clara ltda",
        "amount": "80.06",
        "currency": "BRL",
        "document_date": "2026-10-14",
        "due_date": None,
        "period": None,
    }
    detail = call(cfg, "get_file", path=hit["path"])
    assert detail["fields"]["amount"] == "80.06"
    recent = call(cfg, "list_recent", category="Contracts")["files"]
    assert recent[0]["fields"]["kind"] == "contract"
    totals = call(cfg, "summarize_amounts", issuer="Power Co", period="2026")
    assert totals == {
        "by": None,
        "groups": [],
        "totals": [{"group": None, "currency": "USD", "total": "321.38", "files": 2}],
        "files_without_amount": 0,
    }
    grouped = call(cfg, "summarize_amounts", period="2026-09", by="month")
    assert [(g["group"], g["currency"], g["total"]) for g in grouped["groups"]] == [
        ("2026-09", "USD", "217.47"),
        ("2026-09", "BRL", "176.30"),
    ]


def test_mcp_never_counts_or_shows_a_sensitive_file(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, FILES)
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE files SET status = 'sensitive' WHERE original_name = 'agua.txt'")
    totals = call(cfg, "summarize_amounts", currency="BRL")["totals"]
    assert totals == [{"group": None, "currency": "BRL", "total": "80.06", "files": 1}]
    hits = call(cfg, "search_files", issuer="agua clara")["files"]
    sensitive = next(h for h in hits if h["original_name"] == "agua.txt")
    assert sensitive["fields"] is None and sensitive["description"] is None


def test_mcp_refuses_bad_filters_and_works_before_the_first_run(cfg: Config) -> None:
    assert call(cfg, "summarize_amounts", issuer="Power Co")["totals"] == []

    async def main() -> Any:
        async with Client(build_server(cfg), mode="legacy", raise_exceptions=True) as client:
            bad = await client.call_tool("summarize_amounts", {"period": "next year"})
            empty = await client.call_tool("summarize_amounts", {})
            return bad, empty

    bad, empty = anyio.run(main)
    assert bad.is_error and "not a year or month" in bad.content[0].text
    assert empty.is_error and "give a query" in empty.content[0].text


# --- flib reprocess --fill-fields ----------------------------------------------------------


PLAIN: dict[str, tuple[bytes, Classification | str]] = {
    "power-sep.txt": (b"power, september", answer("Finance", description="A power bill.")),
    # Filed with an issuer already: it is kept, never replaced.
    "agua.txt": (b"water", answer("Finance", description="A water bill.", issuer="Old Name")),
    "notes.txt": (b"notes", answer("Other", description="Notes.")),
}


def forget_answers(cfg: Config) -> None:
    """As after an upgrade: answers cached under the old prompt are never reused."""
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")


FILLED = {
    "power-sep.txt": FILES["power-sep.txt"][1],
    "agua.txt": FILES["agua.txt"][1],
    "notes.txt": doc(None, kind="other", category="Other"),
}


def test_fill_fields_asks_only_for_what_is_missing_and_undo_reverts(
    downloads: Path, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, PLAIN)
    forget_answers(cfg)
    before = {r.original_name: r for r in Store.open(cfg.database).all_files()}
    provider = FakeProvider(FILLED)  # type: ignore[arg-type]
    monkeypatch.setattr(cli, "get_provider", lambda _: provider)
    path = write_config(tmp_path, downloads)
    args = ["reprocess", "-c", str(path), "--fill-fields"]

    result = runner.invoke(cli.app, args, input="n\n")
    assert result.exit_code == 0, result.output
    assert "3 file(s) without fields: 0 answered by the cache, 3 to send to fake" in result.stdout
    assert "Nothing sent, nothing changed." in result.stdout and provider.calls == []

    result = runner.invoke(cli.app, [*args, "--simulate"], input="y\n")
    assert result.exit_code == 0, result.output
    assert (
        "fill    Finance/power-sep.txt  (kind, issuer, amount, currency, document date, "
        "due date, period)"
    ) in result.stdout
    assert "Simulation: nothing changed." in result.stdout
    assert row_for(cfg, "power-sep.txt").kind is None

    result = runner.invoke(cli.app, [*args, "--yes"])
    assert result.exit_code == 0, result.output
    assert "3 answered by the cache, 0 to send" in result.stdout  # the simulation paid
    assert "Reprocess run" in result.stdout and "`flib undo` reverts it." in result.stdout
    power = row_for(cfg, "power-sep.txt")
    assert (power.kind, power.amount_minor, power.currency, power.period) == (
        "bill",
        10391,
        "USD",
        "2026-08",
    )
    agua = row_for(cfg, "agua.txt")
    assert (agua.issuer, agua.amount_minor) == ("Old Name", 17630)  # what it had stays
    for name, row in before.items():  # category, name, place and description untouched
        after = row_for(cfg, name)
        assert (after.category, after.current_path, after.description) == (
            row.category,
            row.current_path,
            row.description,
        )
    assert rows(cfg, "SELECT COUNT(*) FROM runs WHERE trigger = 'reprocess'")[0][0] == 1

    result = runner.invoke(cli.app, [*args, "--yes"])
    assert "Every file in the library already has its fields." in result.stdout

    result = runner.invoke(cli.app, ["undo", "-c", str(path)])
    assert result.exit_code == 0, result.output
    power = row_for(cfg, "power-sep.txt")
    assert (power.kind, power.amount_minor, power.issuer) == (None, None, None)
    assert row_for(cfg, "agua.txt").issuer == "Old Name"


def test_fill_fields_keeps_files_it_cannot_fill(
    downloads: Path, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", "Finance"), "b.txt": (b"b", "Finance")})
    forget_answers(cfg)
    provider = FakeProvider({"a.txt": answer("Finance")})  # no fields; b.txt fails
    monkeypatch.setattr(cli, "get_provider", lambda _: provider)
    path = write_config(tmp_path, downloads)
    result = runner.invoke(cli.app, ["reprocess", "-c", str(path), "--fill-fields", "--yes"])
    assert result.exit_code == 0, result.output
    assert "a.txt  (the document states none of the fields)" in result.stdout
    failed = "b.txt  (classification failed: no fake answer for b.txt; nothing filled)"
    assert failed in result.stdout
    assert "Nothing to record." in result.stdout
    result = runner.invoke(
        cli.app, ["reprocess", "-c", str(path), "--fill-fields", "--rename", "--yes"]
    )
    assert result.exit_code != 0 and "--fill-fields only records" in result.stderr
    result = runner.invoke(
        cli.app, ["reprocess", "-c", str(path), "--fill-fields", "--category", "other", "--yes"]
    )
    assert "Every file in the library already has its fields." in result.stdout
