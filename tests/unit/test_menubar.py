"""The menu bar item (#116): its applet's source, its bundle, its LaunchAgent and the
contract of the state file it reads. Every tool is a fake (the notifier's, and
tests/fake_launchctl.py); the opt-in tests at the bottom compile the real applet and
run its parser with osascript, without ever showing it."""

from __future__ import annotations

import json
import os
import plistlib
import random
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from file_librarian import menubar, notifier, poses, scheduler
from file_librarian.menubar import MenuError

# --- AppleScript literals ----------------------------------------------------------------


def evaluate(expression: str) -> str:
    """What AppleScript makes of an expression ``literal`` writes: string literals
    (``\\\\`` and ``\\"`` escapes; AppleScript would also read ``\\n`` and the like, which
    ``literal`` must therefore never leave in) and ``(character id N)``, joined by ``&``."""
    out = []
    i = 0
    while i < len(expression):
        if expression.startswith('"', i):
            i += 1
            chunk = []
            while expression[i] != '"':
                if expression[i] == "\\":
                    assert expression[i + 1] in '\\"', "an escape AppleScript would interpret"
                    chunk.append(expression[i + 1])
                    i += 2
                    continue
                assert " " <= expression[i] <= "~"
                chunk.append(expression[i])
                i += 1
            out.append("".join(chunk))
            i += 1
        else:
            match = re.compile(r"\(character id (\d+)\)").match(expression, i)
            assert match is not None, expression[i:]
            out.append(chr(int(match.group(1))))
            i = match.end()
        if i < len(expression):
            assert expression.startswith(" & ", i), expression[i:]
            i += 3
    return "".join(out)


HOSTILE = [
    "",
    "/Users/a b/Library/Application Support/file-librarian/menu",
    '/Volumes/x/"quoted"/x',
    "/Volumes/x/back\\slash\\",
    '/Volumes/x/end"\n& do shell script "touch /tmp/pwned" & "',
    "/Volumes/x/\\n not a newline",
    "/Users/jo\u00e3o/caf\u00e9/\u65e5\u672c/\U0001f408",
    "/Volumes/x/tab\there\rcr\x00nul\u202emirror",
    "/Volumes/x/end\\",
    '"',
]


@pytest.mark.parametrize("text", HOSTILE)
def test_literal_evaluates_to_exactly_the_text(text: str) -> None:
    expression = menubar.literal(text)
    assert evaluate(expression) == text
    assert all(" " <= char <= "~" for char in expression)  # nothing for osacompile to decode


def test_literal_fuzzed() -> None:
    rng = random.Random(116)
    alphabet = ' "\\/&()abc\n\t\r\x00\x7f\u00e9\u202e\u2028\U0001f408'
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 24)))
        assert evaluate(menubar.literal(text)) == text


def _handler_body(source: str, name: str) -> str:
    start = source.index(f"on {name}(")
    return source[start : source.index(f"end {name}", start)]


@pytest.mark.parametrize("path", HOSTILE[1:])
def test_the_script_fixes_the_paths_as_literals(path: str) -> None:
    program, config, state = Path(path) / "flib-python", Path(path) / "c.yaml", Path(path)
    source = menubar.script(program, config, state)
    assert evaluate(_returned(source, "stateDir")) == str(state)
    assert evaluate(_returned(source, "flibProgram")) == str(program)
    arguments = _handler_body(source, "actionArguments")
    config_expr = arguments.split('{"--config", ', 1)[1].rstrip().removesuffix("}")
    assert evaluate(config_expr) == str(config)


def _returned(source: str, handler: str) -> str:
    body = _handler_body(source, handler)
    return body.split("return ", 1)[1].strip()


def test_every_placeholder_is_filled() -> None:
    source = menubar.script(Path("/p"), Path("/c"), Path("/s"))
    assert re.search(r"@[A-Z_]+@", source) is None
    assert re.findall(r"@([A-Z_]+)@", menubar.template())  # the template has some


