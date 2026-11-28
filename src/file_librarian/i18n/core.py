"""Text from flib's core, in the catalogue's words when the pages show it (#126).

The command line and the interface share their modules (``search``, ``fix``,
``undo``, ``due``, ``activity``), whose errors and notices are English sentences
the command line prints as they are. The pages pass such a text through
``core``: a text it knows (by its shape, with its variable parts taken as
parameters) comes back as a ``Msg`` in the page's words, the command line's
flags named as the form's fields; any other comes back unchanged and is shown in
English. The tests check every pattern (``tests/unit/test_i18n_pages.py``): the
search errors made by ``search.make_filters`` itself, the others from copies of
the core's wording.

The command line (#141) reads the core's notes with ``detail`` (below). The core's
own messages are ``Msg`` now (``term.*``): ``core`` matches one by its English, so a
page still says it in the page's words.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import date

from file_librarian.i18n import Msg, english, t

_FIELDS = {
    "--since": "search.label.since",
    "--until": "search.label.until",
    "--min-amount": "search.label.min_amount",
    "--max-amount": "search.label.max_amount",
    "--due-before": "search.label.due_before",
    "--due-after": "search.label.due_after",
    "--currency": "search.label.currency",
    "--period": "search.label.period",
    "--issuer": "search.label.issuer",
}
"""The command line's flags, as the search form names its fields."""

_FLAG = "(?P<flag>" + "|".join(re.escape(flag) for flag in _FIELDS) + ")"
_QUOTED = r"(?P<value>'[^']*'|\"[^\"]*\")"


def _field(groups: dict[str, str]) -> dict[str, object]:
    found: dict[str, object] = dict(groups)
    found["field"] = t(_FIELDS[groups["flag"]])
    del found["flag"]
    return found


def _dates(groups: dict[str, str]) -> dict[str, object]:
    """Dates the form sent (``2026-11-25``) as the page shows dates (``25/11/2026``)."""
    from file_librarian.i18n import formats

    found: dict[str, object] = dict(groups)
    for key, value in groups.items():
        with contextlib.suppress(ValueError):
            found[key] = formats.day(date.fromisoformat(value.strip()))
    return found


def _kinds(groups: dict[str, str]) -> dict[str, object]:
    """The kinds the search knows by their labels (``conta, recibo…``), not their codes."""
    found: dict[str, object] = dict(groups)
    codes = [code.strip() for code in groups["kinds"].split(",")]
    found["kinds"] = ", ".join(
        t(f"kind.{code}").text() if f"kind.{code}" in _kind_ids() else code for code in codes
    )
    return found


def _kind_ids() -> set[str]:
    from file_librarian.i18n.messages import CATALOGUE

    return {msg_id for msg_id in CATALOGUE["en"] if msg_id.startswith("kind.")}


_KNOWN: list[tuple[re.Pattern[str], str, Callable[[dict[str, str]], dict[str, object]]]] = []


def _known(
    pattern: str, msg_id: str, params: Callable[[dict[str, str]], dict[str, object]] | None = None
) -> None:
    _KNOWN.append((re.compile(pattern + r"\.?", re.DOTALL), msg_id, params or dict))


