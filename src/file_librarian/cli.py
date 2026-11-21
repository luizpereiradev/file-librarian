"""The ``flib`` command line.

Each command loads and validates the configuration, then calls one module:
``pipeline`` for ``run``/``simulate``, ``undo``, ``search``, ``reprocess``...
Every command but ``reprocess`` (which handles it) and ``status`` (which only
reads) warns when organized files still use a category the configuration no
longer has (RF-37).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shlex
import sqlite3
import sys
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Any, NoReturn, cast

import typer
from pydantic import ValidationError

from file_librarian import (
    __version__,
    catchup,
    credentials,
    embeddings,
    keychain,
    onboarding,
    pipeline,
    scheduler,
)
from file_librarian import due as due_mod
from file_librarian import reprocess as reprocess_mod
from file_librarian import status as status_mod
from file_librarian.config import (
    Config,
    ConfigError,
    ProviderConfig,
    ScheduleConfig,
    default_config_path,
    inactive_options_set,
    is_loopback_url,
    is_within,
    load_config,
)
from file_librarian.context import build_context_safely
from file_librarian.db.store import GROUPS, FileRow, MigrationError, Store
from file_librarian.executor import ItemResult, RunReport, recover
from file_librarian.extractors import ocr
from file_librarian.extractors.origin import download_origin
from file_librarian.fields import KINDS, format_amount, format_decimal
from file_librarian.fix import FixError, fix_file
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.locales import LOCALES, Language
from file_librarian.lock import AlreadyRunningError, instance_lock, reprocess_lock
from file_librarian.models import FileStatus, RunTrigger, explain_review
from file_librarian.planner import Plan
from file_librarian.providers import PLANNED, ProviderError, get_provider, is_implemented
from file_librarian.providers.base import Provider
from file_librarian.reconcile import in_cloud_only
from file_librarian.reconcile import reconcile as run_reconcile
from file_librarian.runlog import append_log, logs_dir, write_log
from file_librarian.scanner import Candidate, ScanError, ScanResult, SkipReason
from file_librarian.search import (
    SearchError,
    SearchFilters,
    SearchHit,
    Sums,
    make_filters,
    original_path,
    semantic_search,
    split_values,
    sum_amounts,
)
from file_librarian.search import search as run_search
from file_librarian.search import to_json as search_json
from file_librarian.undo import NothingToUndoError, undo_file, undo_run

EXIT_ERROR = 1

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
    typer.echo("  inputs:")
    for entry in cfg.inputs:
        typer.echo(f"    {entry.path}: {cfg.input_summary(entry)}")
    typer.echo(f"  output_root: {cfg.output_root}")
    typer.echo(f"  review_folder: {cfg.review_folder}")
    typer.echo(f"  categories: {', '.join(c.name for c in cfg.categories)}")
    typer.echo(f"  provider: {cfg.provider.name} ({cfg.provider.model})")
    spec = credentials.key_spec(cfg.provider)
    if spec is not None:
        found = credentials.resolve(cfg.provider)
        if found.value:
            typer.echo(f"  api key: {found.source} ({found.hint})")
        elif spec.provider == "anthropic":
            typer.echo("  api key: not found (run `flib key set`, or set ANTHROPIC_API_KEY)")
        else:
            typer.echo(
                "  api key: none (fine for a server that needs none; otherwise run "
                f"`flib key set --provider {spec.provider}`, or set {spec.env_var})"
            )
    for entry in cfg.inputs:
        if entry.category is not None and entry.only is None:
            typer.echo(
                f"warning: input {entry.path} has a fixed category and no `only`: everything "
                f"in this folder is filed as {entry.category} without being looked at.",
                err=True,
            )
    if cfg.provider.name == "claude-code":
        _report_claude(cfg)
    if cfg.provider.name in COMPAT_PROVIDERS:
        _report_compat(cfg)
    else:
        _warn_compat_only_options(cfg.provider)
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
    _report_ocr(cfg)
    _report_semantic(cfg)
    _warn_orphan_categories(cfg)
    for option, milestone in inactive_options_set(cfg):
        typer.echo(
            f"warning: {option} is planned for {milestone} and does nothing yet; "
            "your value is accepted but ignored.",
            err=True,
        )


COMPAT_PROVIDERS = ("openai-compat", "ollama")
COMPAT_ONLY_OPTIONS = (
    "base_url", "api_key_env", "price_input_per_mtok", "price_output_per_mtok", "vision",
)  # fmt: skip


def _warn_compat_only_options(provider: ProviderConfig) -> None:
    for option in COMPAT_ONLY_OPTIONS:
        if option in provider.model_fields_set:
            typer.echo(
                f"warning: provider.{option} applies to openai-compat and ollama only; "
                f"{provider.name} ignores it.",
                err=True,
            )


def _report_compat(cfg: Config) -> None:
    """Where file content goes, what the cost cap can do, and (Ollama) whether it answers."""
    p = cfg.provider
    url = p.base_url or ""
    local = is_loopback_url(url)
    where = "this Mac; no rate limit, never_send still applies" if local else "a remote server"
    typer.echo(f"  base_url: {url} ({where})")
    if not local and url.startswith("http://"):
        typer.echo(
            "warning: provider.base_url uses http:// to another machine; file content "
            "would travel unencrypted. Use https://.",
            err=True,
        )
    if p.name == "ollama" and not local:
        typer.echo(
            "warning: provider.base_url is not this Mac: file content leaves it, and Ollama "
            "is treated as a remote provider.",
            err=True,
        )
    priced = p.price_input_per_mtok > 0 or p.price_output_per_mtok > 0
    if not priced and not local and cfg.limits.max_cost_usd_per_run is not None:
        typer.echo(
            "warning: provider.price_input_per_mtok and price_output_per_mtok are not set, so "
            "the cost of each file counts as 0 and limits.max_cost_usd_per_run cannot stop a "
            "run. Set them to your provider's prices.",
            err=True,
        )
    if cfg.vision.enabled and not p.vision:
        typer.echo("  vision: off for this model (set provider.vision: true if it reads images)")
    if p.name == "ollama":
        from file_librarian.providers import ollama

        ready, line = ollama.check(p)
        if ready:
            typer.echo(f"  ollama: {line}")
        else:
            typer.echo(f"warning: {line}", err=True)


def _report_ocr(cfg: Config) -> None:
    """OCR: off, or on with its languages; a warning when this Mac cannot run it."""
    settings = cfg.ocr
    if not settings.enabled:
        typer.echo("  ocr: off")
        return
    found = ocr.availability()
    if not found.available:
        typer.echo("  ocr: unavailable")
        typer.echo(
            f"warning: OCR is on but cannot run here ({found.reason}); images and scanned "
            "PDFs are classified with vision if it is on, else from their name and metadata.",
            err=True,
        )
        return
    typer.echo(
        f"  ocr: on, on this Mac ({', '.join(settings.languages)}; "
        f"first {settings.max_pages} pages of a scanned PDF)"
    )
    unknown = [lang for lang in settings.languages if lang not in found.languages]
    if unknown:
        typer.echo(
            f"warning: ocr.languages: macOS cannot recognise {', '.join(unknown)}; "
            f"it can: {', '.join(found.languages)}",
            err=True,
        )


def _report_semantic(cfg: Config) -> None:
    """Semantic search: off, or where the index's text goes to be embedded."""
    semantic = cfg.search.semantic
    if not semantic.enabled:
        typer.echo("  semantic search: off")
        return
    url = semantic.base_url
    local = is_loopback_url(url)
    where = "this Mac" if local else "a remote server"
    typer.echo(f"  semantic search: {semantic.model} at {url} ({where})")
    if not local:
        typer.echo(
            "warning: search.semantic.base_url is not this Mac: the names, categories, tags "
            "and descriptions in the index, and every semantic query, leave it. Embeddings are "
            "not priced; check your server's rates.",
            err=True,
        )
        if url.startswith("http://"):
            typer.echo(
                "warning: search.semantic.base_url uses http:// to another machine; the index "
                "text would travel unencrypted. Use https://.",
                err=True,
            )
    found = credentials.resolve_embeddings(semantic)
    if found.value:
        typer.echo(f"  embeddings api key: {found.source} ({found.hint})")


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

    With a configuration, also how much text a run would send and where it
    comes from (read from the file, or recognised by OCR on this Mac), never
    the text itself.

    A file or folder that exists only in iCloud is reported as such, not hashed:
    reading it would download it. If the file is in flib's index, also show its
    status, category, why it is in review (if it is), and which name the AI
    judged better (original or its own) and why.
    """
    cfg = _inspect_config(config)
    database = cfg.database if cfg is not None and cfg.database.exists() else None
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
                "index": _index_entry(database, digest, path),
                "text": _text_summary(path, digest, size, cfg)
                if kind == "file" and digest is not None and size is not None
                else None,
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
        summary = item["text"]
        if isinstance(summary, dict):
            typer.echo(f"  text:   {_describe_text(summary)}")
        entry = item["index"]
        if isinstance(entry, dict):
            where = f", {entry['category']}" if entry["category"] else ""
            typer.echo(f"  index:  {entry['status']}{where}")
            review = entry["review"]
            if review is not None:
                typer.echo(f"  review: {review}")
            if entry["duplicate_of"] is not None:
                typer.echo(f"  duplicate of: {entry['duplicate_of']}")
            choice = entry["name_choice"]
            if choice is not None:
                said = "original name kept" if choice == "original" else "new name from the AI"
                reason = entry["name_choice_reason"]
                typer.echo(f"  name:   {said}" + (f" ({reason})" if reason else ""))
            fields = entry["fields"]
            if isinstance(fields, dict):
                for key, value in fields.items():
                    if value is not None and key != "currency":
                        if key == "amount":
                            value = f"{value} {fields['currency']}"
                        typer.echo(f"  {key.replace('_', ' ') + ':':<8}{value}")


@app.command()
def status(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    sensitive: Annotated[
        bool,
        typer.Option(
            "--sensitive", help="List each file left in place as sensitive, and why (names!)."
        ),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Show what waits for you: files in review and why, failures, the last and next run.

    Also files left in place as sensitive and this month's provider cost. It
    only reads: no lock, nothing created, and it works before the first run.
    ``--sensitive`` lists those files by path, here only: never in `flib doctor`,
    a notification or the MCP server.
    """
    cfg = _load(config, warn_orphans=False)
    if sensitive:
        try:
            found = status_mod.sensitive_files(cfg)
        except (status_mod.StatusError, sqlite3.Error) as exc:
            _fatal(f"cannot read the index: {exc}")
        for line in status_mod.render_sensitive(found):
            typer.echo(line)
        return
    try:
        state = status_mod.collect(cfg)
    except (status_mod.StatusError, sqlite3.Error) as exc:
        _fatal(f"cannot read the index: {exc}")
    if as_json:
        typer.echo(status_mod.to_json(state, cfg))
        return
    for line in status_mod.render(state, cfg):
        typer.echo(line)


