"""The catalogue, its rendering and the formats (#126).

What keeps the two languages whole: every id in both, with the same
placeholders; every id used; no user-visible English left outside the catalogue
in the interface's modules, its packaged pages and script, or the menu bar's
words. Then the rules: plurals, the fallback, the escaping (a hostile translation
or value never adds markup), dates, money and the reading of a typed amount, the
menu bar in Portuguese, and the server speaking the running configuration's
language from the next request on.
"""

from __future__ import annotations

import ast
import html
import json
import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from file_librarian import i18n, menubar, menubar_state, poses
from file_librarian import status as status_mod
from file_librarian.config import Config
from file_librarian.due import DueCounts
from file_librarian.i18n import LANGUAGES, Plural, formats, placeholders, t
from file_librarian.i18n.messages import CATALOGUE
from file_librarian.i18n.messages import status as status_messages
from file_librarian.ui import markup
from file_librarian.ui.server import Ui, handoff_text
from tests._helpers import UiClient
from tests.integration.test_menubar_flow import _status

SRC = Path(__file__).resolve().parents[2] / "src" / "file_librarian"
UI = SRC / "ui"


def forms(entry: str | Plural) -> tuple[str, ...]:
    return (entry,) if isinstance(entry, str) else entry.forms()


def fields_of(entry: str | Plural) -> set[str]:
    found = set().union(*(placeholders(form) for form in forms(entry)))
    if isinstance(entry, Plural):
        found.add(entry.by)
    return found


# --- the catalogue is whole --------------------------------------------------------------


def test_every_id_is_in_every_language() -> None:
    ids = {lang: set(CATALOGUE[lang]) for lang in LANGUAGES}
    for lang in LANGUAGES:
        assert ids[lang] == ids["en"], lang
    assert len(ids["en"]) > 500


def test_every_id_has_the_same_placeholders_and_kind_in_every_language() -> None:
    for msg_id, english in CATALOGUE["en"].items():
        for lang in LANGUAGES:
            other = CATALOGUE[lang][msg_id]
            assert type(other) is type(english), (lang, msg_id)
            assert fields_of(other) == fields_of(english), (lang, msg_id)
            if isinstance(english, Plural):
                assert isinstance(other, Plural) and other.by == english.by, (lang, msg_id)


def test_no_text_is_left_empty_or_half_translated() -> None:
    for lang in LANGUAGES:
        for msg_id, entry in CATALOGUE[lang].items():
            for form in forms(entry):
                assert form.strip(), (lang, msg_id)
                assert form.count("`") % 2 == 0, (lang, msg_id)  # commands are paired


def _used_ids() -> tuple[set[str], set[str]]:
    """Every string literal in flib's source outside the catalogue, the packaged
    pages' ``{{t:...}}`` slots, and the prefixes status.js joins an id from."""
    literals: set[str] = set()
    for path in SRC.rglob("*.py"):
        if "messages" in path.parts and path.name != "status.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    for page in (UI / "pages").glob("*.html"):
        literals |= set(re.findall(r"\{\{t:([a-z0-9_.]+)\}\}", page.read_text()))
    script = (UI / "static" / "status.js").read_text()
    literals |= set(re.findall(r'"([a-z]+(?:\.[a-z_]+)+)"', script))
    prefixes = set(re.findall(r'"([a-z]+(?:\.[a-z_]+)*\.)"\s*\+', script))
    # An id the interface spells as f"settings.section.{key}": its prefix.
    for path in UI.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.JoinedStr) and node.values:
                first = node.values[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and re.fullmatch(r"[a-z]+(\.[a-z_]+)*\.", first.value)
                ):
                    prefixes.add(first.value)
    return literals, prefixes


def test_every_id_is_used() -> None:
    literals, prefixes = _used_ids()
    unused = [
        msg_id
        for msg_id in CATALOGUE["en"]
        if msg_id not in literals and not any(msg_id.startswith(p) for p in prefixes)
    ]
    assert unused == []


# --- no English left outside the catalogue --------------------------------------------