def test_the_actions_are_the_only_things_a_click_runs() -> None:
    source = menubar.script(Path("/env/bin/flib-python"), Path("/c.yaml"), Path("/s"))
    # One NSTask, in launchAction, for the fixed program; no shell anywhere.
    assert source.count("NSTask") == 1
    assert "do shell script" not in source and "/bin/sh" not in source
    launch = _handler_body(source, "launchAction")
    assert "setExecutableURL:(ca's NSURL's fileURLWithPath:program)" in launch
    assert "set program to my flibProgram()" in launch
    # The program is checked to be there and executable before anything is started.
    assert launch.index("isExecutableFileAtPath:program") < launch.index("NSTask")
    # Each menu item passes a key; the words come from the table inside the script.
    for action in menubar.ACTIONS:
        handler = source.split(f"on {action.selector}sender", 1)[1].split("end", 1)[0]
        assert f'my launchAction("{action.key}")' in handler
    table = _handler_body(source, "actionArguments")
    branches = re.findall(
        r'if \(k\'s isEqualToString:"(\w+)"\) as boolean then\n\t+set chosen to (\{.*?\})',
        table,
    )
    assert branches == [
        (a.key, "{" + ", ".join(f'"{w}"' for w in a.arguments) + "}") for a in menubar.ACTIONS
    ]
    # Exact: NSString isEqualToString:, never AppleScript's "is" (which ignores width).
    assert "set k to ca's NSString's stringWithString:key" in table
    assert ' is "' not in table.replace('is "busy"', "")
    # No other handler takes words that reach NSTask: launchAction takes a key, and the
    # only list it runs is what actionArguments returns.
    assert "task's setArguments:args" in launch
    assert "set args to my actionArguments(key)" in launch
    assert "launchFlib" not in source and "flibArguments" not in source
    # Output to /dev/null; the working folder is /, never inherited; a fixed environment.
    for stream in ("Input", "Output", "Error"):
        assert f"task's setStandard{stream}:nothing" in launch
    assert "setCurrentDirectoryURL:(ca's NSURL's fileURLWithPath:\"/\")" in launch
    assert "task's setEnvironment:(my childEnvironment())" in launch
    environment = _handler_body(source, "childEnvironment")
    assert f'env\'s setObject:"{scheduler.SAFE_PATH}" forKey:"PATH"' in environment
    assert '{"HOME", "TMPDIR", "USER", "LOGNAME", "LANG"}' in environment
    assert "DYLD" not in environment and "PYTHON" not in environment
    assert '({"-I", "-m", "file_librarian"} & chosen)' in table
    # Organize now is never greyed out: it is what heals a state a killed run left.
    assert "setEnabled" not in _handler_body(source, "show")
    # Nothing read from the state file reaches launchAction.
    assert "objectForKey" not in launch


def test_the_actions_open_pages_flib_ui_knows() -> None:
    from file_librarian.ui import server

    pages = {a.arguments[2] for a in menubar.ACTIONS if a.arguments[0] == "ui"}
    assert pages <= set(server.PAGES)
    assert ("menu", "--organize") in {a.arguments for a in menubar.ACTIONS}


def test_an_action_with_other_words_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = menubar.Action("openStatus", "Open", ("ui", '"; do shell script "x'))
    monkeypatch.setattr(menubar, "ACTIONS", (bad, *menubar.ACTIONS[1:]))
    with pytest.raises(MenuError, match="unexpected words"):
        menubar.script(Path("/p"), Path("/c"), Path("/s"))


