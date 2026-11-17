# file-librarian

Keeps your Downloads folder organized. Once a day, `flib` reads each new file,
gives it a descriptive name, moves it into a category folder and writes a
searchable description to a local SQLite index.

`documento(3).pdf` becomes `Finance/2026-10-05_electricity-invoice.pdf`, and
`flib search "electricity october"` finds it months later.

**The AI only decides. Deterministic code moves files.** The organizing
commands keep these guarantees:

- nothing is deleted and nothing is overwritten;
- every run is recorded and can be undone, except files you changed or moved
  afterwards, which are skipped and listed.
- nothing is downloaded from iCloud: files that exist only in the cloud, and
  folders with any such file inside, are skipped and listed until you
  download them. `flib reconcile` and `flib inspect` do not read them either.

> **Status: pre-alpha (M1 complete, under acceptance).** Scanning,
> extraction, secret detection, classification with Claude, planning, moving,
> search and undo all work. Try it offline first with
> `provider: {name: fake, model: offline}`, which files everything under the
> last category without calling any API. Scheduling (`flib schedule`) comes in
> M2. See the [roadmap](#roadmap).

## Requirements

- The current major release of macOS (the only supported platform)
- [uv](https://docs.astral.sh/uv/) 0.13 or newer (`brew install uv`); it
  installs Python 3.11+ for you
- An Anthropic API key for the default provider (from M1)

## Install

```sh
git clone https://github.com/luizpereiradev/file-librarian.git && cd file-librarian
./scripts/install.sh                      # installs `flib` with the locked dependency versions
mkdir -p ~/.config/file-librarian
cp config.example.yaml ~/.config/file-librarian/config.yaml
flib key set                              # stores your Anthropic API key in the macOS Keychain
flib check-config                         # validates the configuration and finds the key
flib simulate                             # shows what would happen; nothing moves
```

Then `flib run` organizes for real, and `flib undo` reverts the last run.

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
database (`~/Library/Application Support/file-librarian/logs`).

To try it without an API key, set `provider: {name: fake, model: offline}`:
everything is filed under the last category, offline.

### Where the API key comes from

`provider.api_key_source: auto` (the default) uses the first it finds:

1. `ANTHROPIC_API_KEY` in the environment;
2. the macOS Keychain (`flib key set`; `flib key status` shows which is used);
3. the `.env` file named by `provider.env_file`, if you set one.

Scheduled runs don't see your shell's environment, so the Keychain is the
easiest choice for them. Set `api_key_source` to `environment`, `keychain` or
`env_file` to use only one place.

## Commands

| Command | What it does | Milestone |
| --- | --- | --- |
| `flib check-config` | Validate the configuration without touching files | M0 |
| `flib inspect FILE...` | Show hash, size and download origin (not the hash of a file only in iCloud) | M0 |
| `flib simulate` | Print the full plan without changing anything | M1 (available) |
| `flib run` | Classify, rename, move and index new files | M1 (available) |
| `flib search QUERY [--json]` | Full-text search over names, descriptions and tags | M1 (available) |
| `flib undo [--run ID \| --file PATH]` | Revert a run or one file | M1 (available) |
| `flib schedule [--at HH:MM \| --status \| --remove]` | Run every day via launchd, with an access check | M2 (available) |
| `flib reconcile` | Update the index after you move, rename or delete files by hand; files only in iCloud are left unchecked | M2 (available) |
| `flib fix PATH [--category NAME] [--name NAME]` | Correct one file's category or name; flib learns from it | M2 (available) |
| `flib init` | Guided first run on a sample | M2 |
| `flib mcp` | Local MCP server for AI assistants | M2 |
| `flib reprocess` | Reclassify after a rules change | M3 |
| `flib doctor` | Redacted logs for bug reports | M3 |

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
vision off. Answers given with vision are cached apart from answers without
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

## Privacy

With a remote provider such as `anthropic`, this is what leaves your Mac
for each file:

- its name, extension, size, download date and the URL it was downloaded
  from;
- at most `provider.max_chars_per_file` characters of its text (4,000 by
  default), or for a folder, the names of up to 100 files inside it;
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
  content.
- **Credential names (`id_rsa`, `*.pem`…) or secrets found in the content:**
  nothing at all is sent, the file stays where it is, and the index records
  only that it is sensitive.

The index and the logs stay on your Mac. A local provider (Ollama) is planned
for M3, for anyone who wants nothing to leave the machine.

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
[requirements](docs/requirements.md). Adding an AI provider or a file format
has its own guide in [docs/](docs/). Questions go to
[Discussions](https://github.com/luizpereiradev/file-librarian/discussions).

## License

[MIT](LICENSE)
