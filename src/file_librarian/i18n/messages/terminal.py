"""The command line and the notifications (#141); see ``i18n.messages``.

What ``flib`` prints for a person once it has read the configuration, and the
notifications a scheduled run posts, in the configuration's language. English
is the wording the command line always had. Stays English, whatever the
language: ``--help``, ``--json``, logs, ``flib doctor``, the MCP server.

**Rows.** A run's lines about each file start with a word in a column (``move``,
``skip``, ``organized``...): ``_row`` writes them, padded to one width per
language (``EN_TAG``, ``PT_TAG``, in terminal columns), so the paths line up.
English keeps its eight columns, a longer word followed by one space
(``sensitive``); ``flib undo`` has its own column (``_undo_row``).

**Details.** The notes flib's core records in English (a planner's reason, why an
undo skipped a file) are shown in these words by ``i18n.core.detail``; their
English is the recorded text, which the run's log and ``status.count_skips`` read.
"""

from __future__ import annotations

from file_librarian.i18n import Entry, Plural

EN_TAG = 8
PT_TAG = 11
UNDO_TAG = 10


def _row(tag: str, rest: str, width: int) -> str:
    """``  <tag padded to width><rest>``; a tag as long as the column gets one space."""
    return "  " + (tag.ljust(width) if len(tag) < width else tag + " ") + rest


def _en(tag: str, rest: str) -> str:
    return _row(tag, rest, EN_TAG)


def _pt(tag: str, rest: str) -> str:
    return _row(tag, rest, PT_TAG)


ROW_IDS = (
    "term.row.skip", "term.row.keep", "term.row.move", "term.row.dup", "term.row.moved",
    "term.row.moved_review", "term.row.warning", "term.row.organized", "term.row.review",
    "term.row.skipped", "term.row.missing", "term.row.failed", "term.row.sensitive",
    "term.row.duplicate",
)  # fmt: skip
"""The rows of a run's table, which line up in each language (a test checks it)."""
UNDO_ROW_IDS = ("term.undo.row.restored", "term.undo.row.skipped", "term.undo.row.warning")