_WORDS = re.compile(r"[A-Za-z]{2,}[ ,;:'][^`]*[A-Za-z]{2,}|^[A-Z][a-z]{2,}")
_ATTRIBUTES = frozenset(
    {"class_", "type", "name", "method", "action", "href", "id", "value", "autocomplete",
     "inputmode", "spellcheck", "dir", "role", "rel", "src", "formaction", "aria_current",
     "aria_hidden", "aria_labelledby", "data_label", "datetime", "tabindex", "step", "min",
     "max", "maxlength", "anchor", "contentType"}
)  # fmt: skip
"""Keyword arguments that are code (attributes a reader never sees as words)."""
ALLOWED = frozenset(
    {
        # machine words: HTTP, MIME types, headers, cookie attributes, CSS selectors
        "Content-Disposition",
        "Content-Length",
        "Content-Type",
        "Location",
        "Set-Cookie",
        "Allow",
        "Accept",
        "Origin",
        "Host",
        "Upgrade",
        "Cookie",
        "Transfer-Encoding",
        "Sec-Fetch-Site",
        "Sec-Purpose",
        "Purpose",
        "Server: flib",
        "Connection: close",
        "Content-Length: 0",
        "GET, POST",
        "HTTP/0.9",
        "HTTP/1.0",
        "text/html",
        "Value error, ",
        # names, not words of the interface
        "Downloads",
        "Desktop",
        "Anthropic API",
        "Ollama",
        "Claude Code",
        "Keychain",
        "Script Editor",
        "English",
        "Português (Brasil)",
        "Library",  # ~/Library, a path
        "Fields",  # a type alias
        "http://localhost:11434/v1",
        "text/csv; charset=utf-8",
        "text/plain; charset=utf-8",
        # the JSON API's errors: for scripts, versioned, English like any API
        "no session",
        "not found",
        # an exception's text caught on the way: the search falls back with a notice
        "cannot set up (",
        # flib's core English, matched to put the catalogue's words in its place
        "This file",
    }
)
"""Literals that look like words but are not the interface's text."""


def _docstrings(tree: ast.AST) -> set[int]:
    """A string that opens a module, class or function, or follows an assignment
    (an attribute's docstring): documentation, never shown."""
    found = set()
    for node in ast.walk(tree):
        statements: object = getattr(node, "body", None)
        if not isinstance(statements, list):
            continue
        opening = isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        for i, statement in enumerate(statements):
            documents = (i == 0 and opening) or (
                i > 0 and isinstance(statements[i - 1], ast.Assign | ast.AnnAssign)
            )
            if (
                documents
                and isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
            ):
                found.add(id(statement.value))
    return found


def _skipped(tree: ast.AST) -> set[int]:
    """Constants a reader never sees: attribute keywords, exception arguments that
    are never shown (``Malformed``, ``BadRequest``, ``ValueError``...), log calls,
    and text compared with flib's core English (dictionary keys, comparisons, a pair
    with the id of the message shown instead)."""
    found: set[int] = set()
    ids = set(CATALOGUE["en"])
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            found |= {id(n) for n in [node.left, *node.comparators]}
        if isinstance(node, ast.Dict):
            found |= {id(k) for k in node.keys if k is not None}
        if isinstance(node, ast.Tuple) and any(
            isinstance(e, ast.Constant) and e.value in ids for e in node.elts
        ):
            found |= {id(e) for e in node.elts}
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "LAYOUT" for target in node.targets
        ):
            found.add(id(node.value))  # the page frame: markup, its words in slots
        if isinstance(node, ast.keyword) and node.arg in _ATTRIBUTES:
            found |= {id(n) for n in ast.walk(node.value)}
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {
                "Malformed", "BadRequest", "ValueError", "TypeError", "KeyError",
                "RuntimeError", "UiError", "MenuError", "OSError", "debug", "warning",
                "_Reject", "Thread", "compile", "fullmatch", "match", "sub", "search",
                "startswith", "endswith", "removeprefix", "get", "get_all", "send_header",
            }:  # fmt: skip
                found |= {id(n) for n in ast.walk(node)}
    return found


SCANNED = (
    *sorted(UI.glob("*.py")),
    SRC / "menubar_state.py",
)
SCANNED_FUNCTIONS = {
    SRC / "menubar_state.py": {"describe", "next_line"},
    UI / "server.py": {"handoff_text", "_problem", "_ended", "_get", "_handle"},
}
"""In these files only these functions are the interface's (the rest is the command line's)."""


def _strings(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree) | _skipped(tree)
    roots: list[ast.AST] = [tree]
    only = SCANNED_FUNCTIONS.get(path)
    if only is not None:
        roots = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in only]
    found = []
    for root in roots:
        for node in ast.walk(root):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and node.value not in ALLOWED
                and _WORDS.search(node.value)
            ):
                found.append((node.lineno, node.value))
    return found


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_no_words_outside_the_catalogue(path: Path) -> None:
    assert _strings(path) == []


