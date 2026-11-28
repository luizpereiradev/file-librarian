"""The site's CLI reference, generated from the command line itself.

    uv run python -m scripts.docs_cli            # write docs/cli.md and docs/pt-BR/cli.md
    uv run python -m scripts.docs_cli --check    # fail if either is out of date

Every command and option comes from the typer app (``file_librarian.cli.app``):
its help text, its options, their kinds and defaults. Hidden commands and options
are left out. The Brazilian Portuguese page takes the same structure, with each
help text translated in ``scripts/cli_pt_BR.json``, which keeps the English it
was translated from: when a help text changes, ``--check`` (and the test suite)
says which translation to update, and ``--template`` adds the missing ones with
an empty translation to fill in.
"""

from __future__ import annotations

import argparse
import enum
import html
import inspect
import json
import re
import sys
import typing
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

REPO = Path(__file__).resolve().parent.parent
TRANSLATIONS = REPO / "scripts" / "cli_pt_BR.json"
OUTPUT = {"en": REPO / "docs" / "cli.md", "pt-BR": REPO / "docs" / "pt-BR" / "cli.md"}
Language = Literal["en", "pt-BR"]

SEE_ALSO: dict[str, str] = {
    "init": "getting-started.md#first-run-flib-init",
    "check-config": "configuration.md",
    "key": "providers.md#anthropic-anthropic-the-default",
    "simulate": "daily-use.md",
    "run": "daily-use.md",
    "undo": "daily-use.md#activity-and-undo",
    "search": "daily-use.md#search",
    "status": "daily-use.md#what-waits-for-you",
    "fix": "daily-use.md#teaching-it",
    "due": "daily-use.md#what-is-due",
    "reconcile": "daily-use.md#teaching-it",
    "reprocess": "daily-use.md#teaching-it",
    "schedule": "getting-started.md#run-it-every-day",
    "inspect": "privacy.md#what-flib-reads",
    "mcp": "mcp.md",
    "ui": "daily-use.md#the-interface",
    "menu": "getting-started.md#the-menu-bar-item",
    "eval": "providers.md#measuring-quality",
    "doctor": "troubleshooting.md#the-doctor-report",
}
"""Where the rest of the site covers each command (both languages use the same files)."""

ORDER = [
    "init", "check-config", "key", "simulate", "run", "undo", "status", "search", "due",
    "fix", "reconcile", "reprocess", "schedule", "ui", "menu", "inspect", "mcp", "eval",
    "doctor",
]  # fmt: skip
"""The order of the reference: setting up, daily use, then the rest."""

WORDS: dict[Language, dict[str, str]] = {
    "en": {
        "title": "CLI reference",
        "intro": (
            "Every command takes `--config PATH` (`-c`); without it, flib reads "
            "`$FLIB_CONFIG` or `~/.config/file-librarian/config.yaml`. `flib --version` "
            "prints the version, and `flib COMMAND --help` shows what this page shows."
        ),
        "generated": (
            "This page is generated from the command line itself "
            "(`uv run python -m scripts.docs_cli`), so it cannot drift from it."
        ),
        "commands": "Commands",
        "command": "Command",
        "what": "What it does",
        "usage": "Usage",
        "options": "Options",
        "option": "Option",
        "value": "Value",
        "default": "Default",
        "description": "Description",
        "see": "More: [{page}]({link}).",
        "flag": "flag",
        "repeatable": "repeatable",
        "text": "text",
        "integer": "integer",
        "number": "number",
        "path": "path",
        "pair": "two values",
        "one_of": "one of",
        "required": "required",
        "subcommands": "Subcommands",
    },
    "pt-BR": {
        "title": "Referência da linha de comando",
        "intro": (
            "Todo comando aceita `--config PATH` (`-c`); sem ele, o flib lê "
            "`$FLIB_CONFIG` ou `~/.config/file-librarian/config.yaml`. `flib --version` "
            "mostra a versão, e `flib COMANDO --help` mostra o que esta página mostra "
            "(por enquanto, em inglês)."
        ),
        "generated": (
            "Esta página é gerada a partir da própria linha de comando "
            "(`uv run python -m scripts.docs_cli`), com cada texto de ajuda traduzido; "
            "assim ela não fica desatualizada."
        ),
        "commands": "Comandos",
        "command": "Comando",
        "what": "O que faz",
        "usage": "Uso",
        "options": "Opções",
        "option": "Opção",
        "value": "Valor",
        "default": "Padrão",
        "description": "Descrição",
        "see": "Mais: [{page}]({link}).",
        "flag": "flag",
        "repeatable": "pode repetir",
        "text": "texto",
        "integer": "inteiro",
        "number": "número",
        "path": "caminho",
        "pair": "dois valores",
        "one_of": "um de",
        "required": "obrigatório",
        "subcommands": "Subcomandos",
    },
}