# search.make_filters (SearchError), with the flags named as the form names them
_known(
    r"unknown category " + _QUOTED + r" \(known: (?P<known>.*)\)", "core.search.unknown_category"
)
_known(r"bad extension " + _QUOTED + r": give it like pdf or \.pdf", "core.search.bad_extension")
_known(
    r"--since (?P<since>.*) is after --until (?P<until>.*)",
    "core.search.since_after_until",
    _dates,
)
_known(r"--issuer " + _QUOTED + r" names no organisation", "core.search.issuer")
_known(
    r"unknown kind " + _QUOTED + r" \(kinds: (?P<kinds>.*)\)", "core.search.unknown_kind", _kinds
)
_known(
    r"--min-amount (?P<low>.*) is more than --max-amount (?P<high>.*)", "core.search.min_over_max"
)
_known(
    r"--currency " + _QUOTED + r" is not a currency code like USD or BRL", "core.search.currency"
)
_known(
    r"--period " + _QUOTED + r" is not a year or month like 2026 or 2026-09", "core.search.period"
)
_known(
    _FLAG + " " + _QUOTED + r" is not an amount like 100 or 99\.90", "core.search.amount", _field
)
_known(_FLAG + " " + _QUOTED + r" is not a date like 2026-01-31", "core.search.date_form", _field)
_known(_FLAG + " " + _QUOTED + r" is not a valid date", "core.search.date_invalid", _field)
# search.semantic_search's notices
_known(
    r"(?P<count>[0-9]+) file\(s\) have no up-to-date embedding yet and may be found by words "
    r"only; the next run or `flib search --reindex` embeds them",
    "core.search.outdated",
    lambda g: {"count": int(g["count"])},
)
_known(
    r"semantic search is off \(search\.semantic\.enabled: false\); searched by words only",
    "core.search.semantic_off",
)
_known(
    r"no file has an embedding from (?P<model>.*) yet; run `flib search --reindex`\. "
    r"Searched by words only",
    "core.search.no_embeddings",
)
_known(
    r"semantic search unavailable \((?P<why>.*)\); searched by words only",
    "core.search.semantic_unavailable",
)
# activity / due: the index
_known(r"cannot read the index", "common.unreadable")
_known(r"the index is from an older flib; the next `flib run` updates it", "core.index.older")
_known(
    r"the index \(schema (?P<schema>[0-9]+)\) is newer than this flib; upgrade file-librarian",
    "core.index.newer",
)
# fix (FixError), undo (NothingToUndoError) and due.settle (DueError), after
# ``actions.without_path`` put "This file" in place of the path
_known(r"This file is not a file flib organized \(see `flib search`\)", "core.fix.not_organized")
_known(
    r"This file is (?P<status>[a-z]+); only organized or review files can be fixed",
    "core.fix.status",
)
_known(
    r"This file is protected by macOS while the Mac is locked; unlock it and try again",
    "core.fix.locked",
)
_known(
    r"This file changed or moved since it was indexed; run `flib reconcile` first",
    "core.fix.changed",
)
_known(r"This file is already there with that name", "core.fix.same_name")
_known(r"the new name is longer than macOS allows \(255 bytes\)", "core.fix.too_long")
_known(r"This file is not a file flib moved", "core.undo.not_moved")
_known(r"This file has no move to undo", "core.undo.no_move")
_known(r"no run has moved anything yet", "core.undo.no_run")
_known(
    r"run (?P<run>[0-9]+) has nothing left to undo",
    "core.undo.nothing_left",
    lambda g: {"run": int(g["run"])},
)
_known(
    r"This file is (?P<status>[a-z]+); only organized or review files have a due",
    "core.due.status",
)
_known(r"This file states no due date, so it is never listed as due", "core.due.no_due_date")
_known(r"This file is already marked as done", "core.due.already_done")
_known(r"This file is not marked as done", "core.due.not_done")
# undo's reasons to skip a move (``undo.skip_reason``), those ``actions`` does not word
_known(r"no longer there \(moved or deleted by hand\)", "core.undo.gone")
_known(
    r"moved again since, by run (?P<run>[0-9]+) \(undo that first\)",
    "core.undo.moved_again",
    lambda g: {"run": int(g["run"])},
)
_known(r"protected by macOS while the Mac is locked; unlock it and try again", "core.undo.locked")
# Finder tags (warnings of a fix or an undo)
_known(r"Finder tag " + _QUOTED + r" not set: (?P<why>.*)", "core.tag.not_set")
_known(r"Finder tag " + _QUOTED + r" not removed: (?P<why>.*)", "core.tag.not_removed")


def core(text: str) -> str:
    """``text`` in the catalogue's words when it is one the pages know; else as it is.

    A message of the command line's (``term.*``, #141) is matched by its English, so
    the page says it in its own words (the form's field names, "This file")."""
    if isinstance(text, Msg):
        if not text.msg_id.startswith("term."):
            return text
        found = core(english(text))
        return found if isinstance(found, Msg) else text
    for pattern, msg_id, params in _KNOWN:
        match = pattern.fullmatch(text)
        if match is not None:
            return t(msg_id, **params(match.groupdict()))
    return text


# --- the terminal's details (#141) -----------------------------------------------------
#
# A run's notes about each file (``planner``'s reasons, ``executor``'s failures), why
# undo skipped a file, why a watch start left files behind, the kind of a failure:
# flib's core records them in English (the index, the logs and the MCP server read
# them as they are). The command line shows them with ``detail``: in the catalogue's
# words when it knows them, else as recorded. A detail that carries text from outside
# flib (a provider's error, an OS error, a path) keeps that text as it is.

