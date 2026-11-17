"""The ``flib`` command line.

Each command loads and validates the configuration, then calls one module:
``pipeline`` for ``run``/``simulate``, ``undo``, ``search``. Commands of later
milestones exist already and exit with code 2, naming their milestone and
requirement, so the interface can be reviewed before they land.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Annotated, NoReturn, cast

import typer

from file_librarian import __version__, credentials, keychain, onboarding, pipeline, scheduler
from file_librarian.config import (
    Config,
    ConfigError,
    ProviderConfig,
    ScheduleConfig,
    default_config_path,
    inactive_options_set,
    load_config,
)
from file_librarian.db.store import Store
from file_librarian.executor import RunReport, recover
from file_librarian.extractors.origin import download_origin
from file_librarian.fix import FixError, fix_file
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.locales import LOCALES, Language
from file_librarian.lock import AlreadyRunningError, instance_lock
from file_librarian.models import FileStatus, RunTrigger
from file_librarian.planner import Plan
from file_librarian.providers import PLANNED, ProviderError, get_provider, is_implemented
from file_librarian.reconcile import in_cloud_only
from file_librarian.reconcile import reconcile as run_reconcile
from file_librarian.runlog import logs_dir, write_log
from file_librarian.scanner import ScanError, SkipReason
from file_librarian.search import (
    SearchError,
    SearchFilters,
    SearchHit,
    make_filters,
    split_values,
)
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
    if cfg.provider.name == "claude-code":
        _report_claude(cfg)
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
    for option, milestone in inactive_options_set(cfg):
        typer.echo(
            f"warning: {option} is planned for {milestone} and does nothing yet; "
            "your value is accepted but ignored.",
            err=True,
        )


def _report_claude(cfg: Config) -> None:
    """Where the ``claude`` executable is and whether it is logged in (no prompt is sent)."""
    from file_librarian.providers import claude_code

    executable = claude_code.find_claude(cfg.provider)
    if executable is None:
        typer.echo("  claude: not found")
        typer.echo(
            "warning: install Claude Code, or set provider.command to the `claude` executable",
            err=True,
        )
        return
    typer.echo(f"  claude: {executable}")
    login = claude_code.logged_in(executable)
    if login is None:
        typer.echo("warning: could not ask Claude Code whether it is logged in", err=True)
    elif login:
        typer.echo("  claude login: ok")
    else:
        typer.echo(f"warning: Claude Code is not logged in; {claude_code.LOGIN_HINT}", err=True)


@app.command()
def inspect(
    paths: Annotated[list[Path], typer.Argument(exists=True, help="Files to inspect.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    config: ConfigOption = None,
) -> None:
    """Show what flib reads from a file before any AI is involved: hash, size, download origin.

    A file or folder that exists only in iCloud is reported as such, not hashed:
    reading it would download it. If the file is in flib's index, also show its
    status, category, and which name the AI judged better (original or its own)
    and why.
    """
    database = _index_database(config)
    results: list[dict[str, object]] = []
    for path in paths:
        cloud_only = in_cloud_only(path)
        digest: str | None = None
        size: int | None = None
        if path.is_dir():
            kind = "folder unit"
            if not cloud_only:
                manifest = unit_manifest(path)
                cloud_only = manifest.dataless
                if not cloud_only:
                    digest, size = manifest.digest, manifest.size_bytes
        else:
            kind = "file"
            size = path.stat().st_size  # metadata only, never downloads
            if not cloud_only:
                digest = file_sha256(path)
        results.append(
            {
                "path": str(path),
                "kind": kind,
                "icloud_only": cloud_only,
                "sha256": digest,
                "size_bytes": size,
                "source_urls": list(download_origin(path)),
                "index": _index_entry(database, digest),
            }
        )
    if as_json:
        typer.echo(json.dumps(results, indent=2, ensure_ascii=False))
        return
    for item in results:
        typer.echo(str(item["path"]))
        typer.echo(f"  kind:   {item['kind']}")
        if item["icloud_only"]:
            typer.echo(f"  sha256: ({SkipReason.CLOUD_ONLY.value}; download it to inspect it)")
        else:
            typer.echo(f"  sha256: {item['sha256']}")
        if item["size_bytes"] is not None:
            typer.echo(f"  size:   {item['size_bytes']} bytes")
        urls = item["source_urls"]
        if isinstance(urls, list) and urls:
            for url in urls:
                typer.echo(f"  from:   {url}")
        else:
            typer.echo("  from:   (no download origin recorded)")
        entry = item["index"]
        if isinstance(entry, dict):
            where = f", {entry['category']}" if entry["category"] else ""
            typer.echo(f"  index:  {entry['status']}{where}")
            choice = entry["name_choice"]
            if choice is not None:
                said = "original name kept" if choice == "original" else "new name from the AI"
                reason = entry["name_choice_reason"]
                typer.echo(f"  name:   {said}" + (f" ({reason})" if reason else ""))


def _index_database(config: Path | None) -> Path | None:
    """The index to look files up in, if there is one. ``inspect`` works without a
    configuration; only one passed with ``--config`` must load."""
    try:
        database = load_config(config).database
    except ConfigError as exc:
        if config is not None:
            _fatal(str(exc))
        return None
    return database if database.exists() else None


def _index_entry(database: Path | None, digest: str | None) -> dict[str, object] | None:
    """What the index says about a content hash (the model's words are untrusted data)."""
    if database is None or digest is None:
        return None
    with Store.open(database) as store:
        row = store.file_by_sha256(digest)
    if row is None:
        return None
    sensitive = row.status is FileStatus.SENSITIVE
    return {
        "status": row.status.value,
        "category": row.category,
        "current_path": str(row.current_path),
        "name_choice": row.name_choice,
        "name_choice_reason": None if sensitive else row.name_choice_reason,
    }


# --- API key ---------------------------------------------------------------------------

key_app = typer.Typer(
    help="Store the Anthropic API key in the macOS Keychain.", no_args_is_help=True
)
app.add_typer(key_app, name="key")


@key_app.command("set")
def key_set() -> None:
    """Save the API key in the Keychain (asked for, never shown)."""
    problem = _save_key()
    if problem is not None:
        _fatal(problem)


def _save_key() -> str | None:
    """Ask for the API key without echoing it and store it; the error, if any."""
    value = typer.prompt("Anthropic API key", hide_input=True).strip()
    if not value.startswith("sk-ant-"):
        typer.echo("warning: Anthropic keys usually start with sk-ant-", err=True)
    try:
        keychain.write(value)
    except (keychain.KeychainError, ValueError) as exc:
        return str(exc)
    typer.echo(f"Saved in the Keychain (service {keychain.SERVICE!r}). Scheduled runs will use it.")
    return None


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
def run(
    config: ConfigOption = None,
    trigger: Annotated[
        RunTrigger, typer.Option("--trigger", hidden=True, help="Who started the run.")
    ] = RunTrigger.MANUAL,
) -> None:
    """Organize the input folders now: classify, rename, move and index new files."""
    _pipeline(config, simulate=False, trigger=trigger)


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


def _pipeline(
    config_path: Path | None, *, simulate: bool, trigger: RunTrigger = RunTrigger.MANUAL
) -> None:
    cfg = _load(config_path)
    try:
        provider = get_provider(cfg.provider)
        outcome = pipeline.run(cfg, provider, simulate=simulate, trigger=trigger)
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
            if item.warning:
                lines.append(f"  warning {_short(item.destination, cfg)}  ({item.warning})")
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


def _search_filters(
    known: list[str],
    category: list[str] | None,
    ext: list[str] | None,
    since: str | None,
    until: str | None,
) -> SearchFilters:
    try:
        return make_filters(
            categories=category, extensions=ext, since=since, until=until, known_categories=known
        )
    except SearchError as exc:
        _fatal(str(exc))


@app.command()
def search(
    query: Annotated[
        str, typer.Argument(help="What you remember about the file (optional with filters).")
    ] = "",
    category: Annotated[
        list[str] | None,
        typer.Option(
            "--category",
            help="Only this category, any case. Repeat it or use commas for any of several.",
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Processed on or after this day (YYYY-MM-DD, local time)."),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="Processed on or before this day (YYYY-MM-DD, local time)."),
    ] = None,
    ext: Annotated[
        list[str] | None,
        typer.Option(
            "--ext", help="Only this extension (pdf or .pdf). Repeat it or use commas for several."
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON for other programs.")] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500, help="Most results.")] = 20,
    config: ConfigOption = None,
) -> None:
    """Find organized files by name, description or tags, and by category, period or type.

    Without a query, the files matching the filters are listed newest first.
    """
    cfg = _load(config)
    has_filters = bool(split_values(category) or split_values(ext) or since or until)
    if not query.strip() and not has_filters:
        _fatal("give words to search for, a filter (--category, --since, --until, --ext), or both")
    configured = [c.name for c in cfg.categories]
    hits: list[SearchHit] = []
    if not cfg.database.exists():
        _search_filters(configured, category, ext, since, until)  # still validate them
    else:
        with Store.open(cfg.database) as store:
            filters = _search_filters(configured + store.categories(), category, ext, since, until)
            hits = run_search(store, query, filters=filters, limit=limit)
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
            recovered = recover(store, cfg)  # finish the journal before reading it
            report = (
                undo_file(store, file, config=cfg)
                if file is not None
                else undo_run(store, run_id, config=cfg)
            )
    except (NothingToUndoError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    if recovered:
        typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
    for dst, src in report.restored:
        typer.echo(f"  restored  {_short(dst, cfg)}  ->  {src}")
    for path, reason in report.skipped:
        typer.echo(f"  skipped   {_short(path, cfg)}  ({reason})")
    for path, warning in report.warnings:
        typer.echo(f"  warning   {_short(path, cfg)}  ({warning})")
    typer.echo(f"Undo: {len(report.restored)} restored, {len(report.skipped)} skipped.")


@app.command()
def fix(
    file: Annotated[Path, typer.Argument(help="An organized file, by its current path.")],
    category: Annotated[
        str | None, typer.Option("--category", help="The category it belongs to.")
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="Its new file name; the extension is kept if left out."),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Correct a file's category or name; flib learns from it. `flib undo` reverts it."""
    cfg = _load(config)
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            recover(store, cfg)  # finish the journal before adding to it
            report = fix_file(cfg, store, file, category=category, name=name)
    except (FixError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    typer.echo(f"  moved   {_short(report.source, cfg)}  ->  {_short(report.destination, cfg)}")
    if report.category is not None:
        old, new = report.category
        typer.echo(f"  learned category: {old or '-'} -> {new}")
    if report.name is not None:
        typer.echo(f"  learned name: {report.name[0]} -> {report.name[1]}")
    for warning in report.warnings:
        typer.echo(f"  warning {warning}")
    typer.echo(f"Fixed in run {report.run_id}; `flib undo` reverts it.")


@app.command()
def schedule(
    config: ConfigOption = None,
    at: Annotated[
        str | None, typer.Option("--at", help="Time of day, HH:MM (default: schedule.time).")
    ] = None,
    show: Annotated[bool, typer.Option("--status", help="Show the schedule and exit.")] = False,
    remove: Annotated[bool, typer.Option("--remove", help="Stop the daily run.")] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh", help="Re-install the existing schedule as is (used after updates)."
        ),
    ] = False,
    save_key: Annotated[
        bool | None,
        typer.Option(
            "--save-key/--no-save-key",
            help="Copy ANTHROPIC_API_KEY from this shell to the Keychain (default: ask).",
        ),
    ] = None,
    check: Annotated[
        bool, typer.Option("--check/--no-check", help="Test folder access as launchd will.")
    ] = True,
) -> None:
    """Run flib every day at a fixed time (a launchd agent), and check it can work."""
    if remove:
        removed = scheduler.remove()
        typer.echo("Daily run removed." if removed else "No daily run was scheduled.")
        return
    if show:
        _print_schedule_status()
        return
    current = scheduler.status() if refresh else None
    if refresh:
        if current is None or not current.installed:
            typer.echo("No daily run is scheduled; nothing to refresh.")
            return
        config = config or scheduler.scheduled_config()
        at = at or current.time
        save_key, check = False, False
    path = config or default_config_path()
    cfg = _load(path)
    try:
        when = ScheduleConfig(time=at).time if at else cfg.schedule.time
    except ValueError:
        _fatal("--at must be a 24-hour time like 12:30")
    try:
        _install_schedule(path, cfg, when, save_key)
    except scheduler.ScheduleError as exc:
        _fatal(str(exc))
    if check and not _report_access(path.resolve()):
        raise typer.Exit(EXIT_ERROR)


def _install_schedule(path: Path, cfg: Config, when: str, save_key: bool | None) -> None:
    """Install the daily agent for the configuration at ``path``; raises ``ScheduleError``."""
    if not scheduler.is_installed_tool():
        typer.echo(
            "warning: flib is running from a development environment; the schedule will "
            "depend on this folder. Install it with ./scripts/install.sh and run "
            "`flib schedule` again from the installed command.",
            err=True,
        )
    _ensure_key_for_schedule(cfg, save_key)
    _ensure_claude_for_schedule(cfg)
    installed = scheduler.install(when, path.resolve(), logs_dir(cfg.database))
    typer.echo(f"Daily run scheduled at {when} ({installed}).")
    typer.echo("If the Mac is asleep then, it runs on wake. `flib schedule --remove` stops it.")


def _print_schedule_status() -> None:
    try:
        state = scheduler.status()
    except scheduler.ScheduleError as exc:
        _fatal(str(exc))
    if not state.installed:
        typer.echo("No daily run is scheduled. Run `flib schedule` to set one up.")
        return
    loaded = "active" if state.loaded else "installed but not loaded (run `flib schedule` again)"
    typer.echo(f"Daily run at {state.time}: {loaded}")
    typer.echo(f"  runs: {' '.join(state.program)}")
    if not state.interpreter_current:
        typer.echo(
            "warning: flib's interpreter copy is missing or out of date (Python or flib was "
            "updated). Run `flib schedule --refresh`; macOS may ask for access again.",
            err=True,
        )


def _ensure_key_for_schedule(cfg: Config, save_key: bool | None) -> None:
    if cfg.provider.name != "anthropic":
        return
    found = credentials.resolve(cfg.provider)
    if found.value is None:
        typer.echo(
            "warning: no API key found; the daily run will fail until you run `flib key set`.",
            err=True,
        )
        return
    if found.source != "environment":
        return  # Keychain or env_file: launchd can read those
    try:
        in_keychain = keychain.read() is not None
    except keychain.KeychainError:
        in_keychain = False
    if in_keychain:
        return
    if save_key is None:
        save_key = typer.confirm(
            "The API key is only in this shell's environment, which the daily run cannot "
            "see. Save it to the macOS Keychain?",
            default=True,
        )
    if save_key:
        try:
            keychain.write(found.value)
            typer.echo("API key saved to the Keychain.")
        except (keychain.KeychainError, ValueError) as exc:
            typer.echo(f"warning: could not save the key: {exc}", err=True)
    else:
        typer.echo(
            "warning: the daily run will not find the API key; see `flib key set`.", err=True
        )


def _ensure_claude_for_schedule(cfg: Config) -> None:
    """Warn when the scheduled run, with launchd's minimal PATH, would not find ``claude``."""
    if cfg.provider.name != "claude-code":
        return
    from file_librarian.providers import claude_code

    if claude_code.find_claude(cfg.provider, search_path=scheduler.SAFE_PATH) is not None:
        return
    here = claude_code.find_claude(cfg.provider)
    if here is None:
        typer.echo(
            "warning: Claude Code was not found; the daily run will fail until you install "
            "it or set provider.command.",
            err=True,
        )
        return
    typer.echo(
        f"warning: the daily run cannot see this shell's PATH and will not find {here}. "
        f"Add `command: {here}` under `provider:` in the configuration.",
        err=True,
    )


def _report_access(config_path: Path) -> bool:
    """Run the access check and explain any denial; ``False`` when macOS blocks a folder."""
    typer.echo("Checking folder access as the scheduled run will see it...")
    try:
        report = scheduler.check_access(config_path)
    except scheduler.ScheduleError as exc:
        typer.echo(f"warning: {exc}", err=True)
        return True
    denied = {folder: state for folder, state in report.items() if state != "ok"}
    for folder, state in report.items():
        typer.echo(f"  {folder}: {state}")
    if denied:
        binary = scheduler.dedicated_interpreter()
        typer.echo(
            "\nmacOS blocks the scheduled run from these folders. Open System Settings > "
            "Privacy & Security > Full Disk Access, click +, press Cmd+Shift+G and add:\n"
            f"  {binary}\n"
            "This is flib's own copy of Python, so the access covers flib only, not your other "
            "Python projects. Then run `flib schedule` again to re-check.",
            err=True,
        )
        return False
    typer.echo("Access OK: the daily run can read every input folder.")
    return True


@app.command("check-access", hidden=True)
def check_access(
    out: Annotated[Path, typer.Option("--out", help="Where to write the JSON report.")],
    config: ConfigOption = None,
) -> None:
    """Used by `flib schedule`: report whether this process can list the input folders."""
    try:
        report = scheduler.probe_inputs(load_config(config).inputs)
    except ConfigError as exc:
        report = {"config": f"error: {exc}"}
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(report, fh)


@app.command()
def reconcile(config: ConfigOption = None) -> None:
    """Update the index for files moved, renamed or deleted by hand. Moves nothing."""
    cfg = _load(config)
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            report = run_reconcile(cfg, store)
    except AlreadyRunningError as exc:
        _fatal(str(exc))
    for old, new in report.moved:
        typer.echo(f"  moved   {_short(old, cfg)}  ->  {_short(new, cfg)}")
    for path, old_category, new_category in report.recategorized:
        typer.echo(f"  learned {_short(path, cfg)}: {old_category or '-'} -> {new_category}")
    for path in report.found_again:
        typer.echo(f"  found   {_short(path, cfg)}")
    for path in report.missing:
        typer.echo(f"  missing {_short(path, cfg)}")
    for path in report.cloud_only:
        typer.echo(f"  icloud  {_short(path, cfg)}  ({SkipReason.CLOUD_ONLY.value}, not checked)")
    typer.echo(
        f"Reconcile: {report.checked} checked, {len(report.moved)} moved by hand, "
        f"{len(report.found_again)} found again, {len(report.missing)} missing, "
        f"{len(report.cloud_only)} in iCloud only."
    )


# --- First run -------------------------------------------------------------------------

VISION_NOTE = (
    "Vision lets the model look at photos, screenshots and scanned PDFs. What is sent: "
    "the image shrunk to 1568 px with its EXIF and GPS metadata removed, or the first 3 "
    "pages of a PDF with no text. About US$0.0005 per image and US$0.001 per scanned PDF "
    "on the default model. Off, they are classified from their name and metadata."
)


class _Asker:
    """Typer prompts or, with ``--yes``, their defaults, printed so the transcript reads alike."""

    def __init__(self, yes: bool) -> None:
        self.yes = yes

    def text(self, question: str, default: str) -> str:
        if self.yes:
            typer.echo(f"{question} [{default}]: {default}")
            return default
        answer = str(typer.prompt(question, default=default)).strip()
        return answer or default

    def choice(self, question: str, options: list[str], default: str) -> str:
        if self.yes:
            typer.echo(f"{question} ({', '.join(options)}) [{default}]: {default}")
            return default
        while True:
            answer = self.text(f"{question} ({', '.join(options)})", default)
            if answer in options:
                return answer
            typer.echo(f"error: choose one of {', '.join(options)}.", err=True)

    def confirm(self, question: str, default: bool) -> bool:
        if self.yes:
            hint = "[Y/n]" if default else "[y/N]"
            typer.echo(f"{question} {hint}: {'y' if default else 'n'}")
            return default
        return typer.confirm(question, default=default)


@app.command()
def init(
    config: ConfigOption = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Accept every default without asking.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing configuration without asking.")
    ] = False,
    sample: Annotated[
        int,
        typer.Option(
            "--sample", min=0, max=200, help="Recent items to try it on; 0 skips the trial."
        ),
    ] = onboarding.DEFAULT_SAMPLE,
) -> None:
    """Set flib up step by step, then try it on copies of your recent files.

    Nothing in your folders moves: the trial runs on copies in a temporary
    folder, with a throwaway index.
    """
    ask = _Asker(yes)
    path = config or default_config_path()
    typer.echo("Setting up file-librarian. Press Enter to take the suggestion in brackets.\n")
    overwrite = force or (
        path.exists()
        and ask.confirm(f"A configuration already exists at {path}. Replace it?", default=False)
    )
    if path.exists() and not overwrite:
        typer.echo(f"Keeping {path}.")
    else:
        answers = _ask_answers(ask)
        onboarding.write_config(path, onboarding.render_config(answers))
        typer.echo(f"\nWrote {path} (only you can read it). `flib check-config` validates it.")
    cfg = _load(path)
    typer.echo("")
    if _provider_ready(cfg, ask) and sample:
        _trial(cfg, sample, ask)
    else:
        typer.echo("Skipping the trial. `flib simulate` shows the plan once flib can run.")
    typer.echo("")
    _offer_schedule(path, cfg, ask)
    typer.echo(
        "\nNext:\n"
        "  flib simulate   the plan for your real folders; nothing moves\n"
        "  flib run        organize now; `flib undo` reverts it\n"
        "Optional: let Claude search your library through the MCP server; enable it with "
        "`./scripts/install.sh --with-mcp` from the file-librarian folder."
    )


def _ask_answers(ask: _Asker) -> onboarding.Answers:
    language = cast(
        Language,
        ask.choice(
            "Language of names, descriptions and folders",
            list(LOCALES),
            onboarding.detect_language(),
        ),
    )
    while True:
        answers = _ask_folders(ask, language)
        try:
            onboarding.check_answers(answers)
            break
        except ConfigError as exc:
            if ask.yes:
                _fatal(str(exc))
            typer.echo(f"error: {exc}\nLet's try the folders again.", err=True)
    provider, command = _ask_provider(ask)
    vision = False
    if provider == "anthropic":
        typer.echo(VISION_NOTE)
        vision = ask.confirm("Send images and scanned PDFs to the model?", default=False)
    else:
        typer.echo("Images and scanned PDFs are classified from their name and metadata.")
    return dataclasses.replace(answers, provider=provider, vision=vision, command=command)


def _user_path(raw: str) -> Path:
    return Path(os.path.normpath(Path(raw).expanduser()))


def _ask_folders(ask: _Asker, language: Language) -> onboarding.Answers:
    while True:
        folder = _user_path(
            ask.text("Folder to organize", onboarding.display_path(Path.home() / "Downloads"))
        )
        if folder.is_dir():
            break
        if ask.yes:
            _fatal(f"{folder} is not a folder; run `flib init` without --yes to pick another")
        typer.echo(f"error: {folder} is not a folder.", err=True)
    out = ask.text(
        "Where organized files go",
        onboarding.display_path(onboarding.default_output_root(folder, language)),
    )
    review = ask.text("Folder in it for files to review", LOCALES[language].review_folder)
    return onboarding.Answers(language, folder, _user_path(out), review, "anthropic")


def _ask_provider(ask: _Asker) -> tuple[str, Path | None]:
    """The provider, and the ``claude`` to pin when the daily run would not find it.

    Claude Code is the default when it is installed and logged in: nothing to set up.
    """
    from file_librarian.providers import claude_code

    probe = ProviderConfig(name="claude-code", model=onboarding.DEFAULT_MODELS["claude-code"])
    executable = claude_code.find_claude(probe)
    ready = executable is not None and claude_code.logged_in(executable) is True
    if executable is None:
        state = "not found"
    else:
        state = "found, logged in" if ready else "found, not logged in"
    typer.echo(
        "\nWhich AI classifies your files?\n"
        f"  claude-code  your Claude subscription through Claude Code ({state}); no API key\n"
        "  anthropic    the Anthropic API with an API key, billed per use "
        "(a fraction of a cent per file)"
    )
    default = "claude-code" if ready else "anthropic"
    name = ask.choice("Provider", ["claude-code", "anthropic"], default)
    if name != "claude-code" or executable is None:
        return name, None
    # launchd's PATH is minimal: pin an executable the daily run would not find by itself.
    hidden = claude_code.find_claude(probe, search_path=scheduler.SAFE_PATH) is None
    return name, executable if hidden else None


def _provider_ready(cfg: Config, ask: _Asker) -> bool:
    """Whether the provider can classify now, offering to store a missing API key."""
    name = cfg.provider.name
    if name == "anthropic":
        found = credentials.resolve(cfg.provider)
        if found.value is not None:
            typer.echo(f"API key: found in the {found.source} ({found.hint}).")
            return True
        if not ask.yes and ask.confirm(
            "No Anthropic API key found. Store one in the macOS Keychain now?", default=True
        ):
            problem = _save_key()
            if problem is None:
                return True
            typer.echo(f"warning: {problem}", err=True)
        typer.echo("warning: no API key yet; run `flib key set` to add one.", err=True)
        return False
    if name == "claude-code":
        from file_librarian.providers import claude_code

        executable = claude_code.find_claude(cfg.provider)
        if executable is None:
            typer.echo(
                "warning: Claude Code was not found; install it or set provider.command.",
                err=True,
            )
            return False
        if claude_code.logged_in(executable) is False:
            typer.echo(
                f"warning: Claude Code is not logged in; {claude_code.LOGIN_HINT}.", err=True
            )
            return False
        return True
    if not is_implemented(name):
        typer.echo(f"warning: provider {name!r} is not available yet.", err=True)
        return False
    return True


def _trial(cfg: Config, size: int, ask: _Asker) -> None:
    picked = onboarding.pick_sample(cfg, size)
    where = ", ".join(onboarding.display_path(p) for p in cfg.inputs)
    if not picked:
        typer.echo(
            f"No files to try it on in {where}: it is empty, or holds only hidden, sensitive "
            "or large items. `flib simulate` shows the plan once files arrive."
        )
        return
    question = (
        f"Try it on copies of your {len(picked)} most recent item(s)? They are copied to a "
        f"temporary folder and classified with {cfg.provider.name}; nothing in {where} moves."
    )
    if not ask.confirm(question, default=True):
        return
    typer.echo(f"Classifying {len(picked)} copies; this can take a minute...")
    try:
        result = onboarding.run_trial(cfg, get_provider(cfg.provider), picked)
    except (ProviderError, ScanError) as exc:
        typer.echo(f"warning: the trial stopped: {exc}", err=True)
        return
    _print_trial(result, cfg)


def _print_trial(trial: onboarding.Trial, cfg: Config) -> None:
    root = onboarding.display_path(cfg.output_root)
    typer.echo(f"\nTrial on {trial.sampled} copies (destinations are inside {root}):")
    for item in trial.items:
        if item.destination is None:
            typer.echo(f"  keep    {item.name}  ({item.reason})")
        else:
            label = "review" if item.status is FileStatus.REVIEW else "move"
            typer.echo(f"  {label:<8}{item.name}  ->  {item.destination}")
    organized = trial.count(FileStatus.ORGANIZED)
    review = trial.count(FileStatus.REVIEW)
    stay = len(trial.items) - organized - review
    typer.echo(
        f"Summary: {organized} to categories, {review} to {cfg.review_folder} for you to "
        f"decide, {stay} would stay."
    )
    categories = trial.by_category()
    if categories:
        typer.echo("  " + ", ".join(f"{name}: {n}" for name, n in sorted(categories.items())))
    if trial.deferred:
        typer.echo(f"  {trial.deferred} not tried: the cost cap was reached.")
    typer.echo(
        f"Estimated cost of the trial: US$ {trial.cost_usd:.4f}. Nothing in your folders "
        "moved; the copies and the trial's index are already deleted."
    )


def _offer_schedule(path: Path, cfg: Config, ask: _Asker) -> None:
    try:
        state = scheduler.status()
    except scheduler.ScheduleError as exc:
        typer.echo(f"warning: cannot read the schedule: {exc}", err=True)
        return
    if state.installed:
        typer.echo(f"A daily run is already scheduled at {state.time}; `flib schedule` changes it.")
        return
    question = f"Organize new files automatically every day at {cfg.schedule.time}?"
    if not ask.confirm(question, default=True):
        typer.echo("You can schedule it later with `flib schedule` (--at HH:MM for another time).")
        return
    try:
        _install_schedule(path, cfg, cfg.schedule.time, save_key=True if ask.yes else None)
    except scheduler.ScheduleError as exc:
        typer.echo(f"warning: could not schedule the daily run: {exc}", err=True)
        return
    _report_access(path.resolve())


@app.command()
def mcp(config: ConfigOption = None) -> None:
    """Serve the library to AI assistants: a local MCP server over stdio."""
    cfg = _load(config)
    try:
        # The SDK is an optional extra, and loads only for this command.
        from file_librarian.mcp_server import serve
    except ImportError as exc:
        _fatal(
            f"the MCP server is optional and is not installed ({exc}). Enable it with "
            "`./scripts/install.sh --with-mcp` from the file-librarian folder."
        )
    serve(cfg)


@app.command()
def reprocess(config: ConfigOption = None) -> None:
    """Reclassify files after a rules change."""
    _not_implemented("reprocess", "M3", "RF-37")


@app.command()
def doctor(
    config: ConfigOption = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Write the report here [default: a new doctor-<time> file in flib's logs folder].",
            dir_okay=False,
        ),
    ] = None,
    to_stdout: Annotated[
        bool, typer.Option("--stdout", help="Print the report instead of writing a file.")
    ] = False,
    fmt: Annotated[
        str,
        typer.Option("--format", help="text or markdown (paste-ready for a GitHub issue)."),
    ] = "text",
    run_logs: Annotated[
        int, typer.Option("--logs", min=0, help="How many recent run logs to include.")
    ] = 5,
) -> None:
    """Write a diagnostics report for a bug report, with names, paths and secrets redacted.

    It holds versions, the configuration (no API key, not even part of it), the
    schedule, database counts and the last run logs. Read it before you attach it.
    """
    from file_librarian import doctor as diagnostics

    if fmt not in ("text", "markdown"):
        _fatal(f"--format must be text or markdown, not {fmt!r}")
    report_format = cast(diagnostics.Format, fmt)
    report = diagnostics.build_report(
        diagnostics.Options(config_path=config, run_logs=run_logs), report_format
    )
    if to_stdout:
        typer.echo(report, nl=False)
        return
    path = output or diagnostics.default_report_path(config, report_format)
    try:
        diagnostics.write_report(path, report)
    except OSError as exc:
        _fatal(f"cannot write {path}: {exc}; try --stdout or --output")
    typer.echo(f"Report written to {path}")
    typer.echo("Read it, then attach it to the issue. To show it in Finder:")
    typer.echo(f"  open -R {shlex.quote(str(path))}")


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