PAGE_TITLES: dict[Language, dict[str, str]] = {
    "en": {
        "getting-started.md": "Getting started",
        "daily-use.md": "Daily use",
        "configuration.md": "Configuration",
        "providers.md": "Providers",
        "privacy.md": "Privacy and safety",
        "mcp.md": "Use it from Claude",
        "troubleshooting.md": "Troubleshooting",
    },
    "pt-BR": {
        "getting-started.md": "Primeiros passos",
        "daily-use.md": "Uso diário",
        "configuration.md": "Configuração",
        "providers.md": "Provedores",
        "privacy.md": "Privacidade e segurança",
        "mcp.md": "Use pelo Claude",
        "troubleshooting.md": "Solução de problemas",
    },
}


HEADER = ("option", "value", "default", "description")


class StaleTranslation(Exception):
    """A help text has no Portuguese translation, or changed since it was translated."""


@dataclass(frozen=True, slots=True)
class Param:
    key: str
    """``<command> <first option>`` or ``<command> <ARGUMENT>``: the translation key."""
    display: str
    spec: tuple[str, ...]
    default: str
    help: str
    is_argument: bool
    required: bool


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    """``search``, or ``key set`` for a subcommand."""
    help: str
    params: tuple[Param, ...]
    subcommands: tuple[Command, ...] = ()


def _spec(param: Any) -> tuple[str, ...]:
    """What kind of value an option takes, language-neutral: ``("flag",)``,
    ``("choice", "a", "b")``, ``("list", "text")``, ``("pair",)``, ``("integer",)``..."""
    if not param.opts[0].startswith("-"):
        return ()
    annotation = param.type_descriptor.annotation
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if getattr(param, "is_flag", False):
        return ("flag",)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return ("choice", *(str(m.value) for m in annotation))
    if origin is list:
        return ("list", _scalar(args[0] if args else str))
    if origin is tuple:
        return ("pair",)
    return (_scalar(annotation),)


def _scalar(annotation: Any) -> str:
    return {int: "integer", float: "number", Path: "path"}.get(annotation, "text")


def kind(spec: tuple[str, ...], words: dict[str, str]) -> str:
    if not spec:
        return ""
    if spec[0] == "choice":
        return f"{words['one_of']} " + ", ".join(f"`{v}`" for v in spec[1:])
    if spec[0] == "list":
        return f"{words[spec[1]]}, {words['repeatable']}"
    return words[spec[0]]


def _default(param: Any) -> str:
    default = param.default
    if default is None or default is False or getattr(param, "is_flag", False):
        return ""
    if isinstance(default, enum.Enum):
        default = default.value
    if isinstance(default, (list, tuple)):
        return ", ".join(f"`{v}`" for v in default) if default else ""
    return f"`{default}`"


