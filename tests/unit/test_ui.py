"""``flib ui`` (#113): the server, its session and every security rule.

Each server runs in-process on an ephemeral 127.0.0.1 port (``loopback_network``);
nothing else is reachable, and the browser is the fake ``open`` of conftest.py.
Each rule has a request that breaks it.
"""

from __future__ import annotations

import errno
import faulthandler
import hmac
import json
import os
import re
import signal
import socket
import sqlite3
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config, ConfigError, parse_config
from file_librarian.lock import ui_lock
from file_librarian.providers.fake import FakeProvider
from file_librarian.status import StatusError
from file_librarian.ui import api, markup
from file_librarian.ui import server as ui_server
from file_librarian.ui.server import CSP, Ui, UiError, check_bind, load_assets, load_pages
from file_librarian.ui.server import handoff_root as real_handoff_root  # before conftest
from file_librarian.ui.server import names_resolve as real_names_resolve  # before conftest
from tests._helpers import REPO_ROOT, UiClient, answer, fake_config, put, wait_for, write_config

runner = CliRunner()

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
"""Written out here, not imported: a header dropped from the server fails the tests."""


_TIMEOUT_REPLY = ui_server._plain_reply(408)


def raw_headers(reply: bytes) -> dict[str, str]:
    """The status line (as ``""``) and headers of a raw reply."""
    head = reply.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
    found = {"": head[0]}
    for line in head[1:]:
        name, _, value = line.partition(": ")
        found[name] = value
    return found


HANG_GUARD_SECONDS = 20
STOLEN = "This session was ended because its link was opened twice"
"""What every page says after a theft of the launch link (#127)."""


