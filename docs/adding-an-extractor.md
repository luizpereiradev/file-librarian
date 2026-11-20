# Adding an extractor

An extractor turns one file format into plain text. It is the only
format-specific code in flib: everything else (secret detection, what is sent,
naming, moving) is the same for every format. `extractors/mail.py` (`.eml`) is
a complete, recent example to read alongside this guide.

## Where it runs

`context.py` builds what the provider may see for each file (folder units
and `.app` bundles never reach an extractor), in this order:

1. a sensitive name (`id_rsa`, `*.pem`...) or a `never_send` match: the
   extractor is **not** called;
2. `extractor.extract(path, SCAN_WINDOW)`: note the limit is `SCAN_WINDOW`
   (256,000 characters), not `provider.max_chars_per_file`;
3. the secret scan (`sensitive.find_secrets`, on unless
   `sensitive.detect_secrets` is turned off, like the name check in step 1)
   reads everything you returned.
   A hit marks the file sensitive: nothing is sent and it stays where it is;
4. only then is the text cut to `provider.max_chars_per_file` (4,000 by
   default) and sent.

Two consequences:

- **What you return is what is scanned.** Do not rewrite text in a way that
  hides secrets (masking `user:password@host` as an e-mail address, say), and
  put the most telling text first, because only the start is sent.
- **A new extractor changes what leaves the Mac.** A format with no extractor
  is sent as metadata only (small files are read for the secret scan, never
  sent). Decide what of the new format is personal data and should never be
  returned at all, write it in the module docstring, and describe it in the
  README's Privacy section. `mail.py`, for example, keeps only the domain of
  every address in the headers.

## 1. Write the module

Create `src/file_librarian/extractors/<format>.py`. Do not name it after a
standard-library module you import (`email.py`, `html.py`), to keep imports
unambiguous. Check the extensions are not taken (`grep -rn '"\.ext"'
src/file_librarian/extractors/`): the last registration silently wins.

```python
"""Rich Text Format (``.rtf``): its text, roughly, without control words (RF-04)."""

from __future__ import annotations

import re
from pathlib import Path

from file_librarian.extractors import register
from file_librarian.extractors.base import ExtractionError

MAX_BYTES = 4 * 1024 * 1024
_CONTROL = re.compile(rb"\\[a-z]+-?\d* ?|\\'[0-9a-f]{2}|[{}]")


class RtfExtractor:
    extensions = frozenset({".rtf"})  # lower case, with the dot

    def extract(self, path: Path, max_chars: int) -> str | None:
        try:
            with path.open("rb") as fh:
                raw = fh.read(min(MAX_BYTES, max_chars * 8))
        except OSError as exc:
            raise ExtractionError(str(exc)) from exc
        if not raw.startswith(b"{\\rtf"):
            raise ExtractionError("not an RTF file")
        text = _CONTROL.sub(b"", raw).decode("cp1252", errors="replace")
        return text[:max_chars].strip() or None


register(RtfExtractor())
```

The contract (`extractors/base.py`):

- **Return at most `max_chars` characters**, or `None` when there is no
  readable text (an empty file, a scan with no text layer).
- **Raise only `ExtractionError`** for an unreadable, damaged, encrypted or
  legacy file: catch `OSError` and every exception your parser can raise on
  bad input, and re-raise it as `ExtractionError`. The file is then
  classified from its metadata (RF-27). `context.py` also catches anything
  else, but only as a backstop: the fuzz test below fails on it.
- **Cap the work, not only the output.** Read only what you need. A format
  that must be parsed whole needs a byte cap (`MAX_*` constants at the top of
  the module, like `MAX_MESSAGE_BYTES` in `mail.py`, `MAX_PART_BYTES` in
  `office.py`, `MAX_TAR_BYTES` in `archive.py`), and nested or repeated
  structures need a count cap (parts, entries, pages). A file in Downloads is
  untrusted: think of zip bombs, XML entities (refuse a DTD, as `office.py`
  does) and nesting deep enough to raise `RecursionError`.
- **Never write** to the file, its metadata or its folder, and never extract
  an embedded file to disk (archives are listed, not unpacked).
- **No new dependency** unless the standard library cannot do it; the bar is
  in CONTRIBUTING.md ("Dependencies").

## 2. Register it

Append the module name to `BUILTIN` in `extractors/__init__.py`, keeping the
ones already there. The modules are imported on the first lookup:

```python
BUILTIN: tuple[str, ...] = (..., "rtf")
```

## 3. Add metadata (optional)

An extractor returns text only. Anything else the pipeline learns about a file
lives in `FileContext.metadata`, a frozen `FileMetadata` dataclass in
`models.py`, filled in by `context.py`. To record something new, say a page
count:

1. add a typed, documented field defaulting to `None` to `FileMetadata`;
2. set it where `context.py` builds the `FileMetadata` for the file;
3. it stays local unless `user_message` in `providers/prompt.py` sends it. To
   send it, add it there, bump `PROMPT_VERSION` in `classifier.py` (cached
   answers were given without it) and update the golden strings in
   `tests/unit/test_prompt_payload.py`, which pin the exact text sent.

## 4. Test it

Tests are offline and synthetic (CONTRIBUTING.md, "Writing tests"):

- **A sample builder.** Generate the file in code with a `make_<format>`
  function in `tests/_helpers.py` (next to `make_docx`, `make_pdf`,
  `make_eml`), because the extractor and fuzz tests both use it. Use made-up
  names and `.example` domains; never a real document.
- **Behaviour**, in `tests/unit/test_extractors.py` through its `extract`
  helper: normal text, the `max_chars` cap, an empty file returning `None`,
  each cap of step 1 (lower it with `monkeypatch.setattr(module, "MAX_...",
  small)`), a damaged file and an unreadable path (a folder named like the
  file) raising `ExtractionError`, and, if you drop personal data, that it
  is not in the output.
- **Fuzzing.** Add your format to `BUILDERS` in
  `tests/unit/test_damaged_files.py`, with a sample large enough to have
  every structure your parser walks. Its two tests then corrupt and truncate
  it 150 times and check the extractor raises only `ExtractionError` and
  that building a context never raises.
- **The pipeline**, in `tests/unit/test_context.py` with `context_for(path)`:
  a secret in the content still marks the file sensitive (use the fake
  values in `FAKE`), and what you chose not to send is not in `ctx.text`.

Run `uv run coverage report -m`: every `except` and cap in your module should
be covered, or be gone.

## 5. Document it

- **README.md:** a row in the table under "What flib reads", and, if the
  format sends something new, the Privacy section.
- **CHANGELOG.md:** one entry at the top of `### Added` under
  `## [Unreleased]`, saying what is read and what is never sent.
- **The module docstring:** what is returned, what is left out and why, and
  the caps.

## Checklist for the pull request

- [ ] `extractors/<format>.py` with a docstring, registered in `BUILTIN`
- [ ] only `ExtractionError` escapes; byte and count caps on untrusted input
- [ ] `make_<format>` in `tests/_helpers.py`; behaviour tests; a `BUILDERS`
      entry in `test_damaged_files.py`; a secret-scan test in `test_context.py`
- [ ] README table (and Privacy, if needed) and CHANGELOG
- [ ] the four checks in CONTRIBUTING.md pass, coverage at or above 93%
