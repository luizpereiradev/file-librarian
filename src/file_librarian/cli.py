"""The ``flib`` command line.

Each command loads and validates the configuration, then calls one module:
``pipeline`` for ``run``/``simulate``, ``undo``, ``search``, ``reprocess``...
Every command but ``reprocess`` (which handles it) and ``status`` (which only
reads) warns when organized files still use a category the configuration no
longer has (RF-37).

**Language** (#141). What a command prints for a person is in the
configuration's ``language`` from the moment ``_load`` read it (``i18n.use``,
the one rule ``i18n.language_of``); before that, and when the configuration
cannot be read, it is English. Stays English, by design: ``--help`` (typer
builds it at import, before any configuration is read, and it must match the
README), ``--json`` and every machine format, ``flib mcp``, ``flib doctor``,
``flib inspect`` and ``flib eval`` (diagnostics), the log files (a run's log, and
whatever a run started by launchd prints, which goes to a log), and an
unexpected error's report. ``_echo`` prints a message in the language of the
moment, even one made at import.
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
    i18n,
    keychain,
    locality,
    menubar,
    menubar_state,
    notifier,
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
from file_librarian.fields import KINDS, format_decimal
from file_librarian.fix import FixError, fix_file
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.i18n import Msg, english, formats, t
from file_librarian.i18n.core import detail, refusal
from file_librarian.locales import DEFAULT_LANGUAGE, LOCALES, Language
from file_librarian.lock import AlreadyRunningError, holder, instance_lock, reprocess_lock
from file_librarian.models import FileStatus, RunTrigger, explain_review
from file_librarian.planner import Plan
from file_librarian.providers import PLANNED, ProviderError, get_provider, is_implemented
from file_librarian.providers.base import Provider
from file_librarian.reconcile import in_cloud_only
from file_librarian.reconcile import reconcile as run_reconcile
from file_librarian.runlog import append_log, logs_dir, trim, write_log
from file_librarian.scanner import Candidate, ScanError, ScanMemory, ScanResult, SkipReason
from file_librarian.scheduler import watch as watch_mod
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
from file_librarian.ui import server as ui_server
from file_librarian.undo import NothingToUndoError, undo_file, undo_run

EXIT_ERROR = 1
LOCK_WAIT_SECONDS = 600.0
"""How long an automatic run (daily or watch) waits for another run to finish."""

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
    i18n.use(i18n.DEFAULT)  # until a command has read its configuration (``_load``)


def _echo(text: str, *, err: bool = False) -> None:
    """Print a line for a person, in the language of the moment: a message made earlier
    (a module constant, made at import) is rendered again, and what a file's name could
    hold that a terminal acts on (ESC, CR, BEL, a right-to-left override) is shown as an
    escape (``formats.printable``). ``--json`` goes out with ``typer.echo``, as it is."""
    said = text.text() if isinstance(text, Msg) else text
    typer.echo(formats.printable(said, lines=True), err=err)


def _warn(text: str) -> None:
    """``warning: <text>`` on stderr, in the language of the moment."""
    _echo(t("term.warning", text=text), err=True)


def _speak(cfg: Config | None) -> None:
    """From now on, say things in ``cfg``'s language (English without one)."""
    i18n.use(i18n.language_of(cfg))


def _speak_from(config: Path | None) -> None:
    """``_speak`` for a command that works without a configuration (``flib key``): the
    language of the configuration at ``config`` if it can be read, else English."""
    try:
        _speak(load_config(config))
    except ConfigError:
        _speak(None)


YES = frozenset({"s", "sim", "y", "yes"})
NO = frozenset({"n", "não", "nao", "no"})
"""The answers a Portuguese yes/no question takes (English ones too, by habit)."""


def _confirm(question: str, *, default: bool, err: bool = False) -> bool:
    """A yes/no question in the language of the moment. English is typer's own
    (``[y/N]``); pt-BR shows ``[S/n]`` and takes ``s``/``sim`` or ``n``/``não``."""
    if i18n.current() == i18n.DEFAULT:
        if err:
            return typer.confirm(question, default=default, err=True)
        return typer.confirm(question, default=default)
    hint = t("term.yes_no.default_yes" if default else "term.yes_no.default_no")
    while True:
        said = typer.prompt(f"{question} {hint}", default="", show_default=False, err=err)
        answer = str(said).strip().casefold()
        if not answer:
            return default
        if answer in YES:
            return True
        if answer in NO:
            return False
        _echo(t("term.error", text=t("term.yes_no.invalid")), err=True)


# --- Inspection ------------------------------------------------------------------


@app.command("check-config")
def check_config(config: ConfigOption = None) -> None:
    """Validate the configuration file without touching any file."""
    path = config or default_config_path()
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        _fatal(exc.text())  # English: the language could not be read (#141)
    _speak(cfg)
    _echo(f"{path}: OK")
    _echo(f"  language: {cfg.language}")
    _echo("  inputs:")
    for entry in cfg.inputs:
        _echo(f"    {entry.path}: {cfg.input_summary(entry)}")
    _echo(f"  output_root: {cfg.output_root}")
    _echo(f"  review_folder: {cfg.review_folder}")
    _echo(f"  categories: {', '.join(c.name for c in cfg.categories)}")
    _echo(f"  provider: {cfg.provider.name} ({cfg.provider.model})")
    spec = credentials.key_spec(cfg.provider)
    if spec is not None:
        found = credentials.resolve(cfg.provider)
        if found.value:
            _echo(t("term.check.key", source=found.source, hint=found.hint))
        elif spec.provider == "anthropic":
            _echo(t("term.check.key_missing"))
        else:
            _echo(t("term.check.key_none", provider=spec.provider, env=spec.env_var))
    for entry in cfg.inputs:
        if entry.category is not None and entry.only is None:
            _warn(t("term.check.fixed_no_only", path=str(entry.path), category=entry.category))
    if cfg.provider.name == "claude-code":
        _report_claude(cfg)
    if cfg.provider.name in COMPAT_PROVIDERS:
        _report_compat(cfg)
    else:
        _warn_compat_only_options(cfg.provider)
    env_file = cfg.provider.env_file
    if env_file is not None and env_file.exists() and not credentials.env_file_is_private(env_file):
        _warn(t("term.check.env_file_open", path=str(env_file)))
    if not is_implemented(cfg.provider.name):
        milestone = PLANNED.get(cfg.provider.name) or t("term.check.later_milestone")
        _warn(t("term.check.planned_provider", provider=repr(cfg.provider.name), when=milestone))
    _report_ocr(cfg)
    _report_semantic(cfg)
    for line in locality.summary_lines(cfg):
        _echo(f"  {line}")
    for line in status_mod.cost_cap_lines(cfg):
        if status_mod.is_warning(line):
            _echo(line, err=True)
        else:
            _echo(f"  {line}")
    _report_watch(cfg, path)
    _warn_orphan_categories(cfg)
    for option, milestone in inactive_options_set(cfg):
        _warn(t("term.check.inactive_option", option=option, when=milestone))


def _report_watch(cfg: Config, path: Path) -> None:
    """When the installed watch agent was made for other inputs than these."""
    try:
        state = scheduler.status()
        scheduled = scheduler.scheduled_config()
    except (scheduler.ScheduleError, OSError, ValueError):
        return
    if scheduled is None or os.path.realpath(scheduled) != os.path.realpath(path):
        return
    stale = scheduler.stale_watch_message(state, cfg.input_paths)
    if stale:
        _warn(stale)


COMPAT_PROVIDERS = ("openai-compat", "ollama")
COMPAT_ONLY_OPTIONS = (
    "base_url", "api_key_env", "price_input_per_mtok", "price_output_per_mtok", "vision",
)  # fmt: skip


def _warn_compat_only_options(provider: ProviderConfig) -> None:
    for option in COMPAT_ONLY_OPTIONS:
        if option in provider.model_fields_set:
            _warn(t("term.check.compat_only", option=option, provider=provider.name))


def _report_compat(cfg: Config) -> None:
    """Where file content goes, what the cost cap can do, and (Ollama) whether it answers."""
    p = cfg.provider
    url = p.base_url or ""
    local = is_loopback_url(url)
    where = t("term.check.this_mac_no_limit") if local else t("term.local.remote")
    _echo(t("term.check.base_url", url=url, where=where))
    if not local and url.startswith("http://"):
        _warn(t("term.check.http_provider"))
    if p.name == "ollama" and not local:
        _warn(t("term.check.ollama_remote"))
    priced = p.price_input_per_mtok > 0 or p.price_output_per_mtok > 0
    if not priced and not local and cfg.limits.max_cost_usd_per_run is not None:
        _warn(t("term.check.no_prices"))
    if cfg.vision.enabled and not p.vision:
        _echo(t("term.check.vision_off"))
    if p.name == "ollama":
        from file_librarian.providers import ollama

        ready, line = ollama.check(p)
        if ready:
            _echo(f"  ollama: {line}")
        else:
            _warn(line)


def _report_ocr(cfg: Config) -> None:
    """OCR: off, or on with its languages; a warning when this Mac cannot run it."""
    settings = cfg.ocr
    if not settings.enabled:
        _echo(t("term.check.ocr_off"))
        return
    found = ocr.availability()
    if not found.available:
        _echo(t("term.check.ocr_unavailable"))
        _warn(t("term.check.ocr_cannot_run", reason=found.reason))
        return
    _echo(
        t(
            "term.check.ocr_on",
            languages=", ".join(settings.languages),
            count=settings.max_pages,
        )
    )
    unknown = [lang for lang in settings.languages if lang not in found.languages]
    if unknown:
        _warn(
            t(
                "term.check.ocr_languages",
                unknown=", ".join(unknown),
                known=", ".join(found.languages),
            )
        )


def _report_semantic(cfg: Config) -> None:
    """Semantic search: off, or where the index's text goes to be embedded."""
    semantic = cfg.search.semantic
    if not semantic.enabled:
        _echo(t("term.check.semantic_off"))
        return
    url = semantic.base_url
    local = is_loopback_url(url)
    where = t("term.this_mac") if local else t("term.local.remote")
    _echo(t("term.check.semantic", model=semantic.model, url=url, where=where))
    if not local:
        _warn(t("term.check.semantic_remote"))
        if url.startswith("http://"):
            _warn(t("term.check.http_semantic"))
    found = credentials.resolve_embeddings(semantic)
    if found.value:
        _echo(t("term.check.embeddings_key", source=found.source, hint=found.hint))


