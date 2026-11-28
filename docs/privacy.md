# Privacy and safety

flib reads your personal files every day, unattended. This page says exactly
what it reads, what leaves your Mac and to whom, what never does, and how it
keeps your files safe. The [architecture](architecture.md) has the full
design.

## What it promises

**The AI only decides. Deterministic code moves files.** The model returns
data (a category, a name, a description); it never gets a path it can act on.
A category outside your list, a malformed answer or a low confidence never
becomes an action: the file goes to the review folder. The organizing commands
keep these guarantees:

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
  send only metadata.

## What leaves your Mac

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
  [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) recognised in it, like any
  document's text. The recognition runs on your Mac; its text is scanned for
  secrets before anything is sent;
- for an image whose text was recognised, its size in pixels, how many lines
  of text it has and how much of it they cover;
- only with `vision.enabled` (off by default): for an image, the image
  itself, shrunk to `vision.max_image_px` and stripped of its metadata,
  unless OCR found it to be a page of text (then the text alone goes); for a
  PDF with no text layer, its first `vision.max_pdf_pages` pages, when OCR
  found little or no text. Secret detection reads text, not pixels, so what
  OCR could not read goes as it is. Keep such files out with `never_send`;
- your most recent corrections (at most `provider.max_examples`): for each,
  the corrected file's extension, the description the provider wrote for it
  earlier, and the category or name before and after. Never file content,
  and never from files matching `never_send` or flagged sensitive.

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

- [Semantic search](daily-use.md#search-by-meaning-optional) (off by default) sends the
  index's names, categories, tags and descriptions (never content, never a
  sensitive or `never_send` file) and your semantic queries to
  `search.semantic.base_url`: Ollama on this Mac by default, so nothing
  leaves it unless you point it elsewhere.
- The [MCP server](mcp.md) gives an assistant only what `flib
  search` would show you, never file content. What the assistant does with
  it is up to the assistant: a cloud assistant such as Claude sends it to its
  provider like anything else in the conversation.

`flib check-config` and `flib doctor` say `fully local: yes`, or list what
leaves the Mac and where to (the classification request with `anthropic`,
`claude-code` or a remote server; the index text with remote embeddings).

## Sensitive files and `never_send`

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
  `bitwarden export`, `logins.csv`, `client secret(s)`, `api key(s)`,
  `access token`, `refresh token`, `secret key`. OAuth client files as the
  cloud consoles name them (`client_secret_<id>.json`), `token.json`,
  `secrets.json`/`.yaml`, `kubeconfig`, `*.tfstate` and `*.tfvars` count as
  credential names, and a secret under a quoted key (`"client_secret":
  "…"`) is found anywhere, also in a file that is one long line of JSON.
  `1Password-8.10.dmg` or a hexadecimal
  name with `2fa` inside is not caught, and installers (`.dmg`, `.pkg`,
  `.app`…) never are by name alone. A harmless "password reset guide.pdf" is
  still left in place: rename it, or list it in `sensitive.allow_names`, and
  the next run looks at it like any file (a file whose content holds a
  secret stays sensitive whatever its name). `flib status --sensitive` lists
  every file left in place and why, in your terminal only (never in `flib
  doctor`, a notification or the MCP server).

A screenshot of a password or an API key counts too: the text
[OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) recognises in an
image goes through the same secret scan before anything is sent. The
interface never lists or acts on a sensitive file, and search counts them
without naming them.

## What flib reads

