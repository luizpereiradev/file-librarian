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

import dataclasses
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
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from file_librarian import status as status_mod
from file_librarian.config import Config
from file_librarian.lock import AlreadyRunningError, ui_lock
from file_librarian.ui import actions, api, review, settings, views

OPEN = "/usr/bin/open"
"""Opens the handoff file in the default browser; tests swap in a fake."""
HOST = "127.0.0.1"
PAGES = ("status", "review", "search", "activity", "due", "settings")
"""Pages ``--open`` can name."""
VIEWS: dict[str, Callable[[Config, str, str], views.Page]] = {
    "activity": views.activity_page,
}
"""Pages built on the server for each request (#114): config, CSRF token, query."""
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
MAX_FORM_FIELDS = 100
"""Fields of a form body or a query: the search form has a chip per category."""
IDLE_POLL_SECONDS = 1.0
CLOSE_GRACE = 1.0
"""Seconds a request in progress may take to finish once the server ends."""

REFERRER_POLICY = "same-origin"
CSRF_FIELD = "csrf"
CSRF_HEADER = "X-Flib-CSRF"
FORM = "application/x-www-form-urlencoded"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "require-trusted-types-for 'script'"
)
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    # Not "no-referrer": under it a browser sends ``Origin: null`` on a same-origin
    # form post, which ``_guard`` must refuse. "same-origin" sends a referrer (and the
    # origin) to this server only; nothing goes to another site.
    ("Referrer-Policy", REFERRER_POLICY),
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
    "idle": "idle for {minutes}",
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
HANDOFF_STALE_SECONDS = 600.0
"""A handoff folder older than this is left over, whoever made it: a link is used
within seconds, and a ``flib ui`` whose link was never used is no loss."""
END_LINGER = 1.5
"""Seconds the server keeps serving its static files after End session or a theft,
so the "session ended" page it just sent gets its stylesheet and icon."""
MAX_SAVED_SEARCHES = 32
"""Searches kept for ``GET /search?r=<id>`` (post, redirect, get), per server."""


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


def sweep_handoffs(root: Path, *, now: float | None = None) -> None:
    """Remove handoff folders a ``flib ui`` that did not end cleanly left behind.

    ``ui_lock`` is per database, but the folders share the account's temporary
    folder: another ``flib ui`` (another configuration) may be running. So only a
    folder that is provably stale goes: its owner (the process id in its name) is
    gone, or it is older than ``HANDOFF_STALE_SECONDS``."""
    moment = time.time() if now is None else now
    with suppress(OSError):
        for entry in root.iterdir():
            if not entry.name.startswith(HANDOFF_PREFIX):
                continue
            with suppress(OSError):
                info = entry.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                    continue
                old = moment - info.st_mtime > HANDOFF_STALE_SECONDS
                owner = _handoff_owner(entry.name)
                if old or (owner is not None and not _alive(owner)):
                    shutil.rmtree(entry, ignore_errors=True)


def _handoff_owner(name: str) -> int | None:
    """The process id a handoff folder's name records (``ui-launch-<pid>-...``)."""
    head = name.removeprefix(HANDOFF_PREFIX).split("-", 1)[0]
    return int(head) if head.isdigit() and len(head) < 10 else None


def _alive(pid: int) -> bool:
    """Whether process ``pid`` exists (signal 0 checks without sending anything)."""
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # it exists but belongs to someone else
        return True
    return True


