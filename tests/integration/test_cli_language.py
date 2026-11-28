"""The command line and the notifications in the configuration's language (#141).

Each command once in Brazilian Portuguese: its key lines, a plural at 0, 1 and 2,
a date and an amount, a table that stays aligned with accented words. What stays
English whatever the language: ``--help``, ``--json``, the run's log, what a run
started by launchd prints, ``flib doctor``, the MCP server, and the error of a
configuration that cannot be read. The rest of the suite runs in English.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import cli, due, i18n
from file_librarian import status as st
from file_librarian.config import Config, load_config
from file_librarian.db.store import Store
from file_librarian.doctor import Options, build_report
from file_librarian.i18n import formats, t
from file_librarian.models import Classification
from tests._helpers import answer, organize, put, write_config

runner = CliRunner()

TODAY = date(2026, 11, 20)
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def doc(issuer: str, due_in: int, amount: str, currency: str, **kw: Any) -> Classification:
    return answer(
        "Financeiro",
        kind=kw.pop("kind", "bill"),
        issuer=issuer,
        amount=amount,
        currency=currency,
        due_date=(TODAY + timedelta(days=due_in)).isoformat(),
        **kw,
    )


Files = dict[str, tuple[bytes, Classification | str]]
FILES: Files = {
    "power-bill.txt": (b"power", doc("Northwind Power", 3, "1234.56", "BRL")),
    "water-bill.txt": (b"water", doc("Água & Saneamento", -2, "80.06", "BRL")),
    "phone-bill.txt": (b"phone", doc("Fabrikam Mobile", 5, "49.90", "USD")),
    "garden-notes.txt": (b"garden", "Financeiro"),
}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(due, "clock", lambda: NOON)


def flib(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


@pytest.fixture
def pt(tmp_path: Path, downloads: Path) -> Path:
    """A configuration in Brazilian Portuguese with nothing filed yet."""
    return write_config(tmp_path, downloads, extra="language: pt-BR\n")


@pytest.fixture
def filed(pt: Path, downloads: Path) -> Path:
    """Bills and a note filed by a real run, in the Portuguese configuration."""
    organize(downloads, load_config(pt), FILES)
    return pt


def lines(result: Any) -> list[str]:
    assert result.exit_code == 0, result.output
    out: str = result.stdout
    return out.splitlines()


# --- one rule for the language -----------------------------------------------------------


def test_the_language_comes_from_the_configuration_only(cfg: Config) -> None:
    assert i18n.language_of(cfg) == "en"
    assert i18n.language_of(cfg.model_copy(update={"language": "pt-BR"})) == "pt-BR"
    assert i18n.language_of(None) == "en"  # no configuration could be read
    assert i18n.language_of(object()) == "en"


def test_a_command_speaks_its_configuration_and_the_next_one_starts_in_english(
    filed: Path, tmp_path: Path, downloads: Path
) -> None:
    assert "Revisão:" in flib("status", "-c", str(filed)).stdout
    english = write_config(tmp_path, downloads)
    assert "Review:" in flib("status", "-c", str(english)).stdout
    assert i18n.current() == "en"


def test_a_broken_configuration_is_refused_in_english(tmp_path: Path) -> None:
    broken = tmp_path / "broken.yaml"
    broken.write_text("language: pt-BR\nlimits: {max_files_per_run: nope}\n", encoding="utf-8")
    result = flib("status", "-c", str(broken))
    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")


def test_help_stays_english(pt: Path) -> None:
    result = flib("status", "--help", "-c", str(pt))
    assert result.exit_code == 0
    assert "Show what waits for you" in result.stdout


# --- flib status ---------------------------------------------------------------------------


def test_status_in_portuguese(filed: Path) -> None:
    shown = lines(flib("status", "-c", str(filed)))
    assert shown[0] == "Revisão: nada espera em _Revisar."
    assert "Vencimentos: 2 documentos nos próximos 7 dias, 1 vencido (`flib due`)." in shown
    last = next(line for line in shown if line.startswith("Última execução: "))
    # the Status page's words for the same run (one source): trigger and counts
    assert ", manual: 4 organizados, nenhum para revisar, nenhum com falha; US$ 0,0000" in last
    when = last.removeprefix("Última execução: ").split(",")[0]
    assert datetime.strptime(when, "%d/%m/%Y %H:%M")  # 25/11/2026 22:48
    assert "Próxima execução: nenhuma agendada (`flib schedule` agenda uma execução diária)." in (
        shown
    )
    assert shown[-1] == "Este mês: US$ 0,0000"


def test_status_json_stays_english(filed: Path) -> None:
    data = json.loads(flib("status", "--json", "-c", str(filed)).stdout)
    assert data["last_run"]["trigger"] == "manual"
    assert data["summary"]["notifier"] is None or data["summary"]["notifier"]["reason"] in (
        "built and current",
        "the notifier app is not built; `flib schedule --refresh` builds it",
    )


def test_status_review_groups_and_plurals(pt: Path, downloads: Path) -> None:
    unsure: Files = {
        "unsure-one.txt": (b"one", answer("Financeiro", confidence=0.3)),
        "unsure-two.txt": (b"two", answer("Financeiro", confidence=0.2)),
    }
    organize(downloads, load_config(pt), unsure)
    shown = lines(flib("status", "-c", str(pt)))
    assert shown[0].startswith("Revisão: 2 arquivos em _Revisar, o mais antigo há menos de um dia")
    assert "    2  a IA não teve certeza suficiente; ela sugeriu Financeiro" in shown


@pytest.mark.parametrize(
    ("msg_id", "texts"),
    [
        ("term.files", ("nenhum arquivo", "1 arquivo", "2 arquivos")),
        ("term.documents", ("nenhum documento", "1 documento", "2 documentos")),
        (
            "notify.review",
            (
                "nenhum arquivo espera revisão",
                "1 arquivo espera revisão",
                "2 arquivos esperam revisão",
            ),
        ),
        (
            "term.run.sensitive",
            (
                "nenhum sensível deixado no lugar",
                "1 sensível deixado no lugar",
                "2 sensíveis deixados no lugar",
            ),
        ),
        (
            "term.recovered",
            (
                "Nenhuma movimentação para recuperar.",
                "Recuperada 1 movimentação interrompida por uma falha anterior.",
                "Recuperadas 2 movimentações interrompidas por uma falha anterior.",
            ),
        ),
    ],
)
def test_zero_one_two(msg_id: str, texts: tuple[str, str, str]) -> None:
    assert tuple(t(msg_id, count=n).text("pt-BR") for n in (0, 1, 2)) == texts


# --- flib run and flib simulate -------------------------------------------------------------


def test_run_in_portuguese_logs_in_english(pt: Path, downloads: Path) -> None:
    put(downloads, "id_rsa", b"key material")
    organize(downloads, load_config(pt), {})  # nothing to classify: the key stays
    for name in ("first.txt", "second.txt"):
        put(downloads, name, name.encode())
    result = flib("run", "-c", str(pt))
    shown = lines(result)
    assert "  movido     first.txt  ->  Financeiro/first.txt" in shown or any(
        line.startswith("  movido     first.txt  ->  ") for line in shown
    )
    summary = next(line for line in shown if line.startswith("Execução "))
    assert summary.endswith(
        ": 2 organizados, nenhum para revisar, nenhum duplicado separado, "
        "nenhum sensível deixado no lugar, nenhum com falha, nenhum ignorado."
    )
    log = Path(next(line for line in shown if line.startswith("Log: ")).removeprefix("Log: "))
    text = log.read_text(encoding="utf-8")
    assert "  moved   first.txt  ->  " in text
    assert ": 2 organized, 0 to review, 0 duplicates set aside" in text
    assert "Execução" not in text and "movido" not in text


def test_simulate_in_portuguese(pt: Path, downloads: Path) -> None:
    put(downloads, "only.txt", b"only")
    shown = lines(flib("simulate", "-c", str(pt)))
    assert shown[0].startswith("  mover      only.txt  ->  ")
    assert shown[0].endswith("(categoria Outros, nome original mantido)")
    assert shown[-1] == "Simulação: 1 seria movido, nenhum ficaria. Nada mudou."


def test_the_rows_of_a_run_line_up_in_each_language() -> None:
    from file_librarian.i18n.messages import terminal

    widths: tuple[tuple[i18n.Lang, int], ...] = (
        ("en", 2 + terminal.EN_TAG),
        ("pt-BR", 2 + terminal.PT_TAG),
    )
    for lang, width in widths:
        for msg_id in terminal.ROW_IDS:
            template = i18n.template(lang, msg_id, {})
            prefix = template[: template.index("{")]
            assert formats.width(prefix) >= width, (lang, msg_id)
            if lang == "pt-BR" or len(prefix.strip()) < terminal.EN_TAG:
                assert formats.width(prefix) == width, (lang, msg_id)
        for msg_id in terminal.UNDO_ROW_IDS:
            template = i18n.template(lang, msg_id, {})
            assert formats.width(template[: template.index("{")]) == 2 + terminal.UNDO_TAG


def test_a_scheduled_run_prints_english_and_notifies_in_portuguese(
    pt: Path, downloads: Path, fake_osascript: Path
) -> None:
    cfg = load_config(pt)
    unsure: Files = {"unsure.txt": (b"unsure", answer("Financeiro", confidence=0.3))}
    organize(downloads, cfg, unsure)
    with Store.open(cfg.database) as store:
        old = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="seconds")
        store.conn.execute("UPDATE files SET processed_at = ?", (old,))
    put(downloads, "later.txt", b"later")
    result = flib("run", "--trigger", "schedule", "-c", str(pt))
    assert result.exit_code == 0, result.output
    assert "Summary: 1 file waits for review. Run `flib status` for details." in result.stdout
    assert "Execução" not in result.stdout  # it goes to schedule.out.log
    posted = [json.loads(line) for line in fake_osascript.read_text().splitlines()]
    text = posted[-1][posted[-1].index("--") + 1]
    assert text == "1 arquivo espera revisão. Rode `flib status` para ver os detalhes."


def test_the_summary_follows_the_configuration_not_the_environment(
    pt: Path, downloads: Path, fake_osascript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "en_US.UTF-8")  # launchd's environment says nothing
    cfg = load_config(pt)
    organize(downloads, cfg, {"a.txt": (b"a", answer(confidence=0.3))})
    lines_ = st.after_scheduled_run(cfg, now=datetime.now(UTC) + timedelta(days=10))
    assert lines_ == ["Summary: 1 file waits for review. Run `flib status` for details."]
    posted = [json.loads(line) for line in fake_osascript.read_text().splitlines()]
    assert posted[-1][-2:] == [
        "1 arquivo espera revisão. Rode `flib status` para ver os detalhes.",
        "file-librarian",
    ]


def test_a_due_reminder_in_portuguese() -> None:
    reminder = due.Reminder(soon=1, overdue=2, new=[])
    text = st.summary_text(0, 0, due=reminder.text(3))
    assert text.text("pt-BR") == (
        "1 documento vence em até 3 dias, 2 documentos vencidos. "
        "Rode `flib status` para ver os detalhes."
    )
    assert text.text("en") == (
        "1 document is due within 3 days, 2 documents overdue. Run `flib status` for details."
    )
    today = due.Reminder(soon=2, overdue=0, new=[]).text(0)
    assert today is not None and today.text("pt-BR") == "2 documentos vencem hoje"


# --- flib due --------------------------------------------------------------------------------


def test_due_in_portuguese(filed: Path) -> None:
    shown = lines(flib("due", "-c", str(filed)))
    assert shown[0] == "Vencimentos de 21/10/2026 até 27/11/2026 (hoje é 20/11/2026):"
    assert shown[1] == "  18/11/2026  (há 2 dias, vencido)  R$ 80,06  Água & Saneamento  conta"
    assert shown[3] == "  23/11/2026  (em 3 dias)  R$ 1.234,56  Northwind Power  conta"
    assert shown[-1] == "Total em aberto: R$ 1.314,62 (2 documentos); US$ 49,90 (1 documento)"


def test_due_refusals_in_portuguese(filed: Path) -> None:
    result = flib("due", "--within", "3x", "-c", str(filed))
    assert result.exit_code == 1
    assert result.stderr == "erro: --within '3x' não é um intervalo como 7d, 2w ou 1m\n"


def test_due_json_stays_english(filed: Path) -> None:
    data = json.loads(flib("due", "--json", "-c", str(filed)).stdout)
    assert {item["relative"] for item in data["items"]} >= {"in 3 days", "2 days ago"}


# --- flib search -----------------------------------------------------------------------------


def test_search_listing_in_portuguese(filed: Path) -> None:
    shown = lines(flib("search", "power", "-c", str(filed)))
    assert shown[1] == "  [Financeiro] antes: power-bill.txt"
    assert shown[2] == "  conta · Northwind Power · R$ 1.234,56 · vence em 23/11/2026"


def test_sum_by_issuer_stays_aligned_with_accents(filed: Path) -> None:
    shown = lines(flib("search", "--sum", "--by", "issuer", "-c", str(filed)))
    assert shown[0] == "Totais por emissor:"
    rows = shown[1:4]
    assert any("Água & Saneamento" in row for row in rows)
    # the amounts end in the same column, whatever the accents before them
    ends = {formats.width(row[: row.index("  (")]) for row in rows}
    assert len(ends) == 1, rows
    starts = {formats.width(row[: row.index("R$") if "R$" in row else row.index("US$")])
              for row in rows if "R$ 1" in row or "US$" in row}  # fmt: skip
    assert starts
    assert shown[-2] == "Total: R$ 1.314,62 (2 arquivos); US$ 49,90 (1 arquivo)"
    assert shown[-1] == "1 arquivo encontrado não informa o valor; não entra na conta."


def test_sum_json_and_search_json_stay_english(filed: Path) -> None:
    data = json.loads(flib("search", "--sum", "--json", "-c", str(filed)).stdout)
    assert data["totals"][0]["currency"] in ("BRL", "USD")
    hits = json.loads(flib("search", "power", "--json", "-c", str(filed)).stdout)
    assert hits[0]["kind"] == "bill"


def test_search_refusals_in_portuguese(filed: Path) -> None:
    result = flib("search", "--since", "2026-13-01", "-c", str(filed))
    assert result.stderr == "erro: --since '2026-13-01' não é uma data válida\n"
    result = flib("search", "-c", str(filed))
    assert result.stderr.startswith("erro: diga palavras para buscar, um filtro, ou os dois.")
    assert flib("search", "--by", "kind", "-c", str(filed)).stderr == (
        "erro: --by só funciona com --sum\n"
    )


# --- flib fix and flib undo ------------------------------------------------------------------


def test_fix_done_and_undo_in_portuguese(filed: Path, downloads: Path) -> None:
    power = next((downloads / "Organizado").rglob("power-bill.txt"))
    shown = lines(flib("fix", str(power), "--done", "-c", str(filed)))
    assert shown[0].startswith("  marcado como concluído  ")
    assert shown[0].endswith("(vence em 23/11/2026)")
    assert shown[1].startswith("Registrado na execução ") and shown[1].endswith(
        "; `flib undo` desfaz."
    )
    again = flib("fix", str(power), "--done", "-c", str(filed))
    assert again.stderr == f"erro: {power} já está marcado como concluído\n"
    undone = lines(flib("undo", "-c", str(filed)))
    assert undone == [
        "Desfeito: nenhum devolvido, nenhum ignorado, 1 alteração no índice desfeita."
    ]


def test_fix_and_undo_of_a_file(filed: Path, downloads: Path) -> None:
    notes = next((downloads / "Organizado").rglob("garden-notes.txt"))
    shown = lines(flib("fix", str(notes), "--name", "garden.txt", "-c", str(filed)))
    assert shown[0].startswith("  movido     ")
    assert "  nome aprendido: garden-notes.txt -> garden.txt" in shown
    undone = lines(flib("undo", "--file", str(notes.with_name("garden.txt")), "-c", str(filed)))
    assert undone[0].startswith("  devolvido ")
    assert undone[-1] == "Desfeito: 1 devolvido, nenhum ignorado."
    refused = flib("fix", str(notes), "-c", str(filed))
    assert refused.stderr == "erro: diga o que mudar: uma categoria, um nome, ou os dois\n"


# --- flib schedule, flib menu, flib key, flib ui ---------------------------------------------


def test_schedule_and_menu_status_in_portuguese(pt: Path) -> None:
    assert flib("schedule", "--status", "-c", str(pt)).stdout == (
        "Nenhuma execução diária agendada. Rode `flib schedule` para agendar uma.\n"
    )
    assert flib("schedule", "--mode", "hourly", "-c", str(pt)).stderr == (
        "erro: --mode precisa ser daily, watch ou both\n"
    )
    assert flib("menu", "--status", "-c", str(pt)).stdout == (
        "O item da barra de menus não está instalado. "
        "`flib menu` coloca o flib na barra de menus.\n"
    )


def test_schedule_in_portuguese(pt: Path) -> None:
    result = flib("schedule", "-c", str(pt), "--no-check", "--at", "09:30")
    assert result.exit_code == 0, result.output
    assert "Execução diária agendada para 09:30 (" in result.stdout
    assert "Se o Mac estiver dormindo nessa hora, ela roda quando ele acordar." in result.stdout
    assert "`flib schedule --remove` desliga o agendamento." in result.stdout
    shown = flib("schedule", "--status", "-c", str(pt)).stdout
    assert "Agendamento diário às 09:30: ativo" in shown
    status = flib("status", "-c", str(pt)).stdout
    assert "(todo dia às 09:30)" in status
    assert flib("schedule", "--remove", "-c", str(pt)).stdout == "Agendamento removido.\n"


def test_key_status_in_portuguese(pt: Path) -> None:
    assert flib("key", "status", "-c", str(pt)).stdout == (
        "O provedor fake não usa chave de API.\n"
    )
    result = flib("key", "delete", "--provider", "fake", "-c", str(pt))
    assert result.stderr == (
        "erro: --provider precisa ser anthropic, openai-compat ou embeddings; fake não usa chave\n"
    )


def test_ui_without_a_browser_in_portuguese(
    pt: Path, loopback_network: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from file_librarian.ui import server

    said: list[str] = []

    def once(self: server.Ui, timeout: float) -> str | None:
        self.stop("signal")
        return "signal"

    monkeypatch.setattr(server.Ui, "wait", once)
    result = flib("ui", "--no-browser", "-c", str(pt))
    said = result.stdout.splitlines()
    assert result.exit_code == 0, result.output
    assert any(line.startswith("O link funciona uma vez; guarde-o só para você.") for line in said)
    assert said[-1] == "flib ui terminou: interrompido."


# --- what stays English ----------------------------------------------------------------------


def test_doctor_stays_english(filed: Path) -> None:
    with i18n.using("pt-BR"):
        report = build_report(Options(config_path=filed))
    assert "Review: nothing waits in _Revisar." in report
    assert "Revisão" not in report


def test_the_mcp_server_starts_in_english(pt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp")
    from file_librarian import mcp_server

    seen: list[str] = []

    class Server:
        def run(self, transport: str) -> None:
            seen.append(i18n.current())

    monkeypatch.setattr(mcp_server, "build_server", lambda config: Server())
    assert flib("mcp", "-c", str(pt)).exit_code == 0
    assert seen == ["en"]


def test_a_core_message_is_matched_by_its_english_on_the_pages() -> None:
    from file_librarian.i18n.core import core, detail

    with i18n.using("pt-BR"):
        refused = t("term.issuer_none", value="'x'")
        assert str(refused) == "--issuer 'x' não identifica nenhuma organização"
        found = core(refused)
        assert isinstance(found, i18n.Msg)
        assert found.text("en") == "Issuer 'x' names no organisation."
        assert detail("category Bills, original name kept") == (
            "categoria Bills, nome original mantido"
        )
        assert detail("confidence 0.42 < 0.70") == "confiança 0,42 < 0,70"
        assert detail("something flib never wrote") == "something flib never wrote"


@pytest.mark.parametrize(
    ("typed", "default", "said"),
    [("s\n", False, True), ("sim\n", False, True), ("não\n", True, False),
     ("\n", True, True), ("\n", False, False), ("talvez\nn\n", True, False)],
)  # fmt: skip
def test_a_yes_no_question_in_portuguese(typed: str, default: bool, said: bool) -> None:
    with i18n.using("pt-BR"), runner.isolation(input=typed) as (out, err, *_):
        assert cli._confirm("Salvar?", default=default) is said
        shown, refused = out.getvalue().decode(), err.getvalue().decode()
    assert ("Salvar? [S/n]" if default else "Salvar? [s/N]") in shown
    assert ("responda s ou n" in refused) is typed.startswith("talvez")