def _params(command_name: str, command: Any) -> tuple[Param, ...]:
    found = []
    for param in command.params:
        if param.hidden or param.name == "config":
            continue
        is_argument = not param.opts[0].startswith("-")
        if is_argument:
            display = param.name.upper() + ("..." if param.nargs == -1 else "")
            key = f"{command_name} {param.name.upper()}"
        else:
            names = [*param.opts, *param.secondary_opts]
            display = " / ".join(names)
            key = f"{command_name} {param.opts[0]}"
        found.append(
            Param(
                key,
                display,
                _spec(param),
                _default(param),
                inspect.cleandoc(param.help or ""),
                is_argument,
                bool(param.required),
            )
        )
    return tuple(found)


def commands() -> list[Command]:
    """Every visible command of the app, in ``ORDER``."""
    import typer.main

    from file_librarian.cli import app

    root = typer.main.get_command(app)
    subs: dict[str, Any] = root.commands  # type: ignore[attr-defined]
    missing = sorted(n for n, c in subs.items() if not c.hidden and n not in ORDER)
    if missing:
        raise SystemExit(f"commands missing from ORDER in scripts/docs_cli.py: {missing}")
    out = []
    for name in ORDER:
        command = subs[name]
        children = getattr(command, "commands", None) or {}
        out.append(
            Command(
                name,
                inspect.cleandoc(command.help or ""),
                _params(name, command),
                tuple(
                    Command(
                        f"{name} {child_name}",
                        inspect.cleandoc(child.help or ""),
                        _params(f"{name} {child_name}", child),
                    )
                    for child_name, child in children.items()
                    if not child.hidden
                ),
            )
        )
    return out


def _all(found: list[Command]) -> Iterator[Command]:
    for command in found:
        yield command
        yield from command.subcommands


def source_texts(found: list[Command]) -> dict[str, str]:
    """Every help text the reference shows, by translation key."""
    texts: dict[str, str] = {}
    for command in _all(found):
        texts[command.name] = command.help
        for param in command.params:
            if param.help:
                texts[param.key] = param.help
    return texts


def load_translations() -> dict[str, dict[str, str]]:
    if not TRANSLATIONS.exists():
        return {}
    data: dict[str, dict[str, str]] = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
    return data


def problems(found: list[Command], translations: dict[str, dict[str, str]]) -> list[str]:
    """What is missing or stale in ``cli_pt_BR.json``."""
    out = []
    texts = source_texts(found)
    for key, english in texts.items():
        entry = translations.get(key)
        if entry is None or not entry.get("pt"):
            out.append(f"{key!r}: no translation")
        elif entry.get("en") != english:
            out.append(f"{key!r}: the English text changed since it was translated")
    out += [f"{key!r}: no longer in the CLI" for key in translations if key not in texts]
    return out


_CODE = re.compile(r"(`[^`]*`)")


def prose(text: str) -> str:
    """Help text as Markdown: ``<`` and ``>`` escaped outside code spans, and each
    line that starts with an option kept on a line of its own."""
    parts = _CODE.split(text)
    escaped = "".join(p if p.startswith("`") else html.escape(p, quote=False) for p in parts)
    paragraphs = [p.strip() for p in escaped.split("\n\n")]
    return "\n\n".join(p.replace("\n", " ") for p in paragraphs if p)


def _cell(text: str) -> str:
    return prose(text).replace("\n\n", "<br>").replace("|", "\\|")


def _first(text: str) -> str:
    return prose(text).split("\n\n", 1)[0]


def render(
    found: list[Command], language: Language, translations: dict[str, dict[str, str]]
) -> str:
    words = WORDS[language]

    def say(key: str, english: str) -> str:
        if language == "en" or not english:
            return english
        return translations[key]["pt"]

    lines = [
        "<!-- Generated by scripts/docs_cli.py from the typer app: do not edit by hand. -->",
        "",
        f"# {words['title']}",
        "",
        words["intro"],
        "",
        f"*{words['generated']}*",
        "",
        f"## {words['commands']}",
        "",
        f"| {words['command']} | {words['what']} |",
        "| --- | --- |",
    ]
    for command in found:
        anchor = "flib-" + command.name.replace(" ", "-")
        lines.append(
            f"| [`flib {command.name}`](#{anchor}) | {_first(say(command.name, command.help))} |"
        )
    for command in found:
        lines += _section(command, say, words, language, level=2)
    return "\n".join(lines).rstrip() + "\n"