def _report_claude(cfg: Config) -> None:
    """Where the ``claude`` executable is and whether it is logged in (no prompt is sent)."""
    from file_librarian.providers import claude_code

    executable = claude_code.find_claude(cfg.provider)
    if executable is None:
        _echo(t("term.check.claude_missing"))
        _warn(t("term.check.install_claude"))
        return
    _echo(f"  claude: {executable}")
    login = claude_code.logged_in(executable)
    if login is None:
        _warn(t("term.check.claude_unknown"))
    elif login:
        _echo(t("term.check.claude_ok"))
    else:
        _warn(t("term.check.claude_logged_out", hint=t("term.claude.login_hint")))


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
        _echo(str(item["path"]))
        _echo(f"  kind:   {item['kind']}")
        if item["icloud_only"]:
            _echo(f"  sha256: ({SkipReason.CLOUD_ONLY.value}; download it to inspect it)")
        else:
            _echo(f"  sha256: {item['sha256']}")
        if item["size_bytes"] is not None:
            _echo(f"  size:   {item['size_bytes']} bytes")
        urls = item["source_urls"]
        if isinstance(urls, list) and urls:
            for url in urls:
                _echo(f"  from:   {url}")
        else:
            _echo("  from:   (no download origin recorded)")
        summary = item["text"]
        if isinstance(summary, dict):
            _echo(f"  text:   {_describe_text(summary)}")
        entry = item["index"]
        if isinstance(entry, dict):
            where = f", {entry['category']}" if entry["category"] else ""
            _echo(f"  index:  {entry['status']}{where}")
            review = entry["review"]
            if review is not None:
                _echo(f"  review: {review}")
            if entry["duplicate_of"] is not None:
                _echo(f"  duplicate of: {entry['duplicate_of']}")
            choice = entry["name_choice"]
            if choice is not None:
                said = "original name kept" if choice == "original" else "new name from the AI"
                reason = entry["name_choice_reason"]
                _echo(f"  name:   {said}" + (f" ({reason})" if reason else ""))
            fields = entry["fields"]
            if isinstance(fields, dict):
                for key, value in fields.items():
                    if value is not None and key != "currency":
                        if key == "amount":
                            value = f"{value} {fields['currency']}"
                        _echo(f"  {key.replace('_', ' ') + ':':<8}{value}")


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
    cfg = _load(config, warn_orphans=False, speak=not as_json)
    if sensitive:
        try:
            found = status_mod.sensitive_files(cfg)
        except (status_mod.StatusError, sqlite3.Error) as exc:
            _fatal(t("term.index.unreadable", error=str(exc)))
        for line in status_mod.render_sensitive(found):
            _echo(line)
        return
    try:
        state = status_mod.collect(cfg)
    except (status_mod.StatusError, sqlite3.Error) as exc:
        _fatal(t("term.index.unreadable", error=str(exc)))
    if as_json:
        typer.echo(status_mod.to_json(state, cfg))
        return
    for line in status_mod.render(state, cfg):
        _echo(line)


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
    try:
        cfg = load_config(config)
    except ConfigError:
        cfg = None
    _speak(cfg)
    if provider is None:
        found = credentials.key_spec(cfg.provider) if cfg is not None else None
        return found or cast(credentials.KeySpec, credentials.spec_for("anthropic"))
    spec = credentials.spec_for(provider)
    if spec is None:
        _fatal(t("term.key.bad_provider", provider=provider))
    return spec


@key_app.command("set")
def key_set(provider: KeyProviderOption = None, config: ConfigOption = None) -> None:
    """Save the API key in the Keychain (asked for, never shown)."""
    problem = _save_key(_key_spec(provider, config))
    if problem is not None:
        _fatal(problem)


def _save_key(spec: credentials.KeySpec) -> str | None:
    """Ask for the API key without echoing it and store it; the error, if any."""
    value = typer.prompt(t(f"term.key.label.{spec.provider}"), hide_input=True).strip()
    if spec.provider == "anthropic" and not value.startswith("sk-ant-"):
        _warn(t("term.key.prefix"))
    try:
        keychain.write(value, spec.account)
    except (keychain.KeychainError, ValueError) as exc:
        return str(exc)
    _echo(t("term.key.saved", service=repr(keychain.SERVICE), account=repr(spec.account)))
    return None


@key_app.command("status")
def key_status(config: ConfigOption = None) -> None:
    """Show where the configured provider's key would come from, without showing the key."""
    cfg = _load(config)
    spec = credentials.key_spec(cfg.provider)
    if spec is None:
        _echo(t("term.key.none_needed", provider=cfg.provider.name))
        return
    found = credentials.resolve(cfg.provider)
    if found.value is None:
        if spec.provider == "anthropic":
            _echo(t("term.key.not_found", env=spec.env_var))
        else:
            _echo(t("term.key.not_found_for", env=spec.env_var, provider=spec.provider))
        raise typer.Exit(EXIT_ERROR)
    _echo(t("term.key.using", source=found.source, hint=found.hint))


@key_app.command("delete")
def key_delete(provider: KeyProviderOption = None, config: ConfigOption = None) -> None:
    """Remove the API key from the Keychain."""
    spec = _key_spec(provider, config)
    try:
        removed = keychain.delete(spec.account)
    except keychain.KeychainError as exc:
        _fatal(str(exc))
    _echo(t("term.key.removed") if removed else t("term.key.none_there"))


# --- Organizing, searching and undoing ----------------------------------------------


@app.command()
def run(
    config: ConfigOption = None,
    trigger: Annotated[
        RunTrigger, typer.Option("--trigger", hidden=True, help="Who started the run.")
    ] = RunTrigger.MANUAL,
) -> None:
    """Organize the input folders now: classify, rename, move and index new files."""
    if trigger is RunTrigger.WATCH:
        _watch(config)
        return
    _pipeline(config, simulate=False, trigger=trigger)


@app.command()
def simulate(config: ConfigOption = None) -> None:
    """Print the full plan without changing anything."""
    _pipeline(config, simulate=True)


def _fatal(message: str) -> NoReturn:
    _echo(t("term.error", text=message), err=True)
    raise typer.Exit(EXIT_ERROR)


def _load(config: Path | None, *, warn_orphans: bool = True, speak: bool = True) -> Config:
    """The configuration, or the error and exit. ``speak``: what the command prints
    from now on is in its language; ``False`` for output that stays English (``--json``,
    the MCP server, a run started by launchd, whose output goes to a log)."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        _fatal(exc.text())  # in English: the language could not be read
    _speak(cfg if speak else None)
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
        _warn(reprocess_mod.orphan_message(orphans))


def _pipeline(
    config_path: Path | None,
    *,
    simulate: bool,
    trigger: RunTrigger = RunTrigger.MANUAL,
    catch_up: bool = False,
    wait: bool = True,
    memory: ScanMemory | None = None,
) -> pipeline.Outcome | None:
    """One run (or simulation). ``catch_up``: the second pass of an automatic run that
    waited for the Mac to be unlocked; it never waits again. ``wait=False``: the caller
    waits for the unlock itself (watch mode, once it released its own lock).

    An automatic run first waits up to ``LOCK_WAIT_SECONDS`` for another run to end.
    ``None`` when another run still held the instance lock and this one gave way
    quietly (a catch-up, or a watch run); otherwise that is an error."""
    # A run launchd started prints to a log (schedule.out.log, watch.out.log): English.
    cfg = _load(config_path, speak=not (trigger.automatic and not simulate))
    scheduled = trigger.automatic and not simulate
    lock_wait = LOCK_WAIT_SECONDS if scheduled and not catch_up else 0.0
    try:
        provider = get_provider(cfg.provider)
        outcome = pipeline.run(
            cfg, provider, simulate=simulate, trigger=trigger, lock_wait=lock_wait, memory=memory
        )
    except AlreadyRunningError as exc:
        if catch_up:
            _say(t("term.run.not_again", error=exc.args[0]))
            return None
        if trigger is RunTrigger.WATCH:
            _say(t("term.run.not_running", error=exc.args[0]))
            return None
        _fatal(str(exc))
    except (ProviderError, ScanError) as exc:
        if not simulate:
            menubar_state.note_problem(cfg.database, "run_failed")
        _fatal(str(exc))
    if outcome.recovered:
        _echo(t("term.recovered", count=outcome.recovered))
    skipped = _skipped_lines(outcome.scan, cfg)
    cost = _cost_line(outcome.cost_usd, known=outcome.cost_known)
    hint = _access_hint(outcome.scan) if trigger.automatic else None
    locked = _locked(outcome)
    hours = cfg.schedule.wait_for_unlock_hours
    will_wait = scheduled and not catch_up and bool(locked) and hours > 0
    left_to = _unlock_left_to(cfg) if will_wait and trigger is RunTrigger.WATCH else None
    will_wait = will_wait and left_to is None
    if simulate:
        _print_plan(outcome.plan, outcome.deferred, cfg, skipped)
        if cost:
            _echo(cost)
    elif outcome.report is not None:
        menubar_state.note_problem(cfg.database, "no_access" if hint else None)
        # The periodic summary (and its reminders) stays with the daily run: watch runs
        # are many and short, and a reminder after a download would be noise.
        summary = _periodic_summary(cfg, outcome.scan) if trigger is RunTrigger.SCHEDULE else []
        notice = _duplicates_notice(cfg, trigger)
        if notice:
            summary.insert(0, notice)
        if catch_up:
            summary.insert(0, catchup.CATCH_UP_LINE)
        if trigger is RunTrigger.WATCH and not catch_up:
            summary.insert(0, t("term.run.watch_run"))
        if outcome.day_cap_reached:
            summary.extend(_day_cap_notice(cfg))
        if outcome.scan.future:
            summary.append(
                t("term.note", text=t("term.run.future", count=len(outcome.scan.future)))
            )
        if left_to is not None:
            summary.append(t("term.run.left_to", count=len(locked), who=left_to))
        if will_wait:
            summary.append(catchup.WAITING_LINE.format(hours=hours, count=len(locked)))
        _print_report(
            outcome.report, cfg, skipped, cost, hint, summary, watch=trigger is RunTrigger.WATCH
        )
    if outcome.cached:
        _echo(t("term.cached", count=outcome.cached))
    if outcome.embedding is not None and outcome.embedding.error:
        _warn(t("term.run.embedding_failed", error=outcome.embedding.error))
    if will_wait and wait and outcome.report is not None:
        _wait_and_catch_up(config_path, cfg, outcome.report.run_id, locked, trigger)
    return outcome


def _locked(outcome: pipeline.Outcome) -> list[Path]:
    return [path for path, reason in outcome.scan.skipped if reason is SkipReason.LOCKED]


def _day_cap_notice(cfg: Config) -> list[str]:
    """The line about the daily cost cap, in the log of the first run that reached it today."""
    today = datetime.now().astimezone().date().isoformat()
    try:
        with Store.open(cfg.database) as store:
            if store.meta(status_mod.DAY_CAP_KEY) == today:
                return []
            store.set_meta(status_mod.DAY_CAP_KEY, today)
    except (sqlite3.Error, OSError):
        pass
    return [status_mod.day_cap_line(cfg)]


def _watch(config_path: Path | None) -> None:
    """``run --trigger watch``: what launchd starts when an input folder changes.

    Exits at once, with no run, when nothing new is ready (see ``scheduler.watch``).
    """
    cfg = _load(config_path, warn_orphans=False, speak=False)  # to watch.out.log
    logs = logs_dir(cfg.database)
    for name in ("watch.out.log", "watch.err.log"):  # names of files: never let them pile up
        trim(logs / name)
    outcomes: list[pipeline.Outcome] = []

    def run_once(memory: ScanMemory) -> bool:
        outcome = _pipeline(
            config_path, simulate=False, trigger=RunTrigger.WATCH, wait=False, memory=memory
        )
        if outcome is None:
            return False
        outcomes.append(outcome)
        return True

    try:
        watched = watch_mod.watch(cfg, run_once)
    except (ScanError, sqlite3.Error, MigrationError) as exc:
        _fatal(str(exc))
    _note_backlog(cfg, watched, outcomes[-1] if outcomes else None)
    minutes = cfg.schedule.watch_settle_minutes
    if watched.pending:
        _say(
            t(
                "term.watch.pending",
                count=watched.pending,
                minutes=minutes,
                who=_who_picks_up(cfg),
            )
        )
    if watched.ended is watch_mod.Ended.STOPPED:
        _say(t("term.watch.stopped"))
    last = outcomes[-1] if outcomes else None
    if last is None or last.report is None:
        return
    locked = _locked(last)
    if locked and cfg.schedule.wait_for_unlock_hours > 0 and _unlock_left_to(cfg) is None:
        # Only now, with the watch lock released. Waiting holds this agent (launchd runs one
        # process per agent), so only when nothing else will catch up.
        _wait_and_catch_up(config_path, cfg, last.report.run_id, locked, RunTrigger.WATCH)


def _who_picks_up(cfg: Config) -> Msg:
    fallback = cfg.schedule.watch_fallback_minutes
    if fallback:
        return t("term.watch.next_start", minutes=fallback)
    return t("term.watch.next_change")


def _unlock_left_to(cfg: Config) -> Msg | None:
    """Who files what a watch run skipped while the Mac was locked, when not the watch
    run itself (which would hold the watch agent while it waits): ``None`` if nobody."""
    try:
        state = scheduler.status()
    except scheduler.ScheduleError:
        return None
    fallback = state.watch_fallback_minutes
    if state.watch_paths and fallback:
        return t("term.watch.next_start_unlocked", minutes=fallback)
    if state.time is not None and scheduler.daily_agent_loaded():
        return t("term.watch.daily_waits")
    return None


def _note_backlog(cfg: Config, watched: watch_mod.Watched, last: pipeline.Outcome | None) -> None:
    """Remember how many ready items a watch start left in the inputs, and why, for
    ``flib status``; it never fails the run."""
    if watched.ended in (watch_mod.Ended.WATCHING, watch_mod.Ended.BUSY, watch_mod.Ended.STOPPED):
        return
    try:
        with Store.open(cfg.database) as store:
            status_mod.note_backlog(store, watched.left, status_mod.backlog_reason(last))
    except (sqlite3.Error, OSError):
        pass


def _say(line: str) -> None:
    """A line about the scheduled run's waiting, with the time: it goes to schedule.out.log."""
    _echo(f"[{datetime.now().astimezone():%Y-%m-%d %H:%M}] {english(line)}")