def test_the_server_module_constants_shown_to_people_are_messages() -> None:
    from file_librarian.ui import server

    assert all(isinstance(v, i18n.Msg) for v in server.PROBLEMS.values() if v)
    assert isinstance(server.STALE_FORM, i18n.Msg) and isinstance(server.STALE_SESSION, i18n.Msg)


def test_the_packaged_pages_and_script_hold_no_words() -> None:
    for page in (UI / "pages").glob("*.html"):
        text = re.sub(r"\{\{[^}]*\}\}", "", page.read_text())
        text = re.sub(r"<[^>]*>", " ", text)
        words = set(re.findall(r"[A-Za-z][A-Za-z-]+", text)) - {"file-librarian"}
        assert words == set(), page.name
    script = (UI / "static" / "status.js").read_text()
    code = re.sub(r"//[^\n]*", "", script).replace('"use strict"', "")
    assert re.findall(r'"[^"\n]*[A-Za-z]{2,} [A-Za-z]{2,}[^"\n]*"', code) == []


def test_the_menu_entries_are_messages() -> None:
    for action in menubar.ACTIONS:
        assert action.title in CATALOGUE["en"]


# --- plurals, fallback, escaping -------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "count", "form"),
    [("en", 0, "other"), ("en", 1, "one"), ("en", 2, "other"),
     ("pt-BR", 0, "one"), ("pt-BR", 1, "one"), ("pt-BR", 2, "other")],
)  # fmt: skip
def test_plural_categories(lang: i18n.Lang, count: int, form: str) -> None:
    assert i18n.category(lang, count) == form


def test_plural_messages_in_both_languages() -> None:
    with i18n.using("pt-BR"):
        assert [t("menu.to_review", count=n) for n in (0, 1, 2)] == [
            "nenhum arquivo para revisar",
            "1 arquivo para revisar",
            "2 arquivos para revisar",
        ]
    assert [t("menu.to_review", count=n) for n in (0, 1, 2)] == [
        "0 files to review",
        "1 file to review",
        "2 files to review",
    ]


def test_a_plural_needs_a_whole_number() -> None:
    with pytest.raises(TypeError):
        t("menu.to_review", count="2")


def test_unknown_languages_are_english() -> None:
    assert i18n.resolve("fr") == "en" and i18n.resolve(None) == "en"
    assert i18n.resolve("pt-BR") == "pt-BR"
    with i18n.using("xx") as lang:
        assert lang == "en" and t("nav.review") == "Review"


def test_a_missing_translation_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    portuguese = dict(CATALOGUE["pt-BR"])
    del portuguese["nav.review"]
    monkeypatch.setitem(CATALOGUE, "pt-BR", portuguese)
    with i18n.using("pt-BR"):
        assert t("nav.review") == "Review" and t("nav.due") == "Vencimentos"


def test_a_message_renders_again_in_the_language_of_the_page() -> None:
    made = t("menu.to_review", count=2)  # made in English, as a kept outcome is
    assert made == "2 files to review"
    with i18n.using("pt-BR"):
        assert markup.render(made) == "2 arquivos para revisar"
        assert made.text() == "2 arquivos para revisar"
        assert markup.join(made) == "2 arquivos para revisar"
    assert made.text("pt-BR") == "2 arquivos para revisar"


def test_nested_messages_follow_the_language() -> None:
    outer = t("js.month_unpriced", cost="US$ 1.0000", runs=t("js.runs", count=2))
    with i18n.using("pt-BR"):
        assert outer.text() == "US$ 1.0000 (mais 2 execuções sem custo registrado)"


def test_a_hostile_translation_or_value_never_adds_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    hostile = '<script>alert(1)</script>{value} `<b>{value}</b>` & "quote" <img src=x>'
    monkeypatch.setitem(CATALOGUE, "en", {**CATALOGUE["en"], "test.hostile": hostile})
    value = '<img src=x onerror=alert(1)> `not code` {value} "'
    html = markup.render(t("test.hostile", value=value))
    assert "<script" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert html.count("<code>") == 1  # the translation's own backticks, never the value's
    assert "`not code`" in html  # a value's backticks stay backticks
    assert "{value}" in html  # a value is never expanded again
    attribute = markup.el("a", "x", aria_label=t("test.hostile", value=value))
    assert "<img" not in attribute.split(">", 1)[0][3:] and "&quot;" in attribute


