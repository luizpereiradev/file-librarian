"""Guards for the test suite's own safety fixtures (``tests/conftest.py``).

The autouse fixtures there (temporary HOME, no network or DNS, fake Keychain,
launchctl, sips, osascript and ``claude``, no Ollama probe) patch through the same
function-scoped ``monkeypatch`` a test receives. ``monkeypatch.undo()`` reverts
every patch on it, theirs included, so for the rest of that test it could reach
the real home folder or the network. Temporary patches go in
``with monkeypatch.context() as m:`` instead, which reverts only its own.
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import pytest

from file_librarian import keychain, scheduler, status
from tests._helpers import REPO_ROOT
from tests.conftest import NetworkBlockedError

TESTS = REPO_ROOT / "tests"
UNDO = re.compile(r"\bmonkeypatch\.undo\(")


def test_no_test_undoes_the_safety_fixtures() -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}"
        for path in sorted(TESTS.rglob("*.py"))
        if path != Path(__file__)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if UNDO.search(line)
    ]
    assert offenders == [], (
        "monkeypatch.undo() also reverts the autouse safety fixtures in tests/conftest.py "
        "(temporary HOME, no network, fake Keychain, launchctl, sips and claude), so the "
        "rest of the test could reach the real home folder or the network. Put temporary "
        "patches in `with monkeypatch.context() as m:` instead. Found at: " + ", ".join(offenders)
    )


def test_the_safety_fixtures_outlive_a_monkeypatch_context(
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
    fake_keychain: Path,
    fake_launchd: Path,
    fake_osascript: Path,
) -> None:
    with monkeypatch.context() as m:
        m.setenv("HOME", "/nowhere")
        m.setattr(socket, "getaddrinfo", lambda *_a, **_k: [])
        assert os.environ["HOME"] == "/nowhere"

    # Only the context's own patches were reverted; the guards are still in place.
    assert os.environ["HOME"] == str(fake_home) and Path.home() == fake_home
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(NetworkBlockedError), socket.socket() as sock:
        sock.connect(("192.0.2.1", 443))
    assert os.environ["FAKE_KEYCHAIN"] == str(fake_keychain)
    assert Path(keychain.SECURITY).parent == fake_keychain.parent
    assert os.environ["FAKE_LAUNCHD"] == str(fake_launchd)
    assert Path(scheduler.LAUNCHCTL).parent == fake_launchd.parent
    assert Path(status.OSASCRIPT).parent == fake_osascript.parent
