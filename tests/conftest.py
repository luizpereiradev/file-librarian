"""Test-wide guards: no network, no real home folder.

Every test runs with HOME pointed at a temporary directory and with outbound
connections and DNS lookups disabled, and with a throwaway keychain in place of
the user's login Keychain, so code that uses ``Path.home()`` or
``~`` stays in a temporary folder and nothing reaches a real API. A test that
hard-codes an absolute path could still escape; reviewers must reject those.
"""

from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


class NetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def guard(*_args: Any, **_kwargs: Any) -> None:
        raise NetworkBlockedError("tests must not use the network; use FakeProvider")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)


@pytest.fixture(autouse=True)
def fake_keychain(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Every test talks to a throwaway keychain, never the user's login Keychain."""
    folder = tmp_path_factory.mktemp("keychain")
    script = folder / "security"
    script.write_text(
        f'#!/bin/sh\nexec {sys.executable} {Path(__file__).with_name("fake_security.py")} "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("FAKE_KEYCHAIN", str(folder / "items.json"))
    monkeypatch.setattr("file_librarian.keychain.SECURITY", str(script))
    return folder / "items.json"


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FLIB_CONFIG", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return home


@pytest.fixture
def downloads(fake_home: Path) -> Iterator[Path]:
    folder = fake_home / "Downloads"
    folder.mkdir()
    yield folder