def test_a_value_inside_a_command_is_escaped() -> None:
    html = markup.render(t("settings.command", text="flib <x> `y`"))
    assert html == "<code>flib &lt;x&gt; `y`</code>"


def test_a_number_must_be_formatted_first() -> None:
    with pytest.raises(TypeError):
        markup.render(t("js.today", cost=0.5))
    with pytest.raises(TypeError):
        t("js.today", cost=0.5)


# --- formats ----------------------------------------------------------------------------


def test_dates_and_times() -> None:
    day = date(2026, 11, 25)
    moment = datetime(2026, 11, 25, 22, 48)
    assert formats.day(day, "en") == "2026-11-25" and formats.day(day, "pt-BR") == "25/11/2026"
    assert formats.moment(moment, "en") == "2026-11-25 22:48"
    assert formats.moment(moment, "pt-BR") == "25/11/2026 22:48"
    assert formats.long_day(day, "en") == "Wednesday, 25 November 2026"
    assert formats.long_day(day, "pt-BR") == "quarta-feira, 25 de novembro de 2026"
    assert formats.long_day(date(2026, 3, 1), "en") == "Sunday, 01 March 2026"
    assert formats.weekday_clock(moment, "en") == "Wed 22:48"
    assert formats.weekday_clock(moment, "pt-BR") == "qua 22:48"
    assert formats.clock(datetime(2026, 1, 1, 7, 5)) == "07:05"


def test_money() -> None:
    assert formats.amount(123456, "BRL", "en") == "BRL 1,234.56"
    assert formats.amount(123456, "BRL", "pt-BR") == "R$ 1.234,56"
    assert formats.amount(1234, "USD", "pt-BR") == "US$ 12,34"
    assert formats.amount(123456, "EUR", "pt-BR") == "EUR 1.234,56"
    assert formats.amount(1234, "JPY", "pt-BR") == "JPY 1.234"
    assert formats.amount(123456789, "USD", "en") == "USD 1,234,567.89"
    assert formats.usd(0.0123, lang="en") == "US$ 0.0123"
    assert formats.usd(0.0123, lang="pt-BR") == "US$ 0,0123"
    assert formats.usd(3, 2, "pt-BR") == "US$ 3,00"
    assert formats.decimal(0.3, 2, "pt-BR") == "0,30"


def test_the_language_in_use_is_the_default_for_formats() -> None:
    with i18n.using("pt-BR"):
        assert formats.day(date(2026, 1, 2)) == "02/01/2026"
    assert formats.day(date(2026, 1, 2)) == "2026-01-02"


@pytest.mark.parametrize(
    ("lang", "typed", "read"),
    [
        ("pt-BR", "3,00", "3.00"),
        ("pt-BR", "3.00", "3.00"),
        ("pt-BR", "3", "3"),
        ("pt-BR", "0,5", "0.5"),
        ("pt-BR", "1,234", "1.234"),  # a comma is the decimal separator in pt-BR
        ("pt-BR", "12.3456", "12.3456"),
        ("pt-BR", " 2,5 ", "2.5"),
        ("en", "3.00", "3.00"),
        ("en", "3,5", "3.5"),
        ("en", "1.234", "1.234"),
        ("en", "1000", "1000"),
    ],
)
def test_a_typed_amount_is_read(lang: i18n.Lang, typed: str, read: str) -> None:
    assert formats.parse_amount(typed, lang) == Decimal(read)


@pytest.mark.parametrize(
    ("lang", "typed", "kind"),
    [
        ("pt-BR", "1.234", "ambiguous"),  # a thousand and some, or a little over one?
        ("en", "1,234", "ambiguous"),
        ("pt-BR", "1.234,56", "invalid"),
        ("en", "1,234.56", "invalid"),
        ("en", "-1", "invalid"),
        ("en", "+1", "invalid"),
        ("pt-BR", "+1,5", "invalid"),
        ("en", "1e3", "invalid"),
        ("en", "", "invalid"),
        ("en", "12345", "invalid"),
        ("en", "1.23456", "invalid"),
        ("en", "1 000", "invalid"),
        ("pt-BR", "R$ 3", "invalid"),
    ],
)
def test_a_typed_amount_is_refused(lang: i18n.Lang, typed: str, kind: str) -> None:
    with pytest.raises(formats.AmountError) as caught:
        formats.parse_amount(typed, lang)
    assert caught.value.kind == kind


