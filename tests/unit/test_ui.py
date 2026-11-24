"""``flib ui`` (#113): the server, its session and every security rule.

Each server runs in-process on an ephemeral 127.0.0.1 port (``loopback_network``);
nothing else is reachable, and the browser is the fake ``open`` of conftest.py.
Each rule has a request that breaks it.
"""

from __future__ import annotations

import faulthandler
import hmac
import json
import os
import re
import signal
import socket
import sqlite3
import threading
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline
from file_librarian.cli import app
from file_librarian.config import Config, ConfigError, parse_config
from file_librarian.lock import ui_lock
from file_librarian.providers.fake import FakeProvider
from file_librarian.status import StatusError
from file_librarian.ui import api
from file_librarian.ui import server as ui_server
from file_librarian.ui.server import CSP, Ui, UiError, check_bind, load_assets, load_pages
from tests._helpers import REPO_ROOT, UiClient, answer, fake_config, put, wait_for, write_config

runner = CliRunner()

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@pytest.fixture(autouse=True)
def no_hang() -> Iterator[None]:
    """A test that waits on a server for over a minute is stuck: print every thread's
    stack and exit, rather than let the run sit until CI times it out."""
    faulthandler.dump_traceback_later(60, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def ui(loopback_network: None, cfg: Config) -> Iterator[Ui]:
    server = Ui(cfg, poll_seconds=0.01)
    server.start()
    yield server
    server.close()


@pytest.fixture
def client(ui: Ui) -> UiClient:
    """A client with a session: the launch link was opened."""
    c = UiClient(ui.port)
    assert c.launch(ui.launch_url).status == 303
    return c


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


def test_the_server_listens_on_127_0_0_1_only(ui: Ui) -> None:
    assert ui._server.socket.getsockname()[0] == "127.0.0.1"
    assert ui.port > 0
    assert ui.launch_url.startswith(f"http://127.0.0.1:{ui.port}/launch?token=")


def test_a_wildcard_host_is_refused_before_binding(
    monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    monkeypatch.setattr(ui_server, "HOST", WILDCARD)
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
    c = UiClient(ui.port)
    reply = c.launch(ui.launch_url)
    assert reply.status == 303
    assert reply.headers["Location"] == "/status"  # the token leaves the address bar
    assert reply.headers["Referrer-Policy"] == "no-referrer"
    cookie = reply.headers["Set-Cookie"]
    assert cookie.startswith(f"flib_ui_{ui.port}=")
    assert "; Path=/; HttpOnly; SameSite=Strict" in cookie
    assert token_of(ui) not in cookie

    page = c.get("/status")
    assert page.status == 200
    assert "<h1>Status</h1>" in page.text
    assert "{{csrf}}" not in page.text
    assert token_of(ui) not in page.text
    assert c.get("/").headers["Location"] == "/status"


def test_the_launch_token_works_once(ui: Ui) -> None:
    assert UiClient(ui.port).launch(ui.launch_url).status == 303
    stale = UiClient(ui.port)
    reply = stale.launch(ui.launch_url)
    assert reply.status == 403
    assert "This session has ended" in reply.text
    assert "Set-Cookie" not in reply.headers
    assert stale.get("/status").status == 403


def test_the_browser_with_the_session_may_open_the_link_again(client: UiClient, ui: Ui) -> None:
    reply = client.launch(ui.launch_url)
    assert reply.status == 303 and reply.headers["Location"] == "/status"
    assert "Set-Cookie" not in reply.headers


@pytest.mark.parametrize(
    "query", ["", "token=", "token=wrong", "tok=X", "token=X&token=X", "token=%FF%FE"]
)
def test_a_wrong_or_missing_token_gets_the_ended_page(ui: Ui, query: str) -> None:
    query = query.replace("X", token_of(ui))
    reply = UiClient(ui.port).get(f"/launch?{query}")
    assert reply.status == 403
    assert "This session has ended" in reply.text
    # A wrong guess does not burn the real link.
    assert UiClient(ui.port).launch(ui.launch_url).status == 303


def test_too_many_query_fields_are_refused(ui: Ui) -> None:
    reply = UiClient(ui.port).get("/launch?" + "&".join(f"a{i}=1" for i in range(10)))
    assert reply.status == 400


def test_the_token_is_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch, ui: Ui) -> None:
    seen = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return bool(real(a, b))

    monkeypatch.setattr(hmac, "compare_digest", spy)
    UiClient(ui.port).get("/launch?token=guess")
    assert (b"guess", token_of(ui).encode()) in seen


@pytest.mark.parametrize("cookie", [None, "wrong", "OTHER_PORT"])
def test_pages_and_the_api_need_the_session(ui: Ui, cookie: str | None) -> None:
    session = UiClient(ui.port)
    session.launch(ui.launch_url)
    real = session.cookies[ui.cookie_name]
    c = UiClient(ui.port)
    if cookie == "wrong":
        c.cookies[ui.cookie_name] = real[:-2] + "xx"
    elif cookie == "OTHER_PORT":
        c.cookies[f"flib_ui_{ui.port + 1}"] = real  # cookies are not kept apart by port
    for path in ("/", "/status"):
        reply = c.get(path)
        assert reply.status == 403 and "This session has ended" in reply.text
    api_reply = c.get("/api/v1/status")
    assert api_reply.status == 401
    assert api_reply.json() == {"error": "no session"}


def test_the_ended_page_is_styled_without_a_session(ui: Ui) -> None:
    c = UiClient(ui.port)
    assert c.get("/static/app.css").status == 200
    assert c.get("/static/icon.png").status == 200


# --- Host, Origin and fetch metadata ---------------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "evil.example:{port}",
        "127.0.0.1:{other}",
        "127.0.0.1",
        "localhost",
        "LOCALHOST:{port}",
        "127.0.0.1.:{port}",
        "[::1]:{port}",
        "0.0.0.0:{port}",
        "",
    ],
)
def test_a_foreign_host_header_is_misdirected(client: UiClient, ui: Ui, host: str) -> None:
    host = host.format(port=ui.port, other=ui.port + 1)
    for path in ("/status", "/api/v1/status", "/static/app.css", ui.launch_url[22:]):
        reply = client.get(path, host=host)
        assert reply.status == 421, (host, path)
        assert reply.body == b"Misdirected Request\n"


