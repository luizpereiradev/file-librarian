"""``flib doctor``: the report is complete, and nothing personal survives in it (RF-39).

The main test builds a whole environment out of generated names (input folder,
files, run logs, a scheduled-run traceback, a database, a key in the fake
Keychain) and checks that none of those names, paths, secrets or addresses
appears in the report, for several random seeds and both formats.
"""

from __future__ import annotations

import importlib.util
import json
import random
import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import doctor, keychain, scheduler
from file_librarian.cli import ACCESS_HINT, app
from file_librarian.config import load_config
from file_librarian.db.store import Store, load_migrations
from file_librarian.doctor import Options, Redactor, build_report
from file_librarian.models import RunMode, RunTrigger
from file_librarian.runlog import logs_dir, write_log
from file_librarian.sensitive import SECRET_PATTERNS, find_secrets
from tests._helpers import FakeProtection, put

runner = CliRunner()

_CONSONANTS = "bdfgkmnprstvz"
_VOWELS = "aeiouáéíõü"
_EXTENSIONS = (".pdf", ".docx", ".png", ".zip", ".csv", "", ".tar.gz", ".heic")


def _word(rng: random.Random) -> str:
    """A gibberish word that cannot collide with anything the report says itself."""
    syllables = "".join(rng.choice(_CONSONANTS) + rng.choice(_VOWELS) for _ in range(4))
    return syllables.capitalize() + rng.choice(_CONSONANTS)


def _file_name(rng: random.Random) -> str:
    words = [_word(rng) for _ in range(rng.randint(1, 3))]
    separator = rng.choice([" ", "_", "-", " - "])
    extra = rng.choice(["", " (1)", " 2024", "#3", " & co"])
    return separator.join(words) + extra + rng.choice(_EXTENSIONS)


class _Fake:
    """Everything personal the environment holds, to look for in the report."""

    def __init__(self, seed: int) -> None:
        rng = random.Random(seed)
        self.rng = rng
        self.names = list(dict.fromkeys(_file_name(rng) for _ in range(14)))
        self.folders = [_word(rng) for _ in range(3)]
        self.api_key = "sk-ant-api03-" + "".join(rng.choices("abcdefXYZ0123456789", k=48))
        self.aws_key = "AKIA" + "".join(rng.choices("ABCDEFGHIJ0123456789", k=16))
        self.email = f"{_word(rng).lower()}@{_word(rng).lower()}.example"
        self.url = f"https://{_word(rng).lower()}.example/{_word(rng)}"
        self.unknown_category = _word(rng)
        self.pattern = f"*{_word(rng)}*"
        self.hint = f"letters from {_word(rng)}"

    def words(self) -> set[str]:
        found = {*self.folders, self.unknown_category, self.pattern, self.hint}
        for name in self.names:
            found.add(name)
            found.update(w for w in name.replace("_", " ").replace("-", " ").split() if len(w) > 6)
        return found


