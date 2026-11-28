"""Test-wide guards: no network, no real home folder.

Every test runs with HOME pointed at a temporary directory and with outbound
connections and DNS lookups disabled, and with a throwaway keychain in place of
the user's login Keychain and fakes of launchctl, sips, osascript, defaults, the OCR helper,
the tools that build and launch the notifier app, the ``open`` that ``flib ui`` uses to open a
browser, and claude (and of the screen lock and
Data Protection classes), so code that uses
``Path.home()`` or ``~`` stays in a temporary folder and nothing reaches a real API. A test that
hard-codes an absolute path could still escape; reviewers must reject those.

The plain fixtures at the bottom (``downloads``, ``cfg``) are shared conveniences;
helper functions live in ``tests/_helpers.py``.
"""

from __future__ import annotations

import errno
import fcntl
import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from file_librarian import i18n
from file_librarian.config import Config
from file_librarian.extractors import ocr
from file_librarian.fsops import CrossVolumeError
from tests._helpers import FakeClaude, FakeProtection, fake_config
from tests.fake_openai import FakeOpenAI

# --- parallel runs (pytest-xdist) ----------------------------------------------------------
#
# ``-n auto`` (the default, see pyproject.toml) runs the tests in worker processes, each
# with its own temporary folders (tmp_path_factory is per worker) and its own copy of
# every guard below. The tests that use real macOS tools run one at a time.

COVERAGE_CONFIG = "COVERAGE_PROCESS_CONFIG"
"""Set for the workers when ``coverage run`` runs pytest with workers: coverage's own
start-up hook (its ``.pth`` file) then measures each worker from its first line."""
REAL_TOOLS = ("volumes", "ocr", "notifier", "menu", "browser")
"""The markers of the opt-in tests that use real system tools and resources."""


def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, "workerinput"):
        # A worker, measured since it started. Not the processes its tests start
        # (flib's own subprocesses, as in a run without workers).
        os.environ.pop(COVERAGE_CONFIG, None)
        return
    if config.getoption("dist", "no") == "no":
        return
    try:
        from coverage import Coverage
    except ImportError:
        return
    cov = Coverage.current()
    if cov is not None:
        # The configuration ``coverage run`` started with (parallel: each worker
        # writes its own data file; ``coverage combine`` joins them).
        os.environ[COVERAGE_CONFIG] = cov.config.serialize()


@pytest.fixture(autouse=True)
def _real_tools_one_at_a_time(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """The opt-in tests of real system tools (disk images, Vision, Chrome...) never run
    at the same time: under xdist, each holds a lock file in the run's temporary folder,
    which every worker shares. (Not ``--dist loadgroup``: with it, a worker that dies
    leaves the whole run waiting forever, pytest-xdist 3.8.)"""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None or not any(request.node.get_closest_marker(n) for n in REAL_TOOLS):
        yield
        return
    lock = tmp_path_factory.getbasetemp().parent / "real-tools.lock"
    with lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)


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
    """Opt-in for one test: connections to 127.0.0.1 (and ::1) only, for an in-process
    fake server.

    Names are not resolved (not even ``localhost``) and every other address stays
    blocked, so a test using it still cannot reach a real server.
    """

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host != LOOPBACK:
            raise NetworkBlockedError(f"only {LOOPBACK} is allowed in this test, not {host!r}")
        return _REAL_GETADDRINFO(host, *args, **kwargs)

    def connect(sock: socket.socket, address: Any) -> None:
        # ``::1`` too: ``flib ui`` holds it on its port, and a test connects to it.
        if not (isinstance(address, tuple) and address[0] in (LOOPBACK, "::1")):
            raise NetworkBlockedError(f"only {LOOPBACK} is allowed in this test, not {address!r}")
        _REAL_CONNECT(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", _REAL_CREATE_CONNECTION)


@pytest.fixture(autouse=True)
def _english_by_default() -> Iterator[None]:
    """Every test starts in English, and a command that switched the language
    (``flib`` with ``language: pt-BR``, in-process) never leaks it into the next test
    of the worker (#141)."""
    with i18n.using(i18n.DEFAULT):
        yield


@pytest.fixture(autouse=True)
def no_local_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """``flib init`` finds no Ollama unless a test says otherwise; nothing is probed."""
    monkeypatch.setattr("file_librarian.onboarding.probe_ollama", lambda: None)


@pytest.fixture(autouse=True)
def no_lock_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Automatic runs give way at once to a held lock unless a test says otherwise; no
    test sleeps for minutes."""
    monkeypatch.setattr("file_librarian.cli.LOCK_WAIT_SECONDS", 0.0)


TESTS_DIR = Path(__file__).parent
SHEBANG = f"#!{sys.executable} -IS"
"""The fakes' interpreter line. The kernel starts this Python straight away (no
``/bin/sh`` in between), isolated from ``PYTHON*`` variables and the user's site
(``-I``) and without ``site`` (``-S``): the fakes need only the standard library, and
every call of every fake pays for the start-up. macOS splits the line's arguments."""
if any(c.isspace() for c in sys.executable) or len(SHEBANG) > 500:
    raise RuntimeError(f"the fakes cannot be started by {sys.executable!r} (a space, or too long)")


