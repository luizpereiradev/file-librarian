# Requirements

The functional requirements from the [PRD](PRD.md) (written in Portuguese),
in English. Cite the ID in feature issues and pull requests. IDs are never
renumbered; the PRD is the source of truth if the two disagree.

Priority: **P0** is required for the MVP (M1), **P1** lands before v1.0, **P2**
is desirable.

Status, as of v0.1.0, checked against the code: **Done** (implemented and
tested) or **Partial** (what is missing is said); no requirement is left
undone. "Opt-in" marks one that is off until configured. The notes say
where a requirement ends; the
[architecture](architecture.md) has the details. Outside this list, the
PRD's v1.0 milestone also plans a package published on PyPI: that is not
done, and flib is installed from the repository (`scripts/install.sh`).

| ID | Stage | Requirement | Priority | Status |
| --- | --- | --- | --- | --- |
| RF-01 | Scan | Scan the configured input folders and find files not processed yet, by content hash | P0 | Done |
| RF-02 | Scan | Skip downloads in progress (`.crdownload`, `.part`); only process files whose size and mtime stayed the same between two checks | P0 | Done (also `.partial`, `.download`, `.opdownload` and their siblings; `limits.stable_seconds`) |
| RF-03 | Scan | Skip configured glob patterns, system files (`.DS_Store`, `.localized`, `._*`) and the folders flib itself manages | P0 | Done |
| RF-04 | Extract | Extract text from PDF, DOCX, XLSX, PPTX, plain text, Markdown, CSV and source code | P0 | Done (also `.eml` messages) |
| RF-05 | Extract | Send images to a vision-capable model | P1 | Done, opt-in (`vision.enabled`; read by `anthropic`, and by `openai-compat` and `ollama` with `provider.vision`; not by `claude-code`) |
| RF-06 | Extract | For files without readable content (installers, videos, archives), use name, extension, size and metadata; for archives, the list of contained files | P0 | Done (archive listings for `.zip` and tar formats; other archives, such as `.7z`, `.rar` and `.dmg`, by metadata only) |
| RF-07 | Classify | The AI returns schema-validated JSON: category, suggested name, 1–3 sentence description, tags and confidence | P0 | Done |
| RF-08 | Classify | The category must be one of those configured; any other answer is rejected | P0 | Done (the file goes to review, reason `unknown_category`) |
| RF-09 | Classify | Confidence below the configured threshold sends the file to the review folder, without renaming | P0 | Done |
| RF-10 | Execute | Rename with a configurable pattern, keep the extension, resolve collisions with a numeric suffix | P0 | Done |
| RF-11 | Execute | Move into the category folder; never overwrite an existing file | P0 | Done |
| RF-12 | Index | Record each file: original name, new name, paths, hash, category, description, tags and date | P0 | Done |
| RF-13 | Search | `flib search <query>` runs full-text search over names, description and tags | P0 | Done |
| RF-14 | Search | Filters by category, period and extension | P1 | Done (`--category`, `--since`, `--until`, `--ext`) |
| RF-15 | Search | Optional semantic search with embeddings | P2 | Done, opt-in (`search.semantic.enabled`; needs an OpenAI-compatible embeddings server, Ollama on this Mac by default) |
| RF-16 | Safety | `flib simulate` prints the full plan without changing anything | P0 | Done |
| RF-17 | Safety | `flib undo` reverts the last run or one file; checks the hash first and skips, with a warning, files changed since or whose original path is taken | P0 | Done (also `--run` for a given run) |
| RF-18 | Operation | A log per run and a final summary: processed, sent to review, errors | P0 | Done |
| RF-19 | Operation | `flib schedule` installs the daily run as a launchd agent and tests access to Downloads | P1 | Done (a run that skipped files protected while the Mac was locked waits for the unlock and runs once more) |
| RF-20 | Operation | `flib reconcile` detects files moved or deleted by hand and updates the index | P1 | Done |
| RF-21 | Operation | Detect duplicates by hash and move them to a duplicates folder, without deleting | P2 | Done (on by default; `duplicates: leave` turns it off) |
| RF-22 | Search | Local MCP server that lets AI assistants search and inspect indexed files, recategorize a file and undo operations | P1 | Done (`flib mcp`, with the optional `mcp` extra: `./scripts/install.sh --with-mcp`) |
| RF-23 | Scan | Treat sub-folders, `.app` bundles and packages as one unit: move whole or skip, never reorganize their contents | P0 | Done (`folders: as_unit` or `ignore`) |
| RF-24 | Scan | Detect iCloud files that exist only in the cloud and skip them without triggering a download | P1 | Done |
| RF-25 | Extract | Read the download origin URL macOS records on the file and use it as a classification signal | P0 | Done |
| RF-26 | Extract | For scanned PDFs without text, send the first pages as images to a vision model, with a page limit | P1 | Done: the first `ocr.max_pages` pages (and images) are read on the Mac with macOS's text recognition (`ocr.enabled`, on by default), so every provider classifies them from text that went through secret detection; with `vision.enabled`, pages and photos with little text are sent to a provider that reads them (`anthropic` for PDFs) |
| RF-27 | Extract | Password-protected, corrupted or legacy files (`.doc`, `.xls`, `.ppt`) are classified from metadata only; an extraction failure never stops the run | P0 | Done |
| RF-28 | Classify | Detect secrets in content before sending (private keys, tokens, credentials); a sensitive file is not sent, gets no description and is marked `sensitive` | P0 | Done |
| RF-29 | Classify | User corrections (recategorize, rename) are stored and reused as examples in later classifications | P1 | Done (`flib fix`, MCP `recategorize_file`, and moves `flib reconcile` finds) |
| RF-30 | Classify | Provider calls have a requests-per-minute limit, retries with backoff and a timeout; after N failures the file gets status `failed` in the database and is not sent again | P0 | Done |
| RF-31 | Execute | Per-category rename policy (e.g. installers and archives keep their name) and a separate confidence threshold for renaming | P0 | Done |
| RF-32 | Execute | The date in the name is the document's date when found with confidence, read day/month/year by default; otherwise the download date | P0 | Done (`naming.date_order`) |
| RF-33 | Execute | Cross-volume moves copy, verify the copy's hash, and only then remove the source | P1 | Done (destinations on exFAT or FAT are refused, since they cannot rename without replacing) |
| RF-34 | Operation | Single-instance lock: scheduled runs, manual runs and MCP write tools never change files or the database at the same time | P0 | Done (one lock per database: two configurations with different databases over the same folders do not exclude each other) |
| RF-35 | Search | `flib search --json` returns structured results for other programs | P0 | Done |
| RF-36 | Operation | Apply the category as a Finder tag from a closed set; can be turned off in the configuration | P1 | Done (`finder_tags.enabled`) |
| RF-37 | Operation | `flib reprocess` reclassifies files after a rules change; renaming or removing a category needs an explicit migration | P1 | Done |
| RF-38 | Operation | `flib init` guides the first run on a sample of 20–50 files copied to a temporary folder | P1 | Done (30 items by default, `--sample` from 0 to 200) |
| RF-39 | Operation | `flib doctor` collects logs with names and paths redacted, ready to attach to an issue | P1 | Done |
| RF-40 | Operation | Periodic summary of what is waiting in the review folder or has status `failed` | P2 | Done (`flib status`, and a summary after scheduled runs per `summary.every_days`) |
