"""The ``flib`` command line.

M0 ships the full command surface with empty commands, so the interface can be
reviewed before the pipeline exists. A command that is not implemented yet
says which milestone and requirement it belongs to and exits with code 2.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from file_librarian import __version__
from file_librarian.config import ConfigError, default_config_path, load_config
from file_librarian.extractors.origin import download_origin
from file_librarian.providers import PLANNED, is_implemented

EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 2

app = typer.Typer(
    name="flib",
    help="Keep your Downloads folder organized. The AI decides; deterministic code moves files.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Config file [default: $FLIB_CONFIG or ~/.config/file-librarian/config.yaml].",
        dir_okay=False,
    ),
]


def _not_implemented(command: str, milestone: str, requirement: str) -> NoReturn:
    typer.echo(
        f"flib {command} is not implemented yet (planned for {milestone}, {requirement}).",
        err=True,
    )
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"flib {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Show the version."
        ),
    ] = False,
) -> None:
    pass


# --- Commands available in M0 -------------------------------------------------


@app.command("check-config")
def check_config(config: ConfigOption = None) -> None:
    """Validate the configuration file without touching any file."""
    path = config or default_config_path()
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_ERROR) from exc
    typer.echo(f"{path}: OK")
    typer.echo(f"  language: {cfg.language}")
    typer.echo(f"  inputs: {', '.join(str(p) for p in cfg.inputs)}")
    typer.echo(f"  output_root: {cfg.output_root}")
    typer.echo(f"  review_folder: {cfg.review_folder}")
    typer.echo(f"  categories: {', '.join(c.name for c in cfg.categories)}")
    typer.echo(f"  provider: {cfg.provider.name} ({cfg.provider.model})")
    if not is_implemented(cfg.provider.name):
        milestone = PLANNED.get(cfg.provider.name, "a later milestone")
        typer.echo(
            f"warning: provider {cfg.provider.name!r} is planned for {milestone}; "
            "classification commands will not run until then.",
            err=True,
        )


@app.command()
def inspect(
    paths: Annotated[list[Path], typer.Argument(exists=True, help="Files to inspect.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Show what flib reads from a file before any AI is involved: hash, size, download origin."""
    results = []
    for path in paths:
        results.append(
            {
                "path": str(path),
                "sha256": _sha256(path) if path.is_file() else None,
                "size_bytes": path.stat().st_size if path.is_file() else None,
                "source_urls": list(download_origin(path)),
            }
        )
    if as_json:
        typer.echo(json.dumps(results, indent=2, ensure_ascii=False))
        return
    for item in results:
        typer.echo(str(item["path"]))
        typer.echo(f"  sha256: {item['sha256'] or '(directory)'}")
        size = item["size_bytes"]
        typer.echo(f"  size:   {size} bytes" if size is not None else "  size:   (directory)")
        urls = item["source_urls"]
        if isinstance(urls, list) and urls:
            for url in urls:
                typer.echo(f"  from:   {url}")
        else:
            typer.echo("  from:   (no download origin recorded)")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- Commands still to come -----------------------------------------------------


@app.command()
def run(config: ConfigOption = None) -> None:
    """Organize the input folders now: classify, rename, move and index new files."""
    _not_implemented("run", "M1", "RF-01..RF-18")


@app.command()
def simulate(config: ConfigOption = None) -> None:
    """Print the full plan without changing anything."""
    _not_implemented("simulate", "M1", "RF-16")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="What you remember about the file.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON for other programs.")] = False,
    config: ConfigOption = None,
) -> None:
    """Find organized files by name, description or tags."""
    _not_implemented("search", "M1", "RF-13, RF-35")


@app.command()
def undo(
    file: Annotated[
        Path | None, typer.Option("--file", help="Undo the last operation on this file.")
    ] = None,
    run_id: Annotated[int | None, typer.Option("--run", help="Undo this run.")] = None,
    config: ConfigOption = None,
) -> None:
    """Revert the last run, a given run, or a single file."""
    _not_implemented("undo", "M1", "RF-17")


@app.command()
def schedule(config: ConfigOption = None) -> None:
    """Install the daily run as a launchd agent and check folder access."""
    _not_implemented("schedule", "M2", "RF-19")


@app.command()
def reconcile(config: ConfigOption = None) -> None:
    """Update the index for files moved or deleted by hand."""
    _not_implemented("reconcile", "M2", "RF-20")


@app.command()
def init(config: ConfigOption = None) -> None:
    """Guided first run on a sample of files copied to a temporary folder."""
    _not_implemented("init", "M2", "RF-38")


@app.command()
def mcp(config: ConfigOption = None) -> None:
    """Start the local MCP server over stdio."""
    _not_implemented("mcp", "M2", "RF-22")


@app.command()
def reprocess(config: ConfigOption = None) -> None:
    """Reclassify files after a rules change."""
    _not_implemented("reprocess", "M3", "RF-37")


@app.command()
def doctor(config: ConfigOption = None) -> None:
    """Collect logs with names and paths redacted, ready to attach to an issue."""
    _not_implemented("doctor", "M3", "RF-39")


def main() -> None:
    if sys.platform != "darwin":
        typer.echo("file-librarian supports macOS only.", err=True)
        raise SystemExit(EXIT_ERROR)
    app()