def test_an_amount_shown_in_a_field_reads_back_as_it_is() -> None:
    for lang in LANGUAGES:
        for value in (3.0, 0.5, 1.25, 1000.0, 0.0001, 12.3456):
            shown = formats.amount_input(value, lang)
            assert float(formats.parse_amount(shown, lang)) == value, (lang, shown)
    assert formats.amount_input(3.0, "pt-BR") == "3,0"
    assert formats.amount_input(None) == ""


def test_the_status_script_gets_the_formats_and_its_messages() -> None:
    bundle = status_messages.bundle("pt-BR")
    assert bundle["formats"] == {"lang": "pt-BR", "one": [0, 1], "decimal": ",", "dateOrder": "dmy"}
    messages = bundle["m"]
    assert isinstance(messages, dict)
    assert messages["js.nothing_waits"] == "Nada espera por você."
    assert messages["js.waiting"] == {
        "zero": "Nenhum arquivo espera por você.",
        "one": "{count} arquivo espera por você.",
        "other": "{count} arquivos esperam por você.",
        "by": "count",
    }
    assert messages["js.trigger.manual"] == "manual"
    assert set(messages) == set(status_messages.SCRIPT_IDS)


# --- the server speaks the running configuration's language ----------------------------


def _pt(cfg: Config) -> Config:
    return cfg.model_copy(update={"language": "pt-BR"})


@pytest.fixture
def pt_ui(loopback_network: None, cfg: Config) -> Iterator[Ui]:
    server = Ui(_pt(cfg), poll_seconds=0.01)
    server.start()
    yield server
    server.close()


def test_every_page_says_its_language(pt_ui: Ui) -> None:
    client = UiClient(pt_ui.port)
    assert client.launch(pt_ui.launch_url).status == 303
    for path in ("/status", "/review", "/search", "/activity", "/due", "/settings"):
        reply = client.get(path)
        assert reply.status == 200, path
        assert '<html lang="pt-BR">' in reply.text, path
        assert "Encerrar sessão" in reply.text and "Pular para o conteúdo" in reply.text
        assert ">Revisão</a>" in reply.text and ">Configurações</a>" in reply.text
    status = client.get("/status").text
    assert "Carregando…" in status and "Execuções" in status
    meta = re.search(r'<meta name="flib-messages" content="([^"]*)">', status)
    assert meta is not None
    bundle = json.loads(html.unescape(meta.group(1)))
    assert bundle["m"]["js.nothing_waits"] == "Nada espera por você."
    missing = client.get("/no-such-page", headers={"Accept": "text/html"})
    assert missing.status == 404 and "Página não encontrada" in missing.text
    assert "Esta página não existe." in missing.text


def test_the_ended_page_is_in_the_language_too(pt_ui: Ui) -> None:
    stranger = UiClient(pt_ui.port)
    reply = stranger.get("/status")
    assert reply.status == 403 and '<html lang="pt-BR">' in reply.text
    assert "Esta sessão terminou" in reply.text and "<code>flib ui</code>" in reply.text


def test_a_change_of_language_applies_from_the_next_request(ui: Ui) -> None:
    client = UiClient(ui.port)
    assert client.launch(ui.launch_url).status == 303
    assert '<html lang="en">' in client.get("/due").text
    ui.config = _pt(ui.config)  # what saving the language on the Settings page does
    page = client.get("/due").text
    assert '<html lang="pt-BR">' in page and "<h1>Vencimentos</h1>" in page


@pytest.fixture
def ui(loopback_network: None, cfg: Config) -> Iterator[Ui]:
    server = Ui(cfg, poll_seconds=0.01)
    server.start()
    yield server
    server.close()


def test_the_handoff_file_is_in_the_language(cfg: Config) -> None:
    text = handoff_text("pt-BR", "http://127.0.0.1:1/launch", "tok<en>")
    assert '<html lang="pt-BR">' in text and "<title>Abrindo o file-librarian</title>" in text
    assert "<code>flib ui --no-browser</code>" in text
    assert "tok&lt;en&gt;" in text
    done = re.search(r'textContent = ("[^"]*");', text)
    assert done is not None and "\\u00ea" in done.group(1)  # "Você", escaped for the script
    assert json.loads(done.group(1)).startswith("O file-librarian abriu neste navegador.")
    english = handoff_text("en", "http://127.0.0.1:1/launch", "tok")
    assert '<html lang="en">' in english and "Opening file-librarian…" in english


