# Daily use

```sh
flib simulate             # the plan for your real folder; nothing moves
flib run                  # organize now
flib undo                 # revert the last run (--run ID for another, --file PATH for one file)
flib search "power bill"  # find a file again
flib status               # what waits for you
flib due                  # bills due this week, and what is overdue
flib ui                   # the same, in your browser
```

Files the AI is not sure about go to the review folder (`_Review` inside
`~/Downloads/Organized` by default) for you to decide.

## What waits for you

Once flib runs by itself, the files it was not sure about wait in the review
folder, and you only see them if you look. `flib status` sums it up without
naming any file:

```text
--8<-- "en/status.txt"
```

It only reads (no lock, so it works while a run is going on, and before the
first one); `--json` gives the same for scripts.

You do not have to remember to run it: after a *scheduled* run, when a file
has waited in review or failed for two days or more, flib writes a one-line
summary to the run log and shows one macOS notification, such as "3 files
wait for review, 1 failed. Run `flib status` for details." It does this at
most once a week, and never after a `flib run` you started yourself. The
notification holds counts only, never a file name, because notifications show
on the lock screen. Change it under `summary:` in the configuration:
`notify: false` keeps only the log line, `every_days` and `min_age_days` set
how often and how patient.

The notification comes from "file-librarian", with its own icon: `flib
schedule` builds a tiny notifier app for it inside flib's own environment
(never in /Applications), with tools that ship with macOS, and posts one
first notification so macOS asks you then whether to allow it. Allow it (or
later, under System Settings > Notifications > file-librarian). If the app
cannot be built, notifications still come, shown as Script Editor;
`flib status` says which one is in use. If you never see the notification,
see [Troubleshooting](troubleshooting.md#no-notification-appears).

## The interface

```sh
flib ui                  # opens the default browser
flib ui --open review    # starts on another page: status, review, search, activity, due, settings
flib ui --no-browser     # prints a one-time link instead
flib ui --port 8765      # a fixed port (default: a free one)
```

`flib ui` opens a small interface in your browser, light or dark like your
Mac. It reads the same index as the command line, and it exists only while
you use it: it ends after 10 minutes without use (`ui.idle_minutes`), with
**End session**, or with Ctrl-C. One runs at a time: a second `flib ui --open
PAGE` opens that page in the one running (in the browser where it runs) and
exits. How it keeps the session to you is in
[Privacy and safety](privacy.md#the-interface-flib-ui).

![The Status page: 5 files in review, 1 failed, 3 due, 1 sensitive file left in
place, 1 duplicate set aside, the last run and this month's
cost](assets/screenshots/en/status.webp)

- **Status**: the counts of `flib status`.
- **Review**: the files waiting in the review folder, grouped by why they
  are there and what the AI suggested. For each one: **Accept** the
  suggestion, pick **another category** or a **new name** (the same as
  `flib fix`, so flib learns from it), **Put back where it was** (`flib undo`
  for that file: back in Downloads, where later runs leave it), and Open or
  Show in Finder to look first. A group with a suggestion has **Accept all**,
  which lists the files before moving them (at most 100 at a time, never one
  missing on disk), as one run that one Undo reverts.
- **Undo**: after each action, and for each run and move on Activity. It
  shows what will be put back, and where, and what will be skipped and why
  (moved again by a later run, edited since, its old place taken), before
  doing anything; it refuses what `flib undo` refuses, with the same reason.
  Undoing a correction also makes flib forget what it learned from it.
- **Search**: `flib search` with its filters (category and kind chips, the
  dates, the issuer, amounts, the due date, the period), Open and Show in
  Finder for each match, totals per currency as `flib search --sum` adds them
  up, and Export CSV. Sensitive files are never listed there; they are
  counted.
- **Activity**: the runs of the last 7, 30 or 90 days, by day: what each one
  moved where, duplicates set aside, sensitive files left in place (counted,
  never named), a wait for the Mac to be unlocked, and the cost.
- **Due**: `flib due`, with its window (7 days to 3 months, or overdue) and
  totals per currency, and **Mark done** / **Open again** (`flib fix --done`
  / `--not-done`).
- **Settings**: the essentials, without opening the file. When to organize
  (off, daily at a time, as files arrive, or both: `flib schedule` from the
  page), who classifies (a choice among the providers the file already
  allows, with what each one sends; the API key is shown only as set or not
  set), vision, text recognition and search by meaning (each says what
  leaves the Mac), Downloads and the Desktop as input folders (the Desktop
  with `flib init`'s careful rules), duplicates, categories (add, rename,
  remove, hint, sub-folders), the cost caps, the summary notification and
  what is due, and the language (which is also the interface's: English or
  Brazilian Portuguese, from the next page on). Each change is checked as `flib
  check-config` checks it **before** it is written; a value it refuses is
  shown with its field and why, and nothing is written. Only the keys you
  changed are touched: your comments and the order of the file stay. The
  old file is kept as a backup next to it (`config.yaml.backup-<time>Z`, the
  latest 10), and **Restore the previous settings** puts it back after
  showing what changes. A category that holds files is renamed or removed
  with `flib reprocess --rename-category` / `--remove-category`, which move
  them: the page says so instead. Everything else (another folder or
  server, folder rules, the library's place) stays in the file; the page
  names it, opens it in a text editor, and points to `flib check-config` and
  `flib doctor`. If the file changed since the page was shown (edited by
  hand, another tab), nothing is saved and the page shows it as it is.

The pages work without JavaScript. A search goes in the form, not the
address, and Back and Forward work: they never repeat an action. Every
change goes through the same code, lock and journal as the command line,
so `flib undo` reverts it too. If a run is going on, an action says so and
does nothing; if a file changed since the page was shown (moved, undone,
handled in another tab), it is refused, never acted on. Nothing is ever
overwritten: a taken name gets the next free number. Sensitive files are
never listed or acted on.

## Review

![The Review page: four photos grouped under one suggestion, Work, with Accept
all; a recipe whose suggested category is not
configured](assets/screenshots/en/review.webp)

Each file in review says why it is there: the AI's confidence was below
`limits.confidence_threshold` (with the category it suggested), the AI chose a
category you have not configured, its category was removed
(`flib reprocess --remove-category --to-review`), or you moved it into the
review folder yourself. Decide in the interface, or from the terminal with
[`flib fix`](#teaching-it).

## Search

![The Search page: three invoices found for "invoice", totals per currency,
each with its kind, issuer, amount, due date and
period](assets/screenshots/en/search.webp)

Every word of the query must match a name, the description or a tag; accents
are ignored and words match as prefixes. Filters narrow the results and can be
combined with each other and with a query, or used without one, in which case
matches are listed newest first:

- `--category NAME`: case-insensitive. Repeat it or separate names with commas
  to match any of them (`--category finance,contracts`).
- `--ext EXT`: with or without the dot (`pdf` or `.pdf`). Repeat it or use
  commas for several (`--ext jpg,png`).
- `--since DATE` / `--until DATE`: `YYYY-MM-DD`, both days included, in your
  local time. The date is when flib last processed the file (filed it, or sent
  it to review), not the date written in the document (`--period` filters by
  that, see [below](#amounts-dates-and-issuers)), nor the file's modification
  time.
- `--limit N`: at most this many results (20 by default, up to 500).

```sh
flib search "power bill" --category finance --since 2026-01-01
flib search --ext pdf --until 2025-12-31 --json
```

An unknown category, a malformed extension or date, or `--since` after
`--until` stops with a message saying what is wrong.

```text
--8<-- "en/search.txt"
```

### Amounts, dates and issuers

When flib files a document, the AI also notes what the document itself
states, and leaves out what it does not (it is asked never to guess):

- `kind`: one of `bill`, `receipt`, `invoice`, `statement`, `contract`,
  `payslip`, `tax`, `ticket`, `policy`, `report`, `form`, `letter`,
  `manual`, `other`;
- `issuer`: the organisation it comes from (never a private person);
- `amount` and `currency`: the total or amount due (not a balance or any
  other number), with its ISO 4217 code;
- `document_date`, `due_date`, and `period`: the month or year it refers to
  (the billing month of a bill, the tax year).

Numbers are read whatever the format (`1.234,56`, `1,234.56`, `R$`, `€`);
a value flib cannot read is dropped, never guessed, and document, account and
ID numbers are never kept. Results show the fields on one line
(`bill · Power Co · USD 103.91 · due 2026-09-20 · for 2026-08`), and these
filters combine with the others and with a query:

- `--issuer NAME`: case, accents, punctuation and legal forms (`Inc`, `Ltd`,
  `S.A.`) do not matter, so `--issuer "power co"` finds `POWER CO, Inc.`;
- `--kind KIND`: repeat it or use commas for any of several;
- `--min-amount N` / `--max-amount N`, both included; `--currency CODE`;
- `--due-before DATE` / `--due-after DATE`, the day itself excluded;
- `--period 2026` or `--period 2026-09`: the period the document refers to,
  else the month of its date.

`--sum` adds up the amounts of every match instead of listing them, per
currency (two currencies are never added together), and `--by issuer`,
`month`, `year`, `kind` or `category` groups the totals. `--sum` with no
words or filter adds up every filed document:

```sh
flib search --sum --by kind                             # everything, per kind of document
flib search --issuer "Power Co" --period 2026 --sum     # what did I pay Power Co in 2026?
flib search --kind bill --period 2026 --sum --by month --json
flib search --kind bill --due-after 2026-11-20          # bills still to pay
```

```text
--8<-- "en/search-sum.txt"
```

Files filed before flib kept these fields have none: `flib reprocess
--fill-fields` asks the AI about those files only, after showing the cost,
records the fields in the index (nothing moves, categories and names stay)
and `flib undo` reverts it. With `--sum`, a query matches words only, not
meaning. More in [the guide](guide.md#amounts-dates-and-issuers).

### Search by meaning (optional)

Word search finds "electricity invoice" only if those words are in the index.
Semantic search also finds files by meaning: `flib search "power bill"
--semantic` finds a file described as an "electricity invoice". Each file gets
an *embedding*, a list of numbers that places texts with similar meaning close
together, and the query gets one too. The results of both searches are merged
(reciprocal rank fusion), so exact word matches still come first, and the
filters above apply to both. It is off by default. To turn it on with
[Ollama](https://ollama.com) on this Mac:

1. `ollama pull embeddinggemma` (about 600 MB; multilingual).
2. In the configuration:

    ```yaml
    search:
      semantic:
        enabled: true
    ```

3. `flib search --reindex` to embed the files already indexed. After that,
   every `flib run` embeds the files it adds or changes.

Then search with `--semantic`, or set `search.semantic.default: true` to make
it the default (`--no-semantic` then searches by words only, for one search).
The MCP tool `search_files` takes the same choice as its `semantic` flag:
`true`, `false` (words only, even when the default is by meaning) or unset
(the default). With `--json`, and in MCP results, each file says how it was
found (`source`: `text`, `semantic` or `both`) and, when it has an embedding,
its `similarity` to the query.

A search by meaning always has a "nearest" file, even when nothing is related,
so files found **only** by meaning must be at least
`search.semantic.min_similarity` similar to the query (cosine, 0 to 1; 0.25 by
default). A file that matches the words always stays, however low its
similarity. When the floor leaves nothing, the search says "No close
matches; try other words or --no-semantic".

The right floor depends on the model. 0.25 was measured for `embeddinggemma`
on a synthetic corpus of 60 files in Portuguese and English and 32 queries:
it kept 43 of 45 related pairs (the two lost were loose ones, like "credit
card statement" for a bank statement, at 0.20) and dropped 98% of unrelated
ones, leaving on average about one unrelated file per query instead of 18.
For another model, search for a few things you know are there with `--json`,
look at the `similarity` of the files you wanted and of the ones you did not,
and set `min_similarity` between them; `0` turns the floor off.

`embeddinggemma` places a query and a document best when told which is which:
flib puts `task: search result | query: ` before a query and `title: none |
text: ` before a file's text, as its model card asks. Without them, unrelated
files scored nearly as high as related ones (median 0.22, and 5% above 0.34)
and no floor could tell them apart. Other models get no prefix unless
`search.semantic.query_prefix` and `document_prefix` say so (`""` turns one
off). Changing the document prefix embeds the library again on the next run.

What is embedded is only what the index already stores about a file: its
names, category, tags and the AI's description, never its content. A
sensitive file, or one matching `never_send`, is never embedded. Any server
with an OpenAI-compatible `/embeddings` endpoint works
(`search.semantic.base_url` and `model`). Its optional key comes from
`FLIB_EMBEDDINGS_API_KEY`, or the variable `search.semantic.api_key_env`
names, or `flib key set --provider embeddings`; never from the classification
provider's key. With a remote server, that index text and every semantic
query leave your Mac, `flib check-config` warns about it, and the cost is not
counted: check your server's prices. Anthropic has no embeddings endpoint, so
it cannot be used here.

If the embeddings server cannot be reached, or no file has an embedding yet,
the search says so in one line and searches by words. A run never fails
because of embeddings: what was not embedded is retried on the next run.
Changing `search.semantic.model` embeds the library again. Vectors are kept
in the same database and searched by brute force: about 0.1 s for 10,000
files and 0.6 s for 50,000.

## What is due

`flib due` answers "what is due this week?" from the due dates the AI read in
your documents:

```text
--8<-- "en/due.txt"
```

![The Due page: one overdue bill and two due this week, with totals per
currency and Mark done](assets/screenshots/en/due.webp)

Bills, invoices and taxes (`due.kinds`) overdue up to 30 days ago and due in
the next 7 days; `--within 2w` looks further ahead, `--overdue` lists
everything past due, `--issuer` and `--kind` narrow it, `--json` is for
scripts. A document without a stated due date is never listed. When you have
paid one, `flib fix PATH --done` takes it off the list (`flib undo` reverts
it, `--all` still shows it), and `due.done_tag: Paid` lets a Finder tag do the
same. `flib status` adds one line ("Due: 3 documents within 7 days, 1
overdue"), and the periodic summary reminds you once per document when one
comes due within 3 days; details in the [guide](guide.md#what-is-due).

## Activity and undo

![The Activity page: the runs of the last seven days by day, what each moved
where, a fix by hand, an undone move, and the
cost](assets/screenshots/en/activity.webp)

Every run is recorded and can be undone, except files you changed or moved
afterwards, which are skipped and listed:

```sh
flib undo                     # the last run
flib undo --run 12            # another run (its number is on the Activity page)
flib undo --file PATH         # the last operation on one file
```

`flib undo` puts duplicates back like any other move, removes only the Finder
tag flib added, and reverts fixes, reprocesses and what an assistant did over
[MCP](mcp.md). The interface's Activity page offers the same Undo for each run
and each move, and shows what will be put back, and where, before doing it.
[Privacy and safety](privacy.md#how-your-files-are-kept-safe) explains how the
journal makes that reliable.

## Teaching it

When a file lands in the wrong place, correct it:

```sh
flib fix ~/Downloads/Organized/Other/scan.pdf --category Finance
flib fix ~/Downloads/Organized/_Review/document.pdf --category Contracts --name lease-2026
```

The file moves, the index, search and the Finder tag follow, and `flib undo`
reverts it like a run. Moving or renaming a file by hand works too: run
`flib reconcile` and the index catches up. Both kinds of correction are sent
as examples with later requests, so the classifier stops repeating the
mistake (`provider.max_examples`, 10 by default; `0` sends none).

The categories are a closed list, and each category's `hint` in the
configuration is the main lever for fixing wrong classifications. After you
change categories, hints, naming rules or the model, `flib reprocess` files
what is already organized again:

```sh
flib reprocess --simulate          # classify and show what would move; moves nothing
flib reprocess                     # asks before sending, then before moving
flib reprocess --rename-category Docs Documents   # after renaming a category in the config
flib reprocess --remove-category Misc             # after removing one
```

It asks before it sends anything (with a cost estimate) and before it moves
anything, keeps your corrections and file names unless told otherwise, and
`flib undo` puts the whole reprocess back. The details are in
[the guide](guide.md#changing-your-rules).

## Or as files arrive (watch mode)

```sh
flib schedule --mode watch    # run a few minutes after something arrives in an input folder
flib schedule --mode both     # watch, with the daily run as a safety net
```

In watch mode macOS starts flib when an input folder changes, at most once
every `schedule.watch_min_interval_minutes` (5). It takes an item only once
it has not changed for `schedule.watch_quiet_seconds` (5 minutes, as slow
copies and torrents pause), waits for downloads in progress (`.crdownload`,
`.part`, a Finder copy...) as long as they keep changing, and then runs
once for everything that arrived: ten files at once are one run.
When nothing new is ready (for example right after flib's own moves) it
exits at once, with no run and no AI call. macOS watches only the top level
of each input folder, which is all flib organizes anyway, and flib also
looks every `schedule.watch_fallback_minutes` (30) for anything missed. Automatic runs
(daily and watch) also keep to `limits.max_cost_usd_per_day` (three times
the per-run cap by default); once today's cost reaches it they send nothing
more to the AI until midnight, and `flib status` says so. The periodic
summary and the due reminders come only after the daily run, so `both` is
the mode to pick if you want them. Set `schedule.mode` in the configuration
to make the mode the default for `flib schedule`; `--refresh` and `--at`
keep the mode installed and `--remove` stops both. Details, and what flib
cannot detect: [the guide](guide.md#watch-mode-organizing-files-as-they-arrive).