def _wait_and_catch_up(
    config_path: Path | None,
    cfg: Config,
    run_id: int | None,
    locked: list[Path],
    trigger: RunTrigger = RunTrigger.SCHEDULE,
) -> None:
    """After an automatic run that skipped files protected while the Mac is locked:
    wait for an unlock (holding no instance lock) and run once more (see ``catchup``).
    One waiter at a time, whether the daily run or a watch run started it."""
    hours = cfg.schedule.wait_for_unlock_hours

    def note(line: str) -> None:
        _say(line)
        if run_id is not None:
            with contextlib.suppress(OSError):
                append_log(cfg.database, run_id, [line])

    with catchup.waiter(cfg.database) as held:
        if not held:
            note(catchup.NOT_WAITING_LINE)
            return
        _say(t("term.watch.waiting_unlock", hours=hours))
        waited = catchup.wait_for_unlock(hours, partial(catchup.unlocked, locked))
    if waited is catchup.Waited.TIMED_OUT:
        note(catchup.TIMED_OUT_LINE.format(hours=hours))
    elif waited is catchup.Waited.STOPPED:
        note(catchup.STOPPED_LINE)
    else:
        note(catchup.UNLOCKED_LINE)
        _pipeline(config_path, simulate=False, trigger=trigger, catch_up=True)


def _periodic_summary(cfg: Config, scan: ScanResult) -> list[str]:
    """After a scheduled run, what waits for the user, when due (RF-40). Never fails the run."""
    stuck = sum(1 for _, reason in scan.skipped if reason is SkipReason.NOT_WHERE_LEFT)
    locked = sum(1 for _, reason in scan.skipped if reason is SkipReason.LOCKED)
    try:
        return status_mod.after_scheduled_run(cfg, not_where_left=stuck, locked=locked)
    except Exception as exc:  # the run itself succeeded; the summary is a courtesy
        return [
            status_mod.warning(t("term.run.summary_failed", error=f"{type(exc).__name__}: {exc}"))
        ]


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
            if not trigger.automatic and _interactive():
                status_mod.acknowledge_duplicates_notice(store)
    except (sqlite3.Error, OSError):
        return None
    return status_mod.duplicates_notice(cfg)


def _cost_line(cost_usd: float, *, known: bool) -> Msg | None:
    """The provider cost for the summary and the run log; ``None`` when nothing was paid."""
    if not known:
        return t("term.cost_unknown")
    return t("term.cost", cost=formats.later(formats.usd, cost_usd, 4)) if cost_usd else None


def _short(path: Path, cfg: Config) -> str:
    """``path`` from the library or an input folder. The real name: a run's log keeps
    it, and ``_echo`` makes it safe for the terminal."""
    for root in (cfg.output_root, *cfg.input_paths):
        if path.is_relative_to(root):
            return str(path.relative_to(root))
    return str(path)


# Skips that are noise on every run: system files and flib's own folders.
_QUIET_SKIPS = {SkipReason.HIDDEN, SkipReason.MANAGED}


def skip_reason(reason: SkipReason) -> Msg:
    """A scanner's reason to skip an item, in the language in use (its English is the
    value the run's log records, which ``status.count_skips`` reads)."""
    return t(f"term.skip.{reason.name.lower()}")


def _skipped_lines(scan: ScanResult, cfg: Config) -> list[str]:
    """Why items were left alone. Already-processed ones are only counted."""
    skipped = scan.skipped
    lines: list[str] = []
    for path, reason in skipped:
        if reason in _QUIET_SKIPS or reason in (SkipReason.KNOWN, SkipReason.NOT_ONLY):
            continue
        found = scan.details.get(path) if reason in _DETAILED else None
        why = skip_reason(reason)
        if found:
            why = t("term.skip.with_detail", reason=why, detail=str(found))
        if reason is SkipReason.TOO_RECENT:
            left = t("term.skip.days_left", count=scan.days_left.get(path, 1))
            why = t("term.skip.too_recent_left", reason=why, left=left)
        where = str(path) if reason is SkipReason.INPUT_UNREADABLE else _short(path, cfg)
        lines.append(t("term.row.skip", where=where, why=why))
    known = sum(1 for _, reason in skipped if reason is SkipReason.KNOWN)
    if known:
        lines.append(t("term.run.known", count=known))
    outside = sum(1 for _, reason in skipped if reason is SkipReason.NOT_ONLY)
    if outside:
        lines.append(t("term.run.outside_only", count=outside))
    unreadable = sum(1 for _, reason in skipped if reason is SkipReason.INPUT_UNREADABLE)
    if unreadable:
        lines.append(status_mod.warning(t("term.run.inputs_unreadable", count=unreadable)))
    return lines


_DETAILED = {SkipReason.UNREADABLE, SkipReason.INPUT_UNREADABLE}


ACCESS_HINT = "term.run.access_hint"
"""The id of the hint after a scheduled run was denied a file."""


def _access_hint(scan: ScanResult) -> Msg | None:
    """One line when a scheduled run was denied a file, however many were."""
    return t(ACCESS_HINT) if any(d.denied for d in scan.details.values()) else None


def _print_plan(plan: Plan, deferred: int, cfg: Config, skipped: list[str]) -> None:
    if not plan.items:
        _echo(t("term.simulate.nothing"))
    for item in plan.items:
        source = _short(item.context.path, cfg)
        if item.destination is None:
            _echo(t("term.row.keep", source=source, why=detail(item.reason)))
        elif item.status is FileStatus.DUPLICATE and item.original is not None:
            _echo(_dup_line(source, item.destination, item.original, cfg))
        else:
            destination = _short(item.destination, cfg)
            _echo(
                t("term.row.move", source=source, destination=destination, why=detail(item.reason))
            )
    for line in skipped:
        _echo(line)
    moves = len(plan.moves())
    duplicates = sum(1 for i in plan.moves() if i.status is FileStatus.DUPLICATE)
    stay = t("term.simulate.stay", count=len(plan.items) - moves)
    if duplicates:
        among = t("term.simulate.duplicates", count=duplicates, folder=cfg.duplicates_folder)
        _echo(t("term.simulate.summary_duplicates", count=moves, duplicates=among, stay=stay))
    else:
        _echo(t("term.simulate.summary", count=moves, stay=stay))
    if deferred:
        _echo(t("term.simulate.deferred", count=deferred))


def _dup_line(source: str, destination: Path, original: Path, cfg: Config) -> Msg:
    """A duplicate moved (or to be moved) to the duplicates folder (RF-21)."""
    return t(
        "term.row.dup",
        source=source,
        destination=_short(destination, cfg),
        original=_short(original, cfg),
    )


def _moved_line(source: str, item: ItemResult, cfg: Config) -> Msg:
    """A moved item; one sent to review says why."""
    if item.destination is not None and item.original is not None:
        return _dup_line(source, item.destination, item.original, cfg)
    destination = _short(item.destination, cfg) if item.destination else "?"
    if item.status is FileStatus.REVIEW:
        why = detail(item.note or "")
        return t("term.row.moved_review", source=source, destination=destination, why=why)
    return t("term.row.moved", source=source, destination=destination)


def _warning_row(destination: str, warning: str) -> Msg:
    """A moved item's warnings (a Finder tag not set...), each in the words it knows."""
    said = [detail(part) for part in warning.split("; ")]
    joined: Msg = t("term.text", text=said[0])
    for part in said[1:]:
        joined = t("term.then", first=joined, second=part)
    return t("term.row.warning", destination=destination, warning=joined)


def _status_row(status: FileStatus, source: str, note: str | None) -> Msg:
    """An item left where it was, under its status (``sensitive``, ``failed``...)."""
    return t(f"term.row.{status.value}", source=source, why=detail(note or ""))