def _shim(
    folder: Path,
    name: str,
    script: str,
    *,
    call: str = "main(sys.argv[1:])",
    env: dict[str, str] | None = None,
) -> Path:
    """An executable ``name`` in ``folder`` that runs ``tests/<script>``'s ``call`` with
    this Python, after setting ``env``. The fake is imported, so its compiled bytecode
    is reused from one call to the next."""
    lines = [SHEBANG, "import sys"]
    if env:
        lines += ["import os", *(f"os.environ[{k!r}] = {v!r}" for k, v in env.items())]
    module = Path(script).stem
    lines += [
        f"sys.path.insert(0, {str(TESTS_DIR)!r})",
        f"from {module} import main",
        f"sys.exit({call})",
    ]
    shim = folder / name
    shim.write_text("\n".join(lines) + "\n", encoding="utf-8")
    shim.chmod(0o755)
    return shim


NOTIFIER_TOOLS = {
    "osacompile": "OSACOMPILE",
    "codesign": "CODESIGN",
    "sips": "SIPS",
    "iconutil": "ICONUTIL",
    "open": "OPEN",
    "lsregister": "LSREGISTER",
}
FAKE_TOOLS: dict[tuple[str, str], tuple[str, str]] = {
    ("keychain", "security"): ("fake_security.py", "main(sys.argv[1:])"),
    ("launchd", "launchctl"): ("fake_launchctl.py", "main(sys.argv[1:])"),
    ("sips", "sips"): ("fake_sips.py", "main(sys.argv[1:])"),
    ("ocr", "osascript"): ("fake_ocr.py", "main(sys.argv[1:])"),
    ("osascript", "osascript"): ("fake_osascript.py", "main(sys.argv[1:])"),
    ("open", "open"): ("fake_open.py", "main(sys.argv[1:])"),
    ("defaults", "defaults"): ("fake_defaults.py", "main(sys.argv[1:])"),
    **{
        ("notifier", tool): ("fake_notifier_tools.py", f"main({tool!r}, sys.argv[1:])")
        for tool in NOTIFIER_TOOLS
    },
}
"""(folder, name) -> (fake, call): the shims of ``fake_tools``."""


