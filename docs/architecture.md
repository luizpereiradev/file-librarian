# Architecture

file-librarian is a linear pipeline of six stages. Only one stage, the
classifier, uses AI. Each stage is a module with typed input and output
(`models.py`), so each can be tested and replaced on its own.

```text
 input folders
      │
 1. Scanner      scanner.py,       new, stable files (by sha256); folders/.app as one unit;
      │          hashing.py        iCloud cloud-only items skipped from lstat, never read;
      │                            files locked by Data Protection skipped as "locked";
      │                            duplicates found here, never classified
 2. Extractor    context.py,       FileContext: metadata, download origin, text excerpt;
      │          extractors/,      secret detection here (sensitive.py): sensitive files stop
      │          sensitive.py      at this stage and stay where they are; images and scanned
      │                            PDFs are read by local OCR (macOS Vision), whose text is
      │                            scanned too; with vision on, those with little text are
      │                            marked visual
 3. Classifier   classifier.py,    Classification (schema-validated): category, name, description,
      │          providers/        tags, confidence; rate limits, retries, timeouts, cost cap;
      │                            a visual file's downscaled image or first pages
      │                            (extractors/visual.py) go to providers that read them
 4. Planner      planner.py        Operations: naming pattern, rename policy, collisions,
      │                            low confidence → review folder.   `flib simulate` stops here
 5. Executor     executor.py,      record "planned" → move → mark "done"; crash recovery;
      │          fsops.py          another volume: copy → verify → place → delete, each journaled
      │
 6. Indexer      db/, search.py,   file record, FTS index, category as Finder tag;
                 finder_tags.py,   with semantic search on, vectors for new and changed rows
                 embeddings.py
```

`pipeline.py` chains the stages for `flib run` and `flib simulate`. A real run
holds the single-instance lock (`lock.py`) from its first read of the index to
its last write, and first finishes any move a crash interrupted.

## The one rule

**The model never gets access to the file system.** Stage 3 returns data;
stage 4 decides whether that data is valid and what it means on disk. A
category not in the configuration, a malformed answer or a low confidence
never becomes an action: the file goes to the review folder. Names are
sanitized and destinations are always inside the configured category folders,
which is also the defence against text inside a file trying to instruct the
model.

## Safety guarantees

- Nothing is deleted and nothing is overwritten. The only removal is the
  source of a cross-volume move, after the copy's hash is verified (see
  below).
- Every operation is written to `operations` as `planned` before it runs, so a
  crash mid-run is recoverable and every run can be undone. Recovery looks
  for the file under the journaled name, spelled exactly (a case-insensitive
  volume would otherwise answer for `A.pdf` with `a.pdf`).
- A `flib fix` is all or nothing: its journal entry also records the status
  and category it gives the file, and only becomes `done` in the same
  transaction that updates the index and records the correction. After a
  crash, recovery finishes the whole fix (index, correction, Finder tag) if
  the file reached its destination, and otherwise fails the entry and leaves
  the file's row untouched.
- A `flib reprocess` move (RF-37) is all or nothing in the same way: its
  entry, in a run with trigger `reprocess`, records the status and category
  the file gets, and the file's row is not touched until the file is at its
  destination; then the row, the entry and the Finder tags change together.
  After a crash, recovery finishes it if the file got there (including a
  cross-volume copy finished by its phase) and otherwise fails the entry and
  leaves the row as it was: the file is still in its old folder.