def _environment(home: Path, fake: _Fake) -> Path:
    """A configuration, a database, run logs and a schedule, all full of fake names."""
    inputs = home / fake.folders[0] / fake.folders[1]
    inputs.mkdir(parents=True)
    config = home / ".config" / "file-librarian" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"inputs: [{json.dumps(str(inputs))}]\n"
        "provider: {name: fake, model: x}\n"
        f"ignore: ['*.dmg', '.DS_Store', {json.dumps(fake.pattern)}]\n"
        "categories:\n"
        "  - {name: Finance}\n"
        f"  - {{name: Personal, hint: {json.dumps(fake.hint)}}}\n",
        encoding="utf-8",
    )
    cfg = load_config(config)
    cfg.output_root.mkdir(parents=True)
    keychain.write(fake.api_key)
    # A real run: the database and one run log hold the first names.
    for name in fake.names[:4]:
        put(inputs, name, name.encode())
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.stderr
    # Then logs in every format flib writes, with the rest of the names.
    out = cfg.output_root
    a, b, c, d, e, f, g, h, i, j = fake.names[4:14]
    sub = fake.folders[2]
    write_log(
        cfg.database,
        90,
        [
            f"  moved   {a}  ->  Finance/{sub}/{a}",
            f"  dup     {b}  ->  _Duplicates/{b}  (same content as Finance/{sub}/{c})",
            f"  warning Finance/{b}  (could not add the Finder tag: [Errno 1] '{out}/{b}')",
            f"  review  {c}  (model chose unknown category {fake.unknown_category!r})",
            f"  failed  {d}  ([Errno 13] Permission denied: '{inputs}/{d}')",
            f"  sensitive{e}  (sensitive: private key)",
            f"  skip    {f}  (still changing)",
            f"  skip    {i}  (unreadable: EPERM Operation not permitted, on read)",
            f"  skip    {j}  (protected by macOS while the Mac is locked)",
            f"  skipped {g}  (key {fake.aws_key} found; mail {fake.email}; see {fake.url})",
            f"  organized {h}  (password = {fake.api_key})",
            "  (3 item(s) already processed earlier were left alone)",
            "Run 90: 1 organized, 1 to review, 1 sensitive left in place, 1 failed, 2 skipped.",
            ACCESS_HINT,
            "Waiting up to 8 h for the Mac to be unlocked to read 1 file(s) protected while it "
            "is locked; then it runs once more.",
            "The Mac was unlocked; running once more for the files skipped while it was locked.",
        ],
    )
    logs = logs_dir(cfg.database)
    (logs / "schedule.err.log").write_text(
        "Traceback (most recent call last):\n"
        f'  File "{home}/.local/share/uv/tools/file-librarian/lib/python3.12/site-packages/'
        'file_librarian/cli.py", line 10, in run\n'
        f"OSError: [Errno 2] No such file or directory: '{inputs}/{a}'\n"
        f"while reading {inputs}/{sub}/{c} for {home.name}\n",
        encoding="utf-8",
    )
    (logs / "schedule.out.log").write_text(
        f"Log: {logs}/run-000090.log\n  moved   {b}  ->  Personal/{b}\n"
        "[2026-11-20 03:00] Waiting up to 8 h for the Mac to be unlocked (checking every minute).\n"
        f"  skip    {j}  (protected by macOS while the Mac is locked)\n",
        encoding="utf-8",
    )
    scheduler.install("09:15", config, logs)
    return config


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("fmt", ["text", "markdown"])
def test_nothing_personal_survives(fake_home: Path, seed: int, fmt: doctor.Format) -> None:
    fake = _Fake(seed)
    config = _environment(fake_home, fake)
    report = build_report(Options(config_path=config, run_logs=10), fmt)

    leaks = [word for word in fake.words() if word in report]
    assert not leaks, leaks
    for secret in (fake.api_key, fake.api_key[-4:], fake.aws_key, fake.email, fake.url):
        assert secret not in report
    assert str(fake_home) not in report
    assert fake_home.name not in report
    # ... while what helps a maintainer is still there.
    for expected in (
        "<input-1>",
        "<output>",
        "<home>",
        "<file-1>",
        "Finance/",
        "source: keychain",
        "schema version:",
        "run-000090.log",
        "time: 09:15",
        "uses this configuration: yes",
        "<site-packages>/file_librarian/cli.py",
        "<secret: AWS access key>",
        "<email>",
        "'*.dmg'",
        "Run 90: 1 organized",
        "  dup     <file-",
        "->  _Duplicates/<file-",
        "(same content as Finance/",
        "(still changing)",
        "(sensitive: private key)",
        "(unreadable: EPERM Operation not permitted, on read)",
        "(protected by macOS while the Mac is locked)",
        "Waiting up to 8 h for the Mac to be unlocked to read 1 file(s)",
        "[2026-11-20 03:00] Waiting up to 8 h for the Mac to be unlocked (checking every minute).",
        "The Mac was unlocked; running once more",
        "screen locked now: no",
        ACCESS_HINT,
    ):
        assert expected in report, expected


def test_the_same_name_gets_the_same_placeholder() -> None:
    r = Redactor(home=Path("/Users/someone"), keep=["Finance"])
    first = r.path("Finance/Zorbak Trelim.pdf")
    assert first == "Finance/<file-1>.pdf"
    assert r.text("again: /Users/someone/Finance/Zorbak Trelim.pdf, twice") == (
        "again: <home>/Finance/<file-1>.pdf, twice"
    )
    assert r.text("unquoted Velkora.zip") == "unquoted <file-2>.zip"
    assert r.file_name("Zorbak Trelim.pdf") == "<file-1>.pdf"
    assert r.file_name("Other.PDF") == "<file-3>.PDF"
    assert r.file_name("no extension") == "<file-4>"
    assert r.file_name(".hidden") == "<file-5>"
    assert r.path("/Volumes/Disk/x") == "/Volumes/<dir-1>/<file-6>"
    assert r.path("~/Downloads/") == "<home>/Downloads/"
    assert r.path("/Users/someone") == "<home>"


