"""The ``flib ui`` server: loopback only, one session, gone when idle (#113).

How a session starts. The server binds ``127.0.0.1`` on a free port and makes
a random *launch token*. The token never goes on a command line, where every
account on the Mac could read it with ``ps``: ``flib ui`` writes a *handoff
file*, an HTML page readable only by the user (0600, in a 0700 folder in the
account's own temporary folder), and asks macOS to open that file (``OPEN``,
swapped for a fake in every test). The page posts the token to ``/launch`` in
a form body, so it is never in a URL, a history entry or a ``Referer``, and
then clears it. A prefetch or prerender of ``/launch`` is refused. With
``--no-browser`` the user asked for a link: ``/launch?token=...`` is printed.

The first request with the token, compared in constant time, burns it,
deletes the handoff file and starts the only session there will be: it sets
an ``HttpOnly``, ``SameSite=Strict`` cookie named after the port, and
``flib ui`` says "session started". The token never works again. If it comes
back from a client without the session, someone else has it: the session is
dropped, the server ends and ``flib ui`` warns. A stale link, a missing or
wrong cookie or an ended session get the "session ended" page.

Every request, before anything else:

- it is read by the accepting thread, without blocking, until it has fully
  arrived (line, headers and the body they declare), within a deadline and
  size caps; only then does it get one of the few request threads;
- ``Host`` must be exactly ``127.0.0.1:<port>`` (421 otherwise), which defeats
  DNS rebinding; ``localhost`` is refused, since another program could hold
  ``[::1]:<port>``;
- an ``Origin``, when sent, must be exactly the server's; ``Upgrade`` is
  refused; ``Sec-Fetch-Site``, when sent, must say same-origin (or ``none``,
  a typed or opened address, for a GET);
- only GET and POST are served. Every POST route but ``/launch`` is in
  ``POST_ROUTES`` and gets the same checks: the exact origin (``Origin``, else
  ``Sec-Fetch-Site: same-origin``), a small body with a plain length, the
  session cookie and the session's CSRF token (form field ``csrf`` or header
  ``X-Flib-CSRF``). ``POST /launch`` alone may come from the handoff file,
  whose origin is ``null``: it carries the launch token instead.

Static files come from an in-memory map of packaged resources, looked up by
exact path, so no request can reach the file system. Every response carries
a strict Content-Security-Policy (no inline script or style), ``nosniff``,
``no-referrer``, ``no-store`` and framing and cross-origin isolation headers.
Error replies say only the status. Nothing is logged: a request line can hold
the launch token.

The server ends after ``ui.idle_minutes`` without a request from the session,
on "End session" (a POST) or on SIGINT/SIGTERM. One ``flib ui`` runs at a time
(``lock.ui_lock``); a second one says so and exits, since only the first knows
its port and token. The clock, the idle poll and the timeouts are injectable
for tests.
"""

from __future__ import annotations

import hmac
import html
import io
import ipaddress
import json
import os
import queue
import re
import secrets
import selectors
import shutil
import signal
import socket
import socketserver
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import parse_qs, urlsplit

from file_librarian import status as status_mod
from file_librarian.config import Config
from file_librarian.lock import AlreadyRunningError, ui_lock
from file_librarian.ui import api

OPEN = "/usr/bin/open"
"""Opens the handoff file in the default browser; tests swap in a fake."""
HOST = "127.0.0.1"
PAGES = ("status",)
"""Pages ``--open`` can name; Review, Search, Activity, Due and Settings come later."""
LAUNCH = "/launch"

MAX_THREADS = 8
"""Requests handled at once; one more gets 503 and is closed."""
REQUEST_TIMEOUT = 10.0
"""Seconds a client may take on each read or write before it is dropped."""
REQUEST_DEADLINE = 5.0
"""Seconds a client has to send its whole request: line, headers and body."""
MAX_REQUEST_LINE = 4096
MAX_HEADER_BYTES = 16 * 1024
MAX_BODY = 16 * 1024
IDLE_POLL_SECONDS = 1.0
CLOSE_GRACE = 1.0
"""Seconds a request in progress may take to finish once the server ends."""

CSRF_FIELD = "csrf"
CSRF_HEADER = "X-Flib-CSRF"
FORM = "application/x-www-form-urlencoded"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)
ALLOWED_METHODS = "GET, POST"
_CONTENT_LENGTH = re.compile(r"[0-9]{1,5}")

_TEXT = "text/plain; charset=utf-8"
_HTML = "text/html; charset=utf-8"
_JSON = "application/json"
_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}
ENDED = {
    "idle": "idle for {minutes} minutes",
    "ended": "the session was ended in the browser",
    "signal": "interrupted",
    "closed": "closed",
    "stolen": "the launch link was used again by a client without the session",
}
STOLEN_WARNING = (
    "warning: the launch link was used a second time, by a client without the "
    "session. Another program or account on this Mac may have seen it, so the "
    "session was dropped and flib ui ended. Run `flib ui` again for a new link."
)