def _inspect_config(config: Path | None) -> Config | None:
    """The configuration ``inspect`` reads files with, if there is one. ``inspect``
    works without one; only one passed with ``--config`` must load."""
    try:
        return load_config(config)
    except ConfigError as exc:
        if config is not None:
            _fatal(str(exc))
        return None


def _text_summary(path: Path, digest: str, size: int, cfg: Config | None) -> dict[str, object]:
    """What text a run would read from ``path`` and where it comes from; never the text."""
    if cfg is None:
        return {"chars": None, "source": None, "note": "no configuration; not read"}
    st = path.stat()
    candidate = Candidate(
        path=path.absolute(),
        sha256=digest,
        size_bytes=size,
        mtime=st.st_mtime,
        is_unit=False,
        mtime_ns=st.st_mtime_ns,
        inode=st.st_ino,
    )
    ctx = build_context_safely(candidate, cfg)
    meta = ctx.metadata
    if ctx.sensitive:
        note: str | None = f"sensitive ({meta.sensitive}); nothing is sent"
    else:
        note = meta.content or meta.extraction_error
    if meta.ocr_error:
        note = f"{note}; OCR failed: {meta.ocr_error}" if note else f"OCR failed: {meta.ocr_error}"
    return {
        "chars": len(ctx.text) if ctx.text else 0,
        "source": meta.text_source or ("extracted" if ctx.text else None),
        "note": note,
    }


def _describe_text(summary: dict[str, object]) -> str:
    chars, source, note = summary["chars"], summary["source"], summary["note"]
    if source == "ocr":
        said = f"{chars} characters, recognised by OCR on this Mac"
    elif source is not None:
        said = f"{chars} characters"
    else:
        said = "none sent"
    return f"{said} ({note})" if note else said


def _index_entry(database: Path | None, digest: str | None, path: Path) -> dict[str, object] | None:
    """What the index says about a content hash (the model's words are untrusted data).

    A duplicate set aside (RF-21) is shown as itself, with where its original is.
    """
    if database is None or digest is None:
        return None
    with Store.open(database) as store:
        row = store.file_by_current_path(path.absolute())
        if row is None or row.sha256 != digest or row.duplicate_of is None:
            row = store.file_by_sha256(digest)
        original = None if row is None else original_path(store, row.duplicate_of)
    if row is None:
        return None
    sensitive = row.status is FileStatus.SENSITIVE
    in_review = row.status is FileStatus.REVIEW
    return {
        "status": row.status.value,
        "category": row.category,
        "current_path": str(row.current_path),
        "review_reason": row.review_reason.value if in_review and row.review_reason else None,
        "review_category": row.review_category if in_review else None,
        "review": explain_review(row.review_reason, row.review_category, row.confidence)
        if in_review
        else None,
        "name_choice": row.name_choice,
        "name_choice_reason": None if sensitive else row.name_choice_reason,
        "duplicate_of": original,
        "fields": None if sensitive else _row_fields(row),
    }


def _row_fields(row: FileRow) -> dict[str, str | None]:
    """What the AI read in the document (#88); the amount as a decimal string."""
    amount = None
    if row.amount_minor is not None and row.currency is not None:
        amount = format_decimal(row.amount_minor, row.currency)
    return {
        "kind": row.kind,
        "issuer": row.issuer,
        "amount": amount,
        "currency": row.currency,
        "document_date": row.document_date,
        "due_date": row.due_date,
        "period": row.period,
    }


# --- API key ---------------------------------------------------------------------------

key_app = typer.Typer(
    help="Store an API key in the macOS Keychain (anthropic, openai-compat, embeddings).",
    no_args_is_help=True,
)
app.add_typer(key_app, name="key")

KeyProviderOption = Annotated[
    str | None,
    typer.Option(
        "--provider",
        help="anthropic, openai-compat, or embeddings (semantic search's server) "
        "(default: the configured provider, else anthropic).",
    ),
]


def _key_spec(provider: str | None, config: Path | None) -> credentials.KeySpec:
    """The key to act on: ``--provider``, else the configured provider's, else Anthropic's."""
    if provider is None:
        try:
            cfg = load_config(config)
        except ConfigError:
            cfg = None
        found = credentials.key_spec(cfg.provider) if cfg is not None else None
        return found or cast(credentials.KeySpec, credentials.spec_for("anthropic"))
    spec = credentials.spec_for(provider)
    if spec is None:
        _fatal(
            f"--provider must be anthropic, openai-compat or embeddings; {provider} takes no key"
        )
    return spec


@key_app.command("set")
def key_set(provider: KeyProviderOption = None, config: ConfigOption = None) -> None:
    """Save the API key in the Keychain (asked for, never shown)."""
    problem = _save_key(_key_spec(provider, config))
    if problem is not None:
        _fatal(problem)


def _save_key(spec: credentials.KeySpec) -> str | None:
    """Ask for the API key without echoing it and store it; the error, if any."""
    value = typer.prompt(spec.label, hide_input=True).strip()
    if spec.provider == "anthropic" and not value.startswith("sk-ant-"):
        typer.echo("warning: Anthropic keys usually start with sk-ant-", err=True)
    try:
        keychain.write(value, spec.account)
    except (keychain.KeychainError, ValueError) as exc:
        return str(exc)
    typer.echo(
        f"Saved in the Keychain (service {keychain.SERVICE!r}, account {spec.account!r}). "
        "Scheduled runs will use it."
    )
    return None


@key_app.command("status")
def key_status(config: ConfigOption = None) -> None:
    """Show where the configured provider's key would come from, without showing the key."""
    cfg = _load(config)
    spec = credentials.key_spec(cfg.provider)
    if spec is None:
        typer.echo(f"Provider {cfg.provider.name} takes no API key.")
        return
    found = credentials.resolve(cfg.provider)
    if found.value is None:
        typer.echo(
            f"No API key found ({spec.env_var}, Keychain, env_file). Run `flib key set"
            + ("`." if spec.provider == "anthropic" else f" --provider {spec.provider}`.")
        )
        raise typer.Exit(EXIT_ERROR)
    typer.echo(f"Using the key from: {found.source} ({found.hint})")


@key_app.command("delete")
def key_delete(provider: KeyProviderOption = None, config: ConfigOption = None) -> None:
    """Remove the API key from the Keychain."""
    spec = _key_spec(provider, config)
    try:
        removed = keychain.delete(spec.account)
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


def _load(config: Path | None, *, warn_orphans: bool = True) -> Config:
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        _fatal(str(exc))
    if warn_orphans:
        _warn_orphan_categories(cfg)
    return cfg


def _warn_orphan_categories(cfg: Config) -> None:
    """Categories the index still uses but the configuration lost need a migration (RF-37)."""
    if not cfg.database.exists():
        return
    try:
        with Store.open(cfg.database) as store:
            orphans = reprocess_mod.orphan_categories(cfg, store)
    except (sqlite3.Error, MigrationError, OSError):
        return  # the command itself reports a database it cannot use
    if orphans:
        typer.echo(f"warning: {reprocess_mod.orphan_message(orphans)}", err=True)