# --- the menu bar -----------------------------------------------------------------------


def test_the_menu_bar_lines_in_portuguese(cfg: Config) -> None:
    due = _status(cfg, due=DueCounts(7, 2, 1))
    with i18n.using("pt-BR"):
        line, _ = menubar_state.describe(due, running=False, problem="no_access")
        assert (
            line == "O macOS negou acesso a uma pasta (`flib doctor`), 1 vencido, 2 vencem em breve"
        )
        assert menubar_state.describe(due, running=True, problem=None)[0] == "Organizando…"
        assert menubar_state.describe(None, running=False, problem=None)[0].startswith(
            "Não foi possível ler o índice"
        )
        run = status_mod.NextRun(True, True, "12:30", datetime(2026, 11, 26, 12, 30), mode="daily")
        assert menubar_state.next_line(run) == "Próxima execução qui 12:30"
        both = status_mod.NextRun(True, True, "12:30", datetime(2026, 11, 26, 12, 30), mode="both")
        assert menubar_state.next_line(both).endswith(", e quando chegarem arquivos")


def test_the_state_line_follows_the_configuration(cfg: Config, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"language: pt-BR\ndatabase: {cfg.database}\noutput_root: {cfg.output_root}\n"
        f"inputs: [{cfg.input_paths[0]}]\nprovider: {{name: fake, model: x}}\n"
    )
    shown = menubar_state.view(cfg.database, config)
    assert shown.line == "Nada pendente"


def test_the_state_file_stays_ascii_in_portuguese(cfg: Config) -> None:
    view = menubar_state.View(
        poses.Pose.BUSY, 2, "Organizando…", "Próxima execução qui 12:30", 2, 0, True
    )
    data = view.to_json()
    assert data.isascii() and b"\\u2026" in data and b"\\u00f3" in data
    assert re.fullmatch(menubar.FORM, data.decode("ascii"))
    clean = menubar._checked(json.loads(data))
    assert clean["line"] == "Organizando…" and clean["next"] == "Próxima execução qui 12:30"