HANDOFF_NAME = "open-file-librarian.html"
HANDOFF = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>Opening file-librarian</title>
</head>
<body>
<form id="launch" method="post" action="{action}">
<input type="hidden" name="token" value="{token}">
</form>
<p id="state">Opening file-librarian&hellip;</p>
<noscript><p>This page needs JavaScript. Run <code>flib ui --no-browser</code> for a link
instead.</p></noscript>
<script>
(function () {{
  // Posts the token once, then leaves nothing that could post it again: a
  // used token that comes back without the session ends flib ui as stolen.
  function spent() {{
    var form = document.getElementById("launch");
    if (form) {{
      form.elements.token.value = "";
      form.remove();
    }}
    document.getElementById("state").textContent =
      "file-librarian opened in this browser. You can close this tab.";
  }}
  window.addEventListener("pageshow", function (event) {{
    if (event.persisted) spent();  // shown again from the back/forward cache
  }});
  var form = document.getElementById("launch");
  if (form && form.elements.token.value) {{
    form.submit();  // the form data is taken now, before the field is cleared
    form.elements.token.value = "";
  }}
  window.setTimeout(spent, 0);  // the form goes once the post is on its way
}})();
</script>
</body>
</html>
"""
"""The handoff file: opened from disk (not served, so the server's CSP does not
apply), it posts the launch token to the server at once and then clears it,
so going back to it, a double submit or a bfcache restore cannot post it again."""
HANDOFF_PREFIX = "ui-launch-"


class UiError(Exception):
    """The server cannot start (port in use, unknown page, not a loopback address)."""


def check_bind(host: str) -> None:
    """Refuse any address but loopback: never ``0.0.0.0``, ``::`` or a LAN address."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise UiError(f"refusing to listen on {host!r}: not a loopback address") from exc
    if not address.is_loopback:
        raise UiError(f"refusing to listen on {host}: not a loopback address")


def handoff_root() -> Path:
    """Where handoff folders go (tests point it at a temporary folder)."""
    return user_temp_dir()


def user_temp_dir() -> Path:
    """The per-user temporary folder macOS gives each account (``/var/folders/.../T``:
    mode 0700, on the boot volume, not backed up or indexed), never next to the
    database, which may sit on a volume where file modes mean nothing. Falls back
    to Python's temporary folder, where ``mkdtemp`` still makes a 0700 folder with
    a random name."""
    try:
        path = os.confstr("CS_DARWIN_USER_TEMP_DIR")
    except (ValueError, OSError):
        path = None
    if path:
        root = Path(path)
        with suppress(OSError):
            info = root.stat()
            if root.is_dir() and info.st_uid == os.getuid() and not info.st_mode & 0o077:
                return root
    return Path(tempfile.gettempdir())


def sweep_handoffs(root: Path) -> None:
    """Remove handoff folders a ``flib ui`` that did not end cleanly left behind.
    Called while holding ``ui_lock``, so none of them belongs to a running one."""
    with suppress(OSError):
        for entry in root.iterdir():
            if not entry.name.startswith(HANDOFF_PREFIX):
                continue
            with suppress(OSError):
                info = entry.lstat()
                if stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid():
                    shutil.rmtree(entry, ignore_errors=True)


def load_assets() -> dict[str, tuple[bytes, str]]:
    """``/static/<name>`` -> (contents, type), read once from the package."""
    found: dict[str, tuple[bytes, str]] = {}
    for entry in (resources.files("file_librarian.ui") / "static").iterdir():
        kind = _TYPES.get("." + entry.name.rpartition(".")[2])
        if entry.is_file() and kind is not None:
            found[f"/static/{entry.name}"] = (entry.read_bytes(), kind)
    icon = resources.files("file_librarian") / "assets" / "icon-1024.png"
    found["/static/icon.png"] = (icon.read_bytes(), "image/png")
    return found


def load_pages() -> dict[str, str]:
    """Page name -> its HTML, with ``{{csrf}}`` where the session's token goes."""
    folder = resources.files("file_librarian.ui") / "pages"
    return {
        entry.name.removesuffix(".html"): entry.read_text(encoding="utf-8")
        for entry in folder.iterdir()
        if entry.name.endswith(".html")
    }