def test_the_parser_constants_come_from_python() -> None:
    source = menubar.script(Path("/p"), Path("/c"), Path("/s"))
    assert f"property maxStateBytes : {menubar.MAX_STATE_BYTES}" in source
    assert f"property maxText : {menubar.MAX_TEXT}" in source
    assert f"property maxCount : {menubar.MAX_COUNT}" in source
    assert f"property stateVersion : {menubar.STATE_VERSION}" in source
    known = "{" + ", ".join(f'"{p}"' for p in poses.Pose) + "}"
    assert f"return {known}" in _handler_body(source, "knownPoses")
    keys = "{" + ", ".join(f'"{k}"' for k in sorted(menubar.STATE_KEYS)) + "}"
    assert f"return {keys}" in _handler_body(source, "knownKeys")
    # Keys and poses are compared exactly (NSArray containsObject:), never with
    # AppleScript's "contains", which ignores case and width.
    read = _handler_body(source, "readState")
    assert read.count("containsObject:") == 2 and " contains " not in read
    # The whole file must be the plain form, and each key once.
    assert menubar.literal(r"\A" + menubar.FORM + r"\z") in source
    assert menubar.literal(menubar.KEY_FORM) in source
    # Mutable state in globals: a stay-open applet would try to save changed properties.
    assert re.search(r"^property (statusItem|tasks|lastStamp)", source, re.M) is None


# --- the state file's contract ----------------------------------------------------------

GOOD = {
    "version": 1,
    "pose": "attention",
    "count": 3,
    "line": "3 files to review",
    "next": "Next run Thu 12:30",
    "review": 3,
    "due": 0,
    "running": False,
}

