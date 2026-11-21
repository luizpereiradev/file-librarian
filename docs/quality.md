# Measuring quality

`flib eval` runs a built-in, synthetic corpus of 160 downloads through the real
pipeline and scores what flib decides against gold labels. Use it to judge a
prompt change, a new model or a new provider by numbers instead of by eye.

It never touches your files or settings. The corpus is written to a new
temporary folder, the configuration is built from the command's options only
(your config file is never read), and the database is a throwaway one in that
folder, so your library, index and classification cache are never opened. The
pipeline runs in simulate mode: it classifies and plans, and moves nothing.
The folder is deleted at the end.

## Running it

```sh
uv run flib eval                                   # anthropic, its default model, English categories
uv run flib eval --language pt-BR --out haiku-pt.json
uv run flib eval --provider ollama --model qwen3:4b
uv run flib eval --provider claude-code --limit 30  # a fixed sample of 30 items
uv run flib eval --provider fake                    # offline, free: checks the harness only
uv run flib eval --case image-text --vision         # only the six images with text, shown to the model
```

| Option | What it does |
| --- | --- |
| `--provider NAME` | `anthropic` (default), `claude-code`, `ollama`, `openai-compat` or `fake` |
| `--model M` | The model; defaults to the provider's default (required for `ollama` and `openai-compat`) |
| `--base-url URL` | `openai-compat` (required) or `ollama`: the API root |
| `--language en\|pt-BR` | Which locale's default categories and naming language to use (default `en`) |
| `--limit N` | A fixed sample of N items (it always keeps some of the sensitive ones) |
| `--max-cost USD` | Stop sending once this much is spent (default 1.00); the rest is reported as deferred |
| `--vision` | Show images and scanned PDFs to the model (`vision.enabled`; off by default, as in flib) |
| `--no-ocr` | Leave out the text recognised in images (`ocr.enabled: false`) |
| `--case TAG` | Only the items with this hard-case tag (`image-text`, `generic-name`, `review`...); repeatable |
| `--json` / `--out FILE` | Print / save the full result: metrics, usage and every item's outcome |
| `--compare FILE` | Compare this run with a saved one; given twice, compare two saved runs without running |
| `--yes` | Do not ask before sending to a provider that is not local |

