"""The Review page and the undo pages (#115), built on the server like the others.

- **Review** (``GET /review``): the files waiting in the review folder, by
  reason and suggested category, the biggest group first. Each file has
  Accept (its suggested category, when that is configured), another category
  and/or a new name, Put back (undo its last move), and Open / Show in
  Finder. A group of more than one with a configured suggestion has "Accept
  all", which goes to a confirmation page first.
- **Accept all** (``GET /review/accept?g=<group>``): lists the files it would
  move (at most ``actions.BULK_CAP``, never one missing on disk) and posts
  them, as one run. ``g`` is keyed with the session's secret.
- **Undo** (``GET /undo?run=<id>`` or ``?file=<id>``): what undoing would
  put back, and where, and what it would skip and why, before anything is
  done; the button posts it.

Every page only reads (the index is opened read-only); the forms post to
the routes in ``server.POST_ROUTES`` (``actions`` does the work), and each
POST ends in a redirect back here with ``?n=<id>``: what happened, kept on
the server for the session, so no name goes in an address and reload,
Back and Forward never do anything again. Text from the index or the model
(names, suggested categories, descriptions) loses its format characters
(``views.shown``); names are isolated (``<bdi>``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus

from file_librarian.config import Config
from file_librarian.db.store import FileRow, OperationRow
from file_librarian.models import ReviewReason, explain_review
from file_librarian.ui import actions
from file_librarian.ui.actions import Group, Outcome
from file_librarian.ui.markup import Markup, csrf_field, el, join, layout, prose
from file_librarian.ui.views import (
    BadRequest,
    Page,
    Unreadable,
    file_actions,
    filename,
    folder,
    message,
    one_choice,
    openable,
    plural,
    query_params,
    read_index,
    sentence,
    shown,
)

GONE = (
    "What happened there is no longer kept (only the latest are, until the session ends). "
    "Reload the page from the menu to see how things are now."
)
MAX_UNDO_LISTED = 200
"""Most moves an undo confirmation lists; the rest are counted."""

REASONS = {
    ReviewReason.LOW_CONFIDENCE: "Not sure",
    ReviewReason.UNKNOWN_CATEGORY: "Category not configured",
    ReviewReason.CATEGORY_REMOVED: "Category removed",
    ReviewReason.MOVED_BY_HAND: "Moved there by hand",
}


def outcome_notice(outcome: Outcome | None, gone: bool) -> Markup | None:
    """What the last action did, at the top of the page it led to."""
    if gone:
        return message(GONE)
    if outcome is None:
        return None
    items = [
        el("li", filename(item.name), " ", el("span", prose(item.text))) for item in outcome.items
    ]
    undo = None
    if outcome.undo_run is not None:
        undo = el("a", outcome.undo_label, href=f"/undo?run={outcome.undo_run}")
    if outcome.name is not None:
        text = el(
            "p",
            filename(outcome.name),
            " ",
            prose(outcome.text),
            class_="notice error" if outcome.error else "notice",
            role="status",
        )
    else:
        text = message(outcome.text, error=outcome.error)
    return el(
        "div",
        text,
        el("ul", *items, class_="moves") if items else None,
        el("p", undo, class_="note") if undo else None,
        class_="outcome",
    )


def _age(processed_at: str, now: datetime) -> str:
    try:
        moment = datetime.fromisoformat(processed_at)
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    days = (now - moment).days
    return "since today" if days <= 0 else f"for {plural(days, 'day')}"


def _title(group: Group) -> str:
    label = "Filed before flib kept a reason"
    if group.reason is not None:
        label = REASONS[group.reason]
    if group.suggested:
        label += f": suggested {shown(group.suggested)}"
    return label


# --- Review ----------------------------------------------------------------------------


def review_page(
    config: Config, csrf: str, outcome: Outcome | None, *, gone: bool, secret: str
) -> Page:
    """``secret``: the session's, which keys each group's name in addresses."""
    parts: list[Markup | None] = [
        el("h1", "Review"),
        el(
            "p",
            f"Files flib was not sure about wait in {config.review_folder}. Accept the "
            "suggestion, pick another category or a new name, or put a file back where it "
            "was. Every change is journaled; Undo reverts it.",
            class_="lead",
        ),
        outcome_notice(outcome, gone),
    ]
    if not config.database.exists():
        parts.append(message(actions.NO_INDEX))
        return HTTPStatus.OK, layout("Review", "review", join(parts), csrf)
    try:
        with read_index(config) as store:
            groups, more = actions.review_groups(store, secret)
    except Unreadable as exc:
        parts.append(message(str(exc), error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout("Review", "review", join(parts), csrf)
    total = sum(len(g.rows) for g in groups) + more
    if not groups:
        parts.append(message("Nothing waits for review."))
        return HTTPStatus.OK, layout("Review", "review", join(parts), csrf)
    parts.append(el("h2", plural(total, "file") + " in review", class_="count"))
    if len(groups) > 1:
        parts.append(
            el(
                "nav",
                *(
                    el("a", f"{_title(g)} ({len(g.rows)})", href=f"#g-{g.key}", class_="chip")
                    for g in groups
                ),
                class_="chips",
                aria_label="Groups",
            )
        )
    now = datetime.now(UTC)
    for group in groups:
        parts.append(_group(group, config, csrf, now))
    if more:
        parts.append(
            message(f"And {plural(more, 'more file')}; they show here once these are done.")
        )
    title = f"Review: {plural(total, 'file')}"
    return HTTPStatus.OK, layout(title, "review", join(parts), csrf)


def _group(group: Group, config: Config, csrf: str, now: datetime) -> Markup:
    target = group.target(config)
    explained = explain_review(group.reason, group.suggested and shown(group.suggested), None)
    head: list[Markup | None] = [
        el("h2", _title(group), " ", el("span", str(len(group.rows)), class_="badge")),
        el("p", sentence(explained) + ".", class_="note"),
    ]
    count, present = len(group.batch), len(group.present)
    if target is not None and count > 1:
        label = f"Accept {target} for all {count}…"
        if count < present:
            label = f"Accept {target} for {count} of the {present}…"
        head.append(
            el(
                "p",
                el("a", label, href=f"/review/accept?g={group.key}", class_="button"),
                class_="bulk",
            )
        )
    files = [_file(row, target, config, csrf, now) for row in group.rows]
    return el(
        "section",
        *head,
        el("ol", *files, class_="review-files", aria_label=_title(group)),
        class_="group",
        id=f"g-{group.key}",
    )


def _hidden(row: FileRow, csrf: str) -> Markup:
    return join(
        csrf_field(csrf),
        el("input", type="hidden", name="id", value=row.id),
        el("input", type="hidden", name="pin", value=actions.file_pin(row)),
    )


def _file(row: FileRow, target: str | None, config: Config, csrf: str, now: datetime) -> Markup:
    name = shown(row.current_name)
    exists = row.current_path.exists() or row.current_path.is_symlink()
    facts = [
        f"from {folder(row.original_path, config)}",
        f"in review {_age(row.processed_at, now)}",
    ]
    if row.confidence is not None:
        facts.append(f"confidence {row.confidence:.2f}")
    parts: list[Markup | None] = [
        el("div", el("strong", filename(row.current_name)), class_="hit-head"),
        el("div", ", ".join(facts), class_="where"),
    ]
    if row.original_name != row.current_name:
        parts.append(el("div", "was ", filename(row.original_name), class_="note"))
    if row.description:
        parts.append(el("p", shown(row.description), class_="desc"))
    if not exists:
        parts.append(
            el(
                "p",
                el("span", "missing: moved by hand?", class_="badge warn"),
                " ",
                prose("`flib reconcile` in Terminal finds it."),
                class_="note",
            )
        )
        return el("li", *parts, class_="hit")
    decide: list[Markup | None] = []
    if target is not None:
        decide.append(
            el(
                "form",
                _hidden(row, csrf),
                el("input", type="hidden", name="category", value=target),
                el(
                    "button",
                    f"Accept: {target}",
                    type="submit",
                    aria_label=f"Accept {target}: {name}",
                ),
                method="post",
                action="/review/file",
                class_="inline",
            )
        )
    # Starts on "stay in review": a new name typed alone only renames (#115 review).
    options = [el("option", "Keep in review (only rename it)", value="", selected=True)]
    options += [el("option", f"Move to {c.name}", value=c.name) for c in config.categories]
    move = el(
        "details",
        el("summary", "Another category or a new name"),
        el(
            "form",
            _hidden(row, csrf),
            el(
                "label",
                el("span", "Category", class_="sr-only"),
                el("select", *options, name="category", aria_label=f"Category for {name}"),
            ),
            el(
                "label",
                el("span", "New name", class_="sr-only"),
                el(
                    "input",
                    type="text",
                    name="name",
                    value="",
                    placeholder="New name (optional)",
                    maxlength=255,
                    autocomplete="off",
                    spellcheck="false",
                    aria_label=f"New name for {name}",
                ),
            ),
            el(
                "button",
                "Move or rename as chosen",
                type="submit",
                class_="quiet",
                aria_label=f"Move or rename as chosen: {name}",
            ),
            method="post",
            action="/review/file",
            class_="move",
        ),
        class_="other",
        open=target is None,  # without a suggestion to accept, this is the way
    )
    parts.append(el("div", *decide, move, class_="decide"))
    tools = file_actions(row.id, name, csrf, can_open=openable(row.current_path))
    put_back = el("a", "Put back where it was…", href=f"/undo?file={row.id}", class_="small-link")
    parts.append(el("div", tools, put_back, class_="tools"))
    return el("li", *parts, class_="hit")


# --- Accept all ------------------------------------------------------------------------


def accept_page(config: Config, csrf: str, query: str, *, secret: str) -> Page:
    params = query_params(query, frozenset({"g"}))
    try:
        key = actions.a_group(one_choice(params, "g"))
    except actions.Malformed as exc:
        raise BadRequest from exc
    parts: list[Markup | None] = [el("h1", "Accept all")]
    try:
        group = _read_group(config, secret, key)
    except Unreadable as exc:
        parts.append(message(str(exc), error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout("Accept all", "review", join(parts), csrf)
    target = None if group is None else group.target(config)
    batch = [] if group is None else group.batch
    if group is None or target is None or not batch:
        parts.append(message("That group is not in review any more.", error=True))
        parts.append(el("p", el("a", "Back to Review", href="/review")))
        return HTTPStatus.OK, layout("Accept all", "review", join(parts), csrf)
    parts.append(
        el(
            "p",
            f"These {plural(len(batch), 'file')} go to {target}, as one run: one Undo puts "
            "them all back in review. A taken name gets the next free number; nothing is "
            "overwritten.",
            class_="lead",
        )
    )
    missing = sum(1 for r in group.rows if r not in batch and not r.current_path.exists())
    if missing:
        parts.append(
            message(
                f"{plural(missing, 'file')} of this group {'is' if missing == 1 else 'are'} "
                "missing on disk and left out."
            )
        )
    if len(batch) + missing < len(group.rows):
        parts.append(
            message(f"Only {len(batch)} at a time; the rest stay in review for the next round.")
        )
    parts.append(
        el(
            "form",
            csrf_field(csrf),
            el("input", type="hidden", name="g", value=key),
            el("input", type="hidden", name="ids", value=",".join(str(r.id) for r in batch)),
            el("input", type="hidden", name="pin", value=actions.group_pin(batch)),
            el("button", f"Move {plural(len(batch), 'file')} to {target}", type="submit"),
            " ",
            el("a", "Cancel", href=f"/review#g-{key}"),
            method="post",
            action="/review/accept",
            class_="confirm",
        )
    )
    parts.append(
        el(
            "ol",
            *(
                el(
                    "li",
                    filename(r.current_name),
                    " ",
                    el("span", f"from {folder(r.original_path, config)}", class_="where"),
                )
                for r in batch
            ),
            class_="moves",
            aria_label="Files",
        )
    )
    return HTTPStatus.OK, layout("Accept all", "review", join(parts), csrf)


def _read_group(config: Config, secret: str, key: str) -> Group | None:
    if not config.database.exists():
        return None
    with read_index(config) as store:
        return actions.find_group(store, secret, key)


# --- Undo ------------------------------------------------------------------------------


def undo_page(
    config: Config, csrf: str, query: str, outcome: Outcome | None, *, gone: bool
) -> Page:
    """The confirmation (``?run=`` or ``?file=``), or what an undo did (``?n=``)."""
    params = query_params(query, frozenset({"run", "file", "n"}))
    run = one_choice(params, "run")
    file = one_choice(params, "file")
    noted = one_choice(params, "n")
    if sum(v is not None for v in (run, file, noted)) != 1:
        raise BadRequest
    parts: list[Markup | None] = [el("h1", "Undo")]
    if noted is not None:
        parts.append(outcome_notice(outcome, gone))
        parts.append(
            el("p", el("a", "Review", href="/review"), " · ", el("a", "Activity", href="/activity"))
        )
        return HTTPStatus.OK, layout("Undo", "", join(parts), csrf)
    value = run or file or ""
    if not value.isdigit() or not value.isascii() or len(value) > actions.MAX_ID_DIGITS:
        raise BadRequest
    number = int(value)
    if not config.database.exists():
        parts.append(message(actions.NO_INDEX))
        return HTTPStatus.OK, layout("Undo", "", join(parts), csrf)
    try:
        with read_index(config) as store:
            if run is not None:
                plan = actions.plan_run(store, number, config)
                body = _run_plan(plan, config, csrf)
            else:
                file_plan = actions.plan_file(store, number, config)
                body = _file_plan(file_plan, config, csrf)
    except actions.Refused as exc:
        parts.append(message(exc.outcome.text, error=True))
        parts.append(el("p", el("a", "Back to Review", href="/review")))
        return HTTPStatus.OK, layout("Undo", "", join(parts), csrf)
    except Unreadable as exc:
        parts.append(message(str(exc), error=True))
        return HTTPStatus.SERVICE_UNAVAILABLE, layout("Undo", "", join(parts), csrf)
    parts.append(body)
    return HTTPStatus.OK, layout("Undo", "", join(parts), csrf)


def _back(op: OperationRow, config: Config, name: str, why: str | None = None) -> Markup:
    """One move as undo would treat it: back where, or skipped and why."""
    if why is not None:
        return el(
            "li", filename(name), " ", el("span", f"skipped: {actions.stop(why)}", class_="note")
        )
    renamed = None
    if op.src_path.name != name:
        renamed = el("span", " (gets its old name back: ", filename(op.src_path.name), ")")
    return el(
        "li",
        filename(name),
        el("span", " → ", aria_hidden="true"),
        el("span", "back to ", class_="sr-only"),
        el("span", folder(op.src_path, config), class_="where"),
        renamed,
    )


def _form(field: str, value: int, pin: str, label: str, csrf: str) -> Markup:
    return el(
        "form",
        csrf_field(csrf),
        el("input", type="hidden", name=field, value=value),
        el("input", type="hidden", name="pin", value=pin),
        el("button", label, type="submit"),
        " ",
        el("a", "Cancel", href="/review"),
        method="post",
        action="/undo",
        class_="confirm",
    )


def _run_plan(plan: actions.RunPlan, config: Config, csrf: str) -> Markup:
    if not plan.ops and plan.settled:
        return _settle_plan(plan, csrf)
    listed = list(reversed(plan.ops))[:MAX_UNDO_LISTED]
    moves = [
        _back(op, config, plan.rows[op.file_id].current_name, plan.blocked.get(op.id))
        for op in listed
    ]
    if len(plan.ops) > MAX_UNDO_LISTED:
        moves.append(el("li", f"and {len(plan.ops) - MAX_UNDO_LISTED} more", class_="note"))
    count, can = len(plan.ops), plan.restorable
    if count and not can:
        lead = (
            f"Nothing of run {plan.run_id} can be put back now: each of its "
            f"{plural(count, 'file')} is listed below with why."
        )
        return join(
            el("p", lead, class_="lead"),
            el("ul", *moves, class_="moves", aria_label="Moves"),
            el("p", el("a", "Back to Review", href="/review")),
        )
    if count:
        lead = f"Undoing run {plan.run_id} puts {plural(can, 'file')} back where "
        lead += "it was" if can == 1 else "they were"
        if can < count:
            lead += f"; {count - can} {'is' if count - can == 1 else 'are'} skipped (see below)"
        if plan.changes:
            lead += ", and puts back what the run changed in the index"
    else:
        lead = f"Undoing run {plan.run_id} puts back what it changed in the index (nothing moves)"
    lead += ". Nothing is ever forced or overwritten."
    return join(
        el("p", lead, class_="lead"),
        _inputs_note(plan.ops, config),
        _form("run", plan.run_id, plan.pin, f"Undo run {plan.run_id}", csrf),
        el("ul", *moves, class_="moves", aria_label="Moves") if moves else None,
    )


def _settle_plan(plan: actions.RunPlan, csrf: str) -> Markup:
    """A run that only marked documents done or open: said in those words."""
    items = [
        el("li", filename(name), " ", "is open again." if opened else "is marked done again.")
        for name, opened in plan.settled
    ]
    lead = "Undoing this puts the Due list back as it was:"
    label = "Open it again" if len(plan.settled) == 1 and plan.settled[0][1] else "Undo it"
    return join(
        el("p", lead, class_="lead"),
        el("ul", *items, class_="moves"),
        _form("run", plan.run_id, plan.pin, label, csrf),
    )


def _inputs_note(ops: list[OperationRow], config: Config) -> Markup | None:
    """A file put back in an input folder stays there: the next runs leave it alone."""
    if any(not op.src_path.is_relative_to(config.output_root) for op in ops):
        return message(
            "A file put back in an input folder stays there: the next runs leave it where "
            "you put it."
        )
    return None


def _file_plan(plan: actions.FilePlan, config: Config, csrf: str) -> Markup:
    row = plan.row
    if plan.blocked is not None:
        return join(
            el("p", "This file's last move cannot be undone now:", class_="lead"),
            el("ul", _back(plan.op, config, row.current_name, plan.blocked), class_="moves"),
            el("p", el("a", "Back to Review", href="/review")),
        )
    return join(
        el("p", "Undoing this file's last move puts it back:", class_="lead"),
        el("ul", _back(plan.op, config, row.current_name), class_="moves"),
        _inputs_note([plan.op], config),
        _form("file", row.id, plan.pin, "Undo this move", csrf),
    )
