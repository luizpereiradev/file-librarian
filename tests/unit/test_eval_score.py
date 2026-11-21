"""The scoring math of ``flib eval`` on hand-built results, saving and comparing them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from file_librarian.evals import corpus
from file_librarian.evals.score import (
    FIELDS,
    GOLD_KINDS,
    ItemResult,
    ResultError,
    Run,
    Usage,
    compare,
    field_correct,
    gold_kinds,
    load,
    metrics,
    report_lines,
    to_json,
)
from file_librarian.fields import KINDS

META = {"provider": "fake", "model": "m", "language": "en", "prompt": "5", "corpus": "1"}


def item(
    path: str, status: str, expected: tuple[str, ...], category: str | None, **kw: Any
) -> ItemResult:
    return ItemResult(
        path=path,
        status=status,
        expected=expected,
        category=category,
        sent=status != "sensitive",
        **kw,
    )


def hand_built() -> Run:
    items = [
        # Finance: 2 of 3 right; one went to review with the right suggestion.
        item("a.pdf", "organized", ("Finance",), "Finance", expected_kept=True, kept=True),
        item(
            "b.pdf", "organized", ("Finance", "Other"), "Contracts", expected_kept=False, kept=True
        ),
        item("c.pdf", "review", ("Finance",), "Finance", expected_review=False),
        # Other: 1 of 2; one failed (not classified).
        item("d.txt", "organized", ("Other",), "Other", expected_kept=True, kept=False),
        item("e.txt", "failed", ("Other",), None),
        # Contracts: 0 of 1.
        item("f.docx", "organized", ("Contracts",), "Finance", expected_kept=False, kept=False),
        # Expected in review, went there; no right category: not scored for accuracy.
        item("g.pdf", "review", (), "Other", expected_review=True),
        # Expected in review, was filed.
        item("h.pdf", "organized", ("Other",), "Other", expected_review=True),
        # Sensitive and withheld.
        item("id_rsa", "sensitive", (), None, sensitive=True),
    ]
    usage = Usage(
        elapsed_seconds=30.0, calls=8, input_tokens=8000, output_tokens=800, cost_usd=0.01
    )
    return Run(dict(META), items, usage)


def test_metrics_on_a_hand_built_run() -> None:
    m = metrics(hand_built())
    cat = m["category"]
    assert (cat["scored"], cat["correct"]) == (7, 4)
    assert cat["accuracy"] == pytest.approx(4 / 7, abs=1e-4)
    assert cat["per_category"] == {
        "Contracts": {"items": 1, "correct": 0, "accuracy": 0.0},
        "Finance": {"items": 3, "correct": 2, "accuracy": pytest.approx(0.6667)},
        "Other": {"items": 3, "correct": 2, "accuracy": pytest.approx(0.6667)},
    }
    assert cat["confusion"] == [
        {"expected": "Contracts", "chosen": "Finance", "count": 1},
        {"expected": "Finance", "chosen": "Contracts", "count": 1},
        {"expected": "Other", "chosen": "(none)", "count": 1},
    ]
    assert m["review"] == {"reviewed": 2, "rate": 0.25, "expected": 2, "expected_and_reviewed": 1}
    # Filed with a gold name verdict: a (kept, keep), b (kept, rename), d (renamed, keep),
    # f (renamed, rename). Kept: a, b; of them a should be: precision 1/2. Keep: a, d: recall 1/2.
    assert m["name_kept"] == {"scored": 4, "precision": 0.5, "recall": 0.5}
    assert m["sensitive"] == {"expected": 1, "withheld": 1, "sent": 0}
    assert m["status"] == {"failed": 1, "organized": 5, "review": 2, "sensitive": 1}
    assert m["sent"] == 8
    assert m["items_per_minute"] == 16.0
    assert (m["input_tokens"], m["output_tokens"], m["cost_usd"]) == (8000, 800, 0.01)


def test_empty_and_unknown_values_are_reported_as_such() -> None:
    run = Run(dict(META), [], Usage(cost_known=False))
    m = metrics(run)
    assert m["category"]["accuracy"] is None
    assert m["name_kept"] == {"scored": 0, "precision": None, "recall": None}
    assert m["items_per_minute"] is None
    assert m["cost_usd"] is None
    text = "\n".join(report_lines(run))
    assert "Category accuracy: n/a" in text
    assert "cost unknown" in text


def test_a_sensitive_item_that_was_sent_is_a_leak() -> None:
    run = hand_built()
    leak = item("credentials.json", "organized", (), "Other", sensitive=True)
    run.items.append(leak)
    assert run.leaked == ["credentials.json"]
    assert metrics(run)["sensitive"] == {"expected": 2, "withheld": 1, "sent": 1}


def test_report_lists_categories_worst_first_and_the_mistakes() -> None:
    lines = report_lines(hand_built())
    text = "\n".join(lines)
    assert "Category accuracy: 57.1% (4/7)" in text
    assert text.index("Contracts") < text.index("Finance  ")
    assert "Finance -> Contracts: 1" in text
    assert "2 expected there, 1 of them went" in text
    assert "precision 50.0%, recall 50.0%" in text
    assert "Sensitive: 1/1 withheld, 0 sent." in text
    assert "(16.0 items/min)" in text
    assert "US$ 0.0100" in text


def test_saved_result_reads_back(tmp_path: Path) -> None:
    run = hand_built()
    path = tmp_path / "run.json"
    path.write_text(json.dumps(to_json(run)), encoding="utf-8")
    again = load(path)
    assert again.items == run.items
    assert again.meta == run.meta
    assert metrics(again) == metrics(run)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("not json", "cannot read"),
        ('{"format": 99}', "not a flib eval result"),
        ('{"format": 1, "meta": {}}', "cannot read"),
        ('{"format": 1, "meta": {}, "usage": {}, "items": [{"path": "x"}]}', "cannot read"),
        ("[]", "cannot read"),
    ],
)
def test_unreadable_results_are_refused(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ResultError, match=message):
        load(path)
    with pytest.raises(ResultError, match="cannot read"):
        load(tmp_path / "missing.json")


def test_compare_shows_deltas_and_items_that_changed() -> None:
    base = hand_built()
    better = hand_built()
    better.meta["model"] = "bigger"
    better.items[1] = item("b.pdf", "organized", ("Finance", "Other"), "Finance")
    better.items[0] = item("a.pdf", "organized", ("Finance",), "Other")
    better.items[5] = item("f.docx", "organized", ("Contracts",), "Contracts")
    lines = compare(base, better, changed=2)
    text = "\n".join(lines)
    assert lines[0] == "A: fake/m en prompt v5"
    assert lines[1] == "B: fake/bigger en prompt v5"
    assert "category accuracy" in text and "+14.3 pts" in text
    assert "Contracts" in text  # a per-category row that moved
    assert "Items whose category changed verdict (3):" in text
    assert "broke  a.pdf: Finance -> Other" in text
    assert "fixed  b.pdf: Contracts -> Finance" in text
    assert "... and 1 more" in text
    assert "warning" not in text


def test_compare_warns_about_different_corpora_or_settings() -> None:
    base, other = hand_built(), hand_built()
    other.meta |= {"corpus": "2", "language": "pt-BR", "limit": 30}
    text = "\n".join(compare(base, other))
    assert "different corpus versions" in text
    assert "different languages" in text
    assert "different samples" in text


# --- structured fields (#88) ----------------------------------------------------------------

BILL = {
    "kind": "utility bill",
    "issuer": "Power Co",
    "amount": "103.91",
    "currency": "USD",
    "due_date": "2026-09-20",
    "period": "2026-08",
}


def answered(**fields: str | None) -> dict[str, str | None]:
    return dict.fromkeys(FIELDS) | fields


def fields_run() -> Run:
    items = [
        # All right; the issuer differs only in case and legal form, the amount in format.
        item(
            "a.pdf", "organized", ("Finance",), "Finance", expected_fields=BILL,
            fields=answered(kind="bill", issuer="POWER CO, Inc.", amount="103.910",
                            currency="USD", due_date="2026-09-20", period="2026-08"),
        ),
        # Wrong amount and kind, no due date, an invented document date.
        item(
            "b.pdf", "organized", ("Finance",), "Finance", expected_fields=BILL,
            fields=answered(kind="receipt", issuer="Power Co", amount="13.91", currency="USD",
                            period="2026-08", document_date="2026-09-01"),
        ),
        # Not classified: every gold value is a miss, nothing answered.
        item("c.pdf", "failed", ("Finance",), None, expected_fields=BILL),
        # A contract: gold has its kind only; an amount there is invented.
        item(
            "d.docx", "organized", ("Contracts",), "Contracts", expected_fields={"kind": "lease"},
            fields=answered(kind="contract", amount="900.00", currency="BRL"),
        ),
        # No gold fields at all: not scored for fields.
        item("e.png", "organized", ("Images",), "Images", fields=answered(kind="other")),
        item("id_rsa", "sensitive", (), None, sensitive=True, expected_fields={"kind": "x"}),
    ]  # fmt: skip
    return Run(META, items)


def test_field_scoring_math() -> None:
    fields = metrics(fields_run())["fields"]
    assert fields["kind"] == {
        "gold": 4, "answered": 3, "correct": 2, "precision": 0.6667, "recall": 0.5,
        "without_gold": 0, "invented": 0, "invented_rate": None,
    }  # fmt: skip
    assert fields["issuer"] == {
        "gold": 3, "answered": 2, "correct": 2, "precision": 1.0, "recall": 0.6667,
        "without_gold": 1, "invented": 0, "invented_rate": 0.0,
    }  # fmt: skip
    amount = fields["amount"]
    assert (amount["correct"], amount["answered"], amount["gold"]) == (1, 2, 3)
    assert (amount["invented"], amount["without_gold"]) == (1, 1)
    assert fields["currency"]["invented"] == 1
    assert (fields["due_date"]["precision"], fields["due_date"]["recall"]) == (1.0, 0.3333)
    document_date = fields["document_date"]
    assert (document_date["gold"], document_date["invented"], document_date["invented_rate"]) == (
        0,
        1,
        0.25,
    )
    assert document_date["precision"] is None  # no gold value to be right about


def test_field_report_and_compare() -> None:
    run = fields_run()
    lines = report_lines(run)
    text = "\n".join(lines)
    assert "Fields over 4 item(s) with gold fields: precision (when answered), recall " in text
    assert any(
        line.startswith("  amount") and "50.0% (1/2)" in line and "invented 1/1" in line
        for line in lines
    )
    assert any(line.startswith("  kind") and "invented n/a" in line for line in lines)
    table = compare(hand_built(), run)  # an older result without fields: n/a, no delta
    assert any(line.lstrip().startswith("amount recall") and "n/a" in line for line in table)
    assert not any(line.lstrip().startswith("document_date recall") for line in table)


def test_every_gold_kind_maps_to_known_kinds() -> None:
    for entry in corpus.items():
        kind = entry.gold.fields.get("kind")
        if kind is not None:
            assert kind in GOLD_KINDS, kind
            assert set(gold_kinds(kind)) <= set(KINDS)
    assert gold_kinds("quarterly statement") == ("statement",)  # not listed: its last word
    assert gold_kinds("certificate of something") == ("other",)


def test_field_correct_rules() -> None:
    gold_issuer = {"issuer": "Northwind Power & Light"}
    assert field_correct("issuer", "Northwind Power and Light", gold_issuer)
    assert field_correct("amount", "1234.5", {"amount": "1234.50", "currency": "BRL"})
    assert not field_correct("amount", "1234.5", {"amount": "1234.51", "currency": "BRL"})
    assert field_correct("period", "2026-09", {"period": "2026-09"})
    assert not field_correct("period", "2026", {"period": "2026-09"})
    assert not field_correct("period", None, {"period": "2026-09"})
    assert not field_correct("currency", "USD", {})


def test_a_result_saved_before_fields_reads_back(tmp_path: Path) -> None:
    data = to_json(hand_built())
    for raw in data["items"]:
        del raw["fields"], raw["expected_fields"]
    path = tmp_path / "old.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    run = load(path)
    assert run.items[0].fields == {} and metrics(run)["fields"]["amount"]["gold"] == 0
