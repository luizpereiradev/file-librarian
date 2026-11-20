"""Embeddings for semantic search (RF-15, issue #36). Opt in with ``search.semantic.enabled``.

- Where they come from: ``POST {search.semantic.base_url}/embeddings`` on any
  OpenAI-compatible server, Ollama on this Mac by default. It is configured
  apart from the classification provider, through the same HTTP client
  (``providers/http.py``: redirects refused, no proxy for loopback, size cap).
  Anthropic has no embeddings endpoint.
- What is sent: what the index already stores about a file (its names,
  category, tags and the model's description), never its content. A sensitive
  file, or one whose name looks sensitive or matches ``never_send``, is never
  sent and has no vector. The search query itself is sent too.
- Key: optional, from ``search.semantic.api_key_env`` (else
  ``FLIB_EMBEDDINGS_API_KEY``) or the Keychain account ``embeddings-api-key``;
  never the provider's key.
- Prefixes: some models embed a query and a document differently when told which
  is which. ``embeddinggemma`` (its model card) wants ``task: search result |
  query: `` before a query and ``title: none | text: `` before a document; on a
  synthetic bilingual corpus they lowered unrelated pairs' similarity from a
  median of 0.22 to 0.08 and put the right file first for every query. Other
  models get none unless ``search.semantic.query_prefix`` and
  ``document_prefix`` say so.
- Storage: one float32 vector per file in ``embeddings``, normalized so cosine
  similarity is a dot product, with the model and a hash of the embedded text
  (prefix included); a different model or text means the vector is stale and is
  made again.
- When: after each run (``refresh``), for new and changed rows, and with
  ``flib search --reindex``. A failure never fails a run: what was not embedded
  is retried the next time.
- Ranking: brute force over every vector in pure Python, no extension and no
  numpy (``math.sumprod`` on Python 3.12+). Reading and ranking take about
  0.1 s for 10,000 files and 0.6 s for 50,000 (768 dimensions, Apple silicon),
  so about a second per 100,000 files: the practical limit of this approach,
  far above a personal Downloads library.
"""

from __future__ import annotations

import hashlib
import heapq
import math
import operator
import sys
import urllib.error
import urllib.request
from array import array
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from file_librarian import credentials
from file_librarian.config import Config, SemanticConfig, is_loopback_url
from file_librarian.db.store import FileRow, Store
from file_librarian.models import FileStatus
from file_librarian.providers.base import ProviderError
from file_librarian.providers.http import JsonClient, error_detail
from file_librarian.sensitive import sensitive_name, withheld_by_config

MAX_TEXT_CHARS = 2000
"""An index row's text is short; this only bounds a pathological description."""

_sumprod: Callable[[Iterable[float], Iterable[float]], float] | None = getattr(
    math, "sumprod", None
)  # Python 3.12+


class EmbeddingError(Exception):
    """The embeddings server could not be used; the message says why."""


