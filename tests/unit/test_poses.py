"""The cat's poses (#112): the packaged icons and which state shows which pose."""

from __future__ import annotations

import itertools
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest

from file_librarian import poses
from file_librarian.due import DueCounts
from file_librarian.poses import ICON_SIZE, PRIORITY, MenuState, Pose, icon, menu_state, pose_for
from file_librarian.status import FailedGroup, NextRun, ReviewGroup, Status
from tests._helpers import REPO_ROOT

SVG = "{http://www.w3.org/2000/svg}"
ALLOWED_TAGS = {f"{SVG}svg", f"{SVG}path"}
ALLOWED_ATTRIBUTES = {
    f"{SVG}svg": {"width", "height", "viewBox"},
    f"{SVG}path": {"d", "fill", "fill-rule", "stroke", "stroke-width"},
}
"""No style, opacity, transform, id or class: black shapes and nothing else."""
INK = {"black", "none"}
WHEN = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def _parse(pose: Pose) -> ET.Element:
    # Our own packaged files, not untrusted input.
    return ET.fromstring(icon(pose).read_bytes())  # noqa: S314


@pytest.mark.parametrize("pose", list(Pose))
def test_every_pose_has_a_template_icon_on_the_menu_bar_grid(pose: Pose) -> None:
    root = _parse(pose)
    assert root.tag == f"{SVG}svg"
    assert root.attrib == {
        "width": str(ICON_SIZE),
        "height": str(ICON_SIZE),
        "viewBox": f"0 0 {ICON_SIZE} {ICON_SIZE}",
    }
    paths = list(root)
    assert paths, "an icon draws something"
    for element in root.iter():
        assert element.tag in ALLOWED_TAGS, element.tag
        assert set(element.attrib) <= ALLOWED_ATTRIBUTES[element.tag], element.attrib
        assert not (element.text or "").strip()
    for path in paths:
        assert len(path) == 0
        assert path.get("fill") in INK
        assert path.get("stroke", "none") in INK
        assert "black" in {path.get("fill"), path.get("stroke")}


@pytest.mark.parametrize("pose", list(Pose))
def test_an_icon_is_one_plain_svg_with_nothing_external(pose: Pose) -> None:
    text = icon(pose).read_text(encoding="utf-8")
    assert text.startswith('<svg xmlns="http://www.w3.org/2000/svg" ')
    assert text.endswith("</svg>\n")
    # No editor metadata, comments, entities, scripts, raster or fonts, no link out.
    for banned in ("<!", "<?", "<script", "<image", "<text", "<use", "href", "url(", "data:", "@"):
        assert banned not in text, banned
    assert text.count("xmlns") == 1
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text), "colour comes from macOS, not the file"


def test_the_icons_are_all_different_and_nothing_else_is_shipped() -> None:
    folder = REPO_ROOT / "src" / "file_librarian" / "assets" / "menu"
    assert sorted(p.name for p in folder.iterdir()) == sorted(f"{p}.svg" for p in Pose)
    assert len({icon(p).read_bytes() for p in Pose}) == len(Pose)


def test_the_priority_is_the_issues_order() -> None:
    assert PRIORITY == (Pose.BUSY, Pose.DUE, Pose.TROUBLE, Pose.ATTENTION, Pose.ASLEEP)
    assert [str(p) for p in PRIORITY] == ["busy", "due", "trouble", "attention", "asleep"]


def _status(*, review: int = 0, failed: int = 0, due: DueCounts | None = None) -> Status:
    return Status(
        database_exists=True,
        review=[ReviewGroup(None, None, review, WHEN)] if review else [],
        failed=[FailedGroup("timeout", failed, 1, True, WHEN)] if failed else [],
        sensitive=0,
        last_run=None,
        skipped=None,
        schedule=NextRun(False, False, None, None),
        month_cost_usd=0.0,
        month_unpriced_runs=0,
        last_summary=None,
        due=due,
    )


def test_nothing_pending_is_asleep() -> None:
    assert menu_state(_status(), running=False, blocked=False) == MenuState(Pose.ASLEEP, 0)
    # Before the first run, and on an index from before due dates (#91).
    assert pose_for(_status(due=None), running=False, blocked=False) is Pose.ASLEEP
    nothing_due = _status(due=DueCounts(7, 0, 0))
    assert pose_for(nothing_due, running=False, blocked=False) is Pose.ASLEEP


def test_files_in_review_or_failed_get_attention_with_their_count() -> None:
    assert menu_state(_status(review=3), running=False, blocked=False) == MenuState(
        Pose.ATTENTION, 3
    )
    assert menu_state(_status(failed=2), running=False, blocked=False) == MenuState(
        Pose.ATTENTION, 2
    )
    assert menu_state(_status(review=3, failed=2), running=False, blocked=False).count == 5


def test_due_counts_upcoming_and_overdue() -> None:
    for upcoming, overdue in ((2, 0), (0, 1), (2, 1)):
        counts = DueCounts(7, upcoming, overdue)
        state = menu_state(_status(due=counts), running=False, blocked=False)
        assert state == MenuState(Pose.DUE, upcoming + overdue)


def test_an_unreadable_status_is_trouble() -> None:
    assert menu_state(None, running=False, blocked=False) == MenuState(Pose.TROUBLE, 0)
    assert pose_for(None, running=True, blocked=False) is Pose.BUSY


@pytest.mark.parametrize(
    ("running", "due", "blocked", "waiting"),
    list(itertools.product([False, True], repeat=4)),
)
def test_every_combination_shows_the_first_pose_that_applies(
    running: bool, due: bool, blocked: bool, waiting: bool
) -> None:
    status = _status(review=4 if waiting else 0, due=DueCounts(7, 1 if due else 0, 0))
    applies = {
        Pose.BUSY: running,
        Pose.DUE: due,
        Pose.TROUBLE: blocked,
        Pose.ATTENTION: waiting,
        Pose.ASLEEP: True,
    }
    expected = next(p for p in PRIORITY if applies[p])
    state = menu_state(status, running=running, blocked=blocked)
    assert state.pose is expected
    assert state.count == {Pose.DUE: 1, Pose.ATTENTION: 4}.get(expected, 0)


def test_the_icon_is_read_from_the_package() -> None:
    assert icon(Pose.ASLEEP).name == "asleep.svg"
    assert poses.icon(Pose.BUSY).is_file()
