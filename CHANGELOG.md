# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Sub-folders inside a category (#86): an optional `layout` per category,
  with `{year}`, `{month}` and `{issuer}` (`"{year}"`, `"{issuer}/{year}"`...,
  up to three levels, validated at load). The year and month come from the
  document's date, else the download date, else the day filed; the issuer is
  a new field of the answer (the prompt asks for an organisation, not a
  private person; one holding an identifier is dropped), stored in the index
  with the document date and with the date the file's year and month were
  taken from, kept from then on so a change of file metadata (a copy to
  another disk, a time zone) never moves a file (migration 0014; prompt
  version 6). Issuer folders are stable: a name is matched with the folders
  already there ignoring case, accents, punctuation, `.com` and unambiguous
  legal forms (`Inc`, `Ltd`, `LLC`, `S.A.`, a trailing `SA`...), then by a
  conservative similarity (a doubled or swapped letter, never a digit, a
  plural, a different letter or word), and reuses its exact spelling across
  sibling year folders. An unknown value
  stops at the deepest folder known; a folder that cannot take a file sends
  it to the category's top, never failing it.
  `flib reprocess --apply-layout [--category NAME] [--ask-issuer]` moves
  organized files into their layout from what the index stores, as one run
  `flib undo` reverts; files you placed yourself stay, and values taken
  from the cache are written to the index with the run. `flib fix
  --name` now renames a file inside its sub-folder instead of moving it to
  the category's top folder.
- Local OCR (RF-26 done): the text in screenshots, photographed documents and
  scanned PDFs (first `ocr.max_pages`, 3) is recognised on the Mac with
  macOS's Vision framework, through a small `osascript` helper (no install,
  no network, works from the scheduled run). It is then the file's text: the
  secret scan sees it (a screenshot of a key makes the file sensitive),
  `never_send` and `max_chars_per_file` apply, every provider gets it and
  the file can be renamed. Photos with less than `ocr.min_chars` (40) go to
  vision when it is on, else are classified from metadata. `ocr.enabled`
  (on), `ocr.languages` (from `language`); `flib check-config` and `flib
  doctor` say whether OCR works, `flib inspect` whether a file's text came
  from it.
- `flib eval` measures classification quality on a public, synthetic corpus of
  154 downloads (formats flib reads, English and Portuguese, both locales'
  categories, generic and descriptive names, near-identical bills, items for
  review, credential files) with gold labels in
  `src/file_librarian/evals/gold.json`. It runs the real pipeline in simulate
  mode in a temporary folder with a throwaway database, never reading your
  configuration, library or cache, and reports category accuracy (overall,
  per category, common mistakes), review rate, name-kept precision and recall,
  sensitive items withheld (it fails if one is ever sent), items per minute,
  tokens and cost. It asks before spending with a paid provider and honours
  `--max-cost`; `--json`, `--out` and `--compare` save and compare runs.
  Baselines for `claude-haiku-5-5` are in [docs/quality.md](docs/quality.md).

**Setup and scheduling**

- `flib init`, a guided first run: language, folders, provider (Claude Code if
  logged in, else Ollama if running, else `anthropic`), vision and API key. It
  writes a commented configuration (`--force` replaces one), simulates a run on
  copies of your `--sample N` latest items (30) and offers to schedule.
- `flib schedule` installs a launchd agent for a daily `flib run` at
  `schedule.time` (12:30) or `--at HH:MM`; `--status`, `--remove`, `--refresh`.
  It checks folder access as launchd will and offers to store the API key in
  the Keychain. If a run skipped files macOS protects while the Mac is locked,
  it runs once more after the unlock (`schedule.wait_for_unlock_hours`, 8).
- `scripts/install.sh` installs `flib` pinned to `uv.lock` (`--with-mcp`).
  Configuration (`config.example.yaml`) needs only `provider.model`;
  `language: en` or `pt-BR` (15 default categories); `flib check-config`
  validates it.

**Organizing: scan, extract, classify, plan, move, undo**