def _print_report(
    report: RunReport,
    cfg: Config,
    skipped: list[str],
    cost: str | None = None,
    hint: str | None = None,
    summary: list[str] | None = None,
    *,
    watch: bool = False,
) -> None:
    lines: list[str] = []
    for item in report.items:
        source = _short(item.source, cfg)
        if item.destination is not None:
            lines.append(_moved_line(source, item, cfg))
            if item.warning:
                lines.append(_warning_row(_short(item.destination, cfg), item.warning))
        else:
            lines.append(_status_row(item.status, source, item.note))
    lines.extend(skipped)
    lines.append(
        t(
            "term.run.summary",
            run=str(report.run_id),
            organized=t("activity.organized", count=report.count(FileStatus.ORGANIZED, moved=True)),
            review=t("activity.to_review", count=report.count(FileStatus.REVIEW, moved=True)),
            duplicates=t(
                "activity.duplicates", count=report.count(FileStatus.DUPLICATE, moved=True)
            ),
            sensitive=t("term.run.sensitive", count=report.count(FileStatus.SENSITIVE)),
            failed=t("activity.failed", count=report.errors),
            skipped=t("undo.skipped_count", count=report.count(FileStatus.SKIPPED)),
        )
    )
    if report.deferred:
        lines.append(t("term.run.deferred", count=report.deferred))
    if cost:
        lines.append(cost)
    if hint:
        lines.append(hint)
    lines.extend(summary or ())
    for line in lines:
        _echo(line)
    if report.run_id is not None:
        try:
            log = write_log(cfg.database, report.run_id, lines, watch=watch)  # in English
            _echo(t("term.log", path=str(log)))
        except OSError as exc:
            _warn(t("term.log_failed", error=str(exc)))


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


NOTHING_TO_SEARCH = "term.search.nothing"
"""The id of the refusal of a search with no words and no filter."""


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
    amounts of every match instead (never mixing currencies), --by groups them;
    --sum with no words or filter adds up every filed document.
    """
    cfg = _load(config, speak=not as_json)
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
        _fatal(t("term.search.by_alone"))
    if total and duplicates:
        _fatal(t("term.search.sum_duplicates"))
    if reindex:
        _reindex(cfg)
        if not query.strip() and not has_filters:
            return
    if not query.strip() and not has_filters and not total:
        _fatal(t(NOTHING_TO_SEARCH))
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
        _echo(t("term.note", text=notice), err=True)
    # Results the relevance floor left out are only mentioned when none is left.
    if as_json:
        if weak and not hits:
            _echo(t("term.note", text=t("term.search.no_close")), err=True)
        typer.echo(search_json(hits))
        return
    if not hits:
        if weak:
            _echo(t("term.search.no_close_matches"))
        else:
            _echo(t("term.search.no_matches"))
        return
    shown = formats.printable
    for hit in hits:
        path = shown(str(hit.path))
        _echo(path if hit.exists else t("term.search.missing", path=path))
        label = shown(hit.category or hit.status)
        _echo(t("term.search.was", label=label, name=shown(hit.original_name)))
        if hit.duplicate_of is not None:
            _echo(t("term.search.duplicate_of", path=shown(str(hit.duplicate_of))))
        line = hit.fields_line()
        if line:
            _echo(f"  {shown(line)}")
        if hit.description:
            _echo(f"  {shown(hit.description)}")


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
        _fatal(t("term.search.bad_by", groups=", ".join(GROUPS), value=repr(by)))
    if semantic:
        _echo(t("term.note", text=t("term.search.sum_words")), err=True)
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
        _echo(t("term.sum.none"))
    if sums.groups:
        _echo(t("term.sum.by", by=t(f"term.sum.group.{by}")))
        none = t("term.sum.no_group")
        labels = [
            formats.clip(formats.printable(item.group) if item.group else none, 40)
            for item in sums.groups
        ]
        width = min(max(max(formats.width(label) for label in labels), formats.width(none)), 40)
        for label, item in zip(labels, sums.groups, strict=True):
            amount = formats.amount(item.total_minor, item.currency)
            files = t("term.sum.files", count=item.files)
            _echo(
                t(
                    "term.sum.row",
                    label=formats.ljust(label, width),
                    amount=formats.rjust(amount, 18),
                    files=files,
                )
            )
    if sums.totals:
        overall = [
            t(
                "term.amount_files",
                amount=formats.amount(total.total_minor, total.currency),
                files=t("term.sum.files", count=total.files),
            )
            for total in sums.totals
        ]
        joined = overall[0]
        for part in overall[1:]:
            joined = t("term.then", first=joined, second=part)
        _echo(t("term.sum.total", overall=joined))
    if sums.without_amount:
        _echo(t("term.sum.without_amount", count=sums.without_amount))


def _reindex(cfg: Config) -> None:
    """``flib search --reindex``: embed what is missing or stale, under the instance lock."""
    semantic = cfg.search.semantic
    if not semantic.enabled:
        _fatal(t("term.reindex.off"))
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            report = embeddings.refresh(store, cfg)
    except AlreadyRunningError as exc:
        _fatal(str(exc))
    where = t("term.this_mac") if is_loopback_url(semantic.base_url) else semantic.base_url
    _echo(t("term.reindex.done", count=report.embedded, model=semantic.model, where=where))
    if report.error:
        _fatal(t("term.reindex.failed", error=report.error, count=report.pending))


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
        _speak_from(config)
        _fatal(t("term.undo.both"))
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
        _echo(t("term.recovered", count=recovered))
    for dst, src in report.restored:
        _echo(
            t(
                "term.undo.row.restored",
                source=_short(dst, cfg),
                destination=formats.printable(str(src)),
            )
        )
    for path, reason in report.skipped:
        _echo(t("term.undo.row.skipped", path=_short(path, cfg), why=detail(reason)))
    for path, warning in report.warnings:
        _echo(t("term.undo.row.warning", path=_short(path, cfg), why=detail(warning)))
    restored = t("undo.restored", count=len(report.restored))
    skipped = t("undo.skipped_count", count=len(report.skipped))
    if report.reverted:
        reverted = t("undo.reverted", count=report.reverted)
        _echo(t("term.undo.summary_reverted", restored=restored, skipped=skipped,
                     reverted=reverted))  # fmt: skip
    else:
        _echo(t("term.undo.summary", restored=restored, skipped=skipped))


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
            _speak_from(config)
            _fatal(t("term.fix.done_alone"))
        _settle(config, file, done=done)
        return
    cfg = _load(config)
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            recover(store, cfg)  # finish the journal before adding to it
            report = fix_file(cfg, store, file, category=category, name=name)
    except (FixError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    source, destination = _short(report.source, cfg), _short(report.destination, cfg)
    _echo(t("term.row.moved", source=source, destination=destination))
    if report.category is not None:
        old, new = report.category
        _echo(t("term.fix.learned_category", old=old or "-", new=new))
    if report.name is not None:
        _echo(
            t(
                "term.fix.learned_name",
                old=formats.printable(report.name[0]),
                new=formats.printable(report.name[1]),
            )
        )
    for warning in report.warnings:
        _echo(t("term.fix.warning", warning=detail(warning)))
    _echo(t("term.fix.done", run=str(report.run_id)))


def _settle(config: Path | None, file: Path, *, done: bool) -> None:
    """``flib fix --done`` / ``--not-done``."""
    cfg = _load(config)
    try:
        with instance_lock(cfg.database), Store.open(cfg.database) as store:
            recover(store, cfg)  # finish the journal before adding to it
            report = due_mod.settle(store, file, done=done)
    except (due_mod.DueError, AlreadyRunningError) as exc:
        _fatal(str(exc))
    marked = "term.fix.marked_done" if report.done else "term.fix.marked_open"
    due_date = formats.day_text(report.due_date)
    _echo(t(marked, path=_short(report.path, cfg), due=due_date))
    _echo(t("term.fix.recorded", run=str(report.run_id)))


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
    cfg = _load(config, warn_orphans=False, speak=not as_json)
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
    for line in due_mod.render(found, show=lambda path: formats.printable(str(path))):
        _echo(line)


@app.command()
def schedule(
    config: ConfigOption = None,
    at: Annotated[
        str | None, typer.Option("--at", help="Time of day, HH:MM (default: schedule.time).")
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option(
            "--mode",
            help="daily, watch (run when something arrives) or both (default: schedule.mode).",
        ),
    ] = None,
    show: Annotated[bool, typer.Option("--status", help="Show the schedule and exit.")] = False,
    remove: Annotated[
        bool, typer.Option("--remove", help="Stop the daily run and watch mode.")
    ] = False,
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
    """Run flib every day at a fixed time, or as files arrive (launchd agents), and check
    it can work."""
    _speak_from(config or scheduler.scheduled_config())
    if mode is not None and mode not in scheduler.MODES:
        _fatal(t("term.schedule.bad_mode"))
    if remove:
        removed = scheduler.remove()
        notifier.remove()
        _echo(t("term.schedule.removed") if removed else t("term.schedule.nothing_removed"))
        return
    if show:
        _print_schedule_status()
        return
    try:
        current = scheduler.status()
    except scheduler.ScheduleError as exc:
        _fatal(str(exc))
    if refresh:
        _refresh_menu(quiet=True)
        if not current.installed:
            _echo(t("term.schedule.nothing_to_refresh"))
            return
        config = config or scheduler.scheduled_config()
        at = at or current.time
        mode = mode or current.mode
        save_key, check = False, False
    path = config or default_config_path()
    cfg = _load(path)
    if mode is None:
        # The installed mode stays unless the configuration sets one (`--at` alone never
        # turns watch mode off).
        explicit = "mode" in cfg.schedule.model_fields_set
        mode = cfg.schedule.mode if explicit or not current.installed else current.mode
    try:
        when = ScheduleConfig(time=at).time if at else cfg.schedule.time
    except ValueError:
        _fatal(t("term.schedule.bad_at"))
    try:
        _install_schedule(path, cfg, when, save_key, mode or "daily")
    except scheduler.ScheduleError as exc:
        _fatal(str(exc))
    if check and not _report_access(path.resolve()):
        raise typer.Exit(EXIT_ERROR)


def _install_schedule(
    path: Path, cfg: Config, when: str, save_key: bool | None, mode: str = "daily"
) -> None:
    """Install the agents of ``mode`` for the configuration at ``path``; raises
    ``ScheduleError``."""
    if not scheduler.is_installed_tool():
        _warn(t("term.schedule.development"))
    _ensure_key_for_schedule(cfg, save_key)
    _ensure_claude_for_schedule(cfg)
    installed = scheduler.install_for(path, cfg, when, mode)
    if mode in ("daily", "both"):
        _echo(t("term.schedule.daily", time=when, plist=str(installed[0])))
        _echo(t("term.schedule.on_wake"))
    if mode in ("watch", "both"):
        _echo(
            t(
                "term.schedule.watch_on",
                plist=str(installed[-1]),
                count=len(cfg.input_paths),
                minutes=cfg.schedule.watch_min_interval_minutes,
            )
        )
        _echo(t("term.schedule.top_level"))
        fallback = cfg.schedule.watch_fallback_minutes
        if fallback:
            _echo(t("term.schedule.fallback", minutes=fallback))
        if mode == "watch":
            _echo(WATCH_ONLY_NOTE)
        for line in status_mod.cost_cap_lines(cfg):
            _echo(line, err=status_mod.is_warning(line))
    _prepare_notifier(cfg)
    _echo(t("term.schedule.remove_hint"))


def _prepare_notifier(cfg: Config) -> None:
    """Build or refresh flib's notifier app; never fails the schedule (``notifier``)."""
    problem = notifier.ensure()
    if problem is not None:
        _echo(t("term.note", text=t("term.schedule.script_editor", problem=problem)),
                   err=True)  # fmt: skip
        return
    logs = logs_dir(cfg.database)
    if (
        cfg.summary.notify
        and not notifier.introduced(logs)
        and notifier.introduce(logs, language=i18n.language_of(cfg)) is None
    ):
        _echo(t("term.schedule.allow_notifications"))


