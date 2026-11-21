# Measuring quality

`flib eval` runs a built-in, synthetic corpus of 154 downloads through the real
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
```

| Option | What it does |
| --- | --- |
| `--provider NAME` | `anthropic` (default), `claude-code`, `ollama`, `openai-compat` or `fake` |
| `--model M` | The model; defaults to the provider's default (required for `ollama` and `openai-compat`) |
| `--base-url URL` | `openai-compat` (required) or `ollama`: the API root |
| `--language en\|pt-BR` | Which locale's default categories and naming language to use (default `en`) |
| `--limit N` | A fixed sample of N items (it always keeps some of the sensitive ones) |
| `--max-cost USD` | Stop sending once this much is spent (default 1.00); the rest is reported as deferred |
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
- **Languages**: 66 items in Portuguese, 34 in English, and 54 with no text
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

Corpus version 1, default seed, all 154 items (144 sent; the 10 sensitive
ones withheld). Prompt version 5 was measured on 2026-11-20 with flib 0.1.0,
before the structured fields; prompt version 7 (with them) on 2026-11-21.

| Provider / model | Language | Prompt | Category accuracy | Review (expected caught) | Name kept precision / recall | Sensitive sent | Items/min | Tokens in / out | Cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `anthropic` / `claude-haiku-5-5` | `en` | 7 | 98.6% (142/144) | 14 sent, 8/9 expected | 98.6% / 89.7% | 0 | 28.1 | 374,483 / 33,415 | US$ 0.054 |
| `anthropic` / `claude-haiku-5-5` | `en` | 5 | 97.9% (141/144) | 12 sent, 8/9 expected | 98.6% / 87.5% | 0 | 29.6 | 220,403 / 27,673 | US$ 0.036 |
| `anthropic` / `claude-haiku-5-5` | `pt-BR` | 5 | 100.0% (144/144) | 13 sent, 9/9 expected | 100.0% / 75.0% | 0 | 29.9 | 332,147 / 32,141 | US$ 0.049 |
| `ollama` (a local model) | | | not measured | | | | | | |

The structured fields with prompt version 7 (`en`, the same run), over the
56 items with gold fields:

| Field | Precision (when answered) | Recall (of gold values) | Invented (where the gold has none) |
| --- | --- | --- | --- |
| `kind` | 100.0% (56/56) | 100.0% (56/56) | n/a (every item has one) |
| `issuer` | 97.1% (34/35) | 89.5% (34/38) | 14/18 |
| `amount` | 100.0% (25/25) | 100.0% (25/25) | 12/31 |
| `currency` | 100.0% (28/28) | 96.5% (28/29) | 13/27 |
| `document_date` | 100.0% (21/21) | 77.8% (21/27) | 8/29 |
| `due_date` | 100.0% (15/15) | 100.0% (15/15) | 0/41 |
| `period` | 100.0% (25/25) | 100.0% (25/25) | 13/31 |

Every amount, due date and period the model gave where the gold has one is
right, to the cent. Most "invented" values are in the documents but not in
the gold (a hotel booking's total, a diploma's university and date, the rent
of a lease, the month of a receipt); the rest are periods given to receipts
and tickets, which state a date rather than a period. The prompt version 7
run is the one saved as `anthropic-claude-haiku-5-5-en.json`; the
Portuguese file is still the prompt version 5 run.

The full results are saved in [`docs/quality/`](quality/). The Ollama row
waits for issue #92 (a measured, fully local setup); no chat model was
installed on the machine that measured these.

Two items more went to review with prompt version 7 than with version 5
(`aula03.pptx` and `podcast-episodio-42.mp3`, known by name only, at
confidence 0.6). Items between 0.6 and 0.8 change sides from one run to the
next: the three runs made while writing version 7, whose wording changed
only in the name-choice and confidence instructions, sent 14, 12 and 14,
each time a different pair. Expected reviews caught stayed at 8 of 9.

What the Haiku baselines show:

- **Categories are not the problem.** In English the three misses are a hotel
  confirmation and a sales CSV filed under `Finance`, and a Portuguese
  warranty filed under `Contracts`.
- **Descriptive names are often replaced.** Recall of "kept" is 87.5% in
  English and 75% in Portuguese: the model rewrites names that were already
  good (`distrato.pdf`, `boleto-condominio-outubro.pdf`), and with `pt-BR` it
  translates descriptive English names (`boarding-pass.pdf` becomes
  `cartão-de-embarque-…`).
- **Low-signal items go to review a little too often**, with confidence
  0.4–0.6: in English, scans and recordings known by name only
  (`CNH-digital.pdf`, `passport-scan.pdf`, `screen-recording.mov`); in
  Portuguese, short personal notes that belong in `Outros`
  (`shopping-list.txt`, `ideias.md`, a recipe, an invitation). Every item the
  gold expects in review went there in Portuguese; in English, `temp.docx`
  (lorem ipsum) was filed under `Other`.

## In CI

The test suite runs the whole corpus through the offline `fake` provider and
an oracle (`tests/integration/test_eval.py`), checks that no sensitive item is
ever sent, and that the gold file matches the code. No paid provider is ever
called in CI.
