# file-librarian

Keeps your Downloads folder organized. Once a day, `flib` reads each new file,
gives it a descriptive name, moves it into a category folder and writes a
searchable description to a local SQLite index.

`documento(3).pdf` becomes `Finance/2026-10-05_electricity-invoice.pdf`, and
`flib search "electricity october"` finds it months later.

**The AI only decides. Deterministic code moves files.** The organizing
commands keep these guarantees:

- nothing is deleted and nothing is overwritten. The one exception: when the
  destination is on another volume (an external disk), the item is copied,
  the copy is checked against the original's hash, and only then is the
  original removed; extended attributes (download origin, Finder tags),
  permissions and dates go with it. exFAT and FAT drives cannot rename
  without replacing, so flib does not move anything onto them;
- every run is recorded and can be undone, except files you changed or moved
  afterwards, which are skipped and listed.
- nothing is downloaded from iCloud: files that exist only in the cloud, and
  folders with any such file inside, are skipped and listed until you
  download them. `flib reconcile` and `flib inspect` do not read them either.

> **Status: pre-alpha (M2 features complete, in daily-use validation).**
> Scanning, extraction, secret detection, classification with Claude,
> planning, moving, search and undo work, and so do the M2 additions: the
> guided first run (`flib init`), the daily run (`flib schedule`),
> `flib reconcile` and `flib fix`, Finder tags, iCloud-aware scanning, opt-in
> vision, the optional MCP server (`flib mcp`) and the `claude-code` provider.
> M2 is now in its two-week daily-use validation
> ([#24](https://github.com/luizpereiradev/file-librarian/issues/24)). Try it
> offline first with `provider: {name: fake, model: offline}`, which files
> everything under the last category without calling any API. See the
> [roadmap](#roadmap).

## Requirements

- The current major release of macOS (the only supported platform)
- [uv](https://docs.astral.sh/uv/) 0.13 or newer (`brew install uv`); it
  installs Python 3.11+ for you
- An Anthropic API key for the default provider, or
  [Claude Code](https://code.claude.com) logged in to your Claude
  subscription for the `claude-code` provider, or
  [Ollama](https://ollama.com) with a model pulled for the `ollama` provider,
  or any OpenAI-compatible API for `openai-compat`

## Install

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
./scripts/install.sh      # installs `flib` with the locked dependency versions;
                          # asks whether to add the optional MCP server
flib init                 # a few questions, a trial on copies of your files, the daily run
```

`flib init` asks for the language, the folder to organize and where organized
files go, the provider and, with `anthropic`, whether to turn on
[vision](#images-and-scanned-pdfs-vision) (default no).
Every question has a default, so pressing Enter all the way gives a working
setup (`flib init --yes` takes them all without asking):

- **Provider:** `claude-code` when Claude Code is installed and logged in
  (nothing more to set up), then `ollama` when Ollama is running on this Mac
  with a model pulled (it is offered only then, and the trial checks the model
  is there), otherwise `anthropic`, and then it offers to store your API key in
  the Keychain (typed hidden, never shown). `openai-compat` is always offered:
  it asks for the server's URL and the model, and offers to store a key.
- **Configuration:** written to `~/.config/file-librarian/config.yaml` (or
  `--config PATH`), readable only by you. If one exists, it is kept unless you
  confirm replacing it (`--force` replaces it without asking).
- **Trial:** your most recent items (30 by default, `--sample N`; `0` skips
  it) are *copied* to a temporary folder and classified there with a
  throwaway index. You see what would move where, what would go to review and
  the estimated cost. Nothing in your folders moves, and your real index is
  not touched; the copies are deleted afterwards. Hidden, sensitive,
  `never_send`, iCloud-only and large (over 20 MB) items are left out.
- **Daily run:** it offers to schedule it, as `flib schedule` does.

Then `flib simulate` shows the plan for your real folder, `flib run`
organizes for real, and `flib undo` reverts the last run.

To set it up by hand instead:

```sh
mkdir -p ~/.config/file-librarian
cp config.example.yaml ~/.config/file-librarian/config.yaml
flib key set              # stores your API key in the macOS Keychain (anthropic, openai-compat)
flib check-config         # validates the configuration and finds the key
flib simulate             # shows what would happen; nothing moves
```

### Run it every day

```sh
flib schedule                 # daily at schedule.time (12:30 by default); --at 21:00 to change
flib schedule --status        # when it runs and whether it is active
flib schedule --remove        # stop it
```

`flib schedule` installs a launchd agent, offers to move an API key that is
only in your shell into the Keychain, and then checks that a *scheduled*
process can read your folders. macOS may block background processes from
Downloads; if so, it tells you exactly which program to add under System
Settings > Privacy & Security > Full Disk Access. That program is
`flib-python`, flib's own copy of the Python interpreter, so the access you
grant covers flib only and not your other Python projects. After updating
Python or flib, `./scripts/install.sh` refreshes it (`flib schedule
--refresh`); macOS may then ask for access again. If the Mac is asleep at the
scheduled time, the run happens on wake. Each run writes a log next to the
database (`~/Library/Application Support/file-librarian/logs`), ending with
its estimated provider cost (or "unknown" when the provider's prices are not
known).

To try it without an API key, set `provider: {name: fake, model: offline}`:
everything is filed under the last category, offline.

### Staying on top of it

Once flib runs by itself, the files it was not sure about wait in the review
folder, and you only see them if you look. `flib status` sums it up without
naming any file:

```text
Review: 3 files in _Review, the oldest for 10 days
    2  the AI was not sure enough; it suggested Finance
    1  the AI chose a category that is not configured: Recipes
Failed: 1 file
    1  classification failed (1 of 3 attempts; the next run tries again; for 10 days)
Left in place as sensitive: 1 file
Last run: 2026-11-19 12:30, scheduled: 4 organized, 3 to review, 1 failed; US$ 0.0123
  skipped: 1 unreadable, 1 only in iCloud (see its log)
Next run: 2026-11-20 12:30 (daily at 12:30)
This month: US$ 0.0456
```

It only reads (no lock, so it works while a run is going on, and before the
first one); `--json` gives the same for scripts. You do not have to remember
to run it: after a *scheduled* run, when a file has waited in review or failed
for two days or more, flib writes a one-line summary to the run log and shows
one macOS notification, such as "3 files wait for review, 1 failed. Run `flib
status` for details." It does this at most once a week, and never after a
`flib run` you started yourself. The notification holds counts only, never a
file name, because notifications show on the lock screen. Change it under
`summary:` in the configuration: `notify: false` keeps only the log line,
`every_days` and `min_age_days` set how often and how patient.

The notification is posted with `osascript`, so macOS shows it as coming from
Script Editor, and it can be hidden: macOS may only list Script Editor under
System Settings > Notifications after its first notification, with alerts off,
and a Focus mode or "Do Not Disturb" silences it. A notification macOS drops
cannot be detected, so it is not retried. If you never see one, allow
notifications for Script Editor there, or rely on the run log and
`flib status`, which always have the same summary.

### Where the API key comes from

`provider.api_key_source: auto` (the default) uses the first it finds:

1. `ANTHROPIC_API_KEY` in the environment (for `openai-compat`, the variable
   below);
2. the macOS Keychain (`flib key set`; `flib key status` shows which is used);
3. the `.env` file named by `provider.env_file`, if you set one.

Each provider keeps its own key: `flib key set --provider openai-compat`
stores the `openai-compat` key in a separate Keychain entry, so it never
replaces the Anthropic one (`flib key set`, `status` and `delete` act on the
configured provider's key when `--provider` is not given).

Scheduled runs don't see your shell's environment, so the Keychain is the
easiest choice for them. Set `api_key_source` to `environment`, `keychain` or
`env_file` to use only one place.

### Using your Claude subscription instead (`claude-code`)

If you already use Claude Code, flib can classify through it instead of the
API, with no API key:

```yaml
provider:
  name: claude-code
  model: haiku               # any name `claude --model` takes: haiku, sonnet, claude-sonnet-5-5...
```

`flib check-config` shows which `claude` it found and whether it is logged in
(if not, run `claude` once and sign in with `/login`). Each file is one
`claude -p` call with every tool, MCP server, hook, skill and CLAUDE.md turned
off, in an empty temporary folder, with a structured-output schema; only the
same prompt the `anthropic` provider sends leaves your Mac. `ANTHROPIC_API_KEY`
and other secrets in your environment are not passed to it.

Trade-offs:

- it uses your subscription's usage limits; reaching one stops the run, and
  the remaining files wait for the next one;
- each file starts a new `claude` process, so it is slower than the API
  (a few seconds per file);
- `limits.max_cost_usd_per_run` applies to the cost Claude Code reports, an
  estimate at API list prices. On a subscription nothing is charged per call,
  but the figure still caps how much of your plan a run uses;
- images and scanned PDFs are classified from their metadata: `claude -p`
  takes text only, so `vision.enabled` has no effect with this provider.

Scheduled runs have a minimal PATH. flib also looks in the usual install
folders (`~/.local/bin`, `~/.claude/local`, `/opt/homebrew/bin`,
`/usr/local/bin`); if yours is elsewhere, `flib schedule` tells you to set
`provider.command` to the path it found in your shell.

### A local model with Ollama (`ollama`)

With [Ollama](https://ollama.com) running on your Mac, files are classified by
a model on your own machine: nothing leaves it, and there is no key and no bill.

```yaml
provider:
  name: ollama
  model: llama3.2            # any model you have pulled: `ollama pull llama3.2`
  # base_url: http://localhost:11434/v1   # the default
  timeout_seconds: 120       # local models can be slow on the first file
```

`flib check-config` asks Ollama for its model list (no file content is sent)
and tells you if it is not running (`ollama serve`) or the model is not pulled.
Small models make more mistakes than Claude: try `flib simulate` first and
keep `limits.confidence_threshold` where it is, so doubtful files go to review.

What being local changes, precisely: requests are not spaced by
`provider.requests_per_minute`, and loopback requests skip any HTTP proxy.
Nothing else is relaxed: files matching `never_send` still send only their
metadata, and files with credential names or secrets in them are still never
sent at all, even though the model is on your Mac. If you point `base_url` at
another machine, flib treats it as remote (with rate limiting) and
`flib check-config` warns that file content leaves your Mac.

### Any OpenAI-compatible API (`openai-compat`)

Any server that speaks the OpenAI Chat Completions API works: OpenAI,
OpenRouter, Groq, vLLM, LM Studio, llama.cpp's server and others.

```yaml
provider:
  name: openai-compat
  base_url: https://api.openai.com/v1   # required: the part before /chat/completions
  model: gpt-5-mini
  price_input_per_mtok: 0.25            # US$ per million tokens, from your provider's price list
  price_output_per_mtok: 2.00
  # api_key_env: OPENAI_API_KEY         # default: FLIB_OPENAI_COMPAT_API_KEY
  # vision: true                        # the model reads images (also needs vision.enabled)
```

- **Key:** optional (a local server usually needs none). It comes from the
  environment variable `FLIB_OPENAI_COMPAT_API_KEY`, or the one
  `provider.api_key_env` names, then the Keychain (`flib key set --provider
  openai-compat`), then `provider.env_file`. `OPENAI_API_KEY` is not read
  unless you name it in `api_key_env`, so a key you set for other tools never
  goes to a server you did not pair it with.
- **Cost:** flib cannot know your server's prices. With
  `price_input_per_mtok` and `price_output_per_mtok` left at 0, every file
  counts as free and **`limits.max_cost_usd_per_run` cannot stop a run**;
  `flib check-config` warns about it for a remote server, and the run's cost
  is shown and recorded as unknown (a server on this Mac counts as free). `limits.max_files_per_run`
  still caps how many files a run sends.
- **Answers:** flib asks for structured output with a JSON schema. A server
  that rejects that is asked again with plain JSON mode and the schema in the
  instructions, and flib keeps using JSON mode for the rest of the run. Every
  answer is validated the same way: a category outside your list fails the
  file.
- **Images:** sent only with `provider.vision: true` and `vision.enabled`.
  Scanned PDFs are never sent to this provider; they are classified from their
  metadata.
- **Where data goes:** only to `base_url`. Redirects are refused, user names
  or passwords in the URL are rejected (use the Keychain), and `flib
  check-config` warns about `http://` to another machine.

## Commands

| Command | What it does | Milestone |
| --- | --- | --- |
| `flib check-config` | Validate the configuration without touching files | M0 |
| `flib inspect FILE...` | Show hash, size and download origin (not the hash of a file only in iCloud), and for an indexed file its status, category, why it is in review (if it is) and name choice | M0 |
| `flib simulate` | Print the full plan without changing anything | M1 (available) |
| `flib run` | Classify, rename, move and index new files | M1 (available) |
| `flib search [QUERY] [--category NAME] [--since DATE] [--until DATE] [--ext EXT] [--semantic \| --words] [--reindex] [--json]` | Full-text search over names, descriptions and tags, narrowed by category, period or extension; optionally by meaning too ([Searching](#searching)) | M1 (available) |
| `flib undo [--run ID \| --file PATH]` | Revert a run or one file | M1 (available) |
| `flib schedule [--at HH:MM \| --status \| --remove]` | Run every day via launchd, with an access check | M2 (available) |
| `flib reconcile` | Update the index after you move, rename or delete files by hand; files only in iCloud are left unchecked | M2 (available) |
| `flib fix PATH [--category NAME] [--name NAME]` | Correct one file's category or name; flib learns from it | M2 (available) |
| `flib init [--yes] [--sample N] [--force]` | Guided setup with a trial on copies of recent files | M2 (available) |
| `flib mcp` | Local MCP server for AI assistants ([Use it from Claude](#use-it-from-claude)) | M2 (available) |
| `flib reprocess [--category NAME] [--since DATE] [--until DATE] [--ext EXT] [--rename] [--simulate] [--yes]` | Reclassify organized files after the rules change (names kept unless `--rename`); `--rename-category OLD NEW` and `--remove-category OLD [--to-review]` migrate a category ([Changing your rules](#changing-your-rules)) | M3 (available) |
| `flib status [--json]` | What waits for you: files in review and why, failures, files left in place as sensitive, the last and next run, this month's cost ([Staying on top of it](#staying-on-top-of-it)) | v1.0 (available) |
| `flib doctor [--stdout \| -o PATH] [--format markdown]` | A redacted diagnostics report for bug reports ([Troubleshooting](#troubleshooting)) | M3 (available) |

### Searching

Every word of the query must match a name, the description or a tag; accents
are ignored and words match as prefixes. Filters narrow the results and can be
combined with each other and with a query, or used without one, in which case
matches are listed newest first:

- `--category NAME`: case-insensitive. Repeat it or separate names with commas
  to match any of them (`--category finance,contracts`).
- `--ext EXT`: with or without the dot (`pdf` or `.pdf`). Repeat it or use
  commas for several (`--ext jpg,png`).
- `--since DATE` / `--until DATE`: `YYYY-MM-DD`, both days included, in your
  local time. The date is when flib last processed the file (filed it, or sent
  it to review), not the date written in the document, which the index does not
  keep, nor the file's modification time.

```sh
flib search "power bill" --category finance --since 2026-01-01
flib search --ext pdf --until 2025-12-31 --json
```

An unknown category, a malformed extension or date, or `--since` after
`--until` stops with a message saying what is wrong.

#### Semantic search (optional)

Word search finds "electricity invoice" only if those words are in the index.
Semantic search also finds files by meaning: `flib search "power bill"
--semantic` finds a file described as an "electricity invoice". Each file gets
an *embedding*, a list of numbers that places texts with similar meaning close
together, and the query gets one too. The results of both searches are merged
(reciprocal rank fusion), so exact word matches still come first, and the
filters above apply to both. It is off by default. To turn it on with
[Ollama](https://ollama.com) on this Mac:

1. `ollama pull embeddinggemma` (about 600 MB; multilingual).
2. In the configuration:

   ```yaml
   search:
     semantic:
       enabled: true
   ```

3. `flib search --reindex` to embed the files already indexed. After that,
   every `flib run` embeds the files it adds or changes.

Then search with `--semantic`, or set `search.semantic.default: true` to make
it the default (`--words` then searches by words only). The MCP tool
`search_files` takes the same choice as its `semantic` flag.

What is embedded is only what the index already stores about a file: its
names, category, tags and the AI's description, never its content. A
sensitive file, or one matching `never_send`, is never embedded. With
Ollama on this Mac (the default `base_url`), nothing leaves it. Any server
with an OpenAI-compatible `/embeddings` endpoint works
(`search.semantic.base_url` and `model`; an optional key from
`FLIB_EMBEDDINGS_API_KEY`, or
`search.semantic.api_key_env`, or `flib key set --provider embeddings`, never
the classification provider's key). With a remote server, that index text
and every semantic query leave your Mac, `flib check-config` warns about it,
and the cost is not counted: check your server's prices. Anthropic has no
embeddings endpoint, so it cannot be used here.

If the embeddings server cannot be reached, or no file has an embedding yet,
the search says so in one line and searches by words. A run never fails
because of embeddings: what was not embedded is retried on the next run.
Changing `search.semantic.model` embeds the library again. Vectors are kept
in the same database and searched by brute force: about 0.1 s for 10,000
files and 0.6 s for 50,000.

## What flib reads

| Format | What is read |
| --- | --- |
| PDF | the text layer; a scanned PDF, only with [vision](#images-and-scanned-pdfs-vision) |
| Word, Excel, PowerPoint (`.docx`, `.xlsx`, `.pptx` and their macro versions) | the text; for a workbook, the sheet names and cell text |
| Plain text, Markdown, CSV, JSON, YAML, HTML, subtitles and source code | the text |
| E-mail (`.eml`) | subject, date, sender and recipients (names and domains only), attachment names, and the body (plain text, else the HTML's text) |
| Archives (`.zip`, `.tar.gz` and other tarballs) | the list of names inside; nothing is unpacked |
| Folders and `.app` bundles | the names of up to 100 files inside; an app's name and version |
| Images | nothing, unless [vision](#images-and-scanned-pdfs-vision) is on |
| Anything else (`.doc`, videos, installers...) | nothing: classified from its name and metadata |

A damaged or password-protected file is classified from its name and
metadata too. To add a format, see
[docs/adding-an-extractor.md](docs/adding-an-extractor.md).

## Configuration

Everything you can tune lives in one YAML file; see the commented
[`config.example.yaml`](config.example.yaml). Set `language` to `en` or
`pt-BR` to choose the language of generated names and descriptions and of the
default folders (`Organized`/`_Review` or `Organizado`/`_Revisar`) and
categories. Only the provider is required; everything else has a default.

The categories are a closed list, and each category's `hint` is the main lever
for fixing wrong classifications.

Each item moved into a category folder also gets the category name as a
Finder tag, so Finder's sidebar and Spotlight (`tag:Finance`) find it too.
Your own tags are kept, `flib undo` removes only the tag flib added, and
`finder_tags.enabled: false` turns this off.
The API key is read from `ANTHROPIC_API_KEY`, never from the file.

### Duplicates

A file with the same content as one flib already filed (the second
`report (1).pdf`, or a re-download of something in `Finance`) is moved to
`_Duplicates` (`_Duplicados` in pt-BR) inside the organized folder, under
its own name. It is never deleted, and it is not sent to the AI, so it costs
nothing. flib checks first that the filed copy is still there with the same
content. If it is not where flib left it (you renamed, moved, edited or
deleted it, or its disk is not connected), the new copy is left in Downloads
with a note to run `flib reconcile`; once reconcile has found the filed copy
gone, the next run files the new one in its place. A copy of a sensitive
file stays where it is, like the original.

```text
  dup     report (1).pdf  ->  _Duplicates/report (1).pdf  (same content as Finance/report.pdf)
```

`flib undo` puts duplicates back like any other move, and later runs leave
them there. `flib search` leaves duplicates out unless you add
`--duplicates`; `flib inspect` on one says which file it is a copy of, and
`flib status` counts them. If the original disappears, `flib reconcile`
lists its duplicate as orphaned; move it back to Downloads to have it filed.
Empty the folder whenever you like: flib never does. `duplicates: leave`
turns this off (copies then stay where they are), and `duplicates_folder`
renames the folder.

**Upgrading from an earlier version:** with the default `duplicates: move`,
the first run also sets aside the copies that earlier runs left in Downloads
(`report (1).pdf` next to a filed `report.pdf`). It says so once, in a note
after the run. Nothing is deleted: `flib undo` puts them back, and
`duplicates: leave` keeps them where they are.

### Names

A file is renamed only when the model read its content (text, or the image
with vision on), its category allows renaming, and the model is at least
`naming.rename_confidence_threshold` sure (0.8 by default). Videos,
installers, archives, folders and images with vision off keep their name.

The model also compares the file's current name with the subject it proposes
and says which describes the file better. A generic name (`scan001.pdf`,
`document (3).pdf`, `IMG_2041.jpg`) is replaced; a descriptive one keeps its
words, so two similar files stay told apart:

```text
Study guide - 11 tweaks for coding agents.pdf  ->  2026-11-16_study-guide-11-tweaks-for-coding-agents.pdf
scan001.pdf                                    ->  2026-10-05_electricity-bill.pdf
```

Either way flib, not the model, builds the final name: `naming.pattern`
(`{date}_{subject}` by default) with the document's date, or the download
date, or today; lower case words joined by `-`; at most `naming.max_length`
characters; the original extension. A date already at the start of the old
name is not repeated. `flib simulate` marks a kept name with "original name
kept", and `flib inspect` shows the choice and the model's reason.

### Images and scanned PDFs (vision)

Photos, screenshots and scanned PDFs have no text, so by default they are
classified from their name and metadata only and keep their name. Turn vision
on to let the model look at them:

```yaml
vision:
  enabled: true      # off by default
  max_image_px: 1568 # longer side of the image sent, in pixels
  max_pdf_pages: 3   # first pages sent of a PDF with no text layer
```

Images (`.jpg`, `.png`, `.heic`, `.webp`, `.gif`, `.tiff`, `.bmp`) are
converted to JPEG and shrunk by macOS's own `sips` (never enlarged), and
their metadata (EXIF, GPS position, camera details) is removed before sending.
A PDF whose pages have no text gets its first `max_pdf_pages` pages cut into a
new PDF, which is sent as a document. Because the model then sees the content,
these files can be renamed like any document (`IMG_4512.HEIC` becomes, say,
`2026-11-02_beach-at-sunset.HEIC`). If an image cannot be converted, or the
provider cannot read images, the file is classified from its metadata as with
vision off. `anthropic` reads images and scanned PDFs; `ollama` and
`openai-compat` read images only, and only with `provider.vision: true` (set it
when the model is a vision model); `claude-code` reads neither. Answers given with vision are cached apart from answers without
it, so turning it on or off never reuses the other kind.

**Estimated cost** on the default model (Claude Haiku 5.5, US$0.10 per
million input tokens and US$0.50 per million output tokens). An image costs
about width × height / 750 input tokens:

| What is sent | Image tokens | Whole request, estimated |
| --- | --- | --- |
| Photo, 4:3, shrunk to 1568 × 1176 | ~2,460 | ~US$0.0005 |
| Screenshot, 16:10, shrunk to 1568 × 980 | ~2,050 | ~US$0.0005 |
| Scanned PDF, 3 pages | ~1,600-2,500 per page | ~US$0.001 |

"Whole request" adds about 1,000 tokens of instructions and metadata and a
few hundred output tokens. These are estimates from the published formula,
not measurements; what counts toward `limits.max_cost_usd_per_run` is the
usage the API actually bills, so the cap holds either way. Roughly 2,000
photos fit in the default US$1 cap. Lowering `max_image_px` lowers the cost
quadratically (1024 px: about 40% of it).

### Teaching it

When a file lands in the wrong place, correct it:

```sh
flib fix ~/Downloads/Organized/Other/scan.pdf --category Finance
flib fix ~/Downloads/Organized/_Review/document.pdf --category Contracts --name lease-2026
```

The file moves (a taken name gets `-2`, but changing only the capitalization
renames in place; a missing extension is kept), the index, search and the
Finder tag follow, and `flib undo` reverts it like a run. If flib is
interrupted mid-fix, the next command finishes the whole fix or leaves the
file as it was. Corrections,
these and the moves or renames `flib reconcile` finds, are sent as examples
with later requests, so the classifier stops repeating the mistake.
`provider.max_examples` caps how many (10 by default; `0` sends none).

### Changing your rules

New files follow the configuration as it is when they arrive. After you
change categories, their hints, the naming rules or the model, file what is
already organized again:

```sh
flib reprocess --simulate          # classify and show what would move; moves nothing
flib reprocess                     # asks before sending, then before moving
flib reprocess --category finance --since 2026-01-01 --rename
```

It works on files in the library only (organized or in review), never on the
input folders, and never sends a file flagged sensitive, nor the content of
one whose current or original name matches `never_send`. Before anything is
sent it says how many files the cache already answers and estimates the cost
of the rest, and asks; the per-run cost limit
(`limits.max_cost_usd_per_run`) still applies, and running it again goes on
where it stopped, the cache answering what was done. Then it shows the plan
and asks again (`--yes` answers both). `--simulate` still sends the files
(after the same question, so it can cost money; the answers are cached for
the real run) and stops before moving anything.

A file whose answer did not change stays as it is; one the AI is now unsure
about stays in its category; one you put in a sub-folder of its category
stays there unless its category changes. File names are kept: only
`--rename` applies the current naming rules to them too. The filters are the
ones of `flib search` (`--category`, `--since`, `--until`, `--ext`). The
questions are asked before flib takes its lock, so a scheduled run is never
held up by an unanswered one; each file is checked again before it moves.
Only one `flib reprocess` runs at a time. `flib undo` also gives back the
descriptions and tags it replaced.

Your corrections win: a category or a name you set with `flib fix` (or by
moving the file and running `flib reconcile`) is kept, unless you add
`--override-corrections`. Everything moved is one run: `flib undo` puts the
whole reprocess back, Finder tags included.

Renaming or removing a category needs one explicit step. Edit the
configuration first; every command then warns that organized files still use
a category it no longer has, and plain `flib reprocess` refuses until you
say what happened:

```sh
flib reprocess --rename-category Docs Documents  # move Docs/ into Documents/, no AI
flib reprocess --remove-category Misc            # reclassify Misc among the others
flib reprocess --remove-category Misc --to-review  # or send all of it to review
```

A rename keeps file names (a taken one gets `-2`), and your corrections to
the old name now point at the new one; a rename that only changes the case
changes the index and the Finder tags. Files the AI is unsure about after a
removal go to review. `flib undo` reverts a migration like any reprocess,
the index side included (category names in rows and corrections), and makes
the emptied folder again if you deleted it.

After a reprocess that changed nothing (every move failed or was skipped),
plain `flib undo` refuses instead of undoing an earlier run; name that run
with `--run ID` if you meant it.

## Use it from Claude

`flib mcp` is a local [MCP](https://modelcontextprotocol.io) server over
stdio, so Claude (or any MCP client) can answer "where is last month's power
bill?" from your library. It runs only while the client is open, uses no
network and reads the same database as `flib search`.

| Tool | What it does |
| --- | --- |
| `search_files` | Full-text search over names, descriptions and tags, with the same category, extension and period filters as `flib search`; `semantic: true` also searches by meaning ([Semantic search](#semantic-search-optional)) |
| `get_file` | Everything the index knows about one file: where it is and was, category, description, tags, hash, size, download origin, whether it is only in iCloud, why it is in review (if it is), and its history (moves, renames, fixes, undos with when each was undone; newest first, last 20) |
| `list_recent` | The files organized most recently, newest first, optionally in one category |
| `list_review` | What waits in the review folder, newest first, with the AI's confidence and why each file is there (see below) |
| `get_stats` | Counts by status and by category, total size filed, the last run and its provider cost, and this month's total cost |
| `recategorize_file` | Move a file to another configured category and/or rename it, like `flib fix` |
| `undo` | Revert a run or the last move of one file, like `flib undo` |

A file in review says why it is there: `low_confidence` (the AI's confidence
was below `limits.confidence_threshold`), `unknown_category` (the AI chose a
category you have not configured), `category_removed` (`flib reprocess
--remove-category --to-review`) or `moved_by_hand` (`flib reconcile` found it
in the review folder). The first two come with the category the AI suggested.
Files filed before flib recorded reasons show none.

Costs are estimates, recorded per run (`flib run`, `flib reprocess`; a `flib
fix` costs nothing). A provider whose prices flib does not know (an
`openai-compat` server without `price_input_per_mtok`/`price_output_per_mtok`)
records its cost as unknown, not as zero; `get_stats` counts those runs apart.

**1. Enable it.** The MCP server is optional, because its SDK adds 16
packages that the rest of flib does not need. Answer yes when
`./scripts/install.sh` asks, or run it again with the flag:

```sh
./scripts/install.sh --with-mcp
```

Later updates keep it; `--without-mcp` removes it. Without it, `flib mcp`
says how to enable it.

Results carry metadata and descriptions, never file content; sensitive files
show no description, tags or origin. The write tools go through the same
journal as the CLI, so `flib undo` reverts what an assistant did, and they
refuse to run while a run is in progress. Set `mcp.read_only: true` to offer
only the read tools.

**2. Add it to Claude Code:**

```sh
claude mcp add --scope user file-librarian -- ~/.local/bin/flib mcp
```

**Or to Claude Desktop:** add this to
`~/Library/Application Support/Claude/claude_desktop_config.json` and restart
it. Desktop does not expand `~` or read your shell's `PATH`, so give the
absolute path that `./scripts/install.sh` printed:

```json
{
  "mcpServers": {
    "file-librarian": {
      "command": "/Users/you/.local/bin/flib",
      "args": ["mcp"]
    }
  }
}
```

Add `"--config", "/path/to/config.yaml"` to `args` if your configuration is
not in the default place.

## Privacy

With a remote provider such as `anthropic`, this is what leaves your Mac
for each file:

- its name, extension, size, download date and the URL it was downloaded
  from;
- at most `provider.max_chars_per_file` characters of its text (4,000 by
  default), or for a folder, the names of up to 100 files inside it. For an
  e-mail that text starts with its subject, date, the senders' and
  recipients' names and the domains of their addresses (never the part
  before the `@`), and its attachments' names; the body follows as it is,
  like any document's text;
- only with `vision.enabled` (off by default): for an image, the image
  itself, shrunk to `vision.max_image_px` and stripped of its metadata; for a
  PDF with no text layer, its first `vision.max_pdf_pages` pages. Secret
  detection reads text, not pixels: a photo of a document or a screenshot of
  a password goes as it is. Keep such files out with `never_send`.

With every request also go your most recent corrections (at most
`provider.max_examples`): for each, the corrected file's extension, the
description the provider wrote for it earlier, and the category or name before
and after. Never file content, and never from files matching `never_send` or
flagged sensitive.

Two kinds of files are treated differently:

- **Matching `never_send`:** only the metadata above is sent, never the
  content. Add `"*.eml"` to keep e-mail messages to their metadata.
- **Credential names (`id_rsa`, `*.pem`…) or secrets found in the content:**
  nothing at all is sent, the file stays where it is, and the index records
  only that it is sensitive.

With `claude-code` the same goes to Anthropic through the `claude` CLI, except
images and PDF pages, which it never sends. With `openai-compat` it goes to
the server at `provider.base_url`, except PDF pages, which it never sends.

With `ollama` on this Mac (the default `base_url`), nothing leaves the
machine. The same rules still apply: `never_send` files send only their
metadata to the local model and sensitive files send nothing.

The index and the logs stay on your Mac. The MCP server sends an assistant
only what `flib search` would show you, never file content.

[Semantic search](#semantic-search-optional) is off by default. On, it sends
the index's names, categories, tags and descriptions (never content, never a
sensitive or `never_send` file) and your semantic queries to
`search.semantic.base_url`: Ollama on this Mac by default, so nothing leaves
it unless you point it elsewhere.

## Troubleshooting

When something does not work, `flib check-config` validates the
configuration and `flib schedule --status` shows the daily run. To report a
bug, run:

```sh
flib doctor
```

It writes one file, `doctor-<date>-<time>.txt`, and prints its path. The file
holds flib's, Python's and macOS's versions, how flib was installed, whether
the MCP extra is there, the configuration (validated), where the API key comes
from, the schedule's state, the database's schema version and counts, what
`flib status` shows, the last runs with their cost and the last five run logs (`--logs N` for more). It is made to be posted
in a public issue, so before it is written:

- the API key is left out entirely, not even its last characters; only the
  word for where it comes from (`environment`, `keychain`, `env_file`, `none`);
- folders become `<home>`, `<input-1>`, `<output>` or `<data>` (flib's own
  folder), and other folder names `<dir-N>`; category names and the review
  folder stay, as they come from the configuration;
- file names become `<file-N>` with only the extension kept (`<file-3>.pdf`),
  the same number for the same name, so one file can be followed through the logs;
- anything that looks like a secret, e-mail addresses, URLs, quoted text, your
  account name, category hints and `ignore`/`never_send` patterns that name
  something are replaced as well.

Read the file before you attach it: redaction is careful, not infallible.
`--stdout` prints the report instead, `--output PATH` writes it elsewhere, and
`--format markdown` makes it paste-ready for an issue.

The report goes into flib's logs folder
(`~/Library/Application Support/file-librarian/logs`), owner-only, keeping the
last ten, rather than on the Desktop: flib writes nowhere new, and on many Macs
the Desktop is synced to iCloud, which would upload a file you have not read
yet. `open -R` on the printed path shows it in Finder, ready to drag into the
issue.

### A file is skipped as "unreadable"

flib could not look at the file, so it left it where it is and will try again
on the next run. The run summary and the run log say what failed: the step
(`stat` reads the file's details, `read` its content) and the error macOS gave,
for example:

```text
  skip    photo.jpeg  (unreadable: EPERM Operation not permitted, on read)
```

- `EPERM` or `EACCES` in the daily run: macOS keeps the scheduled run out of
  that file. Give `flib-python` Full Disk Access as described in
  [Run it every day](#run-it-every-day), then run `flib schedule` again to
  check. A scheduled run that hits this ends its log with a hint saying so.
  In a manual run, check the file's permissions (`ls -l`).
- `EDEADLK`, `EAGAIN`, `EINTR`, `ETIMEDOUT`, `EBUSY` or `ESTALE`: another
  program (a sync client, an antivirus, Spotlight) had the file busy. flib
  already tried once more after half a second; the next run usually reads it.
- Anything else, or the same file failing every day: run `flib doctor` and
  open an issue with the report.

## Roadmap

| Milestone | Delivers |
| --- | --- |
| M0 Foundation | Repository, CI, database schema, empty CLI commands, contributor docs, download-origin prototype |
| M1 MVP | All P0 requirements with the `anthropic` provider |
| M2 Daily use | Scheduling, reconcile, init, images and scanned PDFs, Finder tags, learning from corrections, iCloud, MCP server, `claude-code` provider |
| M3 Open | `ollama` and `openai-compat` providers, search filters, reprocess, doctor, extension guides |
| v1.0 | Duplicates, optional semantic search, periodic summary, PyPI release |

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md),
[docs/architecture.md](docs/architecture.md) and the
[requirements](docs/requirements.md). Adding a file format or an AI provider
has its own guide: [docs/adding-an-extractor.md](docs/adding-an-extractor.md)
and [docs/adding-a-provider.md](docs/adding-a-provider.md). Questions go to
[Discussions](https://github.com/luizpereiradev/file-librarian/discussions).

## License

[MIT](LICENSE)
