"""Test-wide guards: no network, no real home folder.

Every test runs with HOME pointed at a temporary directory and with outbound
connections and DNS lookups disabled, and with a throwaway keychain in place of
the user's login Keychain and fakes of launchctl, sips and claude, so code that uses
``Path.home()`` or ``~`` stays in a temporary folder and nothing reaches a real API. A test that
hard-codes an absolute path could still escape; reviewers must reject those.

The plain fixtures at the bottom (``downloads``, ``cfg``) are shared conveniences;
helper functions live in ``tests/_helpers.py``.
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from file_librarian.config import Config
from tests._helpers import FakeClaude, fake_config
from tests.fake_openai import FakeOpenAI


class NetworkBlockedError(RuntimeError):
    pass


# The real functions, saved before any test patches them, for ``loopback_network``.
_REAL_CONNECT = socket.socket.connect
_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_CREATE_CONNECTION = socket.create_connection
LOOPBACK = "127.0.0.1"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def guard(*_args: Any, **_kwargs: Any) -> None:
        raise NetworkBlockedError("tests must not use the network; use FakeProvider")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)


@pytest.fixture
def loopback_network(_no_network: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in for one test: connections to 127.0.0.1 only, for an in-process fake server.

    Names are not resolved (not even ``localhost``) and every other address stays
    blocked, so a test using it still cannot reach a real server.
    """

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host != LOOPBACK:
            raise NetworkBlockedError(f"only {LOOPBACK} is allowed in this test, not {host!r}")
        return _REAL_GETADDRINFO(host, *args, **kwargs)

    def connect(sock: socket.socket, address: Any) -> None:
        if not (isinstance(address, tuple) and address[0] == LOOPBACK):
            raise NetworkBlockedError(f"only {LOOPBACK} is allowed in this test, not {address!r}")
        _REAL_CONNECT(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)


@pytest.fixture(autouse=True)
def no_local_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """``flib init`` finds no Ollama unless a test says otherwise; nothing is probed."""
    monkeypatch.setattr("file_librarian.onboarding.probe_ollama", lambda: None)


def _shim(folder: Path, name: str, script: str) -> Path:
    """An executable ``name`` in ``folder`` that runs ``tests/<script>`` with this Python."""
    target = Path(__file__).with_name(script)
    shim = folder / name
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} {target} "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return shim


@pytest.fixture(autouse=True)
def fake_keychain(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Every test talks to a throwaway keychain, never the user's login Keychain."""
    folder = tmp_path_factory.mktemp("keychain")
    script = _shim(folder, "security", "fake_security.py")
    monkeypatch.setenv("FAKE_KEYCHAIN", str(folder / "items.json"))
    monkeypatch.setattr("file_librarian.keychain.SECURITY", str(script))
    return folder / "items.json"


@pytest.fixture(autouse=True)
def fake_launchd(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test talks to a fake launchctl; no real agent is ever loaded."""
    folder = tmp_path_factory.mktemp("launchd")
    script = _shim(folder, "launchctl", "fake_launchctl.py")
    monkeypatch.setenv("FAKE_LAUNCHD", str(folder / "loaded.json"))
    monkeypatch.setattr("file_librarian.scheduler.LAUNCHCTL", str(script))
    # The dedicated interpreter goes into a throwaway environment that reuses this
    # one's packages, never into the developer's .venv.
    env = folder / "env"
    (env / "bin").mkdir(parents=True)
    (env / "pyvenv.cfg").write_text(
        (Path(sys.prefix) / "pyvenv.cfg").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (env / "lib").symlink_to(Path(sys.prefix) / "lib")
    monkeypatch.setattr("file_librarian.scheduler.ENV_ROOT", env)
    return folder / "loaded.json"


@pytest.fixture(autouse=True)
def fake_sips(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test converts images with a fake sips; returns the log of its calls."""
    folder = tmp_path_factory.mktemp("sips")
    script = _shim(folder, "sips", "fake_sips.py")
    monkeypatch.setenv("FAKE_SIPS_LOG", str(folder / "calls.jsonl"))
    monkeypatch.delenv("FAKE_SIPS_SILENT", raising=False)
    monkeypatch.setattr("file_librarian.extractors.visual.SIPS", str(script))
    return folder / "calls.jsonl"


@pytest.fixture(autouse=True)
def fake_claude(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> FakeClaude:
    """Every test finds a fake ``claude`` first on PATH; the real one never runs."""
    folder = tmp_path_factory.mktemp("claude")
    target = Path(__file__).with_name("fake_claude.py")
    shim = folder / "claude"
    # The provider passes only an allow-list of variables, so the shim names its state itself.
    shim.write_text(
        f'#!/bin/sh\nFAKE_CLAUDE_DIR={folder} exec {sys.executable} {target} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr("file_librarian.providers.claude_code.INSTALL_LOCATIONS", ())
    return FakeClaude(folder)


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FLIB_CONFIG", raising=False)
    for name in ("ANTHROPIC_API_KEY", "FLIB_OPENAI_COMPAT_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return home


@pytest.fixture
def downloads(fake_home: Path) -> Iterator[Path]:
    folder = fake_home / "Downloads"
    folder.mkdir()
    yield folder


@pytest.fixture
def cfg(downloads: Path) -> Config:
    """The fake provider's config, watching ``downloads``."""
    return fake_config(downloads)


@pytest.fixture
def fake_openai(loopback_network: None) -> Iterator[FakeOpenAI]:
    """A running OpenAI-compatible server on 127.0.0.1 (see ``tests/fake_openai.py``)."""
    with FakeOpenAI().running() as server:
        yield server