- A category `layout` (#86) only adds sub-folders below the category's own
  folder, at most three, each one plain name (or a real folder already there,
  as the disk spells it; checked by the planner) made with `fsops.ensure_folder`, which refuses a
  symlink on the way. A place already taken by something that is not a real
  folder stops the layout there. `flib reprocess --apply-layout` moves
  through the same journaled `relocate` as any reprocess, never asks the
  provider unless told to, and moves only `organized` files flib placed: a
  file not where its last journaled move put it is the user's, at any
  depth, and so is one deeper than its category folder unless the chain of
  its journaled moves shows flib put it there. Values it takes from the
  cache are written to the index (journaled), so the next run finds the
  same place from the index alone.
- `flib undo` walks a run's operations in reverse, checks each file's hash, and
  skips (and lists) files that were edited or whose original path is taken.
- A move to another volume (RF-33), where a rename is impossible, is copy,
  verify, delete (`fsops.copy_move`), and so is an undo back across volumes.
  The destination volume must support `RENAME_EXCL`; exFAT and FAT do not,
  so nothing is copied there: a probe (an empty folder renamed in the
  library, or in the nearest folder above it while there is none yet) fails
  the item with a clear message, counting an attempt, before any folder is
  created there. A locked item (Finder's "Locked", `uchg`), or a folder with
  anything locked inside, is refused the same way before copying: flib
  would otherwise have to delete a file you locked, and a rename on one
  volume refuses it too. The copy is made with `copyfile(3)` and `COPYFILE_ALL` (a
  folder entry by entry, each folder given its own permissions last, since
  `COPYFILE_RECURSIVE` fails on a symlink inside a read-only folder) into a
  hidden staging folder next to the destination (`.flib-<key>.copy`, the key
  being the journal entry's id and a random suffix), which keeps extended
  attributes (download origin, Finder tags), ACLs, permissions, flags and
  dates. It is flushed with `F_FULLFSYNC` and verified: the copy's SHA-256
  must equal the indexed one (for a folder unit, every file's SHA-256 and
  symlink target must match the source's, and the source's manifest the
  indexed one), and the source must not have changed while it was copied
  (inode, size, modification time). Only then is it renamed into place with
  `RENAME_EXCL`, and the source checked once more, renamed aside
  (`.flib-<key>.delete`, put back if what was renamed is not what was
  checked) and deleted, read-only folders included. Deleting walks open
  folder descriptors (`O_NOFOLLOW`, names relative to them), so a folder
  swapped for a symlink meanwhile cannot lead it outside; only flib's own
  staging and trash paths are deleted this way. A source that changed after
  the copy is kept, with a warning.
  `copyfile(3)` was chosen over `shutil.copy2` because it copies every
  extended attribute, ACLs and file flags, as Finder does. A full disk, a
  failed copy or a mismatch removes the staging folder (a cleanup error is
  logged, never reported in place of the real one) and leaves the source
  untouched; the item fails. A folder unit copied to a file system with
  coarser times (HFS+) gets a different manifest there, so its index row
  takes the new digest, in the same transaction that records the move as
  `done`, and undo and reconcile still recognise it.

  The journal entry records each phase in `copy_phase`, committed with
  `F_FULLFSYNC` so it is on the disk before a source is deleted (only these
  commits pay for it). Recovery decides from the phase and from what is on
  disk. The devices of the copy's two folders are recorded when it starts
  (`copy_src_dev`, `copy_dst_dev`). If a folder cannot be listed and the
  nearest existing folder above it is on another device, its volume is
  unmounted: recovery decides nothing, the entry stays as it is, with a
  warning, and is finished once the volume is back. A folder deleted from a
  volume that is still there is simply absent, and recovery goes on (a
  category folder deleted mid-copy rolls the move back, the source
  untouched). An entry that fails to recover is logged and left; it never
  stops a run.

  | Phase at the crash | What recovery does |
  | --- | --- |
  | `copying` (copy not yet verified) | Source still there: delete the staging folder and fail the item; the source is untouched. Source gone and the destination or the staged copy holds the indexed content (a later phase was lost, or the source was deleted by hand): finish from there. Otherwise leave everything alone. |
  | `copied` (verified, being renamed) | Staged copy there: rename it into place, then remove the source. Name taken meanwhile: roll back (the staging folder is deleted only while the source is still there). Copy already at the destination: check it holds the copied content, then remove the source. |
  | `placed` (in place, removing the source) | Check the destination holds the copied content, then finish removing the source (only if it still has the copied content; otherwise keep it, with a warning) and what is left of `.delete`. If the copy is gone from the destination or is not ours, a source already moved aside is put back instead. |

  For an undo the entry stays `done` while its `copy_phase` is set; a
  rolled-back undo leaves the item in the library, still undoable. A
  verified copy is never deleted unless its source is still in place.
- Nothing that exists only in iCloud is read, hashed or listed, since that
  would make macOS download it. The scanner, `flib reconcile` and
  `flib inspect` look at the `lstat` flags first (`hashing.is_dataless`). A
  scanned item only in iCloud is skipped; `reconcile` does not count one as
  a match, does not walk into a cloud-only folder, and leaves the row of an
  indexed item that is at its recorded path but only in iCloud unchanged
  (it is there, so not `missing`); `inspect` reports it instead of hashing it.
- Files readable only while the Mac is unlocked (Data Protection class A or
  B; `protection.py`) are never taken for broken, changed or gone. When a
  read fails with `EPERM`/`EACCES`, `protection.is_locked_out` reads the
  file's class with `getattrlist(ATTR_CMN_DATA_PROTECT_FLAGS)` (metadata
  only; it works while the Mac is locked). The scanner then skips the item
  as `locked`, not `unreadable` (no attempt counted, no Full Disk Access
  hint); while the screen is locked (`protection.screen_locked`, the window
  server's session dictionary, else `ioreg`) a folder unit with such a file
  inside waits as a whole. On the library side `hashing.check_content`
  answers "cannot tell" (`None`) for such a file, and `reconcile` (reported
  as `locked`), the duplicate check, `undo`, `fix`, `reprocess` and crash
  recovery leave it as it is. flib never changes a file's protection class.
  Tests replace both seams (`fake_protection` in `tests/conftest.py`).
- Duplicates (RF-21) are moved, never deleted. The scanner calls an item a
  duplicate when its content (SHA-256, or a folder unit's manifest digest)
  equals that of another item going ahead in the same run (the original is
  the one without a copy suffix, then created first, then with the shortest
  name), or that of a file filed in the library (organized or in review)
  that is still at its recorded path with that content. The library copy is
  checked first (`reconcile.content_at`, which answers "absent" only when
  the folder that should hold it is there and readable, and "cannot tell"
  for anything only in iCloud, unreadable or on a volume that is not
  mounted); when it cannot tell, the item waits. A filed file that is not
  where flib left it (renamed, moved, edited or deleted by hand, dragged back
  to Downloads) is never assumed gone: the item is skipped with a note to
  run `flib reconcile`. Only a content whose row reconcile marked `missing`
  is filed again from a new copy, which takes over that row; the move is
  journaled on the row, and the row changes only once the item is in place
  (a failed move, a failed classification or a sensitive file leaves it
  untouched; a failure counts toward `max_attempts` on that row, without
  changing its status, and `flib status` lists it as failed). The record the
  row gets is journaled with the move (`operations.pending_record`), so
  recovery after a crash writes the new classification. `flib reconcile`
  makes this loop work: in an input folder it accepts a match for a file
  flib moved only if it is that very file (the device and inode journaled
  with the move; a rename keeps them), so a copy downloaded again leaves the
  row `missing` instead of being taken for the original. Duplicates are not classified
  (no provider call, no cost) and not counted in `max_files_per_run`. The
  planner sends each one to `output_root / duplicates_folder` under its own
  name (`-N` if taken), after the other items; a copy of an item of the same
  run follows it, so if the original stays (sensitive, failed, left for
  later) the copy stays too and nothing is recorded about it. The executor
  checks the original once more right before moving, then moves the copy
  like any item (journaled, recovered after a crash, copied and verified
  across volumes, undoable), with no Finder tag, and gives it a row of its
  own: status `duplicate`, `duplicate_of` the original's row. A copy put back
  by `flib undo` is left where it is by later runs. `duplicates: leave` turns
  all of this off: duplicates are skipped and stay where they are. The
  duplicates folder is inside `output_root`, so it is never scanned, and it
  is never a category. `flib reconcile` never looks for a duplicate by
  content (a copy is indistinguishable from its original) and never looks
  inside the duplicates folder, nor in any library folder that holds a
  duplicate (a duplicates folder used before a rename), nor at a duplicate's
  recorded place, for anything else: a duplicate gone from its
  place becomes `missing`, and one whose original went missing is listed as
  orphaned, never promoted to original on its own. `flib reprocess` and
  `flib fix` leave duplicates alone; search (CLI and MCP) leaves them out
  unless asked (`--duplicates`, `include_duplicates`), and `flib inspect`
  and `get_file` say what a duplicate is a copy of.
- One writer at a time: the scheduled run, a manual run and MCP write tools
  share a single-instance lock. The database runs in WAL mode so MCP reads
  never block a run.

## Known limits

Found in safety reviews (the first one of M1) and accepted for now:

- **Same-user races.** Between checking a category folder and renaming into
  it, another process running as you could swap the folder for a symlink.
  Closing that needs `renameatx_np` relative to a directory opened with
  `O_NOFOLLOW`; it requires an attacker already running as you.
- **One lock per database.** Two configurations with different databases
  but the same input folders are not excluded from each other. A race there
  ends in a failed move, never a lost file.
- **Environmental failures use attempts.** A move that fails because a
  category folder is a symlink, or a cross-volume copy that fails or does
  not match, counts as a failed attempt; after `max_attempts` the file is
  left alone until the index row is cleared. A full disk or quota (`ENOSPC`,
  `EDQUOT`) does not count: the item is retried every run.
- **No moves to exFAT or FAT.** Those file systems cannot rename without
  replacing (`RENAME_EXCL`), so flib refuses to copy there rather than risk
  overwriting a file; such a destination fails the item.
- **Locked items stay.** A locked item is not moved: on one volume the
  rename fails; across volumes it is refused before copying, and so is a
  folder with anything locked inside. Unlock it in Finder (Get Info) to let
  flib move it.
- **An unmounted volume waits.** A move interrupted while its library
  volume is unmounted stays unresolved (and its file is not processed
  again) until the volume is back.
- **`flib fix` stays on one volume.** A correction is a rename inside the
  library; a category folder that is the mount point of another volume
  fails the fix with nothing changed.

## Data

One SQLite file, outside the organized folders (default
`~/Library/Application Support/file-librarian/library.db`):

| Table | Holds |
| --- | --- |
| `files` | one row per file or folder unit. Identity: `sha256` (a folder unit's manifest digest, with `is_unit` 1), `extension`, `size_bytes`. Where: `original_name`/`original_path` and `current_name`/`current_path`. What: `source_url` (the download origin), `category`, `description`, `tags` (JSON array), `confidence`, and which name the model judged better (`name_choice`: `original` or `proposed`, with `name_choice_reason`; NULL when no rename was considered), and what category layouts file by (#86): the date the document refers to (`document_date`, YYYY-MM-DD) and the organisation it comes from (`issuer`), both from the answer and NULL when the model gave none or on rows filed before migration 0014. State: `status` (`organized`, `review`, `skipped`, `missing`, `failed`, `sensitive`, `duplicate`), `attempts` and `last_error` (RF-30), `provider`, `model`, `run_id`, `processed_at`. For a file in review, why (`review_reason`: `low_confidence`, `unknown_category`, `category_removed`, `moved_by_hand`) and the category the model suggested (`review_category`), both NULL outside review. For a duplicate (status `duplicate`, RF-21), the row of its original (`duplicate_of`), kept whatever its status becomes later. A content has one row besides its duplicates' (a partial unique index on `sha256`) |
| `runs` | one row per `flib run`, `flib fix` or `flib reprocess` (and each MCP `recategorize_file`), with `mode` (always `run`: simulations record none), what started it (`trigger`: `schedule`, `manual`, `mcp`, `reprocess`), `started_at`/`finished_at`, counts (`files_seen`, `files_moved`, `files_review`, `errors`, and `files_duplicates`, the duplicates set aside) and the estimated provider cost (`cost_usd`: 0 for a fix, NULL when the provider's prices are unknown and on runs recorded before migration 0010; `files_duplicates` is NULL on runs before 0013) |
| `operations` | the journal behind `flib undo`: one entry per move (`src_path`, `dst_path`, `status`: `planned`, `done`, `undone`, `failed`, `skipped`), with the device and inode moved (`src_dev`, `src_ino`, so recovery and reconcile know the very item even if it was edited), the Finder tag the move added (`finder_tag`), the phase of a cross-volume copy in progress (`copy_phase`, with `copy_key` naming its staging and trash paths and `copy_src_dev`/`copy_dst_dev`), for `flib fix` and `flib reprocess` the status and category it sets (`fix_status`, `fix_category`) and, for `flib reprocess`, the ones the file had before (`prev_status`, `prev_category`; undo gives those back), when it was undone (`undone_at`, set by undo and by the recovery of an interrupted undo), and, for a move that takes over a `missing` row (RF-21), the record the row gets (`pending_record`, JSON) |
| `index_changes` | what a run changed in the index without moving a file (a category renamed in rows and corrections, a Finder tag renamed in the journal, a new description, tags, confidence, document date or issuer, a review reason set or cleared by `flib reprocess` or `flib fix`): `target` (one of a fixed list of columns), `row_id`, `old_value`, `new_value`, `status` (`done`, `undone`). Reverted by `flib undo` with the run, and with one file by `flib undo --file` |
| `corrections` | user recategorizations and renames (`flib fix`, and those `flib reconcile` finds): `field` (`category` or `name`), `old_value`, `new_value`; the latest per file and field, while still in effect, are sent as examples and are part of the cache key |
| `classification_cache` | answers already paid for, per content (`sha256`) and `rules_key` (a hash of provider, model, effort, categories and hints, language, examples and prompt version; answers that saw an image or PDF have a key of their own), kept 7 days (`classifier.CACHE_DAYS`), so `flib run` after `flib simulate`, or `flib reprocess`, does not pay twice |
| `files_fts` | FTS5 index over `original_name`, `current_name`, `description` and `tags`, kept in step with `files` by triggers; accents ignored |
| `meta` | flib's own bookkeeping as key and value: `summary.last_at` (when the periodic summary was last written), `duplicates.first_moved_at` and `duplicates.notice_seen_at` (for the one-time note about duplicates) |
| `embeddings` | only with `search.semantic.enabled`: one vector per file for semantic search (`vector`, float32, normalized, as a BLOB), with `model`, `dimension` and `text_hash`, a hash of the embedded text (names, category, tags, description, after the model's document prefix; never content), so a changed row or model is embedded again; never for a sensitive or `never_send` file, nor for a duplicate. Searched by brute-force cosine in pure Python (`embeddings.py`), no extension |

Schema changes are new numbered files in `db/migrations/`; the applied version
is `PRAGMA user_version`. Names and paths are stored in Unicode NFC.

## Reprocessing

`flib reprocess` (`reprocess.py`, RF-37) runs files already in the library
through stages 2 to 5 again when the rules change: categories, hints,
naming, the model. It never scans the input folders.

1. **Select.** Index rows that are `organized` or in review and live inside
   a folder of `output_root` (not through a symlink), narrowed by the search
   filters. Each must still be at its recorded path with its indexed content
   (otherwise: run `flib reconcile`); one only in iCloud is not read. Rows
   `sensitive` are never selected, and a sensitive name or a `never_send`
   match under the file's original name counts like one under its current
   name.
2. **Estimate.** Answers already in the classification cache are free; the
   rest are priced from the prompt's length before anything is sent, and
   the user is asked.
3. **Classify and plan** with the same classifier (cache, cost cap,
   sensitive check, `never_send`) and planner. A file's own name is free for
   it, so a file whose answer did not change stays exactly where it is. A
   file the provider is now unsure about stays in its category; one that
   cannot be classified stays where it is. Corrections in effect are the
   user's: a corrected category is kept, a corrected name is not renamed,
   and a file with both is not even sent (`--override-corrections` lifts
   this).
4. **Apply** through `executor.relocate`: one run with trigger `reprocess`,
   journaled and recovered as above, undone as a whole by `flib undo`, Finder
   tags swapped as `flib fix` does. A new description, tags or confidence
   is written only where it differs, journaled in `index_changes` so undo
   gives the old ones back. The command first takes the instance lock just
   long enough to finish any interrupted move (`recover`), so it plans on an
   index that matches the disk. Steps 1 to 3 and both questions then run
   without the instance lock, so an unanswered question never blocks a
   scheduled run, but under a second lock of their own
   (`flib-reprocess.lock`): two reprocesses never plan, and pay, at once.
   The executor then checks each item again under the instance lock (its
   index row, and the file's inode, size and modification time) and skips
   what changed. File names are kept unless `--rename`; a file the
   user put in a sub-folder of its category stays there unless its category
   changes.

A category that organized files still use but the configuration no longer
lists is an *orphan*. Every command warns about it, and plain reprocessing
refuses until an explicit migration ran: `--rename-category OLD NEW` moves
the files to the new folder (no AI) and renames the category in the index
and in past corrections, so they stay in effect; `--remove-category OLD`
reclassifies its files among the remaining categories (unsure ones go to
review), or with `--to-review` sends them all to review without asking
anyone. The index side of a rename (rows that do not move, corrections, and
for a rename that only changes case, which is the same folder on macOS, the
organized rows and the Finder tags flib set) is written in `index_changes`
with the run before any file moves, so `flib undo` reverts all of it, even
after a crash in between; a rename run again after such a crash takes over
what the first left, so one undo reverts both. The emptied folder is left in place; undo makes a
missing folder inside `output_root` again. After a reprocess that changed
nothing, plain `flib undo` refuses rather than reach back to an earlier run.

## The MCP server

`flib mcp` (`mcp_server.py`) is another front end over the same modules, built
on the official MCP Python SDK and served over stdio. The SDK is the optional
`mcp` extra (`./scripts/install.sh --with-mcp`); only this command imports it,
and without it the command explains how to enable it:

| Tool | Reuses |
| --- | --- |
| `search_files` | `search.py`, as `flib search`; `semantic` is `--semantic` |
| `get_file` | the index row and its journaled moves, plus the download origin and iCloud-only check `flib inspect` uses (a cloud-only file is never read) |
| `list_recent` | the index: the newest organized files, optionally in one category |
| `list_review` | the index: what waits in the review folder and why (`review_reason`) |
| `get_stats` | `Store.stats()`, as `flib status`: counts by status and category, the size filed, the last run and this month's provider cost |
| `recategorize_file` | `fix.fix_file`, as `flib fix`, with run trigger `mcp` |
| `undo` | `undo.py`, as `flib undo` |

- Reads take no lock and open the database only for the call (WAL mode).
- Each write takes the instance lock for the call only, finishes an
  interrupted journal first (`recover`) and fails at once if a run holds the
  lock. An idle server holds neither the lock nor the database, so the daily
  run is never blocked by an assistant left open.
- `mcp.read_only: true` does not register the write tools at all.
- Results are metadata and model-written descriptions (scrubbed of
  identifiers by the classifier, see `privacy.py`), never file content.
  Rows with status `sensitive` have description, tags and download origin
  withheld. Paths must be absolute and must be in the index; the server
  tells the client that names and descriptions are untrusted data.

## The first run

`flib init` (prompts in `cli.py`, the rest in `onboarding.py`) writes the
configuration, then runs a trial that cannot touch real files: it copies a
sample of recent items into a temporary folder and runs `pipeline.run(...,
simulate=True)` with a configuration whose `inputs`, `output_root` and
`database` all point inside that folder, which is deleted afterwards. The
originals are only read, to copy them.

## Waiting for an unlock after a scheduled run

A scheduled run that skipped files as `locked` does not leave them for the
next day (when the Mac may be locked again). `cli.py` prints its report,
releases the instance lock and hands over to `catchup.py`: it tries to take
the waiter lock (an exclusive `flock` on `flib-waiter.lock` next to the
database) without blocking. If another process holds it, a waiter is alive
and its second pass rescans everything, so this run logs that and does not
wait; the system releases the lock when a waiter dies, so a file left behind
is simply taken, and its contents are never read. With the lock, it sleeps, asking `protection.screen_locked` every 60 seconds,
for up to `schedule.wait_for_unlock_hours` by the wall clock (time the Mac
spends asleep counts; `0` turns the wait off). When the lock state cannot be
told, it tries to `open` one of the skipped files instead. Once the Mac is
unlocked it releases the waiter lock and runs the pipeline once more with
trigger `schedule` (a second run record, whose log starts with "Catch-up
run"); if another run holds the instance lock then, it exits. `SIGTERM`
ends the wait cleanly. flib never signals another process. Each step is a timestamped line in `schedule.out.log`, and how the
wait ended is appended to the first run's log. The wait, the clock and the
sleep are injectable, so the tests run it on a fake clock.

## Status and the periodic summary

`flib status` (`status.py`, RF-40) reads what waits for the user: files in
review grouped by `review_reason` and suggested category, failed files
grouped by the kind of error (the text of `last_error` before its first
colon, since the rest can name a path) and whether the next run retries
them, files left in place as sensitive, the last run with the unreadable,
iCloud-only, not-where-left and `locked` skips counted from its log (with
what to do about `locked` ones), the next run from the installed
launchd agent, and this month's cost from `Store.stats()`. It opens the index
read-only (`mode=ro`) and takes no lock: it neither creates nor migrates the
database, so it works before the first run and during one. It names no file,
so `flib doctor` includes its output (with suggested categories that are not
configured hidden, since the model wrote them).

After a run with trigger `schedule`, `cli.py` asks `status.after_scheduled_run`
for the periodic summary. It is due when some file has been in review or
failed for `summary.min_age_days` and the last summary (`meta`,
`summary.last_at`) is `summary.every_days` or more calendar days old. Then one
line goes to the run log and, with `summary.notify`, one notification is
posted through `/usr/bin/osascript` (`status.OSASCRIPT`, swapped for a fake in
every test). The text holds counts only and reaches AppleScript as arguments
of an `on run argv` handler, never as script source. The time is recorded
before notifying, so a failure is not retried on every run, and nothing in the
summary can fail the run: errors become a warning line in the log. macOS can
drop such a notification without telling (it is attributed to Script Editor,
whose notifications may be off, and Focus modes hide it); the log line and
`flib status` do not depend on it.

## Modules

Everything is in `src/file_librarian/`:

| Module | What it does |
| --- | --- |
| `cli.py`, `__main__.py` | the `flib` command (Typer); prompts and output only, the work is in the modules below |
| `config.py`, `locales.py` | the YAML configuration, validated at startup; the defaults each `language` brings |
| `models.py` | typed values passed between stages (`FileContext`, `Classification`, `Decision`...) |
| `pipeline.py` | one run or simulation, stage by stage |
| `scanner.py`, `hashing.py`, `globs.py` | stage 1; content identity (SHA-256, folder manifests, iCloud-only check); glob matching as the Mac compares names |
| `context.py`, `sensitive.py`, `extractors/` | stage 2; secret detection; one extractor per format (`text`, `office`, `pdf`, `archive`, `mail`), plus `origin.py` (download origin), `ocr.py` with its helper `ocr.js` (local text recognition in images and scanned PDFs) and `visual.py` (images and scanned PDFs for vision); the interface is in `extractors/base.py` |
| `classifier.py`, `privacy.py`, `providers/` | stage 3; identifiers scrubbed from every answer before it is cached or stored (`privacy.descriptions`); one module per provider (`anthropic`, `claude_code`, `openai_compat`, `ollama`, and `fake` for tests), the shared prompt (`prompt.py`), the HTTP client (`http.py`) and the interface (`base.py`) |
| `planner.py`, `naming.py` | stage 4; safe file names and dates; category layouts (sub-folders by year, month or issuer) and issuer folder matching |
| `executor.py`, `fsops.py` | stage 5 and crash recovery; `fsops.py` is the only code that moves, copies or deletes your files (the rest writes only flib's own: logs, reports, the trial copies of `flib init`) |
| `db/store.py`, `db/migrations/` | the SQLite store and its numbered migrations |
| `search.py`, `embeddings.py`, `finder_tags.py` | full-text search and filters; optional semantic search; the category as a Finder tag |
| `lock.py`, `protection.py`, `catchup.py` | the instance and reprocess locks; Data Protection (files readable only while the Mac is unlocked); the wait for an unlock after a scheduled run |
| `undo.py`, `fix.py`, `reconcile.py`, `reprocess.py` | `flib undo`, `flib fix`, `flib reconcile`, `flib reprocess` |
| `status.py`, `doctor.py`, `runlog.py` | `flib status` and the periodic summary; the redacted `flib doctor` report; one log file per run |
| `onboarding.py` | `flib init`, except its questions |
| `scheduler/` | the launchd agent behind `flib schedule` |
| `credentials.py`, `keychain.py` | where an API key comes from; the macOS Keychain through `/usr/bin/security` |
| `mcp_server.py` | `flib mcp` (optional `mcp` extra) |
| `evals/` | `flib eval`: the synthetic corpus and its gold labels, the isolated simulate-mode harness and the scoring ([quality.md](quality.md)) |

## Extension points

- **Providers** (`providers/`): implement `Provider.classify(context, rules)`
  and register it. See [adding-a-provider.md](adding-a-provider.md).
- **Extractors** (`extractors/`): implement `Extractor.extract(path, max_chars)`
  for a set of extensions and register it. See [adding-an-extractor.md](adding-an-extractor.md).

## Decisions

Architecture decisions are recorded as ADRs in [decisions/](decisions/).
