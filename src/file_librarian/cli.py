"""The ``flib`` command line.

Each command loads and validates the configuration, then calls one module:
``pipeline`` for ``run``/``simulate``, ``undo``, ``search``. Commands of later
milestones exist already and exit with code 2, naming their milestone and
requirement, so the interface can be reviewed before they land.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from file_librarian import __version__, credentials, keychain, pipeline
from file_librarian.config import Config, ConfigError, default_config_path, load_config
from file_librarian.db.store import Store
from file_librarian.executor import RunReport, recover
from file_librarian.extractors.origin import download_origin
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.lock import AlreadyRunningError, instance_lock
from file_librarian.models import FileStatus
from file_librarian.planner import Plan
from file_librarian.providers import PLANNED, ProviderError, get_provider, is_implemented
from file_librarian.runlog import write_log
from file_librarian.scanner import ScanError, SkipReason
from file_librarian.search import search as run_search
from file_librarian.search import to_json as search_json
from file_librarian.undo import NothingToUndoError, undo_file, undo_run

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


# --- Inspection ------------------------------------------------------------------


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
    if cfg.provider.name == "anthropic":
        found = credentials.resolve(cfg.provider)
        typer.echo(
            f"  api key: {found.source} ({found.hint})"
            if found.value
            else "  api key: not found (run `flib key set`, or set ANTHROPIC_API_KEY)"
        )
    env_file = cfg.provider.env_file
    if env_file is not None and env_file.exists() and not credentials.env_file_is_private(env_file):
        typer.echo(f"warning: {env_file} is readable by other users; run chmod 600 on it", err=True)
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
        if path.is_dir():
            manifest = unit_manifest(path)
            digest, size, kind = manifest.digest, manifest.size_bytes, "folder unit"
        else:
            digest, size, kind = file_sha256(path), path.stat().st_size, "file"
        results.append(
            {
                "path": str(path),
                "kind": kind,
                "sha256": digest,
                "size_bytes": size,
                "source_urls": list(download_origin(path)),
            }
        )
    if as_json:
        typer.echo(json.dumps(results, indent=2, ensure_ascii=False))
        return
    for item in results:
        typer.echo(str(item["path"]))
        typer.echo(f"  kind:   {item['kind']}")
        typer.echo(f"  sha256: {item['sha256']}")
        typer.echo(f"  size:   {item['size_bytes']} bytes")
        urls = item["source_urls"]
        if isinstance(urls, list) and urls:
            for url in urls:
                typer.echo(f"  from:   {url}")
        else:
            typer.echo("  from:   (no download origin recorded)")


# --- API key ---------------------------------------------------------------------------

key_app = typer.Typer(
    help="Store the Anthropic API key in the macOS Keychain.", no_args_is_help=True
)
app.add_typer(key_app, name="key")


@key_app.command("set")
def key_set() -> None:
    """Save the API key in the Keychain (asked for, never shown)."""
    value = typer.prompt("Anthropic API key", hide_input=True).strip()
    if not value.startswith("sk-ant-"):
        typer.echo("warning: Anthropic keys usually start with sk-ant-", err=True)
    try:
        keychain.write(value)
    except (keychain.KeychainError, ValueError) as exc:
        _fatal(str(exc))
    typer.echo(f"Saved in the Keychain (service {keychain.SERVICE!r}). Scheduled runs will use it.")


@key_app.command("status")
def key_status(config: ConfigOption = None) -> None:
    """Show where the key would come from, without showing the key."""
    cfg = _load(config)
    found = credentials.resolve(cfg.provider)
    if found.value is None:
        typer.echo("No API key found (environment, Keychain, env_file). Run `flib key set`.")
        raise typer.Exit(EXIT_ERROR)
    typer.echo(f"Using the key from: {found.source} ({found.hint})")


@key_app.command("delete")
def key_delete() -> None:
    """Remove the API key from the Keychain."""
    try:
        removed = keychain.delete()
    except keychain.KeychainError as exc:
        _fatal(str(exc))
    typer.echo("Removed from the Keychain." if removed else "There was no key in the Keychain.")


# --- Organizing, searching and undoing ----------------------------------------------


@app.command()
def run(config: ConfigOption = None) -> None:
    """Organize the input folders now: classify, rename, move and index new files."""
    _pipeline(config, simulate=False)


@app.command()
def simulate(config: ConfigOption = None) -> None:
    """Print the full plan without changing anything."""
    _pipeline(config, simulate=True)


def _fatal(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(EXIT_ERROR)


def _load(config: Path | None) -> Config:
    try:
        return load_config(config)
    except ConfigError as exc:
        _fatal(str(exc))


def _pipeline(config_path: Path | None, *, simulate: bool) -> None:
    cfg = _load(config_path)
    try:
        provider = get_provider(cfg.provider)
        outcome = pipeline.run(cfg, provider, simulate=simulate)
    except (ProviderError, ScanError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    if outcome.recovered:
        typer.echo(f"Recovered {outcome.recovered} move(s) interrupted by an earlier crash.")
    skipped = _skipped_lines(outcome.scan.skipped, cfg)
    if simulate:
        _print_plan(outcome.plan, outcome.deferred, cfg, skipped)
    elif outcome.report is not None:
        _print_report(outcome.report, cfg, skipped)
    if outcome.cost_usd:
        typer.echo(f"Estimated provider cost: US$ {outcome.cost_usd:.4f}")
    if outcome.cached:
        typer.echo(f"{outcome.cached} classification(s) reused from the cache at no cost.")


def _short(path: Path, cfg: Config) -> str:
    for root in (cfg.output_root, *cfg.inputs):
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return str(path)


# Skips that are noise on every run: system files and flib's own folders.
_QUIET_SKIPS = {SkipReason.HIDDEN, SkipReason.MANAGED}


def _skipped_lines(skipped: list[tuple[Path, SkipReason]], cfg: Config) -> list[str]:
    """Why items were left alone. Already-processed ones are only counted."""
    lines = [
        f"  skip    {_short(path, cfg)}  ({reason.value})"
        for path, reason in skipped
        if reason not in _QUIET_SKIPS and reason is not SkipReason.KNOWN
    ]
    known = sum(1 for _, reason in skipped if reason is SkipReason.KNOWN)
    if known:
        lines.append(f"  ({known} item(s) already processed earlier were left alone)")
    return lines


def _print_plan(plan: Plan, deferred: int, cfg: Config, skipped: list[str]) -> None:
    if not plan.items:
        typer.echo("Nothing to do: no new files.")
    for item in plan.items:
        source = _short(item.context.path, cfg)
        if item.destination is None:
            typer.echo(f"  keep    {source}  ({item.reason})")
        else:
            typer.echo(f"  move    {source}  ->  {_short(item.destination, cfg)}  ({item.reason})")
    for line in skipped:
        typer.echo(line)
    moves = len(plan.moves())
    typer.echo(
        f"Simulation: {moves} would move, {len(plan.items) - moves} would stay. Nothing changed."
    )
    if deferred:
        typer.echo(f"{deferred} more file(s) wait for a later run (file or cost limit).")


def _print_report(report: RunReport, cfg: Config, skipped: list[str]) -> None:
    lines = []
    for item in report.items:
        source = _short(item.source, cfg)
        if item.destination is not None:
            lines.append(f"  moved   {source}  ->  {_short(item.destination, cfg)}")
        else:
            lines.append(f"  {item.status.value:<8}{source}  ({item.note})")
    lines.extend(skipped)
    lines.append(
        f"Run {report.run_id}: {report.count(FileStatus.ORGANIZED, moved=True)} organized, "
        f"{report.count(FileStatus.REVIEW, moved=True)} to review, "
        f"{report.count(FileStatus.SENSITIVE)} sensitive left in place, "
        f"{report.errors} failed, {report.count(FileStatus.SKIPPED)} skipped."
    )
    if report.deferred:
        lines.append(f"{report.deferred} more file(s) wait for the next run.")
    for line in lines:
        typer.echo(line)
    if report.run_id is not None:
        try:
            log = write_log(cfg.database, report.run_id, lines)
            typer.echo(f"Log: {log}")
        except OSError as exc:
            typer.echo(f"warning: could not write the run log: {exc}", err=True)


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="What you remember about the file.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON for other programs.")] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500, help="Most results.")] = 20,
    config: ConfigOption = None,
) -> None:
    """Find organized files by name, description or tags."""
    cfg = _load(config)
    if not cfg.database.exists():
        hits = []
    else:
        with Store.open(cfg.database) as store:
            hits = run_search(store, query, limit=limit)
    if as_json:
        typer.echo(search_json(hits))
        return
    if not hits:
        typer.echo("No matches.")
        return
    for hit in hits:
        where = hit.path if hit.exists else f"{hit.path}  (missing: moved by hand?)"
        label = hit.category or hit.status
        typer.echo(f"{where}\n  [{label}] was {hit.original_name}")
        if hit.description:
            typer.echo(f"  {hit.description}")


@app.command()
def undo(
    file: Annotated[
        Path | None, typer.Option("--file", help="Undo the last operation on this file.")
    ] = None,
    run_id: Annotated[int | None, typer.Option("--run", help="Undo this run.")] = None,
    config: ConfigOption = None,
) -> None:
    """Revert the last run, a given run, or a single file."""
    if file is not None and run_id is not None:
        _fatal("use either --file or --run, not both")
    cfg = _load(config)
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            recovered = recover(store)  # finish the journal before reading it
            report = undo_file(store, file) if file is not None else undo_run(store, run_id)
    except (NothingToUndoError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    if recovered:
        typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
    for dst, src in report.restored:
        typer.echo(f"  restored  {_short(dst, cfg)}  ->  {src}")
    for path, reason in report.skipped:
        typer.echo(f"  skipped   {_short(path, cfg)}  ({reason})")
    typer.echo(f"Undo: {len(report.restored)} restored, {len(report.skipped)} skipped.")


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
    try:
        app()
    except Exception as exc:
        typer.echo(
            f"error: unexpected {type(exc).__name__}: {exc}\n"
            "This is a bug. Please report it with `flib --version` and the steps that led here: "
            "https://github.com/luizpereiradev/file-librarian/issues",
            err=True,
        )
        raise SystemExit(EXIT_ERROR) from exc