WATCH_ONLY_NOTE = t("term.schedule.watch_only")
"""Said when watch mode replaces the daily run (rendered again when printed)."""


def _print_schedule_status() -> None:
    try:
        state = scheduler.status()
    except scheduler.ScheduleError as exc:
        _fatal(str(exc))
    if not state.installed:
        _echo(t("term.schedule.status_none"))
        return
    stale = _stale_watch_note(state)
    loaded = t("term.schedule.active") if state.loaded else t("term.schedule.not_loaded")
    _echo(t("term.schedule.mode", mode=state.mode))
    if state.time is not None:
        _echo(t("term.schedule.daily_at", time=state.time, state=loaded))
    if state.watch_paths:
        if state.watch_fallback_minutes:
            _echo(
                t(
                    "term.schedule.watching_fallback",
                    state=loaded,
                    minutes=state.watch_interval_minutes,
                    fallback=state.watch_fallback_minutes,
                )
            )
        else:
            _echo(t("term.schedule.watching", state=loaded, minutes=state.watch_interval_minutes))
        for folder in state.watch_paths:
            _echo(f"  {folder}")
    _echo(t("term.schedule.runs", program=" ".join(state.program)))
    if stale:
        _warn(stale)
    if not state.interpreter_current:
        _warn(t("term.schedule.interpreter_stale"))


def _stale_watch_note(state: scheduler.Status) -> str | None:
    """When the watch agent watches other folders than its configuration's inputs."""
    path = scheduler.scheduled_config()
    if not state.watch_paths or path is None:
        return None
    try:
        inputs = load_config(path).input_paths
    except ConfigError:
        return None
    return scheduler.stale_watch_message(state, inputs)


def _ensure_key_for_schedule(cfg: Config, save_key: bool | None) -> None:
    spec = credentials.key_spec(cfg.provider)
    if spec is None:
        return
    found = credentials.resolve(cfg.provider)
    if found.value is None:
        if spec.provider == "anthropic":
            _warn(t("term.schedule.no_key"))
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
        save_key = _confirm(t("term.schedule.save_key"), default=True)
    if save_key:
        try:
            keychain.write(found.value, spec.account)
            _echo(t("term.schedule.key_saved"))
        except (keychain.KeychainError, ValueError) as exc:
            _warn(t("term.schedule.key_not_saved", error=str(exc)))
    else:
        _warn(t("term.schedule.key_not_found"))


def _ensure_claude_for_schedule(cfg: Config) -> None:
    """Warn when the scheduled run, with launchd's minimal PATH, would not find ``claude``."""
    if cfg.provider.name != "claude-code":
        return
    from file_librarian.providers import claude_code

    if claude_code.find_claude(cfg.provider, search_path=scheduler.SAFE_PATH) is not None:
        return
    here = claude_code.find_claude(cfg.provider)
    if here is None:
        _warn(t("term.schedule.no_claude"))
        return
    _warn(t("term.schedule.claude_path", path=str(here)))


def _report_access(config_path: Path) -> bool:
    """Run the access check and explain any denial; ``False`` when macOS blocks a folder."""
    _echo(t("term.access.checking"))
    try:
        report = scheduler.check_access(config_path)
    except scheduler.ScheduleError as exc:
        _warn(str(exc))
        return True
    # The check's report is data from the child process (JSON): "ok", or English words.
    denied = {folder: state for folder, state in report.items() if state != "ok"}
    for folder, state in report.items():
        shown = t("term.access.ok") if state == "ok" else detail(state)
        _echo(f"  {folder}: {shown}")
    if denied:
        binary = scheduler.dedicated_interpreter()
        _echo("\n" + t("term.access.denied", binary=str(binary)), err=True)
        return False
    _echo(t("term.access.all_ok"))
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
        _echo(t("term.recovered", count=recovered))
    for old, new in report.moved:
        _echo(t("term.row.moved", source=_short(old, cfg), destination=_short(new, cfg)))
    for path, old_category, new_category in report.recategorized:
        _echo(
            t(
                "term.reconcile.learned",
                path=_short(path, cfg),
                old=old_category or "-",
                new=new_category,
            )
        )
    for path in report.found_again:
        _echo(t("term.reconcile.found", path=_short(path, cfg)))
    for path in report.missing:
        _echo(t("term.reconcile.missing", path=_short(path, cfg)))
    for path in report.cloud_only:
        _echo(
            t(
                "term.reconcile.icloud",
                path=_short(path, cfg),
                why=skip_reason(SkipReason.CLOUD_ONLY),
            )
        )
    for path in report.unchecked:
        _echo(t("term.reconcile.unread", path=_short(path, cfg)))
    for path in report.locked:
        _echo(
            t("term.reconcile.locked", path=_short(path, cfg), why=skip_reason(SkipReason.LOCKED))
        )
    for path, original in report.orphaned:
        _echo(t("term.reconcile.orphan", path=_short(path, cfg), original=_short(original, cfg)))
    counts = {
        "checked": t("term.reconcile.checked", count=report.checked),
        "moved": t("term.reconcile.moved_by_hand", count=len(report.moved)),
        "found": t("term.reconcile.found_again", count=len(report.found_again)),
        "missing": t("term.reconcile.missing_count", count=len(report.missing)),
        "icloud": t("term.reconcile.icloud_count", count=len(report.cloud_only)),
    }
    if report.orphaned:
        orphans = t("term.reconcile.orphans", count=len(report.orphaned))
        summary = t("term.reconcile.summary_orphans", orphans=orphans, **counts)
    else:
        summary = t("term.reconcile.summary", **counts)
    if report.locked:
        summary = t(
            "term.reconcile.summary_locked",
            summary=summary,
            locked=t("term.reconcile.locked_count", count=len(report.locked)),
        )
    _echo(summary)


# --- First run -------------------------------------------------------------------------

VISION_NOTE = t("term.init.vision_note")
"""What vision sends and costs, said before the question (rendered again when printed)."""


class _Asker:
    """Typer prompts or, with ``--yes``, their defaults, printed so the transcript reads alike."""

    def __init__(self, yes: bool) -> None:
        self.yes = yes

    def text(self, question: str, default: str) -> str:
        if self.yes:
            _echo(f"{question} [{default}]: {default}")
            return default
        answer = str(typer.prompt(question, default=default)).strip()
        return answer or default

    def choice(
        self, question: str, options: list[str], default: str, *, listed: bool = True
    ) -> str:
        """``listed=False``: the question names its options itself."""
        asked = f"{question} ({', '.join(options)})" if listed else question
        if self.yes:
            _echo(f"{asked} [{default}]: {default}")
            return default
        while True:
            answer = self.text(asked, default)
            if answer in options:
                return answer
            _echo(t("term.error", text=t("term.init.choose_one", options=", ".join(options))),
                  err=True)  # fmt: skip

    def confirm(self, question: str, default: bool) -> bool:
        if self.yes:
            hint = t("term.yes_no.default_yes" if default else "term.yes_no.default_no")
            said = t("term.yes_no.yes" if default else "term.yes_no.no")
            _echo(f"{question} {hint}: {said}")
            return default
        return _confirm(question, default=default)


def _init_language(path: Path) -> Language:
    """The language ``flib init`` starts in and suggests for its first question.

    Replacing a configuration it can read, that configuration's language: a library's
    folders and categories must not flip language because this shell's locale differs
    from the one the library was set up in. With none (or one it cannot read), the
    shell's locale (``onboarding.detect_language``, the same signal that picks the
    Portuguese folders), so a Brazilian user's first run is in Portuguese. The answer
    to the question then decides (#141)."""
    try:
        language = load_config(path).language
    except ConfigError:
        language = onboarding.detect_language()
    i18n.use(language)
    return language


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
    local: Annotated[
        bool,
        typer.Option(
            "--local",
            help="Keep everything on this Mac: a local model through Ollama, local text "
            "recognition and local semantic search.",
        ),
    ] = False,
) -> None:
    """Set flib up step by step, then try it on copies of your recent files.

    Nothing in your folders moves: the trial runs on copies in a temporary
    folder, with a throwaway index. With --local, nothing leaves this Mac:
    it checks that Ollama is installed and running (it never installs it),
    offers to download the recommended models, and says what quality to expect.
    """
    ask = _Asker(yes)
    path = config or default_config_path()
    suggested = _init_language(path)
    _echo(t("term.init.welcome"))
    _echo("")
    if local and not _local_setup(ask):
        raise typer.Exit(EXIT_ERROR)
    overwrite = force or (
        path.exists() and ask.confirm(t("term.init.replace", path=str(path)), default=False)
    )
    if path.exists() and not overwrite:
        _echo(t("term.init.keeping", path=str(path)))
    else:
        answers = _ask_answers(ask, local=local, language=suggested)
        onboarding.write_config(path, onboarding.render_config(answers))
        _echo("")
        _echo(t("term.init.wrote", path=str(path)))
    cfg = _load(path)
    _echo("")
    if _provider_ready(cfg, ask) and sample:
        _trial(cfg, sample, ask)
    else:
        _echo(t("term.init.no_trial"))
    if cfg.provider.name == "ollama" and cfg.provider.model == onboarding.LOCAL_MODEL:
        _echo(onboarding.LOCAL_QUALITY)
    _echo("")
    _offer_schedule(path, cfg, ask)
    _offer_menu(path, cfg, ask)
    _echo("")
    _echo(t("term.init.next"))


