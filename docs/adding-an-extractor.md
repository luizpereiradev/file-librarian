# Adding an extractor

An extractor turns one file format into plain text for the classifier (the
pipeline stage that calls the AI provider; it lands in M1).

## 1. Write the module

Create `src/file_librarian/extractors/<format>.py`:

```python
from pathlib import Path

from file_librarian.extractors import register
from file_librarian.extractors.base import ExtractionError


class MarkdownExtractor:
    extensions = frozenset({".md", ".markdown"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                text = fh.read(max_chars)
        except OSError as exc:
            raise ExtractionError(str(exc)) from exc
        return text.strip() or None


register(MarkdownExtractor())
```

Rules for every extractor:

- **Read at most what you need.** Stop once you have `max_chars` characters;
  never load a large file whole.
- **Never crash the run.** Encrypted, damaged or legacy files raise
  `ExtractionError`; the file is then classified from metadata (RF-27).
- **Return `None`** when there is no readable text.
- **Never write** to the file, its metadata or its folder.
- Prefer the standard library. A new dependency needs a reason in the pull request.

## 2. Register it

Append the module name to `BUILTIN` in `extractors/__init__.py`, so it is
imported on the first lookup. Keep the names that are already there:

```python
BUILTIN: tuple[str, ...] = (..., "markdown")
```

## 3. Test it

Generate a small synthetic sample in the test itself (or add a tiny, obviously
fake file under `tests/fixtures/`) and cover: normal text, the `max_chars`
cap, an empty file, and a corrupted file raising `ExtractionError`.

## 4. Document it

Add the format to the README and to `CHANGELOG.md` under `Unreleased`.
