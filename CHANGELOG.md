# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **The interface speaks the configured language** (#126): with `language:
  pt-BR`, every page of `flib ui` (including confirmations, outcomes, error
  and ended pages and the handoff page) and the menu bar item's menu and
  state line are in Brazilian Portuguese, with dates as `25/11/2026 22:48`,
  a decimal comma and `R$ 1.234,56`; English stays as it was and is the
  fallback; Portuguese says "nenhum arquivo" rather than "0 arquivo".
  Changing the language on the Settings page switches the interface from the
  next page and rebuilds the menu bar item. A cost cap or a search amount may
  be typed as `3,00` or `3.00`; `1.234` (in pt-BR) is refused as ambiguous
  rather than guessed. One catalogue (`i18n/messages/`), checked
  by tests. The command line, logs, notifications and docs stay English.

- **flib in the menu bar** (#116): `flib menu` builds a small AppleScriptObjC
  stay-open applet with `osacompile` (no Xcode, nothing downloaded), signs it
  ad hoc, puts it next to the notifier in flib's environment and starts it
  at login with the LaunchAgent `io.github.luizpereiradev.file-librarian.menu`;
  `--remove`
  takes everything away, `--status` and `flib status`/`flib doctor` say how
  it is, `--refresh` (also run by `flib schedule --refresh` and
  `scripts/install.sh`) rebuilds it when it is out of date, and `flib init`
  offers it. It shows the cat's pose for flib's state (#112) and a count,
  and a short menu: the state in words, the next run, Open flib, Review (n)
  and Due (n) when there is something there, Organize now, Settings… and
  Quit. flib writes `~/Library/Application Support/file-librarian/menu/state.json`
  (atomic, 0600, counts only, never a file name) whenever the instance lock
  is released (every run, undo, fix, review action, due settlement, MCP
  write), when a run starts, when the schedule changes and when a run could
  not work; writing never fails or slows a run (one `stat` when the item is
  not installed; no subprocess; a write only when the content changed) and
  then nudges the applet with a distributed notification, which reaches only
  a running applet and can never start one. The file is plain ASCII in one
  flat form, each key once; the applet checks it as a whole and key by key (a
  hostile file can only show the defaults), and a click runs only flib's own
  interpreter, fixed when it was built, with `-I`, the words of one action
  picked by its key inside the script, a fixed minimal environment and
  output to `/dev/null`. Organize now is never greyed out; a "running" left
  by a killed run shows as "The last run did not finish" after three hours,
  and `flib status`, the interface and `flib doctor` (which ask launchd
  anyway) rewrite the state with the live answer. A re-install that launchd
  refuses puts the item that was there back. The agent has no `KeepAlive`: off after Quit or a
  crash until the next login, and nothing respawns if the app is gone. The
  interface's Settings page says whether the item is installed, with the
  command for it. **Organize now** starts the daily agent
  (`launchctl kickstart`) when it is this configuration's, and otherwise runs
  here; with a run going on it does nothing and says so.
- `flib ui --open PAGE` while an interface is already running opens that
  page in it, instead of only saying one runs. The running server records
  its pid and port (`flib-ui.json`, 0600, next to the database) and its
  name in a private record in the account's own temporary folder (#125).
- `flib ui` has a **Settings** page (#117): when to organize (off, daily at
  a time, as files arrive or both, installed in launchd by the same function
  as `flib schedule`), who classifies (a fixed choice among the providers the
  file already allows, never a typed address; the API key only as set or not
  set), vision, OCR and search by meaning, Downloads and the Desktop as input
  folders (from a fixed list, checked again on the server), duplicates,
  categories (add, rename, remove, hint, sub-folders; one that holds files
  is left to `flib reprocess --rename-category` / `--remove-category`, and
  the page says how), the cost caps, the summary notification, what is due
  and the language (which never moves the library: what it brought is
  written in the file first). Each section's form carries a pin of the
  file's bytes: a file changed since the page (by hand, another tab) is
  never overwritten. Each change takes the instance lock (a run in progress
  is said, nothing done), is made in the text by the new `config_edit`
  module (only the keys asked for; comments and order kept; checked by
  parsing the result), validated by `parse_config` before anything is
  written, backed up (`config.yaml.backup-<time>`, 0600, the latest 10) and
  written atomically (0600; a file that is a symbolic link is refused).
  **Restore the previous settings** puts the latest backup back after a
  confirmation page. The menu's Settings item is now a link; `flib ui --open
  settings`. `scheduler.install_for` is shared by `flib schedule` and the
  page. After the review of #130: a field left untouched is never rewritten
  (a multi-line hint is shown, not edited; cost caps keep every digit;
  `due.kinds` keeps its order); what changed is said field by field, inside
  the section that was sent, with what was typed kept on a refusal; a section
  index, folded long rules, and a confirmation before removing an input
  folder (whose comment goes with it); a schedule installed for another
  configuration file is never taken over without an explicit tick; commands
  in refusals are shell-quoted and name the file with `-c`; backups are named
  in UTC with a sequence (`<time>Z[-NN]`) and nothing else is ever listed or
  pruned; the file is checked again just before the rename, and hard links
  are refused; the Keychain is asked whether a key exists, never for it;
  `schedule.time` takes ASCII digits only (also in the file); long category
  names wrap on Search.
- `flib ui` has a **Review** page with **Undo** (#115), the first part of the
  interface that moves files. Review lists the files waiting in the review
  folder, by reason and suggested category, the biggest group first; each
  has Accept (its suggestion), another category and/or a new name (`flib
  fix`: flib learns from it), Put back where it was (`flib undo` for that
  file) and Open / Show in Finder. A group with a configured suggestion has
  Accept all, which lists the files first (at most 100) and moves them as one
  run (new `fix.fix_files`) that one Undo reverts. Undo, for a run or a file,
  from Review's outcome or from Activity, shows what it puts back and where
  before it does anything, and refuses what `flib undo` refuses, in its
  words. Due has Mark done / Open again (`flib fix --done` / `--not-done`).
  Every action is a POST through the route table (cookie, exact origin,
  CSRF), takes the instance lock for that action (a run in progress is said,
  and nothing is done), names files by index id with a pin of the state the
  page showed (a file changed, undone or handled in another tab since is
  refused, never acted on), and ends in a redirect, so reload, Back and
  Forward never repeat it. Categories come only from the configuration;
  names go through `flib fix`'s sanitiser; nothing is overwritten; sensitive
  files are never listed or acted on. The Status page and the menu link
  Review; `flib ui --open review`. A run's counts after `flib fix` now come
  from its journal.
- After the review of #115: undoing a `flib fix` now deletes the corrections
  that fix recorded (new column `corrections.operation_id`, migration 0019),
  so an undone fix is never sent to the classifier as an example and never
  hides an older correction; a number added only because a name was taken
  (`a-2.pdf`) is no longer learned as a name; the corrections of one run
  that fixed many files the same way are sent as one example. `flib undo`
  says "moved again since, by run N (undo that first)" instead of blaming a
  move by hand when a later run took the file elsewhere. Recovery no longer
  takes a copy with the same bytes, already at the name a crashed move had
  journaled, for the moved file while the source is still in place. The
  interface's undo pages list what will be skipped and why before anything
  is done, and Activity offers Undo only where something can go back. File
  names are isolated (`<bdi dir="ltr">`), and on a phone the menu wraps
  instead of widening the page.
  The undo pages and `flib undo` now share one judgement of what would be
  skipped and why (`undo.skip_reason`): a file back where a move put it, with
  the same content, can go back whatever moved it in between; a move a
  failure left half-recorded is judged as recovery will leave it; a missing
  original folder outside the library and a file locked right now are said
  before anything is done. Recovery finds a moved file that was saved anew
  (new inode, same bytes) even when a new file took its old name.
- `flib ui` has Search, Activity and Due pages (#114), read-only and
  readable without JavaScript. **Search** is `flib search` in a form:
  category and kind chips, the other filters under "More filters", each
  match with Open and Show in Finder, totals per currency over every match
  as `flib search --sum` adds them up (optionally by issuer, month, year,
  kind or category), and Export CSV. **Activity** lists the runs of the last
  7, 30 or 90 days by day: what each moved where, duplicates set aside,
  moves undone, sensitive files left in place (a count), files skipped while
  the Mac was locked and the wait for the unlock (from the run logs), and
  the cost. **Due** is `flib due`, with its window (7 days, 2 weeks, 1 or 3
  months, overdue) and totals per currency. `flib ui --open` takes
  `search`, `activity` and `due`. The pages are built on the server with
  every value from the index escaped; search words go in a form body, never
  in the address, so they stay out of the browser's history; sensitive
  files are never listed, only counted; Open and Show in Finder post the
  file's index id and the server checks its real path again (inside the
  library or an input folder, no `..`, no symbolic link) before asking
  macOS; Open takes documents only (an allow-list of extensions without the
  macro-capable `.doc`, `.xls` and `.ppt`; no execute bit; no alias; no
  per-file "Open With" or Finder type/creator picking an app), so an app,
  script, installer or web location gets only Show in Finder; names lose Unicode format characters (no right-to-left
  tricks); the CSV is an attachment of up to 5000 matches whose cells cannot
  run as spreadsheet formulas; a search by meaning waits at most 5 s per socket
  operation for the embeddings server, and falls back to words if its client
  cannot be made; any address choice the page does not offer is 400.
  The Content-Security-Policy now also requires Trusted Types.
  Driven in real browsers before release: every form post (Search, Export,
  Open, Show in Finder and End session, which `flib ui` from #113 had too)
  was refused in Chromium, because `Referrer-Policy: no-referrer` makes a
  browser send `Origin: null`; the policy is now `same-origin`, and a
  headless-Chrome test (`FLIB_TEST_BROWSER=1`, run in CI) drives a whole
  session. Search results survive Back and Forward (post, redirect, get, the
  words never in the address); the "session ended" page keeps its style;
  refusals are pages with the menu and what to do; a window without the
  session is told when it is open elsewhere; another `flib ui`'s handoff
  folder is no longer swept; Status shows review reasons compactly, dates
  and costs as the other pages do; narrow screens get a Due card layout,
  a skip link and stronger contrast.
- The cat's poses for the coming menu bar item (#112): five monochrome
  template icons, hand-written SVG in `assets/menu/` (18 pt, black on
  transparent, nothing external), each a silhouette of the app icon's cat:
  carrying a sheet (a run is in progress), a paw raised (something is due),
  ruffled with its back arched (flib cannot run), sitting and looking at you
  (files in review or failed) and curled up asleep (nothing pending).
  `poses.menu_state` picks the pose from `flib status`'s counts in that order
  of priority, with the count the pose is about. `scripts/menu-icons-sheet.js`
  renders them with AppKit into `docs/images/menu-poses.png`. Nothing shows in
  the menu bar yet: that is #116.
- `flib ui`, an early local interface (#113): a web server on 127.0.0.1
  that exists only while in use, with a Status page (the counts of `flib
  status`, light and dark, in the icon's colours) and greyed-out places for
  Review, Search, Activity, Due and Settings. `--open PAGE`, `--no-browser`
  and `--port N`; it ends after `ui.idle_minutes` (default 10) without use,
  with End session, or with Ctrl-C, and one runs at a time. Standard library
  only, pages shipped in the package, no build step and nothing from a CDN.
  The browser gets a single-use token through a handoff file only the user
  can read, in the account's own temporary folder (never on a command line,
  where other accounts could see it); it
  becomes an `HttpOnly`, `SameSite=Strict` session cookie, and "session
  started" is printed. A used token that comes back without the session
  ends the server with a warning. Every request is checked for an exact
  `127.0.0.1` `Host` (DNS rebinding) and origin, every POST route for the
  session cookie and a per-session CSRF token; a strict
  Content-Security-Policy with no inline script, size limits, requests read
  without a thread until they have fully arrived, a deadline for the whole
  request, a thread cap, static files from memory only, and
  error pages that say nothing. docs/architecture.md has the threat model.
- Notifications show as "file-librarian", with flib's own icon, instead of
  Script Editor. `flib schedule` (and `--refresh`, which
  `./scripts/install.sh` runs) builds a tiny notifier app inside flib's own
  environment, next to `flib-python`, with tools that ship with macOS
  (`osacompile`, `sips`, `iconutil`, `codesign`; no Xcode, no new
  dependency), and posts one first notification so macOS asks for
  permission while you are there. The icon (an orange cat shelved between
  book spines; the artwork was AI-generated for this project, and the README
  shows it too) comes from one packaged image, `assets/icon-1024.png`; the app is rebuilt when it, the script or the
  bundle layout change, and `flib schedule --remove` deletes it. The text
  still reaches the app as arguments only. When the app is not built or
  cannot be launched, the notification goes through `osascript` as before;
  `flib status`, `flib status --json` and `flib doctor` say which notifier
  is in use and why.
- A measured, fully local setup (#92). `flib init --local` (and the
  provider `local` in plain `flib init`) checks that Ollama is installed and
  running (explaining `brew install ollama` or the app if not; it never
  installs it), offers to download `gemma4:e4b` and `embeddinggemma` after
  showing their sizes (never with `--yes`), writes `provider: ollama` with
  that model, local OCR, vision off and semantic search on with
  `embeddinggemma`, runs the trial on copies and states the measured
  quality gap. `flib check-config` and `flib doctor` show `fully local:
  yes`, or what leaves the Mac and where to. Four local models were measured
  on the whole `flib eval` corpus in both languages (docs/quality.md, "Local
  models"): `gemma4:e4b` files 92% (`en`) and 97% (`pt-BR`) of items in a
  right category against 99% and 100% for Claude Haiku, sends 7 of 9
  doubtful items to review against 9 of 9, at 21-24 items a minute; README
  has a "Nothing leaves this Mac" section. The `ollama` provider now
  classifies through Ollama's own `/api/chat`: `think: false`,
  `temperature: 0`, a context sized to the request (8192 to 32768 tokens,
  the excerpt cut to fit), the schema as `format`, or in the instructions
  for models that answer "structured output is unavailable" (the MLX builds
  Ollama pulls by default on Apple silicon, with which every request used to
  fail), repair of near-valid JSON, category matching ignoring case and
  accents, one corrective retry, a 300-second default timeout and a clear
  "not pulled" error. `flib eval` reports how answers were obtained
  (`Answers: repaired, retried...`) and `--vision` also sets
  `provider.vision` for `ollama` and `openai-compat`. The hosted providers'
  requests are unchanged.

- Watch mode: organize files as they arrive (#90). `schedule.mode: watch`
  (or `flib schedule --mode watch`) installs a second launchd agent that
  starts flib when an input folder changes (`WatchPaths`; the top level
  only, as macOS does not watch sub-folders), at most once every
  `schedule.watch_min_interval_minutes` (5); `both` keeps the daily run as
  a safety net, and `daily` stays the default. A watch run waits, holding
  no lock, for downloads in progress and files still changing to settle (up
  to `schedule.watch_settle_minutes`, 10), then runs once for everything
  ready: a burst is one run. A start that finds nothing new, such as the
  one flib's own moves cause, exits at once with no run, no AI call and no
  log line; a run going on, another watch run waiting, or an unlock waiter
  alive make it give way quietly (only `flock`s, no process is signalled).
  New per-day cost cap for automatic runs, `limits.max_cost_usd_per_day`
  (three per-run caps by default; `null` turns it off), summed from the
  recorded cost of today's daily and watch runs: once reached they send
  nothing more to the AI until midnight, saying so once in the run log and
  in `flib status`. Runs record the new trigger `watch` (migration 0018).
  `flib schedule --status`, `flib status` and `flib doctor` show the mode,
  the last watch run and today's automatic cost; `flib init` asks the mode
  after offering the schedule; `--refresh` keeps the mode and `--remove`
  removes both agents. A watch run takes an item only once it has been quiet
  for `schedule.watch_quiet_seconds` (300; slow writers pause for more than
  a minute), counts what is new over every ready item and puts new arrivals
  ahead of items left behind (which are tried again the next day), and
  hashes each item once (digests kept by inode, size and mtime, without
  names). Automatic runs wait up to 10 minutes for a run in progress
  instead of failing; a watch run leaves the unlock wait to the daily agent
  when it is installed. `flib status` shows how many items wait and why. For
  a remote provider with unknown prices, `limits.max_files_per_day` (five
  `max_files_per_run`) limits automatic runs instead, with a warning in
  `flib schedule`, `check-config` and `doctor`. Watch runs keep their own run
  logs (`watch-run-*.log`) and `watch.out.log` is trimmed at about 1 MB. A
  stale `WatchPaths` after `inputs` changed is reported everywhere with
  "run `flib schedule --refresh`". The wait for downloads goes on while they
  keep changing (up to 6 h; `watch_settle_minutes` is how long without any
  change before it gives up), and the watch agent also starts every
  `schedule.watch_fallback_minutes` (30; `StartInterval`) for what launchd
  did not report, including files skipped while the Mac was locked.
- What is due (#91): `flib due` lists the bills, invoices, statements and taxes
  (`due.kinds`) whose due date the AI read in them, overdue up to 30 days ago
  and due in the next 7 days (`--within 2w`, `--overdue`, `--issuer`,
  `--kind`, `--json`), soonest first with the amount, issuer and path, and
  totals per currency. Read-only, no lock, works before the first run; days
  are counted in the local time zone. A document without a stated due date
  is never listed. `flib fix PATH --done` / `--not-done` marks one paid or
  open again, journaled so `flib undo` reverts it; `--all` also shows done
  documents and due dates on other kinds; `due.done_tag` lets a Finder tag
  mark one done. `flib status` adds a "Due:" line (and `due` in `--json`),
  the periodic summary counts documents due within
  `due.remind_days_before` days (3) or overdue, each one reminded once
  (counts only, no names or amounts), and the MCP server has a read-only
  `list_due` tool. The index records when a document was marked done and
  which due date was reminded (migration 0017).
- More than one inbox, each with its own rules (#89): an entry of `inputs`
  may be a mapping with `path` and `only` (name patterns; everything else is
  left alone), `ignore` (added to the global list, wins over `only`),
  `min_age_days` (an item is taken only once that many days have passed
  since it last changed or arrived in the folder, not since it was last
  opened; younger ones are skipped as `too recent (N days left)`, never an
  error and never waited for), a fixed `category` (no AI call, no cost: the
  name is kept unless `rename: true`, no description or structured fields
  are stored, tags are words of the name; sensitive files still stay put)
  with an optional `layout`, and `folders` and `duplicates` overrides. A
  plain list keeps working unchanged. Inputs that overlap (also through a
  symlink or the `/System/Volumes/Data` spelling), the home folder itself
  and inputs inside `output_root` are rejected at load; `flib check-config`
  prints each input with its effective rules and warns about a fixed
  category without `only`. The index records the input each file was taken
  from and whether a rule filed it (migration 0016): `flib status` counts
  files per input (`<input-N>` in `flib doctor`), and `flib reprocess`
  (`--fill-fields` too) never asks the AI about files filed by rule. One
  input folder that cannot be read is skipped with a warning (and counted
  by `flib status`) instead of stopping the run. `flib init` offers the
  Desktop (documents and images only, `min_age_days: 7`, folders left alone)
  and a screenshots folder used for nothing else (a fixed `Screenshots`
  category by year and month, taking only what macOS names as screenshots
  or screen recordings); both default to no.
- Patterns (`ignore`, `never_send`, `only`) now ignore the Unicode form of a
  name as well as its case, so a decomposed `Relatório.pdf` matches
  `Relatório*`. Office lock files (`~$*`) are always skipped, and names that
  say they hold passwords or recovery material, matched as whole words
  (`password`, `senha`, `contraseña`, `mot de passe`, `Passwort`, `2fa`,
  `recovery codes`, `backup codes`, `seed phrase`, `private key`,
  `bitwarden export`, `logins.csv`...; never an installer), are sensitive:
  never sent, left in place. The index records why a file is sensitive
  (its name or its content); one sensitive by its name alone is looked at
  again once renamed or allowed by the new `sensitive.allow_names`, and
  `flib status --sensitive` lists each file left in place and why.
- Amounts, dates and issuers (#88): the answer also carries what a document
  states, and nothing it does not: its `kind` (bill, receipt, invoice,
  statement, contract, payslip, tax, ticket, policy, report, form, letter,
  manual, other), `issuer`, `amount` with its ISO 4217 `currency`, document
  and `due_date`, and the `period` it refers to (prompt version 7). Values
  are read tolerantly (`1.234,56` and `1,234.56`, `R$`, `€`, dates with
  `/`); one that cannot be read is dropped, never the whole answer, and an
  amount that looks like a document or account number is never kept. They
  are stored in typed columns (the amount in cents; migration 0015) and
  journaled like the rest of the answer. `flib search` filters by
  `--issuer` (case, accents and legal forms ignored), `--kind`,
  `--min-amount`/`--max-amount`, `--currency`, `--due-before`/`--due-after`
  and `--period 2026` or `2026-09`, shows the fields with each result, and
  `--sum [--by issuer|month|year|kind|category]` adds up amounts per
  currency: "what did I pay to Power Co in 2026?" is `flib search --issuer
  "Power Co" --period 2026 --sum`. The MCP server's `search_files` takes the
  same filters, every file carries its fields, and a new read-only
  `summarize_amounts` tool gives the totals. `flib inspect` shows the
  fields; `flib reprocess --fill-fields` asks only for the fields of files
  filed before them, after showing the cost, and records them in one run
  `flib undo` reverts. `flib eval` scores each field (precision, recall,
  values given where the gold has none); see
  [docs/quality.md](docs/quality.md) for the baseline.
- Sub-folders inside a category (#86): an optional `layout` per category,
  with `{year}`, `{month}` and `{issuer}` (`"{year}"`, `"{issuer}/{year}"`...,
  up to three levels, validated at load). The year and month come from the
  document's date, else the download date, else the day filed; the issuer is
  a new field of the answer (the prompt asks for an organisation, not a
  private person; one holding an identifier is dropped), stored in the index
  with the document date and with the date the file's year and month were
  taken from, kept from then on so a change of file metadata (a copy to
  another disk, a time zone) never moves a file (migration 0014; prompt
  version 6). Issuer folders are stable: a name is matched with the folders
  already there ignoring case, accents, punctuation, `.com` and unambiguous
  legal forms (`Inc`, `Ltd`, `LLC`, `S.A.`, a trailing `SA`...), then by a
  conservative similarity (a doubled or swapped letter, never a digit, a
  plural, a different letter or word), and reuses its exact spelling across
  sibling year folders. An unknown value
  stops at the deepest folder known; a folder that cannot take a file sends
  it to the category's top, never failing it.
  `flib reprocess --apply-layout [--category NAME] [--ask-issuer]` moves
  organized files into their layout from what the index stores, as one run
  `flib undo` reverts; files you placed yourself stay, and values taken
  from the cache are written to the index with the run. `flib fix
  --name` now renames a file inside its sub-folder instead of moving it to
  the category's top folder.
- Local OCR (RF-26 done): the text in screenshots, photographed documents and
  scanned PDFs (first `ocr.max_pages`, 3) is recognised on the Mac with
  macOS's Vision framework, through a small `osascript` helper (no install,
  no network, works from the scheduled run). It is then the file's text: the
  secret scan sees it (a screenshot of a key makes the file sensitive),
  `never_send` and `max_chars_per_file` apply, every provider gets it and
  the file can be renamed. Photos with less than `ocr.min_chars` (40) go to
  vision when it is on, else are classified from metadata. A scanned PDF,
  or an image that is clearly a document (at least 10 lines and 300 letters
  and digits covering a fifth of it, from the helper's line boxes), sends its
  text only; with vision on, any other image with words in it (a flyer, a
  meme, a chat screenshot, a photo with a caption) sends the image and its
  text, so pictures that contain words are no longer sent to review for
  lack of context (#100). With vision off, or a provider that reads no
  images, the model is told the text was found in a picture, with its size,
  lines and how much of it is text (prompt version 8). `ocr.enabled`
  (on), `ocr.languages` (from `language`); `flib check-config` and `flib
  doctor` say whether OCR works, `flib inspect` whether a file's text came
  from it.
- `flib eval` measures classification quality on a public, synthetic corpus of
  160 downloads (formats flib reads, English and Portuguese, both locales'
  categories, generic and descriptive names, near-identical bills, items for
  review, credential files) with gold labels in
  `src/file_librarian/evals/gold.json`. It runs the real pipeline in simulate
  mode in a temporary folder with a throwaway database, never reading your
  configuration, library or cache, and reports category accuracy (overall,
  per category, common mistakes), review rate, name-kept precision and recall,
  sensitive items withheld (it fails if one is ever sent), items per minute,
  tokens and cost. It asks before spending with a paid provider and honours
  `--max-cost`; `--json`, `--out` and `--compare` save and compare runs.
  Six images with text in them, drawn by code (a flyer, a chat screenshot, a
  meme, a photo with a caption, a photographed receipt, a scanned page),
  come with the text macOS recognised in them, recorded, so a run scores the
  same everywhere; `--vision`, `--no-ocr` and `--case image-text` measure
  them, and the report counts how many went to review and how each was sent.
  Baselines for `claude-haiku-5-5` are in [docs/quality.md](docs/quality.md).

**Setup and scheduling**

- `flib init`, a guided first run: language, folders, provider (Claude Code if
  logged in, else Ollama if running, else `anthropic`), vision and API key. It
  writes a commented configuration (`--force` replaces one), simulates a run on
  copies of your `--sample N` latest items (30) and offers to schedule.
- `flib schedule` installs a launchd agent for a daily `flib run` at
  `schedule.time` (12:30) or `--at HH:MM`; `--status`, `--remove`, `--refresh`.
  It checks folder access as launchd will and offers to store the API key in
  the Keychain. If a run skipped files macOS protects while the Mac is locked,
  it runs once more after the unlock (`schedule.wait_for_unlock_hours`, 8).
- `scripts/install.sh` installs `flib` pinned to `uv.lock` (`--with-mcp`).
  Configuration (`config.example.yaml`) needs only `provider.model`;
  `language: en` or `pt-BR` (15 default categories); `flib check-config`
  validates it.

**Organizing: scan, extract, classify, plan, move, undo**

- The scanner reads each input's top level, skips hidden, empty, in-progress
  and `ignore`d items, waits until items are stable, treats sub-folders and
  bundles as one unit and caps runs at `limits.max_files_per_run`. `flib run`
  and `simulate` say what was left alone and why (e.g. `unreadable: EPERM`).
  iCloud-only files are never downloaded, only skipped until they are local;
  files macOS protects while the Mac is locked are skipped as `locked`.
- Extractors: plain text, Markdown, CSV, JSON, YAML, HTML and code; DOCX,
  XLSX, PPTX; PDF text (first 20 pages); ZIP/TAR name listings; folder and
  `.app` listings; `.eml` (senders and recipients reduced to names and
  domains). Damaged or encrypted files fall back to metadata and never stop
  a run.
- The classifier honours `never_send`, retries transient errors and stops at
  `limits.max_cost_usd_per_run` (US$ 1; the rest wait). Answers are cached 7
  days, so `flib run` right after `flib simulate` costs nothing.
- The planner only targets configured folders under `output_root`; unknown
  categories and low confidence (`limits.confidence_threshold`) go to the
  review folder, with the reason recorded (`low_confidence`,
  `unknown_category`, `category_removed`, `moved_by_hand`).
- Every move is journaled first, never overwrites and is finished after a
  crash. `flib undo` (last run, `--run N`, `--file PATH`) checks content first.
  Moves to another volume are copied, verified, then the source removed
  (exFAT/FAT destinations refused). Filed items get their category as a
  Finder tag (`finder_tags.enabled`); undo removes only that tag.
- A log per run (newest 100 kept); `flib inspect` shows what flib reads.

**Names**

- Names follow `naming.pattern` (`{date}_{subject}`), keep the extension and
  get `-N` on collisions. Files are renamed only when the model read their
  content; folder units and `rename: false` categories keep their names. A
  descriptive original name is kept (rebuilt with flib's rules) rather than
  replaced by a vaguer one; `flib simulate` marks "original name kept". It
  is kept whatever its language and never translated, informal or
  abbreviated names count as descriptive, and only generic names or names
  the content contradicts are replaced; the proposed subject no longer
  appends the issuer or other fields to a good name (#97, prompt version 8).

**Duplicates**

- A file with the same content as one filed in the same run or still in the
  library goes to `_Duplicates` (`_Duplicados` in pt-BR) under `output_root`,
  never deleted and with no provider call; `duplicates: leave` keeps it in
  place, `duplicates_folder` renames it. A name without a copy suffix wins.
  If the filed copy moved, the newcomer waits for `flib reconcile`. Undo puts
  duplicates back; `flib search --duplicates` lists them.

**Search**

- `flib search QUERY` over names, descriptions and tags: accent-insensitive,
  prefix match, names ranked first, `--json`, `--limit`. Filters
  `--category`, `--ext`, `--since`/`--until` (`YYYY-MM-DD`) work with or
  without a query.
- Optional semantic search (`search.semantic.enabled`, off by default):
  `--semantic` (or `search.semantic.default`, which `--no-semantic` and the
  MCP `semantic: false` turn off for one search) so "power bill" finds an
  "electricity invoice". Any OpenAI-compatible `/embeddings` server (Ollama
  with `embeddinggemma` by default; key via `FLIB_EMBEDDINGS_API_KEY` or `flib
  key set --provider embeddings`). Only index metadata is embedded, never
  content; `--reindex` backfills; without a server it searches by words.
  Files found only by meaning need a cosine similarity of at least
  `search.semantic.min_similarity` (0.25, measured for `embeddinggemma`), so
  weak matches no longer fill `--limit`; word matches always stay.
  `embeddinggemma` gets its documented query and document prefixes
  (`query_prefix`, `document_prefix` for other models). `--json` and MCP hits
  carry `source` (`text`, `semantic`, `both`) and `similarity`.

**Corrections and reprocess**

- `flib fix PATH [--category NAME] [--name NAME]` moves a file as its own run
  (undoable, Finder tag swapped) and records a correction; recent corrections
  are sent as examples (`provider.max_examples`, default 10).
- `flib reconcile` updates the index for files moved, renamed or deleted by
  hand, found again by content; moves into another category or renames count
  as corrections, unfound files become `missing`. Nothing on disk changes.
- `flib reprocess` reclassifies filed files after the rules change and moves
  only those whose answer changed (`--rename`, `--override-corrections`,
  search filters). It estimates the cost and asks before sending and before
  moving (`--simulate`, `--yes`); `flib undo` reverts the whole run. A renamed
  or removed category needs `--rename-category OLD NEW` or `--remove-category
  OLD` (`--to-review` skips the AI). `--scrub-descriptions` removes
  identifiers from descriptions stored earlier, locally, also undoable.

**Status, summary and doctor**

- `flib status` (`--json`) shows what waits for you: review by reason and
  suggested category, failures and whether they retry, sensitive and locked
  files, the last and next run, and this month's cost. It names no file.
- After a scheduled run, if something waited `summary.min_age_days` (2), a
  summary goes to the log and a notification (counts only), at most every
  `summary.every_days` (7); `summary.notify: false` keeps only the log line.
- `flib doctor` writes a redacted report for bug reports (names, folders,
  secrets replaced): `--stdout`, `--output`, `--format markdown`, `--logs N`.

**Providers**

- `anthropic`: structured output with the categories as an enum; bad
  credentials or an unknown model stop the run before anything moves.
  `provider.effort` defaults to `low`; the example uses `claude-haiku-5-5`.
- `claude-code`: classifies through `claude -p` on your Claude subscription,
  no API key, sandboxed (no tools, no MCP, empty folder, allow-listed
  environment). `provider.command` sets the executable.
- `openai-compat`: any Chat Completions server at `provider.base_url`, key in
  `FLIB_OPENAI_COMPAT_API_KEY` (or `provider.api_key_env`) or the Keychain;
  `provider.price_input_per_mtok`/`price_output_per_mtok` feed the cost cap.
- `ollama`: a local model at `http://localhost:11434/v1`, no key; `flib
  check-config` checks the model is pulled. `fake` works offline.
- `flib key set|status|delete [--provider]` manages Keychain keys;
  `provider.api_key_source: auto` tries environment, Keychain, `env_file`.

**Vision**

- Opt-in (`vision.enabled`): images go as JPEG at most `vision.max_image_px`
  (1568) with metadata stripped; scanned PDFs send `vision.max_pdf_pages` (3).
  `openai-compat`/`ollama` need `provider.vision: true` (images only).

**MCP server**

- `flib mcp`, a local stdio server (optional `mcp` extra): `search_files`,
  `get_file` (with move history), `list_recent`, `list_review`, `get_stats`,
  `recategorize_file` and `undo`. No file content is returned; write tools
  take the lock per call; `mcp.read_only: true` hides them.

**Developer and CI**

- macOS CI on Python 3.11–3.15 (ruff, mypy, 93% branch coverage); real-volume
  tests with `FLIB_TEST_VOLUMES=1 uv run pytest -m volumes`; guides for adding
  an extractor or a provider, CONTRIBUTING.md and ADRs.

### Fixed

- `flib search --sum` with no words and no filter (for example `--sum --by
  kind`) adds up every filed document instead of refusing, and the message
  for a search with nothing to look for lists every filter (`--issuer`,
  `--kind`, the amounts, due dates, `--period`...).
- In every mode (#90): an item Finder is still copying in (type `brok`,
  creator `MACS`, creation date 24 January 1984) is skipped as a download in
  progress; an item whose modification time is more than five minutes in the
  future is taken once its size and mtime stayed the same for the quiet time
  (a daily run: at its next look) instead of "still changing" forever, with
  a note in the run log; right before a move, a file up to 32 MB is hashed
  again and left for the next run if its content changed. Today's and this
  month's start no longer shift by an hour on a daylight-saving day.
- `flib ui`, leftovers of the real-browser retest (#127):
  - A refused Open or Show in Finder (the file moved, went away, may run a
    program...) sends the browser back to the page it was on (Search with the
    same search, Due in the same window, Review), which says why: post,
    redirect, get, so Back and Forward no longer ask to "Confirm Form
    Resubmission". Asked, they still leave the page and its history as they
    are. Scripts (no `Accept: text/html`) get the status, as before.
  - Search's title and count say how many matched: "Search: 50 of 111
    matches", never just the limit; a search that only uses filters says its
    totals are over every match, not "by words"; "1 sensitive file also
    matched; it is never shown"; the index's and the search's messages from
    flib end with a full stop. In Portuguese too.
  - A renamed file's new name in Activity and the original's path in Search's
    "duplicate of" are isolated left to right too, like every other name.
  - A browser that reaches `flib ui` before its link was opened is told so
    ("This session has not started yet", and which page or link to use),
    not that the session ended.
  - After a theft of the launch link, every page says the session was ended
    because its link was opened twice, for a minute (Ctrl-C ends it at once),
    so the rightful tab learns why instead of finding the server gone; the
    thief gets that page and nothing else.
- `tests/unit/test_ui_pages.py::test_closing_does_not_wait_out_the_linger` no
  longer fails on busy runners (#134): End session asked the server to stop
  only after its reply, so a close right after it could take the reason's
  place. The server now asks before it replies.

### Security

- `flib ui`'s session cookie no longer reaches other servers on loopback
  (#125). Browsers keep cookies per host, not per port, so any program or
  account listening on another 127.0.0.1 port that got the browser to load
  one of its pages (a link, a redirect, an image) received the cookie, and
  with it could read pages and post actions from outside the browser. Each
  session is now served under a random name of its own,
  `flib-<32 hex>.localhost` (current macOS, Chrome and Firefox resolve
  `*.localhost` to loopback by themselves), whose host-only cookie the
  browser sends to that name only; the `Host` check is exact for it. The
  server holds `[::1]` on its port as well, since browsers try it first for
  such a name. The cookie is `flib_ui` with a `Max-Age` renewed by each
  request, so it ends a minute after the idle timer. The name stays off
  every command line and out of the database's folder (which may be on a
  volume that ignores ownership): it is kept in a private record in the
  account's own temporary folder, and a second `flib ui --open PAGE` (the
  menu bar's entries) takes it only from there, then opens a 0600 opener
  file that goes through `/open` (which, for the read-only pages, acts like
  `SameSite=Lax` for whoever knows the name). The terminal says the port
  only, except in the `--no-browser` link, which says the address is
  private until the session ends. A Mac whose resolver does not give loopback
  for `.localhost` gets the interface at `127.0.0.1` as before, with a
  warning on stderr saying what that leaves open. A real-browser test with
  a throwaway server on another port covers it.
- Descriptions, tags and proposed names keep personal identifiers out of the
  index (#82): the prompt asks the AI not to copy phone numbers, e-mail
  addresses, document, account or card numbers, payment keys, addresses or
  names of private people, and flib replaces what still looks like one with
  `[phone]`, `[email]`, `[id number]`... before the answer is cached or
  stored. `privacy.descriptions: verbatim` keeps the AI's text as written.
- Secret detection marks credential files and content with keys, tokens or
  passwords `sensitive`: nothing is sent and the value is never stored.
  Sensitive files show no description or tags in search or MCP.
- API keys go to the Keychain via stdin and are never printed;
  `check-config` warns about a `.env` other users can read. `openai-compat`
  and embeddings never follow redirects.
- The daily run uses `flib-python`, flib's own interpreter copy, so Full Disk
  Access covers flib only.
- The mover compares resolved, case- and Unicode-folded paths and never
  touches its own database, journal or library; replaced or changed items
  are not moved; an item you locked in Finder is never moved to another
  volume, where flib would have to delete the locked original.
- Strict configuration: safe folder names, inputs not `/`, home or inside
  `output_root`. Office XML refuses DTDs; config, logs and reports owner-only.
- Supply chain (ADR 0003): 7-day quarantine for new releases, Dependabot with
  cooldown, `uv audit`, actions pinned by SHA, zizmor.

[Unreleased]: https://github.com/luizpereiradev/file-librarian/commits/main