def _ask_answers(
    ask: _Asker, *, local: bool = False, language: Language = DEFAULT_LANGUAGE
) -> onboarding.Answers:
    """The answers, starting with the language (``language``: the suggestion)."""
    language = cast(Language, ask.choice(t("term.init.language"), list(LOCALES), language))
    i18n.use(language)  # from its first answer on, flib init speaks the chosen language
    while True:
        answers = _ask_folders(ask, language)
        try:
            onboarding.check_answers(answers)
            break
        except ConfigError as exc:
            if ask.yes:
                _fatal(exc.text())
            _echo(t("term.error", text=exc.text()), err=True)
            _echo(t("term.init.folders_again"), err=True)
    answers = _ask_more_inboxes(ask, answers)
    choice = _ProviderChoice("local") if local else _ask_provider(ask)
    while choice.provider == "local" and not local:
        local = _local_setup(ask)  # the "local" answer: set up as `flib init --local` does
        if not local:
            _echo(t("term.init.other_provider"))
            choice = _ask_provider(ask)
    if choice.provider == "local":
        return dataclasses.replace(
            answers,
            provider="ollama",
            model_name=onboarding.LOCAL_MODEL,
            vision=onboarding.LOCAL_VISION,
            semantic=True,
        )
    vision = False
    if choice.provider == "anthropic":
        _echo(VISION_NOTE)
        vision = ask.confirm(t("term.init.vision_question"), default=False)
    else:
        _echo(t("term.init.no_vision"))
    return dataclasses.replace(
        answers,
        provider=choice.provider,
        vision=vision,
        command=choice.command,
        model_name=choice.model,
        base_url=choice.base_url,
    )


def _local_setup(ask: _Asker) -> bool:
    """The fully local setup's checks (#92): Ollama installed and running, and the models
    pulled, offering to download them. False when Ollama cannot be used yet."""
    from file_librarian.providers import ProviderError

    state = onboarding.local_state()
    if state.models is None:
        if state.command is None:
            _echo(t("term.init.no_ollama", hint=t("term.ollama.install_hint")), err=True)
        else:
            _echo(t("term.init.ollama_stopped", command=str(state.command)), err=True)
        return False
    _echo(
        t(
            "term.init.ollama_running",
            model=onboarding.LOCAL_MODEL,
            embedding=onboarding.LOCAL_EMBEDDING_MODEL,
        )
    )
    missing = state.missing
    if not missing:
        _echo(t("term.init.models_there"))
        return True
    sizes = ", ".join(
        t("term.init.model_size", model=m, size=formats.decimal(onboarding.LOCAL_MODELS[m], 1))
        for m in missing
    )
    total = sum(onboarding.LOCAL_MODELS[m] for m in missing)
    _echo(t("term.init.not_downloaded", sizes=sizes))
    # --yes takes defaults; a download of several gigabytes is never one of them.
    question = t("term.init.download_question", size=formats.decimal(total, 1))
    if not ask.confirm(question, default=not ask.yes):
        pulls = "; ".join(f"ollama pull {m}" for m in missing)
        _echo(t("term.init.download_later", pulls=pulls))
        return True
    for model in missing:
        try:
            onboarding.pull_model(model, lambda line: _echo(f"  {line}"))
        except ProviderError as exc:
            _warn(t("term.init.pull_failed", error=str(exc), model=model))
    return True


def _with_inbox(
    answers: onboarding.Answers, candidate: onboarding.Answers, what: str
) -> onboarding.Answers:
    """``candidate`` (``answers`` with one more input) if its configuration is valid."""
    try:
        onboarding.check_answers(candidate)
    except ConfigError as exc:
        _echo(t("term.init.not_adding", what=what, error=exc.text()), err=True)
        return answers
    return candidate


def _ask_more_inboxes(ask: _Asker, answers: onboarding.Answers) -> onboarding.Answers:
    """Offer the Desktop and the screenshots folder as more inputs (#89); no by default."""
    desktop = onboarding.desktop_folder()
    if desktop is not None and not is_within(desktop, answers.input_folder):
        _echo("")
        _echo(t("term.init.desktop", days=onboarding.DESKTOP_MIN_AGE_DAYS))
        if ask.confirm(t("term.init.desktop_question"), default=False):
            answers = _with_inbox(
                answers, dataclasses.replace(answers, desktop=desktop), t("term.init.the_desktop")
            )
    shots = onboarding.screenshot_folder()
    if shots is None:
        return answers
    category = LOCALES[answers.language].screenshots_category
    where = onboarding.display_path(shots)
    if onboarding.shared_folder(shots):
        _echo("")
        _echo(t("term.init.shots_shared", where=where))
        return answers
    found = onboarding.count_matching(shots, onboarding.SCREENSHOT_ONLY)
    _echo("")
    _echo(t("term.init.shots", where=where, count=found, category=category))
    if not found:
        _echo(t("term.init.shots_none"))
    if ask.confirm(t("term.init.shots_question"), default=False):
        answers = _with_inbox(
            answers, dataclasses.replace(answers, screenshots=shots), t("term.init.the_shots")
        )
    return answers


def _user_path(raw: str) -> Path:
    return Path(os.path.normpath(Path(raw).expanduser()))


def _ask_folders(ask: _Asker, language: Language) -> onboarding.Answers:
    while True:
        folder = _user_path(
            ask.text(t("term.init.folder"), onboarding.display_path(Path.home() / "Downloads"))
        )
        if folder.is_dir():
            break
        if ask.yes:
            _fatal(t("term.init.not_a_folder_yes", folder=str(folder)))
        _echo(t("term.error", text=t("term.init.not_a_folder", folder=str(folder))), err=True)
    out = ask.text(
        t("term.init.output"),
        onboarding.display_path(onboarding.default_output_root(folder, language)),
    )
    review = ask.text(t("term.init.review"), LOCALES[language].review_folder)
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
        state = t("term.init.claude_not_found")
    else:
        state = t("term.init.claude_logged_in" if ready else "term.init.claude_logged_out")
    local_models = onboarding.probe_ollama()
    options = ["claude-code", "anthropic"]
    lines: list[str] = [
        t("term.init.option.claude_code", state=state),
        t("term.init.option.anthropic"),
    ]
    if local_models:
        options.append("ollama")
        lines.append(t("term.init.option.ollama", count=len(local_models)))
    options.append("openai-compat")
    lines.append(t("term.init.option.openai_compat"))
    options.append("local")
    lines.append(t("term.init.option.local", model=onboarding.LOCAL_MODEL))
    _echo("")
    _echo(t("term.init.which_ai"))
    for line in lines:
        _echo(line)
    default = "claude-code" if ready else ("ollama" if local_models else "anthropic")
    name = ask.choice(t("term.init.provider"), options, default)
    if name == "ollama" and local_models:
        preferred = onboarding.DEFAULT_MODELS["ollama"]
        suggestion = next((m for m in local_models if m.split(":")[0] == preferred), None)
        model = ask.text(t("term.init.ollama_model"), suggestion or local_models[0])
        return _ProviderChoice(name, model=model)
    if name == "openai-compat":
        while True:
            url = ask.text(t("term.init.base_url"), onboarding.OPENAI_COMPAT_URL)
            try:
                url = ProviderConfig(name=name, model="-", base_url=url).base_url or url
                break
            except ValidationError as exc:
                problem = str(refusal(exc.errors()[0])).removeprefix("Value error, ")
                if ask.yes:  # pragma: no cover - --yes never picks openai-compat
                    _fatal(problem)
                _echo(t("term.error", text=problem), err=True)
        model = ask.text(t("term.init.model"), onboarding.DEFAULT_MODELS["openai-compat"])
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
            _echo(t("term.init.key_found", source=found.source, hint=found.hint))
            return True
        if not ask.yes and ask.confirm(t("term.init.store_key"), default=True):
            problem = _save_key(cast(credentials.KeySpec, credentials.key_spec(cfg.provider)))
            if problem is None:
                return True
            _warn(problem)
        _warn(t("term.init.no_key_yet"))
        return False
    if name == "openai-compat":
        found = credentials.resolve(cfg.provider)
        if found.value is not None:
            _echo(t("term.init.key_found", source=found.source, hint=found.hint))
            return True
        local = is_loopback_url(cfg.provider.base_url or "")
        if not ask.yes and ask.confirm(t("term.init.store_server_key"), default=not local):
            spec = cast(credentials.KeySpec, credentials.key_spec(cfg.provider))
            problem = _save_key(spec)
            if problem is not None:
                _warn(problem)
        return True
    if name == "ollama":
        from file_librarian.providers import ollama

        ready, line = ollama.check(cfg.provider)
        if not ready:
            _warn(t("term.init.sentence", text=line))
        return ready
    if name == "claude-code":
        from file_librarian.providers import claude_code

        executable = claude_code.find_claude(cfg.provider)
        if executable is None:
            _warn(t("term.init.claude_missing"))
            return False
        if claude_code.logged_in(executable) is False:
            _warn(t("term.init.claude_logged_out_warning", hint=t("term.claude.login_hint")))
            return False
        return True
    if not is_implemented(name):
        _warn(t("term.init.not_available", provider=repr(name)))
        return False
    return True


def _trial(cfg: Config, size: int, ask: _Asker) -> None:
    picked = onboarding.pick_sample(cfg, size)
    where = ", ".join(onboarding.display_path(p) for p in cfg.input_paths)
    if not picked:
        _echo(t("term.init.nothing_to_try", where=where))
        return
    question = t("term.init.try_question", count=len(picked), provider=cfg.provider.name,
                 where=where)  # fmt: skip
    if not ask.confirm(question, default=True):
        return
    _echo(t("term.init.classifying", count=len(picked)))
    try:
        result = onboarding.run_trial(cfg, get_provider(cfg.provider), picked)
    except (ProviderError, ScanError) as exc:
        _warn(t("term.init.trial_stopped", error=str(exc)))
        return
    _print_trial(result, cfg)


def _print_trial(trial: onboarding.Trial, cfg: Config) -> None:
    root = onboarding.display_path(cfg.output_root)
    _echo("")
    _echo(t("term.init.trial", count=trial.sampled, root=root))
    for item in trial.items:
        if item.destination is None:
            _echo(t("term.row.keep", source=item.name, why=detail(item.reason)))
        else:
            row = {
                FileStatus.REVIEW: "term.row.trial_review",
                FileStatus.DUPLICATE: "term.row.trial_dup",
            }
            _echo(
                t(
                    row.get(item.status, "term.row.trial_move"),
                    name=item.name,
                    destination=str(item.destination),
                )
            )
    organized = trial.count(FileStatus.ORGANIZED)
    review = trial.count(FileStatus.REVIEW)
    stay = len(trial.items) - organized - review
    _echo(
        t(
            "term.init.trial_summary",
            organized=t("term.init.to_categories", count=organized),
            review=t("term.init.to_review", count=review, folder=cfg.review_folder),
            stay=t("term.init.stay", count=stay),
        )
    )
    categories = trial.by_category()
    if categories:
        _echo("  " + ", ".join(f"{name}: {n}" for name, n in sorted(categories.items())))
    if trial.deferred:
        _echo(t("term.init.not_tried", count=trial.deferred))
    _echo(t("term.init.trial_cost", cost=formats.usd(trial.cost_usd)))


