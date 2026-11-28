"""The terminal's words (#141): what keeps them one source with flib's core.

The English of every message the command line shows for a note flib's core
recorded is the recorded text itself, so the run's log (written in English) and
everything that reads it (``status.count_skips``, the Activity page) see what
they always saw; a review reason reads as ``models.explain_review`` says it. The
columns are measured in terminal cells, not code points.
"""

from __future__ import annotations

import pytest

from file_librarian import i18n
from file_librarian.cli import skip_reason
from file_librarian.i18n import english, formats, t
from file_librarian.i18n.core import detail
from file_librarian.models import ReviewReason, explain_review
from file_librarian.protection import LOCKED_NOW
from file_librarian.scanner import SkipReason
from file_librarian.status import explained

RECORDED = (
    "category Bills",
    "category Bills, original name kept",
    "category Screenshots (input rule, no AI)",
    "category Bills; layout not used (sub-folder '..' is not a plain folder name)",
    "category Bills, original name kept; layout not used (no issuer)",
    "confidence 0.42 < 0.70",
    "model chose unknown category 'Gardening'",
    "sensitive: a credential file",
    "sensitive content",
    "same content as another item of this run, which is left for a later run",
    "same content as another item of this run, which stays (failed)",
    "cannot plan: folder 'X' is not configured",
    "cannot plan",
    "duplicate",
    "not classified",
    "classification failed: the provider timed out",
    "classification failed",
    "invalid answer: confidence must be at most 1",
    "move failed: [Errno 13] Permission denied",
    "not enough space at the destination; retried next run: No space left on device",
    "unknown error",
    "original folder is gone: /Users/someone/Downloads/old",
    "original path is taken: /Users/someone/Downloads/a.pdf",
    "could not move back: Permission denied",
    "changed since it was moved",
    "no longer there (moved or deleted by hand)",
    "moved again since, by run 7 (undo that first)",
    LOCKED_NOW,
    "Finder tag 'Bills' not set: unreadable Finder tags",
    "daily cap reached",
    "provider failing",
    "over max_files_per_run or the per-run cost cap",
    "denied by macOS privacy settings",
)


@pytest.mark.parametrize("text", RECORDED)
def test_a_recorded_note_is_known_and_its_english_is_the_record(text: str) -> None:
    with i18n.using("pt-BR"):
        shown = detail(text)
    assert isinstance(shown, i18n.Msg), text
    assert shown.text("en") == text
    assert shown.text("pt-BR") != text


def test_a_note_flib_does_not_know_is_shown_as_recorded() -> None:
    assert detail("whatever a provider said") == "whatever a provider said"


@pytest.mark.parametrize("reason", list(SkipReason))
def test_a_skip_reason_reads_in_english_as_the_log_records_it(reason: SkipReason) -> None:
    assert skip_reason(reason).text("en") == reason.value
    assert skip_reason(reason).text("pt-BR")


@pytest.mark.parametrize("reason", [None, *ReviewReason])
@pytest.mark.parametrize("suggested", [None, "Bills"])
def test_a_review_reason_reads_as_explain_review(
    reason: ReviewReason | None, suggested: str | None
) -> None:
    assert explained(reason, suggested).text("en") == explain_review(reason, suggested, None)


def test_english_of_a_message_and_of_an_exception() -> None:
    with i18n.using("pt-BR"):
        made = t("term.undo.no_run")
        assert str(made) == "nenhuma execução moveu nada ainda"
        assert english(made) == "no run has moved anything yet"
        assert english(ValueError(made)) == "no run has moved anything yet"
        assert english(ValueError("plain", 2)) == "('plain', 2)"
        assert english("as it is") == "as it is"


def test_a_value_formatted_later_follows_the_message() -> None:
    line = t("term.cost", cost=formats.later(formats.usd, 0.5, 4))
    assert line.text("pt-BR") == "Custo estimado do provedor: US$ 0,5000"
    assert line.text("en") == "Estimated provider cost: US$ 0.5000"
    assert str(formats.later(formats.usd, 0.5, 2)) == "US$ 0.50"


@pytest.mark.parametrize(
    ("text", "columns"),
    [
        ("Agua", 4),
        ("Água", 4),
        ("Água", 4),  # an accent written as its own code point
        ("日本", 4),  # wide characters
        ("", 0),
    ],
)
def test_width_counts_terminal_columns(text: str, columns: int) -> None:
    assert formats.width(text) == columns


def test_padding_and_clipping_by_width() -> None:
    assert formats.ljust("Água", 6) == "Água  "
    assert formats.rjust("Água", 6) == "  Água"
    assert formats.ljust("longer than", 3) == "longer than"
    assert formats.clip("日本語", 4) == "日本"
    assert formats.clip("short", 40) == "short"


def test_a_day_of_the_index_in_each_language() -> None:
    assert formats.day_text("2026-11-25", "pt-BR") == "25/11/2026"
    assert formats.day_text("2026-11-25", "en") == "2026-11-25"
    assert formats.day_text("not a date", "pt-BR") == "not a date"
