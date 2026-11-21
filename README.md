# file-librarian

Keeps your Downloads folder organized. Once a day, `flib` reads each new file,
gives it a descriptive name, moves it into a category folder and writes a
searchable description to a local SQLite index.

`documento(3).pdf` becomes `Finance/2026-10-05_electricity-invoice.pdf`, and
`flib search "electricity october"` finds it months later.

> **Status: alpha, getting ready for v0.1.0.** Everything in this README is
> implemented and tested. The first release is tagged after two weeks of
> daily use ([#24](https://github.com/luizpereiradev/file-librarian/issues/24));
> until then, install from the repository as shown below. macOS only.

## What it promises

**The AI only decides. Deterministic code moves files.** The organizing
commands keep these guarantees:

- **Nothing is deleted and nothing is overwritten.** The one exception: when
  the destination is on another volume (an external disk), the item is
  copied, the copy is checked against the original's hash, and only then is
  the original removed; extended attributes (download origin, Finder tags),
  permissions and dates go with it. exFAT and FAT drives cannot rename
  without replacing, so flib does not move anything onto them.
- **Every run is recorded and can be undone**, except files you changed or
  moved afterwards, which are skipped and listed.
- **Nothing is downloaded from iCloud.** Files that exist only in the cloud,
  and folders with any such file inside, are skipped and listed until you
  download them. `flib reconcile` and `flib inspect` do not read them either.
- **Secrets stay put.** Files with credential names or secrets in their
  content are never sent to the AI and never moved; `never_send` patterns
  send only metadata. [Privacy](#privacy) lists exactly what leaves your Mac.

## Requirements

- The current major release of macOS (the only supported platform)
- [uv](https://docs.astral.sh/uv/) 0.13 or newer (`brew install uv`); it
  installs Python 3.11+ for you
- One AI provider:
  - an Anthropic API key, for the default `anthropic` provider;
  - or [Claude Code](https://code.claude.com) logged in to your Claude
    subscription, for `claude-code` (no key);
  - or [Ollama](https://ollama.com) with a model pulled, for `ollama`
    (nothing leaves your Mac);
  - or any OpenAI-compatible API, for `openai-compat`.

To try flib without any of them, set `provider: {name: fake, model: offline}`:
everything is filed under the last category, offline.

## Install

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
./scripts/install.sh      # installs `flib` with the locked dependency versions;
                          # asks whether to add the optional MCP server
flib init                 # a few questions, a trial on copies of your files, the daily run
```

To update, `git pull` and run `./scripts/install.sh` again.

### First run: `flib init`

`flib init` asks for the language, the folder to organize and where organized
files go, the provider and, with `anthropic`, whether to turn on
[vision](#images-and-scanned-pdfs-vision) (default no). Every question has a
default, so pressing Enter all the way gives a working setup (`flib init
--yes` takes them all without asking):

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

To set it up by hand instead:

```sh
mkdir -p ~/.config/file-librarian
cp config.example.yaml ~/.config/file-librarian/config.yaml
flib key set              # stores your API key in the macOS Keychain (anthropic, openai-compat)
flib check-config         # validates the configuration and finds the key
```

## Daily use

```sh
flib simulate             # the plan for your real folder; nothing moves
flib run                  # organize now
flib undo                 # revert the last run (--run ID for another, --file PATH for one file)
flib search "power bill"  # find a file again
flib status               # what waits for you
```

Files the AI is not sure about go to the review folder (`_Review` inside
`~/Downloads/Organized` by default) for you to decide.

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
--refresh`); macOS may then ask for access again.

If the Mac is asleep at the scheduled time, the run happens on wake. If it
runs while the Mac is locked and skips files that macOS protects until the
unlock, it waits for the unlock and runs once more
([details](docs/troubleshooting.md#a-file-is-skipped-as-locked)). Each run
writes a log to `~/Library/Application Support/file-librarian/logs`, ending
with its estimated provider cost.

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
first one); `--json` gives the same for scripts.

You do not have to remember to run it: after a *scheduled* run, when a file
has waited in review or failed for two days or more, flib writes a one-line
summary to the run log and shows one macOS notification, such as "3 files
wait for review, 1 failed. Run `flib status` for details." It does this at
most once a week, and never after a `flib run` you started yourself. The
notification holds counts only, never a file name, because notifications show
on the lock screen. Change it under `summary:` in the configuration:
`notify: false` keeps only the log line, `every_days` and `min_age_days` set
how often and how patient. If you never see the notification, see
[Troubleshooting](docs/troubleshooting.md#no-notification-appears).

### Teaching it

When a file lands in the wrong place, correct it:

```sh
flib fix ~/Downloads/Organized/Other/scan.pdf --category Finance
flib fix ~/Downloads/Organized/_Review/document.pdf --category Contracts --name lease-2026
```

The file moves, the index, search and the Finder tag follow, and `flib undo`
reverts it like a run. Moving or renaming a file by hand works too: run
`flib reconcile` and the index catches up. Both kinds of correction are sent
as examples with later requests, so the classifier stops repeating the
mistake (`provider.max_examples`, 10 by default; `0` sends none).

The categories are a closed list, and each category's `hint` in the
configuration is the main lever for fixing wrong classifications. After you
change categories, hints, naming rules or the model, `flib reprocess` files
what is already organized again:

```sh
flib reprocess --simulate          # classify and show what would move; moves nothing
flib reprocess                     # asks before sending, then before moving
flib reprocess --rename-category Docs Documents   # after renaming a category in the config
flib reprocess --remove-category Misc             # after removing one
```

It asks before it sends anything (with a cost estimate) and before it moves
anything, keeps your corrections and file names unless told otherwise, and
`flib undo` puts the whole reprocess back. The details are in
[the guide](docs/guide.md#changing-your-rules).

### Names

A file is renamed only when the model read its content (text, text
[recognised by OCR](#text-in-images-and-scanned-pdfs-ocr), or the image with
vision on), its category allows renaming, and the model is at least
`naming.rename_confidence_threshold` sure (0.8 by default). Videos,
installers, archives, folders and photos with vision off keep their name.
A generic name (`scan001.pdf`, `IMG_2041.jpg`) is replaced; a descriptive one
keeps its words:

```text
Study guide - 11 tweaks for coding agents.pdf  ->  2026-11-16_study-guide-11-tweaks-for-coding-agents.pdf
scan001.pdf                                    ->  2026-10-05_electricity-bill.pdf
```

flib, not the model, builds the final name from `naming.pattern`
(`{date}_{subject}` by default); [the guide](docs/guide.md#names) has the
rules.

### Sub-folders by year or issuer

A category is one flat folder unless you give it a `layout`:

```yaml
categories:
  - name: Finance
    layout: "{year}"            # Finance/2026/…
  - name: Contracts
    layout: "{issuer}"          # Contracts/Acme/…
```

`{year}` and `{month}` come from the document's date (else the download
date, else the day it was filed); `{issuer}` is the organisation the
document comes from, as the AI names it (the prompt asks for an
organisation, not a private person; flib itself only drops a name holding a
phone or document number). The same company lands in the same folder:
`Acme Inc.`, `ACME` and `Acme Ltda` join the folder already there, spelled
as it was the first time (in every year's folder with `{year}/{issuer}`),
while different names stay apart. Without an issuer, a file stops at the deepest
folder known (`Finance/2026/` for `{year}/{issuer}`). To move what is
already organized into a new layout, without the AI:

```sh
flib reprocess --apply-layout --simulate   # show what would move
flib reprocess --apply-layout              # asks, then moves; flib undo reverts it
```

Files you moved yourself stay where you put them. The details are in
[the guide](docs/guide.md#sub-folders-inside-a-category).

### Duplicates

A file with the same content as one flib already filed (the second
`report (1).pdf`, or a re-download of something in `Finance`) is moved to
`_Duplicates` (`_Duplicados` in pt-BR) inside the organized folder, under its
own name. It is never deleted, and it is not sent to the AI, so it costs
nothing:

```text
  dup     report (1).pdf  ->  _Duplicates/report (1).pdf  (same content as Finance/report.pdf)
```

`flib undo` puts duplicates back like any other move. `flib search` leaves
them out unless you add `--duplicates`. Empty the folder whenever you like:
flib never does. `duplicates: leave` turns this off, and `duplicates_folder`
renames the folder. [The guide](docs/guide.md#duplicates) covers the edge
cases, including what the first run does with copies earlier versions left
in Downloads.

## Commands

Every command takes `--config PATH` (`-c`); without it, flib reads
`$FLIB_CONFIG` or `~/.config/file-librarian/config.yaml`.

| Command | What it does |
| --- | --- |
| `flib init [--yes] [--sample N] [--force]` | Guided setup with a trial on copies of recent files ([First run](#first-run-flib-init)) |
| `flib check-config` | Validate the configuration and find the API key, without touching files |
| `flib key set\|status\|delete [--provider NAME]` | Store, locate or remove an API key in the macOS Keychain (`anthropic`, `openai-compat`, `embeddings`) |
| `flib simulate` | Print the full plan without changing anything |
| `flib run` | Classify, rename, move and index new files |
| `flib undo [--run ID \| --file PATH]` | Revert the last run, a given run, or the last operation on one file |
| `flib search [QUERY] [--category NAME] [--since DATE] [--until DATE] [--ext EXT] [--issuer NAME] [--kind KIND] [--min-amount N] [--max-amount N] [--currency CODE] [--due-before DATE] [--due-after DATE] [--period YYYY[-MM]] [--sum [--by GROUP]] [--semantic \| --no-semantic] [--reindex] [--duplicates] [--limit N] [--json]` | Search names, descriptions and tags, narrowed by category, period, extension or what the document states (issuer, kind, amount, due date); add up amounts with `--sum`; optionally by meaning ([Searching](#searching)) |
| `flib status [--json] [--sensitive]` | What waits for you: files in review and why, failures, files left in place as sensitive, the last and next run, this month's cost ([Staying on top of it](#staying-on-top-of-it)); `--sensitive` lists each file left in place as sensitive and why |
| `flib fix FILE [--category NAME] [--name NAME]` | Correct one file's category or name; flib learns from it ([Teaching it](#teaching-it)) |
| `flib reconcile` | Update the index after you move, rename or delete files by hand; files only in iCloud are left unchecked |
| `flib reprocess [--category NAME] [--since DATE] [--until DATE] [--ext EXT] [--rename] [--override-corrections] [--simulate] [--yes]` | Reclassify organized files after the rules change; `--rename-category OLD NEW` and `--remove-category OLD [--to-review]` migrate a category ([guide](docs/guide.md#changing-your-rules)); `--apply-layout [--category NAME] [--ask-issuer]` moves organized files into their category's layout ([Sub-folders](#sub-folders-by-year-or-issuer)); `--scrub-descriptions` removes identifiers from stored descriptions, no AI ([Privacy](#privacy)); `--fill-fields` asks only for the amount, dates, issuer, period and kind of files filed before flib kept them ([Amounts and dates](#amounts-dates-and-issuers)) |
| `flib schedule [--at HH:MM \| --status \| --remove \| --refresh] [--no-save-key] [--no-check]` | Run every day via launchd, with an access check ([Run it every day](#run-it-every-day)) |
| `flib inspect FILE... [--json]` | Show hash, size and download origin (no hash for a file only in iCloud), and for an indexed file its status, category, why it is in review, the name choice and what the AI read in it (kind, issuer, amount, dates, period) |
| `flib mcp` | Local MCP server for AI assistants ([Use it from Claude](#use-it-from-claude)) |
| `flib eval [--provider NAME] [--model M] [--language en\|pt-BR] [--limit N] [--json] [--out FILE] [--compare FILE]` | Measure classification quality on a built-in synthetic corpus, never touching your files ([Measuring quality](#measuring-quality)) |
| `flib doctor [--stdout \| -o PATH] [--format markdown] [--logs N]` | A redacted diagnostics report for bug reports ([Troubleshooting](#troubleshooting)) |
| `flib --version` | Print the version |

## Searching

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
  it to review), not the date written in the document (`--period` filters by
  that, see [below](#amounts-dates-and-issuers)), nor the file's modification
  time.
- `--limit N`: at most this many results (20 by default, up to 500).

```sh
flib search "power bill" --category finance --since 2026-01-01
flib search --ext pdf --until 2025-12-31 --json
```

An unknown category, a malformed extension or date, or `--since` after
`--until` stops with a message saying what is wrong.

### Amounts, dates and issuers

When flib files a document, the AI also notes what the document itself
states, and leaves out what it does not (it is asked never to guess):

- `kind`: one of `bill`, `receipt`, `invoice`, `statement`, `contract`,
  `payslip`, `tax`, `ticket`, `policy`, `report`, `form`, `letter`,
  `manual`, `other`;
- `issuer`: the organisation it comes from (never a private person);
- `amount` and `currency`: the total or amount due (not a balance or any
  other number), with its ISO 4217 code;
- `document_date`, `due_date`, and `period`: the month or year it refers to
  (the billing month of a bill, the tax year).

Numbers are read whatever the format (`1.234,56`, `1,234.56`, `R$`, `€`);
a value flib cannot read is dropped, never guessed, and document, account and
ID numbers are never kept. Results show the fields on one line
(`bill · Power Co · USD 103.91 · due 2026-09-20 · for 2026-08`), and these
filters combine with the others and with a query:

- `--issuer NAME`: case, accents, punctuation and legal forms (`Inc`, `Ltd`,
  `S.A.`) do not matter, so `--issuer "power co"` finds `POWER CO, Inc.`;
- `--kind KIND`: repeat it or use commas for any of several;
- `--min-amount N` / `--max-amount N`, both included; `--currency CODE`;
- `--due-before DATE` / `--due-after DATE`, the day itself excluded;
- `--period 2026` or `--period 2026-09`: the period the document refers to,
  else the month of its date.

`--sum` adds up the amounts of every match instead of listing them, per
currency (two currencies are never added together), and `--by issuer`,
`month`, `year`, `kind` or `category` groups the totals:

```sh
flib search --issuer "Power Co" --period 2026 --sum     # what did I pay Power Co in 2026?
flib search --kind bill --period 2026 --sum --by month --json
flib search --kind bill --due-after 2026-11-20          # bills still to pay
```

Files filed before flib kept these fields have none: `flib reprocess
--fill-fields` asks the AI about those files only, after showing the cost,
records the fields in the index (nothing moves, categories and names stay)
and `flib undo` reverts it. With `--sum`, a query matches words only, not
meaning.

### Semantic search (optional)

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
it the default (`--no-semantic` then searches by words only, for one search).
The MCP tool `search_files` takes the same choice as its `semantic` flag:
`true`, `false` (words only, even when the default is by meaning) or unset
(the default). With `--json`, and in MCP results, each file says how it was
found (`source`: `text`, `semantic` or `both`) and, when it has an embedding,
its `similarity` to the query.

A search by meaning always has a "nearest" file, even when nothing is related,
so files found **only** by meaning must be at least
`search.semantic.min_similarity` similar to the query (cosine, 0 to 1; 0.25 by
default). A file that matches the words always stays, however low its
similarity. When the floor leaves nothing, the search says "No close
matches; try other words or --no-semantic".

The right floor depends on the model. 0.25 was measured for `embeddinggemma`
on a synthetic corpus of 60 files in Portuguese and English and 32 queries:
it kept 43 of 45 related pairs (the two lost were loose ones, like "credit
card statement" for a bank statement, at 0.20) and dropped 98% of unrelated
ones, leaving on average about one unrelated file per query instead of 18.
For another model, search for a few things you know are there with `--json`,
look at the `similarity` of the files you wanted and of the ones you did not,
and set `min_similarity` between them; `0` turns the floor off.

`embeddinggemma` places a query and a document best when told which is which:
flib puts `task: search result | query: ` before a query and `title: none |
text: ` before a file's text, as its model card asks. Without them, unrelated
files scored nearly as high as related ones (median 0.22, and 5% above 0.34)
and no floor could tell them apart. Other models get no prefix unless
`search.semantic.query_prefix` and `document_prefix` say so (`""` turns one
off). Changing the document prefix embeds the library again on the next run.

What is embedded is only what the index already stores about a file: its
names, category, tags and the AI's description, never its content. A
sensitive file, or one matching `never_send`, is never embedded. Any server
with an OpenAI-compatible `/embeddings` endpoint works
(`search.semantic.base_url` and `model`). Its optional key comes from
`FLIB_EMBEDDINGS_API_KEY`, or the variable `search.semantic.api_key_env`
names, or `flib key set --provider embeddings`; never from the classification
provider's key. With a remote server, that index text and every semantic
query leave your Mac, `flib check-config` warns about it, and the cost is not
counted: check your server's prices. Anthropic has no embeddings endpoint, so
it cannot be used here.

If the embeddings server cannot be reached, or no file has an embedding yet,
the search says so in one line and searches by words. A run never fails
because of embeddings: what was not embedded is retried on the next run.
Changing `search.semantic.model` embeds the library again. Vectors are kept
in the same database and searched by brute force: about 0.1 s for 10,000
files and 0.6 s for 50,000.

## Providers

`provider.name` picks who classifies. All of them get the same prompt and
the same validation: a category outside your list fails the file.

### Anthropic (`anthropic`, the default)

```yaml
provider:
  name: anthropic
  model: claude-haiku-5-5    # fast and cheap; claude-sonnet-5-5 or claude-opus-5-5 for harder files
```

`provider.api_key_source: auto` (the default) takes the key from the first
place it finds one:

1. `ANTHROPIC_API_KEY` in the environment;
2. the macOS Keychain (`flib key set`; `flib key status` shows which is used);
3. the `.env` file named by `provider.env_file`, if you set one.

Scheduled runs don't see your shell's environment, so the Keychain is the
easiest choice for them. Set `api_key_source` to `environment`, `keychain` or
`env_file` to use only one place. The key never goes in the configuration
file.

Each provider keeps its own key: `flib key set --provider openai-compat`
stores the `openai-compat` key in a separate Keychain entry, so it never
replaces the Anthropic one (`flib key set`, `status` and `delete` act on the
configured provider's key when `--provider` is not given).

### Your Claude subscription (`claude-code`)

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
- `claude -p` takes text only, so `vision.enabled` has no effect with this
  provider: scanned PDFs and screenshots are classified from the text
  [OCR](#text-in-images-and-scanned-pdfs-ocr) recognises on your Mac, and
  photos from their metadata.

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
  is shown and recorded as unknown (a server on this Mac counts as free).
  `limits.max_files_per_run` still caps how many files a run sends.
- **Answers:** flib asks for structured output with a JSON schema. A server
  that rejects that is asked again with plain JSON mode and the schema in the
  instructions, and flib keeps using JSON mode for the rest of the run.
- **Images:** sent only with `provider.vision: true` and `vision.enabled`.
  Scanned PDFs are never sent to this provider; they are classified from the
  text [OCR](#text-in-images-and-scanned-pdfs-ocr) recognises on your Mac.
- **Where data goes:** only to `base_url`. Redirects are refused, user names
  or passwords in the URL are rejected (use the Keychain), and `flib
  check-config` warns about `http://` to another machine.

## What flib reads

| Format | What is read |
| --- | --- |
| PDF | the text layer (first 20 pages); for a scanned PDF, the text [OCR](#text-in-images-and-scanned-pdfs-ocr) recognises in its first 3 pages, on your Mac |
| Word, Excel, PowerPoint (`.docx`, `.xlsx`, `.pptx` and their macro versions) | the text; for a workbook, the sheet names and cell text |
| Plain text, Markdown, CSV, JSON, YAML, HTML, subtitles and source code | the text |
| E-mail (`.eml`) | subject, date, sender and recipients (names and domains only), attachment names, and the body (plain text, else the HTML's text) |
| Archives (`.zip`, `.tar.gz` and other tarballs) | the list of names inside; nothing is unpacked |
| Folders and `.app` bundles | the names of up to 100 files inside; an app's name and version |
| Images | the text [OCR](#text-in-images-and-scanned-pdfs-ocr) recognises in them, on your Mac; a photo with little or no text, nothing unless [vision](#images-and-scanned-pdfs-vision) is on |
| Anything else (`.doc`, videos, installers...) | nothing: classified from its name and metadata |

At most `provider.max_chars_per_file` characters (4,000 by default) of the
text are sent. A damaged or password-protected file is classified from its
name and metadata too. To add a format, see
[docs/adding-an-extractor.md](docs/adding-an-extractor.md).

## Configuration

Everything you can tune lives in one YAML file; the commented
[`config.example.yaml`](config.example.yaml) lists every option with its
default. Only `provider.model` is required; everything else has a default.
`flib check-config` validates the file, and an invalid one stops a run before
any file is touched. API keys never go in this file.

The options you are most likely to change:

| Option | Default | What it does |
| --- | --- | --- |
| `language` | `en` | `en` or `pt-BR`: the language of generated names and descriptions, and of the default folders (`Organized`/`_Review` or `Organizado`/`_Revisar`) and categories |
| `inputs` | `[~/Downloads]` | The folders to keep organized; each can have its own rules ([More than one inbox](#more-than-one-inbox)) |
| `output_root` | `~/Downloads/Organized` | Where category folders are created |
| `categories` | 5 in `en`, 15 in `pt-BR` | The closed list the AI chooses from; each `hint` steers it, `rename: false` keeps original names, `layout` files into sub-folders ([Sub-folders](#sub-folders-by-year-or-issuer)) |
| `never_send` | `*.pem`, `*.key`, `.env*` in the example | Patterns classified from metadata only |
| `limits.confidence_threshold` | `0.7` | Below this, a file goes to review |
| `limits.max_cost_usd_per_run` | `1.00` | Estimated cost cap per run; the rest waits for the next run (`null` = no cap) |
| `limits.max_files_per_run` | `200` | Files sent per run |
| `naming.pattern` | `{date}_{subject}` | New names; see [Names](#names) |
| `duplicates` | `move` | `leave` keeps copies where they are ([Duplicates](#duplicates)) |
| `finder_tags.enabled` | `true` | Tag each filed item with its category in Finder |
| `ocr.enabled` | `true` | Read the text in images and scanned PDFs on your Mac ([OCR](#text-in-images-and-scanned-pdfs-ocr)) |
| `vision.enabled` | `false` | Let the model look at images and scanned PDFs |
| `schedule.time` | `12:30` | When the daily run starts (applied by `flib schedule`) |
| `summary.notify` | `true` | The periodic notification about files waiting for you |
| `search.semantic.enabled` | `false` | [Semantic search](#semantic-search-optional) |
| `mcp.read_only` | `false` | Offer only the read tools in `flib mcp` |
| `privacy.descriptions` | `redact` | `verbatim` stores descriptions and tags exactly as the AI wrote them ([Privacy](#privacy)) |

**Finder tags.** Each item moved into a category folder also gets the
category name as a Finder tag, so Finder's sidebar and Spotlight
(`tag:Finance`) find it too. Your own tags are kept, and `flib undo` removes
only the tag flib added.

### More than one inbox

`inputs` takes plain paths, or a mapping with rules for that folder alone.
The Desktop holds work in progress, so be conservative there; a screenshots
folder can go straight to one category with no AI call at all:

```yaml
inputs:
  - ~/Downloads                       # everything at its top level, as before
  - path: ~/Desktop
    only: ["*.pdf", "*.png", "Screenshot*"]   # everything else is left alone
    min_age_days: 7                   # only what has not changed for a week
    folders: ignore
  - path: ~/Pictures/Screenshots     # a folder just for screenshots
    category: Screenshots             # a configured category; no AI, no cost
    layout: "{year}/{month}"
    only: ["Screenshot*.png", "Screen Recording*.mov"]
    folders: ignore
```

A fixed category files everything it takes without looking at it, so give
it an `only` (`flib check-config` warns when there is none).

The options are `only`, `ignore` (added to the global list), `min_age_days`,
a fixed `category` (with `rename` and `layout`), `folders` and `duplicates`.
`flib check-config` prints each input with the rules it ends up with. Inputs
may not overlap or sit inside `output_root`. `flib init` offers the Desktop
(documents and images untouched for a week, folders left alone) and your
screenshots folder when it holds nothing else; both default to no. An input
folder that cannot be read is skipped with a warning; the others are still
organized.
Each folder is a separate macOS permission for the daily run, which
`flib schedule` checks one by one. The details, including how the age is
counted, are in [the guide](docs/guide.md#more-than-one-inbox).

### Text in images and scanned PDFs (OCR)

Screenshots, photographed receipts and scanned PDFs have no text layer. flib
reads their text on your Mac with macOS's own text recognition (the Vision
framework, run through `osascript`, which every Mac has): nothing is
installed, nothing is sent anywhere for it, and it works from the scheduled
run. The recognised text is then the file's text like any document's: it is
scanned for secrets (a screenshot of a password or an API key makes the file
sensitive, and nothing is sent), `never_send` and
`provider.max_chars_per_file` apply, and the file can be renamed. Every
provider benefits, including those that read text only.

```yaml
ocr:
  enabled: true              # on by default
  languages: [pt-BR, en-US]  # default from `language`: en gives [en-US]
  max_pages: 3               # first pages read of a PDF with no text layer (1-20)
  min_chars: 40              # fewer letters and digits than this: a photo, not a document
```

An image with less text than `min_chars` (a photo) is handled as before:
with [vision](#images-and-scanned-pdfs-vision) on the model sees it,
otherwise it is classified from its name and metadata. When OCR finds enough
text, the text is sent instead of the image, even with vision on: it is
cheaper and went through the secret scan. Recognition takes about a quarter
of a second per image or page; the first one on a Mac can take half a minute
while macOS prepares its model. `flib check-config` says whether OCR works
here, and `flib inspect FILE` whether a file's text came from OCR.

### Images and scanned PDFs (vision)

Photos have no text, so by default they are classified from their name and
metadata only and keep their name. Turn vision on to let the model look at
them:

```yaml
vision:
  enabled: true      # off by default
  max_image_px: 1568 # longer side of the image sent, in pixels (256-2576)
  max_pdf_pages: 3   # first pages sent of a PDF with no text layer (1-20)
```

Images (`.jpg`, `.png`, `.heic`, `.webp`, `.gif`, `.tiff`, `.bmp`) are
converted to JPEG and shrunk by macOS's own `sips` (never enlarged), and
their metadata (EXIF, GPS position, camera details) is removed before sending.
A PDF whose pages have no text gets its first `max_pdf_pages` pages cut into a
new PDF, which is sent as a document. Because the model then sees the content,
these files can be renamed like any document (`IMG_4512.HEIC` becomes, say,
`2026-11-02_beach-at-sunset.HEIC`). If an image cannot be converted, or the
provider cannot read images, the file is classified from its metadata as with
vision off.

Which provider reads what: `anthropic` reads images and scanned PDFs;
`ollama` and `openai-compat` read images only, and only with
`provider.vision: true` (set it when the model is a vision model);
`claude-code` reads neither. Answers given with vision are cached apart from
answers without it, so turning it on or off never reuses the other kind.

On the default model a photo or screenshot costs about US$0.0005 and a
three-page scan about US$0.001, so roughly 2,000 photos fit in the default
US$1 cap; [the guide](docs/guide.md#what-vision-costs) shows the estimate.

## Use it from Claude

`flib mcp` is a local [MCP](https://modelcontextprotocol.io) server over
stdio, so Claude (or any MCP client) can answer "where is last month's power
bill?" from your library. It runs only while the client is open and reads the
same database as `flib search`. It uses no network itself, except that with
semantic search on and a remote `search.semantic.base_url`, a search by
meaning sends the query there (the `search_files` tool then says so).

| Tool | What it does |
| --- | --- |
| `search_files` | Search names, descriptions and tags, with the same category, extension, period, issuer, kind, amount, currency, due date and document period filters as `flib search`; `semantic: true` also searches by meaning ([Semantic search](#semantic-search-optional)); duplicates only with `include_duplicates` |
| `summarize_amounts` | Add up the amounts of the matching documents per currency, optionally by issuer, month, year, kind or category, like `flib search --sum` ("what did I pay to Power Co in 2026?") |
| `get_file` | Everything the index knows about one file: where it is and was, category, description, tags, kind, issuer, amount, dates and period, hash, size, download origin, whether it is only in iCloud, why it is in review (if it is), and its history (moves, renames, fixes, undos with when each was undone; newest first, last 20) |
| `list_recent` | The files organized most recently, newest first, optionally in one category |
| `list_review` | What waits in the review folder, newest first, with the AI's confidence and why each file is there |
| `get_stats` | Counts by status and by category, total size filed, the last run and its provider cost, and this month's total cost |
| `recategorize_file` | Move a file to another configured category and/or rename it, like `flib fix` |
| `undo` | Revert a run or the last move of one file, like `flib undo` |

A file in review says why it is there: `low_confidence` (the AI's confidence
was below `limits.confidence_threshold`), `unknown_category` (the AI chose a
category you have not configured), `category_removed` (`flib reprocess
--remove-category --to-review`) or `moved_by_hand` (`flib reconcile` found it
in the review folder). The first two come with the category the AI suggested.

Costs are estimates, recorded per run (`flib run`, `flib reprocess`; a `flib
fix` costs nothing). A run whose provider prices flib does not know (an
`openai-compat` server without `price_input_per_mtok`/`price_output_per_mtok`)
has no recorded cost rather than a cost of zero; `get_stats` and `flib status`
count those runs apart.

Results carry metadata, descriptions and the fields the AI read in each
document, never file content; sensitive files show no description, tags,
fields or origin, and their amounts are never added up. The write tools go through the same
journal as the CLI, so `flib undo` reverts what an assistant did, and they
refuse to run while a run is in progress. Set `mcp.read_only: true` to offer
only the read tools.

**1. Enable it.** The MCP server is optional, because its SDK adds 16
packages that the rest of flib does not need. Answer yes when
`./scripts/install.sh` asks, or run it again with the flag:

```sh
./scripts/install.sh --with-mcp
```

Later updates keep it; `--without-mcp` removes it. Without it, `flib mcp`
says how to enable it.

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

The index, the logs and the `flib doctor` report stay on your Mac. What
leaves it depends on the provider and on two optional features.

**For each file classified**, the provider receives:

- its name, extension, size, download date and the URL it was downloaded
  from;
- at most `provider.max_chars_per_file` characters of its text (4,000 by
  default), or for a folder, the names of up to 100 files inside it. For an
  e-mail that text starts with its subject, date, the senders' and
  recipients' names and the domains of their addresses (never the part
  before the `@`), and its attachments' names; the body follows as it is,
  like any document's text;
- for an image or a PDF with no text layer, the text
  [OCR](#text-in-images-and-scanned-pdfs-ocr) recognised in it, like any
  document's text. The recognition runs on your Mac; its text is scanned for
  secrets before anything is sent;
- only with `vision.enabled` (off by default) and when OCR found little or no
  text: for an image, the image itself, shrunk to `vision.max_image_px` and
  stripped of its metadata; for a PDF with no text layer, its first
  `vision.max_pdf_pages` pages. Secret detection reads text, not pixels, so
  what OCR could not read goes as it is. Keep such files out with
  `never_send`;
- your most recent corrections (at most `provider.max_examples`): for each,
  the corrected file's extension, the description the provider wrote for it
  earlier, and the category or name before and after. Never file content,
  and never from files matching `never_send` or flagged sensitive.

Two kinds of files are treated differently, with every provider:

- **Matching `never_send`:** only the metadata above is sent, never the
  content. Add `"*.eml"` to keep e-mail messages to their metadata.
- **Credential names (`id_rsa`, `*.pem`…) or secrets found in the content:**
  nothing at all is sent, the file stays where it is, and the index records
  only that it is sensitive, and why. Names that say they hold passwords or
  recovery material count too, matched as whole words in any case:
  `password(s)`, `passwd`, `2fa`, `senha(s)`, `contraseña(s)`,
  `mot(s) de passe`, `Passwort`/`Passwörter`, `recovery code(s)`,
  `backup code(s)`, `seed phrase`, `private key`, `recovery key`,
  `bitwarden export`, `logins.csv`. `1Password-8.10.dmg` or a hexadecimal
  name with `2fa` inside is not caught, and installers (`.dmg`, `.pkg`,
  `.app`…) never are by name alone. A harmless "password reset guide.pdf" is
  still left in place: rename it, or list it in `sensitive.allow_names`, and
  the next run looks at it like any file (a file whose content holds a
  secret stays sensitive whatever its name). `flib status --sensitive` lists
  every file left in place and why, in your terminal only (never in `flib
  doctor`, a notification or the MCP server).

**Where it goes, by provider:**

| Provider | Receives the above | Images (vision on) | Scanned PDF pages (vision on) |
| --- | --- | --- | --- |
| `anthropic` | Anthropic's API | yes | yes |
| `claude-code` | Anthropic, through the `claude` CLI | never | never |
| `openai-compat` | the server at `provider.base_url` | only with `provider.vision: true` | never |
| `ollama` | the model on this Mac (nothing leaves it), unless `base_url` points elsewhere | only with `provider.vision: true` | never |

Text recognition (OCR) is not in this table because it never leaves your
Mac: macOS reads the text locally, and only that text goes on, as above.

**Optional features:**

- [Semantic search](#semantic-search-optional) (off by default) sends the
  index's names, categories, tags and descriptions (never content, never a
  sensitive or `never_send` file) and your semantic queries to
  `search.semantic.base_url`: Ollama on this Mac by default, so nothing
  leaves it unless you point it elsewhere.
- The [MCP server](#use-it-from-claude) gives an assistant only what `flib
  search` would show you, never file content. What the assistant does with
  it is up to the assistant: a cloud assistant such as Claude sends it to its
  provider like anything else in the conversation.

`flib status` and the periodic notification name no file, because
notifications show on the lock screen.

**What the index stores.** For each file: its original and current name and
path, size, content hash, category, status, and what the AI wrote about it (a
description of 1 to 3 sentences, a few tags, its confidence and why it kept
or replaced the name), plus the journal of moves and corrections behind
`flib undo`. Never the file's content. That description is what `flib search`,
`flib inspect` and the MCP tools show, and what semantic search embeds, so it
is kept free of personal identifiers in two ways:

- The AI is asked to say what a file *is* without copying identifiers from
  it: no phone numbers, e-mail addresses, document, ID or tax numbers, bank
  account or card numbers, payment keys, street addresses, licence plates or
  full names of private individuals (names of organisations, products and
  public figures are fine). The proposed file name follows the same rule.
- Before anything is stored, flib replaces what still looks like one with a
  marker: `(00) 90000-0000` becomes `[phone]`, `user@example.com` becomes
  `[email]`, and CPF, CNPJ, card, IBAN and other long numbers become
  `[id number]`, `[card number]` or `[account number]` (secrets become
  `[secret]`). Dates, amounts, version numbers and years are left alone. In a
  proposed name the identifier is dropped. Names of people and street
  addresses cannot be told apart from other words by a pattern: for those,
  only the instruction to the AI applies.

`privacy.descriptions: verbatim` turns the second step off and stores what
the AI wrote (the instruction stays). Descriptions stored by an earlier
version can be cleaned without asking the AI again, at no cost:
`flib reprocess --scrub-descriptions` (`--simulate` to see which files first).
It also cleans the classification cache, removes the embeddings of the files
it changed (the next run makes them again) and is undone by `flib undo`; until
you undo it, its undo journal in the index keeps the old text, shown nowhere.
`flib doctor` never includes descriptions or tags.

## Troubleshooting

When something does not work, `flib check-config` validates the
configuration and `flib schedule --status` shows the daily run. The most
common questions have their own page,
[docs/troubleshooting.md](docs/troubleshooting.md):

- [a file is skipped as "unreadable"](docs/troubleshooting.md#a-file-is-skipped-as-unreadable)
  (usually Full Disk Access for the daily run);
- [a file is skipped as "locked"](docs/troubleshooting.md#a-file-is-skipped-as-locked)
  (files some apps protect while the Mac is locked);
- [no notification appears](docs/troubleshooting.md#no-notification-appears).

To report a bug, run:

```sh
flib doctor
```

It writes one file into flib's logs folder and prints its path; `open -R` on
that path shows it in Finder, ready to drag into an issue. The report holds
versions, the configuration, the schedule, database counts, what `flib
status` shows, the last runs and their logs, with the API key left out
entirely and file names, folders, e-mail addresses, URLs and anything that
looks like a secret replaced. Read it before you attach it: redaction is
careful, not infallible. `--stdout` prints it instead, `--format markdown`
makes it paste-ready, and [the troubleshooting page](docs/troubleshooting.md#the-doctor-report)
lists exactly what is redacted.

## Measuring quality

`flib eval` runs a synthetic corpus of about 150 downloads, in English and
Portuguese, through the real pipeline in a temporary folder and reports
category accuracy, the review rate, how often descriptive names are kept,
speed and cost. It never reads your configuration or touches your library;
with a paid provider it shows the estimated cost (a few US cents with the
default model) and asks first. `--compare` puts two runs side by side, for
two providers or two prompt versions. [docs/quality.md](docs/quality.md) has
the options, what each number means and the current baselines.

## Roadmap

Everything planned for M0–M3 and the P2 backlog is implemented; the
[requirements](docs/requirements.md) have each one's status. Next: v0.1.0
after the daily-use validation, then packaging for v1.0 (a release on PyPI).
The [CHANGELOG](CHANGELOG.md) lists what is in the upcoming release.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md),
[docs/architecture.md](docs/architecture.md) and the
[requirements](docs/requirements.md). Adding a file format or an AI provider
has its own guide: [docs/adding-an-extractor.md](docs/adding-an-extractor.md)
and [docs/adding-a-provider.md](docs/adding-a-provider.md). Questions go to
[Discussions](https://github.com/luizpereiradev/file-librarian/discussions).
Report vulnerabilities privately, as [SECURITY.md](SECURITY.md) describes.

## License

[MIT](LICENSE)