class Embedder:
    """``POST {base_url}/embeddings`` for a batch of texts."""

    label = "the embeddings server"

    def __init__(
        self, config: SemanticConfig, opener: urllib.request.OpenerDirector | None = None
    ) -> None:
        self.model = config.model
        self.base_url = config.base_url
        self.local = is_loopback_url(config.base_url)
        self._http = JsonClient(
            config.base_url,
            label=self.label,
            key=credentials.resolve_embeddings(config).value,
            timeout=config.timeout_seconds,
            opener=opener,
        )

    def embed(self, texts: Sequence[str]) -> list[array[float]]:
        """One normalized vector per text, in order."""
        try:
            payload = self._http.request(
                "POST", "/embeddings", {"model": self.model, "input": list(texts)}
            )
        except urllib.error.HTTPError as exc:
            raise EmbeddingError(self._status_message(exc)) from None
        except ProviderError as exc:
            raise EmbeddingError(str(exc)) from None
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingError(f"{self.label} did not return one embedding per text")
        ordered = sorted(
            (item for item in data if isinstance(item, dict)),
            key=lambda item: item.get("index", 0) if isinstance(item.get("index"), int) else 0,
        )
        vectors = [_vector(item.get("embedding")) for item in ordered]
        if len(vectors) != len(texts) or any(v is None for v in vectors):
            raise EmbeddingError(f"{self.label} returned a malformed embedding")
        dims = {len(v) for v in vectors if v is not None}
        if len(dims) != 1:
            raise EmbeddingError(f"{self.label} returned embeddings of different sizes")
        return [v for v in vectors if v is not None]

    def _status_message(self, exc: urllib.error.HTTPError) -> str:
        status = exc.code
        if 300 <= status < 400:
            return (
                f"{self.label} redirected the request (HTTP {status}); redirects are not "
                "followed. Set search.semantic.base_url to the final address."
            )
        if status in (401, 403):
            return (
                f"{self.label} rejected the credentials (HTTP {status}); run "
                "`flib key set --provider embeddings`, or set "
                f"{credentials.EMBEDDINGS_ENV_VAR}"
            )
        if status == 404:
            return (
                f"{self.label} answered 404: check search.semantic.base_url ({self.base_url}) "
                f"and that model {self.model!r} exists (Ollama: `ollama pull {self.model}`)"
                f"{error_detail(exc)}"
            )
        return f"{self.label} answered HTTP {status}{error_detail(exc)}"[:300]


def _vector(value: object) -> array[float] | None:
    """A normalized float32 vector, or ``None`` if ``value`` is not a usable embedding."""
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(x, int | float) and not isinstance(x, bool) for x in value):
        return None
    norm = math.sqrt(math.fsum(float(x) * float(x) for x in value))
    if not math.isfinite(norm) or norm == 0:
        return None
    return array("f", (float(x) / norm for x in value))


def pack(vector: array[float]) -> bytes:
    """float32, little-endian, whatever this machine's byte order."""
    if sys.byteorder != "little":  # pragma: no cover - every supported Mac is little-endian
        vector = array("f", vector)
        vector.byteswap()
    return vector.tobytes()


def unpack(blob: bytes) -> array[float]:
    vector = array("f")
    vector.frombytes(blob)
    if sys.byteorder != "little":  # pragma: no cover
        vector.byteswap()
    return vector


def dot(a: array[float], b: array[float]) -> float:
    """Cosine similarity of two normalized vectors."""
    if _sumprod is not None:
        return _sumprod(a, b)
    return float(sum(map(operator.mul, a, b)))  # Python 3.11


def similarities(query: array[float], vectors: Iterable[tuple[int, bytes]]) -> dict[int, float]:
    """``file_id -> cosine similarity`` to ``query``, for every vector."""
    return {file_id: dot(query, unpack(blob)) for file_id, blob in vectors}


def nearest(
    query: array[float], vectors: Iterable[tuple[int, bytes]], *, limit: int
) -> list[tuple[int, float]]:
    """The ``limit`` most similar ``(file_id, similarity)``, best first."""
    scored = similarities(query, vectors)
    return [(i, scored[i]) for i in heapq.nlargest(limit, scored, key=scored.__getitem__)]


# --- what is embedded ---------------------------------------------------------------------


def embeddable(row: FileRow, config: Config) -> bool:
    """Never a sensitive file, nor one whose name looks sensitive or matches ``never_send``,
    nor a duplicate (RF-21): its original is embedded already."""
    if row.status is FileStatus.SENSITIVE or row.duplicate_of is not None:
        return False
    names = {row.original_name, row.current_name}
    return not any(sensitive_name(n) or withheld_by_config(n, config.never_send) for n in names)