CORPUS: list[tuple[str, bytes | None, dict[str, Any]]] = [
    ("good", json.dumps(GOOD).encode(), GOOD),
    ("missing", None, {}),
    ("empty", b"", {}),
    ("not json", b"pose=busy", {}),
    ("a list", b'["busy"]', {}),
    ("a string", b'"busy"', {}),
    ("unknown key", json.dumps({**GOOD, "program": "/bin/sh"}).encode(), {}),
    ("key in other case", json.dumps({"version": 1, "Pose": "busy"}).encode(), {}),
    ("no version", json.dumps({"pose": "busy"}).encode(), {}),
    ("other version", json.dumps({**GOOD, "version": 2}).encode(), {}),
    ("version as text", json.dumps({**GOOD, "version": "1"}).encode(), {}),
    ("too big", json.dumps({**GOOD, "line": "x" * 70, "next": " " * 5000}).encode(), {}),
    ("NaN", b'{"version": 1, "count": NaN}', {}),
    ("unknown pose", json.dumps({"version": 1, "pose": "../../evil"}).encode(), {"pose": None}),
    ("pose in capitals", json.dumps({"version": 1, "pose": "BUSY"}).encode(), {"pose": None}),
    ("pose not text", json.dumps({"version": 1, "pose": 3}).encode(), {"pose": None}),
    (
        "counts of the wrong kind",
        json.dumps({"version": 1, "count": "3", "review": True, "due": 2, "running": 1}).encode(),
        {"due": 2},
    ),
    ("a float is not plain", json.dumps({"version": 1, "due": 2.0}).encode(), {}),
    ("negative", json.dumps({"version": 1, "count": -1}).encode(), {}),
    ("above the cap", json.dumps({"version": 1, "review": 10000, "due": 9999}).encode(), {}),
    ("the cap", json.dumps({"version": 1, "due": 9999}).encode(), {"due": 9999}),
    ("fraction", json.dumps({"version": 1, "count": 2.5, "due": 9999}).encode(), {}),
    ("leading zero", b'{"version":1,"count":07}', {}),
    # The reviewer's cases (#132): each side used to read them differently.
    ("key given twice", b'{"version":1,"count":3,"count":5}', {}),
    ("key given twice, bad last", b'{"version":1,"count":3,"count":"x"}', {}),
    ("trailing comma", b'{"version":1,"count":9,}', {}),
    ("lone surrogate", b'{"version":1,"line":"a\\ud800b"}', {}),
    ("surrogate pair", b'{"version":1,"line":"a\\ud83d\\udc08"}', {"line": "a\U0001f408"}),
    ("30-digit decimal", b'{"version":1,"count":3.00000000000000000000000000001}', {}),
    ("deep nesting", b'{"version":1,"line":' + b"[" * 1500 + b"]" * 1500 + b"}", {}),
    ("deep at the top", b"[" * 2000 + b"]" * 2000, {}),
    ("full-width pose", '{"version":1,"pose":"\uff42usy"}'.encode(), {}),
    ("full-width pose, escaped", b'{"version":1,"pose":"\\uff42usy"}', {}),
    ("pose with a space", b'{"version":1,"pose":"busy "}', {}),
    ("byte order mark", b'\xef\xbb\xbf{"version":1,"count":7}', {}),
    ("UTF-16", '{"version":1,"count":8}'.encode("utf-16"), {}),
    ("raw UTF-8", '{"version":1,"line":"caf\u00e9"}'.encode(), {}),
    ("an escaped quote", b'{"version":1,"line":"a\\"b"}', {}),
    ("an escaped slash", b'{"version":1,"line":"a\\/b"}', {}),
    ("upper-case escape", b'{"version":1,"line":"a\\u00E9"}', {}),
    ("nested object", b'{"version":1,"line":{"a":1}}', {}),
    ("trailing garbage", b'{"version":1,"count":9} x', {}),
    ("two spaces", b'{"version":1,  "count":9}', {}),
    ("compact, with a newline", b'{"version":1,"running":true}\n', {"running": True}),
    ("exactly 4096 bytes", b'{"version":1,"next":"' + b"a" * (4096 - 23) + b'"}', {}),
    ("running", json.dumps({"version": 1, "running": True}).encode(), {"running": True}),
    (
        "control and bidi characters",
        json.dumps({"version": 1, "line": "a\nb", "next": "x\u202ey"}).encode(),
        {},
    ),
    ("a bidi override alone", b'{"version":1,"line":"a\\u202eb","due":1}', {"due": 1}),
    ("a zero-width joiner alone", b'{"version":1,"next":"a\\u200db"}', {}),
    ("line separator", json.dumps({"version": 1, "line": "a\u2028b"}).encode(), {}),
    ("empty line", json.dumps({"version": 1, "line": "", "next": ""}).encode(), {}),
    (
        "longest text",
        json.dumps({"version": 1, "line": "y" * 80, "next": "\u00e9" * 80}).encode(),
        {"line": "y" * 80, "next": "\u00e9" * 80},
    ),
    ("one too long", json.dumps({"version": 1, "line": "y" * 81}).encode(), {}),
    (
        "astral characters count twice",
        json.dumps({"version": 1, "line": "\U0001f408" * 41}).encode(),
        {},
    ),
    (
        "unicode",
        json.dumps({"version": 1, "line": "3 arquivos para revis\u00e3o \U0001f408"}).encode(),
        {"line": "3 arquivos para revis\u00e3o \U0001f408"},
    ),
    ("null values", json.dumps({"version": 1, "line": None, "count": None}).encode(), {}),
]


def expected(case: dict[str, Any]) -> dict[str, Any]:
    clean = menubar.defaults()
    clean.update({k: v for k, v in case.items() if v is not None})
    return clean


@pytest.mark.parametrize(("name", "raw", "case"), CORPUS, ids=[c[0] for c in CORPUS])
def test_sanitize_keeps_only_what_the_contract_allows(
    name: str, raw: bytes | None, case: dict[str, Any]
) -> None:
    assert menubar.sanitize(raw) == expected(case)


def test_defaults_say_nothing_is_known() -> None:
    clean = menubar.sanitize(None)
    assert clean["pose"] == "trouble" and clean["line"] == menubar.DEFAULT_LINE
    assert set(clean) == menubar.STATE_KEYS


# --- the bundle ---------------------------------------------------------------------------


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def interpreter() -> Path:
    return scheduler.prepare_interpreter()