def test_free_text_rules() -> None:
    r = Redactor(home=Path("/Users/someone"), keep=["Finance"])
    assert r.text("confidence 0.62 < 0.70, python 3.12") == "confidence 0.62 < 0.70, python 3.12"
    assert r.text("category 'Finance', not 'Kelmora'") == "category 'Finance', not '<text>'"
    assert r.text('"/Users/someone/x.pdf"') == '"<home>/<file-1>.pdf"'
    assert r.text("someone logged in") == "<user> logged in"
    assert r.text("/usr/lib/python3.11/json/decoder.py") == "<python>/json/decoder.py"
    assert r.text("/a/b/src/file_librarian/cli.py") == "<src>/file_librarian/cli.py"
    assert r.log_line("# flib run 3 finished 2026-01-01T00:00:00+00:00").startswith("# flib run 3")
    assert Redactor(home=Path("/u/ab")).text("ab") == "ab"  # short account names stay


def test_the_report_redacts_every_kind_of_secret_the_scanner_detects() -> None:
    # Built at run time so no real-looking token sits in the source.
    samples = {
        "GitHub token": "ghp_" + "a1" * 18,
        "Slack token": "xoxb-" + "1234567890",
        "Stripe key": "sk_live_" + "b2" * 10,
        "Anthropic API key": "sk-ant-" + "c3" * 10,
    }
    r = Redactor(home=Path("/Users/someone"))
    for kind, value in samples.items():
        assert find_secrets(f"token {value} here") == [kind]
        assert r.secrets_and_contacts(f"token {value} here") == f"token <secret: {kind}> here"
    assert {kind for kind, _ in SECRET_PATTERNS} >= samples.keys()


def test_cli_writes_a_private_report_in_the_logs_folder(fake_home: Path, downloads: Path) -> None:
    put(downloads, "Velomar.pdf")
    config = fake_home / "config.yaml"
    config.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n",
        encoding="utf-8",
    )
    assert runner.invoke(app, ["run", "-c", str(config)]).exit_code == 0
    result = runner.invoke(app, ["doctor", "-c", str(config)])
    assert result.exit_code == 0, result.stderr
    folder = logs_dir(load_config(config).database)
    (report,) = folder.glob("doctor-*.txt")
    assert f"Report written to {report}" in result.stdout
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    text = report.read_text(encoding="utf-8")
    assert "== Configuration ==" in text
    assert "Velomar" not in text
    assert "files by status: organized 1" in text


def test_stdout_markdown_and_output_options(fake_home: Path, downloads: Path) -> None:
    config = fake_home / "config.yaml"
    config.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n",
        encoding="utf-8",
    )
    printed = runner.invoke(app, ["doctor", "-c", str(config), "--stdout", "--format", "markdown"])
    assert printed.exit_code == 0
    assert "## Configuration" in printed.stdout
    assert "```text" in printed.stdout
    assert "exists: no (created by the first run)" in printed.stdout

    target = fake_home / "out" / "report.md"
    written = runner.invoke(app, ["doctor", "-c", str(config), "-o", str(target)])
    assert written.exit_code == 0
    assert target.read_text(encoding="utf-8").startswith("flib doctor report")

    bad = runner.invoke(app, ["doctor", "--format", "html"])
    assert bad.exit_code == 1
    blocked = fake_home / "file"
    blocked.write_text("x", encoding="utf-8")
    failed = runner.invoke(app, ["doctor", "-c", str(config), "-o", str(blocked / "r.txt")])
    assert failed.exit_code == 1
    assert "--stdout" in failed.stderr


def test_only_the_newest_reports_are_kept(tmp_path: Path) -> None:
    for i in range(doctor.KEEP_REPORTS + 3):
        doctor.write_report(tmp_path / f"doctor-20260101-0000{i:02d}.txt", "x")
    kept = sorted(p.name for p in tmp_path.iterdir())
    assert len(kept) == doctor.KEEP_REPORTS
    assert kept[0] == "doctor-20260101-000003.txt"


def test_a_broken_config_still_gives_a_report(fake_home: Path) -> None:
    config = fake_home / "Kervolin" / "config.yaml"
    config.parent.mkdir()
    config.write_text(f"inputs: [{json.dumps(str(fake_home / 'Taluvex'))}]\n", encoding="utf-8")
    report = build_report(Options(config_path=config))
    assert "valid: no" in report
    assert "source: unknown" in report
    assert "Kervolin" not in report
    assert "Taluvex" not in report
    assert doctor.default_report_path(config, "markdown").suffix == ".md"


def test_a_failing_section_shows_its_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> scheduler.Status:
        raise scheduler.ScheduleError(f"cannot read {fake_home}/Kelvaro.plist")

    monkeypatch.setattr(scheduler, "status", broken)
    report = build_report(Options())
    assert "error: ScheduleError: cannot read <home>/Kelvaro.plist" in report
    assert "== Database ==" in report  # the sections after it are still there


