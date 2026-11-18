# Architecture

file-librarian is a linear pipeline of six stages. Only one stage, the
classifier, uses AI. Each stage is a module with typed input and output
(`models.py`), so each can be tested and replaced on its own.

```text
 input folders
      │
 1. Scanner      scanner.py        new, stable files (by sha256); folders/.app as one unit;
      │                            iCloud cloud-only items skipped from lstat, never read
 2. Extractor    context.py,       FileContext: metadata, download origin, text excerpt;
      │          extractors/,      secret detection here (sensitive.py): sensitive files stop
      │          sensitive.py      at this stage and stay where they are; with vision on,
      │                            images and scanned PDFs are marked visual
 3. Classifier   classifier.py,    Classification (schema-validated): category, name, description,
      │          providers/        tags, confidence; rate limits, retries, timeouts, cost cap;
      │                            a visual file's downscaled image or first pages
      │                            (extractors/visual.py) go to providers that read them
 4. Planner      planner.py        Operations: naming pattern, rename policy, collisions,
      │                            low confidence → review folder.   `flib simulate` stops here
 5. Executor     executor.py,      single-instance lock; record "planned" → move → mark "done";
      │          fsops.py          another volume: copy → verify → place → delete, each journaled
      │
 6. Indexer      db/, search.py,   file record, FTS index, category as Finder tag
                 finder_tags.py
```

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
- One writer at a time: the scheduled run, a manual run and MCP write tools
  share a single-instance lock. The database runs in WAL mode so MCP reads
  never block a run.

## Known limits

Found in the safety review of M1 and accepted for now:

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
- **Reprocessing holds the lock while it asks.** `flib reprocess` takes the
  instance lock before reading the index and keeps it through its two
  questions (send? move?), so a scheduled run that starts meanwhile stops
  with "already running" and the next day's run catches up.
- **Undoing a category rename keeps the renamed corrections.** `flib undo`
  of `--rename-category OLD NEW` moves the files back to `OLD` (still
  organized there, so every command again points at the missing category),
  but corrections stay renamed to `NEW` and are no longer in effect for
  those files until the rename is run again.
- **Duplicates wait.** A second copy with the same content as one processed
  in the same run stays in place (moving duplicates is RF-21).

## Data

One SQLite file, outside the organized folders (default
`~/Library/Application Support/file-librarian/library.db`):

| Table | Holds |
| --- | --- |
| `files` | one row per file: original and current name/path, hash, origin, category, description, tags, status, attempts |
| `runs` | one row per `flib run`, `flib fix` or `flib reprocess`, with what started it (`trigger`: schedule, manual, mcp, reprocess) and counts (simulations record none) |
| `operations` | the journal behind `flib undo`, including the Finder tag each move added, the phase of a cross-volume copy in progress and, for `flib fix` and `flib reprocess`, the status and category it sets |
| `corrections` | user recategorizations and renames (`flib fix`, `flib reconcile`); the latest per file and field, while still in effect, are sent as examples and are part of the cache key |
| `files_fts` | FTS5 index over names, description and tags; accents ignored |

Schema changes are new numbered files in `db/migrations/`; the applied version
is `PRAGMA user_version`. Names and paths are stored in Unicode NFC.

## Reprocessing

`flib reprocess` (`reprocess.py`, RF-37) runs files already in the library
through stages 2 to 5 again when the rules change: categories, hints,
naming, the model. It never scans the input folders.

1. **Select.** Index rows that are `organized` or in review and live inside
   `output_root`, narrowed by the search filters. Each must still be at its
   recorded path with its indexed content (otherwise: run `flib reconcile`);
   one only in iCloud is not read. Rows `sensitive` are never selected.
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
   tags swapped as `flib fix` does.

A category that organized files still use but the configuration no longer
lists is an *orphan*. Every command warns about it, and plain reprocessing
refuses until an explicit migration ran: `--rename-category OLD NEW` moves
the files to the new folder (no AI) and renames the category in the index
and in past corrections, so they stay in effect; `--remove-category OLD`
reclassifies its files among the remaining categories (unsure ones go to
review), or with `--to-review` sends them all to review without asking
anyone. The emptied folder is left in place, since undo moves files back
into it. A rename that only changes case is the same folder on macOS: only
the index changes.

## The MCP server

`flib mcp` (`mcp_server.py`) is another front end over the same modules, built
on the official MCP Python SDK and served over stdio. The SDK is the optional
`mcp` extra (`./scripts/install.sh --with-mcp`); only this command imports it,
and without it the command explains how to enable it:

| Tool | Reuses |
| --- | --- |
| `search_files` | `search.py`, as `flib search` |
| `get_file` | the index row, plus the download origin and iCloud-only check `flib inspect` uses (a cloud-only file is never read) |
| `recategorize_file` | `fix.fix_file`, as `flib fix`, with run trigger `mcp` |
| `undo` | `undo.py`, as `flib undo` |

- Reads take no lock and open the database only for the call (WAL mode).
- Each write takes the instance lock for the call only, finishes an
  interrupted journal first (`recover`) and fails at once if a run holds the
  lock. An idle server holds neither the lock nor the database, so the daily
  run is never blocked by an assistant left open.
- `mcp.read_only: true` does not register the write tools at all.
- Results are metadata and model-written descriptions, never file content.
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

## Extension points

- **Providers** (`providers/`): implement `Provider.classify(context, rules)`
  and register it. See [adding-a-provider.md](adding-a-provider.md).
- **Extractors** (`extractors/`): implement `Extractor.extract(path, max_chars)`
  for a set of extensions and register it. See [adding-an-extractor.md](adding-an-extractor.md).

## Decisions

Architecture decisions are recorded as ADRs in [decisions/](decisions/).
