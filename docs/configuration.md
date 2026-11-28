# Configuration

Everything you can tune lives in one YAML file,
`~/.config/file-librarian/config.yaml` (or the path in `$FLIB_CONFIG`, or
`--config PATH`). `flib init` writes a short one; the commented
[`config.example.yaml`](#every-option) at the end of this page lists every
option with its default. Only `provider.model` is required; everything else
has a default. `flib check-config` validates the file, and an invalid one stops
a run before any file is touched. API keys never go in this file. The
interface's [Settings page](daily-use.md#the-interface) changes the essentials
for you, with the same checks.

## The options you are most likely to change

| Option | Default | What it does |
| --- | --- | --- |
| `language` | `en` | `en` or `pt-BR`: the language of generated names and descriptions, of the default folders (`Organized`/`_Review` or `Organizado`/`_Revisar`) and categories, and of the web interface (`flib ui`) and the menu bar item; the command line stays English |
| `inputs` | `[~/Downloads]` | The folders to keep organized; each can have its own rules ([More than one inbox](#more-than-one-inbox)) |
| `output_root` | `~/Downloads/Organized` | Where category folders are created |
| `categories` | 5 in `en`, 15 in `pt-BR` | The closed list the AI chooses from; each `hint` steers it, `rename: false` keeps original names, `layout` files into sub-folders ([Sub-folders](#sub-folders-by-year-or-issuer)) |
| `never_send` | `*.pem`, `*.key`, `.env*` in the example | Patterns classified from metadata only ([Sensitive files](privacy.md#sensitive-files-and-never_send)) |
| `limits.confidence_threshold` | `0.7` | Below this, a file goes to review |
| `limits.max_cost_usd_per_run`, `max_cost_usd_per_day`, `max_files_per_run`, `max_files_per_day` | see [Costs and caps](providers.md#costs-and-caps) | What a run, and a day of automatic runs, may spend and send |
| `naming.pattern` | `{date}_{subject}` | New names; see [Names](#names) |
| `duplicates` | `move` | `leave` keeps copies where they are ([Duplicates](#duplicates)) |
| `finder_tags.enabled` | `true` | Tag each filed item with its category in Finder ([Finder tags](#finder-tags)) |
| `ocr.enabled` | `true` | Read the text in images and scanned PDFs on your Mac ([OCR](#text-in-images-and-scanned-pdfs-ocr)) |
| `vision.enabled` | `false` | Let the model look at images and scanned PDFs ([Vision](#images-and-scanned-pdfs-vision)) |
| `schedule.time` | `12:30` | When the daily run starts (applied by `flib schedule`) |
| `schedule.mode` | `daily` | `watch` runs as files arrive, `both` does both ([Watch mode](daily-use.md#or-as-files-arrive-watch-mode)) |
| `summary.notify` | `true` | The periodic notification about files waiting for you ([What waits for you](daily-use.md#what-waits-for-you)) |
| `search.semantic.enabled` | `false` | [Search by meaning](daily-use.md#search-by-meaning-optional) |
| `mcp.read_only` | `false` | Offer only the read tools in `flib mcp` ([MCP](mcp.md)) |
| `privacy.descriptions` | `redact` | `verbatim` stores descriptions and tags exactly as the AI wrote them ([What the index stores](privacy.md#what-the-index-stores)) |

## Finder tags

Each item moved into a category folder also gets the category name as a
Finder tag, so Finder's sidebar and Spotlight (`tag:Finance`) find it too.
Your own tags are kept, and `flib undo` removes only the tag flib added.

## More than one inbox

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
counted, are in [the guide](guide.md#more-than-one-inbox).

## Names

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
(`{date}_{subject}` by default); [the guide](guide.md#names) has the
rules.

## Sub-folders by year or issuer

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
[the guide](guide.md#sub-folders-inside-a-category).

## Duplicates

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
renames the folder. [The guide](guide.md#duplicates) covers the edge
cases, including what the first run does with copies earlier versions left
in Downloads.

## Text in images and scanned PDFs (OCR)

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
otherwise it is classified from its name and metadata. What happens to an
image with enough text depends on whether it is a document:

- a scanned PDF, or an image that is clearly a document (at least 10 lines
  and 300 letters and digits, covering at least a fifth of the picture: a
  scanned or photographed page), sends its text only, even with vision on:
  it is cheaper and went through the secret scan;
- any other image with words in it (a flyer, a poster, a meme, a chat
  screenshot, a photo with a caption, a till receipt on a table) sends the
  image *and* its text when vision is on: the image tells the model what the
  file is, the text helps it read small print. With vision off, or a
  provider that reads no images, the text goes alone, but the model is told
  it was found in a picture, with the picture's size, how many lines and how
  much of it is text, so a flyer is judged as a flyer and not as a broken
  document.

Recognition takes about a quarter
of a second per image or page; the first one on a Mac can take half a minute
while macOS prepares its model. `flib check-config` says whether OCR works
here, and `flib inspect FILE` whether a file's text came from OCR.

## Images and scanned PDFs (vision)

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
`2026-11-02_beach-at-sunset.HEIC`). An image with words in it goes with the
text [OCR](#text-in-images-and-scanned-pdfs-ocr) found in it, unless it is
clearly a document, which goes as text only. If an image cannot be
converted, or the provider cannot read images, the file is classified from
its recognised text, if any, or its metadata, as with vision off.

Which provider reads what: `anthropic` reads images and scanned PDFs;
`ollama` and `openai-compat` read images only, and only with
`provider.vision: true` (set it when the model is a vision model);
`claude-code` reads neither. Answers given with vision are cached apart from
answers without it, so turning it on or off never reuses the other kind.

On the default model a photo or screenshot costs about US$0.0005 and a
three-page scan about US$0.001; see [Costs and caps](providers.md#costs-and-caps).

## Every option

The commented example, as it is in the repository: every option, with its
default and what it does.

```yaml
--8<-- "config.example.yaml"
```