def test_key_sources_and_env_file(
    fake_home: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = fake_home / ".env"
    key = "sk-ant-api03-" + "q" * 40
    env_file.write_text(f"ANTHROPIC_API_KEY={key}\n", encoding="utf-8")
    env_file.chmod(0o644)
    config = fake_home / "config.yaml"
    config.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\n"
        f"provider: {{name: anthropic, model: m, env_file: {json.dumps(str(env_file))}}}\n",
        encoding="utf-8",
    )
    report = build_report(Options(config_path=config))
    assert "source: env_file" in report
    assert "env_file: READABLE BY OTHERS" in report
    assert "env_file: <env-file>" in report
    assert key not in report
    assert key[-4:] not in report
    env_file.unlink()
    assert "env_file: missing" in build_report(Options(config_path=config))
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    assert "source: environment" in build_report(Options(config_path=config))


def test_install_kind_and_mcp_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    assert doctor.install_kind() == "development checkout"
    monkeypatch.setattr(scheduler, "is_installed_tool", lambda: True)
    assert doctor.install_kind() == "uv tool"
    monkeypatch.setattr(scheduler, "is_installed_tool", lambda: False)
    monkeypatch.setattr(doctor, "__file__", "/nowhere/file_librarian/doctor.py")
    monkeypatch.setattr(sys, "base_prefix", sys.prefix + "-other")
    assert doctor.install_kind() == "virtual environment"
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    assert doctor.install_kind() == "system Python"

    assert doctor._mcp_extra().startswith("installed")
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    assert doctor._mcp_extra() == "not installed"


def test_last_runs_show_their_cost(
    fake_home: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = fake_home / "config.yaml"
    config.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n",
        encoding="utf-8",
    )
    database = load_config(config).database
    # A database an older flib wrote: no cost column yet, and the doctor never migrates.
    before = [m for m in load_migrations() if m.version < 10]
    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.db.store.load_migrations", lambda: before)
        with Store.open(database) as old:
            run_id = old.start_run(RunMode.RUN, RunTrigger.MANUAL)
            old.conn.execute("UPDATE runs SET finished_at = 'x' WHERE id = ?", (run_id,))

    def run_lines() -> list[str]:
        printed = runner.invoke(app, ["doctor", "-c", str(config), "--stdout"])
        assert printed.exit_code == 0, printed.stderr
        assert "schema version" in printed.stdout and "cost US$" in printed.stdout
        return [line.strip() for line in printed.stdout.splitlines()]

    lines = run_lines()
    assert f"schema version: 9 (this flib knows {len(load_migrations())})" in lines
    assert any(line.startswith("1 ") and line.endswith(" unknown") for line in lines)

    with Store.open(database) as store:
        run_id = store.start_run(RunMode.RUN, RunTrigger.MANUAL)
        store.finish_run(run_id, seen=1, moved=1, review=0, errors=0, cost_usd=0.0123)
        store.start_run(RunMode.RUN, RunTrigger.MANUAL)  # still running
    lines = run_lines()
    assert any(line.startswith("2 ") and line.endswith(" 0.0123") for line in lines)
    assert any(line.startswith("3 ") and line.endswith(" -") for line in lines)
    assert any(line.startswith("1 ") and line.endswith(" unknown") for line in lines)


def test_the_lock_state_and_the_files_skipped_while_locked(
    fake_home: Path, downloads: Path, fake_protection: FakeProtection
) -> None:
    config = fake_home / "config.yaml"
    config.write_text(
        f"inputs: [{json.dumps(str(downloads))}]\nprovider: {{name: fake, model: x}}\n",
        encoding="utf-8",
    )
    cfg = load_config(config)
    assert "screen locked now: no" in build_report(Options(config_path=config))

    with Store.open(cfg.database) as store:
        run_id = store.start_run(RunMode.RUN, RunTrigger.SCHEDULE)
        store.finish_run(run_id, seen=0, moved=0, review=0, errors=0, cost_usd=0.0)
    write_log(
        cfg.database,
        run_id,
        [
            "  skip    photo.jpeg  (protected by macOS while the Mac is locked)",
            "  skip    scan.pdf  (protected by macOS while the Mac is locked)",
        ],
    )
    fake_protection.lock_screen()
    report = build_report(Options(config_path=config))
    assert "screen locked now: yes" in report
    assert "skipped in the last run as protected while locked: 2" in report
    assert "photo.jpeg" not in report and "scan.pdf" not in report
    fake_protection.locked = None
    assert "screen locked now: unknown" in build_report(Options(config_path=config))
