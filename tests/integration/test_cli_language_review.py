"""What the review of #143 asked for (#141): each finding and each surviving mutant
pinned by a test.

- F3: a note around free text (a provider's error) is translated by its frame only.
- F4: a sensitive file's reason is in the user's words; the index keeps the English.
- F5: costs round exactly as the command line always printed them.
- F7: a file name a terminal would act on (ESC, CR, BEL, U+202E) is printed as escapes;
  a ZWJ emoji stays one, two cells wide; ``--json`` and the log keep the real name.
- Mutants: ``--json`` refusals stay English; the yes/no question; the root callback's
  reset; the first notification's language; single-pass substitution; ``due --json``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import cli, due, i18n, keychain, notifier
from file_librarian.config import load_config
from file_librarian.db.store import Store
from file_librarian.i18n import formats, t
from file_librarian.i18n.core import detail, sensitive
from file_librarian.models import Classification
from file_librarian.sensitive import SECRET_PATTERNS
from tests._helpers import answer, organize, put, write_config

runner = CliRunner()
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def flib(*args: str, **kw: Any) -> Any:
    return runner.invoke(cli.app, list(args), **kw)


@pytest.fixture
def pt(tmp_path: Path, downloads: Path) -> Path:
    return write_config(tmp_path, downloads, extra="language: pt-BR\n")


@pytest.fixture
def en(tmp_path: Path, downloads: Path) -> Path:
    return write_config(tmp_path, downloads)


# --- F3: free text is never searched for flib's phrases ----------------------------------

PHRASES = (
    "category Bills; layout not used (no issuer)",
    "category Bills, original name kept",
    "category Screenshots (input rule, no AI)",
    "confidence 0.42 < 0.70",
    "model chose unknown category 'Gardening'",
    "sensitive: name looks like a credential file",
    "same content as another item of this run, which is left for a later run",
    "no longer there (moved or deleted by hand)",
    "daily cap reached",
)
FRAMES = {
    "classification failed: ": "a classificação falhou: ",
    "invalid answer: ": "resposta inválida: ",
    "move failed: ": "a movimentação falhou: ",
    "cannot plan: ": "não foi possível planejar: ",
    "could not move back: ": "não foi possível devolver: ",
}


@pytest.mark.parametrize("phrase", PHRASES)
@pytest.mark.parametrize("frame", list(FRAMES))
def test_a_provider_error_holding_flib_phrases_is_left_as_it_came(frame: str, phrase: str) -> None:
    error = f"HTTP 500 from the provider: {phrase}"
    with i18n.using("pt-BR"):
        shown = detail(frame + error)
    assert shown == FRAMES[frame] + error
    assert i18n.english(shown) == frame + error


def test_a_layout_note_is_translated_only_after_flib_own_reason() -> None:
    with i18n.using("pt-BR"):
        assert detail("category Bills; layout not used (no issuer)") == (
            "categoria Bills; layout não usado (no issuer)"
        )
        free = "the provider said nothing; layout not used (x)"
        assert detail(free) == free


# --- F4: why a file is sensitive ----------------------------------------------------------


@pytest.mark.parametrize(
    ("recorded", "said"),
    [
        ("name looks like a credential file", "o nome parece de um arquivo de credenciais"),
        (
            "name looks like a credential file: client secret",
            "o nome parece de um arquivo de credenciais: client secret",
        ),
        (
            "content: holds a file named like a credential file",
            "conteúdo: contém um arquivo com nome de arquivo de credenciais",
        ),
        (
            "content: looks like private key, password or secret in JSON",
            "conteúdo: parece ter chave privada, senha ou segredo em JSON",
        ),
    ],
)
def test_a_sensitive_reason_in_portuguese_and_its_english(recorded: str, said: str) -> None:
    with i18n.using("pt-BR"):
        shown = sensitive(recorded)
        assert shown == said
        assert detail(f"sensitive: {recorded}") == f"sensível: {said}"
    assert i18n.english(shown) == recorded


@pytest.mark.parametrize("kind", [kind for kind, _ in SECRET_PATTERNS])
def test_every_kind_of_secret_has_words(kind: str) -> None:
    with i18n.using("pt-BR"):
        shown = sensitive(f"content: looks like {kind}")
    assert isinstance(shown, i18n.Msg)
    assert shown.text("en") == f"content: looks like {kind}"
    assert kind not in shown.text("pt-BR") or kind.startswith(("JSON", "GitHub"))


def test_run_and_status_say_why_in_portuguese_and_the_index_keeps_english(
    pt: Path, downloads: Path
) -> None:
    put(downloads, "id_rsa", b"not really a key")
    shown = flib("run", "-c", str(pt)).stdout
    assert "  sensível   id_rsa  (sensível: o nome parece de um arquivo de credenciais)" in shown
    listed = flib("status", "--sensitive", "-c", str(pt)).stdout
    assert "(o nome parece de um arquivo de credenciais)" in listed
    with Store.open(load_config(pt).database) as store:
        [(_, reason)] = store.sensitive_files()
    assert reason == "name looks like a credential file"


# --- F5: costs round as they always did ---------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [0.0, 5e-05, 0.00005, 0.00015, 0.00025, 0.00285, 0.0029, 0.12345, 0.99995, 1.00005, 12.3456789,
     0.0001, 0.00049999, 3.0, 0.005, 0.015, 2.675],
)  # fmt: skip
def test_a_cost_rounds_as_the_old_expression(value: float) -> None:
    assert formats.usd(value) == f"US$ {value:.4f}"
    assert formats.usd(value, 2) == f"US$ {value:.2f}"
    assert formats.usd(value, lang="pt-BR") == f"US$ {value:.4f}".replace(".", ",")
    line = t("term.cost", cost=formats.later(formats.usd, value, 4))
    assert line.text("en") == f"Estimated provider cost: US$ {value:.4f}"


# --- F7: names a terminal would act on ----------------------------------------------------

HOSTILE = "bill\x1b[2J\rpaid\x07‮fdp.txt"


def test_hostile_names_are_printed_as_escapes(pt: Path, downloads: Path) -> None:
    put(downloads, HOSTILE, b"hostile")
    simulated = flib("simulate", "-c", str(pt)).stdout
    for raw in ("\x1b", "\r", "\x07", "‮"):
        assert raw not in simulated
    assert "bill\\x1b[2J\\x0dpaid\\x07\\u202efdp.txt" in simulated
    ran = flib("run", "-c", str(pt))
    assert "\x1b" not in ran.stdout and "\\x1b" in ran.stdout
    log = Path(next(x for x in ran.stdout.splitlines() if x.startswith("Log: "))[5:])
    assert HOSTILE.encode() in log.read_bytes()  # the log keeps the real name
    found = flib("search", "bill", "-c", str(pt))
    assert "\x1b" not in found.stdout and "\\x1b" in found.stdout
    data = json.loads(flib("search", "bill", "--json", "-c", str(pt)).stdout)
    assert data[0]["original_name"] == HOSTILE


def test_a_hostile_issuer_in_due_and_sum(pt: Path, downloads: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(due, "clock", lambda: NOON)
    bill: dict[str, tuple[bytes, Classification | str]] = {
        "power.txt": (
            b"power",
            answer(
                "Financeiro", kind="bill", issuer="Power\x1b]0;x\x07Co", amount="10.00",
                currency="BRL", due_date="2026-11-22",
            ),
        )
    }  # fmt: skip
    organize(downloads, load_config(pt), bill)
    for args in (("due",), ("search", "--sum", "--by", "issuer")):
        out = flib(*args, "-c", str(pt)).stdout
        assert "\x1b" not in out and "\x07" not in out
        assert "Power\\x1b]0;x\\x07Co" in out


def test_a_zwj_emoji_is_one_glyph_two_cells_wide() -> None:
    coder = "\U0001f469‍\U0001f4bb"
    assert formats.printable(coder) == coder
    assert formats.width(coder) == 2
    assert formats.ljust(coder, 4) == coder + "  "
    assert formats.printable("a​b﻿c") == "abc"  # invisible, dropped
    assert formats.printable("a\nb\tc") == "a\\x0ab\\x09c"
    assert formats.printable("a\nb", lines=True) == "a\nb"


# --- the surviving mutants ----------------------------------------------------------------


def test_a_json_search_refusal_stays_english(pt: Path) -> None:
    result = flib("search", "--since", "2026-13-01", "--json", "-c", str(pt))
    assert result.exit_code == 1
    assert result.stderr == "error: --since '2026-13-01' is not a valid date\n"


@pytest.mark.parametrize(
    ("typed", "default", "said"),
    [("nao\n", True, False), ("NÃO\n", True, False), ("S\n", False, True),
     ("SIM\n", False, True), ("y\n", False, True), ("yes\n", False, True),
     ("n\n", True, False), ("no\n", True, False), ("\n", True, True), ("\n", False, False),
     ("talvez\nsim\n", False, True)],
)  # fmt: skip
def test_the_portuguese_yes_no_takes_every_spelling(typed: str, default: bool, said: bool) -> None:
    with i18n.using("pt-BR"), runner.isolation(input=typed) as (_, err, *_rest):
        assert cli._confirm("Seguir?", default=default) is said
        refused = err.getvalue().decode()
    assert ("responda s ou n" in refused) is typed.startswith("talvez")


def test_the_portuguese_yes_no_aborts_on_end_of_input() -> None:
    import typer

    with i18n.using("pt-BR"), runner.isolation(input=""), pytest.raises(typer.Abort):
        cli._confirm("Seguir?", default=True)


def test_every_invocation_starts_in_english(tmp_path: Path) -> None:
    """The root callback resets the language: what one command set never reaches the
    next, even with no fixture around (here the language is set to pt-BR on purpose)."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("language: pt-BR\nbogus: 1\n", encoding="utf-8")
    some = tmp_path / "some.txt"
    some.write_text("x", encoding="utf-8")
    i18n.use("pt-BR")
    result = flib("inspect", str(some), "-c", str(broken))
    assert result.exit_code == 1
    assert result.stderr.startswith("error: invalid configuration:")