def test_the_menu_is_built_in_the_configuration_language(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("language: pt-BR\nprovider: {name: fake, model: x}\n")
    program, state = tmp_path / "python", tmp_path / "state"
    source = menubar.script(program, config, state)
    for words in ("Abrir o flib", "Revisão", "Vencimentos", "Organizar agora", "Configurações…"):
        assert menubar.literal(words) in source, words
    assert menubar.literal("Sair") in source
    assert menubar.literal("Ainda sem notícias do flib") in source
    english = menubar.script(program, config, state, language="en")
    assert menubar.literal("Organize now") in english and menubar.literal("Quit") in english
    assert english != source  # so the stamp changes and a refresh rebuilds the app
    config.write_text("language: [unreadable\n")
    assert menubar.literal("Organize now") in menubar.script(program, config, state)
    assert menubar.language_of(tmp_path / "missing.yaml") == "en"


# --- the review of #135 -----------------------------------------------------------------


def test_every_count_in_portuguese_has_a_zero_form() -> None:
    """Never "0 arquivo": a pt-BR plural that shows its number says "nenhum" for zero."""
    for msg_id, entry in CATALOGUE["pt-BR"].items():
        if not isinstance(entry, Plural):
            continue
        number = "{" + entry.by + "}"
        if number in entry.one or number in entry.other:
            assert entry.zero is not None, msg_id
            assert number not in entry.zero, msg_id


@pytest.mark.parametrize(
    ("msg_id", "texts"),
    [
        ("undo.skipped_count", ("nenhum ignorado", "1 ignorado", "2 ignorados")),
        ("activity.organized", ("nenhum organizado", "1 organizado", "2 organizados")),
        ("js.runs", ("nenhuma execução", "1 execução", "2 execuções")),
        ("menu.overdue", ("nenhum vencido", "1 vencido", "2 vencidos")),
    ],
)
def test_zero_one_two_in_portuguese(msg_id: str, texts: tuple[str, str, str]) -> None:
    assert tuple(t(msg_id, count=n).text("pt-BR") for n in (0, 1, 2)) == texts


def test_english_keeps_its_numbers_at_zero() -> None:
    assert t("undo.skipped_count", count=0).text("en") == "0 skipped"


def test_an_attribute_from_a_message_is_plain_text() -> None:
    link = markup.el("a", "x", title=t("frame.footer"), aria_label=t("ended.again"))
    head = link.split(">", 1)[0]
    assert "<code>" not in head and "&lt;code&gt;" not in head and "`" not in head
    assert 'title="Served on this Mac, at 127.0.0.1, while flib ui runs.' in head
    assert 'aria-label="For a new session, run flib ui in Terminal."' in head


def test_markup_as_an_attribute_is_its_text_escaped_once() -> None:
    shown = markup.Markup("&lt;b&gt; <i>x</i> &amp; y")
    assert shown.plain_text() == "<b> x & y"
    link = markup.el("a", "z", aria_label=shown)
    assert 'aria-label="&lt;b&gt; x &amp; y"' in link
    assert "&amp;lt;" not in link


def test_an_unpaired_backtick_in_a_translation_is_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(CATALOGUE, "en", {**CATALOGUE["en"], "test.odd": "run `flib {v} now"})
    assert markup.render(t("test.odd", v="<x>")) == "run `flib &lt;x&gt; now"
    monkeypatch.setitem(CATALOGUE, "en", {**CATALOGUE["en"], "test.even": "run `flib {v}` now"})
    assert markup.render(t("test.even", v="<x>")) == "run <code>flib &lt;x&gt;</code> now"


def test_the_language_is_set_again_on_a_thread_that_served_another() -> None:
    import threading

    seen: list[str] = []

    def requests() -> None:  # one thread, two requests in two languages
        i18n.use("pt-BR")
        seen.append(t("nav.review").text())
        i18n.use("en")
        seen.append(t("nav.review").text())
        i18n.use("xx")
        seen.append(i18n.current())

    thread = threading.Thread(target=requests)
    thread.start()
    thread.join()
    assert seen == ["Revisão", "Review", "en"]


def test_a_search_amount_follows_the_same_rule_as_settings(cfg: Config) -> None:
    from file_librarian.ui import views

    def error(lang: i18n.Lang, typed: str) -> str | None:
        form = views.SearchForm.parse({"min_amount": [typed]})
        with i18n.using(lang):
            found = views.find(cfg, form)
            return None if found.error is None else markup.esc(found.error)

    ambiguous = error("pt-BR", "1.234")
    assert ambiguous is not None and "pode ser lido de dois jeitos" in ambiguous
    assert "Valor mínimo" in ambiguous
    english = error("en", "1,234")
    assert english is not None and "could be read two ways" in english
    assert error("pt-BR", "1234,56") is None and error("en", "1234.56") is None
    assert error("pt-BR", "1,234") is None  # a comma is the decimal point in pt-BR
    wrong = error("pt-BR", "1.234,56")
    assert wrong is not None and "não é um valor" in wrong


def test_the_state_is_written_in_english_with_an_unreadable_configuration(
    cfg: Config, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("language: [unreadable\n")
    shown = menubar_state.view(cfg.database, config, status=_status(cfg))
    assert shown.line == "Nothing pending"


def test_search_errors_in_the_page_words(cfg: Config) -> None:
    from file_librarian.ui import views

    def error(**fields: str) -> str:
        form = views.SearchForm.parse({k: [v] for k, v in fields.items()})
        with i18n.using("pt-BR"):
            found = views.find(cfg, form)
            assert found.error is not None
            return markup.esc(found.error)

    since = error(since="2026-11-25", until="2026-11-01")
    assert "25/11/2026" in since and "01/11/2026" in since and "2026-11-25" not in since
    kind = views.SearchForm.parse({"kind": ["nonsense"]})
    with i18n.using("pt-BR"):
        found = views.find(cfg, kind)
        assert found.error is not None
        text = markup.esc(found.error)
    assert "conta, recibo, fatura" in text and "bill" not in text


def test_negative_money_puts_the_sign_first() -> None:
    assert formats.amount(-123456, "BRL", "pt-BR") == "-R$ 1.234,56"
    assert formats.amount(-1234, "EUR", "pt-BR") == "-EUR 12,34"
    assert formats.amount(-123456, "BRL", "en") == "BRL -1,234.56"  # as flib prints it
