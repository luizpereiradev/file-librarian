"""Shared test helpers: plain functions and classes. Import from here.

Fixtures, including the autouse safety guards, live in ``tests/conftest.py``;
the fake ``security``, ``launchctl``, ``sips`` and ``claude`` binaries are the
``tests/fake_*.py`` scripts those fixtures install. A helper used by more than
one test module belongs here, never in a test module another one imports.
"""

from __future__ import annotations

import base64
import builtins
import ctypes
import ctypes.util
import errno
import html
import http.client
import json
import os
import plistlib
import re
import socket
import sqlite3
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode, urlsplit

import pytest

from file_librarian import pipeline
from file_librarian.config import Config, parse_config
from file_librarian.context import build_context
from file_librarian.db.store import FileRow, Store
from file_librarian.evals.documents import make_docx as make_docx
from file_librarian.evals.documents import make_eml as make_eml
from file_librarian.evals.documents import make_pdf as make_pdf
from file_librarian.evals.documents import make_pptx as make_pptx
from file_librarian.evals.documents import make_xlsx as make_xlsx
from file_librarian.finder_tags import USER_TAGS
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.models import Classification, FileContext
from file_librarian.providers.fake import FakeProvider
from file_librarian.scanner import Candidate

REPO_ROOT = Path(__file__).resolve().parent.parent

# A modification time well past `stable_seconds`, shared by every test module.
# On APFS, setting it with os.utime also moves the file's birth time, which flib
# uses as the download date: derive expected dates from OLD_DATE, not today.
OLD = time.time() - 3 * 3600
OLD_DATE = date.fromtimestamp(OLD)

_XATTR_NOFOLLOW = 0x0001


class Crash(BaseException):
    """Stands in for a power cut: not an Exception, so nothing in flib catches it."""


# --- Files in the fake Downloads ---------------------------------------------------


def make_old(path: Path) -> Path:
    """Backdate ``path`` to OLD, so the scanner sees it as stable."""
    os.utime(path, (OLD, OLD))
    return path


def put(folder: Path, name: str, content: bytes = b"data", *, mtime: float = OLD) -> Path:
    """Write ``folder/name`` (creating parents), modified at ``mtime``."""
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))
    return path


def write_xattr(path: Path, name: str, value: bytes) -> None:
    """Set an extended attribute on a synthetic test file (macOS only)."""
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.setxattr.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    lib.setxattr.restype = ctypes.c_int
    rc = lib.setxattr(os.fsencode(path), name.encode(), value, len(value), 0, _XATTR_NOFOLLOW)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(path))


def set_tags(path: Path, tags: list[str]) -> None:
    """Give ``path`` these Finder tags, the way Finder stores them."""
    write_xattr(path, USER_TAGS, plistlib.dumps(tags, fmt=plistlib.FMT_BINARY))


# --- Configuration, runs and the index -------------------------------------------


def fake_config(downloads: Path | None = None, /, **overrides: Any) -> Config:
    """A config for the offline fake provider, watching ``downloads`` if given.

    ``provider`` overrides are merged into the fake provider's settings; every
    other key replaces the default.
    """
    provider = {"name": "fake", "model": "x"} | overrides.pop("provider", {})
    raw: dict[str, Any] = {"provider": provider}
    if downloads is not None:
        raw["inputs"] = [str(downloads)]
    return parse_config(raw | overrides)


def write_config(
    folder: Path, downloads: Path, provider: str = "{name: fake, model: x}", extra: str = ""
) -> Path:
    """A config file in ``folder`` for ``flib -c``; a second call writes a second file."""
    taken = len(list(folder.glob("config*.yaml")))
    path = folder / ("config.yaml" if not taken else f"config{taken}.yaml")
    path.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {provider}\n{extra}",
        encoding="utf-8",
    )
    return path


def answer(
    category: str = "Finance",
    name: str = "x",
    confidence: float = 0.75,
    *,
    description: str = "d",
    **kw: Any,
) -> Classification:
    """A fake provider's answer; by default below the rename threshold, so names stay."""
    return Classification(
        category=category,
        suggested_name=name,
        description=description,
        confidence=confidence,
        **kw,
    )


