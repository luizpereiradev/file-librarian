"""#91 acceptance: "what is due this week?" is one command, and its answer is exactly
the evaluation corpus's gold due dates in the window.

The whole corpus is filed into a temporary library by the real pipeline, with
an offline provider that answers each item with its gold labels; then, for a
few "todays", ``flib due`` must list exactly the items whose gold due date
falls in its window and whose gold kind is one ``due.kinds`` lists, and never
an item without a stated due date.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import cli, due, pipeline
from file_librarian.config import parse_config
from file_librarian.db.store import Store
from file_librarian.evals import corpus
from file_librarian.evals.score import gold_kinds
from file_librarian.providers.fake import FakeProvider
from tests._helpers import gold_answer

pytestmark = pytest.mark.integration

DUE_KINDS = {"bill", "invoice", "tax"}


def expected(items: list[corpus.Item], start: date, end: date, *, every_kind: bool) -> set[str]:
    """The corpus paths the gold says are due from ``start`` to ``end``."""
    found = set()
    for item in items:
        fields = item.gold.fields
        if item.gold.sensitive or "due_date" not in fields:
            continue
        kind = gold_kinds(fields["kind"])[0] if "kind" in fields else None
        if not every_kind and kind not in DUE_KINDS:
            continue
        if start <= date.fromisoformat(fields["due_date"]) <= end:
            found.add(item.path)
    return found


@pytest.mark.parametrize("today", [date(2026, 10, 18), date(2026, 11, 20), date(2026, 2, 5)])
def test_due_lists_exactly_the_gold_due_dates_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, today: date
) -> None:
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
    monkeypatch.setattr(
        due,
        "clock",
        lambda: datetime.combine(today, datetime.min.time(), UTC) + timedelta(hours=12),
    )
    with Store.open(cfg.database) as store:
        original = {str(r.current_path): r.original_name for r in store.all_files()}

    runner = CliRunner()
    for args, start, end, every_kind in (
        ((), today - timedelta(days=30), today + timedelta(days=7), False),
        (("--within", "1m", "--all"), today - timedelta(days=30), due.add_months(today, 1), True),
        (("--overdue",), date.min, today - timedelta(days=1), False),
    ):
        result = runner.invoke(cli.app, ["due", "-c", str(config), "--json", *args])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        listed = [original[item["path"]] for item in data["items"]]
        assert listed or args, "the default window of each today holds gold due dates"
        assert len(listed) == len(set(listed))
        assert set(listed) == expected(items, start, end, every_kind=every_kind), args
        assert [i["due_date"] for i in data["items"]] == sorted(
            i["due_date"] for i in data["items"]
        )


def test_the_corpus_has_due_dates_on_both_sides_of_the_windows() -> None:
    items = corpus.items()
    today = date(2026, 11, 20)
    assert (
        len(
            expected(items, today - timedelta(days=30), today + timedelta(days=7), every_kind=False)
        )
        >= 3
    )
    every = expected(items, date.min, date.max, every_kind=True)
    assert every - expected(items, date.min, date.max, every_kind=False)  # other kinds exist
