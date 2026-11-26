"""A minimal Chrome DevTools Protocol client, standard library only, for the browser
smoke test of ``flib ui`` (``tests/integration/test_ui_browser.py``).

It drives an installed Chrome or Chromium, run headless with a throwaway profile,
over its loopback DevTools socket: one tab, its page events, script evaluation and
real mouse clicks. Nothing here reaches the network beyond 127.0.0.1.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


def find_browser() -> Path | None:
    """``FLIB_TEST_BROWSER_PATH``, else the first Chrome or Chromium installed."""
    named = os.environ.get("FLIB_TEST_BROWSER_PATH")
    for candidate in ([named] if named else []) + list(BROWSERS):
        if Path(candidate).is_file():
            return Path(candidate)
    found = shutil.which("chromium")
    return None if found is None else Path(found)


class _Socket:
    """A WebSocket client: text frames, client-masked, as DevTools speaks it."""

    def __init__(self, url: str) -> None:
        hostport, path = url.removeprefix("ws://").split("/", 1)
        host, port = hostport.split(":")
        self._sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self._sock.sendall(
            (
                f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        data = b""
        while b"\r\n\r\n" not in data:
            data += self._sock.recv(4096)
        head, self._rest = data.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode("latin-1"))
        self._lock = threading.Lock()

    def _read(self, n: int) -> bytes:
        while len(self._rest) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise EOFError
            self._rest += chunk
        out, self._rest = self._rest[:n], self._rest[n:]
        return out

    def recv(self) -> str:
        message = b""
        while True:
            first, second = self._read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            data = self._read(length)
            opcode = first & 0x0F
            if opcode == 8:
                raise EOFError
            if opcode in (9, 10):  # ping, pong
                continue
            message += data
            if first & 0x80:
                return message.decode()

    def send(self, text: str) -> None:
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self._lock:
            self._sock.sendall(head + mask + masked)

    def close(self) -> None:
        self._sock.close()


class Tab:
    """One page of the browser: navigate, evaluate, click, wait."""

    def __init__(self, ws: _Socket, domains: tuple[str, ...] = ("Page", "Runtime")) -> None:
        self._ws = ws
        self._next = 0
        self._replies: dict[int, dict[str, Any]] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []
        self._cv = threading.Condition()
        threading.Thread(target=self._loop, name="cdp-reader", daemon=True).start()
        for domain in domains:
            self.call(f"{domain}.enable")

    def _loop(self) -> None:
        try:
            while True:
                message = json.loads(self._ws.recv())
                with self._cv:
                    if "id" in message:
                        self._replies[message["id"]] = message
                    else:
                        self.events.append((message["method"], message.get("params", {})))
                    self._cv.notify_all()
        except (EOFError, OSError):
            pass

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._cv:
            self._next += 1
            ident = self._next
        self._ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        with self._cv:
            if not self._cv.wait_for(lambda: ident in self._replies, timeout=30):
                raise TimeoutError(method)
            reply = self._replies.pop(ident)
        if "error" in reply:
            raise RuntimeError(f"{method}: {reply['error']}")
        result: dict[str, Any] = reply.get("result", {})
        return result

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"].get("text", "script failed"))
        return result["result"].get("value")

    def wait_for(self, condition: Callable[[], bool], timeout: float = 15.0) -> None:
        """Until ``condition`` holds. A script run while the tab is between two documents
        fails ("Inspected target navigated or closed"): that is "not yet", not an error."""

        def holds() -> bool:
            try:
                return condition()
            except RuntimeError:
                return False

        deadline = time.monotonic() + timeout
        while not holds():
            if time.monotonic() > deadline:
                raise TimeoutError("the page never got there")
            time.sleep(0.05)

    def url(self) -> str:
        return str(self.evaluate("location.href"))

    def text(self) -> str:
        return str(self.evaluate("document.body ? document.body.innerText : ''"))

    def ready(self) -> bool:
        try:
            return bool(self.evaluate("document.readyState") == "complete")
        except RuntimeError:  # between two documents
            return False

    def goto(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})
        self.wait_for(lambda: self.url().startswith(url.split("#", 1)[0]) and self.ready())

    def click(self, selector: str) -> None:
        """A real click: the mouse pressed and released at the element's centre."""
        box = self.evaluate(
            f"(() => {{ const e = document.querySelector({json.dumps(selector)});"
            "e.scrollIntoView({block: 'center'}); const r = e.getBoundingClientRect();"
            "return [r.x + r.width / 2, r.y + r.height / 2]; })()"
        )
        x, y = box
        for kind in ("mousePressed", "mouseReleased"):
            self.call(
                "Input.dispatchMouseEvent",
                {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
            )


@contextmanager
def headless(binary: Path, profile: Path) -> Iterator[tuple[Tab, Callable[[str, Any], Any]]]:
    """A headless browser with a throwaway profile; its one tab, and a caller for
    browser-wide commands. The browser is ended whatever happens."""
    process = subprocess.Popen(  # noqa: S603 - a fixed browser binary, our own flags
        [
            str(binary),
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--metrics-recording-only",
            "--password-store=basic",
            "--use-mock-keychain",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sockets: list[_Socket] = []
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not port_file.exists() or not port_file.read_text().strip():
            if time.monotonic() > deadline or process.poll() is not None:
                raise RuntimeError("the browser did not start")
            time.sleep(0.05)
        port = int(port_file.read_text().split()[0])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as reply:
            pages = [t for t in json.load(reply) if t.get("type") == "page"]
        page = _Socket(pages[0]["webSocketDebuggerUrl"])
        sockets.append(page)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10) as reply:
            browser_socket = _Socket(json.load(reply)["webSocketDebuggerUrl"])
        sockets.append(browser_socket)
        browser = Tab(browser_socket, domains=())
        yield Tab(page), browser.call
    finally:
        for ws in sockets:
            ws.close()
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
