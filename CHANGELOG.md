# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Moves to another volume (RF-33). A destination on another volume used to
  fail the item; now it is copied with `copyfile(3)` (extended attributes
  such as the download origin and Finder tags, ACLs, permissions, flags and
  dates included) into a hidden staging folder, flushed to disk, verified
  (its SHA-256, or for a folder unit every file's content and the source's
  manifest; the source must not have changed meanwhile), renamed into place
  without overwriting, and only then is the source removed, read-only
  folders and locked files included; a source changed after the copy is
  kept, with a warning. `flib undo` copies back the same way. Each phase is
  journaled (migration 0008, `operations.copy_phase` and `copy_key`, flushed
  with `F_FULLFSYNC`) and crash recovery finishes or rolls back from what is
  on disk, never deleting the only verified copy, and waits while a volume
  is unmounted. Volumes that cannot rename without replacing (exFAT, FAT)
  are refused before anything is copied. A full disk cleans up, leaves the
  source untouched and does not count as a failed attempt. A folder unit on
  a file system with coarser times (HFS+) is indexed by its manifest there.
  Opt-in tests on real disk images: `FLIB_TEST_VOLUMES=1 uv run pytest -m
  volumes`.
- `flib doctor` writes one redacted diagnostics report to attach to a bug
  report (RF-39) and prints its path: flib, Python and macOS versions, the
  install kind (uv tool or development checkout), whether the MCP extra is
  installed, the configuration (validated, folders as `<input-1>`, `<output>`,
  category hints and personal patterns replaced), where the API key comes
  from as one word (the key itself never appears, not even its last
  characters), the schedule's state, the database's schema version and counts
  by status, the last runs and the last run logs. In the logs, every file name
  becomes `<file-N>` plus its extension (the same name, the same number),
  folders become placeholders, and secrets (the patterns of secret detection),
  e-mail addresses, URLs and quoted text are replaced; category names stay. A
  section that fails shows its error instead of stopping the report. It goes
  to flib's logs folder (owner-only, last ten kept); `--stdout` prints it,
  `--output PATH` writes it elsewhere, `--format markdown` makes it
  paste-ready, `--logs N` sets how many run logs. The bug report template
  asks for it.
- `flib check-config` warns when an option that is accepted but not active
  yet is set to anything but its default, so nobody thinks it works. The
  list lives in `config.INACTIVE_OPTIONS`; every option in the configuration
  is active today, so only the planned providers (`ollama`, `openai-compat`)
  warn, as before.