def test_the_first_notification_follows_the_configuration(
    pt: Path, fake_notifier_tools: Path
) -> None:
    keychain.write("sk-ant-" + "review-" + "k" * 20)
    result = flib("schedule", "-c", str(pt), "--no-check")
    assert result.exit_code == 0, result.output
    log = fake_notifier_tools
    posts = [json.loads(x) for x in log.read_text().splitlines()]
    opened = [call for call in posts if call[0] == "open"]
    assert [call[-2:] for call in opened] == [
        [t(notifier.INTRO_TEXT).text("pt-BR"), "file-librarian"]
    ]
    assert "As notificações vêm do" in result.stdout


def test_substitution_is_single_pass() -> None:
    hostile = "{source}{x}{0.__class__}"
    with i18n.using("pt-BR"):
        line = t("term.row.moved", source=hostile, destination="{destination}")
    assert str(line) == "  movido     {source}{x}{0.__class__}  ->  {destination}"
    assert line.text("en") == "  moved   {source}{x}{0.__class__}  ->  {destination}"


def test_due_json_is_the_same_bytes_in_both_languages(
    pt: Path, en: Path, downloads: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(due, "clock", lambda: NOON)
    bill: dict[str, tuple[bytes, Classification | str]] = {
        "power.txt": (
            b"power",
            answer("Finance", kind="bill", issuer="Northwind", amount="1234.56",
                   currency="BRL", due_date="2026-11-18"),
        )
    }  # fmt: skip
    organize(downloads, load_config(en), bill)
    english = flib("due", "--json", "--all", "-c", str(en)).stdout
    portuguese = flib("due", "--json", "--all", "-c", str(pt)).stdout
    assert portuguese == english
    data = json.loads(english)
    assert data["items"][0]["relative"] == "2 days ago"
    assert data["items"][0]["amount"] == "1234.56"