def _offer_schedule(path: Path, cfg: Config, ask: _Asker) -> None:
    try:
        state = scheduler.status()
    except scheduler.ScheduleError as exc:
        _warn(t("term.init.schedule_unreadable", error=str(exc)))
        return
    if state.installed:
        if state.mode == "watch":
            _echo(t("term.init.watch_already"))
        elif state.mode == "both":
            _echo(t("term.init.both_already", time=str(state.time)))
        else:
            _echo(t("term.init.daily_already", time=str(state.time)))
        return
    question = t("term.init.schedule_question", time=cfg.schedule.time)
    if not ask.confirm(question, default=True):
        _echo(t("term.init.schedule_later"))
        return
    _echo(t("term.init.watch_explained"))
    mode = ask.choice(
        t("term.init.mode_question"), list(scheduler.MODES), cfg.schedule.mode, listed=False
    )
    if mode == "watch":
        _echo(WATCH_ONLY_NOTE)
    if mode != cfg.schedule.mode and not onboarding.set_schedule_mode(path, mode):
        _echo(t("term.init.set_mode", mode=mode, path=str(path)), err=True)
    try:
        _install_schedule(
            path, cfg, cfg.schedule.time, save_key=True if ask.yes else None, mode=mode
        )
    except scheduler.ScheduleError as exc:
        _warn(t("term.init.not_scheduled", error=str(exc)))
        return
    _report_access(path.resolve())


def _offer_menu(path: Path, cfg: Config, ask: _Asker) -> None:
    """Offer the menu bar item (#116), on unless declined; quiet where it cannot be built."""
    if menubar.missing_tools():
        return
    if menubar.installed():
        _echo(t("term.init.menu_already"))
        return
    if not ask.confirm(t("term.init.menu_question"), default=True):
        _echo(t("term.init.menu_later"))
        return
    _install_menu(path, cfg)


@app.command()
def mcp(config: ConfigOption = None) -> None:
    """Serve the library to AI assistants: a local MCP server over stdio."""
    cfg = _load(config, speak=False)  # its tool results and errors stay English
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
def ui(
    open_page: Annotated[
        ui_server.Page, typer.Option("--open", help="Page to open.")
    ] = ui_server.Page.STATUS,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Print the link without opening a browser.")
    ] = False,
    port: Annotated[
        int,
        typer.Option(
            "--port", min=0, max=65535, help="Port on 127.0.0.1 and ::1 [default: a free one]."
        ),
    ] = 0,
    config: ConfigOption = None,
) -> None:
    """Open the local interface in your browser: Status, Review, Search, Activity, Due and
    Settings.

    A server on loopback only, for this Mac, under a random .localhost name of
    its own, so other servers on the Mac never get its cookie. The browser gets a one-time
    token through a file only you can read (`--no-browser` prints a link that
    works once instead). It ends after `ui.idle_minutes` without use, with End
    session, or with Ctrl-C. A second `flib ui` while one runs says so and exits.
    """
    cfg = _load(config, warn_orphans=False)
    try:
        reason = ui_server.run(
            cfg,
            page=str(open_page),
            port=port,
            browser=not no_browser,
            echo=_echo,
            warn=lambda line: _echo(line, err=True),
            config_path=config or default_config_path(),
        )
    except ui_server.UiError as exc:
        _fatal(str(exc))
    if reason == "stolen":
        raise typer.Exit(EXIT_ERROR)


@app.command()
def menu(
    config: ConfigOption = None,
    install: Annotated[
        bool,
        typer.Option("--install", help="Install it and show it (what plain `flib menu` does)."),
    ] = False,
    remove: Annotated[
        bool, typer.Option("--remove", help="Take it out of the menu bar and uninstall it.")
    ] = False,
    show: Annotated[
        bool, typer.Option("--status", help="Say whether it is installed and running.")
    ] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Rebuild it if an update needs it (install.sh).")
    ] = False,
    organize: Annotated[
        bool, typer.Option("--organize", hidden=True, help="What its Organize now runs.")
    ] = False,
) -> None:
    """Show flib in the menu bar: the cat's pose says what needs you, and its menu opens
    `flib ui` on the right page.

    Built from tools that ship with macOS and started at login; nothing is
    downloaded. The menu shows counts only, never a file name. `--remove` takes
    it away again.
    """
    _speak_from(config or _menu_config())
    if sum((install, remove, show, refresh, organize)) > 1:
        _fatal(t("term.menu.choose_one"))
    if remove:
        try:
            found = menubar.remove()
        except menubar.MenuError as exc:
            _fatal(t("term.menu.not_removed", error=str(exc)))
        _echo(t("term.menu.removed") if found else t("term.menu.was_not_installed"))
        return
    if show:
        _print_menu_status()
        return
    if refresh:
        _refresh_menu(quiet=False)
        return
    if organize:
        _organize_from_menu(config)
        return
    path = config or default_config_path()
    if not _install_menu(path, _load(path)):
        raise typer.Exit(EXIT_ERROR)


def _menu_config() -> Path | None:
    """The configuration the menu bar item was installed for, if it is."""
    with contextlib.suppress(Exception):
        target = menubar.read_target()
        return None if target is None else target.config
    return None


MENU_NEEDS_TOOLS = "term.menu.needs_tools"
"""The id of what is said when this Mac lacks a tool the menu bar item is built with."""


def _install_menu(path: Path, cfg: Config) -> bool:
    """Build, install and start the menu bar item for the configuration at ``path``."""
    missing = menubar.missing_tools()
    if missing:
        problem = t(MENU_NEEDS_TOOLS, missing=", ".join(missing))
        _echo(t("term.error", text=problem), err=True)
        return False
    if not scheduler.is_installed_tool():
        _warn(t("term.menu.development"))
    try:
        plist = menubar.install(path, cfg.database)
    except menubar.MenuError as exc:
        _echo(t("term.error", text=t("term.menu.not_set_up_error", error=str(exc))), err=True)
        return False
    _echo(t("term.menu.installed"))
    _echo(t("term.menu.at_login", plist=str(plist)))
    return True


def _refresh_menu(*, quiet: bool) -> None:
    """Rebuild the menu bar item after an update when it needs it; never fails."""
    try:
        done = menubar.refresh()
    except menubar.MenuError as exc:
        _warn(t("term.menu.not_rebuilt", error=str(exc)))
        return
    if done is not None:
        _echo(t("term.menu.done", what=done))
    elif not quiet:
        _echo(
            t("term.menu.up_to_date") if menubar.installed() else t("term.menu.nothing_to_refresh")
        )


def _print_menu_status() -> None:
    found = menubar.summary(full=True)
    line = menubar.status_line(found)
    if line is None:
        _echo(t("term.menu.status_none"))
        return
    _echo(line)
    if found.config is not None:
        _echo(t("term.menu.for", path=str(found.config)))
    if found.seen is not None:
        seen = found.seen
        _echo(t("term.menu.shows", pose=str(seen["pose"]), line=repr(seen["line"])))