def test_build_makes_a_signed_stay_open_app(
    interpreter: Path, tmp_path: Path, fake_notifier_tools: Path
) -> None:
    config = tmp_path / "config.yaml"
    built = menubar.build(config)
    assert built == menubar.app_path() == interpreter.parent / "file-librarian-menu.app"
    contents = built / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "io.github.luizpereiradev.file-librarian.menu"
    assert info["CFBundleName"] == "file-librarian" and info["LSUIElement"] is True
    # No scripting dictionary, no document or URL type: nothing invites events or files.
    assert info["NSAppleScriptEnabled"] is False
    assert "CFBundleDocumentTypes" not in info and "CFBundleURLTypes" not in info
    assert not any(key.endswith("UsageDescription") for key in info)
    assert "CFBundleIconName" not in info
    assert not (contents / "Resources" / "Assets.car").exists()
    for pose in poses.Pose:
        assert (contents / "Resources" / f"{pose}.svg").read_bytes() == (
            poses.icon(pose).read_bytes()
        )
    script = (contents / "Resources" / "Scripts" / "main.scpt").read_text(encoding="utf-8")
    assert script == menubar.script(interpreter, config, menubar.folder())
    compile_call = next(c for c in calls(fake_notifier_tools) if c[0] == "osacompile")
    assert compile_call[1] == "-s"  # stay open
    tools = [c[0] for c in calls(fake_notifier_tools)]
    assert tools[-3:] == ["codesign", "codesign", "lsregister"]
    assert calls(fake_notifier_tools)[-1] == ["lsregister", "-f", str(built)]
    assert menubar.problem(config) is None
    assert [p.name for p in built.parent.iterdir() if p.name.startswith(".menu-")] == []


