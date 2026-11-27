"""The read-only pages, Review and Undo in Brazilian Portuguese (#126).

The other page tests run in English, the default; these build the same pages with
``i18n.using("pt-BR")``: the words, the plurals, dates and money, a refusal from
flib's core in the form's words, and an outcome kept in one language and shown
in another.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from file_librarian import i18n
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.i18n import Lang, Msg, formats, t
from file_librarian.i18n.core import _KNOWN, core
from file_librarian.search import SearchError, make_filters
from file_librarian.ui import actions, review, views
from file_librarian.ui.markup import render
from tests._helpers import answer
from tests.unit.test_ui_pages import bill, fill, soon
from tests.unit.test_ui_review import REVIEW

PT: Lang = "pt-BR"
SECRET = "a session secret"  # noqa: S105 - keys review groups in addresses


def page(result: tuple[int, bytes]) -> str:
    code, body = result
    assert code == 200
    return body.decode("utf-8")


BILLS = {
    "power bill.pdf": bill(issuer="Power Co", amount="1234.56", currency="BRL", due_date=soon(3)),
    "water bill.pdf": bill(issuer="Water Co", amount="12.34", currency="USD", due_date=soon(1)),
    "notes.txt": answer("Other", confidence=0.95),
}


def test_activity_in_portuguese(downloads: Path, cfg: Config) -> None:
    run_id = fill(downloads, cfg, BILLS)
    with i18n.using(PT):
        text = page(views.activity_page(cfg, "tok", "days=7"))
        heading = formats.long_day(date.today())
    assert "<h1>Atividade</h1>" in text
    assert "O que cada execução fez, da mais recente para a mais antiga." in text
    assert heading in text and " de " in heading  # "quarta-feira, 25 de novembro de 2026"
    assert "1 execução, " in text
    assert f"execução {run_id}" in text and "Desfazer esta execução…" in text
    assert "3 organizados" in text  # the sensitive one is left in place
    assert "1 arquivo sensível ficou onde estava" in text
    assert "7 dias" in text and "What each run did" not in text


def test_search_in_portuguese(downloads: Path, cfg: Config) -> None:
    fill(downloads, cfg, BILLS)
    due = date.fromisoformat(soon(3))
    with i18n.using(PT):
        text = page(views.search_page(cfg, "tok", {"q": ["power"]}))
    assert "R$ 1.234,56" in text  # in the match's line and in the totals
    assert f"vence em {due:%d/%m/%Y}" in text
    assert "Busca: 1 resultado" in text and '<h2 class="count">1 resultado</h2>' in text
    assert "Mostrar no Finder" in text and "Exportar CSV" in text
    assert "Valor mínimo" in text and "Show in Finder" not in text


def test_a_filter_error_from_the_core_is_in_the_forms_words(downloads: Path, cfg: Config) -> None:
    fill(downloads, cfg, BILLS)
    with i18n.using(PT):
        text = page(views.search_page(cfg, "tok", {"q": ["x"], "min_amount": ["abc"]}))
    assert "Valor mínimo: &#x27;abc&#x27; não é um valor como 100 ou 99,90" in text
    with i18n.using("en"):
        english = page(views.search_page(cfg, "tok", {"q": ["x"], "min_amount": ["abc"]}))
    assert "At least &#x27;abc&#x27; is not an amount like 100 or 99.90" in english


def test_money_in_both_languages() -> None:
    assert formats.amount(123456, "BRL", PT) == "R$ 1.234,56"
    assert formats.amount(1234, "USD", PT) == "US$ 12,34"
    assert formats.amount(123456, "EUR", PT) == "EUR 1.234,56"
    assert formats.amount(123456, "BRL", "en") == "BRL 1,234.56"  # as flib prints it
    assert views.money(0.0123) == "US$ 0.0123"
    with i18n.using(PT):
        assert views.money(0.0123) == "US$ 0,0123"
        assert views.money(0) == "sem custo" and views.money(None) == "custo não registrado"


def test_due_in_portuguese(downloads: Path, cfg: Config) -> None:
    fill(downloads, cfg, BILLS)
    first = date.fromisoformat(soon(1))
    with i18n.using(PT):
        text = page(views.due_page(cfg, "tok", "within=7d"))
    assert "<h1>Vencimentos</h1>" in text
    assert f">{first:%d/%m/%Y}</time>" in text and f'datetime="{first.isoformat()}"' in text
    assert "amanhã" in text and "em 3 dias" in text
    assert 'data-label="Vencimento"' in text and "Marcar como concluído" in text
    assert "R$ 1.234,56" in text and "US$ 12,34" in text
    assert "Total em aberto, por moeda" in text


@pytest.mark.parametrize(
    ("days", "words"),
    [(0, "hoje"), (1, "amanhã"), (-1, "ontem"), (2, "em 2 dias"), (-3, "há 3 dias")],
)
def test_due_days_in_words(days: int, words: str) -> None:
    with i18n.using(PT):
        assert views.relative(days).text() == words


def test_review_in_portuguese(downloads: Path, cfg: Config) -> None:
    fill(downloads, cfg, REVIEW)
    with i18n.using(PT):
        text = page(review.review_page(cfg, "tok", None, gone=False, secret=SECRET))
    assert "<h1>Revisão</h1>" in text
    assert "5 arquivos em revisão" in text
    assert "Sem certeza: sugestão Images" in text
    assert "A IA não teve certeza suficiente; ela sugeriu Images." in text
    assert "Aceitar Images para os 3…" in text
    assert "em revisão desde hoje" in text and "confiança 0,30" in text
    assert "Devolver para onde estava…" in text and "Abrir" in text


@pytest.mark.parametrize(
    ("count", "words"), [(0, "Nenhum arquivo"), (1, "1 arquivo"), (2, "2 arquivos")]
)
def test_plurals_in_portuguese_have_a_zero_form(count: int, words: str) -> None:
    assert t("review.count", count=count).text(PT) == f"{words} em revisão"
    english = "1 file in review" if count == 1 else f"{count} files in review"
    assert t("review.count", count=count).text("en") == english


def test_an_outcome_made_in_english_shows_in_portuguese() -> None:
    outcome = actions.Outcome(t("review.now_in", folder="Finance"), name="power bill.pdf")
    assert outcome.text == "is now in Finance."  # made while the language was English
    with i18n.using(PT):
        notice = review.outcome_notice(outcome, False)
    assert notice is not None and "agora está em Finance." in notice
    assert "is now in" not in notice


def test_the_undo_confirmation_in_portuguese(downloads: Path, cfg: Config) -> None:
    run_id = fill(downloads, cfg, BILLS)
    with i18n.using(PT):
        text = page(review.undo_page(cfg, "tok", f"run={run_id}", None, gone=False))
    assert "<h1>Desfazer</h1>" in text
    assert f"Desfazer a execução {run_id} devolve 3 arquivos para onde eles estavam" in text
    assert f"Desfazer a execução {run_id}</button>" in text and "Cancelar" in text


def test_an_undo_of_one_file_in_portuguese(downloads: Path, cfg: Config) -> None:
    fill(downloads, cfg, BILLS)
    with Store.open_read_only(cfg.database) as store:
        file_id = next(r.id for r in store.all_files() if r.original_name == "notes.txt")
    with i18n.using(PT):
        text = page(review.undo_page(cfg, "tok", f"file={file_id}", None, gone=False))
    assert "Desfazer a última movimentação deste arquivo o devolve:" in text
    assert "Desfazer esta movimentação" in text


@pytest.mark.parametrize(
    ("code", "title"),
    [(400, "Pedido inválido"), (403, "Acesso negado"), (404, "Página não encontrada")],
)
def test_problem_pages_in_portuguese(code: int, title: str) -> None:
    with i18n.using(PT):
        status, body = views.problem_page(code, t("problem.not_found"), None)
    text = body.decode()
    assert status == code and f"<h1>{title}</h1>" in text
    assert "Esta página não existe. Use o menu acima." in text


def test_problem_pages_keep_their_english_titles() -> None:
    _, body = views.problem_page(404, t("problem.not_found"), None)
    assert "<h1>Not Found</h1>" in body.decode()
    assert views.problem_title(418).text("en") == "Error 418"


# --- flib's core, in the pages' words ----------------------------------------------------


def _search_error(**filters: object) -> str:
    with pytest.raises(SearchError) as caught:
        make_filters(known_categories=["Finance"], **filters)  # type: ignore[arg-type]
    return str(caught.value)


@pytest.mark.parametrize(
    ("filters", "english", "portuguese"),
    [
        ({"min_amount": "abc"}, "At least 'abc' is not an amount", "Valor mínimo: 'abc'"),
        ({"since": "x"}, "Processed from 'x' is not a date like 2026-01-31", "Processado a partir"),
        ({"since": "2026-02-30"}, "Processed from '2026-02-30' is not a valid date", "válida"),
        ({"currency": "dollars"}, "Currency 'dollars' is not a currency code", "Moeda"),
        ({"period": "soon"}, "Period 'soon' is not a year or month", "Período 'soon'"),
        ({"min_amount": "9", "max_amount": "1"}, "At least 9 is more than At most 1", "mínimo"),
        ({"since": "2026-02-01", "until": "2026-01-01"}, "Processed from 2026-02-01 is after", "“"),
        ({"categories": "Nope"}, "Unknown category 'Nope' (known: Finance)", "desconhecida"),
        ({"kinds": "poem"}, "Unknown kind 'poem'", "Tipo desconhecido"),
        ({"extensions": "a/b"}, "Bad extension 'a/b'", "Extensão inválida"),
        ({"issuer": "!!"}, "Issuer '!!' names no organisation", "Emissor"),
    ],
)
def test_search_errors_from_the_core_are_known(
    filters: dict[str, str], english: str, portuguese: str
) -> None:
    raw: dict[str, object] = dict(filters)
    for key in ("categories", "kinds", "extensions"):
        if key in raw:
            raw[key] = [raw[key]]
    text = _search_error(**raw)
    found = core(text)
    assert isinstance(found, Msg), text
    assert found.text("en").startswith(english)
    assert portuguese in found.text(PT)


SAMPLES = {
    # texts as flib's core writes them (search, activity, due, fix, undo, finder_tags)
    "1 file(s) have no up-to-date embedding yet and may be found by words only; the next run "
    "or `flib search --reindex` embeds them": "1 arquivo ainda não tem",
    "semantic search is off (search.semantic.enabled: false); searched by words only": "desligada",
    "no file has an embedding from m yet; run `flib search --reindex`. Searched by words only": (
        "embedding de m"
    ),
    "semantic search unavailable (timeout); searched by words only": "não está disponível",
    "cannot read the index": "Não foi possível ler o índice",
    "the index is from an older flib; the next `flib run` updates it": "versão anterior",
    "the index (schema 99) is newer than this flib; upgrade file-librarian": "esquema 99",
    "This file is not a file flib organized (see `flib search`)": "não foi organizado",
    "This file is missing; only organized or review files can be fixed": "como missing",
    "This file is protected by macOS while the Mac is locked; unlock it and try again": "protegido",
    "This file changed or moved since it was indexed; run `flib reconcile` first": "reconcile",
    "This file is already there with that name": "já está lá",
    "the new name is longer than macOS allows (255 bytes)": "255 bytes",
    "This file is not a file flib moved": "não foi movido",
    "This file has no move to undo": "não tem movimentação",
    "no run has moved anything yet": "Nenhuma execução",
    "run 7 has nothing left to undo": "execução 7",
    "This file is review; only organized or review files have a due": "têm vencimento",
    "This file states no due date, so it is never listed as due": "não informa data",
    "This file is already marked as done": "já está marcado",
    "This file is not marked as done": "não está marcado",
    "no longer there (moved or deleted by hand)": "apagado à mão",
    "moved again since, by run 4 (undo that first)": "execução 4",
    "protected by macOS while the Mac is locked; unlock it and try again": "desbloqueie",
    "Finder tag 'Finance' not set: denied": "Tag do Finder 'Finance' não aplicada: denied",
    "Finder tag 'Finance' not removed: denied": "não removida",
}


@pytest.mark.parametrize(("text", "portuguese"), SAMPLES.items())
def test_core_texts_are_known(text: str, portuguese: str) -> None:
    found = core(text)
    assert isinstance(found, Msg), text
    assert portuguese in found.text(PT)


def test_every_core_pattern_has_a_sample() -> None:
    known = {msg_id for _, msg_id, _ in _KNOWN}
    used = {core(text).msg_id for text in SAMPLES}  # type: ignore[attr-defined]
    searched = {
        "core.search.unknown_category",
        "core.search.bad_extension",
        "core.search.since_after_until",
        "core.search.issuer",
        "core.search.unknown_kind",
        "core.search.min_over_max",
        "core.search.currency",
        "core.search.period",
        "core.search.amount",
        "core.search.date_form",
        "core.search.date_invalid",
    }
    assert known == used | searched


def test_an_unknown_core_text_stays_as_it_is() -> None:
    assert core("something new happened") == "something new happened"
    assert not isinstance(core("something new happened"), Msg)


def test_a_known_core_text_keeps_english_as_the_pages_showed_it() -> None:
    found = core("This file has no move to undo")
    assert found == "This file has no move to undo."
    with i18n.using(PT):
        assert "Este arquivo" in render(found)  # type: ignore[arg-type]


def test_undo_reasons_and_warnings_in_portuguese() -> None:
    with i18n.using(PT):
        reason = actions._undo_reason("original path is taken: /x/y")
        assert t("undo.skipped", why=reason).text() == (
            "ignorado: outro arquivo tem o nome original agora."
        )
        warned = actions.warned("Finder tag 'Finance' not set: denied")
        assert render(warned) == "Tag do Finder &#x27;Finance&#x27; não aplicada: denied."  # type: ignore[arg-type]
    assert actions.warned("Finder tag 'Finance' not set: denied") == (
        "Finder tag 'Finance' not set: denied."
    )