def same(given: str, expected: str) -> bool:
    """Constant-time comparison of two tokens."""
    return hmac.compare_digest(given.encode("utf-8", "replace"), expected.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    csrf: str


class Ui:
    """One server, its one session and its idle timer. ``start`` serves in threads.

    ``on_event`` hears ``"started"`` when the launch token starts the session
    and ``"stolen"`` when a used token comes back without it (from a request
    thread; never with the token).
    """

    def __init__(
        self,
        config: Config,
        *,
        page: str = "status",
        port: int = 0,
        idle_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        poll_seconds: float = IDLE_POLL_SECONDS,
        max_threads: int = MAX_THREADS,
        request_timeout: float = REQUEST_TIMEOUT,
        request_deadline: float = REQUEST_DEADLINE,
        close_grace: float = CLOSE_GRACE,
        on_event: Callable[[str], None] = lambda event: None,
    ) -> None:
        if page not in PAGES:
            raise UiError(f"no page {page!r}; one of: {', '.join(PAGES)}")
        self.config = config
        self.page = page
        self.idle_seconds = config.ui.idle_minutes * 60.0 if idle_seconds is None else idle_seconds
        self.request_timeout = request_timeout
        self.request_deadline = request_deadline
        self._grace = min(close_grace, request_timeout)
        self.assets = load_assets()
        self.pages = load_pages()
        self.reason: str | None = None
        self.on_event = on_event
        self._clock = clock
        self._poll = poll_seconds
        self._lock = threading.Lock()
        self._launch: str | None = secrets.token_urlsafe(32)
        self._burned: str | None = None
        self._session: Session | None = None
        self._handoff: Path | None = None
        self._last = clock()
        self._stops: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._heads: _Heads | None = None
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        self._wake_w.setblocking(False)
        try:
            self._server = _HttpServer(self, port, max_threads)
        except BaseException as exc:
            self._wake_r.close()
            self._wake_w.close()
            if isinstance(exc, OSError):
                raise UiError(f"cannot listen on {HOST}:{port}: {exc.strerror or exc}") from exc
            raise
        self.port: int = self._server.server_address[1]
        self.origin = f"http://{HOST}:{self.port}"
        self.url = f"{self.origin}/"
        self.launch_url = f"{self.origin}{LAUNCH}?token={self._launch}"
        self.hosts = frozenset({f"{HOST}:{self.port}"})
        self.cookie_name = f"flib_ui_{self.port}"

    # --- lifecycle ---
    #
    # One thread, "flib-ui", accepts connections and reads each request (line,
    # headers and a body of the length it declares) without blocking; only a
    # whole request goes to a request thread, so a client that sends nothing, or
    # a byte at a time, never holds one. The same thread checks the idle timer
    # and the request deadline and, when asked to stop, closes everything and
    # joins every request thread before ``wait`` returns. ``stop`` takes no lock (a
    # reentrant queue and a byte on a socket pair), so it is safe from a signal
    # handler, even one that interrupts another; a handler that took a lock
    # could deadlock the main thread.

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="flib-ui", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        listener = self._server.socket
        listener.setblocking(False)
        tick = min(self._poll, self.request_deadline / 2)
        try:
            with selectors.DefaultSelector() as selector:
                self._heads = heads = _Heads(selector, self._server, self.request_deadline)
                selector.register(listener, selectors.EVENT_READ)
                selector.register(self._wake_r, selectors.EVENT_READ)
                try:
                    while (reason := self._next_reason()) is None:
                        for key, _ in selector.select(tick):
                            if key.fileobj is listener:
                                self._accept(heads)
                            elif key.fileobj is self._wake_r:
                                self._drain_wake()
                            else:
                                heads.readable(key.fileobj)
                        heads.expire()
                        self._server.drop_late(self.request_deadline)
                finally:
                    heads.close_all()
        except Exception:  # the server ends rather than hang
            reason = "closed"
        self.reason = reason
        try:
            self._server.server_close()
            self._server.close_connections(self._grace)
            self.remove_handoff()
        finally:
            self._done.set()

    def _next_reason(self) -> str | None:
        try:
            return self._stops.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            idle = self._clock() - self._last
        return "idle" if idle >= self.idle_seconds else None

    def _accept(self, heads: _Heads) -> None:
        while True:
            try:
                request, address = self._server.get_request()
            except OSError:  # nothing more to accept, or gone before it was
                return
            heads.add(request, address)

    def _drain_wake(self) -> None:
        with suppress(OSError):
            while self._wake_r.recv(64):
                pass

    def stop(self, reason: str) -> None:
        """Ask the server to end; the first reason given is kept. Takes no lock."""
        self._stops.put(reason)
        # Full (a wake-up is already pending) or closed (the server has ended).
        with suppress(OSError):
            self._wake_w.send(b"\0")

    def wait(self, timeout: float | None = None) -> str | None:
        """Why the server ended, once it has; ``None`` while it still runs."""
        return self.reason if self._done.wait(timeout) else None

    def close(self) -> None:
        """End the server and wait until no thread, socket or handoff file of it is left."""
        if self._thread is None:
            self.reason = self.reason or "closed"
            self._server.server_close()
            self.remove_handoff()
            self._done.set()
        else:
            self.stop("closed")
            self._thread.join()
        self._wake_r.close()
        self._wake_w.close()

    # --- the handoff file ---

    def write_handoff(self) -> Path:
        """The page that hands the launch token to the browser, readable by the user only."""
        with self._lock:
            token = self._launch
        if token is None:
            raise UiError("the launch link is already used")
        # A random name, mode 0700; the file inside is new (O_EXCL) and never a
        # link someone planted (O_NOFOLLOW).
        folder = Path(tempfile.mkdtemp(prefix=HANDOFF_PREFIX, dir=handoff_root()))
        path = folder / HANDOFF_NAME
        text = HANDOFF.format(action=html.escape(self.origin + LAUNCH), token=html.escape(token))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self._handoff = path
        return path

    def remove_handoff(self) -> None:
        with self._lock:
            path, self._handoff = self._handoff, None
        if path is not None:
            shutil.rmtree(path.parent, ignore_errors=True)

    # --- session ---

    def touch(self) -> None:
        with self._lock:
            self._last = self._clock()

    def redeem(self, token: str) -> Session | None:
        """The launch token, once: the session it starts, or ``None``."""
        with self._lock:
            if self._launch is None or not same(token, self._launch):
                return None
            self._burned, self._launch = self._launch, None
            self._session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32))
            session = self._session
        self.remove_handoff()
        self.touch()
        self.on_event("started")
        return session

    def burned(self, token: str) -> bool:
        """Whether ``token`` is the launch token, already used."""
        with self._lock:
            used = self._burned
        return used is not None and same(token, used)

    def stolen(self) -> None:
        """The used token came back without the session: drop it and end."""
        self.end_session()
        self.on_event("stolen")
        self.stop("stolen")

    def end_session(self) -> None:
        with self._lock:
            self._session = None

    def session(self, session_id: str) -> Session | None:
        with self._lock:
            current = self._session
        if current is None or not same(session_id, current.id):
            return None
        return current


