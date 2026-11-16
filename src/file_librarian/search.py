"""Full-text search over the index (RF-13, RF-35).

The user's words are never passed to FTS5 as query syntax: each word becomes
a quoted prefix term, so ``AND``, ``NEAR``, ``*`` or quotes in a query are just
text. All words must match. Accents are ignored (the index tokenizer removes
diacritics), so ``orcamento`` finds ``orçamento``. Results are ranked by BM25,
with names weighted above descriptions.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass

from file_librarian.db.store import Store

# Weights per indexed column: original_name, current_name, description, tags.
_BM25 = "bm25(files_fts, 3.0, 3.0, 1.0, 2.0)"


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    original_name: str
    category: str | None
    description: str | None
    tags: list[str]
    status: str
    processed_at: str
    exists: bool


def fts_query(text: str) -> str | None:
    """Turn free text into a safe FTS5 query, or ``None`` if it has no words."""
    words = re.findall(r"\w+", unicodedata.normalize("NFC", text))
    if not words:
        return None
    return " ".join('"' + w.replace('"', '""') + '"*' for w in words[:20])


def search(store: Store, text: str, *, limit: int = 20) -> list[SearchHit]:
    query = fts_query(text)
    if query is None:
        return []
    rows = store.conn.execute(
        f"""SELECT f.* FROM files_fts JOIN files f ON f.id = files_fts.rowid
            WHERE files_fts MATCH ? ORDER BY {_BM25} LIMIT ?""",  # noqa: S608 - constant
        (query, limit),
    ).fetchall()
    hits = []
    for row in rows:
        try:
            tags = [str(t) for t in json.loads(row["tags"] or "[]")]
        except (ValueError, TypeError):
            tags = []
        hits.append(
            SearchHit(
                path=row["current_path"],
                original_name=row["original_name"],
                category=row["category"],
                description=row["description"],
                tags=tags,
                status=row["status"],
                processed_at=row["processed_at"],
                exists=os.path.lexists(row["current_path"]),
            )
        )
    return hits


def to_json(hits: list[SearchHit]) -> str:
    return json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=2)
