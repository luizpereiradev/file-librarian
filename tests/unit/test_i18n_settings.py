"""The Settings page in Brazilian Portuguese (#126).

The page is built under ``i18n.using("pt-BR")``, as the server does for a request
when the configuration says ``language: pt-BR``; the English page is what
``test_ui_settings`` checks. Changes go through ``settings.save``, on the same
commented configuration file in a temporary folder.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from file_librarian import i18n
from file_librarian.config import InputConfig, load_config, parse_config
from file_librarian.i18n import t
from file_librarian.ui import settings
from file_librarian.ui.actions import Outcome, Refused
from file_librarian.ui.markup import render
from file_librarian.ui.server import Ui
from tests.unit.test_ui_settings import FILE


@pytest.fixture
def path(fake_home: Path, downloads: Path) -> Path:
    """The commented configuration file ``test_ui_settings`` changes, in English."""
    folder = fake_home / ".config" / "file-librarian"
    folder.mkdir(parents=True)
    target = folder / "config.yaml"
    target.write_text(FILE.format(downloads=json.dumps(str(downloads))), encoding="utf-8")
    target.chmod(0o600)
    return target


def page_text(path: Path, outcome: Outcome | None = None) -> str:
    code, body = settings.settings_page(path, "token", outcome, gone=False)
    assert code == 200
    return body.decode("utf-8")


def costs(path: Path, **fields: str) -> Outcome:
    """Post the cost caps as the page does (the pin of the file as it is now)."""
    form = {"section": ["costs"], "pin": [settings.read(path).pin]}
    form |= {key: [value] for key, value in fields.items()}
    outcome, _ = settings.save(load_config(path), path, form)
    return outcome


def limits(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["limits"]


def test_the_page_speaks_portuguese(path: Path) -> None:
    with i18n.using("pt-BR"):
        text = page_text(path)
    assert '<html lang="pt-BR">' in text
    for words in (
        "<h1>Configurações</h1>",
        "Quando organizar",
        "Quem classifica",
        "O que o flib lê e envia",
        "Limites de custo",
        "Notificações e vencimentos",
        "Salvar o agendamento",
        "Quando chegarem arquivos",
        "O flib lê só o primeiro nível de cada pasta de entrada.",
        "Adicionar uma categoria",
        "Por execução, US$ (vazio: sem limite)",
        "Idioma da interface, dos nomes e das descrições",
        "Abrir num editor de texto",
        "<code>flib check-config</code>",
    ):
        assert words in text, words
    for english in ("When to organize", "Save the schedule", "Add a category", ">Save<"):
        assert english not in text, english


def test_languages_are_listed_by_their_own_names(path: Path) -> None:
    for lang in ("en", "pt-BR"):
        with i18n.using(lang):
            text = page_text(path)
        assert '<option value="en" selected>English</option>' in text
        assert '<option value="pt-BR">Português (Brasil)</option>' in text


def test_plurals_in_portuguese_say_none_for_zero() -> None:
    with i18n.using("pt-BR"):
        assert t("settings.holds.index", count=0) == "o índice não tem nenhum arquivo nela"
        assert t("settings.holds.index", count=1) == "o índice tem 1 arquivo nela"
        assert t("settings.holds.index", count=2) == "o índice tem 2 arquivos nela"
        assert t("settings.scheduled.watch", count=2).startswith(
            "Modo de observação ligado: o flib roda quando algo chega nas 2 pastas de entrada"
        )
    assert t("settings.holds.index", count=0) == "the index has 0 files in it"


def test_a_cap_typed_with_a_comma_is_read_as_shown(path: Path) -> None:
    with i18n.using("pt-BR"):
        assert 'name="per_run" value="1,0"' in page_text(path)  # the file has 1.00
        assert costs(path, per_run="3,00", per_day="default").text == "Salvo."
        assert limits(path) == {"max_cost_usd_per_run": 3.0}
        costs(path, per_run="2.50", per_day="amount", day_amount="4,5")
        assert limits(path) == {"max_cost_usd_per_run": 2.5, "max_cost_usd_per_day": 4.5}
        assert 'name="per_run" value="2,5"' in page_text(path)


def test_a_cap_posted_as_shown_is_not_rewritten(path: Path) -> None:
    before = path.read_bytes()
    with i18n.using("pt-BR"):
        assert costs(path, per_run="1,0", per_day="default").text == settings.NOTHING
        assert costs(path, per_run="1.0", per_day="default").text == settings.NOTHING
    assert path.read_bytes() == before


@pytest.mark.parametrize(("lang", "typed"), [("pt-BR", "1.234"), ("en", "1,234")])
def test_a_thousands_separator_is_refused_not_guessed(path: Path, lang: str, typed: str) -> None:
    before = path.read_bytes()
    with i18n.using(lang), pytest.raises(Refused) as refused:
        costs(path, per_run=typed, per_day="default")
    words = "pode ser lido de dois jeitos" if lang == "pt-BR" else "could be read two ways"
    assert words in refused.value.outcome.text
    assert path.read_bytes() == before


def test_the_other_separator_is_a_decimal_point(path: Path) -> None:
    with i18n.using("en"):
        costs(path, per_run="1.234", per_day="default")
    assert limits(path) == {"max_cost_usd_per_run": 1.234}
    with i18n.using("pt-BR"):
        costs(path, per_run="1,25", per_day="default")
    assert limits(path) == {"max_cost_usd_per_run": 1.25}


def test_a_bad_amount_gives_the_example_in_the_pages_language(path: Path) -> None:
    with i18n.using("pt-BR"), pytest.raises(Refused) as refused:
        costs(path, per_run="abc", per_day="default")
    assert refused.value.outcome.text == (
        "Não salvo: o limite por execução precisa ser um valor em US$ acima de 0 e de no "
        "máximo 1000, como 1,50."
    )


def test_after_switching_the_language_the_page_and_its_outcome_are_in_the_new_one(
    path: Path,
) -> None:
    form = {"section": ["language"], "pin": [settings.read(path).pin], "language": ["pt-BR"]}
    outcome, saved = settings.save(load_config(path), path, form)  # posted from an English page
    assert saved is not None and saved.language == "pt-BR"
    assert outcome.text == "Saved."
    with i18n.using(saved.language):  # the next page load, as the server makes it
        text = page_text(path, outcome)
    assert '<html lang="pt-BR">' in text
    assert "Salvo." in text and "Saved." not in text
    assert "<strong>Idioma</strong>: English → Português (Brasil)" in text


def test_a_backtick_in_a_name_stays_text_in_portuguese(path: Path) -> None:
    name = "a `rm -rf` b"
    refusal = t(
        "settings.category.not_removed",
        name=name,
        why=t("settings.holds.index", count=2),
        command=settings.command(path, "reprocess", "--remove-category", name),
    )
    with i18n.using("pt-BR"):
        html = render(refusal)
    assert "<code>rm -rf</code>" not in html
    assert "a `rm -rf` b não foi removida: o índice tem 2 arquivos nela." in html
    assert "<code>flib reprocess --remove-category &#x27;a `rm -rf` b&#x27;</code>" in html


def test_the_rules_say_in_english_what_check_config_says(downloads: Path) -> None:
    config = parse_config(
        {
            "inputs": [
                str(downloads),
                {
                    "path": str(downloads.parent / "Desktop"),
                    "only": ["*.pdf", "*.png"],
                    "ignore": ["~*"],
                    "min_age_days": 7,
                    "folders": "ignore",
                },
                {"path": str(downloads.parent / "Scans"), "category": "Other", "rename": False},
            ],
            "ignore": ["*.part"],
            "provider": {"name": "fake", "model": "x"},
        }
    )
    entries: list[InputConfig] = list(config.inputs)
    for entry in entries:
        assert settings.rules_text(config, entry) == config.input_summary(entry)
    with i18n.using("pt-BR"):
        text = str(settings.rules_text(config, entries[1]))
    assert text == (
        "só *.pdf, *.png; ignora *.part, ~* (1 só desta pasta); só o que não muda há 7 dias; "
        "subpastas: ignoradas; duplicados: separados; classificados pela IA"
    )


def test_a_cost_cap_in_what_changed_has_the_page_decimal_separator() -> None:
    from file_librarian.ui import settings as settings_mod

    with i18n.using("pt-BR"):
        assert settings_mod.describe("limits.max_cost_usd_per_run", 3.0) == "3,0"
    assert settings_mod.describe("limits.max_cost_usd_per_run", 3.0) == "3.0"


# --- the menu bar's rebuild never fails a saved change (#135 review) --------------------


@pytest.fixture
def serve(loopback_network: None) -> Iterator[list[Ui]]:
    started: list[Ui] = []
    yield started
    for ui in started:
        ui.close()


@pytest.fixture
def broken_menu(monkeypatch: pytest.MonkeyPatch, path: Path) -> list[str]:
    """The menu bar item installed for this file, and its rebuild failing with an error
    no one planned for (a bundle it cannot read, say)."""
    from file_librarian import menubar

    calls: list[str] = []
    monkeypatch.setattr(menubar, "installed", lambda: True)
    monkeypatch.setattr(
        menubar, "read_target", lambda: menubar.Target(path, load_config(path).database)
    )

    def refresh() -> str:
        calls.append("refresh")
        raise ValueError("bad Info.plist")

    monkeypatch.setattr(menubar, "refresh", refresh)
    return calls


def test_a_failed_menu_rebuild_never_fails_the_language_change(
    serve: list[Ui], path: Path, broken_menu: list[str]
) -> None:
    from tests.unit.test_ui_settings import change, start

    client = start(serve, load_config(path), path)
    text = change(client, "language", language="pt-BR")
    assert broken_menu == ["refresh"]
    assert load_config(path).language == "pt-BR"
    assert serve[0].config.language == "pt-BR"  # the server goes on with what is saved
    assert '<html lang="pt-BR">' in text and "Salvo." in text
    assert "ainda no idioma anterior" in text and "flib menu --refresh" in text


def test_a_failed_menu_rebuild_never_fails_a_restore(
    serve: list[Ui], path: Path, broken_menu: list[str]
) -> None:
    import re

    from tests.unit.test_ui_settings import change, post, start

    client = start(serve, load_config(path), path)
    change(client, "language", language="pt-BR")
    confirm = client.get("/settings/restore").text
    found = dict(re.findall(r'name="(pin|backup)" value="([0-9a-f]+)"', confirm))
    csrf = re.search(r'name="csrf" value="([^"]+)"', confirm)
    assert csrf is not None
    reply = post(client, "/settings/restore", {"csrf": csrf.group(1)} | found)
    assert reply.status == 303
    text = client.get(reply.headers["Location"]).text
    assert broken_menu == ["refresh", "refresh"]
    assert load_config(path).language == "en" and serve[0].config.language == "en"
    assert '<html lang="en">' in text and "The previous settings are back" in text
    assert "still in the old language" in text


def test_a_cap_shown_in_either_language_is_left_alone() -> None:
    assert settings.as_shown("3.0", 3.0) and settings.as_shown("3,0", 3.0)
    assert not settings.as_shown("3,00", 3.0) and not settings.as_shown("3", 3.0)
    with i18n.using("pt-BR"):
        assert settings.as_shown("0.5", 0.5)  # shown in English, posted after a switch
