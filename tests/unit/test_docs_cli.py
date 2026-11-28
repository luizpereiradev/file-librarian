"""The site's CLI reference (``scripts/docs_cli.py``) matches the command line."""

from __future__ import annotations

from scripts import docs_cli


def test_the_cli_reference_is_up_to_date_in_both_languages() -> None:
    # A changed help text needs its Portuguese translation in scripts/cli_pt_BR.json
    # (``--template`` adds it), then ``uv run python -m scripts.docs_cli``.
    wanted = docs_cli.pages()
    for language, text in wanted.items():
        page = docs_cli.OUTPUT[language]
        assert page.read_text(encoding="utf-8") == text, (
            f"{page} is out of date: run `uv run python -m scripts.docs_cli`"
        )


def test_every_visible_command_and_option_is_documented() -> None:
    found = docs_cli.commands()
    names = {c.name for c in found}
    assert {"run", "search", "due", "ui", "menu", "undo", "fix"} <= names
    assert "check-access" not in names  # hidden
    english = docs_cli.OUTPUT["en"].read_text(encoding="utf-8")
    for command in found:
        assert f"## flib {command.name}" in english
        for param in command.params:
            assert f"`{param.display}`" in english


def test_a_stale_translation_is_reported() -> None:
    found = docs_cli.commands()
    translations = docs_cli.load_translations()
    changed = {k: dict(v) for k, v in translations.items()}
    changed["due"]["en"] = "An older help text."
    del changed["search --kind"]
    changed["gone"] = {"en": "x", "pt": "y"}
    problems = docs_cli.problems(found, changed)
    assert "'due': the English text changed since it was translated" in problems
    assert "'search --kind': no translation" in problems
    assert "'gone': no longer in the CLI" in problems
    assert docs_cli.problems(found, translations) == []


def test_help_text_is_escaped_outside_code() -> None:
    assert docs_cli.prose("Use <file> or `a <b>`.") == "Use &lt;file&gt; or `a <b>`."
    assert docs_cli.prose("one\nline\n\ntwo") == "one line\n\ntwo"