def gold_answer(item: Any, language: str = "en") -> Classification:
    """What a perfect model answers for an evaluation corpus item (``corpus.Item``): its
    first acceptable category (or review), and the structured fields of its gold (#88)."""
    from file_librarian.evals.score import gold_kinds

    gold = item.gold
    fields = gold.fields
    cats = gold.categories[language]
    return Classification(
        category=cats[0] if cats else "Other",
        suggested_name="named by the oracle",
        description="d",
        confidence=0.4 if gold.review else 0.95,
        name_choice="original" if gold.keep_name else "proposed",
        kind=gold_kinds(fields["kind"])[0] if "kind" in fields else None,
        issuer=fields.get("issuer"),
        amount=fields.get("amount"),
        currency=fields.get("currency"),
        document_date=fields.get("document_date"),
        due_date=fields.get("due_date"),
        period=fields.get("period"),
    )


def library(downloads: Path) -> Path:
    """Where organized files land by default."""
    return downloads / "Organized"


def organize(
    downloads: Path, cfg: Config, files: dict[str, tuple[bytes, Classification | str]]
) -> None:
    """Drop ``files`` into Downloads and run the pipeline for real.

    Each value is the content and the answer, or just a category for ``answer(category)``.
    """
    answers: dict[str, Classification] = {}
    for name, (content, reply) in files.items():
        put(downloads, name, content)
        answers[name] = answer(reply) if isinstance(reply, str) else reply
    pipeline.run(cfg, FakeProvider(answers), simulate=False)


def row_for(cfg: Config, original: str) -> FileRow:
    """The index row of the file first seen as ``original``."""
    with Store.open(cfg.database) as store:
        return next(r for r in store.all_files() if r.original_name == original)


def rows(cfg: Config, sql: str) -> list[sqlite3.Row]:
    with Store.open(cfg.database) as store:
        return list(store.conn.execute(sql))


def candidate(path: Path) -> Candidate:
    """A scanner candidate for ``path``, a file or a folder unit."""
    if path.is_dir():
        m = unit_manifest(path)
        return Candidate(path, m.digest, m.size_bytes, 0.0, is_unit=True)
    return Candidate(path, file_sha256(path), path.stat().st_size, 0.0, is_unit=False)


def context_for(path: Path, cfg: Config | None = None) -> FileContext:
    """The context flib would build for ``path``."""
    return build_context(candidate(path), cfg or fake_config())


# --- iCloud ----------------------------------------------------------------------


