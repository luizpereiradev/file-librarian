"""``flib init``, ``check-config``, ``reconcile``, ``reprocess`` and the configuration's
refusals in the configured language (#141, part 2).

``flib init`` speaks before any configuration exists: the language of the one it
would replace when it can be read, else the shell's locale (the signal that already
picks Portuguese folders), then the language the user chose. Everything else
follows its configuration; a configuration that cannot be read is refused in English.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import cli, i18n
from file_librarian.config import ConfigError, load_config, parse_config
from file_librarian.db.store import Store
from file_librarian.i18n import t
from tests._helpers import answer, organize, write_config

runner = CliRunner()
MENU_NO = "n\n"


@pytest.fixture(autouse=True)
def _english_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


def flib(*args: str, **kw: Any) -> Any:
    return runner.invoke(cli.app, list(args), **kw)


@pytest.fixture
def pt(tmp_path: Path, downloads: Path) -> Path:
    return write_config(tmp_path, downloads, extra="language: pt-BR\n")


# --- flib init -------------------------------------------------------------------------------


def test_init_with_a_portuguese_locale_speaks_portuguese(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    path = tmp_path / "c.yaml"
    result = flib("init", "--yes", "-c", str(path), "--sample", "0")
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.startswith("Configurando o file-librarian. Aperte Enter para aceitar a sugestão")
    assert "Idioma dos nomes, descrições e pastas (en, pt-BR) [pt-BR]: pt-BR" in out
    assert "Pasta a organizar [~/Downloads]: ~/Downloads" in out
    assert "Para onde vão os arquivos organizados [~/Downloads/Organizado]" in out
    assert f"Configuração gravada em {path} (só você pode lê-la)." in out
    assert "Organizar os arquivos novos automaticamente todo dia às 12:30? [S/n]: s" in out
    assert "Próximos passos:" in out
    assert load_config(path).language == "pt-BR"


def test_init_answers_its_language_question_then_speaks_it(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    path = tmp_path / "c.yaml"
    # The locale suggests pt-BR; the user answers en: the rest is in English.
    answers = "en\n" + "\n\n\n" + "\n" + "n\n"
    result = flib("init", "-c", str(path), "--sample", "0", input=answers + MENU_NO)
    assert result.exit_code == 0, result.output
    assert "Idioma dos nomes, descrições e pastas (en, pt-BR) [pt-BR]: en" in result.stdout
    assert "Folder to organize [~/Downloads]" in result.stdout
    assert "Next:" in result.stdout


def test_init_speaks_the_language_of_the_configuration_it_would_replace(
    pt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = flib("init", "--yes", "-c", str(pt), "--sample", "0")
    assert result.exit_code == 0, result.output
    assert f"Já existe uma configuração em {pt}. Substituir? [s/N]: n" in result.stdout
    assert f"Mantendo {pt}." in result.stdout


def test_init_refuses_folders_in_portuguese(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    path = tmp_path / "c.yaml"
    # An output root that holds the input is refused; the second try takes the defaults.
    answers = "\n\n~\n\n" + "\n\n\n" + "\n" + "n\n"
    result = flib("init", "-c", str(path), "--sample", "0", input=answers + MENU_NO)
    assert result.exit_code == 0, result.output
    assert "erro: configuração inválida:" in result.stderr
    assert "não pode ficar dentro de output_root" in result.stderr
    assert "Vamos tentar as pastas de novo." in result.stderr
    assert load_config(path).output_root == downloads / "Organizado"


# --- the configuration's refusals ---------------------------------------------------------------


def test_the_parser_reasons_in_portuguese_and_english() -> None:
    raw = {
        "provider": {"name": "fake", "model": "x"},
        "limits": {"max_attempts": 0},
        "schedule": {"mode": "hourly", "time": "25:00"},
        "bogus": 1,
        "categories": [],
    }
    with pytest.raises(ConfigError) as caught:
        parse_config(raw)
    english = str(caught.value)
    assert english.startswith("invalid configuration:\n")
    assert "  limits.max_attempts: Input should be greater than 0" in english
    assert "  schedule.time: Value error, schedule.time must be a valid 24-hour time" in english
    assert caught.value.text() == english  # in English, the words never change
    with i18n.using("pt-BR"):
        said = caught.value.text().splitlines()
    assert said[0] == "configuração inválida:"
    assert "  limits.max_attempts: precisa ser maior que 0" in said
    assert "  schedule.time: schedule.time precisa ser um horário válido de 24 horas" in said
    assert "  schedule.mode: precisa ser 'daily', 'watch' ou 'both'" in said
    assert "  bogus: esta chave não existe na configuração" in said
    assert "  categories: a lista precisa ter pelo menos 1 item, não 0" in said


def test_a_refusal_the_catalogue_does_not_know_stays_english() -> None:
    from file_librarian.i18n.core import refusal

    unknown = refusal({"type": "url_parsing", "msg": "Input should be a valid URL"})
    assert unknown.in_language("pt-BR") == "Input should be a valid URL"
    odd = refusal({"type": "greater_than", "msg": "Input should be greater than 1", "ctx": {}})
    assert odd.in_language("pt-BR") == "Input should be greater than 1"
    half = refusal(
        {"type": "greater_than", "msg": "Input should be greater than 0.5", "ctx": {"gt": 0.5}}
    )
    assert half.in_language("pt-BR") == "precisa ser maior que 0,5"
    assert half.in_language("en") == "Input should be greater than 0.5"
    assert str(refusal("not a pydantic error")) == ""


def test_a_missing_configuration_is_said_in_english(tmp_path: Path) -> None:
    result = flib("status", "-c", str(tmp_path / "nowhere.yaml"))
    assert result.exit_code == 1
    assert result.stderr.startswith(f"error: no configuration at {tmp_path / 'nowhere.yaml'}")


# --- flib check-config -------------------------------------------------------------------------


def test_check_config_in_portuguese(pt: Path) -> None:
    result = flib("check-config", "-c", str(pt))
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert f"{pt}: OK" in out
    assert "  language: pt-BR" in out  # the configuration's keys stay as they are written
    assert "subpastas: tratadas como um item só; duplicados: separados" in out
    assert "classificados pela IA" in out  # the Settings page's words (one source)
    assert "totalmente local: sim (classificação, reconhecimento de texto e busca rodam" in out
    assert "  limite de custo diário das execuções automáticas: US$ 3,00" in out


# --- flib reconcile ----------------------------------------------------------------------------


def test_reconcile_in_portuguese(pt: Path, downloads: Path) -> None:
    organize(downloads, load_config(pt), {"notes.txt": (b"notes", answer("Financeiro"))})
    filed = next((downloads / "Organizado").rglob("notes.txt"))
    filed.unlink()
    result = flib("reconcile", "-c", str(pt))
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("  ausente    ")
    assert lines[-1] == (
        "Reconciliação: 1 verificado, nenhum movido à mão, nenhum reencontrado, 1 ausente, "
        "nenhum só no iCloud."
    )


# --- flib reprocess ----------------------------------------------------------------------------


def test_reprocess_in_portuguese(pt: Path, downloads: Path) -> None:
    organize(
        downloads,
        load_config(pt),
        {
            "one.txt": (b"one", answer("Financeiro")),
            "two.txt": (b"two", answer("Financeiro")),
        },
    )
    assert flib("reprocess", "--to-review", "-c", str(pt)).stderr == (
        "erro: --to-review só funciona com --remove-category\n"
    )
    scrub = flib("reprocess", "--scrub-descriptions", "--simulate", "-c", str(pt))
    assert scrub.stdout == "Simulação: nenhum arquivo para limpar; nada mudou.\n"
    result = flib("reprocess", "--yes", "-c", str(pt))
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert (
        "2 arquivos para reclassificar: 2 respondidos pelo cache, nenhum para enviar a fake" in out
    )
    assert "(estimativa: US$ 0,0000; o limite por execução é US$ " in out
    assert "Plano: nenhum seria movido, 2 já no lugar, nenhum mantido como está." in out
    assert out.endswith("Nada para mover: a biblioteca já segue as regras.\n")


def test_reprocess_log_stays_english(pt: Path, downloads: Path) -> None:
    cfg = load_config(pt)
    organize(downloads, cfg, {"one.txt": (b"one", answer("Financeiro"))})
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE files SET category = 'Antiga'")
    result = flib("reprocess", "--remove-category", "Antiga", "--to-review", "--yes", "-c", str(pt))
    assert result.exit_code == 0, result.output
    assert "Reclassificação " in result.stdout
    log = Path(next(x for x in result.stdout.splitlines() if x.startswith("Log: "))[5:])
    text = log.read_text(encoding="utf-8")
    assert "Reprocess run " in text and "Reclassificação" not in text


# --- the review of part 2 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shell", "configured", "folder", "review"),
    [
        ("en_US.UTF-8", "pt-BR", "Organizado", "_Revisar"),
        ("pt_BR.UTF-8", "en", "Organized", "_Review"),
    ],
)
def test_replacing_a_configuration_keeps_its_language(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shell: str,
    configured: str,
    folder: str,
    review: str,
) -> None:
    """A library's folders never flip language because the shell's locale differs."""
    monkeypatch.setenv("LANG", shell)
    path = write_config(tmp_path, downloads, extra=f"language: {configured}\n")
    result = flib("init", "--yes", "--force", "-c", str(path), "--sample", "0")
    assert result.exit_code == 0, result.output
    cfg = load_config(path)
    assert cfg.language == configured
    assert cfg.output_root == downloads / folder
    assert cfg.review_folder == review


def test_the_english_of_a_refusal_never_depends_on_the_language_in_use() -> None:
    raw = {
        "provider": {"name": "fake", "model": "x"},
        "schedule": {"time": "25:00"},
        "inputs": ["relative"],
        "limits": {"max_attempts": 0},
    }
    found: dict[str, str] = {}
    for lang in ("en", "pt-BR"):
        with i18n.using(lang), pytest.raises(ConfigError) as caught:
            parse_config(raw)
        found[lang] = str(caught.value)
        assert all(i18n.english(why) for _, why in caught.value.problems)
    assert found["pt-BR"].encode() == found["en"].encode()
    assert "Value error, schedule.time must be a valid 24-hour time" in found["pt-BR"]


def test_the_trial_summary_in_portuguese() -> None:
    summary = t(
        "term.init.trial_summary",
        organized=t("term.init.to_categories", count=3),
        review=t("term.init.to_review", count=0, folder="_Revisar"),
        stay=t("term.init.stay", count=0),
    )
    assert summary.text("pt-BR") == (
        "Resumo: 3 iriam para as categorias, nenhum para _Revisar (para você decidir), "
        "nenhum ficaria onde está."
    )
    assert (
        summary.text("en")
        == "Summary: 3 to categories, 0 to _Revisar for you to decide, 0 would stay."
    )
