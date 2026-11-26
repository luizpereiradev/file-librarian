"""The cat's poses in the menu bar (#112): which one flib's state shows.

The menu bar item (#116, ADR 0004) shows one pose of the cat from the app
icon, as a monochrome template image that macOS tints for light and dark menu
bars. When several apply, the first in this order wins:

1. ``busy``: carrying a sheet of paper; a run is in progress.
2. ``due``: a paw raised; a document is due soon or overdue.
3. ``trouble``: ruffled, back arched; flib cannot run (no key, no access, an
   index it cannot read). Shown instead of ``attention`` when both apply.
4. ``attention``: sitting, looking at you; files wait in review, or failed.
5. ``asleep``: curled up; nothing pending.

``menu_state`` decides from ``status.Status``, the same counts ``flib status``
shows; whether a run is going on and whether flib can run at all are facts it
does not hold, so the caller passes them. The icons are SVG files in
``assets/menu/``, one per pose, named after it; AppKit loads SVG natively, so
the applet copies them into its bundle as they are.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from importlib.resources.abc import Traversable

from file_librarian.status import Status

ICON_SIZE = 18
"""Points, as drawn: the viewBox of every icon and the size the menu bar shows it at."""


class Pose(StrEnum):
    BUSY = "busy"
    DUE = "due"
    TROUBLE = "trouble"
    ATTENTION = "attention"
    ASLEEP = "asleep"


PRIORITY: tuple[Pose, ...] = tuple(Pose)
"""Every pose, the one that wins first (the definition order of ``Pose``)."""


@dataclass(frozen=True, slots=True)
class MenuState:
    pose: Pose
    count: int
    """What the pose is about, for the small number next to the icon: documents
    due for ``due``, files in review or failed for ``attention``, else 0."""


def menu_state(status: Status | None, *, running: bool, blocked: bool) -> MenuState:
    """The pose and its count. ``status`` is ``None`` when it could not be read
    (``StatusError``), which counts as flib not being able to run."""
    due = _due(status)
    waiting = status.review_total + status.failed_total if status else 0
    if running:
        return MenuState(Pose.BUSY, 0)
    if due:
        return MenuState(Pose.DUE, due)
    if blocked or status is None:
        return MenuState(Pose.TROUBLE, 0)
    if waiting:
        return MenuState(Pose.ATTENTION, waiting)
    return MenuState(Pose.ASLEEP, 0)


def pose_for(status: Status | None, *, running: bool, blocked: bool) -> Pose:
    return menu_state(status, running=running, blocked=blocked).pose


def icon(pose: Pose) -> Traversable:
    """The pose's SVG, packaged with flib."""
    return resources.files("file_librarian").joinpath("assets", "menu", f"{pose}.svg")


def _due(status: Status | None) -> int:
    if status is None or status.due is None:
        return 0
    return status.due.upcoming + status.due.overdue
