"""The ``flib ui`` server: loopback only, one session, gone when idle (#113).

How a session starts. The server binds ``127.0.0.1`` on a free port and makes
a random *launch token*; ``flib ui`` prints ``/launch?token=...`` and asks
macOS to open it (``OPEN``, swapped for a fake in every test). The first
request with that token, compared in constant time, burns it and starts the
only session there will be: the reply is a redirect to the page, without the
token, that sets an ``HttpOnly``, ``SameSite=Strict`` cookie named after the
port. The link never works again; a stale link, a missing or wrong cookie or
an ended session get the "session ended" page.

Every request, before anything else:

- the request line and headers are size-capped, and a slow client times out;
- ``Host`` must be exactly ``127.0.0.1:<port>`` or ``localhost:<port>`` (421
  otherwise), which defeats DNS rebinding;
- ``Sec-Fetch-Site``, when the browser sends it, must say same-origin (or
  ``none``, a typed or opened address, for a GET);
- only GET and POST are served. A POST must come from the exact loopback
  origin (``Origin``, else ``Sec-Fetch-Site: same-origin``), carry a small
  body with a length, a valid session cookie and the session's CSRF token
  (form field ``csrf`` or header ``X-Flib-CSRF``).

Static files come from an in-memory map of packaged resources, looked up by
exact path, so no request can reach the file system. Every response carries
a strict Content-Security-Policy (no inline script or style), ``nosniff``,
``no-referrer`` and ``no-store``. Error replies say only the status. Nothing
is logged: a request line can hold the launch token.

The server ends after ``ui.idle_minutes`` without a request, on "End
session" (a POST) or on SIGINT/SIGTERM. One ``flib ui`` runs at a time
(``lock.ui_lock``); a second one says so and exits, since only the first knows
its port and token. The clock, the idle poll and the timeouts are injectable
for tests.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import queue
import secrets
import selectors
import signal
import socket
import socketserver
import sqlite3
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib import resources
from types import FrameType
from typing import Any
from urllib.parse import parse_qs, urlsplit

from file_librarian import status as status_mod
from file_librarian.config import Config
from file_librarian.lock import AlreadyRunningError, ui_lock
from file_librarian.ui import api

OPEN = "/usr/bin/open"
"""Opens the launch link in the default browser; tests swap in a fake."""
HOST = "127.0.0.1"
PAGES = ("status",)
"""Pages ``--open`` can name; Review, Search, Activity, Due and Settings come later."""

MAX_THREADS = 8
"""Requests handled at once; one more gets 503 and is closed."""
REQUEST_TIMEOUT = 10.0
"""Seconds a client may take on each read or write before it is dropped."""
MAX_REQUEST_LINE = 4096
MAX_HEADER_BYTES = 16 * 1024
MAX_BODY = 16 * 1024
IDLE_POLL_SECONDS = 1.0
CLOSE_GRACE = 1.0
"""Seconds a request in progress may take to finish once the server ends."""

CSRF_FIELD = "csrf"
CSRF_HEADER = "X-Flib-CSRF"
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
}


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
    """One server, its one session and its idle timer. ``start`` serves in threads."""

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
        close_grace: float = CLOSE_GRACE,
    ) -> None:
        if page not in PAGES:
            raise UiError(f"no page {page!r}; one of: {', '.join(PAGES)}")
        self.config = config
        self.page = page
        self.idle_seconds = config.ui.idle_minutes * 60.0 if idle_seconds is None else idle_seconds
        self.request_timeout = request_timeout
        self._grace = min(close_grace, request_timeout)
        self.assets = load_assets()
        self.pages = load_pages()
        self.reason: str | None = None
        self._clock = clock
        self._poll = poll_seconds
        self._lock = threading.Lock()
        self._launch: str | None = secrets.token_urlsafe(32)
        self._session: Session | None = None
        self._last = clock()
        self._stops: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
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
        self.launch_url = f"http://{HOST}:{self.port}/launch?token={self._launch}"
        self.hosts = frozenset({f"{HOST}:{self.port}", f"localhost:{self.port}"})
        self.origins = frozenset(f"http://{host}" for host in self.hosts)
        self.cookie_name = f"flib_ui_{self.port}"

    # --- lifecycle ---
    #
    # One thread, "flib-ui", accepts connections, checks the idle timer and, when
    # asked to stop, closes everything and joins every request thread before
    # ``wait`` returns. ``stop`` takes no lock (a reentrant queue and a byte on a
    # socket pair), so it is safe from a signal handler, even one that interrupts
    # another; a handler that took a lock could deadlock the main thread.

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="flib-ui", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        listener = self._server.socket
        listener.setblocking(False)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(listener, selectors.EVENT_READ)
                selector.register(self._wake_r, selectors.EVENT_READ)
                while (reason := self._next_reason()) is None:
                    for key, _ in selector.select(self._poll):
                        if key.fileobj is listener:
                            self._accept()
                        else:
                            self._drain_wake()
        except Exception:  # the server ends rather than hang
            reason = "closed"
        self.reason = reason
        try:
            self._server.server_close()
            self._server.close_connections(self._grace)
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

    def _accept(self) -> None:
        try:
            request, address = self._server.get_request()
        except OSError:  # gone before it was accepted, or nothing to accept
            return
        self._server.process_request(request, address)

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
        """End the server and wait until no thread or socket of it is left."""
        if self._thread is None:
            self.reason = self.reason or "closed"
            self._server.server_close()
            self._done.set()
        else:
            self.stop("closed")
            self._thread.join()
        self._wake_r.close()
        self._wake_w.close()

    # --- session ---

    def touch(self) -> None:
        with self._lock:
            self._last = self._clock()

    def redeem(self, token: str) -> Session | None:
        """The launch token, once: the session it starts, or ``None``."""
        with self._lock:
            if self._launch is None or not same(token, self._launch):
                return None
            self._launch = None
            self._session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32))
            return self._session

    def session(self, session_id: str) -> Session | None:
        with self._lock:
            current = self._session
        if current is None or not same(session_id, current.id):
            return None
        return current


class _HttpServer(HTTPServer):
    """Accepts nothing by itself: ``Ui._serve`` accepts, this runs each request in a
    thread of its own, at most ``max_threads`` at once, and keeps track of them."""

    allow_reuse_address = False
    allow_reuse_port = False
    request_queue_size = 16

    def __init__(self, ui: Ui, port: int, max_threads: int) -> None:
        check_bind(HOST)
        self.ui = ui
        self._slots = threading.BoundedSemaphore(max_threads)
        self._active: dict[socket.socket, threading.Thread] = {}
        self._active_lock = threading.Lock()
        super().__init__((HOST, port), _Handler)

    def server_bind(self) -> None:
        # Not HTTPServer.server_bind: it looks the host name up (DNS) for nothing.
        socketserver.TCPServer.server_bind(self)
        host, port = self.socket.getsockname()[:2]
        check_bind(host)
        self.server_name = host
        self.server_port = port

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                # Never blocks the accepting thread: the reply fits the socket buffer.
                request.setblocking(False)
                request.send(
                    b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                pass
            self.shutdown_request(request)
            return
        thread = threading.Thread(
            target=self._one,
            args=(request, client_address),
            name="flib-ui-request",
            daemon=True,
        )
        with self._active_lock:
            self._active[request] = thread
        thread.start()

    def _one(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:  # noqa: S110 - a dropped client; nothing is logged
            pass
        finally:
            # Under the lock, so close_connections never shuts a socket whose
            # descriptor was just closed and reused.
            with self._active_lock:
                del self._active[request]
                self.shutdown_request(request)
            self._slots.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Never print a traceback: nothing is logged."""

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
            return list(self._active.values())


