# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Duplicates are moved to a duplicates folder, never deleted (#37, RF-21).
  A file with the same content as another one filed in the same run, or as
  a file in the library that is still there with that content, goes to
  `_Duplicates` (`_Duplicados` in pt-BR) inside `output_root`, without a
  provider call; before, it stayed in Downloads for good. If the filed copy
  is not where flib left it (renamed, moved, deleted or edited by hand, or on
  a volume that is not mounted), nothing is guessed: the newcomer is skipped
  with "run `flib reconcile`", and only once reconcile has marked the filed
  copy `missing` is the newcomer filed in its place (the row changes only
  after it is moved). A library copy only in iCloud or unreadable is never
  read to check (the newcomer waits). A copy of a sensitive, failed or
  deferred file stays with it. The move is
  journaled, recovered after a crash, copied and verified across volumes and
  undone by `flib undo` (later runs then leave the copy where it is); it adds
  no Finder tag. Each duplicate gets its own index row, status `duplicate`,
  pointing at its original (migration 0013). `flib run` and `flib simulate`
  show `dup  NAME  ->  _Duplicates/NAME  (same content as Finance/x.pdf)` and
  count duplicates in the summary; `flib search` and the MCP `search_files`
  leave them out unless asked (`--duplicates`, `include_duplicates`);
  `flib inspect` and `get_file` say what a duplicate is a copy of;
  `flib status` counts them; `flib reconcile` lists duplicates whose original
  went missing and never promotes them on its own; `flib reprocess` and semantic search
  leave them alone (they get no embedding). `duplicates: leave` keeps the old behaviour, and
  `duplicates_folder` renames the folder. A file whose content the index
  had as `missing` is now filed again when it reappears in Downloads. Runs
  record how many duplicates they set aside (`runs.files_duplicates`, shown
  by `flib status` and MCP `get_stats`).
  **Upgrading:** with the default `duplicates: move`, the first run after
  upgrading also sets aside the copies that waited in Downloads from earlier
  runs. A note says where they went (nothing is deleted, `flib undo` puts
  them back, `duplicates: leave` keeps them where they are from then on); it
  is written to each run log and shown by `flib status` until a `flib run`
  prints it at a terminal.