- The scanner reads each input's top level, skips hidden, empty, in-progress
  and `ignore`d items, waits until items are stable, treats sub-folders and
  bundles as one unit and caps runs at `limits.max_files_per_run`. `flib run`
  and `simulate` say what was left alone and why (e.g. `unreadable: EPERM`).
  iCloud-only files are never downloaded, only skipped until they are local;
  files macOS protects while the Mac is locked are skipped as `locked`.
- Extractors: plain text, Markdown, CSV, JSON, YAML, HTML and code; DOCX,
  XLSX, PPTX; PDF text (first 20 pages); ZIP/TAR name listings; folder and
  `.app` listings; `.eml` (senders and recipients reduced to names and
  domains). Damaged or encrypted files fall back to metadata and never stop
  a run.
- The classifier honours `never_send`, retries transient errors and stops at
  `limits.max_cost_usd_per_run` (US$ 1; the rest wait). Answers are cached 7
  days, so `flib run` right after `flib simulate` costs nothing.
- The planner only targets configured folders under `output_root`; unknown
  categories and low confidence (`limits.confidence_threshold`) go to the
  review folder, with the reason recorded (`low_confidence`,
  `unknown_category`, `category_removed`, `moved_by_hand`).
- Every move is journaled first, never overwrites and is finished after a
  crash. `flib undo` (last run, `--run N`, `--file PATH`) checks content first.
  Moves to another volume are copied, verified, then the source removed
  (exFAT/FAT destinations refused). Filed items get their category as a
  Finder tag (`finder_tags.enabled`); undo removes only that tag.
- A log per run (newest 100 kept); `flib inspect` shows what flib reads.

**Names**