def test_both_loopback_names_are_accepted(client: UiClient, ui: Ui) -> None:
    assert client.get("/status", host=f"localhost:{ui.port}").status == 200
    assert client.get("/status", host=f"127.0.0.1:{ui.port}").status == 200


def test_two_host_headers_are_misdirected(client: UiClient, ui: Ui) -> None:
    reply = client.raw(
        f"GET /static/app.css HTTP/1.1\r\nHost: 127.0.0.1:{ui.port}\r\n"
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
        {"Origin": "http://127.0.0.1:{other}"},
        {"Origin": "https://127.0.0.1:{port}"},
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
            sent[name] = value.format(port=ui.port, other=ui.port + 1)
    body = f"csrf={token}".encode()
    reply = client.request("POST", "/session/end", body=body, headers=sent)
    assert reply.status == 403
    assert_running(ui)


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
    other = UiClient(ui.port)
    assert other.post_form("/session/end", {"csrf": token}).status == 403
    assert_running(ui)


def test_the_csrf_token_is_in_a_form_field_or_a_header(client: UiClient, ui: Ui) -> None:
    token = client.csrf()
    page = client.get("/status").text
    assert f'<meta name="flib-csrf" content="{token}">' in page
    # A JSON body does not count as a form.
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


def test_a_post_to_an_unknown_path_is_not_found(client: UiClient, ui: Ui) -> None:
    token = client.csrf()
    assert client.post_form("/api/v1/status", {"csrf": token}).status == 404
    assert client.post_form("/nothing", {"csrf": token}).status == 404
    assert_running(ui)


# --- End session -----------------------------------------------------------------------


def test_end_session_shows_the_ended_page_and_stops_the_server(client: UiClient, ui: Ui) -> None:
    reply = client.post_form("/session/end", {"csrf": client.csrf()})
    assert reply.status == 200
    assert "This session has ended" in reply.text
    assert ui.wait(5) == "ended"
    with pytest.raises(OSError):
        client.get("/status")


# --- limits ----------------------------------------------------------------------------


def test_a_large_body_is_refused_before_it_is_read(client: UiClient, ui: Ui) -> None:
    head = (
        f"POST /session/end HTTP/1.1\r\nHost: 127.0.0.1:{ui.port}\r\n"
        f"Origin: {client.origin}\r\nContent-Length: 100000\r\n\r\n"
    )
    assert client.raw(head.encode()).startswith(b"HTTP/1.0 413 ")


@pytest.mark.parametrize(
    ("extra", "status"),
    [("", b"411"), ("Content-Length: abc\r\n", b"400"), ("Transfer-Encoding: chunked\r\n", b"400")],
)
def test_a_post_needs_a_plain_length(client: UiClient, ui: Ui, extra: str, status: bytes) -> None:
    head = (
        f"POST /session/end HTTP/1.1\r\nHost: 127.0.0.1:{ui.port}\r\n"
        f"Origin: {client.origin}\r\n{extra}\r\n"
    )
    assert client.raw(head.encode()).startswith(b"HTTP/1.0 " + status + b" ")


def test_long_request_lines_and_headers_are_refused(client: UiClient, ui: Ui) -> None:
    assert client.get("/static/app.css?" + "a" * 5000).status == 414
    assert client.get("/static/app.css", headers={"X-Big": "a" * 20000}).status == 431
    many = {f"X-H{i}": "1" for i in range(150)}
    assert client.get("/static/app.css", headers=many).status == 431
    assert client.raw(b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.0 414 ")


def test_a_slow_client_is_dropped(loopback_network: None, cfg: Config) -> None:
    ui = Ui(cfg, poll_seconds=0.01, request_timeout=0.2)
    ui.start()
    try:
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            sock.sendall(b"GET /status HTTP/1.1\r\n")  # and nothing more
            assert sock.recv(1024) == b""  # closed after the timeout, without a reply
    finally:
        ui.close()


def test_requests_beyond_the_thread_cap_are_turned_away(
    loopback_network: None, cfg: Config
) -> None:
    ui = Ui(cfg, poll_seconds=0.01, max_threads=1, request_timeout=5)
    ui.start()
    try:
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as hog:
            hog.sendall(b"GET /status HTTP/1.1\r\n")  # holds the only thread
            # Turned away at once, before it says anything (a request it had sent would
            # be unread, and closing on unread data resets the connection).
            with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as extra:
                assert extra.recv(1024).startswith(b"HTTP/1.0 503 ")
        # The slot comes back once the hog is gone.
        wait_for(lambda: UiClient(ui.port).get("/static/app.css").status == 200)
    finally:
        ui.close()


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
    replies = [
        client.get("/status"),
        client.get("/api/v1/status"),
        client.get("/static/app.css"),
        client.get("/static/status.js"),
        client.get("/"),
        client.get("/nothing"),
        client.get("/status", host="evil"),
        client.request("PUT", "/status"),
        UiClient(ui.port).get("/status"),
        UiClient(ui.port).get("/launch?token=x"),
    ]
    for reply in replies:
        for name, value in SECURITY_HEADERS.items():
            assert reply.headers[name] == value, (reply.status, name)


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
    ui = Ui(cfg, poll_seconds=0.01, request_timeout=0.2)
    ui.start()
    try:
        c = UiClient(ui.port)
        c.get(f"/launch?token={token_of(ui)[:-1]}x")
        c.launch(ui.launch_url)
        c.get("/status")
        c.get("/nothing")
        c.request("BREW", "/")
        with socket.create_connection(("127.0.0.1", ui.port), timeout=5) as sock:
            sock.sendall(b"GET /launch?token=" + token_of(ui).encode())  # then times out
            sock.recv(1024)
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
        c = UiClient(ui.port)
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
        assert ui.wait(0.1) is None
        UiClient(ui.port).get("/static/app.css")  # a request resets the timer
        clock.now += 599
        assert ui.wait(0.1) is None
        clock.now += 2
        assert ui.wait(5) == "idle"
    finally:
        ui.close()
    assert ui.reason == "idle"


def test_a_rejected_host_does_not_keep_the_server_alive(
    loopback_network: None, cfg: Config
) -> None:
    clock = FakeClock()
    ui = Ui(cfg, clock=clock, poll_seconds=0.01, idle_seconds=60)
    ui.start()
    try:
        clock.now += 59
        UiClient(ui.port).get("/static/app.css", host="evil.example")
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


def end_from_the_browser(log: Path) -> threading.Thread:
    """In the background: wait for the fake ``open``, open the link, End session."""

    def browse() -> None:
        wait_for(lambda: opened(log), timeout=30)  # a cold Python start on CI is slow
        url = opened(log)[0][0]
        c = UiClient(int(url.split(":")[2].split("/")[0]))
        c.launch(url)
        c.post_form("/session/end", {"csrf": c.csrf()})

    thread = threading.Thread(target=browse, daemon=True)
    thread.start()
    return thread


def interrupt_when_listening(signum: int) -> threading.Thread:
    """In the background: send ``signum`` once ``flib ui`` handles it."""
    original = signal.getsignal(signum)

    def send() -> None:
        wait_for(lambda: signal.getsignal(signum) is not original, timeout=30)
        os.kill(os.getpid(), signum)

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    return thread


def test_flib_ui_opens_the_link_and_ends_with_the_session(
    loopback_network: None, tmp_path: Path, downloads: Path, fake_browser: Path
) -> None:
    path = write_config(tmp_path, downloads)
    browser = end_from_the_browser(fake_browser)
    result = runner.invoke(app, ["ui", "-c", str(path)])
    browser.join(5)
    assert result.exit_code == 0, result.stdout + result.stderr
    [[url]] = opened(fake_browser)
    assert url.startswith("http://127.0.0.1:") and "/launch?token=" in url
    assert f"flib ui: {url}" in result.stdout
    assert "The link works once" in result.stdout
    assert "flib ui ended: the session was ended in the browser." in result.stdout


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_flib_ui_ends_on_a_signal(
    loopback_network: None, cfg: Config, fake_browser: Path, signum: int
) -> None:
    before = signal.getsignal(signum)
    lines: list[str] = []
    sender = interrupt_when_listening(signum)
    assert ui_server.run(cfg, browser=False, echo=lines.append) == "signal"
    sender.join(5)
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
    browser.join(5)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "warning: could not open the browser (open exited with 1)" in result.stdout
    assert "token" not in result.stdout.split("warning:")[1]


def test_flib_ui_without_open_says_why(
    loopback_network: None, monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    monkeypatch.setattr(ui_server, "OPEN", "/nonexistent/open")
    assert ui_server.open_url("http://127.0.0.1:1/launch?token=x") == "FileNotFoundError"


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
    assert result.exit_code == 1
    assert "no page 'nope'; one of: status" in result.stderr
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
    sender.join(5)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "/launch?token=" in result.stdout
    assert opened(fake_browser) == []


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
    assert set(pages) == {"status", "ended"}
    routes = {f"/{p}" for p in ui_server.PAGES} | {"/session/end"}
    for name, html in pages.items():
        parser = _Collector()
        parser.feed(html)
        assert parser.problems == [], name
        assert parser.refs, name
        for ref in parser.refs:
            assert ref in assets or ref in routes, (name, ref)
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
    assert "*" not in CSP and "http" not in CSP


def test_the_navigation_lists_what_comes_next_disabled() -> None:
    parser = _Collector()
    parser.feed(load_pages()["status"])
    assert parser.disabled == ["Review", "Search", "Activity", "Due", "Settings"]