def _pipeline(
    config_path: Path | None,
    *,
    simulate: bool,
    trigger: RunTrigger = RunTrigger.MANUAL,
    catch_up: bool = False,
) -> None:
    """One run (or simulation). ``catch_up``: the second pass of a scheduled run that
    waited for the Mac to be unlocked; it never waits again."""
    cfg = _load(config_path)
    scheduled = trigger is RunTrigger.SCHEDULE and not simulate
    try:
        provider = get_provider(cfg.provider)
        outcome = pipeline.run(cfg, provider, simulate=simulate, trigger=trigger)
    except AlreadyRunningError as exc:
        if catch_up:
            _say(f"Not running again after the unlock: {exc}. That run takes care of them.")
            return
        _fatal(str(exc))
    except (ProviderError, ScanError) as exc:
        _fatal(str(exc))
    if outcome.recovered:
        typer.echo(f"Recovered {outcome.recovered} move(s) interrupted by an earlier crash.")
    skipped = _skipped_lines(outcome.scan, cfg)
    cost = _cost_line(outcome.cost_usd, known=outcome.cost_known)
    hint = _access_hint(outcome.scan) if trigger is RunTrigger.SCHEDULE else None
    locked = [path for path, reason in outcome.scan.skipped if reason is SkipReason.LOCKED]
    hours = cfg.schedule.wait_for_unlock_hours
    wait = scheduled and not catch_up and bool(locked) and hours > 0
    if simulate:
        _print_plan(outcome.plan, outcome.deferred, cfg, skipped)
        if cost:
            typer.echo(cost)
    elif outcome.report is not None:
        summary = _periodic_summary(cfg, outcome.scan) if trigger is RunTrigger.SCHEDULE else []
        notice = _duplicates_notice(cfg, trigger)
        if notice:
            summary.insert(0, notice)
        if catch_up:
            summary.insert(0, "Catch-up run, after the Mac was unlocked.")
        if wait:
            summary.append(
                f"Waiting up to {hours} h for the Mac to be unlocked to read "
                f"{len(locked)} file(s) protected while it is locked; then it runs once more."
            )
        _print_report(outcome.report, cfg, skipped, cost, hint, summary)
    if outcome.cached:
        typer.echo(f"{outcome.cached} classification(s) reused from the cache at no cost.")
    if outcome.embedding is not None and outcome.embedding.error:
        typer.echo(
            f"warning: semantic search: {outcome.embedding.error}; the files not embedded "
            "are retried on the next run (or `flib search --reindex`).",
            err=True,
        )
    if wait and outcome.report is not None:
        _wait_and_catch_up(config_path, cfg, outcome.report.run_id, locked)


def _say(line: str) -> None:
    """A line about the scheduled run's waiting, with the time: it goes to schedule.out.log."""
    typer.echo(f"[{datetime.now().astimezone():%Y-%m-%d %H:%M}] {line}")


def _wait_and_catch_up(
    config_path: Path | None, cfg: Config, run_id: int | None, locked: list[Path]
) -> None:
    """After a scheduled run that skipped files protected while the Mac is locked:
    wait for an unlock (holding no instance lock) and run once more (see ``catchup``)."""
    hours = cfg.schedule.wait_for_unlock_hours

    def note(line: str) -> None:
        _say(line)
        if run_id is not None:
            with contextlib.suppress(OSError):
                append_log(cfg.database, run_id, [line])

    with catchup.waiter(cfg.database) as held:
        if not held:
            note(
                "Not waiting for an unlock: an earlier scheduled run is waiting already "
                "and runs once more after it."
            )
            return
        _say(f"Waiting up to {hours} h for the Mac to be unlocked (checking every minute).")
        waited = catchup.wait_for_unlock(hours, partial(catchup.unlocked, locked))
    if waited is catchup.Waited.TIMED_OUT:
        note(
            f"The Mac was not unlocked within {hours} h; the next scheduled run "
            "(or `flib run`) gets the files protected while it is locked."
        )
    elif waited is catchup.Waited.STOPPED:
        note("Stopped while waiting for an unlock (SIGTERM); the next run gets those files.")
    else:
        note("The Mac was unlocked; running once more for the files skipped while it was locked.")
        _pipeline(config_path, simulate=False, trigger=RunTrigger.SCHEDULE, catch_up=True)


def _periodic_summary(cfg: Config, scan: ScanResult) -> list[str]:
    """After a scheduled run, what waits for the user, when due (RF-40). Never fails the run."""
    stuck = sum(1 for _, reason in scan.skipped if reason is SkipReason.NOT_WHERE_LEFT)
    locked = sum(1 for _, reason in scan.skipped if reason is SkipReason.LOCKED)
    try:
        return status_mod.after_scheduled_run(cfg, not_where_left=stuck, locked=locked)
    except Exception as exc:  # the run itself succeeded; the summary is a courtesy
        return [f"warning: the periodic summary failed: {type(exc).__name__}: {exc}"]


def _interactive() -> bool:
    """Whether the output reaches a person at a terminal (not a log, not a pipe)."""
    return sys.stdout.isatty()


def _duplicates_notice(cfg: Config, trigger: RunTrigger) -> str | None:
    """The one-time note about duplicates (RF-21), while nobody has seen it.

    It goes to the run log every time until a run started by hand prints it to
    a terminal; ``flib status`` shows it meanwhile.
    """
    try:
        with Store.open(cfg.database) as store:
            if not status_mod.duplicates_notice_due(store):
                return None
            if trigger is not RunTrigger.SCHEDULE and _interactive():
                status_mod.acknowledge_duplicates_notice(store)
    except (sqlite3.Error, OSError):
        return None
    return status_mod.duplicates_notice(cfg)


def _cost_line(cost_usd: float, *, known: bool) -> str | None:
    """The provider cost for the summary and the run log; ``None`` when nothing was paid."""
    if not known:
        return (
            "Provider cost: unknown (no prices configured for this provider; "
            "set provider.price_input_per_mtok and price_output_per_mtok)"
        )
    return f"Estimated provider cost: US$ {cost_usd:.4f}" if cost_usd else None


def _short(path: Path, cfg: Config) -> str:
    for root in (cfg.output_root, *cfg.input_paths):
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return str(path)


# Skips that are noise on every run: system files and flib's own folders.
_QUIET_SKIPS = {SkipReason.HIDDEN, SkipReason.MANAGED}


def _skipped_lines(scan: ScanResult, cfg: Config) -> list[str]:
    """Why items were left alone. Already-processed ones are only counted."""
    skipped = scan.skipped
    lines = []
    for path, reason in skipped:
        if reason in _QUIET_SKIPS or reason in (SkipReason.KNOWN, SkipReason.NOT_ONLY):
            continue
        detail = scan.details.get(path) if reason in _DETAILED else None
        why = f"{reason.value}: {detail}" if detail else reason.value
        if reason is SkipReason.TOO_RECENT:
            left = scan.days_left.get(path, 1)
            why = f"{reason.value} ({left} day{'s' if left != 1 else ''} left)"
        where = str(path) if reason is SkipReason.INPUT_UNREADABLE else _short(path, cfg)
        lines.append(f"  skip    {where}  ({why})")
    known = sum(1 for _, reason in skipped if reason is SkipReason.KNOWN)
    if known:
        lines.append(f"  ({known} item(s) already processed earlier were left alone)")
    outside = sum(1 for _, reason in skipped if reason is SkipReason.NOT_ONLY)
    if outside:
        lines.append(f"  ({outside} item(s) matching none of their input's `only` were left alone)")
    unreadable = sum(1 for _, reason in skipped if reason is SkipReason.INPUT_UNREADABLE)
    if unreadable:
        lines.append(
            f"warning: {unreadable} input folder(s) could not be read and were skipped; the "
            "others were organized. `flib schedule` checks folder access."
        )
    return lines


_DETAILED = {SkipReason.UNREADABLE, SkipReason.INPUT_UNREADABLE}


ACCESS_HINT = (
    "hint: macOS denied the scheduled run access to some files. Give flib-python Full Disk "
    "Access (System Settings > Privacy & Security > Full Disk Access) and run `flib schedule` "
    "to check (README, Run it every day)."
)


def _access_hint(scan: ScanResult) -> str | None:
    """One line when a scheduled run was denied a file, however many were."""
    return ACCESS_HINT if any(d.denied for d in scan.details.values()) else None


def _print_plan(plan: Plan, deferred: int, cfg: Config, skipped: list[str]) -> None:
    if not plan.items:
        typer.echo("Nothing to do: no new files.")
    for item in plan.items:
        source = _short(item.context.path, cfg)
        if item.destination is None:
            typer.echo(f"  keep    {source}  ({item.reason})")
        elif item.status is FileStatus.DUPLICATE and item.original is not None:
            typer.echo(_dup_line(source, item.destination, item.original, cfg))
        else:
            typer.echo(f"  move    {source}  ->  {_short(item.destination, cfg)}  ({item.reason})")
    for line in skipped:
        typer.echo(line)
    moves = len(plan.moves())
    duplicates = sum(1 for i in plan.moves() if i.status is FileStatus.DUPLICATE)
    among = f" ({duplicates} duplicate(s) to {cfg.duplicates_folder})" if duplicates else ""
    typer.echo(
        f"Simulation: {moves} would move{among}, {len(plan.items) - moves} would stay. "
        "Nothing changed."
    )
    if deferred:
        typer.echo(f"{deferred} more file(s) wait for a later run (file or cost limit).")


def _dup_line(source: str, destination: Path, original: Path, cfg: Config) -> str:
    """A duplicate moved (or to be moved) to the duplicates folder (RF-21)."""
    return (
        f"  dup     {source}  ->  {_short(destination, cfg)}  "
        f"(same content as {_short(original, cfg)})"
    )


def _moved_line(source: str, item: ItemResult, cfg: Config) -> str:
    """A moved item; one sent to review says why."""
    if item.destination is not None and item.original is not None:
        return _dup_line(source, item.destination, item.original, cfg)
    line = f"  moved   {source}  ->  {_short(item.destination, cfg) if item.destination else '?'}"
    return line + (f"  (review: {item.note})" if item.status is FileStatus.REVIEW else "")


