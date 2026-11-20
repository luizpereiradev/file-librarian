"""An in-process, OpenAI-compatible HTTP server on 127.0.0.1 for provider tests.

It answers ``POST`` requests from a script: each takes the next ``Reply`` (the
last one repeats), and every request is recorded with its method, path, headers
and JSON body. ``GET /v1/models`` lists ``models``, or sends ``models_reply``.
``POST /v1/embeddings`` answers with ``embedding`` of each input (a bag of words
over ``SYNONYMS``, so a synonym lands on the same direction), or sends
``embeddings_reply``. Tests that use it also need the ``loopback_network`` fixture.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Reply:
    status: int = 200
    body: Any = None
    """A dict is sent as JSON, bytes as they are."""
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


def completion(content: Any, *, tokens: tuple[int, int] = (100, 20), **choice: Any) -> Reply:
    """A Chat Completions answer whose message content is ``content`` (JSON-encoded if a dict)."""
    text = json.dumps(content) if isinstance(content, dict) else content
    return Reply(
        body={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "test-model",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": text}}
                | {"finish_reason": "stop"}
                | choice
            ],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]},
        }
    )


EMBEDDING_DIMENSIONS = 64
SYNONYMS = {"bill": "invoice", "receipt": "invoice", "statement": "invoice", "photo": "picture"}
"""Words the fake embedding model treats as one meaning."""


def embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """A deterministic vector: one count per word, the word's synonyms folded together.

    Labels such as ``name:`` or ``tags:`` are left out, so only the content counts.
    """
    vector = [0.0] * dimensions
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in {"name", "original", "category", "tags", "description"}:
            continue
        word = SYNONYMS.get(word, word)
        slot = int(hashlib.sha256(word.encode()).hexdigest(), 16) % dimensions
        vector[slot] += 1.0
    return vector


def error(status: int, message: str = "nope") -> Reply:
    return Reply(status, {"error": {"message": message, "type": "invalid_request_error"}})


class FakeOpenAI:
    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.requests: list[Seen] = []
        self.models: list[str] = ["test-model"]
        self.models_reply: Reply | None = None
        self.embeddings_reply: Reply | None = None
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}/v1"

    def script(self, *replies: Reply) -> None:
        self.replies = list(replies)

    def _next(self) -> Reply | None:
        with self._lock:
            if not self.replies:
                return None
            return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.requests.append(Seen(handler.command, handler.path, dict(handler.headers), body))
        reply: Reply | None
        if handler.command == "GET" and handler.path.endswith("/models"):
            listing = {"object": "list", "data": [{"id": m} for m in self.models]}
            reply = self.models_reply or Reply(body=listing)
        elif handler.command == "POST" and handler.path.endswith("/embeddings"):
            reply = self.embeddings_reply or self._embeddings(body)
        else:
            reply = self._next()
        reply = reply or Reply(500, {"error": {"message": "no reply scripted"}})
        if reply.delay:
            time.sleep(reply.delay)
        data = reply.body if isinstance(reply.body, bytes) else json.dumps(reply.body).encode()
        try:
            handler.send_response(reply.status)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(data)))
            for name, value in reply.headers.items():
                handler.send_header(name, value)
            handler.end_headers()
            handler.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (a timeout test)

    def embedded_texts(self) -> list[str]:
        """Every text sent to ``/embeddings`` so far, in order."""
        return [
            text
            for seen in self.requests
            if seen.path.endswith("/embeddings") and isinstance(seen.body, dict)
            for text in seen.body.get("input", [])
        ]

    @staticmethod
    def _embeddings(body: Any) -> Reply:
        texts = body.get("input", []) if isinstance(body, dict) else []
        texts = [texts] if isinstance(texts, str) else texts
        data = [
            {"object": "embedding", "index": i, "embedding": embedding(str(t))}
            for i, t in enumerate(texts)
        ]
        model = body.get("model") if isinstance(body, dict) else None
        return Reply(body={"object": "list", "data": data, "model": model})

    @contextmanager
    def running(self) -> Iterator[FakeOpenAI]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake._handle(self)

            def do_POST(self) -> None:
                fake._handle(self)

            def log_message(self, *_args: Any) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()
        try:
            yield self
        finally:
            self._server.shutdown()
            self._server.server_close()
            thread.join(timeout=5)
