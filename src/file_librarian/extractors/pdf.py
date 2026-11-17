"""PDF text (RF-04, RF-27).

Uses pypdf, a pure-Python parser. At most ``MAX_PAGES`` pages are read, and
reading stops once ``max_chars`` are collected. A PDF that needs a password
raises ``ExtractionError``; a scanned PDF with no text layer returns ``None``
(with ``vision.enabled``, its first pages go to the model instead: see
``extractors/visual.py``, RF-26).
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PyPdfError

from file_librarian.extractors import register
from file_librarian.extractors.base import ExtractionError

MAX_PAGES = 20

# pypdf logs every quirk of malformed files as a warning; they are expected here.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class PdfExtractor:
    extensions = frozenset({".pdf"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        try:
            reader = PdfReader(path, strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractionError("password-protected PDF")
            pieces: list[str] = []
            collected = 0
            for page in reader.pages[:MAX_PAGES]:
                text = page.extract_text() or ""
                pieces.append(text)
                collected += len(text)
                if collected >= max_chars:
                    break
        except ExtractionError:
            raise
        except (PdfReadError, PyPdfError, OSError, ValueError, KeyError, TypeError) as exc:
            raise ExtractionError(f"cannot read PDF: {exc}") from exc
        except Exception as exc:  # pypdf can raise almost anything on hostile input
            raise ExtractionError(f"cannot read PDF ({type(exc).__name__})") from exc
        text = "\n".join(p.strip() for p in pieces if p.strip())
        return text[:max_chars] or None


register(PdfExtractor())