_DETAILS: list[tuple[re.Pattern[str], str, Callable[[dict[str, str]], dict[str, object]]]] = []


def _detail(
    pattern: str, msg_id: str, params: Callable[[dict[str, str]], dict[str, object]] | None = None
) -> None:
    _DETAILS.append((re.compile(pattern, re.DOTALL), msg_id, params or dict))


def _confidence(groups: dict[str, str]) -> dict[str, object]:
    from decimal import Decimal

    from file_librarian.i18n import formats

    return {key: formats.later(formats.decimal, Decimal(value), 2) for key, value in groups.items()}


def _state(status: str) -> object:
    """A file's status (``failed``...) in words: its code in English."""
    from file_librarian.i18n.messages import CATALOGUE

    msg_id = f"term.state.{status}"
    return t(msg_id) if msg_id in CATALOGUE["en"] else status


def _nested(groups: dict[str, str]) -> dict[str, object]:
    return {key: detail(value) for key, value in groups.items()}


_detail(r"(?P<base>.+); layout not used \((?P<why>.*)\)", "term.reason.layout_not_used",
        lambda g: {"base": detail(g["base"]), "why": g["why"]})  # fmt: skip
_detail(r"category (?P<name>.+) \(input rule, no AI\)", "term.reason.category_rule")
_detail(r"category (?P<name>.+), original name kept", "term.reason.category_kept")
_detail(r"category (?P<name>.+)", "term.reason.category")
_detail(r"confidence (?P<got>[0-9]+\.[0-9]+) < (?P<need>[0-9]+\.[0-9]+)",
        "term.reason.confidence", _confidence)  # fmt: skip
_detail(r"model chose unknown category (?P<value>.*)", "term.reason.unknown_category")
_detail(r"sensitive: (?P<why>.*)", "term.reason.sensitive", _nested)
_detail(r"sensitive content", "term.reason.sensitive_content")
_detail(r"same content as another item of this run, which is left for a later run",
        "term.reason.duplicate_later")  # fmt: skip
_detail(r"same content as another item of this run, which stays \((?P<status>[a-z]+)\)",
        "term.reason.duplicate_stays", lambda g: {"status": _state(g["status"])})  # fmt: skip
_detail(r"cannot plan: (?P<why>.*)", "term.reason.cannot_plan")
_detail(r"cannot plan", "term.reason.cannot_plan_kind")
_detail(r"duplicate", "term.reason.duplicate")
_detail(r"not classified", "term.reason.not_classified")
_detail(r"classification failed: (?P<why>.*)", "term.reason.classification_failed")
_detail(r"classification failed", "term.reason.classification_failed_kind")
_detail(r"invalid answer: (?P<why>.*)", "term.reason.invalid_answer")
_detail(r"invalid answer", "term.reason.invalid_answer_kind")
_detail(r"move failed: (?P<why>.*)", "term.reason.move_failed")
_detail(r"move failed", "term.reason.move_failed_kind")
_detail(r"not enough space at the destination; retried next run: (?P<why>.*)",
        "term.reason.no_space")  # fmt: skip
_detail(r"not enough space at the destination; retried next run", "term.reason.no_space_kind")
_detail(r"unknown error", "term.reason.unknown_error")
# undo's reasons to skip a file (``undo.skip_reason``), with their path
_detail(r"original folder is gone: (?P<path>.*)", "term.reason.folder_gone")
_detail(r"original path is taken: (?P<path>.*)", "term.reason.path_taken")
_detail(r"could not move back: (?P<why>.*)", "term.reason.not_moved_back")
_detail(r"changed since it was moved", "term.reason.changed")
# why a watch start left ready files in the inputs (``status.backlog_reason``)
_detail(r"daily cap reached", "term.reason.day_cap")
_detail(r"provider failing", "term.reason.provider_failing")
_detail(r"over max_files_per_run or the per-run cost cap", "term.reason.run_caps")
# what the access check under launchd saw (``scheduler.probe_inputs``)
_detail(r"denied by macOS privacy settings", "term.reason.denied")


def detail(text: str) -> str:
    """A note flib's core recorded in English, in the language in use when the
    catalogue knows it (``_DETAILS``, then the pages' ``core``); else as recorded."""
    if isinstance(text, Msg):
        return text
    for pattern, msg_id, params in _DETAILS:
        match = pattern.fullmatch(text)
        if match is not None:
            return t(msg_id, **params(match.groupdict()))
    return core(text)
