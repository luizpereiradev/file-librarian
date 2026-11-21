# Troubleshooting

Start with `flib check-config` (the configuration and the API key) and
`flib schedule --status` (the daily run). `flib status` shows what the last
run skipped, and each run's log in
`~/Library/Application Support/file-librarian/logs` says why.

## A file is skipped as "unreadable"

flib could not look at the file, so it left it where it is and will try again
on the next run. The run summary and the run log say what failed: the step
(`stat` reads the file's details, `read` its content) and the error macOS gave,
for example:

```text
  skip    photo.jpeg  (unreadable: EPERM Operation not permitted, on read)
```

- `EPERM` or `EACCES` in the daily run: macOS keeps the scheduled run out of
  that file. Give `flib-python` Full Disk Access as described in
  [Run it every day](../README.md#run-it-every-day), then run `flib schedule`
  again to check. A scheduled run that hits this ends its log with a hint
  saying so. In a manual run, check the file's permissions (`ls -l`).
- `EDEADLK`, `EAGAIN`, `EINTR`, `ETIMEDOUT`, `EBUSY` or `ESTALE`: another
  program (a sync client, an antivirus, Spotlight) had the file busy. flib
  already tried once more after half a second; the next run usually reads it.
- Anything else, or the same file failing every day: run `flib doctor` and
  open an issue with the report.

## A file is skipped as "locked"

```text
  skip    photo.jpeg  (protected by macOS while the Mac is locked)
```

macOS Data Protection gives every file a protection class. Most files can be
read whenever you have logged in once since the Mac started. Some apps built
for iOS and run on the Mac, such as some messaging apps, save files with a
stricter class: macOS refuses to open them at all while the screen is
locked, even to their owner. Nothing is wrong with the file or with flib's
permissions, so Full Disk Access does not help here.

When the daily run starts while the Mac is locked, flib skips those files as
`locked` (no failed attempt is counted) and then waits for you to unlock the
Mac: it checks once a minute, sleeping in between, for up to
`schedule.wait_for_unlock_hours` (8 by default, up to 23; `0` turns the wait
off), and then runs once more for what is left. The run log and
`schedule.out.log` say when it starts waiting and how the wait ended. While it
waits it holds no lock, so `flib run` by hand works as usual; if a run is
going on when the Mac is unlocked, the wait just ends. `flib schedule
--remove` (or logging out) stops it cleanly. A scheduled run that finds an
earlier one still waiting does not wait as well: the one waiting already runs
once more for everything after the unlock.

`flib status` counts these files under the last run, and the periodic summary
mentions them. To file them right away, unlock the Mac and run `flib run`.
flib never changes a file's protection class: a file moved into the library
keeps it, so `flib reconcile`, `flib undo`, `flib fix` and `flib reprocess`
treat such a file they cannot read while the Mac is locked as "cannot tell
now" (never changed, never missing) and ask you to try again once it is
unlocked.

## Watch mode does not file a download

First check `flib schedule --status`: the mode must be `watch` or `both`
and the watch agent active. Then look at `watch.out.log` in
`~/Library/Application Support/file-librarian/logs`:

- **Nothing at all.** A start that finds nothing new writes nothing, by
  design. The file may still be downloading or changing (it is filed once it
  settles), too recent for its input's `min_age_days`, ignored, sensitive, or
  a file flib tried already: `flib simulate` shows why it is skipped. A file
  placed in a *sub-folder* of an input does not start flib, because macOS
  watches only the top level. If you changed `inputs` since installing it,
  the agent still watches the old folders: `flib status` says so, and `flib
  schedule --refresh` fixes it.
- **"N item(s) were still downloading or changing after 10 min".** The
  download took longer than `schedule.watch_settle_minutes`. It is filed by
  the next change in the folder, usually the browser renaming it when it
  finishes, or by the daily run.
- **"Not running: another flib process ... is running".** A run (by hand, the
  daily run, `flib reprocess`...) held the lock for more than 10 minutes; it
  or the next change takes care of the file.
- **"Automatic runs reached today's cost cap".** `limits.max_cost_usd_per_day`
  (or, for a provider with unknown prices, `limits.max_files_per_day`) was
  reached; files wait for tomorrow, or run `flib run` now. Raise the cap or
  set it to `null` to turn it off. `flib status` shows how many items wait
  and why ("Watch: N files wait in the inputs (daily cap reached)" or
  "(provider failing)"); items left behind are tried by a watch run once
  more the next day, and a new arrival always goes ahead of them.
- **Access denied.** The watch agent runs as `flib-python`, like the daily run:
  `flib schedule` checks folder access for it.

It runs at most once every `schedule.watch_min_interval_minutes` (5 by
default), and takes an item only once it has not changed for
`schedule.watch_quiet_seconds` (5 minutes), so a file can take about ten
minutes to be filed. `watch.out.log` is trimmed to about 1 MB; each watch
run's own log is a `watch-run-*.log` next to it.

## macOS asks about the Desktop or Documents

The Desktop, Documents and Downloads are each guarded by their own macOS
privacy permission (System Settings > Privacy & Security > Files and
Folders). When you add one of them to `inputs`:

- **By hand in Terminal**, the first `flib run` or `flib simulate` that reads
  it makes macOS ask whether *Terminal* (or your terminal app) may access
  that folder. Allow it; that answer covers the terminal only.
- **The daily run** is not your terminal: it runs flib's own copy of Python
  (`flib-python`), which never shows a prompt. `flib schedule` checks every
  input as the daily run sees it and prints one line per folder, `ok` or
  `denied by macOS privacy settings`. Give `flib-python` Full Disk Access
  (the path is printed; Full Disk Access covers the Desktop, Documents and
  Downloads at once), then run `flib schedule` again until each line says
  `ok`.
- A folder the run cannot list (denied, deleted, an unmounted disk) is
  skipped: the run organizes the other inputs, prints
  `skip    <folder>  (input folder could not be read...: EPERM ..., on list)`
  and a warning, and `flib status` counts it. Only when no input can be read
  does the run stop with an error.

A file on the Desktop that is skipped as `too recent (N days left)` is not
a permission problem: its input has `min_age_days`, and the file has not
been there that long yet ([More than one inbox](guide.md#more-than-one-inbox)).

## Images and scanned PDFs are classified from their name only

flib reads the text in screenshots, photographed documents and scanned PDFs
on your Mac with macOS's text recognition (OCR). When a document still ends
up classified from its name alone:

- `flib inspect FILE` says what a run would read from it: `text: N
  characters, recognised by OCR on this Mac`, or `none sent` and why
  (`OCR failed: ...`, `no readable text`, `sensitive`).
- `flib check-config` prints `ocr: on, on this Mac (...)`, or a warning when
  OCR cannot run here, with the reason. OCR uses `/usr/bin/osascript` and
  the Vision framework, which every supported macOS has; nothing needs
  installing. `ocr.enabled: false` turns it off.
- A photo with fewer than `ocr.min_chars` letters and digits (40) is not
  treated as a document: with `vision.enabled` the model sees it, otherwise
  it is classified from its name. Lower `ocr.min_chars` for documents with
  very little text.
- A warning that macOS cannot recognise a language means a code in
  `ocr.languages` is not one Vision knows; the warning lists those it does.
  The others are still used.
- The first recognition on a Mac can take about half a minute while macOS
  prepares its model; each one after that takes a fraction of a second. A
  file is given up after two minutes and handled as if OCR were off.
- A file whose recognised text contains a password or a key is marked
  sensitive on purpose: nothing of it is sent, and it stays where it is.

## No notification appears

The periodic summary's notification is posted with `osascript`, so macOS
shows it as coming from Script Editor, and it can be hidden: macOS may only
list Script Editor under System Settings > Notifications after its first
notification, with alerts off, and a Focus mode or "Do Not Disturb" silences
it. A notification macOS drops cannot be detected, so it is not retried. If
you never see one, allow notifications for Script Editor there, or rely on
the run log and `flib status`, which always have the same summary.

The notification is also skipped on purpose: it follows only a *scheduled*
run, only when a file has waited `summary.min_age_days` (2 by default), and
at most once every `summary.every_days` (7 by default).

## The doctor report

To report a bug, run `flib doctor`. It writes one file,
`doctor-<date>-<time>.txt`, and prints its path. The file holds flib's,
Python's and macOS's versions, how flib was installed, whether the MCP extra
is there, the configuration (validated), where the API key comes from, the
schedule's state, the database's schema version and counts, what `flib
status` shows, the last runs with their cost and the last five run logs
(`--logs N` for more). It is made to be posted in a public issue, so before
it is written:

- the API key is left out entirely, not even its last characters; only the
  word for where it comes from (`environment`, `keychain`, `env_file`, `none`);
- folders become `<home>`, `<input-1>`, `<output>` or `<data>` (flib's own
  folder), and other folder names `<dir-N>`; category names and the review
  folder stay, as they come from the configuration;
- file names become `<file-N>` with only the extension kept (`<file-3>.pdf`),
  the same number for the same name, so one file can be followed through the
  logs;
- anything that looks like a secret, e-mail addresses, URLs, quoted text, your
  account name, category hints and `ignore`/`never_send` patterns that name
  something are replaced as well.

Read the file before you attach it: redaction is careful, not infallible.
`--stdout` prints the report instead, `--output PATH` (`-o`) writes it
elsewhere, and `--format markdown` makes it paste-ready for an issue.

The report goes into flib's logs folder
(`~/Library/Application Support/file-librarian/logs`), owner-only, keeping the
last ten, rather than on the Desktop: flib writes nowhere new, and on many Macs
the Desktop is synced to iCloud, which would upload a file you have not read
yet. `open -R` on the printed path shows it in Finder, ready to drag into the
issue.
