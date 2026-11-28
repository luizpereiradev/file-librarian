"""The terminal samples on the site, taken from the demo library.

    uv run python -m scripts.docs_samples

Builds the demo library (``scripts/demo.py``) in each language in a temporary
folder, runs a few read-only commands on it in this process, as the demo's
sandbox allows (no real tool, ``HOME`` inside the demo), and writes their output
to ``docs/snippets/<language>/<name>.txt``, which the pages include. The demo's
home folder is shown as ``/Users/you``. Run it again whenever the command line's
output changes; the dates follow the day it runs.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from scripts import demo

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "snippets"
HOME_SHOWN = "/Users/you"

SAMPLES: dict[str, list[str]] = {
    "status": ["status"],
    "due": ["due"],
    "search": ["search", "{query}", "--limit", "2"],
    "search-sum": ["search", "--kind", "bill,invoice", "--sum", "--by", "issuer"],
    "simulate": ["simulate"],
}
QUERY = {"en": "invoice", "pt-BR": "fatura"}


def run(built: demo.Demo, args: list[str]) -> str:
    from typer.testing import CliRunner

    from file_librarian.cli import app

    runner = CliRunner()
    with demo.sandboxed(built.home):
        result = runner.invoke(
            app,
            [*args, "-c", str(built.config_path)],
            env={"COLUMNS": "100"},
            catch_exceptions=False,
        )
    if result.exit_code != 0:
        raise SystemExit(f"flib {' '.join(args)} failed: {result.output}")
    text = result.stdout
    for home in {str(built.home), os.path.realpath(built.home)}:
        text = text.replace(home, HOME_SHOWN)
    return text.rstrip() + "\n"


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="flib-samples-"))
    try:
        for language in ("en", "pt-BR"):
            built = demo.build(work / language, language, home=work / "not-home")
            folder = OUT / language
            folder.mkdir(parents=True, exist_ok=True)
            for name, args in SAMPLES.items():
                args = [a.format(query=QUERY[language]) for a in args]
                (folder / f"{name}.txt").write_text(run(built, args), encoding="utf-8")
                print(f"wrote {(folder / f'{name}.txt').relative_to(REPO)}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
