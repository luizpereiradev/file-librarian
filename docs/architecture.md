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
      │                            duplicates found here, never classified;
      │                            each input's rules (only, ignore, min_age_days...);
      │                            fixed-category inputs skip stage 3 (no provider)
 2. Extractor    context.py,       FileContext: metadata, download origin, text excerpt;
      │          extractors/,      secret detection here (sensitive.py): sensitive files stop
      │          sensitive.py      at this stage and stay where they are; images and scanned
      │                            PDFs are read by local OCR (macOS Vision), whose text is
      │                            scanned too; with vision on, those with little text are
      │                            marked visual
 3. Classifier   classifier.py,    Classification (schema-validated): category, name, description,
      │          providers/        tags, confidence; rate limits, retries, timeouts, cost cap;
      │                            a visual file's downscaled image or first pages
      │                            (extractors/visual.py) go to providers that read them;
      │                            fields.py reads the structured fields (amount, dates,
      │                            period, kind) tolerantly
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
| `files` | one row per file or folder unit. Identity: `sha256` (a folder unit's manifest digest, with `is_unit` 1), `extension`, `size_bytes`. Where: `original_name`/`original_path` and `current_name`/`current_path`. What: `source_url` (the download origin), `category`, `description`, `tags` (JSON array), `confidence`, and which name the model judged better (`name_choice`: `original` or `proposed`, with `name_choice_reason`; NULL when no rename was considered), and what category layouts file by (#86): the date the document refers to (`document_date`, YYYY-MM-DD) and the organisation it comes from (`issuer`), both from the answer and NULL when the model gave none or on rows filed before migration 0014, and the date the file's `{year}`/`{month}` were taken from when it was filed (`layout_date`: the document date, else the download date, else the processing date), kept from then on so file metadata never moves a filed file. What the document states (#88, migration 0015), from the answer and NULL when the model gave none or on rows filed before: its total or amount due in the currency's minor units (`amount_minor`, an integer, so sums are exact) with its ISO 4217 code (`currency`), when it is due (`due_date`, YYYY-MM-DD), the month or year it refers to (`period`, YYYY-MM or YYYY) and what sort of document it is (`kind`, a CHECK on the closed list of `fields.KINDS`); never a document, account or ID number. State: `status` (`organized`, `review`, `skipped`, `missing`, `failed`, `sensitive`, `duplicate`), `attempts` and `last_error` (RF-30), `provider`, `model`, `run_id`, `processed_at`. For a file in review, why (`review_reason`: `low_confidence`, `unknown_category`, `category_removed`, `moved_by_hand`) and the category the model suggested (`review_category`), both NULL outside review. For a duplicate (status `duplicate`, RF-21), the row of its original (`duplicate_of`), kept whatever its status becomes later. The input folder it was taken from (`source_input`, #89, migration 0016; NULL on older rows, whose input is the folder of `original_path`), kept from then on, and whether its input's fixed category filed it with no AI (`filed_by_rule`, cleared by any correction). For a sensitive file, whether its name alone or its content made it so (`sensitive_by`: `name` is looked at again on every scan) and why, in words (`sensitive_reason`). A content has one row besides its duplicates' (a partial unique index on `sha256`) |
| `runs` | one row per `flib run`, `flib fix` or `flib reprocess` (and each MCP `recategorize_file`), with `mode` (always `run`: simulations record none), what started it (`trigger`: `schedule`, `manual`, `mcp`, `reprocess`), `started_at`/`finished_at`, counts (`files_seen`, `files_moved`, `files_review`, `errors`, and `files_duplicates`, the duplicates set aside) and the estimated provider cost (`cost_usd`: 0 for a fix, NULL when the provider's prices are unknown and on runs recorded before migration 0010; `files_duplicates` is NULL on runs before 0013) |
| `operations` | the journal behind `flib undo`: one entry per move (`src_path`, `dst_path`, `status`: `planned`, `done`, `undone`, `failed`, `skipped`), with the device and inode moved (`src_dev`, `src_ino`, so recovery and reconcile know the very item even if it was edited), the Finder tag the move added (`finder_tag`), the phase of a cross-volume copy in progress (`copy_phase`, with `copy_key` naming its staging and trash paths and `copy_src_dev`/`copy_dst_dev`), for `flib fix` and `flib reprocess` the status and category it sets (`fix_status`, `fix_category`) and, for `flib reprocess`, the ones the file had before (`prev_status`, `prev_category`; undo gives those back), when it was undone (`undone_at`, set by undo and by the recovery of an interrupted undo), and, for a move that takes over a `missing` row (RF-21), the record the row gets (`pending_record`, JSON) |
| `index_changes` | what a run changed in the index without moving a file (a category renamed in rows and corrections, a Finder tag renamed in the journal, a new description, tags, confidence, document date, issuer, layout date, amount, currency, due date, period or kind, a review reason set or cleared by `flib reprocess` or `flib fix`): `target` (one of a fixed list of columns), `row_id`, `old_value`, `new_value`, `status` (`done`, `undone`). Reverted by `flib undo` with the run, and with one file by `flib undo --file` |
| `corrections` | user recategorizations and renames (`flib fix`, and those `flib reconcile` finds): `field` (`category` or `name`), `old_value`, `new_value`; the latest per file and field, while still in effect, are sent as examples and are part of the cache key |
| `layout_folders` | the folders flib created while moving files (`path`, NFC, with `run_id` and `created_at`): of two folders holding the same issuer, the planner prefers one of these |
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

`--fill-fields` (#88) selects only the library files without structured
fields (no `kind`), asks after the estimate, and keeps only those fields of
each answer, written where the index has none: categories, names,
descriptions and places stay. The values are journaled in `index_changes`
with one `reprocess` run, so `flib undo` clears them again.

## The MCP server

`flib mcp` (`mcp_server.py`) is another front end over the same modules, built
on the official MCP Python SDK and served over stdio. The SDK is the optional
`mcp` extra (`./scripts/install.sh --with-mcp`); only this command imports it,
and without it the command explains how to enable it:

| Tool | Reuses |
| --- | --- |
| `search_files` | `search.py`, as `flib search`; `semantic` is `--semantic`; the field filters are `--issuer`, `--kind`... |
| `summarize_amounts` | `search.sum_amounts`, as `flib search --sum`: totals per currency, optionally grouped |
| `list_due` | `due.find`, as `flib due`: documents with a due date in a window, totals per currency |
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

## The interface (`flib ui`)

`flib ui` (`ui/`, #113) is a third front end: an HTTP server on 127.0.0.1
that exists only while in use, built on `http.server` and threads from the
standard library. Pages are static HTML, CSS and JavaScript in the package
(`ui/pages/`, `ui/static/`), loaded into memory at start; the JavaScript reads
a versioned JSON API (`/api/v1/...`, `ui/api.py`) built from the same modules
as the command line. Status (`status.collect`, as counts) is such a page.

Activity, Search and Due (#114) are built on the server instead
(`ui/views.py`), so they read without JavaScript: `ui/markup.py` makes every
element, and escapes every value with `html.escape`, text and attributes
alike; only what it built itself goes in as markup, and the page frame is
filled in one pass, so a file named `{{csrf}}.pdf` stays a name. They read
through the same modules as the command line and the MCP server
(`activity.collect`, `search.search` / `semantic_search` / `sum_amounts`,
`due.find`), with the index opened read-only (never created or migrated).
Activity lists the runs of the last 7, 30 or 90 days (never simulations)
from the undo journal, sensitive files as a count only, and what each run's
log says about the Mac being locked (the lines `catchup.py` writes). Search
takes its words and filters in a form body (`POST /search`, `POST
/search/export`), never in the address, which would keep them in the
browser's history. `POST /search` keeps the form on the server, under a
random id tied to the session (the latest 32, dropped with the session), and
answers 303 to `GET /search?r=<id>`: post, redirect, get, so Back and Forward
reload a GET instead of asking to resubmit a form; an id no longer kept shows
the empty form and says so. Due and Activity put only fixed choices in the address
(`?within=2w`, `?days=30`); any other value or any other name is 400, and so
is a query on `GET /search`. The search form's own fixed choices (`by`,
`limit`) are enforced the same way. Search never lists a sensitive file: it
says how many matched. Names on the pages lose their Unicode format
characters (category `Cf`), so a right-to-left override cannot make
`invoice\u202efdp.command` read as a PDF. The CSV export is an attachment
(`Content-Disposition: attachment`, `nosniff`) of up to 5000 matches,
whatever the page's limit, with a last row saying so when there were more;
a cell that starts like a formula (`=`, `+`, `-`, `@`, a tab, a carriage
return) gets a leading apostrophe. Open and Show in Finder (`POST
/files/open`, `POST /files/reveal`) take a file's index id, never a path. The
server reads the path from the index and checks it again: organized, in
review or a duplicate, never sensitive; no `..`; its real path
(`os.path.realpath`) inside `output_root` or an input folder, with no
symbolic link below that folder (a link above it, such as `/var`, is the
folder's own); still there. It then runs `open -- <real path>` (or `open -R
--`). **Open** goes further, because opening a file in macOS can run it: only
a regular file with no execute bit, with an extension in
`views.OPENABLE_EXTENSIONS` (PDF, images, plain text and CSV/JSON/Markdown,
modern Office, OpenDocument and iWork documents, mail, audio and video; not
the legacy `.doc`, `.xls` and `.ppt`, which can carry macros), that chose no
app of its own (no per-file "Open With", `com.apple.LaunchServices.OpenWith`,
which flib's moves keep; no Finder type or creator code, bytes 0 to 7 of
`com.apple.FinderInfo`) and is not an alias (`kIsAlias`). An attribute that
cannot be read counts against opening. A folder or bundle (an `.app`), a script or program (`.command`, `.terminal`,
`.tool`, `.sh`), a location file (`.webloc`, `.fileloc`, `.inetloc`), an
installer, disk image, profile, workflow or `.jar`, an HTML page, an alias
or an unknown extension gets 403, and the pages show only Show in Finder for
it. Error pages say what went wrong in general words; no `OSError` or
`sqlite3` text, which holds paths, reaches a page. None of these routes
moves, renames or deletes anything.

**Review and undo** (#115, `ui/review.py` for the pages, `ui/actions.py` for
the actions) are the first routes that move files. They never move a file
by a path of their own: each action calls what the command line calls,
`fix.fix_file` (`flib fix`), `undo.undo_run` / `undo.undo_file` (`flib
undo`) and `due.settle` (`flib fix --done`), plus `fix.fix_files`, which
does several fixes as **one** run, so one undo reverts an "Accept all";
the run's counts come from its journal, also when recovery finishes a batch
a crash cut off. Each action:

- is a POST in `POST_ROUTES` (`/review/file`, `/review/accept`, `/undo`,
  `/due/settle`), so it gets the cookie, exact-origin and CSRF checks, and
  answers 303 to a GET page with `?n=<id>`: what happened is kept on the
  server for the session (the latest 32, like searches), so no name goes in
  the address and reload, Back and Forward never act again;
- names files by index id, never a path (a `path` field is ignored), and
  carries a *pin*: a digest of the state the page showed (id, status, path,
  content hash, category, review reason and suggestion, done date; for an
  undo, the journal entries it would revert; for Accept all, the ids and
  pins of every file listed). Under the lock the pin is computed again from
  the index; a file moved, renamed, undone, deleted or handled in another tab
  or by a run since is refused ("no longer as the page showed it"), so a
  double submit, a second tab or a page left open never acts on a stale row;
- takes `lock.instance_lock` for that action only, finishes an interrupted
  journal first (`executor.recover`), as `flib fix` does, and when a run
  holds the lock says so and does nothing; it never creates the index, and
  writes only to one of exactly this flib's schema (an older one is left
  for the next `flib run` to migrate);
- validates every choice: the category must be configured (an exact name
  from the page's list, else 400); a new name goes through `fix`'s
  sanitiser (no `/`, `:` or leading `.`, at most 255 bytes); ids are ASCII
  digits; collisions follow the mover's rule (the next free `-N`, never
  overwritten);
- refuses a sensitive file without naming it, and undo of a run that moved
  a file now sensitive (`flib undo` in Terminal does it);
- reports with `flib`'s own words, the path taken out (`This file has no
  move to undo`), and general words for anything that carries an `OSError`;
- acts on the row the form named: `fix_file`, `fix_files`, `undo_file` and
  `due.settle` take the index id (`file_id`), since several rows can share a
  path; `fix_files` stops at an index error, keeps what moved journaled, and
  says how many moved, with the run to undo.

A review group in an address (`?g=`) is an HMAC of its reason and
suggestion keyed with a secret of the session, so the model's words cannot
be guessed back from the browser's history. The undo pages ask `undo.skip_reason`, the
same function `undo` acts on (gone, moved again since by a later run, edited
since, locked now, original folder gone outside the library, old place
taken), and list what will be skipped and why; with nothing to put
back there is no button, and Activity offers Undo only for runs and moves
where something can still go back (a cheap look, no hashing). Undoing a fix
deletes the corrections it recorded (`corrections.operation_id`, 0019); a
run that fixed many files the same way is one example for the classifier
(`classifier.correction_examples`), while every correction is kept for
`flib reprocess`, which protects each corrected file. Recovery trusts a
file's content at a journaled destination only when the inode cannot tell
and the source no longer holds the indexed content (gone, or something else
there now), never while the source still holds it: a name is journaled
before the exclusive rename tries it.

Undo is always two steps: `GET /undo?run=<id>` or `?file=<id>` shows what it
would put back and where (from `undo.run_undo_plan` / `file_undo_plan`,
which `undo_run` and `undo_file` use too, so the page refuses exactly what
the command line refuses, in its words), and its button posts it. Accept all
works the same way: `GET /review/accept?g=<group>` (a digest of the reason
and suggestion, never the model's words) lists the oldest
`actions.BULK_CAP` (100) files of the group, and its button posts their
ids. Review lists at most 500 files, by reason and suggestion, the biggest
group first; a missing file is shown with no buttons. Activity links Undo
for each run with something to revert, and for each move that is its
file's last one (what `flib undo --file` reverts). The pages have no
JavaScript: the Move form folds under a `<details>`.

**Settings** (#117, `ui/settings.py`, `config_edit.py`) is the one page that
changes the configuration and launchd. `flib ui` passes it the path of the
file it loaded (`Ui.config_path`); `GET /settings` reads that file, the index
(read-only, for the categories' file counts) and launchd's state, and changes
nothing. Each section is a form posted to `POST /settings/save` (with a
`section` field), `/settings/schedule` or `/settings/restore`, all in
`POST_ROUTES`, answered 303 to `GET /settings?n=<id>` like the actions. Each
form carries a *pin*, a digest of the file's bytes as the page read them.
Under `instance_lock` (a run in progress: said, nothing done) the file is read
again; a different pin (edited by hand, another tab, flib itself) is refused
and nothing is written. The change is then made **in the text**:
PyYAML, the only YAML library, cannot write a file back with its comments, so
`config_edit` splices new text at the positions PyYAML's composer reports for
each node (a scalar replaced where it stands, a missing key added at the end
of its section, a key's or item's lines removed, an item appended in the
list's style; an inline `{...}`/`[...]` collection is written again inline
as a whole). Comments, blank lines and key order around the edit stay byte
for byte, and every edit is checked: the new text must parse to exactly the
old data with that change, else nothing is written (anchors, aliases and odd
layouts end there, with "change it in the file"). The result is validated by
`config.parse_config`, the parser every command uses, **before** it is
written; its refusal is shown with the field and why. Then a backup of the
old bytes (`config.yaml.backup-<YYYYmmdd-HHMMSS>Z[-NN]`, UTC with a sequence number, so a clock or time-zone change cannot reorder them; only names of exactly that form are backups, so a file of the user's that starts the same way is never offered or deleted; created `O_EXCL |
O_NOFOLLOW`, 0600, the latest 10 kept) and an atomic write (a new temporary
file in the same folder, 0600, fsynced, renamed over the file). The file is read once more right before the backup and again right before
the rename: anything else there (an editor saved it meanwhile) refuses the
change and stays as it is. A file that is a symbolic link or a hard link, or
not the user's own plain file, is refused: replacing it would cut the link. A change whose file names another `database` than the
running server's is refused. The server goes on with the configuration it
saved. Restore (`GET /settings/restore`, then its button) shows what the
latest backup changes back and writes it the same way, after backing up the
current file, so a restore is undone the same way.

A field the user did not touch is left alone: each posted value is compared
with what the page showed after the clean-up a browser gives a one-line text
field (line breaks dropped) and the page's own (ends trimmed, NFC), so saving
one field of a category never rewrites another; a hint a text field cannot
hold (line breaks, tabs, longer than 300 characters) is shown as text, not
offered for editing. A cost cap is shown with every digit the file has (never
`1e-05`), and a value posted as shown is not written again; `due.kinds`
keeps the file's order. What changed is said field by field ("Category Work
hint: old → new", "Category Taxes: added"). The outcome, or the refusal with
what was typed, is shown inside the section that was sent, where the
redirect's anchor lands. Removing an input folder has a confirmation page
(`GET /settings/input-remove?index=<i>`), and the comment lines right above a
removed list item, at its indentation, go with it. A command the page
suggests in a refusal (`flib reprocess --rename-category ...`) is quoted with
`shlex.quote` and carries `-c <file>` when this is not the default
configuration file; text from the file or the user is never turned into
`<code>`. When the installed agents belong to another configuration file, the
page says so: Save the schedule changes nothing in launchd unless "Use this
file for the schedule" is ticked, and a change of input folders never
refreshes them. A refused install reports what launchd has loaded, or that
the agent is written but not loaded.

What the page takes is fixed: the provider and model from
`settings.provider_choices` (the current pair, Anthropic, Claude Code, Ollama
only when the file names no address or one on this Mac, an OpenAI-compatible
server only when the file already names it; an index into that list, never an
address); input folders from `settings.candidates` (Downloads, and the
Desktop with `flib init`'s rules), each checked again on the server at the
moment of the change by `folder_problem` (an existing folder of the user's,
under the home folder, not a link, not hidden, not `~/Library`, `/` or the
home folder itself, not the library or inside it, not already an input) and
then by the parser (overlaps); layouts, kinds, modes and the language from
their lists (anything else is 400); amounts and days by pattern; category
names and hints by the parser's own rules (hints: one line, at most 300
characters). A category that holds files (in the index, or a non-empty
folder, or an input's fixed category) is not renamed or removed: the page
gives the `flib reprocess --rename-category` / `--remove-category` command,
the one path that moves them. Changing the language writes what it brought
(`output_root`, the folder names, the categories, `ocr.languages`) into the
file first, so the library never moves. The schedule is installed by
`scheduler.install_for`, the function `flib schedule` uses (with the fake
launchctl in tests), after the mode and time are written; launchd's refusal
is reported with what is installed now; a change of input folders refreshes
an installed watch agent of this file. Secrets never reach a page: an API
key is only "set (in the Keychain)" or "not set" (`settings.key_state` asks
`keychain.exists`, which never reads the secret), and web addresses show their host only (`settings.host_of`); YAML
errors give a line and column, never the file's text. `POST /settings/open`
runs `open -t -- <the server's own path>`, never one from the form.

**Session.** The server binds a free port and makes a random launch token
(256 bits). The token never goes on a command line, where any account on the
Mac could read it with `ps`. `flib ui` writes a *handoff file*, an HTML page
readable only by the user (mode 0600, created with `O_EXCL | O_NOFOLLOW`, in
a 0700 folder with a random name in the account's own temporary folder,
`confstr("CS_DARWIN_USER_TEMP_DIR")`, which is on the boot volume and neither
backed up nor indexed; never next to the database, which may sit on a volume
where file modes mean nothing), and asks macOS to open that file (`/usr/bin/open`, a module constant
swapped for a fake in every test). The page posts the token to `/launch` in a
form body, so it is never in a URL, a history entry or a `Referer`, then
clears the field and removes the form; a page restored from the back/forward
cache shows "you can close this tab". Going back to it, a double submit or a
retried post therefore never sends the used token again, which would look
like theft. The folder's name starts with the owner's process id. Folders
a `flib ui` that crashed left behind are swept at start, but only those that
are provably stale (the owner is gone, or older than 10 minutes): the lock
is per database, and another `flib ui`, for another configuration, shares
the account's temporary folder. The first
use of the token (compared with `hmac.compare_digest`) burns it, deletes the
handoff file, prints "session started" in the terminal, and sets an
`HttpOnly`, `SameSite=Strict` cookie named `flib_ui_<port>`; the page holds a
per-session CSRF token. The reply is a small page whose script moves on to the
interface: a redirect would continue a chain that started on disk, which the
browser treats as cross-site, so the `Strict` cookie would not go with it.
The handoff file is also deleted when the server ends. With `--no-browser`
the user asked for a link: `http://127.0.0.1:<port>/launch?token=...` is
printed, and opening it does the same exchange as a GET, ending in a 303 to
the page. A request with `Sec-Purpose` or `Purpose: prefetch`/`prerender` (a
browser preloading a typed address) never takes part in the exchange: it is
refused without using the token. The token is read from the body of a POST
only; a query on `POST /launch` is refused.

A second use of the token by the browser that holds the session just goes to
the page. From a client *without* the session it means someone else has the
token (or used it first): the session is dropped, the server ends, `flib ui`
warns on stderr and exits with 1. A missing or wrong cookie gets the
"session ended" page, or, while the session goes on in another browser or
window, a page that says so (and how to move it). End session also expires
the cookie (`Max-Age=0`) and drops the session before the server stops; after
End session or a theft the server keeps serving its static files for 1.5 s,
so the page it just sent gets its stylesheet. A browser opening a page
(`Accept: text/html`) gets a refusal (400, 403, 404) as a page with the menu
and one sentence on what to do (a form from an earlier session: reload, or
run `flib ui` again); scripts and other clients get the plain status, and a
wrong `Host` or origin always does. The server ends
after `ui.idle_minutes` (default 10) without a request from the session (other
requests do not count), on End session (a POST), or on SIGINT/SIGTERM. One
`flib ui` runs at a time (an `flock` on `flib-ui.lock` next to the database,
apart from the instance lock, so it never keeps a run from starting); a
second one says so and exits with 0, since only the first knows its port and
token. Actions that change files or the index, when they come, take the
instance lock for that action only.

**Routes.** Every POST but `/launch` is in `server.POST_ROUTES` and is reached
only through one function that checks the exact origin, the session cookie
and the CSRF token first; a test sends every route in the table a request
without each of the three. `POST /launch` is the one exception: the handoff
file is a page on disk, whose origin is `null`, so it may come with `Origin:
null` (or none, or the server's own) and any `Sec-Fetch-Site` but
`same-site`; the launch token in the body is what is checked.

**Ending.** One thread accepts connections (a `selectors` loop, not
`serve_forever`) and reads each request there, without blocking: the line,
the headers and a body of the length they declare. Only a request that has
fully arrived (or whose client stopped sending) goes to a request thread, so
a connection that sends nothing, or a byte at a time, never holds one. A
request not complete within 5 seconds gets 408 (one that sent nothing is just
closed), a line or headers too long get 414 or 431 from that thread, and
past 128 waiting connections the oldest is dropped. The same thread checks
the idle timer.
`stop` takes no lock: it puts the reason on a `queue.SimpleQueue`
and a byte on a socket pair that wakes the loop, so it is safe from a signal
handler, even one that interrupts another (a handler that took a lock held on
the main thread would wait forever). On stop the loop closes the listening
socket, gives requests in progress a second, shuts down the sockets of any
still running, and joins every request thread before `flib ui` returns. Each
connection carries one request (HTTP/1.0, closed after the reply), so idle
keep-alive connections cannot hold the threads.

**Threat model.** What is protected: the index (names, descriptions,
amounts), the files, which Review and Undo move, and the configuration file,
launchd agents and API keys, which Settings changes or names. Who might attack: a web page
open in any browser on the Mac (cross-site requests, DNS rebinding,
clickjacking), another machine on the network, other accounts on the same
Mac (they can list processes and connect to 127.0.0.1), other programs that
listen on loopback or set cookies for it, and anything that reads logs, shell
history or the address bar. Out of scope: a process running as the same user,
which can do anything the user can (read the database, the handoff file, the
browser's cookies). How each is handled:

| Threat | Defence | Tested by |
| --- | --- | --- |
| Reached from the network | binds `127.0.0.1` only; `check_bind` refuses `0.0.0.0`, `::` and any non-loopback address, before and after binding | `test_never_binds_anything_but_loopback`, `test_a_wildcard_host_is_refused_before_binding`, `test_the_bound_address_is_checked_again` |
| Another account on the Mac takes the token | the token is never in a process's arguments: the handoff file (0600, in a 0700 random folder in the account's own temporary folder) carries it, is cleared from the page once posted, and is deleted once used or when the server ends; a prefetch never uses it; a used token that comes back without the session ends everything with a warning; "session started" in the terminal shows when the session began; `--no-browser` prints the link only to whoever asked for it | `test_the_handoff_file_is_for_the_user_only`, `test_the_handoff_file_starts_the_session_and_is_deleted`, `test_a_used_token_from_a_client_without_the_session_ends_everything`, `test_a_stolen_link_ends_flib_ui_with_a_warning`, `test_flib_ui_opens_a_handoff_file_and_ends_with_the_session`, `test_the_handoff_page_posts_the_token_once_and_keeps_nothing`, `test_handoff_folders_go_in_the_user_temp_folder`, `test_the_handoff_file_is_always_new_and_never_a_link`, `test_stale_handoff_folders_are_swept_at_start`, `test_a_prefetch_of_the_link_does_not_use_it` |
| DNS rebinding, another program on `[::1]` | `Host` must be exactly `127.0.0.1:<port>`, one header, else 421; `localhost` is refused and never printed, since another program could hold `[::1]:<port>` | `test_a_foreign_host_header_is_misdirected`, `test_two_host_headers_are_misdirected`, `test_only_127_0_0_1_is_ever_named` |
| Cross-site requests | an `Origin` other than the server's is 403 on any request (exact match, not a prefix); `Upgrade` is 400; `Sec-Fetch-Site` other than same-origin (or `none` on a GET) is 403; every POST route needs the exact `Origin` (or `Sec-Fetch-Site: same-origin`), the session cookie (`SameSite=Strict`) and the CSRF token, form field (in a form body only) or `X-Flib-CSRF` header; no `Access-Control-*` header is ever sent | `test_any_request_from_another_origin_is_forbidden`, `test_an_upgrade_request_is_refused`, `test_a_cross_site_request_is_forbidden`, `test_a_post_from_another_origin_is_forbidden`, `test_every_post_route_needs_the_cookie_the_origin_and_the_csrf_token`, `test_the_csrf_token_is_in_a_form_field_or_a_header`, `test_no_reply_ever_allows_another_origin`, `test_the_launch_post_takes_the_null_origin_and_nothing_wider` |
| A cookie planted on 127.0.0.1 | browsers share cookies across ports: any cookie of the name that holds the session counts, so a planted one cannot lock the user out; each is compared in constant time | `test_a_planted_cookie_of_the_same_name_does_not_lock_the_user_out`, `test_the_session_id_is_compared_in_constant_time` |
| Guessing or replaying the link | 256-bit token, constant-time compare, single use; a wrong guess does not burn it; `Referrer-Policy: same-origin` everywhere (a referrer goes to this server only, never to another site; the handoff page itself is `no-referrer`); never logged | `test_the_launch_token_works_once`, `test_a_wrong_or_missing_token_gets_the_ended_page`, `test_a_wrong_launch_post_gets_the_ended_page`, `test_the_token_is_compared_in_constant_time`, `test_nothing_is_logged` |
| Script injection | `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; require-trusted-types-for 'script'`; no inline script, style or handler; scripts put text in with `textContent` only; server-built pages escape every value from the index (names, descriptions, issuers, tags, search words) through `markup.el`; `nosniff` | `test_every_page_references_only_packaged_files_and_has_no_inline_code`, `test_the_policy_allows_no_inline_code`, `test_the_policy_requires_trusted_types`, `test_each_page_is_escaped_and_has_no_inline_code`, `test_search_finds_by_words_and_escapes_everything`, `test_el_escapes_text_and_attributes`, `test_the_layout_fills_its_slots_in_one_pass` |
| Names and words in the history | search words and filters go in a POST body; the address holds only fixed choices, any other name is 400; Open and Show in Finder post an index id | `test_a_search_in_the_address_is_never_run`, `test_due_refuses_choices_it_does_not_offer`, `test_activity_windows`, `test_names_the_page_does_not_offer_are_refused` |
| Opening what the page names | the id is looked up in the index; the path is checked again (status; no `..`; real path inside the library or an input with no link below it; exists) and passed to `open` after `--`; a path in the form is ignored; Open takes documents only (allow-listed extension, regular file, no execute bit, not an alias), everything else gets Show in Finder only; names lose format characters; failures get a general message | `test_open_and_reveal_ask_macos_by_id`, `test_open_never_takes_a_path`, `test_open_refuses_a_sensitive_file`, `test_open_rechecks_the_path_from_the_index`, `test_a_dotdot_in_the_index_never_leaves_the_library`, `test_a_symlinked_folder_never_leaves_the_library`, `test_open_refuses_what_could_run_and_offers_show_in_finder`, `test_open_refuses_folders_bundles_executables_and_aliases`, `test_format_characters_never_reach_the_page`, `test_when_macos_cannot_open_it_the_page_says_so_plainly` |
| Moving the wrong file, or the same one twice | actions take an index id and a pin of the state the page showed, checked again under the instance lock; a stale pin, a double submit, a second tab or a run in between is refused and nothing moves; post, redirect, get, so reload and Back never act again; categories only from the configuration, names through `fix`'s sanitiser; never overwritten; sensitive files neither listed nor acted on; a run holding the lock is said and nothing is done; only `fix`/`undo`/`due.settle` move or change anything | `tests/unit/test_ui_review.py`: `test_a_stale_row_is_refused`, `test_a_double_submit_moves_once`, `test_two_tabs_one_wins`, `test_a_run_in_progress_holds_everything`, `test_a_taken_name_gets_the_next_number_and_nothing_is_overwritten`, `test_a_sensitive_file_cannot_be_acted_on`, `test_a_form_the_page_never_sends_is_refused`, `test_a_path_in_the_form_is_ignored`, `test_reload_back_and_forward_never_act_again`, `test_getting_every_page_changes_nothing`, `test_each_action_refuses_a_forged_post_and_moves_nothing`, `test_accept_all_is_refused_when_the_group_changed`, `test_a_stale_undo_is_refused`, `tests/integration/test_ui_browser.py::test_review_and_undo_in_a_real_browser` |
| A spreadsheet running the export | `Content-Disposition: attachment` with `nosniff`; cells that start like a formula get a leading apostrophe | `test_the_export_is_an_attachment_a_spreadsheet_cannot_run`, `test_cells_that_look_like_formulas_are_quoted` |
| Changing the configuration behind the user's back, or wrongly | every change is a POST in the route table (cookie, exact origin, CSRF) with a pin of the file's bytes, checked again under the instance lock: a file changed since the page (by hand, another tab, a run) is never overwritten; validated by `parse_config` before writing; written in place (`config_edit`: only the keys asked for, comments kept, the result checked by parsing it), atomically (temporary file in the same folder, rename), 0600, after a 0600 backup; a link is never replaced; a run holding the lock is said and nothing is done; post, redirect, get, so reload and Back never write again; Restore only after a confirmation page | `tests/unit/test_ui_settings.py`: `test_a_file_changed_since_the_page_is_never_overwritten`, `test_two_tabs_the_second_is_refused`, `test_a_run_in_progress_holds_every_change`, `test_reload_back_and_forward_never_change_again`, `test_getting_every_page_changes_nothing`, `test_a_forged_post_changes_nothing`, `test_a_bad_amount_is_said_and_nothing_is_written`, `test_a_bad_category_name_is_said_with_its_field`, `test_a_linked_file_is_never_replaced`, `test_aliases_are_refused_not_half_done`, `test_restore_the_previous_settings`; `tests/unit/test_config_edit.py`; `tests/integration/test_ui_browser.py::test_settings_in_a_real_browser` |
| A secret, a path or an address from the Settings page | API keys only as set / not set, never read into a page; addresses by host only; no field takes a path, an address or a command: folders, providers, layouts and modes are fixed choices checked again on the server (`folder_problem` refuses `/`, the home folder, `~/Library`, hidden folders, links, the library); a provider choice is an index into what the file allows (no new address, Ollama only on this Mac); `open -t` gets the server's own path; YAML errors never quote the file | `test_the_api_key_never_reaches_any_byte`, `test_addresses_show_their_host_only`, `test_a_provider_choice_the_page_never_offered_is_refused`, `test_ollama_is_offered_only_on_this_mac_and_a_server_only_as_the_file_has_it`, `test_a_folder_is_never_taken_from_the_form`, `test_what_may_be_an_input`, `test_a_desktop_that_is_a_link_is_refused`, `test_open_in_a_text_editor_opens_the_servers_own_path`, `test_a_yaml_error_names_the_place_and_never_quotes_the_file` |
| Renaming or removing a category that holds files | refused, with the `flib reprocess --rename-category` / `--remove-category` command (the one path that moves its files); the language change writes its defaults into the file first, so the library never moves | `test_a_category_that_holds_files_is_renamed_only_by_reprocess`, `test_a_category_whose_folder_has_files_is_not_renamed`, `test_a_category_an_input_files_into_is_kept`, `test_the_language_never_moves_the_library` |
| Clickjacking, cross-origin windows | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy` and `-Resource-Policy: same-origin` | `test_every_reply_carries_the_security_headers` (all seven headers on every kind of reply, the 503 and malformed requests included), `test_the_turned_away_reply_carries_the_security_headers` |
| Reading files through the server | static files from an in-memory map of packaged resources, looked up by exact path: no traversal, no directory listing; no file contents are ever served; Status serves counts only (suggested categories the model wrote are masked when not configured); sensitive files are never listed with details, only counted | `test_nothing_outside_the_packaged_files_is_served`, `test_status_holds_counts_and_never_a_file_name`, `test_sensitive_files_never_appear_in_search`, `test_activity_shows_each_run_and_what_it_moved` |
| Exhausting it | request line ≤ 4 KiB, headers ≤ 16 KiB (and at most 100), POST body ≤ 16 KiB with one `Content-Length` of 1 to 5 ASCII digits, no `Transfer-Encoding`; requests read in the accepting thread, so idle or trickling connections hold no request thread; 5 s for the whole request (then 408), at most 128 waiting connections (the oldest dropped), one request per connection, at most 8 requests at once (one more gets 503), 10 s per write | `test_clients_that_never_finish_their_request_hold_no_thread`, `test_past_the_pending_cap_the_oldest_connection_is_dropped`, `test_a_head_too_long_is_refused_without_a_thread`, `test_a_body_that_never_arrives_gets_a_timeout`, `test_a_body_shorter_than_its_length_gets_a_reply`, `test_long_request_lines_and_headers_are_refused`, `test_a_large_body_is_refused_before_it_is_read`, `test_a_post_needs_a_plain_length`, `test_a_slow_client_is_dropped`, `test_a_client_that_trickles_its_request_is_cut_off_at_the_deadline`, `test_a_keep_alive_connection_gets_one_reply_and_is_closed`, `test_requests_beyond_the_thread_cap_are_turned_away` |
| Outliving its use | ends on idle (only the session resets the timer), End session or a signal; `stop` is signal-safe; every end closes the sockets, deletes the handoff file and joins all threads | `test_the_server_ends_after_the_idle_minutes`, `test_only_the_session_keeps_the_server_alive`, `test_a_signal_handler_never_waits_on_a_lock`, `test_close_cuts_off_a_request_in_progress`, `test_the_server_ends_if_its_loop_fails` |
| Referrer policy and origin checks | not `no-referrer`: under it a browser sends `Origin: null` on a same-origin form post, which the origin check refuses, so every form failed in a real browser; `same-origin` sends the real origin; `Origin: null` is still accepted on `POST /launch` only | `test_the_referrer_policy_lets_a_browser_send_its_origin_on_a_post`, `tests/integration/test_ui_browser.py` (headless Chrome, opt-in: `FLIB_TEST_BROWSER=1`) |
| Leaking through errors | GET and POST only (405, unknown methods 501); every error body is its status phrase, or for a browser's page a short page with the menu, with a status line even for a request line that makes no sense; a bug gets a plain 500 and no traceback; `Server: flib`; `Cache-Control: no-store` | `test_only_get_and_post_are_allowed`, `test_error_pages_reveal_nothing`, `test_a_bug_gets_a_plain_500_and_no_traceback` |

Known limits: the cookie is not `Secure`, since there is no TLS on loopback.
Browsers keep cookies per host, not per port, so the session cookie is also
sent to any other server on 127.0.0.1; its name carries the port, and it
is useless without this server, but a program listening there could read it
while the session lasts. With `--no-browser` the first page after the link
gets the link as its `Referer`, from and to this server only.
Many local connections that reconnect as soon as they are dropped can still
crowd out the session's: with 200 of them, about 1 request in 10 from the
session is dropped (with 16 or 100, none). A client that asks for a large
reply and never reads it holds a request thread for up to 10 s per write.
A search by meaning holds its request thread while it waits for the
embeddings server. In the interface the client's timeout is 5 s
(`views.EMBED_TIMEOUT`, less when `search.semantic.timeout_seconds` is
lower), and that is per socket operation (connect, each read), not for the
whole call, so a server that trickles its reply can hold the thread longer; a
few such searches at once can take the 8 request threads. The client is made
only when a query is actually embedded, and a failure to make it falls back
to the search by words, with its notice.
With `--no-browser` the printed link stays in the terminal and, once opened,
in the browser's history; it is useless once used. Anything running as the
user can read the handoff file in the moment before the browser uses it, as
it can read everything else of the user's.

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
posted. It comes from flib's notifier app (`notifier.py`) when that is built
and current, else through `/usr/bin/osascript` (`status.OSASCRIPT`), which
macOS attributes to Script Editor. The app is an AppleScript applet that
`osacompile` makes, with flib's name, bundle identifier
(`io.github.luizpereiradev.file-librarian.notifier`), `LSUIElement` and an
icon that `sips` and `iconutil` derive from `assets/icon-1024.png`, re-signed
ad hoc with `codesign`; a stamp in the bundle holds the hashes of the script
and the icon and a layout version, and a mismatch means a rebuild.
`flib schedule` (and `--refresh`) builds it next to `flib-python` and
`--remove` deletes it. It is launched with `open -g -n -W <app> --args text
title`, so Launch Services gives it its app identity (run directly, it has
none), and reads the arguments from `NSProcessInfo`.
Every tool is a module constant swapped for a fake in every test. Either way
the text holds counts only and reaches AppleScript as arguments, never as
script source. The time is recorded before notifying, so a failure is not
retried on every run, and nothing in the summary can fail the run: errors
become a warning line in the log. macOS can drop a notification without
telling (notifications not allowed, a Focus mode); the log line and `flib
status` do not depend on it.

## Modules

Everything is in `src/file_librarian/`:

| Module | What it does |
| --- | --- |
| `cli.py`, `__main__.py` | the `flib` command (Typer); prompts and output only, the work is in the modules below |
| `config.py`, `locales.py`, `config_edit.py` | the YAML configuration, validated at startup; the defaults each `language` brings; changing the file in place, comments kept, with a backup (the Settings page) |
| `models.py` | typed values passed between stages (`FileContext`, `Classification`, `Decision`...) |
| `pipeline.py` | one run or simulation, stage by stage |
| `scanner.py`, `hashing.py`, `globs.py` | stage 1; content identity (SHA-256, folder manifests, iCloud-only check); glob matching as the Mac compares names |
| `context.py`, `sensitive.py`, `extractors/` | stage 2; secret detection; one extractor per format (`text`, `office`, `pdf`, `archive`, `mail`), plus `origin.py` (download origin), `ocr.py` with its helper `ocr.js` (local text recognition in images and scanned PDFs) and `visual.py` (images and scanned PDFs for vision); the interface is in `extractors/base.py` |
| `classifier.py`, `privacy.py`, `providers/` | stage 3; identifiers scrubbed from every answer before it is cached or stored (`privacy.descriptions`); one module per provider (`anthropic`, `claude_code`, `openai_compat`, `ollama`, and `fake` for tests), the shared prompt (`prompt.py`), the HTTP client (`http.py`) and the interface (`base.py`) |
| `planner.py`, `naming.py` | stage 4; safe file names and dates; category layouts (sub-folders by year, month or issuer) and issuer folder matching |
| `executor.py`, `fsops.py` | stage 5 and crash recovery; `fsops.py` is the only code that moves, copies or deletes your files (the rest writes only flib's own: logs, reports, the trial copies of `flib init`) |
| `db/store.py`, `db/migrations/` | the SQLite store and its numbered migrations |
| `search.py`, `embeddings.py`, `finder_tags.py` | full-text search and filters (structured fields included) and totals of amounts; optional semantic search; the category as a Finder tag |
| `fields.py` | the structured fields of an answer (amount and currency, dates, period, kind) read tolerantly, and amounts in minor units |
| `lock.py`, `protection.py`, `catchup.py` | the instance, reprocess and `flib ui` locks; Data Protection (files readable only while the Mac is unlocked); the wait for an unlock after a scheduled run |
| `undo.py`, `fix.py`, `reconcile.py`, `reprocess.py` | `flib undo`, `flib fix`, `flib reconcile`, `flib reprocess` |
| `status.py`, `doctor.py`, `runlog.py` | `flib status` and the periodic summary; the redacted `flib doctor` report; one log file per run |
| `due.py` | `flib due`, `flib fix --done`, the "Due:" line of `flib status` and the due reminder in the periodic summary (#91) |
| `onboarding.py` | `flib init`, except its questions |
| `scheduler/` | the launchd agent behind `flib schedule` |
| `notifier.py`, `assets/` | the notifier app that posts notifications as "file-librarian", and its icon |
| `credentials.py`, `keychain.py` | where an API key comes from; the macOS Keychain through `/usr/bin/security` |
| `mcp_server.py` | `flib mcp` (optional `mcp` extra) |
| `ui/` | `flib ui`: the loopback server and its session (`server.py`), the JSON API (`api.py`), the pages built on the server (`views.py`, `review.py`, escaped by `markup.py`), the actions that move files (`actions.py`: review, undo, mark done, through `fix`/`undo`/`due`), the Settings page and its changes (`settings.py`, with `config_edit.py`), the static pages (`pages/`, `static/`) |
| `activity.py` | the runs of the last days, what each moved and what its log says about the Mac being locked (the interface's Activity page) |
| `evals/` | `flib eval`: the synthetic corpus and its gold labels, the isolated simulate-mode harness and the scoring ([quality.md](quality.md)) |

## Extension points

- **Providers** (`providers/`): implement `Provider.classify(context, rules)`
  and register it. See [adding-a-provider.md](adding-a-provider.md).
- **Extractors** (`extractors/`): implement `Extractor.extract(path, max_chars)`
  for a set of extensions and register it. See [adding-an-extractor.md](adding-an-extractor.md).

## Decisions

Architecture decisions are recorded as ADRs in [decisions/](decisions/).
