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
    "term.row.duplicate", "term.row.scrub", "term.row.fill", "term.row.leave", "term.row.rename",
    "term.row.trial_review", "term.row.trial_dup", "term.row.trial_move",
    "term.reconcile.learned", "term.reconcile.found", "term.reconcile.missing",
    "term.reconcile.icloud", "term.reconcile.unread", "term.reconcile.locked",
    "term.reconcile.orphan",
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
    # why a file is sensitive (``sensitive.py``; ``i18n.core.sensitive``)
    "term.sensitive.name": "name looks like a credential file",
    "term.sensitive.name_words": "name looks like a credential file: {words}",
    "term.sensitive.holds": "content: holds a file named like a credential file",
    "term.sensitive.holds_words": "content: holds a file named like {words}",
    "term.sensitive.secrets": "content: looks like {kinds}",
    "term.secret.private_key": "private key",
    "term.secret.aws_access_key": "AWS access key",
    "term.secret.github_token": "GitHub token",
    "term.secret.gitlab_token": "GitLab token",
    "term.secret.slack_token": "Slack token",
    "term.secret.stripe_key": "Stripe key",
    "term.secret.google_api_key": "Google API key",
    "term.secret.google_oauth_client_secret": "Google OAuth client secret",
    "term.secret.anthropic_api_key": "Anthropic API key",
    "term.secret.openai_api_key": "OpenAI API key",
    "term.secret.json_web_token": "JSON web token",
    "term.secret.connection_string_with_password": "connection string with password",
    "term.secret.password_or_secret_assignment": "password or secret assignment",
    "term.secret.password_or_secret_in_json": "password or secret in JSON",
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
    "term.reason.layout_not_used_alone": "layout not used ({why})",
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
    # the configuration's refusals (``config``; pydantic's own in ``i18n.core.refusal``)
    "term.config.invalid": "invalid configuration:",
    "term.config.problem": "  {where}: {why}",
    "term.config.not_mapping": "the configuration must be a YAML mapping",
    "term.config.missing": (
        "no configuration at {path}; run `flib init`, or copy config.example.yaml there"
    ),
    "term.config.unreadable": "cannot read {path}: {error}",
    "term.config.not_yaml": "{path} is not valid YAML: {error}",
    "term.config.spaces": "must not start or end with spaces",
    "term.config.plain_folder": "must be a plain folder name (no '/', ':' or '\\')",
    "term.config.control_or_invisible": "must not contain control or invisible characters",
    "term.config.leading_dot": "must not start with '.'",
    "term.config.absolute": "must be an absolute path or start with '~': {value}",
    "term.config.url_invalid": "{option} is not a valid URL: {error}",
    "term.config.url_scheme": "{option} must be an http:// or https:// URL with a host",
    "term.config.url_credentials": (
        "{option} must not contain a user name or password; "
        "the API key goes in the Keychain or an environment variable"
    ),
    "term.config.url_query": "{option} must not have a query (?) or fragment (#)",
    "term.config.base_url_required": (
        "provider.base_url is required for openai-compat (for example https://api.openai.com/v1)"
    ),
    "term.config.env_name": "{option} must be an environment variable name",
    "term.config.unknown_provider": "unknown provider {value} (known: {known})",
    "term.config.pattern_invalid": "naming.pattern is not a valid pattern: {error}",
    "term.config.pattern_unknown": (
        "naming.pattern: unknown placeholder(s) {unknown}; use {allowed}"
    ),
    "term.config.pattern_subject": "naming.pattern must contain {name}",
    "term.config.pattern_spec": "naming.pattern placeholders take no format spec or conversion",
    "term.config.pattern_path": "naming.pattern must produce a file name, not a path",
    "term.config.pattern_control": "naming.pattern must not contain control characters",
    "term.config.layout_depth": Plural(
        one="layout may have at most {levels} levels of sub-folders",
        other="layout may have at most {levels} levels of sub-folders",
        by="levels",
    ),
    "term.config.layout_slashes": "layout must not start or end with '/' nor contain '//'",
    "term.config.layout_invalid": "layout is not a valid pattern: {error}",
    "term.config.layout_unknown": "layout: unknown placeholder(s) in {segment}; use {allowed}",
    "term.config.layout_spec": "layout placeholders take no format spec or conversion",
    "term.config.layout_issuer": ("layout: {name} must be a whole sub-folder, alone between '/'"),
    "term.config.layout_separators": (
        "layout: sub-folders are separated by '/' only (no ':' or '\\')"
    ),
    "term.config.layout_dots": "layout: {segment} is not a folder name",
    "term.config.layout_folder": "layout: sub-folder {segment} {why}",
    "term.config.layout_long": "layout: sub-folder {segment} is longer than 64 characters",
    "term.config.layout_none": (
        "layout needs at least one placeholder: {first}, {second} or {third}"
    ),
    "term.config.glob_empty": "a pattern must not be empty",
    "term.config.glob_path": "{value}: patterns match a name, not a path (no '/')",
    "term.config.input_option": "input {path}: {option} applies only with a fixed category",
    "term.config.ocr_language": "{value} is not a language code like en-US or pt-BR",
    "term.config.time_form": "schedule.time must look like 12:30",
    "term.config.time_invalid": "schedule.time must be a valid 24-hour time",
    "term.config.done_tag": "due.done_tag must be a Finder tag name",
    "term.config.same_folder.category": (
        "category {name} is the same folder as {other} on macOS "
        "(names are compared ignoring case and Unicode form)"
    ),
    "term.config.same_folder.review_folder": (
        "review_folder {name} is the same folder as {other} on macOS "
        "(names are compared ignoring case and Unicode form)"
    ),
    "term.config.same_folder.duplicates_folder": (
        "duplicates_folder {name} is the same folder as {other} on macOS "
        "(names are compared ignoring case and Unicode form)"
    ),
    "term.config.input_category": (
        "input {path}: category {category} is not a configured category (configured: {known})"
    ),
    "term.config.input_broad": "input {folder} is too broad; pick specific folders",
    "term.config.input_in_output": (
        "input {folder} must not be inside output_root ({output}): "
        "flib would pick up the files it has just filed"
    ),
    "term.config.input_twice": "input {path} is listed twice (also as {other})",
    "term.config.inputs_overlap": (
        "inputs must not overlap: {inner} is inside {outer}; list only one of them"
    ),
    "term.config.database_inside": "database must be outside the input folders and output_root",
    "term.config.p.missing": "Field required",
    "term.config.p.extra": "Extra inputs are not permitted",
    "term.config.p.int": "Input should be a valid integer",
    "term.config.p.int_fraction": (
        "Input should be a valid integer, got a number with a fractional part"
    ),
    "term.config.p.number": "Input should be a valid number",
    "term.config.p.bool": "Input should be a valid boolean",
    "term.config.p.string": "Input should be a valid string",
    "term.config.p.gt": "Input should be greater than {gt}",
    "term.config.p.ge": "Input should be greater than or equal to {ge}",
    "term.config.p.lt": "Input should be less than {lt}",
    "term.config.p.le": "Input should be less than or equal to {le}",
    "term.config.p.string_short": Plural(
        one="String should have at least {min_length} character",
        other="String should have at least {min_length} characters",
        by="min_length",
    ),
    "term.config.p.string_long": Plural(
        one="String should have at most {max_length} character",
        other="String should have at most {max_length} characters",
        by="max_length",
    ),
    "term.config.p.too_short": Plural(
        one="{field_type} should have at least {min_length} item after validation, "
        "not {actual_length}",
        other="{field_type} should have at least {min_length} items after validation, "
        "not {actual_length}",
        by="min_length",
    ),
    "term.config.p.too_long": Plural(
        one="{field_type} should have at most {max_length} item after validation, "
        "not {actual_length}",
        other="{field_type} should have at most {max_length} items after validation, "
        "not {actual_length}",
        by="max_length",
    ),
    "term.config.p.literal": "Input should be {expected}",
    "term.config.p.list": "Input should be a valid list",
    "term.config.p.dict": "Input should be a valid dictionary",
    # flib check-config (and what flib doctor says about it, in English)
    "term.check.key": "  api key: {source} ({hint})",
    "term.check.key_missing": (
        "  api key: not found (run `flib key set`, or set ANTHROPIC_API_KEY)"
    ),
    "term.check.key_none": (
        "  api key: none (fine for a server that needs none; otherwise run "
        "`flib key set --provider {provider}`, or set {env})"
    ),
    "term.check.fixed_no_only": (
        "input {path} has a fixed category and no `only`: everything "
        "in this folder is filed as {category} without being looked at."
    ),
    "term.check.env_file_open": "{path} is readable by other users; run chmod 600 on it",
    "term.check.later_milestone": "a later milestone",
    "term.check.planned_provider": (
        "provider {provider} is planned for {when}; classification commands will not run "
        "until then."
    ),
    "term.check.inactive_option": (
        "{option} is planned for {when} and does nothing yet; your value is accepted but ignored."
    ),
    "term.check.compat_only": (
        "provider.{option} applies to openai-compat and ollama only; {provider} ignores it."
    ),
    "term.check.this_mac_no_limit": "this Mac; no rate limit, never_send still applies",
    "term.check.base_url": "  base_url: {url} ({where})",
    "term.check.http_provider": (
        "provider.base_url uses http:// to another machine; file content "
        "would travel unencrypted. Use https://."
    ),
    "term.check.ollama_remote": (
        "provider.base_url is not this Mac: file content leaves it, and Ollama "
        "is treated as a remote provider."
    ),
    "term.check.no_prices": (
        "provider.price_input_per_mtok and price_output_per_mtok are not set, so "
        "the cost of each file counts as 0 and limits.max_cost_usd_per_run cannot stop a "
        "run. Set them to your provider's prices."
    ),
    "term.check.vision_off": (
        "  vision: off for this model (set provider.vision: true if it reads images)"
    ),
    "term.check.ocr_off": "  ocr: off",
    "term.check.ocr_unavailable": "  ocr: unavailable",
    "term.check.ocr_cannot_run": (
        "OCR is on but cannot run here ({reason}); images and scanned "
        "PDFs are classified with vision if it is on, else from their name and metadata."
    ),
    "term.check.ocr_on": Plural(
        one="  ocr: on, on this Mac ({languages}; first {count} pages of a scanned PDF)",
        other="  ocr: on, on this Mac ({languages}; first {count} pages of a scanned PDF)",
    ),
    "term.check.ocr_languages": "ocr.languages: macOS cannot recognise {unknown}; it can: {known}",
    "term.check.semantic_off": "  semantic search: off",
    "term.check.semantic": "  semantic search: {model} at {url} ({where})",
    "term.check.semantic_remote": (
        "search.semantic.base_url is not this Mac: the names, categories, tags "
        "and descriptions in the index, and every semantic query, leave it. Embeddings are "
        "not priced; check your server's rates."
    ),
    "term.check.http_semantic": (
        "search.semantic.base_url uses http:// to another machine; the index "
        "text would travel unencrypted. Use https://."
    ),
    "term.check.embeddings_key": "  embeddings api key: {source} ({hint})",
    "term.check.claude_missing": "  claude: not found",
    "term.check.install_claude": (
        "install Claude Code, or set provider.command to the `claude` executable"
    ),
    "term.check.claude_unknown": "could not ask Claude Code whether it is logged in",
    "term.check.claude_ok": "  claude login: ok",
    "term.check.claude_logged_out": "Claude Code is not logged in; {hint}",
    "term.claude.login_hint": "run `claude` once and sign in with /login (or `claude auth login`)",
    "term.local.yes": (
        "fully local: yes (classification, text recognition and search run on this Mac)"
    ),
    "term.local.no": "fully local: no",
    "term.local.leaves": "  leaves this Mac: {what}, to {where}",
    "term.local.remote": "a remote server",
    "term.local.content": "file names, metadata and text excerpts",
    "term.local.content_pages": (
        "file names, metadata and text excerpts, and images and scanned pages"
    ),
    "term.local.content_images": "file names, metadata and text excerpts, and images",
    "term.local.to_classify": "{content} to classify",
    "term.local.api": "the Anthropic API",
    "term.local.claude_code": "Anthropic (Claude Code)",
    "term.local.index": "the index's names, tags and descriptions, and search queries",
    "term.input.patterns": Plural(one="{count} pattern(s)", other="{count} pattern(s)"),
    "term.ollama.down": (
        "{error}; is Ollama running? Start it with `ollama serve` or the Ollama app"
    ),
    "term.ollama.no_model": "Ollama has no model {model}; {hint}",
    "term.ollama.pull": "run `ollama pull {model}`",
    "term.ollama.ready": "Ollama at {url} has {model}",
    "term.ollama.install_hint": (
        "install Ollama with `brew install ollama` (then `brew services start ollama`) or the "
        "app from https://ollama.com/download, and open it"
    ),
    # flib reconcile
    "term.reconcile.learned": _en("learned", "{path}: {old} -> {new}"),
    "term.reconcile.found": _en("found", "{path}"),
    "term.reconcile.missing": _en("missing", "{path}"),
    "term.reconcile.icloud": _en("icloud", "{path}  ({why}, not checked)"),
    "term.reconcile.unread": _en("unread", "{path}  (cannot be read now, not checked)"),
    "term.reconcile.locked": _en("locked", "{path}  ({why}, not checked)"),
    "term.reconcile.orphan": _en(
        "orphan",
        "{path}  (its original {original} is missing; "
        "move this copy to an input folder to have it filed)",
    ),
    "term.reconcile.checked": Plural(one="{count} checked", other="{count} checked"),
    "term.reconcile.moved_by_hand": Plural(
        one="{count} moved by hand", other="{count} moved by hand"
    ),
    "term.reconcile.found_again": Plural(one="{count} found again", other="{count} found again"),
    "term.reconcile.missing_count": Plural(one="{count} missing", other="{count} missing"),
    "term.reconcile.icloud_count": Plural(
        one="{count} in iCloud only", other="{count} in iCloud only"
    ),
    "term.reconcile.orphans": Plural(
        one="{count} duplicate(s) of missing files", other="{count} duplicate(s) of missing files"
    ),
    "term.reconcile.locked_count": Plural(
        one="{count} could not be read while the Mac is locked; "
        "run `flib reconcile` again once it is unlocked.",
        other="{count} could not be read while the Mac is locked; "
        "run `flib reconcile` again once it is unlocked.",
    ),
    "term.reconcile.summary": "Reconcile: {checked}, {moved}, {found}, {missing}, {icloud}.",
    "term.reconcile.summary_orphans": (
        "Reconcile: {checked}, {moved}, {found}, {missing}, {icloud}, {orphans}."
    ),
    "term.reconcile.summary_locked": "{summary} {locked}",
    # flib reprocess: what it leaves and keeps (``reprocess``)
    "term.reprocess.still_configured": (
        "{name} is still a category in the configuration; "
        "rename or remove it there first, then run this again"
    ),
    "term.reprocess.no_files_in": "no organized file has the category {name}; nothing to migrate",
    "term.reprocess.correction_kept": "your correction is kept",
    "term.reprocess.rule_filed": "filed by its input's fixed category; no AI is asked about it",
    "term.reprocess.cloud_only": "only in iCloud; download it to reprocess it",
    "term.reprocess.not_there": "not at its recorded place; run `flib reconcile`",
    "term.reprocess.partly_cloud": (
        "something inside is only in iCloud; download it to reprocess it"
    ),
    "term.reprocess.changed": "changed or moved since it was indexed; run `flib reconcile`",
    "term.reprocess.left_as_is": "{why}; left as it is",
    "term.reprocess.stays_in": "stays in {category} ({why})",
    "term.reprocess.cost_limit": "cost limit reached; run `flib reprocess` again",
    "term.reprocess.category_kept": "{why}, your category kept",
    "term.reprocess.not_a_category": (
        "{name} is not a category in the configuration (configured: {known}); add it there first"
    ),
    "term.reprocess.renamed": "category {old} renamed to {new}",
    "term.reprocess.removed": "its category was removed",
    "term.reprocess.not_in_folder": "not in the {category} folder",
    "term.reprocess.by_hand": "you moved or renamed it; it stays (`flib fix` hands it back)",
    "term.reprocess.own_layout": "filed under its input's own layout; it stays",
    "term.reprocess.issuer_unknown_ask": (
        "issuer unknown; `--ask-issuer` asks the provider (it may cost)"
    ),
    "term.reprocess.no_issuer": "the provider named no issuer",
    "term.reprocess.never_sent": "sensitive: never sent",
    "term.reprocess.it_stays": "{why}; it stays",
    "term.reprocess.cost_limit_again": "cost limit reached; run it again",
    "term.reprocess.issuer_unknown": "issuer unknown; it stays",
    "term.reprocess.layout_of": "layout of {category}: {where}",
    "term.reprocess.nothing_filled": "{why}; nothing filled",
    "term.reprocess.no_fields": "the document states none of the fields",
    # flib reprocess: the command
    "term.reprocess.ask_issuer_alone": "--ask-issuer goes with --apply-layout",
    "term.reprocess.fill_fields_alone": (
        "--fill-fields only records fields in the index; it takes --category, --ext, "
        "--since, --until, --simulate and --yes"
    ),
    "term.reprocess.apply_layout_alone": (
        "--apply-layout only moves files into their layout; it takes --category, "
        "--ask-issuer, --simulate and --yes, and no category migration"
    ),
    "term.reprocess.scrub_alone": (
        "--scrub-descriptions takes the whole index; leave out the other options"
    ),
    "term.reprocess.rename_or_remove": (
        "use either --rename-category or --remove-category, not both"
    ),
    "term.reprocess.migration_filters": (
        "a category migration takes the whole category; leave out the filters"
    ),
    "term.reprocess.migration_rename": (
        "a category migration keeps file names; leave out --rename"
    ),
    "term.reprocess.to_review_alone": "--to-review goes with --remove-category",
    "term.row.scrub": _en("scrub", "{path}  ({fields})"),
    "term.row.fill": _en("fill", "{path}  ({fields})"),
    "term.row.leave": _en("leave", "{path}  ({why})"),
    "term.row.rename": _en(
        "rename", "category {old} -> {new} in the index (the same folder on macOS)"
    ),
    "term.reprocess.scrub_simulation": Plural(
        one="Simulation: {count} file(s) to scrub; nothing changed.",
        other="Simulation: {count} file(s) to scrub; nothing changed.",
    ),
    "term.reprocess.scrub_question": Plural(
        one="Scrub {count} file(s) in the index? `flib undo` reverts it",
        other="Scrub {count} file(s) in the index? `flib undo` reverts it",
    ),
    "term.reprocess.nothing_changed": "Nothing changed.",
    "term.reprocess.scrub_cached": Plural(
        one="{count} cached answer(s) scrubbed.", other="{count} cached answer(s) scrubbed."
    ),
    "term.reprocess.nothing_to_scrub": (
        "Nothing to scrub: no stored description holds an identifier flib recognizes."
    ),
    "term.reprocess.scrub_files": Plural(one="{count} file(s)", other="{count} file(s)"),
    "term.reprocess.scrub_values": Plural(
        one="{count} value(s) changed", other="{count} value(s) changed"
    ),
    "term.reprocess.scrub_embeddings": Plural(
        one="{count} embedding(s) removed; the next run makes them again.",
        other="{count} embedding(s) removed; the next run makes them again.",
    ),
    "term.reprocess.scrub_done": "Scrub run {run}: {files}, {values}. `flib undo` reverts it.",
    "term.reprocess.scrub_done_embeddings": (
        "Scrub run {run}: {files}, {values}. `flib undo` reverts it. {embeddings}"
    ),
    "term.reprocess.all_have_fields": "Every file in the library already has its fields.",
    "term.reprocess.cached": Plural(
        one="{count} answered by the cache", other="{count} answered by the cache"
    ),
    "term.reprocess.to_ask": Plural(
        one="{count} to send to {provider}", other="{count} to send to {provider}"
    ),
    "term.reprocess.fields_estimate": Plural(
        one="{count} file(s) without fields: {cached}, {to_ask} (estimated {cost}).",
        other="{count} file(s) without fields: {cached}, {to_ask} (estimated {cost}).",
    ),
    "term.reprocess.send_question": Plural(
        one="Send {count} file(s) to {provider}?", other="Send {count} file(s) to {provider}?"
    ),
    "term.reprocess.nothing_sent": "Nothing sent, nothing changed.",
    "term.reprocess.simulation_nothing_changed": "Simulation: nothing changed.",
    "term.reprocess.nothing_to_record": "Nothing to record.",
    "term.reprocess.fields_question": Plural(
        one="Record the fields of {count} file(s) in the index? `flib undo` reverts it",
        other="Record the fields of {count} file(s) in the index? `flib undo` reverts it",
    ),
    "term.reprocess.issuer_estimate": Plural(
        one="{count} file(s) need their issuer: {cached}, {to_ask} (estimated {cost}).",
        other="{count} file(s) need their issuer: {cached}, {to_ask} (estimated {cost}).",
    ),
    "term.reprocess.nothing_sent_issuer": (
        "Nothing sent; files without an issuer stay where they are."
    ),
    "term.reprocess.in_layout": Plural(
        one="{count} file(s) already follow their category's layout.",
        other="{count} file(s) already follow their category's layout.",
    ),
    "term.reprocess.simulation_nothing_moved": "Simulation: nothing moved or changed.",
    "term.reprocess.layout_nothing": "Nothing to move: the library already follows its layouts.",
    "term.reprocess.layout_question": Plural(
        one="Move {count} file(s) into their layout? `flib undo` reverts it",
        other="Move {count} file(s) into their layout? `flib undo` reverts it",
    ),
    "term.reprocess.layout_record_question": Plural(
        one="Record in the index what {count} file(s) are filed by? Nothing moves",
        other="Record in the index what {count} file(s) are filed by? Nothing moves",
    ),
    "term.reprocess.rules_nothing": "Nothing to move: the library already follows the rules.",
    "term.reprocess.move_question": Plural(
        one="Move {count} file(s)? `flib undo` reverts it",
        other="Move {count} file(s)? `flib undo` reverts it",
    ),
    "term.reprocess.rename_question": "Rename the category in the index? `flib undo` reverts it",
    "term.reprocess.nothing": "Nothing to reprocess.",
    "term.reprocess.estimate": Plural(
        one="{count} file(s) to reclassify: {cached}, {to_ask} (estimated {cost}).",
        other="{count} file(s) to reclassify: {cached}, {to_ask} (estimated {cost}).",
    ),
    "term.reprocess.estimate_cap": Plural(
        one="{count} file(s) to reclassify: {cached}, {to_ask} "
        "(estimated {cost}; the limit per run is {cap}).",
        other="{count} file(s) to reclassify: {cached}, {to_ask} "
        "(estimated {cost}; the limit per run is {cap}).",
    ),
    "term.reprocess.plan": Plural(
        one="Plan: {count} would move, {unchanged}, {kept}.",
        other="Plan: {count} would move, {unchanged}, {kept}.",
    ),
    "term.reprocess.in_place": Plural(
        one="{count} already in place", other="{count} already in place"
    ),
    "term.reprocess.kept": Plural(one="{count} kept as they are", other="{count} kept as they are"),
    "term.reprocess.deferred": Plural(
        one="{count} file(s) were not sent: the cost limit was reached. "
        "Run `flib reprocess` again to go on.",
        other="{count} file(s) were not sent: the cost limit was reached. "
        "Run `flib reprocess` again to go on.",
    ),
    "term.reprocess.filed": Plural(one="{count} filed", other="{count} filed"),
    "term.reprocess.index_changes": Plural(
        one="{count} index change(s)", other="{count} index change(s)"
    ),
    "term.reprocess.undoable": "`flib undo` reverts it.",
    "term.reprocess.unchanged": "Nothing changed.",
    "term.reprocess.summary": "Reprocess run {run}: {filed}, {review}, {failed}, {skipped}. {end}",
    "term.reprocess.summary_changes": (
        "Reprocess run {run}: {filed}, {review}, {failed}, {skipped}, {changes}. {end}"
    ),
    "term.reprocess.empty_folder": (
        "The folder {folder} is empty now. Keep it until you are sure of "
        "the change; `flib undo` moves the files back into it."
    ),
    "term.column.description": "description",
    "term.column.tags": "tags",
    "term.column.kind": "kind",
    "term.column.issuer": "issuer",
    "term.column.amount": "amount",
    "term.column.currency": "currency",
    "term.column.document_date": "document date",
    "term.column.due_date": "due date",
    "term.column.period": "period",
    "term.column.name_choice_reason": "name choice reason",
    "term.column.layout_date": "layout date",
    # flib init
    "term.yes_no.yes": "y",
    "term.yes_no.no": "n",
    "term.init.welcome": (
        "Setting up file-librarian. Press Enter to take the suggestion in brackets."
    ),
    "term.init.replace": "A configuration already exists at {path}. Replace it?",
    "term.init.keeping": "Keeping {path}.",
    "term.init.wrote": "Wrote {path} (only you can read it). `flib check-config` validates it.",
    "term.init.no_trial": "Skipping the trial. `flib simulate` shows the plan once flib can run.",
    "term.init.local_quality": (
        "What to expect, measured on flib's test corpus (docs/quality.md): {model} files "
        "92% (English) and 97% (Portuguese) of items in a right category against 99% and 100% "
        "for the hosted default, and sends 7 of 9 doubtful items to review against 9 of 9, so "
        "check the review folder and `flib simulate` more often at first."
    ),
    "term.init.next": (
        "Next:\n"
        "  flib simulate   the plan for your real folders; nothing moves\n"
        "  flib run        organize now; `flib undo` reverts it\n"
        "  flib status     what waits for you in review or failed, and the last and next run\n"
        "Optional: let Claude search your library through the MCP server; enable it with "
        "`./scripts/install.sh --with-mcp` from the file-librarian folder."
    ),
    "term.init.language": "Language of names, descriptions and folders",
    "term.init.choose_one": "choose one of {options}.",
    "term.init.folders_again": "Let's try the folders again.",
    "term.init.other_provider": "Choose another provider for now.",
    "term.init.vision_note": (
        "Vision lets the model look at photos, screenshots and scanned PDFs. What is sent: "
        "the image shrunk to 1568 px with its EXIF and GPS metadata removed, or the first 3 "
        "pages of a PDF with no text. About US$0.0005 per image and US$0.001 per scanned PDF "
        "on the default model. Off, they are classified from their name and metadata."
    ),
    "term.init.vision_question": "Send images and scanned PDFs to the model?",
    "term.init.no_vision": "Images and scanned PDFs are classified from their name and metadata.",
    "term.init.no_ollama": (
        "Ollama is not installed: it runs the local model. flib does not install it "
        "for you; {hint}. Then run `flib init --local` again."
    ),
    "term.init.ollama_stopped": (
        "Ollama is installed ({command}) but not running. Start it with "
        "`ollama serve`, `brew services start ollama` or the Ollama app, then run "
        "`flib init --local` again."
    ),
    "term.init.ollama_running": (
        "Ollama is running. The local setup uses {model} to classify and "
        "{embedding} for semantic search."
    ),
    "term.init.models_there": "Both models are already downloaded.",
    "term.init.model_size": "{model} ({size} GB)",
    "term.init.not_downloaded": "Not downloaded yet: {sizes}.",
    "term.init.download_question": "Download them now ({size} GB)?",
    "term.init.download_later": "Download them later with: {pulls}",
    "term.init.pull_failed": "{error}; try `ollama pull {model}`.",
    "term.init.not_adding": "Not adding {what}: {error}",
    "term.init.the_desktop": "the Desktop",
    "term.init.the_shots": "the screenshots folder",
    "term.init.desktop": (
        "flib can also tidy the Desktop. People keep work in progress there, so it would "
        "take only documents and images that have not changed for "
        "{days} days, and leave folders alone. macOS asks once "
        "whether flib may read the Desktop."
    ),
    "term.init.desktop_question": "Also tidy the Desktop?",
    "term.init.shots_shared": (
        "Screenshots are saved to {where}, which holds other files too, so flib does not "
        "offer to file it by a fixed category. To have screenshots filed on their own, "
        "choose a folder just for them in the Screenshot app (Cmd+Shift+5 > Options > "
        "Other Location), then run `flib init` again or add it to `inputs` "
        "(see config.example.yaml)."
    ),
    "term.init.shots": Plural(
        one="Screenshots are saved in {where} ({count} item(s) there look like screenshots or "
        "screen recordings now). flib can file those into {category}/<year>/<month> without "
        "asking the AI (no cost), keeping their names; anything else there, and folders, "
        "are left alone.",
        other="Screenshots are saved in {where} ({count} item(s) there look like screenshots or "
        "screen recordings now). flib can file those into {category}/<year>/<month> without "
        "asking the AI (no cost), keeping their names; anything else there, and folders, "
        "are left alone.",
    ),
    "term.init.shots_none": (
        "Nothing there has a name flib knows for a screenshot (in English, Portuguese, "
        "Spanish, French, German, Italian or Japanese), so it may never file anything "
        "from it; the answer you probably want is no."
    ),
    "term.init.shots_question": "File screenshots too?",
    "term.init.folder": "Folder to organize",
    "term.init.not_a_folder_yes": (
        "{folder} is not a folder; run `flib init` without --yes to pick another"
    ),
    "term.init.not_a_folder": "{folder} is not a folder.",
    "term.init.output": "Where organized files go",
    "term.init.review": "Folder in it for files to review",
    "term.init.claude_not_found": "not found",
    "term.init.claude_logged_in": "found, logged in",
    "term.init.claude_logged_out": "found, not logged in",
    "term.init.option.claude_code": (
        "  claude-code    your Claude subscription through Claude Code ({state}); no API key"
    ),
    "term.init.option.anthropic": (
        "  anthropic      the Anthropic API with an API key, billed per use "
        "(a fraction of a cent per file)"
    ),
    "term.init.option.ollama": Plural(
        one="  ollama         a local model through Ollama ({count} pulled); "
        "nothing leaves this Mac",
        other="  ollama         a local model through Ollama ({count} pulled); "
        "nothing leaves this Mac",
    ),
    "term.init.option.openai_compat": (
        "  openai-compat  any OpenAI-compatible API, with its URL and an optional key"
    ),
    "term.init.option.local": (
        "  local          nothing leaves this Mac: {model} in Ollama, local "
        "search; slower and less accurate (needs Ollama, ~10 GB, 16 GB of memory)"
    ),
    "term.init.which_ai": "Which AI classifies your files?",
    "term.init.provider": "Provider",
    "term.init.ollama_model": "Ollama model",
    "term.init.base_url": "API base URL (the part before /chat/completions)",
    "term.init.model": "Model",
    "term.init.key_found": "API key: found in the {source} ({hint}).",
    "term.init.store_key": "No Anthropic API key found. Store one in the macOS Keychain now?",
    "term.init.no_key_yet": "no API key yet; run `flib key set` to add one.",
    "term.init.store_server_key": (
        "No API key found for this server (a local one usually needs none). "
        "Store one in the macOS Keychain now?"
    ),
    "term.init.sentence": "{text}.",
    "term.init.claude_missing": "Claude Code was not found; install it or set provider.command.",
    "term.init.claude_logged_out_warning": "Claude Code is not logged in; {hint}.",
    "term.init.not_available": "provider {provider} is not available yet.",
    "term.init.nothing_to_try": (
        "No files to try it on in {where}: it is empty, or holds only hidden, sensitive "
        "or large items. `flib simulate` shows the plan once files arrive."
    ),
    "term.init.try_question": Plural(
        one="Try it on copies of your {count} most recent item(s)? They are copied to a "
        "temporary folder and classified with {provider}; nothing in {where} moves.",
        other="Try it on copies of your {count} most recent item(s)? They are copied to a "
        "temporary folder and classified with {provider}; nothing in {where} moves.",
    ),
    "term.init.classifying": Plural(
        one="Classifying {count} copies; this can take a minute...",
        other="Classifying {count} copies; this can take a minute...",
    ),
    "term.init.trial_stopped": "the trial stopped: {error}",
    "term.init.trial": Plural(
        one="Trial on {count} copies (destinations are inside {root}):",
        other="Trial on {count} copies (destinations are inside {root}):",
    ),
    "term.row.trial_review": _en("review", "{name}  ->  {destination}"),
    "term.row.trial_dup": _en("dup", "{name}  ->  {destination}"),
    "term.row.trial_move": _en("move", "{name}  ->  {destination}"),
    "term.init.trial_summary": "Summary: {organized}, {review}, {stay}.",
    "term.init.to_categories": Plural(one="{count} to categories", other="{count} to categories"),
    "term.init.to_review": Plural(
        one="{count} to {folder} for you to decide", other="{count} to {folder} for you to decide"
    ),
    "term.init.not_tried": Plural(
        one="  {count} not tried: the cost cap was reached.",
        other="  {count} not tried: the cost cap was reached.",
    ),
    "term.init.trial_cost": (
        "Estimated cost of the trial: {cost}. Nothing in your folders "
        "moved; the copies and the trial's index are already deleted."
    ),
    "term.init.schedule_unreadable": "cannot read the schedule: {error}",
    "term.init.watch_already": "Watch mode is already on; `flib schedule` changes it.",
    "term.init.both_already": (
        "A daily run is already scheduled at {time}, with watch mode; `flib schedule` changes it."
    ),
    "term.init.daily_already": (
        "A daily run is already scheduled at {time}; `flib schedule` changes it."
    ),
    "term.init.schedule_question": "Organize new files automatically every day at {time}?",
    "term.init.schedule_later": (
        "You can schedule it later with `flib schedule` (--at HH:MM for another time)."
    ),
    "term.init.watch_explained": (
        "Watch mode organizes files a few minutes after they arrive (once downloads "
        "finish) instead of once a day; both keeps the daily run as a safety net."
    ),
    "term.init.mode_question": "Run daily, on arrival (watch), or both?",
    "term.init.set_mode": (
        "Set schedule.mode: {mode} in {path} so `flib schedule` keeps this choice."
    ),
    "term.init.not_scheduled": "could not schedule the daily run: {error}",
    "term.init.menu_already": (
        "flib is already in the menu bar; `flib menu --remove` takes it away."
    ),
    "term.init.menu_question": (
        "Show flib in the menu bar (what needs you, and a menu that opens flib)?"
    ),
    "term.init.menu_later": "You can add it later with `flib menu`.",
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
    "term.lock.ui": "o flib ui já está rodando (pid {pid})",
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
    # por que um arquivo é sensível
    "term.sensitive.name": "o nome parece de um arquivo de credenciais",
    "term.sensitive.name_words": "o nome parece de um arquivo de credenciais: {words}",
    "term.sensitive.holds": "conteúdo: contém um arquivo com nome de arquivo de credenciais",
    "term.sensitive.holds_words": "conteúdo: contém um arquivo com nome como {words}",
    "term.sensitive.secrets": "conteúdo: parece ter {kinds}",
    "term.secret.private_key": "chave privada",
    "term.secret.aws_access_key": "chave de acesso da AWS",
    "term.secret.github_token": "token do GitHub",
    "term.secret.gitlab_token": "token do GitLab",
    "term.secret.slack_token": "token do Slack",
    "term.secret.stripe_key": "chave do Stripe",
    "term.secret.google_api_key": "chave de API do Google",
    "term.secret.google_oauth_client_secret": "segredo de cliente OAuth do Google",
    "term.secret.anthropic_api_key": "chave de API da Anthropic",
    "term.secret.openai_api_key": "chave de API da OpenAI",
    "term.secret.json_web_token": "JSON web token (JWT)",
    "term.secret.connection_string_with_password": "string de conexão com senha",
    "term.secret.password_or_secret_assignment": "senha ou segredo atribuído",
    "term.secret.password_or_secret_in_json": "senha ou segredo em JSON",
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
        "informe palavras para buscar, um filtro ou os dois. Filtros: --category, --ext, "
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
    "term.search.sum_words": "--sum busca só pelas palavras, não pelo significado",
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
    "term.fix.nothing": "informe o que mudar: uma categoria, um nome ou os dois",
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
    "term.reason.layout_not_used_alone": "layout não usado ({why})",
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
    "term.reason.cannot_plan": "não foi possível planejar: {why}",
    "term.reason.cannot_plan_kind": "não foi possível planejar",
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
    "term.schedule.on_wake": (
        "Se o Mac estiver em repouso nesse horário, ela roda quando ele despertar."
    ),
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
    "term.access.checking": "Verificando se a execução agendada consegue ler as pastas...",
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
    "term.menu.at_login": (
        "Ele abre quando você inicia a sessão ({plist}). `flib menu --remove` o tira de lá."
    ),
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
    # as recusas da configuração
    "term.config.invalid": "configuração inválida:",
    "term.config.problem": "  {where}: {why}",
    "term.config.not_mapping": "a configuração precisa ser um mapeamento YAML (chave: valor)",
    "term.config.missing": (
        "não há configuração em {path}; rode `flib init`, ou copie o config.example.yaml para lá"
    ),
    "term.config.unreadable": "não foi possível ler {path}: {error}",
    "term.config.not_yaml": "{path} não é um YAML válido: {error}",
    "term.config.spaces": "não pode começar nem terminar com espaços",
    "term.config.plain_folder": "precisa ser um nome de pasta simples (sem '/', ':' nem '\\')",
    "term.config.control_or_invisible": "não pode ter caracteres de controle ou invisíveis",
    "term.config.leading_dot": "não pode começar com '.'",
    "term.config.absolute": "precisa ser um caminho absoluto ou começar com '~': {value}",
    "term.config.url_invalid": "{option} não é uma URL válida: {error}",
    "term.config.url_scheme": "{option} precisa ser uma URL http:// ou https:// com um host",
    "term.config.url_credentials": (
        "{option} não pode ter usuário nem senha; "
        "a chave de API vai no Keychain ou numa variável de ambiente"
    ),
    "term.config.url_query": "{option} não pode ter consulta (?) nem fragmento (#)",
    "term.config.base_url_required": (
        "o openai-compat precisa de provider.base_url (por exemplo https://api.openai.com/v1)"
    ),
    "term.config.env_name": "{option} precisa ser o nome de uma variável de ambiente",
    "term.config.unknown_provider": "provedor desconhecido {value} (existentes: {known})",
    "term.config.pattern_invalid": "naming.pattern não é um padrão válido: {error}",
    "term.config.pattern_unknown": (
        "naming.pattern: marcador(es) desconhecido(s) {unknown}; use {allowed}"
    ),
    "term.config.pattern_subject": "naming.pattern precisa conter {name}",
    "term.config.pattern_spec": (
        "os marcadores de naming.pattern não aceitam especificação de formato nem conversão"
    ),
    "term.config.pattern_path": "naming.pattern precisa gerar um nome de arquivo, não um caminho",
    "term.config.pattern_control": "naming.pattern não pode ter caracteres de controle",
    "term.config.layout_depth": Plural(
        zero="o layout não pode ter subpastas",
        one="o layout pode ter no máximo {levels} nível de subpastas",
        other="o layout pode ter no máximo {levels} níveis de subpastas",
        by="levels",
    ),
    "term.config.layout_slashes": (
        "o layout não pode começar nem terminar com '/' nem conter '//'"
    ),
    "term.config.layout_invalid": "o layout não é um padrão válido: {error}",
    "term.config.layout_unknown": (
        "layout: marcador(es) desconhecido(s) em {segment}; use {allowed}"
    ),
    "term.config.layout_spec": (
        "os marcadores do layout não aceitam especificação de formato nem conversão"
    ),
    "term.config.layout_issuer": (
        "layout: {name} precisa ser uma subpasta inteira, sozinho entre '/'"
    ),
    "term.config.layout_separators": (
        "layout: as subpastas são separadas só por '/' (sem ':' nem '\\')"
    ),
    "term.config.layout_dots": "layout: {segment} não é um nome de pasta",
    "term.config.layout_folder": "layout: a subpasta {segment} {why}",
    "term.config.layout_long": "layout: a subpasta {segment} tem mais de 64 caracteres",
    "term.config.layout_none": (
        "o layout precisa de pelo menos um marcador: {first}, {second} ou {third}"
    ),
    "term.config.glob_empty": "um padrão não pode ficar vazio",
    "term.config.glob_path": "{value}: os padrões comparam um nome, não um caminho (sem '/')",
    "term.config.input_option": "entrada {path}: {option} só vale com uma categoria fixa",
    "term.config.ocr_language": "{value} não é um código de idioma como en-US ou pt-BR",
    "term.config.time_form": "schedule.time precisa ser como 12:30",
    "term.config.time_invalid": "schedule.time precisa ser um horário válido de 24 horas",
    "term.config.done_tag": "due.done_tag precisa ser um nome de tag do Finder",
    "term.config.same_folder.category": (
        "a categoria {name} é a mesma pasta que {other} no macOS "
        "(os nomes são comparados ignorando maiúsculas e a forma Unicode)"
    ),
    "term.config.same_folder.review_folder": (
        "review_folder {name} é a mesma pasta que {other} no macOS "
        "(os nomes são comparados ignorando maiúsculas e a forma Unicode)"
    ),
    "term.config.same_folder.duplicates_folder": (
        "duplicates_folder {name} é a mesma pasta que {other} no macOS "
        "(os nomes são comparados ignorando maiúsculas e a forma Unicode)"
    ),
    "term.config.input_category": (
        "entrada {path}: a categoria {category} não está configurada (configuradas: {known})"
    ),
    "term.config.input_broad": "a entrada {folder} é ampla demais; escolha pastas específicas",
    "term.config.input_in_output": (
        "a entrada {folder} não pode ficar dentro de output_root ({output}): "
        "o flib pegaria de novo os arquivos que acabou de organizar"
    ),
    "term.config.input_twice": "a entrada {path} aparece duas vezes (também como {other})",
    "term.config.inputs_overlap": (
        "as entradas não podem se sobrepor: {inner} está dentro de {outer}; deixe só uma delas"
    ),
    "term.config.database_inside": (
        "database precisa ficar fora das pastas de entrada e de output_root"
    ),
    "term.config.p.missing": "campo obrigatório",
    "term.config.p.extra": "esta chave não existe na configuração",
    "term.config.p.int": "precisa ser um número inteiro",
    "term.config.p.int_fraction": "precisa ser um número inteiro, sem casas decimais",
    "term.config.p.number": "precisa ser um número",
    "term.config.p.bool": "precisa ser true ou false",
    "term.config.p.string": "precisa ser um texto",
    "term.config.p.gt": "precisa ser maior que {gt}",
    "term.config.p.ge": "precisa ser pelo menos {ge}",
    "term.config.p.lt": "precisa ser menor que {lt}",
    "term.config.p.le": "pode ser no máximo {le}",
    "term.config.p.string_short": Plural(
        zero="pode ficar vazio",
        one="precisa ter pelo menos {min_length} caractere",
        other="precisa ter pelo menos {min_length} caracteres",
        by="min_length",
    ),
    "term.config.p.string_long": Plural(
        zero="precisa ficar vazio",
        one="pode ter no máximo {max_length} caractere",
        other="pode ter no máximo {max_length} caracteres",
        by="max_length",
    ),
    "term.config.p.too_short": Plural(
        zero="{field_type} pode ficar vazia (tem {actual_length})",
        one="{field_type} precisa ter pelo menos {min_length} item, não {actual_length}",
        other="{field_type} precisa ter pelo menos {min_length} itens, não {actual_length}",
        by="min_length",
    ),
    "term.config.p.too_long": Plural(
        zero="{field_type} precisa ficar vazia (tem {actual_length})",
        one="{field_type} pode ter no máximo {max_length} item, não {actual_length}",
        other="{field_type} pode ter no máximo {max_length} itens, não {actual_length}",
        by="max_length",
    ),
    "term.config.p.literal": "precisa ser {expected}",
    "term.config.p.list": "precisa ser uma lista",
    "term.config.p.dict": "precisa ser um mapeamento (chave: valor)",
    # flib check-config
    "term.check.key": "  chave de API: {source} ({hint})",
    "term.check.key_missing": (
        "  chave de API: não encontrada (rode `flib key set`, ou defina ANTHROPIC_API_KEY)"
    ),
    "term.check.key_none": (
        "  chave de API: nenhuma (tudo bem para um servidor que não precisa de chave; senão "
        "rode `flib key set --provider {provider}`, ou defina {env})"
    ),
    "term.check.fixed_no_only": (
        "a entrada {path} tem uma categoria fixa e nenhum `only`: tudo nesta pasta é "
        "organizado como {category} sem ser examinado."
    ),
    "term.check.env_file_open": "{path} pode ser lido por outros usuários; rode chmod 600 nele",
    "term.check.later_milestone": "uma etapa futura",
    "term.check.planned_provider": (
        "o provedor {provider} está previsto para {when}; os comandos de classificação não "
        "rodam até lá."
    ),
    "term.check.inactive_option": (
        "{option} está previsto para {when} e ainda não faz nada; seu valor é aceito, mas ignorado."
    ),
    "term.check.compat_only": (
        "provider.{option} só vale para openai-compat e ollama; {provider} o ignora."
    ),
    "term.check.this_mac_no_limit": "este Mac; sem limite de taxa, never_send continua valendo",
    "term.check.base_url": "  base_url: {url} ({where})",
    "term.check.http_provider": (
        "provider.base_url usa http:// para outra máquina; o conteúdo dos arquivos viajaria "
        "sem criptografia. Use https://."
    ),
    "term.check.ollama_remote": (
        "provider.base_url não é este Mac: o conteúdo dos arquivos sai dele, e o Ollama é "
        "tratado como um provedor remoto."
    ),
    "term.check.no_prices": (
        "provider.price_input_per_mtok e price_output_per_mtok não estão definidos, então o "
        "custo de cada arquivo conta como 0 e limits.max_cost_usd_per_run não consegue parar "
        "uma execução. Defina os preços do seu provedor."
    ),
    "term.check.vision_off": (
        "  vision: desligada para este modelo (defina provider.vision: true se ele lê imagens)"
    ),
    "term.check.ocr_off": "  ocr: desligado",
    "term.check.ocr_unavailable": "  ocr: indisponível",
    "term.check.ocr_cannot_run": (
        "o OCR está ligado, mas não consegue rodar aqui ({reason}); imagens e PDFs escaneados "
        "são classificados com vision se ela estiver ligada, senão pelo nome e pelos metadados."
    ),
    "term.check.ocr_on": Plural(
        zero="  ocr: ligado, neste Mac ({languages}; nenhuma página de um PDF escaneado)",
        one="  ocr: ligado, neste Mac ({languages}; a primeira página de um PDF escaneado)",
        other="  ocr: ligado, neste Mac ({languages}; as {count} primeiras páginas de um PDF "
        "escaneado)",
    ),
    "term.check.ocr_languages": (
        "ocr.languages: o macOS não reconhece {unknown}; ele reconhece: {known}"
    ),
    "term.check.semantic_off": "  busca por significado: desligada",
    "term.check.semantic": "  busca por significado: {model} em {url} ({where})",
    "term.check.semantic_remote": (
        "search.semantic.base_url não é este Mac: os nomes, categorias, tags e descrições do "
        "índice, e cada busca por significado, saem dele. Os embeddings não têm preço; confira "
        "as tarifas do seu servidor."
    ),
    "term.check.http_semantic": (
        "search.semantic.base_url usa http:// para outra máquina; o texto do índice viajaria "
        "sem criptografia. Use https://."
    ),
    "term.check.embeddings_key": "  chave de API dos embeddings: {source} ({hint})",
    "term.check.claude_missing": "  claude: não encontrado",
    "term.check.install_claude": (
        "instale o Claude Code, ou defina provider.command com o executável `claude`"
    ),
    "term.check.claude_unknown": "não foi possível perguntar ao Claude Code se ele está logado",
    "term.check.claude_ok": "  login do claude: ok",
    "term.check.claude_logged_out": "o Claude Code não está logado; {hint}",
    "term.claude.login_hint": "rode `claude` uma vez e entre com /login (ou `claude auth login`)",
    "term.local.yes": (
        "totalmente local: sim (classificação, reconhecimento de texto e busca rodam neste Mac)"
    ),
    "term.local.no": "totalmente local: não",
    "term.local.leaves": "  sai deste Mac: {what}, para {where}",
    "term.local.remote": "um servidor remoto",
    "term.local.content": "nomes de arquivo, metadados e trechos de texto",
    "term.local.content_pages": (
        "nomes de arquivo, metadados e trechos de texto, e imagens e páginas escaneadas"
    ),
    "term.local.content_images": "nomes de arquivo, metadados e trechos de texto, e imagens",
    "term.local.to_classify": "{content} para classificar",
    "term.local.api": "a API da Anthropic",
    "term.local.claude_code": "Anthropic (Claude Code)",
    "term.local.index": "os nomes, tags e descrições do índice, e as consultas de busca",
    "term.input.patterns": Plural(
        zero="nenhum padrão", one="{count} padrão", other="{count} padrões"
    ),
    "term.ollama.down": (
        "{error}; o Ollama está rodando? Inicie com `ollama serve` ou com o app do Ollama"
    ),
    "term.ollama.no_model": "o Ollama não tem o modelo {model}; {hint}",
    "term.ollama.pull": "rode `ollama pull {model}`",
    "term.ollama.ready": "o Ollama em {url} tem {model}",
    "term.ollama.install_hint": (
        "instale o Ollama com `brew install ollama` (depois `brew services start ollama`) ou o "
        "app de https://ollama.com/download, e abra-o"
    ),
    # flib reconcile
    "term.reconcile.learned": _pt("aprendido", "{path}: {old} -> {new}"),
    "term.reconcile.found": _pt("encontrado", "{path}"),
    "term.reconcile.missing": _pt("ausente", "{path}"),
    "term.reconcile.icloud": _pt("icloud", "{path}  ({why}, não verificado)"),
    "term.reconcile.unread": _pt("ilegível", "{path}  (não dá para ler agora, não verificado)"),
    "term.reconcile.locked": _pt("bloqueado", "{path}  ({why}, não verificado)"),
    "term.reconcile.orphan": _pt(
        "órfão",
        "{path}  (o original dele, {original}, sumiu; "
        "mova esta cópia para uma pasta de entrada para que seja organizada)",
    ),
    "term.reconcile.checked": Plural(
        zero="nenhum verificado", one="{count} verificado", other="{count} verificados"
    ),
    "term.reconcile.moved_by_hand": Plural(
        zero="nenhum movido à mão", one="{count} movido à mão", other="{count} movidos à mão"
    ),
    "term.reconcile.found_again": Plural(
        zero="nenhum reencontrado", one="{count} reencontrado", other="{count} reencontrados"
    ),
    "term.reconcile.missing_count": Plural(
        zero="nenhum ausente", one="{count} ausente", other="{count} ausentes"
    ),
    "term.reconcile.icloud_count": Plural(
        zero="nenhum só no iCloud", one="{count} só no iCloud", other="{count} só no iCloud"
    ),
    "term.reconcile.orphans": Plural(
        zero="nenhuma cópia de arquivo ausente",
        one="{count} cópia de arquivo ausente",
        other="{count} cópias de arquivos ausentes",
    ),
    "term.reconcile.locked_count": Plural(
        zero="Nenhum ficou bloqueado.",
        one="{count} não pôde ser lido com o Mac bloqueado; rode `flib reconcile` de novo "
        "depois de desbloqueá-lo.",
        other="{count} não puderam ser lidos com o Mac bloqueado; rode `flib reconcile` de novo "
        "depois de desbloqueá-lo.",
    ),
    "term.reconcile.summary": "Reconciliação: {checked}, {moved}, {found}, {missing}, {icloud}.",
    "term.reconcile.summary_orphans": (
        "Reconciliação: {checked}, {moved}, {found}, {missing}, {icloud}, {orphans}."
    ),
    "term.reconcile.summary_locked": "{summary} {locked}",
    # flib reprocess: o que fica de fora
    "term.reprocess.still_configured": (
        "{name} ainda é uma categoria da configuração; renomeie ou remova lá primeiro, "
        "depois rode isto de novo"
    ),
    "term.reprocess.no_files_in": (
        "nenhum arquivo organizado tem a categoria {name}; nada para migrar"
    ),
    "term.reprocess.correction_kept": "sua correção é mantida",
    "term.reprocess.rule_filed": (
        "organizado pela categoria fixa da entrada; a IA não é consultada sobre ele"
    ),
    "term.reprocess.cloud_only": "só no iCloud; baixe-o para reclassificá-lo",
    "term.reprocess.not_there": "não está no lugar registrado; rode `flib reconcile`",
    "term.reprocess.partly_cloud": (
        "algo dentro dele está só no iCloud; baixe-o para reclassificá-lo"
    ),
    "term.reprocess.changed": "mudou ou foi movido desde que foi indexado; rode `flib reconcile`",
    "term.reprocess.left_as_is": "{why}; fica como está",
    "term.reprocess.stays_in": "fica em {category} ({why})",
    "term.reprocess.cost_limit": "limite de custo atingido; rode `flib reprocess` de novo",
    "term.reprocess.category_kept": "{why}, sua categoria mantida",
    "term.reprocess.not_a_category": (
        "{name} não é uma categoria da configuração (configuradas: {known}); adicione lá primeiro"
    ),
    "term.reprocess.renamed": "categoria {old} renomeada para {new}",
    "term.reprocess.removed": "a categoria dele foi removida",
    "term.reprocess.not_in_folder": "não está na pasta {category}",
    "term.reprocess.by_hand": "você o moveu ou renomeou; ele fica (`flib fix` o devolve ao flib)",
    "term.reprocess.own_layout": "organizado pelo layout da própria entrada; ele fica",
    "term.reprocess.issuer_unknown_ask": (
        "emissor desconhecido; `--ask-issuer` pergunta ao provedor (pode ter custo)"
    ),
    "term.reprocess.no_issuer": "o provedor não indicou o emissor",
    "term.reprocess.never_sent": "sensível: nunca enviado",
    "term.reprocess.it_stays": "{why}; ele fica",
    "term.reprocess.cost_limit_again": "limite de custo atingido; rode de novo",
    "term.reprocess.issuer_unknown": "emissor desconhecido; ele fica",
    "term.reprocess.layout_of": "layout de {category}: {where}",
    "term.reprocess.nothing_filled": "{why}; nada preenchido",
    "term.reprocess.no_fields": "o documento não informa nenhum dos campos",
    # flib reprocess: o comando
    "term.reprocess.ask_issuer_alone": "--ask-issuer só funciona com --apply-layout",
    "term.reprocess.fill_fields_alone": (
        "--fill-fields só registra campos no índice; ele aceita --category, --ext, --since, "
        "--until, --simulate e --yes"
    ),
    "term.reprocess.apply_layout_alone": (
        "--apply-layout só move arquivos para o layout deles; ele aceita --category, "
        "--ask-issuer, --simulate e --yes, e nenhuma migração de categoria"
    ),
    "term.reprocess.scrub_alone": (
        "--scrub-descriptions vale para o índice inteiro; deixe de fora as outras opções"
    ),
    "term.reprocess.rename_or_remove": "use --rename-category ou --remove-category, não os dois",
    "term.reprocess.migration_filters": (
        "uma migração de categoria vale para a categoria inteira; deixe de fora os filtros"
    ),
    "term.reprocess.migration_rename": (
        "uma migração de categoria mantém os nomes dos arquivos; deixe de fora o --rename"
    ),
    "term.reprocess.to_review_alone": "--to-review só funciona com --remove-category",
    "term.row.scrub": _pt("limpar", "{path}  ({fields})"),
    "term.row.fill": _pt("preencher", "{path}  ({fields})"),
    "term.row.leave": _pt("de fora", "{path}  ({why})"),
    "term.row.rename": _pt(
        "renomear", "categoria {old} -> {new} no índice (a mesma pasta no macOS)"
    ),
    "term.reprocess.scrub_simulation": Plural(
        zero="Simulação: nenhum arquivo para limpar; nada mudou.",
        one="Simulação: {count} arquivo para limpar; nada mudou.",
        other="Simulação: {count} arquivos para limpar; nada mudou.",
    ),
    "term.reprocess.scrub_question": Plural(
        zero="Nada para limpar no índice",
        one="Limpar {count} arquivo no índice? `flib undo` desfaz",
        other="Limpar {count} arquivos no índice? `flib undo` desfaz",
    ),
    "term.reprocess.nothing_changed": "Nada mudou.",
    "term.reprocess.scrub_cached": Plural(
        zero="Nenhuma resposta em cache limpa.",
        one="{count} resposta em cache limpa.",
        other="{count} respostas em cache limpas.",
    ),
    "term.reprocess.nothing_to_scrub": (
        "Nada para limpar: nenhuma descrição guardada tem um identificador que o flib reconheça."
    ),
    "term.reprocess.scrub_files": Plural(
        zero="nenhum arquivo", one="{count} arquivo", other="{count} arquivos"
    ),
    "term.reprocess.scrub_values": Plural(
        zero="nenhum valor alterado",
        one="{count} valor alterado",
        other="{count} valores alterados",
    ),
    "term.reprocess.scrub_embeddings": Plural(
        zero="Nenhum embedding removido.",
        one="{count} embedding removido; a próxima execução o recria.",
        other="{count} embeddings removidos; a próxima execução os recria.",
    ),
    "term.reprocess.scrub_done": (
        "Execução de limpeza {run}: {files}, {values}. `flib undo` desfaz."
    ),
    "term.reprocess.scrub_done_embeddings": (
        "Execução de limpeza {run}: {files}, {values}. `flib undo` desfaz. {embeddings}"
    ),
    "term.reprocess.all_have_fields": "Todos os arquivos da biblioteca já têm os campos.",
    "term.reprocess.cached": Plural(
        zero="nenhum respondido pelo cache",
        one="{count} respondido pelo cache",
        other="{count} respondidos pelo cache",
    ),
    "term.reprocess.to_ask": Plural(
        zero="nenhum para enviar a {provider}",
        one="{count} para enviar a {provider}",
        other="{count} para enviar a {provider}",
    ),
    "term.reprocess.fields_estimate": Plural(
        zero="Nenhum arquivo sem campos.",
        one="{count} arquivo sem campos: {cached}, {to_ask} (estimativa: {cost}).",
        other="{count} arquivos sem campos: {cached}, {to_ask} (estimativa: {cost}).",
    ),
    "term.reprocess.send_question": Plural(
        zero="Nada para enviar a {provider}",
        one="Enviar {count} arquivo para {provider}?",
        other="Enviar {count} arquivos para {provider}?",
    ),
    "term.reprocess.nothing_sent": "Nada enviado, nada mudou.",
    "term.reprocess.simulation_nothing_changed": "Simulação: nada mudou.",
    "term.reprocess.nothing_to_record": "Nada para registrar.",
    "term.reprocess.fields_question": Plural(
        zero="Nada para registrar no índice",
        one="Registrar os campos de {count} arquivo no índice? `flib undo` desfaz",
        other="Registrar os campos de {count} arquivos no índice? `flib undo` desfaz",
    ),
    "term.reprocess.issuer_estimate": Plural(
        zero="Nenhum arquivo precisa do emissor.",
        one="{count} arquivo precisa do emissor: {cached}, {to_ask} (estimativa: {cost}).",
        other="{count} arquivos precisam do emissor: {cached}, {to_ask} (estimativa: {cost}).",
    ),
    "term.reprocess.nothing_sent_issuer": (
        "Nada enviado; os arquivos sem emissor ficam onde estão."
    ),
    "term.reprocess.in_layout": Plural(
        zero="Nenhum arquivo já segue o layout da categoria.",
        one="{count} arquivo já segue o layout da categoria dele.",
        other="{count} arquivos já seguem o layout da categoria deles.",
    ),
    "term.reprocess.simulation_nothing_moved": "Simulação: nada foi movido nem mudou.",
    "term.reprocess.layout_nothing": "Nada para mover: a biblioteca já segue os layouts.",
    "term.reprocess.layout_question": Plural(
        zero="Nenhum arquivo para mover para o layout",
        one="Mover {count} arquivo para o layout dele? `flib undo` desfaz",
        other="Mover {count} arquivos para o layout deles? `flib undo` desfaz",
    ),
    "term.reprocess.layout_record_question": Plural(
        zero="Nada para registrar no índice",
        one="Registrar no índice como {count} arquivo está organizado? Nada é movido",
        other="Registrar no índice como {count} arquivos estão organizados? Nada é movido",
    ),
    "term.reprocess.rules_nothing": "Nada para mover: a biblioteca já segue as regras.",
    "term.reprocess.move_question": Plural(
        zero="Nenhum arquivo para mover",
        one="Mover {count} arquivo? `flib undo` desfaz",
        other="Mover {count} arquivos? `flib undo` desfaz",
    ),
    "term.reprocess.rename_question": "Renomear a categoria no índice? `flib undo` desfaz",
    "term.reprocess.nothing": "Nada para reclassificar.",
    "term.reprocess.estimate": Plural(
        zero="Nenhum arquivo para reclassificar.",
        one="{count} arquivo para reclassificar: {cached}, {to_ask} (estimativa: {cost}).",
        other="{count} arquivos para reclassificar: {cached}, {to_ask} (estimativa: {cost}).",
    ),
    "term.reprocess.estimate_cap": Plural(
        zero="Nenhum arquivo para reclassificar (o limite por execução é {cap}).",
        one="{count} arquivo para reclassificar: {cached}, {to_ask} "
        "(estimativa: {cost}; o limite por execução é {cap}).",
        other="{count} arquivos para reclassificar: {cached}, {to_ask} "
        "(estimativa: {cost}; o limite por execução é {cap}).",
    ),
    "term.reprocess.plan": Plural(
        zero="Plano: nenhum seria movido, {unchanged}, {kept}.",
        one="Plano: {count} seria movido, {unchanged}, {kept}.",
        other="Plano: {count} seriam movidos, {unchanged}, {kept}.",
    ),
    "term.reprocess.in_place": Plural(
        zero="nenhum já no lugar", one="{count} já no lugar", other="{count} já no lugar"
    ),
    "term.reprocess.kept": Plural(
        zero="nenhum mantido como está",
        one="{count} mantido como está",
        other="{count} mantidos como estão",
    ),
    "term.reprocess.deferred": Plural(
        zero="Todos os arquivos foram enviados.",
        one="{count} arquivo não foi enviado: o limite de custo foi atingido. "
        "Rode `flib reprocess` de novo para continuar.",
        other="{count} arquivos não foram enviados: o limite de custo foi atingido. "
        "Rode `flib reprocess` de novo para continuar.",
    ),
    "term.reprocess.filed": Plural(
        zero="nenhum organizado", one="{count} organizado", other="{count} organizados"
    ),
    "term.reprocess.index_changes": Plural(
        zero="nenhuma alteração no índice",
        one="{count} alteração no índice",
        other="{count} alterações no índice",
    ),
    "term.reprocess.undoable": "`flib undo` desfaz.",
    "term.reprocess.unchanged": "Nada mudou.",
    "term.reprocess.summary": (
        "Reclassificação {run}: {filed}, {review}, {failed}, {skipped}. {end}"
    ),
    "term.reprocess.summary_changes": (
        "Reclassificação {run}: {filed}, {review}, {failed}, {skipped}, {changes}. {end}"
    ),
    "term.reprocess.empty_folder": (
        "A pasta {folder} está vazia agora. Mantenha-a até ter certeza da mudança; "
        "`flib undo` devolve os arquivos para ela."
    ),
    "term.column.description": "descrição",
    "term.column.tags": "tags",
    "term.column.kind": "tipo",
    "term.column.issuer": "emissor",
    "term.column.amount": "valor",
    "term.column.currency": "moeda",
    "term.column.document_date": "data do documento",
    "term.column.due_date": "vencimento",
    "term.column.period": "período",
    "term.column.name_choice_reason": "motivo da escolha do nome",
    "term.column.layout_date": "data do layout",
    # flib init
    "term.yes_no.yes": "s",
    "term.yes_no.no": "n",
    "term.init.welcome": (
        "Configurando o file-librarian. Aperte Enter para aceitar a sugestão entre colchetes."
    ),
    "term.init.replace": "Já existe uma configuração em {path}. Substituir?",
    "term.init.keeping": "Mantendo {path}.",
    "term.init.wrote": "Gravei {path} (só você pode lê-lo). `flib check-config` o valida.",
    "term.init.no_trial": (
        "Pulando o teste. `flib simulate` mostra o plano quando o flib puder rodar."
    ),
    "term.init.local_quality": (
        "O que esperar, medido no corpus de teste do flib (docs/quality.md): {model} coloca "
        "92% (inglês) e 97% (português) dos itens numa categoria certa, contra 99% e 100% do "
        "padrão hospedado, e manda para revisão 7 de 9 itens duvidosos, contra 9 de 9; então "
        "confira a pasta de revisão e o `flib simulate` com mais frequência no começo."
    ),
    "term.init.next": (
        "Próximos passos:\n"
        "  flib simulate   o plano para as suas pastas de verdade; nada é movido\n"
        "  flib run        organiza agora; `flib undo` desfaz\n"
        "  flib status     o que espera por você em revisão ou com falha, e a última e a "
        "próxima execução\n"
        "Opcional: deixe o Claude buscar na sua biblioteca pelo servidor MCP; ative com "
        "`./scripts/install.sh --with-mcp` na pasta do file-librarian."
    ),
    "term.init.language": "Idioma dos nomes, descrições e pastas",
    "term.init.choose_one": "escolha um de {options}.",
    "term.init.folders_again": "Vamos tentar as pastas de novo.",
    "term.init.other_provider": "Escolha outro provedor por enquanto.",
    "term.init.vision_note": (
        "A visão (vision) deixa o modelo olhar fotos, capturas de tela e PDFs escaneados. O que "
        "é enviado: a imagem reduzida para 1568 px, sem os metadados EXIF e GPS, ou as 3 "
        "primeiras páginas de um PDF sem texto. Cerca de US$ 0,0005 por imagem e US$ 0,001 por "
        "PDF escaneado no modelo padrão. Desligada, eles são classificados pelo nome e pelos "
        "metadados."
    ),
    "term.init.vision_question": "Enviar imagens e PDFs escaneados ao modelo?",
    "term.init.no_vision": (
        "Imagens e PDFs escaneados são classificados pelo nome e pelos metadados."
    ),
    "term.init.no_ollama": (
        "O Ollama não está instalado: é ele que roda o modelo local. O flib não o instala para "
        "você; {hint}. Depois rode `flib init --local` de novo."
    ),
    "term.init.ollama_stopped": (
        "O Ollama está instalado ({command}), mas não está rodando. Inicie com `ollama serve`, "
        "`brew services start ollama` ou o app do Ollama, e rode `flib init --local` de novo."
    ),
    "term.init.ollama_running": (
        "O Ollama está rodando. A configuração local usa {model} para classificar e "
        "{embedding} para a busca por significado."
    ),
    "term.init.models_there": "Os dois modelos já estão baixados.",
    "term.init.model_size": "{model} ({size} GB)",
    "term.init.not_downloaded": "Ainda não baixados: {sizes}.",
    "term.init.download_question": "Baixar agora ({size} GB)?",
    "term.init.download_later": "Baixe depois com: {pulls}",
    "term.init.pull_failed": "{error}; tente `ollama pull {model}`.",
    "term.init.not_adding": "Não vou adicionar {what}: {error}",
    "term.init.the_desktop": "a Mesa",
    "term.init.the_shots": "a pasta de capturas de tela",
    "term.init.desktop": (
        "O flib também pode arrumar a Mesa. As pessoas guardam trabalho em andamento lá, então "
        "ele pegaria só documentos e imagens que não mudam há {days} dias, e deixaria as pastas "
        "em paz. O macOS pergunta uma vez se o flib pode ler a Mesa."
    ),
    "term.init.desktop_question": "Arrumar a Mesa também?",
    "term.init.shots_shared": (
        "As capturas de tela são salvas em {where}, que guarda outros arquivos também, então o "
        "flib não oferece organizá-la por uma categoria fixa. Para organizar as capturas à "
        "parte, escolha uma pasta só para elas no app Captura de Tela (Cmd+Shift+5 > Opções > "
        "Outro Local), e depois rode `flib init` de novo ou adicione-a em `inputs` "
        "(veja config.example.yaml)."
    ),
    "term.init.shots": Plural(
        zero="As capturas de tela são salvas em {where} (nada lá parece captura ou gravação de "
        "tela agora). O flib pode organizá-las em {category}/<ano>/<mês> sem perguntar à IA "
        "(sem custo), mantendo os nomes; o resto, e as pastas, fica como está.",
        one="As capturas de tela são salvas em {where} ({count} item lá parece captura ou "
        "gravação de tela agora). O flib pode organizá-las em {category}/<ano>/<mês> sem "
        "perguntar à IA (sem custo), mantendo os nomes; o resto, e as pastas, fica como está.",
        other="As capturas de tela são salvas em {where} ({count} itens lá parecem capturas ou "
        "gravações de tela agora). O flib pode organizá-las em {category}/<ano>/<mês> sem "
        "perguntar à IA (sem custo), mantendo os nomes; o resto, e as pastas, fica como está.",
    ),
    "term.init.shots_none": (
        "Nada lá tem um nome que o flib reconheça como captura de tela (em inglês, português, "
        "espanhol, francês, alemão, italiano ou japonês), então ele talvez nunca organize nada "
        "dali; a resposta que você provavelmente quer é não."
    ),
    "term.init.shots_question": "Organizar as capturas de tela também?",
    "term.init.folder": "Pasta a organizar",
    "term.init.not_a_folder_yes": (
        "{folder} não é uma pasta; rode `flib init` sem --yes para escolher outra"
    ),
    "term.init.not_a_folder": "{folder} não é uma pasta.",
    "term.init.output": "Para onde vão os arquivos organizados",
    "term.init.review": "Pasta, dentro dela, para os arquivos a revisar",
    "term.init.claude_not_found": "não encontrado",
    "term.init.claude_logged_in": "encontrado, logado",
    "term.init.claude_logged_out": "encontrado, não logado",
    "term.init.option.claude_code": (
        "  claude-code    sua assinatura do Claude pelo Claude Code ({state}); sem chave de API"
    ),
    "term.init.option.anthropic": (
        "  anthropic      a API da Anthropic com uma chave de API, cobrada por uso "
        "(uma fração de centavo por arquivo)"
    ),
    "term.init.option.ollama": Plural(
        zero="  ollama         um modelo local pelo Ollama (nenhum baixado); nada sai deste Mac",
        one="  ollama         um modelo local pelo Ollama ({count} baixado); nada sai deste Mac",
        other="  ollama         um modelo local pelo Ollama ({count} baixados); nada sai deste Mac",
    ),
    "term.init.option.openai_compat": (
        "  openai-compat  qualquer API compatível com a da OpenAI, com a URL dela e uma "
        "chave opcional"
    ),
    "term.init.option.local": (
        "  local          nada sai deste Mac: {model} no Ollama, busca local; mais lento e "
        "menos preciso (precisa do Ollama, ~10 GB, 16 GB de memória)"
    ),
    "term.init.which_ai": "Qual IA classifica seus arquivos?",
    "term.init.provider": "Provedor",
    "term.init.ollama_model": "Modelo do Ollama",
    "term.init.base_url": "URL base da API (a parte antes de /chat/completions)",
    "term.init.model": "Modelo",
    "term.init.key_found": "Chave de API: encontrada em {source} ({hint}).",
    "term.init.store_key": (
        "Nenhuma chave de API da Anthropic encontrada. Guardar uma no Keychain do macOS agora?"
    ),
    "term.init.no_key_yet": "ainda não há chave de API; rode `flib key set` para adicionar uma.",
    "term.init.store_server_key": (
        "Nenhuma chave de API encontrada para este servidor (um servidor local normalmente não "
        "precisa). Guardar uma no Keychain do macOS agora?"
    ),
    "term.init.sentence": "{text}.",
    "term.init.claude_missing": (
        "o Claude Code não foi encontrado; instale-o ou defina provider.command."
    ),
    "term.init.claude_logged_out_warning": "o Claude Code não está logado; {hint}.",
    "term.init.not_available": "o provedor {provider} ainda não está disponível.",
    "term.init.nothing_to_try": (
        "Nenhum arquivo para testar em {where}: está vazia, ou só tem itens ocultos, sensíveis "
        "ou grandes. `flib simulate` mostra o plano quando chegarem arquivos."
    ),
    "term.init.try_question": Plural(
        zero="Nada para testar em {where} com {provider}.",
        one="Testar numa cópia do seu item mais recente? Ele é copiado para uma pasta "
        "temporária e classificado com {provider}; nada em {where} é movido.",
        other="Testar em cópias dos seus {count} itens mais recentes? Eles são copiados para "
        "uma pasta temporária e classificados com {provider}; nada em {where} é movido.",
    ),
    "term.init.classifying": Plural(
        zero="Nada para classificar.",
        one="Classificando {count} cópia; isso pode levar um minuto...",
        other="Classificando {count} cópias; isso pode levar um minuto...",
    ),
    "term.init.trial_stopped": "o teste parou: {error}",
    "term.init.trial": Plural(
        zero="Teste sem nenhuma cópia (os destinos ficariam dentro de {root}):",
        one="Teste em {count} cópia (os destinos ficam dentro de {root}):",
        other="Teste em {count} cópias (os destinos ficam dentro de {root}):",
    ),
    "term.row.trial_review": _pt("revisão", "{name}  ->  {destination}"),
    "term.row.trial_dup": _pt("duplicado", "{name}  ->  {destination}"),
    "term.row.trial_move": _pt("mover", "{name}  ->  {destination}"),
    "term.init.trial_summary": "Resumo: {organized}, {review}, {stay}.",
    "term.init.to_categories": Plural(
        zero="nenhum para as categorias",
        one="{count} para as categorias",
        other="{count} para as categorias",
    ),
    "term.init.to_review": Plural(
        zero="nenhum para {folder}, para você decidir",
        one="{count} para {folder}, para você decidir",
        other="{count} para {folder}, para você decidir",
    ),
    "term.init.not_tried": Plural(
        zero="  nenhum ficou sem testar.",
        one="  {count} não testado: o limite de custo foi atingido.",
        other="  {count} não testados: o limite de custo foi atingido.",
    ),
    "term.init.trial_cost": (
        "Custo estimado do teste: {cost}. Nada nas suas pastas foi movido; as cópias e o "
        "índice do teste já foram apagados."
    ),
    "term.init.schedule_unreadable": "não foi possível ler o agendamento: {error}",
    "term.init.watch_already": "O modo de observação já está ativo; `flib schedule` muda isso.",
    "term.init.both_already": (
        "Já há uma execução diária agendada para {time}, com o modo de observação; "
        "`flib schedule` muda isso."
    ),
    "term.init.daily_already": (
        "Já há uma execução diária agendada para {time}; `flib schedule` muda isso."
    ),
    "term.init.schedule_question": (
        "Organizar os arquivos novos automaticamente todo dia às {time}?"
    ),
    "term.init.schedule_later": (
        "Você pode agendar depois com `flib schedule` (--at HH:MM para outro horário)."
    ),
    "term.init.watch_explained": (
        "O modo de observação organiza os arquivos alguns minutos depois que eles chegam "
        "(quando os downloads terminam) em vez de uma vez por dia; both mantém a execução "
        "diária como rede de segurança."
    ),
    "term.init.mode_question": "Rodar todo dia (daily), na chegada (watch), ou os dois (both)?",
    "term.init.set_mode": (
        "Defina schedule.mode: {mode} em {path} para que o `flib schedule` mantenha essa escolha."
    ),
    "term.init.not_scheduled": "não foi possível agendar a execução diária: {error}",
    "term.init.menu_already": (
        "O flib já está na barra de menus; `flib menu --remove` o tira de lá."
    ),
    "term.init.menu_question": (
        "Mostrar o flib na barra de menus (o que precisa de você, e um menu que abre o flib)?"
    ),
    "term.init.menu_later": "Você pode adicioná-lo depois com `flib menu`.",
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
        "Nada foi aberto de novo: {error}. Use a aba do navegador que ele abriu, ou encerre lá "
        "({end}) ou com Ctrl-C no "
        "terminal dele, e rode `flib ui` de novo; ele também termina sozinho depois de "
        "{minutes} sem uso."
    ),
}