def _organize_from_menu(config: Path | None) -> None:
    """Organize now, from the menu: exactly what the daily agent does when it is the
    agent of this configuration (``launchctl kickstart``), else a run here. Never a
    second run: with one going on, it says so (the menu shows it too)."""
    cfg = _load(config, warn_orphans=False)
    if holder(cfg.database) is not None:
        menubar_state.changed(cfg.database)
        _echo(t("term.menu.running"))
        return
    scheduled = scheduler.scheduled_config()
    path = config or default_config_path()
    if (
        scheduled is not None
        and os.path.realpath(scheduled) == os.path.realpath(path)
        and scheduler.daily_agent_loaded()
    ):
        try:
            scheduler.kickstart(scheduler.LABEL)
        except scheduler.ScheduleError as exc:
            _fatal(str(exc))
        _echo(t("term.menu.kickstarted"))
        return
    _pipeline(config, simulate=False, trigger=RunTrigger.MANUAL)


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
    _speak_from(config)  # its refusals of options come before the configuration is loaded
    if ask_issuer and not apply_layout:
        _fatal(t("term.reprocess.ask_issuer_alone"))
    if fill_fields:
        if (
            rename or override_corrections or rename_category or remove_category or to_review
            or scrub_descriptions or apply_layout
        ):  # fmt: skip
            _fatal(t("term.reprocess.fill_fields_alone"))
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
            _fatal(t("term.reprocess.apply_layout_alone"))
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
            _fatal(t("term.reprocess.scrub_alone"))
        _scrub_descriptions(_load(config, warn_orphans=False), simulate=simulate, yes=yes)
        return
    if rename_category is not None and remove_category is not None:
        _fatal(t("term.reprocess.rename_or_remove"))
    migrating = rename_category is not None or remove_category is not None
    has_filters = bool(split_values(category) or split_values(ext) or since or until)
    if migrating and (has_filters or override_corrections):
        _fatal(t("term.reprocess.migration_filters"))
    if migrating and rename:
        _fatal(t("term.reprocess.migration_rename"))
    if to_review and remove_category is None:
        _fatal(t("term.reprocess.to_review_alone"))
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
                fields = ", ".join(_column(c) for c in item.values)
                _echo(t("term.row.scrub", path=_short(item.path, cfg), fields=fields))
            if simulate:
                _echo(t("term.reprocess.scrub_simulation", count=len(planned)))
                return
            if planned and not yes:
                question = t("term.reprocess.scrub_question", count=len(planned))
                if not _confirm(question, default=False):
                    _echo(t("term.reprocess.nothing_changed"))
                    return
            with instance_lock(cfg.database), Store.open(cfg.database) as store:
                report = reprocess_mod.apply_scrub(store)
    except AlreadyRunningError as exc:
        _fatal(str(exc))
    if report.cached:
        _echo(t("term.reprocess.scrub_cached", count=report.cached))
    if report.run_id is None:
        _echo(t("term.reprocess.nothing_to_scrub"))
        return
    files = t("term.reprocess.scrub_files", count=report.files)
    values = t("term.reprocess.scrub_values", count=report.values)
    if report.embeddings:
        embeddings = t("term.reprocess.scrub_embeddings", count=report.embeddings)
        _echo(
            t(
                "term.reprocess.scrub_done_embeddings",
                run=str(report.run_id),
                files=files,
                values=values,
                embeddings=embeddings,
            )
        )
    else:
        _echo(t("term.reprocess.scrub_done", run=str(report.run_id), files=files, values=values))


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
                _echo(t("term.recovered", count=recovered))
            with Store.open(cfg.database) as store:
                known = [c.name for c in cfg.categories] + store.categories()
                selection = reprocess_mod.select_fields(
                    cfg, store, filters=_search_filters(known, *filters)
                )
                for path, reason in selection.left:
                    _echo(_leave_row(path, reason, cfg))
                if not selection.targets:
                    _echo(t("term.reprocess.all_have_fields"))
                    return
                estimate = reprocess_mod.estimate(selection.targets, provider, cfg, store)
                _echo(
                    t(
                        "term.reprocess.fields_estimate",
                        count=estimate.files,
                        **_estimate_parts(estimate, provider.name),
                    )
                )
                if (
                    estimate.to_ask
                    and not yes
                    and not _confirm(_send_question(estimate.to_ask, provider.name), default=False)
                ):
                    _echo(t("term.reprocess.nothing_sent"))
                    return
                planned = reprocess_mod.plan_fields(selection.targets, provider, cfg, store)
            for file_id, values in planned.learned.items():
                path = next(x.row.current_path for x in selection.targets if x.row.id == file_id)
                names = ", ".join(_column(c.replace("_minor", "")) for c in values)
                _echo(t("term.row.fill", path=_short(path, cfg), fields=names))
            for path, reason in planned.kept:
                _echo(t("term.row.keep", source=_short(path, cfg), why=detail(reason)))
            cost = _cost_line(planned.cost_usd, known=planned.cost_known)
            if cost:
                _echo(cost)
            if simulate:
                _echo(t("term.reprocess.simulation_nothing_changed"))
                return
            if not planned.learned:
                _echo(t("term.reprocess.nothing_to_record"))
                return
            question = t("term.reprocess.fields_question", count=len(planned.learned))
            if not yes and not _confirm(question, default=False):
                _echo(t("term.reprocess.nothing_changed"))
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
        _echo(t("term.recovered", count=recovered))
    with Store.open(cfg.database) as store:
        selection = reprocess_mod.select_layout(
            cfg, store, categories=categories, ask=provider is not None
        )
        for path, reason in selection.left:
            _echo(_leave_row(path, reason, cfg))
        if provider is not None and selection.missing:
            estimate = reprocess_mod.estimate(selection.missing, provider, cfg, store)
            _echo(
                t(
                    "term.reprocess.issuer_estimate",
                    count=estimate.files,
                    **_estimate_parts(estimate, provider.name),
                )
            )
            if (
                estimate.to_ask
                and not yes
                and not _confirm(_send_question(estimate.to_ask, provider.name), default=False)
            ):
                _echo(t("term.reprocess.nothing_sent_issuer"))
                provider = None
        planned = reprocess_mod.plan_layout(selection, cfg, store, provider=provider)
    _print_reprocess_plan(planned, cfg)
    if planned.in_place:
        _echo(t("term.reprocess.in_layout", count=planned.in_place))
    if simulate:
        _echo(t("term.reprocess.simulation_nothing_moved"))
        return None, planned
    if not planned.moves() and not planned.learned and not planned.respelled:
        _echo(t("term.reprocess.layout_nothing"))
        return None, planned
    question = t("term.reprocess.layout_question", count=len(planned.moves()))
    if not planned.moves():
        count = len(planned.learned.keys() | planned.respelled.keys())
        question = t("term.reprocess.layout_record_question", count=count)
    if not yes and not _confirm(question, default=False):
        _echo(t("term.reprocess.nothing_changed"))
        return None, planned
    with instance_lock(cfg.database), Store.open(cfg.database) as store:
        recovered = recover(store, cfg)
        if recovered:
            _echo(t("term.recovered", count=recovered))
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
        _echo(t("term.recovered", count=recovered))
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
        _echo(t("term.reprocess.simulation_nothing_moved"))
        return None, planned
    if not planned.moves() and not planned.same_folder:
        _echo(t("term.reprocess.rules_nothing"))
        return None, planned
    question = t("term.reprocess.move_question", count=len(planned.moves()))
    if planned.same_folder:
        question = t("term.reprocess.rename_question")
    if not yes and not _confirm(question, default=False):
        _echo(t("term.reprocess.nothing_changed"))
        return None, planned
    # Each item is checked again under the lock (index row and file).
    with instance_lock(cfg.database), Store.open(cfg.database) as store:
        recovered = recover(store, cfg)
        if recovered:
            _echo(t("term.recovered", count=recovered))
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
        _echo(_leave_row(path, reason, cfg))
    if not selection.targets:
        _echo(t("term.reprocess.nothing"))
        return None
    if provider is None:
        return reprocess_mod.plan_to_review(selection.targets, cfg)
    estimate = reprocess_mod.estimate(selection.targets, provider, cfg, store)
    cap = cfg.limits.max_cost_usd_per_run
    parts = _estimate_parts(estimate, provider.name)
    if cap is not None:
        limit = formats.later(formats.usd, cap, 2)
        _echo(t("term.reprocess.estimate_cap", count=estimate.files, cap=limit, **parts))
    else:
        _echo(t("term.reprocess.estimate", count=estimate.files, **parts))
    if (
        estimate.to_ask
        and not yes
        and not _confirm(_send_question(estimate.to_ask, provider.name), default=False)
    ):
        _echo(t("term.reprocess.nothing_sent"))
        return None
    planned = reprocess_mod.plan_reprocess(selection.targets, provider, cfg, store)
    cost = _cost_line(planned.cost_usd, known=planned.cost_known)
    if cost:
        _echo(cost)
    if planned.cached:
        _echo(t("term.cached", count=planned.cached))
    return planned


def _column(name: str) -> str:
    """An index column (``due_date``, ``amount``) as the user reads it."""
    return t(f"term.column.{name}") if f"term.column.{name}" in _COLUMNS else name.replace("_", " ")


_COLUMNS = frozenset(
    f"term.column.{name}"
    for name in ("description", "tags", "kind", "issuer", "amount", "currency", "document_date",
                 "due_date", "period", "name_choice_reason", "layout_date")
)  # fmt: skip


def _leave_row(path: Path, reason: str, cfg: Config) -> Msg:
    return t("term.row.leave", path=_short(path, cfg), why=detail(reason))


def _estimate_parts(estimate: reprocess_mod.Estimate, provider: str) -> dict[str, object]:
    """What an estimate says before ``flib reprocess`` asks: the cache, the provider, the cost."""
    return {
        "cached": t("term.reprocess.cached", count=estimate.cached),
        "to_ask": t("term.reprocess.to_ask", count=estimate.to_ask, provider=provider),
        "cost": formats.later(formats.usd, estimate.cost_usd, 4),
    }


def _send_question(count: int, provider: str) -> Msg:
    return t("term.reprocess.send_question", count=count, provider=provider)


def _print_reprocess_plan(planned: reprocess_mod.ReprocessPlan, cfg: Config) -> None:
    for change in planned.moves():
        item = change.relocation
        source = _short(item.context.path, cfg)
        target = _short(item.destination, cfg) if item.destination is not None else "?"
        _echo(t("term.row.move", source=source, destination=target, why=detail(item.reason)))
    for path, reason in planned.kept:
        _echo(t("term.row.keep", source=_short(path, cfg), why=detail(reason)))
    if planned.rename is not None and planned.same_folder:
        old, new = planned.rename
        _echo(t("term.row.rename", old=old, new=new))
    unchanged = len(planned.changes) - len(planned.moves())
    _echo(
        t(
            "term.reprocess.plan",
            count=len(planned.moves()),
            unchanged=t("term.reprocess.in_place", count=unchanged),
            kept=t("term.reprocess.kept", count=len(planned.kept)),
        )
    )
    if planned.deferred:
        _echo(t("term.reprocess.deferred", count=planned.deferred))


def _print_reprocess_report(
    applied: reprocess_mod.Applied, cfg: Config, planned: reprocess_mod.ReprocessPlan
) -> None:
    report = applied.report
    lines: list[str] = []
    for item in report.items:
        if item.destination is not None:
            lines.append(_moved_line(_short(item.source, cfg), item, cfg))
            if item.warning:
                lines.append(_warning_row(_short(item.destination, cfg), item.warning))
        elif item.status in {FileStatus.FAILED, FileStatus.SKIPPED}:
            lines.append(_status_row(item.status, _short(item.source, cfg), item.note))
    counts = {
        "run": str(report.run_id),
        "filed": t("term.reprocess.filed", count=report.count(FileStatus.ORGANIZED, moved=True)),
        "review": t("activity.to_review", count=report.count(FileStatus.REVIEW, moved=True)),
        "failed": t("activity.failed", count=report.errors),
        "skipped": t("undo.skipped_count", count=report.count(FileStatus.SKIPPED)),
    }
    # Only a run that changed something can be undone; never point at an older one.
    end = t("term.reprocess.undoable") if applied.undoable else t("term.reprocess.unchanged")
    if applied.index_changes:
        changes = t("term.reprocess.index_changes", count=applied.index_changes)
        summary = t("term.reprocess.summary_changes", changes=changes, end=end, **counts)
    else:
        summary = t("term.reprocess.summary", end=end, **counts)
    lines.append(summary)
    cost = _cost_line(planned.cost_usd, known=planned.cost_known)
    if cost:
        lines.append(cost)
    for line in lines:
        _echo(line)
    if planned.rename is not None and not planned.same_folder:
        folder = reprocess_mod.empty_folder(cfg, planned.rename[0])
        if folder is not None:
            _echo(t("term.reprocess.empty_folder", folder=_short(folder, cfg)))
    if report.run_id is not None:
        try:
            log = write_log(cfg.database, report.run_id, lines)  # in English
            _echo(t("term.log", path=str(log)))
        except OSError as exc:
            _warn(t("term.log_failed", error=str(exc)))


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
    _echo(f"Report written to {path}")
    _echo("Read it, then attach it to the issue. To show it in Finder:")
    _echo(f"  open -R {shlex.quote(str(path))}")


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
    vision: Annotated[
        bool, typer.Option("--vision", help="Send images and scanned PDFs to the model.")
    ] = False,
    ocr_on: Annotated[
        bool,
        typer.Option("--ocr/--no-ocr", help="Use the text recognised in images (recorded)."),
    ] = True,
    case: Annotated[
        list[str] | None,
        typer.Option("--case", help="Only the items with this hard-case tag (e.g. image-text)."),
    ] = None,
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
            _echo(line)
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
        vision=vision,
        ocr=ocr_on,
        cases=tuple(case or ()),
    )
    try:
        with harness.prepared(settings) as prep:
            provider = get_provider(prep.config.provider)
            if not provider.local:
                estimate = harness.estimate(prep, provider)
                price = f"US$ {estimate.cost_usd:.4f}" if estimate.prices_known else "unknown"
                _echo(
                    f"{estimate.to_send} synthetic item(s) to send to {provider.name} "
                    f"({prep.config.provider.model}): estimated cost {price}, stopping at "
                    f"US$ {max_cost:.2f}. {estimate.withheld} sensitive item(s) are never sent.",
                    err=True,
                )
                if not yes and not typer.confirm("Send them?", default=False, err=True):
                    _echo("Nothing sent.", err=True)
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
            _echo(line)
        if out is not None:
            _echo(f"Saved to {out}.")
        if runs:
            _echo("")
            for line in scoring.compare(runs[0], result):
                _echo(line)
    if result.leaked:
        _fatal(
            "a sensitive item reached the provider, which must never happen: "
            + ", ".join(result.leaked)
        )


def main() -> None:
    if sys.platform != "darwin":
        _echo("file-librarian supports macOS only.", err=True)
        raise SystemExit(EXIT_ERROR)
    try:
        app()
    except Exception as exc:
        _echo(
            f"error: unexpected {type(exc).__name__}: {exc}\n"
            "This is a bug. Please report it with `flib --version` and the steps that led here: "
            "https://github.com/luizpereiradev/file-librarian/issues",
            err=True,
        )
        raise SystemExit(EXIT_ERROR) from exc
