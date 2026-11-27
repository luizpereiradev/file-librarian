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
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from datetime import date

from file_librarian.i18n import Msg, t

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
    """``text`` in the catalogue's words when it is one the pages know; else as it is."""
    if isinstance(text, Msg):
        return text
    for pattern, msg_id, params in _KNOWN:
        match = pattern.fullmatch(text)
        if match is not None:
            return t(msg_id, **params(match.groupdict()))
    return text