MAX_PENDING = 128
"""Connections whose request has not fully arrived; one more drops the oldest."""
MAX_HEAD = MAX_REQUEST_LINE + MAX_HEADER_BYTES + 4096
"""Bytes of request line and headers read before the request is refused (the
headers' own limits, plus their names, separators and line ends)."""
LINGER = 1.0
"""Seconds a refused connection is drained, so the client reads the refusal
instead of a reset."""
_HEAD_END = re.compile(rb"\r?\n\r?\n")
_LENGTH_HEADER = re.compile(rb"\ncontent-length:[ \t]*([0-9]{1,5})[ \t]*\r?\n", re.IGNORECASE)


@dataclass(slots=True)
class _Pending:
    address: Any
    started: float = field(default_factory=time.monotonic)
    data: bytearray = field(default_factory=bytearray)
    refused: bool = False


class _Heads:
    """Connections whose request is still arriving, read in the accepting thread.

    A request goes to a request thread once its line and headers have arrived,
    with the body when it declares one plain ``Content-Length`` (anything else
    is left to the handler to refuse), or once the client stops sending. One that
    takes longer than the deadline is dropped, one too long is refused, and past
    ``MAX_PENDING`` the oldest is dropped.
    """

    def __init__(
        self, selector: selectors.BaseSelector, server: _HttpServer, deadline: float
    ) -> None:
        self._selector = selector
        self._server = server
        self._deadline = deadline
        self.pending: dict[socket.socket, _Pending] = {}

    def add(self, sock: socket.socket, address: Any) -> None:
        while len(self.pending) >= MAX_PENDING:
            self._forget(next(iter(self.pending)))  # the oldest: dicts keep order
        sock.setblocking(False)
        self.pending[sock] = _Pending(address)
        self._selector.register(sock, selectors.EVENT_READ)
        self.readable(sock)  # a browser sends its request with the connection

    def readable(self, sock: Any) -> None:
        pending = self.pending.get(sock)
        if pending is None:
            return
        try:
            chunk = sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if pending.refused:  # draining until the client has read the refusal
            if not chunk:
                self._forget(sock)
            return
        if not chunk:  # the client stopped sending: the handler says what is wrong
            if pending.data:
                self._hand_off(sock)
            else:
                self._forget(sock)
            return
        pending.data += chunk
        self._check(sock, pending)

    def _check(self, sock: socket.socket, pending: _Pending) -> None:
        data = pending.data
        line_end = data.find(b"\n")
        if (line_end == -1 and len(data) > MAX_REQUEST_LINE) or line_end > MAX_REQUEST_LINE:
            self._refuse(sock, pending, HTTPStatus.REQUEST_URI_TOO_LONG)
            return
        end = _HEAD_END.search(data)
        if end is None:
            if len(data) > MAX_HEAD:
                self._refuse(sock, pending, HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
            return
        lengths = _LENGTH_HEADER.findall(bytes(data[: end.end()]))
        wanted = int(lengths[0]) if len(lengths) == 1 else 0
        if wanted > MAX_BODY:
            wanted = 0  # the handler refuses it before reading
        if len(data) - end.end() >= wanted:
            self._hand_off(sock)

    def expire(self) -> None:
        now = time.monotonic()
        for sock, pending in list(self.pending.items()):
            if pending.refused:
                if now - pending.started > LINGER:
                    self._forget(sock)
            elif now - pending.started > self._deadline:
                if pending.data:  # it started a request: say why it ends
                    self._refuse(sock, pending, HTTPStatus.REQUEST_TIMEOUT)
                else:
                    self._forget(sock)

    def close_all(self) -> None:
        for sock in list(self.pending):
            self._forget(sock)

    def _refuse(self, sock: socket.socket, pending: _Pending, code: int) -> None:
        with suppress(OSError):
            sock.send(_plain_reply(code))
            sock.shutdown(socket.SHUT_WR)
        pending.refused = True
        pending.started = time.monotonic()
        pending.data = bytearray()

    def _hand_off(self, sock: socket.socket) -> None:
        pending = self.pending.pop(sock)
        self._selector.unregister(sock)
        self._server.hand_off(sock, pending.address, bytes(pending.data), pending.started)

    def _forget(self, sock: socket.socket) -> None:
        self.pending.pop(sock, None)
        with suppress(KeyError, ValueError):
            self._selector.unregister(sock)
        sock.close()


@dataclass(slots=True)
class _Connection:
    thread: threading.Thread
    head: bytes
    """What the accepting thread read of the request; the handler reads it first."""
    started: float
    read: bool = False
    """The whole request has arrived; the deadline no longer applies."""


def _plain_reply(code: int) -> bytes:
    """A whole reply with no body, for when there is no handler to send it."""
    phrase = HTTPStatus(code).phrase
    lines = [f"HTTP/1.0 {code} {phrase}", "Server: flib"]
    lines += [f"{name}: {value}" for name, value in SECURITY_HEADERS]
    lines += [f"Content-Type: {_TEXT}", "Content-Length: 0", "Connection: close"]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


class _HttpServer(HTTPServer):
    """Accepts nothing by itself: ``Ui._serve`` accepts, this runs each request in a
    thread of its own, at most ``max_threads`` at once, and keeps track of them."""

    allow_reuse_address = False
    allow_reuse_port = False
    request_queue_size = MAX_PENDING

    def __init__(self, ui: Ui, port: int, max_threads: int) -> None:
        check_bind(HOST)
        self.ui = ui
        self._slots = threading.BoundedSemaphore(max_threads)
        self._active: dict[socket.socket, _Connection] = {}
        self._active_lock = threading.Lock()
        super().__init__((HOST, port), _Handler)

    def server_bind(self) -> None:
        # Not HTTPServer.server_bind: it looks the host name up (DNS) for nothing.
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        check_bind(host)
        self.server_name = host
        self.server_port = port

    def hand_off(self, request: socket.socket, address: Any, head: bytes, started: float) -> None:
        """Serve a request that has arrived (``head`` is what was read of it) in a
        request thread of its own, or turn it away when all are busy."""
        request.setblocking(True)
        if not self._slots.acquire(blocking=False):
            with suppress(OSError):
                # Never blocks the accepting thread: the reply fits the socket buffer.
                request.setblocking(False)
                request.send(_plain_reply(HTTPStatus.SERVICE_UNAVAILABLE))
            self.shutdown_request(request)
            return
        thread = threading.Thread(
            target=self._one,
            args=(request, address),
            name="flib-ui-request",
            daemon=True,
        )
        with self._active_lock:
            self._active[request] = _Connection(thread, head, started)
        thread.start()

    def take_head(self, request: socket.socket) -> bytes:
        """What the accepting thread already read of this request, once."""
        with self._active_lock:
            connection = self._active.get(request)
            if connection is None:
                return b""
            head, connection.head = connection.head, b""
            return head

    def _one(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:  # noqa: S110 - a dropped client; nothing is logged
            pass
        finally:
            # Under the lock, so drop_late and close_connections never shut a
            # socket whose descriptor was just closed and reused.
            with self._active_lock:
                del self._active[request]
                self.shutdown_request(request)
            self._slots.release()

    def request_read(self, request: socket.socket) -> None:
        """The handler has the whole request: no deadline from now on."""
        with self._active_lock:
            if (connection := self._active.get(request)) is not None:
                connection.read = True

    def drop_late(self, deadline: float) -> None:
        """Cut off every client that has not sent its whole request in ``deadline``
        seconds, however slowly it trickles bytes in."""
        now = time.monotonic()
        with self._active_lock:
            for request, connection in self._active.items():
                if not connection.read and now - connection.started > deadline:
                    with suppress(OSError):
                        request.shutdown(socket.SHUT_RDWR)

    def close_connections(self, grace: float) -> None:
        """Let requests in progress finish for up to ``grace`` seconds, then cut the
        rest off; returns once every request thread has ended."""
        deadline = time.monotonic() + grace
        for thread in self._threads():
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._active_lock:
            for request in self._active:
                with suppress(OSError):
                    request.shutdown(socket.SHUT_RDWR)
        for thread in self._threads():
            thread.join()

    def _threads(self) -> list[threading.Thread]:
        with self._active_lock:
            return [connection.thread for connection in self._active.values()]


class _Prefixed(io.RawIOBase):
    """Reads ``head`` first, then the socket: what a request handler reads."""

    def __init__(self, head: bytes, sock: socket.socket) -> None:
        super().__init__()
        self._head = memoryview(head)
        self._sock = sock

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self._head:
            n = min(len(buffer), len(self._head))
            buffer[:n] = self._head[:n]
            self._head = self._head[n:]
            return n
        return self._sock.recv_into(buffer)


class _Reject(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Handler(BaseHTTPRequestHandler):
    server: _HttpServer
    protocol_version = "HTTP/1.0"
    server_version = "flib"
    sys_version = ""
    _replied = False

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.server.ui.request_timeout)
        # The accepting thread already read the request (or most of it).
        self.rfile.close()
        raw = _Prefixed(self.server.take_head(self.connection), self.connection)
        self.rfile = io.BufferedReader(raw)

    def log_message(self, format: str, *args: Any) -> None:
        """Never log: a request line can hold the launch token."""

    def version_string(self) -> str:
        return "flib"

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Every error says its status and nothing else, with a status line and
        the security headers even when the request line made no sense."""
        self.close_connection = True
        if self._replied:
            return
        if self.request_version in ("HTTP/0.9", ""):
            self.request_version = "HTTP/1.0"  # else http.server sends a bare body
        headers = [("Allow", ALLOWED_METHODS)] if code == HTTPStatus.METHOD_NOT_ALLOWED else []
        self._reply(code, f"{HTTPStatus(code).phrase}\n".encode(), _TEXT, headers)

    # --- methods ---

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _not_allowed(self) -> None:
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _not_allowed

    def _handle(self) -> None:
        try:
            if self.command != "POST":  # a GET has no body: it has all arrived
                self.server.request_read(self.connection)
            path = urlsplit(self.path).path
            self._guard(path)
            body = self._body() if self.command == "POST" else b""
            self.server.request_read(self.connection)
            # Only the session keeps the server alive (the launch exchange too,
            # in ``Ui.redeem``): anything else may knock on the port forever.
            if self._session() is not None:
                self.server.ui.touch()
            if self.command != "POST":
                self._get(path)
            elif path == LAUNCH:
                self._launch_from_handoff(body)
            else:
                self._post(path, body)
        except _Reject as reject:
            self.send_error(reject.code)
        except Exception:  # a bug must still get a reply, and never a traceback
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)

    def _guard(self, path: str) -> None:
        ui = self.server.ui
        if len(self.requestline) > MAX_REQUEST_LINE:
            raise _Reject(HTTPStatus.REQUEST_URI_TOO_LONG)
        if sum(len(k) + len(v) for k, v in self.headers.items()) > MAX_HEADER_BYTES:
            raise _Reject(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0] not in ui.hosts:
            raise _Reject(HTTPStatus.MISDIRECTED_REQUEST)
        # http.server already turns a leading "//" into "/".
        if not self.path.startswith("/"):
            raise _Reject(HTTPStatus.BAD_REQUEST)
        if self.headers.get("Upgrade") is not None:
            raise _Reject(HTTPStatus.BAD_REQUEST)
        origins = self.headers.get_all("Origin") or []
        sites = self.headers.get_all("Sec-Fetch-Site") or []
        if len(origins) > 1 or len(sites) > 1:
            raise _Reject(HTTPStatus.FORBIDDEN)
        origin = origins[0] if origins else None
        site = sites[0] if sites else None
        if path == LAUNCH and self._speculative():
            # A browser may prerender a typed address before Enter: that must
            # not use the token (nor count as its theft).
            raise _Reject(HTTPStatus.FORBIDDEN)
        if self.command == "POST" and path == LAUNCH:
            if urlsplit(self.path).query:
                raise _Reject(HTTPStatus.BAD_REQUEST)  # the token goes in the body only
            # The handoff file is a page on disk: its origin is "null", and a
            # browser may call its form post cross-site. The launch token in the
            # body is what is checked; it stays exact-origin otherwise.
            if origin not in (None, "null", ui.origin):
                raise _Reject(HTTPStatus.FORBIDDEN)
            if site == "same-site":
                raise _Reject(HTTPStatus.FORBIDDEN)
            return
        if origin is not None and origin != ui.origin:
            raise _Reject(HTTPStatus.FORBIDDEN)
        allowed_sites = ("same-origin",) if self.command == "POST" else ("same-origin", "none")
        if site is not None and site not in allowed_sites:
            raise _Reject(HTTPStatus.FORBIDDEN)
        if self.command == "POST" and origin is None and site != "same-origin":
            raise _Reject(HTTPStatus.FORBIDDEN)

    def _speculative(self) -> bool:
        """A prefetch or prerender, not the user opening the page."""
        purposes = [*self.headers.get_all("Sec-Purpose", []), *self.headers.get_all("Purpose", [])]
        return any(
            word.strip().lower() in ("prefetch", "prerender")
            for value in purposes
            for word in value.split(";")
        )

    def _session(self) -> Session | None:
        """The session, if any cookie of that name holds it: a cookie another app
        on 127.0.0.1 set with the same name must not shadow the real one."""
        name = self.server.ui.cookie_name
        found: Session | None = None
        for header in self.headers.get_all("Cookie") or []:
            for part in header.split(";"):
                key, sep, value = part.strip().partition("=")
                if sep and key == name and (session := self.server.ui.session(value)):
                    found = session
        return found

    def _body(self) -> bytes:
        """The body of a POST: one plain ``Content-Length`` of ASCII digits, no
        ``Transfer-Encoding``, at most ``MAX_BODY`` bytes."""
        if self.headers.get_all("Transfer-Encoding"):
            raise _Reject(HTTPStatus.BAD_REQUEST)
        lengths = self.headers.get_all("Content-Length") or []
        if not lengths:
            raise _Reject(HTTPStatus.LENGTH_REQUIRED)
        if len(lengths) != 1 or not _CONTENT_LENGTH.fullmatch(lengths[0]):
            raise _Reject(HTTPStatus.BAD_REQUEST)
        length = int(lengths[0])
        if length > MAX_BODY:
            raise _Reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        body = self.rfile.read(length)
        if len(body) != length:
            raise _Reject(HTTPStatus.BAD_REQUEST)
        return body

    def _form(self, body: bytes) -> dict[str, list[str]] | None:
        """The fields of a form body, or ``None`` when the body is not a form."""
        kind = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if kind != FORM:
            return None
        try:
            return parse_qs(body.decode("utf-8", "replace"), max_num_fields=8)
        except ValueError:
            return None

    # --- GET ---

    def _get(self, path: str) -> None:
        ui = self.server.ui
        if path in ui.assets:
            body, kind = ui.assets[path]
            self._reply(HTTPStatus.OK, body, kind)
            return
        if path == LAUNCH:
            try:
                tokens = parse_qs(urlsplit(self.path).query, max_num_fields=4).get("token", [])
            except ValueError as exc:
                raise _Reject(HTTPStatus.BAD_REQUEST) from exc
            self._launch(tokens, from_handoff=False)
            return
        session = self._session()
        if path.startswith("/api/"):
            if session is None:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "no session"})
            elif path == "/api/v1/status":
                self._status()
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if path == "/" or path[1:] in PAGES:
            if session is None:
                self._ended(HTTPStatus.FORBIDDEN)
            elif path == "/":
                self._redirect(f"/{ui.page}")
            else:
                page = ui.pages[path[1:]].replace("{{csrf}}", session.csrf)
                self._reply(HTTPStatus.OK, page.encode(), _HTML)
            return
        raise _Reject(HTTPStatus.NOT_FOUND)

    def _status(self) -> None:
        try:
            data = api.status(self.server.ui.config)
        except status_mod.StatusError as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
        except sqlite3.Error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "cannot read the index"})
        else:
            self._json(HTTPStatus.OK, data)

    # --- the launch exchange ---

    def _launch_from_handoff(self, body: bytes) -> None:
        fields = self._form(body)
        if fields is None:
            raise _Reject(HTTPStatus.BAD_REQUEST)
        self._launch(fields.get("token", []), from_handoff=True)

    def _launch(self, tokens: list[str], *, from_handoff: bool) -> None:
        ui = self.server.ui
        if self._session() is not None:  # the same browser, opening the link again
            self._redirect(f"/{ui.page}")
            return
        token = tokens[0] if len(tokens) == 1 else None
        session = ui.redeem(token) if token is not None else None
        if session is None:
            if token is not None and ui.burned(token):
                ui.stolen()
            self._ended(HTTPStatus.FORBIDDEN)
            return
        cookie = f"{ui.cookie_name}={session.id}; Path=/; HttpOnly; SameSite=Strict"
        if from_handoff:
            # The post came from a page on disk, so a redirect would be a
            # cross-site chain and the SameSite=Strict cookie would not go with
            # it. This page moves on from the server's own origin instead.
            page = ui.pages["opening"].encode()
            self._reply(HTTPStatus.OK, page, _HTML, [("Set-Cookie", cookie)])
        else:
            self._redirect(f"/{ui.page}", [("Set-Cookie", cookie)])

    # --- POST ---

    def _post(self, path: str, body: bytes) -> None:
        """Every POST route but the launch exchange: the session cookie and the
        CSRF token (the exact origin was checked by ``_guard``), then the route."""
        session = self._session()
        if session is None:
            raise _Reject(HTTPStatus.FORBIDDEN)
        token = self._csrf_token(body)
        if token is None or not same(token, session.csrf):
            raise _Reject(HTTPStatus.FORBIDDEN)
        route = POST_ROUTES.get(path)
        if route is None:
            raise _Reject(HTTPStatus.NOT_FOUND)
        route(self, session)

    def _csrf_token(self, body: bytes) -> str | None:
        headers = self.headers.get_all(CSRF_HEADER) or []
        if headers:
            return headers[0] if len(headers) == 1 else None
        fields = self._form(body)
        values = [] if fields is None else fields.get(CSRF_FIELD, [])
        return values[0] if len(values) == 1 else None

    def _end_session(self, session: Session) -> None:
        ui = self.server.ui
        ui.end_session()
        expired = f"{ui.cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        self._ended(HTTPStatus.OK, [("Set-Cookie", expired)])
        ui.stop("ended")

    # --- replies ---

    def _ended(self, code: int, headers: Iterable[tuple[str, str]] = ()) -> None:
        self._reply(code, self.server.ui.pages["ended"].encode(), _HTML, headers)

    def _redirect(self, location: str, headers: Iterable[tuple[str, str]] = ()) -> None:
        self._reply(HTTPStatus.SEE_OTHER, b"", _TEXT, [("Location", location), *headers])

    def _json(self, code: int, data: object) -> None:
        self._reply(code, json.dumps(data, ensure_ascii=False).encode(), _JSON)

    def _reply(
        self, code: int, body: bytes, kind: str, headers: Iterable[tuple[str, str]] = ()
    ) -> None:
        self._replied = True
        self.send_response(code)
        for name, value in (*SECURITY_HEADERS, ("Content-Type", kind), *headers):
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


PostRoute = Callable[[_Handler, Session], None]
POST_ROUTES: dict[str, PostRoute] = {
    "/session/end": _Handler._end_session,
}
"""Every POST route. Each one is reached only through ``_Handler._post``, after
the exact origin, the session cookie and the CSRF token; ``POST /launch`` is the
one exception, and is not in this table."""


# --- flib ui ---------------------------------------------------------------------------


def open_file(path: Path) -> str | None:
    """Ask macOS to open ``path`` in the default browser; why it failed, or ``None``."""
    try:
        # A fixed binary; the path is one argument, never parsed by a shell.
        result = subprocess.run(  # noqa: S603
            [OPEN, str(path)], capture_output=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return type(exc).__name__
    return None if result.returncode == 0 else f"open exited with {result.returncode}"


def run(
    config: Config,
    *,
    page: str = "status",
    port: int = 0,
    browser: bool = True,
    echo: Callable[[str], None] = print,
    warn: Callable[[str], None] = print,
) -> str:
    """``flib ui``: serve until idle, ended or interrupted; why it ended.

    ``warn`` gets what belongs on stderr. Raises ``UiError`` when it cannot
    start. Returns ``"running"`` without serving when another ``flib ui`` holds
    the lock.
    """

    def on_event(event: str) -> None:
        if event == "started":
            echo("flib ui: session started in the browser.")
        elif event == "stolen":
            warn(STOLEN_WARNING)

    ending = (
        f"It ends after {config.ui.idle_minutes} idle minutes, with End session, or with Ctrl-C."
    )
    try:
        with ui_lock(config.database):
            sweep_handoffs(handoff_root())
            ui = Ui(config, page=page, port=port, on_event=on_event)
            try:
                with _signals(ui):
                    ui.start()
                    if browser:
                        handoff = ui.write_handoff()
                        echo(f"flib ui: opening {ui.url} in your browser. {ending}")
                        if (problem := open_file(handoff)) is not None:
                            warn(
                                f"warning: could not open the browser ({problem}). Open "
                                f"{handoff} in your browser, or end this and run "
                                "`flib ui --no-browser` for a link."
                            )
                    else:
                        echo(f"flib ui: {ui.launch_url}")
                        echo(f"The link works once; keep it to yourself. {ending}")
                    while ui.wait(0.5) is None:
                        pass
            finally:
                ui.close()
            reason = ui.reason or "closed"
            echo("flib ui ended: " + ENDED[reason].format(minutes=config.ui.idle_minutes) + ".")
            return reason
    except AlreadyRunningError as exc:
        echo(
            f"{exc}. Use the browser tab it opened, or end it there (End session) or "
            "with Ctrl-C in its terminal, then run `flib ui` again; it also ends on its "
            f"own after {config.ui.idle_minutes} idle minutes."
        )
        return "running"


@contextmanager
def _signals(ui: Ui) -> Iterator[None]:
    """SIGINT and SIGTERM end the server cleanly while the block runs (main thread only)."""

    def handler(signum: int, frame: FrameType | None) -> None:
        ui.stop("signal")

    previous: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, handler)
    try:
        yield
    finally:
        for number, old in previous.items():
            signal.signal(number, old)