def _label(status: FileStatus) -> str:
    """A status as a column eight wide, with a space after a longer one (``sensitive``)."""
    return f"{status.value:<8}" if len(status.value) < 8 else f"{status.value} "


def _print_report(
    report: RunReport,
    cfg: Config,
    skipped: list[str],
    cost: str | None = None,
    hint: str | None = None,
    summary: list[str] | None = None,
) -> None:
    lines = []
    for item in report.items:
        source = _short(item.source, cfg)
        if item.destination is not None:
            lines.append(_moved_line(source, item, cfg))
            if item.warning:
                lines.append(f"  warning {_short(item.destination, cfg)}  ({item.warning})")
        else:
            lines.append(f"  {_label(item.status)}{source}  ({item.note})")
    lines.extend(skipped)
    lines.append(
        f"Run {report.run_id}: {report.count(FileStatus.ORGANIZED, moved=True)} organized, "
        f"{report.count(FileStatus.REVIEW, moved=True)} to review, "
        f"{report.count(FileStatus.DUPLICATE, moved=True)} duplicate(s) set aside, "
        f"{report.count(FileStatus.SENSITIVE)} sensitive left in place, "
        f"{report.errors} failed, {report.count(FileStatus.SKIPPED)} skipped."
    )
    if report.deferred:
        lines.append(f"{report.deferred} more file(s) wait for the next run.")
    if cost:
        lines.append(cost)
    if hint:
        lines.append(hint)
    lines.extend(summary or ())
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
    fields: dict[str, Any] | None = None,
) -> SearchFilters:
    """``fields``: the structured-field filters (#88), as ``make_filters`` names them."""
    try:
        return make_filters(
            categories=category,
            extensions=ext,
            since=since,
            until=until,
            known_categories=known,
            **(fields or {}),
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
    semantic: Annotated[
        bool | None,
        typer.Option(
            "--semantic/--no-semantic",
            help="Also find files by meaning, with embeddings (needs search.semantic.enabled); "
            "--no-semantic searches by words only. [default: search.semantic.default]",
            show_default=False,
        ),
    ] = None,
    reindex: Annotated[
        bool,
        typer.Option(
            "--reindex",
            help="First embed every file whose vector is missing or stale (semantic search).",
        ),
    ] = False,
    duplicates: Annotated[
        bool,
        typer.Option(
            "--duplicates", help="Also list duplicates set aside in the duplicates folder."
        ),
    ] = False,
    issuer: Annotated[
        str | None,
        typer.Option(
            "--issuer",
            help="Only documents from this organisation (case, accents, Inc/Ltd... ignored).",
        ),
    ] = None,
    kind: Annotated[
        list[str] | None,
        typer.Option(
            "--kind",
            help=f"Only this kind of document: {', '.join(KINDS)}. Repeat it or use commas.",
        ),
    ] = None,
    min_amount: Annotated[
        str | None, typer.Option("--min-amount", help="Only amounts of at least this much.")
    ] = None,
    max_amount: Annotated[
        str | None, typer.Option("--max-amount", help="Only amounts of at most this much.")
    ] = None,
    currency: Annotated[
        str | None, typer.Option("--currency", help="Only amounts in this currency (USD, BRL...).")
    ] = None,
    due_before: Annotated[
        str | None, typer.Option("--due-before", help="Only due before this day (YYYY-MM-DD).")
    ] = None,
    due_after: Annotated[
        str | None, typer.Option("--due-after", help="Only due after this day (YYYY-MM-DD).")
    ] = None,
    period: Annotated[
        str | None,
        typer.Option(
            "--period",
            help="Only documents about this year or month (2026, 2026-09): the period they "
            "refer to, else their date.",
        ),
    ] = None,
    total: Annotated[
        bool,
        typer.Option(
            "--sum", help="Add up the amounts of every match, per currency, instead of listing."
        ),
    ] = False,
    by: Annotated[
        str | None,
        typer.Option("--by", help=f"With --sum: totals per {', per '.join(GROUPS)}."),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Find organized files by name, description or tags, and by category, period or type.

    Without a query, the files matching the filters are listed newest first.
    With --semantic, files are also found by meaning ("invoice" finds a "bill"),
    if close enough (search.semantic.min_similarity); --no-semantic turns it off
    for one search when search.semantic.default is true.
    Duplicates are left out unless --duplicates is given.
    --issuer, --kind, --min-amount/--max-amount, --currency, --due-before/--due-after
    and --period filter by what the AI read in the document. --sum adds up the
    amounts of every match instead (never mixing currencies), --by groups them.
    """
    cfg = _load(config)
    fields: dict[str, Any] = {
        "issuer": issuer,
        "kinds": kind,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "currency": currency,
        "due_before": due_before,
        "due_after": due_after,
        "period": period,
    }
    has_filters = bool(
        split_values(category)
        or split_values(ext)
        or since
        or until
        or split_values(kind)
        or any(v is not None for k, v in fields.items() if k != "kinds")
    )
    if by is not None and not total:
        _fatal("--by goes with --sum")
    if total and duplicates:
        _fatal("--sum never counts duplicates; leave out --duplicates")
    if reindex:
        _reindex(cfg)
        if not query.strip() and not has_filters:
            return
    if not query.strip() and not has_filters:
        _fatal("give words to search for, a filter (--category, --since, --until, --ext), or both")
    use_semantic = cfg.search.semantic.default if semantic is None else semantic
    configured = [c.name for c in cfg.categories]
    hits: list[SearchHit] = []
    notice: str | None = None
    weak = 0
    if total:
        _sum(cfg, query, (category, ext, since, until), fields, by, as_json, semantic=semantic)
        return
    if not cfg.database.exists():
        _search_filters(configured, category, ext, since, until, fields)  # still validate them
    else:
        with Store.open(cfg.database) as store:
            known = configured + store.categories()
            filters = _search_filters(known, category, ext, since, until, fields)
            if use_semantic:
                outcome = semantic_search(
                    store,
                    query,
                    cfg,
                    filters=filters,
                    limit=limit,
                    include_duplicates=duplicates,
                )
                hits, notice, weak = outcome.hits, outcome.notice, outcome.weak
            else:
                hits = run_search(
                    store, query, filters=filters, limit=limit, include_duplicates=duplicates
                )
    if notice:
        typer.echo(f"note: {notice}", err=True)
    # Results the relevance floor left out are only mentioned when none is left.
    if as_json:
        if weak and not hits:
            typer.echo("note: no close matches; try other words or --no-semantic", err=True)
        typer.echo(search_json(hits))
        return
    if not hits:
        if weak:
            typer.echo("No close matches; try other words or --no-semantic.")
        else:
            typer.echo("No matches.")
        return
    for hit in hits:
        where = hit.path if hit.exists else f"{hit.path}  (missing: moved by hand?)"
        label = hit.category or hit.status
        typer.echo(f"{where}\n  [{label}] was {hit.original_name}")
        if hit.duplicate_of is not None:
            typer.echo(f"  duplicate of {hit.duplicate_of}")
        line = hit.fields_line()
        if line:
            typer.echo(f"  {line}")
        if hit.description:
            typer.echo(f"  {hit.description}")


def _sum(
    cfg: Config,
    query: str,
    filters: tuple[list[str] | None, list[str] | None, str | None, str | None],
    fields: dict[str, Any],
    by: str | None,
    as_json: bool,
    *,
    semantic: bool | None,
) -> None:
    """``flib search --sum``: totals of the matches' amounts, per currency (and group)."""
    if by is not None and by not in GROUPS:
        _fatal(f"--by must be one of {', '.join(GROUPS)}, not {by!r}")
    if semantic:
        typer.echo("note: --sum matches the words only, not by meaning", err=True)
    configured = [c.name for c in cfg.categories]
    sums = Sums(by, [], [], 0)
    if not cfg.database.exists():
        _search_filters(configured, *filters, fields)  # still validate them
    else:
        with Store.open(cfg.database) as store:
            chosen = _search_filters(configured + store.categories(), *filters, fields)
            sums = sum_amounts(store, query, filters=chosen, by=by)
    if as_json:
        typer.echo(json.dumps(sums.to_dict(), ensure_ascii=False, indent=2))
        return
    if not sums.totals:
        typer.echo("No amounts among the matches.")
    if sums.groups:
        typer.echo(f"Totals by {by}:")
        width = max(len(t.group or "") for t in sums.groups)
        width = min(max(width, len("(none)")), 40)
        for item in sums.groups:
            label = (item.group or "(none)")[:40]
            amount = format_amount(item.total_minor, item.currency)
            typer.echo(f"  {label:<{width}}  {amount:>18}  ({item.files} file(s))")
    if sums.totals:
        overall = "; ".join(
            f"{format_amount(t.total_minor, t.currency)} ({t.files} file(s))" for t in sums.totals
        )
        typer.echo(f"Total: {overall}")
    if sums.without_amount:
        typer.echo(f"{sums.without_amount} matching file(s) state no amount; not counted.")


def _reindex(cfg: Config) -> None:
    """``flib search --reindex``: embed what is missing or stale, under the instance lock."""
    semantic = cfg.search.semantic
    if not semantic.enabled:
        _fatal("semantic search is off; set search.semantic.enabled: true first")
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            report = embeddings.refresh(store, cfg)
    except AlreadyRunningError as exc:
        _fatal(str(exc))
    where = "this Mac" if is_loopback_url(semantic.base_url) else semantic.base_url
    typer.echo(f"Embedded {report.embedded} file(s) with {semantic.model} ({where}).")
    if report.error:
        _fatal(f"{report.error}; {report.pending} file(s) still without an embedding")


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
    reverted = f", {report.reverted} index change(s) reverted" if report.reverted else ""
    typer.echo(f"Undo: {len(report.restored)} restored, {len(report.skipped)} skipped{reverted}.")


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
    done: Annotated[
        bool | None,
        typer.Option(
            "--done/--not-done",
            help="Mark a document with a due date paid or done (it leaves `flib due`), or "
            "open again.",
            show_default=False,
        ),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Correct a file's category or name; flib learns from it. `flib undo` reverts it.

    --done marks a bill or other document with a due date as paid or done, so
    `flib due` leaves it out; --not-done opens it again. Both are journaled
    too: `flib undo` reverts them.
    """
    if done is not None:
        if category is not None or name is not None:
            _fatal("--done and --not-done go alone, without --category or --name")
        _settle(config, file, done=done)
        return
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


def _settle(config: Path | None, file: Path, *, done: bool) -> None:
    """``flib fix --done`` / ``--not-done``."""
    cfg = _load(config)
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            recover(store, cfg)  # finish the journal before adding to it
            report = due_mod.settle(store, file, done=done)
    except (due_mod.DueError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    what = "done" if report.done else "not done (open again)"
    typer.echo(f"  marked {what}  {_short(report.path, cfg)}  (due {report.due_date})")
    typer.echo(f"Recorded in run {report.run_id}; `flib undo` reverts it.")


@app.command()
def due(
    within: Annotated[
        str | None,
        typer.Option(
            "--within",
            help="How far ahead: 7d, 2w, 1m (days, weeks, calendar months). [default: 7d]",
            show_default=False,
        ),
    ] = None,
    overdue: Annotated[
        bool, typer.Option("--overdue", help="Only what is past due, however long ago.")
    ] = False,
    show_all: Annotated[
        bool,
        typer.Option(
            "--all",
            help="Also documents marked done, and due dates on kinds outside due.kinds.",
        ),
    ] = False,
    issuer: Annotated[
        str | None,
        typer.Option(
            "--issuer",
            help="Only documents from this organisation (case, accents, Inc/Ltd... ignored).",
        ),
    ] = None,
    kind: Annotated[
        list[str] | None,
        typer.Option(
            "--kind",
            help=f"Only this kind of document: {', '.join(KINDS)}. Repeat it or use commas.",
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON for other programs.")] = False,
    config: ConfigOption = None,
) -> None:
    """List bills and other documents with a due date: overdue first, then the next 7 days.

    Only due dates the AI read in a document count; nothing is guessed. By
    default the bills, invoices, statements and taxes (due.kinds) from 30 days ago to 7 days
    ahead that are not marked done (`flib fix PATH --done`), soonest first, with
    totals per currency. It only reads: no lock, and it works before the first run.
    """
    cfg = _load(config, warn_orphans=False)
    try:
        query = due_mod.make_query(
            within=within, overdue=overdue, include_all=show_all, issuer=issuer, kinds=kind
        )
        found = due_mod.find(cfg, query)
    except due_mod.DueError as exc:
        _fatal(str(exc))
    if as_json:
        typer.echo(json.dumps(found.to_dict(), ensure_ascii=False, indent=2))
        return
    for line in due_mod.render(found):
        typer.echo(line)


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
            help="Copy the API key from this shell's environment to the Keychain (default: ask).",
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
    spec = credentials.key_spec(cfg.provider)
    if spec is None:
        return
    found = credentials.resolve(cfg.provider)
    if found.value is None:
        if spec.provider == "anthropic":
            typer.echo(
                "warning: no API key found; the daily run will fail until you run `flib key set`.",
                err=True,
            )
        return  # openai-compat: a server that needs no key is fine
    if found.source != "environment":
        return  # Keychain or env_file: launchd can read those
    try:
        in_keychain = keychain.read(spec.account) is not None
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
            keychain.write(found.value, spec.account)
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
            "Python projects. macOS guards the Desktop, Documents and Downloads one by one; "
            "Full Disk Access covers them all at once. Then run `flib schedule` again to "
            "re-check (docs/troubleshooting.md).",
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
        report = scheduler.probe_inputs(load_config(config).input_paths)
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
            # flib's own interrupted moves are finished first, never taken for the user's.
            recovered = recover(store, cfg)
            report = run_reconcile(cfg, store)
    except AlreadyRunningError as exc:
        _fatal(str(exc))
    if recovered:
        typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
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
    for path in report.unchecked:
        typer.echo(f"  unread  {_short(path, cfg)}  (cannot be read now, not checked)")
    for path in report.locked:
        typer.echo(f"  locked  {_short(path, cfg)}  ({SkipReason.LOCKED.value}, not checked)")
    for path, original in report.orphaned:
        typer.echo(
            f"  orphan  {_short(path, cfg)}  (its original {_short(original, cfg)} is missing; "
            "move this copy to an input folder to have it filed)"
        )
    orphaned = f", {len(report.orphaned)} duplicate(s) of missing files" if report.orphaned else ""
    typer.echo(
        f"Reconcile: {report.checked} checked, {len(report.moved)} moved by hand, "
        f"{len(report.found_again)} found again, {len(report.missing)} missing, "
        f"{len(report.cloud_only)} in iCloud only{orphaned}."
        + (
            f" {len(report.locked)} could not be read while the Mac is locked; "
            "run `flib reconcile` again once it is unlocked."
            if report.locked
            else ""
        )
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
        "  flib status     what waits for you in review or failed, and the last and next run\n"
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
    answers = _ask_more_inboxes(ask, answers)
    choice = _ask_provider(ask)
    vision = False
    if choice.provider == "anthropic":
        typer.echo(VISION_NOTE)
        vision = ask.confirm("Send images and scanned PDFs to the model?", default=False)
    else:
        typer.echo("Images and scanned PDFs are classified from their name and metadata.")
    return dataclasses.replace(
        answers,
        provider=choice.provider,
        vision=vision,
        command=choice.command,
        model_name=choice.model,
        base_url=choice.base_url,
    )


def _with_inbox(
    answers: onboarding.Answers, candidate: onboarding.Answers, what: str
) -> onboarding.Answers:
    """``candidate`` (``answers`` with one more input) if its configuration is valid."""
    try:
        onboarding.check_answers(candidate)
    except ConfigError as exc:
        typer.echo(f"Not adding {what}: {exc}", err=True)
        return answers
    return candidate


def _ask_more_inboxes(ask: _Asker, answers: onboarding.Answers) -> onboarding.Answers:
    """Offer the Desktop and the screenshots folder as more inputs (#89); no by default."""
    desktop = onboarding.desktop_folder()
    if desktop is not None and not is_within(desktop, answers.input_folder):
        typer.echo(
            f"\nflib can also tidy the Desktop. People keep work in progress there, so it would "
            f"take only documents and images that have not changed for "
            f"{onboarding.DESKTOP_MIN_AGE_DAYS} days, and leave folders alone. macOS asks once "
            "whether flib may read the Desktop."
        )
        if ask.confirm("Also tidy the Desktop?", default=False):
            answers = _with_inbox(
                answers, dataclasses.replace(answers, desktop=desktop), "the Desktop"
            )
    shots = onboarding.screenshot_folder()
    if shots is None:
        return answers
    category = LOCALES[answers.language].screenshots_category
    where = onboarding.display_path(shots)
    if onboarding.shared_folder(shots):
        typer.echo(
            f"\nScreenshots are saved to {where}, which holds other files too, so flib does not "
            "offer to file it by a fixed category. To have screenshots filed on their own, "
            "choose a folder just for them in the Screenshot app (Cmd+Shift+5 > Options > "
            "Other Location), then run `flib init` again or add it to `inputs` "
            "(see config.example.yaml)."
        )
        return answers
    found = onboarding.count_matching(shots, onboarding.SCREENSHOT_ONLY)
    typer.echo(
        f"\nScreenshots are saved in {where} ({found} item(s) there look like screenshots or "
        f"screen recordings now). flib can file those into {category}/<year>/<month> without "
        "asking the AI (no cost), keeping their names; anything else there, and folders, "
        "are left alone."
    )
    if not found:
        typer.echo(
            "Nothing there has a name flib knows for a screenshot (in English, Portuguese, "
            "Spanish, French, German, Italian or Japanese), so it may never file anything "
            "from it; the answer you probably want is no."
        )
    if ask.confirm("File screenshots too?", default=False):
        answers = _with_inbox(
            answers, dataclasses.replace(answers, screenshots=shots), "the screenshots folder"
        )
    return answers


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


@dataclasses.dataclass(frozen=True, slots=True)
class _ProviderChoice:
    provider: str
    command: Path | None = None
    model: str | None = None
    base_url: str | None = None


def _ask_provider(ask: _Asker) -> _ProviderChoice:
    """The provider and what it needs: the ``claude`` to pin, or a model and URL.

    Claude Code is the default when it is installed and logged in, then Ollama when
    it is running with a model pulled: nothing to set up either way.
    """
    from file_librarian.providers import claude_code

    probe = ProviderConfig(name="claude-code", model=onboarding.DEFAULT_MODELS["claude-code"])
    executable = claude_code.find_claude(probe)
    ready = executable is not None and claude_code.logged_in(executable) is True
    if executable is None:
        state = "not found"
    else:
        state = "found, logged in" if ready else "found, not logged in"
    local_models = onboarding.probe_ollama()
    options = ["claude-code", "anthropic"]
    lines = [
        f"  claude-code    your Claude subscription through Claude Code ({state}); no API key",
        "  anthropic      the Anthropic API with an API key, billed per use "
        "(a fraction of a cent per file)",
    ]
    if local_models:
        options.append("ollama")
        lines.append(
            f"  ollama         a local model through Ollama ({len(local_models)} pulled); "
            "nothing leaves this Mac"
        )
    options.append("openai-compat")
    lines.append("  openai-compat  any OpenAI-compatible API, with its URL and an optional key")
    typer.echo("\nWhich AI classifies your files?\n" + "\n".join(lines))
    default = "claude-code" if ready else ("ollama" if local_models else "anthropic")
    name = ask.choice("Provider", options, default)
    if name == "ollama" and local_models:
        preferred = onboarding.DEFAULT_MODELS["ollama"]
        suggestion = next((m for m in local_models if m.split(":")[0] == preferred), None)
        model = ask.text("Ollama model", suggestion or local_models[0])
        return _ProviderChoice(name, model=model)
    if name == "openai-compat":
        while True:
            url = ask.text("API base URL (the part before /chat/completions)",
                           onboarding.OPENAI_COMPAT_URL)  # fmt: skip
            try:
                url = ProviderConfig(name=name, model="-", base_url=url).base_url or url
                break
            except ValidationError as exc:
                problem = exc.errors()[0]["msg"].removeprefix("Value error, ")
                if ask.yes:  # pragma: no cover - --yes never picks openai-compat
                    _fatal(problem)
                typer.echo(f"error: {problem}", err=True)
        model = ask.text("Model", onboarding.DEFAULT_MODELS["openai-compat"])
        return _ProviderChoice(name, model=model, base_url=url)
    if name != "claude-code" or executable is None:
        return _ProviderChoice(name)
    # launchd's PATH is minimal: pin an executable the daily run would not find by itself.
    hidden = claude_code.find_claude(probe, search_path=scheduler.SAFE_PATH) is None
    return _ProviderChoice(name, command=executable if hidden else None)


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
            problem = _save_key(cast(credentials.KeySpec, credentials.key_spec(cfg.provider)))
            if problem is None:
                return True
            typer.echo(f"warning: {problem}", err=True)
        typer.echo("warning: no API key yet; run `flib key set` to add one.", err=True)
        return False
    if name == "openai-compat":
        found = credentials.resolve(cfg.provider)
        if found.value is not None:
            typer.echo(f"API key: found in the {found.source} ({found.hint}).")
            return True
        local = is_loopback_url(cfg.provider.base_url or "")
        if not ask.yes and ask.confirm(
            "No API key found for this server (a local one usually needs none). "
            "Store one in the macOS Keychain now?",
            default=not local,
        ):
            spec = cast(credentials.KeySpec, credentials.key_spec(cfg.provider))
            problem = _save_key(spec)
            if problem is not None:
                typer.echo(f"warning: {problem}", err=True)
        return True
    if name == "ollama":
        from file_librarian.providers import ollama

        ready, line = ollama.check(cfg.provider)
        if not ready:
            typer.echo(f"warning: {line}.", err=True)
        return ready
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
    where = ", ".join(onboarding.display_path(p) for p in cfg.input_paths)
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
            label = {FileStatus.REVIEW: "review", FileStatus.DUPLICATE: "dup"}.get(
                item.status, "move"
            )
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
def reprocess(
    category: Annotated[
        list[str] | None,
        typer.Option(
            "--category", help="Only files of this category. Repeat it or use commas for several."
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Only files processed on or after this day (YYYY-MM-DD)."),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option("--until", help="Only files processed on or before this day (YYYY-MM-DD)."),
    ] = None,
    ext: Annotated[
        list[str] | None,
        typer.Option("--ext", help="Only this extension (pdf or .pdf). Repeat it or use commas."),
    ] = None,
    rename: Annotated[
        bool,
        typer.Option(
            "--rename",
            help="Also rename files under the current naming rules (default: names are kept).",
        ),
    ] = False,
    override_corrections: Annotated[
        bool,
        typer.Option(
            "--override-corrections",
            help="Reclassify files you corrected (flib fix) too; by default your choice stays.",
        ),
    ] = False,
    rename_category: Annotated[
        tuple[str, str] | None,
        typer.Option(
            "--rename-category",
            metavar="OLD NEW",
            help="You renamed category OLD to NEW in the config: move its files over.",
        ),
    ] = None,
    remove_category: Annotated[
        str | None,
        typer.Option(
            "--remove-category",
            metavar="OLD",
            help="You removed category OLD from the config: reclassify its files.",
        ),
    ] = None,
    to_review: Annotated[
        bool,
        typer.Option(
            "--to-review", help="With --remove-category: send its files to review, no AI."
        ),
    ] = False,
    scrub_descriptions: Annotated[
        bool,
        typer.Option(
            "--scrub-descriptions",
            help="Replace phone numbers, e-mails and document numbers in the descriptions and "
            "tags already stored. Local only: no AI, no cost, nothing moves.",
        ),
    ] = False,
    apply_layout: Annotated[
        bool,
        typer.Option(
            "--apply-layout",
            help="Move organized files into their category's layout (sub-folders by year, "
            "month or issuer) from what the index stores. No AI unless --ask-issuer.",
        ),
    ] = False,
    ask_issuer: Annotated[
        bool,
        typer.Option(
            "--ask-issuer",
            help="With --apply-layout: ask the AI for the issuer of files the index has none "
            "for (shows the cost and asks first); without it they stay.",
        ),
    ] = False,
    fill_fields: Annotated[
        bool,
        typer.Option(
            "--fill-fields",
            help="Ask the AI only for the amount, dates, issuer, period and kind of files the "
            "index has none for (shows the cost and asks first). Nothing moves.",
        ),
    ] = False,
    simulate: Annotated[
        bool,
        typer.Option(
            "--simulate",
            help="Classify (after asking: it may cost) and show the plan; move nothing.",
        ),
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask: send the files and apply the plan.")
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Reclassify organized files after the rules change (categories, hints, names, model).

    Shows the estimated cost and asks before sending anything, then shows the
    plan and asks before moving. File names are kept unless --rename. Files
    you corrected keep your choice. It is one run: `flib undo` reverts all of
    it. A category renamed or removed in the configuration needs
    --rename-category or --remove-category first. --scrub-descriptions only
    cleans what the index already stores, without asking the AI.
    --apply-layout only moves files into their category's sub-folders.
    --fill-fields only records the structured fields of files filed before them.
    """
    if ask_issuer and not apply_layout:
        _fatal("--ask-issuer goes with --apply-layout")
    if fill_fields:
        if (
            rename or override_corrections or rename_category or remove_category or to_review
            or scrub_descriptions or apply_layout
        ):  # fmt: skip
            _fatal(
                "--fill-fields only records fields in the index; it takes --category, --ext, "
                "--since, --until, --simulate and --yes"
            )
        _fill_fields(
            _load(config, warn_orphans=False),
            (category, ext, since, until),
            simulate=simulate,
            yes=yes,
        )
        return
    if apply_layout:
        others = (
            split_values(ext) or since or until or rename or override_corrections
            or rename_category or remove_category or to_review or scrub_descriptions
        )  # fmt: skip
        if others:
            _fatal(
                "--apply-layout only moves files into their layout; it takes --category, "
                "--ask-issuer, --simulate and --yes, and no category migration"
            )
        _apply_layout(
            _load(config, warn_orphans=False),
            split_values(category),
            ask=ask_issuer,
            simulate=simulate,
            yes=yes,
        )
        return
    if scrub_descriptions:
        others = (
            split_values(category) or split_values(ext) or since or until or rename
            or override_corrections or rename_category or remove_category or to_review
        )  # fmt: skip
        if others:
            _fatal("--scrub-descriptions takes the whole index; leave out the other options")
        _scrub_descriptions(_load(config, warn_orphans=False), simulate=simulate, yes=yes)
        return
    if rename_category is not None and remove_category is not None:
        _fatal("use either --rename-category or --remove-category, not both")
    migrating = rename_category is not None or remove_category is not None
    has_filters = bool(split_values(category) or split_values(ext) or since or until)
    if migrating and (has_filters or override_corrections):
        _fatal("a category migration takes the whole category; leave out the filters")
    if migrating and rename:
        _fatal("a category migration keeps file names; leave out --rename")
    if to_review and remove_category is None:
        _fatal("--to-review goes with --remove-category")
    cfg = _load(config, warn_orphans=False)
    provider: Provider | None = None
    if rename_category is None and not to_review:
        try:
            provider = get_provider(cfg.provider)
        except ProviderError as exc:
            _fatal(str(exc))
    try:
        # One reprocess at a time, from planning to the last move.
        with reprocess_lock(cfg.database):
            applied, planned = _reprocess(
                cfg,
                provider,
                filters=(category, ext, since, until),
                rename_files=rename,
                override_corrections=override_corrections,
                rename=rename_category,
                remove=remove_category,
                simulate=simulate,
                yes=yes,
            )
    except (reprocess_mod.ReprocessError, ProviderError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    if applied is not None and planned is not None:
        _print_reprocess_report(applied, cfg, planned)


def _scrub_descriptions(cfg: Config, *, simulate: bool, yes: bool) -> None:
    """``flib reprocess --scrub-descriptions``: privacy.scrub over the stored answers."""
    try:
        with reprocess_lock(cfg.database):
            with Store.open(cfg.database) as store:
                planned = reprocess_mod.plan_scrub(store)
            for item in planned:
                fields = ", ".join(c.replace("_", " ") for c in item.values)
                typer.echo(f"  scrub   {_short(item.path, cfg)}  ({fields})")
            if simulate:
                typer.echo(f"Simulation: {len(planned)} file(s) to scrub; nothing changed.")
                return
            if planned and not yes:
                question = f"Scrub {len(planned)} file(s) in the index? `flib undo` reverts it"
                if not typer.confirm(question, default=False):
                    typer.echo("Nothing changed.")
                    return
            with instance_lock(cfg.database), Store.open(cfg.database) as store:
                report = reprocess_mod.apply_scrub(store)
    except AlreadyRunningError as exc:
        _fatal(str(exc))
    if report.cached:
        typer.echo(f"{report.cached} cached answer(s) scrubbed.")
    if report.run_id is None:
        typer.echo("Nothing to scrub: no stored description holds an identifier flib recognizes.")
        return
    line = (
        f"Scrub run {report.run_id}: {report.files} file(s), {report.values} value(s) changed. "
        "`flib undo` reverts it."
    )
    if report.embeddings:
        line += f" {report.embeddings} embedding(s) removed; the next run makes them again."
    typer.echo(line)


def _fill_fields(
    cfg: Config,
    filters: tuple[list[str] | None, list[str] | None, str | None, str | None],
    *,
    simulate: bool,
    yes: bool,
) -> None:
    """``flib reprocess --fill-fields``: the structured fields of files filed before them."""
    try:
        provider = get_provider(cfg.provider)
    except ProviderError as exc:
        _fatal(str(exc))
    try:
        with reprocess_lock(cfg.database):
            with instance_lock(cfg.database), Store.open(cfg.database) as store:
                recovered = recover(store, cfg)
            if recovered:
                typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
            with Store.open(cfg.database) as store:
                known = [c.name for c in cfg.categories] + store.categories()
                selection = reprocess_mod.select_fields(
                    cfg, store, filters=_search_filters(known, *filters)
                )
                for path, reason in selection.left:
                    typer.echo(f"  leave   {_short(path, cfg)}  ({reason})")
                if not selection.targets:
                    typer.echo("Every file in the library already has its fields.")
                    return
                estimate = reprocess_mod.estimate(selection.targets, provider, cfg, store)
                typer.echo(
                    f"{estimate.files} file(s) without fields: {estimate.cached} answered by "
                    f"the cache, {estimate.to_ask} to send to {provider.name} "
                    f"(estimated US$ {estimate.cost_usd:.4f})."
                )
                if (
                    estimate.to_ask
                    and not yes
                    and not typer.confirm(
                        f"Send {estimate.to_ask} file(s) to {provider.name}?", default=False
                    )
                ):
                    typer.echo("Nothing sent, nothing changed.")
                    return
                planned = reprocess_mod.plan_fields(selection.targets, provider, cfg, store)
            for file_id, values in planned.learned.items():
                path = next(t.row.current_path for t in selection.targets if t.row.id == file_id)
                names = ", ".join(c.replace("_minor", "").replace("_", " ") for c in values)
                typer.echo(f"  fill    {_short(path, cfg)}  ({names})")
            for path, reason in planned.kept:
                typer.echo(f"  keep    {_short(path, cfg)}  ({reason})")
            cost = _cost_line(planned.cost_usd, known=planned.cost_known)
            if cost:
                typer.echo(cost)
            if simulate:
                typer.echo("Simulation: nothing changed.")
                return
            if not planned.learned:
                typer.echo("Nothing to record.")
                return
            question = (
                f"Record the fields of {len(planned.learned)} file(s) in the index? "
                "`flib undo` reverts it"
            )
            if not yes and not typer.confirm(question, default=False):
                typer.echo("Nothing changed.")
                return
            with instance_lock(cfg.database), Store.open(cfg.database) as store:
                applied = reprocess_mod.apply(
                    planned, cfg, store, provider=provider.name, model=cfg.provider.model
                )
    except (reprocess_mod.ReprocessError, ProviderError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    _print_reprocess_report(applied, cfg, planned)


def _apply_layout(
    cfg: Config, categories: list[str], *, ask: bool, simulate: bool, yes: bool
) -> None:
    """``flib reprocess --apply-layout``: organized files into their category's layout."""
    names = {c.name.casefold(): c.name for c in cfg.categories}
    wanted = [names.get(name.casefold(), name) for name in categories]
    provider: Provider | None = None
    if ask:
        try:
            provider = get_provider(cfg.provider)
        except ProviderError as exc:
            _fatal(str(exc))
    try:
        with reprocess_lock(cfg.database):
            applied, planned = _layout_run(cfg, wanted, provider, simulate=simulate, yes=yes)
    except (reprocess_mod.ReprocessError, ProviderError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    if applied is not None and planned is not None:
        _print_reprocess_report(applied, cfg, planned)


def _layout_run(
    cfg: Config, categories: list[str], provider: Provider | None, *, simulate: bool, yes: bool
) -> tuple[reprocess_mod.Applied | None, reprocess_mod.ReprocessPlan | None]:
    """Recover, plan, ask, apply, like ``_reprocess``; only recovering and applying lock."""
    with instance_lock(cfg.database), Store.open(cfg.database) as store:
        recovered = recover(store, cfg)
    if recovered:
        typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
    with Store.open(cfg.database) as store:
        selection = reprocess_mod.select_layout(
            cfg, store, categories=categories, ask=provider is not None
        )
        for path, reason in selection.left:
            typer.echo(f"  leave   {_short(path, cfg)}  ({reason})")
        if provider is not None and selection.missing:
            estimate = reprocess_mod.estimate(selection.missing, provider, cfg, store)
            typer.echo(
                f"{estimate.files} file(s) need their issuer: {estimate.cached} answered by the "
                f"cache, {estimate.to_ask} to send to {provider.name} "
                f"(estimated US$ {estimate.cost_usd:.4f})."
            )
            if (
                estimate.to_ask
                and not yes
                and not typer.confirm(
                    f"Send {estimate.to_ask} file(s) to {provider.name}?", default=False
                )
            ):
                typer.echo("Nothing sent; files without an issuer stay where they are.")
                provider = None
        planned = reprocess_mod.plan_layout(selection, cfg, store, provider=provider)
    _print_reprocess_plan(planned, cfg)
    if planned.in_place:
        typer.echo(f"{planned.in_place} file(s) already follow their category's layout.")
    if simulate:
        typer.echo("Simulation: nothing moved or changed.")
        return None, planned
    if not planned.moves() and not planned.learned and not planned.respelled:
        typer.echo("Nothing to move: the library already follows its layouts.")
        return None, planned
    question = f"Move {len(planned.moves())} file(s) into their layout? `flib undo` reverts it"
    if not planned.moves():
        count = len(planned.learned.keys() | planned.respelled.keys())
        question = f"Record in the index what {count} file(s) are filed by? Nothing moves"
    if not yes and not typer.confirm(question, default=False):
        typer.echo("Nothing changed.")
        return None, planned
    with instance_lock(cfg.database), Store.open(cfg.database) as store:
        recovered = recover(store, cfg)
        if recovered:
            typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
        applied = reprocess_mod.apply(
            planned,
            cfg,
            store,
            provider=provider.name if provider is not None else None,
            model=cfg.provider.model if provider is not None else None,
        )
    return applied, planned


def _reprocess(
    cfg: Config,
    provider: Provider | None,
    *,
    filters: tuple[list[str] | None, list[str] | None, str | None, str | None],
    rename_files: bool,
    override_corrections: bool,
    rename: tuple[str, str] | None,
    remove: str | None,
    simulate: bool,
    yes: bool,
) -> tuple[reprocess_mod.Applied | None, reprocess_mod.ReprocessPlan | None]:
    """Recover, plan, ask, apply. Only recovering and applying hold the instance lock."""
    # The journal is finished first, so the plan reads an index that matches the disk.
    with instance_lock(cfg.database), Store.open(cfg.database) as store:
        recovered = recover(store, cfg)
    if recovered:
        typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
    # Planning and both questions happen without the instance lock, so a
    # question left unanswered never blocks the scheduled run.
    with Store.open(cfg.database) as store:
        planned = _reprocess_plan(
            cfg,
            store,
            provider,
            filters=filters,
            rename_files=rename_files,
            override_corrections=override_corrections,
            rename=rename,
            remove=remove,
            yes=yes,
        )
    if planned is None:
        return None, None
    _print_reprocess_plan(planned, cfg)
    if simulate:
        typer.echo("Simulation: nothing moved or changed.")
        return None, planned
    if not planned.moves() and not planned.same_folder:
        typer.echo("Nothing to move: the library already follows the rules.")
        return None, planned
    question = f"Move {len(planned.moves())} file(s)? `flib undo` reverts it"
    if planned.same_folder:
        question = "Rename the category in the index? `flib undo` reverts it"
    if not yes and not typer.confirm(question, default=False):
        typer.echo("Nothing changed.")
        return None, planned
    # Each item is checked again under the lock (index row and file).
    with instance_lock(cfg.database), Store.open(cfg.database) as store:
        recovered = recover(store, cfg)
        if recovered:
            typer.echo(f"Recovered {recovered} move(s) interrupted by an earlier crash.")
        applied = reprocess_mod.apply(
            planned,
            cfg,
            store,
            provider=provider.name if provider is not None else None,
            model=cfg.provider.model if provider is not None else None,
        )
    return applied, planned


def _reprocess_plan(
    cfg: Config,
    store: Store,
    provider: Provider | None,
    *,
    filters: tuple[list[str] | None, list[str] | None, str | None, str | None],
    rename_files: bool,
    override_corrections: bool,
    rename: tuple[str, str] | None,
    remove: str | None,
    yes: bool,
) -> reprocess_mod.ReprocessPlan | None:
    """The plan, after the cost question; ``None`` when there is nothing to do."""
    if rename is not None:
        return reprocess_mod.plan_rename(cfg, store, *rename)
    category, ext, since, until = filters
    known = [c.name for c in cfg.categories] + store.categories()
    selection = reprocess_mod.select(
        cfg,
        store,
        filters=_search_filters(known, category, ext, since, until),
        removed=remove,
        rename=rename_files,
        override_corrections=override_corrections,
    )
    for path, reason in selection.left:
        typer.echo(f"  leave   {_short(path, cfg)}  ({reason})")
    if not selection.targets:
        typer.echo("Nothing to reprocess.")
        return None
    if provider is None:
        return reprocess_mod.plan_to_review(selection.targets, cfg)
    estimate = reprocess_mod.estimate(selection.targets, provider, cfg, store)
    cap = cfg.limits.max_cost_usd_per_run
    limit = f"; the limit per run is US$ {cap:.2f}" if cap is not None else ""
    typer.echo(
        f"{estimate.files} file(s) to reclassify: {estimate.cached} answered by the cache, "
        f"{estimate.to_ask} to send to {provider.name} "
        f"(estimated US$ {estimate.cost_usd:.4f}{limit})."
    )
    if (
        estimate.to_ask
        and not yes
        and not typer.confirm(f"Send {estimate.to_ask} file(s) to {provider.name}?", default=False)
    ):
        typer.echo("Nothing sent, nothing changed.")
        return None
    planned = reprocess_mod.plan_reprocess(selection.targets, provider, cfg, store)
    cost = _cost_line(planned.cost_usd, known=planned.cost_known)
    if cost:
        typer.echo(cost)
    if planned.cached:
        typer.echo(f"{planned.cached} classification(s) reused from the cache at no cost.")
    return planned


def _print_reprocess_plan(planned: reprocess_mod.ReprocessPlan, cfg: Config) -> None:
    for change in planned.moves():
        item = change.relocation
        source = _short(item.context.path, cfg)
        target = _short(item.destination, cfg) if item.destination is not None else "?"
        typer.echo(f"  move    {source}  ->  {target}  ({item.reason})")
    for path, reason in planned.kept:
        typer.echo(f"  keep    {_short(path, cfg)}  ({reason})")
    if planned.rename is not None and planned.same_folder:
        old, new = planned.rename
        typer.echo(f"  rename  category {old} -> {new} in the index (the same folder on macOS)")
    unchanged = len(planned.changes) - len(planned.moves())
    typer.echo(
        f"Plan: {len(planned.moves())} would move, {unchanged} already in place, "
        f"{len(planned.kept)} kept as they are."
    )
    if planned.deferred:
        typer.echo(
            f"{planned.deferred} file(s) were not sent: the cost limit was reached. "
            "Run `flib reprocess` again to go on."
        )


def _print_reprocess_report(
    applied: reprocess_mod.Applied, cfg: Config, planned: reprocess_mod.ReprocessPlan
) -> None:
    report = applied.report
    lines = []
    for item in report.items:
        if item.destination is not None:
            lines.append(_moved_line(_short(item.source, cfg), item, cfg))
            if item.warning:
                lines.append(f"  warning {_short(item.destination, cfg)}  ({item.warning})")
        elif item.status in {FileStatus.FAILED, FileStatus.SKIPPED}:
            lines.append(f"  {_label(item.status)}{_short(item.source, cfg)}  ({item.note})")
    summary = (
        f"Reprocess run {report.run_id}: {report.count(FileStatus.ORGANIZED, moved=True)} "
        f"filed, {report.count(FileStatus.REVIEW, moved=True)} to review, "
        f"{report.errors} failed, {report.count(FileStatus.SKIPPED)} skipped"
    )
    if applied.index_changes:
        summary += f", {applied.index_changes} index change(s)"
    # Only a run that changed something can be undone; never point at an older one.
    summary += ". `flib undo` reverts it." if applied.undoable else ". Nothing changed."
    lines.append(summary)
    cost = _cost_line(planned.cost_usd, known=planned.cost_known)
    if cost:
        lines.append(cost)
    for line in lines:
        typer.echo(line)
    if planned.rename is not None and not planned.same_folder:
        folder = reprocess_mod.empty_folder(cfg, planned.rename[0])
        if folder is not None:
            typer.echo(
                f"The folder {_short(folder, cfg)} is empty now. Keep it until you are sure of "
                "the change; `flib undo` moves the files back into it."
            )
    if report.run_id is not None:
        try:
            typer.echo(f"Log: {write_log(cfg.database, report.run_id, lines)}")
        except OSError as exc:
            typer.echo(f"warning: could not write the run log: {exc}", err=True)


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


@app.command("eval")
def eval_command(
    provider_name: Annotated[
        str,
        typer.Option(
            "--provider", help="anthropic, claude-code, ollama, openai-compat or fake (offline)."
        ),
    ] = "anthropic",
    model: Annotated[
        str | None,
        typer.Option(
            "--model", help="The model [default: the provider's default, as `flib init` picks]."
        ),
    ] = None,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="openai-compat (required) or ollama: the API root."),
    ] = None,
    language: Annotated[
        str, typer.Option("--language", help="en or pt-BR: the categories and names to use.")
    ] = "en",
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Only a fixed sample of this many items."),
    ] = None,
    max_cost: Annotated[
        float,
        typer.Option("--max-cost", min=0.0001, help="Stop sending once this much (US$) is spent."),
    ] = 1.0,
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Save the result as JSON here, for --compare.", dir_okay=False),
    ] = None,
    compare: Annotated[
        list[Path] | None,
        typer.Option(
            "--compare",
            help="A saved result: compare this run with it. Twice: compare the two "
            "saved results, without running.",
            dir_okay=False,
            exists=True,
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask before sending to a paid provider.")
    ] = False,
) -> None:
    """Measure classification quality on a built-in synthetic corpus of about 150 files.

    The corpus is written to a temporary folder and run through the real
    pipeline in simulate mode with a throwaway database: your configuration,
    library, index and cache are never read, and nothing of yours moves.
    Before sending anything to a provider that is not local, it shows the
    estimated cost and asks. Reports category accuracy, the review rate,
    whether descriptive names were kept, and cost and speed. See docs/quality.md.
    """
    from file_librarian.evals import harness
    from file_librarian.evals import score as scoring

    saved = compare or []
    if len(saved) > 2:
        _fatal("--compare takes one saved result (compared with a new run) or two")
    if saved and as_json:
        _fatal("--compare prints a table; save runs with --out and compare those")
    try:
        runs = [scoring.load(path) for path in saved]
    except scoring.ResultError as exc:
        _fatal(str(exc))
    if len(runs) == 2:
        for line in scoring.compare(runs[0], runs[1]):
            typer.echo(line)
        return
    if language not in LOCALES:
        _fatal(f"--language must be one of {', '.join(LOCALES)}, not {language!r}")
    settings = harness.Settings(
        provider=provider_name,
        model=model,
        language=language,
        limit=limit,
        max_cost_usd=max_cost,
        base_url=base_url,
    )
    try:
        with harness.prepared(settings) as prep:
            provider = get_provider(prep.config.provider)
            if not provider.local:
                estimate = harness.estimate(prep, provider)
                price = f"US$ {estimate.cost_usd:.4f}" if estimate.prices_known else "unknown"
                typer.echo(
                    f"{estimate.to_send} synthetic item(s) to send to {provider.name} "
                    f"({prep.config.provider.model}): estimated cost {price}, stopping at "
                    f"US$ {max_cost:.2f}. {estimate.withheld} sensitive item(s) are never sent.",
                    err=True,
                )
                if not yes and not typer.confirm("Send them?", default=False, err=True):
                    typer.echo("Nothing sent.", err=True)
                    return
            result = harness.run(prep, provider)
    except (harness.SettingsError, ProviderError, ScanError) as exc:
        _fatal(str(exc))
    data = scoring.to_json(result)
    if out is not None:
        try:
            out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        except OSError as exc:
            _fatal(f"cannot write {out}: {exc}")
    if as_json:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        for line in scoring.report_lines(result):
            typer.echo(line)
        if out is not None:
            typer.echo(f"Saved to {out}.")
        if runs:
            typer.echo("")
            for line in scoring.compare(runs[0], result):
                typer.echo(line)
    if result.leaked:
        _fatal(
            "a sensitive item reached the provider, which must never happen: "
            + ", ".join(result.leaked)
        )


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