@pytest.fixture(scope="session")
def fake_tools(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The shims of the fake tools, written once per process (each xdist worker has its
    own). They hold no state: every test points them at its own folder through
    ``FAKE_*`` variables. Written once, not per test, because macOS checks a new
    executable the first time it runs it, which costs about 40 ms."""
    root = tmp_path_factory.mktemp("tools")
    for (folder, name), (script, call) in FAKE_TOOLS.items():
        (root / folder).mkdir(exist_ok=True)
        _shim(root / folder, name, script, call=call)
    return root


@pytest.fixture(autouse=True)
def fake_keychain(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """Every test talks to a throwaway keychain, never the user's login Keychain."""
    folder = tmp_path_factory.mktemp("keychain")
    script = fake_tools / "keychain" / "security"
    monkeypatch.setenv("FAKE_KEYCHAIN", str(folder / "items.json"))
    monkeypatch.setattr("file_librarian.keychain.SECURITY", str(script))
    return folder / "items.json"


@pytest.fixture(autouse=True)
def fake_launchd(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """Every test talks to a fake launchctl; no real agent is ever loaded."""
    folder = tmp_path_factory.mktemp("launchd")
    script = fake_tools / "launchd" / "launchctl"
    monkeypatch.setenv("FAKE_LAUNCHD", str(folder / "loaded.json"))
    monkeypatch.delenv("FAKE_LAUNCHD_STOPPED", raising=False)
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
def fake_sips(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """Every test converts images with a fake sips; returns the log of its calls."""
    folder = tmp_path_factory.mktemp("sips")
    script = fake_tools / "sips" / "sips"
    monkeypatch.setenv("FAKE_SIPS_LOG", str(folder / "calls.jsonl"))
    monkeypatch.delenv("FAKE_SIPS_SILENT", raising=False)
    monkeypatch.setattr("file_librarian.extractors.visual.SIPS", str(script))
    return folder / "calls.jsonl"


@pytest.fixture(autouse=True)
def fake_ocr(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """Text recognition goes to a fake helper (tests/fake_ocr.py); returns the log of its calls.

    It finds only the text ``make_scan`` hides in a synthetic file.
    """
    folder = tmp_path_factory.mktemp("ocr")
    script = fake_tools / "ocr" / "osascript"
    monkeypatch.setenv("FAKE_OCR_LOG", str(folder / "calls.jsonl"))
    monkeypatch.delenv("FAKE_OCR_MODE", raising=False)
    monkeypatch.setattr("file_librarian.extractors.ocr.OSASCRIPT", str(script))
    ocr.availability.cache_clear()
    return folder / "calls.jsonl"


@pytest.fixture(autouse=True)
def fake_osascript(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """Every notification goes to a fake osascript; returns the log of its calls."""
    folder = tmp_path_factory.mktemp("osascript")
    script = fake_tools / "osascript" / "osascript"
    monkeypatch.setenv("FAKE_OSASCRIPT_LOG", str(folder / "calls.jsonl"))
    monkeypatch.delenv("FAKE_OSASCRIPT_FAIL", raising=False)
    monkeypatch.setattr("file_librarian.status.OSASCRIPT", str(script))
    return folder / "calls.jsonl"


@pytest.fixture(autouse=True)
def fake_notifier_tools(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """The notifier app is built and launched with fakes (tests/fake_notifier_tools.py);
    returns the log of their calls. It goes into the throwaway environment of
    ``fake_launchd``, never into the developer's."""
    folder = tmp_path_factory.mktemp("notifier")
    for tool, constant in NOTIFIER_TOOLS.items():
        shim = fake_tools / "notifier" / tool
        monkeypatch.setattr(f"file_librarian.notifier.{constant}", str(shim))
    monkeypatch.setenv("FAKE_NOTIFIER_LOG", str(folder / "calls.jsonl"))
    monkeypatch.delenv("FAKE_NOTIFIER_FAIL", raising=False)
    return folder / "calls.jsonl"


@pytest.fixture(autouse=True)
def menu_nudges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The menu bar item's nudge (a distributed notification) is recorded, never posted;
    returns the names posted."""
    posted: list[str] = []

    def record(name: str) -> bool:
        posted.append(name)
        return True

    monkeypatch.setattr("file_librarian.menubar_state.POST", record)
    return posted


@pytest.fixture(autouse=True)
def fake_browser(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """``flib ui`` opens its handoff file with a fake ``open``; no test opens a real browser.

    Returns the log of its calls (tests/fake_open.py)."""
    folder = tmp_path_factory.mktemp("open")
    script = fake_tools / "open" / "open"
    monkeypatch.setenv("FAKE_OPEN_LOG", str(folder / "calls.jsonl"))
    monkeypatch.delenv("FAKE_OPEN_FAIL", raising=False)
    monkeypatch.setattr("file_librarian.ui.server.OPEN", str(script))
    # Handoff files go in a temporary folder, not the account's real one.
    temp = folder / "T"
    temp.mkdir(mode=0o700)
    monkeypatch.setattr("file_librarian.ui.server.handoff_root", lambda: temp)
    # The network is closed, so no name resolves here: flib ui is told this Mac's
    # resolver gives loopback for .localhost (as current macOS does); names_resolve
    # itself has its own tests.
    monkeypatch.setattr("file_librarian.ui.server.names_resolve", lambda: True)
    return folder / "calls.jsonl"


@pytest.fixture(autouse=True)
def fake_defaults(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch, fake_tools: Path
) -> Path:
    """``flib init`` reads where screenshots go from a fake ``defaults``; returns its call log.

    Unset, screenshots go to the Desktop; a test sets ``FAKE_SCREENSHOT_LOCATION``.
    """
    folder = tmp_path_factory.mktemp("defaults")
    script = fake_tools / "defaults" / "defaults"
    monkeypatch.setenv("FAKE_DEFAULTS_LOG", str(folder / "calls.jsonl"))
    monkeypatch.delenv("FAKE_SCREENSHOT_LOCATION", raising=False)
    monkeypatch.setattr("file_librarian.onboarding.DEFAULTS", str(script))
    return folder / "calls.jsonl"


@pytest.fixture(autouse=True)
def fake_protection(monkeypatch: pytest.MonkeyPatch) -> FakeProtection:
    """No test asks the real session whether the Mac is locked, nor reads a real
    file's Data Protection class: every file is class C and the screen unlocked
    unless the test changes the returned fake."""
    fake = FakeProtection()
    monkeypatch.setattr("file_librarian.protection.protection_class", fake.protection_class)
    monkeypatch.setattr("file_librarian.protection.screen_locked", fake.screen_locked)
    monkeypatch.setattr("file_librarian.protection.IOREG", "/nonexistent/ioreg")
    return fake


@pytest.fixture(autouse=True)
def fake_claude(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> FakeClaude:
    """Every test finds a fake ``claude`` first on PATH; the real one never runs."""
    folder = tmp_path_factory.mktemp("claude")
    # The provider passes only an allow-list of variables, so the shim names its state itself.
    _shim(folder, "claude", "fake_claude.py", env={"FAKE_CLAUDE_DIR": str(folder)})
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr("file_librarian.providers.claude_code.INSTALL_LOCATIONS", ())
    return FakeClaude(folder)


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FLIB_CONFIG", raising=False)
    for name in (
        "ANTHROPIC_API_KEY",
        "FLIB_OPENAI_COMPAT_API_KEY",
        "OPENAI_API_KEY",
        "FLIB_EMBEDDINGS_API_KEY",
    ):
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


@pytest.fixture
def cross_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every move by a run or an undo behaves as if it crossed volumes.

    Copy keys lose their random suffix, so tests can name the staging paths.
    """
    monkeypatch.setattr("file_librarian.executor.new_copy_key", lambda op_id: f"{op_id}-k")
    monkeypatch.setattr("file_librarian.undo.new_copy_key", lambda op_id: f"{op_id}-k")

    def exdev(src: Path, dst: Path) -> None:
        raise CrossVolumeError(errno.EXDEV, "cross-device link", str(dst))

    monkeypatch.setattr("file_librarian.executor.move_no_replace", exdev)
    monkeypatch.setattr("file_librarian.undo.move_no_replace", exdev)