@pytest.fixture(autouse=True)
def no_hang() -> Iterator[None]:
    """A guard, not a timing: every test here takes well under a second. One that
    is stuck prints every thread's stack and exits instead of sitting until CI
    times it out."""
    faulthandler.dump_traceback_later(HANG_GUARD_SECONDS, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def nothing_left_behind(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test leaves a listening socket or a server thread behind: ``close`` and
    every way the server ends join all of them before returning."""
    servers: list[ui_server._HttpServer] = []

    class Recorded(ui_server._HttpServer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            servers.append(self)

    monkeypatch.setattr(ui_server, "_HttpServer", Recorded)
    before = {t for t in threading.enumerate() if t.name.startswith("flib-ui")}
    yield
    for server in servers:
        assert server.socket.fileno() == -1, "a listening socket was left open"
        held = getattr(server.ui, "_listener6", None)  # unset when the Ui never started
        assert held is None or held.fileno() == -1, "the ::1 socket was left open"
        assert server.ui._wake_r.fileno() == -1 and server.ui._wake_w.fileno() == -1
        assert server._active == {}
        assert server.ui._handoff is None, "a handoff file was left behind"
    left = [
        t.name
        for t in threading.enumerate()
        if t.name.startswith("flib-ui") and t not in before and t.is_alive()
    ]
    assert left == [], f"threads left running: {left}"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def events() -> list[str]:
    """What the ``ui`` fixture's server told ``flib ui`` (``on_event``)."""
    return []


@pytest.fixture
def ui(loopback_network: None, cfg: Config, events: list[str]) -> Iterator[Ui]:
    server = Ui(cfg, poll_seconds=0.01, on_event=events.append)
    server.start()
    yield server
    server.close()


@pytest.fixture
def client(ui: Ui) -> UiClient:
    """A client with a session: the launch link was opened."""
    c = UiClient(ui.port, ui.host_name)
    assert c.launch(ui.launch_url).status == 303
    return c


FAILURE_SECONDS = 10
"""How long a test waits for a signal before it fails; never what it waits on.
Under ``HANG_GUARD_SECONDS``, so the failure says what never happened."""


class CountedSlots(threading.BoundedSemaphore):
    """The server's request slots, counted, so a test can wait until each is free.

    A client has its whole reply before the request thread gives its slot back;
    the next request can come in between and be turned away with 503 (#129).
    """

    def __init__(self, value: int) -> None:
        super().__init__(value)
        self._changed = threading.Condition()
        self._taken = 0

    def acquire(self, blocking: bool = True, timeout: float | None = None) -> bool:
        got = super().acquire(blocking, timeout)
        if got:
            with self._changed:
                self._taken += 1
        return got

    def release(self, n: int = 1) -> None:
        with self._changed:
            super().release(n)
            self._taken -= n
            self._changed.notify_all()

    def wait_all_free(self) -> None:
        with self._changed:
            free = self._changed.wait_for(lambda: self._taken == 0, FAILURE_SECONDS)
        assert free, "a request thread never gave its slot back"


@pytest.fixture
def counted_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every server made from here on counts its request slots (``all_slots_free``)."""

    class Counted(ui_server._HttpServer):  # the recording one of ``nothing_left_behind``
        def __init__(self, ui: Ui, port: int, max_threads: int) -> None:
            super().__init__(ui, port, max_threads)
            self._slots = CountedSlots(max_threads)

    monkeypatch.setattr(ui_server, "_HttpServer", Counted)


def all_slots_free(ui: Ui) -> None:
    """Wait until every request before now has given its thread back."""
    slots = ui._server._slots
    assert isinstance(slots, CountedSlots), "the test needs the counted_slots fixture"
    slots.wait_all_free()


class Busy:
    """Makes ``/api/v1/status`` wait until ``release``: a request thread in use.

    Needs the ``counted_slots`` fixture. Nothing here is timed: ``hold`` returns
    once the held request is inside its handler, holding its slot."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.go = threading.Event()
        self.inside = threading.Event()
        self.statuses: list[int] = []
        real = api.status

        def held(config: Config) -> object:
            self.inside.set()
            assert self.go.wait(FAILURE_SECONDS), "never released"
            return real(config)

        monkeypatch.setattr(api, "status", held)

    def hold(self, ui: Ui, client: UiClient) -> threading.Thread:
        all_slots_free(ui)  # or the held request could be the one turned away
        self.inside.clear()

        def get() -> None:
            self.statuses.append(client.get("/api/v1/status").status)

        thread = threading.Thread(target=get, daemon=True)
        thread.start()
        assert self.inside.wait(FAILURE_SECONDS), "the held request never reached its handler"
        return thread

    def release(self, *threads: threading.Thread) -> None:
        self.go.set()
        for thread in threads:
            thread.join(FAILURE_SECONDS)
            assert not thread.is_alive(), "a held request never ended"


def token_of(ui: Ui) -> str:
    return ui.launch_url.split("token=", 1)[1]


def assert_running(ui: Ui) -> None:
    assert ui.wait(0.05) is None


# --- binding ---------------------------------------------------------------------------


WILDCARD = "0.0.0.0"  # noqa: S104 - what the server must refuse


@pytest.mark.parametrize("host", [WILDCARD, "::", "192.168.1.10", "10.0.0.1", "localhost", ""])
def test_never_binds_anything_but_loopback(host: str) -> None:
    with pytest.raises(UiError, match="not a loopback address"):
        check_bind(host)


def test_loopback_addresses_are_allowed() -> None:
    check_bind("127.0.0.1")
    check_bind("::1")


def test_the_server_listens_on_loopback_only(ui: Ui) -> None:
    """127.0.0.1, and ``::1`` on the same port: a browser may try ``::1`` first for a
    ``.localhost`` name, and another program must not be the one listening there."""
    assert ui._server.socket.getsockname()[0] == "127.0.0.1"
    assert ui.port > 0
    assert ui._listener6 is not None
    assert ui._listener6.getsockname()[:2] == ("::1", ui.port)
    assert ui._listener6.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
    assert ui.launch_url.startswith(f"http://{ui.host_name}:{ui.port}/launch?token=")


def test_a_request_to_the_v6_address_is_served(client: UiClient, ui: Ui) -> None:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(("::1", ui.port))
        cookie = f"{ui.cookie_name}={client.cookies[ui.cookie_name]}"
        head = f"GET /status HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\nCookie: {cookie}\r\n"
        sock.sendall(head.encode() + b"\r\n")
        reply = b""
        while chunk := sock.recv(65536):
            reply += chunk
    assert reply.startswith(b"HTTP/1.0 200 ") and b"<h1>Status</h1>" in reply


def test_a_port_another_program_holds_on_v6_is_never_used(
    monkeypatch: pytest.MonkeyPatch, loopback_network: None, cfg: Config
) -> None:
    """Whoever holds ``[::1]:<port>`` would get the browser's requests, cookie and all:
    a free port is tried again until both addresses are the server's."""
    real = ui_server._hold_v6
    tried: list[int] = []

    def taken_once(port: int) -> socket.socket | None:
        tried.append(port)
        if len(tried) == 1:
            raise OSError(errno.EADDRINUSE, "Address already in use")
        return real(port)

    monkeypatch.setattr(ui_server, "_hold_v6", taken_once)
    server = Ui(cfg)
    try:
        assert len(tried) == 2 and server.port == tried[1] != tried[0]
    finally:
        server.close()
    # Every attempt taken: an error, and nothing left listening.
    monkeypatch.setattr(
        ui_server, "_hold_v6", lambda port: (_ for _ in ()).throw(OSError(errno.EADDRINUSE, "x"))
    )
    with pytest.raises(UiError, match="cannot listen"):
        Ui(cfg)
    # Another failure is not retried: said as it is, after one attempt.
    tried.clear()

    def refused(port: int) -> socket.socket | None:
        tried.append(port)
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(ui_server, "_hold_v6", refused)
    with pytest.raises(UiError, match=r"cannot listen .*: Permission denied$"):
        Ui(cfg)
    assert len(tried) == 1


def test_a_port_held_on_v6_is_an_error_when_asked_for(loopback_network: None, cfg: Config) -> None:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as holder:
        holder.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        holder.bind(("::1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        with pytest.raises(
            UiError, match=rf"cannot listen on 127\.0\.0\.1:{port} and \[::1\]:{port}"
        ):
            Ui(cfg, port=port)
        # Without a name, ``::1`` plays no part.
        Ui(cfg, port=port, named=False).close()


def test_a_mac_without_v6_loopback_holds_nothing_there(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``::1`` (an address not on this Mac stands in): nobody can listen there."""
    monkeypatch.setattr(ui_server, "HOST6", "::2")
    assert ui_server._hold_v6(0) is None

    def no_ipv6(*args: Any, **kwargs: Any) -> socket.socket:
        raise OSError(errno.EAFNOSUPPORT, "no IPv6")

    monkeypatch.setattr(socket, "socket", no_ipv6)
    assert ui_server._hold_v6(0) is None


def test_a_v6_socket_that_fails_its_check_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[socket.socket] = []
    real = socket.socket

    def recorded(*args: Any, **kwargs: Any) -> socket.socket:
        made.append(real(*args, **kwargs))
        return made[-1]

    def refuse(host: str) -> None:
        raise UiError("refusing to listen on it: not a loopback address")

    monkeypatch.setattr(socket, "socket", recorded)
    monkeypatch.setattr(ui_server, "check_bind", refuse)
    with pytest.raises(UiError):
        ui_server._hold_v6(0)
    assert [sock.fileno() for sock in made] == [-1]


def test_without_a_name_it_serves_at_127_0_0_1_as_before(
    loopback_network: None, cfg: Config
) -> None:
    server = Ui(cfg, named=False)
    try:
        assert server._listener6 is None
        assert server.host_name == "127.0.0.1"
        assert server.hosts == {f"127.0.0.1:{server.port}"}
        assert server.cookie_name == f"flib_ui_{server.port}"
        assert server.launch_url.startswith(f"http://127.0.0.1:{server.port}/launch?token=")
    finally:
        server.close()


def test_a_wildcard_host_is_refused_before_binding(
    monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    monkeypatch.setattr(ui_server, "HOST", WILDCARD)
    with pytest.raises(UiError, match="not a loopback address"):
        Ui(cfg)


def test_the_bound_address_is_checked_again(
    monkeypatch: pytest.MonkeyPatch, loopback_network: None, cfg: Config
) -> None:
    """``check_bind`` runs on the address asked for and on the one bound."""
    checked: list[str] = []
    real = ui_server.check_bind

    def spy(host: str) -> None:
        checked.append(host)
        real(host)

    monkeypatch.setattr(ui_server, "check_bind", spy)
    Ui(cfg).close()
    assert checked == ["127.0.0.1", "127.0.0.1", "::1"]

    for refused in (2, 3):  # the bound 127.0.0.1, then the bound ::1
        checked.clear()

        def refuse_bound(host: str, refused: int = refused) -> None:
            checked.append(host)
            if len(checked) == refused:
                raise UiError("refusing to listen on it: not a loopback address")

        monkeypatch.setattr(ui_server, "check_bind", refuse_bound)
        with pytest.raises(UiError, match="not a loopback address"):
            Ui(cfg)


def test_a_port_in_use_is_an_error(ui: Ui, cfg: Config) -> None:
    with pytest.raises(UiError, match="cannot listen"):
        Ui(cfg, port=ui.port)


def test_an_unknown_page_is_an_error(cfg: Config) -> None:
    with pytest.raises(UiError, match="no page 'nope'"):
        Ui(cfg, page="nope")


# --- the launch token and the session --------------------------------------------------


def test_the_launch_token_becomes_a_session_cookie(ui: Ui) -> None:
    c = UiClient(ui.port, ui.host_name)
    reply = c.launch(ui.launch_url)
    assert reply.status == 303
    assert reply.headers["Location"] == "/status"  # the token leaves the address bar
    assert reply.headers["Referrer-Policy"] == "same-origin"
    cookie = reply.headers["Set-Cookie"]
    assert cookie.startswith("flib_ui=")
    # Host-only (no Domain): under the session's name, no other host ever gets it.
    assert cookie.endswith(f"; Path=/; Max-Age={ui.cookie_seconds}; HttpOnly; SameSite=Strict")
    assert "domain" not in cookie.lower()
    assert token_of(ui) not in cookie

    page = c.get("/status")
    assert page.status == 200
    assert "<h1>Status</h1>" in page.text
    assert "{{csrf}}" not in page.text
    assert token_of(ui) not in page.text
    assert c.get("/").headers["Location"] == "/status"


def test_the_launch_token_works_once(ui: Ui, events: list[str]) -> None:
    first = UiClient(ui.port, ui.host_name)
    assert first.launch(ui.launch_url).status == 303
    assert events == ["started"]
    stale = UiClient(ui.port, ui.host_name)
    reply = stale.launch(ui.launch_url)
    assert reply.status == 403
    # Its second use is a theft (#127): said so, never a new session.
    assert STOLEN in reply.text
    assert "Set-Cookie" not in reply.headers


def test_after_a_theft_every_page_says_why_and_nothing_is_left_to_take(
    ui: Ui, events: list[str]
) -> None:
    """#127: the server ends on a theft, but first answers for ``stolen_linger`` (a
    signal ends it at once) with the page that says why, to the rightful tab too. The
    thief gets that page and nothing else: no cookie, no session, no second chance."""
    rightful = UiClient(ui.port, ui.host_name)
    assert rightful.launch(ui.launch_url).status == 303
    csrf = rightful.csrf()
    thief = UiClient(ui.port, ui.host_name)
    taken = thief.launch(ui.launch_url)
    assert taken.status == 403 and STOLEN in taken.text
    assert "Set-Cookie" not in taken.headers and thief.cookies == {}
    html = {"Accept": "text/html"}
    for who in (rightful, thief):
        for reply in (
            who.get("/status", headers=html),
            who.get("/search", headers=html),
            who.post_form("/search", {"csrf": csrf, "q": "x"}, html),
            who.post_form("/session/end", {"csrf": csrf}, html),
        ):
            assert reply.status == 403, reply.status
            assert STOLEN in reply.text
            assert "Set-Cookie" not in reply.headers
            assert csrf not in reply.text and token_of(ui) not in reply.text
        css = who.get("/static/app.css")  # the page is styled
        assert css.status == 200 and css.headers["Content-Type"].startswith("text/css")
    assert rightful.get("/api/v1/status").status == 401
    # The token again, and again: no second warning, no session, no other reason.
    assert UiClient(ui.port, ui.host_name).launch(ui.launch_url).status == 403
    assert ui.wait(0) is None  # still answering
    ui.stop("signal")  # Ctrl-C ends the wait for the rightful tab at once
    assert ui.wait(5) == "stolen"
    assert events == ["started", "stolen"]


def test_a_used_token_from_a_client_without_the_session_ends_everything(
    ui: Ui, events: list[str]
) -> None:
    """Whoever presents the used token without the session saw it somewhere: the
    session is dropped (it may be theirs) and the server ends with a warning."""
    first = UiClient(ui.port, ui.host_name)
    first.launch(ui.launch_url)
    session_id = first.cookies[ui.cookie_name]
    assert UiClient(ui.port, ui.host_name).launch(ui.launch_url).status == 403
    assert ui.session(session_id) is None  # dropped before the reply
    ui.stop("closed")  # not the end of the linger: the reason is the first one given
    assert ui.wait(5) == "stolen"
    assert events == ["started", "stolen"]
    assert ui.session(session_id) is None


def test_the_launch_exchange_says_nothing_about_the_token(ui: Ui, events: list[str]) -> None:
    UiClient(ui.port, ui.host_name).launch(ui.launch_url)
    assert events == ["started"]
    assert all(token_of(ui) not in event for event in events)


def test_the_browser_with_the_session_may_open_the_link_again(client: UiClient, ui: Ui) -> None:
    session_id = client.cookies[ui.cookie_name]
    reply = client.launch(ui.launch_url)
    assert reply.status == 303 and reply.headers["Location"] == "/status"
    # No new session: the same cookie, renewed.
    assert reply.headers.get_all("Set-Cookie") == [ui.cookie(session_id)]


def test_each_request_of_the_session_renews_its_cookie(client: UiClient, ui: Ui) -> None:
    """The cookie outlives the idle timer by a minute only, so cookies of earlier
    sessions do not pile up: each request of the session renews it."""
    assert ui.cookie_seconds == int(ui.idle_seconds) + 60
    session_id = client.cookies[ui.cookie_name]
    for reply in (
        client.get("/status"),
        client.get("/static/app.css"),
        client.get("/api/v1/status"),
        client.get("/nope"),
        client.post_form("/search", {"csrf": client.csrf(), "q": "x"}),
    ):
        assert reply.headers.get_all("Set-Cookie") == [ui.cookie(session_id)], reply.status
    # Without the session: nothing to renew, nothing set.
    stranger = UiClient(ui.port, ui.host_name)
    for path in ("/status", "/static/app.css", "/api/v1/status"):
        assert stranger.get(path).headers.get_all("Set-Cookie") is None, path
    # End session expires it, once, rather than renewing it.
    ended = client.post_form("/session/end", {"csrf": client.csrf()})
    assert ended.headers.get_all("Set-Cookie") == [ui.cookie("", seconds=0)]
    assert ui.cookie("", seconds=0).endswith("; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")


def test_the_cookie_lasts_at_least_a_minute(loopback_network: None, cfg: Config) -> None:
    server = Ui(cfg, idle_seconds=0.5)
    try:
        assert server.cookie_seconds == 60
    finally:
        server.close()


@pytest.mark.parametrize(
    "query", ["", "token=", "token=wrong", "tok=X", "token=X&token=X", "token=%FF%FE"]
)
def test_a_wrong_or_missing_token_gets_the_not_started_page(ui: Ui, query: str) -> None:
    """Before the link was opened, the truth (#127): not started yet, use the link; not
    "ended". Nothing of the token, the session or a cookie."""
    query = query.replace("X", token_of(ui))
    reply = UiClient(ui.port, ui.host_name).get(f"/launch?{query}")
    assert reply.status == 403
    assert "This session has not started yet" in reply.text
    assert "This session has ended" not in reply.text
    assert token_of(ui) not in reply.text and "Set-Cookie" not in reply.headers
    # A wrong guess does not burn the real link.
    assert UiClient(ui.port, ui.host_name).launch(ui.launch_url).status == 303


def test_too_many_query_fields_are_refused(ui: Ui) -> None:
    reply = UiClient(ui.port, ui.host_name).get("/launch?" + "&".join(f"a{i}=1" for i in range(10)))
    assert reply.status == 400


def test_the_token_is_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch, ui: Ui) -> None:
    seen = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return bool(real(a, b))

    monkeypatch.setattr(hmac, "compare_digest", spy)
    UiClient(ui.port, ui.host_name).get("/launch?token=guess")
    assert (b"guess", token_of(ui).encode()) in seen


@pytest.mark.parametrize("cookie", [None, "wrong", "OTHER_PORT"])
def test_pages_and_the_api_need_the_session(ui: Ui, cookie: str | None) -> None:
    session = UiClient(ui.port, ui.host_name)
    session.launch(ui.launch_url)
    real = session.cookies[ui.cookie_name]
    c = UiClient(ui.port, ui.host_name)
    if cookie == "wrong":
        c.cookies[ui.cookie_name] = real[:-2] + "xx"
    elif cookie == "OTHER_PORT":
        c.cookies[f"flib_ui_{ui.port + 1}"] = real  # a name this server does not use
    for path in ("/", "/status"):
        reply = c.get(path)
        # The session lives on in the other browser: this one is told so, not "ended".
        assert reply.status == 403 and "is open in another window" in reply.text
        assert "This session has ended" not in reply.text
    api_reply = c.get("/api/v1/status")
    assert api_reply.status == 401
    assert api_reply.json() == {"error": "no session"}


def test_a_planted_cookie_of_the_same_name_does_not_lock_the_user_out(
    client: UiClient, ui: Ui
) -> None:
    """At 127.0.0.1 (no name), cookies are shared by every port: another app may set
    one of this name, and the browser sends both. Under a name nobody else can, but
    the rule is the same."""
    real = client.cookies[ui.cookie_name]
    for header in (
        f"{ui.cookie_name}=planted; {ui.cookie_name}={real}",
        f"{ui.cookie_name}={real}; {ui.cookie_name}=planted",
    ):
        reply = UiClient(ui.port, ui.host_name).get("/status", headers={"Cookie": header})
        assert reply.status == 200, header
    reply = UiClient(ui.port, ui.host_name).get(
        "/status", headers={"Cookie": f"{ui.cookie_name}=planted"}
    )
    assert reply.status == 403


def test_the_session_id_is_compared_in_constant_time(
    monkeypatch: pytest.MonkeyPatch, client: UiClient, ui: Ui
) -> None:
    seen = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return bool(real(a, b))

    monkeypatch.setattr(hmac, "compare_digest", spy)
    session_id = client.cookies[ui.cookie_name]
    assert client.get("/status").status == 200
    assert (session_id.encode(), session_id.encode()) in seen


def test_the_ended_page_is_styled_without_a_session(ui: Ui) -> None:
    c = UiClient(ui.port, ui.host_name)
    assert c.get("/static/app.css").status == 200
    assert c.get("/static/icon.png").status == 200


# --- Host, Origin and fetch metadata ---------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:{port}",
        "{name}:{other}",
        "{name}",
        "{upper}:{port}",
        "{name}.:{port}",
        "x{name}:{port}",
        "{other_name}:{port}",
        "127.0.0.1:{port}",
        "127.0.0.1:{other}",
        "127.0.0.1",
        "localhost",
        "LOCALHOST:{port}",
        "localhost:{port}",
        "127.0.0.1.:{port}",
        "[::1]:{port}",
        "0.0.0.0:{port}",
        "",
    ],
)
def test_a_foreign_host_header_is_misdirected(client: UiClient, ui: Ui, host: str) -> None:
    host = host.format(
        name=ui.host_name,
        upper=ui.host_name.upper(),
        other_name=ui_server.session_name(),
        port=ui.port,
        other=ui.port + 1,
    )
    launch = urlsplit(ui.launch_url)
    for path in ("/status", "/api/v1/status", "/static/app.css", f"{launch.path}?{launch.query}"):
        reply = client.get(path, host=host)
        assert reply.status == 421, (host, path)
        assert reply.body == b"Misdirected Request\n"
    assert client.get("/open?page=review", host=host).status == 421


def test_only_the_sessions_name_is_ever_named(client: UiClient, ui: Ui) -> None:
    """``127.0.0.1`` would share the cookie with every server on loopback; the name is
    the session's own, 128 random bits under ``.localhost``."""
    assert client.get("/status", host=f"{ui.host_name}:{ui.port}").status == 200
    assert ui_server.NAME.fullmatch(ui.host_name)
    assert ui.hosts == {f"{ui.host_name}:{ui.port}"}
    for url in (ui.url, ui.launch_url, ui.origin):
        assert url.startswith(f"http://{ui.host_name}:{ui.port}") and "127.0.0.1" not in url
    assert ui.cookie_name == "flib_ui"


def test_every_session_name_is_new() -> None:
    names = {ui_server.session_name() for _ in range(100)}
    assert len(names) == 100
    assert all(ui_server.NAME.fullmatch(name) for name in names)
    assert not ui_server.NAME.fullmatch("flib-" + "a" * 31 + ".localhost")
    assert not ui_server.NAME.fullmatch("flib-" + "A" * 32 + ".localhost")


def test_two_host_headers_are_misdirected(client: UiClient, ui: Ui) -> None:
    reply = client.raw(
        f"GET /static/app.css HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
        f"Host: evil.example\r\n\r\n".encode()
    )
    assert reply.startswith(b"HTTP/1.0 421 ")


def test_an_absolute_form_request_target_is_refused(client: UiClient, ui: Ui) -> None:
    for target in ("http://evil.example/status", "*"):
        assert client.get(target).status == 400, target
    # http.server itself turns a leading "//" into "/": a path like any other.
    assert client.get("//evil.example/status").status == 404


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_cross_site_request_is_forbidden(client: UiClient, site: str) -> None:
    for path in ("/status", "/api/v1/status", "/static/icon.png"):
        assert client.get(path, headers={"Sec-Fetch-Site": site}).status == 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": None, "Sec-Fetch-Site": None},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://{name}:{other}"},
        {"Origin": "http://{name}:{port}0"},
        {"Origin": "http://{name}:{port}/"},
        {"Origin": "http://127.0.0.1:{port}"},
        {"Origin": "http://localhost:{port}"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "https://{name}:{port}"},
        {"Origin": None, "Sec-Fetch-Site": "none"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_a_post_from_another_origin_is_forbidden(
    client: UiClient, ui: Ui, headers: dict[str, str | None]
) -> None:
    token = client.csrf()
    sent = {
        "Origin": client.origin,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for name, value in headers.items():
        if value is None:
            del sent[name]
        else:
            sent[name] = value.format(name=ui.host_name, port=ui.port, other=ui.port + 1)
    body = f"csrf={token}".encode()
    reply = client.request("POST", "/session/end", body=body, headers=sent)
    assert reply.status == 403
    assert_running(ui)


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "null",
        "http://{name}:{port}0",
        "http://{name}",
        "http://127.0.0.1:{port}",
        "http://127.0.0.1",
    ],
)
def test_any_request_from_another_origin_is_forbidden(
    client: UiClient, ui: Ui, origin: str
) -> None:
    for path in ("/status", "/api/v1/status", "/static/app.css", "/launch?token=x"):
        reply = client.get(path, headers={"Origin": origin.format(name=ui.host_name, port=ui.port)})
        assert reply.status == 403, path
    assert client.get("/status", headers={"Origin": client.origin}).status == 200


def test_an_upgrade_request_is_refused(client: UiClient, ui: Ui) -> None:
    reply = client.get("/status", headers={"Upgrade": "websocket", "Connection": "Upgrade"})
    assert reply.status == 400


def test_a_post_with_only_fetch_metadata_is_accepted(client: UiClient, ui: Ui) -> None:
    token = client.csrf()
    sent = {"Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded"}
    reply = client.request("POST", "/session/end", body=f"csrf={token}".encode(), headers=sent)
    assert reply.status == 200
    assert ui.wait(5) == "ended"


# --- CSRF ------------------------------------------------------------------------------


@pytest.mark.parametrize("fields", [{}, {"csrf": "wrong"}, {"csrf": ""}, {"other": "x"}])
def test_a_post_without_the_csrf_token_is_forbidden(
    client: UiClient, ui: Ui, fields: dict[str, str]
) -> None:
    assert client.post_form("/session/end", fields).status == 403
    assert_running(ui)


def test_two_csrf_fields_are_forbidden(client: UiClient, ui: Ui) -> None:
    token = client.csrf()
    reply = client.request(
        "POST",
        "/session/end",
        body=f"csrf={token}&csrf={token}".encode(),
        headers={"Origin": client.origin, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert reply.status == 403
    assert_running(ui)


def test_the_csrf_token_of_the_page_is_not_enough_without_the_session(
    client: UiClient, ui: Ui
) -> None:
    token = client.csrf()
    other = UiClient(ui.port, ui.host_name)
    assert other.post_form("/session/end", {"csrf": token}).status == 403
    assert_running(ui)


def test_the_csrf_token_is_in_a_form_field_or_a_header(client: UiClient, ui: Ui) -> None:
    token = client.csrf()
    page = client.get("/status").text
    assert f'<meta name="flib-csrf" content="{token}">' in page
    # A body that is not a form does not count, whatever it says.
    for kind in ("text/plain", "multipart/form-data; boundary=x", ""):
        reply = client.request(
            "POST",
            "/session/end",
            body=f"csrf={token}".encode(),
            headers={"Origin": client.origin, "Content-Type": kind},
        )
        assert reply.status == 403, kind
    reply = client.request(
        "POST",
        "/session/end",
        body=json.dumps({"csrf": token}).encode(),
        headers={"Origin": client.origin, "Content-Type": "application/json"},
    )
    assert reply.status == 403
    reply = client.request(
        "POST",
        "/session/end",
        body=b"",
        headers={"Origin": client.origin, "X-Flib-CSRF": token},
    )
    assert reply.status == 200
    assert ui.wait(5) == "ended"


@pytest.mark.parametrize("path", sorted(ui_server.POST_ROUTES))
@pytest.mark.parametrize("missing", ["cookie", "origin", "csrf"])
def test_every_post_route_needs_the_cookie_the_origin_and_the_csrf_token(
    client: UiClient, ui: Ui, path: str, missing: str
) -> None:
    token = client.csrf()
    sender = UiClient(ui.port, ui.host_name)
    if missing != "cookie":
        sender.cookies = dict(client.cookies)
    headers = {"Origin": client.origin, "Content-Type": "application/x-www-form-urlencoded"}
    if missing == "origin":
        del headers["Origin"]
    body = b"" if missing == "csrf" else f"csrf={token}".encode()
    assert sender.request("POST", path, body=body, headers=headers).status == 403
    assert_running(ui)


def test_the_launch_exchange_is_the_only_post_outside_the_route_table() -> None:
    assert ui_server.LAUNCH not in ui_server.POST_ROUTES
    assert set(ui_server.POST_ROUTES) == {
        "/session/end",
        "/search",
        "/search/export",
        "/files/open",
        "/files/reveal",
        "/review/file",
        "/review/accept",
        "/undo",
        "/due/settle",
        "/settings/save",
        "/settings/schedule",
        "/settings/restore",
        "/settings/open",
    }


def test_a_post_to_an_unknown_path_is_not_found(client: UiClient, ui: Ui) -> None:
    token = client.csrf()
    assert client.post_form("/api/v1/status", {"csrf": token}).status == 404
    assert client.post_form("/nothing", {"csrf": token}).status == 404
    assert_running(ui)


# --- End session -----------------------------------------------------------------------


def test_end_session_shows_the_ended_page_and_stops_the_server(client: UiClient, ui: Ui) -> None:
    session_id = client.cookies[ui.cookie_name]
    reply = client.post_form("/session/end", {"csrf": client.csrf()})
    assert reply.status == 200
    assert "This session has ended" in reply.text
    assert reply.headers["Set-Cookie"] == (
        f"{ui.cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
    )
    assert ui.session(session_id) is None  # gone before the server is
    assert ui.wait(5) == "ended"
    with pytest.raises(OSError):
        client.get("/status")


# --- ending: never a hang --------------------------------------------------------------


def test_a_signal_handler_never_waits_on_a_lock(loopback_network: None, cfg: Config) -> None:
    """A signal can arrive at any point of the main thread, even inside another
    handler; a handler that took a lock held there would wait forever."""
    ui = Ui(cfg, poll_seconds=0.01)
    ui.start()
    try:
        with ui_server._signals(ui):
            handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)

        def interrupted_while_holding_the_lock() -> None:
            with ui._lock:
                handler(signal.SIGINT, None)
                handler(signal.SIGTERM, None)

        thread = threading.Thread(target=interrupted_while_holding_the_lock, daemon=True)
        thread.start()
        thread.join(2)
        assert not thread.is_alive(), "the signal handler deadlocked"
        assert ui.wait(5) == "signal"
    finally:
        ui.close()


def test_two_quick_signals_end_flib_ui_once(
    loopback_network: None, cfg: Config, fake_browser: Path
) -> None:
    original = signal.getsignal(signal.SIGINT)

    def twice() -> None:
        wait_for(lambda: signal.getsignal(signal.SIGINT) is not original, timeout=10)
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGINT)

    sender = Background(twice)
    lines: list[str] = []
    assert ui_server.run(cfg, browser=False, echo=lines.append) == "signal"
    sender.join()
    assert lines[-1] == "flib ui ended: interrupted."


def test_close_cuts_off_a_request_in_progress(loopback_network: None, cfg: Config) -> None:
    ui = Ui(cfg, poll_seconds=0.01, request_timeout=30, close_grace=0)
    # A reply far larger than the socket buffers, to a client that never reads it:
    # the request thread blocks writing, for up to 30 seconds per write.
    ui.assets["/static/big.bin"] = (b"x" * 32_000_000, "application/octet-stream")
    ui.start()
    try:
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            sock.sendall(
                f"GET /static/big.bin HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n\r\n".encode()
            )
            wait_for(lambda: ui._server._active)
            started = time.monotonic()
            ui.close()
            assert time.monotonic() - started < 2  # not the 30 seconds of the timeout
    finally:
        ui.close()
    assert ui.reason == "closed"


def test_a_keep_alive_connection_gets_one_reply_and_is_closed(client: UiClient, ui: Ui) -> None:
    """A connection never stays open waiting for a second request, so idle
    connections cannot hold the request threads."""
    reply = client.raw(
        f"GET /static/app.css HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
        "Connection: keep-alive\r\n\r\n".encode()
    )  # returns only once the server closes the connection
    assert reply.startswith(b"HTTP/1.0 200 ")
    assert reply.count(b"HTTP/1.0 ") == 1


def test_the_server_ends_if_its_loop_fails(
    monkeypatch: pytest.MonkeyPatch, loopback_network: None, cfg: Config
) -> None:
    ui = Ui(cfg, poll_seconds=0.01)

    def broken() -> None:
        raise RuntimeError("accept failed")

    monkeypatch.setattr(ui, "_accept", broken)
    ui.start()
    try:
        with pytest.raises(OSError):
            UiClient(ui.port, ui.host_name).get("/status")  # the loop fails on it and closes
        assert ui.wait(5) == "closed"
    finally:
        ui.close()


def test_a_failing_helper_ends_flib_ui_instead_of_hanging(
    loopback_network: None, cfg: Config, fake_browser: Path
) -> None:
    """The guard of the tests below: if what plays the browser fails, ``flib ui``
    ends at once and the failure is raised, rather than serving ten idle minutes."""
    original = signal.getsignal(signal.SIGTERM)

    def fails() -> None:
        wait_for(lambda: signal.getsignal(signal.SIGTERM) is not original, timeout=10)
        raise AssertionError("the browser did not get its page")

    helper = Background(fails)
    assert ui_server.run(cfg, browser=False, echo=lambda line: None) == "signal"
    with pytest.raises(AssertionError, match="did not get its page"):
        helper.join()


# --- limits ----------------------------------------------------------------------------


def test_a_large_body_is_refused_before_it_is_read(client: UiClient, ui: Ui) -> None:
    head = (
        f"POST /session/end HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
        f"Origin: {client.origin}\r\nContent-Length: 20000\r\n\r\n"
    )
    assert client.raw(head.encode()).startswith(b"HTTP/1.0 413 ")


@pytest.mark.parametrize(
    ("extra", "status"),
    [
        ("", b"411"),
        ("Content-Length: abc\r\n", b"400"),
        ("Content-Length: \xb2\r\n", b"400"),  # a digit to str.isdigit, not to int
        ("Content-Length: \u0661\r\n", b"400"),  # an Arabic-Indic one
        ("Content-Length: 1_0\r\n", b"400"),
        ("Content-Length: +5\r\n", b"400"),
        ("Content-Length: 100000\r\n", b"400"),  # six digits
        ("Content-Length: 1\r\nContent-Length: 1\r\n", b"400"),
        ("Content-Length: 1\r\nContent-Length: 99\r\n", b"400"),
        ("Transfer-Encoding: chunked\r\n", b"400"),
        ("Content-Length: 1\r\nTransfer-Encoding: chunked\r\n", b"400"),
    ],
)
def test_a_post_needs_a_plain_length(client: UiClient, ui: Ui, extra: str, status: bytes) -> None:
    head = (
        f"POST /session/end HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
        f"Origin: {client.origin}\r\n{extra}\r\n"
    )
    # Latin-1 where it can be: that is how http.server decodes header bytes.
    data = head.encode("latin-1") if max(map(ord, head)) < 256 else head.encode("utf-8")
    reply = client.raw(data + b"x")
    assert reply.startswith(b"HTTP/1.0 " + status + b" "), reply[:40]
    assert b"Content-Security-Policy: " in reply


def test_long_request_lines_and_headers_are_refused(client: UiClient, ui: Ui) -> None:
    assert client.get("/static/app.css?" + "a" * 5000).status == 414
    assert client.get("/static/app.css", headers={"X-Big": "a" * 20000}).status == 431
    many = {f"X-H{i}": "1" for i in range(150)}
    assert client.get("/static/app.css", headers=many).status == 431
    assert client.raw(b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.0 414 ")


def test_a_slow_client_is_dropped(loopback_network: None, cfg: Config) -> None:
    ui = Ui(cfg, poll_seconds=0.01, request_deadline=0.05)
    ui.start()
    try:
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            sock.sendall(b"GET /status HTTP/1.1\r\n")  # and nothing more
            reply = raw_headers(sock.recv(4096))  # told why, after the deadline
            assert reply[""] == "HTTP/1.0 408 Request Timeout"
            assert reply["Content-Security-Policy"] == CSP
            assert sock.recv(1024) == b""  # then closed
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            assert sock.recv(1024) == b""  # one that says nothing is just closed
    finally:
        ui.close()


def test_a_body_that_never_arrives_gets_a_timeout(loopback_network: None, cfg: Config) -> None:
    ui = Ui(cfg, poll_seconds=0.01, request_deadline=0.05)
    ui.start()
    try:
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            sock.sendall(
                f"POST /session/end HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
                "Content-Length: 50\r\n\r\nab".encode()
            )  # and the other 48 bytes never come
            assert sock.recv(4096).startswith(b"HTTP/1.0 408 ")
        assert ui._server._active == {}  # it never had a request thread
    finally:
        ui.close()


def test_a_client_that_trickles_its_request_is_cut_off_at_the_deadline(
    loopback_network: None, cfg: Config, counted_slots: None
) -> None:
    """A byte at a time, each well within the read timeout, never gets past the
    deadline for the whole request; and the threads come back for everyone else."""
    ui = Ui(cfg, poll_seconds=0.01, max_threads=2, request_timeout=5, request_deadline=0.2)
    ui.start()
    try:
        # Before connecting: the server's deadline starts no earlier than this.
        started = time.monotonic()
        slow = [socket.create_connection(("127.0.0.1", ui.port), timeout=5) for _ in range(2)]
        for sock in slow:
            sock.sendall(
                f"GET /static/app.css HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n".encode()
            )
        cut: set[int] = set()
        while len(cut) < len(slow):
            assert time.monotonic() - started < FAILURE_SECONDS, "never cut off"
            for i, sock in enumerate(slow):
                if i in cut:
                    continue
                try:
                    sock.sendall(b"X-Pad: a\r\n")
                    sock.settimeout(0.02)
                    if sock.recv(1024) in (b"", _TIMEOUT_REPLY):
                        cut.add(i)
                except TimeoutError:
                    pass
                except OSError:
                    cut.add(i)
        for sock in slow:
            sock.close()
        assert time.monotonic() - started >= 0.2
        all_slots_free(ui)
        assert UiClient(ui.port, ui.host_name).get("/static/app.css").status == 200
    finally:
        ui.close()


def test_the_deadline_does_not_cut_a_reply_in_progress(
    monkeypatch: pytest.MonkeyPatch, loopback_network: None, cfg: Config
) -> None:
    """Once the whole request is in, the deadline no longer applies."""
    ui = Ui(cfg, poll_seconds=0.01, request_deadline=0.05)
    ui.start()
    try:
        c = UiClient(ui.port, ui.host_name)
        c.launch(ui.launch_url)
        real = api.status

        def slow(config: Config) -> object:
            time.sleep(0.2)  # longer than the deadline, inside the handler
            return real(config)

        monkeypatch.setattr(api, "status", slow)
        assert c.get("/api/v1/status").status == 200
    finally:
        ui.close()


def test_requests_beyond_the_thread_cap_are_turned_away(
    monkeypatch: pytest.MonkeyPatch, loopback_network: None, cfg: Config, counted_slots: None
) -> None:
    ui = Ui(cfg, poll_seconds=0.01, max_threads=1)
    ui.start()
    busy = Busy(monkeypatch)
    try:
        c = UiClient(ui.port, ui.host_name)
        c.launch(ui.launch_url)
        held = busy.hold(ui, c)  # holds the only thread
        assert UiClient(ui.port, ui.host_name).get("/static/app.css").status == 503
        busy.release(held)
        assert busy.statuses == [200]  # the held request was served in full
        # The slot comes back once the request is done.
        all_slots_free(ui)
        assert UiClient(ui.port, ui.host_name).get("/static/app.css").status == 200
    finally:
        busy.release()
        ui.close()


def test_clients_that_never_finish_their_request_hold_no_thread(
    loopback_network: None, cfg: Config, counted_slots: None
) -> None:
    """Idle and trickling connections wait in the accepting thread, not in a
    request thread: with every one of them open, the session is still served."""
    ui = Ui(cfg, poll_seconds=0.01, max_threads=1)
    ui.start()
    c = UiClient(ui.port, ui.host_name)
    c.launch(ui.launch_url)
    all_slots_free(ui)
    hogs = [socket.create_connection(("127.0.0.1", ui.port), timeout=5) for _ in range(20)]
    try:
        for i, hog in enumerate(hogs):
            if i % 2:
                hog.sendall(b"GET / HTTP/1.1\r\nX: ")  # a head that never ends
            if i % 3 == 0:
                hog.sendall(b"POST /session/end HTTP/1.1\r\nContent-Length: 50\r\n\r\nab")
        wait_for(lambda: ui._heads is not None and len(ui._heads.pending) == len(hogs))
        assert ui._server._active == {}
        for _ in range(5):
            assert c.get("/api/v1/status").status == 200
            all_slots_free(ui)  # the one thread, back before the next request
    finally:
        for hog in hogs:
            hog.close()
        ui.close()


def test_past_the_pending_cap_the_oldest_connection_is_dropped(
    monkeypatch: pytest.MonkeyPatch, loopback_network: None, cfg: Config
) -> None:
    monkeypatch.setattr(ui_server, "MAX_PENDING", 3)
    ui = Ui(cfg, poll_seconds=0.01, request_deadline=60)  # no help from the deadline
    ui.start()
    socks = []
    try:
        for _ in range(4):
            socks.append(socket.create_connection(("127.0.0.1", ui.port), timeout=FAILURE_SECONDS))
            if len(socks) < 4:
                wait_for(lambda: ui._heads is not None and len(ui._heads.pending) == len(socks))
        assert socks[0].recv(10) == b""  # the oldest, closed to make room
        socks[1].settimeout(1)
        with pytest.raises(TimeoutError):
            socks[1].recv(10)  # the others wait on
        wait_for(lambda: ui._heads is not None and len(ui._heads.pending) == 3)
        assert UiClient(ui.port, ui.host_name).get("/static/app.css").status == 200
    finally:
        for sock in socks:
            sock.close()
        ui.close()


@pytest.mark.parametrize(
    "request_head",
    [b"GET /", b"GET /" + b"a" * 5000, b"GET / HTTP/1.1\r\nX: " + b"a" * 30000],
)
def test_a_head_too_long_is_refused_without_a_thread(
    loopback_network: None, cfg: Config, request_head: bytes
) -> None:
    ui = Ui(cfg, poll_seconds=0.01, max_threads=1)
    ui.start()
    try:
        c = UiClient(ui.port, ui.host_name)
        if len(request_head) < 100:
            # A short start that never ends is just pending, then dropped.
            with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
                sock.sendall(request_head)
                wait_for(lambda: ui._heads is not None and len(ui._heads.pending) == 1)
            return
        reply = c.raw(request_head)  # read until the server closes
        status = b"414" if b"\r\n" not in request_head else b"431"
        assert reply.startswith(b"HTTP/1.0 " + status + b" "), reply[:40]
        assert b"Content-Security-Policy: " in reply
        assert ui._server._active == {}
    finally:
        ui.close()


def test_a_body_shorter_than_its_length_gets_a_reply(client: UiClient, ui: Ui) -> None:
    """The client stops sending before the body it declared: it still gets 400."""
    with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
        sock.sendall(
            f"POST /session/end HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
            f"Origin: {client.origin}\r\nContent-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: 50\r\n\r\ncsrf=".encode()
        )
        sock.shutdown(socket.SHUT_WR)
        reply = sock.recv(4096)
    assert reply.startswith(b"HTTP/1.0 400 ")
    assert_running(ui)


# --- methods, paths and error pages ----------------------------------------------------


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "HEAD"])
def test_only_get_and_post_are_allowed(client: UiClient, method: str) -> None:
    reply = client.request(method, "/status")
    assert reply.status == 405
    assert reply.headers["Allow"] == "GET, POST"


def test_an_unknown_method_is_not_implemented(client: UiClient) -> None:
    reply = client.request("BREW", "/status")
    assert reply.status == 501
    assert reply.body == b"Not Implemented\n"


@pytest.mark.parametrize(
    "path",
    [
        "/static/../server.py",
        "/static/%2e%2e/server.py",
        "/static/..%2fserver.py",
        "/static/..%5cserver.py",
        "/static/",
        "/static",
        "/static/app.css/",
        "/static/APP.CSS",
        "/static/./app.css",
        "/pages/status.html",
        "/server.py",
        "/etc/passwd",
        "/status/",
        "/ended",
        "/api/v1/nothing",
    ],
)
def test_nothing_outside_the_packaged_files_is_served(client: UiClient, path: str) -> None:
    reply = client.get(path)
    assert reply.status == 404, path
    assert b"import" not in reply.body and b"root:" not in reply.body


def test_error_pages_reveal_nothing(client: UiClient) -> None:
    reply = client.get("/no/such/page")
    assert reply.body == b"Not Found\n"
    assert reply.headers["Server"] == "flib"
    assert reply.headers["Content-Type"] == "text/plain; charset=utf-8"
    for reply in (client.get("/status", host="evil"), client.request("PUT", "/")):
        assert b"Python" not in reply.body and b"Traceback" not in reply.body
        assert reply.headers["Server"] == "flib"


def test_every_reply_carries_the_security_headers(client: UiClient, ui: Ui) -> None:
    host = f"Host: {ui.host_name}:{ui.port}\r\n"
    replies = [
        dict(reply.headers.items()) | {"": str(reply.status)}
        for reply in (
            client.get("/status"),  # 200, a page
            client.get("/api/v1/status"),  # 200, JSON
            client.get("/static/app.css"),  # 200, a file
            client.get("/"),  # 303
            UiClient(ui.port, ui.host_name).get("/api/v1/status"),  # 401
            UiClient(ui.port, ui.host_name).get("/status"),  # 403, the ended page
            client.get("/status", headers={"Sec-Fetch-Site": "cross-site"}),  # 403
            client.get("/nothing"),  # 404
            client.request("PUT", "/status"),  # 405
            client.post_form("/session/end", {"csrf": "x"}, {"Content-Type": "x"}),  # 403
            client.get("/status", host="evil"),  # 421
            client.request("BREW", "/"),  # 501
        )
    ]
    replies += [
        raw_headers(client.raw(data))
        for data in (
            b"GARBAGE\r\n\r\n",  # 400, a request line that makes no sense
            b"GET /status\r\n\r\n",  # 400, HTTP/0.9: would get a bare body
            b"GET / HTTP/<script>\r\n\r\n",  # 400
            f"POST /session/end HTTP/1.1\r\n{host}Origin: {client.origin}\r\n\r\n".encode(),
            b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n",  # 414
        )
    ]
    for reply in replies:
        assert reply[""], reply
        for name, value in SECURITY_HEADERS.items():
            assert reply.get(name) == value, (reply[""], name)
        assert not any(name.lower().startswith("access-control-") for name in reply), reply
    statuses = {reply[""].split(" ")[1] if " " in reply[""] else reply[""] for reply in replies}
    assert {"200", "303", "400", "401", "403", "404", "405", "411", "414", "421", "501"} <= statuses


def test_the_turned_away_reply_carries_the_security_headers(
    monkeypatch: pytest.MonkeyPatch, loopback_network: None, cfg: Config, counted_slots: None
) -> None:
    ui = Ui(cfg, poll_seconds=0.01, max_threads=1)
    ui.start()
    busy = Busy(monkeypatch)
    try:
        c = UiClient(ui.port, ui.host_name)
        c.launch(ui.launch_url)
        held = busy.hold(ui, c)
        reply = raw_headers(
            c.raw(
                f"GET /static/app.css HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n\r\n".encode()
            )
        )
        assert reply[""] == "HTTP/1.0 503 Service Unavailable"
        for name, value in SECURITY_HEADERS.items():
            assert reply.get(name) == value, name
        busy.release(held)
        assert busy.statuses == [200]
    finally:
        busy.release()
        ui.close()


def test_no_reply_ever_allows_another_origin(client: UiClient, ui: Ui) -> None:
    """No CORS: not on a preflight, not on a reply to a request with an Origin."""
    for method in ("OPTIONS", "GET"):
        reply = client.request(
            method,
            "/api/v1/status",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        assert reply.status in (403, 405)
        assert not [n for n in reply.headers if n.lower().startswith("access-control-")]


def test_a_bug_gets_a_plain_500_and_no_traceback(
    monkeypatch: pytest.MonkeyPatch, client: UiClient, capfd: pytest.CaptureFixture[str]
) -> None:
    def broken(config: Config) -> None:
        raise RuntimeError("a bug at /secret/path")

    monkeypatch.setattr(api, "status", broken)
    reply = client.get("/api/v1/status")
    assert reply.status == 500
    assert reply.body == b"Internal Server Error\n"
    assert reply.headers["Content-Security-Policy"] == CSP
    out, err = capfd.readouterr()
    assert out == "" and err == ""


def test_content_types(client: UiClient) -> None:
    assert client.get("/status").headers["Content-Type"] == "text/html; charset=utf-8"
    assert client.get("/static/app.css").headers["Content-Type"] == "text/css; charset=utf-8"
    js = client.get("/static/status.js").headers["Content-Type"]
    assert js == "text/javascript; charset=utf-8"
    assert client.get("/static/icon.png").headers["Content-Type"] == "image/png"
    assert client.get("/api/v1/status").headers["Content-Type"] == "application/json"


def test_nothing_is_logged(
    loopback_network: None, cfg: Config, capfd: pytest.CaptureFixture[str]
) -> None:
    ui = Ui(cfg, poll_seconds=0.01, request_deadline=0.05)
    ui.start()
    try:
        c = UiClient(ui.port, ui.host_name)
        c.get(f"/launch?token={token_of(ui)[:-1]}x")
        c.launch(ui.launch_url)
        c.get("/status")
        c.get("/nothing")
        c.request("BREW", "/")
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            sock.sendall(b"GET /launch?token=" + token_of(ui).encode())  # then times out
            sock.recv(1024)
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            sock.sendall(b"GET /status HTTP/1.1\r\n")  # and goes away mid-request
    finally:
        ui.close()
    out, err = capfd.readouterr()
    assert out == "" and err == ""


# --- the status API --------------------------------------------------------------------


def test_status_before_the_first_run(client: UiClient, ui: Ui) -> None:
    data = client.get("/api/v1/status").json()
    assert data["api"] == 1
    assert data["database_exists"] is False
    assert data["review"] == {"total": 0, "oldest_days": None, "groups": []}
    assert data["last_run"] is None
    assert not ui.config.database.exists()  # nothing created


NAMES = ("unsure-one", "unsure-two", "odd-recipe", "broken-scan", "id_rsa", "electricity")


def test_status_holds_counts_and_never_a_file_name(
    loopback_network: None, downloads: Path, cfg: Config
) -> None:
    answers = {
        "electricity.pdf": answer("Finance", confidence=0.9),
        "unsure-one.pdf": answer("Finance", confidence=0.3),
        "unsure-two.pdf": answer("Finance", confidence=0.4),
        "odd-recipe.pdf": answer("Recipes", confidence=0.9),
    }
    for name in [*answers, "broken-scan.pdf"]:
        put(downloads, name, name.encode())
    put(downloads, "id_rsa", b"key material")
    pipeline.run(cfg, FakeProvider(answers), simulate=False)

    ui = Ui(cfg, poll_seconds=0.01)
    ui.start()
    try:
        c = UiClient(ui.port, ui.host_name)
        c.launch(ui.launch_url)
        reply = c.get("/api/v1/status")
    finally:
        ui.close()
    data = reply.json()
    assert data["database_exists"] is True
    assert data["review"]["total"] == 3
    assert data["failed"]["total"] == 1
    assert data["sensitive"] == 1
    assert data["last_run"]["organized"] == 1 and data["last_run"]["trigger"] == "manual"
    explanations = [g["explanation"] for g in data["review"]["groups"]]
    assert any("not configured: <other>" in e for e in explanations)  # model text masked
    for name in NAMES:
        assert name not in reply.text
    assert str(downloads) not in reply.text


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (StatusError("the index is from an older flib"), "the index is from an older flib"),
        (sqlite3.OperationalError("disk I/O error at /secret/path"), "cannot read the index"),
    ],
)
def test_status_errors_are_said_plainly(
    monkeypatch: pytest.MonkeyPatch, client: UiClient, error: Exception, message: str
) -> None:
    def broken(config: Config) -> None:
        raise error

    monkeypatch.setattr(api, "status", broken)
    reply = client.get("/api/v1/status")
    assert reply.status == 503
    assert reply.json() == {"error": message}


# --- idle shutdown ---------------------------------------------------------------------


def test_the_server_ends_after_the_idle_minutes(loopback_network: None, cfg: Config) -> None:
    clock = FakeClock()
    ui = Ui(cfg, clock=clock, poll_seconds=0.01)
    assert ui.idle_seconds == 600
    ui.start()
    try:
        clock.now += 599
        c = UiClient(ui.port, ui.host_name)
        c.launch(ui.launch_url)  # the launch exchange resets the timer
        clock.now += 599
        assert ui.wait(0.05) is None
        c.get("/static/app.css")  # so does any request from the session
        clock.now += 599
        assert ui.wait(0.05) is None
        clock.now += 2
        assert ui.wait(5) == "idle"
    finally:
        ui.close()
    assert ui.reason == "idle"


def test_only_the_session_keeps_the_server_alive(loopback_network: None, cfg: Config) -> None:
    """Anything may knock on a port; only the session resets the idle timer."""
    clock = FakeClock()
    ui = Ui(cfg, clock=clock, poll_seconds=0.01, idle_seconds=60)
    ui.start()
    try:
        session = UiClient(ui.port, ui.host_name)
        session.launch(ui.launch_url)
        clock.now += 59
        stranger = UiClient(ui.port, ui.host_name)
        stranger.cookies[ui.cookie_name] = "guess"
        stranger.get("/static/app.css", host="evil.example")
        stranger.get("/static/app.css")
        stranger.get("/status")
        stranger.get("/api/v1/status")
        stranger.get("/launch?token=guess")
        stranger.post_form("/session/end", {"csrf": "x"})
        clock.now += 2
        assert ui.wait(5) == "idle"
    finally:
        ui.close()


def test_idle_minutes_come_from_the_configuration(downloads: Path) -> None:
    cfg = fake_config(downloads, ui={"idle_minutes": 3})
    ui = Ui(cfg)
    try:
        assert ui.idle_seconds == 180
    finally:
        ui.close()
    assert fake_config(downloads).ui.idle_minutes == 10
    for bad in (0, 1441, "soon"):
        with pytest.raises((ConfigError, ValueError)):
            fake_config(downloads, ui={"idle_minutes": bad})
    with pytest.raises((ConfigError, ValueError)):
        fake_config(downloads, ui={"port": 8080})


def test_the_example_configuration_documents_idle_minutes() -> None:
    text = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    assert re.search(r"^ui: .*\n  idle_minutes: 10\b", text, re.MULTILINE)


# --- flib ui ---------------------------------------------------------------------------


def opened(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


class Background:
    """Runs ``work`` beside a ``flib ui`` that blocks the test until it ends.

    If ``work`` fails, ``flib ui`` would serve on for its ten idle minutes: so
    it is ended with SIGTERM (once its handler is in place, never pytest's), and
    ``join`` raises the failure.
    """

    def __init__(self, work: Callable[[], None]) -> None:
        self._original = signal.getsignal(signal.SIGTERM)
        self.error: BaseException | None = None
        self._work = work
        self._thread = threading.Thread(target=self._run, name="test-background", daemon=True)
        self._thread.start()

    def listening(self) -> bool:
        return signal.getsignal(signal.SIGTERM) is not self._original

    def _run(self) -> None:
        try:
            self._work()
        except BaseException as exc:
            self.error = exc
            if self.listening():
                os.kill(os.getpid(), signal.SIGTERM)

    def join(self) -> None:
        self._thread.join(10)
        assert not self._thread.is_alive()
        if self.error is not None:
            raise self.error


def end_from_the_browser(log: Path) -> Background:
    """In the background: wait for the fake ``open``, open the handoff file it was
    given as the browser would, then End session."""

    def browse() -> None:
        wait_for(lambda: opened(log), timeout=10)
        [[argument]] = opened(log)
        handoff = Path(argument)
        action = re.search(
            r'action="(http://flib-[0-9a-f]{32}\.localhost:\d+/launch)"',
            handoff.read_text(encoding="utf-8"),
        )
        assert action is not None
        c = UiClient.for_url(action.group(1))
        reply = c.launch_from_handoff(handoff)
        assert reply.status == 200 and "Session started" in reply.text
        assert not handoff.exists()  # deleted once used
        assert c.get("/").headers["Location"] == "/status"
        assert c.post_form("/session/end", {"csrf": c.csrf()}).status == 200

    return Background(browse)


def interrupt_when_listening(signum: int) -> Background:
    """In the background: send ``signum`` once ``flib ui`` handles it."""
    original = signal.getsignal(signum)

    def send() -> None:
        wait_for(lambda: signal.getsignal(signum) is not original, timeout=10)
        os.kill(os.getpid(), signum)

    return Background(send)


def test_flib_ui_opens_a_handoff_file_and_ends_with_the_session(
    loopback_network: None, tmp_path: Path, downloads: Path, fake_browser: Path
) -> None:
    path = write_config(tmp_path, downloads)
    browser = end_from_the_browser(fake_browser)
    result = runner.invoke(app, ["ui", "-c", str(path)])
    browser.join()
    assert result.exit_code == 0, result.stdout + result.stderr
    [[argument]] = opened(fake_browser)
    assert argument.endswith("/open-file-librarian.html")  # a file, not the token
    assert "token" not in argument and "launch" not in argument.rsplit("/", 1)[1]
    # The address is not printed: under a name, it is what keeps other servers from
    # the cookie, and a terminal is often on screen.
    assert re.search(r"flib ui: opening it in your browser \(port \d+\)\.", result.stdout)
    assert ".localhost" not in result.stdout + result.stderr
    assert "token=" not in result.stdout + result.stderr
    assert "flib ui: session started in the browser." in result.stdout
    assert "flib ui ended: the session was ended in the browser." in result.stdout


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_flib_ui_ends_on_a_signal(
    loopback_network: None, cfg: Config, fake_browser: Path, signum: int
) -> None:
    before = signal.getsignal(signum)
    lines: list[str] = []
    sender = interrupt_when_listening(signum)
    assert ui_server.run(cfg, browser=False, echo=lines.append) == "signal"
    sender.join()
    assert lines[-1] == "flib ui ended: interrupted."
    assert opened(fake_browser) == []  # --no-browser
    assert signal.getsignal(signum) is before  # the handlers are put back


def test_flib_ui_says_when_the_browser_cannot_open(
    loopback_network: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    downloads: Path,
    fake_browser: Path,
) -> None:
    monkeypatch.setenv("FAKE_OPEN_FAIL", "1")
    path = write_config(tmp_path, downloads)
    browser = end_from_the_browser(fake_browser)
    result = runner.invoke(app, ["ui", "-c", str(path)])
    browser.join()
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "warning: could not open the browser (open exited with 1)" in result.stderr
    assert "open-file-librarian.html in your browser" in result.stderr
    assert "token" not in result.stderr


def test_flib_ui_without_open_says_why(
    loopback_network: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ui_server, "OPEN", "/nonexistent/open")
    assert ui_server.open_file(tmp_path / "page.html") == "FileNotFoundError"


def test_a_stolen_link_ends_flib_ui_with_a_warning(
    loopback_network: None, cfg: Config, fake_browser: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui_server, "STOLEN_LINGER", 0.0)  # what the tab sees: tested above
    lines: list[str] = []
    warnings: list[str] = []

    def steal_then_use() -> None:
        wait_for(lambda: any("/launch?token=" in line for line in lines), timeout=10)
        url = next(line for line in lines if "/launch?token=" in line).split(" ", 2)[2]
        assert UiClient.for_url(url).launch(url).status == 303  # another account, first
        assert UiClient.for_url(url).launch(url).status == 403  # the user, second

    thief = Background(steal_then_use)
    assert ui_server.run(cfg, browser=False, echo=lines.append, warn=warnings.append) == "stolen"
    thief.join()
    assert warnings == [ui_server.STOLEN_WARNING]
    assert "Another program or account on this Mac may have seen it" in warnings[0]
    assert lines[-1].startswith("flib ui ended: the launch link was used again")


def launch_url(lines: list[str]) -> str:
    wait_for(lambda: any("/launch?token=" in line for line in lines), timeout=10)
    return next(line for line in lines if "/launch?token=" in line).split(" ", 2)[2]


def test_after_a_theft_flib_ui_can_start_again_at_once(
    loopback_network: None, cfg: Config, fake_browser: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stolen page says "run `flib ui` again" (#127 review): that is true at once.
    On the theft the old server gives up its records and the lock while it serves only
    that page; a new ``flib ui`` starts a fresh session on its own port and name; the
    old one ends later ("stolen", exit 1) and removes nothing of the new one."""
    monkeypatch.setattr(ui_server, "STOLEN_LINGER", 30.0)  # ended below; a failure bound
    made: list[Ui] = []

    class Recorded(ui_server.Ui):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(ui_server, "Ui", Recorded)
    old_lines: list[str] = []
    new_lines: list[str] = []
    second: list[str] = []
    seen: dict[str, Any] = {}

    def run_again() -> None:
        second.append(ui_server.run(cfg, browser=False, echo=new_lines.append, warn=print))

    def steal_then_start_again() -> None:
        url = launch_url(old_lines)
        rightful = UiClient.for_url(url)
        assert rightful.launch(url).status == 303
        assert UiClient.for_url(url).launch(url).status == 403  # the theft
        # Its records go with the lock (the stolen() call ran before the 403 was sent).
        assert not ui_server.running_path(cfg.database).exists()
        assert not ui_server.name_path(cfg.database).exists()
        assert ui_server.running(cfg.database) is None
        # A new flib ui, while the old one still answers with the stolen page.
        again = threading.Thread(target=run_again, name="test-second-ui", daemon=True)
        again.start()
        seen["thread"] = again
        fresh = launch_url(new_lines)
        assert urlsplit(fresh).netloc != urlsplit(url).netloc  # its own port and name
        client = UiClient.for_url(fresh)
        assert client.launch(fresh).status == 303
        assert client.get("/status").status == 200
        assert STOLEN in rightful.get("/status").text  # the old one: that page only
        seen["client"], seen["where"] = client, ui_server.running(cfg.database)
        os.kill(os.getpid(), signal.SIGTERM)  # Ctrl-C in the old one's terminal

    work = Background(steal_then_start_again)
    assert ui_server.run(cfg, browser=False, echo=old_lines.append, warn=print) == "stolen"
    work.join()
    old, new = made
    where = seen["where"]
    assert where is not None and (where.port, where.host) == (new.port, new.host_name)
    # The old one ended and left the new one's records, lock and session alone.
    assert ui_server.running(cfg.database) == where
    assert seen["client"].get("/status").status == 200
    assert ui_server.run(cfg, browser=False, echo=lambda line: None) == "running"
    new.stop("signal")
    seen["thread"].join(10)
    assert second == ["signal"]
    assert ui_server.running(cfg.database) is None  # and the new one removed its own
    assert old.reason == "stolen"


def test_a_flib_ui_removes_only_its_own_records(cfg: Config) -> None:
    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    ui_server.write_running(cfg.database, 4242, "flib-" + "a" * 32 + ".localhost")
    ui_server.remove_running(cfg.database, 4343, "flib-" + "b" * 32 + ".localhost")
    assert ui_server.running_path(cfg.database).exists()  # another port: not this one's
    ui_server.remove_running(cfg.database, 4242, "flib-" + "b" * 32 + ".localhost")
    assert ui_server.running_path(cfg.database).exists() is False  # its pid and port
    assert ui_server.name_path(cfg.database).exists()  # but another name: kept
    ui_server.remove_running(cfg.database, 4242, "flib-" + "a" * 32 + ".localhost")
    assert not ui_server.name_path(cfg.database).exists()


def test_the_theft_linger_outlasts_end_sessions(ui: Ui, events: list[str]) -> None:
    """After a theft the stolen page is served past End session's 1.5 s (a mutant set
    both lingers alike), until a signal ends it."""
    UiClient(ui.port, ui.host_name).launch(ui.launch_url)
    thief = UiClient(ui.port, ui.host_name)
    assert thief.launch(ui.launch_url).status == 403
    assert ui.wait(ui_server.END_LINGER + 0.5) is None  # past End session's linger
    late = thief.get("/status", headers={"Accept": "text/html"})
    assert late.status == 403 and STOLEN in late.text
    ui.stop("signal")
    assert ui.wait(5) == "stolen"


def test_after_a_theft_even_a_missing_page_says_why(ui: Ui) -> None:
    UiClient(ui.port, ui.host_name).launch(ui.launch_url)
    thief = UiClient(ui.port, ui.host_name)
    thief.launch(ui.launch_url)
    for path in ("/no/such/page", "/status?x=1", "/due?within=9y"):
        reply = thief.get(path, headers={"Accept": "text/html"})
        assert reply.status in (400, 403, 404) and STOLEN in reply.text, path
    assert thief.get("/no/such/page").headers["Content-Type"].startswith("text/plain")
    ui.stop("closed")
    assert ui.wait(5) == "stolen"


def test_the_ui_lock_can_be_let_go_early_once(cfg: Config) -> None:
    lock = cfg.database.parent / "flib-ui.lock"
    with ui_lock(cfg.database) as release:
        release()
        assert lock.read_text() == ""
        with ui_lock(cfg.database):  # free: the next one takes it at once
            assert lock.read_text() == str(os.getpid())
            release()  # again: nothing, and the next holder's pid stays
            assert lock.read_text() == str(os.getpid())
    assert lock.read_text() == ""


def test_flib_ui_exits_with_an_error_when_the_link_was_stolen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, downloads: Path
) -> None:
    monkeypatch.setattr(ui_server, "run", lambda *args, **kwargs: "stolen")
    result = runner.invoke(app, ["ui", "-c", str(write_config(tmp_path, downloads))])
    assert result.exit_code == 1


def test_flib_ui_runs_once(
    loopback_network: None, tmp_path: Path, downloads: Path, fake_browser: Path
) -> None:
    path = write_config(tmp_path, downloads)
    cfg = parse_config({"inputs": [str(downloads)], "provider": {"name": "fake", "model": "x"}})
    with ui_lock(cfg.database):
        result = runner.invoke(app, ["ui", "-c", str(path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert f"flib ui is already running (pid {os.getpid()})" in result.stdout
    assert "flib ui:" not in result.stdout
    assert opened(fake_browser) == []


def test_flib_ui_with_a_bad_page_or_port(
    loopback_network: None, tmp_path: Path, downloads: Path, ui: Ui
) -> None:
    path = write_config(tmp_path, downloads)
    result = runner.invoke(app, ["ui", "--open", "nope", "-c", str(path)])
    assert result.exit_code == 2  # a fixed choice: refused before anything starts
    assert "'nope' is not one of" in result.stderr
    result = runner.invoke(app, ["ui", "--port", str(ui.port), "-c", str(path)])
    assert result.exit_code == 1
    assert f"cannot listen on 127.0.0.1:{ui.port}" in result.stderr
    result = runner.invoke(app, ["ui", "--port", "70000", "-c", str(path)])
    assert result.exit_code == 2


def test_flib_ui_no_browser_prints_the_link(
    loopback_network: None, tmp_path: Path, downloads: Path, fake_browser: Path
) -> None:
    path = write_config(tmp_path, downloads)
    sender = interrupt_when_listening(signal.SIGTERM)
    result = runner.invoke(app, ["ui", "--no-browser", "-c", str(path)])
    sender.join()
    assert result.exit_code == 0, result.stdout + result.stderr
    assert re.search(
        r"flib ui: http://flib-[0-9a-f]{32}\.localhost:\d+/launch\?token=", result.stdout
    )
    assert "The link works once" in result.stdout
    # The token works once, but the name lasts the session: said, right after the link.
    assert ui_server.PRIVATE_ADDRESS in result.stdout
    assert "the name before the port lasts the session" in ui_server.PRIVATE_ADDRESS
    assert opened(fake_browser) == []


def test_without_a_name_the_link_needs_no_such_line(
    loopback_network: None, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui_server, "names_resolve", lambda: False)
    lines: list[str] = []
    sender = interrupt_when_listening(signal.SIGTERM)
    ui_server.run(cfg, browser=False, echo=lines.append, warn=lambda line: None)
    sender.join()
    assert ui_server.PRIVATE_ADDRESS not in lines


# --- the handoff file ------------------------------------------------------------------


def test_the_handoff_file_is_for_the_user_only(ui: Ui, cfg: Config) -> None:
    handoff = ui.write_handoff()
    assert handoff.parent.parent == ui_server.handoff_root()  # the account's own temp folder
    assert stat.S_IMODE(handoff.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o600
    text = handoff.read_text(encoding="utf-8")
    assert f'action="http://{ui.host_name}:{ui.port}/launch"' in text
    assert f'value="{token_of(ui)}"' in text
    assert '<meta name="referrer" content="no-referrer">' in text
    assert "?token=" not in text  # never in a URL
    assert "http://" not in text.replace(f'action="{ui.origin}/launch"', "")  # one address
    ui.close()
    assert not handoff.parent.exists()  # gone with the server, even unused


def test_the_handoff_file_starts_the_session_and_is_deleted(ui: Ui, events: list[str]) -> None:
    handoff = ui.write_handoff()
    c = UiClient(ui.port, ui.host_name)
    reply = c.launch_from_handoff(handoff)
    assert reply.status == 200
    assert reply.headers["Set-Cookie"].startswith(f"{ui.cookie_name}=")
    assert reply.headers["Set-Cookie"].endswith("; HttpOnly; SameSite=Strict")
    # No redirect: from a page on disk it would be a cross-site chain, and the
    # browser would not send the SameSite=Strict cookie on it.
    assert "Location" not in reply.headers
    assert '<script src="/static/opening.js" defer></script>' in reply.text
    assert token_of(ui) not in reply.text
    assert not handoff.exists() and not handoff.parent.exists()
    assert events == ["started"]
    assert c.get("/status", headers={"Sec-Fetch-Site": "same-origin"}).status == 200


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": "http://{name}:{port}0"}, 403),
        ({"Origin": "http://127.0.0.1:{port}"}, 403),
        ({"Sec-Fetch-Site": "same-site"}, 403),
        ({"Content-Type": "text/plain"}, 400),
        ({"Origin": None, "Sec-Fetch-Site": None}, 200),
        ({"Origin": "http://{name}:{port}", "Sec-Fetch-Site": "same-origin"}, 200),
        ({"Sec-Fetch-Site": "none"}, 200),
    ],
)
def test_the_launch_post_takes_the_null_origin_and_nothing_wider(
    ui: Ui, headers: dict[str, str | None], status: int
) -> None:
    sent = {
        "Origin": "null",
        "Sec-Fetch-Site": "cross-site",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for name, value in headers.items():
        if value is None:
            del sent[name]
        else:
            sent[name] = value.format(name=ui.host_name, port=ui.port)
    body = f"token={token_of(ui)}".encode()
    assert (
        UiClient(ui.port, ui.host_name).request("POST", "/launch", body=body, headers=sent).status
        == status
    )
    # Only the launch exchange: every other POST still needs the exact origin.
    other = UiClient(ui.port, ui.host_name).request("POST", "/session/end", body=b"", headers=sent)
    assert other.status in (400, 403)


@pytest.mark.parametrize("body", [b"", b"token=", b"token=wrong", b"token=a&token=b"])
def test_a_wrong_launch_post_gets_the_not_started_page(ui: Ui, body: bytes) -> None:
    sent = {"Origin": "null", "Content-Type": "application/x-www-form-urlencoded"}
    reply = UiClient(ui.port, ui.host_name).request("POST", "/launch", body=body, headers=sent)
    assert reply.status == 403 and "This session has not started yet" in reply.text
    assert UiClient(ui.port, ui.host_name).launch(ui.launch_url).status == 303  # still unused


def test_a_used_token_posted_again_without_the_session_ends_everything(
    ui: Ui, events: list[str]
) -> None:
    handoff = ui.write_handoff()
    copy = handoff.read_text(encoding="utf-8")
    assert UiClient(ui.port, ui.host_name).launch_from_handoff(handoff).status == 200
    handoff.parent.mkdir()
    handoff.write_text(copy, encoding="utf-8")  # a copy someone kept
    try:
        assert UiClient(ui.port, ui.host_name).launch_from_handoff(handoff).status == 403
    finally:
        handoff.unlink()
        handoff.parent.rmdir()
    ui.stop("closed")  # the linger's end; the reason stays the theft
    assert ui.wait(5) == "stolen"
    assert events == ["started", "stolen"]


def test_the_handoff_page_posts_the_token_once_and_keeps_nothing() -> None:
    """Back, forward, a bfcache restore or a double submit must not post the used
    token again: that would look like theft and end the session."""
    page = ui_server.handoff_text("en", "http://127.0.0.1:1/launch", "token")
    script = page[page.index("<script>") : page.index("</script>")]
    submit = script.index("form.submit();")
    assert script.index('form.elements.token.value = "";', submit) > submit  # cleared at once
    assert "form.remove();" in script  # and the form goes
    assert 'addEventListener("pageshow"' in script and "event.persisted" in script
    assert "You can close this tab." in script
    assert "<button" not in page  # nothing left to click a second time
    assert '<meta name="referrer" content="no-referrer">' in page


def test_the_handoff_folder_has_a_random_name(ui: Ui) -> None:
    first = ui.write_handoff().parent
    ui.remove_handoff()
    second = ui.write_handoff().parent
    assert first != second
    for folder in (first, second):
        # The owner's process id, then a random part: a sweep can tell it is ours.
        assert re.fullmatch(rf"ui-launch-{os.getpid()}-[A-Za-z0-9_]{{8}}", folder.name)


def test_the_handoff_file_is_always_new_and_never_a_link(
    monkeypatch: pytest.MonkeyPatch, ui: Ui, tmp_path: Path
) -> None:
    flags: list[int] = []
    real_open = os.open

    def spy(path: Any, flag: int, *args: Any) -> int:
        flags.append(flag)
        return real_open(path, flag, *args)

    with monkeypatch.context() as m:
        m.setattr(os, "open", spy)
        ui.write_handoff()
    ui.remove_handoff()
    assert flags[-1] & os.O_EXCL and flags[-1] & os.O_NOFOLLOW and flags[-1] & os.O_CREAT

    # Something planted at the path, a file or a link to one, is never written.
    target = tmp_path / "target"
    target.write_text("untouched", encoding="utf-8")
    for planted in ("file", "link"):
        folder = tmp_path / f"planted-{planted}"
        folder.mkdir(mode=0o700)
        path = folder / ui_server.HANDOFF_NAME
        if planted == "file":
            path.write_text("untouched", encoding="utf-8")
        else:
            path.symlink_to(target)
        with monkeypatch.context() as m:
            m.setattr(tempfile, "mkdtemp", lambda folder=folder, **kw: str(folder))
            with pytest.raises(OSError):
                ui.write_handoff()
        assert target.read_text(encoding="utf-8") == "untouched"
        assert ui._handoff is None


def test_handoff_folders_go_in_the_user_temp_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Not next to the database, which may be on a volume where modes mean nothing."""
    mine = tmp_path / "T"
    mine.mkdir(mode=0o700)
    monkeypatch.setattr(os, "confstr", lambda name: str(mine))
    assert real_handoff_root() == mine


def test_the_user_temp_folder_comes_from_macos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mine = tmp_path / "T"
    mine.mkdir(mode=0o700)
    monkeypatch.setattr(os, "confstr", lambda name: str(mine) + "/")
    assert ui_server.user_temp_dir() == mine
    fallback = Path(tempfile.gettempdir())
    mine.chmod(0o755)  # readable by others: not used
    assert ui_server.user_temp_dir() == fallback
    mine.chmod(0o700)

    def unknown(name: str) -> str:
        raise ValueError("unrecognized configuration name")

    monkeypatch.setattr(os, "confstr", unknown)
    assert ui_server.user_temp_dir() == fallback
    monkeypatch.setattr(os, "confstr", lambda name: None)
    assert ui_server.user_temp_dir() == fallback


def test_stale_handoff_folders_are_swept_at_start(
    loopback_network: None, cfg: Config, fake_browser: Path
) -> None:
    root = ui_server.handoff_root()
    gone = 2**22 + 12345  # a process id no process has (macOS stops at 99998)
    stale = root / f"ui-launch-{gone}-abcdefgh"
    stale.mkdir(mode=0o700)
    (stale / ui_server.HANDOFF_NAME).write_text("old token", encoding="utf-8")
    old = root / "ui-launch-stale123"
    old.mkdir(mode=0o700)
    hour_ago = time.time() - 3600
    os.utime(old, (hour_ago, hour_ago))
    other = root / "something-else"
    other.mkdir()
    name_record = root / f"{ui_server.NAME_PREFIX}{'0' * 32}.json"
    name_record.write_text(json.dumps({"pid": gone, "port": 5, "host": "127.0.0.1"}))
    name_record.chmod(0o600)
    sender = interrupt_when_listening(signal.SIGTERM)
    assert ui_server.run(cfg, browser=False, echo=lambda line: None) == "signal"
    sender.join()
    assert not name_record.exists()  # a private name record of a flib ui that is gone
    assert not stale.exists()  # its owner is gone
    assert not old.exists()  # too old to belong to a link anyone still opens
    assert other.exists()  # only handoff folders


def test_a_running_flib_uis_handoff_folder_is_left_alone(tmp_path: Path) -> None:
    """Another ``flib ui`` (another configuration, so another lock) may be waiting for
    its browser: its fresh folder, whose owner lives, is not swept."""
    alive = tmp_path / f"ui-launch-{os.getppid()}-abcdefgh"
    alive.mkdir(mode=0o700)
    unnamed = tmp_path / "ui-launch-xyz"  # no owner recorded, but fresh
    unnamed.mkdir(mode=0o700)
    ui_server.sweep_handoffs(tmp_path)
    assert alive.exists() and unnamed.exists()
    later = time.time() + ui_server.HANDOFF_STALE_SECONDS + 1
    ui_server.sweep_handoffs(tmp_path, now=later)
    assert not alive.exists() and not unnamed.exists()


def test_a_flib_ui_that_ends_removes_the_opener_folders_named_after_it(tmp_path: Path) -> None:
    mine = tmp_path / f"ui-launch-{os.getpid()}-abcdefgh"
    mine.mkdir(mode=0o700)
    (mine / ui_server.HANDOFF_NAME).write_text("opener", encoding="utf-8")
    other = tmp_path / f"ui-launch-{os.getppid()}-abcdefgh"
    other.mkdir(mode=0o700)
    ui_server.sweep_handoffs(tmp_path)
    assert mine.exists()  # its own, while it runs
    ui_server.sweep_handoffs(tmp_path, mine=True)
    assert not mine.exists() and other.exists()


def test_flib_ui_removes_its_opener_files_when_it_ends(
    loopback_network: None, cfg: Config, fake_browser: Path
) -> None:
    opener: list[Path] = []

    def open_a_page_then_interrupt() -> None:
        wait_for(lambda: ui_server.running(cfg.database) is not None, timeout=10)
        where = ui_server.running(cfg.database)
        assert where is not None and ui_server.NAME.fullmatch(where.host)
        opener.append(ui_server.write_opener(where, "review"))
        os.kill(os.getpid(), signal.SIGTERM)

    sender = Background(open_a_page_then_interrupt)
    assert ui_server.run(cfg, browser=False, echo=lambda line: None) == "signal"
    sender.join()
    assert opener and not opener[0].parent.exists()


# --- the session's name (#125) ----------------------------------------------------------


def _answers(*addresses: str) -> Callable[..., list[tuple[Any, ...]]]:
    asked: list[str] = []

    def lookup(host: str, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        asked.append(host)
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0))
            for a in addresses
        ]

    lookup.asked = asked  # type: ignore[attr-defined]
    return lookup


@pytest.mark.parametrize(
    ("addresses", "resolves"),
    [
        (("::1", "127.0.0.1"), True),  # what current macOS answers
        (("127.0.0.1",), True),
        (("127.0.0.2",), True),
        (("fe80::1%lo0",), False),  # a link-local address is not loopback
        (("::1", "192.168.1.10"), False),  # a DNS server's answer mixed in
        (("203.0.113.7",), False),
        ((), False),
        (("not an address",), False),
    ],
)
def test_a_localhost_name_counts_only_when_it_resolves_to_loopback_only(
    monkeypatch: pytest.MonkeyPatch, addresses: tuple[str, ...], resolves: bool
) -> None:
    lookup = _answers(*addresses)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    assert real_names_resolve() is resolves
    # Asked with a throwaway name, never the session's, a new one each time.
    assert real_names_resolve() is resolves
    asked = lookup.asked  # type: ignore[attr-defined]
    assert len(asked) == 2 and asked[0] != asked[1]
    assert all(ui_server.NAME.fullmatch(name) for name in asked)


def test_a_localhost_name_that_does_not_resolve_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fails(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")

    monkeypatch.setattr(socket, "getaddrinfo", fails)
    assert real_names_resolve() is False


def test_without_names_flib_ui_serves_at_127_0_0_1_and_says_what_that_leaves(
    loopback_network: None, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never a silent downgrade: a Mac whose resolver does not give loopback for
    ``.localhost`` still gets a working interface, and is told on stderr what is
    then open to other servers on 127.0.0.1."""
    monkeypatch.setattr(ui_server, "names_resolve", lambda: False)
    lines: list[str] = []
    warnings: list[str] = []
    sender = interrupt_when_listening(signal.SIGTERM)
    assert ui_server.run(cfg, browser=False, echo=lines.append, warn=warnings.append) == "signal"
    sender.join()
    assert warnings == [ui_server.UNNAMED_WARNING]
    assert "browsers send its session cookie to every server on 127.0.0.1" in warnings[0]
    assert any(re.search(r"http://127\.0\.0\.1:\d+/launch\?token=", line) for line in lines)


# --- /open: where an opener file sends the browser --------------------------------------


@pytest.mark.parametrize("site", [None, "cross-site", "none", "same-origin"])
def test_open_moves_on_to_the_page_from_this_origin(ui: Ui, site: str | None) -> None:
    """An opener file is a page on disk: its navigation is cross-site, so the Strict
    cookie does not go with it. ``/open`` holds nothing and moves on, from this
    origin, to the page, which then gets the cookie."""
    stranger = UiClient(ui.port, ui.host_name)
    headers = {"Accept": "text/html"} | ({} if site is None else {"Sec-Fetch-Site": site})
    for page in ui_server.PAGES:
        reply = stranger.get(f"/open?page={page}", headers=headers)
        assert reply.status == 200, (page, site)
        assert f'<a id="continue" href="/{page}">' in reply.text
        assert '<script src="/static/opening.js" defer></script>' in reply.text
        assert "csrf" not in reply.text and "{{" not in reply.text
        assert "Set-Cookie" not in reply.headers
    assert stranger.get("/status").status == 403  # it gave nothing


@pytest.mark.parametrize(
    ("target", "headers", "status"),
    [
        ("/open", {}, 400),
        ("/open?page=nope", {}, 400),
        ("/open?page=../launch", {}, 400),
        ("/open?page=review&page=due", {}, 400),
        ("/open?page=review&token=x", {}, 400),
        ("/open?page=review", {"Sec-Fetch-Site": "same-site"}, 403),
        ("/open?page=review", {"Origin": "null"}, 403),
        ("/open?page=review", {"Origin": "http://evil.example"}, 403),
    ],
)
def test_open_takes_a_page_and_nothing_else(
    ui: Ui, target: str, headers: dict[str, str], status: int
) -> None:
    assert UiClient(ui.port, ui.host_name).get(target, headers=headers).status == status


def test_open_is_a_get_only(client: UiClient, ui: Ui) -> None:
    """Its leeway (a cross-site navigation) is for a GET: a cross-site POST is refused
    by the origin checks, even with the session's cookie and CSRF token (not a 404)."""
    reply = client.request(
        "POST",
        "/open?page=review",
        body=f"csrf={client.csrf()}".encode(),
        headers={
            "Sec-Fetch-Site": "cross-site",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert reply.status == 403


def test_without_a_name_there_is_no_open(loopback_network: None, cfg: Config) -> None:
    """At 127.0.0.1 the address holds no secret, so the second ``flib ui --open`` opens it
    directly: no cross-site page is let in."""
    server = Ui(cfg, named=False)
    server.start()
    try:
        c = UiClient(server.port)
        assert c.get("/open?page=review", headers={"Sec-Fetch-Site": "cross-site"}).status == 403
        assert c.get("/open?page=review").status == 404
    finally:
        server.close()


@pytest.mark.parametrize(
    "header", ["Sec-Purpose: prefetch", "Sec-Purpose: prefetch;prerender", "Purpose: prefetch"]
)
def test_a_prefetch_of_the_link_does_not_use_it(ui: Ui, events: list[str], header: str) -> None:
    """A browser may prerender a typed address before Enter: that must not use the
    token, nor count as its theft."""
    name, _, value = header.partition(": ")
    split = ui.launch_url.split("/", 3)[3]
    reply = UiClient(ui.port, ui.host_name).get(
        f"/{split}", headers={name: value, "Sec-Fetch-Site": "none"}
    )
    assert reply.status == 403
    assert "Set-Cookie" not in reply.headers
    assert events == []
    assert UiClient(ui.port, ui.host_name).launch(ui.launch_url).status == 303  # still unused
    assert_running(ui)


def test_the_token_is_never_taken_from_the_query_of_a_post(ui: Ui, events: list[str]) -> None:
    reply = UiClient(ui.port, ui.host_name).request(
        "POST",
        f"/launch?token={token_of(ui)}",
        body=b"x=1",
        headers={"Origin": "null", "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert reply.status == 400
    assert events == []
    assert UiClient(ui.port, ui.host_name).launch(ui.launch_url).status == 303  # still unused


def test_two_origin_headers_are_forbidden(client: UiClient, ui: Ui) -> None:
    for origins in ((client.origin, "http://evil.example"), (client.origin, client.origin)):
        head = f"GET /status HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
        head += f"Cookie: {ui.cookie_name}={client.cookies[ui.cookie_name]}\r\n"
        head += "".join(f"Origin: {origin}\r\n" for origin in origins)
        assert client.raw((head + "\r\n").encode()).startswith(b"HTTP/1.0 403 "), origins


def test_two_csrf_headers_are_forbidden(client: UiClient, ui: Ui) -> None:
    token = client.csrf()
    head = (
        f"POST /session/end HTTP/1.1\r\nHost: {ui.host_name}:{ui.port}\r\n"
        f"Origin: {client.origin}\r\n"
        f"Cookie: {ui.cookie_name}={client.cookies[ui.cookie_name]}\r\n"
        f"X-Flib-CSRF: {token}\r\nX-Flib-CSRF: {token}\r\nContent-Length: 0\r\n\r\n"
    )
    assert client.raw(head.encode()).startswith(b"HTTP/1.0 403 ")
    assert_running(ui)


def test_the_opening_script_reads_nothing_from_the_address() -> None:
    script = load_assets()["/static/opening.js"][0].decode("utf-8")
    for read in ("location.hash", "location.search", "location.href", "document.URL"):
        assert read not in script, read
    assert "document.referrer" not in script


# --- the packaged pages ----------------------------------------------------------------


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []
        self.problems: list[str] = []
        self.disabled: list[str] = []
        self._in: list[str] = []
        self._last_disabled = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._in.append(tag)
        values = dict(attrs)
        if tag == "style":
            self.problems.append("a <style> element")
        if tag == "script" and not values.get("src"):
            self.problems.append("an inline <script>")
        for name, value in attrs:
            if name == "style" or name.startswith("on"):
                self.problems.append(f"a {name}= attribute")
            if name in ("src", "href", "action") and value is not None:
                self.refs.append(value)
                if value.lower().startswith(("javascript:", "data:", "http:", "https:", "//")):
                    self.problems.append(f"{name}={value}")
        self._last_disabled = values.get("aria-disabled") == "true"

    def handle_endtag(self, tag: str) -> None:
        if self._in:
            self._in.pop()

    def handle_data(self, data: str) -> None:
        if self._in and self._in[-1] == "script" and data.strip():
            self.problems.append("an inline <script>")
        if self._last_disabled and data.strip():
            self.disabled.append(data.strip())
            self._last_disabled = False


def test_every_page_references_only_packaged_files_and_has_no_inline_code() -> None:
    assets = load_assets()
    pages = load_pages()
    assert set(pages) == {"status", "ended", "opening", "elsewhere", "open", "waiting", "stolen"}
    routes = {"/"} | {f"/{p}" for p in ui_server.PAGES} | set(ui_server.POST_ROUTES)
    for name, html in pages.items():
        html = html.replace("{{next}}", ui_server.PAGE_PATHS["review"])  # what /open fills in
        parser = _Collector()
        parser.feed(html)
        assert parser.problems == [], name
        assert parser.refs, name
        for ref in parser.refs:
            assert ref in assets or ref in routes or ref == "#main", (name, ref)
    for name, (body, _) in assets.items():
        text = body.decode("utf-8", "replace") if not name.endswith(".png") else ""
        for ref in re.findall(r"url\(([^)]*)\)", text):
            assert ref.strip("'\"") in assets, (name, ref)
        assert "innerHTML" not in text and "eval(" not in text, name


def test_the_script_calls_only_served_routes(client: UiClient) -> None:
    script = client.get("/static/status.js").text
    paths = re.findall(r"""["'](/api/[^"']*)["']""", script)
    assert paths == ["/api/v1/status"]
    for path in paths:
        assert client.get(path).status == 200


def test_every_packaged_static_file_is_served() -> None:
    folder = REPO_ROOT / "src" / "file_librarian" / "ui" / "static"
    served = {name.removeprefix("/static/") for name in load_assets()}
    assert {p.name for p in folder.iterdir()} <= served


def test_the_policy_allows_no_inline_code() -> None:
    assert "'unsafe-inline'" not in CSP and "'unsafe-eval'" not in CSP
    assert "default-src 'none'" in CSP
    assert "frame-ancestors 'none'" in CSP and "base-uri 'none'" in CSP
    assert "form-action 'self'" in CSP
    assert "*" not in CSP and "http" not in CSP


def test_the_navigation_has_no_disabled_item_left() -> None:
    parser = _Collector()
    parser.feed(load_pages()["status"])
    assert parser.disabled == []
    assert '<a href="/settings">Settings</a>' in markup.page(load_pages()["status"])