def _usage(command: Command) -> str:
    args = " ".join(
        (p.display if p.required else f"[{p.display}]") for p in command.params if p.is_argument
    )
    if command.subcommands:
        return f"flib {command.name} COMMAND [OPTIONS]"
    return " ".join(part for part in (f"flib {command.name}", "[OPTIONS]", args) if part)


def _section(
    command: Command, say: Any, words: dict[str, str], language: Language, *, level: int
) -> list[str]:
    hashes = "#" * level
    lines = ["", f"{hashes} flib {command.name}", "", prose(say(command.name, command.help)), ""]
    lines += [f"```text\n{_usage(command)}\n```", ""]
    shown = list(command.params)
    if shown:
        lines += [
            "| " + " | ".join(words[w] for w in HEADER) + " |",
            "| --- | --- | --- | --- |",
        ]
        for param in shown:
            value = kind(param.spec, words)
            text = _cell(say(param.key, param.help))
            lines.append(f"| `{param.display}` | {value} | {param.default} | {text} |")
        lines.append("")
    top = command.name.split(" ", 1)[0]
    if level == 2 and top in SEE_ALSO:
        link = SEE_ALSO[top]
        page = PAGE_TITLES[language][link.split("#", 1)[0]]
        lines += [words["see"].format(page=page, link=link), ""]
    for child in command.subcommands:
        lines += _section(child, say, words, language, level=level + 1)
    return lines


def pages() -> dict[Language, str]:
    """Both pages, as they should be; ``StaleTranslation`` if a translation is missing."""
    found = commands()
    translations = load_translations()
    trouble = problems(found, translations)
    if trouble:
        raise StaleTranslation(
            "scripts/cli_pt_BR.json needs updating "
            "(uv run python -m scripts.docs_cli --template, then translate):\n  "
            + "\n  ".join(trouble)
        )
    return {language: render(found, language, translations) for language in ("en", "pt-BR")}


def write_template() -> int:
    """Add every missing or stale help text to ``cli_pt_BR.json`` with an empty
    translation, and drop the ones no longer in the CLI."""
    texts = source_texts(commands())
    old = load_translations()
    new = {}
    for key, english in texts.items():
        entry = old.get(key, {})
        current = entry.get("en") == english and entry.get("pt")
        new[key] = {"en": english, "pt": entry.get("pt", "") if current else ""}
    TRANSLATIONS.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    empty = sum(1 for v in new.values() if not v["pt"])
    print(f"{TRANSLATIONS.relative_to(REPO)}: {len(new)} texts, {empty} to translate")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.docs_cli", description=__doc__.split("\n")[0]
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="fail if a page is out of date")
    group.add_argument("--template", action="store_true", help="add missing translations")
    args = parser.parse_args(argv)
    if args.template:
        return write_template()
    try:
        wanted = pages()
    except StaleTranslation as exc:
        print(exc, file=sys.stderr)
        return 1
    stale = [
        language for language, text in wanted.items()
        if not OUTPUT[language].exists() or OUTPUT[language].read_text(encoding="utf-8") != text
    ]  # fmt: skip
    if args.check:
        if stale:
            print(f"out of date: {', '.join(str(OUTPUT[s].relative_to(REPO)) for s in stale)}; "
                  "run `uv run python -m scripts.docs_cli`", file=sys.stderr)  # fmt: skip
            return 1
        return 0
    for language, text in wanted.items():
        OUTPUT[language].parent.mkdir(parents=True, exist_ok=True)
        OUTPUT[language].write_text(text, encoding="utf-8")
        print(f"wrote {OUTPUT[language].relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
