"""``flib ui`` in a real browser (#114): a smoke test over Chrome's DevTools protocol.

Opt-in: ``FLIB_TEST_BROWSER=1 uv run pytest -m browser`` (CI runs it on macOS, whose
runner image has Chrome). It finds an installed Chrome or Chromium
(``FLIB_TEST_BROWSER_PATH`` names another), runs it headless with a throwaway
profile, and drives it like a person: the launch through the handoff file, a search
submitted by a real click (post, redirect, get; Back and Forward), the CSV export,
and End session, whose page must still get its stylesheet. Every form post goes
through the server's real origin checks, which is what unit tests with a hand-made
``Origin`` header could not show: a browser sends ``Origin: null`` under some
referrer policies. Nothing goes beyond 127.0.0.1.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import secrets
import shutil
import socket
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from file_librarian import config_edit, pipeline
from file_librarian.config import Category, Config, load_config
from file_librarian.db.store import Store
from file_librarian.providers.fake import FakeProvider
from file_librarian.ui.server import Running, Ui, write_opener
from tests._cdp import find_browser, headless
from tests._helpers import answer, put
from tests.unit.test_ui import nothing_left_behind  # noqa: F401 - no socket left behind

BROWSER = find_browser()

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        os.environ.get("FLIB_TEST_BROWSER") != "1" or BROWSER is None,
        reason="the real-browser test is opt-in: FLIB_TEST_BROWSER=1 and Chrome or Chromium",
    ),
]


def test_a_session_in_a_real_browser(
    loopback_network: None, downloads: Path, cfg: Config, tmp_path: Path
) -> None:
    assert BROWSER is not None
    put(downloads, "power bill.pdf", b"power")
    put(downloads, "water bill.pdf", b"water")
    pipeline.run(
        cfg,
        FakeProvider(
            {
                "power bill.pdf": answer(
                    "Finance", description="Power", amount="10", currency="USD"
                ),
                "water bill.pdf": answer("Finance", description="Water"),
            }
        ),
        simulate=False,
    )
    ui = Ui(cfg, poll_seconds=0.05)
    ui.start()
    profile = tmp_path / "profile"
    downloads_to = tmp_path / "downloaded"
    downloads_to.mkdir()
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, browser):
            browser(
                "Browser.setDownloadBehavior",
                {"behavior": "allow", "downloadPath": str(downloads_to)},
            )
            # The launch: the browser opens the handoff file, which posts the token.
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            assert "Status" in tab.text()
            # A search, submitted by a click: a POST that must pass the origin checks.
            tab.goto(f"{ui.origin}/search")
            tab.evaluate("document.querySelector('input[name=q]').value = 'power'")
            tab.click("form.search button[type=submit]:not([formaction])")
            tab.wait_for(lambda: "/search?r=" in tab.url() and tab.ready())
            results = tab.url()
            assert "power" not in results  # the words stay out of the address
            assert "power bill.pdf" in tab.text() and "1 match" in tab.text()
            # Back and Forward: plain GETs, no "Confirm Form Resubmission".
            tab.evaluate("history.back()")
            tab.wait_for(lambda: tab.url().endswith("/search") and tab.ready())
            tab.evaluate("history.forward()")
            tab.wait_for(lambda: tab.url() == results and tab.ready())
            assert "power bill.pdf" in tab.text()
            # The export, from the results page.
            tab.click("button[formaction='/search/export']")
            csv = downloads_to / "flib-search.csv"
            tab.wait_for(lambda: csv.exists())
            assert "power bill.pdf" in csv.read_text(encoding="utf-8")
            # End session: the page says so, styled although the server is ending.
            tab.click("form.end button")
            tab.wait_for(lambda: "This session has ended" in tab.text() and tab.ready())
            tab.wait_for(
                lambda: bool(
                    tab.evaluate(
                        "getComputedStyle(document.body).backgroundColor !== 'rgba(0, 0, 0, 0)'"
                    )
                )
            )
        assert ui.wait(10) == "ended"
    finally:
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)


def test_review_and_undo_in_a_real_browser(
    loopback_network: None, downloads: Path, cfg: Config, tmp_path: Path
) -> None:
    """Accept a file by a real click (#115): it moves on disk; Back and Forward are
    plain GETs that do nothing again; Undo, confirmed by a click, puts it back."""
    assert BROWSER is not None
    put(downloads, "shot.png", b"pixels")
    put(downloads, "other.png", b"more pixels")
    unsure = answer("Images", confidence=0.3)
    pipeline.run(cfg, FakeProvider({"shot.png": unsure, "other.png": unsure}), simulate=False)
    in_review = cfg.output_root / cfg.review_folder / "shot.png"
    filed = cfg.output_root / "Images" / "shot.png"
    assert in_review.exists()
    with Store.open(cfg.database) as store:
        shot = store.file_by_current_path(in_review).id  # type: ignore[union-attr]
    # A category with the longest name a configuration takes (64 characters): its chip
    # on Search must wrap, not widen the page (#117 browser testing).
    long_name = Category(name=("Householdstatements" * 4)[:64])  # one word: it must break
    assert len(long_name.name) == 64
    cfg = cfg.model_copy(update={"categories": [*cfg.categories, long_name]})
    ui = Ui(cfg, poll_seconds=0.05)
    ui.start()
    profile = tmp_path / "profile"
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, _):
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            tab.goto(f"{ui.origin}/review")
            assert "2 files in review" in tab.text()
            # Accept, by a click on the first file's button: post, redirect, get.
            tab.click(f"form[action='/review/file']:has(input[name=id][value='{shot}']) button")
            tab.wait_for(lambda: "/review?n=" in tab.url() and tab.ready())
            after = tab.url()
            assert "is now in Images." in tab.text()
            assert filed.read_bytes() == b"pixels" and not in_review.exists()
            # Back and Forward: no resubmission, nothing moves again.
            tab.evaluate("history.back()")
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/review" and tab.ready())
            # (the page may come back as it was shown; its forms carry the pin, so a
            # second click on that old Accept would be refused, not repeated)
            assert "in review" in tab.text() and filed.read_bytes() == b"pixels"
            tab.evaluate("history.forward()")
            tab.wait_for(lambda: tab.url() == after and tab.ready())
            assert filed.read_bytes() == b"pixels"
            # Undo: the confirmation page (a GET), then a click.
            tab.click(".outcome a")
            tab.wait_for(lambda: "/undo?run=" in tab.url() and tab.ready())
            assert "shot.png" in tab.text() and filed.exists()  # nothing undone yet
            tab.click("form.confirm button")
            tab.wait_for(lambda: "/undo?n=" in tab.url() and tab.ready())
            assert "1 restored" in tab.text()
            assert in_review.read_bytes() == b"pixels" and not filed.exists()
            tab.evaluate("location.reload()")
            tab.wait_for(lambda: "/undo?n=" in tab.url() and tab.ready())
            assert in_review.exists() and not filed.exists()
            # At 390 px no page scrolls sideways, and every menu item is in view.
            tab.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True},
            )
            for page in (
                "status",
                "review",
                "search",
                "activity",
                "due",
                "settings",
                f"undo?file={shot}",
            ):
                tab.goto(f"{ui.origin}/{page}")
                # A phone widens its layout to fit what overflows: measure against 390.
                wide = tab.evaluate(
                    "Math.max(window.innerWidth, document.documentElement.scrollWidth) - 390"
                )
                assert wide <= 0, (page, wide)
                hidden = tab.evaluate(
                    "[...document.querySelectorAll('nav[aria-label=Pages] > *')]"
                    ".filter(e => e.getBoundingClientRect().right > 390).length"
                )
                assert hidden == 0, (page, hidden)
    finally:
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)


def test_settings_in_a_real_browser(
    loopback_network: None, downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    """Change a setting by a real click (#117): it is in the file on disk, with its
    comment; Back and Forward change nothing again; Restore, confirmed by a click, puts
    the file back byte for byte."""
    assert BROWSER is not None
    path = tmp_path / "config.yaml"
    path.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\n"
        "provider: {name: fake, model: x}\n"
        "vision:\n  enabled: false  # images stay here\n",
        encoding="utf-8",
    )
    original = path.read_bytes()
    ui = Ui(load_config(path), poll_seconds=0.05, config_path=path)
    ui.start()
    profile = tmp_path / "profile"
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, _):
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            tab.click("nav a[href='/settings']")
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/settings" and tab.ready())
            # Vision on, by a click on its box and on the section's Save.
            tab.click("#s-reading input[name=vision]")
            tab.click("#s-reading button[type=submit]")
            tab.wait_for(lambda: "/settings?n=" in tab.url() and tab.ready())
            after = tab.url()
            assert "Saved." in tab.text() and "off → on" in tab.text()
            assert "  enabled: true  # images stay here" in path.read_text(encoding="utf-8")
            changed = path.read_bytes()
            assert len(config_edit.backups(path)) == 1
            # Back and Forward: plain GETs, nothing written again.
            tab.evaluate("history.back()")
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/settings" and tab.ready())
            tab.evaluate("history.forward()")
            tab.wait_for(lambda: tab.url() == after and tab.ready())
            assert path.read_bytes() == changed and len(config_edit.backups(path)) == 1
            # Restore: the confirmation page (a GET), then a click.
            tab.click(".outcome a[href='/settings/restore']")
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/settings/restore" and tab.ready())
            assert "on → off" in tab.text() and path.read_bytes() == changed
            tab.click("form.confirm button")
            tab.wait_for(lambda: "/settings?n=" in tab.url() and tab.ready())
            assert "The previous settings are back" in tab.text()
            assert path.read_bytes() == original
            tab.evaluate("location.reload()")
            tab.wait_for(lambda: "/settings?n=" in tab.url() and tab.ready())
            assert path.read_bytes() == original
            # At 390 px neither page scrolls sideways.
            tab.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True},
            )
            for page in ("settings", "settings/restore"):
                tab.goto(f"{ui.origin}/{page}")
                wide = tab.evaluate(
                    "Math.max(window.innerWidth, document.documentElement.scrollWidth) - 390"
                )
                assert wide <= 0, (page, wide)
    finally:
        ui.close()


# --- another server on loopback (#125) ----------------------------------------------------


@dataclass
class Seen:
    """One request the attacker's server got: where it was sent and what came with it."""

    host: str
    path: str
    method: str
    headers: dict[str, str]
    body: str


class Attacker:
    """A throwaway server of another program (or account) on loopback: on its own port,
    on 127.0.0.1 and ::1, it records every request the browser sends it and serves a page
    that makes the browser load more of it, every way a page can."""

    def __init__(self) -> None:
        self.seen: list[Seen] = []
        self._lock = threading.Lock()
        attacker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def _record(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("latin-1") if length else ""
                with attacker._lock:
                    attacker.seen.append(
                        Seen(
                            self.headers.get("Host", ""),
                            self.path,
                            self.command,
                            dict(self.headers.items()),
                            body,
                        )
                    )

            def do_GET(self) -> None:
                self._record()
                if self.path.startswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{attacker.port}/redirected")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                page = self.path.startswith("/page")
                body = attacker.page().encode() if page else b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html" if page else "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = do_GET

        class V6(ThreadingHTTPServer):
            address_family = socket.AF_INET6

            def server_bind(self) -> None:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                super().server_bind()

        self._v4 = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._v4.server_address[1]
        self._v6 = V6(("::1", self.port), Handler)
        for server in (self._v4, self._v6):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def page(self) -> str:
        """Every way a page makes the browser send a request: images, a redirect, fetch
        with credentials, and a form posted at once, to each host it can name."""
        other_name = f"flib-{secrets.token_hex(16)}.localhost"  # a name, not the session's
        hosts = ["127.0.0.1", "localhost", "[::1]", other_name]
        images = "".join(f'<img src="http://{h}:{self.port}/img">' for h in hosts)
        fetches = ",".join(
            f'fetch("http://{h}:{self.port}/fetch", {{credentials: "include", mode: "no-cors"}})'
            for h in hosts
        )
        return (
            "<!doctype html><html><body>"
            f'{images}<img src="/redirect-img">'
            f'<form id="f" method="post" action="http://127.0.0.1:{self.port}/form">'
            '<input name="x" value="1"></form>'
            f"<script>Promise.allSettled([{fetches}]).then(() => "
            'document.getElementById("f").submit());</script>'
            "</body></html>"
        )

    def paths(self) -> set[str]:
        with self._lock:
            return {seen.path for seen in self.seen}

    def close(self) -> None:
        for server in (self._v4, self._v6):
            server.shutdown()
            server.server_close()


def _plain(
    port: int, method: str, path: str, headers: dict[str, str], body: bytes = b""
) -> tuple[int, bytes]:
    """One request from a client outside the browser, to the interface's port."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body or None, headers=headers)
        reply = conn.getresponse()
        return reply.status, reply.read()
    finally:
        conn.close()


def test_another_server_on_loopback_gets_nothing_that_uses_the_session(
    loopback_network: None, downloads: Path, cfg: Config, tmp_path: Path
) -> None:
    """The acceptance test of #125. Browsers keep cookies per host, not per port: a
    server on another loopback port that gets the session's browser to load its pages,
    by a top-level navigation (a link in the interface itself, typed, redirected),
    images, ``fetch`` with credentials and a form post, must get nothing that lets a
    client outside the browser use the session."""
    assert BROWSER is not None
    put(downloads, "power bill.pdf", b"power")
    pipeline.run(cfg, FakeProvider({"power bill.pdf": answer("Finance")}), simulate=False)
    ui = Ui(cfg, poll_seconds=0.05)
    ui.start()
    attacker = Attacker()
    profile = tmp_path / "profile"
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, browser):
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            [session] = browser("Storage.getCookies", {})["cookies"]
            tab.goto(f"{ui.origin}/search")
            csrf = tab.evaluate("document.querySelector('input[name=csrf]').value")
            hidden = {session["value"], csrf, urlsplit(ui.origin).hostname or ""} - {""}
            # 1. A link in the interface itself: a top-level navigation it starts.
            tab.evaluate(f"location.href = 'http://127.0.0.1:{attacker.port}/top'")
            tab.wait_for(lambda: "/top" in attacker.paths())
            # 2. Opened from elsewhere and redirected; then the attacker's own page.
            tab.call("Page.navigate", {"url": f"http://localhost:{attacker.port}/redirect-top"})
            tab.wait_for(lambda: "/redirected" in attacker.paths())
            tab.call("Page.navigate", {"url": f"http://127.0.0.1:{attacker.port}/page"})
            tab.wait_for(lambda: "/form" in attacker.paths(), timeout=20)
            tab.wait_for(lambda: {"/img", "/fetch", "/redirected"} <= attacker.paths())
            # The session itself is untouched: the interface still works in the browser.
            tab.goto(f"{ui.origin}/status")
            assert "Status" in tab.text()
        seen = list(attacker.seen)
        assert {s.path for s in seen} >= {"/top", "/redirect-top", "/page", "/img", "/form"}
        everything = "\n".join(
            f"{s.method} {s.host}{s.path}\n"
            + "\n".join(f"{k}: {v}" for k, v in s.headers.items())
            + f"\n{s.body}"
            for s in seen
        )
        # No cookie at all, no referrer naming the interface, nothing secret anywhere.
        assert [s.path for s in seen if "Cookie" in s.headers] == []
        for secret in hidden:
            assert secret not in everything
        # A client outside the browser, given all of it, can neither read a page nor act.
        cookies = {s.headers["Cookie"] for s in seen if "Cookie" in s.headers} or {""}
        hosts = {f"127.0.0.1:{ui.port}", f"localhost:{ui.port}"} | {
            f"{s.host.rsplit(':', 1)[0]}:{ui.port}" for s in seen
        }
        for cookie in cookies:
            for host in hosts:
                sent = {"Host": host, "Accept": "text/html"} | (
                    {"Cookie": cookie} if cookie else {}
                )
                status, body = _plain(ui.port, "GET", "/search", sent)
                assert status != 200 and b"power bill.pdf" not in body, host
                forged = sent | {
                    "Origin": ui.origin,
                    "Sec-Fetch-Site": "same-origin",
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                status, _ = _plain(ui.port, "POST", "/session/end", forged, b"csrf=" + b"x" * 43)
                assert status != 200, host
        assert ui.wait(0.1) is None and ui.live()  # the session goes on
    finally:
        attacker.close()
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)


def test_only_the_sessions_own_name_gets_the_cookie(
    loopback_network: None, cfg: Config, tmp_path: Path
) -> None:
    """What the name protects, shown: the browser sends the cookie to the session's name
    on any port, and to nothing else. The name is the one host a server on another port
    cannot name; ``Referrer-Policy: same-origin`` keeps it from them (above)."""
    assert BROWSER is not None
    ui = Ui(cfg, poll_seconds=0.05)
    ui.start()
    attacker = Attacker()
    profile = tmp_path / "profile"
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, browser):
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            [session] = browser("Storage.getCookies", {})["cookies"]
            assert session["domain"] == ui.host_name  # host-only, for this name
            assert session["httpOnly"] and session["sameSite"] == "Strict"
            tab.goto(f"http://{ui.host_name}:{attacker.port}/named")
            tab.wait_for(lambda: "/named" in attacker.paths())
        [named] = [s for s in attacker.seen if s.path == "/named"]
        assert named.headers.get("Cookie") == f"{ui.cookie_name}={session['value']}"
    finally:
        attacker.close()
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)


def test_a_second_open_reaches_the_running_session(
    loopback_network: None, cfg: Config, tmp_path: Path
) -> None:
    """``flib ui --open review`` (the menu bar's Review) while the interface runs: the
    opener file sends the browser to ``/open``, which moves on to the page from the
    server's own origin, so the Strict cookie goes with it."""
    assert BROWSER is not None
    ui = Ui(cfg, poll_seconds=0.05)
    ui.start()
    profile = tmp_path / "profile"
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, _):
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            opener = write_opener(Running(os.getpid(), ui.port, ui.host_name), "review")
            tab.call("Page.navigate", {"url": opener.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/review" and tab.ready())
            assert "Review" in tab.text() and "session has ended" not in tab.text()
    finally:
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)


SHOTS = os.environ.get("FLIB_TEST_SCREENSHOTS")
"""A folder to save each page's picture in (pt-BR, light, and a few dark): opt-in, for
looking at the layout with the longer Portuguese words (#126)."""
PAGES_PT = (
    "status",
    "review",
    "search",
    "activity",
    "due",
    "settings",
    "settings/restore",
    "settings/input-remove?index=1",
)
OVERFLOW = """(() => {
  const width = WIDTH;
  const out = [];
  const seen = 'button, a.button, .chip, header nav a, input, select';
  for (const e of document.querySelectorAll(seen)) {
    const r = e.getBoundingClientRect();
    if (r.width === 0) continue;  // folded away in a <details>
    if (r.right > width + 0.5 || r.left < -0.5) out.push(['outside', e.outerHTML.slice(0, 80)]);
    if (e.scrollWidth > e.clientWidth + 1 && e.tagName !== 'INPUT' && e.tagName !== 'SELECT')
      out.push(['clipped', e.outerHTML.slice(0, 80)]);
  }
  return out;
})()"""


WIDEST = """[...document.querySelectorAll('body *')]
  .filter(e => e.getBoundingClientRect().right > WIDTH + 0.5)
  .slice(-4).map(e => e.outerHTML.slice(0, 160))"""
"""What sticks out on the right, for the failure's message."""


def _shot(tab: Any, name: str) -> None:
    if not SHOTS:
        return
    size = tab.call("Page.getLayoutMetrics")["cssContentSize"]
    clip = {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1}
    data = tab.call(
        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True, "clip": clip}
    )["data"]
    folder = Path(SHOTS)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.png").write_bytes(base64.b64decode(data))


def test_every_page_in_portuguese_in_a_real_browser(
    loopback_network: None, downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    """The interface in pt-BR (#126): every page at 390 and 1440 px, with nothing
    scrolling sideways, no button or chip cut off and no menu item out of view; the
    Status page's script in Portuguese; a review decision and a cost cap typed with a
    decimal comma, by real clicks."""
    assert BROWSER is not None
    desktop = downloads.parent / "Desktop"
    desktop.mkdir(exist_ok=True)
    path = tmp_path / "config.yaml"
    path.write_text(
        f"language: pt-BR\ninputs: [{json.dumps(str(downloads))}, {json.dumps(str(desktop))}]\n"
        f"output_root: {json.dumps(str(tmp_path / 'Organizado'))}\n"
        f"database: {json.dumps(str(tmp_path / 'index.db'))}\n"
        "provider: {name: fake, model: x}\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    soon = (date.today() + timedelta(days=3)).isoformat()
    late = (date.today() - timedelta(days=2)).isoformat()
    files = {
        "conta de luz.pdf": answer(
            "Financeiro", kind="bill", issuer="Companhia de Energia", amount="1234.56",
            currency="BRL", due_date=soon, description="Conta de luz",
        ),
        "conta de agua.pdf": answer(
            "Financeiro", kind="bill", issuer="Saneamento", amount="12.34", currency="USD",
            due_date=late, description="Conta de água",
        ),
        "recibo.pdf": answer("Compras", confidence=0.3, description="Recibo"),
        "foto.png": answer("Imagens", confidence=0.3, description="Foto"),
        "notas.txt": answer("Outros", confidence=0.95, description="Anotações"),
    }  # fmt: skip
    for name in files:
        put(downloads, name, name.encode())
    pipeline.run(cfg, FakeProvider(files), simulate=False)
    ui = Ui(cfg, poll_seconds=0.05, config_path=path)
    ui.start()
    profile = tmp_path / "profile"
    try:
        handoff = ui.write_handoff()
        with headless(BROWSER, profile) as (tab, _):
            tab.call("Page.navigate", {"url": handoff.as_uri()})
            tab.wait_for(lambda: tab.url() == f"{ui.origin}/status" and tab.ready())
            tab.wait_for(lambda: "por você" in tab.text())  # the script's words
            assert "Execuções" in tab.text() and "Última execução" in tab.text()
            assert tab.evaluate("document.documentElement.lang") == "pt-BR"
            # A review decision by a click, said in Portuguese.
            tab.goto(f"{ui.origin}/review")
            assert "2 arquivos em revisão" in tab.text()
            tab.click("form[action='/review/file'] button")
            tab.wait_for(lambda: "/review?n=" in tab.url() and tab.ready())
            assert "agora está em" in tab.text()
            # A cost cap typed with a decimal comma, as the page shows numbers.
            tab.goto(f"{ui.origin}/settings")
            tab.evaluate("document.querySelector('input[name=per_run]').value = '3,00'")
            tab.click("#s-costs button[type=submit]")
            tab.wait_for(lambda: "/settings?n=" in tab.url() and tab.ready())
            assert "Salvo." in tab.text()
            assert load_config(path).limits.max_cost_usd_per_run == 3.0
            value = tab.evaluate("document.querySelector('input[name=per_run]').value")
            assert value == "3,0"
            light = [{"name": "prefers-color-scheme", "value": "light"}]
            tab.call("Emulation.setEmulatedMedia", {"features": light})
            for width, height, mobile in ((390, 844, True), (1440, 900, False)):
                metrics = {"width": width, "height": height, "deviceScaleFactor": 1}
                tab.call("Emulation.setDeviceMetricsOverride", {**metrics, "mobile": mobile})
                for page in PAGES_PT:
                    tab.goto(f"{ui.origin}/{page}")
                    if page == "status":
                        tab.wait_for(lambda: "por você" in tab.text())
                    assert tab.evaluate("document.documentElement.lang") == "pt-BR"
                    wide = tab.evaluate(
                        "Math.max(window.innerWidth, document.documentElement.scrollWidth)"
                    )
                    assert wide <= width, (
                        page,
                        width,
                        wide,
                        tab.evaluate(WIDEST.replace("WIDTH", str(width))),
                    )
                    assert tab.evaluate(OVERFLOW.replace("WIDTH", str(width))) == [], (page, width)
                    _shot(tab, f"{width}-{page.split('?')[0].replace('/', '-')}")
            # A few pages in dark mode, for the pictures.
            tab.call(
                "Emulation.setEmulatedMedia",
                {"features": [{"name": "prefers-color-scheme", "value": "dark"}]},
            )
            for page in ("review", "due"):
                tab.goto(f"{ui.origin}/{page}")
                _shot(tab, f"1440-{page}-dark")
            tab.click("form.end button")
            tab.wait_for(lambda: "Esta sessão terminou" in tab.text() and tab.ready())
            _shot(tab, "1440-ended-dark")
        assert ui.wait(10) == "ended"
    finally:
        ui.close()
        shutil.rmtree(profile, ignore_errors=True)
