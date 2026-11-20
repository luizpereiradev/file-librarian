# Guide: the details

The [README](../README.md) covers what most people need. This page holds the
details behind some of its sections.

## Names

A file is renamed only when the model read its content (text, or the image
with vision on), its category allows renaming (`rename: false` keeps original
names), and the model is at least `naming.rename_confidence_threshold` sure
(0.8 by default). Videos, installers, archives, folders and images with vision
off keep their name.

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
characters (80 by default, extension included); the original extension. A
date already at the start of the old name is not repeated. Ambiguous dates in
documents are read as `naming.date_order` says (`DMY` by default). A taken
name gets `-2`, `-3`... `flib simulate` marks a kept name with "original name
kept", and `flib inspect` shows the choice and the model's reason.

## Duplicates

A file with the same content as one flib already filed is moved to
`_Duplicates` (`_Duplicados` in pt-BR) inside the organized folder, under its
own name. It is never deleted, and it is not sent to the AI, so it costs
nothing.

- **The filed copy is checked first.** flib checks that it is still there
  with the same content. If it is not where flib left it (you renamed, moved,
  edited or deleted it, or its disk is not connected), the new copy is left
  in Downloads with a note to run `flib reconcile` (`flib status` counts
  these). Once reconcile has found the filed copy gone, the next run files
  the new one in its place.
- A file you dragged from the organized folder back to Downloads is
  recognised as the same file and left there.
- A copy of a sensitive file stays where it is, like the original.
- `flib undo` puts duplicates back like any other move, and later runs leave
  them there.
- `flib search` leaves duplicates out unless you add `--duplicates`; `flib
  inspect` on one says which file it is a copy of, and `flib status` counts
  them.
- If the original disappears, `flib reconcile` lists its duplicate as
  orphaned; move it back to Downloads to have it filed.
- Empty the folder whenever you like: flib never does. `duplicates: leave`
  turns all this off (copies then stay where they are), and
  `duplicates_folder` renames the folder.

**Copies left by earlier versions.** With the default `duplicates: move`, the
first run also sets aside the copies that earlier runs left in Downloads
(`report (1).pdf` next to a filed `report.pdf`). A note after the run says so;
scheduled runs write it to their log, and `flib status` shows it until you
have seen it after a `flib run` in Terminal. Nothing is deleted: `flib undo`
puts them back, and `duplicates: leave` keeps them where they are.

## Correcting files

```sh
flib fix ~/Downloads/Organized/Other/scan.pdf --category Finance
flib fix ~/Downloads/Organized/_Review/document.pdf --category Contracts --name lease-2026
```

The file moves (a taken name gets `-2`, but changing only the capitalization
renames in place; a missing extension is kept), the index, search and the
Finder tag follow, and `flib undo` reverts it like a run. If flib is
interrupted mid-fix, the next command finishes the whole fix or leaves the
file as it was.

Corrections, these and the moves or renames `flib reconcile` finds, are sent
as examples with later requests, so the classifier stops repeating the
mistake. `provider.max_examples` caps how many (10 by default, up to 50; `0`
sends none). [Privacy](../README.md#privacy) says what an example contains.

## Changing your rules

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

### Renaming or removing a category

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

## What vision costs

Estimated on the default model (Claude Haiku 5.5, US$0.10 per million input
tokens and US$0.50 per million output tokens). An image costs about
width × height / 750 input tokens:

| What is sent | Image tokens | Whole request, estimated |
| --- | --- | --- |
| Photo, 4:3, shrunk to 1568 × 1176 | ~2,460 | ~US$0.0005 |
| Screenshot, 16:10, shrunk to 1568 × 980 | ~2,050 | ~US$0.0005 |
| Scanned PDF, 3 pages | ~1,600-2,500 per page | ~US$0.001 |

"Whole request" adds about 1,000 tokens of instructions and metadata and a
few hundred output tokens. These are estimates from the published formula,
not measurements; what counts toward `limits.max_cost_usd_per_run` is the
usage the API actually bills, so the cap holds either way. Roughly 2,000
photos fit in the default US$1 cap. Lowering `vision.max_image_px` lowers the
cost quadratically (1024 px: about 40% of it).
