# Guide: the details

The [README](../README.md) covers what most people need. This page holds the
details behind some of its sections.

## Names

A file is renamed only when the model read its content (text, text recognised
by OCR in an image or scanned PDF, or the image with vision on), its category
allows renaming (`rename: false` keeps original names), and the model is at
least `naming.rename_confidence_threshold` sure (0.8 by default). Videos,
installers, archives, folders and photos with vision off keep their name.

The model also says whether the file's current name already says what the
file is. Only a generic name (`scan001.pdf`, `document (3).pdf`,
`IMG_2041.jpg`) or one the content contradicts is replaced; a descriptive one
keeps its words, so two similar files stay told apart. A descriptive name is
kept whatever its language and is never translated (`boarding-pass.pdf`
stays `boarding-pass` with `language: pt-BR`), and informal or abbreviated
names count as descriptive (`extrato_out_2026.pdf`, `resume_final_v2.docx`).
The proposed subject does not pile the issuer or other fields onto a name:
those are kept as
[structured fields](../README.md#amounts-dates-and-issuers) and can file the
document into a folder by issuer instead.

```text
Study guide - 11 tweaks for coding agents.pdf  ->  2026-11-16_study-guide-11-tweaks-for-coding-agents.pdf
boarding-pass.pdf                              ->  2026-10-12_boarding-pass.pdf
scan001.pdf                                    ->  2026-10-05_electricity-bill.pdf
```

Either way flib, not the model, builds the final name: `naming.pattern`
(`{date}_{subject}` by default) with the document's date, or the download
date, or today; lower case words joined by `-`; at most `naming.max_length`
characters (80 by default, extension included); the original extension. A
date already at the start of the old name is not repeated. Ambiguous dates in
documents are read as `naming.date_order` says (`DMY` by default). A taken
name gets `-2`, `-3`... `flib simulate` marks a kept name with "original name
kept", and `flib inspect` shows the choice and the model's reason.

## Duplicates

A file with the same content as one flib already filed is moved to
`_Duplicates` (`_Duplicados` in pt-BR) inside the organized folder, under its
own name. It is never deleted, and it is not sent to the AI, so it costs
nothing.

- **The filed copy is checked first.** flib checks that it is still there
  with the same content. If it is not where flib left it (you renamed, moved,
  edited or deleted it, or its disk is not connected), the new copy is left
  in Downloads with a note to run `flib reconcile` (`flib status` counts
  these). Once reconcile has found the filed copy gone, the next run files
  the new one in its place.
- A file you dragged from the organized folder back to Downloads is
  recognised as the same file and left there.
- A copy of a sensitive file stays where it is, like the original.
- `flib undo` puts duplicates back like any other move, and later runs leave
  them there.
- `flib search` leaves duplicates out unless you add `--duplicates`; `flib
  inspect` on one says which file it is a copy of, and `flib status` counts
  them.
- If the original disappears, `flib reconcile` lists its duplicate as
  orphaned; move it back to Downloads to have it filed.
- Empty the folder whenever you like: flib never does. `duplicates: leave`
  turns all this off (copies then stay where they are), and
  `duplicates_folder` renames the folder.

**Copies left by earlier versions.** With the default `duplicates: move`, the
first run also sets aside the copies that earlier runs left in Downloads
(`report (1).pdf` next to a filed `report.pdf`). A note after the run says so;
scheduled runs write it to their log, and `flib status` shows it until you
have seen it after a `flib run` in Terminal. Nothing is deleted: `flib undo`
puts them back, and `duplicates: leave` keeps them where they are.

## More than one inbox

`inputs` lists the folders flib keeps organized. A plain path takes
everything at the folder's top level, as flib always did. A mapping adds
rules for that folder alone:

```yaml
inputs:
  - ~/Downloads
  - path: ~/Desktop
    only: ["*.pdf", "*.docx", "*.png", "*.jpg"]
    ignore: ["draft*"]
    min_age_days: 7
    folders: ignore
  - path: ~/Pictures/Screenshots
    category: Screenshots
    layout: "{year}/{month}"
    only: ["Screenshot*.png", "Screen Recording*.mov"]
    folders: ignore
```

| Option | Default | What it does |
| --- | --- | --- |
| `only` | (everything) | Name patterns; an item matching none of them is left alone and only counted (`N item(s) matching none of their input's only were left alone`) |
| `ignore` | `[]` | Name patterns left alone in this folder, on top of the global `ignore`. A name matching both `ignore` and `only` is ignored |
| `min_age_days` | `0` | Take an item only once it is this many days old (below) |
| `category` | (the AI decides) | A configured category every file from here goes to, with no AI call |
| `rename` | `false` | With `category`: apply `naming.pattern` to the original name (the date is the download date) |
| `layout` | the category's | With `category`: the sub-folders for files from here ([Sub-folders](#sub-folders-inside-a-category)) |
| `folders` | the global `folders` | `as_unit` or `ignore` for sub-folders and bundles here |
| `duplicates` | the global `duplicates` | `move` or `leave` for copies found here |

Patterns match a name, never a path, ignoring case and Unicode form the way
Finder does (`Relatório*` matches however the accent was stored). Hidden
files, downloads in progress, Office lock files (`~$report.docx`), files
only in iCloud and the organized folder itself are skipped in every input
before any rule is looked at.

**How old an item is.** Its age is the time since it last changed or
arrived in the folder: the later of its modification time and its
status-change time, which macOS updates when an item is moved, copied or
renamed into a folder, and when its contents or attributes change. It is
not the last time you opened it: macOS does not record that reliably, so a
document you only read every day still ages. A PDF from 2019 that you
dragged to the Desktop this morning is zero days old, and a time in the
future (a wrong clock) counts as brand new. For
a folder taken as one unit, the newest item inside counts too. A younger
item is skipped as `too recent (N days left)`; it is never an error, it is
not waiting for anything (the daily run does not stay up for it), and the
first run after it is old enough takes it. `flib status` counts how many the
last run left for this reason.

**A fixed category.** Files from an input with `category` are never sent to
the AI: no call, no cost, nothing cached. Nothing checks what they are,
so give such an input an `only` with the names you expect;
`flib check-config` warns about one without (`everything in this folder is
filed as X without being looked at`). They keep their name (unless
`rename: true`), get no description, and their tags are the words of their
name, so `flib search` finds them by name, category and tag. Their
`{year}` and `{month}` come from the download date; an `{issuer}` level is
never filled (no AI names one), so they stop just above it. Text is still
read on your Mac (including OCR of screenshots) for one reason: the secret
scan. A screenshot of a password or an API key is marked sensitive and stays
where it is, like anywhere else. The index marks these files as filed by
rule (`filed_by_rule`), and only those: `flib reprocess` (and
`--fill-fields`) never asks the AI about them, while a file the AI filed
from the same folder into the same category is reprocessed like any other.
`--apply-layout` leaves them alone when their input has a `layout` of its
own.

**Which input a file came from.** The index records it for every file it
files (files indexed before this version count as coming from their
original folder), and keeps it: `flib undo` puts a file back exactly where
it was taken from, and `flib reconcile` recognises a file you dragged back
into an input folder, its own or another, and leaves it there; the index
then knows where it is now and still which input it came from. With more
than one input,
`flib status` shows how many files each input gave (`flib doctor` writes
them as `<input-1>`, `<input-2>`... and never the folder names).

**What is rejected.** Inputs that overlap (the same folder twice, through a
symlink, the `/System/Volumes/Data/...` spelling or a different case, or
one folder inside another), your home folder itself, an input inside
`output_root`, a
`category` that is not in `categories`, `rename` or `layout` without a
`category`, an empty `only`, and a pattern with a `/`. `flib check-config`
prints each input with the rules it ends up with, the global ones included.

**`flib init`** offers two more inboxes after the first folder, both
answered no by default (Enter or `--yes` keep a Downloads-only setup):

- the Desktop, with `only` documents and images (PDF, Word, Excel,
  PowerPoint, Pages, Numbers, Keynote and the OpenDocument formats, and
  common image types; not plain text, Markdown or CSV, where scratch notes
  and exports live), `min_age_days: 7` and `folders: ignore`, because
  people keep work in progress there;
- the folder macOS saves screenshots to (read with `defaults read
  com.apple.screencapture location`), with a fixed `Screenshots` category
  (`Capturas de tela` in pt-BR), a `{year}/{month}` layout,
  `folders: ignore` and an `only` of the names macOS gives screenshots and
  screen recordings (`Screenshot*`, `Screen Shot*`, `Screen Recording*`,
  and the Portuguese, Spanish, French, German, Italian and Japanese ones
  such as `Captura de Tela*`, `Captura de pantalla*`, `Capture d’écran*`,
  `Bildschirmfoto*`, `Schermata*`, `スクリーンショット*`, as images or
  videos). Init says how many items there match before asking, and when
  none does, says that no is probably the answer. It is not offered when that folder holds other
  things: your home folder or a standard one in it (Desktop, the macOS
  default, Documents, Downloads, Pictures, Movies, Music, Library, iCloud
  Drive). Init then says how to pick a folder just for screenshots
  (Cmd+Shift+5 > Options > Other Location).

**Two limits to know.**

- The age check uses when an item last changed or arrived in the folder,
  not when it was last opened: a document you read every day but never edit
  still becomes old enough.
- When screenshots are saved to the Desktop (the macOS default) and the
  Desktop is an input, screenshots older than its `min_age_days` match its
  `only` list (`*.png`) and are classified like any image: their text is
  read on your Mac, the secret scan applies (a screenshot of a password stays
  where it is), and the AI picks the category. To keep them apart, have
  macOS save screenshots to a folder of their own (Cmd+Shift+5 > Options >
  Other Location) and add that folder with a fixed category, as `flib init`
  offers.

**One input that cannot be read** (macOS denied it, it was deleted, its disk
is not connected) is skipped with a warning in the run's output and log,
and `flib status` counts it; the other inputs are organized as usual. The
run fails only when no input can be read.

**macOS privacy.** The Desktop, Documents and Downloads are each guarded by
their own permission. `flib schedule` checks every input as the daily run
sees it and lists each one with `ok` or what macOS said; see
[troubleshooting](troubleshooting.md#macos-asks-about-the-desktop-or-documents).

## Sub-folders inside a category

By default each category is one folder. An optional `layout` files a
category into sub-folders:

```yaml
categories:
  - name: Finance
    layout: "{year}/{month}"    # Finance/2026/03/…
  - name: Contracts
    layout: "{issuer}"          # Contracts/Acme/…
  - name: Bills
    layout: "{issuer}/{year}"   # Bills/Power Co/2026/…
```

Each part between `/` is one folder. The placeholders:

- `{year}` and `{month}` (two digits): the date the document refers to, as
  the AI read it; without one, the download date; without that, the day the
  file was filed. It is the same date the file name gets, so a file's folder
  and its name agree.
- `{issuer}`: the organisation the document comes from (a company, bank,
  utility, public body or brand), as the AI names it. The prompt asks the
  AI never to give a private person's name; flib cannot check that, but it
  drops an answer holding a phone or document number. `{issuer}` stands
  alone between two `/`.

A folder part may add fixed text (`"Y{year}"`, `"{year}-{month}"`), up to
three levels. `flib check-config` refuses unknown placeholders, empty parts,
`.` or `..`, `:` or `\`, and anything that would not make a plain folder
name.

**When a value is unknown** (no issuer named, or nothing usable left of it),
the file stops at the deepest folder known: with `{year}/{issuer}` it goes to
`Finance/2026/`, with `{issuer}/{year}` to `Finance/` itself. The same
happens when something that is not a real folder (a file, a symlink, a
folder only in iCloud) already has that name.

**The same company, the same folder.** Before making a new issuer folder,
flib compares the name with the folders already there, ignoring case,
accents (in either Unicode form), punctuation, a leading "The", a trailing
`.com` or `.com.br`, and legal forms that are never ordinary words (`Inc`,
`Ltd`, `LLC`, `Corp`, `GmbH`, `PLC`, `Ltda`, `EIRELI`, `S.A.`, `SpA`...):
`Acme Inc.`, `ACME`, `Acme Ltda`, `Ácme` and `acme.com` all join an `Acme`
folder, `Banco Inter SA` joins `Banco Inter` (a trailing `SA` after another
word; `SA` alone is a name), and `Power Co` and `PowerCo` share one. `Co`,
`Company`, `Spa` and `Me` can be part of a name (`Lotus Spa`, `Pay Me`), so
they count. Only then, and only when a single folder is that
close, it accepts a slip: a doubled letter (`Fabrikkam`) or two letters
swapped (`Nortwhind`), never with digits. One different letter
(`Acme`/`Acne`), a plural (`Morgan`/`Morgans`), a different number or an
extra word (`Acme Bank`) is another company, with its own folder.

The folder keeps the spelling it was made with, so rename it by hand if you
prefer another (a change of case only is fine: the files stay and the index
follows); flib matches the new name from then on. With `{year}/{issuer}`,
a company new to a year gets the spelling its folder has in the other
years (only year folders count, never one of your own, and only the same
name, never a slip). If two folders hold the same company, a file already in one of them
stays there; a new file goes to the one flib made, else the oldest. An
existing folder is used as it is spelled on disk, even with a `:` (what
Finder shows as `/`). A layout folder that cannot take a file never leaves
it behind: it goes to the category's own folder, and the plan says why.

New files get a name that is free in their own folder (`-2`, `-3`...);
nothing is ever replaced. Review, duplicates and sensitive files never get a
layout.

### Applying a layout to files already organized

Adding or changing a layout affects new files. To file the library into it:

```sh
flib reprocess --apply-layout --simulate            # what would move; moves nothing
flib reprocess --apply-layout                       # asks, then moves
flib reprocess --apply-layout --category Finance    # one category only
```

It does not ask the AI: the date and issuer come from the index (stored with
each answer since this feature, or taken from a recent cached answer).
Organized files whose layout needs an issuer the index does not have stay
where they are; `--ask-issuer` asks the AI for it, showing the estimated cost
and asking first, and keeps only the issuer and date from the answer (the
category and name stay). Names are kept; a taken one gets `-2`.

It never moves a file you placed yourself: one not where flib last put it
(moved or renamed by hand, into a sub-folder, out of one to the category's
top, then `flib reconcile`) stays, and the plan says "you moved or renamed
it". To hand such a file back to flib, move it with `flib fix` (for example
`flib fix FILE --category Finance`, which puts it at the category's top for
the next `--apply-layout`), or move it into its layout folder yourself and
run `flib reconcile`.

The year and month a file is filed under are kept in the index from the
moment it is filed (the document's date, else its download date, else the
day it was filed) and used ever after: copying the library to another disk,
restoring it from a backup or travelling to another time zone never moves a
file. For files organized before this was kept, `--apply-layout` takes it
once from the layout folder the file is already in, or else resolves it as
above, and stores it with the run (`flib undo` removes it). A date or issuer it takes from a cached answer is
written to the index with the run, so the file stays where it went once the
cache has expired, and `flib undo` takes them back out. A category whose layout you removed gets the files flib had put
in its sub-folders back to its top folder. Everything moved is one run:
`flib undo` puts it all back (the issuers and dates `--ask-issuer` learned
too); the emptied sub-folders are left in place. It cannot be combined with
`--rename-category` or `--remove-category`: migrate first, then apply the
layout.

Plain `flib reprocess` follows layouts too, since it files by the current
rules: each file it reclassifies goes to its layout's folder (asking the AI,
with its cost), except one you placed in a sub-folder yourself, which stays
unless its category changes.

**Elsewhere.** `flib reconcile` treats a file you moved between sub-folders
of the same category as still in that category (no correction recorded); a
move into another category's folder, at any depth, is a correction.
`flib fix --name` renames a file in its own sub-folder; `flib fix --category`
moves it to the top of the new category, and `--apply-layout` then files it.
Search, `--category` filters, Finder tags, duplicates, `flib status` and the
MCP tools work the same at any depth.

## Watch mode: organizing files as they arrive

```yaml
schedule:
  mode: watch                     # daily (the default) | watch | both
  watch_min_interval_minutes: 5   # at most one start every 5 minutes (1 or more)
  watch_quiet_seconds: 300        # an item must not have changed for this long
  watch_settle_minutes: 10        # give up on a download after this long without change
  watch_fallback_minutes: 30      # also look every 30 minutes (0 = never)
limits:
  max_cost_usd_per_day: 3.00      # automatic runs only; left out: 3 x max_cost_usd_per_run
  max_files_per_day: 1000         # only for a provider with unknown prices; left out: 5 x max_files_per_run
```

`flib schedule` (or `flib schedule --mode watch`) installs a second launchd
agent next to the daily one: macOS starts flib whenever one of the input
folders changes. What happens then:

- **Only the top level is watched.** macOS reports a change in the folder
  itself (something added, removed or renamed in it, or written to), not
  inside its sub-folders. flib only organizes the top level anyway.
- **Bursts are one run.** macOS starts flib at most once every
  `watch_min_interval_minutes`; changes in between are folded into the next
  start. Ten files arriving together are one run.
- **Files are taken only once they are done.** Items whose name says they
  are still downloading (`.crdownload`, `.part`, `.download`...) and items
  Finder is still copying in are left alone in every mode. A watch run also
  takes only items that have not changed for `watch_quiet_seconds` (5
  minutes, never less than `limits.stable_seconds`), because a file written
  slowly under its final name (a torrent, `curl -o`, `scp`, a copy from a
  slow disk or share, a screen recording) often pauses for longer than a
  minute. A run that sees such items waits for them, looking only at those
  items (every 15 seconds, every minute after the first five), and then runs
  once for everything ready. It goes on waiting while they keep changing, up
  to 6 hours, and gives up only after `watch_settle_minutes` without any
  change (a stalled download). macOS does not start flib when a file merely
  stops changing, nor for changes made while flib runs, so this wait is what
  files a long download a few minutes after it ends. Anything still missed
  is picked up by the agent's fallback start every `watch_fallback_minutes`
  (30; `0` turns it off), which, like any start with nothing new, does
  nothing at all. Right before a move flib checks once more that the item
  has the same size, modification time and inode, and for a file up to 32 MB
  the same content; a larger file whose digest came from flib's memory is
  hashed again by the run itself. Otherwise it is left for the next run. What flib cannot see: a
  writer that pauses for longer than `watch_quiet_seconds` and then goes on,
  or one that keeps a large file's size and modification time while changing
  it. Whether another app still has a file open for writing cannot be told
  cheaply and reliably, so flib does not try. A file whose modification time
  is more than five minutes in the future (a wrong clock, some archives, a
  share whose server clock is ahead) says nothing about when it changed: it
  is taken once its size and modification time stayed the same for the
  quiet time (a daily run takes it at its next run), and the run log says
  so.
- **Nothing to do costs nothing.** A start that finds nothing new exits at
  once: no run, no entry in the run history, no AI call, no log. flib's own
  moves out of an input change that folder, so macOS starts flib once more a
  few minutes after each run; that start finds nothing new and ends. Files a
  watch run left where they were (a failure to retry, files over a cost cap)
  are not new again until the next day, when one watch run tries them once
  more; a new arrival, or the daily run, takes them earlier. A new arrival
  always goes ahead of them, however many are left. `flib status` says how
  many wait and why ("daily cap reached", "provider failing"). Items too
  recent for their input's `min_age_days` are not waited for either. Each
  item is hashed once, not at every look: digests are kept by device, inode,
  size, modification, change and creation times (in the index for the items
  that stay, without names), so any rewrite gives a new key.
- **Never two at once.** A second watch start while one is going on exits at
  once. A watch run that finds another flib run going on waits up to 10
  minutes for it to end, and then gives way quietly; the daily run waits the
  same way, so a watch run never costs you the day's run, its summary or its
  reminders. Runs by hand still stop at once with "another flib process is
  running". Files protected while the Mac is locked are left to the watch
  agent's fallback start, which files them within `watch_fallback_minutes`
  of the unlock; with the fallback off, to the daily run's unlock catch-up
  when the daily agent is loaded
  ([troubleshooting](troubleshooting.md#a-file-is-skipped-as-locked));
  otherwise the watch run waits for the unlock itself, and no new watch run
  starts until it ends. Only one process ever waits for the unlock.
- **A cost cap per day.** The per-run cap still applies to each run. On top,
  the day's automatic runs (daily and watch together, by the cost recorded
  for each run since local midnight, even a run the provider stopped) stop
  at `limits.max_cost_usd_per_day`: once it is reached they send nothing
  more to the AI until midnight. Files of inputs with a fixed category,
  answers already in the cache and duplicates still go ahead, as they cost
  nothing. The first run that reaches the cap says so in its log, and `flib
  status` shows it for the rest of the day, with what automatic runs cost
  today. `flib run` by hand is never held back by it. A remote
  `openai-compat` server with no prices configured records no cost, so no
  cost cap can stop it: `flib schedule`, `flib check-config` and `flib
  doctor` warn about it, `flib status` says "cost unknown", and automatic
  runs then see at most `limits.max_files_per_day` items a day instead.

`mode: both` keeps the daily run as a safety net; the periodic summary and
the due reminders come only after the daily run, never after a watch run,
which is why `flib schedule` and `flib init` suggest `both`. `flib schedule
--status` shows the mode, the folders watched and the interval; `flib
status` and `flib doctor` show the mode, the last watch run and today's
automatic cost. The folders to watch are fixed when the agent is installed:
after you change `inputs`, `flib schedule --status`, `flib status`, `flib
check-config` and `flib doctor` ask you to run `flib schedule --refresh`.
Watch runs are recorded with the trigger `watch` and keep their own run logs
(`watch-run-*.log`, the newest 100, so they never push out the daily runs'
logs); `watch.out.log` in flib's logs folder is trimmed once it passes about
1 MB. Nothing is ever written into a watched folder. `flib schedule
--refresh` and `flib schedule --at` keep the mode installed (unless the
configuration sets `schedule.mode`); `--mode` changes it (the agent of the
other mode is removed); `flib schedule --remove` stops both.

## Correcting files

```sh
flib fix ~/Downloads/Organized/Other/scan.pdf --category Finance
flib fix ~/Downloads/Organized/_Review/document.pdf --category Contracts --name lease-2026
```

The file moves (a taken name gets `-2`, but changing only the capitalization
renames in place; a missing extension is kept), the index, search and the
Finder tag follow, and `flib undo` reverts it like a run. If flib is
interrupted mid-fix, the next command finishes the whole fix or leaves the
file as it was.

Corrections, these and the moves or renames `flib reconcile` finds, are sent
as examples with later requests, so the classifier stops repeating the
mistake. `provider.max_examples` caps how many (10 by default, up to 50; `0`
sends none). [Privacy](../README.md#privacy) says what an example contains.

## Changing your rules

New files follow the configuration as it is when they arrive. After you
change categories, their hints, the naming rules or the model, file what is
already organized again:

```sh
flib reprocess --simulate          # classify and show what would move; moves nothing
flib reprocess                     # asks before sending, then before moving
flib reprocess --category finance --since 2026-01-01 --rename
```

It works on files in the library only (organized or in review), never on the
input folders, and never sends a file flagged sensitive, nor the content of
one whose current or original name matches `never_send`. Before anything is
sent it says how many files the cache already answers and estimates the cost
of the rest, and asks; the per-run cost limit
(`limits.max_cost_usd_per_run`) still applies, and running it again goes on
where it stopped, the cache answering what was done. Then it shows the plan
and asks again (`--yes` answers both). `--simulate` still sends the files
(after the same question, so it can cost money; the answers are cached for
the real run) and stops before moving anything.

A file whose answer did not change stays as it is; one the AI is now unsure
about stays in its category; one you put in a sub-folder of its category
stays there unless its category changes. File names are kept: only
`--rename` applies the current naming rules to them too. The filters are the
ones of `flib search` (`--category`, `--since`, `--until`, `--ext`). The
questions are asked before flib takes its lock, so a scheduled run is never
held up by an unanswered one; each file is checked again before it moves.
Only one `flib reprocess` runs at a time. `flib undo` also gives back the
descriptions and tags it replaced.

Your corrections win: a category or a name you set with `flib fix` (or by
moving the file and running `flib reconcile`) is kept, unless you add
`--override-corrections`. Everything moved is one run: `flib undo` puts the
whole reprocess back, Finder tags included.

### Renaming or removing a category

Renaming or removing a category needs one explicit step. Edit the
configuration first; every command then warns that organized files still use
a category it no longer has, and plain `flib reprocess` refuses until you
say what happened:

```sh
flib reprocess --rename-category Docs Documents  # move Docs/ into Documents/, no AI
flib reprocess --remove-category Misc            # reclassify Misc among the others
flib reprocess --remove-category Misc --to-review  # or send all of it to review
```

A rename keeps file names (a taken one gets `-2`), and your corrections to
the old name now point at the new one; a rename that only changes the case
changes the index and the Finder tags. Files the AI is unsure about after a
removal go to review. `flib undo` reverts a migration like any reprocess,
the index side included (category names in rows and corrections), and makes
the emptied folder again if you deleted it.

After a reprocess that changed nothing (every move failed or was skipped),
plain `flib undo` refuses instead of undoing an earlier run; name that run
with `--run ID` if you meant it.

### Cleaning stored descriptions

Descriptions written before flib kept identifiers out of them (see
[Privacy](../README.md#privacy)) may hold a phone number or a document
number the AI copied from a picture. Clean them without asking the AI again:

```sh
flib reprocess --scrub-descriptions --simulate   # list the files it would change
flib reprocess --scrub-descriptions              # asks, then cleans; no AI, no cost
```

It replaces what `privacy.descriptions: redact` replaces in new answers, in
the descriptions, tags and name-choice reasons of every file, and in the
classification cache. Nothing moves and no file is renamed. Full-text search
follows at once; the embeddings of the changed files are removed and made
again by the next run. It is one run: `flib undo` puts the old text back.

## Amounts, dates and issuers

With each answer the AI fills in what the document itself states (see
[Amounts, dates and issuers](../README.md#amounts-dates-and-issuers)): its
kind, issuer, amount and currency, document date, due date and the period it
refers to. A field the document does not state stays empty; the prompt asks
for that explicitly, and for the amount to be the document's total or amount
due, never a sum the AI made, a balance, a limit or a unit price.

How flib reads what the AI wrote:

- **Amounts.** `1.234,56`, `1,234.56`, `1 234,56` and `1'234.56` are the same
  amount. A single separator followed by exactly three digits is a thousands
  separator (`1.234` is one thousand two hundred thirty-four), except in a
  currency with three decimals (`KWD`). A symbol or code written with the
  amount names the currency (`R$` is BRL, `US$` USD, `€` EUR); a bare `$`
  names none, so the `currency` field must. An amount without a currency, a
  negative one, one with more decimals than its currency has, or one that
  looks like an identifier (nine or more digits in a row, a card number) is
  dropped. The index keeps it as an integer number of cents (of the
  currency's minor unit), so totals are exact.
- **Currencies** are ISO 4217 codes.
- **Dates** are `YYYY-MM-DD` (a time after them is ignored); an impossible
  date is dropped.
- **Periods** are `YYYY-MM` or `YYYY`; `2026-9`, `09/2026` and a full date
  are read as their month.
- **Kinds** come from a fixed list; `utility bill` is read as `bill`, a
  plural as its singular, anything else is dropped.

None of this is an identifier: the privacy scrub (see
[Privacy](../README.md#privacy)) leaves amounts and dates in descriptions,
and document, account and ID numbers never get a field of their own.

`--period` and `--sum --by month|year` use the period a document refers to,
else the month of its document date: a receipt states when it was paid, not
which month it pays for. A bill for December 2025 issued in January 2026
counts for 2025. `--sum` counts organized files and files in review, never
duplicates or sensitive files, adds up each currency on its own, and says how
many matching files state no amount.

### Filling in the fields of files already filed

Files filed before flib kept these fields have none. Ask for them:

```sh
flib reprocess --fill-fields --simulate        # shows the cost, asks, lists what it would record
flib reprocess --fill-fields                   # asks again before recording
flib reprocess --fill-fields --category Finance --since 2026-01-01
```

Only files with no fields are sent (`--category`, `--ext`, `--since` and
`--until` narrow them further), and the cost is shown before anything is
sent; answers already in the cache cost nothing, so a simulation followed by
the real thing pays once. From each answer flib keeps only the fields, and
only where the index has none: category, name, description and the file's
place stay as they are, and nothing moves. It is one run: `flib undo` clears
what it recorded.

## What is due

`flib due` lists the documents whose due date the AI read in them, soonest
first, with the due date, how far away it is ("in 3 days", "2 days ago"),
the amount, the issuer, the kind and where the file is, then the totals per
currency of the open ones. It only reads the index: no lock, no AI, and it
works before the first run.

- **Nothing is guessed.** Only a due date the document states counts; a
  document without one is never listed, with any option.
- **Which kinds.** `due.kinds` (bills, invoices, statements and taxes by default). A due
  date on any other kind, or on a document of unknown kind, is shown only
  with `--all`; `--kind` names the kinds you want, listed or not. Organized
  files and files in review count; duplicates and sensitive files never do.
- **The window** is counted in calendar days of your time zone: a bill due
  tomorrow is "tomorrow" from midnight to midnight, also on the days the
  clock changes. By default it runs from 30 days ago to 7 days ahead;
  `--within 10d`, `2w` or `1m` (calendar months, so from the 31st one month
  later is the month's last day) moves the far end, up to a year. `--overdue`
  lists every open document whose due date has passed, however long ago. A
  document due today is not overdue.
- **Done.** `flib fix PATH --done` marks a document paid or done and
  `--not-done` opens it again. Each is journaled as its own small run, so
  `flib undo` (or `--run ID`) reverts it; nothing moves. Done documents leave
  the list and the totals; `--all` shows them, marked `done`. With
  `due.done_tag: Paid`, a file with the Finder tag `Paid` (any case or
  color) counts as done too, for as long as it has the tag; nothing is
  recorded, so removing the tag opens it again.

`flib status` adds a line such as "Due: 3 documents within 7 days, 1 overdue"
(the default window), and `--json` the same counts.

**The reminder.** After a scheduled run, the [periodic
summary](../README.md#staying-on-top-of-it) also counts the open documents of
`due.kinds` that are due within `due.remind_days_before` days (3 by default)
or overdue (up to 30 days ago): "1 document is due within 3 days, 1 document
overdue". The rule, so it never nags:

- a document no summary counted yet makes the summary come now, whatever
  `summary.every_days`; it is recorded with its due date, so it never does
  that again (a reprocess that reads another due date may remind once more);
- a reminder alone carries only the due counts and does not restart
  `summary.every_days`; a summary that comes for files waiting in review
  carries the due counts too;
- documents marked done are never counted; `due.remind: false` turns the
  reminder off.

Like the rest of the summary, the notification holds counts only: never a
name, an amount or an issuer, because it shows on the lock screen.

The MCP server has the same list as `list_due` (read-only, same filters), so
an assistant can answer "what is due this week?".

## What vision costs

Estimated on the default model (Claude Haiku 5.5, US$0.10 per million input
tokens and US$0.50 per million output tokens). An image costs about
width × height / 750 input tokens:

| What is sent | Image tokens | Whole request, estimated |
| --- | --- | --- |
| Photo, 4:3, shrunk to 1568 × 1176 | ~2,460 | ~US$0.0005 |
| Screenshot, 16:10, shrunk to 1568 × 980 | ~2,050 | ~US$0.0005 |
| Scanned PDF, 3 pages | ~1,600-2,500 per page | ~US$0.001 |

"Whole request" adds about 1,000 tokens of instructions and metadata and a
few hundred output tokens; an image with words in it also carries its
recognised text (a few dozen to a few hundred tokens). These are estimates from the published formula,
not measurements; what counts toward `limits.max_cost_usd_per_run` is the
usage the API actually bills, so the cap holds either way. Roughly 2,000
photos fit in the default US$1 cap. Lowering `vision.max_image_px` lowers the
cost quadratically (1024 px: about 40% of it).