- Names follow `naming.pattern` (`{date}_{subject}`), keep the extension and
  get `-N` on collisions. Files are renamed only when the model read their
  content; folder units and `rename: false` categories keep their names. A
  descriptive original name is kept (rebuilt with flib's rules) rather than
  replaced by a vaguer one; `flib simulate` marks "original name kept".

**Duplicates**

- A file with the same content as one filed in the same run or still in the
  library goes to `_Duplicates` (`_Duplicados` in pt-BR) under `output_root`,
  never deleted and with no provider call; `duplicates: leave` keeps it in
  place, `duplicates_folder` renames it. A name without a copy suffix wins.
  If the filed copy moved, the newcomer waits for `flib reconcile`. Undo puts
  duplicates back; `flib search --duplicates` lists them.

**Search**

- `flib search QUERY` over names, descriptions and tags: accent-insensitive,
  prefix match, names ranked first, `--json`, `--limit`. Filters
  `--category`, `--ext`, `--since`/`--until` (`YYYY-MM-DD`) work with or
  without a query.
- Optional semantic search (`search.semantic.enabled`, off by default):
  `--semantic` (or `search.semantic.default`, which `--no-semantic` and the
  MCP `semantic: false` turn off for one search) so "power bill" finds an
  "electricity invoice". Any OpenAI-compatible `/embeddings` server (Ollama
  with `embeddinggemma` by default; key via `FLIB_EMBEDDINGS_API_KEY` or `flib
  key set --provider embeddings`). Only index metadata is embedded, never
  content; `--reindex` backfills; without a server it searches by words.
  Files found only by meaning need a cosine similarity of at least
  `search.semantic.min_similarity` (0.25, measured for `embeddinggemma`), so
  weak matches no longer fill `--limit`; word matches always stay.
  `embeddinggemma` gets its documented query and document prefixes
  (`query_prefix`, `document_prefix` for other models). `--json` and MCP hits
  carry `source` (`text`, `semantic`, `both`) and `similarity`.

**Corrections and reprocess**

- `flib fix PATH [--category NAME] [--name NAME]` moves a file as its own run
  (undoable, Finder tag swapped) and records a correction; recent corrections
  are sent as examples (`provider.max_examples`, default 10).
- `flib reconcile` updates the index for files moved, renamed or deleted by
  hand, found again by content; moves into another category or renames count
  as corrections, unfound files become `missing`. Nothing on disk changes.
- `flib reprocess` reclassifies filed files after the rules change and moves
  only those whose answer changed (`--rename`, `--override-corrections`,
  search filters). It estimates the cost and asks before sending and before
  moving (`--simulate`, `--yes`); `flib undo` reverts the whole run. A renamed
  or removed category needs `--rename-category OLD NEW` or `--remove-category
  OLD` (`--to-review` skips the AI). `--scrub-descriptions` removes
  identifiers from descriptions stored earlier, locally, also undoable.

**Status, summary and doctor**

- `flib status` (`--json`) shows what waits for you: review by reason and
  suggested category, failures and whether they retry, sensitive and locked
  files, the last and next run, and this month's cost. It names no file.
- After a scheduled run, if something waited `summary.min_age_days` (2), a
  summary goes to the log and a notification (counts only), at most every
  `summary.every_days` (7); `summary.notify: false` keeps only the log line.
- `flib doctor` writes a redacted report for bug reports (names, folders,
  secrets replaced): `--stdout`, `--output`, `--format markdown`, `--logs N`.

**Providers**

- `anthropic`: structured output with the categories as an enum; bad
  credentials or an unknown model stop the run before anything moves.
  `provider.effort` defaults to `low`; the example uses `claude-haiku-5-5`.
- `claude-code`: classifies through `claude -p` on your Claude subscription,
  no API key, sandboxed (no tools, no MCP, empty folder, allow-listed
  environment). `provider.command` sets the executable.
- `openai-compat`: any Chat Completions server at `provider.base_url`, key in
  `FLIB_OPENAI_COMPAT_API_KEY` (or `provider.api_key_env`) or the Keychain;
  `provider.price_input_per_mtok`/`price_output_per_mtok` feed the cost cap.
- `ollama`: a local model at `http://localhost:11434/v1`, no key; `flib
  check-config` checks the model is pulled. `fake` works offline.
- `flib key set|status|delete [--provider]` manages Keychain keys;
  `provider.api_key_source: auto` tries environment, Keychain, `env_file`.

**Vision**

- Opt-in (`vision.enabled`): images go as JPEG at most `vision.max_image_px`
  (1568) with metadata stripped; scanned PDFs send `vision.max_pdf_pages` (3).
  `openai-compat`/`ollama` need `provider.vision: true` (images only).

**MCP server**

- `flib mcp`, a local stdio server (optional `mcp` extra): `search_files`,
  `get_file` (with move history), `list_recent`, `list_review`, `get_stats`,
  `recategorize_file` and `undo`. No file content is returned; write tools
  take the lock per call; `mcp.read_only: true` hides them.

**Developer and CI**

- macOS CI on Python 3.11–3.15 (ruff, mypy, 93% branch coverage); real-volume
  tests with `FLIB_TEST_VOLUMES=1 uv run pytest -m volumes`; guides for adding
  an extractor or a provider, CONTRIBUTING.md and ADRs.

### Security

- Descriptions, tags and proposed names keep personal identifiers out of the
  index (#82): the prompt asks the AI not to copy phone numbers, e-mail
  addresses, document, account or card numbers, payment keys, addresses or
  names of private people, and flib replaces what still looks like one with
  `[phone]`, `[email]`, `[id number]`... before the answer is cached or
  stored. `privacy.descriptions: verbatim` keeps the AI's text as written.
- Secret detection marks credential files and content with keys, tokens or
  passwords `sensitive`: nothing is sent and the value is never stored.
  Sensitive files show no description or tags in search or MCP.
- API keys go to the Keychain via stdin and are never printed;
  `check-config` warns about a `.env` other users can read. `openai-compat`
  and embeddings never follow redirects.
- The daily run uses `flib-python`, flib's own interpreter copy, so Full Disk
  Access covers flib only.
- The mover compares resolved, case- and Unicode-folded paths and never
  touches its own database, journal or library; replaced or changed items
  are not moved; an item you locked in Finder is never moved to another
  volume, where flib would have to delete the locked original.
- Strict configuration: safe folder names, inputs not `/`, home or inside
  `output_root`. Office XML refuses DTDs; config, logs and reports owner-only.
- Supply chain (ADR 0003): 7-day quarantine for new releases, Dependabot with
  cooldown, `uv audit`, actions pinned by SHA, zizmor.

[Unreleased]: https://github.com/luizpereiradev/file-librarian/commits/main