| Format | What is read |
| --- | --- |
| PDF | the text layer (first 20 pages); for a scanned PDF, the text [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) recognises in its first 3 pages, on your Mac |
| Word, Excel, PowerPoint (`.docx`, `.xlsx`, `.pptx` and their macro versions) | the text; for a workbook, the sheet names and cell text |
| Plain text, Markdown, CSV, JSON, YAML, HTML, subtitles and source code | the text |
| E-mail (`.eml`) | subject, date, sender and recipients (names and domains only), attachment names, and the body (plain text, else the HTML's text) |
| Archives (`.zip`, `.tar.gz` and other tarballs) | the list of names inside; nothing is unpacked |
| Folders and `.app` bundles | the names of up to 100 files inside; an app's name and version |
| Images | the text [OCR](configuration.md#text-in-images-and-scanned-pdfs-ocr) recognises in them, on your Mac; a photo with little or no text, nothing unless [vision](configuration.md#images-and-scanned-pdfs-vision) is on |
| Anything else (`.doc`, videos, installers...) | nothing: classified from its name and metadata |

At most `provider.max_chars_per_file` characters (4,000 by default) of the
text are sent. A damaged or password-protected file is classified from its
name and metadata too. To add a format, see
[adding an extractor](adding-an-extractor.md).

## What the index stores

For each file: its original and current name and path, size, content hash,
category, status, and what the AI wrote about it (a description of 1 to 3
sentences, a few tags, its confidence and why it kept or replaced the name),
plus the journal of moves and corrections behind `flib undo`. Never the
file's content. That description is what `flib search`, `flib inspect` and
the MCP tools show, and what semantic search embeds, so it is kept free of
personal identifiers in two ways:

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

`flib status`, the menu bar item and the periodic notification name no file,
because the menu bar is visible to anyone looking at your screen and
notifications show on the lock screen.

## Nothing leaves this Mac

```sh
flib init --local
```

sets flib up so that no file, name or search ever leaves your Mac:

| What | Where it runs |
| --- | --- |
| Classifying files | `gemma4:e4b` in [Ollama](https://ollama.com), on this Mac |
| Text in images and scanned PDFs | macOS's own text recognition (on by default) |
| Semantic search | `embeddinggemma` in Ollama, on this Mac |
| The index, logs, undo history | flib's folder, as always |

`flib init --local` checks that Ollama is installed and running and tells you
how to get it if not (`brew install ollama` and `brew services start
ollama`, or the app from [ollama.com](https://ollama.com/download)); flib
never installs it for you. It then offers to download the two models (about
10 GB; it shows the sizes and asks first), writes the configuration, runs the
usual trial on copies of your recent files, and says what quality to expect.
Plain `flib init` offers the same setup as the provider `local`.

**What quality to expect.** Measured on flib's synthetic test corpus
([quality](quality.md#local-models-92)), `gemma4:e4b` files 92%
of items in a right category in English and 97% in Portuguese, against 99%
and 100% for the hosted default (Claude Haiku), and sends 7 of the 9 items
that need a person to review, against 9 of 9. It keeps descriptive names as
often (and more generic ones too), reads amounts, currencies and due dates as well, and gets
issuers and document dates wrong more often. It does about 20 files a minute
on an M3 Pro, and costs nothing. Expect to correct more at first: `flib
simulate` before the first run, and a look at the review folder.

**Hardware.** An Apple silicon Mac with 16 GB of memory or more, and about
10 GB of disk for the models. The model needs about 9.5 GB once loaded, and
Ollama's figure grows to about 17 GB over a long run before it is unloaded
(10 minutes after the last file); on 16 GB macOS will swap during a large
first run. Of the four models measured, the smaller `qwen3.5:4b` (4 GB) is
less accurate in English (85%) and rarely sends anything to review; the
larger ones are not more accurate enough to pay for being 2.5 to 3 times
slower.

Vision stays off in this setup: on the corpus's images with text it gained
one item in twelve at half the speed. Text recognition reads them instead.

Being local relaxes nothing else: files matching `never_send` still send only
their metadata, and files with credential names or secrets in them are still
never sent at all, even though the model is on your Mac
([Ollama](providers.md#a-local-model-with-ollama-ollama)).

## How your files are kept safe

In plain words, what happens when flib moves a file:

1. **It writes down what it is about to do first.** Every move is recorded in
   the index's journal as *planned* before it happens, and marked *done*
   after. If the Mac crashes or loses power mid-run, the next run finds the
   unfinished entries and either completes them or leaves the file where it
   was; nothing is lost in between.
2. **It never replaces anything.** A destination name that is taken gets the
   next free number (`report (2).pdf`); the rename itself is atomic and
   refuses to replace an existing file. Folders it creates refuse symbolic
   links on the way, so a file can never be led outside your organized folder.
3. **It checks before it undoes.** `flib undo` walks a run's moves backwards,
   checks each file's hash, and skips (and lists) any file you edited since,
   or whose old place is taken now.
4. **Across volumes it copies, verifies, then removes.** On another disk the
   copy is flushed, its SHA-256 compared with the original's, and only then
   is the original removed; if anything differs, the original stays.
5. **What it cannot read, it leaves.** iCloud-only files, files the Mac
   protects while locked, unreadable folders: skipped and listed, never forced.

The details, including every crash case, are in the architecture's
[safety guarantees](architecture.md#safety-guarantees).

## The interface (`flib ui`)

`flib ui` is a web server on your Mac that only you should be able to use, for
as long as you use it. It is built to hold against a web page open in any
browser on the Mac, another machine on the network, other accounts on the same
Mac, other programs listening on loopback, and anything that reads logs, shell
history or the address bar. It cannot hold against a program running as you,
which can already do anything you can. What it does, in plain words:

- **Only this Mac can reach it.** It listens on the loopback address only;
  nothing else on the network can connect. It ends after 10 minutes without
  use (`ui.idle_minutes`), with **End session**, or with Ctrl-C.
- **Each session has an address of its own,**
  `http://flib-<random>.localhost:<port>/`, so other programs listening on
  the Mac never get its cookie, even if a page makes your browser visit them
  (macOS, Safari, Chrome and Firefox resolve `.localhost` names to the Mac
  itself; on a Mac that does not, `flib ui` says so and serves at
  `127.0.0.1`, where they could). Treat that address like the session itself
  while it runs: it is in the address bar and the history, never in a command
  line, and in the terminal only in the `--no-browser` link.
- **The key to the session goes through a file only you can read,** never
  through the command line, where other accounts on the Mac could see it. The
  terminal says "session started" when it is used. If the key is used a second
  time, by anything without your session, `flib ui` ends and warns you.
- **Your searches stay out of your history.** Search and its CSV export go in
  the form, never in the address; Open and Show in Finder send the file's
  number in the index, never a path.
- **Open is for documents only** (PDFs, images, text, modern office
  documents, media): anything that could run a program or go somewhere, such
  as an app, a script, an installer, a web location, a legacy
  `.doc`/`.xls`/`.ppt` or a file set to open with an app of its own, gets only
  Show in Finder.
- **Nothing is loaded from the internet.** It uses only Python's standard
  library and files shipped with flib, and the pages work without JavaScript.
  Every change goes through the same code, lock and journal as the command
  line, so `flib undo` reverts it too.

`--no-browser` prints the key itself, in a link: it stays in the terminal's
scrollback and, once opened, in the browser's history, and anyone who can see
your screen or that scrollback could use it before you do. It works only
once, and a second use ends the server, but prefer the default unless the
browser cannot be opened for you. [Architecture](architecture.md#the-interface-flib-ui)
has the details, with the threat model as a table of each threat, its defence
and the test that proves it: origin and CSRF checks, the content security
policy, request limits, what each page reads.

## Bug reports without your data

`flib doctor` writes a report for an issue with the API key left out entirely
and file names, folders, e-mail addresses, URLs and anything that looks like a
secret replaced. Read it before you attach it: redaction is careful, not
infallible. [Troubleshooting](troubleshooting.md#the-doctor-report) lists
exactly what is redacted.
