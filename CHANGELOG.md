# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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

### Security

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

### Changed

- `flib check-config` warns when the configured provider is not implemented yet.
- `flib inspect` no longer reports a size for directories.
- CI tests Python 3.11 through 3.15; superseded pull request runs are cancelled,
  runs on `main` and the weekly audit are not.
- mypy checks `tests/` too; ruff runs the bandit (`S`) rules.
- Requirements are available in English in `docs/requirements.md`.

[Unreleased]: https://github.com/luizpereiradev/file-librarian/commits/main