def mark_cloud_only(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> set[Path]:
    """Real dataless files cannot be made in a test: report these inodes as cloud-only.

    Any read of them, or of anything inside a cloud-only folder (hashing, opening,
    listing), fails the test.
    """
    from file_librarian import cli, hashing, scanner

    inodes = {os.lstat(p).st_ino for p in paths}
    real_is_dataless = hashing.is_dataless
    monkeypatch.setattr(
        hashing, "is_dataless", lambda st: st.st_ino in inodes or real_is_dataless(st)
    )

    def touched(path: object) -> bool:
        if not isinstance(path, str | bytes | os.PathLike):
            return False  # a file descriptor
        target = Path(os.fsdecode(path)).absolute()
        return any(target == p or p in target.parents for p in paths)

    def guard(what: str, real: Any) -> Any:
        def guarded(path: Any, *args: Any, **kwargs: Any) -> Any:
            assert not touched(path), f"{what} a cloud-only item: {path}"
            return real(path, *args, **kwargs)

        return guarded

    real_sha = hashing.file_sha256
    for module in (hashing, scanner, cli):
        monkeypatch.setattr(module, "file_sha256", guard("hashed", real_sha))
    monkeypatch.setattr(Path, "open", guard("opened", Path.open))
    monkeypatch.setattr(builtins, "open", guard("opened", builtins.open))
    monkeypatch.setattr(os, "walk", guard("walked", os.walk))
    monkeypatch.setattr(os, "scandir", guard("listed", os.scandir))
    monkeypatch.setattr(os, "listdir", guard("listed", os.listdir))
    return set(paths)


# --- macOS Data Protection ------------------------------------------------------


class FakeProtection:
    """Stand-in for Data Protection classes and the screen lock (``tests/conftest.py``).

    Every file is class C (3) and the screen unlocked unless a test says
    otherwise. ``protect`` gives files class A (or B): while the fake screen
    is locked, opening them fails with ``EPERM``, as macOS does.
    ``lock_screen(unlock_after=N)`` keeps it locked for ``N`` more questions
    to ``screen_locked``, then unlocks it; without it, it stays locked.
    """

    def __init__(self) -> None:
        self.classes: dict[Path, int] = {}
        self.locked: bool | None = False
        self.queue: list[bool | None] = []
        self.polls = 0
        self._guarded = False

    def protection_class(self, path: Path) -> int | None:
        return self.classes.get(Path(path).absolute(), 3)

    def screen_locked(self) -> bool | None:
        self.polls += 1
        if self.queue:
            self.locked = self.queue.pop(0)
        return self.locked

    def lock_screen(self, *, unlock_after: int | None = None) -> None:
        self.locked = True
        self.queue = []
        if unlock_after is not None:
            self.queue.extend([True] * unlock_after)
            self.queue.append(False)

    def protect(self, monkeypatch: pytest.MonkeyPatch, *paths: Path, cls: int = 1) -> None:
        for path in paths:
            self.classes[path.absolute()] = cls
        if self._guarded:
            return
        self._guarded = True
        real_open = Path.open

        def guarded(path: Path, *args: Any, **kwargs: Any) -> Any:
            if self.locked and self.classes.get(path.absolute(), 3) in (1, 2):
                raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), str(path))
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", guarded)


# --- Synthetic documents ---------------------------------------------------------


def make_image(path: Path, width: int, height: int) -> Path:
    """A synthetic image only the fake sips (tests/fake_sips.py) can read."""
    path.write_bytes(f"FAKEIMG {width} {height}\n".encode() + b"\0" * 64)
    return path


def make_scan(path: Path, text: str, *, width: int = 1200, height: int = 1600) -> Path:
    """An image (or, for ``.pdf``, a PDF with no text layer) whose "printed" text
    only the fake OCR helper (tests/fake_ocr.py) can read."""
    marker = b"\nFAKEOCR:" + base64.b64encode(text.encode("utf-8")) + b"\n"
    if path.suffix.lower() == ".pdf":
        make_blank_pdf(path, 1)
        path.write_bytes(path.read_bytes() + b"%" + marker[1:])
    else:
        make_image(path, width, height)
        path.write_bytes(path.read_bytes() + marker)
    return path


def make_blank_pdf(path: Path, pages: int) -> Path:
    """A PDF with ``pages`` empty pages: no text layer, like a scan."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def case_insensitive(folder: Path) -> bool:
    """Whether ``folder``'s volume ignores case in names (the macOS default)."""
    probe = folder / ".flib-case-probe"
    probe.write_bytes(b"")
    try:
        return (folder / ".FLIB-CASE-PROBE").exists()
    finally:
        probe.unlink()


def names(folder: Path) -> list[str]:
    """The entry names in ``folder`` as the disk spells them, sorted."""
    return sorted(entry.name for entry in folder.iterdir())


# --- Claude Code CLI ---------------------------------------------------------------