def row_text(row: FileRow) -> str:
    """What the index stores about ``row``, as one text: never the file's content."""
    lines = [f"name: {row.current_name}"]
    if row.original_name != row.current_name:
        lines.append(f"original name: {row.original_name}")
    if row.category:
        lines.append(f"category: {row.category}")
    if row.tags:
        lines.append(f"tags: {', '.join(row.tags)}")
    if row.description:
        lines.append(f"description: {row.description}")
    return "\n".join(lines)[:MAX_TEXT_CHARS]


KNOWN_PREFIXES: dict[str, tuple[str, str]] = {
    "embeddinggemma": ("task: search result | query: ", "title: none | text: "),
}
"""``(query prefix, document prefix)`` a model family's documentation asks for."""


def prefixes(settings: SemanticConfig) -> tuple[str, str]:
    """``(query prefix, document prefix)`` for ``settings.model``: the configured ones,
    else the ones its family documents (``embeddinggemma:300m`` is ``embeddinggemma``)."""
    family = settings.model.lower().rsplit("/", 1)[-1].split(":", 1)[0]
    known = next(
        (pair for name, pair in KNOWN_PREFIXES.items() if family.startswith(name)), ("", "")
    )
    query = known[0] if settings.query_prefix is None else settings.query_prefix
    document = known[1] if settings.document_prefix is None else settings.document_prefix
    return query, document


def query_text(text: str, settings: SemanticConfig) -> str:
    """What is embedded for a search query."""
    return prefixes(settings)[0] + text.strip()[:MAX_TEXT_CHARS]


def document_text(row: FileRow, settings: SemanticConfig) -> str:
    """What is embedded for ``row``: its index text after the model's document prefix."""
    return prefixes(settings)[1] + row_text(row)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _outdated(
    store: Store, config: Config, *, full: bool = False
) -> tuple[list[tuple[int, str, str]], list[int]]:
    """``(file_id, text, hash)`` of each file to embed, and the vectors to delete.

    A file is embedded again when it has no vector, or one of another model or
    of another text (its row changed, or the prefix did); ``full`` takes them all.
    """
    semantic = config.search.semantic
    keys = store.embedding_keys()
    todo: list[tuple[int, str, str]] = []
    drop: list[int] = []
    for row in store.all_files():
        if not embeddable(row, config):
            if row.id in keys:
                drop.append(row.id)
            continue
        text = document_text(row, semantic)
        digest = text_hash(text)
        if full or keys.get(row.id) != (semantic.model, digest):
            todo.append((row.id, text, digest))
    return todo, drop


def outdated_count(store: Store, config: Config) -> int:
    """Files that may be embedded but have no up-to-date vector yet."""
    return len(_outdated(store, config)[0])


@dataclass(slots=True)
class RefreshReport:
    embedded: int = 0
    removed: int = 0
    """Vectors deleted because their file may no longer be embedded (sensitive, never_send)."""
    pending: int = 0
    """Files still without an up-to-date vector (after an error)."""
    error: str | None = None


def refresh(
    store: Store, config: Config, embedder: Embedder | None = None, *, full: bool = False
) -> RefreshReport:
    """Embed every file whose vector is missing or stale; ``full`` re-embeds all of them.

    Never raises for a server problem: the report carries the error, and what
    was not embedded is picked up next time.
    """
    semantic = config.search.semantic
    report = RefreshReport()
    todo, drop = _outdated(store, config, full=full)
    if drop:
        report.removed = store.delete_embeddings(drop)
    if not todo:
        return report
    embedder = embedder or Embedder(semantic)
    for start in range(0, len(todo), semantic.batch_size):
        batch = todo[start : start + semantic.batch_size]
        try:
            vectors = embedder.embed([text for _, text, _ in batch])
        except EmbeddingError as exc:
            report.error = str(exc)
            report.pending = len(todo) - start
            return report
        store.save_embeddings(
            [
                (file_id, semantic.model, len(vector), digest, pack(vector))
                for (file_id, _, digest), vector in zip(batch, vectors, strict=True)
            ]
        )
        report.embedded += len(batch)
    return report