- `flib init`, the guided first run (RF-38). It asks for the language
  (default from the shell's locale), the folder to organize, where organized
  files go and the review folder, the provider and, with `anthropic`, whether
  to turn on vision (default no, with what is sent and the cost per image).
  The provider defaults to `claude-code` when Claude Code is installed and
  logged in, else `anthropic`, and then offers to store a missing API key in
  the Keychain (hidden input). When the daily run's minimal PATH would not
  find `claude`, `provider.command` is written for it. The configuration is
  written commented, mode 600; an existing one is kept unless you confirm
  replacing it or pass `--force`. A trial then copies the most recent items
  (`--sample N`, 30 by default, `0` skips it; hidden, sensitive, `never_send`,
  iCloud-only and items over 20 MB are left out) into a temporary folder and
  simulates a run over the copies with a throwaway database, showing what
  would move where, what would go to review and the estimated cost; nothing
  real is touched. Last, it offers to schedule the daily run with the same
  checks as `flib schedule`, and mentions the optional MCP server. Every
  question has a default and `--yes` takes them all. The README quick start
  is now install, then `flib init`.
- The `claude-code` provider: classify through the Claude Code CLI
  (`claude -p`) on your Claude subscription, with no API key. Each file is one
  call with `--output-format json --json-schema` (the categories as an enum),
  `--tools ""`, `--strict-mcp-config`, `--safe-mode`, `--permission-mode
  dontAsk` and `--no-session-persistence`, run in an empty temporary folder
  with an allow-listed environment (no `ANTHROPIC_API_KEY` or other secrets)
  and Claude Code's own retries off. Not logged in, a usage limit, an unknown
  model or a missing or outdated `claude` stop the run; overload, rate limits
  and timeouts are retried; anything else fails only that file. The cost cap
  applies to the cost Claude Code reports (an estimate at list prices). The
  executable is `provider.command` when set, else `claude` on PATH or in the
  usual install folders; `flib check-config` shows it and the login state,
  and `flib schedule` says when the daily run needs `provider.command`. Images
  and scanned PDFs are classified from metadata with it. The prompt and schema
  moved to `providers/prompt.py`, shared with `anthropic`. Tests use a fake
  `claude`.
- `flib mcp`, a local MCP server over stdio for Claude and other assistants
  (RF-22). Tools: `search_files` (as `flib search`), `get_file` (the index
  record plus hash, size, download origin and `icloud_only`; a cloud-only
  file is never read), `recategorize_file` (as
  `flib fix`, journaled as its own run with trigger `mcp`, so `flib undo`
  reverts it) and `undo` (as `flib undo`). Each has a description and input
  and output JSON schemas. Results never include file content; sensitive
  files show no description, tags or download origin. Write tools take the
  instance lock only for the call and fail at once while a run holds it; an
  idle server holds no lock. `mcp.read_only: true` hides the write tools. The
  README shows how to add it to Claude Code and Claude Desktop. The server
  is optional: the official MCP Python SDK (`mcp`, MIT) is the `mcp` extra
  and adds 16 packages on macOS. `./scripts/install.sh` asks whether to add
  it (default no; `--with-mcp` / `--without-mcp` to choose without asking),
  and an update keeps it once installed. Without it, `flib mcp` says how to
  enable it. `uv sync` installs it for development and CI.
- Vision for images and scanned PDFs (RF-05, RF-26), opt-in: a new
  `vision` section, `enabled: false` by default. When on, an image
  (`.jpg`, `.png`, `.heic`, `.webp`, `.gif`, `.tiff`, `.bmp`) is converted
  to JPEG by macOS's `/usr/bin/sips`, shrunk so its longer side is at most
  `vision.max_image_px` (default 1568, never enlarged) and stripped of EXIF,
  XMP and other metadata before it is sent; a PDF with no text layer is cut
  with pypdf to its first `vision.max_pdf_pages` pages (default 3) and sent
  as a document. No new dependency. `never_send` and sensitive names still
  apply first, and the cost cap counts the billed image tokens. Providers
  declare the media types they read in `attachments`; a provider without
  them (and any file that cannot be prepared) falls back to metadata only.
  A file the model saw can now be renamed. Answers with vision are cached
  under their own key, so toggling vision never reuses the other kind. The
  `limits.scanned_pdf_max_pages` placeholder moved to
  `vision.max_pdf_pages`; old files still load. Tests use a fake `sips`.
- `flib fix PATH [--category NAME] [--name NAME]` and learning from
  corrections (RF-29). The file moves through the journal as its own run, so
  `flib undo` reverts it (an undone fix restores the old category and stops
  teaching); the index and search follow, the Finder tag flib added for the
  old category is swapped for the new one (and back on undo), and the change
  is recorded in `corrections`. A fix is all or nothing: migration 0006
  records on its journal entry the status and category it sets, so if flib
  stops mid-fix, recovery finishes the whole fix (index, correction, Finder
  tag) or, if the file never moved, fails it and leaves the file as it was
  (#47). Changing only a name's capitalization renames the file in place
  instead of adding `-2`, and recovery no longer takes such a rename for
  done when a case-insensitive volume answers for the new spelling with the
  old (#48). Later requests carry the most recent corrections still in
  effect as examples: kind, extension, earlier description and the category
  or name before and after, never content, never from sensitive or
  `never_send` files. `provider.max_examples` caps them (default 10, `0` for
  none). The examples are part of the cache key, and the prompt version is
  now 3, so cached answers are refreshed.
- Finder tags (RF-36): after a move into a category folder, the item gets
  the category name as a Finder tag (`com.apple.metadata:_kMDItemUserTags`).
  The user's own tags and colors are kept, a tag already there is not
  duplicated (Finder ignores case), and a folder unit is tagged on the
  folder itself; files sent to `_Review` get no tag. Migration 0005 records
  on each journal entry the tag the move added, so `flib undo` removes that
  tag and nothing else. A tag that cannot be written or removed is a
  warning in the output and the run log, never a failed move; tags
  `flib` cannot parse are left untouched. `finder_tags.enabled: false` stops
  new tags (existing ones stay). Simulations write no tags.
- iCloud files that exist only in the cloud are skipped (RF-24). The scanner
  recognises them from `lstat` (`SF_DATALESS`) before opening, hashing or
  extracting anything, so no download is triggered; a folder unit with any
  such file inside is skipped as a whole, and the manifest walk stops at the
  first one. `flib simulate` and `flib run` list them as `in iCloud only, not
  downloaded`; a later run picks them up once downloaded.
- `flib reconcile` (RF-20): the index follows files moved, renamed or deleted
  by hand. Files are found again by content (only same-size files are
  hashed; folder units by manifest) under the library and the input folders.
  Moving a file to another category folder, or renaming it, is recorded as a
  correction for the classifier to learn from (RF-29); a file moved back to
  Downloads is left alone; one found nowhere is marked `missing` until it
  turns up again. Nothing on disk changes. Migration 0004 records whether a
  row is a folder unit.
- `flib schedule` (RF-19): installs a launchd agent that runs `flib run`
  daily at `schedule.time` (default 12:30) or `--at HH:MM`, with
  `--status` and `--remove`. It offers to store an API key that is only in
  the shell environment in the Keychain, warns when run from a development
  checkout, and checks folder access from a one-shot launchd agent, so the
  macOS privacy block that silently affects background processes is caught
  at setup with instructions. Runs started by the schedule are recorded with
  trigger `schedule`. Tests use a fake `launchctl`; no real agent is loaded.
- `scripts/install.sh` installs the `flib` command with `uv tool install`,
  pinned to the versions in `uv.lock` (a plain `uv tool install .` would
  resolve the newest versions and skip the 7-day quarantine).
- API key from the macOS Keychain: `flib key set`, `flib key status` and
  `flib key delete`. `provider.api_key_source: auto` (default) tries the
  environment, then the Keychain, then an optional `provider.env_file`;
  `check-config` shows which source is used and warns about a `.env` file
  other users can read. Keys are passed to `security` on stdin and never
  printed.
- Classification cache (migration 0003): answers are kept for 7 days per
  content hash and rules (provider, model, effort, categories and hints,
  language, prompt version), so `flib run` right after `flib simulate` reuses
  them at no cost. Changing a category or hint invalidates the cache.
- `language: pt-BR` defaults to 15 categories, each with a hint that settles
  common overlaps (payslips in Trabalho, tax receipts in Impostos):
  Financeiro, Impostos, Contratos, Documentos pessoais, Saúde, Trabalho,
  Estudos, Viagens, Compras, Casa e veículos, Imagens, Vídeos e áudio,
  Instaladores, Código e dados and Outros.
- M1 exit criterion as an automated acceptance test (50 varied items,
  including a sub-folder, a `.app` and credentials: organized, searched and
  undone with every byte accounted for) and a manual checklist with the real
  provider in `docs/acceptance.md`.
- `anthropic` provider (RF-07, RF-08): structured output against a per-request
  JSON schema with the configured categories as an enum, no sampling
  parameters, refusal checks and server-side `fallbacks: "default"` where
  supported; file content is passed as delimited untrusted data. Bad
  credentials or an unknown model stop the run before anything moves.
- Classifier (RF-30): requests-per-minute spacing, retries with exponential
  backoff for transient errors, and `limits.max_cost_usd_per_run` enforced
  from billed usage; files over the cap wait for the next run and are not
  counted as failures.
- `provider.effort` (default `low`); the example configuration uses
  `claude-haiku-5-5`, the cheapest current Claude model, which has no
  server-side refusal fallback (a declined file is marked failed and
  retried on later runs).
- Dependency: `anthropic` (SDK 2.7.0).
- `flib search QUERY [--json] [--limit N]` (RF-13, RF-35): full-text search
  over names, descriptions and tags, accent-insensitive, every word must
  match as a prefix, ranked with names first; query syntax in the input is
  treated as plain text; missing files are flagged.
- A log file per run next to the database (RF-18), owner-only, newest 100 kept.
- `flib simulate`, `flib run` and `flib undo` (RF-09, RF-10, RF-11, RF-12,
  RF-16, RF-17, RF-31, RF-32). The planner turns classifications into
  destinations that are always a configured folder under `output_root`;
  unknown categories and low confidence go to the review folder unrenamed;
  names follow `naming.pattern`, keep the extension and get a `-N` suffix on
  collisions; folder units and `rename: false` categories keep their names.
  The executor journals every move before making it, refuses to move items
  changed since the scan, creates folders without following symlinks and
  moves with `renamex_np(RENAME_EXCL)`, so nothing is ever overwritten.
  Interrupted moves are resolved at the start of the next run. Undo checks
  each item's content hash and that its original path is free, and lists
  what it skipped.
- The `fake` provider classifies everything into the last category offline,
  so the whole pipeline can be tried without an API key.
- Extractors (RF-04, RF-06, RF-27): plain text, Markdown, CSV and source
  code; DOCX, XLSX and PPTX through the standard library (streamed, size
  capped, DTDs refused); PDF through pypdf (first 20 pages); ZIP and TAR
  listings without extracting. Damaged, encrypted and legacy files fall back
  to metadata and never stop a run.
- Secret detection (RF-28): credential file names and secrets in content
  (private keys, cloud and API tokens, JWTs, connection strings, password
  assignments) mark a file `sensitive`; nothing is sent and the matched value
  is never stored. Files without an extractor are scanned but never sent.
- `context.build_context` assembles what the classifier may see, honoring
  `never_send`, `detect_secrets` and `max_chars_per_file`; folder units send a
  listing of names, `.app` bundles their name and version.
- Dependency: `pypdf`.
- Scanner (RF-01, RF-02, RF-03, RF-23): lists the top level of each input
  folder, skips hidden and system files, symlinks, special files, flib's own
  folders, `ignore` globs and in-progress downloads; waits until items are
  stable; treats sub-folders and bundles as one unit identified by a manifest
  digest; skips known hashes except files still within `max_attempts`; caps
  each run at `max_files_per_run`, oldest first; explains how to grant macOS
  folder access when an input cannot be read.
- Single-instance lock (RF-34) next to the database, released automatically
  if the process dies.
- `limits.max_cost_usd_per_run` (default US$ 1) for the classifier to honor.
- M0 foundation: package layout, `flib` command with every planned command
  (empty until its milestone), `flib check-config`, and `flib inspect`.
- Configuration schema with validation and `config.example.yaml`. Only the
  provider is required.
- `language` option (`en`, `pt-BR`) for generated names and descriptions and
  for the default folder and category names.
- SQLite schema (migration 0001) with WAL mode and an accent-insensitive FTS5 index.
- Prototype reader for the macOS download origin (`kMDItemWhereFroms`).
- Provider interface with an offline `FakeProvider`; extractor interface and registry.
- CI on macOS: lint, format, type check and tests.
- Contributor documents, extension guides and ADRs 0001–0002.

### Changed

- A descriptive original name is no longer replaced by a shorter, vaguer one
  (#59). The answer schema gains `name_choice` (`original` or `proposed`) and
  `name_choice_reason`: the model compares the file's current name with its
  proposal and says which describes the file better. When it picks
  `original`, the planner builds the new name from the original's words, with
  flib's own naming rules (pattern and date, sanitising, length, extension; a
  leading date in the old name is not repeated); generic names (`scan001`,
  `document (3)`) are still replaced. The choice and reason are stored in the
  index (migration 0007), marked "original name kept" in `flib simulate`, and
  shown by `flib inspect` (which now looks the file up in the index; it takes
  `--config`) and the MCP `get_file` tool. Both Claude providers send the new
  schema. `PROMPT_VERSION` is now 4, so cached answers are not reused.
- `FileContext.metadata` is a typed `FileMetadata` instead of a string dict;
  what providers receive is unchanged.
- Of several copies with the same content, the original goes ahead: a name
  without a copy suffix (` (1)`, ` copy`) first, then the copy created first.
  Version numbers like `-0.1.2` are not mistaken for copy suffixes.
- `flib simulate` and `flib run` list the items they left alone and why
  (duplicate, download in progress, still changing...). System files and
  flib's own folders are not listed; already-processed items are counted.
- Code and test quality pass from an independent review: a damaged Office
  file can no longer abort every run (extraction errors of any kind fall back
  to metadata, with a corruption fuzz test over every format); an invalid
  document date from the model no longer discards the whole answer; the
  provider contract (`local`, `last_usage`, `cost_usd`) is declared and
  type-checked and pricing lives with each provider; store reads return
  typed rows; layering follows the pipeline; config options that are not
  active yet are marked as such; tests no longer depend on the time of day;
  failure paths of the executor, undo and CLI are tested, and CI enforces 93%
  branch coverage.
- Files are renamed only when the model could read their content; images,
  video, installers and other metadata-only files keep their original name
  instead of getting one invented from the old name.
- The classification prompt asks the model to judge confidence from the file
  name and source as well as the content. On the acceptance corpus with
  Claude Haiku 5.5, items sent to review fell from 17 to 12 of 48.
- `flib check-config` warns when the configured provider is not implemented yet.
- `flib inspect` no longer reports a size for directories.
- CI tests Python 3.11 through 3.15; superseded pull request runs are cancelled,
  runs on `main` and the weekly audit are not.
- mypy checks `tests/` too; ruff runs the bandit (`S`) rules.
- Requirements are available in English in `docs/requirements.md`.
- The Office extractor opens each document as a ZIP once instead of once per part.
- Shared test helpers live in `tests/_helpers.py` and shared fixtures in
  `tests/conftest.py`; the per-module copies are gone.

### Fixed

- `flib reconcile` and `flib inspect` no longer download iCloud files that
  exist only in the cloud (#44). `reconcile` neither hashes such a file nor
  lists such a folder: as a match found elsewhere it does not count, and an
  indexed item still at its recorded path but only in iCloud (or a folder unit
  with anything inside only in iCloud) keeps its row unchanged instead of
  becoming `missing`, and is listed as `in iCloud only, not checked`.
  `inspect` reports such a file or folder as in iCloud only (`icloud_only` in
  `--json`, with no `sha256`) instead of hashing it.

### Security

- The daily run uses `flib-python`, a copy of the interpreter inside flib's
  own environment, instead of the uv-managed Python that every project using
  that version shares. macOS grants folder access per executable, so Full
  Disk Access now covers flib only. `flib schedule --refresh` re-creates the
  copy (keeping the time); `scripts/install.sh` runs it after reinstalling,
  and `flib schedule --status` warns when the copy is out of date.
- Mover hardening from an adversarial review: paths are compared after
  resolving symlinks and folding case and Unicode, and the scanner skips
  flib's own database, journal, lock and library by device and inode, so a
  differently spelled path can no longer make flib move its own database; one
  odd item (a blank name, a category path that is a file) no longer aborts the
  run; a second copy of the same content in one run is left in place instead
  of sharing an index row; every rename attempt is journaled before it runs
  and crash recovery verifies content before marking a move done; index and
  journal writes are grouped in transactions; a replaced file (new inode) or a
  folder changed deep inside is not moved; Finder's `.DS_Store` no longer
  blocks undoing a folder; empty files, files next to a `.part`/`.crdownload`
  and files with the hidden flag are skipped; `undo` finishes interrupted
  moves first and never steps back to an older run by itself.
- Supply-chain guards (ADR 0003): 7-day quarantine for new releases,
  Dependabot with cooldown, `uv audit` and dependency review in a Security
  workflow, actions pinned by commit SHA, zizmor workflow linting, CODEOWNERS,
  and tests that fail on unpinned actions or non-PyPI sources.
- Stricter configuration validation: folder names cannot contain path
  separators, control characters or a leading dot; categories and the review
  folder are compared the way macOS compares names (case and Unicode form);
  `naming.pattern` accepts only `{date}` and `{subject}` and must produce a file
  name; inputs cannot be `/`, your home folder, overlapping or inside
  `output_root`; the database must live outside inputs and `output_root`;
  unknown providers are rejected.

[Unreleased]: https://github.com/luizpereiradev/file-librarian/commits/main
