"""#88 acceptance: "what did I pay to <issuer> in 2026?" is one command, and its answer
is the evaluation corpus's gold total.

The whole corpus is filed into a temporary library by the real pipeline, with
an offline provider that answers each item with its gold labels; then
``flib search --issuer X --period 2026 --sum`` must add up, for every issuer
of the corpus, exactly what the gold says that issuer billed for 2026 (the
period a document refers to, else its date), per currency.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import cli, pipeline
from file_librarian.config import parse_config
from file_librarian.evals import corpus
from file_librarian.naming import issuer_key
from file_librarian.providers.fake import FakeProvider
from tests._helpers import gold_answer

pytestmark = pytest.mark.integration

YEAR = "2026"


def gold_totals() -> dict[str, dict[str, Decimal]]:
    """Issuer key -> currency -> what the gold says was billed for ``YEAR``."""
    totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for item in corpus.items():
        fields = item.gold.fields
        if item.gold.sensitive or "amount" not in fields or "issuer" not in fields:
            continue
        period = fields.get("period") or fields.get("document_date", "")[:7]
        if period == YEAR or period.startswith(YEAR + "-"):
            totals[issuer_key(fields["issuer"])][fields["currency"]] += Decimal(fields["amount"])
    return totals


def test_what_did_i_pay_to_each_issuer_in_2026(tmp_path: Path) -> None:
    inbox, library = tmp_path / "inbox", tmp_path / "library"
    inbox.mkdir()
    items = corpus.items()
    corpus.build(inbox, items)
    raw = {
        "inputs": [str(inbox)],
        "output_root": str(library),
        "database": str(tmp_path / "library.db"),
        "provider": {"name": "fake", "model": "gold"},
        "limits": {"stable_seconds": 0, "max_files_per_run": len(items)},
    }
    cfg = parse_config(raw)
    provider = FakeProvider({item.path: gold_answer(item) for item in items})
    outcome = pipeline.run(cfg, provider, simulate=False)
    assert outcome.report is not None and outcome.report.errors == 0
    config = tmp_path / "config.yaml"
    config.write_text(json.dumps(raw), encoding="utf-8")  # JSON is YAML

    expected = gold_totals()
    assert len(expected) >= 5  # several issuers, in two currencies, some by document date
    assert {c for by_currency in expected.values() for c in by_currency} == {"USD", "BRL"}
    runner = CliRunner()
    for key, by_currency in expected.items():
        issuer = next(
            i.gold.fields["issuer"]
            for i in items
            if issuer_key(i.gold.fields.get("issuer", "")) == key
        )
        args = ["search", "-c", str(config), "--issuer", issuer, "--period", YEAR, "--sum"]
        result = runner.invoke(cli.app, [*args, "--json"])
        assert result.exit_code == 0, result.output
        totals = {t["currency"]: Decimal(t["total"]) for t in json.loads(result.stdout)["totals"]}
        assert totals == dict(by_currency), issuer