Before sending anything to a provider that is not local (`anthropic`,
`claude-code`, a remote `openai-compat`), it prints the estimated cost and
asks. The API key is found as for `flib run` (environment, Keychain, or the
SDK's own login); nothing about the key is written to the result.

### Comparing two prompts or two providers

```sh
uv run flib eval --out before.json
# change the prompt (and bump PROMPT_VERSION in classifier.py)
uv run flib eval --compare before.json --out after.json
uv run flib eval --compare before.json --compare after.json   # again later, without running
```

The comparison shows each number side by side with the difference, every
category whose accuracy moved, and the items whose verdict changed
(`fixed` or `broke`). It warns when the two runs scored different corpus
versions, languages or samples. Saved baselines live in
[`docs/quality/`](quality/), so `--compare docs/quality/anthropic-claude-haiku-5-5-en.json`
works from a fresh clone.

## What it reports

- **Category accuracy**, overall and per category: the share of items whose
  category (the folder the model chose, or the one it suggested for an item
  it sent to review) is one of the acceptable ones. An item that was not
  classified counts as wrong. Per category, items are grouped under their
  first acceptable category.
- **Most common mistakes**: (expected, chosen) pairs.
- **Review**: how many items went to the review folder, how many the gold
  expects there, and how many of those did.
- **Name kept**: over filed items whose gold says whether the original name
  should survive, precision and recall of "kept". A name counts as kept when
  the file was not renamed or the model chose its original subject.
- **Sensitive**: items flib must withhold, how many it withheld and how many
  reached the provider. That last number must be 0: if it is not, `flib eval`
  exits with an error (after saving `--out`, to look into).
- **Fields** (#88), over the items whose gold lists structured fields
  (bills, statements, receipts, contracts, tickets...: 56 items), for each of
  kind, issuer, amount, currency, document date, due date and period:
  *precision*, the share of the model's answers that are right where the
  gold has a value; *recall*, the share of gold values it gave right (an item
  not classified counts as a miss); *invented*, how many values it gave
  where the gold has none. Kind, currency, dates and period must be equal
  (the gold's kinds are free text, so `GOLD_KINDS` in `evals/score.py` lists
  which of flib's kinds count for each: a "utility bill" is a `bill`, a
  "lease" a `contract`), amounts equal in cents, issuers equal ignoring
  case, accents, punctuation and legal forms (`naming.issuer_key`).
- **Images with text** (#100), over the six items tagged `image-text`: how
  many got the right category, how many went to review, and what the
  provider got for each (`image+text`, `image`, `text`, `metadata`).
- Items per minute, tokens and cost. Items per minute counts the provider's
  calls over wall time, so for a remote provider it mostly reflects
  `provider.requests_per_minute` (30 by default), not the model's speed.

## The corpus

[`src/file_librarian/evals/corpus.py`](../src/file_librarian/evals/corpus.py)
writes it; nothing is committed as a binary fixture, and nothing comes from a
real document. Organisations are fictitious, there are no people's names and
no real document numbers. The same seed writes the same bytes every time.

- **Formats**: PDF with text (71), Word, Excel, PowerPoint, e-mail (`.eml`),
  text, Markdown, CSV, JSON, YAML, SQL, Python, a notebook; images, video,
  audio and installers that flib reads by name only; scans with no text layer;
  ZIP archives; four folder units and an `.app` bundle.
- **Images with text** (#100, tag `image-text`): six PNGs drawn by code
  ([`evals/pictures.py`](../src/file_librarian/evals/pictures.py), with a 5x7
  bitmap font of its own in [`evals/raster.py`](../src/file_librarian/evals/raster.py):
  no dependency, no platform font, the same bytes everywhere): four pictures
  that happen to contain words (a flyer, a group-chat screenshot, a meme, a
  landscape photo with a small caption; tag `picture`) and two documents that
  happen to be images (a photographed till receipt, a scanned A4 page; tag
  `document-image`). The text macOS recognised in each is recorded in
  [`evals/ocr.json`](../src/file_librarian/evals/ocr.json) with its line boxes
  summary and the image's size; `flib eval` replays it instead of running the
  helper, so a run scores the same on every Mac, in CI and anywhere without
  macOS's text recognition. Every other file has no text, as a photo would.
  After changing a picture or `ocr.js`, record it again on a Mac with
  `uv run python -m file_librarian.evals.corpus --record-ocr`; a test fails
  until you do, and the opt-in OCR test (`FLIB_TEST_OCR=1 uv run pytest -m
  ocr`) checks that this Mac still reads them as recorded.
- **Languages**: 71 items in Portuguese, 35 in English, and 54 with no text
  (images, installers, media, binary files, credential files).
- **Categories**: every default category of both locales has items
  (`en`: 5, `pt-BR`: 15).
- **Hard cases**: generic names (`bill.pdf`, `documento(1).pdf`, `scan001.pdf`)
  next to descriptive ones; three bills from the same issuer in three months,
  in each language; ambiguous documents (a Pix receipt for rent, a car rental
  that says "aluguel", a medical receipt for tax deduction); nine items with too
  little to go on, which should go to review; ten credential files (by name,
  by a secret inside, and a folder with a `.env`) that must never be sent.

The gold labels are in [`src/file_librarian/evals/gold.json`](../src/file_librarian/evals/gold.json),
generated from the code (a test keeps the two equal; after changing an item,
run `uv run python -m file_librarian.evals.corpus`, and bump `CORPUS_VERSION`).
For each item: the acceptable categories per language, whether the original
name should be kept (`null`: not scored, flib never renames it), whether it
should go to review, whether it is sensitive, its text's language, hard-case
tags, and, for bills, statements, receipts and other documents, the
structured fields it states (`amount`, `currency`, `document_date`,
`due_date`, `issuer`, `period`, `kind`), which `flib eval` scores.

The gold fields were written for the amounts and dates that matter, not as
a full transcription: some documents state an issuer, a date or an amount
the gold leaves out (a diploma's university, a hotel booking's total, a
lease's monthly rent). An answer there counts as *invented*, so that number
is an upper bound: read the items it lists before calling it a mistake.

The gold labels are deliberately lenient about categories: where two are
defensible (a condo fee is both `Financeiro` and `Casa e veículos`), both
count. Category accuracy therefore sits near the ceiling for a good model;
review and name keeping are where models and prompts differ.

## Baselines

Default seed, all items, the 10 sensitive ones withheld. Prompt version 5
was measured on 2026-11-20, versions 7 and 8 on 2026-11-21. Prompt version 8 (#97, #100) ran on corpus
version 2 (160 items, 150 sent); the earlier rows ran on corpus version 1
(154 items, 144 sent). So that the rows compare, the main columns are over
the 154 items both corpus versions share; the six images with text are
reported apart below.

| Provider / model | Language | Prompt | Category accuracy | Review (expected caught) | Name kept precision / recall | Sensitive sent | Items/min | Tokens in / out (whole run) | Cost (whole run) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `anthropic` / `claude-haiku-5-5` | `en` | 8 | 98.6% (142/144) | 12 sent, 9/9 expected | 97.4% / 95.0% | 0 | 29.7 | 433,056 / 34,911 | US$ 0.061 |
| `anthropic` / `claude-haiku-5-5` | `en` | 7 | 98.6% (142/144) | 14 sent, 8/9 expected | 98.6% / 89.7% | 0 | 28.1 | 374,483 / 33,415 | US$ 0.054 |
| `anthropic` / `claude-haiku-5-5` | `en` | 5 | 97.9% (141/144) | 12 sent, 8/9 expected | 98.6% / 87.5% | 0 | 29.6 | 220,403 / 27,673 | US$ 0.036 |
| `anthropic` / `claude-haiku-5-5` | `pt-BR` | 8 | 100.0% (144/144) | 12 sent, 9/9 expected | 96.2% / 98.7% | 0 | 29.5 | 550,806 / 36,687 | US$ 0.073 |
| `anthropic` / `claude-haiku-5-5` | `pt-BR` | 7 | 100.0% (144/144) | 11 sent, 9/9 expected | 100.0% / 88.5% | 0 | 29.6 | 486,227 / 36,843 | US$ 0.067 |
| `anthropic` / `claude-haiku-5-5` | `pt-BR` | 5 | 100.0% (144/144) | 13 sent, 9/9 expected | 100.0% / 75.0% | 0 | 29.9 | 332,147 / 32,141 | US$ 0.049 |
| `ollama` (a local model) | | | not measured | | | | | | |

The prompt version 8 runs are the ones saved in [`docs/quality/`](quality/)
(`anthropic-claude-haiku-5-5-en.json`, `anthropic-claude-haiku-5-5-pt-BR.json`).

### How much is noise

The same settings do not give the same numbers twice: items whose
confidence sits between 0.6 and 0.8 change sides from one run to the next.
Runs on identical settings sent 14, 12 and 14 items to review (prompt 7,
`en`); over every full run made for prompts 7 and 8 the review count ranged
from 11 to 14, except one draft of prompt 8 (16, below). A difference of two
or three reviews, or one category, is inside that spread and is not claimed
as a change either way. Only one full run per language was made of the final
prompt 8, so its own spread was not measured on the full corpus.

### Names (#97)

Prompt version 8 judges the current name on its own ("does it say what this
file is, more than one bare word for a kind of document?") instead of
against the model's proposal, says that a name in another language is never
translated and need not name the issuer or the month, and asks the proposed
subject not to pile the issuer and other fields onto a name.

- **Recall of "kept" went up clearly in both languages**: from 88.5% to
  98.7% in Portuguese (75.0% with prompt 5) and from 89.7% to 95.0% in
  English: 76 of 77 descriptive names kept against 69 of 78 in Portuguese,
  76 of 80 against 70 of 78 in English. Two runs of one draft on identical
  settings (the 40 hardest names, below) differed by one name, so seven more
  in Portuguese is beyond the noise; the English gain is smaller and, from
  one run, less certain. The English names translated with `pt-BR`
  (`order-confirmation.eml`, `warranty-registration.txt`, `config.yaml`,
  `analysis.ipynb`) and the names that got the issuer appended
  (`extrato_out_2026.pdf`, `manual-cafeteira.pdf`) are kept now.
- **Precision dropped a little**: 97.4% in English (two generic names kept,
  `contract.docx` and `user-guide.pdf`, against one with prompt 7), 96.2% in
  Portuguese (three: `e-ticket.pdf`, `pedido-2644639.eml`, `user-guide.pdf`;
  the gold calls them generic, though each names a kind of document). The
  trade-off is deliberate: a kept name that is a bit vague costs less than a
  good name lost.
- **Still replaced**: `atestado.pdf` in both languages (one bare word, by the
  new rule), and in English three Portuguese names are still translated
  (`recibo-irpf-2026.pdf`, `receita-lasanha.pdf`, `ideias.md`), against
  none of the English names in Portuguese.
- Where to draw the line between "a bare kind of document" and "a short
  descriptive name" is where the model wavers. Drafts measured on a 40-item
  subset of the hardest names (US$ 0.02 each) swung between keeping
  `receipt.eml` and `comprovante.pdf` (recall 100%, precision 78%) and
  replacing `atestado.pdf`, `Statement-Oct-2026.pdf` and `config.yaml`
  (precision 100%, recall 64-78%). A first full-run draft that put "keep the
  original" first kept every descriptive name in English (recall 100%) but
  also six generic ones (precision 91.5%), and sent 16 items to review with
  six categories wrong; the final wording is the middle ground.

### Images with text (#100)

With vision on, pictures that contain words used to be sent as their OCR
text only. Over the six images with text, `--case image-text`, three runs
in Portuguese and one in English per setting (the full runs above use
neither vision nor the images' attachment, and sent the six as text):

| Setting | Sent as | Went to review | Right category | Mean confidence (pt-BR / en) | Input tokens per run (pt-BR) |
| --- | --- | --- | --- | --- | --- |
| vision only (`--vision --no-ocr`): the baseline | image ×6 | 0, 0, 0 / 0 | 6/6 every run | 0.913 / 0.932 | 30,702 |
| before #100 (vision on, OCR text alone, no picture details) | text ×6 | 0, 0, 0 / 0 | 6/6 every run | 0.867 / 0.905 | 22,692 |
| prompt 8, vision on | image+text ×5, text ×1 (the scan) | 0, 0, 0 / 0 | 6/6 every run | 0.915 / 0.925 | 29,934 |
| prompt 8, vision off (text with the picture's size and text share) | text ×6 | 0, 0, 0 / 0 | 6/6 every run | 0.887 / 0.917 | 22,987 |

- Images with text went to review no more often than with vision alone:
  none did, in any setting. With vision on, confidence is back to the
  vision-only level (0.915 against 0.913), above the text-alone route
  (0.867), and the scanned page is filed under `Financeiro` every run
  (vision alone put it under `Casa e veículos`, also accepted).
- The drop to review reported in #100 did **not** reproduce on these
  synthetic pictures: their text is clean, complete and on topic, so even
  the text alone is enough (confidence 0.85, still above the 0.8 review
  threshold). Real flyers and memes, with stylised lettering and a few
  disconnected words, are harder; the lower confidence of the text-alone
  route is the same direction, but the corpus cannot show the size of the
  effect. "Before #100" was run by forcing the old routing in the new code
  (same prompt 8 instructions, no picture details), not with the old
  release.

The document-or-picture rule (`ocr.looks_like_document`: at least 10 lines,
300 letters and digits, and 20% of the image under text boxes) comes from
what macOS's text recognition reports for these images:

| Image | Lines | Letters and digits | Area under text | Verdict |
| --- | --- | --- | --- | --- |
| scanned A4 page | 26 | 988 | 31.7% | document: text only |
| chat screenshot | 23 | 210 | 12.6% | picture: image + text |
| photographed till receipt | 19 | 145 | 6.6% | picture: image + text |
| flyer | 6 | 88 | 12.2% | picture: image + text |
| meme | 3 | 48 | 13.3% | picture: image + text |
| photo with a caption | 4 | 42 | 1.2% | picture: image + text |

Line count alone does not separate a page from a chat (26 against 23); the
amount of text and the share of the image it covers do, by a wide margin.
The receipt lands on the picture side: a small slip on a large photo. That
costs a few hundred more tokens than its text alone and changes nothing
else (filed under `Financeiro` with every field right, every run). A receipt
photographed close up, filling the frame, crosses to the document side.

### Structured fields

Over the 58 items with gold fields (the receipt and the scanned page add
two), prompt version 8:

| Field | Precision `en` | Recall `en` | Precision `pt-BR` | Recall `pt-BR` |
| --- | --- | --- | --- | --- |
| `kind` | 100.0% (58/58) | 100.0% (58/58) | 96.5% (56/58) | 96.5% (56/58) |
| `issuer` | 97.3% (36/37) | 90.0% (36/40) | 97.4% (37/38) | 92.5% (37/40) |
| `amount` | 100.0% (26/26) | 100.0% (26/26) | 100.0% (26/26) | 100.0% (26/26) |
| `currency` | 100.0% (29/29) | 96.7% (29/30) | 100.0% (29/29) | 96.7% (29/30) |
| `document_date` | 100.0% (23/23) | 79.3% (23/29) | 100.0% (23/23) | 79.3% (23/29) |
| `due_date` | 100.0% (15/15) | 100.0% (15/15) | 100.0% (15/15) | 100.0% (15/15) |
| `period` | 100.0% (26/26) | 100.0% (26/26) | 100.0% (26/26) | 100.0% (26/26) |

Against prompt version 7 (`en`: the same precision everywhere; recall
89.5%, 96.5% and 77.8% for issuer, currency and document date) nothing went
down. In Portuguese, prompt 7 had `kind` at 55/56; prompt 8 has 54/56 on
the same items (`transactions-export.csv` as `other`, as before, and a
traffic-fine notice as `tax`): one item, inside the noise. Most "invented"
values are in the documents but not in the gold (a hotel booking's total, a
diploma's university and date, the rent of a lease, the month of a
receipt); the rest are periods given to receipts and tickets, which state a
date rather than a period.

### What the Haiku baselines show

- **Categories are not the problem.** In English the two misses are a
  travel insurance policy filed under `Finance` (as with prompt 7) and a
  household budget spreadsheet filed under `Other`; Portuguese is at 100%.
- **Low-signal items go to review a little too often**, with confidence
  0.4–0.6: in English, scans and recordings known by name only
  (`CNH-digital.pdf`, `passport-scan.pdf`) and a one-word name
  (`proposta.pdf`); in Portuguese, short personal notes and an invitation
  that belong in `Outros` (`shopping-list.txt`, `ideias.md`,
  `wedding-invitation.pdf`).
  Every item the gold expects in review went there, in both languages.

The Ollama row waits for issue #92 (a measured, fully local setup); no chat
model was installed on the machine that measured these.

## In CI

The test suite runs the whole corpus through the offline `fake` provider and
an oracle (`tests/integration/test_eval.py`), checks that no sensitive item is
ever sent, and that the gold file matches the code. No paid provider is ever
called in CI.