EN: dict[str, Entry] = {
    # frames and shared words
    "term.error": "error: {text}",
    "term.warning": "warning: {text}",
    "term.note": "note: {text}",
    "term.text": "{text}",
    "term.and": "{first}, {second}",
    "term.then": "{first}; {second}",
    "term.indented": "  {text}",
    "term.this_mac": "this Mac",
    "term.files": Plural(one="{count} file", other="{count} files"),
    "term.documents": Plural(one="{count} document", other="{count} documents"),
    "term.runs": Plural(one="{count} run(s)", other="{count} run(s)"),
    "term.less_than_a_day": "less than a day",
    "term.left_in_place": Plural(one="{count} left in place", other="{count} left in place"),
    "term.minutes": Plural(one="{count} minute", other="{count} minutes"),
    "term.amount_files": "{amount} ({files})",
    "term.yes_no.default_yes": "[Y/n]",
    "term.yes_no.default_no": "[y/N]",
    "term.yes_no.invalid": "answer y or n",
    "term.state.organized": "organized",
    "term.state.review": "review",
    "term.state.skipped": "skipped",
    "term.state.missing": "missing",
    "term.state.failed": "failed",
    "term.state.sensitive": "sensitive",
    "term.state.duplicate": "duplicate",
    "term.log": "Log: {path}",
    "term.log_failed": "could not write the run log: {error}",
    "term.recovered": Plural(
        one="Recovered {count} move(s) interrupted by an earlier crash.",
        other="Recovered {count} move(s) interrupted by an earlier crash.",
    ),
    "term.cached": Plural(
        one="{count} classification(s) reused from the cache at no cost.",
        other="{count} classification(s) reused from the cache at no cost.",
    ),
    "term.cost": "Estimated provider cost: {cost}",
    "term.cost_unknown": (
        "Provider cost: unknown (no prices configured for this provider; "
        "set provider.price_input_per_mtok and price_output_per_mtok)"
    ),
    "term.orphans": (
        "organized files still use categories missing from the configuration: {names}. "
        "If you renamed one, run `flib reprocess --rename-category {first} NEW`; "
        "if you removed it, run `flib reprocess --remove-category {first}`"
    ),
    "term.index.newer": (
        "the index (schema {schema}) is newer than this flib; upgrade file-librarian"
    ),
    "term.index.older": "the index is from an older flib; the next `flib run` updates it",
    "term.index.unreadable": "cannot read the index: {error}",
    "term.lock.busy": "another flib process (pid {pid}) is running; try again when it finishes",
    "term.lock.reprocess": (
        "another flib reprocess (pid {pid}) is running; try again when it finishes"
    ),
    "term.lock.ui": "flib ui is already running (pid {pid})",
    "term.not_organized": "{path} is not a file flib organized (see `flib search`)",
    "term.issuer_none": "--issuer {value} names no organisation",
    "term.unknown_kind": "unknown kind {value} (kinds: {kinds})",
    # flib status
    "term.status.no_run": "No run yet: the index is created by the first `flib run`.",
    "term.status.review_none": "Review: nothing waits in {folder}.",
    "term.status.review": "Review: {files} in {folder}, the oldest for {age}",
    "term.status.group": "  {count}  {text}",
    "term.explain.low_confidence": "the AI was not sure enough",
    "term.explain.low_confidence_suggested": (
        "the AI was not sure enough; it suggested {suggested}"
    ),
    "term.explain.unknown_category": (
        "the AI chose a category that is not configured: {suggested}"
    ),
    "term.explain.category_removed": "its category was removed from the configuration",
    "term.explain.moved_by_hand": "it was moved to the review folder outside flib",
    "term.explain.unknown": "unknown (filed before flib recorded review reasons)",
    "term.status.failed": "Failed: {files}",
    "term.status.retried": "{attempts} of {limit} attempts; the next run tries again",
    "term.status.given_up": Plural(
        one="gave up after {attempts} attempt",
        other="gave up after {attempts} attempts",
        by="attempts",
    ),
    "term.status.failed_group": "  {count}  {error} ({note}; for {age})",
    "term.status.sensitive": "Left in place as sensitive: {files}",
    "term.duplicates_notice": (
        "Note: files with the same content as one already filed were moved to "
        "{folder} in the organized folder; nothing was deleted. "
        "`flib undo` puts them back, and `duplicates: leave` in the configuration "
        "leaves copies where they are."
    ),
    "term.status.duplicates": (
        "Duplicates set aside in {folder}: {files} (nothing is deleted; review them when you like)"
    ),
    "term.status.by_input": "By input:",
    "term.status.input": Plural(
        one="  {name}: {filed} file filed, {left}",
        other="  {name}: {filed} files filed, {left}",
        by="filed",
    ),
    "term.status.last_run_none": "Last run: none yet.",
    "term.status.last_run": "Last run: {summary}",
    "term.skipped.unreadable": Plural(one="{count} unreadable", other="{count} unreadable"),
    "term.skipped.cloud_only": Plural(one="{count} only in iCloud", other="{count} only in iCloud"),
    "term.skipped.not_where_left": Plural(
        one="{count} waiting for `flib reconcile` "
        "(copies of filed files that are not where flib left them)",
        other="{count} waiting for `flib reconcile` "
        "(copies of filed files that are not where flib left them)",
    ),
    "term.skipped.locked": Plural(
        one="{count} protected while the Mac was locked",
        other="{count} protected while the Mac was locked",
    ),
    "term.skipped.input_unreadable": Plural(
        one="{count} input folder(s) that could not be read",
        other="{count} input folder(s) that could not be read",
    ),
    "term.skipped.too_recent": Plural(
        one="{count} too recent for their input's min_age_days",
        other="{count} too recent for their input's min_age_days",
    ),
    "term.status.skipped": "  skipped: {parts} (see its log)",
    "term.locked_advice_wait": Plural(
        one="{count} file could only be read while the Mac is unlocked (macOS Data "
        "Protection); the scheduled run waits for the next unlock, or run `flib run` now.",
        other="{count} files could only be read while the Mac is unlocked (macOS Data "
        "Protection); the scheduled run waits for the next unlock, or run `flib run` now.",
    ),
    "term.locked_advice": Plural(
        one="{count} file could only be read while the Mac is unlocked (macOS Data "
        "Protection); run `flib run` now.",
        other="{count} files could only be read while the Mac is unlocked (macOS Data "
        "Protection); run `flib run` now.",
    ),
    "term.schedule.unknown": "Next run: unknown ({error})",
    "term.schedule.none": "Next run: none scheduled (`flib schedule` sets up a daily run).",
    "term.schedule.on": "on",
    "term.schedule.not_loaded": "installed but not loaded (run `flib schedule` again)",
    "term.schedule.watch": (
        "Watch mode: {state}; flib runs when something arrives in {folders}, "
        "at most once every {minutes} min"
    ),
    "term.schedule.folders": Plural(one="the input folder", other="the {count} input folders"),
    "term.schedule.daily_not_loaded": (
        "Next run: the daily run is installed but not loaded (run `flib schedule` again)."
    ),
    "term.schedule.next": "Next run: {when} (daily at {time})",
    "term.status.notifier_app": 'Notifications: from the "file-librarian" app, with its own icon.',
    "term.status.notifier_osascript": (
        "Notifications: through osascript, shown as Script Editor ({reason})."
    ),
    "term.status.last_watch": "Last watch run: {when}: {organized}, {review}",
    "term.status.last_watch_none": "Last watch run: none yet.",
    "term.status.today_unpriced": Plural(
        one="Automatic runs today: cost unknown (no prices for {provider}); {seen} files seen",
        other="Automatic runs today: cost unknown (no prices for {provider}); {seen} files seen",
        by="seen",
    ),
    "term.status.today_unpriced_cap": (
        "Automatic runs today: cost unknown (no prices for {provider}); {seen} of {cap} files seen"
    ),
    "term.status.today": "Automatic runs today: {cost}",
    "term.status.today_cap": "Automatic runs today: {cost} of {cap}",
    "term.day_cap": (
        "Automatic runs reached today's cost cap: until midnight they send no "
        "file to the provider; the rest wait for tomorrow, or run `flib run` now."
    ),
    "term.day_cap_amount": (
        "Automatic runs reached today's cost cap ({cap}, limits.max_cost_usd_per_day): "
        "until midnight they send no file to the provider; the rest wait for tomorrow, "
        "or run `flib run` now."
    ),
    "term.status.backlog": Plural(
        one="Watch: {count} file waits in the inputs; a new arrival or the daily run "
        "tries them again, or run `flib run` now.",
        other="Watch: {count} files wait in the inputs; a new arrival or the daily run "
        "tries them again, or run `flib run` now.",
    ),
    "term.status.backlog_why": Plural(
        one="Watch: {count} file waits in the inputs ({why}); a new arrival or the daily "
        "run tries them again, or run `flib run` now.",
        other="Watch: {count} files wait in the inputs ({why}); a new arrival or the daily "
        "run tries them again, or run `flib run` now.",
    ),
    "term.status.month": "This month: {cost}",
    "term.status.month_unpriced": "This month: {cost} (plus {runs} without a recorded cost)",
    "term.cost_cap_none": "daily cost cap for automatic runs: none",
    "term.cost_cap": "daily cost cap for automatic runs: {cap} (limits.max_cost_usd_per_day)",
    "term.sensitive.none": "No file is left in place as sensitive.",
    "term.sensitive.no_reason": "reason not recorded (left in place by an earlier version)",
    "term.sensitive.item": "  {path}  ({why})",
    "term.sensitive.renamed": (
        "A file sensitive only because of its name is looked at again once renamed, or "
        "when `sensitive.allow_names` lets its name through."
    ),
    "term.summary": "Summary: {text}",
    "term.summary_not_shown": (
        "the notification could not be shown ({problem}); "
        "this log and `flib status` have the summary"
    ),
    # notifications
    "notify.summary": "{parts}. Run `flib status` for details.",
    "notify.review": Plural(
        one="{count} file waits for review", other="{count} files wait for review"
    ),
    "notify.reconcile": Plural(
        one="{count} wait for `flib reconcile`", other="{count} wait for `flib reconcile`"
    ),
    "notify.locked": Plural(
        one="{count} could only be read while the Mac is unlocked",
        other="{count} could only be read while the Mac is unlocked",
    ),
    "notify.due_today": Plural(
        one="{count} document is due today", other="{count} documents are due today"
    ),
    "notify.due_within": Plural(
        one="{count} document is due within {days}",
        other="{count} documents are due within {days}",
    ),
    "notify.overdue": Plural(one="{count} document overdue", other="{count} documents overdue"),
    "notify.intro": "Summaries will show here: counts only, never file names.",
    "notify.app.not_built": "the notifier app is not built; `flib schedule --refresh` builds it",
    "notify.app.incomplete": (
        "the notifier app is incomplete; `flib schedule --refresh` rebuilds it"
    ),
    "notify.app.out_of_date": (
        "the notifier app is out of date; `flib schedule --refresh` rebuilds it"
    ),
    "notify.app.current": "built and current",
    "notify.app.not_ready": "the notifier app is not ready",
    # flib due
    "term.due.bad_within": "--within {value} is not a span like 7d, 2w or 1m",
    "term.due.too_far": "--within {value} reaches more than a year ahead",
    "term.due.overdue_within": "--overdue lists what is past due; leave out --within",
    "term.due.status_nothing": "Due: nothing within {days} days.",
    "term.due.status": "Due: {documents} within {days} days (`flib due`).",
    "term.due.status_overdue": "Due: {documents} within {days} days, {overdue} (`flib due`).",
    "term.due.status_refused": ("{path} is {status}; only organized or review files have a due"),
    "term.due.no_due_date": "{path} states no due date, so it is never listed as due",
    "term.due.already_done": "{path} is already marked as done",
    "term.due.not_done": "{path} is not marked as done",
    "term.due.span_any": "from any time to {end} (today is {today})",
    "term.due.span": "from {since} to {end} (today is {today})",
    "term.due.nothing": "Nothing due {span}.",
    "term.due.heading": "Due {span}:",
    "term.due.total": "Total open: {overall}",
    "term.due.without_amount": Plural(
        one="{count} document states no amount; not counted.",
        other="{count} documents state no amount; not counted.",
    ),
    "term.due.hidden": Plural(
        one="{count} document marked done or of another kind not shown (--all shows them).",
        other="{count} documents marked done or of another kind not shown (--all shows them).",
    ),
    "term.due.item": "  {date}  ({when})  {amount}  {issuer}  {kind}",
    "term.due.done": "{when}, done",
    "term.due.done_tag": "{when}, done (Finder tag)",
    "term.due.overdue": "{when}, overdue",
    "term.due.item_where": "    {where}",
    "term.due.item_missing": "    {where}  (missing: moved by hand?)",
    "term.due.item_review": "    {where}  (in review)",
    # flib search
    "term.search.due": "due {date}",
    "term.search.period": "for {period}",
    "term.search.dated": "dated {date}",
    "term.search.none_yet": "none yet",
    "term.search.unknown_category": "unknown category {value} (known: {known})",
    "term.search.bad_extension": "bad extension {value}: give it like pdf or .pdf",
    "term.search.since_after_until": "--since {since} is after --until {until}",
    "term.search.min_over_max": "--min-amount {low} is more than --max-amount {high}",
    "term.search.currency": "--currency {value} is not a currency code like USD or BRL",
    "term.search.bad_period": "--period {value} is not a year or month like 2026 or 2026-09",
    "term.search.amount": "{flag} {value} is not an amount like 100 or 99.90",
    "term.search.date_form": "{flag} {value} is not a date like 2026-01-31",
    "term.search.date_invalid": "{flag} {value} is not a valid date",
    "term.search.semantic_off": (
        "semantic search is off (search.semantic.enabled: false); searched by words only"
    ),
    "term.search.no_embeddings": (
        "no file has an embedding from {model} yet; run `flib search --reindex`. "
        "Searched by words only"
    ),
    "term.search.semantic_unavailable": (
        "semantic search unavailable ({why}); searched by words only"
    ),
    "term.search.outdated": Plural(
        one="{count} file(s) have no up-to-date embedding yet and may be found by words "
        "only; the next run or `flib search --reindex` embeds them",
        other="{count} file(s) have no up-to-date embedding yet and may be found by words "
        "only; the next run or `flib search --reindex` embeds them",
    ),
    "term.search.group_by": "cannot group by {value} (one of: {groups})",
    "term.search.nothing": (
        "give words to search for, a filter, or both. Filters: --category, --ext, --since, "
        "--until (when flib processed it), --issuer, --kind, --min-amount, --max-amount, "
        "--currency, --due-before, --due-after, --period. --sum alone adds up everything."
    ),
    "term.search.by_alone": "--by goes with --sum",
    "term.search.sum_duplicates": "--sum never counts duplicates; leave out --duplicates",
    "term.search.no_close": "no close matches; try other words or --no-semantic",
    "term.search.no_close_matches": "No close matches; try other words or --no-semantic.",
    "term.search.no_matches": "No matches.",
    "term.search.missing": "{path}  (missing: moved by hand?)",
    "term.search.was": "  [{label}] was {name}",
    "term.search.duplicate_of": "  duplicate of {path}",
    "term.search.bad_by": "--by must be one of {groups}, not {value}",
    "term.search.sum_words": "--sum matches the words only, not by meaning",
    "term.sum.none": "No amounts among the matches.",
    "term.sum.by": "Totals by {by}:",
    "term.sum.group.issuer": "issuer",
    "term.sum.group.month": "month",
    "term.sum.group.year": "year",
    "term.sum.group.kind": "kind",
    "term.sum.group.category": "category",
    "term.sum.no_group": "(none)",
    "term.sum.row": "  {label}  {amount}  ({files})",
    "term.sum.files": Plural(one="{count} file(s)", other="{count} file(s)"),
    "term.sum.total": "Total: {overall}",
    "term.sum.without_amount": Plural(
        one="{count} matching file(s) state no amount; not counted.",
        other="{count} matching file(s) state no amount; not counted.",
    ),
    "term.reindex.off": "semantic search is off; set search.semantic.enabled: true first",
    "term.reindex.done": Plural(
        one="Embedded {count} file(s) with {model} ({where}).",
        other="Embedded {count} file(s) with {model} ({where}).",
    ),
    "term.reindex.failed": Plural(
        one="{error}; {count} file(s) still without an embedding",
        other="{error}; {count} file(s) still without an embedding",
    ),
    # flib fix
    "term.fix.nothing": "say what to change: a category, a name, or both",
    "term.fix.status": "{path} is {status}; only organized or review files can be fixed",
    "term.fix.locked": (
        "{path} is protected by macOS while the Mac is locked; unlock it and try again"
    ),
    "term.fix.changed": (
        "{path} changed or moved since it was indexed; run `flib reconcile` first"
    ),
    "term.fix.same_name": "{path} is already there with that name",
    "term.fix.not_moved": "could not move {source}: {error}",
    "term.fix.unknown_category": "unknown category {value} (configured: {known})",
    "term.fix.bad_name": "{value} is not a plain file name (no '/', ':' or leading '.')",
    "term.fix.too_long": "the new name is longer than macOS allows (255 bytes)",
    "term.fix.done_alone": "--done and --not-done go alone, without --category or --name",
    "term.fix.learned_category": "  learned category: {old} -> {new}",
    "term.fix.learned_name": "  learned name: {old} -> {new}",
    "term.fix.warning": "  warning {warning}",
    "term.fix.done": "Fixed in run {run}; `flib undo` reverts it.",
    "term.fix.marked_done": "  marked done  {path}  (due {due})",
    "term.fix.marked_open": "  marked not done (open again)  {path}  (due {due})",
    "term.fix.recorded": "Recorded in run {run}; `flib undo` reverts it.",
    # flib undo
    "term.undo.hint": "; to go further back, name a run with --run ID",
    "term.undo.reprocess_nothing": (
        "the newest reprocess (run {run}) changed nothing, so there is nothing of it to undo{hint}"
    ),
    "term.undo.no_run": "no run has moved anything yet",
    "term.undo.nothing_left": "run {run} has nothing left to undo{hint}",
    "term.undo.not_moved": "{path} is not a file flib moved",
    "term.undo.no_move": "{path} has no move to undo",
    "term.undo.both": "use either --file or --run, not both",
    "term.undo.row.restored": _row("restored", "{source}  ->  {destination}", UNDO_TAG),
    "term.undo.row.skipped": _row("skipped", "{path}  ({why})", UNDO_TAG),
    "term.undo.row.warning": _row("warning", "{path}  ({why})", UNDO_TAG),
    "term.undo.summary": "Undo: {restored}, {skipped}.",
    "term.undo.summary_reverted": "Undo: {restored}, {skipped}, {reverted}.",
    # flib run and flib simulate
    "term.row.skip": _en("skip", "{where}  ({why})"),
    "term.row.keep": _en("keep", "{source}  ({why})"),
    "term.row.move": _en("move", "{source}  ->  {destination}  ({why})"),
    "term.row.dup": _en("dup", "{source}  ->  {destination}  (same content as {original})"),
    "term.row.moved": _en("moved", "{source}  ->  {destination}"),
    "term.row.moved_review": _en("moved", "{source}  ->  {destination}  (review: {why})"),
    "term.row.warning": _en("warning", "{destination}  ({warning})"),
    "term.row.organized": _en("organized", "{source}  ({why})"),
    "term.row.review": _en("review", "{source}  ({why})"),
    "term.row.skipped": _en("skipped", "{source}  ({why})"),
    "term.row.missing": _en("missing", "{source}  ({why})"),
    "term.row.failed": _en("failed", "{source}  ({why})"),
    "term.row.sensitive": _en("sensitive", "{source}  ({why})"),
    "term.row.duplicate": _en("duplicate", "{source}  ({why})"),
    "term.skip.hidden": "hidden",
    "term.skip.symlink": "symlink",
    "term.skip.special": "special file",
    "term.skip.managed": "managed by flib",
    "term.skip.in_progress": "download in progress",
    "term.skip.ignored": "matches ignore",
    "term.skip.not_only": "matches none of the input's only patterns",
    "term.skip.input_unreadable": ("input folder could not be read; the other inputs were scanned"),
    "term.skip.too_recent": "too recent",
    "term.skip.folder_ignored": "folders: ignore",
    "term.skip.unstable": "still changing",
    "term.skip.known": "already processed",
    "term.skip.unreadable": "unreadable",
    "term.skip.locked": "protected by macOS while the Mac is locked",
    "term.skip.empty": "empty file",
    "term.skip.duplicate": "same content as another item in this run",
    "term.skip.cloud_only": "in iCloud only, not downloaded",
    "term.skip.not_where_left": (
        "same content as a filed item that is not where flib left it; run `flib reconcile`"
    ),
    "term.skip.with_detail": "{reason}: {detail}",
    "term.skip.too_recent_left": "{reason} ({left})",
    "term.skip.days_left": Plural(one="{count} day left", other="{count} days left"),
    "term.run.known": Plural(
        one="  ({count} item(s) already processed earlier were left alone)",
        other="  ({count} item(s) already processed earlier were left alone)",
    ),
    "term.run.outside_only": Plural(
        one="  ({count} item(s) matching none of their input's `only` were left alone)",
        other="  ({count} item(s) matching none of their input's `only` were left alone)",
    ),
    "term.run.inputs_unreadable": Plural(
        one="{count} input folder(s) could not be read and were skipped; the others were "
        "organized. `flib schedule` checks folder access.",
        other="{count} input folder(s) could not be read and were skipped; the others were "
        "organized. `flib schedule` checks folder access.",
    ),
    "term.run.access_hint": (
        "hint: macOS denied the scheduled run access to some files. Give flib-python Full Disk "
        "Access (System Settings > Privacy & Security > Full Disk Access) and run "
        "`flib schedule` to check (documentation: Getting started, Run it every day)."
    ),
    "term.simulate.nothing": "Nothing to do: no new files.",
    "term.simulate.stay": Plural(one="{count} would stay", other="{count} would stay"),
    "term.simulate.summary": Plural(
        one="Simulation: {count} would move, {stay}. Nothing changed.",
        other="Simulation: {count} would move, {stay}. Nothing changed.",
    ),
    "term.simulate.summary_duplicates": Plural(
        one="Simulation: {count} would move ({duplicates}), {stay}. Nothing changed.",
        other="Simulation: {count} would move ({duplicates}), {stay}. Nothing changed.",
    ),
    "term.simulate.duplicates": Plural(
        one="{count} duplicate(s) to {folder}", other="{count} duplicate(s) to {folder}"
    ),
    "term.simulate.deferred": Plural(
        one="{count} more file(s) wait for a later run (file or cost limit).",
        other="{count} more file(s) wait for a later run (file or cost limit).",
    ),
    "term.run.summary": "Run {run}: {organized}, {review}, {duplicates}, {sensitive}, "
    "{failed}, {skipped}.",
    "term.run.sensitive": Plural(
        one="{count} sensitive left in place", other="{count} sensitive left in place"
    ),
    "term.run.deferred": Plural(
        one="{count} more file(s) wait for the next run.",
        other="{count} more file(s) wait for the next run.",
    ),
    "term.run.not_again": (
        "Not running again after the unlock: {error}. That run takes care of them."
    ),
    "term.run.not_running": (
        "Not running: {error}. That run or the next change takes care of the new files."
    ),
    "term.run.watch_run": "Watch run: something arrived in an input folder.",
    "term.run.future": Plural(
        one="{count} item(s) have a modification time in the future (a wrong clock, an "
        "archive, a share); they were taken once they stayed the same for the quiet time.",
        other="{count} item(s) have a modification time in the future (a wrong clock, an "
        "archive, a share); they were taken once they stayed the same for the quiet time.",
    ),
    "term.run.left_to": Plural(
        one="{count} file(s) protected while the Mac is locked were left for {who} "
        "(this watch run does not wait for the unlock).",
        other="{count} file(s) protected while the Mac is locked were left for {who} "
        "(this watch run does not wait for the unlock).",
    ),
    "term.run.embedding_failed": (
        "semantic search: {error}; the files not embedded are retried on the next run "
        "(or `flib search --reindex`)."
    ),
    "term.run.summary_failed": "the periodic summary failed: {error}",
    "term.watch.pending": Plural(
        one="{count} item(s) are still downloading or changing (no change for {minutes} "
        "min, or 6 h passed); {who} picks them up.",
        other="{count} item(s) are still downloading or changing (no change for {minutes} "
        "min, or 6 h passed); {who} picks them up.",
    ),
    "term.watch.stopped": "Stopped while waiting for downloads to finish (SIGTERM).",
    "term.watch.next_start": "the watch agent's next start (at most {minutes} min later)",
    "term.watch.next_change": "the next change in the folder (or the daily run)",
    "term.watch.next_start_unlocked": (
        "the watch agent's next start once the Mac is unlocked (at most {minutes} min later)"
    ),
    "term.watch.daily_waits": "the daily run, which waits for the unlock",
    "term.watch.waiting_unlock": (
        "Waiting up to {hours} h for the Mac to be unlocked (checking every minute)."
    ),
    # what a run recorded, shown by ``i18n.core.detail``
    "term.reason.layout_not_used": "{base}; layout not used ({why})",
    "term.reason.category_rule": "category {name} (input rule, no AI)",
    "term.reason.category_kept": "category {name}, original name kept",
    "term.reason.category": "category {name}",
    "term.reason.confidence": "confidence {got} < {need}",
    "term.reason.unknown_category": "model chose unknown category {value}",
    "term.reason.sensitive": "sensitive: {why}",
    "term.reason.sensitive_content": "sensitive content",
    "term.reason.duplicate_later": (
        "same content as another item of this run, which is left for a later run"
    ),
    "term.reason.duplicate_stays": (
        "same content as another item of this run, which stays ({status})"
    ),
    "term.reason.cannot_plan": "cannot plan: {why}",
    "term.reason.cannot_plan_kind": "cannot plan",
    "term.reason.duplicate": "duplicate",
    "term.reason.not_classified": "not classified",
    "term.reason.classification_failed": "classification failed: {why}",
    "term.reason.classification_failed_kind": "classification failed",
    "term.reason.invalid_answer": "invalid answer: {why}",
    "term.reason.invalid_answer_kind": "invalid answer",
    "term.reason.move_failed": "move failed: {why}",
    "term.reason.move_failed_kind": "move failed",
    "term.reason.no_space": "not enough space at the destination; retried next run: {why}",
    "term.reason.no_space_kind": "not enough space at the destination; retried next run",
    "term.reason.unknown_error": "unknown error",
    "term.reason.folder_gone": "original folder is gone: {path}",
    "term.reason.path_taken": "original path is taken: {path}",
    "term.reason.not_moved_back": "could not move back: {why}",
    "term.reason.changed": "changed since it was moved",
    "term.reason.day_cap": "daily cap reached",
    "term.reason.provider_failing": "provider failing",
    "term.reason.run_caps": "over max_files_per_run or the per-run cost cap",
    "term.reason.denied": "denied by macOS privacy settings",
    # flib key
    "term.key.bad_provider": (
        "--provider must be anthropic, openai-compat or embeddings; {provider} takes no key"
    ),
    "term.key.label.anthropic": "Anthropic API key",
    "term.key.label.openai-compat": "openai-compat API key",
    "term.key.label.embeddings": "embeddings API key",
    "term.key.prefix": "Anthropic keys usually start with sk-ant-",
    "term.key.saved": (
        "Saved in the Keychain (service {service}, account {account}). Scheduled runs will use it."
    ),
    "term.key.none_needed": "Provider {provider} takes no API key.",
    "term.key.not_found": "No API key found ({env}, Keychain, env_file). Run `flib key set`.",
    "term.key.not_found_for": (
        "No API key found ({env}, Keychain, env_file). Run `flib key set --provider {provider}`."
    ),
    "term.key.using": "Using the key from: {source} ({hint})",
    "term.key.removed": "Removed from the Keychain.",
    "term.key.none_there": "There was no key in the Keychain.",
    # flib schedule
    "term.schedule.bad_mode": "--mode must be daily, watch or both",
    "term.schedule.removed": "Schedule removed.",
    "term.schedule.nothing_removed": "Nothing was scheduled.",
    "term.schedule.nothing_to_refresh": "Nothing is scheduled; nothing to refresh.",
    "term.schedule.bad_at": "--at must be a 24-hour time like 12:30",
    "term.schedule.development": (
        "flib is running from a development environment; the schedule will "
        "depend on this folder. Install it with ./scripts/install.sh and run "
        "`flib schedule` again from the installed command."
    ),
    "term.schedule.daily": "Daily run scheduled at {time} ({plist}).",
    "term.schedule.on_wake": "If the Mac is asleep then, it runs on wake.",
    "term.schedule.watch_on": Plural(
        one="Watch mode on ({plist}): flib runs when something arrives in {count} input "
        "folder(s), at most once every {minutes} min, once downloads finish.",
        other="Watch mode on ({plist}): flib runs when something arrives in {count} input "
        "folder(s), at most once every {minutes} min, once downloads finish.",
    ),
    "term.schedule.top_level": (
        "macOS watches the top level of each input folder only, which is all flib organizes anyway."
    ),
    "term.schedule.fallback": (
        "It also looks every {minutes} min, for anything a change did not show."
    ),
    "term.schedule.watch_only": (
        "Note: the periodic summary and the due reminders come only after the daily run; "
        "`flib schedule --mode both` keeps both."
    ),
    "term.schedule.remove_hint": "`flib schedule --remove` stops it.",
    "term.schedule.script_editor": (
        "notifications will show as Script Editor ({problem}); "
        "`flib schedule --refresh` tries again."
    ),
    "term.schedule.allow_notifications": (
        'Notifications come from "file-librarian": if macOS asks, allow them '
        "(System Settings > Notifications)."
    ),
    "term.schedule.status_none": "No daily run is scheduled. Run `flib schedule` to set one up.",
    "term.schedule.active": "active",
    "term.schedule.mode": "Mode: {mode}",
    "term.schedule.daily_at": "Daily run at {time}: {state}",
    "term.schedule.watching": (
        "Watch: {state}; at most once every {minutes} min, "
        "when one of these changes (top level only):"
    ),
    "term.schedule.watching_fallback": (
        "Watch: {state}; at most once every {minutes} min, "
        "when one of these changes (top level only), and every {fallback} min:"
    ),
    "term.schedule.runs": "  runs: {program}",
    "term.schedule.interpreter_stale": (
        "flib's interpreter copy is missing or out of date (Python or flib was "
        "updated). Run `flib schedule --refresh`; macOS may ask for access again."
    ),
    "term.schedule.no_key": (
        "no API key found; the daily run will fail until you run `flib key set`."
    ),
    "term.schedule.save_key": (
        "The API key is only in this shell's environment, which the daily run cannot "
        "see. Save it to the macOS Keychain?"
    ),
    "term.schedule.key_saved": "API key saved to the Keychain.",
    "term.schedule.key_not_saved": "could not save the key: {error}",
    "term.schedule.key_not_found": "the daily run will not find the API key; see `flib key set`.",
    "term.schedule.no_claude": (
        "Claude Code was not found; the daily run will fail until you install "
        "it or set provider.command."
    ),
    "term.schedule.claude_path": (
        "the daily run cannot see this shell's PATH and will not find {path}. "
        "Add `command: {path}` under `provider:` in the configuration."
    ),
    "term.schedule.no_environment": (
        "flib is not running from its own environment; install it with ./scripts/install.sh"
    ),
    "term.schedule.watch_needs_input": "watch mode needs at least one input folder",
    "term.schedule.check_timed_out": "the access check did not report back in time",
    "term.schedule.stale_watch": (
        "the watch agent watches other folders than the configuration's inputs; "
        "run `flib schedule --refresh`"
    ),
    "term.access.checking": "Checking folder access as the scheduled run will see it...",
    "term.access.ok": "ok",
    "term.access.denied": (
        "macOS blocks the scheduled run from these folders. Open System Settings > "
        "Privacy & Security > Full Disk Access, click +, press Cmd+Shift+G and add:\n"
        "  {binary}\n"
        "This is flib's own copy of Python, so the access covers flib only, not your other "
        "Python projects. macOS guards the Desktop, Documents and Downloads one by one; "
        "Full Disk Access covers them all at once. Then run `flib schedule` again to "
        "re-check (docs/troubleshooting.md)."
    ),
    "term.access.all_ok": "Access OK: the scheduled runs can read every input folder.",
    # flib menu
    "term.menu.choose_one": "choose one of --install, --remove, --status and --refresh",
    "term.menu.not_removed": "{error}; remove it by hand or run `flib menu --remove` again",
    "term.menu.removed": "Removed from the menu bar.",
    "term.menu.was_not_installed": "The menu bar item was not installed.",
    "term.menu.needs_tools": (
        "the menu bar item cannot be built on this Mac ({missing} missing); `flib status` "
        "and the notifications keep working without it."
    ),
    "term.menu.development": (
        "flib is running from a development environment; the menu bar item "
        "will depend on this folder. Install it with ./scripts/install.sh and run "
        "`flib menu` again from the installed command."
    ),
    "term.menu.not_set_up_error": "could not set up the menu bar item: {error}",
    "term.menu.installed": (
        "The cat is in the menu bar now (it can take a few seconds to show). Its pose says "
        "what needs you; its menu opens flib on the right page."
    ),
    "term.menu.at_login": "It starts at login ({plist}). `flib menu --remove` takes it away.",
    "term.menu.not_rebuilt": "could not rebuild the menu bar item: {error}",
    "term.menu.done": "Menu bar item {what}.",
    "term.menu.rebuilt": "rebuilt and restarted",
    "term.menu.up_to_date": "The menu bar item is up to date.",
    "term.menu.nothing_to_refresh": "The menu bar item is not installed; nothing to refresh.",
    "term.menu.status_none": (
        "The menu bar item is not installed. `flib menu` puts flib in the menu bar."
    ),
    "term.menu.for": "  for: {path}",
    "term.menu.shows": "  it shows: {pose}, {line}",
    "term.menu.running": "A run is in progress; it organizes what is there.",
    "term.menu.kickstarted": "Started the daily run now, as launchd starts it.",
    "term.menu.not_built": "the menu bar app is not built",
    "term.menu.incomplete": "the menu bar app is incomplete",
    "term.menu.out_of_date": "the menu bar app is out of date",
    "term.menu.not_set_up": "flib menu was not set up completely",
    "term.menu.unreadable": "its state cannot be read ({error})",
    "term.menu.line_problem": "Menu bar: {problem}; `flib menu` fixes it.",
    "term.menu.line_shown": "Menu bar: shown (`flib menu --remove` takes it away).",
    "term.menu.line_not_running": (
        "Menu bar: installed, not running ({quit} from its menu); `flib menu` shows it."
    ),
    "term.menu.line_not_loaded": "Menu bar: installed but not loaded; `flib menu` shows it.",
    # flib ui
    "term.ui.started": "flib ui: session started in the browser.",
    "term.ui.stolen": (
        "the launch link was used a second time, by a client without the "
        "session. Another program or account on this Mac may have seen it, so the "
        "session was dropped and this flib ui is ending (its pages say why for a minute; "
        "Ctrl-C ends it now). Run `flib ui` again for a new link: it starts at once."
    ),
    "term.ui.unnamed": (
        "this Mac does not resolve .localhost names to itself, so flib ui is served "
        "at 127.0.0.1. There, browsers send its session cookie to every server on 127.0.0.1, "
        "whatever the port: another program or account listening there could use the "
        "session while it lasts. Current macOS resolves them; see the threat model."
    ),
    "term.ui.private_address": (
        "This address is private until the session ends: the token in it works once, but "
        "the name before the port lasts the session, and anyone who has it and runs a "
        "server on this Mac could take the session. Clear the terminal once you opened it."
    ),
    "term.ui.unconfirmed": (
        "flib ui is already running, but nothing was opened: its address could not be "
        "confirmed as this account's own (its private record is missing or not yours)."
    ),
    "term.ui.ending": "It ends after {minutes} without use, with {end}, or with Ctrl-C.",
    "term.ui.opening": "flib ui: opening it in your browser (port {port}). {ending}",
    "term.ui.no_browser": (
        "could not open the browser ({problem}). Open {handoff} in your browser, or end "
        "this and run `flib ui --no-browser` for a link."
    ),
    "term.ui.link_once": "The link works once; keep it to yourself. {ending}",
    "term.ui.ended": "flib ui ended: {why}.",
    "term.ui.ended.idle": "idle for {minutes}",
    "term.ui.ended.ended": "the session was ended in the browser",
    "term.ui.ended.signal": "interrupted",
    "term.ui.ended.closed": "closed",
    "term.ui.ended.stolen": "the launch link was used again by a client without the session",
    "term.ui.opened_running": (
        "flib ui is already running: opened its {page} page in the browser where "
        'it runs. (Another browser gets the "session ended" page.)'
    ),
    "term.ui.already_running": (
        "{error}. Use the browser tab it opened, or end it there ({end}) or "
        "with Ctrl-C in its terminal, then run `flib ui` again; it also ends on its "
        "own after {minutes} without use."
    ),
}