def test_a_change_of_configuration_icons_or_version_means_a_rebuild(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.yaml"
    menubar.build(config)
    assert menubar.problem(config) is None
    assert menubar.problem(tmp_path / "other.yaml") == "the menu bar app is out of date"
    monkeypatch.setattr(menubar, "APP_VERSION", "test")
    assert menubar.problem(config) == "the menu bar app is out of date"
    menubar.build(config)
    assert menubar.problem(config) is None
    monkeypatch.setattr(menubar, "icons", lambda: {"busy.svg": b"<svg/>"})
    assert menubar.problem(config) == "the menu bar app is out of date"
    menubar.build(config)
    assert menubar.problem(config) is None


def test_a_damaged_app_is_reported(interpreter: Path, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    assert menubar.problem(config) == "the menu bar app is not built"
    menubar.build(config)
    menubar.executable().unlink()
    assert menubar.problem(config) == "the menu bar app is incomplete"


@pytest.mark.parametrize("tool", ["osacompile", "codesign"])
def test_a_failed_build_leaves_no_app(
    interpreter: Path, tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", tool)
    with pytest.raises(MenuError, match=f"{tool} failed"):
        menubar.build(tmp_path / "c.yaml")
    assert not menubar.app_path().exists()
    assert [p for p in menubar.app_path().parent.iterdir() if p.name.startswith(".")] == []


def test_an_osacompile_that_makes_nothing_is_caught(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OSACOMPILE", shutil.which("true") or "/usr/bin/true")
    with pytest.raises(MenuError, match="osacompile made no app bundle"):
        menubar.build(tmp_path / "c.yaml")


def test_without_osacompile_there_is_no_build(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OSACOMPILE", "/nonexistent/osacompile")
    assert menubar.missing_tools() == ["/nonexistent/osacompile"]
    with pytest.raises(MenuError, match="/nonexistent/osacompile not found"):
        menubar.build(tmp_path / "c.yaml")


def test_the_program_must_be_flibs_own_interpreter(interpreter: Path, tmp_path: Path) -> None:
    assert menubar.validate_program(interpreter) == interpreter
    with pytest.raises(MenuError, match="not flib's own interpreter"):
        menubar.validate_program(tmp_path / "python")
    with pytest.raises(MenuError, match="not flib's own interpreter"):
        menubar.validate_program(Path("bin/flib-python"))
    interpreter.chmod(0o644)
    with pytest.raises(MenuError, match="not an executable file"):
        menubar.validate_program(interpreter)
    interpreter.unlink()
    interpreter.symlink_to(shutil.which("python3") or "/usr/bin/python3")
    with pytest.raises(MenuError, match="not an executable file"):
        menubar.validate_program(interpreter)
    interpreter.unlink()
    with pytest.raises(MenuError, match="interpreter is missing"):
        menubar.validate_program(interpreter)


def test_private_files_are_written_whole_and_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "menu" / "state.json"
    menubar.write_private(path, b"one")
    menubar.write_private(path, b"two")
    assert path.read_bytes() == b"two"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert sorted(p.name for p in path.parent.iterdir()) == ["state.json"]


def test_a_failed_private_write_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"

    def fail(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        menubar.write_private(path, b"x")
    assert list(tmp_path.iterdir()) == []


def test_the_agent_starts_at_login_and_stays_off_after_quit(tmp_path: Path) -> None:
    data = menubar.build_plist(tmp_path / "config.yaml")
    assert data["Label"] == scheduler.MENU_LABEL == "io.github.luizpereiradev.file-librarian.menu"
    assert data["Program"] == str(menubar.executable())
    assert data["ProgramArguments"] == [str(menubar.executable())]
    assert data["RunAtLoad"] is True
    assert "KeepAlive" not in data  # no respawn, not even when the app is gone
    assert data["LimitLoadToSessionType"] == "Aqua"
    assert data["ProcessType"] == "Interactive"
    # Its output (and that of what it starts) is nobody's to read.
    assert data["StandardOutPath"] == data["StandardErrorPath"] == os.devnull
    assert data["EnvironmentVariables"] == {
        "FLIB_CONFIG": str(tmp_path / "config.yaml"),
        "PATH": scheduler.SAFE_PATH,
    }


def test_read_seen_checks_what_the_applet_wrote(tmp_path: Path) -> None:
    seen = menubar.folder() / menubar.SEEN_NAME
    assert menubar.read_seen() is None
    menubar.write_private(seen, json.dumps({**GOOD, "pid": 42}).encode())
    assert menubar.read_seen() == {**GOOD, "pid": 42}
    menubar.write_private(seen, json.dumps({**GOOD, "pid": True}).encode())
    assert menubar.read_seen() is None
    menubar.write_private(seen, b"[1]")
    assert menubar.read_seen() is None
    menubar.write_private(seen, json.dumps({**GOOD, "line": "a\nb", "pid": 7}).encode())
    assert menubar.read_seen() == {**GOOD, "line": menubar.DEFAULT_LINE, "pid": 7}


# --- for real (opt-in) ------------------------------------------------------------------

REAL_OSACOMPILE = "/usr/bin/osacompile"
REAL_OSASCRIPT = "/usr/bin/osascript"
REAL_CODESIGN = "/usr/bin/codesign"
real = pytest.mark.skipif(
    os.environ.get("FLIB_TEST_MENU") != "1" or not Path(REAL_OSACOMPILE).exists(),
    reason="compiling the real menu bar applet is opt-in: FLIB_TEST_MENU=1 on macOS",
)
CALL = """on run argv
	set s to load script (POSIX file (item 1 of argv))
	set out to {}
	repeat with i from 2 to count of argv
		set end of out to s's stateJSON(item i of argv)
	end repeat
	set AppleScript's text item delimiters to linefeed
	return out as text
end run
"""
VALUES = """on run argv
	set s to load script (POSIX file (item 1 of argv))
	set out to {}
	repeat with t in ({s's stateDir(), s's flibProgram()} & s's actionArguments("openStatus"))
		set AppleScript's text item delimiters to ","
		set end of out to ((id of (contents of t)) as list) as text
	end repeat
	set AppleScript's text item delimiters to linefeed
	return out as text
end run
"""
"""Each value as the code points AppleScript holds, so nothing depends on how osascript
prints a line break or a NUL."""


def _osascript(source: str, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        [REAL_OSASCRIPT, "-", *args],
        input=source,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.rstrip("\n")


@pytest.mark.menu
@real
def test_the_real_applet_parses_the_state_as_the_contract_says(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OSACOMPILE", REAL_OSACOMPILE)
    monkeypatch.setattr(notifier, "CODESIGN", REAL_CODESIGN)
    # Never registered with Launch Services, never launched: nothing shows.
    monkeypatch.setattr(notifier, "LSREGISTER", "/nonexistent/lsregister")
    built = menubar.build(tmp_path / "config.yaml")
    verify = subprocess.run(  # noqa: S603
        [REAL_CODESIGN, "--verify", "--deep", "--strict", str(built)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    compiled = built / "Contents" / "Resources" / "Scripts" / "main.scpt"
    files = []
    for index, (_, raw, _) in enumerate(CORPUS):
        path = tmp_path / "states" / f"{index}.json"
        path.parent.mkdir(exist_ok=True)
        if raw is not None:
            path.write_bytes(raw)
        files.append(str(path))
    link = tmp_path / "states" / "link.json"
    link.symlink_to(tmp_path / "states" / "0.json")  # the good one, through a link
    files.append(str(link))
    out = _osascript(CALL, str(compiled), *files).split("\n")
    assert len(out) == len(CORPUS) + 1
    for (name, raw, _), line in zip(CORPUS, out, strict=False):
        assert json.loads(line) == menubar.sanitize(raw), name
    assert json.loads(out[-1]) == menubar.defaults()  # a link is not followed


@pytest.mark.menu
@real
@pytest.mark.parametrize("path", HOSTILE[1:])
def test_the_real_applet_gets_the_fixed_paths_exactly(path: str, tmp_path: Path) -> None:
    source = menubar.script(Path(path) / "flib-python", Path(path) / "c.yaml", Path(path))
    (tmp_path / "menu.applescript").write_text(source, encoding="utf-8")
    compiled = tmp_path / "menu.scpt"
    result = subprocess.run(  # noqa: S603
        [REAL_OSACOMPILE, "-o", str(compiled), str(tmp_path / "menu.applescript")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = _osascript(VALUES, str(compiled)).split("\n")
    expected_values = [path, f"{path}/flib-python", "-I", "-m", "file_librarian", "ui",
                       "--open", "status", "--config", f"{path}/c.yaml"]  # fmt: skip
    assert values == [",".join(str(ord(c)) for c in value) for value in expected_values]


KEYS = """on run argv
	set s to load script (POSIX file (item 1 of argv))
	set out to {}
	repeat with k in {"organize", "ORGANIZE", "organize ", "openReview", "openreview", "rm", ""}
		set a to s's actionArguments(contents of k)
		if a is missing value then
			set end of out to "-"
		else
			set AppleScript's text item delimiters to " "
			set end of out to (items 4 thru 5 of a) as text
		end if
	end repeat
	set AppleScript's text item delimiters to linefeed
	return out as text
end run
"""


@pytest.mark.menu
@real
def test_the_real_applet_runs_only_the_exact_keys_of_its_table(tmp_path: Path) -> None:
    source = menubar.script(Path("/env/bin/flib-python"), Path("/c.yaml"), tmp_path)
    (tmp_path / "menu.applescript").write_text(source, encoding="utf-8")
    compiled = tmp_path / "menu.scpt"
    subprocess.run(  # noqa: S603
        [REAL_OSACOMPILE, "-o", str(compiled), str(tmp_path / "menu.applescript")], check=True
    )
    assert _osascript(KEYS, str(compiled)).split("\n") == [
        "menu --organize", "-", "-", "ui --open", "-", "-", "-",
    ]  # fmt: skip


# --- after the second review of #132 -------------------------------------------------------


RUNNING = b'{"version":1,"pose":"busy","line":"Organizing\\u2026","running":true}'


def test_a_stale_running_is_shown_as_a_run_that_did_not_finish() -> None:
    fresh = menubar.sanitize(RUNNING, age=60)
    assert fresh["running"] is True and fresh["pose"] == "busy"
    old = menubar.sanitize(RUNNING, age=menubar.STALE_SECONDS + 1)
    assert old["running"] is False and old["pose"] == "trouble"
    assert old["line"] == menubar.STALE_LINE
    due = RUNNING.replace(b'"busy"', b'"due"')
    assert menubar.sanitize(due, age=menubar.STALE_SECONDS + 1)["pose"] == "due"


def test_the_nudge_is_rate_limited_in_the_applet() -> None:
    source = menubar.script(Path("/p"), Path("/c"), Path("/s"))
    handler = source.split("on stateChanged:aNotification", 1)[1].split("end stateChanged:", 1)[0]
    assert "if waited >= nudgeSeconds then" in handler
    assert 'performSelector:"deferredRefresh:"' in handler  # put off, not dropped
    assert "if not pendingRefresh then" in handler
    assert "property nudgeSeconds : 1" in source
    assert f"property staleSeconds : {menubar.STALE_SECONDS}" in source
    assert menubar.literal(menubar.STALE_LINE) in _handler_body(source, "readState")


def test_read_small_refuses_a_link(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_bytes(b"{}")
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert menubar.read_small(real) == b"{}"
    assert menubar.read_small(link) is None


def test_read_small_never_waits_on_a_fifo(tmp_path: Path) -> None:
    import threading

    fifo = tmp_path / "state.json"
    os.mkfifo(fifo)
    result: list[bytes | None] = []
    reader = threading.Thread(target=lambda: result.append(menubar.read_small(fifo)))
    reader.start()
    reader.join(3)
    blocked = reader.is_alive()
    if blocked:  # let it go: a writer opening the FIFO unblocks the reader
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)
        reader.join(3)
    assert not blocked and result == [None]


def test_read_small_refuses_what_is_not_a_regular_file() -> None:
    assert menubar.read_small(Path("/dev/null")) is None  # readable, empty, a device


def test_read_small_has_a_byte_limit(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x" * 10)
    assert menubar.read_small(path, limit=10) == b"x" * 10
    assert menubar.read_small(path, limit=9) is None


def test_a_folder_of_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "menu"
    assert menubar.private_folder(folder) == folder
    monkeypatch.setattr(os, "getuid", lambda: folder.stat().st_uid + 1)
    with pytest.raises(OSError, match="not a folder of this user's"):
        menubar.private_folder(folder)


def test_a_file_where_the_folder_should_be_is_refused(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_text("x")
    with pytest.raises(OSError):
        menubar.private_folder(file)


@pytest.mark.menu
@real
def test_the_real_applet_drops_a_stale_running(
    interpreter: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OSACOMPILE", REAL_OSACOMPILE)
    monkeypatch.setattr(notifier, "CODESIGN", REAL_CODESIGN)
    monkeypatch.setattr(notifier, "LSREGISTER", "/nonexistent/lsregister")
    built = menubar.build(tmp_path / "config.yaml")
    compiled = built / "Contents" / "Resources" / "Scripts" / "main.scpt"
    fresh, old = tmp_path / "fresh.json", tmp_path / "old.json"
    for path in (fresh, old):
        path.write_bytes(RUNNING)
    long_ago = time.time() - menubar.STALE_SECONDS - 60
    os.utime(old, (long_ago, long_ago))
    out = _osascript(CALL, str(compiled), str(fresh), str(old)).split("\n")
    assert json.loads(out[0]) == menubar.sanitize(RUNNING, age=1)
    assert json.loads(out[1]) == menubar.sanitize(RUNNING, age=menubar.STALE_SECONDS + 60)
    assert json.loads(out[1])["line"] == menubar.STALE_LINE