def load_assets() -> dict[str, tuple[bytes, str]]:
    """``/static/<name>`` -> (contents, type), read once from the package."""
    found: dict[str, tuple[bytes, str]] = {}
    for entry in (resources.files("file_librarian.ui") / "static").iterdir():
        kind = _TYPES.get("." + entry.name.rpartition(".")[2])
        if entry.is_file() and kind is not None:
            found[f"/static/{entry.name}"] = (entry.read_bytes(), kind)
    icon = resources.files("file_librarian") / "assets" / "icon-1024.png"
    found["/static/icon.png"] = (icon.read_bytes(), "image/png")
    found["/favicon.ico"] = found["/static/icon.png"]  # what a browser asks for anyway
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
    secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    """Keys what pages name in addresses (a review group, #115): never sent anywhere."""


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
        end_linger: float = END_LINGER,
        on_event: Callable[[str], None] = lambda event: None,
        config_path: Path | None = None,
    ) -> None:
        if page not in PAGES:
            raise UiError(f"no page {page!r}; one of: {', '.join(PAGES)}")
        self.config = config
        self.config_path = config_path
        """The configuration file the Settings page reads and changes (#117); ``None``:
        it only says so."""
        self.page = page
        self.idle_seconds = config.ui.idle_minutes * 60.0 if idle_seconds is None else idle_seconds
        self.request_timeout = request_timeout
        self.request_deadline = request_deadline
        self._grace = min(close_grace, request_timeout)
        self.end_linger = end_linger
        self._searches: OrderedDict[str, tuple[str, dict[str, list[str]]]] = OrderedDict()
        self._outcomes: OrderedDict[str, tuple[str, actions.Outcome]] = OrderedDict()
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
                        self._turn(selector, listener, heads, tick)
                    if reason in ("ended", "stolen"):
                        # The page that says so asks for its stylesheet next: serve
                        # it (there is no session any more) unless told to stop.
                        until = time.monotonic() + self.end_linger
                        while time.monotonic() < until and self._stops.empty():
                            self._turn(selector, listener, heads, min(tick, 0.05))
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

    def _turn(
        self,
        selector: selectors.BaseSelector,
        listener: socket.socket,
        heads: _Heads,
        tick: float,
    ) -> None:
        for key, _ in selector.select(tick):
            if key.fileobj is listener:
                self._accept(heads)
            elif key.fileobj is self._wake_r:
                self._drain_wake()
            else:
                heads.readable(key.fileobj)
        heads.expire()
        self._server.drop_late(self.request_deadline)

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
        prefix = f"{HANDOFF_PREFIX}{os.getpid()}-"  # so a sweep can tell it is ours
        folder = Path(tempfile.mkdtemp(prefix=prefix, dir=handoff_root()))
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
            self._searches.clear()
            self._outcomes.clear()

    def live(self) -> bool:
        """Whether a session is going on (in some browser)."""
        with self._lock:
            return self._session is not None

    def save_search(self, session: Session, fields: dict[str, list[str]]) -> str:
        """Keep a search form for ``GET /search?r=<id>``; its random id."""
        key = secrets.token_urlsafe(12)
        with self._lock:
            self._searches[key] = (session.id, fields)
            while len(self._searches) > MAX_SAVED_SEARCHES:
                self._searches.popitem(last=False)
        return key

    def saved_search(self, session: Session, key: str) -> dict[str, list[str]] | None:
        """A search this session kept, or ``None`` (unknown, dropped, another session)."""
        with self._lock:
            found = self._searches.get(key)
            if found is None or not same(found[0], session.id):
                return None
            self._searches.move_to_end(key)
            return found[1]

    def keep_outcome(self, session: Session, outcome: actions.Outcome) -> str:
        """Keep what an action did for the page it redirects to (``?n=<id>``)."""
        key = secrets.token_urlsafe(12)
        with self._lock:
            self._outcomes[key] = (session.id, outcome)
            while len(self._outcomes) > MAX_SAVED_SEARCHES:
                self._outcomes.popitem(last=False)
        return key

    def kept_outcome(self, session: Session, key: str) -> actions.Outcome | None:
        """An outcome this session kept, or ``None`` (unknown, dropped, another session)."""
        with self._lock:
            found = self._outcomes.get(key)
            if found is None or not same(found[0], session.id):
                return None
            return found[1]

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
    _posted = b""
    _guarded = False

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
            self._guarded = True  # from here on, a refusal may be a page
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
            if self._guarded and reject.code in PROBLEMS and self._wants_html():
                self._problem(reject.code)
            else:
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
            return parse_qs(body.decode("utf-8", "replace"), max_num_fields=MAX_FORM_FIELDS)
        except ValueError:
            return None

    def _wants_html(self) -> bool:
        """A browser opening a page (not a script asking for JSON or a file)."""
        return "text/html" in self.headers.get("Accept", "")

    def _problem(self, code: int) -> None:
        """A refusal as a page with the menu and one sentence on what to do. Never
        says why in detail: the status and a general hint only."""
        session = self._session()
        text = PROBLEMS[code]
        if code == HTTPStatus.FORBIDDEN:
            text = STALE_FORM if session is not None else STALE_SESSION
        csrf = None if session is None else session.csrf
        self._reply(*views.problem_page(code, text, csrf), _HTML)

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
        if path == "/settings/restore":
            if session is None:
                self._ended(HTTPStatus.FORBIDDEN)
                return
            try:
                code, body = settings.restore_page(
                    ui.config_path, session.csrf, urlsplit(self.path).query
                )
            except views.BadRequest as exc:
                raise _Reject(HTTPStatus.BAD_REQUEST) from exc
            self._reply(code, body, _HTML)
            return
        if path in ACTION_PAGES:
            if session is None:
                self._ended(HTTPStatus.FORBIDDEN)
            else:
                self._action_page(session, path, urlsplit(self.path).query)
            return
        if path == "/" or path[1:] in PAGES:
            query = urlsplit(self.path).query
            if session is None:
                self._ended(HTTPStatus.FORBIDDEN)
            elif path == "/":
                self._redirect(f"/{ui.page}")
            elif path == "/search":
                self._search_page(session, query)
            elif path == "/due":
                self._due_page(session, query)
            elif path == "/settings":
                self._settings_page(session, query)
            elif path[1:] in VIEWS:
                try:
                    code, body = VIEWS[path[1:]](ui.config, session.csrf, urlsplit(self.path).query)
                except views.BadRequest as exc:
                    raise _Reject(HTTPStatus.BAD_REQUEST) from exc
                self._reply(code, body, _HTML)
            else:
                if query:
                    raise _Reject(HTTPStatus.BAD_REQUEST)  # a static page takes nothing
                page = ui.pages[path[1:]].replace("{{csrf}}", session.csrf)
                self._reply(HTTPStatus.OK, page.encode(), _HTML)
            return
        raise _Reject(HTTPStatus.NOT_FOUND)

    def _search_page(self, session: Session, query: str) -> None:
        """``GET /search``: the empty form, or a search kept by ``POST /search``
        (``?r=<id>``): the words never go in the address."""
        ui = self.server.ui
        try:
            params = views.query_params(query, frozenset({"r"}))
            key = views.one_choice(params, "r")
            fields = None if key is None else ui.saved_search(session, key)
            notice = views.SEARCH_GONE if key is not None and fields is None else None
            code, body = views.search_page(ui.config, session.csrf, fields, notice=notice)
        except views.BadRequest as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        self._reply(code, body, _HTML)

    def _due_page(self, session: Session, query: str) -> None:
        """``GET /due``, and with ``?n=<id>`` what Mark done or Open again did."""
        ui = self.server.ui
        try:
            params = views.query_params(query, frozenset({*views.DUE_PARAMS, "n"}))
            key = views.one_choice(params, "n")
            params.pop("n", None)
            outcome = None if key is None else ui.kept_outcome(session, key)
            notice = review.outcome_notice(outcome, key is not None and outcome is None)
            rest = urlencode(params, doseq=True)
            code, body = views.due_page(ui.config, session.csrf, rest, notice=notice)
        except views.BadRequest as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        self._reply(code, body, _HTML)

    def _settings_page(self, session: Session, query: str) -> None:
        """``GET /settings``, and with ``?n=<id>`` what the last change did."""
        ui = self.server.ui
        try:
            key = views.one_choice(views.query_params(query, frozenset({"n"})), "n")
        except views.BadRequest as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        outcome = None if key is None else ui.kept_outcome(session, key)
        gone = key is not None and outcome is None
        code, body = settings.settings_page(ui.config_path, session.csrf, outcome, gone=gone)
        self._reply(code, body, _HTML)

    def _action_page(self, session: Session, path: str, query: str) -> None:
        """Review, Accept all and Undo: pages that read only, whose forms post the
        actions. ``?n=<id>`` shows what the last one did (kept on the server)."""
        ui = self.server.ui
        try:
            if path == "/review/accept":
                code, body = review.accept_page(
                    ui.config, session.csrf, query, secret=session.secret
                )
            else:
                known = frozenset({"n"}) if path == "/review" else frozenset({"run", "file", "n"})
                key = views.one_choice(views.query_params(query, known), "n")
                outcome = None if key is None else ui.kept_outcome(session, key)
                gone = key is not None and outcome is None
                if path == "/review":
                    code, body = review.review_page(
                        ui.config, session.csrf, outcome, gone=gone, secret=session.secret
                    )
                else:
                    code, body = review.undo_page(
                        ui.config, session.csrf, query, outcome, gone=gone
                    )
        except views.BadRequest as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        self._reply(code, body, _HTML)

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
        self._posted = body
        route(self, session)

    def _csrf_token(self, body: bytes) -> str | None:
        headers = self.headers.get_all(CSRF_HEADER) or []
        if headers:
            return headers[0] if len(headers) == 1 else None
        fields = self._form(body)
        values = [] if fields is None else fields.get(CSRF_FIELD, [])
        return values[0] if len(values) == 1 else None

    def _search(self, session: Session) -> None:
        """Keep the form and send the browser to ``GET /search?r=<id>`` (post, redirect,
        get): Back and Forward then work, and the words stay out of the address."""
        fields = self._fields()
        try:
            views.SearchForm.parse(fields)  # its fixed choices, before keeping it
        except views.BadRequest as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        fields.pop(CSRF_FIELD, None)
        self._redirect(f"/search?r={self.server.ui.save_search(session, fields)}")

    def _export(self, session: Session) -> None:
        try:
            code, body, kind = views.search_csv(self.server.ui.config, self._fields())
        except views.BadRequest as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        if code != HTTPStatus.OK:  # a page saying why, not a file
            message = body.decode("utf-8").strip()
            self._reply(*views.problem_page(code, message, session.csrf), _HTML)
            return
        headers = [("Content-Disposition", 'attachment; filename="flib-search.csv"')]
        self._reply(code, body, kind, headers)

    def _open(self, session: Session) -> None:
        self._ask_macos(session, reveal=False)

    def _reveal(self, session: Session) -> None:
        self._ask_macos(session, reveal=True)

    def _ask_macos(self, session: Session, *, reveal: bool) -> None:
        code, text = views.open_by_id(
            self.server.ui.config, self._fields(), reveal=reveal, program=OPEN
        )
        if text is None:  # asked: the browser stays on the page it was on
            self._reply(code, b"", _TEXT)
        else:
            self._reply(*views.problem_page(code, text, session.csrf), _HTML)

    # --- actions (#115): each one ends in a redirect to a page that says what it did ---

    def _act(
        self,
        session: Session,
        back: str,
        do: Callable[[], actions.Outcome],
        *,
        prefix: str = "g-",
    ) -> None:
        """Run one action and send the browser to ``back`` with what happened (post,
        redirect, get): reload, Back and Forward never do it again. A form the pages
        never send is a 400; a refusal is an outcome like any other."""
        try:
            outcome = do()
        except actions.Malformed as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        except actions.Refused as exc:
            outcome = exc.outcome
        key = self.server.ui.keep_outcome(session, outcome)
        anchor = f"#{prefix}{outcome.anchor}" if outcome.anchor else ""
        self._redirect(f"{back}{'&' if '?' in back else '?'}n={key}{anchor}")

    def _review_file(self, session: Session) -> None:
        fields = self._fields()

        def do() -> actions.Outcome:
            file_id, pin = actions.an_id(fields), actions.a_pin(fields)
            category = actions.one(fields, "category", required=False) or None
            name = (actions.one(fields, "name", required=False) or "").strip() or None
            if name is not None and len(name) > actions.MAX_NAME_CHARS:
                raise actions.Malformed("name")
            if category is None and name is None:
                raise actions.Refused("Pick a category or type a new name; nothing was done.")
            ui = self.server.ui
            return actions.fix_one(
                ui.config, file_id, pin, category=category, name=name, secret=session.secret
            )

        self._act(session, "/review", do)

    def _review_accept(self, session: Session) -> None:
        fields = self._fields()

        def do() -> actions.Outcome:
            key = actions.a_group(actions.one(fields, "g"))
            confirmed = actions.ids(actions.one(fields, "ids") or "")
            pin = actions.a_pin(fields)
            config = self.server.ui.config
            outcome = actions.accept_group(config, session.secret, key, confirmed, pin)
            return dataclasses.replace(outcome, anchor=key)

        self._act(session, "/review", do)

    def _undo(self, session: Session) -> None:
        fields = self._fields()

        def do() -> actions.Outcome:
            run, file = fields.get("run"), fields.get("file")
            if (run is None) == (file is None):
                raise actions.Malformed("run or file")
            pin = actions.a_pin(fields)
            config = self.server.ui.config
            if run is not None:
                return actions.undo_one_run(config, actions.an_id(fields, "run"), pin)
            return actions.undo_one_file(config, actions.an_id(fields, "file"), pin)

        self._act(session, "/undo", do)

    def _settle(self, session: Session) -> None:
        fields = self._fields()

        def do() -> actions.Outcome:
            file_id, pin = actions.an_id(fields), actions.a_pin(fields)
            done = actions.one(fields, "done")
            if done not in ("1", "0"):
                raise actions.Malformed("done")
            config = self.server.ui.config
            return actions.settle_one(config, file_id, pin, done=done == "1")

        try:  # back to the window the list was in
            window = views.due_window(fields)
        except views.BadRequest as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        self._act(session, f"/due?{window}" if window else "/due", do)

    # --- settings (#117): each change ends in a redirect to the page, like the actions ---

    def _change_settings(self, session: Session, change: settings.Change) -> None:
        """Run one settings change; the server goes on with the configuration it saved."""
        ui = self.server.ui
        fields = self._fields()

        def do() -> actions.Outcome:
            if ui.config_path is None:
                raise actions.Refused(settings.NO_PATH)
            outcome, saved = change(ui.config, ui.config_path, fields)
            if saved is not None:
                ui.config = saved
            return outcome

        self._act(session, "/settings", do, prefix="s-")

    def _settings_save(self, session: Session) -> None:
        self._change_settings(session, settings.save)

    def _settings_schedule(self, session: Session) -> None:
        self._change_settings(session, settings.schedule)

    def _settings_restore(self, session: Session) -> None:
        self._change_settings(session, settings.restore)

    def _settings_open(self, session: Session) -> None:
        """Open the configuration file in the default text editor: the server's own path,
        never one from the form."""
        problem = settings.open_in_editor(self.server.ui.config_path, OPEN, views.run_open)
        if problem is None:
            self._reply(HTTPStatus.NO_CONTENT, b"", _TEXT)
        else:
            self._reply(*views.problem_page(HTTPStatus.BAD_GATEWAY, problem, session.csrf), _HTML)

    def _fields(self) -> dict[str, list[str]]:
        """The form ``_post`` already read (and found the CSRF token in)."""
        return self._form(self._posted) or {}

    def _end_session(self, session: Session) -> None:
        ui = self.server.ui
        ui.end_session()
        expired = f"{ui.cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        self._ended(HTTPStatus.OK, [("Set-Cookie", expired)])  # never "elsewhere": ended
        ui.stop("ended")

    # --- replies ---

    def _ended(self, code: int, headers: Iterable[tuple[str, str]] = ()) -> None:
        """The "session ended" page, or, while a session goes on in another browser
        or window, the page that says so (and how to move it here)."""
        ui = self.server.ui
        page = ui.pages["elsewhere" if ui.live() else "ended"]
        self._reply(code, page.encode(), _HTML, headers)

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