- `flib reconcile` no longer takes a copy downloaded again for a filed file
  that went missing: in an input folder, only the very file flib moved (same
  inode, dragged back by hand) counts as found. Otherwise the row becomes
  `missing`, and the next run files the copy in its place (#37). A copy that
  fails to take a `missing` row over counts toward `limits.max_attempts`
  and shows under failed in `flib status`; copies skipped because their
  filed file is not where flib left it are counted in `flib status` and the
  periodic summary.
- Optional semantic search (RF-15, #36), off by default
  (`search.semantic.enabled`). `flib search QUERY --semantic` (or
  `search.semantic.default: true`, with `--words` to opt out) fuses the
  word ranking with the cosine similarity of embeddings by reciprocal rank,
  with the same filters on both, so "power bill" finds an "electricity
  invoice". The MCP `search_files` takes a `semantic` flag and reports a
  `notice`. Embeddings come from any OpenAI-compatible `/embeddings` server,
  configured apart from the provider: Ollama on this Mac with
  `embeddinggemma` by default; an optional key from
  `FLIB_EMBEDDINGS_API_KEY` (or `search.semantic.api_key_env`) or the
  Keychain (`flib key set --provider embeddings`), never the provider's.
  Only the index's names, category, tags and description are embedded, never
  content, a sensitive file or a `never_send` match; `flib check-config`
  warns when `base_url` is not this Mac. Vectors live in a new `embeddings`
  table (migration 0012) with the model and a hash of the text, so stale
  ones are made again; each run embeds new and changed rows, `flib search
  --reindex` backfills, and a failure never fails a run. When the server is
  unreachable or nothing is embedded yet, the search says so and uses words.
  The HTTP client is now shared by `openai-compat` and embeddings
  (`providers/http.py`).
- `flib status` (`--json` too) sums up what waits for you (#38, RF-40): files
  in review, grouped by why and by the category the AI suggested, with the
  oldest's age; failed files by kind of error and whether the next run
  retries them; files left in place as sensitive; the last run, its cost and
  the items it skipped as unreadable or only in iCloud; the next scheduled
  run; and this month's cost. It names no file, only reads (no lock, nothing
  created) and works before the first run; `flib doctor` includes it and
  `flib init` points to it. After a *scheduled* run, when a file has waited in
  review or failed for `summary.min_age_days` (2) or more, flib writes the
  same summary in one line to the run log and posts one macOS notification
  with counts only, at most once every `summary.every_days` (7);
  `summary.notify: false` keeps only the log line. A manual `flib run` never
  notifies, and a notification that fails never fails the run. Migration 0011
  adds a small `meta` table that remembers when the last summary went out.
- flib records why a file went to review, what each run cost and when a move
  was undone (#68), in one migration (0010) that leaves earlier rows unknown.
  The review reason is one of `low_confidence`, `unknown_category`,
  `category_removed` or `moved_by_hand`, with the category the model
  suggested; `flib inspect`, the MCP `list_review` and `get_file` show it,
  and the `flib run` line of a file sent to review says why. `flib fix` and
  `flib reprocess` clear it when a file leaves review, and `flib undo` gives
  it back. Each run stores its estimated provider cost (`runs.cost_usd`; 0
  for a fix, unknown rather than 0 for an `openai-compat` server with no
  prices configured): the run log and summary show it, `flib doctor` lists it
  for the last runs, and `get_stats` reports this month's total and the last
  run's cost. Undo (of a run or of one file, and the recovery of an
  interrupted undo) records when it happened, and `get_file` history shows it.
- E-mail messages (`.eml`) are read, with the standard library: subject,
  date, sender and recipients, attachment names (never their content) and
  the body (the plain-text part, else the HTML part's text without tags,
  scripts or styles). Addresses keep only their domain (`Power Co
  <…@power.example>`), in every header line. The body goes through the
  secret scan like any document. At most 8 MB of a message is read and 200
  MIME parts visited; a damaged message is classified from its metadata.
  The README lists every format flib reads.
- The guides for adding an extractor and a provider
  (`docs/adding-an-extractor.md`, `docs/adding-a-provider.md`) cover the
  whole path, checked by following them: the contract, every file to touch
  (configuration, credentials, the test suite's key isolation), test fakes,
  fuzz tests, documentation and a pull request checklist. CONTRIBUTING.md
  has a "Your first contribution" path (#35).
- `flib reprocess` (RF-37): files already in the library (organized or in
  review) are classified again after the rules change (categories, hints,
  naming, model) and only those whose answer changed move. File names are
  kept unless `--rename`, and a file you put in a sub-folder of its category
  stays there unless its category changes. It estimates the cost (cached
  answers are free; images and scanned pages priced by their size) and asks
  before sending anything, shows the plan and asks before moving
  (`--simulate` classifies and stops there, `--yes` answers both); the
  questions come before the instance lock is taken, and every file is
  checked again before it moves. It honours the per-run cost limit (run it
  again to go on), takes the search filters (`--category`, `--since`,
  `--until`, `--ext`), and never sends a file whose current or original name
  is sensitive or matches `never_send`. Corrections made with `flib fix` or
  found by `flib reconcile` are kept unless `--override-corrections`. The
  moves are one run with the new trigger `reprocess`, journaled like a fix
  so a crash either finishes a move or leaves the file and its row as they
  were, and `flib undo` reverts the whole of it, Finder tags included,
  giving back each file's previous status and category. A category renamed
  or removed in the configuration while files still use it is reported by
  every command and needs `--rename-category OLD NEW` (moves the folder's
  files, renames the category in the index, corrections and, for a change
  of case only, Finder tags) or `--remove-category OLD` (reclassifies its
  files, or `--to-review` sends them to review without AI); the index side
  is journaled too, so undo reverts it, and undo makes a deleted category
  folder again. Migration 0009 rebuilds `runs` to allow the new trigger,
  adds the previous status and category to `operations` and the
  `index_changes` journal. After a reprocess that changed nothing, plain
  `flib undo` refuses instead of undoing an earlier run. An item's own name
  no longer counts as taken when the planner re-plans it into its folder.
  In the MCP server, `get_file` history marks these moves `by: reprocess`
  and `get_stats` counts a reprocess as the last run. Only one reprocess
  runs at a time (its own lock file), and it finishes interrupted moves
  before planning; `flib reconcile` now does too, so flib's own interrupted
  move is never recorded as a correction. New descriptions, tags and
  confidence are journaled and undone with the run, and `flib undo --file`
  of a reprocessed file also reverts that run's index changes for it.
- `ollama` provider (#33): classification by a local model through Ollama's
  OpenAI-compatible endpoint (default `http://localhost:11434/v1`), with no
  key. A preset of `openai-compat`. On this Mac it is `local`, which only
  drops the requests-per-minute spacing: `never_send` and sensitive files are
  withheld exactly as with a remote provider. Pointed at another machine it is
  treated as remote. `flib check-config` checks that Ollama answers and the
  model is pulled (one `GET /v1/models`, no file content), and `flib init`
  offers it, as the default after a logged-in Claude Code, when it is running
  with a model pulled.
- `openai-compat` provider (#34): any server that speaks the OpenAI Chat
  Completions API, at `provider.base_url` (required; http(s), no credentials,
  query or fragment). Structured output with `response_format: json_schema`
  (the same schema as the Claude providers); a server that rejects it gets
  `json_object` with the schema in the instructions, remembered for the run,
  and every answer is validated strictly. 401/403/404 and redirects stop the
  run (redirects are never followed); 408/409/429/5xx, timeouts and connection
  errors are retried. Optional key from `FLIB_OPENAI_COMPAT_API_KEY` (or the
  variable `provider.api_key_env` names; `OPENAI_API_KEY` is not read unless
  named), the Keychain account `openai-compat-api-key` (`flib key set
  --provider openai-compat`) or `provider.env_file`. Cost from
  `provider.price_input_per_mtok` and `price_output_per_mtok` (default 0 =
  unknown, so the per-run cost cap cannot stop a run; `flib check-config`
  warns). Images with `provider.vision: true`; scanned PDFs are never sent.
  Plain `urllib`, no new dependency; loopback requests skip proxies.
  `flib key set|status|delete` take `--provider` and act on the configured
  provider's key by default, each provider with its own Keychain account;
  `flib doctor` redacts `base_url` and every stored key.
- MCP: three more read-only tools and file history (#52). `list_recent`
  lists the newest organized files (optionally one category), `list_review`
  what waits in the review folder with the AI's confidence (and the
  threshold, and how many wait in all), and `get_stats` counts files by
  status and by category, the total size filed and the last run (single-file
  fixes not counted). `get_file` now returns the file's journaled moves,
  newest first, at most 20: whether a run or a fix made each one, what
  started it, moved and/or renamed, and whether it was undone. Like the
  other read tools they take no lock, never create the library and never
  show a sensitive file's description or tags.

- Moves to another volume (RF-33). A destination on another volume used to
  fail the item; now it is copied with `copyfile(3)` (extended attributes
  such as the download origin and Finder tags, ACLs, permissions, flags and
  dates included) into a hidden staging folder, flushed to disk, verified
  (its SHA-256, or for a folder unit every file's content and the source's
  manifest; the source must not have changed meanwhile), renamed into place
  without overwriting, and only then is the source removed, read-only
  folders included; a source changed after the copy is kept, with a
  warning. A locked item (Finder's "Locked"), or a folder with anything
  locked inside, is refused before copying, so flib never deletes what you
  locked.
  `flib undo` copies back the same way. Each phase is journaled (migration
  0008: `operations.copy_phase`, `copy_key` and the two folders' devices;
  flushed with `F_FULLFSYNC`) and crash recovery finishes or rolls back from
  what is on disk, never deleting the only verified copy, and waits while a
  volume is unmounted. Volumes that cannot rename without replacing (exFAT,
  FAT) are refused before anything is copied or created there. A full disk cleans up, leaves the
  source untouched and does not count as a failed attempt. A folder unit on
  a file system with coarser times (HFS+) is indexed by its manifest there.
  Opt-in tests on real disk images: `FLIB_TEST_VOLUMES=1 uv run pytest -m
  volumes`.
- Search filters (RF-14): `flib search` takes `--category` (any case),
  `--ext` (with or without the dot), both repeatable or comma-separated, and
  `--since`/`--until` (`YYYY-MM-DD`, inclusive, local time) on the day flib
  last processed the file. Filters combine with the query and with each
  other, and work without a query, listing matches newest first. Bad values
  stop with a clear message. The MCP `search_files` tool takes the same
  filters (`categories`, `extensions`, `since`, `until`), and its `query` is
  now optional when a filter is given.
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
- CI tests Python 3.11 through 3.15 on `main` and the oldest and newest on
  pull requests, behind one required `CI passed` check; superseded pull request runs are cancelled,
  runs on `main` and the weekly audit are not.
- mypy checks `tests/` too; ruff runs the bandit (`S`) rules.
- Requirements are available in English in `docs/requirements.md`.
- The Office extractor opens each document as a ZIP once instead of once per part.
- Shared test helpers live in `tests/_helpers.py` and shared fixtures in
  `tests/conftest.py`; the per-module copies are gone.

### Fixed

- Files that macOS lets anyone read only while the Mac is unlocked (Data
  Protection class A or B, used by some apps built for iOS) are no longer
  skipped as `unreadable` by every scheduled run that starts while the Mac
  is locked, with a misleading hint about Full Disk Access. The scanner
  reads the file's protection class (`getattrlist`, metadata only) when a
  read fails with `EPERM`/`EACCES` and skips it as `locked` ("protected by
  macOS while the Mac is locked") instead; no attempt is counted, and a
  folder unit holding such a file waits as a whole while the screen is
  locked. After a scheduled run that skipped files this way, flib releases
  its lock and waits for the Mac to be unlocked, checking once a minute for
  up to `schedule.wait_for_unlock_hours` (new, default 8, `0` = do not
  wait), then runs once more. The wait ends cleanly on `SIGTERM`, never
  blocks `flib run`, exits if another run is going on when it wakes, and
  there is only ever one: a scheduled run that finds a waiter alive (an
  `flock` on `flib-waiter.lock`) does not wait itself, and no process is
  ever signalled. `flib status`, its
  `--json` and the periodic summary count these files with what to do;
  `flib doctor` shows whether the screen is locked and how many the last
  run skipped. In the library, such a file that cannot be read while the
  Mac is locked is "cannot tell now" for `flib reconcile` (reported as
  `locked`, never `missing`), the duplicate check, `flib undo`, `flib fix`,
  `flib reprocess` and crash recovery. flib never changes a file's
  protection class.
- A file skipped as unreadable now says why, in the `flib run` and `flib
  simulate` output and in the run log: the error and the step that failed,
  as in `(unreadable: EPERM Operation not permitted, on read)`. A read that
  fails with an error that is usually transient (`EDEADLK`, `EAGAIN`,
  `EINTR`, `ETIMEDOUT`, `EBUSY`, `ESTALE`) is tried once more after half a
  second instead of waiting for the next day; `EPERM`, `EACCES` and `ENOENT`
  are not retried. When a scheduled run is denied a file (`EPERM`/`EACCES`),
  its summary and log end with one line pointing to Full Disk Access for
  `flib-python` and `flib schedule`. `flib doctor` redacts the new lines like
  any other. The README's troubleshooting section explains what to do.
- Tests no longer call `monkeypatch.undo()`, which also switched off the
  suite's safety guards (temporary HOME, no network) for the rest of a test; a
  guard test keeps it from coming back.
- The classification cache tells servers apart: answers from one
  `openai-compat` or `ollama` server are no longer reused for another server
  with the same model name (#71).
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
