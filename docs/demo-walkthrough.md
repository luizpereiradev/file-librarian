# Demo walkthrough (for maintainers)

A 60 to 90 second sequence for the presentation video, step by step, on the
[demo library](getting-started.md#try-it-on-a-demo-library): every file, name
and amount on screen is invented, and nothing touches your own Downloads,
configuration, index or Keychain. Each take starts from a fresh demo, so a take
can be redone exactly. The [video script](video-script.md) is the shorter
version with narration, in English and Portuguese.

## Before each take

```sh
uv sync
uv run python -m scripts.demo /tmp/flib-demo        # rebuilds it from scratch if it is there
```

Add `--messy` for a cluttered `before/Downloads` folder to film before anything
else: forty files named the way downloads arrive. flib never reads it, so
nothing below changes.

Paste the three lines it prints (`DEMO_HOME=…`, `FLIB=…`, `CFG=…`) into the
terminal you will record, then clear the screen. For a shorter command line on
screen, also paste:

```sh
flib() { HOME="$DEMO_HOME" "$FLIB" "$@" -c "$CFG"; }
```

From here on, `flib status` in that terminal means the demo's. For the
Brazilian Portuguese take, build with `--pt-BR` into another folder and search
for `fatura` instead of `invoice`.

!!! warning "Never install the menu bar item or a schedule from the demo"
    `flib menu` and `flib schedule` install LaunchAgents whose labels are the
    same as your real ones. For the menu bar shot, use
    `docs/assets/screenshots/en/menu.webp` (rendered from the demo's state by
    `scripts/screenshots.py`), or your own Mac's real item.

## The sequence

| Time | Show | Type or click | What the viewer sees |
| --- | --- | --- | --- |
| 0–8 s | Terminal | `flib status` | 5 files in review (4 photos, one suggestion), 1 failed, 1 overdue and 2 due this week, 1 file left in place as sensitive, a duplicate set aside, the month's cost |
| 8–20 s | Terminal | `flib simulate`, then `flib run` | Today's three downloads: `scan_0007.pdf` becomes `Finance/2026/<today>_pharmacy-receipt.pdf`, `untitled.docx` a statement of work in Contracts, the study notes keep their name; the failed export is filed on retry. The answers come from the cache: no cost |
| 20–30 s | Terminal | `flib search invoice`, then `flib search --kind bill,invoice --sum --by issuer` | Two invoices with issuer, amount, due date and period; then totals per issuer, BRL and USD never added together |
| 30–38 s | Terminal | `flib due` | One overdue electricity bill, a water bill in 2 days, a hosting invoice in 5, totals per currency |
| 38–42 s | Terminal | `flib ui` | The browser opens on Status |
| 42–55 s | Browser, Review | **Accept Work for all 4…**, confirm | The four photos move to Work in one run; the page offers Undo |
| 55–62 s | Browser, Review | **Undo**, confirm | The photos are back in review: nothing is lost |
| 62–72 s | Browser, Search | type `invoice`, **Search** | The invoices with totals per currency; Open and Show in Finder on each |
| 72–80 s | Browser, Due | **Mark done** on the water bill | It leaves the list; Undo is offered |
| 80–90 s | Menu bar image, then Activity | — | The cat with a paw raised and its menu; Activity shows the days of runs, the fix by hand, the undone move |

Close with **End session** in the browser (or Ctrl-C in the terminal).

## After recording

```sh
uv run python -m scripts.demo /tmp/flib-demo --clean
unset -f flib
```

If the interface or the command line changed since the screenshots on the site
were taken, refresh them and the terminal samples:

```sh
uv run python -m scripts.screenshots     # needs Chrome or Chromium; headless, no window
uv run python -m scripts.docs_samples
```
