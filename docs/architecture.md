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
      │          sensitive.py      at this stage and stay where they are
 3. Classifier   providers/        Classification (schema-validated): category, name, description,
      │                            tags, confidence; rate limits, retries, timeouts
 4. Planner      planner.py        Operations: naming pattern, rename policy, collisions,
      │                            low confidence → review folder.   `flib simulate` stops here
 5. Executor     executor.py       single-instance lock; record "planned" → move → mark "done"
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
  source of a cross-volume move, after the copy's hash is verified.
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
- `flib undo` walks a run's operations in reverse, checks each file's hash, and
  skips (and lists) files that were edited or whose original path is taken.
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
- **Environmental failures use attempts.** A move that fails because the
  destination is on another volume or a category folder is a symlink counts
  as a failed attempt; after `max_attempts` the file is left alone until the
  index row is cleared.
- **Duplicates wait.** A second copy with the same content as one processed
  in the same run stays in place (moving duplicates is RF-21).

## Data

One SQLite file, outside the organized folders (default
`~/Library/Application Support/file-librarian/library.db`):

| Table | Holds |
| --- | --- |
| `files` | one row per file: original and current name/path, hash, origin, category, description, tags, status, attempts |
| `runs` | one row per `flib run`, with counts (simulations record none) |
| `operations` | the journal behind `flib undo`, including the Finder tag each move added and, for `flib fix`, the status and category it sets |
| `corrections` | user recategorizations and renames (`flib fix`, `flib reconcile`); the latest per file and field, while still in effect, are sent as examples and are part of the cache key |
| `files_fts` | FTS5 index over names, description and tags; accents ignored |

Schema changes are new numbered files in `db/migrations/`; the applied version
is `PRAGMA user_version`. Names and paths are stored in Unicode NFC.

## Extension points

- **Providers** (`providers/`): implement `Provider.classify(context, rules)`
  and register it. See [adding-a-provider.md](adding-a-provider.md).
- **Extractors** (`extractors/`): implement `Extractor.extract(path, max_chars)`
  for a set of extensions and register it. See [adding-an-extractor.md](adding-an-extractor.md).

## Decisions

Architecture decisions are recorded as ADRs in [decisions/](decisions/).