PT_BR: dict[str, Entry] = {
    # moldura e palavras comuns
    "term.error": "erro: {text}",
    "term.warning": "aviso: {text}",
    "term.note": "observação: {text}",
    "term.text": "{text}",
    "term.and": "{first}, {second}",
    "term.then": "{first}; {second}",
    "term.indented": "  {text}",
    "term.this_mac": "este Mac",
    "term.files": Plural(zero="nenhum arquivo", one="{count} arquivo", other="{count} arquivos"),
    "term.documents": Plural(
        zero="nenhum documento", one="{count} documento", other="{count} documentos"
    ),
    "term.runs": Plural(zero="nenhuma execução", one="{count} execução", other="{count} execuções"),
    "term.less_than_a_day": "menos de um dia",
    "term.left_in_place": Plural(
        zero="nenhum deixado no lugar",
        one="{count} deixado no lugar",
        other="{count} deixados no lugar",
    ),
    "term.minutes": Plural(zero="nenhum minuto", one="{count} minuto", other="{count} minutos"),
    "term.amount_files": "{amount} ({files})",
    "term.yes_no.default_yes": "[S/n]",
    "term.yes_no.default_no": "[s/N]",
    "term.yes_no.invalid": "responda s ou n",
    "term.state.organized": "organizado",
    "term.state.review": "em revisão",
    "term.state.skipped": "ignorado",
    "term.state.missing": "ausente",
    "term.state.failed": "com falha",
    "term.state.sensitive": "sensível",
    "term.state.duplicate": "duplicado",
    "term.log": "Log: {path}",
    "term.log_failed": "não foi possível gravar o log da execução: {error}",
    "term.recovered": Plural(
        zero="Nenhuma movimentação para recuperar.",
        one="Recuperada {count} movimentação interrompida por uma falha anterior.",
        other="Recuperadas {count} movimentações interrompidas por uma falha anterior.",
    ),
    "term.cached": Plural(
        zero="Nenhuma classificação reaproveitada do cache.",
        one="{count} classificação reaproveitada do cache, sem custo.",
        other="{count} classificações reaproveitadas do cache, sem custo.",
    ),
    "term.cost": "Custo estimado do provedor: {cost}",
    "term.cost_unknown": (
        "Custo do provedor: desconhecido (nenhum preço configurado para este provedor; "
        "defina provider.price_input_per_mtok e price_output_per_mtok)"
    ),
    "term.orphans": (
        "arquivos organizados ainda usam categorias que não estão na configuração: {names}. "
        "Se você renomeou uma, rode `flib reprocess --rename-category {first} NOVA`; "
        "se você a removeu, rode `flib reprocess --remove-category {first}`"
    ),
    "term.index.newer": (
        "o índice (esquema {schema}) é mais novo que este flib; atualize o file-librarian"
    ),
    "term.index.older": "o índice é de um flib mais antigo; o próximo `flib run` o atualiza",
    "term.index.unreadable": "não foi possível ler o índice: {error}",
    "term.lock.busy": (
        "outro processo do flib (pid {pid}) está rodando; tente de novo quando ele terminar"
    ),
    "term.lock.reprocess": (
        "outra reclassificação do flib (pid {pid}) está rodando; tente de novo quando ela terminar"
    ),
    "term.lock.ui": "O flib ui já está rodando (pid {pid})",
    "term.not_organized": "{path} não é um arquivo que o flib organizou (veja `flib search`)",
    "term.issuer_none": "--issuer {value} não identifica nenhuma organização",
    "term.unknown_kind": "tipo desconhecido {value} (tipos: {kinds})",
    # flib status
    "term.status.no_run": "Nenhuma execução ainda: o índice é criado pelo primeiro `flib run`.",
    "term.status.review_none": "Revisão: nada espera em {folder}.",
    "term.status.review": "Revisão: {files} em {folder}, o mais antigo há {age}",
    "term.status.group": "  {count}  {text}",
    "term.explain.low_confidence": "a IA não teve certeza suficiente",
    "term.explain.low_confidence_suggested": (
        "a IA não teve certeza suficiente; ela sugeriu {suggested}"
    ),
    "term.explain.unknown_category": (
        "a IA escolheu uma categoria que não está configurada: {suggested}"
    ),
    "term.explain.category_removed": "a categoria dele foi removida da configuração",
    "term.explain.moved_by_hand": "ele foi movido para a pasta de revisão por fora do flib",
    "term.explain.unknown": (
        "desconhecido (arquivado antes de o flib registrar os motivos de revisão)"
    ),
    "term.status.failed": "Com falha: {files}",
    "term.status.retried": "{attempts} de {limit} tentativas; a próxima execução tenta de novo",
    "term.status.given_up": Plural(
        zero="desistiu sem nenhuma tentativa",
        one="desistiu depois de {attempts} tentativa",
        other="desistiu depois de {attempts} tentativas",
        by="attempts",
    ),
    "term.status.failed_group": "  {count}  {error} ({note}; há {age})",
    "term.status.sensitive": "Sensíveis, deixados onde estavam: {files}",
    "term.duplicates_notice": (
        "Observação: arquivos com o mesmo conteúdo de um já organizado foram movidos para "
        "{folder} na pasta organizada; nada foi apagado. `flib undo` os devolve, e "
        "`duplicates: leave` na configuração deixa as cópias onde estão."
    ),
    "term.status.duplicates": (
        "Duplicados separados em {folder}: {files} (nada é apagado; revise quando quiser)"
    ),
    "term.status.by_input": "Por entrada:",
    "term.status.input": Plural(
        zero="  {name}: nenhum arquivo organizado, {left}",
        one="  {name}: {filed} arquivo organizado, {left}",
        other="  {name}: {filed} arquivos organizados, {left}",
        by="filed",
    ),
    "term.status.last_run_none": "Última execução: nenhuma ainda.",
    "term.status.last_run": "Última execução: {summary}",
    "term.skipped.unreadable": Plural(
        zero="nenhum ilegível", one="{count} ilegível", other="{count} ilegíveis"
    ),
    "term.skipped.cloud_only": Plural(
        zero="nenhum só no iCloud", one="{count} só no iCloud", other="{count} só no iCloud"
    ),
    "term.skipped.not_where_left": Plural(
        zero="nenhum esperando o `flib reconcile`",
        one="{count} esperando o `flib reconcile` (cópia de um arquivo organizado que não "
        "está onde o flib o deixou)",
        other="{count} esperando o `flib reconcile` (cópias de arquivos organizados que não "
        "estão onde o flib os deixou)",
    ),
    "term.skipped.locked": Plural(
        zero="nenhum protegido enquanto o Mac estava bloqueado",
        one="{count} protegido enquanto o Mac estava bloqueado",
        other="{count} protegidos enquanto o Mac estava bloqueado",
    ),
    "term.skipped.input_unreadable": Plural(
        zero="nenhuma pasta de entrada ilegível",
        one="{count} pasta de entrada que não pôde ser lida",
        other="{count} pastas de entrada que não puderam ser lidas",
    ),
    "term.skipped.too_recent": Plural(
        zero="nenhum recente demais",
        one="{count} recente demais para o min_age_days da entrada",
        other="{count} recentes demais para o min_age_days da entrada",
    ),
    "term.status.skipped": "  ignorados: {parts} (veja o log da execução)",
    "term.locked_advice_wait": Plural(
        zero="Nenhum arquivo ficou esperando o Mac ser desbloqueado.",
        one="{count} arquivo só pôde ser lido com o Mac desbloqueado (Proteção de Dados do "
        "macOS); a execução agendada espera o próximo desbloqueio, ou rode `flib run` agora.",
        other="{count} arquivos só puderam ser lidos com o Mac desbloqueado (Proteção de Dados "
        "do macOS); a execução agendada espera o próximo desbloqueio, ou rode `flib run` agora.",
    ),
    "term.locked_advice": Plural(
        zero="Nenhum arquivo ficou esperando o Mac ser desbloqueado.",
        one="{count} arquivo só pôde ser lido com o Mac desbloqueado (Proteção de Dados do "
        "macOS); rode `flib run` agora.",
        other="{count} arquivos só puderam ser lidos com o Mac desbloqueado (Proteção de Dados "
        "do macOS); rode `flib run` agora.",
    ),
    "term.schedule.unknown": "Próxima execução: desconhecida ({error})",
    "term.schedule.none": (
        "Próxima execução: nenhuma agendada (`flib schedule` agenda uma execução diária)."
    ),
    "term.schedule.on": "ativo",
    "term.schedule.not_loaded": "instalado, mas não carregado (rode `flib schedule` de novo)",
    "term.schedule.watch": (
        "Modo de observação: {state}; o flib roda quando algo chega {folders}, "
        "no máximo uma vez a cada {minutes} min"
    ),
    "term.schedule.folders": Plural(
        zero="em nenhuma pasta de entrada",
        one="na pasta de entrada",
        other="nas {count} pastas de entrada",
    ),
    "term.schedule.daily_not_loaded": (
        "Próxima execução: a execução diária está instalada, mas não carregada "
        "(rode `flib schedule` de novo)."
    ),
    "term.schedule.next": "Próxima execução: {when} (todo dia às {time})",
    "term.status.notifier_app": 'Notificações: do app "file-librarian", com o ícone dele.',
    "term.status.notifier_osascript": (
        "Notificações: pelo osascript, aparecendo como Editor de Scripts ({reason})."
    ),
    "term.status.last_watch": "Última execução na chegada: {when}: {organized}, {review}",
    "term.status.last_watch_none": "Última execução na chegada: nenhuma ainda.",
    "term.status.today_unpriced": Plural(
        zero="Execuções automáticas hoje: custo desconhecido (sem preços para {provider}); "
        "nenhum arquivo visto",
        one="Execuções automáticas hoje: custo desconhecido (sem preços para {provider}); "
        "{seen} arquivo visto",
        other="Execuções automáticas hoje: custo desconhecido (sem preços para {provider}); "
        "{seen} arquivos vistos",
        by="seen",
    ),
    "term.status.today_unpriced_cap": (
        "Execuções automáticas hoje: custo desconhecido (sem preços para {provider}); "
        "{seen} de {cap} arquivos vistos"
    ),
    "term.status.today": "Execuções automáticas hoje: {cost}",
    "term.status.today_cap": "Execuções automáticas hoje: {cost} de {cap}",
    "term.day_cap": (
        "As execuções automáticas atingiram o limite de custo de hoje: até a meia-noite, elas "
        "não enviam nenhum arquivo ao provedor; o resto espera até amanhã, ou rode `flib run` "
        "agora."
    ),
    "term.day_cap_amount": (
        "As execuções automáticas atingiram o limite de custo de hoje ({cap}, "
        "limits.max_cost_usd_per_day): até a meia-noite, elas não enviam nenhum arquivo ao "
        "provedor; o resto espera até amanhã, ou rode `flib run` agora."
    ),
    "term.status.backlog": Plural(
        zero="Observação: nenhum arquivo espera nas entradas.",
        one="Observação: {count} arquivo espera nas entradas; uma nova chegada ou a execução "
        "diária tenta de novo, ou rode `flib run` agora.",
        other="Observação: {count} arquivos esperam nas entradas; uma nova chegada ou a "
        "execução diária tenta de novo, ou rode `flib run` agora.",
    ),
    "term.status.backlog_why": Plural(
        zero="Observação: nenhum arquivo espera nas entradas ({why}).",
        one="Observação: {count} arquivo espera nas entradas ({why}); uma nova chegada ou a "
        "execução diária tenta de novo, ou rode `flib run` agora.",
        other="Observação: {count} arquivos esperam nas entradas ({why}); uma nova chegada ou "
        "a execução diária tenta de novo, ou rode `flib run` agora.",
    ),
    "term.status.month": "Este mês: {cost}",
    "term.status.month_unpriced": "Este mês: {cost} (mais {runs} sem custo registrado)",
    "term.cost_cap_none": "limite de custo diário das execuções automáticas: nenhum",
    "term.cost_cap": (
        "limite de custo diário das execuções automáticas: {cap} (limits.max_cost_usd_per_day)"
    ),
    "term.sensitive.none": "Nenhum arquivo foi deixado no lugar por ser sensível.",
    "term.sensitive.no_reason": "motivo não registrado (deixado no lugar por uma versão anterior)",
    "term.sensitive.item": "  {path}  ({why})",
    "term.sensitive.renamed": (
        "Um arquivo sensível só por causa do nome é examinado de novo depois de renomeado, ou "
        "quando `sensitive.allow_names` libera o nome dele."
    ),
    "term.summary": "Resumo: {text}",
    "term.summary_not_shown": (
        "não foi possível mostrar a notificação ({problem}); "
        "este log e o `flib status` têm o resumo"
    ),
    # notificações
    "notify.summary": "{parts}. Rode `flib status` para ver os detalhes.",
    "notify.review": Plural(
        zero="nenhum arquivo espera revisão",
        one="{count} arquivo espera revisão",
        other="{count} arquivos esperam revisão",
    ),
    "notify.reconcile": Plural(
        zero="nenhum espera o `flib reconcile`",
        one="{count} espera o `flib reconcile`",
        other="{count} esperam o `flib reconcile`",
    ),
    "notify.locked": Plural(
        zero="nenhum ficou bloqueado",
        one="{count} só pôde ser lido com o Mac desbloqueado",
        other="{count} só puderam ser lidos com o Mac desbloqueado",
    ),
    "notify.due_today": Plural(
        zero="nenhum documento vence hoje",
        one="{count} documento vence hoje",
        other="{count} documentos vencem hoje",
    ),
    "notify.due_within": Plural(
        zero="nenhum documento vence em até {days}",
        one="{count} documento vence em até {days}",
        other="{count} documentos vencem em até {days}",
    ),
    "notify.overdue": Plural(
        zero="nenhum documento vencido",
        one="{count} documento vencido",
        other="{count} documentos vencidos",
    ),
    "notify.intro": "Os resumos vão aparecer aqui: só contagens, nunca nomes de arquivo.",
    "notify.app.not_built": (
        "o app de notificações não foi criado; `flib schedule --refresh` cria o app"
    ),
    "notify.app.incomplete": (
        "o app de notificações está incompleto; `flib schedule --refresh` recria o app"
    ),
    "notify.app.out_of_date": (
        "o app de notificações está desatualizado; `flib schedule --refresh` recria o app"
    ),
    "notify.app.current": "criado e atualizado",
    "notify.app.not_ready": "o app de notificações não está pronto",
    # flib due
    "term.due.bad_within": "--within {value} não é um intervalo como 7d, 2w ou 1m",
    "term.due.too_far": "--within {value} vai mais de um ano à frente",
    "term.due.overdue_within": "--overdue lista o que já venceu; deixe de fora o --within",
    "term.due.status_nothing": "Vencimentos: nada nos próximos {days} dias.",
    "term.due.status": "Vencimentos: {documents} nos próximos {days} dias (`flib due`).",
    "term.due.status_overdue": (
        "Vencimentos: {documents} nos próximos {days} dias, {overdue} (`flib due`)."
    ),
    "term.due.status_refused": (
        "{path} está {status}; só arquivos organizados ou em revisão têm vencimento"
    ),
    "term.due.no_due_date": (
        "{path} não informa data de vencimento, então nunca aparece nos vencimentos"
    ),
    "term.due.already_done": "{path} já está marcado como concluído",
    "term.due.not_done": "{path} não está marcado como concluído",
    "term.due.span_any": "de qualquer data até {end} (hoje é {today})",
    "term.due.span": "de {since} até {end} (hoje é {today})",
    "term.due.nothing": "Nenhum vencimento {span}.",
    "term.due.heading": "Vencimentos {span}:",
    "term.due.total": "Total em aberto: {overall}",
    "term.due.without_amount": Plural(
        zero="Todos os documentos informam o valor.",
        one="{count} documento não informa o valor; não entra na conta.",
        other="{count} documentos não informam o valor; não entram na conta.",
    ),
    "term.due.hidden": Plural(
        zero="Nenhum documento ficou de fora.",
        one="{count} documento concluído ou de outro tipo não aparece (--all mostra).",
        other="{count} documentos concluídos ou de outros tipos não aparecem (--all mostra).",
    ),
    "term.due.item": "  {date}  ({when})  {amount}  {issuer}  {kind}",
    "term.due.done": "{when}, concluído",
    "term.due.done_tag": "{when}, concluído (tag do Finder)",
    "term.due.overdue": "{when}, vencido",
    "term.due.item_where": "    {where}",
    "term.due.item_missing": "    {where}  (sumiu: movido à mão?)",
    "term.due.item_review": "    {where}  (em revisão)",
    # flib search
    "term.search.due": "vence em {date}",
    "term.search.period": "referente a {period}",
    "term.search.dated": "de {date}",
    "term.search.none_yet": "nenhuma ainda",
    "term.search.unknown_category": "categoria desconhecida {value} (existentes: {known})",
    "term.search.bad_extension": "extensão inválida {value}: escreva como pdf ou .pdf",
    "term.search.since_after_until": "--since {since} é depois de --until {until}",
    "term.search.min_over_max": "--min-amount {low} é maior que --max-amount {high}",
    "term.search.currency": "--currency {value} não é um código de moeda como USD ou BRL",
    "term.search.bad_period": "--period {value} não é um ano ou mês como 2026 ou 2026-09",
    "term.search.amount": "{flag} {value} não é um valor como 100 ou 99.90",
    "term.search.date_form": "{flag} {value} não é uma data como 2026-01-31",
    "term.search.date_invalid": "{flag} {value} não é uma data válida",
    "term.search.semantic_off": (
        "a busca por significado está desligada (search.semantic.enabled: false); "
        "busquei só pelas palavras"
    ),
    "term.search.no_embeddings": (
        "nenhum arquivo tem embedding de {model} ainda; rode `flib search --reindex`. "
        "Busquei só pelas palavras"
    ),
    "term.search.semantic_unavailable": (
        "busca por significado indisponível ({why}); busquei só pelas palavras"
    ),
    "term.search.outdated": Plural(
        zero="todos os arquivos têm embedding atualizado",
        one="{count} arquivo ainda não tem embedding atualizado e pode ser encontrado só pelas "
        "palavras; a próxima execução ou `flib search --reindex` cuida dele",
        other="{count} arquivos ainda não têm embedding atualizado e podem ser encontrados só "
        "pelas palavras; a próxima execução ou `flib search --reindex` cuida deles",
    ),
    "term.search.group_by": "não é possível agrupar por {value} (use um de: {groups})",
    "term.search.nothing": (
        "diga palavras para buscar, um filtro, ou os dois. Filtros: --category, --ext, "
        "--since, --until (quando o flib o processou), --issuer, --kind, --min-amount, "
        "--max-amount, --currency, --due-before, --due-after, --period. --sum sozinho soma tudo."
    ),
    "term.search.by_alone": "--by só funciona com --sum",
    "term.search.sum_duplicates": "--sum nunca conta duplicados; deixe de fora o --duplicates",
    "term.search.no_close": "nenhum resultado próximo; tente outras palavras ou --no-semantic",
    "term.search.no_close_matches": (
        "Nenhum resultado próximo; tente outras palavras ou --no-semantic."
    ),
    "term.search.no_matches": "Nenhum resultado.",
    "term.search.missing": "{path}  (sumiu: movido à mão?)",
    "term.search.was": "  [{label}] antes: {name}",
    "term.search.duplicate_of": "  duplicado de {path}",
    "term.search.bad_by": "--by precisa ser um de {groups}, não {value}",
    "term.search.sum_words": "--sum compara só as palavras, não o significado",
    "term.sum.none": "Nenhum valor entre os resultados.",
    "term.sum.by": "Totais por {by}:",
    "term.sum.group.issuer": "emissor",
    "term.sum.group.month": "mês",
    "term.sum.group.year": "ano",
    "term.sum.group.kind": "tipo",
    "term.sum.group.category": "categoria",
    "term.sum.no_group": "(nenhum)",
    "term.sum.row": "  {label}  {amount}  ({files})",
    "term.sum.files": Plural(
        zero="nenhum arquivo", one="{count} arquivo", other="{count} arquivos"
    ),
    "term.sum.total": "Total: {overall}",
    "term.sum.without_amount": Plural(
        zero="Todos os arquivos encontrados informam o valor.",
        one="{count} arquivo encontrado não informa o valor; não entra na conta.",
        other="{count} arquivos encontrados não informam o valor; não entram na conta.",
    ),
    "term.reindex.off": (
        "a busca por significado está desligada; defina search.semantic.enabled: true antes"
    ),
    "term.reindex.done": Plural(
        zero="Nenhum arquivo recebeu embedding com {model} ({where}).",
        one="{count} arquivo recebeu embedding com {model} ({where}).",
        other="{count} arquivos receberam embedding com {model} ({where}).",
    ),
    "term.reindex.failed": Plural(
        zero="{error}; nenhum arquivo ficou sem embedding",
        one="{error}; {count} arquivo ainda está sem embedding",
        other="{error}; {count} arquivos ainda estão sem embedding",
    ),
    # flib fix
    "term.fix.nothing": "diga o que mudar: uma categoria, um nome, ou os dois",
    "term.fix.status": (
        "{path} está {status}; só arquivos organizados ou em revisão podem ser corrigidos"
    ),
    "term.fix.locked": (
        "{path} está protegido pelo macOS enquanto o Mac está bloqueado; desbloqueie e tente "
        "de novo"
    ),
    "term.fix.changed": (
        "{path} mudou ou foi movido desde que foi indexado; rode `flib reconcile` antes"
    ),
    "term.fix.same_name": "{path} já está lá com esse nome",
    "term.fix.not_moved": "não foi possível mover {source}: {error}",
    "term.fix.unknown_category": "categoria desconhecida {value} (configuradas: {known})",
    "term.fix.bad_name": (
        "{value} não é um nome de arquivo simples (sem '/', ':' nem '.' no início)"
    ),
    "term.fix.too_long": "o novo nome é maior do que o macOS permite (255 bytes)",
    "term.fix.done_alone": "--done e --not-done vão sozinhos, sem --category nem --name",
    "term.fix.learned_category": "  categoria aprendida: {old} -> {new}",
    "term.fix.learned_name": "  nome aprendido: {old} -> {new}",
    "term.fix.warning": "  aviso {warning}",
    "term.fix.done": "Corrigido na execução {run}; `flib undo` desfaz.",
    "term.fix.marked_done": "  marcado como concluído  {path}  (vence em {due})",
    "term.fix.marked_open": "  reaberto  {path}  (vence em {due})",
    "term.fix.recorded": "Registrado na execução {run}; `flib undo` desfaz.",
    # flib undo
    "term.undo.hint": "; para voltar mais, indique uma execução com --run ID",
    "term.undo.reprocess_nothing": (
        "a reclassificação mais recente (execução {run}) não mudou nada, então não há nada "
        "dela para desfazer{hint}"
    ),
    "term.undo.no_run": "nenhuma execução moveu nada ainda",
    "term.undo.nothing_left": "a execução {run} não tem mais nada para desfazer{hint}",
    "term.undo.not_moved": "{path} não é um arquivo que o flib moveu",
    "term.undo.no_move": "{path} não tem movimentação para desfazer",
    "term.undo.both": "use --file ou --run, não os dois",
    "term.undo.row.restored": _row("devolvido", "{source}  ->  {destination}", UNDO_TAG),
    "term.undo.row.skipped": _row("ignorado", "{path}  ({why})", UNDO_TAG),
    "term.undo.row.warning": _row("aviso", "{path}  ({why})", UNDO_TAG),
    "term.undo.summary": "Desfeito: {restored}, {skipped}.",
    "term.undo.summary_reverted": "Desfeito: {restored}, {skipped}, {reverted}.",
    # flib run e flib simulate
    "term.row.skip": _pt("ignorado", "{where}  ({why})"),
    "term.row.keep": _pt("fica", "{source}  ({why})"),
    "term.row.move": _pt("mover", "{source}  ->  {destination}  ({why})"),
    "term.row.dup": _pt("duplicado", "{source}  ->  {destination}  (mesmo conteúdo de {original})"),
    "term.row.moved": _pt("movido", "{source}  ->  {destination}"),
    "term.row.moved_review": _pt("movido", "{source}  ->  {destination}  (revisão: {why})"),
    "term.row.warning": _pt("aviso", "{destination}  ({warning})"),
    "term.row.organized": _pt("organizado", "{source}  ({why})"),
    "term.row.review": _pt("revisão", "{source}  ({why})"),
    "term.row.skipped": _pt("ignorado", "{source}  ({why})"),
    "term.row.missing": _pt("ausente", "{source}  ({why})"),
    "term.row.failed": _pt("falhou", "{source}  ({why})"),
    "term.row.sensitive": _pt("sensível", "{source}  ({why})"),
    "term.row.duplicate": _pt("duplicado", "{source}  ({why})"),
    "term.skip.hidden": "oculto",
    "term.skip.symlink": "link simbólico",
    "term.skip.special": "arquivo especial",
    "term.skip.managed": "gerenciado pelo flib",
    "term.skip.in_progress": "download em andamento",
    "term.skip.ignored": "combina com ignore",
    "term.skip.not_only": "não combina com nenhum padrão do only da entrada",
    "term.skip.input_unreadable": (
        "não foi possível ler a pasta de entrada; as outras entradas foram verificadas"
    ),
    "term.skip.too_recent": "recente demais",
    "term.skip.folder_ignored": "folders: ignore",
    "term.skip.unstable": "ainda mudando",
    "term.skip.known": "já processado",
    "term.skip.unreadable": "ilegível",
    "term.skip.locked": "protegido pelo macOS enquanto o Mac está bloqueado",
    "term.skip.empty": "arquivo vazio",
    "term.skip.duplicate": "mesmo conteúdo de outro item desta execução",
    "term.skip.cloud_only": "só no iCloud, não baixado",
    "term.skip.not_where_left": (
        "mesmo conteúdo de um item organizado que não está onde o flib o deixou; "
        "rode `flib reconcile`"
    ),
    "term.skip.with_detail": "{reason}: {detail}",
    "term.skip.too_recent_left": "{reason} ({left})",
    "term.skip.days_left": Plural(
        zero="nenhum dia restante", one="falta {count} dia", other="faltam {count} dias"
    ),
    "term.run.known": Plural(
        zero="  (nenhum item já processado antes)",
        one="  ({count} item já processado antes ficou de fora)",
        other="  ({count} itens já processados antes ficaram de fora)",
    ),
    "term.run.outside_only": Plural(
        zero="  (nenhum item fora do `only` da entrada)",
        one="  ({count} item que não combina com nenhum `only` da entrada ficou de fora)",
        other="  ({count} itens que não combinam com nenhum `only` da entrada ficaram de fora)",
    ),
    "term.run.inputs_unreadable": Plural(
        zero="nenhuma pasta de entrada ficou sem ser lida.",
        one="{count} pasta de entrada não pôde ser lida e foi pulada; as outras foram "
        "organizadas. `flib schedule` verifica o acesso às pastas.",
        other="{count} pastas de entrada não puderam ser lidas e foram puladas; as outras "
        "foram organizadas. `flib schedule` verifica o acesso às pastas.",
    ),
    "term.run.access_hint": (
        "dica: o macOS negou à execução agendada o acesso a alguns arquivos. Dê Acesso Total "
        "ao Disco ao flib-python (Ajustes do Sistema > Privacidade e Segurança > Acesso Total "
        "ao Disco) e rode `flib schedule` para conferir (documentação: Primeiros passos, Rodar "
        "todo dia)."
    ),
    "term.simulate.nothing": "Nada a fazer: nenhum arquivo novo.",
    "term.simulate.stay": Plural(
        zero="nenhum ficaria", one="{count} ficaria", other="{count} ficariam"
    ),
    "term.simulate.summary": Plural(
        zero="Simulação: nenhum seria movido, {stay}. Nada mudou.",
        one="Simulação: {count} seria movido, {stay}. Nada mudou.",
        other="Simulação: {count} seriam movidos, {stay}. Nada mudou.",
    ),
    "term.simulate.summary_duplicates": Plural(
        zero="Simulação: nenhum seria movido ({duplicates}), {stay}. Nada mudou.",
        one="Simulação: {count} seria movido ({duplicates}), {stay}. Nada mudou.",
        other="Simulação: {count} seriam movidos ({duplicates}), {stay}. Nada mudou.",
    ),
    "term.simulate.duplicates": Plural(
        zero="nenhum duplicado",
        one="{count} duplicado para {folder}",
        other="{count} duplicados para {folder}",
    ),
    "term.simulate.deferred": Plural(
        zero="Nenhum arquivo ficou para depois.",
        one="Mais {count} arquivo espera uma próxima execução (limite de arquivos ou de custo).",
        other="Mais {count} arquivos esperam uma próxima execução (limite de arquivos ou de "
        "custo).",
    ),
    "term.run.summary": "Execução {run}: {organized}, {review}, {duplicates}, {sensitive}, "
    "{failed}, {skipped}.",
    "term.run.sensitive": Plural(
        zero="nenhum sensível deixado no lugar",
        one="{count} sensível deixado no lugar",
        other="{count} sensíveis deixados no lugar",
    ),
    "term.run.deferred": Plural(
        zero="Nenhum arquivo ficou para a próxima execução.",
        one="Mais {count} arquivo espera a próxima execução.",
        other="Mais {count} arquivos esperam a próxima execução.",
    ),
    "term.run.not_again": (
        "Não vou rodar de novo depois do desbloqueio: {error}. Aquela execução cuida deles."
    ),
    "term.run.not_running": (
        "Não vou rodar: {error}. Aquela execução ou a próxima mudança cuida dos arquivos novos."
    ),
    "term.run.watch_run": "Execução na chegada: algo chegou numa pasta de entrada.",
    "term.run.future": Plural(
        zero="Nenhum item com data de modificação no futuro.",
        one="{count} item tem data de modificação no futuro (relógio errado, um arquivo "
        "compactado, um compartilhamento); ele foi pego depois de ficar igual pelo tempo de "
        "espera.",
        other="{count} itens têm data de modificação no futuro (relógio errado, um arquivo "
        "compactado, um compartilhamento); eles foram pegos depois de ficarem iguais pelo "
        "tempo de espera.",
    ),
    "term.run.left_to": Plural(
        zero="Nenhum arquivo bloqueado ficou para depois.",
        one="{count} arquivo protegido enquanto o Mac está bloqueado ficou para {who} (esta "
        "execução na chegada não espera o desbloqueio).",
        other="{count} arquivos protegidos enquanto o Mac está bloqueado ficaram para {who} "
        "(esta execução na chegada não espera o desbloqueio).",
    ),
    "term.run.embedding_failed": (
        "busca por significado: {error}; os arquivos sem embedding são tentados de novo na "
        "próxima execução (ou com `flib search --reindex`)."
    ),
    "term.run.summary_failed": "o resumo periódico falhou: {error}",
    "term.watch.pending": Plural(
        zero="Nenhum item ainda baixando ou mudando.",
        one="{count} item ainda está baixando ou mudando (sem mudança por {minutes} min, ou "
        "6 h se passaram); {who} cuida dele.",
        other="{count} itens ainda estão baixando ou mudando (sem mudança por {minutes} min, ou "
        "6 h se passaram); {who} cuida deles.",
    ),
    "term.watch.stopped": "Parou enquanto esperava os downloads terminarem (SIGTERM).",
    "term.watch.next_start": (
        "o próximo início do agente de observação (no máximo {minutes} min depois)"
    ),
    "term.watch.next_change": "a próxima mudança na pasta (ou a execução diária)",
    "term.watch.next_start_unlocked": (
        "o próximo início do agente de observação depois que o Mac for desbloqueado "
        "(no máximo {minutes} min depois)"
    ),
    "term.watch.daily_waits": "a execução diária, que espera o desbloqueio",
    "term.watch.waiting_unlock": (
        "Esperando até {hours} h o Mac ser desbloqueado (verificando a cada minuto)."
    ),
    # o que uma execução registrou, mostrado por ``i18n.core.detail``
    "term.reason.layout_not_used": "{base}; layout não usado ({why})",
    "term.reason.category_rule": "categoria {name} (regra da entrada, sem IA)",
    "term.reason.category_kept": "categoria {name}, nome original mantido",
    "term.reason.category": "categoria {name}",
    "term.reason.confidence": "confiança {got} < {need}",
    "term.reason.unknown_category": "o modelo escolheu a categoria desconhecida {value}",
    "term.reason.sensitive": "sensível: {why}",
    "term.reason.sensitive_content": "conteúdo sensível",
    "term.reason.duplicate_later": (
        "mesmo conteúdo de outro item desta execução, que ficou para uma próxima"
    ),
    "term.reason.duplicate_stays": (
        "mesmo conteúdo de outro item desta execução, que fica onde está ({status})"
    ),
    "term.reason.cannot_plan": "não deu para planejar: {why}",
    "term.reason.cannot_plan_kind": "não deu para planejar",
    "term.reason.duplicate": "duplicado",
    "term.reason.not_classified": "não classificado",
    "term.reason.classification_failed": "a classificação falhou: {why}",
    "term.reason.classification_failed_kind": "a classificação falhou",
    "term.reason.invalid_answer": "resposta inválida: {why}",
    "term.reason.invalid_answer_kind": "resposta inválida",
    "term.reason.move_failed": "a movimentação falhou: {why}",
    "term.reason.move_failed_kind": "a movimentação falhou",
    "term.reason.no_space": (
        "sem espaço suficiente no destino; nova tentativa na próxima execução: {why}"
    ),
    "term.reason.no_space_kind": (
        "sem espaço suficiente no destino; nova tentativa na próxima execução"
    ),
    "term.reason.unknown_error": "erro desconhecido",
    "term.reason.folder_gone": "a pasta de origem não existe mais: {path}",
    "term.reason.path_taken": "o caminho original está ocupado: {path}",
    "term.reason.not_moved_back": "não foi possível devolver: {why}",
    "term.reason.changed": "mudou depois de ser movido",
    "term.reason.day_cap": "limite diário atingido",
    "term.reason.provider_failing": "provedor falhando",
    "term.reason.run_caps": "acima de max_files_per_run ou do limite de custo por execução",
    "term.reason.denied": "negado pelos ajustes de privacidade do macOS",
    # flib key
    "term.key.bad_provider": (
        "--provider precisa ser anthropic, openai-compat ou embeddings; {provider} não usa chave"
    ),
    "term.key.label.anthropic": "Chave de API da Anthropic",
    "term.key.label.openai-compat": "Chave de API do openai-compat",
    "term.key.label.embeddings": "Chave de API dos embeddings",
    "term.key.prefix": "as chaves da Anthropic costumam começar com sk-ant-",
    "term.key.saved": (
        "Salva no Keychain (serviço {service}, conta {account}). As execuções agendadas vão usá-la."
    ),
    "term.key.none_needed": "O provedor {provider} não usa chave de API.",
    "term.key.not_found": (
        "Nenhuma chave de API encontrada ({env}, Keychain, env_file). Rode `flib key set`."
    ),
    "term.key.not_found_for": (
        "Nenhuma chave de API encontrada ({env}, Keychain, env_file). "
        "Rode `flib key set --provider {provider}`."
    ),
    "term.key.using": "Usando a chave de: {source} ({hint})",
    "term.key.removed": "Removida do Keychain.",
    "term.key.none_there": "Não havia chave no Keychain.",
    # flib schedule
    "term.schedule.bad_mode": "--mode precisa ser daily, watch ou both",
    "term.schedule.removed": "Agendamento removido.",
    "term.schedule.nothing_removed": "Não havia nada agendado.",
    "term.schedule.nothing_to_refresh": "Não há nada agendado; nada para atualizar.",
    "term.schedule.bad_at": "--at precisa ser um horário de 24 horas, como 12:30",
    "term.schedule.development": (
        "o flib está rodando de um ambiente de desenvolvimento; o agendamento vai depender "
        "desta pasta. Instale com ./scripts/install.sh e rode `flib schedule` de novo pelo "
        "comando instalado."
    ),
    "term.schedule.daily": "Execução diária agendada para {time} ({plist}).",
    "term.schedule.on_wake": "Se o Mac estiver dormindo nessa hora, ela roda quando ele acordar.",
    "term.schedule.watch_on": Plural(
        zero="Modo de observação ativo ({plist}), mas sem nenhuma pasta de entrada.",
        one="Modo de observação ativo ({plist}): o flib roda quando algo chega em {count} pasta "
        "de entrada, no máximo uma vez a cada {minutes} min, depois que os downloads terminam.",
        other="Modo de observação ativo ({plist}): o flib roda quando algo chega em {count} "
        "pastas de entrada, no máximo uma vez a cada {minutes} min, depois que os downloads "
        "terminam.",
    ),
    "term.schedule.top_level": (
        "O macOS observa só o primeiro nível de cada pasta de entrada, que é tudo o que o flib "
        "organiza mesmo."
    ),
    "term.schedule.fallback": (
        "Ele também confere a cada {minutes} min, para pegar o que uma mudança não mostrou."
    ),
    "term.schedule.watch_only": (
        "Observação: o resumo periódico e os lembretes de vencimento só vêm depois da execução "
        "diária; `flib schedule --mode both` mantém os dois."
    ),
    "term.schedule.remove_hint": "`flib schedule --remove` desliga o agendamento.",
    "term.schedule.script_editor": (
        "as notificações vão aparecer como Editor de Scripts ({problem}); "
        "`flib schedule --refresh` tenta de novo."
    ),
    "term.schedule.allow_notifications": (
        'As notificações vêm do "file-librarian": se o macOS perguntar, permita '
        "(Ajustes do Sistema > Notificações)."
    ),
    "term.schedule.status_none": (
        "Nenhuma execução diária agendada. Rode `flib schedule` para agendar uma."
    ),
    "term.schedule.active": "ativo",
    "term.schedule.mode": "Modo: {mode}",
    "term.schedule.daily_at": "Agendamento diário às {time}: {state}",
    "term.schedule.watching": (
        "Modo de observação: {state}; no máximo uma vez a cada {minutes} min, "
        "quando uma destas pastas muda (só o primeiro nível):"
    ),
    "term.schedule.watching_fallback": (
        "Modo de observação: {state}; no máximo uma vez a cada {minutes} min, "
        "quando uma destas pastas muda (só o primeiro nível), e a cada {fallback} min:"
    ),
    "term.schedule.runs": "  roda: {program}",
    "term.schedule.interpreter_stale": (
        "a cópia do interpretador do flib está faltando ou desatualizada (o Python ou o flib "
        "foi atualizado). Rode `flib schedule --refresh`; o macOS pode pedir acesso de novo."
    ),
    "term.schedule.no_key": (
        "nenhuma chave de API encontrada; a execução diária vai falhar até você rodar "
        "`flib key set`."
    ),
    "term.schedule.save_key": (
        "A chave de API está só no ambiente deste shell, que a execução diária não enxerga. "
        "Salvar no Keychain do macOS?"
    ),
    "term.schedule.key_saved": "Chave de API salva no Keychain.",
    "term.schedule.key_not_saved": "não foi possível salvar a chave: {error}",
    "term.schedule.key_not_found": (
        "a execução diária não vai encontrar a chave de API; veja `flib key set`."
    ),
    "term.schedule.no_claude": (
        "o Claude Code não foi encontrado; a execução diária vai falhar até você instalá-lo "
        "ou definir provider.command."
    ),
    "term.schedule.claude_path": (
        "a execução diária não enxerga o PATH deste shell e não vai encontrar {path}. "
        "Adicione `command: {path}` em `provider:` na configuração."
    ),
    "term.schedule.no_environment": (
        "o flib não está rodando do próprio ambiente; instale com ./scripts/install.sh"
    ),
    "term.schedule.watch_needs_input": (
        "o modo de observação precisa de pelo menos uma pasta de entrada"
    ),
    "term.schedule.check_timed_out": "a verificação de acesso não respondeu a tempo",
    "term.schedule.stale_watch": (
        "o agente de observação observa pastas diferentes das entradas da configuração; "
        "rode `flib schedule --refresh`"
    ),
    "term.access.checking": "Verificando o acesso às pastas como a execução agendada vai vê-lo...",
    "term.access.ok": "ok",
    "term.access.denied": (
        "O macOS bloqueia a execução agendada nestas pastas. Abra Ajustes do Sistema > "
        "Privacidade e Segurança > Acesso Total ao Disco, clique em +, aperte Cmd+Shift+G e "
        "adicione:\n"
        "  {binary}\n"
        "Esta é a cópia do Python do próprio flib, então o acesso vale só para o flib, não para "
        "seus outros projetos em Python. O macOS protege a Mesa, os Documentos e os Downloads "
        "um por um; o Acesso Total ao Disco cobre todos de uma vez. Depois rode "
        "`flib schedule` de novo para conferir (docs/troubleshooting.md)."
    ),
    "term.access.all_ok": (
        "Acesso OK: as execuções agendadas conseguem ler todas as pastas de entrada."
    ),
    # flib menu
    "term.menu.choose_one": "escolha só um de --install, --remove, --status e --refresh",
    "term.menu.not_removed": "{error}; remova à mão ou rode `flib menu --remove` de novo",
    "term.menu.removed": "Removido da barra de menus.",
    "term.menu.was_not_installed": "O item da barra de menus não estava instalado.",
    "term.menu.needs_tools": (
        "não dá para criar o item da barra de menus neste Mac (falta {missing}); o "
        "`flib status` e as notificações continuam funcionando sem ele."
    ),
    "term.menu.development": (
        "o flib está rodando de um ambiente de desenvolvimento; o item da barra de menus vai "
        "depender desta pasta. Instale com ./scripts/install.sh e rode `flib menu` de novo "
        "pelo comando instalado."
    ),
    "term.menu.not_set_up_error": "não foi possível configurar o item da barra de menus: {error}",
    "term.menu.installed": (
        "O gato está na barra de menus agora (pode levar alguns segundos para aparecer). A pose "
        "dele diz o que precisa de você; o menu dele abre o flib na página certa."
    ),
    "term.menu.at_login": "Ele abre no login ({plist}). `flib menu --remove` o tira de lá.",
    "term.menu.not_rebuilt": "não foi possível recriar o item da barra de menus: {error}",
    "term.menu.done": "Item da barra de menus {what}.",
    "term.menu.rebuilt": "recriado e reiniciado",
    "term.menu.up_to_date": "O item da barra de menus está atualizado.",
    "term.menu.nothing_to_refresh": (
        "O item da barra de menus não está instalado; nada para atualizar."
    ),
    "term.menu.status_none": (
        "O item da barra de menus não está instalado. `flib menu` coloca o flib na barra de menus."
    ),
    "term.menu.for": "  para: {path}",
    "term.menu.shows": "  ele mostra: {pose}, {line}",
    "term.menu.running": "Uma execução está em andamento; ela organiza o que estiver lá.",
    "term.menu.kickstarted": "A execução diária começou agora, do jeito que o launchd a inicia.",
    "term.menu.not_built": "o app da barra de menus não foi criado",
    "term.menu.incomplete": "o app da barra de menus está incompleto",
    "term.menu.out_of_date": "o app da barra de menus está desatualizado",
    "term.menu.not_set_up": "o flib menu não foi configurado por completo",
    "term.menu.unreadable": "não foi possível ler o estado dele ({error})",
    "term.menu.line_problem": "Barra de menus: {problem}; `flib menu` resolve.",
    "term.menu.line_shown": (
        "Barra de menus: o item está visível (`flib menu --remove` o tira de lá)."
    ),
    "term.menu.line_not_running": (
        "Barra de menus: o item está instalado, mas fechado ({quit} no menu dele); "
        "`flib menu` o mostra."
    ),
    "term.menu.line_not_loaded": (
        "Barra de menus: o item está instalado, mas não carregado; `flib menu` o mostra."
    ),
    # flib ui
    "term.ui.started": "flib ui: sessão iniciada no navegador.",
    "term.ui.stolen": (
        "o link de abertura foi usado uma segunda vez, por um cliente sem a sessão. Outro "
        "programa ou conta neste Mac pode tê-lo visto, então a sessão foi descartada e este "
        "flib ui está terminando (as páginas dele explicam o motivo por um minuto; Ctrl-C "
        "encerra agora). Rode `flib ui` de novo para ter um link novo: ele começa na hora."
    ),
    "term.ui.unnamed": (
        "este Mac não resolve nomes .localhost para ele mesmo, então o flib ui é servido em "
        "127.0.0.1. Lá, os navegadores mandam o cookie da sessão para qualquer servidor em "
        "127.0.0.1, seja qual for a porta: outro programa ou conta ouvindo ali poderia usar a "
        "sessão enquanto ela durar. O macOS atual resolve esses nomes; veja o modelo de ameaças."
    ),
    "term.ui.private_address": (
        "Este endereço é privado até a sessão terminar: o token nele funciona uma vez, mas o "
        "nome antes da porta dura a sessão inteira, e quem o tiver e rodar um servidor neste "
        "Mac poderia tomar a sessão. Limpe o terminal depois de abri-lo."
    ),
    "term.ui.unconfirmed": (
        "O flib ui já está rodando, mas nada foi aberto: não deu para confirmar que o endereço "
        "dele é desta conta (o registro privado dele está faltando ou não é seu)."
    ),
    "term.ui.ending": "Ele termina depois de {minutes} sem uso, com {end}, ou com Ctrl-C.",
    "term.ui.opening": "flib ui: abrindo no seu navegador (porta {port}). {ending}",
    "term.ui.no_browser": (
        "não foi possível abrir o navegador ({problem}). Abra {handoff} no seu navegador, ou "
        "encerre isto e rode `flib ui --no-browser` para ter um link."
    ),
    "term.ui.link_once": "O link funciona uma vez; guarde-o só para você. {ending}",
    "term.ui.ended": "flib ui terminou: {why}.",
    "term.ui.ended.idle": "sem uso por {minutes}",
    "term.ui.ended.ended": "a sessão foi encerrada no navegador",
    "term.ui.ended.signal": "interrompido",
    "term.ui.ended.closed": "fechado",
    "term.ui.ended.stolen": "o link de abertura foi usado de novo por um cliente sem a sessão",
    "term.ui.opened_running": (
        "O flib ui já está rodando: abri a página {page} no navegador onde ele roda. "
        '(Outro navegador recebe a página "sessão encerrada".)'
    ),
    "term.ui.already_running": (
        "{error}. Use a aba do navegador que ele abriu, ou encerre lá ({end}) ou com Ctrl-C no "
        "terminal dele, e rode `flib ui` de novo; ele também termina sozinho depois de "
        "{minutes} sem uso."
    ),
}
