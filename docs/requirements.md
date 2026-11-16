# Requirements

The functional requirements from the [PRD](PRD.md) (written in Portuguese),
in English. Cite the ID in feature issues and pull requests. IDs are never
renumbered; the PRD is the source of truth if the two disagree.

Priority: **P0** is required for the MVP (M1), **P1** lands before v1.0, **P2**
is desirable.

| ID | Stage | Requirement | Priority |
| --- | --- | --- | --- |
| RF-01 | Scan | Scan the configured input folders and find files not processed yet, by content hash | P0 |
| RF-02 | Scan | Skip downloads in progress (`.crdownload`, `.part`); only process files whose size and mtime stayed the same between two checks | P0 |
| RF-03 | Scan | Skip configured glob patterns, system files (`.DS_Store`, `.localized`, `._*`) and the folders flib itself manages | P0 |
| RF-04 | Extract | Extract text from PDF, DOCX, XLSX, PPTX, plain text, Markdown, CSV and source code | P0 |
| RF-05 | Extract | Send images to a vision-capable model | P1 |
| RF-06 | Extract | For files without readable content (installers, videos, archives), use name, extension, size and metadata; for archives, the list of contained files | P0 |
| RF-07 | Classify | The AI returns schema-validated JSON: category, suggested name, 1–3 sentence description, tags and confidence | P0 |
| RF-08 | Classify | The category must be one of those configured; any other answer is rejected | P0 |
| RF-09 | Classify | Confidence below the configured threshold sends the file to the review folder, without renaming | P0 |
| RF-10 | Execute | Rename with a configurable pattern, keep the extension, resolve collisions with a numeric suffix | P0 |
| RF-11 | Execute | Move into the category folder; never overwrite an existing file | P0 |
| RF-12 | Index | Record each file: original name, new name, paths, hash, category, description, tags and date | P0 |
| RF-13 | Search | `flib search <query>` runs full-text search over names, description and tags | P0 |
| RF-14 | Search | Filters by category, period and extension | P1 |
| RF-15 | Search | Optional semantic search with embeddings | P2 |
| RF-16 | Safety | `flib simulate` prints the full plan without changing anything | P0 |
| RF-17 | Safety | `flib undo` reverts the last run or one file; checks the hash first and skips, with a warning, files changed since or whose original path is taken | P0 |
| RF-18 | Operation | A log per run and a final summary: processed, sent to review, errors | P0 |
| RF-19 | Operation | `flib schedule` installs the daily run as a launchd agent and tests access to Downloads | P1 |
| RF-20 | Operation | `flib reconcile` detects files moved or deleted by hand and updates the index | P1 |
| RF-21 | Operation | Detect duplicates by hash and move them to a duplicates folder, without deleting | P2 |
| RF-22 | Search | Local MCP server that lets AI assistants search and inspect indexed files, recategorize a file and undo operations | P1 |
| RF-23 | Scan | Treat sub-folders, `.app` bundles and packages as one unit: move whole or skip, never reorganize their contents | P0 |
| RF-24 | Scan | Detect iCloud files that exist only in the cloud and skip them without triggering a download | P1 |
| RF-25 | Extract | Read the download origin URL macOS records on the file and use it as a classification signal | P0 |
| RF-26 | Extract | For scanned PDFs without text, send the first pages as images to a vision model, with a page limit | P1 |
| RF-27 | Extract | Password-protected, corrupted or legacy files (`.doc`, `.xls`, `.ppt`) are classified from metadata only; an extraction failure never stops the run | P0 |
| RF-28 | Classify | Detect secrets in content before sending (private keys, tokens, credentials); a sensitive file is not sent, gets no description and is marked `sensitive` | P0 |
| RF-29 | Classify | User corrections (recategorize, rename) are stored and reused as examples in later classifications | P1 |
| RF-30 | Classify | Provider calls have a requests-per-minute limit, retries with backoff and a timeout; after N failures the file gets status `failed` in the database and is not sent again | P0 |
| RF-31 | Execute | Per-category rename policy (e.g. installers and archives keep their name) and a separate confidence threshold for renaming | P0 |
| RF-32 | Execute | The date in the name is the document's date when found with confidence, read day/month/year by default; otherwise the download date | P0 |
| RF-33 | Execute | Cross-volume moves copy, verify the copy's hash, and only then remove the source | P1 |
| RF-34 | Operation | Single-instance lock: scheduled runs, manual runs and MCP write tools never change files or the database at the same time | P0 |
| RF-35 | Search | `flib search --json` returns structured results for other programs | P0 |
| RF-36 | Operation | Apply the category as a Finder tag from a closed set; can be turned off in the configuration | P1 |
| RF-37 | Operation | `flib reprocess` reclassifies files after a rules change; renaming or removing a category needs an explicit migration | P1 |
| RF-38 | Operation | `flib init` guides the first run on a sample of 20–50 files copied to a temporary folder | P1 |
| RF-39 | Operation | `flib doctor` collects logs with names and paths redacted, ready to attach to an issue | P1 |
| RF-40 | Operation | Periodic summary of what is waiting in the review folder or has status `failed` | P2 |