class FakeClaude:
    """Controls the fake ``claude`` installed for every test (``tests/fake_claude.py``)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.executable = folder / "claude"

    def reply(self, stdout: str = "", *, stderr: str = "", exit: int = 0, sleep: float = 0) -> None:
        """Make the next calls print this instead of a successful answer."""
        payload = {"stdout": stdout, "stderr": stderr, "exit": exit, "sleep": sleep}
        (self.folder / "reply.json").write_text(json.dumps(payload), encoding="utf-8")

    def log_out(self) -> None:
        (self.folder / "logged_out").touch()

    def calls(self) -> list[dict[str, Any]]:
        log = self.folder / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# --- Anthropic API -----------------------------------------------------------------


def reply(
    data: Any,
    *,
    stop: str = "end_turn",
    usage: tuple[int, int] = (1000, 100),
    iterations: list[Any] | None = None,
) -> SimpleNamespace:
    """A Messages API response carrying ``data`` as its text block."""
    text = data if isinstance(data, str) else json.dumps(data)
    return SimpleNamespace(
        stop_reason=stop,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1], iterations=iterations),
    )


class FakeClient:
    """Stands in for ``anthropic.Anthropic``: answers with ``responses`` in order."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def planned(monkeypatch: pytest.MonkeyPatch, name: str, milestone: str = "M9") -> None:
    """Pretend provider ``name`` is accepted by the configuration but not implemented yet."""
    from file_librarian import providers

    registry = {k: v for k, v in providers.REGISTRY.items() if k != name}
    monkeypatch.setattr(providers, "REGISTRY", registry)
    monkeypatch.setattr(providers, "PLANNED", {name: milestone})
    monkeypatch.setattr("file_librarian.cli.PLANNED", {name: milestone})


# --- flib ui -------------------------------------------------------------------------


class UiResponse:
    """One reply from the ``flib ui`` server."""

    def __init__(self, status: int, headers: http.client.HTTPMessage, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.body)


class UiClient:
    """A browser stand-in for ``flib ui`` tests: requests to 127.0.0.1 with any headers,
    and a cookie jar. Needs the ``loopback_network`` fixture."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.cookies: dict[str, str] = {}

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        host: str | None = None,
    ) -> UiResponse:
        sent = {"Host": host if host is not None else f"127.0.0.1:{self.port}"}
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        sent |= headers or {}
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=sent)
            reply = conn.getresponse()
            result = UiResponse(reply.status, reply.headers, reply.read())
        finally:
            conn.close()
        for cookie in result.headers.get_all("Set-Cookie") or []:
            name, _, rest = cookie.partition("=")
            self.cookies[name] = rest.split(";", 1)[0]
        return result

    def get(self, path: str, **kw: Any) -> UiResponse:
        return self.request("GET", path, **kw)

    def launch(self, url: str) -> UiResponse:
        """Open the launch link, as the browser does when ``open`` hands it over."""
        split = urlsplit(url)
        return self.get(f"{split.path}?{split.query}", headers={"Sec-Fetch-Site": "none"})

    def launch_from_handoff(self, path: Path) -> UiResponse:
        """Open the handoff file, as the browser does when ``open`` hands it over: the
        page on disk (origin ``null``) posts its token to the server."""
        text = path.read_text(encoding="utf-8")
        action = re.search(r'action="([^"]+)"', text)
        token = re.search(r'name="token" value="([^"]+)"', text)
        assert action is not None and token is not None, "not a handoff page"
        split = urlsplit(html.unescape(action.group(1)))
        assert split.netloc == f"127.0.0.1:{self.port}", split.netloc
        return self.request(
            "POST",
            split.path,
            body=urlencode({"token": html.unescape(token.group(1))}).encode(),
            headers={
                "Origin": "null",
                "Sec-Fetch-Site": "cross-site",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

    def csrf(self, page: str = "/status") -> str:
        match = re.search(r'name="csrf" value="([^"]+)"', self.get(page).text)
        assert match is not None, "no CSRF token on the page"
        return match.group(1)

    def post_form(
        self, path: str, fields: dict[str, str], headers: dict[str, str] | None = None
    ) -> UiResponse:
        """A same-origin form POST, as the browser sends it; ``headers`` override."""
        sent = {
            "Origin": self.origin,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/x-www-form-urlencoded",
        } | (headers or {})
        return self.request("POST", path, body=urlencode(fields).encode(), headers=sent)

    def raw(self, data: bytes, *, timeout: float = 5.0) -> bytes:
        """Send ``data`` as is and read until the server closes the connection."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
            sock.sendall(data)
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        return b"".join(chunks)


def wait_for(condition: Callable[[], object], timeout: float = 5.0) -> None:
    """Poll ``condition`` until it is true; fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.01)