class _Reject(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Handler(BaseHTTPRequestHandler):
    server: _HttpServer
    protocol_version = "HTTP/1.0"
    server_version = "flib"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.server.ui.request_timeout)

    def log_message(self, format: str, *args: Any) -> None:
        """Never log: a request line can hold the launch token."""

    def version_string(self) -> str:
        return "flib"

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Every error says its status and nothing else."""
        self.close_connection = True
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
            self._guard()
            self.server.ui.touch()
            if self.command == "POST":
                self._post()
            else:
                self._get()
        except _Reject as reject:
            self.send_error(reject.code)

    def _guard(self) -> None:
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
        site = self.headers.get("Sec-Fetch-Site")
        allowed_sites = ("same-origin",) if self.command == "POST" else ("same-origin", "none")
        if site is not None and site not in allowed_sites:
            raise _Reject(HTTPStatus.FORBIDDEN)
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin is None and site != "same-origin":
                raise _Reject(HTTPStatus.FORBIDDEN)
            if origin is not None and origin not in ui.origins:
                raise _Reject(HTTPStatus.FORBIDDEN)

    def _session(self) -> Session | None:
        name = self.server.ui.cookie_name
        for header in self.headers.get_all("Cookie") or []:
            for part in header.split(";"):
                key, sep, value = part.strip().partition("=")
                if sep and key == name:
                    return self.server.ui.session(value)
        return None

    # --- GET ---

    def _get(self) -> None:
        ui = self.server.ui
        path = urlsplit(self.path).path
        if path in ui.assets:
            body, kind = ui.assets[path]
            self._reply(HTTPStatus.OK, body, kind)
            return
        if path == "/launch":
            self._launch(urlsplit(self.path).query)
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

    def _launch(self, query: str) -> None:
        ui = self.server.ui
        if self._session() is not None:
            self._redirect(f"/{ui.page}")
            return
        try:
            tokens = parse_qs(query, max_num_fields=4).get("token", [])
        except ValueError as exc:
            raise _Reject(HTTPStatus.BAD_REQUEST) from exc
        session = ui.redeem(tokens[0]) if len(tokens) == 1 else None
        if session is None:
            self._ended(HTTPStatus.FORBIDDEN)
            return
        cookie = f"{ui.cookie_name}={session.id}; Path=/; HttpOnly; SameSite=Strict"
        self._redirect(f"/{ui.page}", [("Set-Cookie", cookie)])

    def _status(self) -> None:
        try:
            data = api.status(self.server.ui.config)
        except status_mod.StatusError as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
        except sqlite3.Error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "cannot read the index"})
        else:
            self._json(HTTPStatus.OK, data)

    # --- POST ---

    def _post(self) -> None:
        if self.headers.get("Transfer-Encoding") is not None:
            raise _Reject(HTTPStatus.BAD_REQUEST)
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise _Reject(HTTPStatus.LENGTH_REQUIRED)
        if not raw.isdigit():
            raise _Reject(HTTPStatus.BAD_REQUEST)
        if int(raw) > MAX_BODY:
            raise _Reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        body = self.rfile.read(int(raw))
        session = self._session()
        if session is None:
            raise _Reject(HTTPStatus.FORBIDDEN)
        token = self._csrf_token(body)
        if token is None or not same(token, session.csrf):
            raise _Reject(HTTPStatus.FORBIDDEN)
        if urlsplit(self.path).path == "/session/end":
            self._ended(HTTPStatus.OK)
            self.server.ui.stop("ended")
            return
        raise _Reject(HTTPStatus.NOT_FOUND)

    def _csrf_token(self, body: bytes) -> str | None:
        header = self.headers.get(CSRF_HEADER)
        if header is not None:
            return header
        kind = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if kind != "application/x-www-form-urlencoded":
            return None
        try:
            fields = parse_qs(body.decode("utf-8", "replace"), max_num_fields=8)
        except ValueError:
            return None
        values = fields.get(CSRF_FIELD, [])
        return values[0] if len(values) == 1 else None

    # --- replies ---

    def _ended(self, code: int) -> None:
        self._reply(code, self.server.ui.pages["ended"].encode(), _HTML)

    def _redirect(self, location: str, headers: Iterable[tuple[str, str]] = ()) -> None:
        self._reply(HTTPStatus.SEE_OTHER, b"", _TEXT, [("Location", location), *headers])

    def _json(self, code: int, data: object) -> None:
        self._reply(code, json.dumps(data, ensure_ascii=False).encode(), _JSON)

    def _reply(
        self, code: int, body: bytes, kind: str, headers: Iterable[tuple[str, str]] = ()
    ) -> None:
        self.send_response(code)
        for name, value in (*SECURITY_HEADERS, ("Content-Type", kind), *headers):
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


# --- flib ui ---------------------------------------------------------------------------


def open_url(url: str) -> str | None:
    """Ask macOS to open ``url``; why it failed, or ``None``. Never says the URL."""
    try:
        # A fixed binary; the URL is one argument, never parsed by a shell.
        result = subprocess.run(  # noqa: S603
            [OPEN, url], capture_output=True, timeout=15, check=False
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
) -> str:
    """``flib ui``: serve until idle, ended or interrupted; why it ended.

    Raises ``UiError`` when it cannot start. Returns ``"running"`` without
    serving when another ``flib ui`` holds the lock.
    """
    try:
        with ui_lock(config.database):
            ui = Ui(config, page=page, port=port)
            try:
                with _signals(ui):
                    ui.start()
                    echo(f"flib ui: {ui.launch_url}")
                    echo(
                        "The link works once. The interface ends after "
                        f"{config.ui.idle_minutes} idle minutes, with End session, or with "
                        "Ctrl-C."
                    )
                    if browser and (problem := open_url(ui.launch_url)) is not None:
                        echo(
                            f"warning: could not open the browser ({problem}); open the link above."
                        )
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
