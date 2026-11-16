# Architecture

file-librarian is a linear pipeline of six stages. Only one stage, the
classifier, uses AI. Each stage is a module with typed input and output
(`models.py`), so each can be tested and replaced on its own.

```text
 input folders
      │
 1. Scanner      scanner.py        new, stable files (by sha256); folders/.app as one unit
      │
 2. Extractor    context.py,       FileContext: metadata, download origin, text excerpt;
      │          extractors/,      secret detection here (sensitive.py): sensitive files stop
      │          sensitive.py      at this stage and stay where they are
 3. Classifier   providers/        Classification (schema-validated): category, name, description,
      │                            tags, confidence; rate limits, retries, timeouts
 4. Planner      planner.py        Operations: naming pattern, rename policy, collisions,
      │                            low confidence → review folder.   `flib simulate` stops here
 5. Executor     executor.py       single-instance lock; record "planned" → move → mark "done"
      │
 6. Indexer      db/, search.py    file record, FTS index, Finder tag
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
  crash mid-run is recoverable and every run can be undone.
- `flib undo` walks a run's operations in reverse, checks each file's hash, and
  skips (and lists) files that were edited or whose original path is taken.
- One writer at a time: the scheduled run, a manual run and MCP write tools
  share a single-instance lock. The database runs in WAL mode so MCP reads
  never block a run.

## Data

One SQLite file, outside the organized folders (default
`~/Library/Application Support/file-librarian/library.db`):

| Table | Holds |
| --- | --- |
| `files` | one row per file: original and current name/path, hash, origin, category, description, tags, status, attempts |
| `runs` | one row per run or simulation, with counts |
| `operations` | the journal behind `flib undo` |
| `corrections` | user recategorizations and renames, reused as examples |
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