PROBLEMS: dict[int, str] = {
    HTTPStatus.BAD_REQUEST: "This address or form asks for something the page does not "
    "offer. Go back, or open the page from the menu.",
    HTTPStatus.FORBIDDEN: "",  # STALE_FORM or STALE_SESSION
    HTTPStatus.NOT_FOUND: "There is no such page here. Use the menu above.",
}
"""Refusals a browser gets as a page (``Accept: text/html``), with one sentence each."""
STALE_FORM = (
    "This form is from an earlier page or an earlier flib ui session. Reload the "
    "page and try again."
)
STALE_SESSION = (
    "This page is from an earlier flib ui session, or that session has ended. Run "
    "`flib ui` in Terminal for a new one."
)

ACTION_PAGES = frozenset({"/review", "/review/accept", "/undo"})
"""Pages whose forms post actions (#115); built per request, they only read."""

PostRoute = Callable[[_Handler, Session], None]
POST_ROUTES: dict[str, PostRoute] = {
    "/session/end": _Handler._end_session,
    "/search": _Handler._search,
    "/search/export": _Handler._export,
    "/files/open": _Handler._open,
    "/files/reveal": _Handler._reveal,
    "/review/file": _Handler._review_file,
    "/review/accept": _Handler._review_accept,
    "/undo": _Handler._undo,
    "/due/settle": _Handler._settle,
    "/settings/save": _Handler._settings_save,
    "/settings/schedule": _Handler._settings_schedule,
    "/settings/restore": _Handler._settings_restore,
    "/settings/open": _Handler._settings_open,
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
    config_path: Path | None = None,
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

    minutes = _minutes(config.ui.idle_minutes)
    ending = f"It ends after {minutes} without use, with End session, or with Ctrl-C."
    try:
        with ui_lock(config.database):
            sweep_handoffs(handoff_root())
            ui = Ui(config, page=page, port=port, on_event=on_event, config_path=config_path)
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
            echo("flib ui ended: " + ENDED[reason].format(minutes=minutes) + ".")
            return reason
    except AlreadyRunningError as exc:
        echo(
            f"{exc}. Use the browser tab it opened, or end it there (End session) or "
            "with Ctrl-C in its terminal, then run `flib ui` again; it also ends on its "
            f"own after {minutes} without use."
        )
        return "running"


def _minutes(count: int) -> str:
    return "1 minute" if count == 1 else f"{count} minutes"


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
